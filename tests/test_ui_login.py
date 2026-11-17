"""Start-up and sign-out screens: the tool opens on the sign-in screen and there is only ever one sign-in widget."""
import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _has_display() -> bool:
    if sys.platform.startswith("win"):
        return True
    if not os.environ.get("DISPLAY"):
        return False
    try:
        tk.Tk().destroy()
        return True
    except tk.TclError:
        return False


@unittest.skipUnless(_has_display(), "needs a display")
class StartOnLogin(unittest.TestCase):
    def setUp(self):
        from core.config import SettingsRepository
        import ui.app as A
        self.A = A
        self.tmp = tempfile.TemporaryDirectory()
        repo = SettingsRepository(Path(self.tmp.name) / "settings.json")
        self.app = A.App(repo)
        self.app.update()

    def tearDown(self):
        with mock.patch.object(self.A.messagebox, "askyesno", return_value=True):
            self.app._on_close()
        self.tmp.cleanup()

    def _login_widgets(self):
        found, stack = [], [self.app]
        while stack:
            w = stack.pop()
            if isinstance(w, self.A.LoginView):
                found.append(w)
            stack.extend(w.winfo_children())
        return found

    def test_starts_on_the_sign_in_screen_covering_the_app(self):
        lv = self.app.login_view
        self.assertTrue(self.app.shell.login_shown)
        self.assertTrue(lv.winfo_ismapped())
        self.assertEqual(lv.winfo_height(), self.app.shell.body.winfo_height())   # covers sidebar + pages
        self.assertEqual(len(self._login_widgets()), 1)

    def test_sign_in_shows_the_app_and_sign_out_reuses_the_one_form(self):
        lv = self.app.login_view
        self.app.shell.show_page("Overview")
        self.app.shell.show_app()
        self.app.update()
        self.assertFalse(self.app.shell.login_shown)
        lv.pass_v.set("secret")
        lv.busy(True)
        self.app.sign_out()
        self.app.update()
        self.assertTrue(self.app.shell.login_shown)
        self.assertIs(self.app.login_view, lv)
        self.assertEqual(len(self._login_widgets()), 1)
        self.assertEqual(lv.pass_v.get(), "")                       # password cleared
        self.assertEqual(str(lv.btn.cget("state")), "normal")       # not stuck on "Signing in…"


if __name__ == "__main__":
    unittest.main()
