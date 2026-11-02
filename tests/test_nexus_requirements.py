import unittest

from nexus_auto_updater.app.ports import GameRef, SourceIncompatible, SourceUnavailable
from nexus_auto_updater.domain.requirements import ModRequirements
from nexus_auto_updater.nexus.catalog import NexusV2FileSource
from nexus_auto_updater.nexus.graphql import RequestError, SchemaError, TransientError, _raise_for_errors

GAME = GameRef(1704, "skyrimspecialedition")


class FakeClient:
    def __init__(self, *results):
        self.results = list(results)
        self.queries = []

    def query(self, query, variables):
        self.queries.append((query, variables))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def req(mod_id, game_id="1704", external=False):
    return {"gameId": str(game_id), "modId": str(mod_id), "externalRequirement": external}


def node(mod_id, *entries, total=None):
    return {
        "modId": mod_id,
        "modRequirements": {
            "nexusRequirements": {
                "totalCount": len(entries) if total is None else total,
                "nodes": list(entries),
            }
        },
    }


def page(*nodes):
    return {"legacyModsByDomain": {"nodes": list(nodes)}}


def fetch(*results, ids=(1,)):
    client = FakeClient(*results)
    return NexusV2FileSource(client).mod_requirements(GAME, list(ids)), client


class RequestTest(unittest.TestCase):
    def test_batch_request_shape(self):
        _, client = fetch(page(node(2)), ids=(2, 0, 2))
        ((query, variables),) = client.queries
        self.assertEqual(
            {"ids": [{"gameDomain": "skyrimspecialedition", "modId": 2}], "count": 1}, variables
        )
        self.assertIn("legacyModsByDomain(ids: $ids, count: $count)", query)
        self.assertIn("nexusRequirements(offset: 0, count: 25)", query)

    def test_nothing_to_look_up_makes_no_request(self):
        result, client = fetch(ids=(0, -3))
        self.assertEqual({0: None, -3: None}, result)
        self.assertEqual([], client.queries)

    def test_large_inputs_are_chunked_and_failures_keep_earlier_chunks(self):
        ids = list(range(1, 121))  # 3 chunks of at most 50
        result, client = fetch(
            page(node(1, req(7)), node(50)), TransientError("down"), ids=ids
        )
        self.assertEqual(2, len(client.queries))
        self.assertEqual(ModRequirements(frozenset({7}), True), result[1])
        self.assertEqual(ModRequirements(frozenset(), True), result[50])
        self.assertIsNone(result[51])
        self.assertIsNone(result[120])

    def test_failure_of_the_first_chunk_raises(self):
        with self.assertRaises(SourceUnavailable):
            fetch(TransientError("down"))
        with self.assertRaises(SourceIncompatible):
            fetch(SchemaError("gone"))


class ParsingTest(unittest.TestCase):
    def one(self, *entries, total=None):
        result, _ = fetch(page(node(1, *entries, total=total)))
        return result[1]

    def test_declared_requirements(self):
        self.assertEqual(
            ModRequirements(frozenset({30379, 32444}), True), self.one(req(30379), req(32444))
        )

    def test_none_declared_is_known_and_empty(self):
        self.assertEqual(ModRequirements(frozenset(), True), self.one())

    def test_external_requirements_are_skipped_whatever_their_game(self):
        found = self.one(req(30379), req(0, 0, True), req(0, 110, True), req(5, 1704, True))
        self.assertEqual(ModRequirements(frozenset({30379}), True), found)

    def test_zero_or_missing_mod_id_is_not_a_node(self):
        found = self.one(req(0), {"gameId": "1704", "externalRequirement": False})
        self.assertEqual(ModRequirements(frozenset(), True), found)

    def test_other_game_is_dropped_and_marks_incomplete(self):
        self.assertEqual(
            ModRequirements(frozenset({7}), False), self.one(req(7), req(8, 110))
        )

    def test_short_page_is_incomplete(self):
        self.assertEqual(ModRequirements(frozenset({7}), False), self.one(req(7), total=30))

    def test_missing_total_is_incomplete(self):
        data = node(1, req(7))
        del data["modRequirements"]["nexusRequirements"]["totalCount"]
        result, _ = fetch(page(data))
        self.assertEqual(ModRequirements(frozenset({7}), False), result[1])

    def test_unreadable_entries_are_dropped_and_mark_incomplete(self):
        bad_flag = {"gameId": "1704", "modId": "9", "externalRequirement": None}
        found = self.one(req(7), "junk", bad_flag)
        self.assertEqual(ModRequirements(frozenset({7}), False), found)

    def test_unreadable_requirements_leave_the_mod_unknown(self):
        for data in (
            {"modId": 1},
            {"modId": 1, "modRequirements": None},
            {"modId": 1, "modRequirements": {"nexusRequirements": {"nodes": "x"}}},
        ):
            result, _ = fetch(page(data))
            self.assertIsNone(result[1], data)

    def test_nodes_are_matched_by_id_not_position(self):
        result, _ = fetch(page(node(2, req(7)), node(1, req(8))), ids=(1, 2))
        self.assertEqual({8}, result[1].mod_ids)
        self.assertEqual({7}, result[2].mod_ids)

    def test_omitted_unrequested_and_malformed_nodes(self):
        result, _ = fetch(page(node(99, req(7)), "junk"), ids=(1, 2))
        self.assertEqual({1: None, 2: None}, result)
        result, _ = fetch(page({"modId": "x"}), ids=(1, 2))
        self.assertEqual({1: None, 2: None}, result)

    def test_page_contract_violations_are_incompatible(self):
        for data in (
            {"legacyModsByDomain": None},
            {"legacyModsByDomain": {"nodes": None}},
            page(node(1), node(2)),  # more nodes than requested
        ):
            with self.assertRaises(SourceIncompatible, msg=data):
                fetch(data)

    def test_request_errors_become_source_errors(self):
        from nexus_auto_updater.app.ports import SourceError

        with self.assertRaises(SourceError):
            fetch(RequestError("nope", ["X"]))


class QueryTooComplexTest(unittest.TestCase):
    def test_is_classified_as_a_schema_level_error(self):
        with self.assertRaises(SchemaError):
            _raise_for_errors([{"message": "too complex", "extensions": {"code": "QUERY_TOO_COMPLEX"}}])


if __name__ == "__main__":
    unittest.main()
