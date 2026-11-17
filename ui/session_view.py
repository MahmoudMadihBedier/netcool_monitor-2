"""One tab = one independent monitoring session."""
from __future__ import annotations

from core import clock
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from core import messages as m
from core.worker import SessionWorker
from . import theme as T
from .widgets import Banner, KpiCard, SeverityBar

MAX_LOG_LINES = 1500
MAX_EVENT_ROWS = 6000
SEV_SHORT = {5: "Critical", 4: "Major", 3: "Minor", 2: "Warning", 1: "Indeterminate", 0: "Clear"}


class SessionView(ttk.Frame):
    def __init__(self, master, worker: SessionWorker, on_change_filter, on_close, on_state=None,
                 on_severities=None, on_open_outage=None, actions=None, on_plan=None):
        super().__init__(master, padding=10)
        self.worker = worker
        self._on_state = on_state or (lambda *_: None)
        self._on_sev = on_severities or (lambda *_: None)
        self._on_open = on_open_outage or (lambda *_: None)
        self._actions = actions
        self.purpose_guess = lambda: ""  # app: what the AI thinks this session is for ("" = no AI / no guess)
        self._guessed = False
        self.state_name = "CONNECTING"
        self.alert_count = 0
        self.pre_count = 0
        self.outages: list = []
        self.checks: list = []
        self._event_sig = None
        self._color_tags: set = set()

        # ---------------- header card
        card = ttk.Frame(self, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x")
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        self.status = T.pill(top, "CONNECTING", T.STATE_COLORS["CONNECTING"])
        self.status.pack(side="left")
        self.detail = ttk.Label(top, text="", style="CardMuted.TLabel")
        self.detail.pack(side="left", padx=10)
        ttk.Button(top, text="Close session", command=on_close).pack(side="right")
        ttk.Button(top, text="⟳ Refresh now", command=worker.refresh_now).pack(side="right", padx=6)
        ttk.Button(top, text="Change filter", style="Primary.TButton",
                   command=on_change_filter).pack(side="right")
        if on_plan:
            ttk.Button(top, text="🎯 Purpose / alarms", command=on_plan).pack(side="right", padx=6)
        self.filter_lbl = ttk.Label(card, text="", style="Card.TLabel", font=(T.FONT, 10, "bold"))
        self.filter_lbl.pack(anchor="w", pady=(8, 0))
        self.view_lbl = ttk.Label(card, text="", style="CardMuted.TLabel")
        self.view_lbl.pack(anchor="w")
        self.plan_lbl = tk.Label(card, text="", bg=T.WHITE, fg=T.TURQUOISE, font=(T.FONT, 9, "bold"), anchor="w")
        self.plan_lbl.pack(anchor="w")
        live = tk.Frame(card, bg=T.WHITE)
        live.pack(anchor="w", pady=(4, 0), fill="x")
        self.mom_dot = tk.Label(live, text="●", bg=T.WHITE, fg=T.GREY_3, font=(T.FONT, 11))
        self.mom_dot.pack(side="left")
        self.mom_lbl = tk.Label(live, text="Waiting for the first MOM refresh…", bg=T.WHITE, fg=T.GREY_9,
                                font=(T.FONT, 9, "bold"))
        self.mom_lbl.pack(side="left", padx=(4, 0))
        self.next_lbl = tk.Label(live, text="", bg=T.WHITE, fg=T.GREY_6, font=(T.FONT, 9))
        self.next_lbl.pack(side="left", padx=(12, 0))
        self.more_btn = ttk.Button(live, text="", style="Small.TButton", command=self._toggle_summary)
        self.more_btn.pack(side="right")
        self._next_refresh = None
        self.after(1000, self._countdown)
        row = tk.Frame(card, bg=T.WHITE)
        row.pack(anchor="w", pady=(8, 0), fill="x")
        self.sevbar = SeverityBar(row, worker.severities, self._sev_changed)
        self.sevbar.pack(side="left")
        self.interval_v = tk.StringVar(value=f"{worker.interval} s")
        cb = ttk.Combobox(top, textvariable=self.interval_v, width=6, state="readonly",
                          values=[f"{x} s" for x in worker.INTERVALS])
        cb.pack(side="right", padx=(4, 10))
        tk.Label(top, text="Live refresh:", bg=T.WHITE, fg=T.GREY_9, font=(T.FONT, 9, "bold")).pack(side="right")
        cb.bind("<<ComboboxSelected>>", self._interval_changed)
        self._on_interval = None
        self.update_filter_labels()

        kpis = tk.Frame(self, bg=T.GREY_1)
        kpis.pack(fill="x", pady=(8, 0))
        self.k = {}
        for key, label, accent in (("rows", "Rows in filter", T.GREY_9), ("down", "Down physical sites", T.TURQUOISE),
                                   ("watch", "Watching (2-3)", T.LEMON), ("pre", "Pre-outages (4)", T.FRESH_ORANGE),
                                   ("out", "Outages (5+)", T.RED), ("upd", "Last refresh", T.GREY_6)):
            c = KpiCard(kpis, label, accent)
            c.pack(side="left", padx=(0, 8), fill="y")
            self.k[key] = c
        self.kpis = kpis
        self.banner = Banner(self)
        self.banner.pack(fill="x", pady=(6, 0))
        # short screens (laptops): start with the summary folded so the tables keep their height
        self._summary = self.winfo_screenheight() >= 900
        self._apply_summary()

        # ---------------- outages (top) + live MOM grid / log (bottom), resizable
        pane = ttk.Panedwindow(self, orient="vertical")
        pane.pack(fill="both", expand=True, pady=(8, 0))
        f1 = ttk.Frame(pane, style="Card.TFrame", padding=8)
        bottom = ttk.Notebook(pane, style="Inner.TNotebook")
        pane.add(f1, weight=1)
        pane.add(bottom, weight=2)
        self.inner = bottom

        bar = ttk.Frame(f1, style="Card.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text="Outages", style="CardH2.TLabel").pack(side="left")
        self.actions_menu = tk.Menu(self, tearoff=0)
        self.actions_btn = ttk.Menubutton(bar, text="Actions ▾", menu=self.actions_menu, style="Primary.TMenubutton")
        self.actions_btn.pack(side="right", padx=2)
        ttk.Button(bar, text="Details", style="Small.TButton", command=lambda: self._act(None)).pack(side="right", padx=2)
        ttk.Label(bar, text="   same chain · same HUB + area · same Node + area. Double-click or right-click a row.",
                  style="CardMuted.TLabel").pack(side="left")   # packed last: shrinks first on small screens
        self.action_bar = bar
        self._action_buttons: list = []
        cols = ("level", "n", "foc", "hub", "area", "region", "first", "fault", "tt", "team", "conf", "ai")
        self.otv = ttk.Treeview(f1, columns=cols, show="headings", height=5, selectmode="browse")
        for c, t, w in (("level", "Level", 175), ("n", "Sites", 50), ("foc", "First of chain", 110),
                        ("hub", "HUB", 80), ("area", "Area", 150), ("region", "Region", 80),
                        ("first", "First down", 80), ("fault", "Fault time", 80), ("tt", "Global TT", 125),
                        ("team", "Team", 80), ("conf", "Evidence", 120), ("ai", "AI advice", 150)):
            self.otv.heading(c, text=t)
            self.otv.column(c, width=w, anchor="w", stretch=c in ("area", "conf"))
        for lvl, col in (("OUTAGE_RED", T.RED), ("OUTAGE_ORANGE", T.FRESH_ORANGE), ("OUTAGE_YELLOW", T.LEMON),
                         ("PRE", T.FRESH_ORANGE), ("WATCH", T.WHITE), ("POSSIBLE", T.AQUA)):
            self.otv.tag_configure(lvl, background=col,
                                   foreground=T.WHITE if col in (T.RED,) else T.BLACK)
        self.otv.pack(fill="both", expand=True, pady=(6, 0))
        self.otv.bind("<Double-1>", lambda e: self._act(None))
        self.otv.bind("<Button-3>", self._outage_menu)
        self.refresh_actions()
        self.empty_lbl = ttk.Label(f1, text="No down-site groups right now.", style="CardMuted.TLabel")

        f2 = ttk.Frame(bottom, style="Card.TFrame", padding=8)
        bottom.add(f2, text="  ● Live events (MOM)  ")
        hdr = ttk.Frame(f2, style="Card.TFrame")
        hdr.pack(fill="x")
        self.ev_count = ttk.Label(hdr, text="Waiting for data…", style="Card.TLabel", font=(T.FONT, 9, "bold"))
        self.ev_count.pack(side="left")
        self.ev_delta = tk.Label(hdr, text="", bg=T.WHITE, fg=T.GREY_9, font=(T.FONT, 9))
        self.ev_delta.pack(side="left", padx=(10, 0))
        self.search_v = tk.StringVar()
        ttk.Entry(hdr, textvariable=self.search_v, width=26).pack(side="right")
        ttk.Label(hdr, text="Search:", style="CardMuted.TLabel").pack(side="right", padx=4)
        ttk.Label(hdr, text="NEW = appeared · UPD = changed in MOM   ", style="CardMuted.TLabel").pack(side="right")
        self.search_v.trace_add("write", lambda *_: self._render_events(force=True))
        ecols = ("mark", "sev", "site", "down", "last", "hub", "area", "region", "node", "fc", "tt", "summary")
        wrap = ttk.Frame(f2, style="Card.TFrame")
        wrap.pack(fill="both", expand=True, pady=(6, 0))
        self.events = ttk.Treeview(wrap, columns=ecols, show="headings", height=6)
        for c, t, w in (("mark", "", 46), ("sev", "Severity", 78), ("site", "SiteID", 80), ("down", "First", 122),
                        ("last", "Last", 122), ("hub", "HUB", 110), ("area", "SubRegion", 125),
                        ("region", "Region", 70), ("node", "Node", 65), ("fc", "FaultCode", 90),
                        ("tt", "TT", 120), ("summary", "Summary", 360)):
            self.events.heading(c, text=t)
            self.events.column(c, width=w, minwidth=30, anchor="w", stretch=c == "summary")
        self.events.tag_configure("newrow", font=(T.FONT, 9, "bold"))
        esb = ttk.Scrollbar(wrap, orient="vertical", command=self.events.yview)
        self.events.configure(yscrollcommand=esb.set)
        self.events.pack(side="left", fill="both", expand=True)
        esb.pack(side="right", fill="y")
        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="Copy SiteID", command=lambda: self._copy_sel(2))
        self._menu.add_command(label="Copy row", command=lambda: self._copy_sel(None))
        self.events.bind("<Button-3>", self._popup)
        self._shown: dict = {}          # iid -> (values, tags) currently in the grid
        self._marks: dict = {}          # iid -> (text, until monotonic)
        self.tt_lookup = lambda o: ""   # app: TT number already opened for an outage

        f3 = ttk.Frame(bottom, style="Card.TFrame", padding=8)
        bottom.add(f3, text="  Session log  ")
        self.log = tk.Text(f3, height=6, state="disabled", font=(T.MONO, 9), bg=T.BLACK, fg=T.WHITE,
                           insertbackground=T.WHITE, relief="flat", padx=8, pady=6)
        self.log.tag_configure("ALERT", foreground="#FF6B6B", font=(T.MONO, 9, "bold"))
        self.log.tag_configure("ERROR", foreground="#FF6B6B")
        self.log.tag_configure("WARN", foreground="#FFC857")
        self.log.tag_configure("OK", foreground="#7BE495")
        self.log.pack(fill="both", expand=True)

        self._last_events: list = []

    # ---------------------------------------------------------------- api
    def update_filter_labels(self) -> None:
        w = self.worker
        guess = self.purpose_guess()
        ai = f"   ·   🧠 AI thinks: {guess}" if guess and guess != w.plan.purpose else ""
        self.plan_lbl.config(text=f"🎯 {w.plan.summary()}{ai}")
        self.filter_lbl.config(text=f"Filter:  {w.filter_id}")
        self.view_lbl.config(text=f"View:  {w.view_id}   ·   live refresh every {w.interval}s   ·   "
                                  f"pre-outage at {w.rules.min_sites}, outage at {w.rules.outage_sites} "
                                  f"physical sites   ·   Global TT at minute {w.rules.tt_minutes}")

    def _interval_changed(self, _=None) -> None:
        try:
            sec = int(self.interval_v.get().split()[0])
        except (ValueError, IndexError):
            return
        self.worker.set_interval(sec)
        self.update_filter_labels()
        if self._on_interval:
            self._on_interval(self)

    def _sev_changed(self, sel: set) -> None:
        self.worker.set_severities(sel)
        self._on_sev(self)
        self._render_events(force=True)

    def handle(self, msg) -> None:
        if isinstance(msg, m.StateChanged):
            self.state_name = msg.state
            self.status.config(text=msg.state, bg=T.STATE_COLORS.get(msg.state, T.GREY_6))
            self.detail.config(text=msg.detail)
            self._on_state(self)
        elif isinstance(msg, m.Snapshot):
            self._snapshot(msg)
        elif isinstance(msg, m.LogLine):
            self._log(msg.level, msg.text)
        elif isinstance(msg, m.AlertCleared):
            self._log("OK", f"CLEARED  {msg.title}")

    def _snapshot(self, s: m.Snapshot) -> None:
        self.outages, self.checks = list(s.outages), list(s.checks)
        meta = s.meta or {}
        parts = []
        lu = meta.get("lastUpdate")
        if isinstance(lu, (int, float)) and lu > 0:
            parts.append(f"MOM data updated {clock.from_epoch(lu / 1000):%H:%M:%S}")
        else:
            parts.append(f"Read at {s.at:%H:%M:%S}")
        for k, word in (("nodesInserted", "new"), ("nodesDeleted", "cleared"), ("nodesModified", "changed")):
            v = meta.get(k)
            if isinstance(v, int):
                parts.append(f"{v} {word}")
        parts.append(f"{s.total_rows} rows read" + ("" if s.complete else " (INCOMPLETE - re-reading)"))
        self.mom_lbl.config(text="   ·   ".join(parts), fg=T.GREY_9 if s.complete else T.RED_DARK)
        self.mom_dot.config(fg=T.GREEN if s.complete else T.FRESH_ORANGE)
        self._next_refresh = s.next_refresh
        outs = [o for o in s.outages if o.level == "OUTAGE"]
        pres = [o for o in s.outages if o.level == "PRE-OUTAGE"]
        self.alert_count, self.pre_count = len(outs), len(pres)
        self.k["rows"].set(s.total_rows)
        self.k["down"].set(s.physical_down)
        self.k["watch"].set(sum(1 for o in s.outages if o.level == "WATCH"))
        self.k["pre"].set(len(pres), T.FRESH_ORANGE if pres else T.BLACK)
        self.k["out"].set(len(outs), T.RED if outs else T.BLACK)
        self.k["upd"].set(f"{s.at:%H:%M:%S}")
        self.sevbar.set_counts(s.sev_counts)
        self.banner.show(s.checks)
        self._render_outages()
        if s.fresh and not self._guessed and s.events:
            self._guessed = True
            self.update_filter_labels()
        self._last_events = sorted(s.events, key=lambda e: (e.down_at or e.last_at or datetime.min, e.serial),
                                   reverse=True)
        if s.fresh:
            now = time.monotonic()
            keep = max(60, 3 * (s.interval or self.worker.interval))
            for k in s.changed_ids:
                self._marks["r" + k] = ("UPD", now + keep)
            for k in s.new_ids:
                self._marks["r" + k] = ("NEW", now + keep)
            for k in [k for k, (_, until) in self._marks.items() if until < now]:
                del self._marks[k]
            sel = self.worker.severities
            n_new = sum(1 for e in s.events if e.serial in s.new_ids and e.severity in sel)
            n_chg = sum(1 for e in s.events if e.serial in s.changed_ids and e.severity in sel)
            n_gone = sum(1 for e in s.removed if e.severity in sel)
            txt = f"last refresh {s.at:%H:%M:%S}:  +{n_new} new   ·   {n_chg} updated   ·   {n_gone} gone"
            self.ev_delta.config(text=txt, fg=T.RED_DARK if n_new else T.GREY_9)
        self._render_events(force=True)
        self._on_state(self)

    def _render_outages(self) -> None:
        sel = self.otv.selection()
        self.otv.delete(*self.otv.get_children())
        tt = self.worker.rules.tt_minutes
        for i, o in enumerate(self.outages[:300]):
            if o.confidence == "MEDIUM":
                tag = "POSSIBLE"
            elif o.level == "OUTAGE":
                tag = f"OUTAGE_{o.color or 'RED'}"
            elif o.level == "PRE-OUTAGE":
                tag = "PRE"
            else:
                tag = "WATCH"
            lvl = ("POSSIBLE " if o.confidence == "MEDIUM" else "") + o.level + (f" {o.color}" if o.color else "")
            self.otv.insert("", "end", iid=str(i), tags=(tag,), values=(
                lvl, o.count, (o.foc or "-") + ("" if o.foc_in_mom or not o.foc else " (?)"),
                o.hub or "-", o.area or "-", o.region or "-", f"{o.start:%H:%M}",
                f"{o.fault_time:%H:%M}" if o.fault_time else "-", self.tt_lookup(o) or self._tt_text(o, tt), o.team,
                ", ".join(o.evidence), self._ai_short(o)))
        if sel and self.otv.exists(sel[0]):
            self.otv.selection_set(sel[0])
        if self.outages:
            self.empty_lbl.pack_forget()
        else:
            self.empty_lbl.pack(anchor="w", pady=6)

    @staticmethod
    def _ai_short(o) -> str:
        p = (getattr(o, "ai", None) or {}).get("p")
        if p is None:
            return "learning…" if getattr(o, "ai", None) else ""
        return f"NOT outage {round((1 - p) * 100)}%" if p <= 0.25 else (
            f"REAL {round(p * 100)}%" if p >= 0.75 else f"unsure ({round(p * 100)}%)")

    @staticmethod
    def _tt_text(o, tt: int) -> str:
        due = o.tt_due(tt)
        if not due:
            return "-"
        mins = int((due - clock.now()).total_seconds() // 60)
        return f"{due:%H:%M} (in {mins}m)" if mins >= 0 else f"{due:%H:%M} DUE!"

    def _countdown(self) -> None:
        try:
            if self._next_refresh:
                left = int((self._next_refresh - clock.now()).total_seconds())
                self.next_lbl.config(text=f"LIVE · next refresh in {left}s" if left > 0 else "LIVE · refreshing…")
            self.after(1000, self._countdown)
        except tk.TclError:
            pass

    def refresh_timers(self) -> None:
        tt = self.worker.rules.tt_minutes
        for iid in self.otv.get_children():
            i = int(iid)
            if i < len(self.outages):
                o = self.outages[i]
                self.otv.set(iid, "tt", self.tt_lookup(o) or self._tt_text(o, tt))

    def selected_outage(self):
        sel = self.otv.selection()
        if sel and int(sel[0]) < len(self.outages):
            return self.outages[int(sel[0])]
        return self.outages[0] if len(self.outages) == 1 else None

    def refresh_actions(self) -> None:
        """(Re)build the outage actions menu from the plugins' action list (called when a plugin adds one).
        Primary actions are also a button next to the menu; every action is in 'Actions ▾' and on right-click."""
        for b in self._action_buttons:
            b.destroy()
        self._action_buttons = []
        self.actions_menu.delete(0, "end")
        self.actions_menu.add_command(label="Details / chain", command=lambda: self._act(None))
        self.actions_menu.add_separator()
        items = list(self._actions.items()) if self._actions else []
        for label, cmd, _primary in items:
            self.actions_menu.add_command(label=label, command=lambda c=cmd: self._act(c))
        for label, cmd, _primary in reversed([i for i in items if i[2]]):
            b = ttk.Button(self.action_bar, text=label, style="Small.TButton", command=lambda c=cmd: self._act(c))
            b.pack(side="right", padx=2, before=self.actions_btn)
            self._action_buttons.append(b)

    def _toggle_summary(self) -> None:
        self._summary = not self._summary
        self._apply_summary()

    def _apply_summary(self) -> None:
        """Show/fold the view line, purpose line and KPI cards."""
        for w, opts in ((self.view_lbl, {"anchor": "w", "before": self.plan_lbl}),
                        (self.plan_lbl, {"anchor": "w", "before": self.mom_dot.master}),
                        (self.kpis, {"fill": "x", "pady": (8, 0), "before": self.banner})):
            w.pack(**opts) if self._summary else w.pack_forget()
        self.more_btn.configure(text="▴ Less" if self._summary else "▾ Summary")

    def _outage_menu(self, event) -> None:
        iid = self.otv.identify_row(event.y)
        if iid:
            self.otv.selection_set(iid)
            self.actions_menu.tk_popup(event.x_root, event.y_root)

    def _act(self, command) -> None:
        o = self.selected_outage()
        if o is None:
            return
        if command is None:
            self._on_open(self.worker.sid, o)
        else:
            command(o)

    def _render_events(self, force: bool = False) -> None:
        """MOM-like live grid: ONLY the selected severities; rows are updated in place (no flicker, scroll
        position and selection stay), new / updated rows are marked."""
        q = self.search_v.get().lower().strip()
        sel = set(self.worker.severities)
        now = time.monotonic()
        rows, seen, per_sev, total = [], set(), {}, 0
        for e in self._last_events:
            if e.severity not in sel:
                continue
            iid = "r" + (e.serial or f"{e.site}|{e.down_at}")
            if iid in seen:
                continue
            seen.add(iid)
            total += 1
            per_sev[e.severity] = per_sev.get(e.severity, 0) + 1
            if q and q not in f"{e.site} {e.hub_raw} {e.area} {e.region} {e.node} {e.summary} " \
                               f"{(e.fields or {}).get('TTNumber', '')}".lower():
                continue
            if len(rows) >= MAX_EVENT_ROWS:
                continue
            bg = e.bg or T.SEV.get(e.severity, ("", T.WHITE, T.BLACK))[1]
            fg = e.fg or T.SEV.get(e.severity, ("", T.WHITE, T.BLACK))[2]
            ctag = f"c{bg}{fg}".replace("#", "_")
            if ctag not in self._color_tags:
                self.events.tag_configure(ctag, background=bg, foreground=fg)
                self._color_tags.add(ctag)
            mk = self._marks.get(iid)
            mark = mk[0] if mk and mk[1] >= now else ""
            f = e.fields or {}
            vals = (mark, e.severity_label or SEV_SHORT.get(e.severity, ""), e.site,
                    f"{e.down_at:%d/%m %H:%M:%S}" if e.down_at else "",
                    f"{e.last_at:%d/%m %H:%M:%S}" if e.last_at else "",
                    e.hub_raw, e.area, e.region, e.node, f.get("FaultCode", ""), f.get("TTNumber", ""), e.summary)
            tags = (ctag, "newrow") if mark else (ctag,)
            rows.append((iid, vals, tags))
        try:
            existing = set(self.events.get_children())
            want = [r[0] for r in rows]
            wanted = set(want)
            gone = [i for i in existing if i not in wanted]
            if gone:
                self.events.delete(*gone)
                for i in gone:
                    self._shown.pop(i, None)
            for idx, (iid, vals, tags) in enumerate(rows):
                cur = self._shown.get(iid)
                if iid in existing and iid not in gone:
                    if cur != (vals, tags):
                        self.events.item(iid, values=vals, tags=tags)
                else:
                    self.events.insert("", idx, iid=iid, values=vals, tags=tags)
                self._shown[iid] = (vals, tags)
            if list(self.events.get_children()) != want:
                for idx, iid in enumerate(want):
                    self.events.move(iid, "", idx)
        except tk.TclError:
            return
        parts = " · ".join(f"{SEV_SHORT[v]} {per_sev[v]}" for v in sorted(per_sev, reverse=True))
        shown = len(rows)
        txt = f"{total} rows" + (f"  ({parts})" if parts else "")
        if q:
            txt += f"  ·  {shown} match the search"
        elif shown < total:
            txt += f"  ·  first {shown} shown"
        if not sel:
            txt = "No severity selected"
        self.ev_count.config(text=txt)

    def _popup(self, event) -> None:
        iid = self.events.identify_row(event.y)
        if iid:
            self.events.selection_set(iid)
            self._menu.tk_popup(event.x_root, event.y_root)

    def _copy_sel(self, col) -> None:
        sel = self.events.selection()
        if not sel:
            return
        vals = self.events.item(sel[0], "values")
        text = vals[col] if col is not None else "\t".join(str(v) for v in vals[1:])
        self.clipboard_clear()
        self.clipboard_append(text)

    def _log(self, level: str, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", f"{clock.now():%H:%M:%S}  {level:<6} {text}\n", (level,))
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log.see("end")
        self.log.config(state="disabled")
