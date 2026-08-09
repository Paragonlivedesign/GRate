"""GRate application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from grate import __app_name__
from grate.config import load_config
from grate.core.engine import AppEngine
from grate.ui.main_window import MainWindow
from grate.ui.theme import DARK_QSS


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

    config = load_config()
    # Seed first lane with first available device if empty
    try:
        from grate.audio.capture import list_input_devices

        devices = list_input_devices()
        if devices and config.lanes and not config.lanes[0].device_name:
            config.lanes[0].device_name = devices[0].name
            config.lanes[0].device_index = devices[0].index
    except Exception:  # noqa: BLE001
        pass

    engine = AppEngine(config)
    window = MainWindow(engine)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
