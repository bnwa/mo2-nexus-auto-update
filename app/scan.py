"""Check installed mods against the file source and choose their update targets."""

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, Mapping, Sequence, Tuple, Union

from ..domain.selection import Selection, plan_updates
from .ports import (
    ArchiveMatcher,
    GameRef,
    InstalledMod,
    ModFileSource,
    SourceError,
    SourceIncompatible,
    SourceUnavailable,
)

# Consecutive transient failures after which the source is presumed down.
MAX_CONSECUTIVE_UNAVAILABLE = 3


@dataclass(frozen=True)
class ModScan:
    """Outcome of checking one mod. Exactly one of the outcomes is populated."""

    mod: InstalledMod
    selections: Tuple[Selection, ...] = ()
    # Archive recognition for the selected targets, by file ID (may be partial).
    target_matchers: Mapping[int, ArchiveMatcher] = field(default_factory=lambda: {})
    error: str = ""
    skipped_reason: str = ""


@dataclass(frozen=True)
class ScanProgress:
    done: int
    total: int
    mod_name: str  # Mod being checked next; empty when finished.


@dataclass(frozen=True)
class ModScanned:
    scan: ModScan


class StopReason:
    INCOMPATIBLE = "incompatible"  # The source's contract changed; the plugin needs an update.
    UNAVAILABLE = "unavailable"  # The source keeps failing; try again later.


@dataclass(frozen=True)
class ScanFailed:
    """The scan stopped early; mods not yet checked were left out."""

    reason: str  # A StopReason value
    message: str
    unchecked: int


ScanEvent = Union[ScanProgress, ModScanned, ScanFailed]


class ScanPlan:
    """A blocking, interruptible scan; run it off the UI thread."""

    def __init__(self, source: ModFileSource, game: GameRef, mods: Sequence[InstalledMod]):
        self._source = source
        self._game = game
        self._blocked = [m for m in mods if m.download_blocker]
        self._checkable = [m for m in mods if not m.download_blocker]

    @property
    def total(self) -> int:
        """Number of mods that need a source request."""
        return len(self._checkable)

    @property
    def is_empty(self) -> bool:
        return not (self._blocked or self._checkable)

    def run(self, should_stop: Callable[[], bool]) -> Iterator[ScanEvent]:
        for mod in self._blocked:
            yield ModScanned(ModScan(mod, skipped_reason=mod.download_blocker))

        total = len(self._checkable)
        consecutive_unavailable = 0
        for index, mod in enumerate(self._checkable):
            if should_stop():
                return
            yield ScanProgress(index, total, mod.name)
            try:
                listing = self._source.list_files(self._game, mod.mod_id)
            except SourceIncompatible as exc:
                # Every remaining request would fail the same way.
                yield ScanFailed(StopReason.INCOMPATIBLE, str(exc), total - index)
                return
            except SourceUnavailable as exc:
                consecutive_unavailable += 1
                yield ModScanned(ModScan(mod, error=str(exc)))
                if consecutive_unavailable >= MAX_CONSECUTIVE_UNAVAILABLE:
                    yield ScanFailed(StopReason.UNAVAILABLE, str(exc), total - index - 1)
                    return
                continue
            except SourceError as exc:
                consecutive_unavailable = 0
                yield ModScanned(ModScan(mod, error=str(exc)))
                continue

            consecutive_unavailable = 0
            selections = tuple(plan_updates(mod.installed_file_ids, listing.files))
            matchers: Dict[int, ArchiveMatcher] = {}
            for selection in selections:
                if selection.target is not None:
                    matcher = listing.archive_matcher(selection.target.file_id)
                    if matcher is not None:
                        matchers[selection.target.file_id] = matcher
            yield ModScanned(ModScan(mod, selections, matchers))
        if total:
            yield ScanProgress(total, total, "")
