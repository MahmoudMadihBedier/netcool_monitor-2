"""Run:  python -m unittest discover tests"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.cascade import CascadeReport  # noqa: E402
from core.config import MailSettings, RuleSettings  # noqa: E402
from plugins.mail.mail import build_mail, global_tt_text  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402
from core.outage import OutageEngine, OutageTracker  # noqa: E402
from core.sites import physical_id, technology  # noqa: E402

T0 = datetime(2026, 10, 30, 14, 0, 0)
REPORT = CascadeReport.load(ROOT / "data" / "CascadedSitesReport_20092026.CSV")
SEV = {5, 4}


def ev(site, hub="", area="Sharkeyya_Rural", minute=0, sev=4, node="RNC52", fc="", summary="down"):
    return NetcoolEvent(serial=site, site=site, hub_raw=hub, area=area, region="Delta", node=node,
                        summary=summary, severity=sev, severity_label="", down_at=T0 + timedelta(minutes=minute),
                        last_at=T0 + timedelta(minutes=minute), clear_time="", fields={"FaultCode": fc})


class SitesTest(unittest.TestCase):
    def test_physical(self):
        self.assertEqual(physical_id("GD1234"), "D1234")
        self.assertEqual(physical_id("CD1234"), "D1234")
        self.assertEqual(physical_id("LK3523"), "K3523")
        self.assertEqual(physical_id("L4982"), "4982")
        self.assertEqual(physical_id("D1234"), "D1234")
        self.assertEqual(technology("LD1234"), "4G")
        self.assertEqual(technology("0070", "BSC80"), "2G")


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.e = OutageEngine(RuleSettings())

    def test_chain_outage_counts_physical_and_finds_foc(self):
        # chain 3635 -> 3636 -> 3637 -> 3638 -> 3639 (+3086); 3G and 4G of same site count once
        evs = [ev("G3635", minute=0), ev("L3635", minute=0), ev("G3636", minute=1), ev("G3637", minute=2),
               ev("G3638", minute=3), ev("G3639", minute=4), ev("L3639", minute=4)]
        res = self.e.evaluate(evs, SEV, REPORT)
        o = res.outages[0]
        self.assertEqual(o.level, "OUTAGE")
        self.assertEqual(o.count, 5)
        self.assertEqual(o.foc, "3635")
        self.assertEqual(o.site_ids[0], "3635")
        self.assertEqual(o.color, "YELLOW")
        self.assertEqual(o.fault_time, T0 + timedelta(minutes=4))
        self.assertIn("CHAIN", o.evidence)
        self.assertEqual(o.tech_counts.get("4G"), 2)

    def test_foc_not_in_mom_suggests_pa_nfm(self):
        evs = [ev("G3637"), ev("G3638", minute=1), ev("G3639", minute=1), ev("G3086", minute=2)]
        o = self.e.evaluate(evs, SEV, REPORT).outages[0]
        self.assertEqual(o.level, "PRE-OUTAGE")
        self.assertEqual(o.foc, "3635")
        self.assertFalse(o.foc_in_mom)
        self.assertIn("3636", o.chain_unseen)
        self.assertTrue(o.team.startswith("PA"))

    def test_hub_rule_still_works_without_report(self):
        evs = [ev(f"GD9{i}00", hub=f"H4179 00-{i}", minute=i) for i in range(4)]
        o = self.e.evaluate(evs, SEV, None).outages[0]
        self.assertEqual((o.level, o.hub, o.confidence), ("PRE-OUTAGE", "H4179", "HIGH"))

    def test_node_only_is_possible(self):
        evs = [ev(f"GX7{i}11", node="RNC40", minute=i) for i in range(5)]
        o = self.e.evaluate(evs, SEV, REPORT).outages[0]
        self.assertEqual((o.confidence, o.evidence), ("MEDIUM", ["NODE"]))

    def test_severity_selection(self):
        evs = [ev(f"GD9{i}00", hub="H1", minute=i, sev=3) for i in range(5)]
        self.assertEqual(self.e.evaluate(evs, SEV, None).outages, [])
        self.assertEqual(self.e.evaluate(evs, {3}, None).outages[0].level, "OUTAGE")

    def test_power_foc_goes_to_nfm(self):
        evs = [ev("G3635", fc="Power", summary="Correlated External Power Alarm")] + \
              [ev(f"G{s}", minute=1) for s in ("3636", "3637", "3638", "3639")]
        self.assertEqual(self.e.evaluate(evs, SEV, REPORT).outages[0].team, "NFM")


class TrackerTest(unittest.TestCase):
    def test_flow(self):
        r = RuleSettings()
        e, tr = OutageEngine(r), OutageTracker(r)
        four = [ev(f"GD9{i}00", hub="H9", minute=i) for i in range(4)]
        fire, _ = tr.update(e.evaluate(four, SEV, None, now=T0).outages, T0)
        self.assertEqual(fire[0][1], "PRE-OUTAGE")
        five = four + [ev("GD9900", hub="H9", minute=4)]
        fire, _ = tr.update(e.evaluate(five, SEV, None).outages, T0 + timedelta(minutes=5))
        self.assertEqual(fire[0][1], "ESCALATED TO OUTAGE")
        fire, _ = tr.update(e.evaluate(five, SEV, None).outages, T0 + timedelta(minutes=20))
        self.assertTrue(any("GLOBAL TT" in k for _, k in fire))
        _, cleared = tr.update([], T0 + timedelta(minutes=21))
        self.assertEqual(cleared, [])                       # needs 2 empty refreshes
        _, cleared = tr.update([], T0 + timedelta(minutes=22))
        self.assertEqual(len(cleared), 1)

    def test_mute(self):
        r = RuleSettings()
        e, tr = OutageEngine(r), OutageTracker(r)
        outs = e.evaluate([ev(f"GD9{i}00", hub="H9", minute=i) for i in range(5)], SEV, None).outages
        tr.mute(outs[0].key, now=T0)
        self.assertEqual(tr.update(outs, T0)[0], [])


class MailTest(unittest.TestCase):
    def test_mail_like_macro(self):
        evs = [ev("G3635"), ev("G3636", minute=1), ev("G3637", minute=2), ev("G3638", minute=3)]
        o = OutageEngine(RuleSettings()).evaluate(evs, SEV, REPORT).outages[0]
        m = build_mail(o, MailSettings(), now=T0)
        self.assertEqual(m.subject, "30-Oct-2026 || Downsites")
        self.assertIn("5TH site", m.html)
        self.assertIn("#FFFF00", m.html)
        self.assertEqual(m.rows[0][1], "G3635")
        self.assertTrue(m.tsv.startswith("LastOccurrence\tSiteID"))
        self.assertIn("3635", global_tt_text(o))


if __name__ == "__main__":
    unittest.main()
