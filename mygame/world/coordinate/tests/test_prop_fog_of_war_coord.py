"""
Property-based tests for the coordinate-based Fog of War system.

Property 10: Vision computation is the union of all vision source circles —
For any player position and set of owned building positions, the set of
visible tiles SHALL equal the union of: a circle of radius
player_vision_radius (Chebyshev distance) centered on the player position,
and a circle of radius building_vision_radius centered on each owned
building position.

Property 11: Fog tiles hide enemy players but show discovered buildings —
For any tile in the "fog" visibility state, the rendered symbol SHALL
never show enemy Player_Characters. If the player's Discovery_Memory
contains a building snapshot for that tile, the rendered symbol SHALL
show the building abbreviation.

Property 12: Discovery memory records all visible tiles and enemy building
snapshots — For any set of tiles entering a Player_Character's vision,
all those tiles SHALL be marked as discovered in the Discovery_Memory.
For any visible tile containing an enemy building, a snapshot SHALL be
stored.

**Validates: Requirements 5.6, 5.9, 11.2, 11.3, 11.4, 11.5, 11.6, 11.8**
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

from mygame.world.coordinate.fog_of_war import (  # noqa: E402
    DiscoveredBuildingState,
    FogOfWarSystem,
)
from mygame.world.coordinate.discovery_bitfield import DiscoveryBitfield  # noqa: E402


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _FakeDB:
    """Minimal attribute-bag mimicking Evennia's db handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeBalance:
    """Minimal stand-in for BalanceConfig.

    ``min_vision_radius`` defaults to 0 here (unlike the real BalanceConfig's
    1) so the pure circle-union geometry properties below keep exercising
    radius-0 circles; the terrain-strategy minimum-radius behavior is covered
    by its own tests.
    """
    def __init__(self, pvr=10, bvr=7, min_vision_radius=0):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr
        self.min_vision_radius = min_vision_radius


class _FakePlayer:
    """Lightweight player stand-in."""
    def __init__(self, name="Player1", x=50, y=50, planet="earth"):
        self.key = name
        self.db = _FakeDB(
            coord_x=x,
            coord_y=y,
            coord_planet=planet,
            discovery_memory={},
        )


class _FakeRoom:
    """Lightweight room stand-in."""
    def __init__(self, x=0, y=0, building=None):
        self.x = x
        self.y = y
        self.building = building


class _FakeBuilding:
    """Lightweight building stand-in."""
    def __init__(self, btype="HQ", owner=None, location=None):
        self._btype = btype
        self.owner = owner
        self.location = location
        # Provide db.coord_x/coord_y for _get_building_coords
        self.db = type("_Db", (), {
            "coord_x": location.x if location else None,
            "coord_y": location.y if location else None,
            "owner": owner,
        })()

    def get_display_abbreviation(self):
        return self._btype

    @property
    def attributes(self):
        return self

    def get(self, key, default=None):
        if key == "building_type":
            return self._btype
        if key == "owner":
            return self.owner
        return default


class _FakeTileResolver:
    """Minimal tile resolver that also acts as a fake PlanetRoom for update_discovery."""
    def __init__(self, rooms=None):
        self._rooms = rooms or {}

    def get_if_exists(self, x, y, planet):
        return self._rooms.get((x, y, planet))

    def get_cached(self, x, y, planet):
        return self._rooms.get((x, y, planet))

    def get_buildings_at(self, x, y):
        """PlanetRoom-compatible building query."""
        for (rx, ry, _), room in self._rooms.items():
            if rx == x and ry == y:
                bld = getattr(room, "building", None)
                if bld is not None:
                    return [bld]
        return []


# -------------------------------------------------------------- #
#  Helper: compute expected Chebyshev circle
# -------------------------------------------------------------- #

def _chebyshev_circle(cx, cy, radius):
    """Return the set of (x, y) tiles within Chebyshev distance radius of (cx, cy)."""
    result = set()
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            result.add((cx + dx, cy + dy))
    return result


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

coord_strategy = st.integers(min_value=-200, max_value=200)
small_radius = st.integers(min_value=0, max_value=5)
building_count = st.integers(min_value=0, max_value=4)

building_abbrevs = st.sampled_from(["HQ", "VV", "TL", "AA", "BB"])


@st.composite
def building_positions_strategy(draw, count):
    """Generate a list of (x, y) positions for buildings."""
    positions = []
    for _ in range(count):
        bx = draw(st.integers(min_value=-200, max_value=200))
        by = draw(st.integers(min_value=-200, max_value=200))
        positions.append((bx, by))
    return positions


# -------------------------------------------------------------- #
#  Property 10: Vision computation is the union of all vision
#  source circles
#  **Validates: Requirements 5.9, 11.8**
# -------------------------------------------------------------- #

class TestProperty10VisionComputation(unittest.TestCase):
    """Property 10: Vision computation is the union of all vision source circles.

    For any player position and set of owned building positions, the set
    of visible tiles SHALL equal the union of: a circle of radius
    player_vision_radius (Chebyshev distance) centered on the player
    position, and a circle of radius building_vision_radius centered on
    each owned building position.

    **Validates: Requirements 5.9, 11.8**
    """

    @given(
        px=coord_strategy,
        py=coord_strategy,
        pvr=small_radius,
        bvr=small_radius,
        num_buildings=building_count,
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_visible_tiles_equals_union_of_chebyshev_circles(
        self, px, py, pvr, bvr, num_buildings, data
    ):
        """Visible tiles SHALL equal the union of all vision source circles."""
        # Generate building positions
        building_positions = []
        for _ in range(num_buildings):
            bx = data.draw(st.integers(min_value=-200, max_value=200))
            by = data.draw(st.integers(min_value=-200, max_value=200))
            building_positions.append((bx, by))

        # Set up FogOfWarSystem
        balance = _FakeBalance(pvr=pvr, bvr=bvr)
        fow = FogOfWarSystem(balance)
        player = _FakePlayer(x=px, y=py)

        # Create fake buildings with locations
        fake_buildings = []
        for bx, by in building_positions:
            room = _FakeRoom(x=bx, y=by)
            building = _FakeBuilding(location=room)
            fake_buildings.append(building)

        # Compute actual visible tiles
        actual = fow.get_visible_tiles(player, fake_buildings)

        # Compute expected: union of all Chebyshev circles
        expected = _chebyshev_circle(px, py, pvr)
        for bx, by in building_positions:
            expected |= _chebyshev_circle(bx, by, bvr)

        self.assertEqual(
            actual,
            expected,
            f"Vision mismatch: player=({px},{py}) r={pvr}, "
            f"buildings={building_positions} r={bvr}. "
            f"Extra in actual: {actual - expected}, "
            f"Missing from actual: {expected - actual}",
        )

    @given(
        px=coord_strategy,
        py=coord_strategy,
        pvr=small_radius,
    )
    @settings(max_examples=200)
    def test_player_only_vision_is_single_circle(self, px, py, pvr):
        """With no buildings, visible tiles SHALL be exactly the player circle."""
        balance = _FakeBalance(pvr=pvr, bvr=7)
        fow = FogOfWarSystem(balance)
        player = _FakePlayer(x=px, y=py)

        actual = fow.get_visible_tiles(player, [])
        expected = _chebyshev_circle(px, py, pvr)

        self.assertEqual(actual, expected)

    @given(
        px=coord_strategy,
        py=coord_strategy,
        bx=coord_strategy,
        by=coord_strategy,
        pvr=small_radius,
        bvr=small_radius,
    )
    @settings(max_examples=200)
    def test_single_building_union(self, px, py, bx, by, pvr, bvr):
        """With one building, visible tiles SHALL be the union of player + building circles."""
        balance = _FakeBalance(pvr=pvr, bvr=bvr)
        fow = FogOfWarSystem(balance)
        player = _FakePlayer(x=px, y=py)

        room = _FakeRoom(x=bx, y=by)
        building = _FakeBuilding(location=room)

        actual = fow.get_visible_tiles(player, [building])
        expected = _chebyshev_circle(px, py, pvr) | _chebyshev_circle(bx, by, bvr)

        self.assertEqual(actual, expected)


# -------------------------------------------------------------- #
#  Property 11: Fog tiles hide enemy players but show discovered
#  buildings
#  **Validates: Requirements 5.6, 11.5, 11.6**
# -------------------------------------------------------------- #

class TestProperty11FogHidesEnemies(unittest.TestCase):
    """Property 11: Fog tiles hide enemy players but show discovered buildings.

    For any tile in the "fog" visibility state (previously discovered,
    not currently visible), the rendered symbol SHALL never show enemy
    Player_Characters. If the player's Discovery_Memory contains a
    building snapshot for that tile, the rendered symbol SHALL show the
    building abbreviation.

    **Validates: Requirements 5.6, 11.5, 11.6**
    """

    @given(
        fx=coord_strategy,
        fy=coord_strategy,
    )
    @settings(max_examples=200)
    def test_fog_tile_returns_fog_visibility(self, fx, fy):
        """A discovered tile outside current vision SHALL have visibility 'fog'."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(x=0, y=0)
        # Mark tile as discovered but ensure it's outside current vision
        assume(max(abs(fx), abs(fy)) > 10)  # outside player vision radius of 10
        player.db.discovery_memory = {"discovered": DiscoveryBitfield.from_set({(fx, fy)}).to_dict(), "buildings": {}}

        visible_tiles = fow.get_visible_tiles(player, [])
        visibility = fow.get_tile_visibility(player, fx, fy, visible_tiles)

        self.assertEqual(
            visibility,
            "fog",
            f"Tile ({fx},{fy}) should be 'fog' (discovered but not visible), got '{visibility}'",
        )

    @given(
        fx=coord_strategy,
        fy=coord_strategy,
        abbrev=building_abbrevs,
    )
    @settings(max_examples=200)
    def test_fog_tile_with_discovered_building_returns_snapshot(self, fx, fy, abbrev):
        """A fog tile with a discovered building SHALL return the building snapshot."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(x=0, y=0)
        # Ensure tile is outside vision
        assume(max(abs(fx), abs(fy)) > 10)

        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(fx, fy)}).to_dict(),
            "buildings": {
                (fx, fy): {
                    "building_type": abbrev,
                    "owner_name": "Enemy1",
                    "x": fx,
                    "y": fy,
                }
            },
        }

        visible_tiles = fow.get_visible_tiles(player, [])
        # Confirm it's fog
        visibility = fow.get_tile_visibility(player, fx, fy, visible_tiles)
        self.assertEqual(visibility, "fog")

        # Get discovered buildings — should return the snapshot
        buildings = fow.get_discovered_buildings(player, fx, fy)
        self.assertEqual(len(buildings), 1)
        self.assertIsInstance(buildings[0], DiscoveredBuildingState)
        self.assertEqual(buildings[0].building_type, abbrev)
        self.assertEqual(buildings[0].owner_name, "Enemy1")

    @given(
        fx=coord_strategy,
        fy=coord_strategy,
    )
    @settings(max_examples=200)
    def test_fog_tile_without_building_returns_empty(self, fx, fy):
        """A fog tile without a discovered building SHALL return no building snapshots."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(x=0, y=0)
        assume(max(abs(fx), abs(fy)) > 10)

        player.db.discovery_memory = {"discovered": DiscoveryBitfield.from_set({(fx, fy)}).to_dict(), "buildings": {}}

        visible_tiles = fow.get_visible_tiles(player, [])
        visibility = fow.get_tile_visibility(player, fx, fy, visible_tiles)
        self.assertEqual(visibility, "fog")

        buildings = fow.get_discovered_buildings(player, fx, fy)
        self.assertEqual(buildings, [])

    @given(
        fx=coord_strategy,
        fy=coord_strategy,
    )
    @settings(max_examples=200)
    def test_unexplored_tile_has_no_building_data(self, fx, fy):
        """An unexplored tile SHALL return 'unexplored' and no building snapshots."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(x=0, y=0)
        assume(max(abs(fx), abs(fy)) > 10)

        player.db.discovery_memory = {"discovered": {}, "buildings": {}}

        visible_tiles = fow.get_visible_tiles(player, [])
        visibility = fow.get_tile_visibility(player, fx, fy, visible_tiles)
        self.assertEqual(visibility, "unexplored")

        buildings = fow.get_discovered_buildings(player, fx, fy)
        self.assertEqual(buildings, [])


# -------------------------------------------------------------- #
#  Property 12: Discovery memory records all visible tiles and
#  enemy building snapshots
#  **Validates: Requirements 11.2, 11.3, 11.4**
# -------------------------------------------------------------- #

class TestProperty12DiscoveryMemory(unittest.TestCase):
    """Property 12: Discovery memory records all visible tiles and enemy building snapshots.

    For any set of tiles entering a Player_Character's vision, all those
    tiles SHALL be marked as discovered in the Discovery_Memory. For any
    visible tile containing an enemy building, a snapshot SHALL be stored.

    **Validates: Requirements 11.2, 11.3, 11.4**
    """

    @given(
        px=st.integers(min_value=0, max_value=50),
        py=st.integers(min_value=0, max_value=50),
        pvr=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=200)
    def test_all_visible_tiles_marked_discovered(self, px, py, pvr):
        """All tiles in the visible set SHALL be marked as discovered after update."""
        fow = FogOfWarSystem(_FakeBalance(pvr=pvr, bvr=1))
        player = _FakePlayer(x=px, y=py, planet="earth")
        resolver = _FakeTileResolver()

        visible = fow.get_visible_tiles(player, [])
        fow.update_discovery(player, visible, resolver)

        discovered = fow.get_discovered_tile_set(player)

        for tile in visible:
            self.assertIn(
                tile,
                discovered,
                f"Tile {tile} should be in discovered set after update_discovery",
            )

    @given(
        px=st.integers(min_value=0, max_value=50),
        py=st.integers(min_value=0, max_value=50),
        bx_offset=st.integers(min_value=-2, max_value=2),
        by_offset=st.integers(min_value=-2, max_value=2),
        abbrev=building_abbrevs,
    )
    @settings(max_examples=200)
    def test_enemy_building_snapshot_stored(self, px, py, bx_offset, by_offset, abbrev):
        """Visible tiles with enemy buildings SHALL have snapshots stored."""
        fow = FogOfWarSystem(_FakeBalance(pvr=3, bvr=1))
        player = _FakePlayer(name="Player1", x=px, y=py, planet="earth")
        enemy = _FakePlayer(name="Enemy1")

        # Place enemy building within player vision
        bx = px + bx_offset
        by = py + by_offset
        building_room = _FakeRoom(x=bx, y=by)
        enemy_building = _FakeBuilding(btype=abbrev, owner=enemy, location=building_room)
        building_room.building = enemy_building

        resolver = _FakeTileResolver({(bx, by, "earth"): building_room})

        visible = fow.get_visible_tiles(player, [])
        # The building tile should be within vision (offset <= 2, radius = 3)
        assume((bx, by) in visible)

        fow.update_discovery(player, visible, resolver)

        mem = player.db.discovery_memory
        buildings_mem = mem.get("buildings", {})

        self.assertIn(
            (bx, by),
            buildings_mem,
            f"Enemy building at ({bx},{by}) should have a snapshot in discovery memory",
        )
        snap = buildings_mem[(bx, by)]
        self.assertEqual(snap["building_type"], abbrev)
        self.assertEqual(snap["owner_name"], "Enemy1")
        self.assertEqual(snap["x"], bx)
        self.assertEqual(snap["y"], by)

    @given(
        px=st.integers(min_value=0, max_value=50),
        py=st.integers(min_value=0, max_value=50),
        pvr=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=200)
    def test_own_building_not_snapshotted(self, px, py, pvr):
        """Own buildings SHALL NOT be stored as enemy snapshots."""
        fow = FogOfWarSystem(_FakeBalance(pvr=pvr, bvr=1))
        player = _FakePlayer(name="Player1", x=px, y=py, planet="earth")

        # Place own building at player position
        building_room = _FakeRoom(x=px, y=py)
        own_building = _FakeBuilding(btype="HQ", owner=player, location=building_room)
        building_room.building = own_building

        resolver = _FakeTileResolver({(px, py, "earth"): building_room})

        visible = fow.get_visible_tiles(player, [])
        fow.update_discovery(player, visible, resolver)

        mem = player.db.discovery_memory
        buildings_mem = mem.get("buildings", {})

        self.assertNotIn(
            (px, py),
            buildings_mem,
            f"Own building at ({px},{py}) should NOT be in enemy building snapshots",
        )

    @given(
        px=st.integers(min_value=0, max_value=50),
        py=st.integers(min_value=0, max_value=50),
        pvr=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=200)
    def test_discovery_is_cumulative(self, px, py, pvr):
        """Discovery memory SHALL accumulate across multiple update_discovery calls."""
        fow = FogOfWarSystem(_FakeBalance(pvr=pvr, bvr=1))
        player = _FakePlayer(x=px, y=py, planet="earth")
        resolver = _FakeTileResolver()

        # First update at position (px, py)
        visible1 = fow.get_visible_tiles(player, [])
        fow.update_discovery(player, visible1, resolver)

        # Move player and update again
        new_x = px + pvr * 2 + 1  # far enough that circles don't overlap
        player.db.coord_x = new_x
        visible2 = fow.get_visible_tiles(player, [])
        fow.update_discovery(player, visible2, resolver)

        discovered = fow.get_discovered_tile_set(player)

        # All tiles from both updates should be discovered
        for tile in visible1:
            self.assertIn(tile, discovered, f"Tile {tile} from first update should still be discovered")
        for tile in visible2:
            self.assertIn(tile, discovered, f"Tile {tile} from second update should be discovered")


if __name__ == "__main__":
    unittest.main()
