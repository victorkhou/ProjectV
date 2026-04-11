"""
Unit tests for Building typeclass.

Tests building properties, take_damage, set_offline, display abbreviation,
and get_structured_state.

Requirements: 3.6, 3.7, 3.8, 10.1, 10.5, 27.1
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

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "TestBuilding")
            self.location = None

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
        "DefaultObject": DefaultObject,
        "DefaultCharacter": DefaultCharacter,
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

from mygame.typeclasses.objects import Building  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_building(building_type="HQ", name="Headquarters", hp=200,
                   level=1, owner=None, offline=False) -> Building:
    """Create a Building with stubbed Evennia internals."""
    bld = Building(key=name)
    bld.attributes.add("building_type", building_type)
    bld.attributes.add("hp", hp)
    bld.attributes.add("hp_max", hp)
    bld.attributes.add("building_level", level)
    bld.attributes.add("offline", offline)
    if owner is not None:
        bld.attributes.add("owner", owner)
    return bld

def _make_owner(name="Player1"):
    """Create a mock owner character."""
    owner = MagicMock()
    owner.key = name
    return owner

# -------------------------------------------------------------- #
#  Tests: properties
# -------------------------------------------------------------- #

class TestBuildingProperties(unittest.TestCase):
    def test_building_level_default(self):
        bld = _make_building()
        self.assertEqual(bld.building_level, 1)

    def test_building_level_custom(self):
        bld = _make_building(level=3)
        self.assertEqual(bld.building_level, 3)

    def test_is_offline_default_false(self):
        bld = _make_building()
        self.assertFalse(bld.is_offline)

    def test_is_offline_true(self):
        bld = _make_building(offline=True)
        self.assertTrue(bld.is_offline)

    def test_owner_returns_set_owner(self):
        owner = _make_owner("Alice")
        bld = _make_building(owner=owner)
        self.assertIs(bld.owner, owner)

    def test_owner_returns_none_when_unset(self):
        bld = _make_building()
        self.assertIsNone(bld.owner)

    def test_building_def_returns_none_without_registry(self):
        bld = _make_building()
        self.assertIsNone(bld.building_def)

# -------------------------------------------------------------- #
#  Tests: set_offline
# -------------------------------------------------------------- #

class TestSetOffline(unittest.TestCase):
    def test_set_offline_true(self):
        bld = _make_building()
        bld.set_offline(True)
        self.assertTrue(bld.is_offline)

    def test_set_offline_false(self):
        bld = _make_building(offline=True)
        bld.set_offline(False)
        self.assertFalse(bld.is_offline)

# -------------------------------------------------------------- #
#  Tests: take_damage
# -------------------------------------------------------------- #

class TestTakeDamage(unittest.TestCase):
    def test_reduces_hp(self):
        bld = _make_building(hp=100)
        bld.take_damage(30)
        self.assertEqual(bld.attributes.get("hp"), 70)

    def test_hp_does_not_go_below_zero(self):
        bld = _make_building(hp=50)
        bld.take_damage(100)
        self.assertEqual(bld.attributes.get("hp"), 0)

    def test_zero_damage(self):
        bld = _make_building(hp=100)
        bld.take_damage(0)
        self.assertEqual(bld.attributes.get("hp"), 100)

    def test_exact_lethal_damage(self):
        bld = _make_building(hp=50)
        bld.take_damage(50)
        self.assertEqual(bld.attributes.get("hp"), 0)

    @patch("world.event_bus.event_bus")
    def test_publishes_event_on_destruction(self, mock_bus):
        bld = _make_building(hp=10)
        attacker = _make_owner("Attacker")
        bld.take_damage(10, attacker=attacker)
        mock_bus.publish.assert_called_once()

# -------------------------------------------------------------- #
#  Tests: get_display_abbreviation
# -------------------------------------------------------------- #

class TestGetDisplayAbbreviation(unittest.TestCase):
    def test_returns_building_type(self):
        bld = _make_building(building_type="VV")
        self.assertEqual(bld.get_display_abbreviation(), "VV")

    def test_returns_hq(self):
        bld = _make_building(building_type="HQ")
        self.assertEqual(bld.get_display_abbreviation(), "HQ")

    def test_truncates_long_type(self):
        bld = _make_building(building_type="LONG")
        self.assertEqual(bld.get_display_abbreviation(), "LO")

# -------------------------------------------------------------- #
#  Tests: get_structured_state
# -------------------------------------------------------------- #

class TestGetStructuredState(unittest.TestCase):
    def test_returns_dict(self):
        bld = _make_building()
        state = bld.get_structured_state()
        self.assertIsInstance(state, dict)

    def test_contains_expected_keys(self):
        bld = _make_building()
        state = bld.get_structured_state()
        for key in ("building_type", "name", "owner", "building_level",
                     "hp", "hp_max", "offline"):
            self.assertIn(key, state)

    def test_building_type_matches(self):
        bld = _make_building(building_type="MM")
        self.assertEqual(bld.get_structured_state()["building_type"], "MM")

    def test_name_matches(self):
        bld = _make_building(name="Mill")
        self.assertEqual(bld.get_structured_state()["name"], "Mill")

    def test_owner_name_in_state(self):
        owner = _make_owner("Alice")
        bld = _make_building(owner=owner)
        self.assertEqual(bld.get_structured_state()["owner"], "Alice")

    def test_owner_empty_when_none(self):
        bld = _make_building()
        self.assertEqual(bld.get_structured_state()["owner"], "")

    def test_hp_values(self):
        bld = _make_building(hp=150)
        state = bld.get_structured_state()
        self.assertEqual(state["hp"], 150)
        self.assertEqual(state["hp_max"], 150)

    def test_offline_state(self):
        bld = _make_building(offline=True)
        self.assertTrue(bld.get_structured_state()["offline"])

    def test_level_in_state(self):
        bld = _make_building(level=4)
        self.assertEqual(bld.get_structured_state()["building_level"], 4)

if __name__ == "__main__":
    unittest.main()
