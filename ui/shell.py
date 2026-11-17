"""The window frame: menu bar, sidebar navigation, a page area, status bar; scaling for every screen.

Pages are added by the app (Overview, sessions) and by plugins (through host.add_page). Menus are built
from kernel MenuItems, so a plugin's menu entries and toolbar buttons appear without touching this file.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from kernel import MenuItem
from . import theme as T

MENU_ORDER = ["File", "Sessions", "Tasks", "Tools", "View", "Help"]


def auto_scale(root: tk.Tk) -> float:
    """Pick a font / padding factor from the monitor: DPI and width (laptop 1366 .. 4K)."""
    try:
        dpi = root.winfo_fpixels("1i")
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    except tk.TclError:
        return 1.0
    scale = dpi / 96.0
    if w < 1400:                       # small laptop: a little tighter
        scale *= 0.92
    elif w >= 2500:                    # 4K / wide: a little larger
        scale *= 1.1
    if h < 800:
        scale *= 0.92
    return max(0.8, min(1.6, scale))


class Shell:
    def __init__(self, root: tk.Tk, on_theme: Callable[[str], None], version: str):
        self.root = root
        self.version = version
        self._on_theme = on_theme
        self._pages: dict = {}             # name -> (widget, button)
        self._current: Optional[str] = None
        self._toolbar_items: list = []
        self._menus: dict = {}
        root.option_add("*tearOff", False)

        self.menubar = tk.Menu(root)
        root.config(menu=self.menubar)
        for name in MENU_ORDER:
            self._menus[name] = tk.Menu(self.menubar)
            self.menubar.add_cascade(label=name, menu=self._menus[name])

        # header band
        self.header = tk.Frame(root, bg=T.RED, height=T.px(54))
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        self.title_lbl = tk.Label(self.header, text="Vodafone Egypt", bg=T.RED, fg="#FFFFFF",
                                  font=(T.FONT, T.fs(16), "bold"))
        self.title_lbl.pack(side="left", padx=(T.px(18), T.px(8)))
        tk.Frame(self.header, bg="#FFFFFF", width=2, height=T.px(24)).pack(side="left", pady=T.px(15))
        tk.Label(self.header, text="Netcool Shift Monitor", bg=T.RED, fg="#FFFFFF",
                 font=(T.FONT, T.fs(12))).pack(side="left", padx=T.px(10))
        self.right = tk.Frame(self.header, bg=T.RED)
        self.right.pack(side="right", padx=T.px(14))
        self.shift_lbl = tk.Label(self.right, text="", bg=T.RED, fg="#FFFFFF", font=(T.FONT, T.fs(10), "bold"))
        self.shift_lbl.pack(side="right", padx=(T.px(14), 0))
        self.server_lbl = tk.Label(self.right, text="Not signed in", bg=T.RED, fg="#FDE8E8", font=(T.FONT, T.fs(10)))
        self.server_lbl.pack(side="right")

        # status bar first (side=bottom) so a short window never pushes it out
        sb = tk.Frame(root, bg=T.WHITE, highlightthickness=1, highlightbackground=T.GREY_3)
        sb.pack(fill="x", side="bottom")
        self.status_lbl = tk.Label(sb, text="Ready", bg=T.WHITE, fg=T.GREY_6, font=(T.FONT, T.fs(9)), anchor="w")
        self.status_lbl.pack(side="left", padx=T.px(10), pady=T.px(3))
        self.status_right = tk.Frame(sb, bg=T.WHITE)
        self.status_right.pack(side="right")

        # body: sidebar + toolbar + page area
        self.body = tk.Frame(root, bg=T.GREY_1)
        self.body.pack(fill="both", expand=True)
        self.sidebar = ttk.Frame(self.body, style="Side.TFrame", width=T.px(200))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.nav = ttk.Frame(self.sidebar, style="Side.TFrame")
        self.nav.pack(fill="x", pady=(T.px(8), 0))
        self.side_bottom = ttk.Frame(self.sidebar, style="Side.TFrame")
        self.side_bottom.pack(side="bottom", fill="x", pady=T.px(8))
        self.main = tk.Frame(self.body, bg=T.GREY_1)
        self.main.pack(side="left", fill="both", expand=True)
        self.toolbar = tk.Frame(self.main, bg=T.GREY_1)
        self.toolbar.pack(fill="x", padx=T.px(8), pady=(T.px(6), 0))
        self.pages = tk.Frame(self.main, bg=T.GREY_1)
        self.pages.pack(fill="both", expand=True)

        self._login: Optional[tk.Widget] = None

    # ---------------------------------------------------------- login screen / app screen
    def show_login(self, login: tk.Widget) -> None:
        """The sign-in screen covers the whole body (sidebar, toolbar and pages); nothing else is reachable."""
        self._login = login
        login.place(in_=self.body, x=0, y=0, relwidth=1, relheight=1)
        login.lift()

    def show_app(self) -> None:
        if self._login is not None:
            self._login.place_forget()

    @property
    def login_shown(self) -> bool:
        return self._login is not None and bool(self._login.winfo_manager())

    def status(self, text: str) -> None:
        self.status_lbl.config(text=text)

    def status_widget(self, text: str, command=None) -> tk.Label:
        lbl = tk.Label(self.status_right, text=text, bg=T.WHITE, fg=T.GREY_6, font=(T.FONT, T.fs(9)),
                       cursor="hand2" if command else "")
        lbl.pack(side="right", padx=T.px(10))
        if command:
            lbl.bind("<Button-1>", lambda e: command())
        return lbl

    # ---------------------------------------------------------- menus
    def add_menu_item(self, item: MenuItem) -> None:
        menu = self._menus.get(item.menu)
        if menu is None:
            menu = self._menus[item.menu] = tk.Menu(self.menubar)
            self.menubar.insert_cascade(self.menubar.index("Help"), label=item.menu, menu=menu)
        label = f"{item.icon} {item.label}".strip()
        menu.add_command(label=label, command=item.command, accelerator=item.shortcut)
        if item.shortcut:
            keys = item.shortcut.lower().replace("ctrl", "Control").replace("+", "-")
            try:
                self.root.bind_all(f"<{keys}>", lambda e, c=item.command: c())
            except tk.TclError:
                pass
        if item.toolbar:
            self.add_tool_button(label, item.command)

    def add_separator(self, menu: str) -> None:
        if menu in self._menus:
            self._menus[menu].add_separator()

    def add_tool_button(self, label: str, command, primary: bool = False, side: str = "left") -> ttk.Button:
        b = ttk.Button(self.toolbar, text=label, command=command, style="Primary.TButton" if primary else "TButton")
        b.pack(side=side, padx=(0, T.px(6)))
        return b

    # ---------------------------------------------------------- pages
    def add_page(self, name: str, widget: tk.Widget, icon: str = "", bottom: bool = False) -> None:
        parent = self.side_bottom if bottom else self.nav
        btn = ttk.Button(parent, text=f"{icon}  {name}".strip(), style="Side.TButton",
                         command=lambda: self.show_page(name))
        btn.pack(fill="x", padx=T.px(6), pady=1)
        self._pages[name] = (widget, btn)
        if self._current is None:
            self.show_page(name)

    def rename_page(self, name: str, text: str) -> None:
        if name in self._pages:
            self._pages[name][1].config(text=text)

    def remove_page(self, name: str) -> None:
        item = self._pages.pop(name, None)
        if item:
            item[0].pack_forget()
            item[1].destroy()
            if self._current == name:
                self._current = None
                if self._pages:
                    self.show_page(next(iter(self._pages)))

    def show_page(self, name: str) -> None:
        if name not in self._pages:
            return
        for n, (w, b) in self._pages.items():
            if n == name:
                w.pack(fill="both", expand=True, padx=T.px(8), pady=T.px(8))
                b.configure(style="SideOn.TButton")
            else:
                w.pack_forget()
                b.configure(style="Side.TButton")
        self._current = name

    @property
    def current_page(self) -> Optional[str]:
        return self._current

    def page_names(self) -> list:
        return list(self._pages)
