import re
import unittest
from dataclasses import replace

from nexus_auto_updater.app.scan import ScanFailed, StopReason
from nexus_auto_updater.app.session import EntryStatus, Phase, UpdateEntry
from nexus_auto_updater.domain.files import FileCategory, ModFile
from nexus_auto_updater.ui import text

COLOR = re.compile(r"^#[0-9a-f]{6}$")
ENTRY = UpdateEntry(1, "SkyUI", 12604, "5.1", "5.2SE", 1, None, None, EntryStatus.READY, "why")


class TablesTest(unittest.TestCase):
    def test_every_status_has_text_and_colour(self):
        for status in EntryStatus:
            with self.subTest(status=status):
                self.assertTrue(text.STATUS_TEXT[status])
                self.assertRegex(text.STATUS_COLORS[status], COLOR)

    def test_every_non_idle_phase_has_text_and_colour(self):
        for phase in Phase:
            with self.subTest(phase=phase):
                if phase is Phase.IDLE:
                    self.assertNotIn(phase, text.PHASE_TEXT)
                    self.assertNotIn(phase, text.PHASE_COLORS)
                else:
                    self.assertTrue(text.PHASE_TEXT[phase])
                    self.assertRegex(text.PHASE_COLORS[phase], COLOR)

    def test_lock_strings(self):
        self.assertEqual("\U0001F512", text.LOCK_HEADER)
        self.assertIn("Lock", text.LOCK_TOOLTIP)
        self.assertRegex(text.LOCKED_COLOR, COLOR)


class EntryTextTest(unittest.TestCase):
    def test_idle_shows_the_status(self):
        for status in EntryStatus:
            entry = replace(ENTRY, status=status)
            self.assertEqual(text.STATUS_TEXT[status], text.status_text(entry))
            self.assertEqual(text.STATUS_COLORS[status], text.status_color(entry))

    def test_phase_overrides_the_status(self):
        entry = replace(ENTRY, phase=Phase.DOWNLOAD_FAILED)
        self.assertEqual("Download failed", text.status_text(entry))
        self.assertEqual("#e74c3c", text.status_color(entry))
        self.assertEqual("Queued", text.status_text(replace(ENTRY, phase=Phase.QUEUED)))

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
