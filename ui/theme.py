"""Vodafone look: Vodafone Red, white, black + the Vodafone secondary palette.
MOM severity colours are the exact AEL row colours."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

# --- Vodafone primary
RED = "#E60000"
RED_DARK = "#BD0000"
RED_DEEP = "#820000"
RED_TINT = "#FDE8E8"
BLACK = "#000000"
CHARCOAL = "#25282B"
GREY_9 = "#4A4D4E"          # Vodafone dark grey
GREY_6 = "#7E7E7E"
GREY_3 = "#CCCCCC"
GREY_2 = "#E6E6E6"
GREY_1 = "#F4F4F4"
WHITE = "#FFFFFF"
# --- Vodafone secondary
AQUA = "#00B0CA"
TURQUOISE = "#007C92"
SPRING_GREEN = "#A8B400"
FRESH_ORANGE = "#EB9700"
LEMON = "#FECB00"
RED_VIOLET = "#9C2AA0"
GREEN = "#008A00"
AMBER = FRESH_ORANGE

# --- MOM (OMNIbus AEL) severity colours
SEV = {5: ("Critical", "#ff0000", WHITE), 4: ("Major", "#ffb329", BLACK), 3: ("Minor", "#ffff00", BLACK),
       2: ("Warning", "#63b8ff", BLACK), 1: ("Indeterminate", "#bf3eff", WHITE), 0: ("Clear", "#00cd00", BLACK)}

LEVEL_COLORS = {"OUTAGE": RED, "PRE-OUTAGE": FRESH_ORANGE, "WATCH": LEMON, "POSSIBLE": AQUA}
OUTAGE_COLOR = {"YELLOW": LEMON, "ORANGE": FRESH_ORANGE, "RED": RED}

FONT = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"
MONO = "Consolas" if sys.platform.startswith("win") else "DejaVu Sans Mono"

STATE_COLORS = {
    "CONNECTING": FRESH_ORANGE, "MONITORING": GREEN, "WARNING": FRESH_ORANGE,
    "BLIND": RED, "ERROR": RED, "STOPPED": GREY_6,
}
STATE_MARK = {"MONITORING": "●", "WARNING": "▲", "BLIND": "✖", "CONNECTING": "◌", "STOPPED": "■"}

# --- surfaces that change between light and dark (widgets read T.WHITE / T.GREY_1 / T.BLACK at build time)
_LIGHT = dict(WHITE="#FFFFFF", GREY_1="#F4F4F4", GREY_2="#E6E6E6", GREY_3="#CCCCCC", GREY_6="#7E7E7E",
              GREY_9="#4A4D4E", BLACK="#000000", CHARCOAL="#25282B", RED_TINT="#FDE8E8")
_DARK = dict(WHITE="#1E2124", GREY_1="#15171A", GREY_2="#2A2E33", GREY_3="#3A3F45", GREY_6="#9AA0A6",
             GREY_9="#C9CDD1", BLACK="#F2F2F2", CHARCOAL="#E6E6E6", RED_TINT="#4A1C1C")
SCALE = 1.0
MODE = "light"


def set_mode(mode: str) -> None:
    """'light' or 'dark': swaps the surface colours. Call before the window is built."""
    global MODE
    MODE = "dark" if mode == "dark" else "light"
    globals().update(_DARK if MODE == "dark" else _LIGHT)
    STATE_COLORS["STOPPED"] = GREY_6
    SEV[4] = ("Major", "#ffb329", "#000000")


def set_scale(scale: float) -> None:
    """Font / padding factor for high-DPI and small screens (0.8 .. 1.6). 0 = automatic (see shell)."""
    global SCALE
    SCALE = max(0.8, min(1.6, scale or 1.0))


def px(n: int) -> int:
    return max(1, int(round(n * SCALE)))


def fs(n: int) -> int:
    return max(7, int(round(n * SCALE)))


def apply(root: tk.Tk) -> ttk.Style:
    root.configure(bg=GREY_1)
    from tkinter import font as tkfont
    for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10), ("TkHeadingFont", 10),
                       ("TkFixedFont", 9)):
        try:
            tkfont.nametofont(name).configure(family=MONO if name == "TkFixedFont" else FONT, size=fs(size))
        except tk.TclError:
            pass
    try:
        root.option_add("*Menu.background", WHITE)
        root.option_add("*Menu.foreground", BLACK)
        root.option_add("*Menu.activeBackground", RED)
        root.option_add("*Menu.activeForeground", "#FFFFFF")
        root.option_add("*Menu.relief", "flat")
        root.option_add("*Menu.font", (FONT, fs(10)))
    except tk.TclError:
        pass
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=GREY_1, foreground=BLACK, font=(FONT, fs(10)),
                 bordercolor=GREY_3, focuscolor=RED)
    st.configure("Side.TFrame", background=CHARCOAL if MODE == "light" else "#0F1113")
    st.configure("Side.TButton", background=CHARCOAL if MODE == "light" else "#0F1113", foreground="#FFFFFF",
                 bordercolor=CHARCOAL, anchor="w", padding=(px(14), px(9)), font=(FONT, fs(10)), relief="flat")
    st.map("Side.TButton", background=[("active", RED), ("selected", RED)])
    st.configure("SideOn.TButton", background=RED, foreground="#FFFFFF", bordercolor=RED, anchor="w",
                 padding=(px(14), px(9)), font=(FONT, fs(10), "bold"), relief="flat")
    st.map("SideOn.TButton", background=[("active", RED_DARK)])

    st.configure("TFrame", background=GREY_1)
    st.configure("Card.TFrame", background=WHITE, relief="flat")
    st.configure("Dark.TFrame", background=BLACK)
    st.configure("TLabel", background=GREY_1, foreground=BLACK)
    st.configure("Card.TLabel", background=WHITE, foreground=BLACK)
    st.configure("Muted.TLabel", background=GREY_1, foreground=GREY_6)
    st.configure("CardMuted.TLabel", background=WHITE, foreground=GREY_6)
    st.configure("H1.TLabel", background=WHITE, foreground=BLACK, font=(FONT, fs(18), "bold"))
    st.configure("H2.TLabel", background=GREY_1, foreground=BLACK, font=(FONT, fs(12), "bold"))
    st.configure("CardH2.TLabel", background=WHITE, foreground=BLACK, font=(FONT, fs(12), "bold"))
    st.configure("Stat.TLabel", background=WHITE, foreground=BLACK, font=(FONT, fs(16), "bold"))

    st.configure("TButton", background=WHITE, foreground=BLACK, bordercolor=GREY_3,
                 padding=(12, 6), relief="flat")
    st.map("TButton", background=[("active", GREY_2), ("disabled", GREY_1)],
           foreground=[("disabled", GREY_3)], bordercolor=[("active", RED)])
    st.configure("Primary.TButton", background=RED, foreground="#FFFFFF", bordercolor=RED,
                 font=(FONT, fs(10), "bold"), padding=(14, 7))
    st.map("Primary.TButton", background=[("pressed", RED_DEEP), ("active", RED_DARK),
                                          ("disabled", GREY_3)],
           foreground=[("disabled", "#FFFFFF")])
    st.configure("Small.TButton", padding=(8, 3), font=(FONT, fs(9)))
    st.map("Small.TButton", background=[("active", GREY_2)], bordercolor=[("active", RED)])
    st.configure("Primary.TMenubutton", background=RED, foreground="#FFFFFF", bordercolor=RED,
                 arrowcolor="#FFFFFF", padding=(8, 3), font=(FONT, fs(9), "bold"))
    st.map("Primary.TMenubutton", background=[("active", RED_DARK), ("pressed", RED_DEEP)])
    st.configure("Toolbar.TButton", background=CHARCOAL, foreground=WHITE, bordercolor=CHARCOAL,
                 padding=(9, 6))
    st.map("Toolbar.TButton", background=[("pressed", RED_DEEP), ("active", RED)])
    st.configure("Toolbar.TCheckbutton", background=BLACK, foreground=WHITE)
    st.map("Toolbar.TCheckbutton", background=[("active", BLACK)],
           indicatorcolor=[("selected", RED), ("!selected", WHITE)])
    st.configure("TCheckbutton", background=GREY_1)
    st.configure("Card.TRadiobutton", background=WHITE)
    st.map("Card.TRadiobutton", background=[("active", WHITE)])
    st.configure("Card.TCheckbutton", background=WHITE)
    st.map("Card.TCheckbutton", background=[("active", WHITE)],
           indicatorcolor=[("selected", RED), ("!selected", WHITE)])

    st.configure("TEntry", fieldbackground=WHITE, bordercolor=GREY_3, padding=5)
    st.map("TEntry", bordercolor=[("focus", RED)], lightcolor=[("focus", RED)])
    st.configure("TCombobox", fieldbackground=WHITE, padding=4, arrowcolor=RED)
    st.map("TCombobox", bordercolor=[("focus", RED)], fieldbackground=[("readonly", WHITE)])
    st.configure("TSpinbox", fieldbackground=WHITE, arrowcolor=RED, padding=4)

    st.configure("TNotebook", background=GREY_1, borderwidth=0, tabmargins=(8, 6, 8, 0))
    st.configure("TNotebook.Tab", background=GREY_2, foreground=GREY_9, padding=(16, 7),
                 font=(FONT, fs(10), "bold"))
    st.map("TNotebook.Tab", background=[("selected", RED), ("active", WHITE)],
           foreground=[("selected", WHITE)], expand=[("selected", (1, 1, 1, 0))])
    st.configure("Inner.TNotebook", background=WHITE, tabmargins=(0, 4, 0, 0))
    st.configure("Inner.TNotebook.Tab", background=GREY_1, foreground=GREY_9, padding=(14, 5),
                 font=(FONT, fs(9), "bold"))
    st.map("Inner.TNotebook.Tab", background=[("selected", BLACK), ("active", GREY_2)],
           foreground=[("selected", WHITE)])

    st.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=BLACK,
                 rowheight=px(24), bordercolor=GREY_3)
    st.map("Treeview", background=[("selected", RED)], foreground=[("selected", "#FFFFFF")])
    st.configure("Treeview.Heading", background=BLACK if MODE == "light" else GREY_2,
                 foreground="#FFFFFF" if MODE == "light" else BLACK, relief="flat",
                 font=(FONT, fs(9), "bold"), padding=(px(6), px(5)))
    st.map("Treeview.Heading", background=[("active", RED)])
    st.configure("TLabelframe", background=WHITE, bordercolor=GREY_3)
    st.configure("TLabelframe.Label", background=WHITE, foreground=RED, font=(FONT, fs(10), "bold"))
    st.configure("Vertical.TScrollbar", background=GREY_3, troughcolor=GREY_1, arrowcolor=BLACK)
    st.configure("TPanedwindow", background=GREY_1)
    return st


def pill(master, text: str, color: str, fg: str = "#FFFFFF") -> tk.Label:
    return tk.Label(master, text=text, bg=color, fg=fg, font=(FONT, fs(10), "bold"), padx=12, pady=3)


def load_logo(height: int = 34):
    """Official logo file placed by the user in assets/ (logo.png / logo.gif). None if absent."""
    from core.config import ASSETS_DIR
    for name in ("logo.png", "logo.gif", "vodafone_logo.png"):
        p = ASSETS_DIR / name
        if p.exists():
            try:
                img = tk.PhotoImage(file=str(p))
                h = img.height()
                if h > height:
                    f = max(1, round(h / height))
                    img = img.subsample(f, f)
                return img
            except tk.TclError:
                continue
    return None
