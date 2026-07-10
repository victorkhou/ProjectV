"""
Regression tests for the production-wiring / real-Evennia-object gap.

The full unit suite is green, yet a holistic review (2026-07-10) found five
HIGH-severity bugs that only manifest against the REAL composition-root wiring
(``server/conf/game_init.py``) and the REAL Evennia object model. The unit
suite misses them because its fakes are, in the one place that matters,
*higher-fidelity-than-real*: a fake ``db`` raises ``AttributeError`` on a missing
attribute, whereas Evennia's ``DbHolder.__getattribute__`` returns ``None`` and
never raises. That single difference hides a predicate (``is_player``) that fails
*open* on every object.

These tests deliberately use an ``_EvenniaDb`` proxy that mimics the REAL
behaviour (missing attr -> ``None``, never raises) and a ``PlanetRoom``-shaped
location that exposes ``planet_name`` but NOT ``x``/``y``/``z`` (coordinates live
on the entity's ``db.coord_*``, as in production). Each test pins one of the five
findings; they fail against the pre-fix code and pass after the fix.

Findings covered:
  1. is_player() fail-open  -> a Building is misidentified as a player.
  2. tick clock frozen at 0  -> game_init must inject current_tick_func into
     BuildingSystem / CombatEngine / PowerupSystem.
  3. active-building list empty -> _compute_active_data must resolve the planet
     from the entity, not loc.z.
  4. combat XP bypasses progression -> a kill must recompute level / fire
     LEVEL_CHANGED.
  5. registry.get_coord_space() doesn't exist -> grid dims must resolve via the
     PlanetRegistry, not a missing method silently swallowed.
"""

import ast
import sys
import types
import unittest
from pathlib import Path


# ------------------------------------------------------------------ #
#  Evennia stubs (framework-free; mimic REAL db semantics)
# ------------------------------------------------------------------ #

def _ensure_evennia_stubs():
    if "evennia" in sys.modules and getattr(
        sys.modules["evennia"], "__file__", None
    ):
        return
    mods = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        for k, v in (attrs or {}).items():
            setattr(m, k, v)
        mods[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    for name, mod in mods.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from world.utils import is_player, is_building  # noqa: E402


class _EvenniaDb:
    """A ``db`` proxy that behaves like Evennia's ``DbHolder``.

    The crucial fidelity point: reading an attribute that was never set returns
    ``None`` and NEVER raises ``AttributeError`` — so ``hasattr(db, "anything")``
    is always ``True``. This is what breaks a ``hasattr``-based type predicate in
    production while a naive Python-object fake (which raises) hides the bug.
    """

    def __init__(self, **initial):
        object.__setattr__(self, "_store", dict(initial))

    def __getattr__(self, key):
        # Never raise — mirror DbHolder.__getattribute__ -> AttributeHandler.get.
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store")[key] = value


class _RealObj:
    """An object whose ``.db`` is an Evennia-faithful proxy."""

    def __init__(self, **initial):
        self.db = _EvenniaDb(**initial)


class _FakeBuildingDb(_RealObj):
    """A building as it exists at runtime: has ``building_type``, no ``combat_xp``."""

    def __init__(self, building_type="HQ"):
        super().__init__(building_type=building_type)


class _FakePlayerDb(_RealObj):
    """A player/agent: carries ``combat_xp`` (set by at_combat_entity_init)."""

    def __init__(self, combat_xp=0):
        super().__init__(combat_xp=combat_xp)


# ================================================================== #
#  Finding 1: is_player() must not fail open on real objects
# ================================================================== #

class TestIsPlayerFailOpen(unittest.TestCase):
    """is_player must return False for a Building even when db never raises."""

    def test_building_is_not_a_player_with_evennia_db(self):
        building = _FakeBuildingDb(building_type="HQ")
        # Sanity: the faithful db returns None (not raise) for combat_xp.
        self.assertIsNone(building.db.combat_xp)
        # The regression: pre-fix, hasattr(db, "combat_xp") is True -> misclassified.
        self.assertFalse(
            is_player(building),
            "a Building (no combat_xp value) must not be identified as a player",
        )

    def test_real_player_is_a_player(self):
        player = _FakePlayerDb(combat_xp=0)
        self.assertTrue(is_player(player), "a player with combat_xp set is a player")

    def test_building_is_building_not_player(self):
        building = _FakeBuildingDb(building_type="HQ")
        self.assertTrue(is_building(building))
        self.assertFalse(is_player(building))

    def test_death_routing_prefers_building_destruction_for_a_building(self):
        """The concrete blast radius: a 0-HP building must route to destruction,
        not player-respawn. Mirrors combat_engine._finalize_hit's branch order
        (enemy -> player -> building) using the real is_player/is_building."""
        building = _FakeBuildingDb(building_type="HQ")

        def route(target):
            # Same order as _finalize_hit; enemy check omitted (npc_type unset).
            if is_player(target):
                return "player_defeat"
            if is_building(target):
                return "building_destruction"
            return "none"

        self.assertEqual(
            route(building), "building_destruction",
            "a destroyed building must be routed to building destruction "
            "(so BUILDING_DESTROYED fires and base-elimination can run)",
        )


# ================================================================== #
#  Finding 2: game_init must inject current_tick_func into the 3 systems
# ================================================================== #

class TestTickClockInjectedStatically(unittest.TestCase):
    """Static (AST) assertion that game_init passes current_tick_func to the
    three time-sensitive systems. An AST check needs no live Evennia DB and
    pins the composition-root wiring the unit suite never boots."""

    @staticmethod
    def _ctor_kwargs(source: str, class_name: str):
        """Return the set of keyword names in the first `ClassName(...)` call."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == class_name
            ):
                return {kw.arg for kw in node.keywords if kw.arg}
        return None

    def setUp(self):
        self.src = (
            Path(__file__).resolve().parent.parent
            / "server" / "conf" / "game_init.py"
        ).read_text()

    def test_combat_engine_gets_tick_func(self):
        kwargs = self._ctor_kwargs(self.src, "CombatEngine")
        self.assertIsNotNone(kwargs, "CombatEngine(...) not found in game_init")
        self.assertIn(
            "current_tick_func", kwargs,
            "CombatEngine must be constructed with current_tick_func or its "
            "clock is frozen at 0 (combat lockout math breaks)",
        )

    def test_powerup_system_gets_tick_func(self):
        kwargs = self._ctor_kwargs(self.src, "PowerupSystem")
        self.assertIsNotNone(kwargs, "PowerupSystem(...) not found in game_init")
        self.assertIn(
            "current_tick_func", kwargs,
            "PowerupSystem must get current_tick_func or powerups expire the "
            "tick after activation (frozen-0 expiry vs real-tick process_tick)",
        )

    def test_building_system_gets_tick_func(self):
        kwargs = self._ctor_kwargs(self.src, "BuildingSystem")
        self.assertIsNotNone(kwargs, "BuildingSystem(...) not found in game_init")
        self.assertIn(
            "current_tick_func", kwargs,
            "BuildingSystem must get current_tick_func or the combat-lockout "
            "build gate reads a frozen-0 clock",
        )


# ================================================================== #
#  Finding 3: _compute_active_data must resolve planet from the entity
# ================================================================== #

class _PlanetRoomLike:
    """A PlanetRoom as it really is: has planet_name, NO x/y/z attributes."""

    def __init__(self, planet="earth"):
        self._planet = planet

    @property
    def planet_name(self):
        return self._planet


class _OnlinePlayer:
    """A player on a PlanetRoom, with coords on db (production shape)."""

    def __init__(self, room, x=10, y=10):
        self.location = room
        self.db = _EvenniaDb(coord_x=x, coord_y=y, coord_planet=room.planet_name)


def _building_at(planet, x, y, btype="HQ"):
    b = _FakeBuildingDb(building_type=btype)
    b.db.coord_x = x
    b.db.coord_y = y
    b.db.coord_planet = planet
    return b


class TestActiveBuildingsNonEmptyWithPlanetRoom(unittest.TestCase):
    """With an online player on a PlanetRoom (no loc.z), the tick's active
    buildings must NOT be empty. Exercises GameTickScript._compute_active_data
    driving the REAL WorldChunkManager (not a reimplementing stub), with
    production-shaped objects (coords on db, planet on db.coord_planet)."""

    def _make_script(self):
        from typeclasses.scripts import GameTickScript
        return GameTickScript.__new__(GameTickScript)

    def _real_chunking(self):
        from world.chunking import WorldChunkManager
        return WorldChunkManager(chunk_size=16)

    def test_compute_active_data_finds_buildings_for_online_player(self):
        room = _PlanetRoomLike("earth")
        player = _OnlinePlayer(room, x=10, y=10)
        building = _building_at("earth", 11, 10)

        script = self._make_script()
        script._get_all_buildings = lambda: [building]

        # Drives the REAL WorldChunkManager: this is what exercises the
        # chunking.py db.coord_x/coord_y fix (a stub would bypass it).
        active = script._compute_active_data(self._real_chunking(), [player])
        self.assertIn(
            building, active,
            "an online player on a PlanetRoom must yield a non-empty active "
            "building list via the real WorldChunkManager (coords read from the "
            "entity db, planet from db.coord_planet — not loc.z/x/y)",
        )

    def test_active_buildings_isolated_per_planet_no_cross_leak_or_dup(self):
        """Finding C: a building is active only when a player is on ITS planet,
        and appears at most once even with players on multiple planets."""
        earth_room = _PlanetRoomLike("earth")
        mars_room = _PlanetRoomLike("mars")
        earth_player = _OnlinePlayer(earth_room, x=10, y=10)
        mars_player = _OnlinePlayer(mars_room, x=10, y=10)  # same tile, other planet

        earth_bldg = _building_at("earth", 11, 10)
        mars_bldg = _building_at("mars", 11, 10)  # same tile as earth_bldg

        script = self._make_script()
        script._get_all_buildings = lambda: [earth_bldg, mars_bldg]

        active = script._compute_active_data(
            self._real_chunking(), [earth_player, mars_player]
        )
        # Each building appears exactly once (no duplicate from the per-planet
        # loop) and only its own planet's building is present per planet.
        self.assertEqual(active.count(earth_bldg), 1, "earth building duplicated")
        self.assertEqual(active.count(mars_bldg), 1, "mars building duplicated")

    def test_building_not_active_when_only_other_planet_has_players(self):
        """Finding C: no cross-planet activation — a building on mars is NOT
        active when the only online player is on earth (same chunk coords)."""
        earth_room = _PlanetRoomLike("earth")
        earth_player = _OnlinePlayer(earth_room, x=10, y=10)
        mars_bldg = _building_at("mars", 11, 10)  # same chunk as earth_player

        script = self._make_script()
        script._get_all_buildings = lambda: [mars_bldg]

        active = script._compute_active_data(self._real_chunking(), [earth_player])
        self.assertNotIn(
            mars_bldg, active,
            "a mars building must not be activated by an earth-only player "
            "(cross-planet chunk leak)",
        )


# ================================================================== #
#  Finding 4: combat XP must recompute progression / fire LEVEL_CHANGED
# ================================================================== #

class _RecordingBus:
    def __init__(self):
        self.published = []

    def publish(self, event, **kw):
        self.published.append((event, kw))

    def subscribe(self, *a, **k):
        pass


class _RecordingRankSystem:
    """Stands in for RankSystem; records award_xp/deduct_xp calls and, like the
    real one, recomputes db.level via CombatEntity.award_xp on the entity."""

    def __init__(self):
        self.awards = []   # (player, amount, reason)
        self.deducts = []  # (player, amount)

    def award_xp(self, player, amount, reason=""):
        self.awards.append((player, amount, reason))
        if hasattr(player, "award_xp"):
            player.award_xp(amount)

    def deduct_xp(self, player, amount):
        self.deducts.append((player, amount))
        if hasattr(player, "deduct_xp"):
            player.deduct_xp(amount)


class _LevelingPlayer:
    """A player-shaped attacker whose award_xp recomputes level from a trivial
    100-XP-per-level curve (independent of the global progression thresholds)."""

    def __init__(self):
        self.db = _EvenniaDb(combat_xp=0, level=1, rank_level=1)
        self.key = "Raider"

    def award_xp(self, amount):
        self.db.combat_xp = (self.db.combat_xp or 0) + amount
        self.db.level = 1 + (self.db.combat_xp // 100)
        return self.db.combat_xp


class TestCombatXpDrivesProgression(unittest.TestCase):
    """A combat kill's XP must flow through the progression path (RankSystem →
    recompute level/rank + LEVEL_CHANGED), not a raw db.combat_xp write."""

    def _engine(self, bus, rank_system=None):
        from world.systems.combat_engine import CombatEngine

        class _Reg:
            class balance:
                xp_kill = 500
                xp_building_destroy = 500
                xp_death_loss = 0
                combat_lockout_ticks = 5
        engine = CombatEngine(
            _Reg(), bus, current_tick_func=lambda: 42,
            player_xp_awarder_provider=(lambda: rank_system),
        )
        return engine

    def _kill_enemy(self, engine, attacker):
        victim = _RealObj(npc_type="enemy", owner=None, combat_xp=0,
                          coord_x=1, coord_y=1)
        victim.key = "Guard"
        victim.delete = lambda: None
        engine._handle_enemy_death(victim, attacker)

    def test_enemy_kill_routes_player_xp_through_rank_system(self):
        rank = _RecordingRankSystem()
        engine = self._engine(_RecordingBus(), rank_system=rank)
        attacker = _LevelingPlayer()

        self._kill_enemy(engine, attacker)

        self.assertEqual(
            len(rank.awards), 1,
            "a player kill must award XP through the RankSystem (which fires "
            "LEVEL_CHANGED / RANK_*), not via a raw db.combat_xp write",
        )
        self.assertEqual(rank.awards[0][0], attacker)
        self.assertEqual(rank.awards[0][1], 500)

    def test_enemy_kill_recomputes_attacker_level(self):
        # No rank system injected: the engine must still recompute via the
        # entity's own award_xp (the second fallback), never a raw set.
        engine = self._engine(_RecordingBus(), rank_system=None)
        attacker = _LevelingPlayer()
        level_before = attacker.db.level

        self._kill_enemy(engine, attacker)

        self.assertGreater(attacker.db.combat_xp, 0)
        self.assertGreater(
            attacker.db.level, level_before,
            "combat XP must recompute the attacker's level, not just bump "
            "db.combat_xp (killing enemies must be able to level you up)",
        )


# ================================================================== #
#  Finding 5: grid dimensions must resolve without a non-existent method
# ================================================================== #

class TestNoCallToMissingCoordSpaceMethod(unittest.TestCase):
    """DataRegistry has no get_coord_space() and PlanetDef has no coord_space.
    Grid-dimension resolution must not depend on that missing API (which today
    is silently swallowed by a broad except)."""

    def test_data_registry_has_no_get_coord_space(self):
        from world.data_registry import DataRegistry
        # If someone later ADDS get_coord_space, this test becomes moot but not
        # wrong; the real assertion is that pathfinding doesn't rely on it.
        self.assertFalse(
            hasattr(DataRegistry, "get_coord_space"),
            "DataRegistry.get_coord_space still doesn't exist — the pathfinding "
            "call site must resolve dimensions via the PlanetRegistry instead",
        )

    def test_pathfinding_resolves_dims_via_planet_registry(self):
        """compute_path_for_npc must pick up real width/height from the
        PlanetRegistry in game_systems, not fall through to the 100x100 default."""
        from world import pathfinding

        class _Space:
            width = 300
            height = 250

        class _PlanetReg:
            def get_space(self, key):
                return _Space()

        class _Reg:
            def get_terrain(self, t):
                return types.SimpleNamespace(passable=True)

        class _RoomDb:
            planet = "earth"

        class _Room:
            db = _RoomDb()
            _game_systems = {
                "registry": _Reg(),
                "planet_registry": _PlanetReg(),
                "_terrain_generators": {},  # no tgen -> bounds-only checker
            }

        class _Npc:
            location = _Room()

        # Straight vertical path (0,0) -> (0,120): reachable on a 250-tall planet
        # but OUT OF BOUNDS under the old 100x100 fallback (y=120 >= 100 fails).
        # A non-empty path therefore proves the real dimensions were resolved via
        # the PlanetRegistry rather than the missing get_coord_space path.
        path = pathfinding.compute_path_for_npc(_Npc(), (0, 0), (0, 120))
        self.assertTrue(
            path,
            "with a 300x250 planet from the PlanetRegistry, a path to (0,120) "
            "must exist; an empty path means dimensions fell back to 100x100 "
            "(the missing get_coord_space path)",
        )


# ================================================================== #
#  Round 2, Finding A: base-destroy XP must route through progression
# ================================================================== #

class TestBaseDestroyXpDrivesProgression(unittest.TestCase):
    """BaseEliminationHandler awards the largest XP grant in the game
    (xp_hq_destroy). It must flow through the RankSystem (recompute + events),
    not a raw db.combat_xp write."""

    def _handler(self, rank_system):
        from world.systems.base_elimination import BaseEliminationHandler

        class _Reg:
            class balance:
                xp_hq_destroy = 500

            def get_base_template(self, tier):
                return None
        return BaseEliminationHandler(
            _Reg(), _RecordingBus(),
            owned_entities_provider=lambda s: [],
            loot_drop_func=lambda *a, **k: None,
            player_xp_awarder_provider=lambda: rank_system,
        )

    def _sentinel(self):
        s = _RealObj(is_sentinel=True, base_tier="outpost", base_planet="earth")
        s.id = 9999
        return s

    def _hq(self):
        b = _FakeBuildingDb(building_type="HQ")
        b.db.coord_x = 5
        b.db.coord_y = 5
        b.location = None
        b.delete = lambda: None
        return b

    def test_base_destroy_routes_player_xp_through_rank_system(self):
        rank = _RecordingRankSystem()
        handler = self._handler(rank)
        attacker = _LevelingPlayer()  # real player: no npc_type

        handler._eliminate_base(self._sentinel(), self._hq(), attacker, None)

        self.assertEqual(
            len(rank.awards), 1,
            "destroying an NPC-base HQ must award xp_hq_destroy through the "
            "RankSystem (recompute level/rank + fire events), not a raw write",
        )
        self.assertEqual(rank.awards[0][1], 500)

    def test_base_destroy_no_rank_system_still_recomputes_level(self):
        handler = self._handler(None)  # no rank system injected
        attacker = _LevelingPlayer()
        level_before = attacker.db.level

        handler._eliminate_base(self._sentinel(), self._hq(), attacker, None)

        self.assertGreater(
            attacker.db.level, level_before,
            "without a rank system, base-destroy XP must still recompute the "
            "destroyer's level via the entity's own award_xp",
        )

    def test_enemy_npc_attacker_earns_no_base_xp(self):
        rank = _RecordingRankSystem()
        handler = self._handler(rank)
        # An enemy NPC satisfies is_player (carries combat_xp) but has npc_type.
        enemy = _RealObj(combat_xp=0, npc_type="enemy")

        handler._eliminate_base(self._sentinel(), self._hq(), enemy, None)

        self.assertEqual(
            len(rank.awards), 0,
            "an enemy-NPC attacker (npc_type set) must not earn base-destroy XP",
        )


# ================================================================== #
#  Round 2, Finding B: NPC passability must use real planet bounds
# ================================================================== #

class TestNpcPassabilityUsesRealBounds(unittest.TestCase):
    """typeclasses.npcs._is_tile_passable must resolve grid bounds from the
    PlanetRegistry, not a hardcoded 256x256 (which made every tile past 256 a
    movement dead-zone on larger planets)."""

    def _npc_on_planet(self, width, height):
        from typeclasses.npcs import NPC

        class _Space:
            pass
        space = _Space()
        space.width, space.height = width, height

        class _PlanetReg:
            def get_space(self, key):
                return space

        class _Terrain:
            passable = True

        class _Reg:
            def get_terrain(self, t):
                return _Terrain()

        class _Tgen:
            def get_terrain(self, x, y):
                return "grass"

        class _RoomDb:
            planet = "terra"

        class _Room:
            db = _RoomDb()
            _game_systems = {
                "_terrain_generators": {"terra": _Tgen()},
                "registry": _Reg(),
                "planet_registry": _PlanetReg(),
            }

            def get_buildings_at(self, x, y):
                return []  # no offline building blocking the tile

        npc = NPC.__new__(NPC)
        npc.location = _Room()
        return npc

    def test_tile_beyond_256_is_passable_on_a_500_wide_planet(self):
        npc = self._npc_on_planet(500, 500)
        # (300, 300) is out of bounds under the old 256 default (impassable) but
        # in-bounds on a 500x500 planet with all-passable terrain.
        self.assertTrue(
            npc._is_tile_passable(300, 300),
            "tile (300,300) on a 500x500 planet must be passable; a False here "
            "means the hardcoded 256x256 bound is still in effect",
        )

    def test_tile_outside_real_bounds_is_impassable(self):
        npc = self._npc_on_planet(500, 500)
        self.assertFalse(
            npc._is_tile_passable(600, 600),
            "tile (600,600) is outside a 500x500 planet and must be impassable",
        )


if __name__ == "__main__":
    unittest.main()
