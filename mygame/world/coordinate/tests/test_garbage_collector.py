"""
Unit tests for RoomGarbageCollector.

Tests use lightweight stubs — no Django/Evennia server required.
The GC's ``run(rooms=...)`` injection path is used throughout.

Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 10.5
"""

import pytest

from mygame.world.coordinate.garbage_collector import RoomGarbageCollector
from mygame.world.coordinate.room_cache import RoomCache


# ------------------------------------------------------------------ #
#  Helpers — lightweight stubs
# ------------------------------------------------------------------ #


class _FakeAttrs:
    """Minimal Evennia-like Attribute store."""

    def __init__(self):
        self._data: dict = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class _DbProxy:
    """Proxy that reads/writes through an _FakeAttrs."""

    def __init__(self, store: _FakeAttrs):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class _FakeTags:
    """Minimal Evennia-like Tag store."""

    def __init__(self):
        self._tags: list[tuple[str, str | None]] = []

    def add(self, value, category=None):
        self._tags.append((value, category))

    def get(self, category=None, return_list=False, **kw):
        for val, cat in self._tags:
            if cat == category:
                return val
        return None


class _FakePlayer:
    """Stub representing a player character in room contents."""

    account = True  # presence of 'account' marks it as a player


class _FakeBuilding:
    """Stub representing a building in room contents."""

    building_type = "HQ"


class _FakeRoom:
    """Lightweight stand-in for OverworldRoom."""

    def __init__(
        self,
        x: int = 0,
        y: int = 0,
        planet: str = "space",
        persistence_type: str = "dynamic",
        contents: list | None = None,
        desc: str | None = None,
    ):
        self._attr_store = _FakeAttrs()
        self.attributes = self._attr_store
        self.db = _DbProxy(self._attr_store)
        self.tags = _FakeTags()
        self.contents = contents if contents is not None else []
        self.deleted = False

        # Set coordinate attributes
        self.db.x = x
        self.db.y = y
        self.db.planet = planet

        # Set description if provided
        if desc is not None:
            self.db.desc = desc

        # Set tags
        self.tags.add(persistence_type, category="persistence_type")
        self.tags.add("overworld_tile", category="room_type")

    def delete(self):
        self.deleted = True


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def cache():
    return RoomCache(max_size=100)


@pytest.fixture
def gc(cache):
    return RoomGarbageCollector(room_cache=cache, interval_ticks=100, min_age_ticks=50)


# ------------------------------------------------------------------ #
#  Tests — basic eligibility
# ------------------------------------------------------------------ #


class TestDeletesEmptyDynamicRooms:
    """GC deletes dynamic rooms with no players, no buildings, no custom desc."""

    def test_deletes_empty_dynamic_room(self, gc):
        room = _FakeRoom(persistence_type="dynamic")
        count = gc.run(rooms=[room])
        assert count == 1
        assert room.deleted is True

    def test_deletes_multiple_empty_dynamic_rooms(self, gc):
        rooms = [_FakeRoom(x=i, persistence_type="dynamic") for i in range(5)]
        count = gc.run(rooms=rooms)
        assert count == 5
        assert all(r.deleted for r in rooms)


class TestDeletesEmptyStaticRooms:
    """GC now deletes empty static rooms too — only rooms with state persist."""

    def test_empty_static_room_deleted(self, gc):
        room = _FakeRoom(persistence_type="static")
        count = gc.run(rooms=[room])
        assert count == 1
        assert room.deleted is True

    def test_static_room_with_building_kept(self, gc):
        room = _FakeRoom(persistence_type="static", contents=[_FakeBuilding()])
        count = gc.run(rooms=[room])
        assert count == 0
        assert room.deleted is False

    def test_mixed_static_and_dynamic_both_empty(self, gc):
        static = _FakeRoom(x=0, persistence_type="static")
        dynamic = _FakeRoom(x=1, persistence_type="dynamic")
        count = gc.run(rooms=[static, dynamic])
        assert count == 2
        assert static.deleted is True
        assert dynamic.deleted is True


class TestSkipsRoomsWithPlayers:
    """GC skips dynamic rooms that contain player characters."""

    def test_room_with_player_not_deleted(self, gc):
        room = _FakeRoom(persistence_type="dynamic", contents=[_FakePlayer()])
        count = gc.run(rooms=[room])
        assert count == 0
        assert room.deleted is False


class TestSkipsRoomsWithBuildings:
    """GC skips dynamic rooms that contain buildings."""

    def test_room_with_building_not_deleted(self, gc):
        room = _FakeRoom(persistence_type="dynamic", contents=[_FakeBuilding()])
        count = gc.run(rooms=[room])
        assert count == 0
        assert room.deleted is False

    def test_room_with_building_and_player_not_deleted(self, gc):
        room = _FakeRoom(
            persistence_type="dynamic",
            contents=[_FakePlayer(), _FakeBuilding()],
        )
        count = gc.run(rooms=[room])
        assert count == 0
        assert room.deleted is False


class TestSkipsRoomsWithCustomDescription:
    """GC skips dynamic rooms whose description differs from default."""

    def test_room_with_custom_desc_not_deleted(self, gc):
        room = _FakeRoom(persistence_type="dynamic", desc="A mysterious void.")
        count = gc.run(rooms=[room])
        assert count == 0
        assert room.deleted is False

    def test_room_with_default_evennia_desc_is_deleted(self, gc):
        room = _FakeRoom(
            persistence_type="dynamic", desc="You see nothing special."
        )
        count = gc.run(rooms=[room])
        assert count == 1
        assert room.deleted is True

    def test_room_with_empty_desc_is_deleted(self, gc):
        room = _FakeRoom(persistence_type="dynamic", desc="")
        count = gc.run(rooms=[room])
        assert count == 1
        assert room.deleted is True

    def test_room_with_none_desc_is_deleted(self, gc):
        room = _FakeRoom(persistence_type="dynamic", desc=None)
        count = gc.run(rooms=[room])
        assert count == 1
        assert room.deleted is True


# ------------------------------------------------------------------ #
#  Tests — cache eviction
# ------------------------------------------------------------------ #


class TestCacheEviction:
    """Deleted rooms are removed from the RoomCache."""

    def test_deleted_room_evicted_from_cache(self, gc, cache):
        room = _FakeRoom(x=5, y=10, planet="space", persistence_type="dynamic")
        cache.put(5, 10, "space", room)
        assert cache.get(5, 10, "space") is room

        gc.run(rooms=[room])

        assert cache.get(5, 10, "space") is None

    def test_non_deleted_room_stays_in_cache(self, gc, cache):
        room = _FakeRoom(
            x=5, y=10, planet="space",
            persistence_type="dynamic",
            contents=[_FakePlayer()],
        )
        cache.put(5, 10, "space", room)

        gc.run(rooms=[room])

        assert cache.get(5, 10, "space") is room


# ------------------------------------------------------------------ #
#  Tests — return value
# ------------------------------------------------------------------ #


class TestReturnValue:
    """run() returns the count of deleted rooms."""

    def test_returns_zero_when_no_rooms(self, gc):
        assert gc.run(rooms=[]) == 0

    def test_returns_correct_count(self, gc):
        eligible = [_FakeRoom(x=i, persistence_type="dynamic") for i in range(3)]
        ineligible = [
            _FakeRoom(x=10, persistence_type="static", contents=[_FakeBuilding()]),
            _FakeRoom(x=11, persistence_type="dynamic", contents=[_FakePlayer()]),
        ]
        count = gc.run(rooms=eligible + ineligible)
        assert count == 3


# ------------------------------------------------------------------ #
#  Tests — configuration
# ------------------------------------------------------------------ #


class TestConfiguration:
    """GC stores interval_ticks and min_age_ticks from balance config."""

    def test_default_interval(self):
        gc = RoomGarbageCollector(room_cache=RoomCache())
        assert gc.interval_ticks == 100

    def test_default_min_age(self):
        gc = RoomGarbageCollector(room_cache=RoomCache())
        assert gc.min_age_ticks == 50

    def test_custom_interval(self):
        gc = RoomGarbageCollector(
            room_cache=RoomCache(), interval_ticks=200, min_age_ticks=75
        )
        assert gc.interval_ticks == 200
        assert gc.min_age_ticks == 75
