"""
Agent System — manages player-owned NPC agents.

Handles training, role assignment, demotion/promotion reserve,
and per-tick processing of agent behavior scripts.

"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from world.core.ports.entity_repository import AgentFactory, AgentRepository

from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem
from world.systems.agent_behavior import AgentBehaviorMixin
from world.systems.agent_progression import AgentProgressionMixin
from world.utils import get_building_attr as _get_building_attr_shared
from world.utils import set_building_attr as _set_building_attr_shared
from world.utils import resting_activity_status
from world.constants import (
    TRAINING_PROGRESS_INTERVAL,
    DEFAULT_CARRY_CAPACITY,
    MIN_PATROL_WAYPOINTS,
    MAX_PATROL_WAYPOINTS,
    DeliveryState,
)

# ------------------------------------------------------------------ #
#  Constants
# ------------------------------------------------------------------ #
#
# Role/ability metadata is defined once in ``typeclasses.agent_scripts``; the
# derived lookups (VALID_ROLES, BUILDING_ROLE_MAP, ARMY_ROLES,
# AGENT_XP_SOURCE_FIELDS, ABILITY_SCRIPT_KEYS) plus the shared ``logger`` live
# in the leaf ``agent_constants`` module (so the mixins can share them without
# an import cycle). They are re-exported here for the many callers/tests that
# import them from ``agent_system``.
from world.systems.agent_constants import (  # noqa: E402
    logger,
    VALID_ROLES,
    BUILDING_ROLE_MAP,
    ARMY_ROLES,
    AGENT_XP_SOURCE_FIELDS,
    ABILITY_SCRIPT_KEYS,
)


class AgentSystem(AgentProgressionMixin, AgentBehaviorMixin, BaseSystem):
    """Manages player-owned NPC agents: training, assignment, reserve.

    Constructor args:
        registry:          DataRegistry for rank/building lookups.
        event_bus:          EventBus for publishing agent events.
        create_npc_func:    Optional factory ``(player, agent_id) -> NPC``.
                            Back-compat seam; when given it overrides
                            *agent_factory*. Used by the unit-test suite.
        agent_repository:   Optional :class:`AgentRepository` for roster/tick
                            queries. Defaults to the Evennia adapter.
        agent_factory:      Optional :class:`AgentFactory` for NPC creation.
                            Defaults to the Evennia adapter.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_npc_func: Callable | None = None,
        agent_repository: "AgentRepository | None" = None,
        agent_factory: "AgentFactory | None" = None,
    ) -> None:
        super().__init__(registry, event_bus)
        # Ports (injected at the composition root). Lazy Evennia-adapter
        # defaults keep the fast unit-test suite working without a live DB;
        # production injects the adapters via game_init.
        from world.adapters.evennia_agent_repository import (
            EvenniaAgentFactory,
            EvenniaAgentRepository,
        )

        self._repo: "AgentRepository" = agent_repository or EvenniaAgentRepository()
        self._factory: "AgentFactory" = agent_factory or EvenniaAgentFactory()
        # Back-compat: a raw factory callable still overrides the port so the
        # existing tests' ``create_npc_func`` seam keeps working.
        self._create_npc_func = create_npc_func or self._factory.create_agent
        # In-memory cache of buildings currently training agents.
        # Avoids a DB query every tick. Updated by train_agent/complete_training.
        self._training_buildings: list[Any] = []

    # ------------------------------------------------------------------ #
    #  Training
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
        # The persisted, monotonic ``next_agent_id`` counter is the source of
        # truth: agent IDs must be strictly increasing, unique, and NEVER reused
        # (Req 7b.5), so a counter — not the live roster — governs. Deriving from
        # ``max(roster) + 1`` would reuse an ID after the highest agent is
        # dismissed, and would collide if the roster query transiently returned
        # empty. The roster only ever raises the floor (never lowers it), so a
        # legacy player whose counter lags its roster still can't collide.
        counter = getattr(getattr(player, "db", None), "next_agent_id", None)
        try:
            counter = int(counter)
        except (TypeError, ValueError):
            counter = 1
        roster_floor = 1
        agents = self.get_agents(player)
        if agents:
            roster_floor = max(getattr(a.db, "agent_id", 0) for a in agents) + 1
        next_id = max(counter, roster_floor)

        # --- cost calculation ---
        # Cost scales with how many agents you'll have after training
        bal = self.registry.balance
        n = current_count + 1
        cost = {res: base * n for res, base in bal.base_training_cost.items()}

        if not player.has_resources(cost):
            from world.utils import format_insufficient_resources

            return False, format_insufficient_resources(player, cost)

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
        self.notify(player, "agent_training_complete", agent_id=agent_id)

        return npc

    # ------------------------------------------------------------------ #
    #  Assignment
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
        if old_target is not target_building:
            self._clear_building_assignment(old_target, agent)

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

        # Path agent to building coordinates instead of teleporting
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
                            from world.utils import place_on_tile
                            place_on_tile(agent, planet_room, px, py)

                # Walk to the building, or snap there if no path/already there.
                # The resting status on arrival ("Working") is derived from the
                # now-set role/role_target by resting_activity_status — the
                # mover no longer names it.
                self._move_agent_to(
                    agent, bx, by,
                    moving_status=f"Moving to {role} assignment",
                )
            elif hasattr(agent, "move_to"):
                # Legacy fallback: building doesn't have coordinates yet
                loc = getattr(target_building, "location", target_building)
                agent.move_to(loc, quiet=True)
        else:
            # Army role (soldier/medic) — no target building, so the movement
            # block above (which derives the arrival status) never runs. Derive
            # the resting status here so the agent reads "Ready" on assignment
            # instead of a stale "Working"/"Idle" left from a prior role.
            agent.db.activity_status = resting_activity_status(agent)

        return True, f"Agent #{agent_id} assigned as {role}."

    # ------------------------------------------------------------------ #
    #  Unassignment
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
        self._clear_building_assignment(
            getattr(agent.db, "role_target", None), agent
        )

        # Detach behavior script before clearing role
        self._detach_behavior_script(agent)

        # Clear current movement queue
        if hasattr(agent, "clear_movement"):
            agent.clear_movement()

        # Clear patrol-related attributes
        agent.db.patrol_route = None
        agent.db.patrol_waypoint_index = 0

        # Clear delivery-related attributes
        agent.db.delivery_state = None
        agent.db.carried_resources = {}
        agent.db.delivery_target = None

        agent.db.role = ""
        agent.db.role_target = None

        # Compute path to HQ instead of teleporting. role/role_target are
        # already cleared above, so the derived resting status resolves to Idle.
        hq = self._find_hq(player)
        if hq is not None:
            hx = getattr(getattr(hq, "db", None), "coord_x", None)
            hy = getattr(getattr(hq, "db", None), "coord_y", None)
            if hx is not None and hy is not None:
                # Walk back to HQ, or snap there if no path/already there.
                self._move_agent_to(
                    agent, int(hx), int(hy),
                    moving_status="Returning to HQ",
                )
            elif hasattr(agent, "move_to"):
                # Legacy fallback: HQ doesn't have coordinates yet
                loc = getattr(hq, "location", hq)
                agent.move_to(loc, quiet=True)
                agent.db.activity_status = resting_activity_status(agent)
        else:
            agent.db.activity_status = resting_activity_status(agent)

        return True, f"Agent #{agent_id} unassigned and returned to HQ."

    # ------------------------------------------------------------------ #
    #  Patrol routes
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

        # Still assigned (only the patrol route was cleared), so the derived
        # resting status is "Working" — a guard on station without an active
        # patrol, not "Idle".
        agent.db.activity_status = resting_activity_status(agent)

        return True, f"Agent #{agent_id} patrol route cleared."

    # ------------------------------------------------------------------ #
    #  Stop / cancel
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

        # Harvesters retain carried resources — no cleanup needed.
        # Just reset delivery_state so the behavior script can re-evaluate.
        role = getattr(agent.db, "role", "")
        if role == "harvester":
            agent.db.delivery_state = DeliveryState.IDLE

        # Clear the building's assigned_agent reference so it can accept
        # a new assignment.
        self._clear_building_assignment(
            getattr(agent.db, "role_target", None), agent
        )

        # Detach behavior scripts and clear role assignment
        self._detach_behavior_script(agent)
        agent.db.role = ""
        agent.db.role_target = None

        # Derive the resting status AFTER clearing the role, so a stopped agent
        # correctly reads "Idle" (not the stale role's "Working").
        agent.db.activity_status = resting_activity_status(agent)

        return True, f"Agent #{agent_id} stopped."

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def get_agents(self, player: Any) -> list:
        """Return all NPC objects tagged 'agent' owned by *player*.

        Delegates to the injected :class:`AgentRepository`, so the query
        mechanism is swappable and unit tests inject a fake with no Evennia DB.
        """
        return self._repo.find_agents_for_owner(player)

    def get_all_agents(self) -> list:
        """Return every agent NPC in the world (all owners).

        Delegates to the injected :class:`AgentRepository`. Used by the tick
        loop to feed passive systems (e.g. HP regen) the full agent roster.
        """
        return self._repo.find_all_agents()

    def get_all_enemies(self) -> list:
        """Return every enemy NPC (npc_type="enemy") — NPC-base guards.

        Delegates to the injected :class:`AgentRepository`. Used by the tick
        loop to feed the guard-combat sweep, so NPC-base guards (which are NOT
        in the agent roster) also acquire targets and fight back.
        """
        return self._repo.find_all_enemies()

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
                if player is not None and self._player_inside_building(player, building):
                    self.notify(player, "agent_training_progress",
                                agent_id=agent_id, remaining=remaining)

    # ------------------------------------------------------------------ #
    #  Tick processing
    # ------------------------------------------------------------------ #

    def _is_actively_assigned(self, agent: Any) -> bool:
        """True iff *agent* is actively assigned: has a non-empty ``db.role``,
        is not reserved, and is not incapacitated.
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
        """Award time-served XP for one agent.

        For an actively-assigned, non-reserved, non-incapacitated agent, awards
        ``"time_served"`` once per tick (a zero configured amount is a no-op via
        ``CombatEntity.award_xp``; an agent frozen at its cap ceiling is
        short-circuited inside ``award_agent_xp``). When an award happens,
        ``award_agent_xp`` re-evaluates gated abilities itself.

        Gated-ability convergence is fully event-driven — the only things that
        change an agent's effective level are its own XP award (handled above)
        and its owner's level changing (the ``LEVEL_CHANGED`` subscriber
        ``on_owner_level_changed`` re-evaluates every owned agent). So there is
        NO unconditional per-tick ``evaluate_gated_abilities`` here: under the
        shipped default (``agent_xp_time_served = 0``) that pass would otherwise
        run for every actively-assigned agent every tick — an
        O(agents x gates x scripts) scan that can never observe a change the two
        event triggers didn't already apply.
        """
        if not self._is_actively_assigned(agent):
            return
        self.award_agent_xp(agent, "time_served")

    def process_tick(self, tick_number: int, agents: list | None = None) -> None:
        """Process all agent-related per-tick work.

        For each actively-assigned agent, awards the configured time-served XP
        once per tick and re-evaluates its gated abilities.
        Then iterates all agents with behavior scripts (interval=0) and calls
        ``at_repeat()`` on each script to drive polling-based behaviors
        (harvesting, patrol, delivery).

        Each agent's award + gate re-eval is wrapped in its own try/except so a
        single misbehaving agent never halts the whole tick.

        Args:
            tick_number: The current game tick.
            agents: The agent roster for this tick. The tick loop passes its
                cached roster (invalidated only when an NPC is created/deleted —
                see ``GameTickScript._get_all_agents``) so this step does NOT
                re-issue a full ``find_all_agents`` DB tag-scan every second.
                Falls back to a live query when omitted (isolated tests).
        """
        if agents is None:
            agents = self._repo.find_all_agents()
        if not agents:
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
        self._training_buildings.extend(self._repo.find_training_buildings())
        return len(self._training_buildings)

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
    def _clear_building_assignment(old_target: Any, agent: Any) -> None:
        """Clear ``assigned_agent`` on *old_target* if it points at *agent*.

        The single teardown used by assign/unassign/stop (was copy-pasted
        verbatim three times). Handles both the attributes-handler path (live
        Building) and the ``db`` path (test doubles), and only clears when the
        reference is actually this agent (never stomps another agent's slot).
        """
        if old_target is None:
            return
        if hasattr(old_target, "attributes") and hasattr(old_target.attributes, "add"):
            if old_target.attributes.get("assigned_agent") is agent:
                old_target.attributes.add("assigned_agent", None)
        elif hasattr(old_target, "db"):
            if getattr(old_target.db, "assigned_agent", None) is agent:
                old_target.db.assigned_agent = None

    def _move_agent_to(
        self, agent: Any, gx: int, gy: int,
        moving_status: str,
    ) -> None:
        """Path *agent* toward ``(gx, gy)``; on no-path/arrival, place it there.

        The shared "walk there, else snap to the tile" move used by both
        assign_agent (to the building) and unassign_agent (back to HQ) — was
        duplicated between them. While walking, sets a transient
        ``"{moving_status} (N tiles)"`` status; once placed, the *resting*
        status is left to the single authority (``resting_activity_status``,
        applied by ``NPC.advance_movement`` on arrival, or set directly here on
        the snap branch). Callers no longer pass an arrival status — deriving it
        removes the old "the mover guesses the status" split that let the
        movement engine overwrite an engineer's "Working" with "Idle".
        """
        ax = getattr(agent.db, "coord_x", None)
        ay = getattr(agent.db, "coord_y", None)
        path = []
        if ax is not None and ay is not None:
            path = self._compute_path_to(agent, int(ax), int(ay), gx, gy)

        if path and hasattr(agent, "set_movement_queue"):
            agent.set_movement_queue(path)
            agent.db.activity_status = f"{moving_status} ({len(path)} tiles)"
            # advance_movement applies the derived resting status on arrival.
        else:
            planet_room = getattr(agent, "location", None)
            if planet_room is not None and hasattr(planet_room, "move_entity"):
                planet_room.move_entity(agent, gx, gy)
            else:
                agent.db.coord_x = gx
                agent.db.coord_y = gy
            # Already on the tile — resolve the resting status now.
            agent.db.activity_status = resting_activity_status(agent)

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
                planet_registry = systems.get("planet_registry")
                planet_key = getattr(
                    getattr(planet_room, "db", None), "planet", None
                )
                # Grid dimensions come from the PlanetRegistry's
                # CoordinateSpaceDef. (The old registry.get_coord_space(
                # planet_def.coord_space) call referenced a DataRegistry method
                # and a PlanetDef field that don't exist; the AttributeError was
                # swallowed, so this always fell through to the 256x256 default.)
                if planet_registry is not None and planet_key:
                    try:
                        space = planet_registry.get_space(planet_key)
                        return space.width, space.height
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
