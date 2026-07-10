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

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from world.coordinate.fog_of_war import _get_planet, _get_coord

if TYPE_CHECKING:
    from world.coordinate.fog_of_war import FogOfWarSystem
    from world.coordinate.terrain_generator import TerrainGenerator

# Terrain type -> Evennia color code
_TERRAIN_COLORS: dict[str, str] = {
    # Terra (earth)
    "Plains":        "|g",   # green
    "Forest":        "|G",   # dark green
    "Dirt":          "|y",   # yellow
    "Rock":          "|w",   # white
    "Mountain":      "|W",   # bright white
    "River":         "|c",   # cyan
    "Sand":          "|Y",   # bright yellow
    "Snow":          "|W",   # bright white
    # Forge (industrial)
    "Power_Grid":    "|Y",   # bright yellow
    "Scrapyard":     "|y",   # yellow
    "Circuit_Field": "|c",   # cyan
    "Factory_Floor": "|w",   # white
    "Ruins":         "|x",   # dark grey
    "Toxic_Waste":   "|R",   # dark red
    "Pipeline":      "|w",   # white
    "Warehouse":     "|w",   # white
    # Tundra (frozen)
    "Snowfield":     "|W",   # bright white
    "Frozen_Lake":   "|C",   # bright cyan
    "Pine_Forest":   "|G",   # dark green
    "Ice_Cave":      "|C",   # bright cyan
    "Permafrost":    "|w",   # white
    "Glacier":       "|W",   # bright white
    "Hot_Spring":    "|Y",   # bright yellow
    "Tundra_Moss":   "|g",   # green
    # Inferno (volcanic)
    "Ash_Wastes":    "|x",   # dark grey
    "Lava_Flow":     "|R",   # dark red
    "Obsidian_Plain":"|w",   # white
    "Magma_Vent":    "|r",   # red
    "Scorched_Rock": "|y",   # yellow
    "Sulfur_Pit":    "|Y",   # bright yellow
    "Ember_Field":   "|R",   # dark red
    "Basalt_Ridge":  "|w",   # white
    # Citadel (fortress)
    "Corridor":      "|w",   # white
    "Vault_Room":    "|m",   # magenta
    "Armory_Ruin":   "|y",   # yellow
    "Control_Room":  "|c",   # cyan
    "Open_Chamber":  "|w",   # white
    "Blast_Door":    "|W",   # bright white
    "Generator_Room":"|Y",   # bright yellow
    "Barracks_Ruin": "|x",   # dark grey
    # Space
    "Void":          "|X",   # dark grey (near black)
    "Nebula":        "|m",   # magenta
    "Asteroid":      "|w",   # white
    "Debris":        "|y",   # yellow
    "Ice_Field":     "|C",   # bright cyan
    "Wormhole":      "|m",   # magenta
    "Radiation_Zone":"|R",   # dark red
    "Derelict_Ship": "|x",   # dark grey
}

_FOG_COLOR = "|x"  # grey for fog-of-war tiles
_UNEXPLORED = "|X..|n"  # near-black dots for never-seen tiles


class ProceduralMapRenderer:
    """Renders ASCII map from procedural terrain with RTS fog of war."""

    def __init__(
        self,
        fog_system: FogOfWarSystem,
        terrain_generators: dict[str, TerrainGenerator],
        data_registry: Any = None,
        tile_resolver: Any = None,
    ) -> None:
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
            player, visible_tiles,
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

        # Bulk query all objects in the viewport from PlanetRoom
        planet_room = getattr(player, "location", None)
        objects_by_coord: dict[tuple[int, int], list] = {}
        if planet_room is not None and hasattr(planet_room, "get_objects_in_area"):
            for obj in planet_room.get_objects_in_area(min_x, min_y, max_x, max_y):
                cx = getattr(getattr(obj, "db", None), "coord_x", None)
                cy = getattr(getattr(obj, "db", None), "coord_y", None)
                if cx is not None and cy is not None:
                    objects_by_coord.setdefault((int(cx), int(cy)), []).append(obj)

        # 4. Render — inlined visibility check for speed
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
                        tile_objects = objects_by_coord.get(coord)
                        if tile_objects:
                            sym = self._colored_objects(tile_objects, player, x, y, planet)
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
        # Try coordinate-based lookup first (PlanetRoom)
        planet_room = getattr(player, "location", None)
        if planet_room is not None and hasattr(planet_room, "get_objects_at"):
            objs = planet_room.get_objects_at(x, y)
            if objs:
                for obj in objs:
                    if hasattr(obj, "has_account") and obj.has_account:
                        if obj is player:
                            return "@@"
                        return "**"
                # Check for building
                for obj in objs:
                    if hasattr(obj, "tags") and obj.tags.get("building", category="object_type"):
                        if hasattr(obj, "get_display_abbreviation"):
                            return obj.get_display_abbreviation()
                        abbr = obj.attributes.get("building_type", default=None) if hasattr(obj, "attributes") else None
                        if abbr:
                            return str(abbr)[:2]
                        return "??"
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

    def _colored_objects(self, objects: list[Any], looker: Any, x: int, y: int, planet: str) -> str:
        """Get the colored symbol for a tile from its object list.

        Display priority (highest to lowest):
        1. Player self -> |Y@@|n (yellow)
        2. Enemy player -> |r**|n (red)
        3. Own agent (overworld) -> |g{sym}|n (green)
        4. Enemy agent (overworld) -> |r{sym}|n (red)
        5. Neutral NPC -> |y{sym}|n (yellow)
        6. Occupied building (entity inside) -> |B{abbr}|n (dark blue)
        7. Unoccupied own building -> |c{abbr}|n (cyan)
        8. Unoccupied enemy building -> |R{abbr}|n (dark red)
        9. Terrain symbol
        """
        looker_id = getattr(looker, "id", None)

        own_agent = None
        enemy_agent = None
        neutral_npc = None
        building_obj = None

        for obj in objects:
            # Player characters
            if hasattr(obj, "has_account") and obj.has_account:
                if obj is looker:
                    return "|Y@@|n"
                return "|r**|n"

            # NPC objects with npc_type tag
            if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
                npc_owner = getattr(obj.db, "owner", None) if hasattr(obj, "db") else None
                npc_owner_id = getattr(npc_owner, "id", None) if npc_owner else None
                if npc_owner_id is not None and npc_owner_id == looker_id:
                    if own_agent is None:
                        own_agent = obj
                elif npc_owner_id is not None:
                    if enemy_agent is None:
                        enemy_agent = obj
                else:
                    if neutral_npc is None:
                        neutral_npc = obj
                continue

            # Building objects
            if hasattr(obj, "tags") and obj.tags.get("building", category="object_type"):
                if building_obj is None:
                    building_obj = obj

        # Overworld NPCs — priority: own > enemy > neutral
        if own_agent is not None:
            role = getattr(own_agent.db, "role", "") if hasattr(own_agent, "db") else ""
            sym = _agent_symbol(role)
            return f"|g{sym}|n"
        if enemy_agent is not None:
            return f"|r{_agent_symbol('')}|n"
        if neutral_npc is not None:
            return f"|y{_agent_symbol('')}|n"

        # Building
        if building_obj is not None:
            bld = building_obj
            if hasattr(bld, "get_display_abbreviation"):
                abbr = bld.get_display_abbreviation()
            else:
                abbr = "??"
                if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                    bt = bld.attributes.get("building_type", default=None)
                    if bt:
                        abbr = str(bt)[:2]

            # Check if building has entities inside (occupied)
            bld_contents = getattr(bld, "contents", [])
            has_entity_inside = False
            for obj in bld_contents:
                if hasattr(obj, "has_account") and obj.has_account:
                    has_entity_inside = True
                    break
                if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
                    has_entity_inside = True
                    break

            if has_entity_inside:
                return f"|B{abbr}|n"

            owner = None
            if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                owner = bld.attributes.get("owner", default=None)
            if owner is looker:
                return f"|c{abbr}|n"
            return f"|R{abbr}|n"

        # Terrain — always from generator
        return self._colored_terrain(x, y, planet)


# ------------------------------------------------------------------ #
#  Module-level helpers
# ------------------------------------------------------------------ #

# Role -> 2-char abbreviation for agent map symbols
_ROLE_SYMBOLS: dict[str, str] = {
    "harvester": "ha",
    "engineer":  "en",
    "soldier":   "so",
    "guard":     "gu",
    "scout":     "sc",
    "medic":     "me",
}


def _agent_symbol(role: str) -> str:
    """Return the 2-char map symbol for an agent role."""
    return _ROLE_SYMBOLS.get(role, "ag")


def _room_display_symbol(room: Any, looker: Any) -> str:
    """Get the raw display symbol from a room (no color).

    Used by the _get_tile_symbol / _symbol_visible test paths.
    """
    if hasattr(room, "get_display_symbol"):
        return room.get_display_symbol(looker)
    return ".."
