"""Turns WebGUI REST items into NetcoolEvent objects."""
from __future__ import annotations

from . import clock
from datetime import datetime
from typing import Optional

from .config import RuleSettings
from .models import NetcoolEvent

EMPTY = {"", "NULL", "NONE", "-", "N/A"}


def _to_dt(v) -> Optional[datetime]:
    if isinstance(v, (int, float)) and v > 0:
        return clock.from_epoch(v / 1000.0)
    return None


def mom_time(dt: datetime) -> str:
    """MOM display format: '5/12/26, 3:03 PM'."""
    h = dt.hour % 12 or 12
    return f"{dt.month}/{dt.day}/{dt:%y}, {h}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


class EventParser:
    def __init__(self, rules: RuleSettings):
        self.r = rules

    def parse_item(self, item: dict) -> NetcoolEvent:
        vals, disp = {}, {}
        for p in item.get("properties", []):
            pid = p.get("id")
            vals[pid] = p.get("value")
            d = p.get("displayValue")
            if d is None:
                v = p.get("value")
                if p.get("valueType") == "datetime" and isinstance(v, (int, float)) and v > 0:
                    d = mom_time(_to_dt(v))
                else:
                    d = v.get("label") if isinstance(v, dict) else ("" if v is None else str(v))
            disp[pid] = d

        sev = vals.get("Severity")
        if isinstance(sev, dict):
            sev_val, sev_label = int(sev.get("value", -1)), sev.get("label") or sev.get("tooltip") or ""
        else:
            sev_val, sev_label = (int(sev) if isinstance(sev, (int, float)) else -1), str(sev or "")

        def text(fid: str) -> str:
            v = disp.get(fid, "") if fid else ""
            return "" if v is None else str(v).strip()

        down = _to_dt(vals.get(self.r.time_field)) or _to_dt(vals.get("FirstOccurrence")) \
            or _to_dt(vals.get("EventTime"))
        return NetcoolEvent(
            serial=str(item.get("id") or vals.get("Serial") or vals.get("Identifier") or ""),   # MOM row id
            site=text(self.r.site_field),
            hub_raw=text(self.r.hub_field),
            area=text(self.r.area_field),
            region=text(self.r.region_field),
            node=text("Node"),
            summary=text("Summary"),
            severity=sev_val,
            severity_label=sev_label,
            down_at=down,
            last_at=_to_dt(vals.get("LastOccurrence")),
            clear_time=text("ClearTime"),
            fields=disp,
            bg=item.get("background-color") or "",
            fg=item.get("foreground-color") or "",
        )

    def parse(self, items: list) -> list:
        return [self.parse_item(it) for it in items]

    def hub_key(self, raw: str) -> str:
        raw = (raw or "").strip()
        if raw.upper() in EMPTY:
            return ""
        if self.r.hub_match == "code":
            return raw.split()[0]
        return raw
