import threading
import time
import unittest
from dataclasses import replace
from unittest import mock

from nexus_auto_updater.app.ports import SourceError, SourceUnavailable
from nexus_auto_updater.app.session import (
    InstalledFile,
    Offered,
    Skipped,
    UpdateEntry,
    Unresolved,
)
from nexus_auto_updater.domain.files import FileCategory, ModFile
from tests import HAS_QT, QtTestCase, wait_until
from tests.fakes import T0, Gate

if HAS_QT:
    from nexus_auto_updater.ui.notes import NotesPane, markup_to_html


class FakeModNotes:
    """ModNotes stand-in: scripted descriptions per mod, optional gates per call."""

    def __init__(self, descriptions=None):
        self.descriptions = dict(descriptions or {})
        self.gates = {}  # mod_id -> Gate, consumed by the next call
        self.calls = []
        self._lock = threading.Lock()

    def page_url(self, mod_id):
        return f"https://www.nexusmods.com/skyrimspecialedition/mods/{mod_id}?a=1&b=2"

    def description(self, mod_id):
        with self._lock:
            self.calls.append(mod_id)
            gate = self.gates.pop(mod_id, None)
        if gate is not None:
            gate.wait()
        result = self.descriptions.get(mod_id, "")
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def entry(mod_id=1, *, finding=None, installed=None, target=None, newer_files=(), **kwargs) -> UpdateEntry:
    if finding is None:
        installed_file = InstalledFile(None, installed, newer_files)
        finding = (
            Offered(installed_file, target, False, "why")
            if target is not None
            else Unresolved(installed_file, "why")
        )
    defaults = dict(
        key=mod_id, mod_name=f"Mod {mod_id}", mod_id=mod_id, version="1", newest_version="2",
        finding=finding,
    )
    defaults.update(kwargs)
    return UpdateEntry(**defaults)


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable")
class MarkupToHtmlTest(unittest.TestCase):
    def test_script_tags_are_removed(self):
        result = markup_to_html("Hi<script>alert(1)</script> there")
        self.assertNotIn("<script", result.lower())
        self.assertEqual("Hialert(1) there", result)

    def test_encoded_tags_stay_text(self):
        self.assertEqual("&lt;script&gt;x&lt;/script&gt;", markup_to_html("&lt;script&gt;x&lt;/script&gt;"))

    def test_entities(self):
        self.assertEqual("Tom &amp; Jerry &lt;3 &quot;q&quot; é", markup_to_html("Tom &amp; Jerry &lt;3 \"q\" &eacute;"))

    def test_links_survive(self):
        self.assertEqual(
            '<a href="https://www.nexusmods.com/skyrimspecialedition/mods/12604">SkyUI</a>',
            markup_to_html("[url=https://www.nexusmods.com/skyrimspecialedition/mods/12604]SkyUI[/url]"),
        )
        self.assertEqual('<a href="http://a.b/c?x=1&amp;y=2">http://a.b/c?x=1&amp;y=2</a>',
                         markup_to_html("[url]http://a.b/c?x=1&y=2[/url]"))
        self.assertEqual('<a href="https://q.com">q</a>', markup_to_html("[URL='https://q.com']q[/URL]"))
        self.assertEqual('<a href="https://q.com">q</a>', markup_to_html('[url="https://q.com"]q[/url]'))

    def test_javascript_and_other_schemes_lose_the_link(self):
        for markup in ("[url=javascript:alert(1)]click[/url]", "[url= JavaScript:alert(1)]click[/url]",
                       "[url=data:text/html,x]click[/url]", "[url=//evil.example]click[/url]"):
            with self.subTest(markup=markup):
                self.assertEqual("click", markup_to_html(markup))
        self.assertEqual("javascript:alert(1)", markup_to_html("[url]javascript:alert(1)[/url]"))

    def test_attribute_breaking_urls_are_neutralised(self):
        result = markup_to_html('[url=https://x.com/"onmouseover="alert(1)]x[/url]')
        self.assertEqual('<a href="https://x.com/&quot;onmouseover=&quot;alert(1)">x</a>', result)
        for markup in ('[url=https://x.com/"><img src=x onerror=alert(1)>]x[/url]',
                       '[url]https://x.com/"><img src=x onerror=alert(1)>[/url]',
                       "[url=https://x.com/'><svg onload=alert(1)>]x[/url]"):
            with self.subTest(markup=markup):
                result = markup_to_html(markup)
                self.assertNotIn("<img", result)
                self.assertNotIn("<svg", result)
                self.assertNotRegex(result, r'href="[^"]*"[^>]*on\w+=')

    def test_styles_list_items_and_line_breaks(self):
        self.assertEqual("<b>b</b><i>i</i><u>u</u><s>s</s><b>B</b>",
                         markup_to_html("[b]b[/b][i]i[/i][u]u[/u][s]s[/s][B]B[/B]"))
        self.assertEqual("• one<br>• two", markup_to_html("[list]\n[*]one\n[*]two\n[/list]"))
        self.assertEqual("a<br>b<br>c<br>d<br>e", markup_to_html("a<br>b<br/>c<BR />d\r\n<br>\r\ne"))
        self.assertEqual("a<br>b", markup_to_html("a\r\nb"))

    def test_images_removed_and_other_tags_stripped(self):
        self.assertEqual("before after", markup_to_html("before [img]https://x/y.png[/img]after"))
        self.assertEqual("x", markup_to_html("[img width=50]https://x/y.png[/img]x"))
        markup = ("[center][color=#ff0000][size=5][font=Arial]Hi[/font][/size][/color][/center]"
                  "[quote][spoiler][code]c[/code][/spoiler][/quote][line][heading]h[/heading]"
                  "[youtube]id[/youtube][left]l[/left][right]r[/right]")
        self.assertEqual("Hichidlr", markup_to_html(markup))
        self.assertEqual("[unknown]x[/unknown]", markup_to_html("[unknown]x[/unknown]"))
        self.assertEqual("unclosed", markup_to_html("[b]unclosed"))

    def test_html_tags_stripped(self):
        self.assertEqual("bold link", markup_to_html('<b>bold</b> <a href="javascript:x">link</a>'))

    def test_blank_lines_collapse_and_whitespace_is_trimmed(self):
        self.assertEqual("a<br><br>b", markup_to_html("a\n\n\n\n\nb"))
        self.assertEqual("a<br><br>b", markup_to_html(" \n a\n\nb \n "))
        self.assertEqual("", markup_to_html("  \n [img]x[/img] \n"))
        self.assertEqual("", markup_to_html(""))


class NotesPaneTest(QtTestCase):
    def setUp(self):
        self.notes = FakeModNotes()
        self.pane = NotesPane(self.notes)
        self.addCleanup(self.pane.deleteLater)

    def wait_loaded(self, mod_id):
        wait_until(lambda: mod_id in self.pane._pages and self.pane._loading is None)

    def text(self):
        return self.pane.toPlainText()

    def test_placeholder_and_link_settings(self):
        self.assertEqual("Select a row to read the mod author's notes.", self.pane.placeholderText())
        self.assertTrue(self.pane.openExternalLinks())

    def test_loading_then_description(self):
        gate = Gate()
        self.notes.gates[1] = gate
        self.notes.descriptions[1] = "[b]Great[/b] mod"
        self.pane.show_entry(entry(1))
        self.assertTrue(gate.entered.wait(5))
        self.assertIn("Loading the mod page from Nexus…", self.text())
        self.assertIn("Mod 1", self.text())
        self.assertIn('href="https://www.nexusmods.com/skyrimspecialedition/mods/1?a=1&amp;b=2"', self.pane.toHtml())
        gate.release.set()
        self.wait_loaded(1)
        self.assertIn("Great mod", self.text())
        self.assertNotIn("Loading", self.text())

    def test_empty_description(self):
        self.pane.show_entry(entry(1))
        self.wait_loaded(1)
        self.assertIn("Mod page\nNo description.", self.text())

    def test_failed_load_is_retried_on_reselection_and_success_is_cached(self):
        self.notes.descriptions[1] = [SourceUnavailable("Nexus v2 API unavailable: HTTP 503"), "Recovered"]
        self.pane.show_entry(entry(1))
        self.wait_loaded(1)
        self.assertIn("Could not load the mod page: Nexus v2 API unavailable: HTTP 503", self.text())
        self.pane.show_entry(entry(1))
        self.wait_loaded(1)
        self.assertIn("Recovered", self.text())
        self.pane.show_entry(entry(1))
        self.pane.show_entry(None)
        self.pane.show_entry(entry(1))
        time.sleep(0.05)
        wait_until(lambda: True)
        self.assertEqual([1, 1], self.notes.calls)
        self.assertIn("Recovered", self.text())

    def test_error_without_message(self):
        self.notes.descriptions[1] = SourceError()
        self.pane.show_entry(entry(1))
        self.wait_loaded(1)
        self.assertIn("Could not load the mod page: Nexus did not answer.", self.text())

    def test_only_the_latest_selection_is_fetched_while_loading(self):
        gate = Gate()
        self.notes.gates[1] = gate
        self.notes.descriptions.update({1: "one", 2: "two", 3: "three"})
        self.pane.show_entry(entry(1))
        self.assertTrue(gate.entered.wait(5))
        self.pane.show_entry(entry(2))
        self.pane.show_entry(entry(3))
        gate.release.set()
        self.wait_loaded(3)
        self.assertEqual([1, 3], self.notes.calls)
        self.assertIn("three", self.text())
        self.assertNotIn(2, self.pane._pages)

    def test_result_for_a_deselected_mod_is_cached_and_does_not_disturb_the_view(self):
        first, second = Gate(), Gate()
        self.notes.gates.update({1: first, 2: second})
        self.notes.descriptions.update({1: "one", 2: "two"})
        self.pane.show_entry(entry(1))
        self.assertTrue(first.entered.wait(5))
        self.pane.show_entry(entry(2))
        first.release.set()
        # The pane starts fetching mod 2 only once mod 1's page has arrived.
        wait_until(second.entered.is_set)
        self.assertNotIn("one", self.text())
        self.assertIn("Mod 2", self.text())
        second.release.set()
        self.wait_loaded(2)
        self.assertIn("two", self.text())
        self.pane.show_entry(entry(1))
        self.assertIn("one", self.text())
        self.assertEqual([1, 2], self.notes.calls)

    def test_skipped_entries_make_no_request(self):
        skipped = entry(5, finding=Skipped("Other <game>"), mod_name="<LE> Mod")
        self.pane.show_entry(skipped)
        wait_until(lambda: True)
        self.assertEqual([], self.notes.calls)
        self.assertEqual("<LE> Mod\nOther <game>", self.text())
        self.assertNotIn("nexusmods.com", self.pane.toHtml())

    def test_none_clears(self):
        self.pane.show_entry(entry(1))
        self.wait_loaded(1)
        self.pane.show_entry(None)
        self.assertEqual("", self.text())
        self.pane.show_entry(None)
        self.assertEqual("", self.text())

    def test_files_are_listed_newest_first_with_the_target_marked(self):
        newest = ModFile(3, name="Newest", version="3.0", category=FileCategory.UPDATE, uploaded_at=T0 + 3000,
                         description="Third", changelog=("Fixed [b]crash[/b]", "Faster"))
        target = ModFile(2, name="Target", version="2.0", category=FileCategory.MAIN, uploaded_at=T0 + 2000,
                         description="Second <script>x</script>")
        undated = ModFile(4, name="Undated", category=FileCategory.UNKNOWN)
        installed = ModFile(1, name="Installed", version="1.0", category=FileCategory.MAIN, uploaded_at=T0)
        self.pane.show_entry(entry(1, installed=installed, target=target, newer_files=(newest, target, undated)))
        self.wait_loaded(1)
        plain = self.text()
        day = lambda ts: time.strftime("%Y-%m-%d", time.localtime(ts))  # noqa: E731
        expected = [
            "Newer files",
            f"Newest 3.0 [UPDATE] — uploaded {day(T0 + 3000)}",
            "Third",
            "Changelog:",
            "crash",
            "Faster",
            f"Target 2.0 [MAIN] — uploaded {day(T0 + 2000)} — proposed update",
            "Second x",
            "Undated [?]",
            "No description.",
            "Installed file",
            f"Installed 1.0 [MAIN] — uploaded {day(T0)}",
            "Mod page",
        ]
        position = 0
        for fragment in expected:
            found = plain.find(fragment, position)
            self.assertGreaterEqual(found, 0, f"{fragment!r} missing after position {position}:\n{plain}")
            position = found + len(fragment)
        self.assertEqual(1, plain.count("proposed update"))
        self.assertNotIn("<script", self.pane.toHtml())

    def test_no_newer_files_and_no_installed_file(self):
        self.pane.show_entry(entry(1))
        self.wait_loaded(1)
        self.assertNotIn("Newer files", self.text())
        self.assertNotIn("Installed file", self.text())

    def test_newer_files_without_target(self):
        undated = ModFile(4, name="Undated", category=FileCategory.UNKNOWN)
        self.pane.show_entry(entry(1, newer_files=(undated,)))
        self.wait_loaded(1)
        self.assertNotIn("proposed update", self.text())

    def test_result_after_the_bridge_is_gone_is_dropped(self):
        self.pane._bridge = mock.Mock()
        self.pane._bridge.loaded.emit.side_effect = RuntimeError("wrapped C/C++ object has been deleted")
        self.notes.descriptions[1] = "x"
        self.pane._fetch(1)  # Must not raise.
        self.assertEqual([1], self.notes.calls)

    def test_escapes_the_mod_name(self):
        self.pane.show_entry(entry(1, mod_name="<b>Bold</b> & Co"))
        self.wait_loaded(1)
        self.assertTrue(self.text().startswith("<b>Bold</b> & Co"))


if __name__ == "__main__":
    unittest.main()
