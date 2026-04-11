"""
Property-based tests for TileResolver.

Property 4: Room creation produces correct attributes and tags —
For any valid coordinate (x, y, planet), when the Tile_Resolver creates
a new On_Demand_Room, the room SHALL have: x and y Attributes matching
the input coordinates, a planet Attribute matching the planet key, a
terrain Tag matching the Terrain_Generator output for that coordinate,
a resource_node_data Attribute consistent with the terrain-to-resource
mapping, a persistence_type Tag matching the Coordinate_Space
configuration, an "overworld_tile" Tag in category "room_type", and a
key in the format "{TerrainType} ({x},{y})".

Property 13: Planet coordinate spaces are isolated —
For any two different planets and any coordinate (x, y) valid in both,
resolving (x, y, planet_a) and (x, y, planet_b) SHALL return different
room objects.

**Validates: Requirements 2.1, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 6.3, 6.4, 9.5, 9.6, 10.2, 10.3**
"""

import sys
import types
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.definitions import CoordinateSpaceDef  # noqa: E402
from mygame.world.coordinate.planet_registry import PlanetRegistry  # noqa: E402
from mygame.world.coordinate.room_cache import RoomCache  # noqa: E402
from mygame.world.coordinate.terrain_generator import TerrainGenerator  # noqa: E402
from mygame.world.coordinate.tile_resolver import TileResolver  # noqa: E402

# -------------------------------------------------------------- #
#  Lightweight stubs (same pattern as test_tile_resolver.py)
# -------------------------------------------------------------- #

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
    """Lightweight stand-in for OverworldRoom."""

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

# -------------------------------------------------------------- #
#  Shared test data
# -------------------------------------------------------------- #

EARTH_WEIGHTS = {
    "Plains": 0.35,
    "Forest": 0.25,
    "Dirt": 0.15,
    "Rock": 0.15,
    "Mountain": 0.10,
}

EARTH_RESOURCE_MAP: dict[str, str | None] = {
    "Plains": "Straw",
    "Forest": "Wood",
    "Dirt": None,
    "Rock": "Stone",
    "Mountain": "Iron",
}

INDUSTRIAL_WEIGHTS = {
    "Power_Grid": 0.30,
    "Scrapyard": 0.30,
    "Circuit_Field": 0.25,
    "Ruins": 0.15,
}

INDUSTRIAL_RESOURCE_MAP: dict[str, str | None] = {
    "Power_Grid": "Energy",
    "Scrapyard": "Scrap",
    "Circuit_Field": None,
    "Ruins": "Parts",
}

# -------------------------------------------------------------- #
#  Helpers: build resolver for a single planet
# -------------------------------------------------------------- #

def _make_earth_space():
    return CoordinateSpaceDef(
        planet_key="earth_planet",
        planet_type="earth",
        width=100,
        height=100,
        terrain_seed=42,
        terrain_noise_cell_size=8,
        terrain_weights=EARTH_WEIGHTS,
        persistence_type="static",
        spawn_x=50,
        spawn_y=50,
    )

def _make_industrial_space():
    return CoordinateSpaceDef(
        planet_key="industrial_planet",
        planet_type="industrial",
        width=50,
        height=50,
        terrain_seed=7,
        terrain_noise_cell_size=8,
        terrain_weights=INDUSTRIAL_WEIGHTS,
        persistence_type="static",
        spawn_x=25,
        spawn_y=25,
    )

def _build_resolver_earth():
    """Build a TileResolver wired to earth_planet only."""
    space = _make_earth_space()
    registry = PlanetRegistry()
    registry._spaces = {space.planet_key: space}

    gen = TerrainGenerator(space)
    gen._set_resource_map(EARTH_RESOURCE_MAP)

    cache = RoomCache(max_size=500)
    return TileResolver(
        planet_registry=registry,
        terrain_generators={space.planet_key: gen},
        room_cache=cache,
        create_object_func=_fake_create_object,
    ), gen, space

def _build_resolver_two_planets():
    """Build a TileResolver wired to both earth_planet and industrial_planet."""
    earth = _make_earth_space()
    industrial = _make_industrial_space()

    registry = PlanetRegistry()
    registry._spaces = {
        earth.planet_key: earth,
        industrial.planet_key: industrial,
    }

    earth_gen = TerrainGenerator(earth)
    earth_gen._set_resource_map(EARTH_RESOURCE_MAP)

    industrial_gen = TerrainGenerator(industrial)
    industrial_gen._set_resource_map(INDUSTRIAL_RESOURCE_MAP)

    cache = RoomCache(max_size=500)
    return TileResolver(
        planet_registry=registry,
        terrain_generators={
            earth.planet_key: earth_gen,
            industrial.planet_key: industrial_gen,
        },
        room_cache=cache,
        create_object_func=_fake_create_object,
    ), earth_gen, industrial_gen, earth, industrial

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

# Coordinates valid within earth_planet (0-99, 0-99)
earth_x = st.integers(min_value=0, max_value=99)
earth_y = st.integers(min_value=0, max_value=99)

# Coordinates valid in both earth (0-99) and industrial (0-49)
overlap_x = st.integers(min_value=0, max_value=49)
overlap_y = st.integers(min_value=0, max_value=49)

# -------------------------------------------------------------- #
#  Property 4: Room creation produces correct attributes and tags
#  **Validates: Requirements 2.1, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 9.5, 9.6, 10.2, 10.3**
# -------------------------------------------------------------- #

class TestProperty4RoomCreationCorrectness(unittest.TestCase):
    """Property 4: Room creation produces correct attributes and tags.

    For any valid coordinate (x, y, planet), when the Tile_Resolver
    creates a new On_Demand_Room, the room SHALL have: x and y
    Attributes matching the input coordinates, a planet Attribute
    matching the planet key, a terrain Tag matching the
    Terrain_Generator output for that coordinate, a resource_node_data
    Attribute consistent with the terrain-to-resource mapping, a
    persistence_type Tag matching the Coordinate_Space configuration,
    an "overworld_tile" Tag in category "room_type", and a key in the
    format "{TerrainType} ({x},{y})".

    **Validates: Requirements 2.1, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 9.5, 9.6, 10.2, 10.3**
    """

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_x_attribute_matches_input(self, x, y):
        """Room's x Attribute SHALL match the input x coordinate."""
        resolver, gen, space = _build_resolver_earth()
        room = resolver.resolve(x, y, "earth_planet")
        self.assertEqual(room.db.x, x)

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_y_attribute_matches_input(self, x, y):
        """Room's y Attribute SHALL match the input y coordinate."""
        resolver, gen, space = _build_resolver_earth()
        room = resolver.resolve(x, y, "earth_planet")
        self.assertEqual(room.db.y, y)

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_planet_attribute_matches_input(self, x, y):
        """Room's planet Attribute SHALL match the planet key."""
        resolver, gen, space = _build_resolver_earth()
        room = resolver.resolve(x, y, "earth_planet")
        self.assertEqual(room.db.planet, "earth_planet")

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_terrain_tag_matches_generator(self, x, y):
        """Room's terrain Tag SHALL match the TerrainGenerator output."""
        resolver, gen, space = _build_resolver_earth()
        expected_terrain = gen.get_terrain(x, y)
        room = resolver.resolve(x, y, "earth_planet")
        actual_terrain = room.tags.get(category="terrain")
        self.assertEqual(actual_terrain, expected_terrain)

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_resource_node_data_consistent(self, x, y):
        """Room's resource_node_data SHALL be consistent with terrain-to-resource mapping."""
        resolver, gen, space = _build_resolver_earth()
        _, expected_resource = gen.get_terrain_and_resource(x, y)
        room = resolver.resolve(x, y, "earth_planet")
        rn = room.db.resource_node_data

        if expected_resource is not None:
            self.assertIsNotNone(rn, f"Expected resource_node_data for resource '{expected_resource}'")
            self.assertEqual(rn["resource_type"], expected_resource)
            self.assertFalse(rn["depleted"])
            self.assertEqual(rn["respawn_counter"], 0)
        else:
            self.assertIsNone(rn, "Expected resource_node_data to be None for terrain without resource")

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_persistence_type_tag_matches_space(self, x, y):
        """Room's persistence_type Tag SHALL match the CoordinateSpace configuration."""
        resolver, gen, space = _build_resolver_earth()
        room = resolver.resolve(x, y, "earth_planet")
        pt = room.tags.get(category="persistence_type")
        self.assertEqual(pt, space.persistence_type)

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_has_overworld_tile_tag(self, x, y):
        """Room SHALL have an 'overworld_tile' Tag in category 'room_type'."""
        resolver, gen, space = _build_resolver_earth()
        room = resolver.resolve(x, y, "earth_planet")
        room_type = room.tags.get(category="room_type")
        self.assertEqual(room_type, "overworld_tile")

    @given(x=earth_x, y=earth_y)
    @settings(max_examples=200)
    def test_room_key_format(self, x, y):
        """Room key SHALL be in the format '{TerrainType} ({x},{y})'."""
        resolver, gen, space = _build_resolver_earth()
        expected_terrain = gen.get_terrain(x, y)
        room = resolver.resolve(x, y, "earth_planet")
        expected_key = f"{expected_terrain} ({x},{y})"
        self.assertEqual(room.key, expected_key)

# -------------------------------------------------------------- #
#  Property 13: Planet coordinate spaces are isolated
#  **Validates: Requirements 6.3, 6.4**
# -------------------------------------------------------------- #

class TestProperty13PlanetIsolation(unittest.TestCase):
    """Property 13: Planet coordinate spaces are isolated.

    For any two different planets and any coordinate (x, y) valid in
    both, resolving (x, y, planet_a) and (x, y, planet_b) SHALL return
    different room objects.

    **Validates: Requirements 6.3, 6.4**
    """

    @given(x=overlap_x, y=overlap_y)
    @settings(max_examples=200)
    def test_same_coords_different_planets_return_different_rooms(self, x, y):
        """Resolving the same (x, y) on two different planets returns different room objects."""
        resolver, earth_gen, industrial_gen, earth, industrial = _build_resolver_two_planets()

        room_a = resolver.resolve(x, y, "earth_planet")
        room_b = resolver.resolve(x, y, "industrial_planet")

        self.assertIsNot(
            room_a,
            room_b,
            f"Rooms at ({x}, {y}) on earth_planet and industrial_planet should be different objects",
        )

    @given(x=overlap_x, y=overlap_y)
    @settings(max_examples=200)
    def test_isolated_planet_attributes(self, x, y):
        """Rooms on different planets have their respective planet Attribute."""
        resolver, earth_gen, industrial_gen, earth, industrial = _build_resolver_two_planets()

        room_a = resolver.resolve(x, y, "earth_planet")
        room_b = resolver.resolve(x, y, "industrial_planet")

        self.assertEqual(room_a.db.planet, "earth_planet")
        self.assertEqual(room_b.db.planet, "industrial_planet")

    @given(x=overlap_x, y=overlap_y)
    @settings(max_examples=200)
    def test_isolated_terrain_generation(self, x, y):
        """Rooms on different planets use their own TerrainGenerator."""
        resolver, earth_gen, industrial_gen, earth, industrial = _build_resolver_two_planets()

        room_a = resolver.resolve(x, y, "earth_planet")
        room_b = resolver.resolve(x, y, "industrial_planet")

        terrain_a = room_a.tags.get(category="terrain")
        terrain_b = room_b.tags.get(category="terrain")

        # Terrain should match each planet's own generator
        self.assertEqual(terrain_a, earth_gen.get_terrain(x, y))
        self.assertEqual(terrain_b, industrial_gen.get_terrain(x, y))

if __name__ == "__main__":
    unittest.main()
