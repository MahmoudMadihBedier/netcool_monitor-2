"""Rules plugin: a session's own alarm rules (every new row, rows with words, row count)."""
from __future__ import annotations

from core.session_plan import SessionPlan


class RuleChecker:
    """Evaluates a plan's own alarm rules on each fresh read. Remembers the row-count alarm so it fires once
    when the threshold is crossed and again only after the count went back below it."""

    def __init__(self, plan: SessionPlan):
        self.plan = plan
        self._count_on = False

    def check(self, events: list, new_ids: set, severities: set) -> list:
        """-> [(key, title, body)] alarms to raise now."""
        p, out = self.plan, []
        rows = [e for e in events if e.severity in severities]
        new = [e for e in rows if e.serial in new_ids]
        words = p.words()
        if p.alarm_new_rows and new:
            out.append(("NEW", f"{len(new)} new alarm(s)", _rows_text(new)))
        if words and new:
            hit = [e for e in new if any(w in f"{e.summary} {(e.fields or {}).get('FaultCode', '')} {e.site}".lower()
                                         for w in words)]
            if hit:
                out.append(("WORDS", f"{len(hit)} new alarm(s) matching: {', '.join(words)}", _rows_text(hit)))
        if p.alarm_count:
            if len(rows) >= p.alarm_count and not self._count_on:
                self._count_on = True
                out.append(("COUNT", f"{len(rows)} rows (limit {p.alarm_count})", _rows_text(rows[:15])))
            elif len(rows) < p.alarm_count:
                self._count_on = False
        return out


def _rows_text(rows: list) -> str:
    lines = [f"  {e.site or '-':<9} {e.severity_label:<9} {(e.down_at or e.last_at):%H:%M:%S}  {e.summary[:60]}"
             if (e.down_at or e.last_at) else f"  {e.site or '-':<9} {e.severity_label:<9} {e.summary[:60]}"
             for e in rows[:20]]
    if len(rows) > 20:
        lines.append(f"  ... and {len(rows) - 20} more")
    return "\n".join(lines)
