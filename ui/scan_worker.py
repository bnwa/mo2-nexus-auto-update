from PyQt6.QtCore import QThread, pyqtSignal

from ..app.scan import ScanPlan


class ScanWorker(QThread):
    """Runs a scan plan off the UI thread and forwards its events as a signal."""

    event = pyqtSignal(object)  # app.scan.ScanEvent

    def __init__(self, plan: ScanPlan, parent=None):
        super().__init__(parent)
        self._plan = plan

    def run(self) -> None:
        for event in self._plan.run(should_stop=self.isInterruptionRequested):
            self.event.emit(event)
