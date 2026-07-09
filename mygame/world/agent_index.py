"""
Agent-index generation counter for cheap tick-loop cache invalidation.

The game tick feeds passive per-entity systems (e.g. HP regen) the full agent
roster, which requires a DB tag-search for every NPC tagged ``agent``. That set
only changes when an agent NPC is created or deleted, so ``GameTickScript``
caches the search and reuses it while the roster is unchanged. This module holds
a process-wide generation counter that the :class:`~typeclasses.npcs.NPC`
typeclass bumps on create/delete; the tick script re-runs the search only when
the counter advances.

Mirrors :mod:`world.building_index`. Framework-free (no Evennia import) so both
the typeclass and the script can import it without a cycle. The counter is
in-memory only — after a server restart it resets to 0 and the first tick simply
repopulates the cache.

"""

from __future__ import annotations

_generation: int = 0


def bump() -> None:
    """Advance the agent-index generation (call on agent create/delete)."""
    global _generation
    _generation += 1


def generation() -> int:
    """Return the current agent-index generation."""
    return _generation
