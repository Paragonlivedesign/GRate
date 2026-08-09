"""Per-channel channel strip: HP/LP EQ + shelves + gate, sample-clocked envelopes, spike triggers."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

try:
    from scipy.signal import sosfilt
except ImportError:  # pragma: no cover
    sosfilt = None

from grate.config import (
    FULL_RANGE_HIGH,
    FULL_RANGE_LOW,
    ChannelTriggerConfig,
)

# One envelope point per this many audio samples — ties the envelope trace to
# the audio sample clock so it scrolls in lockstep with the waveform + beat grid.
ENVELOPE_HOP = 512

# Envelope meter ballistics (per hop point): fast attack, slower release →
# smooth humps per hit instead of jagged noise.
ENV_ATTACK = 0.55
ENV_RELEASE = 0.12

# Default shelf corner frequencies
LOW_SHELF_HZ = 200.0
HIGH_SHELF_HZ = 4000.0

# 4th-order Butterworth cascade Q values (two biquad sections)
BUTTER4_Q = (0.54119610, 1.30656296)

_BUTTERWORTH_Q = 0.70710678
Q_MIN, Q_MAX = 0.4, 6.0
SHELF_Q_MIN, SHELF_Q_MAX = 0.2, 2.0

# UI frequency presets: (label, low_hz, high_hz)
FREQ_PRESETS = (
    ("Full 20–20k", FULL_RANGE_LOW, FULL_RANGE_HIGH),
    ("Kick 40–120", 40.0, 120.0),
    ("Bass 60–250", 60.0, 250.0),
    ("Snare 150–400", 150.0, 400.0),
    ("Vocal 300–3.4k", 300.0, 3400.0),
    ("Hats 5k–10k", 5000.0, 10000.0),
)


@dataclass
class ChannelTriggerFire:
    trigger_id: str
    command: str
    timestamp: float
    midi_enabled: bool = False
    midi_channel: int = 1
    midi_note: int = 36


# ---------------------------------------------------------------- biquad design (RBJ)


def _biquad_highpass(sr: float, f0: float, q: float) -> list[float]:
    w0 = 2.0 * math.pi * min(f0, sr * 0.49) / sr
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    b0 = (1 + cw) / 2
    b1 = -(1 + cw)
    b2 = (1 + cw) / 2
    a0 = 1 + alpha
    a1 = -2 * cw
    a2 = 1 - alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def _biquad_lowpass(sr: float, f0: float, q: float) -> list[float]:
    w0 = 2.0 * math.pi * min(f0, sr * 0.49) / sr
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    b0 = (1 - cw) / 2
    b1 = 1 - cw
    b2 = (1 - cw) / 2
    a0 = 1 + alpha
    a1 = -2 * cw
    a2 = 1 - alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def _biquad_low_shelf(sr: float, f0: float, gain_db: float, s: float = 0.9) -> list[float]:
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * min(f0, sr * 0.49) / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / 2.0 * math.sqrt((a + 1 / a) * (1 / s - 1) + 2)
    two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
    b0 = a * ((a + 1) - (a - 1) * cw + two_sqrt_a_alpha)
    b1 = 2 * a * ((a - 1) - (a + 1) * cw)
    b2 = a * ((a + 1) - (a - 1) * cw - two_sqrt_a_alpha)
    a0 = (a + 1) + (a - 1) * cw + two_sqrt_a_alpha
    a1 = -2 * ((a - 1) + (a + 1) * cw)
    a2 = (a + 1) + (a - 1) * cw - two_sqrt_a_alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def _biquad_high_shelf(sr: float, f0: float, gain_db: float, s: float = 0.9) -> list[float]:
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * min(f0, sr * 0.49) / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / 2.0 * math.sqrt((a + 1 / a) * (1 / s - 1) + 2)
    two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
    b0 = a * ((a + 1) + (a - 1) * cw + two_sqrt_a_alpha)
    b1 = -2 * a * ((a - 1) + (a + 1) * cw)
    b2 = a * ((a + 1) + (a - 1) * cw - two_sqrt_a_alpha)
    a0 = (a + 1) - (a - 1) * cw + two_sqrt_a_alpha
    a1 = 2 * ((a - 1) - (a + 1) * cw)
    a2 = (a + 1) - (a - 1) * cw - two_sqrt_a_alpha
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def build_sections(
    sr: float,
    hp_hz: float,
    lp_hz: float,
    *,
    hp_q: float = _BUTTERWORTH_Q,
    lp_q: float = _BUTTERWORTH_Q,
    low_shelf_hz: float = LOW_SHELF_HZ,
    low_shelf_db: float = 0.0,
    low_shelf_q: float = 0.9,
    high_shelf_hz: float = HIGH_SHELF_HZ,
    high_shelf_db: float = 0.0,
    high_shelf_q: float = 0.9,
) -> list[list[float]]:
    """Biquad sections for the channel strip. Shared by the audio path and the EQ graph.

    HP/LP are 4th-order (24 dB/oct) cascades. The user Q scales the second
    section so that |H(cutoff)| == Q exactly: Q 0.707 = flat Butterworth,
    higher Q adds a resonance bump of 20*log10(Q) dB at the cutoff.

    The topology is constant (all sections always present): a 20 Hz HP,
    20 kHz LP, and 0 dB shelves are (near-)identity, so bands fade in/out
    smoothly with their parameters. This lets the audio path keep filter
    state across live tweaks with no clicks.
    """
    hp_factor = max(Q_MIN, min(Q_MAX, hp_q)) / _BUTTERWORTH_Q
    lp_factor = max(Q_MIN, min(Q_MAX, lp_q)) / _BUTTERWORTH_Q
    hp_hz = max(FULL_RANGE_LOW, min(hp_hz, sr * 0.45))
    lp_hz = max(25.0, min(lp_hz, sr * 0.49))
    ls_s = max(SHELF_Q_MIN, min(SHELF_Q_MAX, low_shelf_q))
    hs_s = max(SHELF_Q_MIN, min(SHELF_Q_MAX, high_shelf_q))
    return [
        _biquad_highpass(sr, hp_hz, BUTTER4_Q[0]),
        _biquad_highpass(sr, hp_hz, max(0.2, BUTTER4_Q[1] * hp_factor)),
        _biquad_lowpass(sr, lp_hz, BUTTER4_Q[0]),
        _biquad_lowpass(sr, lp_hz, max(0.2, BUTTER4_Q[1] * lp_factor)),
        _biquad_low_shelf(sr, low_shelf_hz, low_shelf_db, s=ls_s),
        _biquad_high_shelf(sr, high_shelf_hz, high_shelf_db, s=hs_s),
    ]


def sections_response_db(freqs: np.ndarray, sections: list[list[float]], sr: float) -> np.ndarray:
    """Magnitude response (dB) of a biquad cascade at the given frequencies."""
    if not sections:
        return np.zeros(freqs.size)
    w = 2.0 * np.pi * freqs / sr
    z1 = np.exp(-1j * w)
    z2 = z1 * z1
    h = np.ones(freqs.size, dtype=complex)
    for b0, b1, b2, _a0, a1, a2 in sections:
        h *= (b0 + b1 * z1 + b2 * z2) / (1.0 + a1 * z1 + a2 * z2)
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-6))


def _sosfilt_py(sos: np.ndarray, x: np.ndarray, zi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pure-Python fallback if scipy is unavailable (direct form II transposed)."""
    y = x.astype(np.float64, copy=True)
    for s in range(sos.shape[0]):
        b0, b1, b2, _a0, a1, a2 = sos[s]
        z1, z2 = zi[s]
        out = np.empty_like(y)
        for i, xi in enumerate(y):
            yi = b0 * xi + z1
            z1 = b1 * xi - a1 * yi + z2
            z2 = b2 * xi - a2 * yi
            out[i] = yi
        y = out
        zi[s, 0] = z1
        zi[s, 1] = z2
    return y, zi


class FilterChain:
    """Cascaded SOS filter (HP×2 → LP×2 → shelves) with persistent state."""

    def __init__(self):
        self._sos: np.ndarray | None = None
        self._zi: np.ndarray | None = None

    def design(self, sr: float, **eq_params) -> None:
        sections = build_sections(sr, **eq_params)
        if not sections:
            self._sos = None
            self._zi = None
            return
        sos = np.asarray(sections, dtype=np.float64)
        # Keep filter state across live tweaks (same topology) to avoid clicks
        if self._zi is not None and self._sos is not None and self._sos.shape == sos.shape:
            self._sos = sos
        else:
            self._sos = sos
            self._zi = np.zeros((sos.shape[0], 2), dtype=np.float64)

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self._sos is None or samples.size == 0:
            return samples.astype(np.float32, copy=False)
        if sosfilt is not None:
            out, self._zi = sosfilt(self._sos, samples.astype(np.float64, copy=False), zi=self._zi)
        else:
            out, self._zi = _sosfilt_py(self._sos, samples, self._zi)
        return out.astype(np.float32)


class NoiseGate:
    """RMS gate evaluated per envelope hop with fast open / slow close."""

    OPEN_COEF = 0.6
    CLOSE_COEF = 0.06

    def __init__(self):
        self._gain = 1.0

    def reset(self) -> None:
        self._gain = 1.0

    def process_hop(self, hop: np.ndarray, threshold_db: float) -> np.ndarray:
        rms = float(np.sqrt(np.mean(np.square(hop))) + 1e-12)
        db = 20.0 * math.log10(max(rms, 1e-9))
        target = 1.0 if db >= threshold_db else 0.0
        coef = self.OPEN_COEF if target > self._gain else self.CLOSE_COEF
        self._gain += (target - self._gain) * coef
        if self._gain >= 0.999:
            return hop
        return hop * np.float32(self._gain)


class ChannelProcessor:
    """One per channel: EQ chain + gate, sample-clocked smoothed envelope, spike triggers.

    The envelope records one point per ENVELOPE_HOP audio samples (with meter
    attack/release ballistics) and keeps exactly ``history_seconds`` worth, so
    the UI trace covers the same time span as the raw waveform and scrolls at
    the same speed.
    """

    WARMUP_POINTS = 40  # ~0.4 s at 48 kHz before adaptive triggers arm

    def __init__(
        self,
        sample_rate: float = 48000.0,
        freq_low_hz: float = FULL_RANGE_LOW,
        freq_high_hz: float = FULL_RANGE_HIGH,
        history_seconds: float = 3.0,
        hp_q: float = _BUTTERWORTH_Q,
        lp_q: float = _BUTTERWORTH_Q,
        low_shelf_hz: float = LOW_SHELF_HZ,
        low_shelf_db: float = 0.0,
        low_shelf_q: float = 0.9,
        high_shelf_hz: float = HIGH_SHELF_HZ,
        high_shelf_db: float = 0.0,
        high_shelf_q: float = 0.9,
        gate_enabled: bool = False,
        gate_db: float = -50.0,
    ):
        self._sr = float(sample_rate)
        self._lo = float(freq_low_hz)
        self._hi = float(freq_high_hz)
        self._hp_q = float(hp_q)
        self._lp_q = float(lp_q)
        self._low_shelf_hz = float(low_shelf_hz)
        self._low_shelf_db = float(low_shelf_db)
        self._low_shelf_q = float(low_shelf_q)
        self._high_shelf_hz = float(high_shelf_hz)
        self._high_shelf_db = float(high_shelf_db)
        self._high_shelf_q = float(high_shelf_q)
        self._gate_enabled = bool(gate_enabled)
        self._gate_db = float(gate_db)
        self._history_seconds = float(max(1.0, min(16.0, history_seconds)))
        self._chain = FilterChain()
        self._gate = NoiseGate()
        # Smoothed (audible) parameter values glide toward the targets above,
        # so live EQ tweaks sweep instead of jumping (no static/zipper noise).
        self._cur = self._param_targets()
        self._dirty = False
        self._redesign()
        self._env: deque[float] = deque(maxlen=self._env_points())
        self._env_smooth = 0.0
        self._partial = np.zeros(0, dtype=np.float32)
        self._avg = 1e-6
        self._level = 0.0
        self._latest_energy = 0.0
        self._points_total = 0
        self._last_fire: dict[str, float] = {}
        self._trigger_flash: dict[str, float] = {}

    def _env_points(self) -> int:
        return max(16, int(self._sr * self._history_seconds / ENVELOPE_HOP))

    _LOG_PARAMS = frozenset({"lo", "hi", "hp_q", "lp_q", "ls_hz", "hs_hz"})

    def _param_targets(self) -> dict[str, float]:
        return {
            "lo": self._lo,
            "hi": self._hi,
            "hp_q": self._hp_q,
            "lp_q": self._lp_q,
            "ls_hz": self._low_shelf_hz,
            "ls_db": self._low_shelf_db,
            "ls_q": self._low_shelf_q,
            "hs_hz": self._high_shelf_hz,
            "hs_db": self._high_shelf_db,
            "hs_q": self._high_shelf_q,
        }

    def _redesign(self) -> None:
        cur = self._cur
        self._chain.design(
            self._sr,
            hp_hz=cur["lo"],
            lp_hz=cur["hi"],
            hp_q=cur["hp_q"],
            lp_q=cur["lp_q"],
            low_shelf_hz=cur["ls_hz"],
            low_shelf_db=cur["ls_db"],
            low_shelf_q=cur["ls_q"],
            high_shelf_hz=cur["hs_hz"],
            high_shelf_db=cur["hs_db"],
            high_shelf_q=cur["hs_q"],
        )

    def _advance_smoothing(self, n_samples: int) -> None:
        """Glide the audible params toward targets (~50 ms time constant)."""
        alpha = 1.0 - math.exp(-max(1, n_samples) / (0.05 * self._sr))
        targets = self._param_targets()
        done = True
        for key, tgt in targets.items():
            cur = self._cur[key]
            if cur == tgt:
                continue
            if key in self._LOG_PARAMS and cur > 0 and tgt > 0:
                new = cur * (tgt / cur) ** alpha
                if abs(math.log(new / tgt)) < 1e-3:
                    new = tgt
            else:
                new = cur + (tgt - cur) * alpha
                if abs(new - tgt) < 0.02:
                    new = tgt
            if new != tgt:
                done = False
            self._cur[key] = new
        self._redesign()
        self._dirty = not done

    def _resize_env(self) -> None:
        points = self._env_points()
        if self._env.maxlen != points:
            self._env = deque(self._env, maxlen=points)

    def set_sample_rate(self, sample_rate: float) -> None:
        rate = float(sample_rate)
        if rate <= 0 or abs(rate - self._sr) < 1:
            return
        self._sr = rate
        # Rate change invalidates the running state anyway; snap, don't glide.
        self._cur = self._param_targets()
        self._dirty = False
        self._redesign()
        self._resize_env()

    def set_freq_range(self, low_hz: float, high_hz: float) -> None:
        low_hz = float(low_hz)
        high_hz = float(max(high_hz, low_hz + 5.0))
        if abs(low_hz - self._lo) < 0.5 and abs(high_hz - self._hi) < 0.5:
            return
        self._lo = low_hz
        self._hi = high_hz
        self._dirty = True

    def configure_eq(
        self,
        low_shelf_db: float,
        high_shelf_db: float,
        gate_enabled: bool,
        gate_db: float,
        *,
        hp_q: float | None = None,
        lp_q: float | None = None,
        low_shelf_hz: float | None = None,
        low_shelf_q: float | None = None,
        high_shelf_hz: float | None = None,
        high_shelf_q: float | None = None,
    ) -> None:
        new = {
            "_low_shelf_db": float(low_shelf_db),
            "_high_shelf_db": float(high_shelf_db),
            "_hp_q": float(hp_q) if hp_q is not None else self._hp_q,
            "_lp_q": float(lp_q) if lp_q is not None else self._lp_q,
            "_low_shelf_hz": float(low_shelf_hz) if low_shelf_hz is not None else self._low_shelf_hz,
            "_low_shelf_q": float(low_shelf_q) if low_shelf_q is not None else self._low_shelf_q,
            "_high_shelf_hz": float(high_shelf_hz) if high_shelf_hz is not None else self._high_shelf_hz,
            "_high_shelf_q": float(high_shelf_q) if high_shelf_q is not None else self._high_shelf_q,
        }
        changed = any(abs(v - getattr(self, k)) >= 0.01 for k, v in new.items())
        for k, v in new.items():
            setattr(self, k, v)
        self._gate_enabled = bool(gate_enabled)
        self._gate_db = float(gate_db)
        if changed:
            self._dirty = True
        if not self._gate_enabled:
            self._gate.reset()

    def set_history_seconds(self, seconds: float) -> None:
        seconds = float(max(1.0, min(16.0, seconds)))
        if abs(seconds - self._history_seconds) < 0.05:
            return
        self._history_seconds = seconds
        self._resize_env()

    @property
    def level(self) -> float:
        return self._level

    @property
    def sample_rate(self) -> float:
        return self._sr

    def envelope_history(self) -> np.ndarray:
        if not self._env:
            return np.zeros(0, dtype=np.float32)
        return np.asarray(self._env, dtype=np.float32)

    def process(self, samples: np.ndarray) -> np.ndarray:
        """EQ + gate a block, update level + smoothed envelope. Returns processed audio."""
        if samples.size == 0:
            return samples.astype(np.float32, copy=False)
        if self._dirty:
            # While gliding, filter in small sub-chunks so coefficients update
            # every ~5 ms — a smooth sweep instead of one audible step per block.
            chunk = max(64, int(self._sr * 0.005))
            parts: list[np.ndarray] = []
            for start in range(0, samples.size, chunk):
                piece = samples[start : start + chunk]
                if self._dirty:
                    self._advance_smoothing(piece.size)
                parts.append(self._chain.process(piece))
            filtered = np.concatenate(parts) if len(parts) > 1 else parts[0]
        else:
            filtered = self._chain.process(samples)

        buf = np.concatenate([self._partial, filtered]) if self._partial.size else filtered
        n_points = buf.size // ENVELOPE_HOP
        out_hops: list[np.ndarray] = []
        latest = 0.0
        for i in range(n_points):
            hop = buf[i * ENVELOPE_HOP : (i + 1) * ENVELOPE_HOP]
            if self._gate_enabled:
                hop = self._gate.process_hop(hop, self._gate_db)
            out_hops.append(hop)
            point = float(np.sqrt(np.mean(np.square(hop))) + 1e-12)
            # Meter ballistics: fast attack, slower release → smooth humps
            coef = ENV_ATTACK if point > self._env_smooth else ENV_RELEASE
            self._env_smooth += (point - self._env_smooth) * coef
            self._env.append(self._env_smooth)
            self._avg = 0.995 * self._avg + 0.005 * max(point, 1e-9)
            self._points_total += 1
            latest = max(latest, point)
        self._partial = buf[n_points * ENVELOPE_HOP :].copy()
        if n_points:
            self._latest_energy = latest

        processed = (
            np.concatenate(out_hops + [self._partial])[-samples.size :]
            if out_hops
            else filtered
        )
        rms = float(np.sqrt(np.mean(np.square(processed))) + 1e-12)
        self._level = 0.85 * self._level + 0.15 * rms
        return processed.astype(np.float32, copy=False)

    def evaluate_triggers(self, triggers: list[ChannelTriggerConfig]) -> list[ChannelTriggerFire]:
        now = time.monotonic()
        fires: list[ChannelTriggerFire] = []
        for tid in list(self._trigger_flash.keys()):
            self._trigger_flash[tid] = max(0.0, self._trigger_flash[tid] - 0.12)

        if self._points_total < self.WARMUP_POINTS:
            return fires

        energy = self._latest_energy
        for trig in triggers:
            if not trig.enabled:
                continue
            if not trig.osc_command.strip() and not trig.midi_enabled:
                continue
            avg = max(self._avg, 1e-9)
            # sensitivity 1..10 → threshold multiplier from ~3.6 down to ~1.15
            sens = max(1, min(10, int(trig.sensitivity)))
            k = 3.6 - (sens - 1) * (2.45 / 9.0)
            threshold = avg * k
            # Absolute floor so silence doesn't trip
            if energy < max(threshold, 0.002):
                continue
            last = self._last_fire.get(trig.id, 0.0)
            cooldown = max(50, int(trig.cooldown_ms)) / 1000.0
            if now - last < cooldown:
                continue
            self._last_fire[trig.id] = now
            self._trigger_flash[trig.id] = 1.0
            fires.append(
                ChannelTriggerFire(
                    trigger_id=trig.id,
                    command=trig.osc_command.strip(),
                    timestamp=now,
                    midi_enabled=trig.midi_enabled,
                    midi_channel=trig.midi_channel,
                    midi_note=trig.midi_note,
                )
            )
        return fires

    def trigger_flash(self, trigger_id: str) -> float:
        return self._trigger_flash.get(trigger_id, 0.0)
