"""
Unit tests for FogOfWarSystem.

Tests visibility computation, tile visibility classification,
discovery memory updates, and discovered building retrieval.
"""

import sys
import types

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules before any game imports
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

import pytest  # noqa: E402

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
    """Minimal stand-in for BalanceConfig."""
    def __init__(self, pvr=10, bvr=7):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr


class _FakeEquipment:
    """Minimal equipment handler exposing get_stat_total."""
    def __init__(self, **stats):
        self._stats = stats

    def get_stat_total(self, stat_name):
        return self._stats.get(stat_name, 0.0)


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
    """Minimal tile resolver that returns pre-configured rooms.
    Now also acts as a fake PlanetRoom for update_discovery tests.
    """
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
#  Tests: DiscoveredBuildingState dataclass
# -------------------------------------------------------------- #

class TestDiscoveredBuildingState:
    def test_creation(self):
        state = DiscoveredBuildingState(
            building_type="HQ", owner_name="Enemy", x=10, y=20
        )
        assert state.building_type == "HQ"
        assert state.owner_name == "Enemy"
        assert state.x == 10
        assert state.y == 20


# -------------------------------------------------------------- #
#  Tests: get_visible_tiles
# -------------------------------------------------------------- #

class TestGetVisibleTiles:
    def test_player_only_vision(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        player = _FakePlayer(x=5, y=5)
        tiles = fow.get_visible_tiles(player, [])
        # Chebyshev radius 2 around (5,5) => 5x5 = 25 tiles
        assert len(tiles) == 25
        assert (5, 5) in tiles
        assert (3, 3) in tiles
        assert (7, 7) in tiles
        # Outside radius
        assert (2, 5) not in tiles
        assert (8, 5) not in tiles

    def test_building_extends_vision(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=1, bvr=1))
        player = _FakePlayer(x=0, y=0)
        building_room = _FakeRoom(x=10, y=10)
        building = _FakeBuilding(location=building_room)
        tiles = fow.get_visible_tiles(player, [building])
        # Player circle: radius 1 around (0,0) => 9 tiles
        # Building circle: radius 1 around (10,10) => 9 tiles
        # No overlap => 18 tiles
        assert len(tiles) == 18
        assert (0, 0) in tiles
        assert (10, 10) in tiles

    def test_overlapping_vision_deduplicates(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=2))
        player = _FakePlayer(x=5, y=5)
        building_room = _FakeRoom(x=5, y=5)
        building = _FakeBuilding(location=building_room)
        tiles = fow.get_visible_tiles(player, [building])
        # Both circles centered at same point, same radius => 25 tiles
        assert len(tiles) == 25

    def test_no_buildings_no_crash(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=0, bvr=0))
        player = _FakePlayer(x=0, y=0)
        tiles = fow.get_visible_tiles(player, [])
        # Radius 0 => just the center tile
        assert tiles == {(0, 0)}

    def test_sight_range_bonus_extends_vision(self):
        """An equipped sight_range stat adds to the player vision radius."""
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        player = _FakePlayer(x=5, y=5)
        # A float bonus is coerced to int; radius 2 + 1 => 3
        player.equipment = _FakeEquipment(sight_range=1.9)
        tiles = fow.get_visible_tiles(player, [])
        # Chebyshev radius 3 around (5,5) => 7x7 = 49 tiles
        assert len(tiles) == 49
        assert (8, 8) in tiles
        assert (2, 2) in tiles
        # Just outside the extended radius
        assert (9, 5) not in tiles

    def test_missing_equipment_falls_back_to_base_radius(self):
        """A player without an equipment handler keeps the base radius."""
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        player = _FakePlayer(x=5, y=5)  # no .equipment attribute
        tiles = fow.get_visible_tiles(player, [])
        assert len(tiles) == 25

    def test_chebyshev_distance_not_euclidean(self):
        """Chebyshev radius 3 includes diagonal corners like (3,3)."""
        fow = FogOfWarSystem(_FakeBalance(pvr=3, bvr=1))
        player = _FakePlayer(x=10, y=10)
        tiles = fow.get_visible_tiles(player, [])
        # Diagonal corners at Chebyshev distance 3
        assert (13, 13) in tiles
        assert (7, 7) in tiles
        assert (13, 7) in tiles
        assert (7, 13) in tiles
        # Just outside
        assert (14, 14) not in tiles


# -------------------------------------------------------------- #
#  Tests: get_tile_visibility
# -------------------------------------------------------------- #

class TestGetTileVisibility:
    def test_visible_tile(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        visible = {(5, 5), (6, 6)}
        assert fow.get_tile_visibility(player, 5, 5, visible) == "visible"

    def test_fog_tile(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = {"discovered": DiscoveryBitfield.from_set({(10, 10)}).to_dict(), "buildings": {}}
        visible = set()
        assert fow.get_tile_visibility(player, 10, 10, visible) == "fog"

    def test_unexplored_tile(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        visible = set()
        assert fow.get_tile_visibility(player, 99, 99, visible) == "unexplored"

    def test_visible_takes_priority_over_discovered(self):
        """A tile that is both visible and discovered should be 'visible'."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = {"discovered": DiscoveryBitfield.from_set({(5, 5)}).to_dict(), "buildings": {}}
        visible = {(5, 5)}
        assert fow.get_tile_visibility(player, 5, 5, visible) == "visible"


# -------------------------------------------------------------- #
#  Tests: update_discovery
# -------------------------------------------------------------- #

class TestUpdateDiscovery:
    def test_marks_tiles_as_discovered(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(planet="earth")
        resolver = _FakeTileResolver()
        visible = {(1, 1), (2, 2), (3, 3)}
        fow.update_discovery(player, visible, resolver)
        bf = fow.get_discovered_tile_set(player)
        assert (1, 1) in bf
        assert (2, 2) in bf
        assert (3, 3) in bf

    def test_snapshots_enemy_building(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(name="Player1", x=5, y=5, planet="earth")
        enemy = _FakePlayer(name="Enemy1")
        building_room = _FakeRoom(x=6, y=6, building=None)
        enemy_building = _FakeBuilding(btype="HQ", owner=enemy, location=building_room)
        building_room.building = enemy_building
        resolver = _FakeTileResolver({(6, 6, "earth"): building_room})
        visible = {(6, 6)}
        fow.update_discovery(player, visible, resolver)
        bmap = fow.get_discovered_buildings_map(player)
        assert (6, 6) in bmap
        snap = bmap[(6, 6)]
        assert snap["building_type"] == "HQ"
        assert snap["owner_name"] == "Enemy1"

    def test_does_not_snapshot_own_building(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(name="Player1", x=5, y=5, planet="earth")
        building_room = _FakeRoom(x=5, y=5)
        own_building = _FakeBuilding(btype="HQ", owner=player, location=building_room)
        building_room.building = own_building
        resolver = _FakeTileResolver({(5, 5, "earth"): building_room})
        visible = {(5, 5)}
        fow.update_discovery(player, visible, resolver)
        bmap = fow.get_discovered_buildings_map(player)
        assert (5, 5) not in bmap

    def test_removes_stale_building_snapshot(self):
        """When vision is regained and building is gone, remove snapshot."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(name="Player1", planet="earth")
        # Pre-populate a building snapshot
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(10, 10)}).to_dict(),
            "buildings": {
                (10, 10): {
                    "building_type": "HQ",
                    "owner_name": "Enemy",
                    "x": 10,
                    "y": 10,
                }
            },
        }
        # Room exists but has no building now
        empty_room = _FakeRoom(x=10, y=10, building=None)
        resolver = _FakeTileResolver({(10, 10, "earth"): empty_room})
        visible = {(10, 10)}
        fow.update_discovery(player, visible, resolver)
        bmap = fow.get_discovered_buildings_map(player)
        assert (10, 10) not in bmap

    def test_removes_stale_when_no_room(self):
        """When vision is regained and no room exists, remove snapshot."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(name="Player1", planet="earth")
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(20, 20)}).to_dict(),
            "buildings": {
                (20, 20): {
                    "building_type": "VV",
                    "owner_name": "Enemy",
                    "x": 20,
                    "y": 20,
                }
            },
        }
        resolver = _FakeTileResolver()  # no rooms
        visible = {(20, 20)}
        fow.update_discovery(player, visible, resolver)
        bmap = fow.get_discovered_buildings_map(player)
        assert (20, 20) not in bmap

    def test_updates_existing_snapshot(self):
        """When regaining vision, snapshot is updated to current state."""
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer(name="Player1", planet="earth")
        enemy = _FakePlayer(name="Enemy1")
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(7, 7)}).to_dict(),
            "buildings": {
                (7, 7): {
                    "building_type": "HQ",
                    "owner_name": "Enemy1",
                    "x": 7,
                    "y": 7,
                }
            },
        }
        # Enemy upgraded to a different building type
        building_room = _FakeRoom(x=7, y=7)
        new_building = _FakeBuilding(btype="VV", owner=enemy, location=building_room)
        building_room.building = new_building
        resolver = _FakeTileResolver({(7, 7, "earth"): building_room})
        visible = {(7, 7)}
        fow.update_discovery(player, visible, resolver)
        bmap = fow.get_discovered_buildings_map(player)
        assert bmap[(7, 7)]["building_type"] == "VV"


# -------------------------------------------------------------- #
#  Tests: get_discovered_buildings
# -------------------------------------------------------------- #

class TestGetDiscoveredBuildings:
    def test_returns_snapshot(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(15, 20)}).to_dict(),
            "buildings": {
                (15, 20): {
                    "building_type": "HQ",
                    "owner_name": "Enemy1",
                    "x": 15,
                    "y": 20,
                }
            },
        }
        result = fow.get_discovered_buildings(player, 15, 20)
        assert len(result) == 1
        assert isinstance(result[0], DiscoveredBuildingState)
        assert result[0].building_type == "HQ"
        assert result[0].owner_name == "Enemy1"
        assert result[0].x == 15
        assert result[0].y == 20

    def test_returns_empty_for_no_building(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = {"discovered": {}, "buildings": {}}
        result = fow.get_discovered_buildings(player, 99, 99)
        assert result == []

    def test_returns_empty_for_no_memory(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = None  # corrupted / missing
        result = fow.get_discovered_buildings(player, 0, 0)
        assert result == []


# -------------------------------------------------------------- #
#  Tests: discovery memory initialisation
# -------------------------------------------------------------- #

class TestDiscoveryMemoryInit:
    def test_initialises_missing_memory(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = None
        visible = {(0, 0)}
        resolver = _FakeTileResolver()
        fow.update_discovery(player, visible, resolver)
        bf = fow.get_discovered_tile_set(player)
        assert (0, 0) in bf

    def test_handles_non_dict_memory(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        player.db.discovery_memory = "corrupted"
        result = fow.get_tile_visibility(player, 0, 0, set())
        # Should not crash, returns unexplored
        assert result == "unexplored"
