"""Heartbeat plugin: the Heartbeat Failure task (menu + toolbar) with its result window and PDF."""
from __future__ import annotations

import logging
from tkinter import messagebox

from core.pdf_export import html_to_pdf
from kernel import MenuItem
from ui.widgets import Toast
from .heartbeat import build_html, run_task
from .ui import HeartbeatDialog, HeartbeatWindow, ask_pdf_path, open_file

log = logging.getLogger(__name__)


class HeartbeatPlugin:
    name = "heartbeat"

    def start(self, host) -> None:
        self.host = host
        host.add_menu(MenuItem("Heartbeat task…", self.run, menu="Tasks", icon="💓", toolbar=True, shortcut="Ctrl+H"))

    def stop(self) -> None:
        pass

    def run(self) -> None:
        host, root = self.host, self.host.ui
        if not host.client:
            return
        dlg = HeartbeatDialog(root, host.settings, host.filters, host.views)
        if not dlg.result:
            return
        r = dlg.result
        hs = host.settings.heartbeat
        hs.hb_filter, hs.hb_view, hs.ref_filter, hs.ref_view = r["hb_filter"], r["hb_view"], r["ref_filter"], r["ref_view"]
        hs.window, hs.hours, hs.severities = r["window"], r["hours"], sorted(r["severities"], reverse=True)
        host.save_settings()
        Toast.show(root, "Heartbeat task running - reading both filters from MOM…")
        client, rules, ds = host.client, host.settings.rules, host.settings.server.datasource

        def work():
            return run_task(client, rules, r["hb_filter"], r["hb_view"], r["ref_filter"], r["ref_view"],
                            r["since"], r["until"], r["severities"], hs.summary_regex, ds, r.get("title", ""))

        def done(rep, err):
            if err:
                messagebox.showerror("Heartbeat task", f"Could not read MOM: {err}", parent=root)
                return
            logging.getLogger("alerts").warning("Heartbeat task: %d to investigate, %d without TT",
                                                len(rep.investigate), len(rep.without_tt))
            HeartbeatWindow(root, rep, on_pdf=self.pdf, on_rerun=self.run)
        host.run_bg(work, done)

    def pdf(self, rep) -> None:
        host, root = self.host, self.host.ui
        path = ask_pdf_path(root, rep)
        if not path:
            return
        html = build_html(rep, (host.client.user if host.client else "") or "")
        transport = getattr(host.client, "t", None)

        def done(out, err):
            if err:
                messagebox.showerror("Heartbeat PDF", f"Could not create the PDF: {err}", parent=root)
                return
            Toast.show(root, f"Heartbeat report saved:\n{out}", ms=7000)
            open_file(out)
        host.run_bg(lambda: html_to_pdf(html, path, "Heartbeat Failure Task", False, transport), done)
