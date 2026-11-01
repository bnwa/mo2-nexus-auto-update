"""What the application needs from the outside world.

Adapters (``host`` for MO2, ``nexus`` for the Nexus API) implement these; the
application never imports them. The Nexus v2 API is beta, so everything the app
knows about remote files arrives through ``ModFileSource`` and its error types.
"""

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, Tuple

from ..domain.files import ModFile


@dataclass(frozen=True)
class GameRef:
    nexus_game_id: int  # 0 when the host doesn't know it
    nexus_domain: str


@dataclass(frozen=True)
class InstalledMod:
    """A locally installed mod that came from Nexus."""

    name: str
    mod_id: int
    installed_file_ids: Tuple[int, ...]
    version: str
    newest_version: str
    # Why the host cannot download updates for this mod; empty when it can.
    download_blocker: str = ""


# --- Remote mod files ---


class ArchiveMatcher(Protocol):
    """Recognises the downloaded archive of one specific file by its file name."""

    def matches(self, file_name: str) -> bool: ...


@dataclass(frozen=True)
class FileListing:
    files: Tuple[ModFile, ...]
    # Optional per-file archive recognition; sources may provide none.
    archive_matchers: Mapping[int, ArchiveMatcher] = field(default_factory=dict)

    def archive_matcher(self, file_id: int) -> Optional[ArchiveMatcher]:
        return self.archive_matchers.get(file_id)


class SourceError(Exception):
    """The file source could not answer."""


class SourceUnavailable(SourceError):
    """Transient failure (network, rate limit, outage): retrying later may work."""


class SourceIncompatible(SourceError):
    """The source no longer behaves as this plugin expects; retrying won't help.

    Raised when the API contract appears to have changed, e.g. a queried field
    no longer exists or a response lacks required data.
    """


class ModFileSource(Protocol):
    def list_files(self, game: GameRef, mod_id: int) -> FileListing:
        """All files of a mod. Raises ``SourceError`` subclasses on failure."""
        ...

    def mod_page_url(self, game: GameRef, mod_id: int) -> str:
        """Web page of a mod, for the user to read. Makes no request."""
        ...

    def mod_description(self, game: GameRef, mod_id: int) -> str:
        """The mod page's description, as markup. Raises ``SourceError`` subclasses."""
        ...


# --- Host actions ---


class Downloader(Protocol):
    def start_nexus_download(self, mod_id: int, file_id: int) -> None: ...


class Installer(Protocol):
    def install_archive(self, archive_path: str, mod_name: str) -> bool: ...


class ModLocks(Protocol):
    """Persistent user locks: a locked mod is still checked but never updated."""

    def is_locked(self, mod_id: int) -> bool: ...

    def set_locked(self, mod_id: int, locked: bool) -> None: ...
