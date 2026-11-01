"""Read a mod's page on the file source, so the user can judge an update."""

import threading
from typing import Dict

from .ports import GameRef, ModFileSource


class ModNotes:
    def __init__(self, source: ModFileSource, game: GameRef):
        self._source = source
        self._game = game
        self._descriptions: Dict[int, str] = {}
        self._lock = threading.Lock()

    def page_url(self, mod_id: int) -> str:
        return self._source.mod_page_url(self._game, mod_id)

    def description(self, mod_id: int) -> str:
        """Blocking and cached; raises ``SourceError`` subclasses on failure."""
        with self._lock:
            cached = self._descriptions.get(mod_id)
        if cached is not None:
            return cached
        description = self._source.mod_description(self._game, mod_id)
        with self._lock:
            self._descriptions[mod_id] = description
        return description
