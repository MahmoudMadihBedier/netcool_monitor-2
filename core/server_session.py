"""Keeps the single shared login alive for the whole shift."""
from __future__ import annotations

import logging
import queue
import threading

from . import messages as m
from .netcool_api import NetcoolClient, NetcoolError

log = logging.getLogger(__name__)


class KeepAliveThread(threading.Thread):
    def __init__(self, client: NetcoolClient, bus: "queue.Queue", interval_s: int = 60):
        super().__init__(daemon=True, name="keepalive")
        self.client, self.bus, self.interval = client, bus, max(20, interval_s)
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.wait(self.interval):
            if not self.client.logged_in and not self.client.user:
                continue
            try:
                left = self.client.keepalive()
                self.bus.put(m.ServerStatus(True, "Connected", left if left is not None else -1))
            except NetcoolError as exc:
                log.warning("keep-alive: %s", exc)
                self.bus.put(m.ServerStatus(False, str(exc)[:120]))
            except Exception as exc:
                log.exception("keep-alive crashed")
                self.bus.put(m.ServerStatus(False, f"Keep-alive error: {exc}"[:120]))

    def stop(self) -> None:
        self._stop_evt.set()
