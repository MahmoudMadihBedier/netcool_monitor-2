"""Performance fixes: Playwright's pipe reader, batched browser fetches, cached time conversion."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import clock  # noqa: E402
from core.parser import mom_time  # noqa: E402


def _browser_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


class TimeCache(unittest.TestCase):
    def tearDown(self):
        clock.set_zone(clock.DEFAULT_ZONE)

    def test_cached_conversion_follows_the_selected_zone(self):
        ms = 1_793_219_288_000
        clock.set_zone("Asia/Riyadh")
        riyadh = clock.from_epoch_ms(ms)
        self.assertEqual(riyadh, clock.from_epoch(ms / 1000))
        clock.set_zone("Africa/Johannesburg")
        self.assertEqual((riyadh - clock.from_epoch_ms(ms)).total_seconds(), 3600)   # not the cached Riyadh value

    def test_mom_time_text(self):
        self.assertEqual(mom_time(datetime(2026, 5, 12, 15, 3)), "5/12/26, 3:03 PM")
        self.assertEqual(mom_time(datetime(2026, 5, 12, 0, 7)), "5/12/26, 12:07 AM")


class PipeReader(unittest.TestCase):
    def test_patch_applies_or_leaves_playwright_untouched(self):
        try:
            from playwright._impl import _transport as pt
        except ImportError:
            self.skipTest("playwright not installed")
        from core.transport import patch_playwright_pipe
        ok = patch_playwright_pipe()
        self.assertEqual(ok, getattr(pt.PipeTransport.run, "_nsm_linear", False))
        self.assertEqual(patch_playwright_pipe(), ok)                     # second call is a no-op

    @unittest.skipUnless(_browser_ok(), "needs a Playwright browser")
    def test_large_reply_arrives_whole(self):
        from core.transport import patch_playwright_pipe
        patch_playwright_pipe()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            text = b.new_page().evaluate("() => 'ab'.repeat(6 * 1024 * 1024)")   # 12 MB in one message
            b.close()
        self.assertEqual(len(text), 12 * 1024 * 1024)
        self.assertEqual(text[:4], "abab")


class Batches(unittest.TestCase):
    def test_request_many_keeps_order_across_batches(self):
        from core.transport import BrowserTransport

        class Fake(BrowserTransport):
            def __init__(self):            # no real browser: evaluate() answers from the request itself
                self.calls = []
                self._page = self

            def _ensure_origin_page(self):
                pass

            def _call(self, fn, timeout):
                return fn()

            def evaluate(self, js, arg):
                self.calls.append(len(arg["reqs"]))
                return [{"ok": True, "status": 200, "url": q["url"], "text": q["url"]} for q in arg["reqs"]]

        t = Fake()
        reqs = [("GET", f"http://x/p{i}", None) for i in range(14)]
        out = t.request_many(reqs, limit=6)
        self.assertEqual([r.text for r in out], [f"http://x/p{i}" for i in range(14)])
        self.assertEqual(t.calls, [6, 6, 2])                               # never one giant reply


if __name__ == "__main__":
    unittest.main()
