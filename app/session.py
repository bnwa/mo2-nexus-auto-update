"""State of one updater session: what can be updated and how far each update got."""

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from itertools import count
from typing import Callable, Deque, Dict, List, Optional

from ..domain.files import ModFile
from ..domain.selection import Verdict
from ..tracking.tracker import DownloadTracker, Ticket
from .ports import ArchiveMatcher, Downloader, Installer, ModLocks
from .scan import ModScan

EntryKey = int


class EntryStatus(Enum):
    READY = "ready"
    REVIEW = "review"
    UP_TO_DATE = "up_to_date"
    UNRESOLVED = "unresolved"
    SKIPPED = "skipped"
    ERROR = "error"


class Phase(Enum):
    IDLE = "idle"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    DOWNLOAD_FAILED = "download_failed"
    INSTALLED = "installed"
    INSTALL_CANCELLED = "install_cancelled"


_VERDICT_STATUS = {
    Verdict.READY: EntryStatus.READY,
    Verdict.REVIEW: EntryStatus.REVIEW,
    Verdict.UP_TO_DATE: EntryStatus.UP_TO_DATE,
    Verdict.UNRESOLVED: EntryStatus.UNRESOLVED,
}
_OFFERED = (EntryStatus.READY, EntryStatus.REVIEW)


@dataclass(frozen=True)
class UpdateEntry:
    """Read model of one installed file and its proposed update."""

    key: EntryKey
    mod_name: str
    mod_id: int
    version: str
    newest_version: str
    installed_file_id: Optional[int]
    installed: Optional[ModFile]
    target: Optional[ModFile]
    status: EntryStatus
    reason: str
    phase: Phase = Phase.IDLE
    archive_path: str = ""
    locked: bool = False

    @property
    def can_download(self) -> bool:
        return (
            not self.locked
            and self.target is not None
            and self.status in _OFFERED
            and self.phase in (Phase.IDLE, Phase.DOWNLOAD_FAILED)
        )

    @property
    def can_install(self) -> bool:
        return not self.locked and self.phase is Phase.DOWNLOADED and bool(self.archive_path)


EntryListener = Callable[[UpdateEntry], None]


class UpdateSession:
    def __init__(
        self,
        downloader: Downloader,
        installer: Installer,
        tracker: DownloadTracker,
        locks: ModLocks,
        max_concurrent_downloads: int,
    ):
        self._downloader = downloader
        self._installer = installer
        self._tracker = tracker
        self._locks = locks
        self._max_downloads = max_concurrent_downloads
        self._queue: Deque[EntryKey] = deque()
        self._entries: Dict[EntryKey, UpdateEntry] = {}
        self._matchers: Dict[EntryKey, ArchiveMatcher] = {}
        self._awaiting: Dict[Ticket, EntryKey] = {}
        self._keys = count(1)
        self._listener: Optional[EntryListener] = None
        tracker.set_listener(self._on_download_resolved)

    def subscribe(self, listener: Optional[EntryListener]) -> None:
        """Be told whenever an existing entry changes."""
        self._listener = listener

    def entries(self) -> List[UpdateEntry]:
        return list(self._entries.values())

    def record_scan(self, scan: ModScan) -> List[UpdateEntry]:
        """Turn a mod's scan outcome into new entries."""
        mod = scan.mod
        common = dict(
            mod_name=mod.name,
            mod_id=mod.mod_id,
            version=mod.version,
            newest_version=mod.newest_version,
            locked=self._locks.is_locked(mod.mod_id),
        )
        if scan.skipped_reason or scan.error:
            status = EntryStatus.SKIPPED if scan.skipped_reason else EntryStatus.ERROR
            return [
                self._add(
                    UpdateEntry(
                        key=next(self._keys),
                        installed_file_id=None,
                        installed=None,
                        target=None,
                        status=status,
                        reason=scan.skipped_reason or scan.error,
                        **common,
                    ),
                    None,
                )
            ]

        added = []
        for selection in scan.selections:
            target = selection.target
            entry = UpdateEntry(
                key=next(self._keys),
                installed_file_id=selection.installed_file_id,
                installed=selection.installed,
                target=target,
                status=_VERDICT_STATUS[selection.verdict],
                reason=selection.reason,
                **common,
            )
            matcher = scan.target_matchers.get(target.file_id) if target else None
            added.append(self._add(entry, matcher))
        return added

    def download(self, key: EntryKey) -> None:
        """Queue a download; it starts once fewer than the maximum are in flight."""
        entry = self._entries[key]
        if not entry.can_download:
            return
        self._queue.append(key)
        self._update(key, phase=Phase.QUEUED, archive_path="")
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
            changes = dict(locked=locked)
            if locked and entry.phase is Phase.QUEUED:
                self._queue.remove(key)
                changes["phase"] = Phase.IDLE
            self._update(key, **changes)

    def install(self, key: EntryKey) -> None:
        entry = self._entries[key]
        if not entry.can_install:
            return
        installed = self._installer.install_archive(entry.archive_path, entry.mod_name)
        self._update(key, phase=Phase.INSTALLED if installed else Phase.INSTALL_CANCELLED)

    def close(self) -> None:
        """Stop tracking this session's downloads; the tracker outlives the session."""
        for ticket in self._awaiting:
            self._tracker.cancel(ticket)
        self._awaiting.clear()
        self._queue.clear()
        self._tracker.set_listener(None)
        self._listener = None

    def _start_queued(self) -> None:
        while self._queue and len(self._awaiting) < self._max_downloads:
            key = self._queue.popleft()
            entry = self._entries[key]
            target = entry.target
            assert target is not None  # Guaranteed by can_download when queued.
            # Archive-name matching is only a fallback to the host's metadata, so a
            # source that offers none still works.
            matcher = self._matchers.get(key)
            ticket = self._tracker.expect(
                entry.mod_id,
                target.file_id,
                matcher.matches if matcher is not None else _never,
            )
            self._awaiting[ticket] = key
            self._update(key, phase=Phase.DOWNLOADING)
            self._downloader.start_nexus_download(entry.mod_id, target.file_id)

    def _add(self, entry: UpdateEntry, matcher: Optional[ArchiveMatcher]) -> UpdateEntry:
        self._entries[entry.key] = entry
        if matcher is not None:
            self._matchers[entry.key] = matcher
        return entry

    def _update(self, key: EntryKey, **changes) -> None:
        entry = replace(self._entries[key], **changes)
        self._entries[key] = entry
        if self._listener is not None:
            self._listener(entry)

    def _on_download_resolved(self, ticket: Ticket, archive_path: str, succeeded: bool) -> None:
        key = self._awaiting.pop(ticket, None)
        if key is None:
            return
        if succeeded:
            self._update(key, phase=Phase.DOWNLOADED, archive_path=archive_path)
        else:
            self._update(key, phase=Phase.DOWNLOAD_FAILED)
        self._start_queued()


def _never(_file_name: str) -> bool:
    return False
