"""
Fog of War System — RTS-style per-player visibility and discovery memory.

Manages three visibility states per tile:
- visible: within any vision source (player or owned building)
- fog: previously discovered but not currently visible
- unexplored: never seen

Vision is computed as the union of Chebyshev-distance circles around
the player position and each owned building.

Requirements: 5.4, 5.5, 5.6, 5.7, 5.9, 11.1–11.9
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from world.coordinate.discovery_bitfield import DiscoveryBitfield

if TYPE_CHECKING:
    from world.definitions import BalanceConfig
    from world.coordinate.tile_resolver import TileResolver


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
        self._map_border: int = getattr(balance, "map_border_tiles", 5)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def get_visible_tiles(
        self, player: Any, player_buildings: list[Any]
    ) -> set[tuple[int, int]]:
        """Compute the union of all vision circles for the player.

        Returns a set of (x, y) tuples that are currently visible.
        """
        visible: set[tuple[int, int]] = set()

        # Player vision circle
        px = _get_coord(player, "coord_x")
        py = _get_coord(player, "coord_y")
        _add_chebyshev_circle(visible, px, py, self.player_vision_radius)

        # Building vision circles
        for building in player_buildings:
            bx, by = _get_building_coords(building)
            _add_chebyshev_circle(visible, bx, by, self.building_vision_radius)

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
        tile_resolver: TileResolver,
    ) -> None:
        """Update the player's discovery memory for all currently visible tiles."""
        bitfield, buildings_mem = self._get_discovery_data(player)

        player_key = player.key if hasattr(player, "key") else ""
        planet = _get_planet(player)

        # Batch-add all visible tiles to the bitfield
        tiles_changed = bitfield.add_many(visible_tiles)

        # Check for buildings on cached tiles
        buildings_changed = False
        for coord in visible_tiles:
            x, y = coord
            room = tile_resolver.get_cached(x, y, planet)
            building = _get_room_building(room) if room is not None else None

            if building is not None:
                owner = _get_building_owner(building)
                owner_name = _owner_name(owner)

                if owner is not player and owner_name != player_key:
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


def _get_building_coords(building: Any) -> tuple[int, int]:
    """Extract (x, y) from a building's location."""
    loc = getattr(building, "location", None)
    if loc is not None:
        x = getattr(loc, "x", None)
        if x is not None:
            y = getattr(loc, "y", 0)
            return (int(x), int(y))
    # Fallback: building might store coords directly
    x = getattr(building, "x", 0) or 0
    y = getattr(building, "y", 0) or 0
    return (int(x), int(y))


def _get_room_building(room: Any) -> Any | None:
    """Get the building from a room, if any."""
    if hasattr(room, "building"):
        return room.building
    return None


def _get_building_owner(building: Any) -> Any | None:
    """Get the owner of a building."""
    # Try attributes system first (Evennia)
    if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
        owner = building.attributes.get("owner", default=None)
        if owner is not None:
            return owner
    # Fallback: db attribute
    if hasattr(building, "db"):
        return getattr(building.db, "owner", None)
    return getattr(building, "owner", None)


def _get_building_type(building: Any) -> str:
    """Get the building type abbreviation."""
    if hasattr(building, "get_display_abbreviation"):
        return building.get_display_abbreviation()
    if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
        btype = building.attributes.get("building_type", default=None)
        if btype:
            return str(btype)
    if hasattr(building, "db"):
        btype = getattr(building.db, "building_type", None)
        if btype:
            return str(btype)
    return "??"


def _owner_name(owner: Any) -> str:
    """Get a display name from an owner object."""
    if owner is None:
        return "Unknown"
    if hasattr(owner, "key"):
        return owner.key
    return str(owner)
