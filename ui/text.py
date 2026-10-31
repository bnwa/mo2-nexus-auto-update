"""Display text and colours for application states."""

from typing import Optional

from ..app.scan import ScanFailed, StopReason
from ..app.session import EntryStatus, Phase, UpdateEntry
from ..domain.files import FileCategory, ModFile

STATUS_TEXT = {
    EntryStatus.READY: "Ready",
    EntryStatus.REVIEW: "Needs review",
    EntryStatus.UP_TO_DATE: "Up to date",
    EntryStatus.UNRESOLVED: "Unresolved",
    EntryStatus.SKIPPED: "Skipped",
    EntryStatus.ERROR: "Error",
}
STATUS_COLORS = {
    EntryStatus.READY: "#27ae60",
    EntryStatus.REVIEW: "#e67e22",
    EntryStatus.UNRESOLVED: "#c0392b",
    EntryStatus.ERROR: "#e74c3c",
    EntryStatus.SKIPPED: "#7f8c8d",
    EntryStatus.UP_TO_DATE: "#7f8c8d",
}
PHASE_TEXT = {
    Phase.QUEUED: "Queued",
    Phase.DOWNLOADING: "Downloading",
    Phase.DOWNLOADED: "Downloaded",
    Phase.DOWNLOAD_FAILED: "Download failed",
    Phase.INSTALLED: "Installed",
    Phase.INSTALL_CANCELLED: "Install cancelled",
}
PHASE_COLORS = {
    Phase.QUEUED: "#45beef",
    Phase.DOWNLOADING: "#45beef",
    Phase.DOWNLOADED: "#45beef",
    Phase.DOWNLOAD_FAILED: "#e74c3c",
    Phase.INSTALLED: "#27ae60",
    Phase.INSTALL_CANCELLED: "#e67e22",
}


def status_text(entry: UpdateEntry) -> str:
    return PHASE_TEXT.get(entry.phase) or STATUS_TEXT[entry.status]


def status_color(entry: UpdateEntry) -> str:
    return PHASE_COLORS.get(entry.phase) or STATUS_COLORS[entry.status]


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
