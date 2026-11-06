import unittest

from nexus_auto_updater.app.ports import FileListing
from nexus_auto_updater.app.scan import ModChecked, ModScanned, ModSkipped, ScanPlan
from nexus_auto_updater.app.session import UpdateSession
from nexus_auto_updater.app.sync import RowChanged, RowsAdded, Sync
from nexus_auto_updater.domain.selection import Verdict
from nexus_auto_updater.tracking.tracker import DownloadTracker
from tests import HAS_QT, QtTestCase
from tests.fakes import (
    GAME,
    ArchiveIds,
    FakeDownloader,
    FakeInstaller,
    FakeLocks,
    FakeScanRunner,
    FakeSource,
    ManualScheduler,
    installed_mod,
)
from tests.test_app_session import selection

if HAS_QT:
    from PyQt6.QtCore import QModelIndex, Qt, qInstallMessageHandler
    from PyQt6.QtTest import QAbstractItemModelTester
    from PyQt6.QtWidgets import QStyleOptionViewItem

    from nexus_auto_updater.ui.header import CenteredCheckDelegate
    from nexus_auto_updater.ui.table_model import (
        COL_CHECK,
        COL_DETAILS,
        COL_INSTALLED,
        COL_LOCK,
        COL_MOD,
        COL_STATUS,
        COL_TARGET,
        HideCurrentProxy,
        UpdatesModel,
    )


class ModelTestCase(QtTestCase):
    def setUp(self):
        super().setUp()
        self.ids = ArchiveIds()
        self.tracker = DownloadTracker(self.ids, ManualScheduler())
        self.locks = FakeLocks()
        self.session = UpdateSession(FakeDownloader(), FakeInstaller(), self.tracker, self.locks, lambda: 3)
        self.runner = FakeScanRunner()
        self.sync = Sync(self.session, ScanPlan(FakeSource({}), GAME, [installed_mod(1)]), self.runner)
        self.model = UpdatesModel(self.sync)
        self.proxy = HideCurrentProxy(self.model)
        self.addCleanup(self.proxy.deleteLater)
        self.addCleanup(self.model.deleteLater)
        self.sync.subscribe(self.on_change)
        self.sync.start()

    def on_change(self, change):
        if isinstance(change, RowsAdded):
            self.model.rows_added(change.keys)
        elif isinstance(change, RowChanged):
            self.model.row_changed(change.key)

    def scan(self, mod_id, *selections, name=""):
        ids = tuple(s.installed_file_id for s in selections)
        mod = installed_mod(mod_id, ids, name=name)
        self.runner.deliver(ModScanned(ModChecked(mod, tuple(selections))))

    def index(self, row, column, model=None):
        return (model or self.model).index(row, column)

    def data(self, row, column, role=None, model=None):
        role = Qt.ItemDataRole.DisplayRole if role is None else role
        return self.index(row, column, model).data(role)


class ContractTest(ModelTestCase):
    def test_qt_model_contract_holds_while_rows_arrive_and_change(self):
        messages = []
        previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
        self.addCleanup(qInstallMessageHandler, previous)
        testers = [
            QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
            for model in (self.model, self.proxy)
        ]
        self.scan(1, selection(1), selection(2, Verdict.UP_TO_DATE))
        self.scan(2, selection(3, Verdict.REVIEW))
        self.sync.set_locked(1, True)
        self.sync.set_all_selected(True)
        self.proxy.set_hide_current(False)
        self.runner.deliver(ModScanned(ModSkipped(installed_mod(9), "x")))
        del testers
        self.assertEqual([], messages)
        self.assertEqual((4, 7), (self.model.rowCount(), self.model.columnCount()))

    def test_rows_only_exist_once_announced(self):
        self.assertEqual(0, self.model.rowCount())
        self.scan(1, selection(1), selection(2))
        self.assertEqual(2, self.model.rowCount())

    def test_a_table_has_no_children(self):
        self.scan(1, selection(1))
        parent = self.index(0, 0)
        self.assertEqual((0, 0), (self.model.rowCount(parent), self.model.columnCount(parent)))

    def test_changed_row_reports_the_whole_row(self):
        self.scan(1, selection(1), selection(2))
        changed = []
        self.model.dataChanged.connect(lambda top, bottom: changed.append((top.row(), top.column(), bottom.row(), bottom.column())))
        self.sync.set_selected(self.sync.rows()[1], False)
        self.assertEqual([(1, 0, 1, 6)], changed)


class DisplayTest(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.scan(1, selection(1), selection(2, Verdict.UP_TO_DATE), name="Alpha")

    def test_cells(self):
        self.assertEqual("Alpha", self.data(0, COL_MOD))
        self.assertIn("Nexus mod 1", self.data(0, COL_MOD, Qt.ItemDataRole.ToolTipRole))
        self.assertEqual("File ID 1 [?]", self.data(0, COL_INSTALLED))
        self.assertEqual("File ID 101 [?]", self.data(0, COL_TARGET))
        self.assertEqual("File ID 101\nGroup unknown", self.data(0, COL_TARGET, Qt.ItemDataRole.ToolTipRole))
        self.assertEqual("Ready", self.data(0, COL_STATUS))
        self.assertEqual("why 1", self.data(0, COL_DETAILS))
        self.assertEqual("—", self.data(1, COL_TARGET))
        self.assertEqual("File ID 2 [?]", self.data(1, COL_INSTALLED))
        tooltip = self.data(0, COL_INSTALLED, Qt.ItemDataRole.ToolTipRole)
        self.assertEqual("File ID 1\nGroup unknown", tooltip)
        self.assertIsNone(self.data(0, COL_STATUS, Qt.ItemDataRole.ToolTipRole))
        self.assertIn("Lock", self.data(0, COL_LOCK, Qt.ItemDataRole.ToolTipRole))
        self.assertEqual("Up to date", self.data(1, COL_STATUS))

    def test_colours(self):
        self.assertEqual("#27ae60", self.data(0, COL_STATUS, Qt.ItemDataRole.ForegroundRole).color().name())
        self.assertIsNone(self.data(0, COL_MOD, Qt.ItemDataRole.ForegroundRole))
        self.sync.set_locked(1, True)
        for column in (COL_MOD, COL_INSTALLED, COL_TARGET, COL_STATUS, COL_DETAILS):
            self.assertEqual("#7f8c8d", self.data(0, column, Qt.ItemDataRole.ForegroundRole).color().name())

    def test_details_tooltip_names_the_archive(self):
        key = self.sync.rows()[0]
        self.sync.download_selected()
        path = "C:\\downloads\\File-101.7z"
        self.ids.ids[path] = (1, 101)
        self.tracker.download_finished(path, True)
        tooltip = self.data(0, COL_DETAILS, Qt.ItemDataRole.ToolTipRole)
        self.assertEqual("why 1\nArchive: " + path, tooltip)
        self.assertEqual("Downloaded", self.data(0, COL_STATUS))
        self.assertEqual("why 2", self.data(1, COL_DETAILS))
        self.assertEqual("why 2", self.data(1, COL_DETAILS, Qt.ItemDataRole.ToolTipRole))
        self.assertTrue(key)

    def test_headers(self):
        headers = [self.model.headerData(c, Qt.Orientation.Horizontal) for c in range(7)]
        self.assertEqual(["", "\U0001F512", "Mod", "Installed file", "Update to", "Status", "Details"], headers)
        tip = Qt.ItemDataRole.ToolTipRole
        self.assertEqual("Select / deselect all updates", self.model.headerData(COL_CHECK, Qt.Orientation.Horizontal, tip))
        self.assertIn("Lock", self.model.headerData(COL_LOCK, Qt.Orientation.Horizontal, tip))
        self.assertIsNone(self.model.headerData(COL_MOD, Qt.Orientation.Horizontal, tip))
        self.assertIsNone(self.model.headerData(0, Qt.Orientation.Vertical))
        self.assertIsNone(self.model.headerData(COL_MOD, Qt.Orientation.Horizontal, Qt.ItemDataRole.EditRole))

    def test_invalid_index_and_unused_roles(self):
        self.assertIsNone(self.model.data(QModelIndex()))
        self.assertEqual(Qt.ItemFlag.NoItemFlags, self.model.flags(QModelIndex()))
        self.assertIsNone(self.data(0, COL_MOD, Qt.ItemDataRole.DecorationRole))
        self.assertIsNone(self.data(0, COL_CHECK))
        self.assertIsNone(self.data(0, COL_LOCK))
        self.assertIsNone(self.data(0, COL_LOCK, Qt.ItemDataRole.DecorationRole))


class CheckingTest(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.scan(1, selection(1), selection(2, Verdict.REVIEW), selection(3, Verdict.UP_TO_DATE))

    def state(self, row, column):
        return self.data(row, column, Qt.ItemDataRole.CheckStateRole)

    def flags(self, row, column):
        return self.model.flags(self.index(row, column))

    def test_check_state_follows_the_selection_and_lock(self):
        self.assertEqual([Qt.CheckState.Checked, Qt.CheckState.Unchecked], [self.state(0, COL_CHECK), self.state(1, COL_CHECK)])
        self.assertEqual(Qt.CheckState.Unchecked, self.state(0, COL_LOCK))
        self.sync.set_locked(1, True)
        self.assertEqual(Qt.CheckState.Checked, self.state(0, COL_LOCK))
        self.assertEqual(Qt.CheckState.Unchecked, self.state(0, COL_CHECK))

    def test_flags(self):
        checkable = Qt.ItemFlag.ItemIsUserCheckable
        self.assertTrue(self.flags(0, COL_CHECK) & checkable)
        self.assertTrue(self.flags(1, COL_CHECK) & checkable)
        self.assertFalse(self.flags(2, COL_CHECK) & checkable)
        self.assertFalse(self.flags(0, COL_CHECK) & Qt.ItemFlag.ItemIsSelectable)
        self.assertTrue(self.flags(2, COL_LOCK) & checkable)
        self.assertTrue(self.flags(2, COL_MOD) & Qt.ItemFlag.ItemIsSelectable)
        self.assertFalse(self.flags(2, COL_MOD) & checkable)
        self.sync.set_locked(1, True)
        self.assertFalse(self.flags(0, COL_CHECK) & checkable)

    def test_set_data_accepts_the_enum_and_the_plain_int(self):
        role = Qt.ItemDataRole.CheckStateRole
        self.assertTrue(self.model.setData(self.index(1, COL_CHECK), Qt.CheckState.Checked, role))
        self.assertTrue(self.sync.is_selected(self.sync.rows()[1]))
        self.assertTrue(self.model.setData(self.index(1, COL_CHECK), Qt.CheckState.Unchecked.value, role))
        self.assertFalse(self.sync.is_selected(self.sync.rows()[1]))
        self.assertTrue(self.model.setData(self.index(0, COL_LOCK), Qt.CheckState.Checked.value, role))
        self.assertTrue(self.locks.is_locked(1))
        self.assertTrue(self.model.setData(self.index(0, COL_LOCK), Qt.CheckState.Unchecked, role))
        self.assertFalse(self.locks.is_locked(1))

    def test_set_data_refuses_everything_else(self):
        self.assertFalse(self.model.setData(self.index(0, COL_CHECK), "x", Qt.ItemDataRole.EditRole))
        self.assertFalse(self.model.setData(self.index(0, COL_MOD), 2, Qt.ItemDataRole.CheckStateRole))
        self.assertFalse(self.model.setData(QModelIndex(), 2, Qt.ItemDataRole.CheckStateRole))

    def test_the_delegate_draws_the_state_the_model_reports(self):
        delegate = CenteredCheckDelegate()
        self.addCleanup(delegate.deleteLater)
        for row, column, expected in (
            (0, COL_CHECK, Qt.CheckState.Checked),
            (1, COL_CHECK, Qt.CheckState.Unchecked),
            (0, COL_LOCK, Qt.CheckState.Unchecked),
        ):
            option = QStyleOptionViewItem()
            delegate.initStyleOption(option, self.index(row, column))
            self.assertEqual(expected, option.checkState)
        self.sync.set_locked(1, True)
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, self.index(0, COL_LOCK))
        self.assertEqual(Qt.CheckState.Checked, option.checkState)


class ProxyTest(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.scan(1, selection(1), selection(2, Verdict.UP_TO_DATE))

    def test_up_to_date_rows_are_hidden_by_default_and_shown_on_request(self):
        self.assertEqual(1, self.proxy.rowCount())
        self.assertEqual("Ready", self.data(0, COL_STATUS, model=self.proxy))
        self.proxy.set_hide_current(False)
        self.assertEqual(2, self.proxy.rowCount())
        self.proxy.set_hide_current(True)
        self.assertEqual(1, self.proxy.rowCount())

    def test_entry_of_maps_through_the_filter(self):
        self.proxy.set_hide_current(False)
        self.assertEqual(self.sync.entry(self.sync.rows()[1]), self.proxy.entry_of(self.index(1, 0, self.proxy)))
        self.assertIsNone(self.proxy.entry_of(QModelIndex()))

    def test_editing_through_the_proxy_reaches_the_sync(self):
        key = self.sync.rows()[0]
        self.assertTrue(self.proxy.setData(self.index(0, COL_CHECK, self.proxy), Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole))
        self.assertFalse(self.sync.is_selected(key))


if __name__ == "__main__":
    unittest.main()
