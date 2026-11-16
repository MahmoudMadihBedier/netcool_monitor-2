"""Run:  python -m unittest discover tests"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import RuleSettings  # noqa: E402
from core.detector import AlertTracker, OutageDetector  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402

T0 = datetime(2026, 10, 29, 14, 0, 0)


def ev(site, hub="H4179 00-3-1", area="Dakahleyya_Rural", minute=0, sev=4, clear=""):
    return NetcoolEvent(serial=site, site=site, hub_raw=hub, area=area, region="Delta", node="RNC",
                        summary="UtranCell_ServiceUnavailable", severity=sev, severity_label="Major",
                        down_at=T0 + timedelta(minutes=minute), last_at=None, clear_time=clear)


class DetectorTest(unittest.TestCase):
    def setUp(self):
        self.d = OutageDetector(RuleSettings(min_sites=4, window_minutes=5))

    def test_four_sites_same_hub_area_time_alerts(self):
        inc, _ = self.d.evaluate([ev("GK2299", minute=0), ev("GD2479", minute=1),
                                  ev("GD3563", "H4179 00-10-2", minute=2), ev("GD1350", minute=3)])
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0].hub, "H4179")
        self.assertEqual(sorted(inc[0].site_ids), ["GD1350", "GD2479", "GD3563", "GK2299"])

    def test_three_sites_no_alert(self):
        inc, groups = self.d.evaluate([ev("A1"), ev("A2", minute=1), ev("A3", minute=2)])
        self.assertEqual(inc, [])
        self.assertEqual(len(groups[0].sites_in_window), 3)

    def test_same_site_twice_counts_once(self):
        inc, _ = self.d.evaluate([ev("A1"), ev("A1", minute=1), ev("A2"), ev("A3")])
        self.assertEqual(inc, [])

    def test_different_area_no_alert(self):
        inc, _ = self.d.evaluate([ev("A1"), ev("A2"), ev("A3"), ev("A4", area="Mansoura")])
        self.assertEqual(inc, [])

    def test_different_hub_no_alert(self):
        inc, _ = self.d.evaluate([ev("A1"), ev("A2"), ev("A3"), ev("A4", hub="H0110 00-2")])
        self.assertEqual(inc, [])

    def test_outside_time_window_no_alert(self):
        inc, _ = self.d.evaluate([ev("A1", minute=0), ev("A2", minute=1), ev("A3", minute=2),
                                  ev("A4", minute=30)])
        self.assertEqual(inc, [])

    def test_clear_and_empty_hub_ignored(self):
        inc, _ = self.d.evaluate([ev("A1"), ev("A2"), ev("A3"), ev("A4", sev=0),
                                  ev("A5", hub="NULL"), ev("A6", clear="10/29/26 2:05 PM")])
        self.assertEqual(inc, [])

    def test_tracker_new_grow_reminder_clear(self):
        tr = AlertTracker(realert_minutes=15)
        base = [ev("A1"), ev("A2"), ev("A3"), ev("A4")]
        inc, _ = self.d.evaluate(base)
        fire, _ = tr.update(inc, T0)
        self.assertEqual(fire[0][1], "NEW OUTAGE")
        fire, _ = tr.update(inc, T0 + timedelta(minutes=1))
        self.assertEqual(fire, [])
        inc, _ = self.d.evaluate(base + [ev("A5", minute=2)])
        fire, _ = tr.update(inc, T0 + timedelta(minutes=2))
        self.assertTrue(fire[0][1].startswith("MORE SITES"))
        fire, _ = tr.update(inc, T0 + timedelta(minutes=20))
        self.assertTrue(fire[0][1].startswith("STILL DOWN"))
        fire, cleared = tr.update([], T0 + timedelta(minutes=21))
        self.assertEqual(cleared, [("H4179", "Dakahleyya_Rural")])


if __name__ == "__main__":
    unittest.main()
