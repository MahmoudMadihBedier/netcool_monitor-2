"""Client for IBM DASH / OMNIbus WebGUI - speaks to the server exactly like the Event Viewer.

The Event Viewer page reads its rows from the WebGUI REST provider:

    /ibm/tivoli/rest/providers/OMNIbusWebGUI/datasources/any/datasets/...

Sequence copied from the captured browser traffic:
  login:    POST /ibm/console/j_security_check -> navigation.do?XSS=...
            GET  userPreferences/items                          (once)
  open a session (new clientId):
            GET  eventGrouping/originItems  (eventsEnabled=false, clearCache)
            GET  eventData/columns
            GET  eventData/originItems      (start=0, clearCache)  <- the rows
            GET  eventData                  (summary)
  refresh:  GET  eventData/originItems  page by page (start/count), then eventData
  close:    DELETE eventGrouping + eventData

One login is shared by every session; each session has its own clientId.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from .config import ServerSettings
from .models import FetchResult, FilterInfo, ViewInfo
from .transport import Response, Transport, TransportError, make_transport

log = logging.getLogger(__name__)

REST = "/ibm/tivoli/rest/providers/OMNIbusWebGUI/datasources/any/datasets"


class NetcoolError(Exception):
    """Server/network problem (retry later)."""


class NetworkError(NetcoolError):
    """Could not reach the server at all."""


class ServerError(NetcoolError):
    """Server answered with an error (e.g. HTTP 500). `status` and `snippet` for diagnosis."""
    def __init__(self, msg: str, status: int = 0, snippet: str = ""):
        super().__init__(msg)
        self.status, self.snippet = status, snippet


class AuthError(NetcoolError):
    """Wrong user / password."""


class SessionExpired(NetcoolError):
    """Server keeps sending the logon page even after re-login."""


def new_client_id() -> str:
    return str(uuid.uuid4())


def _ms() -> str:
    return str(int(time.time() * 1000))


def _snippet(text: str, n: int = 300) -> str:
    t = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:n]


# Words DASH / WebSphere use on the "this user is already logged in" page.
CONFLICT_WORDS = re.compile(r"already\s+(logged|signed)\s+in|currently\s+(logged|signed)\s+in|"
                            r"another\s+(user|session)|same\s+user|other\s+session|session\s+is\s+(already\s+)?active|"
                            r"multiple\s+(logins|sessions)", re.I)
_GO = re.compile(r"force|log\s*-?\s*out|logout|log\s*off|end|terminate|continue|proceed|yes|ok|replace|take", re.I)
_STOP = re.compile(r"cancel|return|back|\bno\b|abort", re.I)


class _Forms(HTMLParser):
    """Collects <form>s with their inputs and the text near radio buttons (stdlib only)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list = []
        self._cur = None
        self._last_radio = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._cur = {"action": a.get("action", ""), "method": (a.get("method") or "post").lower(), "inputs": []}
            self.forms.append(self._cur)
        elif tag in ("input", "button") and self._cur is not None:
            inp = {"type": (a.get("type") or ("submit" if tag == "button" else "text")).lower(),
                   "name": a.get("name", ""), "value": a.get("value", ""), "checked": "checked" in a,
                   "label": ""}
            self._cur["inputs"].append(inp)
            self._last_radio = inp if inp["type"] == "radio" else None
        elif tag == "form" and self._cur is not None:
            self._cur = None

    def handle_endtag(self, tag):
        if tag == "form":
            self._cur = None

    def handle_data(self, data):
        if self._last_radio is not None and data.strip():
            self._last_radio["label"] += " " + data.strip()


def conflict_form(html_text: str):
    """If the page is a 'user already logged in' page, return (action, method, data) that CONTINUES the
    sign-in (logs out the other session). None when the page is something else."""
    if not html_text or not CONFLICT_WORDS.search(re.sub(r"<[^>]+>", " ", html_text)):
        return None
    p = _Forms()
    try:
        p.feed(html_text)
    except Exception:
        return None
    for f in p.forms:
        inputs = f["inputs"]
        data = {i["name"]: i["value"] for i in inputs if i["type"] == "hidden" and i["name"]}
        radios = [i for i in inputs if i["type"] == "radio" and i["name"]]
        if radios:
            pick = next((r for r in radios if _GO.search(r["value"] + " " + r["label"])
                         and not _STOP.search(r["value"] + " " + r["label"])), None)
            if pick is None:
                continue
            data[pick["name"]] = pick["value"]
        subs = [i for i in inputs if i["type"] in ("submit", "button", "image")]
        sub = next((b for b in subs if b["name"] and _GO.search(b["value"]) and not _STOP.search(b["value"])), None)
        if sub is None and not radios:
            sub = next((b for b in subs if _GO.search(b["value"] or "") and not _STOP.search(b["value"] or "")), None)
            if sub is None:
                continue
        if sub is not None and sub["name"]:
            data[sub["name"]] = sub["value"]
        if "j_password" in data or any(i["type"] == "password" for i in inputs):
            continue                      # that is the normal login form, not the conflict page
        return f["action"], f["method"], data
    return None


class NetcoolClient:
    def __init__(self, cfg: ServerSettings, transport: Optional[Transport] = None, on_log=None):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")
        self._say = on_log or (lambda s: log.info(s))
        self.t: Transport = transport or make_transport(cfg.transport, self.base, cfg.verify_tls, self._say,
                                                        getattr(cfg, "browser", "auto"))
        self._login_lock = threading.Lock()
        self._generation = 0
        self._user: Optional[str] = None
        self._password: Optional[str] = None
        self.xss = ""
        self.portlet_ns = cfg.portlet_namespace
        self.referer = self.base + "/ibm/console/"
        self.logged_in = False
        self.on_relogin: Optional[Callable[[bool, str], None]] = None
        self.on_conflict: Optional[Callable[[str], None]] = None
        self._kicks: deque = deque(maxlen=10)       # times the server dropped our session
        self._last_login = 0.0
        self.conflict_note = ""                      # last "same user elsewhere" situation (for the UI)
        self.parallel = True                         # read the pages of a filter in parallel

    @property
    def mode(self) -> str:
        return self.t.name

    def close(self) -> None:
        self.t.close()

    # ------------------------------------------------------------- basics
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else self.base + "/" + path.lstrip("/")

    def _send(self, method, path, params=None, data=None, headers=None) -> Response:
        try:
            return self.t.request(method, self._url(path), params=params, data=data,
                                  headers=headers, timeout=self.cfg.request_timeout_s)
        except TransportError as exc:
            raise NetworkError(str(exc)) from exc

    @staticmethod
    def _looks_logged_out(r: Response) -> bool:
        if r.status in (401, 403):
            return True
        if "logon.jsp" in r.url or "j_security_check" in r.url:
            return True
        if any("logon.jsp" in (u or "") for u in r.redirect_urls):
            return True
        return "text/html" in r.ctype.lower() and "j_security_check" in r.text[:20000]

    # -------------------------------------------------------------- login
    def login(self, user: str, password: str) -> None:
        with self._login_lock:
            self._do_login(user, password)
            self._user, self._password = user, password

    def _do_login(self, user: str, password: str) -> None:
        self.t.clear_cookies()
        self._send("GET", "/ibm/console/logon.jsp")
        r = self._send("POST", "/ibm/console/j_security_check",
                       data={"j_username": user, "j_password": password},
                       headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        for _ in range(2):           # "this user is already logged in elsewhere" page -> continue
            if "navigation.do" in r.url:
                break
            form = conflict_form(r.text or "")
            if form is None:
                break
            action, method, data = form
            target = urljoin(r.url, action or r.url)
            log.warning("Login: MOM says user %s is already signed in elsewhere - continuing", user)
            self.conflict_note = ("MOM said this user was already signed in elsewhere (for example MOM open "
                                  "in Edge). The tool continued the sign-in.")
            r = self._send("POST" if method != "get" else "GET", target,
                           data=data if method != "get" else None, params=data if method == "get" else None,
                           headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            if self.on_conflict:
                try:
                    self.on_conflict(self.conflict_note)
                except Exception:
                    pass
        q = urlparse(r.url).query.lower()
        if "navigation.do" not in r.url and ("logon.jsp" in r.url or "j_security_check" in r.url
                                             or "error" in q):
            raise AuthError("Login failed - check the user ID and password.")
        if r.status >= 400:
            raise ServerError(f"Login returned HTTP {r.status}", r.status, _snippet(r.text))

        self.xss = parse_qs(urlparse(r.url).query).get("XSS", [""])[0]
        if not self.xss:
            m = re.search(r"XSS=([0-9a-f]{16,})", r.text or "")
            self.xss = m.group(1) if m else ""
        self.referer = r.url
        self._open_event_viewer()
        self.t.park("/ibm/console/secure/isclite/tiles/nop.jsp")
        self._generation += 1
        self._last_login = time.monotonic()
        self.logged_in = True
        sync = getattr(self.t, "sync_mom_windows", None)
        if sync:
            sync()
        self._say(f"Signed in as {user} (portlet {self.portlet_ns})")

    def _open_event_viewer(self) -> None:
        """Same calls the console makes when you click 'Event Viewer' (best effort)."""
        try:
            if self.xss:
                r = self._send("POST", "/ibm/console/navfragment.do",
                               params={"pageID": "item.desktop.navigationElement.EventViewer",
                                       "moduleRef": "com.ibm.isclite.global.custom.module",
                                       "ISC.TASKTYPE": "0", "XSS": self.xss},
                               headers={"X-Requested-With": "XMLHttpRequest"})
                m = re.search(r"(contentRender\.do\?oid=(_\d+)[^\"'\s<>]*)", r.text or "")
                if m:
                    self.portlet_ns = f"ns_{m.group(2)}_"
                    cr = "/ibm/console/" + m.group(1).replace("&amp;", "&")
                    self._send("GET", cr)
                    self.referer = self._url(cr)
            self._send("GET", f"{REST}/userPreferences/items",
                       params={"properties": "all", "optimize": "true", "clearCache": "true",
                               "request.preventCache": _ms()}, headers=self._xhr())
        except NetcoolError as exc:
            log.warning("Event Viewer warm-up step failed (continuing): %s", exc)

    KICK_WINDOW_S = 600       # 2+ lost sessions in 10 minutes = someone else uses the same MOM user
    BACKOFF_S = 60            # then sign in again at most once a minute (do not fight every second)

    def relogin(self, seen_generation: int) -> None:
        with self._login_lock:
            if self._generation != seen_generation:
                return  # another session already logged in again
            if not self._user:
                raise SessionExpired("Not signed in")
            now = time.monotonic()
            if getattr(self, "_kick_gen", None) != seen_generation:     # count each lost session once
                self._kick_gen = seen_generation
                self._kicks.append(now)
            recent = [k for k in self._kicks if now - k <= self.KICK_WINDOW_S]
            if len(recent) >= 2:
                self.conflict_note = ("The MOM session of the tool was ended {} times in 10 minutes. The same MOM "
                                      "user is probably signed in in another browser (e.g. MOM open in Edge). "
                                      "Each sign-in ends the other one. Use 'Open MOM' in the tool to see MOM "
                                      "with the tool's login, or give the tool its own MOM user."
                                      ).format(len(recent))
                if self.on_conflict:
                    try:
                        self.on_conflict(self.conflict_note)
                    except Exception:
                        pass
                wait = self.BACKOFF_S - (now - self._last_login)
                if wait > 0:
                    raise SessionExpired(f"MOM user signed in elsewhere - signing in again in {int(wait)} s")
            log.warning("Server session expired - signing in again")
            ok, detail = False, ""
            try:
                self._do_login(self._user, self._password or "")
                ok, detail = True, "Signed in again automatically"
            except NetcoolError as exc:
                self.logged_in = False
                detail = f"Automatic sign-in failed: {exc}"
                raise
            finally:
                if self.on_relogin:
                    try:
                        self.on_relogin(ok, detail)
                    except Exception:
                        pass

    def logout(self) -> None:
        try:
            self._send("GET", "/ibm/console/logoff.jsp")
        except Exception:
            pass
        self.t.clear_cookies()
        self.logged_in = False
        self._user = self._password = None

    # ----------------------------------------------------------- requests
    def _xhr(self) -> dict:
        return {"Accept": "application/javascript, application/json",
                "X-Requested-With": "XMLHttpRequest", "Referer": self.referer}

    def _decode(self, r: Response, path: str):
        if r.status >= 400:
            snip = _snippet(r.text)
            raise ServerError(f"HTTP {r.status} from server on {path.split('datasets/')[-1]}"
                              + (f": {snip[:160]}" if snip else ""), r.status, snip)
        if not r.text.strip():
            return {}
        try:
            return json.loads(r.text)
        except ValueError:
            raise ServerError(f"Unexpected reply on {path.split('datasets/')[-1]}: "
                              f"{_snippet(r.text, 160)}", r.status, _snippet(r.text))

    def _json(self, method: str, path: str, params: Optional[dict] = None):
        for attempt in (1, 2):
            gen = self._generation
            r = self._send(method, path, params=params, headers=self._xhr())
            if self._looks_logged_out(r):
                if attempt == 1:
                    self.relogin(gen)
                    continue
                raise SessionExpired("Server keeps returning the logon page")
            return self._decode(r, path)

    def _json_many(self, path: str, param_list: list):
        """Several GETs at once (one round trip through the browser). Any problem -> None (caller falls
        back to the one-by-one path, which handles re-login and errors)."""
        try:
            res = self.t.request_many([("GET", self._url(path), p) for p in param_list],
                                      timeout=self.cfg.request_timeout_s)
        except TransportError:
            return None
        out = []
        for r in res:
            if isinstance(r, Exception) or self._looks_logged_out(r) or r.status >= 400:
                return None
            try:
                out.append(self._decode(r, path))
            except ServerError:
                return None
        return out

    def keepalive(self) -> Optional[int]:
        gen = self._generation
        r = self._send("GET", "/ibm/console/secure/isclite/tiles/nop.jsp",
                       headers={"X-Requested-With": "XMLHttpRequest"})
        if self._looks_logged_out(r):
            self.relogin(gen)
            return None
        r2 = self._send("GET", "/ibm/console/secure/isclite/tiles/sessionTimeLeft.jsp",
                        headers={"X-Requested-With": "XMLHttpRequest"})
        m = re.search(r"\b(\d{2,7})\b", _snippet(r2.text, 2000))
        return int(m.group(1)) if m else None

    # -------------------------------------------------------- filter lists
    @staticmethod
    def _props(item: dict) -> dict:
        return {p.get("id"): p.get("value") for p in item.get("properties", [])}

    def list_filters(self) -> list:
        d = self._json("GET", f"{REST}/filters/items", {
            "properties": "all", "optimize": "true",
            "condition": "(((TYPE = 'global') or (TYPE = 'user') or (TYPE = 'group')))",
            "clearCache": "true", "request.preventCache": _ms()})
        out = []
        for it in d.get("items", []):
            p = self._props(it)
            view = p.get("VIEWNAME") or ""
            out.append(FilterInfo(
                id=it.get("id", ""), label=it.get("label") or it.get("id", ""),
                name=p.get("NAME") or it.get("id", ""), type=p.get("TYPE") or "",
                datasource=self.cfg.datasource,  # browser always sends VFEP_A
                default_view=f"{view}:global" if view and ":" not in view else view))
        out.sort(key=lambda f: f.label.lower())
        return out

    def list_views(self) -> list:
        d = self._json("GET", f"{REST}/views/items", {
            "properties": "all", "optimize": "true",
            "condition": "(((TYPE = 'global') or (TYPE = 'user') or (TYPE = 'system')))",
            "clearCache": "true", "request.preventCache": _ms()})
        out = [ViewInfo(id=it.get("id", ""), label=it.get("label") or it.get("id", ""),
                        name=self._props(it).get("NAME") or it.get("id", ""))
               for it in d.get("items", [])]
        out.sort(key=lambda v: v.label.lower())
        return out

    # ---------------------------------------------------------- event data
    def _p(self, client_id: str, filter_id: str, view_id: str, datasource: str, **extra) -> dict:
        p = {"properties": "all", "param_filter": filter_id, "param_view": view_id,
             "param_datasources": datasource or self.cfg.datasource,
             "param_clientId": client_id, "param_guiorientation": "ltr",
             "param_textdirection": "ltr", "param_portletNamespace": self.portlet_ns,
             "menu": "alerts", "timezone": self.cfg.timezone, "theme": "oneui"}
        p.update(extra)
        p["request.preventCache"] = _ms()
        return p

    def open_dataset(self, client_id: str, filter_id: str, view_id: str, datasource: str) -> dict:
        """What the Event Viewer does before it reads rows for a (new) filter/view.
        Returns the view's columns {id: label} (used to find HUB / SubRegion in ANY view)."""
        try:
            self._json("GET", f"{REST}/eventGrouping/originItems",
                       self._p(client_id, filter_id, view_id, datasource, param_eventsEnabled="false",
                               start="0", count="100", clearCache="true"))
        except ServerError as exc:
            log.info("eventGrouping open ignored: %s", exc)
        d = self._json("GET", f"{REST}/eventData/columns",
                       self._p(client_id, filter_id, view_id, datasource, optimize="true"))
        return {c.get("id"): (c.get("label") or c.get("id") or "") for c in (d or {}).get("items", []) if c.get("id")}

    def fetch_events(self, client_id: str, filter_id: str, view_id: str, datasource: str,
                     page_size: int = 150, first: bool = False, max_rows: int = 20000) -> FetchResult:
        """Reads EVERY row of the filter, page by page, like the grid.
        Rows are de-duplicated by id. If MOM changed the list while we were paging (total moved),
        the read is repeated once so the result is one consistent picture."""
        labels = self.open_dataset(client_id, filter_id, view_id, datasource) if first else {}
        rows, total, complete = [], 0, True
        path = f"{REST}/eventData/originItems"
        for attempt in (1, 2):
            seen, rows, start, totals = set(), [], 0, []

            def take(d):
                items = d.get("items", []) or []
                tot = int(d.get("totalRows", d.get("filteredRows", len(items))) or 0)
                totals.append(tot)
                for it in items:
                    key = it.get("id") or it.get("uri") or id(it)
                    if key not in seen:
                        seen.add(key)
                        rows.append(it)
                return items, tot

            extra = {"start": "0", "count": str(page_size)}
            if first and attempt == 1:
                extra["clearCache"] = "true"
            items, total = take(self._json("GET", path, self._p(client_id, filter_id, view_id, datasource, **extra)))
            start = len(items)
            # the other pages all at once (like opening the grid), checked page by page
            if self.parallel and items and len(items) == page_size and start < min(total, max_rows):
                starts = list(range(page_size, min(total, max_rows), page_size))
                pages = self._json_many(path, [self._p(client_id, filter_id, view_id, datasource,
                                                       start=str(st), count=str(page_size)) for st in starts])
                if pages is not None:
                    ok = True
                    for st, d in zip(starts, pages):
                        got, _ = take(d)
                        expect = min(page_size, total - st)
                        if len(got) < expect:            # server sent a short page -> finish one by one
                            ok = False
                            start = st + len(got)
                            break
                        start = st + len(got)
                    if ok:
                        start = max(start, min(total, max_rows))
            while items and start < total and start < max_rows:      # one by one (also the fallback)
                items, total = take(self._json("GET", path, self._p(client_id, filter_id, view_id, datasource,
                                                                    start=str(start), count=str(page_size))))
                start += len(items)
                if not items:
                    break
            stable = len(set(totals)) == 1
            complete = stable and (len(rows) >= min(total, max_rows) or total == 0)
            if complete:
                break
            log.info("rows moved while paging (totals %s, got %d) - reading again", totals, len(rows))
        meta = {}
        try:  # summary call the grid makes after each refresh (keeps server state identical)
            sm = self._json("GET", f"{REST}/eventData",
                            self._p(client_id, filter_id, view_id, datasource, optimize="true")) or {}
            meta = {k: sm.get(k) for k in ("lastUpdate", "nodesInserted", "nodesDeleted", "nodesModified",
                                           "matchedRowCount", "totalRowCount")}
        except NetcoolError:
            pass
        cols = set()
        for it in rows[:40]:
            cols.update(p.get("id") for p in it.get("properties", []))
        return FetchResult(rows=rows, total=total, columns=cols, labels=labels, meta=meta,
                           complete=complete)

    def release(self, client_id: str, filter_id: str, view_id: str, datasource: str) -> None:
        for ds in ("eventGrouping", "eventData"):
            try:
                extra = {"optimize": "true"}
                if ds == "eventGrouping":
                    extra["param_eventsEnabled"] = "false"
                p = self._p(client_id, filter_id, view_id, datasource, **extra)
                p.pop("properties", None)
                self._json("DELETE", f"{REST}/{ds}", p)
            except Exception as exc:
                log.info("release %s(%s) ignored: %s", ds, client_id, exc)

    # ------------------------------------------------------------ diagnose
    def diagnose(self, filter_id: str, view_id: str) -> list:
        """Step-by-step check used by the Diagnostics button. Returns lines of text."""
        out = [f"Server: {self.base}", f"Connection mode: {self.mode}",
               f"Signed in: {self.logged_in}  portlet: {self.portlet_ns}  XSS: {bool(self.xss)}"]
        cid = new_client_id()
        steps = [
            ("keep-alive", lambda: self.keepalive()),
            ("open dataset", lambda: self.open_dataset(cid, filter_id, view_id, self.cfg.datasource)),
            ("read rows", lambda: self.fetch_events(cid, filter_id, view_id, self.cfg.datasource,
                                                    first=False)),
        ]
        for name, fn in steps:
            t0 = time.time()
            try:
                res = fn()
                extra = f"{res.total} rows, columns: {', '.join(sorted(res.columns))[:300]}" \
                    if hasattr(res, "total") else (f"result {res}" if res is not None else "")
                out.append(f"OK    {name} ({time.time() - t0:.1f}s) {extra}")
            except ServerError as exc:
                out.append(f"FAIL  {name}: HTTP {exc.status} - {exc.snippet[:300]}")
            except Exception as exc:
                out.append(f"FAIL  {name}: {exc}")
        self.release(cid, filter_id, view_id, self.cfg.datasource)
        return out

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def user(self) -> Optional[str]:
        return self._user
