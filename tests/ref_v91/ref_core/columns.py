"""Find the SiteID / HUB / SubRegion / Region columns in ANY view.

Views name and label columns differently (3G view: 'SubRegion', 2G view: 'Sub_Region'; the HUB is
labelled 'HUB' but stored as CI_Organization). The configured column id is used when the view has
it; otherwise the column is found by its label in the view's column list (eventData/columns).
"""
from __future__ import annotations

import re
from dataclasses import replace

from .config import RuleSettings

ROLES = {
    "site_field": ("site id", ["siteid", "site id", "site", "site_id"]),
    "hub_field": ("HUB", ["hub", "hub id", "hub_id", "hubid"]),
    "area_field": ("SubRegion", ["subregion", "sub region", "sub_region", "sub-region", "area"]),
    "region_field": ("Region", ["region"]),
}


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower())


def resolve(rules: RuleSettings, columns: set, labels: dict) -> tuple:
    """-> (effective_rules, notes[str])"""
    if not columns:
        return rules, []
    changes, notes = {}, []
    for attr, (role, names) in ROLES.items():
        cur = getattr(rules, attr)
        if attr == "area_field" and cur == "":
            continue                       # user turned the area off
        if cur in columns:
            continue
        wanted = {_norm(n) for n in names}
        found = next((cid for cid, lab in labels.items() if cid in columns and _norm(lab) in wanted), None)
        if found is None:
            found = next((cid for cid in columns if _norm(cid) in wanted), None)
        if found:
            changes[attr] = found
            notes.append(f"{role} column: using '{labels.get(found, found)}' ({found})")
    if rules.time_field not in columns:
        for cand in ("FirstOccurrence", "EventTime", "LastOccurrence"):
            if cand in columns:
                changes["time_field"] = cand
                notes.append(f"Down-time column: using {cand}")
                break
    return (replace(rules, **changes) if changes else rules), notes
