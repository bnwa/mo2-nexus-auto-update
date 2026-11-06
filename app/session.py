"""State of one updater session: what can be updated and how far each update got."""

from dataclasses import dataclass, replace
from itertools import count
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, assert_never

from ..domain.files import ModFile
from ..domain.requirements import DependencyGraph
from ..domain.selection import Selection, Verdict
from ..tracking.tracker import DownloadTracker, Expiry, Ticket
from .ports import ArchiveMatcher, Downloader, InstalledMod, Installer, ModLocks
from .scan import ModChecked, ModErrored, ModScan, ModSkipped

EntryKey = int

# MO2 reports no download progress, so a download that has produced no matching archive
# for this long is released from the batch and the concurrency limit. It is not given up
# on: an archive that still arrives resolves the entry.
DOWNLOAD_PATIENCE_MS = 30 * 60 * 1000


@dataclass(frozen=True)
class InstalledFile:
    """The installed file as far as the source knows it."""

    file_id: Optional[int]
    file: Optional[ModFile]
    # Files that may supersede the installed one, newest first.
    newer_files: Tuple[ModFile, ...] = ()


@dataclass(frozen=True)
class Skipped:
    """The mod can't be updated through the source."""

    reason: str


@dataclass(frozen=True)
class ScanError:
    message: str


@dataclass(frozen=True)
class UpToDate:
    installed: InstalledFile
    reason: str


@dataclass(frozen=True)
class Unresolved:
    installed: InstalledFile
    reason: str


@dataclass(frozen=True)
class Offered:
    installed: InstalledFile
    target: ModFile
    needs_review: bool
    reason: str


Finding = Union[Skipped, ScanError, UpToDate, Unresolved, Offered]


@dataclass(frozen=True)
class Idle:
    pass


@dataclass(frozen=True)
class Queued:
    pass


@dataclass(frozen=True)
class Downloading:
    pass


@dataclass(frozen=True)
class DownloadFailed:
    pass


@dataclass(frozen=True)
class Unmatched:
    """No matching archive appeared in time; one that arrives later still counts."""


@dataclass(frozen=True)
class Downloaded:
    archive: str


@dataclass(frozen=True)
class Installed:
    archive: str


@dataclass(frozen=True)
class InstallCancelled:
    archive: str


Progress = Union[
    Idle,
    Queued,
    Downloading,
    Unmatched,
    DownloadFailed,
    Downloaded,
    Installed,
    InstallCancelled,
]


@dataclass(frozen=True)
class UpdateEntry:
    """Read model of one installed file: what the scan found and how far its update got."""

    key: EntryKey
    mod_name: str
    mod_id: int
    version: str
    newest_version: str
    finding: Finding
    progress: Progress = Idle()
    locked: bool = False


def installed_of(finding: Finding) -> Optional[InstalledFile]:
    match finding:
        case Skipped() | ScanError():
            return None
        case UpToDate(installed=installed) | Unresolved(installed=installed):
            return installed
        case Offered(installed=installed):
            return installed
        case _:
            assert_never(finding)


def downloadable(entry: UpdateEntry) -> Optional[Offered]:
    """The offer, if the entry can be downloaded now."""
    if entry.locked or not isinstance(entry.progress, (Idle, DownloadFailed, Unmatched)):
        return None
    return entry.finding if isinstance(entry.finding, Offered) else None


def installable(entry: UpdateEntry) -> Optional[Downloaded]:
    """The downloaded archive, if the entry can be installed now."""
    if entry.locked or not isinstance(entry.progress, Downloaded):
        return None
    return entry.progress


EntryListener = Callable[[UpdateEntry], None]


class UpdateSession:
    def __init__(
        self,
        downloader: Downloader,
        installer: Installer,
        tracker: DownloadTracker,
        locks: ModLocks,
        max_concurrent_downloads: Callable[[], int],
    ):
        self._downloader = downloader
        self._installer = installer
        self._tracker = tracker
        self._locks = locks
        self._max_downloads = max_concurrent_downloads
        # Insertion order is the order downloads start in.
        self._queue: Dict[EntryKey, ModFile] = {}
        self._entries: Dict[EntryKey, UpdateEntry] = {}
        self._matchers: Dict[EntryKey, ArchiveMatcher] = {}
        self._awaiting: Dict[Ticket, EntryKey] = {}
        # Expired expectations: no slot held, but a late archive can still resolve them.
        self._unmatched: Dict[Ticket, EntryKey] = {}
        self._keys = count(1)
        self._listener: Optional[EntryListener] = None
        self._graph: Optional[DependencyGraph] = None
        tracker.set_listener(self._on_download_resolved)

    def subscribe(self, listener: Optional[EntryListener]) -> None:
        """Be told whenever an existing entry changes."""
        self._listener = listener

    def entries(self) -> List[UpdateEntry]:
        return list(self._entries.values())

    @property
    def dependency_graph(self) -> Optional[DependencyGraph]:
        """Requirements among installed mods; ``None`` until they have been loaded."""
        return self._graph

    def set_dependency_graph(self, graph: DependencyGraph) -> None:
        self._graph = graph

    def record_scan(self, scan: ModScan) -> List[UpdateEntry]:
        """Turn a mod's scan outcome into new entries."""
        match scan:
            case ModSkipped(mod=mod, reason=reason):
                return [self._add(mod, Skipped(reason), None)]
            case ModErrored(mod=mod, message=message):
                return [self._add(mod, ScanError(message), None)]
            case ModChecked(mod=mod, selections=selections, target_matchers=matchers):
                added: List[UpdateEntry] = []
                for selection in selections:
                    finding = _finding(selection)
                    matcher = (
                        matchers.get(finding.target.file_id)
                        if isinstance(finding, Offered)
                        else None
                    )
                    added.append(self._add(mod, finding, matcher))
                return added
            case _:
                assert_never(scan)

    def download(self, key: EntryKey) -> None:
        """Queue a download; it starts once fewer than the maximum are in flight."""
        offer = downloadable(self._entries[key])
        if offer is None:
            return
        for ticket, owner in list(self._unmatched.items()):
            if owner == key:
                self._tracker.cancel(ticket)
                del self._unmatched[ticket]
        self._queue[key] = offer.target
        self._update(key, progress=Queued())
        self._start_queued()

    def set_locked(self, mod_id: int, locked: bool) -> None:
        """Lock or unlock every entry of a Nexus mod, and remember it.

        Locking dequeues queued downloads. A download already in flight can't be
        cancelled, but its archive can't be installed while the mod is locked.
        """
        self._locks.set_locked(mod_id, locked)
        for key, entry in list(self._entries.items()):
            if entry.mod_id != mod_id or entry.locked == locked:
                continue
            changes: Dict[str, Any] = dict(locked=locked)
            if locked and isinstance(entry.progress, Queued):
                del self._queue[key]
                changes["progress"] = Idle()
            self._update(key, **changes)

    def install(self, key: EntryKey) -> None:
        entry = self._entries[key]
        download = installable(entry)
        if download is None:
            return
        installed = self._installer.install_archive(download.archive, entry.mod_name)
        progress = Installed(download.archive) if installed else InstallCancelled(download.archive)
        self._update(key, progress=progress)

    def close(self) -> None:
        """Stop tracking this session's downloads; the tracker outlives the session."""
        for ticket in (*self._awaiting, *self._unmatched):
            self._tracker.cancel(ticket)
        self._awaiting.clear()
        self._unmatched.clear()
        self._queue.clear()
        self._tracker.set_listener(None)
        self._listener = None

    def _start_queued(self) -> None:
        while self._queue and len(self._awaiting) < self._max_downloads():
            key = next(iter(self._queue))
            target = self._queue.pop(key)
            entry = self._entries[key]
            # Archive-name matching is only a fallback to the host's metadata, so a
            # source that offers none still works.
            matcher = self._matchers.get(key)
            ticket = self._tracker.expect(
                entry.mod_id,
                target.file_id,
                matcher.matches if matcher is not None else _never,
                Expiry(DOWNLOAD_PATIENCE_MS, self._on_download_expired),
            )
            self._awaiting[ticket] = key
            self._update(key, progress=Downloading())
            self._downloader.start_download(entry.mod_id, target.file_id)

    def _add(
        self, mod: InstalledMod, finding: Finding, matcher: Optional[ArchiveMatcher]
    ) -> UpdateEntry:
        entry = UpdateEntry(
            key=next(self._keys),
            mod_name=mod.name,
            mod_id=mod.mod_id,
            version=mod.version,
            newest_version=mod.newest_version,
            finding=finding,
            locked=self._locks.is_locked(mod.mod_id),
        )
        self._entries[entry.key] = entry
        if matcher is not None:
            self._matchers[entry.key] = matcher
        return entry

    def _update(self, key: EntryKey, **changes: Any) -> None:
        entry = replace(self._entries[key], **changes)
        self._entries[key] = entry
        if self._listener is not None:
            self._listener(entry)

    def _on_download_resolved(self, ticket: Ticket, archive_path: str, succeeded: bool) -> None:
        key = self._awaiting.pop(ticket, None)
        if key is None:
            key = self._unmatched.pop(ticket, None)
        if key is None:
            return
        if succeeded:
            self._update(key, progress=Downloaded(archive_path))
        else:
            self._update(key, progress=DownloadFailed())
        self._start_queued()

    def _on_download_expired(self, ticket: Ticket) -> None:
        key = self._awaiting.pop(ticket)  # The tracker only expires tickets still awaited.
        self._unmatched[ticket] = key
        self._update(key, progress=Unmatched())
        self._start_queued()


def _finding(selection: Selection) -> Finding:
    installed = InstalledFile(
        selection.installed_file_id, selection.installed, selection.newer_files
    )
    match selection.verdict:
        case Verdict.READY | Verdict.REVIEW:
            assert selection.target is not None  # The domain only offers a file it chose.
            return Offered(
                installed, selection.target, selection.verdict is Verdict.REVIEW, selection.reason
            )
        case Verdict.UP_TO_DATE:
            return UpToDate(installed, selection.reason)
        case Verdict.UNRESOLVED:
            return Unresolved(installed, selection.reason)
        case _:
            assert_never(selection.verdict)


def _never(_file_name: str) -> bool:
    return False
