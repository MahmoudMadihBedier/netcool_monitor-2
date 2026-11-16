"""Mail plugin: outage mail (Automated_Outage layout) and the Global TT text, as outage actions."""
from __future__ import annotations

from tkinter import messagebox

from kernel import Decision, OutageAction, Topics
from ui.widgets import Toast
from .mail import build_mail, global_tt_text, open_draft


class MailPlugin:
    name = "mail"

    def start(self, host) -> None:
        self.host = host
        host.add_outage_action(OutageAction("✉ Create outage mail", self.create_mail))
        host.add_outage_action(OutageAction("Copy Global TT info", self.copy_tt))

    def stop(self) -> None:
        pass

    def _real(self, o) -> None:                 # using the mail / TT text means: it IS an outage
        self.host.bus.publish(Topics.DECISION, Decision(o, True, purpose=self.host.purpose_of(o)))

    def create_mail(self, o) -> None:
        root = self.host.ui
        self._real(o)
        try:
            mail = build_mail(o, self.host.settings.mail)
            result = open_draft(mail)
            root.clipboard_clear()
            root.clipboard_append(mail.tsv)
            Toast.show(root, f"{result}\nTable also copied (paste into Automated_Outage sheet A1).")
        except Exception as exc:
            messagebox.showerror("Outage mail", f"Could not create the mail: {exc}", parent=root)

    def copy_tt(self, o) -> None:
        root = self.host.ui
        self._real(o)
        root.clipboard_clear()
        root.clipboard_append(global_tt_text(o, self.host.settings.rules.tt_minutes))
        Toast.show(root, "Global TT info copied (Asset ID = FOC, fault time, notes, outage sites).")
