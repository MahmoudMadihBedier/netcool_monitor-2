"""v6 guarantees: never miss what v4 caught; find outages v4/v5 missed; stable alert identity.
Run:  python -m unittest discover tests"""
import random
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.cascade import CascadeReport  # noqa: E402
from core.columns import resolve  # noqa: E402
from core.config import RuleSettings  # noqa: E402
from core.detector import OutageDetector  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402
from core.outage import OutageEngine, OutageTracker  # noqa: E402

T0 = datetime(2026, 10, 31, 14, 0)
REPORT = CascadeReport.load(ROOT / "data" / "CascadedSitesReport_20092026.CSV")
ALL = {5, 4, 3, 2, 1}


def ev(site, hub, area, minute, sev=5, node="BSC80", summary="Down Site"):
    return NetcoolEvent(serial=f"{site}-{minute}", site=site, hub_raw=hub, area=area, region="Delta", node=node,
                        summary=summary, severity=sev, severity_label="", down_at=T0 + timedelta(minutes=minute),
                        last_at=None, clear_time="", fields={})


class NeverWorseThanV4(unittest.TestCase):
    def test_random_2g_like_data(self):
        rnd = random.Random(42)
        chain_sites = sorted(s for s in REPORT.children if REPORT.children[s] and s[:1] in "DKJA")[:400]
        for trial in range(1500):
            evs = []
            hubs = [f"H{rnd.randint(1000, 1030)}" for _ in range(6)]
            areas = ["Haram", "Maadi", "Sharkeyya_Rural"]
            pool = [f"{rnd.choice('DKJA')}{rnd.randint(1000, 9999)}" for _ in range(40)] + rnd.sample(chain_sites, 20)
            for site in set(pool):
                hub = rnd.choice(hubs) + f" 00-{rnd.randint(1, 9)}"
                area = rnd.choice(areas)
                base = rnd.choice([-600, -240, -60, -20, -5, 0, 2, 4])
                evs.append(ev(site, hub, area, base + rnd.randint(0, 6), sev=rnd.choice([5, 5, 4, 3])))
                if rnd.random() < 0.3:     # an older second alarm on the same site
                    evs.append(ev(site, hub, area, base - rnd.randint(30, 1500), sev=4, summary="External Power"))
            rules = RuleSettings(min_sites=4, window_minutes=5)
            v4 = OutageDetector(rules).evaluate(evs)[0]
            v6 = OutageEngine(rules).evaluate(evs, ALL, REPORT, now=T0).outages
            alerting = [set(o.site_ids) for o in v6 if o.level != "WATCH"]
            for inc in v4:
                need = set(inc.site_ids)
                self.assertTrue(any(len(need & s) >= rules.min_sites for s in alerting),
                                f"trial {trial}: v4 found {sorted(need)} at hub {inc.hub}, v6 did not")


class FindsWhatV5Missed(unittest.TestCase):
    def test_old_and_new_outage_same_hub_and_old_alarms(self):
        evs = [ev(s, "H2201 00-1", "Sharkeyya_Rural", -180 + i) for i, s in enumerate(
            ["3634", "3635", "3636", "3637", "3638", "3639"])]
        evs += [ev(s, "H2201 00-2", "Sharkeyya_Rural", i) for i, s in enumerate(
            ["D7001", "D7002", "D7003", "D7004", "D7005"])]
        evs += [ev(s, "H9999 00-1", "Haram", i) for i, s in enumerate(["K8001", "K8002", "K8003", "K8004", "K8005"])]
        evs += [ev(s, "H9999 00-1", "Haram", -1400, sev=4, summary="External Power") for s in ("K8001", "K8002")]
        outs = [o for o in OutageEngine(RuleSettings()).evaluate(evs, ALL, REPORT, now=T0).outages
                if o.level == "OUTAGE"]
        got = sorted((o.hub, o.count, f"{o.start:%H:%M}") for o in outs)
        self.assertEqual(got, [("H2201", 5, "14:00"), ("H2201", 6, "11:00"), ("H9999", 5, "14:00")])


class StableIdentity(unittest.TestCase):
    def test_growing_outage_keeps_one_identity(self):
        r = RuleSettings()
        e, tr = OutageEngine(r), OutageTracker(r)
        base = [ev(f"D50{i}0", "H7 00-1", "Haram", i) for i in range(5)]
        f1, _ = tr.update(e.evaluate(base, ALL, None, now=T0).outages, T0)
        self.assertEqual([k for _, k in f1], ["NEW OUTAGE"])
        more = base + [ev("D5990", "H7 00-1", "Haram", -1)]       # earlier site joins -> start moves
        outs = e.evaluate(more, ALL, None, now=T0).outages
        f2, _ = tr.update(outs, T0 + timedelta(minutes=1))
        self.assertTrue(f2[0][1].startswith("MORE SITES"))
        self.assertEqual(tr.active_count, 1)

    def test_mute_survives_growth(self):
        r = RuleSettings()
        e, tr = OutageEngine(r), OutageTracker(r)
        base = [ev(f"D60{i}0", "H8 00-1", "Haram", i) for i in range(5)]
        outs = e.evaluate(base, ALL, None, now=T0).outages
        tr.update(outs, T0)
        tr.mute(outs[0].key, now=T0)
        more = base + [ev("D6990", "H8 00-1", "Haram", 4)]
        fire, _ = tr.update(e.evaluate(more, ALL, None, now=T0).outages, T0 + timedelta(minutes=1))
        self.assertEqual(fire, [])


class Columns(unittest.TestCase):
    def test_2g_view_labels(self):
        cols = {"SiteID", "Node", "Location", "Sub_Region_Field", "RegionX", "EventTime"}
        labels = {"SiteID": "SiteID", "Location": "HUB", "Sub_Region_Field": "Sub_Region", "RegionX": "Region"}
        eff, notes = resolve(RuleSettings(), cols, labels)
        self.assertEqual((eff.hub_field, eff.area_field, eff.region_field, eff.time_field),
                         ("Location", "Sub_Region_Field", "RegionX", "EventTime"))

    def test_3g_view_unchanged(self):
        cols = {"SiteID", "CI_Organization", "TECHostname", "NetworkAssureHost", "FirstOccurrence"}
        eff, notes = resolve(RuleSettings(), cols, {})
        self.assertEqual(eff, RuleSettings())
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
