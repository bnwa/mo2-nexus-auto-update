import unittest

from nexus_auto_updater.app.scan import ModScan
from nexus_auto_updater.app.session import EntryStatus, Phase, UpdateEntry, UpdateSession, _never
from nexus_auto_updater.domain.files import ModFile
from nexus_auto_updater.domain.selection import Selection, Verdict
from nexus_auto_updater.tracking.tracker import DownloadTracker
from tests.fakes import (
    ArchiveIds,
    FakeDownloader,
    FakeInstaller,
    FakeLocks,
    FakeMatcher,
    ManualScheduler,
    installed_mod,
)

DOWNLOADS = "C:\\Modding\\MO2\\downloads\\"


def selection(installed_id, verdict=Verdict.READY, target_id=None):
    target = None
    if verdict in (Verdict.READY, Verdict.REVIEW):
        target = ModFile(target_id or installed_id + 100)
    return Selection(
        installed_id, verdict, ModFile(installed_id), target, f"why {installed_id}",
        (target,) if target else (),
    )


def archive(file_id):
    return f"{DOWNLOADS}File-{file_id}.7z"


class SessionTestCase(unittest.TestCase):
    max_downloads = 3

    def setUp(self):
        self.ids = ArchiveIds()
        self.scheduler = ManualScheduler()
        self.tracker = DownloadTracker(self.ids, self.scheduler)
        self.downloader = FakeDownloader()
        self.installer = FakeInstaller()
        self.locks = FakeLocks()
        self.session = UpdateSession(
            self.downloader, self.installer, self.tracker, self.locks, self.max_downloads
        )
        self.changes = []
        self.session.subscribe(self.changes.append)

    def record(self, mod_id, *selections, matchers=None, **mod_kwargs):
        ids = tuple(s.installed_file_id for s in selections)
        scan = ModScan(installed_mod(mod_id, ids, **mod_kwargs), tuple(selections), matchers or {})
        return self.session.record_scan(scan)

    def ready(self, mod_id, file_id):
        (entry,) = self.record(mod_id, selection(file_id))
        return entry

    def entry(self, key) -> UpdateEntry:
        return {e.key: e for e in self.session.entries()}[key]

    def finish(self, entry, succeeded=True):
        """Report the entry's archive with matching .meta IDs."""
        path = archive(entry.target.file_id)
        self.ids.ids[path] = (entry.mod_id, entry.target.file_id)
        self.tracker.download_finished(path, succeeded)
        return path

    def phases(self, *entries):
        return [self.entry(e.key).phase for e in entries]


class RecordScanTest(SessionTestCase):
    def test_skipped_and_error_scans(self):
        mod = installed_mod(5, (), name="Old LE Mod", version="1", newest_version="2")
        (skipped,) = self.session.record_scan(ModScan(mod, skipped_reason="Other game"))
        (error,) = self.session.record_scan(ModScan(mod, error="HTTP 404"))
        self.assertIs(EntryStatus.SKIPPED, skipped.status)
        self.assertEqual("Other game", skipped.reason)
        self.assertIs(EntryStatus.ERROR, error.status)
        self.assertEqual("HTTP 404", error.reason)
        for entry in (skipped, error):
            self.assertEqual(("Old LE Mod", 5, "1", "2"), (entry.mod_name, entry.mod_id, entry.version, entry.newest_version))
            self.assertIsNone(entry.installed_file_id)
            self.assertIsNone(entry.target)
            self.assertIs(Phase.IDLE, entry.phase)
            self.assertFalse(entry.can_download)
        self.assertNotEqual(skipped.key, error.key)
        self.assertEqual([], self.changes)

    def test_verdicts_map_to_statuses(self):
        entries = self.record(
            1,
            selection(1, Verdict.READY),
            selection(2, Verdict.REVIEW),
            selection(3, Verdict.UP_TO_DATE),
            selection(4, Verdict.UNRESOLVED),
        )
        self.assertEqual(
            [EntryStatus.READY, EntryStatus.REVIEW, EntryStatus.UP_TO_DATE, EntryStatus.UNRESOLVED],
            [e.status for e in entries],
        )
        self.assertEqual([True, True, False, False], [e.can_download for e in entries])
        first = entries[0]
        self.assertEqual((1, ModFile(1), ModFile(101), "why 1", (ModFile(101),)),
                         (first.installed_file_id, first.installed, first.target, first.reason, first.newer_files))
        self.assertEqual(entries, self.session.entries())
        self.assertEqual(4, len({e.key for e in entries}))

    def test_locked_is_read_at_scan_time(self):
        self.locks.locked.add(1)
        locked = self.ready(1, 1)
        self.locks.locked.discard(1)
        later = self.ready(1, 2)
        other = self.ready(2, 3)
        self.assertEqual([True, False, False], [locked.locked, later.locked, other.locked])

    def test_matcher_is_used_for_name_matching(self):
        name = "SkyUI_5_2_SE-12604-5-2SE.7z"
        (entry,) = self.record(1, selection(1), matchers={101: FakeMatcher(name), 999: FakeMatcher("x")})
        self.session.download(entry.key)
        self.tracker.download_finished(DOWNLOADS + name, True)
        self.assertIs(Phase.DOWNLOADED, self.entry(entry.key).phase)
        self.assertEqual(DOWNLOADS + name, self.entry(entry.key).archive_path)

    def test_without_matcher_names_never_match(self):
        self.assertFalse(_never("anything.7z"))
        entry = self.ready(1, 1)
        self.session.download(entry.key)
        self.tracker.download_finished(archive(101), True)
        self.assertIs(Phase.DOWNLOADING, self.entry(entry.key).phase)
        self.assertEqual([250], self.scheduler.delays)


class DownloadTest(SessionTestCase):
    def test_download_starts_and_notifies_queued_then_downloading(self):
        entry = self.ready(12604, 1)
        self.session.download(entry.key)
        self.assertEqual([Phase.QUEUED, Phase.DOWNLOADING], [c.phase for c in self.changes])
        self.assertEqual([(12604, 101)], self.downloader.calls)
        path = self.finish(entry)
        final = self.entry(entry.key)
        self.assertIs(Phase.DOWNLOADED, final.phase)
        self.assertEqual(path, final.archive_path)
        self.assertTrue(final.can_install)
        self.assertFalse(final.can_download)

    def test_failed_download_can_be_requeued(self):
        entry = self.ready(1, 1)
        self.session.download(entry.key)
        self.finish(entry, succeeded=False)
        failed = self.entry(entry.key)
        self.assertIs(Phase.DOWNLOAD_FAILED, failed.phase)
        self.assertFalse(failed.can_install)
        self.assertTrue(failed.can_download)
        self.session.download(entry.key)
        self.assertIs(Phase.DOWNLOADING, self.entry(entry.key).phase)
        self.assertEqual("", self.entry(entry.key).archive_path)
        self.assertEqual(2, len(self.downloader.calls))

    def test_refuses_entries_that_are_not_offered(self):
        entries = self.record(1, selection(1, Verdict.UP_TO_DATE), selection(2, Verdict.UNRESOLVED))
        mod = installed_mod(2, ())
        entries += self.session.record_scan(ModScan(mod, skipped_reason="x"))
        entries += self.session.record_scan(ModScan(mod, error="y"))
        for entry in entries:
            self.session.download(entry.key)
        self.assertEqual([], self.downloader.calls)
        self.assertEqual([], self.changes)

    def test_review_entries_can_be_downloaded(self):
        (entry,) = self.record(1, selection(1, Verdict.REVIEW))
        self.session.download(entry.key)
        self.assertEqual([(1, 101)], self.downloader.calls)

    def test_queued_or_downloading_cannot_be_queued_twice(self):
        session = UpdateSession(self.downloader, self.installer, self.tracker, self.locks, 1)
        a, b = (session.record_scan(ModScan(installed_mod(i, (i,)), (selection(i),)))[0] for i in (1, 2))
        session.download(a.key)
        session.download(b.key)
        session.download(a.key)  # DOWNLOADING
        session.download(b.key)  # QUEUED
        self.assertEqual([(1, 101)], self.downloader.calls)
        self.finish(a)
        self.finish(b)
        self.assertEqual([(1, 101), (2, 102)], self.downloader.calls)
        self.assertEqual([], self.scheduler.pending)

    def test_unknown_ticket_is_ignored(self):
        entry = self.ready(1, 1)
        self.session._on_download_resolved(999, archive(101), True)
        self.assertEqual([], self.changes)
        self.assertIs(Phase.IDLE, self.entry(entry.key).phase)

    def test_unrelated_archive_changes_nothing(self):
        entry = self.ready(1, 1)
        self.session.download(entry.key)
        self.ids.ids[archive(5)] = (77, 5)
        self.tracker.download_finished(archive(5), True)
        self.assertIs(Phase.DOWNLOADING, self.entry(entry.key).phase)


class QueueTest(SessionTestCase):
    max_downloads = 2

    def test_never_more_than_the_limit_in_flight(self):
        entries = [self.ready(i, i) for i in range(1, 6)]
        for entry in entries:
            self.session.download(entry.key)
        self.assertEqual([Phase.DOWNLOADING] * 2 + [Phase.QUEUED] * 3, self.phases(*entries))
        self.assertEqual(2, len(self.downloader.calls))
        self.finish(entries[1])
        self.assertEqual(
            [Phase.DOWNLOADING, Phase.DOWNLOADED, Phase.DOWNLOADING, Phase.QUEUED, Phase.QUEUED],
            self.phases(*entries),
        )
        self.finish(entries[0], succeeded=False)  # A failure frees a slot too.
        self.assertEqual(
            [Phase.DOWNLOAD_FAILED, Phase.DOWNLOADED, Phase.DOWNLOADING, Phase.DOWNLOADING, Phase.QUEUED],
            self.phases(*entries),
        )
        self.assertEqual([(i, 100 + i) for i in (1, 2, 3, 4)], self.downloader.calls)

    def test_close_cancels_clears_and_unsubscribes(self):
        entries = [self.ready(i, i) for i in range(1, 4)]
        for entry in entries:
            self.session.download(entry.key)
        self.changes.clear()
        self.session.close()
        self.assertIsNone(self.tracker._listener)
        self.assertEqual({}, self.tracker._expected)
        self.finish(entries[0])
        self.session._on_download_resolved(1, archive(101), True)
        self.assertEqual([], self.changes)
        self.assertEqual(2, len(self.downloader.calls))
        self.assertEqual([Phase.DOWNLOADING, Phase.DOWNLOADING, Phase.QUEUED], self.phases(*entries))


class StrictOrderTest(SessionTestCase):
    max_downloads = 1

    def test_limit_of_one_runs_strictly_in_order(self):
        entries = [self.ready(i, i) for i in (3, 1, 2)]
        for entry in entries:
            self.session.download(entry.key)
        for index, entry in enumerate(entries):
            self.assertEqual(index + 1, len(self.downloader.calls))
            self.assertEqual((entry.mod_id, entry.target.file_id), self.downloader.calls[-1])
            self.finish(entry, succeeded=index != 1)
        self.assertEqual(
            [Phase.DOWNLOADED, Phase.DOWNLOAD_FAILED, Phase.DOWNLOADED], self.phases(*entries)
        )

    def test_dequeued_entry_does_not_hold_a_slot(self):
        a, b, c = (self.ready(i, i) for i in (1, 2, 3))
        for entry in (a, b, c):
            self.session.download(entry.key)
        self.session.set_locked(2, True)
        self.finish(a)
        self.assertEqual([Phase.DOWNLOADED, Phase.IDLE, Phase.DOWNLOADING], self.phases(a, b, c))
        self.assertEqual([(1, 101), (3, 103)], self.downloader.calls)


class LockTest(SessionTestCase):
    def test_locked_before_scan_loads_locked_and_refuses_downloads(self):
        self.locks.locked.add(1)
        entry = self.ready(1, 1)
        self.assertTrue(entry.locked)
        self.assertFalse(entry.can_download)
        self.session.download(entry.key)
        self.assertEqual([], self.downloader.calls)
        self.assertEqual([], self.changes)

    def test_lock_covers_every_entry_of_the_mod_and_no_other(self):
        mine = self.record(1, selection(1), selection(2), selection(3, Verdict.UP_TO_DATE))
        other = self.ready(2, 9)
        self.session.set_locked(1, True)
        self.assertEqual([(1, True)], self.locks.set_calls)
        self.assertEqual(sorted(e.key for e in mine), sorted(c.key for c in self.changes))
        self.assertTrue(all(self.entry(e.key).locked for e in mine))
        self.assertFalse(self.entry(other.key).locked)

    def test_locking_queued_dequeues_to_idle(self):
        session = UpdateSession(self.downloader, self.installer, self.tracker, self.locks, 1)
        session.subscribe(self.changes.append)
        a, b = (session.record_scan(ModScan(installed_mod(i, (i,)), (selection(i),)))[0] for i in (1, 2))
        session.download(a.key)
        session.download(b.key)
        self.changes.clear()
        session.set_locked(2, True)
        (change,) = self.changes
        self.assertEqual((b.key, Phase.IDLE, True), (change.key, change.phase, change.locked))
        self.finish(a)
        self.assertEqual([(1, 101)], self.downloader.calls)

    def test_locking_downloading_keeps_it_in_flight_but_blocks_install(self):
        entry = self.ready(1, 1)
        self.session.download(entry.key)
        self.session.set_locked(1, True)
        self.assertIs(Phase.DOWNLOADING, self.entry(entry.key).phase)
        self.finish(entry)
        downloaded = self.entry(entry.key)
        self.assertIs(Phase.DOWNLOADED, downloaded.phase)
        self.assertFalse(downloaded.can_install)
        self.session.install(entry.key)
        self.assertEqual([], self.installer.calls)
        self.session.set_locked(1, False)
        self.assertTrue(self.entry(entry.key).can_install)

    def test_unlocking_makes_it_downloadable_without_starting_anything(self):
        self.locks.locked.add(1)
        entry = self.ready(1, 1)
        self.session.set_locked(1, False)
        unlocked = self.entry(entry.key)
        self.assertFalse(unlocked.locked)
        self.assertTrue(unlocked.can_download)
        self.assertIs(Phase.IDLE, unlocked.phase)
        self.assertEqual([], self.downloader.calls)
        self.assertEqual([(1, False)], self.locks.set_calls)

    def test_setting_the_current_state_notifies_no_one_but_still_persists(self):
        self.ready(1, 1)
        self.session.set_locked(1, False)
        self.assertEqual([], self.changes)
        self.assertEqual([(1, False)], self.locks.set_calls)


class InstallTest(SessionTestCase):
    def downloaded(self):
        entry = self.ready(7, 1)
        self.session.download(entry.key)
        return entry, self.finish(entry)

    def test_install(self):
        entry, path = self.downloaded()
        self.session.install(entry.key)
        self.assertEqual([(path, "Mod 7")], self.installer.calls)
        self.assertIs(Phase.INSTALLED, self.entry(entry.key).phase)
        self.assertFalse(self.entry(entry.key).can_install)

    def test_install_cancelled(self):
        self.installer.result = False
        entry, _ = self.downloaded()
        self.session.install(entry.key)
        self.assertIs(Phase.INSTALL_CANCELLED, self.entry(entry.key).phase)

    def test_refused_when_not_installable(self):
        entry = self.ready(1, 1)
        self.session.install(entry.key)
        self.session.download(entry.key)
        self.session.install(entry.key)
        self.assertEqual([], self.installer.calls)

    def test_downloaded_without_archive_path_is_not_installable(self):
        entry = UpdateEntry(1, "m", 1, "", "", 1, None, ModFile(2), EntryStatus.READY, "", Phase.DOWNLOADED)
        self.assertFalse(entry.can_install)


class ListenerTest(SessionTestCase):
    def test_unsubscribed_session_still_works(self):
        self.session.subscribe(None)
        entry = self.ready(1, 1)
        self.session.download(entry.key)
        self.assertIs(Phase.DOWNLOADING, self.entry(entry.key).phase)
        self.assertEqual([], self.changes)


if __name__ == "__main__":
    unittest.main()
