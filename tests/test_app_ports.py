import unittest

from nexus_auto_updater.app import ports
from tests.fakes import GAME, FakeMatcher


class PortsTest(unittest.TestCase):
    def test_file_listing_archive_matcher(self):
        matcher = FakeMatcher("a.7z")
        listing = ports.FileListing((), {5: matcher})
        self.assertIs(matcher, listing.archive_matcher(5))
        self.assertIsNone(listing.archive_matcher(6))
        self.assertIsNone(ports.FileListing(()).archive_matcher(5))

    def test_error_hierarchy(self):
        self.assertTrue(issubclass(ports.SourceUnavailable, ports.SourceError))
        self.assertTrue(issubclass(ports.SourceIncompatible, ports.SourceError))

    def test_installed_mod_has_no_blocker_by_default(self):
        self.assertEqual("", ports.InstalledMod("m", 1, (2,), "1", "2").download_blocker)

    def test_protocol_bodies_are_inert(self):
        source = ports.ModFileSource
        self.assertIsNone(source.list_files(None, GAME, 1))
        self.assertIsNone(source.mod_page_url(None, GAME, 1))
        self.assertIsNone(source.mod_description(None, GAME, 1))
        self.assertIsNone(ports.ArchiveMatcher.matches(None, "x"))
        self.assertIsNone(ports.Downloader.start_nexus_download(None, 1, 2))
        self.assertIsNone(ports.Installer.install_archive(None, "a", "b"))
        self.assertIsNone(ports.ModLocks.is_locked(None, 1))
        self.assertIsNone(ports.ModLocks.set_locked(None, 1, True))


if __name__ == "__main__":
    unittest.main()
