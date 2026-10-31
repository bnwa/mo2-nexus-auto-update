"""Transport for the public Nexus Mods v2 GraphQL API (beta)."""

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

API_URL = "https://api.nexusmods.com/v2/graphql"
USER_AGENT = "MO2-NexusAutoUpdater/0.1"

# Error codes in a GraphQL error's ``extensions.code`` meaning the query itself no
# longer fits the schema (observed: ``undefinedField``). graphql-ruby also uses the
# others for schema/query mismatches.
_SCHEMA_ERROR_CODES = frozenset(
    {
        "undefinedField",
        "undefinedType",
        "argumentNotAccepted",
        "missingRequiredArguments",
        "argumentLiteralsIncompatible",
        "variableMismatch",
        "selectionMismatch",
        "GRAPHQL_VALIDATION_FAILED",
        "GRAPHQL_PARSE_FAILED",
    }
)


class GraphQLError(Exception):
    """Base class for transport and GraphQL failures."""


class TransientError(GraphQLError):
    """Network trouble, timeouts, rate limiting or server errors."""


class SchemaError(GraphQLError):
    """The query no longer matches the API's schema, or the response is malformed."""


class RequestError(GraphQLError):
    """The API understood the query but rejected it (e.g. ``GAME_NOT_FOUND``)."""

    def __init__(self, message: str, codes: List[str]):
        super().__init__(message)
        self.codes = codes


class GraphQLClient:
    """POSTs GraphQL queries, spacing requests at least ``min_interval_s`` apart."""

    def __init__(self, min_interval_s: float = 0.25, timeout_s: float = 20.0):
        self._min_interval_s = max(0.0, min_interval_s)
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._last_request = 0.0

    def query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        self._pace()
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            message = f"HTTP {exc.code}: {detail or exc.reason}"
            if exc.code == 429 or exc.code >= 500:
                raise TransientError(message) from exc
            # Other 4xx on a fixed endpoint: the endpoint or protocol changed.
            raise SchemaError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientError(f"Network error: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise SchemaError(f"Response is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SchemaError("Response is not a JSON object.")

        errors = payload.get("errors")
        if errors:
            _raise_for_errors(errors)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SchemaError("Response has no data.")
        return data

    def _pace(self) -> None:
        with self._lock:
            wait = self._last_request + self._min_interval_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()


def _raise_for_errors(errors: Any) -> None:
    if not isinstance(errors, list):
        raise SchemaError(f"Unexpected errors payload: {str(errors)[:300]}")
    messages, codes = [], []
    for error in errors:
        if isinstance(error, dict):
            messages.append(str(error.get("message", error)))
            extensions = error.get("extensions")
            if isinstance(extensions, dict) and extensions.get("code"):
                codes.append(str(extensions["code"]))
        else:
            messages.append(str(error))
    message = "; ".join(messages)
    if any(code in _SCHEMA_ERROR_CODES for code in codes):
        raise SchemaError(f"GraphQL schema mismatch: {message}")
    raise RequestError(f"GraphQL error: {message}", codes)
