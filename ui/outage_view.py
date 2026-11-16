"""Outage details: chain tree (FOC -> sites), guidance from the FO manual, mail / TT actions."""
from __future__ import annotations

from core import clock
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import theme as T


class OutageActions:
    """The buttons shown for an outage: `items()` -> [(label, command(o), primary)], filled from the plugins'
    kernel.OutageAction list by the app (so a new plugin's button appears everywhere automatically)."""

    def __init__(self, items):
        self.items = items              # callable returning [(label, command, primary)]


def level_color(o) -> str:
    if o.confidence == "MEDIUM":
        return T.AQUA
    if o.level == "OUTAGE":
        return T.OUTAGE_COLOR.get(o.color, T.RED)
    return T.LEVEL_COLORS.get(o.level, T.GREY_6)


class OutageDialog(tk.Toplevel):
    def __init__(self, master, o, sid: int, session_name: str, actions: OutageActions, tt_minutes: int = 15):
        self.actions = actions
        super().__init__(master)
        self.title(f"Outage details · {session_name}")
        self.configure(bg=T.WHITE)
        self.geometry("1000x700")
        self.minsize(820, 560)
        self.transient(master)
        col = level_color(o)
        fg = T.BLACK if col in (T.LEMON, T.FRESH_ORANGE, T.AQUA) else T.WHITE
        head = tk.Frame(self, bg=col)
        head.pack(fill="x")
        lvl = ("POSSIBLE " if o.confidence == "MEDIUM" else "") + o.level + (f" · {o.color}" if o.color else "")
        tk.Label(head, text=lvl, bg=col, fg=fg, font=(T.FONT, 11, "bold")).pack(anchor="w", padx=18, pady=(10, 0))
        tk.Label(head, text=o.title, bg=col, fg=fg, font=(T.FONT, 16, "bold")).pack(anchor="w", padx=18)
        tk.Label(head, text=f"Evidence: {', '.join(o.evidence)}   ·   confidence {o.confidence}   ·   "
                            f"session {session_name}", bg=col, fg=fg, font=(T.FONT, 9)).pack(
            anchor="w", padx=18, pady=(0, 10))

        bar = tk.Frame(self, bg=T.WHITE)
        bar.pack(side="bottom", fill="x", padx=14, pady=12)
        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True)
        facts = ttk.Frame(body, style="Card.TFrame")
        facts.pack(fill="x")
        due = o.tt_due(tt_minutes)
        rows = [("First of chain", (o.foc or "-") + ("" if not o.foc else
                                                    ("  (down in MOM)" if o.foc_in_mom else "  (NOT in MOM down list)"))),
                ("HUB / Node", f"{o.hub or '-'}  /  {o.node or '-'}"),
                ("Area / Region", f"{o.area or '-'}  /  {o.region or '-'}"),
                ("Physical sites", f"{o.count}   ({', '.join(f'{v} {k}' for k, v in sorted(o.tech_counts.items()))})"),
                ("First down", f"{o.start:%d/%m %H:%M:%S}"),
                ("Actual fault time", f"{o.fault_time:%H:%M:%S}  (5th site)" if o.fault_time else "-  (under 5 sites)"),
                ("Open Global TT", f"{due:%H:%M}" + (self._left(due)) if due else "-"),
                ("Suggested team", f"{o.team}  -  {o.team_reason}")]
        for i, (k, v) in enumerate(rows):
            ttk.Label(facts, text=k, style="CardMuted.TLabel").grid(row=i // 2, column=(i % 2) * 2, sticky="w",
                                                                    padx=(0, 10), pady=2)
            ttk.Label(facts, text=v, style="Card.TLabel", font=(T.FONT, 10, "bold"), wraplength=330).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 30), pady=2)

        if o.notes or o.issues:
            box = tk.Frame(body, bg=T.GREY_1)
            box.pack(fill="x", pady=(12, 0))
            for n in o.notes:
                tk.Label(box, text="•  " + n, bg=T.GREY_1, fg=T.BLACK, anchor="w", justify="left",
                         wraplength=900, font=(T.FONT, 10)).pack(fill="x", padx=10, pady=2)
            for n in o.issues:
                tk.Label(box, text="⚠  " + n, bg=T.GREY_1, fg=T.RED_DARK, anchor="w", justify="left",
                         wraplength=900, font=(T.FONT, 10, "bold")).pack(fill="x", padx=10, pady=2)

        ttk.Label(body, text="Chain  (first of chain on top; grey = behind the FOC but not reported down)",
                  style="CardH2.TLabel").pack(anchor="w", pady=(12, 4))
        cols = ("time", "tech", "hub", "node", "fault")
        tv = ttk.Treeview(body, columns=cols, show="tree headings", height=8)
        tv.heading("#0", text="Site")
        tv.column("#0", width=170)
        for c, t, w in (("time", "Down at", 110), ("tech", "Tech", 80), ("hub", "HUB", 150),
                        ("node", "Node", 80), ("fault", "Fault code / summary", 360)):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor="w", stretch=c == "fault")
        tv.tag_configure("foc", background=T.RED, foreground=T.WHITE)
        tv.tag_configure("down", background=T.RED_TINT)
        tv.tag_configure("unseen", foreground=T.GREY_6)
        sb = ttk.Scrollbar(body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        sites = {s.pid: s for s in o.sites}
        root_id = ""
        if o.foc:
            s = sites.get(o.foc)
            root_id = tv.insert("", "end", text=f"★ {o.foc}", open=True, tags=("foc",),
                                values=self._vals(s) if s else ("not in MOM", "", "", "", "first of chain"))
        for s in o.sites:
            if s.pid == o.foc:
                continue
            tv.insert(root_id, "end", text=s.pid, values=self._vals(s), tags=("down",))
        for u in o.chain_unseen[:15]:
            tv.insert(root_id, "end", text=f"? {u}", values=("not reported", "", "", "", "check manually"),
                      tags=("unseen",))

        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        for label, cmd, primary in reversed(list(actions.items())):
            ttk.Button(bar, text=label, style="Primary.TButton" if primary else "TButton",
                       command=lambda c=cmd: (c(o), self.destroy() if "Not an outage" in label else None)
                       ).pack(side="right", padx=6)

    @staticmethod
    def _vals(s) -> tuple:
        return (f"{s.first_down:%d/%m %H:%M:%S}", "/".join(sorted(s.techs)), s.hub_raw or "-", s.node or "-",
                (s.fault_code + " · " if s.fault_code else "") + s.summary[:80])

    @staticmethod
    def _left(due: datetime) -> str:
        mins = int((due - clock.now()).total_seconds() // 60)
        return f"   (in {mins} min)" if mins >= 0 else f"   (DUE {-mins} min ago)"
