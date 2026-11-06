import unittest

from nexus_auto_updater.tracking.tracker import DEFAULT_RETRY_DELAYS_MS, DownloadTracker, Expiry
from tests.fakes import ArchiveIds, ManualScheduler

ARCHIVE = r"C:\Modding\MO2\downloads\SkyUI_5_2_SE-12604-5-2SE.7z"
OTHER = r"C:\Modding\MO2\downloads\Unrelated-999-1-0.zip"


def never(_name):
    return False


class TrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.ids = ArchiveIds()
        self.scheduler = ManualScheduler()
        self.tracker = DownloadTracker(self.ids, self.scheduler)
        self.resolved = []
        self.tracker.set_listener(lambda *args: self.resolved.append(args))


class MatchingTest(TrackerTestCase):
    def test_nothing_expected_is_a_no_op(self):
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.ids.calls)
        self.assertEqual([], self.scheduler.pending)
        self.assertEqual([], self.resolved)

    def test_file_id_match(self):
        for meta_mod_id in (0, 12604):
            with self.subTest(meta_mod_id=meta_mod_id):
                self.setUp()
                other = self.tracker.expect(12604, 2, never)
                ticket = self.tracker.expect(12604, 1, never)
                self.ids.ids[ARCHIVE] = (meta_mod_id, 1)
                self.tracker.download_finished(ARCHIVE, True)
                self.assertEqual([(ticket, ARCHIVE, True)], self.resolved)
                self.assertEqual([], self.scheduler.pending)
                self.assertNotEqual(ticket, other)

    def test_file_id_of_another_mod_does_not_match(self):
        self.tracker.expect(12604, 1, never)
        self.ids.ids[ARCHIVE] = (555, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.scheduler.pending)

    def test_file_id_not_ours_is_final_even_if_the_name_matches(self):
        self.tracker.expect(12604, 1, lambda name: True)
        self.ids.ids[ARCHIVE] = (12604, 77)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.scheduler.pending)

    def test_name_match_uses_the_basename_of_a_windows_path(self):
        seen = []

        def matcher(name):
            seen.append(name)
            return name == "SkyUI_5_2_SE-12604-5-2SE.7z"

        ticket = self.tracker.expect(12604, 1, matcher)
        self.tracker.download_finished(ARCHIVE, False)
        self.assertEqual(["SkyUI_5_2_SE-12604-5-2SE.7z"], seen)
        self.assertEqual([(ticket, ARCHIVE, False)], self.resolved)

    def test_unique_mod_id_match(self):
        self.tracker.expect(1, 5, never)
        ticket = self.tracker.expect(12604, 1, never)
        self.ids.ids[ARCHIVE] = (12604, 0)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([(ticket, ARCHIVE, True)], self.resolved)

    def test_mod_id_of_no_expectation_does_not_retry(self):
        self.tracker.expect(1, 5, never)
        self.ids.ids[ARCHIVE] = (12604, 0)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.scheduler.pending)

    def test_several_expectations_for_one_mod_wait_for_the_file_id(self):
        self.tracker.expect(12604, 1, never)
        second = self.tracker.expect(12604, 2, never)
        self.ids.ids[ARCHIVE] = (12604, 0)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.resolved)
        self.assertEqual([250], self.scheduler.delays)
        self.ids.ids[ARCHIVE] = (12604, 2)
        self.scheduler.fire_next()
        self.assertEqual([(second, ARCHIVE, True)], self.resolved)


class RetryTest(TrackerTestCase):
    def test_retries_at_250_500_1000_then_gives_up(self):
        self.assertEqual((250, 500, 1000), DEFAULT_RETRY_DELAYS_MS)
        self.tracker.expect(12604, 1, never)
        self.tracker.download_finished(ARCHIVE, True)
        self.scheduler.fire_all()
        self.assertEqual([250, 500, 1000], self.scheduler.delays)
        self.assertEqual([ARCHIVE] * 4, self.ids.calls)
        self.assertEqual([], self.resolved)

    def test_a_retry_that_sees_metadata_resolves_with_the_original_outcome(self):
        ticket = self.tracker.expect(12604, 1, never)
        self.tracker.download_finished(ARCHIVE, False)
        self.scheduler.fire_next()
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.scheduler.fire_next()
        self.assertEqual([(ticket, ARCHIVE, False)], self.resolved)
        self.assertEqual([250, 500], self.scheduler.delays)
        self.assertEqual([], self.scheduler.pending)

    def test_retry_after_the_expectation_was_resolved_elsewhere_is_a_no_op(self):
        ticket = self.tracker.expect(12604, 1, never)
        self.tracker.download_finished(OTHER, True)
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([(ticket, ARCHIVE, True)], self.resolved)
        calls = len(self.ids.calls)
        self.scheduler.fire_all()
        self.assertEqual(calls, len(self.ids.calls))

    def test_custom_retry_delays(self):
        tracker = DownloadTracker(self.ids, self.scheduler, retry_delays_ms=[10])
        tracker.expect(1, 1, never)
        tracker.download_finished(ARCHIVE, True)
        self.scheduler.fire_all()
        self.assertEqual([10], self.scheduler.delays)


class LifecycleTest(TrackerTestCase):
    def test_tickets_are_unique(self):
        tickets = [self.tracker.expect(1, i, never) for i in range(1, 6)]
        self.assertEqual(len(tickets), len(set(tickets)))

    def test_resolved_expectations_are_removed(self):
        self.tracker.expect(12604, 1, never)
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual(1, len(self.resolved))
        self.assertEqual(1, len(self.ids.calls))  # Second call: nothing expected.

    def test_cancel(self):
        ticket = self.tracker.expect(12604, 1, never)
        self.tracker.cancel(ticket)
        self.tracker.cancel(ticket)  # Unknown tickets are ignored.
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.resolved)

    def test_without_listener_the_match_is_still_consumed(self):
        self.tracker.set_listener(None)
        self.tracker.expect(12604, 1, never)
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.tracker.set_listener(lambda *args: self.resolved.append(args))
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([], self.resolved)


class ExpiryTest(TrackerTestCase):
    def setUp(self):
        super().setUp()
        self.expired = []
        self.ticket = self.tracker.expect(12604, 1, never, Expiry(1000, self.expired.append))

    def test_an_unresolved_expectation_expires_once(self):
        self.assertEqual([1000], self.scheduler.delays)
        self.scheduler.fire_all()
        self.assertEqual([self.ticket], self.expired)

    def test_a_resolved_expectation_does_not_expire(self):
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.scheduler.fire_all()
        self.assertEqual([], self.expired)

    def test_a_cancelled_expectation_does_not_expire(self):
        self.tracker.cancel(self.ticket)
        self.scheduler.fire_all()
        self.assertEqual([], self.expired)

    def test_an_expired_expectation_still_resolves(self):
        self.scheduler.fire_all()
        self.ids.ids[ARCHIVE] = (12604, 1)
        self.tracker.download_finished(ARCHIVE, True)
        self.assertEqual([(self.ticket, ARCHIVE, True)], self.resolved)

    def test_expectations_without_an_expiry_schedule_nothing(self):
        self.scheduler.pending.clear()
        self.tracker.expect(12604, 2, never)
        self.assertEqual([], self.scheduler.pending)


if __name__ == "__main__":
    unittest.main()
