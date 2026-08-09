"""Per-lane aubio tempo tracker with smoothing, gating, and octave clamping."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import numpy as np

try:
    import aubio
except ImportError:  # pragma: no cover
    aubio = None  # type: ignore

SIGNAL_GATE_DB = -48.0
OUTLIER_PCT = 0.08
OUTLIER_STREAK = 3
LOCK_WINDOW = 5
LOCK_TOLERANCE_BPM = 2.0
LOCK_BEAT_AGE = 2.0


@dataclass
class BeatEvent:
    bpm: float
    confidence: float
    is_beat: bool
    timestamp: float
    has_signal: bool = True
    locked: bool = False


def clamp_octave(bpm: float, lo: float, hi: float) -> float:
    if bpm <= 0:
        return 0.0
    value = float(bpm)
    guard = 0
    while value < lo and guard < 8:
        value *= 2.0
        guard += 1
    guard = 0
    while value > hi and guard < 8:
        value /= 2.0
        guard += 1
    return max(lo, min(hi, value))


class BeatEngine:
    def __init__(
        self,
        sample_rate: int = 48000,
        hop_size: int = 512,
        bpm_min: float = 60.0,
        bpm_max: float = 180.0,
        confidence_threshold: float = 0.0,
        hold_on_silence: bool = True,
        silence_freeze_seconds: float = 0.0,
    ):
        self.hop_size = hop_size
        self.bpm_min = bpm_min
        self.bpm_max = bpm_max
        self.confidence_threshold = confidence_threshold
        self.hold_on_silence = hold_on_silence
        self.silence_freeze_seconds = silence_freeze_seconds

        self._sample_rate = int(sample_rate)
        self._tempo = None
        self._pending = np.zeros(0, dtype=np.float32)
        self._intervals: deque[float] = deque(maxlen=8)
        self._last_beat_time = 0.0
        self._last_audio_time = time.monotonic()
        self._bpm = 0.0
        self._confidence = 0.0
        self._manual_override_until = 0.0
        self._tap_times: deque[float] = deque(maxlen=8)
        self._has_signal = False
        self._recent_bpms: deque[float] = deque(maxlen=LOCK_WINDOW)
        self._outlier_candidate = 0.0
        self._outlier_count = 0
        self._rebuild_tempo()

    def configure(
        self,
        bpm_min: float | None = None,
        bpm_max: float | None = None,
        confidence_threshold: float | None = None,
        hold_on_silence: bool | None = None,
        silence_freeze_seconds: float | None = None,
    ) -> None:
        if bpm_min is not None:
            self.bpm_min = bpm_min
        if bpm_max is not None:
            self.bpm_max = bpm_max
        if confidence_threshold is not None:
            self.confidence_threshold = confidence_threshold
        if hold_on_silence is not None:
            self.hold_on_silence = hold_on_silence
        if silence_freeze_seconds is not None:
            self.silence_freeze_seconds = silence_freeze_seconds

    def set_sample_rate(self, sample_rate: float) -> None:
        rate = int(sample_rate)
        if rate <= 0 or rate == self._sample_rate:
            return
        self._sample_rate = rate
        self._rebuild_tempo()

    def _rebuild_tempo(self) -> None:
        if aubio is None:
            self._tempo = None
            return
        self._tempo = aubio.tempo("default", self.hop_size * 2, self.hop_size, self._sample_rate)
        try:
            self._tempo.set_silence(-50.0)
        except Exception:
            pass

    @property
    def bpm(self) -> float:
        return self._bpm

    @property
    def confidence(self) -> float:
        return self._confidence

    @property
    def has_signal(self) -> bool:
        return self._has_signal

    @property
    def locked(self) -> bool:
        if not self._has_signal or self._bpm <= 0:
            return False
        if self._last_beat_time <= 0:
            return False
        if time.monotonic() - self._last_beat_time > LOCK_BEAT_AGE:
            return False
        if len(self._recent_bpms) < LOCK_WINDOW:
            return False
        arr = np.asarray(self._recent_bpms, dtype=np.float32)
        return float(np.max(arr) - np.min(arr)) <= LOCK_TOLERANCE_BPM

    def process(self, samples: np.ndarray) -> list[BeatEvent]:
        now = time.monotonic()
        if samples.size:
            self._last_audio_time = now

        events: list[BeatEvent] = []

        # Silence freeze (optional hard zero)
        silence_age = now - self._last_audio_time
        if (
            self.silence_freeze_seconds > 0
            and silence_age >= self.silence_freeze_seconds
            and now >= self._manual_override_until
        ):
            self._bpm = 0.0
            self._confidence = 0.0
            self._has_signal = False
            return events

        if self._tempo is None or samples.size == 0:
            return events

        rms = float(np.sqrt(np.mean(np.square(samples))) + 1e-12)
        db = 20.0 * np.log10(max(rms, 1e-9))
        self._has_signal = db >= SIGNAL_GATE_DB

        if not self._has_signal:
            # Hold last BPM; do not feed noise into aubio
            events.append(
                BeatEvent(
                    bpm=self._bpm,
                    confidence=self._confidence,
                    is_beat=False,
                    timestamp=now,
                    has_signal=False,
                    locked=False,
                )
            )
            return events

        audio = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        offset = 0
        while offset + self.hop_size <= audio.size:
            frame = audio[offset : offset + self.hop_size]
            offset += self.hop_size
            beat = self._tempo(frame)
            is_beat = bool(beat and float(beat[0]) != 0.0)
            raw_bpm = float(self._tempo.get_bpm())
            conf = 0.0
            try:
                conf = float(self._tempo.get_confidence())
            except Exception:
                conf = 1.0 if is_beat else self._confidence

            if now < self._manual_override_until:
                events.append(
                    BeatEvent(
                        bpm=self._bpm,
                        confidence=1.0,
                        is_beat=is_beat,
                        timestamp=now,
                        has_signal=True,
                        locked=True,
                    )
                )
                if is_beat:
                    self._last_beat_time = now
                    self._recent_bpms.append(self._bpm)
                continue

            if is_beat and raw_bpm > 0:
                folded = clamp_octave(raw_bpm, self.bpm_min, self.bpm_max)
                if self._last_beat_time > 0:
                    interval = now - self._last_beat_time
                    if 60.0 / self.bpm_max <= interval <= 60.0 / max(self.bpm_min, 1.0):
                        self._intervals.append(interval)
                self._last_beat_time = now

                if self._intervals:
                    median_interval = float(np.median(self._intervals))
                    median_bpm = clamp_octave(60.0 / median_interval, self.bpm_min, self.bpm_max)
                    candidate = 0.65 * folded + 0.35 * median_bpm
                else:
                    candidate = folded

                accepted = False
                if self._bpm <= 0:
                    accepted = True
                else:
                    delta = abs(candidate - self._bpm) / max(self._bpm, 1.0)
                    if delta <= OUTLIER_PCT:
                        accepted = True
                        self._outlier_count = 0
                        self._outlier_candidate = 0.0
                    else:
                        if abs(candidate - self._outlier_candidate) / max(candidate, 1.0) <= OUTLIER_PCT:
                            self._outlier_count += 1
                        else:
                            self._outlier_candidate = candidate
                            self._outlier_count = 1
                        if self._outlier_count >= OUTLIER_STREAK:
                            accepted = True
                            self._outlier_count = 0
                            self._outlier_candidate = 0.0

                if accepted and conf >= self.confidence_threshold:
                    self._bpm = candidate
                    self._confidence = conf
                    self._recent_bpms.append(candidate)
                elif accepted and not self.hold_on_silence:
                    self._bpm = candidate
                    self._confidence = conf
                    self._recent_bpms.append(candidate)
                elif conf >= self.confidence_threshold and self._bpm > 0:
                    # Soft track when close enough was already handled; keep confidence warm
                    self._confidence = max(self._confidence * 0.9, conf)

            events.append(
                BeatEvent(
                    bpm=self._bpm,
                    confidence=self._confidence,
                    is_beat=is_beat,
                    timestamp=now,
                    has_signal=True,
                    locked=self.locked,
                )
            )

        self._pending = audio[offset:]
        return events

    def tap(self) -> float:
        """Manual tap-tempo. Returns current estimated BPM."""
        now = time.monotonic()
        self._tap_times.append(now)
        if len(self._tap_times) >= 2:
            intervals = [
                self._tap_times[i] - self._tap_times[i - 1]
                for i in range(1, len(self._tap_times))
            ]
            median_interval = float(np.median(intervals))
            if median_interval > 0:
                self._bpm = clamp_octave(60.0 / median_interval, self.bpm_min, self.bpm_max)
                self._confidence = 1.0
                self._manual_override_until = now + 4.0
                self._recent_bpms.clear()
                for _ in range(LOCK_WINDOW):
                    self._recent_bpms.append(self._bpm)
                self._last_beat_time = now
                self._has_signal = True
        return self._bpm

    def multiply(self, factor: float) -> float:
        if self._bpm <= 0:
            return self._bpm
        self._bpm = clamp_octave(self._bpm * factor, self.bpm_min, self.bpm_max)
        self._manual_override_until = time.monotonic() + 4.0
        self._confidence = 1.0
        self._recent_bpms.clear()
        for _ in range(LOCK_WINDOW):
            self._recent_bpms.append(self._bpm)
        self._last_beat_time = time.monotonic()
        return self._bpm

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._intervals.clear()
        self._last_beat_time = 0.0
        self._bpm = 0.0
        self._confidence = 0.0
        self._tap_times.clear()
        self._has_signal = False
        self._recent_bpms.clear()
        self._outlier_candidate = 0.0
        self._outlier_count = 0
        self._rebuild_tempo()
