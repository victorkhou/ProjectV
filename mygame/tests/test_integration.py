"""
Integration tests for the Procedural Coordinate World system.

Tests multi-system workflows end-to-end using lightweight fakes
instead of a running Evennia server.

Integration scenarios:
1. Movement + FogOfWar + MapRenderer cycle
2. Full cycle: create player → move → check coords → check fog → render map

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
from mygame.world.coordinate.fog_of_war import FogOfWarSystem  # noqa: E402
from mygame.world.coordinate.procedural_map_renderer import ProceduralMapRenderer  # noqa: E402

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
        self.db.discovery_memory = {"discovered": {}, "buildings": {}}
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
        self._store = _FakeAttrs({"building_type": btype, "owner": owner})
        self.db = _DbProxy(self._store)
        self.building_type = btype
        self.owner = owner
        # fog_of_war._get_building_coords reads coord_x/coord_y from db
        if location is not None and hasattr(location, "db"):
            self.db.coord_x = location.db.x
            self.db.coord_y = location.db.y
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
#  (TileResolver/RoomCache/GarbageCollector tests removed — classes deleted)
# -------------------------------------------------------------- #

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
        self.balance = _make_balance(pvr=2, bvr=1)
        self.fog = FogOfWarSystem(self.balance)
        self.renderer = ProceduralMapRenderer(
            fog_system=self.fog,
            terrain_generators={"earth_planet": self.gen},
        )

    def test_move_updates_fog_discovery(self):
        """After moving, tiles around the new position are discovered."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        player.db.coord_x = 50
        player.db.coord_y = 51

        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible)

        bf = self.fog.get_discovered_tile_set(player)
        self.assertIn((50, 51), bf)
        self.assertIn((50, 52), bf)
        self.assertIn((49, 50), bf)

    def test_map_renders_after_move(self):
        """Map renderer produces non-empty output after a move."""
        player = _FakePlayer(x=50, y=51, planet="earth_planet")
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
        """The player's own tile shows '@@' in the rendered map."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")
        map_str = self.renderer.render(player, [])
        self.assertIn("@@", map_str)

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
        self.balance = _make_balance(pvr=2, bvr=1)
        self.fog = FogOfWarSystem(self.balance)
        self.renderer = ProceduralMapRenderer(
            fog_system=self.fog,
            terrain_generators={"earth_planet": self.gen},
        )

    def test_full_move_fog_render_cycle(self):
        """Complete cycle: spawn → move north → check coords → fog → render."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")

        # Initial fog update at spawn
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible)
        bf = self.fog.get_discovered_tile_set(player)
        self.assertIn((50, 50), bf)

        # Render initial map
        map_str = self.renderer.render(player, [])
        self.assertIn("@@", map_str)

        # Move north: (50, 50) → (50, 51)
        player.db.coord_x = 50
        player.db.coord_y = 51

        self.assertEqual(player.db.coord_x, 50)
        self.assertEqual(player.db.coord_y, 51)

        # Update fog after move
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible)

        bf = self.fog.get_discovered_tile_set(player)
        self.assertIn((50, 53), bf)  # within radius 2 of y=51
        self.assertIn((50, 50), bf)  # old spawn still discovered

        # Render map after move
        map_str = self.renderer.render(player, [])
        self.assertIn("@@", map_str)
        self.assertGreater(len(map_str), 0)

    def test_full_cycle_with_building_vision(self):
        """Building extends vision, discovered tiles include building area."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")

        # Place a building at (55, 50)
        building = _FakeBuilding(btype="HQ", owner=player)
        building.db.coord_x = 55
        building.db.coord_y = 50

        # Update fog with building
        visible = self.fog.get_visible_tiles(player, [building])
        self.fog.update_discovery(player, visible)

        # Building vision radius is 1 in our test balance
        bf = self.fog.get_discovered_tile_set(player)
        self.assertIn((55, 50), bf)
        self.assertIn((55, 51), bf)
        self.assertIn((54, 50), bf)

        # Render includes building area
        map_str = self.renderer.render(player, [building])
        self.assertIsInstance(map_str, str)
        self.assertGreater(len(map_str), 0)

    def test_fog_remembers_discovered_tiles(self):
        """Tiles discovered in vision persist in fog memory after moving away."""
        player = _FakePlayer(x=50, y=50, planet="earth_planet")

        # Update fog at spawn
        visible = self.fog.get_visible_tiles(player, [])
        self.fog.update_discovery(player, visible)

        # Move player far away
        player.db.coord_x = 50
        player.db.coord_y = 55

        # Old spawn tile is still discovered (now fog)
        visible2 = self.fog.get_visible_tiles(player, [])
        vis_state = self.fog.get_tile_visibility(player, 50, 50, visible2)
        self.assertEqual(vis_state, "fog")

        bf = self.fog.get_discovered_tile_set(player)
        self.assertIn((50, 50), bf)

if __name__ == "__main__":
    unittest.main()
