"""
World Chunk Manager for the RTS Combat Overworld game.

Divides the overworld into rectangular chunks for performance-optimized
tick processing. Only active chunks (near online players) are processed
each game tick.

"""

from __future__ import annotations

from typing import Any


class WorldChunkManager:
    """Manages world chunks for tick processing optimization.

    A chunk is a rectangular region of chunk_size x chunk_size tiles.
    Chunk coordinates are calculated as (x // chunk_size, y // chunk_size).

    A chunk is active if any online player is within it or within one
    chunk radius of it.

    Args:
        chunk_size: The side length of each chunk in tiles (default 10).
    """

    def __init__(self, chunk_size: int = 10) -> None:
        self.chunk_size = chunk_size

    def get_chunk_coord(self, x: int, y: int) -> tuple[int, int]:
        """Return the chunk coordinate for a tile at (x, y).

        Uses floor division so negative coordinates work correctly.

        Args:
            x: Tile x-coordinate.
            y: Tile y-coordinate.

        Returns:
            (chunk_x, chunk_y) tuple.
        """
        return (x // self.chunk_size, y // self.chunk_size)

    def get_active_chunks(
        self, planet: str, online_players: list[Any]
    ) -> set[tuple[int, int]]:
        """Determine which chunks are active based on online player positions.

        A chunk is active if a player is in it or within 1 chunk radius.
        This means for each player chunk (cx, cy), all chunks in the range
        (cx-1..cx+1, cy-1..cy+1) are activated.

        Args:
            planet: The planet name (used to filter players by planet).
            online_players: List of player objects with position info.

        Returns:
            Set of active (chunk_x, chunk_y) tuples.
        """
        active = set()
        for player in online_players:
            # Only players ON this planet activate its chunks — otherwise a
            # player on planet A would activate the same-numbered chunk on every
            # planet, defeating per-planet isolation.
            if not self._on_planet(player, planet):
                continue
            pos = self._get_player_position(player, planet)
            if pos is None:
                continue
            x, y = pos
            cx, cy = self.get_chunk_coord(x, y)
            # Activate the player's chunk and all neighbors (1 radius)
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    active.add((cx + dx, cy + dy))
        return active

    def get_buildings_in_chunks(
        self, planet: str, chunks: set[tuple[int, int]], all_buildings: list[Any]
    ) -> list[Any]:
        """Filter buildings to only those within the given active chunks.

        Args:
            planet: The planet name.
            chunks: Set of active chunk coordinates.
            all_buildings: List of building objects to filter.

        Returns:
            List of buildings that fall within active chunks.
        """
        result = []
        for building in all_buildings:
            # Only buildings ON this planet are eligible — the active-chunk set is
            # per-planet, and without this a building at (x,y) on planet A would
            # match an active chunk computed from planet B's players. It also
            # prevents the same building being appended once per planet by a
            # caller that loops get_buildings_in_chunks over multiple planets.
            if not self._on_planet(building, planet):
                continue
            pos = self._get_building_position(building)
            if pos is None:
                continue
            x, y = pos
            chunk = self.get_chunk_coord(x, y)
            if chunk in chunks:
                result.append(building)
        return result

    @staticmethod
    def _on_planet(obj: Any, planet: str) -> bool:
        """Return True if *obj* is on *planet*.

        Reads ``db.coord_planet`` (the real coordinate model). Objects with no
        recorded planet (lightweight test fakes that only carry a ``.position``)
        are treated as matching, so position-only tests keep working.
        """
        db = getattr(obj, "db", None)
        obj_planet = getattr(db, "coord_planet", None) if db is not None else None
        if not obj_planet:
            return True  # no planet recorded (test fake) — don't exclude
        return str(obj_planet) == str(planet)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_player_position(player: Any, planet: str) -> tuple[int, int] | None:
        """Extract (x, y) position from a player object.

        Supports multiple attribute patterns for flexibility in testing
        and production.
        """
        # Direct position attribute (used in tests / lightweight fakes)
        if hasattr(player, "position"):
            pos = player.position
            if pos is not None:
                return (pos[0], pos[1])

        # Real coordinate model: position lives on the entity's db
        # (coord_x/coord_y), NOT on the room. (Reading loc.x/loc.y off the
        # PlanetRoom always yielded None, so no chunk was ever active.)
        db = getattr(player, "db", None)
        if db is not None:
            x = getattr(db, "coord_x", None)
            y = getattr(db, "coord_y", None)
            if x is not None and y is not None:
                return (int(x), int(y))

        return None

    @staticmethod
    def _get_building_position(building: Any) -> tuple[int, int] | None:
        """Extract (x, y) position from a building object."""
        # Real coordinate model: a building's tile is on its db (coord_x/coord_y),
        # not on the PlanetRoom it lives in. (Reading loc.x/loc.y always gave
        # None, so buildings were never matched into any active chunk.)
        db = getattr(building, "db", None)
        if db is not None:
            x = getattr(db, "coord_x", None)
            y = getattr(db, "coord_y", None)
            if x is not None and y is not None:
                return (int(x), int(y))

        if hasattr(building, "position"):
            pos = building.position
            if pos is not None:
                return (pos[0], pos[1])

        return None
