"""The tool's own database (SQLite, part of Python): every alarm, outage, decision and Global TT.

Tables
  alarms     one row per MOM alarm per filter: first / last seen, gone time
  outages    every outage the engine found (latest state)
  decisions  real / not an outage, reason, note, team, night activity, planned activity
  tts        every Global TT sent to Remedy: team, TT number or error, request (password masked), answer
"""
from __future__ import annotations

import csv
import logging
import sqlite3
import threading
from pathlib import Path

from core import clock
from core.config import APP_DIR

log = logging.getLogger(__name__)
DB_FILE = APP_DIR / "netcool.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS alarms (serial TEXT, filter TEXT, site TEXT, severity INTEGER, summary TEXT,
    fault_code TEXT, hub TEXT, area TEXT, region TEXT, node TEXT, down_at TEXT, first_seen TEXT, last_seen TEXT,
    gone_at TEXT, PRIMARY KEY (serial, filter));
CREATE TABLE IF NOT EXISTS outages (okey TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, level TEXT,
    count INTEGER, foc TEXT, hub TEXT, area TEXT, region TEXT, node TEXT, team TEXT, sites TEXT, session TEXT,
    fault_time TEXT, ai TEXT);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, at TEXT, okey TEXT, verdict TEXT, reason TEXT,
    note TEXT, team TEXT, night INTEGER, planned INTEGER, title TEXT, purpose TEXT);
CREATE TABLE IF NOT EXISTS tts (id INTEGER PRIMARY KEY, at TEXT, okey TEXT, team TEXT, tt_number TEXT, ok INTEGER,
    error TEXT, user TEXT, request TEXT, response TEXT);
CREATE INDEX IF NOT EXISTS ix_tts_okey ON tts (okey);
"""
TABLES = {"outages": "last_seen", "decisions": "id", "tts": "id", "alarms": "last_seen"}
PLANNED_WORDS = ("pa ", "planned", "activity", "maintenance", "upgrade", "نشاط", "صيانة")


def okey(o) -> str:
    return "|".join(str(k) for k in o.key)


def _now() -> str:
    return clock.now().isoformat(sep=" ", timespec="seconds")


class Records:
    def __init__(self, path: Path = DB_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()                       # one writer at a time (workers + UI)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)

    def _run(self, sql: str, rows=None, many: bool = False) -> None:
        try:
            with self._lock, self.db:
                (self.db.executemany if many else self.db.execute)(sql, rows or ())
        except sqlite3.Error as exc:                        # the database must never stop monitoring
            log.warning("records database: %s", exc)

    # ---------------------------------------------------------- writes
    def record_rows(self, filter_id: str, events: list, new_ids: set, changed_ids: set, removed: list,
                    first: bool) -> None:
        now = _now()
        todo = [e for e in events if e.serial and (first or e.serial in new_ids or e.serial in changed_ids)]
        self._run("""INSERT INTO alarms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
                     ON CONFLICT(serial, filter) DO UPDATE SET severity=excluded.severity,
                     summary=excluded.summary, last_seen=excluded.last_seen, gone_at=NULL""",
                  [(e.serial, filter_id, e.site, e.severity, e.summary, (e.fields or {}).get("FaultCode", ""),
                    e.hub_raw, e.area, e.region, e.node, f"{e.down_at:%Y-%m-%d %H:%M:%S}" if e.down_at else "",
                    now, now) for e in todo], many=True)
        if removed:
            self._run("UPDATE alarms SET gone_at=? WHERE serial=? AND filter=?",
                      [(now, e.serial, filter_id) for e in removed], many=True)

    def record_outages(self, outages: list, session: str) -> None:
        now = _now()
        self._run("""INSERT INTO outages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(okey) DO UPDATE SET last_seen=excluded.last_seen, level=excluded.level,
                     count=excluded.count, team=excluded.team, sites=excluded.sites, ai=excluded.ai""",
                  [(okey(o), now, now, o.level, o.count, o.foc, o.hub, o.area, o.region, o.node, o.team,
                    " ".join(o.site_ids), session, f"{o.fault_time:%Y-%m-%d %H:%M:%S}" if o.fault_time else "",
                    (o.ai or {}).get("text", "")) for o in outages if o.level != "WATCH"], many=True)

    def add_decision(self, o, real: bool, reason: str = "", note: str = "", team: str = "", purpose: str = "") -> None:
        hour = clock.now().hour
        text = f" {reason} {note} ".lower()
        self._run("INSERT INTO decisions (at, okey, verdict, reason, note, team, night, planned, title, purpose) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (_now(), okey(o), "REAL" if real else "NOT", reason, note, team, int(2 <= hour < 6),
                   int(any(w in text for w in PLANNED_WORDS)), o.title, purpose))

    def add_tt(self, o, team: str, ok: bool, tt_number: str, error: str, user: str, request: str,
               response: str) -> None:
        self._run("INSERT INTO tts (at, okey, team, tt_number, ok, error, user, request, response) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (_now(), okey(o), team, tt_number, int(ok), error, user, request, response[:20000]))

    # ---------------------------------------------------------- reads
    def tt_for(self, o) -> str:
        """TT number already opened for this outage ('' = none)."""
        with self._lock:
            row = self.db.execute("SELECT tt_number FROM tts WHERE okey=? AND ok=1 ORDER BY id DESC LIMIT 1",
                                  (okey(o),)).fetchone()
        return row[0] if row else ""

    def query(self, table: str, search: str = "", limit: int = 500) -> tuple:
        """(columns, rows) newest first; search looks in every column. Table names are a fixed list."""
        if table not in TABLES:
            raise ValueError(f"unknown table {table}")
        with self._lock:
            cols = [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]
            where, args = "", []
            if search:
                where = " WHERE " + " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in cols)
                args = [f"%{search}%"] * len(cols)
            rows = self.db.execute(f"SELECT * FROM {table}{where} ORDER BY {TABLES[table]} DESC LIMIT ?",
                                   args + [int(limit)]).fetchall()
        return cols, rows

    def export_csv(self, table: str, path) -> int:
        cols, rows = self.query(table, limit=1_000_000)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        return len(rows)

    # ---------------------------------------------------------- team experience (local files only)
    SHARE_TABLES = ("decisions", "tts", "outages")
    PRIVATE_COLS = frozenset({"request", "response", "user", "id"})     # never exported, never imported
    SCALAR = (str, int, float, type(None))

    def export_experience(self) -> dict:
        """Decisions, TTs and outages as plain rows (no requests / responses / logins)."""
        out = {}
        for table in self.SHARE_TABLES:
            cols, rows = self.query(table, limit=1_000_000)
            keep = [i for i, c in enumerate(cols) if c not in self.PRIVATE_COLS]
            out[table] = {"columns": [cols[i] for i in keep], "rows": [[r[i] for i in keep] for r in rows]}
        return out

    def import_experience(self, data: dict) -> int:
        """Merge another engineer's rows; a row that already exists (same values) is skipped."""
        added = 0
        for table in self.SHARE_TABLES:
            block = data.get(table) or {}
            if not isinstance(block, dict) or table not in TABLES:
                continue
            given, rows = block.get("columns"), block.get("rows")
            if not isinstance(given, list) or not isinstance(rows, list):
                continue
            with self._lock:
                mine = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")} - self.PRIVATE_COLS
            idx = [i for i, c in enumerate(given) if isinstance(c, str) and c in mine]
            cols = [given[i] for i in idx]
            if not cols:
                continue
            where = " AND ".join(f"{c} IS ?" for c in cols)
            for r in rows[:50000]:
                if not isinstance(r, list) or len(r) != len(given) or not all(isinstance(v, self.SCALAR) for v in r):
                    continue                                   # malformed row in a hand-edited file
                vals = [r[i] for i in idx]
                try:
                    with self._lock, self.db:
                        if self.db.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", vals).fetchone():
                            continue
                        if table == "outages":
                            self.db.execute(f"INSERT OR IGNORE INTO outages ({','.join(cols)}) VALUES "
                                            f"({','.join('?' * len(cols))})", vals)
                        else:
                            self.db.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES "
                                            f"({','.join('?' * len(cols))})", vals)
                        added += 1
                except sqlite3.Error as exc:
                    log.warning("import row skipped: %s", exc)
        return added

    def close(self) -> None:
        with self._lock:
            self.db.close()
