"""WASAPI device enumeration and shared input streams with per-lane fan-out."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np
import sounddevice as sd

DeviceFamily = Literal["dante", "voicemeeter", "physical", "virtual"]


@dataclass(frozen=True)
class InputDeviceInfo:
    index: int
    name: str
    channels: int
    sample_rate: float
    hostapi_name: str
    family: DeviceFamily = "physical"
    is_input: bool = True

    @property
    def label(self) -> str:
        return f"{self.name}  [{self.channels}ch @ {int(self.sample_rate)}Hz]"

    @property
    def family_label(self) -> str:
        return {
            "dante": "Dante (DVS)",
            "voicemeeter": "Voicemeeter",
            "physical": "Physical",
            "virtual": "Other virtual",
        }.get(self.family, "Other")


ChannelMode = str  # left | right | sum


def classify_device_family(name: str) -> DeviceFamily:
    lower = name.lower()
    if "dvs" in lower or "dante" in lower:
        return "dante"
    if "voicemeeter" in lower or "vb-audio" in lower:
        return "voicemeeter"
    virtual_keys = (
        "ndi",
        "cable",
        "steam",
        "oculus",
        "vdvad",
        "virtual",
        "point",
        "cato",
    )
    if any(k in lower for k in virtual_keys):
        return "virtual"
    return "physical"


def _collect_devices(*, inputs: bool) -> list[InputDeviceInfo]:
    devices: list[InputDeviceInfo] = []
    hostapis = sd.query_hostapis()
    for idx, info in enumerate(sd.query_devices()):
        channels = int(info.get("max_input_channels" if inputs else "max_output_channels", 0))
        if channels <= 0:
            continue
        hostapi = hostapis[int(info["hostapi"])]["name"]
        name = str(info["name"])
        devices.append(
            InputDeviceInfo(
                index=idx,
                name=name,
                channels=channels,
                sample_rate=float(info.get("default_samplerate", 48000)),
                hostapi_name=str(hostapi),
                family=classify_device_family(name),
                is_input=inputs,
            )
        )
    devices.sort(
        key=lambda d: (
            0 if "WASAPI" in d.hostapi_name else 1,
            {"dante": 0, "voicemeeter": 1, "physical": 2, "virtual": 3}.get(d.family, 9),
            d.name.lower(),
        )
    )
    return devices


def list_input_devices() -> list[InputDeviceInfo]:
    return _collect_devices(inputs=True)


def list_output_devices() -> list[InputDeviceInfo]:
    return _collect_devices(inputs=False)


def resolve_device(
    device_name: str,
    device_index: int | None,
    devices: list[InputDeviceInfo] | None = None,
    *,
    outputs: bool = False,
) -> InputDeviceInfo | None:
    devices = devices or (list_output_devices() if outputs else list_input_devices())
    if not devices:
        return None
    if device_name:
        for d in devices:
            if d.name == device_name:
                return d
        for d in devices:
            if device_name.lower() in d.name.lower():
                return d
    if device_index is not None:
        for d in devices:
            if d.index == device_index:
                return d
    return devices[0]


def extract_channel(block: np.ndarray, mode: ChannelMode) -> np.ndarray:
    """Extract mono float32 vector from an (frames, channels) block."""
    if block.ndim == 1:
        return block.astype(np.float32, copy=False)
    if block.shape[1] == 1:
        return block[:, 0].astype(np.float32, copy=False)
    if mode == "left":
        return block[:, 0].astype(np.float32, copy=False)
    if mode == "right":
        return block[:, min(1, block.shape[1] - 1)].astype(np.float32, copy=False)
    return np.mean(block, axis=1, dtype=np.float32)


def db_to_lin(gain_db: float) -> float:
    return float(10.0 ** (gain_db / 20.0))


class _LaneSink:
    def __init__(
        self,
        lane_id: str,
        channel_mode: ChannelMode,
        gain_db: float = 0.0,
        waveform_seconds: float = 3.0,
    ):
        self.lane_id = lane_id
        self.channel_mode = channel_mode
        self.waveform_seconds = waveform_seconds
        self._gain_lin = db_to_lin(gain_db)
        self._lock = threading.Lock()
        self._audio_q: deque[np.ndarray] = deque()
        self._waveform = np.zeros(0, dtype=np.float32)
        self._sample_rate = 48000.0
        self._level = 0.0
        self._last_audio_time = 0.0

    def set_gain_db(self, gain_db: float) -> None:
        with self._lock:
            self._gain_lin = db_to_lin(float(gain_db))

    def set_channel_mode(self, mode: ChannelMode) -> None:
        with self._lock:
            self.channel_mode = mode

    def set_waveform_seconds(self, seconds: float) -> None:
        seconds = float(max(1.0, min(16.0, seconds)))
        with self._lock:
            if abs(seconds - self.waveform_seconds) < 0.05:
                return
            self.waveform_seconds = seconds
            maxlen = max(1, int(self._sample_rate * self.waveform_seconds))
            old = self._waveform
            self._waveform = np.zeros(maxlen, dtype=np.float32)
            if old.size:
                n = min(old.size, maxlen)
                self._waveform[-n:] = old[-n:]

    def set_sample_rate(self, rate: float) -> None:
        self._sample_rate = rate
        maxlen = max(1, int(rate * self.waveform_seconds))
        with self._lock:
            if self._waveform.size != maxlen:
                old = self._waveform
                self._waveform = np.zeros(maxlen, dtype=np.float32)
                if old.size:
                    n = min(old.size, maxlen)
                    self._waveform[-n:] = old[-n:]

    def push(self, block: np.ndarray) -> None:
        with self._lock:
            mode = self.channel_mode
            gain = self._gain_lin
        mono = extract_channel(block, mode) * gain
        mono = np.clip(mono, -1.0, 1.0).astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
        with self._lock:
            self._audio_q.append(mono.copy())
            while len(self._audio_q) > 200:
                self._audio_q.popleft()
            if self._waveform.size:
                n = min(mono.size, self._waveform.size)
                self._waveform = np.roll(self._waveform, -n)
                self._waveform[-n:] = mono[-n:]
            self._level = 0.85 * self._level + 0.15 * rms
            self._last_audio_time = time.monotonic()

    def pop_audio(self, max_samples: int | None = None) -> np.ndarray:
        with self._lock:
            if not self._audio_q:
                return np.zeros(0, dtype=np.float32)
            chunks = list(self._audio_q)
            self._audio_q.clear()
        data = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if max_samples is not None and data.size > max_samples:
            return data[-max_samples:]
        return data

    def waveform_snapshot(self) -> tuple[np.ndarray, float]:
        with self._lock:
            return self._waveform.copy(), self._level

    @property
    def last_audio_age(self) -> float:
        with self._lock:
            if self._last_audio_time <= 0:
                return 1e9
            return time.monotonic() - self._last_audio_time


class _DeviceStream:
    def __init__(self, device: InputDeviceInfo, blocksize: int = 512):
        self.device = device
        self.blocksize = blocksize
        self.sample_rate = device.sample_rate
        self._sinks: dict[str, _LaneSink] = {}
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def add_sink(self, sink: _LaneSink) -> None:
        sink.set_sample_rate(self.sample_rate)
        with self._lock:
            self._sinks[sink.lane_id] = sink

    def remove_sink(self, lane_id: str) -> None:
        with self._lock:
            self._sinks.pop(lane_id, None)

    def sink_count(self) -> int:
        with self._lock:
            return len(self._sinks)

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        block = np.asarray(indata, dtype=np.float32)
        with self._lock:
            sinks = list(self._sinks.values())
        for sink in sinks:
            sink.push(block)

    def start(self) -> None:
        if self._stream is not None:
            return
        channels = min(self.device.channels, 2)
        self._stream = sd.InputStream(
            device=self.device.index,
            channels=channels,
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None


class AudioCaptureManager:
    """Reference-counted shared WASAPI streams with per-lane channel fan-out."""

    def __init__(self, blocksize: int = 512):
        self.blocksize = blocksize
        self._streams: dict[int, _DeviceStream] = {}
        self._lane_bindings: dict[str, tuple[int, _LaneSink]] = {}
        self._lock = threading.Lock()

    def bind_lane(
        self,
        lane_id: str,
        device_name: str,
        device_index: int | None,
        channel_mode: ChannelMode = "sum",
        gain_db: float = 0.0,
        waveform_seconds: float = 3.0,
    ) -> InputDeviceInfo | None:
        device = resolve_device(device_name, device_index)
        if device is None:
            return None
        sink = _LaneSink(
            lane_id,
            channel_mode,
            gain_db=gain_db,
            waveform_seconds=max(1.0, min(16.0, float(waveform_seconds))),
        )
        with self._lock:
            self.unbind_lane_unlocked(lane_id)
            stream = self._streams.get(device.index)
            if stream is None:
                stream = _DeviceStream(device, blocksize=self.blocksize)
                self._streams[device.index] = stream
            stream.add_sink(sink)
            self._lane_bindings[lane_id] = (device.index, sink)
            stream.start()
        return device

    def set_lane_gain(self, lane_id: str, gain_db: float) -> None:
        with self._lock:
            binding = self._lane_bindings.get(lane_id)
        if binding is None:
            return
        binding[1].set_gain_db(gain_db)

    def set_lane_channel_mode(self, lane_id: str, mode: ChannelMode) -> None:
        with self._lock:
            binding = self._lane_bindings.get(lane_id)
        if binding is None:
            return
        binding[1].set_channel_mode(mode)

    def set_lane_waveform_seconds(self, lane_id: str, seconds: float) -> None:
        with self._lock:
            binding = self._lane_bindings.get(lane_id)
        if binding is None:
            return
        binding[1].set_waveform_seconds(seconds)

    def is_bound(self, lane_id: str) -> bool:
        with self._lock:
            return lane_id in self._lane_bindings

    def unbind_lane(self, lane_id: str) -> None:
        with self._lock:
            self.unbind_lane_unlocked(lane_id)

    def unbind_lane_unlocked(self, lane_id: str) -> None:
        binding = self._lane_bindings.pop(lane_id, None)
        if binding is None:
            return
        device_index, _sink = binding
        stream = self._streams.get(device_index)
        if stream is None:
            return
        stream.remove_sink(lane_id)
        if stream.sink_count() == 0:
            stream.stop()
            self._streams.pop(device_index, None)

    def pop_audio(self, lane_id: str) -> tuple[np.ndarray, float]:
        with self._lock:
            binding = self._lane_bindings.get(lane_id)
        if binding is None:
            return np.zeros(0, dtype=np.float32), 48000.0
        device_index, sink = binding
        stream = self._streams.get(device_index)
        rate = stream.sample_rate if stream else 48000.0
        return sink.pop_audio(), rate

    def waveform(self, lane_id: str) -> tuple[np.ndarray, float]:
        with self._lock:
            binding = self._lane_bindings.get(lane_id)
        if binding is None:
            return np.zeros(0, dtype=np.float32), 0.0
        return binding[1].waveform_snapshot()

    def stop_all(self) -> None:
        with self._lock:
            for lane_id in list(self._lane_bindings.keys()):
                self.unbind_lane_unlocked(lane_id)
