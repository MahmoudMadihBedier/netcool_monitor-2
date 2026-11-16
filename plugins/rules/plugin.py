"""Rules plugin: raises the session's own alarms (new rows / words / row count) from each refresh."""
from __future__ import annotations

from kernel import AlertRequest, Refresh, Topics
from ui import theme as T
from .rules import RuleChecker


class RulesPlugin:
    name = "rules"

    def start(self, host) -> None:
        self.host = host
        self._checkers: dict = {}            # sid -> RuleChecker (remembers the row-count state)
        host.bus.subscribe(Topics.REFRESH, self.on_refresh, owner=self.name)
        host.bus.subscribe(Topics.SESSION_STOPPED, lambda s: self._checkers.pop(s.sid, None), owner=self.name)

    def stop(self) -> None:
        self._checkers.clear()

    def on_refresh(self, r: Refresh) -> None:
        plan = self.host.plan_of(r.sid)
        if plan is None:
            return
        c = self._checkers.get(r.sid)
        if c is None or c.plan is not plan:
            c = self._checkers[r.sid] = RuleChecker(plan)
        for key, title, body in c.check(r.events, r.new_ids, r.severities):
            self.host.alert(AlertRequest((r.sid, f"RULE:{key}"), f"{r.session}: {title}", body,
                                         session=f"{r.session}  ·  {r.filter_id}", color=T.AQUA))
