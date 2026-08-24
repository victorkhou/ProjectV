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

from mygame.world.constants import RESEARCH_LAB  # noqa: E402


class FakeLab:
    """A research-lab building owned by a FakePlayer.

    Hosts one tech tree. ``building_type`` is the lab's abbreviation; the
    registry resolves its ``research_tree`` from that (research-lab-trees gate).
    A convenience ``_tree_capable`` set makes ``building_has_capability`` report
    the RESEARCH_LAB flag without a registry (the tech system passes the
    registry, but the fake registry in this module may lack building defs).
    """
    def __init__(self, building_type, tree, planet=None, owner=None):
        self.key = building_type
        self.db = types.SimpleNamespace(
            building_type=building_type, coord_planet=planet,
            under_construction=False, owner=owner,
        )
        self.owner = owner
        self.research_tree = tree
        self.capabilities = frozenset({RESEARCH_LAB})

    def has_capability(self, cap):
        return cap in self.capabilities


class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self):
        self.rank_level = 5
        self.researched_techs = set()
        self.hp = 100
        self.hp_max = 100
        self.coord_planet = "terra"

class FakePlayer:
    """Lightweight stand-in for CombatCharacter.

    ``research_tree`` (default ``"research"``) is the tree of the research lab
    this player owns; ``get_buildings`` returns that lab so the tech system's
    ownership gate passes. Pass ``research_tree=None`` to model a player with no
    research lab.
    """
    def __init__(self, name="TestPlayer", rank_level=5, resources=None,
                 research_tree="research"):
        self.key = name
        self.db = FakeDB()
        self.db.rank_level = rank_level
        self._research_tree = research_tree
        self._resources = {
            "Straw": 0, "Clay": 0, "Wood": 0, "Stone": 0, "Iron": 0,
            "Energy": 0, "Metals": 0, "Circuits": 0,
        }
        if resources:
            self._resources.update(resources)

    def get_buildings(self):
        if self._research_tree is None:
            return []
        abbr = _LAB_ABBR_BY_TREE.get(self._research_tree, "LB")
        return [FakeLab(abbr, self._research_tree,
                        planet=self.db.coord_planet, owner=self)]

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

#: Which lab abbreviation hosts each tree, for the FakeLab the FakePlayer owns.
_LAB_ABBR_BY_TREE = {
    "weapons": "WX", "defense": "DF", "resource": "RX", "research": "LB",
}


def _lab_building_defs():
    """The four research-lab BuildingDefs, so the registry can resolve a
    FakeLab's abbreviation to its research_tree (the tech-gate lookup)."""
    from mygame.world.definitions import BuildingDef
    defs = {}
    for tree, abbr in _LAB_ABBR_BY_TREE.items():
        defs[abbr] = BuildingDef(
            name=f"{tree.title()} Lab", abbreviation=abbr, cost={},
            max_health=250, requires_hq=True, required_terrain=None,
            category="research", produces=None,
            capabilities=frozenset({RESEARCH_LAB}), research_tree=tree,
        )
    return defs


def _make_registry():
    """Create a DataRegistry with test definitions."""
    registry = DataRegistry()
    registry.ranks = list(SAMPLE_RANKS)
    registry.technologies = dict(SAMPLE_TECHS)
    registry.balance = BalanceConfig()
    # Research-lab defs so the tech-gate can resolve a FakeLab abbr -> tree.
    registry.buildings = _lab_building_defs()
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


class TestTechBonusRecompute(unittest.TestCase):
    """R13.5 grandfathering: rebuild db.tech_bonuses from researched_techs."""

    def test_recompute_rebuilds_bonuses_from_scratch(self):
        """A pre-rebalance player (techs known, no tech_bonuses) gains effects."""
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)
        # Simulate the old auto-grant: techs in the set, but tech_bonuses never
        # written (the old apply-path mutated stats in place).
        player.db.researched_techs = {"reinforced_walls", "basic_armor"}
        player.db.tech_bonuses = {}

        system.recompute_tech_bonuses(player)

        # building_hp (50, from reinforced_walls) + damage_reduction (20, from
        # basic_armor) both materialize.
        self.assertEqual(player.db.tech_bonuses.get("building_hp"), 50)
        self.assertEqual(player.db.tech_bonuses.get("damage_reduction"), 20)

    def test_recompute_skips_unknown_tech_keys(self):
        """A stale/removed tech key in the set is ignored, not a crash."""
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)
        player.db.researched_techs = {"reinforced_walls", "deleted_tech"}
        player.db.tech_bonuses = {}

        system.recompute_tech_bonuses(player)  # must not raise

        self.assertEqual(player.db.tech_bonuses.get("building_hp"), 50)

    def test_recompute_is_idempotent(self):
        """Recomputing twice yields the same bonuses (no accumulation)."""
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)
        player.db.researched_techs = {"reinforced_walls"}
        player.db.tech_bonuses = {}

        system.recompute_tech_bonuses(player)
        first = dict(player.db.tech_bonuses)
        system.recompute_tech_bonuses(player)

        self.assertEqual(dict(player.db.tech_bonuses), first)


class TestTechAdminGrantRevoke(unittest.TestCase):
    """The admin single-writer paths behind the ``@tech`` adapter
    (unified-admin-crud R7.7, R7.8, R7.9). Grant/revoke mutate the
    researched set through the research path and recompute the derived
    ``db.tech_bonuses`` BEFORE returning; grant-state violations return
    ``(False, error)`` and change nothing."""

    def test_grant_adds_tech_and_recomputes_bonuses(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)

        ok, error = system.admin_grant_technology(player, "basic_armor")

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertIn("basic_armor", player.db.researched_techs)
        # Derived bonus present the instant the call returns (R7.7).
        self.assertEqual(player.db.tech_bonuses.get("damage_reduction"), 20)

    def test_grant_publishes_the_research_event(self):
        system, bus = _make_system()
        events = []
        bus.subscribe(TECHNOLOGY_RESEARCHED, lambda **kw: events.append(kw))
        player = FakePlayer(rank_level=5)

        system.admin_grant_technology(player, "basic_armor")

        # Mirrors research-completion: subscribers see the grant (R7.7).
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["technology"].key, "basic_armor")

    def test_grant_unknown_tech_errors_and_changes_nothing(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)

        ok, error = system.admin_grant_technology(player, "no_such_tech")

        self.assertFalse(ok)
        self.assertIn("Unknown technology", error)
        self.assertEqual(player.db.researched_techs, set())

    def test_double_grant_states_current_state_and_changes_nothing(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)
        system.admin_grant_technology(player, "basic_armor")
        bonuses_before = dict(player.db.tech_bonuses)

        ok, error = system.admin_grant_technology(player, "basic_armor")

        self.assertFalse(ok)  # R7.9: already-granted grant is rejected
        self.assertIn("already holds", error)
        self.assertIn("granted", error)
        self.assertEqual(player.db.researched_techs, {"basic_armor"})
        self.assertEqual(player.db.tech_bonuses, bonuses_before)

    def test_revoke_removes_tech_and_recomputes_bonuses(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)
        system.admin_grant_technology(player, "basic_armor")

        ok, error = system.admin_revoke_technology(player, "basic_armor")

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertNotIn("basic_armor", player.db.researched_techs)
        # The derived bonus is gone after the recompute (R7.8).
        self.assertNotIn("damage_reduction", player.db.tech_bonuses)

    def test_revoke_leaves_other_grants_bonuses_intact(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)
        system.admin_grant_technology(player, "basic_armor")
        system.admin_grant_technology(player, "reinforced_walls")

        system.admin_revoke_technology(player, "basic_armor")

        # Revoking one tech recomputes from the surviving set (R7.8).
        self.assertEqual(player.db.researched_techs, {"reinforced_walls"})
        self.assertNotIn("damage_reduction", player.db.tech_bonuses)
        self.assertEqual(player.db.tech_bonuses.get("building_hp"), 50)

    def test_absent_revoke_states_current_state_and_changes_nothing(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)

        ok, error = system.admin_revoke_technology(player, "basic_armor")

        self.assertFalse(ok)  # R7.9: not-held revoke is rejected
        self.assertIn("does not hold", error)
        self.assertIn("not granted", error)
        self.assertEqual(player.db.researched_techs, set())

    def test_grant_then_revoke_round_trips_to_the_prior_state(self):
        system, _ = _make_system()
        player = FakePlayer(rank_level=5)

        system.admin_grant_technology(player, "basic_armor")
        system.admin_revoke_technology(player, "basic_armor")

        self.assertEqual(player.db.researched_techs, set())
        self.assertEqual(player.db.tech_bonuses, {})


class TestReactivePlatingResearch(unittest.TestCase):
    """Reactive Plating (item-loot-economy R11.3) — data-only DR tech.

    Runs the full chain against the REAL data files (technologies.yaml +
    balance.yaml): research completes → ``db.tech_bonuses`` gains
    ``damage_reduction`` → the existing combat-engine armor path applies it
    and clamps the tech stack at ``perm_bonus_cap_dr``.
    """

    @classmethod
    def setUpClass(cls):
        import os
        data_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "data",
        ))
        cls.registry = DataRegistry()
        cls.registry.load_all(data_dir)

    def _make_researcher(self):
        """A max-level player with ample resources (passes every rank gate)."""
        player = FakePlayer(resources={
            "Iron": 1000, "Circuits": 1000, "Energy": 1000, "Stone": 1000,
        })
        player.db.level = 100
        player.db.tech_bonuses = {}
        # Mark as a combat entity so the combat engine treats it as a player.
        player.db.combat_xp = 0
        return player

    def _research(self, system, player, tech_key):
        """Start *tech_key* and tick it to completion.

        Gives the player the research lab that hosts this tech's tree, so the
        ownership gate passes — these tests exercise EFFECT application, not the
        gate (which has its own tests).
        """
        player._research_tree = self.registry.technologies[tech_key].tree
        ok, msg = system.start_research(player, tech_key)
        self.assertTrue(ok, f"start_research({tech_key}) failed: {msg}")
        ticks = self.registry.technologies[tech_key].research_ticks
        for _ in range(ticks):
            system.process_tick()
        self.assertIn(tech_key, player.db.researched_techs)

    def test_reactive_plating_defined_in_real_data(self):
        """The tech loads from technologies.yaml with a DR effect payload."""
        tdef = self.registry.technologies.get("reactive_plating")
        self.assertIsNotNone(tdef, "reactive_plating missing from technologies.yaml")
        self.assertEqual(tdef.effect_type, "stat_bonus")
        self.assertEqual(tdef.effect_value, {"damage_reduction": 3})

    def test_research_writes_damage_reduction_into_tech_bonuses(self):
        """Research completion accumulates DR into db.tech_bonuses (R13.3)."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()

        self._research(system, player, "reactive_plating")

        self.assertEqual(player.db.tech_bonuses.get("damage_reduction"), 3.0)

    def test_combat_applies_reactive_plating_dr(self):
        """The existing combat armor path reads the researched DR."""
        from mygame.world.systems.combat_engine import CombatEngine

        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "reactive_plating")

        engine = CombatEngine(self.registry, EventBus(),
                              current_tick_func=lambda: 0)
        dr = engine._get_target_armor_reduction(player)
        # Gear=0, alliance=0, tech=3 → 3.0 (under the cap of 6)
        self.assertEqual(dr, 3.0)

    def test_dr_stack_clamped_by_perm_bonus_cap(self):
        """Improved Armor (5) + Reactive Plating (3) = 8 → capped at 6."""
        from mygame.world.systems.combat_engine import CombatEngine

        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "improved_armor")
        self._research(system, player, "reactive_plating")

        # Both techs accumulate uncapped in tech_bonuses …
        self.assertEqual(player.db.tech_bonuses.get("damage_reduction"), 8.0)

        # … but the combat armor path clamps at perm_bonus_cap_dr (6).
        self.assertEqual(self.registry.balance.perm_bonus_cap_dr, 6.0)
        engine = CombatEngine(self.registry, EventBus(),
                              current_tick_func=lambda: 0)
        dr = engine._get_target_armor_reduction(player)
        self.assertEqual(dr, 6.0)


class TestSalvageProtocolsResearch(unittest.TestCase):
    """Salvage Protocols (item-loot-economy R11.2, task 5.4) — economy tech.

    Runs the full chain against the REAL data files: the tech loads from
    technologies.yaml → research completes → ``db.tech_bonuses`` gains
    ``salvage_cost_mult`` → the EquipmentSystem reroll-cost consumer reads
    and clamps it (R11.7: no dead tech key).

    Validates: Requirements 11.2, 11.7
    """

    @classmethod
    def setUpClass(cls):
        import os
        data_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "data",
        ))
        cls.registry = DataRegistry()
        cls.registry.load_all(data_dir)

    def _make_researcher(self):
        """A max-level player with ample resources (passes every rank gate)."""
        player = FakePlayer(resources={
            "Iron": 1000, "Circuits": 1000, "Energy": 1000, "Stone": 1000,
        })
        player.db.level = 100
        player.db.tech_bonuses = {}
        return player

    def _research(self, system, player, tech_key):
        """Start *tech_key* and tick it to completion.

        Gives the player the research lab that hosts this tech's tree, so the
        ownership gate passes — these tests exercise EFFECT application, not the
        gate (which has its own tests).
        """
        player._research_tree = self.registry.technologies[tech_key].tree
        ok, msg = system.start_research(player, tech_key)
        self.assertTrue(ok, f"start_research({tech_key}) failed: {msg}")
        ticks = self.registry.technologies[tech_key].research_ticks
        for _ in range(ticks):
            system.process_tick()
        self.assertIn(tech_key, player.db.researched_techs)

    def test_salvage_protocols_defined_in_real_data(self):
        """The tech loads from technologies.yaml with the cost-mult payload."""
        tdef = self.registry.technologies.get("salvage_protocols")
        self.assertIsNotNone(
            tdef, "salvage_protocols missing from technologies.yaml")
        self.assertEqual(tdef.effect_type, "stat_bonus")
        self.assertEqual(tdef.effect_value, {"salvage_cost_mult": 0.75})

    def test_research_writes_salvage_cost_mult_into_tech_bonuses(self):
        """Research completion accumulates the multiplier (R13.3 path)."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()

        self._research(system, player, "salvage_protocols")

        self.assertEqual(
            player.db.tech_bonuses.get("salvage_cost_mult"), 0.75)

    def test_equipment_consumer_reads_researched_multiplier(self):
        """The reroll-cost consumer reads the key (R11.7 — no dead key)."""
        from mygame.world.systems.equipment_system import EquipmentSystem

        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "salvage_protocols")

        equipment = EquipmentSystem(self.registry, EventBus())
        self.assertEqual(equipment._salvage_cost_multiplier(player), 0.75)
        # An unresearched player reads the identity multiplier (default 1.0
        # — NOT 0.0, the "free rerolls" wiring landmine).
        self.assertEqual(
            equipment._salvage_cost_multiplier(self._make_researcher()), 1.0)


class _RealDataTechTest(unittest.TestCase):
    """Shared harness for the Phase-6 research tests (task 6.4, R11).

    Loads the REAL data files once per class and provides the researcher/
    research helpers the Reactive Plating / Salvage Protocols classes use,
    so every new tech is validated end-to-end: loads from technologies.yaml
    → research completes → ``db.tech_bonuses`` gains the key → the live
    consumer reads it (R11.7 — no dead tech keys).
    """

    @classmethod
    def setUpClass(cls):
        import os
        data_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "data",
        ))
        cls.registry = DataRegistry()
        cls.registry.load_all(data_dir)

    def _make_researcher(self):
        """A max-level player with ample resources (passes every rank gate)."""
        player = FakePlayer(resources={
            "Iron": 1000, "Circuits": 1000, "Energy": 1000, "Stone": 1000,
        })
        player.db.level = 100
        player.db.tech_bonuses = {}
        # Mark as a combat entity so the combat engine treats it as a player.
        player.db.combat_xp = 0
        return player

    def _research(self, system, player, tech_key):
        """Start *tech_key* and tick it to completion.

        Gives the player the research lab that hosts this tech's tree, so the
        ownership gate passes — these tests exercise EFFECT application, not the
        gate (which has its own tests).
        """
        player._research_tree = self.registry.technologies[tech_key].tree
        ok, msg = system.start_research(player, tech_key)
        self.assertTrue(ok, f"start_research({tech_key}) failed: {msg}")
        ticks = self.registry.technologies[tech_key].research_ticks
        for _ in range(ticks):
            system.process_tick()
        self.assertIn(tech_key, player.db.researched_techs)


class _FakeRangedWeapon:
    """Minimal weapon double the combat engine's stat readers accept."""

    def __init__(self, weapon_range=5):
        self.key = "test_rifle"
        self.slot = "weapon_ranged"
        self.category = "weapon"
        self.weapon_type = "ranged"
        self.stat_modifiers = {"damage": 20, "range": weapon_range}


class TestBallisticsOptimizationResearch(_RealDataTechTest):
    """Ballistics Optimization (item-loot-economy R11.4, task 6.4).

    End-to-end against the real data: research → ``tech_bonuses`` gains
    ``weapon_range`` → the R8 range hook (`_resolve_weapon_range`) extends
    combat range by +1, still clamped by ``balance.max_weapon_range``.

    Validates: Requirements 11.4, 11.7
    """

    def _engine(self):
        from mygame.world.systems.combat_engine import CombatEngine
        return CombatEngine(self.registry, EventBus(),
                            current_tick_func=lambda: 0)

    def test_ballistics_optimization_defined_in_real_data(self):
        tdef = self.registry.technologies.get("ballistics_optimization")
        self.assertIsNotNone(
            tdef, "ballistics_optimization missing from technologies.yaml")
        self.assertEqual(tdef.effect_type, "stat_bonus")
        self.assertEqual(tdef.effect_value, {"weapon_range": 1})

    def test_research_writes_weapon_range_into_tech_bonuses(self):
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()

        self._research(system, player, "ballistics_optimization")

        self.assertEqual(player.db.tech_bonuses.get("weapon_range"), 1.0)

    def test_range_consumer_extends_combat_range(self):
        """The R8 hook reads the key (R11.7 — live consumer, no dead key)."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        engine = self._engine()
        weapon = _FakeRangedWeapon(weapon_range=5)

        # Unresearched: base range only (no tile, no tech).
        self.assertEqual(engine._resolve_weapon_range(player, weapon), 5)

        self._research(system, player, "ballistics_optimization")
        self.assertEqual(engine._resolve_weapon_range(player, weapon), 6)

    def test_max_weapon_range_cap_respected(self):
        """The tech can never push range past balance.max_weapon_range."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "ballistics_optimization")

        cap = int(self.registry.balance.max_weapon_range)
        self.assertGreater(cap, 0)
        engine = self._engine()
        weapon = _FakeRangedWeapon(weapon_range=cap)  # already at the cap
        self.assertEqual(engine._resolve_weapon_range(player, weapon), cap)

    def test_melee_stays_range_one(self):
        """Researched range never leaks onto melee (R8: melee is always 1)."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "ballistics_optimization")

        weapon = _FakeRangedWeapon(weapon_range=1)
        weapon.weapon_type = "melee"
        self.assertEqual(
            self._engine()._resolve_weapon_range(player, weapon), 1)


class TestToxicologyResearch(_RealDataTechTest):
    """Toxicology (item-loot-economy R11.5, task 6.4) — poison DoT boost.

    End-to-end against the real data: research → ``tech_bonuses`` gains
    ``poison_dot_mult`` → ``_apply_poison_dot`` scales the per-tick amount,
    clamped to ``[1.0, POISON_DOT_MULT_CAP]``. The R9 counters survive: the
    boost scales the fraction only — the DoT's duration is unchanged (regen/
    medkits still out-heal it tick-for-tick) and ``poison_resist`` still
    mitigates the typed hit itself upstream.

    Validates: Requirements 11.5, 11.7
    """

    RAW = 100

    def _engine(self):
        from mygame.world.systems.combat_engine import CombatEngine
        return CombatEngine(self.registry, EventBus(),
                            current_tick_func=lambda: 0)

    def _dot_effect(self, attacker):
        """Apply a poison DoT from *attacker* and return the effect dict."""
        target = FakePlayer(name="Victim")
        self._engine()._apply_poison_dot(target, self.RAW, attacker)
        effects = target.db.active_effects
        self.assertEqual(len(effects), 1)
        return effects[0]

    def test_toxicology_defined_in_real_data(self):
        tdef = self.registry.technologies.get("toxicology")
        self.assertIsNotNone(
            tdef, "toxicology missing from technologies.yaml")
        self.assertEqual(tdef.effect_type, "stat_bonus")
        self.assertEqual(tdef.effect_value, {"poison_dot_mult": 1.25})

    def test_research_writes_poison_dot_mult_into_tech_bonuses(self):
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()

        self._research(system, player, "toxicology")

        self.assertEqual(player.db.tech_bonuses.get("poison_dot_mult"), 1.25)

    def test_poison_dot_consumer_boosts_per_tick_damage(self):
        """_apply_poison_dot reads the key (R11.7 — live consumer)."""
        system = TechLabSystem(self.registry, EventBus())
        fraction = float(self.registry.balance.poison_dot_fraction)

        base = self._dot_effect(self._make_researcher())["damage"]
        self.assertEqual(base, max(1, int(round(self.RAW * fraction))))

        researcher = self._make_researcher()
        self._research(system, researcher, "toxicology")
        boosted = self._dot_effect(researcher)["damage"]
        self.assertEqual(
            boosted, max(1, int(round(self.RAW * fraction * 1.25))))
        self.assertGreater(boosted, base)

    def test_poison_dot_mult_clamped_at_cap(self):
        """Additive tech stacking can never run past POISON_DOT_MULT_CAP."""
        from mygame.world.systems.combat_engine import POISON_DOT_MULT_CAP

        fraction = float(self.registry.balance.poison_dot_fraction)
        attacker = self._make_researcher()
        attacker.db.tech_bonuses = {"poison_dot_mult": 99.0}
        effect = self._dot_effect(attacker)
        self.assertEqual(
            effect["damage"],
            max(1, int(round(self.RAW * fraction * POISON_DOT_MULT_CAP))))

    def test_poison_dot_mult_never_below_identity(self):
        """A sub-1.0 multiplier reads as 1.0 — the tech key can never
        WEAKEN the base DoT (clamped to [1.0, POISON_DOT_MULT_CAP])."""
        fraction = float(self.registry.balance.poison_dot_fraction)
        under = self._make_researcher()
        under.db.tech_bonuses = {"poison_dot_mult": 0.5}
        self.assertEqual(
            self._dot_effect(under)["damage"],
            max(1, int(round(self.RAW * fraction))))

    def test_boost_does_not_extend_dot_duration(self):
        """The R9.4 counter survives: the boosted DoT ticks the standard
        poison_dot_ticks — regen/medkits out-heal it exactly as before."""
        system = TechLabSystem(self.registry, EventBus())
        researcher = self._make_researcher()
        self._research(system, researcher, "toxicology")

        effect = self._dot_effect(researcher)
        self.assertEqual(effect["ticks_remaining"],
                         int(self.registry.balance.poison_dot_ticks))
        self.assertEqual(effect["type"], "poison")


class TestEfficientConstructionResearch(_RealDataTechTest):
    """Efficient Construction (item-loot-economy R11.1, task 6.4).

    End-to-end against the real data: research → ``tech_bonuses`` gains
    ``build_cost_mult`` → the task-3.3 consumer
    (``building_system._build_cost_multiplier``) reads it, reduces real
    build costs, and clamps at ``balance.build_cost_mult_floor``.

    Validates: Requirements 11.1, 11.7
    """

    def _building_system(self):
        from mygame.world.systems.building_system import BuildingSystem
        return BuildingSystem(self.registry, EventBus())

    def test_efficient_construction_defined_in_real_data(self):
        tdef = self.registry.technologies.get("efficient_construction")
        self.assertIsNotNone(
            tdef, "efficient_construction missing from technologies.yaml")
        self.assertEqual(tdef.effect_type, "stat_bonus")
        self.assertEqual(tdef.effect_value, {"build_cost_mult": 0.85})

    def test_research_writes_build_cost_mult_into_tech_bonuses(self):
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()

        self._research(system, player, "efficient_construction")

        self.assertEqual(player.db.tech_bonuses.get("build_cost_mult"), 0.85)

    def test_build_cost_consumer_reduces_real_costs(self):
        """The task-3.3 consumer reads the key (R11.7 — live consumer)."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "efficient_construction")

        buildings = self._building_system()
        self.assertEqual(buildings._build_cost_multiplier(player), 0.85)
        # An unresearched player pays full price (identity default 1.0).
        self.assertEqual(
            buildings._build_cost_multiplier(self._make_researcher()), 1.0)

        # Real building def, real cost math: every resource is ×0.85.
        hq = self.registry.get_building("HQ")
        expected = {r: round(c * 0.85) for r, c in hq.cost.items()}
        self.assertEqual(buildings.get_build_cost(hq, player), expected)

    def test_upgrade_cost_discounted_too(self):
        """get_upgrade_cost reads the same multiplier (R11.1 — both cost
        paths), and the tech never touches production_multiplier (the
        design §6.3 guardrail)."""
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        buildings = self._building_system()
        hq = self.registry.get_building("HQ")

        base_upgrade = buildings.get_upgrade_cost(
            hq, 2, self._make_researcher())
        self._research(system, player, "efficient_construction")

        discounted_upgrade = buildings.get_upgrade_cost(hq, 2, player)
        for res, amt in discounted_upgrade.items():
            self.assertLessEqual(amt, base_upgrade[res])
        self.assertNotEqual(discounted_upgrade, base_upgrade)
        self.assertNotIn("production_multiplier", player.db.tech_bonuses)

    def test_build_cost_floor_respected(self):
        """Stacked cost research clamps at balance.build_cost_mult_floor."""
        player = self._make_researcher()
        player.db.tech_bonuses = {"build_cost_mult": 0.1}
        floor = float(getattr(self.registry.balance,
                              "build_cost_mult_floor", 0.6))
        self.assertEqual(
            self._building_system()._build_cost_multiplier(player), floor)
        # Additive stacking above 1.0 can never RAISE costs either.
        player.db.tech_bonuses = {"build_cost_mult": 1.7}
        self.assertEqual(
            self._building_system()._build_cost_multiplier(player), 1.0)


class TestMasterGunsmithingResearch(_RealDataTechTest):
    """Master Gunsmithing (item-loot-economy R11.6, task 6.4).

    End-to-end against the real data: research → ``tech_bonuses`` gains
    ``craft_iqs_floor`` → the craft path (``equipment_system``) hands it to
    the loot roller as the crafted-roll U-clamp, raising the low end of
    every crafted roll while the roll stays INSIDE the craft band (R6.1).

    Validates: Requirements 11.6, 11.7
    """

    SEED = 1234

    def _equipment(self, seed=None):
        import random as _random
        from mygame.world.systems.equipment_system import EquipmentSystem
        equipment = EquipmentSystem(self.registry, EventBus())
        if seed is not None:
            equipment._rng = _random.Random(seed)
        return equipment

    def test_master_gunsmithing_defined_in_real_data(self):
        tdef = self.registry.technologies.get("master_gunsmithing")
        self.assertIsNotNone(
            tdef, "master_gunsmithing missing from technologies.yaml")
        self.assertEqual(tdef.effect_type, "stat_bonus")
        self.assertEqual(tdef.effect_value, {"craft_iqs_floor": 0.25})

    def test_research_writes_craft_iqs_floor_into_tech_bonuses(self):
        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()

        self._research(system, player, "master_gunsmithing")

        self.assertEqual(player.db.tech_bonuses.get("craft_iqs_floor"), 0.25)

    def test_craft_floor_consumer_reads_clamped_value(self):
        """The equipment consumer reads the key (R11.7 — live consumer)."""
        from mygame.world.systems.equipment_system import CRAFT_IQS_FLOOR_CAP

        system = TechLabSystem(self.registry, EventBus())
        player = self._make_researcher()
        self._research(system, player, "master_gunsmithing")

        equipment = self._equipment()
        self.assertEqual(equipment._craft_iqs_floor(player), 0.25)
        # Unresearched → no floor (0.0, exactly today's crafted roll).
        self.assertEqual(
            equipment._craft_iqs_floor(self._make_researcher()), 0.0)
        # Additive tech stacking / garbage clamps into [0, cap].
        stacked = self._make_researcher()
        stacked.db.tech_bonuses = {"craft_iqs_floor": 99.0}
        self.assertEqual(equipment._craft_iqs_floor(stacked),
                         CRAFT_IQS_FLOOR_CAP)
        negative = self._make_researcher()
        negative.db.tech_bonuses = {"craft_iqs_floor": -1.0}
        self.assertEqual(equipment._craft_iqs_floor(negative), 0.0)

    def test_crafted_rolls_floor_raised_within_craft_band(self):
        """Researched crafts roll at/above the floored minimum and NEVER
        escape the craft band (the floor lifts the low end, not the top)."""
        system = TechLabSystem(self.registry, EventBus())
        researcher = self._make_researcher()
        self._research(system, researcher, "master_gunsmithing")

        item_def = self.registry.get_item("assault_rifle")
        band = item_def.roll_spec["craft"]["damage"]  # {min: 20, max: 25}
        lo, hi = float(band["min"]), float(band["max"])
        skew = float(self.registry.balance.loot_roll_skew)
        floored_min = lo + (hi - lo) * 0.25 ** skew

        equipment = self._equipment(seed=self.SEED)
        rolls = []
        for _ in range(200):
            item = {}
            equipment._roll_spawned_gear(item, item_def, crafted=True,
                                         owner=researcher)
            rolls.append(item["rolled_stats"]["damage"])
            # Crafted contract intact (R6.1): no rarity, no affixes.
            self.assertNotIn("rarity", item)
            self.assertNotIn("affixes", item)

        for value in rolls:
            self.assertGreaterEqual(value, floored_min - 1e-9)
            self.assertLessEqual(value, hi)

        # The unresearched crafter's rolls (same seed → same U sequence)
        # dip below the researched floor: the tech visibly matters.
        baseline_equipment = self._equipment(seed=self.SEED)
        baseline = []
        for _ in range(200):
            item = {}
            baseline_equipment._roll_spawned_gear(
                item, item_def, crafted=True,
                owner=self._make_researcher())
            baseline.append(item["rolled_stats"]["damage"])
        self.assertLess(min(baseline), floored_min)
        # Pointwise: the floored U-clamp can only raise a roll, never
        # lower it (same U sequence under the shared seed).
        for floored, plain in zip(rolls, baseline):
            self.assertGreaterEqual(floored, plain - 1e-9)


# -------------------------------------------------------------- #
#  Research-lab-tree gating (research-lab-trees feature)
# -------------------------------------------------------------- #

#: Techs across three trees for the gating tests, all at Recruit rank so the
#: rank gate never interferes (the tree gate runs before rank, but keeping
#: rank open isolates the behaviour under test).
_TREE_TECHS = {
    "wtech": TechnologyDef(
        name="Weapon Tech", key="wtech", tree="weapons",
        required_rank="Recruit", resource_cost={}, research_ticks=2,
        effect_type="stat_bonus", effect_value={"damage": 10},
    ),
    "dtech": TechnologyDef(
        name="Defense Tech", key="dtech", tree="defense",
        required_rank="Recruit", resource_cost={}, research_ticks=2,
        effect_type="stat_bonus", effect_value={"building_hp": 50},
    ),
    "rtech": TechnologyDef(
        name="Research Tech", key="rtech", tree="research",
        required_rank="Recruit", resource_cost={}, research_ticks=2,
        effect_type="stat_bonus", effect_value={"sight_range": 2},
    ),
}


def _tree_registry():
    registry = _make_registry()
    registry.technologies = dict(_TREE_TECHS)
    return registry


class TestResearchLabTreeGate(unittest.TestCase):
    """start_research / list_available honour the OWNED lab's tree.

    A player owns at most one research lab per planet; its tree decides which
    techs they may research. Validates the research-lab-trees ownership gate.
    """

    def setUp(self):
        self.registry = _tree_registry()
        self.system = TechLabSystem(self.registry, EventBus())

    def test_owned_research_tree_reads_the_owned_lab(self):
        for tree in ("weapons", "defense", "resource", "research"):
            player = FakePlayer(research_tree=tree)
            self.assertEqual(self.system.owned_research_tree(player), tree)

    def test_no_lab_reports_no_owned_tree(self):
        player = FakePlayer(research_tree=None)
        self.assertIsNone(self.system.owned_research_tree(player))

    def test_matching_tree_research_starts(self):
        player = FakePlayer(research_tree="weapons")
        ok, msg = self.system.start_research(player, "wtech")
        self.assertTrue(ok, msg)

    def test_wrong_tree_research_rejected(self):
        """A weapons lab cannot research a defense tech, and the message names
        both the tech's tree and the owned tree."""
        player = FakePlayer(research_tree="weapons")
        ok, msg = self.system.start_research(player, "dtech")
        self.assertFalse(ok)
        self.assertIn("defense", msg)
        self.assertIn("weapons", msg)

    def test_no_lab_research_rejected(self):
        player = FakePlayer(research_tree=None)
        ok, msg = self.system.start_research(player, "rtech")
        self.assertFalse(ok)
        self.assertIn("research lab", msg.lower())

    def test_wrong_tree_does_not_deduct_or_queue(self):
        """A rejected wrong-tree attempt changes nothing."""
        player = FakePlayer(research_tree="defense")
        ok, _ = self.system.start_research(player, "wtech")
        self.assertFalse(ok)
        self.assertEqual(player.db.researched_techs, set())
        # Ticking does not complete a never-queued research.
        for _ in range(5):
            self.system.process_tick()
        self.assertEqual(player.db.researched_techs, set())

    def test_list_available_filters_to_owned_tree(self):
        player = FakePlayer(research_tree="defense")
        keys = {t.key for t in self.system.list_available(player)}
        self.assertEqual(keys, {"dtech"})

    def test_list_available_empty_without_lab(self):
        player = FakePlayer(research_tree=None)
        self.assertEqual(self.system.list_available(player), [])

    def test_switching_lab_switches_the_researchable_tree(self):
        """Same player, different owned lab → different tree gate. Models a
        demolish-and-rebuild (one lab per planet, so switching is the way)."""
        player = FakePlayer(research_tree="weapons")
        ok, _ = self.system.start_research(player, "dtech")
        self.assertFalse(ok)  # weapons lab can't research defense
        player._research_tree = "defense"
        ok, msg = self.system.start_research(player, "dtech")
        self.assertTrue(ok, msg)


# -------------------------------------------------------------- #
#  Branch dormancy filter on the bonus recompute
#  (tech-tree-branch-foundation R5.1, R5.2, R5.3, R5.7, R5.10)
# -------------------------------------------------------------- #

class _NoLabPlayer(FakePlayer):
    """A player who owns no research lab at all — no Branch_Commitment."""

    def get_buildings(self):
        return []


class _SuspendedLabPlayer(FakePlayer):
    """A player whose lab is offline AND mid-upgrade, but still standing.

    R5.10's case: a non-Operational lab still confers its owner's commitment,
    because commitment follows OWNERSHIP of a completed lab, not the lab's
    Operational state.
    """

    def get_buildings(self):
        labs = super().get_buildings()
        for lab in labs:
            lab.db.offline = True
            lab.db.upgrading = True
        return labs


class _BrokenResolver:
    """A Branch resolver whose filter blows up on every call."""

    def __init__(self):
        self.calls = 0

    def applied_technologies(self, player, planet=None):
        self.calls += 1
        raise RuntimeError("resolver exploded")


class TestBranchDormancyBonusFilter(unittest.TestCase):
    """``recompute_tech_bonuses`` applies only the committed Branch's techs.

    The dormancy filter (R5.1): the rebuild runs over the researched record but
    accumulates only the technologies of the Branch the player is committed to
    on the occupied planet, minus any still awaiting Reinstatement (R5.7). The
    record itself is never touched (R5.3), and the filter comes from the real
    ``BranchSystem`` so the bonus dict and the unlock gate share one definition
    of an applied technology.

    ``_TREE_TECHS`` spans three trees: ``wtech`` (weapons, ``damage`` 10),
    ``dtech`` (defense, ``building_hp`` 50), ``rtech`` (research,
    ``sight_range`` 2).
    """

    ALL_TECHS = ("wtech", "dtech", "rtech")

    def setUp(self):
        from mygame.world.systems.branch_system import BranchSystem

        self.registry = _tree_registry()
        self.tech = TechLabSystem(self.registry, EventBus())
        self.branch = BranchSystem(
            self.registry, EventBus(), tech_system=self.tech,
        )

    def _researcher(self, tree="weapons", researched=ALL_TECHS, cls=FakePlayer):
        player = cls(research_tree=tree)
        player.db.researched_techs = set(researched)
        player.db.tech_bonuses = {}
        return player

    # -------------------------------------------------------------- #
    #  Unwired: the pre-feature rebuild, unchanged
    # -------------------------------------------------------------- #

    def test_unwired_resolver_accumulates_every_researched_tech(self):
        """No resolver injected → no dormancy, exactly as before this feature."""
        player = self._researcher()
        self.assertIsNone(self.tech._branch)

        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(
            player.db.tech_bonuses,
            {"damage": 10.0, "building_hp": 50.0, "sight_range": 2.0},
        )

    def test_a_broken_resolver_rebuilds_unfiltered_instead_of_zeroing(self):
        """A resolver that cannot answer must not silently erase every bonus."""
        resolver = _BrokenResolver()
        self.tech.set_branch_resolver(resolver)
        player = self._researcher()

        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(resolver.calls, 1)
        self.assertEqual(
            player.db.tech_bonuses,
            {"damage": 10.0, "building_hp": 50.0, "sight_range": 2.0},
        )

    # -------------------------------------------------------------- #
    #  Wired: the commitment filter (R5.1, R5.2)
    # -------------------------------------------------------------- #

    def test_only_the_committed_branchs_techs_apply(self):
        self.tech.set_branch_resolver(self.branch)
        expected = {
            "weapons": {"damage": 10.0},
            "defense": {"building_hp": 50.0},
            "research": {"sight_range": 2.0},
            # A committed Branch the player has researched nothing in applies
            # nothing — and withholds the other Branches all the same.
            "resource": {},
        }
        for tree, bonuses in expected.items():
            with self.subTest(commitment=tree):
                player = self._researcher(tree=tree)
                self.tech.recompute_tech_bonuses(player)
                self.assertEqual(player.db.tech_bonuses, bonuses)

    def test_no_commitment_applies_nothing(self):
        """R5.1 at its limit: committed to nothing → every tech is dormant."""
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(cls=_NoLabPlayer)

        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(player.db.tech_bonuses, {})

    def test_switching_the_committed_branch_swaps_the_applied_bonuses(self):
        """R5.2: the next recompute is the whole mechanism — the dict is derived."""
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons")

        self.tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

        player._research_tree = "defense"        # the lab that stands changed
        self.tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"building_hp": 50.0})

        # …and coming back restores the weapons bonus with no research (R5.9's
        # premise: the record was never touched).
        player._research_tree = "weapons"
        self.tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

    def test_the_filtered_rebuild_is_idempotent(self):
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons")

        self.tech.recompute_tech_bonuses(player)
        first = dict(player.db.tech_bonuses)
        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(dict(player.db.tech_bonuses), first)

    def test_an_explicit_planet_scopes_the_commitment(self):
        """The optional planet argument is what the arrival trigger will pass."""
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons")   # lab stands on "terra"

        self.tech.recompute_tech_bonuses(player, planet="terra")
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

        # On another planet the player owns no lab, so nothing is committed
        # there and every recorded technology is dormant.
        self.tech.recompute_tech_bonuses(player, planet="mars")
        self.assertEqual(player.db.tech_bonuses, {})

    # -------------------------------------------------------------- #
    #  The record is untouched (R5.3)
    # -------------------------------------------------------------- #

    def test_the_researched_record_survives_dormancy(self):
        self.tech.set_branch_resolver(self.branch)
        for tree in ("weapons", "defense", "research", "resource"):
            with self.subTest(commitment=tree):
                player = self._researcher(tree=tree)
                self.tech.recompute_tech_bonuses(player)
                self.assertEqual(
                    player.db.researched_techs, set(self.ALL_TECHS)
                )

    def test_no_commitment_still_keeps_the_record(self):
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(cls=_NoLabPlayer)

        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(player.db.researched_techs, set(self.ALL_TECHS))

    # -------------------------------------------------------------- #
    #  Reinstatement pending (R5.7)
    # -------------------------------------------------------------- #

    def test_a_tech_awaiting_reinstatement_is_excluded(self):
        from mygame.world.constants import ATTR_BRANCH_REINSTATEMENT

        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons")
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {"weapons": ["wtech"]})

        self.tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {})
        self.assertIn("wtech", player.db.researched_techs)   # R5.3

        # The job completes: the key leaves the pending set and the effect lands
        # at the same moment a first-time research effect would.
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {"weapons": []})
        self.tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

    def test_an_absent_pending_set_withholds_nothing(self):
        """Nothing writes the attribute until the Reinstatement bookkeeping
        lands, so its documented default must apply everything committed."""
        from mygame.world.constants import ATTR_BRANCH_REINSTATEMENT

        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons")
        self.assertIsNone(getattr(player.db, ATTR_BRANCH_REINSTATEMENT, None))

        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

    # -------------------------------------------------------------- #
    #  A suspended lab keeps its Branch applied (R5.10)
    # -------------------------------------------------------------- #

    def test_an_offline_mid_upgrade_lab_keeps_its_bonuses_applied(self):
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons", cls=_SuspendedLabPlayer)

        self.tech.recompute_tech_bonuses(player)

        # The lab withholds its FUNCTION, not the Branch's researched bonuses.
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

    # -------------------------------------------------------------- #
    #  One definition of "applied"
    # -------------------------------------------------------------- #

    def test_the_filter_agrees_with_the_unlock_gates_verdict(self):
        """The bonus filter and the building-unlock gate read the same rule.

        Both ask ``BranchSystem`` whether a technology's effects are applied, so
        a technology that unlocks a building is exactly a technology whose bonus
        is in the dict — the two can never disagree about a dormant or
        reinstatement-pending key.
        """
        from mygame.world.constants import ATTR_BRANCH_REINSTATEMENT

        for tree in ("weapons", "defense", "research", "resource"):
            for pending in ({}, {tree: ["wtech", "dtech", "rtech"]}):
                with self.subTest(commitment=tree, pending=bool(pending)):
                    player = self._researcher(tree=tree)
                    setattr(player.db, ATTR_BRANCH_REINSTATEMENT, pending)
                    applied = self.branch.applied_technologies(player)
                    for key in self.ALL_TECHS:
                        self.assertEqual(
                            key in applied,
                            self.branch._unapplied_reason(player, key) is None,
                        )

    def test_an_unrecorded_tech_is_never_applied(self):
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons", researched=())

        self.tech.recompute_tech_bonuses(player)

        self.assertEqual(player.db.tech_bonuses, {})
        self.assertEqual(self.branch.applied_technologies(player), frozenset())

    def test_a_stale_tech_key_is_skipped_under_the_filter(self):
        """An unknown key in the record cannot raise and cannot apply."""
        self.tech.set_branch_resolver(self.branch)
        player = self._researcher(tree="weapons",
                                  researched=("wtech", "deleted_tech"))

        self.tech.recompute_tech_bonuses(player)   # must not raise

        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})


# -------------------------------------------------------------- #
#  The Reinstatement research job
#  (tech-tree-branch-foundation R5.6, R5.7, R5.8)
# -------------------------------------------------------------- #

#: Priced weapons techs for the Reinstatement job, chosen so the scaled values
#: exercise the rounding and the floor at the default fraction of 0.5:
#: ``Iron 100 -> 50`` and ``Wood 51 -> 26`` (round-half-to-even on 25.5),
#: ``7 ticks -> 4``; and on the cheap one ``Iron 1 -> 1`` and ``1 tick -> 1``,
#: which are the floor rather than the arithmetic.
_JOB_TECHS = {
    "wpriced": TechnologyDef(
        name="Priced Weapon Tech", key="wpriced", tree="weapons",
        required_rank="Recruit", resource_cost={"Iron": 100, "Wood": 51},
        research_ticks=7, effect_type="stat_bonus",
        effect_value={"damage": 10},
    ),
    "wcheap": TechnologyDef(
        name="Cheap Weapon Tech", key="wcheap", tree="weapons",
        required_rank="Recruit", resource_cost={"Iron": 1, "Wood": 3},
        research_ticks=1, effect_type="stat_bonus",
        effect_value={"building_hp": 5},
    ),
    "wranked": TechnologyDef(
        name="Ranked Weapon Tech", key="wranked", tree="weapons",
        required_rank="Captain", resource_cost={"Iron": 10},
        research_ticks=4, effect_type="stat_bonus",
        effect_value={"damage": 1},
    ),
    "dpriced": TechnologyDef(
        name="Priced Defense Tech", key="dpriced", tree="defense",
        required_rank="Recruit", resource_cost={"Iron": 10},
        research_ticks=4, effect_type="stat_bonus",
        effect_value={"building_hp": 50},
    ),
}


class TestReinstatementResearchJob(unittest.TestCase):
    """A Reinstatement job is an ordinary research job at a reduced price.

    It rides the same ``_active_research`` queue, the same tick countdown, the
    same completion publish, and the same gates — the rank gate included,
    unchanged (R5.8). Two things differ: the resource cost per line and the
    duration are scaled by ``balance.branch_reinstatement_cost_fraction``
    (R5.6), and completing it clears the key from the Branch's pending set and
    rebuilds the bonus dict rather than adding to a record that already holds
    the key (R5.7).
    """

    def setUp(self):
        from mygame.world.constants import ATTR_BRANCH_REINSTATEMENT
        from mygame.world.systems.branch_system import BranchSystem

        self.attr = ATTR_BRANCH_REINSTATEMENT
        self.registry = _make_registry()
        self.registry.technologies = dict(_JOB_TECHS)
        self.bus = EventBus()
        self.tech = TechLabSystem(self.registry, self.bus)
        self.branch = BranchSystem(
            self.registry, self.bus, tech_system=self.tech,
        )
        self.tech.set_branch_resolver(self.branch)

    def _reinstater(self, pending=("wpriced",), recorded=("wpriced",),
                    rank_level=5, tree="weapons"):
        """A committed player whose record holds *recorded*, *pending* owed."""
        player = FakePlayer(
            rank_level=rank_level, research_tree=tree,
            resources={"Iron": 500, "Wood": 500},
        )
        player.db.researched_techs = set(recorded)
        player.db.tech_bonuses = {}
        setattr(player.db, self.attr, {tree: list(pending)})
        return player

    def _pending(self, player):
        return getattr(player.db, self.attr)

    def _tick(self, times):
        for _ in range(times):
            self.tech.process_tick()

    # -------------------------------------------------------------- #
    #  start_research treats a pending key as reinstatable (R5.7)
    # -------------------------------------------------------------- #

    def test_a_pending_key_starts_a_job_instead_of_being_refused(self):
        player = self._reinstater()

        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertTrue(ok, msg)
        self.assertIn("reinstat", msg.lower())

    def test_a_recorded_key_with_nothing_pending_is_still_refused(self):
        player = self._reinstater(pending=())

        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertFalse(ok)
        self.assertIn("already researched", msg)
        self.assertEqual(player.get_resource("Iron"), 500)
        self.assertEqual(self.tech._active_research, [])

    def test_an_unwired_resolver_keeps_the_pre_feature_refusal(self):
        """No resolver → no Reinstatement, so a recorded key is simply done."""
        self.tech.set_branch_resolver(None)
        player = self._reinstater()

        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertFalse(ok)
        self.assertIn("already researched", msg)

    def test_a_broken_resolver_refuses_rather_than_discounting(self):
        class _Exploding:
            def reinstatement_pending(self, player, tech_key):
                raise RuntimeError("resolver exploded")

        self.tech.set_branch_resolver(_Exploding())
        player = self._reinstater()

        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertFalse(ok)
        self.assertIn("already researched", msg)

    def test_the_job_cannot_be_started_twice(self):
        player = self._reinstater()
        self.assertTrue(self.tech.start_research(player, "wpriced")[0])

        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertFalse(ok)
        self.assertIn("already being researched", msg)
        self.assertEqual(len(self.tech._active_research), 1)

    def test_the_tree_gate_still_applies_to_a_reinstatement_job(self):
        """The Branch's lab must be owned — reinstating is researching."""
        player = self._reinstater(pending=("dpriced",), recorded=("dpriced",))
        setattr(player.db, self.attr, {"defense": ["dpriced"]})

        ok, msg = self.tech.start_research(player, "dpriced")

        self.assertFalse(ok)
        self.assertIn("defense", msg)
        self.assertIn("weapons", msg)

    # -------------------------------------------------------------- #
    #  The existing rank gate, unchanged (R5.8)
    # -------------------------------------------------------------- #

    def test_the_rank_gate_refuses_a_reinstatement_job_too(self):
        player = self._reinstater(pending=("wranked",), recorded=("wranked",),
                                  rank_level=1)

        ok, msg = self.tech.start_research(player, "wranked")

        self.assertFalse(ok)
        self.assertIn("Requires rank", msg)
        self.assertEqual(player.get_resource("Iron"), 500)
        self.assertEqual(self.tech._active_research, [])

    def test_the_rank_gate_passes_a_reinstatement_job_at_rank(self):
        player = self._reinstater(pending=("wranked",), recorded=("wranked",),
                                  rank_level=5)

        ok, msg = self.tech.start_research(player, "wranked")

        self.assertTrue(ok, msg)

    # -------------------------------------------------------------- #
    #  The marker, and the shared queue (R5.6)
    # -------------------------------------------------------------- #

    def test_the_entry_carries_the_reinstatement_marker(self):
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")

        entry = self.tech._active_research[0]
        self.assertIs(entry["reinstatement"], True)
        self.assertEqual(entry["tech_key"], "wpriced")
        self.assertIs(entry["player"], player)

    def test_a_first_time_job_carries_no_marker(self):
        player = self._reinstater(pending=(), recorded=())

        self.tech.start_research(player, "wpriced")

        self.assertIs(self.tech._active_research[0]["reinstatement"], False)

    # -------------------------------------------------------------- #
    #  Scaled cost and duration (R5.6)
    # -------------------------------------------------------------- #

    def test_the_cost_is_scaled_per_resource_line(self):
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")

        # Iron 100 * 0.5 = 50; Wood 51 * 0.5 = 25.5 -> 26.
        self.assertEqual(player.get_resource("Iron"), 450)
        self.assertEqual(player.get_resource("Wood"), 474)

    def test_the_duration_is_scaled(self):
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")

        # 7 ticks * 0.5 = 3.5 -> 4, and the countdown is the shared one.
        self.assertEqual(self.tech._active_research[0]["ticks_remaining"], 4)
        self._tick(3)
        self.assertEqual(len(self.tech._active_research), 1)
        self._tick(1)
        self.assertEqual(self.tech._active_research, [])

    def test_a_cheap_technology_keeps_a_floor_of_one_per_line_and_tick(self):
        player = self._reinstater(pending=("wcheap",), recorded=("wcheap",))

        ok, msg = self.tech.start_research(player, "wcheap")

        self.assertTrue(ok, msg)
        # Iron 1 * 0.5 = 0.5 -> 0, floored to 1; Wood 3 * 0.5 -> 2.
        self.assertEqual(player.get_resource("Iron"), 499)
        self.assertEqual(player.get_resource("Wood"), 498)
        self.assertEqual(self.tech._active_research[0]["ticks_remaining"], 1)

    def test_a_fraction_of_one_charges_the_defined_values(self):
        self.registry.balance.branch_reinstatement_cost_fraction = 1.0
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")

        self.assertEqual(player.get_resource("Iron"), 400)
        self.assertEqual(player.get_resource("Wood"), 449)
        self.assertEqual(self.tech._active_research[0]["ticks_remaining"], 7)

    def test_a_fraction_of_zero_still_costs_one_per_line_and_one_tick(self):
        self.registry.balance.branch_reinstatement_cost_fraction = 0.0
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")

        self.assertEqual(player.get_resource("Iron"), 499)
        self.assertEqual(player.get_resource("Wood"), 499)
        self.assertEqual(self.tech._active_research[0]["ticks_remaining"], 1)

    def test_a_first_time_job_is_never_scaled(self):
        player = self._reinstater(pending=(), recorded=())

        self.tech.start_research(player, "wpriced")

        self.assertEqual(player.get_resource("Iron"), 400)
        self.assertEqual(player.get_resource("Wood"), 449)
        self.assertEqual(self.tech._active_research[0]["ticks_remaining"], 7)

    def test_an_insufficient_purse_is_measured_against_the_scaled_cost(self):
        player = self._reinstater()
        player._resources.update({"Iron": 40, "Wood": 500})

        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertFalse(ok)
        self.assertIn("Iron: 40/50", msg)          # the scaled need, not 100
        self.assertEqual(player.get_resource("Iron"), 40)

    # -------------------------------------------------------------- #
    #  Completion clears the key and the effect lands (R5.7)
    # -------------------------------------------------------------- #

    def test_completion_clears_the_key_and_applies_the_effect(self):
        player = self._reinstater()
        self.tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {})    # withheld while pending

        self.tech.start_research(player, "wpriced")
        self._tick(4)

        self.assertEqual(self._pending(player), {})
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})
        self.assertEqual(player.db.researched_techs, {"wpriced"})   # R5.3

    def test_only_the_completed_key_leaves_the_pending_set(self):
        player = self._reinstater(pending=("wpriced", "wcheap"),
                                  recorded=("wpriced", "wcheap"))

        self.tech.start_research(player, "wcheap")
        self._tick(1)

        self.assertEqual(self._pending(player), {"weapons": ["wpriced"]})
        # The finished one applies; the one still owed stays withheld.
        self.assertEqual(player.db.tech_bonuses, {"building_hp": 5.0})

    def test_the_effect_is_withheld_until_the_countdown_ends(self):
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")
        self._tick(3)

        self.assertEqual(self._pending(player), {"weapons": ["wpriced"]})
        self.assertEqual(player.db.tech_bonuses, {})

    def test_completion_publishes_the_same_research_event(self):
        events = []
        self.bus.subscribe(TECHNOLOGY_RESEARCHED, lambda **kw: events.append(kw))
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")
        self._tick(4)

        self.assertEqual(len(events), 1)
        self.assertIs(events[0]["player"], player)
        self.assertEqual(events[0]["technology"].key, "wpriced")

    def test_a_reinstated_key_can_be_reinstated_again_after_a_reseed(self):
        """A second abandonment owes the key again; the job runs again."""
        player = self._reinstater()
        self.tech.start_research(player, "wpriced")
        self._tick(4)

        setattr(player.db, self.attr, {"weapons": ["wpriced"]})   # reseeded
        ok, msg = self.tech.start_research(player, "wpriced")

        self.assertTrue(ok, msg)
        self._tick(4)
        self.assertEqual(self._pending(player), {})
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})

    def test_a_completion_never_raises_when_the_resolver_cannot_clear(self):
        """A tick must survive a resolver that cannot take the write."""
        class _Exploding:
            def reinstatement_pending(self, player, tech_key):
                return True

            def on_reinstatement_completed(self, player, tech_key):
                raise RuntimeError("resolver exploded")

        self.tech.set_branch_resolver(_Exploding())
        player = self._reinstater()

        self.tech.start_research(player, "wpriced")
        self._tick(4)                                  # must not raise

        self.assertEqual(self.tech._active_research, [])
        self.assertEqual(self._pending(player), {"weapons": ["wpriced"]})

    def test_a_first_time_job_completes_exactly_as_before(self):
        """The shared path is untouched for a technology never researched."""
        player = self._reinstater(pending=(), recorded=())

        self.tech.start_research(player, "wpriced")
        self._tick(7)

        self.assertEqual(player.db.researched_techs, {"wpriced"})
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})


# -------------------------------------------------------------- #
#  The technology view (R13.1, R13.2, R13.5)
# -------------------------------------------------------------- #

class TestTechnologyView(unittest.TestCase):
    """``report_technology_view`` publishes the whole view as structured data.

    R13.1: the commitment on the occupied planet, that Branch's
    Signature_Vector, and that Branch's researched and available technologies.
    R13.2: every dormant Branch with the count of technologies recorded in it,
    plus the Reinstatement cost fraction. R13.5: figures and keys only — the
    ``technology_view`` formatter in the NotificationPresenter owns every word.
    """

    def setUp(self):
        from mygame.world.systems.branch_system import BranchSystem

        self.registry = _tree_registry()
        self.bus = EventBus()
        self.tech = TechLabSystem(self.registry, self.bus)
        self.branch = BranchSystem(
            self.registry, self.bus, tech_system=self.tech,
        )
        self.tech.set_branch_resolver(self.branch)

    def _viewer(self, tree="weapons", researched=("wtech", "dtech", "rtech"),
                cls=FakePlayer):
        player = cls(research_tree=tree)
        player.db.researched_techs = set(researched)
        return player

    def _published(self, player):
        """The (kind, data) pairs the view publishes for *player*."""
        from mygame.world.event_bus import PLAYER_NOTIFICATION

        seen = []
        self.bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda player=None, kind=None, data=None, **kw: seen.append(
                (player, kind, data)
            ),
        )
        view = self.tech.report_technology_view(player)
        return view, seen

    # -------------------------------------------------------------- #
    #  R13.1 — commitment, signature vector, researched, available
    # -------------------------------------------------------------- #

    def test_the_view_reports_the_commitment_and_its_signature_vector(self):
        from mygame.world.constants import (
            BRANCH_DOCTRINE, BRANCH_OPERATION_KIND,
        )

        for tree in ("weapons", "defense", "resource", "research"):
            with self.subTest(commitment=tree):
                view = self.tech.report_technology_view(self._viewer(tree=tree))
                self.assertEqual(view["branch"], tree)
                self.assertEqual(view["doctrine"], BRANCH_DOCTRINE[tree])
                self.assertEqual(
                    view["operation_kind"], BRANCH_OPERATION_KIND[tree]
                )
                self.assertEqual(view["planet"], "terra")

    def test_the_view_reports_the_committed_branchs_researched_technologies(self):
        view = self.tech.report_technology_view(self._viewer(tree="defense"))

        self.assertEqual(
            view["researched"], [{"key": "dtech", "name": "Defense Tech"}]
        )

    def test_the_view_reports_the_technologies_available_to_research(self):
        view = self.tech.report_technology_view(
            self._viewer(tree="weapons", researched=())
        )

        self.assertEqual(
            [entry["key"] for entry in view["available"]], ["wtech"]
        )
        self.assertEqual(view["researched"], [])

    def test_no_commitment_reports_no_branch_and_nothing_researchable(self):
        """R13.1 at its limit: no lab here, so no doctrine is committed."""
        view = self.tech.report_technology_view(self._viewer(cls=_NoLabPlayer))

        self.assertIsNone(view["branch"])
        self.assertIsNone(view["doctrine"])
        self.assertIsNone(view["operation_kind"])
        self.assertEqual(view["researched"], [])
        self.assertEqual(view["available"], [])

    def test_a_suspended_lab_still_reports_its_commitment(self):
        """R3.9/R5.10: commitment follows ownership, not Operational state."""
        view = self.tech.report_technology_view(
            self._viewer(tree="weapons", cls=_SuspendedLabPlayer)
        )

        self.assertEqual(view["branch"], "weapons")

    # -------------------------------------------------------------- #
    #  R13.2 — dormant Branches and the Reinstatement fraction
    # -------------------------------------------------------------- #

    def test_the_view_counts_the_record_in_each_dormant_branch(self):
        view = self.tech.report_technology_view(self._viewer(tree="weapons"))

        self.assertEqual(view["dormant"], [
            {"branch": "defense", "doctrine": "Fortification", "count": 1},
            {"branch": "research", "doctrine": "Recon", "count": 1},
        ])
        self.assertEqual(view["dormant_count"], 2)

    def test_no_commitment_leaves_the_whole_record_dormant(self):
        view = self.tech.report_technology_view(self._viewer(cls=_NoLabPlayer))

        self.assertEqual(
            [entry["branch"] for entry in view["dormant"]],
            ["weapons", "defense", "research"],
        )
        self.assertEqual(view["dormant_count"], 3)

    def test_a_record_wholly_inside_the_commitment_reports_no_dormancy(self):
        view = self.tech.report_technology_view(
            self._viewer(tree="weapons", researched=("wtech",))
        )

        self.assertEqual(view["dormant"], [])
        self.assertEqual(view["dormant_count"], 0)

    def test_the_view_quotes_the_configured_reinstatement_fraction(self):
        self.registry.balance.branch_reinstatement_cost_fraction = 0.25

        view = self.tech.report_technology_view(self._viewer())

        self.assertEqual(view["reinstatement_fraction"], 0.25)

    def test_the_view_names_the_keys_still_awaiting_reinstatement(self):
        from mygame.world.constants import ATTR_BRANCH_REINSTATEMENT

        player = self._viewer(tree="weapons")
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {"weapons": ["wtech"]})

        view = self.tech.report_technology_view(player)

        self.assertEqual(view["reinstatement_pending"], ["wtech"])
        # The key is still on record, so it is still reported as researched.
        self.assertEqual([e["key"] for e in view["researched"]], ["wtech"])

    def test_an_unwired_resolver_still_reports_the_record_it_can_read(self):
        """No Branch system wired: the view groups the record locally."""
        self.tech.set_branch_resolver(None)

        view = self.tech.report_technology_view(self._viewer(tree="weapons"))

        self.assertEqual(view["branch"], "weapons")
        self.assertEqual(view["dormant_count"], 2)
        self.assertEqual(view["reinstatement_pending"], [])

    def test_a_broken_resolver_falls_back_instead_of_raising(self):
        class _Exploding:
            def commitment(self, player, planet=None):
                return "weapons"

            def dormant_branches(self, player, planet=None):
                raise RuntimeError("resolver exploded")

        self.tech.set_branch_resolver(_Exploding())

        view = self.tech.report_technology_view(self._viewer(tree="weapons"))

        self.assertEqual(view["branch"], "weapons")
        self.assertEqual(view["dormant_count"], 2)

    # -------------------------------------------------------------- #
    #  R13.5 — one structured notification, no composed text
    # -------------------------------------------------------------- #

    def test_the_view_publishes_one_structured_notification(self):
        player = self._viewer()

        view, seen = self._published(player)

        self.assertEqual(len(seen), 1)
        target, kind, data = seen[0]
        self.assertIs(target, player)
        self.assertEqual(kind, "technology_view")
        self.assertEqual(data, view)

    def test_the_published_kind_has_a_formatter(self):
        from mygame.world.presenters.notification_presenter import (
            NotificationPresenter,
        )

        self.assertIn("technology_view", NotificationPresenter._FORMATTERS)

    def test_the_view_writes_nothing(self):
        """Asking for the view changes no record and no bonus dict."""
        player = self._viewer(tree="weapons")
        player.db.tech_bonuses = {"damage": 10.0}

        self.tech.report_technology_view(player)

        self.assertEqual(
            player.db.researched_techs, {"wtech", "dtech", "rtech"}
        )
        self.assertEqual(player.db.tech_bonuses, {"damage": 10.0})


if __name__ == "__main__":
    unittest.main()
