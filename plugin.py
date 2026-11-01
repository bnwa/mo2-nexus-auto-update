"""MO2 tool plugin: composition root wiring the layers together."""

from typing import List, Optional

import mobase
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon

from .app.notes import ModNotes
from .app.scan import ScanPlan
from .app.session import UpdateSession
from .host.metadata import read_archive_ids
from .host.mo2 import Mo2Host
from .nexus.catalog import NexusV2FileSource
from .nexus.graphql import GraphQLClient
from .tracking.tracker import DownloadTracker

SETTING_SCAN_ALL = "scan_all_nexus_mods"
SETTING_REQUEST_DELAY = "request_delay_ms"
DEFAULT_REQUEST_DELAY_MS = 250
SETTING_MAX_DOWNLOADS = "max_concurrent_downloads"
DEFAULT_MAX_DOWNLOADS = 3


class NexusAutoUpdater(mobase.IPluginTool):
    NAME = "Nexus Auto Updater"

    def __init__(self):
        super().__init__()
        self._organizer: Optional[mobase.IOrganizer] = None
        self._host: Optional[Mo2Host] = None
        self._tracker: Optional[DownloadTracker] = None
        self._parent_widget = None

    # --- IPlugin ---

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        self._host = Mo2Host(organizer, self.NAME)
        # Lives as long as the plugin: MO2's download hooks cannot be unregistered.
        self._tracker = DownloadTracker(
            read_archive_ids=read_archive_ids,
            schedule=lambda delay_ms, callback: QTimer.singleShot(delay_ms, callback),
        )
        organizer.onUserInterfaceInitialized(lambda _window: self._connect_downloads())
        return True

    def name(self) -> str:
        return self.NAME

    def author(self) -> str:
        return "Benoit"

    def description(self) -> str:
        return (
            "Finds the Nexus file that supersedes each installed file of mods MO2 "
            "flags as outdated (via the Nexus v2 GraphQL API) and downloads it "
            "through MO2's download manager."
        )

    def version(self) -> mobase.VersionInfo:
        return mobase.VersionInfo(0, 1, 0, mobase.ReleaseType.PRE_ALPHA)

    def settings(self) -> List[mobase.PluginSetting]:
        return [
            mobase.PluginSetting(
                SETTING_SCAN_ALL,
                "Check every Nexus mod, not only those MO2 flags as having an update",
                False,
            ),
            mobase.PluginSetting(
                SETTING_REQUEST_DELAY,
                "Delay between Nexus API requests (milliseconds)",
                DEFAULT_REQUEST_DELAY_MS,
            ),
            mobase.PluginSetting(
                SETTING_MAX_DOWNLOADS,
                "Maximum simultaneous downloads (read when the tool is opened)",
                DEFAULT_MAX_DOWNLOADS,
            ),
        ]

    # --- IPluginTool ---

    def displayName(self) -> str:
        return self.NAME

    def tooltip(self) -> str:
        return "Download the newest Nexus files for outdated mods"

    def icon(self) -> QIcon:
        return QIcon()

    def setParentWidget(self, widget) -> None:
        self._parent_widget = widget

    def display(self) -> None:
        if self._host is None or self._tracker is None:
            return
        from .ui.dialog import UpdaterDialog  # Deferred: keeps plugin load light.

        self._connect_downloads()
        scan_all = bool(self._setting(SETTING_SCAN_ALL))
        # The only place that knows the file source is Nexus v2 (beta).
        source = NexusV2FileSource(GraphQLClient(min_interval_s=self._request_delay_ms() / 1000))
        game = self._host.managed_game()
        plan = ScanPlan(source, game, self._host.nexus_mods(not scan_all))
        session = UpdateSession(
            self._host, self._host, self._tracker, self._host, self._max_concurrent_downloads()
        )

        dialog = UpdaterDialog(
            self._parent_widget,
            plan,
            session,
            ModNotes(source, game),
            scope_label="all Nexus mods" if scan_all else "mods MO2 flags as having an update",
            empty_message=(
                "No mods to check. MO2 has not flagged any Nexus mod as outdated — run "
                f"MO2's 'Check for updates' first, or enable '{SETTING_SCAN_ALL}'."
            ),
        )
        try:
            dialog.exec()
        finally:
            session.close()

    # --- Helpers ---

    def _connect_downloads(self) -> None:
        if self._host is not None and self._tracker is not None:
            self._host.on_download_finished(self._tracker.download_finished)

    def _setting(self, key: str):
        assert self._organizer is not None
        return self._organizer.pluginSetting(self.NAME, key)

    def _request_delay_ms(self) -> int:
        try:
            return max(0, int(self._setting(SETTING_REQUEST_DELAY)))
        except (TypeError, ValueError):
            return DEFAULT_REQUEST_DELAY_MS

    def _max_concurrent_downloads(self) -> int:
        try:
            return max(1, int(self._setting(SETTING_MAX_DOWNLOADS)))
        except (TypeError, ValueError):
            return DEFAULT_MAX_DOWNLOADS
