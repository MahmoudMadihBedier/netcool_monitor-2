"""Overview page: KPI cards, active outages of all sessions, sessions table, alerts of the shift."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core import clock
from . import theme as T
from .widgets import KpiCard


class OverviewPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=T.px(12))
        self.app = app
        self._build()

    def _build(self) -> None:
        ov = self
        kp = tk.Frame(ov, bg=T.GREY_1)
        kp.pack(fill="x")
        self.ok = {}
        for key, label, accent in (("sessions", "Sessions running", T.GREY_9), ("out", "Outages (5+)", T.RED),
                                   ("pre", "Pre-outages (4)", T.FRESH_ORANGE),
                                   ("warn", "Sessions with data warnings", T.LEMON),
                                   ("casc", "Cascaded report", T.TURQUOISE)):
            c = KpiCard(kp, label, accent)
            c.pack(side="left", padx=(0, 8), fill="y")
            self.ok[key] = c
        self.ok["casc"].bind("<Button-1>", lambda e: self.app.upload_cascade())

        card = ttk.Frame(ov, style="Card.TFrame", padding=12)
        card.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(card, text="Active outages - all sessions", style="CardH2.TLabel").pack(anchor="w")
        ttk.Label(card, text="The same outage seen in several sessions (e.g. 2G + 3G filters) is shown once. "
                             "Double-click for the chain, mail and Global TT info.",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(0, 6))
        ocols = ("level", "n", "foc", "hub", "area", "fault", "tt", "team", "sessions")
        self.ov_out = ttk.Treeview(card, columns=ocols, show="headings", height=6)
        for c, t, w in (("level", "Level", 175), ("n", "Sites", 55), ("foc", "First of chain", 110),
                        ("hub", "HUB", 80), ("area", "Area", 160), ("fault", "Fault time", 80),
                        ("tt", "Global TT", 110), ("team", "Team", 80), ("sessions", "Seen in", 260)):
            self.ov_out.heading(c, text=t)
            self.ov_out.column(c, width=w, anchor="w", stretch=c == "sessions")
        for tag, col in (("RED", T.RED), ("ORANGE", T.FRESH_ORANGE), ("YELLOW", T.LEMON),
                         ("PRE", T.FRESH_ORANGE), ("POSSIBLE", T.AQUA)):
            self.ov_out.tag_configure(tag, background=col, foreground=T.WHITE if col == T.RED else T.BLACK)
        self.ov_out.pack(fill="x")
        self.ov_out.bind("<Double-1>", self._ov_outage_open)
        self._ov_out_map: dict = {}

        ttk.Label(card, text="Sessions", style="CardH2.TLabel").pack(anchor="w", pady=(14, 4))
        cols = ("name", "state", "filter", "sev", "rows", "pre", "outages", "upd")
        self.ov_tree = ttk.Treeview(card, columns=cols, show="headings", height=6)
        for c, t, w in (("name", "Session", 140), ("state", "State", 100), ("filter", "Filter", 380),
                        ("sev", "Severities", 140), ("rows", "Rows", 60), ("pre", "Pre-outages", 90),
                        ("outages", "Outages", 70), ("upd", "Last refresh", 90)):
            self.ov_tree.heading(c, text=t)
            self.ov_tree.column(c, width=w, anchor="w", stretch=c == "filter")
        self.ov_tree.tag_configure("alert", background=T.RED, foreground=T.WHITE)
        self.ov_tree.tag_configure("pre", background=T.FRESH_ORANGE)
        self.ov_tree.tag_configure("bad", background=T.RED_TINT)
        self.ov_tree.pack(fill="x")
        self.ov_tree.bind("<Double-1>", self._ov_open)

        ttk.Label(card, text="Alerts this shift", style="CardH2.TLabel").pack(anchor="w", pady=(14, 4))
        acols = ("time", "session", "what", "sites")
        self.ov_alerts = ttk.Treeview(card, columns=acols, show="headings", height=7)
        for c, t, w in (("time", "Time", 80), ("session", "Session", 140), ("what", "Alert", 460),
                        ("sites", "Sites", 360)):
            self.ov_alerts.heading(c, text=t)
            self.ov_alerts.column(c, width=w, anchor="w", stretch=c == "sites")
        self.ov_alerts.pack(fill="both", expand=True)
        self.empty_hint = ttk.Label(card, text="No sessions yet - press  ＋ New session  and pick a filter "
                                               "(e.g. 2G_Marco + 2g_H_easymonitoring, 3G_Marco + "
                                               "3g_H_easymonitoring).", style="CardMuted.TLabel")
        self.empty_hint.pack(anchor="w", pady=(8, 0))


    def refresh(self) -> None:
        try:
            self.ov_tree.delete(*self.ov_tree.get_children())
        except tk.TclError:
            return
        names = {5: "Crit", 4: "Maj", 3: "Min", 2: "Warn", 1: "Ind", 0: "Clr"}
        warn = 0
        merged: dict = {}
        for sid, v in self.app.sm.views.items():
            w = v.worker
            if any(c.level in ("ERROR", "WARN") for c in v.checks) or v.state_name in ("BLIND", "WARNING"):
                warn += 1
            tag = "alert" if v.alert_count else ("pre" if v.pre_count else
                                                 ("bad" if v.state_name in ("BLIND", "WARNING") else ""))
            self.ov_tree.insert("", "end", iid=str(sid), values=(
                w.session_name, v.state_name, f"{w.filter_id}   [{w.view_id}]",
                "+".join(names[s] for s in sorted(w.severities, reverse=True)), w.total_rows,
                v.pre_count, v.alert_count, f"{w.last_update:%H:%M:%S}" if w.last_update else "-"),
                tags=(tag,) if tag else ())
            for o in v.outages:
                if o.level == "WATCH":
                    continue
                cur = merged.get(o.key)
                if cur is None or o.count > cur[0].count:
                    merged[o.key] = (o, sid, (cur[2] if cur else []) + [w.session_name])
                else:
                    cur[2].append(w.session_name)
        if self.app.sm.views:
            self.empty_hint.pack_forget()
        self.ov_out.delete(*self.ov_out.get_children())
        self._ov_out_map = {}
        tt = self.app.settings.rules.tt_minutes
        n_out = n_pre = 0
        for i, (o, sid, names_) in enumerate(sorted(merged.values(), key=lambda x: (x[0].level != "OUTAGE",
                                                                                  -x[0].count))):
            n_out += o.level == "OUTAGE"
            n_pre += o.level == "PRE-OUTAGE"
            tag = "POSSIBLE" if o.confidence == "MEDIUM" else (o.color or "RED") if o.level == "OUTAGE" else "PRE"
            due = o.tt_due(tt)
            ttxt = "-"
            if due:
                mins = int((due - clock.now()).total_seconds() // 60)
                ttxt = f"{due:%H:%M} (in {mins}m)" if mins >= 0 else f"{due:%H:%M} DUE!"
            self.ov_out.insert("", "end", iid=str(i), tags=(tag,), values=(
                ("POSSIBLE " if o.confidence == "MEDIUM" else "") + o.level + (f" {o.color}" if o.color else ""),
                o.count, o.foc or "-", o.hub or "-", o.area or "-",
                f"{o.fault_time:%H:%M}" if o.fault_time else "-", ttxt, o.team, ", ".join(sorted(set(names_)))))
            self._ov_out_map[str(i)] = (sid, o)
        self.ok["sessions"].set(len(self.app.sm.views))
        self.ok["out"].set(n_out, T.RED if n_out else T.BLACK)
        self.ok["pre"].set(n_pre, T.FRESH_ORANGE if n_pre else T.BLACK)
        self.ok["warn"].set(warn, T.RED_DARK if warn else T.BLACK)

    def add_alert(self, when: str, session: str, title: str, sites: str) -> None:
        self.ov_alerts.insert("", 0, values=(when, session, title, sites))

    def _ov_open(self, _=None) -> None:
        sel = self.ov_tree.selection()
        if sel and int(sel[0]) in self.app.sm.views:
            self.app.show_session(int(sel[0]))

    def _ov_outage_open(self, _=None) -> None:
        sel = self.ov_out.selection()
        if sel and sel[0] in self._ov_out_map:
            sid, o = self._ov_out_map[sel[0]]
            self.app.open_outage(sid, o)

