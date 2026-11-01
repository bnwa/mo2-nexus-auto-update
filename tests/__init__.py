"""Test bootstrap: runs before any test module imports plugin code.

* Registers the repo root as package ``nexus_auto_updater`` without executing its
  ``__init__.py`` (which needs mobase and Qt), whatever the checkout is called.
* Installs a stand-in ``mobase`` (MO2's real ``mobase.pyd`` needs MO2's DLLs).
* Prepares Qt for offscreen use; Qt tests are skipped when PyQt6 is missing.
"""

import importlib.machinery
import importlib.util
import os
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "nexus_auto_updater"


def _register_package() -> None:
    if PACKAGE in sys.modules:
        return
    spec = importlib.machinery.ModuleSpec(PACKAGE, None, is_package=True)
    spec.submodule_search_locations = [REPO_ROOT]
    module = importlib.util.module_from_spec(spec)
    module.__file__ = os.path.join(REPO_ROOT, "__init__.py")
    sys.modules[PACKAGE] = module


_register_package()

from . import fake_mobase  # noqa: E402

fake_mobase.install()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
if os.path.isdir(_FONTS):
    os.environ.setdefault("QT_QPA_FONTDIR", _FONTS)

try:
    from PyQt6 import QtWidgets  # noqa: F401

    HAS_QT = True
except ImportError:
    HAS_QT = False

requires_qt = unittest.skipUnless(
    HAS_QT, "PyQt6 is not importable; install tests\\requirements.txt to run Qt tests"
)

_app = None


def qapp():
    """The shared offscreen QApplication, created on first use."""
    global _app
    from PyQt6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    return _app


def wait_until(predicate, timeout: float = 5.0) -> None:
    """Pump the Qt event loop until ``predicate()`` is true."""
    from PyQt6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while True:
        QCoreApplication.processEvents()
        if predicate():
            return
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for a condition")
        time.sleep(0.002)


@requires_qt
class QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        qapp()
