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
#  Agent resting status (single derived authority)
# ------------------------------------------------------------------ #

def resting_activity_status(agent: Any) -> str:
    """Return the *resting* activity status for *agent* — what it shows when
    stationed and not mid-action.

    This is the single authority for the resting status. It is a pure function
    of the agent's persistent state (incapacitated / reserve / role /
    role_target), so no caller has to *guess* the status and no two writers can
    disagree. In particular the movement engine (``NPC.advance_movement``),
    which knows nothing about roles, calls this on arrival instead of
    hardcoding ``"Idle"`` — the defect that left an engineer at a producing
    Armory stuck reading "Idle" (it has no per-tick status writer of its own).

    Transient, moment-to-moment statuses (``"Harvesting Wood"``,
    ``"Patrol blocked — retrying"``, ``"Delivering ..."``) are NOT resting
    statuses: the role scripts set those imperatively each tick, after
    movement, so they naturally supersede this default while the agent is
    actively doing something. Precedence (highest first):

        incapacitated > reserve > (role at a building) > (army role) > idle
    """
    from world.constants import (
        ACTIVITY_IDLE, ACTIVITY_WORKING, ACTIVITY_READY,
        ACTIVITY_RESERVE, ACTIVITY_INCAPACITATED,
    )

    db = getattr(agent, "db", None)
    if db is None:
        return ACTIVITY_IDLE

    if getattr(db, "incapacitated", False):
        return ACTIVITY_INCAPACITATED
    if getattr(db, "reserve", False):
        return ACTIVITY_RESERVE

    role = getattr(db, "role", None) or ""
    if not role:
        return ACTIVITY_IDLE

    # Assigned to a building (engineer/harvester/guard/scout/medic-at-Medbay):
    # it's on station, doing (or ready to do) its building's work.
    if getattr(db, "role_target", None) is not None:
        return ACTIVITY_WORKING

    # A building-less army role (soldier, or medic with no Medbay) is on
    # standby — "Ready", not "Idle" (it has a job, just no fixed post).
    try:
        from world.systems.agent_constants import ARMY_ROLES
        if role in ARMY_ROLES:
            return ACTIVITY_READY
    except Exception:  # noqa: BLE001 - role table unavailable (isolated import)
        pass

    return ACTIVITY_IDLE


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


def chebyshev_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Return the Chebyshev (chessboard) distance between two coordinate pairs.

    ``max(|dx|, |dy|)`` — a diagonal step counts as distance 1, so all eight
    surrounding tiles are "1 away". This is the SINGLE distance metric for the
    game's spatial reach: combat range/adjacency, guard/turret target
    acquisition, and throw AoE all use it, matching the Chebyshev vision circles
    used by fog-of-war. So "1 north and 1 west" (a diagonal) is in melee reach.
    """
    return max(abs(x1 - x2), abs(y1 - y2))


def nearby_players(location: Any, x: int, y: int, radius: int) -> list:
    """Return players near ``(x, y)`` within *radius* via *location*.

    The single spatial-targeting helper shared by turret fire (CombatEngine) and
    guard combat AI (GuardCombatSystem) — previously copy-pasted in both. Prefers
    the PlanetRoom's ``get_nearby_players(x, y, radius)`` spatial query; falls
    back to a ``_nearby_players`` attribute for lightweight test doubles. Returns
    ``[]`` when *location* is ``None`` or exposes neither.
    """
    if location is None:
        return []
    if hasattr(location, "get_nearby_players"):
        return location.get_nearby_players(x, y, radius)
    if hasattr(location, "_nearby_players"):
        return location._nearby_players
    return []


# ------------------------------------------------------------------ #
#  Tile (room) item capacity
# ------------------------------------------------------------------ #

#: object_type tags that count as a "loose ground item" against a tile's cap.
#: Buildings and NPCs/agents do NOT count — only pickupable drops.
_GROUND_ITEM_TAGS = ("item", "resource_drop")


def tile_object_count(room: Any, x: int, y: int) -> int:
    """Count loose ground items (GameItems + ResourceDrops) at ``(x, y)``.

    This is a tile's "carry weight" for the room-capacity cap: dropped gear,
    dropped supplies, and resource drops. Buildings and agents/NPCs on the tile
    are excluded — they are not pickupable drops. Returns 0 when *room* can't be
    queried (no ``get_objects_at``), so lightweight test doubles are safe.
    """
    if room is None or not hasattr(room, "get_objects_at"):
        return 0
    total = 0
    for tag in _GROUND_ITEM_TAGS:
        try:
            total += len(room.get_objects_at(int(x), int(y), type_tag=tag))
        except Exception:  # noqa: BLE001 - a query failure must not break drops
            continue
    return total


def tile_item_capacity(
    room: Any, x: int, y: int, provider: Any = None, balance: Any = None
) -> int:
    """Return the max loose ground items a tile at ``(x, y)`` may hold.

    Depends on the building (if any) occupying the tile:
      * no building                 -> ``balance.room_capacity_empty`` (1)
      * Vault (storage) / Extractor (harvestable) -> ``room_capacity_per_storage_level``
        x the building's level
      * any other building          -> ``balance.room_capacity_building`` (10)

    *provider*/*balance* may be injected for tests; both default to the live
    registry. Falls back to the empty-tile cap when the room can't be queried.
    """
    from world.constants import HARVESTABLE, STORAGE

    if balance is None:
        from world.adapters.registry_definitions_provider import default_balance
        balance = default_balance()

    empty_cap = int(getattr(balance, "room_capacity_empty", 1))
    building_cap = int(getattr(balance, "room_capacity_building", 10))
    per_level = int(getattr(balance, "room_capacity_per_storage_level", 20))

    building = _building_on_tile(room, x, y)
    if building is None:
        return empty_cap

    # Storage (Vault) or resource (Extractor) tiles scale with building level.
    if building_has_capability(building, STORAGE, provider=provider) or \
            building_has_capability(building, HARVESTABLE, provider=provider):
        level = get_building_level(building)
        return per_level * max(1, int(level))

    return building_cap


def tile_has_room(
    room: Any, x: int, y: int, provider: Any = None, balance: Any = None
) -> bool:
    """Return True if a NEW ground item can be created at ``(x, y)``.

    Compares the current loose-item count against :func:`tile_item_capacity`.
    Callers that MERGE into an existing drop (growing its count, not adding an
    object) should skip this check — a merge never increases the object count,
    so it is always allowed. Only creating a brand-new drop object is capped.
    """
    return tile_object_count(room, x, y) < tile_item_capacity(
        room, x, y, provider=provider, balance=balance
    )


def _building_on_tile(room: Any, x: int, y: int) -> Any | None:
    """Return the building occupying tile ``(x, y)`` of *room*, or None."""
    if room is None:
        return None
    if hasattr(room, "get_buildings_at"):
        try:
            buildings = room.get_buildings_at(int(x), int(y))
        except Exception:  # noqa: BLE001
            return None
        return buildings[0] if buildings else None
    return None


def nearest_free_tile(room: Any, x: int, y: int, *, in_bounds=None,
                      max_radius: int = 12) -> tuple[int, int]:
    """Return the nearest tile to ``(x, y)`` with no building on it.

    Spawn points (a fixed planet spawn, an HQ tile that's since gone) can land
    on a tile a building occupies — dropping the player *inside* someone else's
    structure. This scans outward in growing rings (Chebyshev) and returns the
    first building-free tile, so the player lands *beside* an obstruction rather
    than on it. ``(x, y)`` itself is returned when already free, and as the
    last-resort fallback if nothing free is found within *max_radius*.

    Args:
        room: The PlanetRoom (needs ``get_buildings_at``); a None/other room
            means we can't check occupancy, so ``(x, y)`` is returned as-is.
        x, y: The desired tile.
        in_bounds: Optional ``(x, y) -> bool`` predicate; candidate tiles that
            fail it are skipped (so we never suggest an off-map tile).
        max_radius: How far out to search before giving up.
    """
    if room is None or not hasattr(room, "get_buildings_at"):
        return (x, y)

    def _ok(cx, cy):
        if in_bounds is not None:
            try:
                if not in_bounds(cx, cy):
                    return False
            except Exception:  # noqa: BLE001
                return False
        return _building_on_tile(room, cx, cy) is None

    if _ok(x, y):
        return (x, y)
    for r in range(1, max_radius + 1):
        # Walk the perimeter of the r-ring around (x, y).
        for cx in range(x - r, x + r + 1):
            for cy in (y - r, y + r):
                if _ok(cx, cy):
                    return (cx, cy)
        for cy in range(y - r + 1, y + r):
            for cx in (x - r, x + r):
                if _ok(cx, cy):
                    return (cx, cy)
    return (x, y)  # nothing free within range — caller lands on the original


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
    """Return True if the entity is a ``CombatEntity`` — i.e. a mobile combat unit.

    Despite the name, this is really "is this a combat entity that carries
    ``db.combat_xp``": that covers player characters AND every NPC
    (``CombatEntity``: player-owned agents AND enemy-base guards). It does NOT
    cover buildings/items/drops (``GameEntity`` only, no ``combat_xp``). Callers
    that mean specifically "player character" should additionally check
    ``has_account`` / ``npc_type``; callers that mean "any movable combat unit"
    (e.g. the ``transfer`` admin command, which pulls players AND NPCs but must
    reject fixed structures) use this predicate as-is. Combat routing relies on
    the same breadth — see ``combat_engine`` / ``base_elimination`` ("enemy NPCs
    also satisfy is_player").

    The check reads the VALUE of ``combat_xp``, not merely whether the attribute
    is accessible: on a real Evennia object ``db`` is a ``DbHolder`` whose
    ``__getattribute__`` returns ``None`` for any unset attribute and never
    raises, so ``hasattr(entity.db, "combat_xp")`` is ``True`` for *every* object
    with a ``.db``. A value-based check (``combat_xp is not None``) correctly
    excludes buildings, whose ``combat_xp`` is unset (``None``). ``combat_xp`` is
    always initialised to ``0`` on a CombatEntity, so a live unit still reads a
    non-``None`` value.
    """
    if entity is None or not hasattr(entity, "db"):
        return False
    return getattr(entity.db, "combat_xp", None) is not None


def player_is_present(entity: Any) -> bool:
    """Return True if *entity* is a player present in the world as a combat target.

    The single "can this player be hit / seen by turret & guard targeting"
    predicate, used by ``get_players_at`` / ``get_nearby_players``. "Present"
    means:

    * puppeted AND actually in the game — ``player_state`` is ``PLAYING`` or
      ``None`` (the lobby flow disabled, or a legacy character: unchanged
      behavior); OR
    * ``LINKDEAD`` — a dropped player's character lingers in the world as a live
      combat target during its grace window (the anti-combat-log rule), even
      though it holds no session (``has_account`` False).

    A player who is OOC in the spawning/lobby flow (``SPAWNING`` / ``LOBBY``) is
    NOT present: they are staging, not deployed, so they can't be targeted — this
    closes the spawn-camp / login-window window where a just-respawned or
    just-logged-in character sat at full HP on a tile and could be re-downed
    every tick. A stowed/logged-out character (not linkdead) is also not present.
    Never raises.
    """
    if entity is None or not hasattr(entity, "has_account"):
        return False
    from world.constants import (
        PLAYER_STATE_LINKDEAD, PLAYER_STATE_PLAYING,
    )
    state = getattr(getattr(entity, "db", None), "player_state", None)
    if state == PLAYER_STATE_LINKDEAD:
        return True
    try:
        if not entity.has_account:
            return False
    except Exception:  # noqa: BLE001
        return False
    # Puppeted: present only if actually in the game (PLAYING) or the flow is
    # off / legacy (state None). SPAWNING/LOBBY are OOC and not targetable.
    return state in (None, PLAYER_STATE_PLAYING)


def find_linkdead_characters() -> list:
    """Return every character persisted in the LINKDEAD state (or ``[]``).

    The single enumeration of linkdead characters, shared by the tick-loop grace
    expiry (``GameTickScript._process_linkdead_expiry``) and the ``who`` roster
    (``CmdWho._append_linkdead_rows``) — they hold no session, so the
    online-players roster misses them and both callers must search the DB.

    Correctness note (the reason this is a function, not an inline ORM filter):
    a plain ``db.player_state = "..."`` assignment stores the value PICKLED in
    ``db_value`` with ``db_strvalue`` left ``None``, so a ``db_strvalue`` ORM
    filter matches NOTHING on a real DB (the "grace timer effectively infinite"
    regression). ``search_object_attribute`` matches on the actual stored value.
    Best-effort — returns ``[]`` if the search is unavailable (e.g. stubbed test
    env) so neither caller breaks.
    """
    try:
        from evennia.utils.search import search_object_attribute
        from world.constants import PLAYER_STATE_LINKDEAD
        return list(
            search_object_attribute(key="player_state", value=PLAYER_STATE_LINKDEAD)
        )
    except Exception:  # noqa: BLE001 - enumeration must never raise into callers
        return []


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


def building_is_open(building: Any, provider: Any = None) -> bool:
    """Return True if *building* is *open* to ranged fire (CLOSED when unset).

    Open buildings can be targeted/hit by ranged weapons and turrets and give
    their occupants no cover; closed ones can only be hit by adjacent melee
    attacks and shelter a player inside them. The single reader shared by the
    combat engine (direct/queued attacks + turret targeting) and the throw-AoE
    targeting, so the open/closed rule lives in one place.

    A **Wall** (any ``combat_barrier`` building) is intrinsically OPEN regardless
    of its ``open`` attribute: a wall is a barrier meant to be shot/broken down,
    never cover, so ranged weapons and turrets can breach it (the "breach the
    walls" raid mechanic). For every other building the per-instance ``open``
    attribute governs, read via ``get_obj_attr``; an unset value reads as CLOSED
    so pre-existing buildings are treated as cover.
    """
    from world.constants import COMBAT_BARRIER

    if building_has_capability(building, COMBAT_BARRIER, provider=provider):
        return True
    return bool(get_obj_attr(building, "open", False))


def player_is_sheltered(player: Any) -> bool:
    """Return True if *player* is sheltered from ranged fire.

    A player is sheltered when they are INSIDE a building (``db.inside_building``)
    that is CLOSED (:func:`building_is_open` is False for the building on their
    tile). A sheltered player cannot be targeted or hit by turrets, ranged
    weapons, or thrown explosives — only by an adjacent melee attack. The single
    reader shared by turret targeting, guard targeting, ranged ``queue_attack``,
    and throw-AoE, so the shelter rule lives in one place.

    Returns False for anything that isn't a player inside a closed building
    (agents/NPCs, players in the open, players inside an open building), so
    callers stay guard-free. Never raises.
    """
    db = getattr(player, "db", None)
    if db is None:
        return False
    if not getattr(db, "inside_building", False):
        return False
    room = getattr(player, "location", None)
    x = getattr(db, "coord_x", None)
    y = getattr(db, "coord_y", None)
    if room is None or x is None or y is None:
        return False
    building = _building_on_tile(room, int(x), int(y))
    if building is None:
        return False
    return not building_is_open(building)


def target_inside_building(target: Any) -> bool:
    """Return True if *target* is a player currently inside a building.

    A player standing inside a building (``db.inside_building``) that actually
    exists on their tile is "in a room" — a melee attacker must be on the SAME
    tile (i.e. inside the same building) to reach them; an adjacent attacker on
    a neighbouring tile cannot. Unlike :func:`player_is_sheltered` this does NOT
    depend on the building being closed: even an OPEN enemy building is a room
    for the purpose of melee reach (open only governs ranged cover).

    Returns False for anything that isn't a player inside a real building
    (NPCs, players in the open), so callers stay guard-free. Never raises.
    """
    db = getattr(target, "db", None)
    if db is None:
        return False
    if not getattr(db, "inside_building", False):
        return False
    room = getattr(target, "location", None)
    x = getattr(db, "coord_x", None)
    y = getattr(db, "coord_y", None)
    if room is None or x is None or y is None:
        return False
    return _building_on_tile(room, int(x), int(y)) is not None


def same_tile(a: Any, b: Any) -> bool:
    """Return True if entities *a* and *b* occupy the same (x, y) tile.

    Compares ``db.coord_x/coord_y`` via :func:`get_coords`. Returns False when
    either entity has no resolvable coordinates. Never raises.
    """
    ca = get_coords(a)
    cb = get_coords(b)
    if ca is None or cb is None:
        return False
    return ca == cb


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


def _is_real_player(entity: Any) -> bool:
    """Return True if *entity* is a real player character (not an NPC/Sentinel).

    The belt-and-braces guard behind the alliance real-player invariant (C8):
    an alliance member must have a live account, must not be a Sentinel (the
    never-puppeted NPC-base owner), and must not carry an ``npc_type`` (agents /
    enemy guards). Even if a stray ``player_alliance`` pointer were somehow
    written onto an NPC base owner, :func:`are_allied` would still refuse to
    treat it as an ally — so a PvE fortress can never be made untargetable.

    Value-based reads only; never raises.
    """
    if entity is None:
        return False
    try:
        if not getattr(entity, "has_account", False):
            return False
    except Exception:  # noqa: BLE001
        return False
    db = getattr(entity, "db", None)
    if db is None:
        return False
    # An NPC (agent/enemy guard) carries npc_type; a Sentinel is a non-account
    # CombatCharacter (already excluded by has_account) but guard on the tag too.
    if getattr(db, "npc_type", None) is not None:
        return False
    try:
        if entity.tags.get("sentinel", category="npc_role"):
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def are_allied(a: Any, b: Any) -> bool:
    """Return True iff *a* and *b* are two DISTINCT real players in the same alliance.

    The single ally predicate — the alliance counterpart to :func:`is_owner`,
    added alongside it as the one authority for "same side". In combat it is
    ALWAYS called with the Owning_Players (via ``_owning_player``), never raw
    units, so a turret/agent is judged by its owner's alliance.

    Returns ``True`` only when ALL hold:

    * both *a* and *b* are real player characters (:func:`_is_real_player` — has
      an account, not a Sentinel, no ``npc_type``), so an NPC base owner can
      never be treated as an ally (C8);
    * they are DISTINCT players — sameness decided exactly like ``is_owner``
      (compare ``.id`` when both non-``None``; else identity), so a unit is never
      "allied to itself" and two same-PK instances after an idmapper flush are
      treated as the same player (→ ``False``);
    * both hold the SAME non-``None`` ``db.player_alliance`` (value-based reads:
      ``is None`` / ``==``, never truthiness — a legitimate ``alliance_id`` is
      always ``>= 1`` so this never trips on ``0``);
    * that shared ``alliance_id`` STILL resolves to a live Alliance_Record via
      the AllianceSystem — a stale pointer left by a disband while a member was
      offline resolves to nothing and yields ``False``.

    Fails toward ``False`` on any missing ``db``, unavailable AllianceSystem, or
    unresolved record: a lookup failure must never SUPPRESS legitimate hostile
    targeting (the safe direction is "treat as enemies").
    """
    if a is None or b is None:
        return False
    if not (_is_real_player(a) and _is_real_player(b)):
        return False
    # Distinct-player check, mirroring is_owner's idmapper-safe comparison.
    a_id = getattr(a, "id", None)
    b_id = getattr(b, "id", None)
    if a_id is not None and b_id is not None:
        if a_id == b_id:
            return False
    elif a is b:
        return False

    a_alliance = getattr(getattr(a, "db", None), "player_alliance", None)
    b_alliance = getattr(getattr(b, "db", None), "player_alliance", None)
    if a_alliance is None or b_alliance is None:
        return False
    if a_alliance != b_alliance:
        return False

    # The shared id must still resolve to a LIVE record (defends stale pointers).
    system = get_system(a, "alliance_system")
    if system is None:
        return False
    try:
        return system.alliance_exists(a_alliance)
    except Exception:  # noqa: BLE001 - a lookup failure never suppresses hostility
        return False


def shared_visible_tiles(player: Any, player_buildings: Any, fog_system: Any) -> set:
    """Return *player*'s visible tiles, unioned with PLAYING allies' if the
    shared-vision perk is active.

    The single entry point the three fog-of-war callers (ASCII renderer, web
    map-data provider, and the ``look`` path) use so shared vision cannot drift
    between them. Delegates to ``AllianceSystem.shared_visible_tiles`` (which
    applies the PLAYING-only filter and the per-ally union); falls back to the
    player's own ``fog_system.get_visible_tiles(player, player_buildings)`` when
    there is no AllianceSystem or the perk is inactive. Never raises into map
    building.
    """
    try:
        system = get_system(player, "alliance_system")
        if system is not None:
            return system.shared_visible_tiles(
                player, player_buildings, fog_system,
                building_lookup=lambda ally: (
                    ally.get_buildings() if hasattr(ally, "get_buildings") else []
                ),
            )
    except Exception:  # noqa: BLE001 - shared vision never breaks the base view
        logger.debug("shared_visible_tiles failed; using own vision", exc_info=True)
    return set(fog_system.get_visible_tiles(player, player_buildings) or [])


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


def active_hq_owner_ids(buildings: Any, provider: Any = None) -> set:
    """Return the set of owner ``.id``s that have a completed HQ in *buildings*.

    A per-tick precomputation for the "no HQ = base inert" gate. Iterating the
    already-gathered active-building list once — using only in-memory capability
    lookups (no DB query) — yields every owner whose base is currently powered.
    Turret and guard-AI steps then test ``owner.id in active_ids`` instead of
    calling :func:`owner_has_active_hq` (which runs a ``get_buildings()`` DB
    query) for *every* turret/guard on *every* tick. This turns an
    O(entities)-DB-queries-per-tick gate into a single O(buildings) in-memory
    pass.

    An HQ that is still ``under_construction`` does not count (mirrors
    :func:`owner_has_active_hq`).

    Args:
        buildings: The active-building list for this tick.
        provider: Optional DefinitionsProvider for the capability lookup;
            defaults to the live registry inside ``building_has_capability``.

    Returns:
        A set of owner ids (ints). Owners without a resolvable ``.id`` are
        omitted.
    """
    from world.constants import HEADQUARTERS

    ids: set = set()
    for b in buildings or ():
        if get_obj_attr(b, "under_construction", False):
            continue
        if not building_has_capability(b, HEADQUARTERS, provider=provider):
            continue
        owner = getattr(b, "owner", None)
        if owner is None:
            owner = get_obj_attr(b, "owner")
        oid = getattr(owner, "id", None)
        if oid is not None:
            ids.add(oid)
    return ids


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
