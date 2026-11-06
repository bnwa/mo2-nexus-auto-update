import unittest

from nexus_auto_updater.app.ports import SourceError, SourceIncompatible
from nexus_auto_updater.app.requirements import load_dependency_graph
from nexus_auto_updater.app.session import UpdateSession
from nexus_auto_updater.domain.requirements import DependencyGraph, ModRequirements
from nexus_auto_updater.tracking.tracker import DownloadTracker

from .fakes import (
    GAME,
    ArchiveIds,
    FakeDownloader,
    FakeInstaller,
    FakeLocks,
    ManualScheduler,
    installed_mod,
)


class FakeRequirementSource:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def mod_requirements(self, game, mod_ids):
        self.calls.append((game, list(mod_ids)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class LoadDependencyGraphTest(unittest.TestCase):
    def test_builds_graph_from_the_managed_games_mods_only(self):
        source = FakeRequirementSource(
            {1: ModRequirements(frozenset({2})), 2: ModRequirements(frozenset())}
        )
        mods = [
            installed_mod(2),
            installed_mod(1),
            installed_mod(2),
            installed_mod(3, in_managed_game=False),
        ]
        graph = load_dependency_graph(source, GAME, mods)
        self.assertEqual([(GAME, [1, 2])], source.calls)
        self.assertEqual({2}, graph.dependencies(1))
        self.assertEqual({1}, graph.dependents(2))
        self.assertEqual(frozenset(), graph.dependents(3))
        self.assertFalse(graph.dependencies_known(3))

    def test_source_failure_gives_an_all_unknown_graph(self):
        for error in (SourceError("x"), SourceIncompatible("y")):
            graph = load_dependency_graph(FakeRequirementSource(error), GAME, [installed_mod(1)])
            self.assertFalse(graph.dependencies_known(1))
            self.assertFalse(graph.dependents_known(1))


class SessionGraphTest(unittest.TestCase):
    def test_graph_is_none_until_loaded(self):
        session = UpdateSession(
            FakeDownloader(),
            FakeInstaller(),
            DownloadTracker(ArchiveIds(), ManualScheduler()),
            FakeLocks(),
            lambda: 3,
        )
        self.assertIsNone(session.dependency_graph)
        graph = DependencyGraph([1], {1: ModRequirements(frozenset())})
        session.set_dependency_graph(graph)
        self.assertIs(graph, session.dependency_graph)


if __name__ == "__main__":
    unittest.main()
