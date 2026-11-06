import threading

from tests import HAS_QT, QtTestCase, wait_until
from tests.fakes import Gate

if HAS_QT:
    from nexus_auto_updater.ui.scan_worker import ScanWorker


class FakePlan:
    def __init__(self, events, gate=None):
        self.events = events
        self.gate = gate
        self.stop_answers = []
        self.thread = None

    def run(self, should_stop):
        self.thread = threading.current_thread()
        for event in self.events:
            if self.gate is not None and event == "second":
                self.gate.wait()
            answer = should_stop()
            self.stop_answers.append(answer)
            if answer:
                return
            yield event


class ScanWorkerTest(QtTestCase):
    def start(self, plan):
        worker = ScanWorker(plan)
        self.addCleanup(worker.deleteLater)
        self.addCleanup(worker.wait, 5000)
        received = []
        worker.scan_event.connect(received.append)
        worker.start()
        return worker, received

    def test_forwards_every_event_off_the_gui_thread(self):
        events = [object(), "progress", ("scanned", 1), None]
        plan = FakePlan(events)
        worker, received = self.start(plan)
        wait_until(lambda: worker.isFinished() and len(received) == len(events))
        self.assertEqual(events, received)
        self.assertIsNot(threading.main_thread(), plan.thread)
        self.assertEqual([False] * 4, plan.stop_answers)

    def test_stops_on_interruption(self):
        gate = Gate()
        plan = FakePlan(["first", "second", "third"], gate)
        worker, received = self.start(plan)
        wait_until(lambda: received == ["first"] and gate.entered.is_set())
        worker.requestInterruption()
        gate.release.set()
        wait_until(worker.isFinished)
        wait_until(lambda: True)
        self.assertEqual(["first"], received)
        self.assertEqual([False, True], plan.stop_answers)

    # coverage.py doesn't trace QThreads, so run() is also exercised synchronously.
    def test_run_forwards_events_synchronously(self):
        worker = ScanWorker(FakePlan(["a", "b"]))
        self.addCleanup(worker.deleteLater)
        received = []
        worker.scan_event.connect(received.append)
        worker.run()
        self.assertEqual(["a", "b"], received)

    def test_run_passes_the_interruption_flag(self):
        plan = FakePlan(["a", "b"])
        worker = ScanWorker(plan)
        self.addCleanup(worker.deleteLater)
        received = []
        worker.scan_event.connect(received.append)
        worker.isInterruptionRequested = lambda: True
        worker.run()
        self.assertEqual([], received)
        self.assertEqual([True], plan.stop_answers)

    def test_parent(self):
        from PyQt6.QtCore import QObject

        parent = QObject()
        self.addCleanup(parent.deleteLater)
        self.assertIs(parent, ScanWorker(FakePlan([]), parent).parent())
