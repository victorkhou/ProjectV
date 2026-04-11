"""
Unit tests for OverworldRoom typeclass.

Tests the terrain_type, resource_node, building, planet_name properties,
get_display_symbol priority logic, at_object_receive, and get_structured_state.

Since Evennia requires Django settings to import, we mock the Evennia
imports and test the OverworldRoom methods directly on lightweight
stand-in objects.

Requirements: 1.1, 1.5, 1.8, 27.1
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules so rooms.py can be imported
#  without a running Django/Evennia server.
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return  # real Evennia — don't overwrite
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    # Shared lightweight Attribute/db stubs used by multiple typeclasses
    class _AttrStore:
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
        """Proxy that reads/writes through an _AttrStore."""
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
        def at_post_login(self, session, **kwargs):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultRoom": type("DefaultRoom", (), {
            "at_object_receive": lambda self, moved_obj, source_location, **kwargs: None,
        }),
        "DefaultObject": DefaultObject,
        "DefaultCharacter": DefaultCharacter,
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.typeclasses.rooms import OverworldRoom  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers — thin wrappers that let real OverworldRoom methods run
# -------------------------------------------------------------- #

class _FakeAttrs:
    """Minimal Evennia-like Attribute store."""
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def has(self, key):
        return key in self._data

class _FakeTags:
    """Minimal Evennia-like Tag store."""
    def __init__(self, data=None):
        self._data = data or {}  # category -> value

    def get(self, category=None, return_list=False, **kw):
        return self._data.get(category)

class _FakeRoom(OverworldRoom):
    """Concrete stand-in that bypasses Evennia DB layer."""

    def __init__(self, terrain="Plains", x=5, y=10, planet="earth_1",
                 contents=None, resource_node_data=None):
        # Do NOT call super().__init__() — no DB
        self._terrain = terrain
        self._contents = contents or []
        self.tags = _FakeTags({"terrain": terrain})
        attr_data = {"x": x, "y": y, "planet": planet}
        if resource_node_data:
            attr_data["resource_node_data"] = resource_node_data
        self.attributes = _FakeAttrs(attr_data)

    @property
    def contents(self):
        return list(self._contents)

def _make_player(name="TestPlayer"):
    """Create a mock player character."""
    player = MagicMock()
    player.key = name
    player.has_account = True
    player.msg = MagicMock()
    player.__class__ = type("CombatCharacter", (), {})
    player.__class__.__name__ = "CombatCharacter"
    player.__class__.__module__ = "mygame.typeclasses.characters"
    # Ensure building detection skips players
    player.attributes = _FakeAttrs({})
    return player

def _make_building(building_type="HQ", name="Headquarters", level=1,
                   owner=None):
    """Create a mock building object."""
    bld = MagicMock()
    bld.key = name
    bld.__class__ = type("Building", (), {})
    bld.__class__.__name__ = "Building"
    bld.__class__.__module__ = "mygame.typeclasses.objects"
    bld.has_account = False

    attr_data = {"building_type": building_type, "building_level": level}
    if owner:
        attr_data["owner"] = owner
    bld.attributes = _FakeAttrs(attr_data)
    bld.get_display_abbreviation = lambda: building_type
    bld.building_level = level
    return bld

# -------------------------------------------------------------- #
#  Tests: terrain_type property
# -------------------------------------------------------------- #

class TestTerrainType(unittest.TestCase):
    def test_returns_terrain_tag(self):
        room = _FakeRoom(terrain="Forest")
        self.assertEqual(room.terrain_type, "Forest")

    def test_returns_unknown_when_no_tag(self):
        room = _FakeRoom(terrain="Plains")
        room.tags = _FakeTags({})  # no terrain tag
        self.assertEqual(room.terrain_type, "unknown")

# -------------------------------------------------------------- #
#  Tests: resource_node property
# -------------------------------------------------------------- #

class TestResourceNode(unittest.TestCase):
    def test_returns_resource_data(self):
        data = {"resource_type": "Wood", "depleted": False,
                "respawn_counter": 0}
        room = _FakeRoom(resource_node_data=data)
        self.assertEqual(room.resource_node, data)

    def test_returns_none_when_no_data(self):
        room = _FakeRoom()
        self.assertIsNone(room.resource_node)

# -------------------------------------------------------------- #
#  Tests: building property
# -------------------------------------------------------------- #

class TestBuildingProperty(unittest.TestCase):
    def test_returns_building_object(self):
        bld = _make_building("MM", "Mill")
        room = _FakeRoom(contents=[bld])
        self.assertIs(room.building, bld)

    def test_returns_none_when_no_building(self):
        room = _FakeRoom(contents=[])
        self.assertIsNone(room.building)

    def test_skips_player_characters(self):
        player = _make_player("Alice")
        room = _FakeRoom(contents=[player])
        self.assertIsNone(room.building)

# -------------------------------------------------------------- #
#  Tests: planet_name property
# -------------------------------------------------------------- #

class TestPlanetName(unittest.TestCase):
    def test_returns_planet_attribute(self):
        room = _FakeRoom(planet="earth_1")
        self.assertEqual(room.planet_name, "earth_1")

    def test_returns_unknown_when_no_planet_attribute(self):
        room = _FakeRoom()
        room.attributes = _FakeAttrs({"x": 0, "y": 0})  # no planet key
        self.assertEqual(room.planet_name, "unknown")

# -------------------------------------------------------------- #
#  Tests: get_display_symbol (Requirement 1.8)
# -------------------------------------------------------------- #

class TestGetDisplaySymbol(unittest.TestCase):
    """Display priority: @@ (self) > ** (other player) > building > terrain."""

    def test_self_indicator_highest_priority(self):
        looker = _make_player("Looker")
        room = _FakeRoom(terrain="Plains", contents=[looker])
        self.assertEqual(room.get_display_symbol(looker), "@@")

    def test_other_player_indicator(self):
        looker = _make_player("Looker")
        other = _make_player("Other")
        room = _FakeRoom(terrain="Plains", contents=[other])
        self.assertEqual(room.get_display_symbol(looker), "**")

    def test_self_takes_priority_over_other_player(self):
        looker = _make_player("Looker")
        other = _make_player("Other")
        room = _FakeRoom(terrain="Plains", contents=[looker, other])
        self.assertEqual(room.get_display_symbol(looker), "@@")

    def test_building_when_no_players(self):
        bld = _make_building("VV", "Turret")
        room = _FakeRoom(terrain="Plains", contents=[bld])
        self.assertEqual(room.get_display_symbol(_make_player("Looker")), "VV")

    def test_player_takes_priority_over_building(self):
        other = _make_player("Other")
        bld = _make_building("HQ", "Headquarters")
        room = _FakeRoom(terrain="Plains", contents=[other, bld])
        self.assertEqual(room.get_display_symbol(_make_player("Looker")), "**")

    @patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
           return_value="PP")
    def test_terrain_when_empty(self, _mock):
        room = _FakeRoom(terrain="Plains", contents=[])
        self.assertEqual(room.get_display_symbol(_make_player("Looker")), "PP")

# -------------------------------------------------------------- #
#  Tests: get_structured_state (Requirement 27.1)
# -------------------------------------------------------------- #

class TestGetStructuredState(unittest.TestCase):
    def test_empty_tile(self):
        room = _FakeRoom(terrain="Rock", contents=[])
        state = room.get_structured_state()
        self.assertEqual(state["terrain_type"], "Rock")
        self.assertIsNone(state["resource_node"])
        self.assertIsNone(state["building"])
        self.assertEqual(state["players"], [])

    def test_tile_with_resource_node(self):
        rn = {"resource_type": "Iron", "depleted": False,
              "respawn_counter": 0}
        room = _FakeRoom(terrain="Mountain", resource_node_data=rn)
        state = room.get_structured_state()
        self.assertEqual(state["resource_node"]["resource_type"], "Iron")
        self.assertFalse(state["resource_node"]["depleted"])

    def test_tile_with_building(self):
        bld = _make_building("MM", "Mill", level=3, owner="player1")
        room = _FakeRoom(terrain="Plains", contents=[bld])
        state = room.get_structured_state()
        self.assertIsNotNone(state["building"])
        self.assertEqual(state["building"]["type"], "MM")
        self.assertEqual(state["building"]["name"], "Mill")
        self.assertEqual(state["building"]["level"], 3)

    def test_tile_with_players(self):
        p1 = _make_player("Alice")
        p2 = _make_player("Bob")
        room = _FakeRoom(terrain="Forest", contents=[p1, p2])
        state = room.get_structured_state()
        self.assertEqual(sorted(state["players"]), ["Alice", "Bob"])

    def test_full_tile(self):
        p = _make_player("Alice")
        bld = _make_building("HQ", "Headquarters")
        rn = {"resource_type": "Straw", "depleted": True,
              "respawn_counter": 5}
        room = _FakeRoom(terrain="Plains", resource_node_data=rn,
                         contents=[p, bld])
        state = room.get_structured_state()
        self.assertEqual(state["terrain_type"], "Plains")
        self.assertTrue(state["resource_node"]["depleted"])
        self.assertIsNotNone(state["building"])
        self.assertIn("Alice", state["players"])

# -------------------------------------------------------------- #
#  Tests: at_object_receive (Requirement 1.5)
# -------------------------------------------------------------- #

class TestAtObjectReceive(unittest.TestCase):
    def test_player_receives_tile_info(self):
        player = _make_player("Alice")
        room = _FakeRoom(terrain="Forest", contents=[player])
        room.at_object_receive(player, None)
        player.msg.assert_called_once()
        msg = player.msg.call_args[0][0]
        self.assertIn("Forest", msg)

    def test_non_player_does_not_receive_info(self):
        obj = MagicMock()
        obj.has_account = False
        room = _FakeRoom(terrain="Plains")
        room.at_object_receive(obj, None)
        obj.msg.assert_not_called()

    def test_shows_resource_info(self):
        player = _make_player("Alice")
        rn = {"resource_type": "Wood", "depleted": False,
              "respawn_counter": 0}
        room = _FakeRoom(terrain="Forest", resource_node_data=rn,
                         contents=[player])
        room.at_object_receive(player, None)
        msg = player.msg.call_args[0][0]
        self.assertIn("Wood", msg)

    def test_shows_depleted_resource(self):
        player = _make_player("Alice")
        rn = {"resource_type": "Iron", "depleted": True,
              "respawn_counter": 10}
        room = _FakeRoom(terrain="Mountain", resource_node_data=rn,
                         contents=[player])
        room.at_object_receive(player, None)
        msg = player.msg.call_args[0][0]
        self.assertIn("depleted", msg)

    def test_shows_other_players(self):
        alice = _make_player("Alice")
        bob = _make_player("Bob")
        room = _FakeRoom(terrain="Plains", contents=[alice, bob])
        room.at_object_receive(alice, None)
        msg = alice.msg.call_args[0][0]
        self.assertIn("Bob", msg)

if __name__ == "__main__":
    unittest.main()
