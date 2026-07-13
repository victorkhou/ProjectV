"""
CombatEntity mixin — shared combat state for players and NPCs.

Pure Python mixin with NO Evennia base class. Expects the host class
to provide ``self.db.*`` (Evennia AttributeHandler).

"""

import logging

logger = logging.getLogger("evennia.typeclasses.combat_entity")

# Default number of ticks an entity stays incapacitated before respawn.
DEFAULT_RESPAWN_TICKS = 10


class CombatEntity:
    """Mixin providing shared combat state for players and NPCs.

    Expects the host class to provide ``self.db.*`` via Evennia's
    ``AttributeHandler``.  Both ``DefaultCharacter`` and
    ``DefaultObject`` satisfy this requirement.
    """

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    def at_combat_entity_init(self):
        """Called from ``at_object_creation()`` of the host typeclass."""
        self.db.hp = 100
        self.db.hp_max = 100
        # Portion of ``hp_max`` currently contributed by equipped ``max_hp``
        # gear. Tracked so the contribution can be backed out and refolded on
        # any equip/unequip without disturbing the base (or the tech-tree
        # bonus, which also raises ``hp_max``). See ``refresh_equipment_hp_max``.
        self.db.equipment_hp_bonus = 0
        self.db.equipment_slots = {}       # slot_name -> item_ref
        self.db.incapacitated = False
        self.db.respawn_timer = 0          # ticks remaining
        self.db.respawn_location = None    # room ref or None
        # Progression state (owner-agnostic; derived from combat_xp).
        self.db.combat_xp = 0
        self.db.level = 1
        self.db.rank_level = 1
        # Cosmetic lifetime kill/death tallies (players and agents). Shown on
        # the score sheet; NOT progression inputs — they never feed
        # XP/level/owner cap.
        self.db.kills = 0
        self.db.deaths = 0

    # ------------------------------------------------------------------ #
    #  Progression (Entity_Progression) — owner-agnostic, pure Python
    # ------------------------------------------------------------------ #

    def award_xp(self, amount: int) -> int:
        """Add positive XP to ``db.combat_xp`` and recompute level/rank.

        A non-positive *amount* is a no-op. Reads ``self.db.combat_xp or 0``
        so legacy entities without the attribute start from 0. Returns the
        resulting ``combat_xp``.
        """
        current = self.db.combat_xp or 0
        if amount <= 0:
            return current
        self.db.combat_xp = current + amount
        self.recompute_progression()
        return self.db.combat_xp

    def deduct_xp(self, amount: int) -> int:
        """Subtract XP from ``db.combat_xp``, floored at 0, recompute level/rank.

        A non-positive *amount* is a no-op. Reads ``self.db.combat_xp or 0``
        so legacy entities without the attribute start from 0. Returns the
        resulting ``combat_xp``.
        """
        current = self.db.combat_xp or 0
        if amount <= 0:
            return current
        self.db.combat_xp = max(0, current - amount)
        self.recompute_progression()
        return self.db.combat_xp

    def recompute_progression(self) -> None:
        """Recompute ``db.level`` and ``db.rank_level`` from ``db.combat_xp``.

        Called on every XP change, regardless of whether the values differ.
        ``db.level`` is the raw level derived from the XP curve and
        ``db.rank_level`` is the rank for that level. Uses the shared
        ``world.progression`` helper (imported lazily to keep this mixin free
        of import-order coupling).
        """
        from world import progression

        level = progression.level_for_xp(self.db.combat_xp or 0)
        self.db.level = level
        self.db.rank_level = progression.rank_for_level(level)

    def get_raw_level(self) -> int:
        """Return the raw level derived from ``combat_xp`` (owner-agnostic)."""
        from world import progression

        return progression.level_for_xp(self.get_combat_xp())

    def get_raw_rank(self) -> int:
        """Return the rank for this entity's raw level."""
        from world import progression

        return progression.rank_for_level(self.get_raw_level())

    def get_combat_xp(self) -> int:
        """Return ``db.combat_xp`` with a 0 fallback for legacy entities."""
        return self.db.combat_xp or 0

    # ------------------------------------------------------------------ #
    #  Damage / Healing
    # ------------------------------------------------------------------ #

    def take_damage(self, amount: int) -> int:
        """Reduce hp by *amount* (min 0).  Returns actual damage dealt.

        If hp reaches 0, calls :meth:`incapacitate` with
        ``DEFAULT_RESPAWN_TICKS``.
        """
        if amount < 0:
            amount = 0
        current_hp = self.db.hp
        actual = min(amount, current_hp)
        self.db.hp = current_hp - actual
        if self.db.hp <= 0:
            self.db.hp = 0
            self.incapacitate(DEFAULT_RESPAWN_TICKS)
        return actual

    def heal(self, amount: int) -> int:
        """Increase hp by *amount* (capped at hp_max).  Returns actual healing."""
        if amount < 0:
            amount = 0
        current_hp = self.db.hp
        hp_max = self.db.hp_max
        actual = min(amount, hp_max - current_hp)
        self.db.hp = current_hp + actual
        return actual

    # ------------------------------------------------------------------ #
    #  Equipment
    # ------------------------------------------------------------------ #

    @property
    def equipment(self):
        """Return a cached EquipmentHandler for this entity.

        Shared by players and NPCs so both can carry stat-modifying items
        (e.g. a ``move_speed`` bonus on an NPC). ``CombatCharacter`` may
        override this with its own handler.
        """
        if not hasattr(self, "_equipment_handler") or self._equipment_handler is None:
            from world.systems.equipment_handler import EquipmentHandler
            self._equipment_handler = EquipmentHandler(self)
        return self._equipment_handler

    def _get_move_speed_modifier(self) -> int:
        """Return the total ``move_speed`` bonus from equipped items.

        A positive value speeds the entity up (reduces its effective
        movement delay via :func:`world.constants.compute_effective_delay`).
        Returns ``0`` when the entity has no usable equipment handler or no
        equipped item provides a ``move_speed`` stat (the common case), so
        movement is unaffected by default.

        Shared by NPCs (per-tick ``advance_movement``) and players (the
        in-combat movement lag in ``CmdMove``) so both derive the modifier
        from equipment identically.
        """
        equipment = getattr(self, "equipment", None)
        if equipment is None or not hasattr(equipment, "get_stat_total"):
            return 0
        try:
            return int(equipment.get_stat_total("move_speed"))
        except Exception:
            logger.debug(
                "move_speed lookup failed for entity %s; defaulting modifier to 0",
                getattr(self, "id", "?"),
                exc_info=True,
            )
            return 0

    def refresh_equipment_hp_max(self) -> int:
        """Re-fold the equipped ``max_hp`` bonus into ``db.hp_max``.

        Recomputes the entity's max-HP ceiling from the *current* equipped set
        rather than tracking a running delta, so it stays correct across
        arbitrary equip/unequip/swap sequences (the same
        recompute-from-truth discipline used for ``move_speed``).

        The stored ``db.equipment_hp_bonus`` records how much of the current
        ``hp_max`` came from gear; this backs that out and folds in the fresh
        total (``hp_max = hp_max - old_bonus + new_bonus``). That leaves the
        base ceiling — and any tech-tree ``max_hp`` bonus, which mutates
        ``hp_max`` directly — untouched.

        Raising the ceiling (equipping) does **not** heal: current ``hp`` is
        left as-is, so gear grants headroom, not free health. Lowering the
        ceiling (unequipping) clamps current ``hp`` down to the new max so an
        entity is never left above its maximum.

        Returns the new ``db.hp_max``. Never raises into the equip path — a
        lookup failure leaves ``hp_max`` unchanged.
        """
        equipment = getattr(self, "equipment", None)
        if equipment is None or not hasattr(equipment, "get_stat_total"):
            return self.db.hp_max
        try:
            new_bonus = int(equipment.get_stat_total("max_hp"))
        except Exception:
            logger.debug(
                "max_hp lookup failed for entity %s; leaving hp_max unchanged",
                getattr(self, "id", "?"),
                exc_info=True,
            )
            return self.db.hp_max

        if new_bonus < 0:
            new_bonus = 0
        old_bonus = self.db.equipment_hp_bonus or 0
        if new_bonus == old_bonus:
            return self.db.hp_max

        base_max = (self.db.hp_max or 0) - old_bonus
        new_max = max(1, base_max + new_bonus)
        self.db.hp_max = new_max
        self.db.equipment_hp_bonus = new_bonus

        # Clamp current HP down if the ceiling dropped (e.g. unequip). Never
        # heals on a raise — headroom only.
        if (self.db.hp or 0) > new_max:
            self.db.hp = new_max
        return new_max

    # ------------------------------------------------------------------ #
    #  Status queries
    # ------------------------------------------------------------------ #

    def is_alive(self) -> bool:
        """Return ``True`` if hp > 0 and not incapacitated."""
        return self.db.hp > 0 and not self.db.incapacitated

    # ------------------------------------------------------------------ #
    #  Incapacitation / Respawn
    # ------------------------------------------------------------------ #

    def incapacitate(self, respawn_ticks: int) -> None:
        """Mark entity as incapacitated and set respawn timer."""
        self.db.incapacitated = True
        self.db.respawn_timer = respawn_ticks

    def tick_respawn(self) -> bool:
        """Decrement respawn timer.  If expired, restore to full HP.

        Returns ``True`` when the entity has respawned this tick.
        """
        if not self.db.incapacitated:
            return False
        self.db.respawn_timer -= 1
        if self.db.respawn_timer <= 0:
            self.db.respawn_timer = 0
            self.db.incapacitated = False
            self.db.hp = self.db.hp_max
            return True
        return False

    # ------------------------------------------------------------------ #
    #  Structured state
    # ------------------------------------------------------------------ #

    def get_structured_state(self) -> dict:
        """Return a dict snapshot of combat-relevant state."""
        return {
            "hp": self.db.hp,
            "hp_max": self.db.hp_max,
            "incapacitated": self.db.incapacitated,
            "respawn_timer": self.db.respawn_timer,
            "equipment_slots": dict(self.db.equipment_slots or {}),
        }
