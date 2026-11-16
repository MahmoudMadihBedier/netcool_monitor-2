"""Messages workers put on the bus; only the UI thread consumes them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import clock
from typing import Optional


@dataclass
class StateChanged:
    sid: int
    state: str          # CONNECTING | MONITORING | WARNING | BLIND | STOPPED
    detail: str = ""


@dataclass
class Snapshot:
    sid: int
    at: datetime
    total_rows: int
    relevant_rows: int
    groups: list        # list[GroupStatus] (kept for compatibility)
    events: list        # list[NetcoolEvent] (for the live table)
    outages: list = field(default_factory=list)     # list[Outage], all levels
    checks: list = field(default_factory=list)      # list[DataCheck]
    sev_counts: dict = field(default_factory=dict)  # severity -> rows
    physical_down: int = 0
    meta: dict = field(default_factory=dict)        # MOM summary of this refresh
    next_refresh: Optional[datetime] = None
    complete: bool = True
    new_ids: set = field(default_factory=set)        # rows that appeared since the previous refresh
    changed_ids: set = field(default_factory=set)    # rows MOM updated (severity, tally, last occurrence...)
    removed: list = field(default_factory=list)      # rows gone from the filter (cleared / deleted)
    fresh: bool = True                                # False = re-scored from the last read (no server call)
    interval: int = 0


@dataclass
class LogLine:
    sid: int
    level: str
    text: str


@dataclass
class AlertRaised:
    sid: int
    title: str
    body: str
    reason: str
    incident: object = None
    blind: bool = False


@dataclass
class AlertCleared:
    sid: int
    title: str


@dataclass
class ServerStatus:
    """Global (sid = 0): login / keep-alive state shared by all sessions."""
    ok: bool
    detail: str
    seconds_left: int = -1
    at: datetime = field(default_factory=clock.now)
