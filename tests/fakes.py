"""Fake ports and builders shared by the application-level tests."""

import threading
from typing import Callable, Dict, List, Optional, Tuple

from nexus_auto_updater.app.ports import FileListing, GameRef, InstalledMod
from nexus_auto_updater.domain.files import FileCategory, ModFile

GAME = GameRef(1704, "skyrimspecialedition")
T0 = 1_700_000_000  # A realistic Nexus upload timestamp.


def mod_file(
    file_id: int,
    *,
    group_id: Optional[int] = 10,
    uploaded_at: Optional[int] = None,
    category: FileCategory = FileCategory.MAIN,
    **kwargs,
) -> ModFile:
    """A fully known file; ``uploaded_at`` defaults to ``T0 + file_id``."""
    if uploaded_at is None and "unknown_time" not in kwargs:
        uploaded_at = T0 + file_id
    kwargs.pop("unknown_time", None)
    return ModFile(file_id, group_id=group_id, uploaded_at=uploaded_at, category=category, **kwargs)


def installed_mod(
    mod_id: int = 100,
    file_ids: Tuple[int, ...] = (1,),
    *,
    name: str = "",
    version: str = "1.0",
    newest_version: str = "2.0",
    blocker: str = "",
    in_managed_game: bool = True,
) -> InstalledMod:
    return InstalledMod(
        name=name or f"Mod {mod_id}",
        mod_id=mod_id,
        installed_file_ids=file_ids,
        version=version,
        newest_version=newest_version,
        download_blocker=blocker,
        in_managed_game=in_managed_game,
    )


class FakeMatcher:
    def __init__(self, *names: str):
        self.names = set(names)

    def matches(self, file_name: str) -> bool:
        return file_name in self.names


class FakeSource:
    """ModFileSource with scripted results: a value, an exception, or a list of them."""

    def __init__(self, listings=None, descriptions=None):
        self.listings: Dict[int, object] = dict(listings or {})
        self.descriptions: Dict[int, object] = dict(descriptions or {})
        self.list_calls: List[Tuple[GameRef, int]] = []
        self.description_calls: List[Tuple[GameRef, int]] = []
        self.before_list: Optional[Callable[[int], None]] = None

    @staticmethod
    def _next(script: Dict[int, object], mod_id: int):
        result = script[mod_id]
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def list_files(self, game: GameRef, mod_id: int) -> FileListing:
        self.list_calls.append((game, mod_id))
        if self.before_list is not None:
            self.before_list(mod_id)
        return self._next(self.listings, mod_id)

    def mod_page_url(self, game: GameRef, mod_id: int) -> str:
        return f"https://www.nexusmods.com/{game.nexus_domain}/mods/{mod_id}"

    def mod_description(self, game: GameRef, mod_id: int) -> str:
        self.description_calls.append((game, mod_id))
        return self._next(self.descriptions, mod_id)


class FakeDownloader:
    def __init__(self):
        self.calls: List[Tuple[int, int]] = []

    def start_download(self, mod_id: int, file_id: int) -> None:
        self.calls.append((mod_id, file_id))


class FakeInstaller:
    def __init__(self, result: bool = True):
        self.result = result
        self.calls: List[Tuple[str, str]] = []

    def install_archive(self, archive_path: str, mod_name: str) -> bool:
        self.calls.append((archive_path, mod_name))
        return self.result


class FakeLocks:
    def __init__(self, *locked: int):
        self.locked = set(locked)
        self.set_calls: List[Tuple[int, bool]] = []

    def is_locked(self, mod_id: int) -> bool:
        return mod_id in self.locked

    def set_locked(self, mod_id: int, locked: bool) -> None:
        self.set_calls.append((mod_id, locked))
        (self.locked.add if locked else self.locked.discard)(mod_id)


class ManualScheduler:
    """Captures the tracker's retries and expiries so tests fire them explicitly."""

    def __init__(self):
        self.pending: List[Tuple[int, Callable[[], None]]] = []
        self.delays: List[int] = []

    def __call__(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.delays.append(delay_ms)
        self.pending.append((delay_ms, callback))

    def fire_next(self) -> None:
        _, callback = self.pending.pop(0)
        callback()

    def fire(self, delay_ms: int) -> None:
        """Fire, and forget, the pending callbacks scheduled with exactly this delay."""
        due = [callback for delay, callback in self.pending if delay == delay_ms]
        self.pending = [(delay, callback) for delay, callback in self.pending if delay != delay_ms]
        for callback in due:
            callback()

    def fire_all(self) -> None:
        while self.pending:
            self.fire_next()


class ArchiveIds:
    """A scriptable ``read_archive_ids``: path -> (mod_id, file_id), default zeros."""

    def __init__(self, ids: Optional[Dict[str, Tuple[int, int]]] = None):
        self.ids = dict(ids or {})
        self.calls: List[str] = []

    def __call__(self, path: str) -> Tuple[int, int]:
        self.calls.append(path)
        return self.ids.get(path, (0, 0))


class Gate:
    """Blocks a fake call on a worker thread until the test releases it."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def wait(self, timeout: float = 5.0) -> None:
        self.entered.set()
        if not self.release.wait(timeout):
            raise TimeoutError("Gate was never released")


class FakeScanRunner:
    """A ``ScanRunner`` that delivers events when the test says so, on the test's thread."""

    def __init__(self, synchronous: bool = False):
        self.synchronous = synchronous
        self.starts = 0
        self.stopped = False
        self.plan = None
        self.on_event: Callable = lambda event: None
        self.on_finished: Callable = lambda: None

    def start(self, plan, on_event, on_finished) -> None:
        self.starts += 1
        self.plan, self.on_event, self.on_finished = plan, on_event, on_finished
        if self.synchronous:
            self.run_plan()

    def stop(self) -> None:
        self.stopped = True

    def deliver(self, event) -> None:
        self.on_event(event)

    def finish(self) -> None:
        self.on_finished()

    def run_plan(self) -> None:
        for event in self.plan.run(should_stop=lambda: False):
            self.on_event(event)
        self.on_finished()
