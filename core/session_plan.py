"""What a session is for (its plan). 2G / 3G filters keep the FO-manual defaults; other filters get a plan
from the user. The plan's own alarm rules are checked by the 'rules' plugin."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields


PURPOSES = ["Down sites (FO outage rules)", "Heartbeat", "Power alarms", "Transmission (TX)",
            "Cell / service degradation", "Other"]
# Suggested alarm rules when the user picks a purpose (he can change them).
PURPOSE_DEFAULTS = {
    PURPOSES[0]: dict(fo_rules=True, alarm_new_rows=False, alarm_words=""),
    "Heartbeat": dict(fo_rules=False, alarm_new_rows=True, alarm_words=""),
    "Power alarms": dict(fo_rules=False, alarm_new_rows=False, alarm_words="power, mains, battery, generator"),
    "Transmission (TX)": dict(fo_rules=True, alarm_new_rows=False, alarm_words="microwave, link, los, transmission"),
    "Cell / service degradation": dict(fo_rules=False, alarm_new_rows=True, alarm_words=""),
    "Other": dict(fo_rules=False, alarm_new_rows=True, alarm_words=""),
}


@dataclass
class SessionPlan:
    purpose: str = PURPOSES[0]
    fo_rules: bool = True          # FO outage rules (pre-outage at 4, outage at 5 physical sites)
    alarm_new_rows: bool = False   # alarm on every NEW row (selected severities)
    alarm_words: str = ""          # alarm on NEW rows whose text contains one of these words (comma list)
    alarm_count: int = 0           # alarm when the selected rows reach this number (0 = off)
    note: str = ""                 # the user's own words about the session (the AI reads them)
    asked: bool = False            # the user answered the questions (False = automatic 2G/3G default)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d) -> "SessionPlan":
        names = {f.name for f in fields(cls)}
        p = cls(**{k: v for k, v in dict(d or {}).items() if k in names})
        p.alarm_count = max(0, int(p.alarm_count or 0))
        return p

    def words(self) -> list:
        return [w.strip().lower() for w in self.alarm_words.replace("،", ",").split(",") if w.strip()]

    def summary(self) -> str:
        parts = ["FO outage rules"] if self.fo_rules else []
        if self.alarm_new_rows:
            parts.append("every new alarm")
        if self.words():
            parts.append("new alarms with: " + ", ".join(self.words()))
        if self.alarm_count:
            parts.append(f"{self.alarm_count}+ rows")
        return f"{self.purpose}  ·  alarm on: " + (" · ".join(parts) or "nothing (watch only)")


def is_default_filter(filter_id: str) -> bool:
    """2G / 3G filters (2G_Marco, 3G_Marco, Copyof3G_Marco ...) keep the FO defaults."""
    low = (filter_id or "").lower()
    return "2g" in low or "3g" in low


