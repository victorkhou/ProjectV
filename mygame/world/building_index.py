"""
Building-index generation counter for cheap tick-loop cache invalidation.

The game tick searches every Building each second (a DB tag-search) to compute
the active set. The result only changes when a building is created or
destroyed, so ``GameTickScript`` caches the search and reuses it while the
world is unchanged. This module holds a process-wide generation counter that
the :class:`~typeclasses.objects.Building` typeclass bumps on create/delete;
the tick script re-runs the search only when the counter advances.

Framework-free (no Evennia import) so both the typeclass and the script can
import it without a cycle. The counter is in-memory only — after a server
restart it resets to 0 and the first tick simply repopulates the cache.

"""

from __future__ import annotations

_generation: int = 0


def bump() -> None:
    """Advance the building-index generation (call on building create/delete)."""
    global _generation
    _generation += 1


def generation() -> int:
    """Return the current building-index generation."""
    return _generation
