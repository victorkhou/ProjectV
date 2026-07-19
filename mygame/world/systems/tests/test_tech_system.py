"""
Unit tests for TechLabSystem.

Tests:
- Research timer countdown and completion
- Rank-gated research rejection
- Resource deduction on research start
- Technology effect application

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

import sys
import types
import unittest

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

from mygame.world.systems.tech_system import TechLabSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig,
    RankDef,
    TechnologyDef,
)
from mygame.world.event_bus import EventBus, TECHNOLOGY_RESEARCHED  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self):
        self.rank_level = 5
        self.researched_techs = set()
        self.hp = 100
        self.hp_max = 100

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", rank_level=5, resources=None):
        self.key = name
        self.db = FakeDB()
        self.db.rank_level = rank_level
        self._resources = {
            "Straw": 0, "Clay": 0, "Wood": 0, "Stone": 0, "Iron": 0,
            "Energy": 0, "Metals": 0, "Circuits": 0,
        }
        if resources:
            self._resources.update(resources)

    def get_resource(self, resource_type: str) -> int:
        return self._resources.get(resource_type, 0)

    def add_resource(self, resource_type: str, amount: int) -> None:
        self._resources[resource_type] = self._resources.get(resource_type, 0) + amount

    def has_resources(self, costs: dict) -> bool:
        return all(self._resources.get(r, 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs: dict) -> bool:
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            self._resources[r] = self._resources.get(r, 0) - amt
        return True

SAMPLE_RANKS = [
    RankDef(name="Recruit", level=0, xp_threshold=0),
    RankDef(name="Private", level=1, xp_threshold=100),
    RankDef(name="Corporal", level=2, xp_threshold=300),
    RankDef(name="Sergeant", level=3, xp_threshold=600),
    RankDef(name="Captain", level=5, xp_threshold=1500),
]

SAMPLE_TECHS = {
    "reinforced_walls": TechnologyDef(
        name="Reinforced Walls", key="reinforced_walls",
        required_rank="Sergeant",
        resource_cost={"Stone": 200, "Iron": 100},
        research_ticks=5,
        effect_type="stat_bonus",
        effect_value={"building_hp": 50},
    ),
    "basic_armor": TechnologyDef(
        name="Basic Armor", key="basic_armor",
        required_rank="Recruit",
        resource_cost={"Wood": 50},
        research_ticks=3,
        effect_type="stat_bonus",
        effect_value={"damage_reduction": 20},
    ),
    "advanced_weapons": TechnologyDef(
        name="Advanced Weapons", key="advanced_weapons",
        required_rank="Captain",
        resource_cost={"Iron": 500, "Energy": 200},
        research_ticks=10,
        effect_type="stat_bonus",
        effect_value={"damage": 15},
    ),
}

def _make_registry():
    """Create a DataRegistry with test definitions."""
    registry = DataRegistry()
    registry.ranks = list(SAMPLE_RANKS)
    registry.technologies = dict(SAMPLE_TECHS)
    registry.balance = BalanceConfig()
    return registry

def _make_system(registry=None, event_bus=None):
    """Create a TechLabSystem with optional overrides."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    return TechLabSystem(registry, event_bus), event_bus

# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestTechLabResearchTimer(unittest.TestCase):
    """Test research timer countdown and completion.

    Requirements: 8.2, 8.3
    """

    def test_research_completes_after_exact_ticks(self):
        """Research completes after exactly research_ticks process_tick calls."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=5, resources={"Wood": 100})

        ok, msg = system.start_research(player, "basic_armor")
        self.assertTrue(ok, msg)

        # Process 2 ticks (research_ticks=3, so not done yet)
        system.process_tick()
        system.process_tick()
        self.assertNotIn("basic_armor", player.db.researched_techs)

        # 3rd tick completes it
        system.process_tick()
        self.assertIn("basic_armor", player.db.researched_techs)

    def test_research_not_complete_before_timer(self):
        """Research is not complete before the timer runs out."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=5, resources={"Wood": 100})

        system.start_research(player, "basic_armor")

        # Only 1 tick
        system.process_tick()
        self.assertNotIn("basic_armor", player.db.researched_techs)

    def test_research_publishes_event_on_completion(self):
        """technology_researched event is published when research completes."""
        system, bus = _make_system()
        events = []
        bus.subscribe(TECHNOLOGY_RESEARCHED, lambda **kw: events.append(kw))

        player = FakePlayer(rank_level=5, resources={"Wood": 100})
        system.start_research(player, "basic_armor")

        for _ in range(3):
            system.process_tick()

        self.assertEqual(len(events), 1)
        self.assertIs(events[0]["player"], player)
        self.assertEqual(events[0]["technology"].key, "basic_armor")

    def test_multiple_researches_complete_independently(self):
        """Multiple research projects complete at their own timers."""
        system, bus = _make_system()
        player = FakePlayer(
            rank_level=5,
            resources={"Wood": 200, "Stone": 300, "Iron": 200},
        )

        system.start_research(player, "basic_armor")  # 3 ticks
        system.start_research(player, "reinforced_walls")  # 5 ticks

        for _ in range(3):
            system.process_tick()

        self.assertIn("basic_armor", player.db.researched_techs)
        self.assertNotIn("reinforced_walls", player.db.researched_techs)

        system.process_tick()
        system.process_tick()

        self.assertIn("reinforced_walls", player.db.researched_techs)

class TestTechLabRankGating(unittest.TestCase):
    """Test rank-gated research rejection.

    Requirements: 8.4
    """

    def test_reject_research_above_rank(self):
        """Research requiring a higher rank is rejected."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=1, resources={"Stone": 999, "Iron": 999})

        ok, msg = system.start_research(player, "reinforced_walls")
        self.assertFalse(ok)
        self.assertIn("Requires rank", msg)

    def test_allow_research_at_sufficient_rank(self):
        """Research is allowed when player meets the rank requirement."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=3, resources={"Stone": 999, "Iron": 999})

        ok, msg = system.start_research(player, "reinforced_walls")
        self.assertTrue(ok, msg)

    def test_list_available_filters_by_rank(self):
        """list_available only returns techs at or below player rank."""
        system, bus = _make_system()

        # Rank 0 (Recruit) — only basic_armor (required_rank=Recruit)
        player_low = FakePlayer(rank_level=0)
        available = system.list_available(player_low)
        keys = [t.key for t in available]
        self.assertIn("basic_armor", keys)
        self.assertNotIn("reinforced_walls", keys)
        self.assertNotIn("advanced_weapons", keys)

        # Rank 5 (Captain) — all techs available
        player_high = FakePlayer(rank_level=5)
        available = system.list_available(player_high)
        keys = [t.key for t in available]
        self.assertIn("basic_armor", keys)
        self.assertIn("reinforced_walls", keys)
        self.assertIn("advanced_weapons", keys)

class TestTechLabResourceDeduction(unittest.TestCase):
    """Test resource deduction on research start.

    Requirements: 8.2, 8.5
    """

    def test_resources_deducted_on_start(self):
        """Starting research deducts the required resources."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=5, resources={"Stone": 300, "Iron": 200})

        ok, msg = system.start_research(player, "reinforced_walls")
        self.assertTrue(ok, msg)

        self.assertEqual(player.get_resource("Stone"), 100)  # 300 - 200
        self.assertEqual(player.get_resource("Iron"), 100)   # 200 - 100

    def test_reject_insufficient_resources(self):
        """Research is rejected when player lacks resources."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=5, resources={"Stone": 50, "Iron": 10})

        ok, msg = system.start_research(player, "reinforced_walls")
        self.assertFalse(ok)
        # Uses the shared have/need breakdown (identical to build/upgrade/train).
        self.assertIn("Insufficient Resources:", msg)
        self.assertIn("Stone: 50/200", msg)
        self.assertIn("Iron: 10/100", msg)

    def test_resources_not_deducted_on_rejection(self):
        """Resources are not deducted when research is rejected."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=1, resources={"Stone": 999, "Iron": 999})

        # Rejected due to rank
        system.start_research(player, "reinforced_walls")

        self.assertEqual(player.get_resource("Stone"), 999)
        self.assertEqual(player.get_resource("Iron"), 999)

    def test_reject_already_researched(self):
        """Cannot research a technology that's already been researched."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=5, resources={"Wood": 200})

        ok, _ = system.start_research(player, "basic_armor")
        self.assertTrue(ok)

        # Complete it
        for _ in range(3):
            system.process_tick()

        # Try again
        ok2, msg2 = system.start_research(player, "basic_armor")
        self.assertFalse(ok2)
        self.assertIn("already researched", msg2)

class TestTechLabEffectApplication(unittest.TestCase):
    """Test technology effect application.

    Requirements: 8.3, 8.6
    """

    def test_stat_bonus_applied_on_completion(self):
        """R13.3: stat_bonus technology writes into db.tech_bonuses."""
        system, bus = _make_system()
        player = FakePlayer(rank_level=5, resources={"Wood": 100})
        player.db.tech_bonuses = {}

        system.start_research(player, "basic_armor")
        for _ in range(3):
            system.process_tick()

        # basic_armor effect_value is {"damage_reduction": 20}
        bonuses = player.db.tech_bonuses or {}
        self.assertEqual(bonuses.get("damage_reduction"), 20)

    def test_item_unlock_does_not_crash(self):
        """item_unlock effect type completes without error."""
        system, bus = _make_system()
        player = FakePlayer(
            rank_level=5,
            resources={"Iron": 999, "Energy": 999},
        )

        ok, _ = system.start_research(player, "advanced_weapons")
        self.assertTrue(ok)

        for _ in range(10):
            system.process_tick()

        self.assertIn("advanced_weapons", player.db.researched_techs)

if __name__ == "__main__":
    unittest.main()
