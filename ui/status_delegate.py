import time
from typing import Callable, Optional, assert_never

from PyQt6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from ..app.session import (
    DownloadFailed,
    Downloaded,
    Downloading,
    Idle,
    InstallCancelled,
    Installed,
    Progress,
    Queued,
    Unmatched,
)
from .qt_types import present
from .table_model import COL_STATUS, PROGRESS_ROLE

ICON_SIZE = 14
FRAME_MS = 50
SPIN_PERIOD_S = 1.0

# Draws one stage into a square of the icon, given the spinner's angle in degrees.
StagePainter = Callable[[QPainter, QRectF, float], None]


def _queued(painter: QPainter, box: QRectF, angle: float) -> None:
    painter.setOpacity(0.4)
    painter.drawEllipse(box)


def _downloading(painter: QPainter, box: QRectF, angle: float) -> None:
    painter.drawArc(box, int(-angle * 16), 270 * 16)


def _done(painter: QPainter, box: QRectF, angle: float) -> None:
    painter.drawPolyline(
        QPolygonF(
            [
                QPointF(box.left(), box.center().y()),
                QPointF(box.left() + box.width() * 0.4, box.bottom() - box.height() * 0.1),
                QPointF(box.right(), box.top() + box.height() * 0.1),
            ]
        )
    )


def _failed(painter: QPainter, box: QRectF, angle: float) -> None:
    inset = box.adjusted(box.width() * 0.1, box.height() * 0.1, -box.width() * 0.1, -box.height() * 0.1)
    painter.drawLine(inset.topLeft(), inset.bottomRight())
    painter.drawLine(inset.topRight(), inset.bottomLeft())


def _stage_painter(progress: Progress) -> Optional[StagePainter]:
    """How a started update is drawn; ``None`` while there is nothing to show."""
    match progress:
        case Idle() | InstallCancelled():
            return None
        case Queued():
            return _queued
        case Downloading():
            return _downloading
        case Downloaded() | Installed():
            return _done
        case Unmatched() | DownloadFailed():
            return _failed
        case _:
            assert_never(progress)


class StatusDelegate(QStyledItemDelegate):
    """Draws a small icon for the stage of a download before the status text.

    Qt's own drawing still handles selection, elision and the row's text colour, because
    the icon is handed to it as the item's decoration.
    """

    def __init__(self, view: QTableView, clock: Callable[[], float] = time.monotonic):
        super().__init__(view)
        self._view = view
        self._clock = clock
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._repaint)

    @property
    def animating(self) -> bool:
        return self._timer.isActive()

    def set_animating(self, animating: bool) -> None:
        if not animating:
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start()

    def initStyleOption(self, option: Optional[QStyleOptionViewItem], index: QModelIndex) -> None:
        super().initStyleOption(option, index)
        option = present(option)
        progress = index.data(PROGRESS_ROLE)
        draw = _stage_painter(progress) if progress is not None else None
        if draw is None:
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        angle = self._clock() % SPIN_PERIOD_S / SPIN_PERIOD_S * 360
        option.icon = QIcon(self._pixmap(draw, option.palette.color(role), angle))
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
        option.decorationSize = QSize(ICON_SIZE, ICON_SIZE)

    def _pixmap(self, draw: StagePainter, colour: QColor, angle: float) -> QPixmap:
        ratio = self._view.devicePixelRatioF()
        pixmap = QPixmap(round(ICON_SIZE * ratio), round(ICON_SIZE * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(colour, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        draw(painter, QRectF(1.5, 1.5, ICON_SIZE - 3, ICON_SIZE - 3), angle)
        painter.end()
        return pixmap

    def _repaint(self) -> None:
        view = self._view
        column = QRect(
            view.columnViewportPosition(COL_STATUS),
            0,
            view.columnWidth(COL_STATUS),
            present(view.viewport()).height(),
        )
        present(view.viewport()).update(column)

