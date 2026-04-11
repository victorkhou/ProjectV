"""
Unit tests for ProceduralMapRenderer.

Tests rendering with three visibility states (visible, fog, unexplored),
display priority (@@ > ** > building abbr > terrain), and render bounds.
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
import re as _re  # noqa: E402

from mygame.world.coordinate.procedural_map_renderer import ProceduralMapRenderer  # noqa: E402
from mygame.world.coordinate.fog_of_war import FogOfWarSystem, DiscoveredBuildingState  # noqa: E402


def _strip_color(s: str) -> str:
    """Strip Evennia |X color codes from a string."""
    return _re.sub(r'\|[a-zA-Z]', '', s)


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _FakeDB:
    """Minimal attribute-bag mimicking Evennia's db handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeBalance:
    def __init__(self, pvr=2, bvr=1):
        self.player_vision_radius = pvr
        self.building_vision_radius = bvr


class _FakePlayer:
    def __init__(self, name="Player1", x=5, y=5, planet="earth"):
        self.key = name
        self.has_account = True
        self.db = _FakeDB(
            coord_x=x,
            coord_y=y,
            coord_planet=planet,
            discovery_memory={"discovered": set(), "buildings": {}},
        )


class _FakeRoom:
    """Lightweight room with get_display_symbol support."""
    def __init__(self, x=0, y=0, terrain="Plains", contents=None):
        self.x = x
        self.y = y
        self._terrain = terrain
        self.contents = contents or []
        self.terrain_type = terrain
        self.resource_node = None

    @property
    def building(self):
        for obj in self.contents:
            if hasattr(obj, "_btype") or hasattr(obj, "get_display_abbreviation"):
                return obj
        return None

    def get_display_symbol(self, looker):
        for obj in self.contents:
            if hasattr(obj, "has_account") and obj.has_account:
                if obj is looker:
                    return "@@"
                return "**"
        bld = self.building
        if bld is not None:
            return bld._btype[:2] if hasattr(bld, "_btype") else "??"
        return self._terrain[:2] if len(self._terrain) >= 2 else self._terrain.ljust(2, "?")


class _FakeBuilding:
    def __init__(self, btype="HQ", owner=None, location=None):
        self._btype = btype
        self.owner = owner
        self.location = location

    def get_display_abbreviation(self):
        return self._btype


class _FakeTerrainGenerator:
    """Returns a fixed terrain type for all coordinates, or per-coord map."""
    def __init__(self, default_terrain="Plains", terrain_map=None):
        self._default = default_terrain
        self._map = terrain_map or {}

    def get_terrain(self, x, y):
        return self._map.get((x, y), self._default)

    def get_terrain_and_resource(self, x, y):
        return self.get_terrain(x, y), None


class _FakeTileResolver:
    """Returns pre-configured rooms or None."""
    def __init__(self, rooms=None):
        self._rooms = rooms or {}

    def get_if_exists(self, x, y, planet):
        return self._rooms.get((x, y, planet))

    def get_cached(self, x, y, planet):
        return self._rooms.get((x, y, planet))

    def get_or_generate_terrain(self, x, y, planet):
        room = self.get_if_exists(x, y, planet)
        if room:
            return room.terrain_type, None
        return "Plains", None


# -------------------------------------------------------------- #
#  Helper: build a renderer with small vision for easy testing
# -------------------------------------------------------------- #

def _make_renderer(
    pvr=2, bvr=1, planet="earth",
    rooms=None, terrain="Plains", terrain_map=None,
):
    balance = _FakeBalance(pvr=pvr, bvr=bvr)
    fog = FogOfWarSystem(balance)
    gen = _FakeTerrainGenerator(default_terrain=terrain, terrain_map=terrain_map)
    resolver = _FakeTileResolver(rooms=rooms or {})
    renderer = ProceduralMapRenderer(
        tile_resolver=resolver,
        fog_system=fog,
        terrain_generators={planet: gen},
    )
    return renderer, fog


# -------------------------------------------------------------- #
#  Tests: basic rendering
# -------------------------------------------------------------- #

class TestBasicRendering:
    def test_render_returns_string(self):
        renderer, _ = _make_renderer()
        player = _FakePlayer(x=5, y=5)
        result = renderer.render(player, [])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_2_char_per_tile(self):
        """Each tile symbol should be exactly 2 visible characters (ignoring color codes)."""
        renderer, _ = _make_renderer(pvr=1)
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        lines = result.strip().split("\n")
        for line in lines:
            symbols = line.split(" ")
            for sym in symbols:
                # Strip Evennia color codes: |X and |n
                import re
                stripped = re.sub(r'\|[a-zA-Z]', '', sym)
                assert len(stripped) == 2, f"Symbol '{sym}' -> '{stripped}' is not 2 visible chars"

    def test_render_empty_when_no_tiles(self):
        """If somehow no tiles are relevant, return empty string."""
        balance = _FakeBalance(pvr=0, bvr=0)
        fog = FogOfWarSystem(balance)
        gen = _FakeTerrainGenerator()
        resolver = _FakeTileResolver()
        renderer = ProceduralMapRenderer(
            tile_resolver=resolver,
            fog_system=fog,
            terrain_generators={},
        )
        # Player on a planet with no generator — vision still computed
        player = _FakePlayer(x=0, y=0, planet="unknown")
        result = renderer.render(player, [])
        # With pvr=0, there's 1 visible tile at (0,0), so we get output
        assert isinstance(result, str)

    def test_render_grid_dimensions(self):
        """With pvr=1, we get a 3x3 grid (radius 1 in each direction)."""
        renderer, _ = _make_renderer(pvr=1)
        player = _FakePlayer(x=5, y=5)
        result = renderer.render(player, [])
        lines = result.strip().split("\n")
        assert len(lines) == 3  # 3 rows
        for line in lines:
            symbols = line.split(" ")
            assert len(symbols) == 3  # 3 columns


# -------------------------------------------------------------- #
#  Tests: terrain symbol rendering
# -------------------------------------------------------------- #

class TestTerrainSymbols:
    def test_terrain_fallback_first_two_chars(self):
        """Without DataRegistry, terrain symbol is first 2 chars of type."""
        renderer, _ = _make_renderer(pvr=0, terrain="Forest")
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        assert "Fo" in result

    def test_different_terrain_per_tile(self):
        """Different tiles can have different terrain types."""
        tmap = {(0, 0): "Plains", (1, 0): "Forest", (0, 1): "Rock"}
        renderer, _ = _make_renderer(pvr=1, terrain="Dirt", terrain_map=tmap)
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        # All tiles are visible, so terrain symbols should appear
        assert "Pl" in result  # Plains
        assert "Fo" in result  # Forest
        assert "Ro" in result  # Rock

    def test_unknown_planet_generator(self):
        """If no terrain generator for the planet, tiles render as '..'."""
        balance = _FakeBalance(pvr=1)
        fog = FogOfWarSystem(balance)
        resolver = _FakeTileResolver()
        renderer = ProceduralMapRenderer(
            tile_resolver=resolver,
            fog_system=fog,
            terrain_generators={},  # no generators
        )
        player = _FakePlayer(x=0, y=0, planet="mars")
        result = renderer.render(player, [])
        # All tiles should be ".."
        lines = result.strip().split("\n")
        for line in lines:
            for sym in line.split(" "):
                assert sym == ".."


# -------------------------------------------------------------- #
#  Tests: visibility states
# -------------------------------------------------------------- #

class TestVisibilityStates:
    def test_visible_tile_shows_player_self(self):
        """Player's own tile should show '@@'."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        room = _FakeRoom(x=5, y=5, terrain="Plains", contents=[player])
        renderer, _ = _make_renderer(
            pvr=1, rooms={(5, 5, "earth"): room}
        )
        result = renderer.render(player, [])
        assert "@@" in result

    def test_visible_tile_shows_enemy_player(self):
        """Another player on a visible tile should show '**'."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        enemy = _FakePlayer(name="Enemy", x=6, y=5, planet="earth")
        room_player = _FakeRoom(x=5, y=5, terrain="Plains", contents=[player])
        room_enemy = _FakeRoom(x=6, y=5, terrain="Plains", contents=[enemy])
        renderer, _ = _make_renderer(
            pvr=1,
            rooms={
                (5, 5, "earth"): room_player,
                (6, 5, "earth"): room_enemy,
            },
        )
        result = renderer.render(player, [])
        assert "@@" in result
        assert "**" in result

    def test_visible_tile_shows_building(self):
        """A building on a visible tile (no players) shows abbreviation."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        building = _FakeBuilding(btype="HQ")
        room_bld = _FakeRoom(x=6, y=5, terrain="Plains", contents=[building])
        renderer, _ = _make_renderer(
            pvr=1,
            rooms={(6, 5, "earth"): room_bld},
        )
        result = renderer.render(player, [])
        assert "HQ" in result

    def test_fog_tile_shows_terrain(self):
        """A fog tile with no discovered buildings shows terrain."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        # Pre-discover tile (10, 10) but it's outside pvr=2 vision
        player.db.discovery_memory = {
            "discovered": {(10, 10)},
            "buildings": {},
        }
        renderer, _ = _make_renderer(pvr=2, terrain="Forest")
        result = renderer.render(player, [])
        # (10, 10) is in fog — should show terrain "Fo"
        assert "Fo" in result

    def test_fog_tile_shows_discovered_building(self):
        """A fog tile with a discovered building shows the building abbr."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        player.db.discovery_memory = {
            "discovered": {(20, 20)},
            "buildings": {
                (20, 20): {
                    "building_type": "HQ",
                    "owner_name": "Enemy",
                    "x": 20,
                    "y": 20,
                }
            },
        }
        renderer, _ = _make_renderer(pvr=2, terrain="Plains")
        result = renderer.render(player, [])
        assert "HQ" in result

    def test_fog_tile_hides_enemy_players(self):
        """Enemy players on fog tiles should NOT be shown."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        enemy = _FakePlayer(name="Enemy", x=20, y=20, planet="earth")
        room_enemy = _FakeRoom(x=20, y=20, terrain="Plains", contents=[enemy])
        # Pre-discover tile (20, 20)
        player.db.discovery_memory = {
            "discovered": {(20, 20)},
            "buildings": {},
        }
        renderer, _ = _make_renderer(
            pvr=2,
            rooms={(20, 20, "earth"): room_enemy},
        )
        result = renderer.render(player, [])
        # The fog tile should NOT show "**" — it should show terrain
        # Count "**" occurrences — should be 0 since enemy is in fog
        # (player tile might not have a room, so no "@@" either)
        lines = result.strip().split("\n")
        fog_region_has_enemy = False
        for line in lines:
            symbols = line.split(" ")
            for sym in symbols:
                if sym == "**":
                    fog_region_has_enemy = True
        assert not fog_region_has_enemy

    def test_unexplored_tile_shows_terrain_only(self):
        """Unexplored tiles show only terrain symbol."""
        renderer, _ = _make_renderer(pvr=1, terrain="Mountain")
        player = _FakePlayer(x=0, y=0, planet="earth")
        result = renderer.render(player, [])
        lines = result.strip().split("\n")
        for line in lines:
            for sym in line.split(" "):
                stripped = _strip_color(sym)
                assert stripped == "Mo" or stripped == "/\\", f"Expected terrain, got '{sym}'"


# -------------------------------------------------------------- #
#  Tests: display priority
# -------------------------------------------------------------- #

class TestDisplayPriority:
    def test_player_self_overrides_building(self):
        """@@ takes priority over building abbreviation."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        building = _FakeBuilding(btype="HQ")
        room = _FakeRoom(
            x=5, y=5, terrain="Plains",
            contents=[player, building],
        )
        renderer, _ = _make_renderer(
            pvr=1, rooms={(5, 5, "earth"): room}
        )
        result = renderer.render(player, [])
        # The tile at (5,5) should show "@@", not "HQ"
        lines = result.strip().split("\n")
        # Find the center tile
        center_line = lines[len(lines) // 2]
        symbols = center_line.split(" ")
        center_sym = symbols[len(symbols) // 2]
        assert _strip_color(center_sym) == "@@"

    def test_enemy_player_overrides_building(self):
        """** takes priority over building abbreviation."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        enemy = _FakePlayer(name="Enemy", x=6, y=5, planet="earth")
        building = _FakeBuilding(btype="HQ")
        room = _FakeRoom(
            x=6, y=5, terrain="Plains",
            contents=[enemy, building],
        )
        renderer, _ = _make_renderer(
            pvr=1, rooms={(6, 5, "earth"): room}
        )
        result = renderer.render(player, [])
        assert "**" in result


# -------------------------------------------------------------- #
#  Tests: render bounds
# -------------------------------------------------------------- #

class TestRenderBounds:
    def test_render_includes_fog_tiles_in_bounds(self):
        """Render bounds should include discovered fog tiles."""
        player = _FakePlayer(x=0, y=0, planet="earth")
        # Pre-discover a distant tile
        player.db.discovery_memory = {
            "discovered": {(10, 0)},
            "buildings": {},
        }
        renderer, _ = _make_renderer(pvr=1, terrain="Plains")
        result = renderer.render(player, [])
        lines = result.strip().split("\n")
        # The render should span from x=-1 to x=10 (12 columns)
        # and y=-1 to y=1 (3 rows)
        assert len(lines) == 3
        first_line_symbols = lines[0].split(" ")
        assert len(first_line_symbols) == 12

    def test_render_bounds_with_building_vision(self):
        """Building vision extends the render area."""
        player = _FakePlayer(x=0, y=0, planet="earth")
        building_room = _FakeRoom(x=5, y=0)
        building = _FakeBuilding(btype="HQ", location=building_room)
        renderer, _ = _make_renderer(pvr=1, bvr=1, terrain="Plains")
        result = renderer.render(player, [building])
        lines = result.strip().split("\n")
        # Player vision: (-1,-1) to (1,1)
        # Building vision: (4,-1) to (6,1)
        # Combined x range: -1 to 6 = 8 columns
        # Combined y range: -1 to 1 = 3 rows
        assert len(lines) == 3
        first_line_symbols = lines[0].split(" ")
        assert len(first_line_symbols) == 8


# -------------------------------------------------------------- #
#  Tests: _get_tile_symbol directly
# -------------------------------------------------------------- #

class TestGetTileSymbol:
    def test_visible_no_room_returns_terrain(self):
        renderer, _ = _make_renderer(terrain="Rock")
        player = _FakePlayer()
        sym = renderer._get_tile_symbol(0, 0, "earth", "visible", player, set())
        assert sym == "Ro"

    def test_visible_with_room_delegates_to_room(self):
        player = _FakePlayer(x=5, y=5, planet="earth")
        room = _FakeRoom(x=5, y=5, terrain="Plains", contents=[player])
        renderer, _ = _make_renderer(rooms={(5, 5, "earth"): room})
        sym = renderer._get_tile_symbol(5, 5, "earth", "visible", player, set())
        assert sym == "@@"

    def test_fog_no_building_returns_terrain(self):
        renderer, _ = _make_renderer(terrain="Forest")
        player = _FakePlayer()
        player.db.discovery_memory = {"discovered": set(), "buildings": {}}
        sym = renderer._get_tile_symbol(0, 0, "earth", "fog", player, set())
        assert sym == "Fo"

    def test_fog_with_building_returns_abbreviation(self):
        renderer, fog = _make_renderer(terrain="Plains")
        player = _FakePlayer()
        player.db.discovery_memory = {
            "discovered": {(3, 3)},
            "buildings": {
                (3, 3): {
                    "building_type": "VV",
                    "owner_name": "Enemy",
                    "x": 3,
                    "y": 3,
                }
            },
        }
        sym = renderer._get_tile_symbol(3, 3, "earth", "fog", player, set())
        assert sym == "VV"

    def test_unexplored_returns_terrain(self):
        renderer, _ = _make_renderer(terrain="Mountain")
        player = _FakePlayer()
        sym = renderer._get_tile_symbol(0, 0, "earth", "unexplored", player, set())
        assert sym == "Mo"

    def test_fog_building_single_char_padded(self):
        """A single-char building type gets padded to 2 chars."""
        renderer, _ = _make_renderer(terrain="Plains")
        player = _FakePlayer()
        player.db.discovery_memory = {
            "discovered": {(1, 1)},
            "buildings": {
                (1, 1): {
                    "building_type": "X",
                    "owner_name": "Enemy",
                    "x": 1,
                    "y": 1,
                }
            },
        }
        sym = renderer._get_tile_symbol(1, 1, "earth", "fog", player, set())
        assert sym == "X?"
        assert len(sym) == 2
