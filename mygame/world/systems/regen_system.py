"""
Regen System — passive HP regeneration for players and agents.

Each tick, living player/agent ``CombatEntity`` objects slowly heal back toward
their ``hp_max``. Buildings do NOT passively heal — they must be repaired — so
this system is intentionally scoped to players and agents; the tick loop passes
it only those entities.

The rate is hot-tunable balance (``hp_regen_percent`` of ``hp_max`` every
``hp_regen_interval_ticks`` ticks — default 1% per 2 ticks) and is scaled
per-entity by a ``regen_multiplier`` (default 1.0). That multiplier is the
extension point for effects determined later — heal-rate technologies, powerups,
etc.: they set/raise ``db.regen_multiplier`` (or register a modifier provider)
and this system already honors it. Additional global modifier providers can be
injected via :meth:`add_modifier_provider`.

Sub-integer healing (e.g. 0.5 HP/tick) is accumulated in
``db.hp_regen_accumulator`` so small rates still restore whole HP points over
time rather than being lost to integer truncation each tick.

The Field Hospital heal aura (item-loot-economy §7, R10.3) also lives here:
while an entity's owning player stands on their OWN, OPERATIONAL ``heal_aura``
building's tile, :meth:`RegenSystem._tile_heal_bonus` adds a flat
``1 + (level - 1) // 2`` HP per regen interval on top of the percent-based
regen. Riding the regen machinery (rather than a separate tick step) means the
aura automatically obeys the same rules: the interval cadence, no healing while
dead/incapacitated, the ``hp_max`` clamp, and the fractional accumulator. The
flip side — documented deliberately — is that the aura shares the regen
cadence gate: if passive regen is globally disabled (``hp_regen_percent`` or
``hp_regen_interval_ticks`` non-positive), the Field Hospital is inert too.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from world.systems.base_system import BaseSystem

logger = logging.getLogger("mygame.regen_system")


class RegenSystem(BaseSystem):
    """Applies passive HP regeneration to players and agents each tick.

    Args:
        registry: The :class:`DataRegistry` (for the hot-tunable balance).
        event_bus: The shared :class:`EventBus`.
    """

    def __init__(self, registry, event_bus) -> None:
        super().__init__(registry, event_bus)
        # Zero-arg-per-entity callables ``(entity) -> float`` whose returns are
        # multiplied into the base rate. Seeded empty; heal-rate tech/powerups
        # can register here (in addition to the per-entity db.regen_multiplier).
        self._modifier_providers: list[Callable[[Any], float]] = []

    def add_modifier_provider(self, provider: Callable[[Any], float]) -> None:
        """Register a global regen-rate modifier provider.

        *provider* is called as ``provider(entity)`` and must return a
        non-negative multiplier; all providers' returns (and the entity's
        ``db.regen_multiplier``) multiply together with the base rate. This is
        how later features (heal-rate technologies, powerups) hook in without
        editing this system.
        """
        self._modifier_providers.append(provider)

    # ------------------------------------------------------------------ #
    #  Tick processing
    # ------------------------------------------------------------------ #

    def should_regen_this_tick(self, tick_number: int) -> bool:
        """Return True if passive regen applies on *tick_number*.

        True only when regen is enabled (positive percent and interval) AND
        *tick_number* lands on the ``hp_regen_interval_ticks`` boundary. The
        tick loop calls this BEFORE enumerating the (potentially large) agent
        roster, so an off-interval tick skips that DB scan entirely rather than
        doing the work and discarding it. This is the single source of truth
        for the timing gate — ``process_tick`` consults it too.
        """
        balance = getattr(self.registry, "balance", None)
        percent = float(getattr(balance, "hp_regen_percent", 0.0) or 0.0)
        interval = int(getattr(balance, "hp_regen_interval_ticks", 0) or 0)
        if percent <= 0 or interval <= 0:
            return False
        return tick_number % interval == 0

    def process_tick(self, entities: Iterable[Any], tick_number: int) -> None:
        """Regenerate HP for each eligible entity in *entities*.

        Only applies on the ``hp_regen_interval_ticks`` boundary (the "per N
        ticks" period). An entity regenerates when it is alive (hp > 0), not
        incapacitated, and below ``hp_max``. Dead/incapacitated entities heal
        through respawn, not passive regen, so they are skipped.

        Callers may pre-check :meth:`should_regen_this_tick` to avoid assembling
        *entities* on off-interval ticks; this method re-checks the same gate so
        it stays correct when called unconditionally (e.g. in tests).

        Args:
            entities: The players and/or agents to consider (buildings are
                intentionally not passed by the tick loop).
            tick_number: The current game tick.
        """
        if not self.should_regen_this_tick(tick_number):
            return

        balance = getattr(self.registry, "balance", None)
        percent = float(getattr(balance, "hp_regen_percent", 0.0) or 0.0)

        for entity in entities or ():
            try:
                self._regen_entity(entity, percent)
            except Exception:
                logger.exception(
                    "Regen failed for %s", getattr(entity, "key", "?")
                )

    def _regen_entity(self, entity: Any, percent: float) -> None:
        """Apply one interval's worth of regen to a single entity."""
        db = getattr(entity, "db", None)
        if db is None:
            return

        # Skip the dead/incapacitated — they recover via respawn, not regen.
        if getattr(db, "incapacitated", False):
            return

        hp = int(getattr(db, "hp", 0) or 0)
        hp_max = int(getattr(db, "hp_max", 0) or 0)
        if hp <= 0 or hp_max <= 0 or hp >= hp_max:
            return

        multiplier = self._regen_multiplier(entity)
        tile_heal = self._tile_heal_bonus(entity)
        if multiplier <= 0 and tile_heal <= 0:
            return

        # Base heal for this interval, scaled by all modifiers, plus the flat
        # Field Hospital heal aura (R10.3) — additive, NOT scaled by the regen
        # multiplier (the facility heals you; it isn't your metabolism, so a
        # zeroed regen_multiplier doesn't switch the hospital off). Accumulate
        # the fractional remainder so sub-1-HP rates still heal over time.
        heal_amount = hp_max * (percent / 100.0) * multiplier + tile_heal
        accumulated = float(getattr(db, "hp_regen_accumulator", 0.0) or 0.0)
        accumulated += heal_amount

        whole = int(accumulated)
        if whole <= 0:
            # Not enough banked yet for a whole HP — keep accumulating.
            db.hp_regen_accumulator = accumulated
            return

        new_hp = min(hp_max, hp + whole)
        applied = new_hp - hp
        db.hp = new_hp
        # Keep the sub-HP remainder; drop any surplus once at full HP.
        db.hp_regen_accumulator = 0.0 if new_hp >= hp_max else (accumulated - applied)

    def _tile_heal_bonus(self, entity: Any) -> int:
        """Field Hospital heal aura at *entity*'s tile (item-loot-economy R10.3).

        The Field Hospital term of the passive heal, delegated to the ONE
        shared aura read :func:`world.utils.tile_aura_level` (DRY H2 — also
        used by the Sniper Nest range and Watchtower vision auras) with the
        ``HEAL_AURA`` capability: grants ``1 + (level - 1) // 2`` → L1 +1,
        L3 +2, L5 +3 extra HP per regen interval while the entity's owning
        player stands on their OWN, OPERATIONAL heal-aura building's tile.

        Owner attribution uses the helper's shared owning-player resolver: a
        player is its own owner; an owner's AGENT on the tile also benefits
        (the Sniper Nest attribution shape), so an enemy or a stranger
        camping the tile gets nothing. Strictly ON-TILE and OWNER-ONLY —
        positional, not permanent. Returns 0 in every other case and never
        raises, so a bad building read can never break the regen tick.
        """
        from world.constants import HEAL_AURA
        from world.utils import tile_aura_level

        return tile_aura_level(entity, HEAL_AURA, provider=self.registry)

    def _regen_multiplier(self, entity: Any) -> float:
        """Combined regen multiplier for *entity* (>= 0).

        The product of the entity's own ``db.regen_multiplier`` (default 1.0)
        and every registered modifier provider's return. This is the single
        place later heal-rate effects feed into.
        """
        db = getattr(entity, "db", None)
        multiplier = 1.0
        if db is not None:
            raw = getattr(db, "regen_multiplier", None)
            if raw is not None:
                try:
                    multiplier = float(raw)
                except (TypeError, ValueError):
                    multiplier = 1.0

        for provider in self._modifier_providers:
            try:
                multiplier *= float(provider(entity))
            except Exception:
                logger.exception("Regen modifier provider failed")

        return max(0.0, multiplier)
