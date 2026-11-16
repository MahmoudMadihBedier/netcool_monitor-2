"""v10: AI brain (NLP + neural network), session purpose and custom alarm rules, MP3 sound backend."""
import json
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.ai import nlp  # noqa: E402
from plugins.ai.brain import MIN_EACH, Brain  # noqa: E402
from core.outage import Outage, PhysicalSite  # noqa: E402

T0 = datetime(2026, 11, 13, 10, 0)


def outage(n=5, summary="UtranCell_ServiceUnavailable", fc="TE", area="Haram", hub="H0439", ev=("HUB",)):
    sites = [PhysicalSite(pid=f"D{1000 + i}", first_down=T0 + timedelta(minutes=i), summary=summary,
                          fault_code=fc, area=area, hub=hub, techs={"2G"}) for i in range(n)]
    return Outage(key=("HUB", hub, area, "x"), level="OUTAGE" if n >= 5 else "PRE-OUTAGE", confidence="HIGH",
                  evidence=list(ev), sites=sites, hub=hub, area=area, region="Giza", node="BSC83",
                  tech_counts={"2G": n}, team="TX")


def teach(b, rnd, real_n=8, not_n=8):
    for i in range(real_n):
        b.learn_outage(outage(rnd.randint(5, 12), "Microwave link down LOS", "TX", area=rnd.choice(["Haram", "Maadi"]),
                              hub=f"H{rnd.randint(1000, 1100)}"), True, "Down sites (FO outage rules)")
    for i in range(not_n):
        b.learn_outage(outage(rnd.randint(4, 6), "Site planned activity upgrade", "PA", area="Zagazig",
                              hub=f"H{rnd.randint(2000, 2100)}"), False, "Down sites (FO outage rules)",
                       "PA / planned activity", "PA team working on the HUB")


def wait_trained(b, n, timeout=20):
    end = time.time() + timeout
    while time.time() < end and not (b.net is not None and b.trained_on >= n):
        time.sleep(0.05)


class NLP(unittest.TestCase):
    def test_english_arabic_and_site_ids(self):
        self.assertEqual(nlp.tokens("Site GD1234 down due to POWER"), ["site", "gd####", "down", "power"])
        self.assertEqual(nlp.tokens("نشاط مخطط على الموقع"), ["نشاط", "مخطط", "الموقع"])

    def test_similar_texts_are_close(self):
        a = nlp.normalize(nlp.text_features("planned activity on site", "s"))
        b = nlp.normalize(nlp.text_features("site planned activity", "s"))
        c = nlp.normalize(nlp.text_features("microwave link down", "s"))
        self.assertGreater(nlp.cosine(a, b), nlp.cosine(a, c))


class BrainLearns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "brain.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_still_learning_below_minimum(self):
        b = Brain(self.path)
        b.learn_outage(outage(), True)
        adv = b.advise(outage())
        self.assertIsNone(adv["p"])
        self.assertIn("still learning", adv["text"])

    def test_learns_pattern_and_explains(self):
        b = Brain(self.path)
        teach(b, random.Random(1))
        wait_trained(b, 16)
        not_like = b.advise(outage(5, "Site planned activity upgrade", "PA", area="Zagazig", hub="H2050"))
        real_like = b.advise(outage(7, "Microwave link down LOS", "TX", area="Maadi", hub="H1050"))
        self.assertLess(not_like["p"], 0.25)
        self.assertIn("NOT an outage", not_like["text"])
        self.assertIn("PA / planned activity", not_like["text"])
        self.assertGreater(real_like["p"], 0.75)
        self.assertIn("REAL", real_like["text"])

    def test_memory_survives_restart_and_import_merges(self):
        b = Brain(self.path)
        teach(b, random.Random(2), 5, 5)
        b2 = Brain(self.path)                                  # restart
        self.assertEqual(b2.counts()[:2], (5, 5))
        exp = Path(self.tmp.name) / "team.json"
        b2.export_to(exp)
        other = Brain(Path(self.tmp.name) / "other.json")
        self.assertEqual(other.import_from(exp), 10)
        self.assertEqual(other.import_from(exp), 0)             # same file again: nothing duplicated
        self.assertEqual(other.counts()[:2], (5, 5))

    def test_corrupt_or_foreign_file_is_safe(self):
        self.path.write_text("{not json", encoding="utf-8")
        b = Brain(self.path)                                     # starts fresh, keeps the bad file aside
        self.assertEqual(b.counts(), (0, 0, 0))
        self.assertTrue(any(p.name.startswith("brain.bad_") for p in self.path.parent.iterdir()))
        evil = Path(self.tmp.name) / "evil.json"
        evil.write_text(json.dumps({"version": 1, "outages": [
            {"id": "x", "label": 7, "feats": {"1": 1}},                       # bad label
            {"id": "y", "label": 1, "feats": {"999999": 1, "-5": 2}}]}), encoding="utf-8")   # slots out of range
        self.assertEqual(b.import_from(evil), 0)
        with self.assertRaises(ValueError):
            evil.write_text(json.dumps({"version": 99}), encoding="utf-8")
            b.import_from(evil)

    def test_purpose_guess(self):
        b = Brain(self.path)
        self.assertEqual(b.guess_purpose("LTE_Heartbeat_Ericsson:group:X", "v")[0], "Heartbeat")
        self.assertEqual(b.guess_purpose("Weird_Filter_1", "v")[0], "")
        self.assertEqual(b.guess_purpose("Weird_Filter_1", "v", "Correlated External Power Alarm")[0], "")
        b.learn_purpose("Alex_Generators:group:SMC", "v", "Power alarms", "generator fuel low")
        self.assertEqual(b.guess_purpose("Alex_Generators_2:group:SMC", "v")[0], "Power alarms")


# ------------------------------------------------------------------ session purpose + own alarm rules
from core.models import NetcoolEvent  # noqa: E402
from core.session_plan import PURPOSE_DEFAULTS, SessionPlan, is_default_filter  # noqa: E402
from plugins.rules.rules import RuleChecker  # noqa: E402


def row(serial, site="GD1", summary="Heartbeat failure", sev=5):
    return NetcoolEvent(serial=serial, site=site, hub_raw="", area="A", region="R", node="N", summary=summary,
                        severity=sev, severity_label="Critical", down_at=T0, last_at=T0, clear_time="",
                        fields={"FaultCode": ""})


class Rules(unittest.TestCase):
    def test_2g_3g_keep_defaults(self):
        for f in ("2G_Marco:group:X", "3G_Marco:group:X", "Copyof3G_Marco:group:X"):
            self.assertTrue(is_default_filter(f), f)
        for f in ("LTE_Heartbeat_Ericsson:group:X", "Power_Alarms:group:X", ""):
            self.assertFalse(is_default_filter(f), f)

    def test_new_rows_words_and_count(self):
        plan = SessionPlan(purpose="Other", fo_rules=False, alarm_new_rows=True, alarm_words="mains, battery",
                           alarm_count=3)
        c = RuleChecker(plan)
        rows = [row("1"), row("2", summary="Mains failure"), row("3", sev=0)]
        keys = [k for k, _, _ in c.check(rows, {"1", "2", "3"}, {5, 4})]
        self.assertEqual(keys, ["NEW", "WORDS"])                       # Clear row (sev 0) not selected
        self.assertEqual(c.check(rows, set(), {5, 4}), [])              # nothing new -> no alarm
        many = [row(str(i)) for i in range(4)]
        self.assertEqual([k for k, _, _ in c.check(many, set(), {5})], ["COUNT"])
        self.assertEqual(c.check(many, set(), {5}), [])                 # once, not every refresh
        c.check(many[:1], set(), {5})                                   # back below the limit
        self.assertEqual([k for k, _, _ in c.check(many, set(), {5})], ["COUNT"])

    def test_plan_roundtrip_and_arabic_comma(self):
        p = SessionPlan.from_dict({"purpose": "Power alarms", "alarm_words": "power، battery", "junk": 1,
                                   "alarm_count": -4})
        self.assertEqual(p.words(), ["power", "battery"])
        self.assertEqual(p.alarm_count, 0)
        self.assertEqual(SessionPlan.from_dict(p.to_dict()), p)
        self.assertIn("Heartbeat", PURPOSE_DEFAULTS)

    def test_worker_skips_outages_for_non_fo_plan_and_publishes_refresh(self):
        import queue
        from core import messages as m
        from core.config import Settings, SettingsRepository
        from core.worker import SessionWorker
        from kernel import Host, Topics
        with tempfile.TemporaryDirectory() as d:
            host = Host(Settings(), SettingsRepository(Path(d) / "s.json"), Path(d))
            got = []
            host.bus.subscribe(Topics.REFRESH, got.append)
            bus = queue.Queue()
            w = SessionWorker(1, "HB", client=None, settings=Settings(), filter_id="LTE_Heartbeat", view_id="v",
                              datasource="d", bus=bus, plan=SessionPlan(purpose="Heartbeat", fo_rules=False),
                              severities={5, 4}, host=host)
            evs = [row(str(i), site=f"GD10{i}") for i in range(6)]          # would be an outage by FO rules
            w._evaluate(evs, 6, set(), fresh=True, diff=({"0", "1"}, set(), []))
            msgs = [bus.get_nowait() for _ in range(bus.qsize())]
            snap = next(x for x in msgs if isinstance(x, m.Snapshot))
            self.assertEqual(snap.outages, [])
            self.assertEqual(len(got), 1)
            self.assertEqual((got[0].sid, got[0].purpose, got[0].new_ids), (1, "Heartbeat", {"0", "1"}))


# ------------------------------------------------------------------ own .mp3 / .wav alarm
class OwnSound(unittest.TestCase):
    def test_install_checks_type_and_size(self):
        from core import sound
        with tempfile.TemporaryDirectory() as d:
            sound.SOUND_DIR = Path(d) / "sounds"
            bad = Path(d) / "x.exe"
            bad.write_bytes(b"MZ")
            with self.assertRaises(ValueError):
                sound.install_sound(bad)
            empty = Path(d) / "e.mp3"
            empty.write_bytes(b"")
            with self.assertRaises(ValueError):
                sound.install_sound(empty)
            ok = Path(d) / "siren.mp3"
            ok.write_bytes(b"ID3" + b"\0" * 100)
            self.assertEqual(sound.install_sound(ok).parent, sound.SOUND_DIR)

    def test_volume_scales_builtin_alarm(self):
        import array, io, wave
        from core.sound import _alarm_bytes

        def peak(v):
            with wave.open(io.BytesIO(_alarm_bytes(v))) as w:
                return max(abs(x) for x in array.array("h", w.readframes(w.getnframes())))
        self.assertGreater(peak(100), peak(30) * 2.5)

    def test_mp3_loop_through_mci(self):
        import threading
        from core.sound import SoundPlayer
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "siren.mp3"
            mp3.write_bytes(b"ID3" + b"\0" * 100)
            p = SoundPlayer(None, str(mp3), volume=40)
            self.assertTrue(p.custom)
            sent = []

            def fake_mci(cmd, buf, n, cb):
                sent.append(cmd)
                if cmd.startswith("status"):
                    buf.value = "playing" if sum(c.startswith("status") for c in sent) % 3 else "stopped"
                return 0
            p._mci = fake_mci
            p._want.set()
            t = threading.Thread(target=p._play_custom_loop)
            t.start()
            time.sleep(0.8)
            p.stop()                       # acknowledge -> loop ends, device closed
            t.join(2)
            self.assertFalse(t.is_alive())
            self.assertTrue(sent[0].startswith('open "') and "mpegvideo" in sent[0])
            self.assertIn("setaudio nsm_alarm volume to 400", sent)
            self.assertGreaterEqual(sum(c.startswith("play") for c in sent), 2)   # repeats until acknowledged
            self.assertEqual(sent[-2:], ["stop nsm_alarm", "close nsm_alarm"])

    def test_unplayable_file_reports_error(self):
        from core.sound import SoundPlayer
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "broken.mp3"
            mp3.write_bytes(b"x")
            p = SoundPlayer(None, str(mp3))
            p._mci = lambda cmd, buf, n, cb: 263 if cmd.startswith("open") else 0
            p._want.set()
            self.assertFalse(p._play_custom_loop())
            self.assertIn("MCI error 263", p.last_error)


if __name__ == "__main__":
    unittest.main()
