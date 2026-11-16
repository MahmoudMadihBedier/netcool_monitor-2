"""Global TT in Remedy through the HPD_WebInterface_Integration web service (operation TT_Create).

* 'Test connection' reads the WSDL: the real SOAP address, the soapAction and the fields TT_Create accepts
  (with their allowed values) - nothing is guessed.
* The request is built like the TT_Create sample (same element names, same order). Every value is XML-escaped.
* The engineer's Remedy password is only put into the request being sent; previews and logs mask it.
* Sending happens only on the engineer's click and is NEVER retried automatically (no duplicate TTs).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape

from core import clock
from core.config import REMEDY_TEAM_REQUIRED, RemedySettings

log = logging.getLogger(__name__)
NS = "urn:HPD_WebInterface_Integration"
MAX_WSDL = 3 * 1024 * 1024
# TT_Create elements, in the order of the sample request
TT_FIELDS = ["Submitter", "Stage-Submitter__c", "Status", "Status__c", "Assigned_To", "Assigned_group_ID",
             "Assigned_Group", "Assignee", "Assignee_Login_ID__c", "Assigned_Support_Organization",
             "Assigned_Support_Company", "Categorization_Tier_1", "Categorization_Tier_2", "Categorization_Tier_3",
             "Classification_Tier_3", "Urgency", "Impact", "Contact_Company", "Direct_Contact_Person_ID",
             "Corporate_ID", "Short_Description", "Description", "Detailed_Decription", "Comment__c", "Notes",
             "Template", "Incident_Type", "Service_Type", "Reported_Source", "TT_Create_Date__c",
             "TT_Last_Modified_Date__c", "Schedule_Start_Date__c", "First_Nmae__c", "Last_Name__c",
             "Data_Affecting", "TT_Number__c", "Actual_Fault_Time", "Auto_Assign_Company", "Asset_ID",
             "Service_Affecting", "Outage", "Integration_Process_Request_ID", "Action_Owner__c", "Sites",
             "AffectedTRUs", "QA"]
URGENCY = ["1-Critical", "2-High", "3-Medium", "4-Low"]
IMPACT = ["1-Extensive/Widespread", "2-Significant/Large", "3-Moderate/Limited", "4-Minor/Localized"]


class RemedyError(Exception):
    pass


# ------------------------------------------------------------------ WSDL
@dataclass
class WsdlInfo:
    endpoint: str
    soap_action: str
    fields: dict = field(default_factory=dict)      # name -> {"type": "...", "enum": [...]}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_wsdl(text: str) -> WsdlInfo:
    if not text or len(text) > MAX_WSDL:
        raise RemedyError("empty or too large WSDL")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RemedyError(f"the address did not return a WSDL ({exc})") from exc
    endpoint = soap_action = ""
    for el in root.iter():
        name = _local(el.tag)
        if name == "address" and el.get("location") and not endpoint:
            endpoint = el.get("location")
        if name == "operation" and el.get("name") == "TT_Create":
            for sub in el.iter():
                if _local(sub.tag) == "operation" and sub.get("soapAction") is not None:
                    soap_action = sub.get("soapAction")
    if not endpoint:
        raise RemedyError("the WSDL has no SOAP address")
    if not any(_local(e.tag) == "operation" and e.get("name") == "TT_Create" for e in root.iter()):
        raise RemedyError("the web service has no TT_Create operation")
    types = {e.get("name"): e for e in root.iter() if _local(e.tag) in ("complexType", "simpleType") and e.get("name")}
    top = next((e for e in root.iter() if _local(e.tag) == "element" and e.get("name") == "TT_Create"), None)
    box = top
    if top is not None and top.get("type"):
        box = types.get(top.get("type").split(":")[-1], top)
    fields = {}
    for e in (box.iter() if box is not None else []):
        if _local(e.tag) != "element" or e is top or not e.get("name"):
            continue
        typ = (e.get("type") or "").split(":")[-1]
        enum = []
        st = types.get(typ)
        for node in ([st] if st is not None else []) + [e]:
            enum += [x.get("value") for x in node.iter() if _local(x.tag) == "enumeration"]
        fields[e.get("name")] = {"type": typ or "string", "enum": enum}
    return WsdlInfo(endpoint=endpoint, soap_action=soap_action or "urn:HPD_WebInterface_Integration/TT_Create",
                    fields=fields)


# ------------------------------------------------------------------ request
def missing_team_fields(rs: RemedySettings, team: str) -> list:
    t = rs.teams.get(team) or {}
    return [k for k in REMEDY_TEAM_REQUIRED if not str(t.get(k, "")).strip()]


def can_send(rs: RemedySettings, team: str) -> tuple:
    """(allowed, why not) - Open TT stays locked until the connection is tested and the team is complete."""
    if not rs.verified or not rs.endpoint:
        return False, "Remedy connection not tested yet (🎫 Remedy > Test connection)"
    miss = missing_team_fields(rs, team)
    if miss:
        return False, f"Remedy template of team {team} is missing: {', '.join(miss)}"
    return True, ""


def _fault_time_text(dt, rs: RemedySettings) -> str:
    """xsd:dateTime (per WSDL, or when unknown) -> ISO with the selected zone; other types -> MOM style."""
    if dt is None:
        return ""
    info = next((f for f in rs.accepted_fields if isinstance(f, dict) and f.get("name") == "Actual_Fault_Time"), {})
    if "date" in (info.get("type") or "dateTime").lower():
        return f"{dt:%Y-%m-%dT%H:%M:%S}+{clock.ZONES[clock.zone()]:02d}:00"
    return f"{dt:%m/%d/%Y %I:%M:%S %p}"


def build_fields(o, team: str, rs: RemedySettings, user: str, events: Optional[list] = None,
                 sessions: str = "") -> dict:
    """Pre-filled Global TT (FO manual): Asset ID = FOC, fault time, sites, notes, TRUs, team template."""
    sites = o.tt_sites
    trus = 0
    for e in events or []:
        if e.site in sites:
            try:
                trus += int(float((e.fields or {}).get("No_Of_TRUs") or 0))
            except ValueError:
                pass
    ft = o.tt_fault_time
    detail = [f"Outage: {o.count} physical sites  ({o.level}{' ' + o.color if o.color else ''})",
              f"First of chain (Asset ID): {o.foc or '-'}" + ("" if o.foc_in_mom else "  (not in MOM down list)"),
              f"Actual fault time: {ft:%d/%m/%Y %H:%M:%S}" if ft else "Actual fault time: -",
              f"HUB: {o.hub or '-'}   Node: {o.node or '-'}   Area: {o.area or '-'}   Region: {o.region or '-'}",
              f"Evidence: {', '.join(o.evidence)}   Team: {team}",
              f"Sites: {', '.join(sites)}"] + ([f"Seen in sessions: {sessions}"] if sessions else [])
    values = {k: "" for k in TT_FIELDS}
    values.update({k: str(v) for k, v in rs.common.items() if k in values})
    values.update({k: str(v) for k, v in (rs.teams.get(team) or {}).items() if k in values})
    values.update({
        "Submitter": user, "Stage-Submitter__c": user,
        "Short_Description": f"Outage {o.count} sites @ {o.area or o.region} - FOC {o.foc or '-'}"[:100],
        "Description": o.tt_notes, "Notes": o.tt_notes, "Detailed_Decription": "\n".join(detail),
        "Actual_Fault_Time": _fault_time_text(ft, rs), "Asset_ID": o.foc or (sites[0] if sites else ""),
        "Sites": ", ".join(sites), "AffectedTRUs": str(trus),
    })
    return values


def build_envelope(values: dict, user: str, password: str, accepted: Optional[list] = None) -> str:
    """The SOAP request. Only fields the WSDL accepts are sent (all of the sample's when not known)."""
    names = [k for k in TT_FIELDS if not accepted or k in accepted] + \
            [k for k in values if k not in TT_FIELDS and accepted and k in accepted]
    body = "\n".join(f"<urn:{k}>{escape(str(values.get(k, '')))}</urn:{k}>" for k in names)
    return ("<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
            f"xmlns:urn=\"{NS}\">\n<soapenv:Header>\n<urn:AuthenticationInfo>\n"
            f"<urn:userName>{escape(user)}</urn:userName>\n<urn:password>{escape(password)}</urn:password>\n"
            "<urn:authentication></urn:authentication>\n<urn:locale></urn:locale>\n<urn:timeZone></urn:timeZone>\n"
            f"</urn:AuthenticationInfo>\n</soapenv:Header>\n<soapenv:Body>\n<urn:TT_Create>\n{body}\n"
            "</urn:TT_Create>\n</soapenv:Body>\n</soapenv:Envelope>")


def mask(xml: str) -> str:
    return re.sub(r"(<urn:password>).*?(</urn:password>)", r"\1******\2", xml, flags=re.S)


# ------------------------------------------------------------------ response
@dataclass
class RemedyResult:
    ok: bool
    tt_number: str = ""
    error: str = ""
    fields: dict = field(default_factory=dict)


def parse_response(status: int, text: str) -> RemedyResult:
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError:
        return RemedyResult(False, error=f"HTTP {status}: not a SOAP answer: {re.sub(r'<[^>]+>', ' ', text or '')[:200]}")
    leaves = {}
    for el in root.iter():
        if len(el) == 0 and (el.text or "").strip():
            leaves.setdefault(_local(el.tag), el.text.strip())
    if "faultstring" in leaves or any(_local(e.tag) == "Fault" for e in root.iter()):
        msg = leaves.get("faultstring") or leaves.get("Text") or leaves.get("message") or "SOAP fault"
        return RemedyResult(False, error=msg[:500], fields=leaves)
    tt = next((v for k, v in leaves.items() if re.search(r"incident_number|tt_number|request_id|ticket", k, re.I)), "")
    if not tt:
        tt = next((m.group(0) for v in leaves.values() for m in [re.search(r"\b(INC|TT|CRQ)\d{6,}\b", v)] if m), "")
    if status >= 400:
        return RemedyResult(False, error=f"HTTP {status}", fields=leaves)
    return RemedyResult(bool(tt), tt_number=tt, error="" if tt else "Remedy answered without a TT number",
                        fields=leaves)


# ------------------------------------------------------------------ client
def check_endpoint(wsdl_url: str, endpoint: str) -> None:
    """The TT request carries the engineer's password: only send it to the WSDL's own server or the same
    company domain (a wrong / tampered WSDL must not redirect it elsewhere)."""
    from urllib.parse import urlsplit
    w, e = urlsplit(wsdl_url).hostname or "", urlsplit(endpoint).hostname or ""
    domain = ".".join(w.split(".")[-2:])
    if not e or not (e == w or (domain.count(".") and e.endswith("." + domain))):
        raise RemedyError(f"the WSDL sends TTs to another server ({e or endpoint}) - refused for safety")
    if urlsplit(endpoint).scheme not in ("http", "https"):
        raise RemedyError(f"unsupported address {endpoint}")


class RemedyClient:
    """Talks to Remedy through the tool's transport (browser = same VPN path as Edge)."""

    def __init__(self, transport, rs: RemedySettings):
        self.t, self.rs = transport, rs

    def test_connection(self) -> WsdlInfo:
        r = self.t.raw("GET", self.rs.wsdl_url, origin=self.rs.wsdl_url, timeout=60)
        if r.status >= 400:
            raise RemedyError(f"HTTP {r.status} from the WSDL address")
        info = parse_wsdl(r.text)
        check_endpoint(self.rs.wsdl_url, info.endpoint)
        self.rs.endpoint, self.rs.soap_action = info.endpoint, info.soap_action
        self.rs.accepted_fields = [{"name": k, **v} for k, v in info.fields.items()]
        self.rs.verified = True
        log.info("Remedy WSDL OK: %s (%d TT_Create fields)", info.endpoint, len(info.fields))
        return info

    def create_tt(self, values: dict, user: str, password: str) -> RemedyResult:
        accepted = [f["name"] for f in self.rs.accepted_fields if isinstance(f, dict)]
        xml = build_envelope(values, user, password, accepted)
        r = self.t.raw("POST", self.rs.endpoint, body=xml, origin=self.rs.wsdl_url, timeout=120,
                       headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": self.rs.soap_action})
        res = parse_response(r.status, r.text)
        log.info("Remedy TT_Create by %s: %s %s", user, "OK" if res.ok else "FAILED", res.tt_number or res.error)
        return res
