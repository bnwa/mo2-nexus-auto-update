"""A stand-in ``mobase`` module and fake MO2 objects (organizer, mods, downloads)."""

import enum
import sys
import types
from typing import Callable, Dict, List, Optional


def _build_module() -> types.ModuleType:
    module = types.ModuleType("mobase")
    module.__doc__ = "Test stand-in for MO2's mobase; only what the plugin touches."

    class IPlugin:
        def __init__(self):
            pass

    class IPluginTool(IPlugin):
        pass

    class IOrganizer:
        pass

    class IModInterface:
        pass

    class ReleaseType(enum.Enum):
        PRE_ALPHA = 0
        ALPHA = 1
        BETA = 2
        CANDIDATE = 3
        FINAL = 4

    class VersionInfo:
        def __init__(self, major, minor, subminor, release_type):
            self.parts = (major, minor, subminor, release_type)

        def __eq__(self, other):
            return isinstance(other, VersionInfo) and self.parts == other.parts

    class PluginSetting:
        def __init__(self, key, description, default_value):
            self.key = key
            self.description = description
            self.default_value = default_value

    for item in (IPlugin, IPluginTool, IOrganizer, IModInterface, ReleaseType, VersionInfo, PluginSetting):
        setattr(module, item.__name__, item)
    return module


def install() -> None:
    sys.modules.setdefault("mobase", _build_module())


class FakeVersion:
    """mobase.VersionInfo as the host uses it: validity, equality, ordering."""

    def __init__(self, *parts: int):
        self.parts = parts

    def isValid(self) -> bool:
        return bool(self.parts)

    def __eq__(self, other):
        return self.parts == other.parts

    def __lt__(self, other):
        return self.parts < other.parts


class FakeGame:
    def __init__(self, nexus_id=1704, nexus_name="skyrimspecialedition", short_name="SkyrimSE"):
        self._nexus_id = nexus_id
        self._nexus_name = nexus_name
        self._short_name = short_name

    def nexusGameID(self):
        return self._nexus_id

    def gameNexusName(self) -> str:
        return self._nexus_name

    def gameShortName(self) -> str:
        return self._short_name


class FakeMod:
    def __init__(
        self,
        name: str = "Some Mod",
        nexus_id: int = 100,
        path: str = r"C:\Modding\MO2\mods\Some Mod",
        *,
        game_name: str = "skyrimspecialedition",
        repository: str = "Nexus",
        separator: bool = False,
        foreign: bool = False,
        backup: bool = False,
        overwrite: bool = False,
        version: FakeVersion = FakeVersion(1, 0),
        newest: FakeVersion = FakeVersion(2, 0),
        ignored: FakeVersion = FakeVersion(),
        installation_file: str = "",
    ):
        self._name = name
        self._nexus_id = nexus_id
        self._path = path
        self._game_name = game_name
        self._repository = repository
        self._flags = (separator, foreign, backup, overwrite)
        self._version = version
        self._newest = newest
        self._ignored = ignored
        self._installation_file = installation_file

    def name(self):
        return self._name

    def nexusId(self):
        return self._nexus_id

    def absolutePath(self):
        return self._path

    def gameName(self):
        return self._game_name

    def repository(self):
        return self._repository

    def isSeparator(self):
        return self._flags[0]

    def isForeign(self):
        return self._flags[1]

    def isBackup(self):
        return self._flags[2]

    def isOverwrite(self):
        return self._flags[3]

    def version(self):
        return self._version

    def newestVersion(self):
        return self._newest

    def ignoredVersion(self):
        return self._ignored

    def installationFile(self):
        return self._installation_file


class FakeModList:
    def __init__(self, mods: Dict[str, Optional[FakeMod]]):
        self._mods = mods  # internal name -> mod, in priority order

    def allModsByProfilePriority(self) -> List[str]:
        return list(self._mods)

    def getMod(self, name: str):
        return self._mods[name]


class FakeDownloadManager:
    def __init__(self):
        self.started = []
        self.paths: Dict[int, str] = {}
        self.complete_hooks: List[Callable[[int], None]] = []
        self.failed_hooks: List[Callable[[int], None]] = []

    def startDownloadNexusFile(self, mod_id, file_id):
        self.started.append((mod_id, file_id))
        return len(self.started) - 1

    def onDownloadComplete(self, callback):
        self.complete_hooks.append(callback)
        return True

    def onDownloadFailed(self, callback):
        self.failed_hooks.append(callback)
        return True

    def downloadPath(self, download_id):
        return self.paths.get(download_id, "")


class FakeOrganizer:
    def __init__(
        self,
        mods: Optional[Dict[str, Optional[FakeMod]]] = None,
        *,
        game: Optional[FakeGame] = None,
        downloads_path: str = r"C:\Modding\MO2\downloads",
        download_manager: Optional[FakeDownloadManager] = None,
        settings: Optional[dict] = None,
    ):
        self.game = game or FakeGame()
        self.mod_list = FakeModList(mods or {})
        self.downloads_path = downloads_path
        self.download_manager = download_manager
        self.settings = dict(settings or {})
        self.persistent_store: Dict[tuple, object] = {}
        self.set_persistent_calls = []
        self.install_calls = []
        self.install_result: object = object()
        self.ui_callbacks = []

    def managedGame(self):
        return self.game

    def downloadsPath(self):
        return self.downloads_path

    def modList(self):
        return self.mod_list

    def downloadManager(self):
        return self.download_manager

    def persistent(self, plugin_name, key, default=None):
        return self.persistent_store.get((plugin_name, key), default)

    def setPersistent(self, plugin_name, key, value, sync=True):
        self.set_persistent_calls.append((plugin_name, key, value, sync))
        self.persistent_store[(plugin_name, key)] = value

    def installMod(self, archive_path, name_suggestion=""):
        self.install_calls.append((archive_path, name_suggestion))
        return self.install_result

    def pluginSetting(self, plugin_name, key):
        return self.settings.get(key)

    def onUserInterfaceInitialized(self, callback):
        self.ui_callbacks.append(callback)
        return True
