"""JSON settings persistence in %APPDATA%\\GRate."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_DIR_NAME = "GRate"
CONFIG_FILENAME = "settings.json"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / CONFIG_FILENAME


@dataclass
class OscSettings:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    prefix: str = "gma3/cmd"  # full OSC cmd path, or MA3 prefix "gma3"
    bpm_delta: float = 0.25


@dataclass
class MidiSettings:
    enabled: bool = False
    port_name: str = ""
    clock_lane_id: str = ""
    note_velocity: int = 100
    note_duration_ms: int = 20


@dataclass
class LaneTriggerConfig:
    speed_master: str = "3.1"
    send_bpm: bool = True
    beat_command: str = ""
    beat_divider: int = 1
    resync_command: str = ""
    midi_enabled: bool = False
    midi_channel: int = 1  # 1-16
    midi_note: int = 36  # C2
    beat_offset_ms: int = 0


@dataclass
class BandTriggerConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    enabled: bool = True
    band: str = "low"  # low | mid | high
    sensitivity: int = 5  # 1-10
    cooldown_ms: int = 250
    osc_command: str = ""


@dataclass
class LaneBeatConfig:
    bpm_min: float = 60.0
    bpm_max: float = 180.0
    hold_on_silence: bool = True
    silence_freeze_seconds: float = 0.0  # 0 = never freeze to 0
    confidence_threshold: float = 0.0
    beat_source: str = "mix"  # mix | low | mid | high


@dataclass
class LaneConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Lane"
    enabled: bool = True
    device_name: str = ""
    device_index: int | None = None
    channel_mode: str = "sum"  # left | right | sum
    gain_db: float = 0.0
    monitor_enabled: bool = False
    monitor_device_name: str = ""
    monitor_device_index: int | None = None
    monitor_gain_db: float = 0.0
    monitor_source: str = "mix"  # mix | low | mid | high | beat
    trigger: LaneTriggerConfig = field(default_factory=LaneTriggerConfig)
    beat: LaneBeatConfig = field(default_factory=LaneBeatConfig)
    band_triggers: list[BandTriggerConfig] = field(default_factory=list)


@dataclass
class AppConfig:
    osc: OscSettings = field(default_factory=OscSettings)
    midi: MidiSettings = field(default_factory=MidiSettings)
    lanes: list[LaneConfig] = field(default_factory=list)
    window_geometry: str = ""

    def ensure_default_lane(self) -> None:
        if not self.lanes:
            self.lanes.append(LaneConfig(name="Lane 1"))


def _band_trigger_from_dict(data: dict[str, Any]) -> BandTriggerConfig:
    base = asdict(BandTriggerConfig())
    base.update({k: v for k, v in data.items() if k in base})
    return BandTriggerConfig(**base)


def _lane_from_dict(data: dict[str, Any]) -> LaneConfig:
    trigger = LaneTriggerConfig(**{**asdict(LaneTriggerConfig()), **data.get("trigger", {})})
    beat = LaneBeatConfig(**{**asdict(LaneBeatConfig()), **data.get("beat", {})})
    band_triggers = [_band_trigger_from_dict(item) for item in data.get("band_triggers", [])]
    base = asdict(LaneConfig())
    base.update({k: v for k, v in data.items() if k not in ("trigger", "beat", "band_triggers")})
    base["trigger"] = trigger
    base["beat"] = beat
    base["band_triggers"] = band_triggers
    return LaneConfig(**base)


def default_config() -> AppConfig:
    cfg = AppConfig()
    cfg.ensure_default_lane()
    return cfg


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()

    osc = OscSettings(**{**asdict(OscSettings()), **raw.get("osc", {})})
    midi = MidiSettings(**{**asdict(MidiSettings()), **raw.get("midi", {})})
    lanes = [_lane_from_dict(item) for item in raw.get("lanes", [])]
    cfg = AppConfig(
        osc=osc,
        midi=midi,
        lanes=lanes,
        window_geometry=raw.get("window_geometry", ""),
    )
    cfg.ensure_default_lane()
    return cfg


def save_config(cfg: AppConfig) -> None:
    payload = {
        "osc": asdict(cfg.osc),
        "midi": asdict(cfg.midi),
        "lanes": [asdict(lane) for lane in cfg.lanes],
        "window_geometry": cfg.window_geometry,
    }
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def clone_config(cfg: AppConfig) -> AppConfig:
    return load_config_from_dict(json.loads(json.dumps({
        "osc": asdict(cfg.osc),
        "midi": asdict(cfg.midi),
        "lanes": [asdict(lane) for lane in cfg.lanes],
        "window_geometry": cfg.window_geometry,
    })))


def load_config_from_dict(raw: dict[str, Any]) -> AppConfig:
    osc = OscSettings(**{**asdict(OscSettings()), **raw.get("osc", {})})
    midi = MidiSettings(**{**asdict(MidiSettings()), **raw.get("midi", {})})
    lanes = [_lane_from_dict(item) for item in raw.get("lanes", [])]
    cfg = AppConfig(
        osc=osc,
        midi=midi,
        lanes=lanes,
        window_geometry=raw.get("window_geometry", ""),
    )
    cfg.ensure_default_lane()
    return cfg


def new_lane(name: str | None = None) -> LaneConfig:
    return LaneConfig(name=name or "Lane")
