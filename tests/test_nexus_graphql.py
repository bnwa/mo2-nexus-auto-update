import io
import json
import socket
import unittest
import urllib.error
from unittest import mock

from nexus_auto_updater.nexus import graphql
from nexus_auto_updater.nexus.graphql import (
    _SCHEMA_ERROR_CODES,
    API_URL,
    USER_AGENT,
    GraphQLClient,
    RequestError,
    SchemaError,
    TransientError,
)

# Shapes as returned by the live v2 API.
UNDEFINED_FIELD = {
    "errors": [
        {
            "message": "Field 'foo' doesn't exist on type 'ModFile'",
            "locations": [{"line": 3, "column": 5}],
            "path": ["query ModFiles", "modFiles", "foo"],
            "extensions": {"code": "undefinedField", "typeName": "ModFile", "fieldName": "foo"},
        }
    ]
}
GAME_NOT_FOUND = {
    "errors": [
        {
            "message": "Game not found",
            "locations": [{"line": 2, "column": 3}],
            "path": ["game"],
            "extensions": {"code": "GAME_NOT_FOUND"},
        }
    ],
    "data": {"game": None},
}
MOD_FILES = {
    "data": {
        "modFiles": [
            {
                "fileId": 1000172397,
                "name": "SkyUI",
                "version": "5.2SE",
                "category": "MAIN",
                "primary": 1,
                "groupId": 1000172397,
                "date": 1700000000,
                "uri": "SkyUI_5_2_SE-12604-5-2SE.7z",
                "sqid": "Q2aN3bT",
                "description": "Elements are [b]better[/b].",
                "changelogText": ["Fixed the map."],
            }
        ]
    }
}


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def respond(payload) -> FakeResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return FakeResponse(body)


def http_error(code: int, body: bytes = b"", reason: str = "Reason") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(API_URL, code, reason, {}, io.BytesIO(body))


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(graphql.urllib.request, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = GraphQLClient(min_interval_s=0)

    def query_with(self, result):
        if isinstance(result, BaseException):
            self.urlopen.side_effect = result
        else:
            self.urlopen.return_value = respond(result)
        return self.client.query("query { x }", {"modId": "1"})


class RequestTest(ClientTestCase):
    def test_request_shape(self):
        client = GraphQLClient(min_interval_s=0, timeout_s=7.5)
        self.urlopen.return_value = respond(MOD_FILES)
        data = client.query("query ModFiles { x }", {"modId": "12604", "gameId": "1704"})
        self.assertEqual(MOD_FILES["data"], data)
        (request,), kwargs = self.urlopen.call_args
        self.assertEqual({"timeout": 7.5}, kwargs)
        self.assertEqual("POST", request.get_method())
        self.assertEqual(API_URL, request.full_url)
        self.assertEqual("https://api.nexusmods.com/v2/graphql", request.full_url)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual("application/json", headers["content-type"])
        self.assertEqual("application/json", headers["accept"])
        self.assertEqual(USER_AGENT, headers["user-agent"])
        self.assertEqual(
            {"query": "query ModFiles { x }", "variables": {"modId": "12604", "gameId": "1704"}},
            json.loads(request.data.decode("utf-8")),
        )

    def test_default_timeout(self):
        self.query_with({"data": {}})
        self.assertEqual({"timeout": 20.0}, self.urlopen.call_args.kwargs)

    def test_data_returned_alongside_empty_errors(self):
        self.assertEqual({"a": 1}, self.query_with({"data": {"a": 1}, "errors": []}))


class HttpErrorTest(ClientTestCase):
    def test_rate_limit_and_server_errors_are_transient(self):
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                with self.assertRaises(TransientError) as ctx:
                    self.query_with(http_error(code, b"Slow down"))
                self.assertEqual(f"HTTP {code}: Slow down", str(ctx.exception))

    def test_other_4xx_is_a_schema_error_with_the_body(self):
        for code in (400, 401, 403, 404, 405):
            with self.subTest(code=code):
                with self.assertRaises(SchemaError) as ctx:
                    self.query_with(http_error(code, b'{"error":"Not Found"}'))
                self.assertIn('{"error":"Not Found"}', str(ctx.exception))
                self.assertIn(f"HTTP {code}", str(ctx.exception))

    def test_empty_body_uses_the_reason_and_long_bodies_are_truncated(self):
        with self.assertRaises(SchemaError) as ctx:
            self.query_with(http_error(404, b"", reason="Not Found"))
        self.assertEqual("HTTP 404: Not Found", str(ctx.exception))
        with self.assertRaises(SchemaError) as ctx:
            self.query_with(http_error(400, b"x" * 1000))
        self.assertEqual("HTTP 400: " + "x" * 300, str(ctx.exception))

    def test_undecodable_body_is_replaced(self):
        with self.assertRaises(SchemaError) as ctx:
            self.query_with(http_error(400, b"\xff\xfebad"))
        self.assertIn("bad", str(ctx.exception))

    def test_network_failures_are_transient(self):
        errors = [
            urllib.error.URLError(ConnectionRefusedError(10061, "No connection could be made")),
            TimeoutError("timed out"),
            socket.timeout("timed out"),
            ConnectionResetError(10054, "An existing connection was forcibly closed"),
            OSError("[WinError 10051] network unreachable"),
        ]
        for error in errors:
            with self.subTest(error=error):
                with self.assertRaises(TransientError) as ctx:
                    self.query_with(error)
                self.assertTrue(str(ctx.exception).startswith("Network error: "))
                self.assertIs(error, ctx.exception.__cause__)


class PayloadTest(ClientTestCase):
    def test_non_json_is_a_schema_error(self):
        for body in (b"<html>Bad gateway</html>", b"\xff\xfe", b""):
            with self.subTest(body=body):
                with self.assertRaises(SchemaError):
                    self.query_with(body)

    def test_non_object_payload_is_a_schema_error(self):
        for payload in ([], [{"data": {}}], "text", 3, None):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(SchemaError, "not a JSON object"):
                    self.query_with(payload)

    def test_missing_or_non_dict_data_is_a_schema_error(self):
        for payload in ({}, {"data": None}, {"data": []}, {"data": "x"}, {"errors": None}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(SchemaError, "no data"):
                    self.query_with(payload)

    def test_non_list_errors_is_a_schema_error(self):
        for errors in ({"message": "x"}, "boom", 5):
            with self.subTest(errors=errors):
                with self.assertRaisesRegex(SchemaError, "Unexpected errors payload"):
                    self.query_with({"errors": errors, "data": {}})


class GraphQLErrorTest(ClientTestCase):
    def test_live_undefined_field_is_a_schema_error(self):
        with self.assertRaises(SchemaError) as ctx:
            self.query_with(UNDEFINED_FIELD)
        self.assertEqual(
            "GraphQL schema mismatch: Field 'foo' doesn't exist on type 'ModFile'", str(ctx.exception)
        )

    def test_every_schema_code_is_a_schema_error(self):
        self.assertIn("undefinedField", _SCHEMA_ERROR_CODES)
        for code in sorted(_SCHEMA_ERROR_CODES):
            with self.subTest(code=code):
                payload = {"errors": [{"message": "m", "extensions": {"code": code}}]}
                with self.assertRaises(SchemaError):
                    self.query_with(payload)

    def test_schema_code_anywhere_wins(self):
        payload = {"errors": [{"message": "a", "extensions": {"code": "INVALID"}},
                              {"message": "b", "extensions": {"code": "undefinedType"}}]}
        with self.assertRaisesRegex(SchemaError, "a; b"):
            self.query_with(payload)

    def test_live_game_not_found_is_a_request_error(self):
        with self.assertRaises(RequestError) as ctx:
            self.query_with(GAME_NOT_FOUND)
        self.assertEqual(["GAME_NOT_FOUND"], ctx.exception.codes)
        self.assertEqual("GraphQL error: Game not found", str(ctx.exception))

    def test_other_codes_and_no_code_are_request_errors(self):
        cases = {
            "INVALID": ({"message": "Invalid ID", "extensions": {"code": "INVALID"}}, ["INVALID"]),
            "no extensions": ({"message": "Oops"}, []),
            "empty code": ({"message": "Oops", "extensions": {"code": ""}}, []),
            "non-dict extensions": ({"message": "Oops", "extensions": ["code"]}, []),
        }
        for label, (error, codes) in cases.items():
            with self.subTest(label):
                with self.assertRaises(RequestError) as ctx:
                    self.query_with({"errors": [error], "data": None})
                self.assertEqual(codes, ctx.exception.codes)

    def test_non_dict_error_entries(self):
        with self.assertRaises(RequestError) as ctx:
            self.query_with({"errors": ["plain failure", 42, {"extensions": {"code": "X"}}]})
        self.assertEqual(
            "GraphQL error: plain failure; 42; {'extensions': {'code': 'X'}}", str(ctx.exception)
        )
        self.assertEqual(["X"], ctx.exception.codes)


class PacingTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(graphql.urllib.request, "urlopen", return_value=respond({"data": {}}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 1000.0
        self.sleeps = []
        for name, fake in (("monotonic", lambda: self.now), ("sleep", self._sleep)):
            patcher = mock.patch.object(graphql.time, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def test_sleeps_only_the_remaining_interval(self):
        client = GraphQLClient(min_interval_s=0.25)
        client.query("q", {})
        self.assertEqual([], self.sleeps)
        self.now += 0.1
        client.query("q", {})
        self.assertEqual(1, len(self.sleeps))
        self.assertAlmostEqual(0.15, self.sleeps[0])
        self.now += 0.3
        client.query("q", {})
        self.assertEqual(1, len(self.sleeps))

    def test_negative_interval_is_clamped(self):
        client = GraphQLClient(min_interval_s=-5)
        self.assertEqual(0.0, client._min_interval_s)
        for _ in range(3):
            client.query("q", {})
        self.assertEqual([], self.sleeps)


if __name__ == "__main__":
    unittest.main()
