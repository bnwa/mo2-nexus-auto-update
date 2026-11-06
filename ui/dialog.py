from collections import Counter
from typing import Optional, Tuple, assert_never

from PyQt6.QtCore import QModelIndex, Qt, QTimer
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
    QSplitter,
    QTableView,
    QVBoxLayout,
)

from ..app.notes import ModNotes
from ..app.sync import (
    Complete,
    Finished,
    InProgress,
    NoBatch,
    NothingToScan,
    NotStarted,
    RowChanged,
    RowsAdded,
    Scanning,
    SelectAll,
    Stopped,
    StatusChanged,
    Sync,
    SyncChange,
    SyncStatus,
)
from . import text
from .header import CenteredCheckDelegate, CheckableHeader
from .notes import NotesPane
from .qt_types import present
from .table_model import (
    COL_CHECK,
    COL_INSTALLED,
    COL_LOCK,
    COL_MOD,
    COL_STATUS,
    COL_TARGET,
    HideCurrentProxy,
    UpdatesModel,
)


class UpdaterDialog(QDialog):
    def __init__(
        self,
        parent,
        sync: Sync,
        notes: ModNotes,
        *,
        scope_label: str,
        empty_message: str,
    ):
        super().__init__(parent)
        self._sync = sync
        self._scope_label = scope_label
        self._empty_message = empty_message

        self.setWindowTitle("Nexus Auto Updater")
        self.resize(1150, 760)

        self._summary = QLabel()
        self._summary.setObjectName("summary")
        self._summary.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setObjectName("progress")
        self._progress.setVisible(False)

        self._hide_current = QCheckBox("Hide up-to-date")
        self._hide_current.setObjectName("hide_current")
        self._hide_current.setChecked(True)

        self._model = UpdatesModel(sync, self)
        self._proxy = HideCurrentProxy(self._model, self)
        self._proxy.set_hide_current(self._hide_current.isChecked())
        self._hide_current.toggled.connect(self._proxy.set_hide_current)

        self._table = QTableView()
        self._table.setObjectName("updates")
        header = CheckableHeader(COL_CHECK, self._table)
        header.toggled.connect(sync.set_all_selected)
        self._header = header
        self._table.setHorizontalHeader(header)
        self._table.setModel(self._proxy)
        present(self._table.verticalHeader()).setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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
        present(self._table.selectionModel()).currentChanged.connect(self._on_current_changed)

        self._notes = NotesPane(notes)
        self._notes.setObjectName("notes")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._notes)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._download_button = QPushButton("Download checked")
        self._download_button.setObjectName("download_button")
        self._download_button.clicked.connect(sync.download_selected)
        self._install_button = QPushButton("Install downloaded…")
        self._install_button.setObjectName("install_button")
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
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)

        sync.subscribe(self._on_change)
        self._render_status()
        QTimer.singleShot(0, sync.start)

    # --- Sync notifications ---

    def _on_change(self, change: SyncChange) -> None:
        match change:
            case RowsAdded(keys=keys):
                self._model.rows_added(keys)
            case RowChanged(key=key):
                self._model.row_changed(key)
            case StatusChanged():
                self._render_status()
            case _:
                assert_never(change)

    def _on_current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self._notes.show_entry(self._proxy.entry_of(current))

    # --- Status ---

    def _render_status(self) -> None:
        status = self._sync.status()
        self._render_activity(status)
        self._header.set_state(status.select_all is SelectAll.ALL, status.can_download)
        # The scan and the downloads share one summary and bar, so downloads wait for the scan.
        scanning = isinstance(status.scan, Scanning)
        self._download_button.setEnabled(status.can_download and not scanning)
        self._install_button.setEnabled(status.can_install)

    def _render_activity(self, status: SyncStatus) -> None:
        scan = status.scan
        match scan:
            case NotStarted():
                self._show("", None)
            case NothingToScan():
                self._show(self._empty_message, None)
            case Scanning(done=done, total=total, current=current):
                self._show(self._scanning_text(done, total, current), (done, max(1, total)))
            case Stopped(failure=failure):
                self._show_downloads_or(status, text.scan_failed_message(failure))
            case Complete():
                self._show_downloads_or(status, self._scan_summary())
            case _:
                assert_never(scan)

    def _show_downloads_or(self, status: SyncStatus, scan_result: str) -> None:
        batch = status.batch
        match batch:
            case NoBatch():
                self._show(scan_result, None)
            case InProgress(done=done):
                self._show(
                    f"Downloading updates: {done}/{batch.total} finished.", (done, batch.total)
                )
            case Finished(downloaded=downloaded, failed=failed):
                self._show(
                    f"Downloads complete: {downloaded} downloaded, {failed} failed."
                    + (" Use 'Install downloaded…' to install them." if downloaded else ""),
                    None,
                )
            case _:
                assert_never(batch)

    def _show(self, summary: str, progress: Optional[Tuple[int, int]]) -> None:
        self._summary.setText(summary)
        if progress is None:
            self._progress.setVisible(False)
            return
        value, maximum = progress
        self._progress.setRange(0, maximum)
        self._progress.setValue(value)
        self._progress.setVisible(True)

    def _scanning_text(self, done: int, total: int, current: str) -> str:
        if current:
            return f"Checking {done + 1}/{total}: {current}"
        if done == 0:
            return f"Checking {total} {self._scope_label} against Nexus…"
        return f"Checking {done}/{total}…"

    def _scan_summary(self) -> str:
        counts = Counter(
            text.finding_label(self._sync.entry(key).finding) for key in self._sync.rows()
        )
        return (
            "Scan complete — "
            + ", ".join(f"{label}: {counts[label]}" for label in text.FINDING_LABELS if counts[label])
            + ". Rows marked 'Needs review' are unchecked; verify before downloading."
        )

    # --- Actions ---

    def _install_downloaded(self) -> None:
        keys = self._sync.status().installable
        answer = QMessageBox.question(
            self,
            "Install downloaded updates",
            f"Install {len(keys)} downloaded update(s)?\n\n"
            "MO2 will ask how to handle each existing mod (choose Replace to keep "
            "its priority and enabled state), and FOMOD installers will open as usual.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._sync.install_downloaded(keys)

    # --- Lifetime ---

    def done(self, a0: int) -> None:
        self._sync.close()
        super().done(a0)
