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
    """Minimal stand-in for BalanceConfig.

    ``min_vision_radius`` defaults to 0 here (unlike the real BalanceConfig's
    1) so the radius-0 geometry tests below stay exact; the terrain-strategy
    minimum-radius behavior is covered by its own tests.
    """
    def __init__(self, pvr=10, bvr=7, min_vision_radius=0):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr
        self.min_vision_radius = min_vision_radius


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


class _FakeScout:
    """Minimal scout agent for vision tests (R5)."""
    def __init__(self, x=0, y=0, role="scout", incapacitated=False,
                 reserve=False):
        self.db = _FakeDB(coord_x=x, coord_y=y, role=role,
                          incapacitated=incapacitated, reserve=reserve)


class TestScoutVision:
    """Scout agents project vision circles (early-game rebalance R5)."""

    def _fow(self, pvr=1, svr=2):
        bal = _FakeBalance(pvr=pvr, bvr=1)
        bal.scout_vision_radius = svr
        return FogOfWarSystem(bal)

    def test_scout_projects_vision(self):
        fow = self._fow(pvr=1, svr=2)
        player = _FakePlayer(x=0, y=0)
        scout = _FakeScout(x=20, y=20)
        tiles = fow.get_visible_tiles(player, [], player_scouts=[scout])
        # Player circle r=1 (9 tiles) + scout circle r=2 (25 tiles), no overlap
        assert len(tiles) == 34
        assert (20, 20) in tiles
        assert (22, 22) in tiles
        assert (23, 20) not in tiles  # outside scout radius

    def test_incapacitated_scout_projects_nothing(self):
        fow = self._fow()
        player = _FakePlayer(x=0, y=0)
        scout = _FakeScout(x=20, y=20, incapacitated=True)
        tiles = fow.get_visible_tiles(player, [], player_scouts=[scout])
        assert (20, 20) not in tiles

    def test_reserved_scout_projects_nothing(self):
        fow = self._fow()
        player = _FakePlayer(x=0, y=0)
        scout = _FakeScout(x=20, y=20, reserve=True)
        tiles = fow.get_visible_tiles(player, [], player_scouts=[scout])
        assert (20, 20) not in tiles

    def test_non_scout_role_projects_nothing(self):
        fow = self._fow()
        player = _FakePlayer(x=0, y=0)
        guard = _FakeScout(x=20, y=20, role="guard")
        tiles = fow.get_visible_tiles(player, [], player_scouts=[guard])
        assert (20, 20) not in tiles

    def test_zero_radius_disables_scout_vision(self):
        fow = self._fow(svr=0)
        player = _FakePlayer(x=0, y=0)
        scout = _FakeScout(x=20, y=20)
        tiles = fow.get_visible_tiles(player, [], player_scouts=[scout])
        assert (20, 20) not in tiles

    def test_no_scouts_kwarg_backward_compatible(self):
        """The legacy 2-arg call keeps working (no scouts passed)."""
        fow = self._fow()
        player = _FakePlayer(x=0, y=0)
        tiles = fow.get_visible_tiles(player, [])
        assert (0, 0) in tiles


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

class TestRememberBuildingWithoutVision:
    """``remember_building`` records intel the player never SAW — the Survey
    Array pinpointing an enemy base. Discovery memory is additive, so the mark
    stays known after the intel source is gone."""

    def test_marks_the_tile_discovered_so_the_snapshot_renders(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()

        assert fow.remember_building(player, 42, 17, "HQ", "Outpost #2") is True

        # Discovered but not currently visible => renders through the fog path.
        assert (42, 17) in fow.get_discovered_tile_set(player)
        assert fow.get_tile_visibility(player, 42, 17, set()) == "fog"
        seen = fow.get_discovered_buildings(player, 42, 17)
        assert len(seen) == 1
        assert seen[0].building_type == "HQ"
        assert seen[0].owner_name == "Outpost #2"
        assert (seen[0].x, seen[0].y) == (42, 17)

    def test_repeating_an_identical_snapshot_is_a_no_op(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        fow.remember_building(player, 3, 4, "HQ", "Outpost #1")

        assert fow.remember_building(player, 3, 4, "HQ", "Outpost #1") is False
        assert fow.get_discovered_buildings(player, 3, 4)

    def test_a_changed_snapshot_is_persisted(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        fow.remember_building(player, 3, 4, "HQ", "Outpost #1")

        assert fow.remember_building(player, 3, 4, "TU", "Outpost #1") is True
        assert fow.get_discovered_buildings(player, 3, 4)[0].building_type == "TU"

    def test_marks_accumulate_across_tiles(self):
        fow = FogOfWarSystem(_FakeBalance())
        player = _FakePlayer()
        fow.remember_building(player, 5, 6, "HQ", "Outpost #1")
        fow.remember_building(player, 9, 9, "HQ", "Fortress #1")

        assert fow.get_discovered_buildings(player, 5, 6)
        assert fow.get_discovered_buildings(player, 9, 9)


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


# -------------------------------------------------------------- #
#  Tests: is_in_bounds (out-of-bounds = fog of war)
# -------------------------------------------------------------- #

class TestIsInBounds:
    """A tile beyond a planet's 0,0..max coords is out of bounds and rendered
    as fog. Bounds come from an injected check (planet_registry's
    is_valid_coordinate); unwired falls open so tests/edge cases never fog real
    tiles."""

    @staticmethod
    def _bounds_10x8(x, y, planet):
        # A 10x8 'earth' map: 0 <= x < 10, 0 <= y < 8 (like is_valid_coordinate).
        if planet != "earth":
            raise KeyError(planet)
        return 0 <= x < 10 and 0 <= y < 8

    def test_falls_open_when_unwired(self):
        fow = FogOfWarSystem(_FakeBalance())
        # No bounds func injected -> every tile is in-bounds.
        assert fow.is_in_bounds("earth", -50, -50) is True
        assert fow.is_in_bounds("earth", 9999, 9999) is True

    def test_in_bounds_true_inside_map(self):
        fow = FogOfWarSystem(_FakeBalance())
        fow.set_in_bounds_func(self._bounds_10x8)
        assert fow.is_in_bounds("earth", 0, 0) is True
        assert fow.is_in_bounds("earth", 9, 7) is True
        assert fow.is_in_bounds("earth", 5, 4) is True

    def test_out_of_bounds_beyond_origin(self):
        fow = FogOfWarSystem(_FakeBalance())
        fow.set_in_bounds_func(self._bounds_10x8)
        assert fow.is_in_bounds("earth", -1, 0) is False
        assert fow.is_in_bounds("earth", 0, -1) is False

    def test_out_of_bounds_beyond_max(self):
        fow = FogOfWarSystem(_FakeBalance())
        fow.set_in_bounds_func(self._bounds_10x8)
        assert fow.is_in_bounds("earth", 10, 0) is False  # x == width
        assert fow.is_in_bounds("earth", 0, 8) is False    # y == height

    def test_unknown_planet_falls_open(self):
        """An unknown planet key (bounds func raises KeyError) must not fog a
        tile — falls open."""
        fow = FogOfWarSystem(_FakeBalance())
        fow.set_in_bounds_func(self._bounds_10x8)
        assert fow.is_in_bounds("nonexistent", 3, 3) is True


# -------------------------------------------------------------- #
#  Tests: terrain vision fallback and minimum radius
#  (terrain-strategy Req 3.5, 3.6, 3.2)
# -------------------------------------------------------------- #

class _FakeBalanceNoMin:
    """Balance stand-in WITHOUT a ``min_vision_radius`` attribute.

    Used to verify the FogOfWarSystem defaults the minimum vision radius to 1
    when the balance configuration omits the field (Req 3.6).
    """
    def __init__(self, pvr=10, bvr=7):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr


class _FakeModifiers:
    """Minimal TerrainModifiers stand-in exposing only ``vision``."""
    def __init__(self, vision=0):
        self.vision = vision


class _FakeResolver:
    """Terrain modifier resolver returning fixed vision modifiers."""
    def __init__(self, player_vision=0, base_vision=0):
        self._player_vision = player_vision
        self._base_vision = base_vision

    def resolve_for_player(self, player, planet, x, y):
        return _FakeModifiers(self._player_vision)

    def resolve_base(self, planet, x, y):
        return _FakeModifiers(self._base_vision)


class _RaisingResolver:
    """Terrain modifier resolver whose lookups always raise."""
    def resolve_for_player(self, player, planet, x, y):
        raise RuntimeError("resolver blew up")

    def resolve_base(self, planet, x, y):
        raise RuntimeError("resolver blew up")


class TestTerrainVisionFallback:
    """Fail-soft terrain vision: unset or raising resolver yields modifier 0
    (Req 3.5)."""

    def test_unset_resolver_yields_zero_player_modifier(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        player = _FakePlayer(x=5, y=5)
        tiles = fow.get_visible_tiles(player, [])
        # No resolver injected -> terrain vision 0 -> base radius 2 -> 25 tiles
        assert len(tiles) == 25

    def test_unset_resolver_yields_zero_building_modifier(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=1, bvr=2))
        player = _FakePlayer(x=0, y=0)
        building = _FakeBuilding(location=_FakeRoom(x=20, y=20))
        tiles = fow.get_visible_tiles(player, [building])
        # Building circle keeps its base radius 2 -> 25 tiles around (20,20)
        assert (22, 22) in tiles
        assert (23, 20) not in tiles

    def test_wired_resolver_adjusts_player_radius(self):
        """Sanity check: a wired resolver actually changes the circle, so the
        fallback tests above are meaningful."""
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        fow.set_terrain_modifier_resolver(_FakeResolver(player_vision=1))
        player = _FakePlayer(x=5, y=5)
        tiles = fow.get_visible_tiles(player, [])
        # Radius 2 + 1 => 3 -> 7x7 = 49 tiles
        assert len(tiles) == 49

    def test_raising_resolver_yields_zero_and_no_exception(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        fow.set_terrain_modifier_resolver(_RaisingResolver())
        player = _FakePlayer(x=5, y=5)
        tiles = fow.get_visible_tiles(player, [])  # must not raise
        # Terrain vision degraded to 0 -> base radius 2 -> 25 tiles
        assert len(tiles) == 25

    def test_raising_resolver_building_circle_unaffected(self):
        fow = FogOfWarSystem(_FakeBalance(pvr=1, bvr=2))
        fow.set_terrain_modifier_resolver(_RaisingResolver())
        player = _FakePlayer(x=0, y=0)
        building = _FakeBuilding(location=_FakeRoom(x=20, y=20))
        tiles = fow.get_visible_tiles(player, [building])  # must not raise
        # Building circle keeps its base radius 2 around (20,20)
        assert (22, 22) in tiles
        assert (23, 20) not in tiles


class TestMinVisionRadiusDefault:
    """A balance config omitting ``min_vision_radius`` defaults the minimum to
    1 for both player and building circles (Req 3.6, 3.2)."""

    def test_default_min_applied_to_player_circle(self):
        fow = FogOfWarSystem(_FakeBalanceNoMin(pvr=2, bvr=1))
        assert fow.min_vision_radius == 1
        # Large negative terrain modifier drives the raw radius below 1.
        fow.set_terrain_modifier_resolver(_FakeResolver(player_vision=-10))
        player = _FakePlayer(x=5, y=5)
        tiles = fow.get_visible_tiles(player, [])
        # max(1, int(2 - 10)) == 1 -> 3x3 = 9 tiles
        assert len(tiles) == 9
        assert (5, 5) in tiles
        assert (6, 6) in tiles
        assert (7, 5) not in tiles

    def test_default_min_applied_to_building_circle(self):
        fow = FogOfWarSystem(_FakeBalanceNoMin(pvr=1, bvr=2))
        fow.set_terrain_modifier_resolver(_FakeResolver(base_vision=-10))
        player = _FakePlayer(x=0, y=0)
        building = _FakeBuilding(location=_FakeRoom(x=20, y=20))
        tiles = fow.get_visible_tiles(player, [building])
        # Building circle: max(1, int(2 - 10)) == 1 -> radius 1 around (20,20)
        assert (20, 20) in tiles
        assert (21, 21) in tiles
        assert (22, 20) not in tiles

    def test_default_min_does_not_inflate_normal_radius(self):
        """The default minimum of 1 never shrinks or grows a radius already
        above it."""
        fow = FogOfWarSystem(_FakeBalanceNoMin(pvr=2, bvr=1))
        player = _FakePlayer(x=5, y=5)
        tiles = fow.get_visible_tiles(player, [])
        assert len(tiles) == 25


# -------------------------------------------------------------- #
#  Tests: Watchtower VISION_AURA tile bonus
#  (item-loot-economy task 6.2, R10.2)
# -------------------------------------------------------------- #

class _FakeBuildingDef:
    """BuildingDef stand-in exposing has_capability."""

    def __init__(self, capabilities=()):
        self._caps = frozenset(capabilities)

    def has_capability(self, cap):
        return cap in self._caps


class _FakeDefsProvider:
    """DefinitionsProvider stand-in resolving building abbreviations."""

    def __init__(self, defs):
        self._defs = dict(defs)

    def resolve_building(self, btype):
        return self._defs.get(btype)


class _AuraRoom:
    """PlanetRoom-shaped fake answering ``get_buildings_at`` — the tile
    read ``_tile_vision_bonus`` mirrors from the Sniper Nest's
    ``_tile_range_bonus``."""

    def __init__(self):
        self._at = {}

    def place(self, x, y, building):
        self._at.setdefault((x, y), []).append(building)

    def get_buildings_at(self, x, y):
        return list(self._at.get((x, y), []))


class _FakeTower:
    """Watchtower building fake; attributes read through the db bag."""

    def __init__(self, btype="WT", owner=None, level=1, offline=False,
                 under_construction=False):
        self.db = _FakeDB(
            building_type=btype,
            owner=owner,
            offline=offline,
            under_construction=under_construction,
        )
        self.building_level = level


def _aura_fow(pvr=2):
    """A FogOfWarSystem with a WT (vision_aura) definitions provider."""
    fow = FogOfWarSystem(_FakeBalance(pvr=pvr, bvr=1))
    fow.set_definitions_provider(_FakeDefsProvider({
        "WT": _FakeBuildingDef({"vision_aura", "upgradable"}),
        "HQ": _FakeBuildingDef({"headquarters"}),
    }))
    return fow


def _aura_player(room, x, y, oid=1):
    """A player standing on tile (x, y) of *room*, with a stable id."""
    player = _FakePlayer(name=f"P{oid}", x=x, y=y)
    player.id = oid
    player.location = room
    return player


class TestTileVisionBonus:
    """R10.2 (item-loot-economy task 6.2): the Watchtower VISION_AURA.

    ``_tile_vision_bonus`` grants ``1 + (level-1)//2`` extra sight_range
    only while the player stands on their OWN, OPERATIONAL vision-aura
    building's tile — on-tile only and owner-only, mirroring the Sniper
    Nest range aura.
    """

    def test_owner_on_tile_extends_vision_circle(self):
        """The aura flows into get_visible_tiles: radius 2 + L1 tower = 3."""
        fow = _aura_fow(pvr=2)
        room = _AuraRoom()
        player = _aura_player(room, 5, 5)
        room.place(5, 5, _FakeTower(owner=player, level=1))
        tiles = fow.get_visible_tiles(player, [])
        # Chebyshev radius 3 around (5,5) => 7x7 = 49 tiles
        assert len(tiles) == 49
        assert (8, 8) in tiles
        assert (9, 5) not in tiles

    def test_bonus_applies_only_on_the_tower_tile(self):
        """On the tower's tile → +1 (L1); one tile off → 0 (no adjacency)."""
        fow = _aura_fow()
        room = _AuraRoom()
        owner = _aura_player(room, 5, 5)
        room.place(5, 5, _FakeTower(owner=owner, level=1))
        assert fow._tile_vision_bonus(owner) == 1
        # Same owner, adjacent tile: strictly on-tile.
        owner.db.coord_x = 6
        assert fow._tile_vision_bonus(owner) == 0

    def test_level_scaling_plus_one_to_plus_three(self):
        """Formula 1 + (lvl-1)//2: L1 +1, L2 +1, L3 +2, L4 +2, L5 +3."""
        fow = _aura_fow()
        expected = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3}
        for level, bonus in expected.items():
            room = _AuraRoom()
            player = _aura_player(room, 0, 0)
            room.place(0, 0, _FakeTower(owner=player, level=level))
            assert fow._tile_vision_bonus(player) == bonus, (
                f"level {level} should grant +{bonus}"
            )

    def test_someone_elses_tower_grants_nothing(self):
        """Owner-only (R10.2): standing on another player's tower → 0."""
        fow = _aura_fow()
        room = _AuraRoom()
        intruder = _aura_player(room, 2, 3, oid=1)
        builder = _FakePlayer(name="Builder", x=0, y=0)
        builder.id = 2
        room.place(2, 3, _FakeTower(owner=builder, level=5))
        assert fow._tile_vision_bonus(intruder) == 0

    def test_owners_agent_on_tile_benefits(self):
        """UNIFIED owner attribution (DRY H2 extraction): an owner's AGENT
        (db.owner) standing on the tower tile extends the aura on the
        owner's behalf — consistent with the Sniper Nest / Field Hospital
        attribution (this reader previously credited the raw player only)."""
        fow = _aura_fow()
        room = _AuraRoom()
        owner = _aura_player(room, 9, 9, oid=1)
        agent = _aura_player(room, 2, 3, oid=7)
        agent.db.owner = owner  # agent shape: attributed to its owning player
        room.place(2, 3, _FakeTower(owner=owner, level=1))
        assert fow._tile_vision_bonus(agent) == 1
        # A STRANGER's agent on the tile still gets nothing (owner-only).
        stranger = _aura_player(room, 9, 0, oid=2)
        strangers_agent = _aura_player(room, 2, 3, oid=8)
        strangers_agent.db.owner = stranger
        assert fow._tile_vision_bonus(strangers_agent) == 0

    def test_corrupted_building_level_none_degrades_to_zero(self):
        """A tower whose level reads None (corrupted data) grants 0 rather
        than raising out of the fog computation (shared-helper guard)."""
        fow = _aura_fow()
        room = _AuraRoom()
        player = _aura_player(room, 0, 0)
        room.place(0, 0, _FakeTower(owner=player, level=None))
        assert fow._tile_vision_bonus(player) == 0

    def test_non_operational_tower_inert(self):
        """An offline or mid-upgrade tower grants nothing."""
        fow = _aura_fow()
        room = _AuraRoom()
        player = _aura_player(room, 0, 0)
        room.place(0, 0, _FakeTower(owner=player, level=3, offline=True))
        assert fow._tile_vision_bonus(player) == 0

        room2 = _AuraRoom()
        player2 = _aura_player(room2, 0, 0)
        room2.place(0, 0, _FakeTower(owner=player2, level=3,
                                     under_construction=True))
        assert fow._tile_vision_bonus(player2) == 0

    def test_non_aura_building_grants_nothing(self):
        """Standing on an owned building WITHOUT vision_aura (an HQ) → 0."""
        fow = _aura_fow()
        room = _AuraRoom()
        player = _aura_player(room, 0, 0)
        room.place(0, 0, _FakeTower(btype="HQ", owner=player, level=5))
        assert fow._tile_vision_bonus(player) == 0

    def test_empty_tile_and_missing_location_never_raise(self):
        """No building / no location / no provider → 0, never an exception."""
        fow = _aura_fow()
        room = _AuraRoom()
        player = _aura_player(room, 0, 0)
        assert fow._tile_vision_bonus(player) == 0
        # Player with no location at all.
        homeless = _FakePlayer(x=0, y=0)
        assert fow._tile_vision_bonus(homeless) == 0
        # A provider that can't resolve the building type falls soft → 0.
        unknown = FogOfWarSystem(_FakeBalance(pvr=2, bvr=1))
        unknown.set_definitions_provider(_FakeDefsProvider({}))
        player3 = _aura_player(room, 0, 0)
        room.place(0, 0, _FakeTower(owner=player3, level=5))
        assert unknown._tile_vision_bonus(player3) == 0
