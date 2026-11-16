"""v12 core must decide EXACTLY like v9.1 (the version the team trusts): same outages, same levels, same sites,
same alert reasons, on 600 random shifts with growth over 6 refreshes."""
import random
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "ref_v91"))

from core.config import RuleSettings  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402
from core.outage import OutageEngine, OutageTracker  # noqa: E402
from ref_core.config import RuleSettings as RefRules  # noqa: E402
from ref_core.models import NetcoolEvent as RefEvent  # noqa: E402
from ref_core.outage import OutageEngine as RefEngine, OutageTracker as RefTracker  # noqa: E402

T0 = datetime(2026, 11, 16, 3, 0)


def make_shift(rnd):
    hubs = [f"H{rnd.randint(1000, 1030)}" for _ in range(6)]
    areas = ["Haram", "Maadi", "Zagazig", "Tanta"]
    pool = [f"{rnd.choice('DKJA')}{rnd.randint(1000, 9999)}" for _ in range(45)]
    rows = []
    for i, site in enumerate(pool):
        tech = rnd.choice(["", "G", "L", "C"])
        rows.append(dict(serial=str(i), site=tech + site, hub=rnd.choice(hubs + ["NULL", ""]),
                         area=rnd.choice(areas), minute=rnd.randint(-900, 0), sev=rnd.choice([5, 5, 4, 3, 0]),
                         node=rnd.choice(["BSC80", "RNC52", "LTE"]), summary=rnd.choice(["Down Site", "Power", "LOS"])))
    for k in range(rnd.randint(0, 3)):                    # groups that look like outages
        hub, area, base = rnd.choice(hubs), rnd.choice(areas), rnd.randint(-40, 0)
        for j in range(rnd.randint(3, 7)):
            rows.append(dict(serial=f"g{k}{j}", site=f"D{rnd.randint(1000, 9999)}", hub=hub + " 00-1", area=area,
                             minute=base + rnd.randint(0, 6), sev=rnd.choice([5, 4]), node="BSC80",
                             summary="Down Site"))
    return rows


def ev(cls, r, offset):
    t = T0 + timedelta(minutes=r["minute"] + offset)
    return cls(serial=r["serial"], site=r["site"], hub_raw=r["hub"], area=r["area"], region="Delta", node=r["node"],
               summary=r["summary"], severity=r["sev"], severity_label="", down_at=t, last_at=t, clear_time="",
               fields={})


def digest(outages):
    return sorted((o.level, o.count, tuple(o.site_ids), tuple(o.evidence), o.hub, o.area,
                   o.fault_time.isoformat() if o.fault_time else "") for o in outages)


class CoreParity(unittest.TestCase):
    def test_same_decisions_as_v91(self):
        rnd = random.Random(2026)
        sev = {5, 4, 3, 2, 1}
        for trial in range(600):
            rows = make_shift(rnd)
            new, ref = OutageEngine(RuleSettings()), RefEngine(RefRules())
            tn, tr = OutageTracker(RuleSettings()), RefTracker(RefRules())
            for step in range(6):
                now = T0 + timedelta(minutes=step * 10)
                if step:
                    rows = rows[rnd.randint(0, 2):] + [dict(serial=f"n{step}{j}", site=f"D{rnd.randint(1000, 9999)}",
                                                            hub=rows[-1]["hub"], area=rows[-1]["area"],
                                                            minute=step * 10 - j, sev=5, node="BSC80",
                                                            summary="Down Site") for j in range(rnd.randint(0, 2))]
                a = new.evaluate([ev(NetcoolEvent, r, 0) for r in rows], sev, None, now)
                b = ref.evaluate([ev(RefEvent, r, 0) for r in rows], sev, None, now)
                self.assertEqual(digest(a.outages), digest(b.outages), f"trial {trial} step {step}")
                fa, ca = tn.update(a.outages, now)
                fb, cb = tr.update(b.outages, now)
                self.assertEqual([(r, o.title) for o, r in fa], [(r, o.title) for o, r in fb], f"alerts {trial}/{step}")
                self.assertEqual(ca, cb)


if __name__ == "__main__":
    unittest.main()
