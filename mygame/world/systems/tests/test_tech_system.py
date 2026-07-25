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
        """Start *tech_key* and tick it to completion."""
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
        """Start *tech_key* and tick it to completion."""
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
        """Start *tech_key* and tick it to completion."""
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
        self.slot = "weapon"
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


if __name__ == "__main__":
    unittest.main()
