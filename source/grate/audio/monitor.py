"""Per-lane monitor output stream."""

from __future__ import annotations

import threading
from collections import deque

import numpy as np
import sounddevice as sd

from grate.audio.capture import db_to_lin, list_output_devices, resolve_device


class MonitorOutput:
    """One OutputStream per monitored lane, fed from a ring buffer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._queue: deque[np.ndarray] = deque()
        self._device_index: int | None = None
        self._sample_rate = 48000.0
        self._gain_lin = 1.0
        self._enabled = False
        self._last_error = ""

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(
        self,
        enabled: bool,
        device_name: str,
        device_index: int | None,
        gain_db: float,
        sample_rate: float,
    ) -> bool:
        with self._lock:
            self._gain_lin = db_to_lin(gain_db)
            self._sample_rate = float(sample_rate) if sample_rate > 0 else 48000.0
            if not enabled:
                self._enabled = False
                self._close_unlocked()
                return True

            device = resolve_device(device_name, device_index, list_output_devices(), outputs=True)
            if device is None:
                self._enabled = False
                self._last_error = "No monitor output device"
                self._close_unlocked()
                return False

            need_restart = (
                self._stream is None
                or self._device_index != device.index
                or abs(self._sample_rate - device.sample_rate) > 1
            )
            self._device_index = device.index
            self._sample_rate = device.sample_rate
            if need_restart:
                self._close_unlocked()
                try:
                    self._stream = sd.OutputStream(
                        device=device.index,
                        channels=2,
                        samplerate=self._sample_rate,
                        blocksize=512,
                        dtype="float32",
                        callback=self._callback,
                    )
                    self._stream.start()
                    self._enabled = True
                    self._last_error = ""
                except Exception as exc:  # noqa: BLE001
                    self._enabled = False
                    self._last_error = str(exc)
                    self._stream = None
                    return False
            else:
                self._enabled = True
                self._last_error = ""
            return True

    def push(self, mono: np.ndarray) -> None:
        if mono.size == 0:
            return
        with self._lock:
            if not self._enabled or self._stream is None:
                return
            self._queue.append(mono.astype(np.float32, copy=False))
            while len(self._queue) > 100:
                self._queue.popleft()

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        needed = int(frames)
        with self._lock:
            gain = self._gain_lin
            chunks: list[np.ndarray] = []
            got = 0
            while self._queue and got < needed:
                chunk = self._queue.popleft()
                chunks.append(chunk)
                got += chunk.size
            if got > needed:
                # Put leftover back
                flat = np.concatenate(chunks)
                keep = flat[:needed]
                leftover = flat[needed:]
                if leftover.size:
                    self._queue.appendleft(leftover)
                mono = keep
            elif got == needed:
                mono = np.concatenate(chunks) if chunks else np.zeros(needed, dtype=np.float32)
            else:
                mono = np.zeros(needed, dtype=np.float32)
                if chunks:
                    filled = np.concatenate(chunks)
                    mono[: filled.size] = filled
        mono = np.clip(mono * gain, -1.0, 1.0)
        outdata[:, 0] = mono
        outdata[:, 1] = mono

    def _close_unlocked(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        self._queue.clear()

    def close(self) -> None:
        with self._lock:
            self._enabled = False
            self._close_unlocked()
