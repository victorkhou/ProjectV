"""
Procedural Map Renderer — ASCII map with RTS fog of war.

Renders the overworld as a 2-char-per-tile ASCII grid using procedural
terrain data.  Tiles that have no On_Demand_Room are rendered directly
from the TerrainGenerator — no room creation for rendering.

Three visibility states:
- visible: full state (players, buildings, terrain)
- fog: terrain + discovered buildings from memory (no enemy players)
- unexplored: terrain only

Color scheme (Evennia |X codes):
- Player (self): |Y yellow
- Enemy players: |r red
- Buildings (own): |c cyan
- Buildings (enemy/fog): |R dark red
- Terrain: per-type color
- Fog tiles: |x dark grey overlay

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from world.coordinate.fog_of_war import _get_planet, _get_coord

if TYPE_CHECKING:
    from world.coordinate.fog_of_war import FogOfWarSystem
    from world.coordinate.terrain_generator import TerrainGenerator
    from world.coordinate.tile_resolver import TileResolver

# Terrain type -> Evennia color code
_TERRAIN_COLORS: dict[str, str] = {
    # Earth
    "Plains":        "|g",   # green
    "Dirt":           "|y",   # yellow
    "Forest":        "|G",   # dark green
    "Rock":          "|w",   # white
    "Mountain":      "|W",   # bright white
    # Industrial
    "Power_Grid":    "|Y",   # bright yellow
    "Scrapyard":     "|y",   # yellow
    "Circuit_Field": "|c",   # cyan
    "Ruins":         "|x",   # dark grey
    # Space
    "Void":          "|X",   # dark grey (near black)
    "Nebula":        "|m",   # magenta
    "Asteroid":      "|w",   # white
    "Debris":        "|y",   # yellow
    "Ice_Field":     "|C",   # bright cyan
}

_FOG_COLOR = "|x"  # grey for fog-of-war tiles
_UNEXPLORED = "|X..|n"  # near-black dots for never-seen tiles


class ProceduralMapRenderer:
    """Renders ASCII map from procedural terrain with RTS fog of war."""

    def __init__(
        self,
        tile_resolver: TileResolver,
        fog_system: FogOfWarSystem,
        terrain_generators: dict[str, TerrainGenerator],
        data_registry: Any = None,
    ) -> None:
        self._tile_resolver = tile_resolver
        self._fog_system = fog_system
        self._terrain_generators = terrain_generators
        self._data_registry = data_registry
        # Pre-build terrain_type -> 2-char symbol map
        self._symbol_cache: dict[str, str] = {}
        reg = data_registry
        if reg is None:
            try:
                from server.conf.game_init import game_systems
                reg = game_systems.get("registry")
                self._data_registry = reg
            except (ImportError, AttributeError):
                pass
        if reg is not None:
            for gen in terrain_generators.values():
                for _, terrain_type in gen._terrain_thresholds:
                    if terrain_type not in self._symbol_cache:
                        try:
                            tdef = reg.get_terrain(terrain_type)
                            self._symbol_cache[terrain_type] = tdef.map_symbol
                        except Exception:
                            self._symbol_cache[terrain_type] = terrain_type[:2] if len(terrain_type) >= 2 else terrain_type.ljust(2, "?")

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def render(
        self,
        player: Any,
        player_buildings: list[Any],
    ) -> str:
        """Render the full colored ASCII map for a player.

        The map is centered on the player's vision range. Tiles are:
        - Visible (in vision): full color terrain/players/buildings
        - Fog (previously discovered, outside vision): dimmed terrain
        - Unexplored (never seen): dark empty
        """
        planet = _get_planet(player)

        # 1. Compute visible tiles
        visible_tiles = self._fog_system.get_visible_tiles(player, player_buildings)

        # 2. Update discovery memory
        self._fog_system.update_discovery(
            player, visible_tiles, self._tile_resolver
        )

        # 3. Render bounds anchored to PLAYER position only (not buildings)
        if not visible_tiles:
            return ""

        discovered = self._fog_system.get_discovered_tile_set(player)

        # Bounds from player vision radius, not building vision
        px = _get_coord(player, "coord_x")
        py = _get_coord(player, "coord_y")
        pvr = self._fog_system.player_vision_radius
        _BORDER = getattr(self._fog_system, '_map_border', 5)
        min_x = px - pvr - _BORDER
        max_x = px + pvr + _BORDER
        min_y = py - pvr - _BORDER
        max_y = py + pvr + _BORDER

        # Pre-fetch discovery memory once
        buildings_mem = self._fog_system.get_discovered_buildings_map(player)

        # Preload all rooms in the visible area into cache (single DB query)
        self._tile_resolver.preload_area(min_x, max_x, min_y, max_y, planet)

        # 4. Render — inlined visibility check for speed
        get_cached = self._tile_resolver.get_cached
        player_coord = (px, py)
        lines: list[str] = []
        for y in range(max_y, min_y - 1, -1):
            row: list[str] = []
            for x in range(min_x, max_x + 1):
                coord = (x, y)
                if coord in visible_tiles:
                    # Check if the player is on this tile
                    if coord == player_coord:
                        sym = "|Y@@|n"
                    else:
                        room = get_cached(x, y, planet)
                        if room is not None:
                            sym = self._colored_room(room, player, x, y, planet)
                        else:
                            sym = self._colored_terrain(x, y, planet)
                elif coord in discovered:
                    # Fog — dimmed but recognizable terrain
                    entry = buildings_mem.get(coord)
                    if entry:
                        abbr = entry.get("building_type", "??")
                        raw = abbr[:2] if len(abbr) >= 2 else abbr.ljust(2, "?")
                        sym = f"|R{raw}|n"
                    else:
                        sym = self._fog_terrain(x, y, planet)
                else:
                    # Unexplored — faint dashes
                    sym = _UNEXPLORED
                row.append(sym)
            lines.append(" ".join(row))

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Tile symbol resolution (used by tests)
    # ------------------------------------------------------------------ #

    def _get_tile_symbol(
        self, x: int, y: int, planet: str,
        visibility: str, player: Any,
        visible_tiles: set[tuple[int, int]],
    ) -> str:
        """Get the 2-char symbol for a tile (uncolored, for tests)."""
        if visibility == "visible":
            return self._symbol_visible(x, y, planet, player)
        elif visibility == "fog":
            return self._symbol_fog(x, y, planet, player)
        return self._terrain_symbol(x, y, planet)

    def _symbol_visible(self, x: int, y: int, planet: str, player: Any) -> str:
        room = self._tile_resolver.get_cached(x, y, planet)
        if room is not None:
            return _room_display_symbol(room, player)
        return self._terrain_symbol(x, y, planet)

    def _symbol_fog(self, x: int, y: int, planet: str, player: Any) -> str:
        terrain_sym = self._terrain_symbol(x, y, planet)
        buildings = self._fog_system.get_discovered_buildings(player, x, y)
        if buildings:
            abbr = buildings[0].building_type
            return abbr[:2] if len(abbr) >= 2 else abbr.ljust(2, "?")
        return terrain_sym

    # ------------------------------------------------------------------ #
    #  Terrain + color helpers
    # ------------------------------------------------------------------ #

    def _terrain_symbol(self, x: int, y: int, planet: str) -> str:
        """Get the raw 2-char terrain symbol (no color)."""
        gen = self._terrain_generators.get(planet)
        if gen is None:
            return ".."
        terrain_type = gen.get_terrain(x, y)
        sym = self._symbol_cache.get(terrain_type)
        if sym:
            return sym
        sym = self._lookup_symbol(terrain_type)
        self._symbol_cache[terrain_type] = sym
        return sym

    def _lookup_symbol(self, terrain_type: str) -> str:
        """Look up the map symbol for a terrain type from the registry."""
        reg = self._data_registry
        if reg is not None:
            try:
                tdef = reg.get_terrain(terrain_type)
                return tdef.map_symbol
            except Exception:
                pass
        return terrain_type[:2] if len(terrain_type) >= 2 else terrain_type.ljust(2, "?")

    def _colored_terrain(self, x: int, y: int, planet: str) -> str:
        """Get the colored terrain symbol for a visible tile."""
        gen = self._terrain_generators.get(planet)
        if gen is None:
            return ".."
        terrain_type = gen.get_terrain(x, y)
        sym = self._symbol_cache.get(terrain_type)
        if not sym:
            sym = self._lookup_symbol(terrain_type)
            self._symbol_cache[terrain_type] = sym
        color = _TERRAIN_COLORS.get(terrain_type, "|w")
        return f"{color}{sym}|n"

    def _fog_terrain(self, x: int, y: int, planet: str) -> str:
        """Get the dimmed terrain symbol for a fog/unexplored tile."""
        sym = self._terrain_symbol(x, y, planet)
        return f"{_FOG_COLOR}{sym}|n"

    def _colored_room(self, room: Any, looker: Any, x: int, y: int, planet: str) -> str:
        """Get the colored symbol for a room tile.

        Uses the room only for building detection.
        For player detection, checks coordinates to handle the shared
        PlanetRoom where ALL players are in contents.
        Terrain symbol always comes from the generator + symbol cache
        so it stays in sync with the YAML definitions.
        """
        # Check for player characters by coordinate match
        contents = getattr(room, "contents", [])
        for obj in contents:
            if hasattr(obj, "has_account") and obj.has_account:
                # Filter by coordinates — in a PlanetRoom, all players
                # are in contents, so we must check position
                ox = _get_coord(obj, "coord_x") if hasattr(obj, "db") else None
                oy = _get_coord(obj, "coord_y") if hasattr(obj, "db") else None
                if ox == x and oy == y:
                    if obj is looker:
                        return "|Y@@|n"
                    return "|r**|n"

        # Check for building
        bld = getattr(room, "building", None)
        if bld is not None:
            if hasattr(bld, "get_display_abbreviation"):
                abbr = bld.get_display_abbreviation()
            else:
                abbr = "??"
                if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                    bt = bld.attributes.get("building_type", default=None)
                    if bt:
                        abbr = str(bt)[:2]
            owner = None
            if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                owner = bld.attributes.get("owner", default=None)
            if owner is looker:
                return f"|c{abbr}|n"
            return f"|R{abbr}|n"

        # Terrain — always from generator, not from room
        return self._colored_terrain(x, y, planet)


# ------------------------------------------------------------------ #
#  Module-level helpers
# ------------------------------------------------------------------ #


def _room_display_symbol(room: Any, looker: Any) -> str:
    """Get the raw display symbol from a room (no color).

    Used by the _get_tile_symbol / _symbol_visible test paths.
    """
    if hasattr(room, "get_display_symbol"):
        return room.get_display_symbol(looker)
    return ".."
