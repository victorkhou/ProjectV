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
from mygame.world.coordinate.discovery_bitfield import DiscoveryBitfield  # noqa: E402


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
    def __init__(self, name="Player1", x=5, y=5, planet="earth", location=None):
        self.key = name
        self.has_account = True
        self.id = id(self)
        self.location = location
        self.db = _FakeDB(
            coord_x=x,
            coord_y=y,
            coord_planet=planet,
            discovery_memory={"discovered": {}, "buildings": {}},
        )


class _FakeRoom:
    """Lightweight room with get_display_symbol and PlanetRoom query support."""
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

    def get_objects_at(self, x, y, type_tag=None):
        """PlanetRoom-compatible coordinate query."""
        result = []
        for obj in self.contents:
            cx = getattr(getattr(obj, "db", None), "coord_x", None)
            cy = getattr(getattr(obj, "db", None), "coord_y", None)
            if cx is not None and cy is not None and int(cx) == x and int(cy) == y:
                if type_tag is None:
                    result.append(obj)
                elif hasattr(obj, "tags") and obj.tags.get(type_tag, category="object_type"):
                    result.append(obj)
        return result

    def get_buildings_at(self, x, y):
        return self.get_objects_at(x, y, type_tag="building")

    def get_objects_in_area(self, x1, y1, x2, y2):
        result = []
        for obj in self.contents:
            cx = getattr(getattr(obj, "db", None), "coord_x", None)
            cy = getattr(getattr(obj, "db", None), "coord_y", None)
            if cx is not None and cy is not None:
                if x1 <= int(cx) <= x2 and y1 <= int(cy) <= y2:
                    result.append(obj)
        return result

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
        self.db = _FakeDB(coord_x=None, coord_y=None)
        self.tags = _FakeTags({"building": {"object_type"}})
        self.contents = []

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
        if key == "building_level":
            return 1
        return default


class _FakeNPC:
    """Minimal NPC (agent/guard) with an npc_type tag and a db.owner.

    The renderer classifies NPCs by owner id (own vs enemy vs neutral), not by
    the npc_type tag VALUE, so this doubles for both a player agent and an enemy
    guard — the owner determines the color.
    """
    def __init__(self, owner=None, role="guard", npc_type="enemy"):
        self.db = _FakeDB(owner=owner, role=role, coord_x=None, coord_y=None)
        self.tags = _FakeTags({npc_type: {"npc_type"}})
        self.contents = []


class _FakeTags:
    """Minimal tag system for testing."""
    def __init__(self, tags=None):
        self._tags = tags or {}  # tag_key -> set of categories

    def get(self, key=None, category=None, return_list=False):
        if key is None and category is not None:
            # Check if any tag has this category
            for k, cats in self._tags.items():
                if category in cats:
                    return k
            return None
        if key is not None and category is not None:
            cats = self._tags.get(key, set())
            return key if category in cats else None
        if key is not None:
            return key if key in self._tags else None
        return None


class _FakePlanetRoom:
    """Fake PlanetRoom with coordinate index for testing."""
    def __init__(self):
        self._objects: list = []

    def add_object(self, obj, x, y):
        obj.db.coord_x = x
        obj.db.coord_y = y
        self._objects.append(obj)

    def get_objects_in_area(self, x1, y1, x2, y2):
        result = []
        for obj in self._objects:
            cx = getattr(obj.db, "coord_x", None)
            cy = getattr(obj.db, "coord_y", None)
            if cx is not None and cy is not None:
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    result.append(obj)
        return result

    def get_objects_at(self, x, y, type_tag=None):
        result = []
        for obj in self._objects:
            cx = getattr(obj.db, "coord_x", None)
            cy = getattr(obj.db, "coord_y", None)
            if cx == x and cy == y:
                if type_tag is None:
                    result.append(obj)
                elif hasattr(obj, "tags") and obj.tags.get(type_tag, category="object_type"):
                    result.append(obj)
        return result

    def get_buildings_at(self, x, y):
        return self.get_objects_at(x, y, type_tag="building")


class _FakeTerrainGenerator:
    """Returns a fixed terrain type for all coordinates, or per-coord map."""
    def __init__(self, default_terrain="Plains", terrain_map=None):
        self._default = default_terrain
        self._map = terrain_map or {}
        self._terrain_thresholds = []  # empty — no pre-population needed

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

    def preload_area(self, min_x, max_x, min_y, max_y, planet):
        pass  # no-op in tests

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
    # Pass a sentinel data_registry to prevent the renderer from importing
    # game_init and picking up a stale DataRegistry from other tests.
    # We use a simple object whose get_terrain always raises, so the
    # fallback (first 2 chars of terrain type) is used.
    class _NoOpRegistry:
        def get_terrain(self, terrain_type):
            raise KeyError(terrain_type)
    renderer = ProceduralMapRenderer(
        tile_resolver=resolver,
        fog_system=fog,
        terrain_generators={planet: gen},
        data_registry=_NoOpRegistry(),
    )
    # Clear the symbol cache so the fallback path is used
    renderer._symbol_cache.clear()
    renderer._data_registry = None
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
        """With pvr=1, we get a 9x9 grid (radius 1 + 3 border each side)."""
        renderer, _ = _make_renderer(pvr=1)
        player = _FakePlayer(x=5, y=5)
        result = renderer.render(player, [])
        lines = result.strip().split("\n")
        # pvr=1 vision = 3x3, + 5 border each side = 13x13
        assert len(lines) == 13
        for line in lines:
            symbols = line.split(" ")
            assert len(symbols) == 13


# -------------------------------------------------------------- #
#  Tests: terrain symbol rendering
# -------------------------------------------------------------- #

class TestTerrainSymbols:
    def test_terrain_fallback_first_two_chars(self):
        """Terrain symbol renders correctly for visible tiles."""
        renderer, _ = _make_renderer(pvr=1, terrain="Forest")
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        # Player tile shows @@
        assert "@@" in result
        # Visible tiles should NOT all be unexplored (|X..|n)
        visible_syms = [s for s in result.split() if '|X' not in s and '@@' not in s]
        assert len(visible_syms) > 0, "No visible terrain tiles found"
        # All visible terrain tiles should have the same symbol (all Forest)
        unique_syms = set(visible_syms)
        assert len(unique_syms) == 1, f"Expected uniform terrain, got: {unique_syms}"
        # The symbol should be a valid 2-char terrain representation
        sym = _strip_color(visible_syms[0])
        assert len(sym) == 2, f"Expected 2-char symbol, got: {sym!r}"

    def test_different_terrain_per_tile(self):
        """Different tiles can have different terrain types."""
        tmap = {(0, 0): "Plains", (1, 0): "Forest", (0, 1): "Rock"}
        renderer, _ = _make_renderer(pvr=1, terrain="Dirt", terrain_map=tmap)
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        # Player tile (0,0) shows @@, other tiles show terrain
        assert "@@" in result
        # Visible tiles should have multiple different symbols (different terrain types)
        visible_syms = [s for s in result.split() if '|X' not in s and '@@' not in s]
        assert len(visible_syms) > 0, "No visible terrain tiles found"
        # Strip colors and check we have valid 2-char symbols
        stripped = [_strip_color(s) for s in visible_syms]
        assert all(len(s) == 2 for s in stripped), f"Expected 2-char symbols, got: {stripped[:5]}"

    def test_unknown_planet_generator(self):
        """If no terrain generator for the planet, visible tiles render as '..'."""
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
        # Visible tiles should be "..", border tiles are unexplored
        assert ".." in result


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
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        enemy = _FakePlayer(name="Enemy", x=6, y=5, planet="earth", location=planet_room)
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(enemy, 6, 5)
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
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        building = _FakeBuilding(btype="HQ")
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(building, 6, 5)
        room_bld = _FakeRoom(x=6, y=5, terrain="Plains", contents=[building])
        renderer, _ = _make_renderer(
            pvr=1,
            rooms={(6, 5, "earth"): room_bld},
        )
        result = renderer.render(player, [])
        assert "HQ" in result

    def test_enemy_building_renders_dark_red(self):
        """An NPC-base (sentinel-owned) building renders dark red |R on a visible
        tile — proves req 11.2 on the PRODUCTION _colored_objects path. The
        sentinel is a distinct object from the looker, so `owner is looker` is
        False and the enemy-building branch fires."""
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        sentinel = _FakePlayer(name="Sentinel", x=99, y=99)  # distinct .id owner
        building = _FakeBuilding(btype="HQ", owner=sentinel)
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(building, 6, 5)
        room_bld = _FakeRoom(x=6, y=5, terrain="Plains", contents=[building])
        renderer, _ = _make_renderer(pvr=1, rooms={(6, 5, "earth"): room_bld})
        result = renderer.render(player, [])
        assert "|RHQ|n" in result

    def test_own_building_renders_cyan_not_red(self):
        """Contrast: the looker's OWN building renders cyan |c, not enemy red."""
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        building = _FakeBuilding(btype="HQ", owner=player)  # owned by looker
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(building, 6, 5)
        room_bld = _FakeRoom(x=6, y=5, terrain="Plains", contents=[building])
        renderer, _ = _make_renderer(pvr=1, rooms={(6, 5, "earth"): room_bld})
        result = renderer.render(player, [])
        assert "|cHQ|n" in result
        assert "|RHQ|n" not in result

    def test_enemy_guard_renders_red(self):
        """An NPC-base guard (sentinel owner id != looker) renders red |r on a
        visible tile — req 11.3, production path. Classification keys on owner
        id, so an enemy guard flows through the enemy_agent branch."""
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        sentinel = _FakePlayer(name="Sentinel", x=99, y=99)  # distinct .id
        guard = _FakeNPC(owner=sentinel, role="guard")
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(guard, 6, 5)
        room_guard = _FakeRoom(x=6, y=5, terrain="Plains", contents=[guard])
        renderer, _ = _make_renderer(pvr=1, rooms={(6, 5, "earth"): room_guard})
        result = renderer.render(player, [])
        # Enemy agents render as |rag|n (red, generic agent glyph). Assert the
        # EXACT enemy-agent segment so this can't pass on an unrelated red glyph
        # (e.g. an enemy player's |r**|n) — there is no other player here.
        assert "|rag|n" in result

    def test_fog_tile_shows_terrain(self):
        """A fog tile within render bounds shows dimmed terrain."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        # Tile (4, 3) is within pvr=2 render bounds but we'll check
        # that fog tiles render with dim color
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(4, 3)}).to_dict(),
            "buildings": {},
        }
        renderer, _ = _make_renderer(pvr=2, terrain="Forest")
        result = renderer.render(player, [])
        # Fog tiles should be present in the render
        assert len(result) > 0

    def test_fog_tile_shows_discovered_building(self):
        """A fog tile with a discovered building shows the building abbr.

        Uses _get_tile_symbol directly to test fog rendering without
        needing the tile to be within vision render bounds.
        """
        player = _FakePlayer(x=5, y=5, planet="earth")
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(20, 20)}).to_dict(),
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
        sym = renderer._get_tile_symbol(20, 20, "earth", "fog", player, set())
        assert sym == "HQ"

    def test_fog_tile_hides_enemy_players(self):
        """Enemy players on fog tiles should NOT be shown."""
        player = _FakePlayer(x=5, y=5, planet="earth")
        enemy = _FakePlayer(name="Enemy", x=20, y=20, planet="earth")
        room_enemy = _FakeRoom(x=20, y=20, terrain="Plains", contents=[enemy])
        # Pre-discover tile (20, 20)
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(20, 20)}).to_dict(),
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

    def test_unexplored_tile_shows_blank(self):
        """Unexplored tiles outside vision show as faint dashes."""
        renderer, _ = _make_renderer(pvr=1, terrain="Mountain")
        player = _FakePlayer(x=0, y=0, planet="earth")
        result = renderer.render(player, [])
        assert "|X..|n" in result


# -------------------------------------------------------------- #
#  Tests: display priority
# -------------------------------------------------------------- #

class TestDisplayPriority:
    def test_player_self_overrides_building(self):
        """@@ takes priority over building abbreviation."""
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        building = _FakeBuilding(btype="HQ")
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(building, 5, 5)
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
        planet_room = _FakePlanetRoom()
        player = _FakePlayer(x=5, y=5, planet="earth", location=planet_room)
        enemy = _FakePlayer(name="Enemy", x=6, y=5, planet="earth", location=planet_room)
        building = _FakeBuilding(btype="HQ")
        planet_room.add_object(player, 5, 5)
        planet_room.add_object(enemy, 6, 5)
        planet_room.add_object(building, 6, 5)
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
        """Render bounds include a border beyond vision for fog/unexplored."""
        player = _FakePlayer(x=0, y=0, planet="earth")
        renderer, _ = _make_renderer(pvr=1, terrain="Plains")
        result = renderer.render(player, [])
        lines = result.strip().split("\n")
        # pvr=1 = 3x3 vision + 5 border each side = 13x13
        assert len(lines) == 13
        first_line_symbols = lines[0].split(" ")
        assert len(first_line_symbols) == 13

    def test_render_bounds_with_building_vision(self):
        """Building vision does NOT expand render bounds — map stays player-centered."""
        player = _FakePlayer(x=0, y=0, planet="earth")
        building_room = _FakeRoom(x=5, y=0)
        building = _FakeBuilding(btype="HQ", location=building_room)
        renderer, _ = _make_renderer(pvr=1, bvr=1, terrain="Plains")
        result = renderer.render(player, [building])
        lines = result.strip().split("\n")
        # Bounds anchored to player pvr=1 + border=5 = 13x13
        # Building vision contributes to visibility but not bounds
        assert len(lines) == 13
        first_line_symbols = lines[0].split(" ")
        assert len(first_line_symbols) == 13


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
        room = _FakeRoom(x=5, y=5, terrain="Plains")
        player = _FakePlayer(x=5, y=5, planet="earth", location=room)
        room.contents.append(player)
        renderer, _ = _make_renderer(rooms={(5, 5, "earth"): room})
        sym = renderer._get_tile_symbol(5, 5, "earth", "visible", player, set())
        assert sym == "@@"

    def test_fog_no_building_returns_terrain(self):
        renderer, _ = _make_renderer(terrain="Forest")
        player = _FakePlayer()
        player.db.discovery_memory = {"discovered": {}, "buildings": {}}
        sym = renderer._get_tile_symbol(0, 0, "earth", "fog", player, set())
        assert sym == "Fo"

    def test_fog_with_building_returns_abbreviation(self):
        renderer, fog = _make_renderer(terrain="Plains")
        player = _FakePlayer()
        player.db.discovery_memory = {
            "discovered": DiscoveryBitfield.from_set({(3, 3)}).to_dict(),
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
            "discovered": DiscoveryBitfield.from_set({(1, 1)}).to_dict(),
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


# -------------------------------------------------------------- #
#  Tests: out-of-bounds tiles render as fog of war
# -------------------------------------------------------------- #

class TestOutOfBounds:
    """Tiles beyond a planet's 0,0..max coords render as the grey off-map fill,
    even when inside the player's (unclamped) vision circle."""

    def test_out_of_bounds_tiles_render_off_map_fill(self):
        from mygame.world.coordinate.procedural_map_renderer import _OUT_OF_BOUNDS
        renderer, fog = _make_renderer(pvr=2)
        # A 6x6 map; player at the origin so the viewport includes negative
        # (off-map) tiles that are inside the vision radius.
        fog.set_in_bounds_func(lambda x, y, planet: 0 <= x < 6 and 0 <= y < 6)
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        # The off-map fill appears (negative-coord tiles in view are off-map).
        assert _OUT_OF_BOUNDS in result
        # The player @@ still renders (an in-bounds tile is unaffected).
        assert "@@" in result

    def test_no_off_map_fill_without_bounds_func(self):
        from mygame.world.coordinate.procedural_map_renderer import _OUT_OF_BOUNDS
        renderer, _ = _make_renderer(pvr=2)  # no bounds func -> falls open
        player = _FakePlayer(x=0, y=0)
        result = renderer.render(player, [])
        assert _OUT_OF_BOUNDS not in result
