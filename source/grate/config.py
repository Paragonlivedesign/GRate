"""JSON settings persistence in %APPDATA%\\GRate.

Data model: AppConfig -> tracks: list[TrackConfig] -> channels: list[ChannelConfig].
Each channel has its own input device, custom frequency range, color, and
triggers. The track owns BPM (driven by one chosen channel) and the
Speed Master / per-beat outputs. Legacy "lanes" settings files are migrated
transparently on load.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_DIR_NAME = "GRate"
CONFIG_FILENAME = "settings.json"

# Default colors cycled for new channels (teal / orange / violet / pink / yellow / blue)
DEFAULT_CHANNEL_COLORS = (
    "#4cc9a0",
    "#f4a261",
    "#b388eb",
    "#f472b6",
    "#f4d35e",
    "#4ea8de",
)

# Legacy fixed band ranges (used for migrating old band triggers / beat sources)
LEGACY_BAND_RANGES = {
    "low": (20.0, 150.0),
    "mid": (150.0, 2000.0),
    "high": (2000.0, 10000.0),
}

FULL_RANGE_LOW = 20.0
FULL_RANGE_HIGH = 20000.0


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
    bpm_delta: float = 1.0  # global fallback; tracks can override


@dataclass
class MidiSettings:
    enabled: bool = False
    port_name: str = ""
    clock_lane_id: str = ""  # track id that drives MIDI clock
    note_velocity: int = 100
    note_duration_ms: int = 20


@dataclass
class TrackTriggerConfig:
    speed_master: str = "3.1"
    send_bpm: bool = True
    beat_command: str = ""
    beat_divider: int = 1
    resync_command: str = ""
    midi_enabled: bool = False
    midi_channel: int = 1  # 1-16
    midi_note: int = 36  # C2
    beat_offset_ms: int = 0
    # OSC BPM send shaping (per track)
    bpm_average_count: int = 4  # 1 = off; median of last N readings
    bpm_send_delta: float = 1.0  # min BPM change before sending
    bpm_send_interval_ms: int = 500  # min time between BPM OSC sends


@dataclass
class TrackBeatConfig:
    bpm_min: float = 60.0
    bpm_max: float = 180.0
    hold_on_silence: bool = True
    silence_freeze_seconds: float = 0.0  # 0 = never freeze to 0
    confidence_threshold: float = 0.0


@dataclass
class ChannelTriggerConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    enabled: bool = True
    sensitivity: int = 5  # 1-10
    cooldown_ms: int = 250
    osc_command: str = ""
    midi_enabled: bool = False
    midi_channel: int = 1
    midi_note: int = 36


@dataclass
class ChannelConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Ch 1"
    enabled: bool = True
    device_name: str = ""
    device_index: int | None = None
    channel_mode: str = "sum"  # left | right | sum
    gain_db: float = 0.0
    freq_low_hz: float = FULL_RANGE_LOW  # high-pass cutoff
    freq_high_hz: float = FULL_RANGE_HIGH  # low-pass cutoff
    hp_q: float = 0.707  # high-pass resonance (0.707 = Butterworth)
    lp_q: float = 0.707  # low-pass resonance
    low_shelf_hz: float = 200.0
    low_shelf_db: float = 0.0
    low_shelf_q: float = 0.9  # shelf slope
    high_shelf_hz: float = 4000.0
    high_shelf_db: float = 0.0
    high_shelf_q: float = 0.9
    gate_enabled: bool = False
    gate_db: float = -50.0  # gate threshold (dBFS)
    color: str = DEFAULT_CHANNEL_COLORS[0]
    mute: bool = False  # dormant: no waveform, no triggers, no BPM
    solo: bool = False  # while any solo active, only soloed channels are read
    monitor: bool = False  # send filtered audio to the track monitor output
    triggers: list[ChannelTriggerConfig] = field(default_factory=list)

    @property
    def is_full_range(self) -> bool:
        return self.freq_low_hz <= FULL_RANGE_LOW + 5 and self.freq_high_hz >= FULL_RANGE_HIGH - 2000


@dataclass
class TrackConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Track"
    enabled: bool = True
    wave_window_seconds: float = 3.0  # visible history; larger = slower scroll
    tempo_multiplier: float = 1.0  # 0.5 / 1 / 2 from ½ and ×2 buttons
    monitor_enabled: bool = True  # master; auto-on when any channel 🎧 is pressed
    monitor_gain_db: float = 0.0
    monitor_device_name: str = ""  # blank = app default monitor output
    monitor_device_index: int | None = None
    beat_channel_id: str = ""  # which channel drives BPM
    trigger: TrackTriggerConfig = field(default_factory=TrackTriggerConfig)
    beat: TrackBeatConfig = field(default_factory=TrackBeatConfig)
    channels: list[ChannelConfig] = field(default_factory=list)

    def ensure_channel(self) -> None:
        if not self.channels:
            self.channels.append(ChannelConfig(name="Ch 1"))
        if not self.beat_channel_id or not any(c.id == self.beat_channel_id for c in self.channels):
            self.beat_channel_id = self.channels[0].id

    def beat_channel(self) -> ChannelConfig | None:
        for ch in self.channels:
            if ch.id == self.beat_channel_id:
                return ch
        return self.channels[0] if self.channels else None


@dataclass
class AppConfig:
    osc: OscSettings = field(default_factory=OscSettings)
    midi: MidiSettings = field(default_factory=MidiSettings)
    tracks: list[TrackConfig] = field(default_factory=list)
    default_monitor_device_name: str = ""
    default_monitor_device_index: int | None = None
    window_geometry: str = ""

    def ensure_default_track(self) -> None:
        if not self.tracks:
            self.tracks.append(new_track("Track 1"))
        for track in self.tracks:
            track.ensure_channel()


def _channel_trigger_from_dict(data: dict[str, Any]) -> ChannelTriggerConfig:
    base = asdict(ChannelTriggerConfig())
    base.update({k: v for k, v in data.items() if k in base})
    return ChannelTriggerConfig(**base)


def _channel_from_dict(data: dict[str, Any]) -> ChannelConfig:
    triggers = [_channel_trigger_from_dict(item) for item in data.get("triggers", [])]
    base = asdict(ChannelConfig())
    base.update({k: v for k, v in data.items() if k in base and k != "triggers"})
    base["triggers"] = triggers
    return ChannelConfig(**base)


def _track_from_dict(data: dict[str, Any]) -> TrackConfig:
    trigger = TrackTriggerConfig(
        **{**asdict(TrackTriggerConfig()), **_known(data.get("trigger", {}), TrackTriggerConfig)}
    )
    beat = TrackBeatConfig(
        **{**asdict(TrackBeatConfig()), **_known(data.get("beat", {}), TrackBeatConfig)}
    )
    channels = [_channel_from_dict(item) for item in data.get("channels", [])]
    base = asdict(TrackConfig())
    base.update(
        {k: v for k, v in data.items() if k in base and k not in ("trigger", "beat", "channels")}
    )
    base["trigger"] = trigger
    base["beat"] = beat
    base["channels"] = channels
    track = TrackConfig(**base)
    track.ensure_channel()
    return track


def _known(data: dict[str, Any], cls: type) -> dict[str, Any]:
    allowed = {f for f in asdict(cls())}  # dataclass field names
    return {k: v for k, v in data.items() if k in allowed}


def _track_from_legacy_lane(data: dict[str, Any]) -> TrackConfig:
    """Convert a legacy lane dict (single input + fixed 3-band analysis) to a track."""
    trigger = TrackTriggerConfig(
        **{**asdict(TrackTriggerConfig()), **_known(data.get("trigger", {}), TrackTriggerConfig)}
    )
    beat_raw = data.get("beat", {})
    beat = TrackBeatConfig(**{**asdict(TrackBeatConfig()), **_known(beat_raw, TrackBeatConfig)})

    main = ChannelConfig(
        name="Main",
        device_name=str(data.get("device_name", "")),
        device_index=data.get("device_index"),
        channel_mode=str(data.get("channel_mode", "sum")),
        gain_db=float(data.get("gain_db", 0.0)),
        color=DEFAULT_CHANNEL_COLORS[0],
    )
    # Legacy beat_source band becomes the main channel's filter range
    beat_source = str(beat_raw.get("beat_source", "mix")).lower()
    if beat_source in LEGACY_BAND_RANGES:
        main.freq_low_hz, main.freq_high_hz = LEGACY_BAND_RANGES[beat_source]

    channels = [main]
    band_channels: dict[str, ChannelConfig] = {}
    for i, bt in enumerate(data.get("band_triggers", [])):
        band = str(bt.get("band", "low")).lower()
        lo, hi = LEGACY_BAND_RANGES.get(band, (FULL_RANGE_LOW, FULL_RANGE_HIGH))
        ch = band_channels.get(band)
        if ch is None:
            ch = ChannelConfig(
                name=band.title(),
                device_name=main.device_name,
                device_index=main.device_index,
                channel_mode=main.channel_mode,
                gain_db=main.gain_db,
                freq_low_hz=lo,
                freq_high_hz=hi,
                color=DEFAULT_CHANNEL_COLORS[(len(channels)) % len(DEFAULT_CHANNEL_COLORS)],
            )
            band_channels[band] = ch
            channels.append(ch)
        ch.triggers.append(
            ChannelTriggerConfig(
                id=str(bt.get("id", uuid.uuid4().hex[:8])),
                enabled=bool(bt.get("enabled", True)),
                sensitivity=int(bt.get("sensitivity", 5)),
                cooldown_ms=int(bt.get("cooldown_ms", 250)),
                osc_command=str(bt.get("osc_command", "")),
            )
        )

    # Legacy monitor source → per-channel monitor flag
    monitor_source = str(data.get("monitor_source", "mix")).lower()
    if monitor_source in band_channels:
        band_channels[monitor_source].monitor = True
    else:
        main.monitor = True

    track = TrackConfig(
        id=str(data.get("id", uuid.uuid4().hex[:8])),
        name=str(data.get("name", "Track")),
        enabled=bool(data.get("enabled", True)),
        wave_window_seconds=float(data.get("wave_window_seconds", 3.0)),
        tempo_multiplier=float(data.get("tempo_multiplier", 1.0)),
        monitor_enabled=bool(data.get("monitor_enabled", True)),
        monitor_gain_db=float(data.get("monitor_gain_db", 0.0)),
        monitor_device_name=str(data.get("monitor_device_name", "")),
        monitor_device_index=data.get("monitor_device_index"),
        beat_channel_id=main.id,
        trigger=trigger,
        beat=beat,
        channels=channels,
    )
    return track


def default_config() -> AppConfig:
    cfg = AppConfig()
    cfg.ensure_default_track()
    return cfg


def load_config_from_dict(raw: dict[str, Any]) -> AppConfig:
    osc = OscSettings(**{**asdict(OscSettings()), **_known(raw.get("osc", {}), OscSettings)})
    midi = MidiSettings(**{**asdict(MidiSettings()), **_known(raw.get("midi", {}), MidiSettings)})
    if "tracks" in raw:
        tracks = [_track_from_dict(item) for item in raw.get("tracks", [])]
    else:
        tracks = [_track_from_legacy_lane(item) for item in raw.get("lanes", [])]
    cfg = AppConfig(
        osc=osc,
        midi=midi,
        tracks=tracks,
        default_monitor_device_name=str(raw.get("default_monitor_device_name", "")),
        default_monitor_device_index=raw.get("default_monitor_device_index"),
        window_geometry=raw.get("window_geometry", ""),
    )
    cfg.ensure_default_track()
    # Older configs left track Monitor off while channel 🎧 was on — unmute that.
    for track in cfg.tracks:
        if any(ch.monitor for ch in track.channels) and not track.monitor_enabled:
            track.monitor_enabled = True
    return cfg


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()
    return load_config_from_dict(raw)


def _config_payload(cfg: AppConfig) -> dict[str, Any]:
    return {
        "osc": asdict(cfg.osc),
        "midi": asdict(cfg.midi),
        "tracks": [asdict(track) for track in cfg.tracks],
        "default_monitor_device_name": cfg.default_monitor_device_name,
        "default_monitor_device_index": cfg.default_monitor_device_index,
        "window_geometry": cfg.window_geometry,
    }


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_config_payload(cfg), indent=2), encoding="utf-8")
    tmp.replace(path)


def clone_config(cfg: AppConfig) -> AppConfig:
    return load_config_from_dict(json.loads(json.dumps(_config_payload(cfg))))


def new_track(name: str | None = None) -> TrackConfig:
    track = TrackConfig(name=name or "Track")
    track.ensure_channel()
    return track


def new_channel(track: TrackConfig, name: str | None = None) -> ChannelConfig:
    """Create a channel with the first unused palette color; copies the first channel's device."""
    used = {c.color.lower() for c in track.channels}
    color = next(
        (c for c in DEFAULT_CHANNEL_COLORS if c.lower() not in used),
        DEFAULT_CHANNEL_COLORS[len(track.channels) % len(DEFAULT_CHANNEL_COLORS)],
    )
    ch = ChannelConfig(name=name or f"Ch {len(track.channels) + 1}", color=color)
    if track.channels:
        first = track.channels[0]
        ch.device_name = first.device_name
        ch.device_index = first.device_index
    return ch
