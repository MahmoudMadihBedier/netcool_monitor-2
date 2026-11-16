"""v12 flow recorder: placeholders, templating, safe replay (final submit left to the engineer)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.flows.flows import (Flow, FlowStore, collect_recording, dedupe, outage_values, render,  # noqa: E402
                                 replay, start_recording, templatize)
from tests.test_v10_ai import outage  # noqa: E402

FORM = """<html><body>
<input id="asset"><input name="sites"><input id="pw" type="password"><select id="team">
<option value="TX">TX</option><option value="NFM">NFM</option></select>
<button id="submit" onclick="document.body.setAttribute('data-sent','1')">Submit</button>
</body></html>"""


class Templating(unittest.TestCase):
    def test_typed_outage_values_become_placeholders(self):
        o = outage(6)
        o.foc = "D1000"
        vals = outage_values(o, "TX")
        steps = [{"action": "click", "selector": "#asset", "value": ""},
                 {"action": "fill", "selector": "#asset", "value": "D1000"},
                 {"action": "fill", "selector": "input[name=sites]", "value": vals["sites"]},
                 {"action": "fill", "selector": "#pw", "value": "{password}"},
                 {"action": "fill", "selector": "#other", "value": "free text"}]
        out = dedupe(templatize(steps, vals))
        self.assertEqual([s["value"] for s in out], ["{foc}", "{sites}", "{password}", "free text"])
        self.assertEqual(render("{foc} / {sites} / {password} / {unknown}", vals, "pw"),
                         f"D1000 / {vals['sites']} / pw / {{unknown}}")

    def test_store_roundtrip_and_bad_steps_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            st = FlowStore(Path(d))
            f = Flow("Global TT on TX", "http://x/arsys/", [{"action": "fill", "selector": "#a", "value": "{foc}"},
                                                          {"action": "evil", "selector": "x"}])
            st.save(f)
            self.assertEqual(st.names(), ["Global TT on TX"])
            g = st.load("Global TT on TX")
            self.assertEqual(len(g.steps), 1)
            st.delete("Global TT on TX")
            self.assertEqual(st.names(), [])


class RealBrowser(unittest.TestCase):
    def test_record_then_replay_stops_before_submit(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        o = outage(5)
        o.foc = "D1000"
        vals = outage_values(o, "TX")
        with sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:
                self.skipTest(f"no browser: {exc}")
            page = b.new_page()
            page.set_content(FORM)
            start_recording(page)
            page.click("#asset")
            page.fill("#asset", "D1000")
            page.fill("input[name=sites]", vals["sites"])
            page.fill("#pw", "s3cret")
            page.select_option("#team", "NFM")
            page.click("#submit")
            steps = dedupe(templatize(collect_recording(page), vals))
            acts = [(s["action"], s.get("value", "")) for s in steps]
            self.assertIn(("fill", "{foc}"), acts)
            self.assertIn(("fill", "{sites}"), acts)
            self.assertIn(("fill", "{password}"), acts)
            self.assertNotIn("s3cret", str(steps))                  # the password is never stored
            self.assertIn(("select", "NFM"), acts)
            self.assertEqual(acts[-1][0], "click")
            # replay on a fresh page with ANOTHER outage: values filled, submit NOT pressed
            o2 = outage(7, area="Maadi")
            o2.foc = "D2000"
            v2 = outage_values(o2, "TX")
            page2 = b.new_page()
            page2.set_content(FORM)
            n = replay(page2, Flow("f", "", steps), v2, password="pw2")
            self.assertEqual(page2.input_value("#asset"), "D2000")
            self.assertEqual(page2.input_value("input[name=sites]"), v2["sites"])
            self.assertEqual(page2.input_value("#pw"), "pw2")
            self.assertEqual(page2.input_value("#team"), "NFM")
            self.assertIsNone(page2.get_attribute("body", "data-sent"))   # Submit left to the engineer
            self.assertEqual(n, len(steps) - 1)
            b.close()


if __name__ == "__main__":
    unittest.main()
