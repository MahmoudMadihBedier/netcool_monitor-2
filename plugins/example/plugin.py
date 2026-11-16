"""Example plugin for developers: copy this folder, rename it in plugin.json, set "enabled": true.

It shows the whole plugin API in 30 lines: a setting, a menu item, an outage button, events in and out.
"""
from __future__ import annotations

from dataclasses import dataclass

from kernel import AlertRequest, MenuItem, OutageAction, OutageAlert, Refresh, Topics


@dataclass
class ExampleSettings:
    big_outage: int = 20            # stored in settings.json under plugins.example


class ExamplePlugin:
    name = "example"

    def start(self, host) -> None:
        self.host = host
        self.cfg = host.settings_section("example", ExampleSettings)
        host.bus.subscribe(Topics.OUTAGE_ALERT, self.on_outage, owner=self.name)   # core -> plugin
        host.bus.subscribe(Topics.REFRESH, self.on_refresh, owner=self.name)
        host.add_menu(MenuItem("Example: say hello", self.hello, menu="Tools"))
        host.add_outage_action(OutageAction("Example action", lambda o: self.hello()))

    def stop(self) -> None:
        pass

    def on_outage(self, a: OutageAlert) -> None:
        if a.outage.count >= self.cfg.big_outage:                                    # plugin -> host
            self.host.alert(AlertRequest(("example", a.outage.key), "BIG OUTAGE (example plugin)",
                                         f"{a.outage.count} sites in {a.outage.area}", session=a.session))

    def on_refresh(self, r: Refresh) -> None:
        pass            # r.events (rows), r.new_ids, r.removed, r.outages ... every refresh of every session

    def hello(self) -> None:
        from tkinter import messagebox
        messagebox.showinfo("Example plugin", f"Hello from the example plugin (big_outage = {self.cfg.big_outage})")
