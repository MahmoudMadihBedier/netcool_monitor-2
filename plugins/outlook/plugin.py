"""Outlook plugin: when an outage appears, searches the engineer's OWN Outlook mailbox (pywin32, local COM)
for the outage's site IDs / HUB together with words like CRQ, planned, activity, night - and shows the
matching mails on the popup and in the Global TT window. The engineer decides; nothing is sent anywhere.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from dataclasses import dataclass

from kernel import Advice, OutageAlert, Topics

log = logging.getLogger(__name__)
ACTIVITY_WORDS = ("crq", "planned", "activity", "maintenance", "upgrade", "night", "outage window", "swap",
                  "نشاط", "صيانة", "مخطط")


@dataclass
class OutlookSettings:
    enabled: bool = True
    days_back: int = 3
    folders: str = "Inbox"           # comma list: Inbox, Sent Items, Archive ...
    max_mails: int = 600             # newest mails scanned per folder (keeps the laptop fast)


class OutlookPlugin:
    name = "outlook"

    def start(self, host) -> None:
        self.host = host
        self.cfg = host.settings_section("outlook", OutlookSettings)
        self._cache: dict = {}                   # outage key -> (time, [lines])
        self._lock = threading.Lock()
        host.register_activity_source(self)
        host.register_advisor(self)
        host.bus.subscribe(Topics.OUTAGE_ALERT, self.on_outage, owner=self.name)

    def stop(self) -> None:
        pass

    # ---- interfaces
    def activities_for(self, sites: list, hub: str = "", area: str = "") -> list:
        return self.search(sites, hub)

    def advise(self, outage, purpose: str = "") -> Advice:
        hit = self._cache.get(outage.key)
        if hit and hit[1]:
            return Advice(f"📧 Outlook: {len(hit[1])} mail(s) mention these sites with planned/CRQ words",
                          source="outlook")
        return Advice("")

    def on_outage(self, a: OutageAlert) -> None:
        """Search in the background right away, so the result is ready when the engineer looks."""
        if not self.cfg.enabled or a.outage.key in self._cache:
            return
        sites = sorted(a.outage.all_site_ids)
        threading.Thread(target=lambda: self.search(sites, a.outage.hub, a.outage.key), daemon=True).start()

    # ---- the search
    def search(self, sites: list, hub: str = "", key=None) -> list:
        if key is not None:
            with self._lock:
                hit = self._cache.get(key)
            if hit and time.time() - hit[0] < 900:
                return hit[1]
        lines = []
        try:
            lines = self._search_outlook(sites, hub)
        except Exception as exc:                 # Outlook closed / not installed / COM busy
            log.info("Outlook search skipped: %s", str(exc)[:160])
        if key is not None:
            with self._lock:
                self._cache[key] = (time.time(), lines)
        return lines

    def _search_outlook(self, sites: list, hub: str) -> list:
        if not sys.platform.startswith("win") or not self.cfg.enabled:
            return []
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        pythoncom.CoInitialize()
        try:
            ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            return scan(_folders(ns, self.cfg.folders), sites, hub, self.cfg.days_back, self.cfg.max_mails)
        finally:
            pythoncom.CoUninitialize()


def _folders(ns, names: str) -> list:
    out = []
    for name in [n.strip() for n in names.split(",") if n.strip()]:
        try:
            out.append(ns.GetDefaultFolder(6) if name.lower() == "inbox" else ns.GetDefaultFolder(5)
                       if name.lower() == "sent items" else ns.Folders.Item(1).Folders.Item(name))
        except Exception:
            continue
    return out


def _site_core(site: str) -> str:
    s = site.upper()
    return s[1:] if s[:1] in "CGML" and len(s) > 4 and s[1].isalpha() else s


def scan(folders: list, sites: list, hub: str, days_back: int, max_mails: int, now=None) -> list:
    """Pure part (tested with fake folders): mails of the last N days whose subject/body name a site (or the
    HUB code) AND an activity word -> 'date  subject  (from)'."""
    import datetime as dt
    now = now or dt.datetime.now()
    since = now - dt.timedelta(days=max(1, days_back))
    keys = {_site_core(s) for s in sites if s} | ({hub.split()[0].upper()} if hub else set())
    rx = re.compile("|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True)), re.I) if keys else None
    if rx is None:
        return []
    hits, seen = [], set()
    for folder in folders:
        items = folder.Items
        try:
            items.Sort("[ReceivedTime]", True)
        except Exception:
            pass
        n = 0
        for mail in items:
            n += 1
            if n > max_mails:
                break
            try:
                received = mail.ReceivedTime
                when = dt.datetime(received.year, received.month, received.day, received.hour, received.minute)
                if when < since:
                    break
                text = f"{mail.Subject}\n{(mail.Body or '')[:20000]}"
            except Exception:
                continue
            low = text.lower()
            if rx.search(text) and any(w in low for w in ACTIVITY_WORDS):
                line = f"{when:%d/%m %H:%M}  {mail.Subject[:90]}  ({getattr(mail, 'SenderName', '')[:30]})"
                if line not in seen:
                    seen.add(line)
                    hits.append(line)
            if len(hits) >= 10:
                return hits
    return hits
