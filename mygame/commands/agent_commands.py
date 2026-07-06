"""
Agent management commands — train, assign, unassign, list, patrol, stop agents.

"""

from __future__ import annotations

from commands.command_router import GameSubcommandRouter
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
#  CmdAgent  — Game subcommand router
# ------------------------------------------------------------------ #

class CmdAgent(GameSubcommandRouter):
    """Manage your agents.

    Usage:
        agent <subcommand> [args]

    Subcommands:
        list                              — list your agents
        assign <id> [role]                — assign an agent to a role
        unassign <id>                     — unassign an agent
        train                             — train a new agent at an Academy
        patrol <id> <x,y> [<x,y> ...]     — set a patrol route
        patrol <id> clear                 — clear a patrol route
        stop <id>                         — stop an agent's current action
        ability <id> [<key> on|off]       — view or toggle a gated ability
    """

    key = "agent"
    help_category = "Game"

    def sub_list(self, args):
        """List all of the caller's agents."""
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
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

                activity = getattr(agent.db, "activity_status", None) or "Idle"

                # Progression segment. Defensive: a missing or
                # raising get_agent_progression_view must never break the roster.
                prog_segment = ""
                try:
                    view = agent_system.get_agent_progression_view(agent)
                    eff = view.get("effective_level")
                    rank_name = view.get("rank_name", "") or ""
                    ability_status = view.get("ability_status") or {}
                    capped = view.get("capped_by_commander", False)

                    # Per-ability status: decode each gate's wire status via the
                    # AgentSystem helper (the inverse of its encoder) rather than
                    # hand-parsing the string here. Show "no abilities" when the
                    # agent qualifies for none (every gate locked, or no gates at
                    # all); otherwise list each gate and its state.
                    from world.systems.agent_system import AgentSystem
                    ability_parts = []
                    qualifies = False
                    for key, state in ability_status.items():
                        decoded_state, readable = AgentSystem.decode_ability_status(state)
                        if decoded_state != "locked":
                            qualifies = True
                        ability_parts.append(f"{key}: {readable}")

                    if not ability_status or not qualifies:
                        ability_text = "no abilities"
                    else:
                        ability_text = ", ".join(ability_parts)

                    prog_segment = f"  Lv {eff} {rank_name}  [{ability_text}]"
                    if capped:
                        prog_segment += "  |y[capped]|n"
                except Exception:
                    prog_segment = ""

                lines.append(
                    f"  |c#{aid}|n  {role:<12s}  {loc_str:<10s}  "
                    f"{status} — {activity}{prog_segment}"
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

    def sub_assign(self, args):
        """Assign an agent to a role."""
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        parts = args.strip().split()
        if not parts:
            caller.msg("Usage: agent assign <id> [role]")
            return

        agent_id = self.parse_int(parts[0])
        if agent_id is None:
            return

        role = None
        target_building = None

        if len(parts) >= 2:
            role = parts[1].lower()
        else:
            building = _get_current_building(caller)
            if building is None:
                caller.msg(
                    "You must be inside a building to auto-assign, "
                    "or specify a role: agent assign <id> <role>"
                )
                return

            btype = getattr(building.db, "building_type", "") if hasattr(building, "db") else ""
            role = BUILDING_ROLE_MAP.get(btype)
            if role is None:
                caller.msg(f"Cannot assign agents to this building type ({btype}).")
                return

            existing = getattr(building.db, "assigned_agent", None) if hasattr(building, "db") else None
            if existing is not None:
                caller.msg("This building already has an agent assigned.")
                return

            target_building = building

        success, msg = agent_system.assign_agent(
            caller, agent_id, role, target_building=target_building
        )
        caller.msg(msg)

        if success:
            from commands.game_commands import _send_map_update
            _send_map_update(caller)

    def sub_unassign(self, args):
        """Unassign an agent from their current role."""
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        args = args.strip()
        if not args:
            caller.msg("Usage: agent unassign <id>")
            return

        agent_id = self.parse_int(args)
        if agent_id is None:
            return

        success, msg = agent_system.unassign_agent(caller, agent_id)
        caller.msg(msg)
        if success:
            from commands.game_commands import _send_map_update
            _send_map_update(caller)

    def sub_train(self, args):
        """Train a new agent at the Academy the caller is inside."""
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        building = _get_current_building(caller)
        if building is None:
            caller.msg("You must be inside an Academy to train agents.")
            return

        btype = getattr(building.db, "building_type", "") if hasattr(building, "db") else ""
        if btype != "AC":
            caller.msg("You must be inside an Academy to train agents.")
            return

        training_id = getattr(building.db, "training_agent_id", None) if hasattr(building, "db") else None
        if training_id is not None:
            caller.msg("This Academy is already training an agent.")
            return

        success, msg = agent_system.train_agent(caller, building)
        caller.msg(msg)

    def sub_patrol(self, args):
        """Set or clear a patrol route for a guard or scout agent."""
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        parts = args.strip().split()
        if len(parts) < 1:
            caller.msg(
                "Usage: agent patrol <agent_id> <x1>,<y1> <x2>,<y2> ...\n"
                "       agent patrol <agent_id> clear"
            )
            return

        agent_id = self.parse_int(parts[0])
        if agent_id is None:
            return

        if len(parts) < 2:
            caller.msg(
                "Usage: agent patrol <agent_id> <x1>,<y1> <x2>,<y2> ...\n"
                "       agent patrol <agent_id> clear"
            )
            return

        if parts[1].lower() == "clear":
            success, msg = agent_system.clear_patrol_route(caller, agent_id)
            caller.msg(msg)
            return

        waypoints = []
        for token in parts[1:]:
            coords = token.split(",")
            if len(coords) != 2:
                caller.msg(
                    f"Invalid waypoint '{token}'. "
                    "Use format: x,y (e.g. 50,50)"
                )
                return
            try:
                wx, wy = int(coords[0]), int(coords[1])
            except ValueError:
                caller.msg(
                    f"Invalid waypoint '{token}'. "
                    "Coordinates must be integers."
                )
                return
            waypoints.append((wx, wy))

        success, msg = agent_system.set_patrol_route(caller, agent_id, waypoints)
        caller.msg(msg)

    def sub_stop(self, args):
        """Cancel an agent's current movement and set it to idle."""
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        args = args.strip()
        if not args:
            caller.msg("Usage: agent stop <agent_id>")
            return

        agent_id = self.parse_int(args)
        if agent_id is None:
            return

        success, msg = agent_system.stop_agent(caller, agent_id)
        caller.msg(msg)

    def sub_ability(self, args):
        """Enable/disable or view a gated ability for an agent.

        Usage:
            agent ability <id> <key> on|off   — enable/disable a gated ability
            agent ability <id>                 — show per-ability status

        All rules live in AgentSystem; this handler only parses and delegates.
        """
        caller = self.caller
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        parts = args.strip().split()
        if not parts:
            caller.msg("Usage: agent ability <id> [<key> on|off]")
            return

        agent_id = self.parse_int(parts[0])
        if agent_id is None:
            return

        if len(parts) == 1:                         # status form
            caller.msg(agent_system.get_ability_status(caller, agent_id))
            return

        if len(parts) == 3 and parts[2].lower() in ("on", "off"):
            key, toggle = parts[1], parts[2].lower()
            if toggle == "on":
                caller.msg(agent_system.enable_ability(caller, agent_id, key))
            else:
                caller.msg(agent_system.disable_ability(caller, agent_id, key))
            return

        caller.msg("Usage: agent ability <id> [<key> on|off]")

    subcommands = {
        "list": (sub_list, "List your agents", ""),
        "assign": (sub_assign, "Assign an agent to a role", ""),
        "unassign": (sub_unassign, "Unassign an agent", ""),
        "train": (sub_train, "Train a new agent at an Academy", ""),
        "patrol": (sub_patrol, "Set or clear a patrol route", ""),
        "stop": (sub_stop, "Stop an agent's current action", ""),
        "ability": (sub_ability, "Enable/disable or view a gated ability", ""),
    }
