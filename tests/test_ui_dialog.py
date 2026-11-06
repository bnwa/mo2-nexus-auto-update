import threading
import time
from unittest import mock

from nexus_auto_updater.app.notes import ModNotes
from nexus_auto_updater.app.ports import FileListing, SourceError, SourceIncompatible
from nexus_auto_updater.app.scan import ScanPlan
from nexus_auto_updater.app.session import DOWNLOAD_PATIENCE_MS, Installed, Offered, UpdateSession
from nexus_auto_updater.app.sync import Complete, Stopped, Sync
from nexus_auto_updater.tracking.tracker import DownloadTracker
from tests import HAS_QT, QtTestCase, wait_until
from tests.fakes import (
    GAME,
    ArchiveIds,
    FakeDownloader,
    FakeInstaller,
    FakeLocks,
    FakeMatcher,
    FakeScanRunner,
    FakeSource,
    Gate,
    ManualScheduler,
    installed_mod,
    mod_file,
)

if HAS_QT:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QCheckBox,
        QLabel,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QStyleOptionViewItem,
        QTableView,
    )

    from nexus_auto_updater.ui import dialog as dialog_module
    from nexus_auto_updater.ui.dialog import UpdaterDialog
    from nexus_auto_updater.ui.notes import NotesPane
    from nexus_auto_updater.ui.scan_worker import QtScanRunner
    from nexus_auto_updater.ui.table_model import (
        COL_CHECK,
        COL_DETAILS,
        COL_INSTALLED,
        COL_LOCK,
        COL_MOD,
        COL_STATUS,
        COL_TARGET,
    )

DOWNLOADS = "C:\\Modding\\MO2\\downloads\\"
SCOPE = "mods MO2 flags as having an update"
EMPTY = "No mods to check."
GREY, GREEN, BLUE = "#7f8c8d", "#27ae60", "#45beef"


def listing(*files, matchers=None):
    return FileListing(tuple(files), matchers or {})


# Blocked first in the table, then in priority order.
ALPHA = installed_mod(1, (1,), name="Alpha")  # Ready: 1 -> 2
BETA = installed_mod(2, (7,), name="Beta")  # Needs review: 7 unlisted -> primary 50
GAMMA = installed_mod(3, (1,), name="Gamma")  # Up to date
DELTA = installed_mod(4, (), name="Delta", blocker="Mod belongs to Nexus game 'Skyrim'")
EPSILON = installed_mod(5, (1,), name="Epsilon")  # Error
STANDARD_MODS = [ALPHA, BETA, GAMMA, DELTA, EPSILON]
STANDARD_LISTINGS = {
    1: listing(mod_file(1, name="Alpha Main", version="1.0"), mod_file(2, name="Alpha Main", version="1.1")),
    2: listing(mod_file(50, name="Beta Main", is_primary=True, group_id=5)),
    3: listing(mod_file(1)),
    5: SourceError("GraphQL error: Mod not found"),
}


class DialogTestCase(QtTestCase):
    max_downloads = 3

    def open(self, mods=None, listings=None, *, locks=None, installer_result=True, gate_mod=None, threaded=False):
        """Open the dialog; the scan runs synchronously unless ``threaded`` or ``gate_mod`` is set."""
        mods = STANDARD_MODS if mods is None else mods
        self.source = FakeSource(STANDARD_LISTINGS if listings is None else listings,
                                 {m.mod_id: "" for m in mods})
        self.gate = Gate()
        if gate_mod is not None:
            self.source.before_list = lambda mod_id: self.gate.wait() if mod_id == gate_mod else None
        self.archive_ids = ArchiveIds()
        self.scheduler = ManualScheduler()
        self.tracker = DownloadTracker(self.archive_ids, self.scheduler)
        self.downloader = FakeDownloader()
        self.installer = FakeInstaller(installer_result)
        self.locks = locks or FakeLocks()
        self.session = UpdateSession(self.downloader, self.installer, self.tracker, self.locks, lambda: self.max_downloads)
        self.runner = QtScanRunner() if threaded or gate_mod is not None else FakeScanRunner(synchronous=True)
        self.sync = Sync(self.session, ScanPlan(self.source, GAME, mods), self.runner)
        self.dialog = UpdaterDialog(
            None, self.sync, ModNotes(self.source, GAME), scope_label=SCOPE, empty_message=EMPTY
        )
        self.addCleanup(self._close)
        self.table = self.dialog.findChild(QTableView, "updates")
        self.model = self.table.model()
        return self.dialog

    def _close(self):
        self.gate.release.set()
        self.dialog.done(0)
        wait_until(lambda: True)
        self.dialog.deleteLater()
        if isinstance(self.runner, QtScanRunner):
            self.runner.deleteLater()

    def open_scanned(self, *args, **kwargs):
        dialog = self.open(*args, **kwargs)
        self.wait_scanned()
        return dialog

    def wait_scanned(self):
        wait_until(lambda: isinstance(self.sync.status().scan, (Complete, Stopped)))

    def show_up_to_date(self):
        self.dialog.findChild(QCheckBox, "hide_current").setChecked(False)

    # --- Reading what the user sees ---

    def names(self):
        return [self.model.index(r, COL_MOD).data() for r in range(self.model.rowCount())]

    def index(self, mod_name, column, nth=0):
        rows = [r for r, name in enumerate(self.names()) if name == mod_name]
        return self.model.index(rows[nth], column)

    def cell(self, mod_name, column, nth=0, role=None):
        role = Qt.ItemDataRole.DisplayRole if role is None else role
        return self.index(mod_name, column, nth).data(role)

    def status(self, mod_name, nth=0):
        return self.cell(mod_name, COL_STATUS, nth)

    def colour(self, mod_name, column, nth=0):
        return self.cell(mod_name, column, nth, Qt.ItemDataRole.ForegroundRole).color().name()

    def checkable(self, mod_name, nth=0):
        return bool(self.model.flags(self.index(mod_name, COL_CHECK, nth)) & Qt.ItemFlag.ItemIsUserCheckable)

    def checked(self, mod_name, column=None, nth=0):
        column = COL_CHECK if column is None else column
        return self.cell(mod_name, column, nth, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    def set_checked(self, mod_name, checked=True, column=None, nth=0):
        column = COL_CHECK if column is None else column
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        return self.model.setData(self.index(mod_name, column, nth), state, Qt.ItemDataRole.CheckStateRole)

    def summary(self):
        return self.dialog.findChild(QLabel, "summary").text()

    def progress(self):
        return self.dialog.findChild(QProgressBar, "progress")

    def progress_hidden(self):
        return self.progress().isHidden()

    def downloads(self):
        label = self.dialog.findChild(QLabel, "downloads")
        return None if label.isHidden() else label.text()

    def spinning(self):
        return self.table.itemDelegateForColumn(COL_STATUS).animating

    def button(self, name):
        return self.dialog.findChild(QPushButton, name)

    def header(self):
        return self.table.horizontalHeader()

    def finish_download(self, mod_id, file_id, succeeded=True):
        path = f"{DOWNLOADS}File-{mod_id}-{file_id}.7z"
        self.archive_ids.ids[path] = (mod_id, file_id)
        self.tracker.download_finished(path, succeeded)
        return path

    def answer(self, button, during=None):
        box = mock.Mock()
        box.StandardButton = QMessageBox.StandardButton

        def question(*args):
            if during is not None:
                during()
            return button

        box.question.side_effect = question
        return mock.patch.object(dialog_module, "QMessageBox", box)


class ScanDisplayTest(DialogTestCase):
    def test_empty_plan(self):
        self.open(mods=[])
        wait_until(lambda: self.summary() == EMPTY)
        self.assertTrue(self.progress_hidden())
        self.assertEqual(0, self.runner.starts)
        self.assertFalse(self.button("download_button").isEnabled())
        self.assertFalse(self.button("install_button").isEnabled())
        self.assertEqual(0, self.model.rowCount())

    def test_window_and_headers(self):
        dialog = self.open(mods=[])
        self.assertEqual("Nexus Auto Updater", dialog.windowTitle())
        headers = [self.model.headerData(c, Qt.Orientation.Horizontal) for c in range(self.model.columnCount())]
        self.assertEqual(["", "\U0001F512", "Mod", "Installed file", "Update to", "Status", "Details"], headers)
        self.assertEqual(
            "Select / deselect all updates",
            self.model.headerData(COL_CHECK, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole),
        )

    def test_one_row_per_entry(self):
        self.open_scanned()
        self.show_up_to_date()
        self.assertEqual(["Delta", "Alpha", "Beta", "Gamma", "Epsilon"], self.names())
        self.assertEqual(["Skipped", "Ready", "Needs review", "Up to date", "Error"],
                         [self.status(m) for m in self.names()])
        self.assertEqual("Alpha Main 1.0 [MAIN]", self.cell("Alpha", COL_INSTALLED))
        self.assertEqual("Alpha Main 1.1 [MAIN]", self.cell("Alpha", COL_TARGET))
        self.assertEqual("File ID 2\nGroup 10", self.cell("Alpha", COL_TARGET, role=Qt.ItemDataRole.ToolTipRole))
        self.assertEqual("File ID 7", self.cell("Beta", COL_INSTALLED))
        self.assertEqual("—", self.cell("Delta", COL_TARGET))
        self.assertEqual("—", self.cell("Delta", COL_INSTALLED))
        self.assertEqual("File ID 1 [MAIN]", self.cell("Gamma", COL_INSTALLED))
        self.assertEqual("GraphQL error: Mod not found", self.cell("Epsilon", COL_DETAILS))
        self.assertIn("Nexus mod 1", self.cell("Alpha", COL_MOD, role=Qt.ItemDataRole.ToolTipRole))
        self.assertEqual(GREEN, self.colour("Alpha", COL_STATUS))

    def test_initial_check_states(self):
        self.open_scanned()
        self.show_up_to_date()
        self.assertTrue(self.checked("Alpha"))
        self.assertTrue(self.checkable("Alpha"))
        self.assertFalse(self.checked("Beta"))
        self.assertTrue(self.checkable("Beta"))
        for name in ("Gamma", "Delta", "Epsilon"):
            self.assertFalse(self.checkable(name))
            self.assertFalse(self.checked(name))
        for name in ("Alpha", "Delta"):
            flags = self.model.flags(self.index(name, COL_LOCK))
            self.assertTrue(flags & Qt.ItemFlag.ItemIsUserCheckable)
            self.assertFalse(self.checked(name, COL_LOCK))

    def test_hide_up_to_date_toggle(self):
        dialog = self.open_scanned()
        hide = dialog.findChild(QCheckBox, "hide_current")
        self.assertTrue(hide.isChecked())
        self.assertNotIn("Gamma", self.names())
        self.assertIn("Alpha", self.names())
        hide.setChecked(False)
        self.assertIn("Gamma", self.names())
        hide.setChecked(True)
        self.assertNotIn("Gamma", self.names())

    def test_completion_summary_and_hidden_progress(self):
        self.open_scanned()
        self.assertEqual(
            "Scan complete — Ready: 1, Needs review: 1, Up to date: 1, Skipped: 1, Error: 1. "
            "Rows marked 'Needs review' are unchecked; verify before downloading.",
            self.summary(),
        )
        self.assertTrue(self.progress_hidden())
        self.assertTrue(self.button("download_button").isEnabled())
        self.assertFalse(self.button("install_button").isEnabled())

    def test_progress_while_scanning(self):
        self.open(gate_mod=2)
        wait_until(lambda: self.summary() == "Checking 4 mods MO2 flags as having an update against Nexus…")
        wait_until(lambda: self.gate.entered.is_set() and self.model.rowCount() == 2
                   and self.summary() == "Checking 2/4: Beta")
        progress = self.progress()
        self.assertEqual((0, 4, 1), (progress.minimum(), progress.maximum(), progress.value()))
        self.assertTrue(self.checked("Alpha"))
        self.assertFalse(self.progress_hidden())
        self.assertIsNone(self.downloads())
        self.gate.release.set()
        self.wait_scanned()
        self.assertTrue(self.progress_hidden())

    def test_scan_failure_message_persists(self):
        listings = dict(STANDARD_LISTINGS)
        listings[2] = SourceIncompatible("Nexus v2 API changed: GraphQL schema mismatch")
        self.open_scanned(listings=listings)
        self.assertEqual(
            "Scan stopped: the Nexus v2 API (beta) no longer answers the way this plugin expects, so the "
            "plugin likely needs an update. 3 mod(s) were not checked.\n"
            "Nexus v2 API changed: GraphQL schema mismatch",
            self.summary(),
        )
        self.assertTrue(self.progress_hidden())
        self.assertEqual(2, self.model.rowCount())


class SelectionTest(DialogTestCase):
    def test_header_select_all_touches_only_checkable_rows(self):
        self.open_scanned()
        self.show_up_to_date()
        header = self.header()
        self.assertEqual((False, True), (header.checked, header.enabled))
        header.toggled.emit(True)
        self.assertTrue(self.checked("Alpha") and self.checked("Beta"))
        self.assertFalse(any(self.checked(n) for n in ("Gamma", "Delta", "Epsilon")))
        self.assertTrue(header.checked)
        header.toggled.emit(False)
        self.assertFalse(self.checked("Alpha") or self.checked("Beta"))
        self.assertFalse(header.checked)

    def test_header_reflects_individual_checks(self):
        self.open_scanned()
        self.set_checked("Beta")
        self.assertTrue(self.header().checked)
        self.set_checked("Alpha", False)
        self.assertFalse(self.header().checked)

    def test_header_disabled_without_checkable_rows(self):
        self.open_scanned(mods=[GAMMA])
        self.assertEqual((False, False), (self.header().checked, self.header().enabled))
        self.assertFalse(self.button("download_button").isEnabled())

    def test_up_to_date_rows_cannot_be_checked(self):
        self.open_scanned()
        self.show_up_to_date()
        self.set_checked("Gamma")
        self.assertFalse(self.checked("Gamma"))

    def test_editing_other_columns_does_nothing(self):
        self.open_scanned()
        edited = self.model.setData(self.index("Alpha", COL_DETAILS), "edited", Qt.ItemDataRole.EditRole)
        self.assertFalse(edited)
        self.assertEqual([], self.locks.set_calls)

    def test_selecting_a_row_feeds_the_notes_pane(self):
        dialog = self.open_scanned()
        notes = dialog.findChild(NotesPane, "notes")
        self.table.setCurrentIndex(self.index("Beta", COL_MOD))
        self.assertIn("Beta", notes.toPlainText())
        wait_until(lambda: self.source.description_calls)
        self.assertEqual([(GAME, 2)], self.source.description_calls)
        self.table.selectionModel().clearCurrentIndex()
        self.assertEqual("", notes.toPlainText())


class DownloadTest(DialogTestCase):
    def test_download_checked_queues_only_checked_downloadable_rows(self):
        self.open_scanned()
        self.button("download_button").click()
        self.assertEqual([(1, 2)], self.downloader.calls)
        self.assertEqual("Downloading", self.status("Alpha"))
        self.assertEqual(BLUE, self.colour("Alpha", COL_STATUS))
        self.assertFalse(self.checkable("Alpha"))
        self.assertFalse(self.checked("Alpha"))
        self.assertEqual("Downloads: 0/1 finished", self.downloads())
        self.assertTrue(self.summary().startswith("Scan complete"))
        self.assertTrue(self.progress_hidden())
        self.assertTrue(self.spinning())
        self.assertTrue(self.button("download_button").isEnabled())  # Beta can still be downloaded.
        path = self.finish_download(1, 2)
        self.assertEqual("Downloaded", self.status("Alpha"))
        self.assertIn(f"Archive: {path}", self.cell("Alpha", COL_DETAILS, role=Qt.ItemDataRole.ToolTipRole))
        self.assertFalse(self.spinning())
        self.assertEqual(
            "Downloads complete: 1 downloaded, 0 failed. Use 'Install downloaded…' to install them.",
            self.downloads(),
        )
        self.assertTrue(self.summary().startswith("Scan complete"))
        self.assertTrue(self.button("install_button").isEnabled())

    def test_nothing_checked_downloads_nothing(self):
        self.open_scanned()
        self.set_checked("Alpha", False)
        self.button("download_button").click()
        self.assertEqual([], self.downloader.calls)
        self.assertIsNone(self.downloads())
        self.assertFalse(self.spinning())

    def test_downloads_start_during_the_scan_and_both_areas_update_independently(self):
        self.open(gate_mod=2)
        wait_until(lambda: self.gate.entered.is_set() and self.model.rowCount() == 2)
        self.assertTrue(self.button("download_button").isEnabled())
        self.button("download_button").click()
        self.assertEqual([(1, 2)], self.downloader.calls)
        self.assertEqual("Downloads: 0/1 finished", self.downloads())
        self.assertEqual("Checking 2/4: Beta", self.summary())
        self.assertFalse(self.progress_hidden())
        self.finish_download(1, 2)
        self.assertTrue(self.downloads().startswith("Downloads complete"))
        self.assertEqual("Checking 2/4: Beta", self.summary())
        self.gate.release.set()
        self.wait_scanned()
        self.assertTrue(self.summary().startswith("Scan complete"))
        self.assertTrue(self.downloads().startswith("Downloads complete"))

    def test_a_stopped_scan_message_stays_beside_the_downloads(self):
        listings = dict(STANDARD_LISTINGS)
        listings[2] = SourceIncompatible("Nexus v2 API changed: GraphQL schema mismatch")
        self.open_scanned(listings=listings)
        message = self.summary()
        self.button("download_button").click()
        self.finish_download(1, 2)
        self.assertEqual(message, self.summary())
        self.assertTrue(self.downloads().startswith("Downloads complete"))

    def test_the_status_column_is_wide_enough_for_the_icon(self):
        self.open_scanned(mods=[ALPHA])
        before = self.table.columnWidth(COL_STATUS)
        self.button("download_button").click()
        index = self.index("Alpha", COL_STATUS)
        hint = self.table.itemDelegateForColumn(COL_STATUS).sizeHint(QStyleOptionViewItem(), index)
        self.assertGreater(self.table.columnWidth(COL_STATUS), before)
        self.assertGreaterEqual(self.table.columnWidth(COL_STATUS), hint.width())


class UnmatchedTest(DialogTestCase):
    def test_a_download_that_never_matches_is_released_and_can_be_retried(self):
        self.open_scanned()
        self.button("download_button").click()
        self.assertTrue(self.spinning())
        self.scheduler.fire(DOWNLOAD_PATIENCE_MS)
        self.assertEqual("Not matched", self.status("Alpha"))
        self.assertFalse(self.spinning())
        self.assertEqual(
            "Downloads complete: 0 downloaded, 0 failed, 1 not matched.", self.downloads()
        )
        self.assertTrue(self.checkable("Alpha"))
        self.assertTrue(self.model.setData(self.index("Alpha", COL_CHECK), Qt.CheckState.Checked,
                                           Qt.ItemDataRole.CheckStateRole))
        self.button("download_button").click()
        self.assertEqual("Downloading", self.status("Alpha"))
        self.assertEqual("Downloads: 0/1 finished", self.downloads())

    def test_every_download_can_be_unmatched(self):
        self.open_scanned()
        self.set_checked("Beta")
        self.button("download_button").click()
        self.scheduler.fire(DOWNLOAD_PATIENCE_MS)
        self.assertEqual(
            "Downloads complete: 0 downloaded, 0 failed, 2 not matched.", self.downloads()
        )


class QueueProgressTest(DialogTestCase):
    max_downloads = 1

    def test_queued_rows_count_as_unfinished(self):
        self.open_scanned()
        self.set_checked("Beta")
        self.button("download_button").click()
        self.assertEqual(["Downloading", "Queued"], [self.status("Alpha"), self.status("Beta")])
        self.assertEqual("Downloads: 0/2 finished", self.downloads())
        self.finish_download(1, 2, succeeded=False)
        self.assertEqual(["Download failed", "Downloading"], [self.status("Alpha"), self.status("Beta")])
        self.assertEqual("Downloads: 1/2 finished, 1 failed", self.downloads())
        self.assertTrue(self.spinning())
        self.finish_download(2, 50)
        self.assertFalse(self.spinning())
        self.assertEqual(
            "Downloads complete: 1 downloaded, 1 failed. Use 'Install downloaded…' to install them.",
            self.downloads(),
        )

    def test_all_failed(self):
        self.open_scanned()
        self.button("download_button").click()
        self.finish_download(1, 2, succeeded=False)
        self.assertEqual("Downloads complete: 0 downloaded, 1 failed.", self.downloads())
        self.assertTrue(self.checkable("Alpha"))  # A failed download can be retried.
        self.assertFalse(self.checked("Alpha"))
        self.assertFalse(self.button("install_button").isEnabled())

    def test_later_changes_outside_a_batch_leave_the_downloads_line(self):
        self.open_scanned()
        self.button("download_button").click()
        self.finish_download(1, 2)
        footer = self.downloads()
        with self.answer(QMessageBox.StandardButton.Yes):
            self.button("install_button").click()
        self.assertEqual(footer, self.downloads())
        self.assertEqual("Installed", self.status("Alpha"))


class LockTest(DialogTestCase):
    def two_file_mod(self):
        mods = [installed_mod(9, (1, 3), name="Twin"), ALPHA]
        listings = {
            9: listing(mod_file(1), mod_file(2), mod_file(3, group_id=20), mod_file(4, group_id=20)),
            1: STANDARD_LISTINGS[1],
        }
        return self.open_scanned(mods=mods, listings=listings)

    def test_toggling_one_rows_lock_updates_every_row_of_the_mod(self):
        self.two_file_mod()
        self.assertEqual(2, self.names().count("Twin"))
        self.set_checked("Twin", True, column=COL_LOCK, nth=1)
        self.assertEqual([(9, True)], self.locks.set_calls)
        self.assertTrue(self.locks.is_locked(9))
        for nth in (0, 1):
            self.assertTrue(self.checked("Twin", COL_LOCK, nth))
            self.assertFalse(self.checkable("Twin", nth))
            self.assertFalse(self.checked("Twin", COL_CHECK, nth))
            for column in (COL_MOD, COL_INSTALLED, COL_TARGET, COL_STATUS, COL_DETAILS):
                self.assertEqual(GREY, self.colour("Twin", column, nth))
        self.assertFalse(self.checked("Alpha", COL_LOCK))
        self.assertTrue(self.checked("Alpha"))
        self.button("download_button").click()
        self.assertEqual([(1, 2)], self.downloader.calls)

        self.set_checked("Twin", False, column=COL_LOCK, nth=0)
        self.assertEqual([(9, True), (9, False)], self.locks.set_calls)
        for nth in (0, 1):
            self.assertFalse(self.checked("Twin", COL_LOCK, nth))
            self.assertTrue(self.checkable("Twin", nth))
            self.assertFalse(self.checked("Twin", COL_CHECK, nth))
            self.assertEqual(GREEN, self.colour("Twin", COL_STATUS, nth))

    def test_mod_locked_before_the_scan(self):
        self.open_scanned(locks=FakeLocks(1))
        self.assertTrue(self.checked("Alpha", COL_LOCK))
        self.assertFalse(self.checkable("Alpha"))
        self.assertFalse(self.checked("Alpha"))
        self.assertEqual(GREY, self.colour("Alpha", COL_STATUS))
        self.button("download_button").click()
        self.assertEqual([], self.downloader.calls)


class InstallTest(DialogTestCase):
    def downloaded_two(self):
        self.open_scanned()
        self.set_checked("Beta")
        self.button("download_button").click()
        self.finish_download(1, 2)
        self.finish_download(2, 50)
        self.assertTrue(self.button("install_button").isEnabled())

    def test_confirmed_installs_every_installable_row(self):
        self.downloaded_two()
        with self.answer(QMessageBox.StandardButton.Yes) as box:
            self.button("install_button").click()
        (args, _), = box.question.call_args_list
        self.assertIs(self.dialog, args[0])
        self.assertEqual("Install downloaded updates", args[1])
        self.assertTrue(args[2].startswith("Install 2 downloaded update(s)?"))
        self.assertEqual(["Alpha", "Beta"], [name for _, name in self.installer.calls])
        self.assertEqual(["Installed", "Installed"], [self.status("Alpha"), self.status("Beta")])
        self.assertFalse(self.button("install_button").isEnabled())

    def test_declined_installs_nothing(self):
        self.downloaded_two()
        with self.answer(QMessageBox.StandardButton.No):
            self.button("install_button").click()
        self.assertEqual([], self.installer.calls)
        self.assertEqual("Downloaded", self.status("Alpha"))

    def test_cancelled_install(self):
        self.open_scanned(installer_result=False)
        self.button("download_button").click()
        self.finish_download(1, 2)
        with self.answer(QMessageBox.StandardButton.Yes):
            self.button("install_button").click()
        self.assertEqual("Install cancelled", self.status("Alpha"))

    def test_nothing_installable_asks_nothing(self):
        self.open_scanned()
        with self.answer(QMessageBox.StandardButton.Yes) as box:
            self.button("install_button").click()
        box.question.assert_not_called()

    def test_only_what_the_user_was_asked_about_is_installed(self):
        self.open_scanned()
        self.set_checked("Beta")
        self.button("download_button").click()
        self.finish_download(1, 2)
        with self.answer(QMessageBox.StandardButton.Yes, during=lambda: self.finish_download(2, 50)) as box:
            self.button("install_button").click()
        self.assertTrue(box.question.call_args.args[2].startswith("Install 1 downloaded update(s)?"))
        self.assertEqual(["Alpha"], [name for _, name in self.installer.calls])
        self.assertEqual(["Installed", "Downloaded"], [self.status("Alpha"), self.status("Beta")])
        self.assertTrue(self.button("install_button").isEnabled())


class LifetimeTest(DialogTestCase):
    def test_closing_during_the_scan_stops_it(self):
        self.open(gate_mod=2)
        wait_until(self.gate.entered.is_set)
        releaser = threading.Timer(0.05, self.gate.release.set)
        releaser.start()
        self.addCleanup(releaser.cancel)
        self.dialog.done(0)
        self.assertEqual([1, 2], [mod_id for _, mod_id in self.source.list_calls])
        wait_until(lambda: True)
        self.assertEqual(2, self.model.rowCount())

    def test_closing_before_the_scan_starts_prevents_it(self):
        self.open(threaded=True)
        self.dialog.done(0)
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            wait_until(lambda: True)
        self.assertEqual([], self.source.list_calls)
        self.assertEqual(0, self.model.rowCount())

    def test_closing_after_the_scan(self):
        self.open_scanned()
        self.dialog.done(1)
        self.assertEqual(1, self.dialog.result())

    def test_nothing_changes_once_closed(self):
        self.open_scanned()
        self.button("download_button").click()
        footer = self.downloads()
        self.dialog.done(0)
        self.finish_download(1, 2)
        self.assertEqual("Downloading", self.status("Alpha"))
        self.assertEqual(footer, self.downloads())

    def test_closing_stops_the_spinner_of_an_unfinished_download(self):
        self.open_scanned()
        self.button("download_button").click()
        self.assertTrue(self.spinning())
        self.dialog.done(0)
        self.assertFalse(self.spinning())


class EndToEndTest(DialogTestCase):
    def test_scan_check_download_match_install(self):
        archive = "SkyUI_5_2_SE-12604-5-2SE.7z"
        skyui = installed_mod(12604, (1000172397,), name="SkyUI")
        files = (
            mod_file(1000172397, name="SkyUI", version="5.1SE", group_id=1000172397),
            mod_file(1000172398, name="SkyUI", version="5.2SE", group_id=1000172397),
        )
        listings = {12604: listing(*files, matchers={1000172398: FakeMatcher(archive)})}
        self.open_scanned(mods=[skyui], listings=listings, threaded=True)
        self.assertEqual("Ready", self.status("SkyUI"))
        self.assertTrue(self.checked("SkyUI"))

        self.button("download_button").click()
        self.assertEqual([(12604, 1000172398)], self.downloader.calls)
        self.assertEqual("Downloading", self.status("SkyUI"))
        self.assertEqual("Downloads: 0/1 finished", self.downloads())

        path = DOWNLOADS + archive
        self.archive_ids.ids[path] = (12604, 1000172398)
        self.tracker.download_finished(path, True)
        self.assertEqual("Downloaded", self.status("SkyUI"))
        self.assertTrue(self.downloads().startswith("Downloads complete: 1 downloaded"))
        self.assertTrue(self.button("install_button").isEnabled())

        with self.answer(QMessageBox.StandardButton.Yes):
            self.button("install_button").click()
        self.assertEqual([(path, "SkyUI")], self.installer.calls)
        self.assertEqual("Installed", self.status("SkyUI"))
        (installed,) = self.session.entries()
        self.assertIsInstance(installed.finding, Offered)
        self.assertEqual(Installed(path), installed.progress)
