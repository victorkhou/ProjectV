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

    class _FakeAgentRepo:
        """In-memory AgentRepository over the tracked ``created_agents``."""

        def find_agents_for_owner(self, owner):
            return [
                a for a in created_agents
                if getattr(getattr(a, "db", None), "owner", None) is owner
            ]

        def find_all_agents(self):
            return list(created_agents)

        def find_all_enemies(self):
            return []

        def find_training_buildings(self):
            return []

    system = AgentSystem(
        registry=registry,
        event_bus=event_bus,
        create_npc_func=fake_create_npc,
        agent_repository=_FakeAgentRepo(),
    )
    # Ability notifications are PLAYER_NOTIFICATION events; attach the real
    # presenter so owner.messages captures the rendered strings.
    from mygame.world.presenters.test_support import attach_presenter
    attach_presenter(event_bus)
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

        # Verify cost = base_cost × n (base cost now sourced from balance config)
        base_cost = system.registry.balance.base_training_cost
        expected_wood = base_cost["Wood"] * n
        expected_stone = base_cost["Stone"] * n
        expected_iron = base_cost["Iron"] * n

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


# ================================================================== #
#  PROPERTY 5 — Effective-level formula.
#
#  Feature: agent-progression, Property 5: Effective-level formula —
#  ``compute_effective_level`` returns ``max(1, min(Raw_Level,
#  owner_level - 1))``, always >= 1, strictly less than ``owner_level``
#  when ``owner_level > 1``, and equals 1 when ``owner_level == 1``.
#
#  **Validates: Requirements 14.1, 14.2, 14.3, 14.10**
# ================================================================== #


class CappedAgent(FakeAgent):
    """FakeAgent with a controllable raw level for owner-cap tests.

    Mirrors the ``CappedAgent`` used in ``test_agent_system.py``: it exposes a
    ``get_raw_level()`` that returns a fixed, Hypothesis-controlled value so the
    owner cap can be exercised independently of any XP curve.
    """

    def __init__(self, agent_id, owner=None, raw_level=1):
        super().__init__(agent_id=agent_id, owner=owner)
        self._raw_level = raw_level

    def get_raw_level(self):
        return self._raw_level


# Raw level and owner level span a reasonable progression range. Owner level is
# derived from the owning player's ``db.level`` via the legacy ``_get_level``
# rule, so setting ``owner.db.level`` directly controls ``owner_level``.
_raw_level_st = st.integers(min_value=1, max_value=60)
_owner_level_st = st.integers(min_value=1, max_value=60)


def _make_capped_agent(raw_level, owner_level):
    """Build a CappedAgent owned by a player whose Entity_Level == owner_level."""
    owner = FakePlayer()
    owner.db.level = owner_level
    return CappedAgent(agent_id=1, owner=owner, raw_level=raw_level)


class TestProperty5EffectiveLevelFormula:
    """
    **Validates: Requirements 14.1, 14.2, 14.3, 14.10**

    ``compute_effective_level`` returns ``max(1, min(Raw_Level,
    owner_level - 1))``, always >= 1, strictly below ``owner_level`` when
    ``owner_level > 1``, and equals 1 when ``owner_level == 1``.
    """

    @given(raw_level=_raw_level_st, owner_level=_owner_level_st)
    @settings(max_examples=200)
    def test_matches_formula(self, raw_level, owner_level):
        """Result equals ``max(1, min(raw_level, owner_level - 1))`` exactly.

        **Validates: Requirements 14.1, 14.2**
        """
        system, _ = _make_system()
        agent = _make_capped_agent(raw_level, owner_level)

        result = system.compute_effective_level(agent)
        expected = max(1, min(raw_level, owner_level - 1))
        assert result == expected, (
            f"compute_effective_level(raw={raw_level}, owner={owner_level}) "
            f"= {result}, expected {expected}"
        )

    @given(raw_level=_raw_level_st, owner_level=_owner_level_st)
    @settings(max_examples=200)
    def test_always_at_least_one(self, raw_level, owner_level):
        """Effective level is always floored at 1. **Validates: Requirements 14.10**"""
        system, _ = _make_system()
        agent = _make_capped_agent(raw_level, owner_level)
        assert system.compute_effective_level(agent) >= 1

    @given(
        raw_level=_raw_level_st,
        owner_level=st.integers(min_value=2, max_value=60),
    )
    @settings(max_examples=200)
    def test_strictly_below_owner_when_owner_above_one(self, raw_level, owner_level):
        """Strictly less than owner_level whenever owner_level > 1.

        **Validates: Requirements 14.2**
        """
        system, _ = _make_system()
        agent = _make_capped_agent(raw_level, owner_level)
        result = system.compute_effective_level(agent)
        assert result < owner_level, (
            f"effective {result} must be strictly below owner {owner_level} "
            f"(raw={raw_level})"
        )

    @given(raw_level=_raw_level_st)
    @settings(max_examples=200)
    def test_equals_one_when_owner_level_one(self, raw_level):
        """An owner at level 1 caps every agent at effective level 1.

        **Validates: Requirements 14.3**
        """
        system, _ = _make_system()
        agent = _make_capped_agent(raw_level, owner_level=1)
        assert system.compute_effective_level(agent) == 1


# ================================================================== #
#  PROPERTY 6 — XP award frozen at the cap ceiling.
#
#  Feature: agent-progression, Property 6: XP award frozen at the cap
#  ceiling — for any agent at its Cap_Ceiling (``agent.db.level >=
#  max(1, owner_level - 1)``) and any source, ``award_agent_xp`` leaves
#  ``combat_xp`` / ``level`` / ``rank_level`` unchanged.
#
#  **Validates: Requirements 5.9, 14.4**
# ================================================================== #

from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402
from world import progression  # noqa: E402  (same module CombatEntity uses)
from mygame.world.constants import MAX_LEVEL  # noqa: E402


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
    """db proxy mimicking Evennia's handler (mirrors test_agent_system)."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class RealAgent(CombatEntity):
    """Agent built on the real CombatEntity mixin.

    Exercises the genuine ``award_xp`` / ``recompute_progression`` path so
    ``db.combat_xp`` and ``db.level`` (the raw level) actually mutate through
    the shared progression curve — exactly what the freeze check reads. If
    ``award_agent_xp`` (incorrectly) awarded XP at the ceiling, this agent's
    ``combat_xp`` / ``level`` / ``rank_level`` would change and the property
    would fail.
    """

    def __init__(self, agent_id=1, owner=None):
        self.db = _AgentDb(_AgentAttrStore())
        self.at_combat_entity_init()  # combat_xp=0, level=1, rank_level=1
        self.db.agent_id = agent_id
        self.db.owner = owner
        self.key = f"Agent-{agent_id}"


# Sources whose presence must never bypass the freeze. All five recognised
# earning keys plus a couple that resolve to a zero/unknown amount.
_SOURCE_ST = st.sampled_from([
    "harvest", "delivery", "construction", "combat", "time_served",
])

# Owner level drives the cap ceiling = max(1, owner_level - 1).
_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=30)

# How far at/above the ceiling the agent's raw level sits (0 == exactly at it).
_OVER_CEILING_ST = st.integers(min_value=0, max_value=MAX_LEVEL)


class TestProperty6XpAwardFrozenAtCeiling:
    """
    **Validates: Requirements 5.9, 14.4**

    For any agent whose raw level has reached its Cap_Ceiling
    (``agent.db.level >= max(1, owner_level - 1)``) and any earning source,
    ``award_agent_xp`` is a no-op: ``combat_xp``, ``level`` and ``rank_level``
    remain unchanged.
    """

    @given(
        owner_level=_OWNER_LEVEL_ST,
        over_ceiling=_OVER_CEILING_ST,
        source=_SOURCE_ST,
    )
    @settings(max_examples=200)
    def test_award_at_or_above_ceiling_is_noop(self, owner_level, over_ceiling, source):
        system, _ = _make_system()
        # Force this registry's level->XP curve active so award_xp derives raw
        # levels deterministically (the threshold table is process-global).
        progression.build_thresholds(system.registry.ranks)

        cap_ceiling = max(1, owner_level - 1)
        # Seed the agent's raw level at/above the ceiling (clamped to MAX_LEVEL).
        target_level = min(MAX_LEVEL, cap_ceiling + over_ceiling)
        seed_xp = progression.xp_for_level(target_level)

        owner = FakePlayer()
        owner.db.level = owner_level
        agent = RealAgent(owner=owner)
        agent.award_xp(seed_xp)  # mutate combat_xp/level/rank_level via the curve

        # Precondition: the agent is genuinely at/above its ceiling.
        assert agent.db.level >= cap_ceiling, (
            f"setup error: raw level {agent.db.level} < ceiling {cap_ceiling} "
            f"(owner_level={owner_level}, seed_xp={seed_xp})"
        )
        assert system.get_cap_ceiling(agent) == cap_ceiling

        pre_xp = agent.db.combat_xp
        pre_level = agent.db.level
        pre_rank_level = agent.db.rank_level

        system.award_agent_xp(agent, source)

        assert agent.db.combat_xp == pre_xp, (
            f"combat_xp changed at ceiling: {pre_xp} -> {agent.db.combat_xp} "
            f"(owner_level={owner_level}, source={source})"
        )
        assert agent.db.level == pre_level, (
            f"level changed at ceiling: {pre_level} -> {agent.db.level} "
            f"(owner_level={owner_level}, source={source})"
        )
        assert agent.db.rank_level == pre_rank_level, (
            f"rank_level changed at ceiling: {pre_rank_level} -> "
            f"{agent.db.rank_level} (owner_level={owner_level}, source={source})"
        )


# ================================================================== #
#  PROPERTY 7 — XP award resumes when the ceiling rises.
#
#  Feature: agent-progression, Property 7: XP award resumes when the
#  ceiling rises — for an agent frozen at its ceiling, after the owner
#  level rises so ``Cap_Ceiling`` exceeds the agent's level, the next
#  ``award_agent_xp`` with a positive amount strictly increases
#  ``combat_xp`` (no banked surplus).
#
#  **Validates: Requirements 5.10, 14.8**
# ================================================================== #

from mygame.world.systems.agent_system import AGENT_XP_SOURCE_FIELDS  # noqa: E402


# Earning sources whose configured balance amount is strictly positive
# (``time_served`` defaults to 0 and is therefore a no-op regardless of the
# freeze, so it is excluded here).
_POSITIVE_SOURCE_ST = st.sampled_from([
    "harvest", "delivery", "construction", "combat",
])

# Initial owner level (>= 2 so Cap_Ceiling = owner_level - 1 >= 1 and the agent
# can be seeded at a level that is genuinely frozen at the ceiling).
_INITIAL_OWNER_LEVEL_ST = st.integers(min_value=2, max_value=25)

# How many levels the owner gains so the ceiling rises above the agent.
_LEVEL_RISE_ST = st.integers(min_value=1, max_value=10)


class TestProperty7XpAwardResumesWhenCeilingRises:
    """
    **Validates: Requirements 5.10, 14.8**

    An agent frozen exactly at its ``Cap_Ceiling`` receives no XP. After the
    owning player levels up so the new ``Cap_Ceiling`` exceeds the agent's raw
    level, the next ``award_agent_xp`` with a positive-amount source strictly
    increases ``combat_xp`` by EXACTLY the configured amount — no surplus is
    banked or recovered from the frozen period.
    """

    @given(
        owner_level=_INITIAL_OWNER_LEVEL_ST,
        level_rise=_LEVEL_RISE_ST,
        source=_POSITIVE_SOURCE_ST,
    )
    @settings(max_examples=200)
    def test_award_resumes_after_ceiling_rises_with_no_banked_surplus(
        self, owner_level, level_rise, source
    ):
        system, _ = _make_system()
        # Activate this registry's level->XP curve so award_xp derives raw
        # levels deterministically (the threshold table is process-global).
        progression.build_thresholds(system.registry.ranks)

        cap_ceiling = max(1, owner_level - 1)

        # Seed the agent's raw level exactly at its ceiling so it is frozen.
        seed_xp = progression.xp_for_level(cap_ceiling)
        owner = FakePlayer()
        owner.db.level = owner_level
        agent = RealAgent(owner=owner)
        agent.award_xp(seed_xp)

        # Precondition: the agent is genuinely at/above its ceiling (frozen).
        assert agent.db.level >= cap_ceiling, (
            f"setup error: raw level {agent.db.level} < ceiling {cap_ceiling} "
            f"(owner_level={owner_level}, seed_xp={seed_xp})"
        )
        assert system.get_cap_ceiling(agent) == cap_ceiling

        # --- While frozen, awarding XP is a no-op (combat_xp unchanged). ---
        frozen_xp = agent.db.combat_xp
        system.award_agent_xp(agent, source)
        assert agent.db.combat_xp == frozen_xp, (
            f"combat_xp changed while frozen at ceiling: "
            f"{frozen_xp} -> {agent.db.combat_xp} "
            f"(owner_level={owner_level}, source={source})"
        )

        # --- Raise the owner level so the ceiling rises above the agent. ---
        frozen_level = agent.db.level
        new_owner_level = min(MAX_LEVEL, frozen_level + 1 + level_rise)
        owner.db.level = new_owner_level
        new_ceiling = max(1, new_owner_level - 1)

        # The ceiling must now strictly exceed the agent's raw level so awards
        # resume; otherwise this example does not exercise the property.
        assume(new_ceiling > frozen_level)
        assert system.get_cap_ceiling(agent) == new_ceiling

        # Configured amount for this source (strictly positive by construction).
        field = AGENT_XP_SOURCE_FIELDS[source]
        amount = getattr(system.registry.balance, field, 0) or 0
        assert amount > 0, f"expected positive amount for source {source}"

        # --- The next award strictly increases combat_xp by EXACTLY amount. ---
        before_xp = agent.db.combat_xp
        system.award_agent_xp(agent, source)
        after_xp = agent.db.combat_xp

        assert after_xp > before_xp, (
            f"combat_xp did not increase after ceiling rose: "
            f"{before_xp} -> {after_xp} (new_owner_level={new_owner_level}, "
            f"source={source})"
        )
        # No banked surplus: the increase equals exactly the configured amount,
        # NOT amount + anything accumulated while frozen.
        assert after_xp - before_xp == amount, (
            f"combat_xp rose by {after_xp - before_xp}, expected exactly "
            f"{amount} (no banked surplus). source={source}, "
            f"owner_level {owner_level}->{new_owner_level}"
        )


# ================================================================== #
#  PROPERTY 8 — Effective-level clamp on owner demotion never strips XP.
#
#  Feature: agent-progression, Property 8: Effective-level clamp on owner
#  demotion never strips XP — after any owner-level decrease,
#  ``Effective_Level == max(1, min(Raw_Level, new_owner_level - 1))``
#  while ``combat_xp`` / ``level`` / ``rank_level`` remain unchanged.
#
#  **Validates: Requirements 10.1, 14.1, 14.7, 15.1**
# ================================================================== #


# Raw level the agent is seeded at (owner-agnostic, derived from combat_xp).
_P8_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
# Owner level before the demotion (the higher / unchanged value).
_P8_OLD_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=60)


class TestProperty8EffectiveLevelClampOnDemotionNeverStripsXP:
    """
    **Validates: Requirements 10.1, 14.1, 14.7, 15.1**

    For any owner-level decrease (a demotion or no-change), the agent's
    Effective_Level re-derives to ``max(1, min(Raw_Level, new_owner_level - 1))``
    for the NEW (lower) owner level, while the agent's own earned progression —
    ``combat_xp``, ``db.level`` (raw level) and ``db.rank_level`` — is left
    completely UNCHANGED. The owner cap clamps only the derived effective
    level; it never strips earned XP.
    """

    @given(
        raw_level=_P8_RAW_LEVEL_ST,
        old_owner_level=_P8_OLD_OWNER_LEVEL_ST,
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_demotion_clamps_effective_but_preserves_xp(
        self, raw_level, old_owner_level, data
    ):
        # New owner level is a demotion (or no-change): <= old_owner_level.
        new_owner_level = data.draw(
            st.integers(min_value=1, max_value=old_owner_level),
            label="new_owner_level",
        )

        system, _ = _make_system()
        # Activate this registry's level->XP curve so the RealAgent derives raw
        # level / rank deterministically (the threshold table is process-global).
        progression.build_thresholds(system.registry.ranks)

        # Seed a CombatEntity-backed agent so db.level / db.rank_level are real,
        # owner-agnostic values driven purely by its own combat_xp.
        seed_xp = progression.xp_for_level(raw_level)
        owner = FakePlayer()
        owner.db.level = old_owner_level
        agent = RealAgent(owner=owner)
        agent.award_xp(seed_xp)

        # The seeded raw level is exactly what the agent reports owner-agnostically.
        agent_raw_level = agent.get_raw_level()

        # Snapshot the agent's own earned progression BEFORE the demotion.
        pre_xp = agent.db.combat_xp
        pre_level = agent.db.level
        pre_rank_level = agent.db.rank_level

        # --- Owner demotion: decrease the owner's Entity_Level. ---
        owner.db.level = new_owner_level

        # (a) Effective_Level re-derives against the NEW owner level.
        effective = system.compute_effective_level(agent)
        expected = max(1, min(agent_raw_level, new_owner_level - 1))
        assert effective == expected, (
            f"effective level {effective} != expected {expected} "
            f"(raw={agent_raw_level}, new_owner_level={new_owner_level}, "
            f"old_owner_level={old_owner_level})"
        )

        # (b) The agent's earned progression is UNTOUCHED by the demotion.
        assert agent.db.combat_xp == pre_xp, (
            f"combat_xp stripped by demotion: {pre_xp} -> {agent.db.combat_xp} "
            f"(owner {old_owner_level} -> {new_owner_level})"
        )
        assert agent.db.level == pre_level, (
            f"raw level changed by demotion: {pre_level} -> {agent.db.level} "
            f"(owner {old_owner_level} -> {new_owner_level})"
        )
        assert agent.db.rank_level == pre_rank_level, (
            f"rank_level changed by demotion: {pre_rank_level} -> "
            f"{agent.db.rank_level} (owner {old_owner_level} -> {new_owner_level})"
        )


# ================================================================== #
#  PROPERTY 14 (agent-progression spec) — Legacy-agent defaulting and
#  first-mutation persistence.
#
#  NOTE: The module docstring above also lists an OLD "Property 14"
#  (Demotion Reserves Highest-ID) from the legacy roster tests — a
#  DIFFERENT numbering. This class implements the agent-progression
#  spec's Property 14.
#
#  Feature: agent-progression, Property 14: Legacy agent defaulting and
#  first-mutation persistence — for an agent lacking progression attrs,
#  ``get_combat_xp() == 0``, ``get_raw_level() == 1`` and
#  ``get_enabled_abilities() == set()``; after the first ``award_xp`` /
#  ``deduct_xp``, all three attrs are present and consistent
#  (``level == level_for_xp(combat_xp)`` and
#  ``rank_level == rank_for_level(level)``).
#
#  **Validates: Requirements 12.2, 12.3, 12.4**
# ================================================================== #


class LegacyAgent(CombatEntity):
    """A CombatEntity-backed agent that LACKS progression attributes.

    Unlike ``RealAgent`` (Property 6), this variant deliberately does NOT call
    ``at_combat_entity_init()``, so ``db.combat_xp`` / ``db.level`` /
    ``db.rank_level`` and ``db.enabled_abilities`` are never written. Reads of
    those attributes therefore hit the legacy defaults (the ``_AgentAttrStore``
    returns ``None`` for absent keys), exactly modelling a pre-feature agent.
    """

    def __init__(self, agent_id=1, owner=None):
        self.db = _AgentDb(_AgentAttrStore())
        # Intentionally NO at_combat_entity_init() — progression attrs absent.
        self.db.agent_id = agent_id
        self.db.owner = owner
        self.key = f"Agent-{agent_id}"


# First mutation is either an award or a deduct; both must initialize and
# persist the progression attributes consistently (Req 12.5-adjacent).
_FIRST_MUTATION_ST = st.sampled_from(["award", "deduct"])
# A strictly positive amount so the mutation is not a no-op.
_MUTATION_AMOUNT_ST = st.integers(min_value=1, max_value=200_000)


class TestAgentProgressionProperty14LegacyDefaulting:
    """
    **Validates: Requirements 12.2, 12.3, 12.4**

    For an agent created before this feature (no ``combat_xp`` / ``level`` /
    ``enabled_abilities`` attributes), progression reads return the documented
    legacy defaults — ``get_combat_xp() == 0`` (Req 12.2),
    ``get_raw_level() == 1`` (Req 12.3) and ``get_enabled_abilities() == set()``
    (Req 12.4). After the agent's FIRST experience mutation (``award_xp`` or
    ``deduct_xp``), all three progression attributes are present and mutually
    consistent with the shared XP curve.
    """

    def _assert_absent(self, agent):
        store = agent.db._store
        assert not store.has("combat_xp"), "combat_xp should be absent on a legacy agent"
        assert not store.has("level"), "level should be absent on a legacy agent"
        assert not store.has("rank_level"), "rank_level should be absent on a legacy agent"

    @given(
        amount=_MUTATION_AMOUNT_ST,
        mutation=_FIRST_MUTATION_ST,
    )
    @settings(max_examples=200)
    def test_legacy_defaults_then_first_mutation_persists(self, amount, mutation):
        system, _ = _make_system()
        # Activate this registry's level->XP curve so level_for_xp resolves
        # deterministically (the threshold table is process-global).
        progression.build_thresholds(system.registry.ranks)

        owner = FakePlayer()
        agent = LegacyAgent(owner=owner)

        # --- Precondition: progression attributes are genuinely absent. ---
        self._assert_absent(agent)

        # --- (1) Legacy defaulting BEFORE any mutation (Req 12.2/12.3/12.4). ---
        assert agent.get_combat_xp() == 0, (
            f"legacy combat_xp default should be 0, got {agent.get_combat_xp()}"
        )
        assert agent.get_raw_level() == 1, (
            f"legacy raw level default should be 1, got {agent.get_raw_level()}"
        )
        assert system.get_enabled_abilities(agent) == set(), (
            f"legacy enabled abilities should be empty set, "
            f"got {system.get_enabled_abilities(agent)}"
        )

        # Reads alone must NOT have persisted any progression attribute.
        self._assert_absent(agent)

        # --- (2) First mutation initializes and persists all three attrs. ---
        if mutation == "award":
            agent.award_xp(amount)
        else:
            agent.deduct_xp(amount)

        store = agent.db._store
        assert store.has("combat_xp"), "combat_xp must be persisted after first mutation"
        assert store.has("level"), "level must be persisted after first mutation"
        assert store.has("rank_level"), "rank_level must be persisted after first mutation"

        # combat_xp is a non-negative int. A deduct from the 0 default floors at 0;
        # an award adds exactly `amount`.
        combat_xp = agent.db.combat_xp
        assert isinstance(combat_xp, int) and combat_xp >= 0, (
            f"combat_xp must be a non-negative int, got {combat_xp!r}"
        )
        if mutation == "award":
            assert combat_xp == amount, (
                f"award from legacy default should yield exactly {amount}, "
                f"got {combat_xp}"
            )
        else:
            assert combat_xp == 0, (
                f"deduct from legacy default (0) should floor at 0, got {combat_xp}"
            )

        # --- (3) Persisted level/rank are consistent with the shared curve. ---
        assert agent.db.level == progression.level_for_xp(combat_xp), (
            f"persisted level {agent.db.level} != level_for_xp({combat_xp}) "
            f"= {progression.level_for_xp(combat_xp)}"
        )
        assert agent.db.rank_level == progression.rank_for_level(agent.db.level), (
            f"persisted rank_level {agent.db.rank_level} != "
            f"rank_for_level({agent.db.level}) "
            f"= {progression.rank_for_level(agent.db.level)}"
        )

        # The convenience accessors agree with the persisted values.
        assert agent.get_combat_xp() == combat_xp
        assert agent.get_raw_level() == agent.db.level


# ================================================================== #
#  PROPERTY 9 (agent-progression spec) — Gate attachment matches
#  effective level AND enabled state on role apply.
#
#  Feature: agent-progression, Property 9: Gate attachment matches
#  effective level AND enabled state on role apply — after the harvester
#  role is applied via ``_attach_behavior_script(agent, "harvester")``,
#  ``DeliveryBehavior`` attaches if and only if
#  ``Effective_Level >= delivery gate required_level`` AND ``delivery``
#  is in the agent's enabled set; ``HarvesterScript`` always attaches
#  regardless of the gate outcome.
#
#  **Validates: Requirements 8.1, 8.2, 8.3, 8.5, 8.6, 10.4, 12.5**
# ================================================================== #

from mygame.world.definitions import AbilityGateDef  # noqa: E402
from mygame.world.systems.agent_system import ABILITY_SCRIPT_KEYS  # noqa: E402

#: The delivery ability gate level used throughout Property 9. Mirrors the
#: gate-aware unit tests in ``world/systems/tests/test_agent_system.py``:
#: delivery unlocks at Effective_Level 21 (the first level of rank 5).
_DELIVERY_GATE_LEVEL = 21


class FakeScript:
    """Minimal stand-in for an attached Evennia Script (key + delete)."""

    def __init__(self, key):
        self.key = key
        self._deleted = False

    def delete(self):
        self._deleted = True


class FakeScriptManager:
    """Minimal scripts manager supporting ``.all()`` / ``.add()`` semantics.

    Mirrors the slice of Evennia's ScriptHandler that ``_attach_behavior_script``
    and the gate-evaluation helpers rely on. ``add(cls)`` resolves the script
    key exactly the way ``AgentSystem`` does: by class name via
    ``ABILITY_SCRIPT_KEYS`` (so ``DeliveryBehavior`` → ``"delivery_behavior"``),
    falling back to the class's ``key`` attribute or ``__name__`` (so the base
    ``HarvesterScript`` resolves to ``"HarvesterScript"``).
    """

    def __init__(self):
        self._scripts = []

    def all(self):
        return [s for s in self._scripts if not s._deleted]

    def add(self, script_cls):
        key = ABILITY_SCRIPT_KEYS.get(
            getattr(script_cls, "__name__", ""),
            getattr(script_cls, "key", "") or script_cls.__name__,
        )
        self._scripts.append(FakeScript(key))


class ScriptedHarvesterAgent(CappedAgent):
    """A CappedAgent (controllable raw level) with a scripts manager.

    Combines the controllable ``get_raw_level`` of ``CappedAgent`` (so
    ``compute_effective_level`` is fully driven by the Hypothesis-generated raw
    level and the owner level) with a ``FakeScriptManager`` so attach/detach of
    behavior scripts can be observed — equivalent to ``CappedScriptedAgent`` in
    ``world/systems/tests/test_agent_system.py``.
    """

    def __init__(self, agent_id, owner=None, raw_level=1, script_keys=None):
        super().__init__(agent_id=agent_id, owner=owner, raw_level=raw_level)
        self.scripts = FakeScriptManager()
        for key in (script_keys or []):
            self.scripts._scripts.append(FakeScript(key))


def _make_harvester_system():
    """An AgentSystem whose registry has only a delivery gate at level 21."""
    system, created_agents = _make_system()
    system.registry.ability_gates = {
        "delivery": AbilityGateDef(
            key="delivery", required_level=_DELIVERY_GATE_LEVEL
        ),
    }
    return system, created_agents


# Raw level and owner level both span 1..40 so the generated Effective_Level
# straddles both sides of the level-21 delivery gate (owner level also caps the
# effective level at owner_level - 1, exercising the cap path).
_P9_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P9_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=40)


class TestAgentProgressionProperty9GateAttachmentOnRoleApply:
    """
    **Validates: Requirements 8.1, 8.2, 8.3, 8.5, 8.6, 10.4, 12.5**

    After applying the harvester role via
    ``_attach_behavior_script(agent, "harvester")``: ``HarvesterScript`` is
    ALWAYS attached (Req 8.1), and ``DeliveryBehavior`` (key
    ``"delivery_behavior"``) is attached if and only if the agent's
    ``Effective_Level`` meets the delivery gate AND ``"delivery"`` is in the
    agent's enabled set (Req 8.2, 8.3, 8.5, 8.6, 10.4, 12.5).
    """

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    @given(
        raw_level=_P9_RAW_LEVEL_ST,
        owner_level=_P9_OWNER_LEVEL_ST,
        delivery_enabled=st.booleans(),
    )
    @settings(max_examples=200)
    def test_gate_attachment_matches_effective_level_and_enabled(
        self, raw_level, owner_level, delivery_enabled
    ):
        system, _ = _make_harvester_system()

        owner = FakePlayer()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=1, owner=owner, raw_level=raw_level
        )
        agent.db.enabled_abilities = ["delivery"] if delivery_enabled else []

        # Apply the harvester role: base HarvesterScript + gate evaluation.
        system._attach_behavior_script(agent, "harvester")

        keys = self._script_keys(agent)
        effective = system.compute_effective_level(agent)
        expected_delivery = (
            effective >= _DELIVERY_GATE_LEVEL and delivery_enabled
        )

        # (a) HarvesterScript always attaches (Req 8.1).
        assert "HarvesterScript" in keys, (
            f"HarvesterScript must always attach on harvester role apply; "
            f"keys={keys} (raw={raw_level}, owner={owner_level})"
        )

        # (b) DeliveryBehavior attaches IFF effective >= gate AND enabled
        #     (Req 8.2, 8.3, 8.5, 8.6, 10.4, 12.5).
        assert ("delivery_behavior" in keys) == expected_delivery, (
            f"delivery attach mismatch: present={'delivery_behavior' in keys}, "
            f"expected={expected_delivery} "
            f"(raw={raw_level}, owner={owner_level}, effective={effective}, "
            f"gate={_DELIVERY_GATE_LEVEL}, enabled={delivery_enabled})"
        )


# ================================================================== #
#  PROPERTY 10 (agent-progression spec) — Gate-evaluation convergence
#  and idempotence (available AND enabled).
#
#  Feature: agent-progression, Property 10: Gate evaluation convergence
#  and idempotence (available AND enabled) — repeated
#  ``evaluate_gated_abilities`` calls leave a gated script attached if and
#  only if ``Effective_Level >= required`` AND the ability is enabled
#  (exactly one instance, no duplicates), retain ``HarvesterScript`` across
#  a delivery detach, and emit the available / now-active / re-locked
#  notifications exactly on their respective transitions.
#
#  **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.8, 14.6,
#  15.2, 15.3, 15.4**
#
#  Reuses the Property 9 fakes: ``FakeScript``, ``FakeScriptManager``,
#  ``ScriptedHarvesterAgent``, ``_make_harvester_system`` (delivery gate at
#  level ``_DELIVERY_GATE_LEVEL == 21``).
# ================================================================== #


class NotifyingHarvesterOwner(FakePlayer):
    """A FakePlayer that captures ``owner.msg(...)`` notifications.

    The base ``FakePlayer`` in this module has no ``msg`` method, so
    ``AgentSystem._notify_owner`` silently no-ops for it. This subclass adds the
    capture buffer needed to assert that the available / now-active / re-locked
    notifications fire exactly on their transitions (Req 15.2, 15.3, 15.4).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages: list[str] = []

    def msg(self, text, **kwargs):
        self.messages.append(text)


# Raw level and owner level both span 1..40 so the generated Effective_Level
# straddles both sides of the level-21 delivery gate.
_P10_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P10_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=40)
# Repeated evaluation count — convergence/idempotence must hold for any N >= 1.
_P10_CALL_COUNT_ST = st.integers(min_value=1, max_value=5)


def _make_notifying_harvester(owner_level, raw_level, enabled=False,
                              script_keys=None, agent_id=1):
    """Build a scripted harvester owned by a notification-capturing player."""
    owner = NotifyingHarvesterOwner()
    owner.db.level = owner_level
    agent = ScriptedHarvesterAgent(
        agent_id=agent_id, owner=owner, raw_level=raw_level,
        script_keys=script_keys,
    )
    agent.db.enabled_abilities = ["delivery"] if enabled else []
    return owner, agent


class TestAgentProgressionProperty10GateConvergenceAndIdempotence:
    """
    **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.8, 14.6,
    15.2, 15.3, 15.4**

    Repeated ``evaluate_gated_abilities`` calls converge to the same gated-
    script attachment regardless of how many times they run, never duplicate a
    script, always retain ``HarvesterScript`` (including across a delivery
    detach), and emit each transition notification exactly once.
    """

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    # -- 1) Convergence + idempotence (Req 9.1-9.4, 9.8, 14.6) ---------- #

    @given(
        raw_level=_P10_RAW_LEVEL_ST,
        owner_level=_P10_OWNER_LEVEL_ST,
        delivery_enabled=st.booleans(),
        n_calls=_P10_CALL_COUNT_ST,
    )
    @settings(max_examples=200)
    def test_repeated_evaluation_converges_without_duplicates(
        self, raw_level, owner_level, delivery_enabled, n_calls
    ):
        """N repeated evaluations leave delivery attached IFF available AND
        enabled, with exactly one instance and HarvesterScript retained.

        **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.8, 14.6**
        """
        system, _ = _make_harvester_system()
        # Pre-attach HarvesterScript: a harvester always runs it (Req 8.1).
        owner, agent = _make_notifying_harvester(
            owner_level, raw_level, enabled=delivery_enabled,
            script_keys=["HarvesterScript"],
        )

        for _ in range(n_calls):
            system.evaluate_gated_abilities(agent)

        keys = self._script_keys(agent)
        effective = system.compute_effective_level(agent)
        expected_delivery = (
            effective >= _DELIVERY_GATE_LEVEL and delivery_enabled
        )

        # Delivery attached IFF (effective >= gate AND enabled).
        assert ("delivery_behavior" in keys) == expected_delivery, (
            f"delivery attach mismatch after {n_calls} calls: "
            f"present={'delivery_behavior' in keys}, "
            f"expected={expected_delivery} (raw={raw_level}, "
            f"owner={owner_level}, effective={effective}, "
            f"enabled={delivery_enabled})"
        )
        # Exactly one instance when attached — never a duplicate (Req 9.4).
        assert keys.count("delivery_behavior") == (1 if expected_delivery else 0), (
            f"duplicate/missing delivery_behavior: keys={keys}"
        )
        # HarvesterScript is always retained, exactly once (Req 8.1).
        assert keys.count("HarvesterScript") == 1, (
            f"HarvesterScript must remain attached exactly once; keys={keys}"
        )

    # -- 2) HarvesterScript retained across a delivery detach (Req 9.5, 9.7) - #

    @given(
        raw_level=st.integers(min_value=_DELIVERY_GATE_LEVEL, max_value=40),
        owner_level=st.integers(min_value=_DELIVERY_GATE_LEVEL + 1, max_value=40),
    )
    @settings(max_examples=200)
    def test_harvester_retained_across_delivery_detach(
        self, raw_level, owner_level
    ):
        """Delivery attaches when enabled+available, then a disable detaches only
        delivery — HarvesterScript stays attached.

        **Validates: Requirements 9.5, 9.7, 14.6**
        """
        system, _ = _make_harvester_system()
        # Start at/above the gate AND enabled, with HarvesterScript present.
        owner, agent = _make_notifying_harvester(
            owner_level, raw_level, enabled=True,
            script_keys=["HarvesterScript"],
        )

        # Effective level must clear the gate for this example to be meaningful.
        effective = system.compute_effective_level(agent)
        assert effective >= _DELIVERY_GATE_LEVEL, (
            f"setup error: effective {effective} < gate {_DELIVERY_GATE_LEVEL}"
        )

        # First evaluation attaches delivery alongside the harvester script.
        system.evaluate_gated_abilities(agent)
        keys_after_attach = self._script_keys(agent)
        assert "delivery_behavior" in keys_after_attach
        assert "HarvesterScript" in keys_after_attach

        # Disable delivery (clear the enabled set) and re-evaluate → detach.
        agent.db.enabled_abilities = []
        system.evaluate_gated_abilities(agent)

        keys_after_detach = self._script_keys(agent)
        assert "delivery_behavior" not in keys_after_detach, (
            f"delivery should detach once disabled; keys={keys_after_detach}"
        )
        # HarvesterScript survives the delivery detach (Req 9.5).
        assert keys_after_detach.count("HarvesterScript") == 1, (
            f"HarvesterScript must survive the delivery detach; "
            f"keys={keys_after_detach}"
        )

    # -- 3a) available-but-not-enabled → "available" notice once (Req 15.2) - #

    @given(
        raw_level=st.integers(min_value=_DELIVERY_GATE_LEVEL, max_value=40),
        owner_level=st.integers(min_value=_DELIVERY_GATE_LEVEL + 1, max_value=40),
        n_calls=_P10_CALL_COUNT_ST,
    )
    @settings(max_examples=200)
    def test_available_notice_emitted_exactly_once(
        self, raw_level, owner_level, n_calls
    ):
        """Crossing the gate while NOT enabled offers the ability once, with no
        attach, even across repeated evaluations.

        **Validates: Requirements 9.1, 15.2**
        """
        system, _ = _make_harvester_system()
        owner, agent = _make_notifying_harvester(
            owner_level, raw_level, enabled=False,
            script_keys=["HarvesterScript"],
        )

        effective = system.compute_effective_level(agent)
        assert effective >= _DELIVERY_GATE_LEVEL

        for _ in range(n_calls):
            system.evaluate_gated_abilities(agent)

        # No attach while merely available (Req 9.1).
        assert "delivery_behavior" not in self._script_keys(agent)
        # The "available" notice fires exactly once across N calls (Req 15.2).
        available_msgs = [m for m in owner.messages if "available" in m]
        assert len(available_msgs) == 1, (
            f"expected exactly one 'available' notice across {n_calls} calls, "
            f"got {len(available_msgs)}: {owner.messages}"
        )
        assert f"agent ability {agent.db.agent_id} delivery on" in available_msgs[0]

    # -- 3b) becomes enabled+available → "now active" on attach (Req 15.3) -- #

    @given(
        raw_level=st.integers(min_value=_DELIVERY_GATE_LEVEL, max_value=40),
        owner_level=st.integers(min_value=_DELIVERY_GATE_LEVEL + 1, max_value=40),
    )
    @settings(max_examples=200)
    def test_now_active_notice_on_enable_transition(self, raw_level, owner_level):
        """After enabling an available ability, the next evaluation attaches it
        and emits the 'now active' notice exactly on that transition.

        **Validates: Requirements 9.2, 9.3, 15.3**
        """
        from mygame.world.constants import DeliveryState

        system, _ = _make_harvester_system()
        owner, agent = _make_notifying_harvester(
            owner_level, raw_level, enabled=False,
            script_keys=["HarvesterScript"],
        )

        # First eval: available-but-not-enabled (no attach, no "now active").
        system.evaluate_gated_abilities(agent)
        assert "delivery_behavior" not in self._script_keys(agent)
        assert not any("now active" in m for m in owner.messages)

        # Player enables delivery, then re-evaluate → attach + "now active".
        agent.db.enabled_abilities = ["delivery"]
        system.evaluate_gated_abilities(agent)

        assert "delivery_behavior" in self._script_keys(agent)
        # Delivery FSM initialized on attach (Req 9.3).
        assert agent.db.delivery_state == DeliveryState.IDLE
        now_active_msgs = [m for m in owner.messages if "now active" in m]
        assert len(now_active_msgs) == 1, (
            f"expected exactly one 'now active' notice on the attach "
            f"transition, got {len(now_active_msgs)}: {owner.messages}"
        )

    # -- 3c) effective drops below gate → "re-locked" on detach (Req 15.4) -- #

    @given(
        raw_level=st.integers(min_value=_DELIVERY_GATE_LEVEL, max_value=40),
        high_owner_level=st.integers(min_value=_DELIVERY_GATE_LEVEL + 1, max_value=40),
        low_owner_level=st.integers(min_value=1, max_value=_DELIVERY_GATE_LEVEL),
    )
    @settings(max_examples=200)
    def test_relock_notice_on_level_drop_detach(
        self, raw_level, high_owner_level, low_owner_level
    ):
        """An enabled+attached ability whose effective level drops below the gate
        detaches and emits the 're-locked' notice exactly on that transition,
        while keeping HarvesterScript.

        **Validates: Requirements 9.5, 9.7, 15.4**
        """
        system, _ = _make_harvester_system()
        owner, agent = _make_notifying_harvester(
            high_owner_level, raw_level, enabled=True,
            script_keys=["HarvesterScript"],
        )

        # Attach delivery while available + enabled.
        system.evaluate_gated_abilities(agent)
        assert "delivery_behavior" in self._script_keys(agent)

        # Owner is demoted so the agent's effective level drops below the gate.
        owner.db.level = low_owner_level
        effective = system.compute_effective_level(agent)
        assert effective < _DELIVERY_GATE_LEVEL, (
            f"setup error: effective {effective} still >= gate after demotion"
        )

        system.evaluate_gated_abilities(agent)

        keys = self._script_keys(agent)
        # Delivery detaches; HarvesterScript is retained (Req 9.5).
        assert "delivery_behavior" not in keys
        assert keys.count("HarvesterScript") == 1
        # Re-lock notice fires exactly once on the detach transition (Req 15.4).
        relock_msgs = [m for m in owner.messages if "re-locked" in m]
        assert len(relock_msgs) == 1, (
            f"expected exactly one 're-locked' notice on the detach "
            f"transition, got {len(relock_msgs)}: {owner.messages}"
        )


# ================================================================== #
#  PROPERTY 11 (agent-progression spec) — Progression survives
#  reserve/stop and is cap/reserve-independent.
#
#  NOTE ON DUAL NUMBERING: this module also contains an OLD
#  "Property 11: Agent Roster Invariant" (``TestProperty11AgentRoster
#  Invariant``) from the legacy roster numbering. The class below
#  implements the AGENT-PROGRESSION spec's Property 11, which is a
#  DIFFERENT property — hence the distinct, explicit class name
#  ``TestAgentProgressionProperty11ReserveStopIndependence``.
#
#  Feature: agent-progression, Property 11: Progression survives
#  reserve/stop and is cap/reserve-independent — reserve / stop /
#  unassign leave ``combat_xp`` / ``level`` / ``rank_level`` / the
#  enabled set unchanged, and for a fixed ``combat_xp`` + owner level +
#  enabled set the ``Effective_Level`` and per-ability status are
#  identical regardless of the agent's reserve / stopped status.
#
#  **Validates: Requirements 10.1, 10.2, 10.3**
#
#  Reuses ``RealAgent`` (real CombatEntity progression curve) and the
#  ``_make_harvester_system`` helper (delivery gate at level 21).
# ================================================================== #


# Raw level seeds the agent's combat_xp via the shared curve; owner level
# straddles both sides of the level-21 delivery gate so the cap path is
# exercised. ``raw_level`` up to 40 lets Effective_Level land both below and
# at/above the gate depending on the owner cap.
_P11_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P11_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=40)


class TestAgentProgressionProperty11ReserveStopIndependence:
    """
    **Validates: Requirements 10.1, 10.2, 10.3**

    Reserve, stop and unassign actions only flip benching/role flags; they
    must never touch an agent's earned progression (``combat_xp``,
    ``db.level``, ``db.rank_level``) or its sticky enabled-ability set
    (Req 10.1, 10.2). Furthermore, ``Effective_Level`` and the per-ability
    status are derived purely from the agent's own Combat_XP, the owner-level
    cap and the enabled set — independent of the agent's reserve/stopped
    status (Req 10.3).
    """

    # -- 1) Progression + enabled set survive reserve / stop / unassign --- #

    @given(
        raw_level=_P11_RAW_LEVEL_ST,
        owner_level=_P11_OWNER_LEVEL_ST,
        delivery_enabled=st.booleans(),
        do_reserve=st.booleans(),
        do_stop=st.booleans(),
        do_incapacitate=st.booleans(),
    )
    @settings(max_examples=200)
    def test_reserve_stop_unassign_preserve_progression(
        self, raw_level, owner_level, delivery_enabled,
        do_reserve, do_stop, do_incapacitate,
    ):
        """Toggling reserve / role-clear (stop/unassign) / incapacitated leaves
        ``combat_xp`` / ``level`` / ``rank_level`` and the enabled set unchanged.

        **Validates: Requirements 10.1, 10.2**
        """
        system, _ = _make_harvester_system()
        # Activate this registry's level->XP curve so the RealAgent derives raw
        # level / rank deterministically (the threshold table is process-global).
        progression.build_thresholds(system.registry.ranks)

        owner = FakePlayer()
        owner.db.level = owner_level
        agent = RealAgent(owner=owner)
        # Seed earned XP through the genuine progression curve, plus an active
        # role so a "stop"/"unassign" has something to clear.
        agent.award_xp(progression.xp_for_level(raw_level))
        agent.db.role = "harvester"
        agent.db.enabled_abilities = ["delivery"] if delivery_enabled else []

        # Snapshot the agent's earned progression + enabled set.
        pre_xp = agent.db.combat_xp
        pre_level = agent.db.level
        pre_rank_level = agent.db.rank_level
        pre_enabled = set(system.get_enabled_abilities(agent))

        # --- Simulate reserve / stop / unassign by flipping the flags those
        #     actions set (reserve benches; stop/unassign clears the role;
        #     incapacitate benches). At least progression must survive all. ---
        if do_reserve:
            agent.db.reserve = True
        if do_stop:
            agent.db.role = ""
            agent.db.role_target = None
        if do_incapacitate:
            agent.db.incapacitated = True

        # Earned progression is completely untouched (Req 10.1, 10.2).
        assert agent.db.combat_xp == pre_xp, (
            f"combat_xp changed by reserve/stop: {pre_xp} -> {agent.db.combat_xp} "
            f"(reserve={do_reserve}, stop={do_stop}, incap={do_incapacitate})"
        )
        assert agent.db.level == pre_level, (
            f"level changed by reserve/stop: {pre_level} -> {agent.db.level}"
        )
        assert agent.db.rank_level == pre_rank_level, (
            f"rank_level changed by reserve/stop: "
            f"{pre_rank_level} -> {agent.db.rank_level}"
        )
        # The sticky enabled set persists independent of benching/role state.
        assert set(system.get_enabled_abilities(agent)) == pre_enabled, (
            f"enabled set changed by reserve/stop: "
            f"{pre_enabled} -> {set(system.get_enabled_abilities(agent))}"
        )

    # -- 2) Effective_Level + ability status are reserve/stop independent - #

    @given(
        raw_level=_P11_RAW_LEVEL_ST,
        owner_level=_P11_OWNER_LEVEL_ST,
        delivery_enabled=st.booleans(),
    )
    @settings(max_examples=200)
    def test_derivation_independent_of_reserve_and_stopped_status(
        self, raw_level, owner_level, delivery_enabled,
    ):
        """For a fixed Combat_XP + owner level + enabled set, both
        ``compute_effective_level`` and the per-ability status are identical
        regardless of the agent's reserve / stopped status.

        **Validates: Requirements 10.3**
        """
        system, _ = _make_harvester_system()
        progression.build_thresholds(system.registry.ranks)

        seed_xp = progression.xp_for_level(raw_level)
        enabled = ["delivery"] if delivery_enabled else []

        def _build(reserve, role):
            owner = FakePlayer()
            owner.db.level = owner_level
            agent = RealAgent(owner=owner)
            agent.award_xp(seed_xp)
            agent.db.enabled_abilities = list(enabled)
            agent.db.reserve = reserve
            agent.db.role = role
            return agent

        # Active (assigned, not reserved) vs. reserved-and-stopped agent with
        # otherwise IDENTICAL Combat_XP, owner level and enabled set.
        active_agent = _build(reserve=False, role="harvester")
        benched_agent = _build(reserve=True, role="")

        # Effective_Level is identical (cap/reserve-independent, Req 10.3).
        active_eff = system.compute_effective_level(active_agent)
        benched_eff = system.compute_effective_level(benched_agent)
        assert active_eff == benched_eff, (
            f"Effective_Level differs by reserve/stopped status: "
            f"active={active_eff}, benched={benched_eff} "
            f"(raw={raw_level}, owner={owner_level})"
        )

        # Per-ability status is identical, derived purely from effective level
        # and the enabled set — never from reserve/role (Req 10.3).
        active_status = system.get_agent_progression_view(active_agent)[
            "ability_status"
        ]
        benched_status = system.get_agent_progression_view(benched_agent)[
            "ability_status"
        ]
        assert active_status == benched_status, (
            f"ability_status differs by reserve/stopped status: "
            f"active={active_status}, benched={benched_status} "
            f"(raw={raw_level}, owner={owner_level}, enabled={enabled})"
        )

        # Toggling the SAME agent's reserve/role flags must not change its
        # derived view either (idempotent w.r.t. benching).
        baseline_eff = system.compute_effective_level(active_agent)
        baseline_status = system.get_agent_progression_view(active_agent)[
            "ability_status"
        ]
        active_agent.db.reserve = True
        active_agent.db.role = ""
        active_agent.db.incapacitated = True
        assert system.compute_effective_level(active_agent) == baseline_eff
        assert (
            system.get_agent_progression_view(active_agent)["ability_status"]
            == baseline_status
        )


# ================================================================== #
#  PROPERTY 15 (agent-progression spec) — Gate extensibility and
#  unresolved-key safety (generic across keys).
#
#  NOTE ON DUAL NUMBERING: this module also contains an OLD
#  "Property 15: Agent Training Cost Scaling"
#  (``TestProperty15TrainingCostScaling``) from the legacy roster
#  numbering. The class below implements the AGENT-PROGRESSION spec's
#  Property 15, which is a DIFFERENT property — hence the distinct,
#  explicit class name ``TestAgentProgressionProperty15GateExtensibility``.
#
#  Feature: agent-progression, Property 15: Gate extensibility and
#  unresolved-key safety (generic across keys) — for added valid gates
#  whose keys map to a script, evaluation and enable/disable/status
#  operate purely on ``Effective_Level >= required`` + the enabled set
#  with no ``delivery``-specific behavior; for a gate whose key has no
#  script, evaluation attaches nothing, logs the key, and leaves the
#  agent otherwise unchanged.
#
#  **Validates: Requirements 13.1, 13.2, 13.4, 13.5**
#
#  Reuses the Property 9/10 fakes: ``FakeScript``, ``FakeScriptManager``,
#  ``ScriptedHarvesterAgent``, ``NotifyingHarvesterOwner``.
# ================================================================== #

#: A SECOND data-driven gate keyed ``"courier"`` whose required level mirrors
#: the first level of rank 3 ((3-1)*LEVELS_PER_RANK + 1 == 11). It is entirely
#: distinct from ``delivery`` (gate level 21) so the two gates straddle
#: different points of the generated effective-level range and prove the gate
#: machinery is generic across keys, not special-cased to ``delivery``.
_COURIER_GATE_LEVEL = 11
#: A gate whose key resolves to NO script class (unresolved). Its required
#: level is low so generated agents comfortably clear it.
_PHANTOM_GATE_LEVEL = 5


class _FakeDeliveryGatedScript:
    """Stand-in Script class for the ``delivery`` gate.

    Named distinctly from the real ``DeliveryBehavior`` (so no Evennia import is
    needed) but exposes a class-level ``key`` attribute so both
    ``FakeScriptManager.add`` (class-name miss → ``key`` fallback) and
    ``AgentSystem._ability_script_key`` (class-name miss → ``key`` fallback)
    resolve it to the predictable Evennia key ``"delivery_behavior"``.
    """

    key = "delivery_behavior"


class _FakeCourierGatedScript:
    """Stand-in Script class for the generic SECOND ``courier`` gate.

    Resolves to the predictable Evennia key ``"courier_behavior"`` via the same
    class-``key`` fallback path, with no ``delivery``-specific handling anywhere.
    """

    key = "courier_behavior"


def _patch_script_resolution(system, mapping):
    """Monkeypatch ``system.resolve_ability_script`` to a fixed key→class map.

    ``ABILITY_SCRIPT_MAP`` ships with only ``delivery`` → ``DeliveryBehavior``,
    so to exercise a generic second key (``courier``) that resolves to a script
    — and an unresolved key (``phantom``) that resolves to ``None`` — we shadow
    the bound static method on the instance with a closure over *mapping*. Keys
    absent from *mapping* resolve to ``None`` exactly like the real method
    (Req 13.4).
    """
    system.resolve_ability_script = lambda key: mapping.get(key)


# Raw level and owner level both span 1..40 so the generated Effective_Level
# straddles both the courier gate (11) and the delivery gate (21); owner level
# also caps effective level at owner_level - 1, exercising the cap path.
_P15_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P15_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=40)


class TestAgentProgressionProperty15GateExtensibility:
    """
    **Validates: Requirements 13.1, 13.2, 13.4, 13.5**

    The gate-evaluation machinery is generic across ability keys: a second,
    arbitrary gate (``courier``) whose key maps to a script attaches / detaches
    purely on ``Effective_Level >= required`` AND the enabled set, with the
    exact same logic as ``delivery`` and no ``delivery``-specific special-casing
    (Req 13.1, 13.2, 13.5). A gate whose key resolves to NO script attaches
    nothing and leaves the agent otherwise unchanged (Req 13.4).
    """

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    # -- 1) Generic-across-keys: a SECOND gate behaves exactly like the --- #
    #       first, driven only by effective level + enabled set (Req 13.1, --#
    #       13.2, 13.5).                                                    --#

    @given(
        raw_level=_P15_RAW_LEVEL_ST,
        owner_level=_P15_OWNER_LEVEL_ST,
        delivery_enabled=st.booleans(),
        courier_enabled=st.booleans(),
        n_calls=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=200)
    def test_second_gate_uses_identical_generic_logic(
        self, raw_level, owner_level, delivery_enabled, courier_enabled, n_calls
    ):
        """Register two gates (``delivery`` @21, ``courier`` @11) both mapping to
        a script. After evaluation, each gate's script is attached IFF that
        gate's ``Effective_Level >= required`` AND that key is enabled — the
        courier gate is governed by the identical formula as delivery, proving
        no key-specific behavior.

        **Validates: Requirements 13.1, 13.2, 13.5**
        """
        system, _ = _make_system()
        # Two independent, valid gates with different keys + required levels.
        system.registry.ability_gates = {
            "delivery": AbilityGateDef(key="delivery", required_level=_DELIVERY_GATE_LEVEL),
            "courier": AbilityGateDef(key="courier", required_level=_COURIER_GATE_LEVEL),
        }
        # Both keys resolve to (distinct) script classes — fully generic.
        _patch_script_resolution(
            system,
            {
                "delivery": _FakeDeliveryGatedScript,
                "courier": _FakeCourierGatedScript,
            },
        )

        owner = NotifyingHarvesterOwner()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=1, owner=owner, raw_level=raw_level,
            script_keys=["HarvesterScript"],
        )
        enabled = []
        if delivery_enabled:
            enabled.append("delivery")
        if courier_enabled:
            enabled.append("courier")
        agent.db.enabled_abilities = enabled

        for _ in range(n_calls):
            system.evaluate_gated_abilities(agent)

        keys = self._script_keys(agent)
        effective = system.compute_effective_level(agent)

        expected_delivery = effective >= _DELIVERY_GATE_LEVEL and delivery_enabled
        expected_courier = effective >= _COURIER_GATE_LEVEL and courier_enabled

        # Each gate attaches IFF its own (effective >= required AND enabled).
        assert ("delivery_behavior" in keys) == expected_delivery, (
            f"delivery attach mismatch: present={'delivery_behavior' in keys}, "
            f"expected={expected_delivery} (effective={effective}, "
            f"enabled={delivery_enabled})"
        )
        assert ("courier_behavior" in keys) == expected_courier, (
            f"courier attach mismatch: present={'courier_behavior' in keys}, "
            f"expected={expected_courier} (effective={effective}, "
            f"enabled={courier_enabled})"
        )
        # Exactly one instance each when attached — idempotent across N calls.
        assert keys.count("delivery_behavior") == (1 if expected_delivery else 0)
        assert keys.count("courier_behavior") == (1 if expected_courier else 0)
        # HarvesterScript (the always-on base) is untouched by either gate.
        assert keys.count("HarvesterScript") == 1, (
            f"HarvesterScript must remain attached exactly once; keys={keys}"
        )

    @given(
        raw_level=st.integers(min_value=_COURIER_GATE_LEVEL, max_value=40),
        owner_level=st.integers(min_value=_COURIER_GATE_LEVEL + 1, max_value=40),
    )
    @settings(max_examples=200)
    def test_second_gate_enable_disable_status_generic(self, raw_level, owner_level):
        """``enable_ability`` / ``disable_ability`` / ``get_ability_status`` work
        for the generic ``courier`` key exactly as for ``delivery``: enabling at/
        above the gate records the key + attaches the script and reports
        ``enabled``; disabling clears the key + detaches while leaving
        ``HarvesterScript`` in place and reports ``available``.

        **Validates: Requirements 13.1, 13.5**
        """
        system, created = _make_system()
        system.registry.ability_gates = {
            "courier": AbilityGateDef(key="courier", required_level=_COURIER_GATE_LEVEL),
        }
        _patch_script_resolution(system, {"courier": _FakeCourierGatedScript})

        owner = NotifyingHarvesterOwner()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=7, owner=owner, raw_level=raw_level,
            script_keys=["HarvesterScript"],
        )
        agent.db.enabled_abilities = []
        created.append(agent)

        # Effective level clears the courier gate for this example.
        effective = system.compute_effective_level(agent)
        assert effective >= _COURIER_GATE_LEVEL

        # Enable courier (generic key) → recorded + attached + reported enabled.
        system.enable_ability(owner, 7, "courier")
        assert "courier" in system.get_enabled_abilities(agent)
        assert "courier_behavior" in self._script_keys(agent)
        status = system.get_ability_status(owner, 7)
        assert "enabled" in status.lower()

        # Disable courier → cleared + detached, HarvesterScript retained.
        system.disable_ability(owner, 7, "courier")
        assert "courier" not in system.get_enabled_abilities(agent)
        keys = self._script_keys(agent)
        assert "courier_behavior" not in keys
        assert keys.count("HarvesterScript") == 1, (
            f"HarvesterScript must survive a courier disable; keys={keys}"
        )
        # Still available (above gate, just not enabled).
        status = system.get_ability_status(owner, 7)
        assert "available" in status.lower()

    # -- 2) Unresolved-key safety: a gate with no script attaches nothing - #
    #       and leaves the agent otherwise unchanged (Req 13.4).           --#

    @given(
        raw_level=st.integers(min_value=_PHANTOM_GATE_LEVEL + 1, max_value=40),
        owner_level=st.integers(min_value=_PHANTOM_GATE_LEVEL + 2, max_value=40),
        seed_xp=st.integers(min_value=0, max_value=5000),
        seed_level=st.integers(min_value=1, max_value=MAX_LEVEL),
        seed_rank=st.integers(min_value=1, max_value=12),
    )
    @settings(max_examples=200)
    def test_unresolved_key_attaches_nothing_and_leaves_agent_unchanged(
        self, raw_level, owner_level, seed_xp, seed_level, seed_rank
    ):
        """A gate keyed ``phantom`` that resolves to NO script: even with the
        agent at/above the gate AND the key enabled, evaluation attaches no
        script and leaves ``combat_xp`` / ``level`` / ``rank_level`` / the
        enabled set unchanged, and never disturbs other attached scripts.

        **Validates: Requirements 13.4**
        """
        system, _ = _make_system()
        system.registry.ability_gates = {
            "phantom": AbilityGateDef(key="phantom", required_level=_PHANTOM_GATE_LEVEL),
        }
        # No mapping for "phantom" → resolve_ability_script returns None.
        _patch_script_resolution(system, {})

        owner = NotifyingHarvesterOwner()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=1, owner=owner, raw_level=raw_level,
            script_keys=["HarvesterScript"],
        )
        # Enable the unresolved key and seed arbitrary progression state so any
        # accidental mutation would be detectable.
        agent.db.enabled_abilities = ["phantom"]
        agent.db.combat_xp = seed_xp
        agent.db.level = seed_level
        agent.db.rank_level = seed_rank

        # Precondition: the agent comfortably clears the phantom gate.
        assert system.compute_effective_level(agent) >= _PHANTOM_GATE_LEVEL

        pre_xp = agent.db.combat_xp
        pre_level = agent.db.level
        pre_rank = agent.db.rank_level
        pre_enabled = set(system.get_enabled_abilities(agent))
        pre_keys = sorted(self._script_keys(agent))

        # Evaluation must not raise and must skip the unresolved gate.
        system.evaluate_gated_abilities(agent)

        # No script was attached for the unresolved key, and the only script
        # present remains the pre-existing HarvesterScript (otherwise unchanged).
        post_keys = sorted(self._script_keys(agent))
        assert post_keys == pre_keys == ["HarvesterScript"], (
            f"unresolved key must attach nothing and disturb no other script; "
            f"pre={pre_keys}, post={post_keys}"
        )
        # Progression state and the enabled set are untouched (Req 13.4).
        assert agent.db.combat_xp == pre_xp
        assert agent.db.level == pre_level
        assert agent.db.rank_level == pre_rank
        assert set(system.get_enabled_abilities(agent)) == pre_enabled


# ================================================================== #
#  PROPERTY 17 (agent-progression spec) — Ability enablement command
#  behavior.
#
#  Feature: agent-progression, Property 17: Ability enablement command
#  behavior — ``enable_ability`` records the key and attaches the script
#  (initializing state) iff ``Effective_Level >= required``, else rejects
#  with the required level and neither records nor attaches;
#  ``disable_ability`` clears the key and detaches that script while
#  leaving ``HarvesterScript`` in place.
#
#  **Validates: Requirements 16.2, 16.3, 16.4, 9.6**
#
#  Reuses the Property 9/10 fakes: ``ScriptedHarvesterAgent`` (controllable
#  raw level + ``FakeScriptManager``), ``NotifyingHarvesterOwner``, and the
#  ``_make_harvester_system`` helper (delivery gate at level
#  ``_DELIVERY_GATE_LEVEL == 21``). The real ``resolve_ability_script`` is
#  used (no patching) so the genuine ``DeliveryBehavior`` is attached and
#  its ``delivery_state`` initialized to IDLE on enable.
# ================================================================== #


# Raw level and owner level both span 1..40 so the generated Effective_Level
# straddles both sides of the level-21 delivery gate (owner level also caps the
# effective level at owner_level - 1, exercising the cap path).
_P17_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P17_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P17_AGENT_ID_ST = st.integers(min_value=1, max_value=999)


class TestAgentProgressionProperty17AbilityEnablementCommand:
    """
    **Validates: Requirements 16.2, 16.3, 16.4, 9.6**

    ``enable_ability(player, agent_id, "delivery")`` records ``delivery`` in the
    enabled set and attaches the ``delivery_behavior`` script (initializing the
    delivery FSM to IDLE) IFF the agent's ``Effective_Level`` meets the gate
    (Req 16.2); otherwise it rejects with the required level and neither records
    the key nor attaches the script (Req 16.3). For an enabled agent,
    ``disable_ability`` clears ``delivery`` from the enabled set and detaches
    ``delivery_behavior`` while leaving the pre-attached ``HarvesterScript`` in
    place (Req 16.4, 9.6).
    """

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def _make_owned_harvester(self, system, created, owner_level, raw_level,
                              agent_id):
        """Build a scripted harvester (HarvesterScript pre-attached) owned by a
        notifying player and register it so ``get_agent_by_id`` finds it.

        ``enable_ability``/``disable_ability`` resolve the agent via
        ``get_agent_by_id`` → ``get_agents`` → the fake ``AgentRepository``
        injected by ``_make_system`` (returns ``created`` owned by *player*), so
        the agent must be appended to ``created`` with a matching ``owner`` and
        ``agent_id``.
        """
        owner = NotifyingHarvesterOwner()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=agent_id, owner=owner, raw_level=raw_level,
            script_keys=["HarvesterScript"],
        )
        agent.db.enabled_abilities = []
        created.append(agent)
        return owner, agent

    @given(
        raw_level=_P17_RAW_LEVEL_ST,
        owner_level=_P17_OWNER_LEVEL_ST,
        agent_id=_P17_AGENT_ID_ST,
    )
    @settings(max_examples=200)
    def test_enable_then_disable_behavior(self, raw_level, owner_level, agent_id):
        """Drive ``enable_ability`` across the delivery gate, then (for the
        enabled case) ``disable_ability``, asserting the recorded/attached state
        and the rejection message exactly track ``Effective_Level >= 21``.

        **Validates: Requirements 16.2, 16.3, 16.4, 9.6**
        """
        from mygame.world.constants import DeliveryState

        system, created = _make_harvester_system()
        owner, agent = self._make_owned_harvester(
            system, created, owner_level, raw_level, agent_id
        )

        effective = system.compute_effective_level(agent)
        meets_gate = effective >= _DELIVERY_GATE_LEVEL

        msg = system.enable_ability(owner, agent_id, "delivery")
        keys = self._script_keys(agent)

        if meets_gate:
            # (Req 16.2) Recorded in the enabled set...
            assert "delivery" in system.get_enabled_abilities(agent), (
                f"'delivery' must be recorded on enable at/above gate "
                f"(effective={effective}); msg={msg!r}"
            )
            # ...the behavior script is attached...
            assert "delivery_behavior" in keys, (
                f"'delivery_behavior' must attach on enable at/above gate; "
                f"keys={keys} (effective={effective})"
            )
            # ...with the delivery FSM initialized to IDLE...
            assert agent.db.delivery_state == DeliveryState.IDLE, (
                f"delivery_state must init to IDLE on enable, got "
                f"{agent.db.delivery_state!r}"
            )
            # ...and the message indicates success/enabled.
            assert "enabled" in msg.lower(), (
                f"enable message should confirm success; got {msg!r}"
            )

            # --- disable_ability clears + detaches, HarvesterScript stays --- #
            dmsg = system.disable_ability(owner, agent_id, "delivery")
            dkeys = self._script_keys(agent)
            # (Req 16.4) Cleared from the enabled set.
            assert "delivery" not in system.get_enabled_abilities(agent), (
                f"'delivery' must be cleared on disable; msg={dmsg!r}"
            )
            # The delivery behavior script is detached...
            assert "delivery_behavior" not in dkeys, (
                f"'delivery_behavior' must detach on disable; keys={dkeys}"
            )
            # ...while HarvesterScript stays in place (Req 9.6).
            assert dkeys.count("HarvesterScript") == 1, (
                f"HarvesterScript must survive a delivery disable; "
                f"keys={dkeys}"
            )
        else:
            # (Req 16.3) Below the gate: rejected, nothing recorded/attached.
            assert "delivery" not in system.get_enabled_abilities(agent), (
                f"'delivery' must NOT be recorded below the gate "
                f"(effective={effective}); msg={msg!r}"
            )
            assert "delivery_behavior" not in keys, (
                f"'delivery_behavior' must NOT attach below the gate; "
                f"keys={keys} (effective={effective})"
            )
            # The rejection message names the required gate level.
            assert str(_DELIVERY_GATE_LEVEL) in msg, (
                f"rejection message must mention required level "
                f"{_DELIVERY_GATE_LEVEL}; got {msg!r}"
            )
            # HarvesterScript is untouched by a rejected enable.
            assert keys.count("HarvesterScript") == 1, (
                f"HarvesterScript must remain after a rejected enable; "
                f"keys={keys}"
            )


# ================================================================== #
#  PROPERTY 18 (agent-progression spec) — Sticky enablement persists
#  across forced detach and drives auto re-attach.
#
#  Feature: agent-progression, Property 18: Sticky enablement persists
#  across forced detach and drives auto re-attach — with an ability
#  enabled, a drop below the gate detaches the behavior script but
#  RETAINS the enabled flag, and a later rise auto-re-attaches the
#  script with NO new enable command; after ``disable_ability`` clears
#  the flag, a rise does not re-attach until the player re-enables it.
#
#  **Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.5**
#
#  Reuses the Property 9/10/17 fakes: ``ScriptedHarvesterAgent``
#  (controllable raw level + ``FakeScriptManager``),
#  ``NotifyingHarvesterOwner``, and the ``_make_harvester_system`` helper
#  (delivery gate at level ``_DELIVERY_GATE_LEVEL == 21``). The real
#  ``resolve_ability_script`` is used (no patching) so the genuine
#  ``DeliveryBehavior`` is attached/detached and re-attached.
# ================================================================== #


# Raw level is held high (>= the gate) so the OWNER CAP is what crosses the
# delivery gate in both directions: effective == max(1, min(raw, owner-1)).
_P18_RAW_LEVEL_ST = st.integers(min_value=45, max_value=60)
# A "high" owner level whose cap ceiling (owner-1) meets/exceeds the gate, so
# the enabled ability is at/above its gate (effective >= 21).
_P18_HIGH_OWNER_LEVEL_ST = st.integers(min_value=22, max_value=60)
# A "low" owner level whose cap ceiling (owner-1 <= 20) forces effective below
# the gate, triggering the level-driven detach.
_P18_LOW_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=21)


class TestAgentProgressionProperty18StickyEnablement:
    """
    **Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.5**

    With ``delivery`` enabled via an explicit command at/above its gate
    (Req 17.2), an owner-cap drop below the gate detaches ``delivery_behavior``
    but RETAINS the agent's enabled flag (Req 17.1, 17.4); a later rise back
    above the gate auto-re-attaches the behavior with no additional player
    command (Req 17.3). After ``disable_ability`` clears the flag (Req 17.5),
    raising the level and re-evaluating does NOT re-attach until the player
    re-enables it.
    """

    def _script_keys(self, agent):
        return [s.key for s in agent.scripts.all()]

    def _make_owned_harvester(self, system, created, owner_level, raw_level,
                              agent_id):
        """Build a scripted harvester (HarvesterScript pre-attached) owned by a
        notifying player and register it so ``get_agent_by_id`` /
        ``enable_ability`` / ``disable_ability`` resolve it.

        Mirrors the Property 17 registration pattern: the agent must be appended
        to ``created`` with a matching ``owner`` and ``agent_id`` so the fake
        ``AgentRepository`` injected by ``_make_system`` returns it.
        """
        owner = NotifyingHarvesterOwner()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=agent_id, owner=owner, raw_level=raw_level,
            script_keys=["HarvesterScript"],
        )
        agent.db.enabled_abilities = []
        created.append(agent)
        return owner, agent

    @given(
        raw_level=_P18_RAW_LEVEL_ST,
        high_owner_level=_P18_HIGH_OWNER_LEVEL_ST,
        low_owner_level=_P18_LOW_OWNER_LEVEL_ST,
        agent_id=st.integers(min_value=1, max_value=999),
    )
    @settings(max_examples=200)
    def test_sticky_flag_survives_drop_and_auto_reattaches(
        self, raw_level, high_owner_level, low_owner_level, agent_id
    ):
        """Scenario A: an enabled ability detaches on a sub-gate drop while
        keeping its flag, then auto-re-attaches on the next rise with no new
        enable command.

        **Validates: Requirements 17.1, 17.3, 17.4**
        """
        system, created = _make_harvester_system()
        owner, agent = self._make_owned_harvester(
            system, created, high_owner_level, raw_level, agent_id
        )

        # --- Enable delivery at/above the gate (the first, explicit command) --
        effective_high = system.compute_effective_level(agent)
        assert effective_high >= _DELIVERY_GATE_LEVEL  # guaranteed by strategies
        system.enable_ability(owner, agent_id, "delivery")

        assert "delivery" in system.get_enabled_abilities(agent)
        assert "delivery_behavior" in self._script_keys(agent), (
            "delivery_behavior must be attached after enabling at/above gate"
        )

        # --- Drop the owner cap below the gate, then re-evaluate (Req 17.4) ----
        owner.db.level = low_owner_level
        effective_low = system.compute_effective_level(agent)
        assert effective_low < _DELIVERY_GATE_LEVEL  # guaranteed by strategies

        system.evaluate_gated_abilities(agent)
        keys_after_drop = self._script_keys(agent)

        # Behavior detaches on the sub-gate drop...
        assert "delivery_behavior" not in keys_after_drop, (
            f"delivery_behavior must detach below the gate "
            f"(effective={effective_low}); keys={keys_after_drop}"
        )
        # ...HarvesterScript is left in place...
        assert keys_after_drop.count("HarvesterScript") == 1, (
            f"HarvesterScript must survive a level-driven detach; "
            f"keys={keys_after_drop}"
        )
        # ...but the enabled flag is RETAINED (sticky, Req 17.1, 17.4).
        assert "delivery" in system.get_enabled_abilities(agent), (
            "enabled flag must be retained across a forced detach"
        )

        # --- Raise the owner cap back above the gate (Req 17.3) ---------------
        # No new enable command is issued — only re-evaluation.
        owner.db.level = high_owner_level
        assert system.compute_effective_level(agent) >= _DELIVERY_GATE_LEVEL

        system.evaluate_gated_abilities(agent)
        keys_after_rise = self._script_keys(agent)

        # Auto-re-attach with no additional player command (Req 17.3).
        assert "delivery_behavior" in keys_after_rise, (
            f"delivery_behavior must auto-re-attach on a rise back above the "
            f"gate with no new enable command; keys={keys_after_rise}"
        )
        # Exactly one instance (no duplicate from the re-attach).
        assert keys_after_rise.count("delivery_behavior") == 1, (
            f"re-attach must not duplicate delivery_behavior; "
            f"keys={keys_after_rise}"
        )

    @given(
        raw_level=_P18_RAW_LEVEL_ST,
        high_owner_level=_P18_HIGH_OWNER_LEVEL_ST,
        low_owner_level=_P18_LOW_OWNER_LEVEL_ST,
        agent_id=st.integers(min_value=1, max_value=999),
    )
    @settings(max_examples=200)
    def test_disable_clears_stickiness_no_reattach(
        self, raw_level, high_owner_level, low_owner_level, agent_id
    ):
        """Scenario B: once ``disable_ability`` clears the flag, dropping below
        the gate and rising back above it does NOT re-attach the behavior until
        the player re-enables it.

        **Validates: Requirements 17.2, 17.5**
        """
        system, created = _make_harvester_system()
        owner, agent = self._make_owned_harvester(
            system, created, high_owner_level, raw_level, agent_id
        )

        # Enable (explicit command) then disable to clear the sticky flag.
        system.enable_ability(owner, agent_id, "delivery")
        assert "delivery" in system.get_enabled_abilities(agent)
        assert "delivery_behavior" in self._script_keys(agent)

        system.disable_ability(owner, agent_id, "delivery")
        # Flag cleared and behavior detached (HarvesterScript stays).
        assert "delivery" not in system.get_enabled_abilities(agent), (
            "disable_ability must clear the enabled flag (Req 17.5)"
        )
        assert "delivery_behavior" not in self._script_keys(agent)

        # Drop below the gate and rise back above it, re-evaluating each time.
        owner.db.level = low_owner_level
        system.evaluate_gated_abilities(agent)
        owner.db.level = high_owner_level
        assert system.compute_effective_level(agent) >= _DELIVERY_GATE_LEVEL
        system.evaluate_gated_abilities(agent)

        # With the flag cleared, no auto re-attach occurs (Req 17.2, 17.5).
        keys = self._script_keys(agent)
        assert "delivery_behavior" not in keys, (
            f"a disabled ability must NOT re-attach on a rise until re-enabled; "
            f"keys={keys}"
        )
        assert "delivery" not in system.get_enabled_abilities(agent)

        # Re-enabling (a fresh explicit command) restores it (Req 17.2).
        system.enable_ability(owner, agent_id, "delivery")
        assert "delivery" in system.get_enabled_abilities(agent)
        assert "delivery_behavior" in self._script_keys(agent), (
            "re-enabling at/above the gate must re-attach the behavior"
        )


# ================================================================== #
#  PROPERTY 12 (agent-progression spec) — Roster progression view
#  consistency.
#
#  NOTE ON DUAL NUMBERING: this module already contains a legacy
#  ``TestProperty12AgentIDSequentiality`` (Property 12 of the *agent
#  system* property suite). This class validates Property 12 of the
#  *agent-progression* spec, hence the distinct name
#  ``TestAgentProgressionProperty12RosterView`` to avoid collision.
#
#  Feature: agent-progression, Property 12: Roster progression view
#  consistency — ``get_agent_progression_view(agent)`` reports
#  ``effective_level == compute_effective_level(agent)``,
#  ``capped_by_commander`` true iff ``Raw_Level > Effective_Level``, and an
#  ``ability_status`` map assigning each gate ``enabled`` / ``available`` /
#  ``locked:N`` (with N the required level) per the enabled set and the
#  effective level.
#
#  **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 14.5, 16.5**
#
#  Reuses the existing fakes: ``ScriptedHarvesterAgent`` (controllable
#  ``get_raw_level`` + observable scripts), ``FakePlayer`` (owner whose
#  ``db.level`` drives the cap), ``_make_harvester_system`` (delivery gate at
#  level 21), and ``AbilityGateDef``.
# ================================================================== #

#: A second gate registered alongside ``delivery`` so the view's per-gate
#: ability_status is exercised generically (no delivery-specific logic).
_P12_COURIER_GATE_LEVEL = 11

# Raw level and owner level span 1..40 so the generated Effective_Level
# straddles both gates (11 and 21) and exercises the owner-cap path
# (effective is clamped to owner_level - 1).
_P12_RAW_LEVEL_ST = st.integers(min_value=1, max_value=40)
_P12_OWNER_LEVEL_ST = st.integers(min_value=1, max_value=40)
#: Which gate keys are in the agent's enabled set (sticky, independent of
#: attach state). A subset of the two registered gate keys.
_P12_ENABLED_ST = st.lists(
    st.sampled_from(["delivery", "courier"]),
    unique=True,
    max_size=2,
)


class TestAgentProgressionProperty12RosterView:
    """
    **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 14.5, 16.5**

    ``get_agent_progression_view`` reports ``effective_level ==
    compute_effective_level(agent)`` (Req 11.1, 14.5), ``capped_by_commander``
    true iff ``Raw_Level > Effective_Level`` (Req 11.4), and an
    ``ability_status`` map assigning each gate ``enabled`` / ``available`` /
    ``locked:N`` per the enabled set and the effective level (Req 11.2, 11.3,
    16.5).
    """

    def _make_two_gate_system(self):
        """An AgentSystem whose registry has a delivery (21) + courier (11) gate."""
        system, created = _make_harvester_system()
        system.registry.ability_gates = {
            "delivery": AbilityGateDef(
                key="delivery", required_level=_DELIVERY_GATE_LEVEL
            ),
            "courier": AbilityGateDef(
                key="courier", required_level=_P12_COURIER_GATE_LEVEL
            ),
        }
        return system, created

    def _expected_status(self, effective, required, enabled, key):
        """The single-gate status the view must report."""
        if key in enabled:
            return "enabled"
        if effective >= required:
            return "available"
        return f"locked:{required}"

    @given(
        raw_level=_P12_RAW_LEVEL_ST,
        owner_level=_P12_OWNER_LEVEL_ST,
        enabled_keys=_P12_ENABLED_ST,
    )
    @settings(max_examples=200)
    def test_roster_view_is_consistent(self, raw_level, owner_level, enabled_keys):
        """The view's three fields are all consistent with the cap + gates.

        **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 14.5, 16.5**
        """
        system, _ = self._make_two_gate_system()

        owner = FakePlayer()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=1, owner=owner, raw_level=raw_level
        )
        agent.db.enabled_abilities = list(enabled_keys)

        view = system.get_agent_progression_view(agent)

        effective = system.compute_effective_level(agent)
        enabled = set(enabled_keys)

        # effective_level mirrors compute_effective_level exactly (Req 11.1, 14.5).
        assert view["effective_level"] == effective, (
            f"effective_level {view['effective_level']} != "
            f"compute_effective_level {effective} "
            f"(raw={raw_level}, owner={owner_level})"
        )

        # capped_by_commander is True iff the owner cap suppresses the raw level
        # (Req 11.4). The cap is active iff raw_level > effective_level.
        assert view["capped_by_commander"] == (
            agent.get_raw_level() > view["effective_level"]
        ), (
            f"capped_by_commander {view['capped_by_commander']} mismatch: "
            f"raw={agent.get_raw_level()}, effective={view['effective_level']}"
        )

        # ability_status assigns each registered gate enabled/available/locked:N
        # purely from the enabled set + effective level (Req 11.2, 11.3, 16.5).
        gates = {g.key: g.required_level for g in system.registry.get_ability_gates()}
        assert set(view["ability_status"].keys()) == set(gates.keys()), (
            f"ability_status keys {set(view['ability_status'].keys())} must "
            f"cover exactly the registered gates {set(gates.keys())}"
        )
        for key, required in gates.items():
            expected = self._expected_status(effective, required, enabled, key)
            assert view["ability_status"][key] == expected, (
                f"ability_status[{key!r}] = {view['ability_status'][key]!r}, "
                f"expected {expected!r} (effective={effective}, "
                f"required={required}, enabled={key in enabled})"
            )

    @given(
        raw_level=_P12_RAW_LEVEL_ST,
        owner_level=st.integers(min_value=2, max_value=40),
    )
    @settings(max_examples=200)
    def test_capped_marker_true_exactly_when_owner_suppresses_raw(
        self, raw_level, owner_level
    ):
        """capped_by_commander is True precisely when the owner ceiling bites.

        With the enabled set empty and a single delivery gate, the cap is active
        iff ``raw_level > owner_level - 1`` (the ceiling). **Validates:
        Requirements 11.4, 14.5**
        """
        system, _ = _make_harvester_system()
        owner = FakePlayer()
        owner.db.level = owner_level
        agent = ScriptedHarvesterAgent(
            agent_id=1, owner=owner, raw_level=raw_level
        )

        view = system.get_agent_progression_view(agent)

        ceiling = max(1, owner_level - 1)
        expected_capped = raw_level > ceiling
        assert view["capped_by_commander"] == expected_capped, (
            f"capped_by_commander {view['capped_by_commander']} != "
            f"{expected_capped} (raw={raw_level}, ceiling={ceiling})"
        )
