from typing import Optional, Tuple

from PyQt6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QWidget,
)

from .qt_types import present


class CheckableHeader(QHeaderView):
    """Horizontal header that draws a select-all checkbox in one section."""

    toggled = pyqtSignal(bool)

    def __init__(self, column: int, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._column = column
        self._checked = False
        self._enabled = False
        self.setSectionsClickable(True)

    @property
    def checked(self) -> bool:
        return self._checked

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_state(self, checked: bool, enabled: bool) -> None:
        if (checked, enabled) != (self._checked, self._enabled):
            self._checked, self._enabled = checked, enabled
            present(self.viewport()).update()

    def paintSection(self, painter: Optional[QPainter], rect: QRect, logicalIndex: int) -> None:
        painter = present(painter)
        painter.save()
        super().paintSection(painter, rect, logicalIndex)
        painter.restore()
        if logicalIndex != self._column:
            return

        style = present(self.style())
        width = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self)
        height = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, None, self)
        option = QStyleOptionButton()
        option.rect = QRect(
            rect.x() + (rect.width() - width) // 2,
            rect.y() + (rect.height() - height) // 2,
            width,
            height,
        )
        option.state = QStyle.StateFlag.State_On if self._checked else QStyle.StateFlag.State_Off
        if self._enabled:
            option.state |= QStyle.StateFlag.State_Enabled
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, option, painter, self)

    def mousePressEvent(self, e: Optional[QMouseEvent]) -> None:
        e = present(e)
        if self.logicalIndexAt(e.position().toPoint()) == self._column:
            if self._enabled:
                self.toggled.emit(not self._checked)
            e.accept()
            return
        super().mousePressEvent(e)


class CenteredCheckDelegate(QStyledItemDelegate):
    """Draws and toggles an item's checkbox centred in its cell, under the header's.

    Qt always puts an item's check indicator at the cell's left edge.
    """

    def paint(self, painter: Optional[QPainter], option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter = present(painter)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style, widget = _style(opt)
        checked = opt.checkState == Qt.CheckState.Checked
        indicator = self._indicator_rect(opt)

        # Background and selection, without Qt's left-aligned indicator.
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        check = QStyleOptionViewItem(opt)
        check.rect = indicator
        check.state &= ~QStyle.StateFlag.State_HasFocus
        check.state |= QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off
        if not index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            check.state &= ~QStyle.StateFlag.State_Enabled
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, check, painter, widget
        )

    def editorEvent(
        self,
        event: Optional[QEvent],
        model: Optional[QAbstractItemModel],
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        event, model = present(event), present(model)
        flags = index.flags()
        if not (flags & Qt.ItemFlag.ItemIsUserCheckable and flags & Qt.ItemFlag.ItemIsEnabled):
            return False
        kind = event.type()
        if isinstance(event, QMouseEvent) and kind in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            hit = self._indicator_rect(opt).contains(event.position().toPoint())
            if not hit or event.button() != Qt.MouseButton.LeftButton:
                return False
            # Toggle on the button going down: a quick second click arrives as a
            # double-click instead of a press, so each click is exactly one of them.
            if kind == QEvent.Type.MouseButtonRelease:
                return True
        elif isinstance(event, QKeyEvent) and kind == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            return False

        state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
        new_state = (
            Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked
        )
        # As an int, like Qt's own delegate.
        return model.setData(index, new_state.value, Qt.ItemDataRole.CheckStateRole)

    @staticmethod
    def _indicator_rect(opt: QStyleOptionViewItem) -> QRect:
        style, widget = _style(opt)
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget
        )
        rect.moveCenter(opt.rect.center())
        return rect


def _style(opt: QStyleOptionViewItem) -> Tuple[QStyle, Optional[QWidget]]:
    widget = opt.widget
    return present(widget.style() if widget is not None else QApplication.style()), widget
