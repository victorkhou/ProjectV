"""
Unit tests for GameTickScript new tick steps added in Task 5.3.

Tests:
- Agent processing step calls agent_system.process_tick
- Active-presence step calls building/resource system for players in building/harvesting state
- Combat timer decrement step clears expired timers
- Extractor production step calls resource_system.process_extractor_production
- AgentSystem is wired into game_systems dict
"""

import sys
import types
import unittest


# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
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

    class FakeDefaultScript:
        class _db:
            tick_count = 0
        class _ndb:
            systems = None
        db = _db()
        ndb = _ndb()

        def __init__(self, *a, **kw):
            self.db = type("db", (), {"tick_count": 0, "systems": None})()
            self.ndb = type("ndb", (), {"systems": None})()

    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {"DefaultScript": FakeDefaultScript})

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from typeclasses.scripts import GameTickScript  # noqa: E402


# -------------------------------------------------------------- #
#  Fake objects
# -------------------------------------------------------------- #

class FakeDB:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakePlayer:
    def __init__(self, activity_state="idle", combat_timer_expires=0):
        self.db = FakeDB(
            activity_state=activity_state,
            activity_target=None,
            activity_progress=0,
            combat_timer_expires=combat_timer_expires,
        )


class FakeAgentSystem:
    def __init__(self, agents=None):
        self.ticks = []
        self.rosters = []
        self._agents = agents or []

    def get_all_agents(self):
        return self._agents

    def process_tick(self, tick_number, agents=None):
        self.ticks.append(tick_number)
        self.rosters.append(agents)


class FakeBuildingSystem:
    def __init__(self):
        self.construction_ticks = []

    def process_construction_tick(self, player):
        self.construction_ticks.append(player)


class FakeResourceSystem:
    def __init__(self):
        self.harvest_ticks = []
        self.extractor_calls = []
        self.production_calls = []
        self.respawn_calls = []

    def process_harvest_tick(self, player):
        self.harvest_ticks.append(player)

    def process_extractor_production(self, buildings):
        self.extractor_calls.append(buildings)

    def process_production(self, buildings):
        self.production_calls.append(buildings)

    def process_respawns(self, tiles):
        self.respawn_calls.append(tiles)


class FakeEventBus:
    def __init__(self):
        self.published = []

    def publish(self, event_name, **kwargs):
        self.published.append((event_name, kwargs))


# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestAgentProcessingStep(unittest.TestCase):
    """Agent processing step calls agent_system.process_tick."""

    def test_agent_system_called_with_tick_number(self):
        roster = [object(), object()]
        script = GameTickScript()
        agent_sys = FakeAgentSystem(agents=roster)
        systems = {"agent_system": agent_sys}

        steps = script._build_tick_steps(systems, tick_number=42)
        step_names = [name for name, _ in steps]
        self.assertIn("agent_processing", step_names)

        # Execute the agent_processing step
        for name, fn in steps:
            if name == "agent_processing":
                fn()
        self.assertEqual(agent_sys.ticks, [42])
        # process_tick must be fed the cached roster (from _get_all_agents),
        # not left to re-scan the DB itself (Perf: no per-tick find_all_agents).
        self.assertEqual(agent_sys.rosters, [roster])

    def test_no_agent_step_when_system_missing(self):
        script = GameTickScript()
        systems = {}
        steps = script._build_tick_steps(systems, tick_number=1)
        step_names = [name for name, _ in steps]
        self.assertNotIn("agent_processing", step_names)


class TestActivePresenceStep(unittest.TestCase):
    """Active-presence step routes to building or resource system."""

    def _run_steps(self, systems, tick_number=1):
        script = GameTickScript()
        # Patch _get_online_players to return our fake players
        players = systems.pop("_test_players", [])
        script._get_online_players = lambda: players
        steps = script._build_tick_steps(systems, tick_number)
        for name, fn in steps:
            try:
                fn()
            except Exception:
                pass
        return steps

    def test_building_state_calls_construction_tick(self):
        player = FakePlayer(activity_state="building")
        bs = FakeBuildingSystem()
        systems = {
            "building_system": bs,
            "_test_players": [player],
        }
        self._run_steps(systems)
        self.assertIn(player, bs.construction_ticks)

    def test_harvesting_state_calls_harvest_tick(self):
        player = FakePlayer(activity_state="harvesting")
        rs = FakeResourceSystem()
        systems = {
            "resource_system": rs,
            "_test_players": [player],
        }
        self._run_steps(systems)
        self.assertIn(player, rs.harvest_ticks)

    def test_idle_state_calls_neither(self):
        player = FakePlayer(activity_state="idle")
        bs = FakeBuildingSystem()
        rs = FakeResourceSystem()
        systems = {
            "building_system": bs,
            "resource_system": rs,
            "_test_players": [player],
        }
        self._run_steps(systems)
        self.assertEqual(bs.construction_ticks, [])
        self.assertEqual(rs.harvest_ticks, [])


class TestCombatTimerDecrementStep(unittest.TestCase):
    """Combat timer decrement clears expired timers."""

    def _run_steps(self, players, tick_number):
        script = GameTickScript()
        script._get_online_players = lambda: players
        systems = {}
        steps = script._build_tick_steps(systems, tick_number)
        for name, fn in steps:
            try:
                fn()
            except Exception:
                pass

    def test_expired_timer_cleared(self):
        player = FakePlayer(combat_timer_expires=10)
        self._run_steps([player], tick_number=10)
        self.assertEqual(player.db.combat_timer_expires, 0)

    def test_future_timer_not_cleared(self):
        player = FakePlayer(combat_timer_expires=20)
        self._run_steps([player], tick_number=10)
        self.assertEqual(player.db.combat_timer_expires, 20)

    def test_zero_timer_stays_zero(self):
        player = FakePlayer(combat_timer_expires=0)
        self._run_steps([player], tick_number=5)
        self.assertEqual(player.db.combat_timer_expires, 0)


class TestExtractorProductionStep(unittest.TestCase):
    """Harvester-agent production is driven by HarvesterScript, not a tick step.

    The old ``extractor_production`` tick step was a second production driver
    for the same (extractor, agent) pairs and double-counted yield, so it was
    removed; HarvesterScript (run in the agent_processing step) is the single
    canonical driver per the agent-ai spec (Req 9.8).
    """

    def test_extractor_production_step_absent(self):
        script = GameTickScript()
        rs = FakeResourceSystem()
        systems = {"resource_system": rs}
        steps = script._build_tick_steps(systems, tick_number=1)
        step_names = [name for name, _ in steps]
        self.assertNotIn("extractor_production", step_names)

    def test_extractor_production_not_called_from_tick_loop(self):
        script = GameTickScript()
        rs = FakeResourceSystem()

        # Patch to inject buildings into tick_data
        script._get_online_players = lambda: []
        systems = {"resource_system": rs}
        steps = script._build_tick_steps(systems, tick_number=1)

        # Execute all steps — active_chunks populates tick_data
        for name, fn in steps:
            try:
                fn()
            except Exception:
                pass

        # The tick loop must not drive extractor production any more
        # (HarvesterScript does it per-agent in agent_processing).
        self.assertEqual(len(rs.extractor_calls), 0)


class TestStepOrdering(unittest.TestCase):
    """Verify new steps appear in the correct order."""

    def test_step_order(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        agent_sys = FakeAgentSystem()
        bs = FakeBuildingSystem()
        rs = FakeResourceSystem()
        eb = FakeEventBus()
        systems = {
            "agent_system": agent_sys,
            "building_system": bs,
            "resource_system": rs,
            "event_bus": eb,
        }
        steps = script._build_tick_steps(systems, tick_number=1)
        step_names = [name for name, _ in steps]

        # Agent processing before active presence
        self.assertLess(
            step_names.index("agent_processing"),
            step_names.index("active_presence"),
        )
        # Passive resource_production removed — Extractors require
        # player presence or a Harvester agent, not automatic production.
        self.assertNotIn("resource_production", step_names)
        # The extractor_production tick step was removed to stop double
        # production; HarvesterScript (in agent_processing) is the single
        # canonical harvester-agent production driver.
        self.assertNotIn("extractor_production", step_names)
        # Combat timer decrement exists
        self.assertIn("combat_timer_decrement", step_names)


class TestHpRegenStep(unittest.TestCase):
    """The hp_regen tick step feeds RegenSystem players + agents (not buildings)."""

    class _FakeRegen:
        def __init__(self, should_regen=True):
            self.calls = []  # (entities, tick_number)
            self._should_regen = should_regen
            self.gate_checks = []  # tick_numbers passed to should_regen_this_tick

        def should_regen_this_tick(self, tick_number):
            self.gate_checks.append(tick_number)
            return self._should_regen

        def process_tick(self, entities, tick_number):
            self.calls.append((list(entities), tick_number))

    class _FakeAgentSystemWithRoster(FakeAgentSystem):
        def __init__(self, agents):
            super().__init__()
            self._agents = agents

        def get_all_agents(self):
            return list(self._agents)

    def test_regen_step_registered_and_after_combat_timer(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        systems = {
            "regen_system": self._FakeRegen(),
            "event_bus": FakeEventBus(),
        }
        steps = script._build_tick_steps(systems, tick_number=1)
        names = [n for n, _ in steps]
        self.assertIn("hp_regen", names)
        self.assertLess(
            names.index("combat_timer_decrement"), names.index("hp_regen")
        )

    def test_regen_step_absent_without_system(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        steps = script._build_tick_steps({"event_bus": FakeEventBus()}, tick_number=1)
        self.assertNotIn("hp_regen", [n for n, _ in steps])

    def test_regen_fed_players_and_agents(self):
        player = object()
        agent = object()
        regen = self._FakeRegen()
        script = GameTickScript()
        script._get_online_players = lambda: [player]
        systems = {
            "regen_system": regen,
            "agent_system": self._FakeAgentSystemWithRoster([agent]),
            "event_bus": FakeEventBus(),
        }
        steps = dict(script._build_tick_steps(systems, tick_number=4))
        # active_chunks populates tick_data["online_players"]; run it first.
        steps["active_chunks"]()
        steps["hp_regen"]()
        self.assertEqual(len(regen.calls), 1)
        entities, tick = regen.calls[0]
        self.assertIn(player, entities)
        self.assertIn(agent, entities)
        self.assertEqual(tick, 4)

    def test_regen_skips_agent_scan_off_interval(self):
        """On an off-interval tick the step returns before enumerating agents.

        The interval gate is checked BEFORE get_all_agents(), so a tick where
        should_regen_this_tick is False must not touch the (expensive) roster.
        """
        agent_system = self._FakeAgentSystemWithRoster([object()])
        agent_system.get_all_agents_called = 0
        _orig = agent_system.get_all_agents

        def _counting():
            agent_system.get_all_agents_called += 1
            return _orig()

        agent_system.get_all_agents = _counting

        regen = self._FakeRegen(should_regen=False)
        script = GameTickScript()
        script._get_online_players = lambda: []
        systems = {
            "regen_system": regen,
            "agent_system": agent_system,
            "event_bus": FakeEventBus(),
        }
        steps = dict(script._build_tick_steps(systems, tick_number=3))
        steps["active_chunks"]()
        steps["hp_regen"]()

        # Gate consulted, but no regen work and no roster scan.
        self.assertEqual(regen.gate_checks, [3])
        self.assertEqual(len(regen.calls), 0)
        self.assertEqual(agent_system.get_all_agents_called, 0)


class TestGuardCombatStep(unittest.TestCase):
    """The guard_combat step runs guard AI before combat_resolution."""

    class _FakeGuardCombat:
        def __init__(self):
            self.calls = []  # (tick_number, guards, active_owner_ids)

        def process_tick(self, tick_number, guards, active_owner_ids=None):
            self.calls.append((tick_number, list(guards), active_owner_ids))

    class _FakeAgentSystemWithRoster(FakeAgentSystem):
        def __init__(self, agents, enemies=None):
            super().__init__()
            self._agents = agents
            self._enemies = enemies or []

        def get_all_agents(self):
            return list(self._agents)

        def get_all_enemies(self):
            return list(self._enemies)

    def test_guard_step_registered_before_combat_resolution(self):
        script = GameTickScript()
        script._get_online_players = lambda: []

        class _FakeCombatEngine:
            def resolve_tick(self, buildings):
                pass

            def process_turrets(self, buildings, active_owner_ids=None):
                pass

        systems = {
            "guard_combat_system": self._FakeGuardCombat(),
            "combat_engine": _FakeCombatEngine(),
            "event_bus": FakeEventBus(),
        }
        steps = script._build_tick_steps(systems, tick_number=1)
        names = [n for n, _ in steps]
        self.assertIn("guard_combat", names)
        self.assertLess(
            names.index("guard_combat"), names.index("combat_resolution")
        )

    def test_guard_step_absent_without_system(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        steps = script._build_tick_steps({"event_bus": FakeEventBus()}, tick_number=1)
        self.assertNotIn("guard_combat", [n for n, _ in steps])

    def test_guard_step_fed_agent_roster(self):
        from world import agent_index
        agent_index.bump()  # invalidate any cached roster from a prior test

        guard = object()
        gc = self._FakeGuardCombat()
        script = GameTickScript()
        script._get_online_players = lambda: []
        systems = {
            "guard_combat_system": gc,
            "agent_system": self._FakeAgentSystemWithRoster([guard]),
            "event_bus": FakeEventBus(),
        }
        steps = dict(script._build_tick_steps(systems, tick_number=7))
        steps["guard_combat"]()
        self.assertEqual(len(gc.calls), 1)
        tick, guards = gc.calls[0][0], gc.calls[0][1]
        self.assertEqual(tick, 7)
        self.assertIn(guard, guards)

    def test_guard_step_feeds_both_agents_and_enemies(self):
        """NPC-base guards (npc_type='enemy') must reach the guard AI too, else
        outposts never fight back (Phase 5 roster-feed dependency)."""
        from world import agent_index
        agent_index.bump()

        player_guard = object()
        enemy_guard = object()
        gc = self._FakeGuardCombat()
        script = GameTickScript()
        script._get_online_players = lambda: []
        systems = {
            "guard_combat_system": gc,
            "agent_system": self._FakeAgentSystemWithRoster(
                [player_guard], enemies=[enemy_guard]),
            "event_bus": FakeEventBus(),
        }
        steps = dict(script._build_tick_steps(systems, tick_number=3))
        steps["guard_combat"]()
        guards = gc.calls[0][1]
        self.assertIn(player_guard, guards)
        self.assertIn(enemy_guard, guards)


class TestOutpostRespawnStep(unittest.TestCase):
    """The outpost_respawn step drives the spawner's process_respawns."""

    class _FakeSpawner:
        def __init__(self):
            self.calls = []

        def process_respawns(self, tick_number):
            self.calls.append(tick_number)
            return 0

    def test_step_registered_and_calls_process_respawns(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        spawner = self._FakeSpawner()
        systems = {"outpost_spawner": spawner, "event_bus": FakeEventBus()}
        steps = dict(script._build_tick_steps(systems, tick_number=42))
        self.assertIn("outpost_respawn", steps)
        steps["outpost_respawn"]()
        self.assertEqual(spawner.calls, [42])

    def test_step_absent_without_spawner(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        steps = script._build_tick_steps({"event_bus": FakeEventBus()}, tick_number=1)
        self.assertNotIn("outpost_respawn", [n for n, _ in steps])


class TestOutpostStaleStep(unittest.TestCase):
    """The outpost_stale step drives the spawner's process_stale.

    Regression guard: a step registered in ``_build_tick_steps`` but MISSING from
    ``TICK_STEP_ORDER`` is silently dropped (emit only includes declared steps),
    so process_stale would never run and the 24h staleness decay would be dead
    despite passing unit tests that call process_stale directly.
    """

    class _FakeSpawner:
        def __init__(self):
            self.stale_calls = []
            self.respawn_calls = []

        def process_respawns(self, tick_number):
            self.respawn_calls.append(tick_number)
            return 0

        def process_stale(self, tick_number):
            self.stale_calls.append(tick_number)
            return 0

    def test_step_registered_and_calls_process_stale(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        spawner = self._FakeSpawner()
        systems = {"outpost_spawner": spawner, "event_bus": FakeEventBus()}
        steps = dict(script._build_tick_steps(systems, tick_number=99))
        # Must actually be EMITTED (i.e. present in TICK_STEP_ORDER), not just
        # in the `registered` dict — this is the exact wiring gap being guarded.
        self.assertIn("outpost_stale", steps)
        steps["outpost_stale"]()
        self.assertEqual(spawner.stale_calls, [99])

    def test_stale_runs_after_respawn(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        spawner = self._FakeSpawner()
        systems = {"outpost_spawner": spawner, "event_bus": FakeEventBus()}
        names = [n for n, _ in script._build_tick_steps(systems, tick_number=1)]
        self.assertLess(
            names.index("outpost_respawn"), names.index("outpost_stale")
        )

    def test_step_absent_without_spawner(self):
        script = GameTickScript()
        script._get_online_players = lambda: []
        steps = script._build_tick_steps({"event_bus": FakeEventBus()}, tick_number=1)
        self.assertNotIn("outpost_stale", [n for n, _ in steps])


class TestBuildingCacheInvalidation(unittest.TestCase):
    """_get_all_buildings caches the tag search and re-runs it only when a
    building is created/destroyed (the building-index generation advances)."""

    def setUp(self):
        from world import building_index
        self._building_index = building_index
        self.calls = 0

        # _get_all_buildings imports `from evennia.utils.search import
        # search_object_by_tag`. The lightweight test env stubs evennia.utils as
        # a plain module, so register a counting search module for the import.
        self._had_search = "evennia.utils.search" in sys.modules
        self._orig_search_mod = sys.modules.get("evennia.utils.search")
        search_mod = types.ModuleType("evennia.utils.search")

        def _counting_search(key=None, category=None):
            self.calls += 1
            return ["b1", "b2"]

        search_mod.search_object_by_tag = _counting_search
        sys.modules["evennia.utils.search"] = search_mod
        # Advance the generation so the cache from any prior test is stale.
        building_index.bump()

    def tearDown(self):
        if self._had_search:
            sys.modules["evennia.utils.search"] = self._orig_search_mod
        else:
            sys.modules.pop("evennia.utils.search", None)

    def test_repeated_calls_hit_cache_until_bump(self):
        script = GameTickScript()
        first = script._get_all_buildings()
        script._get_all_buildings()
        script._get_all_buildings()
        # Three calls, no building create/destroy -> one DB search.
        self.assertEqual(self.calls, 1)
        self.assertEqual(first, ["b1", "b2"])

        # A building create/destroy bumps the generation -> one re-search.
        self._building_index.bump()
        script._get_all_buildings()
        self.assertEqual(self.calls, 2)

        # Steady state again — no further searches.
        script._get_all_buildings()
        self.assertEqual(self.calls, 2)


class TestAgentCacheInvalidation(unittest.TestCase):
    """_get_all_agents caches agent_system.get_all_agents() and re-queries only
    when an agent NPC is created/deleted (the agent-index generation advances)."""

    class _CountingAgentSystem:
        def __init__(self):
            self.calls = 0

        def get_all_agents(self):
            self.calls += 1
            return ["a1", "a2"]

    def setUp(self):
        from world import agent_index
        self._agent_index = agent_index
        # Advance the generation so any cache from a prior test is stale.
        agent_index.bump()

    def test_repeated_calls_hit_cache_until_bump(self):
        script = GameTickScript()
        agent_system = self._CountingAgentSystem()

        first = script._get_all_agents(agent_system)
        script._get_all_agents(agent_system)
        script._get_all_agents(agent_system)
        # Three calls, no agent create/destroy -> one roster query.
        self.assertEqual(agent_system.calls, 1)
        self.assertEqual(first, ["a1", "a2"])

        # An agent create/destroy bumps the generation -> one re-query.
        self._agent_index.bump()
        script._get_all_agents(agent_system)
        self.assertEqual(agent_system.calls, 2)

        # Steady state again — no further queries.
        script._get_all_agents(agent_system)
        self.assertEqual(agent_system.calls, 2)

    def test_none_agent_system_returns_empty(self):
        script = GameTickScript()
        self.assertEqual(script._get_all_agents(None), [])


if __name__ == "__main__":
    unittest.main()
