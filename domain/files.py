from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FileCategory(Enum):
    MAIN = "main"
    UPDATE = "update"
    OPTIONAL = "optional"
    MISCELLANEOUS = "miscellaneous"
    OLD_VERSION = "old_version"
    ARCHIVED = "archived"
    REMOVED = "removed"
    # The source reported a category this plugin doesn't recognise (or none).
    UNKNOWN = "unknown"


# Categories still offered for download on a mod's files tab.
ACTIVE_CATEGORIES = frozenset(
    {FileCategory.MAIN, FileCategory.UPDATE, FileCategory.OPTIONAL, FileCategory.MISCELLANEOUS}
)


@dataclass(frozen=True)
class ModFile:
    """One uploaded file of a Nexus mod.

    Everything except ``file_id`` may be unknown: the data comes from a beta API,
    and the selection policy treats unknowns conservatively instead of guessing.
    """

    file_id: int
    name: str = ""
    version: str = ""
    category: FileCategory = FileCategory.UNKNOWN
    is_primary: bool = False
    # Successive uploads of the same logical file share a group, even across renames.
    group_id: Optional[int] = None
    uploaded_at: Optional[int] = None  # Unix timestamp

    @property
    def is_active(self) -> bool:
        return self.category in ACTIVE_CATEGORIES

    @property
    def is_fully_known(self) -> bool:
        return self.category is not FileCategory.UNKNOWN and self.uploaded_at is not None
