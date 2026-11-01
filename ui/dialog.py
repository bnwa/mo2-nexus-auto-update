from typing import Dict, List, Optional, Set

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..app.scan import ModScanned, ScanFailed, ScanPlan, ScanProgress
from ..app.session import EntryKey, EntryStatus, Phase, UpdateEntry, UpdateSession
from . import text
from .header import CenteredCheckDelegate, CheckableHeader
from .scan_worker import ScanWorker

COL_CHECK, COL_LOCK, COL_MOD, COL_INSTALLED, COL_TARGET, COL_STATUS, COL_DETAILS = range(7)
HEADERS = ["", text.LOCK_HEADER, "Mod", "Installed file", "Update to", "Status", "Details"]


class UpdaterDialog(QDialog):
    def __init__(
        self,
        parent,
        plan: ScanPlan,
        session: UpdateSession,
        *,
        scope_label: str,
        empty_message: str,
    ):
        super().__init__(parent)
        self._plan = plan
        self._session = session
        self._empty_message = empty_message
        self._worker: Optional[ScanWorker] = None
        self._scanning = False
        self._scan_failed = False
        self._bulk_check = False  # Suppresses per-item header syncs during bulk edits.
        # Table bookkeeping: row index <-> entry, plus the latest snapshot of each.
        self._row_keys: List[EntryKey] = []
        self._row_of: Dict[EntryKey, int] = {}
        self._entries: Dict[EntryKey, UpdateEntry] = {}
        # Downloads counted by the progress bar until they all finish.
        self._download_batch: Set[EntryKey] = set()

        self.setWindowTitle("Nexus Auto Updater")
        self.resize(1150, 620)

        self._summary = QLabel(f"Checking {plan.total} {scope_label} against Nexus…")
        self._summary.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setRange(0, max(1, plan.total))

        self._hide_current = QCheckBox("Hide up-to-date")
        self._hide_current.setChecked(True)
        self._hide_current.toggled.connect(self._apply_row_visibility)

        self._table = QTableWidget(0, len(HEADERS))
        header = CheckableHeader(COL_CHECK, self._table)
        header.toggled.connect(self._set_all_checked)
        self._header = header
        self._table.setHorizontalHeader(header)
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.horizontalHeaderItem(COL_CHECK).setToolTip("Select / deselect all updates")
        self._table.horizontalHeaderItem(COL_LOCK).setToolTip(text.LOCK_TOOLTIP)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemChanged.connect(self._on_item_changed)
        self._check_delegate = CenteredCheckDelegate(self._table)
        for column in (COL_CHECK, COL_LOCK):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(column, 32)
            self._table.setItemDelegateForColumn(column, self._check_delegate)
        for column in (COL_MOD, COL_INSTALLED, COL_TARGET):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, 250)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

        self._download_button = QPushButton("Download checked")
        self._download_button.clicked.connect(self._download_checked)
        self._install_button = QPushButton("Install downloaded…")
        self._install_button.clicked.connect(self._install_downloaded)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self._hide_current)
        buttons.addStretch(1)
        buttons.addWidget(self._download_button)
        buttons.addWidget(self._install_button)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._progress)
        layout.addWidget(self._table, 1)
        layout.addLayout(buttons)

        session.subscribe(self._on_entry_changed)
        self._update_buttons()
        QTimer.singleShot(0, self._start_scan)

    # --- Scanning ---

    def _start_scan(self) -> None:
        if self._plan.is_empty:
            self._progress.setVisible(False)
            self._summary.setText(self._empty_message)
            return
        self._worker = ScanWorker(self._plan, self)
        self._worker.event.connect(self._on_scan_event)
        self._worker.finished.connect(self._on_scan_finished)
        self._scanning = True
        self._update_buttons()
        self._worker.start()

    def _on_scan_event(self, event) -> None:
        if isinstance(event, ModScanned):
            for entry in self._session.record_scan(event.scan):
                self._add_row(entry)
            self._update_buttons()
        elif isinstance(event, ScanProgress):
            self._progress.setRange(0, max(1, event.total))
            self._progress.setValue(event.done)
            if event.mod_name:
                self._summary.setText(f"Checking {event.done + 1}/{event.total}: {event.mod_name}")
        elif isinstance(event, ScanFailed):
            self._scan_failed = True
            self._summary.setText(text.scan_failed_message(event))

    def _on_scan_finished(self) -> None:
        self._scanning = False
        self._progress.setVisible(False)
        self._update_buttons()
        if self._scan_failed:
            return  # Keep the error message visible.
        counts = {status: 0 for status in EntryStatus}
        for entry in self._entries.values():
            counts[entry.status] += 1
        self._summary.setText(
            "Scan complete — "
            + ", ".join(
                f"{text.STATUS_TEXT[status]}: {count}" for status, count in counts.items() if count
            )
            + ". Rows marked 'Needs review' are unchecked; verify before downloading."
        )

    # --- Table ---

    def _add_row(self, entry: UpdateEntry) -> None:
        index = self._table.rowCount()
        self._table.insertRow(index)
        self._row_keys.append(entry.key)
        self._row_of[entry.key] = index
        self._entries[entry.key] = entry
        self._render(index, initial=True)
        self._table.setRowHidden(index, self._hidden(entry))

    def _hidden(self, entry: UpdateEntry) -> bool:
        return self._hide_current.isChecked() and entry.status is EntryStatus.UP_TO_DATE

    def _render(self, index: int, initial: bool = False) -> None:
        self._bulk_check = True
        try:
            self._render_cells(index, initial)
        finally:
            self._bulk_check = False
        self._sync_header()

    def _render_cells(self, index: int, initial: bool) -> None:
        entry = self._entries[self._row_keys[index]]

        check = self._table.item(index, COL_CHECK)
        if check is None:
            check = QTableWidgetItem()
            self._table.setItem(index, COL_CHECK, check)
        flags = Qt.ItemFlag.ItemIsEnabled
        if entry.can_download:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        check.setFlags(flags)
        if initial:
            ready = entry.status is EntryStatus.READY and entry.can_download
            check.setCheckState(Qt.CheckState.Checked if ready else Qt.CheckState.Unchecked)
        elif not entry.can_download:
            check.setCheckState(Qt.CheckState.Unchecked)

        lock = self._table.item(index, COL_LOCK)
        if lock is None:
            lock = QTableWidgetItem()
            lock.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            lock.setToolTip(text.LOCK_TOOLTIP)
            self._table.setItem(index, COL_LOCK, lock)
        lock.setCheckState(Qt.CheckState.Checked if entry.locked else Qt.CheckState.Unchecked)

        mod_item = QTableWidgetItem(entry.mod_name)
        mod_item.setToolTip(text.mod_tooltip(entry))
        self._table.setItem(index, COL_MOD, mod_item)

        installed_item = QTableWidgetItem(text.file_label(entry.installed, entry.installed_file_id))
        installed_item.setToolTip(text.file_tooltip(entry.installed))
        self._table.setItem(index, COL_INSTALLED, installed_item)

        target_item = QTableWidgetItem(text.file_label(entry.target))
        target_item.setToolTip(text.file_tooltip(entry.target))
        self._table.setItem(index, COL_TARGET, target_item)

        status_item = QTableWidgetItem(text.status_text(entry))
        self._table.setItem(index, COL_STATUS, status_item)

        details = entry.reason
        if entry.archive_path:
            details += f"\nArchive: {entry.archive_path}"
        details_item = QTableWidgetItem(entry.reason)
        details_item.setToolTip(details)
        self._table.setItem(index, COL_DETAILS, details_item)

        if entry.locked:
            grey = QBrush(QColor(text.LOCKED_COLOR))
            for column in (COL_MOD, COL_INSTALLED, COL_TARGET, COL_STATUS, COL_DETAILS):
                self._table.item(index, column).setForeground(grey)
        else:
            status_item.setForeground(QBrush(QColor(text.status_color(entry))))

    def _apply_row_visibility(self) -> None:
        for index, key in enumerate(self._row_keys):
            self._table.setRowHidden(index, self._hidden(self._entries[key]))

    def _checkable_items(self) -> List[QTableWidgetItem]:
        items = []
        for index in range(self._table.rowCount()):
            item = self._table.item(index, COL_CHECK)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                items.append(item)
        return items

    def _sync_header(self) -> None:
        items = self._checkable_items()
        all_checked = bool(items) and all(
            item.checkState() == Qt.CheckState.Checked for item in items
        )
        self._header.set_state(all_checked, bool(items))

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._bulk_check = True
        try:
            for item in self._checkable_items():
                item.setCheckState(state)
        finally:
            self._bulk_check = False
        self._sync_header()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._bulk_check:
            return
        if item.column() == COL_CHECK:
            self._sync_header()
        elif item.column() == COL_LOCK:
            entry = self._entries[self._row_keys[item.row()]]
            # Re-renders every row of the mod through _on_entry_changed.
            self._session.set_locked(entry.mod_id, item.checkState() == Qt.CheckState.Checked)

    def _update_buttons(self) -> None:
        # The progress bar is shared, so downloads wait for the scan to finish.
        entries = self._entries.values()
        self._download_button.setEnabled(
            not self._scanning and any(e.can_download for e in entries)
        )
        self._install_button.setEnabled(any(e.can_install for e in entries))

    def _on_entry_changed(self, entry: UpdateEntry) -> None:
        index = self._row_of.get(entry.key)
        if index is None:
            return
        self._entries[entry.key] = entry
        self._render(index)
        self._update_buttons()
        self._update_download_progress()

    def _update_download_progress(self) -> None:
        batch = self._download_batch
        if not batch:
            return
        total = len(batch)
        phases = [self._entries[key].phase for key in batch]
        done = sum(1 for phase in phases if phase not in (Phase.QUEUED, Phase.DOWNLOADING))
        if done < total:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
            self._progress.setVisible(True)
            self._summary.setText(f"Downloading updates: {done}/{total} finished.")
            return

        failed = sum(1 for phase in phases if phase is Phase.DOWNLOAD_FAILED)
        batch.clear()
        self._progress.setVisible(False)
        self._summary.setText(
            f"Downloads complete: {total - failed} downloaded, {failed} failed."
            + (" Use 'Install downloaded…' to install them." if total > failed else "")
        )

    # --- Actions ---

    def _download_checked(self) -> None:
        for index, key in enumerate(self._row_keys):
            check = self._table.item(index, COL_CHECK)
            if check is None or check.checkState() != Qt.CheckState.Checked:
                continue
            if not self._entries[key].can_download:
                continue
            # Add first: the session reports the QUEUED change synchronously.
            self._download_batch.add(key)
            self._session.download(key)
        self._update_buttons()
        self._update_download_progress()

    def _install_downloaded(self) -> None:
        keys = [key for key in self._row_keys if self._entries[key].can_install]
        if not keys:
            return
        answer = QMessageBox.question(
            self,
            "Install downloaded updates",
            f"Install {len(keys)} downloaded update(s)?\n\n"
            "MO2 will ask how to handle each existing mod (choose Replace to keep "
            "its priority and enabled state), and FOMOD installers will open as usual.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for key in keys:
            self._session.install(key)

    # --- Lifetime ---

    def done(self, result: int) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(5000)
        self._session.subscribe(None)
        super().done(result)
