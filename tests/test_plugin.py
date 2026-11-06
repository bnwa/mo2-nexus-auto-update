import os
import unittest
from unittest import mock

import mobase

from nexus_auto_updater.app.notes import ModNotes
from nexus_auto_updater.app.sync import Scanning
from tests import HAS_QT, REPO_ROOT, QtTestCase, wait_until
from tests.fake_mobase import FakeDownloadManager, FakeMod, FakeOrganizer, FakeVersion
from tests.fakes import FakeScanRunner

if HAS_QT:
    from PyQt6.QtGui import QIcon

    from nexus_auto_updater import plugin as plugin_module
    from nexus_auto_updater.host.mo2 import Mo2Host
    from nexus_auto_updater.tracking.tracker import DownloadTracker
    from nexus_auto_updater.ui import dialog as dialog_module
    from nexus_auto_updater.ui import scan_worker as scan_worker_module
    from nexus_auto_updater.ui.scan_worker import QtScanRunner


class FakeDialog:
    instances = []
    during_exec = None

    def __init__(self, parent, sync, notes, *, scope_label, empty_message):
        self.parent = parent
        self.sync = sync
        self.notes = notes
        self.scope_label = scope_label
        self.empty_message = empty_message
        FakeDialog.instances.append(self)

    def exec(self):
        if FakeDialog.during_exec is not None:
            FakeDialog.during_exec()
        if FakeDialog.raise_on_exec:
            raise RuntimeError("dialog crashed")
        return 0


class PluginTestCase(QtTestCase):
    def setUp(self):
        FakeDialog.instances = []
        FakeDialog.raise_on_exec = False
        FakeDialog.during_exec = None
        patcher = mock.patch.object(dialog_module, "UpdaterDialog", FakeDialog)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The real runner starts a thread against Nexus; this one only records.
        self.runners = []

        def make_runner(parent=None):
            self.runners.append(FakeScanRunner())
            return self.runners[-1]

        self.runner_patch = mock.patch.object(scan_worker_module, "QtScanRunner", make_runner)
        self.runner_patch.start()
        self.addCleanup(self.runner_patch.stop)
        self.manager = FakeDownloadManager()
        flagged = FakeMod("Flagged", 1, r"C:\Modding\MO2\mods\Flagged")
        current = FakeMod("Current", 2, r"C:\Modding\MO2\mods\Current",
                          version=FakeVersion(2, 0), newest=FakeVersion(2, 0))
        self.organizer = FakeOrganizer({"Flagged": flagged, "Current": current}, download_manager=self.manager)
        self.plugin = plugin_module.NexusAutoUpdater()

    def display(self):
        self.plugin.display()
        (dialog,) = FakeDialog.instances
        return dialog


class MetadataTest(PluginTestCase):
    def test_is_a_tool_plugin(self):
        self.assertIsInstance(self.plugin, mobase.IPluginTool)

    def test_metadata(self):
        self.assertEqual("Nexus Auto Updater", self.plugin.name())
        self.assertEqual("Nexus Auto Updater", self.plugin.displayName())
        self.assertEqual("Benoit", self.plugin.author())
        self.assertIn("Nexus v2 GraphQL API", self.plugin.description())
        self.assertEqual("Download the newest Nexus files for outdated mods", self.plugin.tooltip())
        self.assertEqual(mobase.VersionInfo(0, 1, 0, mobase.ReleaseType.PRE_ALPHA), self.plugin.version())
        self.assertIsInstance(self.plugin.icon(), QIcon)

    def test_settings(self):
        settings = {s.key: s.default_value for s in self.plugin.settings()}
        self.assertEqual(
            {"scan_all_nexus_mods": False, "request_delay_ms": 250, "max_concurrent_downloads": 3}, settings
        )
        self.assertTrue(all(s.description for s in self.plugin.settings()))

    def test_set_parent_widget(self):
        from PyQt6.QtWidgets import QWidget

        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        self.plugin.setParentWidget(parent)
        self.plugin.init(self.organizer)
        self.runner_patch.stop()
        self.assertIs(parent, self.display().parent)
        self.assertIsNotNone(parent.findChild(QtScanRunner))


class InitTest(PluginTestCase):
    def test_init_creates_host_and_tracker_and_waits_for_the_ui(self):
        self.assertTrue(self.plugin.init(self.organizer))
        self.assertIsInstance(self.plugin._host, Mo2Host)
        self.assertIsInstance(self.plugin._tracker, DownloadTracker)
        self.assertEqual([], self.manager.complete_hooks)
        (callback,) = self.organizer.ui_callbacks
        callback(object())
        self.assertEqual(1, len(self.manager.complete_hooks))
        self.assertEqual(1, len(self.manager.failed_hooks))

    def test_download_hooks_feed_the_tracker(self):
        self.plugin.init(self.organizer)
        self.organizer.ui_callbacks[0](None)
        tracker = self.plugin._tracker
        with mock.patch.object(tracker, "download_finished") as finished:
            self.plugin._host.on_download_finished(tracker.download_finished)
            self.manager.paths[0] = r"C:\Modding\MO2\downloads\Foo.7z"
            self.manager.complete_hooks[0](0)
        finished.assert_called_once_with(r"C:\Modding\MO2\downloads\Foo.7z", True)

    def test_tracker_retries_use_a_qt_timer(self):
        self.plugin.init(self.organizer)
        callback = mock.Mock()
        with mock.patch.object(plugin_module, "QTimer") as timer:
            self.plugin._tracker._schedule(250, callback)
        timer.singleShot.assert_called_once_with(250, callback)

    def test_tracker_retries_fire_on_the_event_loop(self):
        self.plugin.init(self.organizer)
        fired = []
        self.plugin._tracker._schedule(1, lambda: fired.append(True))
        self.assertEqual([], fired)
        wait_until(lambda: fired == [True])

    def test_tracker_reads_archive_metadata_from_disk(self):
        self.plugin.init(self.organizer)
        self.assertEqual((0, 0), self.plugin._tracker._read_archive_ids(r"C:\Does\Not\Exist\x.7z"))

    def test_connect_downloads_before_init_is_a_no_op(self):
        self.plugin._connect_downloads()
        self.assertEqual([], self.manager.complete_hooks)


class SettingsParsingTest(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.plugin.init(self.organizer)

    def with_setting(self, key, value):
        self.organizer.settings[key] = value

    def test_request_delay(self):
        cases = [(None, 250), ("garbage", 250), ([], 250), (-5, 0), (0, 0), ("100", 100), (500, 500), (12.7, 12)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.with_setting("request_delay_ms", value)
                self.assertEqual(expected, self.plugin._request_delay_ms())

    def test_max_concurrent_downloads(self):
        cases = [(None, 3), ("x", 3), ({}, 3), (0, 1), (-2, 1), (1, 1), ("5", 5), (8, 8)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.with_setting("max_concurrent_downloads", value)
                self.assertEqual(expected, self.plugin._max_concurrent_downloads())

    def test_setting_reads_the_plugins_own_namespace(self):
        with mock.patch.object(self.organizer, "pluginSetting", return_value=7) as setting:
            self.assertEqual(7, self.plugin._setting("request_delay_ms"))
        setting.assert_called_once_with("Nexus Auto Updater", "request_delay_ms")


class DisplayTest(PluginTestCase):
    def test_before_init_does_nothing(self):
        self.plugin.display()
        self.assertEqual([], FakeDialog.instances)

    def scan_while_open(self):
        """Start the scan as the dialog would, and report where it stands."""
        seen = []

        def start():
            sync = FakeDialog.instances[0].sync
            sync.start()
            seen.append(sync.status().scan)

        FakeDialog.during_exec = start
        self.plugin.display()
        return seen[0]

    def test_flagged_mods_only(self):
        self.plugin.init(self.organizer)
        self.assertEqual(Scanning(0, 1, ""), self.scan_while_open())
        (dialog,) = FakeDialog.instances
        self.assertIsInstance(dialog.notes, ModNotes)
        self.assertEqual("mods MO2 flags as having an update", dialog.scope_label)
        self.assertIn("run MO2's 'Check for updates' first, or enable 'scan_all_nexus_mods'", dialog.empty_message)
        self.assertEqual("https://www.nexusmods.com/skyrimspecialedition/mods/5", dialog.notes.page_url(5))
        self.assertIsNone(dialog.parent)

    def test_scan_all(self):
        self.organizer.settings["scan_all_nexus_mods"] = True
        self.plugin.init(self.organizer)
        self.assertEqual(Scanning(0, 2, ""), self.scan_while_open())
        self.assertEqual("all Nexus mods", FakeDialog.instances[0].scope_label)

    def test_request_delay_reaches_the_nexus_client(self):
        self.organizer.settings["request_delay_ms"] = 500
        self.plugin.init(self.organizer)
        with mock.patch.object(plugin_module, "GraphQLClient", wraps=plugin_module.GraphQLClient) as client:
            self.display()
        client.assert_called_once_with(min_interval_s=0.5)

    def test_display_connects_download_hooks(self):
        self.organizer.download_manager = None
        self.plugin.init(self.organizer)
        self.organizer.download_manager = self.manager
        self.display()
        self.assertEqual(1, len(self.manager.complete_hooks))

    def test_the_run_is_stopped_after_the_dialog(self):
        self.plugin.init(self.organizer)
        during = []
        FakeDialog.during_exec = lambda: during.append(self.runners[0].stopped)
        self.display()
        self.assertEqual([False], during)
        self.assertTrue(self.runners[0].stopped)

    def test_the_run_is_stopped_even_if_exec_raises(self):
        FakeDialog.raise_on_exec = True
        self.plugin.init(self.organizer)
        with self.assertRaisesRegex(RuntimeError, "dialog crashed"):
            self.plugin.display()
        self.assertTrue(self.runners[0].stopped)


class RootInitTest(QtTestCase):
    def test_create_plugin(self):
        path = os.path.join(REPO_ROOT, "__init__.py")
        with open(path, encoding="utf-8") as handle:
            code = compile(handle.read(), path, "exec")
        namespace = {"__name__": "nexus_auto_updater", "__package__": "nexus_auto_updater", "__file__": path}
        exec(code, namespace)
        plugin = namespace["createPlugin"]()
        self.assertIsInstance(plugin, plugin_module.NexusAutoUpdater)
        self.assertIsNot(plugin, namespace["createPlugin"]())
        self.assertIs(namespace["mobase"], mobase)


if __name__ == "__main__":
    unittest.main()
