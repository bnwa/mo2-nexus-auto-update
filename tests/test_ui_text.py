import re
import unittest
from dataclasses import replace

from nexus_auto_updater.app.scan import ScanFailed, StopReason
from nexus_auto_updater.app.session import (
    DownloadFailed,
    Downloaded,
    Downloading,
    Idle,
    InstallCancelled,
    Installed,
    InstalledFile,
    Offered,
    Queued,
    ScanError,
    Skipped,
    Unmatched,
    Unresolved,
    UpdateEntry,
    UpToDate,
)
from nexus_auto_updater.domain.files import FileCategory, ModFile
from nexus_auto_updater.ui import text

COLOR = re.compile(r"^#[0-9a-f]{6}$")
INSTALLED = InstalledFile(1, None)
READY = Offered(INSTALLED, ModFile(2), False, "why")
FINDINGS = {
    "Ready": READY,
    "Needs review": Offered(INSTALLED, ModFile(2), True, "why"),
    "Up to date": UpToDate(INSTALLED, "why"),
    "Unresolved": Unresolved(INSTALLED, "why"),
    "Skipped": Skipped("why"),
    "Error": ScanError("why"),
}
STARTED = {
    "Queued": Queued(),
    "Downloading": Downloading(),
    "Downloaded": Downloaded("a.7z"),
    "Download failed": DownloadFailed(),
    "Not matched": Unmatched(),
    "Installed": Installed("a.7z"),
    "Install cancelled": InstallCancelled("a.7z"),
}
ENTRY = UpdateEntry(1, "SkyUI", 12604, "5.1", "5.2SE", READY)


class TablesTest(unittest.TestCase):
    def test_lock_strings(self):
        self.assertEqual("\U0001F512", text.LOCK_HEADER)
        self.assertIn("Lock", text.LOCK_TOOLTIP)
        self.assertRegex(text.LOCKED_COLOR, COLOR)


class EntryTextTest(unittest.TestCase):
    def test_idle_shows_the_finding(self):
        for label, finding in FINDINGS.items():
            with self.subTest(label):
                entry = replace(ENTRY, finding=finding)
                self.assertEqual(label, text.status_text(entry))
                self.assertRegex(text.status_color(entry), COLOR)
                self.assertEqual(label, text.finding_label(finding))
                self.assertEqual("why", text.finding_detail(finding))

    def test_scan_summary_labels_cover_every_finding(self):
        self.assertEqual(set(FINDINGS), set(text.FINDING_LABELS))

    def test_started_update_overrides_the_finding(self):
        for label, progress in STARTED.items():
            with self.subTest(label):
                entry = replace(ENTRY, progress=progress)
                self.assertEqual(label, text.status_text(entry))
                self.assertRegex(text.status_color(entry), COLOR)
        self.assertEqual("#e74c3c", text.status_color(replace(ENTRY, progress=DownloadFailed())))

    def test_archive_is_known_once_downloaded(self):
        self.assertIsNone(text.archive_of(Idle()))
        self.assertIsNone(text.archive_of(Queued()))
        self.assertIsNone(text.archive_of(Downloading()))
        self.assertIsNone(text.archive_of(DownloadFailed()))
        for progress in (Downloaded("a.7z"), Installed("a.7z"), InstallCancelled("a.7z")):
            self.assertEqual("a.7z", text.archive_of(progress))

    def test_error_detail_is_its_message(self):
        self.assertEqual("HTTP 404", text.finding_detail(ScanError("HTTP 404")))

    def test_mod_tooltip(self):
        self.assertEqual("Nexus mod 12604\nMO2 version: 5.1\nMO2 newest version: 5.2SE", text.mod_tooltip(ENTRY))
        blank = replace(ENTRY, version="", newest_version="")
        self.assertEqual("Nexus mod 12604\nMO2 version: ?\nMO2 newest version: ?", text.mod_tooltip(blank))


class FileTextTest(unittest.TestCase):
    def test_file_label(self):
        full = ModFile(5, name="SkyUI", version="5.2SE", category=FileCategory.MAIN)
        self.assertEqual("SkyUI 5.2SE [MAIN]", text.file_label(full))
        self.assertEqual("File ID 5 [?]", text.file_label(ModFile(5)))
        self.assertEqual("File ID 5 1.0 [OLD_VERSION]",
                         text.file_label(ModFile(5, version="1.0", category=FileCategory.OLD_VERSION)))
        self.assertEqual("SkyUI 5.2SE [MAIN]", text.file_label(full, 99))

    def test_file_label_without_file(self):
        self.assertEqual("File ID 99", text.file_label(None, 99))
        self.assertEqual("—", text.file_label(None))
        self.assertEqual("—", text.file_label(None, 0))

    def test_file_tooltip(self):
        self.assertEqual("", text.file_tooltip(None))
        self.assertEqual("File ID 5\nGroup 7", text.file_tooltip(ModFile(5, group_id=7)))
        self.assertEqual("File ID 5\nGroup 0", text.file_tooltip(ModFile(5, group_id=0)))
        self.assertEqual("File ID 5\nGroup unknown", text.file_tooltip(ModFile(5)))


class ScanFailedMessageTest(unittest.TestCase):
    def test_incompatible(self):
        message = text.scan_failed_message(ScanFailed(StopReason.INCOMPATIBLE, "Field 'foo'", 3))
        self.assertTrue(message.startswith("Scan stopped: the Nexus v2 API (beta) no longer answers"))
        self.assertIn("needs an update. 3 mod(s) were not checked.\nField 'foo'", message)

    def test_unavailable(self):
        message = text.scan_failed_message(ScanFailed(StopReason.UNAVAILABLE, "HTTP 503", 2))
        self.assertEqual(
            "Scan stopped: Nexus is not responding. Try again later. 2 mod(s) were not checked.\nHTTP 503",
            message,
        )

    def test_nothing_unchecked(self):
        message = text.scan_failed_message(ScanFailed(StopReason.UNAVAILABLE, "down", 0))
        self.assertEqual("Scan stopped: Nexus is not responding. Try again later.\ndown", message)
        self.assertNotIn("not checked", text.scan_failed_message(ScanFailed(StopReason.INCOMPATIBLE, "x", 0)))


if __name__ == "__main__":
    unittest.main()
