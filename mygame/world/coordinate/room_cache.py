"""
LRU cache for recently accessed OverworldRoom objects.

Maps (x, y, planet) coordinate tuples to room instances, avoiding
repeated database queries.  Uses ``collections.OrderedDict`` for O(1)
LRU eviction.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class RoomCache:
    """LRU cache mapping (x, y, planet) -> OverworldRoom.

    The *room* value is typed as ``Any`` to avoid circular imports
    (OverworldRoom lives in typeclasses).
    """

    def __init__(self, max_size: int = 1000) -> None:
        """Initialise the cache.

        Args:
            max_size: Maximum number of entries before LRU eviction
                      kicks in.  Read from ``balance.room_cache_max_size``
                      at startup.
        """
        self._max_size = max(max_size, 1)
        self._data: OrderedDict[tuple[int, int, str], Any] = OrderedDict()

    # -- public API ------------------------------------------------ #

    def get(self, x: int, y: int, planet: str) -> Any | None:
        """Return the cached room for *(x, y, planet)*, or ``None``.

        Accessing an entry moves it to the *most-recently-used* end.
        """
        key = (x, y, planet)
        try:
            self._data.move_to_end(key)
            return self._data[key]
        except KeyError:
            return None

    def put(self, x: int, y: int, planet: str, room: Any) -> None:
        """Store *room* under *(x, y, planet)*.

        If the key already exists it is updated and moved to the MRU
        end.  If the cache exceeds *max_size* the least-recently-used
        entry is evicted.
        """
        key = (x, y, planet)
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = room
        else:
            self._data[key] = room
            if len(self._data) > self._max_size:
                self._data.popitem(last=False)  # evict LRU

    def remove(self, x: int, y: int, planet: str) -> None:
        """Remove the entry for *(x, y, planet)* if it exists."""
        self._data.pop((x, y, planet), None)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._data.clear()

    @property
    def size(self) -> int:
        """Return the current number of cached entries."""
        return len(self._data)
