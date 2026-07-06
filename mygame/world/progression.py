"""
Shared progression curve helper for the RTS Combat Overworld game.

Pure-Python module-level helper that holds the precomputed level->XP
threshold table. This is the single source of truth for the XP curve so
that both ``CombatEntity`` (``typeclasses/combat_entity.py``) and
``RankSystem`` (``world/systems/rank_system.py``) derive levels from one
place without duplicating the linear-interpolation logic.

The table is built from ``DataRegistry.ranks`` and the tuning constants
(``LEVELS_PER_RANK``, ``MAX_LEVEL``, ``FINAL_RANK_XP_PER_LEVEL``), exactly
reproducing the curve that ``RankSystem._rebuild_thresholds`` produced.

This module must stay free of Evennia imports and must not import
``RankSystem`` at module load time. To avoid a circular import
(``rank_system`` -> ``progression`` -> ``rank_system``), the level->rank
rule (``rank_from_level``) is imported lazily inside ``rank_for_level``;
only ``world.constants`` is imported at the top level.

"""

from __future__ import annotations

from typing import Any

from world.constants import (
    MAX_LEVEL,
    LEVELS_PER_RANK,
    FINAL_RANK_XP_PER_LEVEL,
)

#: Precomputed level->XP threshold table. Index 0 is unused; valid
#: indices are 1..MAX_LEVEL. Empty until ``build_thresholds`` runs.
_level_thresholds: list[int] = []


def build_thresholds(ranks: Any) -> list[int]:
    """Build and cache the level->XP threshold table from registry ranks.

    Reproduces ``RankSystem._rebuild_thresholds`` EXACTLY: for each rank,
    linearly interpolate ``LEVELS_PER_RANK`` levels between consecutive rank
    ``xp_threshold`` values; the final rank uses ``FINAL_RANK_XP_PER_LEVEL``
    per level (no next rank to interpolate against).

    Idempotent: calling it repeatedly with the same ``ranks`` produces the
    same table. Call once at server start and again on hot-reload.

    Args:
        ranks: An iterable of rank definitions, each exposing ``level`` and
            ``xp_threshold`` attributes (e.g. ``DataRegistry.ranks``).

    Returns:
        The newly built threshold table (also cached module-side).
    """
    global _level_thresholds

    sorted_ranks = sorted(ranks, key=lambda r: r.level)
    thresholds = [0] * (MAX_LEVEL + 1)  # index 0 unused, 1..MAX_LEVEL

    for i, rank_def in enumerate(sorted_ranks):
        base_xp = rank_def.xp_threshold
        if i + 1 < len(sorted_ranks):
            next_xp = sorted_ranks[i + 1].xp_threshold
        else:
            # Final rank: use a fixed interval per level
            next_xp = base_xp + LEVELS_PER_RANK * FINAL_RANK_XP_PER_LEVEL

        interval = (next_xp - base_xp) / LEVELS_PER_RANK
        for sub in range(LEVELS_PER_RANK):
            lvl = (rank_def.level - 1) * LEVELS_PER_RANK + sub + 1
            if 1 <= lvl <= MAX_LEVEL:
                thresholds[lvl] = int(base_xp + sub * interval)

    _level_thresholds = thresholds
    return _level_thresholds


def is_initialized() -> bool:
    """Return ``True`` once the threshold table has been built."""
    return bool(_level_thresholds)


def _ensure_initialized() -> bool:
    """Best-effort lazy build of the threshold table.

    If the table is not yet built, attempt to obtain the singleton
    ``DataRegistry`` and build from its ranks. Used so ``CombatEntity``
    stays usable in the Evennia-stub test suite and in any uninitialized
    state. Returns ``True`` if the table is available afterwards.
    """
    if _level_thresholds:
        return True
    try:
        from world.data_registry import DataRegistry

        registry = DataRegistry.get_instance()
        if registry is not None and getattr(registry, "ranks", None):
            build_thresholds(registry.ranks)
    except Exception:
        # Registry unavailable (e.g. fast unit-test suite) — treat as
        # uninitialized and let callers apply their fallback.
        return False
    return bool(_level_thresholds)


def level_for_xp(xp: int, thresholds: list[int] | None = None) -> int:
    """Return the highest level whose threshold is <= ``xp``.

    Clamped to ``1..MAX_LEVEL``. Passing *thresholds* (a table as returned by
    ``build_thresholds``) makes this a pure function with no global reach —
    the preferred form for callers that hold a registry and for unit tests.
    When omitted, falls back to the lazily-built module curve (and to level
    ``1`` if that cannot be built).

    Args:
        xp: The combat XP to map to a level.
        thresholds: Optional explicit threshold table; when given, the module
            singleton is not consulted.
    """
    table = thresholds
    if table is None:
        if not _ensure_initialized():
            return 1
        table = _level_thresholds

    if xp is None:
        xp = 0

    best = 1
    for lvl in range(1, MAX_LEVEL + 1):
        if table[lvl] <= xp:
            best = lvl
        else:
            break
    return best


def xp_for_level(level: int, thresholds: list[int] | None = None) -> int:
    """Return the XP threshold required to reach ``level``.

    ``level`` is clamped to ``1..MAX_LEVEL``. Passing *thresholds* makes this
    a pure function with no global reach; when omitted, falls back to the
    lazily-built module curve (and to ``0`` if that cannot be built).

    Args:
        level: The target level.
        thresholds: Optional explicit threshold table; when given, the module
            singleton is not consulted.
    """
    table = thresholds
    if table is None:
        if not _ensure_initialized():
            return 0
        table = _level_thresholds
    level = max(1, min(level, MAX_LEVEL))
    return table[level]


def rank_for_level(level: int) -> int:
    """Return the rank number (1..NUM_RANKS) for ``level``.

    Delegates to ``rank_from_level`` (defined in ``rank_system``), imported
    lazily here to avoid the ``rank_system`` <-> ``progression`` circular
    import.

    """
    from world.systems.rank_system import rank_from_level

    return rank_from_level(level)
