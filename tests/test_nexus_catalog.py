import unittest

from nexus_auto_updater.app.ports import GameRef, SourceError, SourceIncompatible, SourceUnavailable
from nexus_auto_updater.domain.files import FileCategory, ModFile
from nexus_auto_updater.nexus.archives import ArchiveFingerprint
from nexus_auto_updater.nexus.catalog import NexusV2FileSource, _int
from nexus_auto_updater.nexus.graphql import GraphQLError, RequestError, SchemaError, TransientError

GAME = GameRef(1704, "skyrimspecialedition")
UNKNOWN_ID_GAME = GameRef(0, "skyrimspecialedition")

SKYUI = {
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
    "changelogText": ["Fixed the map.", "", "   ", None, 5, "Faster menus."],
}


class FakeClient:
    """GraphQLClient stand-in: scripted results/exceptions, recorded queries."""

    def __init__(self, *results):
        self.results = list(results)
        self.queries = []

    def query(self, query, variables):
        self.queries.append((query, variables))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def files_data(*items):
    return {"modFiles": list(items)}


class ListFilesMappingTest(unittest.TestCase):
    def test_maps_every_field(self):
        client = FakeClient(files_data(SKYUI))
        listing = NexusV2FileSource(client).list_files(GAME, 12604)
        self.assertEqual(
            (
                ModFile(
                    file_id=1000172397,
                    name="SkyUI",
                    version="5.2SE",
                    category=FileCategory.MAIN,
                    is_primary=True,
                    group_id=1000172397,
                    uploaded_at=1700000000,
                    description="Elements are [b]better[/b].",
                    changelog=("Fixed the map.", "Faster menus."),
                ),
            ),
            listing.files,
        )
        self.assertEqual(
            ArchiveFingerprint("SkyUI_5_2_SE-12604-5-2SE.7z", "Q2aN3bT"), listing.archive_matcher(1000172397)
        )
        ((query, variables),) = client.queries
        self.assertEqual({"modId": "12604", "gameId": "1704"}, variables)
        self.assertIn("modFiles(modId: $modId, gameId: $gameId)", query)
        self.assertIn("fileId name version category primary groupId date uri sqid description changelogText", query)

    def test_category_spellings(self):
        cases = {
            "MAIN": FileCategory.MAIN, "main": FileCategory.MAIN, "Update": FileCategory.UPDATE,
            "OPTIONAL": FileCategory.OPTIONAL, "miscellaneous": FileCategory.MISCELLANEOUS,
            "OLD_VERSION": FileCategory.OLD_VERSION, "archived": FileCategory.ARCHIVED,
            "REMOVED": FileCategory.REMOVED, "DELETED": FileCategory.UNKNOWN,
            "OLD VERSION": FileCategory.UNKNOWN, "": FileCategory.UNKNOWN, None: FileCategory.UNKNOWN,
        }
        for spelling, category in cases.items():
            with self.subTest(spelling=spelling):
                listing = NexusV2FileSource(FakeClient(files_data({"fileId": 1, "category": spelling}))).list_files(GAME, 1)
                self.assertIs(category, listing.files[0].category)

    def test_minimal_entry_degrades_to_unknown(self):
        listing = NexusV2FileSource(FakeClient(files_data({"fileId": "77"}))).list_files(GAME, 1)
        self.assertEqual((ModFile(77),), listing.files)
        self.assertEqual({}, dict(listing.archive_matchers))

    def test_optional_field_edge_cases(self):
        item = {"fileId": 5, "primary": 0, "groupId": "abc", "date": 0, "name": None, "version": 2,
                "description": None, "changelogText": "not a list"}
        (mod_file,) = NexusV2FileSource(FakeClient(files_data(item))).list_files(GAME, 1).files
        self.assertFalse(mod_file.is_primary)
        self.assertIsNone(mod_file.group_id)
        self.assertIsNone(mod_file.uploaded_at)
        self.assertEqual(("", "2", "", ()), (mod_file.name, mod_file.version, mod_file.description, mod_file.changelog))

    def test_int_edge_cases(self):
        cases = [(None, None), ("", None), (True, None), (False, None), ("abc", None), ([], None),
                 ({}, None), ("12", 12), (12, 12), ("-3", -3), (7.9, 7), ("1.5", None), (0, 0)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, _int(value))

    def test_empty_listing(self):
        listing = NexusV2FileSource(FakeClient(files_data())).list_files(GAME, 1)
        self.assertEqual((), listing.files)

    def test_contract_violations_are_incompatible(self):
        cases = {
            "missing fileId": files_data({"name": "x"}),
            "zero fileId": files_data({"fileId": 0}),
            "garbage fileId": files_data({"fileId": "abc"}),
            "boolean fileId": files_data({"fileId": True}),
            "non-dict entry": files_data("SkyUI"),
            "null modFiles": {"modFiles": None},
            "missing modFiles": {},
            "dict modFiles": {"modFiles": {"fileId": 1}},
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(SourceIncompatible):
                    NexusV2FileSource(FakeClient(data)).list_files(GAME, 1)

    def test_matchers_only_with_a_legacy_name_or_sqid(self):
        items = [
            {"fileId": 1, "uri": "Foo-1-1-0-1700000000.7z", "sqid": None},
            {"fileId": 2, "uri": "https://cf-files.nexusmods.com/cdn/1704/1/abc", "sqid": "Xy7"},
            {"fileId": 3, "uri": "cdn/1704/3/opaque", "sqid": ""},
            {"fileId": 4},
        ]
        listing = NexusV2FileSource(FakeClient(files_data(*items))).list_files(GAME, 1)
        self.assertEqual({1: ArchiveFingerprint("Foo-1-1-0-1700000000.7z", ""), 2: ArchiveFingerprint("", "Xy7")},
                         dict(listing.archive_matchers))


class ErrorMappingTest(unittest.TestCase):
    def test_transport_errors_map_to_port_errors(self):
        cases = [
            (TransientError("HTTP 503"), SourceUnavailable),
            (RequestError("GraphQL error: Mod not found", ["NOT_FOUND"]), SourceError),
            (GraphQLError("other"), SourceError),
        ]
        for error, expected in cases:
            with self.subTest(error=error):
                with self.assertRaises(expected) as ctx:
                    NexusV2FileSource(FakeClient(error)).list_files(GAME, 1)
                self.assertIs(error, ctx.exception.__cause__)
                if expected is SourceError:
                    self.assertNotIsInstance(ctx.exception, (SourceUnavailable, SourceIncompatible))
                    self.assertEqual(str(error), str(ctx.exception))

    def test_messages(self):
        with self.assertRaisesRegex(SourceUnavailable, "^Nexus v2 API unavailable: HTTP 503$"):
            NexusV2FileSource(FakeClient(TransientError("HTTP 503"))).list_files(GAME, 1)
        with self.assertRaisesRegex(SourceIncompatible, "^Nexus v2 API changed: gone$"):
            NexusV2FileSource(FakeClient(SchemaError("gone"), SchemaError("gone"))).list_files(GAME, 1)


class NoteFieldsFallbackTest(unittest.TestCase):
    def test_schema_error_retries_once_without_note_fields_for_good(self):
        plain = dict(SKYUI)
        del plain["description"], plain["changelogText"]
        client = FakeClient(SchemaError("Field 'changelogText' doesn't exist"), files_data(plain), files_data(plain))
        source = NexusV2FileSource(client)
        first = source.list_files(GAME, 12604)
        self.assertEqual(1000172397, first.files[0].file_id)
        self.assertEqual("", first.files[0].description)
        source.list_files(GAME, 3863)
        queries = [q for q, _ in client.queries]
        self.assertIn("description changelogText", queries[0])
        self.assertNotIn("description", queries[1])
        self.assertNotIn("changelogText", queries[2])
        self.assertEqual({"modId": "12604", "gameId": "1704"}, client.queries[1][1])

    def test_schema_error_without_note_fields_raises(self):
        client = FakeClient(SchemaError("a"), SchemaError("Field 'sqid' doesn't exist"))
        source = NexusV2FileSource(client)
        with self.assertRaisesRegex(SourceIncompatible, "sqid"):
            source.list_files(GAME, 1)
        self.assertEqual(2, len(client.queries))
        client.results.append(SchemaError("still"))
        with self.assertRaises(SourceIncompatible):
            source.list_files(GAME, 1)
        self.assertEqual(3, len(client.queries))  # No more retries once the notes are dropped.

    def test_other_errors_do_not_drop_note_fields(self):
        client = FakeClient(TransientError("x"), files_data())
        source = NexusV2FileSource(client)
        with self.assertRaises(SourceUnavailable):
            source.list_files(GAME, 1)
        source.list_files(GAME, 1)
        self.assertIn("changelogText", client.queries[1][0])


class GameIdTest(unittest.TestCase):
    def test_known_game_id_is_used_directly(self):
        client = FakeClient(files_data())
        NexusV2FileSource(client).list_files(GameRef(1704, "ignored"), 1)
        self.assertEqual(1, len(client.queries))

    def test_game_id_is_resolved_by_domain_and_cached(self):
        client = FakeClient({"game": {"id": "1704"}}, files_data(), files_data(), {"mod": {"description": "d"}})
        source = NexusV2FileSource(client)
        source.list_files(UNKNOWN_ID_GAME, 1)
        source.list_files(UNKNOWN_ID_GAME, 2)
        source.mod_description(UNKNOWN_ID_GAME, 3)
        game_query, game_vars = client.queries[0]
        self.assertIn("game(domainName: $domainName) { id }", game_query)
        self.assertEqual({"domainName": "skyrimspecialedition"}, game_vars)
        self.assertEqual(["1704", "1704", "1704"], [v["gameId"] for _, v in client.queries[1:]])
        self.assertEqual(4, len(client.queries))

    def test_resolution_is_per_domain(self):
        client = FakeClient({"game": {"id": 1704}}, files_data(), {"game": {"id": 110}}, files_data())
        source = NexusV2FileSource(client)
        source.list_files(UNKNOWN_ID_GAME, 1)
        source.list_files(GameRef(0, "skyrim"), 1)
        self.assertEqual("110", client.queries[3][1]["gameId"])

    def test_unresolvable_game_is_incompatible(self):
        cases = {
            "GAME_NOT_FOUND": RequestError("GraphQL error: Game not found", ["GAME_NOT_FOUND"]),
            "schema": SchemaError("game gone"),
            "other": GraphQLError("odd"),
            "null game": {"game": None},
            "no game": {},
            "non-dict game": {"game": [1704]},
            "no id": {"game": {}},
            "empty id": {"game": {"id": ""}},
            "zero id": {"game": {"id": 0}},
            "garbage id": {"game": {"id": "sse"}},
        }
        for label, result in cases.items():
            with self.subTest(label):
                client = FakeClient(result)
                with self.assertRaises(SourceIncompatible) as ctx:
                    NexusV2FileSource(client).list_files(UNKNOWN_ID_GAME, 1)
                self.assertIn("skyrimspecialedition", str(ctx.exception))
                self.assertEqual(1, len(client.queries))

    def test_unavailable_during_resolution_stays_unavailable_and_is_not_cached(self):
        client = FakeClient(TransientError("down"), {"game": {"id": 1704}}, files_data())
        source = NexusV2FileSource(client)
        with self.assertRaises(SourceUnavailable):
            source.list_files(UNKNOWN_ID_GAME, 1)
        source.list_files(UNKNOWN_ID_GAME, 1)
        self.assertEqual("1704", client.queries[2][1]["gameId"])


class ModPageTest(unittest.TestCase):
    def test_mod_description(self):
        client = FakeClient({"mod": {"description": "[center]SkyUI[/center]"}})
        self.assertEqual("[center]SkyUI[/center]", NexusV2FileSource(client).mod_description(GAME, 12604))
        query, variables = client.queries[0]
        self.assertIn("mod(modId: $modId, gameId: $gameId) { description }", query)
        self.assertEqual({"modId": "12604", "gameId": "1704"}, variables)

    def test_mod_description_degrades(self):
        for data in ({"mod": None}, {}, {"mod": "x"}, {"mod": {}}, {"mod": {"description": None}}):
            with self.subTest(data=data):
                self.assertEqual("", NexusV2FileSource(FakeClient(data)).mod_description(GAME, 1))

    def test_missing_mod_is_a_source_error(self):
        client = FakeClient(RequestError("GraphQL error: Mod not found", ["NOT_FOUND"]))
        with self.assertRaises(SourceError) as ctx:
            NexusV2FileSource(client).mod_description(GAME, 1)
        self.assertNotIsInstance(ctx.exception, (SourceUnavailable, SourceIncompatible))

    def test_mod_page_url_makes_no_request(self):
        client = FakeClient()
        url = NexusV2FileSource(client).mod_page_url(UNKNOWN_ID_GAME, 12604)
        self.assertEqual("https://www.nexusmods.com/skyrimspecialedition/mods/12604", url)
        self.assertEqual([], client.queries)


if __name__ == "__main__":
    unittest.main()
