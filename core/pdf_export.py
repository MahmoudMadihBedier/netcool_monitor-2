"""HTML -> PDF with the Edge/Chrome engine already used by the tool (no extra package)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

FOOTER = ("<div style='font-family:Arial;font-size:7px;color:#888;width:100%;padding:0 10mm;"
          "display:flex;justify-content:space-between'><span>{title}</span>"
          "<span>Page <span class='pageNumber'></span> of <span class='totalPages'></span></span></div>")


def _render(page, html: str, out: Path, title: str, landscape: bool) -> None:
    page.set_content(html, wait_until="load")
    page.pdf(path=str(out), format="A4", landscape=landscape, print_background=True,
             display_header_footer=True, header_template="<div></div>",
             footer_template=FOOTER.format(title=title),
             margin={"top": "10mm", "bottom": "14mm", "left": "8mm", "right": "8mm"})


def html_to_pdf(html: str, out_path, title: str = "", landscape: bool = True, transport=None) -> Path:
    """Returns the written file: a .pdf, or an .html next to it when no PDF engine is available."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 1) the browser the tool already runs (connection mode 'browser')
    if transport is not None and hasattr(transport, "_call") and hasattr(transport, "_ctx"):
        try:
            def job():
                pg = transport._ctx.new_page()
                try:
                    _render(pg, html, out, title, landscape)
                finally:
                    pg.close()
            transport._call(job, 120)
            return out
        except Exception as exc:
            log.warning("PDF through the running browser failed: %s", exc)
    # 2) a short-lived headless Edge / Chrome / bundled Chromium (own thread: Playwright sync rules)
    err: list = []

    def run():
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser, last = None, None
                for ch in ("msedge", "chrome", ""):
                    try:
                        browser = p.chromium.launch(**({"channel": ch} if ch else {}))
                        break
                    except Exception as exc:  # try the next browser
                        last = exc
                if browser is None:
                    raise RuntimeError(f"no browser: {last}")
                try:
                    _render(browser.new_page(), html, out, title, landscape)
                finally:
                    browser.close()
        except Exception as exc:
            err.append(exc)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(180)
    if not err and out.exists():
        return out
    log.warning("PDF engine unavailable (%s) - saving HTML instead", err[0] if err else "timeout")
    alt = out.with_suffix(".html")
    alt.write_text(html, encoding="utf-8")
    return alt
