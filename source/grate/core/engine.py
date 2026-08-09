"""Track/channel runtime + trigger router coordinating capture, DSP, OSC, and MIDI."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from grate.audio.capture import AudioCaptureManager
from grate.audio.monitor import MonitorOutput
from grate.config import AppConfig, ChannelConfig, TrackConfig, save_config
from grate.dsp.bands import ChannelProcessor
from grate.dsp.beat_engine import BeatEngine
from grate.outputs.midi_out import MidiOutput
from grate.outputs.osc_out import OscOutput


def _sink_key(track_id: str, channel_id: str) -> str:
    return f"{track_id}:{channel_id}"


@dataclass
class ChannelRuntime:
    config: ChannelConfig
    processor: ChannelProcessor
    level: float = 0.0
    sample_rate: float = 48000.0


@dataclass
class TrackRuntime:
    config: TrackConfig
    beat: BeatEngine = field(default_factory=BeatEngine)
    monitor: MonitorOutput = field(default_factory=MonitorOutput)
    channels: dict[str, ChannelRuntime] = field(default_factory=dict)
    bpm: float = 0.0
    smoothed_bpm: float = 0.0
    confidence: float = 0.0
    level: float = 0.0
    beat_flash: float = 0.0
    beat_counter: int = 0
    last_error: str = ""
    sample_rate: float = 48000.0
    has_signal: bool = False
    locked: bool = False
    lock_status: str = "NO SIGNAL"  # LOCKED | TRACKING | NO SIGNAL
    bpm_history: deque = field(default_factory=lambda: deque(maxlen=16))
    last_bpm_send_time: float = 0.0
    last_bpm_sent: float = 0.0
    last_beat_time: float = 0.0
    sample_counter: int = 0
    tap_mode: bool = False
    tap_flash: float = 0.0

    def solo_active(self) -> bool:
        return any(c.config.solo and c.config.enabled and not c.config.mute for c in self.channels.values())

    def channel_active(self, ch: ChannelRuntime) -> bool:
        """Active = read + drawn + triggering. Mute/solo gate analysis, not just audio."""
        if not ch.config.enabled or ch.config.mute:
            return False
        if self.solo_active() and not ch.config.solo:
            return False
        return True


class AppEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.capture = AudioCaptureManager()
        self.osc = OscOutput()
        self.midi = MidiOutput()
        self.tracks: dict[str, TrackRuntime] = {}
        self._running = False
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._rebuild_tracks_from_config()
        self.apply_io_settings()

    # ---------------------------------------------------------------- runtime setup

    def _make_channel_runtime(self, track_cfg: TrackConfig, ch_cfg: ChannelConfig) -> ChannelRuntime:
        proc = ChannelProcessor(
            freq_low_hz=ch_cfg.freq_low_hz,
            freq_high_hz=ch_cfg.freq_high_hz,
            history_seconds=track_cfg.wave_window_seconds,
            hp_q=ch_cfg.hp_q,
            lp_q=ch_cfg.lp_q,
            low_shelf_hz=ch_cfg.low_shelf_hz,
            low_shelf_db=ch_cfg.low_shelf_db,
            low_shelf_q=ch_cfg.low_shelf_q,
            high_shelf_hz=ch_cfg.high_shelf_hz,
            high_shelf_db=ch_cfg.high_shelf_db,
            high_shelf_q=ch_cfg.high_shelf_q,
            gate_enabled=ch_cfg.gate_enabled,
            gate_db=ch_cfg.gate_db,
        )
        return ChannelRuntime(config=ch_cfg, processor=proc)

    def _make_runtime(self, track_cfg: TrackConfig) -> TrackRuntime:
        track_cfg.ensure_channel()
        engine = BeatEngine(
            bpm_min=track_cfg.beat.bpm_min,
            bpm_max=track_cfg.beat.bpm_max,
            confidence_threshold=track_cfg.beat.confidence_threshold,
            hold_on_silence=track_cfg.beat.hold_on_silence,
            silence_freeze_seconds=track_cfg.beat.silence_freeze_seconds,
        )
        runtime = TrackRuntime(config=track_cfg, beat=engine)
        for ch_cfg in track_cfg.channels:
            runtime.channels[ch_cfg.id] = self._make_channel_runtime(track_cfg, ch_cfg)
        return runtime

    def _rebuild_tracks_from_config(self) -> None:
        self.tracks = {}
        for track_cfg in self.config.tracks:
            self.tracks[track_cfg.id] = self._make_runtime(track_cfg)

    def _sync_channels(self, track: TrackRuntime) -> None:
        """Reconcile ChannelRuntime dict with the config's channel list."""
        cfg_ids = {c.id for c in track.config.channels}
        for ch_id in list(track.channels.keys()):
            if ch_id not in cfg_ids:
                self.capture.unbind_lane(_sink_key(track.config.id, ch_id))
                track.channels.pop(ch_id, None)
        for ch_cfg in track.config.channels:
            if ch_cfg.id not in track.channels:
                track.channels[ch_cfg.id] = self._make_channel_runtime(track.config, ch_cfg)

    def apply_io_settings(self) -> None:
        self.osc.configure(self.config.osc)
        self.midi.configure(self.config.midi)

    def save(self) -> None:
        save_config(self.config)

    @property
    def running(self) -> bool:
        return self._running

    # ---------------------------------------------------------------- binding

    def _bind_channel_io(self, track: TrackRuntime, ch: ChannelRuntime) -> None:
        key = _sink_key(track.config.id, ch.config.id)
        if not ch.config.device_name and ch.config.device_index is None:
            self.capture.unbind_lane(key)
            return
        device = self.capture.bind_lane(
            key,
            ch.config.device_name,
            ch.config.device_index,
            ch.config.channel_mode,
            gain_db=ch.config.gain_db,
            waveform_seconds=track.config.wave_window_seconds,
        )
        if device is None:
            track.last_error = "No input device"
            return
        ch.config.device_name = device.name
        ch.config.device_index = device.index
        ch.sample_rate = device.sample_rate
        ch.processor.set_sample_rate(device.sample_rate)
        if ch.config.id == track.config.beat_channel_id:
            track.sample_rate = device.sample_rate
            track.beat.set_sample_rate(device.sample_rate)
        track.last_error = ""

    def _bind_track_io(self, track: TrackRuntime) -> None:
        self._sync_channels(track)
        for ch in track.channels.values():
            if ch.config.enabled:
                self._bind_channel_io(track, ch)
            else:
                self.capture.unbind_lane(_sink_key(track.config.id, ch.config.id))
        self._configure_monitor(track)

    def _unbind_track_io(self, track: TrackRuntime) -> None:
        for ch_id in list(track.channels.keys()):
            self.capture.unbind_lane(_sink_key(track.config.id, ch_id))
        track.monitor.close()

    def _configure_monitor(self, track: TrackRuntime) -> None:
        device_name = track.config.monitor_device_name or self.config.default_monitor_device_name
        device_index = (
            track.config.monitor_device_index
            if track.config.monitor_device_name
            else self.config.default_monitor_device_index
        )
        any_channel_monitored = any(
            c.config.monitor for c in track.channels.values() if c.config.enabled
        )
        want = bool(track.config.monitor_enabled and any_channel_monitored and self._running)
        ok = track.monitor.configure(
            enabled=want,
            device_name=device_name or "",
            device_index=device_index,
            gain_db=track.config.monitor_gain_db,
            sample_rate=track.sample_rate,
        )
        if want and not ok:
            track.last_error = f"Monitor: {track.monitor.last_error or 'failed'}"
        elif "Monitor:" in (track.last_error or ""):
            track.last_error = ""

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._running:
            return
        self.apply_io_settings()
        self._stop.clear()
        for track in self.tracks.values():
            if not track.config.enabled:
                continue
            self._bind_track_io(track)
        self._running = True
        # Re-enable monitors now that running flag is set
        for track in self.tracks.values():
            if track.config.enabled:
                self._configure_monitor(track)
        self._worker = threading.Thread(target=self._dsp_loop, name="grate-dsp", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        self._worker = None
        for track in self.tracks.values():
            track.monitor.close()
        self.capture.stop_all()

    def shutdown(self) -> None:
        self.stop()
        self.midi.close()
        self.save()

    # ---------------------------------------------------------------- track/channel management

    def add_track(self, track_cfg: TrackConfig) -> TrackRuntime:
        runtime = self._make_runtime(track_cfg)
        self.config.tracks.append(track_cfg)
        self.tracks[track_cfg.id] = runtime
        if self._running and track_cfg.enabled:
            self._bind_track_io(runtime)
        return runtime

    def remove_track(self, track_id: str) -> None:
        track = self.tracks.pop(track_id, None)
        if track:
            self._unbind_track_io(track)
        self.config.tracks = [t for t in self.config.tracks if t.id != track_id]
        if not self.config.tracks:
            from grate.config import new_track

            self.add_track(new_track("Track 1"))

    def rebind_track(self, track_id: str) -> None:
        track = self.tracks.get(track_id)
        if track is None:
            return
        self._unbind_track_io(track)
        self._sync_channels(track)
        track.beat.configure(
            bpm_min=track.config.beat.bpm_min,
            bpm_max=track.config.beat.bpm_max,
            confidence_threshold=track.config.beat.confidence_threshold,
            hold_on_silence=track.config.beat.hold_on_silence,
            silence_freeze_seconds=track.config.beat.silence_freeze_seconds,
        )
        if self._running and track.config.enabled:
            self._bind_track_io(track)

    def apply_track_live(self, track_id: str) -> None:
        """Apply gain/freq/mute/solo/monitor changes without a full rebind when possible."""
        track = self.tracks.get(track_id)
        if track is None:
            return
        self._sync_channels(track)

        if self._running:
            if not track.config.enabled:
                self._unbind_track_io(track)
                return
            for ch in track.channels.values():
                key = _sink_key(track.config.id, ch.config.id)
                bound = self.capture.is_bound(key)
                should_bind = ch.config.enabled and bool(
                    ch.config.device_name or ch.config.device_index is not None
                )
                if should_bind and not bound:
                    self._bind_channel_io(track, ch)
                elif not should_bind and bound:
                    self.capture.unbind_lane(key)

        for ch in track.channels.values():
            key = _sink_key(track.config.id, ch.config.id)
            self.capture.set_lane_gain(key, ch.config.gain_db)
            self.capture.set_lane_channel_mode(key, ch.config.channel_mode)
            self.capture.set_lane_waveform_seconds(key, track.config.wave_window_seconds)
            ch.processor.set_freq_range(ch.config.freq_low_hz, ch.config.freq_high_hz)
            ch.processor.configure_eq(
                low_shelf_db=ch.config.low_shelf_db,
                high_shelf_db=ch.config.high_shelf_db,
                gate_enabled=ch.config.gate_enabled,
                gate_db=ch.config.gate_db,
                hp_q=ch.config.hp_q,
                lp_q=ch.config.lp_q,
                low_shelf_hz=ch.config.low_shelf_hz,
                low_shelf_q=ch.config.low_shelf_q,
                high_shelf_hz=ch.config.high_shelf_hz,
                high_shelf_q=ch.config.high_shelf_q,
            )
            ch.processor.set_history_seconds(track.config.wave_window_seconds)

        track.beat.configure(
            bpm_min=track.config.beat.bpm_min,
            bpm_max=track.config.beat.bpm_max,
            confidence_threshold=track.config.beat.confidence_threshold,
            hold_on_silence=track.config.beat.hold_on_silence,
            silence_freeze_seconds=track.config.beat.silence_freeze_seconds,
        )
        if self._running:
            self._configure_monitor(track)

    # ---------------------------------------------------------------- UI data access

    def waveform(self, track_id: str) -> tuple[np.ndarray, float]:
        """Raw waveform of the track's beat channel."""
        track = self.tracks.get(track_id)
        if track is None:
            return np.zeros(0, dtype=np.float32), 0.0
        beat_ch = track.config.beat_channel()
        if beat_ch is None:
            return np.zeros(0, dtype=np.float32), 0.0
        return self.capture.waveform(_sink_key(track.config.id, beat_ch.id))

    def channel_envelopes(self, track_id: str) -> dict[str, np.ndarray]:
        track = self.tracks.get(track_id)
        if track is None:
            return {}
        result: dict[str, np.ndarray] = {}
        for ch_id, ch in track.channels.items():
            if track.channel_active(ch):
                result[ch_id] = ch.processor.envelope_history()
            else:
                result[ch_id] = np.zeros(0, dtype=np.float32)
        return result

    def channel_levels(self, track_id: str) -> dict[str, float]:
        track = self.tracks.get(track_id)
        if track is None:
            return {}
        return {ch_id: ch.level for ch_id, ch in track.channels.items()}

    def trigger_flash(self, track_id: str, channel_id: str, trigger_id: str) -> float:
        track = self.tracks.get(track_id)
        if track is None:
            return 0.0
        ch = track.channels.get(channel_id)
        if ch is None:
            return 0.0
        return ch.processor.trigger_flash(trigger_id)

    # ---------------------------------------------------------------- tap / manual tempo

    def set_tap_mode(self, track_id: str, enabled: bool) -> None:
        track = self.tracks.get(track_id)
        if not track:
            return
        track.tap_mode = bool(enabled)
        if not enabled and not track.beat.manual_sticky:
            # Leaving tap mode without Apply — drop provisional taps
            track.beat.clear_manual()

    def tap(self, track_id: str) -> None:
        track = self.tracks.get(track_id)
        if not track:
            return
        if not track.tap_mode:
            track.tap_mode = True
        bpm = track.beat.tap()
        track.bpm = bpm
        track.tap_flash = 1.0
        track.has_signal = True
        if bpm > 0:
            track.locked = True
            track.lock_status = "TRACKING"
        track.config.tempo_multiplier = 1.0
        # 4+ taps: auto-commit as sticky override (like pressing Apply).
        # Tap mode stays on so further taps keep refining the tempo.
        if bpm > 0 and track.beat.tap_count >= 4:
            track.beat.apply_manual()
            track.lock_status = "LOCKED"
            self._emit_bpm(track, force=True)

    def apply_tap(self, track_id: str) -> None:
        """Overwrite live waveform tempo with tapped BPM (sticky until cleared)."""
        track = self.tracks.get(track_id)
        if not track:
            return
        bpm = track.beat.apply_manual()
        if bpm <= 0:
            return
        track.bpm = bpm
        track.locked = True
        track.has_signal = True
        track.lock_status = "LOCKED"
        track.tap_mode = False
        track.config.tempo_multiplier = 1.0
        self._emit_bpm(track, force=True)

    def clear_tap(self, track_id: str) -> None:
        track = self.tracks.get(track_id)
        if not track:
            return
        track.beat.clear_manual()
        track.tap_mode = False
        track.lock_status = "TRACKING" if track.bpm > 0 else "NO SIGNAL"

    def multiply_bpm(self, track_id: str, factor: float) -> None:
        track = self.tracks.get(track_id)
        if not track:
            return
        bpm = track.beat.multiply(factor)
        track.bpm = bpm
        track.locked = True
        track.lock_status = "LOCKED"
        mult = float(track.config.tempo_multiplier or 1.0) * float(factor)
        # Keep in a sensible set so ½ / ×2 highlight stays meaningful
        track.config.tempo_multiplier = max(0.25, min(4.0, mult))
        self._emit_bpm(track, force=True)

    def resync(self, track_id: str) -> None:
        track = self.tracks.get(track_id)
        if not track:
            return
        cmd = track.config.trigger.resync_command.strip()
        if cmd and self.config.osc.enabled:
            self.osc.send_command(cmd)

    # ---------------------------------------------------------------- DSP loop

    def _dsp_loop(self) -> None:
        while not self._stop.is_set() and self._running:
            for track in list(self.tracks.values()):
                if not track.config.enabled:
                    continue
                self._process_track(track)
            time.sleep(0.005)

    def _process_track(self, track: TrackRuntime) -> None:
        beat_channel_id = track.config.beat_channel_id
        beat_audio: np.ndarray | None = None
        monitor_chunks: list[np.ndarray] = []

        for ch in list(track.channels.values()):
            key = _sink_key(track.config.id, ch.config.id)
            samples, rate = self.capture.pop_audio(key)
            if rate and abs(rate - ch.sample_rate) > 1:
                ch.sample_rate = rate
                ch.processor.set_sample_rate(rate)
                if ch.config.id == beat_channel_id:
                    track.sample_rate = rate
                    track.beat.set_sample_rate(rate)
                if ch.config.monitor:
                    track.monitor.set_source_rate(rate)
            if samples.size == 0:
                continue

            active = track.channel_active(ch)
            filtered = ch.processor.process(samples)
            ch.level = ch.processor.level

            if active:
                for fire in ch.processor.evaluate_triggers(ch.config.triggers):
                    if fire.command and self.config.osc.enabled:
                        self.osc.send_command(fire.command)
                    if fire.midi_enabled and self.config.midi.enabled:
                        self.midi.send_note(fire.midi_channel, fire.midi_note)
                if ch.config.id == beat_channel_id:
                    beat_audio = filtered
                    track.sample_counter += int(samples.size)
                    _wave, level = self.capture.waveform(key)
                    track.level = level

            # Monitor is independent of mute/solo — headphone button per channel
            if ch.config.monitor and track.config.monitor_enabled:
                monitor_chunks.append(filtered)

        if monitor_chunks and track.monitor.enabled:
            if len(monitor_chunks) == 1:
                mix = monitor_chunks[0]
            else:
                longest = max(c.size for c in monitor_chunks)
                mix = np.zeros(longest, dtype=np.float32)
                for chunk in monitor_chunks:
                    mix[: chunk.size] += chunk
            track.monitor.push(np.clip(mix, -1.0, 1.0))

        if beat_audio is not None:
            for event in track.beat.process(beat_audio):
                track.has_signal = event.has_signal
                track.locked = event.locked
                if not event.has_signal:
                    track.lock_status = "NO SIGNAL"
                elif event.locked:
                    track.lock_status = "LOCKED"
                elif event.bpm > 0:
                    track.lock_status = "TRACKING"
                else:
                    track.lock_status = "NO SIGNAL"

                if event.bpm > 0:
                    track.bpm = event.bpm
                    track.confidence = event.confidence
                    self._emit_bpm(track)
                    self.midi.set_clock_bpm(track.config.id, event.bpm)
                if event.is_beat:
                    track.beat_flash = 1.0
                    track.last_beat_time = event.timestamp
                    self._emit_beat(track)

        if track.beat_flash > 0:
            track.beat_flash = max(0.0, track.beat_flash - 0.08)
        if track.tap_flash > 0:
            track.tap_flash = max(0.0, track.tap_flash - 0.12)
        if track.beat.manual_sticky:
            track.lock_status = "LOCKED"

    # ---------------------------------------------------------------- output emission

    def _smoothed_bpm(self, track: TrackRuntime) -> float:
        """Median of recent BPM readings (per-track average window)."""
        if track.bpm <= 0:
            return 0.0
        count = max(1, int(track.config.trigger.bpm_average_count))
        if track.bpm_history.maxlen != max(16, count):
            track.bpm_history = deque(track.bpm_history, maxlen=max(16, count))
        track.bpm_history.append(float(track.bpm))
        if count <= 1 or len(track.bpm_history) == 0:
            track.smoothed_bpm = float(track.bpm)
            return track.smoothed_bpm
        window = list(track.bpm_history)[-count:]
        track.smoothed_bpm = float(np.median(np.asarray(window, dtype=np.float32)))
        return track.smoothed_bpm

    def _emit_bpm(self, track: TrackRuntime, force: bool = False) -> None:
        trig = track.config.trigger
        if not trig.send_bpm or not trig.speed_master:
            return
        if not self.config.osc.enabled:
            return

        value = self._smoothed_bpm(track)
        if value <= 0:
            return

        now = time.monotonic()
        delta_need = float(trig.bpm_send_delta)
        if delta_need <= 0:
            delta_need = float(self.config.osc.bpm_delta) or 1.0
        interval = max(0, int(trig.bpm_send_interval_ms)) / 1000.0

        if not force:
            if track.last_bpm_sent > 0 and abs(value - track.last_bpm_sent) < delta_need:
                return
            if interval > 0 and track.last_bpm_send_time > 0 and (now - track.last_bpm_send_time) < interval:
                return

        sent = self.osc.send_bpm(
            track.config.id,
            trig.speed_master,
            value,
            force=True,  # track already applied delta/interval gating
            min_delta=0.0,
        )
        if sent:
            track.last_bpm_sent = value
            track.last_bpm_send_time = now

    def _emit_beat(self, track: TrackRuntime) -> None:
        trig = track.config.trigger
        divider = max(1, int(trig.beat_divider))
        track.beat_counter = (track.beat_counter + 1) % divider
        if not hasattr(track, "_abs_beats"):
            track._abs_beats = 0  # type: ignore[attr-defined]
        track._abs_beats += 1  # type: ignore[attr-defined]
        if track._abs_beats % divider != 0:  # type: ignore[attr-defined]
            return

        offset = max(0, int(trig.beat_offset_ms)) / 1000.0

        def fire() -> None:
            if trig.beat_command.strip() and self.config.osc.enabled:
                self.osc.send_beat_command(trig.beat_command.strip())
            if trig.midi_enabled and self.config.midi.enabled:
                self.midi.send_note(trig.midi_channel, trig.midi_note)

        if offset > 0:
            threading.Timer(offset, fire).start()
        else:
            fire()
