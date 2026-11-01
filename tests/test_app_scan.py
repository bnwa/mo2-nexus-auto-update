import unittest

from nexus_auto_updater.app.ports import (
    FileListing,
    SourceError,
    SourceIncompatible,
    SourceUnavailable,
)
from nexus_auto_updater.app.scan import (
    MAX_CONSECUTIVE_UNAVAILABLE,
    ModScanned,
    ScanFailed,
    ScanPlan,
    ScanProgress,
    StopReason,
)
from nexus_auto_updater.domain.selection import Verdict
from tests.fakes import GAME, FakeMatcher, FakeSource, installed_mod, mod_file


def listing(*files, matchers=None):
    return FileListing(tuple(files), matchers or {})


UP_TO_DATE = listing(mod_file(1))


def run(plan, should_stop=lambda: False):
    return list(plan.run(should_stop))


def scanned(events):
    return [e.scan for e in events if isinstance(e, ModScanned)]


class PlanShapeTest(unittest.TestCase):
    def test_blocked_mods_are_skipped_first_and_not_counted(self):
        blocked = installed_mod(5, blocker="Other game")
        mods = [installed_mod(1), blocked, installed_mod(2)]
        source = FakeSource({1: UP_TO_DATE, 2: UP_TO_DATE})
        plan = ScanPlan(source, GAME, mods)
        self.assertEqual(2, plan.total)
        self.assertFalse(plan.is_empty)
        events = run(plan)
        self.assertEqual(ModScanned, type(events[0]))
        self.assertEqual(blocked, events[0].scan.mod)
        self.assertEqual("Other game", events[0].scan.skipped_reason)
        self.assertEqual([(GAME, 1), (GAME, 2)], source.list_calls)

    def test_is_empty(self):
        self.assertTrue(ScanPlan(FakeSource(), GAME, []).is_empty)
        only_blocked = ScanPlan(FakeSource(), GAME, [installed_mod(blocker="x")])
        self.assertFalse(only_blocked.is_empty)
        self.assertEqual(0, only_blocked.total)

    def test_only_blocked_mods_emit_no_progress(self):
        events = run(ScanPlan(FakeSource(), GAME, [installed_mod(blocker="x")]))
        self.assertEqual(1, len(events))
        self.assertFalse(any(isinstance(e, ScanProgress) for e in events))

    def test_progress_events(self):
        mods = [installed_mod(1, name="Alpha"), installed_mod(2, name="Beta")]
        events = run(ScanPlan(FakeSource({1: UP_TO_DATE, 2: UP_TO_DATE}), GAME, mods))
        progress = [e for e in events if isinstance(e, ScanProgress)]
        self.assertEqual(
            [ScanProgress(0, 2, "Alpha"), ScanProgress(1, 2, "Beta"), ScanProgress(2, 2, "")], progress
        )
        self.assertIsInstance(events[0], ScanProgress)
        self.assertEqual(ScanProgress(2, 2, ""), events[-1])

    def test_should_stop_is_honoured_before_each_mod(self):
        source = FakeSource({1: UP_TO_DATE, 2: UP_TO_DATE})
        plan = ScanPlan(source, GAME, [installed_mod(1), installed_mod(2)])
        calls = []

        def should_stop():
            calls.append(1)
            return len(calls) > 1

        events = run(plan, should_stop)
        self.assertEqual([(GAME, 1)], source.list_calls)
        self.assertFalse(any(e == ScanProgress(2, 2, "") for e in events))

    def test_should_stop_immediately(self):
        source = FakeSource()
        self.assertEqual([], run(ScanPlan(source, GAME, [installed_mod(1)]), lambda: True))
        self.assertEqual([], source.list_calls)


class SelectionsTest(unittest.TestCase):
    def test_selections_and_matchers_only_for_selected_targets(self):
        matcher_target = FakeMatcher("a")
        files = (mod_file(1), mod_file(2), mod_file(3, group_id=20), mod_file(4, group_id=20))
        matchers = {2: matcher_target, 1: FakeMatcher("installed"), 3: FakeMatcher("old")}
        mods = [installed_mod(1, (1, 3))]
        (scan,) = scanned(run(ScanPlan(FakeSource({1: listing(*files, matchers=matchers)}), GAME, mods)))
        self.assertEqual([Verdict.READY, Verdict.READY], [s.verdict for s in scan.selections])
        self.assertEqual([2, 4], [s.target.file_id for s in scan.selections])
        # Target 4 has no matcher; non-targets' matchers are dropped.
        self.assertEqual({2: matcher_target}, dict(scan.target_matchers))
        self.assertEqual("", scan.error)
        self.assertEqual("", scan.skipped_reason)

    def test_no_target_no_matchers(self):
        (scan,) = scanned(run(ScanPlan(FakeSource({1: UP_TO_DATE}), GAME, [installed_mod(1)])))
        self.assertIs(Verdict.UP_TO_DATE, scan.selections[0].verdict)
        self.assertEqual({}, dict(scan.target_matchers))


class ErrorsTest(unittest.TestCase):
    def test_source_error_is_reported_per_mod(self):
        source = FakeSource({1: SourceError("mod hidden"), 2: UP_TO_DATE})
        events = run(ScanPlan(source, GAME, [installed_mod(1), installed_mod(2)]))
        scans = scanned(events)
        self.assertEqual("mod hidden", scans[0].error)
        self.assertEqual((), scans[0].selections)
        self.assertEqual("", scans[1].error)
        self.assertFalse(any(isinstance(e, ScanFailed) for e in events))

    def test_first_incompatible_stops_the_scan(self):
        mods = [installed_mod(i) for i in range(1, 6)]
        source = FakeSource({1: UP_TO_DATE, 2: SourceIncompatible("field gone")})
        events = run(ScanPlan(source, GAME, mods))
        self.assertEqual(ScanFailed(StopReason.INCOMPATIBLE, "field gone", 4), events[-1])
        self.assertEqual([1, 2], [m for _, m in source.list_calls])
        self.assertEqual(1, len(scanned(events)))

    def test_incompatible_on_the_first_mod_leaves_all_unchecked(self):
        events = run(ScanPlan(FakeSource({1: SourceIncompatible("x")}), GAME, [installed_mod(1)]))
        self.assertEqual(ScanFailed(StopReason.INCOMPATIBLE, "x", 1), events[-1])

    def test_three_consecutive_unavailable_stop_the_scan(self):
        self.assertEqual(3, MAX_CONSECUTIVE_UNAVAILABLE)
        mods = [installed_mod(i) for i in range(1, 7)]
        source = FakeSource({1: UP_TO_DATE, 2: SourceUnavailable("a"), 3: SourceUnavailable("b"),
                             4: SourceUnavailable("c")})
        events = run(ScanPlan(source, GAME, mods))
        self.assertEqual(ScanFailed(StopReason.UNAVAILABLE, "c", 2), events[-1])
        self.assertEqual(["", "a", "b", "c"], [s.error for s in scanned(events)])
        self.assertEqual([1, 2, 3, 4], [m for _, m in source.list_calls])

    def test_unavailable_on_the_last_mods_leaves_none_unchecked(self):
        mods = [installed_mod(i) for i in range(1, 4)]
        source = FakeSource({i: SourceUnavailable("down") for i in range(1, 4)})
        events = run(ScanPlan(source, GAME, mods))
        self.assertEqual(ScanFailed(StopReason.UNAVAILABLE, "down", 0), events[-1])

    def test_success_resets_the_unavailable_count(self):
        self._assert_reset(UP_TO_DATE)

    def test_plain_source_error_resets_the_unavailable_count(self):
        self._assert_reset(SourceError("hidden"))

    def _assert_reset(self, middle):
        down = SourceUnavailable("down")
        script = {1: down, 2: down, 3: middle, 4: down, 5: down, 6: UP_TO_DATE}
        mods = [installed_mod(i) for i in range(1, 7)]
        events = run(ScanPlan(FakeSource(script), GAME, mods))
        self.assertFalse(any(isinstance(e, ScanFailed) for e in events))
        self.assertEqual(6, len(scanned(events)))
        self.assertEqual(ScanProgress(6, 6, ""), events[-1])


if __name__ == "__main__":
    unittest.main()
