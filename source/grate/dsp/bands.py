"""Frequency-band analyzer, bandpass filters, and adaptive band triggers."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from grate.config import BandTriggerConfig

BANDS = ("low", "mid", "high")
BAND_RANGES_HZ = {
    "low": (20.0, 150.0),
    "mid": (150.0, 2000.0),
    "high": (2000.0, 10000.0),
}
BAND_COLORS = {
    "low": "#f4a261",
    "mid": "#4cc9a0",
    "high": "#4ea8de",
}


@dataclass
class BandEnergies:
    low: float = 0.0
    mid: float = 0.0
    high: float = 0.0

    def get(self, band: str) -> float:
        return float(getattr(self, band, 0.0))


@dataclass
class BandTriggerFire:
    trigger_id: str
    band: str
    command: str
    timestamp: float


class BiquadBandpass:
    """Stateful RBJ bandpass (constant skirt gain) — direct form II."""

    def __init__(self, sample_rate: float, low_hz: float, high_hz: float):
        self._sr = float(sample_rate)
        self._low = float(low_hz)
        self._high = float(high_hz)
        self._z1 = 0.0
        self._z2 = 0.0
        self._b0 = self._b1 = self._b2 = 0.0
        self._a1 = self._a2 = 0.0
        self._rebuild()

    def set_sample_rate(self, sample_rate: float) -> None:
        rate = float(sample_rate)
        if rate <= 0 or abs(rate - self._sr) < 1:
            return
        self._sr = rate
        self._rebuild()
        self.reset()

    def reset(self) -> None:
        self._z1 = 0.0
        self._z2 = 0.0

    def _rebuild(self) -> None:
        nyq = self._sr * 0.5
        f0 = max(1.0, min(math.sqrt(self._low * self._high), nyq * 0.98))
        bw = max(1.0, self._high - self._low)
        q = max(0.2, f0 / bw)
        w0 = 2.0 * math.pi * f0 / self._sr
        alpha = math.sin(w0) / (2.0 * q)
        cos_w0 = math.cos(w0)
        b0 = alpha
        b1 = 0.0
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
        self._b0 = b0 / a0
        self._b1 = b1 / a0
        self._b2 = b2 / a0
        self._a1 = a1 / a0
        self._a2 = a2 / a0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if samples.size == 0:
            return samples.astype(np.float32, copy=False)
        out = np.empty(samples.size, dtype=np.float32)
        z1 = self._z1
        z2 = self._z2
        b0, b1, b2 = self._b0, self._b1, self._b2
        a1, a2 = self._a1, self._a2
        for i, x in enumerate(samples.astype(np.float32, copy=False)):
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            out[i] = y
        self._z1 = z1
        self._z2 = z2
        return out


class BandAnalyzer:
    """FFT band energies + bandpass banks + adaptive band triggers."""

    def __init__(self, sample_rate: float = 48000.0, history_seconds: float = 5.0):
        self._sr = float(sample_rate)
        self._history_seconds = history_seconds
        self._history_len = max(32, int(history_seconds * 30))  # ~30 updates/sec
        self._energies = BandEnergies()
        self._hist: dict[str, deque[float]] = {b: deque(maxlen=self._history_len) for b in BANDS}
        self._avg: dict[str, float] = {b: 1e-6 for b in BANDS}
        self._filters = {
            band: BiquadBandpass(self._sr, *BAND_RANGES_HZ[band]) for band in BANDS
        }
        self._last_fire: dict[str, float] = {}
        self._trigger_flash: dict[str, float] = {}
        self._blocks = 0
        self._warmup_blocks = 40  # ~warmup before adaptive triggers arm

    def set_sample_rate(self, sample_rate: float) -> None:
        rate = float(sample_rate)
        if rate <= 0 or abs(rate - self._sr) < 1:
            return
        self._sr = rate
        for filt in self._filters.values():
            filt.set_sample_rate(rate)

    @property
    def energies(self) -> BandEnergies:
        return self._energies

    def envelope_history(self, band: str) -> np.ndarray:
        hist = self._hist.get(band)
        if not hist:
            return np.zeros(0, dtype=np.float32)
        return np.asarray(hist, dtype=np.float32)

    def bandpass(self, samples: np.ndarray, band: str) -> np.ndarray:
        filt = self._filters.get(band)
        if filt is None:
            return samples.astype(np.float32, copy=False)
        return filt.process(samples)

    def select_source(self, samples: np.ndarray, source: str, beat_source: str = "mix") -> np.ndarray:
        key = source
        if key == "beat":
            key = beat_source
        if key in BANDS:
            return self.bandpass(samples, key)
        return samples.astype(np.float32, copy=False)

    def process(self, samples: np.ndarray) -> BandEnergies:
        if samples.size == 0:
            return self._energies
        # Windowed rfft energy in each band
        n = int(2 ** math.ceil(math.log2(max(64, samples.size))))
        frame = np.zeros(n, dtype=np.float32)
        take = min(samples.size, n)
        frame[:take] = samples[-take:]
        frame *= np.hanning(n).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(frame))
        freqs = np.fft.rfftfreq(n, d=1.0 / self._sr)
        energies = {}
        for band, (lo, hi) in BAND_RANGES_HZ.items():
            mask = (freqs >= lo) & (freqs < hi)
            if not np.any(mask):
                energies[band] = 0.0
                continue
            # Mean magnitude normalized roughly to 0..1 range
            energies[band] = float(np.mean(spectrum[mask]) / (n * 0.25 + 1e-9))
        self._energies = BandEnergies(**energies)
        for band in BANDS:
            e = energies[band]
            self._hist[band].append(e)
            self._avg[band] = 0.98 * self._avg[band] + 0.02 * max(e, 1e-9)
        self._blocks += 1
        return self._energies

    def evaluate_triggers(self, triggers: list[BandTriggerConfig]) -> list[BandTriggerFire]:
        now = time.monotonic()
        fires: list[BandTriggerFire] = []
        # Decay flashes
        for tid in list(self._trigger_flash.keys()):
            self._trigger_flash[tid] = max(0.0, self._trigger_flash[tid] - 0.12)

        if self._blocks < self._warmup_blocks:
            return fires

        for trig in triggers:
            if not trig.enabled or not trig.osc_command.strip():
                continue
            band = trig.band if trig.band in BANDS else "low"
            energy = self._energies.get(band)
            avg = max(self._avg[band], 1e-9)
            # sensitivity 1..10 → threshold multiplier from ~3.5 down to ~1.15
            sens = max(1, min(10, int(trig.sensitivity)))
            k = 3.6 - (sens - 1) * (2.45 / 9.0)
            threshold = avg * k
            # Require some absolute floor so silence doesn't trip
            if energy < max(threshold, 0.002):
                continue
            last = self._last_fire.get(trig.id, 0.0)
            cooldown = max(50, int(trig.cooldown_ms)) / 1000.0
            if now - last < cooldown:
                continue
            # Rising-edge-ish: energy must be above threshold and above previous envelope tip
            self._last_fire[trig.id] = now
            self._trigger_flash[trig.id] = 1.0
            fires.append(
                BandTriggerFire(
                    trigger_id=trig.id,
                    band=band,
                    command=trig.osc_command.strip(),
                    timestamp=now,
                )
            )
        return fires

    def trigger_flash(self, trigger_id: str) -> float:
        return self._trigger_flash.get(trigger_id, 0.0)
