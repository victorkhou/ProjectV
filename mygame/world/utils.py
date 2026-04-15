"""
Shared utility functions for the RTS Combat Overworld.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("evennia.world.utils")


# ------------------------------------------------------------------ #
#  System lookup
# ------------------------------------------------------------------ #

def get_system(caller: Any, system_name: str) -> Any | None:
    """Look up a game system by name.

    Checks caller.ndb.systems first (set during game init),
    then falls back to the module-level game_systems dict.
    """
    systems = getattr(getattr(caller, "ndb", None), "systems", None)
    if systems and isinstance(systems, dict):
        return systems.get(system_name)
    try:
        from server.conf.game_init import game_systems
        return game_systems.get(system_name)
    except (ImportError, AttributeError):
        return None


def get_game_systems() -> dict:
    """Return the global game_systems dict directly."""
    try:
        from server.conf.game_init import game_systems
        return game_systems
    except (ImportError, AttributeError):
        return {}


# ------------------------------------------------------------------ #
#  Coordinates
# ------------------------------------------------------------------ #

def get_coords(obj: Any) -> tuple[int, int] | None:
    """Extract (x, y) coordinates from an object.

    Checks coord_x/coord_y attributes on the object.
    """
    if hasattr(obj, "db"):
        cx = getattr(obj.db, "coord_x", None)
        cy = getattr(obj.db, "coord_y", None)
        if cx is not None and cy is not None:
            return (int(cx), int(cy))
    return None


def ensure_coords(caller: Any) -> tuple[Any, Any, str | None]:
    """Ensure caller has valid coordinates.

    Returns (x, y, planet) or (None, None, None) if unresolvable.
    """
    x = getattr(caller.db, "coord_x", None)
    y = getattr(caller.db, "coord_y", None)
    planet = getattr(caller.db, "coord_planet", None)

    if x is not None and y is not None and planet:
        return x, y, planet

    # Try to sync planet from PlanetRoom location
    loc = getattr(caller, "location", None)
    if loc is not None and hasattr(loc, "planet_name"):
        rp = getattr(loc, "planet_name", None)
        if rp and rp != "unknown":
            caller.db.coord_planet = rp
            planet = rp

    if hasattr(caller, "_ensure_overworld_position"):
        caller._ensure_overworld_position()
        x = getattr(caller.db, "coord_x", None)
        y = getattr(caller.db, "coord_y", None)
        planet = getattr(caller.db, "coord_planet", None)

    return x, y, planet


# ------------------------------------------------------------------ #
#  Entity type helpers
# ------------------------------------------------------------------ #

def get_obj_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Read an Evennia attribute from any object safely.

    Checks ``attributes`` handler first, then ``db`` proxy.
    Works on buildings, NPCs, rooms, or any Evennia object.
    """
    if obj is None:
        return default
    if hasattr(obj, "attributes") and hasattr(obj.attributes, "get"):
        val = obj.attributes.get(key, default=None)
        if val is not None:
            return val
    if hasattr(obj, "db"):
        val = getattr(obj.db, key, None)
        if val is not None:
            return val
    return default


def set_obj_attr(obj: Any, key: str, value: Any) -> None:
    """Write an Evennia attribute on any object safely."""
    if obj is None:
        return
    if hasattr(obj, "attributes") and hasattr(obj.attributes, "add"):
        obj.attributes.add(key, value)
    elif hasattr(obj, "db"):
        setattr(obj.db, key, value)


def get_building_type(building: Any) -> str | None:
    """Read the building_type string from a building object."""
    return get_obj_attr(building, "building_type")


def get_building_level(building: Any) -> int:
    """Read the building level from a building object."""
    if hasattr(building, "building_level"):
        return building.building_level
    return get_obj_attr(building, "building_level", 1) or 1


def is_player(entity: Any) -> bool:
    """Return True if the entity is a player character (has combat_xp)."""
    return (
        entity is not None
        and hasattr(entity, "db")
        and hasattr(entity.db, "combat_xp")
    )


def is_building(entity: Any) -> bool:
    """Return True if the entity is a building (has building_type attribute)."""
    return get_building_type(entity) is not None


# ------------------------------------------------------------------ #
#  Player / building location helpers
# ------------------------------------------------------------------ #

def player_at_building(player: Any, building: Any) -> bool:
    """Return True if the player is at the same tile as the building.

    Compares player coordinates against building coordinates.
    Reads ``db.coord_x/coord_y`` first, falls back to location
    properties for backward compatibility with test fakes.
    """
    # Get player coordinates — try db attrs, then location object
    px = getattr(getattr(player, "db", None), "coord_x", None)
    py = getattr(getattr(player, "db", None), "coord_y", None)
    if px is None or py is None:
        player_loc = getattr(player, "location", None)
        if player_loc is not None:
            px = getattr(player_loc, "x", None)
            py = getattr(player_loc, "y", None)
            if px is None and hasattr(player_loc, "db"):
                px = getattr(player_loc.db, "coord_x", None)
                py = getattr(player_loc.db, "coord_y", None)
    if px is None or py is None:
        return False

    # Get building coordinates — prefer db.coord_x/coord_y
    bx = getattr(getattr(building, "db", None), "coord_x", None)
    by = getattr(getattr(building, "db", None), "coord_y", None)
    if bx is None or by is None:
        # Fallback: try building.location for legacy rooms / test fakes
        loc = getattr(building, "location", None)
        if loc is not None:
            bx = getattr(loc, "x", None)
            by = getattr(loc, "y", None)
            if bx is None and hasattr(loc, "db"):
                bx = getattr(loc.db, "coord_x", None)
                by = getattr(loc.db, "coord_y", None)
    if bx is None or by is None:
        return False

    return int(px) == int(bx) and int(py) == int(by)


def player_inside_building(player: Any, building: Any) -> bool:
    """Return True if the player is inside the given building.

    Checks ``inside_building`` flag AND coordinate match.
    """
    if not getattr(getattr(player, "db", None), "inside_building", False):
        return False
    return player_at_building(player, building)


# ------------------------------------------------------------------ #
#  Building attribute helpers (aliases for get_obj_attr/set_obj_attr)
# ------------------------------------------------------------------ #

# These are the same as get_obj_attr/set_obj_attr but kept for backward
# compatibility and readability in building-specific code.
get_building_attr = get_obj_attr
set_building_attr = set_obj_attr


def get_building_info(building: Any) -> dict:
    """Extract common building info as a dict.

    Returns dict with keys: type, level, hp, hp_max, owner, name.
    """
    return {
        "type": get_building_attr(building, "building_type", "??") or "??",
        "level": get_building_attr(building, "building_level", 1) or 1,
        "hp": get_building_attr(building, "hp", "?"),
        "hp_max": get_building_attr(building, "hp_max", "?"),
        "owner": get_building_attr(building, "owner"),
        "name": getattr(building, "key", "??"),
    }


def get_closed_exits(building: Any) -> set[str]:
    """Return the set of closed exit directions for a building."""
    raw = get_building_attr(building, "closed_exits")
    if raw:
        try:
            return set(raw)
        except (TypeError, ValueError):
            pass
    return set()


def is_exit_closed(building: Any, direction: str) -> bool:
    """Check if a building's exit in the given direction is closed."""
    dir_map = {"n": "north", "s": "south", "e": "east", "w": "west"}
    direction = dir_map.get(direction, direction)
    return direction in get_closed_exits(building)


def is_owner(caller: Any, owner: Any) -> bool:
    """Check if caller is the owner of a building.

    Compares by .id for reliability across server restarts.
    """
    if owner is None:
        return False
    caller_id = getattr(caller, "id", None)
    owner_id = getattr(owner, "id", None)
    if caller_id is not None and owner_id is not None:
        return caller_id == owner_id
    return owner is caller


def is_admin(caller: Any) -> bool:
    """Check if caller has Builder+ permissions."""
    if hasattr(caller, "check_permstring"):
        try:
            return caller.check_permstring("Builder")
        except Exception:
            pass
    return False


# ------------------------------------------------------------------ #
#  Broadcast
# ------------------------------------------------------------------ #

def broadcast(message: str, cls: str = "game-chat") -> None:
    """Broadcast a tagged message to all connected players.

    Args:
        message: The text to send.
        cls: CSS class for webclient routing (default: "game-chat").
    """
    try:
        from evennia import SESSION_HANDLER
        for session in SESSION_HANDLER.get_sessions():
            account = session.get_account()
            if account and hasattr(account, "msg"):
                account.msg(text=(message, {"cls": cls}))
    except Exception:
        logger.exception("broadcast failed")


# ------------------------------------------------------------------ #
#  Chat formatting
# ------------------------------------------------------------------ #

def format_channel_message(sender: Any, message: str) -> str:
    """Format a global chat message: [Rank] Name: message"""
    name = getattr(sender, "key", "Unknown")
    rank = _get_rank_name(sender)
    return f"[{rank}] {name}: {message}"


def format_dm_message(sender: Any, message: str) -> str:
    """Format a direct message: [Rank] Name (DM): message"""
    name = getattr(sender, "key", "Unknown")
    rank = _get_rank_name(sender)
    return f"[{rank}] {name} (DM): {message}"


def _get_rank_name(player: Any) -> str:
    """Get the rank name for a player."""
    rank_name = getattr(player, "rank_name", None)
    if rank_name:
        return rank_name
    try:
        from world.systems.rank_system import rank_from_level
        level = getattr(getattr(player, "db", None), "level", None)
        if level is None:
            level = getattr(getattr(player, "db", None), "rank_level", None)
        if level is not None:
            rank_num = rank_from_level(int(level))
            try:
                from server.conf.game_init import game_systems
                registry = game_systems.get("registry")
                if registry:
                    for r in registry.ranks:
                        if r.level == rank_num:
                            return r.name.replace("_", " ")
            except (ImportError, AttributeError):
                pass
            return f"Rank {rank_num}"
    except (ImportError, AttributeError):
        pass
    return "Recruit"
