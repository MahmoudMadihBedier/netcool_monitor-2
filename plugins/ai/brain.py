"""The tool's learning assistant (offline, standard library only).

* Learns from the engineers' decisions: an acknowledged outage / outage mail / Global TT = REAL,
  'Not an outage' + reason + note = NOT REAL.
* A small neural network (sparse input -> 12 hidden units -> probability) scores new outages; similar past
  decisions explain the score ("usually PA activity, seen 9 times").
* Learns what each session is for (filter / view names + alarm texts -> purpose).
ADVISES ONLY: it never hides or silences an alarm.
"""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from core import clock
from core.config import APP_DIR
from . import nlp

log = logging.getLogger(__name__)
BRAIN_FILE = APP_DIR / "brain.json"
MIN_EACH = 5                  # decisions of each kind before the AI gives advice
MAX_EXAMPLES = 3000
MAX_FILE_BYTES = 20 * 1024 * 1024
TRAIN_BUDGET_S = 4.0          # training never takes more than this (background, low priority)
REASONS = ["PA / planned activity", "Power (site / area power)", "Not the same chain / root cause",
           "Already has a TT / reported", "Flapping / clears by itself", "Test / new site", "Other"]
PURPOSES = ["Down sites (FO outage rules)", "Heartbeat", "Power alarms", "Transmission (TX)",
            "Cell / service degradation", "Other"]


# ------------------------------------------------------------------ features
def _bucket(n: int, edges=(2, 4, 5, 10, 25, 50)) -> str:
    for e in edges:
        if n < e:
            return f"<{e}"
    return f">={edges[-1]}"


PLANNED_WORDS = ("pa ", "planned", "activity", "maintenance", "upgrade", "crq", "swap", "نشاط", "صيانة")


def context_features(context: dict) -> list:
    """Extra conditions from the plugins / the engineer: planned activity found (Outlook), TT already open,
    the note's words. context = {"activities": [lines], "tt": "INC..", "note": "..."}"""
    parts = []
    acts = context.get("activities") or []
    parts.append(nlp.value_feature("outlook", "hit" if acts else "none"))
    if acts:
        parts.append(nlp.text_features(" ".join(acts)[:2000], "mail", 0.3))
    parts.append(nlp.value_feature("has_tt", bool(context.get("tt"))))
    note = context.get("note") or ""
    if note:
        parts.append(nlp.text_features(note, "sum", 0.5))
        parts.append(nlp.value_feature("planned_note", any(w in note.lower() for w in PLANNED_WORDS)))
    return parts


def outage_features(o, purpose: str = "", now=None, context: dict = None) -> dict:
    """What the AI looks at in an outage: size, level, evidence, area, time of day / night window, weekday,
    technologies, alarm texts, FOC state, plus the context (Outlook activity, TT, engineer's note)."""
    now = now or clock.now()
    parts = [nlp.value_feature("size", _bucket(o.count)), nlp.value_feature("level", o.level),
             nlp.value_feature("night", 2 <= now.hour < 6), nlp.value_feature("early", 6 <= now.hour < 7),
             nlp.value_feature("weekday", now.weekday()),
             nlp.value_feature("planned_alarm", any(w in " ".join(s.summary for s in o.sites[:30]).lower()
                                                   for w in PLANNED_WORDS)),
             nlp.value_feature("conf", o.confidence), nlp.value_feature("area", o.area),
             nlp.value_feature("region", o.region), nlp.value_feature("node", o.node),
             nlp.value_feature("hub", o.hub), nlp.value_feature("hour", now.hour // 3),
             nlp.value_feature("foc", "in_mom" if (o.foc and o.foc_in_mom) else ("missing" if o.foc else "none")),
             nlp.value_feature("purpose", purpose), nlp.value_feature("team", o.team)]
    parts += [nlp.value_feature("ev", e) for e in o.evidence]
    parts += [nlp.value_feature("tech", t) for t in (o.tech_counts or {})]
    unknown = sum(1 for s in o.sites if not s.in_report)
    parts.append(nlp.value_feature("unknown", _bucket(unknown, (1, 2, 5))))
    texts = " ".join(f"{s.summary} {s.fault_code}" for s in o.sites[:30])
    parts.append(nlp.text_features(texts, "sum", 1.0 / max(1, min(len(o.sites), 30)) ** 0.5))
    parts += context_features(context or {})
    return nlp.normalize(nlp.merge(*parts))


# ------------------------------------------------------------------ neural network
class TinyNet:
    """Sparse input -> H tanh units -> sigmoid. Trained with plain SGD (fast enough: inputs are sparse)."""
    H = 12

    def __init__(self, seed: int = 7):
        self.rnd = random.Random(seed)
        self.w1: dict = {}
        self.b1 = [0.0] * self.H
        self.w2 = [self.rnd.uniform(-0.3, 0.3) for _ in range(self.H)]
        self.b2 = 0.0

    def _row(self, k: int) -> list:
        r = self.w1.get(k)
        if r is None:
            r = self.w1[k] = [self.rnd.uniform(-0.1, 0.1) for _ in range(self.H)]
        return r

    def _hidden(self, x: dict) -> list:
        h = list(self.b1)
        for k, v in x.items():
            r = self.w1.get(k)
            if r is not None:
                for j in range(self.H):
                    h[j] += r[j] * v
        return [math.tanh(z) for z in h]

    def predict(self, x: dict) -> float:
        h = self._hidden(x)
        z = self.b2 + sum(a * b for a, b in zip(self.w2, h))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    def fit(self, data: list, epochs: int = 40, lr: float = 0.15, l2: float = 1e-4,
            time_budget_s: float = 0.0, pause_every: int = 0) -> int:
        """data = [(x, y, weight)]. Stops early when the time budget is used (the laptop belongs to the shift).
        pause_every: yield the CPU briefly every N examples so the UI never stutters. Returns epochs done."""
        import time as _t
        t0, n_seen = _t.monotonic(), 0
        for x, _, _ in data:
            for k in x:
                self._row(k)
        order = list(range(len(data)))
        done = 0
        for ep in range(epochs):
            if time_budget_s and _t.monotonic() - t0 > time_budget_s and ep >= 3:
                break
            done = ep + 1
            self.rnd.shuffle(order)
            rate = lr / (1 + ep * 0.05)
            for i in order:
                x, y, wt = data[i]
                h = self._hidden(x)
                z = self.b2 + sum(a * b for a, b in zip(self.w2, h))
                p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
                g = (p - y) * wt                                  # d(logloss)/dz
                for j in range(self.H):
                    gh = g * self.w2[j] * (1 - h[j] * h[j])
                    self.w2[j] -= rate * (g * h[j] + l2 * self.w2[j])
                    self.b1[j] -= rate * gh
                    for k, v in x.items():
                        r = self.w1[k]
                        r[j] -= rate * (gh * v + l2 * r[j])
                self.b2 -= rate * g
                n_seen += 1
                if pause_every and n_seen % pause_every == 0:
                    _t.sleep(0.002)
        return done


# ------------------------------------------------------------------ brain
class Brain:
    def __init__(self, path: Path = BRAIN_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._train_lock = threading.Lock()   # one training at a time; never swap in an older network
        self.outages: list = []         # {"id","t","label","reason","note","title","feats"}
        self.purposes: list = []        # {"id","t","purpose","text"}
        self.net: Optional[TinyNet] = None
        self.trained_on = 0
        self._changes = 0                     # bumps on every new decision; the net knows which it saw
        self._net_changes = -1
        self.load()

    # ---------------------------------------------------------- persistence
    def load(self) -> None:
        try:
            if self.path.exists():
                data = self._read_valid(self.path)
                self.outages, self.purposes = data["outages"], data["purposes"]
        except Exception as exc:                     # never let a bad file stop monitoring
            bad = self.path.with_suffix(f".bad_{int(time.time())}.json")
            try:
                self.path.replace(bad)
            except OSError:
                pass
            log.warning("AI memory unreadable (%s) - kept as %s, starting fresh", exc, bad.name)
            self.outages, self.purposes = [], []
        self._changes += 1
        self._train_async()                       # never hold up the window at start

    def save(self) -> None:
        with self._lock:
            data = {"version": 1, "outages": list(self.outages), "purposes": list(self.purposes)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("AI memory not saved: %s", exc)

    @staticmethod
    def _read_valid(path: Path) -> dict:
        """Strict check of a brain / export file (it may come from another PC)."""
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("not a Netcool Shift Monitor AI file")
        outs, purs = [], []
        for e in raw.get("outages", [])[-MAX_EXAMPLES:]:
            feats = {int(k): float(v) for k, v in dict(e["feats"]).items()
                     if 0 <= int(k) < nlp.DIM and math.isfinite(float(v))}
            label = int(e["label"])
            if label not in (0, 1) or not feats:
                continue
            outs.append({"id": str(e["id"])[:40], "t": str(e.get("t", ""))[:25], "label": label,
                         "reason": str(e.get("reason", ""))[:60], "note": str(e.get("note", ""))[:500],
                         "title": str(e.get("title", ""))[:200], "team": str(e.get("team", ""))[:20],
                         "feats": feats})
        for e in raw.get("purposes", [])[-MAX_EXAMPLES:]:
            purs.append({"id": str(e["id"])[:40], "t": str(e.get("t", ""))[:25],
                         "purpose": str(e["purpose"])[:60], "text": str(e.get("text", ""))[:2000]})
        return {"outages": outs, "purposes": purs}

    def export_to(self, path, records=None) -> int:
        """One 'experience' file: the AI's learned decisions (+ the records database when given)."""
        with self._lock:
            data = {"version": 1, "outages": list(self.outages), "purposes": list(self.purposes)}
        if records is not None:
            data["records"] = records.export_experience()
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return len(data["outages"]) + len(data["purposes"])

    def import_from(self, path, records=None) -> int:
        """Merge another PC's experience (by id, nothing overwritten). Returns how many were new."""
        raw_path = Path(path)
        data = self._read_valid(raw_path)
        if records is not None:
            try:
                rec = json.loads(raw_path.read_text(encoding="utf-8")).get("records")
                if isinstance(rec, dict):
                    records.import_experience(rec)
            except Exception as exc:
                log.warning("records part of the experience file skipped: %s", exc)
        with self._lock:
            have = {e["id"] for e in self.outages} | {e["id"] for e in self.purposes}
            new_o = [e for e in data["outages"] if e["id"] not in have]
            new_p = [e for e in data["purposes"] if e["id"] not in have]
            self.outages = (self.outages + new_o)[-MAX_EXAMPLES:]
            self.purposes = (self.purposes + new_p)[-MAX_EXAMPLES:]
            self._changes += 1
        self.save()
        self._train_async()
        return len(new_o) + len(new_p)

    # ---------------------------------------------------------- learning
    def learn_outage(self, o, real: bool, purpose: str = "", reason: str = "", note: str = "",
                     team: str = "", context: dict = None) -> None:
        feats = outage_features(o, purpose, context=dict(context or {}, note=note))
        ex = {"id": uuid.uuid4().hex, "t": clock.now().isoformat(timespec="minutes"), "label": int(bool(real)),
              "reason": reason if not real else "", "note": note.strip()[:500], "title": o.title[:200],
              "team": team if real else "", "feats": feats}
        with self._lock:
            self.outages = (self.outages + [ex])[-MAX_EXAMPLES:]
            self._changes += 1
        log.info("AI learned: %s = %s %s", o.title, "REAL" if real else "NOT an outage", reason)
        self.save()
        self._train_async()

    def learn_purpose(self, filter_id: str, view_id: str, purpose: str, sample_text: str = "") -> None:
        ex = {"id": uuid.uuid4().hex, "t": clock.now().isoformat(timespec="minutes"), "purpose": purpose,
              "text": f"{filter_id} {view_id} {sample_text}"[:2000]}
        with self._lock:
            self.purposes = (self.purposes + [ex])[-MAX_EXAMPLES:]
        self.save()

    def counts(self) -> tuple:
        with self._lock:
            pos = sum(1 for e in self.outages if e["label"] == 1)
            return pos, len(self.outages) - pos, len(self.purposes)

    def _train_async(self) -> None:
        def low_priority():
            try:                                  # Windows: below-normal priority for the training thread
                import ctypes
                ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -1)
            except Exception:
                pass
            self._train()
        threading.Thread(target=low_priority, name="ai-train", daemon=True).start()

    def _train(self) -> None:
        with self._train_lock:
            self._train_now()

    def _train_now(self) -> None:
        with self._lock:
            data = [(e["feats"], e["label"]) for e in self.outages]
            changes = self._changes
        if changes == self._net_changes:
            return                                                  # already trained on exactly this
        pos = sum(y for _, y in data)
        neg = len(data) - pos
        if pos < MIN_EACH or neg < MIN_EACH:
            self.net, self.trained_on, self._net_changes = None, len(data), changes
            return
        wp, wn = len(data) / (2 * pos), len(data) / (2 * neg)      # balance real / not-real
        net = TinyNet()
        epochs = net.fit([(x, y, wp if y else wn) for x, y in data], time_budget_s=TRAIN_BUDGET_S, pause_every=50)
        log.info("AI trained on %d decisions (%d epochs)", len(data), epochs)
        self.net, self.trained_on, self._net_changes = net, len(data), changes   # swap when ready

    # ---------------------------------------------------------- advice
    def advise(self, o, purpose: str = "", context: dict = None) -> dict:
        """{'p': chance it is REAL, 'text': one line for the screen, 'reason': likely 'not' reason}."""
        pos, neg, _ = self.counts()
        net = self.net
        if net is None:
            return {"p": None, "text": f"AI: still learning ({pos} real / {neg} not-outage decisions so far)",
                    "reason": ""}
        x = outage_features(o, purpose, context=context)
        p = net.predict(x)
        with self._lock:
            sims = sorted(((nlp.cosine(x, e["feats"]), e) for e in self.outages), key=lambda t: -t[0])[:7]
        neg_near = [e for s, e in sims if s > 0.3 and e["label"] == 0]
        reason, seen = "", 0
        if neg_near:
            tally: dict = {}
            for e in neg_near:
                tally[e["reason"] or "Other"] = tally.get(e["reason"] or "Other", 0) + 1
            reason, seen = max(tally.items(), key=lambda t: t[1])
        if p <= 0.25:
            text = f"AI: likely NOT an outage ({round((1 - p) * 100)}%)"
            if reason:
                text += f" - similar cases were '{reason}' ({seen}x)"
        elif p >= 0.75:
            text = f"AI: looks like a REAL outage ({round(p * 100)}%)"
        else:
            text = f"AI: unsure ({round(p * 100)}% real) - check it"
        return {"p": p, "text": text, "reason": reason}

    def suggest_team(self, o, purpose: str = "") -> tuple:
        """(team, how many similar Global TTs went to it) from past REAL outages with a team; ('', 0) if unsure."""
        x = outage_features(o, purpose)
        with self._lock:
            near = [(nlp.cosine(x, e["feats"]), e["team"]) for e in self.outages if e["label"] == 1 and e.get("team")]
        near = sorted((t for t in near if t[0] > 0.3), key=lambda t: -t[0])[:7]
        tally: dict = {}
        for _, team in near:
            tally[team] = tally.get(team, 0) + 1
        if not tally:
            return "", 0
        team, n = max(tally.items(), key=lambda t: t[1])
        return (team, n) if n >= 2 else ("", 0)

    def learn_team(self, o, team: str, purpose: str = "") -> None:
        """A Global TT was opened on this team: remember it (and that the outage was real)."""
        with self._lock:
            done = next((e for e in reversed(self.outages) if e["title"] == o.title[:200] and e["label"] == 1), None)
            if done is not None:
                done["team"] = team
                self._changes += 1
        if done is None:
            self.learn_outage(o, True, purpose, team=team)
        else:
            self.save()

    def guess_purpose(self, filter_id: str, view_id: str, sample_text: str = "") -> tuple:
        """(purpose, confidence 0..1, why). Learned examples first; simple words when nothing is learned."""
        text = f"{filter_id} {view_id} {sample_text}"
        x = nlp.normalize(nlp.text_features(text, "p"))
        with self._lock:
            ex = list(self.purposes)
        best: dict = {}
        for e in ex:
            s = nlp.cosine(x, nlp.normalize(nlp.text_features(e["text"], "p")))
            best[e["purpose"]] = max(best.get(e["purpose"], 0.0), s)
        if best:
            purpose, score = max(best.items(), key=lambda t: t[1])
            if score >= 0.35:
                return purpose, min(1.0, score), f"like {len(ex)} learned sessions"
        # nothing learned matches: judge by the filter / view NAMES only (alarm texts mention everything)
        low = f"{filter_id} {view_id}".lower()
        for words, purpose in ((("heartbeat", "heart beat"), "Heartbeat"), (("power", "mains"), "Power alarms"),
                               (("_tx", " tx", "transmission", "microwave"), "Transmission (TX)"),
                               (("2g", "3g", "4g", "down"), PURPOSES[0])):
            if any(w in low for w in words):
                return purpose, 0.5, "from the filter name"
        return "", 0.0, ""
