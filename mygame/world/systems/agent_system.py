"""
Agent System — manages player-owned NPC agents.

Handles training, role assignment, demotion/promotion reserve,
and per-tick processing of agent behavior scripts.

Requirements: 7b.1–7b.14, 8.1–8.7
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.utils import get_building_attr as _get_building_attr_shared
from world.utils import set_building_attr as _set_building_attr_shared
from world.constants import (
    TRAINING_PROGRESS_INTERVAL,
    DEFAULT_CARRY_CAPACITY,
    MIN_PATROL_WAYPOINTS,
    MAX_PATROL_WAYPOINTS,
    ACTIVITY_IDLE,
    LEVELS_PER_RANK,
    NUM_RANKS,
    DeliveryState,
)

logger = logging.getLogger("mygame.agent_system")

# ------------------------------------------------------------------ #
#  Constants
# ------------------------------------------------------------------ #

VALID_ROLES = ("harvester", "engineer", "soldier", "guard", "scout", "medic")

# Maps building abbreviation → required agent role
BUILDING_ROLE_MAP: dict[str, str] = {
    "EX": "harvester",
    "TU": "guard",
    "RD": "scout",
    "AR": "engineer",
    "LB": "engineer",
    "MB": "medic",
}

# Roles that belong to the army and do NOT require a target building
ARMY_ROLES = ("soldier", "medic")

# Maps an XP source key → the BalanceConfig attribute holding its amount.
# Death loss is handled separately (it uses ``agent_xp_death_loss``).
# An unknown source key resolves to no field → 0 amount → no-op award.
AGENT_XP_SOURCE_FIELDS: dict[str, str] = {
    "harvest": "agent_xp_harvest",
    "delivery": "agent_xp_delivery",
    "construction": "agent_xp_construction",
    "combat": "agent_xp_combat",
    "time_served": "agent_xp_time_served",
}

# Maps a gated ability Script class name → its Evennia ``key``. Script
# subclasses set ``key`` in ``at_script_creation`` (not as a class attribute),
# so this lets the attach/detach helpers match scripts by key without
# instantiating the class (which silently fails outside the Evennia DB context).
ABILITY_SCRIPT_KEYS: dict[str, str] = {
    "DeliveryBehavior": "delivery_behavior",
}


class AgentSystem:
    """Manages player-owned NPC agents: training, assignment, reserve.

    Constructor args:
        registry:        DataRegistry for rank/building lookups.
        event_bus:        EventBus for publishing agent events.
        create_npc_func:  Optional factory ``(player, agent_id) -> NPC``.
                          If *None*, uses ``evennia.create_object``.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_npc_func: Callable | None = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self._create_npc_func = create_npc_func or self._default_create_npc
        # In-memory cache of buildings currently training agents.
        # Avoids a DB query every tick. Updated by train_agent/complete_training.
        self._training_buildings: list[Any] = []

    # ------------------------------------------------------------------ #
    #  NPC factory
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_create_npc(player: Any, agent_id: int) -> Any:
        """Create an NPC agent via Evennia's object creation API.

        Places the NPC in the player's PlanetRoom at the player's HQ
        coordinates (or the player's current position as fallback) so
        it exists in the game world and can pathfind to its assignment.
        """
        import evennia

        # Place in the player's PlanetRoom so the NPC has a location
        planet_room = getattr(player, "location", None)

        npc = evennia.create_object(
            "typeclasses.npcs.NPC",
            key=f"Agent-{agent_id}",
            location=planet_room,
        )
        npc.db.owner = player
        npc.db.npc_type = "agent"
        npc.db.agent_id = agent_id
        npc.db.role = ""
        npc.db.role_target = None
        npc.db.reserve = False
        npc.tags.add("agent", category="npc_type")
        owner_id = getattr(player, "id", id(player))
        npc.tags.add(f"player_{owner_id}", category="agent_owner")

        # Place at HQ coordinates so the agent walks to its assignment.
        # Falls back to player position if no HQ exists.
        spawn_x, spawn_y = None, None
        try:
            buildings = player.get_buildings() if hasattr(player, "get_buildings") else []
            for b in buildings:
                if getattr(b.db, "building_type", "") == "HQ":
                    spawn_x = getattr(b.db, "coord_x", None)
                    spawn_y = getattr(b.db, "coord_y", None)
                    break
        except Exception:
            pass

        if spawn_x is None or spawn_y is None:
            spawn_x = getattr(getattr(player, "db", None), "coord_x", None)
            spawn_y = getattr(getattr(player, "db", None), "coord_y", None)

        if spawn_x is not None and spawn_y is not None:
            npc.db.coord_x = int(spawn_x)
            npc.db.coord_y = int(spawn_y)
            # at_object_receive saw coord_x=None during create_object,
            # so manually register in the coordinate index now.
            if planet_room is not None and hasattr(planet_room, "coord_index"):
                planet_room.coord_index.add(npc, int(spawn_x), int(spawn_y))

        return npc

    # ------------------------------------------------------------------ #
    #  Training  (Req 8.1–8.7)
    # ------------------------------------------------------------------ #

    def train_agent(
        self, player: Any, academy_building: Any
    ) -> tuple[bool, str]:
        """Begin training a new agent at *academy_building*.

        Checks:
        1. Agent cap not exceeded.
        2. Player can afford scaled cost (base × N where N = total agents after training).
        3. Sets a training timer on the academy based on its level.

        Returns ``(success, message)``.
        """
        # --- cap check ---
        current_count = self.get_agent_count(player)
        max_agents = self.get_max_agents(player)
        if current_count >= max_agents:
            return False, f"Agent cap reached ({current_count}/{max_agents}). Promote to a higher rank for more agents."

        # --- determine next ID ---
        # Derive from existing agents to stay in sync after deletions
        agents = self.get_agents(player)
        if agents:
            max_id = max(getattr(a.db, "agent_id", 0) for a in agents)
            next_id = max_id + 1
        else:
            next_id = 1  # first agent

        # --- cost calculation ---
        # Cost scales with how many agents you'll have after training
        bal = self.registry.balance
        n = current_count + 1
        cost = {res: base * n for res, base in bal.base_training_cost.items()}

        if not player.has_resources(cost):
            cost_str = ", ".join(f"{v} {k}" for k, v in cost.items())
            return False, f"Insufficient resources. Training agent #{next_id} costs {cost_str}."

        # --- deduct resources ---
        player.deduct_resources(cost)

        # --- compute training time ---
        academy_level = getattr(academy_building.db, "building_level", 1) if academy_building else 1
        reduction = bal.academy_training_reduction_per_level * academy_level
        training_ticks = max(1, int(bal.base_training_ticks * (1 - reduction)))

        # Store training state on the academy building using explicit
        # attributes.add for reliable DB persistence and query-ability
        if academy_building is not None:
            if hasattr(academy_building, "attributes"):
                academy_building.attributes.add("training_agent_id", next_id)
                academy_building.attributes.add("training_ticks_remaining", training_ticks)
                academy_building.attributes.add("training_owner", player)
            else:
                academy_building.db.training_agent_id = next_id
                academy_building.db.training_ticks_remaining = training_ticks
                academy_building.db.training_owner = player
            # Track in memory for tick processing (avoids DB query per tick)
            if academy_building not in self._training_buildings:
                self._training_buildings.append(academy_building)

        # Update the player's next_agent_id
        player.db.next_agent_id = next_id + 1

        return True, (
            f"Training agent #{next_id}. "
            f"Time remaining: {training_ticks} ticks."
        )

    def complete_training(self, academy_building: Any) -> Any | None:
        """Finish training and spawn the NPC.  Returns the new NPC or None."""
        agent_id = None
        player = None
        if hasattr(academy_building, "attributes"):
            agent_id = academy_building.attributes.get("training_agent_id")
            player = academy_building.attributes.get("training_owner")
        if agent_id is None:
            agent_id = getattr(getattr(academy_building, "db", None), "training_agent_id", None)
        if player is None:
            player = getattr(getattr(academy_building, "db", None), "training_owner", None)
        if agent_id is None or player is None:
            return None

        npc = self._create_npc_func(player, agent_id)

        # Clear academy training state
        if hasattr(academy_building, "attributes"):
            academy_building.attributes.add("training_agent_id", None)
            academy_building.attributes.add("training_ticks_remaining", None)
            academy_building.attributes.add("training_owner", None)
        else:
            academy_building.db.training_agent_id = None
            academy_building.db.training_ticks_remaining = None
            academy_building.db.training_owner = None

        # Remove from training cache
        try:
            self._training_buildings.remove(academy_building)
        except (ValueError, AttributeError):
            pass

        # Notify the player
        if player is not None and hasattr(player, "msg"):
            player.msg(
                f"|g[Complete] Agent #{agent_id} training finished! "
                f"Use 'agents' to see your roster and 'assign {agent_id}' "
                f"to put them to work.|n"
            )

        return npc

    # ------------------------------------------------------------------ #
    #  Assignment  (Req 7b.6, 7b.7, 7b.8, 7b.11)
    # ------------------------------------------------------------------ #

    def assign_agent(
        self,
        player: Any,
        agent_id: int,
        role: str,
        target_building: Any = None,
    ) -> tuple[bool, str]:
        """Assign *agent_id* to *role*, optionally at *target_building*.

        Validates:
        - Agent exists and belongs to player.
        - Agent is not incapacitated or reserved.
        - Role is valid.
        - Building/role match (Extractor→Harvester, etc.).
        - Army roles (Soldier, Medic) don't need a building.

        Returns ``(success, message)``.
        """
        role = role.lower()
        if role not in VALID_ROLES:
            return False, f"Invalid role '{role}'. Valid: {', '.join(VALID_ROLES)}."

        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return False, f"Agent #{agent_id} not found."

        # Cannot assign incapacitated agents
        if getattr(agent.db, "incapacitated", False):
            return False, f"Agent #{agent_id} is incapacitated and cannot be assigned."

        # Cannot assign reserved agents
        if getattr(agent.db, "reserve", False):
            return False, f"Agent #{agent_id} is in reserve and cannot be reassigned."

        # --- building / role validation ---
        if role in ARMY_ROLES:
            # Army roles don't require a target building
            pass
        else:
            if target_building is None:
                return False, f"Role '{role}' requires a target building."
            btype = getattr(target_building.db, "building_type", "")
            expected_role = BUILDING_ROLE_MAP.get(btype)
            if expected_role is None:
                return False, f"Building type '{btype}' does not support agent assignment."
            if expected_role != role:
                return False, (
                    f"Building type '{btype}' requires role '{expected_role}', "
                    f"not '{role}'."
                )

        # --- apply assignment ---

        # Clear assigned_agent on the old building (if any)
        old_target = getattr(agent.db, "role_target", None)
        if old_target is not None and old_target is not target_building:
            if hasattr(old_target, "attributes") and hasattr(old_target.attributes, "add"):
                if old_target.attributes.get("assigned_agent") is agent:
                    old_target.attributes.add("assigned_agent", None)
            elif hasattr(old_target, "db"):
                if getattr(old_target.db, "assigned_agent", None) is agent:
                    old_target.db.assigned_agent = None

        agent.db.role = role
        agent.db.role_target = target_building

        # Track assignment on the new building
        if target_building is not None:
            if hasattr(target_building, "attributes") and hasattr(target_building.attributes, "add"):
                target_building.attributes.add("assigned_agent", agent)
            elif hasattr(target_building, "db"):
                target_building.db.assigned_agent = agent

        # Detach any existing behavior script before attaching a new one
        self._detach_behavior_script(agent)

        # Clear any in-progress movement from the previous assignment
        if hasattr(agent, "clear_movement"):
            agent.clear_movement()

        # Attach the behavior script for this role
        self._attach_behavior_script(agent, role)

        # Clear stale state from previous role
        agent.db.patrol_route = None
        agent.db.patrol_waypoint_index = 0
        agent.db.delivery_state = None
        agent.db.carried_resources = {}
        agent.db.delivery_target = None

        # Path agent to building coordinates instead of teleporting (Req 2.6)
        if target_building is not None:
            bx = getattr(getattr(target_building, "db", None), "coord_x", None)
            by = getattr(getattr(target_building, "db", None), "coord_y", None)
            if bx is not None and by is not None:
                bx, by = int(bx), int(by)

                # Ensure agent is in the PlanetRoom (old agents may lack location)
                if getattr(agent, "location", None) is None:
                    planet_room = getattr(player, "location", None)
                    if planet_room is not None:
                        agent.location = planet_room
                        # Set initial coords to player position
                        px = getattr(player.db, "coord_x", None)
                        py = getattr(player.db, "coord_y", None)
                        if px is not None and py is not None:
                            agent.db.coord_x = int(px)
                            agent.db.coord_y = int(py)
                            if hasattr(planet_room, "coord_index"):
                                planet_room.coord_index.add(agent, int(px), int(py))

                ax = getattr(agent.db, "coord_x", None)
                ay = getattr(agent.db, "coord_y", None)

                path = []
                if ax is not None and ay is not None:
                    path = self._compute_path_to(agent, int(ax), int(ay), bx, by)

                if path and hasattr(agent, "set_movement_queue"):
                    agent.set_movement_queue(path)
                    agent.db.activity_status = (
                        f"Moving to {role} assignment ({len(path)} tiles)"
                    )
                else:
                    # Fallback: no path found or already at destination —
                    # place agent directly at building coordinates
                    planet_room = getattr(agent, "location", None)
                    if planet_room is not None and hasattr(planet_room, "move_entity"):
                        planet_room.move_entity(agent, bx, by)
                    else:
                        agent.db.coord_x = bx
                        agent.db.coord_y = by
                    agent.db.activity_status = f"Assigned as {role}"
            elif hasattr(agent, "move_to"):
                # Legacy fallback: building doesn't have coordinates yet
                loc = getattr(target_building, "location", target_building)
                agent.move_to(loc, quiet=True)

        return True, f"Agent #{agent_id} assigned as {role}."

    # ------------------------------------------------------------------ #
    #  Unassignment  (Req 7b.7)
    # ------------------------------------------------------------------ #

    def unassign_agent(
        self, player: Any, agent_id: int
    ) -> tuple[bool, str]:
        """Clear role from *agent_id* and path back to HQ.

        Clears movement queue, patrol route, delivery state, then
        computes a path to HQ.  Falls back to direct teleport if no
        path is found.

        Returns ``(success, message)``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return False, f"Agent #{agent_id} not found."

        # Clear assigned_agent on the building
        old_target = getattr(agent.db, "role_target", None)
        if old_target is not None:
            if hasattr(old_target, "attributes") and hasattr(old_target.attributes, "add"):
                if old_target.attributes.get("assigned_agent") is agent:
                    old_target.attributes.add("assigned_agent", None)
            elif hasattr(old_target, "db"):
                if getattr(old_target.db, "assigned_agent", None) is agent:
                    old_target.db.assigned_agent = None

        # Detach behavior script before clearing role
        self._detach_behavior_script(agent)

        # Clear current movement queue (Req 11.2)
        if hasattr(agent, "clear_movement"):
            agent.clear_movement()

        # Clear patrol-related attributes (Req 3.6)
        agent.db.patrol_route = None
        agent.db.patrol_waypoint_index = 0

        # Clear delivery-related attributes (Req 3.6, 11.2)
        agent.db.delivery_state = None
        agent.db.carried_resources = {}
        agent.db.delivery_target = None

        agent.db.role = ""
        agent.db.role_target = None

        # Compute path to HQ instead of teleporting (Req 11.2)
        hq = self._find_hq(player)
        if hq is not None:
            hx = getattr(getattr(hq, "db", None), "coord_x", None)
            hy = getattr(getattr(hq, "db", None), "coord_y", None)
            if hx is not None and hy is not None:
                hx, hy = int(hx), int(hy)
                ax = getattr(agent.db, "coord_x", None)
                ay = getattr(agent.db, "coord_y", None)

                path = []
                if ax is not None and ay is not None:
                    path = self._compute_path_to(agent, int(ax), int(ay), hx, hy)

                if path and hasattr(agent, "set_movement_queue"):
                    agent.set_movement_queue(path)
                    agent.db.activity_status = (
                        f"Returning to HQ ({len(path)} tiles)"
                    )
                else:
                    # Fallback: no path found or already at HQ —
                    # place agent directly at HQ coordinates
                    planet_room = getattr(agent, "location", None)
                    if planet_room is not None and hasattr(planet_room, "move_entity"):
                        planet_room.move_entity(agent, hx, hy)
                    else:
                        agent.db.coord_x = hx
                        agent.db.coord_y = hy
                    agent.db.activity_status = ACTIVITY_IDLE
            elif hasattr(agent, "move_to"):
                # Legacy fallback: HQ doesn't have coordinates yet
                loc = getattr(hq, "location", hq)
                agent.move_to(loc, quiet=True)
                agent.db.activity_status = ACTIVITY_IDLE
        else:
            agent.db.activity_status = ACTIVITY_IDLE

        return True, f"Agent #{agent_id} unassigned and returned to HQ."

    # ------------------------------------------------------------------ #
    #  Patrol routes  (Req 3.1, 3.6, 3.7, 3.8)
    # ------------------------------------------------------------------ #

    def set_patrol_route(
        self, player: Any, agent_id: int, waypoints: list
    ) -> tuple[bool, str]:
        """Set a patrol route on a guard or scout agent.

        Validates:
        - Agent exists and belongs to player.
        - Agent role is guard or scout.
        - Waypoint count is between MIN_PATROL_WAYPOINTS and MAX_PATROL_WAYPOINTS.
        - All waypoints are within planet bounds.

        Returns ``(success, message)``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return False, f"Agent #{agent_id} not found."

        role = getattr(agent.db, "role", "")
        if role not in ("guard", "scout"):
            return False, (
                f"Agent #{agent_id} is a {role or 'unassigned'} — "
                f"only guards and scouts can patrol."
            )

        # Validate waypoint count
        if len(waypoints) < MIN_PATROL_WAYPOINTS:
            return False, (
                f"Patrol route requires at least {MIN_PATROL_WAYPOINTS} "
                f"waypoints (got {len(waypoints)})."
            )
        if len(waypoints) > MAX_PATROL_WAYPOINTS:
            return False, (
                f"Patrol route allows at most {MAX_PATROL_WAYPOINTS} "
                f"waypoints (got {len(waypoints)})."
            )

        # Determine planet bounds for validation
        width, height = self._get_planet_bounds(agent)

        # Validate all waypoints are within bounds
        for i, wp in enumerate(waypoints):
            wx, wy = int(wp[0]), int(wp[1])
            if wx < 0 or wx >= width or wy < 0 or wy >= height:
                return False, (
                    f"Waypoint {i + 1} ({wx}, {wy}) is outside planet "
                    f"bounds (0–{width - 1}, 0–{height - 1})."
                )

        # Store patrol route as list of [x, y] pairs (Evennia-safe)
        agent.db.patrol_route = [[int(wp[0]), int(wp[1])] for wp in waypoints]
        agent.db.patrol_waypoint_index = 0

        return True, (
            f"Agent #{agent_id} patrol route set with "
            f"{len(waypoints)} waypoints."
        )

    def clear_patrol_route(
        self, player: Any, agent_id: int
    ) -> tuple[bool, str]:
        """Clear the patrol route on an agent and stop movement.

        Clears patrol_route, patrol_waypoint_index, and movement_queue.

        Returns ``(success, message)``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return False, f"Agent #{agent_id} not found."

        agent.db.patrol_route = None
        agent.db.patrol_waypoint_index = 0

        if hasattr(agent, "clear_movement"):
            agent.clear_movement()
        else:
            agent.db.movement_queue = []

        agent.db.activity_status = ACTIVITY_IDLE

        return True, f"Agent #{agent_id} patrol route cleared."

    # ------------------------------------------------------------------ #
    #  Stop / cancel  (Req 11.1, 11.3, 11.4)
    # ------------------------------------------------------------------ #

    def stop_agent(
        self, player: Any, agent_id: int
    ) -> tuple[bool, str]:
        """Stop an agent's current movement and set it to idle.

        Clears the movement queue, detaches behavior scripts, clears
        the building's ``assigned_agent`` reference, and sets
        activity_status to "Idle".
        Retains carried resources if the agent is a harvester.

        Returns ``(success, message)``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return False, f"Agent #{agent_id} not found."

        if hasattr(agent, "clear_movement"):
            agent.clear_movement()
        else:
            agent.db.movement_queue = []

        agent.db.activity_status = ACTIVITY_IDLE

        # Harvesters retain carried resources (Req 11.4) — no cleanup needed.
        # Just reset delivery_state so the behavior script can re-evaluate.
        role = getattr(agent.db, "role", "")
        if role == "harvester":
            agent.db.delivery_state = DeliveryState.IDLE

        # Clear the building's assigned_agent reference so it can accept
        # a new assignment.
        old_target = getattr(agent.db, "role_target", None)
        if old_target is not None:
            if hasattr(old_target, "attributes") and hasattr(old_target.attributes, "add"):
                if old_target.attributes.get("assigned_agent") is agent:
                    old_target.attributes.add("assigned_agent", None)
            elif hasattr(old_target, "db"):
                if getattr(old_target.db, "assigned_agent", None) is agent:
                    old_target.db.assigned_agent = None

        # Detach behavior scripts and clear role assignment
        self._detach_behavior_script(agent)
        agent.db.role = ""
        agent.db.role_target = None

        return True, f"Agent #{agent_id} stopped."

    # ------------------------------------------------------------------ #
    #  Queries  (Req 7b.10)
    # ------------------------------------------------------------------ #

    def get_agents(self, player: Any) -> list:
        """Return all NPC objects tagged 'agent' owned by *player*."""
        owner_id = getattr(player, "id", id(player))
        try:
            from evennia.objects.models import ObjectDB

            return list(
                ObjectDB.objects.filter(
                    db_tags__db_key="agent",
                    db_tags__db_category="npc_type",
                ).filter(
                    db_tags__db_key=f"player_{owner_id}",
                    db_tags__db_category="agent_owner",
                )
            )
        except Exception:
            # Fallback for test environments without full Evennia DB
            return self._get_agents_fallback(player)

    def _get_agents_fallback(self, player: Any) -> list:
        """Fallback agent query for test environments."""
        return []

    def get_agent_by_id(self, player: Any, agent_id: int) -> Any | None:
        """Find a specific agent by ID.  Returns NPC or None."""
        for agent in self.get_agents(player):
            if getattr(agent.db, "agent_id", None) == agent_id:
                return agent
        return None

    def get_agent_count(self, player: Any) -> int:
        """Total number of trained agent NPCs owned by the player."""
        return len(self.get_agents(player))

    def get_max_agents(self, player: Any) -> int:
        """Return the max agent slots for the player's current rank.

        agent_cap in YAML includes the commander slot, so the usable
        agent-only cap is ``agent_cap - 1``.
        """
        rank_def = self.registry.get_rank_for_xp(player.db.combat_xp)
        return rank_def.agent_cap - 1

    # ------------------------------------------------------------------ #
    #  Owner-level cap  (Req 14.1, 14.2, 14.3, 14.5, 14.6, 14.7)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _coerce_level(value: Any) -> int | None:
        """Coerce a stored level/rank value to ``int``; ``None`` if not numeric.

        Corrupted out-of-band state (an admin edit or migration bug leaving a
        non-numeric ``db.level``/``db.rank_level``) must not raise ``ValueError``
        up through the owner-cap math into a command handler. Returns ``None``
        so the caller can fall back to its conservative default with a log.
        """
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.debug("Ignoring non-numeric level value %r; treating as unset.", value)
            return None

    @classmethod
    def _level_from_player(cls, player: Any) -> int:
        """Resolve a player's Entity_Level using the legacy RankSystem rule.

        Mirrors ``RankSystem._get_level``: prefer ``db.level``; fall back to
        ``db.rank_level`` (1-12 rank number → first level of that rank); default
        to 1 when neither is set. Kept in sync with that rule rather than holding
        a RankSystem reference, since AgentSystem has none. A non-numeric stored
        value is treated as unset (conservative default of 1) rather than raising.
        """
        if hasattr(player, "db"):
            lvl = cls._coerce_level(getattr(player.db, "level", None))
            if lvl is not None:
                return lvl
            rl = cls._coerce_level(getattr(player.db, "rank_level", None))
            if rl is not None:
                if rl <= NUM_RANKS:
                    return (rl - 1) * LEVELS_PER_RANK + 1
                return rl
        return 1

    def get_owner_level(self, agent: Any) -> int:
        """Return the owning player's Entity_Level (default 1 when missing).

        Reuses the ``RankSystem._get_level`` legacy rule for the owner. When the
        agent has no ``db.owner`` (orphaned), defaults to 1 — the most
        conservative outcome (cap_ceiling 1, effective_level 1). Never raises.
        """
        owner = getattr(getattr(agent, "db", None), "owner", None)
        if owner is None:
            logger.debug(
                "Agent %s has no owner; defaulting owner_level to 1.",
                getattr(agent, "key", "?"),
            )
            return 1
        return self._level_from_player(owner)

    def get_cap_ceiling(self, agent: Any) -> int:
        """Return the agent's Cap_Ceiling = ``max(1, owner_level - 1)``.

        The maximum Effective_Level the owner cap permits (Req 14.4). Floors at
        1 so an agent owned by a level-1 player (or an orphaned agent) has a
        ceiling of 1.
        """
        return max(1, self.get_owner_level(agent) - 1)

    @staticmethod
    def _raw_level(agent: Any) -> int:
        """Return the agent's owner-agnostic Raw_Level from its own Combat_XP.

        Prefers the ``CombatEntity.get_raw_level`` method; falls back to deriving
        it from ``db.combat_xp`` via the shared curve for bare agents (e.g. test
        fakes without the mixin). Single source of truth for raw-level derivation
        so ``compute_effective_level`` and ``get_agent_progression_view`` cannot
        drift apart.
        """
        if hasattr(agent, "get_raw_level"):
            raw = AgentSystem._coerce_level(agent.get_raw_level())
            if raw is not None:
                return raw
        from world import progression

        raw_xp = getattr(getattr(agent, "db", None), "combat_xp", 0) or 0
        try:
            return progression.level_for_xp(raw_xp)
        except (TypeError, ValueError):
            logger.debug("Non-numeric combat_xp %r; treating raw level as 1.", raw_xp)
            return 1

    def compute_effective_level(self, agent: Any) -> int:
        """Return the agent's Effective_Level under the owner-level cap.

        ``max(1, min(Raw_Level, owner_level - 1))`` (Req 14.1, 14.2, 14.3). The
        Raw_Level is derived owner-agnostically from the agent's own Combat_XP
        via ``agent.get_raw_level()``; the cap bounds it strictly below the
        owner's level. Handles the owner-demotion edge case (Req 14.5) where a
        stored raw level can exceed the new ceiling, and re-derives on XP/owner
        changes (Req 14.6, 14.7).
        """
        return max(1, min(self._raw_level(agent), self.get_owner_level(agent) - 1))

    # ------------------------------------------------------------------ #
    #  Ability-status classification  (single source of truth)
    # ------------------------------------------------------------------ #
    #
    # One classifier decides an ability's state; the roster wire encoding
    # (``get_agent_progression_view``), the roster's human rendering
    # (``agent_commands.sub_list``), and the ``agent ability`` status command
    # (``get_ability_status``) all derive from it, so the three renderings can
    # never diverge (Req 11.2, 11.3, 16.5).

    @staticmethod
    def _classify_ability(
        effective: int, required: int, is_enabled: bool
    ) -> tuple[str, int]:
        """Return ``(state, required_level)`` for one gate.

        ``state`` is one of ``"enabled"`` / ``"available"`` / ``"locked"``.
        ``required_level`` is echoed back so callers can render "locked" with
        the threshold without re-reading the gate.
        """
        if is_enabled:
            return "enabled", required
        if effective >= required:
            return "available", required
        return "locked", required

    @classmethod
    def _encode_ability_status(
        cls, effective: int, required: int, is_enabled: bool
    ) -> str:
        """Encode a gate's status for the roster view's ``ability_status`` map.

        Wire encoding consumed by ``agent_commands.sub_list``:
        ``"enabled"`` / ``"available"`` / ``"locked:N"`` (N = required level).
        Kept stable because the roster decodes it via ``decode_ability_status``.
        """
        state, req = cls._classify_ability(effective, required, is_enabled)
        return f"locked:{req}" if state == "locked" else state

    @staticmethod
    def decode_ability_status(encoded: str) -> tuple[str, str]:
        """Decode an ``ability_status`` value into ``(state, readable)``.

        Inverse of ``_encode_ability_status``, so the roster command never
        hand-parses the wire format. Returns the bare ``state``
        (``"enabled"`` / ``"available"`` / ``"locked"`` / other) and a
        human-readable rendering (``"locked LvN"`` for the locked encoding).
        """
        if isinstance(encoded, str) and encoded.startswith("locked:"):
            return "locked", f"locked Lv{encoded.split(':', 1)[1]}"
        return encoded, encoded

    # ------------------------------------------------------------------ #
    #  Enabled-ability state  (Req 12.1, 12.4, 17.1)
    # ------------------------------------------------------------------ #

    def get_enabled_abilities(self, agent: Any) -> set:
        """Return the agent's stored set of enabled gated-ability keys.

        Reads ``agent.db.enabled_abilities`` (a persisted list); absent or
        ``None`` → empty set (legacy default, Req 12.4). The set is sticky and
        independent of attach state — it reflects what the player has explicitly
        enabled, not what is currently active (Req 17.1).
        """
        keys = getattr(getattr(agent, "db", None), "enabled_abilities", None)
        if not keys:
            return set()
        return set(keys)

    def _set_enabled_abilities(self, agent: Any, keys) -> None:
        """Persist the enabled-ability set back to ``agent.db.enabled_abilities``.

        Stored as a list for Evennia attribute persistence (Req 12.1, 17.1).
        """
        agent.db.enabled_abilities = list(keys)

    # ------------------------------------------------------------------ #
    #  Gate evaluation  (Req 8, 9, 12.5, 12.6, 13.4, 15, 17)
    # ------------------------------------------------------------------ #

    def evaluate_gated_abilities(self, agent: Any, notify: bool = True) -> None:
        """Converge an agent's gated behavior scripts to its current state.

        For each ``Ability_Gate`` in the registry, attaches or detaches the
        gate's behavior script so that it is present *if and only if* the agent's
        ``Effective_Level`` meets or exceeds the gate's required level AND the
        owning player has enabled that ability for the agent (Req 8.5, 8.6).

        Per-gate branch logic (mirrors the design pseudocode):

        - ``want and not attached`` → attach + init delivery state + notify the
          owner the ability is now active (Req 9.2, 9.3, 15.3, 17.3).
        - ``attached and not want`` → detach the script. Notify re-lock ONLY when
          the loss was caused by a level drop (``not available``); a detach
          caused purely by the player disabling a still-available ability is
          silent here (the disable command confirms it) (Req 9.5, 9.6, 9.7,
          15.4, 17.4).
        - ``available and not enabled and not attached`` → mark the ability
          available and notify the owner how to enable it, once per
          availability window (Req 9.1, 15.2).
        - otherwise → no-op (Req 9.3, 9.8).

        Unresolved ability keys are skipped with a single warning so a missing
        script never blocks evaluation of the remaining gates (Req 13.4). The
        method is idempotent: repeated calls leave at most one instance of each
        script attached (Req 9.4).
        """
        effective = self.compute_effective_level(agent)
        enabled = self.get_enabled_abilities(agent)
        notified = self._get_notified_available(agent)
        notified_changed = False

        for gate in self.registry.get_ability_gates():
            key = gate.key
            required = gate.required_level
            available = effective >= required
            is_enabled = key in enabled

            script_cls = self.resolve_ability_script(key)
            if script_cls is None:
                # Unresolved key — skip attachment, log once, keep evaluating.
                logger.warning("Unresolved ability gate key: %s", key)
                continue

            script_key = self._ability_script_key(script_cls)
            attached = self._has_ability_script(agent, script_key)
            want = available and is_enabled

            if want and not attached:
                # Two conditions met and not yet attached → attach + activate.
                self._attach_single_script(agent, script_cls)
                if key in notified:
                    notified.discard(key)
                    notified_changed = True
                self._notify_owner(
                    agent,
                    notify,
                    f"|g[Ability] '{key}' is now active for Agent "
                    f"#{self._agent_id(agent)}.|n",
                )
            elif attached and not want:
                # Attached but no longer wanted → detach. Re-lock notification
                # only when the cause is a level drop (ability still wanted by
                # the player, i.e. enabled, but no longer available).
                self._detach_single_script(agent, script_key)
                if not available:
                    # The availability window has closed: clear any stale
                    # "available" flag so a future re-cross into the
                    # available-but-not-enabled state notifies again (Req 15.2,
                    # Property 10). Mirrors the cleanup in the no-op else branch.
                    if key in notified:
                        notified.discard(key)
                        notified_changed = True
                    self._notify_owner(
                        agent,
                        notify,
                        f"|r[Ability] '{key}' has re-locked for Agent "
                        f"#{self._agent_id(agent)} — its level dropped below "
                        f"{required}.|n",
                    )
            elif available and not is_enabled and not attached:
                # Unlocked but not enabled → offer it to the player once.
                if key not in notified:
                    notified.add(key)
                    notified_changed = True
                    self._notify_owner(
                        agent,
                        notify,
                        f"|y[Ability] '{key}' is now available for Agent "
                        f"#{self._agent_id(agent)}. Enable it with "
                        f"'agent ability {self._agent_id(agent)} {key} on'.|n",
                    )
            else:
                # No-op. If the gate is no longer available, clear any stale
                # "available" notification so a future re-cross notifies again.
                if not available and key in notified:
                    notified.discard(key)
                    notified_changed = True

        if notified_changed:
            self._set_notified_available(agent, notified)

    # -- gate-evaluation helpers --------------------------------------- #

    @staticmethod
    def _agent_id(agent: Any) -> Any:
        """Return the agent's display id (``db.agent_id``), or '?' when absent."""
        return getattr(getattr(agent, "db", None), "agent_id", None) or "?"

    @staticmethod
    def _has_ability_script(agent: Any, script_key: str | None) -> bool:
        """Return True if a script with ``script_key`` is attached to *agent*.

        Scans ``agent.scripts`` by key. Guards ``hasattr`` so it is safe in test
        environments and on agents without a script manager (returns False).
        """
        if script_key is None:
            return False
        if not hasattr(agent, "scripts"):
            return False
        try:
            for script in agent.scripts.all():
                if getattr(script, "key", "") == script_key:
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _get_notified_available(agent: Any) -> set:
        """Return the per-agent set of ability keys already offered to the owner.

        Read from ``agent.db.notified_available_abilities`` (a persisted list);
        absent or ``None`` → empty set. Used to send the "available, enable with"
        notification at most once per availability window (Req 15.2).
        """
        keys = getattr(
            getattr(agent, "db", None), "notified_available_abilities", None
        )
        if not keys:
            return set()
        return set(keys)

    @staticmethod
    def _set_notified_available(agent: Any, keys) -> None:
        """Persist the notified-available set as a list for Evennia attributes."""
        agent.db.notified_available_abilities = list(keys)

    @staticmethod
    def _notify_owner(agent: Any, notify: bool, message: str) -> None:
        """Send *message* to the agent's owning player when notifications are on.

        No-ops when ``notify`` is False, the agent has no ``db.owner``, or the
        owner has no ``msg`` method (e.g. offline-only or test fakes).
        """
        if not notify:
            return
        owner = getattr(getattr(agent, "db", None), "owner", None)
        if owner is None or not hasattr(owner, "msg"):
            return
        try:
            owner.msg(message)
        except Exception:
            logger.exception(
                "Failed to notify owner of agent %s",
                getattr(agent, "key", "?"),
            )

    # ------------------------------------------------------------------ #
    #  Ability enable / disable / status command backends
    #  (Req 13.5, 16.2-16.7, 17.2, 17.5)
    # ------------------------------------------------------------------ #

    def enable_ability(self, player: Any, agent_id: Any, key: str) -> str:
        """Enable a gated ability *key* for the owner's *agent_id*.

        Validates ownership (unknown agent → reject, Req 16.7) and that *key* is
        a known ability gate (unknown key → reject, Req 16.6). When the agent's
        ``Effective_Level`` meets or exceeds the gate's required level, records
        the key in the enabled set, attaches the gate's behavior script (which
        initializes its delivery state), and confirms (Req 16.2, 17.2). When
        below the gate, rejects with the required level and neither records the
        key nor attaches the script (Req 16.3). Generic across keys (Req 13.5).

        Returns a human-readable string for the command layer to ``msg()``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return f"Agent #{agent_id} not found."

        if key not in self.registry.ability_gates:
            return f"Unknown ability '{key}'."

        gate = self.registry.get_ability_gate(key)
        effective = self.compute_effective_level(agent)

        if effective < gate.required_level:
            return (
                f"Agent #{agent_id} cannot enable '{key}' yet — requires "
                f"level {gate.required_level} (currently level {effective})."
            )

        # Record the key in the enabled set (sticky, Req 17.1/17.2).
        enabled = self.get_enabled_abilities(agent)
        enabled.add(key)
        self._set_enabled_abilities(agent, enabled)

        # Attach the gate's behavior script (inits delivery state, Req 16.2).
        self._attach_single_script(agent, self.resolve_ability_script(key))

        # Enabling attaches the script directly (bypassing the
        # available-but-not-enabled branch of evaluate_gated_abilities), so
        # clear any stale "available" notification flag here too. Otherwise a
        # later detach + re-cross would find the flag set and skip the
        # legitimate re-notification (Req 15.2, Property 10).
        notified = self._get_notified_available(agent)
        if key in notified:
            notified.discard(key)
            self._set_notified_available(agent, notified)

        return f"Ability '{key}' enabled for Agent #{agent_id}."

    def disable_ability(self, player: Any, agent_id: Any, key: str) -> str:
        """Disable a gated ability *key* for the owner's *agent_id*.

        Validates ownership (unknown agent → reject, Req 16.7) and that *key* is
        a known ability gate (unknown key → reject, Req 16.6). Clears the key
        from the enabled set so it does not auto-re-attach (Req 17.5) and detaches
        only that ability's behavior script via ``_detach_single_script`` —
        ``HarvesterScript`` and any other scripts stay attached (Req 16.4, 9.6).

        Returns a human-readable string for the command layer to ``msg()``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return f"Agent #{agent_id} not found."

        if key not in self.registry.ability_gates:
            return f"Unknown ability '{key}'."

        # Clear the enabled flag (Req 16.4, 17.5).
        enabled = self.get_enabled_abilities(agent)
        enabled.discard(key)
        self._set_enabled_abilities(agent, enabled)

        # Detach only this ability's script, leaving HarvesterScript et al.
        script_cls = self.resolve_ability_script(key)
        if script_cls is not None:
            self._detach_single_script(
                agent, self._ability_script_key(script_cls)
            )

        return f"Ability '{key}' disabled for Agent #{agent_id}."

    def get_ability_status(self, player: Any, agent_id: Any) -> str:
        """Return a per-ability status summary for the owner's *agent_id*.

        Validates ownership (unknown agent → reject, Req 16.7). For each gate in
        the registry, reports one of (Req 16.5):

        - ``locked (Lv N)`` when the agent's ``Effective_Level`` is below the
          gate's required level N;
        - ``available`` when the effective level meets/exceeds the gate but the
          key is not enabled;
        - ``enabled`` when the key is in the agent's enabled set.

        Generic across all gate keys (Req 13.5). Returns a readable multi-line
        string for the command layer to ``msg()``.
        """
        agent = self.get_agent_by_id(player, agent_id)
        if agent is None:
            return f"Agent #{agent_id} not found."

        effective = self.compute_effective_level(agent)
        enabled = self.get_enabled_abilities(agent)

        gates = self.registry.get_ability_gates()
        if not gates:
            return f"Agent #{agent_id} has no gated abilities."

        lines = [f"Agent #{agent_id} abilities (level {effective}):"]
        for gate in gates:
            state, required = self._classify_ability(
                effective, gate.required_level, gate.key in enabled
            )
            label = f"locked (Lv {required})" if state == "locked" else state
            lines.append(f"  {gate.key}: {label}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Roster progression view  (Req 11.1, 11.2, 11.3, 11.4, 14.5)
    # ------------------------------------------------------------------ #

    def _rank_name_for_level(self, level: int) -> str:
        """Return the cosmetic rank name for a (effective) *level*.

        Mirrors how ``RankSystem`` derives a rank name from a level: map the
        level to a rank number via ``rank_from_level`` then find the registry
        ``RankDef`` whose ``.level`` equals that rank number, returning its
        ``.name`` with underscores normalized to spaces. Falls back to a
        generic ``Rank N`` when no matching ``RankDef`` is loaded so the view
        never raises (Req 11.1, 14.5).
        """
        from world.systems.rank_system import rank_from_level

        rank_num = rank_from_level(int(level))
        for rank in getattr(self.registry, "ranks", []) or []:
            if getattr(rank, "level", None) == rank_num:
                return str(rank.name).replace("_", " ")
        return f"Rank {rank_num}"

    def get_agent_progression_view(self, agent: Any) -> dict:
        """Return a roster-display view of an agent's capped progression.

        Computes everything on demand from the agent's own Combat_XP and the
        owner-level cap, so the view can never go stale:

        - ``effective_level``: ``compute_effective_level(agent)`` — the
          owner-capped level (Req 11.1).
        - ``rank_name``: the cosmetic rank name derived from the *effective*
          level, not the raw level, so a capped agent shows its capped rank
          (Req 11.1, 14.5).
        - ``ability_status``: a map of each registry gate key to its status —
          ``'enabled'`` when the key is in the agent's enabled set,
          ``'available'`` when ``effective_level >= required_level`` but not
          enabled, else ``'locked:N'`` with ``N`` the gate's required level
          (Req 11.2, 11.3).
        - ``capped_by_commander``: ``True`` iff the agent's Raw_Level exceeds
          its Effective_Level, i.e. the owner cap is actively suppressing it
          (Req 11.4).

        Generic across all gate keys (no delivery-specific behavior).
        """
        effective = self.compute_effective_level(agent)
        raw_level = self._raw_level(agent)

        enabled = self.get_enabled_abilities(agent)
        ability_status: dict[str, str] = {}
        for gate in self.registry.get_ability_gates():
            ability_status[gate.key] = self._encode_ability_status(
                effective, gate.required_level, gate.key in enabled
            )

        return {
            "effective_level": effective,
            "rank_name": self._rank_name_for_level(effective),
            "ability_status": ability_status,
            "capped_by_commander": raw_level > effective,
        }

    # ------------------------------------------------------------------ #
    #  Freeze-aware XP award / death loss  (Req 5.7, 5.9, 5.10, 6, 14.4)
    # ------------------------------------------------------------------ #

    def _reevaluate_agent(self, agent: Any) -> None:
        """Re-evaluate gated abilities after an XP change, if available.

        ``evaluate_gated_abilities`` lands in a later task (8.3). Guard the
        call so the freeze-aware award path works correctly before that
        method exists, and converges once it does. Defensive against errors
        so an XP award is never lost to a re-evaluation failure.
        """
        evaluate = getattr(self, "evaluate_gated_abilities", None)
        if evaluate is None:
            return
        try:
            evaluate(agent)
        except Exception:
            logger.exception(
                "evaluate_gated_abilities failed for agent %s",
                getattr(agent, "key", "?"),
            )

    def on_owner_level_changed(
        self, player: Any, old_level: Any = None, new_level: Any = None
    ) -> None:
        """Re-evaluate every owned Agent when the owning Player's level changes.

        Subscribed to the ``LEVEL_CHANGED`` event (payload ``player``,
        ``old_level``, ``new_level``); the level arguments are accepted to match
        that payload but are not needed here because each Agent's ``Cap_Ceiling``
        is recomputed from the owner's current level inside
        ``evaluate_gated_abilities`` (Req 15.5).

        For each Agent owned by *player*, recomputes ``Cap_Ceiling`` /
        ``Effective_Level`` and calls ``evaluate_gated_abilities``, which applies
        the per-gate convergence:

        - a level rise that crosses a gate marks the ability available and
          notifies the owner (no attach) unless the ability is already enabled,
          in which case it attaches the script and notifies it is active (Req
          14.7, 14.8, 15.1, 15.2, 15.3);
        - a level drop below a gate detaches the script, retains the Agent's
          enabled flag, and notifies a re-lock (Req 15.4).

        Each Agent is evaluated inside its own ``try``/``except`` so one bad
        Agent never halts re-evaluation of the rest of the roster.
        """
        for agent in self.get_agents(player):
            try:
                self.evaluate_gated_abilities(agent)
            except Exception:
                logger.exception(
                    "on_owner_level_changed: evaluate_gated_abilities failed "
                    "for agent %s",
                    getattr(agent, "key", "?"),
                )

    def award_agent_xp(self, agent: Any, source: str) -> bool:
        """FREEZE-AWARE Combat-XP award to *agent* for an earning *source*.

        Computes the effective level and cap ceiling FIRST. WHILE the agent's
        level has reached its ``Cap_Ceiling``, no XP is awarded — gain is frozen
        at the ceiling and no surplus accumulates (Req 5.9, 14.4). Otherwise the
        amount is looked up from ``registry.balance`` by *source* key, awarded
        via ``agent.award_xp`` (a zero/unknown amount is a no-op, Req 5.8), and
        the agent's effective level + gated abilities are re-evaluated (Req
        14.6). When the owner later raises the ceiling, awards resume on the
        next earning event (Req 5.10, 14.8).

        Returns ``True`` iff an award actually happened (and therefore gated
        abilities were re-evaluated), so callers like ``_process_agent_tick``
        can avoid a redundant second ``evaluate_gated_abilities`` pass.
        """
        # FREEZE check first — compute cap ceiling and compare against the
        # agent's raw level. No banking when at/above the ceiling (Req 5.9).
        cap_ceiling = self.get_cap_ceiling(agent)
        current_level = getattr(getattr(agent, "db", None), "level", None)
        if current_level is None:
            current_level = self.compute_effective_level(agent)
        if int(current_level) >= cap_ceiling:
            return False

        # Look up the data-driven amount for this source (unknown → no-op).
        field = AGENT_XP_SOURCE_FIELDS.get(source)
        if field is None:
            return False
        amount = getattr(self.registry.balance, field, 0) or 0
        if amount <= 0:
            # Zero amount → no-op (Req 5.8). Nothing changed; skip re-eval.
            return False

        agent.award_xp(amount)

        # Re-derive effective level + gated abilities after the change (Req 14.6).
        self._reevaluate_agent(agent)
        return True

    def apply_agent_death_loss(self, agent: Any) -> None:
        """Apply the configured death-loss XP penalty to *agent*.

        Deducts ``balance.agent_xp_death_loss`` via ``agent.deduct_xp`` (floored
        at 0 by ``CombatEntity``), then re-derives the effective level and gated
        abilities (Req 6.1, 6.2, 6.3, 14.6). Death loss is NEVER frozen — it only
        reduces XP, never adds past the ceiling.
        """
        amount = getattr(self.registry.balance, "agent_xp_death_loss", 0) or 0
        if amount > 0:
            agent.deduct_xp(amount)
        self._reevaluate_agent(agent)

    def handle_demotion(self, player: Any, new_agent_cap: int) -> None:
        """Reserve highest-ID agents that exceed the new cap.

        new_agent_cap includes the commander slot, so agent-only max = cap - 1.
        """
        agents = self.get_agents(player)
        agents.sort(key=lambda a: getattr(a.db, "agent_id", 0), reverse=True)

        max_agents = new_agent_cap - 1
        excess = len(agents) - max_agents
        if excess <= 0:
            return

        for agent in agents:
            if excess <= 0:
                break
            if not getattr(agent.db, "reserve", False):
                agent.db.reserve = True
                excess -= 1

    def handle_promotion(self, player: Any, new_agent_cap: int) -> None:
        """Restore reserved agents up to the new cap (lowest IDs first).

        new_agent_cap includes the commander slot, so agent-only max = cap - 1.
        """
        agents = self.get_agents(player)
        agents.sort(key=lambda a: getattr(a.db, "agent_id", 0))

        max_agents = new_agent_cap - 1
        reserved = [a for a in agents if getattr(a.db, "reserve", False)]
        active = len(agents) - len(reserved)
        slots_available = max_agents - active

        for agent in reserved:
            if slots_available <= 0:
                break
            agent.db.reserve = False
            slots_available -= 1

    # ------------------------------------------------------------------ #
    #  Training timer processing
    # ------------------------------------------------------------------ #

    # How often to send training progress updates (in ticks/seconds)
    # (imported from world.constants)

    def process_training_tick(self, buildings: list) -> None:
        """Decrement training timers on Academy buildings and spawn agents.

        Called once per game tick.  For each building with an active
        ``training_ticks_remaining``, decrements by 1.  When the timer
        reaches 0, calls :meth:`complete_training` to spawn the NPC.

        Args:
            buildings: Iterable of building objects to check.
        """
        for building in buildings:
            agent_id = self._get_building_attr(building, "training_agent_id")
            if agent_id is None:
                continue

            remaining = self._get_building_attr(
                building, "training_ticks_remaining", 0
            )
            if remaining is None or remaining <= 0:
                self.complete_training(building)
                continue

            remaining -= 1
            self._set_building_attr(building, "training_ticks_remaining", remaining)

            if remaining <= 0:
                self.complete_training(building)
                continue

            # Periodic progress update — only if player is inside the Academy
            if remaining % TRAINING_PROGRESS_INTERVAL == 0:
                player = self._get_building_attr(building, "training_owner")
                if player is not None and hasattr(player, "msg"):
                    if self._player_inside_building(player, building):
                        player.msg(
                            f"|y[Training] Agent #{agent_id}... "
                            f"{remaining}s remaining|n"
                        )

    # ------------------------------------------------------------------ #
    #  Tick processing
    # ------------------------------------------------------------------ #

    def _is_actively_assigned(self, agent: Any) -> bool:
        """True iff *agent* is actively assigned: has a non-empty ``db.role``,
        is not reserved, and is not incapacitated (Req 5.5).
        """
        db = getattr(agent, "db", None)
        if db is None:
            return False
        role = getattr(db, "role", None)
        if not role:
            return False
        if getattr(db, "reserve", False):
            return False
        if getattr(db, "incapacitated", False):
            return False
        return True

    def _process_agent_tick(self, agent: Any) -> None:
        """Award time-served XP and converge gated abilities for one agent.

        For an actively-assigned, non-reserved, non-incapacitated agent, awards
        ``"time_served"`` once per tick (a zero configured amount is a no-op via
        ``CombatEntity.award_xp``, Req 5.8; an agent frozen at its cap ceiling is
        short-circuited inside ``award_agent_xp``, Req 5.9), then runs a
        defensive ``evaluate_gated_abilities`` re-eval so an agent whose effective
        level changed converges (Req 14.6).
        """
        if not self._is_actively_assigned(agent):
            return
        # award_agent_xp already re-evaluates gated abilities when it awards, so
        # only run the defensive convergence pass when it did NOT (frozen at the
        # ceiling, or a zero/unknown amount) — e.g. after a direct XP edit that
        # changed the effective level out-of-band. Avoids a duplicate per-agent
        # script scan every tick when time-served XP is configured > 0.
        if not self.award_agent_xp(agent, "time_served"):
            self.evaluate_gated_abilities(agent)

    def process_tick(self, tick_number: int) -> None:
        """Process all agent-related per-tick work.

        For each actively-assigned agent, awards the configured time-served XP
        once per tick and re-evaluates its gated abilities (Req 5.5, 5.8, 5.9).
        Then iterates all agents with behavior scripts (interval=0) and calls
        ``at_repeat()`` on each script to drive polling-based behaviors
        (harvesting, patrol, delivery).

        Each agent's award + gate re-eval is wrapped in its own try/except so a
        single misbehaving agent never halts the whole tick (Req 5.5).
        """
        try:
            from evennia.utils.search import search_object_by_tag

            agents = list(search_object_by_tag("agent", category="npc_type"))
        except Exception:
            return

        # Per-tick progression: award time-served XP + converge gated abilities.
        for agent in agents:
            try:
                self._process_agent_tick(agent)
            except Exception:
                logger.exception(
                    "Error processing agent tick for %s",
                    getattr(agent, "key", "?"),
                )

        # Drive polling-based behavior scripts.
        for agent in agents:
            if not hasattr(agent, "scripts"):
                continue
            # Reserved (benched) agents do no per-tick work: their scripts stay
            # attached but must not produce resources or advance construction
            # while sidelined by an owner demotion (handle_demotion sets
            # reserve without detaching scripts). Incapacitated agents are NOT
            # skipped here — each script guards incapacitation itself, and
            # DeliveryBehavior needs at_repeat to drop carried resources.
            if getattr(getattr(agent, "db", None), "reserve", False):
                continue
            try:
                for script in agent.scripts.all():
                    if getattr(script, "interval", None) == 0:
                        try:
                            script.at_repeat()
                        except Exception:
                            logger.exception(
                                "Error in script %s on %s",
                                getattr(script, "key", "?"),
                                getattr(agent, "key", "?"),
                            )
            except Exception:
                pass

    def restore_training_cache(self) -> int:
        """Repopulate _training_buildings from the DB after a server restart.

        Called once from game_init. Returns the number of buildings found.
        """
        self._training_buildings.clear()
        try:
            from evennia.objects.models import ObjectDB
            candidates = list(
                ObjectDB.objects.filter(
                    db_attributes__db_key="training_agent_id",
                )
            )
            for b in candidates:
                val = b.attributes.get("training_agent_id")
                if val is not None:
                    self._training_buildings.append(b)
        except Exception:
            pass
        return len(self._training_buildings)

    # ------------------------------------------------------------------ #
    #  Behavior script management
    # ------------------------------------------------------------------ #

    def _attach_behavior_script(self, agent: Any, role: str) -> None:
        """Attach the Evennia base role Script(s) for *role*, then gate abilities.

        Uses ``ROLE_SCRIPT_MAP`` from ``agent_scripts`` to look up the correct
        base Script class (or list of classes) and adds each via Evennia's
        ``scripts.add``. When a role maps to a list, every script in the list is
        attached (list-handling path retained for any future multi-script role).

        After the base role script(s) are attached, ``evaluate_gated_abilities``
        runs unconditionally so gated abilities (e.g. ``DeliveryBehavior`` for
        harvesters) attach if and only if the agent's ``Effective_Level`` meets or
        exceeds the gate's required level AND the owning player has enabled that
        ability. For harvesters this means ``HarvesterScript`` always attaches and
        ``DeliveryBehavior`` attaches only via the gate evaluation, so the
        ``assign_agent`` reserve-restore/reassign path attaches delivery iff
        effective ≥ gate AND enabled (Req 8.1, 8.2, 8.3, 8.5, 8.6, 10.3, 10.4,
        12.6).

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            from typeclasses.agent_scripts import ROLE_SCRIPT_MAP

            value = ROLE_SCRIPT_MAP.get(role)

            if value is not None and hasattr(agent, "scripts"):
                # Normalise to a list so both single classes and lists are handled
                script_classes = value if isinstance(value, list) else [value]
                for script_cls in script_classes:
                    agent.scripts.add(script_cls)
        except Exception:
            pass

        # Regardless of role, converge gated abilities so delivery (and any
        # future gated ability) attaches only when effective level meets the
        # gate AND the player has enabled it. Defensive so a base-script attach
        # is never undone by a gate-evaluation failure.
        try:
            self.evaluate_gated_abilities(agent)
        except Exception:
            logger.exception(
                "evaluate_gated_abilities failed during _attach_behavior_script "
                "for agent %s",
                getattr(agent, "key", "?"),
            )

    @staticmethod
    def _detach_behavior_script(agent: Any) -> None:
        """Remove any agent behavior script(s) from the NPC.

        Removes all scripts whose key matches a known behavior script.
        Uses hardcoded keys to avoid instantiating Evennia Script classes
        outside the DB context (which silently fails).
        """
        try:
            if not hasattr(agent, "scripts"):
                return

            known_keys = {
                "harvester_script",
                "engineer_script",
                "patrol_behavior",
                "delivery_behavior",
                "soldier_script",
                "medic_script",
            }

            for script in list(agent.scripts.all()):
                if getattr(script, "key", "") in known_keys:
                    script.delete()
        except Exception:
            pass

    @staticmethod
    def resolve_ability_script(key: str) -> type | None:
        """Resolve a gated ability *key* to its Script class.

        Looks up ``ABILITY_SCRIPT_MAP`` from ``agent_scripts`` lazily so the
        system stays decoupled from Script construction and importable outside
        the Evennia DB context. Returns the Script class, or ``None`` when the
        key is unresolved or the import fails (Req 13.4).
        """
        try:
            from typeclasses.agent_scripts import ABILITY_SCRIPT_MAP

            return ABILITY_SCRIPT_MAP.get(key)
        except Exception:
            return None

    @staticmethod
    def _ability_script_key(script_cls: type) -> str | None:
        """Return the Evennia ``key`` for a gated ability Script class.

        Script subclasses set ``key`` inside ``at_script_creation`` rather than
        as a class attribute, so we map by class name via ``ABILITY_SCRIPT_KEYS``
        to avoid instantiating the class outside the DB context. Falls back to a
        class-level ``key`` attribute if one is reliably present.
        """
        name = getattr(script_cls, "__name__", "")
        mapped = ABILITY_SCRIPT_KEYS.get(name)
        if mapped:
            return mapped
        key = getattr(script_cls, "key", "")
        return key or None

    def _attach_single_script(self, agent: Any, script_cls: type) -> None:
        """Idempotently attach a single gated ability *script_cls* to *agent*.

        Checks the agent's existing scripts by ``key`` before adding so a
        duplicate is never attached (Req 9.4). When attaching ``DeliveryBehavior``,
        initializes ``delivery_state = DeliveryState.IDLE`` so the delivery FSM
        starts from a clean state (Req 9.3).

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            if script_cls is None:
                return
            if not hasattr(agent, "scripts"):
                return

            script_key = self._ability_script_key(script_cls)

            # Idempotency: don't add if a script with this key already exists.
            if script_key is not None:
                for script in list(agent.scripts.all()):
                    if getattr(script, "key", "") == script_key:
                        return

            agent.scripts.add(script_cls)

            # Initialize delivery FSM state when attaching DeliveryBehavior.
            if getattr(script_cls, "__name__", "") == "DeliveryBehavior":
                agent.db.delivery_state = DeliveryState.IDLE
        except Exception:
            pass

    @staticmethod
    def _detach_single_script(agent: Any, script_key: str) -> None:
        """Remove only the gated script whose key == *script_key*.

        Unlike ``_detach_behavior_script`` (which removes all behavior scripts on
        reassignment), this removes a single named ability script, leaving all
        other scripts — including ``HarvesterScript`` — attached. Used by gate
        re-lock and player disable (Req 13.4).

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            if not hasattr(agent, "scripts"):
                return

            for script in list(agent.scripts.all()):
                if getattr(script, "key", "") == script_key:
                    script.delete()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_path_to(
        agent: Any, start_x: int, start_y: int, goal_x: int, goal_y: int
    ) -> list[tuple[int, int]]:
        """Compute a path from (start_x, start_y) to (goal_x, goal_y).

        Delegates to ``compute_path_for_npc`` in the pathfinding module.
        Returns an empty list if no path exists.
        """
        from world.pathfinding import compute_path_for_npc
        return compute_path_for_npc(agent, (start_x, start_y), (goal_x, goal_y))

    @staticmethod
    def _find_hq(player: Any) -> Any | None:
        """Find the player's HQ building, if any."""
        try:
            buildings = player.get_buildings()
            for b in buildings:
                if getattr(b.db, "building_type", "") == "HQ":
                    return b
        except Exception:
            pass
        return None

    @staticmethod
    def _get_planet_bounds(agent: Any) -> tuple[int, int]:
        """Return (width, height) for the planet the agent is on.

        Tries to resolve via the agent's PlanetRoom and game systems.
        Falls back to a generous default if unavailable.
        """
        planet_room = getattr(agent, "location", None)
        if planet_room is not None:
            systems = None
            if hasattr(planet_room, "_game_systems"):
                systems = planet_room._game_systems
            if systems:
                registry = systems.get("registry")
                planet_key = getattr(
                    getattr(planet_room, "db", None), "planet", None
                )
                if registry and planet_key:
                    try:
                        planet_def = registry.get_planet(planet_key)
                        coord_space = registry.get_coord_space(
                            planet_def.coord_space
                        )
                        return coord_space.width, coord_space.height
                    except (KeyError, AttributeError):
                        pass
            # Try reading width/height directly from the room
            w = getattr(getattr(planet_room, "db", None), "width", None)
            h = getattr(getattr(planet_room, "db", None), "height", None)
            if w is not None and h is not None:
                return int(w), int(h)
        # Generous fallback — matches no real planet, but prevents
        # out-of-bounds rejections in edge cases.
        return 256, 256

    @staticmethod
    def _player_inside_building(player: Any, building: Any) -> bool:
        """Return True if the player is inside the given building."""
        from world.utils import player_inside_building
        return player_inside_building(player, building)

    @staticmethod
    def _get_building_attr(building: Any, key: str, default: Any = None) -> Any:
        """Read an attribute from a building object safely."""
        return _get_building_attr_shared(building, key, default)

    @staticmethod
    def _set_building_attr(building: Any, key: str, value: Any) -> None:
        """Write an attribute on a building object safely."""
        _set_building_attr_shared(building, key, value)
