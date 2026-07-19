"""
Map Data Provider — generates structured tile data for the graphical webclient.

Produces a JSON-serializable dict describing the visible map area,
including terrain types, buildings, players, fog state, and player
position. The webclient Canvas renderer consumes this data.

The provider reuses the same fog/visibility logic as the ASCII renderer
but outputs structured data instead of colored text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from world.coordinate.fog_of_war import _get_planet, _get_coord
from world.coordinate.tile_render import (
    building_is_occupied,
    partition_tile_objects,
)
from world.utils import get_system

if TYPE_CHECKING:
    from world.coordinate.fog_of_war import FogOfWarSystem
    from world.coordinate.terrain_generator import TerrainGenerator


def _alliance_tag(obj: Any) -> str | None:
    """Return *obj*'s alliance tag for the map payload, or ``None``.

    Best-effort via the AllianceSystem's ``tag_for``; ``None`` when there is no
    system or the player is not in an alliance. Never raises into map building.
    """
    try:
        system = get_system(obj, "alliance_system")
        if system is None:
            return None
        return system.tag_for(obj)
    except Exception:  # noqa: BLE001
        return None


class MapDataProvider:
    """Generates structured map data for the graphical webclient."""

    def __init__(
        self,
        fog_system: FogOfWarSystem,
        terrain_generators: dict[str, TerrainGenerator],
        tile_resolver: Any = None,
    ) -> None:
        self._fog_system = fog_system
        self._terrain_generators = terrain_generators

    def get_map_data(
        self,
        player: Any,
        player_buildings: list[Any],
    ) -> dict:
        """Return a JSON-serializable dict of the player's visible map.

        Returns:
            {
                "player": {"x": int, "y": int, "planet": str},
                "bounds": {"min_x": int, "max_x": int, "min_y": int, "max_y": int},
                "vision_radius": int,
                "tiles": [
                    {"x": int, "y": int, "terrain": str, "state": str,
                     "building": {...} | null,
                     "players": [{"name": str, "linkdead": bool}, ...]}
                ]
            }

        Tile states: "visible", "fog", "unexplored"
        """
        planet = _get_planet(player)
        px = _get_coord(player, "coord_x")
        py = _get_coord(player, "coord_y")
        pvr = self._fog_system.player_vision_radius
        border = getattr(self._fog_system, "_map_border", 5)

        # Compute visibility (unioned with PLAYING allies' if the shared-vision
        # perk is active — via the one shared helper all three fog callers use).
        from world.utils import shared_visible_tiles
        visible_tiles = shared_visible_tiles(player, player_buildings, self._fog_system)

        # Bulk query all objects in the viewport from PlanetRoom
        planet_room = getattr(player, "location", None)

        self._fog_system.update_discovery(player, visible_tiles, planet_room=planet_room)
        discovered = self._fog_system.get_discovered_tile_set(player)
        buildings_mem = self._fog_system.get_discovered_buildings_map(player)

        # Bounds
        min_x = px - pvr - border
        max_x = px + pvr + border
        min_y = py - pvr - border
        max_y = py + pvr + border

        objects_by_coord: dict[tuple[int, int], list] = {}
        if planet_room is not None and hasattr(planet_room, "get_objects_in_area"):
            for obj in planet_room.get_objects_in_area(min_x, min_y, max_x, max_y):
                cx = getattr(getattr(obj, "db", None), "coord_x", None)
                cy = getattr(getattr(obj, "db", None), "coord_y", None)
                if cx is not None and cy is not None:
                    objects_by_coord.setdefault((int(cx), int(cy)), []).append(obj)

        gen = self._terrain_generators.get(planet)
        tiles = []

        for y in range(max_y, min_y - 1, -1):
            for x in range(min_x, max_x + 1):
                coord = (x, y)
                terrain = gen.get_terrain(x, y) if gen else "unknown"

                # Out-of-bounds tiles (beyond the planet edge) are not part of
                # the world — always fog, checked FIRST so an edge tile inside
                # the (unclamped) vision circle still fogs. The out_of_bounds
                # flag lets the Canvas draw the map edge distinctly.
                if not self._fog_system.is_in_bounds(planet, x, y):
                    tile_data = {"x": x, "y": y, "terrain": terrain,
                                 "state": "fog", "out_of_bounds": True}
                elif coord in visible_tiles:
                    tile_data = self._visible_tile_from_objects(
                        x, y, terrain, player, objects_by_coord.get(coord)
                    )
                elif coord in discovered:
                    tile_data = self._fog_tile(x, y, terrain, buildings_mem)
                else:
                    tile_data = {"x": x, "y": y, "terrain": terrain, "state": "unexplored"}

                tiles.append(tile_data)

        return {
            "player": {"x": px, "y": py, "planet": planet},
            "bounds": {
                "min_x": min_x, "max_x": max_x,
                "min_y": min_y, "max_y": max_y,
            },
            "vision_radius": pvr,
            "tiles": tiles,
        }

    def _visible_tile_from_objects(self, x, y, terrain, player, tile_objects):
        """Build tile data for a visible tile from coordinate-grouped objects."""
        tile = {"x": x, "y": y, "terrain": terrain, "state": "visible"}
        player_id = getattr(player, "id", None)

        if not tile_objects:
            return tile

        from world.player_lifecycle import get_state as _get_state
        from world.constants import PLAYER_STATE_LINKDEAD

        occ = partition_tile_objects(tile_objects, player)

        # Present players other than the looker (incl. linkdead — still on the
        # tile during grace). Carry each one's linkdead flag so the client can
        # draw the linkdead variant instead of a live enemy, plus the alliance
        # tag (friend or foe) for shared-side identity.
        players_here = [
            {
                "name": getattr(obj, "key", "?"),
                "linkdead": _get_state(obj) == PLAYER_STATE_LINKDEAD,
                "tag": _alliance_tag(obj),
            }
            for obj in occ.other_players
        ]
        # NPC agents: keep only the own/role fields the client needs.
        agents_here = [{"own": own, "role": role} for _obj, own, role in occ.agents]
        building_obj = occ.building

        if building_obj is not None:
            bld = building_obj
            owner = None
            if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                owner = bld.attributes.get("owner", default=None)
            owner_id = getattr(owner, "id", None) if owner else None
            tile["building"] = {
                "type": bld.attributes.get("building_type", default="??") if hasattr(bld, "attributes") else "??",
                "level": bld.attributes.get("building_level", default=1) if hasattr(bld, "attributes") else 1,
                "own": owner_id is not None and owner_id == player_id,
                "name": getattr(bld, "key", "?"),
            }

            # Check if building has entities inside (occupied flag)
            tile["building"]["occupied"] = building_is_occupied(bld)

            # Shield state (Shield Generator feature): a building under a shield
            # carries a second HP bar. Emit shield/shield_max so the client can
            # draw a shield gauge; omitted (0) for unshielded buildings.
            if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                shield_max = int(bld.attributes.get("shield_max", default=0) or 0)
                if shield_max > 0:
                    tile["building"]["shield"] = int(bld.attributes.get("shield", default=0) or 0)
                    tile["building"]["shield_max"] = shield_max

        if players_here:
            tile["players"] = players_here
        if agents_here:
            tile["agents"] = agents_here

        return tile

    def _fog_tile(self, x, y, terrain, buildings_mem):
        """Build tile data for a fog-of-war tile."""
        tile = {"x": x, "y": y, "terrain": terrain, "state": "fog"}
        entry = buildings_mem.get((x, y))
        if entry:
            tile["building"] = {
                "type": entry.get("building_type", "??"),
                "own": False,
            }
        return tile
