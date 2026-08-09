"""Per-channel parametric EQ editor.

Pro-Q-style graph: the response curve is the real filter chain, nodes ride the
curve (HP/LP) or sit at their gain height (shelves), and every parameter can be
typed directly. Drag semantics:
  - HP / LP node: left-right = cutoff frequency, up-down = Q (resonance).
  - Shelf nodes: left-right = corner frequency, up-down = gain.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from grate.config import FULL_RANGE_HIGH, FULL_RANGE_LOW, ChannelConfig
from grate.dsp.bands import (
    Q_MAX,
    Q_MIN,
    SHELF_Q_MAX,
    SHELF_Q_MIN,
    build_sections,
    sections_response_db,
)

_SR = 48000.0
_FMIN, _FMAX = 20.0, 20000.0
_DB_MIN, _DB_MAX = -30.0, 18.0
_SHELF_LIMIT = 15.0

_FREQ_LABELS = [30, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000]
_DB_LINES = [-24, -18, -12, -6, 0, 6, 12]


def _fmt_freq(f: float) -> str:
    return f"{f / 1000:g}k" if f >= 1000 else f"{f:.0f}"


def _sections_for(cfg: ChannelConfig, *, skip: str = "") -> list[list[float]]:
    """Build the biquad chain from a channel config; ``skip`` omits one band."""
    return build_sections(
        _SR,
        hp_hz=FULL_RANGE_LOW if skip == "hp" else cfg.freq_low_hz,
        lp_hz=FULL_RANGE_HIGH if skip == "lp" else cfg.freq_high_hz,
        hp_q=cfg.hp_q,
        lp_q=cfg.lp_q,
        low_shelf_hz=cfg.low_shelf_hz,
        low_shelf_db=0.0 if skip == "ls" else cfg.low_shelf_db,
        low_shelf_q=cfg.low_shelf_q,
        high_shelf_hz=cfg.high_shelf_hz,
        high_shelf_db=0.0 if skip == "hs" else cfg.high_shelf_db,
        high_shelf_q=cfg.high_shelf_q,
    )


class _Field(QVBoxLayout):
    """Small gray caption above a typed spinbox."""

    def __init__(self, caption: str, spin: QDoubleSpinBox):
        super().__init__()
        self.setSpacing(1)
        label = QLabel(caption)
        label.setStyleSheet("color:#6d7588; font-size:10px;")
        label.setAlignment(Qt.AlignHCenter)
        self.addWidget(label)
        self.addWidget(spin)


def _freq_spin(lo: float, hi: float) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(0)
    s.setSuffix(" Hz")
    s.setSingleStep(10)
    s.setKeyboardTracking(False)
    s.setFixedWidth(96)
    s.setAlignment(Qt.AlignCenter)
    return s


def _gain_spin() -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(-_SHELF_LIMIT, _SHELF_LIMIT)
    s.setDecimals(1)
    s.setSuffix(" dB")
    s.setSingleStep(0.5)
    s.setKeyboardTracking(False)
    s.setFixedWidth(84)
    s.setAlignment(Qt.AlignCenter)
    return s


def _q_spin(lo: float, hi: float) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(2)
    s.setSingleStep(0.1)
    s.setKeyboardTracking(False)
    s.setFixedWidth(72)
    s.setAlignment(Qt.AlignCenter)
    return s


class EqDialog(QDialog):
    """Interactive EQ graph + typed fields for one channel. Applies live."""

    eq_changed = Signal()

    def __init__(self, cfg: ChannelConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle(f"EQ — {cfg.name}")
        self.resize(760, 560)
        self._updating = False
        self._freqs = np.logspace(math.log10(_FMIN), math.log10(_FMAX), 400)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ------------------------------------------------ response plot
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#0e1117")
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setXRange(math.log10(_FMIN), math.log10(_FMAX), padding=0.005)
        self.plot.setYRange(_DB_MIN, _DB_MAX, padding=0)
        plot_item = self.plot.getPlotItem()
        plot_item.hideAxis("bottom")
        left_axis = plot_item.getAxis("left")
        left_axis.setTextPen("#4a5164")
        left_axis.setPen("#262b36")
        left_axis.setTicks([[(db, f"{db:+d}" if db else "0") for db in _DB_LINES]])

        # Very light grid drawn manually so it stays subtle
        grid_pen = pg.mkPen("#1b2029", width=1)
        for db in _DB_LINES:
            self.plot.addLine(y=db, pen=grid_pen if db != 0 else pg.mkPen("#2c3342", width=1))
        for decade in (10, 100, 1000, 10000):
            for mult in range(1, 10):
                f = decade * mult
                if _FMIN <= f <= _FMAX:
                    self.plot.addLine(x=math.log10(f), pen=grid_pen)

        # Light frequency labels along the bottom of the plot
        for f in _FREQ_LABELS:
            t = pg.TextItem(_fmt_freq(f), color="#4a5164", anchor=(0.5, 1.0))
            t.setPos(math.log10(f), _DB_MIN + 0.4)
            self.plot.addItem(t)

        col = QColor(cfg.color)
        self.curve = self.plot.plot(
            pen=pg.mkPen(cfg.color, width=2.5),
            fillLevel=_DB_MIN,
            brush=pg.mkBrush(col.red(), col.green(), col.blue(), 34),
        )

        def make_handle(label: str) -> pg.TargetItem:
            item = pg.TargetItem(
                size=15,
                movable=True,
                pen=pg.mkPen("#e8ecf4", width=1.5),
                brush=pg.mkBrush(cfg.color),
                label=label,
                labelOpts={"offset": (0, -19), "color": "#aab3c5"},
            )
            item.setZValue(20)
            self.plot.addItem(item)
            return item

        self.hp_handle = make_handle("HP")
        self.ls_handle = make_handle("LO SHELF")
        self.hs_handle = make_handle("HI SHELF")
        self.lp_handle = make_handle("LP")
        self.hp_handle.sigPositionChanged.connect(lambda: self._handle_moved("hp"))
        self.lp_handle.sigPositionChanged.connect(lambda: self._handle_moved("lp"))
        self.ls_handle.sigPositionChanged.connect(lambda: self._handle_moved("ls"))
        self.hs_handle.sigPositionChanged.connect(lambda: self._handle_moved("hs"))
        root.addWidget(self.plot, stretch=1)

        hint = QLabel(
            "Drag nodes: HP/LP left–right = cutoff, up–down = Q (resonance) · "
            "shelves left–right = frequency, up–down = gain. Or type values below."
        )
        hint.setStyleSheet("color:#6d7588; font-size:11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # ------------------------------------------------ typed fields per band
        bands_row = QHBoxLayout()
        bands_row.setSpacing(10)

        def band_frame(title: str) -> tuple[QFrame, QHBoxLayout]:
            frame = QFrame()
            frame.setStyleSheet(
                "QFrame { background:#151922; border:1px solid #262b36; border-radius:6px; }"
                "QLabel { border:none; background:transparent; }"
            )
            v = QVBoxLayout(frame)
            v.setContentsMargins(8, 5, 8, 7)
            v.setSpacing(3)
            head = QLabel(title)
            head.setStyleSheet(f"color:{cfg.color}; font-size:10px; font-weight:700; border:none;")
            head.setAlignment(Qt.AlignHCenter)
            v.addWidget(head)
            fields = QHBoxLayout()
            fields.setSpacing(6)
            v.addLayout(fields)
            return frame, fields

        self.hp_freq = _freq_spin(_FMIN, _FMAX)
        self.hp_q = _q_spin(Q_MIN, Q_MAX)
        frame, fields = band_frame("HIGH PASS · 24 dB/oct")
        fields.addLayout(_Field("Freq", self.hp_freq))
        fields.addLayout(_Field("Q", self.hp_q))
        bands_row.addWidget(frame)

        self.ls_freq = _freq_spin(_FMIN, 2000)
        self.ls_gain = _gain_spin()
        self.ls_q = _q_spin(SHELF_Q_MIN, SHELF_Q_MAX)
        frame, fields = band_frame("LOW SHELF")
        fields.addLayout(_Field("Freq", self.ls_freq))
        fields.addLayout(_Field("Gain", self.ls_gain))
        fields.addLayout(_Field("Q", self.ls_q))
        bands_row.addWidget(frame)

        self.hs_freq = _freq_spin(500, _FMAX)
        self.hs_gain = _gain_spin()
        self.hs_q = _q_spin(SHELF_Q_MIN, SHELF_Q_MAX)
        frame, fields = band_frame("HIGH SHELF")
        fields.addLayout(_Field("Freq", self.hs_freq))
        fields.addLayout(_Field("Gain", self.hs_gain))
        fields.addLayout(_Field("Q", self.hs_q))
        bands_row.addWidget(frame)

        self.lp_freq = _freq_spin(25, _FMAX)
        self.lp_q = _q_spin(Q_MIN, Q_MAX)
        frame, fields = band_frame("LOW PASS · 24 dB/oct")
        fields.addLayout(_Field("Freq", self.lp_freq))
        fields.addLayout(_Field("Q", self.lp_q))
        bands_row.addWidget(frame)

        root.addLayout(bands_row)
        for spin in (
            self.hp_freq, self.hp_q, self.ls_freq, self.ls_gain, self.ls_q,
            self.hs_freq, self.hs_gain, self.hs_q, self.lp_freq, self.lp_q,
        ):
            spin.valueChanged.connect(self._fields_changed)

        # ------------------------------------------------ gate
        gate_row = QHBoxLayout()
        gate_row.setSpacing(8)
        self.gate_check = QCheckBox("Gate")
        self.gate_check.setToolTip("Noise gate — mutes the channel below the threshold (fast open, slow close)")
        self.gate_check.toggled.connect(self._gate_changed)
        gate_row.addWidget(self.gate_check)
        self.gate_slider = QSlider(Qt.Horizontal)
        self.gate_slider.setRange(-80, -10)
        self.gate_slider.setToolTip("Gate threshold (dBFS)")
        self.gate_slider.valueChanged.connect(self._gate_changed)
        gate_row.addWidget(self.gate_slider, stretch=1)
        self.gate_label = QLabel("-50 dB")
        self.gate_label.setFixedWidth(56)
        gate_row.addWidget(self.gate_label)
        root.addLayout(gate_row)

        # ------------------------------------------------ footer
        footer = QHBoxLayout()
        flat_btn = QPushButton("Flat (reset)")
        flat_btn.setToolTip("Full range, Q 0.71, shelves at 0 dB, gate off")
        flat_btn.clicked.connect(self._reset_flat)
        footer.addWidget(flat_btn)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self.refresh_from_config()

    # ------------------------------------------------------------- sync

    def refresh_from_config(self) -> None:
        """Pull cfg into every control without emitting."""
        self._updating = True
        try:
            self.hp_freq.setValue(self.cfg.freq_low_hz)
            self.hp_q.setValue(self.cfg.hp_q)
            self.ls_freq.setValue(self.cfg.low_shelf_hz)
            self.ls_gain.setValue(self.cfg.low_shelf_db)
            self.ls_q.setValue(self.cfg.low_shelf_q)
            self.hs_freq.setValue(self.cfg.high_shelf_hz)
            self.hs_gain.setValue(self.cfg.high_shelf_db)
            self.hs_q.setValue(self.cfg.high_shelf_q)
            self.lp_freq.setValue(self.cfg.freq_high_hz)
            self.lp_q.setValue(self.cfg.lp_q)
            self.gate_check.setChecked(self.cfg.gate_enabled)
            self.gate_slider.setValue(int(self.cfg.gate_db))
            self.gate_slider.setEnabled(self.cfg.gate_enabled)
        finally:
            self._updating = False
        self._redraw()

    # ------------------------------------------------------------- events

    def _fields_changed(self) -> None:
        if self._updating:
            return
        hp = float(self.hp_freq.value())
        lp = float(self.lp_freq.value())
        if hp > lp - 10:
            hp = max(_FMIN, lp - 10)
        self.cfg.freq_low_hz = round(hp)
        self.cfg.freq_high_hz = round(lp)
        self.cfg.hp_q = float(self.hp_q.value())
        self.cfg.lp_q = float(self.lp_q.value())
        self.cfg.low_shelf_hz = round(float(self.ls_freq.value()))
        self.cfg.low_shelf_db = float(self.ls_gain.value())
        self.cfg.low_shelf_q = float(self.ls_q.value())
        self.cfg.high_shelf_hz = round(float(self.hs_freq.value()))
        self.cfg.high_shelf_db = float(self.hs_gain.value())
        self.cfg.high_shelf_q = float(self.hs_q.value())
        self._redraw()
        self.eq_changed.emit()

    def _q_from_node_y(self, y_db: float, cutoff_hz: float, skip: str) -> float:
        """Node height above the rest of the chain's response = 20*log10(Q)."""
        rest = _sections_for(self.cfg, skip=skip)
        base = float(
            sections_response_db(np.array([cutoff_hz]), rest, _SR)[0]
        ) if rest else 0.0
        q = 10.0 ** ((y_db - base) / 20.0)
        return max(Q_MIN, min(Q_MAX, q))

    def _handle_moved(self, which: str) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            if which == "hp":
                pos = self.hp_handle.pos()
                f = 10 ** min(max(pos.x(), math.log10(_FMIN)), math.log10(max(self.cfg.freq_high_hz - 10, _FMIN + 1)))
                self.cfg.freq_low_hz = round(f)
                self.cfg.hp_q = round(self._q_from_node_y(pos.y(), f, "hp"), 2)
                self.hp_freq.setValue(self.cfg.freq_low_hz)
                self.hp_q.setValue(self.cfg.hp_q)
            elif which == "lp":
                pos = self.lp_handle.pos()
                f = 10 ** min(max(pos.x(), math.log10(self.cfg.freq_low_hz + 10)), math.log10(_FMAX))
                self.cfg.freq_high_hz = round(f)
                self.cfg.lp_q = round(self._q_from_node_y(pos.y(), f, "lp"), 2)
                self.lp_freq.setValue(self.cfg.freq_high_hz)
                self.lp_q.setValue(self.cfg.lp_q)
            elif which == "ls":
                pos = self.ls_handle.pos()
                f = 10 ** min(max(pos.x(), math.log10(_FMIN)), math.log10(2000))
                g = min(max(pos.y(), -_SHELF_LIMIT), _SHELF_LIMIT)
                self.cfg.low_shelf_hz = round(f)
                self.cfg.low_shelf_db = round(g, 1)
                self.ls_freq.setValue(self.cfg.low_shelf_hz)
                self.ls_gain.setValue(self.cfg.low_shelf_db)
            elif which == "hs":
                pos = self.hs_handle.pos()
                f = 10 ** min(max(pos.x(), math.log10(500)), math.log10(_FMAX))
                g = min(max(pos.y(), -_SHELF_LIMIT), _SHELF_LIMIT)
                self.cfg.high_shelf_hz = round(f)
                self.cfg.high_shelf_db = round(g, 1)
                self.hs_freq.setValue(self.cfg.high_shelf_hz)
                self.hs_gain.setValue(self.cfg.high_shelf_db)
        finally:
            self._updating = False
        self._redraw()
        self.eq_changed.emit()

    def _gate_changed(self, *_args) -> None:
        if self._updating:
            return
        self.cfg.gate_enabled = self.gate_check.isChecked()
        self.cfg.gate_db = float(self.gate_slider.value())
        self.gate_slider.setEnabled(self.cfg.gate_enabled)
        self.gate_label.setText(f"{int(self.cfg.gate_db)} dB")
        self.eq_changed.emit()

    def _reset_flat(self) -> None:
        self.cfg.freq_low_hz = FULL_RANGE_LOW
        self.cfg.freq_high_hz = FULL_RANGE_HIGH
        self.cfg.hp_q = 0.707
        self.cfg.lp_q = 0.707
        self.cfg.low_shelf_hz = 200.0
        self.cfg.low_shelf_db = 0.0
        self.cfg.low_shelf_q = 0.9
        self.cfg.high_shelf_hz = 4000.0
        self.cfg.high_shelf_db = 0.0
        self.cfg.high_shelf_q = 0.9
        self.cfg.gate_enabled = False
        self.refresh_from_config()
        self.eq_changed.emit()

    # ------------------------------------------------------------- drawing

    def _redraw(self) -> None:
        cfg = self.cfg
        sections = _sections_for(cfg)
        db = np.clip(sections_response_db(self._freqs, sections, _SR), _DB_MIN, _DB_MAX)
        self.curve.setData(np.log10(self._freqs), db)

        def resp_at(f: float) -> float:
            return float(
                np.clip(sections_response_db(np.array([f]), sections, _SR)[0], _DB_MIN + 1, _DB_MAX - 0.5)
            ) if sections else 0.0

        self._updating = True
        try:
            # HP/LP nodes ride the curve at their cutoff; shelves sit at gain height
            self.hp_handle.setPos(math.log10(max(cfg.freq_low_hz, _FMIN)), resp_at(cfg.freq_low_hz))
            self.lp_handle.setPos(math.log10(min(cfg.freq_high_hz, _FMAX)), resp_at(cfg.freq_high_hz))
            self.ls_handle.setPos(math.log10(cfg.low_shelf_hz), cfg.low_shelf_db)
            self.hs_handle.setPos(math.log10(cfg.high_shelf_hz), cfg.high_shelf_db)
        finally:
            self._updating = False

        self.gate_label.setText(f"{int(cfg.gate_db)} dB")

    def set_channel_name(self, name: str) -> None:
        self.setWindowTitle(f"EQ — {name}")
