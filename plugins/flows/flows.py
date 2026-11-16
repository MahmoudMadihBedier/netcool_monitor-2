"""Recorded browser flows: what the engineer clicks and types once, replayed later with an outage's values.

A flow is a list of steps {action: click|fill|select|press|goto, selector, value, url}. Values typed during
recording that equal an outage field (FOC, site list, fault time ...) are stored as placeholders like
{foc}, so the replay fills the NEW outage's values. Saved as JSON in <app dir>/flows/<name>.json.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

PLACEHOLDERS = ("foc", "sites", "fault_time", "notes", "hub", "area", "count", "team")
_RECORDER_JS = r"""
() => {
  if (window.__nsmRec) return;
  window.__nsmRec = [];
  const sel = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    const al = el.getAttribute && el.getAttribute('aria-label');
    if (al) return el.tagName.toLowerCase() + '[aria-label="' + al + '"]';
    const txt = (el.innerText || el.value || '').trim().slice(0, 40);
    if (txt && ['BUTTON','A','OPTION'].includes(el.tagName)) return 'text=' + txt;
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 6; e = e.parentElement) {
      let p = e.tagName.toLowerCase();
      if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }
      const sib = Array.from(e.parentElement ? e.parentElement.children : []).filter(x => x.tagName === e.tagName);
      if (sib.length > 1) p += ':nth-of-type(' + (sib.indexOf(e) + 1) + ')';
      parts.unshift(p);
    }
    return parts.join(' > ');
  };
  document.addEventListener('click', e => window.__nsmRec.push({action: 'click', selector: sel(e.target), t: Date.now()}), true);
  document.addEventListener('change', e => {
    const el = e.target;
    if (el.tagName === 'SELECT') window.__nsmRec.push({action: 'select', selector: sel(el), value: el.value, t: Date.now()});
    else if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      if (el.type === 'password') window.__nsmRec.push({action: 'fill', selector: sel(el), value: '{password}', t: Date.now()});
      else window.__nsmRec.push({action: 'fill', selector: sel(el), value: el.value, t: Date.now()});
    }
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Enter') window.__nsmRec.push({action: 'press', selector: sel(e.target), value: 'Enter', t: Date.now()}); }, true);
}
"""


@dataclass
class Flow:
    name: str
    start_url: str
    steps: list = field(default_factory=list)      # dicts
    created: str = ""
    runs: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Flow":
        f = cls(name=str(d.get("name", "")), start_url=str(d.get("start_url", "")), created=str(d.get("created", "")),
                runs=int(d.get("runs", 0) or 0))
        for st in d.get("steps") or []:
            if isinstance(st, dict) and st.get("action") in ("click", "fill", "select", "press", "goto"):
                f.steps.append({"action": st["action"], "selector": str(st.get("selector", ""))[:500],
                                "value": str(st.get("value", ""))[:4000], "url": str(st.get("url", ""))[:2000]})
        return f


def outage_values(o, team: str = "") -> dict:
    """The values a recorded flow may need, by placeholder name."""
    ft = o.tt_fault_time
    return {"foc": o.foc or "", "sites": ", ".join(o.tt_sites), "fault_time": f"{ft:%d/%m/%Y %H:%M}" if ft else "",
            "notes": o.tt_notes, "hub": o.hub or "", "area": o.area or "", "count": str(o.count), "team": team}


def templatize(steps: list, values: dict) -> list:
    """Typed values that equal an outage value become placeholders ({foc}, {sites} ...). Passwords never kept."""
    out = []
    for st in steps:
        st = dict(st)
        if st.get("action") == "fill":
            v = st.get("value", "")
            if v == "{password}":
                out.append(st)
                continue
            for name in PLACEHOLDERS:
                val = values.get(name, "")
                if val and v.strip() == val.strip():
                    st["value"] = "{" + name + "}"
                    break
        out.append(st)
    return out


def dedupe(steps: list) -> list:
    """A click followed by typing in the same field is one 'fill'; repeated identical steps collapse."""
    out = []
    for st in steps:
        if out and st["action"] == "fill" and out[-1]["action"] == "click" and out[-1]["selector"] == st["selector"]:
            out.pop()
        if out and out[-1] == st:
            continue
        out.append(st)
    return out


def render(value: str, values: dict, password: str = "") -> str:
    def sub(m):
        key = m.group(1)
        if key == "password":
            return password
        return values.get(key, m.group(0))
    return re.sub(r"\{(\w+)\}", sub, value or "")


class FlowStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe = re.sub(r"[^\w\- ]+", "_", name).strip() or "flow"
        return self.folder / f"{safe}.json"

    def names(self) -> list:
        return sorted(p.stem for p in self.folder.glob("*.json"))

    def load(self, name: str) -> Optional[Flow]:
        p = self._path(name)
        if not p.exists():
            return None
        try:
            return Flow.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return None

    def save(self, flow: Flow) -> Path:
        p = self._path(flow.name)
        p.write_text(json.dumps(flow.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    def delete(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            p.unlink()


# ------------------------------------------------------------------ record / replay on a Playwright page
def start_recording(page) -> None:
    page.add_init_script(_RECORDER_JS)
    page.evaluate(_RECORDER_JS)


def collect_recording(page) -> list:
    """Steps recorded so far on the page (plus 'goto' steps for navigations seen between calls)."""
    try:
        steps = page.evaluate("() => (window.__nsmRec || [])") or []
    except Exception:
        steps = []
    return [{"action": s.get("action"), "selector": s.get("selector", ""), "value": s.get("value", ""), "url": ""}
            for s in steps if s.get("action")]


def replay(page, flow: Flow, values: dict, password: str = "", stop_before_last_click: bool = True,
           step_timeout_ms: int = 15000, on_step=None) -> int:
    """Replays a flow with the values filled in. By default the LAST click (usually Submit) is left to the
    engineer, so nothing is sent without a human. Returns the number of steps done."""
    steps = flow.steps
    last_click = max((i for i, s in enumerate(steps) if s["action"] in ("click", "press")), default=-1)
    done = 0
    if flow.start_url:
        page.goto(render(flow.start_url, values), wait_until="domcontentloaded")
    for i, st in enumerate(steps):
        if stop_before_last_click and i == last_click:
            break
        act, sel = st["action"], st["selector"]
        val = render(st.get("value", ""), values, password)
        if on_step:
            on_step(i, st)
        if act == "goto":
            page.goto(render(st.get("url", ""), values), wait_until="domcontentloaded")
        elif act == "click":
            page.click(sel, timeout=step_timeout_ms)
        elif act == "fill":
            page.fill(sel, val, timeout=step_timeout_ms)
        elif act == "select":
            page.select_option(sel, val, timeout=step_timeout_ms)
        elif act == "press":
            page.press(sel or "body", val or "Enter", timeout=step_timeout_ms)
        done += 1
        time.sleep(0.15)
    return done
