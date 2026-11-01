import unittest

from nexus_auto_updater.app.notes import ModNotes
from nexus_auto_updater.app.ports import SourceError, SourceUnavailable
from tests.fakes import GAME, FakeSource


class ModNotesTest(unittest.TestCase):
    def test_page_url_delegates_to_the_source(self):
        notes = ModNotes(FakeSource(), GAME)
        self.assertEqual("https://www.nexusmods.com/skyrimspecialedition/mods/12604", notes.page_url(12604))

    def test_description_is_cached_per_mod(self):
        source = FakeSource(descriptions={1: "[b]One[/b]", 2: ""})
        notes = ModNotes(source, GAME)
        self.assertEqual("[b]One[/b]", notes.description(1))
        self.assertEqual("[b]One[/b]", notes.description(1))
        self.assertEqual("", notes.description(2))
        self.assertEqual("", notes.description(2))  # Empty descriptions are cached too.
        self.assertEqual([(GAME, 1), (GAME, 2)], source.description_calls)

    def test_errors_propagate_and_are_not_cached(self):
        for error in (SourceError("missing"), SourceUnavailable("down")):
            with self.subTest(error=error):
                source = FakeSource(descriptions={1: [error, "found"]})
                notes = ModNotes(source, GAME)
                with self.assertRaises(type(error)):
                    notes.description(1)
                self.assertEqual("found", notes.description(1))
                self.assertEqual(2, len(source.description_calls))


if __name__ == "__main__":
    unittest.main()
