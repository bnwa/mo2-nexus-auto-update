import tempfile
import unittest
from pathlib import Path

from nexus_auto_updater.app.ports import GameRef
from nexus_auto_updater.host.mo2 import _LOCKS_KEY, Mo2Host
from tests.fake_mobase import (
    FakeDownloadManager,
    FakeGame,
    FakeMod,
    FakeOrganizer,
    FakeVersion as V,
)

PLUGIN = "Nexus Auto Updater"


class Mo2TestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mods_dir = self.root / "mods"
        self.downloads = self.root / "downloads"
        self.mods_dir.mkdir()
        self.downloads.mkdir()

    def mod(self, name="Some Mod", nexus_id=100, meta=None, **kwargs) -> FakeMod:
        path = self.mods_dir / name
        path.mkdir(exist_ok=True)
        if meta is not None:
            (path / "meta.ini").write_bytes(meta.replace("\n", "\r\n").encode("utf-8"))
        return FakeMod(name, nexus_id, str(path), **kwargs)

    def host(self, *mods, **kwargs) -> Mo2Host:
        mod_map = {}
        for index, mod in enumerate(mods):
            mod_map[mod.name() if mod is not None else f"<none {index}>"] = mod
        self.organizer = FakeOrganizer(mod_map, downloads_path=str(self.downloads), **kwargs)
        return Mo2Host(self.organizer, PLUGIN)


def installed_files(*pairs):
    lines = ["[installedFiles]"]
    for index, (mod_id, file_id) in enumerate(pairs, 1):
        lines += [f"{index}\\modid={mod_id}", f"{index}\\fileid={file_id}"]
    lines.append(f"size={len(pairs)}")
    return "\n".join(lines) + "\n"


class GameTest(Mo2TestCase):
    def test_managed_game(self):
        host = self.host(game=FakeGame(nexus_id=1704, nexus_name="skyrimspecialedition"))
        self.assertEqual(GameRef(1704, "skyrimspecialedition"), host.managed_game())
        host = self.host(game=FakeGame(nexus_id=0, nexus_name="newgame"))
        self.assertEqual(GameRef(0, "newgame"), host.managed_game())


class InventoryTest(Mo2TestCase):
    def test_filters_non_nexus_mods_and_keeps_priority_order(self):
        keep = [
            self.mod("Zeta", 3, repository="Nexus"),
            self.mod("Alpha", 1, repository=""),
            self.mod("Mid", 2, repository="NEXUS"),
        ]
        drop = [
            None,
            self.mod("Separator_separator", 4, separator=True),
            self.mod("DLC Dawnguard", 5, foreign=True),
            self.mod("Backup", 6, backup=True),
            self.mod("Overwrite", 7, overwrite=True),
            self.mod("Manual", 0),
            self.mod("Negative", -1),
            self.mod("LoversLab", 8, repository="LoversLab"),
        ]
        host = self.host(drop[0], keep[0], *drop[1:4], keep[1], *drop[4:], keep[2])
        mods = host.nexus_mods(only_flagged=False)
        self.assertEqual(["Zeta", "Alpha", "Mid"], [m.name for m in mods])
        self.assertEqual([3, 1, 2], [m.mod_id for m in mods])

    def test_only_flagged(self):
        cases = {
            "no newest version": (dict(newest=V()), False),
            "ignored equals newest": (dict(newest=V(2, 0), ignored=V(2, 0)), False),
            "ignored differs": (dict(newest=V(2, 0), ignored=V(1, 5)), True),
            "current invalid": (dict(version=V(), newest=V(2, 0)), True),
            "current older": (dict(version=V(1, 0), newest=V(2, 0)), True),
            "current equal": (dict(version=V(2, 0), newest=V(2, 0)), False),
            "current newer": (dict(version=V(3, 0), newest=V(2, 0)), False),
        }
        for label, (kwargs, flagged) in cases.items():
            with self.subTest(label):
                host = self.host(self.mod("M", 1, **kwargs))
                self.assertEqual(flagged, len(host.nexus_mods(only_flagged=True)) == 1)
                self.assertEqual(1, len(host.nexus_mods(only_flagged=False)))

    def test_versions_from_meta_ini(self):
        meta = '[General]\nversion=1.0.0.0\nnewestVersion="2.1"\n' + installed_files((100, 5))
        (mod,) = self.host(self.mod("M", 100, meta=meta)).nexus_mods(False)
        self.assertEqual(("1.0.0.0", "2.1"), (mod.version, mod.newest_version))
        self.assertEqual((5,), mod.installed_file_ids)
        self.assertEqual("", mod.download_blocker)

    def test_missing_meta_ini(self):
        (mod,) = self.host(self.mod("M", 100)).nexus_mods(False)
        self.assertEqual(("", "", ()), (mod.version, mod.newest_version, mod.installed_file_ids))

    def test_other_game_gets_a_blocker(self):
        game = FakeGame(nexus_name="skyrimspecialedition", short_name="SkyrimSE")
        mods = [
            self.mod("Own Nexus", 1, game_name="SkyrimSpecialEdition"),
            self.mod("Own Short", 2, game_name="skyrimse"),
            self.mod("LE", 3, game_name="Skyrim"),
        ]
        result = self.host(*mods, game=game).nexus_mods(False)
        self.assertEqual(["", ""], [m.download_blocker for m in result[:2]])
        self.assertEqual(
            "Mod belongs to Nexus game 'Skyrim'; MO2's plugin API can only start downloads for "
            "the managed game.",
            result[2].download_blocker,
        )

    def test_installed_ids_filtered_to_the_mods_own_id(self):
        meta = installed_files((100, 5), (999, 6), (100, 7))
        (mod,) = self.host(self.mod("M", 100, meta=meta, installation_file="ignored.7z")).nexus_mods(False)
        self.assertEqual((5, 7), mod.installed_file_ids)


class InstallationArchiveFallbackTest(Mo2TestCase):
    def write_archive_meta(self, directory: Path, name: str, mod_id: int, file_id: int) -> Path:
        archive = directory / name
        (directory / (name + ".meta")).write_text(
            f"[General]\nmodID={mod_id}\nfileID={file_id}\n", encoding="utf-8"
        )
        return archive

    def ids(self, installation_file, meta=None, mod_id=100):
        (mod,) = self.host(self.mod("M", mod_id, meta=meta, installation_file=installation_file)).nexus_mods(False)
        return mod.installed_file_ids

    def test_relative_installation_file_is_under_downloads(self):
        self.write_archive_meta(self.downloads, "Foo-100-1-0.7z", 100, 55)
        self.assertEqual((55,), self.ids("Foo-100-1-0.7z"))

    def test_absolute_installation_file_is_kept(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        archive = self.write_archive_meta(elsewhere, "Foo-100-1-0.7z", 100, 56)
        self.assertTrue(archive.is_absolute())
        self.assertEqual((56,), self.ids(str(archive)))

    def test_used_when_installed_files_has_only_foreign_entries(self):
        self.write_archive_meta(self.downloads, "Foo.7z", 0, 57)
        self.assertEqual((57,), self.ids("Foo.7z", meta=installed_files((999, 1))))

    def test_meta_mod_id_zero_or_equal_is_accepted(self):
        for meta_mod_id in (0, 100):
            with self.subTest(meta_mod_id=meta_mod_id):
                self.write_archive_meta(self.downloads, "Foo.7z", meta_mod_id, 58)
                self.assertEqual((58,), self.ids("Foo.7z"))

    def test_mismatched_mod_id_or_zero_file_id_is_rejected(self):
        self.write_archive_meta(self.downloads, "Other.7z", 555, 59)
        self.assertEqual((), self.ids("Other.7z"))
        self.write_archive_meta(self.downloads, "NoFile.7z", 100, 0)
        self.assertEqual((), self.ids("NoFile.7z"))

    def test_no_installation_file_or_no_meta(self):
        self.assertEqual((), self.ids(""))
        self.assertEqual((), self.ids("Missing-100-1-0.7z"))


class LocksTest(Mo2TestCase):
    def test_round_trip(self):
        host = self.host()
        self.assertFalse(host.is_locked(12604))
        host.set_locked(12604, True)
        host.set_locked(3863, True)
        self.assertEqual((PLUGIN, _LOCKS_KEY, "3863,12604", True), self.organizer.set_persistent_calls[-1])
        self.assertEqual("locked_mod_ids", _LOCKS_KEY)
        self.assertTrue(host.is_locked(12604))
        self.assertTrue(Mo2Host(self.organizer, PLUGIN).is_locked(3863))

    def test_unlocking_one_keeps_the_others(self):
        host = self.host()
        self.organizer.persistent_store[(PLUGIN, _LOCKS_KEY)] = "1,2,3"
        host.set_locked(2, False)
        self.assertEqual((PLUGIN, _LOCKS_KEY, "1,3", True), self.organizer.set_persistent_calls[-1])
        host.set_locked(42, False)
        self.assertEqual("1,3", self.organizer.set_persistent_calls[-1][2])
        host.set_locked(1, True)
        self.assertEqual("1,3", self.organizer.set_persistent_calls[-1][2])

    def test_malformed_values_read_as_nothing_locked(self):
        host = self.host()
        for raw in (None, "", ",", "abc", "1.5", "-3", [], 0):
            with self.subTest(raw=raw):
                self.organizer.persistent_store[(PLUGIN, _LOCKS_KEY)] = raw
                self.assertFalse(any(host.is_locked(i) for i in (0, 1, 3, 5)))

    def test_junk_entries_are_ignored(self):
        host = self.host()
        self.organizer.persistent_store[(PLUGIN, _LOCKS_KEY)] = " 7 ,x,,8;9, 10"
        self.assertTrue(host.is_locked(7))
        self.assertTrue(host.is_locked(10))
        self.assertFalse(host.is_locked(8))
        host.set_locked(11, True)
        self.assertEqual("7,10,11", self.organizer.set_persistent_calls[-1][2])

    def test_numeric_stored_value(self):
        host = self.host()
        self.organizer.persistent_store[(PLUGIN, _LOCKS_KEY)] = 12604
        self.assertTrue(host.is_locked(12604))


class DownloadsTest(Mo2TestCase):
    def test_start_nexus_download(self):
        manager = FakeDownloadManager()
        self.host(download_manager=manager).start_nexus_download(12604, 1000172397)
        self.assertEqual([(12604, 1000172397)], manager.started)

    def test_hooks_wait_for_the_download_manager(self):
        host = self.host()
        received = []
        host.on_download_finished(lambda *a: received.append(("first",) + a))
        manager = FakeDownloadManager()
        self.organizer.download_manager = manager
        host.on_download_finished(lambda *a: received.append(("second",) + a))
        host.on_download_finished(lambda *a: received.append(("third",) + a))
        self.assertEqual(1, len(manager.complete_hooks))
        self.assertEqual(1, len(manager.failed_hooks))
        manager.paths = {0: r"C:\Modding\MO2\downloads\Foo-1-1-0.7z", 1: r"C:\Modding\MO2\downloads\Bar.zip"}
        manager.complete_hooks[0](0)
        manager.failed_hooks[0](1)
        self.assertEqual(
            [("third", r"C:\Modding\MO2\downloads\Foo-1-1-0.7z", True),
             ("third", r"C:\Modding\MO2\downloads\Bar.zip", False)],
            received,
        )

    def test_empty_download_path_is_ignored(self):
        manager = FakeDownloadManager()
        host = self.host(download_manager=manager)
        received = []
        host.on_download_finished(lambda *a: received.append(a))
        manager.complete_hooks[0](5)
        self.assertEqual([], received)

    def test_no_listener_is_ignored(self):
        manager = FakeDownloadManager()
        manager.paths = {0: r"C:\x.7z"}
        host = self.host(download_manager=manager)
        host._finished(0, True)  # Nothing to notify; must not fail.


class InstallTest(Mo2TestCase):
    def test_install_archive(self):
        host = self.host()
        path = r"C:\Modding\MO2\downloads\SkyUI_5_2_SE-12604-5-2SE.7z"
        self.assertTrue(host.install_archive(path, "SkyUI"))
        self.assertEqual([(path, "SkyUI")], self.organizer.install_calls)
        self.organizer.install_result = None
        self.assertFalse(host.install_archive(path, "SkyUI"))


if __name__ == "__main__":
    unittest.main()
