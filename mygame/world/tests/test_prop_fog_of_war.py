"""
Property-based tests for Fog of War filtering.

Property 2: Fog of War filtering — tiles outside sight range hide
enemy players/buildings.

Validates: Requirements 1.9
"""

import sys
import types
import unittest

from hypothesis import given, settings, assume
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

from mygame.world.map_renderer import ASCIIMapRenderer  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
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

class FakePlayer:
    """Lightweight stand-in for a player."""

    def __init__(self, name="Looker"):
        self.key = name
        self.id = id(self)
        self.is_player = True

class FakeBuilding:
    """Lightweight stand-in for a building."""

    def __init__(self, abbreviation="VV"):
        self._abbreviation = abbreviation

    def get_display_abbreviation(self):
        return self._abbreviation

class FakeTile:
    """Lightweight stand-in for a tile."""

    def __init__(self, terrain_type="Plains", players=None, building=None):
        self.terrain_type = terrain_type
        self.players = players or []
        self.building = building

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def sight_range_strategy(draw):
    """Generate a sight range (1-10)."""
    return draw(st.integers(min_value=1, max_value=10))

@st.composite
def terrain_strategy(draw):
    """Generate a random terrain type."""
    return draw(st.sampled_from(TERRAIN_TYPES))

@st.composite
def offset_outside_range_strategy(draw, sight_range):
    """Generate an (dx, dy) offset that is outside the sight range."""
    # At least one of dx, dy must have abs > sight_range
    dx = draw(st.integers(min_value=-(sight_range + 5), max_value=sight_range + 5))
    dy = draw(st.integers(min_value=-(sight_range + 5), max_value=sight_range + 5))
    assume(max(abs(dx), abs(dy)) > sight_range)
    return dx, dy

@st.composite
def offset_inside_range_strategy(draw, sight_range):
    """Generate an (dx, dy) offset that is inside the sight range."""
    dx = draw(st.integers(min_value=-sight_range, max_value=sight_range))
    dy = draw(st.integers(min_value=-sight_range, max_value=sight_range))
    return dx, dy

# -------------------------------------------------------------- #
#  Property 2: Fog of War filtering
#  **Validates: Requirements 1.9**
# -------------------------------------------------------------- #

class TestProperty2FogOfWarFiltering(unittest.TestCase):
    """Property 2: Fog of War filtering.

    For any player position and sight range, tiles outside the sight
    range SHALL show only their terrain symbol, hiding all enemy
    players and enemy buildings.

    **Validates: Requirements 1.9**
    """

    @given(
        terrain=terrain_strategy(),
        sight_range=sight_range_strategy(),
    )
    @settings(max_examples=100)
    def test_tile_outside_range_shows_only_terrain(self, terrain, sight_range):
        """Tiles outside sight range show only terrain, hiding players/buildings."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")
        enemy = FakePlayer("Enemy")
        building = FakeBuilding("VV")

        # Tile with enemy player and building, outside sight range
        tile = FakeTile(
            terrain_type=terrain,
            players=[enemy],
            building=building,
        )

        # in_sight=False should return only terrain symbol
        symbol = renderer.get_tile_symbol(tile, looker, in_sight=False)
        expected = TERRAIN_SYMBOLS[terrain]
        self.assertEqual(
            symbol, expected,
            f"Outside sight range, tile should show terrain '{expected}', "
            f"got '{symbol}'",
        )

    @given(
        terrain=terrain_strategy(),
        sight_range=sight_range_strategy(),
    )
    @settings(max_examples=100)
    def test_tile_inside_range_shows_enemy_player(self, terrain, sight_range):
        """Tiles inside sight range show enemy players."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")
        enemy = FakePlayer("Enemy")

        tile = FakeTile(terrain_type=terrain, players=[enemy])

        symbol = renderer.get_tile_symbol(tile, looker, in_sight=True)
        self.assertEqual(
            symbol, "**",
            f"Inside sight range with enemy, should show '**', got '{symbol}'",
        )

    @given(
        terrain=terrain_strategy(),
        sight_range=sight_range_strategy(),
    )
    @settings(max_examples=100)
    def test_tile_inside_range_shows_building(self, terrain, sight_range):
        """Tiles inside sight range show building abbreviation when no players."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")
        building = FakeBuilding("HQ")

        tile = FakeTile(terrain_type=terrain, building=building)

        symbol = renderer.get_tile_symbol(tile, looker, in_sight=True)
        self.assertEqual(
            symbol, "HQ",
            f"Inside sight range with building, should show 'HQ', got '{symbol}'",
        )

    @given(
        terrain=terrain_strategy(),
    )
    @settings(max_examples=100)
    def test_tile_with_looker_shows_self_symbol(self, terrain):
        """Tile containing the looker shows '@@' regardless."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")

        tile = FakeTile(terrain_type=terrain, players=[looker])

        symbol = renderer.get_tile_symbol(tile, looker, in_sight=True)
        self.assertEqual(
            symbol, "@@",
            f"Tile with looker should show '@@', got '{symbol}'",
        )

    @given(
        terrain=terrain_strategy(),
    )
    @settings(max_examples=100)
    def test_display_priority_player_over_building(self, terrain):
        """Player indicator takes priority over building abbreviation."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")
        enemy = FakePlayer("Enemy")
        building = FakeBuilding("VV")

        tile = FakeTile(
            terrain_type=terrain,
            players=[enemy],
            building=building,
        )

        symbol = renderer.get_tile_symbol(tile, looker, in_sight=True)
        self.assertEqual(
            symbol, "**",
            f"Player should take priority over building, got '{symbol}'",
        )

    @given(
        terrain=terrain_strategy(),
    )
    @settings(max_examples=100)
    def test_display_priority_self_over_enemy(self, terrain):
        """Self indicator '@@' takes priority over enemy '**'."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")
        enemy = FakePlayer("Enemy")

        tile = FakeTile(
            terrain_type=terrain,
            players=[looker, enemy],
        )

        symbol = renderer.get_tile_symbol(tile, looker, in_sight=True)
        self.assertEqual(
            symbol, "@@",
            f"Self '@@' should take priority over enemy '**', got '{symbol}'",
        )

    @given(
        terrain=terrain_strategy(),
        sight_range=sight_range_strategy(),
    )
    @settings(max_examples=100)
    def test_fog_hides_building_outside_range(self, terrain, sight_range):
        """Buildings outside sight range are hidden by fog."""
        renderer = ASCIIMapRenderer()
        looker = FakePlayer("Looker")
        building = FakeBuilding("AA")

        tile = FakeTile(terrain_type=terrain, building=building)

        symbol = renderer.get_tile_symbol(tile, looker, in_sight=False)
        expected = TERRAIN_SYMBOLS[terrain]
        self.assertEqual(
            symbol, expected,
            f"Building should be hidden outside range, got '{symbol}'",
        )

if __name__ == "__main__":
    unittest.main()
