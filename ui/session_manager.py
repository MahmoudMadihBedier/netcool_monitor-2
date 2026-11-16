"""Session lifecycle: create, change filter, close, restore, save - one SessionWorker + SessionView each."""
from __future__ import annotations

import logging
from typing import Optional

from core.session_plan import SessionPlan, is_default_filter
from core.worker import SessionWorker
from kernel import SessionInfo, Topics
from . import theme as T
from .dialogs import FilterPickerDialog
from .purpose_dialog import PurposeDialog
from .session_view import SessionView
from .widgets import Toast

log = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, app):
        self.app = app
        self.views: dict = {}           # sid -> SessionView
        self._next_id = 1

    # ---------------------------------------------------------- queries (used by the host)
    def infos(self) -> list:
        return [SessionInfo(sid, v.worker.session_name, v.worker.filter_id, v.worker.view_id, v.worker.plan.purpose)
                for sid, v in self.views.items()]

    def plan_of(self, sid: int):
        v = self.views.get(sid)
        return v.worker.plan if v else None

    def purpose_of(self, o) -> str:
        for v in self.views.values():
            if any(x.key == o.key for x in v.outages):
                return v.worker.plan.purpose
        return ""

    def events_of(self, o) -> list:
        out = []
        for v in self.views.values():
            if any(x.key == o.key for x in v.outages):
                out += v._last_events
        return out

    def current(self) -> Optional[SessionView]:
        name = self.app.shell.current_page
        return next((v for v in self.views.values() if v.page_name == name), None)

    def _ai_guess(self, view) -> str:
        ai = self.app.host.service("ai")
        if not ai or not view._last_events:
            return ""
        sample = " ".join(e.summary for e in view._last_events[:40])
        return ai.guess_purpose(view.worker.filter_id, view.worker.view_id, sample)[0]

    # ---------------------------------------------------------- plan
    def ask_plan(self, filter_id: str, view_id: str, current=None) -> SessionPlan:
        """2G / 3G: FO defaults, no question. Other filters: what to monitor + what must alarm."""
        if current is None and is_default_filter(filter_id):
            return SessionPlan()
        ai = self.app.host.service("ai")
        guess = ai.guess_purpose(filter_id, view_id) if ai else ("", 0.0, "")
        plan = PurposeDialog(self.app, filter_id, guess, current).run()
        if plan is None:
            return current or SessionPlan()
        if ai:
            ai.learn_purpose(filter_id, view_id, plan.purpose, plan.note)
        return plan

    def edit_plan(self, sid: int) -> None:
        v = self.views.get(sid)
        if not v:
            return
        v.worker.set_plan(self.ask_plan(v.worker.filter_id, v.worker.view_id, v.worker.plan))
        v.update_filter_labels()
        self.save()

    # ---------------------------------------------------------- lifecycle
    def new(self) -> None:
        app = self.app
        if not app.client:
            return
        dlg = FilterPickerDialog(app, app.filters, app.views_list, app.settings, f"Session {self._next_id}")
        if dlg.result:
            r = dlg.result
            plan = self.ask_plan(r["filter"].id, r["view"])
            self.start(r["name"], r["filter"].id, r["view"], r["filter"].datasource, plan=plan)

    def start(self, name: str, filter_id: str, view_id: str, datasource: str = "", severities=None,
              interval=None, plan=None) -> SessionView:
        app = self.app
        sid, self._next_id = self._next_id, self._next_id + 1
        if severities is None:
            severities = app.settings.rules.default_severities
        worker = SessionWorker(sid, name, app.client, app.settings, filter_id, view_id,
                               datasource or app.settings.server.datasource, app.bus, app.history,
                               severities=set(severities), cascade=app.cascade,
                               interval=interval or app.settings.monitor.poll_seconds,
                               plan=plan or SessionPlan(), host=app.host)
        view = SessionView(app.shell.pages, worker,
                           on_change_filter=lambda: self.change_filter(sid),
                           on_close=lambda: self.close(sid),
                           on_state=app.on_session_state,
                           on_severities=lambda v: self.save(),
                           on_open_outage=app.open_outage, actions=app.actions,
                           on_plan=lambda: self.edit_plan(sid))
        view._on_interval = lambda v: self.save()
        view.tt_lookup = lambda o: (f"TT {app.host.tt_lookup(o)}" if app.host.tt_lookup(o) else "")
        view.page_name = f"session:{sid}"
        view.purpose_guess = lambda: self._ai_guess(view)
        self.views[sid] = view
        app.shell.add_page(view.page_name, view, icon="◌")
        app.shell.rename_page(view.page_name, f"◌  {name}")
        app.shell.show_page(view.page_name)
        worker.start()
        app.bus_publish(Topics.SESSION_STARTED, SessionInfo(sid, name, filter_id, view_id, worker.plan.purpose))
        self.save()
        app.update_power()
        app.overview.refresh()
        return view

    def change_filter(self, sid: int) -> None:
        app, v = self.app, self.views.get(sid)
        if not v:
            return
        dlg = FilterPickerDialog(app, app.filters, app.views_list, app.settings, v.worker.session_name,
                                 current=(v.worker.filter_id, v.worker.view_id), ask_name=False)
        if dlg.result:
            f = dlg.result["filter"]
            v.worker.change_filter(f.id, dlg.result["view"], f.datasource)
            if not is_default_filter(f.id):
                v.worker.set_plan(self.ask_plan(f.id, dlg.result["view"], v.worker.plan))
            v.update_filter_labels()
            app.alerts.drop_session(sid)
            self.save()

    def close(self, sid: int) -> None:
        app, v = self.app, self.views.pop(sid, None)
        if not v:
            return
        v.worker.stop()
        app.alerts.drop_session(sid)
        app.bus_publish(Topics.SESSION_STOPPED, SessionInfo(sid, v.worker.session_name, v.worker.filter_id,
                                                           v.worker.view_id, v.worker.plan.purpose))
        app.shell.remove_page(v.page_name)
        v.destroy()
        self.save()
        app.update_power()
        app.overview.refresh()

    def restore(self) -> None:
        app = self.app
        if not app.client:
            return
        known = {f.id: f for f in app.filters}
        for s in list(app.settings.last_sessions):
            if any(v.worker.filter_id == s.get("filter_id") and v.worker.view_id == s.get("view_id")
                   for v in self.views.values()):
                continue
            f = known.get(s.get("filter_id"))
            if f is None:
                Toast.show(app, f"Filter '{s.get('filter_id')}' no longer exists on the server - skipped.", T.RED_DARK)
                continue
            self.start(s.get("name") or "Session", s.get("filter_id"), s.get("view_id"), f.datasource,
                       s.get("severities"), s.get("interval"),
                       SessionPlan.from_dict(s["plan"]) if s.get("plan") else None)

    def save(self) -> None:
        app = self.app
        app.settings.last_sessions = [dict(name=v.worker.session_name, filter_id=v.worker.filter_id,
                                           view_id=v.worker.view_id,
                                           severities=sorted(v.worker.severities, reverse=True),
                                           interval=v.worker.interval, plan=v.worker.plan.to_dict())
                                      for v in self.views.values()]
        app.repo.save(app.settings)

    def stop_all(self, save: bool) -> None:
        if save:
            self.save()
        for sid in list(self.views):
            v = self.views.pop(sid)
            v.worker.stop()
            self.app.bus_publish(Topics.SESSION_STOPPED, SessionInfo(sid, v.worker.session_name, v.worker.filter_id,
                                                                     v.worker.view_id, v.worker.plan.purpose))
        self.app.alerts.ack_all()
