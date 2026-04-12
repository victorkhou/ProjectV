"""
Chunk-based bitfield for tracking discovered tiles.

Each 16x16 chunk of the map is stored as a single integer (256 bits).
This reduces memory from ~50 bytes per tile (set of tuples) to ~0.1
bytes per tile (one bit per tile + dict overhead per chunk).

A player who has explored the entire 100x100 earth map uses ~1.2KB
instead of ~500KB.
"""

from __future__ import annotations

_CHUNK_SIZE = 16  # tiles per chunk side
_CHUNK_BITS = _CHUNK_SIZE * _CHUNK_SIZE  # 256 bits per chunk


class DiscoveryBitfield:
    """Compact storage for discovered tile coordinates.

    Internally stores a dict of {(chunk_x, chunk_y): int} where each
    int is a 256-bit bitfield representing a 16x16 tile chunk.
    """

    __slots__ = ("_chunks",)

    def __init__(self, chunks: dict | None = None) -> None:
        self._chunks: dict[tuple[int, int], int] = dict(chunks) if chunks else {}

    def add(self, x: int, y: int) -> None:
        """Mark tile (x, y) as discovered."""
        cx, cy = x >> 4, y >> 4  # x // 16, y // 16
        bit = ((x & 0xF) << 4) | (y & 0xF)  # (x % 16) * 16 + (y % 16)
        key = (cx, cy)
        self._chunks[key] = self._chunks.get(key, 0) | (1 << bit)

    def __contains__(self, coord: tuple[int, int]) -> bool:
        """Check if tile (x, y) has been discovered."""
        x, y = coord
        cx, cy = x >> 4, y >> 4
        bit = ((x & 0xF) << 4) | (y & 0xF)
        return bool(self._chunks.get((cx, cy), 0) & (1 << bit))

    def add_many(self, coords: set[tuple[int, int]] | list[tuple[int, int]]) -> None:
        """Mark multiple tiles as discovered (batch operation)."""
        chunks = self._chunks
        for x, y in coords:
            cx, cy = x >> 4, y >> 4
            bit = ((x & 0xF) << 4) | (y & 0xF)
            key = (cx, cy)
            chunks[key] = chunks.get(key, 0) | (1 << bit)

    def __len__(self) -> int:
        """Return the total number of discovered tiles."""
        return sum(bin(v).count("1") for v in self._chunks.values())

    def to_dict(self) -> dict:
        """Serialize to a plain dict for Evennia persistence.

        Keys are stored as strings ("cx,cy") because Evennia's
        serializer handles string keys better than tuple keys.
        """
        return {f"{cx},{cy}": v for (cx, cy), v in self._chunks.items()}

    @classmethod
    def from_dict(cls, data: dict) -> DiscoveryBitfield:
        """Deserialize from the dict format stored in Evennia."""
        chunks = {}
        if data:
            for key, val in data.items():
                if isinstance(key, str) and "," in key:
                    parts = key.split(",", 1)
                    try:
                        chunks[(int(parts[0]), int(parts[1]))] = int(val)
                    except (ValueError, TypeError):
                        pass
                elif isinstance(key, tuple) and len(key) == 2:
                    chunks[key] = int(val)
        return cls(chunks)

    @classmethod
    def from_set(cls, tile_set: set[tuple[int, int]]) -> DiscoveryBitfield:
        """Convert an old-style set of (x,y) tuples to a bitfield."""
        bf = cls()
        bf.add_many(tile_set)
        return bf
