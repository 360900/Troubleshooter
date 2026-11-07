"""The main window: oneDesign chrome, navigation, and the flow between pages."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QPoint, QProcess, QRectF, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import engine, history
from ..database import Category, Database, Entry
from ..patches import PlanError
from ..search import SearchIndex
from ..system import System
from .pages import ApplyPage, CategoryPage, FixPage, HistoryPage, HomePage, ReproducePage, ResultsPage
from .theme import DENSITY, SHADOW, theme
from .widgets import Icon, button, label, qcolor, repolish

SIDEBAR_WIDTH = 260
RESIZE_MARGIN = 6


class _Signals(QObject):
    finished = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        self.signals.finished.emit(self.fn())


class Request:
    """What the user asked to check, kept so reproducing can run the same thing again."""

    def __init__(self, heading: str, run):
        self.heading, self.run = heading, run


class NavButton(QPushButton):
    """Sidebar row. Built from child widgets because Qt's button gap between icon and text is fixed."""

    def __init__(self, text: str, icon_name: str):
        super().__init__()
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.icon_name = icon_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)
        self.glyph = Icon(icon_name)
        self.caption = label(text, "nav", wrap=False)
        for child in (self.glyph, self.caption):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.glyph)
        layout.addWidget(self.caption, 1)
        self.toggled.connect(self._restyle)

    def _restyle(self, checked: bool) -> None:
        self.glyph.set_icon(self.icon_name, "onContainer" if checked else "text")
        self.caption.setProperty("role", "navActive" if checked else "nav")
        repolish(self.caption)


class Shell(QWidget):
    """Paints the window's soft shadow, since it has no server-side frame."""

    def paintEvent(self, event):
        if theme.maximized:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        for grow in range(SHADOW, 0, -1):
            alpha = 0.16 * (1 - grow / SHADOW) ** 2 / 3
            painter.setBrush(QColor.fromRgbF(0, 0, 0, alpha))
            painter.drawRoundedRect(rect.adjusted(-grow, -grow + 3, grow, grow + 3), 18 + grow, 18 + grow)
        painter.setPen(qcolor("strokeStrong"))
        painter.setBrush(qcolor("canvas"))
        painter.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), 18, 18)


class Header(QWidget):
    """Solid bar with a centered title and a close button only. Drag to move."""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.setFixedHeight(DENSITY["headerbar"])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 13, 0)
        self.back = button("", "headerback", "go-previous")
        self.back.setToolTip("Back")
        self.back.setVisible(False)
        self.title = QLabel()
        self.title.setProperty("role", "heading")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        close = button("", "round", "window-close", 12)
        close.setToolTip("Close")
        close.clicked.connect(window.close)
        left = QWidget()
        left.setObjectName("transparent")
        left.setFixedWidth(40)
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.back)
        right = QWidget()
        right.setObjectName("transparent")
        right.setFixedWidth(40)
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch(1)
        right_layout.addWidget(close)
        layout.addWidget(left)
        layout.addWidget(self.title, 1)
        layout.addWidget(right)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._window.windowHandle():
            self._window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        self._window.toggle_maximized()


class MainWindow(QMainWindow):
    def __init__(self, db: Database, make_system=System, hist: history.History | None = None,
                 elevate: list[str] | None = None, dismissed: history.Dismissed | None = None):
        super().__init__()
        self.db = db
        self.make_system = make_system
        self.history = hist or history.History()
        self.dismissed = dismissed or history.Dismissed()
        self.found: list[engine.Diagnosis] = []  # the launch check's findings, dismissed ones included
        self.pending: list[history.Record] = []
        self.elevate = elevate if elevate is not None else ([] if os.geteuid() == 0 else ["pkexec"])
        self.index = SearchIndex(db)
        self.system = make_system()
        self.pool = QThreadPool(self)
        self.tasks: set = set()
        self.ticket = 0
        self.nav_stack: list[QWidget] = []
        self.last_request: Request | None = None
        self.cursor: str | None = None
        self.process: QProcess | None = None

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Troubleshooter")
        self.resize(1040 + 2 * SHADOW, 740 + 2 * SHADOW)
        self.setMinimumSize(760 + 2 * SHADOW, 520 + 2 * SHADOW)
        self.setMouseTracking(True)

        self.shell = Shell()
        self.setCentralWidget(self.shell)
        self.shell_layout = QHBoxLayout(self.shell)
        self.shell_layout.setSpacing(0)
        self.shell_layout.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        self.shell_layout.addWidget(self._build_sidebar())

        content = QWidget()
        content.setObjectName("content")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.header = Header(self)
        self.header.back.clicked.connect(self.go_back)
        column.addWidget(self.header)
        self.stack = QStackedWidget()
        self.stack.setObjectName("transparent")
        column.addWidget(self.stack, 1)
        self.shell_layout.addWidget(content, 1)

        self.home = HomePage(self.index, self.system.os_name)
        self.home.describe.connect(self.check_description)
        self.home.pick_entry.connect(self.check_entry)
        self.home.pick_category.connect(self.open_category)
        self.home.pick_diagnosis.connect(self.open_diagnosis)
        self.home.dismiss.connect(self.dismiss_recommendation)
        self.home.restore.connect(self.restore_recommendation)
        self.results = ResultsPage()
        self.results.pick.connect(self.open_diagnosis)
        self.results.reproduce.connect(self.start_reproduce)
        self.reproduce = ReproducePage()
        self.reproduce.done.connect(self.finish_reproduce)
        self.reproduce.cancel.connect(self.go_back)
        self.fix = FixPage()
        self.fix.apply.connect(self.apply_plan)
        self.fix.reproduce.connect(lambda d: self.start_reproduce())
        self.fix.open_url.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.fix.dismiss.connect(self.dismiss_recommendation)
        self.fix.restore.connect(self.restore_recommendation)
        self.apply = ApplyPage()
        self.apply.done.connect(self.go_home)
        self.apply.undo.connect(self.undo_record)
        self.apply.reboot.connect(lambda: QProcess.startDetached("systemctl", ["reboot"]))
        self.hist_page = HistoryPage()
        self.hist_page.undo.connect(self.undo_record)
        for page in (self.home, self.results, self.reproduce, self.fix, self.apply, self.hist_page):
            self.stack.addWidget(page)

        self.show_root(self.home, self.nav_home)
        QShortcut(QKeySequence.StandardKey.Find, self, self.focus_search)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.go_back)
        self.run_scan()

    # Chrome

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        column = QVBoxLayout(sidebar)
        column.setContentsMargins(0, 0, 0, 8)
        column.setSpacing(0)
        brand = label("Troubleshooter", "heading", wrap=False)
        brand.setFixedHeight(DENSITY["headerbar"])
        brand.setContentsMargins(18, 0, 0, 0)
        column.addWidget(brand)

        inner = QWidget()
        inner.setObjectName("transparent")
        nav = QVBoxLayout(inner)
        nav.setContentsMargins(8, 0, 8, 0)
        nav.setSpacing(2)
        self.nav_buttons: list[NavButton] = []
        self.nav_home = self._nav("Home", "user-home", nav, self.go_home)
        nav.addSpacing(6)
        for category in self.db.categories.values():
            self._nav(category.title, category.icon, nav, lambda c=category: self.open_category(c))
        nav.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        column.addWidget(scroll, 1)

        footer = QVBoxLayout()
        footer.setContentsMargins(8, 6, 8, 0)
        self.nav_history = self._nav("Applied fixes", "document-open-recent", footer, self.open_history)
        column.addLayout(footer)
        return sidebar

    def _nav(self, text, icon_name, layout, on_click) -> NavButton:
        widget = NavButton(text, icon_name)
        widget.clicked.connect(lambda: on_click())
        layout.addWidget(widget)
        self.nav_buttons.append(widget)
        return widget

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            maximized = self.isMaximized() or self.isFullScreen()
            margin = 0 if maximized else SHADOW
            self.shell_layout.setContentsMargins(margin, margin, margin, margin)
            theme.set_maximized(maximized)
        super().changeEvent(event)

    def closeEvent(self, event):
        # Closing would kill the root script halfway through changing the system.
        if self.process:
            event.ignore()
            self.apply.status.setText("Still working. Please wait for this to finish before closing.")
            return
        super().closeEvent(event)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _edges(self, pos: QPoint):
        if self.isMaximized():
            return Qt.Edge(0)
        rect = self.rect().adjusted(SHADOW - RESIZE_MARGIN, SHADOW - RESIZE_MARGIN, -SHADOW + RESIZE_MARGIN, -SHADOW + RESIZE_MARGIN)
        edges = Qt.Edge(0)
        if abs(pos.x() - rect.left()) < RESIZE_MARGIN * 2:
            edges |= Qt.Edge.LeftEdge
        elif abs(pos.x() - rect.right()) < RESIZE_MARGIN * 2:
            edges |= Qt.Edge.RightEdge
        if abs(pos.y() - rect.top()) < RESIZE_MARGIN * 2:
            edges |= Qt.Edge.TopEdge
        elif abs(pos.y() - rect.bottom()) < RESIZE_MARGIN * 2:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event):
        edges = self._edges(event.position().toPoint())
        if edges != Qt.Edge(0) and self.windowHandle():
            self.windowHandle().startSystemResize(edges)

    # Navigation

    def _check_nav(self, active) -> None:
        for widget in self.nav_buttons:
            widget.setChecked(widget is active)

    def _show(self, page) -> None:
        self.stack.setCurrentWidget(page)
        self.header.title.setText(page.title)
        self.header.back.setVisible(bool(self.nav_stack))
        page.verticalScrollBar().setValue(0)

    def show_root(self, page, nav_button=None) -> None:
        self.nav_stack = []
        if nav_button:
            self._check_nav(nav_button)
        self._show(page)

    def push(self, page) -> None:
        current = self.stack.currentWidget()
        if current is not page:
            self.nav_stack.append(current)
        self._show(page)

    def go_back(self) -> None:
        if self.process:
            return
        self.ticket += 1  # anything still running answers to nobody now
        if self.nav_stack:
            self._show(self.nav_stack.pop())

    def go_home(self) -> None:
        if self.process:
            return
        self.ticket += 1
        self.show_root(self.home, self.nav_home)

    def focus_search(self) -> None:
        self.go_home()
        self.home.entry.setFocus()
        self.home.entry.selectAll()

    def _replace_category(self, page) -> None:
        old = getattr(self, "category_page", None)
        self.stack.addWidget(page)
        self.category_page = page
        if old:
            self.stack.removeWidget(old)
            old.deleteLater()

    # Background work

    def background(self, fn, on_done) -> None:
        self.ticket += 1
        ticket = self.ticket
        self._run(fn, lambda result: on_done(result) if ticket == self.ticket else None)

    def _run(self, fn, on_done) -> None:
        task = _Task(fn)
        task.setAutoDelete(False)
        self.tasks.add(task)

        def done(result):
            self.tasks.discard(task)
            on_done(result)
        task.signals.finished.connect(done, Qt.ConnectionType.QueuedConnection)
        self.pool.start(task)

    def _fresh_system(self) -> System:
        system = self.make_system()
        system.cursor = self.cursor
        return system

    def run_scan(self) -> None:
        self.home.set_scanning()

        def work():
            system = self.make_system()
            pending = self.history.pending_restart(system.boot_time)
            waiting = {r.entry_id for r in pending}
            return [d for d in engine.scan(self.db, system) if d.entry.id not in waiting], pending

        def done(result):
            self.found, self.pending = result
            self._publish_scan()
        self._run(work, done)

    def _publish_scan(self) -> None:
        hidden = self.dismissed.ids()
        self.home.set_scan_results([d for d in self.found if d.entry.id not in hidden], self.pending,
                                   [d for d in self.found if d.entry.id in hidden])

    def dismiss_recommendation(self, diagnosis: engine.Diagnosis) -> None:
        """Stop recommending this on launch. Nothing on the system changes."""
        self.dismissed.add(diagnosis.entry.id)
        self._dismissal_changed(diagnosis, True)

    def restore_recommendation(self, diagnosis: engine.Diagnosis) -> None:
        self.dismissed.remove(diagnosis.entry.id)
        self._dismissal_changed(diagnosis, False)

    def _dismissal_changed(self, diagnosis: engine.Diagnosis, dismissed: bool) -> None:
        if self.home.scan_results is not None:
            self._publish_scan()
        if getattr(self.fix, "diagnosis", None) is not None and self.fix.diagnosis.entry.id == diagnosis.entry.id:
            self.fix.set_dismissed(dismissed)

    # Flow

    def open_category(self, category: Category) -> None:
        for widget, cat in zip(self.nav_buttons[1:], self.db.categories.values()):
            if cat is category:
                self._check_nav(widget)
        self.ticket += 1
        page = CategoryPage(category, self.db.in_category(category.id), self.system)
        page.check.connect(self.check_category)
        page.pick_entry.connect(self.check_entry)
        self._replace_category(page)
        self.show_root(page)

    def open_history(self) -> None:
        self.ticket += 1
        self.hist_page.show_records(self.history.load())
        self.show_root(self.hist_page, self.nav_history)

    def check_description(self, text: str) -> None:
        hits = [(h.target, h.score) for h in self.index.search(text, 12) if not h.is_category]
        self.last_request = Request(f"Results for “{text}”",
                                    lambda s: engine.diagnose_hits(hits, s))
        self._run_request()

    def check_category(self, category: Category) -> None:
        self.last_request = Request(f"{category.title} check",
                                    lambda s: engine.diagnose_category(self.db, category.id, s))
        self._run_request()

    def _run_request(self) -> None:
        self.results.show_waiting()
        if self.stack.currentWidget() is not self.results:
            self.push(self.results)
        request = self.last_request

        def done(results):
            reproduced = self.cursor is not None
            self.results.show_results(request.heading, results,
                                      engine.can_reproduce(results) and not reproduced, reproduced)
            self.cursor = None
        self.background(lambda: request.run(self._fresh_system()), done)

    def check_entry(self, entry: Entry) -> None:
        self.last_request = None
        self._diagnose_entry(entry)

    def _diagnose_entry(self, entry: Entry) -> None:
        self.fix.show_waiting()
        if self.stack.currentWidget() is not self.fix:
            self.push(self.fix)

        def work():
            return self._with_plan(engine.diagnose_entry(entry, self._fresh_system()))
        self.background(work, self._show_fix)

    def open_diagnosis(self, diagnosis: engine.Diagnosis) -> None:
        self.fix.show_waiting()
        self.push(self.fix)
        self.background(lambda: self._with_plan(diagnosis), self._show_fix)

    def _with_plan(self, diagnosis):
        try:
            return diagnosis, engine.build_plan(diagnosis, self.make_system()), None
        except PlanError as err:
            return diagnosis, None, str(err)

    def _show_fix(self, result) -> None:
        diagnosis, plan, error = result
        self.fix.show_diagnosis(diagnosis, plan, error, self.system.os_name,
                                diagnosis.entry.id in self.dismissed.ids())
        self.cursor = None
        self.stack.currentWidget().verticalScrollBar().setValue(0)

    # Reproducing

    def start_reproduce(self) -> None:
        current = self.stack.currentWidget()
        entry = self.fix.diagnosis.entry if current is self.fix else None
        prompt = (entry.reproduce if entry else None) or "Do whatever makes the problem show up."
        self.reproduce_for = entry
        self.reproduce.set_prompt(prompt)
        self.push(self.reproduce)
        self._run(lambda: self.make_system().journal_cursor(), lambda cursor: setattr(self, "cursor", cursor))

    def finish_reproduce(self) -> None:
        self._show(self.nav_stack.pop())
        if self.reproduce_for:
            self._diagnose_entry(self.reproduce_for)
        else:
            self._run_request()

    # Applying

    def _start_process(self, script: str, on_finished) -> None:
        self.process = QProcess(self)
        # Script goes in as an argument, not on stdin: tools it runs may prompt, and
        # anything reading stdin would otherwise eat the rest of the script.
        self.process.setStandardInputFile(QProcess.nullDevice())
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        argv = [*self.elevate, "/bin/bash", "-c", script]
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.errorOccurred.connect(lambda err: self._process_error(err, on_finished))
        self.process.finished.connect(lambda code, _status: self._process_done(code, on_finished))
        self._saw_output = False
        self.process.start(argv[0], argv[1:])

    def _read_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self._saw_output = self._saw_output or "==> " in text
        self.apply.add_output(text)

    def _process_error(self, error, on_finished) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.process = None
            self.apply.add_output("Couldn't start pkexec. Is polkit installed?\n")
            on_finished("start_failed")

    def _process_done(self, code: int, on_finished) -> None:
        self.process = None
        # pkexec exits 126 when the dialog is dismissed and 127 when refused, before
        # the script runs. After the script has started those are its own exit codes.
        if code in (126, 127) and not self._saw_output:
            on_finished("cancelled")
        else:
            on_finished("ok" if code == 0 else "failed")

    def apply_plan(self, plan, script: str, edited: bool) -> None:
        self.apply.start(plan.title)
        self.apply.title = "Fix"
        self.push(self.apply)
        self.nav_stack = []
        self.header.back.setVisible(False)

        def finished(outcome):
            if outcome == "ok":
                record = self.history.add(plan, history.APPLIED, edited)
                if plan.reboot:
                    self.apply.finish("success", "Done. Restart your PC to finish.", record, can_reboot=True)
                else:
                    self.apply.finish("success", "Done.", record)
                self.run_scan()
            elif outcome == "failed":
                record = self.history.add(plan, history.FAILED, edited)
                self.apply.finish("danger", "This fix failed partway. Undo restores what it changed.", record)
            elif outcome == "cancelled":
                self.apply.finish("body2", "Cancelled. Nothing was changed.")
            else:
                self.apply.finish("danger", "Couldn't ask for permission to make changes.")
        self._start_process(script, finished)

    def undo_record(self, record: history.Record) -> None:
        self.apply.start(f"Undoing: {record.title}")
        self.apply.title = "Undo"
        self.push(self.apply)
        self.nav_stack = []
        self.header.back.setVisible(False)
        if record.edited:
            self.apply.add_output("You edited this patch, so undo only reverses the original patch's changes.\n")

        def finished(outcome):
            if outcome == "ok":
                self.history.mark(record.run_id, history.UNDONE)
                self.apply.finish("success", "Undone. Restart your PC to finish." if record.reboot else "Undone.",
                                  can_reboot=record.reboot)
                self.run_scan()
            elif outcome == "failed":
                self.apply.finish("danger", "Undo finished with errors. Check the output above.", record)
            elif outcome == "cancelled":
                self.apply.finish("body2", "Cancelled. Nothing was changed.")
            else:
                self.apply.finish("danger", "Couldn't ask for permission to make changes.")
        self._start_process(record.undo_script, finished)
