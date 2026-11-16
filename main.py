"""Vodafone Egypt - Netcool Shift Monitor (entry point)."""
import logging
import os
import socket
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import APP_DIR, LOG_FILE, SettingsRepository  # noqa: E402


def setup_logging() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=3_000_000, backupCount=8, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    def hook(args):  # never lose a background-thread crash
        logging.getLogger("thread").error("Uncaught in %s", args.thread.name,
                                          exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = hook


def _use_bundled_browser() -> None:
    """Portable build: the tool's own Chromium ships inside the folder (no download, no Edge needed)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    if (base / "playwright" / "driver" / "package" / ".local-browsers").is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"      # always the bundled one (ignore PC-wide settings)


_INSTANCE_LOCK = None


def _single_instance() -> bool:
    """Two copies of the tool = two MOM sign-ins that log each other out. Allow only one per user."""
    global _INSTANCE_LOCK
    port = 47000 + (sum(map(ord, os.environ.get("USERNAME", os.environ.get("USER", "u")))) % 900)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _INSTANCE_LOCK = s
    return True


def main() -> None:
    setup_logging()
    _use_bundled_browser()
    if not _single_instance():
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Netcool Shift Monitor", "The tool is already running (check the taskbar).\n\n"
                                "A second copy would sign in to MOM again and log the first one out.")
            r.destroy()
        except Exception:
            pass
        return
    try:
        import requests  # noqa: F401
    except ImportError:
        sys.exit("Missing dependency. Run:  pip install -r requirements.txt")
    from ui.app import App
    app = App(SettingsRepository())
    app.report_callback_exception = lambda *a: logging.getLogger("tk").error("Tk error", exc_info=a)
    app.mainloop()


if __name__ == "__main__":
    main()
