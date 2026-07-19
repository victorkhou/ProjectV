"""
Shield System — regenerating damage-absorbing shields from Shield Generators.

A Shield Generator (``shield_generator`` capability) projects a shield onto the
OWNER's buildings within a level-scaled Chebyshev radius:

    radius      = balance.shield_base_radius + (level - 1)   (L1 = 2 → a 5x5 area)
    shield_max  = balance.shield_hp_fraction * level * (covered building's hp_max)
                  (L1 = 25%, L2 = 50%, L4 = 100%, L5 = 125% of each building's HP)

A shield is a second HP bar drained BEFORE HP: the combat engine's
``_apply_damage`` spends ``db.shield`` first (see CombatEngine). This system owns
the other half:

  * :meth:`refresh` recomputes every building's ``db.shield_max`` (and clamps
    ``db.shield``) from the generators currently covering it. Overlapping
    generators do NOT stack — a building takes the single LARGEST covering
    shield (``max``). Called on build/upgrade/destroy and defensively on a
    cadence, so a building always reflects the live generator layout.
  * :meth:`process_tick` regenerates each shielded building's ``db.shield``
    toward ``db.shield_max`` at ``shield_regen_percent`` every
    ``shield_regen_interval_ticks`` (default 1% per 5 ticks), with a sub-point
    accumulator so small rates aren't lost to integer truncation.

Framework-free: operates on the building objects the tick loop already passes,
reading/writing plain ``db`` attributes via the shared value-based accessors.
Grouping is per (owner, planet) so one player's generator never shields another
player's — or an NPC base's — buildings.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from world.constants import SHIELD_GENERATOR
from world.event_bus import (
    BUILDING_CONSTRUCTED,
    BUILDING_DESTROYED,
    BUILDING_UPGRADED,
    CONSTRUCTION_COMPLETED,
)
from world.systems.base_system import BaseSystem
from world.utils import (
    building_has_capability,
    chebyshev_distance,
    get_obj_attr,
)

logger = logging.getLogger("mygame.shield_system")


class ShieldSystem(BaseSystem):
    """Computes and regenerates building shields from Shield Generators.

    Args:
        registry: The :class:`DataRegistry` (hot-tunable ``shield_*`` balance +
            the capability provider for ``building_has_capability``).
        event_bus: The shared :class:`EventBus`.
    """

    def __init__(self, registry, event_bus) -> None:
        super().__init__(registry, event_bus)
        # Recompute shields when the building layout changes so a new/upgraded/
        # destroyed generator (or a building placed under one) takes effect
        # immediately, not only on the next periodic sweep. Each event re-refreshes
        # the affected OWNER's buildings (a generator change ripples to every
        # neighbour it covers, so the whole owner set is recomputed).
        if event_bus is not None:
            for evt in (BUILDING_CONSTRUCTED, CONSTRUCTION_COMPLETED,
                        BUILDING_UPGRADED, BUILDING_DESTROYED):
                event_bus.subscribe(evt, self._on_building_changed)

    def _on_building_changed(self, event_name: str = "", building: Any = None,
                             player: Any = None, **kwargs) -> None:
        """Refresh the affected owner's building shields on a layout change.

        BUILDING_DESTROYED fires BEFORE the building is deleted, so the roster
        still contains it — exclude it here so a destroyed generator immediately
        stops shielding its neighbours (rather than waiting for the periodic
        sweep to drop it once the DB row is gone).
        """
        owner = player if player is not None else get_obj_attr(building, "owner")
        if owner is None or not hasattr(owner, "get_buildings"):
            return
        try:
            roster = list(owner.get_buildings() or [])
            if event_name == BUILDING_DESTROYED and building is not None:
                dead_id = getattr(building, "id", None)
                roster = [
                    b for b in roster
                    if not (b is building
                            or (dead_id is not None and getattr(b, "id", None) == dead_id))
                ]
            self.refresh(roster)
        except Exception:  # noqa: BLE001 - a refresh failure must not break the event bus
            logger.exception("Shield refresh on building change failed")

    # ------------------------------------------------------------------ #
    #  Timing gate
    # ------------------------------------------------------------------ #

    def should_regen_this_tick(self, tick_number: int) -> bool:
        """True when shield regen applies on *tick_number*.

        Only when regen is enabled (positive percent + interval) AND the tick
        lands on the ``shield_regen_interval_ticks`` boundary. The tick loop
        checks this before assembling the building list so an off-interval tick
        skips the work entirely.
        """
        bal = getattr(self.registry, "balance", None)
        percent = float(getattr(bal, "shield_regen_percent", 0.0) or 0.0)
        interval = int(getattr(bal, "shield_regen_interval_ticks", 0) or 0)
        if percent <= 0 or interval <= 0:
            return False
        return tick_number % interval == 0

    # ------------------------------------------------------------------ #
    #  Shield capacity (recompute from covering generators)
    # ------------------------------------------------------------------ #

    def refresh_owners(self, active_buildings: Iterable[Any]) -> None:
        """Recompute shields for the owners of *active_buildings*, full-roster.

        The tick loop's periodic safety-net sweep. The build/upgrade/destroy
        event hooks keep shields current on the normal paths, but a building
        that comes into existence WITHOUT firing one of those events — an admin
        ``@building spawn``, or a building predating the Shield Generator
        feature — would otherwise never receive a shield. Running this each
        regen interval makes shields self-healing regardless of creation path.

        For every distinct owner with at least one building in
        *active_buildings*, refreshes that owner's ENTIRE roster
        (``owner.get_buildings()``). It deliberately does NOT refresh from the
        chunk-active subset: a covered building and the generator shielding it
        must be considered together, and a generator just outside the active
        chunks must not be dropped from the pass (which would spuriously clamp a
        covered building's ``shield_max`` to 0). Best-effort per owner — one
        failed roster query never aborts the sweep.
        """
        seen: set = set()
        for b in active_buildings or ():
            owner = get_obj_attr(b, "owner")
            owner_id = getattr(owner, "id", None) if owner is not None else None
            if owner_id is None or owner_id in seen:
                continue
            seen.add(owner_id)
            if not hasattr(owner, "get_buildings"):
                continue
            try:
                self.refresh(list(owner.get_buildings() or []))
            except Exception:  # noqa: BLE001 - one owner never breaks the sweep
                logger.exception(
                    "Shield refresh sweep failed for owner %s", owner_id
                )

    def generator_radius(self, level: int) -> int:
        """Chebyshev coverage radius for a generator at *level*."""
        base = int(getattr(self.registry.balance, "shield_base_radius", 2) or 0)
        return max(0, base + (max(1, int(level or 1)) - 1))

    def refresh(self, buildings: Iterable[Any]) -> None:
        """Recompute ``db.shield_max`` for every building in *buildings*.

        For each building, the shield capacity is the LARGEST shield any of the
        owner's same-planet Shield Generators covering its tile would grant
        (overlaps take the max, they don't stack). A building covered by no
        generator has ``shield_max`` 0 (and its ``shield`` is cleared).
        ``db.shield`` is clamped to the new max — a shrunk/removed generator
        immediately caps the current shield, but never raises it (regen does).

        Idempotent: safe to call repeatedly (build/upgrade/destroy hooks + a
        periodic sweep). One pass, no DB queries beyond the passed roster.
        """
        buildings = [b for b in (buildings or ()) if b is not None]
        if not buildings:
            return

        # Collect the active generators once: (owner_id, planet) -> list of
        # (gx, gy, radius, level).
        generators: dict[tuple, list[tuple[int, int, int, int]]] = {}
        for b in buildings:
            if not self._is_generator(b):
                continue
            # A generator under construction / offline projects nothing.
            if get_obj_attr(b, "under_construction", False):
                continue
            if getattr(b, "is_offline", False) or get_obj_attr(b, "offline", False):
                continue
            key = self._group_key(b)
            coords = self._coords(b)
            if key is None or coords is None:
                continue
            level = int(get_obj_attr(b, "building_level", 1) or 1)
            generators.setdefault(key, []).append(
                (coords[0], coords[1], self.generator_radius(level), level)
            )

        frac = float(getattr(self.registry.balance, "shield_hp_fraction", 0.0) or 0.0)

        for b in buildings:
            self._refresh_one(b, generators, frac)

    def _refresh_one(self, building: Any, generators: dict, frac: float) -> None:
        """Set one building's ``db.shield_max`` from its covering generators.

        Newly-available capacity comes online CHARGED: when ``shield_max`` rises
        (a generator built/upgraded near this building, or this building placed
        under an existing generator), the increase is added to the current
        ``shield`` so the shield powers on ready — not slowly filled from zero.
        When ``shield_max`` falls (generator lost/downgraded) the live shield is
        clamped down. A static layout recomputes the SAME ``shield_max`` (no
        delta), so a periodic refresh never spuriously refills a drained shield
        — combat damage is only recovered via slow regen.
        """
        db = getattr(building, "db", None)
        if db is None:
            return
        key = self._group_key(building)
        coords = self._coords(building)
        best_max = 0
        if key is not None and coords is not None and frac > 0:
            hp_max = int(get_obj_attr(building, "hp_max", 0) or 0)
            if hp_max > 0:
                bx, by = coords
                for gx, gy, radius, glevel in generators.get(key, ()):  # same owner+planet
                    if chebyshev_distance(bx, by, gx, gy) <= radius:
                        # Overlap rule: take the single strongest shield.
                        candidate = int(hp_max * frac * glevel)
                        if candidate > best_max:
                            best_max = candidate

        prev_max = int(getattr(db, "shield_max", 0) or 0)
        cur = max(0, int(getattr(db, "shield", 0) or 0))
        if best_max > prev_max:
            # New capacity powers on charged: bank the increase.
            cur = min(best_max, cur + (best_max - prev_max))
        elif best_max < prev_max:
            # Lost capacity: clamp the live shield down.
            cur = min(cur, best_max)
        if best_max != prev_max:
            db.shield_max = best_max
        db.shield = cur
        if best_max == 0:
            db.shield_regen_accumulator = 0.0

    # ------------------------------------------------------------------ #
    #  Regeneration
    # ------------------------------------------------------------------ #

    def process_tick(self, buildings: Iterable[Any], tick_number: int) -> None:
        """Regenerate each shielded building's ``db.shield`` one interval's worth.

        Only on the ``shield_regen_interval_ticks`` boundary. A building
        regenerates when it has a positive ``shield_max`` and its ``shield`` is
        below it. Re-checks the timing gate so it's correct when called
        unconditionally (tests).
        """
        if not self.should_regen_this_tick(tick_number):
            return
        percent = float(getattr(self.registry.balance, "shield_regen_percent", 0.0) or 0.0)
        for b in buildings or ():
            try:
                self._regen_one(b, percent)
            except Exception:  # noqa: BLE001 - one bad building never breaks the tick
                logger.exception("Shield regen failed for %s", getattr(b, "key", "?"))

    def _regen_one(self, building: Any, percent: float) -> None:
        db = getattr(building, "db", None)
        if db is None:
            return
        shield_max = int(getattr(db, "shield_max", 0) or 0)
        shield = int(getattr(db, "shield", 0) or 0)
        if shield_max <= 0 or shield >= shield_max:
            return
        if shield < 0:
            shield = 0
        gain = shield_max * (percent / 100.0)
        acc = float(getattr(db, "shield_regen_accumulator", 0.0) or 0.0) + gain
        whole = int(acc)
        if whole <= 0:
            db.shield_regen_accumulator = acc
            return
        new_shield = min(shield_max, shield + whole)
        applied = new_shield - shield
        db.shield = new_shield
        db.shield_regen_accumulator = 0.0 if new_shield >= shield_max else (acc - applied)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _is_generator(self, building: Any) -> bool:
        return building_has_capability(
            building, SHIELD_GENERATOR, provider=self.registry
        )

    @staticmethod
    def _coords(building: Any) -> tuple[int, int] | None:
        x = get_obj_attr(building, "coord_x")
        y = get_obj_attr(building, "coord_y")
        if x is None or y is None:
            return None
        try:
            return int(x), int(y)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _group_key(building: Any) -> tuple | None:
        """Return the (owner_id, planet) a shield is scoped to, or None.

        Shields are per player per planet: a generator only covers its owner's
        buildings on the same planet. An ownerless building (no owner id) can
        neither project nor receive a shield.
        """
        owner = get_obj_attr(building, "owner")
        owner_id = getattr(owner, "id", None) if owner is not None else None
        if owner_id is None:
            return None
        planet = get_obj_attr(building, "coord_planet")
        return (owner_id, planet)
