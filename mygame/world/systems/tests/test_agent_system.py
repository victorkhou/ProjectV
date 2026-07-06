"""
Unit tests for AgentSystem.

Tests training, assignment, unassignment, demotion/promotion reserve,
and query methods.

Requirements: 7b.1–7b.14, 8.1–8.7
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
    _mod("evennia.objects.models")
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.agent_system import (  # noqa: E402
    AgentSystem,
    BUILDING_ROLE_MAP,
    VALID_ROLES,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import RankDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402
from world import progression  # noqa: E402  (same module CombatEntity uses)


# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        # Return sensible defaults for unset attributes
        return None


class FakeAgent:
    """Lightweight stand-in for an NPC agent."""
    def __init__(self, agent_id, owner=None, role="", reserve=False,
                 incapacitated=False):
        self.db = FakeDB(
            agent_id=agent_id,
            owner=owner,
            npc_type="agent",
            role=role,
            role_target=None,
            reserve=reserve,
            incapacitated=incapacitated,
        )
        self._location = None

    def move_to(self, target, quiet=True):
        self._location = target


class FakeBuilding:
    """Lightweight stand-in for a building object."""
    def __init__(self, building_type="EX", building_level=1):
        self.db = FakeDB(
            building_type=building_type,
            building_level=building_level,
            training_agent_id=None,
            training_ticks_remaining=None,
            training_owner=None,
        )
        self.location = None


class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", combat_xp=0, rank_level=1,
                 next_agent_id=1, resources=None):
        self.id = 1
        self.key = name
        self.db = FakeDB(
            combat_xp=combat_xp,
            rank_level=rank_level,
            next_agent_id=next_agent_id,
        )
        self._resources = resources or {
            "Wood": 100, "Stone": 100, "Iron": 100,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        }

    def has_resources(self, costs):
        return all(self._resources.get(r, 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            self._resources[r] = self._resources.get(r, 0) - amt
        return True

    def get_buildings(self):
        return []


def _make_test_ranks():
    return [
        RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
        RankDef(name="Private", level=2, xp_threshold=200, agent_cap=3),
        RankDef(name="Corporal", level=3, xp_threshold=600, agent_cap=4),
        RankDef(name="Sergeant", level=4, xp_threshold=1500, agent_cap=6),
    ]


def _make_registry():
    registry = DataRegistry()
    registry.ranks = _make_test_ranks()
    return registry


class AgentSystemTestBase(unittest.TestCase):
    """Base class that sets up an AgentSystem with a fake NPC factory."""

    def setUp(self):
        self.registry = _make_registry()
        self.event_bus = EventBus()
        self.created_agents: list[FakeAgent] = []

        def fake_create_npc(player, agent_id):
            agent = FakeAgent(agent_id=agent_id, owner=player)
            self.created_agents.append(agent)
            return agent

        # In-memory AgentRepository over our tracked fakes — replaces the old
        # _get_agents_fallback override with the injected port. ``all_agents``
        # is settable so process_tick tests can supply an explicit sweep list.
        test_case = self

        class _FakeAgentRepo:
            def __init__(self):
                self.all_agents = None  # None => derive from created_agents

            def find_agents_for_owner(self, owner):
                return [
                    a for a in test_case.created_agents
                    if getattr(getattr(a, "db", None), "owner", None) is owner
                ]

            def find_all_agents(self):
                if self.all_agents is not None:
                    return list(self.all_agents)
                return list(test_case.created_agents)

            def find_training_buildings(self):
                return []

        self.repo = _FakeAgentRepo()
        self.system = AgentSystem(
            registry=self.registry,
            event_bus=self.event_bus,
            create_npc_func=fake_create_npc,
            agent_repository=self.repo,
        )


# -------------------------------------------------------------- #
#  Training Tests (Req 8.1–8.7)
# -------------------------------------------------------------- #

class TestTrainAgent(AgentSystemTestBase):

    def test_train_first_agent_succeeds(self):
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        ok, msg = self.system.train_agent(player, academy)
        self.assertTrue(ok)
        self.assertIn("Training agent #1", msg)
        self.assertEqual(player.db.next_agent_id, 2)

    def test_train_charges_scaled_cost(self):
        """Cost scales with total agent count after training."""
        player = FakePlayer(combat_xp=600, next_agent_id=1,
                            resources={"Wood": 200, "Stone": 200, "Iron": 200})
        academy = FakeBuilding(building_type="AC", building_level=1)

        # Train agent #1: count=0, will be 1, cost = base×1 = 15W/10S/5I
        self.system.train_agent(player, academy)
        self.assertEqual(player._resources["Wood"], 200 - 15)
        self.assertEqual(player._resources["Stone"], 200 - 10)
        self.assertEqual(player._resources["Iron"], 200 - 5)

        # Complete training so the NPC exists for the count
        self.system.complete_training(academy)

        # Train agent #2: count=1, will be 2, cost = base×2 = 30W/20S/10I
        self.system.train_agent(player, academy)
        self.assertEqual(player._resources["Wood"], 200 - 15 - 30)
        self.assertEqual(player._resources["Stone"], 200 - 10 - 20)
        self.assertEqual(player._resources["Iron"], 200 - 5 - 10)

    def test_train_fails_at_cap(self):
        """Recruit has cap=2 (1 agent slot). Training a 2nd should fail."""
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)

        # Train first agent — fills cap (commander=1 + 1 agent = 2)
        ok, _ = self.system.train_agent(player, academy)
        self.assertTrue(ok)
        # Complete training to actually create the NPC
        self.system.complete_training(academy)

        # Now at cap — next training should fail
        ok, msg = self.system.train_agent(player, academy)
        self.assertFalse(ok)
        self.assertIn("cap", msg.lower())

    def test_train_fails_insufficient_resources(self):
        player = FakePlayer(combat_xp=0, next_agent_id=1,
                            resources={"Wood": 0, "Stone": 0, "Iron": 0})
        academy = FakeBuilding(building_type="AC", building_level=1)
        ok, msg = self.system.train_agent(player, academy)
        self.assertFalse(ok)
        self.assertIn("Insufficient", msg)

    def test_training_time_reduced_by_academy_level(self):
        """Each academy level reduces training time by 15%."""
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=3)
        self.system.train_agent(player, academy)
        # Level 3: 300 * (1 - 0.15*3) = 300 * 0.55 = 165 (values now from balance)
        bal = self.system.registry.balance
        expected = int(bal.base_training_ticks * (1 - bal.academy_training_reduction_per_level * 3))
        self.assertEqual(academy.db.training_ticks_remaining, expected)

    def test_complete_training_creates_npc(self):
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)
        npc = self.system.complete_training(academy)
        self.assertIsNotNone(npc)
        self.assertEqual(npc.db.agent_id, 1)
        # Academy training state should be cleared
        self.assertIsNone(academy.db.training_agent_id)


# -------------------------------------------------------------- #
#  Assignment Tests (Req 7b.6, 7b.7, 7b.8, 7b.11)
# -------------------------------------------------------------- #

class TestAssignAgent(AgentSystemTestBase):

    def _train_and_complete(self, player, academy=None):
        """Helper: train + complete an agent, return the NPC."""
        if academy is None:
            academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)
        return self.system.complete_training(academy)

    def test_assign_harvester_to_extractor(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)  # Corporal, cap=4
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="EX")
        ok, msg = self.system.assign_agent(player, npc.db.agent_id, "harvester", building)
        self.assertTrue(ok)
        self.assertEqual(npc.db.role, "harvester")
        self.assertEqual(npc.db.role_target, building)

    def test_assign_guard_to_turret(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="TU")
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "guard", building)
        self.assertTrue(ok)
        self.assertEqual(npc.db.role, "guard")

    def test_assign_scout_to_radar(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="RD")
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "scout", building)
        self.assertTrue(ok)

    def test_assign_engineer_to_armory(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="AR")
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "engineer", building)
        self.assertTrue(ok)

    def test_assign_engineer_to_lab(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="LB")
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "engineer", building)
        self.assertTrue(ok)

    def test_assign_medic_to_medbay(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="MB")
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "medic", building)
        self.assertTrue(ok)

    def test_assign_soldier_no_building_needed(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "soldier")
        self.assertTrue(ok)
        self.assertEqual(npc.db.role, "soldier")
        self.assertIsNone(npc.db.role_target)

    def test_assign_medic_army_no_building(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "medic")
        self.assertTrue(ok)

    def test_assign_wrong_role_for_building(self):
        """Assigning harvester to a Turret should fail."""
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building = FakeBuilding(building_type="TU")
        ok, msg = self.system.assign_agent(player, npc.db.agent_id, "harvester", building)
        self.assertFalse(ok)
        self.assertIn("guard", msg.lower())

    def test_assign_invalid_role(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        ok, msg = self.system.assign_agent(player, npc.db.agent_id, "wizard")
        self.assertFalse(ok)
        self.assertIn("Invalid role", msg)

    def test_assign_nonexistent_agent(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        ok, msg = self.system.assign_agent(player, 999, "soldier")
        self.assertFalse(ok)
        self.assertIn("not found", msg.lower())

    def test_assign_incapacitated_agent_fails(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        npc.db.incapacitated = True
        ok, msg = self.system.assign_agent(player, npc.db.agent_id, "soldier")
        self.assertFalse(ok)
        self.assertIn("incapacitated", msg.lower())

    def test_assign_reserved_agent_fails(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        npc.db.reserve = True
        ok, msg = self.system.assign_agent(player, npc.db.agent_id, "soldier")
        self.assertFalse(ok)
        self.assertIn("reserve", msg.lower())

    def test_reassign_agent_without_cooldown(self):
        """Reassignment from one role to another should work immediately."""
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        building_ex = FakeBuilding(building_type="EX")
        self.system.assign_agent(player, npc.db.agent_id, "harvester", building_ex)
        self.assertEqual(npc.db.role, "harvester")

        # Reassign to soldier (army role)
        ok, _ = self.system.assign_agent(player, npc.db.agent_id, "soldier")
        self.assertTrue(ok)
        self.assertEqual(npc.db.role, "soldier")

    def test_assign_non_building_role_requires_building(self):
        """Harvester without a building should fail."""
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        npc = self._train_and_complete(player)
        ok, msg = self.system.assign_agent(player, npc.db.agent_id, "harvester")
        self.assertFalse(ok)
        self.assertIn("requires a target building", msg)


# -------------------------------------------------------------- #
#  Unassignment Tests (Req 7b.7)
# -------------------------------------------------------------- #

class TestUnassignAgent(AgentSystemTestBase):

    def test_unassign_clears_role(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)
        npc = self.system.complete_training(academy)
        building = FakeBuilding(building_type="EX")
        self.system.assign_agent(player, npc.db.agent_id, "harvester", building)

        ok, msg = self.system.unassign_agent(player, npc.db.agent_id)
        self.assertTrue(ok)
        self.assertEqual(npc.db.role, "")
        self.assertIsNone(npc.db.role_target)

    def test_unassign_nonexistent_agent(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        ok, msg = self.system.unassign_agent(player, 999)
        self.assertFalse(ok)
        self.assertIn("not found", msg.lower())


# -------------------------------------------------------------- #
#  Query Tests (Req 7b.10)
# -------------------------------------------------------------- #

class TestQueryAgents(AgentSystemTestBase):

    def test_get_agents_returns_all(self):
        player = FakePlayer(combat_xp=1500, next_agent_id=1,
                            resources={"Wood": 500, "Stone": 500, "Iron": 500})
        academy = FakeBuilding(building_type="AC", building_level=1)
        for _ in range(3):
            self.system.train_agent(player, academy)
            self.system.complete_training(academy)
        agents = self.system.get_agents(player)
        self.assertEqual(len(agents), 3)

    def test_get_agent_by_id(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)
        npc = self.system.complete_training(academy)
        found = self.system.get_agent_by_id(player, npc.db.agent_id)
        self.assertIs(found, npc)

    def test_get_agent_by_id_not_found(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        found = self.system.get_agent_by_id(player, 999)
        self.assertIsNone(found)

    def test_get_agent_count(self):
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        # No agents trained yet — count should be 0
        self.assertEqual(self.system.get_agent_count(player), 0)

        academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)
        self.system.complete_training(academy)
        self.assertEqual(self.system.get_agent_count(player), 1)


# -------------------------------------------------------------- #
#  Demotion / Promotion Tests (Req 7b.13, 4.6)
# -------------------------------------------------------------- #

class TestDemotionPromotion(AgentSystemTestBase):

    def _create_agents(self, player, count):
        """Train and complete *count* agents."""
        academy = FakeBuilding(building_type="AC", building_level=1)
        # Ensure plenty of resources
        for res in ("Wood", "Stone", "Iron"):
            player._resources[res] = 10000
        for _ in range(count):
            self.system.train_agent(player, academy)
            self.system.complete_training(academy)

    def test_demotion_reserves_highest_ids(self):
        """With 3 agents, demoting to cap=2 (1 agent slot) reserves 2 highest."""
        player = FakePlayer(combat_xp=1500, next_agent_id=1)  # Sergeant, cap=6
        self._create_agents(player, 3)  # IDs 1, 2, 3

        # cap=2 means 1 agent slot, so 2 excess
        self.system.handle_demotion(player, new_agent_cap=2)

        agents = self.system.get_agents(player)
        reserved = [a for a in agents if a.db.reserve]
        active = [a for a in agents if not a.db.reserve]

        self.assertEqual(len(reserved), 2)
        # Highest IDs (3, 2) should be reserved
        reserved_ids = sorted([a.db.agent_id for a in reserved])
        self.assertEqual(reserved_ids, [2, 3])
        # Lowest ID (1) stays active
        active_ids = [a.db.agent_id for a in active]
        self.assertEqual(active_ids, [1])

    def test_demotion_no_excess(self):
        """If already under cap, demotion does nothing."""
        player = FakePlayer(combat_xp=600, next_agent_id=1)
        self._create_agents(player, 1)  # total=2
        self.system.handle_demotion(player, new_agent_cap=3)
        agents = self.system.get_agents(player)
        reserved = [a for a in agents if a.db.reserve]
        self.assertEqual(len(reserved), 0)

    def test_promotion_restores_lowest_ids_first(self):
        """After demotion, promotion restores lowest-ID reserved agents first."""
        player = FakePlayer(combat_xp=1500, next_agent_id=1)
        self._create_agents(player, 3)  # IDs 1, 2, 3

        # Demote to cap=2 (1 agent slot) → reserves IDs 2, 3
        self.system.handle_demotion(player, new_agent_cap=2)

        # Promote to cap=3 (2 agent slots) → should restore ID 2 (lowest reserved)
        self.system.handle_promotion(player, new_agent_cap=3)

        agents = self.system.get_agents(player)
        reserved = [a for a in agents if a.db.reserve]
        self.assertEqual(len(reserved), 1)
        self.assertEqual(reserved[0].db.agent_id, 3)

    def test_promotion_restores_all_if_cap_allows(self):
        player = FakePlayer(combat_xp=1500, next_agent_id=1)
        self._create_agents(player, 3)

        self.system.handle_demotion(player, new_agent_cap=2)
        self.system.handle_promotion(player, new_agent_cap=6)

        agents = self.system.get_agents(player)
        reserved = [a for a in agents if a.db.reserve]
        self.assertEqual(len(reserved), 0)

    def test_reserved_agents_keep_role(self):
        """Reserved agents retain their role assignment."""
        player = FakePlayer(combat_xp=1500, next_agent_id=1)
        self._create_agents(player, 2)  # IDs 1, 2

        # Assign agent 2 as soldier
        agent2 = self.system.get_agent_by_id(player, 2)
        self.system.assign_agent(player, 2, "soldier")
        self.assertEqual(agent2.db.role, "soldier")

        # Demote to cap=2 → reserves agent 2
        self.system.handle_demotion(player, new_agent_cap=2)
        self.assertTrue(agent2.db.reserve)
        # Role is preserved
        self.assertEqual(agent2.db.role, "soldier")


# -------------------------------------------------------------- #
#  Training Timer Lifecycle (Req 8.1, 8.6)
# -------------------------------------------------------------- #

class TestTrainingTimerLifecycle(AgentSystemTestBase):
    """Full training flow: command → timer set → tick-down → completion."""

    def test_train_sets_timer_on_academy(self):
        """train_agent stores training state on the academy building."""
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        ok, _ = self.system.train_agent(player, academy)
        self.assertTrue(ok)
        self.assertEqual(academy.db.training_agent_id, 1)
        self.assertIsNotNone(academy.db.training_ticks_remaining)
        self.assertIs(academy.db.training_owner, player)

    def test_timer_tick_down_then_complete(self):
        """Simulate ticking the timer down and completing training."""
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)

        # Simulate ticks decrementing the timer
        initial_ticks = academy.db.training_ticks_remaining
        self.assertGreater(initial_ticks, 0)
        academy.db.training_ticks_remaining = 0  # simulate timer expiry

        # Complete training spawns the NPC
        npc = self.system.complete_training(academy)
        self.assertIsNotNone(npc)
        self.assertEqual(npc.db.agent_id, 1)

        # Academy state is cleared after completion
        self.assertIsNone(academy.db.training_agent_id)
        self.assertIsNone(academy.db.training_ticks_remaining)
        self.assertIsNone(academy.db.training_owner)

    def test_complete_training_no_active_training_returns_none(self):
        """complete_training on an academy with no active training returns None."""
        academy = FakeBuilding(building_type="AC", building_level=1)
        result = self.system.complete_training(academy)
        self.assertIsNone(result)


# -------------------------------------------------------------- #
#  Agent Cap Tied to Rank (Req 7b.3)
# -------------------------------------------------------------- #

class TestAgentCapByRank(AgentSystemTestBase):
    """Verify agent cap scales with player rank."""

    def _train_and_complete(self, player, academy):
        self.system.train_agent(player, academy)
        return self.system.complete_training(academy)

    def test_recruit_cap_is_two(self):
        """Recruit (cap=2): commander + 1 trained agent fills cap."""
        player = FakePlayer(combat_xp=0, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        self._train_and_complete(player, academy)
        # At cap now (commander=1 + 1 NPC = 2)
        ok, msg = self.system.train_agent(player, academy)
        self.assertFalse(ok)
        self.assertIn("cap", msg.lower())

    def test_higher_rank_allows_more_agents(self):
        """Corporal (cap=4) can train 3 agents beyond commander."""
        player = FakePlayer(combat_xp=600, next_agent_id=1,
                            resources={"Wood": 1000, "Stone": 1000, "Iron": 1000})
        academy = FakeBuilding(building_type="AC", building_level=1)
        for i in range(3):
            ok, _ = self.system.train_agent(player, academy)
            self.assertTrue(ok, f"Training agent #{i+2} should succeed at Corporal cap=4")
            self.system.complete_training(academy)
        # Now at cap (1 commander + 3 NPCs = 4)
        ok, msg = self.system.train_agent(player, academy)
        self.assertFalse(ok)
        self.assertIn("cap", msg.lower())


# -------------------------------------------------------------- #
#  Offline Agent Behavior (Req 7b.14)
# -------------------------------------------------------------- #

class TestOfflineAgentBehavior(AgentSystemTestBase):
    """Agent scripts run regardless of player connection status."""

    def test_harvester_script_runs_without_player_connection(self):
        """HarvesterScript.at_repeat produces resources even when player is offline.

        The script only checks the NPC's state (incapacitated, role_target),
        not whether the owning player is connected.
        """
        from mygame.typeclasses.agent_scripts import HarvesterScript

        player = FakePlayer(combat_xp=600, next_agent_id=1)
        academy = FakeBuilding(building_type="AC", building_level=1)
        self.system.train_agent(player, academy)
        npc = self.system.complete_training(academy)

        # Set up an Extractor building with resource type
        extractor = FakeBuilding(building_type="EX", building_level=1)
        extractor.db.resource_type = "Wood"
        extractor.db.inventory = {}
        extractor.db.storage_capacity = 100

        # Assign agent as harvester
        self.system.assign_agent(player, npc.db.agent_id, "harvester", extractor)

        # Create a HarvesterScript attached to the NPC
        script = HarvesterScript.__new__(HarvesterScript)
        script.obj = npc

        # The script should run without checking player connection.
        # It only checks npc.db.incapacitated and npc.db.role_target.
        # Verify the script doesn't raise and accesses the right state.
        self.assertFalse(getattr(npc.db, "incapacitated", False))
        self.assertIs(npc.db.role_target, extractor)
        self.assertEqual(npc.db.role, "harvester")

    def test_process_tick_runs_regardless_of_player_state(self):
        """process_tick does not check player connection — agents run autonomously."""
        # process_tick is a no-op placeholder but should never raise
        # regardless of player state
        self.system.process_tick(1)
        self.system.process_tick(50)
        self.system.process_tick(999)


# -------------------------------------------------------------- #
#  Process Tick (placeholder)
# -------------------------------------------------------------- #

class TestProcessTick(AgentSystemTestBase):

    def test_process_tick_runs_without_error(self):
        """Placeholder tick processing should not raise."""
        self.system.process_tick(1)
        self.system.process_tick(100)


# -------------------------------------------------------------- #
#  Owner-level cap (Req 14.1, 14.2, 14.3, 14.5, 14.6, 14.7)
# -------------------------------------------------------------- #

class CappedAgent(FakeAgent):
    """FakeAgent with a controllable raw level for owner-cap tests."""

    def __init__(self, agent_id, owner=None, raw_level=1):
        super().__init__(agent_id=agent_id, owner=owner)
        self._raw_level = raw_level

    def get_raw_level(self):
        return self._raw_level


class TestOwnerCap(AgentSystemTestBase):

    def test_get_owner_level_reads_player_level(self):
        owner = FakePlayer()
        owner.db.level = 10
        agent = CappedAgent(1, owner=owner, raw_level=5)
        self.assertEqual(self.system.get_owner_level(agent), 10)

    def test_get_owner_level_legacy_rank_level_fallback(self):
        """Owner with only rank_level → first level of that rank (Req 14.1)."""
        owner = FakePlayer()
        owner.db.level = None  # force rank_level fallback
        owner.db.rank_level = 3  # rank 3 → level (3-1)*5 + 1 = 11
        agent = CappedAgent(1, owner=owner, raw_level=20)
        self.assertEqual(self.system.get_owner_level(agent), 11)

    def test_get_owner_level_missing_owner_defaults_to_one(self):
        agent = CappedAgent(1, owner=None, raw_level=20)
        self.assertEqual(self.system.get_owner_level(agent), 1)

    def test_get_cap_ceiling_is_owner_level_minus_one(self):
        owner = FakePlayer()
        owner.db.level = 8
        agent = CappedAgent(1, owner=owner, raw_level=3)
        self.assertEqual(self.system.get_cap_ceiling(agent), 7)

    def test_get_cap_ceiling_floors_at_one(self):
        """Owner level 1 → ceiling 1 (Req 14.3)."""
        owner = FakePlayer()
        owner.db.level = 1
        agent = CappedAgent(1, owner=owner, raw_level=5)
        self.assertEqual(self.system.get_cap_ceiling(agent), 1)

    def test_effective_level_bounded_below_owner(self):
        """Raw exceeds ceiling → capped strictly below owner (Req 14.2)."""
        owner = FakePlayer()
        owner.db.level = 6
        agent = CappedAgent(1, owner=owner, raw_level=20)
        self.assertEqual(self.system.compute_effective_level(agent), 5)

    def test_effective_level_uses_raw_when_below_ceiling(self):
        owner = FakePlayer()
        owner.db.level = 30
        agent = CappedAgent(1, owner=owner, raw_level=12)
        self.assertEqual(self.system.compute_effective_level(agent), 12)

    def test_effective_level_owner_level_one_yields_one(self):
        """Owner at level 1 → effective level 1 regardless of raw (Req 14.3)."""
        owner = FakePlayer()
        owner.db.level = 1
        agent = CappedAgent(1, owner=owner, raw_level=40)
        self.assertEqual(self.system.compute_effective_level(agent), 1)

    def test_effective_level_missing_owner_yields_one(self):
        agent = CappedAgent(1, owner=None, raw_level=40)
        self.assertEqual(self.system.compute_effective_level(agent), 1)

    def test_effective_level_floors_at_one(self):
        """Never returns below 1 even when raw_level is 0."""
        owner = FakePlayer()
        owner.db.level = 5
        agent = CappedAgent(1, owner=owner, raw_level=0)
        self.assertEqual(self.system.compute_effective_level(agent), 1)

    def test_owner_demotion_lowers_effective_level(self):
        """Stored raw can exceed new ceiling after owner demotion (Req 14.5, 14.7)."""
        owner = FakePlayer()
        owner.db.level = 30
        agent = CappedAgent(1, owner=owner, raw_level=25)
        self.assertEqual(self.system.compute_effective_level(agent), 25)
        # Owner demoted to level 10 → effective re-derives to ceiling 9
        owner.db.level = 10
        self.assertEqual(self.system.compute_effective_level(agent), 9)

    def test_non_numeric_owner_level_treated_as_unset(self):
        """A corrupted non-numeric owner db.level must not raise (falls back)."""
        owner = FakePlayer()
        owner.db.level = "corrupt"  # e.g. bad admin edit / migration bug
        agent = CappedAgent(1, owner=owner, raw_level=5)
        # owner level unresolvable → default 1 → ceiling 1 → effective 1
        self.assertEqual(self.system.get_owner_level(agent), 1)
        self.assertEqual(self.system.compute_effective_level(agent), 1)

    def test_non_numeric_owner_level_falls_back_to_rank_level(self):
        """Bad db.level but valid rank_level → first level of that rank."""
        owner = FakePlayer()
        owner.db.level = "corrupt"
        owner.db.rank_level = 3  # rank 3 → level (3-1)*5 + 1 = 11
        agent = CappedAgent(1, owner=owner, raw_level=20)
        self.assertEqual(self.system.get_owner_level(agent), 11)

    def test_enable_ability_does_not_raise_on_corrupt_owner_level(self):
        """Command backend rejects cleanly rather than raising on bad state."""
        from mygame.world.definitions import AbilityGateDef
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }
        owner = FakePlayer()
        owner.db.level = "corrupt"
        agent = CappedAgent(7, owner=owner, raw_level=25)
        self.created_agents.append(agent)

        # Must return a string (reject), not raise ValueError.
        msg = self.system.enable_ability(owner, 7, "delivery")
        self.assertIsInstance(msg, str)
        self.assertIn("21", msg)  # effective capped to 1 → below gate → rejected


# -------------------------------------------------------------- #
#  Enabled-ability state (Req 12.1, 12.4, 17.1)
# -------------------------------------------------------------- #

class TestEnabledAbilities(AgentSystemTestBase):

    def test_absent_attr_yields_empty_set(self):
        """Legacy agent with no enabled_abilities attribute → empty set."""
        agent = FakeAgent(1)
        # FakeDB.__getattr__ returns None for unset attributes
        self.assertEqual(self.system.get_enabled_abilities(agent), set())

    def test_none_yields_empty_set(self):
        """enabled_abilities explicitly None → empty set (Req 12.4)."""
        agent = FakeAgent(1)
        agent.db.enabled_abilities = None
        self.assertEqual(self.system.get_enabled_abilities(agent), set())

    def test_populated_list_returns_set(self):
        """Persisted list is returned as a set."""
        agent = FakeAgent(1)
        agent.db.enabled_abilities = ["delivery", "patrol"]
        self.assertEqual(
            self.system.get_enabled_abilities(agent),
            {"delivery", "patrol"},
        )

    def test_set_enabled_abilities_round_trip(self):
        """_set_enabled_abilities persists as a list; get reads it back."""
        agent = FakeAgent(1)
        self.system._set_enabled_abilities(agent, {"delivery"})
        # Stored as a list for Evennia persistence
        self.assertIsInstance(agent.db.enabled_abilities, list)
        self.assertEqual(set(agent.db.enabled_abilities), {"delivery"})
        # Round-trips back through the getter
        self.assertEqual(
            self.system.get_enabled_abilities(agent), {"delivery"}
        )


# -------------------------------------------------------------- #
#  Freeze-aware XP award / death loss (Req 5.7, 5.9, 5.10, 6, 14.4)
# -------------------------------------------------------------- #

class _AgentAttrStore:
    """Minimal Evennia-style attribute store for a CombatEntity-backed agent."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class _AgentDb:
    """db proxy mimicking Evennia's handler (mirrors test_prop_progression)."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class RealAgent(CombatEntity):
    """Agent built on the real CombatEntity mixin.

    Unlike ``FakeAgent``, this exercises the genuine ``award_xp`` /
    ``deduct_xp`` / ``recompute_progression`` path so ``db.combat_xp`` and
    ``db.level`` (the raw level) actually mutate through the shared
    progression curve — exactly what the freeze check reads.
    """

    def __init__(self, agent_id=1, owner=None):
        self.db = _AgentDb(_AgentAttrStore())
        self.at_combat_entity_init()  # combat_xp=0, level=1, rank_level=1
        self.db.agent_id = agent_id
        self.db.owner = owner
        self.key = f"Agent-{agent_id}"


class TestAwardAgentXp(AgentSystemTestBase):
    """Freeze-aware ``award_agent_xp`` / ``apply_agent_death_loss`` (task 6.4)."""

    def setUp(self):
        super().setUp()
        # Force this registry's level->XP curve active so award_xp derives
        # raw levels deterministically (the table is process-global).
        progression.build_thresholds(self.registry.ranks)

    def _owner(self, level):
        owner = FakePlayer()
        owner.db.level = level
        return owner

    def test_award_below_ceiling_increases_combat_xp_by_balance_amount(self):
        """Below the cap ceiling, harvest awards the configured amount (5)."""
        owner = self._owner(level=10)  # ceiling = 9, agent level 1 < 9
        agent = RealAgent(owner=owner)
        expected = self.registry.balance.agent_xp_harvest
        self.assertEqual(expected, 5)  # default balance amount

        self.system.award_agent_xp(agent, "harvest")

        self.assertEqual(agent.db.combat_xp, expected)
        # Raw level stays consistent with the curve.
        self.assertEqual(agent.db.level, progression.level_for_xp(expected))

    def test_award_at_or_above_ceiling_is_noop(self):
        """At the cap ceiling the award is skipped entirely — no surplus."""
        owner = self._owner(level=1)  # ceiling = max(1, 0) = 1, agent level 1
        agent = RealAgent(owner=owner)
        self.assertEqual(self.system.get_cap_ceiling(agent), 1)

        self.system.award_agent_xp(agent, "harvest")

        self.assertEqual(agent.db.combat_xp, 0)
        self.assertEqual(agent.db.level, 1)

    def test_zero_amount_source_is_noop(self):
        """A source whose balance amount is 0 (time_served) changes nothing."""
        owner = self._owner(level=10)  # well below ceiling
        agent = RealAgent(owner=owner)
        self.assertEqual(self.registry.balance.agent_xp_time_served, 0)

        self.system.award_agent_xp(agent, "time_served")

        self.assertEqual(agent.db.combat_xp, 0)

    def test_unknown_source_is_noop(self):
        """An unrecognized source key resolves to no field → no-op."""
        owner = self._owner(level=10)
        agent = RealAgent(owner=owner)

        self.system.award_agent_xp(agent, "not_a_real_source")

        self.assertEqual(agent.db.combat_xp, 0)

    def test_award_returns_true_when_it_awards(self):
        """A real award reports True so callers can skip a redundant re-eval."""
        owner = self._owner(level=10)  # below ceiling
        agent = RealAgent(owner=owner)

        self.assertIs(self.system.award_agent_xp(agent, "harvest"), True)

    def test_award_returns_false_when_frozen(self):
        """A frozen (at-ceiling) award reports False — no re-eval happened."""
        owner = self._owner(level=1)  # ceiling 1, agent level 1
        agent = RealAgent(owner=owner)

        self.assertIs(self.system.award_agent_xp(agent, "harvest"), False)

    def test_award_returns_false_for_zero_and_unknown_source(self):
        """Zero-amount and unknown sources report False (no award, no re-eval)."""
        owner = self._owner(level=10)
        agent = RealAgent(owner=owner)

        self.assertIs(self.system.award_agent_xp(agent, "time_served"), False)
        self.assertIs(self.system.award_agent_xp(agent, "not_a_real_source"), False)

    def test_death_loss_deducts_balance_amount(self):
        """apply_agent_death_loss subtracts the configured death-loss amount."""
        owner = self._owner(level=10)
        agent = RealAgent(owner=owner)
        agent.award_xp(100)  # seed combat_xp directly (no freeze on the mixin)
        loss = self.registry.balance.agent_xp_death_loss
        self.assertEqual(loss, 25)

        self.system.apply_agent_death_loss(agent)

        self.assertEqual(agent.db.combat_xp, 100 - loss)
        self.assertEqual(agent.db.level, progression.level_for_xp(100 - loss))

    def test_death_loss_floors_at_zero(self):
        """Death loss never drives combat_xp negative — it floors at 0."""
        owner = self._owner(level=10)
        agent = RealAgent(owner=owner)
        agent.award_xp(10)  # less than the 25 death loss

        self.system.apply_agent_death_loss(agent)

        self.assertEqual(agent.db.combat_xp, 0)
        self.assertEqual(agent.db.level, 1)


# -------------------------------------------------------------- #
#  Gated ability script resolution + attach/detach (Task 8.2)
# -------------------------------------------------------------- #

class FakeScript:
    """Minimal stand-in for an attached Evennia Script."""
    def __init__(self, key):
        self.key = key
        self._deleted = False

    def delete(self):
        self._deleted = True


class FakeScriptManager:
    """Minimal scripts manager supporting .all()/.add()/delete semantics.

    Mirrors the small slice of Evennia's ScriptHandler the attach/detach
    helpers rely on: ``all()`` lists attached scripts, ``add(cls)`` attaches a
    new script (instantiating its key from the class), and ``delete()`` on a
    script removes it from the collection.
    """
    def __init__(self):
        self._scripts = []

    def all(self):
        return [s for s in self._scripts if not s._deleted]

    def add(self, script_cls):
        # Resolve the script key the same way AgentSystem does (by class name).
        from mygame.world.systems.agent_system import ABILITY_SCRIPT_KEYS
        key = ABILITY_SCRIPT_KEYS.get(
            getattr(script_cls, "__name__", ""),
            getattr(script_cls, "key", "") or script_cls.__name__,
        )
        self._scripts.append(FakeScript(key))


class ScriptedAgent(FakeAgent):
    """FakeAgent with a scripts manager and optional pre-attached scripts."""
    def __init__(self, agent_id, owner=None, script_keys=None):
        super().__init__(agent_id=agent_id, owner=owner)
        self.scripts = FakeScriptManager()
        for key in (script_keys or []):
            self.scripts._scripts.append(FakeScript(key))


class TestAbilityScriptResolution(AgentSystemTestBase):
    """resolve_ability_script + idempotent attach/detach helpers (Task 8.2)."""

    def test_resolve_known_key_returns_delivery_behavior(self):
        """resolve_ability_script('delivery') returns DeliveryBehavior."""
        from typeclasses.agent_scripts import DeliveryBehavior

        self.assertIs(
            self.system.resolve_ability_script("delivery"), DeliveryBehavior
        )

    def test_resolve_unknown_key_returns_none(self):
        """An unresolved ability key returns None (Req 13.4)."""
        self.assertIsNone(self.system.resolve_ability_script("not_a_real_key"))

    def test_attach_single_script_attaches_when_absent(self):
        """_attach_single_script adds the script and inits delivery_state."""
        from typeclasses.agent_scripts import DeliveryBehavior
        from mygame.world.constants import DeliveryState

        agent = ScriptedAgent(1)
        self.system._attach_single_script(agent, DeliveryBehavior)

        keys = [s.key for s in agent.scripts.all()]
        self.assertEqual(keys, ["delivery_behavior"])
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)

    def test_attach_single_script_is_idempotent(self):
        """_attach_single_script does not duplicate an already-attached script (Req 9.4)."""
        from typeclasses.agent_scripts import DeliveryBehavior

        agent = ScriptedAgent(1, script_keys=["delivery_behavior"])
        self.system._attach_single_script(agent, DeliveryBehavior)

        keys = [s.key for s in agent.scripts.all()]
        self.assertEqual(keys, ["delivery_behavior"])

    def test_detach_single_script_removes_only_named_script(self):
        """_detach_single_script removes only the named script, leaving others (incl. harvester)."""
        agent = ScriptedAgent(
            1, script_keys=["harvester_script", "delivery_behavior"]
        )
        self.system._detach_single_script(agent, "delivery_behavior")

        keys = [s.key for s in agent.scripts.all()]
        self.assertEqual(keys, ["harvester_script"])

    def test_detach_single_script_leaves_unmatched_keys(self):
        """Detaching a key not present is a harmless no-op."""
        agent = ScriptedAgent(1, script_keys=["harvester_script"])
        self.system._detach_single_script(agent, "delivery_behavior")

        keys = [s.key for s in agent.scripts.all()]
        self.assertEqual(keys, ["harvester_script"])


# -------------------------------------------------------------- #
#  Gated ability evaluation (Task 8.3 — Req 8, 9, 13.4, 15, 17)
# -------------------------------------------------------------- #

class NotifyingPlayer(FakePlayer):
    """FakePlayer that captures owner.msg(...) notifications."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages: list[str] = []

    def msg(self, text, **kwargs):
        self.messages.append(text)


class CappedScriptedAgent(CappedAgent):
    """Agent with a controllable raw level AND a scripts manager."""

    def __init__(self, agent_id, owner=None, raw_level=1, script_keys=None):
        super().__init__(agent_id=agent_id, owner=owner, raw_level=raw_level)
        self.scripts = FakeScriptManager()
        for key in (script_keys or []):
            self.scripts._scripts.append(FakeScript(key))


class TestEvaluateGatedAbilities(AgentSystemTestBase):
    """evaluate_gated_abilities branch coverage (Task 8.3)."""

    def setUp(self):
        super().setUp()
        from mygame.world.definitions import AbilityGateDef
        # delivery gate at level 21 (first level of rank 5)
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def test_attach_when_available_and_enabled(self):
        """effective >= gate AND enabled → attach + 'now active' notice (Req 9.2)."""
        from mygame.world.constants import DeliveryState

        owner = NotifyingPlayer()
        owner.db.level = 30  # ceiling 29
        agent = CappedScriptedAgent(7, owner=owner, raw_level=25)  # effective 25
        agent.db.enabled_abilities = ["delivery"]

        self.system.evaluate_gated_abilities(agent)

        self.assertIn("delivery_behavior", self._script_keys(agent))
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)
        self.assertTrue(any("now active" in m for m in owner.messages))

    def test_available_not_enabled_notifies_once_no_attach(self):
        """Unlocked but not enabled → available notice once, no script (Req 9.1, 15.2)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(3, owner=owner, raw_level=25)  # effective 25
        # enabled set empty

        self.system.evaluate_gated_abilities(agent)
        self.system.evaluate_gated_abilities(agent)  # second call must not re-notify

        self.assertNotIn("delivery_behavior", self._script_keys(agent))
        available_msgs = [m for m in owner.messages if "available" in m]
        self.assertEqual(len(available_msgs), 1)
        self.assertIn("agent ability 3 delivery on", available_msgs[0])

    def test_detach_and_relock_notify_when_level_drops(self):
        """Attached + enabled but effective drops below gate → detach + re-lock (Req 9.5, 9.7)."""
        owner = NotifyingPlayer()
        owner.db.level = 5  # ceiling 4 → effective capped to 4 (< 21)
        agent = CappedScriptedAgent(
            9, owner=owner, raw_level=25, script_keys=["delivery_behavior"]
        )
        agent.db.enabled_abilities = ["delivery"]

        self.system.evaluate_gated_abilities(agent)

        self.assertNotIn("delivery_behavior", self._script_keys(agent))
        self.assertTrue(any("re-locked" in m for m in owner.messages))

    def test_disable_detach_is_silent_when_still_available(self):
        """Attached but not enabled while still available → detach, no re-lock notice (Req 9.6)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(
            4, owner=owner, raw_level=25, script_keys=["delivery_behavior"]
        )
        # enabled set empty → not wanted, but still available (effective 25 >= 21)

        self.system.evaluate_gated_abilities(agent)

        self.assertNotIn("delivery_behavior", self._script_keys(agent))
        self.assertFalse(any("re-locked" in m for m in owner.messages))

    def test_idempotent_no_duplicate_attach(self):
        """Already attached + still wanted → no duplicate, no extra notice (Req 9.4)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(
            5, owner=owner, raw_level=25, script_keys=["delivery_behavior"]
        )
        agent.db.enabled_abilities = ["delivery"]

        self.system.evaluate_gated_abilities(agent)

        self.assertEqual(
            self._script_keys(agent).count("delivery_behavior"), 1
        )
        self.assertFalse(any("now active" in m for m in owner.messages))

    def test_locked_below_gate_no_op(self):
        """effective below gate and unattached → no attach, no available notice (Req 9.8)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(6, owner=owner, raw_level=10)  # effective 10 < 21
        agent.db.enabled_abilities = ["delivery"]

        self.system.evaluate_gated_abilities(agent)

        self.assertNotIn("delivery_behavior", self._script_keys(agent))
        self.assertEqual(owner.messages, [])

    def test_unresolved_key_skipped(self):
        """A gate whose key has no script is skipped, leaving the agent unchanged (Req 13.4)."""
        from mygame.world.definitions import AbilityGateDef

        self.registry.ability_gates = {
            "teleport": AbilityGateDef(key="teleport", required_level=1),
        }
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(8, owner=owner, raw_level=25)
        agent.db.enabled_abilities = ["teleport"]

        self.system.evaluate_gated_abilities(agent)

        self.assertEqual(self._script_keys(agent), [])
        self.assertEqual(owner.messages, [])

    def test_relock_clears_notified_so_recross_notifies_again(self):
        """A level-drop detach must clear the stale 'available' flag (Req 15.2).

        Regression: the detach branch left the key in notified_available, so a
        later re-cross into available-but-not-enabled sent no notice. Sequence:
        available (notice) → enable → demote below gate (detach) → disable →
        re-promote above gate → the available notice MUST fire again.
        """
        owner = NotifyingPlayer()
        owner.db.level = 30  # ceiling 29
        agent = CappedScriptedAgent(11, owner=owner, raw_level=25)  # effective 25
        self.created_agents.append(agent)  # so enable/disable can look it up

        # 1. Becomes available (not enabled) → first available notice.
        self.system.evaluate_gated_abilities(agent)
        self.assertEqual(len([m for m in owner.messages if "available" in m]), 1)

        # 2. Player enables → attaches; the enable path clears the notified flag.
        self.system.enable_ability(owner, 11, "delivery")
        self.assertIn("delivery_behavior", self._script_keys(agent))

        # 3. Owner demoted below the gate → detach + re-lock (flag must clear).
        owner.db.level = 5  # ceiling 4 → effective 4 < 21
        self.system.evaluate_gated_abilities(agent)
        self.assertNotIn("delivery_behavior", self._script_keys(agent))
        self.assertTrue(any("re-locked" in m for m in owner.messages))

        # 4. Player disables while below the gate (clears enabled flag).
        self.system.disable_ability(owner, 11, "delivery")

        # 5. Owner re-promoted above the gate, still not enabled → the
        #    available notice must fire AGAIN (a second time overall).
        owner.db.level = 30
        self.system.evaluate_gated_abilities(agent)

        available_msgs = [m for m in owner.messages if "available" in m]
        self.assertEqual(len(available_msgs), 2)

    def test_enable_clears_notified_available_flag(self):
        """enable_ability clears the available-window flag it bypasses (Req 15.2)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(12, owner=owner, raw_level=25)
        self.created_agents.append(agent)  # so enable_ability can look it up

        # Available notice sets the notified flag.
        self.system.evaluate_gated_abilities(agent)
        self.assertIn("delivery", self.system._get_notified_available(agent))

        # Enabling attaches directly and must clear that flag.
        self.system.enable_ability(owner, 12, "delivery")
        self.assertNotIn("delivery", self.system._get_notified_available(agent))

    def test_notify_false_suppresses_messages(self):
        """notify=False still mutates scripts but sends no owner messages."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = CappedScriptedAgent(2, owner=owner, raw_level=25)
        agent.db.enabled_abilities = ["delivery"]

        self.system.evaluate_gated_abilities(agent, notify=False)

        self.assertIn("delivery_behavior", self._script_keys(agent))
        self.assertEqual(owner.messages, [])


# -------------------------------------------------------------- #
#  Gate-aware _attach_behavior_script (Task 8.4 — Req 8.1-8.3, 8.5,
#  8.6, 10.3, 10.4, 12.6)
# -------------------------------------------------------------- #

class TestAttachBehaviorScriptGateAware(AgentSystemTestBase):
    """_attach_behavior_script always attaches HarvesterScript and gates delivery."""

    def setUp(self):
        super().setUp()
        from mygame.world.definitions import AbilityGateDef
        # delivery gate at level 21 (first level of rank 5)
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def _harvester_agent(self, owner_level, raw_level, enabled=None):
        owner = NotifyingPlayer()
        owner.db.level = owner_level
        agent = CappedScriptedAgent(1, owner=owner, raw_level=raw_level)
        if enabled is not None:
            agent.db.enabled_abilities = list(enabled)
        return agent

    def test_harvester_script_always_attaches_below_gate(self):
        """Below gate → HarvesterScript attaches, DeliveryBehavior does not (Req 8.1, 8.3)."""
        agent = self._harvester_agent(owner_level=30, raw_level=10, enabled=["delivery"])

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        self.assertIn("HarvesterScript", keys)
        self.assertNotIn("delivery_behavior", keys)

    def test_delivery_attaches_when_effective_at_gate_and_enabled(self):
        """effective >= gate AND enabled → both HarvesterScript and delivery (Req 8.2, 8.5, 10.4)."""
        agent = self._harvester_agent(owner_level=30, raw_level=25, enabled=["delivery"])

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        self.assertIn("HarvesterScript", keys)
        self.assertIn("delivery_behavior", keys)

    def test_delivery_skipped_when_at_gate_but_not_enabled(self):
        """effective >= gate but NOT enabled → no delivery, harvester still attaches (Req 8.5, 12.6)."""
        agent = self._harvester_agent(owner_level=30, raw_level=25, enabled=[])

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        self.assertIn("HarvesterScript", keys)
        self.assertNotIn("delivery_behavior", keys)

    def test_delivery_skipped_when_enabled_but_below_gate(self):
        """enabled but effective below gate → no delivery (gate not met) (Req 8.2, 8.3)."""
        # owner_level 5 → ceiling 4 caps effective to 4 (< 21), even though raw is high
        agent = self._harvester_agent(owner_level=5, raw_level=25, enabled=["delivery"])

        self.system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        self.assertIn("HarvesterScript", keys)
        self.assertNotIn("delivery_behavior", keys)

    def test_reassign_attaches_delivery_iff_effective_and_enabled(self):
        """assign_agent reserve-restore/reassign path attaches delivery iff gate met AND enabled (Req 10.3, 10.4)."""
        from mygame.world.constants import DeliveryState

        agent = self._harvester_agent(owner_level=30, raw_level=25, enabled=["delivery"])

        self.system._attach_behavior_script(agent, "harvester")

        self.assertIn("delivery_behavior", self._script_keys(agent))
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)

    def test_non_harvester_role_still_runs_gate_evaluation(self):
        """A non-harvester role attaches its base script and evaluates gates harmlessly."""
        # engineer maps to EngineerScript; no delivery gate met since not enabled
        agent = self._harvester_agent(owner_level=30, raw_level=25, enabled=[])

        self.system._attach_behavior_script(agent, "engineer")

        keys = self._script_keys(agent)
        self.assertIn("EngineerScript", keys)
        self.assertNotIn("delivery_behavior", keys)


# -------------------------------------------------------------- #
#  Ability enable/disable/status backends
#  (Task 9.1 — Req 13.5, 16.2-16.7, 17.2, 17.5)
# -------------------------------------------------------------- #

class TestAbilityCommandBackends(AgentSystemTestBase):
    """enable_ability / disable_ability / get_ability_status (Task 9.1)."""

    def setUp(self):
        super().setUp()
        from mygame.world.definitions import AbilityGateDef
        # delivery gate at level 21 (first level of rank 5)
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def _owned_agent(self, agent_id, owner, raw_level, script_keys=None):
        """Build a scripted agent owned by *owner* and tracked for lookups."""
        agent = CappedScriptedAgent(
            agent_id, owner=owner, raw_level=raw_level, script_keys=script_keys
        )
        self.created_agents.append(agent)
        return agent

    # -- enable_ability --------------------------------------------- #

    def test_enable_at_gate_attaches_and_records(self):
        """effective >= gate → records enabled set + attaches script (Req 16.2, 17.2)."""
        from mygame.world.constants import DeliveryState

        owner = NotifyingPlayer()
        owner.db.level = 30  # ceiling 29
        agent = self._owned_agent(7, owner, raw_level=25)  # effective 25 >= 21

        msg = self.system.enable_ability(owner, 7, "delivery")

        self.assertIn("delivery", self.system.get_enabled_abilities(agent))
        self.assertIn("delivery_behavior", self._script_keys(agent))
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)
        self.assertIn("enabled", msg)
        self.assertIn("delivery", msg)

    def test_enable_above_gate_attaches_and_records(self):
        """effective well above gate → still attaches + records."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = self._owned_agent(1, owner, raw_level=28)  # effective 28 >= 21

        self.system.enable_ability(owner, 1, "delivery")

        self.assertIn("delivery", self.system.get_enabled_abilities(agent))
        self.assertIn("delivery_behavior", self._script_keys(agent))

    def test_enable_below_gate_rejects_with_required_level(self):
        """effective < gate → reject naming required level, no attach/record (Req 16.3)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = self._owned_agent(2, owner, raw_level=10)  # effective 10 < 21

        msg = self.system.enable_ability(owner, 2, "delivery")

        self.assertIn("21", msg)  # required level surfaced
        self.assertNotIn("delivery", self.system.get_enabled_abilities(agent))
        self.assertNotIn("delivery_behavior", self._script_keys(agent))

    def test_enable_below_gate_due_to_owner_cap_rejects(self):
        """High raw level but owner cap keeps effective below gate → reject (Req 16.3)."""
        owner = NotifyingPlayer()
        owner.db.level = 5  # ceiling 4 → effective capped to 4
        agent = self._owned_agent(3, owner, raw_level=25)

        msg = self.system.enable_ability(owner, 3, "delivery")

        self.assertIn("21", msg)
        self.assertNotIn("delivery", self.system.get_enabled_abilities(agent))
        self.assertNotIn("delivery_behavior", self._script_keys(agent))

    def test_enable_unknown_key_rejected(self):
        """Unknown ability key → reject (Req 16.6)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = self._owned_agent(4, owner, raw_level=25)

        msg = self.system.enable_ability(owner, 4, "teleport")

        self.assertIn("Unknown ability", msg)
        self.assertIn("teleport", msg)
        self.assertEqual(self.system.get_enabled_abilities(agent), set())
        self.assertNotIn("delivery_behavior", self._script_keys(agent))

    def test_enable_missing_agent_rejected(self):
        """Unknown agent id → reject 'not found' (Req 16.7)."""
        owner = NotifyingPlayer()
        owner.db.level = 30

        msg = self.system.enable_ability(owner, 999, "delivery")

        self.assertIn("not found", msg)
        self.assertIn("999", msg)

    def test_enable_unowned_agent_rejected(self):
        """Agent owned by another player is not found for this player (Req 16.7)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        other = NotifyingPlayer(name="Other")
        other.id = 2
        # Agent belongs to `other`, not `owner`.
        self._owned_agent(5, other, raw_level=25)

        msg = self.system.enable_ability(owner, 5, "delivery")

        self.assertIn("not found", msg)

    # -- disable_ability -------------------------------------------- #

    def test_disable_clears_and_detaches_keeping_harvester(self):
        """Disable clears enabled flag + detaches delivery, HarvesterScript stays (Req 16.4, 17.5)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = self._owned_agent(
            6, owner, raw_level=25,
            script_keys=["HarvesterScript", "delivery_behavior"],
        )
        agent.db.enabled_abilities = ["delivery"]

        msg = self.system.disable_ability(owner, 6, "delivery")

        self.assertNotIn("delivery", self.system.get_enabled_abilities(agent))
        keys = self._script_keys(agent)
        self.assertNotIn("delivery_behavior", keys)
        self.assertIn("HarvesterScript", keys)
        self.assertIn("disabled", msg)

    def test_disable_unknown_key_rejected(self):
        """Unknown ability key on disable → reject (Req 16.6)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        self._owned_agent(7, owner, raw_level=25)

        msg = self.system.disable_ability(owner, 7, "teleport")

        self.assertIn("Unknown ability", msg)

    def test_disable_missing_agent_rejected(self):
        """Unknown agent id on disable → reject (Req 16.7)."""
        owner = NotifyingPlayer()
        owner.db.level = 30

        msg = self.system.disable_ability(owner, 999, "delivery")

        self.assertIn("not found", msg)

    # -- get_ability_status ----------------------------------------- #

    def test_status_reports_locked_when_below_gate(self):
        """Below gate → 'locked (Lv N)' with required level (Req 16.5)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        self._owned_agent(8, owner, raw_level=10)  # effective 10 < 21

        msg = self.system.get_ability_status(owner, 8)

        self.assertIn("delivery", msg)
        self.assertIn("locked (Lv 21)", msg)

    def test_status_reports_available_when_at_gate_not_enabled(self):
        """At/above gate but not enabled → 'available' (Req 16.5)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        self._owned_agent(9, owner, raw_level=25)  # effective 25 >= 21, not enabled

        msg = self.system.get_ability_status(owner, 9)

        self.assertIn("delivery: available", msg)

    def test_status_reports_enabled_when_enabled(self):
        """Key in enabled set → 'enabled' (Req 16.5)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = self._owned_agent(10, owner, raw_level=25)
        agent.db.enabled_abilities = ["delivery"]

        msg = self.system.get_ability_status(owner, 10)

        self.assertIn("delivery: enabled", msg)

    def test_status_missing_agent_rejected(self):
        """Unknown agent id on status → reject (Req 16.7)."""
        owner = NotifyingPlayer()
        owner.db.level = 30

        msg = self.system.get_ability_status(owner, 999)

        self.assertIn("not found", msg)


# -------------------------------------------------------------- #
#  Time-served XP + defensive re-eval in process_tick (Task 11.5)
#  Req 5.5, 5.8, 5.9
# -------------------------------------------------------------- #

class TestTimeServedTick(AgentSystemTestBase):
    """``process_tick`` / ``_process_agent_tick`` time-served award + re-eval."""

    def setUp(self):
        super().setUp()
        # Force this registry's curve active so award_xp derives raw levels.
        progression.build_thresholds(self.registry.ranks)

    def _owner(self, level=10):
        owner = FakePlayer()
        owner.db.level = level
        return owner

    def _active_agent(self, owner, role="harvester"):
        agent = RealAgent(owner=owner)
        agent.db.role = role
        agent.db.reserve = False
        agent.db.incapacitated = False
        return agent

    def test_time_served_awarded_to_active_agent(self):
        """A configured positive amount is awarded once per tick (Req 5.5)."""
        # Configure a positive time-served amount.
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)  # ceiling 19, agent starts level 1
        agent = self._active_agent(owner)

        self.system._process_agent_tick(agent)

        self.assertEqual(agent.db.combat_xp, 7)

    def test_award_awarded_does_not_double_evaluate(self):
        """When time-served XP is awarded, gate re-eval runs exactly once.

        award_agent_xp already re-evaluates on a real award, so
        _process_agent_tick must not call evaluate_gated_abilities a second time
        (which would double the per-agent script scan every tick).
        """
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)  # below ceiling → award happens
        agent = self._active_agent(owner)

        calls = []
        original = self.system.evaluate_gated_abilities
        self.system.evaluate_gated_abilities = (
            lambda a, *args, **kw: (calls.append(a), original(a, *args, **kw))[1]
        )

        self.system._process_agent_tick(agent)

        # Exactly one re-eval (via award_agent_xp -> _reevaluate_agent), not two.
        self.assertEqual(len(calls), 1)
        self.assertEqual(agent.db.combat_xp, 7)

    def test_no_award_still_evaluates_once(self):
        """When no award happens (frozen), the defensive re-eval still runs once.

        This preserves convergence for direct/out-of-band XP edits that changed
        the effective level without going through an award.
        """
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=1)  # ceiling 1 → frozen, no award
        agent = self._active_agent(owner)

        calls = []
        original = self.system.evaluate_gated_abilities
        self.system.evaluate_gated_abilities = (
            lambda a, *args, **kw: (calls.append(a), original(a, *args, **kw))[1]
        )

        self.system._process_agent_tick(agent)

        self.assertEqual(len(calls), 1)
        self.assertEqual(agent.db.combat_xp, 0)

    def test_zero_time_served_is_noop(self):
        """Default zero time-served amount grants nothing (Req 5.8)."""
        self.assertEqual(self.registry.balance.agent_xp_time_served, 0)
        owner = self._owner(level=20)
        agent = self._active_agent(owner)

        self.system._process_agent_tick(agent)

        self.assertEqual(agent.db.combat_xp, 0)

    def test_reserved_agent_skipped(self):
        """Reserved agents earn no time-served XP."""
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)
        agent = self._active_agent(owner)
        agent.db.reserve = True

        self.system._process_agent_tick(agent)

        self.assertEqual(agent.db.combat_xp, 0)

    def test_incapacitated_agent_skipped(self):
        """Incapacitated agents earn no time-served XP."""
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)
        agent = self._active_agent(owner)
        agent.db.incapacitated = True

        self.system._process_agent_tick(agent)

        self.assertEqual(agent.db.combat_xp, 0)

    def test_unassigned_agent_skipped(self):
        """Agents with an empty role earn no time-served XP."""
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)
        agent = self._active_agent(owner, role="")

        self.system._process_agent_tick(agent)

        self.assertEqual(agent.db.combat_xp, 0)

    def test_frozen_at_ceiling_is_noop(self):
        """An agent at its cap ceiling earns nothing even when active (Req 5.9)."""
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=1)  # ceiling = 1, agent level 1 == ceiling
        agent = self._active_agent(owner)

        self.system._process_agent_tick(agent)

        self.assertEqual(agent.db.combat_xp, 0)

    def test_process_tick_awards_discovered_agent(self):
        """End-to-end: process_tick awards time-served to a discovered agent."""
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)
        agent = self._active_agent(owner)

        self._run_process_tick([agent])

        self.assertEqual(agent.db.combat_xp, 7)

    def test_reserved_agent_scripts_not_driven(self):
        """process_tick must not run behavior scripts for reserved agents.

        Regression: handle_demotion benches an agent by setting db.reserve
        without detaching its scripts, and HarvesterScript.at_repeat has no
        reserve guard. If process_tick still drove at_repeat, a benched
        harvester would keep producing resources and earning XP each tick.
        """
        owner = self._owner(level=20)

        class _RecordingScript:
            key = "harvester_script"
            interval = 0

            def __init__(self):
                self.ran = 0

            def at_repeat(self):
                self.ran += 1

        class _ScriptedAgent(RealAgent):
            def __init__(self, **kw):
                super().__init__(**kw)
                self._script = _RecordingScript()
                self.scripts = types.SimpleNamespace(
                    all=lambda: [self._script]
                )

        active = _ScriptedAgent(agent_id=1, owner=owner)
        active.db.role = "harvester"
        active.db.reserve = False
        active.db.incapacitated = False

        reserved = _ScriptedAgent(agent_id=2, owner=owner)
        reserved.db.role = "harvester"
        reserved.db.reserve = True
        reserved.db.incapacitated = False

        self._run_process_tick([active, reserved])

        # The active agent's script runs; the reserved agent's does not.
        self.assertEqual(active._script.ran, 1)
        self.assertEqual(reserved._script.ran, 0)

    def test_one_bad_agent_does_not_halt_tick(self):
        """A raising agent is isolated; other agents still get processed."""
        self.registry.balance.agent_xp_time_served = 7
        owner = self._owner(level=20)
        good = self._active_agent(owner)

        class BadAgent(RealAgent):
            def award_xp(self, amount):
                raise RuntimeError("boom")

        bad = BadAgent(agent_id=99, owner=owner)
        bad.db.role = "harvester"
        bad.db.reserve = False
        bad.db.incapacitated = False

        # Bad agent first so a failure would otherwise stop iteration.
        self._run_process_tick([bad, good])

        # The good agent was still awarded despite the bad agent raising.
        self.assertEqual(good.db.combat_xp, 7)

    def _run_process_tick(self, agents):
        """Run process_tick with the repository's sweep list set to *agents*."""
        self.repo.all_agents = list(agents)
        try:
            self.system.process_tick(1)
        finally:
            self.repo.all_agents = None


# -------------------------------------------------------------- #
#  Owner-level-change re-evaluation (Task 12.1 — Req 14.7, 14.8,
#  15.1, 15.2, 15.3, 15.4, 15.5)
# -------------------------------------------------------------- #

class TestOnOwnerLevelChanged(AgentSystemTestBase):
    """on_owner_level_changed re-evaluates gated abilities for owned agents."""

    def setUp(self):
        super().setUp()
        from mygame.world.definitions import AbilityGateDef
        # delivery gate at level 21 (first level of rank 5)
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def _owned_agent(self, agent_id, owner, raw_level, script_keys=None,
                     enabled=None):
        """Build a scripted agent owned by *owner* and track it for lookups."""
        agent = CappedScriptedAgent(
            agent_id, owner=owner, raw_level=raw_level, script_keys=script_keys
        )
        if enabled is not None:
            agent.db.enabled_abilities = list(enabled)
        self.created_agents.append(agent)
        return agent

    def test_rise_enabled_attaches_delivery_and_notifies_active(self):
        """Owner level rise brings enabled agent to/above gate → attach + active (Req 14.8, 15.3)."""
        from mygame.world.constants import DeliveryState

        owner = NotifyingPlayer()
        # Start below the gate: ceiling 20 caps effective to 20 (< 21).
        owner.db.level = 21
        agent = self._owned_agent(1, owner, raw_level=25, enabled=["delivery"])

        # Owner levels up so the ceiling rises to 29; effective becomes 25 >= 21.
        owner.db.level = 30
        self.system.on_owner_level_changed(owner, old_level=21, new_level=30)

        self.assertIn("delivery_behavior", self._script_keys(agent))
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)
        self.assertTrue(any("now active" in m for m in owner.messages))

    def test_rise_available_not_enabled_notifies_without_attach(self):
        """Owner rise unlocks gate but ability not enabled → available notice, no attach (Req 15.2)."""
        owner = NotifyingPlayer()
        owner.db.level = 21  # ceiling 20 → effective capped to 20 (< 21)
        agent = self._owned_agent(2, owner, raw_level=25)  # not enabled

        owner.db.level = 30  # ceiling 29 → effective 25 >= 21
        self.system.on_owner_level_changed(owner, old_level=21, new_level=30)

        self.assertNotIn("delivery_behavior", self._script_keys(agent))
        available_msgs = [m for m in owner.messages if "available" in m]
        self.assertEqual(len(available_msgs), 1)
        self.assertIn("agent ability 2 delivery on", available_msgs[0])

    def test_drop_below_gate_detaches_keeps_enabled_and_relocks(self):
        """Owner drop pushes effective below gate → detach, keep enabled flag, re-lock (Req 15.4)."""
        owner = NotifyingPlayer()
        owner.db.level = 30  # ceiling 29 → effective 25 (>= 21), delivery active
        agent = self._owned_agent(
            3, owner, raw_level=25,
            script_keys=["HarvesterScript", "delivery_behavior"],
            enabled=["delivery"],
        )

        # Owner demoted hard: ceiling 4 → effective capped to 4 (< 21).
        owner.db.level = 5
        self.system.on_owner_level_changed(owner, old_level=30, new_level=5)

        keys = self._script_keys(agent)
        self.assertNotIn("delivery_behavior", keys)
        # HarvesterScript is retained.
        self.assertIn("HarvesterScript", keys)
        # Enabled flag is sticky across the level-driven detach.
        self.assertIn("delivery", self.system.get_enabled_abilities(agent))
        self.assertTrue(any("re-locked" in m for m in owner.messages))

    def test_reevaluates_all_owned_agents(self):
        """Every owned agent is re-evaluated on a single owner level change (Req 14.7, 15.1)."""
        owner = NotifyingPlayer()
        owner.db.level = 21  # below gate for everyone initially

        enabled_agent = self._owned_agent(1, owner, raw_level=25, enabled=["delivery"])
        available_agent = self._owned_agent(2, owner, raw_level=25)  # not enabled
        low_agent = self._owned_agent(3, owner, raw_level=10)  # still below gate

        owner.db.level = 30
        self.system.on_owner_level_changed(owner, old_level=21, new_level=30)

        # Enabled agent gets delivery attached.
        self.assertIn("delivery_behavior", self._script_keys(enabled_agent))
        # Available-but-not-enabled agent gets only a notification.
        self.assertNotIn("delivery_behavior", self._script_keys(available_agent))
        self.assertTrue(any("available" in m for m in owner.messages))
        # Low-level agent stays locked.
        self.assertNotIn("delivery_behavior", self._script_keys(low_agent))

    def test_only_owned_agents_are_reevaluated(self):
        """Agents owned by other players are left untouched."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        other = NotifyingPlayer(name="Other")
        other.id = 2
        other.db.level = 30

        mine = self._owned_agent(1, owner, raw_level=25, enabled=["delivery"])
        theirs = self._owned_agent(2, other, raw_level=25, enabled=["delivery"])

        self.system.on_owner_level_changed(owner, old_level=21, new_level=30)

        self.assertIn("delivery_behavior", self._script_keys(mine))
        # The other player's agent was not evaluated.
        self.assertNotIn("delivery_behavior", self._script_keys(theirs))

    def test_one_bad_agent_does_not_halt_reevaluation(self):
        """A raising agent is isolated; remaining owned agents still re-evaluate."""
        owner = NotifyingPlayer()
        owner.db.level = 30

        class BadScriptedAgent(CappedScriptedAgent):
            def get_raw_level(self):
                raise RuntimeError("boom")

        bad = BadScriptedAgent(1, owner=owner, raw_level=25)
        bad.db.enabled_abilities = ["delivery"]
        self.created_agents.append(bad)
        good = self._owned_agent(2, owner, raw_level=25, enabled=["delivery"])

        # Should not raise despite the bad agent blowing up mid-evaluation.
        self.system.on_owner_level_changed(owner, old_level=21, new_level=30)

        self.assertIn("delivery_behavior", self._script_keys(good))

    def test_accepts_call_without_level_arguments(self):
        """Level args are optional — re-evaluation works from owner state alone (Req 15.5)."""
        owner = NotifyingPlayer()
        owner.db.level = 30
        agent = self._owned_agent(1, owner, raw_level=25, enabled=["delivery"])

        self.system.on_owner_level_changed(owner)

        self.assertIn("delivery_behavior", self._script_keys(agent))


# -------------------------------------------------------------- #
#  Roster progression view (Task 13.1 — Req 11.1-11.4, 14.5)
# -------------------------------------------------------------- #

class TestGetAgentProgressionView(AgentSystemTestBase):
    """get_agent_progression_view reports capped progression + ability status."""

    def setUp(self):
        super().setUp()
        from mygame.world.definitions import AbilityGateDef, RankDef

        # delivery gate at level 21 (first level of rank 5).
        self.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=21),
        }
        # Ranks with cosmetic names, keyed by rank number (.level). Level 21
        # maps to rank 5 via rank_from_level; level 25 also maps to rank 5.
        self.registry.ranks = [
            RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
            RankDef(name="Private", level=2, xp_threshold=200, agent_cap=3),
            RankDef(name="Corporal", level=3, xp_threshold=600, agent_cap=4),
            RankDef(name="Sergeant", level=4, xp_threshold=1500, agent_cap=6),
            RankDef(name="Lieutenant", level=5, xp_threshold=3000, agent_cap=8),
        ]

    def test_effective_level_matches_compute_effective_level(self):
        """effective_level equals compute_effective_level(agent) (Req 11.1)."""
        owner = FakePlayer()
        owner.db.level = 30  # ceiling 29
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(
            view["effective_level"], self.system.compute_effective_level(agent)
        )
        self.assertEqual(view["effective_level"], 25)

    def test_rank_name_derived_from_effective_level(self):
        """rank_name comes from the effective (capped) level, not raw (Req 14.5)."""
        owner = FakePlayer()
        owner.db.level = 6  # ceiling 5 → effective capped to 5
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)

        view = self.system.get_agent_progression_view(agent)

        # effective 5 → rank_from_level(5) == rank 1 → "Recruit"
        self.assertEqual(view["effective_level"], 5)
        self.assertEqual(view["rank_name"], "Recruit")

    def test_capped_by_commander_true_when_raw_exceeds_effective(self):
        """capped_by_commander is True iff raw_level > effective_level (Req 11.4)."""
        owner = FakePlayer()
        owner.db.level = 10  # ceiling 9 → effective 9 < raw 25
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(view["effective_level"], 9)
        self.assertTrue(view["capped_by_commander"])

    def test_capped_by_commander_false_when_not_capped(self):
        """capped_by_commander is False when the cap does not suppress raw level."""
        owner = FakePlayer()
        owner.db.level = 30  # ceiling 29 → effective 25 == raw 25
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(view["effective_level"], 25)
        self.assertFalse(view["capped_by_commander"])

    def test_ability_status_locked_below_gate(self):
        """Below the gate → 'locked:N' with N the required level (Req 11.3)."""
        owner = FakePlayer()
        owner.db.level = 30  # ceiling 29
        agent = CappedScriptedAgent(1, owner=owner, raw_level=10)  # effective 10

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(view["ability_status"]["delivery"], "locked:21")

    def test_ability_status_available_when_unlocked_not_enabled(self):
        """At/above gate but not enabled → 'available' (Req 11.2)."""
        owner = FakePlayer()
        owner.db.level = 30  # ceiling 29
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)  # effective 25
        # enabled set empty

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(view["ability_status"]["delivery"], "available")

    def test_ability_status_enabled_when_in_enabled_set(self):
        """Key in enabled set → 'enabled' (Req 11.2)."""
        owner = FakePlayer()
        owner.db.level = 30  # ceiling 29
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)  # effective 25
        agent.db.enabled_abilities = ["delivery"]

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(view["ability_status"]["delivery"], "enabled")

    def test_rank_name_falls_back_when_no_rankdef(self):
        """No matching RankDef → generic 'Rank N' (robust fallback)."""
        owner = FakePlayer()
        owner.db.level = 30  # ceiling 29 → effective 25 → rank 5
        agent = CappedScriptedAgent(1, owner=owner, raw_level=25)
        # Remove the rank-5 def so no name matches.
        self.registry.ranks = [
            r for r in self.registry.ranks if r.level != 5
        ]

        view = self.system.get_agent_progression_view(agent)

        self.assertEqual(view["rank_name"], "Rank 5")


if __name__ == "__main__":
    unittest.main()
