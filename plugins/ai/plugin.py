"""AI plugin: learns from decisions and TTs, advises on outages, suggests teams and session purposes."""
from __future__ import annotations

from kernel import Advice, Decision, MenuItem, OutageAction, TTOpened, Topics
from .brain import Brain


class AIPlugin:
    name = "ai"

    def start(self, host) -> None:
        self.host = host
        self.brain = Brain(host.app_dir / "brain.json")
        host.offer("ai", self)
        host.register_advisor(self)
        b = host.bus
        b.subscribe(Topics.DECISION, self.on_decision, owner=self.name)
        b.subscribe(Topics.TT_OPENED, self.on_tt, owner=self.name)
        host.add_menu(MenuItem("AI assistant…", self.open_window, menu="Tools", icon="🧠", shortcut="Ctrl+I"))
        host.add_outage_action(OutageAction("Not an outage", self.not_an_outage))

    def stop(self) -> None:
        self.brain.save()

    # ---- advisor interface
    def _context(self, outage) -> dict:
        host = self.host
        sites = sorted({l for s in outage.sites for l in s.logical} | {s.pid for s in outage.sites})
        acts = []
        for src in host.activity_sources:                    # Outlook etc. (cached by the source)
            try:
                acts += list(src.activities_for(sites, outage.hub, outage.area) or [])
            except Exception:
                pass
        return {"activities": acts, "tt": host.tt_lookup(outage)}

    def advise(self, outage, purpose: str = "") -> Advice:
        a = self.brain.advise(outage, purpose, self._context(outage))
        return Advice(a["text"], a["p"], source="ai")

    def suggest_team(self, outage, purpose: str = "") -> tuple:
        return self.brain.suggest_team(outage, purpose)

    def guess_purpose(self, filter_id: str, view_id: str, sample: str = "") -> tuple:
        return self.brain.guess_purpose(filter_id, view_id, sample)

    def learn_purpose(self, filter_id: str, view_id: str, purpose: str, note: str = "") -> None:
        self.brain.learn_purpose(filter_id, view_id, purpose, note)

    # ---- learning from events
    def on_decision(self, d: Decision) -> None:
        self.brain.learn_outage(d.outage, d.real, d.purpose, d.reason, d.note, d.team, self._context(d.outage))

    def on_tt(self, t: TTOpened) -> None:
        self.brain.learn_team(t.outage, t.team, self.host.purpose_of(t.outage))

    # ---- UI
    def not_an_outage(self, o) -> None:
        """Asks why (reason + note), then publishes the decision; the app mutes on that decision."""
        from .ui import NotOutageDialog
        answer = NotOutageDialog(self.host.ui, o, getattr(o, "ai", None)).run()
        if answer is None:
            return
        reason, note = answer
        self.host.bus.publish(Topics.DECISION, Decision(o, False, reason, note, purpose=self.host.purpose_of(o)))

    def open_window(self) -> None:
        from .ui import BrainDialog
        BrainDialog(self.host.ui, self.brain, self.host.service("records")).run()
