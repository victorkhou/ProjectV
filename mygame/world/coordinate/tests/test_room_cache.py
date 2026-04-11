"""Unit tests for RoomCache."""

import pytest

from mygame.world.coordinate.room_cache import RoomCache


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

class _FakeRoom:
    """Lightweight stand-in for OverworldRoom."""

    def __init__(self, label: str = "room") -> None:
        self.label = label

    def __repr__(self) -> str:
        return f"FakeRoom({self.label!r})"


# ------------------------------------------------------------------ #
#  Tests: basic get / put / remove / clear
# ------------------------------------------------------------------ #

class TestBasicOperations:
    def test_get_missing_returns_none(self):
        cache = RoomCache(max_size=10)
        assert cache.get(0, 0, "earth") is None

    def test_put_then_get_returns_same_room(self):
        cache = RoomCache(max_size=10)
        room = _FakeRoom("A")
        cache.put(1, 2, "earth", room)
        assert cache.get(1, 2, "earth") is room

    def test_put_overwrites_existing(self):
        cache = RoomCache(max_size=10)
        room1 = _FakeRoom("old")
        room2 = _FakeRoom("new")
        cache.put(1, 2, "earth", room1)
        cache.put(1, 2, "earth", room2)
        assert cache.get(1, 2, "earth") is room2
        assert cache.size == 1

    def test_remove_existing_entry(self):
        cache = RoomCache(max_size=10)
        cache.put(5, 5, "space", _FakeRoom())
        cache.remove(5, 5, "space")
        assert cache.get(5, 5, "space") is None
        assert cache.size == 0

    def test_remove_missing_is_noop(self):
        cache = RoomCache(max_size=10)
        cache.remove(99, 99, "nowhere")  # should not raise

    def test_clear_empties_cache(self):
        cache = RoomCache(max_size=10)
        for i in range(5):
            cache.put(i, 0, "earth", _FakeRoom(str(i)))
        cache.clear()
        assert cache.size == 0
        assert cache.get(0, 0, "earth") is None


# ------------------------------------------------------------------ #
#  Tests: size property
# ------------------------------------------------------------------ #

class TestSize:
    def test_empty_cache_size_zero(self):
        assert RoomCache(max_size=10).size == 0

    def test_size_tracks_inserts(self):
        cache = RoomCache(max_size=100)
        for i in range(7):
            cache.put(i, 0, "earth", _FakeRoom())
        assert cache.size == 7

    def test_size_after_remove(self):
        cache = RoomCache(max_size=100)
        cache.put(0, 0, "earth", _FakeRoom())
        cache.put(1, 0, "earth", _FakeRoom())
        cache.remove(0, 0, "earth")
        assert cache.size == 1


# ------------------------------------------------------------------ #
#  Tests: LRU eviction
# ------------------------------------------------------------------ #

class TestLRUEviction:
    def test_evicts_lru_when_over_max(self):
        cache = RoomCache(max_size=3)
        cache.put(0, 0, "e", _FakeRoom("A"))
        cache.put(1, 0, "e", _FakeRoom("B"))
        cache.put(2, 0, "e", _FakeRoom("C"))
        # Cache is full. Adding a 4th should evict (0,0,"e").
        cache.put(3, 0, "e", _FakeRoom("D"))
        assert cache.size == 3
        assert cache.get(0, 0, "e") is None  # evicted
        assert cache.get(1, 0, "e") is not None

    def test_get_refreshes_lru_order(self):
        cache = RoomCache(max_size=3)
        cache.put(0, 0, "e", _FakeRoom("A"))
        cache.put(1, 0, "e", _FakeRoom("B"))
        cache.put(2, 0, "e", _FakeRoom("C"))
        # Access (0,0) to make it most-recently-used
        cache.get(0, 0, "e")
        # Now insert a 4th — (1,0) should be evicted (it's the LRU)
        cache.put(3, 0, "e", _FakeRoom("D"))
        assert cache.get(1, 0, "e") is None
        assert cache.get(0, 0, "e") is not None

    def test_put_existing_refreshes_lru_order(self):
        cache = RoomCache(max_size=3)
        cache.put(0, 0, "e", _FakeRoom("A"))
        cache.put(1, 0, "e", _FakeRoom("B"))
        cache.put(2, 0, "e", _FakeRoom("C"))
        # Re-put (0,0) to refresh it
        cache.put(0, 0, "e", _FakeRoom("A2"))
        # Insert 4th — (1,0) should be evicted
        cache.put(3, 0, "e", _FakeRoom("D"))
        assert cache.get(1, 0, "e") is None
        assert cache.get(0, 0, "e") is not None

    def test_max_size_one(self):
        cache = RoomCache(max_size=1)
        cache.put(0, 0, "e", _FakeRoom("A"))
        cache.put(1, 0, "e", _FakeRoom("B"))
        assert cache.size == 1
        assert cache.get(0, 0, "e") is None
        assert cache.get(1, 0, "e") is not None

    def test_max_size_clamped_to_one(self):
        cache = RoomCache(max_size=0)
        cache.put(0, 0, "e", _FakeRoom("A"))
        assert cache.size == 1  # max_size clamped to 1


# ------------------------------------------------------------------ #
#  Tests: planet isolation
# ------------------------------------------------------------------ #

class TestPlanetIsolation:
    def test_same_coords_different_planets(self):
        cache = RoomCache(max_size=10)
        room_e = _FakeRoom("earth")
        room_s = _FakeRoom("space")
        cache.put(5, 5, "earth", room_e)
        cache.put(5, 5, "space", room_s)
        assert cache.get(5, 5, "earth") is room_e
        assert cache.get(5, 5, "space") is room_s
        assert cache.size == 2
