"""Outage engine (FO manual rules) - evidence based, counts PHYSICAL sites.

Evidence that down sites share one root cause:
  CHAIN  same transmission chain in the Cascaded Sites Report   (strong)
  HUB    same HUB code + same area                              (strong, the classic rule)
  NODE   same Node (RNC/BSC) + same area                        (weak -> "possible", ask the user)
Levels:  WATCH (2..)  ->  PRE-OUTAGE (min_sites, 4: call TX for FOC + 5th site)
         ->  OUTAGE (outage_sites, 5)  colour Yellow < 25 <= Orange < 50 <= Red
Times:   actual fault time = time the 5th physical site went down; Global TT at +15 min.
"""
from __future__ import annotations

from . import clock
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .cascade import CascadeReport
from .config import RuleSettings
from .models import NetcoolEvent
from .parser import EMPTY, EventParser
from .sites import physical_id, technology

LEVEL_ORDER = {"WATCH": 0, "PRE-OUTAGE": 1, "OUTAGE": 2}
POWER_WORDS = re.compile(r"power|mains|battery|c2b\.10|c2a\.50|c2a\.45|high temp|thermal|\bht\b|"
                         r"rectifier|ac fail|dc fail|generator", re.I)
TX_WORDS = re.compile(r"microwave|\bte\b|\btp\b|transmission|\btx\b|link|e1|stm|ptn|los\b|"
                      r"heartbeat|connection lost", re.I)


@dataclass
class PhysicalSite:
    pid: str
    logical: set = field(default_factory=set)
    techs: set = field(default_factory=set)
    first_down: Optional[datetime] = None
    hub: str = ""
    hub_raw: str = ""
    node: str = ""
    area: str = ""
    region: str = ""
    fault_code: str = ""
    summary: str = ""
    severity: int = -1
    events: list = field(default_factory=list)
    in_report: bool = True
    points: list = field(default_factory=list)     # every down time of this site (selected rows)


@dataclass
class Outage:
    key: tuple
    level: str                      # WATCH / PRE-OUTAGE / OUTAGE
    confidence: str                 # HIGH / MEDIUM
    evidence: list                  # ["CHAIN", "HUB", "NODE"]
    sites: list                     # list[PhysicalSite], ranked (FOC first, chain order, time)
    hub: str
    area: str
    region: str
    node: str
    foc: str = ""
    foc_down: bool = True
    foc_in_mom: bool = True
    chain_unseen: list = field(default_factory=list)
    fault_time: Optional[datetime] = None          # 5th physical site down
    tenth_time: Optional[datetime] = None
    color: str = ""                                 # YELLOW / ORANGE / RED (OUTAGE only)
    team: str = ""                                  # TX / NFM / PA? suggestion
    team_reason: str = ""
    notes: list = field(default_factory=list)       # guidance lines
    issues: list = field(default_factory=list)      # data-quality warnings for this outage
    tech_counts: dict = field(default_factory=dict)
    foc_site: Optional[PhysicalSite] = None

    # --- compatibility with the v2-v4 Incident object (history, popups) ---
    @property
    def site_ids(self) -> list:
        return [s.pid for s in self.sites]

    @property
    def start(self) -> datetime:
        return min(s.first_down for s in self.sites)

    @property
    def end(self) -> datetime:
        return max(s.first_down for s in self.sites)

    @property
    def count(self) -> int:
        return len(self.sites)

    def tt_due(self, minutes: int) -> Optional[datetime]:
        return self.fault_time + timedelta(minutes=minutes) if self.fault_time else None

    @property
    def title(self) -> str:
        where = self.area or self.region or self.node or "?"
        anchor = f"FOC {self.foc}" if self.foc else (f"HUB {self.hub}" if self.hub else f"Node {self.node}")
        return f"{self.count} sites · {anchor} · {where}"


@dataclass
class DataCheck:
    level: str     # INFO / WARN / ERROR
    text: str


@dataclass
class EngineResult:
    outages: list
    checks: list
    sev_counts: dict
    physical_down: int
    used_rows: int
    events: list


class OutageEngine:
    def __init__(self, rules: RuleSettings):
        self.r = rules
        self.parser = EventParser(rules)
        self._inc = re.compile(rules.include_summary_regex, re.I) if rules.include_summary_regex else None
        self._exc = re.compile(rules.exclude_summary_regex, re.I) if rules.exclude_summary_regex else None
        self._ignore = set(rules.ignore_severities or []) | {0}     # Clear is never a down site

    # ------------------------------------------------------------ filters
    def usable(self, e: NetcoolEvent, severities: set) -> bool:
        if e.severity not in severities or e.severity in self._ignore:
            return False
        if not e.site or e.site.upper() in EMPTY or e.down_at is None:
            return False
        if self.r.ignore_cleared and e.clear_time and e.clear_time.upper() not in EMPTY:
            return False
        if self._inc and not self._inc.search(e.summary):
            return False
        if self._exc and self._exc.search(e.summary):
            return False
        return True

    # ------------------------------------------------------------ main
    def evaluate(self, events: list, severities: set, cascade: Optional[CascadeReport] = None,
                 now: Optional[datetime] = None, columns: Optional[set] = None) -> EngineResult:
        now = now or clock.now()
        sev_counts = Counter(e.severity for e in events)
        used = [e for e in events if self.usable(e, severities)]
        known = cascade.sites if cascade else None
        phys = self._aggregate(used, known, cascade)
        checks = self._data_checks(events, used, phys, cascade, columns, severities, now)
        outages = self._cluster(phys, cascade, now)
        return EngineResult(outages=outages, checks=checks, sev_counts=dict(sev_counts),
                            physical_down=len(phys), used_rows=len(used), events=events)

    def _aggregate(self, used: list, known, cascade) -> dict:
        phys: dict = {}
        for e in sorted(used, key=lambda x: x.down_at):
            pid = physical_id(e.site, known)
            p = phys.get(pid)
            if p is None:
                p = phys[pid] = PhysicalSite(pid=pid, first_down=e.down_at,
                                             in_report=bool(cascade and cascade.known(pid)))
            p.logical.add(e.site)
            if e.down_at not in p.points:
                p.points.append(e.down_at)
            p.techs.add(technology(e.site, e.node, e.summary))
            p.events.append(e)
            hub = self.parser.hub_key(e.hub_raw)
            if hub and not p.hub:
                p.hub, p.hub_raw = hub, e.hub_raw
            for attr, val in (("node", e.node), ("area", e.area), ("region", e.region),
                              ("summary", e.summary)):
                if val and val.upper() not in EMPTY and not getattr(p, attr):
                    setattr(p, attr, val)
            fc = (e.fields or {}).get("FaultCode", "")
            if fc and not p.fault_code:
                p.fault_code = fc
            p.severity = max(p.severity, e.severity)
        return phys

    # ------------------------------------------------------------ clustering
    def _cluster(self, phys: dict, cascade, now: datetime) -> list:
        """Each evidence group (HUB+area, chain, node+area) is checked ON ITS OWN and can give
        SEVERAL outages at different times. A site joins an outage with the time of its alarm
        that falls in the window (not its oldest alarm). Overlapping HIGH clusters are merged."""
        if not phys:
            return []
        r = self.r
        chain_of: dict = {}
        clusters: list = []        # dict(sites={pid: t}, ev=set(), win=minutes)

        if r.use_hub:
            by_hub = defaultdict(list)
            for p in phys.values():
                if p.hub:
                    by_hub[(p.hub, p.area if r.area_field else "")].append(p)
            for members in by_hub.values():
                if len(members) >= 2:
                    for c in _extract_clusters(members, r.window_minutes):
                        clusters.append(dict(sites=c, ev={"HUB"}, win=r.window_minutes))
        if r.use_chain and cascade:
            for ch in cascade.analyze(set(phys), r.chain_merge_cap):
                if ch.size < 2:
                    continue
                for m in ch.members:
                    chain_of.setdefault(m, ch)
                for c in _extract_clusters([phys[m] for m in ch.members], r.chain_window_minutes):
                    clusters.append(dict(sites=c, ev={"CHAIN"}, win=r.chain_window_minutes))

        merged = _merge_clusters(clusters)
        outages = [self._build(_at(phys, c["sites"]), sorted(c["ev"]), "HIGH", chain_of, now, phys)
                   for c in merged]

        if r.use_node:
            busy = [(set(c["sites"]), min(c["sites"].values()), max(c["sites"].values())) for c in merged]
            by_node = defaultdict(list)
            for p in phys.values():
                if p.node and p.node.upper() not in ("LTE", "NULL"):
                    by_node[(p.node, p.area)].append(p)
            for members in by_node.values():
                for c in _extract_clusters(members, r.window_minutes):
                    if len(c) < r.min_sites:
                        continue
                    lo, hi = min(c.values()), max(c.values())
                    covered = any(len(set(c) & s) * 2 >= len(c) and lo <= h2 + timedelta(minutes=15)
                                  and l2 <= hi + timedelta(minutes=15) for s, l2, h2 in busy)
                    if not covered:
                        outages.append(self._build(_at(phys, c), ["NODE"], "MEDIUM", chain_of, now, phys))

        outages.sort(key=lambda o: (-LEVEL_ORDER[o.level], -o.count, o.start))
        return outages

    def _build(self, sites: list, evidence: list, confidence: str, chain_of: dict,
               now: datetime, phys: dict | None = None) -> Outage:
        r = self.r
        chain = None
        for s in sites:
            ch = chain_of.get(s.pid)
            if ch and (chain is None or ch.size > chain.size):
                chain = ch
        depth = chain.depth if chain else {}
        foc = chain.foc if chain else ""
        sites = sorted(sites, key=lambda s: (s.pid != foc, depth.get(s.pid, 99), s.first_down, s.pid))
        n = len(sites)
        level = "OUTAGE" if n >= r.outage_sites else ("PRE-OUTAGE" if n >= r.min_sites else "WATCH")
        times = sorted(s.first_down for s in sites)
        hub = _most_common([s.hub for s in sites])
        area = _most_common([s.area for s in sites])
        region = _most_common([s.region for s in sites])
        node = _most_common([s.node for s in sites])
        tech = Counter(t for s in sites for t in s.techs)
        o = Outage(key=(), level=level, confidence=confidence, evidence=evidence, sites=sites,
                   hub=hub, area=area, region=region, node=node, foc=foc,
                   foc_down=bool(chain and chain.foc_down),
                   foc_in_mom=bool(foc and (any(s.pid == foc for s in sites) or foc in (phys or {}))),
                   chain_unseen=list(chain.unseen) if chain else [],
                   fault_time=times[r.outage_sites - 1] if n >= r.outage_sites else None,
                   tenth_time=times[9] if n >= 10 else None, tech_counts=dict(tech))
        stamp = f"{times[0]:%m%d%H%M}"
        if foc:
            o.key = ("FOC", foc, stamp)
        elif hub:
            o.key = ("HUB", hub, area, stamp)
        else:
            o.key = ("NODE", node, area, stamp)
        if level == "OUTAGE":
            o.color = "YELLOW" if n < r.yellow_max else ("ORANGE" if n < r.orange_max else "RED")
        o.foc_site = (phys or {}).get(foc) if foc else None
        self._advise(o, now)
        return o

    def _advise(self, o: Outage, now: datetime) -> None:
        r = self.r
        foc_site = next((s for s in o.sites if s.pid == o.foc), None) or getattr(o, "foc_site", None)
        probe = f"{foc_site.fault_code} {foc_site.summary}" if foc_site else \
            " ".join(f"{s.fault_code} {s.summary}" for s in o.sites[:5])
        if o.foc and not o.foc_in_mom:
            o.team, o.team_reason = "PA / NFM ?", (f"First of chain {o.foc} is not in the MOM down list "
                                                   "(halted / activity?). FO manual: if FOC is halted, "
                                                   "sites are with PA or NFM.")
        elif POWER_WORDS.search(probe):
            o.team, o.team_reason = "NFM", "FOC alarm looks like power / high temperature."
        elif TX_WORDS.search(probe) or not probe.strip():
            o.team, o.team_reason = "TX", "FOC alarm looks like transmission (or unknown) - default TX."
        else:
            o.team, o.team_reason = "TX", "Default: Global TT on TX unless FOC is down on power."
        notes = []
        if o.level == "PRE-OUTAGE":
            notes.append(f"{o.count} sites down: call TX - ask for the first of chain and whether there "
                         f"is a {r.outage_sites}th site; check the 5th site manually.")
        if o.level == "OUTAGE":
            if o.fault_time:
                due = o.tt_due(r.tt_minutes)
                notes.append(f"Actual fault time (5th site down): {o.fault_time:%H:%M}. "
                             f"Open Global TT at {due:%H:%M} ({r.tt_minutes}th minute).")
            notes.append("Check activity with PA first - if PA activity: open Global TT immediately on PA.")
        if 2 <= now.hour < 6:
            notes.append("Down-time window 02:00-06:00: if activity with PA or 2nd line -> don't open outage.")
        elif 6 <= now.hour < 7:
            notes.append("After 06:00 with PA activity: open Global TT at 07:00 "
                         "(fault time 06:00, or 07:00 if PA extends).")
        if o.foc and foc_site and not any(s.pid == o.foc for s in o.sites):
            notes.append(f"First of chain {o.foc} has been down in MOM since {foc_site.first_down:%d/%m %H:%M} "
                         "(before this group) - check if it is halted / already reported.")
        if o.chain_unseen and o.level != "WATCH":
            notes.append("Not reported down but behind the FOC - check manually: "
                         + ", ".join(o.chain_unseen[:8]))
        o.notes = notes
        issues = []
        no_hub = [s.pid for s in o.sites if not s.hub]
        if no_hub and "HUB" in o.evidence:
            issues.append(f"{len(no_hub)} site(s) have no HUB value: {', '.join(no_hub[:6])}")
        unknown = [s.pid for s in o.sites if not s.in_report]
        if unknown:
            issues.append(f"{len(unknown)} site(s) not in the Cascaded Sites Report: {', '.join(unknown[:6])}")
        areas = {s.area for s in o.sites if s.area}
        if len(areas) > 1:
            issues.append("Sites are in different areas: " + ", ".join(sorted(areas)[:4]))
        if o.confidence == "MEDIUM":
            issues.append("Only the same Node + area links these sites (no HUB / chain data) - "
                          "please confirm with TX.")
        o.issues = issues

    # ------------------------------------------------------------ checks
    def _data_checks(self, events, used, phys, cascade, columns, severities, now) -> list:
        c = []
        if not events:
            c.append(DataCheck("WARN", "The filter returned 0 rows."))
            return c
        if columns:
            need = {"SiteID": self.r.site_field, "HUB": self.r.hub_field}
            if self.r.area_field:
                need["SubRegion"] = self.r.area_field
            miss = [k for k, v in need.items() if v not in columns]
            if miss:
                c.append(DataCheck("ERROR", "The view has no " + ", ".join(miss) + " column - choose a "
                                   "view like 2g_H_easymonitoring / 3g_H_easymonitoring."))
        if not used:
            names = ", ".join(_sev_name(v) for v in sorted(severities, reverse=True)) or "none"
            if severities and set(severities) <= self._ignore:
                c.append(DataCheck("INFO", f"Showing {names} rows only. Cleared rows are never counted as down "
                                           "sites - select Critical / Major / ... to detect outages."))
            else:
                c.append(DataCheck("INFO", f"No rows match the selected severities ({names})."))
        else:
            no_hub = sum(1 for p in phys.values() if not p.hub)
            if no_hub and no_hub >= max(3, len(phys) * 0.3):
                c.append(DataCheck("WARN", f"{no_hub} of {len(phys)} down sites have no HUB - the HUB rule "
                                           "can't use them; chain / node rules are used instead."))
            no_time = sum(1 for e in events if e.down_at is None)
            if no_time:
                c.append(DataCheck("WARN", f"{no_time} row(s) have no down time and were skipped."))
            if cascade:
                unk = [p.pid for p in phys.values() if not p.in_report]
                if unk and len(unk) >= max(3, len(phys) * 0.15):
                    c.append(DataCheck("WARN", f"{len(unk)} down sites are not in the Cascaded Sites Report "
                                               f"(e.g. {', '.join(unk[:4])}) - upload a newer report."))
        if cascade is None and self.r.use_chain:
            c.append(DataCheck("WARN", "No Cascaded Sites Report loaded - first-of-chain can't be found."))
        elif cascade:
            age = cascade.age_days(now)
            if age is not None and age > 7:
                c.append(DataCheck("INFO", f"Cascaded Sites Report is {age} days old."))
        return c


# ---------------------------------------------------------------- helpers
class _UnionFind:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _best_window(sites: list, window: timedelta) -> list:
    pts = sorted(sites, key=lambda s: s.first_down)
    best: list = []
    j = 0
    for i in range(len(pts)):
        while pts[i].first_down - pts[j].first_down > window:
            j += 1
        if i - j + 1 >= len(best):
            best = pts[j:i + 1]
    return list(best)


def _extract_clusters(sites: list, window_minutes: int) -> list:
    """Repeatedly take the time window holding the most DISTINCT sites (latest wins on ties),
    using every down time of every site; remove those sites; continue. -> [ {pid: time} ]"""
    win = timedelta(minutes=max(0, window_minutes))
    pts = sorted((t, s.pid) for s in sites for t in (s.points or [s.first_down]) if t)
    left = {s.pid for s in sites}
    out = []
    while len(left) >= 2:
        cur = [(t, pid) for t, pid in pts if pid in left]
        best, best_n, j, cnt = None, 0, 0, Counter()
        for i, (t, pid) in enumerate(cur):
            cnt[pid] += 1
            while t - cur[j][0] > win:
                cnt[cur[j][1]] -= 1
                if cnt[cur[j][1]] == 0:
                    del cnt[cur[j][1]]
                j += 1
            if len(cnt) >= best_n:
                best_n, best = len(cnt), (j, i)
        if best_n < 2:
            break
        c: dict = {}
        for t, pid in cur[best[0]:best[1] + 1]:
            if pid not in c:
                c[pid] = t
        out.append(c)
        left -= set(c)
    return out


def _merge_clusters(clusters: list) -> list:
    """Clusters that share a site and overlap in time are one outage (evidence combined)."""
    n = len(clusters)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    spans = [(min(c["sites"].values()), max(c["sites"].values())) for c in clusters]
    for a in range(n):
        for b in range(a + 1, n):
            if not (set(clusters[a]["sites"]) & set(clusters[b]["sites"])):
                continue
            slack = timedelta(minutes=max(clusters[a]["win"], clusters[b]["win"]))
            if spans[a][0] <= spans[b][1] + slack and spans[b][0] <= spans[a][1] + slack:
                parent[find(b)] = find(a)
    out: dict = {}
    for i, c in enumerate(clusters):
        m = out.setdefault(find(i), dict(sites={}, ev=set(), win=0))
        for pid, t in c["sites"].items():
            m["sites"][pid] = min(t, m["sites"].get(pid, t))
        m["ev"] |= c["ev"]
        m["win"] = max(m["win"], c["win"])
    return list(out.values())


def _at(phys: dict, sites: dict) -> list:
    """Copies of the physical sites carrying the time they went down IN this outage."""
    from dataclasses import replace
    return [replace(phys[pid], first_down=t) for pid, t in sites.items()]


def _most_common(vals: list) -> str:
    vals = [v for v in vals if v and v.upper() not in EMPTY]
    return Counter(vals).most_common(1)[0][0] if vals else ""


def _sev_name(v: int) -> str:
    return {5: "Critical", 4: "Major", 3: "Minor", 2: "Warning", 1: "Indeterminate", 0: "Clear"}.get(v, str(v))


# ---------------------------------------------------------------- tracker
@dataclass
class _Track:
    level: str
    sites: set
    last_alert: datetime
    tt_fired: bool = False
    missing: int = 0
    confidence: str = "HIGH"
    muted_until: Optional[datetime] = None


class OutageTracker:
    """When must the operator be alerted?  NEW (PRE/OUTAGE), ESCALATED, MORE SITES, REMINDER,
    TT DUE (15th minute); 'cleared' only after N refreshes without the outage.
    An outage keeps ONE identity while it grows or shrinks: it is matched to the tracked outage
    that shares most of its sites, and takes that key (stable popups, history and muting)."""

    def __init__(self, rules: RuleSettings):
        self.r = rules
        self.realert = timedelta(minutes=rules.realert_minutes) if rules.realert_minutes > 0 else None
        self._t: dict = {}

    # ---- identity
    def _match(self, o, taken: set):
        sites = set(o.site_ids)
        if o.key in self._t and o.key not in taken:
            return o.key
        best, best_n = None, 0
        for k, t in self._t.items():
            if k in taken or k[0] != o.key[0] and not (k[0] in ("FOC", "HUB") and o.key[0] in ("FOC", "HUB")):
                continue
            n = len(sites & t.sites)
            if n and n * 2 >= min(len(sites), len(t.sites)) and n > best_n:
                best, best_n = k, n
        return best

    def mute(self, key: tuple, hours: float = 2, now: Optional[datetime] = None) -> None:
        now = now or clock.now()
        t = self._t.get(key)
        if t is None:
            t = self._t[key] = _Track("OUTAGE", set(), now)
        t.muted_until = now + timedelta(hours=hours)

    def is_muted(self, key: tuple, now: datetime) -> bool:
        t = self._t.get(key)
        return bool(t and t.muted_until and t.muted_until > now)

    def update(self, outages: list, now: Optional[datetime] = None, hold_clears: bool = False) -> tuple:
        now = now or clock.now()
        fire, seen = [], set()
        for o in sorted(outages, key=lambda x: -x.count):
            if o.level == "WATCH":
                continue
            k = self._match(o, seen)
            if k is not None:
                o.key = k
            seen.add(o.key)
            sites = set(o.site_ids)
            t = self._t.get(o.key)
            if t is not None and t.muted_until and t.muted_until > now:
                t.sites |= sites
                t.missing = 0
                continue
            prefix = "POSSIBLE " if o.confidence == "MEDIUM" else ""
            if t is None or not t.sites:
                due0 = o.tt_due(self.r.tt_minutes)
                old = due0 is not None and now - due0 > timedelta(minutes=45)
                label = "NEW OUTAGE" if o.level == "OUTAGE" else "PRE-OUTAGE"
                if old:   # found already long down (e.g. at shift start): one alert, no TT reminder
                    label = f"OUTAGE (already down since {o.start:%H:%M})"
                fire.append((o, prefix + label))
                t = self._t[o.key] = _Track(o.level, sites, now, confidence=o.confidence)
                t.tt_fired = old
            else:
                t.missing = 0
                if LEVEL_ORDER[o.level] > LEVEL_ORDER[t.level]:
                    fire.append((o, prefix + "ESCALATED TO OUTAGE"))
                    t.level, t.sites, t.last_alert = o.level, t.sites | sites, now
                elif not sites <= t.sites:
                    fire.append((o, prefix + f"MORE SITES DOWN (+{len(sites - t.sites)})"))
                    t.sites, t.last_alert = t.sites | sites, now
                elif self.realert and now - t.last_alert >= self.realert:
                    fire.append((o, prefix + "STILL DOWN (reminder)"))
                    t.last_alert = now
            due = o.tt_due(self.r.tt_minutes)
            if o.level == "OUTAGE" and due and now >= due and not t.tt_fired:
                t.tt_fired = True
                fire.append((o, f"{self.r.tt_minutes}TH MINUTE - OPEN GLOBAL TT"))
        cleared = []
        for k in list(self._t):
            t = self._t[k]
            if k in seen or hold_clears:
                continue
            if t.muted_until and t.muted_until > now:
                continue
            t.missing += 1
            if t.missing >= max(1, self.r.clear_after_polls):
                del self._t[k]
                if t.sites and not t.muted_until:
                    cleared.append(k)
        return fire, cleared

    def keys_for(self, outages: list) -> None:
        """Give WATCH rows the identity of a tracked outage when they are the same sites."""
        for o in outages:
            if o.level == "WATCH":
                k = self._match(o, set())
                if k is not None:
                    o.key = k

    def reset(self) -> None:
        self._t = {k: t for k, t in self._t.items() if t.muted_until and t.muted_until > clock.now()}

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._t.values() if t.sites and not t.muted_until)
