"""
Agent management commands — train, assign, unassign, list agents.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7
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
    tile_resolver = _get_system(caller, "tile_resolver")
    if tile_resolver is None:
        return None
    x = getattr(caller.db, "coord_x", None)
    y = getattr(caller.db, "coord_y", None)
    planet = getattr(caller.db, "coord_planet", None)
    if x is None or y is None or not planet:
        return None
    try:
        tile = tile_resolver.get_if_exists(x, y, planet)
    except (ValueError, KeyError):
        return None
    if tile is None:
        return None
    return getattr(tile, "building", None)


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
        lines.append(f"  |c#1|n  Commander (you)  |gActive|n")

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

                lines.append(f"  |c#{aid}|n  {role:<12s}  {loc_str:<10s}  {status}")

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

        if agent_id == 1:
            caller.msg("You cannot assign the commander (yourself).")
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

        if agent_id == 1:
            caller.msg("You cannot unassign the commander (yourself).")
            return

        success, msg = agent_system.unassign_agent(caller, agent_id)
        caller.msg(msg)


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
