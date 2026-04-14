"""
CombatEntity mixin — shared combat state for players and NPCs.

Pure Python mixin with NO Evennia base class. Expects the host class
to provide ``self.db.*`` (Evennia AttributeHandler).

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""

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
        self.db.equipment_slots = {}       # slot_name -> item_ref
        self.db.incapacitated = False
        self.db.respawn_timer = 0          # ticks remaining
        self.db.respawn_location = None    # room ref or None

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
