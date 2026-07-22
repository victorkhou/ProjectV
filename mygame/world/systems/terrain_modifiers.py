"""
Terrain Modifier System — the single resolution point for terrain modifiers.

Answers "what terrain modifiers apply to an entity at this coordinate?" for
every consumer (FogOfWarSystem, the in-combat movement gate, the CombatEngine,
BuildingSystem placement feedback, and the inspection/score displays), so no
consumer ever combines terrain data itself (terrain-strategy feature, Req 2).

Resolution combines, per modifier kind (vision / movement / defense):

  * the base modifier of the TerrainDef for the terrain type the planet's
    TerrainGenerator reports at (x, y) — Req 2.1;
  * for players only, the summed matching class Terrain_Affinity adjustments
    (multiple matches for the same terrain+kind sum, Req 6.2, 6.7) plus the
    completed-technology adjustment read from ``db.tech_bonuses`` under the
    structured key ``terrain_affinity:{terrain_type}:{kind}`` (Req 7.3, 7.4);
  * a sign-preserving clamp to the balance bound for that kind, applied on
    EVERY return path so consumers only ever see clamped values (Req 9.2, 9.5).

The resolver is stateless, coordinate-based, and tile-agnostic: the
occupied-vs-destination tile choice (Req 2.7 asymmetry — vision and defense
use the occupied tile, movement uses the destination tile) belongs to the
CALLERS, each of which passes the coordinate it needs. Do not unify it here.

Guiding rule (codebase convention): load time fails fast, run time fails soft.
Every runtime path here degrades to zero/base modifiers and never raises into
movement, combat, or rendering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import copysign
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from world.coordinate.terrain_generator import TerrainGenerator

logger = logging.getLogger("mygame.terrain_modifiers")

#: The three modifier kinds, matching TerrainAffinity.kind and the structured
#: ``terrain_affinity:{terrain_type}:{kind}`` tech-bonus key family.
MODIFIER_KINDS = ("vision", "movement", "defense")


@dataclass(frozen=True)
class TerrainModifiers:
    """Resolved, clamped terrain modifiers for one entity at one coordinate."""

    terrain_type: str | None  # None when no generator / resolution failed
    vision: int               # tiles, + widens / - narrows fog-of-war reveal
    movement: float           # ticks, + reduces in-combat lag / - increases it
    defense: float            # DR points, + adds / - subtracts


#: Failure-path result: no terrain resolved, all modifiers zero (Req 2.3, 2.5).
ZERO_MODIFIERS = TerrainModifiers(None, 0, 0.0, 0.0)


def _clamp(total: float, bound: float) -> float:
    """Sign-preserving clamp: |result| <= bound, sign of *total* kept (Req 9.2)."""
    if abs(total) > bound:
        return copysign(bound, total)
    return total


class TerrainModifierSystem:
    """Stateless resolver combining terrain, class, and technology modifiers.

    Args:
        registry: The DataRegistry (terrain defs, class defs, balance bounds).
        terrain_generators: Mapping of planet key -> TerrainGenerator, the same
            per-planet generators the rest of the game uses.

    No caching: ``TerrainGenerator.get_terrain`` is a pure hash computation and
    the rest is dict reads, so determinism (Req 2.4) follows from purity —
    identical inputs always produce identical outputs.
    """

    def __init__(
        self, registry: Any, terrain_generators: dict[str, TerrainGenerator]
    ) -> None:
        self._registry = registry
        self._generators = terrain_generators
        #: Affinity-read failures are logged once per system instance so a
        #: persistently broken player attribute cannot spam the log every tick.
        self._affinity_failure_logged = False

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def resolve_base(self, planet: str, x: int, y: int) -> TerrainModifiers:
        """Base terrain modifiers at (x, y) — no class/tech adjustments.

        Used for buildings (Req 2.6, 3.3, 5.3) and as the fallback path.
        Returns ``ZERO_MODIFIERS`` when no generator exists for *planet*
        (Req 2.3) or the generator's terrain type has no TerrainDef (Req 2.5).
        Every returned value is already clamped (Req 9.5).
        """
        resolved = self._base_totals(planet, x, y)
        if resolved is None:
            return ZERO_MODIFIERS
        terrain_type, totals = resolved
        return self._clamped_result(terrain_type, totals)

    def resolve_for_player(
        self, player: Any, planet: str, x: int, y: int
    ) -> TerrainModifiers:
        """Base modifiers plus the player's class and completed-tech affinity
        adjustments for the resolved terrain type (Req 2.2), clamped (Req 9.2,
        9.5).

        Any failure reading class or tech affinities degrades to base
        modifiers (Req 6.4) — logged once, never raises.
        """
        resolved = self._base_totals(planet, x, y)
        if resolved is None:
            return ZERO_MODIFIERS
        terrain_type, totals = resolved
        if player is not None:
            totals = self._apply_affinities(player, terrain_type, totals)
        return self._clamped_result(terrain_type, totals)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _base_totals(
        self, planet: str, x: int, y: int
    ) -> tuple[str, dict[str, float]] | None:
        """Resolve terrain type and base modifier totals, or None on failure.

        None covers both failure modes shared by base and player resolution:
        no generator for *planet* (Req 2.3) and a generator terrain type with
        no TerrainDef — cross-file drift (Req 2.5).
        """
        generator = self._generators.get(planet)
        if generator is None:
            return None
        terrain_type = generator.get_terrain(x, y)
        tdef = self._registry.terrain.get(terrain_type)
        if tdef is None:
            return None
        return terrain_type, {
            "vision": tdef.vision_modifier,
            "movement": tdef.movement_modifier,
            "defense": tdef.defense_modifier,
        }

    def _apply_affinities(
        self, player: Any, terrain_type: str, base: dict[str, float]
    ) -> dict[str, float]:
        """Add class + completed-tech affinity adjustments to *base* totals.

        Class affinities matching (terrain_type, kind) are summed — a class may
        define several for the same pair (Req 6.2, 6.7). Technology adjustments
        are single dict reads from ``db.tech_bonuses`` under the structured key
        the TechLabSystem writes on research completion, so in-progress research
        inherently contributes zero (Req 7.3) and same-key values already sum
        across technologies (Req 7.4). Any exception degrades to the *base*
        totals unchanged (Req 6.4), logged once per system instance.
        """
        totals = dict(base)
        try:
            cls = self._registry.get_class(player.db.player_class)
            if cls is not None:
                for affinity in cls.terrain_affinities:
                    if affinity.terrain_type == terrain_type:
                        totals[affinity.kind] += affinity.adjustment
            bonuses = getattr(player.db, "tech_bonuses", None) or {}
            for kind in MODIFIER_KINDS:
                key = f"terrain_affinity:{terrain_type}:{kind}"
                totals[kind] += bonuses.get(key, 0)
        except Exception:
            if not self._affinity_failure_logged:
                self._affinity_failure_logged = True
                logger.warning(
                    "Terrain affinity resolution failed for terrain %r; "
                    "using base modifiers.",
                    terrain_type,
                    exc_info=True,
                )
            return dict(base)
        return totals

    def _clamped_result(
        self, terrain_type: str, totals: dict[str, float]
    ) -> TerrainModifiers:
        """Clamp each total to its balance bound and build the frozen result.

        Bounds are read from the live balance config so hot-reloads apply
        immediately. ``vision`` is coerced to int AFTER clamping (truncation
        toward zero), keeping the clamp exact for fractional totals.
        """
        balance = self._registry.balance
        return TerrainModifiers(
            terrain_type=terrain_type,
            vision=int(_clamp(totals["vision"], balance.terrain_vision_bound)),
            movement=float(
                _clamp(totals["movement"], balance.terrain_movement_bound)
            ),
            defense=float(
                _clamp(totals["defense"], balance.terrain_defense_bound)
            ),
        )
