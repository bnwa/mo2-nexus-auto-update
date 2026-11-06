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
    BatchState,
    Complete,
    Finished,
    InProgress,
    NoBatch,
    NothingToScan,
    NotStarted,
    RowChanged,
    RowsAdded,
    Scanning,
    ScanState,
    SelectAll,
    Stopped,
    StatusChanged,
    Sync,
    SyncChange,
)
from . import text
from .header import CenteredCheckDelegate, CheckableHeader
from .notes import NotesPane
from .qt_types import present
from .status_delegate import StatusDelegate
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
        self._status_delegate = StatusDelegate(self._table)
        self._table.setItemDelegateForColumn(COL_STATUS, self._status_delegate)
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

        self._downloads = QLabel()
        self._downloads.setObjectName("downloads")
        self._downloads.setVisible(False)

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
        layout.addWidget(self._downloads)
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
        self._render_scan(status.scan)
        self._render_downloads(status.batch)
        # Downloads only start through the batch, so a row is queued or downloading
        # exactly while the batch is in progress.
        self._status_delegate.set_animating(isinstance(status.batch, InProgress))
        self._header.set_state(status.select_all is SelectAll.ALL, status.can_download)
        self._download_button.setEnabled(status.can_download)
        self._install_button.setEnabled(status.can_install)

    def _render_scan(self, scan: ScanState) -> None:
        match scan:
            case NotStarted():
                self._show("", None)
            case NothingToScan():
                self._show(self._empty_message, None)
            case Scanning(done=done, total=total, current=current):
                self._show(self._scanning_text(done, total, current), (done, max(1, total)))
            case Stopped(failure=failure):
                self._show(text.scan_failed_message(failure), None)
            case Complete():
                self._show(self._scan_summary(), None)
            case _:
                assert_never(scan)

    def _render_downloads(self, batch: BatchState) -> None:
        match batch:
            case NoBatch():
                self._downloads.setVisible(False)
                return
            case InProgress(done=done, failed=failed):
                failures = f", {failed} failed" if failed else ""
                self._downloads.setText(f"Downloads: {done}/{batch.total} finished{failures}")
            case Finished(downloaded=downloaded, failed=failed):
                self._downloads.setText(
                    f"Downloads complete: {downloaded} downloaded, {failed} failed."
                    + (" Use 'Install downloaded…' to install them." if downloaded else "")
                )
            case _:
                assert_never(batch)
        self._downloads.setVisible(True)

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
        # Closing leaves any in-flight download unsettled, so the batch would keep animating.
        self._status_delegate.set_animating(False)
        self._sync.close()
        super().done(a0)
