import unittest

from nexus_auto_updater.domain.files import ACTIVE_CATEGORIES, FileCategory, ModFile


class ModFileTest(unittest.TestCase):
    def test_defaults_are_unknown(self):
        mod_file = ModFile(5)
        self.assertEqual("", mod_file.name)
        self.assertIs(FileCategory.UNKNOWN, mod_file.category)
        self.assertIsNone(mod_file.group_id)
        self.assertIsNone(mod_file.uploaded_at)
        self.assertEqual((), mod_file.changelog)
        self.assertFalse(mod_file.is_primary)

    def test_is_active_for_every_category(self):
        active = {FileCategory.MAIN, FileCategory.UPDATE, FileCategory.OPTIONAL, FileCategory.MISCELLANEOUS}
        self.assertEqual(active, set(ACTIVE_CATEGORIES))
        for category in FileCategory:
            with self.subTest(category=category):
                self.assertEqual(category in active, ModFile(1, category=category).is_active)

    def test_is_fully_known(self):
        self.assertTrue(ModFile(1, category=FileCategory.MAIN, uploaded_at=1).is_fully_known)
        self.assertTrue(ModFile(1, category=FileCategory.REMOVED, uploaded_at=0).is_fully_known)
        self.assertFalse(ModFile(1, category=FileCategory.UNKNOWN, uploaded_at=1).is_fully_known)
        self.assertFalse(ModFile(1, category=FileCategory.MAIN).is_fully_known)

    def test_is_frozen(self):
        with self.assertRaises(AttributeError):
            ModFile(1).name = "x"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
