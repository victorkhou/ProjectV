"""
Agent management commands — train, assign, unassign, list, patrol, stop agents.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 3.1, 3.6, 3.7, 3.8,
              10.3, 11.1
"""

from __future__ import annotations

from commands.game_commands import GameCommand
from world.utils import get_system as _get_system
from world.systems.agent_system import BUILDING_ROLE_MAP


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _get_current_building(caller):
    """Return the building object the caller is inside, or None."""
    if not getattr(caller.db, "inside_building", False):
        return None
    planet_room = getattr(caller, "location", None)
    if planet_room is None or not hasattr(planet_room, "get_buildings_at"):
        return None
    x = getattr(caller.db, "coord_x", None)
    y = getattr(caller.db, "coord_y", None)
    if x is None or y is None:
        return None
    buildings = planet_room.get_buildings_at(int(x), int(y))
    return buildings[0] if buildings else None


# ------------------------------------------------------------------ #
#  CmdAgents  (Req 13.1)
# ------------------------------------------------------------------ #

class CmdAgents(GameCommand):
    """List all your agents.

    Usage:
        agents
        list agents
    """

    key = "agents"
    aliases = ["list agents"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        agents = agent_system.get_agents(caller)

        lines = ["|wYour Agents:|n"]

        if agents:
            for agent in sorted(agents, key=lambda a: getattr(a.db, "agent_id", 0)):
                aid = getattr(agent.db, "agent_id", "?")
                role = getattr(agent.db, "role", "") or "unassigned"
                incap = getattr(agent.db, "incapacitated", False)
                reserve = getattr(agent.db, "reserve", False)

                if incap:
                    status = "|rIncapacitated|n"
                elif reserve:
                    status = "|yReserved|n"
                else:
                    status = "|gActive|n"

                target = getattr(agent.db, "role_target", None)
                if target is not None:
                    btype = getattr(target.db, "building_type", None) if hasattr(target, "db") else None
                    loc_str = btype or "building"
                elif role in ("soldier", "medic"):
                    loc_str = "army"
                else:
                    loc_str = "HQ"

                # Include activity_status (Req 10.3)
                activity = getattr(agent.db, "activity_status", None) or "Idle"
                lines.append(
                    f"  |c#{aid}|n  {role:<12s}  {loc_str:<10s}  "
                    f"{status} — {activity}"
                )

        # Show agents currently in training
        try:
            buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
            for b in buildings:
                tid = None
                if hasattr(b, "attributes"):
                    tid = b.attributes.get("training_agent_id")
                if tid is not None:
                    remaining = 0
                    if hasattr(b, "attributes"):
                        remaining = b.attributes.get("training_ticks_remaining") or 0
                    lines.append(f"  |c#{tid}|n  |ytraining     Academy     {remaining}s remaining|n")
        except Exception:
            pass

        if len(lines) == 1:
            lines.append("  No agents. Use |wtrain|n at an Academy.")

        caller.msg("\n".join(lines))


# ------------------------------------------------------------------ #
#  CmdAssign  (Req 13.2, 13.3, 13.6, 13.7)
# ------------------------------------------------------------------ #

class CmdAssign(GameCommand):
    """Assign an agent to a role.

    Usage:
        assign <id>           — inside a building, infers role
        assign <id> <role>    — army roles (soldier, medic)

    Building role mapping:
        Extractor → Harvester, Turret → Guard, Radar → Scout,
        Armory → Engineer, Lab → Engineer, Medbay → Medic
    """

    key = "assign"
    help_category = "Game"

    def func(self):
        caller = self.caller
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        args = self.args.strip().split()
        if not args:
            caller.msg("Usage: assign <id> [role]")
            return

        # Parse agent ID
        try:
            agent_id = int(args[0])
        except ValueError:
            caller.msg("Agent ID must be a number.")
            return

        role = None
        target_building = None

        if len(args) >= 2:
            # Explicit role provided — army roles
            role = args[1].lower()
        else:
            # Context-aware: infer role from current building
            building = _get_current_building(caller)
            if building is None:
                caller.msg(
                    "You must be inside a building to auto-assign, "
                    "or specify a role: assign <id> <role>"
                )
                return

            btype = getattr(building.db, "building_type", "") if hasattr(building, "db") else ""
            role = BUILDING_ROLE_MAP.get(btype)
            if role is None:
                caller.msg(f"Cannot assign agents to this building type ({btype}).")
                return

            # Check if building already has an assigned agent (Req 13.7)
            existing = getattr(building.db, "assigned_agent", None) if hasattr(building, "db") else None
            if existing is not None:
                caller.msg("This building already has an agent assigned.")
                return

            target_building = building

        success, msg = agent_system.assign_agent(
            caller, agent_id, role, target_building=target_building
        )
        caller.msg(msg)

        # Refresh the graphical map so the agent is visible immediately
        if success:
            from commands.game_commands import _send_map_update
            _send_map_update(caller)


# ------------------------------------------------------------------ #
#  CmdUnassign  (Req 13.4)
# ------------------------------------------------------------------ #

class CmdUnassign(GameCommand):
    """Unassign an agent from their current role.

    Usage:
        unassign <id>
    """

    key = "unassign"
    help_category = "Game"

    def func(self):
        caller = self.caller
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        args = self.args.strip()
        if not args:
            caller.msg("Usage: unassign <id>")
            return

        try:
            agent_id = int(args)
        except ValueError:
            caller.msg("Agent ID must be a number.")
            return

        success, msg = agent_system.unassign_agent(caller, agent_id)
        caller.msg(msg)
        if success:
            from commands.game_commands import _send_map_update
            _send_map_update(caller)


# ------------------------------------------------------------------ #
#  CmdTrain  (Req 13.5)
# ------------------------------------------------------------------ #

class CmdTrain(GameCommand):
    """Train a new agent at the Academy you are inside.

    Usage:
        train
    """

    key = "train"
    help_category = "Game"

    def func(self):
        caller = self.caller
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        building = _get_current_building(caller)
        if building is None:
            caller.msg("You must be inside an Academy to train agents.")
            return

        btype = getattr(building.db, "building_type", "") if hasattr(building, "db") else ""
        if btype != "AC":
            caller.msg("You must be inside an Academy to train agents.")
            return

        # Check if academy is already training
        training_id = getattr(building.db, "training_agent_id", None) if hasattr(building, "db") else None
        if training_id is not None:
            caller.msg("This Academy is already training an agent.")
            return

        success, msg = agent_system.train_agent(caller, building)
        caller.msg(msg)


# ------------------------------------------------------------------ #
#  CmdPatrol  (Req 3.1, 3.6, 3.7, 3.8)
# ------------------------------------------------------------------ #

class CmdPatrol(GameCommand):
    """Set or clear a patrol route for a guard or scout agent.

    Usage:
        patrol <agent_id> <x1>,<y1> <x2>,<y2> [<x3>,<y3> ...]
        patrol <agent_id> clear

    Sets a patrol route for a guard or scout agent. The agent will cycle
    through the waypoints continuously. Use "clear" to stop patrolling.

    Examples:
        patrol 3 50,50 55,50 55,55 50,55
        patrol 3 clear
    """

    key = "patrol"
    help_category = "Game"

    def func(self):
        caller = self.caller
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        args = self.args.strip().split()
        if len(args) < 2:
            caller.msg(
                "Usage: patrol <agent_id> <x1>,<y1> <x2>,<y2> ...\n"
                "       patrol <agent_id> clear"
            )
            return

        # Parse agent ID
        try:
            agent_id = int(args[0])
        except ValueError:
            caller.msg("Agent ID must be a number.")
            return

        # Check for "clear" subcommand
        if args[1].lower() == "clear":
            success, msg = agent_system.clear_patrol_route(caller, agent_id)
            caller.msg(msg)
            return

        # Parse waypoints: each arg should be "x,y"
        waypoints = []
        for token in args[1:]:
            parts = token.split(",")
            if len(parts) != 2:
                caller.msg(
                    f"Invalid waypoint '{token}'. "
                    "Use format: x,y (e.g. 50,50)"
                )
                return
            try:
                wx, wy = int(parts[0]), int(parts[1])
            except ValueError:
                caller.msg(
                    f"Invalid waypoint '{token}'. "
                    "Coordinates must be integers."
                )
                return
            waypoints.append((wx, wy))

        success, msg = agent_system.set_patrol_route(caller, agent_id, waypoints)
        caller.msg(msg)


# ------------------------------------------------------------------ #
#  CmdStopAgent  (Req 11.1)
# ------------------------------------------------------------------ #

class CmdStopAgent(GameCommand):
    """Cancel an agent's current movement and set it to idle.

    Usage:
        stopagent <agent_id>

    Cancels the agent's current movement and sets it to idle.
    """

    key = "stopagent"
    help_category = "Game"

    def func(self):
        caller = self.caller
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        args = self.args.strip()
        if not args:
            caller.msg("Usage: stopagent <agent_id>")
            return

        try:
            agent_id = int(args)
        except ValueError:
            caller.msg("Agent ID must be a number.")
            return

        success, msg = agent_system.stop_agent(caller, agent_id)
        caller.msg(msg)
