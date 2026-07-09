"""
NPC typeclass — base for agents, enemies, and vendors.

Extends ``GameEntity`` (which extends ``DefaultObject``) because NPCs do not
need account puppeting, command sets, or session handling.  Behavior is
driven by Evennia Scripts attached to the NPC object.

"""

from __future__ import annotations

import logging

from typeclasses.combat_entity import CombatEntity
from typeclasses.objects import GameEntity
from world.constants import ACTIVITY_IDLE, compute_effective_delay

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
        self.at_combat_entity_init()  # CombatEntity HP/equipment + progression state
        # NOTE: combat_xp/level/rank_level are set by at_combat_entity_init()
        # above (the single init path) — do not re-default them here.

        # Enabled Gated_Ability keys — empty on creation.
        self.db.enabled_abilities = []

        self.db.owner = None
        self.db.npc_type = "agent"
        self.db.agent_id = 0
        self.db.role = ""
        self.db.role_target = None
        self.db.reserve = False

        # Movement engine attributes
        from world.constants import ACTIVITY_IDLE, DEFAULT_MOVEMENT_DELAY
        self.db.movement_queue = []
        self.db.movement_delay = DEFAULT_MOVEMENT_DELAY
        self.db.activity_status = ACTIVITY_IDLE

        # Tag for efficient querying by npc_type.
        # The owner tag ("player_<id>", "agent_owner") is added later
        # when an owner is actually assigned, since owner is None here.
        self.tags.add("agent", category="npc_type")

        # Invalidate the tick loop's cached agent roster (mirrors Building).
        self._bump_agent_index()

    def at_object_delete(self):
        """Clean up (via GameEntity), then invalidate the agent-roster cache."""
        result = super().at_object_delete()
        self._bump_agent_index()
        return result

    @staticmethod
    def _bump_agent_index() -> None:
        """Advance the agent-index generation so the tick loop re-searches.

        Guarded so an NPC create/delete never fails if the counter module is
        somehow unavailable (defensive; it is a pure-stdlib module).
        """
        try:
            from world import agent_index
            agent_index.bump()
        except Exception:  # noqa: BLE001 - cache freshness must not block lifecycle
            pass

    # ------------------------------------------------------------------ #
    #  Movement Engine
    # ------------------------------------------------------------------ #

    def advance_movement(self, tick_number: int) -> bool:
        """Advance one step if tick aligns with movement_delay.

        Returns ``True`` if the NPC moved this tick.

        Skips movement when the NPC is incapacitated.
        Halts and clears the queue if the next tile is impassable
        (dynamic obstacle detection).
        """
        # Skip if incapacitated
        if getattr(self.db, "incapacitated", False):
            return False

        queue = getattr(self.db, "movement_queue", None)
        if not queue:
            return False

        # Movement delay gating
        # Equipment may provide a "move_speed" modifier that reduces the
        # effective delay (positive modifier = faster). Clamped to >= 1.
        base_delay = getattr(self.db, "movement_delay", 1) or 1
        delay = compute_effective_delay(base_delay, self._get_move_speed_modifier())
        if tick_number % delay != 0:
            return False

        # Peek at next step
        next_step = queue[0]
        nx, ny = int(next_step[0]), int(next_step[1])

        # Dynamic obstacle detection
        if not self._is_tile_passable(nx, ny):
            self.clear_movement()
            self.db.activity_status = "Blocked — waiting"
            return False

        # Move the NPC via PlanetRoom.move_entity
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

        # Check if queue is now empty → movement complete
        if not queue:
            self.db.activity_status = ACTIVITY_IDLE
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

    # ``_get_move_speed_modifier`` is inherited from CombatEntity so players
    # and agents derive the equipment ``move_speed`` bonus identically.

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
