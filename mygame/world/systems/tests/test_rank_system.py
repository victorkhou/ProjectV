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
from mygame.world.event_bus import EventBus, RANK_PROMOTED, RANK_DEMOTED  # noqa: E402

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


# -------------------------------------------------------------- #
#  Sub-Level Tests (Requirement 4b)
# -------------------------------------------------------------- #

class TestSubLevel(unittest.TestCase):
    """Test sub-level computation within ranks."""

    def test_sub_level_at_rank_start(self):
        """At the exact rank threshold, sub-level should be 1."""
        player = FakePlayer(combat_xp=100, rank_level=2)  # Private threshold=100
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 1)

    def test_sub_level_mid_rank(self):
        """Mid-rank XP should give an intermediate sub-level."""
        # Private: threshold=100, next=Corporal at 300
        # interval = (300-100)/5 = 40
        # Level 1: 100, Level 2: 140, Level 3: 180, Level 4: 220, Level 5: 260
        player = FakePlayer(combat_xp=180, rank_level=2)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 3)

    def test_sub_level_just_before_next_rank(self):
        """Just before next rank threshold should be level 5."""
        # Private: 100-299, interval=40, Level 5 starts at 260
        player = FakePlayer(combat_xp=299, rank_level=2)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 5)

    def test_sub_level_at_first_rank(self):
        """Recruit (level 1) with 0 XP should be sub-level 1."""
        # Recruit: threshold=0, next=Private at 100
        # interval = 100/5 = 20
        player = FakePlayer(combat_xp=0, rank_level=1)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 1)

    def test_sub_level_first_rank_mid(self):
        """Recruit with 50 XP: interval=20, level = 50//20 + 1 = 3."""
        player = FakePlayer(combat_xp=50, rank_level=1)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 3)

    def test_sub_level_max_rank_uses_fixed_interval(self):
        """Max rank (Captain in test data) uses 10000 XP fixed interval."""
        # Captain threshold=1000, no next rank, interval=10000
        player = FakePlayer(combat_xp=1000, rank_level=5)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 1)

    def test_sub_level_max_rank_level_2(self):
        """Max rank at 11000 XP: (11000-1000)//10000 + 1 = 2."""
        player = FakePlayer(combat_xp=11000, rank_level=5)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 2)

    def test_sub_level_max_rank_capped_at_5(self):
        """Max rank at very high XP should cap at level 5."""
        player = FakePlayer(combat_xp=100000, rank_level=5)
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 5)


# -------------------------------------------------------------- #
#  Sub-Level Notification Tests (Requirement 4b.3, 4b.4)
# -------------------------------------------------------------- #

class TestSubLevelNotification(unittest.TestCase):
    """Test sub-level change notifications on XP changes."""

    def test_notification_on_level_up(self):
        """Player should receive a message when sub-level changes."""
        # Private: threshold=100, interval=40
        # At 100 XP -> level 1, at 140 XP -> level 2
        messages = []
        player = FakePlayer(combat_xp=100, rank_level=2)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.award_xp(player, 40, "test")
        self.assertEqual(len(messages), 1)
        self.assertIn("Private", messages[0])
        self.assertIn("Level 2", messages[0])

    def test_no_notification_when_level_unchanged(self):
        """No message when sub-level doesn't change."""
        messages = []
        player = FakePlayer(combat_xp=100, rank_level=2)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.award_xp(player, 5, "small")
        self.assertEqual(len(messages), 0)

    def test_notification_on_deduct(self):
        """Sub-level notification fires on XP deduction too."""
        # Private: threshold=100, interval=40
        # At 140 XP -> level 2, at 139 -> level 1
        messages = []
        player = FakePlayer(combat_xp=140, rank_level=2)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 1)
        self.assertEqual(len(messages), 1)
        self.assertIn("Level 1", messages[0])

    def test_notification_replaces_underscores(self):
        """Rank names with underscores should be displayed with spaces."""
        # Use real ranks with underscores
        from mygame.world.definitions import RankDef
        ranks = [
            RankDef(name="Staff_Sergeant", level=1, xp_threshold=0, agent_cap=8),
            RankDef(name="Lieutenant", level=2, xp_threshold=200, agent_cap=10),
        ]
        registry = DataRegistry()
        registry.ranks = ranks
        registry.technologies = {}
        registry.powerups = {}
        system = RankSystem(registry=registry, event_bus=EventBus())

        messages = []
        player = FakePlayer(combat_xp=0, rank_level=1)
        player.msg = lambda m: messages.append(m)
        # interval = 200/5 = 40, level 1 at 0, level 2 at 40
        system.award_xp(player, 40, "test")
        self.assertEqual(len(messages), 1)
        self.assertIn("Staff Sergeant", messages[0])
        self.assertNotIn("_", messages[0])


# -------------------------------------------------------------- #
#  Agent Cap in Events Tests (Requirements 4.5, 4.6, 4.8)
# -------------------------------------------------------------- #

class TestAgentCapInEvents(unittest.TestCase):
    """Test that promotion/demotion events include new_agent_cap."""

    def _make_ranks_with_caps(self):
        from mygame.world.definitions import RankDef
        return [
            RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
            RankDef(name="Private", level=2, xp_threshold=100, agent_cap=3),
            RankDef(name="Corporal", level=3, xp_threshold=300, agent_cap=4),
        ]

    def test_promotion_event_includes_agent_cap(self):
        registry = DataRegistry()
        registry.ranks = self._make_ranks_with_caps()
        registry.technologies = {}
        registry.powerups = {}
        bus = EventBus()
        system = RankSystem(registry=registry, event_bus=bus)

        events = []
        bus.subscribe(RANK_PROMOTED, lambda **kw: events.append(kw))

        player = FakePlayer(combat_xp=0, rank_level=1)
        system.award_xp(player, 100, "test")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["new_agent_cap"], 3)
        self.assertEqual(events[0]["new_rank"].name, "Private")

    def test_demotion_event_includes_agent_cap(self):
        registry = DataRegistry()
        registry.ranks = self._make_ranks_with_caps()
        registry.technologies = {}
        registry.powerups = {}
        bus = EventBus()
        system = RankSystem(registry=registry, event_bus=bus)

        events = []
        bus.subscribe(RANK_DEMOTED, lambda **kw: events.append(kw))

        player = FakePlayer(combat_xp=300, rank_level=3)
        system.deduct_xp(player, 250)  # XP=50, should demote to Recruit

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["new_agent_cap"], 2)
        self.assertEqual(events[0]["new_rank"].name, "Recruit")
        self.assertEqual(events[0]["old_rank"].name, "Corporal")


# -------------------------------------------------------------- #
#  Planet Access Gating Tests (Requirements 1.4, 4.7)
# -------------------------------------------------------------- #

class FakePlanetRegistry:
    """Lightweight stand-in for PlanetRegistry."""
    def __init__(self, spaces):
        self._spaces = spaces

    def get_space(self, planet_key):
        if planet_key not in self._spaces:
            raise KeyError(planet_key)
        return self._spaces[planet_key]


class TestPlanetAccessGating(unittest.TestCase):
    """Test planet access gating based on rank vs rank_requirement."""

    def _make_system_with_planets(self):
        from mygame.world.definitions import CoordinateSpaceDef
        spaces = {
            "terra": CoordinateSpaceDef(
                planet_key="terra", planet_type="earth",
                width=500, height=500, terrain_seed=42,
                rank_requirement=1,
            ),
            "forge": CoordinateSpaceDef(
                planet_key="forge", planet_type="industrial",
                width=400, height=400, terrain_seed=7,
                rank_requirement=3,
            ),
            "inferno": CoordinateSpaceDef(
                planet_key="inferno", planet_type="volcanic",
                width=300, height=300, terrain_seed=66,
                rank_requirement=6,
            ),
        }
        planet_registry = FakePlanetRegistry(spaces)
        registry = _make_registry()
        bus = EventBus()
        system = RankSystem(registry=registry, event_bus=bus,
                            planet_registry=planet_registry)
        return system

    def test_can_access_terra_at_rank_1(self):
        system = self._make_system_with_planets()
        player = FakePlayer(rank_level=1)
        self.assertTrue(system.can_access_planet(player, "terra"))

    def test_cannot_access_forge_at_rank_1(self):
        system = self._make_system_with_planets()
        player = FakePlayer(rank_level=1)
        self.assertFalse(system.can_access_planet(player, "forge"))

    def test_can_access_forge_at_rank_3(self):
        system = self._make_system_with_planets()
        player = FakePlayer(rank_level=3)
        self.assertTrue(system.can_access_planet(player, "forge"))

    def test_can_access_forge_above_requirement(self):
        system = self._make_system_with_planets()
        player = FakePlayer(rank_level=5)
        self.assertTrue(system.can_access_planet(player, "forge"))

    def test_cannot_access_inferno_at_rank_5(self):
        system = self._make_system_with_planets()
        player = FakePlayer(rank_level=5)
        self.assertFalse(system.can_access_planet(player, "inferno"))

    def test_unknown_planet_denied(self):
        system = self._make_system_with_planets()
        player = FakePlayer(rank_level=5)
        self.assertFalse(system.can_access_planet(player, "nonexistent"))

    def test_no_planet_registry_allows_all(self):
        """When no planet_registry is set, access is allowed by default."""
        system, _ = _make_rank_system()
        player = FakePlayer(rank_level=1)
        self.assertTrue(system.can_access_planet(player, "anything"))


# -------------------------------------------------------------- #
#  get_status with Sub-Level Tests
# -------------------------------------------------------------- #

class TestGetStatusWithSubLevel(unittest.TestCase):
    """Test that get_status includes sub-level info."""

    def test_status_includes_sub_level(self):
        player = FakePlayer(combat_xp=150, rank_level=2)
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertIn("sub_level", status)
        # Private: threshold=100, interval=40
        # 150-100=50, 50//40=1, level=2
        self.assertEqual(status["sub_level"], 2)

    def test_status_includes_xp_to_next_level(self):
        player = FakePlayer(combat_xp=150, rank_level=2)
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertIn("xp_to_next_level", status)
        # Private: threshold=100, interval=40, sub_level=2
        # next level XP = 100 + 2*40 = 180, xp_to_next_level = 180-150 = 30
        self.assertEqual(status["xp_to_next_level"], 30)

    def test_status_max_rank_sub_level(self):
        player = FakePlayer(combat_xp=1000, rank_level=5)
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertEqual(status["sub_level"], 1)
        # Max rank, interval=10000, next level at 1000+10000=11000
        self.assertEqual(status["xp_to_next_level"], 10000)


if __name__ == "__main__":
    unittest.main()
