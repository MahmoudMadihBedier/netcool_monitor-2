"""Application settings: one JSON file in ~/.netcool_monitor/settings.json.

Everything that may need tuning on site (URL, rule thresholds, column ids) lives
here so it can be changed from the Settings screen without touching code.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

APP_DIR = Path(os.path.expanduser("~")) / ".netcool_monitor"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "monitor.log"
ALERT_HISTORY_DIR = APP_DIR / "alert_history"
CASCADE_DIR = APP_DIR / "cascade"
MAIL_DIR = APP_DIR / "mails"
HEARTBEAT_DIR = APP_DIR / "heartbeat"
if getattr(sys, "frozen", False):          # single-file .exe (PyInstaller): files unpacked to _MEIPASS
    PROJECT_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    EXE_DIR = Path(sys.executable).resolve().parent
else:
    PROJECT_DIR = Path(__file__).resolve().parents[1]
    EXE_DIR = PROJECT_DIR
BUNDLED_CASCADE = PROJECT_DIR / "data" / "CascadedSitesReport_20092026.CSV"
# a logo.png placed next to the .exe (assets\logo.png) wins over the bundled folder
ASSETS_DIR = EXE_DIR / "assets" if (EXE_DIR / "assets").is_dir() else PROJECT_DIR / "assets"

# MOM / OMNIbus severities (value -> label, MOM row colour)
SEVERITIES = [(5, "Critical", "#ff0000"), (4, "Major", "#ffb329"), (3, "Minor", "#ffff00"),
              (2, "Warning", "#63b8ff"), (1, "Indeterminate", "#bf3eff"), (0, "Clear", "#00cd00")]

log = logging.getLogger(__name__)


@dataclass
class ServerSettings:
    base_url: str = "https://10.21.161.203:16311"
    transport: str = "browser"          # "browser" = the tool's own hidden browser carries the requests
    #                                     "direct" = Python requests
    browser: str = "auto"               # auto = tool's own Chromium first (never touches your Edge),
    #                                     then Edge / Chrome with a separate hidden profile; "edge" = Edge first
    verify_tls: bool = False             # DASH uses a self-signed certificate
    request_timeout_s: int = 90
    timezone: str = "Asia/Riyadh"        # Asia/Riyadh (UTC+3) or Africa/Johannesburg (UTC+2) ONLY:
    #                                      sent to MOM for its display AND used by the tool's clock
    datasource: str = "VFEP_A"           # fallback if a filter does not name one
    portlet_namespace: str = "ns__30899058_"  # fallback; discovered automatically after login
    keepalive_s: int = 60


@dataclass
class RuleSettings:
    """Outage rules (FO manual): physical sites, same root cause, same time.
    min_sites      -> PRE-OUTAGE alert (4: call TX for first-of-chain + 5th site)
    outage_sites   -> OUTAGE (5 physical sites)"""
    min_sites: int = 4
    outage_sites: int = 5
    window_minutes: int = 5               # same HUB / same node: down within N minutes
    chain_window_minutes: int = 15        # same cascaded chain: down within N minutes
    use_chain: bool = True                # group by Cascaded Sites Report chains
    use_hub: bool = True                  # group by HUB + area (classic rule)
    use_node: bool = True                 # group by Node (RNC/BSC) + area -> "possible" outage
    chain_merge_cap: int = 30             # max chain size used to merge separate roots
    tt_minutes: int = 15                  # open Global TT at the 15th minute
    clear_after_polls: int = 2            # outage must be gone N refreshes before "cleared"
    yellow_max: int = 25                  # < 25 physical sites  -> Yellow
    orange_max: int = 50                  # < 50 physical sites  -> Orange, else Red
    default_severities: list = field(default_factory=lambda: [5, 4, 3, 2, 1])  # all but Clear (like v4)
    site_field: str = "SiteID"
    hub_field: str = "CI_Organization"   # column labelled "HUB" in the Event Viewer
    hub_match: str = "code"              # "code" = first token (H4179), "full" = whole value
    area_field: str = "TECHostname"      # column labelled "SubRegion"; "" = ignore area
    region_field: str = "NetworkAssureHost"  # column labelled "Region" (display only)
    time_field: str = "FirstOccurrence"  # when the site went down
    ignore_severities: list = field(default_factory=lambda: [0])  # 0 = Clear
    ignore_cleared: bool = True          # skip rows that have a ClearTime
    include_summary_regex: str = ""      # optional: only rows whose Summary matches
    exclude_summary_regex: str = ""      # optional: drop rows whose Summary matches
    realert_minutes: int = 15            # remind while still active; 0 = never


@dataclass
class MonitorSettings:
    poll_seconds: int = 20              # live refresh (each session can change it: 10 / 15 / 20 / 30 / 60 s)
    page_size: int = 150
    blind_after_failures: int = 3
    default_view: str = "3g_H_easymonitoring:global"
    shift_hours: int = 12
    prevent_sleep: bool = True
    alarm_wav: str = ""                 # own alarm sound .wav / .mp3 ("" = built-in alarm) - Sound window
    alarm_volume: int = 100             # 5..100


@dataclass
class MailSettings:
    """Outage mail - same as the Automated_Outage.xlsm macro."""
    to: str = "Tech-SOCTransport@Vodafoneeg.onmicrosoft.com"
    cc: str = ("Tech-SMCCairoAccessServiceFrontDesk@Vodafoneeg.onmicrosoft.com;"
               "Tech-SMCAlex_DeltaAccessServiceFrontDesk@Vodafoneeg.onmicrosoft.com")
    subject_template: str = "{date} || Downsites"   # fields: {date} {region} {area} {count} {foc} {hub}
    include_foc_hint: bool = True
    signature: str = ""                              # empty = Windows user name (like the macro)


@dataclass
class CascadeSettings:
    active_path: str = ""          # uploaded report in use ("" = bundled report)
    uploaded_at: str = ""          # ISO time of the last upload
    max_age_days: int = 7          # warn when the report is older than this


@dataclass
class HeartbeatSettings:
    """Heartbeat Failure task (button 'Heartbeat task')."""
    hb_filter: str = "LTE_Heartbeat_Ericsson:group:Tech-SMC Cairo Access Service Front Desk"
    hb_view: str = "3g_H_easymonitoring:global"
    ref_filter: str = "3G_Marco:group:Tech-SMC Cairo Access Service Front Desk"
    ref_view: str = "3g_H_easymonitoring:global"
    window: str = "previous_shift"    # previous_shift (last completed) | current_shift | last_hours | custom
    hours: int = 12
    shift_start: str = "06:30"        # Morning 06:30-18:30, Night 18:30-06:30
    severities: list = field(default_factory=lambda: [5, 4])
    summary_regex: str = r"heart\s*beat"


# Remedy fields every Global TT template must have before 'Open TT' is allowed
REMEDY_TEAM_REQUIRED = ["Assigned_group_ID", "Assigned_Group", "Assigned_Support_Organization",
                        "Assigned_Support_Company", "Categorization_Tier_1", "Categorization_Tier_2",
                        "Categorization_Tier_3", "Urgency", "Impact"]
REMEDY_TEAM_OPTIONAL = ["Assignee", "Template", "Classification_Tier_3"]


def _team_templates() -> dict:
    return {t: {k: "" for k in REMEDY_TEAM_REQUIRED + REMEDY_TEAM_OPTIONAL} for t in ("TX", "NFM", "PA")}


@dataclass
class RemedySettings:
    """Global TT through Remedy's HPD_WebInterface_Integration web service (TT_Create)."""
    wsdl_url: str = ("https://remedy-web-intg.internal.eg.vodafone.com/arsys/WSDL/public/remedy-app-intg/"
                     "HPD_WebInterface_Integration")
    endpoint: str = ""                  # SOAP address read from the WSDL by 'Test connection'
    soap_action: str = ""               # soapAction of TT_Create from the WSDL
    verified: bool = False              # Test connection succeeded -> 'Open TT' unlocked
    last_user: str = ""                 # Remedy login of this PC's engineer (password: Windows Credential Manager)
    accepted_fields: list = field(default_factory=list)   # TT_Create elements the WSDL lists
    teams: dict = field(default_factory=_team_templates)  # TX / NFM / PA -> Remedy fields
    common: dict = field(default_factory=lambda: {"Contact_Company": "Vodafone Egypt", "Status__c": "1",
                                                  "Incident_Type": "0", "Service_Type": "1",
                                                  "Reported_Source": "", "Outage": "", "Service_Affecting": ""})


@dataclass
class SavedSession:
    name: str
    filter_id: str
    view_id: str


SCHEMA = 8


@dataclass
class Settings:
    schema: int = SCHEMA
    server: ServerSettings = field(default_factory=ServerSettings)
    rules: RuleSettings = field(default_factory=RuleSettings)
    monitor: MonitorSettings = field(default_factory=MonitorSettings)
    mail: MailSettings = field(default_factory=MailSettings)
    cascade: CascadeSettings = field(default_factory=CascadeSettings)
    heartbeat: HeartbeatSettings = field(default_factory=HeartbeatSettings)
    remedy: RemedySettings = field(default_factory=RemedySettings)
    saved_usernames: list = field(default_factory=list)
    last_username: str = ""
    last_sessions: list = field(default_factory=list)  # list[SavedSession as dict]
    plugins: dict = field(default_factory=dict)        # plugin name -> its settings (dataclass or dict)
    plugins_disabled: list = field(default_factory=list)
    ui: dict = field(default_factory=lambda: {"theme": "light", "scale": 0})   # 0 = automatic scaling

    # ---- (de)serialisation, tolerant to missing/unknown keys ----
    def to_dict(self) -> dict:
        d = asdict(self)
        d["plugins"] = {k: (asdict(v) if is_dataclass(v) else dict(v or {})) for k, v in self.plugins.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        def build(dc, data):
            data = data or {}
            names = {f.name for f in fields(dc)}
            return dc(**{k: v for k, v in data.items() if k in names})

        return cls(
            schema=int(d.get("schema", 0) or 0),
            server=build(ServerSettings, d.get("server")),
            rules=build(RuleSettings, d.get("rules")),
            monitor=build(MonitorSettings, d.get("monitor")),
            mail=build(MailSettings, d.get("mail")),
            cascade=build(CascadeSettings, d.get("cascade")),
            heartbeat=build(HeartbeatSettings, d.get("heartbeat")),
            remedy=build(RemedySettings, d.get("remedy")),
            saved_usernames=list(d.get("saved_usernames", [])),
            last_username=d.get("last_username", ""),
            last_sessions=list(d.get("last_sessions", [])),
            plugins={str(k): dict(v or {}) for k, v in dict(d.get("plugins") or {}).items()},
            plugins_disabled=[str(x) for x in d.get("plugins_disabled", [])],
            ui={**{"theme": "light", "scale": 0}, **dict(d.get("ui") or {})},
        )


class SettingsRepository:
    def __init__(self, path: Path = SETTINGS_FILE):
        self._path = path

    def load(self) -> Settings:
        try:
            if self._path.exists():
                s = Settings.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
                if s.schema < SCHEMA:
                    migrate(s)
                    self.save(s)
                return s
        except Exception:
            log.exception("Settings file unreadable, using defaults")
        return Settings()

    def save(self, s: Settings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self._path)


def migrate(s: Settings) -> None:
    """v5 -> v6: count every severity except Clear (as v4 did), refresh every minute,
    narrower chain merging. Saved sessions get the new default severities once."""
    old = s.schema
    if old < 6:
        s.rules.default_severities = [5, 4, 3, 2, 1]
        s.rules.chain_merge_cap = min(s.rules.chain_merge_cap, 30)
        s.monitor.poll_seconds = 60
        for sess in s.last_sessions:
            if isinstance(sess, dict):
                sess["severities"] = [5, 4, 3, 2, 1]
    if old < 7:
        s.heartbeat.window = "previous_shift"
        s.heartbeat.shift_start = "06:30"
        if s.server.timezone not in ("Asia/Riyadh", "Africa/Johannesburg"):
            s.server.timezone = "Asia/Riyadh"
    if old < 8:
        s.monitor.poll_seconds = 20               # live monitoring
        s.server.browser = "auto"                 # the tool's own browser, isolated from the user's Edge
        s.monitor.blind_after_failures = max(3, s.monitor.blind_after_failures)
        if 0 not in s.rules.ignore_severities:
            s.rules.ignore_severities = sorted(set(s.rules.ignore_severities) | {0})
    s.schema = SCHEMA
    log.info("Settings migrated from schema %s to %s", old, SCHEMA)
