"""How Nexus names downloaded archives, and recognising a file from that name."""

import re
from dataclasses import dataclass

# Trailing duplicate-download suffix such as "(1)" after the archive stem.
_DUPLICATE_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")
_ARCHIVE_EXTENSIONS = (".tar.gz", ".7z", ".zip", ".rar", ".tar", ".gz", ".bz2", ".xz")


@dataclass(frozen=True)
class ArchiveFingerprint:
    """What a Nexus archive name reveals about the file it contains.

    Two naming schemes exist:

    * Legacy uploads: ``<name>-<modid>-<version>-<timestamp>.<ext>``, which the v2
      API reports verbatim as the file's ``uri``.
    * Newer uploads: ``<name> <modid> <version> <YYYY-MM-DDTHH-MMZ> <sqid>.<ext>``;
      ``uri`` is then an opaque storage path, and the trailing ``sqid`` is the only
      per-file identifier in the name.
    """

    legacy_file_name: str = ""
    sqid: str = ""

    @classmethod
    def from_api(cls, uri: str, sqid: str) -> "ArchiveFingerprint":
        legacy = uri if uri and "/" not in uri else ""
        return cls(legacy_file_name=legacy, sqid=sqid)

    def matches(self, file_name: str) -> bool:
        name = file_name.strip()
        if self.legacy_file_name and name.lower() == self.legacy_file_name.lower():
            return True
        if not self.sqid:
            return False
        stem = name
        lowered = stem.lower()
        for extension in _ARCHIVE_EXTENSIONS:
            if lowered.endswith(extension):
                stem = stem[: -len(extension)]
                break
        stem = _DUPLICATE_SUFFIX_RE.sub("", stem)
        # sqids are case-sensitive, so compare exactly.
        return stem.rsplit(" ", 1)[-1] == self.sqid
