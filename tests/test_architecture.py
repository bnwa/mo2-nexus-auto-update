"""Enforces ARCH.md's dependency rules on every production module."""

import ast
import os
import unittest
from typing import Iterator, List, Optional, Tuple

from tests import REPO_ROOT

LAYERS = ("domain", "tracking", "app", "nexus", "host", "ui")
# Layer -> internal imports it may make (a layer name, or a dotted module).
ALLOWED = {
    "domain": ("domain",),
    "tracking": ("tracking",),
    "app": ("app", "domain", "tracking"),
    "nexus": ("nexus", "domain", "app.ports"),
    "host": ("host", "app.ports"),
    "ui": ("ui", "app", "domain"),
}
MOBASE_ALLOWED = {"host/mo2.py", "plugin.py", "__init__.py"}
_SKIPPED_DIRS = {"tests", ".venv", "__pycache__", ".git"}


def production_modules() -> List[str]:
    """Repo-relative paths (forward slashes) of every production module."""
    found = []
    for directory, subdirs, files in os.walk(REPO_ROOT):
        subdirs[:] = [d for d in subdirs if d not in _SKIPPED_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                path = os.path.relpath(os.path.join(directory, name), REPO_ROOT)
                found.append(path.replace(os.sep, "/"))
    return sorted(found)


def _imports(rel_path: str, source: str) -> Iterator[Tuple[Optional[str], str]]:
    """Yield ``(internal dotted target or None, external module name)``."""
    package = rel_path[: -len(".py")].split("/")[:-1]
    for node in ast.walk(ast.parse(source, rel_path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield None, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                yield None, node.module or ""
                continue
            base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
            if node.level - 1 > len(package):
                yield "<beyond the package>", ""
                continue
            module = base + (node.module.split(".") if node.module else [])
            # Include each imported name: ``from ..app import scan`` imports app.scan.
            for alias in node.names:
                yield ".".join(module + [alias.name]), ""


def violations(rel_path: str, source: str) -> List[str]:
    layer = rel_path.split("/")[0] if "/" in rel_path else None
    problems = []
    for internal, external in _imports(rel_path, source):
        if internal is not None:
            if layer is None:
                continue  # plugin.py and the root __init__.py may import anything.
            allowed = ALLOWED[layer]
            if not any(internal == a or internal.startswith(a + ".") for a in allowed):
                problems.append(f"{rel_path}: layer '{layer}' must not import '{internal}'")
            continue
        top = external.split(".")[0]
        if top == "mobase" and rel_path not in MOBASE_ALLOWED:
            problems.append(f"{rel_path}: must not import 'mobase'")
        elif top == "PyQt6" and not (layer == "ui" or rel_path == "plugin.py"):
            problems.append(f"{rel_path}: must not import Qt ('{external}')")
        elif top in LAYERS or top == "nexus_auto_updater":
            problems.append(f"{rel_path}: absolute import of plugin code '{external}'")
    return problems


class DependencyRulesTest(unittest.TestCase):
    def test_every_production_module_follows_the_rules(self):
        modules = production_modules()
        for expected in ("plugin.py", "__init__.py", "ui/dialog.py", "host/mo2.py", "nexus/graphql.py"):
            self.assertIn(expected, modules)
        problems = []
        for rel_path in modules:
            with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as handle:
                problems += violations(rel_path, handle.read())
        self.assertEqual([], problems, "\n".join(problems))


class CheckerSelfTest(unittest.TestCase):
    """The checker itself must catch each kind of violation."""

    def assertViolation(self, rel_path, source, fragment):
        problems = violations(rel_path, source)
        self.assertTrue(any(fragment in p for p in problems), problems)
        self.assertTrue(all(p.startswith(rel_path) for p in problems))

    def test_layer_violations(self):
        self.assertViolation("domain/x.py", "from ..app import ports", "'domain' must not import 'app.ports'")
        self.assertViolation("tracking/x.py", "from ..domain.files import ModFile", "'domain.files.ModFile'")
        self.assertViolation("app/x.py", "from ..nexus.catalog import X", "'nexus.catalog.X'")
        self.assertViolation("nexus/x.py", "from ..app.scan import ScanPlan", "'app.scan.ScanPlan'")
        self.assertViolation("nexus/x.py", "from ..app import scan", "'app.scan'")
        self.assertViolation("host/x.py", "from ..app.session import X", "'app.session.X'")
        self.assertViolation("host/x.py", "from ..domain.files import ModFile", "'domain.files.ModFile'")
        self.assertViolation("ui/x.py", "from ..tracking.tracker import X", "'tracking.tracker.X'")
        self.assertViolation("app/x.py", "from .. import host", "'host'")
        self.assertViolation("ui/x.py", "from ..host import mo2", "'host.mo2'")
        self.assertViolation("ui/x.py", "from ... import y", "<beyond the package>")

    def test_external_violations(self):
        self.assertViolation("ui/x.py", "import mobase", "'mobase'")
        self.assertViolation("host/metadata.py", "import mobase", "'mobase'")
        self.assertViolation("app/x.py", "from PyQt6.QtCore import QTimer", "Qt")
        self.assertViolation("host/mo2.py", "import PyQt6.QtCore", "Qt")
        self.assertViolation("app/x.py", "import domain.files", "absolute import")

    def test_allowed_imports(self):
        self.assertEqual([], violations("nexus/x.py", "from ..app.ports import GameRef\nfrom . import graphql"))
        self.assertEqual([], violations("host/mo2.py", "import mobase\nfrom ..app.ports import GameRef"))
        self.assertEqual([], violations("ui/x.py", "from PyQt6.QtCore import Qt\nfrom ..app.session import X"))
        self.assertEqual([], violations("plugin.py", "import mobase\nfrom PyQt6 import QtCore\nfrom .host import mo2"))
        self.assertEqual([], violations("app/x.py", "import threading\nfrom .ports import GameRef"))


if __name__ == "__main__":
    unittest.main()
