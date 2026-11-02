import unittest

from nexus_auto_updater.domain.requirements import DependencyGraph, ModRequirements


def reqs(*ids, complete=True):
    return ModRequirements(frozenset(ids), complete)


class DependencyGraphTest(unittest.TestCase):
    def setUp(self):
        # 3 needs 1 and 2; 2 needs 1 and 99 (not installed); 1 needs nothing.
        self.graph = DependencyGraph(
            [1, 2, 3], {1: reqs(), 2: reqs(1, 99), 3: reqs(1, 2)}
        )

    def test_dependencies_and_dependents_are_inverse(self):
        self.assertEqual({1, 2}, self.graph.dependencies(3))
        self.assertEqual({1}, self.graph.dependencies(2))
        self.assertEqual({2, 3}, self.graph.dependents(1))
        self.assertEqual({3}, self.graph.dependents(2))
        self.assertEqual(frozenset(), self.graph.dependents(3))

    def test_requirements_outside_the_installed_set_are_reported_apart(self):
        self.assertEqual({99}, self.graph.not_installed(2))
        self.assertNotIn(99, self.graph.dependencies(2))
        self.assertEqual(frozenset(), self.graph.not_installed(3))

    def test_everything_known_gives_exact_answers(self):
        for mod_id in (1, 2, 3):
            self.assertTrue(self.graph.dependencies_known(mod_id))
            self.assertTrue(self.graph.dependents_known(mod_id))

    def test_unknown_mod_is_not_reported_as_having_no_dependencies(self):
        graph = DependencyGraph([1, 2], {1: reqs(), 2: None})
        self.assertFalse(graph.dependencies_known(2))
        self.assertEqual(frozenset(), graph.dependencies(2))

    def test_one_unknown_mod_makes_every_dependents_answer_inexact(self):
        graph = DependencyGraph([1, 2, 3], {1: reqs(), 2: reqs(1)})  # 3 missing
        self.assertTrue(graph.dependencies_known(2))
        self.assertFalse(graph.dependents_known(1))

    def test_incomplete_requirements_count_as_not_known(self):
        graph = DependencyGraph([1, 2], {1: reqs(complete=False), 2: reqs(1)})
        self.assertFalse(graph.dependencies_known(1))
        self.assertFalse(graph.dependents_known(2))

    def test_self_edges_and_foreign_nodes_are_ignored(self):
        graph = DependencyGraph([1, 2], {1: reqs(1, 2), 7: reqs(1)})
        self.assertEqual({2}, graph.dependencies(1))
        self.assertEqual(frozenset(), graph.dependents(1))
        self.assertEqual(frozenset(), graph.not_installed(1))

    def test_mod_that_is_not_installed_has_no_exact_dependents(self):
        self.assertFalse(self.graph.dependents_known(42))

    def test_cycles_are_representable(self):
        graph = DependencyGraph([1, 2], {1: reqs(2), 2: reqs(1)})
        self.assertEqual({2}, graph.dependents(1))
        self.assertEqual({1}, graph.dependents(2))


if __name__ == "__main__":
    unittest.main()
