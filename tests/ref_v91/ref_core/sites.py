"""Site naming rules from the FO manual.

The same PHYSICAL site appears with a band prefix per technology:
    D1234   2G 900        CD1234  2G 1800
    GD1234  3G 2100       MD1234  3G U900
    LD1234  4G (all bands)
Region letters (D Delta, K Cairo, J Giza, A Alex, U, R, H, FA, ...) belong to the physical id.
An outage is counted in PHYSICAL sites, so GD1234 + LD1234 + D1234 = 1 site.
"""
from __future__ import annotations

import re
from typing import Optional

TECH_LETTERS = {"C": "2G", "G": "3G", "M": "3G", "L": "4G"}
_REST = re.compile(r"^[A-Z]{0,3}\d{3,5}[A-Z]?$")


def physical_id(site_id: str, known: Optional[set] = None) -> str:
    s = (site_id or "").strip().upper()
    if not s:
        return ""
    if known is not None and s in known:
        return s
    if s[0] in TECH_LETTERS and _REST.match(s[1:]):
        cand = s[1:]
        # 'CD1234' -> 'D1234'; but keep the id unchanged if the stripped form is not a site id
        return cand
    return s


def technology(site_id: str, node: str = "", summary: str = "") -> str:
    s = (site_id or "").strip().upper()
    n = (node or "").strip().upper()
    if s[:1] in TECH_LETTERS and _REST.match(s[1:]):
        return TECH_LETTERS[s[0]]
    if n.startswith("BSC"):
        return "2G"
    if n == "LTE" or "EUTRAN" in (summary or "").upper():
        return "4G"
    if n.startswith("RNC"):
        return "3G"
    return "2G"
