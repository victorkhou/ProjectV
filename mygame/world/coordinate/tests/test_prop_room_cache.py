"""
Property-based tests for RoomCache.

Property 5: Room cache round-trip — for any coordinate (x, y, planet)
and any OverworldRoom, storing the room in the Room_Cache via put and
then retrieving it via get SHALL return the same room object. For any
coordinate not in the cache, get SHALL return None.

Property 6: Room cache LRU eviction respects max size — for any
sequence of put operations on a Room_Cache with max_size N, the cache
size SHALL never exceed N. When the cache is full and a new entry is
added, the least-recently-used entry SHALL be evicted.

**Validates: Requirements 2.2, 4.1, 4.2**
"""

import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.coordinate.room_cache import RoomCache


# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

class _FakeRoom:
    """Lightweight stand-in for OverworldRoom."""

    def __init__(self, label: str = "room") -> None:
        self.label = label

    def __repr__(self) -> str:
        return f"FakeRoom({self.label!r})"


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

coordinate_strategy = st.tuples(
    st.integers(min_value=-10_000, max_value=10_000),
    st.integers(min_value=-10_000, max_value=10_000),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=10,
    ),
)

max_size_strategy = st.integers(min_value=1, max_value=50)


# -------------------------------------------------------------- #
#  Property 5: Room cache round-trip
#  **Validates: Requirements 2.2, 4.1**
# -------------------------------------------------------------- #

class TestProperty5CacheRoundTrip(unittest.TestCase):
    """Property 5: Room cache round-trip.

    For any coordinate (x, y, planet) and any OverworldRoom, storing
    the room in the Room_Cache via put and then retrieving it via get
    SHALL return the same room object. For any coordinate not in the
    cache, get SHALL return None.

    **Validates: Requirements 2.2, 4.1**
    """

    @given(coord=coordinate_strategy)
    @settings(max_examples=200)
    def test_put_then_get_returns_same_object(self, coord):
        """put(x, y, planet, room) followed by get(x, y, planet) returns the same room object."""
        x, y, planet = coord
        cache = RoomCache(max_size=100)
        room = _FakeRoom(f"{x},{y},{planet}")
        cache.put(x, y, planet, room)
        retrieved = cache.get(x, y, planet)
        self.assertIs(
            retrieved,
            room,
            f"get({x}, {y}, {planet!r}) did not return the same object that was put",
        )

    @given(coord=coordinate_strategy)
    @settings(max_examples=200)
    def test_get_missing_returns_none(self, coord):
        """get on a coordinate that was never put SHALL return None."""
        x, y, planet = coord
        cache = RoomCache(max_size=100)
        result = cache.get(x, y, planet)
        self.assertIsNone(
            result,
            f"get({x}, {y}, {planet!r}) on empty cache returned {result!r} instead of None",
        )

    @given(
        coords=st.lists(coordinate_strategy, min_size=1, max_size=20, unique=True),
    )
    @settings(max_examples=200)
    def test_multiple_entries_round_trip(self, coords):
        """Multiple distinct entries can all be retrieved after insertion."""
        cache = RoomCache(max_size=len(coords) + 10)
        rooms = {}
        for x, y, planet in coords:
            room = _FakeRoom(f"{x},{y},{planet}")
            rooms[(x, y, planet)] = room
            cache.put(x, y, planet, room)

        for (x, y, planet), expected_room in rooms.items():
            retrieved = cache.get(x, y, planet)
            self.assertIs(
                retrieved,
                expected_room,
                f"Round-trip failed for ({x}, {y}, {planet!r})",
            )


# -------------------------------------------------------------- #
#  Property 6: Room cache LRU eviction respects max size
#  **Validates: Requirements 4.2**
# -------------------------------------------------------------- #

# Strategy: a sequence of (x, y, planet) put operations
put_sequence_strategy = st.lists(
    coordinate_strategy,
    min_size=1,
    max_size=100,
)


class TestProperty6CacheLRUEviction(unittest.TestCase):
    """Property 6: Room cache LRU eviction respects max size.

    For any sequence of put operations on a Room_Cache with max_size N,
    the cache size SHALL never exceed N. When the cache is full and a
    new entry is added, the least-recently-used entry SHALL be evicted.

    **Validates: Requirements 4.2**
    """

    @given(
        max_size=max_size_strategy,
        ops=put_sequence_strategy,
    )
    @settings(max_examples=200)
    def test_cache_size_never_exceeds_max(self, max_size, ops):
        """After any sequence of puts, cache.size <= max_size."""
        cache = RoomCache(max_size=max_size)
        for x, y, planet in ops:
            cache.put(x, y, planet, _FakeRoom(f"{x},{y},{planet}"))
            self.assertLessEqual(
                cache.size,
                max_size,
                f"Cache size {cache.size} exceeded max_size {max_size}",
            )

    @given(max_size=max_size_strategy)
    @settings(max_examples=200)
    def test_lru_entry_evicted_when_full(self, max_size):
        """When cache is full and a new unique entry is added, the LRU entry is evicted."""
        cache = RoomCache(max_size=max_size)

        # Fill the cache with max_size unique entries
        for i in range(max_size):
            cache.put(i, 0, "test", _FakeRoom(f"room-{i}"))

        self.assertEqual(cache.size, max_size)

        # The LRU entry is (0, 0, "test") — it was inserted first and never accessed since
        # Add one more unique entry to trigger eviction
        cache.put(max_size, 0, "test", _FakeRoom("new"))

        self.assertEqual(cache.size, max_size, "Size should remain at max_size after eviction")
        self.assertIsNone(
            cache.get(0, 0, "test"),
            "LRU entry (0, 0, 'test') should have been evicted",
        )
        self.assertIsNotNone(
            cache.get(max_size, 0, "test"),
            "Newly added entry should be present",
        )


if __name__ == "__main__":
    unittest.main()
