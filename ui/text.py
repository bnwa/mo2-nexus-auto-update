"""Display text and colours for application states."""

from typing import Optional, Tuple, assert_never

from ..app.scan import ScanFailed, StopReason
from ..app.session import (
    DownloadFailed,
    Downloaded,
    Downloading,
    Finding,
    Idle,
    InstallCancelled,
    Installed,
    Offered,
    Progress,
    Queued,
    ScanError,
    Skipped,
    Unmatched,
    UpdateEntry,
    Unresolved,
    UpToDate,
)
from ..domain.files import FileCategory, ModFile

# Order of the scan summary.
FINDING_LABELS = ("Ready", "Needs review", "Up to date", "Unresolved", "Skipped", "Error")


LOCK_HEADER = "\N{LOCK}"
LOCK_TOOLTIP = (
    "Lock this mod's version: updates are still checked for, but not downloaded "
    "or installed until you unlock it."
)
LOCKED_COLOR = "#7f8c8d"


def _finding_look(finding: Finding) -> Tuple[str, str]:
    match finding:
        case Offered(needs_review=True):
            return "Needs review", "#e67e22"
        case Offered():
            return "Ready", "#27ae60"
        case UpToDate():
            return "Up to date", "#7f8c8d"
        case Unresolved():
            return "Unresolved", "#c0392b"
        case Skipped():
            return "Skipped", "#7f8c8d"
        case ScanError():
            return "Error", "#e74c3c"
        case _:
            assert_never(finding)


def finding_label(finding: Finding) -> str:
    return _finding_look(finding)[0]


def finding_detail(finding: Finding) -> str:
    match finding:
        case Skipped(reason=detail) | UpToDate(reason=detail) | Unresolved(reason=detail):
            return detail
        case Offered(reason=detail):
            return detail
        case ScanError(message=detail):
            return detail
        case _:
            assert_never(finding)


def _progress_look(progress: Progress) -> Optional[Tuple[str, str]]:
    """Text and colour of a started update; ``None`` while nothing has happened."""
    match progress:
        case Idle():
            return None
        case Queued():
            return "Queued", "#45beef"
        case Downloading():
            return "Downloading", "#45beef"
        case Unmatched():
            return "Not matched", "#e67e22"
        case Downloaded():
            return "Downloaded", "#45beef"
        case DownloadFailed():
            return "Download failed", "#e74c3c"
        case Installed():
            return "Installed", "#27ae60"
        case InstallCancelled():
            return "Install cancelled", "#e67e22"
        case _:
            assert_never(progress)


def status_text(entry: UpdateEntry) -> str:
    look = _progress_look(entry.progress)
    return look[0] if look else finding_label(entry.finding)


def status_color(entry: UpdateEntry) -> str:
    look = _progress_look(entry.progress)
    return look[1] if look else _finding_look(entry.finding)[1]


def archive_of(progress: Progress) -> Optional[str]:
    match progress:
        case Downloaded(archive=archive) | Installed(archive=archive):
            return archive
        case InstallCancelled(archive=archive):
            return archive
        case Idle() | Queued() | Downloading() | Unmatched() | DownloadFailed():
            return None
        case _:
            assert_never(progress)


def file_label(mod_file: Optional[ModFile], fallback_file_id: Optional[int] = None) -> str:
    if mod_file is not None:
        # Any of these may be missing when the source omits them.
        name = mod_file.name or f"File ID {mod_file.file_id}"
        category = "?" if mod_file.category is FileCategory.UNKNOWN else mod_file.category.name
        return " ".join(part for part in (name, mod_file.version, f"[{category}]") if part)
    if fallback_file_id:
        return f"File ID {fallback_file_id}"
    return "—"


def file_tooltip(mod_file: Optional[ModFile]) -> str:
    if mod_file is None:
        return ""
    group = mod_file.group_id if mod_file.group_id is not None else "unknown"
    return f"File ID {mod_file.file_id}\nGroup {group}"


def scan_failed_message(event: ScanFailed) -> str:
    skipped = f" {event.unchecked} mod(s) were not checked." if event.unchecked else ""
    if event.reason == StopReason.INCOMPATIBLE:
        return (
            "Scan stopped: the Nexus v2 API (beta) no longer answers the way this plugin "
            f"expects, so the plugin likely needs an update.{skipped}\n{event.message}"
        )
    return (
        f"Scan stopped: Nexus is not responding. Try again later.{skipped}\n{event.message}"
    )


def mod_tooltip(entry: UpdateEntry) -> str:
    return (
        f"Nexus mod {entry.mod_id}\nMO2 version: {entry.version or '?'}"
        f"\nMO2 newest version: {entry.newest_version or '?'}"
    )
