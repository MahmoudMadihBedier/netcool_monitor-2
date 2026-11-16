"""Payloads carried on the event bus and the interfaces a plugin may implement.

Everything a plugin needs is here and in `kernel.host.Host`; plugins never import `core.*` internals
or other plugins. New fields are only ever ADDED (with defaults), so old plugins keep working.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Protocol, runtime_checkable


# ------------------------------------------------------------------ payloads
@dataclass
class SessionInfo:
    sid: int
    name: str
    filter_id: str
    view_id: str
    purpose: str = ""


@dataclass
class OutageRef:
    """Identity of an outage across sessions (the core's stable key)."""
    key: tuple
    title: str
    sid: int = 0
    session: str = ""


@dataclass
class OutageAlert:
    """The core found / changed an outage. `outage` is core.outage.Outage (read-only for plugins)."""
    reason: str                  # NEW OUTAGE, PRE-OUTAGE, ESCALATED TO OUTAGE, MORE SITES DOWN, 15TH MINUTE ...
    outage: object
    sid: int
    session: str
    purpose: str = ""
    events: list = field(default_factory=list)      # the session's alarm rows at that moment


@dataclass
class Refresh:
    """One fresh read of a session: rows, diffs and the core's outages (published every refresh)."""
    sid: int
    session: str
    filter_id: str
    view_id: str
    purpose: str
    events: list
    outages: list
    severities: set
    new_ids: set = field(default_factory=set)
    changed_ids: set = field(default_factory=set)
    removed: list = field(default_factory=list)
    first: bool = False
    at: Optional[datetime] = None


@dataclass
class Decision:
    """What the engineer decided about an outage."""
    outage: object
    real: bool
    reason: str = ""             # for 'not an outage': PA / power / not same chain / ...
    note: str = ""
    team: str = ""               # for a real outage with a Global TT
    purpose: str = ""
    at: Optional[datetime] = None


@dataclass
class TTOpened:
    outage: object
    team: str
    tt_number: str
    user: str = ""


@dataclass
class AlertRequest:
    """Ask the host for a popup + sound. `key` makes repeats update one window."""
    key: tuple
    title: str
    body: str
    session: str = ""
    color: str = ""
    buttons: list = field(default_factory=list)     # [(text, callback, primary)]
    question: str = ""
    blind: bool = False


@dataclass
class AlertAck:
    key: tuple
    by_user: bool


@dataclass
class Advice:
    """A plugin's opinion shown next to an outage (never changes the core's decision)."""
    text: str
    score: Optional[float] = None    # chance it is a real outage, 0..1
    source: str = ""


@dataclass
class MenuItem:
    label: str
    command: Callable
    menu: str = "Tools"              # Tools | Tasks | View | Help, or a new top-level menu name
    shortcut: str = ""               # e.g. "Ctrl+T"
    toolbar: bool = False            # also a toolbar button
    icon: str = ""                   # emoji / short glyph


@dataclass
class OutageAction:
    """A button shown on outage popups / the outage window."""
    label: str
    command: Callable                # command(outage)
    primary: bool = False


# ------------------------------------------------------------------ interfaces
@runtime_checkable
class Plugin(Protocol):
    """Implemented by every plugin's main class (name set in plugin.json)."""
    def start(self, host) -> None: ...       # subscribe, register menus / actions / settings
    def stop(self) -> None: ...


@runtime_checkable
class Advisor(Protocol):
    """A plugin that gives advice on outages (the AI). The core shows it, nothing more."""
    def advise(self, outage, purpose: str = "") -> Advice: ...


@runtime_checkable
class ActivitySource(Protocol):
    """A plugin that knows about planned / night activities (Outlook, CRQ list ...)."""
    def activities_for(self, sites: list, hub: str, area: str) -> list: ...   # [str] human lines
