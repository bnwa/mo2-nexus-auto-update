import unittest

from nexus_auto_updater.nexus.archives import _ARCHIVE_EXTENSIONS, ArchiveFingerprint

LEGACY = "SkyUI_5_2_SE-12604-5-2SE.7z"
SQID = "Q2aN3bT"
NEW_STYLE = "SkyUI 12604 5.2SE 2024-03-01T12-30Z " + SQID


class FromApiTest(unittest.TestCase):
    def test_plain_uri_is_a_legacy_name(self):
        self.assertEqual(ArchiveFingerprint(LEGACY, SQID), ArchiveFingerprint.from_api(LEGACY, SQID))

    def test_storage_path_uri_is_not_a_legacy_name(self):
        fingerprint = ArchiveFingerprint.from_api("https://cf-files.nexusmods.com/cdn/1704/12604/x.7z", SQID)
        self.assertEqual(ArchiveFingerprint("", SQID), fingerprint)
        self.assertEqual("", ArchiveFingerprint.from_api("", "").legacy_file_name)


class MatchesTest(unittest.TestCase):
    def test_legacy_match_is_case_insensitive_and_stripped(self):
        fingerprint = ArchiveFingerprint(LEGACY)
        self.assertTrue(fingerprint.matches(LEGACY))
        self.assertTrue(fingerprint.matches("  skyui_5_2_se-12604-5-2se.7Z \t"))
        self.assertFalse(fingerprint.matches("SkyUI_5_2_SE-12604-5-2SE-1.7z"))

    def test_sqid_match_for_every_extension(self):
        fingerprint = ArchiveFingerprint(sqid=SQID)
        self.assertIn(".tar.gz", _ARCHIVE_EXTENSIONS)
        for extension in _ARCHIVE_EXTENSIONS + (".7Z", ".ZIP", ".Tar.Gz"):
            with self.subTest(extension=extension):
                self.assertTrue(fingerprint.matches(NEW_STYLE + extension))

    def test_tar_gz_is_stripped_whole(self):
        self.assertTrue(ArchiveFingerprint(sqid="abc").matches("Mod 1 1.0 2024-01-01T00-00Z abc.tar.gz"))
        self.assertFalse(ArchiveFingerprint(sqid="abc.tar").matches("Mod 1 1.0 2024-01-01T00-00Z abc.tar.gz"))

    def test_duplicate_download_suffix_is_ignored(self):
        fingerprint = ArchiveFingerprint(sqid=SQID)
        self.assertTrue(fingerprint.matches(NEW_STYLE + "(1).7z"))
        self.assertTrue(fingerprint.matches(NEW_STYLE + " (12).zip"))
        self.assertFalse(fingerprint.matches(NEW_STYLE + " (x).zip"))

    def test_sqid_is_case_sensitive(self):
        fingerprint = ArchiveFingerprint(sqid=SQID)
        self.assertFalse(fingerprint.matches(NEW_STYLE.replace(SQID, SQID.lower()) + ".7z"))
        self.assertFalse(fingerprint.matches(NEW_STYLE + "X.7z"))

    def test_name_without_extension(self):
        self.assertTrue(ArchiveFingerprint(sqid=SQID).matches(NEW_STYLE))
        self.assertTrue(ArchiveFingerprint(sqid=SQID).matches(SQID))

    def test_neither_identifier_never_matches(self):
        self.assertFalse(ArchiveFingerprint().matches(NEW_STYLE + ".7z"))
        self.assertFalse(ArchiveFingerprint().matches(""))

    def test_legacy_miss_falls_back_to_sqid(self):
        fingerprint = ArchiveFingerprint(LEGACY, SQID)
        self.assertTrue(fingerprint.matches(NEW_STYLE + ".7z"))
        self.assertFalse(ArchiveFingerprint(LEGACY).matches(NEW_STYLE + ".7z"))


if __name__ == "__main__":
    unittest.main()
