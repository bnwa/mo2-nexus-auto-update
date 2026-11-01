import tempfile
import unittest
from pathlib import Path

from nexus_auto_updater.host.metadata import read_archive_ids, read_general_keys, read_installed_files


class MetadataTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def write(self, name: str, text: str, *, bom: bool = False, newline: str = "\r\n") -> Path:
        path = self.dir / name
        data = text.replace("\n", newline).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write((b"\xef\xbb\xbf" if bom else b"") + data)
        return path


META_INI = """[General]
gameName=SkyrimSE
modid=12604
version=5.2.0.0
newestVersion=5.2SE
installationFile=SkyUI_5_2_SE-12604-5-2SE.7z

[installedFiles]
2\\modid=12604
2\\fileid=1000172398
1\\modid=12604
1\\fileid=1000172397
3\\modid=0
3\\fileid=55
4\\modid=12604
4\\fileid=0
5\\modid=12604
6\\fileid=77
size=6

[Plugins]
1\\modid=1
1\\fileid=1
"""


class InstalledFilesTest(MetadataTestCase):
    def test_reads_entries_in_index_order(self):
        path = self.write("meta.ini", META_INI)
        self.assertEqual([(12604, 1000172397), (12604, 1000172398)], read_installed_files(path))

    def test_case_insensitive_section_and_keys_with_bom_and_lf(self):
        text = "[INSTALLEDFILES]\n 1\\ModID = 7 \n1\\FileId=8\n"
        for bom in (False, True):
            for newline in ("\n", "\r\n"):
                with self.subTest(bom=bom, newline=newline):
                    path = self.write("meta.ini", text, bom=bom, newline=newline)
                    self.assertEqual([(7, 8)], read_installed_files(path))

    def test_bom_before_the_section_header(self):
        path = self.write("meta.ini", "[installedFiles]\n1\\modid=3\n1\\fileid=4\n", bom=True)
        self.assertEqual([(3, 4)], read_installed_files(path))

    def test_missing_file_and_no_section(self):
        self.assertEqual([], read_installed_files(self.dir / "absent" / "meta.ini"))
        self.assertEqual([], read_installed_files(self.write("meta.ini", "[General]\nmodid=1\n")))

    def test_a_directory_reads_as_missing(self):
        self.assertEqual([], read_installed_files(self.dir))

    def test_non_numeric_entries_are_ignored(self):
        path = self.write("meta.ini", "[installedFiles]\n1\\modid=abc\n1\\fileid=4\n2\\modid=5\n2\\fileid=6\n")
        self.assertEqual([(5, 6)], read_installed_files(path))


class GeneralKeysTest(MetadataTestCase):
    def test_reads_requested_keys_with_the_callers_spelling(self):
        path = self.write("meta.ini", META_INI)
        self.assertEqual(
            {"version": "5.2.0.0", "newestVersion": "5.2SE"},
            read_general_keys(path, ("version", "newestVersion", "absent")),
        )
        self.assertEqual({"NEWESTVERSION": "5.2SE"}, read_general_keys(path, ("NEWESTVERSION",)))

    def test_keys_before_any_header_count_as_general(self):
        path = self.write("x.meta", 'modID=12604\nfileID="1000172397"\n[installed]\nversion=9\n')
        self.assertEqual({"modID": "12604", "fileID": "1000172397"}, read_general_keys(path, ("modID", "fileID", "version")))

    def test_values_are_stripped_of_quotes_and_spaces(self):
        path = self.write("meta.ini", '[General]\nversion = "1.0 beta" \ncomments="said \\"hi\\""\nempty=\n')
        self.assertEqual(
            {"version": "1.0 beta", "empty": ""},
            read_general_keys(path, ("version", "empty")),
        )

    def test_returns_to_general_section(self):
        path = self.write("meta.ini", "[Other]\nversion=bad\n[general]\nversion=good\n")
        self.assertEqual({"version": "good"}, read_general_keys(path, ("version",)))

    def test_missing_file(self):
        self.assertEqual({}, read_general_keys(self.dir / "nope.ini", ("version",)))

    def test_utf8_bom_and_non_ascii(self):
        path = self.write("meta.ini", "[General]\nversion=1.0\nnewestVersion=1.1-ß\n", bom=True)
        self.assertEqual({"version": "1.0", "newestVersion": "1.1-ß"}, read_general_keys(path, ("version", "newestVersion")))


class ArchiveIdsTest(MetadataTestCase):
    def test_reads_archive_meta(self):
        archive = str(self.dir / "SkyUI_5_2_SE-12604-5-2SE.7z")

        self.write(
            "SkyUI_5_2_SE-12604-5-2SE.7z.meta",
            "[General]\ngameName=skyrimspecialedition\nmodID=12604\nfileID=1000172397\n"
            "url=\"https://www.nexusmods.com/skyrimspecialedition/mods/12604\"\ninstalled=false\n",
        )
        self.assertEqual((12604, 1000172397), read_archive_ids(archive))

    def test_non_integer_values_read_as_zero(self):
        archive = str(self.dir / "Foo-123-1-0.7z")
        self.write("Foo-123-1-0.7z.meta", "[General]\nmodID=abc\nfileID=\n")
        self.assertEqual((0, 0), read_archive_ids(archive))

    def test_missing_meta(self):
        self.assertEqual((0, 0), read_archive_ids(str(self.dir / "Foo-123-1-0.7z")))
        self.assertEqual((0, 0), read_archive_ids(r"C:\Definitely\Not\There\Foo-123-1-0.7z"))

    def test_partial_meta(self):
        archive = str(self.dir / "Foo-123-1-0.7z")
        self.write("Foo-123-1-0.7z.meta", "[General]\nmodID=123\n")
        self.assertEqual((123, 0), read_archive_ids(archive))


if __name__ == "__main__":
    unittest.main()
