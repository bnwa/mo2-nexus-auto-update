from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import QHeaderView, QStyle, QStyleOptionButton


class CheckableHeader(QHeaderView):
    """Horizontal header that draws a select-all checkbox in one section."""

    toggled = pyqtSignal(bool)

    def __init__(self, column: int, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._column = column
        self._checked = False
        self._enabled = False
        self.setSectionsClickable(True)

    def set_state(self, checked: bool, enabled: bool) -> None:
        if (checked, enabled) != (self._checked, self._enabled):
            self._checked, self._enabled = checked, enabled
            self.viewport().update()

    def paintSection(self, painter, rect, logical_index) -> None:
        painter.save()
        super().paintSection(painter, rect, logical_index)
        painter.restore()
        if logical_index != self._column:
            return

        style = self.style()
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

    def mousePressEvent(self, event) -> None:
        if self.logicalIndexAt(event.position().toPoint()) == self._column:
            if self._enabled:
                self.toggled.emit(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)
