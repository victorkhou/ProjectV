"""
Property-based test for map display priority.

**Property 19: Map Display Priority**
For any visible tile containing multiple entities (player, agents, building),
the rendered symbol SHALL follow the priority order:
1. Player self -> @@ yellow
2. Enemy player -> ** red
3. Own agent (overworld) -> role abbreviation green
4. Enemy agent (overworld) -> ag red
5. Neutral NPC -> ag yellow
6. Occupied building (entity inside) -> building abbreviation dark blue
7. Unoccupied own building -> building abbreviation cyan
8. Unoccupied enemy building -> building abbreviation dark red
9. Terrain symbol

**Validates: Requirements 19.6, 19.5, 19.8**
"""

import unittest

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.coordinate.procedural_map_renderer import (
    ProceduralMapRenderer,
    _agent_symbol,
    _ROLE_SYMBOLS,
)


# ------------------------------------------------------------------ #
#  Fake objects to simulate Evennia room contents
# ------------------------------------------------------------------ #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        return None


class FakeTags:
    """Simulates Evennia's tag handler."""
    def __init__(self, tags=None):
        self._tags = tags or {}  # category -> list of tag values

    def get(self, key=None, category=None):
        if category:
            return self._tags.get(category, [])
        return []


class FakeAttributes:
    """Simulates Evennia's attribute handler."""
    def __init__(self, **kwargs):
        self._data = kwargs

    def get(self, key, default=None):
        return self._data.get(key, default)


class FakePlayer:
    """Simulates a player character on the map."""
    def __init__(self, player_id=1, x=5, y=5):
        self.id = player_id
        self.has_account = True
        self.db = FakeDB(coord_x=x, coord_y=y)
        self.tags = FakeTags()


class FakeNPC:
    """Simulates an NPC (agent/enemy/neutral) on the map."""
    def __init__(self, owner=None, role="", npc_type="agent"):
        self.has_account = False
        self.db = FakeDB(owner=owner, role=role, npc_type=npc_type)
        self.tags = FakeTags({"npc_type": [npc_type]})


class FakeBuilding:
    """Simulates a building object attached to a room."""
    def __init__(self, abbreviation="HQ", owner=None, contents=None):
        self._abbreviation = abbreviation
        self.attributes = FakeAttributes(
            building_type=abbreviation,
            owner=owner,
        )
        self.contents = contents or []

    def get_display_abbreviation(self):
        return self._abbreviation


class FakeRoom:
    """Simulates an Evennia room tile with contents and optional building."""
    def __init__(self, contents=None, building=None):
        self.contents = contents or []
        self.building = building


# ------------------------------------------------------------------ #
#  Minimal renderer for testing _colored_room()
# ------------------------------------------------------------------ #

def _make_renderer():
    """Create a minimal ProceduralMapRenderer for testing _colored_room().

    We only need _colored_terrain() to work for the terrain fallback case.
    We stub it to return a fixed terrain string.
    """
    renderer = object.__new__(ProceduralMapRenderer)
    renderer._tile_resolver = None
    renderer._fog_system = None
    renderer._terrain_generators = {"terra": None}
    renderer._data_registry = None
    renderer._symbol_cache = {}
    return renderer


# Patch _colored_terrain to return a fixed terrain symbol for tests
_TERRAIN_FALLBACK = "|g..|n"


def _patched_colored_terrain(self, x, y, planet):
    return _TERRAIN_FALLBACK


# Apply the patch
ProceduralMapRenderer._colored_terrain_original = ProceduralMapRenderer._colored_terrain
ProceduralMapRenderer._colored_terrain = _patched_colored_terrain


# ------------------------------------------------------------------ #
#  Hypothesis strategies
# ------------------------------------------------------------------ #

ROLES = list(_ROLE_SYMBOLS.keys())
role_st = st.sampled_from(ROLES)
building_abbr_st = st.sampled_from(["HQ", "EX", "AC", "LB", "AR", "TU", "VT", "RD", "WL", "BK", "MB", "RL"])

# Entity presence flags
entity_flags_st = st.fixed_dictionaries({
    "has_self": st.booleans(),
    "has_enemy_player": st.booleans(),
    "has_own_agent": st.booleans(),
    "has_enemy_agent": st.booleans(),
    "has_neutral_npc": st.booleans(),
    "has_building": st.booleans(),
    "building_occupied": st.booleans(),
    "building_is_own": st.booleans(),
    "own_agent_role": role_st,
    "building_abbr": building_abbr_st,
})


def _build_room(flags, looker):
    """Build a FakeRoom from entity presence flags."""
    contents = []
    building = None

    # Add player self
    if flags["has_self"]:
        contents.append(looker)

    # Add enemy player
    if flags["has_enemy_player"]:
        enemy = FakePlayer(player_id=999, x=5, y=5)
        contents.append(enemy)

    # Add own agent (overworld — not inside building)
    if flags["has_own_agent"]:
        own_agent = FakeNPC(owner=looker, role=flags["own_agent_role"])
        contents.append(own_agent)

    # Add enemy agent (overworld)
    if flags["has_enemy_agent"]:
        enemy_owner = FakePlayer(player_id=888)
        enemy_agent = FakeNPC(owner=enemy_owner, role="soldier")
        contents.append(enemy_agent)

    # Add neutral NPC
    if flags["has_neutral_npc"]:
        neutral = FakeNPC(owner=None, role="")
        contents.append(neutral)

    # Add building
    if flags["has_building"]:
        bld_owner = looker if flags["building_is_own"] else FakePlayer(player_id=777)
        bld_contents = []
        if flags["building_occupied"]:
            # Put an NPC inside the building to make it occupied
            occupant = FakeNPC(owner=looker, role="harvester", npc_type="agent")
            bld_contents.append(occupant)
        building = FakeBuilding(
            abbreviation=flags["building_abbr"],
            owner=bld_owner,
            contents=bld_contents,
        )

    return FakeRoom(contents=contents, building=building)


def _expected_symbol(flags, looker):
    """Compute the expected rendered symbol based on priority order."""
    abbr = flags["building_abbr"]

    # Priority 1: Player self
    if flags["has_self"]:
        return "|Y@@|n"

    # Priority 2: Enemy player
    if flags["has_enemy_player"]:
        return "|r**|n"

    # Priority 3: Own agent (overworld)
    if flags["has_own_agent"]:
        sym = _agent_symbol(flags["own_agent_role"])
        return f"|g{sym}|n"

    # Priority 4: Enemy agent (overworld)
    if flags["has_enemy_agent"]:
        return "|rag|n"

    # Priority 5: Neutral NPC
    if flags["has_neutral_npc"]:
        return "|yag|n"

    # Priority 6: Occupied building
    if flags["has_building"] and flags["building_occupied"]:
        return f"|B{abbr}|n"

    # Priority 7: Unoccupied own building
    if flags["has_building"] and flags["building_is_own"]:
        return f"|c{abbr}|n"

    # Priority 8: Unoccupied enemy building
    if flags["has_building"] and not flags["building_is_own"]:
        return f"|R{abbr}|n"

    # Priority 9: Terrain
    return _TERRAIN_FALLBACK


# ================================================================== #
#  Property 19: Map Display Priority
#  **Validates: Requirements 19.6, 19.5, 19.8**
# ================================================================== #

class TestProperty19MapDisplayPriority(unittest.TestCase):
    """Property 19: Map Display Priority.

    For any visible tile containing multiple entities (player, agents,
    building), the rendered symbol SHALL follow the priority order:
    player self > enemy player > own agent > enemy agent > neutral NPC >
    occupied building > unoccupied building > terrain.

    **Validates: Requirements 19.6, 19.5, 19.8**
    """

    @given(flags=entity_flags_st)
    @settings(max_examples=200)
    def test_display_priority_order(self, flags):
        """Rendered symbol matches the highest-priority entity present."""
        # Need at least one entity or terrain
        looker = FakePlayer(player_id=1, x=5, y=5)
        renderer = _make_renderer()

        room = _build_room(flags, looker)
        expected = _expected_symbol(flags, looker)

        actual = renderer._colored_room(room, looker, 5, 5, "terra")

        self.assertEqual(
            actual, expected,
            f"Display priority mismatch.\n"
            f"  Flags: self={flags['has_self']}, enemy_player={flags['has_enemy_player']}, "
            f"own_agent={flags['has_own_agent']}(role={flags['own_agent_role']}), "
            f"enemy_agent={flags['has_enemy_agent']}, neutral={flags['has_neutral_npc']}, "
            f"building={flags['has_building']}(abbr={flags['building_abbr']}, "
            f"occupied={flags['building_occupied']}, own={flags['building_is_own']})\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}"
        )

    @given(role=role_st)
    @settings(max_examples=100)
    def test_own_agent_uses_role_abbreviation(self, role):
        """Own agents on overworld render with their role abbreviation in green."""
        looker = FakePlayer(player_id=1, x=5, y=5)
        renderer = _make_renderer()

        own_agent = FakeNPC(owner=looker, role=role)
        room = FakeRoom(contents=[own_agent])

        actual = renderer._colored_room(room, looker, 5, 5, "terra")
        expected_sym = _ROLE_SYMBOLS[role]
        expected = f"|g{expected_sym}|n"

        self.assertEqual(
            actual, expected,
            f"Own agent with role '{role}' should render as '{expected}', got '{actual}'"
        )

    @given(building_abbr=building_abbr_st)
    @settings(max_examples=100)
    def test_occupied_building_renders_dark_blue(self, building_abbr):
        """A building with any entity inside renders in dark blue regardless of owner."""
        looker = FakePlayer(player_id=1, x=5, y=5)
        renderer = _make_renderer()

        occupant = FakeNPC(owner=looker, role="harvester")
        building = FakeBuilding(
            abbreviation=building_abbr,
            owner=looker,
            contents=[occupant],
        )
        room = FakeRoom(contents=[], building=building)

        actual = renderer._colored_room(room, looker, 5, 5, "terra")
        expected = f"|B{building_abbr}|n"

        self.assertEqual(
            actual, expected,
            f"Occupied building '{building_abbr}' should render as '{expected}', got '{actual}'"
        )


if __name__ == "__main__":
    unittest.main()
