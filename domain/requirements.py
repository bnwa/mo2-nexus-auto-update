"""Which installed mods declare that they need which others.

The source only knows mod-level requirements: no versions, no files, and authors
use them for soft links too. So the graph is advisory, and it always says whether
an answer is *known* instead of letting "unknown" read as "none".
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ModRequirements:
    """The Nexus-hosted mods one mod page declares as requirements."""

    mod_ids: FrozenSet[int]
    # False when the declared list may be longer than ``mod_ids`` (truncated,
    # unreadable or out-of-game entries were dropped).
    complete: bool = True


class DependencyGraph:
    """Requirement edges among installed mods, keyed by Nexus mod ID.

    ``requirements`` maps each installed mod to what the source declared for it,
    or ``None`` when that is unknown. Mods missing from the mapping are unknown.
    """

    def __init__(
        self,
        installed: Iterable[int],
        requirements: Mapping[int, Optional[ModRequirements]],
    ):
        self._installed = frozenset(installed)
        self._requirements: Dict[int, ModRequirements] = {
            mod_id: reqs
            for mod_id, reqs in requirements.items()
            if mod_id in self._installed and reqs is not None
        }
        self._dependencies: Dict[int, FrozenSet[int]] = {}
        self._missing: Dict[int, FrozenSet[int]] = {}
        dependents: Dict[int, set] = {}
        for mod_id, reqs in self._requirements.items():
            declared = reqs.mod_ids - {mod_id}
            present = declared & self._installed
            self._dependencies[mod_id] = frozenset(present)
            self._missing[mod_id] = frozenset(declared - present)
            for dependency in present:
                dependents.setdefault(dependency, set()).add(mod_id)
        self._dependents = {mod_id: frozenset(ids) for mod_id, ids in dependents.items()}

    def dependencies(self, mod_id: int) -> FrozenSet[int]:
        """Installed mods this one declares as requirements."""
        return self._dependencies.get(mod_id, frozenset())

    def dependents(self, mod_id: int) -> FrozenSet[int]:
        """Installed mods that declare this one as a requirement."""
        return self._dependents.get(mod_id, frozenset())

    def not_installed(self, mod_id: int) -> FrozenSet[int]:
        """Declared requirements not found among the installed mods.

        Not proof of a problem: a requirement may live outside MO2 (such as a
        script extender in the game folder) or have been installed by hand.
        """
        return self._missing.get(mod_id, frozenset())

    def dependencies_known(self, mod_id: int) -> bool:
        """Whether ``dependencies`` and ``not_installed`` are the full answer."""
        reqs = self._requirements.get(mod_id)
        return reqs is not None and reqs.complete

    def dependents_known(self, mod_id: int) -> bool:
        """Whether ``dependents`` is the full answer.

        Any installed mod whose requirements are unknown or incomplete could be
        a dependent we can't see, so one gap makes every answer inexact.
        """
        return mod_id in self._installed and all(
            self.dependencies_known(other) for other in self._installed
        )
