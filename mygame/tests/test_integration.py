"""
Integration tests for the Procedural Coordinate World system.

Tests multi-system workflows end-to-end using lightweight fakes
instead of a running Evennia server.

Integration scenarios:
1. TileResolver + TerrainGenerator + RoomCache round-trip
2. Movement + FogOfWar + MapRenderer cycle
3. GarbageCollector + TileResolver + RoomCache cleanup
4. PlanetRegistry + TileResolver planet isolation
5. Full cycle: create player → move → check coords → check fog → render map

Requirements: 7.3, 7.4, 7.5, 7.6, 7.7
"""

import sys
import types
import unittest

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
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

    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "")
            self.location = None

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "")
        def at_object_creation(self):
            pass
        def at_post_login(self, session=None, **kwargs):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": DefaultCharacter,
    })
    _mod("evennia.commands")
    _mod("evennia.commands.command", {
        "Command": type("Command", (), {"func": lambda self: None}),
    })
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {
        "DefaultScript": type("DefaultScript", (), {}),
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.definitions import CoordinateSpaceDef, BalanceConfig  # noqa: E402
from mygame.world.coordinate.planet_registry import PlanetRegistry  # noqa: E402
from mygame.world.coordinate.terrain_generator import TerrainGenerator  # noqa: E402
from mygame.world.coordinate.room_cache import RoomCache  # noqa: E402
from mygame.world.coordinate.tile_resolver import TileResolver  # noqa: E402
from mygame.world.coordinate.fog_of_war import FogOfWarSystem  # noqa: E402
from mygame.world.coordinate.procedural_map_renderer import ProceduralMapRenderer  # noqa: E402
from mygame.world.coordinate.garbage_collector import RoomGarbageCollector  # noqa: E402

# -------------------------------------------------------------- #
#  Shared Fakes
# -------------------------------------------------------------- #

class _FakeAttrs:
    """Minimal Evennia-like Attribute store."""

    def __init__(self, data=None):
        self._data = data or {}

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
        self._tags: list[tuple[str, str | None]] = []

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
        self.contents = []
        self.deleted = False

    @property
    def terrain_type(self):
        tag = self.tags.get(category="terrain")
        return tag or "unknown"

    @property
    def resource_node(self):
        data = self.attributes.get("resource_node_data")
        return data if data else None

    @property
    def building(self):
        for obj in self.contents:
            if hasattr(obj, "building_type"):
                return obj
        return None

    def get_display_symbol(self, looker):
        for obj in self.contents:
            if hasattr(obj, "has_account") and obj.has_account:
                if obj is looker:
                    return "@@"
                return "**"
        bld = self.building
        if bld is not None:
            return str(bld.building_type)[:2]
        t = self.terrain_type
        return t[:2] if len(t) >= 2 else t.ljust(2, "?")

    def delete(self):
        self.deleted = True

class _FakePlayer:
    """Lightweight player stand-in with coordinate attributes."""

    def __init__(self, name="Player1", x=50, y=50, planet="earth_planet"):
        self.key = name
        self.has_account = True
        self._attr_store = _FakeAttrs()
        self.db = _DbProxy(self._attr_store)
        self.db.coord_x = x
        self.db.coord_y = y
        self.db.coord_planet = planet
        self.db.discovery_memory = {"discovered": set(), "buildings": {}}
        self.location = None
        self._messages = []

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def move_to(self, target, quiet=True):
        self.location = target

class _FakeBuildingLocation:
    """Wraps a room to expose x/y as direct attributes for fog_of_war."""

    def __init__(self, x, y):
        self.x = x
        self.y = y

class _FakeBuilding:
    """Lightweight building stand-in."""

    def __init__(self, btype="HQ", owner=None, location=None):
        self.building_type = btype
        self.owner = owner
        # fog_of_war._get_building_coords reads location.x / location.y
        if location is not None and hasattr(location, "db"):
            self.location = _FakeBuildingLocation(location.db.x, location.db.y)
        else:
            self.location = location

    def get_display_abbreviation(self):
        return self.building_type

    @property
    def attributes(self):
        return self

    def get(self, key, default=None):
        if key == "building_type":
            return self.building_type
        if key == "owner":
            return self.owner
        return default

def _fake_create_object(**kwargs):
    """Factory that returns a _FakeRoom instead of a real Evennia object."""
    return _FakeRoom(**kwargs)

def _make_earth_space():
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

def _make_industrial_space():
    return CoordinateSpaceDef(
        planet_key="industrial_planet",
        planet_type="industrial",
        width=50,
        height=50,
        terrain_seed=7,
        terrain_noise_cell_size=8,
        terrain_weights={
            "Power_Grid": 0.30,
            "Scrapyard": 0.30,
            "Circuit_Field": 0.25,
            "Ruins": 0.15,
        },
        persistence_type="static",
        spawn_x=25,
        spawn_y=25,
    )

def _make_space_space():
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

def _make_registry(*spaces):
    """Create a PlanetRegistry with the given CoordinateSpaceDefs."""
    reg = PlanetRegistry()
    reg._spaces = {s.planet_key: s for s in spaces}
    return reg

def _make_terrain_gen(space_def, resource_map=None):
    """Create a TerrainGenerator with optional resource map injection."""
    gen = TerrainGenerator(space_def)
    if resource_map is not None:
        gen._set_resource_map(resource_map)
    return gen

def _make_balance(pvr=3, bvr=2):
    """Create a BalanceConfig with small vision radii for testing."""
    return BalanceConfig(player_vision_radius=pvr, building_vision_radius=bvr)

# -------------------------------------------------------------- #
#  1. TileResolver + TerrainGenerator + RoomCache round-trip
# -------------------------------------------------------------- #

class TestTileResolverTerrainCacheRoundTrip(unittest.TestCase):
    """Resolve a coordinate, verify terrain from generator, verify caching."""

    def setUp(self):
        self.earth = _make_earth_space()
        self.registry = _make_registry(self.earth)
        self.gen = _make_terrain_gen(self.earth, resource_map={
            "Plains": "Straw", "Forest": "Wood", "Dirt": None,
            "Rock": "Stone", "Mountain": "Iron",
        })
        self.cache = RoomCache(max_size=100)
        self.resolver = TileResolver(
            planet_registry=self.registry,
            terrain_generators={"earth_planet": self.gen},
            room_cache=self.cache,
            create_object_func=_fake_create_object,
        )

    def test_resolve_creates_room_with_correct_terrain(self):
        """Resolved room terrain matches the TerrainGenerator output."""
        room = self.resolver.resolve(10, 20, "earth_planet")
        expected_terrain = self.gen.get_terrain(10, 20)
        self.assertEqual(room.terrain_type, expected_terrain)

    def test_resolved_room_is_cached(self):
        """After resolve, the room is in the cache."""
        room = self.resolver.resolve(10, 20, "earth_planet")
        cached = self.cache.get(10, 20, "earth_planet")
        self.assertIs(cached, room)

    def test_second_resolve_returns_same_room(self):
        """Resolving the same coordinate twice returns the same object."""
        room1 = self.resolver.resolve(10, 20, "earth_planet")
        room2 = self.resolver.resolve(10, 20, "earth_planet")
        self.assertIs(room1, room2)

    def test_room_has_correct_coordinates(self):
        """Created room stores the correct x, y, planet attributes."""
        room = self.resolver.resolve(15, 25, "earth_planet")
        self.assertEqual(room.db.x, 15)
        self.assertEqual(room.db.y, 25)
        self.assertEqual(room.db.planet, "earth_planet")

    def test_room_resource_matches_terrain(self):
        """Resource node data matches the terrain-to-resource mapping."""
        room = self.resolver.resolve(10, 20, "earth_planet")
        terrain = self.gen.get_terrain(10, 20)
        _, expected_resource = self.gen.get_terrain_and_resource(10, 20)
        rn = room.db.resource_node_data
        if expected_resource:
            self.assertIsNotNone(rn)
            self.assertEqual(rn["resource_type"], expected_resource)
        else:
            self.assertIsNone(rn)

# -------------------------------------------------------------- #
#  2. Movement + FogOfWar + MapRenderer cycle
# -------------------------------------------------------------- #

class TestMovementFogMapCycle(unittest.TestCase):
    """Move a player, verify fog updates, verify map renders output."""

    def setUp(self):
        self.earth = _make_earth_space()
        self.registry = _make_registry(self.earth)
        self.gen = _make_terrain_gen(self.earth, resource_map={
            "Plains": "Straw", "Forest": "Wood", "Dirt": None,
            "Rock": "Stone", "Mountain": "Iron",
        })
        self.cache = RoomCache(max_size=100)
        self.resolver = TileResolver(
            planet_registry=self.registry,
            terrain_generators={"earth_planet": self.gen},
            room_cache=self.cache,
            create_object_func=_fake_create_object,
        )
        self.balance = _make_balance(pvr=2, bvr=1)
        self.fog = FogOfWarSystem(self.balance)
        self.renderer = ProceduralMapRenderer(
            tile_resolver=self.resolver,
            fog_system=self.fog,
            terrain_generators={"earth_planet": self.gen},
        )

    def test_move_updates_fog_discovery(self):
        """After moving, tiles around the new position are discovered."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")

        # Resolve target and move
        target = self.resolver.resolve(50, 51, "earth_planet")
        player.move_to(target)
        player.db.coord_x = 50
        player.db.coord_y = 51

        # Update fog of war
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible, self.resolver)

        # Verify discovery memory contains tiles around (50, 51)
        mem = player.db.discovery_memory
        discovered = mem["discovered"]
        self.assertIn((50, 51), discovered)
        self.assertIn((50, 52), discovered)  # within radius 2
        self.assertIn((49, 50), discovered)

    def test_map_renders_after_move(self):
        """Map renderer produces non-empty output after a move."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")

        # Resolve and move
        target = self.resolver.resolve(50, 51, "earth_planet")
        player.move_to(target)
        player.db.coord_x = 50
        player.db.coord_y = 51

        # Render map
        map_str = self.renderer.render(player, [])
        self.assertIsInstance(map_str, str)
        self.assertGreater(len(map_str), 0)

    def test_map_tiles_are_2_chars(self):
        """All rendered tile symbols are exactly 2 visible characters."""
        import re
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        map_str = self.renderer.render(player, [])
        lines = map_str.strip().split("\n")
        for line in lines:
            for sym in line.split(" "):
                stripped = re.sub(r'\|[a-zA-Z]', '', sym)
                self.assertEqual(len(stripped), 2, f"Symbol '{sym}' -> '{stripped}' is not 2 visible chars")

    def test_player_tile_shows_self_symbol(self):
        """The player's own tile shows '@@' when room exists with player."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        room = self.resolver.resolve(50, 50, "earth_planet")
        room.contents.append(player)
        player.move_to(room)

        map_str = self.renderer.render(player, [])
        self.assertIn("@@", map_str)

# -------------------------------------------------------------- #
#  3. GarbageCollector + TileResolver + RoomCache cleanup
# -------------------------------------------------------------- #

class TestGarbageCollectorResolverCache(unittest.TestCase):
    """Create dynamic rooms via resolver, run GC, verify cleanup."""

    def setUp(self):
        self.space = _make_space_space()
        self.registry = _make_registry(self.space)
        self.cache = RoomCache(max_size=100)
        self.resolver = TileResolver(
            planet_registry=self.registry,
            terrain_generators={},
            room_cache=self.cache,
            create_object_func=_fake_create_object,
        )
        self.gc = RoomGarbageCollector(
            room_cache=self.cache, interval_ticks=100, min_age_ticks=50,
        )

    def test_gc_deletes_empty_dynamic_rooms(self):
        """Empty dynamic rooms created by resolver are deleted by GC."""
        room = self.resolver.resolve(10, 10, "space")
        self.assertEqual(room.tags.get(category="persistence_type"), "dynamic")

        deleted = self.gc.run(rooms=[room])
        self.assertEqual(deleted, 1)
        self.assertTrue(room.deleted)

    def test_gc_evicts_deleted_rooms_from_cache(self):
        """After GC deletes a room, it's removed from the cache."""
        room = self.resolver.resolve(10, 10, "space")
        self.assertIs(self.cache.get(10, 10, "space"), room)

        self.gc.run(rooms=[room])
        self.assertIsNone(self.cache.get(10, 10, "space"))

    def test_gc_skips_rooms_with_players(self):
        """Dynamic rooms with players are not deleted."""
        room = self.resolver.resolve(10, 10, "space")

        class _Player:
            account = True

        room.contents.append(_Player())

        deleted = self.gc.run(rooms=[room])
        self.assertEqual(deleted, 0)
        self.assertFalse(room.deleted)
        self.assertIs(self.cache.get(10, 10, "space"), room)

    def test_gc_skips_rooms_with_buildings(self):
        """Dynamic rooms with buildings are not deleted."""
        room = self.resolver.resolve(10, 10, "space")

        class _Building:
            building_type = "HQ"

        room.contents.append(_Building())

        deleted = self.gc.run(rooms=[room])
        self.assertEqual(deleted, 0)
        self.assertFalse(room.deleted)

    def test_gc_deletes_empty_static_rooms(self):
        """Empty static rooms are now cleaned up too."""
        earth = _make_earth_space()
        reg = _make_registry(earth, self.space)
        gen = _make_terrain_gen(earth)
        cache = RoomCache(max_size=100)
        resolver = TileResolver(
            planet_registry=reg,
            terrain_generators={"earth_planet": gen},
            room_cache=cache,
            create_object_func=_fake_create_object,
        )
        gc = RoomGarbageCollector(room_cache=cache)

        static_room = resolver.resolve(10, 10, "earth_planet")
        self.assertEqual(static_room.tags.get(category="persistence_type"), "static")

        deleted = gc.run(rooms=[static_room])
        self.assertEqual(deleted, 1)
        self.assertTrue(static_room.deleted)

    def test_gc_keeps_static_rooms_with_buildings(self):
        """Static rooms with buildings are preserved."""
        earth = _make_earth_space()
        reg = _make_registry(earth, self.space)
        gen = _make_terrain_gen(earth)
        cache = RoomCache(max_size=100)
        resolver = TileResolver(
            planet_registry=reg,
            terrain_generators={"earth_planet": gen},
            room_cache=cache,
            create_object_func=_fake_create_object,
        )
        gc = RoomGarbageCollector(room_cache=cache)

        static_room = resolver.resolve(10, 10, "earth_planet")

        class _Bld:
            building_type = "HQ"
        static_room.contents.append(_Bld())

        deleted = gc.run(rooms=[static_room])
        self.assertEqual(deleted, 0)
        self.assertFalse(static_room.deleted)

# -------------------------------------------------------------- #
#  4. PlanetRegistry + TileResolver planet isolation
# -------------------------------------------------------------- #

class TestPlanetIsolation(unittest.TestCase):
    """Same (x, y) on different planets yields different rooms/terrain."""

    def setUp(self):
        self.earth = _make_earth_space()
        self.industrial = _make_industrial_space()
        self.registry = _make_registry(self.earth, self.industrial)

        self.earth_gen = _make_terrain_gen(self.earth, resource_map={
            "Plains": "Straw", "Forest": "Wood", "Dirt": None,
            "Rock": "Stone", "Mountain": "Iron",
        })
        self.industrial_gen = _make_terrain_gen(self.industrial, resource_map={
            "Power_Grid": "Energy", "Scrapyard": "Metals",
            "Circuit_Field": "Circuits", "Ruins": None,
        })
        self.cache = RoomCache(max_size=200)
        self.resolver = TileResolver(
            planet_registry=self.registry,
            terrain_generators={
                "earth_planet": self.earth_gen,
                "industrial_planet": self.industrial_gen,
            },
            room_cache=self.cache,
            create_object_func=_fake_create_object,
        )

    def test_same_coords_different_planets_different_rooms(self):
        """Resolving (10, 10) on earth vs industrial gives different rooms."""
        earth_room = self.resolver.resolve(10, 10, "earth_planet")
        industrial_room = self.resolver.resolve(10, 10, "industrial_planet")
        self.assertIsNot(earth_room, industrial_room)

    def test_same_coords_different_planets_different_terrain(self):
        """Terrain at (10, 10) differs between planets (different seeds)."""
        earth_terrain = self.earth_gen.get_terrain(10, 10)
        industrial_terrain = self.industrial_gen.get_terrain(10, 10)

        earth_room = self.resolver.resolve(10, 10, "earth_planet")
        industrial_room = self.resolver.resolve(10, 10, "industrial_planet")

        self.assertEqual(earth_room.terrain_type, earth_terrain)
        self.assertEqual(industrial_room.terrain_type, industrial_terrain)

    def test_planet_rooms_cached_independently(self):
        """Cache stores rooms per-planet, no cross-contamination."""
        earth_room = self.resolver.resolve(10, 10, "earth_planet")
        industrial_room = self.resolver.resolve(10, 10, "industrial_planet")

        self.assertIs(self.cache.get(10, 10, "earth_planet"), earth_room)
        self.assertIs(self.cache.get(10, 10, "industrial_planet"), industrial_room)

    def test_earth_terrain_in_earth_set(self):
        """Earth room terrain is from the Earth terrain set."""
        earth_terrains = set(self.earth.terrain_weights.keys())
        room = self.resolver.resolve(10, 10, "earth_planet")
        self.assertIn(room.terrain_type, earth_terrains)

    def test_industrial_terrain_in_industrial_set(self):
        """Industrial room terrain is from the Industrial terrain set."""
        industrial_terrains = set(self.industrial.terrain_weights.keys())
        room = self.resolver.resolve(10, 10, "industrial_planet")
        self.assertIn(room.terrain_type, industrial_terrains)

    def test_out_of_bounds_on_smaller_planet(self):
        """Coords valid on earth (100x100) but invalid on industrial (50x50)."""
        # (75, 75) is valid on earth but out of bounds on industrial
        room = self.resolver.resolve(75, 75, "earth_planet")
        self.assertIsNotNone(room)

        with self.assertRaises(ValueError):
            self.resolver.resolve(75, 75, "industrial_planet")

# -------------------------------------------------------------- #
#  5. Full cycle: player → move → coords → fog → render → verify
# -------------------------------------------------------------- #

class TestFullCycle(unittest.TestCase):
    """End-to-end: create player, move, check coords, fog, render."""

    def setUp(self):
        self.earth = _make_earth_space()
        self.registry = _make_registry(self.earth)
        self.gen = _make_terrain_gen(self.earth, resource_map={
            "Plains": "Straw", "Forest": "Wood", "Dirt": None,
            "Rock": "Stone", "Mountain": "Iron",
        })
        self.cache = RoomCache(max_size=100)
        self.resolver = TileResolver(
            planet_registry=self.registry,
            terrain_generators={"earth_planet": self.gen},
            room_cache=self.cache,
            create_object_func=_fake_create_object,
        )
        self.balance = _make_balance(pvr=2, bvr=1)
        self.fog = FogOfWarSystem(self.balance)
        self.renderer = ProceduralMapRenderer(
            tile_resolver=self.resolver,
            fog_system=self.fog,
            terrain_generators={"earth_planet": self.gen},
        )

    def test_full_move_fog_render_cycle(self):
        """Complete cycle: spawn → move north → check coords → fog → render."""
        # 1. Create player at spawn
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        spawn_room = self.resolver.resolve(50, 50, "earth_planet")
        spawn_room.contents.append(player)
        player.move_to(spawn_room)

        # 2. Initial fog update at spawn
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible, self.resolver)
        mem = player.db.discovery_memory
        self.assertIn((50, 50), mem["discovered"])

        # 3. Render initial map
        map_str = self.renderer.render(player, [])
        self.assertIn("@@", map_str)

        # 4. Move north: (50, 50) → (50, 51)
        spawn_room.contents.remove(player)
        target = self.resolver.resolve(50, 51, "earth_planet")
        target.contents.append(player)
        player.move_to(target)
        player.db.coord_x = 50
        player.db.coord_y = 51

        # 5. Check coordinates updated
        self.assertEqual(player.db.coord_x, 50)
        self.assertEqual(player.db.coord_y, 51)

        # 6. Update fog after move
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible, self.resolver)

        # 7. Verify new tiles discovered
        mem = player.db.discovery_memory
        self.assertIn((50, 53), mem["discovered"])  # within radius 2 of y=51

        # 8. Old spawn tile is still discovered (now fog)
        self.assertIn((50, 50), mem["discovered"])

        # 9. Render map after move
        map_str = self.renderer.render(player, [])
        self.assertIn("@@", map_str)
        self.assertGreater(len(map_str), 0)

    def test_full_cycle_with_building_vision(self):
        """Building extends vision, discovered tiles include building area."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        spawn_room = self.resolver.resolve(50, 50, "earth_planet")
        spawn_room.contents.append(player)
        player.move_to(spawn_room)

        # Place a building at (55, 50)
        building_room = self.resolver.resolve(55, 50, "earth_planet")
        building = _FakeBuilding(btype="HQ", owner=player, location=building_room)
        building_room.contents.append(building)

        # Update fog with building
        visible = self.fog.get_visible_tiles(player, [building])
        self.fog.update_discovery(player, visible, self.resolver)

        # Building vision radius is 1 in our test balance
        # Tiles around (55, 50) should be discovered
        mem = player.db.discovery_memory
        self.assertIn((55, 50), mem["discovered"])
        self.assertIn((55, 51), mem["discovered"])
        self.assertIn((54, 50), mem["discovered"])

        # Render includes building area
        map_str = self.renderer.render(player, [building])
        self.assertIsInstance(map_str, str)
        self.assertGreater(len(map_str), 0)

    def test_fog_remembers_enemy_building(self):
        """Enemy building discovered in vision persists in fog memory."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        enemy = _FakePlayer(name="Enemy", x=99, y=99, planet="earth_planet")

        # Create a room with an enemy building within vision
        bld_room = self.resolver.resolve(51, 50, "earth_planet")
        enemy_bld = _FakeBuilding(btype="VV", owner=enemy, location=bld_room)
        bld_room.contents.append(enemy_bld)

        # Update fog — (51, 50) is within pvr=2 of (50, 50)
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible, self.resolver)

        # Verify enemy building is in discovery memory
        mem = player.db.discovery_memory
        self.assertIn((51, 50), mem["buildings"])
        self.assertEqual(mem["buildings"][(51, 50)]["building_type"], "VV")

        # Move player far away so (51, 50) is in fog
        player.db.coord_x = 50
        player.db.coord_y = 55

        # Re-render — fog tile should still show building from memory
        visible2 = self.fog.get_visible_tiles(player, [])
        vis_state = self.fog.get_tile_visibility(player, 51, 50, visible2)
        self.assertEqual(vis_state, "fog")

        # Building snapshot persists
        buildings = self.fog.get_discovered_buildings(player, 51, 50)
        self.assertEqual(len(buildings), 1)
        self.assertEqual(buildings[0].building_type, "VV")

if __name__ == "__main__":
    unittest.main()
