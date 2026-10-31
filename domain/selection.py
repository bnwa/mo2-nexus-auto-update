from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional, Sequence

from .files import FileCategory, ModFile

_INCOMPLETE_NOTE = " Some file details from Nexus were missing or unrecognised; verify."


class Verdict(Enum):
    READY = "ready"
    REVIEW = "review"
    UP_TO_DATE = "up_to_date"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Selection:
    """The successor chosen for one installed file."""

    installed_file_id: Optional[int]
    verdict: Verdict
    installed: Optional[ModFile]
    target: Optional[ModFile]
    reason: str


def select_successor(installed_file_id: Optional[int], files: Iterable[ModFile]) -> Selection:
    """Choose the replacement for ``installed_file_id`` among a mod's files.

    The primary signal is the file group: an author's successive uploads of the same
    logical file share a group even when renamed. When the group can't answer,
    fall back to the mod's single primary MAIN file, flagged for review.

    Incomplete file data never yields READY or UP_TO_DATE: a beta source may drop or
    rename fields, and a confident wrong answer is worse than asking the user.
    """
    files = list(files)
    installed = next((f for f in files if f.file_id == installed_file_id), None)

    if installed is None:
        why = (
            f"Installed file {installed_file_id} is not listed on Nexus."
            if installed_file_id
            else "No installed file ID is recorded for this mod."
        )
        return _fallback(installed_file_id, None, files, why)

    if installed.group_id is None:
        return _fallback(installed_file_id, installed, files, "Installed file has no file group.")

    group = [f for f in files if f.group_id == installed.group_id and f.file_id != installed.file_id]
    newer = [f for f in group if _maybe_newer(f, installed)]
    uncertain = not installed.is_fully_known or any(not f.is_fully_known for f in newer)
    candidates = [f for f in newer if f.is_active and f.uploaded_at is not None]
    newest = max(candidates, key=lambda f: (f.uploaded_at, f.file_id), default=None)

    if newest is not None:
        reason = f"Newest active file in file group {installed.group_id}."
        if uncertain:
            return Selection(
                installed_file_id, Verdict.REVIEW, installed, newest, reason + _INCOMPLETE_NOTE
            )
        return Selection(installed_file_id, Verdict.READY, installed, newest, reason)

    if uncertain:
        return Selection(
            installed_file_id,
            Verdict.UNRESOLVED,
            installed,
            None,
            f"Cannot tell whether file group {installed.group_id} has a newer file."
            + _INCOMPLETE_NOTE,
        )
    if installed.is_active:
        return Selection(
            installed_file_id,
            Verdict.UP_TO_DATE,
            installed,
            None,
            "Installed file is still the newest in its file group.",
        )
    # Retired file whose group has nothing newer: the author most likely moved the
    # file line to a new group.
    return _fallback(
        installed_file_id,
        installed,
        files,
        f"Installed file is {installed.category.value} and file group "
        f"{installed.group_id} has no newer active file.",
    )


def plan_updates(installed_file_ids: Sequence[int], files: Iterable[ModFile]) -> List[Selection]:
    """One selection per installed file, never proposing the same target twice."""
    files = list(files)
    selections: List[Selection] = []
    proposed = set()
    for file_id in installed_file_ids or [None]:
        selection = select_successor(file_id, files)
        if selection.target is not None:
            if selection.target.file_id in proposed:
                continue
            proposed.add(selection.target.file_id)
        selections.append(selection)
    return selections


def _maybe_newer(candidate: ModFile, installed: ModFile) -> bool:
    if candidate.uploaded_at is None or installed.uploaded_at is None:
        return True
    return candidate.uploaded_at > installed.uploaded_at


def _fallback(
    installed_file_id: Optional[int],
    installed: Optional[ModFile],
    files: List[ModFile],
    why: str,
) -> Selection:
    """Suggest the mod's single primary main file; always for review, never automatic."""
    primaries = [f for f in files if f.is_primary and f.category is FileCategory.MAIN]
    if len(primaries) == 1:
        candidate = primaries[0]
        if installed is not None and candidate.file_id == installed.file_id:
            return Selection(installed_file_id, Verdict.UP_TO_DATE, installed, None, why)
        if installed is None or _maybe_newer(candidate, installed):
            return Selection(
                installed_file_id,
                Verdict.REVIEW,
                installed,
                candidate,
                f"{why} Suggesting the mod's primary main file.",
            )
    return Selection(installed_file_id, Verdict.UNRESOLVED, installed, None, why)
