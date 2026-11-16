"""Heartbeat Failure task (Access Ericsson SOC procedure, M. Madih).

Procedure -> tool:
  1. Collect alarms from MOM: the LTE Heartbeat filter (Critical) and the 3G filter.
  2/3. Result sheet: for every heartbeat SiteID,  VLOOKUP(SiteID, '3G Filter'!B:B)
       found  -> the site is already in the 3G filter (already handled as down) -> not investigated
       #N/A   -> heartbeat only -> INVESTIGATE
     Only heartbeats of the LAST SHIFT ("make sure the time orientation"): LastOccurrence in the window.
  4. Keep unique sites.
  5. TT number present -> already handled;  no TT -> THE LIST: check in ENM (real or fake heartbeat).
Shifts: Morning 06:30-18:30, Night 18:30-06:30 (default window = the last completed shift).
Result for the user = the site NUMBERS only: unique, heartbeat in the shift, not in the 3G filter, no TT.
"""
from __future__ import annotations

from core import clock
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from core.parser import EMPTY

SEV_NAMES = {5: "Critical", 4: "Major", 3: "Minor", 2: "Warning", 1: "Indeterminate", 0: "Clear"}
SEV_COLORS = {5: "#ff0000", 4: "#ffb329", 3: "#ffff00", 2: "#63b8ff", 1: "#bf3eff", 0: "#00cd00"}


@dataclass
class HeartbeatSite:
    site: str
    last: datetime                 # latest LastOccurrence of a heartbeat alarm in the window
    first: Optional[datetime]      # FirstOccurrence of that alarm
    node: str
    hub: str
    area: str
    region: str
    severity: int
    alarms: int                    # heartbeat alarms of this site in the window
    tt: str                        # TT number(s) from MOM ('' = none)
    fault_code: str
    in_reference: bool             # found in the 3G filter (VLOOKUP hit)
    ref_summary: str = ""          # what the 3G filter shows for the site

    @property
    def action(self) -> str:
        if self.in_reference:
            return "In 3G filter - not investigated"
        if self.tt:
            return f"Has TT {self.tt} - handle the ticket"
        return "No TT - check in ENM (real or fake heartbeat)"


@dataclass
class HeartbeatReport:
    since: datetime
    until: datetime
    hb_filter: str
    ref_filter: str
    severities: set
    read_alarms: int               # heartbeat rows read from MOM
    in_window: int                 # heartbeat alarms inside the time window + severities
    investigate: list              # [HeartbeatSite] not in the 3G filter (the #N/A sites), newest first
    excluded: list                 # [HeartbeatSite] found in the 3G filter
    skipped_old: int               # alarms outside the window
    skipped_sev: int               # alarms with another severity
    skipped_not_hb: int            # rows whose Summary is not a heartbeat
    created: datetime = field(default_factory=clock.now)
    title: str = ""                # e.g. "Last shift (Night shift)"

    @property
    def sites_to_check(self) -> list:
        """THE result: unique heartbeat sites of the window, not in the 3G filter, without TT."""
        return [s for s in self.investigate if not s.tt]

    @property
    def with_tt(self) -> list:
        return [s for s in self.investigate if s.tt]

    @property
    def without_tt(self) -> list:
        return [s for s in self.investigate if not s.tt]


def _clean(v: str) -> str:
    v = (v or "").strip()
    return "" if v.upper() in EMPTY else v


def analyze(hb_events: list, ref_events: list, since: datetime, until: datetime,
            severities: set, summary_regex: str = r"heart\s*beat", hb_filter: str = "",
            ref_filter: str = "") -> HeartbeatReport:
    rx = re.compile(summary_regex, re.I) if summary_regex else None
    ref = {}
    for e in ref_events:
        sid = (e.site or "").strip().upper()
        if sid and sid not in ref:
            ref[sid] = e.summary
    by_site: dict = {}
    old = sev_skip = not_hb = n_in = 0
    for e in hb_events:
        sid = (e.site or "").strip().upper()
        if not sid:
            continue
        if rx and not rx.search(e.summary or ""):
            not_hb += 1
            continue
        when = e.last_at or e.down_at
        if when is None or not (since <= when <= until):
            old += 1
            continue
        if e.severity not in severities:
            sev_skip += 1
            continue
        n_in += 1
        tt = _clean((e.fields or {}).get("TTNumber", ""))
        cur = by_site.get(sid)
        if cur is None:
            by_site[sid] = HeartbeatSite(
                site=sid, last=when, first=e.down_at, node=_clean(e.node), hub=_clean(e.hub_raw),
                area=_clean(e.area), region=_clean(e.region), severity=e.severity, alarms=1, tt=tt,
                fault_code=_clean((e.fields or {}).get("FaultCode", "")), in_reference=sid in ref,
                ref_summary=ref.get(sid, ""))
        else:
            cur.alarms += 1
            cur.severity = max(cur.severity, e.severity)
            if tt and tt not in cur.tt.split(", "):
                cur.tt = f"{cur.tt}, {tt}" if cur.tt else tt
            if when > cur.last:
                cur.last, cur.first = when, e.down_at
            for attr, val in (("node", e.node), ("hub", e.hub_raw), ("area", e.area), ("region", e.region)):
                if not getattr(cur, attr) and _clean(val):
                    setattr(cur, attr, _clean(val))
    sites = sorted(by_site.values(), key=lambda s: s.last, reverse=True)
    return HeartbeatReport(since=since, until=until, hb_filter=hb_filter, ref_filter=ref_filter,
                           severities=set(severities), read_alarms=len(hb_events), in_window=n_in,
                           investigate=[s for s in sites if not s.in_reference],
                           excluded=[s for s in sites if s.in_reference],
                           skipped_old=old, skipped_sev=sev_skip, skipped_not_hb=not_hb)


def _hm(text: str) -> tuple:
    try:
        h, m = (int(x) for x in str(text).split(":")[:2])
        return h % 24, m % 60
    except ValueError:
        return 6, 30


def shift_window(kind: str, now: Optional[datetime] = None, hours: int = 12, start: str = "06:30") -> tuple:
    """Shifts: Morning start..start+12h (06:30-18:30), Night start+12h..start next day (18:30-06:30).
    'previous_shift' = the last COMPLETED shift, 'current_shift' = the running one (until now),
    'last_hours' = now-hours..now. Returns (since, until)."""
    now = now or clock.now()
    if kind == "last_hours":
        return now - timedelta(hours=hours), now
    h, m = _hm(start)
    morning = now.replace(hour=h, minute=m, second=0, microsecond=0)
    starts = [morning - timedelta(days=1), morning - timedelta(hours=12), morning, morning + timedelta(hours=12)]
    cur = max(x for x in starts if x <= now)
    if kind == "current_shift":
        return cur, now
    return cur - timedelta(hours=12), cur


def shift_name(since: datetime, start: str = "06:30") -> str:
    h, m = _hm(start)
    return "Morning shift" if (since.hour, since.minute) == (h, m) else (
        "Night shift" if (since.hour, since.minute) == ((h + 12) % 24, m) else "Custom window")


def site_list_text(rep: HeartbeatReport) -> str:
    """The site numbers only, one per line (paste into the sheet / mail)."""
    return "\n".join(s.site for s in rep.sites_to_check)


def to_tsv(rep: HeartbeatReport) -> str:
    lines = ["#\tSiteID\tLast heartbeat\tAlarms\tSeverity"]
    for i, s in enumerate(rep.sites_to_check, 1):
        lines.append(f"{i}\t{s.site}\t{s.last:%d/%m/%Y %H:%M}\t{s.alarms}\t{SEV_NAMES.get(s.severity, '')}")
    return "\n".join(lines)


def build_html(rep: HeartbeatReport, user: str = "") -> str:
    e = html.escape
    sev = ", ".join(SEV_NAMES[v] for v in sorted(rep.severities, reverse=True))
    sites = rep.sites_to_check
    rows = "".join(
        f"<tr><td class='n'>{i}</td><td class='site'>{e(s.site)}</td><td>{s.last:%d/%m/%Y %H:%M}</td>"
        f"<td class='c'>{s.alarms}</td><td><span class='sev' style='background:{SEV_COLORS.get(s.severity, '#fff')}'>"
        f"{SEV_NAMES.get(s.severity, '?')}</span></td><td>{e(s.area)}</td></tr>"
        for i, s in enumerate(sites, 1)) or \
        "<tr><td colspan='6' style='text-align:center;padding:16px'>No heartbeat site without TT in this shift.</td></tr>"
    tt = ", ".join(f"{e(s.site)} ({e(s.tt)})" for s in rep.with_tt) or "none"
    ref = ", ".join(e(s.site) for s in rep.excluded) or "none"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Heartbeat Failure Task</title><style>
@page {{ size: A4; margin: 12mm 12mm 14mm; }}
body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: #111; }}
.band {{ background: #E60000; color: #fff; padding: 14px 18px; border-radius: 0 0 0 18px; }}
.band h1 {{ margin: 0; font-size: 19pt; }} .band .s {{ font-size: 12pt; margin-top: 4px; font-weight: 600; }}
.band .m {{ font-size: 8.5pt; opacity: .92; margin-top: 3px; }}
.big {{ display: flex; align-items: center; gap: 14px; border: 1px solid #ddd; border-left: 6px solid #E60000;
        padding: 10px 14px; margin: 12px 0 10px; }}
.big .num {{ font-size: 28pt; font-weight: 700; color: #E60000; }} .big span {{ font-size: 10.5pt; color: #333; }}
table {{ border-collapse: collapse; width: 100%; }}
th {{ background: #000; color: #fff; padding: 5px 8px; text-align: left; font-size: 9pt; }}
td {{ border-bottom: 1px solid #e3e3e3; padding: 4px 8px; font-size: 9.5pt; }}
tr:nth-child(even) td {{ background: #f6f6f6; }} tr {{ page-break-inside: avoid; }}
td.site {{ font-size: 12pt; font-weight: 700; letter-spacing: .5px; }} td.n, td.c {{ color: #666; width: 28px; }}
.sev {{ padding: 0 5px; border-radius: 3px; font-weight: 700; font-size: 8pt; }}
h2 {{ font-size: 11pt; color: #E60000; margin: 14px 0 4px; }}
.ref {{ font-size: 8.5pt; color: #444; line-height: 1.5; }}
.note {{ font-size: 8.5pt; color: #555; margin-top: 10px; border-top: 1px solid #ddd; padding-top: 6px; }}
</style></head><body>
<div class='band'><h1>Heartbeat Failure Task</h1>
<div class='s'>{e(rep.title or 'Shift')} · {rep.since:%d/%m/%Y %H:%M} → {rep.until:%d/%m/%Y %H:%M} ({e(clock.label())})</div>
<div class='m'>Severity: {e(sev)} · {e(rep.hb_filter.split(':')[0])} vs {e(rep.ref_filter.split(':')[0])} ·
generated {rep.created:%d/%m/%Y %H:%M}{(' by ' + e(user)) if user else ''}</div></div>
<div class='big'><div class='num'>{len(sites)}</div><span>site(s) with heartbeat failure in this shift, <b>not in the 3G filter</b> and
<b>without TT</b> - check each in ENM (real or fake heartbeat).</span></div>
<table><tr><th>#</th><th>SiteID</th><th>Last heartbeat</th><th>Alarms</th><th>Severity</th><th>SubRegion</th></tr>{rows}</table>
<h2>Not on the list (for reference)</h2>
<div class='ref'><b>Already have a TT ({len(rep.with_tt)}):</b> {tt}</div>
<div class='ref'><b>Also in the 3G filter ({len(rep.excluded)}):</b> {ref}</div>
<div class='note'>Rule (Heartbeat workbook): heartbeat SiteID looked up in the 3G filter (VLOOKUP); only "#N/A" sites, LastOccurrence in
the shift, each site once, TT number empty. Read {rep.read_alarms} heartbeat alarm(s); {rep.in_window} in the shift and severity;
{rep.skipped_old} outside the shift. Next: real heartbeat in ENM → open a TT and assign the site; clear → keep it as up; send the mail.
<br>Netcool Shift Monitor · Access Ericsson team, Vodafone Egypt</div>
</body></html>"""


def run_task(client, rules, hb_filter: str, hb_view: str, ref_filter: str, ref_view: str,
             since: datetime, until: datetime, severities: set, summary_regex: str,
             datasource: str = "", title: str = "") -> HeartbeatReport:
    """Reads BOTH filters from MOM once (fresh datasets, released after) and analyses them."""
    from core.columns import resolve
    from core.netcool_api import new_client_id
    from core.parser import EventParser

    def read(fid, vid):
        cid = new_client_id()
        try:
            res = client.fetch_events(cid, fid, vid, datasource, first=True)
        finally:
            client.release(cid, fid, vid, datasource)
        eff, _ = resolve(rules, res.columns, res.labels)
        return EventParser(eff).parse(res.rows)

    hb = read(hb_filter, hb_view)
    ref = read(ref_filter, ref_view) if ref_filter else []
    rep = analyze(hb, ref, since, until, severities, summary_regex, hb_filter, ref_filter)
    rep.title = title
    return rep
