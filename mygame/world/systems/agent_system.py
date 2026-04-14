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

# Base training cost for agent #N is base_cost × N
BASE_TRAINING_COST: dict[str, int] = {
    "Wood": 15,
    "Stone": 10,
    "Iron": 5,
}

# Base training time in ticks (5 minutes at 1 tick/s = 300 ticks)
BASE_TRAINING_TICKS = 300


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
        """Create an NPC agent via Evennia's object creation API."""
        import evennia

        npc = evennia.create_object(
            "typeclasses.npcs.NPC",
            key=f"Agent-{agent_id}",
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
        rank_def = self.registry.get_rank_for_xp(player.db.combat_xp)
        agent_cap = rank_def.agent_cap
        if current_count >= agent_cap:
            return False, "Agent cap reached. Promote to a higher rank for more agents."

        # --- determine next ID ---
        # Derive from existing agents to stay in sync after deletions
        agents = self.get_agents(player)
        if agents:
            max_id = max(getattr(a.db, "agent_id", 0) for a in agents)
            next_id = max_id + 1
        else:
            next_id = 2  # commander is #1

        # --- cost calculation ---
        # Cost scales with total agent count (including commander),
        # not the ID number. This keeps costs fair after agent losses.
        n = current_count + 1  # what the count will be after training
        cost = {res: base * n for res, base in BASE_TRAINING_COST.items()}

        if not player.has_resources(cost):
            cost_str = ", ".join(f"{v} {k}" for k, v in cost.items())
            return False, f"Insufficient resources. Training agent #{next_id} costs {cost_str}."

        # --- deduct resources ---
        player.deduct_resources(cost)

        # --- compute training time ---
        academy_level = getattr(academy_building.db, "building_level", 1) if academy_building else 1
        reduction = 0.15 * academy_level
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

        # Attach the behavior script for this role
        self._attach_behavior_script(agent, role)

        # Move agent to building tile or keep in army pool
        if target_building is not None and hasattr(agent, "move_to"):
            loc = getattr(target_building, "location", target_building)
            agent.move_to(loc, quiet=True)

        return True, f"Agent #{agent_id} assigned as {role}."

    # ------------------------------------------------------------------ #
    #  Unassignment  (Req 7b.7)
    # ------------------------------------------------------------------ #

    def unassign_agent(
        self, player: Any, agent_id: int
    ) -> tuple[bool, str]:
        """Clear role from *agent_id* and move to HQ.

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

        agent.db.role = ""
        agent.db.role_target = None

        # Attempt to move agent to player's HQ
        hq = self._find_hq(player)
        if hq is not None and hasattr(agent, "move_to"):
            loc = getattr(hq, "location", hq)
            agent.move_to(loc, quiet=True)

        return True, f"Agent #{agent_id} unassigned and returned to HQ."

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
        """Total agent count including the commander (ID 1)."""
        # Commander is always counted as 1
        return 1 + len(self.get_agents(player))

    # ------------------------------------------------------------------ #
    #  Demotion / Promotion  (Req 7b.13, 4.6)
    # ------------------------------------------------------------------ #

    def handle_demotion(self, player: Any, new_agent_cap: int) -> None:
        """Reserve highest-ID agents that exceed *new_agent_cap*.

        Reserved agents keep their role but cannot be reassigned.
        """
        agents = self.get_agents(player)
        # Sort by agent_id descending so highest IDs are first
        agents.sort(key=lambda a: getattr(a.db, "agent_id", 0), reverse=True)

        # Current total including commander
        total = 1 + len(agents)
        excess = total - new_agent_cap
        if excess <= 0:
            return

        for agent in agents:
            if excess <= 0:
                break
            if not getattr(agent.db, "reserve", False):
                agent.db.reserve = True
                excess -= 1

    def handle_promotion(self, player: Any, new_agent_cap: int) -> None:
        """Restore reserved agents up to *new_agent_cap* (lowest IDs first)."""
        agents = self.get_agents(player)
        # Sort by agent_id ascending so lowest IDs are restored first
        agents.sort(key=lambda a: getattr(a.db, "agent_id", 0))

        total = 1 + len(agents)
        reserved = [a for a in agents if getattr(a.db, "reserve", False)]

        # How many slots are available?
        non_reserved = total - len(reserved)
        slots_available = new_agent_cap - non_reserved

        for agent in reserved:
            if slots_available <= 0:
                break
            agent.db.reserve = False
            slots_available -= 1

    # ------------------------------------------------------------------ #
    #  Training timer processing
    # ------------------------------------------------------------------ #

    # How often to send training progress updates (in ticks/seconds)
    TRAINING_PROGRESS_INTERVAL = 5

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
            if remaining % self.TRAINING_PROGRESS_INTERVAL == 0:
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
        """Process all agent-related per-tick work."""
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
        """Attach the Evennia Script for *role* to the agent NPC.

        Uses ``ROLE_SCRIPT_MAP`` from ``agent_scripts`` to look up the
        correct Script class, then adds it via Evennia's ``scripts.add``.
        Silently no-ops in test environments where Evennia isn't available.
        """
        try:
            from typeclasses.agent_scripts import ROLE_SCRIPT_MAP

            script_cls = ROLE_SCRIPT_MAP.get(role)
            if script_cls is None:
                return

            # Evennia's scripts.add accepts a typeclass path or class
            if hasattr(agent, "scripts"):
                agent.scripts.add(script_cls)
        except Exception:
            pass

    @staticmethod
    def _detach_behavior_script(agent: Any) -> None:
        """Remove any agent behavior script from the NPC.

        Searches for scripts whose key ends with ``_script`` (the
        naming convention from ``agent_scripts.py``) and deletes them.
        """
        try:
            from typeclasses.agent_scripts import ROLE_SCRIPT_MAP

            if not hasattr(agent, "scripts"):
                return

            script_keys = {f"{role}_script" for role in ROLE_SCRIPT_MAP}
            for script in agent.scripts.all():
                if getattr(script, "key", "") in script_keys:
                    script.delete()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

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
