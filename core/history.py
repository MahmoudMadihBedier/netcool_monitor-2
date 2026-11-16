"""Shift alert history: one CSV per day in ~/.netcool_monitor/alert_history/."""
from __future__ import annotations

from . import clock
import csv
import logging
import threading

from .config import ALERT_HISTORY_DIR

log = logging.getLogger(__name__)


class AlertHistory:
    def __init__(self, folder=ALERT_HISTORY_DIR):
        self.folder = folder
        self._lock = threading.Lock()

    def record(self, session: str, filter_id: str, reason: str, inc) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / f"alerts_{clock.now():%Y-%m-%d}.csv"
            with self._lock:
                new = not path.exists()
                with path.open("a", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(["time", "session", "filter", "reason", "hub", "area",
                                    "region", "sites_count", "sites", "first_down", "last_down"])
                    w.writerow([f"{clock.now():%Y-%m-%d %H:%M:%S}", session, filter_id, reason,
                                inc.hub, inc.area, inc.region, len(inc.sites),
                                " ".join(inc.site_ids), f"{inc.start:%H:%M:%S}", f"{inc.end:%H:%M:%S}"])
        except Exception:
            log.exception("Could not write alert history")
