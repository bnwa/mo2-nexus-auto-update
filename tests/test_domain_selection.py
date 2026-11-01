import unittest

from nexus_auto_updater.domain.files import FileCategory as C
from nexus_auto_updater.domain.selection import (
    _INCOMPLETE_NOTE,
    Verdict,
    plan_updates,
    select_successor,
)
from tests.fakes import T0, mod_file


def primary(file_id, **kwargs):
    kwargs.setdefault("group_id", 99)
    return mod_file(file_id, is_primary=True, **kwargs)


class InstalledFileMissingTest(unittest.TestCase):
    def test_unlisted_file_falls_back_to_the_single_primary_main(self):
        main = primary(50)
        result = select_successor(7, [mod_file(1), main])
        self.assertIs(Verdict.REVIEW, result.verdict)
        self.assertIsNone(result.installed)
        self.assertIs(main, result.target)
        self.assertEqual(7, result.installed_file_id)
        self.assertEqual(
            "Installed file 7 is not listed on Nexus. Suggesting the mod's primary main file.",
            result.reason,
        )
        self.assertEqual((main,), result.newer_files)

    def test_no_recorded_file_id(self):
        for file_id in (None, 0):
            with self.subTest(file_id=file_id):
                result = select_successor(file_id, [primary(50)])
                self.assertIs(Verdict.REVIEW, result.verdict)
                self.assertTrue(result.reason.startswith("No installed file ID is recorded for this mod."))

    def test_unlisted_without_primary_is_unresolved(self):
        result = select_successor(7, [mod_file(1)])
        self.assertIs(Verdict.UNRESOLVED, result.verdict)
        self.assertIsNone(result.target)
        self.assertEqual("Installed file 7 is not listed on Nexus.", result.reason)
        self.assertEqual((), result.newer_files)

    def test_unlisted_with_empty_listing(self):
        self.assertIs(Verdict.UNRESOLVED, select_successor(None, []).verdict)


class FallbackTest(unittest.TestCase):
    def test_no_group_suggests_newer_primary(self):
        installed = mod_file(1, group_id=None)
        main = primary(50)
        result = select_successor(1, [installed, main])
        self.assertIs(Verdict.REVIEW, result.verdict)
        self.assertIs(installed, result.installed)
        self.assertIs(main, result.target)
        self.assertEqual(
            "Installed file has no file group. Suggesting the mod's primary main file.", result.reason
        )

    def test_primary_is_the_installed_file(self):
        installed = primary(1, group_id=None)
        result = select_successor(1, [installed, mod_file(2, group_id=None)])
        self.assertIs(Verdict.UP_TO_DATE, result.verdict)
        self.assertIsNone(result.target)
        self.assertEqual("Installed file has no file group.", result.reason)
        self.assertEqual((), result.newer_files)

    def test_primary_older_than_installed_is_unresolved(self):
        installed = mod_file(1, group_id=None, uploaded_at=T0 + 100)
        result = select_successor(1, [installed, primary(50, uploaded_at=T0)])
        self.assertIs(Verdict.UNRESOLVED, result.verdict)
        self.assertIsNone(result.target)

    def test_primary_with_unknown_upload_time_is_suggested(self):
        main = primary(50, unknown_time=True)
        result = select_successor(1, [mod_file(1, group_id=None), main])
        self.assertIs(Verdict.REVIEW, result.verdict)
        self.assertIs(main, result.target)

    def test_zero_or_several_primaries_is_unresolved(self):
        installed = mod_file(1, group_id=None)
        cases = {
            "none": [installed],
            "several": [installed, primary(50), primary(51)],
            "primary but not main": [installed, primary(50, category=C.OPTIONAL)],
            "main but not primary": [installed, mod_file(50, group_id=99)],
        }
        for label, files in cases.items():
            with self.subTest(label):
                result = select_successor(1, files)
                self.assertIs(Verdict.UNRESOLVED, result.verdict)
                self.assertIsNone(result.target)
                self.assertEqual("Installed file has no file group.", result.reason)

    def test_retired_file_with_empty_lineage_falls_back(self):
        for category in (C.OLD_VERSION, C.ARCHIVED, C.REMOVED):
            with self.subTest(category=category):
                installed = mod_file(1, category=category)
                main = primary(50)
                result = select_successor(1, [installed, main, mod_file(0, uploaded_at=T0 - 5)])
                self.assertIs(Verdict.REVIEW, result.verdict)
                self.assertIs(main, result.target)
                self.assertEqual(
                    f"Installed file is {category.value} and file group 10 has no newer active "
                    "file. Suggesting the mod's primary main file.",
                    result.reason,
                )
                self.assertEqual((main,), result.newer_files)

    def test_retired_file_without_primary_is_unresolved(self):
        result = select_successor(1, [mod_file(1, category=C.ARCHIVED)])
        self.assertIs(Verdict.UNRESOLVED, result.verdict)
        self.assertIn("archived", result.reason)


class GroupLineageTest(unittest.TestCase):
    def test_newest_in_group_across_renames_is_ready(self):
        installed = mod_file(1, name="Old Name", version="1.0")
        middle = mod_file(2, name="Renamed", version="1.1", category=C.UPDATE)
        newest = mod_file(3, name="Renamed Again", version="1.2", category=C.OPTIONAL)
        other_group = mod_file(9, group_id=20, uploaded_at=T0 + 1000)
        older = mod_file(0, uploaded_at=T0 - 100)
        result = select_successor(1, [older, installed, newest, other_group, middle])
        self.assertIs(Verdict.READY, result.verdict)
        self.assertIs(newest, result.target)
        self.assertIs(installed, result.installed)
        self.assertEqual("Newest active file in file group 10.", result.reason)
        self.assertEqual((newest, middle), result.newer_files)

    def test_tie_on_upload_time_breaks_by_file_id(self):
        a = mod_file(5, uploaded_at=T0 + 50)
        b = mod_file(6, uploaded_at=T0 + 50)
        result = select_successor(1, [mod_file(1), b, a])
        self.assertIs(b, result.target)
        self.assertEqual((b, a), result.newer_files)

    def test_inactive_newer_files_are_never_targets(self):
        installed = mod_file(1)
        retired = [mod_file(2, category=C.OLD_VERSION), mod_file(3, category=C.ARCHIVED),
                   mod_file(4, category=C.REMOVED)]
        active = mod_file(5, category=C.MISCELLANEOUS, uploaded_at=T0 + 3)
        result = select_successor(1, [installed, active] + retired)
        self.assertIs(Verdict.READY, result.verdict)
        self.assertIs(active, result.target)
        self.assertEqual((retired[2], active, retired[1], retired[0]), result.newer_files)

    def test_only_inactive_newer_files_means_up_to_date(self):
        result = select_successor(1, [mod_file(1), mod_file(2, category=C.REMOVED)])
        self.assertIs(Verdict.UP_TO_DATE, result.verdict)
        self.assertIsNone(result.target)
        self.assertEqual((), result.newer_files)

    def test_up_to_date(self):
        installed = mod_file(1)
        result = select_successor(1, [mod_file(0, uploaded_at=T0 - 1), installed])
        self.assertIs(Verdict.UP_TO_DATE, result.verdict)
        self.assertIs(installed, result.installed)
        self.assertIsNone(result.target)
        self.assertEqual("Installed file is still the newest in its file group.", result.reason)
        self.assertEqual((), result.newer_files)

    def test_same_upload_time_is_not_newer(self):
        result = select_successor(1, [mod_file(1, uploaded_at=T0), mod_file(2, uploaded_at=T0)])
        self.assertIs(Verdict.UP_TO_DATE, result.verdict)


class IncompleteDataTest(unittest.TestCase):
    def assertIncomplete(self, result, verdict):
        self.assertIs(verdict, result.verdict)
        self.assertTrue(result.reason.endswith(_INCOMPLETE_NOTE), result.reason)

    def test_installed_with_unknown_category_downgrades_ready(self):
        newest = mod_file(2)
        result = select_successor(1, [mod_file(1, category=C.UNKNOWN), newest])
        self.assertIncomplete(result, Verdict.REVIEW)
        self.assertIs(newest, result.target)
        self.assertEqual("Newest active file in file group 10." + _INCOMPLETE_NOTE, result.reason)

    def test_installed_without_upload_time_treats_every_group_file_as_newer(self):
        older = mod_file(0, uploaded_at=T0 - 100)
        newer = mod_file(2)
        result = select_successor(1, [mod_file(1, unknown_time=True), older, newer])
        self.assertIncomplete(result, Verdict.REVIEW)
        self.assertIs(newer, result.target)
        self.assertEqual((newer, older), result.newer_files)

    def test_unknown_newer_file_downgrades_ready(self):
        known = mod_file(2)
        unknown = mod_file(3, category=C.UNKNOWN)
        result = select_successor(1, [mod_file(1), known, unknown])
        self.assertIncomplete(result, Verdict.REVIEW)
        self.assertIs(known, result.target)  # UNKNOWN isn't active.
        self.assertEqual((unknown, known), result.newer_files)

    def test_newer_file_without_upload_time_downgrades_ready(self):
        known = mod_file(2)
        undated = mod_file(3, unknown_time=True)
        result = select_successor(1, [mod_file(1), known, undated])
        self.assertIncomplete(result, Verdict.REVIEW)
        self.assertIs(known, result.target)
        # Undated files sort as oldest.
        self.assertEqual((known, undated), result.newer_files)

    def test_nothing_newer_but_unknown_category_is_unresolved(self):
        unknown = mod_file(2, category=C.UNKNOWN)
        result = select_successor(1, [mod_file(1), unknown])
        self.assertIncomplete(result, Verdict.UNRESOLVED)
        self.assertIsNone(result.target)
        self.assertEqual(
            "Cannot tell whether file group 10 has a newer file." + _INCOMPLETE_NOTE, result.reason
        )
        self.assertEqual((unknown,), result.newer_files)

    def test_nothing_newer_but_undated_file_is_unresolved(self):
        undated = mod_file(2, unknown_time=True)
        result = select_successor(1, [mod_file(1), undated])
        self.assertIncomplete(result, Verdict.UNRESOLVED)
        self.assertEqual((undated,), result.newer_files)

    def test_incomplete_installed_with_nothing_newer_is_unresolved(self):
        result = select_successor(1, [mod_file(1, category=C.UNKNOWN), mod_file(0, uploaded_at=T0 - 9)])
        self.assertIncomplete(result, Verdict.UNRESOLVED)


class PlanUpdatesTest(unittest.TestCase):
    def test_one_selection_per_installed_file(self):
        files = [mod_file(1, group_id=1), mod_file(2, group_id=1), mod_file(3, group_id=2)]
        selections = plan_updates([1, 3], iter(files))
        self.assertEqual([1, 3], [s.installed_file_id for s in selections])
        self.assertEqual([Verdict.READY, Verdict.UP_TO_DATE], [s.verdict for s in selections])

    def test_no_installed_ids_gives_one_selection_for_none(self):
        for ids in ((), []):
            with self.subTest(ids=ids):
                selections = plan_updates(ids, [primary(5)])
                self.assertEqual(1, len(selections))
                self.assertIsNone(selections[0].installed_file_id)
                self.assertIs(Verdict.REVIEW, selections[0].verdict)

    def test_never_proposes_the_same_target_twice(self):
        files = [mod_file(1), mod_file(2), mod_file(3)]
        selections = plan_updates([1, 2], files)
        self.assertEqual([1], [s.installed_file_id for s in selections])
        self.assertEqual(3, selections[0].target.file_id)

    def test_selections_without_target_are_all_kept(self):
        selections = plan_updates([7, 8], [mod_file(1)])
        self.assertEqual([7, 8], [s.installed_file_id for s in selections])
        self.assertTrue(all(s.verdict is Verdict.UNRESOLVED for s in selections))


if __name__ == "__main__":
    unittest.main()
