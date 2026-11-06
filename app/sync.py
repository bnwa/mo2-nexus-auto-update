"""Orchestrates one updater run: the scan, the entries it produces, which of them the
user selected, and the batch of downloads they started.

Four independent parts of state live here, each moved only by pure transition
functions: the scan lifecycle, the download batch, the selection, and the entries
themselves (owned by ``UpdateSession``). A view renders snapshots and forwards input.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, FrozenSet, Optional, Protocol, Tuple, Union, assert_never

from .scan import ModScanned, ScanEvent, ScanFailed, ScanPlan, ScanProgress
from .session import (
    DownloadFailed,
    Downloading,
    EntryKey,
    Progress,
    Queued,
    UpdateEntry,
    UpdateSession,
    downloadable,
    installable,
)


class ScanRunner(Protocol):
    """Runs a ``ScanPlan`` off the thread that owns ``Sync``.

    Both callbacks are delivered on the thread that owns ``Sync``, never concurrently.
    After ``stop()`` returns no callback is delivered, and ``stop()`` does nothing if
    the runner was never started or is already stopped.
    """

    def start(
        self,
        plan: ScanPlan,
        on_event: Callable[[ScanEvent], None],
        on_finished: Callable[[], None],
    ) -> None: ...

    def stop(self) -> None: ...


# --- Scan lifecycle ---


@dataclass(frozen=True)
class NotStarted:
    pass


@dataclass(frozen=True)
class NothingToScan:
    pass


@dataclass(frozen=True)
class Scanning:
    done: int
    total: int
    current: str  # Mod being checked next; empty when finished.


@dataclass(frozen=True)
class Stopped:
    """The scan gave up early; the runner's own finish doesn't change that."""

    failure: ScanFailed


@dataclass(frozen=True)
class Complete:
    pass


ScanState = Union[NotStarted, NothingToScan, Scanning, Stopped, Complete]


def scan_started(is_empty: bool, total: int) -> ScanState:
    return NothingToScan() if is_empty else Scanning(0, total, "")


def scan_event(state: ScanState, event: ScanEvent) -> ScanState:
    match state:
        case Scanning():
            match event:
                case ScanProgress(done=done, total=total, mod_name=name):
                    return Scanning(done, total, name)
                case ModScanned():
                    return state
                case ScanFailed():
                    return Stopped(event)
                case _:
                    assert_never(event)
        case NotStarted() | NothingToScan() | Stopped() | Complete():
            return state
        case _:
            assert_never(state)


def scan_finished(state: ScanState) -> ScanState:
    match state:
        case Scanning():
            return Complete()
        case NotStarted() | NothingToScan() | Stopped() | Complete():
            return state
        case _:
            assert_never(state)


# --- Download batch ---


@dataclass(frozen=True)
class NoBatch:
    pass


@dataclass(frozen=True)
class InProgress:
    """Downloads the user started, counted until every one has finished."""

    keys: FrozenSet[EntryKey]
    done: int
    failed: int

    @property
    def total(self) -> int:
        return len(self.keys)


@dataclass(frozen=True)
class Finished:
    downloaded: int
    failed: int


BatchState = Union[NoBatch, InProgress, Finished]


def batch_started(batch: BatchState, key: EntryKey) -> BatchState:
    match batch:
        case InProgress(keys=keys, done=done, failed=failed):
            return InProgress(keys | {key}, done, failed)
        case NoBatch() | Finished():
            return InProgress(frozenset({key}), 0, 0)
        case _:
            assert_never(batch)


def batch_settled(batch: BatchState, progress_of: Callable[[EntryKey], Progress]) -> BatchState:
    match batch:
        case InProgress(keys=keys):
            steps = [progress_of(key) for key in keys]
            done = sum(1 for step in steps if not isinstance(step, (Queued, Downloading)))
            failed = sum(1 for step in steps if isinstance(step, DownloadFailed))
            if done < len(keys):
                return InProgress(keys, done, failed)
            return Finished(len(keys) - failed, failed)
        case NoBatch() | Finished():
            return batch
        case _:
            assert_never(batch)


# --- Snapshots and notifications ---


class SelectAll(Enum):
    UNAVAILABLE = "unavailable"  # Nothing can be downloaded.
    NOT_ALL = "not_all"
    ALL = "all"


@dataclass(frozen=True)
class SyncStatus:
    scan: ScanState
    batch: BatchState
    select_all: SelectAll
    installable: FrozenSet[EntryKey]

    @property
    def can_download(self) -> bool:
        return self.select_all is not SelectAll.UNAVAILABLE

    @property
    def can_install(self) -> bool:
        return bool(self.installable)


@dataclass(frozen=True)
class RowsAdded:
    keys: Tuple[EntryKey, ...]


@dataclass(frozen=True)
class RowChanged:
    key: EntryKey


@dataclass(frozen=True)
class StatusChanged:
    pass


SyncChange = Union[RowsAdded, RowChanged, StatusChanged]


class Sync:
    """One updater run. All methods and notifications run on the owning thread."""

    def __init__(self, session: UpdateSession, plan: ScanPlan, runner: ScanRunner):
        self._session = session
        self._plan = plan
        self._runner = runner
        self._entries: Dict[EntryKey, UpdateEntry] = {}
        self._rows: Tuple[EntryKey, ...] = ()
        self._row_of: Dict[EntryKey, int] = {}
        self._closed = False
        self._selected: FrozenSet[EntryKey] = frozenset()
        self._scan: ScanState = NotStarted()
        self._batch: BatchState = NoBatch()
        self._listener: Optional[Callable[[SyncChange], None]] = None
        session.subscribe(self._on_entry_changed)

    def subscribe(self, listener: Optional[Callable[[SyncChange], None]]) -> None:
        self._listener = listener

    # --- Snapshots ---

    def rows(self) -> Tuple[EntryKey, ...]:
        """Every entry key in the order the entries appeared.

        Append-only: a row is never reordered or removed, so a view can count the rows
        it has been told about with ``RowsAdded``.
        """
        return self._rows

    def row_of(self, key: EntryKey) -> int:
        return self._row_of[key]

    def entry(self, key: EntryKey) -> UpdateEntry:
        return self._entries[key]

    def is_selected(self, key: EntryKey) -> bool:
        return key in self._selected

    def status(self) -> SyncStatus:
        downloadable_count = sum(1 for e in self._entries.values() if downloadable(e))
        if downloadable_count == 0:
            select_all = SelectAll.UNAVAILABLE
        elif len(self._selected) == downloadable_count:
            select_all = SelectAll.ALL
        else:
            select_all = SelectAll.NOT_ALL
        return SyncStatus(
            scan=self._scan,
            batch=self._batch,
            select_all=select_all,
            installable=frozenset(key for key, e in self._entries.items() if installable(e)),
        )

    # --- Commands ---

    def start(self) -> None:
        """Begin the scan; does nothing once started or closed."""
        if self._closed or not isinstance(self._scan, NotStarted):
            return
        self._scan = scan_started(self._plan.is_empty, self._plan.total)
        self._emit(StatusChanged())
        if isinstance(self._scan, Scanning):
            self._runner.start(self._plan, self._on_scan_event, self._on_scan_finished)

    def close(self) -> None:
        """Stop scanning and downloading. Safe to call more than once."""
        self._closed = True
        self._runner.stop()
        self._session.close()
        self._listener = None

    def set_selected(self, key: EntryKey, selected: bool) -> None:
        if selected == (key in self._selected):
            return
        if selected and downloadable(self._entries[key]) is None:
            return
        self._selected = self._selected | {key} if selected else self._selected - {key}
        self._emit(RowChanged(key))
        self._emit(StatusChanged())

    def set_all_selected(self, selected: bool) -> None:
        target = (
            frozenset(key for key, entry in self._entries.items() if downloadable(entry))
            if selected
            else frozenset[EntryKey]()
        )
        changed = [key for key in self._entries if (key in target) != (key in self._selected)]
        if not changed:
            return
        self._selected = target
        for key in changed:
            self._emit(RowChanged(key))
        self._emit(StatusChanged())

    def set_locked(self, mod_id: int, locked: bool) -> None:
        self._session.set_locked(mod_id, locked)

    def download_selected(self) -> None:
        # The session reports each change synchronously and that edits the selection,
        # so work from a copy of the keys.
        keys = [key for key in self._entries if key in self._selected]
        for key in keys:
            # Counted before the download starts: its Queued change must find the key.
            self._batch = batch_started(self._batch, key)
            self._session.download(key)
        if keys:
            self._emit(StatusChanged())

    def install_downloaded(self, keys: FrozenSet[EntryKey]) -> None:
        """Install the given entries, those of them still installable.

        The caller passes the keys it showed the user: MO2's installer runs a nested
        event loop, so other entries can become installable in the meantime.
        """
        for key in self._rows:
            if key in keys:
                self._session.install(key)

    # --- Events ---

    def _on_scan_event(self, event: ScanEvent) -> None:
        if isinstance(event, ModScanned) and isinstance(self._scan, Scanning):
            added = self._session.record_scan(event.scan)
            for entry in added:
                self._entries[entry.key] = entry
                self._row_of[entry.key] = len(self._rows)
                self._rows += (entry.key,)
                offer = downloadable(entry)
                if offer is not None and not offer.needs_review:
                    self._selected = self._selected | {entry.key}
            if added:
                self._emit(RowsAdded(tuple(entry.key for entry in added)))
        self._scan = scan_event(self._scan, event)
        self._emit(StatusChanged())

    def _on_scan_finished(self) -> None:
        self._scan = scan_finished(self._scan)
        self._emit(StatusChanged())

    def _on_entry_changed(self, entry: UpdateEntry) -> None:
        self._entries[entry.key] = entry
        if downloadable(entry) is None:
            self._selected = self._selected - {entry.key}
        self._batch = batch_settled(self._batch, lambda key: self._entries[key].progress)
        self._emit(RowChanged(entry.key))
        self._emit(StatusChanged())

    def _emit(self, change: SyncChange) -> None:
        if self._listener is not None:
            self._listener(change)
