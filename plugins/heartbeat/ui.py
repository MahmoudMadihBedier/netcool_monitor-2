"""Heartbeat Failure task: options dialog + results window (table, PDF, copy)."""
from __future__ import annotations

from core import clock
import os
import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from core.config import HEARTBEAT_DIR, Settings
from .heartbeat import (SEV_NAMES, HeartbeatReport, build_html, shift_name, shift_window,
                            site_list_text, to_tsv)
from ui import theme as T


def _parse_dt(text: str) -> Optional[datetime]:
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


class FilterBox(ttk.Combobox):
    """Combobox over 550+ filters that narrows its list while you type."""

    def __init__(self, master, values: list, textvariable: tk.StringVar, **kw):
        super().__init__(master, textvariable=textvariable, values=values, **kw)
        self._all = values
        self.bind("<KeyRelease>", self._narrow)

    def _narrow(self, event=None) -> None:
        if event is not None and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        words = [w for w in self.get().lower().split() if w]
        self.configure(values=[v for v in self._all if all(w in v.lower() for w in words)][:400] or self._all)


class HeartbeatDialog(tk.Toplevel):
    """result = dict(hb_filter, hb_view, ref_filter, ref_view, since, until, severities, window, hours)"""

    def __init__(self, master, settings: Settings, filters: list, views: list):
        super().__init__(master)
        self.title("Heartbeat task")
        self.configure(bg=T.WHITE)
        self.transient(master)
        self.resizable(False, False)
        self.result: Optional[dict] = None
        hs = settings.heartbeat
        self._hs = hs
        fids = [f.id for f in filters]
        vids = [v.id for v in views]

        bar = tk.Frame(self, bg=T.RED)
        bar.pack(fill="x")
        tk.Label(bar, text="💓  Heartbeat Failure task", bg=T.RED, fg=T.WHITE,
                 font=(T.FONT, 13, "bold")).pack(anchor="w", padx=16, pady=(10, 0))
        tk.Label(bar, text="Site numbers with heartbeat in the shift, not in the 3G filter (#N/A), without TT - each once.",
                 bg=T.RED, fg=T.RED_TINT, font=(T.FONT, 9)).pack(anchor="w", padx=16, pady=(0, 10))
        f = ttk.Frame(self, style="Card.TFrame", padding=16)
        f.pack(fill="both")
        f.columnconfigure(1, weight=1)

        def pick(default, pool, *hints):
            if default in pool:
                return default
            for h in hints:
                hit = next((x for x in pool if h.lower() in x.lower()), None)
                if hit:
                    return hit
            return default

        self.hb_v = tk.StringVar(value=pick(hs.hb_filter, fids, "LTE_Heartbeat_Ericsson", "heartbeat"))
        self.hbv_v = tk.StringVar(value=pick(hs.hb_view, vids, "3g_H_easymonitoring"))
        self.ref_v = tk.StringVar(value=pick(hs.ref_filter, fids, "3G_Marco:group", "3G_Marco"))
        self.refv_v = tk.StringVar(value=pick(hs.ref_view, vids, "3g_H_easymonitoring"))
        rows = [("Heartbeat filter (LTE)", FilterBox(f, fids, self.hb_v, width=62)),
                ("   view", ttk.Combobox(f, textvariable=self.hbv_v, values=vids, width=40)),
                ("Compare with (3G filter)", FilterBox(f, fids, self.ref_v, width=62)),
                ("   view", ttk.Combobox(f, textvariable=self.refv_v, values=vids, width=40))]
        for i, (lab, w) in enumerate(rows):
            ttk.Label(f, text=lab, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=3, padx=(0, 10))
            w.grid(row=i, column=1, sticky="ew", pady=3)

        ttk.Label(f, text=f"Shift (heartbeat LastOccurrence, times {clock.label()})", style="CardH2.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self.win_v = tk.StringVar(value=hs.window)
        self.hours_v = tk.IntVar(value=hs.hours)
        tw = ttk.Frame(f, style="Card.TFrame")
        tw.grid(row=5, column=0, columnspan=2, sticky="w")
        start = getattr(hs, "shift_start", "06:30")
        prev = shift_window("previous_shift", start=start)
        cur = shift_window("current_shift", start=start)
        ttk.Radiobutton(tw, text=f"Last shift: {shift_name(prev[0], start)}  {prev[0]:%d/%m %H:%M} → "
                                 f"{prev[1]:%d/%m %H:%M}   (recommended)",
                        variable=self.win_v, value="previous_shift").grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Radiobutton(tw, text=f"Current shift: {shift_name(cur[0], start)}  {cur[0]:%d/%m %H:%M} → now",
                        variable=self.win_v, value="current_shift").grid(row=1, column=0, columnspan=6, sticky="w")
        ttk.Radiobutton(tw, text="Last", variable=self.win_v, value="last_hours").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(tw, from_=1, to=72, width=4, textvariable=self.hours_v).grid(row=2, column=1, padx=4, sticky="w")
        ttk.Label(tw, text="hours", style="Card.TLabel").grid(row=2, column=2, sticky="w")
        ttk.Radiobutton(tw, text="From", variable=self.win_v, value="custom").grid(row=3, column=0, sticky="w")
        self.from_v = tk.StringVar(value=f"{prev[0]:%d/%m/%Y %H:%M}")
        self.to_v = tk.StringVar(value=f"{clock.now():%d/%m/%Y %H:%M}")
        ttk.Entry(tw, textvariable=self.from_v, width=17).grid(row=3, column=1, columnspan=2, padx=4, sticky="w")
        ttk.Label(tw, text="to", style="Card.TLabel").grid(row=3, column=3, padx=4)
        ttk.Entry(tw, textvariable=self.to_v, width=17).grid(row=3, column=4, sticky="w")
        ttk.Label(tw, text="(day/month/year hour:minute)", style="CardMuted.TLabel").grid(row=3, column=5, padx=6)
        ttk.Label(tw, text="Shifts: Morning 06:30-18:30 · Night 18:30-06:30", style="CardMuted.TLabel").grid(
            row=4, column=0, columnspan=6, sticky="w", pady=(4, 0))

        ttk.Label(f, text="Alarm severity", style="CardH2.TLabel").grid(row=6, column=0, columnspan=2,
                                                                          sticky="w", pady=(12, 4))
        sv = tk.Frame(f, bg=T.WHITE)
        sv.grid(row=7, column=0, columnspan=2, sticky="w")
        self.sev_vars = {}
        for v in (5, 4, 3, 2, 1):
            name, col, fg = T.SEV[v]
            var = tk.BooleanVar(value=v in hs.severities)
            self.sev_vars[v] = var
            tk.Checkbutton(sv, text=f" {name} ", variable=var, bg=col, fg=T.BLACK, selectcolor=T.WHITE,
                           activebackground=col, font=(T.FONT, 9, "bold"), padx=6, pady=2,
                           relief="flat").pack(side="left", padx=2)
        ttk.Label(f, text="The procedure uses Critical; add Major to see more.", style="CardMuted.TLabel").grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

        b = ttk.Frame(f, style="Card.TFrame")
        b.grid(row=9, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Run heartbeat task", style="Primary.TButton", command=self._ok).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            self.geometry(f"+{max(0, x)}+{master.winfo_rooty() + 70}")
        except Exception:
            pass
        self.grab_set()
        self.wait_window()

    def _ok(self) -> None:
        sev = {v for v, var in self.sev_vars.items() if var.get()}
        if not sev:
            messagebox.showwarning("Severity", "Select at least one severity.", parent=self)
            return
        if not self.hb_v.get().strip():
            messagebox.showwarning("Filter", "Choose the heartbeat filter.", parent=self)
            return
        kind = self.win_v.get()
        try:
            hours = max(1, int(self.hours_v.get()))
        except (tk.TclError, ValueError):
            hours = 12
        if kind == "custom":
            since, until = _parse_dt(self.from_v.get()), _parse_dt(self.to_v.get())
            if not since or not until or since >= until:
                messagebox.showwarning("Time", "Enter From / To as day/month/year hour:minute, From before To.",
                                       parent=self)
                return
        else:
            since, until = shift_window(kind, hours=hours, start=getattr(self._hs, "shift_start", "06:30"))
        start = getattr(self._hs, "shift_start", "06:30")
        title = {"previous_shift": f"Last shift ({shift_name(since, start)})",
                 "current_shift": f"Current shift ({shift_name(since, start)})",
                 "last_hours": f"Last {hours} hours"}.get(kind, "Custom window")
        self.result = dict(hb_filter=self.hb_v.get().strip(), hb_view=self.hbv_v.get().strip(),
                           ref_filter=self.ref_v.get().strip(), ref_view=self.refv_v.get().strip(),
                           since=since, until=until, severities=sev, window=kind, hours=hours, title=title)
        self.destroy()


class HeartbeatWindow(tk.Toplevel):
    """THE result = site numbers: heartbeat in the shift, not in the 3G filter, no TT, each site once."""

    def __init__(self, master, rep: HeartbeatReport, on_pdf, on_rerun):
        super().__init__(master)
        self.rep = rep
        self.title("Heartbeat task - result")
        self.configure(bg=T.WHITE)
        self.geometry("1040x700")
        self.minsize(820, 520)
        head = tk.Frame(self, bg=T.RED)
        head.pack(fill="x")
        tk.Label(head, text=f"💓  Heartbeat task · {rep.title or 'Shift'}", bg=T.RED, fg=T.WHITE,
                 font=(T.FONT, 15, "bold")).pack(anchor="w", padx=16, pady=(10, 0))
        sev = ", ".join(SEV_NAMES[v] for v in sorted(rep.severities, reverse=True))
        tk.Label(head, text=f"{rep.since:%d/%m/%Y %H:%M}  →  {rep.until:%d/%m/%Y %H:%M}  ({clock.label()})   ·   "
                            f"{sev}   ·   {rep.hb_filter.split(':')[0]}  vs  {rep.ref_filter.split(':')[0]}",
                 bg=T.RED, fg=T.RED_TINT, font=(T.FONT, 9)).pack(anchor="w", padx=16, pady=(0, 10))

        bar = tk.Frame(self, bg=T.WHITE)
        bar.pack(side="bottom", fill="x", padx=12, pady=10)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="⟳ Run again", command=lambda: (self.destroy(), on_rerun())).pack(side="right", padx=6)
        ttk.Button(bar, text="Copy table", command=self._copy_table).pack(side="right", padx=6)
        ttk.Button(bar, text="Copy site numbers", command=self._copy_sites).pack(side="right", padx=6)
        ttk.Button(bar, text="📄  Save PDF", style="Primary.TButton", command=lambda: on_pdf(rep)).pack(
            side="right", padx=6)

        top = tk.Frame(self, bg=T.WHITE)
        top.pack(fill="x", padx=12, pady=(10, 4))
        big = tk.Frame(top, bg=T.WHITE, highlightthickness=1, highlightbackground=T.GREY_2)
        big.pack(side="left", padx=(0, 10))
        tk.Frame(big, bg=T.RED, width=6).pack(side="left", fill="y")
        bx = tk.Frame(big, bg=T.WHITE)
        bx.pack(side="left", padx=(12, 18), pady=6)
        tk.Label(bx, text=str(len(rep.sites_to_check)), bg=T.WHITE, fg=T.RED, font=(T.FONT, 26, "bold")).pack(anchor="w")
        tk.Label(bx, text="Sites to check in ENM\n(heartbeat · not in 3G filter · no TT)", bg=T.WHITE, fg=T.GREY_9,
                 font=(T.FONT, 9), justify="left").pack(anchor="w")
        for val, lab in ((len(rep.with_tt), "Already have a TT"), (len(rep.excluded), "Also in 3G filter"),
                         (f"{rep.in_window}/{rep.read_alarms}", "Heartbeat alarms in shift / read")):
            c = tk.Frame(top, bg=T.WHITE, highlightthickness=1, highlightbackground=T.GREY_2)
            c.pack(side="left", padx=(0, 8))
            tk.Frame(c, bg=T.GREY_6, width=4).pack(side="left", fill="y")
            b2 = tk.Frame(c, bg=T.WHITE)
            b2.pack(side="left", padx=(10, 14), pady=6)
            tk.Label(b2, text=str(val), bg=T.WHITE, font=(T.FONT, 15, "bold")).pack(anchor="w")
            tk.Label(b2, text=lab, bg=T.WHITE, fg=T.GREY_6, font=(T.FONT, 9)).pack(anchor="w")

        nb = ttk.Notebook(self, style="Inner.TNotebook")
        nb.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        main = self._table(nb, rep.sites_to_check, [("site", "SiteID", 110), ("last", "Last heartbeat", 140),
                                                    ("cnt", "Alarms", 60), ("sev", "Severity", 80),
                                                    ("area", "SubRegion", 170), ("region", "Region", 90)])
        nb.add(main, text=f"  Sites to check ({len(rep.sites_to_check)})  ")
        tt = self._table(nb, rep.with_tt, [("site", "SiteID", 110), ("last", "Last heartbeat", 140),
                                           ("tt", "TT number", 300), ("area", "SubRegion", 170)])
        nb.add(tt, text=f"  Already have TT ({len(rep.with_tt)})  ")
        ex = self._table(nb, rep.excluded, [("site", "SiteID", 110), ("last", "Last heartbeat", 140),
                                            ("ref", "3G filter shows", 420)])
        nb.add(ex, text=f"  Also in 3G filter ({len(rep.excluded)})  ")

    def _table(self, nb, sites, cols):
        fr = ttk.Frame(nb, style="Card.TFrame", padding=6)
        ids = ["n"] + [c for c, _, _ in cols]
        tv = ttk.Treeview(fr, columns=ids, show="headings")
        tv.heading("n", text="#")
        tv.column("n", width=40, anchor="w", stretch=False)
        for c, t, w in cols:
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor="w", stretch=c in ("area", "ref", "tt"))
        tv.tag_configure("odd", background=T.GREY_1)
        get = {"site": lambda s: s.site, "last": lambda s: f"{s.last:%d/%m/%Y %H:%M}", "cnt": lambda s: s.alarms,
               "sev": lambda s: SEV_NAMES.get(s.severity, ""), "area": lambda s: s.area, "region": lambda s: s.region,
               "tt": lambda s: s.tt, "ref": lambda s: s.ref_summary[:90]}
        for i, s in enumerate(sites, 1):
            tv.insert("", "end", tags=("odd",) if i % 2 else (), values=[i] + [get[c](s) for c, _, _ in cols])
        if not sites:
            tv.insert("", "end", values=["", "(none)"])
        sb = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return fr

    def _copy_sites(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(site_list_text(self.rep))
        messagebox.showinfo("Copied", f"{len(self.rep.sites_to_check)} site number(s) copied, one per line.",
                            parent=self)

    def _copy_table(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(to_tsv(self.rep))
        messagebox.showinfo("Copied", "Table copied (paste into Excel).", parent=self)


def default_pdf_path(rep: HeartbeatReport):
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    return HEARTBEAT_DIR / f"Heartbeat_Task_{rep.created:%Y%m%d_%H%M}.pdf"


def ask_pdf_path(parent, rep: HeartbeatReport) -> Optional[str]:
    d = default_pdf_path(rep)
    return filedialog.asksaveasfilename(parent=parent, title="Save heartbeat task PDF",
                                        initialdir=str(d.parent), initialfile=d.name,
                                        defaultextension=".pdf", filetypes=[("PDF", "*.pdf")]) or None


def open_file(path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa
    except Exception:
        pass


__all__ = ["HeartbeatDialog", "HeartbeatWindow", "ask_pdf_path", "open_file", "build_html"]
