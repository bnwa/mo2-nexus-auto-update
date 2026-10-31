"""``ModFileSource`` adapter backed by the Nexus v2 GraphQL API (beta).

Everything specific to v2 (queries, field names, enum spellings, error codes,
numeric game IDs) stays in this package. Responses are parsed defensively: fields
the plugin can live without degrade to "unknown" instead of failing.
"""

import threading
from typing import Any, Dict, Optional

from ..app.ports import (
    FileListing,
    GameRef,
    SourceError,
    SourceIncompatible,
    SourceUnavailable,
)
from ..domain.files import FileCategory, ModFile
from .archives import ArchiveFingerprint
from .graphql import GraphQLClient, GraphQLError, RequestError, SchemaError, TransientError

_MOD_FILES_QUERY = """
query ModFiles($modId: ID!, $gameId: ID!) {
  modFiles(modId: $modId, gameId: $gameId) {
    fileId name version category primary groupId date uri sqid
  }
}
"""

_GAME_QUERY = """
query Game($domainName: String) {
  game(domainName: $domainName) { id }
}
"""

_CATEGORIES = {
    "MAIN": FileCategory.MAIN,
    "UPDATE": FileCategory.UPDATE,
    "OPTIONAL": FileCategory.OPTIONAL,
    "MISCELLANEOUS": FileCategory.MISCELLANEOUS,
    "OLD_VERSION": FileCategory.OLD_VERSION,
    "ARCHIVED": FileCategory.ARCHIVED,
    "REMOVED": FileCategory.REMOVED,
}


class NexusV2FileSource:
    def __init__(self, client: GraphQLClient):
        self._client = client
        self._game_ids: Dict[str, int] = {}
        self._lock = threading.Lock()

    def list_files(self, game: GameRef, mod_id: int) -> FileListing:
        game_id = self._game_id(game)
        data = self._query(_MOD_FILES_QUERY, {"modId": str(mod_id), "gameId": str(game_id)})
        items = data.get("modFiles")
        if not isinstance(items, list):
            raise SourceIncompatible("Nexus v2 'modFiles' did not return a list.")

        files = []
        matchers = {}
        for item in items:
            mod_file = _to_mod_file(item)
            files.append(mod_file)
            fingerprint = ArchiveFingerprint.from_api(_text(item, "uri"), _text(item, "sqid"))
            if fingerprint.legacy_file_name or fingerprint.sqid:
                matchers[mod_file.file_id] = fingerprint
        return FileListing(tuple(files), matchers)

    def _game_id(self, game: GameRef) -> int:
        if game.nexus_game_id > 0:
            return game.nexus_game_id
        with self._lock:
            cached = self._game_ids.get(game.nexus_domain)
        if cached is not None:
            return cached

        try:
            data = self._query(_GAME_QUERY, {"domainName": game.nexus_domain})
        except SourceError as exc:
            if isinstance(exc, SourceUnavailable):
                raise
            # An unknown game fails every mod alike, so stop rather than retry per mod.
            raise SourceIncompatible(f"Could not resolve Nexus game '{game.nexus_domain}': {exc}") from exc
        game_data = data.get("game")
        game_id = _int(game_data.get("id")) if isinstance(game_data, dict) else None
        if not game_id:
            raise SourceIncompatible(f"Nexus v2 returned no ID for game '{game.nexus_domain}'.")
        with self._lock:
            self._game_ids[game.nexus_domain] = game_id
        return game_id

    def _query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._client.query(query, variables)
        except TransientError as exc:
            raise SourceUnavailable(f"Nexus v2 API unavailable: {exc}") from exc
        except SchemaError as exc:
            raise SourceIncompatible(f"Nexus v2 API changed: {exc}") from exc
        except RequestError as exc:
            raise SourceError(str(exc)) from exc
        except GraphQLError as exc:
            raise SourceError(str(exc)) from exc


def _to_mod_file(item: Any) -> ModFile:
    if not isinstance(item, dict):
        raise SourceIncompatible("Nexus v2 returned a file entry that is not an object.")
    file_id = _int(item.get("fileId"))
    if not file_id:
        raise SourceIncompatible("Nexus v2 returned a file without a usable 'fileId'.")
    return ModFile(
        file_id=file_id,
        name=_text(item, "name"),
        version=_text(item, "version"),
        category=_CATEGORIES.get(_text(item, "category").upper(), FileCategory.UNKNOWN),
        is_primary=bool(item.get("primary")),
        group_id=_int(item.get("groupId")),
        uploaded_at=_int(item.get("date")) or None,
    )


def _text(item: Dict[str, Any], key: str) -> str:
    value = item.get(key)
    return str(value) if value is not None else ""


def _int(value: Any) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
