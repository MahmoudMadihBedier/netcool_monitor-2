"""Host: the one object a plugin gets. Everything a plugin may do goes through it.

    class MyPlugin:
        def start(self, host):
            host.bus.subscribe(Topics.OUTAGE_ALERT, self.on_outage, owner="my_plugin")
            host.add_menu(MenuItem("My task…", self.run, menu="Tasks"))
            host.add_outage_action(OutageAction("Do X", self.do_x))
            host.settings_section("my_plugin", MySettings)   # dataclass stored in settings.json
        def stop(self): pass

The host never exposes the outage engine or the MOM client for writing: plugins read, advise and act;
only the core decides what an outage is.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Callable, Optional

from .contracts import Advice, Advisor, AlertRequest, MenuItem, OutageAction
from .events import EventBus, Topics

log = logging.getLogger(__name__)


class Host:
    def __init__(self, settings, repo, app_dir: Path, bus: Optional[EventBus] = None):
        self.bus = bus or EventBus()
        self.settings = settings
        self._repo = repo
        self.app_dir = Path(app_dir)
        self.menus: list = []                # MenuItem
        self.outage_actions: list = []       # OutageAction
        self.advisors: list = []             # Advisor plugins
        self.activity_sources: list = []     # ActivitySource plugins
        self.services: dict = {}             # name -> object a plugin offers to others (e.g. "records")
        self.client = None                   # the signed-in MOM client (read-only use: transport, user)
        self.sessions: Callable[[], list] = lambda: []   # app fills: current SessionInfo list
        self.plan_of: Callable[[int], object] = lambda sid: None      # app fills: SessionPlan of a session
        self.purpose_of: Callable[[object], str] = lambda o: ""      # app fills: purpose of an outage's session
        self.outage_events: Callable[[object], list] = lambda o: []  # app fills: alarm rows behind an outage
        self.tt_lookup: Callable[[object], str] = lambda o: ""       # a TT plugin fills: TT number of an outage
        self.on_actions_changed: Callable[[], None] = lambda: None   # app fills: redraw outage buttons
        self.ui = None                       # tk root while the UI runs (None in tests / headless)
        self._lock = threading.Lock()

    # ---------------------------------------------------------- settings
    def settings_section(self, name: str, dataclass_type):
        """A plugin's own settings dataclass, stored under `plugins.<name>` in settings.json."""
        store = self.settings.plugins
        cur = store.get(name)
        names = {f.name for f in fields(dataclass_type)} if is_dataclass(dataclass_type) else set()
        obj = dataclass_type(**{k: v for k, v in (cur or {}).items() if k in names})
        store[name] = obj
        return obj

    def save_settings(self) -> None:
        self._repo.save(self.settings)
        self.bus.publish(Topics.SETTINGS_CHANGED, self.settings)

    # ---------------------------------------------------------- registration
    def add_menu(self, item: MenuItem) -> None:
        with self._lock:
            self.menus.append(item)

    def add_outage_action(self, action: OutageAction) -> None:
        with self._lock:
            self.outage_actions.append(action)
        self.on_actions_changed()

    def register_advisor(self, advisor: Advisor) -> None:
        self.advisors.append(advisor)

    def register_activity_source(self, source) -> None:
        self.activity_sources.append(source)

    def offer(self, name: str, service) -> None:
        """Share an object with other plugins (looked up with host.service(name))."""
        self.services[name] = service

    def service(self, name: str, default=None):
        return self.services.get(name, default)

    # ---------------------------------------------------------- actions
    def alert(self, req: AlertRequest) -> None:
        self.bus.publish(Topics.ALERT, req)

    def advice_for(self, outage, purpose: str = "") -> list:
        out = []
        for a in self.advisors:
            try:
                adv = a.advise(outage, purpose)
                if adv and adv.text:
                    out.append(adv if isinstance(adv, Advice) else Advice(str(adv)))
            except Exception:
                log.exception("advisor %s failed", a)
        return out

    def activities_for(self, sites: list, hub: str = "", area: str = "") -> list:
        out = []
        for s in self.activity_sources:
            try:
                out += list(s.activities_for(sites, hub, area) or [])
            except Exception:
                log.exception("activity source %s failed", s)
        return out

    def run_bg(self, fn: Callable, done: Optional[Callable] = None) -> None:
        """Run fn on a thread; done(result, error) is called on the UI thread."""
        def job():
            try:
                res, err = fn(), None
            except Exception as exc:
                res, err = None, exc
            if done:
                self.bus.ui_queue.put((lambda _p: done(res, err), "bg", None))
        threading.Thread(target=job, daemon=True).start()
