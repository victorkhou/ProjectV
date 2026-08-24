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
from world.utils import coords_of, resting_activity_status
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
# Re-exported from agent_constants for convenience.
from world.systems.agent_constants import (  # noqa: E402
    logger,
    VALID_ROLES,
    ALL_ROLES,
    BUILDING_ROLE_MAP,
    ARMY_ROLES,
    AGENT_XP_SOURCE_FIELDS,
    ABILITY_SCRIPT_KEYS,
    GATED_BRANCH_ROLES,
    GATED_ROLE_FOR_BRANCH,
    UNGATED_BRANCH_ROLES,
)

#: Agent fields the admin ``@agent set`` verb may write through
#: :meth:`AgentSystem.admin_set_agent_field`. Bounds and
#: permission tiers for these live in the AgentAdapter's Field_Specs.
ADMIN_SETTABLE_AGENT_FIELDS: tuple[str, ...] = (
    "hp",
    "hp_max",
    "kills",
    "deaths",
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
        # The Branch_System, injected at the composition root via
        # set_branch_resolver. None until then, and None in every fixture that
        # predates the Branch feature — which is why the role gate keeps its
        # pre-feature behavior (no gate) rather than assuming a resolver.
        self._branch: Any = None

    # ------------------------------------------------------------------ #
    #  Branch resolver injection
    # ------------------------------------------------------------------ #

    def set_branch_resolver(self, resolver: Any) -> None:
        """Inject the Branch_System that owns Branch_Commitment.

        Called once at the composition root. The agent system asks the resolver
        which Branch is live for a player on a planet rather than deriving it
        itself, so commitment has exactly one implementation.

        Pass ``None`` to unwire it, which restores the pre-feature behavior
        exactly: the Branch role gate does not apply and every role in
        ``VALID_ROLES`` is assignable on the pre-feature terms alone.
        """
        self._branch = resolver

    def _refuse_branch_role(
        self, player: Any, agent: Any, role: str
    ) -> str | None:
        """Return a refusal message when *role* is gated shut for *player*.

        The Branch_Commitment gate (R7.6, R7.7): a role this feature introduced
        is assignable only while the player's commitment **on the agent's
        planet** is the Branch that role belongs to, and a refusal names the
        Branch the role requires.

        Returns ``None`` — assignment permitted — in every other case:

        * a role outside :data:`GATED_BRANCH_ROLES`, which is every pre-feature
          role plus `scout` (see :data:`UNGATED_BRANCH_ROLES` for that decided
          asymmetry);
        * no Branch resolver wired, or one exposing no ``commitment``, so an
          unwired deployment behaves exactly as it did before the feature;
        * a resolver that raises — the gate fails **open** and logs, because a
          collaborator's failure must not lock a player out of their own roster
          (R15.3).
        """
        required = GATED_BRANCH_ROLES.get(role)
        if required is None:
            return None
        resolver = self._branch
        if resolver is None:
            return None
        derive = getattr(resolver, "commitment", None)
        if not callable(derive):
            return None

        # The commitment is per-planet, and the planet that matters is the one
        # the AGENT stands on — not its owner's, who may be somewhere else. A
        # None planet is passed through: the resolver reads it as "the planet
        # the player occupies", the same fallback every other caller gets.
        planet = getattr(getattr(agent, "db", None), "coord_planet", None)
        try:
            current = derive(player, planet)
        except Exception:  # noqa: BLE001 - a resolver never breaks assignment
            logger.exception(
                "Branch resolver failed to derive a commitment; leaving the "
                "role gate open for this assignment."
            )
            return None

        if current == required:
            return None

        return (
            f"Role '{role}' belongs to the {required} Branch — it can only be "
            f"assigned while {required} is your Branch commitment on that "
            f"agent's planet."
        )

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
        # truth: agent IDs are strictly increasing, unique, and never reused.
        # The roster only ever raises the floor (never lowers it).
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

        # Economy XP award for training completion — via the shared
        # award_player_xp choke point.
        from world.utils import award_player_xp
        amount = getattr(self.registry.balance, "xp_agent_trained", 0) or 0
        award_player_xp(player, amount, reason="agent_trained")

        # Directive trigger
        try:
            from world.event_bus import AGENT_TRAINED
            self.event_bus.publish(AGENT_TRAINED, player=player, agent_id=agent_id)
        except Exception:
            pass

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
        allow_hidden: bool = False,
    ) -> tuple[bool, str]:
        """Assign *agent_id* to *role*, optionally at *target_building*.

        Validates:
        - Agent exists and belongs to player.
        - Agent is not incapacitated or reserved.
        - Role is valid (hidden roles only when ``allow_hidden`` —
          the admin/test escape hatch for placeholder roles).
        - Branch_Commitment, for the five roles this feature introduced
          (R7.6, R7.7) — see :meth:`_refuse_branch_role`.
        - Building/role match (Extractor→Harvester, etc.).
        - Army roles (guard, scout, medic and the four Branch roles — and the
          hidden soldier) don't need a building.

        Returns ``(success, message)``.
        """
        role = role.lower()
        valid = ALL_ROLES if allow_hidden else VALID_ROLES
        if role not in valid:
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

        # --- Branch_Commitment gate (R7.6, R7.7) ---
        refusal = self._refuse_branch_role(player, agent, role)
        if refusal is not None:
            return False, refusal

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
            b_coords = coords_of(target_building)
            if b_coords is not None:
                bx, by, _planet = b_coords
                bx, by = int(bx), int(by)

                # Ensure agent is in the PlanetRoom (old agents may lack location)
                if getattr(agent, "location", None) is None:
                    planet_room = getattr(player, "location", None)
                    if planet_room is not None:
                        agent.location = planet_room
                        # Set initial coords to player position
                        p_coords = coords_of(player)
                        if p_coords is not None:
                            px, py, _planet = p_coords
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
            # Army role (guard/scout/medic, the Branch roles, hidden soldier) —
            # no target building, so the movement block above (which derives the arrival
            # status) never runs. Derive the resting status here so the agent
            # reads "Ready" on assignment instead of a stale "Working"/"Idle"
            # left from a prior role.
            agent.db.activity_status = resting_activity_status(agent)

        # Directive trigger
        try:
            from world.event_bus import AGENT_ASSIGNED
            self.event_bus.publish(
                AGENT_ASSIGNED, player=player, agent_id=agent_id, role=role,
            )
        except Exception:
            pass

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
            hq_coords = coords_of(hq)
            if hq_coords is not None:
                hx, hy, _planet = hq_coords
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

    def unassign_branch_roles(
        self, player: Any, planet: Any, branch: str
    ) -> int:
        """Release every agent of *player* holding *branch*'s role on *planet*.

        The dormancy release (R7.8): a Branch that is no longer *player*'s
        commitment on that planet commands no agents, so each agent standing
        there in that Branch's role goes back to the unassigned state. The
        Branch_System calls this the moment a commitment lapses — it owns the
        trigger, this owns the roster.

        Every agent is released through :meth:`unassign_agent`, so the teardown
        is the existing one (``_detach_behavior_script``, the ``role_target``
        clear, the building's ``assigned_agent`` release, the walk back to HQ)
        rather than a second implementation that could drift from it. Each
        release is isolated, so one unreleasable agent never strands the rest.

        Only the five gated roles are released: `scout` is exempt for the same
        reason it is exempt from the assignment gate (see
        :data:`UNGATED_BRANCH_ROLES`) — a lapsed Recon commitment leaves
        existing patrols running. A Branch outside the six, a blank one, or one
        owning no gated role is a no-op rather than an error (R15.3).

        Args:
            player: The owner whose roster to release from.
            planet: The planet the lapse happened on. ``None`` falls back to the
                planet *player* occupies, matching how every other commitment
                read resolves an unspecified planet.
            branch: The Branch that lapsed.

        Returns:
            The number of agents released, so callers and tests can read the
            effect instead of inferring it.
        """
        wanted = branch.strip().lower() if isinstance(branch, str) else None
        role = GATED_ROLE_FOR_BRANCH.get(wanted)
        if role is None or player is None:
            return 0

        if planet is None:
            planet = getattr(getattr(player, "db", None), "coord_planet", None)

        released = 0
        for agent in self.get_agents(player):
            db = getattr(agent, "db", None)
            if db is None:
                continue
            if (getattr(db, "role", "") or "").lower() != role:
                continue
            # Scoped per-planet because a commitment is: the same player may
            # hold this or another Branch elsewhere, and those agents keep
            # serving. Strict equality is deliberate on BOTH unreadable sides,
            # and deliberately NARROWER than eligible_carrier's "counts on
            # every planet" wildcard: a release is a destructive write, so an
            # agent whose planet cannot be read is skipped (it may belong to a
            # planet where this same Branch is still committed), and an
            # unresolvable lapse planet releases only such placeless agents
            # rather than sweeping every planet. The hole this leaves is
            # harmless — a role kept past its commitment commands nothing,
            # because the operation chain's own commitment check (R8.3)
            # refuses its requests regardless of the roster.
            if getattr(db, "coord_planet", None) != planet:
                continue
            agent_id = getattr(db, "agent_id", None)
            if agent_id is None:
                continue
            try:
                ok, _msg = self.unassign_agent(player, agent_id)
            except Exception:  # noqa: BLE001 - one agent never strands the rest
                logger.exception(
                    "unassign_branch_roles: failed to release agent %s from "
                    "role %s", agent_id, role,
                )
                continue
            if ok:
                released += 1

        if released:
            logger.debug(
                "Released %d %s agent(s) on %r: %r is no longer committed.",
                released, role, planet, branch,
            )
        return released

    # ------------------------------------------------------------------ #
    #  Operation XP
    # ------------------------------------------------------------------ #

    def award_operation_xp(self, agent: Any, kind: str) -> bool:
        """Award *agent* the Combat-XP a completed *kind* operation is worth.

        The Carrier_Agent XP award (R7.10). The amount is **not** stored here:
        the Operation_Kind definition names a ``BalanceConfig`` field
        (``OperationKindDef.agent_xp_field``) and this reads that field, so
        tuning stays in ``balance.yaml`` behind ``@reload`` and the
        vector-to-field binding stays in one reviewable data table.

        Routes through the same freeze-aware body as every other agent-XP
        source, so an agent sitting at its owner-level ceiling banks nothing
        from an operation either — carrying a vector is an earning event like
        harvesting, not an exception to the cap.

        Args:
            agent: The Carrier_Agent that completed the operation.
            kind: The Operation_Kind identifier.

        Returns:
            ``True`` iff an award actually happened. An unknown *kind*, a
            definition naming no field, an unloaded field, or a zero amount is
            a no-op returning ``False`` — never an error, because a missing
            tunable must not undo a resolved operation.
        """
        kinds = getattr(self.registry, "operation_kinds", None) or {}
        kdef = kinds.get(kind)
        if kdef is None:
            logger.debug(
                "award_operation_xp: no Operation_Kind definition for %r; "
                "no XP awarded.", kind,
            )
            return False
        field = getattr(kdef, "agent_xp_field", None)
        if not field:
            return False
        return self._award_agent_xp_field(agent, field)

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

        # Directive trigger — role in payload so guard and scout handlers
        # can condition on it.
        try:
            from world.event_bus import PATROL_SET
            self.event_bus.publish(
                PATROL_SET, player=player, agent_id=agent_id, role=role,
            )
        except Exception:
            pass

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

        Rank derives from the player's LEVEL via the RANK_BANDS lookup —
        not from ranks.yaml ``xp_threshold`` values. agent_cap in YAML
        includes the commander slot, so the usable agent-only cap is
        ``agent_cap - 1``. When ranks.yaml lacks the exact rank number, the
        highest defined rank at or below it applies.
        """
        from world.systems.rank_system import rank_from_level
        from world.utils import get_player_level

        rank_num = rank_from_level(get_player_level(player))
        rank_def = self.registry.get_rank_by_level(rank_num)
        if rank_def is None:
            candidates = [r for r in self.registry.ranks if r.level <= rank_num]
            rank_def = (max(candidates, key=lambda r: r.level)
                        if candidates else self.registry.ranks[0])
        return rank_def.agent_cap - 1

    # ------------------------------------------------------------------ #
    #  Admin single-writer paths (unified-admin-crud @agent adapter)
    # ------------------------------------------------------------------ #
    #
    # The AgentAdapter (``world/admin/adapters/agent_adapter.py``) routes
    # every admin write through these methods so AgentSystem stays the
    # single writer for agent state.

    def admin_create_agent(self, player: Any) -> Any | None:
        """Create one agent NPC for *player* instantly (admin spawn path).

        Bypasses the training cost and timer (the admin override the
        legacy ``@agent create`` provided) but uses the same monotonic
        ``next_agent_id`` rule as :meth:`train_agent`, so agent IDs stay
        strictly increasing and are never reused.

        Returns the new NPC, or ``None`` when the factory fails.
        """
        counter = getattr(getattr(player, "db", None), "next_agent_id", None)
        try:
            counter = int(counter)
        except (TypeError, ValueError):
            counter = 1
        roster_floor = 1
        agents = self.get_agents(player)
        if agents:
            roster_floor = max(
                getattr(a.db, "agent_id", 0) for a in agents
            ) + 1
        next_id = max(counter, roster_floor)

        npc = self._create_npc_func(player, next_id)
        if npc is None:
            return None
        player.db.next_agent_id = next_id + 1
        return npc

    def admin_set_agent_field(
        self, agent: Any, field: str, value: Any
    ) -> bool:
        """Write one admin-settable field onto *agent*.

        The single-writer path for admin field writes (``@agent set``).
        Only fields in :data:`ADMIN_SETTABLE_AGENT_FIELDS` are accepted;
        bounds/clamping are the admin layer's concern — this method only
        performs the guarded write. Returns ``True`` on success.
        """
        if field not in ADMIN_SETTABLE_AGENT_FIELDS:
            return False
        db = getattr(agent, "db", None)
        if db is None:
            return False
        try:
            setattr(db, field, value)
        except Exception:
            logger.exception(
                "admin_set_agent_field failed: %s=%r on %r",
                field, value, agent,
            )
            return False
        return True

    def admin_destroy_agent(self, agent: Any) -> bool:
        """Delete *agent* through the existing deletion path.

        Clears the agent's building assignment (if any) before deleting
        the NPC object — the same sequence the legacy ``@agent destroy``
        performed. Returns ``True`` when the deletion succeeded.
        """
        building = getattr(getattr(agent, "db", None), "role_target", None)
        if building is not None and hasattr(building, "db"):
            if getattr(building.db, "assigned_agent", None) is agent:
                building.db.assigned_agent = None
        deleter = getattr(agent, "delete", None)
        if not callable(deleter):
            return False
        try:
            deleter()
        except Exception:
            logger.exception("admin_destroy_agent failed for %r", agent)
            return False
        return True

    def admin_clear_training(self, player: Any) -> int:
        """Clear stuck training state on *player*'s buildings.

        The legacy ``@agent destroy training <player>`` unstick tool:
        wipes ``training_agent_id`` / ``training_ticks_remaining`` /
        ``training_owner`` on every building currently training for the
        player (their own buildings, plus any entry in the in-memory
        training cache owned by them). Returns the number of buildings
        cleared.
        """
        candidates: list[Any] = []
        try:
            candidates.extend(player.get_buildings() or [])
        except Exception:
            pass
        for building in list(self._training_buildings):
            if building not in candidates:
                candidates.append(building)

        cleared = 0
        for building in candidates:
            tid = self._get_building_attr(building, "training_agent_id")
            if tid is None:
                continue
            owner = self._get_building_attr(building, "training_owner")
            if owner is not None and owner is not player:
                continue
            self._set_building_attr(building, "training_agent_id", None)
            self._set_building_attr(building, "training_ticks_remaining", None)
            self._set_building_attr(building, "training_owner", None)
            try:
                self._training_buildings.remove(building)
            except ValueError:
                pass
            cleared += 1
        return cleared

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
        assign_agent (to the building) and unassign_agent (back to HQ). While
        walking, sets a transient ``"{moving_status} (N tiles)"`` status; once
        placed, the *resting* status is left to the single authority
        (``resting_activity_status``, applied by ``NPC.advance_movement`` on
        arrival, or set directly here on the snap branch). Callers do not pass
        an arrival status — deriving it here keeps status a single authority,
        so the movement engine cannot overwrite an engineer's "Working" with
        "Idle".
        """
        a_coords = coords_of(agent)
        path = []
        if a_coords is not None:
            ax, ay, _planet = a_coords
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
