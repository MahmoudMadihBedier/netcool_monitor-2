from __future__ import annotations

from core import clock
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from core.config import Settings
from core.sound import SoundPlayer
from . import theme as T


def _center(win: tk.Toplevel, master) -> None:
    win.update_idletasks()
    try:
        x = master.winfo_rootx() + (master.winfo_width() - win.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass


def _header(win, title: str, sub: str = "") -> None:
    bar = tk.Frame(win, bg=T.RED)
    bar.pack(fill="x")
    tk.Label(bar, text=title, bg=T.RED, fg=T.WHITE, font=(T.FONT, 13, "bold")).pack(
        anchor="w", padx=16, pady=(10, 0 if sub else 10))
    if sub:
        tk.Label(bar, text=sub, bg=T.RED, fg=T.RED_TINT, font=(T.FONT, 9)).pack(
            anchor="w", padx=16, pady=(0, 10))


# ---------------------------------------------------------------------------
class FilterPickerDialog(tk.Toplevel):
    """Pick a filter (searchable, 500+ entries) and the view to read it with.
    result = dict(name, filter, view)"""

    def __init__(self, master, filters: list, views: list, settings: Settings,
                 default_name: str, current: Optional[tuple] = None, ask_name: bool = True):
        super().__init__(master)
        self.title("Select filter")
        self.configure(bg=T.WHITE)
        self.transient(master)
        self.resizable(True, True)
        self.geometry("640x600")
        self.result: Optional[dict] = None
        self._filters = filters
        self._views = views
        self._view_ids = [v.id for v in views]
        self._shown: list = []
        self._settings = settings

        _header(self, "Choose what this session monitors",
                "Each session is independent: its own filter, view, refresh and alerts.")
        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        self.name_v = tk.StringVar(value=default_name)
        if ask_name:
            ttk.Label(body, text="Session name", style="Card.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Entry(body, textvariable=self.name_v).grid(row=1, column=0, sticky="ew", pady=(2, 10))

        ttk.Label(body, text=f"Filter  ({len(filters)} available - type to search)",
                  style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.search_v = tk.StringVar()
        ent = ttk.Entry(body, textvariable=self.search_v)
        ent.grid(row=3, column=0, sticky="ew", pady=(2, 4))
        self.search_v.trace_add("write", lambda *_: self._refill())

        lf = ttk.Frame(body, style="Card.TFrame")
        lf.grid(row=4, column=0, sticky="nsew")
        body.rowconfigure(4, weight=1)
        self.lb = tk.Listbox(lf, activestyle="none", font=(T.FONT, 10), bd=1, relief="solid",
                             highlightthickness=0, selectbackground=T.RED, selectforeground=T.WHITE)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", self._on_pick)
        self.lb.bind("<Double-1>", lambda e: self._ok())

        ttk.Label(body, text="View (decides which columns come back - needs HUB and SubRegion)",
                  style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.view_v = tk.StringVar()
        self.view_cb = ttk.Combobox(body, textvariable=self.view_v, values=self._view_ids)
        self.view_cb.grid(row=6, column=0, sticky="ew", pady=(2, 4))
        self._view_touched = False
        self.view_cb.bind("<<ComboboxSelected>>", lambda e: setattr(self, "_view_touched", True))
        self.view_cb.bind("<Key>", lambda e: setattr(self, "_view_touched", True))
        self.picked_lbl = ttk.Label(body, text="", style="CardMuted.TLabel", wraplength=580)
        self.picked_lbl.grid(row=7, column=0, sticky="w")

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=8, column=0, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Start monitoring", style="Primary.TButton",
                   command=self._ok).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self._refill()
        if current:
            self.search_v.set(current[0].split(":")[0])
            self._select_id(current[0])
            self.view_v.set(current[1])
            self._view_touched = True
        elif settings.monitor.default_view in self._view_ids:
            self.view_v.set(settings.monitor.default_view)
        _center(self, master)
        ent.focus_set()
        self.grab_set()
        self.wait_window()

    def _refill(self) -> None:
        words = [w for w in re.split(r"\s+", self.search_v.get().lower().strip()) if w]
        self._shown = [f for f in self._filters
                       if all(w in (f.label + " " + f.id).lower() for w in words)]
        self.lb.delete(0, "end")
        for f in self._shown[:1500]:
            self.lb.insert("end", f.label)

    def _select_id(self, fid: str) -> None:
        for i, f in enumerate(self._shown):
            if f.id == fid:
                self.lb.selection_clear(0, "end")
                self.lb.selection_set(i)
                self.lb.see(i)
                self._on_pick()
                return

    def _selected(self):
        sel = self.lb.curselection()
        return self._shown[sel[0]] if sel else None

    def _on_pick(self, _=None) -> None:
        f = self._selected()
        if not f:
            return
        if not self._view_touched:
            dv = self._settings.monitor.default_view
            self.view_v.set(dv if dv in self._view_ids else (f.default_view or dv))
        self.picked_lbl.config(text=f"Selected: {f.id}   (filter's own view: {f.default_view or '-'})")

    def _ok(self) -> None:
        f = self._selected()
        if not f:
            messagebox.showwarning("Filter", "Select a filter from the list.", parent=self)
            return
        view = self.view_v.get().strip()
        if not view:
            messagebox.showwarning("View", "Select a view.", parent=self)
            return
        self.result = dict(name=self.name_v.get().strip() or f.name, filter=f, view=view)
        self.destroy()


# ---------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    """Form for rules and monitoring options. Applies to running sessions too."""

    FIELDS = [
        ("Alert rule", [
            ("rules.min_sites", "PRE-OUTAGE alert at N physical sites (FO manual: 4)", int),
            ("rules.outage_sites", "OUTAGE at N physical sites (FO manual: 5)", int),
            ("rules.window_minutes", "'Same time' window for HUB / Node groups (minutes)", int),
            ("rules.chain_window_minutes", "'Same time' window for the same chain (minutes)", int),
            ("rules.use_chain", "Use Cascaded Sites Report chains (first of chain)", bool),
            ("rules.use_hub", "Use same HUB + area", bool),
            ("rules.use_node", "Use same Node (RNC/BSC) + area as 'possible outage'", bool),
            ("rules.tt_minutes", "Open Global TT at minute N after the 5th site", int),
            ("rules.clear_after_polls", "Clear an outage only after N refreshes without it", int),
            ("rules.yellow_max", "Yellow below N physical sites", int),
            ("rules.orange_max", "Orange below N physical sites (else Red)", int),
            ("rules.hub_match", "HUB compare: code = first word (H4179) / full", ["code", "full"]),
            ("rules.area_field", "Area column (TECHostname = SubRegion, NetworkAssureHost = Region, empty = ignore)",
             ["TECHostname", "NetworkAssureHost", ""]),
            ("rules.time_field", "Down-time column", ["FirstOccurrence", "EventTime", "LastOccurrence"]),
            ("rules.realert_minutes", "Repeat alert while still down every (min, 0 = never)", int),
            ("rules.ignore_cleared", "Ignore rows that have a ClearTime", bool),
            ("rules.include_summary_regex", "Only rows whose Summary matches (regex, optional)", str),
            ("rules.exclude_summary_regex", "Ignore rows whose Summary matches (regex, optional)", str),
        ]),
        ("Monitoring", [
            ("monitor.poll_seconds", "Default live refresh for NEW sessions (seconds, 10-300; each session "
                                     "can change its own)", int),
            ("monitor.blind_after_failures", "BLIND alert after N failed reads", int),
            ("monitor.default_view", "Default view for new sessions", str),
            ("monitor.page_size", "Rows per request", int),
            ("monitor.shift_hours", "Shift length (hours)", int),
            ("monitor.prevent_sleep", "Keep PC awake while monitoring", bool),
        ]),
        ("Outage mail (Automated_Outage)", [
            ("mail.to", "To", str),
            ("mail.cc", "CC (separate with ;)", str),
            ("mail.subject_template", "Subject  ({date} {region} {area} {count} {foc} {hub})", str),
            ("mail.include_foc_hint", "Add expected first of chain line", bool),
            ("mail.signature", "Signature name (empty = Windows user)", str),
            ("cascade.max_age_days", "Warn when Cascaded Sites Report is older than (days)", int),
        ]),
        ("Server", [
            ("server.base_url", "Server URL", str),
            ("server.timezone", "Time zone (MOM times and the tool's clock)", ["Asia/Riyadh", "Africa/Johannesburg"]),
            ("server.transport", "Connection mode (browser = the tool's own hidden browser, direct = Python) - "
                                 "sign in again to apply", ["browser", "direct"]),
            ("server.browser", "Browser engine (auto = tool's own Chromium, never your Edge; edge = Edge first) - "
                               "sign in again to apply", ["auto", "edge"]),
            ("server.datasource", "Datasource", str),
            ("server.request_timeout_s", "Request timeout (s)", int),
            ("server.verify_tls", "Verify TLS certificate", bool),
        ]),
        ("Columns (advanced)", [
            ("rules.site_field", "Site column id", str),
            ("rules.hub_field", "HUB column id", str),
            ("rules.region_field", "Region column id", str),
        ]),
    ]

    FIXED_CHOICES = {"server.timezone", "server.transport", "server.browser"}

    def __init__(self, master, settings: Settings):
        super().__init__(master)
        self.title("Settings & rules")
        self.configure(bg=T.WHITE)
        self.transient(master)
        self.geometry("760x680")
        self.result: Optional[Settings] = None
        self._s = Settings.from_dict(settings.to_dict())
        self._vars: dict = {}

        _header(self, "Settings & alert rules", "Changes apply immediately to all running sessions.")
        outer = ttk.Frame(self, style="Card.TFrame")
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=T.WHITE, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, style="Card.TFrame", padding=16)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner.columnconfigure(1, weight=1)

        r = 0
        for section, items in self.FIELDS:
            ttk.Label(inner, text=section, style="CardH2.TLabel", foreground=T.RED).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=(12 if r else 0, 6))
            r += 1
            for path, label, kind in items:
                val = self._get(path)
                ttk.Label(inner, text=label, style="Card.TLabel", wraplength=380).grid(
                    row=r, column=0, sticky="w", pady=3, padx=(0, 12))
                if kind is bool:
                    v = tk.BooleanVar(value=bool(val))
                    ttk.Checkbutton(inner, variable=v, style="Card.TCheckbutton").grid(row=r, column=1, sticky="w")
                elif isinstance(kind, list):
                    v = tk.StringVar(value=str(val))
                    ttk.Combobox(inner, textvariable=v, values=kind,
                                 state="readonly" if path in self.FIXED_CHOICES else "normal").grid(
                        row=r, column=1, sticky="ew")
                else:
                    v = tk.StringVar(value=str(val))
                    ttk.Entry(inner, textvariable=v).grid(row=r, column=1, sticky="ew")
                self._vars[path] = (v, kind)
                r += 1

        bar = ttk.Frame(self, style="Card.TFrame", padding=(16, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Save", style="Primary.TButton", command=self._save).pack(side="right", padx=8)
        _center(self, master)
        self.grab_set()
        self.wait_window()

    def _get(self, path: str):
        sec, key = path.split(".")
        return getattr(getattr(self._s, sec), key)

    def _save(self) -> None:
        try:
            for path, (v, kind) in self._vars.items():
                sec, key = path.split(".")
                raw = v.get()
                if kind is int:
                    val = int(str(raw).strip())
                    if val < 0:
                        raise ValueError(f"{key} must be >= 0")
                elif kind is bool:
                    val = bool(raw)
                else:
                    val = str(raw).strip()
                if key.endswith("_regex") and val:
                    re.compile(val)
                setattr(getattr(self._s, sec), key, val)
            if self._s.server.timezone not in ("Asia/Riyadh", "Africa/Johannesburg"):
                raise ValueError("Time zone must be Asia/Riyadh or Africa/Johannesburg")
            if self._s.rules.min_sites < 2:
                raise ValueError("Minimum sites must be at least 2")
            self._s.monitor.poll_seconds = max(10, min(300, self._s.monitor.poll_seconds))
            self._s.monitor.page_size = max(20, min(1000, self._s.monitor.page_size or 150))
            self._s.monitor.blind_after_failures = max(1, self._s.monitor.blind_after_failures)
            if self._s.rules.outage_sites < self._s.rules.min_sites:
                raise ValueError("OUTAGE sites must be >= PRE-OUTAGE sites")
        except Exception as exc:
            messagebox.showerror("Invalid value", str(exc), parent=self)
            return
        self.result = self._s
        self.destroy()


# ---------------------------------------------------------------------------
class AlertCenter:
    """Always-on-top popups; alarm keeps sounding until every popup is acknowledged.
    One popup per outage: the same outage seen by several sessions updates one window."""

    def __init__(self, root: tk.Tk, sound: SoundPlayer, on_change=None, on_ack=None):
        self._root, self._sound = root, sound
        self._on_ack = on_ack or (lambda key: None)   # the engineer acknowledged THIS popup (AI learns 'real')
        self._open: dict = {}  # key -> dict(win, tl, bl, ts, sessions)
        self._on_change = on_change or (lambda n: None)

    @property
    def open_count(self) -> int:
        return len(self._open)

    def show(self, key, title: str, body: str, session: str = "", blind: bool = False,
             color: str = "", buttons: list | None = None, question: str = "") -> None:
        stamp = f"{clock.now():%H:%M:%S}"
        head_bg = color or (T.BLACK if blind else T.RED)
        head_fg = T.BLACK if head_bg in (T.LEMON, T.FRESH_ORANGE, T.AQUA) else T.WHITE
        if key in self._open:
            it = self._open[key]
            if session and session not in it["sessions"]:
                it["sessions"].append(session)
            it["tl"].config(text=title)
            it["bl"].config(text=body)
            it["ts"].config(text=f"{' · '.join(it['sessions'])}   ·   updated {stamp}")
            for w in (it["head"], it["tl"], it["ts"], it["icon"], it["hbox"]):
                w.config(bg=head_bg)
            it["tl"].config(fg=head_fg)
            self._raise(it["win"])
            self._sound.start()
            return

        win = tk.Toplevel(self._root)
        win.title("ALERT - " + (session or "Netcool"))
        win.configure(bg=T.WHITE)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        head = tk.Frame(win, bg=head_bg)
        head.pack(fill="x")
        icon = tk.Label(head, text="⚠", bg=head_bg, fg=head_fg, font=(T.FONT, 26, "bold"))
        icon.pack(side="left", padx=(18, 6), pady=10)
        hbox = tk.Frame(head, bg=head_bg)
        hbox.pack(side="left", fill="x", expand=True, pady=10, padx=(0, 18))
        tl = tk.Label(hbox, text=title, bg=head_bg, fg=head_fg, font=(T.FONT, 14, "bold"),
                      wraplength=640, justify="left")
        tl.pack(anchor="w")
        ts = tk.Label(hbox, text=f"{session}   ·   {stamp}", bg=head_bg, fg=head_fg, font=(T.FONT, 9))
        ts.pack(anchor="w")
        if question:
            tk.Label(win, text=question, bg=T.GREY_1, fg=T.BLACK, font=(T.FONT, 11, "bold"),
                     anchor="w", padx=20, pady=8).pack(fill="x")
        bl = tk.Label(win, text=body, bg=T.WHITE, fg=T.BLACK, font=(T.MONO, 10), justify="left",
                      anchor="w")
        bl.pack(fill="x", padx=20, pady=14)
        bar = tk.Frame(win, bg=T.WHITE)
        bar.pack(fill="x", padx=20, pady=(0, 16))
        tk.Button(bar, text="ACKNOWLEDGE", bg=T.RED, fg=T.WHITE, activebackground=T.RED_DARK,
                  activeforeground=T.WHITE, relief="flat", font=(T.FONT, 11, "bold"), padx=18, pady=6,
                  cursor="hand2", command=lambda: self.ack(key, by_user=True)).pack(side="right")
        tk.Button(bar, text="Acknowledge all", bg=T.WHITE, fg=T.BLACK, relief="solid", bd=1,
                  font=(T.FONT, 9), padx=10, pady=5, cursor="hand2", command=self.ack_all).pack(side="right", padx=8)
        for text, cb, primary in (buttons or []):
            def run(c=cb):
                try:
                    c()
                finally:
                    pass
            tk.Button(bar, text=text, bg=T.BLACK if primary else T.GREY_1, fg=T.WHITE if primary else T.BLACK,
                      activebackground=T.RED, relief="flat", font=(T.FONT, 9, "bold"), padx=10, pady=5,
                      cursor="hand2", command=run).pack(side="left", padx=(0, 6))
        win.protocol("WM_DELETE_WINDOW", lambda: self.ack(key))
        self._open[key] = dict(win=win, tl=tl, bl=bl, ts=ts, head=head, icon=icon, hbox=hbox,
                               sessions=[session] if session else [])
        n = len(self._open) - 1
        win.update_idletasks()
        try:
            sw = win.winfo_screenwidth()
            win.geometry(f"+{max(0, (sw - win.winfo_width()) // 2 + 24 * (n % 6))}+{60 + 24 * (n % 6)}")
        except Exception:
            pass
        self._raise(win)
        self._sound.start()
        self._on_change(len(self._open))

    @staticmethod
    def _raise(win) -> None:
        try:
            win.deiconify()
            win.lift()
            win.attributes("-topmost", True)
            win.focus_force()
            win.bell()
        except Exception:
            pass

    def ack(self, key, by_user: bool = False) -> None:
        item = self._open.pop(key, None)
        if item and by_user:
            try:
                self._on_ack(key)
            except Exception:
                pass
        if item:
            try:
                item["win"].destroy()
            except Exception:
                pass
        if not self._open:
            self._sound.stop()
        self._on_change(len(self._open))

    def ack_all(self) -> None:
        for k in list(self._open):
            self.ack(k)

    def drop_session(self, sid: int) -> None:
        for k in [k for k in self._open if isinstance(k, tuple) and k and k[0] == sid]:
            self.ack(k)
