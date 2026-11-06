import threading
from unittest import mock

from nexus_auto_updater.app.notes import ModNotes
from nexus_auto_updater.app.ports import FileListing, SourceError, SourceIncompatible
from nexus_auto_updater.app.scan import ModChecked, ScanPlan
from nexus_auto_updater.app.session import Installed, Offered, UpdateSession
from nexus_auto_updater.domain.selection import select_successor
from nexus_auto_updater.tracking.tracker import DownloadTracker
from tests import HAS_QT, QtTestCase, wait_until
from tests.fakes import (
    GAME,
    ArchiveIds,
    FakeDownloader,
    FakeInstaller,
    FakeLocks,
    FakeMatcher,
    FakeSource,
    Gate,
    ManualScheduler,
    installed_mod,
    mod_file,
)

if HAS_QT:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMessageBox

    from nexus_auto_updater.ui import dialog as dialog_module
    from nexus_auto_updater.ui.dialog import (
        COL_CHECK,
        COL_DETAILS,
        COL_INSTALLED,
        COL_LOCK,
        COL_MOD,
        COL_STATUS,
        COL_TARGET,
        UpdaterDialog,
    )

DOWNLOADS = "C:\\Modding\\MO2\\downloads\\"
SCOPE = "mods MO2 flags as having an update"
EMPTY = "No mods to check."


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

    def open(self, mods=None, listings=None, *, locks=None, installer_result=True, gate_mod=None):
        mods = STANDARD_MODS if mods is None else mods
        self.source = FakeSource(STANDARD_LISTINGS if listings is None else listings,
                                 {m.mod_id: "" for m in mods})
        self.gate = Gate()
        if gate_mod is not None:
            self.source.before_list = lambda mod_id: self.gate.wait() if mod_id == gate_mod else None
        self.plan = ScanPlan(self.source, GAME, mods)
        self.archive_ids = ArchiveIds()
        self.scheduler = ManualScheduler()
        self.tracker = DownloadTracker(self.archive_ids, self.scheduler)
        self.downloader = FakeDownloader()
        self.installer = FakeInstaller(installer_result)
        self.locks = locks or FakeLocks()
        self.session = UpdateSession(self.downloader, self.installer, self.tracker, self.locks, self.max_downloads)
        self.dialog = UpdaterDialog(
            None, self.plan, self.session, ModNotes(self.source, GAME), scope_label=SCOPE, empty_message=EMPTY
        )
        self.addCleanup(self._close)
        self.table = self.dialog._table
        return self.dialog

    def _close(self):
        self.gate.release.set()
        self.dialog.done(0)
        if self.dialog._worker is not None:
            self.dialog._worker.wait(5000)
        wait_until(lambda: True)
        self.dialog.deleteLater()

    def open_scanned(self, *args, **kwargs):
        dialog = self.open(*args, **kwargs)
        self.wait_scanned()
        return dialog

    def wait_scanned(self):
        wait_until(lambda: self.dialog._worker is not None and self.dialog._worker.isFinished()
                   and not self.dialog._scanning)

    def row_of(self, mod_name, nth=0):
        rows = [r for r in range(self.table.rowCount()) if self.table.item(r, COL_MOD).text() == mod_name]
        return rows[nth]

    def cell(self, mod_name, column, nth=0):
        return self.table.item(self.row_of(mod_name, nth), column)

    def status(self, mod_name, nth=0):
        return self.cell(mod_name, COL_STATUS, nth).text()

    def checkable(self, mod_name, nth=0):
        return bool(self.cell(mod_name, COL_CHECK, nth).flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def checked(self, mod_name, nth=0):
        return self.cell(mod_name, COL_CHECK, nth).checkState() == Qt.CheckState.Checked

    def set_checked(self, mod_name, checked=True, column=None, nth=0):
        column = COL_CHECK if column is None else column
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.cell(mod_name, column, nth).setCheckState(state)

    def finish_download(self, mod_id, file_id, succeeded=True):
        path = f"{DOWNLOADS}File-{mod_id}-{file_id}.7z"
        self.archive_ids.ids[path] = (mod_id, file_id)
        self.tracker.download_finished(path, succeeded)
        return path

    def summary(self):
        return self.dialog._summary.text()

    def progress_hidden(self):
        return self.dialog._progress.isHidden()

    def answer(self, button):
        box = mock.Mock()
        box.StandardButton = QMessageBox.StandardButton
        box.question.return_value = button
        return mock.patch.object(dialog_module, "QMessageBox", box)


class ScanDisplayTest(DialogTestCase):
    def test_empty_plan(self):
        dialog = self.open(mods=[])
        self.assertEqual("Checking 0 mods MO2 flags as having an update against Nexus…", self.summary())
        wait_until(self.progress_hidden)
        self.assertEqual(EMPTY, self.summary())
        self.assertIsNone(dialog._worker)
        self.assertFalse(dialog._download_button.isEnabled())
        self.assertFalse(dialog._install_button.isEnabled())
        self.assertEqual(0, self.table.rowCount())

    def test_window_and_headers(self):
        dialog = self.open(mods=[])
        self.assertEqual("Nexus Auto Updater", dialog.windowTitle())
        headers = [self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount())]
        self.assertEqual(["", "\U0001F512", "Mod", "Installed file", "Update to", "Status", "Details"], headers)
        self.assertEqual("Select / deselect all updates", self.table.horizontalHeaderItem(COL_CHECK).toolTip())

    def test_one_row_per_entry(self):
        self.open_scanned()
        self.assertEqual(["Delta", "Alpha", "Beta", "Gamma", "Epsilon"],
                         [self.table.item(r, COL_MOD).text() for r in range(self.table.rowCount())])
        self.assertEqual(["Skipped", "Ready", "Needs review", "Up to date", "Error"],
                         [self.status(m) for m in ("Delta", "Alpha", "Beta", "Gamma", "Epsilon")])
        self.assertEqual("Alpha Main 1.0 [MAIN]", self.cell("Alpha", COL_INSTALLED).text())
        self.assertEqual("Alpha Main 1.1 [MAIN]", self.cell("Alpha", COL_TARGET).text())
        self.assertEqual("File ID 2\nGroup 10", self.cell("Alpha", COL_TARGET).toolTip())
        self.assertEqual("File ID 7", self.cell("Beta", COL_INSTALLED).text())
        self.assertEqual("—", self.cell("Delta", COL_TARGET).text())
        self.assertEqual("GraphQL error: Mod not found", self.cell("Epsilon", COL_DETAILS).text())
        self.assertIn("Nexus mod 1", self.cell("Alpha", COL_MOD).toolTip())
        self.assertEqual("#27ae60", self.cell("Alpha", COL_STATUS).foreground().color().name())

    def test_initial_check_states(self):
        self.open_scanned()
        self.assertTrue(self.checked("Alpha"))
        self.assertTrue(self.checkable("Alpha"))
        self.assertFalse(self.checked("Beta"))
        self.assertTrue(self.checkable("Beta"))
        for name in ("Gamma", "Delta", "Epsilon"):
            self.assertFalse(self.checkable(name))
            self.assertFalse(self.checked(name))
        for name in ("Alpha", "Delta"):
            lock = self.cell(name, COL_LOCK)
            self.assertTrue(lock.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            self.assertEqual(Qt.CheckState.Unchecked, lock.checkState())

    def test_hide_up_to_date_toggle(self):
        dialog = self.open_scanned()
        self.assertTrue(dialog._hide_current.isChecked())
        gamma = self.row_of("Gamma")
        self.assertTrue(self.table.isRowHidden(gamma))
        self.assertFalse(self.table.isRowHidden(self.row_of("Alpha")))
        dialog._hide_current.setChecked(False)
        self.assertFalse(self.table.isRowHidden(gamma))
        dialog._hide_current.setChecked(True)
        self.assertTrue(self.table.isRowHidden(gamma))

    def test_completion_summary_and_hidden_progress(self):
        dialog = self.open_scanned()
        self.assertEqual(
            "Scan complete — Ready: 1, Needs review: 1, Up to date: 1, Skipped: 1, Error: 1. "
            "Rows marked 'Needs review' are unchecked; verify before downloading.",
            self.summary(),
        )
        self.assertTrue(self.progress_hidden())
        self.assertTrue(dialog._download_button.isEnabled())
        self.assertFalse(dialog._install_button.isEnabled())

    def test_progress_and_disabled_download_while_scanning(self):
        dialog = self.open(gate_mod=2)
        self.assertEqual("Checking 4 mods MO2 flags as having an update against Nexus…", self.summary())
        wait_until(lambda: self.gate.entered.is_set() and self.table.rowCount() == 2
                   and self.summary() == "Checking 2/4: Beta")
        self.assertEqual((0, 4, 1), (dialog._progress.minimum(), dialog._progress.maximum(), dialog._progress.value()))
        self.assertTrue(self.checked("Alpha"))
        self.assertFalse(dialog._download_button.isEnabled())
        self.assertFalse(self.progress_hidden())
        self.gate.release.set()
        self.wait_scanned()
        self.assertTrue(dialog._download_button.isEnabled())

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
        self.assertEqual(2, self.table.rowCount())

    def test_unknown_scan_events_are_ignored(self):
        self.open_scanned()
        before = self.summary()
        self.dialog._on_scan_event(object())
        self.assertEqual(before, self.summary())


class SelectionTest(DialogTestCase):
    def test_header_select_all_touches_only_checkable_rows(self):
        dialog = self.open_scanned()
        header = dialog._header
        self.assertEqual((False, True), (header._checked, header._enabled))
        header.toggled.emit(True)
        self.assertTrue(self.checked("Alpha") and self.checked("Beta"))
        self.assertFalse(any(self.checked(n) for n in ("Gamma", "Delta", "Epsilon")))
        self.assertTrue(header._checked)
        header.toggled.emit(False)
        self.assertFalse(self.checked("Alpha") or self.checked("Beta"))
        self.assertFalse(header._checked)

    def test_header_reflects_individual_checks(self):
        dialog = self.open_scanned()
        self.set_checked("Beta")
        self.assertTrue(dialog._header._checked)
        self.set_checked("Alpha", False)
        self.assertFalse(dialog._header._checked)

    def test_header_disabled_without_checkable_rows(self):
        dialog = self.open_scanned(mods=[GAMMA])
        self.assertEqual((False, False), (dialog._header._checked, dialog._header._enabled))
        self.assertFalse(dialog._download_button.isEnabled())

    def test_editing_other_columns_does_nothing(self):
        self.open_scanned()
        self.cell("Alpha", COL_DETAILS).setText("edited")
        self.assertEqual([], self.locks.set_calls)

    def test_selecting_a_row_feeds_the_notes_pane(self):
        dialog = self.open_scanned()
        self.table.setCurrentCell(self.row_of("Beta"), COL_MOD)
        self.assertEqual("Beta", dialog._notes._entry.mod_name)
        wait_until(lambda: 2 in dialog._notes._pages)
        self.assertEqual([(GAME, 2)], self.source.description_calls)
        self.table.setCurrentItem(None)
        self.assertIsNone(dialog._notes._entry)


class DownloadTest(DialogTestCase):
    def test_download_checked_queues_only_checked_downloadable_rows(self):
        dialog = self.open_scanned()
        self.cell("Gamma", COL_CHECK).setCheckState(Qt.CheckState.Checked)  # Stale check on an up-to-date row.
        dialog._download_button.click()
        self.assertEqual([(1, 2)], self.downloader.calls)
        self.assertEqual("Downloading", self.status("Alpha"))
        self.assertEqual("#45beef", self.cell("Alpha", COL_STATUS).foreground().color().name())
        self.assertFalse(self.checkable("Alpha"))
        self.assertFalse(self.checked("Alpha"))
        self.assertEqual("Downloading updates: 0/1 finished.", self.summary())
        self.assertFalse(self.progress_hidden())
        self.assertTrue(dialog._download_button.isEnabled())  # Beta can still be downloaded.
        path = self.finish_download(1, 2)
        self.assertEqual("Downloaded", self.status("Alpha"))
        self.assertIn(f"Archive: {path}", self.cell("Alpha", COL_DETAILS).toolTip())
        self.assertTrue(self.progress_hidden())
        self.assertEqual(
            "Downloads complete: 1 downloaded, 0 failed. Use 'Install downloaded…' to install them.", self.summary()
        )
        self.assertTrue(dialog._install_button.isEnabled())

    def test_nothing_checked_downloads_nothing(self):
        dialog = self.open_scanned()
        self.set_checked("Alpha", False)
        before = self.summary()
        dialog._download_button.click()
        self.assertEqual([], self.downloader.calls)
        self.assertEqual(before, self.summary())

    def test_entries_outside_the_table_are_ignored(self):
        self.open_scanned()
        selection = select_successor(1, [mod_file(1), mod_file(2)])
        (entry,) = self.session.record_scan(ModChecked(installed_mod(9, (1,)), (selection,)))
        rows, summary = self.table.rowCount(), self.summary()
        self.session.download(entry.key)  # Notifies the dialog about an entry it never showed.
        self.assertEqual([(9, 2)], self.downloader.calls)
        self.assertEqual((rows, summary), (self.table.rowCount(), self.summary()))


class QueueProgressTest(DialogTestCase):
    max_downloads = 1

    def test_queued_rows_count_as_unfinished(self):
        dialog = self.open_scanned()
        self.set_checked("Beta")
        dialog._download_button.click()
        self.assertEqual(["Downloading", "Queued"], [self.status("Alpha"), self.status("Beta")])
        self.assertEqual("Downloading updates: 0/2 finished.", self.summary())
        self.assertEqual((0, 2), (dialog._progress.value(), dialog._progress.maximum()))
        self.finish_download(1, 2, succeeded=False)
        self.assertEqual(["Download failed", "Downloading"], [self.status("Alpha"), self.status("Beta")])
        self.assertEqual("Downloading updates: 1/2 finished.", self.summary())
        self.assertFalse(self.progress_hidden())
        self.finish_download(2, 50)
        self.assertTrue(self.progress_hidden())
        self.assertEqual(
            "Downloads complete: 1 downloaded, 1 failed. Use 'Install downloaded…' to install them.", self.summary()
        )

    def test_all_failed(self):
        dialog = self.open_scanned()
        dialog._download_button.click()
        self.finish_download(1, 2, succeeded=False)
        self.assertEqual("Downloads complete: 0 downloaded, 1 failed.", self.summary())
        self.assertTrue(self.checkable("Alpha"))  # A failed download can be retried.
        self.assertFalse(self.checked("Alpha"))
        self.assertFalse(dialog._install_button.isEnabled())

    def test_later_changes_outside_a_batch_leave_the_summary(self):
        dialog = self.open_scanned()
        dialog._download_button.click()
        self.finish_download(1, 2)
        summary = self.summary()
        with self.answer(QMessageBox.StandardButton.Yes):
            dialog._install_button.click()
        self.assertEqual(summary, self.summary())
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
        dialog = self.two_file_mod()
        self.assertEqual(2, sum(1 for r in range(self.table.rowCount()) if self.table.item(r, COL_MOD).text() == "Twin"))
        self.set_checked("Twin", True, column=COL_LOCK, nth=1)
        self.assertEqual([(9, True)], self.locks.set_calls)
        self.assertTrue(self.locks.is_locked(9))
        for nth in (0, 1):
            self.assertEqual(Qt.CheckState.Checked, self.cell("Twin", COL_LOCK, nth).checkState())
            self.assertFalse(self.checkable("Twin", nth))
            self.assertFalse(self.checked("Twin", nth))
            for column in (COL_MOD, COL_INSTALLED, COL_TARGET, COL_STATUS, COL_DETAILS):
                self.assertEqual("#7f8c8d", self.cell("Twin", column, nth).foreground().color().name())
        self.assertEqual(Qt.CheckState.Unchecked, self.cell("Alpha", COL_LOCK).checkState())
        self.assertTrue(self.checked("Alpha"))
        dialog._download_button.click()
        self.assertEqual([(1, 2)], self.downloader.calls)

        self.set_checked("Twin", False, column=COL_LOCK, nth=0)
        self.assertEqual([(9, True), (9, False)], self.locks.set_calls)
        for nth in (0, 1):
            self.assertEqual(Qt.CheckState.Unchecked, self.cell("Twin", COL_LOCK, nth).checkState())
            self.assertTrue(self.checkable("Twin", nth))
            self.assertFalse(self.checked("Twin", nth))
            self.assertEqual("#27ae60", self.cell("Twin", COL_STATUS, nth).foreground().color().name())

    def test_mod_locked_before_the_scan(self):
        dialog = self.open_scanned(locks=FakeLocks(1))
        self.assertEqual(Qt.CheckState.Checked, self.cell("Alpha", COL_LOCK).checkState())
        self.assertFalse(self.checkable("Alpha"))
        self.assertFalse(self.checked("Alpha"))
        self.assertEqual("#7f8c8d", self.cell("Alpha", COL_STATUS).foreground().color().name())
        dialog._download_button.click()
        self.assertEqual([], self.downloader.calls)


class InstallTest(DialogTestCase):
    def downloaded_two(self):
        dialog = self.open_scanned()
        self.set_checked("Beta")
        dialog._download_button.click()
        self.finish_download(1, 2)
        self.finish_download(2, 50)
        self.assertTrue(dialog._install_button.isEnabled())
        return dialog

    def test_confirmed_installs_every_installable_row(self):
        dialog = self.downloaded_two()
        with self.answer(QMessageBox.StandardButton.Yes) as box:
            dialog._install_button.click()
        (args, _), = box.question.call_args_list
        self.assertIs(dialog, args[0])
        self.assertEqual("Install downloaded updates", args[1])
        self.assertTrue(args[2].startswith("Install 2 downloaded update(s)?"))
        self.assertEqual(["Alpha", "Beta"], [name for _, name in self.installer.calls])
        self.assertEqual(["Installed", "Installed"], [self.status("Alpha"), self.status("Beta")])
        self.assertFalse(dialog._install_button.isEnabled())

    def test_declined_installs_nothing(self):
        dialog = self.downloaded_two()
        with self.answer(QMessageBox.StandardButton.No):
            dialog._install_button.click()
        self.assertEqual([], self.installer.calls)
        self.assertEqual("Downloaded", self.status("Alpha"))

    def test_cancelled_install(self):
        dialog = self.open_scanned(installer_result=False)
        dialog._download_button.click()
        self.finish_download(1, 2)
        with self.answer(QMessageBox.StandardButton.Yes):
            dialog._install_button.click()
        self.assertEqual("Install cancelled", self.status("Alpha"))

    def test_nothing_installable_asks_nothing(self):
        dialog = self.open_scanned()
        with self.answer(QMessageBox.StandardButton.Yes) as box:
            dialog._install_downloaded()
        box.question.assert_not_called()


class LifetimeTest(DialogTestCase):
    def test_done_interrupts_the_worker_and_unsubscribes(self):
        dialog = self.open(gate_mod=2)
        wait_until(self.gate.entered.is_set)
        worker = dialog._worker
        releaser = threading.Timer(0.05, self.gate.release.set)
        releaser.start()
        self.addCleanup(releaser.cancel)
        dialog.done(0)
        self.assertTrue(worker.isFinished())
        self.assertIsNone(self.session._listener)
        self.assertEqual([1, 2], [mod_id for _, mod_id in self.source.list_calls])

    def test_done_after_the_scan(self):
        dialog = self.open_scanned()
        dialog.done(1)
        self.assertIsNone(self.session._listener)
        self.assertEqual(1, dialog.result())


class EndToEndTest(DialogTestCase):
    def test_scan_check_download_match_install(self):
        archive = "SkyUI_5_2_SE-12604-5-2SE.7z"
        skyui = installed_mod(12604, (1000172397,), name="SkyUI")
        files = (
            mod_file(1000172397, name="SkyUI", version="5.1SE", group_id=1000172397),
            mod_file(1000172398, name="SkyUI", version="5.2SE", group_id=1000172397),
        )
        listings = {12604: listing(*files, matchers={1000172398: FakeMatcher(archive)})}
        dialog = self.open_scanned(mods=[skyui], listings=listings)
        self.assertEqual("Ready", self.status("SkyUI"))
        self.assertTrue(self.checked("SkyUI"))

        dialog._download_button.click()
        self.assertEqual([(12604, 1000172398)], self.downloader.calls)
        self.assertEqual("Downloading", self.status("SkyUI"))
        self.assertFalse(self.progress_hidden())

        path = DOWNLOADS + archive
        self.archive_ids.ids[path] = (12604, 1000172398)
        self.tracker.download_finished(path, True)
        self.assertEqual("Downloaded", self.status("SkyUI"))
        self.assertTrue(self.progress_hidden())
        self.assertTrue(dialog._install_button.isEnabled())

        with self.answer(QMessageBox.StandardButton.Yes):
            dialog._install_button.click()
        self.assertEqual([(path, "SkyUI")], self.installer.calls)
        self.assertEqual("Installed", self.status("SkyUI"))
        (installed,) = self.session.entries()
        self.assertIsInstance(installed.finding, Offered)
        self.assertEqual(Installed(path), installed.progress)
