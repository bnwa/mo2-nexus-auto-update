from typing import Callable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..app.scan import ScanEvent, ScanPlan


class ScanWorker(QThread):
    """Runs a scan plan off the UI thread and forwards its events as a signal."""

    scan_event = pyqtSignal(object)  # app.scan.ScanEvent

    def __init__(self, plan: ScanPlan, parent=None):
        super().__init__(parent)
        self._plan = plan

    def run(self) -> None:
        for event in self._plan.run(should_stop=self.isInterruptionRequested):
            self.scan_event.emit(event)


class QtScanRunner(QObject):
    """Runs a scan on a ``ScanWorker`` and calls back on the thread that owns this object."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._worker: Optional[ScanWorker] = None
        self._on_event: Callable[[ScanEvent], None] = lambda event: None
        self._on_finished: Callable[[], None] = lambda: None

    def start(
        self,
        plan: ScanPlan,
        on_event: Callable[[ScanEvent], None],
        on_finished: Callable[[], None],
    ) -> None:
        self._on_event, self._on_finished = on_event, on_finished
        self._worker = ScanWorker(plan, self)
        # Bound methods of this object, so Qt queues both onto the thread it lives in.
        self._worker.scan_event.connect(self._forward_event)
        self._worker.finished.connect(self._forward_finished)
        self._worker.start()

    def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(5000)

    # Signals the worker queued before stop() are still pending afterwards; dropping
    # them here is what makes "no callback after stop()" true.
    def _forward_event(self, event: ScanEvent) -> None:
        if self._worker is not None:
            self._on_event(event)

    def _forward_finished(self) -> None:
        if self._worker is not None:
            self._on_finished()
