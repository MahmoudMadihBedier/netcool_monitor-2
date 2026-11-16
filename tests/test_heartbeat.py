"""Heartbeat task must give exactly the Result sheet of Heartbeat_task.xlsx (VLOOKUP #N/A sites)."""
import csv
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import clock  # noqa: E402
from plugins.heartbeat.heartbeat import analyze, build_html, shift_name, shift_window, site_list_text, to_tsv  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402

DATA = ROOT / "tests" / "data"


def t(s):   # MOM text '5/19/26, 8:21 AM'  (month/day/year - the "time orientation")
    return datetime.strptime(s, "%m/%d/%y, %I:%M %p") if s else None


def load():
    hb, g = [], []
    with open(DATA / "heartbeat_sample.csv", newline="") as f:
        for r in csv.DictReader(f):
            e = NetcoolEvent(serial="", site=r["SiteID"], hub_raw=r["HUB"], area=r["SubRegion"],
                             region=r["Region"], node=r["Node"], summary=r["Summary"], severity=5,
                             severity_label="Critical", down_at=t(r["FirstOccurrence"]),
                             last_at=t(r["LastOccurrence"]), clear_time="",
                             fields={"TTNumber": r["TTNumber"], "FaultCode": r["FaultCode"]})
            (hb if r["sheet"] == "Heartbeat filter" else g).append(e)
    exp = {}
    for line in (DATA / "heartbeat_expected.txt").read_text().splitlines():
        k, *v = line.split()
        exp[k] = set(v)
    return hb, g, exp


class WorkbookParity(unittest.TestCase):
    def test_same_sites_as_result_sheet(self):
        hb, g, exp = load()
        rep = analyze(hb, g, datetime(2000, 1, 1), datetime(2100, 1, 1), {5})
        self.assertEqual({s.site for s in rep.investigate}, exp["NA"])
        self.assertEqual({s.site for s in rep.excluded}, exp["FOUND"])

    def test_last_shift_only_and_unique(self):
        hb, g, _ = load()
        since, until = datetime(2026, 5, 18, 20, 0), datetime(2026, 5, 19, 8, 30)
        rep = analyze(hb, g, since, until, {5})
        sites = [s.site for s in rep.investigate]
        self.assertEqual(len(sites), len(set(sites)))                 # unique
        self.assertTrue(all(since <= s.last <= until for s in rep.investigate + rep.excluded))
        self.assertIn("L1335", sites)
        self.assertNotIn("G1508", sites)                              # 5/18 6:50 PM - before the shift
        lk = next(s for s in rep.investigate if s.site == "LK5000")   # 3 alarms, 2 TTs
        self.assertEqual(lk.alarms, 3)
        self.assertIn("INC000078130811", lk.tt)
        self.assertEqual(sites, sorted(sites, key=lambda x: -next(s.last for s in rep.investigate
                                                                   if s.site == x).timestamp()))

    def test_severity_filter(self):
        hb, g, _ = load()
        rep = analyze(hb, g, datetime(2000, 1, 1), datetime(2100, 1, 1), {4})
        self.assertEqual(rep.investigate, [])
        self.assertEqual(rep.skipped_sev, rep.read_alarms - 1)       # one row has no SiteID

    def test_outputs(self):
        hb, g, _ = load()
        rep = analyze(hb, g, datetime(2026, 5, 18, 20), datetime(2026, 5, 19, 8, 30), {5, 4},
                      hb_filter="LTE_Heartbeat_Ericsson", ref_filter="3G_Marco")
        html = build_html(rep, "Mahmoud Madih")
        self.assertIn("L1335", html)
        self.assertIn("check each in ENM", html)
        self.assertIn("Already have a TT", html)
        self.assertTrue(to_tsv(rep).startswith("#\tSiteID\tLast heartbeat"))

    def test_shift_window_0630(self):
        night = datetime(2026, 10, 31, 4, 41)            # inside the night shift
        self.assertEqual(shift_window("previous_shift", night),
                         (datetime(2026, 10, 30, 6, 30), datetime(2026, 10, 30, 18, 30)))
        self.assertEqual(shift_window("current_shift", night), (datetime(2026, 10, 30, 18, 30), night))
        day = datetime(2026, 10, 31, 9, 0)               # inside the morning shift
        self.assertEqual(shift_window("previous_shift", day),
                         (datetime(2026, 10, 30, 18, 30), datetime(2026, 10, 31, 6, 30)))
        self.assertEqual(shift_name(datetime(2026, 10, 30, 18, 30)), "Night shift")
        self.assertEqual(shift_name(datetime(2026, 10, 30, 6, 30)), "Morning shift")
        edge = datetime(2026, 10, 31, 6, 30)             # exactly at the change of shift
        self.assertEqual(shift_window("previous_shift", edge),
                         (datetime(2026, 10, 30, 18, 30), datetime(2026, 10, 31, 6, 30)))

    def test_final_list_is_na_without_tt_unique(self):
        hb, g, exp = load()
        rep = analyze(hb, g, datetime(2000, 1, 1), datetime(2100, 1, 1), {5})
        with_tt = {e.site.upper() for e in hb if e.site and e.fields.get("TTNumber")}
        expected = exp["NA"] - with_tt
        got = [s.site for s in rep.sites_to_check]
        self.assertEqual(set(got), expected)
        self.assertEqual(len(got), len(set(got)))
        self.assertEqual(site_list_text(rep).split("\n"), got)

    def test_night_shift_of_workbook(self):
        hb, g, _ = load()
        night = analyze(hb, g, datetime(2026, 5, 18, 18, 30), datetime(2026, 5, 19, 6, 30), {5})
        self.assertEqual(night.sites_to_check, [])                      # every #N/A site has a TT
        self.assertEqual(sorted(s.site for s in night.with_tt), ["LD4082", "LD6668", "LK5000"])
        self.assertNotIn("GD8082", [s.site for s in night.investigate])  # 06:44 = morning shift
        morning = analyze(hb, g, datetime(2026, 5, 19, 6, 30), datetime(2026, 5, 19, 18, 30), {5})
        self.assertEqual([s.site for s in morning.sites_to_check], ["L1335", "LD8078", "GD8082"])


class Clock(unittest.TestCase):
    def tearDown(self):
        clock.set_zone("Asia/Riyadh")

    def test_two_zones_only(self):
        self.assertEqual(clock.set_zone("Africa/Johannesburg"), "Africa/Johannesburg")
        jhb = clock.from_epoch(0)
        self.assertEqual(clock.set_zone("Asia/Riyadh"), "Asia/Riyadh")
        self.assertEqual((clock.from_epoch(0) - jhb).total_seconds(), 3600)
        self.assertEqual(clock.set_zone("Europe/London"), "Asia/Riyadh")   # not allowed -> default
        self.assertEqual(clock.from_epoch(0), datetime(1970, 1, 1, 3, 0))


if __name__ == "__main__":
    unittest.main()
