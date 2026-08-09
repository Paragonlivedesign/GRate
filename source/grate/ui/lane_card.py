"""Lane card: input picker, live waveform, BPM, band triggers, monitor, expandable settings."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from grate.audio.capture import InputDeviceInfo, list_input_devices, list_output_devices
from grate.config import BandTriggerConfig, LaneConfig
from grate.core.engine import LaneRuntime
from grate.dsp.bands import BANDS, BAND_COLORS
from grate.ui.device_picker import DevicePickerDialog
from grate.ui.theme import (
    ACCENT,
    BAND_HIGH,
    BAND_LOW,
    BAND_MID,
    BEAT_COLOR,
    LOCK_AMBER,
    LOCK_GRAY,
    LOCK_GREEN,
)


class BandTriggerRow(QWidget):
    changed = Signal()
    remove_requested = Signal(str)

    def __init__(self, trigger: BandTriggerConfig, parent=None):
        super().__init__(parent)
        self.trigger = trigger
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.enabled = QCheckBox()
        self.enabled.setChecked(trigger.enabled)
        self.enabled.stateChanged.connect(self._emit)

        self.band = QComboBox()
        for b in BANDS:
            self.band.addItem(b.title(), b)
        idx = self.band.findData(trigger.band)
        self.band.setCurrentIndex(max(0, idx))
        self.band.currentIndexChanged.connect(self._emit)

        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(1, 10)
        self.sens.setValue(trigger.sensitivity)
        self.sens.setFixedWidth(80)
        self.sens.valueChanged.connect(self._emit)
        self.sens_label = QLabel(str(trigger.sensitivity))
        self.sens_label.setFixedWidth(18)

        self.cooldown = QSpinBox()
        self.cooldown.setRange(50, 5000)
        self.cooldown.setSuffix(" ms")
        self.cooldown.setValue(trigger.cooldown_ms)
        self.cooldown.valueChanged.connect(self._emit)

        self.command = QLineEdit(trigger.osc_command)
        self.command.setPlaceholderText("Go+ Sequence 5")
        self.command.editingFinished.connect(self._emit)

        self.flash = QLabel("●")
        self.flash.setStyleSheet("color:#333; font-size:14px;")

        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.clicked.connect(lambda: self.remove_requested.emit(self.trigger.id))

        row.addWidget(self.enabled)
        row.addWidget(self.band)
        row.addWidget(QLabel("Sens"))
        row.addWidget(self.sens)
        row.addWidget(self.sens_label)
        row.addWidget(self.cooldown)
        row.addWidget(self.command, stretch=1)
        row.addWidget(self.flash)
        row.addWidget(remove)

    def _emit(self, *_args) -> None:
        self.sens_label.setText(str(self.sens.value()))
        self.apply()
        self.changed.emit()

    def apply(self) -> BandTriggerConfig:
        self.trigger.enabled = self.enabled.isChecked()
        self.trigger.band = self.band.currentData() or "low"
        self.trigger.sensitivity = int(self.sens.value())
        self.trigger.cooldown_ms = int(self.cooldown.value())
        self.trigger.osc_command = self.command.text().strip()
        return self.trigger

    def set_flash(self, amount: float) -> None:
        if amount > 0.3:
            color = BAND_COLORS.get(self.trigger.band, BEAT_COLOR)
            self.flash.setStyleSheet(f"color: {color}; font-size:14px;")
        else:
            self.flash.setStyleSheet("color:#333; font-size:14px;")


class LaneCard(QFrame):
    changed = Signal(str)  # lane_id
    remove_requested = Signal(str)
    tap_requested = Signal(str)
    multiply_requested = Signal(str, float)
    resync_requested = Signal(str)
    rebind_requested = Signal(str)

    def __init__(self, runtime: LaneRuntime, devices: list[InputDeviceInfo], parent=None):
        super().__init__(parent)
        self.setObjectName("laneCard")
        self.runtime = runtime
        self._devices = devices
        self._outputs = list_output_devices()
        self._expanded = False
        self._trigger_rows: list[BandTriggerRow] = []
        self._build()
        self.load_from_config()

    @property
    def lane_id(self) -> str:
        return self.runtime.config.id

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        left = QVBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Lane name")
        self.name_edit.setMaximumWidth(180)
        self.name_edit.editingFinished.connect(self._on_edited)
        left.addWidget(self.name_edit)

        device_row = QHBoxLayout()
        self.device_label = QLabel("No device")
        self.device_label.setWordWrap(True)
        self.device_label.setMinimumWidth(160)
        self.device_label.setStyleSheet("color:#9aa3b5; font-size:12px;")
        self.device_btn = QPushButton("Choose…")
        self.device_btn.setObjectName("accentButton")
        self.device_btn.clicked.connect(self._pick_input)
        device_row.addWidget(self.device_label, stretch=1)
        device_row.addWidget(self.device_btn)
        left.addLayout(device_row)

        self.channel_combo = QComboBox()
        self.channel_combo.addItem("Sum (L+R)", "sum")
        self.channel_combo.addItem("Left", "left")
        self.channel_combo.addItem("Right", "right")
        self.channel_combo.currentIndexChanged.connect(self._on_edited)
        left.addWidget(self.channel_combo)

        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel("Gain"))
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(-12.0, 36.0)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setSingleStep(1.0)
        self.gain_spin.valueChanged.connect(self._on_edited)
        gain_row.addWidget(self.gain_spin)
        left.addLayout(gain_row)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.stateChanged.connect(self._on_edited)
        left.addWidget(self.enabled_check)
        top.addLayout(left)

        # Waveform + band envelopes + color legend
        wave_col = QVBoxLayout()
        wave_col.setSpacing(4)
        pg.setConfigOptions(antialias=True, background="#0e1015", foreground="#9aa3b5")
        self.plot = pg.PlotWidget()
        self.plot.setFixedHeight(100)
        self.plot.setMinimumWidth(280)
        self.plot.hideAxis("left")
        self.plot.hideAxis("bottom")
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)
        # Waveform as a subtle background; band envelopes drawn on top and more opaque
        self.curve = self.plot.plot(pen=pg.mkPen("#4cc9a055", width=0.9))
        self.band_curves = {
            "low": self.plot.plot(pen=pg.mkPen(BAND_LOW, width=2.0)),
            "mid": self.plot.plot(pen=pg.mkPen(BAND_MID, width=2.0)),
            "high": self.plot.plot(pen=pg.mkPen(BAND_HIGH, width=2.0)),
        }
        self.plot.setYRange(-0.6, 1.05)
        wave_col.addWidget(self.plot)

        legend = QHBoxLayout()
        legend.setSpacing(12)
        for color, title, tip in (
            (BAND_LOW, "Low", "20–150 Hz · kick / bass"),
            (BAND_MID, "Mid", "150 Hz–2 kHz · snare / vocals"),
            (BAND_HIGH, "High", "2–10 kHz · hats / cymbals"),
            ("#4cc9a088", "Wave", "full mix waveform"),
        ):
            chip = QLabel(f"● {title}")
            chip.setStyleSheet(f"color:{color}; font-size:11px; font-weight:600;")
            chip.setToolTip(tip)
            legend.addWidget(chip)
        legend.addStretch(1)
        wave_col.addLayout(legend)
        top.addLayout(wave_col, stretch=2)

        # BPM block
        bpm_box = QVBoxLayout()
        bpm_row = QHBoxLayout()
        self.beat_dot = QLabel("●")
        self.beat_dot.setStyleSheet("color: #333; font-size: 18px;")
        self.bpm_label = QLabel("--.-")
        self.bpm_label.setObjectName("bpmValue")
        self.bpm_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bpm_row.addWidget(self.beat_dot)
        bpm_row.addWidget(self.bpm_label)
        bpm_box.addLayout(bpm_row)

        self.lock_chip = QLabel("NO SIGNAL")
        self.lock_chip.setAlignment(Qt.AlignCenter)
        self.lock_chip.setStyleSheet(
            f"background:#1a1c22; color:{LOCK_GRAY}; border-radius:8px; padding:2px 8px; font-size:11px; font-weight:600;"
        )
        bpm_box.addWidget(self.lock_chip)

        self.level_label = QLabel("Level —")
        self.level_label.setStyleSheet("color:#6d7588; font-size:11px;")
        bpm_box.addWidget(self.level_label)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        self.tap_btn = QPushButton("Tap")
        self.tap_btn.clicked.connect(lambda: self.tap_requested.emit(self.lane_id))
        self.half_btn = QPushButton("½")
        self.half_btn.clicked.connect(lambda: self.multiply_requested.emit(self.lane_id, 0.5))
        self.dbl_btn = QPushButton("×2")
        self.dbl_btn.clicked.connect(lambda: self.multiply_requested.emit(self.lane_id, 2.0))
        for b, w in ((self.tap_btn, 52), (self.half_btn, 40), (self.dbl_btn, 44)):
            b.setObjectName("compactButton")
            b.setFixedWidth(w)
            b.setFixedHeight(28)
            quick.addWidget(b)
        bpm_box.addLayout(quick)
        top.addLayout(bpm_box)

        # Trigger summary + actions
        right = QVBoxLayout()
        self.summary = QLabel("")
        self.summary.setObjectName("triggerSummary")
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(160)
        right.addWidget(self.summary)

        btn_row = QHBoxLayout()
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("accentButton")
        self.settings_btn.clicked.connect(self.toggle_settings)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.lane_id))
        btn_row.addWidget(self.settings_btn)
        btn_row.addWidget(self.remove_btn)
        right.addLayout(btn_row)
        top.addLayout(right)

        root.addLayout(top)

        # Expandable settings
        self.settings_panel = QWidget()
        form = QFormLayout(self.settings_panel)
        form.setContentsMargins(4, 8, 4, 4)
        form.setSpacing(6)

        self.speed_master = QLineEdit()
        self.speed_master.setPlaceholderText("3.1")
        self.speed_master.editingFinished.connect(self._on_edited)
        form.addRow("Speed Master", self.speed_master)

        self.send_bpm = QCheckBox("Send BPM via OSC")
        self.send_bpm.stateChanged.connect(self._on_edited)
        form.addRow("", self.send_bpm)

        self.beat_source = QComboBox()
        self.beat_source.addItem("Full mix", "mix")
        self.beat_source.addItem("Low (kick/bass)", "low")
        self.beat_source.addItem("Mid (snare/vox)", "mid")
        self.beat_source.addItem("High (hats)", "high")
        self.beat_source.currentIndexChanged.connect(self._on_edited)
        form.addRow("Beat source", self.beat_source)

        self.beat_command = QLineEdit()
        self.beat_command.setPlaceholderText("Go+ Sequence 1")
        self.beat_command.editingFinished.connect(self._on_edited)
        form.addRow("Per-beat command", self.beat_command)

        self.beat_divider = QComboBox()
        for n in (1, 2, 4, 8):
            self.beat_divider.addItem(f"Every {n} beat(s)", n)
        self.beat_divider.currentIndexChanged.connect(self._on_edited)
        form.addRow("Beat divider", self.beat_divider)

        self.resync_command = QLineEdit()
        self.resync_command.setPlaceholderText("Go+ Sequence 1 Cue 1")
        self.resync_command.editingFinished.connect(self._on_edited)
        form.addRow("Resync command", self.resync_command)

        self.midi_enabled = QCheckBox("MIDI note per beat")
        self.midi_enabled.stateChanged.connect(self._on_edited)
        form.addRow("", self.midi_enabled)

        midi_row = QHBoxLayout()
        self.midi_channel = QSpinBox()
        self.midi_channel.setRange(1, 16)
        self.midi_note = QSpinBox()
        self.midi_note.setRange(0, 127)
        self.midi_channel.valueChanged.connect(self._on_edited)
        self.midi_note.valueChanged.connect(self._on_edited)
        midi_row.addWidget(QLabel("Ch"))
        midi_row.addWidget(self.midi_channel)
        midi_row.addWidget(QLabel("Note"))
        midi_row.addWidget(self.midi_note)
        form.addRow("MIDI", midi_row)

        self.bpm_min = QDoubleSpinBox()
        self.bpm_min.setRange(20, 300)
        self.bpm_max = QDoubleSpinBox()
        self.bpm_max.setRange(20, 400)
        self.bpm_min.valueChanged.connect(self._on_edited)
        self.bpm_max.valueChanged.connect(self._on_edited)
        range_row = QHBoxLayout()
        range_row.addWidget(self.bpm_min)
        range_row.addWidget(QLabel("–"))
        range_row.addWidget(self.bpm_max)
        form.addRow("BPM range", range_row)

        self.beat_offset = QSpinBox()
        self.beat_offset.setRange(-200, 500)
        self.beat_offset.setSuffix(" ms")
        self.beat_offset.valueChanged.connect(self._on_edited)
        form.addRow("Beat offset", self.beat_offset)

        # Monitor
        mon_box = QHBoxLayout()
        self.monitor_enabled = QCheckBox("Monitor")
        self.monitor_enabled.stateChanged.connect(self._on_edited)
        self.monitor_device_btn = QPushButton("Output…")
        self.monitor_device_btn.clicked.connect(self._pick_monitor)
        self.monitor_device_label = QLabel("Default")
        self.monitor_device_label.setStyleSheet("color:#6d7588; font-size:11px;")
        self.monitor_source = QComboBox()
        self.monitor_source.addItem("Mix", "mix")
        self.monitor_source.addItem("Low solo", "low")
        self.monitor_source.addItem("Mid solo", "mid")
        self.monitor_source.addItem("High solo", "high")
        self.monitor_source.addItem("Beat source", "beat")
        self.monitor_source.currentIndexChanged.connect(self._on_edited)
        self.monitor_gain = QDoubleSpinBox()
        self.monitor_gain.setRange(-24.0, 24.0)
        self.monitor_gain.setSuffix(" dB")
        self.monitor_gain.valueChanged.connect(self._on_edited)
        mon_box.addWidget(self.monitor_enabled)
        mon_box.addWidget(self.monitor_device_btn)
        mon_box.addWidget(self.monitor_device_label, stretch=1)
        mon_box.addWidget(self.monitor_source)
        mon_box.addWidget(self.monitor_gain)
        form.addRow("Listen", mon_box)

        # Band triggers
        form.addRow(QLabel("Band triggers"))
        self.triggers_container = QWidget()
        self.triggers_host = QVBoxLayout(self.triggers_container)
        self.triggers_host.setContentsMargins(0, 0, 0, 0)
        self.triggers_host.setSpacing(4)
        form.addRow(self.triggers_container)
        add_trig = QPushButton("+ Add band trigger")
        add_trig.clicked.connect(self._add_trigger)
        form.addRow("", add_trig)

        action_row = QHBoxLayout()
        tap_btn = QPushButton("Tap")
        tap_btn.clicked.connect(lambda: self.tap_requested.emit(self.lane_id))
        half_btn = QPushButton("½")
        half_btn.clicked.connect(lambda: self.multiply_requested.emit(self.lane_id, 0.5))
        dbl_btn = QPushButton("×2")
        dbl_btn.clicked.connect(lambda: self.multiply_requested.emit(self.lane_id, 2.0))
        resync_btn = QPushButton("Resync")
        resync_btn.setObjectName("accentButton")
        resync_btn.clicked.connect(lambda: self.resync_requested.emit(self.lane_id))
        action_row.addWidget(tap_btn)
        action_row.addWidget(half_btn)
        action_row.addWidget(dbl_btn)
        action_row.addWidget(resync_btn)
        form.addRow("Manual", action_row)

        self.settings_panel.setVisible(False)
        root.addWidget(self.settings_panel)

    def toggle_settings(self) -> None:
        self._expanded = not self._expanded
        self.settings_panel.setVisible(self._expanded)
        self.settings_btn.setText("Hide" if self._expanded else "Settings")

    def refresh_devices(self, devices: list[InputDeviceInfo]) -> None:
        self._devices = devices
        self._outputs = list_output_devices()
        self._update_device_label()

    def _update_device_label(self) -> None:
        name = self.runtime.config.device_name
        if name:
            short = name if len(name) < 42 else name[:39] + "…"
            self.device_label.setText(short)
            self.device_label.setToolTip(name)
        else:
            self.device_label.setText("No device — Choose…")
            self.device_label.setToolTip("")
        mon = self.runtime.config.monitor_device_name
        self.monitor_device_label.setText(mon if mon else "Default / system")

    def _pick_input(self) -> None:
        devices = self._devices or list_input_devices()
        dlg = DevicePickerDialog(
            devices,
            self.runtime.config.device_name,
            title="Select input device",
            parent=self,
        )
        if dlg.exec() != DevicePickerDialog.Accepted:
            return
        device = dlg.selected_device()
        if device is None:
            return
        self.runtime.config.device_name = device.name
        self.runtime.config.device_index = device.index
        self._update_device_label()
        self.changed.emit(self.lane_id)
        self.rebind_requested.emit(self.lane_id)

    def _pick_monitor(self) -> None:
        devices = self._outputs or list_output_devices()
        dlg = DevicePickerDialog(
            devices,
            self.runtime.config.monitor_device_name,
            title="Select monitor output",
            parent=self,
        )
        if dlg.exec() != DevicePickerDialog.Accepted:
            return
        device = dlg.selected_device()
        if device is None:
            return
        self.runtime.config.monitor_device_name = device.name
        self.runtime.config.monitor_device_index = device.index
        self._update_device_label()
        self._on_edited()

    def _clear_trigger_rows(self) -> None:
        while self._trigger_rows:
            row = self._trigger_rows.pop()
            self.triggers_host.removeWidget(row)
            row.deleteLater()

    def _rebuild_trigger_rows(self) -> None:
        self._clear_trigger_rows()
        for trig in self.runtime.config.band_triggers:
            self._add_trigger_row(trig)

    def _add_trigger(self) -> None:
        trig = BandTriggerConfig(band="low", osc_command="")
        self.runtime.config.band_triggers.append(trig)
        self._add_trigger_row(trig)
        self._on_edited()

    def _add_trigger_row(self, trig: BandTriggerConfig) -> None:
        row = BandTriggerRow(trig)
        row.changed.connect(self._on_edited)
        row.remove_requested.connect(self._remove_trigger)
        self._trigger_rows.append(row)
        self.triggers_host.addWidget(row)

    def _remove_trigger(self, trigger_id: str) -> None:
        self.runtime.config.band_triggers = [
            t for t in self.runtime.config.band_triggers if t.id != trigger_id
        ]
        self._rebuild_trigger_rows()
        self._on_edited()

    def load_from_config(self) -> None:
        cfg = self.runtime.config
        widgets = [
            self.name_edit, self.channel_combo, self.enabled_check, self.gain_spin,
            self.speed_master, self.send_bpm, self.beat_source, self.beat_command,
            self.beat_divider, self.resync_command, self.midi_enabled, self.midi_channel,
            self.midi_note, self.bpm_min, self.bpm_max, self.beat_offset,
            self.monitor_enabled, self.monitor_source, self.monitor_gain,
        ]
        for w in widgets:
            w.blockSignals(True)

        self.name_edit.setText(cfg.name)
        self.enabled_check.setChecked(cfg.enabled)
        ch_idx = self.channel_combo.findData(cfg.channel_mode)
        self.channel_combo.setCurrentIndex(max(0, ch_idx))
        self.gain_spin.setValue(cfg.gain_db)
        self.speed_master.setText(cfg.trigger.speed_master)
        self.send_bpm.setChecked(cfg.trigger.send_bpm)
        src_idx = self.beat_source.findData(cfg.beat.beat_source)
        self.beat_source.setCurrentIndex(max(0, src_idx))
        self.beat_command.setText(cfg.trigger.beat_command)
        div_idx = self.beat_divider.findData(cfg.trigger.beat_divider)
        self.beat_divider.setCurrentIndex(max(0, div_idx))
        self.resync_command.setText(cfg.trigger.resync_command)
        self.midi_enabled.setChecked(cfg.trigger.midi_enabled)
        self.midi_channel.setValue(cfg.trigger.midi_channel)
        self.midi_note.setValue(cfg.trigger.midi_note)
        self.bpm_min.setValue(cfg.beat.bpm_min)
        self.bpm_max.setValue(cfg.beat.bpm_max)
        self.beat_offset.setValue(cfg.trigger.beat_offset_ms)
        self.monitor_enabled.setChecked(cfg.monitor_enabled)
        mon_idx = self.monitor_source.findData(cfg.monitor_source)
        self.monitor_source.setCurrentIndex(max(0, mon_idx))
        self.monitor_gain.setValue(cfg.monitor_gain_db)
        self._update_device_label()
        self._rebuild_trigger_rows()
        self._update_summary()

        for w in widgets:
            w.blockSignals(False)

    def apply_to_config(self) -> LaneConfig:
        cfg = self.runtime.config
        cfg.name = self.name_edit.text().strip() or "Lane"
        cfg.enabled = self.enabled_check.isChecked()
        cfg.channel_mode = self.channel_combo.currentData() or "sum"
        cfg.gain_db = float(self.gain_spin.value())
        cfg.trigger.speed_master = self.speed_master.text().strip() or "3.1"
        cfg.trigger.send_bpm = self.send_bpm.isChecked()
        cfg.beat.beat_source = self.beat_source.currentData() or "mix"
        cfg.trigger.beat_command = self.beat_command.text().strip()
        cfg.trigger.beat_divider = int(self.beat_divider.currentData() or 1)
        cfg.trigger.resync_command = self.resync_command.text().strip()
        cfg.trigger.midi_enabled = self.midi_enabled.isChecked()
        cfg.trigger.midi_channel = self.midi_channel.value()
        cfg.trigger.midi_note = self.midi_note.value()
        cfg.trigger.beat_offset_ms = self.beat_offset.value()
        cfg.beat.bpm_min = float(self.bpm_min.value())
        cfg.beat.bpm_max = float(self.bpm_max.value())
        cfg.monitor_enabled = self.monitor_enabled.isChecked()
        cfg.monitor_source = self.monitor_source.currentData() or "mix"
        cfg.monitor_gain_db = float(self.monitor_gain.value())
        for row in self._trigger_rows:
            row.apply()
        self._update_summary()
        return cfg

    def _update_summary(self) -> None:
        cfg = self.runtime.config
        parts = []
        if cfg.trigger.send_bpm:
            parts.append(f"SM {cfg.trigger.speed_master}")
        if cfg.beat.beat_source and cfg.beat.beat_source != "mix":
            parts.append(f"beat:{cfg.beat.beat_source}")
        if cfg.trigger.beat_command:
            parts.append(f"{cfg.trigger.beat_command} /{cfg.trigger.beat_divider}")
        enabled_trigs = [t for t in cfg.band_triggers if t.enabled and t.osc_command]
        if enabled_trigs:
            parts.append(f"{len(enabled_trigs)} band trig")
        if cfg.monitor_enabled:
            parts.append("mon")
        if cfg.trigger.midi_enabled:
            parts.append(f"MIDI ch{cfg.trigger.midi_channel} n{cfg.trigger.midi_note}")
        self.summary.setText(" + ".join(parts) if parts else "No triggers assigned")

    def _on_edited(self, *_args) -> None:
        self.apply_to_config()
        self.changed.emit(self.lane_id)

    def update_live(
        self,
        waveform: np.ndarray,
        level: float,
        bpm: float,
        flash: float,
        *,
        lock_status: str = "NO SIGNAL",
        band_histories: dict[str, np.ndarray] | None = None,
        trigger_flashes: dict[str, float] | None = None,
    ) -> None:
        wave_len = 0
        if waveform.size:
            step = max(1, waveform.size // 400)
            y = waveform[::step]
            peak = float(np.max(np.abs(y))) + 1e-6
            # Keep the raw wave small and centered low so band envelopes stay readable
            y = (y / max(peak, 0.05)) * 0.35
            self.curve.setData(y)
            wave_len = y.size
            self.plot.setYRange(-0.55, 1.05)

        if band_histories:
            for band, hist in band_histories.items():
                curve = self.band_curves.get(band)
                if curve is None or hist.size == 0:
                    continue
                env = hist.astype(np.float32)
                peak = float(np.max(env)) + 1e-9
                env = (env / peak) * 0.95
                target = wave_len if wave_len else env.size
                if env.size != target and target > 1:
                    x_old = np.linspace(0, 1, env.size)
                    x_new = np.linspace(0, 1, target)
                    env = np.interp(x_new, x_old, env).astype(np.float32)
                curve.setData(env)
                # Ensure bands stay above the waveform layer
                try:
                    curve.setZValue(10)
                except Exception:
                    pass
            try:
                self.curve.setZValue(0)
            except Exception:
                pass

        if bpm > 0:
            self.bpm_label.setText(f"{bpm:5.1f}")
        else:
            self.bpm_label.setText("--.-")

        db = 20 * np.log10(max(level, 1e-6))
        if db < -40:
            color = "#e76f51"
            hint = "  · signal low — raise gain"
        elif db < -24:
            color = LOCK_AMBER
            hint = ""
        else:
            color = LOCK_GREEN
            hint = ""
        self.level_label.setText(f"Level {db:5.1f} dBFS{hint}")
        self.level_label.setStyleSheet(f"color:{color}; font-size:11px;")

        status = lock_status or "NO SIGNAL"
        if status == "LOCKED":
            chip_bg, chip_fg = "#1a2e24", LOCK_GREEN
        elif status == "TRACKING":
            chip_bg, chip_fg = "#2e2a1a", LOCK_AMBER
        else:
            chip_bg, chip_fg = "#1a1c22", LOCK_GRAY
        self.lock_chip.setText(status)
        self.lock_chip.setStyleSheet(
            f"background:{chip_bg}; color:{chip_fg}; border-radius:8px; "
            f"padding:2px 8px; font-size:11px; font-weight:600;"
        )

        if flash > 0.3:
            self.beat_dot.setStyleSheet(f"color: {BEAT_COLOR}; font-size: 18px;")
            self.bpm_label.setStyleSheet(f"color: {ACCENT};")
        else:
            self.beat_dot.setStyleSheet("color: #333; font-size: 18px;")
            self.bpm_label.setStyleSheet("")

        if trigger_flashes:
            for row in self._trigger_rows:
                row.set_flash(trigger_flashes.get(row.trigger.id, 0.0))
