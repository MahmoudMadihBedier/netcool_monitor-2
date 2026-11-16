"""Event bus: how the core and the plugins talk without knowing each other.

* Topics are plain strings (see Topics). Payloads are the dataclasses of `kernel.contracts`.
* `publish` is thread-safe and never raises: a failing subscriber is logged and the others still run.
* Subscribers may ask for delivery on the UI thread (`ui=True`); the host pumps that queue.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Topics:
    """Every topic the kernel defines. Plugins may add their own as 'plugin.<name>.<event>'."""
    SESSION_STARTED = "session.started"      # SessionInfo
    SESSION_STOPPED = "session.stopped"      # SessionInfo
    REFRESH = "monitor.refresh"              # Refresh       (every fresh read: rows, diffs, outages)
    OUTAGE_ALERT = "outage.alert"            # OutageAlert   (NEW / ESCALATED / MORE SITES / TT DUE ...)
    OUTAGE_CLEARED = "outage.cleared"        # OutageRef
    DECISION = "outage.decision"             # Decision      (engineer: real / not an outage, reason, note)
    TT_OPENED = "tt.opened"                  # TTOpened      (Global TT created)
    ALERT = "alert.raise"                    # AlertRequest  (any plugin may ask for a popup + sound)
    ALERT_ACK = "alert.ack"                  # AlertAck
    SERVER_STATUS = "server.status"          # core.messages.ServerStatus
    SETTINGS_CHANGED = "settings.changed"    # Settings
    SHUTDOWN = "app.shutdown"                # None


@dataclass
class _Sub:
    topic: str
    fn: Callable
    ui: bool
    owner: str


class EventBus:
    def __init__(self):
        self._subs: list = []
        self._lock = threading.RLock()
        self.ui_queue: "queue.Queue" = queue.Queue()      # (fn, payload) to run on the UI thread

    def subscribe(self, topic: str, fn: Callable, ui: bool = False, owner: str = "") -> Callable:
        """Returns a function that removes the subscription."""
        sub = _Sub(topic, fn, ui, owner)
        with self._lock:
            self._subs.append(sub)

        def off():
            with self._lock:
                if sub in self._subs:
                    self._subs.remove(sub)
        return off

    def unsubscribe_owner(self, owner: str) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s.owner != owner]

    def publish(self, topic: str, payload=None) -> int:
        """Delivers to every subscriber of the topic (and of '*'). Returns how many were called / queued."""
        with self._lock:
            subs = [s for s in self._subs if s.topic == topic or s.topic == "*"]
        n = 0
        for s in subs:
            n += 1
            if s.ui:
                self.ui_queue.put((s.fn, topic, payload))
                continue
            try:
                s.fn(payload) if s.topic != "*" else s.fn(topic, payload)
            except Exception:
                log.exception("subscriber %s failed on %s", s.owner or s.fn, topic)
        return n

    def pump(self, limit: int = 500) -> int:
        """Run queued UI-thread deliveries. Called by the host from the Tk loop."""
        done = 0
        try:
            while done < limit:
                fn, topic, payload = self.ui_queue.get_nowait()
                done += 1
                try:
                    fn(payload) if topic != "*" else fn(topic, payload)
                except Exception:
                    log.exception("UI subscriber %s failed on %s", fn, topic)
        except queue.Empty:
            pass
        return done

    def subscribers(self, topic: Optional[str] = None) -> int:
        with self._lock:
            return len([s for s in self._subs if topic is None or s.topic == topic])
