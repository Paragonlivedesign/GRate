"""Main window: top bar, lane cards, global OSC/MIDI settings."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QByteArray
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from grate import __app_name__, __version__
from grate.audio.capture import list_input_devices
from grate.config import new_lane
from grate.core.engine import AppEngine
from grate.outputs.midi_out import list_midi_output_names
from grate.ui.lane_card import LaneCard
from grate.ui.theme import DARK_QSS


class SettingsDialog(QDialog):
    def __init__(self, engine: AppEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("GRate Settings")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        osc_form = QFormLayout()
        self.osc_enabled = QCheckBox("Enable OSC")
        self.osc_host = QLineEdit()
        self.osc_port = QSpinBox()
        self.osc_port.setRange(1, 65535)
        self.osc_prefix = QLineEdit()
        self.osc_prefix.setPlaceholderText("gma3/cmd")
        self.osc_prefix.setToolTip(
            "OSC command address. Use gma3/cmd (full path).\n"
            "On MA3, Prefix field is only gma3 (no slash) — MA3 forbids / in Prefix."
        )
        self.osc_delta = QDoubleSpinBox()
        self.osc_delta.setRange(0.01, 10.0)
        self.osc_delta.setSingleStep(0.05)
        self.test_osc_btn = QPushButton("Test OSC send")
        self.test_osc_btn.clicked.connect(self._test_osc)
        osc_form.addRow(self.osc_enabled)
        osc_form.addRow("Console IP", self.osc_host)
        osc_form.addRow("Port", self.osc_port)
        osc_form.addRow("OSC path", self.osc_prefix)
        osc_form.addRow("BPM send delta", self.osc_delta)
        osc_form.addRow("", self.test_osc_btn)
        layout.addWidget(QLabel("OSC → grandMA3"))
        layout.addLayout(osc_form)

        midi_form = QFormLayout()
        self.midi_enabled = QCheckBox("Enable MIDI")
        self.midi_port = QComboBox()
        self.midi_port.setEditable(True)
        self.clock_lane = QComboBox()
        midi_form.addRow(self.midi_enabled)
        midi_form.addRow("Output port", self.midi_port)
        midi_form.addRow("Clock source lane", self.clock_lane)
        layout.addWidget(QLabel("MIDI"))
        layout.addLayout(midi_form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()

    def _load(self) -> None:
        cfg = self.engine.config
        self.osc_enabled.setChecked(cfg.osc.enabled)
        self.osc_host.setText(cfg.osc.host)
        self.osc_port.setValue(cfg.osc.port)
        self.osc_prefix.setText(cfg.osc.prefix)
        self.osc_delta.setValue(cfg.osc.bpm_delta)

        self.midi_enabled.setChecked(cfg.midi.enabled)
        ports = list_midi_output_names()
        self.midi_port.clear()
        self.midi_port.addItems(ports)
        if cfg.midi.port_name:
            idx = self.midi_port.findText(cfg.midi.port_name)
            if idx >= 0:
                self.midi_port.setCurrentIndex(idx)
            else:
                self.midi_port.setEditText(cfg.midi.port_name)

        self.clock_lane.clear()
        self.clock_lane.addItem("(auto)", "")
        for lane in cfg.lanes:
            self.clock_lane.addItem(lane.name, lane.id)
        idx = self.clock_lane.findData(cfg.midi.clock_lane_id)
        self.clock_lane.setCurrentIndex(max(0, idx))

    def _test_osc(self) -> None:
        from grate.config import save_config

        self.apply()
        self.engine.apply_io_settings()
        save_config(self.engine.config)
        # Same command style as the manual terminal tests that reach MA3
        ok = self.engine.osc.test_send()
        addr = self.engine.osc.cmd_address()
        host = self.engine.config.osc.host
        port = self.engine.config.osc.port
        if ok:
            QMessageBox.information(
                self,
                "OSC",
                f"Sent 3 BPM tests to {host}:{port}\n"
                f"{addr}\n"
                f"Master 3.1 At BPM 111.1 / 122.2 / 133.3\n\n"
                "Watch MA3 System Monitor (Echo Input).",
            )
        else:
            QMessageBox.warning(
                self,
                "OSC",
                f"Send failed: {self.engine.osc.last_error or 'disabled / unreachable'}",
            )

    def apply(self) -> None:
        cfg = self.engine.config
        cfg.osc.enabled = self.osc_enabled.isChecked()
        cfg.osc.host = self.osc_host.text().strip() or "127.0.0.1"
        cfg.osc.port = self.osc_port.value()
        # Empty path breaks MA3 when Prefix is gma3 — keep a working default
        cfg.osc.prefix = self.osc_prefix.text().strip() or "gma3/cmd"
        cfg.osc.bpm_delta = float(self.osc_delta.value()) or 0.25
        cfg.midi.enabled = self.midi_enabled.isChecked()
        cfg.midi.port_name = self.midi_port.currentText().strip()
        cfg.midi.clock_lane_id = self.clock_lane.currentData() or ""


class MainWindow(QMainWindow):
    def __init__(self, engine: AppEngine):
        super().__init__()
        self.engine = engine
        self.cards: dict[str, LaneCard] = {}
        self.setWindowTitle(f"{__app_name__}  v{__version__}")
        self.resize(1100, 720)
        self.setStyleSheet(DARK_QSS)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        top = QFrame()
        top.setObjectName("topBar")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(16, 10, 16, 10)
        title = QLabel(__app_name__)
        title.setObjectName("appTitle")
        top_l.addWidget(title)
        top_l.addSpacing(16)
        subtitle = QLabel("Dante BPM → MA3")
        subtitle.setStyleSheet("color:#6d7588;")
        top_l.addWidget(subtitle)
        top_l.addStretch()

        self.osc_status = QLabel("● OSC")
        self.osc_status.setObjectName("statusDot")
        self.midi_status = QLabel("● MIDI")
        self.midi_status.setObjectName("statusDot")
        top_l.addWidget(self.osc_status)
        top_l.addWidget(self.midi_status)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        top_l.addWidget(self.settings_btn)

        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("startButton")
        self.start_btn.clicked.connect(self.toggle_run)
        top_l.addWidget(self.start_btn)
        layout.addWidget(top)

        # Lane scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lane_host = QWidget()
        self.lane_layout = QVBoxLayout(self.lane_host)
        self.lane_layout.setContentsMargins(16, 16, 16, 16)
        self.lane_layout.setSpacing(12)
        self.lane_layout.addStretch()
        scroll.setWidget(self.lane_host)
        layout.addWidget(scroll, stretch=1)

        # Bottom bar
        bottom = QFrame()
        bottom.setObjectName("settingsPanel")
        bottom_l = QHBoxLayout(bottom)
        bottom_l.setContentsMargins(16, 10, 16, 10)
        self.add_lane_btn = QPushButton("+ Add Lane")
        self.add_lane_btn.setObjectName("accentButton")
        self.add_lane_btn.clicked.connect(self.add_lane)
        self.refresh_btn = QPushButton("Refresh Devices")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        bottom_l.addWidget(self.add_lane_btn)
        bottom_l.addWidget(self.refresh_btn)
        bottom_l.addStretch()
        hint = QLabel("Tip: run Dante Virtual Soundcard in WDM mode · OSC needs Receive Command = Yes on MA3")
        hint.setStyleSheet("color:#5c6478; font-size:11px;")
        bottom_l.addWidget(hint)
        layout.addWidget(bottom)

        self._devices = list_input_devices()
        self.rebuild_cards()

        if self.engine.config.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromHex(self.engine.config.window_geometry.encode("ascii")))
            except Exception:  # noqa: BLE001
                pass

        self.timer = QTimer(self)
        self.timer.setInterval(33)  # ~30 fps
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._update_status()

    def rebuild_cards(self) -> None:
        for card in list(self.cards.values()):
            self.lane_layout.removeWidget(card)
            card.deleteLater()
        self.cards.clear()
        # Insert before stretch
        stretch = self.lane_layout.takeAt(self.lane_layout.count() - 1)
        for lane in self.engine.config.lanes:
            runtime = self.engine.lanes[lane.id]
            card = LaneCard(runtime, self._devices)
            card.changed.connect(self.on_lane_changed)
            card.remove_requested.connect(self.remove_lane)
            card.tap_requested.connect(self.engine.tap)
            card.multiply_requested.connect(self.engine.multiply_bpm)
            card.resync_requested.connect(self.engine.resync)
            card.rebind_requested.connect(self.on_lane_rebind)
            self.cards[lane.id] = card
            self.lane_layout.addWidget(card)
        self.lane_layout.addStretch()
        if stretch:
            del stretch

    def refresh_devices(self) -> None:
        self._devices = list_input_devices()
        for card in self.cards.values():
            card.refresh_devices(self._devices)

    def add_lane(self) -> None:
        n = len(self.engine.config.lanes) + 1
        lane = new_lane(f"Lane {n}")
        if self._devices:
            lane.device_name = self._devices[0].name
            lane.device_index = self._devices[0].index
        self.engine.add_lane(lane)
        self.rebuild_cards()
        self.engine.save()

    def remove_lane(self, lane_id: str) -> None:
        if len(self.engine.config.lanes) <= 1:
            QMessageBox.information(self, __app_name__, "At least one lane is required.")
            return
        self.engine.remove_lane(lane_id)
        self.rebuild_cards()
        self.engine.save()

    def on_lane_changed(self, lane_id: str) -> None:
        card = self.cards.get(lane_id)
        if card:
            card.apply_to_config()
        # Live params (gain/monitor/triggers) — no stream restart
        self.engine.apply_lane_live(lane_id)
        self.engine.save()

    def on_lane_rebind(self, lane_id: str) -> None:
        card = self.cards.get(lane_id)
        if card:
            card.apply_to_config()
        self.engine.rebind_lane(lane_id)
        self.engine.save()

    def toggle_run(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self.start_btn.setText("Start")
            self.start_btn.setProperty("running", False)
        else:
            # Push UI values into config first
            for card in self.cards.values():
                card.apply_to_config()
            self.engine.apply_io_settings()
            self.engine.start()
            self.start_btn.setText("Stop")
            self.start_btn.setProperty("running", True)
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self._update_status()
        self.engine.save()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.engine, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self.engine.apply_io_settings()
            self.engine.save()
            self._update_status()

    def _update_status(self) -> None:
        osc_on = self.engine.config.osc.enabled and self.engine.osc.ok
        midi_on = self.engine.config.midi.enabled and self.engine.midi.ok
        self.osc_status.setText(
            f"● OSC {self.engine.config.osc.host}:{self.engine.config.osc.port}"
        )
        self.osc_status.setStyleSheet(
            f"color: {'#4cc9a0' if osc_on else ('#f4d35e' if self.engine.config.osc.enabled else '#555')};"
        )
        port = self.engine.config.midi.port_name or "—"
        self.midi_status.setText(f"● MIDI {port}")
        self.midi_status.setStyleSheet(
            f"color: {'#4cc9a0' if midi_on else ('#f4d35e' if self.engine.config.midi.enabled else '#555')};"
        )

    def _tick(self) -> None:
        for lane_id, card in self.cards.items():
            runtime = self.engine.lanes.get(lane_id)
            if runtime is None:
                continue
            wave, level = self.engine.waveform(lane_id)
            histories = self.engine.band_histories(lane_id)
            flashes = {
                t.id: self.engine.trigger_flash(lane_id, t.id)
                for t in runtime.config.band_triggers
            }
            card.update_live(
                wave,
                level if level else runtime.level,
                runtime.bpm,
                runtime.beat_flash,
                lock_status=runtime.lock_status,
                band_histories=histories,
                trigger_flashes=flashes,
            )
        self._update_status()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.engine.config.window_geometry = bytes(self.saveGeometry().toHex()).decode("ascii")
        self.engine.shutdown()
        super().closeEvent(event)
