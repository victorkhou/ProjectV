"""
NPC typeclass — base for agents, enemies, and vendors.

Extends ``DefaultObject`` (not ``DefaultCharacter``) because NPCs do not
need account puppeting, command sets, or session handling.  Behavior is
driven by Evennia Scripts attached to the NPC object.

Requirements: 7.7, 7.8, 7.9, 7.10
"""

from evennia.objects.objects import DefaultObject

from typeclasses.combat_entity import CombatEntity


class NPC(CombatEntity, DefaultObject):
    """Base NPC typeclass for agents, enemies, and vendors.

    Attributes (``db.*``):
        owner:       reference to owning CombatCharacter (or ``None``)
        npc_type:    ``"agent"``, ``"enemy"``, or ``"vendor"``
        agent_id:    sequential permanent ID (agents only, 0 = unset)
        role:        ``""``, ``"harvester"``, ``"engineer"``, ``"soldier"``,
                     ``"guard"``, ``"scout"``, ``"medic"``
        role_target: building reference (or ``None``)
        reserve:     ``True`` if placed in reserve due to demotion

    Tags:
        ``("agent", "npc_type")``              — for efficient NPC-type queries
        ``("player_<owner_id>", "agent_owner")`` — added when owner is set
    """

    def at_object_creation(self):
        """Called once when the object is first created."""
        self.at_combat_entity_init()

        self.db.owner = None
        self.db.npc_type = "agent"
        self.db.agent_id = 0
        self.db.role = ""
        self.db.role_target = None
        self.db.reserve = False

        # Tag for efficient querying by npc_type.
        # The owner tag ("player_<id>", "agent_owner") is added later
        # when an owner is actually assigned, since owner is None here.
        self.tags.add("agent", category="npc_type")
