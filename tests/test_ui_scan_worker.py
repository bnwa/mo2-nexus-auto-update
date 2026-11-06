import threading
import time

from tests import HAS_QT, QtTestCase, wait_until
from tests.fakes import Gate

if HAS_QT:
    from nexus_auto_updater.ui.scan_worker import QtScanRunner, ScanWorker


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


class RunningPlan:
    """Yields ``first``, then spins until asked to stop."""

    def __init__(self):
        self.entered = threading.Event()
        self.saw_stop = threading.Event()

    def run(self, should_stop):
        yield "first"
        self.entered.set()
        while not should_stop():
            time.sleep(0.001)
        self.saw_stop.set()


class QtScanRunnerTest(QtTestCase):
    def setUp(self):
        super().setUp()
        self.runner = QtScanRunner()
        self.addCleanup(self.runner.deleteLater)
        self.addCleanup(self.runner.stop)
        self.events = []
        self.finished = []
        self.threads = []

    def start(self, plan):
        def on_event(event):
            self.threads.append(threading.current_thread())
            self.events.append(event)

        self.runner.start(plan, on_event, lambda: self.finished.append(True))

    def test_delivers_events_then_finish_on_the_gui_thread(self):
        self.start(FakePlan(["a", "b"]))
        wait_until(lambda: self.finished)
        self.assertEqual(["a", "b"], self.events)
        self.assertEqual([threading.main_thread()] * 2, self.threads)

    def test_nothing_is_delivered_after_stop_even_if_already_queued(self):
        done = threading.Event()

        class Plan:
            def run(self, should_stop):
                yield "a"
                yield "b"
                done.set()

        self.start(Plan())
        # Waiting without pumping the event loop leaves every signal queued.
        self.assertTrue(done.wait(5))
        self.runner.stop()
        wait_until(lambda: True)
        self.assertEqual([], self.events)
        self.assertEqual([], self.finished)

    def test_stop_interrupts_a_running_scan(self):
        plan = RunningPlan()
        self.start(plan)
        wait_until(lambda: self.events == ["first"] and plan.entered.is_set())
        self.runner.stop()
        self.assertTrue(plan.saw_stop.is_set())
        wait_until(lambda: True)
        self.assertEqual(["first"], self.events)
        self.assertEqual([], self.finished)

    def test_stop_is_safe_before_start_and_repeatable(self):
        self.runner.stop()
        self.start(FakePlan([]))
        wait_until(lambda: self.finished)
        self.runner.stop()
        self.runner.stop()
