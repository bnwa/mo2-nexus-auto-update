"""Contract test against the real Nexus v2 API. Not part of the gate.

Run with ``NEXUS_LIVE_TESTS=1`` set; needs network access.
"""

import os
import unittest

from nexus_auto_updater.app.ports import GameRef, SourceIncompatible
from nexus_auto_updater.nexus.catalog import NexusV2FileSource
from nexus_auto_updater.nexus.graphql import GraphQLClient

SKYUI = 12604


@unittest.skipUnless(os.environ.get("NEXUS_LIVE_TESTS") == "1", "set NEXUS_LIVE_TESTS=1 to query the live API")
class NexusV2LiveTest(unittest.TestCase):
    def setUp(self):
        self.source = NexusV2FileSource(GraphQLClient())
        # nexus_game_id=0 exercises domain -> ID resolution.
        self.game = GameRef(0, "skyrimspecialedition")

    def test_list_files_parses(self):
        try:
            listing = self.source.list_files(self.game, SKYUI)
        except SourceIncompatible as exc:
            self.fail(f"Nexus v2 contract changed: {exc}")
        self.assertTrue(listing.files)
        self.assertTrue(all(f.file_id > 0 for f in listing.files))
        self.assertTrue(any(f.is_primary for f in listing.files))
        self.assertTrue(listing.archive_matchers)

    def test_mod_description_parses(self):
        try:
            description = self.source.mod_description(self.game, SKYUI)
        except SourceIncompatible as exc:
            self.fail(f"Nexus v2 contract changed: {exc}")
        self.assertIn("SkyUI", description)


if __name__ == "__main__":
    unittest.main()
