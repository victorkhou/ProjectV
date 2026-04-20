"""
NPC typeclass — base for agents, enemies, and vendors.

Extends ``GameEntity`` (which extends ``DefaultObject``) because NPCs do not
need account puppeting, command sets, or session handling.  Behavior is
driven by Evennia Scripts attached to the NPC object.

Requirements: 7.7, 7.8, 7.9, 7.10, 2.1–2.9, 5.1, 8.1, 8.2, 8.6, 8.7
"""

from __future__ import annotations

import logging

from typeclasses.combat_entity import CombatEntity
from typeclasses.objects import GameEntity

logger = logging.getLogger("evennia.typeclasses.npcs")


class NPC(CombatEntity, GameEntity):
    """Base NPC typeclass for agents, enemies, and vendors.

    Attributes (``db.*``):
        owner:           reference to owning CombatCharacter (or ``None``)
        npc_type:        ``"agent"``, ``"enemy"``, or ``"vendor"``
        agent_id:        sequential permanent ID (agents only, 0 = unset)
        role:            ``""``, ``"harvester"``, ``"engineer"``, ``"soldier"``,
                         ``"guard"``, ``"scout"``, ``"medic"``
        role_target:     building reference (or ``None``)
        reserve:         ``True`` if placed in reserve due to demotion
        movement_queue:  list of ``[x, y]`` steps remaining (default ``[]``)
        movement_delay:  ticks between steps; 1 = fastest (default ``1``)
        activity_status: human-readable status string (default ``"Idle"``)

    Tags:
        ``("npc", "object_type")``             — for coordinate index queries
        ``("agent", "npc_type")``              — for efficient NPC-type queries
        ``("player_<owner_id>", "agent_owner")`` — added when owner is set
    """

    _object_type_tag = "npc"

    def at_object_creation(self):
        """Called once when the object is first created."""
        super().at_object_creation()  # GameEntity tags + coord init
        self.at_combat_entity_init()  # CombatEntity HP/equipment

        self.db.owner = None
        self.db.npc_type = "agent"
        self.db.agent_id = 0
        self.db.role = ""
        self.db.role_target = None
        self.db.reserve = False

        # Movement engine attributes (Req 2.1, 5.1, 8.1, 8.7)
        from world.constants import DEFAULT_MOVEMENT_DELAY
        self.db.movement_queue = []
        self.db.movement_delay = DEFAULT_MOVEMENT_DELAY
        self.db.activity_status = "Idle"

        # Tag for efficient querying by npc_type.
        # The owner tag ("player_<id>", "agent_owner") is added later
        # when an owner is actually assigned, since owner is None here.
        self.tags.add("agent", category="npc_type")

    # ------------------------------------------------------------------ #
    #  Movement Engine (Req 2.1–2.9, 5.1, 8.1, 8.2, 8.6, 8.7)
    # ------------------------------------------------------------------ #

    def advance_movement(self, tick_number: int) -> bool:
        """Advance one step if tick aligns with movement_delay.

        Returns ``True`` if the NPC moved this tick.

        Skips movement when the NPC is incapacitated (Req 2.4).
        Halts and clears the queue if the next tile is impassable
        (dynamic obstacle detection — Req 2.3).
        """
        # Skip if incapacitated (Req 2.4)
        if getattr(self.db, "incapacitated", False):
            return False

        queue = getattr(self.db, "movement_queue", None)
        if not queue:
            return False

        # Movement delay gating (Req 8.1, 8.6)
        delay = getattr(self.db, "movement_delay", 1) or 1
        if tick_number % delay != 0:
            return False

        # Peek at next step
        next_step = queue[0]
        nx, ny = int(next_step[0]), int(next_step[1])

        # Dynamic obstacle detection (Req 2.3, 2.8, 2.9)
        if not self._is_tile_passable(nx, ny):
            self.clear_movement()
            self.db.activity_status = "Blocked — waiting"
            return False

        # Move the NPC via PlanetRoom.move_entity (Req 2.5)
        room = self.location
        if room and hasattr(room, "move_entity"):
            room.move_entity(self, nx, ny)
        else:
            # Fallback: direct coordinate update
            self.db.coord_x = nx
            self.db.coord_y = ny

        # Consume the step
        queue.pop(0)
        self.db.movement_queue = queue

        # Check if queue is now empty → movement complete (Req 2.2)
        if not queue:
            self.db.activity_status = "Idle"
            self.at_movement_complete()

        return True

    def set_movement_queue(self, path: list[tuple[int, int]]) -> None:
        """Replace the current movement queue with a new path.

        Registers the NPC with the MovementSystem if available so it
        is tracked for per-tick processing.
        """
        self.db.movement_queue = [[x, y] for x, y in path]

        # Register with MovementSystem if available
        movement_system = self._get_movement_system()
        if movement_system:
            movement_system.register_moving(self)

    def clear_movement(self) -> None:
        """Clear the movement queue and halt.

        Unregisters the NPC from the MovementSystem.
        """
        self.db.movement_queue = []

        # Unregister from MovementSystem if available
        movement_system = self._get_movement_system()
        if movement_system:
            movement_system.unregister_moving(self)

    def at_movement_complete(self) -> None:
        """Hook called when the movement queue is exhausted.

        The NPC base implementation is a no-op. Behavior scripts do NOT
        override this method — instead, each script's ``at_repeat`` checks
        the NPC's state (empty queue, delivery_state, etc.) and acts
        accordingly. This avoids the multiple-scripts-overriding-one-hook
        problem.

        This hook exists for subclass overrides (e.g., enemy NPCs) where
        a single behavior owns the NPC.
        """
        pass

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _get_movement_system(self):
        """Return the MovementSystem from game_systems, or None."""
        try:
            from server.conf.game_init import game_systems
            return game_systems.get("movement_system")
        except (ImportError, AttributeError):
            return None

    def _is_tile_passable(self, x: int, y: int) -> bool:
        """Check if a tile is passable for dynamic obstacle detection.

        Uses ``make_passability_checker`` from the pathfinding module to
        avoid duplicating terrain/building passability logic. Falls back
        to ``True`` if game systems are unavailable (e.g., during tests).
        """
        room = self.location
        if room is None or not hasattr(room, "_game_systems"):
            return True

        systems = room._game_systems
        if not systems:
            return True

        terrain_generators = systems.get("_terrain_generators")
        registry = systems.get("registry")
        planet_key = getattr(room.db, "planet", None) if hasattr(room, "db") else None

        if terrain_generators and registry and planet_key:
            tgen = terrain_generators.get(planet_key)
            if tgen:
                from world.pathfinding import make_passability_checker

                # Get grid dimensions — use generous defaults since we
                # only need bounds for the checker, not for pathfinding.
                width = getattr(getattr(room, "db", None), "grid_width", 256) or 256
                height = getattr(getattr(room, "db", None), "grid_height", 256) or 256
                checker = make_passability_checker(
                    tgen, registry, room, int(width), int(height),
                )
                return checker(x, y)

        return True
