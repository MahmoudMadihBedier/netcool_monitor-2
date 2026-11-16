"""v9: live stream (diffs, parallel paging), Clear never counted, MOM-in-Edge conflict, alarm sound, settings."""
import io
import queue
import sys
import time
import unittest
import wave
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import RuleSettings, ServerSettings, Settings, migrate  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402
from core.netcool_api import NetcoolClient, SessionExpired, conflict_form  # noqa: E402
from core.outage import OutageEngine  # noqa: E402
from core.transport import Response, Transport, TransportError  # noqa: E402

T0 = datetime(2026, 11, 3, 10, 0)


def ev(site, hub="H4179 00-1", area="Dakahleyya_Rural", minute=0, sev=4, serial=None, tally="1"):
    return NetcoolEvent(serial=serial or site, site=site, hub_raw=hub, area=area, region="Delta", node="RNC52",
                        summary="down", severity=sev, severity_label="", down_at=T0 + timedelta(minutes=minute),
                        last_at=T0 + timedelta(minutes=minute), clear_time="", fields={"Tally": tally})


# ------------------------------------------------------------------ Clear
class ClearIsNeverDown(unittest.TestCase):
    def test_clear_rows_do_not_make_outages(self):
        eng = OutageEngine(RuleSettings())
        rows = [ev(s, sev=0) for s in ("GD1001", "GD1002", "GD1003", "GD1004", "GD1005")]
        res = eng.evaluate(rows, {0, 5, 4}, None, T0 + timedelta(minutes=2))
        self.assertEqual([o for o in res.outages if o.level != "WATCH"], [])
        self.assertEqual(res.used_rows, 0)

    def test_only_clear_selected_gives_clear_message(self):
        eng = OutageEngine(RuleSettings())
        res = eng.evaluate([ev("GD1001", sev=0)], {0}, None, T0)
        self.assertTrue(any("never counted" in c.text for c in res.checks))

    def test_major_still_detected_next_to_clear(self):
        eng = OutageEngine(RuleSettings())
        rows = [ev(s, sev=4) for s in ("GD1001", "GD1002", "GD1003", "GD1004", "GD1005")]
        rows += [ev(f"GD20{i}1", sev=0) for i in range(5)]
        res = eng.evaluate(rows, {5, 4, 3, 2, 1, 0}, None, T0 + timedelta(minutes=2))
        self.assertEqual(res.outages[0].level, "OUTAGE")
        self.assertEqual(res.outages[0].count, 5)


# ------------------------------------------------------------------ live diff
class LiveDiff(unittest.TestCase):
    def _worker(self):
        from core.worker import SessionWorker
        bus = queue.Queue()
        w = SessionWorker(1, "S", client=None, settings=Settings(), filter_id="f", view_id="v", datasource="d",
                          bus=bus)
        return w, bus

    def test_new_changed_removed(self):
        w, bus = self._worker()
        a = [ev("GD1", serial="1"), ev("GD2", serial="2"), ev("GD3", serial="3")]
        self.assertEqual(w._diff(a, True), (set(), set(), []))                    # first read: no marks
        b = [ev("GD1", serial="1"), ev("GD2", serial="2", tally="5"), ev("GD4", serial="4")]
        new, changed, removed = w._diff(b, True)
        self.assertEqual(new, {"4"})
        self.assertEqual(changed, {"2"})
        self.assertEqual([e.site for e in removed], ["GD3"])

    def test_incomplete_read_never_reports_removed(self):
        w, _ = self._worker()
        w._diff([ev("GD1", serial="1"), ev("GD2", serial="2")], True)
        new, changed, removed = w._diff([ev("GD1", serial="1")], False)
        self.assertEqual(removed, [])
        new, changed, removed = w._diff([ev("GD1", serial="1"), ev("GD2", serial="2")], True)
        self.assertEqual((new, removed), (set(), []))                              # reference kept

    def test_interval_limits(self):
        w, _ = self._worker()
        w.set_interval(3)
        self.assertEqual(w.interval, 10)
        w.set_interval(15)
        self.assertEqual(w.interval, 15)
        w.set_interval("x")
        self.assertEqual(w.interval, 20)


# ------------------------------------------------------------------ live refresh = fresh dataset
class CachingClient:
    """Behaves like MOM's server: a dataset (clientId) keeps its first rows unless read with first=True."""
    def __init__(self):
        from core.models import FetchResult
        self.FR, self.calls, self.released, self.open, self.version, self.fail = FetchResult, [], [], set(), 1, False
        self.cache = {}

    def fetch_events(self, cid, f, v, ds, page_size=150, first=False, max_rows=20000):
        self.calls.append((cid, first))
        if self.fail:
            self.open.add(cid)
            raise RuntimeError("network down")
        self.open.add(cid)
        if first or cid not in self.cache:                # clearCache -> current MOM rows, else the old copy
            self.cache[cid] = [{"id": str(i), "properties": [
                {"id": "SiteID", "value": f"GD{i}"}, {"id": "Severity", "value": 5},
                {"id": "FirstOccurrence", "value": 1_700_000_000_000}]} for i in range(self.version)]
        rows = self.cache[cid]
        return self.FR(rows=rows, total=len(rows), columns={"SiteID", "Severity", "FirstOccurrence"})

    def release(self, cid, f, v, ds):
        self.released.append(cid)
        self.open.discard(cid)


class LiveRefresh(unittest.TestCase):
    def _worker(self):
        from core.worker import SessionWorker
        c = CachingClient()
        w = SessionWorker(1, "S", client=c, settings=Settings(), filter_id="f", view_id="v", datasource="d",
                          bus=queue.Queue())
        return w, c

    def test_every_refresh_reads_new_data(self):
        w, c = self._worker()
        w._poll()
        self.assertEqual(len(w._last_events), 1)
        c.version = 3                                    # MOM got 2 new rows
        w._poll()
        self.assertEqual(len(w._last_events), 3)         # v9 bug: stayed at 1 (cached dataset)
        self.assertTrue(all(first for _, first in c.calls))
        self.assertEqual(len({cid for cid, _ in c.calls}), 2)

    def test_previous_dataset_released_only_one_open(self):
        w, c = self._worker()
        for _ in range(5):
            w._poll()
        self.assertEqual(c.open, {w.client_id})          # exactly one dataset kept on the server
        self.assertEqual(len(c.released), 4)

    def test_failed_read_leaves_nothing_behind(self):
        w, c = self._worker()
        w._poll()
        good = w.client_id
        c.fail = True
        with self.assertRaises(RuntimeError):
            w._poll()
        self.assertEqual(c.open, {good})                 # the half-open new dataset was released
        self.assertEqual(w.client_id, good)              # the last good data stays in use
        c.fail = False
        w._poll()
        self.assertEqual(c.open, {w.client_id})


# ------------------------------------------------------------------ parallel paging
class FakeServer(Transport):
    """Pages of a filter; optional short page; counts calls."""
    name = "fake"

    def __init__(self, total=430, cap=None, logged_out_once=False):
        self.total, self.cap, self.calls, self.many = total, cap, 0, 0
        self.logged_out_once = logged_out_once

    def _page(self, params):
        import json
        if "originItems" not in params.get("_path", ""):
            pass
        st, c = int(params.get("start", 0)), int(params.get("count", 150))
        if self.cap:
            c = min(c, self.cap)
        items = [{"id": str(i), "properties": [{"id": "Serial", "value": i}]}
                 for i in range(st, min(st + c, self.total))]
        return json.dumps({"items": items, "totalRows": self.total})

    def request(self, method, url, params=None, data=None, headers=None, timeout=90):
        self.calls += 1
        if self.logged_out_once:
            self.logged_out_once = False
            return Response(200, url.replace("datasets", "logon.jsp"), "text/html", "<form j_security_check>")
        return Response(200, url, "application/json", self._page(params or {}))

    def request_many(self, reqs, timeout=90, limit=6):
        self.many += 1
        return [self.request(m, u, params=p) for m, u, p in reqs]

    def clear_cookies(self):
        pass


class ParallelPaging(unittest.TestCase):
    def _client(self, t):
        c = NetcoolClient(ServerSettings(base_url="http://x"), transport=t)
        c._user, c._password = "u", "p"
        return c

    def test_all_rows_read_once(self):
        t = FakeServer(total=430)
        res = self._client(t).fetch_events("c", "f", "v", "d", page_size=150)
        self.assertEqual(len(res.rows), 430)
        self.assertTrue(res.complete)
        self.assertEqual(t.many, 1)                     # pages 2..3 in ONE round trip

    def test_server_caps_page_size_falls_back(self):
        t = FakeServer(total=430, cap=100)              # server returns only 100 per page
        res = self._client(t).fetch_events("c", "f", "v", "d", page_size=150)
        self.assertEqual(len(res.rows), 430)
        self.assertEqual(len({r["id"] for r in res.rows}), 430)
        self.assertTrue(res.complete)

    def test_small_filter_single_call(self):
        t = FakeServer(total=40)
        res = self._client(t).fetch_events("c", "f", "v", "d", page_size=150)
        self.assertEqual(len(res.rows), 40)
        self.assertEqual(t.many, 0)


# ------------------------------------------------------------------ same user in Edge
WEBSPHERE_CONFLICT = """<html><body><p>Another user is currently logged in with the same user ID.</p>
<form method="post" action="secure/securelogon.do"><input type="hidden" name="csrf" value="abc">
<input type="radio" name="action" value="forceLogin" checked>Log out the user with the same user ID in the other session
<input type="radio" name="action" value="cancel">Return to the Log in page
<input type="submit" name="submit" value="OK"></form></body></html>"""
LOGIN_PAGE = """<html><body>Log in<form action="j_security_check" method="post">
<input name="j_username"><input type="password" name="j_password"><input type="submit" value="Log in">
</form></body></html>"""


class SameUser(unittest.TestCase):
    def test_conflict_page_is_continued(self):
        action, method, data = conflict_form(WEBSPHERE_CONFLICT)
        self.assertEqual(action, "secure/securelogon.do")
        self.assertEqual(data["action"], "forceLogin")
        self.assertEqual(data["csrf"], "abc")

    def test_normal_pages_are_not_touched(self):
        self.assertIsNone(conflict_form(LOGIN_PAGE))
        self.assertIsNone(conflict_form("<html>Login failed: wrong password</html>"))
        self.assertIsNone(conflict_form(""))

    def test_kicked_repeatedly_backs_off(self):
        c = NetcoolClient(ServerSettings(base_url="http://x"), transport=FakeServer())
        c._user, c._password = "u", "p"
        notes = []
        c.on_conflict = notes.append
        logins = []
        c._do_login = lambda u, p: (logins.append(1), setattr(c, "_generation", c._generation + 1),
                                    setattr(c, "_last_login", time.monotonic()))
        c.relogin(c.generation)                          # 1st lost session: sign in again at once
        self.assertEqual(len(logins), 1)
        with self.assertRaises(SessionExpired):          # 2nd within 10 min: wait, tell the user
            c.relogin(c.generation)
        self.assertEqual(len(logins), 1)
        self.assertTrue(notes and "Open MOM" in notes[-1])
        c._last_login -= 61                              # a minute later it signs in again
        c.relogin(c.generation)
        self.assertEqual(len(logins), 2)

    def test_same_generation_counted_once(self):
        c = NetcoolClient(ServerSettings(base_url="http://x"), transport=FakeServer())
        c._user, c._password = "u", "p"
        c._do_login = lambda u, p: setattr(c, "_generation", c._generation + 1)
        g = c.generation
        c.relogin(g)
        c.relogin(g)          # a second session thread with the old generation: no new login, no extra kick
        self.assertEqual(len(c._kicks), 1)


# ------------------------------------------------------------------ alarm sound
class Sound(unittest.TestCase):
    def test_wav_is_valid_and_loud(self):
        from core.sound import _alarm_bytes
        with wave.open(io.BytesIO(_alarm_bytes())) as w:
            self.assertEqual(w.getframerate(), 22050)
            self.assertGreater(w.getnframes(), 22050)          # > 1 s of alarm

    def test_start_stop_restart(self):
        from core.sound import SoundPlayer

        class Root:
            def __init__(self):
                self.bells, self.jobs = 0, []

            def bell(self):
                self.bells += 1

            def after(self, ms, fn):
                self.jobs.append(fn)

        r = Root()
        p = SoundPlayer(r)
        p._win = False
        p.start()
        self.assertTrue(p.playing)
        p.stop()
        self.assertFalse(p.playing)
        while r.jobs:                                   # bell loop ends after stop
            r.jobs.pop(0)()
        p.start()                                       # and can start again (old bug: stuck 'playing')
        self.assertTrue(p.playing)
        self.assertGreaterEqual(r.bells, 2)
        p.set_muted(True)
        self.assertFalse(p.playing)
        p.start()
        self.assertFalse(p.playing)


# ------------------------------------------------------------------ settings
class Settings8(unittest.TestCase):
    def test_migration_to_live(self):
        s = Settings()
        s.schema, s.monitor.poll_seconds, s.server.browser = 7, 60, "edge"
        migrate(s)
        self.assertEqual(s.schema, 8)
        self.assertEqual(s.monitor.poll_seconds, 20)
        self.assertEqual(s.server.browser, "auto")
        self.assertIn(0, s.rules.ignore_severities)

    def test_browser_order(self):
        from core.transport import browser_order
        self.assertEqual(browser_order("auto")[0], "chromium")
        self.assertEqual(browser_order("edge")[0], "msedge")


if __name__ == "__main__":
    unittest.main()
