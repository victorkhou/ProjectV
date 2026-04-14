"""
Admin commands for the RTS Combat Overworld.

Restricted to Builder+ permission level. All executions are logged
with operator name, command, and target.

Requirements: 26.1, 33.1, 33.2, 33.3, 33.4, 33.5
"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand
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


class CmdGiveResource(BaseCommand):
    """Give resources to a player.

    Usage:
        @giveresource <player> <resource> <amount>

    Restricted to Builder+ permission level.
    """

    key = "@giveresource"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        # Permission check
        if not _check_perm(caller, "Builder"):
            caller.msg("Permission denied. Builder+ required.")
            return

        args = self.args.strip().split()
        if len(args) < 3:
            caller.msg("Usage: @giveresource <player> <resource> <amount>")
            return

        player_name, resource_type, amount_str = args[0], args[1], args[2]

        try:
            amount = int(amount_str)
        except ValueError:
            caller.msg(f"Invalid amount: {amount_str}")
            return

        if amount <= 0:
            caller.msg("Amount must be positive.")
            return

        # Find the target player
        target = caller.search(player_name) if hasattr(caller, "search") else None
        if target is None:
            caller.msg(f"Could not find player '{player_name}'.")
            return

        if not hasattr(target, "add_resource"):
            caller.msg(f"{player_name} is not a valid player character.")
            return

        target.add_resource(resource_type, amount)

        caller.msg(
            f"Gave {amount} {resource_type} to {player_name}."
        )
        logger.info(
            "Admin @giveresource: %s gave %d %s to %s",
            caller.key, amount, resource_type, player_name,
        )

        # Notify the target if they have msg
        if hasattr(target, "msg") and target is not caller:
            target.msg(
                f"|y[Admin] You received {amount} {resource_type} "
                f"from {caller.key}.|n"
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


class CmdSpawnBuilding(BaseCommand):
    """Spawn a building on your current tile.

    Usage:
        @spawnbuilding <type> [owner=<name>] [level=<n>]

    Arguments:
        type  - Building abbreviation (HQ, MM, QQ, VV, AA, AR, etc.)
        owner - Character name to own the building (default: you)
        level - Building level 1-5 (default: 1)

    Examples:
        @spawnbuilding HQ
        @spawnbuilding MM owner=victor level=3
        @spawnbuilding VV level=5

    Restricted to Builder+ permission level.
    """

    key = "@spawnbuilding"
    aliases = ["@sb"]
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        args = self.args.strip()
        if not args:
            caller.msg("Usage: @spawnbuilding <type> [owner=<name>] [level=<n>]")
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
                caller.msg(f"Unknown building type '{btype}'. Valid: HQ, MM, QQ, II, LL, KK, AA, AR, VV, TL, HV")
                return
        else:
            bdef = None

        # Get current tile — buildings need a real room at their coordinates
        loc = caller.location
        if loc is None:
            caller.msg("You have no location.")
            return

        # For buildings, we need a real OverworldRoom at the tile coordinates
        # (not the shared PlanetRoom)
        building_room = loc
        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)
        cp = getattr(caller.db, "coord_planet", None)

        if cx is not None and cy is not None and cp:
            resolver = _get_system(caller, "tile_resolver")
            if resolver is not None:
                try:
                    building_room = resolver.resolve(cx, cy, cp)
                except (ValueError, KeyError):
                    caller.msg("Could not resolve tile for building placement.")
                    return

        # Create the building
        try:
            from evennia.utils.create import create_object

            hp = bdef.max_health if bdef else 500
            name = bdef.name if bdef else btype

            building = create_object(
                typeclass="typeclasses.objects.Building",
                key=name,
                location=building_room,
            )
            building.attributes.add("building_type", btype)
            building.attributes.add("owner", owner)
            building.attributes.add("building_level", level)
            building.attributes.add("hp", hp)
            building.attributes.add("hp_max", hp)
            building.attributes.add("offline", False)
            building.tags.add("building", category="object_type")

            owner_name = getattr(owner, "key", "nobody") if owner else "nobody"
            logger.info(
                "Admin %s spawned %s (level %d, owner=%s) at %s",
                caller.key, btype, level, owner_name, building_room.key if hasattr(building_room, "key") else building_room,
            )
            caller.msg(f"Spawned {name} ({btype}) level {level}, owned by {owner_name}.")
        except Exception as e:
            caller.msg(f"Failed to create building: {e}")


class CmdTeleport(BaseCommand):
    """Teleport to coordinates on the overworld.

    Usage:
        teleport <x>,<y>,<planet>
        teleport <x>,<y>

    If planet is omitted, uses your current planet.
    Creates the room on-demand via TileResolver.

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

        # Update coordinates
        caller.db.coord_x = tx
        caller.db.coord_y = ty
        caller.db.coord_planet = planet

        # Only move_to if changing planets or not in a planet room
        if planet_rooms:
            target_room = planet_rooms.get(planet)
            if target_room and caller.location is not target_room:
                caller.move_to(target_room, quiet=True)
        else:
            # Fallback: use tile resolver if no planet rooms
            resolver = _get_system(caller, "tile_resolver")
            if resolver is not None:
                try:
                    target = resolver.resolve(tx, ty, planet)
                    if target:
                        caller.move_to(target, quiet=True)
                except (ValueError, KeyError) as e:
                    caller.msg(f"Could not resolve coordinates: {e}")
                    return

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
    """Purge empty overworld rooms from the database.

    Removes all OverworldRoom objects that have no players, no
    buildings, no custom descriptions, and no depleted resources.
    This reclaims DB space from rooms created by exploration.

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
            from typeclasses.rooms import OverworldRoom

            all_rooms = list(
                OverworldRoom.objects.filter_family(
                    db_tags__db_key="overworld_tile",
                    db_tags__db_category="room_type",
                )
            )
        except Exception:
            caller.msg("Could not query overworld rooms.")
            return

        deleted = 0
        kept = 0
        for room in all_rooms:
            contents = getattr(room, "contents", [])

            # Keep rooms with players
            has_player = any(
                hasattr(obj, "has_account") and obj.has_account
                for obj in contents
            )
            if has_player:
                kept += 1
                continue

            # Keep rooms with buildings
            has_building = any(
                hasattr(obj, "attributes") and obj.attributes.has("building_type")
                for obj in contents
            )
            if has_building:
                kept += 1
                continue

            # Keep rooms with depleted resources
            rn = room.attributes.get("resource_node_data")
            if rn and isinstance(rn, dict) and rn.get("depleted"):
                kept += 1
                continue

            # Keep rooms with custom descriptions
            desc = room.attributes.get("desc")
            if desc and desc != "" and desc != "You see nothing special.":
                kept += 1
                continue

            # Safe to delete
            room.delete()
            deleted += 1

        logger.info(
            "Admin %s purged %d empty rooms (%d kept)",
            caller.key, deleted, kept,
        )
        caller.msg(f"Purged {deleted} empty rooms. {kept} rooms with state kept.")


class CmdResetResources(BaseCommand):
    """Reset all players to starting resources.

    Usage:
        @resetresources

    Sets every player's resources back to starting values.
    Does NOT touch other attributes — use @migrate for that.

    Restricted to Admin+ permission level.
    """

    key = "@resetresources"
    locks = "cmd:perm(Admin);view:perm(Admin)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        if not _check_perm(caller, "Admin"):
            caller.msg("Permission denied. Admin+ required.")
            return

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
                logger.exception("Failed to reset resources for %s", getattr(char, "key", "?"))

        logger.info("Admin %s reset resources for %d characters", caller.key, updated)
        caller.msg(f"Reset {updated} player(s) to starting resources.")


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


class CmdDestroyAgent(BaseCommand):
    """Destroy an agent NPC by ID, or clear stuck training state.

    Usage:
        @destroyagent <player> <agent_id>
        @destroyagent <player> training

    The ``training`` variant clears any stuck training state on all
    Academy buildings owned by the player and resets their next_agent_id.

    Restricted to Admin+ permission level.
    """

    key = "@destroyagent"
    locks = "cmd:perm(Admin);view:perm(Admin)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        if not _check_perm(caller, "Admin"):
            caller.msg("Permission denied. Admin+ required.")
            return

        args = self.args.strip().split()
        if len(args) < 2:
            caller.msg("Usage: @destroyagent <player> <agent_id|training>")
            return

        player_name = args[0]
        target_arg = args[1]

        # Find the target player
        target = caller.search(player_name) if hasattr(caller, "search") else None
        if target is None:
            caller.msg(f"Could not find player '{player_name}'.")
            return

        if target_arg.lower() == "training":
            self._clear_training(caller, target)
            return

        try:
            agent_id = int(target_arg)
        except ValueError:
            caller.msg("Agent ID must be a number or 'training'.")
            return

        self._destroy_agent(caller, target, agent_id)

    def _clear_training(self, caller, target):
        """Clear all stuck training state for a player."""
        cleared = 0
        try:
            from evennia.objects.models import ObjectDB

            # Find all buildings with training_owner pointing to this player
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

        logger.info(
            "Admin %s cleared training state on %d buildings for %s",
            caller.key, cleared, target.key,
        )
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

            logger.info(
                "Admin %s destroyed agent #%d (%s) belonging to %s",
                caller.key, agent_id, agent_name, target.key,
            )
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
                    caller.msg(f"Cleared stuck training for agent #{agent_id} on {target.key}'s Academy.")
                    logger.info(
                        "Admin %s cleared stuck training #%d for %s",
                        caller.key, agent_id, target.key,
                    )
                    break
        except Exception:
            pass

        if not cleared:
            caller.msg(f"Agent #{agent_id} not found for {target.key} (not spawned, not in training).")


class CmdListAgents(BaseCommand):
    """List all agents belonging to a player.

    Usage:
        @agents <player>

    Shows agent IDs, roles, status, and location for the target player.
    Restricted to Builder+ permission level.
    """

    key = "@agents"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        if not _check_perm(caller, "Builder"):
            caller.msg("Permission denied. Builder+ required.")
            return

        player_name = self.args.strip()
        if not player_name:
            caller.msg("Usage: @agents <player>")
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

        lines = [f"|w=== Agents for {target.key} ({count} total, next_id={next_id}) ===|n"]
        lines.append(f"  |c#1|n  Commander (player character)")

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

        caller.msg("\n".join(lines))
