"""Remedy plugin: 'Outage -> Global TT' (FO checklist, pre-filled TT, review, send) + Remedy setup."""
from __future__ import annotations

import logging
from tkinter import messagebox

from core.credentials import CredentialStore
from kernel import Decision, MenuItem, OutageAction, TTOpened, Topics
from .remedy import RemedyClient, build_envelope, build_fields, can_send, mask
from .ui import GlobalTTDialog, RemedyLoginDialog, RemedySetupDialog, fo_checklist, fo_team

log = logging.getLogger(__name__)


class RemedyPlugin:
    name = "remedy"

    def start(self, host) -> None:
        self.host = host
        self.creds = CredentialStore("NetcoolShiftMonitor-Remedy")
        self.opened: dict = {}                      # outage key -> TT number (this run)
        host.offer("remedy", self)
        host.add_outage_action(OutageAction("✅ Outage → Global TT", self.open_global_tt, primary=True))
        host.add_menu(MenuItem("Remedy / Global TT setup…", self.setup, menu="Tools", icon="🎫"))
        host.tt_lookup = self.tt_for

    def stop(self) -> None:
        pass

    # ---- helpers
    @property
    def rs(self):
        return self.host.settings.remedy

    def _client(self, rs=None):
        if not self.host.client:
            raise RuntimeError("Sign in first (the Remedy call uses the tool's connection)")
        return RemedyClient(self.host.client.t, rs or self.rs)

    def tt_for(self, o) -> str:
        if o.key in self.opened:
            return self.opened[o.key]
        db = self.host.service("records")
        return db.tt_for(o) if db else ""

    def setup(self) -> None:
        rs = RemedySetupDialog(self.host.ui, self.rs, self._client).run()
        if rs is not None:
            self.host.settings.remedy = rs
            self.host.save_settings()

    # ---- flow
    def open_global_tt(self, o) -> None:
        host, root = self.host, self.host.ui
        purpose = host.purpose_of(o)
        host.bus.publish(Topics.DECISION, Decision(o, True, purpose=purpose))
        events = host.outage_events(o)
        sites = o.all_site_ids
        mom_tts = sorted({(e.fields or {}).get("TTNumber", "").strip() for e in events if e.site in sites}
                         - {"", "NULL"})
        ai = host.service("ai")
        ai_team, seen = ai.suggest_team(o, purpose) if ai else ("", 0)
        team = ai_team if seen >= 3 else fo_team(o)
        hint = f"AI: similar outages went to {ai_team} ({seen}x)" if ai_team else ""
        acts = host.activities_for(sorted(sites), o.hub, o.area)
        user = self.rs.last_user or (host.client.user if host.client else "") or ""
        sessions = ", ".join(s.name for s in host.sessions())
        checks = fo_checklist(o, host.settings.rules.tt_minutes, mom_tts, acts)
        dlg = GlobalTTDialog(root, o, team, hint, lambda t: build_fields(o, t, self.rs, user, events, sessions),
                             lambda t: can_send(self.rs, t), checks, self.tt_for(o), acts)
        res = dlg.run()
        if res:
            self.send(o, *res)

    def send(self, o, team: str, values: dict) -> None:
        host, root, rs = self.host, self.host.ui, self.rs
        user = rs.last_user
        pw = self.creds.get(user) if user else None
        if not pw:
            login = RemedyLoginDialog(root, user).run()
            if not login:
                return
            user, pw, remember = login
            rs.last_user = user
            host.save_settings()
            if remember:
                self.creds.save(user, pw)
        values = dict(values, Submitter=user, **{"Stage-Submitter__c": user})
        request = mask(build_envelope(values, user, pw))
        db = host.service("records")

        def done(res, err):
            if err:
                if db:
                    db.add_tt(o, team, False, "", str(err), user, request, "")
                messagebox.showerror("Global TT", f"The TT was NOT opened:\n{err}\n\nNothing was sent twice - "
                                     "check the VPN / Remedy and press Open TT again.", parent=root)
                return
            if db:
                db.add_tt(o, team, res.ok, res.tt_number, res.error, user, request, str(res.fields))
            if not res.ok:
                if "auth" in res.error.lower() or "password" in res.error.lower():
                    self.creds.delete(user)
                messagebox.showerror("Global TT", f"Remedy refused the TT:\n{res.error}", parent=root)
                return
            self.opened[o.key] = res.tt_number
            host.bus.publish(Topics.TT_OPENED, TTOpened(o, team, res.tt_number, user))
            root.clipboard_clear()
            root.clipboard_append(res.tt_number)
            logging.getLogger("alerts").warning("Global TT %s opened on %s for %s", res.tt_number, team, o.title)
            messagebox.showinfo("Global TT", f"Global TT opened: {res.tt_number}\nTeam: {team}\n\n"
                                "(TT number copied - check it in Remedy.)", parent=root)
        host.run_bg(lambda: self._client().create_tt(values, user, pw), done)
