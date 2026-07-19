"""
Fog of War System — RTS-style per-player visibility and discovery memory.

Manages three visibility states per tile:
- visible: within any vision source (player or owned building)
- fog: previously discovered but not currently visible
- unexplored: never seen

Vision is computed as the union of Chebyshev-distance circles around
the player position and each owned building.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from world.coordinate.discovery_bitfield import DiscoveryBitfield

if TYPE_CHECKING:
    from world.definitions import BalanceConfig


@dataclass
class DiscoveredBuildingState:
    """Snapshot of an enemy building seen in fog."""

    building_type: str  # abbreviation e.g. "HQ"
    owner_name: str
    x: int
    y: int


class FogOfWarSystem:
    """RTS-style fog of war with discovery memory.

    Vision sources:
    - Circle of ``player_vision_radius`` around the player position
    - Circle of ``building_vision_radius`` around each owned building

    Distance metric: Chebyshev (max(|dx|, |dy|) <= radius).
    """

    def __init__(self, balance: BalanceConfig) -> None:
        self.player_vision_radius: int = balance.player_vision_radius
        self.building_vision_radius: int = balance.building_vision_radius
        self.scout_vision_radius: int = getattr(balance, "scout_vision_radius", 0)
        self._map_border: int = getattr(balance, "map_border_tiles", 5)
        #: Injected ``(x, y, planet_key) -> bool`` map-bounds check (the
        #: PlanetRegistry's ``is_valid_coordinate``), wired at the composition
        #: root. When unset, every tile is treated as in-bounds (tests /
        #: unwired), so the bounds overlay only ever ADDS edge fog, never hides
        #: a real tile.
        self._in_bounds_func = None

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def set_in_bounds_func(self, fn) -> None:
        """Inject the map-bounds check (``planet_registry.is_valid_coordinate``).

        Late-bound at the composition root because the PlanetRegistry is built
        after the FogOfWarSystem.
        """
        self._in_bounds_func = fn

    def is_in_bounds(self, planet: str, x: int, y: int) -> bool:
        """Return True if tile ``(x, y)`` is inside *planet*'s map bounds.

        A tile beyond ``0,0`` or the planet's max coords is OUT of bounds — it
        is not part of the world and the renderers show it as fog of war. Falls
        open (True) when no bounds func is wired or the planet is unknown, so an
        unwired/test context never turns real tiles into edge fog. Never raises.
        """
        if self._in_bounds_func is None:
            return True
        try:
            return bool(self._in_bounds_func(int(x), int(y), planet))
        except Exception:  # noqa: BLE001 - unknown planet / bad coords: fall open
            return True

    def get_visible_tiles(
        self, player: Any, player_buildings: list[Any],
        player_scouts: list[Any] | None = None,
    ) -> set[tuple[int, int]]:
        """Compute the union of all vision circles for the player.

        Vision sources: the player circle, each owned building's circle, and —
        when *player_scouts* is passed — a circle around each active scout
        agent (early-game rebalance R5: patrol has a visible payoff).

        Returns a set of (x, y) tuples that are currently visible.
        """
        visible: set[tuple[int, int]] = set()

        # Player vision circle (base radius + equipped sight_range bonus)
        px = _get_coord(player, "coord_x")
        py = _get_coord(player, "coord_y")
        vision_radius = self.player_vision_radius + _get_sight_bonus(player)
        _add_chebyshev_circle(visible, px, py, vision_radius)

        # Building vision circles
        for building in player_buildings:
            bx, by = _get_building_coords(building)
            _add_chebyshev_circle(visible, bx, by, self.building_vision_radius)

        # Scout-agent vision circles (R5) — only active scouts project vision.
        if player_scouts and self.scout_vision_radius > 0:
            for scout in player_scouts:
                if not _is_scout_active(scout):
                    continue
                sx = _get_coord(scout, "coord_x")
                sy = _get_coord(scout, "coord_y")
                _add_chebyshev_circle(visible, sx, sy, self.scout_vision_radius)

        return visible

    def get_tile_visibility(
        self,
        player: Any,
        x: int,
        y: int,
        visible_tiles: set[tuple[int, int]],
    ) -> str:
        """Return 'visible', 'fog', or 'unexplored' for a tile."""
        if (x, y) in visible_tiles:
            return "visible"

        bitfield, _ = self._get_discovery_data(player)
        if (x, y) in bitfield:
            return "fog"

        return "unexplored"

    def update_discovery(
        self,
        player: Any,
        visible_tiles: set[tuple[int, int]],
        planet_room: Any = None,
        tile_resolver: Any = None,
    ) -> None:
        """Update the player's discovery memory for all currently visible tiles.

        Uses planet_room.get_buildings_at for building discovery.
        """
        bitfield, buildings_mem = self._get_discovery_data(player)

        player_key = player.key if hasattr(player, "key") else ""
        planet = _get_planet(player)

        # Batch-add all visible tiles to the bitfield
        tiles_changed = bitfield.add_many(visible_tiles)

        # Resolve planet_room from player if not provided
        # Handle case where a PlanetRoom was passed as tile_resolver (positional arg)
        if planet_room is None and tile_resolver is not None and hasattr(tile_resolver, "get_buildings_at"):
            planet_room = tile_resolver
            tile_resolver = None
        if planet_room is None:
            loc = getattr(player, "location", None)
            if loc is not None and hasattr(loc, "get_buildings_at"):
                planet_room = loc

        # Check for buildings on visible tiles
        buildings_changed = False
        for coord in visible_tiles:
            x, y = coord
            building = None

            # Use PlanetRoom coordinate query
            if planet_room is not None and hasattr(planet_room, "get_buildings_at"):
                buildings_at = planet_room.get_buildings_at(x, y)
                building = buildings_at[0] if buildings_at else None

            if building is not None:
                owner = _get_building_owner(building)
                owner_name = _owner_name(owner)

                # An allied member's building is treated like the player's own —
                # it is NOT recorded as an enemy discovery (shared-vision perk).
                from world.utils import are_allied
                allied = are_allied(player, owner)

                if owner is not player and owner_name != player_key and not allied:
                    btype = _get_building_type(building)
                    new_snap = {
                        "building_type": btype,
                        "owner_name": owner_name,
                        "x": x, "y": y,
                    }
                    if buildings_mem.get((x, y)) != new_snap:
                        buildings_mem[(x, y)] = new_snap
                        buildings_changed = True
                elif (x, y) in buildings_mem:
                    del buildings_mem[(x, y)]
                    buildings_changed = True
            elif (x, y) in buildings_mem:
                del buildings_mem[(x, y)]
                buildings_changed = True

        # Only persist if something actually changed
        if tiles_changed or buildings_changed:
            self._save_discovery_data(player, bitfield, buildings_mem)

    def get_discovered_buildings(
        self, player: Any, x: int, y: int
    ) -> list[DiscoveredBuildingState]:
        """Return last-known enemy building snapshots for a fog tile."""
        _, buildings_mem = self._get_discovery_data(player)
        entry = buildings_mem.get((x, y))
        if entry is None:
            return []
        return [
            DiscoveredBuildingState(
                building_type=entry.get("building_type", "??"),
                owner_name=entry.get("owner_name", "Unknown"),
                x=entry.get("x", x),
                y=entry.get("y", y),
            )
        ]

    def get_discovered_tile_set(self, player: Any) -> DiscoveryBitfield:
        """Return the bitfield of all discovered tiles.

        The returned object supports ``(x, y) in bitfield`` checks.
        """
        bitfield, _ = self._get_discovery_data(player)
        return bitfield

    def get_discovered_buildings_map(self, player: Any) -> dict:
        """Return the full buildings memory dict for the player."""
        _, buildings = self._get_discovery_data(player)
        return buildings

    # ------------------------------------------------------------------ #
    #  Discovery memory persistence (chunk-based bitfield)
    # ------------------------------------------------------------------ #

    def _get_discovery_data(self, player: Any) -> tuple[DiscoveryBitfield, dict]:
        """Return (bitfield, buildings_dict) from the player's discovery memory.

        Auto-migrates from the old set-based format if detected.
        """
        mem = None
        if hasattr(player, "db"):
            mem = player.db.discovery_memory
        if not mem or not hasattr(mem, "get"):
            return DiscoveryBitfield(), {}

        discovered_raw = mem.get("discovered")
        buildings_raw = mem.get("buildings")

        # Build the bitfield
        if isinstance(discovered_raw, dict) or (
            hasattr(discovered_raw, "keys") and not hasattr(discovered_raw, "add")
        ):
            # New format: dict of "cx,cy" -> int bitfield chunks
            bitfield = DiscoveryBitfield.from_dict(dict(discovered_raw))
        elif discovered_raw and hasattr(discovered_raw, "__iter__"):
            # Old format: set/list of (x, y) tuples — migrate
            bitfield = DiscoveryBitfield.from_set(set(discovered_raw))
            # Save back in new format so migration only happens once
            self._save_discovery_data(player, bitfield, dict(buildings_raw or {}))
        else:
            bitfield = DiscoveryBitfield()

        try:
            buildings = dict(buildings_raw or {})
        except (TypeError, ValueError):
            buildings = {}

        return bitfield, buildings

    def _save_discovery_data(self, player: Any, bitfield: DiscoveryBitfield, buildings: dict) -> None:
        """Persist discovery data using the bitfield format."""
        if hasattr(player, "db"):
            player.db.discovery_memory = {
                "discovered": bitfield.to_dict(),
                "buildings": buildings,
            }


# ------------------------------------------------------------------ #
#  Module-level helpers
# ------------------------------------------------------------------ #

def _add_chebyshev_circle(
    tiles: set[tuple[int, int]], cx: int, cy: int, radius: int
) -> None:
    """Add all tiles within Chebyshev distance *radius* of (cx, cy)."""
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            tiles.add((cx + dx, cy + dy))


def _get_sight_bonus(player: Any) -> int:
    """Return the player's aggregate ``sight_range`` bonus from equipped gear.

    Falls back to ``0`` when the player has no equipment handler (e.g.
    synthetic viewers or tests), so the vision radius stays at the base
    ``player_vision_radius``. The bonus stat may be a float, so it is
    coerced to ``int`` to keep the radius integral.
    """
    equipment = getattr(player, "equipment", None)
    if equipment is None or not hasattr(equipment, "get_stat_total"):
        return 0
    try:
        return int(equipment.get_stat_total("sight_range"))
    except (TypeError, ValueError):
        return 0


def _get_coord(obj: Any, attr: str) -> int:
    """Read a coordinate attribute from an object."""
    if hasattr(obj, "db"):
        val = getattr(obj.db, attr, 0)
        return val if val is not None else 0
    return getattr(obj, attr, 0) or 0


def _get_planet(player: Any) -> str:
    """Read the player's current planet key."""
    if hasattr(player, "db"):
        val = getattr(player.db, "coord_planet", "")
        return val if val is not None else ""
    return getattr(player, "coord_planet", "") or ""


def _is_scout_active(agent: Any) -> bool:
    """Return True if *agent* is an active scout that projects vision (R5.2).

    An active scout has role "scout", is not incapacitated, and is not
    benched in reserve. Value-based db reads only; never raises.
    """
    db = getattr(agent, "db", None)
    if db is None:
        return False
    if (getattr(db, "role", "") or "").lower() != "scout":
        return False
    if getattr(db, "incapacitated", False):
        return False
    if getattr(db, "reserve", False):
        return False
    return True


def _get_building_coords(building: Any) -> tuple[int, int]:
    """Extract (x, y) from a building's coordinate attributes."""
    from world.utils import get_coords
    coords = get_coords(building)
    if coords is not None:
        return coords
    # Last resort: bare x/y attributes.
    x = getattr(building, "x", 0) or 0
    y = getattr(building, "y", 0) or 0
    return (int(x), int(y))


def _get_building_owner(building: Any) -> Any | None:
    """Get the owner of a building."""
    from world.utils import get_obj_attr
    return get_obj_attr(building, "owner")


def _get_building_type(building: Any) -> str:
    """Get the building type abbreviation."""
    if hasattr(building, "get_display_abbreviation"):
        return building.get_display_abbreviation()
    from world.utils import get_building_type
    return get_building_type(building) or "??"


def _owner_name(owner: Any) -> str:
    """Get a display name from an owner object."""
    if owner is None:
        return "Unknown"
    if hasattr(owner, "key"):
        return owner.key
    return str(owner)
