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
    def __init__(self):
        self.ticks = []

    def process_tick(self, tick_number):
        self.ticks.append(tick_number)


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
        script = GameTickScript()
        agent_sys = FakeAgentSystem()
        systems = {"agent_system": agent_sys}

        steps = script._build_tick_steps(systems, tick_number=42)
        step_names = [name for name, _ in steps]
        self.assertIn("agent_processing", step_names)

        # Execute the agent_processing step
        for name, fn in steps:
            if name == "agent_processing":
                fn()
        self.assertEqual(agent_sys.ticks, [42])

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


if __name__ == "__main__":
    unittest.main()
