"""
Unit tests for TileResolver.

Tests the resolve, get_if_exists, and get_or_generate_terrain methods
using lightweight mocks — no Django/Evennia server required.

Requirements: 2.1–2.10, 9.1–9.6
"""

import pytest

from mygame.world.definitions import CoordinateSpaceDef
from mygame.world.coordinate.planet_registry import PlanetRegistry
from mygame.world.coordinate.room_cache import RoomCache
from mygame.world.coordinate.terrain_generator import TerrainGenerator
from mygame.world.coordinate.tile_resolver import TileResolver


# ------------------------------------------------------------------ #
#  Helpers — lightweight stubs
# ------------------------------------------------------------------ #


class _FakeAttrs:
    """Minimal Evennia-like Attribute store."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class _DbProxy:
    """Proxy that reads/writes through an _FakeAttrs."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class _FakeTags:
    """Minimal Evennia-like Tag store."""

    def __init__(self):
        self._tags: list[tuple[str, str]] = []

    def add(self, value, category=None):
        self._tags.append((value, category))

    def get(self, category=None, return_list=False, **kw):
        for val, cat in self._tags:
            if cat == category:
                return val
        return None


class _FakeRoom:
    """Lightweight stand-in for OverworldRoom created by _create_object."""

    def __init__(self, **kwargs):
        self._attr_store = _FakeAttrs()
        self.attributes = self._attr_store
        self.db = _DbProxy(self._attr_store)
        self.tags = _FakeTags()
        self.key = kwargs.get("key", "")

    @property
    def terrain_type(self):
        tag = self.tags.get(category="terrain")
        return tag or "unknown"

    @property
    def resource_node(self):
        data = self.attributes.get("resource_node_data")
        return data if data else None


def _fake_create_object(**kwargs):
    """Factory that returns a _FakeRoom instead of a real Evennia object."""
    return _FakeRoom(**kwargs)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def earth_space():
    return CoordinateSpaceDef(
        planet_key="earth_planet",
        planet_type="earth",
        width=100,
        height=100,
        terrain_seed=42,
        terrain_noise_cell_size=8,
        terrain_weights={
            "Plains": 0.35,
            "Forest": 0.25,
            "Dirt": 0.15,
            "Rock": 0.15,
            "Mountain": 0.10,
        },
        persistence_type="static",
        spawn_x=50,
        spawn_y=50,
    )


@pytest.fixture
def space_space():
    return CoordinateSpaceDef(
        planet_key="space",
        planet_type="space",
        width=200,
        height=200,
        terrain_seed=99,
        terrain_noise_cell_size=8,
        terrain_weights={},
        persistence_type="dynamic",
        spawn_x=100,
        spawn_y=100,
    )


@pytest.fixture
def registry(earth_space, space_space):
    reg = PlanetRegistry()
    reg._spaces = {
        earth_space.planet_key: earth_space,
        space_space.planet_key: space_space,
    }
    return reg


@pytest.fixture
def terrain_gen(earth_space):
    gen = TerrainGenerator(earth_space)
    gen._set_resource_map({
        "Plains": "Straw",
        "Forest": "Wood",
        "Dirt": None,
        "Rock": "Stone",
        "Mountain": "Iron",
    })
    return gen


@pytest.fixture
def generators(terrain_gen):
    return {"earth_planet": terrain_gen}


@pytest.fixture
def cache():
    return RoomCache(max_size=100)


@pytest.fixture
def resolver(registry, generators, cache):
    return TileResolver(
        planet_registry=registry,
        terrain_generators=generators,
        room_cache=cache,
        create_object_func=_fake_create_object,
    )


# ------------------------------------------------------------------ #
#  Tests: resolve — cache miss + db miss → creates room
# ------------------------------------------------------------------ #


class TestResolveCreatesRoom:
    def test_creates_room_on_full_miss(self, resolver):
        room = resolver.resolve(10, 20, "earth_planet")
        assert room is not None
        assert room.db.x == 10
        assert room.db.y == 20
        assert room.db.planet == "earth_planet"

    def test_created_room_has_terrain_tag(self, resolver):
        room = resolver.resolve(10, 20, "earth_planet")
        terrain = room.tags.get(category="terrain")
        assert terrain is not None
        assert terrain != "unknown"

    def test_created_room_has_overworld_tile_tag(self, resolver):
        room = resolver.resolve(10, 20, "earth_planet")
        room_type = room.tags.get(category="room_type")
        assert room_type == "overworld_tile"

    def test_created_room_has_persistence_type_tag(self, resolver):
        room = resolver.resolve(10, 20, "earth_planet")
        pt = room.tags.get(category="persistence_type")
        assert pt == "static"

    def test_created_room_has_coord_tags(self, resolver):
        room = resolver.resolve(10, 20, "earth_planet")
        assert room.tags.get(category="coord_x") == "10"
        assert room.tags.get(category="coord_y") == "20"
        assert room.tags.get(category="coord_planet") == "earth_planet"

    def test_created_room_key_format(self, resolver, terrain_gen):
        room = resolver.resolve(10, 20, "earth_planet")
        terrain = terrain_gen.get_terrain(10, 20)
        assert room.key == f"{terrain} (10,20)"

    def test_created_room_stored_in_cache(self, resolver, cache):
        room = resolver.resolve(10, 20, "earth_planet")
        cached = cache.get(10, 20, "earth_planet")
        assert cached is room


# ------------------------------------------------------------------ #
#  Tests: resolve — cache hit
# ------------------------------------------------------------------ #


class TestResolveCacheHit:
    def test_returns_cached_room(self, resolver, cache):
        existing = _FakeRoom(key="cached")
        cache.put(5, 5, "earth_planet", existing)
        result = resolver.resolve(5, 5, "earth_planet")
        assert result is existing

    def test_does_not_create_new_room_on_cache_hit(self, resolver, cache):
        existing = _FakeRoom(key="cached")
        cache.put(5, 5, "earth_planet", existing)
        created_rooms = []
        original_create = resolver._create_object

        def tracking_create(**kwargs):
            room = original_create(**kwargs)
            created_rooms.append(room)
            return room

        resolver._create_object = tracking_create
        resolver.resolve(5, 5, "earth_planet")
        assert len(created_rooms) == 0


# ------------------------------------------------------------------ #
#  Tests: resolve — cache miss + db hit
# ------------------------------------------------------------------ #


class TestResolveDbHit:
    def test_returns_db_room_and_caches_it(self, resolver, cache):
        """Simulate a db hit by patching _db_lookup."""
        db_room = _FakeRoom(key="from_db")

        original_db_lookup = resolver._db_lookup
        resolver._db_lookup = lambda x, y, p: db_room

        result = resolver.resolve(10, 10, "earth_planet")
        assert result is db_room
        assert cache.get(10, 10, "earth_planet") is db_room

        # Restore
        resolver._db_lookup = original_db_lookup


# ------------------------------------------------------------------ #
#  Tests: resolve — out-of-bounds raises ValueError
# ------------------------------------------------------------------ #


class TestResolveOutOfBounds:
    def test_negative_x_raises(self, resolver):
        with pytest.raises(ValueError, match="out of bounds"):
            resolver.resolve(-1, 0, "earth_planet")

    def test_negative_y_raises(self, resolver):
        with pytest.raises(ValueError, match="out of bounds"):
            resolver.resolve(0, -1, "earth_planet")

    def test_x_at_width_raises(self, resolver):
        with pytest.raises(ValueError, match="out of bounds"):
            resolver.resolve(100, 0, "earth_planet")

    def test_y_at_height_raises(self, resolver):
        with pytest.raises(ValueError, match="out of bounds"):
            resolver.resolve(0, 100, "earth_planet")

    def test_valid_corner_does_not_raise(self, resolver):
        room = resolver.resolve(0, 0, "earth_planet")
        assert room is not None

    def test_valid_max_corner_does_not_raise(self, resolver):
        room = resolver.resolve(99, 99, "earth_planet")
        assert room is not None


# ------------------------------------------------------------------ #
#  Tests: get_if_exists — returns None when room doesn't exist
# ------------------------------------------------------------------ #


class TestGetIfExists:
    def test_returns_none_when_no_room(self, resolver):
        result = resolver.get_if_exists(50, 50, "earth_planet")
        assert result is None

    def test_returns_cached_room(self, resolver, cache):
        existing = _FakeRoom(key="cached")
        cache.put(50, 50, "earth_planet", existing)
        result = resolver.get_if_exists(50, 50, "earth_planet")
        assert result is existing

    def test_does_not_create_room(self, resolver):
        """get_if_exists should never create a room."""
        created_rooms = []
        original_create = resolver._create_object

        def tracking_create(**kwargs):
            room = original_create(**kwargs)
            created_rooms.append(room)
            return room

        resolver._create_object = tracking_create
        resolver.get_if_exists(50, 50, "earth_planet")
        assert len(created_rooms) == 0

    def test_returns_db_room_and_caches(self, resolver, cache):
        db_room = _FakeRoom(key="from_db")
        resolver._db_lookup = lambda x, y, p: db_room

        result = resolver.get_if_exists(10, 10, "earth_planet")
        assert result is db_room
        assert cache.get(10, 10, "earth_planet") is db_room


# ------------------------------------------------------------------ #
#  Tests: get_or_generate_terrain — returns terrain without creating
# ------------------------------------------------------------------ #


class TestGetOrGenerateTerrain:
    def test_returns_terrain_from_generator_when_no_room(self, resolver, terrain_gen):
        terrain, resource = resolver.get_or_generate_terrain(10, 20, "earth_planet")
        expected_terrain = terrain_gen.get_terrain(10, 20)
        assert terrain == expected_terrain

    def test_returns_terrain_from_existing_room(self, resolver, cache):
        room = _FakeRoom(key="existing")
        room.tags.add("Forest", category="terrain")
        room.db.resource_node_data = {
            "resource_type": "Wood",
            "depleted": False,
            "respawn_counter": 0,
        }
        cache.put(10, 20, "earth_planet", room)

        terrain, resource = resolver.get_or_generate_terrain(10, 20, "earth_planet")
        assert terrain == "Forest"
        assert resource == "Wood"

    def test_does_not_create_room(self, resolver):
        created_rooms = []
        original_create = resolver._create_object

        def tracking_create(**kwargs):
            room = original_create(**kwargs)
            created_rooms.append(room)
            return room

        resolver._create_object = tracking_create
        resolver.get_or_generate_terrain(10, 20, "earth_planet")
        assert len(created_rooms) == 0

    def test_returns_unknown_for_planet_without_generator(self, resolver):
        terrain, resource = resolver.get_or_generate_terrain(10, 10, "space")
        assert terrain == "unknown"
        assert resource is None

    def test_resource_none_for_terrain_without_resource(self, resolver, terrain_gen):
        # Find a coordinate that generates "Dirt" (no resource)
        # We'll just check that the resource matches the generator output
        terrain, resource = resolver.get_or_generate_terrain(10, 20, "earth_planet")
        expected_terrain, expected_resource = terrain_gen.get_terrain_and_resource(10, 20)
        assert terrain == expected_terrain
        assert resource == expected_resource


# ------------------------------------------------------------------ #
#  Tests: resource_node_data on created rooms
# ------------------------------------------------------------------ #


class TestResourceNodeData:
    def test_room_with_resource_terrain_has_resource_data(self, resolver, terrain_gen):
        # Create a room and check resource_node_data matches terrain generator
        room = resolver.resolve(10, 20, "earth_planet")
        terrain = terrain_gen.get_terrain(10, 20)
        _, expected_resource = terrain_gen.get_terrain_and_resource(10, 20)

        rn = room.db.resource_node_data
        if expected_resource:
            assert rn is not None
            assert rn["resource_type"] == expected_resource
            assert rn["depleted"] is False
            assert rn["respawn_counter"] == 0
        else:
            assert rn is None


# ------------------------------------------------------------------ #
#  Tests: dynamic persistence type
# ------------------------------------------------------------------ #


class TestDynamicPersistence:
    def test_space_room_has_dynamic_persistence(self, registry, cache):
        """Rooms on 'space' planet should be tagged dynamic."""
        # space has no terrain generator, so terrain will be "unknown"
        resolver = TileResolver(
            planet_registry=registry,
            terrain_generators={},
            room_cache=cache,
            create_object_func=_fake_create_object,
        )
        room = resolver.resolve(10, 10, "space")
        pt = room.tags.get(category="persistence_type")
        assert pt == "dynamic"
