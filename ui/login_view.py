"""Sign-in card shown once at start-up. One login serves every session."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.config import Settings
from core.credentials import CredentialStore
from . import theme as T


class LoginView(tk.Frame):
    def __init__(self, master, settings: Settings, creds: CredentialStore, on_submit):
        super().__init__(master, bg=T.GREY_1)
        self._creds, self._on_submit = creds, on_submit

        card = tk.Frame(self, bg=T.WHITE, highlightthickness=1, highlightbackground=T.GREY_3)
        card.place(relx=0.5, rely=0.45, anchor="center")
        stripe = tk.Frame(card, bg=T.RED, height=6)
        stripe.pack(fill="x")
        inner = ttk.Frame(card, style="Card.TFrame", padding=(36, 26))
        inner.pack()

        ttk.Label(inner, text="Sign in to Netcool", style="H1.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(inner, text="One sign-in is shared by all monitoring sessions.",
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 18))

        ttk.Label(inner, text="User ID", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.user_v = tk.StringVar(value=settings.last_username)
        self.user_cb = ttk.Combobox(inner, textvariable=self.user_v, values=settings.saved_usernames,
                                    width=36)
        self.user_cb.grid(row=3, column=0, sticky="ew", pady=(2, 10))
        self.user_cb.bind("<<ComboboxSelected>>", lambda e: self._load_pw())
        self.user_cb.bind("<FocusOut>", lambda e: self._load_pw())

        ttk.Label(inner, text="Password", style="Card.TLabel").grid(row=4, column=0, sticky="w")
        self.pass_v = tk.StringVar()
        self.pass_e = ttk.Entry(inner, textvariable=self.pass_v, show="●", width=38)
        self.pass_e.grid(row=5, column=0, sticky="ew", pady=(2, 8))

        self.remember_v = tk.BooleanVar(value=creds.available)
        ttk.Checkbutton(inner, text="Remember password (Windows Credential Manager)",
                        variable=self.remember_v, style="Card.TCheckbutton",
                        state="normal" if creds.available else "disabled").grid(row=6, column=0, sticky="w")

        ttk.Label(inner, text="Server", style="CardMuted.TLabel").grid(row=7, column=0, sticky="w", pady=(12, 0))
        self.url_v = tk.StringVar(value=settings.server.base_url)
        ttk.Entry(inner, textvariable=self.url_v, width=38).grid(row=8, column=0, sticky="ew", pady=(2, 14))

        self.btn = ttk.Button(inner, text="Sign in", style="Primary.TButton", command=self._submit)
        self.btn.grid(row=9, column=0, sticky="ew")
        self.msg = tk.Label(inner, text="", bg=T.WHITE, fg=T.RED, font=(T.FONT, 9), wraplength=320,
                            justify="left")
        self.msg.grid(row=10, column=0, sticky="w", pady=(10, 0))

        for w in (self.user_cb, self.pass_e):
            w.bind("<Return>", lambda e: self._submit())
        self._load_pw()
        (self.pass_e if self.user_v.get() else self.user_cb).focus_set()

    def _load_pw(self) -> None:
        pw = self._creds.get(self.user_v.get().strip())
        if pw and not self.pass_v.get():
            self.pass_v.set(pw)

    def _submit(self) -> None:
        user, pw = self.user_v.get().strip(), self.pass_v.get()
        if not user or not pw:
            self.set_error("User ID and password are required.")
            return
        self.busy(True)
        self._on_submit(user, pw, self.remember_v.get(), self.url_v.get().strip())

    def busy(self, on: bool) -> None:
        self.btn.config(state="disabled" if on else "normal", text="Signing in…" if on else "Sign in")
        if on:
            self.msg.config(text="Connecting to the server… (first start can take ~20 s)", fg=T.GREY_6)

    def set_error(self, text: str) -> None:
        self.busy(False)
        self.msg.config(text=text, fg=T.RED)
