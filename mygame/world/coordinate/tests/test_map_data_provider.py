"""
Unit tests for MapDataProvider.

Tests that structured map data is generated correctly for the
graphical webclient, with proper tile states and player/building info.
"""

import sys
import types


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

from mygame.world.coordinate.map_data_provider import MapDataProvider  # noqa: E402
from mygame.world.coordinate.fog_of_war import FogOfWarSystem  # noqa: E402
from mygame.world.coordinate.discovery_bitfield import DiscoveryBitfield  # noqa: E402


class _FakeDB:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeBalance:
    def __init__(self, pvr=2, bvr=1):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr
        self.map_border_tiles = 1


class _FakePlayer:
    def __init__(self, name="Player1", x=5, y=5, planet="earth"):
        self.key = name
        self.has_account = True
        self.id = 1
        self.db = _FakeDB(
            coord_x=x, coord_y=y, coord_planet=planet,
            hp=350, hp_max=500, level=7,
            discovered_tiles=DiscoveryBitfield(),
            discovered_buildings={},
            discovery_memory={"discovered": {}, "buildings": {}},
        )

    def get_buildings(self):
        return []


class _FakeTerrainGen:
    def __init__(self):
        self._terrain_thresholds = [(1.0, "Plains")]

    def get_terrain(self, x, y):
        return "Plains"

    def get_terrain_and_resource(self, x, y):
        return "Plains", None


class _FakeTileResolver:
    def __init__(self):
        self._rooms = {}

    def get_cached(self, x, y, planet):
        return self._rooms.get((x, y, planet))

    def get_if_exists(self, x, y, planet):
        return self._rooms.get((x, y, planet))

    def preload_area(self, min_x, max_x, min_y, max_y, planet):
        pass


class TestMapDataProvider:
    def _make_provider(self, pvr=2):
        balance = _FakeBalance(pvr=pvr)
        fog = FogOfWarSystem(balance)
        gen = _FakeTerrainGen()
        resolver = _FakeTileResolver()
        provider = MapDataProvider(
            tile_resolver=resolver,
            fog_system=fog,
            terrain_generators={"earth": gen},
        )
        return provider, resolver

    def test_basic_output_structure(self):
        provider, _ = self._make_provider()
        player = _FakePlayer()
        data = provider.get_map_data(player, [])

        assert "player" in data
        assert data["player"]["x"] == 5
        assert data["player"]["y"] == 5
        assert data["player"]["planet"] == "earth"
        assert "bounds" in data
        assert "tiles" in data
        assert "vision_radius" in data
        assert data["vision_radius"] == 2

    def test_player_payload_includes_hp_and_level(self):
        """The player sub-dict carries hp/hp_max/level for the webclient's map
        footer (the graphical equivalent of the telnet status prompt)."""
        provider, _ = self._make_provider()
        player = _FakePlayer()
        data = provider.get_map_data(player, [])
        assert data["player"]["hp"] == 350
        assert data["player"]["hp_max"] == 500
        assert data["player"]["level"] == 7

    def test_tile_states(self):
        provider, _ = self._make_provider(pvr=1)
        player = _FakePlayer()
        data = provider.get_map_data(player, [])

        tiles_by_state = {}
        for t in data["tiles"]:
            tiles_by_state.setdefault(t["state"], []).append(t)

        # Player at (5,5) with pvr=1 and border=1
        # Visible: tiles within Chebyshev distance 1 of (5,5)
        assert "visible" in tiles_by_state
        assert len(tiles_by_state["visible"]) > 0

        # All visible tiles should have terrain
        for t in tiles_by_state["visible"]:
            assert t["terrain"] == "Plains"

    def test_player_position_in_bounds(self):
        provider, _ = self._make_provider()
        player = _FakePlayer(x=10, y=10)
        data = provider.get_map_data(player, [])

        bounds = data["bounds"]
        assert bounds["min_x"] <= 10 <= bounds["max_x"]
        assert bounds["min_y"] <= 10 <= bounds["max_y"]

    def test_linkdead_player_listed_on_tile(self):
        """A LINKDEAD player (no session) still occupies its tile during grace,
        so _visible_tile_from_objects must include it in the tile's player list —
        it uses player_is_present, not raw has_account (False when sessionless).
        A staging (SPAWNING/LOBBY) player, by contrast, is OOC and excluded. Each
        listed player carries its linkdead flag so the client draws the linkdead
        variant."""
        provider, _ = self._make_provider()
        looker = _FakePlayer(name="Looker", x=5, y=5)

        linkdead = _FakePlayer(name="Dropped", x=6, y=5)
        linkdead.has_account = False
        linkdead.db.player_state = "linkdead"

        staging = _FakePlayer(name="Staging", x=6, y=5)
        staging.has_account = True
        staging.db.player_state = "lobby"

        tile = provider._visible_tile_from_objects(
            6, 5, "Plains", looker, [linkdead, staging]
        )
        assert tile.get("players") == [
            {"name": "Dropped", "linkdead": True, "tag": None}
        ], (
            "linkdead player must be listed (flagged linkdead, no alliance tag); "
            "staging excluded"
        )

    def test_live_player_flagged_not_linkdead(self):
        """A live (PLAYING) player on the tile is listed with linkdead=False, so
        the client draws the live-enemy variant, not the linkdead one."""
        provider, _ = self._make_provider()
        looker = _FakePlayer(name="Looker", x=5, y=5)
        live = _FakePlayer(name="Rival", x=6, y=5)
        live.has_account = True
        live.db.player_state = "playing"

        tile = provider._visible_tile_from_objects(
            6, 5, "Plains", looker, [live]
        )
        assert tile.get("players") == [
            {"name": "Rival", "linkdead": False, "tag": None}
        ]

    def test_tiles_are_json_serializable(self):
        """Ensure the output can be JSON-serialized for the webclient."""
        import json
        provider, _ = self._make_provider()
        player = _FakePlayer()
        data = provider.get_map_data(player, [])
        # Should not raise
        json.dumps(data)

    def test_building_payload_includes_hp_and_shield(self):
        """A live building tile carries hp/hp_max (for label health color) and
        shield/shield_max (for the shield gauge + blue outline)."""
        provider, _ = self._make_provider()
        player = _FakePlayer()

        class _Tags:
            def __init__(self, is_building):
                self._b = is_building
            def get(self, key=None, category=None, **kw):
                if category == "object_type" and (key == "building" or key is None):
                    return "building" if self._b else None
                return None

        class _Attrs:
            def __init__(self, data):
                self._d = data
            def get(self, key, default=None):
                return self._d.get(key, default)

        class _Building:
            def __init__(self):
                self.key = "HQ"
                self.tags = _Tags(True)
                self.contents = []
                self.attributes = _Attrs({
                    "building_type": "HQ", "building_level": 1,
                    "owner": player, "hp": 120, "hp_max": 400,
                    "shield": 90, "shield_max": 100,
                })

        tile = provider._visible_tile_from_objects(
            5, 5, "Plains", player, [_Building()]
        )
        b = tile["building"]
        assert b["hp"] == 120 and b["hp_max"] == 400
        assert b["shield"] == 90 and b["shield_max"] == 100
        assert b["own"] is True

    def test_fog_tiles_after_move(self):
        """After moving, previously visible tiles become fog."""
        provider, _ = self._make_provider(pvr=1)
        player = _FakePlayer(x=5, y=5)

        # First render at (5,5)
        data1 = provider.get_map_data(player, [])

        # Move to (7,7) — old tiles should be fog
        player.db.coord_x = 7
        player.db.coord_y = 7
        data2 = provider.get_map_data(player, [])

        fog_coords = {(t["x"], t["y"]) for t in data2["tiles"] if t["state"] == "fog"}
        # (5,5) was visible before, now should be fog
        assert (5, 5) in fog_coords

    def test_out_of_bounds_tiles_are_fog(self):
        """Tiles beyond the planet's 0,0..max coords render as fog with an
        out_of_bounds flag — even when they fall inside the vision circle."""
        provider, _ = self._make_provider(pvr=2)  # viewport = player ± (2 + border)
        # A small 3x3 'earth' map (0..2); player at the origin so the viewport
        # spans negative (off-map) coords AND the past-max edge (x==3).
        provider._fog_system.set_in_bounds_func(
            lambda x, y, planet: 0 <= x < 3 and 0 <= y < 3
        )
        player = _FakePlayer(x=0, y=0)
        data = provider.get_map_data(player, [])

        by_coord = {(t["x"], t["y"]): t for t in data["tiles"]}
        # A negative tile is inside the vision radius (Chebyshev 1) but off-map:
        # it must be fog + flagged, NOT visible.
        oob = by_coord[(-1, -1)]
        assert oob["state"] == "fog"
        assert oob.get("out_of_bounds") is True
        # An in-bounds tile at the player is still visible (not flagged).
        here = by_coord[(0, 0)]
        assert here["state"] == "visible"
        assert "out_of_bounds" not in here
        # A tile past the max edge (x == width) is also fogged.
        assert by_coord[(3, 0)]["state"] == "fog"
        assert by_coord[(3, 0)].get("out_of_bounds") is True

    def test_in_bounds_unaffected_when_no_bounds_func(self):
        """Without an injected bounds func, no tile is flagged out_of_bounds
        (backward-compatible)."""
        provider, _ = self._make_provider(pvr=2)
        player = _FakePlayer(x=0, y=0)
        data = provider.get_map_data(player, [])
        assert not any(t.get("out_of_bounds") for t in data["tiles"])
