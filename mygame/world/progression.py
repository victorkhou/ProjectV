"""
Shared progression curve — single source of truth for level->XP thresholds.

Hybrid curve: per-level XP delta starts at ``xp_curve_base_delta`` (40) for
L1→L2, grows by ``xp_curve_early_ratio`` (+20%) per level through the knee
(L20), then by ``xp_curve_late_ratio`` (+5%) to MAX_LEVEL (100).

``build_thresholds(ranks)`` reads curve tunables from the live balance config;
the *ranks* argument triggers a rebuild but supplies no threshold data.

Must stay free of Evennia imports. ``rank_from_level`` is imported lazily
inside ``rank_for_level`` to avoid circular imports with ``rank_system``.
"""

from __future__ import annotations

from typing import Any

from world.constants import MAX_LEVEL

#: Hybrid-curve defaults (mirrored in BalanceConfig / balance.yaml — the
#: build reads the live balance first and falls back to these).
DEFAULT_BASE_DELTA = 40
DEFAULT_EARLY_RATIO = 1.2
DEFAULT_LATE_RATIO = 1.05
DEFAULT_KNEE_LEVEL = 20

#: Precomputed level->XP threshold table. Index 0 is unused; valid
#: indices are 1..MAX_LEVEL. Empty until ``build_thresholds`` runs.
_level_thresholds: list[int] = []


def _curve_tunables() -> tuple[int, float, float, int]:
    """Read the hybrid-curve tunables from the live balance, else defaults.

    Reaches the registry via the definitions-provider choke point (the same
    lazy path ``_ensure_initialized`` uses) so a balance.yaml retune of the
    curve takes effect on the next ``build_thresholds`` call — no code edit.
    Falls back to the module defaults in stub/test contexts.
    """
    try:
        from world.adapters.registry_definitions_provider import default_balance

        bal = default_balance()
        return (
            int(getattr(bal, "xp_curve_base_delta", DEFAULT_BASE_DELTA)),
            float(getattr(bal, "xp_curve_early_ratio", DEFAULT_EARLY_RATIO)),
            float(getattr(bal, "xp_curve_late_ratio", DEFAULT_LATE_RATIO)),
            int(getattr(bal, "xp_curve_knee_level", DEFAULT_KNEE_LEVEL)),
        )
    except Exception:
        return (DEFAULT_BASE_DELTA, DEFAULT_EARLY_RATIO,
                DEFAULT_LATE_RATIO, DEFAULT_KNEE_LEVEL)


def xp_delta(level: int, *, base_delta: int | None = None,
             early_ratio: float | None = None,
             late_ratio: float | None = None,
             knee_level: int | None = None) -> int:
    """XP needed to go from ``level - 1`` to ``level``.

    ``delta(n) = base × early^(n−2)`` for ``n <= knee``;
    ``delta(n) = delta(knee) × late^(n−knee)`` beyond it.
    Level 1 costs 0 (the starting level).
    """
    if level <= 1:
        return 0
    if base_delta is None:
        base_delta, early_ratio, late_ratio, knee_level = _curve_tunables()
    if level <= knee_level:
        return round(base_delta * early_ratio ** (level - 2))
    knee_delta = base_delta * early_ratio ** (knee_level - 2)
    return round(knee_delta * late_ratio ** (level - knee_level))


def build_thresholds(ranks: Any = None) -> list[int]:
    """Build and cache the level->XP threshold table from the hybrid formula.

    The *ranks* argument is accepted for signature compatibility but supplies
    no threshold data — the curve is parameterized by the live balance tunables.

    Idempotent for fixed tunables. Call once at server start and again on
    hot-reload.

    Returns:
        The newly built threshold table (also cached module-side).
    """
    global _level_thresholds

    base_delta, early_ratio, late_ratio, knee_level = _curve_tunables()

    thresholds = [0] * (MAX_LEVEL + 1)  # index 0 unused, 1..MAX_LEVEL
    running = 0
    for lvl in range(2, MAX_LEVEL + 1):
        running += xp_delta(
            lvl, base_delta=base_delta, early_ratio=early_ratio,
            late_ratio=late_ratio, knee_level=knee_level,
        )
        thresholds[lvl] = running

    _level_thresholds = thresholds
    return _level_thresholds


def is_initialized() -> bool:
    """Return ``True`` once the threshold table has been built."""
    return bool(_level_thresholds)


def _ensure_initialized() -> bool:
    """Best-effort lazy build of the threshold table.

    The hybrid curve needs no rank data, so the lazy build always succeeds —
    it simply uses the balance tunables when a registry is live and the
    module defaults otherwise (e.g. the fast unit-test suite).
    """
    if _level_thresholds:
        return True
    try:
        build_thresholds()
    except Exception:
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
    for lvl in range(1, min(MAX_LEVEL, len(table) - 1) + 1):
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
    level = max(1, min(level, MAX_LEVEL, len(table) - 1))
    return table[level]


def rank_for_level(level: int) -> int:
    """Return the rank number (1..NUM_RANKS) for ``level``.

    Delegates to ``rank_from_level`` (defined in ``rank_system``), imported
    lazily here to avoid the ``rank_system`` <-> ``progression`` circular
    import.

    """
    from world.systems.rank_system import rank_from_level

    return rank_from_level(level)
