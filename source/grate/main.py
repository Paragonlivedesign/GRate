"""GRate application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from grate import __app_name__
from grate.config import load_config
from grate.core.engine import AppEngine
from grate.ui.main_window import MainWindow
from grate.ui.theme import DARK_QSS


def _app_icon() -> QIcon:
    here = Path(__file__).resolve().parent / "ui" / "assets"
    ico = here / "grate.ico"
    png = here / "grate_icon_256.png"
    if ico.exists():
        return QIcon(str(ico))
    if png.exists():
        return QIcon(str(png))
    return QIcon()


def main() -> int:
    # High-DPI friendly
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName("GRate")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    config = load_config()
    # Seed the first channel with the first available device if empty
    try:
        from grate.audio.capture import list_input_devices

        devices = list_input_devices()
        if devices and config.tracks and config.tracks[0].channels:
            first = config.tracks[0].channels[0]
            if not first.device_name:
                first.device_name = devices[0].name
                first.device_index = devices[0].index
    except Exception:  # noqa: BLE001
        pass

    engine = AppEngine(config)
    window = MainWindow(engine)
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
