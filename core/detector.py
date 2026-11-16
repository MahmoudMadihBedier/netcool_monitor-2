"""The outage rule and alert de-duplication.

Rule: alert when at least `min_sites` DIFFERENT sites with the SAME hub and the
SAME area went down within `window_minutes` of each other.
"""
from __future__ import annotations

from . import clock
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from .config import RuleSettings
from .models import GroupStatus, Incident, NetcoolEvent
from .parser import EMPTY, EventParser


class OutageDetector:
    def __init__(self, rules: RuleSettings):
        self.r = rules
        self._parser = EventParser(rules)
        self._inc = re.compile(rules.include_summary_regex, re.I) if rules.include_summary_regex else None
        self._exc = re.compile(rules.exclude_summary_regex, re.I) if rules.exclude_summary_regex else None

    def relevant(self, e: NetcoolEvent) -> bool:
        if not e.site or e.site.upper() in EMPTY or e.down_at is None:
            return False
        if e.severity in (self.r.ignore_severities or []):
            return False
        if self.r.ignore_cleared and e.clear_time and e.clear_time.upper() not in EMPTY:
            return False
        if self._inc and not self._inc.search(e.summary):
            return False
        if self._exc and self._exc.search(e.summary):
            return False
        return True

    def evaluate(self, events: list) -> tuple:
        """Returns (incidents, group_statuses). Group statuses cover every hub+area
        with >= 2 sites in one window, so the operator sees what is building up."""
        window = timedelta(minutes=max(0, self.r.window_minutes))
        groups = defaultdict(list)          # (hub, area) -> [(time, site, summary, region)]
        for e in events:
            if not self.relevant(e):
                continue
            hub = self._parser.hub_key(e.hub_raw)
            if not hub:
                continue
            area = e.area if self.r.area_field else ""
            groups[(hub, area)].append((e.down_at, e.site, e.summary, e.region))

        incidents, statuses = [], []
        for (hub, area), pts in groups.items():
            pts.sort(key=lambda p: p[0])
            best = self._best_window(pts, window)
            sites_total = len({p[1] for p in pts})
            region = next((p[3] for p in pts if p[3]), "")
            triggered = len(best) >= self.r.min_sites
            if len(best) >= 2:
                statuses.append(GroupStatus(hub=hub, area=area, region=region,
                                            sites_in_window=[s for s, _, _ in best],
                                            total_sites=sites_total, triggered=triggered,
                                            start=best[0][1] if best else None))
            if triggered:
                incidents.append(Incident(hub=hub, area=area, region=region, sites=best,
                                          start=best[0][1], end=best[-1][1]))
        statuses.sort(key=lambda g: (not g.triggered, -len(g.sites_in_window), g.hub))
        incidents.sort(key=lambda i: i.start)
        return incidents, statuses

    @staticmethod
    def _best_window(pts: list, window: timedelta) -> list:
        """Sliding window over time-sorted points; returns the window holding the most
        distinct sites (latest wins on ties) as [(site, first_time, summary)]."""
        best: list = []
        j = 0
        for i in range(len(pts)):
            while pts[i][0] - pts[j][0] > window:
                j += 1
            first_seen: dict = {}
            for t, site, summ, _ in pts[j:i + 1]:
                if site not in first_seen:
                    first_seen[site] = (site, t, summ)
            if len(first_seen) >= len(best):
                best = sorted(first_seen.values(), key=lambda x: x[1])
        return best


class AlertTracker:
    """Decides when an incident must pop up: new, grew, or reminder. Remembers clears."""

    def __init__(self, realert_minutes: int):
        self.realert = timedelta(minutes=realert_minutes) if realert_minutes > 0 else None
        self._active: dict = {}  # key -> (alerted_sites:set, last_alert:datetime)

    def update(self, incidents: list, now: Optional[datetime] = None) -> tuple:
        now = now or clock.now()
        fire, seen = [], set()
        for inc in incidents:
            seen.add(inc.key)
            sites = set(inc.site_ids)
            prev = self._active.get(inc.key)
            if prev is None:
                fire.append((inc, "NEW OUTAGE"))
                self._active[inc.key] = (sites, now)
            elif not sites <= prev[0]:
                fire.append((inc, f"MORE SITES DOWN (+{len(sites - prev[0])})"))
                self._active[inc.key] = (prev[0] | sites, now)
            elif self.realert and now - prev[1] >= self.realert:
                fire.append((inc, "STILL DOWN (reminder)"))
                self._active[inc.key] = (prev[0], now)
        cleared = [k for k in self._active if k not in seen]
        for k in cleared:
            del self._active[k]
        return fire, cleared

    def reset(self) -> None:
        self._active.clear()

    @property
    def active_count(self) -> int:
        return len(self._active)
