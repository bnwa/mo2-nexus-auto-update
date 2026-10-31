"""Match finished archives to expected downloads.

Pure Python: reading archive metadata and scheduling retries are supplied by the
caller, so this module knows nothing of MO2, Qt or Nexus naming schemes.
"""

import ntpath
from dataclasses import dataclass
from itertools import count
from typing import Callable, Dict, Optional, Sequence, Tuple

Ticket = int
# Returns (mod_id, file_id) recorded for an archive; zero when unknown.
ReadArchiveIds = Callable[[str], Tuple[int, int]]
Schedule = Callable[[int, Callable[[], None]], None]
# (ticket, archive_path, succeeded)
ResolvedListener = Callable[[Ticket, str, bool], None]

# Waits before re-reading an archive's metadata in case the host has not written
# its IDs yet (about 1.75 s in total).
DEFAULT_RETRY_DELAYS_MS = (250, 500, 1000)


@dataclass(frozen=True)
class _Expectation:
    mod_id: int
    file_id: int
    name_matches: Callable[[str], bool]


class DownloadTracker:
    def __init__(
        self,
        read_archive_ids: ReadArchiveIds,
        schedule: Schedule,
        retry_delays_ms: Sequence[int] = DEFAULT_RETRY_DELAYS_MS,
    ):
        self._read_archive_ids = read_archive_ids
        self._schedule = schedule
        self._retry_delays_ms = tuple(retry_delays_ms)
        self._expected: Dict[Ticket, _Expectation] = {}
        self._tickets = count(1)
        self._listener: Optional[ResolvedListener] = None

    def set_listener(self, listener: Optional[ResolvedListener]) -> None:
        self._listener = listener

    def expect(self, mod_id: int, file_id: int, name_matches: Callable[[str], bool]) -> Ticket:
        ticket = next(self._tickets)
        self._expected[ticket] = _Expectation(mod_id, file_id, name_matches)
        return ticket

    def cancel(self, ticket: Ticket) -> None:
        self._expected.pop(ticket, None)

    def download_finished(self, archive_path: str, succeeded: bool) -> None:
        self._resolve(archive_path, succeeded, attempt=0)

    def _resolve(self, archive_path: str, succeeded: bool, attempt: int) -> None:
        if not self._expected:
            return
        ticket, worth_retrying = self._match(archive_path)
        if ticket is None:
            if worth_retrying and attempt < len(self._retry_delays_ms):
                self._schedule(
                    self._retry_delays_ms[attempt],
                    lambda: self._resolve(archive_path, succeeded, attempt + 1),
                )
            return
        del self._expected[ticket]
        if self._listener is not None:
            self._listener(ticket, archive_path, succeeded)

    def _match(self, archive_path: str) -> Tuple[Optional[Ticket], bool]:
        """Return ``(ticket, worth_retrying)``; retry when metadata may be pending."""
        mod_id, file_id = self._read_archive_ids(archive_path)

        # 1. The metadata names a specific file: authoritative either way.
        if file_id > 0:
            for ticket, expected in self._expected.items():
                if expected.file_id == file_id and mod_id in (0, expected.mod_id):
                    return ticket, False
            return None, False

        # 2. The archive name identifies the file.
        file_name = ntpath.basename(archive_path)
        for ticket, expected in self._expected.items():
            if expected.name_matches(file_name):
                return ticket, False

        # 3. The metadata only knows the mod: accept when exactly one expectation fits.
        if mod_id > 0:
            candidates = [t for t, e in self._expected.items() if e.mod_id == mod_id]
            if len(candidates) == 1:
                return candidates[0], False
            # Several files of this mod are expected: wait for the file ID.
            return None, bool(candidates)

        # No usable metadata yet (or not a download we can identify): look again.
        return None, True
