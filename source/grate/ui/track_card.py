"""Track card: header BPM + tap cluster, overlay waveform, channel rows, settings."""

from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtGui import QColor, QPainter, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from grate.audio.capture import InputDeviceInfo, list_input_devices, list_output_devices
from grate.config import (
    ChannelConfig,
    ChannelTriggerConfig,
    TrackConfig,
    new_channel,
)
from grate.core.engine import TrackRuntime
from grate.dsp.bands import FREQ_PRESETS
from grate.ui.device_picker import DevicePickerDialog
from grate.ui.eq_dialog import EqDialog
from grate.ui.theme import (
    ACCENT,
    BEAT_COLOR,
    LOCK_AMBER,
    LOCK_GRAY,
    LOCK_GREEN,
)


class MiniLevelMeter(QWidget):
    """Tiny horizontal level bar tinted with the channel color."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._frac = 0.0
        self.setFixedSize(30, 14)
        self.setToolTip("Channel level (post-filter)")

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_level(self, rms: float, dimmed: bool = False) -> None:
        db = 20.0 * np.log10(max(float(rms), 1e-6))
        frac = max(0.0, min(1.0, (db + 60.0) / 60.0))
        self._frac = frac
        self._dimmed = dimmed
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#10131a"))
        color = QColor(self._color)
        if getattr(self, "_dimmed", False):
            color = QColor("#3a4150")
        w = int(self.width() * self._frac)
        if w > 0:
            painter.fillRect(0, 2, w, self.height() - 4, color)
        painter.setPen(QColor("#232833"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class ChannelTriggerRow(QWidget):
    changed = Signal()
    remove_requested = Signal(str)

    def __init__(self, trigger: ChannelTriggerConfig, color: str, parent=None):
        super().__init__(parent)
        self.trigger = trigger
        self._color = color
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.enabled = QCheckBox()
        self.enabled.setChecked(trigger.enabled)
        self.enabled.setToolTip("Enable this trigger")
        self.enabled.stateChanged.connect(self._emit)

        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(1, 10)
        self.sens.setValue(trigger.sensitivity)
        self.sens.setFixedWidth(80)
        self.sens.setToolTip("Sensitivity — higher fires on smaller spikes")
        self.sens.valueChanged.connect(self._emit)
        self.sens_label = QLabel(str(trigger.sensitivity))
        self.sens_label.setFixedWidth(18)

        self.cooldown = QSpinBox()
        self.cooldown.setRange(50, 5000)
        self.cooldown.setSuffix(" ms")
        self.cooldown.setValue(trigger.cooldown_ms)
        self.cooldown.setToolTip("Minimum time between fires")
        self.cooldown.valueChanged.connect(self._emit)

        self.command = QLineEdit(trigger.osc_command)
        self.command.setPlaceholderText("OSC: Go+ Sequence 5 (optional)")
        self.command.setToolTip("OSC command sent on each fire — leave empty for MIDI-only")
        self.command.editingFinished.connect(self._emit)

        self.midi_check = QCheckBox("MIDI")
        self.midi_check.setChecked(trigger.midi_enabled)
        self.midi_check.setToolTip("Also send a MIDI note on each fire (needs MIDI enabled in Settings)")
        self.midi_check.stateChanged.connect(self._emit)

        self.midi_ch = QSpinBox()
        self.midi_ch.setRange(1, 16)
        self.midi_ch.setValue(trigger.midi_channel)
        self.midi_ch.setPrefix("ch ")
        self.midi_ch.setMinimumWidth(84)
        self.midi_ch.setToolTip("MIDI channel")
        self.midi_ch.valueChanged.connect(self._emit)

        self.midi_note = QSpinBox()
        self.midi_note.setRange(0, 127)
        self.midi_note.setValue(trigger.midi_note)
        self.midi_note.setPrefix("n ")
        self.midi_note.setMinimumWidth(84)
        self.midi_note.setToolTip("MIDI note number (36 = C2)")
        self.midi_note.valueChanged.connect(self._emit)

        self.flash = QLabel("●")
        self.flash.setStyleSheet("color:#333; font-size:14px;")

        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.clicked.connect(lambda: self.remove_requested.emit(self.trigger.id))

        row.addWidget(self.enabled)
        row.addWidget(QLabel("Sens"))
        row.addWidget(self.sens)
        row.addWidget(self.sens_label)
        row.addWidget(self.cooldown)
        row.addWidget(self.command, stretch=1)
        row.addWidget(self.midi_check)
        row.addWidget(self.midi_ch)
        row.addWidget(self.midi_note)
        row.addWidget(self.flash)
        row.addWidget(remove)
        self._sync_midi_enabled()

    def _sync_midi_enabled(self) -> None:
        on = self.midi_check.isChecked()
        self.midi_ch.setEnabled(on)
        self.midi_note.setEnabled(on)

    def _emit(self, *_args) -> None:
        self.sens_label.setText(str(self.sens.value()))
        self._sync_midi_enabled()
        self.apply()
        self.changed.emit()

    def apply(self) -> ChannelTriggerConfig:
        self.trigger.enabled = self.enabled.isChecked()
        self.trigger.sensitivity = int(self.sens.value())
        self.trigger.cooldown_ms = int(self.cooldown.value())
        self.trigger.osc_command = self.command.text().strip()
        self.trigger.midi_enabled = self.midi_check.isChecked()
        self.trigger.midi_channel = int(self.midi_ch.value())
        self.trigger.midi_note = int(self.midi_note.value())
        return self.trigger

    def set_color(self, color: str) -> None:
        self._color = color

    def set_flash(self, amount: float) -> None:
        if amount > 0.3:
            self.flash.setStyleSheet(f"color: {self._color}; font-size:14px;")
        else:
            self.flash.setStyleSheet("color:#333; font-size:14px;")


class ChannelRow(QFrame):
    changed = Signal()  # live-tunable change (gain, freq, M/S/monitor, name, color)
    rebind_needed = Signal()  # device / enabled / add-remove changes
    remove_requested = Signal(str)  # channel id
    beat_selected = Signal(str)  # channel id

    def __init__(
        self,
        track_cfg: TrackConfig,
        ch_cfg: ChannelConfig,
        devices: list[InputDeviceInfo],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("channelRow")
        self.track_cfg = track_cfg
        self.cfg = ch_cfg
        self._devices = devices
        self._trigger_rows: list[ChannelTriggerRow] = []
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._build()
        self.load_from_config()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)

        self.beat_radio = QRadioButton()
        self.beat_radio.setAutoExclusive(False)
        self.beat_radio.setToolTip("This channel drives the track BPM")
        self.beat_radio.clicked.connect(self._on_beat_radio)
        row.addWidget(self.beat_radio)

        self.swatch = QPushButton()
        self.swatch.setObjectName("swatchButton")
        self.swatch.setToolTip("Channel color — click to change")
        self.swatch.clicked.connect(self._pick_color)
        row.addWidget(self.swatch)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Kick / Snare / Vox…")
        self.name_edit.setFixedWidth(92)
        self.name_edit.setToolTip("Channel name — label it your way")
        self.name_edit.editingFinished.connect(self._emit_changed)
        row.addWidget(self.name_edit)

        self.device_btn = QPushButton("Choose…")
        self.device_btn.setObjectName("compactButton")
        self.device_btn.setFixedWidth(140)
        self.device_btn.setToolTip("Select input device (Dante channel, etc.)")
        self.device_btn.clicked.connect(self._pick_input)
        row.addWidget(self.device_btn)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Σ", "sum")
        self.mode_combo.addItem("L", "left")
        self.mode_combo.addItem("R", "right")
        self.mode_combo.setFixedWidth(52)
        self.mode_combo.setToolTip("Which channel of the input: Left / Right / Sum")
        self.mode_combo.currentIndexChanged.connect(self._emit_changed)
        row.addWidget(self.mode_combo)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(-12.0, 36.0)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setSingleStep(1.0)
        self.gain_spin.setFixedWidth(84)
        self.gain_spin.setToolTip("Input gain")
        self.gain_spin.valueChanged.connect(self._emit_changed)
        row.addWidget(self.gain_spin)

        self.freq_lo = QSpinBox()
        self.freq_lo.setRange(20, 20000)
        self.freq_lo.setSingleStep(10)
        self.freq_lo.setFixedWidth(84)
        self.freq_lo.setToolTip("High-pass cutoff (Hz) — 24 dB/oct, cuts everything below")
        self.freq_lo.valueChanged.connect(self._on_freq_edited)
        self.freq_hi = QSpinBox()
        self.freq_hi.setRange(25, 20000)
        self.freq_hi.setSingleStep(10)
        self.freq_hi.setFixedWidth(84)
        self.freq_hi.setToolTip("Low-pass cutoff (Hz) — 24 dB/oct, cuts everything above")
        self.freq_hi.valueChanged.connect(self._on_freq_edited)
        row.addWidget(self.freq_lo)
        dash = QLabel("–")
        dash.setStyleSheet("color:#6d7588;")
        row.addWidget(dash)
        row.addWidget(self.freq_hi)

        self.preset_combo = QComboBox()
        for label, lo, hi in FREQ_PRESETS:
            self.preset_combo.addItem(label, (lo, hi))
        self.preset_combo.addItem("Custom", None)
        self.preset_combo.setFixedWidth(120)
        self.preset_combo.setToolTip("Frequency range preset")
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        row.addWidget(self.preset_combo)

        self.eq_btn = QPushButton("EQ")
        self.eq_btn.setObjectName("eqButton")
        self.eq_btn.setFixedSize(34, 22)
        self.eq_btn.setToolTip("Open the EQ editor — high/low pass, shelves, and gate")
        self.eq_btn.clicked.connect(self._open_eq)
        row.addWidget(self.eq_btn)
        self._eq_dialog: EqDialog | None = None

        self.meter = MiniLevelMeter(self.cfg.color)
        row.addWidget(self.meter)

        self.mute_btn = QPushButton("M")
        self.mute_btn.setObjectName("muteButton")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip("Mute — removes channel from waveform, triggers, and BPM")
        self.mute_btn.clicked.connect(self._emit_changed)
        row.addWidget(self.mute_btn)

        self.solo_btn = QPushButton("S")
        self.solo_btn.setObjectName("soloButton")
        self.solo_btn.setCheckable(True)
        self.solo_btn.setToolTip("Solo — only soloed channels are read and drawn")
        self.solo_btn.clicked.connect(self._emit_changed)
        row.addWidget(self.solo_btn)

        self.monitor_btn = QPushButton("🎧")
        self.monitor_btn.setObjectName("monitorButton")
        self.monitor_btn.setCheckable(True)
        self.monitor_btn.setToolTip(
            "Listen — send this channel (filtered) to the monitor output.\n"
            "Uses the app default speakers (Settings), or a per-track override.\n"
            "Independent of mute/solo."
        )
        self.monitor_btn.clicked.connect(self._emit_changed)
        row.addWidget(self.monitor_btn)

        self.trig_dot = QLabel("●")
        self.trig_dot.setStyleSheet("color:#333; font-size:14px;")
        self.trig_dot.setToolTip("Flashes when a trigger on this channel fires")
        row.addWidget(self.trig_dot)

        self.expander = QToolButton()
        self.expander.setArrowType(Qt.RightArrow)
        self.expander.setCheckable(True)
        self.expander.setToolTip("Show this channel's EQ, gate, and triggers")
        self.expander.toggled.connect(self._toggle_triggers)
        row.addWidget(self.expander)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setObjectName("compactButton")
        self.remove_btn.setFixedWidth(24)
        self.remove_btn.setToolTip("Remove channel")
        self.remove_btn.clicked.connect(self._on_remove)
        row.addWidget(self.remove_btn)

        outer.addLayout(row)

        # Expandable EQ / gate / trigger editor
        self.triggers_panel = QWidget()
        panel = QVBoxLayout(self.triggers_panel)
        panel.setContentsMargins(28, 2, 4, 2)
        panel.setSpacing(4)

        self.triggers_host = QVBoxLayout()
        self.triggers_host.setContentsMargins(0, 0, 0, 0)
        self.triggers_host.setSpacing(4)
        panel.addLayout(self.triggers_host)
        add_btn = QPushButton("+ Add trigger")
        add_btn.setObjectName("compactButton")
        add_btn.setFixedWidth(110)
        add_btn.clicked.connect(self._add_trigger)
        panel.addWidget(add_btn)
        self.triggers_panel.setVisible(False)
        outer.addWidget(self.triggers_panel)

    # ------------------------------------------------------------- interactions

    def _on_beat_radio(self) -> None:
        self.beat_radio.setChecked(True)
        self.beat_selected.emit(self.cfg.id)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.cfg.color), self, "Channel color")
        if not color.isValid():
            return
        self.cfg.color = color.name()
        self._apply_swatch()
        self.meter.set_color(self.cfg.color)
        for row in self._trigger_rows:
            row.set_color(self.cfg.color)
        self._emit_changed()

    def _pick_input(self) -> None:
        devices = self._devices or list_input_devices()
        dlg = DevicePickerDialog(
            devices,
            self.cfg.device_name,
            title=f"Input for {self.cfg.name}",
            parent=self,
        )
        if dlg.exec() != DevicePickerDialog.Accepted:
            return
        device = dlg.selected_device()
        if device is None:
            return
        self.cfg.device_name = device.name
        self.cfg.device_index = device.index
        self._update_device_btn()
        self.rebind_needed.emit()

    def _on_preset(self) -> None:
        data = self.preset_combo.currentData()
        if data is None:
            return
        lo, hi = data
        self.freq_lo.blockSignals(True)
        self.freq_hi.blockSignals(True)
        self.freq_lo.setValue(int(lo))
        self.freq_hi.setValue(int(hi))
        self.freq_lo.blockSignals(False)
        self.freq_hi.blockSignals(False)
        self._emit_changed()

    def _on_freq_edited(self) -> None:
        # Manual edit → select Custom unless values match a preset
        lo, hi = self.freq_lo.value(), self.freq_hi.value()
        match = None
        for i in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(i)
            if data and int(data[0]) == lo and int(data[1]) == hi:
                match = i
                break
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(match if match is not None else self.preset_combo.count() - 1)
        self.preset_combo.blockSignals(False)
        self._emit_changed()

    def _toggle_triggers(self, on: bool) -> None:
        self.expander.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.triggers_panel.setVisible(on)

    # ------------------------------------------------------------- EQ dialog

    def _open_eq(self) -> None:
        if self._eq_dialog is None:
            self._eq_dialog = EqDialog(self.cfg, self)
            self._eq_dialog.eq_changed.connect(self._on_eq_changed)
        else:
            self._eq_dialog.refresh_from_config()
        self._eq_dialog.set_channel_name(self.cfg.name)
        self._eq_dialog.show()
        self._eq_dialog.raise_()
        self._eq_dialog.activateWindow()

    def _on_eq_changed(self) -> None:
        # The dialog writes to cfg directly — mirror into the row's freq spins
        # so apply_to_config doesn't overwrite the new values later.
        self.freq_lo.blockSignals(True)
        self.freq_hi.blockSignals(True)
        self.freq_lo.setValue(int(self.cfg.freq_low_hz))
        self.freq_hi.setValue(int(self.cfg.freq_high_hz))
        self.freq_lo.blockSignals(False)
        self.freq_hi.blockSignals(False)
        self._sync_preset_combo()
        self._update_eq_btn()
        self._refresh_dim_state()
        self.changed.emit()

    def _sync_preset_combo(self) -> None:
        lo, hi = int(self.cfg.freq_low_hz), int(self.cfg.freq_high_hz)
        match = None
        for i in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(i)
            if data and int(data[0]) == lo and int(data[1]) == hi:
                match = i
                break
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(match if match is not None else self.preset_combo.count() - 1)
        self.preset_combo.blockSignals(False)

    def _update_eq_btn(self) -> None:
        active = (
            self.cfg.freq_low_hz > 25
            or self.cfg.freq_high_hz < 19000
            or abs(self.cfg.low_shelf_db) >= 0.25
            or abs(self.cfg.high_shelf_db) >= 0.25
            or self.cfg.gate_enabled
        )
        self.eq_btn.setStyleSheet(
            f"QPushButton#eqButton {{ color: {self.cfg.color}; border: 1px solid {self.cfg.color}; }}"
            if active
            else ""
        )

    def _on_remove(self) -> None:
        if any(t.osc_command.strip() for t in self.cfg.triggers):
            reply = QMessageBox.question(
                self,
                "Remove channel",
                f"'{self.cfg.name}' has triggers configured. Remove anyway?",
            )
            if reply != QMessageBox.Yes:
                return
        self.remove_requested.emit(self.cfg.id)

    # ------------------------------------------------------------- triggers

    def _clear_trigger_rows(self) -> None:
        while self._trigger_rows:
            row = self._trigger_rows.pop()
            self.triggers_host.removeWidget(row)
            row.deleteLater()

    def _rebuild_trigger_rows(self) -> None:
        self._clear_trigger_rows()
        for trig in self.cfg.triggers:
            self._add_trigger_row(trig)

    def _add_trigger(self) -> None:
        trig = ChannelTriggerConfig()
        self.cfg.triggers.append(trig)
        self._add_trigger_row(trig)
        if not self.expander.isChecked():
            self.expander.setChecked(True)
        self._emit_changed()

    def _add_trigger_row(self, trig: ChannelTriggerConfig) -> None:
        row = ChannelTriggerRow(trig, self.cfg.color)
        row.changed.connect(self._emit_changed)
        row.remove_requested.connect(self._remove_trigger)
        self._trigger_rows.append(row)
        self.triggers_host.addWidget(row)

    def _remove_trigger(self, trigger_id: str) -> None:
        self.cfg.triggers = [t for t in self.cfg.triggers if t.id != trigger_id]
        self._rebuild_trigger_rows()
        self._emit_changed()

    # ------------------------------------------------------------- config sync

    def _apply_swatch(self) -> None:
        self.swatch.setStyleSheet(
            f"QPushButton#swatchButton {{ background-color: {self.cfg.color}; }}"
        )

    def _update_device_btn(self) -> None:
        name = self.cfg.device_name
        if name:
            short = name if len(name) <= 18 else name[:16] + "…"
            self.device_btn.setText(short)
            self.device_btn.setToolTip(name)
        else:
            self.device_btn.setText("Choose…")
            self.device_btn.setToolTip("Select input device (Dante channel, etc.)")

    def refresh_devices(self, devices: list[InputDeviceInfo]) -> None:
        self._devices = devices

    def load_from_config(self) -> None:
        widgets = [
            self.name_edit, self.mode_combo, self.gain_spin,
            self.freq_lo, self.freq_hi, self.preset_combo,
            self.mute_btn, self.solo_btn, self.monitor_btn, self.beat_radio,
        ]
        for w in widgets:
            w.blockSignals(True)
        self.name_edit.setText(self.cfg.name)
        idx = self.mode_combo.findData(self.cfg.channel_mode)
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.gain_spin.setValue(self.cfg.gain_db)
        self.freq_lo.setValue(int(self.cfg.freq_low_hz))
        self.freq_hi.setValue(int(self.cfg.freq_high_hz))
        match = None
        for i in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(i)
            if data and int(data[0]) == int(self.cfg.freq_low_hz) and int(data[1]) == int(self.cfg.freq_high_hz):
                match = i
                break
        self.preset_combo.setCurrentIndex(match if match is not None else self.preset_combo.count() - 1)
        self.mute_btn.setChecked(self.cfg.mute)
        self.solo_btn.setChecked(self.cfg.solo)
        self.monitor_btn.setChecked(self.cfg.monitor)
        self.beat_radio.setChecked(self.cfg.id == self.track_cfg.beat_channel_id)
        if self._eq_dialog is not None:
            self._eq_dialog.refresh_from_config()
        self._update_eq_btn()
        for w in widgets:
            w.blockSignals(False)
        self._apply_swatch()
        self.meter.set_color(self.cfg.color)
        self._update_device_btn()
        self._rebuild_trigger_rows()
        self._refresh_dim_state()

    def apply_to_config(self) -> ChannelConfig:
        self.cfg.name = self.name_edit.text().strip() or "Ch"
        self.cfg.channel_mode = self.mode_combo.currentData() or "sum"
        self.cfg.gain_db = float(self.gain_spin.value())
        self.cfg.freq_low_hz = float(self.freq_lo.value())
        self.cfg.freq_high_hz = float(max(self.freq_hi.value(), self.freq_lo.value() + 5))
        self.cfg.mute = self.mute_btn.isChecked()
        self.cfg.solo = self.solo_btn.isChecked()
        self.cfg.monitor = self.monitor_btn.isChecked()
        # Headphone on implies the track's monitor master is on — otherwise 🎧
        # lights up and nothing plays (easy to miss buried in track Settings).
        if self.cfg.monitor and not self.track_cfg.monitor_enabled:
            self.track_cfg.monitor_enabled = True
        for row in self._trigger_rows:
            row.apply()
        return self.cfg

    def set_beat_checked(self, checked: bool) -> None:
        self.beat_radio.blockSignals(True)
        self.beat_radio.setChecked(checked)
        self.beat_radio.blockSignals(False)

    def _refresh_dim_state(self) -> None:
        self._opacity.setOpacity(0.45 if self.cfg.mute else 1.0)
        self.setProperty("muted", self.cfg.mute)
        self.setProperty("soloed", self.cfg.solo)
        self.style().unpolish(self)
        self.style().polish(self)

    def _emit_changed(self, *_args) -> None:
        self.apply_to_config()
        self._refresh_dim_state()
        self._update_eq_btn()
        if self._eq_dialog is not None and self._eq_dialog.isVisible():
            self._eq_dialog.refresh_from_config()
        self.changed.emit()

    # ------------------------------------------------------------- live updates

    def update_live(self, level: float, trigger_flashes: dict[str, float] | None) -> None:
        self.meter.set_level(level, dimmed=self.cfg.mute)
        peak = 0.0
        if trigger_flashes:
            for row in self._trigger_rows:
                amt = trigger_flashes.get(row.trigger.id, 0.0)
                row.set_flash(amt)
                peak = max(peak, amt)
        if peak > 0.3:
            self.trig_dot.setStyleSheet(f"color:{self.cfg.color}; font-size:14px;")
        else:
            self.trig_dot.setStyleSheet("color:#333; font-size:14px;")


class TrackCard(QFrame):
    changed = Signal(str)  # track_id — live-tunable change
    remove_requested = Signal(str)
    tap_requested = Signal(str)
    apply_tap_requested = Signal(str)
    clear_tap_requested = Signal(str)
    multiply_requested = Signal(str, float)
    resync_requested = Signal(str)
    rebind_requested = Signal(str)

    def __init__(self, runtime: TrackRuntime, devices: list[InputDeviceInfo], parent=None):
        super().__init__(parent)
        self.setObjectName("laneCard")
        self.runtime = runtime
        self._devices = devices
        self._outputs = list_output_devices()
        self._expanded = False
        self._channel_rows: dict[str, ChannelRow] = {}
        self._env_curves: dict[str, pg.PlotDataItem] = {}
        self._legend_chips: list[QPushButton] = []
        self._highlight_until: dict[str, float] = {}
        # Slow-decay display references — old traces don't rescale as peaks scroll out
        self._env_scale: dict[str, float] = {}
        self._wave_scale: float = 1e-6
        self._build()
        self.load_from_config()

    @property
    def track_id(self) -> str:
        return self.runtime.config.id

    # ---------------------------------------------------------------- build

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ---- header: accent | name | On | ... BPM + chips ... | summary | gear | remove
        header = QHBoxLayout()
        header.setSpacing(10)

        self.accent_bar = QFrame()
        self.accent_bar.setFixedWidth(4)
        self.accent_bar.setMinimumHeight(40)
        header.addWidget(self.accent_bar)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Track name")
        self.name_edit.setMaximumWidth(180)
        self.name_edit.setToolTip("Track name — label it your way")
        self.name_edit.editingFinished.connect(self._on_edited)
        name_col.addWidget(self.name_edit)
        self.enabled_check = QCheckBox("On")
        self.enabled_check.stateChanged.connect(self._on_edited)
        name_col.addWidget(self.enabled_check)
        header.addLayout(name_col)

        header.addStretch(1)

        self.beat_dot = QLabel("●")
        self.beat_dot.setStyleSheet("color: #333; font-size: 18px;")
        header.addWidget(self.beat_dot)
        self.bpm_label = QLabel("--.-")
        self.bpm_label.setObjectName("bpmValue")
        self.bpm_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self.bpm_label)

        chips_col = QVBoxLayout()
        chips_col.setSpacing(3)
        self.lock_chip = QLabel("NO SIGNAL")
        self.lock_chip.setAlignment(Qt.AlignCenter)
        self.lock_chip.setStyleSheet(
            f"background:#1a1c22; color:{LOCK_GRAY}; border-radius:8px; padding:2px 8px; font-size:11px; font-weight:600;"
        )
        chips_col.addWidget(self.lock_chip)
        self.tap_chip = QLabel("TAP")
        self.tap_chip.setAlignment(Qt.AlignCenter)
        self.tap_chip.setToolTip("Manual tap override active — Apply is lit; untoggle to return to live BPM")
        self.tap_chip.setStyleSheet(
            f"background:#2e2a1a; color:{LOCK_AMBER}; border-radius:8px; padding:2px 8px; font-size:11px; font-weight:700;"
        )
        self.tap_chip.setVisible(False)
        chips_col.addWidget(self.tap_chip)
        header.addLayout(chips_col)

        header.addStretch(1)

        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        self.summary = QLabel("")
        self.summary.setObjectName("triggerSummary")
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(150)
        self.summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right_col.addWidget(self.summary)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch(1)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("accentButton")
        self.settings_btn.clicked.connect(self.toggle_settings)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.track_id))
        btn_row.addWidget(self.settings_btn)
        btn_row.addWidget(self.remove_btn)
        right_col.addLayout(btn_row)
        header.addLayout(right_col)

        root.addLayout(header)

        # ---- second row: level + tap cluster
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        self.level_label = QLabel("Level —")
        self.level_label.setStyleSheet("color:#6d7588; font-size:11px;")
        quick_row.addWidget(self.level_label)
        quick_row.addStretch(1)

        self.tap_btn = QPushButton("Tap")
        self.tap_btn.setCheckable(True)
        self.tap_btn.setToolTip(
            "Click to the beat — 4 taps sets and locks the BPM automatically.\n"
            "Keep tapping to refine it; toggle Apply off to return to live BPM."
        )
        self.tap_btn.clicked.connect(self._on_tap_clicked)
        self.apply_tap_btn = QPushButton("Apply")
        self.apply_tap_btn.setCheckable(True)
        self.apply_tap_btn.setToolTip(
            "Toggle ON: lock tapped BPM (overwrites waveform tempo).\n"
            "Toggle OFF: return to live waveform BPM."
        )
        self.apply_tap_btn.clicked.connect(self._on_apply_toggled)
        self.half_btn = QPushButton("½")
        self.half_btn.setToolTip("Halve tempo (highlights while half-time is active)")
        self.half_btn.clicked.connect(lambda: self.multiply_requested.emit(self.track_id, 0.5))
        self.dbl_btn = QPushButton("×2")
        self.dbl_btn.setToolTip("Double tempo (highlights while double-time is active)")
        self.dbl_btn.clicked.connect(lambda: self.multiply_requested.emit(self.track_id, 2.0))
        for b, w in (
            (self.tap_btn, 72),
            (self.apply_tap_btn, 56),
            (self.half_btn, 36),
            (self.dbl_btn, 40),
        ):
            b.setObjectName("compactButton")
            b.setFixedWidth(w)
            b.setFixedHeight(28)
            quick_row.addWidget(b)
        root.addLayout(quick_row)
        self._refresh_multiplier_buttons()

        # ---- plot
        pg.setConfigOptions(antialias=True, background="#0e1015", foreground="#9aa3b5")
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(120)
        self.plot.setMaximumHeight(260)
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plot.hideAxis("left")
        self.plot.showAxis("bottom")
        bottom_axis = self.plot.getAxis("bottom")
        bottom_axis.setPen(pg.mkPen("#3a4150"))
        bottom_axis.setTextPen(pg.mkPen("#6d7588"))
        bottom_axis.setStyle(tickTextOffset=2, tickLength=-4)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)
        try:
            self.plot.hideButtons()
            pi = self.plot.getPlotItem()
            pi.hideButtons()
            if hasattr(pi, "autoBtn") and pi.autoBtn is not None:
                pi.autoBtn.hide()
                pi.autoBtn.setParent(None)
        except Exception:
            pass
        # Raw wave of the beat channel as dim background; channel envelopes on top
        self.curve = self.plot.plot(pen=pg.mkPen("#9aa3b533", width=0.9))
        # Reusable pool of beat-grid lines: repositioning items is far cheaper
        # (and flicker-free) vs removing + re-adding them every frame.
        self._grid_pool: list[pg.InfiniteLine] = []
        # X domain is fixed 0..1 (0 = oldest, 1 = now) so nothing stretches
        # when the data array lengths vary frame to frame.
        self.plot.setXRange(0.0, 1.0, padding=0)
        self.plot.setYRange(-0.6, 1.05)
        self.plot.installEventFilter(self)
        self.plot.viewport().installEventFilter(self)

        self.empty_hint = QLabel("Choose an input to start", self.plot)
        self.empty_hint.setStyleSheet("color:#5c6478; font-size:13px; background:transparent;")
        self.empty_hint.move(16, 12)
        self.empty_hint.setVisible(False)

        root.addWidget(self.plot)

        # ---- zoom + legend
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setObjectName("compactButton")
        self.zoom_out_btn.setFixedSize(28, 26)
        self.zoom_out_btn.setToolTip("Zoom out — longer history, slower scroll")
        self.zoom_out_btn.clicked.connect(lambda: self._nudge_zoom(1.25))
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setObjectName("compactButton")
        self.zoom_in_btn.setFixedSize(28, 26)
        self.zoom_in_btn.setToolTip("Zoom in — shorter history, faster scroll")
        self.zoom_in_btn.clicked.connect(lambda: self._nudge_zoom(1 / 1.25))
        self.zoom_label = QLabel("3.0s")
        self.zoom_label.setStyleSheet("color:#9aa3b5; font-size:11px; min-width:36px;")
        self.grid_hint = QLabel("│ beat   ║ bar")
        self.grid_hint.setStyleSheet("color:#5c6478; font-size:10px;")
        zoom_row.addWidget(self.zoom_out_btn)
        zoom_row.addWidget(self.zoom_in_btn)
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addWidget(self.grid_hint)
        zoom_row.addStretch(1)
        self.legend_host = QHBoxLayout()
        self.legend_host.setSpacing(10)
        zoom_row.addLayout(self.legend_host)
        root.addLayout(zoom_row)

        # ---- channel rows
        self.channels_host = QVBoxLayout()
        self.channels_host.setSpacing(5)
        root.addLayout(self.channels_host)

        add_ch_row = QHBoxLayout()
        self.add_channel_btn = QPushButton("+ Add channel")
        self.add_channel_btn.setObjectName("compactButton")
        self.add_channel_btn.setFixedWidth(120)
        self.add_channel_btn.clicked.connect(self._add_channel)
        add_ch_row.addWidget(self.add_channel_btn)
        add_ch_row.addStretch(1)
        root.addLayout(add_ch_row)

        # ---- expandable track settings
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

        avg_row = QHBoxLayout()
        self.bpm_average = QSpinBox()
        self.bpm_average.setRange(1, 16)
        self.bpm_average.setToolTip("Median of the last N BPM readings before sending (1 = off)")
        self.bpm_average.valueChanged.connect(self._on_edited)
        self.bpm_send_delta = QDoubleSpinBox()
        self.bpm_send_delta.setRange(0.1, 20.0)
        self.bpm_send_delta.setSingleStep(0.25)
        self.bpm_send_delta.setSuffix(" BPM")
        self.bpm_send_delta.setToolTip("Only send OSC when smoothed BPM moves by at least this much")
        self.bpm_send_delta.valueChanged.connect(self._on_edited)
        self.bpm_send_interval = QSpinBox()
        self.bpm_send_interval.setRange(0, 5000)
        self.bpm_send_interval.setSingleStep(50)
        self.bpm_send_interval.setSuffix(" ms")
        self.bpm_send_interval.setToolTip("Minimum time between BPM OSC sends (0 = no time limit)")
        self.bpm_send_interval.valueChanged.connect(self._on_edited)
        avg_row.addWidget(QLabel("Avg"))
        avg_row.addWidget(self.bpm_average)
        avg_row.addWidget(QLabel("Δ"))
        avg_row.addWidget(self.bpm_send_delta)
        avg_row.addWidget(QLabel("Every"))
        avg_row.addWidget(self.bpm_send_interval)
        form.addRow("BPM send", avg_row)

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

        mon_box = QHBoxLayout()
        self.monitor_enabled = QCheckBox("Monitor")
        self.monitor_enabled.setToolTip(
            "Master switch for this track's monitor output.\n"
            "Turns on automatically when you press a channel's 🎧 button.\n"
            "Use each channel's 🎧 to pick what you hear."
        )
        self.monitor_enabled.stateChanged.connect(self._on_edited)
        self.monitor_gain = QDoubleSpinBox()
        self.monitor_gain.setRange(-24.0, 24.0)
        self.monitor_gain.setSuffix(" dB")
        self.monitor_gain.valueChanged.connect(self._on_edited)
        self.monitor_device_btn = QPushButton("Output…")
        self.monitor_device_btn.clicked.connect(self._pick_monitor)
        self.monitor_device_label = QLabel("App default")
        self.monitor_device_label.setStyleSheet("color:#6d7588; font-size:11px;")
        self.monitor_clear_btn = QPushButton("Use default")
        self.monitor_clear_btn.setObjectName("compactButton")
        self.monitor_clear_btn.setToolTip("Clear override — use the app's default monitor output (Settings)")
        self.monitor_clear_btn.clicked.connect(self._clear_monitor_device)
        mon_box.addWidget(self.monitor_enabled)
        mon_box.addWidget(self.monitor_gain)
        mon_box.addWidget(self.monitor_device_btn)
        mon_box.addWidget(self.monitor_device_label, stretch=1)
        mon_box.addWidget(self.monitor_clear_btn)
        form.addRow("Listen", mon_box)

        resync_row = QHBoxLayout()
        resync_btn = QPushButton("Resync")
        resync_btn.setObjectName("accentButton")
        resync_btn.clicked.connect(lambda: self.resync_requested.emit(self.track_id))
        resync_row.addWidget(resync_btn)
        resync_row.addStretch(1)
        form.addRow("Manual", resync_row)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_settings_btn = QPushButton("Close settings")
        close_settings_btn.setObjectName("accentButton")
        close_settings_btn.clicked.connect(self.toggle_settings)
        close_row.addWidget(close_settings_btn)
        form.addRow("", close_row)

        self.settings_panel.setVisible(False)
        root.addWidget(self.settings_panel)

    # ---------------------------------------------------------------- channels

    def _rebuild_channel_rows(self) -> None:
        for row in list(self._channel_rows.values()):
            self.channels_host.removeWidget(row)
            row.deleteLater()
        self._channel_rows.clear()
        for ch_cfg in self.runtime.config.channels:
            row = ChannelRow(self.runtime.config, ch_cfg, self._devices)
            row.changed.connect(self._on_edited)
            row.rebind_needed.connect(self._on_channel_rebind)
            row.remove_requested.connect(self._remove_channel)
            row.beat_selected.connect(self._on_beat_selected)
            self._channel_rows[ch_cfg.id] = row
            self.channels_host.addWidget(row)
        self._rebuild_env_curves()
        self._rebuild_legend()
        self._refresh_accent()

    def _rebuild_env_curves(self) -> None:
        for curve in self._env_curves.values():
            self.plot.removeItem(curve)
        self._env_curves.clear()
        for ch_cfg in self.runtime.config.channels:
            curve = self.plot.plot(
                pen=pg.mkPen(ch_cfg.color, width=2.0),
                fillLevel=0.0,
                brush=pg.mkBrush(QColor(ch_cfg.color).red(), QColor(ch_cfg.color).green(), QColor(ch_cfg.color).blue(), 40),
            )
            curve.setZValue(10)
            self._env_curves[ch_cfg.id] = curve
        self.curve.setZValue(0)

    def _rebuild_legend(self) -> None:
        while self.legend_host.count():
            item = self.legend_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._legend_chips = []
        for ch_cfg in self.runtime.config.channels:
            chip = QPushButton(f"● {ch_cfg.name}")
            chip.setFlat(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(
                f"QPushButton {{ color:{ch_cfg.color}; font-size:11px; font-weight:600;"
                f" background:transparent; border:none; padding:0; }}"
            )
            lo, hi = int(ch_cfg.freq_low_hz), int(ch_cfg.freq_high_hz)
            chip.setToolTip(f"{ch_cfg.name} · {lo}–{hi} Hz · click to highlight")
            chip.clicked.connect(lambda _=False, cid=ch_cfg.id: self._flash_curve(cid))
            self.legend_host.addWidget(chip)
            self._legend_chips.append(chip)

    def _flash_curve(self, channel_id: str) -> None:
        self._highlight_until[channel_id] = time.monotonic() + 1.2

    def _refresh_accent(self) -> None:
        color = (
            self.runtime.config.channels[0].color
            if self.runtime.config.channels
            else ACCENT
        )
        self.accent_bar.setStyleSheet(f"background-color:{color}; border-radius:2px;")

    def _add_channel(self) -> None:
        ch = new_channel(self.runtime.config)
        self.runtime.config.channels.append(ch)
        self._rebuild_channel_rows()
        self.rebind_requested.emit(self.track_id)

    def _remove_channel(self, channel_id: str) -> None:
        cfg = self.runtime.config
        if len(cfg.channels) <= 1:
            QMessageBox.information(self, "GRate", "A track needs at least one channel.")
            return
        cfg.channels = [c for c in cfg.channels if c.id != channel_id]
        if cfg.beat_channel_id == channel_id:
            cfg.beat_channel_id = cfg.channels[0].id
        self._rebuild_channel_rows()
        self.rebind_requested.emit(self.track_id)

    def _on_beat_selected(self, channel_id: str) -> None:
        self.runtime.config.beat_channel_id = channel_id
        for ch_id, row in self._channel_rows.items():
            row.set_beat_checked(ch_id == channel_id)
        self.rebind_requested.emit(self.track_id)

    def _on_channel_rebind(self) -> None:
        self.rebind_requested.emit(self.track_id)

    # ---------------------------------------------------------------- settings / devices

    def toggle_settings(self) -> None:
        self._expanded = not self._expanded
        self.settings_panel.setVisible(self._expanded)
        self.settings_btn.setText("Hide" if self._expanded else "Settings")

    def refresh_devices(self, devices: list[InputDeviceInfo]) -> None:
        self._devices = devices
        self._outputs = list_output_devices()
        for row in self._channel_rows.values():
            row.refresh_devices(devices)

    def _pick_monitor(self) -> None:
        devices = self._outputs or list_output_devices()
        dlg = DevicePickerDialog(
            devices,
            self.runtime.config.monitor_device_name,
            title="Monitor output for this track",
            parent=self,
        )
        if dlg.exec() != DevicePickerDialog.Accepted:
            return
        device = dlg.selected_device()
        if device is None:
            return
        self.runtime.config.monitor_device_name = device.name
        self.runtime.config.monitor_device_index = device.index
        self._update_monitor_label()
        self._on_edited()

    def _clear_monitor_device(self) -> None:
        self.runtime.config.monitor_device_name = ""
        self.runtime.config.monitor_device_index = None
        self._update_monitor_label()
        self._on_edited()

    def _update_monitor_label(self) -> None:
        name = self.runtime.config.monitor_device_name
        self.monitor_device_label.setText(name if name else "App default")

    # ---------------------------------------------------------------- config sync

    def load_from_config(self) -> None:
        cfg = self.runtime.config
        widgets = [
            self.name_edit, self.enabled_check,
            self.speed_master, self.send_bpm, self.bpm_average, self.bpm_send_delta,
            self.bpm_send_interval, self.beat_command,
            self.beat_divider, self.resync_command, self.midi_enabled, self.midi_channel,
            self.midi_note, self.bpm_min, self.bpm_max, self.beat_offset,
            self.monitor_enabled, self.monitor_gain,
        ]
        for w in widgets:
            w.blockSignals(True)

        self.name_edit.setText(cfg.name)
        self.enabled_check.setChecked(cfg.enabled)
        self.zoom_label.setText(f"{float(cfg.wave_window_seconds or 3.0):g}s")
        self._refresh_multiplier_buttons()
        self.speed_master.setText(cfg.trigger.speed_master)
        self.send_bpm.setChecked(cfg.trigger.send_bpm)
        self.bpm_average.setValue(max(1, int(cfg.trigger.bpm_average_count)))
        self.bpm_send_delta.setValue(float(cfg.trigger.bpm_send_delta))
        self.bpm_send_interval.setValue(max(0, int(cfg.trigger.bpm_send_interval_ms)))
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
        self.monitor_gain.setValue(cfg.monitor_gain_db)
        self._update_monitor_label()
        self._rebuild_channel_rows()
        self._update_summary()

        for w in widgets:
            w.blockSignals(False)

    def apply_to_config(self) -> TrackConfig:
        cfg = self.runtime.config
        cfg.name = self.name_edit.text().strip() or "Track"
        cfg.enabled = self.enabled_check.isChecked()
        cfg.trigger.speed_master = self.speed_master.text().strip() or "3.1"
        cfg.trigger.send_bpm = self.send_bpm.isChecked()
        cfg.trigger.bpm_average_count = int(self.bpm_average.value())
        cfg.trigger.bpm_send_delta = float(self.bpm_send_delta.value())
        cfg.trigger.bpm_send_interval_ms = int(self.bpm_send_interval.value())
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
        cfg.monitor_gain_db = float(self.monitor_gain.value())
        for row in self._channel_rows.values():
            row.apply_to_config()
        self._update_summary()
        return cfg

    def _update_summary(self) -> None:
        cfg = self.runtime.config
        parts = []
        if cfg.trigger.send_bpm:
            parts.append(f"SM {cfg.trigger.speed_master}")
        beat_ch = cfg.beat_channel()
        if beat_ch is not None and len(cfg.channels) > 1:
            parts.append(f"beat:{beat_ch.name}")
        if cfg.trigger.beat_command:
            parts.append(f"{cfg.trigger.beat_command} /{cfg.trigger.beat_divider}")
        n_trigs = sum(
            1
            for ch in cfg.channels
            for t in ch.triggers
            if t.enabled and (t.osc_command or t.midi_enabled)
        )
        if n_trigs:
            parts.append(f"{n_trigs} trig")
        if any(c.monitor for c in cfg.channels):
            if cfg.monitor_enabled:
                parts.append("mon")
            else:
                parts.append("mon off")
        err = (self.runtime.last_error or "").strip()
        if err and "onitor" in err.lower():
            parts.append(f"⚠ {err}")
        if cfg.trigger.midi_enabled:
            parts.append(f"MIDI ch{cfg.trigger.midi_channel} n{cfg.trigger.midi_note}")
        self.summary.setText(" + ".join(parts) if parts else "No triggers assigned")

    def _on_edited(self, *_args) -> None:
        self.apply_to_config()
        # Keep the Settings checkbox in sync if a channel 🎧 auto-enabled Monitor
        if self.monitor_enabled.isChecked() != self.runtime.config.monitor_enabled:
            self.monitor_enabled.blockSignals(True)
            self.monitor_enabled.setChecked(self.runtime.config.monitor_enabled)
            self.monitor_enabled.blockSignals(False)
        self._refresh_accent()
        self._rebuild_legend()
        self.changed.emit(self.track_id)

    # ---------------------------------------------------------------- zoom / grid

    def _nudge_zoom(self, factor: float) -> None:
        cur = float(self.runtime.config.wave_window_seconds or 3.0)
        steps = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0]
        if factor > 1:
            candidates = [s for s in steps if s > cur + 0.01]
            new = candidates[0] if candidates else 16.0
        else:
            candidates = [s for s in steps if s < cur - 0.01]
            new = candidates[-1] if candidates else 1.0
        self.runtime.config.wave_window_seconds = float(new)
        self.zoom_label.setText(f"{new:g}s")
        self.changed.emit(self.track_id)

    def _refresh_multiplier_buttons(self) -> None:
        mult = float(self.runtime.config.tempo_multiplier or 1.0)
        half_on = mult < 0.99
        dbl_on = mult > 1.01
        self.half_btn.setProperty("selected", half_on)
        self.dbl_btn.setProperty("selected", dbl_on)
        for b in (self.half_btn, self.dbl_btn):
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_tap_clicked(self) -> None:
        # First click arms + records a tap; while armed, each click is a tap
        if not self.tap_btn.isChecked() and not self.runtime.tap_mode:
            self.tap_btn.setChecked(True)
        self.tap_requested.emit(self.track_id)

    def _on_apply_toggled(self) -> None:
        if self.apply_tap_btn.isChecked():
            self.apply_tap_requested.emit(self.track_id)
        else:
            self.clear_tap_requested.emit(self.track_id)

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Wheel and obj in (self.plot, self.plot.viewport()):
            wheel = event
            if isinstance(wheel, QWheelEvent):
                delta = wheel.angleDelta().y()
                if delta > 0:
                    self._nudge_zoom(1 / 1.25)  # zoom in
                elif delta < 0:
                    self._nudge_zoom(1.25)  # zoom out
                return True
        return super().eventFilter(obj, event)

    _GRID_BAR_PEN = None
    _GRID_BEAT_PEN = None

    def _update_beat_grid(
        self,
        has_wave: bool,
        bpm: float,
        *,
        sample_counter: int,
        sample_rate: float,
    ) -> None:
        """Beat/bar lines locked to the audio sample clock (scroll with the waveform).

        X coordinates are in the fixed 0..1 domain (1 = now). Lines come from a
        reusable pool — repositioned, never destroyed — to avoid per-frame
        scene churn (visible as flicker/tearing).
        """
        if TrackCard._GRID_BAR_PEN is None:
            TrackCard._GRID_BAR_PEN = pg.mkPen("#6d7588", width=1.3)
            TrackCard._GRID_BEAT_PEN = pg.mkPen("#2e3340", width=1.0)

        window = max(1.0, float(self.runtime.config.wave_window_seconds or 3.0))
        self.zoom_label.setText(f"{window:g}s")
        axis = self.plot.getAxis("bottom")

        samples_per_beat = sample_rate * 60.0 / bpm if bpm > 0 else 0.0
        if not has_wave or samples_per_beat < 8 or sample_rate <= 0:
            for line in self._grid_pool:
                line.hide()
            axis.setTicks([[(0.0, f"−{window:g}s"), (1.0, "now")]])
            return

        window_samples = max(1.0, sample_rate * window)
        # Phase in samples at the right edge ("now") — advances exactly with audio
        phase = float(sample_counter % int(samples_per_beat))
        ages: list[tuple[float, bool]] = []
        age = phase
        beat_index = 0
        while age <= window_samples:
            ages.append((age, (beat_index % 4) == 0))
            age += samples_per_beat
            beat_index += 1

        while len(self._grid_pool) < len(ages):
            line = pg.InfiniteLine(angle=90, pen=TrackCard._GRID_BEAT_PEN)
            line.setZValue(-5)
            self.plot.addItem(line)
            self._grid_pool.append(line)

        major = [(0.0, f"−{window:g}s"), (1.0, "now")]
        minor: list[tuple[float, str]] = []
        for i, line in enumerate(self._grid_pool):
            if i >= len(ages):
                line.hide()
                continue
            age_s, is_bar = ages[i]
            x = 1.0 - age_s / window_samples
            line.setPos(x)
            line.setPen(TrackCard._GRID_BAR_PEN if is_bar else TrackCard._GRID_BEAT_PEN)
            line.show()
            if age_s >= sample_rate * 0.05:
                (major if is_bar else minor).append((x, "║" if is_bar else "│"))
        axis.setTicks([major, minor])

    # ---------------------------------------------------------------- live updates

    def update_live(
        self,
        waveform: np.ndarray,
        level: float,
        bpm: float,
        flash: float,
        *,
        lock_status: str = "NO SIGNAL",
        envelopes: dict[str, np.ndarray] | None = None,
        channel_levels: dict[str, float] | None = None,
        trigger_flashes: dict[str, dict[str, float]] | None = None,
        sample_counter: int = 0,
        sample_rate: float = 48000.0,
        tap_mode: bool = False,
        tap_flash: float = 0.0,
        manual_sticky: bool = False,
    ) -> None:
        has_wave = False
        if waveform.size >= 4:
            has_wave = True
            # Peak (min/max) downsampling: each bin keeps its extremes, so the
            # trace preserves transients and doesn't shimmer as data scrolls
            # (plain slicing picks different samples every frame).
            bins = 300
            step = max(1, waveform.size // bins)
            n = (waveform.size // step) * step
            chunked = waveform[waveform.size - n :].reshape(-1, step)
            y = np.empty(chunked.shape[0] * 2, dtype=np.float32)
            y[0::2] = chunked.min(axis=1)
            y[1::2] = chunked.max(axis=1)
            # Slow-decay reference: jumps up instantly on louder audio, decays
            # slowly — so history doesn't visibly rescale as peaks scroll out
            peak = float(np.max(np.abs(y)))
            self._wave_scale = max(peak, self._wave_scale * 0.998, 0.05)
            y = (y / self._wave_scale) * 0.35
            self.curve.setData(np.linspace(0.0, 1.0, y.size), y)

        has_device = any(
            c.device_name or c.device_index is not None for c in self.runtime.config.channels
        )
        self.empty_hint.setVisible(not has_device)

        now = time.monotonic()
        if envelopes:
            for ch_id, hist in envelopes.items():
                curve = self._env_curves.get(ch_id)
                if curve is None:
                    continue
                if hist.size == 0:
                    curve.setData([])
                    continue
                env = hist.astype(np.float32)
                peak = float(np.max(env))
                scale = max(peak, self._env_scale.get(ch_id, 1e-6) * 0.998, 1e-4)
                self._env_scale[ch_id] = scale
                env = (env / scale) * 0.95
                # Envelopes are sample-clocked over the same window as the
                # waveform, so they map straight onto the shared 0..1 domain.
                curve.setData(np.linspace(0.0, 1.0, env.size), env)
                # Legend click → temporary highlight
                ch_cfg = next(
                    (c for c in self.runtime.config.channels if c.id == ch_id), None
                )
                if ch_cfg is not None:
                    width = 3.5 if self._highlight_until.get(ch_id, 0.0) > now else 2.0
                    curve.setPen(pg.mkPen(ch_cfg.color, width=width))

        self._update_beat_grid(
            has_wave,
            bpm,
            sample_counter=sample_counter,
            sample_rate=sample_rate,
        )
        self._refresh_multiplier_buttons()

        # ---- tap cluster state
        if self.tap_btn.isChecked() != tap_mode:
            self.tap_btn.blockSignals(True)
            self.tap_btn.setChecked(tap_mode)
            self.tap_btn.blockSignals(False)
        if self.apply_tap_btn.isChecked() != manual_sticky:
            self.apply_tap_btn.blockSignals(True)
            self.apply_tap_btn.setChecked(manual_sticky)
            self.apply_tap_btn.blockSignals(False)

        # Tap button shows the tapped BPM while arming / applied
        if (tap_mode or manual_sticky) and bpm > 0:
            self.tap_btn.setText(f"Tap {bpm:.0f}")
        else:
            self.tap_btn.setText("Tap")

        self.tap_btn.setProperty("selected", tap_mode or manual_sticky)
        self.apply_tap_btn.setProperty("selected", manual_sticky)
        # Flash: your taps while arming; steady metronome at the applied BPM once applied
        if manual_sticky and bpm > 0:
            metro_on = (now * bpm / 60.0) % 1.0 < 0.22
        else:
            metro_on = tap_flash > 0.35
        self.tap_btn.setProperty("metro", metro_on)
        for b in (self.tap_btn, self.apply_tap_btn):
            b.style().unpolish(b)
            b.style().polish(b)

        self.tap_chip.setVisible(manual_sticky)

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

        for ch_id, row in self._channel_rows.items():
            lvl = (channel_levels or {}).get(ch_id, 0.0)
            flashes = (trigger_flashes or {}).get(ch_id)
            row.update_live(lvl, flashes)

        # Surface monitor open failures without forcing a full summary rebuild every tick
        mon_err = "Monitor:" in (self.runtime.last_error or "")
        if mon_err != getattr(self, "_last_mon_err", False):
            self._last_mon_err = mon_err
            self._update_summary()
