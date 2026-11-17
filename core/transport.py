"""How bytes reach the server. Two interchangeable implementations:

* BrowserTransport (default) - ONE hidden Edge/Chrome (Playwright). Every request is a
  `fetch()` executed inside that browser, so it uses exactly the same network path,
  proxy/PAC, TLS and cookies as when you open Netcool yourself. Nothing is scraped from
  the screen: the browser only carries the requests.
* DirectTransport - plain Python `requests` (lighter; use if the browser cannot start).

Both return the same Response object, so the Netcool client does not care which is used.
"""
from __future__ import annotations

import concurrent.futures as cf
import inspect
import logging
import queue
import re
import textwrap
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote, urlencode


def build_url(url: str, params: Optional[dict]) -> str:
    """Encode query parameters the way the browser does: space -> %20 (NOT '+').
    The WebGUI server does not turn '+' back into a space, so a filter like
    'X:group:Tech-SMC Cairo Access ...' sent as 'Tech-SMC+Cairo+...' fails with HEMDP0119E."""
    if not params:
        return url
    q = urlencode(params, quote_via=quote, safe="")
    return url + ("&" if "?" in url else "?") + q

log = logging.getLogger(__name__)


class TransportError(Exception):
    """Network-level failure (timeout, refused, DNS...)."""


@dataclass
class Response:
    status: int
    url: str                      # final URL after redirects
    ctype: str
    text: str
    redirect_urls: list = field(default_factory=list)

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8", "replace")


class Transport:
    name = "base"

    def request(self, method: str, url: str, params: Optional[dict] = None,
                data: Optional[dict] = None, headers: Optional[dict] = None,
                timeout: float = 90) -> Response:
        raise NotImplementedError

    def request_many(self, reqs: list, timeout: float = 90, limit: int = 6) -> list:
        """reqs = [(method, url, params)]. Returns one Response or TransportError per request."""
        out = []
        for method, url, params in reqs:
            try:
                out.append(self.request(method, url, params=params, timeout=timeout))
            except TransportError as exc:
                out.append(exc)
        return out

    def cookies(self) -> list:
        """Login cookies as Playwright-style dicts (for the shared MOM window)."""
        return []

    def raw(self, method: str, url: str, body: Optional[str] = None, headers: Optional[dict] = None,
            timeout: float = 90, origin: str = "") -> Response:
        """Any HTTP call with a raw body (SOAP). `origin` = a page on the target server (browser mode)."""
        raise NotImplementedError

    def clear_cookies(self) -> None:
        raise NotImplementedError

    def park(self, path: str) -> None:
        pass

    def close(self) -> None:
        pass


# --------------------------------------------------------------------- direct
class DirectTransport(Transport):
    name = "direct"

    def __init__(self, verify_tls: bool, connect_timeout: float = 15):
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._requests = requests
        self._jar = requests.cookies.RequestsCookieJar()
        self._local = threading.local()
        self._verify = verify_tls
        self._connect_timeout = connect_timeout

    def _s(self):
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._requests.Session()
            s.cookies = self._jar
            s.verify = self._verify
            s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                              "Accept-Language": "en-US,en;q=0.9"})
            self._local.s = s
        return s

    def request(self, method, url, params=None, data=None, headers=None, timeout=90) -> Response:
        try:
            r = self._s().request(method, build_url(url, params), data=data, headers=headers or {},
                                  timeout=(self._connect_timeout, timeout), allow_redirects=True)
        except self._requests.RequestException as exc:
            raise TransportError(_short_net_error(exc)) from exc
        return Response(r.status_code, r.url, r.headers.get("Content-Type", ""), r.text,
                        [h.headers.get("Location", "") for h in r.history])

    def request_many(self, reqs: list, timeout: float = 90, limit: int = 6) -> list:
        def one(q):
            try:
                return self.request(q[0], q[1], params=q[2], timeout=timeout)
            except TransportError as exc:
                return exc
        with cf.ThreadPoolExecutor(max_workers=max(1, min(limit, len(reqs) or 1))) as ex:
            return list(ex.map(one, reqs))

    def raw(self, method, url, body=None, headers=None, timeout=90, origin="") -> Response:
        try:
            r = self._s().request(method, url, data=body.encode("utf-8") if body is not None else None,
                                  headers=headers or {}, timeout=(self._connect_timeout, timeout))
        except self._requests.RequestException as exc:
            raise TransportError(_short_net_error(exc)) from exc
        return Response(r.status_code, r.url, r.headers.get("Content-Type", ""), r.text, [])

    def cookies(self) -> list:
        out = []
        for c in self._jar:
            out.append({"name": c.name, "value": c.value, "domain": c.domain or "", "path": c.path or "/",
                        "secure": bool(c.secure)})
        return out

    def clear_cookies(self) -> None:
        self._jar.clear()


def _short_net_error(exc: Exception) -> str:
    s = str(exc)
    for key, txt in (("ConnectTimeout", "cannot connect to the server (connect timeout) - check VPN/network"),
                     ("timed out", "server did not answer in time (timeout)"),
                     ("Connection refused", "connection refused by server"),
                     ("RemoteDisconnected", "server closed the connection"),
                     ("ConnectionReset", "connection reset by server/network"),
                     ("NameResolution", "cannot resolve server name")):
        if key.lower() in s.lower():
            return txt
    return s[:200]


# -------------------------------------------------------------------- browser
_ONE_JS = """
async function one(q, timeoutMs) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(q.url, {method: q.method, headers: q.headers, body: q.body, credentials: 'include',
                                  redirect: 'follow', cache: 'no-store', signal: ctl.signal});
    const text = await r.text();
    return {ok: true, status: r.status, url: r.url, redirected: r.redirected,
            ctype: r.headers.get('content-type') || '', text};
  } catch (e) {
    return {ok: false, error: String(e && (e.name + ': ' + e.message) || e)};
  } finally { clearTimeout(t); }
}
"""
_FETCH_JS = "async ({url, method, headers, body, timeoutMs}) => {" + _ONE_JS + \
    "return await one({url, method, headers, body}, timeoutMs); }"
# several requests at once inside the browser (limited parallelism) -> one round trip for the tool
_FETCH_MANY_JS = "async ({reqs, timeoutMs, limit}) => {" + _ONE_JS + """
  const out = new Array(reqs.length); let i = 0;
  const worker = async () => { while (i < reqs.length) { const k = i++; out[k] = await one(reqs[k], timeoutMs); } };
  await Promise.all(Array.from({length: Math.max(1, Math.min(limit, reqs.length))}, worker));
  return out;
}"""



# The tool's OWN browser, isolated from the user's Edge (own temporary profile, no shared windows).
# No background traffic to Google/Microsoft (updates, sign-in, metrics, safe-browsing pings): on company devices the
# tool's browser talks ONLY to the MOM / Remedy servers the user opens.
_BROWSER_ARGS = ("--ignore-certificate-errors", "--disable-features=msSmartScreenProtection",
                 "--no-first-run", "--no-default-browser-check", "--disable-sync",
                 "--disable-background-networking", "--disable-component-update", "--disable-domain-reliability",
                 "--disable-client-side-phishing-detection", "--no-pings", "--metrics-recording-only")


def browser_order(pref: str) -> list:
    """'auto' = the Chromium shipped with the tool first (never touches the user's Edge), then Edge, then Chrome.
    'edge' = Edge first.  'chromium' = Chromium only, then the others as a last resort."""
    pref = (pref or "auto").lower()
    if pref == "edge":
        return ["msedge", "chromium", "chrome"]
    return ["chromium", "msedge", "chrome"]


_CONCAT = re.compile(r"if len\(buffer\):\s*\n\s*buffer = buffer \+ data\s*\n\s*else:\s*\n\s*buffer = data")


def patch_playwright_pipe() -> bool:
    """Playwright's pipe reader joins every message with `buffer = buffer + data` in 32 KB steps, so its cost grows
    with the SQUARE of the message size (one 30 MB MOM read = ~14 GB copied, holding the GIL: high CPU, laggy UI).
    Take the installed version's own source, change only those lines to a bytearray, compile it back.
    If the source does not look exactly as expected, nothing is changed. Returns True when the fix is active."""
    try:
        from playwright._impl import _transport as pt
        cls = pt.PipeTransport
        if getattr(cls.run, "_nsm_linear", False):
            return True
        src = textwrap.dedent(inspect.getsource(cls.run))
        fixed, n = _CONCAT.subn("buffer += data", src)
        if n != 1 or src.count("buffer = bytes(0)") != 1:
            log.info("Playwright pipe reader not patched (unknown version layout)")
            return False
        fixed = fixed.replace("buffer = bytes(0)", "buffer = bytearray()")     # json.loads accepts bytearray
        ns: dict = {}
        exec(compile(fixed, pt.__file__, "exec"), pt.__dict__, ns)               # noqa: S102 - the library's own code
        ns["run"]._nsm_linear = True
        cls.run = ns["run"]
        return True
    except Exception as exc:                                                     # never block the browser start
        log.info("Playwright pipe reader not patched: %s", exc)
        return False


class BrowserTransport(Transport):
    """Single Playwright thread; every other thread submits jobs to it."""
    name = "browser"

    def __init__(self, base_url: str, channel_order=None, headless: bool = True):
        self.base = base_url.rstrip("/")
        self._jobs: "queue.Queue" = queue.Queue()
        self._ready = cf.Future()
        self._channels = tuple(channel_order or browser_order("auto"))
        self._headless = headless
        self.channel_used = ""
        self.tried: list = []
        self._mom_windows: list = []
        self._origin_pages: dict = {}     # other servers (Remedy) -> a page on that server for fetch()
        self._thread = threading.Thread(target=self._run, name="browser", daemon=True)
        self._thread.start()
        self._ready.result(timeout=180)  # raises if no browser can start

    # ---- browser thread
    def _launch(self, pw, ch: str, headless: bool):
        kw = dict(headless=headless, args=list(_BROWSER_ARGS))
        if ch == "chromium":
            try:            # full Chromium shipped with the tool, new headless mode (one browser for both uses)
                return pw.chromium.launch(channel="chromium", **kw)
            except Exception:
                return pw.chromium.launch(**kw)      # headless shell, if that is what is installed
        if ch:
            kw["channel"] = ch
        return pw.chromium.launch(**kw)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            patch_playwright_pipe()
            pw = sync_playwright().start()
            browser = ctx = page = None
            for ch in self._channels:
                try:
                    browser = self._launch(pw, ch, self._headless)
                    ctx = browser.new_context(ignore_https_errors=True)
                    page = ctx.new_page()
                    if not browser.is_connected():
                        raise RuntimeError("browser closed right after start")
                    self.channel_used = ch
                    break
                except Exception as exc:   # e.g. Edge handed the start to an Edge window already open
                    self.tried.append(f"{ch}: {str(exc).splitlines()[0][:160]}")
                    for obj in (browser,):
                        try:
                            obj and obj.close()
                        except Exception:
                            pass
                    browser = ctx = page = None
            if browser is None:
                raise RuntimeError("No browser could be started: " + " | ".join(self.tried))
            page.set_default_timeout(90000)
            self._pw, self._browser, self._ctx, self._page = pw, browser, ctx, page
            try:   # a tiny same-origin page to run fetch() from (re-tried later if the network is down now)
                page.goto(self.base + "/ibm/console/logon.jsp", wait_until="domcontentloaded")
            except Exception as exc:
                log.warning("first page load failed (will retry on first request): %s", exc)
            self._ready.set_result(True)
        except Exception as exc:
            self._ready.set_exception(exc)
            return
        while True:
            job = self._jobs.get()
            if job is None:
                break
            fn, fut = job
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn())
                except Exception as exc:
                    fut.set_exception(exc)
        for w in list(self._mom_windows):
            try:
                w["browser"].close()
            except Exception:
                pass
        for closer in (self._ctx.close, self._browser.close, self._pw.stop):
            try:
                closer()
            except Exception:
                pass

    def _call(self, fn: Callable, timeout: float):
        fut: cf.Future = cf.Future()
        self._jobs.put((fn, fut))
        try:
            return fut.result(timeout=timeout + 30)
        except cf.TimeoutError as exc:
            raise TransportError("browser did not respond in time") from exc

    def _ensure_origin_page(self) -> None:
        # fetch() needs a page on the server origin; recover if the page navigated away/crashed
        try:
            if not self._page.url.startswith(self.base):
                raise RuntimeError
        except Exception:
            try:
                self._page = self._ctx.new_page()
            except Exception:
                pass
            self._page.goto(self.base + "/ibm/console/logon.jsp", wait_until="domcontentloaded")

    # ---- public API (any thread)
    def request(self, method, url, params=None, data=None, headers=None, timeout=90) -> Response:
        url = build_url(url, params)
        hdrs = {k: v for k, v in (headers or {}).items() if k.lower() not in ("referer", "origin", "user-agent")}
        body = None
        if data is not None:
            body = urlencode(data)
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"

        def job():
            self._ensure_origin_page()
            return self._page.evaluate(_FETCH_JS, {"url": url, "method": method, "headers": hdrs,
                                                   "body": body, "timeoutMs": int(timeout * 1000)})
        try:
            r = self._call(job, timeout)
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"browser error: {str(exc)[:200]}") from exc
        if not r.get("ok"):
            err = r.get("error", "")
            raise TransportError("server did not answer in time (timeout)" if "Abort" in err
                                 else f"cannot reach server ({err[:150]})")
        return Response(int(r["status"]), r["url"], r.get("ctype", ""), r.get("text", ""),
                        [r["url"]] if r.get("redirected") else [])

    def request_many(self, reqs: list, timeout: float = 90, limit: int = 6) -> list:
        """`limit` requests run in parallel inside the browser; each batch comes back as its own message,
        so a big filter is never one huge reply (smaller transfers, the other threads keep running)."""
        if not reqs:
            return []
        items = [{"url": build_url(url, params), "method": method, "headers": {}, "body": None}
                 for method, url, params in reqs]
        size = max(1, int(limit))

        def job(batch):
            self._ensure_origin_page()
            return self._page.evaluate(_FETCH_MANY_JS, {"reqs": batch, "timeoutMs": int(timeout * 1000),
                                                        "limit": size})
        res: list = []
        for i in range(0, len(items), size):
            batch = items[i:i + size]
            try:
                res += self._call(lambda b=batch: job(b), timeout * 2)
            except TransportError:
                raise
            except Exception as exc:
                raise TransportError(f"browser error: {str(exc)[:200]}") from exc
        out = []
        for r in res:
            if not r or not r.get("ok"):
                err = (r or {}).get("error", "")
                out.append(TransportError("server did not answer in time (timeout)" if "Abort" in err
                                          else f"cannot reach server ({err[:150]})"))
            else:
                out.append(Response(int(r["status"]), r["url"], r.get("ctype", ""), r.get("text", ""),
                                    [r["url"]] if r.get("redirected") else []))
        return out

    def raw(self, method, url, body=None, headers=None, timeout=90, origin="") -> Response:
        """From a page ON the target server (e.g. Remedy): same origin, same proxy / VPN path as Edge."""
        origin = origin or url

        def job():
            from urllib.parse import urlsplit
            site = "{0.scheme}://{0.netloc}".format(urlsplit(origin))
            page = self._origin_pages.get(site)
            if page is None or page.is_closed():
                page = self._origin_pages[site] = self._ctx.new_page()
                page.goto(origin, wait_until="domcontentloaded")
            return page.evaluate(_FETCH_JS, {"url": url, "method": method, "headers": headers or {},
                                             "body": body, "timeoutMs": int(timeout * 1000)})
        try:
            r = self._call(job, timeout + 60)
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"cannot reach {url.split('/')[2] if '//' in url else url} "
                                 f"({str(exc).splitlines()[0][:150]})") from exc
        if not r.get("ok"):
            raise TransportError(f"cannot reach server ({r.get('error', '')[:150]})")
        return Response(int(r["status"]), r["url"], r.get("ctype", ""), r.get("text", ""), [])

    def cookies(self) -> list:
        try:
            return self._call(lambda: self._ctx.cookies(), 30)
        except Exception:
            return []

    def clear_cookies(self) -> None:
        try:
            self._call(lambda: self._ctx.clear_cookies(), 30)
        except Exception:
            pass

    def park(self, path: str) -> None:
        """Move the helper page to a tiny static page after login (no console scripts)."""
        def job():
            try:
                self._page.goto(self.base + path, wait_until="domcontentloaded")
            except Exception:
                pass
        try:
            self._call(job, 60)
        except Exception:
            pass

    def open_mom_window(self, url: str) -> str:
        """A VISIBLE browser window that shares the tool's login (same cookies = same MOM session),
        so the user can work in MOM without a second sign-in that would log the tool out."""
        def job():
            cookies = self._ctx.cookies()
            for w in list(self._mom_windows):            # reuse the window if it is still open
                try:
                    if w["browser"].is_connected():
                        w["ctx"].add_cookies(cookies)
                        pg = w["ctx"].pages[0] if w["ctx"].pages else w["ctx"].new_page()
                        pg.goto(url, wait_until="commit")
                        pg.bring_to_front()
                        return w["channel"]
                except Exception:
                    pass
                self._mom_windows.remove(w)
            errors = []
            order = ["chromium", "msedge", "chrome"]
            if self.channel_used in order:               # the engine that already works first
                order.remove(self.channel_used)
                order.insert(0, self.channel_used)
            for ch in order:
                try:
                    br = self._launch(self._pw, ch, False)
                    cx = br.new_context(ignore_https_errors=True, no_viewport=True)
                    cx.add_cookies(cookies)
                    pg = cx.new_page()
                    pg.goto(url, wait_until="commit")
                    self._mom_windows.append({"browser": br, "ctx": cx, "channel": ch})
                    return ch
                except Exception as exc:
                    errors.append(f"{ch}: {str(exc).splitlines()[0][:120]}")
            raise RuntimeError("Could not open a browser window: " + " | ".join(errors))
        return self._call(job, 120)

    def visible_page(self, url: str, with_login: bool = True):
        """A VISIBLE window on the tool's engine for a plugin to drive (flow recording / replay). Returns a
        handle usable only through `on_page(fn)`; the window shares the tool's login when asked."""
        def job():
            br = self._launch(self._pw, self.channel_used or "chromium", False)
            cx = br.new_context(ignore_https_errors=True, no_viewport=True)
            if with_login:
                cx.add_cookies(self._ctx.cookies())
            pg = cx.new_page()
            pg.goto(url, wait_until="commit")
            self._mom_windows.append({"browser": br, "ctx": cx, "channel": self.channel_used, "page": pg})
            return len(self._mom_windows) - 1
        return self._call(job, 120)

    def on_page(self, handle: int, fn, timeout: float = 600):
        """Run fn(page) on the browser thread (Playwright's rule) for a window from visible_page()."""
        def job():
            w = self._mom_windows[handle]
            return fn(w["page"])
        return self._call(job, timeout)

    def sync_mom_windows(self) -> None:
        """After the tool signed in again, give the open MOM windows the new session cookies."""
        if not self._mom_windows:
            return

        def job():
            cookies = self._ctx.cookies()
            for w in list(self._mom_windows):
                try:
                    w["ctx"].add_cookies(cookies)
                except Exception:
                    self._mom_windows.remove(w)
        try:
            self._call(job, 30)
        except Exception:
            pass

    def close(self) -> None:
        self._jobs.put(None)


def make_transport(kind: str, base_url: str, verify_tls: bool, on_log=None, browser: str = "auto") -> Transport:
    """kind: 'browser' (fallback to direct if no browser) or 'direct'."""
    say = on_log or (lambda s: log.info(s))
    if kind == "browser":
        try:
            t = BrowserTransport(base_url, browser_order(browser))
            name = {"chromium": "tool's own Chromium", "msedge": "Edge (separate hidden profile)",
                    "chrome": "Chrome (separate hidden profile)"}.get(t.channel_used, t.channel_used)
            say(f"Connection mode: browser engine - {name}")
            for line in t.tried:
                log.info("browser not used: %s", line)
            return t
        except Exception as exc:
            say(f"Browser engine unavailable ({str(exc)[:200]}) - using direct connection")
    say("Connection mode: direct")
    return DirectTransport(verify_tls)
