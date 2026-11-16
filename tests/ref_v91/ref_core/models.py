"""Plain data objects shared by every layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class FilterInfo:
    id: str            # e.g. "Copyof3G_Marco:group:Tech-SMC Cairo Access Service Front Desk"
    label: str         # e.g. "Copyof3G_Marco (group ...)"
    name: str
    type: str          # global / user / group
    datasource: str    # e.g. VFEP_A
    default_view: str  # view name the filter is normally shown with ("" if none)


@dataclass(frozen=True)
class ViewInfo:
    id: str            # e.g. "3g_H_easymonitoring:global"
    label: str
    name: str


@dataclass
class NetcoolEvent:
    serial: str
    site: str
    hub_raw: str
    area: str
    region: str
    node: str
    summary: str
    severity: int
    severity_label: str
    down_at: Optional[datetime]
    last_at: Optional[datetime]
    clear_time: str
    fields: dict = field(default_factory=dict)  # every column id -> display value
    bg: str = ""                                # MOM row colour (background-color)
    fg: str = ""


@dataclass
class FetchResult:
    rows: list          # raw REST items (unique)
    total: int
    columns: set        # column ids present in the returned rows
    labels: dict = field(default_factory=dict)   # column id -> label of the view (on first read)
    meta: dict = field(default_factory=dict)     # MOM summary: lastUpdate, inserted, deleted, ...
    complete: bool = True                         # every row of the filter was read


@dataclass
class Incident:
    """>= N distinct sites, same hub, same area, down within the time window."""
    hub: str
    area: str
    region: str
    sites: list          # list[(site, datetime, summary)] sorted by time
    start: datetime
    end: datetime

    @property
    def key(self) -> tuple:
        return (self.hub, self.area)

    @property
    def site_ids(self) -> list:
        return [s for s, _, _ in self.sites]


@dataclass
class GroupStatus:
    """One row of the per-session 'hub watch' table."""
    hub: str
    area: str
    region: str
    sites_in_window: list
    total_sites: int
    triggered: bool
    start: Optional[datetime] = None
