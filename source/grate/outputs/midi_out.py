"""MIDI note-per-beat and MIDI clock from a designated lane."""

from __future__ import annotations

import threading
import time
from typing import Any

import mido

from grate.config import MidiSettings


def list_midi_output_names() -> list[str]:
    try:
        return list(mido.get_output_names())
    except Exception:  # noqa: BLE001
        return []


class MidiOutput:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._port: Any = None
        self._settings = MidiSettings()
        self._ok = False
        self._last_error = ""
        self._clock_bpm = 0.0
        self._clock_thread: threading.Thread | None = None
        self._clock_stop = threading.Event()
        self._clock_lane_id = ""

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def last_error(self) -> str:
        return self._last_error

    def configure(self, settings: MidiSettings) -> None:
        with self._lock:
            self._settings = settings
            self._clock_lane_id = settings.clock_lane_id or ""
            self._open_port()
            self._restart_clock_unlocked()

    def _open_port(self) -> None:
        self._close_port_unlocked()
        self._ok = False
        self._last_error = ""
        if not self._settings.enabled:
            return
        name = (self._settings.port_name or "").strip()
        try:
            names = list(mido.get_output_names())
            if not names:
                self._last_error = "No MIDI output ports found"
                return
            if name and name in names:
                self._port = mido.open_output(name)
            else:
                self._port = mido.open_output(names[0])
                self._settings.port_name = names[0]
            self._ok = True
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            self._ok = False
            self._port = None

    def _close_port_unlocked(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:  # noqa: BLE001
                pass
            self._port = None

    def close(self) -> None:
        with self._lock:
            self._stop_clock_unlocked()
            self._close_port_unlocked()
            self._ok = False

    def set_clock_bpm(self, lane_id: str, bpm: float) -> None:
        with self._lock:
            if self._clock_lane_id and lane_id != self._clock_lane_id:
                return
            if not self._clock_lane_id:
                # If none designated, first active lane wins until configured
                self._clock_lane_id = lane_id
            self._clock_bpm = max(0.0, float(bpm))

    def send_note(self, channel: int, note: int, velocity: int | None = None) -> bool:
        with self._lock:
            if not self._settings.enabled or self._port is None:
                return False
            ch = max(0, min(15, int(channel) - 1))
            n = max(0, min(127, int(note)))
            vel = max(1, min(127, int(velocity if velocity is not None else self._settings.note_velocity)))
            try:
                self._port.send(mido.Message("note_on", note=n, velocity=vel, channel=ch))
                # Immediate note-off after short duration via delayed send is fine for triggers
                duration = max(1, int(self._settings.note_duration_ms)) / 1000.0
                port = self._port

                def _off() -> None:
                    try:
                        port.send(mido.Message("note_off", note=n, velocity=0, channel=ch))
                    except Exception:  # noqa: BLE001
                        pass

                threading.Timer(duration, _off).start()
                self._ok = True
                self._last_error = ""
                return True
            except Exception as exc:  # noqa: BLE001
                self._ok = False
                self._last_error = str(exc)
                return False

    def _restart_clock_unlocked(self) -> None:
        self._stop_clock_unlocked()
        if not self._settings.enabled:
            return
        self._clock_stop.clear()
        self._clock_thread = threading.Thread(target=self._clock_loop, name="midi-clock", daemon=True)
        self._clock_thread.start()

    def _stop_clock_unlocked(self) -> None:
        self._clock_stop.set()
        thread = self._clock_thread
        self._clock_thread = None
        if thread and thread.is_alive():
            thread.join(timeout=0.5)

    def _clock_loop(self) -> None:
        # MIDI clock: 24 ppqn
        while not self._clock_stop.is_set():
            with self._lock:
                bpm = self._clock_bpm
                port = self._port
                enabled = self._settings.enabled
            if not enabled or port is None or bpm < 20:
                time.sleep(0.05)
                continue
            interval = 60.0 / (bpm * 24.0)
            try:
                port.send(mido.Message("clock"))
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._ok = False
                    self._last_error = str(exc)
                time.sleep(0.1)
                continue
            self._clock_stop.wait(interval)
