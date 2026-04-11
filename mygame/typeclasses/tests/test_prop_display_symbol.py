"""
Property-based tests for tile display symbol priority.

**Validates: Requirements 1.8**

Property 1: Tile display symbol priority
    For any overworld tile with any combination of players, buildings,
    and terrain, the 2-char display symbol SHALL follow the priority:
    player indicator ("@@"/"**") > building abbreviation > terrain symbol.
    If a player is present, the symbol must be a player indicator
    regardless of other contents.

Uses the same Evennia stub approach as the unit tests in
test_overworld_room.py.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules (same as unit test file)
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
#  Helpers (same as unit test file)
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
        self._data = data or {}

    def get(self, category=None, return_list=False, **kw):
        return self._data.get(category)

class _FakeRoom(OverworldRoom):
    """Concrete stand-in that bypasses Evennia DB layer."""

    def __init__(self, terrain="Plains", x=5, y=10, planet="earth_1",
                 contents=None, resource_node_data=None):
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
#  Known terrain types and their 2-char map symbols
# -------------------------------------------------------------- #

TERRAIN_SYMBOLS = {
    "Plains": "PP",
    "Dirt": "~~",
    "Forest": "FF",
    "Rock": "RR",
    "Mountain": "MT",
    "Power_Grid": "GG",
    "Scrapyard": "SS",
    "Circuit_Field": "CC",
    "Ruins": "UU",
}

TERRAIN_TYPES = list(TERRAIN_SYMBOLS.keys())

# Building abbreviations from the game definitions
BUILDING_ABBRS = ["HQ", "MM", "QQ", "II", "LL", "KK", "AA", "AR", "VV", "TL", "HV"]

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

terrain_strategy = st.sampled_from(TERRAIN_TYPES)
building_abbr_strategy = st.sampled_from(BUILDING_ABBRS)
player_count_strategy = st.integers(min_value=0, max_value=3)
building_present_strategy = st.booleans()
player_name_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=8
).map(lambda s: s.capitalize())

@st.composite
def tile_state_strategy(draw):
    """Generate a random tile state: terrain, players, optional building."""
    terrain = draw(terrain_strategy)
    num_players = draw(player_count_strategy)
    has_building = draw(building_present_strategy)

    # Generate unique player names
    names = set()
    while len(names) < num_players:
        names.add(draw(player_name_strategy))
    players = [_make_player(n) for n in names]

    building = None
    if has_building:
        abbr = draw(building_abbr_strategy)
        building = _make_building(building_type=abbr, name=f"Bld_{abbr}")

    return {
        "terrain": terrain,
        "players": players,
        "building": building,
    }

# -------------------------------------------------------------- #
#  Property test
# -------------------------------------------------------------- #

class TestTileDisplaySymbolPriority(unittest.TestCase):
    """
    Feature: rts-combat-overworld
    Property 1: Tile display symbol priority

    **Validates: Requirements 1.8**
    """

    @given(state=tile_state_strategy())
    @settings(max_examples=100)
    def test_symbol_always_two_chars(self, state):
        """The display symbol is always exactly 2 characters."""
        contents = list(state["players"])
        if state["building"]:
            contents.append(state["building"])

        room = _FakeRoom(terrain=state["terrain"], contents=contents)
        looker = _make_player("Looker")

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[state["terrain"]]):
            symbol = room.get_display_symbol(looker)

        self.assertEqual(len(symbol), 2,
                         f"Symbol '{symbol}' is not 2 chars for state: "
                         f"terrain={state['terrain']}, "
                         f"players={len(state['players'])}, "
                         f"building={state['building'] is not None}")

    @given(state=tile_state_strategy())
    @settings(max_examples=100)
    def test_self_on_tile_always_returns_at_at(self, state):
        """When the looker is on the tile, symbol is always '@@'."""
        looker = _make_player("Looker")
        contents = [looker] + list(state["players"])
        if state["building"]:
            contents.append(state["building"])

        room = _FakeRoom(terrain=state["terrain"], contents=contents)

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[state["terrain"]]):
            symbol = room.get_display_symbol(looker)

        self.assertEqual(symbol, "@@",
                         f"Expected '@@' when looker is on tile, got '{symbol}'")

    @given(state=tile_state_strategy())
    @settings(max_examples=100)
    def test_other_player_returns_star_star(self, state):
        """When another player is on the tile (not the looker), symbol is '**'."""
        assume(len(state["players"]) >= 1)

        looker = _make_player("Looker")
        # Looker is NOT in contents — only other players
        contents = list(state["players"])
        if state["building"]:
            contents.append(state["building"])

        room = _FakeRoom(terrain=state["terrain"], contents=contents)

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[state["terrain"]]):
            symbol = room.get_display_symbol(looker)

        self.assertEqual(symbol, "**",
                         f"Expected '**' when other player present, got '{symbol}'")

    @given(terrain=terrain_strategy, abbr=building_abbr_strategy)
    @settings(max_examples=100)
    def test_building_only_returns_abbreviation(self, terrain, abbr):
        """When only a building is on the tile (no players), symbol is the building abbreviation."""
        building = _make_building(building_type=abbr, name=f"Bld_{abbr}")
        room = _FakeRoom(terrain=terrain, contents=[building])
        looker = _make_player("Looker")

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[terrain]):
            symbol = room.get_display_symbol(looker)

        self.assertEqual(symbol, abbr,
                         f"Expected building abbr '{abbr}', got '{symbol}'")

    @given(terrain=terrain_strategy)
    @settings(max_examples=100)
    def test_empty_tile_returns_terrain_symbol(self, terrain):
        """When the tile is empty, symbol is the terrain symbol."""
        room = _FakeRoom(terrain=terrain, contents=[])
        looker = _make_player("Looker")

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[terrain]):
            symbol = room.get_display_symbol(looker)

        self.assertEqual(symbol, TERRAIN_SYMBOLS[terrain],
                         f"Expected terrain symbol '{TERRAIN_SYMBOLS[terrain]}', "
                         f"got '{symbol}'")

    @given(state=tile_state_strategy())
    @settings(max_examples=100)
    def test_strict_priority_ordering(self, state):
        """Priority is strictly: self > other player > building > terrain."""
        looker = _make_player("Looker")
        looker_on_tile = True  # We'll test with looker present

        contents = [looker] + list(state["players"])
        if state["building"]:
            contents.append(state["building"])

        room = _FakeRoom(terrain=state["terrain"], contents=contents)

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[state["terrain"]]):
            symbol = room.get_display_symbol(looker)

        # With looker on tile, self always wins
        self.assertEqual(symbol, "@@")

        # Now remove looker, check other player > building > terrain
        contents_no_looker = list(state["players"])
        if state["building"]:
            contents_no_looker.append(state["building"])

        room2 = _FakeRoom(terrain=state["terrain"], contents=contents_no_looker)

        with patch("mygame.typeclasses.rooms.OverworldRoom._terrain_symbol",
                   return_value=TERRAIN_SYMBOLS[state["terrain"]]):
            symbol2 = room2.get_display_symbol(looker)

        if state["players"]:
            self.assertEqual(symbol2, "**",
                             "Other player should take priority over building/terrain")
        elif state["building"]:
            self.assertEqual(symbol2, state["building"].get_display_abbreviation(),
                             "Building should take priority over terrain")
        else:
            self.assertEqual(symbol2, TERRAIN_SYMBOLS[state["terrain"]],
                             "Empty tile should show terrain symbol")

if __name__ == "__main__":
    unittest.main()
