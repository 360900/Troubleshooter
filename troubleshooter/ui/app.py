from __future__ import annotations

import gc
import sys

from PySide6.QtCore import QObject
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from ..database import load_database
from .theme import ASSETS, UI_FONT, theme
from .window import MainWindow


class MainThreadGC(QObject):
    """Runs Python's cyclic garbage collector on the GUI thread only.

    Automatic collection runs in whichever thread happens to allocate. When that's a
    worker (the checks run in a thread pool) and the garbage holds Qt objects, they get
    destroyed off the GUI thread and the app crashes. So automatic collection is off,
    and this timer does the same generational collection from the GUI thread.
    """

    def __init__(self, parent: QObject, interval_ms: int = 500):
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        gc.disable()
        self.startTimer(interval_ms)

    def timerEvent(self, event):
        counts = gc.get_count()
        if counts[0] <= self.threshold[0]:
            return
        generation = 0
        if counts[1] > self.threshold[1]:
            generation = 2 if counts[2] > self.threshold[2] else 1
        gc.collect(generation)


def create_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    if not app.findChild(MainThreadGC):
        MainThreadGC(app)
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
