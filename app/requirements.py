"""Build the dependency graph of the installed mods."""

from typing import Mapping, Optional, Sequence

from ..domain.requirements import DependencyGraph, ModRequirements
from .ports import GameRef, InstalledMod, ModRequirementSource, SourceError


def load_dependency_graph(
    source: ModRequirementSource, game: GameRef, mods: Sequence[InstalledMod]
) -> DependencyGraph:
    """Blocking; run it off the UI thread.

    Requirements are advisory, so a failing source yields a graph in which every
    mod is unknown rather than an error. Only mods of the managed game are nodes:
    Nexus mod IDs of other games would collide with them.
    """
    ids = sorted({mod.mod_id for mod in mods if mod.in_managed_game})
    requirements: Mapping[int, Optional[ModRequirements]]
    try:
        requirements = source.mod_requirements(game, ids)
    except SourceError:
        requirements = {}
    return DependencyGraph(ids, requirements)
