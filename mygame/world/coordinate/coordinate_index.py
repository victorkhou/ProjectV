"""
Spatial index mapping (x, y) coordinate pairs to sets of objects.

Provides O(1) average-case lookup by coordinate for PlanetRoom contents.
Stored on PlanetRoom.ndb (non-persistent) and lazily rebuilt from room
contents on first access after a server restart or reload.
"""

from __future__ import annotations


class CoordinateIndex:
    """O(1) spatial index mapping (x, y) → set of objects.

    Stored on PlanetRoom.ndb (non-persistent). Lazily rebuilt from
    room contents on first access after restart.

    Future optimization note: if the index grows beyond ~10k keys,
    get_in_area() iterates all keys (O(n) on index size).
    A grid-of-buckets or quadtree can replace the flat dict if needed.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[int, int], set] = {}

    def add(self, obj, x: int, y: int) -> None:
        """Add *obj* to the bucket at *(x, y)*."""
        self._data.setdefault((x, y), set()).add(obj)

    def remove(self, obj, x: int, y: int) -> None:
        """Remove *obj* from the bucket at *(x, y)*.

        Silently ignores missing objects. Cleans up empty buckets.
        """
        bucket = self._data.get((x, y))
        if bucket:
            bucket.discard(obj)
            if not bucket:
                del self._data[(x, y)]

    def move(self, obj, old_x, old_y, new_x: int, new_y: int) -> None:
        """Move *obj* from *(old_x, old_y)* to *(new_x, new_y)*.

        If *old_x* or *old_y* is ``None`` the object is treated as newly
        placed (no removal from a previous bucket).
        """
        if old_x is not None and old_y is not None:
            self.remove(obj, int(old_x), int(old_y))
        self.add(obj, new_x, new_y)

    def get_at(self, x: int, y: int) -> list:
        """Return a list of objects at *(x, y)*."""
        return list(self._data.get((x, y), set()))

    def get_in_area(self, x1: int, y1: int, x2: int, y2: int) -> list:
        """Return all objects whose coordinates fall within the bounding box.

        The bounds are inclusive: ``x1 <= cx <= x2`` and ``y1 <= cy <= y2``.
        """
        result = []
        for (cx, cy), objs in self._data.items():
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                result.extend(objs)
        return result

    def clear(self) -> None:
        """Remove all entries from the index."""
        self._data.clear()

    def __len__(self) -> int:
        """Return the total number of indexed objects across all buckets."""
        return sum(len(s) for s in self._data.values())

    @classmethod
    def build_from_contents(cls, contents) -> "CoordinateIndex":
        """Build a new index from an iterable of objects.

        Each object is expected to expose ``db.coord_x`` and ``db.coord_y``.
        Objects without valid coordinates are silently skipped.
        """
        idx = cls()
        for obj in contents:
            cx = getattr(getattr(obj, "db", None), "coord_x", None)
            cy = getattr(getattr(obj, "db", None), "coord_y", None)
            if cx is not None and cy is not None:
                idx.add(obj, int(cx), int(cy))
        return idx
