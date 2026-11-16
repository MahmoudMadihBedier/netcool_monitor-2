"""Outage mail + Global TT text - same layout as the Automated_Outage.xlsm macro.

Table columns (A:F of the 'Outage' sheet): LastOccurrence, SiteID, Node, HUB, SubRegion, Region
Header row yellow + bold, borders, centred, Calibri 11.
Intro:  < 5 sites -> "...provide us with FOC and if there is a 5TH site?"
        >= 5     -> "...provide us with FOC."
Rows are ranked: first of chain first, then chain order, then down time.
Opens as a DRAFT in Outlook (like .Display in the macro) - never sent automatically.
"""
from __future__ import annotations

from core import clock
import getpass
import html
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

from core.config import MAIL_DIR, MailSettings

log = logging.getLogger(__name__)
COLUMNS = ["LastOccurrence", "SiteID", "Node", "HUB", "SubRegion", "Region"]


@dataclass
class OutageMail:
    to: str
    cc: str
    subject: str
    html: str
    rows: list           # [[6 strings]] without header

    @property
    def tsv(self) -> str:
        """Paste-ready for the Automated_Outage sheet (A1)."""
        return "\n".join("\t".join(r) for r in [COLUMNS] + self.rows)


def _mom_time(dt: datetime) -> str:
    """'5/12/26, 3:03 PM' like MOM / the sheet."""
    h = dt.hour % 12 or 12
    return f"{dt.month}/{dt.day}/{dt:%y}, {h}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def build_rows(outage) -> list:
    rows, seen = [], set()
    for site in outage.sites:
        evs = sorted(site.events, key=lambda e: (e.last_at or e.down_at or datetime.min))
        latest = {}
        for e in evs:                       # one row per logical site (latest occurrence)
            latest[e.site] = e
        for e in sorted(latest.values(), key=lambda e: e.down_at or datetime.min):
            if e.site in seen:
                continue
            seen.add(e.site)
            f = e.fields or {}
            t = f.get("LastOccurrence") or (_mom_time(e.last_at) if e.last_at else
                                            (_mom_time(e.down_at) if e.down_at else ""))
            if t.isdigit():
                t = _mom_time(e.last_at or e.down_at)
            rows.append([t, e.site, e.node or "", e.hub_raw or "", e.area or "", e.region or ""])
    return rows


def build_mail(outage, cfg: MailSettings, now: datetime | None = None) -> OutageMail:
    now = now or clock.now()
    rows = build_rows(outage)
    n = outage.count
    fields = dict(date=now.strftime("%d-%b-%Y"), region=outage.region or "", area=outage.area or "",
                  count=n, foc=outage.foc or "", hub=outage.hub or "")
    try:
        subject = cfg.subject_template.format(**fields)
    except (KeyError, IndexError, ValueError):
        subject = f"{fields['date']} || Downsites"
    if n < 5:
        intro = ("Dears,<br><br>Kindly check if the Down sites below related to the same chain and "
                 "provide us with FOC and if there is a 5TH site?")
    else:
        intro = ("Dears,<br><br>Kindly check if the Down sites below related to the same chain and "
                 "provide us with FOC.")
    if cfg.include_foc_hint and outage.foc:
        state = "down" if outage.foc_in_mom else "not in our down list"
        intro += (f"<br>As per the Cascaded Sites Report, the expected first of chain is "
                  f"<b>{html.escape(outage.foc)}</b> ({state}).")
    td = "border:1px solid black;padding:5px;text-align:center;"
    body = ["<html><body style='font-family:Calibri;font-size:11pt;'>", intro, "<br><br>",
            "<table style='border-collapse:collapse;border:1px solid black;font-family:Calibri;"
            "font-size:11pt;'>",
            "<tr style='background-color:#FFFF00;font-weight:bold;'>"
            + "".join(f"<td style='{td}'>{c}</td>" for c in COLUMNS) + "</tr>"]
    for r in rows:
        body.append("<tr>" + "".join(f"<td style='{td}'>{html.escape(c)}</td>" for c in r) + "</tr>")
    sig = cfg.signature or os.environ.get("USERNAME") or getpass.getuser()
    body.append(f"</table><br><br>Regards,<br>{html.escape(sig)}</body></html>")
    return OutageMail(to=cfg.to, cc=cfg.cc, subject=subject, html="".join(body), rows=rows)


def open_draft(mail: OutageMail) -> str:
    """Outlook draft via COM (.Display), else an .eml draft opened by the default mail app."""
    if sys.platform.startswith("win"):
        try:
            import win32com.client  # type: ignore
            app = win32com.client.Dispatch("Outlook.Application")
            item = app.CreateItem(0)
            item.To, item.CC, item.Subject, item.HTMLBody = mail.to, mail.cc, mail.subject, mail.html
            item.Display()
            return "Outlook draft opened."
        except Exception as exc:
            log.info("Outlook COM not available (%s) - using .eml draft", exc)
    MAIL_DIR.mkdir(parents=True, exist_ok=True)
    msg = EmailMessage()
    msg["To"], msg["Cc"], msg["Subject"] = mail.to.replace(";", ", "), mail.cc.replace(";", ", "), mail.subject
    msg["X-Unsent"] = "1"                    # Outlook opens it as an editable draft
    msg.set_content("Please view this mail in HTML.")
    msg.add_alternative(mail.html, subtype="html")
    path = MAIL_DIR / f"outage_{clock.now():%Y%m%d_%H%M%S}.eml"
    path.write_bytes(bytes(msg))
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa
        return f"Draft saved and opened: {path}"
    except Exception:
        return f"Draft saved: {path}"


def global_tt_text(outage, tt_minutes: int = 15) -> str:
    """Fields for Remedy -> Global Relation Tool."""
    sites = "\n".join(outage.tt_sites)
    ft = outage.tt_fault_time
    lines = [
        f"Asset ID (first of chain): {outage.foc or '(ask TX)'}",
        f"Actual fault time: {ft:%m/%d/%Y %I:%M %p}" if ft else "Actual fault time: (less than 5 sites)",
        f"Notes: {outage.tt_notes}",
        f"Suggested team: {outage.team}",
        f"Global TT due: {outage.tt_due(tt_minutes):%H:%M}" if outage.fault_time else "",
        "Outage sites:", sites,
    ]
    return "\n".join(x for x in lines if x is not None)
