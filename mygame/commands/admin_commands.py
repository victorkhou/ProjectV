"""
Admin commands for the RTS Combat Overworld.

Restricted to Builder+ permission level. All executions are logged
with operator name, command, and target.

Requirements: 26.1, 33.1, 33.2, 33.3, 33.4, 33.5
"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand

logger = logging.getLogger("mygame.admin")


class CmdReloadData(BaseCommand):
    """Hot-reload all YAML definition files.

    Usage:
        @reloaddata

    Restricted to Builder+ permission level.
    """

    key = "@reloaddata"
    locks = "cmd:perm(Builder)"
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
    locks = "cmd:perm(Builder)"
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

    key = "teleport"
    aliases = ["@tel"]
    locks = "cmd:perm(Builder)"
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

        # Get tile resolver
        resolver = _get_system(caller, "tile_resolver")
        if resolver is None:
            caller.msg("Tile resolver not available.")
            return

        # Validate bounds
        if not registry.is_valid_coordinate(tx, ty, planet):
            caller.msg(f"Coordinates ({tx}, {ty}) are out of bounds for {planet}.")
            return

        # Resolve (creates room on demand)
        try:
            target = resolver.resolve(tx, ty, planet)
        except (ValueError, KeyError) as e:
            caller.msg(f"Could not resolve coordinates: {e}")
            return

        if target is None:
            caller.msg("Could not create room at those coordinates.")
            return

        # Remember old room for cleanup
        old_room = caller.location

        caller.move_to(target, quiet=True)
        caller.db.coord_x = tx
        caller.db.coord_y = ty
        caller.db.coord_planet = planet

        # Clean up old room if empty
        if old_room is not None and old_room is not target:
            from commands.game_commands import _maybe_cleanup_room
            _maybe_cleanup_room(old_room, resolver)

        logger.info("Admin %s teleported to (%d, %d, %s)", caller.key, tx, ty, planet)
        caller.msg(f"Teleported to ({tx}, {ty}) on {planet}.")


def _get_system(caller, system_name):
    """Look up a game system by name."""
    systems = getattr(getattr(caller, "ndb", None), "systems", None)
    if systems and isinstance(systems, dict):
        return systems.get(system_name)
    try:
        from server.conf.game_init import game_systems
        return game_systems.get(system_name)
    except (ImportError, AttributeError):
        return None


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
    locks = "cmd:perm(Builder)"
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
