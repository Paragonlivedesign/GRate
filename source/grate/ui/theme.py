"""Dark touring-software stylesheet."""

DARK_QSS = """
QWidget {
    background-color: #0e0f12;
    color: #e8e8ec;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #0e0f12;
}
QToolTip {
    background-color: #1a1c22;
    color: #e8e8ec;
    border: 1px solid #2a2d36;
}
QPushButton {
    background-color: #1c1f27;
    border: 1px solid #2e3340;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e8e8ec;
}
QPushButton:hover {
    background-color: #252a35;
    border-color: #3d4454;
}
QPushButton:pressed {
    background-color: #15181f;
}
QPushButton#startButton {
    background-color: #1f6f4a;
    border-color: #2a9d68;
    font-weight: 600;
    min-width: 96px;
}
QPushButton#startButton:hover {
    background-color: #258557;
}
QPushButton#startButton[running="true"] {
    background-color: #8a2f2f;
    border-color: #c44;
}
QPushButton#accentButton {
    background-color: #243049;
    border-color: #3d5a80;
}
QPushButton#compactButton {
    padding: 4px 10px;
    min-height: 26px;
    font-size: 12px;
}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #15181f;
    border: 1px solid #2e3340;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #3d5a80;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #15181f;
    border: 1px solid #2e3340;
    selection-background-color: #2a3550;
}
QCheckBox, QLabel {
    background: transparent;
}
QGroupBox {
    border: 1px solid #262a34;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #9aa3b5;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: #0e0f12;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2e3340;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QFrame#topBar {
    background-color: #12141a;
    border-bottom: 1px solid #232733;
}
QFrame#laneCard {
    background-color: #14171e;
    border: 1px solid #242833;
    border-radius: 10px;
}
QFrame#laneCard:hover {
    border-color: #33405a;
}
QLabel#appTitle {
    font-size: 18px;
    font-weight: 700;
    color: #f2f4f8;
    letter-spacing: 1px;
}
QLabel#bpmValue {
    font-size: 42px;
    font-weight: 700;
    color: #f5f7fb;
}
QLabel#triggerSummary {
    color: #8b93a7;
    font-size: 12px;
}
QLabel#statusDot {
    font-size: 14px;
}
QFrame#settingsPanel {
    background-color: #12141a;
    border-top: 1px solid #232733;
}
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
