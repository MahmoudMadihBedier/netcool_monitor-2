"""Alarm sound that keeps ringing until every alert is acknowledged.

Your own .wav or .mp3 (Sound window) is played through Windows' built-in MCI player (no extra package).
Windows: a dedicated thread plays the alarm WAV again and again (synchronous PlaySound - no reliance on the
SND_LOOP/SND_PURGE flags that stop working on some PCs). If the WAV cannot be played, it falls back to
winsound.Beep, then MessageBeep. Other systems: Tk bell from the UI thread.
The WAV is (re)written at every start, so a missing / damaged / quarantined file never silences the tool.
"""
from __future__ import annotations

import io
import logging
import shutil
import math
import struct
import sys
import threading
import wave
from pathlib import Path

from .config import APP_DIR

log = logging.getLogger(__name__)
ALARM_WAV = APP_DIR / "alarm.wav"
SOUND_DIR = APP_DIR / "sounds"
SOUND_TYPES = (".wav", ".mp3")
MAX_SOUND_BYTES = 20 * 1024 * 1024
_ALIAS = "nsm_alarm"
_TONES = ((1250, 0.20), (0, 0.05), (950, 0.20), (0, 0.05), (1250, 0.20), (0, 0.05), (950, 0.20), (0, 0.40))


def _alarm_bytes(volume: int = 100) -> bytes:
    rate, out = 22050, io.BytesIO()
    frames = bytearray()
    for tone, dur in _TONES:
        n = int(rate * dur)
        for i in range(n):
            if tone:
                env = min(1.0, i / 200, (n - i) / 200)          # no clicks
                v = int(29000 * max(5, min(100, volume)) / 100 * env * math.sin(2 * math.pi * tone * i / rate))
            else:
                v = 0
            frames += struct.pack("<h", v)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out.getvalue()


def install_sound(src) -> Path:
    """Copy the user's .wav / .mp3 into the tool's folder (so it still works if the original moves)."""
    src = Path(src)
    if src.suffix.lower() not in SOUND_TYPES:
        raise ValueError("Choose a .wav or .mp3 file")
    if not src.is_file() or src.stat().st_size == 0:
        raise ValueError("The sound file is empty or missing")
    if src.stat().st_size > MAX_SOUND_BYTES:
        raise ValueError("The sound file is larger than 20 MB")
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    dst = SOUND_DIR / src.name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return dst


def _make_alarm_wav(path, volume: int = 100) -> None:
    data = _alarm_bytes(volume)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != data:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


class SoundPlayer:
    def __init__(self, tk_root=None, custom_wav: str = "", volume: int = 100):
        self.muted = False
        self.volume = max(5, min(100, int(volume or 100)))
        self._mci = None                      # winmm.mciSendStringW (Windows) - replaced by a fake in tests
        self._root = tk_root
        self._want = threading.Event()        # set while the alarm must ring
        self._lock = threading.Lock()
        self._thread = None
        self._win = sys.platform.startswith("win")
        self.backend = "bell"
        self.last_error = ""
        self.wav = ALARM_WAV
        self.set_custom(custom_wav)
        if self._win:
            try:
                _make_alarm_wav(ALARM_WAV, self.volume)
            except Exception as exc:
                self.last_error = f"alarm.wav could not be written: {exc}"
                log.warning(self.last_error)
            self.backend = "wav"
        log.info("Alarm sound backend: %s (%s)", self.backend, self.wav)

    # ------------------------------------------------------------------ settings
    def set_custom(self, path: str) -> None:
        p = Path(path) if path else None
        self.wav = p if (p and p.is_file() and p.suffix.lower() in SOUND_TYPES) else ALARM_WAV

    def set_volume(self, volume: int) -> None:
        self.volume = max(5, min(100, int(volume)))
        if self._win:
            try:
                _make_alarm_wav(ALARM_WAV, self.volume)
            except Exception:
                pass

    @property
    def custom(self) -> bool:
        return Path(self.wav) != ALARM_WAV

    # ------------------------------------------------------------------ control
    @property
    def playing(self) -> bool:
        return self._want.is_set()

    def start(self) -> None:
        if self.muted:
            return
        self._want.set()
        with self._lock:
            if self._win:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._loop, name="alarm", daemon=True)
                    self._thread.start()
            elif self._thread is None:           # Tk bell loop on the UI thread
                self._thread = True
                self._bell_loop()

    def stop(self) -> None:
        self._want.clear()
        if self._win:
            try:
                import winsound
                winsound.PlaySound(None, 0)      # interrupts the sound being played now
            except Exception:
                pass
            # an own .mp3 / .wav stops by itself: the MCI loop sees the cleared flag within 0.1 s

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.stop()

    def test(self, seconds: float = 2.5) -> None:
        """Short test from the UI (Settings / Test alert)."""
        if self.muted:
            return
        self.start()
        if self._root is not None:
            try:
                self._root.after(int(seconds * 1000), self.stop)
            except Exception:
                pass

    # ------------------------------------------------------------------ Windows player thread
    def _play_once(self) -> bool:
        import winsound
        try:
            if not Path(self.wav).exists():
                _make_alarm_wav(ALARM_WAV)
                self.wav = ALARM_WAV
            winsound.PlaySound(str(self.wav), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            self.backend = "wav"
            return True
        except Exception as exc:
            self.last_error = f"PlaySound failed: {exc}"
        try:
            for tone, dur in _TONES:
                if not self._want.is_set():
                    break
                if tone:
                    winsound.Beep(tone, int(dur * 1000))
                else:
                    self._want.wait(dur)
            self.backend = "beep"
            return True
        except Exception as exc:
            self.last_error = f"Beep failed: {exc}"
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND)
            self.backend = "messagebeep"
            return True
        except Exception as exc:
            self.last_error = f"MessageBeep failed: {exc}"
        return False

    def _mci_cmd(self, command: str) -> tuple:
        import ctypes
        if self._mci is None:
            self._mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(128)
        err = self._mci(command, buf, 127, 0)
        return err, buf.value

    def _play_custom_loop(self) -> bool:
        """Own .mp3 / .wav through MCI, repeated until stop(). False = could not play (fall back)."""
        err, _ = self._mci_cmd(f'open "{self.wav}" type mpegvideo alias {_ALIAS}')
        if err:
            self._mci_cmd(f"close {_ALIAS}")              # a previous run left it open: retry once
            err, _ = self._mci_cmd(f'open "{self.wav}" type mpegvideo alias {_ALIAS}')
        if err:
            self.last_error = f"cannot open {Path(self.wav).name} (MCI error {err})"
            return False
        try:
            self._mci_cmd(f"setaudio {_ALIAS} volume to {self.volume * 10}")
            self.backend = "mci"
            while self._want.is_set() and not self.muted:
                if self._mci_cmd(f"play {_ALIAS} from 0")[0]:
                    self.last_error = f"cannot play {Path(self.wav).name}"
                    return False
                while self._want.is_set() and self._mci_cmd(f"status {_ALIAS} mode")[1] == "playing":
                    self._want.wait(0.1)
                threading.Event().wait(0.15)
            return True
        finally:
            self._mci_cmd(f"stop {_ALIAS}")
            self._mci_cmd(f"close {_ALIAS}")

    def _loop(self) -> None:
        if self.custom:
            try:
                if self._play_custom_loop():
                    return
            except Exception as exc:
                self.last_error = f"own sound failed: {exc}"
            log.warning("Own alarm sound not playable (%s) - using the built-in alarm", self.last_error)
            self.wav = ALARM_WAV
        failures = 0
        while self._want.is_set() and not self.muted:
            ok = self._play_once()
            if not ok:
                failures += 1
                if failures in (1, 20):
                    log.warning("Alarm sound problem: %s", self.last_error)
                self._want.wait(1.0)
            else:
                failures = 0
            # tiny pause between repeats; a stop() during it ends the loop at once
            if self._want.is_set():
                threading.Event().wait(0.15)

    # ------------------------------------------------------------------ other systems
    def _bell_loop(self) -> None:
        if not self._want.is_set() or self._root is None or self.muted:
            self._thread = None
            return
        try:
            self._root.bell()
            self._root.after(700, self._bell_loop)
        except Exception:
            self._thread = None
