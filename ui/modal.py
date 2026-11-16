"""Shared dialog base for the app and its plugins, plus the alarm-sound dialog."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core.sound import SOUND_TYPES, install_sound
from . import theme as T
from .dialogs import _center, _header


class _Modal(tk.Toplevel):
    def __init__(self, master, title: str, sub: str = "", width: int = 620):
        super().__init__(master)
        self.title(title)
        self.configure(bg=T.WHITE)
        self.transient(master)
        self.resizable(False, False)
        self.attributes("-topmost", True)      # above the always-on-top alarm popups while open
        self.result = None
        _header(self, title, sub)
        self.body = ttk.Frame(self, style="Card.TFrame", padding=16)
        self.body.pack(fill="both", expand=True)
        self._width = width

    def buttons(self, ok_text: str = "OK", cancel_text: str = "Cancel") -> None:
        bar = ttk.Frame(self, style="Card.TFrame", padding=(16, 0, 16, 14))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text=cancel_text, command=self.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, style="Primary.TButton", command=self._ok).pack(side="right", padx=8)
        self.bind("<Escape>", lambda e: self.destroy())

    def run(self):
        _center(self, self.master)
        self.grab_set()
        self.wait_window()
        return self.result

    def _ok(self) -> None:
        raise NotImplementedError


def _label(parent, text, bold=False, muted=False, wrap=560):
    return ttk.Label(parent, text=text, style="CardMuted.TLabel" if muted else "Card.TLabel", wraplength=wrap,
                     font=(T.FONT, 10, "bold") if bold else None)



# ------------------------------------------------------------------ alarm sound
class SoundDialog(_Modal):
    """Built-in alarm or own .wav / .mp3, volume, test. -> (file, volume) or None."""

    def __init__(self, master, sound, current_file: str, volume: int):
        super().__init__(master, "Alarm sound", "Choose the sound that rings until alerts are acknowledged")
        self.sound = sound
        b = self.body
        self.file_v = tk.StringVar(value=current_file)
        self.vol_v = tk.IntVar(value=volume)
        _label(b, "Sound file:", bold=True).pack(anchor="w")
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(fill="x", pady=(2, 8))
        self.file_lbl = ttk.Label(row, style="Card.TLabel", width=52)
        self.file_lbl.pack(side="left")
        ttk.Button(row, text="Built-in alarm", command=lambda: self._set_file("")).pack(side="right")
        ttk.Button(row, text="Choose .wav / .mp3…", command=self._choose).pack(side="right", padx=6)
        _label(b, "Volume:", bold=True).pack(anchor="w")
        ttk.Scale(b, from_=5, to=100, variable=self.vol_v, orient="horizontal", length=360).pack(anchor="w")
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(anchor="w", pady=10)
        ttk.Button(row, text="▶ Test (4 s)", command=self._test).pack(side="left")
        ttk.Button(row, text="■ Stop", command=sound.stop).pack(side="left", padx=6)
        self.err = _label(b, "", muted=True)
        self.err.pack(anchor="w")
        self._set_file(current_file)
        self.buttons("Save")

    def _set_file(self, path: str) -> None:
        self.file_v.set(path)
        self.file_lbl.config(text=path.replace("\\", "/").split("/")[-1] if path else "Built-in alarm (3 tones)")

    def _choose(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose the alarm sound",
                                          filetypes=[("Sound", " ".join(f"*{t}" for t in SOUND_TYPES))])
        if not path:
            return
        try:
            self._set_file(str(install_sound(path)))
        except (ValueError, OSError) as exc:
            messagebox.showerror("Alarm sound", str(exc), parent=self)

    def _test(self) -> None:
        self.sound.stop()
        self.sound.set_custom(self.file_v.get())
        self.sound.set_volume(int(self.vol_v.get()))
        self.sound.test(4)
        self.after(1500, lambda: self.err.config(
            text=(f"Problem: {self.sound.last_error}" if self.sound.last_error else f"Playing via {self.sound.backend}")))

    def _ok(self) -> None:
        self.sound.stop()
        self.result = (self.file_v.get(), int(self.vol_v.get()))
        self.destroy()


