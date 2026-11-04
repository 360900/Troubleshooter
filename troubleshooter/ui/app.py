from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from ..database import load_database
from .theme import ASSETS, UI_FONT, theme
from .window import MainWindow


def create_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Troubleshooter")
    app.setDesktopFileName("troubleshooter")
    app.setWindowIcon(QIcon(str(ASSETS / "troubleshooter.svg")))
    # Fusion draws nothing of its own under our stylesheet, whatever Qt style the desktop sets.
    app.setStyle("Fusion")
    theme.setup(app)
    app.setFont(QFont(UI_FONT, 10))
    return app


def run() -> int:
    app = create_app()
    window = MainWindow(load_database())
    window.show()
    return app.exec()
