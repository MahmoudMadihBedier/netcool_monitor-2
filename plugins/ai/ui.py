"""Dialogs of the AI plugin: session purpose, 'why not an outage', AI memory."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from ui import theme as T
from ui.modal import _label, _Modal
from .brain import REASONS


# ------------------------------------------------------------------ session purpose + alarm rules
class NotOutageDialog(_Modal):
    """-> (reason, note) or None (cancel: nothing is muted)."""

    def __init__(self, master, outage, advice: Optional[dict] = None):
        super().__init__(master, "Why is this not an outage?", outage.title)
        b = self.body
        _label(b, "Your answer teaches the AI. The group is silenced for 2 hours in all sessions.", muted=True).pack(
            anchor="w")
        guess = (advice or {}).get("reason", "")
        self.reason_v = tk.StringVar(value=guess if guess in REASONS else "")
        for r in REASONS:
            ttk.Radiobutton(b, text=r + ("   ← AI guess" if r == guess else ""), value=r, variable=self.reason_v,
                            style="Card.TRadiobutton").pack(anchor="w", pady=1)
        _label(b, "Note (optional, English or Arabic) - e.g. 'PA upgrade on HUB H4179 until 03:00':",
               bold=True).pack(anchor="w", pady=(10, 2))
        self.note = tk.Text(b, height=3, width=70, font=(T.FONT, 10), relief="solid", bd=1, wrap="word")
        self.note.pack(fill="x")
        self.buttons("Not an outage")

    def _ok(self) -> None:
        if not self.reason_v.get():
            messagebox.showinfo("Reason", "Pick a reason (or 'Other' and write a note).", parent=self)
            return
        self.result = (self.reason_v.get(), self.note.get("1.0", "end").strip()[:500])
        self.destroy()


# ------------------------------------------------------------------ AI memory
class BrainDialog(_Modal):
    def __init__(self, master, brain, records=None):
        self.records = records
        super().__init__(master, "AI assistant", "Learns from your decisions - advises, never silences an alarm")
        self.brain = brain
        b = self.body
        self.info = _label(b, "")
        self.info.pack(anchor="w")
        cols = ("t", "what", "title")
        self.tv = ttk.Treeview(b, columns=cols, show="headings", height=10)
        for c, t, w in (("t", "When", 120), ("what", "Decision", 220), ("title", "Outage", 330)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w")
        self.tv.pack(fill="both", pady=8)
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="Export experience (AI + records)…", command=self._export).pack(side="left")
        ttk.Button(row, text="Import a colleague's experience…", command=self._import).pack(side="left", padx=6)
        bar = ttk.Frame(self, style="Card.TFrame", padding=(16, 0, 16, 14))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self._refresh()

    def _refresh(self) -> None:
        pos, neg, pur = self.brain.counts()
        state = "giving advice" if self.brain.net is not None else "still learning (needs 5 of each)"
        self.info.config(text=f"Learned {pos} real outages, {neg} 'not an outage' decisions and {pur} session "
                              f"purposes  ·  {state}")
        self.tv.delete(*self.tv.get_children())
        for e in reversed(self.brain.outages[-50:]):
            what = "REAL outage" if e["label"] else f"NOT: {e['reason'] or 'Other'}"
            self.tv.insert("", "end", values=(e["t"].replace("T", " "), what, e["title"]))

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile="netcool_ai_brain.json",
                                            filetypes=[("AI memory", "*.json")])
        if path:
            n = self.brain.export_to(path, self.records)
            messagebox.showinfo("Export", f"{n} decisions saved to\n{path}", parent=self)

    def _import(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=[("AI memory", "*.json")])
        if not path:
            return
        try:
            n = self.brain.import_from(path, self.records)
        except Exception as exc:
            messagebox.showerror("Import", f"Not a valid AI memory file:\n{exc}", parent=self)
            return
        messagebox.showinfo("Import", f"{n} new decisions merged. The AI re-learns in the background.", parent=self)
        self._refresh()
