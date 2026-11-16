"""Cascaded Sites Report: which physical sites hang behind which (transmission chains).

CSV columns:  Site, Physical Cascading Sites (';' separated, all downstream sites),
              No. Physical Cascading
If site X is down, every site in X's list goes down with it -> X is the FIRST OF CHAIN (FOC).
"""
from __future__ import annotations

from . import clock
import csv
import io
import logging
import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import BUNDLED_CASCADE, CASCADE_DIR

log = logging.getLogger(__name__)


class CascadeError(Exception):
    pass


@dataclass
class Chain:
    foc: str                     # first of chain (physical id)
    foc_down: bool               # FOC itself is in the down list?
    members: list                # down sites, ranked: FOC first, then by depth in the chain
    depth: dict                  # site -> depth below FOC (0 = FOC)
    unseen: list = field(default_factory=list)   # sites behind the FOC that are NOT reported down

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class CascadeReport:
    path: str
    children: dict               # site -> frozenset(downstream sites)
    parents: dict                # site -> set(upstream sites)
    report_date: Optional[datetime]
    rows: int
    with_chain: int
    warnings: list

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, path) -> "CascadeReport":
        p = Path(path)
        if not p.exists():
            raise CascadeError(f"File not found: {p}")
        raw = p.read_bytes()
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None or not text.strip():
            raise CascadeError("The file is empty or not a text/CSV file.")
        first = text.splitlines()[0]
        delim = ";" if first.count(";") > first.count(",") and "Site" in first else ","
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        header = [h.strip().lower() for h in next(reader, [])]
        if not header or "site" not in header[0]:
            raise CascadeError("This is not a Cascaded Sites Report: first column must be 'Site' "
                               f"(found: {', '.join(header[:3]) or 'nothing'}).")
        children, parents, warnings = {}, {}, []
        bad = rows = with_chain = 0
        inner_sep = "," if delim == ";" else ";"
        for rec in reader:
            if not rec or not rec[0].strip():
                continue
            rows += 1
            site = rec[0].strip().upper()
            lst = []
            if len(rec) >= 3:
                lst = [x.strip().upper() for x in re.split(r"[;" + inner_sep + r"]", rec[1]) if x.strip()]
            elif len(rec) == 2:
                if not rec[1].strip().isdigit():
                    lst = [x.strip().upper() for x in rec[1].split(inner_sep) if x.strip()]
            else:
                bad += 1
                continue
            lst = [x for x in lst if x != site]
            if site in children:
                warnings.append(f"Site {site} appears more than once (lists merged).")
                lst = list(children[site] | set(lst))
            children[site] = frozenset(lst)
            if lst:
                with_chain += 1
            for c in lst:
                parents.setdefault(c, set()).add(site)
        if rows < 50:
            raise CascadeError(f"Only {rows} sites found - this does not look like a full report.")
        if bad > rows * 0.05:
            raise CascadeError(f"{bad} of {rows} lines could not be read - check the file format.")
        if bad:
            warnings.append(f"{bad} unreadable line(s) skipped.")
        return cls(path=str(p), children=children, parents=parents,
                   report_date=_date_from_name(p), rows=rows, with_chain=with_chain,
                   warnings=warnings[:20])

    # ------------------------------------------------------------ queries
    @property
    def sites(self) -> set:
        cached = getattr(self, "_sites", None)
        if cached is None:
            cached = self._sites = set(self.children) | set(self.parents)
        return cached

    def known(self, site: str) -> bool:
        return site in self.children or site in self.parents

    def age_days(self, now: Optional[datetime] = None) -> Optional[int]:
        if not self.report_date:
            return None
        return ((now or clock.now()) - self.report_date).days

    def analyze(self, down: set, merge_cap: int = 80) -> list:
        """Group down physical sites into chains and find each chain's FOC."""
        d = {s for s in down if self.known(s)}
        if not d:
            return []
        roots = sorted(s for s in d if not (self.parents.get(s, set()) & d))
        # union roots that share a (not-down) common ancestor of reasonable size
        parent_of = {r: r for r in roots}

        def find(x):
            while parent_of[x] != x:
                parent_of[x] = parent_of[parent_of[x]]
                x = parent_of[x]
            return x

        anc = {r: {a for a in self.parents.get(r, set())
                   if len(self.children.get(a, ())) <= merge_cap} for r in roots}
        for i, a in enumerate(roots):
            if not anc[a]:
                continue
            for b in roots[i + 1:]:
                if anc[a] & anc[b]:
                    parent_of[find(b)] = find(a)
        groups: dict = {}
        for r in roots:
            groups.setdefault(find(r), []).append(r)

        chains = []
        for rs in groups.values():
            members = set(rs)
            for r in rs:
                members |= (self.children.get(r, frozenset()) & d)
            if len(rs) == 1:
                foc, foc_down = rs[0], True
            else:
                common = set.intersection(*(anc[r] for r in rs)) if all(anc[r] for r in rs) else set()
                if common:
                    foc = min(common, key=lambda a: (len(self.children.get(a, ())), a))
                    foc_down = False
                else:
                    foc, foc_down = rs[0], True
            scope = set(self.children.get(foc, frozenset())) | {foc} | members
            depth = {m: len(self.parents.get(m, set()) & scope) for m in members}
            ranked = sorted(members, key=lambda m: (m != foc, depth[m], m))
            unseen_all = set(self.children.get(foc, frozenset())) - d
            unseen = sorted(unseen_all, key=lambda m: (len(self.parents.get(m, set()) & scope), m))
            chains.append(Chain(foc=foc, foc_down=foc_down, members=ranked, depth=depth,
                                unseen=unseen[:15]))
        chains.sort(key=lambda c: -c.size)
        return chains


def _date_from_name(p: Path) -> Optional[datetime]:
    m = re.search(r"(\d{2})(\d{2})(20\d{2})", p.stem)       # ..._20092026 -> 20 Sep 2026
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", p.stem)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    try:
        return clock.from_epoch(p.stat().st_mtime)
    except OSError:
        return None


class CascadeStore:
    """Holds the report in use; thread-safe swap; copies uploads into the app folder."""

    def __init__(self):
        self._lock = threading.Lock()
        self._report: Optional[CascadeReport] = None
        self.error = ""

    @property
    def report(self) -> Optional[CascadeReport]:
        return self._report

    def load_initial(self, active_path: str) -> Optional[CascadeReport]:
        for cand in (active_path, str(BUNDLED_CASCADE)):
            if cand and Path(cand).exists():
                try:
                    rep = CascadeReport.load(cand)
                    with self._lock:
                        self._report = rep
                    self.error = ""
                    return rep
                except CascadeError as exc:
                    self.error = str(exc)
                    log.warning("cascade load %s: %s", cand, exc)
        return None

    def upload(self, src: str) -> CascadeReport:
        rep = CascadeReport.load(src)              # validate BEFORE copying
        CASCADE_DIR.mkdir(parents=True, exist_ok=True)
        dst = CASCADE_DIR / Path(src).name
        if Path(src).resolve() != dst.resolve():
            shutil.copy2(src, dst)
        rep.path = str(dst)
        if rep.report_date and rep.report_date.date() > clock.now().date():
            rep.warnings.append("Report date in the file name is in the future - check the file.")
        with self._lock:
            self._report = rep
        self.error = ""
        return rep
