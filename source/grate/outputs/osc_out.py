"""Shared UDP OSC client for grandMA3 /cmd messages (no bundles)."""

from __future__ import annotations

import datetime
import threading

from pythonosc.udp_client import SimpleUDPClient

from grate.config import OscSettings, app_data_dir


def _log(line: str) -> None:
    try:
        path = app_data_dir() / "osc.log"
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except Exception:  # noqa: BLE001
        pass


class OscOutput:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: SimpleUDPClient | None = None
        self._settings = OscSettings()
        self._last_bpm: dict[str, float] = {}
        self._ok = False
        self._last_error = ""

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def last_error(self) -> str:
        return self._last_error

    def configure(self, settings: OscSettings) -> None:
        with self._lock:
            self._settings = settings
            self._rebuild()

    def _rebuild(self) -> None:
        self._client = None
        self._ok = False
        self._last_error = ""
        if not self._settings.enabled:
            return
        try:
            # Same client path as the manual tests that reach MA3
            self._client = SimpleUDPClient(self._settings.host, int(self._settings.port))
            self._ok = True
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            self._ok = False

    def cmd_address(self) -> str:
        """Primary MA3 command OSC address, derived from the configured prefix."""
        raw = (self._settings.prefix or "").strip().strip("/")
        if not raw:
            return "/cmd"
        if raw.endswith("/cmd") or raw == "cmd":
            return f"/{raw}"
        return f"/{raw}/cmd"

    def cmd_addresses(self) -> list[str]:
        """All addresses we send to, so it works whether or not MA3 has a prefix.

        MA3 with an empty Prefix listens on `/cmd`; with Prefix `gma3` it
        listens on `/gma3/cmd`. Sending both covers either configuration.
        """
        primary = self.cmd_address()
        addrs = [primary]
        for extra in ("/cmd", "/gma3/cmd"):
            if extra not in addrs:
                addrs.append(extra)
        return addrs

    def send_command(self, command: str) -> bool:
        command = (command or "").strip()
        if not command:
            return False
        with self._lock:
            if not self._settings.enabled or self._client is None:
                _log(f"SKIP (enabled={self._settings.enabled} client={self._client is not None}) {command}")
                return False
            sent_any = False
            for addr in self.cmd_addresses():
                try:
                    # Single OSC message only — MA3 does not support bundles
                    self._client.send_message(addr, command)
                    _log(f"SENT {self._settings.host}:{self._settings.port} {addr} {command}")
                    sent_any = True
                except Exception as exc:  # noqa: BLE001
                    _log(f"FAIL {self._settings.host}:{self._settings.port} {addr} {exc}")
                    self._last_error = str(exc)
            self._ok = sent_any
            if sent_any:
                self._last_error = ""
            return sent_any

    def send_bpm(self, lane_id: str, speed_master: str, bpm: float, force: bool = False) -> bool:
        if bpm <= 0 or not speed_master:
            return False
        with self._lock:
            last = self._last_bpm.get(lane_id)
            delta = abs(bpm - last) if last is not None else 999.0
            if not force and last is not None and delta < self._settings.bpm_delta:
                return False
            self._last_bpm[lane_id] = bpm
        value = round(float(bpm), 2)
        return self.send_command(f"Master {speed_master} At BPM {value}")

    def send_beat_command(self, command: str) -> bool:
        return self.send_command(command)

    def test_send(self) -> bool:
        """Burst a few BPM values so Echo Input is obvious."""
        ok = False
        for bpm in (111.1, 122.2, 133.3):
            if self.send_command(f"Master 3.1 At BPM {bpm}"):
                ok = True
        return ok
