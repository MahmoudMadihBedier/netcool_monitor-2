"""v11: Global TT through Remedy (WSDL, TT_Create request, answer), records database, team learning."""
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import RemedySettings  # noqa: E402
from core.models import NetcoolEvent  # noqa: E402
from plugins.remedy.remedy import (RemedyClient, RemedyError, build_envelope, build_fields, can_send,  # noqa: E402
                                   mask, parse_response, parse_wsdl)
from core.transport import Response  # noqa: E402
from tests.test_v10_ai import T0, outage  # noqa: E402

WSDL = (ROOT / "tests" / "data" / "remedy_sample.wsdl").read_text(encoding="utf-8")
OK_ANSWER = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>
<ns0:TT_CreateResponse xmlns:ns0="urn:HPD_WebInterface_Integration"><ns0:Incident_Number>INC000078991234</ns0:Incident_Number>
</ns0:TT_CreateResponse></soapenv:Body></soapenv:Envelope>"""
FAULT = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><soapenv:Fault>
<faultcode>soapenv:Server.userException</faultcode><faultstring>ERROR (623): Authentication failed; v24</faultstring>
</soapenv:Fault></soapenv:Body></soapenv:Envelope>"""


def ready_settings() -> RemedySettings:
    rs = RemedySettings()
    for k in rs.teams["TX"]:
        rs.teams["TX"][k] = f"tx-{k}"
    rs.teams["TX"].update(Urgency="1-Critical", Impact="2-Significant/Large")
    return rs


class Wsdl(unittest.TestCase):
    def test_reads_address_action_fields_and_allowed_values(self):
        info = parse_wsdl(WSDL)
        self.assertIn("webService=HPD_WebInterface_Integration", info.endpoint)
        self.assertEqual(info.soap_action, "urn:HPD_WebInterface_Integration/TT_Create")
        self.assertIn("Asset_ID", info.fields)
        self.assertEqual(info.fields["Urgency"]["enum"][0], "1-Critical")
        self.assertEqual(info.fields["Outage"]["enum"], ["Yes", "No"])
        self.assertEqual(info.fields["Actual_Fault_Time"]["type"], "dateTime")
        self.assertNotIn("Incident_Number", info.fields)            # response field is not a create field

    def test_wrong_page_is_refused(self):
        with self.assertRaises(RemedyError):
            parse_wsdl("<html><body>Login</body></html>")
        with self.assertRaises(RemedyError):
            parse_wsdl("not xml at all <")


class Request(unittest.TestCase):
    def test_prefill_from_fo_rules_and_template(self):
        rs = ready_settings()
        o = outage(6, "Microwave link down", "TX")
        o.foc = "D1000"
        o.fault_time = T0
        ev = [NetcoolEvent(serial="1", site="D1000", hub_raw="", area="", region="", node="", summary="", severity=5,
                           severity_label="", down_at=T0, last_at=T0, clear_time="", fields={"No_Of_TRUs": "6"}),
              NetcoolEvent(serial="2", site="D1001", hub_raw="", area="", region="", node="", summary="", severity=5,
                           severity_label="", down_at=T0, last_at=T0, clear_time="", fields={"No_Of_TRUs": "4"})]
        v = build_fields(o, "TX", rs, "v26user", ev)
        self.assertEqual(v["Asset_ID"], "D1000")
        self.assertEqual(v["Submitter"], "v26user")
        self.assertEqual(v["Assigned_group_ID"], "tx-Assigned_group_ID")
        self.assertEqual(v["AffectedTRUs"], "10")
        self.assertIn("6 2G sites @ Haram", v["Notes"])
        self.assertEqual(v["Actual_Fault_Time"], "2026-11-13T10:00:00+03:00")
        self.assertIn("D1005", v["Sites"])

    def test_xml_is_escaped_and_password_masked(self):
        v = {"Notes": "PA & TX <check> \"now\"", "Submitter": "u"}
        xml = build_envelope(v, "u", "p&ss<word>")
        root = ET.fromstring(xml)                                   # still valid XML
        notes = next(e.text for e in root.iter() if e.tag.endswith("Notes"))
        self.assertEqual(notes, v["Notes"])
        self.assertNotIn("p&ss", mask(xml))
        self.assertIn("<urn:password>******</urn:password>", mask(xml))

    def test_only_accepted_fields_are_sent(self):
        xml = build_envelope({"Sites": "D1", "QA": "1"}, "u", "p", accepted=["Sites"])
        self.assertIn("<urn:Sites>", xml)
        self.assertNotIn("<urn:QA>", xml)

    def test_locked_until_tested_and_template_complete(self):
        rs = RemedySettings()
        self.assertFalse(can_send(rs, "TX")[0])
        rs.verified, rs.endpoint = True, "http://x"
        ok, why = can_send(rs, "TX")
        self.assertFalse(ok)
        self.assertIn("Assigned_group_ID", why)
        rs = ready_settings()
        rs.verified, rs.endpoint = True, "http://x"
        self.assertTrue(can_send(rs, "TX")[0])


class EndpointSafety(unittest.TestCase):
    def test_password_only_goes_to_the_remedy_domain(self):
        from plugins.remedy.remedy import check_endpoint
        base = "https://remedy-web-intg.internal.eg.vodafone.com/arsys/WSDL/public/x/HPD"
        check_endpoint(base, "https://remedy-web-intg.internal.eg.vodafone.com/arsys/services/ARService")
        check_endpoint(base, "https://remedy-mt2.vodafone.com/arsys/services/ARService")       # same company
        for bad in ("https://evil.example.com/ars", "https://vodafone.com.evil.io/x", "file:///c:/x", ""):
            with self.assertRaises(RemedyError, msg=bad):
                check_endpoint(base, bad)


class Answer(unittest.TestCase):
    def test_tt_number_fault_and_html(self):
        ok = parse_response(200, OK_ANSWER)
        self.assertTrue(ok.ok)
        self.assertEqual(ok.tt_number, "INC000078991234")
        bad = parse_response(500, FAULT)
        self.assertFalse(bad.ok)
        self.assertIn("Authentication failed", bad.error)
        html = parse_response(502, "<html><body>Bad gateway</body>")
        self.assertFalse(html.ok)


class FakeRemedy:
    def __init__(self):
        self.calls = []

    def raw(self, method, url, body=None, headers=None, timeout=90, origin=""):
        self.calls.append((method, url, body, headers or {}))
        return Response(200, url, "text/xml", WSDL if method == "GET" else OK_ANSWER, [])


class Client(unittest.TestCase):
    def test_tampered_wsdl_is_refused(self):
        rs = ready_settings()                           # real Vodafone WSDL address, WSDL points elsewhere
        with self.assertRaises(RemedyError):
            RemedyClient(FakeRemedy(), rs).test_connection()
        self.assertFalse(rs.verified)

    def test_test_connection_then_one_create(self):
        t, rs = FakeRemedy(), ready_settings()
        rs.wsdl_url = "http://127.0.0.1:18312/arsys/WSDL/public/remedy-app-intg/HPD_WebInterface_Integration"
        c = RemedyClient(t, rs)
        c.test_connection()
        self.assertTrue(rs.verified)
        self.assertTrue(can_send(rs, "TX")[0])
        res = c.create_tt(build_fields(outage(5), "TX", rs, "me"), "me", "secret")
        self.assertEqual(res.tt_number, "INC000078991234")
        posts = [x for x in t.calls if x[0] == "POST"]
        self.assertEqual(len(posts), 1)                              # never retried
        self.assertEqual(posts[0][3]["SOAPAction"], "urn:HPD_WebInterface_Integration/TT_Create")
        self.assertIn("webService=HPD_WebInterface_Integration", posts[0][1])
        self.assertNotIn("<urn:QA>", posts[0][2])                     # not in this WSDL -> not sent


class RecordsDb(unittest.TestCase):
    def setUp(self):
        from plugins.records.records import Records
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Records(Path(self.tmp.name) / "n.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _ev(self, serial, site, sev=5):
        return NetcoolEvent(serial=serial, site=site, hub_raw="H1", area="A", region="R", node="N", summary="down",
                            severity=sev, severity_label="", down_at=T0, last_at=T0, clear_time="", fields={})

    def test_alarms_life_cycle(self):
        a, b = self._ev("1", "GD1"), self._ev("2", "GD2")
        self.db.record_rows("F", [a, b], set(), set(), [], first=True)
        c = self._ev("3", "GD3")
        self.db.record_rows("F", [a, c], {"3"}, set(), [b], first=False)
        cols, rows = self.db.query("alarms")
        gone = {r[cols.index("serial")]: r[cols.index("gone_at")] for r in rows}
        self.assertEqual(set(gone), {"1", "2", "3"})
        self.assertIsNotNone(gone["2"])
        self.assertIsNone(gone["1"])

    def test_decisions_tts_and_search(self):
        o = outage(5)
        self.db.record_outages([o], "2G")
        self.db.add_decision(o, False, "PA / planned activity", "PA upgrade tonight", "", "2G")
        self.assertEqual(self.db.tt_for(o), "")
        self.db.add_tt(o, "TX", True, "INC000000000123", "", "me", "<xml/>", "<ok/>")
        self.assertEqual(self.db.tt_for(o), "INC000000000123")
        cols, rows = self.db.query("decisions", search="upgrade")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][cols.index("planned")], 1)
        with self.assertRaises(ValueError):
            self.db.query("sqlite_master; DROP TABLE tts")
        out = Path(self.tmp.name) / "tts.csv"
        self.assertEqual(self.db.export_csv("tts", out), 1)
        self.assertIn("INC000000000123", out.read_text(encoding="utf-8-sig"))

    def test_search_text_is_a_parameter(self):
        self.db.record_outages([outage(5)], "S")
        cols, rows = self.db.query("outages", search="x' OR '1'='1")
        self.assertEqual(rows, [])


class TeamLearning(unittest.TestCase):
    def test_suggests_the_team_used_for_similar_outages(self):
        from plugins.ai.brain import Brain
        with tempfile.TemporaryDirectory() as d:
            b = Brain(Path(d) / "b.json")
            self.assertEqual(b.suggest_team(outage(6, "Mains power failure", "Power")), ("", 0))
            for i in range(3):
                b.learn_team(outage(6 + i, "Mains power failure", "Power", hub=f"H{i}"), "NFM")
                b.learn_team(outage(6 + i, "Microwave link down", "TX", area="Maadi", hub=f"H9{i}"), "TX")
            self.assertEqual(b.suggest_team(outage(7, "Mains power failure", "Power", hub="H77"))[0], "NFM")
            self.assertEqual(b.suggest_team(outage(7, "Microwave link down", "TX", area="Maadi"))[0], "TX")


if __name__ == "__main__":
    unittest.main()
