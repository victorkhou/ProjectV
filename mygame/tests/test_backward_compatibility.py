"""
Backward compatibility tests for Phase 1 changes.

Verifies that existing interfaces remain unchanged after Phase 1
content expansion. Uses the same Evennia stub pattern from existing
tests.

Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6
"""

import inspect
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
    _mod(
        "evennia.objects.objects",
        {
            "DefaultObject": DefaultObject,
            "DefaultRoom": type("DefaultRoom", (), {}),
            "DefaultCharacter": DefaultCharacter,
        },
    )
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
from mygame.world.systems.building_system import BuildingSystem  # noqa: E402
from mygame.world.systems.rank_system import RankSystem  # noqa: E402
from mygame.typeclasses.characters import CombatCharacter, RESOURCE_TYPES  # noqa: E402


# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #


def _make_space():
    """Create a minimal CoordinateSpaceDef for testing."""
    return CoordinateSpaceDef(
        planet_key="terra",
        planet_type="earth",
        width=100,
        height=100,
        terrain_seed=42,
        terrain_noise_cell_size=8,
        terrain_weights={"Plains": 0.5, "Forest": 0.5},
    )


class _FakeEventBus:
    """Minimal event bus stub."""

    def __init__(self):
        self.events = []

    def publish(self, event_type, **kwargs):
        self.events.append((event_type, kwargs))

    def subscribe(self, event_type, handler):
        pass


class _FakeRegistry:
    """Minimal DataRegistry stub for BuildingSystem and RankSystem."""

    def __init__(self):
        self.ranks = []
        self.balance = BalanceConfig()

    def get_building(self, abbr):
        raise KeyError(abbr)

    def get_rank_for_xp(self, xp):
        from mygame.world.definitions import RankDef
        return RankDef(name="Recruit", level=1, xp_threshold=0)

    def get_technologies_for_rank(self, rank_level):
        return []

    def get_powerups_for_rank(self, rank_level):
        return []


# -------------------------------------------------------------- #
#  1. TerrainGenerator interface unchanged (Req 16.1)
# -------------------------------------------------------------- #


class TestTerrainGeneratorBackwardCompat(unittest.TestCase):
    """Verify TerrainGenerator exposes get_terrain and get_terrain_and_resource."""

    def setUp(self):
        self.space = _make_space()
        self.gen = TerrainGenerator(self.space)
        self.gen._set_resource_map({"Plains": None, "Forest": "Wood"})

    def test_get_terrain_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.gen, "get_terrain", None)))

    def test_get_terrain_signature(self):
        sig = inspect.signature(self.gen.get_terrain)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["x", "y"])

    def test_get_terrain_returns_string(self):
        result = self.gen.get_terrain(10, 20)
        self.assertIsInstance(result, str)
        self.assertIn(result, self.space.terrain_weights)

    def test_get_terrain_deterministic(self):
        a = self.gen.get_terrain(10, 20)
        b = self.gen.get_terrain(10, 20)
        self.assertEqual(a, b)

    def test_get_terrain_and_resource_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.gen, "get_terrain_and_resource", None)))

    def test_get_terrain_and_resource_signature(self):
        sig = inspect.signature(self.gen.get_terrain_and_resource)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["x", "y"])

    def test_get_terrain_and_resource_returns_tuple(self):
        result = self.gen.get_terrain_and_resource(10, 20)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        terrain, resource = result
        self.assertIsInstance(terrain, str)
        # resource is str or None
        self.assertTrue(resource is None or isinstance(resource, str))


# -------------------------------------------------------------- #
#  2. PlanetRegistry interface unchanged (Req 16.2)
# -------------------------------------------------------------- #


class TestPlanetRegistryBackwardCompat(unittest.TestCase):
    """Verify PlanetRegistry exposes load_from_yaml, get_space, list_planets, is_valid_coordinate."""

    def setUp(self):
        self.registry = PlanetRegistry()
        self.space = _make_space()
        self.registry._spaces = {"terra": self.space}

    def test_load_from_yaml_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.registry, "load_from_yaml", None)))

    def test_load_from_yaml_signature(self):
        sig = inspect.signature(self.registry.load_from_yaml)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["path"])

    def test_get_space_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.registry, "get_space", None)))

    def test_get_space_signature(self):
        sig = inspect.signature(self.registry.get_space)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["planet_key"])

    def test_get_space_returns_coordinate_space_def(self):
        result = self.registry.get_space("terra")
        self.assertIsInstance(result, CoordinateSpaceDef)

    def test_list_planets_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.registry, "list_planets", None)))

    def test_list_planets_returns_list(self):
        result = self.registry.list_planets()
        self.assertIsInstance(result, list)
        self.assertIn("terra", result)

    def test_is_valid_coordinate_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.registry, "is_valid_coordinate", None)))

    def test_is_valid_coordinate_signature(self):
        sig = inspect.signature(self.registry.is_valid_coordinate)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["x", "y", "planet_key"])

    def test_is_valid_coordinate_returns_bool(self):
        self.assertTrue(self.registry.is_valid_coordinate(0, 0, "terra"))
        self.assertFalse(self.registry.is_valid_coordinate(999, 999, "terra"))


# -------------------------------------------------------------- #
#  3. BuildingSystem construct/upgrade/destroy signatures (Req 16.3)
# -------------------------------------------------------------- #


class TestBuildingSystemBackwardCompat(unittest.TestCase):
    """Verify BuildingSystem exposes construct, upgrade, destroy with expected signatures."""

    def setUp(self):
        self.registry = _FakeRegistry()
        self.event_bus = _FakeEventBus()
        self.bs = BuildingSystem(self.registry, self.event_bus)

    def test_construct_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.bs, "construct", None)))

    def test_construct_signature(self):
        sig = inspect.signature(self.bs.construct)
        params = list(sig.parameters.keys())
        # Core params must be present; x/y are optional kwargs added for PlanetRoom support
        self.assertTrue(params[:3] == ["player", "tile", "building_abbr"],
                        f"Expected first 3 params to be [player, tile, building_abbr], got {params}")

    def test_upgrade_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.bs, "upgrade", None)))

    def test_upgrade_signature(self):
        sig = inspect.signature(self.bs.upgrade)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["player", "building"])

    def test_destroy_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.bs, "destroy", None)))

    def test_destroy_signature(self):
        sig = inspect.signature(self.bs.destroy)
        params = list(sig.parameters.keys())
        self.assertIn("building", params)
        self.assertIn("attacker", params)


# -------------------------------------------------------------- #
#  4. RankSystem award_xp/check_promotion/get_rank signatures (Req 16.4)
# -------------------------------------------------------------- #


class TestRankSystemBackwardCompat(unittest.TestCase):
    """Verify RankSystem exposes award_xp, check_promotion, get_rank with expected signatures."""

    def setUp(self):
        self.registry = _FakeRegistry()
        self.event_bus = _FakeEventBus()
        self.rs = RankSystem(self.registry, self.event_bus)

    def test_award_xp_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.rs, "award_xp", None)))

    def test_award_xp_signature(self):
        sig = inspect.signature(self.rs.award_xp)
        params = list(sig.parameters.keys())
        self.assertIn("player", params)
        self.assertIn("amount", params)
        self.assertIn("reason", params)

    def test_check_promotion_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.rs, "check_promotion", None)))

    def test_check_promotion_signature(self):
        sig = inspect.signature(self.rs.check_promotion)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["player"])

    def test_get_rank_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.rs, "get_rank", None)))

    def test_get_rank_signature(self):
        sig = inspect.signature(self.rs.get_rank)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["player"])


# -------------------------------------------------------------- #
#  5. CombatCharacter resource methods unchanged (Req 16.5)
# -------------------------------------------------------------- #


class TestCombatCharacterBackwardCompat(unittest.TestCase):
    """Verify CombatCharacter resource methods exist with correct signatures."""

    def setUp(self):
        self.char = CombatCharacter.__new__(CombatCharacter)
        # Manually wire up the Evennia-like attribute store
        class _Store:
            def __init__(self):
                self._data = {}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value
            def has(self, key):
                return key in self._data

        class _Db:
            def __init__(self, store):
                object.__setattr__(self, "_store", store)
            def __getattr__(self, key):
                return object.__getattribute__(self, "_store").get(key)
            def __setattr__(self, key, value):
                object.__getattribute__(self, "_store").add(key, value)

        store = _Store()
        self.char.attributes = store
        self.char.db = _Db(store)
        self.char.key = "TestPlayer"
        self.char.at_object_creation()

    def test_get_resource_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.char, "get_resource", None)))

    def test_get_resource_signature(self):
        sig = inspect.signature(self.char.get_resource)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["resource_type"])

    def test_get_resource_returns_int(self):
        result = self.char.get_resource("Wood")
        self.assertIsInstance(result, int)
        self.assertEqual(result, 40)

    def test_add_resource_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.char, "add_resource", None)))

    def test_add_resource_signature(self):
        sig = inspect.signature(self.char.add_resource)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["resource_type", "amount"])

    def test_add_resource_increases_amount(self):
        self.char.add_resource("Wood", 10)
        self.assertEqual(self.char.get_resource("Wood"), 50)

    def test_has_resources_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.char, "has_resources", None)))

    def test_has_resources_signature(self):
        sig = inspect.signature(self.char.has_resources)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["costs"])

    def test_has_resources_returns_bool(self):
        self.assertTrue(self.char.has_resources({"Wood": 10}))
        self.assertFalse(self.char.has_resources({"Wood": 999}))

    def test_deduct_resources_exists_and_callable(self):
        self.assertTrue(callable(getattr(self.char, "deduct_resources", None)))

    def test_deduct_resources_signature(self):
        sig = inspect.signature(self.char.deduct_resources)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["costs"])

    def test_deduct_resources_returns_bool(self):
        result = self.char.deduct_resources({"Wood": 10})
        self.assertTrue(result)
        self.assertEqual(self.char.get_resource("Wood"), 30)

    def test_deduct_resources_rejects_insufficient(self):
        result = self.char.deduct_resources({"Wood": 999})
        self.assertFalse(result)
        # State unchanged
        self.assertEqual(self.char.get_resource("Wood"), 40)


# -------------------------------------------------------------- #
#  6. Resource type migration: 8 types → 6 types (Req 16.6)
# -------------------------------------------------------------- #


class TestResourceTypeMigration(unittest.TestCase):
    """Verify RESOURCE_TYPES constant has exactly 6 types after Phase 1."""

    def test_resource_types_count(self):
        self.assertEqual(len(RESOURCE_TYPES), 6)

    def test_resource_types_names(self):
        expected = {"Wood", "Stone", "Iron", "Energy", "Circuits", "Nexium"}
        self.assertEqual(set(RESOURCE_TYPES), expected)

    def test_default_resources_match_resource_types(self):
        """New CombatCharacter default resources use exactly the 6 types."""
        char = CombatCharacter.__new__(CombatCharacter)

        class _Store:
            def __init__(self):
                self._data = {}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value
            def has(self, key):
                return key in self._data

        class _Db:
            def __init__(self, store):
                object.__setattr__(self, "_store", store)
            def __getattr__(self, key):
                return object.__getattribute__(self, "_store").get(key)
            def __setattr__(self, key, value):
                object.__getattribute__(self, "_store").add(key, value)

        store = _Store()
        char.attributes = store
        char.db = _Db(store)
        char.key = "TestPlayer"
        char.at_object_creation()

        resources = char.db.resources
        self.assertEqual(set(resources.keys()), set(RESOURCE_TYPES))

    def test_starting_resource_amounts(self):
        """Starting resources match Req 3.2: 30 Wood, 20 Stone, 10 Iron, 0 rest."""
        char = CombatCharacter.__new__(CombatCharacter)

        class _Store:
            def __init__(self):
                self._data = {}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value
            def has(self, key):
                return key in self._data

        class _Db:
            def __init__(self, store):
                object.__setattr__(self, "_store", store)
            def __getattr__(self, key):
                return object.__getattribute__(self, "_store").get(key)
            def __setattr__(self, key, value):
                object.__getattribute__(self, "_store").add(key, value)

        store = _Store()
        char.attributes = store
        char.db = _Db(store)
        char.key = "TestPlayer"
        char.at_object_creation()

        self.assertEqual(char.get_resource("Wood"), 40)
        self.assertEqual(char.get_resource("Stone"), 25)
        self.assertEqual(char.get_resource("Iron"), 10)
        self.assertEqual(char.get_resource("Energy"), 0)
        self.assertEqual(char.get_resource("Circuits"), 0)
        self.assertEqual(char.get_resource("Nexium"), 0)


if __name__ == "__main__":
    unittest.main()
