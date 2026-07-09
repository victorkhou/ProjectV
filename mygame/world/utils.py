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


# ------------------------------------------------------------------ #
#  Level reading  (single source of truth)
# ------------------------------------------------------------------ #

def _coerce_level(value: Any) -> int | None:
    """Coerce a stored level/rank value to ``int``; ``None`` if not numeric.

    Corrupted out-of-band state (an admin edit or migration bug leaving a
    non-numeric ``db.level``/``db.rank_level``) must not raise ``ValueError``
    up through level math into a command handler. Returns ``None`` so the
    caller falls back to its default.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug("Ignoring non-numeric level value %r; treating as unset.", value)
        return None


def get_player_level(entity: Any, default: int = 1) -> int:
    """Read an entity's Entity_Level (1-60), with legacy fallback.

    Prefers ``db.level``. Falls back to the legacy ``db.rank_level`` (a 1-12
    rank number) by mapping it to the first level of that rank
    (``(rank - 1) * LEVELS_PER_RANK + 1``); a ``rank_level`` already above the
    rank range is treated as an actual level. Returns ``default`` when the
    entity has no ``db`` or neither attribute is set.

    Single source of truth shared by RankSystem, TechLabSystem, PowerupSystem
    and AgentSystem so the "which level is this" rule cannot drift between them.
    Non-numeric stored values are treated as unset rather than raising.
    """
    from world.constants import NUM_RANKS, LEVELS_PER_RANK

    db = getattr(entity, "db", None)
    if db is None:
        return default
    lvl = _coerce_level(getattr(db, "level", None))
    if lvl is not None:
        return lvl
    rl = _coerce_level(getattr(db, "rank_level", None))
    if rl is not None:
        if 1 <= rl <= NUM_RANKS:
            return (rl - 1) * LEVELS_PER_RANK + 1
        return rl
    return default


def is_building(entity: Any) -> bool:
    """Return True if the entity is a building (has building_type attribute)."""
    return get_building_type(entity) is not None


def building_has_capability(building: Any, capability: str, provider: Any = None) -> bool:
    """Return True if *building*'s definition declares *capability*.

    Resolves the building's ``building_type`` via a
    :class:`~world.core.ports.definitions_provider.DefinitionsProvider` and
    checks its ``capabilities`` (see ``world.constants.BUILDING_CAPABILITIES``).
    Pass *provider* to inject a fake in tests; when omitted it defaults to a
    provider over the live ``DataRegistry`` (``default_definitions_provider``).
    Returns False if no provider is available or the type is unknown, so callers
    stay safe outside a running server. Shared entry point for capability checks
    on a *live building object* (a ``BuildingDef`` exposes ``has_capability``
    directly).
    """
    btype = get_building_type(building)
    if not btype:
        return False
    try:
        if provider is None:
            from world.adapters.registry_definitions_provider import (
                default_definitions_provider,
            )
            provider = default_definitions_provider()
        if provider is None:
            return False
        bdef = provider.resolve_building(btype)
    except Exception:
        return False
    return bdef is not None and bdef.has_capability(capability)


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


def _building_planet(building: Any) -> Any:
    """Best-effort planet key for a *building*.

    A building does not store its own planet; it is derived from its location
    (the ``PlanetRoom``, which exposes ``planet_name``). Falls back to a
    ``coord_planet`` attribute if one is present. Returns ``None`` when the
    planet cannot be determined (callers treat ``None`` planet as "any planet").
    """
    loc = getattr(building, "location", None)
    if loc is not None:
        pn = getattr(loc, "planet_name", None)
        if pn:
            return pn
    return get_obj_attr(building, "coord_planet")


def owner_has_active_hq(owner: Any, planet: Any = None, provider: Any = None) -> bool:
    """Return True if *owner* has a live (non-under-construction) HQ on *planet*.

    This is the "no HQ = base inert" predicate: it gates turret auto-fire,
    guard combat AI, equipment production, and building-specific commands, so
    that destroying a base's HQ deactivates the whole base until an HQ is
    rebuilt (PvP) — or, for an NPC base, until it is wiped (PvE). It is a live
    query with no stored state: the moment a new HQ finishes construction, this
    flips back to True and every gated system reactivates on the next tick.

    Shares the ``get_buildings`` enumeration + ``HEADQUARTERS`` capability check
    with :func:`_owner_hq_buildings`, which ``BuildingSystem._player_has_hq``
    also uses (Req 12.5), so the "does this owner have an HQ" logic lives in one
    place. Unlike ``_player_has_hq`` (which counts an HQ under construction, to
    enforce one-HQ-per-planet at build time), this predicate ignores an HQ that
    is still ``under_construction`` — a half-built HQ does not power the base.

    Args:
        owner: The building/agent owner (a Character, or an NPC Sentinel).
        planet: Planet key to scope the search to. ``None`` means any planet.
        provider: Optional DefinitionsProvider for the capability lookup
            (injected in tests); defaults to the live registry.

    Returns:
        ``True`` if *owner* has at least one completed HQ (optionally on
        *planet*); ``False`` otherwise.
    """
    for hq in _owner_hq_buildings(owner, planet=planet, provider=provider):
        if not get_obj_attr(hq, "under_construction", False):
            return True
    return False


def _owner_hq_buildings(owner: Any, planet: Any = None, provider: Any = None):
    """Yield *owner*'s HQ-capability buildings (optionally scoped to *planet*).

    The single enumeration used by both :func:`owner_has_active_hq` and
    ``BuildingSystem._player_has_hq`` (Req 12.5). Enumerates ``owner``'s
    buildings via ``get_buildings()`` (NOT planet-scoped — it returns every
    planet's buildings), filters to those declaring the ``HEADQUARTERS``
    capability, and — when *planet* is given — to those on that planet.

    Yields building objects; callers decide whether to further filter (e.g. on
    ``under_construction``). Safe outside a full Evennia env: an owner with no
    ``get_buildings`` yields nothing.
    """
    from world.constants import HEADQUARTERS

    if owner is None or not hasattr(owner, "get_buildings"):
        return
    try:
        buildings = owner.get_buildings()
    except Exception:
        return
    for b in buildings or ():
        if not building_has_capability(b, HEADQUARTERS, provider=provider):
            continue
        if planet is not None and _building_planet(b) not in (None, planet):
            continue
        yield b


# ------------------------------------------------------------------ #
#  Broadcast
# ------------------------------------------------------------------ #

def broadcast(message: str, cls: str = "game-chat") -> None:
    """Broadcast a tagged message to all connected players.

    Thin compatibility shim that delegates to the default
    :class:`~world.adapters.evennia_notifier.EvenniaNotifier`. New code should
    depend on the :class:`~world.core.ports.notifier.Notifier` port and have an
    adapter injected rather than calling this module-level helper.

    Args:
        message: The text to send.
        cls: CSS class for webclient routing (default: "game-chat").
    """
    from world.adapters.evennia_notifier import EvenniaNotifier

    EvenniaNotifier().broadcast(message, cls=cls)


# ------------------------------------------------------------------ #
#  Resource formatting
# ------------------------------------------------------------------ #

def format_insufficient_resources(player: Any, costs: dict[str, int]) -> str:
    """Format the shared "insufficient resources" breakdown.

    Returns a multi-line message listing EVERY required resource (not just the
    ones short), each as ``have/need`` and colored green when the requirement is
    met, red when it is not — a quick visual aid for what still needs gathering.
    Used by building construction/upgrade, agent training, and anywhere else a
    resource cost can't be met, so the message is identical everywhere.
    """
    lines = ["|rInsufficient Resources:|n"]
    for resource, needed in costs.items():
        current = player.get_resource(resource)
        color = "|g" if current >= needed else "|r"
        lines.append(f"  {color}{resource}: {current}/{needed}|n")
    return "\n".join(lines)


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


def _get_rank_name(player: Any, provider: Any = None) -> str:
    """Get the rank name for a player.

    Resolves rank definitions via a
    :class:`~world.core.ports.definitions_provider.DefinitionsProvider`
    (injectable for tests; defaults to a provider over the live registry).
    """
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
                if provider is None:
                    from world.adapters.registry_definitions_provider import (
                        default_definitions_provider,
                    )
                    provider = default_definitions_provider()
                if provider is not None:
                    for r in provider.ranks:
                        if r.level == rank_num:
                            return r.name.replace("_", " ")
            except (ImportError, AttributeError):
                pass
            return f"Rank {rank_num}"
    except (ImportError, AttributeError):
        pass
    return "Recruit"
