from typing import Any, Optional, Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QBrush, QColor

from ..app.session import (
    EntryKey,
    Offered,
    UpdateEntry,
    UpToDate,
    downloadable,
    installed_of,
)
from ..app.sync import Sync
from . import text

COL_CHECK, COL_LOCK, COL_MOD, COL_INSTALLED, COL_TARGET, COL_STATUS, COL_DETAILS = range(7)
HEADERS = ["", text.LOCK_HEADER, "Mod", "Installed file", "Update to", "Status", "Details"]
SELECT_ALL_TOOLTIP = "Select / deselect all updates"


class UpdatesModel(QAbstractTableModel):
    """The updater's rows as a table, read from a ``Sync`` and edited through it.

    The owner forwards ``Sync``'s ``RowsAdded`` and ``RowChanged`` notifications to
    ``rows_added`` and ``row_changed``; only the rows announced so far are visible.
    """

    def __init__(self, sync: Sync, parent: Optional[Any] = None):
        super().__init__(parent)
        self._sync = sync
        self._count = 0

    def entry_at(self, row: int) -> UpdateEntry:
        return self._sync.entry(self._sync.rows()[row])

    def rows_added(self, keys: Sequence[EntryKey]) -> None:
        self.beginInsertRows(QModelIndex(), self._count, self._count + len(keys) - 1)
        self._count += len(keys)
        self.endInsertRows()

    def row_changed(self, key: EntryKey) -> None:
        row = self._sync.row_of(key)
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(HEADERS) - 1))

    # --- QAbstractTableModel ---

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._count

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        column = index.column()
        if column == COL_LOCK:
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        if column == COL_CHECK:
            flags = Qt.ItemFlag.ItemIsEnabled
            if downloadable(self.entry_at(index.row())) is not None:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            return flags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        entry = self.entry_at(index.row())
        column = index.column()
        if column == COL_CHECK:
            if role == Qt.ItemDataRole.CheckStateRole:
                return _check_state(self._sync.is_selected(entry.key))
            return None
        if column == COL_LOCK:
            if role == Qt.ItemDataRole.CheckStateRole:
                return _check_state(entry.locked)
            if role == Qt.ItemDataRole.ToolTipRole:
                return text.LOCK_TOOLTIP
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return _display(entry, column)
        if role == Qt.ItemDataRole.ToolTipRole:
            return _tooltip(entry, column)
        if role == Qt.ItemDataRole.ForegroundRole:
            return _foreground(entry, column)
        return None

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.CheckStateRole:
            return False
        entry = self.entry_at(index.row())
        checked = Qt.CheckState(value) == Qt.CheckState.Checked
        if index.column() == COL_CHECK:
            self._sync.set_selected(entry.key, checked)
            return True
        if index.column() == COL_LOCK:
            self._sync.set_locked(entry.mod_id, checked)
            return True
        return False

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return {COL_CHECK: SELECT_ALL_TOOLTIP, COL_LOCK: text.LOCK_TOOLTIP}.get(section)
        return None


class HideCurrentProxy(QSortFilterProxyModel):
    """Optionally hides the rows whose mod is already up to date."""

    def __init__(self, model: UpdatesModel, parent: Optional[Any] = None):
        super().__init__(parent)
        self._model = model
        self._hide = True
        self.setSourceModel(model)

    def set_hide_current(self, hide: bool) -> None:
        self._hide = hide
        self.invalidateFilter()

    def entry_of(self, index: QModelIndex) -> Optional[UpdateEntry]:
        if not index.isValid():
            return None
        return self._model.entry_at(self.mapToSource(index).row())

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        return not (self._hide and isinstance(self._model.entry_at(source_row).finding, UpToDate))


def _check_state(checked: bool) -> Qt.CheckState:
    return Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked


def _display(entry: UpdateEntry, column: int) -> str:
    if column == COL_MOD:
        return entry.mod_name
    if column == COL_INSTALLED:
        installed = installed_of(entry.finding)
        file_id = installed.file_id if installed else None
        return text.file_label(installed.file if installed else None, file_id)
    if column == COL_TARGET:
        return text.file_label(_target(entry))
    if column == COL_STATUS:
        return text.status_text(entry)
    return text.finding_detail(entry.finding)


def _tooltip(entry: UpdateEntry, column: int) -> Optional[str]:
    if column == COL_MOD:
        return text.mod_tooltip(entry)
    if column == COL_INSTALLED:
        installed = installed_of(entry.finding)
        return text.file_tooltip(installed.file if installed else None)
    if column == COL_TARGET:
        return text.file_tooltip(_target(entry))
    if column == COL_DETAILS:
        detail = text.finding_detail(entry.finding)
        archive = text.archive_of(entry.progress)
        if archive:
            return detail + "\nArchive: " + archive
        return detail
    return None


def _foreground(entry: UpdateEntry, column: int) -> Optional[QBrush]:
    if entry.locked:
        return QBrush(QColor(text.LOCKED_COLOR))
    if column == COL_STATUS:
        return QBrush(QColor(text.status_color(entry)))
    return None


def _target(entry: UpdateEntry):
    return entry.finding.target if isinstance(entry.finding, Offered) else None
