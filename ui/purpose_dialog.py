"""'What will this session monitor?' - purpose + alarm rules of a session (asked for non-2G/3G filters)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from core.session_plan import PURPOSE_DEFAULTS, PURPOSES, SessionPlan
from . import theme as T
from .modal import _label, _Modal


class PurposeDialog(_Modal):
    """'What will this session monitor and what must raise an alarm?' -> SessionPlan (or None = cancel)."""

    def __init__(self, master, filter_id: str, guess: tuple = ("", 0.0, ""), plan: Optional[SessionPlan] = None):
        super().__init__(master, "What will this session monitor?", filter_id.split(":")[0])
        plan = plan or SessionPlan(purpose=guess[0] or PURPOSES[-1], fo_rules=False, alarm_new_rows=True)
        b = self.body
        if guess[0]:
            tk.Label(b, text=f"🧠 AI suggestion: {guess[0]}  ({round(guess[1] * 100)}% - {guess[2]})",
                     bg=T.RED_TINT, fg=T.BLACK, font=(T.FONT, 9, "bold"), anchor="w", padx=8, pady=4).pack(fill="x")
        _label(b, "This session is for:", bold=True).pack(anchor="w", pady=(10, 2))
        self.purpose_v = tk.StringVar(value=plan.purpose if plan.purpose in PURPOSES else PURPOSES[-1])
        grid = ttk.Frame(b, style="Card.TFrame")
        grid.pack(anchor="w")
        for i, p in enumerate(PURPOSES):
            ttk.Radiobutton(grid, text=p, value=p, variable=self.purpose_v, style="Card.TRadiobutton",
                            command=self._apply_defaults).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 24))
        _label(b, "Raise an alarm (popup + sound) when:", bold=True).pack(anchor="w", pady=(12, 2))
        self.fo_v = tk.BooleanVar(value=plan.fo_rules)
        self.new_v = tk.BooleanVar(value=plan.alarm_new_rows)
        self.words_v = tk.StringVar(value=plan.alarm_words)
        self.count_v = tk.StringVar(value=str(plan.alarm_count))
        self.note_v = tk.StringVar(value=plan.note)
        ttk.Checkbutton(b, text="Down-site outage rules (FO manual: pre-outage at 4, outage at 5 sites)",
                        variable=self.fo_v, style="Card.TCheckbutton").pack(anchor="w")
        ttk.Checkbutton(b, text="Any NEW alarm row appears (selected severities)", variable=self.new_v,
                        style="Card.TCheckbutton").pack(anchor="w")
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(anchor="w", fill="x", pady=2)
        _label(row, "New alarm whose text contains (comma list):").pack(side="left")
        ttk.Entry(row, textvariable=self.words_v, width=34).pack(side="left", padx=6)
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(anchor="w", pady=2)
        _label(row, "Rows reach (0 = off):").pack(side="left")
        ttk.Spinbox(row, from_=0, to=100000, textvariable=self.count_v, width=8).pack(side="left", padx=6)
        _label(b, "Your note (optional - the AI learns from it):", bold=True).pack(anchor="w", pady=(12, 2))
        ttk.Entry(b, textvariable=self.note_v, width=80).pack(anchor="w", fill="x")
        _label(b, "You can change this later with 'Purpose / alarms' on the session.", muted=True).pack(
            anchor="w", pady=(8, 0))
        self.buttons("Start monitoring")

    def _apply_defaults(self) -> None:
        d = PURPOSE_DEFAULTS.get(self.purpose_v.get(), {})
        self.fo_v.set(d.get("fo_rules", False))
        self.new_v.set(d.get("alarm_new_rows", False))
        self.words_v.set(d.get("alarm_words", ""))

    def _ok(self) -> None:
        try:
            count = int(self.count_v.get().strip() or 0)
            if count < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Rows", "'Rows reach' must be a whole number (0 = off).", parent=self)
            return
        plan = SessionPlan(purpose=self.purpose_v.get(), fo_rules=self.fo_v.get(), alarm_new_rows=self.new_v.get(),
                           alarm_words=self.words_v.get().strip()[:300], alarm_count=count,
                           note=self.note_v.get().strip()[:500], asked=True)
        if not (plan.fo_rules or plan.alarm_new_rows or plan.words() or plan.alarm_count):
            if not messagebox.askyesno("No alarm", "Nothing will raise an alarm in this session (watch only). "
                                                   "Continue?", parent=self):
                return
        self.result = plan
        self.destroy()


# ------------------------------------------------------------------ why not an outage
