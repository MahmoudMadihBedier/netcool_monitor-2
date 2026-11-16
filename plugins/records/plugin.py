"""Records plugin: writes every refresh, outage, decision and TT into the local SQLite database."""
from __future__ import annotations

from kernel import Decision, MenuItem, Refresh, TTOpened, Topics
from .records import Records


class RecordsPlugin:
    name = "records"

    def start(self, host) -> None:
        self.host = host
        self.db = Records(host.app_dir / "netcool.db")
        host.offer("records", self.db)
        b = host.bus
        b.subscribe(Topics.REFRESH, self.on_refresh, owner=self.name)
        b.subscribe(Topics.DECISION, self.on_decision, owner=self.name)
        b.subscribe(Topics.TT_OPENED, self.on_tt, owner=self.name)
        host.add_menu(MenuItem("Records…", self.open_window, menu="Tools", icon="📚", shortcut="Ctrl+R"))

    def stop(self) -> None:
        self.db.close()

    def on_refresh(self, r: Refresh) -> None:
        self.db.record_rows(r.filter_id, r.events, r.new_ids, r.changed_ids, r.removed, r.first)
        self.db.record_outages(r.outages, r.session)

    def on_decision(self, d: Decision) -> None:
        self.db.add_decision(d.outage, d.real, d.reason, d.note, d.team, d.purpose)

    def on_tt(self, t: TTOpened) -> None:
        pass                       # the remedy plugin writes the full request/answer itself (add_tt)

    def open_window(self) -> None:
        if self.host.ui is None:
            return
        from plugins.remedy.ui import RecordsDialog
        RecordsDialog(self.host.ui, self.db).run()
