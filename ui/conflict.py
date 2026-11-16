"""Non-blocking notice: the same MOM user is signed in somewhere else (e.g. MOM open in Edge)."""
from __future__ import annotations

import logging
import tkinter as tk
from datetime import timedelta

from core import clock
from . import theme as T

log = logging.getLogger("alerts")


class ConflictNotice:
    def __init__(self, root, on_open_mom, status):
        self.root, self.on_open_mom, self.status = root, on_open_mom, status
        self._win = None
        self._shown_at = None

    def show(self, note: str) -> None:
        self.status(note[:140])
        log.warning("Session conflict: %s", note)
        try:
            if self._win is not None and self._win.winfo_exists():
                self._win.msg.config(text=note)
                self._win.lift()
                return
        except tk.TclError:
            pass
        if self._shown_at and clock.now() - self._shown_at < timedelta(minutes=30):
            return
        self._shown_at = clock.now()
        win = self._win = tk.Toplevel(self.root)
        win.title("MOM user signed in elsewhere")
        win.configure(bg=T.WHITE)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        head = tk.Frame(win, bg=T.FRESH_ORANGE)
        head.pack(fill="x")
        tk.Label(head, text="MOM is open with the same user somewhere else", bg=T.FRESH_ORANGE, fg="#000000",
                 font=(T.FONT, T.fs(12), "bold"), padx=16, pady=10).pack(anchor="w")
        win.msg = tk.Label(win, text=note, bg=T.WHITE, fg=T.BLACK, font=(T.FONT, T.fs(10)), justify="left",
                           wraplength=T.px(560), padx=16, pady=10)
        win.msg.pack(anchor="w")
        tk.Label(win, text="MOM allows one sign-in per user. To use MOM and the tool together:\n"
                           "  •  open MOM from the tool (Tools > Open MOM) - it shares the tool's login, or\n"
                           "  •  sign in to MOM in Edge with a DIFFERENT user.\n"
                           "Monitoring continues; the tool signs in again by itself when needed.",
                 bg=T.WHITE, fg=T.GREY_9, font=(T.FONT, T.fs(9)), justify="left", padx=16).pack(anchor="w")
        bar = tk.Frame(win, bg=T.WHITE)
        bar.pack(fill="x", padx=16, pady=12)
        tk.Button(bar, text="OK", bg=T.WHITE, fg=T.BLACK, relief="solid", bd=1, padx=14, pady=4,
                  command=win.destroy).pack(side="right")
        tk.Button(bar, text="🌐 Open MOM from the tool", bg=T.RED, fg="#FFFFFF", relief="flat", padx=14, pady=4,
                  font=(T.FONT, T.fs(9), "bold"),
                  command=lambda: (win.destroy(), self.on_open_mom())).pack(side="right", padx=8)
        win.update_idletasks()
        try:
            win.geometry(f"+{max(0, self.root.winfo_rootx() + 300)}+{self.root.winfo_rooty() + 140}")
        except tk.TclError:
            pass
