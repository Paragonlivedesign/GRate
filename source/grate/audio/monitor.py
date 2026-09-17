"""Per-track monitor output stream with jitter buffering + rate conversion."""

from __future__ import annotations

import math
import threading
from collections import deque

import numpy as np
import sounddevice as sd

from grate.audio.capture import db_to_lin, list_output_devices, resolve_device


class MonitorOutput:
    """One OutputStream per monitored track.

    Robustness against pops/crackle:
    - Jitter buffer: playback holds silence until ~PREFILL_SEC of audio is
      buffered, and re-primes after an underrun instead of machine-gunning gaps.
    - Rate conversion: incoming audio (capture rate) is continuously linear-
      resampled to the output device rate, so mismatched clocks (e.g. 44.1k
      Dante capture -> 48k speakers) don't drift, overflow, and drop chunks.
    - Drift servo: even devices at the same nominal rate drift by ~50-100 ppm,
      which slowly drains/fills the buffer until it pops. The resample ratio is
      nudged (max +/-0.2%, inaudible) to hold the buffer at its target level.
    - Soft edges: fade-out into an underrun and fade-in on resume instead of
      hard discontinuities.
    """

    PREFILL_SEC = 0.1
    MAX_BUFFER_SEC = 0.5
    FADE_SAMPLES = 256
    DRIFT_MAX = 0.002  # max resample ratio correction (0.2%)

    def __init__(self):
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._queue: deque[np.ndarray] = deque()
        self._buffered = 0  # total samples queued (at output rate)
        self._device_index: int | None = None
        self._src_rate = 48000.0
        self._out_rate = 48000.0
        self._gain_lin = 1.0
        self._enabled = False
        self._last_error = ""
        # resampler state
        self._tail = np.zeros(1, dtype=np.float32)
        self._phase = 0.0
        self._drift = 1.0
        # playback state
        self._primed = False
        self._last_out = 0.0
        self._fade_in = 0

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
            if sample_rate > 0:
                self._src_rate = float(sample_rate)
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
                or abs(self._out_rate - device.sample_rate) > 1
            )
            self._device_index = device.index
            if need_restart:
                self._close_unlocked()
                self._out_rate = float(device.sample_rate) or 48000.0
                try:
                    self._stream = sd.OutputStream(
                        device=device.index,
                        channels=2,
                        samplerate=self._out_rate,
                        # 1024 = ~21 ms deadline per callback: headroom against
                        # GIL stalls from UI rendering (512 was only ~10 ms)
                        blocksize=1024,
                        dtype="float32",
                        latency="high",
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

    def set_source_rate(self, sample_rate: float) -> None:
        """Update the incoming audio rate (capture streams report it late)."""
        if sample_rate <= 0:
            return
        with self._lock:
            if abs(sample_rate - self._src_rate) > 1:
                self._src_rate = float(sample_rate)
                self._tail = np.zeros(1, dtype=np.float32)
                self._phase = 0.0

    # ------------------------------------------------------------- input side

    def _resample(self, x: np.ndarray) -> np.ndarray:
        """Continuous linear resample src_rate -> out_rate (phase preserved).

        Always active (even at matching nominal rates) so the drift servo can
        correct real-world clock offsets between capture and output devices.
        """
        buf = np.concatenate([self._tail, x])
        step = (self._src_rate / self._out_rate) * self._drift
        span = buf.size - 1
        # Output positions t = phase + k*step must satisfy t < span so that
        # interpolation between buf[i] and buf[i+1] stays in bounds.
        n = int((span - self._phase) / step)
        if n <= 0:
            self._tail = buf
            return np.zeros(0, dtype=np.float32)
        t = self._phase + step * np.arange(n, dtype=np.float64)
        i = t.astype(np.int64)
        frac = (t - i).astype(np.float32)
        out = buf[i] * (1.0 - frac) + buf[i + 1] * frac
        # Advance: keep the fractional phase in [0, 1) and retain the samples
        # still needed for the next interpolation. (Re-referencing to the last
        # sample would drive phase negative -> extrapolated samples at every
        # push boundary = audible crackle.)
        next_t = self._phase + n * step
        consumed = min(int(next_t), span)
        self._phase = next_t - consumed
        self._tail = buf[consumed:]
        return out.astype(np.float32, copy=False)

    def push(self, mono: np.ndarray) -> None:
        if mono.size == 0:
            return
        with self._lock:
            if not self._enabled or self._stream is None:
                return
            # Drift servo: steer the resample ratio so the buffer holds at
            # ~1.5x prefill. step > 1 consumes input faster (fewer output
            # samples), shrinking the buffer; < 1 grows it.
            target = self.PREFILL_SEC * self._out_rate * 1.5
            err_sec = (self._buffered - target) / self._out_rate
            desired = 1.0 + max(-self.DRIFT_MAX, min(self.DRIFT_MAX, err_sec * 0.02))
            self._drift += 0.05 * (desired - self._drift)
            chunk = self._resample(mono.astype(np.float32, copy=False))
            if chunk.size == 0:
                return
            self._queue.append(chunk)
            self._buffered += chunk.size
            # Overflow (clock drift / stalls): resync to target latency
            max_buf = int(self.MAX_BUFFER_SEC * self._out_rate)
            if self._buffered > max_buf:
                target = int(self.PREFILL_SEC * self._out_rate) * 2
                while self._queue and self._buffered > target:
                    dropped = self._queue.popleft()
                    self._buffered -= dropped.size
                self._primed = False  # re-prime for a clean edge

    # ------------------------------------------------------------- output side

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        needed = int(frames)
        with self._lock:
            gain = self._gain_lin
            prefill = int(self.PREFILL_SEC * self._out_rate)
            if not self._primed:
                if self._buffered >= prefill:
                    self._primed = True
                    self._fade_in = self.FADE_SAMPLES
                else:
                    outdata.fill(0.0)
                    self._last_out = 0.0
                    return
            chunks: list[np.ndarray] = []
            got = 0
            while self._queue and got < needed:
                chunk = self._queue.popleft()
                chunks.append(chunk)
                got += chunk.size
            if got > needed:
                flat = np.concatenate(chunks)
                mono = flat[:needed]
                leftover = flat[needed:]
                self._queue.appendleft(leftover)
                self._buffered -= needed
            elif got == needed:
                mono = np.concatenate(chunks)
                self._buffered -= needed
            else:
                # Underrun: fade what we have to zero, then re-prime
                mono = np.zeros(needed, dtype=np.float32)
                if chunks:
                    filled = np.concatenate(chunks)
                    mono[: filled.size] = filled
                    fade_n = min(self.FADE_SAMPLES, filled.size)
                    if fade_n > 1:
                        mono[filled.size - fade_n : filled.size] *= np.linspace(
                            1.0, 0.0, fade_n, dtype=np.float32
                        )
                self._buffered = 0
                self._primed = False
            fade_in = self._fade_in
            if fade_in > 0:
                ramp_n = min(fade_in, mono.size)
                start = 1.0 - fade_in / self.FADE_SAMPLES
                stop = 1.0 - (fade_in - ramp_n) / self.FADE_SAMPLES
                mono[:ramp_n] *= np.linspace(start, stop, ramp_n, dtype=np.float32)
                self._fade_in -= ramp_n
        mono = np.clip(mono * gain, -1.0, 1.0)
        self._last_out = float(mono[-1]) if mono.size else 0.0
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
        self._buffered = 0
        self._primed = False
        self._tail = np.zeros(1, dtype=np.float32)
        self._phase = 0.0
        self._drift = 1.0

    def close(self) -> None:
        with self._lock:
            self._enabled = False
            self._close_unlocked()
