"""
Unit tests for RankSystem.

Tests XP award/deduction, promotion, demotion, tech/powerup
unlock/revoke, and status reporting.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10
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

from mygame.world.systems.rank_system import RankSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import RankDef, TechnologyDef, PowerupDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, combat_xp=0, rank_level=1, researched_techs=None):
        self.combat_xp = combat_xp
        self.rank_level = rank_level
        self.researched_techs = researched_techs if researched_techs is not None else set()

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", combat_xp=0, rank_level=1,
                 researched_techs=None):
        self.key = name
        self.db = FakeDB(
            combat_xp=combat_xp,
            rank_level=rank_level,
            researched_techs=researched_techs,
        )

def _make_test_ranks() -> list[RankDef]:
    """Create a small set of test ranks."""
    return [
        RankDef(name="Recruit", level=1, xp_threshold=0),
        RankDef(name="Private", level=2, xp_threshold=100),
        RankDef(name="Corporal", level=3, xp_threshold=300),
        RankDef(name="Sergeant", level=4, xp_threshold=600),
        RankDef(name="Captain", level=5, xp_threshold=1000),
    ]

def _make_test_techs() -> dict[str, TechnologyDef]:
    """Create test technologies gated by rank."""
    return {
        "basic_armor": TechnologyDef(
            name="Basic Armor", key="basic_armor",
            required_rank="Recruit",
        ),
        "improved_weapons": TechnologyDef(
            name="Improved Weapons", key="improved_weapons",
            required_rank="Corporal",
        ),
        "advanced_tactics": TechnologyDef(
            name="Advanced Tactics", key="advanced_tactics",
            required_rank="Captain",
        ),
    }

def _make_test_powerups() -> dict[str, PowerupDef]:
    """Create test powerups gated by rank."""
    return {
        "speed_boost": PowerupDef(
            name="Speed Boost", key="speed_boost",
            required_rank="Private",
            effect_type="speed", effect_value=1.5,
            duration_ticks=10, cooldown_ticks=30,
        ),
        "shield": PowerupDef(
            name="Shield", key="shield",
            required_rank="Sergeant",
            effect_type="defense", effect_value=2.0,
            duration_ticks=5, cooldown_ticks=60,
        ),
    }

def _make_registry() -> DataRegistry:
    """Create a DataRegistry with test rank/tech/powerup data."""
    registry = DataRegistry()
    registry.ranks = _make_test_ranks()
    registry.technologies = _make_test_techs()
    registry.powerups = _make_test_powerups()
    return registry

def _make_rank_system(registry=None, event_bus=None):
    """Create a RankSystem with test defaults."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    return RankSystem(registry=registry, event_bus=event_bus), event_bus

# -------------------------------------------------------------- #
#  Award XP Tests
# -------------------------------------------------------------- #

class TestAwardXP(unittest.TestCase):
    """Test XP awarding."""

    def test_award_xp_increases_total(self):
        player = FakePlayer(combat_xp=50)
        system, _ = _make_rank_system()
        system.award_xp(player, 30, "test")
        self.assertEqual(player.db.combat_xp, 80)

    def test_award_zero_xp_no_change(self):
        player = FakePlayer(combat_xp=50)
        system, _ = _make_rank_system()
        system.award_xp(player, 0, "test")
        self.assertEqual(player.db.combat_xp, 50)

    def test_award_negative_xp_no_change(self):
        player = FakePlayer(combat_xp=50)
        system, _ = _make_rank_system()
        system.award_xp(player, -10, "test")
        self.assertEqual(player.db.combat_xp, 50)

# -------------------------------------------------------------- #
#  Deduct XP Tests
# -------------------------------------------------------------- #

class TestDeductXP(unittest.TestCase):
    """Test XP deduction."""

    def test_deduct_xp_decreases_total(self):
        player = FakePlayer(combat_xp=200)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 50)
        self.assertEqual(player.db.combat_xp, 150)

    def test_deduct_xp_floors_at_zero(self):
        player = FakePlayer(combat_xp=30)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 100)
        self.assertEqual(player.db.combat_xp, 0)

    def test_deduct_zero_no_change(self):
        player = FakePlayer(combat_xp=200)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 0)
        self.assertEqual(player.db.combat_xp, 200)

# -------------------------------------------------------------- #
#  Promotion Tests
# -------------------------------------------------------------- #

class TestPromotion(unittest.TestCase):
    """Test rank promotion."""

    def test_promote_on_threshold(self):
        """Player at exactly the next rank threshold gets promoted."""
        player = FakePlayer(combat_xp=0, rank_level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 100, "kill")
        self.assertEqual(player.db.rank_level, 2)

    def test_promote_above_threshold(self):
        """Player above the next rank threshold gets promoted."""
        player = FakePlayer(combat_xp=0, rank_level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 150, "kill")
        self.assertEqual(player.db.rank_level, 2)

    def test_multi_rank_promotion(self):
        """Player can skip multiple ranks in one XP award."""
        player = FakePlayer(combat_xp=0, rank_level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 1000, "massive kill")
        self.assertEqual(player.db.rank_level, 5)  # Captain

    def test_no_promotion_below_threshold(self):
        """Player below next threshold stays at current rank."""
        player = FakePlayer(combat_xp=0, rank_level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 50, "damage")
        self.assertEqual(player.db.rank_level, 1)

    def test_promotion_publishes_event(self):
        events = []
        event_bus = EventBus()
        event_bus.subscribe("rank_promoted", lambda **kw: events.append(kw))
        system = RankSystem(registry=_make_registry(), event_bus=event_bus)

        player = FakePlayer(combat_xp=0, rank_level=1)
        system.award_xp(player, 100, "kill")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["old_rank"].name, "Recruit")
        self.assertEqual(events[0]["new_rank"].name, "Private")

    def test_promotion_unlocks_techs(self):
        """Promotion to Corporal (level 3) unlocks basic_armor and improved_weapons."""
        player = FakePlayer(combat_xp=0, rank_level=1, researched_techs=set())
        system, _ = _make_rank_system()
        system.award_xp(player, 300, "kill")
        self.assertEqual(player.db.rank_level, 3)
        self.assertIn("basic_armor", player.db.researched_techs)
        self.assertIn("improved_weapons", player.db.researched_techs)
        self.assertNotIn("advanced_tactics", player.db.researched_techs)

# -------------------------------------------------------------- #
#  Demotion Tests
# -------------------------------------------------------------- #

class TestDemotion(unittest.TestCase):
    """Test rank demotion."""

    def test_demote_below_threshold(self):
        """Player demoted when XP falls below current rank threshold."""
        player = FakePlayer(combat_xp=150, rank_level=2)  # Private, threshold=100
        system, _ = _make_rank_system()
        system.deduct_xp(player, 100)  # XP=50, below Private threshold
        self.assertEqual(player.db.rank_level, 1)  # Recruit

    def test_multi_rank_demotion(self):
        """Player can drop multiple ranks in one deduction."""
        player = FakePlayer(combat_xp=700, rank_level=4)  # Sergeant, threshold=600
        system, _ = _make_rank_system()
        system.deduct_xp(player, 650)  # XP=50, below Private threshold
        self.assertEqual(player.db.rank_level, 1)  # Recruit

    def test_no_demotion_above_threshold(self):
        """Player stays at rank if XP still meets threshold."""
        player = FakePlayer(combat_xp=200, rank_level=2)  # Private, threshold=100
        system, _ = _make_rank_system()
        system.deduct_xp(player, 50)  # XP=150, still above 100
        self.assertEqual(player.db.rank_level, 2)

    def test_demotion_publishes_event(self):
        events = []
        event_bus = EventBus()
        event_bus.subscribe("rank_demoted", lambda **kw: events.append(kw))
        system = RankSystem(registry=_make_registry(), event_bus=event_bus)

        player = FakePlayer(combat_xp=150, rank_level=2)
        system.deduct_xp(player, 100)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["old_rank"].name, "Private")
        self.assertEqual(events[0]["new_rank"].name, "Recruit")

    def test_demotion_revokes_techs(self):
        """Demotion from Corporal to Recruit revokes improved_weapons."""
        player = FakePlayer(
            combat_xp=350, rank_level=3,
            researched_techs={"basic_armor", "improved_weapons"},
        )
        system, _ = _make_rank_system()
        system.deduct_xp(player, 300)  # XP=50, Recruit
        self.assertEqual(player.db.rank_level, 1)
        self.assertIn("basic_armor", player.db.researched_techs)
        self.assertNotIn("improved_weapons", player.db.researched_techs)

# -------------------------------------------------------------- #
#  get_rank / get_status Tests
# -------------------------------------------------------------- #

class TestGetRankAndStatus(unittest.TestCase):
    """Test rank lookup and status reporting."""

    def test_get_rank_returns_correct_rank(self):
        player = FakePlayer(rank_level=3)
        system, _ = _make_rank_system()
        rank = system.get_rank(player)
        self.assertEqual(rank.name, "Corporal")
        self.assertEqual(rank.level, 3)

    def test_get_status_shows_xp_to_next(self):
        player = FakePlayer(combat_xp=150, rank_level=2)
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertEqual(status["rank_name"], "Private")
        self.assertEqual(status["combat_xp"], 150)
        self.assertEqual(status["xp_to_next_rank"], 150)  # 300 - 150
        self.assertEqual(status["next_rank_name"], "Corporal")

    def test_get_status_max_rank_no_next(self):
        player = FakePlayer(combat_xp=2000, rank_level=5)
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertEqual(status["rank_name"], "Captain")
        self.assertIsNone(status["xp_to_next_rank"])
        self.assertIsNone(status["next_rank_name"])

# -------------------------------------------------------------- #
#  Edge Cases
# -------------------------------------------------------------- #

class TestEdgeCases(unittest.TestCase):
    """Test edge cases."""

    def test_at_max_rank_no_promotion(self):
        """Player at max rank doesn't promote further."""
        player = FakePlayer(combat_xp=1000, rank_level=5)
        system, _ = _make_rank_system()
        system.award_xp(player, 5000, "overkill")
        self.assertEqual(player.db.rank_level, 5)

    def test_at_min_rank_no_demotion(self):
        """Player at rank 1 with 0 XP doesn't demote further."""
        player = FakePlayer(combat_xp=0, rank_level=1)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 100)
        self.assertEqual(player.db.rank_level, 1)
        self.assertEqual(player.db.combat_xp, 0)

    def test_exact_threshold_stays_at_rank(self):
        """Player at exactly their current rank threshold stays."""
        player = FakePlayer(combat_xp=300, rank_level=3)  # Corporal threshold=300
        system, _ = _make_rank_system()
        # No XP change, just check demotion
        system.check_demotion(player)
        self.assertEqual(player.db.rank_level, 3)

if __name__ == "__main__":
    unittest.main()
