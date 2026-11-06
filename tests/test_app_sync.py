import unittest

from nexus_auto_updater.app.ports import FileListing
from nexus_auto_updater.app.scan import (
    ModChecked,
    ModErrored,
    ModScanned,
    ModSkipped,
    ScanFailed,
    ScanPlan,
    ScanProgress,
    StopReason,
)
from nexus_auto_updater.app.session import (
    DownloadFailed,
    Downloaded,
    Downloading,
    Installed,
    Queued,
    UpdateSession,
)
from nexus_auto_updater.app.sync import (
    Complete,
    Finished,
    InProgress,
    NoBatch,
    NotStarted,
    NothingToScan,
    RowChanged,
    RowsAdded,
    Scanning,
    SelectAll,
    StatusChanged,
    Stopped,
    Sync,
)
from nexus_auto_updater.domain.selection import Verdict
from nexus_auto_updater.tracking.tracker import DownloadTracker
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
    mod_file,
)
from tests.test_app_session import archive, selection

FAILED = ScanFailed(StopReason.UNAVAILABLE, "down", 2)


class SyncTestCase(unittest.TestCase):
    max_downloads = 3
    plan_mods = (installed_mod(1),)

    def setUp(self):
        self.ids = ArchiveIds()
        self.tracker = DownloadTracker(self.ids, ManualScheduler())
        self.downloader = FakeDownloader()
        self.installer = FakeInstaller()
        self.locks = FakeLocks()
        self.runner = FakeScanRunner()
        self.session = UpdateSession(
            self.downloader, self.installer, self.tracker, self.locks, lambda: self.max_downloads
        )
        self.plan = ScanPlan(FakeSource({}), GAME, list(self.plan_mods))
        self.sync = Sync(self.session, self.plan, self.runner)
        self.changes = []
        self.sync.subscribe(self.changes.append)

    def scanned(self, mod_id, *selections):
        ids = tuple(s.installed_file_id for s in selections)
        scan = ModChecked(installed_mod(mod_id, ids), tuple(selections))
        self.runner.deliver(ModScanned(scan))
        return self.sync.rows()[-len(selections):]

    def scanning(self, *selections_by_mod):
        """Start the scan and report one mod per argument: ``(mod_id, *selections)``."""
        self.sync.start()
        keys = []
        for mod_id, *selections in selections_by_mod:
            keys += self.scanned(mod_id, *selections)
        return keys

    def finish_download(self, key, succeeded=True):
        entry = self.sync.entry(key)
        path = archive(entry.finding.target.file_id)
        self.ids.ids[path] = (entry.mod_id, entry.finding.target.file_id)
        self.tracker.download_finished(path, succeeded)
        return path

    def progress(self, *keys):
        return [type(self.sync.entry(k).progress) for k in keys]


class ScanLifecycleTest(SyncTestCase):
    def test_not_started_until_start(self):
        status = self.sync.status()
        self.assertEqual(NotStarted(), status.scan)
        self.assertEqual(NoBatch(), status.batch)
        self.assertEqual(SelectAll.UNAVAILABLE, status.select_all)
        self.assertEqual(frozenset(), status.installable)
        self.assertEqual((), self.sync.rows())
        self.assertEqual(0, self.runner.starts)

    def test_empty_plan_has_nothing_to_scan_and_never_runs(self):
        sync = Sync(self.session, ScanPlan(FakeSource({}), GAME, []), self.runner)
        sync.start()
        self.assertEqual(NothingToScan(), sync.status().scan)
        self.assertEqual(0, self.runner.starts)

    def test_start_runs_the_plan_once(self):
        self.sync.start()
        self.assertEqual(Scanning(0, 1, ""), self.sync.status().scan)
        self.assertIs(self.plan, self.runner.plan)
        self.sync.start()
        self.assertEqual(1, self.runner.starts)

    def test_progress_then_finish(self):
        self.sync.start()
        self.runner.deliver(ScanProgress(0, 3, "Alpha"))
        self.assertEqual(Scanning(0, 3, "Alpha"), self.sync.status().scan)
        self.runner.deliver(ScanProgress(3, 3, ""))
        self.assertEqual(Scanning(3, 3, ""), self.sync.status().scan)
        self.runner.finish()
        self.assertEqual(Complete(), self.sync.status().scan)

    def test_failure_stops_and_the_runner_finishing_keeps_it(self):
        self.sync.start()
        self.runner.deliver(FAILED)
        self.assertEqual(Stopped(FAILED), self.sync.status().scan)
        self.runner.finish()
        self.assertEqual(Stopped(FAILED), self.sync.status().scan)

    def test_events_outside_a_scan_are_ignored(self):
        scan = ModScanned(ModSkipped(installed_mod(9), "x"))
        self.runner.deliver(ScanProgress(1, 3, "Early"))
        self.runner.deliver(scan)
        self.runner.deliver(FAILED)
        self.assertEqual(NotStarted(), self.sync.status().scan)
        self.assertEqual((), self.sync.rows())

        self.sync.start()
        self.runner.finish()
        self.runner.deliver(ScanProgress(1, 3, "Late"))
        self.runner.deliver(scan)
        self.runner.deliver(FAILED)
        self.runner.finish()
        self.assertEqual(Complete(), self.sync.status().scan)
        self.assertEqual((), self.sync.rows())

    def test_a_runner_that_reports_from_inside_start_works(self):
        source = FakeSource({1: FileListing((mod_file(1), mod_file(2)))})
        runner = FakeScanRunner(synchronous=True)
        sync = Sync(self.session, ScanPlan(source, GAME, [installed_mod(1)]), runner)
        sync.start()
        self.assertEqual(Complete(), sync.status().scan)
        (key,) = sync.rows()
        self.assertTrue(sync.is_selected(key))
        self.assertEqual(SelectAll.ALL, sync.status().select_all)


class RowsTest(SyncTestCase):
    def test_scanned_mods_become_rows_in_order(self):
        keys = self.scanning((1, selection(1), selection(2)), (2, selection(3)))
        self.assertEqual(tuple(keys), self.sync.rows())
        self.assertEqual([1, 2, 3], [self.sync.entry(k).finding.installed.file_id for k in keys])

    def test_skipped_and_errored_mods_become_rows(self):
        self.sync.start()
        self.runner.deliver(ModScanned(ModSkipped(installed_mod(5), "Other game")))
        self.runner.deliver(ModScanned(ModErrored(installed_mod(6), "HTTP 404")))
        self.assertEqual(2, len(self.sync.rows()))
        self.assertEqual(SelectAll.UNAVAILABLE, self.sync.status().select_all)

    def test_a_checked_mod_without_selections_adds_no_rows(self):
        self.sync.start()
        del self.changes[:]
        self.runner.deliver(ModScanned(ModChecked(installed_mod(1), ())))
        self.assertEqual((), self.sync.rows())
        self.assertEqual([StatusChanged()], self.changes)

    def test_rows_know_their_position(self):
        keys = self.scanning((1, selection(1), selection(2)), (2, selection(3)))
        self.assertEqual([0, 1, 2], [self.sync.row_of(k) for k in keys])

    def test_notifications(self):
        self.sync.start()
        self.assertEqual([StatusChanged()], self.changes)
        del self.changes[:]
        (a, b) = self.scanning((1, selection(1), selection(2)))
        self.assertEqual([RowsAdded((a, b)), StatusChanged()], self.changes)
        del self.changes[:]
        self.sync.set_locked(1, True)
        self.assertEqual(
            [RowChanged(a), StatusChanged(), RowChanged(b), StatusChanged()], self.changes
        )

    def test_unsubscribed_sync_still_works(self):
        self.sync.subscribe(None)
        self.scanning((1, selection(1)))
        self.assertEqual(1, len(self.sync.rows()))
        self.assertEqual([], self.changes)


class SelectionTest(SyncTestCase):
    def test_ready_entries_start_selected_and_review_ones_do_not(self):
        ready, review, current, unresolved = self.scanning(
            (1, selection(1, Verdict.READY), selection(2, Verdict.REVIEW),
             selection(3, Verdict.UP_TO_DATE), selection(4, Verdict.UNRESOLVED))
        )
        self.assertEqual(
            [True, False, False, False], [self.sync.is_selected(k) for k in (ready, review, current, unresolved)]
        )
        self.assertEqual(SelectAll.NOT_ALL, self.sync.status().select_all)

    def test_a_locked_mod_is_not_selected(self):
        self.locks.locked.add(1)
        (key,) = self.scanning((1, selection(1)))
        self.assertFalse(self.sync.is_selected(key))
        self.assertEqual(SelectAll.UNAVAILABLE, self.sync.status().select_all)

    def test_only_downloadable_entries_can_be_selected(self):
        review, current = self.scanning((1, selection(2, Verdict.REVIEW), selection(3, Verdict.UP_TO_DATE)))
        self.sync.set_selected(current, True)
        self.assertFalse(self.sync.is_selected(current))
        self.sync.set_selected(review, True)
        self.assertTrue(self.sync.is_selected(review))
        self.assertEqual(SelectAll.ALL, self.sync.status().select_all)
        self.sync.set_selected(review, False)
        self.assertEqual(SelectAll.NOT_ALL, self.sync.status().select_all)

    def test_selecting_the_current_state_is_silent(self):
        (key,) = self.scanning((1, selection(1)))
        del self.changes[:]
        self.sync.set_selected(key, True)
        self.assertEqual([], self.changes)

    def test_select_all_and_none(self):
        ready, review, current = self.scanning(
            (1, selection(1), selection(2, Verdict.REVIEW), selection(3, Verdict.UP_TO_DATE))
        )
        del self.changes[:]
        self.sync.set_all_selected(True)
        self.assertEqual([RowChanged(review), StatusChanged()], self.changes)
        self.assertEqual([True, True, False], [self.sync.is_selected(k) for k in (ready, review, current)])
        self.assertEqual(SelectAll.ALL, self.sync.status().select_all)
        del self.changes[:]
        self.sync.set_all_selected(True)
        self.assertEqual([], self.changes)
        self.sync.set_all_selected(False)
        self.assertEqual(SelectAll.NOT_ALL, self.sync.status().select_all)
        self.assertFalse(any(self.sync.is_selected(k) for k in (ready, review, current)))

    def test_started_downloads_leave_the_selection_and_a_failure_does_not_restore_it(self):
        (key,) = self.scanning((1, selection(1)))
        self.sync.download_selected()
        self.assertFalse(self.sync.is_selected(key))
        self.finish_download(key, succeeded=False)
        self.assertFalse(self.sync.is_selected(key))
        self.assertEqual(SelectAll.NOT_ALL, self.sync.status().select_all)
        self.sync.set_selected(key, True)
        self.assertTrue(self.sync.is_selected(key))

    def test_locking_deselects_and_unlocking_does_not_reselect(self):
        (key,) = self.scanning((1, selection(1)))
        self.sync.set_locked(1, True)
        self.assertFalse(self.sync.is_selected(key))
        self.assertTrue(self.sync.entry(key).locked)
        self.assertEqual(SelectAll.UNAVAILABLE, self.sync.status().select_all)
        self.sync.set_locked(1, False)
        self.assertFalse(self.sync.is_selected(key))
        self.assertEqual(SelectAll.NOT_ALL, self.sync.status().select_all)


class DownloadBatchTest(SyncTestCase):
    max_downloads = 1

    def test_batch_counts_until_every_download_finishes(self):
        a, b = self.scanning((1, selection(1)), (2, selection(2)))
        self.sync.download_selected()
        self.assertEqual(InProgress(frozenset({a, b}), 0, 0), self.sync.status().batch)
        self.assertEqual(2, self.sync.status().batch.total)
        self.assertEqual([Downloading, Queued], self.progress(a, b))
        self.finish_download(a)
        self.assertEqual(InProgress(frozenset({a, b}), 1, 0), self.sync.status().batch)
        self.finish_download(b)
        self.assertEqual(Finished(2, 0), self.sync.status().batch)

    def test_failures_are_counted(self):
        a, b = self.scanning((1, selection(1)), (2, selection(2)))
        self.sync.download_selected()
        self.finish_download(a, succeeded=False)
        self.assertEqual(InProgress(frozenset({a, b}), 1, 1), self.sync.status().batch)
        self.finish_download(b)
        self.assertEqual(Finished(1, 1), self.sync.status().batch)

    def test_a_new_download_after_finishing_starts_a_new_batch(self):
        a, b = self.scanning((1, selection(1)), (2, selection(2, Verdict.REVIEW)))
        self.sync.download_selected()
        self.finish_download(a)
        self.assertEqual(Finished(1, 0), self.sync.status().batch)
        self.sync.set_selected(b, True)
        self.sync.download_selected()
        self.assertEqual(InProgress(frozenset({b}), 0, 0), self.sync.status().batch)

    def test_downloads_added_while_running_join_the_batch(self):
        a, b = self.scanning((1, selection(1)), (2, selection(2, Verdict.REVIEW)))
        self.sync.download_selected()
        self.sync.set_selected(b, True)
        self.sync.download_selected()
        self.assertEqual(InProgress(frozenset({a, b}), 0, 0), self.sync.status().batch)

    def test_a_dequeued_download_counts_as_finished(self):
        a, b = self.scanning((1, selection(1)), (2, selection(2)))
        self.sync.download_selected()
        self.sync.set_locked(2, True)
        self.assertEqual(InProgress(frozenset({a, b}), 1, 0), self.sync.status().batch)
        self.finish_download(a)
        self.assertEqual(Finished(2, 0), self.sync.status().batch)

    def test_nothing_selected_starts_nothing(self):
        self.scanning((1, selection(1, Verdict.REVIEW)))
        self.sync.download_selected()
        self.assertEqual(NoBatch(), self.sync.status().batch)
        self.assertEqual([], self.downloader.calls)

    def test_downloads_are_not_gated_by_the_scan_and_a_failed_scan_leaves_the_batch_alone(self):
        (key,) = self.scanning((1, selection(1)))
        self.assertIsInstance(self.sync.status().scan, Scanning)
        self.assertTrue(self.sync.status().can_download)
        self.sync.download_selected()
        self.runner.deliver(FAILED)
        self.assertEqual(Stopped(FAILED), self.sync.status().scan)
        self.assertEqual(InProgress(frozenset({key}), 0, 0), self.sync.status().batch)
        self.finish_download(key)
        self.assertEqual(Finished(1, 0), self.sync.status().batch)
        self.assertEqual(Stopped(FAILED), self.sync.status().scan)


class InstallTest(SyncTestCase):
    def test_installs_every_downloaded_unlocked_entry(self):
        a, b, c = self.scanning((1, selection(1)), (2, selection(2)), (3, selection(3)))
        self.assertFalse(self.sync.status().can_install)
        self.sync.download_selected()
        paths = [self.finish_download(k) for k in (a, b, c)]
        self.sync.set_locked(3, True)
        self.assertEqual(frozenset({a, b}), self.sync.status().installable)
        self.sync.install_downloaded(self.sync.status().installable)
        self.assertEqual([(paths[0], "Mod 1"), (paths[1], "Mod 2")], self.installer.calls)
        self.assertEqual([Installed, Installed, Downloaded], self.progress(a, b, c))
        self.assertFalse(self.sync.status().can_install)

    def test_only_the_given_entries_are_installed(self):
        a, b = self.scanning((1, selection(1)), (2, selection(2)))
        self.sync.download_selected()
        self.finish_download(a)
        shown = self.sync.status().installable
        self.finish_download(b)  # Finishes while the user is being asked.
        self.sync.install_downloaded(shown)
        self.assertEqual([Installed, Downloaded], self.progress(a, b))
        self.assertEqual(frozenset({b}), self.sync.status().installable)

    def test_nothing_to_install(self):
        self.scanning((1, selection(1)))
        self.sync.install_downloaded(frozenset())
        self.assertEqual([], self.installer.calls)


class CloseTest(SyncTestCase):
    def test_close_stops_the_runner_and_can_be_repeated(self):
        self.sync.start()
        self.sync.close()
        self.sync.close()
        self.assertTrue(self.runner.stopped)

    def test_a_closed_sync_does_not_start(self):
        self.sync.close()
        self.sync.start()
        self.assertEqual(0, self.runner.starts)
        self.assertEqual(NotStarted(), self.sync.status().scan)

    def test_close_before_starting_and_after_nothing_to_scan(self):
        self.sync.close()
        sync = Sync(self.session, ScanPlan(FakeSource({}), GAME, []), self.runner)
        sync.start()
        sync.close()
        self.assertEqual(NothingToScan(), sync.status().scan)

    def test_close_withdraws_downloads_and_silences_notifications(self):
        (key,) = self.scanning((1, selection(1)))
        self.sync.download_selected()
        self.sync.close()
        del self.changes[:]
        self.finish_download(key)
        self.assertEqual([Downloading], self.progress(key))
        self.assertEqual([], self.changes)


if __name__ == "__main__":
    unittest.main()
