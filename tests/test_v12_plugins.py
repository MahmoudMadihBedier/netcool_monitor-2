"""v12 plugins through the kernel only: rules, records, ai, mail, outlook scan, remedy lock."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import Settings, SettingsRepository  # noqa: E402
from core.session_plan import SessionPlan  # noqa: E402
from kernel import Decision, Host, PluginManager, Refresh, Topics  # noqa: E402
from tests.test_v10_ai import outage, row  # noqa: E402


def make_host(d):
    host = Host(Settings(), SettingsRepository(Path(d) / "s.json"), Path(d))
    plans = {1: SessionPlan(purpose="Other", fo_rules=False, alarm_new_rows=True, alarm_count=3)}
    host.plan_of = lambda sid: plans.get(sid)
    host.purpose_of = lambda o: "Down sites (FO outage rules)"
    return host


class Rules(unittest.TestCase):
    def test_rule_alarms_come_through_the_bus(self):
        with tempfile.TemporaryDirectory() as d:
            host = make_host(d)
            alerts = []
            host.bus.subscribe(Topics.ALERT, alerts.append)
            pm = PluginManager(host)
            pm.start(next(m for m in __import__("kernel").read_manifests() if m.name == "rules"))
            evs = [row(str(i), site=f"GD10{i}") for i in range(4)]
            host.bus.publish(Topics.REFRESH, Refresh(1, "HB", "f", "v", "Other", evs, [], {5, 4}, {"0", "1"}))
            self.assertEqual([a.key[1] for a in alerts], ["RULE:NEW", "RULE:COUNT"])
            host.bus.publish(Topics.REFRESH, Refresh(1, "HB", "f", "v", "Other", evs, [], {5, 4}, set()))
            self.assertEqual(len(alerts), 2)                       # count alarm fires once
            host.bus.publish(Topics.REFRESH, Refresh(2, "3G", "f", "v", "x", evs, [], {5, 4}, {"0"}))
            self.assertEqual(len(alerts), 2)                       # session without a plan: nothing
            pm.stop_all()


class RecordsAndAI(unittest.TestCase):
    def test_decision_reaches_records_and_ai(self):
        with tempfile.TemporaryDirectory() as d:
            host = make_host(d)
            pm = PluginManager(host)
            ms = {m.name: m for m in __import__("kernel").read_manifests()}
            for name in ("records", "ai", "mail"):
                pm.start(ms[name])
            o = outage(5)
            host.bus.publish(Topics.REFRESH, Refresh(1, "3G", "f", "v", "x", [row("1")], [o], {5}, first=True))
            host.bus.publish(Topics.DECISION, Decision(o, False, "PA / planned activity", "PA on H0439"))
            db = host.service("records")
            cols, rows = db.query("decisions")
            self.assertEqual(rows[0][cols.index("verdict")], "NOT")
            self.assertEqual(host.service("ai").brain.counts()[1], 1)
            self.assertEqual(len(host.advisors), 1)
            self.assertIn("still learning", host.advice_for(o)[0].text)
            labels = [a.label for a in host.outage_actions]
            self.assertIn("Not an outage", labels)
            self.assertIn("✉ Create outage mail", labels)
            pm.stop_all()


class FakeMail:
    def __init__(self, when, subject, body, sender="PA Team"):
        class R:  # Outlook-like time object
            year, month, day, hour, minute = when.year, when.month, when.day, when.hour, when.minute
        self.ReceivedTime, self.Subject, self.Body, self.SenderName = R(), subject, body, sender


class FakeFolder:
    def __init__(self, mails):
        self.Items = mails

    class _Items(list):
        def Sort(self, *a): pass


class OutlookScan(unittest.TestCase):
    def test_finds_planned_activity_for_the_sites(self):
        from plugins.outlook.plugin import scan
        now = dt.datetime(2026, 11, 16, 3, 0)
        mails = FakeFolder._Items([
            FakeMail(now - dt.timedelta(hours=5), "CRQ000123 planned activity on H4179 tonight", "sites GD1350, GD3563"),
            FakeMail(now - dt.timedelta(hours=6), "Lunch menu", "GD1350 is the best"),                  # no activity word
            FakeMail(now - dt.timedelta(hours=7), "Night maintenance Delta", "sites D9999 only"),         # other sites
            FakeMail(now - dt.timedelta(days=5), "CRQ old planned GD1350", "x")])                        # too old
        folder = FakeFolder(mails)
        hits = scan([folder], ["GD1350", "LD3563"], "H4179 00-3-1", 3, 500, now)
        self.assertEqual(len(hits), 1)
        self.assertIn("CRQ000123", hits[0])
        self.assertEqual(scan([folder], [], "", 3, 500, now), [])


if __name__ == "__main__":
    unittest.main()


class SmartAI(unittest.TestCase):
    """The AI learns the CONDITIONS (night window, planned words, Outlook findings, TT) - fast, time-capped."""

    def _brain(self, d):
        from plugins.ai.brain import Brain
        return Brain(Path(d) / "b.json")

    def test_outlook_findings_and_planned_words_change_the_advice(self):
        from tests.test_v10_ai import wait_trained
        with tempfile.TemporaryDirectory() as d:
            b = self._brain(d)
            act = {"activities": ["15/11 22:10  CRQ planned activity on HUB H2000  (PA team)"]}
            for i in range(7):      # same-looking outages: REAL without a mail, NOT with a planned-activity mail
                b.learn_outage(outage(5 + i % 3, "UtranCell_ServiceUnavailable", "TE", hub=f"H2{i:03d}"), True)
                b.learn_outage(outage(5 + i % 3, "UtranCell_ServiceUnavailable", "TE", hub=f"H2{i:03d}"), False,
                               reason="PA / planned activity", context=act)
            wait_trained(b, 14)
            o = outage(6, "UtranCell_ServiceUnavailable", "TE", hub="H2999")
            with_mail = b.advise(o, context=act)["p"]
            without = b.advise(o, context={"activities": []})["p"]
            self.assertLess(with_mail, without)
            self.assertLess(with_mail, 0.4)
            self.assertGreater(without, 0.6)

    def test_training_respects_the_time_budget(self):
        import time
        from plugins.ai import brain as B
        from plugins.ai.brain import TinyNet
        data = [({k: 1.0 for k in range(i * 7, i * 7 + 40)}, i % 2, 1.0) for i in range(300)]
        t0 = time.monotonic()
        epochs = TinyNet().fit(data, epochs=400, time_budget_s=0.5)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertLess(epochs, 400)
        self.assertGreaterEqual(epochs, 3)
        self.assertLessEqual(B.TRAIN_BUDGET_S, 5)

    def test_experience_file_carries_brain_and_records(self):
        from plugins.records.records import Records
        with tempfile.TemporaryDirectory() as d:
            b1, db1 = self._brain(d), Records(Path(d) / "a.db")
            o = outage(5)
            b1.learn_outage(o, False, reason="Test / new site", note="test site")
            db1.add_decision(o, False, "Test / new site", "test site")
            db1.add_tt(o, "TX", True, "INC000000000777", "", "me", "<secret request>", "<answer>")
            exp = Path(d) / "exp.json"
            b1.export_to(exp, db1)
            text = exp.read_text(encoding="utf-8")
            self.assertIn("INC000000000777", text)
            self.assertNotIn("secret request", text)              # requests / logins never leave the PC
            b2, db2 = self._brain(Path(d) / "x"), Records(Path(d) / "b.db")
            self.assertEqual(b2.import_from(exp, db2), 1)
            self.assertEqual(db2.tt_for(o), "INC000000000777")
            self.assertEqual(db2.query("decisions")[1][0][3], "NOT")
            self.assertEqual(b2.import_from(exp, db2), 0)        # again: nothing duplicated
            self.assertEqual(len(db2.query("tts")[1]), 1)
            db1.close()
            db2.close()

    def test_bad_experience_file_is_ignored_safely(self):
        from plugins.records.records import Records
        with tempfile.TemporaryDirectory() as d:
            db = Records(Path(d) / "c.db")
            bad = {"decisions": {"columns": ["id", "user", "verdict"], "rows": [[1, "x", "NOT"], [2], "junk",
                                                                                 [3, "y", {"a": 1}]]},
                   "tts": {"columns": "oops", "rows": 5}, "outages": [], "sqlite_master": {"columns": ["x"]}}
            self.assertEqual(db.import_experience(bad), 1)        # only the well-formed row, id / user dropped
            cols, rows = db.query("decisions")
            self.assertEqual(rows[0][cols.index("verdict")], "NOT")
            db.close()
