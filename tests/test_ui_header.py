import unittest

from tests import HAS_QT, QtTestCase, wait_until

if HAS_QT:
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QKeyEvent, QMouseEvent
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QStyleOptionViewItem, QTableWidget, QTableWidgetItem

    from nexus_auto_updater.ui.header import CenteredCheckDelegate, CheckableHeader


class CheckableHeaderTest(QtTestCase):
    def setUp(self):
        self.table = QTableWidget(2, 3)
        self.addCleanup(self.table.deleteLater)
        self.header = CheckableHeader(0, self.table)
        self.table.setHorizontalHeader(self.header)
        self.table.setHorizontalHeaderLabels(["", "Lock", "Mod"])
        self.table.resize(400, 200)
        self.table.show()
        wait_until(self.table.isVisible)
        self.toggles = []
        self.header.toggled.connect(self.toggles.append)
        self.pressed = []
        self.header.sectionPressed.connect(self.pressed.append)

    def click(self, column):
        x = self.header.sectionViewportPosition(column) + self.header.sectionSize(column) // 2
        QTest.mouseClick(self.header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, self.header.height() // 2))

    def test_starts_unchecked_and_disabled(self):
        self.assertEqual((False, False), (self.header.checked, self.header.enabled))
        self.assertTrue(self.header.sectionsClickable())
        self.assertIs(Qt.Orientation.Horizontal, self.header.orientation())

    def test_click_on_disabled_check_column_does_nothing(self):
        self.click(0)
        self.assertEqual([], self.toggles)
        self.assertEqual([], self.pressed)

    def test_click_on_enabled_check_column_toggles(self):
        self.header.set_state(False, True)
        self.click(0)
        self.assertEqual([True], self.toggles)
        self.header.set_state(True, True)
        self.click(0)
        self.assertEqual([True, False], self.toggles)
        self.assertEqual([], self.pressed)

    def test_clicks_elsewhere_pass_through(self):
        self.header.set_state(False, True)
        self.click(2)
        self.assertEqual([], self.toggles)
        self.assertEqual([2], self.pressed)

    def test_set_state(self):
        self.header.set_state(True, True)
        self.assertEqual((True, True), (self.header.checked, self.header.enabled))
        self.header.set_state(True, True)  # Unchanged: no repaint needed.
        self.header.set_state(False, False)
        self.assertEqual((False, False), (self.header.checked, self.header.enabled))

    def test_painting_every_state(self):
        for checked, enabled in ((False, False), (True, True), (True, False), (False, True)):
            with self.subTest(checked=checked, enabled=enabled):
                self.header.set_state(checked, enabled)
                self.assertFalse(self.header.grab().isNull())


class CenteredCheckDelegateTest(QtTestCase):
    def setUp(self):
        self.table = QTableWidget(3, 1)
        self.addCleanup(self.table.deleteLater)
        self.delegate = CenteredCheckDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self.delegate)
        self.table.resize(300, 200)
        self.table.setColumnWidth(0, 60)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        for row, item_flags in enumerate((flags, Qt.ItemFlag.ItemIsEnabled, Qt.ItemFlag.ItemIsUserCheckable)):
            item = QTableWidgetItem()
            item.setFlags(item_flags)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, item)
        self.table.show()
        wait_until(self.table.isVisible)

    def option(self, row, with_widget=True):
        option = QStyleOptionViewItem()
        option.rect = self.table.visualRect(self.table.model().index(row, 0))
        if with_widget:
            option.widget = self.table
        return option

    def mouse(self, kind, row=0, button=None, offset=None):
        button = Qt.MouseButton.LeftButton if button is None else button
        center = self.option(row).rect.center()
        point = QPointF(center if offset is None else center + offset)
        return QMouseEvent(kind, point, point, button, button, Qt.KeyboardModifier.NoModifier)

    def send(self, event, row=0, with_widget=True):
        index = self.table.model().index(row, 0)
        return self.delegate.editorEvent(event, self.table.model(), self.option(row, with_widget), index)

    def state(self, row=0):
        return self.table.item(row, 0).checkState()

    def test_press_on_indicator_toggles(self):
        self.assertTrue(self.send(self.mouse(QEvent.Type.MouseButtonPress)))
        self.assertEqual(Qt.CheckState.Checked, self.state())
        self.assertTrue(self.send(self.mouse(QEvent.Type.MouseButtonPress)))
        self.assertEqual(Qt.CheckState.Unchecked, self.state())

    def test_release_is_consumed_without_toggling(self):
        self.assertTrue(self.send(self.mouse(QEvent.Type.MouseButtonRelease)))
        self.assertEqual(Qt.CheckState.Unchecked, self.state())

    def test_double_click_toggles(self):
        self.assertTrue(self.send(self.mouse(QEvent.Type.MouseButtonDblClick)))
        self.assertEqual(Qt.CheckState.Checked, self.state())

    def test_miss_or_other_button_is_ignored(self):
        self.assertFalse(self.send(self.mouse(QEvent.Type.MouseButtonPress, offset=QPoint(25, 0))))
        self.assertFalse(self.send(self.mouse(QEvent.Type.MouseButtonPress, button=Qt.MouseButton.RightButton)))
        self.assertEqual(Qt.CheckState.Unchecked, self.state())

    def test_space_and_select_toggle_other_keys_are_ignored(self):
        for key, expected in ((Qt.Key.Key_Space, Qt.CheckState.Checked), (Qt.Key.Key_Select, Qt.CheckState.Unchecked)):
            event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
            self.assertTrue(self.send(event))
            self.assertEqual(expected, self.state())
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(self.send(event))
        self.assertEqual(Qt.CheckState.Unchecked, self.state())

    def test_other_event_types_are_ignored(self):
        self.assertFalse(self.send(QEvent(QEvent.Type.HoverEnter)))
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(self.send(release))

    def test_non_checkable_or_disabled_items_are_ignored(self):
        for row in (1, 2):
            with self.subTest(row=row):
                self.assertFalse(self.send(self.mouse(QEvent.Type.MouseButtonPress, row), row))
                self.assertEqual(Qt.CheckState.Unchecked, self.state(row))

    def test_option_without_widget_uses_the_application_style(self):
        self.assertTrue(self.send(self.mouse(QEvent.Type.MouseButtonPress), with_widget=False))
        self.assertEqual(Qt.CheckState.Checked, self.state())

    def test_check_state_is_readable_by_the_item(self):
        self.send(self.mouse(QEvent.Type.MouseButtonPress))
        self.assertTrue(self.table.item(0, 0).checkState() == Qt.CheckState.Checked)

    def test_real_click_toggles_once(self):
        rect = self.table.visualItemRect(self.table.item(0, 0))
        QTest.mouseClick(self.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual(Qt.CheckState.Checked, self.state())

    def test_paint(self):
        self.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self.table.selectRow(0)
        self.assertFalse(self.table.grab().isNull())


if __name__ == "__main__":
    unittest.main()
