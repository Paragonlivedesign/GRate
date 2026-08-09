"""Dark touring-software stylesheet."""

from __future__ import annotations

from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_SPIN_UP = (_ASSET_DIR / "spin_up.png").as_posix()
_SPIN_DOWN = (_ASSET_DIR / "spin_down.png").as_posix()
_COMBO_DOWN = (_ASSET_DIR / "combo_down.png").as_posix()

DARK_QSS = f"""
QWidget {{
    background-color: #0e0f12;
    color: #e8e8ec;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: #0e0f12;
}}
QToolTip {{
    background-color: #1a1c22;
    color: #e8e8ec;
    border: 1px solid #2a2d36;
}}
QPushButton {{
    background-color: #1c1f27;
    border: 1px solid #2e3340;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e8e8ec;
}}
QPushButton:hover {{
    background-color: #252a35;
    border-color: #3d4454;
}}
QPushButton:pressed {{
    background-color: #15181f;
}}
QPushButton#startButton {{
    background-color: #1f6f4a;
    border-color: #2a9d68;
    font-weight: 600;
    min-width: 96px;
}}
QPushButton#startButton:hover {{
    background-color: #258557;
}}
QPushButton#startButton[running="true"] {{
    background-color: #8a2f2f;
    border-color: #c44;
}}
QPushButton#accentButton {{
    background-color: #243049;
    border-color: #3d5a80;
}}
QPushButton#compactButton {{
    padding: 4px 10px;
    min-height: 26px;
    font-size: 12px;
}}
QPushButton#compactButton[selected="true"] {{
    background-color: #2a4a3a;
    border-color: #4cc9a0;
    color: #4cc9a0;
    font-weight: 600;
}}
QPushButton#compactButton[metro="true"] {{
    background-color: #3a3420;
    border-color: #c4a35a;
    color: #f0e6c8;
}}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: #15181f;
    border: 1px solid #2e3340;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #3d5a80;
    color: #e8e8ec;
}}
QSpinBox, QDoubleSpinBox {{
    padding-right: 22px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #3a4150;
    border-bottom: 1px solid #2a2f3a;
    border-top-right-radius: 5px;
    background-color: #252a35;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border-left: 1px solid #3a4150;
    border-bottom-right-radius: 5px;
    background-color: #252a35;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: #3a4558;
}}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background-color: #1a1f28;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{_SPIN_UP}");
    width: 9px;
    height: 9px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{_SPIN_DOWN}");
    width: 9px;
    height: 9px;
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 22px;
    border-left: 1px solid #3a4150;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background-color: #252a35;
}}
QComboBox::drop-down:hover {{
    background-color: #3a4558;
}}
QComboBox::down-arrow {{
    image: url("{_COMBO_DOWN}");
    width: 9px;
    height: 9px;
}}
QComboBox QAbstractItemView {{
    background-color: #15181f;
    border: 1px solid #2e3340;
    selection-background-color: #2a3550;
    color: #e8e8ec;
}}
QCheckBox, QLabel {{
    background: transparent;
}}
QGroupBox {{
    border: 1px solid #262a34;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #9aa3b5;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: #0e0f12;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #2e3340;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QFrame#topBar {{
    background-color: #12141a;
    border-bottom: 1px solid #232733;
}}
QFrame#laneCard {{
    background-color: #14171e;
    border: 1px solid #242833;
    border-radius: 10px;
}}
QFrame#laneCard:hover {{
    border-color: #33405a;
}}
QLabel#appTitle {{
    font-size: 18px;
    font-weight: 700;
    color: #f2f4f8;
    letter-spacing: 1px;
}}
QLabel#bpmValue {{
    font-size: 42px;
    font-weight: 700;
    color: #f5f7fb;
}}
QLabel#triggerSummary {{
    color: #8b93a7;
    font-size: 12px;
}}
QLabel#statusDot {{
    font-size: 14px;
}}
QFrame#settingsPanel {{
    background-color: #12141a;
    border-top: 1px solid #232733;
}}
QFrame#channelRow {{
    background-color: #171b23;
    border: 1px solid #232833;
    border-radius: 8px;
}}
QFrame#channelRow[muted="true"] {{
    border-color: #1c2029;
}}
QFrame#channelRow[soloed="true"] {{
    border-left: 3px solid #f4d35e;
}}
QPushButton#muteButton, QPushButton#soloButton, QPushButton#monitorButton {{
    padding: 0px;
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    font-size: 11px;
    font-weight: 700;
    border-radius: 5px;
}}
QPushButton#muteButton:checked {{
    background-color: #4a2020;
    border-color: #e76f51;
    color: #e76f51;
}}
QPushButton#soloButton:checked {{
    background-color: #443c1a;
    border-color: #f4d35e;
    color: #f4d35e;
}}
QPushButton#monitorButton:checked {{
    background-color: #1f3a4a;
    border-color: #4ea8de;
    color: #4ea8de;
}}
QPushButton#swatchButton {{
    padding: 0px;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    border-radius: 4px;
    border: 1px solid #3a4150;
}}
QRadioButton {{
    background: transparent;
    spacing: 0px;
}}
"""

ACCENT = "#4cc9a0"
WAVE_COLOR = "#4cc9a0"
BEAT_COLOR = "#f4d35e"
LEVEL_COLOR = "#3d5a80"
BAND_LOW = "#f4a261"
BAND_MID = "#4cc9a0"
BAND_HIGH = "#4ea8de"
LOCK_GREEN = "#4cc9a0"
LOCK_AMBER = "#e9c46a"
LOCK_GRAY = "#6d7588"
MUTE_RED = "#e76f51"
SOLO_YELLOW = "#f4d35e"
MONITOR_BLUE = "#4ea8de"

# Default channel colors (mirrors config.DEFAULT_CHANNEL_COLORS)
from grate.config import DEFAULT_CHANNEL_COLORS as CHANNEL_PALETTE  # noqa: E402
