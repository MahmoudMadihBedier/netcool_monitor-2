"""v12 kernel: event bus, host settings sections, plugin manifests, order, failures."""
import json
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import Settings, SettingsRepository  # noqa: E402
from kernel import EventBus, Host, Manifest, PluginManager, Topics, order, read_manifests  # noqa: E402


class Bus(unittest.TestCase):
    def test_publish_ui_queue_and_failures_isolated(self):
        bus, got = EventBus(), []
        bus.subscribe(Topics.OUTAGE_ALERT, lambda p: (_ for _ in ()).throw(RuntimeError("boom")), owner="bad")
        bus.subscribe(Topics.OUTAGE_ALERT, got.append, owner="good")
        bus.subscribe(Topics.OUTAGE_ALERT, lambda p: got.append(("ui", p)), ui=True, owner="ui")
        bus.subscribe("*", lambda t, p: got.append(("any", t)), owner="any")
        self.assertEqual(bus.publish(Topics.OUTAGE_ALERT, 1), 4)
        self.assertEqual(got, [1, ("any", Topics.OUTAGE_ALERT)])          # bad one did not stop the rest
        self.assertEqual(bus.pump(), 1)
        self.assertIn(("ui", 1), got)
        bus.unsubscribe_owner("good")
        self.assertEqual(bus.subscribers(Topics.OUTAGE_ALERT), 2)

    def test_thread_safe_publish(self):
        bus, n = EventBus(), []
        bus.subscribe("t", lambda p: n.append(p))
        ths = [threading.Thread(target=lambda: [bus.publish("t", i) for i in range(200)]) for _ in range(8)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(len(n), 1600)


@dataclass
class MySettings:
    threshold: int = 3
    words: str = "power"


class HostApi(unittest.TestCase):
    def test_settings_section_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            repo = SettingsRepository(Path(d) / "settings.json")
            s = Settings()
            host = Host(s, repo, Path(d))
            cfg = host.settings_section("my", MySettings)
            cfg.threshold = 7
            host.save_settings()
            again = Host(repo.load(), repo, Path(d)).settings_section("my", MySettings)
            self.assertEqual(again.threshold, 7)
            self.assertEqual(again.words, "power")
            # unknown keys from a newer version are ignored, not fatal
            raw = json.loads((Path(d) / "settings.json").read_text())
            raw["plugins"]["my"]["future"] = 1
            (Path(d) / "settings.json").write_text(json.dumps(raw))
            self.assertEqual(Host(repo.load(), repo, Path(d)).settings_section("my", MySettings).threshold, 7)


def _plugin(folder: Path, name, requires=(), body="", enabled=True):
    (folder / name).mkdir(parents=True)
    (folder / name / "plugin.json").write_text(json.dumps({"name": name, "title": name, "version": "1",
                                                           "entry": "plugin:P", "requires": list(requires),
                                                           "enabled": enabled}))
    (folder / name / "plugin.py").write_text(body or f"""
class P:
    def start(self, host):
        host.offer('{name}', self)
        host.bus.publish('plugin.{name}.started', None)
    def stop(self): pass
""")
    (folder / name / "__init__.py").write_text("")


class Loader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "plugins"
        (self.root).mkdir()
        (self.root / "__init__.py").write_text("")
        sys.path.insert(0, self.tmp.name)       # so `plugins.<name>` imports from the temp folder
        import importlib
        self.importlib = importlib

    def tearDown(self):
        sys.path.remove(self.tmp.name)
        for k in [k for k in sys.modules if k.startswith("plugins.") or k == "plugins"]:
            del sys.modules[k]
        self.tmp.cleanup()

    def test_order_and_missing_requirement(self):
        ms = [Manifest("b", "b", "1", "plugin:P", self.root, ["a"]), Manifest("a", "a", "1", "plugin:P", self.root),
              Manifest("c", "c", "1", "plugin:P", self.root, ["zzz"]), Manifest("d", "d", "1", "plugin:P", self.root, [], False)]
        self.assertEqual([m.name for m in order(ms)], ["a", "b"])
        self.assertEqual([m.name for m in order(ms, disabled={"a"})], [])

    def test_circular_is_reported_not_fatal(self):
        ms = [Manifest("a", "a", "1", "plugin:P", self.root, ["b"]), Manifest("b", "b", "1", "plugin:P", self.root, ["a"]),
              Manifest("c", "c", "1", "plugin:P", self.root)]
        self.assertEqual([m.name for m in order(ms)], ["c"])

    def test_start_all_skips_broken_plugin(self):
        _plugin(self.root, "alpha")
        _plugin(self.root, "beta", requires=["alpha"])
        _plugin(self.root, "broken", body="class P:\n    def start(self, host): raise RuntimeError('x')\n    def stop(self): pass\n")
        (self.root / "junk").mkdir()
        (self.root / "junk" / "plugin.json").write_text("{not json")
        with tempfile.TemporaryDirectory() as d:
            host = Host(Settings(), SettingsRepository(Path(d) / "s.json"), Path(d))
            seen = []
            host.bus.subscribe("*", lambda t, p: seen.append(t))
            pm = PluginManager(host, self.root)
            pm.start_all()
            self.assertEqual(sorted(pm.loaded), ["alpha", "beta"])
            self.assertIn("broken", pm.failed)
            self.assertEqual(seen, ["plugin.alpha.started", "plugin.beta.started"])
            self.assertIs(host.service("alpha"), pm.get("alpha"))
            pm.stop_all()
            self.assertEqual(pm.loaded, {})
            self.assertEqual(len(read_manifests(self.root)), 3)


if __name__ == "__main__":
    unittest.main()
