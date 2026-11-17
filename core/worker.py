"""One SessionWorker per tab: its own filter, view, severities, clientId, outage state and alerts.
All workers share the single logged-in NetcoolClient and the Cascaded Sites Report."""
from __future__ import annotations

from . import clock
import logging
import queue
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from . import messages as m
from .cascade import CascadeStore
from .columns import resolve as resolve_columns
from .config import Settings
from .history import AlertHistory
from .models import GroupStatus
from .netcool_api import (AuthError, NetcoolClient, NetcoolError, NetworkError, ServerError,
                          new_client_id)
from .outage import DataCheck, OutageEngine, OutageTracker
from .parser import EventParser
from kernel import OutageAlert, OutageRef, Refresh, Topics
from .session_plan import SessionPlan

log = logging.getLogger(__name__)
_LOG_LEVELS = {"WARN": logging.WARNING, "ALERT": logging.WARNING, "OK": logging.WARNING, "ERROR": logging.ERROR}


class SessionWorker(threading.Thread):
    def __init__(self, sid: int, name: str, client: NetcoolClient, settings: Settings,
                 filter_id: str, view_id: str, datasource: str, bus: "queue.Queue",
                 history: Optional[AlertHistory] = None, severities: Optional[set] = None,
                 cascade: Optional[CascadeStore] = None, interval: Optional[int] = None,
                 plan: Optional[SessionPlan] = None, host=None):
        super().__init__(daemon=True, name=f"session-{sid}")
        self.sid, self.session_name = sid, name
        self.client, self.bus, self.history, self.cascade = client, bus, history, cascade
        self.plan = plan or SessionPlan()
        self.host = host                         # kernel.Host: events out, advice in (None in tests)
        self.base_rules = replace(settings.rules)     # what the user configured
        self.rules = replace(settings.rules)          # + columns resolved for this view
        self.mon = replace(settings.monitor)
        self.severities: set = set(severities if severities is not None else self.rules.default_severities)
        # requested filter (shown in UI / saved) and the one currently open on the server
        self.filter_id, self.view_id, self.datasource = filter_id, view_id, datasource
        self._active = (filter_id, view_id, datasource)
        self.client_id = new_client_id()
        self._stop_evt = threading.Event()
        self._wake = threading.Event()
        self._pending: Optional[tuple] = None
        self._lock = threading.Lock()
        self._build_rule_objects()
        self.state = "CONNECTING"
        self.last_update: Optional[datetime] = None
        self.total_rows = 0
        self.active_alerts = 0
        self._first = True
        self._failures = 0
        self._blind_alerted = False
        self._column_warned = False
        self._prev_total: Optional[int] = None
        self._zero_polls = 0
        self._last_events: list = []
        self._last_columns: set = set()
        self._labels: dict = {}
        self._rescore = False
        self.next_refresh: Optional[datetime] = None
        self.mom_meta: dict = {}
        self.interval = self._clamp(interval or settings.monitor.poll_seconds)
        self._prev_sig: Optional[dict] = None          # row id -> signature of the previous read
        self._prev_events: dict = {}

    INTERVALS = (10, 15, 20, 30, 60)

    @staticmethod
    def _clamp(sec) -> int:
        try:
            return max(10, min(300, int(sec)))
        except (TypeError, ValueError):
            return 20

    # ------------------------------------------------------------ control
    def _build_rule_objects(self, keep_tracker: bool = False) -> None:
        self.parser = EventParser(self.rules)
        self.engine = OutageEngine(self.rules)
        if keep_tracker and getattr(self, "tracker", None) is not None:
            self.tracker.r = self.rules
            self.tracker.realert = (timedelta(minutes=self.rules.realert_minutes)
                                    if self.rules.realert_minutes > 0 else None)
        else:
            self.tracker = OutageTracker(self.rules)

    def _apply_columns(self, columns: set) -> None:
        """Use the right SiteID / HUB / SubRegion / Region columns for THIS view."""
        eff, notes = resolve_columns(self.base_rules, columns, self._labels)
        if eff != self.rules:
            with self._lock:
                self.rules = eff
                self._build_rule_objects(keep_tracker=True)
            for n in notes:
                self._log("INFO", n)

    def change_filter(self, filter_id: str, view_id: str, datasource: str) -> None:
        with self._lock:
            self._pending = (filter_id, view_id, datasource)
            self.filter_id, self.view_id, self.datasource = filter_id, view_id, datasource
        self._wake.set()

    def set_severities(self, severities: set) -> None:
        """Re-evaluates the last data immediately (no server call) and from then on."""
        with self._lock:
            self.severities = set(severities)
            self._rescore = True
        self._wake.set()

    def set_plan(self, plan: SessionPlan) -> None:
        """New purpose / alarm rules from the user; applied to the data already read, at once."""
        with self._lock:
            self.plan = plan
            self._rescore = True
        self._wake.set()

    def mute(self, key: tuple, hours: float = 2) -> None:
        with self._lock:
            self.tracker.mute(key, hours)

    def apply_settings(self, settings: Settings) -> None:
        with self._lock:
            self.base_rules = replace(settings.rules)
            self.rules = resolve_columns(self.base_rules, self._last_columns, self._labels)[0]
            self.mon = replace(settings.monitor)
            self._build_rule_objects(keep_tracker=True)
            self._rescore = True
        self._wake.set()

    def refresh_now(self) -> None:
        self._wake.set()

    def set_interval(self, seconds: int) -> None:
        """Live refresh period of THIS session; applied at once."""
        with self._lock:
            self.interval = self._clamp(seconds)
            self._new_period = True
        self._wake.set()

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    @property
    def stopped(self) -> bool:
        return self._stop_evt.is_set()

    # ------------------------------------------------------------ helpers
    def _post(self, msg) -> None:
        self.bus.put(msg)

    def _log(self, level: str, text: str) -> None:
        log.log(_LOG_LEVELS.get(level, logging.INFO), "[%s] %s", self.session_name, text)
        self._post(m.LogLine(self.sid, level, text))

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self._post(m.StateChanged(self.sid, state, detail))

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        self._set_state("CONNECTING", "Loading filter data")
        self._log("INFO", f"Filter: {self.filter_id}  |  View: {self.view_id}")
        import time as _t
        next_poll = 0.0
        while not self._stop_evt.is_set():
            self._apply_pending()
            period = self.interval
            wait = period
            with self._lock:
                rescore_only, self._rescore = self._rescore and self.last_update is not None, False
                if getattr(self, "_new_period", False):     # interval changed: next read at the new pace
                    self._new_period = False
                    next_poll = min(next_poll, _t.monotonic() + period)
            try:
                if rescore_only and self._last_events and not self._first and _t.monotonic() < next_poll:
                    self._evaluate(self._last_events, self.total_rows, self._last_columns, fresh=False)
                else:
                    next_poll = _t.monotonic() + period          # fixed clock: every N s from START
                    self.next_refresh = clock.now() + timedelta(seconds=period)
                    self._poll()
                wait = max(1.0, next_poll - _t.monotonic())
            except AuthError as exc:
                self._failure(f"Login rejected: {exc}")
            except ServerError as exc:
                self._failure(str(exc))
                self._reopen()
                wait = 15
            except NetworkError as exc:
                self._failure(f"Network: {exc}")
                wait = 15
            except NetcoolError as exc:
                self._failure(str(exc))
                wait = 20
            except Exception as exc:  # never let the thread die during a shift
                log.exception("Unexpected error in %s", self.session_name)
                self._failure(f"Unexpected error: {exc}")
            self._wake.wait(wait)
            self._wake.clear()
        self.client.release(self.client_id, *self._active)
        self._set_state("STOPPED", "Session closed")

    def _reopen(self) -> None:
        try:
            self.client.release(self.client_id, *self._active)
        except Exception:
            pass
        self.client_id = new_client_id()
        self._first = True

    def _apply_pending(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if not pending:
            return
        self.client.release(self.client_id, *self._active)
        self._active = pending
        self.client_id = new_client_id()
        self.tracker.reset()
        self._labels, self._last_events = {}, []
        self._prev_sig, self._prev_events = None, {}
        self._first, self._column_warned, self._prev_total = True, False, None
        self._set_state("CONNECTING", "Switching filter")
        self._log("INFO", f"Filter changed -> {pending[0]}  |  View: {pending[1]}")

    def _poll(self) -> None:
        """Every refresh reads a NEW server dataset with clearCache, exactly like the first read.
        MOM's server keeps the rows of a dataset cached: re-reading the same clientId returns the first
        snapshot again, so the live grid would never change."""
        f_id, v_id, ds = self._active
        cid = new_client_id()
        try:
            res = self.client.fetch_events(cid, f_id, v_id, ds, page_size=self.mon.page_size, first=True)
        except Exception:
            self.client.release(cid, f_id, v_id, ds)       # never leave a half-open dataset on the server
            raise
        old = None if self._first else self.client_id     # first time: nothing was opened before
        self.client_id, self._first = cid, False
        try:
            self._use_result(res)
        finally:
            if old:                                        # the previous dataset is no longer needed
                self.client.release(old, f_id, v_id, ds)

    def _use_result(self, res) -> None:
        if res.labels:
            self._labels = res.labels
        self._apply_columns(res.columns)
        self.mom_meta = dict(res.meta or {})
        self._complete = res.complete
        events = self.parser.parse(res.rows)
        if self._failures >= self.mon.blind_after_failures and self._blind_alerted:
            self._post(m.AlertCleared(self.sid, "Monitoring restored"))
            self._log("OK", "Data flowing again - monitoring restored")
        self._failures, self._blind_alerted = 0, False
        self._last_events, self._last_columns = events, res.columns
        first_ref = self._prev_sig is None
        diff = self._diff(events, res.complete)
        self._first_ref = first_ref
        self._evaluate(events, res.total, res.columns, fresh=True, diff=diff)

    @staticmethod
    def _sig(e) -> tuple:
        f = e.fields or {}
        return (e.severity, e.last_at, e.summary, e.clear_time, f.get("StateChange", ""), f.get("Tally", ""),
                f.get("TTNumber", ""), f.get("Acknowledged", ""))

    def _diff(self, events: list, complete: bool) -> tuple:
        """New / changed / removed rows compared with the previous read (the live 'stream')."""
        cur = {}
        for e in events:
            if e.serial and e.serial not in cur:
                cur[e.serial] = self._sig(e)
        prev = self._prev_sig
        if prev is None:
            new, changed, removed = set(), set(), []
        else:
            new = set(cur) - set(prev)
            changed = {k for k in cur.keys() & prev.keys() if cur[k] != prev[k]}
            removed = [self._prev_events[k] for k in prev.keys() - cur.keys() if k in self._prev_events] \
                if complete else []
        if complete or prev is None:          # an incomplete read never becomes the reference
            self._prev_sig = cur
            self._prev_events = {e.serial: e for e in events if e.serial}
        if new or removed:
            def names(es):
                ids = sorted({x.site or x.serial for x in es})
                return ", ".join(ids[:8]) + (f" (+{len(ids) - 8})" if len(ids) > 8 else "")
            parts = []
            if new:
                parts.append(f"+{len(new)} new: " + names([e for e in events if e.serial in new]))
            if removed:
                parts.append(f"-{len(removed)} gone: " + names(removed))
            self._log("INFO", "   ".join(parts))
        return new, changed, removed

    # ------------------------------------------------------------ evaluation
    def _evaluate(self, events: list, total: int, columns: set, fresh: bool, diff=None) -> None:
        now = clock.now()
        with self._lock:
            sev = set(self.severities)
            engine, tracker = self.engine, self.tracker
        report = self.cascade.report if self.cascade else None
        result = engine.evaluate(events, sev, report, now, columns)
        checks = list(result.checks)
        plan = self.plan
        if not plan.fo_rules:                   # this session is not about down-site outages
            result.outages = []
            checks = [c for c in checks if "HUB" not in c.text and "Cascaded" not in c.text]
        if self.host is not None:               # plugins may ADD advice; they never change the outage
            for o in result.outages:
                if o.level != "WATCH":
                    adv = self.host.advice_for(o, plan.purpose)
                    o.ai = {"text": adv[0].text, "p": adv[0].score} if adv else {}

        # ---- validation of the data itself (protect against false clears / false alarms)
        suspicious = False
        if fresh:
            prev = self._prev_total
            if prev is not None and prev >= 20 and total < prev * 0.2:
                suspicious = True
                checks.insert(0, DataCheck("ERROR", f"Rows dropped suddenly from {prev} to {total}. "
                                                    "MOM may be reloading - outages are kept until the "
                                                    "next refresh confirms."))
                self._log("WARN", f"Sudden drop {prev} -> {total} rows: holding outage state")
            if not getattr(self, "_complete", True):
                suspicious = True
                checks.insert(0, DataCheck("WARN", "MOM changed the list while it was being read - some rows "
                                                   "may be missing this minute; outages are kept and the "
                                                   "next refresh re-reads everything."))
            self._zero_polls = self._zero_polls + 1 if total == 0 else 0
            if self._zero_polls >= 3:
                checks.insert(0, DataCheck("WARN", f"The filter returned 0 rows {self._zero_polls} times in a "
                                                   "row - is the filter correct / is MOM updating?"))
            self._prev_total = total
        if any(c.level == "ERROR" and "view has no" in c.text for c in checks):
            if not self._column_warned:
                self._log("WARN", next(c.text for c in checks if "view has no" in c.text))
                self._column_warned = True
            self._set_state("WARNING", "View lacks needed columns")
        else:
            self._set_state("MONITORING", f"{total} rows")

        self.last_update, self.total_rows = now, total
        if fresh and self.host is not None:
            self.host.bus.publish(Topics.REFRESH, Refresh(
                sid=self.sid, session=self.session_name, filter_id=self._active[0], view_id=self._active[1],
                purpose=plan.purpose, events=events, outages=result.outages, severities=sev,
                new_ids=set(diff[0]) if diff else set(), changed_ids=set(diff[1]) if diff else set(),
                removed=list(diff[2]) if diff else [], first=getattr(self, "_first_ref", True), at=now))
        with self._lock:          # identities first, so the screen, popups and mute agree
            fire, cleared = tracker.update(result.outages, now, hold_clears=suspicious)
            tracker.keys_for(result.outages)
        self.active_alerts = tracker.active_count
        groups = [GroupStatus(hub=o.hub or o.foc or o.node, area=o.area, region=o.region,
                              sites_in_window=o.site_ids, total_sites=o.count,
                              triggered=o.level != "WATCH", start=o.start) for o in result.outages]
        self._post(m.Snapshot(self.sid, now, total, result.used_rows, groups, events,
                              outages=result.outages, checks=checks, sev_counts=result.sev_counts,
                              physical_down=result.physical_down, meta=dict(self.mom_meta),
                              next_refresh=self.next_refresh, complete=getattr(self, "_complete", True),
                              new_ids=set(diff[0]) if diff else set(), changed_ids=set(diff[1]) if diff else set(),
                              removed=list(diff[2]) if diff else [], fresh=fresh, interval=self.interval))
        for o, reason in fire:
            title = f"{reason}: {o.title}"
            body = self._body(o)
            self._log("ALERT", f"{title} -> {', '.join(o.site_ids)}")
            self._post(m.AlertRaised(self.sid, title, body, reason, o))
            if self.history:
                self.history.record(self.session_name, self._active[0], reason, o)
            if self.host is not None:
                self.host.bus.publish(Topics.OUTAGE_ALERT, OutageAlert(reason, o, self.sid, self.session_name,
                                                                         plan.purpose, events))
        for key in cleared:
            self._log("OK", f"Cleared: {' '.join(str(k) for k in key if k)} - no longer down")
            self._post(m.AlertCleared(self.sid, " ".join(str(k) for k in key if k)))
            if self.host is not None:
                self.host.bus.publish(Topics.OUTAGE_CLEARED, OutageRef(key, " ".join(str(k) for k in key if k),
                                                                          self.sid, self.session_name))

    def _body(self, o) -> str:
        r = self.rules
        lines = ([o.ai["text"]] if getattr(o, "ai", None) and o.ai.get("text") else []) + [
                 f"{o.count} physical sites  ·  level {o.level}"
                 + (f" ({o.color})" if o.color else "") + f"  ·  confidence {o.confidence}",
                 f"First of chain: {o.foc or '-'}"
                 + ("" if not o.foc else (" (down)" if o.foc_in_mom else " (NOT in MOM list)"))
                 + f"   HUB: {o.hub or '-'}   Node: {o.node or '-'}",
                 f"Area: {o.area or '-'}   Region: {o.region or '-'}   Evidence: {', '.join(o.evidence)}",
                 f"Suggested team: {o.team}  -  {o.team_reason}"]
        if o.fault_time:
            lines.append(f"Actual fault time (5th site): {o.fault_time:%H:%M:%S}   ->   "
                         f"Global TT at {o.tt_due(r.tt_minutes):%H:%M}")
        lines.append("")
        for s in o.sites[:30]:
            mark = "FOC " if s.pid == o.foc else "    "
            lines.append(f"  {mark}{s.pid:<8} {s.first_down:%H:%M:%S}  {'/'.join(sorted(s.techs)):<9}"
                         f" {(s.fault_code or s.summary)[:48]}")
        if o.count > 30:
            lines.append(f"  ... and {o.count - 30} more")
        for n in o.notes:
            lines.append("• " + n)
        for i in o.issues:
            lines.append("⚠ " + i)
        return "\n".join(lines)

    def _failure(self, why: str) -> None:
        self._failures += 1
        self._log("ERROR", f"Read failed ({self._failures}): {why}")
        if self._failures >= self.mon.blind_after_failures:
            self._set_state("BLIND", why[:80])
            if not self._blind_alerted:
                self._blind_alerted = True
                self._post(m.AlertRaised(
                    self.sid, "MONITORING BLIND - no data",
                    f"The tool could not read events {self._failures} times in a row.\n"
                    f"Last error: {why}\n\nCheck the network / VPN. The tool keeps retrying "
                    f"and re-logs in automatically.", "BLIND", None, blind=True))
        else:
            self._set_state("CONNECTING", f"Retrying ({self._failures})")
