"""Small interactive widgets: severity chips (MOM colours), KPI cards, toasts, banners."""
from __future__ import annotations

import tkinter as tk

from . import theme as T


class SeverityBar(tk.Frame):
    """Six toggle chips like the MOM AEL severity bar: colour + live count. Click to include/exclude."""

    def __init__(self, master, selected: set, on_change, bg=None):
        bg = bg or T.WHITE
        super().__init__(master, bg=bg)
        self._sel = set(selected)
        self._on_change = on_change
        self._chips: dict = {}
        tk.Label(self, text="Show & count:", bg=bg, fg=T.GREY_9,
                 font=(T.FONT, 9, "bold")).pack(side="left", padx=(0, 8))
        for val in (5, 4, 3, 2, 1, 0):
            name, color, fg = T.SEV[val]
            chip = tk.Label(self, text=f" {name}  0 ", font=(T.FONT, 9, "bold"), padx=8, pady=3,
                            cursor="hand2", bd=0, relief="flat")
            chip.pack(side="left", padx=2)
            chip.bind("<Button-1>", lambda e, v=val: self._toggle(v))
            chip.bind("<Enter>", lambda e, c=chip: c.config(relief="solid", bd=1))
            chip.bind("<Leave>", lambda e, c=chip: c.config(relief="flat", bd=0))
            self._chips[val] = (chip, color, fg, name)
        for text, sel in (("Down (Crit+Major)", {5, 4}), ("All", {5, 4, 3, 2, 1, 0})):
            b = tk.Label(self, text=text, bg=bg, fg=T.RED, font=(T.FONT, 9, "underline"), cursor="hand2")
            b.pack(side="left", padx=(10, 0))
            b.bind("<Button-1>", lambda e, s=sel: self._set(s))
        self._paint()

    def _toggle(self, v: int) -> None:
        sel = set(self._sel)
        sel.symmetric_difference_update({v})
        if not sel:
            return  # at least one severity must stay selected
        self._set(sel)

    def _set(self, sel: set) -> None:
        self._sel = set(sel)
        self._paint()
        self._on_change(set(self._sel))

    def _paint(self) -> None:
        for v, (chip, color, fg, _) in self._chips.items():
            if v in self._sel:
                chip.config(bg=color, fg=fg)
            else:
                chip.config(bg=T.GREY_2, fg=T.GREY_6)

    def set_counts(self, counts: dict) -> None:
        for v, (chip, _, _, name) in self._chips.items():
            chip.config(text=f" {'✓ ' if v in self._sel else ''}{name}  {counts.get(v, 0)} ")

    @property
    def selected(self) -> set:
        return set(self._sel)


class KpiCard(tk.Frame):
    def __init__(self, master, label: str, accent: str = T.RED, width: int = 150):
        super().__init__(master, bg=T.WHITE, highlightthickness=1, highlightbackground=T.GREY_2)
        tk.Frame(self, bg=accent, width=4).pack(side="left", fill="y")
        box = tk.Frame(self, bg=T.WHITE)
        box.pack(side="left", fill="both", expand=True, padx=(10, 14), pady=8)
        self.value = tk.Label(box, text="-", bg=T.WHITE, fg=T.BLACK, font=(T.FONT, 18, "bold"), anchor="w")
        self.value.pack(anchor="w")
        tk.Label(box, text=label, bg=T.WHITE, fg=T.GREY_6, font=(T.FONT, 9), anchor="w").pack(anchor="w")
        self.configure(width=width)

    def set(self, value, color: str = "") -> None:
        self.value.config(text=str(value), fg=color or T.BLACK)


class Banner(tk.Frame):
    """Data-check strip: shows warnings, hides itself when there are none."""

    @staticmethod
    def _colors(level: str) -> tuple:          # read at draw time: follows the light / dark theme
        return {"ERROR": (T.RED, "#FFFFFF"), "WARN": (T.LEMON, "#000000")}.get(level, (T.GREY_2, T.GREY_9))

    def __init__(self, master):
        super().__init__(master, bg=T.WHITE)
        self._rows: list = []

    def show(self, checks: list) -> None:
        for w in self._rows:
            w.destroy()
        self._rows = []
        for c in checks[:4]:
            bg, fg = self._colors(c.level)
            icon = {"ERROR": "✖", "WARN": "⚠", "INFO": "ℹ"}.get(c.level, "ℹ")
            lbl = tk.Label(self, text=f"  {icon}  {c.text}", bg=bg, fg=fg, anchor="w", justify="left",
                           font=(T.FONT, 9, "bold" if c.level != "INFO" else "normal"), padx=6, pady=3,
                           wraplength=1100)
            lbl.pack(fill="x", pady=1)
            self._rows.append(lbl)


class Toast:
    """Non-blocking notification in the bottom-right corner (auto-closes)."""

    _stack: list = []

    @classmethod
    def show(cls, root, text: str, color: str = "", ms: int = 6000) -> None:
        color = color or T.CHARCOAL
        try:
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            frame = tk.Frame(win, bg=color)
            frame.pack()
            tk.Frame(frame, bg=T.RED, width=5).pack(side="left", fill="y")
            tk.Label(frame, text=text, bg=color, fg=T.WHITE, font=(T.FONT, 10), padx=14, pady=10,
                     wraplength=360, justify="left").pack(side="left")
            win.update_idletasks()
            cls._stack = [w for w in cls._stack if w.winfo_exists()]
            n = len(cls._stack)
            x = root.winfo_rootx() + root.winfo_width() - win.winfo_width() - 20
            y = root.winfo_rooty() + root.winfo_height() - (win.winfo_height() + 10) * (n + 1) - 30
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
            cls._stack.append(win)
            win.bind("<Button-1>", lambda e: win.destroy())
            root.after(ms, lambda: win.winfo_exists() and win.destroy())
        except tk.TclError:
            pass


def hover(widget: tk.Widget, normal: str, over: str) -> None:
    widget.bind("<Enter>", lambda e: widget.config(bg=over))
    widget.bind("<Leave>", lambda e: widget.config(bg=normal))
