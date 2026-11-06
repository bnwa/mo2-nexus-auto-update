import time
import unittest

from nexus_auto_updater.app.scan import ModChecked, ModScanned
from nexus_auto_updater.app.session import (
    DownloadFailed,
    Downloaded,
    Downloading,
    Idle,
    InstallCancelled,
    Installed,
    Queued,
    UpdateSession,
)
from nexus_auto_updater.app.sync import Sync, RowChanged, RowsAdded
from nexus_auto_updater.domain.selection import Verdict
from nexus_auto_updater.tracking.tracker import DownloadTracker
from tests import HAS_QT, QtTestCase, wait_until
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
    from PyQt6.QtCore import QEvent, QObject, QRect, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTableView

    from nexus_auto_updater.app.scan import ScanPlan
    from nexus_auto_updater.ui.status_delegate import StatusDelegate
    from nexus_auto_updater.ui.table_model import (
        COL_MOD,
        COL_STATUS,
        PROGRESS_ROLE,
        HideCurrentProxy,
        UpdatesModel,
    )


if HAS_QT:

    class PaintCounter(QObject):
        def __init__(self):
            super().__init__()
            self.count = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                self.count += 1
            return False


class StatusTestCase(QtTestCase):
    max_downloads = 1

    def setUp(self):
        super().setUp()
        self.ids = ArchiveIds()
        self.tracker = DownloadTracker(self.ids, ManualScheduler())
        self.installer = FakeInstaller()
        session = UpdateSession(FakeDownloader(), self.installer, self.tracker, FakeLocks(), lambda: self.max_downloads)
        self.runner = FakeScanRunner()
        self.sync = Sync(session, ScanPlan(FakeSource({}), GAME, [installed_mod(1)]), self.runner)
        self.model = UpdatesModel(self.sync)
        self.proxy = HideCurrentProxy(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.delegate = StatusDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_STATUS, self.delegate)
        self.addCleanup(self.table.deleteLater)
        self.sync.subscribe(self.on_change)
        self.sync.start()
        for mod_id in (1, 2, 3):
            self.scan(mod_id, selection(mod_id))
        self.scan(4, selection(4, Verdict.REVIEW))

    def on_change(self, change):
        if isinstance(change, RowsAdded):
            self.model.rows_added(change.keys)
        elif isinstance(change, RowChanged):
            self.model.row_changed(change.key)

    def scan(self, mod_id, *selections):
        ids = tuple(s.installed_file_id for s in selections)
        self.runner.deliver(ModScanned(ModChecked(installed_mod(mod_id, ids), tuple(selections))))

    def finish(self, mod_id, succeeded=True):
        path = f"C:\\downloads\\File-{mod_id}.7z"
        self.ids.ids[path] = (mod_id, mod_id + 100)
        self.tracker.download_finished(path, succeeded)

    def index(self, row):
        return self.proxy.index(row, COL_STATUS)

    def option(self, row):
        option = QStyleOptionViewItem()
        self.delegate.initStyleOption(option, self.index(row))
        return option

    def has_icon(self, row):
        return not self.option(row).icon.isNull()


class ProgressRoleTest(StatusTestCase):
    def test_the_status_cell_reports_its_progress_through_the_proxy(self):
        self.assertEqual(Idle(), self.index(0).data(PROGRESS_ROLE))
        self.sync.download_selected()
        self.assertEqual(Downloading(), self.index(0).data(PROGRESS_ROLE))
        self.assertEqual(Queued(), self.index(1).data(PROGRESS_ROLE))
        self.assertIsNone(self.proxy.index(0, COL_MOD).data(PROGRESS_ROLE))
        self.assertIsNone(self.proxy.index(0, COL_STATUS).data(Qt.ItemDataRole.EditRole))


class IconTest(StatusTestCase):
    def test_each_stage_gets_an_icon_and_a_wider_cell(self):
        plain = QStyledItemDelegate()
        self.addCleanup(plain.deleteLater)
        self.assertFalse(self.has_icon(0))
        self.sync.download_selected()
        self.assertTrue(self.has_icon(0))  # Downloading
        self.assertTrue(self.has_icon(1))  # Queued
        for row in (0, 1):
            base = plain.sizeHint(QStyleOptionViewItem(), self.index(row))
            self.assertGreater(self.delegate.sizeHint(QStyleOptionViewItem(), self.index(row)).width(), base.width())
        self.finish(1, succeeded=False)
        self.assertTrue(self.has_icon(0))  # Download failed
        self.finish(2)
        self.assertTrue(self.has_icon(1))  # Downloaded
        self.sync.install_downloaded(self.sync.status().installable)
        self.assertEqual(Installed, type(self.index(1).data(PROGRESS_ROLE)))
        self.assertTrue(self.has_icon(1))  # Installed
        self.assertFalse(self.has_icon(3))  # Never started

    def test_a_cancelled_install_has_no_icon(self):
        self.installer.result = False
        self.sync.download_selected()
        self.finish(1)
        self.sync.install_downloaded(self.sync.status().installable)
        self.assertEqual(InstallCancelled, type(self.index(0).data(PROGRESS_ROLE)))
        self.assertFalse(self.has_icon(0))

    def test_every_stage_paints(self):
        self.sync.download_selected()
        self.finish(1, succeeded=False)
        self.finish(2)
        self.sync.install_downloaded(self.sync.status().installable)
        image = QImage(240, 24, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        try:
            for row in range(self.proxy.rowCount()):
                option = QStyleOptionViewItem()
                option.rect = QRect(0, 0, 240, 24)
                self.delegate.paint(painter, option, self.index(row))
        finally:
            painter.end()

    def test_the_spinner_turns_with_the_clock(self):
        times = iter([0.0, 0.25])
        delegate = StatusDelegate(self.table, clock=lambda: next(times))
        self.addCleanup(delegate.deleteLater)
        self.sync.download_selected()
        first, second = QStyleOptionViewItem(), QStyleOptionViewItem()
        delegate.initStyleOption(first, self.index(0))
        delegate.initStyleOption(second, self.index(0))
        self.assertNotEqual(first.icon.pixmap(14, 14).toImage(), second.icon.pixmap(14, 14).toImage())


class AnimationTest(StatusTestCase):
    def test_the_repaint_timer_runs_only_when_asked(self):
        self.assertFalse(self.delegate.animating)
        self.delegate.set_animating(True)
        self.delegate.set_animating(True)
        self.assertTrue(self.delegate.animating)
        self.delegate.set_animating(False)
        self.assertFalse(self.delegate.animating)
        self.delegate.set_animating(False)
        self.assertFalse(self.delegate.animating)

    def test_a_running_timer_repaints_the_table_and_a_stopped_one_does_not(self):
        counter = PaintCounter()
        self.addCleanup(counter.deleteLater)
        self.table.resize(600, 200)
        self.table.show()
        self.addCleanup(self.table.hide)
        self.table.viewport().installEventFilter(counter)
        wait_until(lambda: counter.count > 0)
        self.delegate.set_animating(True)
        self.addCleanup(self.delegate.set_animating, False)
        seen = counter.count
        wait_until(lambda: counter.count > seen)
        self.delegate.set_animating(False)
        wait_until(lambda: True)
        seen = counter.count
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            wait_until(lambda: True)
        self.assertEqual(seen, counter.count)


if __name__ == "__main__":
    unittest.main()
