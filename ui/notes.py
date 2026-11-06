"""Pane showing the author's notes for the selected row: file descriptions,
changelogs and the mod page, so the user can judge an update before taking it."""

import html
import re
import threading
import time
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QTextBrowser

from ..app.notes import ModNotes
from ..app.ports import SourceError
from ..app.session import Offered, Skipped, UpdateEntry, installed_of
from ..domain.files import ModFile
from . import text


class NotesPane(QTextBrowser):
    def __init__(self, notes: ModNotes, parent=None):
        super().__init__(parent)
        self._notes = notes
        self._entry: Optional[UpdateEntry] = None
        # mod_id -> (description, error). Errors are dropped on reselection to retry.
        self._pages: Dict[int, Tuple[str, str]] = {}
        self._loading: Optional[int] = None
        self._wanted: Optional[int] = None
        self._bridge = _Bridge(self)
        self._bridge.loaded.connect(self._on_loaded)
        self.setOpenExternalLinks(True)
        self.setPlaceholderText("Select a row to read the mod author's notes.")

    def show_entry(self, entry: Optional[UpdateEntry]) -> None:
        self._entry = entry
        if entry is not None and not isinstance(entry.finding, Skipped):
            if self._pages.get(entry.mod_id, ("", ""))[1]:
                del self._pages[entry.mod_id]
            if entry.mod_id not in self._pages:
                self._wanted = entry.mod_id
                self._load_next()
        self._render()

    def _load_next(self) -> None:
        mod_id = self._wanted
        if self._loading is not None or mod_id is None or mod_id in self._pages:
            return
        self._wanted = None
        self._loading = mod_id
        # A daemon thread rather than a QThread: a slow request must not hold up
        # closing the dialog, and a result arriving after that is simply dropped.
        threading.Thread(target=self._fetch, args=(mod_id,), daemon=True).start()

    def _fetch(self, mod_id: int) -> None:
        try:
            result = (self._notes.description(mod_id), "")
        except SourceError as exc:
            result = ("", str(exc) or "Nexus did not answer.")
        try:
            self._bridge.loaded.emit(mod_id, *result)
        except RuntimeError:
            pass  # The dialog was closed meanwhile.

    def _on_loaded(self, mod_id: int, description: str, error: str) -> None:
        self._pages[mod_id] = (description, error)
        self._loading = None
        if self._entry is not None and self._entry.mod_id == mod_id:
            self._render()
        self._load_next()

    def _render(self) -> None:
        entry = self._entry
        if entry is None:
            self.clear()
            return
        parts = [f"<h3>{html.escape(entry.mod_name)}</h3>"]
        if isinstance(entry.finding, Skipped):
            parts.append(f"<p>{html.escape(entry.finding.reason)}</p>")
            self.setHtml("".join(parts))
            return

        url = html.escape(self._notes.page_url(entry.mod_id))
        parts.append(f'<p><a href="{url}">Open the mod page on Nexus</a></p>')
        installed = installed_of(entry.finding)
        if installed is not None and installed.newer_files:
            parts.append("<h4>Newer files</h4>")
            target_id = entry.finding.target.file_id if isinstance(entry.finding, Offered) else None
            for mod_file in installed.newer_files:
                parts.append(_file_html(mod_file, mod_file.file_id == target_id))
        if installed is not None and installed.file is not None:
            parts.append("<h4>Installed file</h4>")
            parts.append(_file_html(installed.file, False))

        parts.append("<h4>Mod page</h4>")
        page = self._pages.get(entry.mod_id)
        if page is None:
            parts.append("<p><i>Loading the mod page from Nexus…</i></p>")
        elif page[1]:
            parts.append(f"<p><i>Could not load the mod page: {html.escape(page[1])}</i></p>")
        else:
            parts.append(f"<p>{markup_to_html(page[0]) or '<i>No description.</i>'}</p>")
        self.setHtml("".join(parts))


class _Bridge(QObject):
    """Carries results from the fetch thread to the GUI thread."""

    loaded = pyqtSignal(int, str, str)  # mod_id, description, error


def _file_html(mod_file: ModFile, is_target: bool) -> str:
    heading = f"<b>{html.escape(text.file_label(mod_file))}</b>"
    if mod_file.uploaded_at:
        uploaded = time.strftime("%Y-%m-%d", time.localtime(mod_file.uploaded_at))
        heading += f" — uploaded {uploaded}"
    if is_target:
        heading += " — <b>proposed update</b>"
    parts = [f"<p>{heading}</p>"]
    description = markup_to_html(mod_file.description)
    parts.append(f"<p>{description}</p>" if description else "<p><i>No description.</i></p>")
    if mod_file.changelog:
        items = "".join(f"<li>{markup_to_html(line)}</li>" for line in mod_file.changelog)
        parts.append(f"<p>Changelog:</p><ul>{items}</ul>")
    return "".join(parts)


# Nexus notes mix BBCode with literal HTML line breaks. Everything is escaped and
# only a few harmless constructs are rebuilt, so author content can't inject markup.
_BR = re.compile(r"\r?\n?<br\s*/?>\r?\n?", re.IGNORECASE)
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_IMG = re.compile(r"\[img[^\]]*\].*?\[/img\]", re.IGNORECASE | re.DOTALL)
_URL = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.IGNORECASE | re.DOTALL)
_BARE_URL = re.compile(r"\[url\](.*?)\[/url\]", re.IGNORECASE | re.DOTALL)
_STYLES = [
    (re.compile(rf"\[{tag}\](.*?)\[/{tag}\]", re.IGNORECASE | re.DOTALL), rf"<{tag}>\1</{tag}>")
    for tag in ("b", "i", "u", "s")
]
_LIST_ITEM = re.compile(r"\[\*\]")
_OTHER_TAG = re.compile(
    r"\[/?(?:center|right|left|color|size|font|quote|spoiler|list|line|heading|youtube|code"
    r"|b|i|u|s|img|url)(?:=[^\]]*)?\]",
    re.IGNORECASE,
)
_BLANK_LINES = re.compile(r"\n{3,}")


def markup_to_html(markup: str) -> str:
    plain = html.unescape(_HTML_TAG.sub("", _BR.sub("\n", markup)))
    result = html.escape(plain)
    result = _IMG.sub("", result)
    result = _URL.sub(lambda m: _link(m.group(1), m.group(2)), result)
    result = _BARE_URL.sub(lambda m: _link(m.group(1), m.group(1)), result)
    for pattern, replacement in _STYLES:
        result = pattern.sub(replacement, result)
    result = _LIST_ITEM.sub("• ", result)
    result = _OTHER_TAG.sub("", result)
    result = _BLANK_LINES.sub("\n\n", result.replace("\r", ""))
    return result.strip().replace("\n", "<br>")


def _link(escaped_url: str, label: str) -> str:
    url = html.unescape(escaped_url).strip().strip("'\"")
    if not url.lower().startswith(("http://", "https://")):
        return label
    return f'<a href="{html.escape(url)}">{label}</a>'
