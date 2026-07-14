"""
Spawn-location resolution (state 3.1) — headquarters / place of death / random.

Turns a player's chosen spawn option into a concrete ``(planet, x, y)`` target,
with the fallback chain the spec requires. Shared by the chargen/respawn menu
(the player picks) and could back an automatic-respawn rule later.

Framework-free: all Evennia I/O (finding the player's HQ tile, reading a
planet's fixed spawn point / bounds, the random source) is injected as callables
so the resolver is pure logic and unit-testable with plain fakes. The three
spawn options and their fallbacks:

* ``SPAWN_HQ`` — the player's live HQ tile. Falls back to the planet's fixed
  spawn point when the player has no HQ, or (PvP) their HQ was destroyed / the
  base is inert.
* ``SPAWN_DEATH`` — the recorded place of death (``db.death_*``). Falls back to
  the planet spawn when the player has never died (no recorded tile) or the
  tile is no longer in bounds.
* ``SPAWN_RANDOM`` — a random valid, in-bounds tile on the planet (rejection
  sampling via the injected bounds check). Falls back to the planet spawn if no
  valid tile is found within a bounded number of attempts.

Every option ultimately falls back to the planet's fixed spawn point, so a
spawn choice never dead-ends without a target.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("mygame.spawn_resolver")

#: The three selectable spawn options (state 3.1).
SPAWN_HQ = "hq"
SPAWN_DEATH = "death"
SPAWN_RANDOM = "random"

SPAWN_OPTIONS = (SPAWN_HQ, SPAWN_DEATH, SPAWN_RANDOM)

#: Human-readable labels for the selection menu.
SPAWN_OPTION_LABELS = {
    SPAWN_HQ: "Headquarters",
    SPAWN_DEATH: "Place of death",
    SPAWN_RANDOM: "Random location",
}

#: How many random tiles to sample before giving up and using the planet spawn.
_RANDOM_MAX_ATTEMPTS = 50


class SpawnResolver:
    """Resolve a spawn *choice* to a concrete ``(planet, x, y)`` target.

    Injected collaborators (all optional so the resolver degrades gracefully and
    is testable in isolation):

    * ``planet_spawn_func(planet_key) -> (x, y) | None`` — the planet's fixed
      spawn point (from ``CoordinateSpaceDef.spawn_x/spawn_y``). The ultimate
      fallback for every option.
    * ``hq_locator_func(player, planet_key) -> (x, y) | None`` — the player's
      live HQ tile on *planet_key*, or None (no HQ / inert base).
    * ``in_bounds_func(x, y, planet_key) -> bool`` — whether a tile is on the
      map (``PlanetRegistry.is_valid_coordinate``). Used to validate a recorded
      death tile and to reject out-of-bounds random samples.
    * ``planet_size_func(planet_key) -> (width, height) | None`` — the planet's
      dimensions, for uniform random sampling.
    * ``buildings_locator_func(planet_key) -> iterable[(x, y)]`` — the tiles of
      every building on *planet_key*, so a RANDOM spawn can stay clear of them
      (Chebyshev >= ``min_building_distance``). Optional: without it, random
      sampling only checks bounds (no building-distance constraint).
    * ``min_building_distance`` — the minimum Chebyshev distance a random spawn
      must keep from any building (default from the game constant).
    * ``rng`` — a ``random.Random`` for deterministic tests (defaults to a real
      one).
    """

    def __init__(
        self,
        planet_spawn_func: Callable[[str], tuple[int, int] | None] | None = None,
        hq_locator_func: Callable[[Any, str], tuple[int, int] | None] | None = None,
        in_bounds_func: Callable[[int, int, str], bool] | None = None,
        planet_size_func: Callable[[str], tuple[int, int] | None] | None = None,
        buildings_locator_func: Callable[[str], Any] | None = None,
        min_building_distance: int | None = None,
        rng: Any = None,
    ) -> None:
        self._planet_spawn_func = planet_spawn_func
        self._hq_locator_func = hq_locator_func
        self._in_bounds_func = in_bounds_func
        self._planet_size_func = planet_size_func
        self._buildings_locator_func = buildings_locator_func
        if min_building_distance is None:
            from world.constants import RANDOM_SPAWN_MIN_BUILDING_DISTANCE
            min_building_distance = RANDOM_SPAWN_MIN_BUILDING_DISTANCE
        self._min_building_distance = int(min_building_distance)
        if rng is None:
            import random
            rng = random.Random()
        self._rng = rng

    # ------------------------------------------------------------------ #
    #  Setters (composition root)
    # ------------------------------------------------------------------ #

    def set_planet_spawn_func(self, fn) -> None:
        self._planet_spawn_func = fn

    def set_hq_locator_func(self, fn) -> None:
        self._hq_locator_func = fn

    def set_in_bounds_func(self, fn) -> None:
        self._in_bounds_func = fn

    def set_planet_size_func(self, fn) -> None:
        self._planet_size_func = fn

    def set_buildings_locator_func(self, fn) -> None:
        self._buildings_locator_func = fn

    # ------------------------------------------------------------------ #
    #  Resolution
    # ------------------------------------------------------------------ #

    def resolve(
        self, player: Any, choice: str, planet_key: str,
    ) -> tuple[str, int, int] | None:
        """Resolve *choice* to a concrete ``(planet, x, y)`` spawn target.

        Applies the per-option fallback chain, ending at the planet's fixed
        spawn point. Returns ``None`` only when even the planet spawn is
        unavailable (misconfigured planet) — the caller then leaves the player
        where they are.
        """
        if choice == SPAWN_HQ:
            hq = self._hq_tile(player, planet_key)
            if hq is not None:
                return (planet_key, hq[0], hq[1])
        elif choice == SPAWN_DEATH:
            death = self._death_tile(player, planet_key)
            if death is not None:
                return death  # already a (planet, x, y) — death may be off-planet
        elif choice == SPAWN_RANDOM:
            rand = self._random_tile(planet_key)
            if rand is not None:
                return (planet_key, rand[0], rand[1])
        # Fallback (unknown/failed choice): the planet's fixed spawn point.
        return self._planet_spawn(planet_key)

    # ------------------------------------------------------------------ #
    #  Per-option helpers (each returns None on miss -> caller falls back)
    # ------------------------------------------------------------------ #

    def _hq_tile(self, player, planet_key) -> tuple[int, int] | None:
        if self._hq_locator_func is None:
            return None
        try:
            hq = self._hq_locator_func(player, planet_key)
        except Exception:  # noqa: BLE001 - a lookup failure just falls back
            return None
        if hq is None:
            return None
        try:
            return (int(hq[0]), int(hq[1]))
        except (TypeError, ValueError, IndexError):
            return None

    def _death_tile(self, player, planet_key) -> tuple[str, int, int] | None:
        """Return the recorded place of death as ``(planet, x, y)``, or None.

        The death may have been on a different planet than *planet_key*
        (the player picked "place of death" while the menu defaults to their
        current planet), so this returns the FULL target including its planet.
        Validates against bounds when a bounds func is wired.
        """
        db = getattr(player, "db", None)
        if db is None:
            return None
        dx = getattr(db, "death_x", None)
        dy = getattr(db, "death_y", None)
        dplanet = getattr(db, "death_planet", None) or planet_key
        if dx is None or dy is None:
            return None
        try:
            dx, dy = int(dx), int(dy)
        except (TypeError, ValueError):
            return None
        if not self._is_in_bounds(dx, dy, dplanet):
            return None
        return (dplanet, dx, dy)

    def _random_tile(self, planet_key) -> tuple[int, int] | None:
        """Return a random in-bounds tile via rejection sampling, or None.

        Prefers a tile at least ``min_building_distance`` (Chebyshev) from every
        building, so a "random location" spawn lands in open ground rather than
        next to a base. This is best-effort: if the attempt budget is exhausted
        without a far-enough tile (e.g. a cramped, building-dense map), it makes
        a second pass that only checks bounds — the constraint is relaxed rather
        than dead-ending the spawn.
        """
        if self._planet_size_func is None:
            return None
        try:
            size = self._planet_size_func(planet_key)
        except Exception:  # noqa: BLE001
            return None
        if not size:
            return None
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None

        building_tiles = self._building_tiles(planet_key)

        # Pass 1: honor the min-building-distance constraint (if we have both a
        # distance and building data). Pass 2: bounds-only fallback so a dense
        # map never leaves the player without a random tile.
        enforce_distance = bool(building_tiles) and self._min_building_distance > 0
        for enforce in (True, False) if enforce_distance else (False,):
            for _ in range(_RANDOM_MAX_ATTEMPTS):
                x = self._rng.randint(0, width - 1)
                y = self._rng.randint(0, height - 1)
                if not self._is_in_bounds(x, y, planet_key):
                    continue
                if enforce and not self._far_from_buildings(x, y, building_tiles):
                    continue
                return (x, y)
        return None  # no valid tile found in the budget -> caller falls back

    def _building_tiles(self, planet_key) -> list:
        """Return the list of ``(x, y)`` building tiles on *planet_key*, or []."""
        if self._buildings_locator_func is None:
            return []
        try:
            tiles = self._buildings_locator_func(planet_key)
        except Exception:  # noqa: BLE001 - a lookup failure just skips the constraint
            return []
        out = []
        for t in tiles or ():
            try:
                out.append((int(t[0]), int(t[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _far_from_buildings(self, x: int, y: int, building_tiles) -> bool:
        """True if ``(x, y)`` is >= min_building_distance from every building."""
        d = self._min_building_distance
        for bx, by in building_tiles:
            if max(abs(x - bx), abs(y - by)) < d:
                return False
        return True

    def _planet_spawn(self, planet_key) -> tuple[str, int, int] | None:
        if self._planet_spawn_func is None:
            return None
        try:
            spawn = self._planet_spawn_func(planet_key)
        except Exception:  # noqa: BLE001
            return None
        if spawn is None:
            return None
        try:
            return (planet_key, int(spawn[0]), int(spawn[1]))
        except (TypeError, ValueError, IndexError):
            return None

    def _is_in_bounds(self, x, y, planet_key) -> bool:
        """Bounds check that falls OPEN when unwired (never rejects in tests)."""
        if self._in_bounds_func is None:
            return True
        try:
            return bool(self._in_bounds_func(int(x), int(y), planet_key))
        except Exception:  # noqa: BLE001 - unknown planet / bad coords
            return False
