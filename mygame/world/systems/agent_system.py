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
    BASE_TRAINING_COST,
    BASE_TRAINING_TICKS,
    ACADEMY_TRAINING_REDUCTION_PER_LEVEL,
    TRAINING_PROGRESS_INTERVAL,
    DEFAULT_CARRY_CAPACITY,
    MIN_PATROL_WAYPOINTS,
    MAX_PATROL_WAYPOINTS,
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
        n = current_count + 1
        cost = {res: base * n for res, base in BASE_TRAINING_COST.items()}

        if not player.has_resources(cost):
            cost_str = ", ".join(f"{v} {k}" for k, v in cost.items())
            return False, f"Insufficient resources. Training agent #{next_id} costs {cost_str}."

        # --- deduct resources ---
        player.deduct_resources(cost)

        # --- compute training time ---
        academy_level = getattr(academy_building.db, "building_level", 1) if academy_building else 1
        reduction = ACADEMY_TRAINING_REDUCTION_PER_LEVEL * academy_level
        training_ticks = max(1, int(BASE_TRAINING_TICKS * (1 - reduction)))

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
                    agent.db.activity_status = "Idle"
            elif hasattr(agent, "move_to"):
                # Legacy fallback: HQ doesn't have coordinates yet
                loc = getattr(hq, "location", hq)
                agent.move_to(loc, quiet=True)
                agent.db.activity_status = "Idle"
        else:
            agent.db.activity_status = "Idle"

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

        agent.db.activity_status = "Idle"

        return True, f"Agent #{agent_id} patrol route cleared."

    # ------------------------------------------------------------------ #
    #  Stop / cancel  (Req 11.1, 11.3, 11.4)
    # ------------------------------------------------------------------ #

    def stop_agent(
        self, player: Any, agent_id: int
    ) -> tuple[bool, str]:
        """Stop an agent's current movement and set it to idle.

        Clears the movement queue and sets activity_status to "Idle".
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

        agent.db.activity_status = "Idle"

        # Harvesters retain carried resources (Req 11.4) — no cleanup needed.
        # Just reset delivery_state so the behavior script can re-evaluate.
        role = getattr(agent.db, "role", "")
        if role == "harvester":
            agent.db.delivery_state = "idle"

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
    #  Demotion / Promotion  (Req 7b.13, 4.6)
    # ------------------------------------------------------------------ #

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

    def process_tick(self, tick_number: int) -> None:
        """Process all agent-related per-tick work.

        Iterates all agents with behavior scripts (interval=0) and
        calls ``at_repeat()`` on each script to drive polling-based
        behaviors (harvesting, patrol, delivery).
        """
        try:
            from evennia.utils.search import search_object_by_tag

            agents = list(search_object_by_tag("agent", category="npc_type"))
        except Exception:
            return

        for agent in agents:
            if not hasattr(agent, "scripts"):
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

    @staticmethod
    def _attach_behavior_script(agent: Any, role: str) -> None:
        """Attach the Evennia Script(s) for *role* to the agent NPC.

        Uses ``ROLE_SCRIPT_MAP`` from ``agent_scripts`` to look up the
        correct Script class (or list of classes), then adds each via
        Evennia's ``scripts.add``.  When a role maps to a list (e.g.
        ``"harvester": [HarvesterScript, DeliveryBehavior]``), all
        scripts in the list are attached.

        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            from typeclasses.agent_scripts import ROLE_SCRIPT_MAP

            value = ROLE_SCRIPT_MAP.get(role)
            if value is None:
                return

            if not hasattr(agent, "scripts"):
                return

            # Normalise to a list so both single classes and lists are handled
            script_classes = value if isinstance(value, list) else [value]
            for script_cls in script_classes:
                agent.scripts.add(script_cls)
        except Exception:
            pass

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
