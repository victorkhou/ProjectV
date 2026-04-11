"""
ASCII Map Renderer for the RTS Combat Overworld game.

Renders the overworld as a 2-char-per-tile ASCII grid centered on
the player's position, with Fog of War hiding enemy details outside
sight range.

Requirements: 1.7, 1.8, 1.9, 27.2, 27.4
"""

from __future__ import annotations

from typing import Any


class ASCIIMapRenderer:
    """Renders the overworld as a 2-char-per-tile ASCII grid.

    Display priority (Requirement 1.8):
        1. "@@" if the looker is on the tile
        2. "**" if another player is on the tile
        3. Building abbreviation if a building is present
        4. Terrain symbol

    Fog of War (Requirement 1.9):
        Tiles outside the looker's sight range show only terrain symbols,
        hiding enemy players and buildings.
    """

    def render(
        self,
        center: tuple[int, int],
        sight_range: int,
        planet: str,
        looker: Any,
        tile_lookup: dict[tuple[int, int], Any] | None = None,
    ) -> str:
        """Render the ASCII map centered on the looker.

        Args:
            center: (x, y) coordinates of the looker.
            sight_range: How many tiles the looker can see in each direction.
            planet: Planet name (for context).
            looker: The player character viewing the map.
            tile_lookup: Optional dict mapping (x, y) -> tile object.
                If not provided, tiles outside the lookup are rendered
                as empty space.

        Returns:
            A multi-line string representing the ASCII map.
        """
        if tile_lookup is None:
            tile_lookup = {}

        cx, cy = center
        lines = []

        # Render from top (highest y) to bottom (lowest y)
        for y in range(cy + sight_range, cy - sight_range - 1, -1):
            row = []
            for x in range(cx - sight_range, cx + sight_range + 1):
                tile = tile_lookup.get((x, y))
                if tile is None:
                    row.append("..")
                    continue

                dist = max(abs(x - cx), abs(y - cy))
                in_sight = dist <= sight_range

                symbol = self.get_tile_symbol(tile, looker, in_sight)
                row.append(symbol)

            lines.append(" ".join(row))

        return "\n".join(lines)

    def get_tile_symbol(
        self, tile: Any, looker: Any, in_sight: bool = True
    ) -> str:
        """Get the 2-character display symbol for a tile.

        Display priority:
            1. "@@" if looker is on this tile
            2. "**" if another player is on this tile
            3. Building abbreviation
            4. Terrain symbol

        Fog of War: if not in_sight, only terrain symbol is shown.

        Args:
            tile: The tile object.
            looker: The player viewing the map.
            in_sight: Whether the tile is within the looker's sight range.

        Returns:
            A 2-character string.
        """
        terrain_symbol = self._get_terrain_symbol(tile)

        if not in_sight:
            # Fog of War: only show terrain
            return terrain_symbol

        # Check for players on the tile
        players = self._get_players_on_tile(tile)
        for player in players:
            if self._is_same_entity(player, looker):
                return "@@"

        if players:
            return "**"

        # Check for building
        building = self._get_building_on_tile(tile)
        if building is not None:
            return self._get_building_symbol(building)

        return terrain_symbol

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_terrain_symbol(tile: Any) -> str:
        """Get the terrain symbol from a tile."""
        if hasattr(tile, "terrain_symbol"):
            return tile.terrain_symbol or ".."
        if hasattr(tile, "terrain_type"):
            # Map common terrain types to symbols
            terrain_map = {
                "Plains": "PP",
                "Dirt": "~~",
                "Forest": "FF",
                "Rock": "RR",
                "Mountain": "MT",
                "Power_Grid": "GG",
                "Scrapyard": "SS",
                "Circuit_Field": "CC",
                "Ruins": "UU",
            }
            return terrain_map.get(tile.terrain_type, "..")
        return ".."

    @staticmethod
    def _get_players_on_tile(tile: Any) -> list:
        """Get list of players on a tile."""
        if hasattr(tile, "players"):
            return list(tile.players)
        # Evennia: contents filtered by typeclass
        if hasattr(tile, "contents"):
            return [
                obj for obj in tile.contents
                if hasattr(obj, "is_player") and obj.is_player
            ]
        return []

    @staticmethod
    def _get_building_on_tile(tile: Any) -> Any | None:
        """Get the building on a tile, if any."""
        if hasattr(tile, "building"):
            return tile.building
        return None

    @staticmethod
    def _get_building_symbol(building: Any) -> str:
        """Get the 2-char abbreviation for a building."""
        if hasattr(building, "get_display_abbreviation"):
            return building.get_display_abbreviation()
        if hasattr(building, "abbreviation"):
            return building.abbreviation
        if hasattr(building, "key"):
            return building.key[:2].upper()
        return "??"

    @staticmethod
    def _is_same_entity(a: Any, b: Any) -> bool:
        """Check if two objects represent the same entity."""
        if a is b:
            return True
        if hasattr(a, "id") and hasattr(b, "id"):
            return a.id == b.id
        if hasattr(a, "key") and hasattr(b, "key"):
            return a.key == b.key
        return False
