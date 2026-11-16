"""Flows plugin: 'Record a flow' (the engineer does a task once in the tool's browser) and 'Run flow for
this outage' (the same steps with the outage's values; the final Submit is left to the engineer)."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from core import clock
from kernel import MenuItem, OutageAction
from ui.modal import _label, _Modal
from .flows import Flow, FlowStore, collect_recording, dedupe, outage_values, replay, start_recording, templatize

log = logging.getLogger(__name__)


class FlowsPlugin:
    name = "flows"

    def start(self, host) -> None:
        self.host = host
        self.store = FlowStore(host.app_dir / "flows")
        self._rec = None                      # (handle, Flow being recorded, values at record time)
        host.offer("flows", self)
        host.add_menu(MenuItem("Record a browser flow…", self.record, menu="Tools", icon="⏺"))
        host.add_menu(MenuItem("Stop recording and save", self.stop_recording, menu="Tools", icon="⏹"))
        host.add_menu(MenuItem("Recorded flows…", self.manage, menu="Tools", icon="▶"))
        host.add_outage_action(OutageAction("▶ Run flow", self.run_for_outage))

    def stop(self) -> None:
        pass

    # ---- helpers
    def _transport(self):
        t = getattr(self.host.client, "t", None) if self.host.client else None
        if t is None or not hasattr(t, "visible_page"):
            messagebox.showinfo("Flows", "Sign in first with the 'browser' connection mode (the recorder uses the "
                                "tool's own browser).", parent=self.host.ui)
            return None
        return t

    # ---- record
    def record(self) -> None:
        t = self._transport()
        if t is None:
            return
        if self._rec:
            messagebox.showinfo("Flows", "A recording is already running - finish it with 'Stop recording'.",
                                parent=self.host.ui)
            return
        name = simpledialog.askstring("Record a flow", "Name of the flow (e.g. 'Global TT on TX'):",
                                      parent=self.host.ui)
        if not name:
            return
        url = simpledialog.askstring("Record a flow", "Start page (URL):",
                                     initialvalue=self.host.settings.remedy.wsdl_url.split("/arsys")[0] + "/arsys/",
                                     parent=self.host.ui)
        if not url:
            return
        self.host.ui.config(cursor="watch")

        def done(handle, err):
            self.host.ui.config(cursor="")
            if err:
                messagebox.showerror("Flows", f"Could not open the browser window:\n{err}", parent=self.host.ui)
                return
            self._rec = (handle, Flow(name=name, start_url=url, created=clock.now().isoformat(timespec="minutes")))
            messagebox.showinfo("Recording", "Do the task in the browser window that opened. Type the outage's real "
                                "values (FOC, sites, fault time, notes) - the tool turns them into placeholders.\n\n"
                                "When finished: Tools > Stop recording and save.", parent=self.host.ui)

        def work():
            h = t.visible_page(url)
            t.on_page(h, start_recording, 60)
            return h
        self.host.run_bg(work, done)

    def stop_recording(self) -> None:
        if not self._rec:
            return
        t = self._transport()
        handle, flow = self._rec
        self._rec = None
        if t is None:
            return
        try:
            steps = t.on_page(handle, collect_recording, 60)
        except Exception as exc:
            messagebox.showerror("Flows", f"Could not read the recording: {exc}", parent=self.host.ui)
            return
        values = self._ask_values_for_templating()
        flow.steps = dedupe(templatize(steps, values))
        if not flow.steps:
            messagebox.showinfo("Flows", "Nothing was recorded.", parent=self.host.ui)
            return
        path = self.store.save(flow)
        messagebox.showinfo("Flows", f"Saved {len(flow.steps)} steps to\n{path}", parent=self.host.ui)

    def _ask_values_for_templating(self) -> dict:
        """Which outage values were typed during the recording (so they become placeholders)."""
        vals = {}
        for key, label in (("foc", "First of chain typed"), ("sites", "Site list typed"),
                           ("fault_time", "Fault time typed"), ("notes", "Notes typed")):
            v = simpledialog.askstring("Placeholders", f"{label} during the recording (leave empty if none):",
                                       parent=self.host.ui)
            if v:
                vals[key] = v
        return vals

    # ---- replay
    def run_for_outage(self, o) -> None:
        t = self._transport()
        if t is None:
            return
        names = self.store.names()
        if not names:
            messagebox.showinfo("Flows", "No recorded flows yet (Tools > Record a browser flow).", parent=self.host.ui)
            return
        dlg = _PickFlow(self.host.ui, names)
        name = dlg.run()
        if not name:
            return
        flow = self.store.load(name)
        if flow is None:
            return
        team = "TX"
        values = outage_values(o, team)
        pw = ""
        if any("{password}" in s.get("value", "") for s in flow.steps):
            pw = simpledialog.askstring("Flow", "Password for this flow (used once, not saved):", show="•",
                                        parent=self.host.ui) or ""

        def work():
            h = t.visible_page(flow.start_url)
            return t.on_page(h, lambda page: replay(page, flow, values, pw), 900)

        def done(n, err):
            if err:
                messagebox.showerror("Flows", f"The flow stopped after an error:\n{err}\n\nFinish it by hand in the "
                                     "browser window.", parent=self.host.ui)
                return
            flow.runs += 1
            self.store.save(flow)
            messagebox.showinfo("Flows", f"{n} step(s) done with this outage's values. The final click (submit) is "
                                "yours: check the page and press it.", parent=self.host.ui)
        self.host.run_bg(work, done)

    def manage(self) -> None:
        _ManageFlows(self.host.ui, self.store).run()


class _PickFlow(_Modal):
    def __init__(self, master, names):
        super().__init__(master, "Run a recorded flow", "The steps are replayed with this outage's values")
        self.v = tk.StringVar(value=names[0])
        for n in names:
            ttk.Radiobutton(self.body, text=n, value=n, variable=self.v, style="Card.TRadiobutton").pack(anchor="w")
        self.buttons("Run")

    def _ok(self):
        self.result = self.v.get()
        self.destroy()


class _ManageFlows(_Modal):
    def __init__(self, master, store: FlowStore):
        super().__init__(master, "Recorded flows", "Stored on this PC in the tool's folder")
        self.store = store
        self.lst = tk.Listbox(self.body, height=8, width=60)
        self.lst.pack(fill="x")
        self.info = _label(self.body, "")
        self.info.pack(anchor="w", pady=6)
        self.lst.bind("<<ListboxSelect>>", self._show)
        row = ttk.Frame(self.body, style="Card.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="Delete", command=self._delete).pack(side="left")
        bar = ttk.Frame(self, style="Card.TFrame", padding=(16, 0, 16, 14))
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self._fill()

    def _fill(self):
        self.lst.delete(0, "end")
        for n in self.store.names():
            self.lst.insert("end", n)

    def _show(self, _=None):
        sel = self.lst.curselection()
        if not sel:
            return
        f = self.store.load(self.lst.get(sel[0]))
        if f:
            lines = [f"{s['action']} {s['selector'][:50]} {s.get('value', '')[:30]}" for s in f.steps[:12]]
            self.info.config(text=f"{len(f.steps)} steps · recorded {f.created} · run {f.runs}x\n" + "\n".join(lines))

    def _delete(self):
        sel = self.lst.curselection()
        if sel and messagebox.askyesno("Delete", "Delete this flow?", parent=self):
            self.store.delete(self.lst.get(sel[0]))
            self._fill()
            self.info.config(text="")
