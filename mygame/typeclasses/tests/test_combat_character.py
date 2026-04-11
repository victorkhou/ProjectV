"""
Unit tests for CombatCharacter typeclass.

Tests resource helpers, structured status, initialization,
coordinate tracking, discovery memory, and overworld spawn.

Requirements: 1.4, 2.4, 7.8, 8.2, 10.1, 10.4, 11.1, 11.7, 27.1
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


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

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "TestChar")
        def at_object_creation(self):
            pass
        def at_post_login(self, session, **kwargs):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultCharacter": DefaultCharacter,
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from mygame.typeclasses.characters import (  # noqa: E402
    CombatCharacter, RESOURCE_TYPES, DEFAULT_HEALTH,
)


# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_char(name="TestChar") -> CombatCharacter:
    char = CombatCharacter(key=name)
    char.at_object_creation()
    return char


# -------------------------------------------------------------- #
#  Tests: at_object_creation
# -------------------------------------------------------------- #

class TestAtObjectCreation(unittest.TestCase):
    def test_hp_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.hp, DEFAULT_HEALTH)
        self.assertEqual(char.db.hp_max, DEFAULT_HEALTH)

    def test_combat_xp_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.combat_xp, 0)

    def test_rank_level_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.rank_level, 1)

    def test_resources_initialized_to_zero(self):
        char = _make_char()
        for r in RESOURCE_TYPES:
            self.assertEqual(char.get_resource(r), 0)

    def test_powerup_attributes_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.active_powerups, {})
        self.assertEqual(char.db.powerup_cooldowns, {})
        self.assertEqual(char.db.researched_techs, set())
        self.assertEqual(char.db.combat_lockout_tick, 0)

    def test_coord_x_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.coord_x, 0)

    def test_coord_y_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.coord_y, 0)

    def test_coord_planet_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.coord_planet, "")

    def test_discovery_memory_initialized(self):
        char = _make_char()
        self.assertEqual(char.db.discovery_memory, {})


# -------------------------------------------------------------- #
#  Tests: resource helpers
# -------------------------------------------------------------- #

class TestResourceHelpers(unittest.TestCase):
    def test_get_resource_default_zero(self):
        char = _make_char()
        self.assertEqual(char.get_resource("straw"), 0)

    def test_add_resource(self):
        char = _make_char()
        char.add_resource("wood", 50)
        self.assertEqual(char.get_resource("wood"), 50)

    def test_add_resource_accumulates(self):
        char = _make_char()
        char.add_resource("iron", 10)
        char.add_resource("iron", 20)
        self.assertEqual(char.get_resource("iron"), 30)

    def test_has_resources_true(self):
        char = _make_char()
        char.add_resource("stone", 100)
        self.assertTrue(char.has_resources({"stone": 50}))

    def test_has_resources_false(self):
        char = _make_char()
        self.assertFalse(char.has_resources({"clay": 1}))

    def test_has_resources_exact_amount(self):
        char = _make_char()
        char.add_resource("energy", 10)
        self.assertTrue(char.has_resources({"energy": 10}))

    def test_deduct_resources_success(self):
        char = _make_char()
        char.add_resource("metals", 50)
        char.add_resource("circuits", 30)
        result = char.deduct_resources({"metals": 20, "circuits": 10})
        self.assertTrue(result)
        self.assertEqual(char.get_resource("metals"), 30)
        self.assertEqual(char.get_resource("circuits"), 20)

    def test_deduct_resources_failure_no_change(self):
        char = _make_char()
        char.add_resource("straw", 5)
        result = char.deduct_resources({"straw": 10})
        self.assertFalse(result)
        self.assertEqual(char.get_resource("straw"), 5)

    def test_deduct_multi_resource_failure_no_partial(self):
        """If one resource is insufficient, none are deducted."""
        char = _make_char()
        char.add_resource("wood", 100)
        char.add_resource("stone", 1)
        result = char.deduct_resources({"wood": 50, "stone": 10})
        self.assertFalse(result)
        self.assertEqual(char.get_resource("wood"), 100)
        self.assertEqual(char.get_resource("stone"), 1)

    def test_get_resource_unknown_type_returns_zero(self):
        char = _make_char()
        self.assertEqual(char.get_resource("unobtanium"), 0)


# -------------------------------------------------------------- #
#  Tests: get_structured_status
# -------------------------------------------------------------- #

class TestGetStructuredStatus(unittest.TestCase):
    def test_returns_dict(self):
        char = _make_char("Alice")
        status = char.get_structured_status()
        self.assertIsInstance(status, dict)

    def test_contains_expected_keys(self):
        char = _make_char("Alice")
        status = char.get_structured_status()
        for key in ("name", "hp", "hp_max", "combat_xp", "rank_level",
                     "resources", "active_powerups", "researched_techs",
                     "combat_lockout_tick"):
            self.assertIn(key, status)

    def test_name_matches(self):
        char = _make_char("Bob")
        self.assertEqual(char.get_structured_status()["name"], "Bob")

    def test_resources_reflect_additions(self):
        char = _make_char()
        char.add_resource("iron", 42)
        status = char.get_structured_status()
        self.assertEqual(status["resources"]["iron"], 42)


# -------------------------------------------------------------- #
#  Tests: equipment property
# -------------------------------------------------------------- #

class TestEquipmentProperty(unittest.TestCase):
    def test_equipment_handler_exists(self):
        char = _make_char()
        handler = char.equipment
        self.assertIsNotNone(handler)

    def test_equipment_handler_cached(self):
        char = _make_char()
        h1 = char.equipment
        h2 = char.equipment
        self.assertIs(h1, h2)


# -------------------------------------------------------------- #
#  Tests: get_buildings
# -------------------------------------------------------------- #

class TestGetBuildings(unittest.TestCase):
    def test_returns_empty_list(self):
        char = _make_char()
        self.assertEqual(char.get_buildings(), [])


# -------------------------------------------------------------- #
#  Tests: _ensure_overworld_position
# -------------------------------------------------------------- #

class TestEnsureOverworldPosition(unittest.TestCase):
    """Test the _ensure_overworld_position login hook."""

    def _make_limbo_char(self):
        """Create a char whose location looks like Limbo (id=2)."""
        char = _make_char()
        limbo = MagicMock()
        limbo.id = 2
        char.location = limbo
        char.move_to = MagicMock()
        char.msg = MagicMock()
        return char

    def test_moves_to_spawn_from_limbo(self):
        char = self._make_limbo_char()

        from world.definitions import CoordinateSpaceDef
        space = CoordinateSpaceDef(
            planet_key="earth",
            planet_type="earth",
            width=100, height=100,
            terrain_seed=42,
            terrain_weights={"Plains": 1.0},
            spawn_x=50, spawn_y=50,
            default_planet=True,
        )

        mock_registry = MagicMock()
        mock_registry.list_planets.return_value = ["earth"]
        mock_registry.get_space.return_value = space

        mock_room = MagicMock()
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = mock_room

        with patch("world.coordinate.planet_registry.PlanetRegistry", return_value=mock_registry), \
             patch("world.coordinate.terrain_generator.TerrainGenerator"), \
             patch("world.coordinate.room_cache.RoomCache"), \
             patch("world.coordinate.tile_resolver.TileResolver", return_value=mock_resolver):
            char._ensure_overworld_position()

        char.move_to.assert_called_once_with(mock_room, quiet=True)
        self.assertEqual(char.db.coord_x, 50)
        self.assertEqual(char.db.coord_y, 50)
        self.assertEqual(char.db.coord_planet, "earth")

    def test_no_move_when_not_in_limbo(self):
        """If the character is not in Limbo, nothing happens."""
        char = _make_char()
        loc = MagicMock()
        loc.id = 999  # Not Limbo
        char.location = loc
        char.move_to = MagicMock()
        char._ensure_overworld_position()
        char.move_to.assert_not_called()

    def test_no_crash_on_import_failure(self):
        """If PlanetRegistry can't be imported, method silently handles it."""
        char = self._make_limbo_char()
        # The method catches all exceptions, so even if imports fail
        # it should not raise
        with patch.dict(sys.modules, {"world.coordinate.planet_registry": None}):
            char._ensure_overworld_position()
        # No exception raised — that's the test


if __name__ == "__main__":
    unittest.main()
