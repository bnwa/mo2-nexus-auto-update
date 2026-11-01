"""Adapter implementing the application's host ports on top of mobase."""

from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

import mobase

from ..app.ports import GameRef, InstalledMod
from .metadata import read_archive_ids, read_general_keys, read_installed_files

# (archive_path, succeeded)
DownloadFinished = Callable[[str, bool], None]

_LOCKS_KEY = "locked_mod_ids"


class Mo2Host:
    def __init__(self, organizer: mobase.IOrganizer, plugin_name: str):
        self._organizer = organizer
        self._plugin_name = plugin_name
        self._download_listener: Optional[DownloadFinished] = None
        self._hooks_registered = False

    # --- Game ---

    def managed_game(self) -> GameRef:
        game = self._organizer.managedGame()
        return GameRef(int(game.nexusGameID()), game.gameNexusName())

    # --- Inventory ---

    def nexus_mods(self, only_flagged: bool) -> List[InstalledMod]:
        """Nexus mods in priority order; ``only_flagged`` limits to MO2's update flags."""
        game = self._organizer.managedGame()
        own_game_names = {game.gameShortName().lower(), game.gameNexusName().lower()}
        downloads = Path(self._organizer.downloadsPath())
        mod_list = self._organizer.modList()

        mods: List[InstalledMod] = []
        for internal_name in mod_list.allModsByProfilePriority():
            mod = mod_list.getMod(internal_name)
            if not _is_nexus_mod(mod):
                continue
            if only_flagged and not _update_flagged(mod):
                continue

            meta_ini = Path(mod.absolutePath()) / "meta.ini"
            versions = read_general_keys(meta_ini, ("version", "newestVersion"))
            blocker = ""
            if mod.gameName().lower() not in own_game_names:
                blocker = (
                    f"Mod belongs to Nexus game '{mod.gameName()}'; MO2's plugin API can "
                    "only start downloads for the managed game."
                )
            mods.append(
                InstalledMod(
                    name=mod.name(),
                    mod_id=mod.nexusId(),
                    installed_file_ids=_installed_file_ids(mod, meta_ini, downloads),
                    version=versions.get("version", ""),
                    newest_version=versions.get("newestVersion", ""),
                    download_blocker=blocker,
                )
            )
        return mods

    # --- Locks (app.ports.ModLocks) ---

    def is_locked(self, mod_id: int) -> bool:
        return mod_id in self._locked_ids()

    def set_locked(self, mod_id: int, locked: bool) -> None:
        ids = self._locked_ids()
        if locked:
            ids.add(mod_id)
        else:
            ids.discard(mod_id)
        # Per-instance storage: survives reinstalling or renaming the mod.
        self._organizer.setPersistent(
            self._plugin_name, _LOCKS_KEY, ",".join(str(i) for i in sorted(ids)), True
        )

    def _locked_ids(self) -> Set[int]:
        raw = self._organizer.persistent(self._plugin_name, _LOCKS_KEY, "")
        return {int(part) for part in str(raw or "").split(",") if part.strip().isdigit()}

    # --- Downloads (app.ports.Downloader) ---

    def start_nexus_download(self, mod_id: int, file_id: int) -> None:
        # The returned ID only predicts MO2's internal list index, so callers
        # correlate completion through the archive instead.
        self._organizer.downloadManager().startDownloadNexusFile(mod_id, file_id)

    def on_download_finished(self, listener: DownloadFinished) -> None:
        """Report finished downloads by archive path. Safe to call repeatedly."""
        self._download_listener = listener
        if self._hooks_registered:
            return
        manager = self._organizer.downloadManager()
        if manager is None:  # Not available until MO2's UI is initialised.
            return
        manager.onDownloadComplete(lambda download_id: self._finished(download_id, True))
        manager.onDownloadFailed(lambda download_id: self._finished(download_id, False))
        self._hooks_registered = True

    def _finished(self, download_id: int, succeeded: bool) -> None:
        if self._download_listener is None:
            return
        # Resolve now: download IDs are list indices that can shift later.
        path = self._organizer.downloadManager().downloadPath(download_id)
        if path:
            self._download_listener(path, succeeded)

    # --- Installs (app.ports.Installer) ---

    def install_archive(self, archive_path: str, mod_name: str) -> bool:
        """Install via MO2, which shows its own replace prompt and any FOMOD wizard."""
        return self._organizer.installMod(archive_path, mod_name) is not None


def _is_nexus_mod(mod: Optional[mobase.IModInterface]) -> bool:
    if mod is None:
        return False
    if mod.isSeparator() or mod.isForeign() or mod.isBackup() or mod.isOverwrite():
        return False
    return mod.nexusId() > 0 and mod.repository().lower() in ("", "nexus")


def _update_flagged(mod: mobase.IModInterface) -> bool:
    newest = mod.newestVersion()
    if not newest.isValid():
        return False
    ignored = mod.ignoredVersion()
    if ignored.isValid() and ignored == newest:
        return False
    current = mod.version()
    return not current.isValid() or current < newest


def _installed_file_ids(
    mod: mobase.IModInterface, meta_ini: Path, downloads: Path
) -> Tuple[int, ...]:
    mod_id = mod.nexusId()
    ids = tuple(file_id for owner, file_id in read_installed_files(meta_ini) if owner == mod_id)
    if ids:
        return ids

    installation_file = mod.installationFile()
    if installation_file:
        archive = Path(installation_file)
        if not archive.is_absolute():
            archive = downloads / archive
        meta_mod_id, file_id = read_archive_ids(str(archive))
        if file_id > 0 and meta_mod_id in (0, mod_id):
            return (file_id,)
    return ()
