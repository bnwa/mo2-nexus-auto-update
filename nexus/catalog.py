"""``ModFileSource`` adapter backed by the Nexus v2 GraphQL API (beta).

Everything specific to v2 (queries, field names, enum spellings, error codes,
numeric game IDs) stays in this package. Responses are parsed defensively: fields
the plugin can live without degrade to "unknown" instead of failing.
"""

import threading
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..app.ports import (
    FileListing,
    GameRef,
    SourceError,
    SourceIncompatible,
    SourceUnavailable,
)
from ..domain.files import FileCategory, ModFile
from ..domain.requirements import ModRequirements
from .archives import ArchiveFingerprint
from .graphql import GraphQLClient, GraphQLError, RequestError, SchemaError, TransientError

_MOD_FILES_QUERY = """
query ModFiles($modId: ID!, $gameId: ID!) {
  modFiles(modId: $modId, gameId: $gameId) {
    fileId name version category primary groupId date uri sqid%s
  }
}
"""
# Only shown to the user: if v2 drops these, the scan goes on without them.
_NOTE_FIELDS = " description changelogText"

_MOD_DESCRIPTION_QUERY = """
query ModDescription($modId: ID!, $gameId: ID!) {
  mod(modId: $modId, gameId: $gameId) { description }
}
"""

# Mods per request: the API silently caps a page at 80 and rejects queries whose
# estimated cost exceeds 10000 (about batch * (5 * requirements + 4) here). The
# batch also sees hidden and removed mods, which a per-mod `mod(...)` lookup can't.
_REQUIREMENTS_BATCH = 50
_REQUIREMENTS_PER_MOD = 25
_MOD_REQUIREMENTS_QUERY = """
query ModRequirements($ids: [CompositeDomainWithIdInput!]!, $count: Int) {
  legacyModsByDomain(ids: $ids, count: $count) {
    nodes {
      modId
      modRequirements {
        nexusRequirements(offset: 0, count: %d) {
          totalCount
          nodes { gameId modId externalRequirement }
        }
      }
    }
  }
}
""" % _REQUIREMENTS_PER_MOD

_MOD_PAGE_URL = "https://www.nexusmods.com/{domain}/mods/{mod_id}"

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
        self._with_notes = True

    def list_files(self, game: GameRef, mod_id: int) -> FileListing:
        game_id = self._game_id(game)
        variables = {"modId": str(mod_id), "gameId": str(game_id)}
        try:
            data = self._query(_MOD_FILES_QUERY % self._note_fields(), variables)
        except SourceIncompatible:
            if not self._note_fields():
                raise
            # Possibly only the note fields are gone; retry without them for good.
            self._with_notes = False
            data = self._query(_MOD_FILES_QUERY % "", variables)
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

    def mod_requirements(
        self, game: GameRef, mod_ids: Sequence[int]
    ) -> Mapping[int, Optional[ModRequirements]]:
        result: Dict[int, Optional[ModRequirements]] = {mod_id: None for mod_id in mod_ids}
        wanted = sorted(mod_id for mod_id in result if mod_id > 0)
        if not wanted:
            return result
        game_id = self._game_id(game)
        for start in range(0, len(wanted), _REQUIREMENTS_BATCH):
            chunk = wanted[start : start + _REQUIREMENTS_BATCH]
            variables = {
                "ids": [{"gameDomain": game.nexus_domain, "modId": mod_id} for mod_id in chunk],
                "count": len(chunk),
            }
            try:
                data = self._query(_MOD_REQUIREMENTS_QUERY, variables)
                result.update(_to_requirements(data, chunk, game_id))
            except SourceError:
                if start == 0:
                    raise
                break  # Keep what was read; the rest stays unknown.
        return result

    def mod_page_url(self, game: GameRef, mod_id: int) -> str:
        return _MOD_PAGE_URL.format(domain=game.nexus_domain, mod_id=mod_id)

    def mod_description(self, game: GameRef, mod_id: int) -> str:
        game_id = self._game_id(game)
        data = self._query(
            _MOD_DESCRIPTION_QUERY, {"modId": str(mod_id), "gameId": str(game_id)}
        )
        mod = data.get("mod")
        return _text(mod, "description") if isinstance(mod, dict) else ""

    def _note_fields(self) -> str:
        with self._lock:
            return _NOTE_FIELDS if self._with_notes else ""

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
        description=_text(item, "description"),
        changelog=_lines(item.get("changelogText")),
    )


def _to_requirements(
    data: Dict[str, Any], requested: Sequence[int], game_id: int
) -> Dict[int, Optional[ModRequirements]]:
    page = data.get("legacyModsByDomain")
    nodes = page.get("nodes") if isinstance(page, dict) else None
    if not isinstance(nodes, list) or len(nodes) > len(requested):
        raise SourceIncompatible("Nexus v2 'legacyModsByDomain' returned an unexpected page.")
    wanted = set(requested)
    parsed: Dict[int, Optional[ModRequirements]] = {}
    for node in nodes:
        # Order is not preserved and unknown mods are omitted, so match by ID.
        mod_id = _int(node.get("modId")) if isinstance(node, dict) else None
        if mod_id in wanted:
            parsed[mod_id] = _declared_requirements(node, game_id)
    return parsed


def _declared_requirements(node: Dict[str, Any], game_id: int) -> Optional[ModRequirements]:
    requirements = node.get("modRequirements")
    page = requirements.get("nexusRequirements") if isinstance(requirements, dict) else None
    if not isinstance(page, dict):
        return None
    entries = page.get("nodes")
    if not isinstance(entries, list):
        return None
    # `nodesCount` always equals `totalCount`, so it can't reveal a short page.
    total = _int(page.get("totalCount"))
    complete = total is not None and len(entries) >= total
    mod_ids = set()
    for entry in entries:
        external = entry.get("externalRequirement") if isinstance(entry, dict) else None
        if not isinstance(external, bool):
            complete = False
            continue
        required_id = _int(entry.get("modId"))
        if external or not required_id:
            continue  # Off-site: not a mod, so not a graph node.
        if _int(entry.get("gameId")) != game_id:
            complete = False  # Another game's mod ID space.
            continue
        mod_ids.add(required_id)
    return ModRequirements(frozenset(mod_ids), complete)


def _text(item: Dict[str, Any], key: str) -> str:
    value = item.get(key)
    return str(value) if value is not None else ""


def _lines(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(line) for line in value if isinstance(line, str) and line.strip())


def _int(value: Any) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
