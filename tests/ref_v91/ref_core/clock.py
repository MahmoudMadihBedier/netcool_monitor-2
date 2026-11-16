"""One clock for the whole tool, in the time zone chosen in Settings.

Only two zones are allowed (both without daylight saving, so fixed offsets are exact and no extra
package is needed on Windows):
    Asia/Riyadh          UTC+3
    Africa/Johannesburg  UTC+2
MOM alarm times, 'now', countdowns, the 15th-minute due time and the shift windows all use this
clock, so comparisons stay correct and the times shown match MOM's display (the same zone is sent
to MOM as its 'timezone' parameter).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ZONES = {"Asia/Riyadh": 3, "Africa/Johannesburg": 2}
DEFAULT_ZONE = "Asia/Riyadh"

_name = DEFAULT_ZONE
_offset = timedelta(hours=ZONES[DEFAULT_ZONE])


def set_zone(name: str) -> str:
    """Select the zone; anything else falls back to the default. Returns the zone in use."""
    global _name, _offset
    if name not in ZONES:
        name = DEFAULT_ZONE
    _name, _offset = name, timedelta(hours=ZONES[name])
    return _name


def zone() -> str:
    return _name


def label() -> str:
    return f"UTC+{ZONES[_name]} ({_name})"


def now() -> datetime:
    """Current time in the selected zone (naive datetime)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + _offset


def from_epoch(seconds: float) -> datetime:
    """Unix seconds -> naive datetime in the selected zone."""
    return datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=None) + _offset
