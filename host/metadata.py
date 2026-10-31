"""MO2's per-mod ``meta.ini`` and per-download ``<archive>.meta`` files.

These are QSettings INI files; only a few flat keys are needed, so they are parsed
with simple line matching rather than configparser (which trips over MO2's long
quoted/escaped values). No mobase dependency.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
_ARRAY_ENTRY_RE = re.compile(
    r"^\s*(?P<index>\d+)\\(?P<key>modid|fileid)\s*=\s*(?P<value>\d+)\s*$", re.IGNORECASE
)
_KEY_VALUE_RE = re.compile(r"^\s*(?P<key>[^=\s]+)\s*=\s*(?P<value>.*?)\s*$")


def _read_lines(path: Path) -> Optional[List[str]]:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return None


def read_installed_files(meta_ini: Path) -> List[Tuple[int, int]]:
    """Return ``(mod_id, file_id)`` pairs from a mod's ``[installedFiles]`` array."""
    lines = _read_lines(meta_ini)
    if lines is None:
        return []

    entries: Dict[int, Dict[str, int]] = {}
    section = ""
    for line in lines:
        header = _SECTION_RE.match(line)
        if header:
            section = header.group("name").strip().lower()
            continue
        if section != "installedfiles":
            continue
        match = _ARRAY_ENTRY_RE.match(line)
        if match:
            entry = entries.setdefault(int(match.group("index")), {})
            entry[match.group("key").lower()] = int(match.group("value"))

    return [
        (entry["modid"], entry["fileid"])
        for _, entry in sorted(entries.items())
        if entry.get("modid", 0) > 0 and entry.get("fileid", 0) > 0
    ]


def read_general_keys(path: Path, keys: Tuple[str, ...]) -> Dict[str, str]:
    """Return selected ``[General]`` values (case-insensitive key match)."""
    lines = _read_lines(path)
    if lines is None:
        return {}

    wanted = {k.lower(): k for k in keys}
    found: Dict[str, str] = {}
    section = "general"  # QSettings writes top-level keys under [General]
    for line in lines:
        header = _SECTION_RE.match(line)
        if header:
            section = header.group("name").strip().lower()
            continue
        if section != "general":
            continue
        match = _KEY_VALUE_RE.match(line)
        if match and match.group("key").lower() in wanted:
            found[wanted[match.group("key").lower()]] = match.group("value").strip('"')
    return found


def read_archive_ids(archive_path: str) -> Tuple[int, int]:
    """Return ``(mod_id, file_id)`` from ``<archive>.meta``; zeros when unknown."""
    values = read_general_keys(Path(archive_path + ".meta"), ("modID", "fileID"))

    def as_int(key: str) -> int:
        try:
            return int(values.get(key, "0"))
        except ValueError:
            return 0

    return as_int("modID"), as_int("fileID")
