"""oneDesign components: boxed lists, rows, buttons, the confidence meter."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from .theme import icon, icon_pixmap, theme

CONTENT_WIDTH = 720


def qcolor(role: str) -> QColor:
    c = theme.c[role]
    return QColor.fromRgbF(c.r, c.g, c.b, c.a)


def label(text: str = "", role: str | None = None, wrap: bool = True, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class Icon(QLabel):
    """A symbolic icon that recolors itself when the theme changes."""

    def __init__(self, name: str, role: str = "text", size: int = 16):
        super().__init__()
        self._spec = (name, role, size)
        self.setFixedSize(size, size)
        theme.changed.connect(self._paint)
        self._paint()

    def _paint(self) -> None:
        self.setPixmap(icon_pixmap(*self._spec))

    def set_icon(self, name: str, role: str = "text") -> None:
        self._spec = (name, role, self._spec[2])
        self._paint()


def tile(name: str, tone: str | None = None) -> QFrame:
    """The small rounded square holding a row's icon."""
    frame = QFrame()
    frame.setObjectName("tile")
    frame.setFixedSize(32, 32)
    if tone:
        frame.setProperty("tone", tone)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    role = {"accent": "onContainer", "success": "success", "warning": "warning", "danger": "danger"}.get(tone, "text")
    layout.addWidget(Icon(name, role), alignment=Qt.AlignmentFlag.AlignCenter)
    return frame


def number_tile(number: int) -> QFrame:
    frame = QFrame()
    frame.setObjectName("tile")
    frame.setFixedSize(32, 32)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(label(str(number), "number", wrap=False), alignment=Qt.AlignmentFlag.AlignCenter)
    return frame


class Button(QPushButton):
    """Uses a bound method for theme changes, so Qt disconnects it when the button is deleted."""

    def __init__(self, text: str, kind: str | None, icon_name: str | None, icon_size: int):
        super().__init__(text)
        if kind:
            self.setProperty("kind", kind)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon = icon_name
        self._icon_size = icon_size
        self._role = {"suggested": "onAccent", "danger": "onDanger"}.get(kind, "text")
        if icon_name:
            self.setIconSize(QSize(icon_size, icon_size))
            theme.changed.connect(self._paint_icon)
            self._paint_icon()

    def _paint_icon(self) -> None:
        self.setIcon(icon(self._icon, self._role, self._icon_size))


def button(text: str, kind: str | None = None, icon_name: str | None = None, icon_size: int = 16) -> QPushButton:
    return Button(text, kind, icon_name, icon_size)


class Row(QFrame):
    """A preference-style list row."""
    clicked = Signal()

    def __init__(self, title: str, subtitle: str = "", icon_name: str | None = None, tone: str | None = None,
                 trailing: QWidget | None = None, chevron: bool = False, on_click=None, leading: QWidget | None = None):
        super().__init__()
        self.setObjectName("row")
        self.setMinimumHeight(52)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)
        if leading:
            layout.addWidget(leading)
        if icon_name:
            layout.addWidget(tile(icon_name, tone))
        text = QVBoxLayout()
        text.setSpacing(0)
        text.setContentsMargins(0, 0, 0, 0)
        self.title = label(title)
        text.addWidget(self.title)
        self.subtitle = None
        if subtitle:
            self.subtitle = label(subtitle, "caption")
            text.addWidget(self.subtitle)
        self.extra = text
        layout.addLayout(text, 1)
        if trailing:
            layout.addWidget(trailing, alignment=Qt.AlignmentFlag.AlignVCenter)
        if chevron:
            layout.addWidget(Icon("go-next", "text2", 12))
        if on_click:
            self.clicked.connect(on_click)
            self.setProperty("clickable", True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if self.property("clickable") and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class BoxedList(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("box")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(0)
        self.rows: list[QWidget] = []

    def add(self, row: QWidget) -> QWidget:
        self._layout.addWidget(row)
        self.rows.append(row)
        for index, item in enumerate(self.rows):
            item.setProperty("first", index == 0)
            item.setProperty("last", index == len(self.rows) - 1)
            item.setProperty("divider", index > 0)
            repolish(item)
        return row


def pill(text: str, level: str) -> QLabel:
    widget = label(text, wrap=False)
    widget.setProperty("pill", level)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


class Meter(QWidget):
    """Thin bar showing a confidence value."""

    def __init__(self, value: int, role: str):
        super().__init__()
        self.value, self.role = value, role
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        painter.setBrush(qcolor("fillStrong"))
        painter.drawRoundedRect(rect, 3, 3)
        if self.value > 0:
            rect.setWidth(max(6.0, rect.width() * self.value / 100))
            painter.setBrush(qcolor(self.role))
            painter.drawRoundedRect(rect, 3, 3)


class Spinner(QWidget):
    """An indeterminate ring; stops itself when hidden."""

    def __init__(self, size: int = 20):
        super().__init__()
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = self.startTimer(40)

    def timerEvent(self, event):
        self._angle = (self._angle + 12) % 360
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qcolor("accent"), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.drawArc(rect, -self._angle * 16, 270 * 16)


class CheckBox(QAbstractButton):
    """Rounded check per oneDesign, with a label in the given role color."""

    def __init__(self, text: str, role: str = "text", bold: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setText(text)
        self.role = role
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        font = self.font()
        font.setBold(bold)
        self.setFont(font)
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 34, max(metrics.height() + 8, 28))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1, (self.height() - 20) / 2, 20, 20)
        path = QPainterPath()
        path.addRoundedRect(box, 6, 6)
        if self.isChecked():
            painter.fillPath(path, qcolor("danger" if self.role == "danger" else "accent"))
            painter.drawPixmap(int(box.x()) + 2, int(box.y()) + 2,
                               icon_pixmap("object-select", "onDanger" if self.role == "danger" else "onAccent"))
        else:
            painter.setPen(QPen(qcolor(self.role if self.hasFocus() else "strokeStrong"), 1.5))
            painter.drawPath(path)
        painter.setPen(qcolor(self.role))
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(30, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.text())


class Page(QScrollArea):
    """A scrolling page with a centered column at oneDesign's reading width."""
    title = ""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QWidget()
        outer.setObjectName("transparent")
        row = QHBoxLayout(outer)
        row.setContentsMargins(24, 12, 24, 32)
        self.column = QWidget()
        self.column.setObjectName("transparent")
        self.column.setMaximumWidth(CONTENT_WIDTH)
        self.body = QVBoxLayout(self.column)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        row.addWidget(self.column, 100)
        row.addStretch(1)
        self.setWidget(outer)

    def section(self, text: str, note: str = "") -> None:
        header = QHBoxLayout()
        header.setSpacing(8)
        header.setContentsMargins(2, 12, 2, 0)
        header.addWidget(label(text, "section", wrap=False))
        if note:
            caption = label(note, "caption")
            header.addWidget(caption, 1)
        else:
            header.addStretch(1)
        self.body.addLayout(header)

    def reset(self) -> None:
        clear_layout(self.body)


def hbox(*items, spacing: int = 8, stretch_first: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(0, 0, 0, 0)
    if stretch_first:
        layout.addStretch(1)
    for item in items:
        if item is None:
            layout.addStretch(1)
        else:
            layout.addWidget(item)
    return layout
