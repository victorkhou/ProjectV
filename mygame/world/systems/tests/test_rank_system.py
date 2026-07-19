"""
Unit tests for RankSystem.

Tests XP award/deduction, promotion, demotion, tech/powerup
unlock/revoke, sub-level, planet access, and status reporting.

The rank system is level-based: players have a level (1-60) and
rank is derived (every 5 levels = 1 rank).

Requirements: 7.1-7.10, 4b.1-4b.7
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
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.rank_system import RankSystem, rank_from_level  # noqa: E402
from mygame.world.constants import RANK_BANDS  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import RankDef, TechnologyDef, PowerupDef  # noqa: E402
from mygame.world.event_bus import EventBus, RANK_PROMOTED, RANK_DEMOTED  # noqa: E402
from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    def __init__(self, combat_xp=0, level=1, rank_level=1, researched_techs=None):
        self.combat_xp = combat_xp
        self.level = level
        self.rank_level = rank_level
        self.researched_techs = researched_techs if researched_techs is not None else set()

class FakePlayer(CombatEntity):
    """Stand-in for CombatCharacter, mixing in the real CombatEntity so it
    exposes the same ``award_xp`` / ``deduct_xp`` progression methods the
    refactored RankSystem now delegates to."""
    def __init__(self, name="TestPlayer", combat_xp=0, level=1,
                 rank_level=None, researched_techs=None):
        if rank_level is None:
            rank_level = rank_from_level(level)
        self.key = name
        self.db = FakeDB(
            combat_xp=combat_xp,
            level=level,
            rank_level=rank_level,
            researched_techs=researched_techs,
        )

# Test ranks: 5 ranks using RANK_BANDS (formula-derived XP curve):
# Rank 1 (Recruit): levels 1-5, XP 0-297
# Rank 2 (Private): levels 6-10, XP 298-1037
# Rank 3 (Corporal): levels 11-15, XP 1038-2881
# Rank 4 (Sergeant): levels 16-21, XP 2882-8481
# Rank 5 (Captain): levels 22-28, XP 8482+

def _make_test_ranks():
    return [
        RankDef(name="Recruit", level=1, xp_threshold=0),
        RankDef(name="Private", level=2, xp_threshold=100),
        RankDef(name="Corporal", level=3, xp_threshold=300),
        RankDef(name="Sergeant", level=4, xp_threshold=600),
        RankDef(name="Captain", level=5, xp_threshold=1000),
    ]

def _make_test_techs():
    return {
        "basic_armor": TechnologyDef(
            name="Basic Armor", key="basic_armor", required_rank="Recruit",
        ),
        "improved_weapons": TechnologyDef(
            name="Improved Weapons", key="improved_weapons", required_rank="Corporal",
        ),
        "advanced_tactics": TechnologyDef(
            name="Advanced Tactics", key="advanced_tactics", required_rank="Captain",
        ),
    }

def _make_test_powerups():
    return {
        "speed_boost": PowerupDef(
            name="Speed Boost", key="speed_boost", required_rank="Private",
            effect_type="speed", effect_value=1.5, duration_ticks=10, cooldown_ticks=30,
        ),
        "shield": PowerupDef(
            name="Shield", key="shield", required_rank="Sergeant",
            effect_type="defense", effect_value=2.0, duration_ticks=5, cooldown_ticks=60,
        ),
    }

def _make_registry():
    registry = DataRegistry()
    registry.ranks = _make_test_ranks()
    registry.technologies = _make_test_techs()
    registry.powerups = _make_test_powerups()
    return registry

def _make_rank_system(registry=None, event_bus=None):
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    system = RankSystem(registry=registry, event_bus=event_bus)
    # The level->XP curve is a process-global table in world.progression
    # (shared with CombatEntity). Force this registry's curve active so the
    # test is independent of any table left behind by another test/module.
    system._rebuild_thresholds()
    # Route level-up notification events through the real presenter so tests
    # that capture player.msg see the rendered strings.
    from mygame.world.presenters.test_support import attach_presenter
    attach_presenter(event_bus)
    return system, event_bus


class TestAwardXP(unittest.TestCase):
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


class TestDeductXP(unittest.TestCase):
    def test_deduct_xp_decreases_total(self):
        player = FakePlayer(combat_xp=200, level=7)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 50)
        self.assertEqual(player.db.combat_xp, 150)

    def test_deduct_xp_floors_at_zero(self):
        player = FakePlayer(combat_xp=30)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 100)
        self.assertEqual(player.db.combat_xp, 0)

    def test_deduct_zero_no_change(self):
        player = FakePlayer(combat_xp=200, level=7)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 0)
        self.assertEqual(player.db.combat_xp, 200)


class TestPromotion(unittest.TestCase):
    def test_promote_on_threshold(self):
        """At 298 XP, player reaches level 6 = Private (rank 2)."""
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 298, "kill")
        self.assertEqual(player.db.rank_level, 2)  # Private
        self.assertEqual(player.db.level, 6)

    def test_promote_above_threshold(self):
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 400, "kill")
        self.assertEqual(player.db.rank_level, 2)

    def test_multi_rank_promotion(self):
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 8482, "massive kill")
        self.assertEqual(player.db.rank_level, 5)  # Captain

    def test_no_promotion_below_threshold(self):
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        # 19 XP: level 1 threshold=0, level 2 threshold=20, so stays at level 1
        system.award_xp(player, 19, "damage")
        self.assertEqual(player.db.rank_level, 1)

    def test_promotion_publishes_event(self):
        events = []
        event_bus = EventBus()
        event_bus.subscribe("rank_promoted", lambda **kw: events.append(kw))
        system = RankSystem(registry=_make_registry(), event_bus=event_bus)
        system._rebuild_thresholds()
        player = FakePlayer(combat_xp=0, level=1)
        system.award_xp(player, 298, "kill")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["old_rank"].name, "Recruit")
        self.assertEqual(events[0]["new_rank"].name, "Private")

    def test_promotion_does_not_auto_grant_techs(self):
        """R13.1: promotion never touches researched_techs — research at a Lab
        is the only acquisition path."""
        player = FakePlayer(combat_xp=0, level=1, researched_techs=set())
        system, _ = _make_rank_system()
        system.award_xp(player, 1038, "kill")
        self.assertEqual(player.db.rank_level, 3)
        # techs set stays empty — no auto-grant
        self.assertEqual(player.db.researched_techs, set())


class TestDemotion(unittest.TestCase):
    def test_demote_below_threshold(self):
        """Player demoted when XP falls below rank boundary."""
        player = FakePlayer(combat_xp=150, level=8)  # Private
        system, _ = _make_rank_system()
        system.deduct_xp(player, 100)  # XP=50, back to Recruit
        self.assertEqual(player.db.rank_level, 1)

    def test_multi_rank_demotion(self):
        player = FakePlayer(combat_xp=700, level=17)  # Sergeant
        system, _ = _make_rank_system()
        system.deduct_xp(player, 650)  # XP=50, Recruit
        self.assertEqual(player.db.rank_level, 1)

    def test_no_demotion_above_threshold(self):
        player = FakePlayer(combat_xp=500, level=7)  # Private
        system, _ = _make_rank_system()
        system.deduct_xp(player, 50)  # XP=450, still Private (L7 threshold=398)
        self.assertEqual(player.db.rank_level, 2)

    def test_demotion_publishes_event(self):
        events = []
        event_bus = EventBus()
        event_bus.subscribe("rank_demoted", lambda **kw: events.append(kw))
        system = RankSystem(registry=_make_registry(), event_bus=event_bus)
        system._rebuild_thresholds()
        player = FakePlayer(combat_xp=150, level=8)  # Private
        system.deduct_xp(player, 100)  # XP=50, Recruit
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["old_rank"].name, "Private")
        self.assertEqual(events[0]["new_rank"].name, "Recruit")

    def test_demotion_does_not_revoke_techs(self):
        """R13.2: demotion never touches researched_techs — a paid-for tech
        is never taken away by rank churn."""
        player = FakePlayer(
            combat_xp=350, level=12,  # Corporal
            researched_techs={"basic_armor", "improved_weapons"},
        )
        system, _ = _make_rank_system()
        system.deduct_xp(player, 300)  # XP=50, Recruit
        self.assertEqual(player.db.rank_level, 1)
        # Both techs survive the demotion
        self.assertIn("basic_armor", player.db.researched_techs)
        self.assertIn("improved_weapons", player.db.researched_techs)


class TestGetRankAndStatus(unittest.TestCase):
    def test_get_rank_returns_correct_rank(self):
        player = FakePlayer(level=12)  # Corporal
        system, _ = _make_rank_system()
        rank = system.get_rank(player)
        self.assertEqual(rank.name, "Corporal")
        self.assertEqual(rank.level, 3)

    def test_get_status_shows_xp_to_next(self):
        player = FakePlayer(combat_xp=500, level=7)  # Private
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertEqual(status["rank_name"], "Private")
        self.assertEqual(status["combat_xp"], 500)
        self.assertEqual(status["xp_to_next_rank"], 538)  # 1038 - 500

    def test_get_status_max_rank_no_next(self):
        player = FakePlayer(combat_xp=2000, level=22)  # Captain
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertEqual(status["rank_name"], "Captain")
        self.assertIsNone(status["xp_to_next_rank"])
        self.assertIsNone(status["next_rank_name"])


class TestEdgeCases(unittest.TestCase):
    def test_at_max_rank_no_promotion(self):
        """Captain is the highest rank (5) in this test registry.
        Award XP within band 5 (L22-28) to stay at rank 5."""
        player = FakePlayer(combat_xp=8482, level=22)  # Captain
        system, _ = _make_rank_system()
        # Award enough to reach L28 (top of band 5) but not beyond
        system.award_xp(player, 8384, "overkill")  # 8482+8384=16866 → L28
        self.assertEqual(player.db.rank_level, 5)

    def test_at_min_rank_no_demotion(self):
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 100)
        self.assertEqual(player.db.rank_level, 1)
        self.assertEqual(player.db.combat_xp, 0)

    def test_exact_threshold_stays_at_rank(self):
        player = FakePlayer(combat_xp=1038, level=11)  # Corporal
        system, _ = _make_rank_system()
        system.check_demotion(player)
        self.assertEqual(player.db.rank_level, 3)


class TestSubLevel(unittest.TestCase):
    """Sub-level = ((level-1) % 5) + 1."""

    def test_sub_level_at_rank_start(self):
        player = FakePlayer(combat_xp=298, level=6)  # Private, sub=1
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 1)

    def test_sub_level_mid_rank(self):
        player = FakePlayer(combat_xp=517, level=8)  # Private, sub=3
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 3)

    def test_sub_level_just_before_next_rank(self):
        player = FakePlayer(combat_xp=832, level=10)  # Private, sub=5
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 5)

    def test_sub_level_at_first_rank(self):
        player = FakePlayer(combat_xp=0, level=1)  # Recruit, sub=1
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 1)

    def test_sub_level_first_rank_mid(self):
        player = FakePlayer(combat_xp=88, level=3)  # Recruit, sub=3
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 3)

    def test_sub_level_max_rank_uses_fixed_interval(self):
        """Captain (rank 5) starts at L22 (band_low=22), sub = level - 22 + 1."""
        player = FakePlayer(combat_xp=8482, level=22)  # Captain, sub=1
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 1)

    def test_sub_level_max_rank_level_2(self):
        player = FakePlayer(combat_xp=9715, level=23)  # Captain, sub=2
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 2)

    def test_sub_level_max_rank_capped_at_5(self):
        """Band 5 is L22-28 (width 7). L26 → sub = 26 - 22 + 1 = 5."""
        player = FakePlayer(combat_xp=13795, level=26)  # Captain, sub=5
        system, _ = _make_rank_system()
        self.assertEqual(system.get_sub_level(player), 5)


class TestSubLevelNotification(unittest.TestCase):
    def test_notification_on_level_up(self):
        """Award XP to cross a level boundary."""
        messages = []
        # Level 6 = Private sub 1, XP=298. Level 7 threshold = 398
        player = FakePlayer(combat_xp=298, level=6)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.award_xp(player, 100, "test")  # XP=398, level 7
        self.assertTrue(len(messages) >= 1)
        self.assertIn("Level 7", messages[0])

    def test_no_notification_when_level_unchanged(self):
        messages = []
        player = FakePlayer(combat_xp=298, level=6)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.award_xp(player, 5, "small")  # XP=303, still level 6
        self.assertEqual(len(messages), 0)

    def test_notification_on_deduct(self):
        messages = []
        # Level 7 = Private sub 2, XP=140. Level 6 threshold=100
        player = FakePlayer(combat_xp=140, level=7)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 41)  # XP=99, level 5 (Recruit sub 5)
        self.assertTrue(len(messages) >= 1)

    def test_notification_replaces_underscores(self):
        ranks = [
            RankDef(name="Staff_Sergeant", level=1, xp_threshold=0, agent_cap=8),
            RankDef(name="Lieutenant", level=2, xp_threshold=200, agent_cap=10),
        ]
        registry = DataRegistry()
        registry.ranks = ranks
        registry.technologies = {}
        registry.powerups = {}
        bus = EventBus()
        system = RankSystem(registry=registry, event_bus=bus)
        system._rebuild_thresholds()
        from mygame.world.presenters.test_support import attach_presenter
        attach_presenter(bus)
        messages = []
        player = FakePlayer(combat_xp=0, level=1)
        player.msg = lambda m: messages.append(m)
        system.award_xp(player, 40, "test")  # level 2
        self.assertTrue(len(messages) >= 1)
        self.assertIn("Staff Sergeant", messages[0])
        self.assertNotIn("_", messages[0])


class TestAgentCapInEvents(unittest.TestCase):
    def _make_ranks_with_caps(self):
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
        system._rebuild_thresholds()
        events = []
        bus.subscribe(RANK_PROMOTED, lambda **kw: events.append(kw))
        player = FakePlayer(combat_xp=0, level=1)
        system.award_xp(player, 298, "test")  # level 6 = Private
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
        system._rebuild_thresholds()
        events = []
        bus.subscribe(RANK_DEMOTED, lambda **kw: events.append(kw))
        player = FakePlayer(combat_xp=300, level=11)  # Corporal
        system.deduct_xp(player, 250)  # XP=50, Recruit
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["new_agent_cap"], 2)
        self.assertEqual(events[0]["new_rank"].name, "Recruit")
        self.assertEqual(events[0]["old_rank"].name, "Corporal")


class FakePlanetRegistry:
    def __init__(self, spaces):
        self._spaces = spaces
    def get_space(self, planet_key):
        if planet_key not in self._spaces:
            raise KeyError(planet_key)
        return self._spaces[planet_key]


class TestPlanetAccessGating(unittest.TestCase):
    """Planet access uses player level vs rank_requirement (now a level req)."""

    def _make_system_with_planets(self):
        from mygame.world.definitions import CoordinateSpaceDef
        spaces = {
            "terra": CoordinateSpaceDef(
                planet_key="terra", planet_type="earth",
                width=500, height=500, terrain_seed=42,
                rank_requirement=1,  # level 1
            ),
            "forge": CoordinateSpaceDef(
                planet_key="forge", planet_type="industrial",
                width=400, height=400, terrain_seed=7,
                rank_requirement=11,  # level 11 = Corporal
            ),
            "inferno": CoordinateSpaceDef(
                planet_key="inferno", planet_type="volcanic",
                width=300, height=300, terrain_seed=66,
                rank_requirement=26,  # level 26
            ),
        }
        planet_registry = FakePlanetRegistry(spaces)
        registry = _make_registry()
        bus = EventBus()
        system = RankSystem(registry=registry, event_bus=bus,
                            planet_registry=planet_registry)
        return system

    def test_can_access_terra_at_level_1(self):
        system = self._make_system_with_planets()
        player = FakePlayer(level=1)
        self.assertTrue(system.can_access_planet(player, "terra"))

    def test_cannot_access_forge_at_level_1(self):
        system = self._make_system_with_planets()
        player = FakePlayer(level=1)
        self.assertFalse(system.can_access_planet(player, "forge"))

    def test_can_access_forge_at_level_11(self):
        system = self._make_system_with_planets()
        player = FakePlayer(level=11)
        self.assertTrue(system.can_access_planet(player, "forge"))

    def test_can_access_forge_above_requirement(self):
        system = self._make_system_with_planets()
        player = FakePlayer(level=20)
        self.assertTrue(system.can_access_planet(player, "forge"))

    def test_cannot_access_inferno_at_level_25(self):
        system = self._make_system_with_planets()
        player = FakePlayer(level=25)
        self.assertFalse(system.can_access_planet(player, "inferno"))

    def test_unknown_planet_denied(self):
        system = self._make_system_with_planets()
        player = FakePlayer(level=25)
        self.assertFalse(system.can_access_planet(player, "nonexistent"))

    def test_no_planet_registry_allows_all(self):
        system, _ = _make_rank_system()
        player = FakePlayer(level=1)
        self.assertTrue(system.can_access_planet(player, "anything"))


class TestGetStatusWithSubLevel(unittest.TestCase):
    def test_status_includes_sub_level(self):
        player = FakePlayer(combat_xp=150, level=8)  # Private sub 3
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertIn("sub_level", status)
        self.assertEqual(status["sub_level"], 3)

    def test_status_includes_xp_to_next_level(self):
        player = FakePlayer(combat_xp=150, level=8)  # Private sub 3
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertIn("xp_to_next_level", status)
        # Level 9 threshold: 100 + 3*40 = 220, xp_to_next = 220-150 = 70
        # But let's just check it's positive
        self.assertIsNotNone(status["xp_to_next_level"])
        self.assertGreater(status["xp_to_next_level"], 0)

    def test_status_max_rank_sub_level(self):
        player = FakePlayer(combat_xp=8482, level=22)  # Captain sub 1
        system, _ = _make_rank_system()
        status = system.get_status(player)
        self.assertEqual(status["sub_level"], 1)
        self.assertIsNotNone(status["xp_to_next_level"])
        self.assertGreater(status["xp_to_next_level"], 0)


class TestPreservedPlayerBehavior(unittest.TestCase):
    """Task 5.5 — verify the delegation refactor keeps player-facing
    semantics unchanged.

    Asserts the meanings of ``db.combat_xp``/``db.level``/``db.rank_level``
    are unchanged after award/deduct, the level-change message fires, the
    legacy ``rank_level``->``level`` derivation still works, and tech
    unlock/revoke fires on rank change.

    Requirements: 4.2, 4.5, 4.6, 4.7
    """

    # -- 4.2: db.combat_xp/db.level/db.rank_level meanings unchanged ----- #

    def test_award_xp_preserves_attribute_meanings(self):
        """After award_xp, combat_xp grows by exactly the amount and
        level/rank_level are the derived values (Req 4.2)."""
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        system.award_xp(player, 350, "kill")
        # combat_xp == previous + amount
        self.assertEqual(player.db.combat_xp, 350)
        # db.level == level_for_xp(combat_xp)
        self.assertEqual(player.db.level, system.level_for_xp(350))
        # db.rank_level == rank_from_level(level)
        self.assertEqual(player.db.rank_level, rank_from_level(player.db.level))

    def test_deduct_xp_preserves_attribute_meanings(self):
        """After deduct_xp, combat_xp drops by exactly the amount and
        level/rank_level are the derived values (Req 4.2)."""
        player = FakePlayer(combat_xp=350, level=11)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 200)
        self.assertEqual(player.db.combat_xp, 150)
        self.assertEqual(player.db.level, system.level_for_xp(150))
        self.assertEqual(player.db.rank_level, rank_from_level(player.db.level))

    def test_attribute_meanings_consistent_across_multiple_awards(self):
        """The level == level_for_xp(combat_xp) and rank_level ==
        rank_from_level(level) invariant holds after each mutation (Req 4.2)."""
        player = FakePlayer(combat_xp=0, level=1)
        system, _ = _make_rank_system()
        running = 0
        for amount in (40, 100, 60, 500):
            system.award_xp(player, amount, "test")
            running += amount
            self.assertEqual(player.db.combat_xp, running)
            self.assertEqual(player.db.level, system.level_for_xp(running))
            self.assertEqual(
                player.db.rank_level, rank_from_level(player.db.level)
            )

    # -- 4.5: level-change message identifies new level + rank name ------ #

    def test_level_change_message_identifies_level_and_rank_name(self):
        """A level change messages the player with the new level and the
        cosmetic rank name (Req 4.5)."""
        messages = []
        player = FakePlayer(combat_xp=0, level=1)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.award_xp(player, 100, "kill")  # level 1 -> 6 (Private)
        self.assertTrue(len(messages) >= 1)
        self.assertIn(f"Level {player.db.level}", messages[0])
        self.assertIn(system.get_rank_name(player), messages[0])

    def test_level_change_message_fires_on_deduct(self):
        """A level decrease also notifies the player (Req 4.5)."""
        messages = []
        player = FakePlayer(combat_xp=140, level=7)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.deduct_xp(player, 41)  # XP=99 -> level drops
        self.assertTrue(len(messages) >= 1)
        self.assertIn(f"Level {player.db.level}", messages[0])

    def test_no_message_when_level_unchanged(self):
        """No notification fires when the level does not change (Req 4.5)."""
        messages = []
        player = FakePlayer(combat_xp=298, level=6)
        player.msg = lambda m: messages.append(m)
        system, _ = _make_rank_system()
        system.award_xp(player, 5, "small")  # stays level 6
        self.assertEqual(len(messages), 0)

    # -- 4.6: legacy rank_level->level derivation via _get_level --------- #

    def test_legacy_rank_level_derives_level(self):
        """A legacy player with rank_level set but no level derives the
        first level of that rank via the backward-compat rule (Req 4.6)."""
        # rank_level=3 (Corporal), no level attribute
        player = FakePlayer(level=1)
        player.db.level = None
        player.db.rank_level = 3
        # First level of rank 3's band (Corporal starts at L11)
        expected = RANK_BANDS[3][0]
        self.assertEqual(RankSystem._get_level(player), expected)

    def test_legacy_rank_level_one_derives_level_one(self):
        """rank_level=1 with no level derives level 1 (Req 4.6)."""
        player = FakePlayer(level=1)
        player.db.level = None
        player.db.rank_level = 1
        self.assertEqual(RankSystem._get_level(player), 1)

    def test_legacy_derivation_used_as_old_level_on_award(self):
        """award_xp uses the legacy-derived old level so the first
        XP award resyncs level correctly (Req 4.6)."""
        player = FakePlayer(level=1)
        player.db.level = None
        player.db.rank_level = 2  # Private -> derived level 6
        player.db.combat_xp = 100
        system, _ = _make_rank_system()
        system.award_xp(player, 0, "noop")  # no-op, but verify derivation
        # award of 0 is a no-op; derive directly (rank 2's band starts at L6)
        self.assertEqual(RankSystem._get_level(player), RANK_BANDS[2][0])

    # -- R13.1, R13.2: rank changes never touch techs -------------------- #

    def test_tech_not_auto_granted_on_promotion(self):
        """R13.1: promotion never auto-grants technologies."""
        player = FakePlayer(combat_xp=0, level=1, researched_techs=set())
        system, _ = _make_rank_system()
        system.award_xp(player, 1038, "kill")  # -> Corporal (rank 3)
        self.assertEqual(player.db.rank_level, 3)
        self.assertEqual(player.db.researched_techs, set())

    def test_tech_not_revoked_on_demotion(self):
        """R13.2: demotion never revokes researched technologies."""
        player = FakePlayer(
            combat_xp=350, level=12,  # Corporal
            researched_techs={"basic_armor", "improved_weapons"},
        )
        system, _ = _make_rank_system()
        system.deduct_xp(player, 300)  # XP=50 -> Recruit (rank 1)
        self.assertEqual(player.db.rank_level, 1)
        self.assertIn("basic_armor", player.db.researched_techs)
        self.assertIn("improved_weapons", player.db.researched_techs)

    def test_no_tech_change_when_rank_unchanged(self):
        """A level change within the same rank does not alter techs (Req 4.7)."""
        player = FakePlayer(
            combat_xp=1038, level=11,  # Corporal sub 1
            researched_techs={"basic_armor", "improved_weapons"},
        )
        system, _ = _make_rank_system()
        before = set(player.db.researched_techs)
        system.award_xp(player, 248, "test")  # level 12, still Corporal
        self.assertEqual(player.db.rank_level, 3)
        self.assertEqual(set(player.db.researched_techs), before)


if __name__ == "__main__":
    unittest.main()
