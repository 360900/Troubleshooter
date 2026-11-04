"""Applies oneDesign to Qt: fonts, stylesheet, tinted Adwaita symbolic icons. Follows the OS light/dark."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .tokens import TOKENS, resolve

ASSETS = Path(__file__).parent / "assets"
SHAPE = TOKENS["shape"]
DENSITY = TOKENS["density"]
UI_FONT = "Inter Variable"
MONO_FONT = "Geist Mono"
SHADOW = 18  # room around the window for its painted shadow


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.dark = False
        self.maximized = False
        self.c = resolve(False)

    def setup(self, app) -> None:
        for font in ("InterVariable.ttf", "GeistMono.ttf"):
            QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / font))
        hints = app.styleHints()
        hints.colorSchemeChanged.connect(lambda _: self.refresh(app))
        self.refresh(app)

    def refresh(self, app) -> None:
        self.dark = self.is_dark(app)
        self.c = resolve(self.dark)
        app.setStyleSheet(self.stylesheet())
        self.changed.emit()

    def is_dark(self, app) -> bool:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Unknown:  # no desktop setting to read: go by the palette
            return app.palette().window().color().lightness() < 128
        return scheme == Qt.ColorScheme.Dark

    def set_maximized(self, value: bool) -> None:
        if value != self.maximized:
            self.maximized = value
            QGuiApplication.instance().setStyleSheet(self.stylesheet())

    def stylesheet(self) -> str:
        c = {k: v.css for k, v in self.c.items()}
        r_win = 0 if self.maximized else SHAPE["window"]
        r_box, r_ctl, r_sm = SHAPE["container"], SHAPE["control"], SHAPE["small"]
        ctl = DENSITY["control"]
        return f"""
* {{ font-family: "{UI_FONT}", "Inter", sans-serif; font-size: 14px; color: {c['text']}; outline: none; }}
QWidget#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['stroke']};
    border-top-left-radius: {r_win}px; border-bottom-left-radius: {r_win}px; }}
QWidget#content {{ background: {c['canvas']};
    border-top-right-radius: {r_win}px; border-bottom-right-radius: {r_win}px; }}
QWidget#transparent, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

QLabel {{ background: transparent; }}
QLabel[role="display"] {{ font-size: 28px; font-weight: 750; }}
QLabel[role="title1"] {{ font-size: 28px; font-weight: 750; }}
QLabel[role="title2"] {{ font-size: 22px; font-weight: 750; }}
QLabel[role="title3"] {{ font-size: 18px; font-weight: 650; }}
QLabel[role="heading"] {{ font-size: 15px; font-weight: 700; }}
QLabel[role="strong"] {{ font-weight: 600; }}
QLabel[role="body2"] {{ color: {c['text2']}; }}
QLabel[role="caption"] {{ font-size: 12px; color: {c['text2']}; }}
QLabel[role="caption3"] {{ font-size: 12px; color: {c['text3']}; }}
QLabel[role="mono"] {{ font-family: "{MONO_FONT}", monospace; font-size: 12px; color: {c['text2']}; }}
QLabel[role="danger"] {{ color: {c['danger']}; font-weight: 600; }}
QLabel[role="warning"] {{ color: {c['warning']}; font-weight: 600; }}
QLabel[role="success"] {{ color: {c['success']}; font-weight: 600; }}
QLabel[role="section"] {{ font-size: 15px; font-weight: 700; }}

QLabel[pill="high"] {{ background: {c['successSoft']}; color: {c['success']}; }}
QLabel[pill="medium"] {{ background: {c['warningSoft']}; color: {c['warning']}; }}
QLabel[pill="low"] {{ background: {c['fill']}; color: {c['text2']}; }}
QLabel[pill] {{ border-radius: {r_sm}px; padding: 2px 8px; font-size: 12px; font-weight: 600; }}

QPushButton {{ background: {c['fill']}; border: none; border-radius: {r_ctl}px; min-height: {ctl}px;
    padding: 0 14px; font-weight: 600; }}
QPushButton:hover {{ background: {c['fillStrong']}; }}
QPushButton:pressed {{ background: {c['strokeStrong']}; }}
QPushButton:focus {{ border: 2px solid {c['accent']}; }}
QPushButton:disabled {{ color: {c['text3']}; background: {c['fill']}; }}
QPushButton[kind="suggested"] {{ background: {c['accent']}; color: {c['onAccent']}; }}
QPushButton[kind="suggested"]:hover {{ background: {c['accentHover']}; }}
QPushButton[kind="suggested"]:pressed {{ background: {c['accentPressed']}; }}
QPushButton[kind="suggested"]:disabled {{ background: {c['fill']}; color: {c['text3']}; }}
QPushButton[kind="danger"] {{ background: {c['danger']}; color: {c['onDanger']}; }}
QPushButton[kind="flat"] {{ background: transparent; }}
QPushButton[kind="flat"]:hover {{ background: {c['hover']}; }}
QPushButton[kind="round"] {{ background: {c['fill']}; border-radius: 12px; min-height: 24px; max-height: 24px;
    min-width: 24px; max-width: 24px; padding: 0; }}
QPushButton[kind="round"]:hover {{ background: {c['fillStrong']}; }}
QPushButton[kind="headerback"] {{ background: transparent; min-width: {ctl}px; max-width: {ctl}px; padding: 0; }}
QPushButton[kind="headerback"]:hover {{ background: {c['hover']}; }}
QPushButton[kind="chip"] {{ min-height: 28px; padding: 0 12px; font-size: 12px; background: {c['surface']};
    border: 1px solid {c['stroke']}; border-radius: {r_sm}px; font-weight: 500; }}
QPushButton[kind="chip"]:hover {{ background: {c['hover']}; }}

QPushButton#nav {{ background: transparent; padding: 0; min-height: {ctl}px; border-radius: {r_ctl}px; }}
QPushButton#nav:hover {{ background: {c['hover']}; }}
QPushButton#nav:checked {{ background: {c['container']}; }}
QLabel[role="nav"] {{ font-weight: 450; }}
QLabel[role="navActive"] {{ color: {c['onContainer']}; font-weight: 600; }}
QPushButton#nav:focus {{ border: none; }}

QLineEdit {{ background: {c['fill']}; border: none; border-radius: {r_ctl}px; min-height: {ctl}px;
    padding: 0 12px; selection-background-color: {c['accent']}; selection-color: {c['onAccent']}; }}
QLineEdit:focus {{ background: {c['surface']}; border: 2px solid {c['accent']}; padding: 0 10px; }}

QFrame#box {{ background: {c['surface']}; border: 1px solid {c['stroke']}; border-radius: {r_box}px; }}
QFrame#row {{ background: transparent; border: none; }}
QFrame#row[divider="true"] {{ border-top: 1px solid {c['stroke']}; border-top-left-radius: 0; border-top-right-radius: 0; }}
QFrame#row[clickable="true"]:hover {{ background: {c['hover']}; }}
QFrame#row[first="true"] {{ border-top-left-radius: {r_box - 1}px; border-top-right-radius: {r_box - 1}px; }}
QFrame#row[last="true"] {{ border-bottom-left-radius: {r_box - 1}px; border-bottom-right-radius: {r_box - 1}px; }}
QFrame#tile {{ background: {c['fill']}; border-radius: {r_sm + 2}px; }}
QFrame#tile[tone="accent"] {{ background: {c['container']}; }}
QFrame#tile[tone="success"] {{ background: {c['successSoft']}; }}
QFrame#tile[tone="warning"] {{ background: {c['warningSoft']}; }}
QFrame#tile[tone="danger"] {{ background: {c['dangerSoft']}; }}
QFrame#notice {{ background: {c['container']}; border-radius: {r_box}px; }}
QFrame#notice QLabel {{ color: {c['onContainer']}; }}

QPlainTextEdit {{ background: {c['surface']}; border: 1px solid {c['stroke']}; border-radius: {r_box}px;
    padding: 8px; font-family: "{MONO_FONT}", monospace; font-size: 12px;
    selection-background-color: {c['accent']}; selection-color: {c['onAccent']}; }}
QPlainTextEdit[editing="true"] {{ border: 1px solid {c['danger']}; }}

QAbstractScrollArea::corner {{ background: transparent; }}
QFrame#tile QLabel[role="number"] {{ font-size: 12px; font-weight: 600; color: {c['text2']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['fillStrong']}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {c['strokeStrong']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['fillStrong']}; border-radius: 3px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QToolTip {{ background: {c['float']}; color: {c['text']}; border: 1px solid {c['stroke']}; border-radius: {r_sm}px; padding: 4px 8px; }}
"""


theme = Theme()


@lru_cache(maxsize=512)
def _render(name: str, color: str, size: int, scale: float) -> QPixmap:
    svg = (ASSETS / "icons" / f"{name}.svg").read_text()
    svg = re.sub(r'fill="(?!none)[^"]*"', f'fill="{color}"', svg)
    svg = re.sub(r"fill:\s*#[0-9a-fA-F]+", f"fill:{color}", svg)
    px = round(size * scale)
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, px, px))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def icon_pixmap(name: str, role: str = "text", size: int = 16, scale: float = 2.0) -> QPixmap:
    return _render(name, theme.c[role].hex, size, scale)


def icon(name: str, role: str = "text", size: int = 16) -> QIcon:
    return QIcon(icon_pixmap(name, role, size))

