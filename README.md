# Netcool Shift Monitor v12 - Access Ericsson team, Vodafone Egypt

## Give it to users (no installation)
1. On one Windows PC with Python 3.9+ and internet: double-click **build_exe.bat**.
2. Share **dist\NetcoolShiftMonitor_portable.zip**.
3. Users: right-click the zip > Properties > tick **Unblock** > OK, then extract it to e.g. `Documents`,
   and run **NetcoolShiftMonitor.exe** inside the folder. Nothing is installed; no admin rights needed.
   If the company still blocks it, IT must allow `NetcoolShiftMonitor.exe` (path or file-hash rule).

## Run from source (developers)
`install.bat` once, then `run.bat`. Tests: `python -m unittest discover tests`.

## What is new in v12
- **Core = v9.1.** Monitoring, outage detection and alarms work exactly like v9.1 (guarded by
  `tests/test_v12_core_parity.py`). Everything else is a plugin; a broken plugin is skipped, the core keeps running.
- **Menus.** Sessions / File / Tasks / Tools / View / Help, with shortcuts (Ctrl+N new session, F5 refresh all,
  Ctrl+M open MOM, Ctrl+I AI, Ctrl+R records). Outage actions: the **Actions ▾** button or right-click a row.
- **Any screen.** Starts maximised, scales to the monitor (View > Scale), light or dark theme (View).
  On laptop screens the summary cards fold away (**▾ Summary** shows them).
- **Smarter AI.** Learns each decision with its context: night window, planned words, Outlook planned
  activity / CRQ found, TT already open, team, your note. Trains in the background at low priority, time-capped.
- **Outlook.** Searches your Outlook (on your PC, via pywin32) for planned / night activity and CRQs matching the
  outage and shows what it found. You decide.
- **Flow recorder** (Tools > Record a browser flow): record a Remedy / MOM task once; replay it from an outage
  with the outage values filled in. Replay stops before the final submit; passwords are never saved.
- **Share experience locally.** Tools > AI assistant > *Export experience* writes one file with your AI learning
  and records (no TT texts, no user names). A colleague uses *Import a colleague's experience*; duplicates are
  skipped. Nothing goes to any server or API: it is a file you pass on yourself.

## Global TT in Remedy
1. Tools > Remedy / Global TT setup once: check the WSDL address, press **Test connection** (on the VPN), and fill
   the TX / NFM / PA templates from a real Global TT.
2. Outage > **Actions ▾ > Outage → Global TT**: pre-filled from the FO manual. Tick the FO checks, review,
   **Open TT in Remedy**.
3. Your Remedy login is asked once and kept only in Windows Credential Manager. Nothing is sent without your
   click; a failed send is never retried by itself.

## Write a plugin (developers)
1. Copy `plugins/example` to `plugins/<name>`, set `name` and `"enabled": true` in `plugin.json`
   (`entry` = `module:Class`, `requires` = plugins that must start first).
2. Your class has `name`, `start(host)` and `stop()`. Through `host` you get:
   - `host.bus.subscribe(Topics.X, fn)` / `publish` - events: `OUTAGE_ALERT`, `OUTAGE_CLEARED`, `REFRESH`,
     `DECISION`, `TT_OPENED`, `SESSION_STARTED/STOPPED`, `ALERT_ACK`, `SHUTDOWN` (data classes in `kernel/contracts.py`);
   - `host.add_menu(MenuItem(...))`, `host.add_outage_action(OutageAction(...))` - UI without touching UI code;
   - `host.settings_section(name, Dataclass)` - your settings; `host.alert(AlertRequest(...))` - raise an alarm;
   - `host.offer(name, obj)` / `host.service(name)` - share a service; `register_advisor`,
     `register_activity_source` - feed the AI; `host.run_bg(fn)` - background work.
3. Plugins never import each other or `ui/`; talk through the bus or services. Users can switch a plugin off
   with `plugins_disabled` in settings.json. A built .exe also loads a plugin folder dropped into `_internal\plugins`.

## Notes
- MOM allows ONE sign-in per user. Use **Open MOM** (Ctrl+M) to work in MOM with the tool's login,
  or use a different MOM user in Edge.
- The tool uses its own browser (bundled Chromium), never your Edge.
- Logo (optional): put `assets\logo.png` next to the .exe.
- Settings, database, logs and alert history: `%USERPROFILE%\.netcool_monitor` (all local).
