"""The application: window shell + the monitoring core (sign-in, sessions, alerts) + the plugin host.

Everything else - AI, Remedy, heartbeat, mail, Outlook, records, session rules - is a plugin under plugins/.
This file knows nothing about them: it starts the kernel, and the shell shows whatever they register.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from core import clock
from core import messages as m
from core.cascade import CascadeError, CascadeStore
from core.config import ALERT_HISTORY_DIR, APP_DIR, PROJECT_DIR, Settings, SettingsRepository
from core.credentials import CredentialStore
from core.history import AlertHistory
from core.netcool_api import AuthError, NetcoolClient, NetcoolError
from core.power import keep_awake
from core.server_session import KeepAliveThread
from core.sound import SoundPlayer
from kernel import Host, MenuItem, PluginManager, Topics
from . import theme as T
from .alerts_controller import AlertsController
from .conflict import ConflictNotice
from .dialogs import AlertCenter, SettingsDialog
from .login_view import LoginView
from .modal import SoundDialog
from .outage_view import OutageActions, OutageDialog
from .overview import OverviewPage
from .session_manager import SessionManager
from .shell import Shell, auto_scale
from .widgets import Toast

VERSION = "v12"
log = logging.getLogger("app")


class _Done:
    """Result of a background job, delivered through the bus."""
    def __init__(self, cb, result=None, error=None):
        self.cb, self.result, self.error = cb, result, error


class _Conflict:
    def __init__(self, note: str):
        self.note = note


class App(tk.Tk):
    def __init__(self, repo: SettingsRepository):
        super().__init__()
        self.repo = repo
        self.settings: Settings = repo.load()
        self.settings.server.timezone = clock.set_zone(self.settings.server.timezone)
        T.set_mode(self.settings.ui.get("theme", "light"))
        T.set_scale(self.settings.ui.get("scale") or auto_scale(self))
        self.title(f"Vodafone Egypt · Netcool Shift Monitor {VERSION}")
        self._fit_screen()
        T.apply(self)
        self._set_icon()

        # ---- core objects
        self.creds = CredentialStore()
        self.bus: "queue.Queue" = queue.Queue()            # core messages (workers -> UI)
        self.sound = SoundPlayer(self, self.settings.monitor.alarm_wav, self.settings.monitor.alarm_volume)
        self.history = AlertHistory()
        self.cascade = CascadeStore()
        self.client: NetcoolClient | None = None
        self._login_client: NetcoolClient | None = None
        self.keepalive: KeepAliveThread | None = None
        self.filters: list = []
        self.views_list: list = []
        self.shift_start: datetime | None = None

        # ---- plugin host
        self.host = Host(self.settings, repo, APP_DIR)
        self.host.ui = self
        self.sm = SessionManager(self)
        self.host.sessions = self.sm.infos
        self.host.plan_of = self.sm.plan_of
        self.host.purpose_of = self.sm.purpose_of
        self.host.outage_events = self.sm.events_of
        self.host.on_actions_changed = self._actions_changed
        self.actions = OutageActions(lambda: [(a.label, a.command, a.primary) for a in self.host.outage_actions])

        # ---- window
        self.shell = Shell(self, self.set_theme, VERSION)
        self.alerts = AlertCenter(self, self.sound, on_change=lambda n: self.overview.refresh(),
                                  on_ack=lambda key: self.alerts_ctl.on_ack(key, True))
        self.alerts_ctl = AlertsController(self)
        self.conflict = ConflictNotice(self, self.open_mom, self.shell.status)
        self.overview = OverviewPage(self.shell.pages, self)
        self.shell.add_page("Overview", self.overview, icon="▦")
        self._build_menus()
        self.cascade_lbl = self.shell.status_widget("Cascaded report: loading…", self.upload_cascade)
        self.zone_lbl = self.shell.status_widget("", self.open_settings)
        self._show_zone()

        # ---- plugins
        self.plugins = PluginManager(self.host)
        self.plugins.start_all()
        for item in self.host.menus:
            self.shell.add_menu_item(item)
        for name, err in self.plugins.failed.items():
            Toast.show(self, f"Plugin '{name}' could not start: {err[:120]}", T.RED_DARK, 9000)

        self.login_view = LoginView(self.shell.body, self.settings, self.creds, self._do_login)
        self.shell.show_login(self.login_view)          # the tool always starts on the sign-in screen
        self.host.run_bg(lambda: self.cascade.load_initial(self.settings.cascade.active_path),
                         lambda rep, err: self._cascade_changed())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self._pump)
        self.after(1000, self._tick)

    # ================================================================ window pieces
    def _fit_screen(self) -> None:
        """Start maximised on any monitor / ratio; a sane minimum so nothing is cut on a 1366x768 laptop."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.minsize(min(1000, sw - 40), min(620, sh - 60))
        self.geometry(f"{int(sw * 0.9)}x{int(sh * 0.85)}+{int(sw * 0.05)}+{int(sh * 0.04)}")
        try:
            self.state("zoomed")                     # Windows
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)     # Linux
            except tk.TclError:
                pass

    def _set_icon(self) -> None:
        try:
            ico = PROJECT_DIR / "assets" / "app.ico"
            if ico.exists() and sys.platform.startswith("win"):
                self.iconbitmap(default=str(ico))
        except Exception:
            pass

    def _build_menus(self) -> None:
        add, sh = self.shell.add_menu_item, self.shell
        add(MenuItem("New session…", self.sm.new, "Sessions", "Ctrl+N", toolbar=True, icon="＋"))
        add(MenuItem("Restore last sessions", self.sm.restore, "Sessions", icon="↺"))
        add(MenuItem("Refresh all now", lambda: [v.worker.refresh_now() for v in self.sm.views.values()],
                     "Sessions", "F5", icon="⟳"))
        add(MenuItem("Close current session", self._close_current, "Sessions", "Ctrl+W"))
        add(MenuItem("Cascaded Sites Report…", self.upload_cascade, "File", "Ctrl+O", icon="📄"))
        add(MenuItem("Settings / rules…", self.open_settings, "File", "Ctrl+,", icon="⚙"))
        add(MenuItem("Alarm sound…", self.open_sound, "File", icon="🔊"))
        sh.add_separator("File")
        add(MenuItem("Sign out", self.sign_out, "File"))
        add(MenuItem("Exit", self._on_close, "File", "Alt+F4"))
        add(MenuItem("Open MOM (shares the tool's login)", self.open_mom, "Tools", "Ctrl+M", toolbar=True, icon="🌐"))
        add(MenuItem("Diagnostics…", self.run_diagnostics, "Tools", icon="🩺"))
        add(MenuItem("Alert history folder", self._open_history, "Tools"))
        add(MenuItem("Test alert", self._test_alert, "Tools"))
        add(MenuItem("Light theme", lambda: self.set_theme("light"), "View"))
        add(MenuItem("Dark theme", lambda: self.set_theme("dark"), "View"))
        sh.add_separator("View")
        for label, scale in (("Scale: automatic", 0), ("Scale: 90 %", 0.9), ("Scale: 100 %", 1.0),
                             ("Scale: 115 %", 1.15), ("Scale: 130 %", 1.3), ("Scale: 150 %", 1.5)):
            add(MenuItem(label, lambda s=scale: self.set_scale(s), "View"))
        add(MenuItem("About", self._about, "Help"))
        add(MenuItem("Plugins loaded…", self._plugins_info, "Help"))
        self.mute_v = tk.BooleanVar()
        ttk.Checkbutton(self.shell.toolbar, text="Mute sound", variable=self.mute_v,
                        command=lambda: self.sound.set_muted(self.mute_v.get())).pack(side="right", padx=T.px(6))

    def _actions_changed(self) -> None:
        for v in self.sm.views.values():
            try:
                v.refresh_actions()
            except tk.TclError:
                pass

    def set_theme(self, mode: str) -> None:
        self.settings.ui["theme"] = mode
        self.repo.save(self.settings)
        messagebox.showinfo("Theme", "The theme is applied the next time the tool starts.", parent=self)

    def set_scale(self, scale: float) -> None:
        self.settings.ui["scale"] = scale
        self.repo.save(self.settings)
        messagebox.showinfo("Scale", "The new size is applied the next time the tool starts.", parent=self)

    def _show_zone(self) -> None:
        try:
            self.zone_lbl.config(text=f"Times: {clock.label()}")
        except tk.TclError:
            pass

    def _about(self) -> None:
        messagebox.showinfo("About", f"Netcool Shift Monitor {VERSION}\nMahmoud Madih and the Access Ericsson team, "
                            "Vodafone Egypt\n\nCore: MOM monitoring + FO-manual outage rules.\n"
                            f"Plugins: {', '.join(self.plugins.loaded) or 'none'}", parent=self)

    def _plugins_info(self) -> None:
        lines = [f"✔ {mf.title} ({name} {mf.version})" for name, (mf, _) in self.plugins.loaded.items()]
        lines += [f"✖ {name}: {err}" for name, err in self.plugins.failed.items()]
        messagebox.showinfo("Plugins", "\n".join(lines) or "No plugins.", parent=self)

    def bus_publish(self, topic: str, payload=None) -> None:
        self.host.bus.publish(topic, payload)

    def status(self, text: str) -> None:
        self.shell.status(text)

    # ================================================================ sign-in
    def _do_login(self, user: str, pw: str, remember: bool, url: str) -> None:
        if url:
            self.settings.server.base_url = url
        cfg = self.settings.server
        say = lambda text: self.bus.put(m.ServerStatus(True, text))  # noqa: E731
        self.login_view.msg.config(text="Starting connection engine and signing in…")

        def work():
            c = self._login_client
            if c is None or c.base != cfg.base_url.rstrip("/") or c.cfg.transport != cfg.transport:
                if c is not None:
                    c.close()
                c = self._login_client = NetcoolClient(cfg, on_log=say)
            c.login(user, pw)
            return c, c.list_filters(), c.list_views()

        def done(res, err):
            if err:
                self.login_view.set_error(str(err) if isinstance(err, (AuthError, NetcoolError)) else f"Error: {err}")
                return
            self.client, self.filters, self.views_list = res
            self._login_client = None
            self.host.client = self.client
            self.client.on_relogin = lambda ok, d: self.bus.put(m.ServerStatus(ok, d))
            self.client.on_conflict = lambda note: self.bus.put(_Conflict(note))
            if self.client.conflict_note:
                self.bus.put(_Conflict(self.client.conflict_note))
            if user not in self.settings.saved_usernames:
                self.settings.saved_usernames.append(user)
            self.settings.last_username = user
            self.repo.save(self.settings)
            if remember:
                self.creds.save(user, pw)
            self.keepalive = KeepAliveThread(self.client, self.bus, self.settings.server.keepalive_s)
            self.keepalive.start()
            self.shift_start = clock.now()
            self.shell.server_lbl.config(text=f"●  {user}  ·  connected", fg="#FFFFFF")
            self.status(f"Signed in ({self.client.mode} mode). {len(self.filters)} filters, "
                        f"{len(self.views_list)} views loaded.")
            self.shell.show_page("Overview")
            self.shell.show_app()
            self.after(300, self._shift_start_flow)

        self.host.run_bg(work, done)

    def _shift_start_flow(self) -> None:
        self._ask_cascade_for_shift()
        if self.settings.last_sessions and messagebox.askyesno(
                "Restore sessions", f"Re-open the {len(self.settings.last_sessions)} session(s) from your last shift?",
                parent=self):
            self.sm.restore()

    def _ask_cascade_for_shift(self) -> None:
        rep = self.cascade.report
        try:
            if clock.now() - datetime.fromisoformat(self.settings.cascade.uploaded_at) < timedelta(hours=10):
                return
        except (ValueError, TypeError):
            pass
        cur = (f"{rep.report_date:%d-%b-%Y}" if rep and rep.report_date else "unknown date") if rep else "none"
        age = rep.age_days() if rep else None
        msg = (f"Upload today's Cascaded Sites Report?\n\nReport in use: {cur}"
               + (f"  ({age} days old)" if age is not None else "")
               + "\n\nYes = choose the new CSV file\nNo = continue with the report in use")
        if messagebox.askyesno("Cascaded Sites Report", msg, parent=self):
            self.upload_cascade()

    def sign_out(self) -> None:
        if self.sm.views and not messagebox.askyesno("Sign out", "All sessions will stop. Sign out?", parent=self):
            return
        self.sm.stop_all(save=True)
        keep_awake(False)
        if self.keepalive:
            self.keepalive.stop()
        if self.client:
            c = self.client
            threading.Thread(target=lambda: (c.logout(), c.close()), daemon=True).start()
        self.client = self.host.client = None
        self.shift_start = None
        self.shell.server_lbl.config(text="Not signed in", fg="#FDE8E8")
        self.login_view.reset(self.settings)
        self.shell.show_login(self.login_view)

    # ================================================================ cascaded report
    def upload_cascade(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose the Cascaded Sites Report (CSV)",
                                          filetypes=[("Cascaded Sites Report", "*.csv *.CSV"), ("All files", "*.*")])
        if not path:
            return
        self.status("Checking the Cascaded Sites Report…")

        def done(rep, err):
            if err:
                msg = str(err) if isinstance(err, CascadeError) else f"Could not read the file: {err}"
                messagebox.showerror("Cascaded Sites Report", msg + "\n\nThe previous report stays in use.", parent=self)
                self.status("Cascaded report NOT changed.")
                return
            self.settings.cascade.active_path = rep.path
            self.settings.cascade.uploaded_at = clock.now().isoformat(timespec="minutes")
            self.repo.save(self.settings)
            self._cascade_changed()
            info = f"Loaded {rep.rows:,} sites ({rep.with_chain:,} with cascaded sites)."
            if rep.report_date:
                info += f"\nReport date: {rep.report_date:%d-%b-%Y}"
            if rep.warnings:
                info += "\n\nNotes:\n- " + "\n- ".join(rep.warnings[:5])
            messagebox.showinfo("Cascaded Sites Report", info, parent=self)
            for v in self.sm.views.values():
                v.worker.set_severities(v.worker.severities)     # re-evaluate with the new chains
        self.host.run_bg(lambda: self.cascade.upload(path), done)

    def _cascade_changed(self) -> None:
        rep = self.cascade.report
        if rep:
            age = rep.age_days()
            old = age is not None and age > self.settings.cascade.max_age_days
            txt = f"Cascaded report: {rep.report_date:%d-%b-%Y}" if rep.report_date else "Cascaded report: loaded"
            if age is not None:
                txt += f" ({age} days old)"
            self.cascade_lbl.config(text=txt + "  - click to upload", fg=T.RED if old else T.GREY_6)
            self.overview.ok["casc"].set(f"{rep.report_date:%d-%b}" if rep.report_date else "OK",
                                         T.RED if old else T.BLACK)
        else:
            self.cascade_lbl.config(text="Cascaded report: NOT loaded - click to upload", fg=T.RED)
            self.overview.ok["casc"].set("none", T.RED)

    # ================================================================ sessions (delegated)
    def _close_current(self) -> None:
        v = self.sm.current()
        if v and messagebox.askyesno("Close session", f"Close session '{v.worker.session_name}'?", parent=self):
            self.sm.close(v.worker.sid)

    def show_session(self, sid: int) -> None:
        v = self.sm.views.get(sid)
        if v:
            self.shell.show_page(v.page_name)

    def update_power(self) -> None:
        keep_awake(bool(self.sm.views) and self.settings.monitor.prevent_sleep)

    def on_session_state(self, view) -> None:
        mark = T.STATE_MARK.get(view.state_name, "○")
        if view.alert_count:
            mark = "⚠"
        elif view.pre_count:
            mark = "△"
        self.shell.rename_page(view.page_name, f"{mark}  {view.worker.session_name}")
        self.overview.refresh()

    def open_outage(self, sid: int, o) -> None:
        v = self.sm.views.get(sid)
        OutageDialog(self, o, sid, v.worker.session_name if v else "?", self.actions, self.settings.rules.tt_minutes)

    # ================================================================ tools
    def open_sound(self) -> None:
        res = SoundDialog(self, self.sound, self.settings.monitor.alarm_wav, self.settings.monitor.alarm_volume).run()
        if res:
            self.settings.monitor.alarm_wav, self.settings.monitor.alarm_volume = res
            self.repo.save(self.settings)
        self.sound.set_custom(self.settings.monitor.alarm_wav)
        self.sound.set_volume(self.settings.monitor.alarm_volume)

    def open_mom(self) -> None:
        """A browser window that shares the tool's login (one MOM session for the user and the tool)."""
        if not self.client:
            return
        t = getattr(self.client, "t", None)
        if not hasattr(t, "open_mom_window"):
            messagebox.showinfo("Open MOM", "The tool is in 'direct' connection mode, so it cannot share its login "
                                "with a browser window.\n\nIf you open MOM in Edge with the SAME user, MOM may log "
                                "the tool out. Use another MOM user in Edge, or switch the connection to 'browser' "
                                "in Settings.", parent=self)
            return
        self.status("Opening MOM with the tool's login…")
        url = self.client.base + "/ibm/console/"

        def done(ch, err):
            if err:
                messagebox.showerror("Open MOM", f"Could not open the MOM window:\n{err}", parent=self)
                return
            Toast.show(self, "MOM opened with the tool's login - use THIS window instead of signing in again "
                             "in Edge (one MOM session for you and the tool).", T.GREEN, 8000)
            self.status(f"MOM window opened ({ch}).")
        self.host.run_bg(lambda: t.open_mom_window(url), done)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.settings)
        if not dlg.result:
            return
        keep = ("saved_usernames", "last_username", "last_sessions", "plugins", "plugins_disabled", "ui", "remedy")
        for k in keep:
            setattr(dlg.result, k, getattr(self.settings, k))
        dlg.result.cascade.active_path = self.settings.cascade.active_path
        dlg.result.cascade.uploaded_at = self.settings.cascade.uploaded_at
        self.settings = self.host.settings = dlg.result
        self.settings.server.timezone = clock.set_zone(self.settings.server.timezone)
        if self.client:
            self.client.cfg.timezone = self.settings.server.timezone
        self._show_zone()
        self.sound.set_custom(self.settings.monitor.alarm_wav)
        self.host.save_settings()
        for v in self.sm.views.values():
            v.worker.apply_settings(self.settings)
            v.update_filter_labels()
        self.update_power()
        self._cascade_changed()
        self.status("Settings saved and applied to all sessions.")

    def _test_alert(self) -> None:
        self.alerts.show(("test",), "TEST ALERT", "If you can see this window and hear the alarm,\nnotifications "
                         "are working.", session="Test")

    def run_diagnostics(self) -> None:
        if not self.client:
            return
        v = self.sm.current() or next(iter(self.sm.views.values()), None)
        if v is None:
            messagebox.showinfo("Diagnostics", "Open a session first (the test uses its filter).", parent=self)
            return
        fid, vid, client, rep = v.worker.filter_id, v.worker.view_id, self.client, self.cascade.report
        self.status("Running diagnostics…")

        def done(lines, err):
            t = getattr(client, "t", None)
            head = [f"Time: {clock.now():%Y-%m-%d %H:%M:%S}", f"Version: {VERSION}", f"Filter: {fid}", f"View: {vid}",
                    f"Cascaded report: {rep.path if rep else 'none'}" + (f" ({rep.rows} sites)" if rep else ""),
                    f"Browser engine: {getattr(t, 'channel_used', '-') or '-'}"
                    + "".join(f"\n   not used: {x}" for x in getattr(t, 'tried', [])),
                    f"Alarm sound: {self.sound.backend} ({self.sound.wav})"
                    + (f"  last problem: {self.sound.last_error}" if self.sound.last_error else ""),
                    f"Same-user note: {client.conflict_note or 'none'}",
                    f"Plugins: {', '.join(self.plugins.loaded) or 'none'}"
                    + "".join(f"\n   failed: {n}: {e}" for n, e in self.plugins.failed.items())]
            lines = head + (list(lines or []) if not err else [f"Diagnostics crashed: {err}"])
            path = APP_DIR / f"diagnostics_{clock.now():%Y%m%d_%H%M%S}.txt"
            try:
                path.write_text("\n".join(lines), encoding="utf-8")
            except Exception:
                pass
            self.status(f"Diagnostics saved: {path}")
            win = tk.Toplevel(self)
            win.title("Diagnostics")
            txt = tk.Text(win, width=110, height=24, font=(T.MONO, T.fs(9)), wrap="word")
            txt.insert("1.0", "\n".join(lines) + f"\n\nSaved to: {path}")
            txt.pack(fill="both", expand=True, padx=10, pady=10)
        self.host.run_bg(lambda: client.diagnose(fid, vid), done)

    def _open_history(self) -> None:
        ALERT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                import os
                os.startfile(str(ALERT_HISTORY_DIR))  # noqa
            else:
                messagebox.showinfo("Alert history", f"Folder: {ALERT_HISTORY_DIR}", parent=self)
        except Exception as exc:
            messagebox.showinfo("Alert history", f"Folder: {ALERT_HISTORY_DIR}\n({exc})", parent=self)

    # ================================================================ message pumps
    def _pump(self) -> None:
        try:
            for _ in range(500):
                self._dispatch(self.bus.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            log.exception("UI dispatch error")
        self.host.bus.pump()
        self.after(250, self._pump)

    def _dispatch(self, msg) -> None:
        if isinstance(msg, _Done):
            msg.cb(msg.result, msg.error)
            return
        if isinstance(msg, _Conflict):
            self.conflict.show(msg.note)
            return
        if isinstance(msg, m.ServerStatus):
            self.host.bus.publish(Topics.SERVER_STATUS, msg)
            if not self.client:
                self.status(msg.detail)
                return
            user = self.client.user or ""
            if msg.ok:
                left = f"  ·  server session {msg.seconds_left // 60} min" if msg.seconds_left > 0 else ""
                self.shell.server_lbl.config(text=f"●  {user}  ·  connected{left}", fg="#FFFFFF")
            else:
                self.shell.server_lbl.config(text=f"●  {user}  ·  {msg.detail[:60]}", fg="#000000")
            self.status(f"{msg.at:%H:%M:%S}  {msg.detail}")
            return
        view = self.sm.views.get(getattr(msg, "sid", -1))
        if view is None:
            return
        view.handle(msg)
        self.alerts_ctl.on_message(view, msg)

    def _tick(self) -> None:
        if self.shift_start:
            el = clock.now() - self.shift_start
            hrs = self.settings.monitor.shift_hours
            h, rem = divmod(int(el.total_seconds()), 3600)
            txt = f"Shift  {h:02d}:{rem // 60:02d} / {hrs}:00" + ("  (shift complete)" if el > timedelta(hours=hrs) else "")
            self.shell.shift_lbl.config(text=txt)
            for v in self.sm.views.values():
                try:
                    v.refresh_timers()
                except tk.TclError:
                    pass
            self.overview.refresh()
        else:
            self.shell.shift_lbl.config(text="")
        self.after(15000 if self.shift_start else 1000, self._tick)

    def _on_close(self) -> None:
        if self.sm.views and not messagebox.askyesno("Exit", "Monitoring sessions are running. Exit the tool?",
                                                      parent=self):
            return
        self.sm.stop_all(save=True)
        keep_awake(False)
        self.host.bus.publish(Topics.SHUTDOWN, None)
        self.plugins.stop_all()
        self.sound.stop()
        if self.keepalive:
            self.keepalive.stop()
        if self.client:
            c = self.client
            threading.Thread(target=lambda: (c.logout(), c.close()), daemon=True).start()
        self.destroy()
