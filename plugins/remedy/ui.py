"""Global TT screens: FO checklist + pre-filled TT, Remedy login, Remedy setup (templates, test), records."""
from __future__ import annotations

import copy
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import clock
from core.config import REMEDY_TEAM_OPTIONAL, REMEDY_TEAM_REQUIRED, RemedySettings
from ui import theme as T
from ui.modal import _label, _Modal
from .remedy import IMPACT, URGENCY, build_envelope, mask

TEAMS = ["TX", "NFM", "PA"]
EDIT_FIELDS = [("Short_Description", "Short description"), ("Asset_ID", "Asset ID (first of chain)"),
               ("Actual_Fault_Time", "Actual fault time"), ("Sites", "Sites"), ("Notes", "Notes"),
               ("AffectedTRUs", "Affected TRUs"), ("Urgency", "Urgency"), ("Impact", "Impact")]


def fo_team(o) -> str:
    """FO manual suggestion -> one of TX / NFM / PA."""
    return "PA" if o.team.startswith("PA") else ("NFM" if o.team == "NFM" else "TX")


def fo_checklist(o, tt_minutes: int, mom_tts: list, activities: list = ()) -> list:
    """FO-manual checks before a Global TT: [(text, auto_ticked)]."""
    now = clock.now()
    due = o.tt_due(tt_minutes)
    items = [(f"15th minute reached (Global TT due {due:%H:%M}) - or PA activity confirmed: open at once"
              if due else "Fault time known (5th site down)", bool(due and now >= due)),
             ("Checked PA / planned activity for these sites"
              + (f" - Outlook found {len(activities)} mail(s), see below" if activities else ""), False),
             (f"TX confirmed the first of chain ({o.foc or 'ask TX'}) and the 5th site", False)]
    if 2 <= now.hour < 6:
        items.append(("02:00-06:00: NO PA / 2nd-line activity on these sites (with activity: don't open the outage)",
                      False))
    elif 6 <= now.hour < 7:
        items.append(("After 06:00 with PA activity: Global TT at 07:00 (fault time 06:00, or 07:00 if PA extends)",
                      False))
    items.append(("No TT already open for these sites" + (f" - MOM shows: {', '.join(mom_tts[:4])}" if mom_tts else
                                                          " (MOM TT column is empty)"), not mom_tts))
    return items


# ------------------------------------------------------------------ Global TT window
class GlobalTTDialog(_Modal):
    """Review and send the pre-filled Global TT. result = (team, values) when 'Open TT' is pressed."""

    def __init__(self, master, o, team: str, ai_hint: str, build, can_send, checks: list, existing_tt: str,
                 activities: list = ()):
        super().__init__(master, "Global TT", o.title)
        self.o, self.build, self.can_send = o, build, can_send
        b = self.body
        if activities:
            box = tk.Frame(b, bg=T.LEMON)
            box.pack(fill="x", pady=(0, 6))
            tk.Label(box, text="📧 Outlook: possible planned / night activity on these sites", bg=T.LEMON,
                     fg=T.BLACK, font=(T.FONT, 9, "bold"), anchor="w", padx=8, pady=3).pack(fill="x")
            for line in list(activities)[:5]:
                tk.Label(box, text="• " + line, bg=T.LEMON, fg=T.BLACK, font=(T.FONT, 9), anchor="w",
                         padx=14, wraplength=820, justify="left").pack(fill="x")
        if existing_tt:
            tk.Label(b, text=f"⚠ A Global TT was already opened for this outage: {existing_tt}", bg=T.LEMON,
                     fg=T.BLACK, font=(T.FONT, 10, "bold"), anchor="w", padx=8, pady=4).pack(fill="x", pady=(0, 6))
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(fill="x")
        _label(row, "Team:", bold=True).pack(side="left")
        self.team_v = tk.StringVar(value=team)
        cb = ttk.Combobox(row, textvariable=self.team_v, values=TEAMS, state="readonly", width=6)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._refill())
        _label(row, f"FO manual: {o.team} - {o.team_reason}" + (f"   ·   🧠 {ai_hint}" if ai_hint else ""),
               muted=True, wrap=470).pack(side="left", padx=8)
        _label(b, "FO manual checks (all needed before Open TT):", bold=True).pack(anchor="w", pady=(10, 2))
        self.checks = []
        for text, ticked in checks:
            v = tk.BooleanVar(value=ticked)
            ttk.Checkbutton(b, text=text, variable=v, style="Card.TCheckbutton", command=self._update).pack(anchor="w")
            self.checks.append(v)
        grid = ttk.Frame(b, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(1, weight=1)
        self.vars = {}
        for i, (k, label) in enumerate(EDIT_FIELDS):
            _label(grid, label).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 8))
            v = tk.StringVar()
            if k in ("Urgency", "Impact"):
                w = ttk.Combobox(grid, textvariable=v, values=URGENCY if k == "Urgency" else IMPACT, width=30)
            else:
                w = ttk.Entry(grid, textvariable=v, width=70)
            w.grid(row=i, column=1, sticky="ew", pady=2)
            self.vars[k] = v
        _label(b, "Detailed description:").pack(anchor="w", pady=(6, 2))
        self.detail = tk.Text(b, height=6, width=90, font=(T.MONO, 9), relief="solid", bd=1, wrap="word")
        self.detail.pack(fill="x")
        self.lock = tk.Label(b, text="", bg=T.WHITE, fg=T.RED_DARK, font=(T.FONT, 9, "bold"), anchor="w")
        self.lock.pack(fill="x", pady=(6, 0))
        bar = ttk.Frame(self, style="Card.TFrame", padding=(16, 0, 16, 14))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        self.send_btn = ttk.Button(bar, text="🎫 Open TT in Remedy", style="Primary.TButton", command=self._ok)
        self.send_btn.pack(side="right", padx=8)
        ttk.Button(bar, text="Preview request", command=self._preview).pack(side="left")
        self._refill()

    def _refill(self) -> None:
        self.values = self.build(self.team_v.get())
        for k, v in self.vars.items():
            v.set(self.values.get(k, ""))
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", self.values.get("Detailed_Decription", ""))
        self._update()

    def _collect(self) -> dict:
        vals = dict(self.values)
        vals.update({k: v.get().strip() for k, v in self.vars.items()})
        vals["Detailed_Decription"] = self.detail.get("1.0", "end").strip()
        vals["Description"] = vals.get("Notes", "")
        return vals

    def _update(self) -> None:
        ok, why = self.can_send(self.team_v.get())
        if ok and not all(v.get() for v in self.checks):
            ok, why = False, "Tick every FO check first"
        self.lock.config(text="" if ok else f"🔒 {why}")
        self.send_btn.state(["!disabled"] if ok else ["disabled"])

    def _preview(self) -> None:
        win = tk.Toplevel(self)
        win.title("Remedy request (password hidden)")
        win.attributes("-topmost", True)
        txt = tk.Text(win, width=110, height=34, font=(T.MONO, 9))
        txt.insert("1.0", mask(build_envelope(self._collect(), self.values.get("Submitter", ""), "******")))
        txt.pack(fill="both", expand=True)

    def _ok(self) -> None:
        if not messagebox.askyesno("Open TT", f"Open the Global TT on team {self.team_v.get()} in Remedy now?",
                                   parent=self):
            return
        self.result = (self.team_v.get(), self._collect())
        self.destroy()


# ------------------------------------------------------------------ Remedy login
class RemedyLoginDialog(_Modal):
    def __init__(self, master, user: str = "", message: str = ""):
        super().__init__(master, "Remedy sign-in", "Your own Remedy account - used only to open Global TTs")
        b = self.body
        if message:
            _label(b, message).pack(anchor="w", pady=(0, 6))
        self.user_v, self.pw_v, self.remember_v = tk.StringVar(value=user), tk.StringVar(), tk.BooleanVar(value=True)
        for label, var, show in (("Remedy user", self.user_v, ""), ("Password", self.pw_v, "•")):
            _label(b, label).pack(anchor="w")
            ttk.Entry(b, textvariable=var, show=show, width=40).pack(anchor="w", pady=(0, 6))
        ttk.Checkbutton(b, text="Remember the password (Windows Credential Manager)", variable=self.remember_v,
                        style="Card.TCheckbutton").pack(anchor="w")
        self.buttons("Sign in")

    def _ok(self) -> None:
        if not self.user_v.get().strip() or not self.pw_v.get():
            messagebox.showinfo("Remedy", "User and password are required.", parent=self)
            return
        self.result = (self.user_v.get().strip(), self.pw_v.get(), self.remember_v.get())
        self.destroy()


# ------------------------------------------------------------------ Remedy setup
class RemedySetupDialog(_Modal):
    """WSDL address + Test connection, team templates (TX / NFM / PA), common fields. result = RemedySettings."""

    def __init__(self, master, rs: RemedySettings, make_client):
        super().__init__(master, "Remedy / Global TT setup", "Fill each team once from a real Global TT")
        self.rs = copy.deepcopy(rs)
        self.make_client = make_client
        nb = ttk.Notebook(self.body)
        nb.pack(fill="both", expand=True)
        f = ttk.Frame(nb, style="Card.TFrame", padding=10)
        nb.add(f, text=" Connection ")
        _label(f, "Remedy web service address (WSDL):", bold=True).pack(anchor="w")
        self.wsdl_v = tk.StringVar(value=self.rs.wsdl_url)
        ttk.Entry(f, textvariable=self.wsdl_v, width=100).pack(anchor="w", fill="x")
        ttk.Button(f, text="Test connection (reads the WSDL)", command=self._test).pack(anchor="w", pady=8)
        self.status = _label(f, self._status_text(), wrap=640)
        self.status.pack(anchor="w")
        self.vars = {}
        enums = {x["name"]: x.get("enum") or [] for x in self.rs.accepted_fields if isinstance(x, dict)}
        for team in TEAMS:
            tf = ttk.Frame(nb, style="Card.TFrame", padding=10)
            nb.add(tf, text=f" Team {team} ")
            self._form(tf, team, REMEDY_TEAM_REQUIRED + REMEDY_TEAM_OPTIONAL, self.rs.teams.setdefault(team, {}), enums)
        cf = ttk.Frame(nb, style="Card.TFrame", padding=10)
        nb.add(cf, text=" Common fields ")
        self._form(cf, "common", list(self.rs.common), self.rs.common, enums)
        self.buttons("Save")

    def _form(self, parent, group, keys, data, enums) -> None:
        parent.columnconfigure(1, weight=1)
        for i, k in enumerate(keys):
            req = group != "common" and k in REMEDY_TEAM_REQUIRED
            _label(parent, k + (" *" if req else "")).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 10))
            v = tk.StringVar(value=str(data.get(k, "")))
            choices = enums.get(k) or (URGENCY if k == "Urgency" else IMPACT if k == "Impact" else None)
            w = ttk.Combobox(parent, textvariable=v, values=choices, width=50) if choices else \
                ttk.Entry(parent, textvariable=v, width=52)
            w.grid(row=i, column=1, sticky="ew", pady=2)
            self.vars[(group, k)] = v

    def _status_text(self) -> str:
        if not self.rs.verified:
            return "Not tested yet - 'Open TT' stays locked until a test succeeds."
        warn = "\n   ⚠ The TT address is plain http (not encrypted) - ask IT for the https address." \
            if self.rs.endpoint.startswith("http://") and self.rs.wsdl_url.startswith("https://") else ""
        return (f"✔ Connected: {self.rs.endpoint}\n   {len(self.rs.accepted_fields)} TT_Create fields; "
                f"soapAction {self.rs.soap_action}{warn}")

    def _test(self) -> None:
        self.rs.wsdl_url = self.wsdl_v.get().strip()
        self.status.config(text="Testing… (needs the VPN)")
        box = {}

        def work():
            try:
                box["info"] = self.make_client(self.rs).test_connection()
            except Exception as exc:
                box["err"] = exc

        th = threading.Thread(target=work, daemon=True)
        th.start()

        def poll():
            if th.is_alive():
                self.after(300, poll)
                return
            if "err" in box:
                self.rs.verified = False
                self.status.config(text=f"✖ Test failed: {box['err']}")
            else:
                self.status.config(text=self._status_text())
        poll()

    def _ok(self) -> None:
        self.rs.wsdl_url = self.wsdl_v.get().strip()
        for (group, k), v in self.vars.items():
            (self.rs.common if group == "common" else self.rs.teams[group])[k] = v.get().strip()[:300]
        self.result = self.rs
        self.destroy()


# ------------------------------------------------------------------ records
class RecordsDialog(_Modal):
    def __init__(self, master, records):
        super().__init__(master, "Records", "Everything the tool saw and decided (its own database)", width=900)
        self.records = records
        self.nb = ttk.Notebook(self.body)
        self.nb.pack(fill="both", expand=True)
        self.tabs = {}
        for table, title in (("outages", "Outages"), ("decisions", "Decisions"), ("tts", "Global TTs"),
                             ("alarms", "Alarms")):
            f = ttk.Frame(self.nb, style="Card.TFrame", padding=6)
            self.nb.add(f, text=f" {title} ")
            top = ttk.Frame(f, style="Card.TFrame")
            top.pack(fill="x")
            q = tk.StringVar()
            ttk.Entry(top, textvariable=q, width=30).pack(side="left")
            ttk.Button(top, text="Search", command=lambda t=table, v=q: self._fill(t, v.get())).pack(side="left", padx=4)
            ttk.Button(top, text="Export CSV…", command=lambda t=table: self._export(t)).pack(side="right")
            tv = ttk.Treeview(f, show="headings", height=16)
            sb = ttk.Scrollbar(f, orient="horizontal", command=tv.xview)
            tv.configure(xscrollcommand=sb.set)
            tv.pack(fill="both", expand=True, pady=4)
            sb.pack(fill="x")
            self.tabs[table] = tv
            self._fill(table, "")
        bar = ttk.Frame(self, style="Card.TFrame", padding=(16, 0, 16, 14))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")

    def _fill(self, table: str, search: str) -> None:
        tv = self.tabs[table]
        all_cols, rows = self.records.query(table, search.strip(), 500)
        cols = [c for c in all_cols if c not in ("request", "response")]
        tv.configure(columns=cols)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=110 if c not in ("title", "summary", "sites", "note", "ai") else 220, anchor="w")
        tv.delete(*tv.get_children())
        for r in rows:
            d = dict(zip(all_cols, r))
            tv.insert("", "end", values=[d.get(c, "") for c in cols])

    def _export(self, table: str) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=f"{table}.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            n = self.records.export_csv(table, path)
            messagebox.showinfo("Export", f"{n} rows saved.", parent=self)
