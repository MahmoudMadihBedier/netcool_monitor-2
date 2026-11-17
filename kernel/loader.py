"""Finds and starts plugins.

A plugin is a folder under `plugins/` with:
    plugin.json   {"name": "remedy", "title": "Global TT (Remedy)", "version": "1.0",
                   "entry": "plugin:RemedyPlugin", "requires": ["records"], "enabled": true}
    plugin.py     the module named in "entry", with the class named after the colon.

Plugins start in dependency order; one that fails to start is logged and skipped - the core keeps running.
A user can switch a plugin off in settings.json ("plugins_disabled": ["remedy"]).
"""
from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"


@dataclass
class Manifest:
    name: str
    title: str
    version: str
    entry: str
    folder: Path
    requires: list = field(default_factory=list)
    enabled: bool = True


class PluginError(Exception):
    pass


def read_manifests(folder: Path = PLUGINS_DIR) -> list:
    out = []
    for mf in sorted(folder.glob("*/plugin.json")):
        try:
            d = json.loads(mf.read_text(encoding="utf-8"))
            name = str(d["name"])
            if not name.isidentifier() or ":" not in str(d.get("entry", "")):
                raise ValueError("name must be an identifier and entry 'module:Class'")
            out.append(Manifest(name=name, title=str(d.get("title", name)), version=str(d.get("version", "0")),
                                entry=str(d["entry"]), folder=mf.parent, requires=list(d.get("requires", [])),
                                enabled=bool(d.get("enabled", True))))
        except Exception as exc:
            log.warning("plugin manifest %s ignored: %s", mf, exc)
    return out


def order(manifests: list, disabled: set = frozenset()) -> list:
    """Dependency order; a plugin whose requirement is missing / disabled is skipped (with a log line)."""
    by_name = {m.name: m for m in manifests if m.enabled and m.name not in disabled}
    done, out = set(), []

    def visit(m: Manifest, chain: tuple):
        if m.name in done:
            return True
        if m.name in chain:
            raise PluginError(f"circular requirement: {' -> '.join(chain + (m.name,))}")
        for req in m.requires:
            dep = by_name.get(req)
            if dep is None or not visit(dep, chain + (m.name,)):
                log.warning("plugin %s skipped: requires '%s' which is missing or disabled", m.name, req)
                return False
        done.add(m.name)
        out.append(m)
        return True

    for m in list(by_name.values()):
        try:
            visit(m, ())
        except PluginError as exc:
            log.error("%s", exc)
    return out


class PluginManager:
    def __init__(self, host, folder: Path = PLUGINS_DIR):
        self.host, self.folder = host, folder
        self.loaded: dict = {}       # name -> (manifest, instance)
        self.failed: dict = {}       # name -> error text

    def start_all(self) -> None:
        disabled = set(getattr(self.host.settings, "plugins_disabled", []) or [])
        for m in order(read_manifests(self.folder), disabled):
            self.start(m)

    def start(self, m: Manifest) -> bool:
        mod_name, cls_name = m.entry.split(":", 1)
        try:
            module = importlib.import_module(f"plugins.{m.folder.name}.{mod_name}")
            inst = getattr(module, cls_name)()
            inst.start(self.host)
            self.loaded[m.name] = (m, inst)
            log.info("plugin %s %s started", m.name, m.version)
            return True
        except Exception as exc:
            self.failed[m.name] = f"{type(exc).__name__}: {exc}"
            log.exception("plugin %s failed to start - skipped", m.name)
            return False

    def stop_all(self) -> None:
        for name, (_mf, inst) in reversed(list(self.loaded.items())):
            try:
                inst.stop()
            except Exception:
                log.exception("plugin %s failed to stop", name)
            self.host.bus.unsubscribe_owner(name)
        self.loaded.clear()

    def get(self, name: str):
        item = self.loaded.get(name)
        return item[1] if item else None
