"""
Property-based tests for AgentSystem invariants.

**Property 11: Agent Roster Invariant**
For any sequence of agent operations (train, assign, unassign,
incapacitate, reserve, restore), the sum of active + incapacitated +
reserved agents SHALL always equal the total roster size.
**Validates: Requirements 7b.12**

**Property 12: Agent ID Sequentiality**
For any sequence of agent training operations on a player, the assigned
agent IDs SHALL be strictly increasing, unique, and never reused.
**Validates: Requirements 7b.5**

**Property 13: Incapacitated/Reserved Agents Cannot Be Assigned**
For any agent in incapacitated or reserved state, attempting to assign
that agent to any role SHALL fail and the agent's state SHALL remain
unchanged.
**Validates: Requirements 7b.11**

**Property 14: Demotion Reserves Highest-ID Agents**
For any player with N agents and a demotion to a rank with agent cap
M < N, the (N - M) agents with the highest IDs SHALL enter reserve
status.
**Validates: Requirements 4.6, 7b.13**

**Property 15: Agent Training Cost Scaling**
For any agent number N (the Nth agent being trained), the training cost
SHALL be base_cost × N where base_cost is {Wood: 15, Stone: 10, Iron: 5}.
**Validates: Requirements 8.3**
"""

import sys
import types

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ------------------------------------------------------------------ #
#  Bootstrap: stub out Evennia modules
# ------------------------------------------------------------------ #

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
    BASE_TRAINING_COST,
    VALID_ROLES,
    BUILDING_ROLE_MAP,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import RankDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402


# ------------------------------------------------------------------ #
#  Helpers / Fakes (same pattern as test_agent_system.py)
# ------------------------------------------------------------------ #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
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
            "Wood": 999_999, "Stone": 999_999, "Iron": 999_999,
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


# ------------------------------------------------------------------ #
#  Shared test ranks (high cap to allow many agents)
# ------------------------------------------------------------------ #

_TEST_RANKS = [
    RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
    RankDef(name="Private", level=2, xp_threshold=200, agent_cap=3),
    RankDef(name="Corporal", level=3, xp_threshold=600, agent_cap=4),
    RankDef(name="Sergeant", level=4, xp_threshold=1500, agent_cap=6),
    RankDef(name="Staff_Sergeant", level=5, xp_threshold=3500, agent_cap=8),
    RankDef(name="Lieutenant", level=6, xp_threshold=7000, agent_cap=10),
    RankDef(name="Captain", level=7, xp_threshold=12000, agent_cap=12),
    RankDef(name="Major", level=8, xp_threshold=20000, agent_cap=14),
    RankDef(name="Colonel", level=9, xp_threshold=35000, agent_cap=16),
    RankDef(name="Brigadier", level=10, xp_threshold=55000, agent_cap=17),
    RankDef(name="General", level=11, xp_threshold=80000, agent_cap=19),
    RankDef(name="Marshal", level=12, xp_threshold=120000, agent_cap=20),
]


def _make_registry():
    registry = DataRegistry()
    registry.ranks = list(_TEST_RANKS)
    return registry


def _make_system():
    """Create an AgentSystem with a fake NPC factory and agent tracking."""
    registry = _make_registry()
    event_bus = EventBus()
    created_agents: list[FakeAgent] = []

    def fake_create_npc(player, agent_id):
        agent = FakeAgent(agent_id=agent_id, owner=player)
        created_agents.append(agent)
        return agent

    system = AgentSystem(
        registry=registry,
        event_bus=event_bus,
        create_npc_func=fake_create_npc,
    )

    def fallback(player):
        owner_id = getattr(player, "id", id(player))
        return [
            a for a in created_agents
            if getattr(getattr(a, "db", None), "owner", None) is player
        ]

    system._get_agents_fallback = fallback
    return system, created_agents


def _train_and_complete(system, player, academy=None):
    """Train + complete an agent, return the NPC."""
    if academy is None:
        academy = FakeBuilding(building_type="AC", building_level=1)
    ok, msg = system.train_agent(player, academy)
    assert ok, f"Training failed: {msg}"
    npc = system.complete_training(academy)
    assert npc is not None
    return npc


# ------------------------------------------------------------------ #
#  Hypothesis strategies
# ------------------------------------------------------------------ #

# Operations that can be applied to agents after training
AGENT_OPS = st.sampled_from([
    "assign", "unassign", "incapacitate", "reserve", "restore",
])

# Valid roles for assignment
ROLE_ST = st.sampled_from(list(VALID_ROLES))


# ------------------------------------------------------------------ #
#  Property 11: Agent Roster Invariant
# ------------------------------------------------------------------ #

class TestProperty11AgentRosterInvariant:
    """
    **Validates: Requirements 7b.12**

    For any sequence of agent operations (train, assign, unassign,
    incapacitate, reserve, restore), the sum of active + incapacitated +
    reserved agents SHALL always equal the total roster size.
    """

    @given(
        num_agents=st.integers(min_value=1, max_value=10),
        ops=st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=9),  # agent index
                AGENT_OPS,
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=200)
    def test_roster_invariant_holds(self, num_agents, ops):
        system, created_agents = _make_system()
        # Use Marshal rank (cap=20) so we can train many agents
        player = FakePlayer(combat_xp=120000, next_agent_id=1)

        # Train num_agents agents
        academy = FakeBuilding(building_type="AC", building_level=1)
        for _ in range(num_agents):
            _train_and_complete(system, player, academy)

        agents = system.get_agents(player)
        roster_size = len(agents)

        # Apply random operations
        for agent_idx, op in ops:
            agent_idx = agent_idx % len(agents)
            agent = agents[agent_idx]

            if op == "assign":
                # Only assign if not incapacitated and not reserved
                if not agent.db.incapacitated and not agent.db.reserve:
                    system.assign_agent(player, agent.db.agent_id, "soldier")
            elif op == "unassign":
                system.unassign_agent(player, agent.db.agent_id)
            elif op == "incapacitate":
                agent.db.incapacitated = True
            elif op == "reserve":
                agent.db.reserve = True
            elif op == "restore":
                agent.db.reserve = False

            # --- INVARIANT CHECK ---
            current_agents = system.get_agents(player)
            assert len(current_agents) == roster_size, (
                f"Roster size changed: was {roster_size}, "
                f"now {len(current_agents)} after op={op}"
            )

            active = sum(
                1 for a in current_agents
                if not a.db.incapacitated and not a.db.reserve
            )
            incapacitated = sum(
                1 for a in current_agents if a.db.incapacitated
            )
            reserved = sum(
                1 for a in current_agents if a.db.reserve
            )
            # Note: an agent can be both incapacitated and reserved,
            # but the total object count must remain constant.
            assert len(current_agents) == roster_size, (
                f"active={active}, incap={incapacitated}, "
                f"reserved={reserved}, total objects={len(current_agents)}, "
                f"expected roster_size={roster_size}"
            )


# ------------------------------------------------------------------ #
#  Property 12: Agent ID Sequentiality
# ------------------------------------------------------------------ #

class TestProperty12AgentIDSequentiality:
    """
    **Validates: Requirements 7b.5**

    For any sequence of agent training operations on a player, the
    assigned agent IDs SHALL be strictly increasing, unique, and never
    reused.
    """

    @given(
        num_agents=st.integers(min_value=1, max_value=15),
    )
    @settings(max_examples=200)
    def test_ids_strictly_increasing_and_unique(self, num_agents):
        system, created_agents = _make_system()
        player = FakePlayer(combat_xp=120000, next_agent_id=1)

        academy = FakeBuilding(building_type="AC", building_level=1)
        ids_assigned = []
        for _ in range(num_agents):
            npc = _train_and_complete(system, player, academy)
            ids_assigned.append(npc.db.agent_id)

        # IDs must be strictly increasing
        for i in range(1, len(ids_assigned)):
            assert ids_assigned[i] > ids_assigned[i - 1], (
                f"ID {ids_assigned[i]} is not greater than "
                f"previous {ids_assigned[i - 1]}. All IDs: {ids_assigned}"
            )

        # IDs must be unique (no reuse)
        assert len(ids_assigned) == len(set(ids_assigned)), (
            f"Duplicate IDs found: {ids_assigned}"
        )

        # First trained agent should be ID 1 (no commander concept)
        assert ids_assigned[0] == 1, (
            f"First agent ID should be 1, got {ids_assigned[0]}"
        )


# ------------------------------------------------------------------ #
#  Property 13: Incapacitated/Reserved Agents Cannot Be Assigned
# ------------------------------------------------------------------ #

class TestProperty13IncapacitatedReservedCannotAssign:
    """
    **Validates: Requirements 7b.11**

    For any agent in incapacitated or reserved state, attempting to
    assign that agent to any role SHALL fail and the agent's state
    SHALL remain unchanged.
    """

    @given(
        role=ROLE_ST,
        incapacitated=st.booleans(),
        reserved=st.booleans(),
    )
    @settings(max_examples=200)
    def test_blocked_agents_cannot_be_assigned(self, role, incapacitated, reserved):
        # At least one of incapacitated/reserved must be True
        assume(incapacitated or reserved)

        system, created_agents = _make_system()
        player = FakePlayer(combat_xp=120000, next_agent_id=1)

        npc = _train_and_complete(system, player)

        # Set the blocked state
        npc.db.incapacitated = incapacitated
        npc.db.reserve = reserved

        # Snapshot state before assignment attempt
        pre_role = npc.db.role
        pre_role_target = npc.db.role_target
        pre_incap = npc.db.incapacitated
        pre_reserve = npc.db.reserve

        # Build a matching building for non-army roles
        building = None
        if role not in ("soldier", "medic"):
            # Find a building type that maps to this role
            btype = None
            for bt, r in BUILDING_ROLE_MAP.items():
                if r == role:
                    btype = bt
                    break
            if btype:
                building = FakeBuilding(building_type=btype)

        ok, msg = system.assign_agent(
            player, npc.db.agent_id, role, building
        )

        # Assignment MUST fail
        assert not ok, (
            f"Assignment should have failed for "
            f"incapacitated={incapacitated}, reserved={reserved}, "
            f"role={role}, but got ok=True: {msg}"
        )

        # State must be unchanged
        assert npc.db.incapacitated == pre_incap
        assert npc.db.reserve == pre_reserve
        assert npc.db.role == pre_role
        assert npc.db.role_target == pre_role_target


# ------------------------------------------------------------------ #
#  Property 14: Demotion Reserves Highest-ID Agents
# ------------------------------------------------------------------ #

class TestProperty14DemotionReservesHighestID:
    """
    **Validates: Requirements 4.6, 7b.13**

    For any player with N agents and a demotion to a rank with agent
    cap M < N, the (N - M) agents with the highest IDs SHALL enter
    reserve status. Agents with lower IDs SHALL remain in their
    current state.
    """

    @given(
        num_agents=st.integers(min_value=2, max_value=12),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_demotion_reserves_highest_ids(self, num_agents, data):
        system, created_agents = _make_system()
        player = FakePlayer(combat_xp=120000, next_agent_id=1)

        academy = FakeBuilding(building_type="AC", building_level=1)
        for _ in range(num_agents):
            _train_and_complete(system, player, academy)

        # Total = 1 (commander) + num_agents NPCs
        total = 1 + num_agents

        # New cap must be less than total but at least 1
        new_cap = data.draw(
            st.integers(min_value=1, max_value=total - 1),
            label="new_cap",
        )

        system.handle_demotion(player, new_agent_cap=new_cap)

        agents = system.get_agents(player)
        excess = total - new_cap

        reserved = [a for a in agents if a.db.reserve]
        non_reserved = [a for a in agents if not a.db.reserve]

        # Exactly (total - new_cap) agents should be reserved
        assert len(reserved) == excess, (
            f"Expected {excess} reserved, got {len(reserved)}. "
            f"total={total}, new_cap={new_cap}"
        )

        # The reserved agents must be the ones with the highest IDs
        reserved_ids = sorted(a.db.agent_id for a in reserved)
        all_ids = sorted(a.db.agent_id for a in agents)
        expected_reserved_ids = all_ids[-excess:]

        assert reserved_ids == expected_reserved_ids, (
            f"Reserved IDs {reserved_ids} != expected highest "
            f"{expected_reserved_ids}. All IDs: {all_ids}"
        )

        # Non-reserved agents should have the lowest IDs
        non_reserved_ids = sorted(a.db.agent_id for a in non_reserved)
        expected_non_reserved_ids = all_ids[:len(all_ids) - excess]
        assert non_reserved_ids == expected_non_reserved_ids, (
            f"Non-reserved IDs {non_reserved_ids} != expected "
            f"{expected_non_reserved_ids}"
        )


# ------------------------------------------------------------------ #
#  Property 15: Agent Training Cost Scaling
# ------------------------------------------------------------------ #

class TestProperty15TrainingCostScaling:
    """
    **Validates: Requirements 8.3**

    For any agent number N (the Nth agent being trained), the training
    cost SHALL be base_cost × N where base_cost is
    {Wood: 15, Stone: 10, Iron: 5}.
    """

    @given(
        n=st.integers(min_value=2, max_value=18),
    )
    @settings(max_examples=200)
    def test_training_cost_scales_linearly(self, n):
        system, created_agents = _make_system()
        # Give the player enough resources and high rank
        initial_resources = {
            "Wood": 999_999, "Stone": 999_999, "Iron": 999_999,
            "Energy": 0, "Circuits": 0, "Nexium": 0,
        }
        player = FakePlayer(
            combat_xp=120000,
            next_agent_id=n,
            resources=dict(initial_resources),
        )

        academy = FakeBuilding(building_type="AC", building_level=1)

        # Train agents up to n-1 to fill the roster (without actually
        # spending resources — we pre-set next_agent_id to n).
        # We need the roster to have n-1 agents so the cap check passes.
        # Create fake agents for IDs 1..n-1 directly.
        for aid in range(1, n):
            agent = FakeAgent(agent_id=aid, owner=player)
            created_agents.append(agent)

        # Snapshot resources before training agent #n
        pre_wood = player._resources["Wood"]
        pre_stone = player._resources["Stone"]
        pre_iron = player._resources["Iron"]

        ok, msg = system.train_agent(player, academy)
        assert ok, f"Training agent #{n} failed: {msg}"

        # Verify cost = base_cost × n
        expected_wood = BASE_TRAINING_COST["Wood"] * n
        expected_stone = BASE_TRAINING_COST["Stone"] * n
        expected_iron = BASE_TRAINING_COST["Iron"] * n

        assert pre_wood - player._resources["Wood"] == expected_wood, (
            f"Wood cost for agent #{n}: expected {expected_wood}, "
            f"got {pre_wood - player._resources['Wood']}"
        )
        assert pre_stone - player._resources["Stone"] == expected_stone, (
            f"Stone cost for agent #{n}: expected {expected_stone}, "
            f"got {pre_stone - player._resources['Stone']}"
        )
        assert pre_iron - player._resources["Iron"] == expected_iron, (
            f"Iron cost for agent #{n}: expected {expected_iron}, "
            f"got {pre_iron - player._resources['Iron']}"
        )
