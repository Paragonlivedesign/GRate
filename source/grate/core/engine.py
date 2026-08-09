"""Lane runtime + trigger router coordinating capture, DSP, OSC, and MIDI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

from grate.audio.capture import AudioCaptureManager
from grate.audio.monitor import MonitorOutput
from grate.config import AppConfig, LaneConfig, save_config
from grate.dsp.bands import BANDS, BandAnalyzer, BandEnergies
from grate.dsp.beat_engine import BeatEngine
from grate.outputs.midi_out import MidiOutput
from grate.outputs.osc_out import OscOutput


@dataclass
class LaneRuntime:
    config: LaneConfig
    beat: BeatEngine = field(default_factory=BeatEngine)
    bands: BandAnalyzer = field(default_factory=BandAnalyzer)
    monitor: MonitorOutput = field(default_factory=MonitorOutput)
    bpm: float = 0.0
    confidence: float = 0.0
    level: float = 0.0
    beat_flash: float = 0.0
    beat_counter: int = 0
    last_error: str = ""
    sample_rate: float = 48000.0
    has_signal: bool = False
    locked: bool = False
    band_energies: BandEnergies = field(default_factory=BandEnergies)
    lock_status: str = "NO SIGNAL"  # LOCKED | TRACKING | NO SIGNAL


class AppEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.capture = AudioCaptureManager()
        self.osc = OscOutput()
        self.midi = MidiOutput()
        self.lanes: dict[str, LaneRuntime] = {}
        self._running = False
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._rebuild_lanes_from_config()
        self.apply_io_settings()

    def _make_runtime(self, lane_cfg: LaneConfig) -> LaneRuntime:
        engine = BeatEngine(
            bpm_min=lane_cfg.beat.bpm_min,
            bpm_max=lane_cfg.beat.bpm_max,
            confidence_threshold=lane_cfg.beat.confidence_threshold,
            hold_on_silence=lane_cfg.beat.hold_on_silence,
            silence_freeze_seconds=lane_cfg.beat.silence_freeze_seconds,
        )
        return LaneRuntime(config=lane_cfg, beat=engine)

    def _rebuild_lanes_from_config(self) -> None:
        self.lanes = {}
        for lane_cfg in self.config.lanes:
            self.lanes[lane_cfg.id] = self._make_runtime(lane_cfg)

    def apply_io_settings(self) -> None:
        self.osc.configure(self.config.osc)
        self.midi.configure(self.config.midi)

    def save(self) -> None:
        save_config(self.config)

    @property
    def running(self) -> bool:
        return self._running

    def _bind_lane_io(self, lane: LaneRuntime) -> None:
        device = self.capture.bind_lane(
            lane.config.id,
            lane.config.device_name,
            lane.config.device_index,
            lane.config.channel_mode,
            gain_db=lane.config.gain_db,
        )
        if device is None:
            lane.last_error = "No input device"
            return
        lane.config.device_name = device.name
        lane.config.device_index = device.index
        lane.sample_rate = device.sample_rate
        lane.beat.set_sample_rate(device.sample_rate)
        lane.bands.set_sample_rate(device.sample_rate)
        lane.last_error = ""
        self._configure_monitor(lane)

    def _configure_monitor(self, lane: LaneRuntime) -> None:
        ok = lane.monitor.configure(
            enabled=lane.config.monitor_enabled and self._running,
            device_name=lane.config.monitor_device_name,
            device_index=lane.config.monitor_device_index,
            gain_db=lane.config.monitor_gain_db,
            sample_rate=lane.sample_rate,
        )
        if not ok and lane.config.monitor_enabled:
            lane.last_error = lane.monitor.last_error or "Monitor failed"

    def start(self) -> None:
        if self._running:
            return
        self.apply_io_settings()
        self._stop.clear()
        for lane in self.lanes.values():
            if not lane.config.enabled:
                continue
            self._bind_lane_io(lane)
        self._running = True
        # Re-enable monitors now that running flag is set
        for lane in self.lanes.values():
            if lane.config.enabled:
                self._configure_monitor(lane)
        self._worker = threading.Thread(target=self._dsp_loop, name="grate-dsp", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        self._worker = None
        for lane in self.lanes.values():
            lane.monitor.close()
        self.capture.stop_all()

    def shutdown(self) -> None:
        self.stop()
        self.midi.close()
        self.save()

    def add_lane(self, lane_cfg: LaneConfig) -> LaneRuntime:
        runtime = self._make_runtime(lane_cfg)
        self.config.lanes.append(lane_cfg)
        self.lanes[lane_cfg.id] = runtime
        if self._running and lane_cfg.enabled:
            self._bind_lane_io(runtime)
        return runtime

    def remove_lane(self, lane_id: str) -> None:
        lane = self.lanes.pop(lane_id, None)
        if lane:
            lane.monitor.close()
        self.capture.unbind_lane(lane_id)
        self.config.lanes = [l for l in self.config.lanes if l.id != lane_id]
        if not self.config.lanes:
            from grate.config import new_lane

            self.add_lane(new_lane("Lane 1"))

    def rebind_lane(self, lane_id: str) -> None:
        lane = self.lanes.get(lane_id)
        if lane is None:
            return
        self.capture.unbind_lane(lane_id)
        lane.monitor.close()
        lane.beat.configure(
            bpm_min=lane.config.beat.bpm_min,
            bpm_max=lane.config.beat.bpm_max,
            confidence_threshold=lane.config.beat.confidence_threshold,
            hold_on_silence=lane.config.beat.hold_on_silence,
            silence_freeze_seconds=lane.config.beat.silence_freeze_seconds,
        )
        if self._running and lane.config.enabled:
            self._bind_lane_io(lane)

    def apply_lane_live(self, lane_id: str) -> None:
        """Apply gain/channel/monitor changes without full rebind when possible."""
        lane = self.lanes.get(lane_id)
        if lane is None:
            return
        # If enable state flipped while running, need a real bind/unbind
        if self._running:
            bound = self.capture.is_bound(lane_id)
            if lane.config.enabled and not bound:
                self._bind_lane_io(lane)
                return
            if not lane.config.enabled and bound:
                self.capture.unbind_lane(lane_id)
                lane.monitor.close()
                return
        self.capture.set_lane_gain(lane_id, lane.config.gain_db)
        self.capture.set_lane_channel_mode(lane_id, lane.config.channel_mode)
        lane.beat.configure(
            bpm_min=lane.config.beat.bpm_min,
            bpm_max=lane.config.beat.bpm_max,
            confidence_threshold=lane.config.beat.confidence_threshold,
            hold_on_silence=lane.config.beat.hold_on_silence,
            silence_freeze_seconds=lane.config.beat.silence_freeze_seconds,
        )
        if self._running:
            self._configure_monitor(lane)

    def waveform(self, lane_id: str) -> tuple[np.ndarray, float]:
        return self.capture.waveform(lane_id)

    def band_histories(self, lane_id: str) -> dict[str, np.ndarray]:
        lane = self.lanes.get(lane_id)
        if lane is None:
            return {b: np.zeros(0, dtype=np.float32) for b in BANDS}
        return {b: lane.bands.envelope_history(b) for b in BANDS}

    def trigger_flash(self, lane_id: str, trigger_id: str) -> float:
        lane = self.lanes.get(lane_id)
        if lane is None:
            return 0.0
        return lane.bands.trigger_flash(trigger_id)

    def tap(self, lane_id: str) -> None:
        lane = self.lanes.get(lane_id)
        if not lane:
            return
        bpm = lane.beat.tap()
        lane.bpm = bpm
        lane.locked = True
        lane.has_signal = True
        lane.lock_status = "LOCKED"
        self._emit_bpm(lane, force=True)

    def multiply_bpm(self, lane_id: str, factor: float) -> None:
        lane = self.lanes.get(lane_id)
        if not lane:
            return
        bpm = lane.beat.multiply(factor)
        lane.bpm = bpm
        lane.locked = True
        lane.lock_status = "LOCKED"
        self._emit_bpm(lane, force=True)

    def resync(self, lane_id: str) -> None:
        lane = self.lanes.get(lane_id)
        if not lane:
            return
        cmd = lane.config.trigger.resync_command.strip()
        if cmd and self.config.osc.enabled:
            self.osc.send_command(cmd)

    def _dsp_loop(self) -> None:
        while not self._stop.is_set() and self._running:
            for lane in list(self.lanes.values()):
                if not lane.config.enabled:
                    continue
                samples, rate = self.capture.pop_audio(lane.config.id)
                wave, level = self.capture.waveform(lane.config.id)
                lane.level = level
                if rate and abs(rate - lane.sample_rate) > 1:
                    lane.sample_rate = rate
                    lane.beat.set_sample_rate(rate)
                    lane.bands.set_sample_rate(rate)
                if samples.size == 0:
                    continue

                # Band analysis on the post-gain mix
                lane.band_energies = lane.bands.process(samples)

                # Band triggers
                for fire in lane.bands.evaluate_triggers(lane.config.band_triggers):
                    if self.config.osc.enabled:
                        self.osc.send_command(fire.command)

                # Beat source filtering
                beat_src = (lane.config.beat.beat_source or "mix").lower()
                beat_audio = lane.bands.select_source(samples, beat_src)

                # Monitor feed (may solo a band)
                mon_src = (lane.config.monitor_source or "mix").lower()
                mon_audio = lane.bands.select_source(samples, mon_src, beat_source=beat_src)
                lane.monitor.push(mon_audio)

                events = lane.beat.process(beat_audio)
                for event in events:
                    lane.has_signal = event.has_signal
                    lane.locked = event.locked
                    if not event.has_signal:
                        lane.lock_status = "NO SIGNAL"
                    elif event.locked:
                        lane.lock_status = "LOCKED"
                    elif event.bpm > 0:
                        lane.lock_status = "TRACKING"
                    else:
                        lane.lock_status = "NO SIGNAL"

                    if event.bpm > 0:
                        lane.bpm = event.bpm
                        lane.confidence = event.confidence
                        self._emit_bpm(lane)
                        self.midi.set_clock_bpm(lane.config.id, event.bpm)
                    if event.is_beat:
                        lane.beat_flash = 1.0
                        self._emit_beat(lane)
                if lane.beat_flash > 0:
                    lane.beat_flash = max(0.0, lane.beat_flash - 0.08)
            time.sleep(0.005)

    def _emit_bpm(self, lane: LaneRuntime, force: bool = False) -> None:
        trig = lane.config.trigger
        if not trig.send_bpm or not trig.speed_master:
            return
        if self.config.osc.enabled:
            self.osc.send_bpm(lane.config.id, trig.speed_master, lane.bpm, force=force)

    def _emit_beat(self, lane: LaneRuntime) -> None:
        trig = lane.config.trigger
        lane.beat_counter = (lane.beat_counter + 1) % max(1, int(trig.beat_divider))
        divider = max(1, int(trig.beat_divider))
        if not hasattr(lane, "_abs_beats"):
            lane._abs_beats = 0  # type: ignore[attr-defined]
        lane._abs_beats += 1  # type: ignore[attr-defined]
        if lane._abs_beats % divider != 0:  # type: ignore[attr-defined]
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
