"""Alerts: turns core / plugin alert messages into popups, and engineer actions into bus decisions.

Popup buttons = 'Details / chain' + every kernel.OutageAction the plugins registered. Acknowledging an outage
popup publishes a REAL decision; a plugin's 'Not an outage' publishes a NOT decision, and this controller
mutes the outage in every session when it sees that decision.
"""
from __future__ import annotations

import logging

from core import clock
from core import messages as m
from kernel import AlertAck, AlertRequest, Decision, Topics
from . import theme as T
from .outage_view import level_color
from .widgets import Toast

log = logging.getLogger("alerts")


class AlertsController:
    def __init__(self, app):
        self.app = app
        self._outage_of: dict = {}            # popup key -> outage (for learning on acknowledge)
        self._decided: set = set()            # outage keys already learned as REAL
        b = app.host.bus
        b.subscribe(Topics.ALERT, self.on_request, ui=True, owner="app.alerts")
        b.subscribe(Topics.DECISION, self.on_decision, ui=True, owner="app.alerts")

    # ---------------------------------------------------------- from the core (session worker messages)
    def on_message(self, view, msg) -> None:
        if isinstance(msg, m.AlertRaised):
            self._raise(view, msg)
        elif isinstance(msg, m.AlertCleared):
            if msg.title == "Monitoring restored":
                self.app.alerts.ack((msg.sid, "BLIND"))
            Toast.show(self.app, f"{view.worker.session_name}: cleared - {msg.title}", T.GREEN)

    def _raise(self, view, msg) -> None:
        app, name, o = self.app, view.worker.session_name, msg.incident
        session_txt = f"{name}  ·  {view.worker.filter_id}"
        if msg.blind or o is None:
            app.alerts.show((msg.sid, "BLIND"), msg.title, msg.body, session=session_txt, blind=True)
        else:
            sid = msg.sid
            buttons = [("Details / chain", lambda: app.open_outage(sid, o), False)]
            buttons += [(a.label, (lambda c=a.command: c(o)), a.primary) for a in app.host.outage_actions]
            buttons.sort(key=lambda b: not b[2])
            question = ""
            if o.confidence == "MEDIUM":
                question = ("Possible outage: these sites share only the same Node + area (no HUB / chain "
                            "data). Is it a real outage? Confirm with TX - press 'Not an outage' if not.")
            self._outage_of[("OUT", o.key)] = o
            app.alerts.show(("OUT", o.key), msg.title, msg.body, session=name, color=level_color(o),
                            buttons=buttons, question=question)
        sites = ", ".join(o.site_ids) if o else ""
        app.overview.add_alert(f"{clock.now():%H:%M:%S}", name, msg.title, sites)
        log.warning("[%s] %s | %s", name, msg.title, sites)

    # ---------------------------------------------------------- from plugins
    def on_request(self, req: AlertRequest) -> None:
        self.app.alerts.show(req.key, req.title, req.body, session=req.session, blind=req.blind, color=req.color,
                             buttons=req.buttons, question=req.question)

    def on_decision(self, d: Decision) -> None:
        o = d.outage
        if d.real:
            self._decided.add(o.key)
            return
        self._decided.discard(o.key)
        for v in self.app.sm.views.values():            # the same outage in every session
            v.worker.mute(o.key, 2)
        self.app.alerts.ack(("OUT", o.key))
        Toast.show(self.app, f"Marked as NOT an outage for 2 hours: {o.title}", T.GREY_9)
        log.warning("Operator: NOT an outage %s (%s) %s", o.key, o.title, d.reason)

    # ---------------------------------------------------------- acknowledge
    def on_ack(self, key, by_user: bool) -> None:
        self.app.host.bus.publish(Topics.ALERT_ACK, AlertAck(key, by_user))
        o = self._outage_of.get(key)
        if by_user and o is not None and o.key not in self._decided:
            self._decided.add(o.key)
            self.app.host.bus.publish(Topics.DECISION, Decision(o, True, purpose=self.app.sm.purpose_of(o)))
