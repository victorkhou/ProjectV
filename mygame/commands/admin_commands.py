"""
Admin commands for the RTS Combat Overworld.

Restricted to Builder+ permission level. All executions are logged
with operator name, command, and target.

Requirements: 26.1, 33.1, 33.2, 33.3, 33.4, 33.5
"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand
from commands.command_router import AdminSubcommandRouter
from world.utils import get_system as _get_system

logger = logging.getLogger("mygame.admin")


class CmdReloadData(BaseCommand):
    """Hot-reload all YAML definition files.

    Usage:
        @reloaddata

    Restricted to Builder+ permission level.
    """

    key = "@reloaddata"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        # Permission check
        if not _check_perm(caller, "Builder"):
            caller.msg("Permission denied. Builder+ required.")
            return

        logger.info(
            "Admin command @reloaddata executed by %s",
            getattr(caller, "key", "?"),
        )

        registry = _get_registry(caller)
        if registry is None:
            caller.msg("Data Registry unavailable.")
            return

        success, errors = registry.reload_all()
        if success:
            caller.msg("|gData reload successful.|n")
            logger.info("Data reload successful (operator: %s)", caller.key)
        else:
            error_text = "\n".join(errors)
            caller.msg(f"|rData reload failed:|n\n{error_text}")
            logger.warning(
                "Data reload failed (operator: %s): %s",
                caller.key, "; ".join(errors),
            )


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _check_perm(caller, perm_name):
    """Check if the caller has the required permission.

    Tries Evennia's perm() method first, then falls back to a
    simple attribute check for testing.
    """
    if hasattr(caller, "check_permstring"):
        return caller.check_permstring(perm_name)
    if hasattr(caller, "permissions"):
        perms = caller.permissions
        if hasattr(perms, "check"):
            return perms.check(perm_name)
        if isinstance(perms, (list, tuple, set)):
            # Simple hierarchy check
            hierarchy = ["Player", "Helper", "Builder", "Admin", "Developer"]
            caller_level = -1
            required_level = -1
            for i, p in enumerate(hierarchy):
                if p in perms:
                    caller_level = max(caller_level, i)
                if p == perm_name:
                    required_level = i
            return caller_level >= required_level
    # Fallback for testing: check _permissions attribute
    if hasattr(caller, "_permissions"):
        return perm_name in caller._permissions
    return False


def _get_registry(caller):
    """Look up the DataRegistry."""
    systems = getattr(getattr(caller, "ndb", None), "systems", None)
    if systems and isinstance(systems, dict):
        return systems.get("registry")
    try:
        from server.conf.game_init import game_systems
        return game_systems.get("registry")
    except (ImportError, AttributeError):
        return None


class CmdAdminBuilding(AdminSubcommandRouter):
    """Manage buildings on the overworld.

    Usage:
        @building spawn <type> [owner=<name>] [level=<N>]
        @building destroy

    Subcommands:
        spawn   — Spawn a building at your current tile (Builder+)
        destroy — Destroy the building at your current tile (Builder+)

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
    """

    key = "@building"

    def sub_spawn(self, args):
        """Spawn a building at the caller's current tile.

        Args:
            args: "<type> [owner=<name>] [level=<N>]"
        """
        caller = self.caller
        if not args:
            caller.msg("Usage: @building spawn <type> [owner=<name>] [level=<N>]")
            return

        parts = args.split()
        btype = parts[0].upper()

        # Parse optional kwargs
        owner = caller
        level = 1
        for part in parts[1:]:
            if part.lower().startswith("owner="):
                owner_name = part.split("=", 1)[1]
                if owner_name.lower() in ("none", "nobody", "null", ""):
                    owner = None
                else:
                    found = caller.search(owner_name, quiet=True) if hasattr(caller, "search") else None
                    if not found:
                        caller.msg(f"Could not find player '{owner_name}'.")
                        return
                    owner = found[0] if isinstance(found, list) else found
            elif part.lower().startswith("level="):
                try:
                    level = int(part.split("=", 1)[1])
                    level = max(1, min(level, 5))
                except ValueError:
                    caller.msg("Level must be a number 1-5.")
                    return

        # Validate building type exists
        registry = _get_registry(caller)
        if registry:
            try:
                bdef = registry.get_building(btype)
            except KeyError:
                caller.msg(
                    f"Unknown building type '{btype}'. "
                    "Valid: HQ, MM, QQ, II, LL, KK, AA, AR, VV, TL, HV"
                )
                return
        else:
            bdef = None

        # Get current location — must be a PlanetRoom
        planet_room = caller.location
        if planet_room is None:
            caller.msg("You have no location.")
            return

        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)

        if cx is None or cy is None:
            caller.msg("You have no coordinates set.")
            return

        # Create the building in PlanetRoom at caller's coordinates
        try:
            from evennia.utils.create import create_object

            hp = bdef.max_health if bdef else 500
            name = bdef.name if bdef else btype

            building = create_object(
                typeclass="typeclasses.objects.Building",
                key=name,
                location=planet_room,
            )
            building.attributes.add("building_type", btype)
            building.attributes.add("owner", owner)
            building.attributes.add("building_level", level)
            building.attributes.add("hp", hp)
            building.attributes.add("hp_max", hp)
            building.attributes.add("offline", False)
            # Set coordinates on the building
            building.db.coord_x = cx
            building.db.coord_y = cy
            # at_object_receive saw coord_x=None during create_object,
            # so manually register in the coordinate index now.
            if hasattr(planet_room, "coord_index"):
                planet_room.coord_index.add(building, int(cx), int(cy))

            owner_name = getattr(owner, "key", "nobody") if owner else "nobody"
            self._log_admin(
                "spawn",
                f"{btype} level {level} owner={owner_name} at ({cx}, {cy}) in "
                f"{planet_room.key if hasattr(planet_room, 'key') else planet_room}",
            )
            caller.msg(
                f"Spawned {name} ({btype}) level {level}, "
                f"owned by {owner_name} at ({cx}, {cy})."
            )
        except Exception as e:
            caller.msg(f"Failed to create building: {e}")

    def sub_destroy(self, args):
        """Destroy the building at the caller's current tile.

        Finds the first building at the caller's coordinates and deletes
        it without refunding resources (admin override).
        """
        caller = self.caller

        planet_room = caller.location
        if planet_room is None:
            caller.msg("You have no location.")
            return

        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)

        if cx is None or cy is None:
            caller.msg("You have no coordinates set.")
            return

        if not hasattr(planet_room, "get_objects_at"):
            caller.msg("Current location does not support coordinate queries.")
            return

        buildings = planet_room.get_objects_at(int(cx), int(cy), type_tag="building")
        if not buildings:
            caller.msg(f"No building found at ({cx}, {cy}).")
            return

        building = buildings[0]
        btype = building.attributes.get("building_type", default="??") if hasattr(building, "attributes") else "??"
        bname = getattr(building, "key", btype)

        building.delete()

        self._log_admin("destroy", f"{bname} ({btype}) at ({cx}, {cy})")
        caller.msg(f"Destroyed {bname} ({btype}) at ({cx}, {cy}).")

    subcommands = {
        "spawn": (sub_spawn, "Spawn a building at your tile", "Builder"),
        "destroy": (sub_destroy, "Destroy building at your tile", "Builder"),
    }


class CmdAdminAgent(AdminSubcommandRouter):
    """Manage agent NPCs for players.

    Usage:
        @agent create <player> [count]
        @agent destroy <id> <player>
        @agent destroy training <player>
        @agent list <player>

    Subcommands:
        create  — Instantly create agent(s) bypassing cost/timer (Admin+)
        destroy — Destroy an agent by ID or clear training state (Admin+)
        list    — List all agents for a player (Builder+)

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
    """

    key = "@agent"

    def sub_create(self, args):
        """Create agent(s) for a player, bypassing cost and timer.

        Args:
            args: "<player> [count]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @agent create <player> [count]")
            return

        parts = args.strip().split()
        player_name = parts[0]
        count = 1
        if len(parts) >= 2:
            try:
                count = int(parts[1])
            except ValueError:
                caller.msg("Count must be a number.")
                return
            if count < 1:
                caller.msg("Count must be at least 1.")
                return

        target = caller.search(player_name) if hasattr(caller, "search") else None
        if target is None:
            caller.msg(f"Could not find player '{player_name}'.")
            return

        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        created_ids = []
        for _ in range(count):
            agents = agent_system.get_agents(target)
            if agents:
                max_id = max(getattr(a.db, "agent_id", 0) for a in agents)
                next_id = max_id + 1
            else:
                next_id = 1

            npc = agent_system._create_npc_func(target, next_id)
            if npc is not None:
                created_ids.append(next_id)
                target.db.next_agent_id = next_id + 1

        if created_ids:
            ids_str = ", ".join(f"#{i}" for i in created_ids)
            self._log_admin("create", f"{len(created_ids)} agent(s) for {target.key}: {ids_str}")
            caller.msg(f"Created {len(created_ids)} agent(s) for {target.key}: {ids_str}.")
            if hasattr(target, "msg") and target is not caller:
                target.msg(f"|y[Admin] {len(created_ids)} agent(s) created for you: {ids_str}.|n")
        else:
            caller.msg("Failed to create agents.")

    def sub_destroy(self, args):
        """Destroy an agent by ID or clear training state.

        Args:
            args: "<id> <player>" or "training <player>"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @agent destroy <id> <player> | @agent destroy training <player>")
            return

        parts = args.strip().split()
        if len(parts) < 2:
            caller.msg("Usage: @agent destroy <id> <player> | @agent destroy training <player>")
            return

        first_arg = parts[0]
        player_name = parts[1]

        # Find the target player
        target = caller.search(player_name) if hasattr(caller, "search") else None
        if target is None:
            caller.msg(f"Could not find player '{player_name}'.")
            return

        if first_arg.lower() == "training":
            self._clear_training(caller, target)
            return

        try:
            agent_id = int(first_arg)
        except ValueError:
            caller.msg("Agent ID must be a number or 'training'.")
            return

        self._destroy_agent(caller, target, agent_id)

    def _clear_training(self, caller, target):
        """Clear all stuck training state for a player."""
        cleared = 0
        try:
            from evennia.objects.models import ObjectDB

            buildings = list(ObjectDB.objects.filter(
                db_attributes__db_key="training_owner",
            ))
            for b in buildings:
                owner = b.attributes.get("training_owner")
                if owner is target:
                    b.attributes.add("training_agent_id", None)
                    b.attributes.add("training_ticks_remaining", None)
                    b.attributes.add("training_owner", None)
                    cleared += 1
        except Exception:
            pass

        # Also try via player's buildings
        try:
            for b in target.get_buildings():
                agent_id = None
                if hasattr(b, "attributes"):
                    agent_id = b.attributes.get("training_agent_id")
                elif hasattr(b, "db"):
                    agent_id = getattr(b.db, "training_agent_id", None)
                if agent_id is not None:
                    if hasattr(b, "attributes"):
                        b.attributes.add("training_agent_id", None)
                        b.attributes.add("training_ticks_remaining", None)
                        b.attributes.add("training_owner", None)
                    cleared += 1
        except Exception:
            pass

        self._log_admin("destroy", f"cleared training state on {cleared} building(s) for {target.key}")
        caller.msg(f"Cleared training state on {cleared} building(s) for {target.key}.")

    def _destroy_agent(self, caller, target, agent_id):
        """Destroy a specific agent NPC, or clear its stuck training state."""
        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        agent = agent_system.get_agent_by_id(target, agent_id)
        if agent is not None:
            # Clear building assignment if any
            building = getattr(agent.db, "role_target", None) if hasattr(agent, "db") else None
            if building is not None and hasattr(building, "db"):
                if getattr(building.db, "assigned_agent", None) is agent:
                    building.db.assigned_agent = None

            agent_name = getattr(agent, "key", f"Agent-{agent_id}")
            if hasattr(agent, "delete"):
                agent.delete()

            self._log_admin("destroy", f"agent #{agent_id} ({agent_name}) belonging to {target.key}")
            caller.msg(f"Destroyed agent #{agent_id} ({agent_name}) belonging to {target.key}.")
            return

        # Agent NPC doesn't exist — check if it's stuck in training
        cleared = False
        try:
            for b in target.get_buildings():
                tid = None
                if hasattr(b, "attributes"):
                    tid = b.attributes.get("training_agent_id")
                elif hasattr(b, "db"):
                    tid = getattr(b.db, "training_agent_id", None)
                if tid == agent_id:
                    if hasattr(b, "attributes"):
                        b.attributes.add("training_agent_id", None)
                        b.attributes.add("training_ticks_remaining", None)
                        b.attributes.add("training_owner", None)
                    elif hasattr(b, "db"):
                        b.db.training_agent_id = None
                        b.db.training_ticks_remaining = None
                        b.db.training_owner = None
                    cleared = True
                    self._log_admin("destroy", f"cleared stuck training #{agent_id} for {target.key}")
                    caller.msg(f"Cleared stuck training for agent #{agent_id} on {target.key}'s Academy.")
                    break
        except Exception:
            pass

        if not cleared:
            caller.msg(f"Agent #{agent_id} not found for {target.key} (not spawned, not in training).")

    def sub_list(self, args):
        """List all agents belonging to a player.

        Args:
            args: "<player>"
        """
        caller = self.caller

        player_name = args.strip() if args else ""
        if not player_name:
            caller.msg("Usage: @agent list <player>")
            return

        target = caller.search(player_name) if hasattr(caller, "search") else None
        if target is None:
            caller.msg(f"Could not find player '{player_name}'.")
            return

        agent_system = _get_system(caller, "agent_system")
        if agent_system is None:
            caller.msg("Agent system unavailable.")
            return

        agents = agent_system.get_agents(target)
        next_id = getattr(getattr(target, "db", None), "next_agent_id", None)
        count = agent_system.get_agent_count(target)

        lines = [f"|w=== Agents for {target.key} ({count} agents, next_id={next_id}) ===|n"]

        if not agents:
            lines.append("  No trained agents.")
        else:
            for agent in sorted(agents, key=lambda a: getattr(a.db, "agent_id", 0)):
                aid = getattr(agent.db, "agent_id", "?")
                role = getattr(agent.db, "role", "") or "unassigned"
                incap = getattr(agent.db, "incapacitated", False)
                reserve = getattr(agent.db, "reserve", False)
                obj_id = getattr(agent, "id", "?")

                status_parts = []
                if incap:
                    status_parts.append("|rIncapacitated|n")
                if reserve:
                    status_parts.append("|yReserved|n")
                if not status_parts:
                    status_parts.append("|gActive|n")
                status = " ".join(status_parts)

                target_bld = getattr(agent.db, "role_target", None)
                loc_str = "HQ"
                if target_bld is not None:
                    btype = getattr(target_bld.db, "building_type", "?") if hasattr(target_bld, "db") else "?"
                    loc_str = btype
                elif role in ("soldier", "medic"):
                    loc_str = "army"

                lines.append(
                    f"  |c#{aid}|n (db#{obj_id})  {role:<12s}  "
                    f"{loc_str:<10s}  {status}"
                )

        # Show training state on buildings
        try:
            for b in target.get_buildings():
                tid = None
                if hasattr(b, "attributes"):
                    tid = b.attributes.get("training_agent_id")
                if tid is not None:
                    remaining = b.attributes.get("training_ticks_remaining") or 0
                    btype = b.attributes.get("building_type") or "??"
                    lines.append(f"  |y[Training] #{tid} at {btype} — {remaining}s remaining|n")
        except Exception:
            pass

        self._log_admin("list", f"agents for {target.key}")
        caller.msg("\n".join(lines))

    subcommands = {
        "create": (sub_create, "Create agent(s) for a player", "Admin"),
        "destroy": (sub_destroy, "Destroy agent or clear training", "Admin"),
        "list": (sub_list, "List agents for a player", "Builder"),
    }


class CmdAdminResource(AdminSubcommandRouter):
    """Manage player resources.

    Usage:
        @resource give <type> <amount> [player]
        @resource reset [player]

    Subcommands:
        give  — Give resources to a player (Builder+)
        reset — Reset player(s) to starting resources (Admin+)

    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
    """

    key = "@resource"

    def sub_give(self, args):
        """Give resources to a player.

        Args:
            args: "<type> <amount> [player]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @resource give <type> <amount> [player]")
            return

        parts = args.strip().split()
        if len(parts) < 2:
            caller.msg("Usage: @resource give <type> <amount> [player]")
            return

        resource_type = parts[0]
        amount_str = parts[1]
        player_name = parts[2] if len(parts) >= 3 else None

        try:
            amount = int(amount_str)
        except ValueError:
            caller.msg(f"Invalid amount: {amount_str}")
            return

        if amount <= 0:
            caller.msg("Amount must be positive.")
            return

        # Resolve target: specified player or self
        if player_name:
            target = caller.search(player_name) if hasattr(caller, "search") else None
            if target is None:
                caller.msg(f"Could not find player '{player_name}'.")
                return
        else:
            target = caller

        if not hasattr(target, "add_resource"):
            target_name = getattr(target, "key", "target")
            caller.msg(f"{target_name} is not a valid player character.")
            return

        target.add_resource(resource_type, amount)

        target_name = getattr(target, "key", "?")
        caller.msg(f"Gave {amount} {resource_type} to {target_name}.")

        self._log_admin("give", f"{amount} {resource_type} to {target_name}")

        # Notify the target if they have msg and are not the caller
        if hasattr(target, "msg") and target is not caller:
            target.msg(
                f"|y[Admin] You received {amount} {resource_type} "
                f"from {caller.key}.|n"
            )

    def sub_reset(self, args):
        """Reset player(s) to starting resources.

        Args:
            args: "[player]" — if specified, reset just that player;
                  if empty, reset all players.
        """
        caller = self.caller
        player_name = args.strip() if args else ""

        if player_name:
            # Reset a single player
            target = caller.search(player_name) if hasattr(caller, "search") else None
            if target is None:
                caller.msg(f"Could not find player '{player_name}'.")
                return

            try:
                from typeclasses.characters import STARTING_RESOURCES
            except ImportError:
                caller.msg("Could not load starting resource definitions.")
                return

            try:
                target.attributes.add("resources", dict(STARTING_RESOURCES))
            except Exception:
                caller.msg(f"Failed to reset resources for {target.key}.")
                return

            self._log_admin("reset", f"resources for {target.key}")
            caller.msg(f"Reset {target.key} to starting resources.")
        else:
            # Reset all players
            try:
                from typeclasses.characters import STARTING_RESOURCES
                from evennia.objects.models import ObjectDB

                characters = list(
                    ObjectDB.objects.filter(db_attributes__db_key="combat_xp")
                )
            except Exception:
                caller.msg("Could not query player characters from the database.")
                return

            if not characters:
                caller.msg("No player characters found in the database.")
                return

            updated = 0
            for char in characters:
                try:
                    char.attributes.add("resources", dict(STARTING_RESOURCES))
                    updated += 1
                except Exception:
                    logger.exception(
                        "Failed to reset resources for %s",
                        getattr(char, "key", "?"),
                    )

            self._log_admin("reset", f"resources for {updated} character(s)")
            caller.msg(f"Reset {updated} player(s) to starting resources.")

    subcommands = {
        "give": (sub_give, "Give resources to a player", "Builder"),
        "reset": (sub_reset, "Reset player(s) to starting resources", "Admin"),
    }


class CmdAdminPlayer(AdminSubcommandRouter):
    """Manage player level and rank.

    Usage:
        @player level <N> [player]
        @player rank <N> [player]

    Subcommands:
        level — Set a player's level (Admin+)
        rank  — Set a player's rank (Admin+)

    If [player] is omitted, targets the caller.

    Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
    """

    key = "@player"

    def sub_level(self, args):
        """Set a player's level by numeric value (1-MAX_LEVEL).

        Args:
            args: "<N> [player]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @player level <N> [player]")
            return

        parts = args.strip().split()
        try:
            level = int(parts[0])
        except ValueError:
            caller.msg("Level must be a number.")
            return

        player_name = parts[1] if len(parts) >= 2 else None

        from world.constants import MAX_LEVEL
        if level < 1 or level > MAX_LEVEL:
            caller.msg(f"Level must be between 1 and {MAX_LEVEL}.")
            return

        # Resolve target: specified player or self
        if player_name:
            target = caller.search(player_name) if hasattr(caller, "search") else None
            if target is None:
                caller.msg(f"Could not find player '{player_name}'.")
                return
        else:
            target = caller

        if not hasattr(target, "db"):
            target_name = getattr(target, "key", "target")
            caller.msg(f"{target_name} is not a valid player character.")
            return

        # Compute rank from level
        from world.systems.rank_system import rank_from_level
        rank_num = rank_from_level(level)

        # Set XP to the threshold for this level
        rank_system = _get_system(caller, "rank_system")
        if rank_system:
            xp = rank_system.xp_for_level(level)
            target.db.combat_xp = xp
        else:
            xp = None

        target.db.level = level
        target.db.rank_level = rank_num

        # Trigger rank events (unlock techs, adjust agent cap)
        if rank_system:
            rank_system.check_promotion(target)

        # Look up rank name
        rank_name = f"Rank {rank_num}"
        registry = _get_registry(caller)
        if registry and hasattr(registry, "ranks"):
            rank_def = next((r for r in registry.ranks if r.level == rank_num), None)
            if rank_def:
                rank_name = rank_def.name

        target_name = getattr(target, "key", "?")
        xp_str = f", XP={xp}" if xp is not None else ""
        self._log_admin("level", f"set {target_name} to level {level} ({rank_name}{xp_str})")
        caller.msg(f"Set {target_name} to level {level} ({rank_name}{xp_str}).")

    def sub_rank(self, args):
        """Set a player's rank by numeric rank ID (1-NUM_RANKS).

        Args:
            args: "<N> [player]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @player rank <N> [player]")
            return

        parts = args.strip().split()
        try:
            rank_id = int(parts[0])
        except ValueError:
            caller.msg("Rank ID must be a number.")
            return

        player_name = parts[1] if len(parts) >= 2 else None

        from world.constants import NUM_RANKS
        if rank_id < 1 or rank_id > NUM_RANKS:
            caller.msg(f"Rank ID must be between 1 and {NUM_RANKS}.")
            return

        # Resolve target: specified player or self
        if player_name:
            target = caller.search(player_name) if hasattr(caller, "search") else None
            if target is None:
                caller.msg(f"Could not find player '{player_name}'.")
                return
        else:
            target = caller

        if not hasattr(target, "db"):
            target_name = getattr(target, "key", "target")
            caller.msg(f"{target_name} is not a valid player character.")
            return

        # Convert rank to level: first level of that rank
        from world.systems.rank_system import level_range_for_rank
        level, _ = level_range_for_rank(rank_id)

        # Set XP to the threshold for this level
        rank_system = _get_system(caller, "rank_system")
        if rank_system:
            xp = rank_system.xp_for_level(level)
            target.db.combat_xp = xp
        else:
            xp = None

        target.db.level = level
        target.db.rank_level = rank_id

        # Trigger rank events (unlock techs, adjust agent cap)
        if rank_system:
            rank_system.check_promotion(target)

        # Look up rank name
        rank_name = f"Rank {rank_id}"
        registry = _get_registry(caller)
        if registry and hasattr(registry, "ranks"):
            rank_def = next((r for r in registry.ranks if r.level == rank_id), None)
            if rank_def:
                rank_name = rank_def.name

        target_name = getattr(target, "key", "?")
        xp_str = f", XP={xp}" if xp is not None else ""
        self._log_admin("rank", f"set {target_name} to {rank_name} (rank {rank_id}, level {level}{xp_str})")
        caller.msg(f"Set {target_name} to {rank_name} (rank {rank_id}, level {level}{xp_str}).")

    subcommands = {
        "level": (sub_level, "Set a player's level", "Admin"),
        "rank": (sub_rank, "Set a player's rank", "Admin"),
    }


class CmdTeleport(BaseCommand):
    """Teleport to coordinates on the overworld.

    Usage:
        teleport <x>,<y>,<planet>
        teleport <x>,<y>

    If planet is omitted, uses your current planet.
    Updates coordinates via PlanetRoom.move_entity.

    Examples:
        teleport 50,50,earth_planet
        teleport 25,25
    """

    key = "@teleport"
    aliases = ["@tel"]
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        args = self.args.strip()
        if not args:
            caller.msg("Usage: teleport <x>,<y>[,<planet|z_level>]")
            return

        parts = [p.strip() for p in args.split(",")]
        if len(parts) < 2:
            caller.msg("Usage: teleport <x>,<y>[,<planet|z_level>]")
            return

        try:
            tx = int(parts[0])
            ty = int(parts[1])
        except ValueError:
            caller.msg("Coordinates must be integers.")
            return

        # Get registry for planet resolution
        registry = _get_system(caller, "planet_registry")
        if registry is None:
            caller.msg("Planet registry not available.")
            return

        if len(parts) >= 3:
            planet = registry.resolve_planet(parts[2])
            if planet is None:
                caller.msg(f"Unknown planet '{parts[2]}'. Use a name, prefix, or z-level (0/1/2).")
                return
        else:
            planet = getattr(caller.db, "coord_planet", None)
            if not planet:
                caller.msg("No planet specified and no current planet set.")
                return

        # Validate bounds
        if not registry.is_valid_coordinate(tx, ty, planet):
            caller.msg(f"Coordinates ({tx}, {ty}) are out of bounds for {planet}.")
            return

        # Get the shared planet room
        planet_rooms = None
        try:
            from server.conf.game_init import game_systems
            planet_rooms = game_systems.get("planet_rooms", {})
        except (ImportError, AttributeError):
            pass

        if not planet_rooms:
            caller.msg("Planet rooms not available.")
            return

        target_room = planet_rooms.get(planet)
        if not target_room:
            caller.msg(f"No PlanetRoom found for {planet}.")
            return

        # Update planet attribute
        caller.db.coord_planet = planet

        # Only move_to if changing planets (different PlanetRoom)
        if caller.location is not target_room:
            caller.move_to(target_room, quiet=True)

        # Use move_entity for coordinate update within the PlanetRoom
        target_room.move_entity(caller, tx, ty)

        logger.info("Admin %s teleported to (%d, %d, %s)", caller.key, tx, ty, planet)
        caller.msg(f"Teleported to ({tx}, {ty}) on {planet}.")


class CmdClearFog(BaseCommand):
    """Clear a player's fog of war discovery memory.

    Usage:
        @clearfog [player]

    If no player is specified, clears your own fog.
    Restricted to Builder+ permission level.
    """

    key = "@clearfog"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        target_name = self.args.strip()

        if target_name:
            target = caller.search(target_name, quiet=True) if hasattr(caller, "search") else None
            if not target:
                caller.msg(f"Could not find player '{target_name}'.")
                return
            target = target[0] if isinstance(target, list) else target
        else:
            target = caller

        if hasattr(target, "db"):
            target.db.discovery_memory = {"discovered": {}, "buildings": {}}

        name = getattr(target, "key", "?")
        logger.info("Admin %s cleared fog of war for %s", caller.key, name)
        caller.msg(f"Cleared fog of war for {name}.")


class CmdPurgeRooms(BaseCommand):
    """Delete all legacy OverworldRoom objects from the database.

    Removes all remaining OverworldRoom objects as migration cleanup.
    Run @migraterooms first to move buildings and resources to PlanetRoom.

    Usage:
        @purgerooms

    Restricted to Builder+ permission level.
    """

    key = "@purgerooms"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        try:
            from evennia.utils.search import search_tag

            all_rooms = list(search_tag("overworld_tile", category="room_type"))
        except Exception:
            caller.msg("Could not query overworld rooms.")
            return

        deleted = 0
        for room in all_rooms:
            room.delete()
            deleted += 1

        logger.info(
            "Admin %s purged %d legacy OverworldRoom objects",
            caller.key, deleted,
        )
        caller.msg(f"Purged {deleted} legacy OverworldRoom object(s).")


class CmdMigrate(BaseCommand):
    """Ensure all players have valid attributes.

    Usage:
        @migrate

    Reads PLAYER_DEFAULTS from characters.py and ensures every player
    has all attributes with valid (non-None) values. Only fills in
    missing attributes — never overwrites existing data.

    Run this after adding new player attributes to the codebase.

    Restricted to Admin+ permission level.
    """

    key = "@migrate"
    locks = "cmd:perm(Admin);view:perm(Admin)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        if not _check_perm(caller, "Admin"):
            caller.msg("Permission denied. Admin+ required.")
            return

        try:
            from typeclasses.characters import PLAYER_DEFAULTS
            from evennia.objects.models import ObjectDB

            characters = list(
                ObjectDB.objects.filter(db_attributes__db_key="combat_xp")
            )
        except Exception:
            caller.msg("Could not query player characters from the database.")
            return

        if not characters:
            caller.msg("No player characters found in the database.")
            return

        updated = 0
        attrs_added = 0
        for char in characters:
            try:
                for key, default in PLAYER_DEFAULTS.items():
                    current = char.attributes.get(key)
                    if current is None:
                        import copy
                        char.attributes.add(key, copy.deepcopy(default))
                        attrs_added += 1
                updated += 1
            except Exception:
                logger.exception("Failed to migrate %s", getattr(char, "key", "?"))

        logger.info("Admin %s migrated %d characters (%d attrs added)", caller.key, updated, attrs_added)
        caller.msg(f"Migrated {updated} player(s). {attrs_added} missing attribute(s) filled in.")
