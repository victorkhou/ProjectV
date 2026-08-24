"""
Unit tests for the BranchSystem shared vector services.

Feature: tech-tree-branch-foundation, design section "Components and Interfaces
/ shared vector services" — the three services the six Vector_Systems consume
rather than reimplement, each tested on the axis it exists for:

* **Carrier eligibility (R7.1, R7.5)** — ``eligible_carrier`` is the conjunction
  of the four conditions, so each one is falsified on its own over an otherwise
  eligible agent, and the roster is walked through the *real* ``AgentSystem``
  over an in-memory repository rather than a stand-in that answers a fixed list.
* **Charge and refund (R12.2, R12.3, R8.6)** — the charge is whole or none and
  is the character's own ``has_resources`` / ``deduct_resources`` pair, a refused
  charge leaves every counter untouched, a refund returns the full amount, and
  the have-and-need breakdown is structured data rather than a composed
  sentence (R13.5).
* **Targeting (R10.4, R10.7, R11.8, R11.9, R11.11)** — the new-player shield,
  the allied-target refusal, and the support-consent check, each reporting the
  value its requirement asks be reported, applied to an alliance member and to
  an unaffiliated player on identical terms, and every consent revoked when a
  player leaves an alliance.
* **The three limit ledgers (R8.19, R8.20, R10.6, R10.7)** — the cooldown is per
  building per Operation_Kind and measured against the *injected* clock, the
  in-flight count is the vector's own non-terminal records for that player on
  that planet, and the escalation cap is per attacker per target inside a
  rolling window that prunes itself on read. Each is read against a knob that is
  retuned mid-test, because every one of them must stay hot (R15.7).
* **The Counter_Web advantage and the Response_Window floor (R8.8, R9.4, R9.5)**
  — the advantage is one lookup clamped into ``[1.0, counter_advantage_cap]``,
  exactly ``1.0`` where the web names no edge, and never the product of a chain;
  the window is ``max(floor, base - reduction)``, so no reduction takes a hostile
  operation below the floor. Both knobs are retuned mid-test as well.
* **Registration and the tick fan-out (R8.10, R15.9)** — one duck-typed call keys
  a Vector_System by its own Operation_Kind, re-registering a kind rewires it
  rather than doubling it, and one tick step advances every registered vector
  with each one isolated, so a vector that raises leaves the others advanced and
  is tried again next tick. An empty registry — the state this feature ships in —
  is a no-op.

The property modules own the input space (Properties 11, 14, 17, 18, 19, and 20);
these are the fixed, concrete claims.

**Validates: Requirements 7.1, 7.5, 8.8, 8.10, 8.19, 8.20, 9.4, 9.5, 10.4, 10.6,
10.7, 11.8, 11.9, 11.11, 12.2, 12.3, 15.9**
"""

import unittest
from types import SimpleNamespace

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (  # noqa: E402
    CANONICAL_COUNTER_WEB,
    FakeAttributes,
    FakeBuilding,
    FakeDB,
    FakePlayer,
    fixture_registry,
    make_registry,
)

from mygame.world.constants import (  # noqa: E402
    ATTR_VECTOR_CONSENT,
    ATTR_VECTOR_COOLDOWNS,
    ATTR_VECTOR_ESCALATION,
    CONSENT_SUPPORT,
    CONSENT_TARGET_SHARING,
)
from mygame.world.definitions import OperationKindDef  # noqa: E402
from mygame.world.event_bus import ALLIANCE_MEMBER_LEFT, EventBus  # noqa: E402
from mygame.world.systems.agent_system import AgentSystem  # noqa: E402
from mygame.world.systems.branch_system import (  # noqa: E402
    MSG_VECTOR_CONSENT_REQUIRED,
    MSG_VECTOR_ESCALATION_LIMIT,
    MSG_VECTOR_TARGET_ALLIED,
    MSG_VECTOR_TARGET_SHIELDED,
    BranchSystem,
)

HOME = "earth"
AWAY = "mars"
ROLE = "spotter"


class _Agent:
    """A framework-free agent NPC: the four eligibility flags and nothing else."""

    def __init__(
        self,
        owner=None,
        role=ROLE,
        planet=HOME,
        hp=100,
        reserve=False,
        incapacitated=False,
        agent_id=1,
    ):
        self.key = f"Agent #{agent_id}"
        self.attributes = FakeAttributes({
            "agent_id": agent_id,
            "owner": owner,
            "npc_type": "agent",
            "role": role,
            "coord_planet": planet,
            "hp": hp,
            "reserve": reserve,
            "incapacitated": incapacitated,
        })
        self.db = FakeDB(self.attributes)


class _Roster:
    """The ``AgentRepository`` port over an in-memory list (identity ownership)."""

    def __init__(self, agents=()):
        self.agents = list(agents)

    def find_agents_for_owner(self, owner):
        return [agent for agent in self.agents if agent.db.owner is owner]

    def find_all_agents(self):
        return list(self.agents)

    def find_all_enemies(self):
        return []

    def find_training_buildings(self):
        return []


class _SimpleAlliance:
    """An AllianceSystem stand-in over an explicit set of allied pairs.

    Exposes exactly the three reads the targeting and consent paths make of the
    injected collaborator: the ``are_allied`` predicate, the ``alliance_summary``
    a refusal quotes, and the ``_live_members`` roster the revocation walks.
    """

    def __init__(self, pairs=(), members=(), name="The Pact", tag="PCT"):
        self._pairs = [(a, b) for a, b in pairs]
        self.members = list(members)
        self.name = name
        self.tag = tag

    def are_allied(self, first, second):
        if first is second:
            return False
        return any(
            (first is a and second is b) or (first is b and second is a)
            for a, b in self._pairs
        )

    def alliance_summary(self, alliance_id, **_kwargs):
        return {"name": self.name, "tag": self.tag}

    def _live_members(self, _alliance_id):
        return list(self.members)


def _system(alliance=None, agents=None, bus=None, registry=None, clock=None):
    """A BranchSystem over the fixture catalog with the named collaborators."""
    return BranchSystem(
        registry if registry is not None else fixture_registry(),
        bus if bus is not None else EventBus(),
        current_tick_func=clock,
        agent_system=agents,
        alliance_system=alliance,
    )


def _agent_system(*agents):
    """A REAL AgentSystem whose roster is the in-memory repository."""
    return AgentSystem(
        registry=fixture_registry(),
        event_bus=EventBus(),
        agent_repository=_Roster(agents),
    )


class _Clock:
    """The injected tick source, driven by the test rather than by a script."""

    def __init__(self, tick: int = 0, broken: bool = False):
        self.tick = tick
        self.broken = broken

    def __call__(self) -> int:
        if self.broken:
            raise RuntimeError("the tick script cannot be read")
        return self.tick


class _Vector:
    """A Vector_System stand-in holding a tracked-record list.

    Exposes what the in-flight count actually reaches for: an
    ``operation_kind`` and the driver's own ``_tracked`` list. ``accessor``
    switches it to the public ``tracked_records()`` form the count prefers, and
    ``broken`` makes that accessor raise, so both shapes and the degraded case
    are exercised against the same fake.
    """

    def __init__(self, kind, records=(), accessor=False, broken=False):
        self.operation_kind = kind
        self._tracked = list(records)
        self._exposed = list(records)
        self._broken = broken
        if accessor or broken:
            # Only the accessor can answer, so a count that arrives is proof
            # the public form was preferred over the private list.
            self._tracked = []
            self.tracked_records = self._records

    def _records(self):
        if self._broken:
            raise RuntimeError("this vector cannot enumerate its records")
        return list(self._exposed)


class _TickVector:
    """A Vector_System stand-in recording every tick its ``advance_all`` saw.

    The fan-out's counterpart to :class:`_Vector`: it exposes the other half of
    what a vector is duck-typed on. ``broken`` makes ``advance_all`` raise *after*
    recording the call, so a test can prove the vector was reached and that the
    raise was contained; ``advances=False`` drops ``advance_all`` altogether,
    which is the unwired-collaborator case (R15.2).
    """

    def __init__(self, kind, broken=False, advances=True, on_advance=None):
        self.operation_kind = kind
        self.ticks = []
        self._broken = broken
        self._on_advance = on_advance
        if advances:
            self.advance_all = self._advance

    def _advance(self, tick):
        self.ticks.append(tick)
        if self._on_advance is not None:
            self._on_advance()
        if self._broken:
            raise RuntimeError("this vector cannot advance")


class _NamelessVector:
    """A vector whose ``operation_kind`` raises rather than answering."""

    def __init__(self):
        self.ticks = []

    @property
    def operation_kind(self):
        raise RuntimeError("this vector cannot name its kind")

    def advance_all(self, tick):
        self.ticks.append(tick)


def _register(system, vector):
    """Register *vector* with *system* through the public call, returning it.

    A one-line wrapper purely so a test reads ``vector = _register(...)``:
    ``register_vector`` answers ``None``, since a caller at the composition root
    already holds the vector it is registering.
    """
    system.register_vector(vector)
    return vector


def _record(owner=None, kind="trap", planet=HOME, state="pending", **extra):
    """One Operation_Record-shaped object: read by attribute."""
    return SimpleNamespace(
        op_id=f"op-{id(owner)}-{state}",
        kind=kind,
        owner_ref=owner,
        planet=planet,
        state=state,
        **extra,
    )


def _persisted(owner=None, kind="trap", planet=HOME, state="pending"):
    """The same record in its PERSISTED shape: a plain dict, read by key."""
    return {
        "op_id": "op-persisted",
        "kind": kind,
        "owner_ref": owner,
        "planet": planet,
        "state": state,
    }


# ================================================================== #
#  Requirements 7.1, 7.5 — carrier eligibility
# ================================================================== #

class TestEligibleCarrier(unittest.TestCase):
    """``eligible_carrier`` is the conjunction of the four conditions (R7.5)."""

    def _owner_with(self, **flags):
        """An owner on HOME plus one agent carrying *flags*."""
        owner = FakePlayer(planet=HOME)
        agent = _Agent(owner=owner, **flags)
        return owner, agent, _system(agents=_agent_system(agent))

    def test_an_agent_meeting_all_four_conditions_is_returned(self):
        owner, agent, system = self._owner_with()

        self.assertIs(system.eligible_carrier(owner, ROLE), agent)

    def test_a_dead_agent_is_not_eligible(self):
        owner, _agent, system = self._owner_with(hp=0)

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_an_agent_in_another_role_is_not_eligible(self):
        owner, _agent, system = self._owner_with(role="sapper")

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_an_unassigned_agent_is_not_eligible(self):
        owner, _agent, system = self._owner_with(role="")

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_a_reserved_agent_is_not_eligible(self):
        owner, _agent, system = self._owner_with(reserve=True)

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_an_incapacitated_agent_is_not_eligible(self):
        owner, _agent, system = self._owner_with(incapacitated=True)

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_the_role_comparison_ignores_case(self):
        owner, agent, system = self._owner_with(role=ROLE.upper())

        self.assertIs(system.eligible_carrier(owner, ROLE), agent)

    def test_an_agent_on_another_planet_is_not_eligible(self):
        owner, _agent, system = self._owner_with(planet=AWAY)

        self.assertIsNone(system.eligible_carrier(owner, ROLE, planet=HOME))

    def test_the_planet_defaults_to_the_one_the_owner_occupies(self):
        owner, _agent, system = self._owner_with(planet=AWAY)

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_the_first_eligible_agent_of_several_is_returned(self):
        owner = FakePlayer(planet=HOME)
        benched = _Agent(owner=owner, reserve=True, agent_id=1)
        ready = _Agent(owner=owner, agent_id=2)
        system = _system(agents=_agent_system(benched, ready))

        self.assertIs(system.eligible_carrier(owner, ROLE), ready)

    def test_another_players_agent_is_never_eligible(self):
        owner = FakePlayer(planet=HOME)
        stranger = FakePlayer(planet=HOME)
        system = _system(agents=_agent_system(_Agent(owner=stranger)))

        self.assertIsNone(system.eligible_carrier(owner, ROLE))

    def test_no_agent_system_injected_answers_none(self):
        # R15.2: an unwired collaborator degrades to a refusal, not an error.
        self.assertIsNone(_system().eligible_carrier(FakePlayer(), ROLE))

    def test_unresolvable_input_answers_none(self):
        system = _system(agents=_agent_system())
        for player, role in ((None, ROLE), (FakePlayer(), ""), (None, None)):
            with self.subTest(player=player, role=role):
                self.assertIsNone(system.eligible_carrier(player, role))


# ================================================================== #
#  Requirements 12.2, 12.3, 8.6 — charge, refund, and the breakdown
# ================================================================== #

class TestChargeAndRefund(unittest.TestCase):
    """The charge is whole or none, and a refused charge writes nothing."""

    def setUp(self):
        self.system = _system()
        self.player = FakePlayer(resources={"Iron": 10, "Wood": 4})

    def test_a_covered_cost_is_charged_in_full(self):
        self.assertTrue(self.system.charge(self.player, {"Iron": 10, "Wood": 4}))

        self.assertEqual(self.player.get_resource("Iron"), 0)
        self.assertEqual(self.player.get_resource("Wood"), 0)

    def test_an_uncovered_cost_charges_nothing_at_all(self):
        before = self.player.resource_snapshot()

        self.assertFalse(self.system.charge(self.player, {"Iron": 4, "Wood": 99}))

        self.assertEqual(self.player.resource_snapshot(), before)

    def test_an_empty_cost_succeeds_and_writes_nothing(self):
        # R12.6: an NPC-originated operation charges nothing.
        before = self.player.resource_snapshot()

        for cost in ({}, None, {"Iron": 0}):
            with self.subTest(cost=cost):
                self.assertTrue(self.system.charge(self.player, cost))
        self.assertEqual(self.player.resource_snapshot(), before)

    def test_a_refund_returns_the_whole_charged_amount(self):
        before = self.player.resource_snapshot()
        cost = {"Iron": 7, "Wood": 2}
        self.system.charge(self.player, cost)

        self.system.refund(self.player, cost)

        self.assertEqual(self.player.resource_snapshot(), before)

    def test_a_player_with_no_resource_methods_is_not_charged(self):
        self.assertFalse(self.system.charge(object(), {"Iron": 1}))

    def test_the_shortfall_breakdown_reports_every_line(self):
        breakdown = self.system.resource_shortfall(
            self.player, {"Iron": 25, "Wood": 4}
        )

        self.assertEqual(breakdown, {
            "Iron": {"have": 10, "need": 25},
            "Wood": {"have": 4, "need": 4},
        })

    def test_the_shortfall_of_nothing_is_empty(self):
        self.assertEqual(self.system.resource_shortfall(self.player, {}), {})
        self.assertEqual(self.system.resource_shortfall(None, {"Iron": 1}), {})

    def test_neither_half_raises_on_unreadable_input(self):
        # R15.3: a request path reads a value, it does not guard a call.
        self.assertFalse(self.system.charge(None, {"Iron": 1}))
        self.assertIsNone(self.system.refund(None, {"Iron": 1}))
        self.assertIsNone(self.system.refund(object(), {"Iron": 1}))


# ================================================================== #
#  Requirements 10.4, 10.7, 11.8, 11.9 — may_target
# ================================================================== #

class TestMayTarget(unittest.TestCase):
    """The three protection gates, folded into one answer."""

    def setUp(self):
        self.registry = fixture_registry()
        self.shield = self.registry.balance.new_player_vector_shield_level

    def _veteran(self):
        return FakePlayer(key="Vet", level=self.shield + 5)

    def _newcomer(self):
        return FakePlayer(key="New", level=max(1, self.shield - 1))

    def test_an_unaffiliated_veteran_target_is_permitted(self):
        actor, target = self._veteran(), self._veteran()

        self.assertIsNone(_system().may_target(actor, target))

    def test_a_target_below_the_shield_level_is_refused(self):
        actor, target = self._veteran(), self._newcomer()

        refusal = _system(registry=self.registry).may_target(actor, target)

        self.assertEqual(refusal, MSG_VECTOR_TARGET_SHIELDED)
        self.assertEqual(refusal.data["required_level"], self.shield)
        self.assertEqual(refusal.data["target_level"], target.db.level)
        self.assertEqual(refusal.data["target_name"], "New")

    def test_the_shield_reads_the_same_for_an_alliance_member(self):
        # R10.7: an alliance changes neither whether a gate fires nor what it
        # reports, so a shielded ally answers with the qualifying level too.
        actor, target = self._veteran(), self._newcomer()
        alliance = _SimpleAlliance(pairs=[(actor, target)])

        refusal = _system(alliance=alliance, registry=self.registry).may_target(
            actor, target
        )

        self.assertEqual(refusal, MSG_VECTOR_TARGET_SHIELDED)
        self.assertEqual(refusal.data["required_level"], self.shield)

    def test_an_allied_target_is_refused_and_names_the_alliance(self):
        actor, target = self._veteran(), self._veteran()
        target.db.player_alliance = 7
        alliance = _SimpleAlliance(pairs=[(actor, target)])

        refusal = _system(alliance=alliance).may_target(actor, target)

        self.assertEqual(refusal, MSG_VECTOR_TARGET_ALLIED)
        self.assertEqual(refusal.data["alliance"], 7)
        self.assertEqual(refusal.data["alliance_name"], "The Pact")

    def test_a_building_resolves_to_its_owner(self):
        actor, owner = self._veteran(), self._newcomer()
        building = FakeBuilding(building_type="WL", owner=owner, planet=HOME)

        refusal = _system(registry=self.registry).may_target(actor, building)

        self.assertEqual(refusal, MSG_VECTOR_TARGET_SHIELDED)

    def test_a_players_own_entity_is_always_targetable(self):
        actor = self._newcomer()
        own = FakeBuilding(building_type="WL", owner=actor, planet=HOME)

        self.assertIsNone(_system(registry=self.registry).may_target(actor, own))

    def test_an_npc_base_is_not_shielded(self):
        # R11.6: an NPC base is the practice target a new player is meant to have.
        actor = self._newcomer()
        sentinel = FakePlayer(key="Sentinel", level=1)
        sentinel.db.is_sentinel = True

        self.assertIsNone(_system(registry=self.registry).may_target(actor, sentinel))

    def test_an_unresolvable_target_is_permitted(self):
        # A lookup failure must never suppress legitimate targeting (R15.3).
        self.assertIsNone(_system().may_target(self._veteran(), None))

    def test_a_supporting_operation_needs_the_allys_consent(self):
        actor, ally = self._veteran(), self._veteran()
        system = _system(alliance=_SimpleAlliance(pairs=[(actor, ally)]))

        refusal = system.may_target(actor, ally, hostile=False)

        self.assertEqual(refusal, MSG_VECTOR_CONSENT_REQUIRED)
        self.assertEqual(refusal.data["consent"], CONSENT_SUPPORT)
        self.assertEqual(refusal.data["ally_name"], "Vet")

    def test_a_supporting_operation_proceeds_once_consent_is_granted(self):
        actor, ally = self._veteran(), self._veteran()
        system = _system(alliance=_SimpleAlliance(pairs=[(actor, ally)]))
        system.grant_consent(ally, CONSENT_SUPPORT, actor)

        self.assertIsNone(system.may_target(actor, ally, hostile=False))

    def test_a_consent_from_someone_else_does_not_count(self):
        actor, ally, other = self._veteran(), self._veteran(), self._veteran()
        system = _system(alliance=_SimpleAlliance(pairs=[(actor, ally)]))
        system.grant_consent(ally, CONSENT_SUPPORT, other)

        self.assertEqual(
            system.may_target(actor, ally, hostile=False), MSG_VECTOR_CONSENT_REQUIRED
        )

    def test_supporting_a_non_ally_needs_no_consent(self):
        actor, stranger = self._veteran(), self._veteran()

        self.assertIsNone(_system().may_target(actor, stranger, hostile=False))


# ================================================================== #
#  Requirement 11.11 — the consent store and its revocation
# ================================================================== #

class TestConsentStore(unittest.TestCase):
    """The second persisted player state, and this system as its single writer."""

    def setUp(self):
        self.giver = FakePlayer(key="Giver")
        self.ally = FakePlayer(key="Ally")
        self.alliance = _SimpleAlliance(
            pairs=[(self.giver, self.ally)], members=[self.giver, self.ally]
        )
        self.system = _system(alliance=self.alliance)

    def test_a_granted_consent_reads_back(self):
        self.assertTrue(
            self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)
        )

        self.assertTrue(
            self.system.has_consent(self.giver, CONSENT_SUPPORT, self.ally)
        )

    def test_the_two_kinds_are_independent(self):
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)

        self.assertFalse(
            self.system.has_consent(self.giver, CONSENT_TARGET_SHARING, self.ally)
        )

    def test_a_consent_is_directional(self):
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)

        self.assertFalse(
            self.system.has_consent(self.ally, CONSENT_SUPPORT, self.giver)
        )

    def test_a_consent_survives_only_while_the_two_are_allied(self):
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)
        stranger = _system(alliance=_SimpleAlliance())

        self.assertFalse(
            stranger.has_consent(self.giver, CONSENT_SUPPORT, self.ally)
        )

    def test_granting_twice_is_idempotent(self):
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)

        stored = getattr(self.giver.db, ATTR_VECTOR_CONSENT)
        self.assertEqual(stored, {CONSENT_SUPPORT: {self.ally.id: True}})

    def test_revoking_clears_the_entry_and_the_empty_kind(self):
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)

        self.assertTrue(
            self.system.revoke_consent(self.giver, CONSENT_SUPPORT, self.ally)
        )

        self.assertEqual(getattr(self.giver.db, ATTR_VECTOR_CONSENT), {})

    def test_revoking_what_was_never_granted_reports_false(self):
        self.assertFalse(
            self.system.revoke_consent(self.giver, CONSENT_SUPPORT, self.ally)
        )

    def test_an_unknown_kind_is_refused_rather_than_stored(self):
        self.assertFalse(self.system.grant_consent(self.giver, "gossip", self.ally))
        self.assertFalse(self.system.has_consent(self.giver, "gossip", self.ally))

    def test_a_garbage_store_reads_as_no_consent(self):
        # R14.8: a hand-edited value collapses to the documented default.
        for garbage in (None, 0, "nope", [1, 2], {CONSENT_SUPPORT: "yes"}):
            with self.subTest(garbage=garbage):
                setattr(self.giver.db, ATTR_VECTOR_CONSENT, garbage)

                self.assertFalse(
                    self.system.has_consent(self.giver, CONSENT_SUPPORT, self.ally)
                )

    def test_leaving_an_alliance_revokes_both_directions(self):
        self.system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)
        self.system.grant_consent(self.giver, CONSENT_TARGET_SHARING, self.ally)
        self.system.grant_consent(self.ally, CONSENT_SUPPORT, self.giver)

        changed = self.system.revoke_alliance_consents(self.giver, alliance_id=3)

        self.assertEqual(changed, 2)
        self.assertEqual(getattr(self.giver.db, ATTR_VECTOR_CONSENT), {})
        self.assertEqual(getattr(self.ally.db, ATTR_VECTOR_CONSENT), {})

    def test_the_alliance_member_left_event_revokes(self):
        bus = EventBus()
        system = _system(alliance=self.alliance, bus=bus)
        system.grant_consent(self.giver, CONSENT_SUPPORT, self.ally)

        bus.publish(ALLIANCE_MEMBER_LEFT, alliance_id=3, player=self.giver)

        self.assertEqual(getattr(self.giver.db, ATTR_VECTOR_CONSENT), {})

    def test_a_revocation_with_nothing_stored_changes_nothing(self):
        self.assertEqual(self.system.revoke_alliance_consents(self.giver, 3), 0)

    def test_a_revocation_never_raises_on_unreadable_input(self):
        self.assertEqual(self.system.revoke_alliance_consents(None), 0)
        self.assertIsNone(self.system.on_alliance_member_left(player=None))


# ================================================================== #
#  Requirement 8.19 — the per-building, per-kind cooldown ledger
# ================================================================== #

class TestCooldownLedger(unittest.TestCase):
    """The cooldown is per originating building per Operation_Kind."""

    KIND = "trap"
    OTHER_KIND = "strategic_strike"

    def setUp(self):
        self.registry = fixture_registry()
        self.length = self.registry.balance.trap_cooldown_ticks
        self.clock = _Clock(100)
        self.system = _system(registry=self.registry, clock=self.clock)
        self.building = FakeBuilding(building_type="WL", planet=HOME)

    def test_a_building_that_never_ran_the_kind_is_ready(self):
        self.assertEqual(self.system.cooldown_remaining(self.building, self.KIND), 0)

    def test_noting_a_cooldown_reports_the_configured_length(self):
        self.system.note_cooldown(self.building, self.KIND)

        self.assertEqual(
            self.system.cooldown_remaining(self.building, self.KIND), self.length
        )

    def test_the_remaining_ticks_are_the_length_minus_the_elapsed_ticks(self):
        self.system.note_cooldown(self.building, self.KIND)

        for elapsed in (1, self.length // 2, self.length - 1):
            with self.subTest(elapsed=elapsed):
                self.clock.tick = 100 + elapsed

                self.assertEqual(
                    self.system.cooldown_remaining(self.building, self.KIND),
                    self.length - elapsed,
                )

    def test_the_cooldown_is_elapsed_once_the_length_has_passed(self):
        self.system.note_cooldown(self.building, self.KIND)

        for elapsed in (self.length, self.length + 1, self.length * 10):
            with self.subTest(elapsed=elapsed):
                self.clock.tick = 100 + elapsed

                self.assertEqual(
                    self.system.cooldown_remaining(self.building, self.KIND), 0
                )

    def test_the_ledger_records_the_ready_at_tick_on_the_building(self):
        self.system.note_cooldown(self.building, self.KIND)

        self.assertEqual(
            getattr(self.building.db, ATTR_VECTOR_COOLDOWNS),
            {self.KIND: 100 + self.length},
        )

    def test_two_buildings_cool_down_independently(self):
        other = FakeBuilding(building_type="WL", planet=HOME)

        self.system.note_cooldown(self.building, self.KIND)

        self.assertEqual(self.system.cooldown_remaining(other, self.KIND), 0)

    def test_two_kinds_on_one_building_cool_down_independently(self):
        self.system.note_cooldown(self.building, self.KIND)

        self.assertEqual(
            self.system.cooldown_remaining(self.building, self.OTHER_KIND), 0
        )

    def test_noting_a_second_kind_keeps_the_first(self):
        # Read-copy-write (R14.7): the hostile store discards in-place mutation,
        # so an entry survives only if the whole container was written back.
        building = FakeBuilding(building_type="WL", planet=HOME, hostile=True)
        other_length = self.registry.balance.strategic_strike_cooldown_ticks
        self.system.note_cooldown(building, self.KIND)

        self.system.note_cooldown(building, self.OTHER_KIND)

        self.assertEqual(
            getattr(building.db, ATTR_VECTOR_COOLDOWNS),
            {self.KIND: 100 + self.length, self.OTHER_KIND: 100 + other_length},
        )

    def test_re_noting_the_same_kind_moves_the_clock_forward(self):
        self.system.note_cooldown(self.building, self.KIND)
        self.clock.tick = 200

        self.system.note_cooldown(self.building, self.KIND)

        self.assertEqual(
            self.system.cooldown_remaining(self.building, self.KIND), self.length
        )

    def test_the_length_is_read_per_call_so_a_retune_lands(self):
        # R15.7: an @reload retunes the live game, so nothing may be cached.
        self.registry.balance.trap_cooldown_ticks = self.length + 30
        self.system.note_cooldown(self.building, self.KIND)

        self.assertEqual(
            self.system.cooldown_remaining(self.building, self.KIND), self.length + 30
        )

    def test_a_retune_to_zero_frees_a_building_that_is_still_cooling(self):
        self.system.note_cooldown(self.building, self.KIND)
        self.registry.balance.trap_cooldown_ticks = 0

        self.assertEqual(self.system.cooldown_remaining(self.building, self.KIND), 0)

    def test_the_balance_field_binding_comes_from_the_kind_registry(self):
        # The BINDING is data: an entry may name any field, and the value is
        # read from the field it names.
        registry = make_registry(
            operation_kinds={
                self.KIND: OperationKindDef(
                    kind=self.KIND,
                    branch="defense",
                    carrier_role="sapper",
                    cost_field="trap_cost",
                    cooldown_field="strategic_strike_cooldown_ticks",
                    cap_field="trap_max_in_flight",
                    agent_xp_field="agent_xp_trap",
                )
            },
        )
        system = _system(registry=registry, clock=self.clock)

        system.note_cooldown(self.building, self.KIND)

        self.assertEqual(
            system.cooldown_remaining(self.building, self.KIND),
            registry.balance.strategic_strike_cooldown_ticks,
        )

    def test_a_stored_tick_from_a_lost_clock_cannot_outlast_the_length(self):
        # The shipped tick source answers 0 when it cannot read the tick script,
        # so a far-future ready_at must clamp to one cooldown rather than lock
        # the building out for hundreds of ticks.
        setattr(self.building.db, ATTR_VECTOR_COOLDOWNS, {self.KIND: 999_999})

        self.clock.tick = 0

        self.assertEqual(
            self.system.cooldown_remaining(self.building, self.KIND), self.length
        )

    def test_a_clock_that_cannot_be_read_enforces_nothing(self):
        system = _system(registry=self.registry, clock=_Clock(broken=True))
        setattr(self.building.db, ATTR_VECTOR_COOLDOWNS, {self.KIND: 999_999})

        self.assertEqual(system.cooldown_remaining(self.building, self.KIND), 0)
        self.assertIsNone(system.note_cooldown(self.building, self.KIND))

    def test_a_garbage_ledger_reads_as_ready(self):
        # R14.8: a hand-edited value collapses to the documented default.
        for garbage in (None, 0, "soon", [1, 2], {self.KIND: "soon"}, {"": 5}):
            with self.subTest(garbage=garbage):
                setattr(self.building.db, ATTR_VECTOR_COOLDOWNS, garbage)

                self.assertEqual(
                    self.system.cooldown_remaining(self.building, self.KIND), 0
                )

    def test_neither_half_raises_on_unreadable_input(self):
        for building, kind in ((None, self.KIND), (self.building, ""), (None, None)):
            with self.subTest(building=building, kind=kind):
                self.assertEqual(self.system.cooldown_remaining(building, kind), 0)
                self.assertIsNone(self.system.note_cooldown(building, kind))


# ================================================================== #
#  Requirement 8.20 — the in-flight count and cap
# ================================================================== #

class TestInFlightLedger(unittest.TestCase):
    """The count is the vector's own non-terminal records; there is no ledger."""

    KIND = "trap"

    def setUp(self):
        self.registry = fixture_registry()
        self.cap = self.registry.balance.trap_max_in_flight
        self.system = _system(registry=self.registry)
        self.player = FakePlayer(key="Owner", planet=HOME, player_id=42)

    def _tracking(self, *records, **kwargs):
        """Register a vector for ``KIND`` tracking *records*."""
        return _register(self.system, _Vector(self.KIND, records, **kwargs))

    def test_a_kind_no_vector_is_registered_for_counts_nothing(self):
        # R15.2: an unwired collaborator degrades to a value, not an error.
        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 0)

    def test_the_players_own_pending_records_are_counted(self):
        self._tracking(_record(self.player), _record(self.player))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 2)

    def test_a_suspended_record_is_still_in_flight(self):
        self._tracking(_record(self.player, state="suspended"))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 1)

    def test_a_terminal_record_is_not_in_flight(self):
        # R8.2: Resolved, Expired, Cancelled, and Discarded are terminal.
        for state in ("resolved", "expired", "cancelled", "discarded"):
            with self.subTest(state=state):
                self.system._vectors.clear()
                self._tracking(_record(self.player, state=state))

                self.assertEqual(
                    self.system.in_flight_count(self.player, self.KIND), 0
                )

    def test_a_record_whose_state_cannot_be_read_is_counted(self):
        # A tracked record is in flight until it says otherwise, and counting it
        # is the conservative direction for a cap.
        self._tracking(_record(self.player, state=None))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 1)

    def test_another_players_record_is_not_counted(self):
        stranger = FakePlayer(key="Stranger", planet=HOME, player_id=7)
        self._tracking(_record(stranger))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 0)

    def test_an_owner_reference_by_id_and_by_dbref_both_count(self):
        self._tracking(_record(self.player.id), _record(f"#{self.player.id}"))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 2)

    def test_an_unattributable_record_is_counted_against_nobody(self):
        self._tracking(_record(None))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 0)

    def test_another_kinds_record_on_the_same_vector_is_not_counted(self):
        self._tracking(_record(self.player, kind="strategic_strike"))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 0)

    def test_another_planets_record_is_not_counted(self):
        self._tracking(_record(self.player, planet=AWAY))

        self.assertEqual(
            self.system.in_flight_count(self.player, self.KIND, planet=HOME), 0
        )

    def test_the_planet_defaults_to_the_one_the_owner_occupies(self):
        self._tracking(
            _record(self.player, planet=HOME), _record(self.player, planet=AWAY)
        )

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 1)

    def test_a_record_with_no_planet_counts_on_every_planet(self):
        self._tracking(_record(self.player, planet=None))

        for planet in (HOME, AWAY):
            with self.subTest(planet=planet):
                self.assertEqual(
                    self.system.in_flight_count(self.player, self.KIND, planet), 1
                )

    def test_the_persisted_dict_shape_counts_the_same(self):
        self._tracking(_persisted(self.player), _persisted(self.player, state="resolved"))

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 1)

    def test_the_public_accessor_is_preferred_over_the_tracked_list(self):
        self._tracking(_record(self.player), accessor=True)

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 1)

    def test_a_vector_that_cannot_enumerate_counts_nothing(self):
        self._tracking(_record(self.player), broken=True)

        self.assertEqual(self.system.in_flight_count(self.player, self.KIND), 0)

    def test_the_cap_is_the_balance_field_named_for_the_kind(self):
        self.assertEqual(self.system.in_flight_cap(self.KIND), self.cap)

    def test_the_cap_is_read_per_call_so_a_retune_lands(self):
        self.registry.balance.trap_max_in_flight = self.cap + 3

        self.assertEqual(self.system.in_flight_cap(self.KIND), self.cap + 3)

    def test_an_unconfigured_or_negative_cap_reads_as_unbounded(self):
        # 0 means "no cap configured", never "refuse everything".
        self.registry.balance.trap_max_in_flight = -5

        self.assertEqual(self.system.in_flight_cap(self.KIND), 0)
        self.assertEqual(self.system.in_flight_cap("no_such_kind"), 0)
        self.assertEqual(self.system.in_flight_cap(""), 0)

    def test_the_count_never_raises_on_unreadable_input(self):
        self._tracking(_record(self.player))

        self.assertEqual(self.system.in_flight_count(None, self.KIND), 0)
        self.assertEqual(self.system.in_flight_count(self.player, None), 0)


# ================================================================== #
#  Requirements 10.6, 10.7 — the escalation ledger
# ================================================================== #

class TestEscalationLedger(unittest.TestCase):
    """Per attacker per target, inside a rolling window, alliance-blind."""

    def setUp(self):
        self.registry = fixture_registry()
        self.cap = self.registry.balance.escalation_cap
        self.window = self.registry.balance.escalation_window_ticks
        self.shield = self.registry.balance.new_player_vector_shield_level
        self.clock = _Clock(1000)
        self.system = _system(registry=self.registry, clock=self.clock)
        self.actor = FakePlayer(key="Vet", level=self.shield + 5, player_id=1)
        self.target = FakePlayer(key="Foe", level=self.shield + 5, player_id=2)

    def _resolve(self, times, target=None, step=0):
        """Note *times* resolutions against *target*, *step* ticks apart."""
        for _ in range(times):
            self.system.note_escalation(self.actor, target or self.target)
            self.clock.tick += step

    def test_an_actor_who_has_resolved_nothing_is_unthrottled(self):
        self.assertEqual(self.system.escalation_remaining(self.actor, self.target), 0)

    def test_staying_under_the_cap_is_unthrottled(self):
        self._resolve(self.cap - 1)

        self.assertEqual(self.system.escalation_remaining(self.actor, self.target), 0)

    def test_reaching_the_cap_reports_the_ticks_until_a_slot_frees(self):
        self._resolve(self.cap, step=5)
        oldest = 1000

        self.assertEqual(
            self.system.escalation_remaining(self.actor, self.target),
            oldest + self.window - self.clock.tick,
        )

    def test_the_oldest_entry_ages_out_of_the_window(self):
        self._resolve(self.cap, step=5)

        self.clock.tick = 1000 + self.window

        self.assertEqual(self.system.escalation_remaining(self.actor, self.target), 0)

    def test_the_ledger_records_the_resolution_ticks_on_the_attacker(self):
        self._resolve(2, step=10)

        self.assertEqual(
            getattr(self.actor.db, ATTR_VECTOR_ESCALATION),
            {self.target.id: [1000, 1010]},
        )

    def test_each_target_is_throttled_separately(self):
        other = FakePlayer(key="Other", level=self.shield + 5, player_id=3)
        self._resolve(self.cap)

        self.assertEqual(self.system.escalation_remaining(self.actor, other), 0)
        self.assertGreater(
            self.system.escalation_remaining(self.actor, self.target), 0
        )

    def test_noting_a_second_target_keeps_the_first(self):
        # Read-copy-write (R14.7) against the hostile store; see the cooldown.
        actor = FakePlayer(key="Vet", level=self.shield + 5, player_id=1, hostile=True)
        other = FakePlayer(key="Other", level=self.shield + 5, player_id=3)
        self.system.note_escalation(actor, self.target)

        self.system.note_escalation(actor, other)

        self.assertEqual(
            getattr(actor.db, ATTR_VECTOR_ESCALATION),
            {self.target.id: [1000], other.id: [1000]},
        )

    def test_a_targets_building_counts_against_that_target(self):
        building = FakeBuilding(building_type="WL", owner=self.target, planet=HOME)
        self._resolve(self.cap, target=building)

        self.assertGreater(
            self.system.escalation_remaining(self.actor, self.target), 0
        )

    def test_both_knobs_are_read_per_call_so_a_retune_lands(self):
        # R15.7: raising the cap frees the actor on the very next request.
        self._resolve(self.cap)

        self.registry.balance.escalation_cap = self.cap + 1

        self.assertEqual(self.system.escalation_remaining(self.actor, self.target), 0)

    def test_a_cap_retuned_downward_waits_on_the_entry_that_frees_a_slot(self):
        self._resolve(self.cap, step=5)
        self.registry.balance.escalation_cap = 1

        # With a cap of one, the slot frees when the NEWEST entry ages out.
        newest = 1000 + (self.cap - 1) * 5
        self.assertEqual(
            self.system.escalation_remaining(self.actor, self.target),
            newest + self.window - self.clock.tick,
        )

    def test_an_unconfigured_cap_or_window_enforces_nothing(self):
        self._resolve(self.cap)

        for field in ("escalation_cap", "escalation_window_ticks"):
            with self.subTest(field=field):
                registry = fixture_registry()
                setattr(registry.balance, field, 0)
                system = _system(registry=registry, clock=self.clock)

                self.assertEqual(
                    system.escalation_remaining(self.actor, self.target), 0
                )

    def test_entries_from_a_clock_that_went_backwards_are_dropped(self):
        # The shipped tick source answers 0 when it cannot read the tick script;
        # an entry in the future describes no past resolution.
        setattr(
            self.actor.db,
            ATTR_VECTOR_ESCALATION,
            {self.target.id: [self.clock.tick + 50] * self.cap},
        )

        self.assertEqual(self.system.escalation_remaining(self.actor, self.target), 0)

    def test_a_clock_that_cannot_be_read_enforces_nothing(self):
        system = _system(registry=self.registry, clock=_Clock(broken=True))
        setattr(
            self.actor.db, ATTR_VECTOR_ESCALATION, {self.target.id: [1000] * self.cap}
        )

        self.assertEqual(system.escalation_remaining(self.actor, self.target), 0)
        self.assertIsNone(system.note_escalation(self.actor, self.target))

    def test_a_garbage_ledger_reads_as_unthrottled(self):
        # R14.8: a hand-edited value collapses to the documented default.
        for garbage in (None, 0, "soon", [1, 2], {2: "soon"}, {2: ["x", None]}):
            with self.subTest(garbage=garbage):
                setattr(self.actor.db, ATTR_VECTOR_ESCALATION, garbage)

                self.assertEqual(
                    self.system.escalation_remaining(self.actor, self.target), 0
                )

    def test_neither_half_raises_on_unreadable_input(self):
        self.assertEqual(self.system.escalation_remaining(None, self.target), 0)
        self.assertEqual(self.system.escalation_remaining(self.actor, None), 0)
        self.assertIsNone(self.system.note_escalation(None, self.target))
        self.assertIsNone(self.system.note_escalation(self.actor, None))

    def test_a_hostile_request_at_the_cap_is_refused_with_the_figures(self):
        self._resolve(self.cap)

        refusal = self.system.may_target(self.actor, self.target)

        self.assertEqual(refusal, MSG_VECTOR_ESCALATION_LIMIT)
        self.assertEqual(refusal.data["remaining_ticks"], self.window)
        self.assertEqual(refusal.data["count"], self.cap)
        self.assertEqual(refusal.data["cap"], self.cap)
        self.assertEqual(refusal.data["window"], self.window)
        self.assertEqual(refusal.data["target_name"], "Foe")

    def test_a_supporting_operation_is_not_escalation(self):
        self._resolve(self.cap)

        self.assertIsNone(
            self.system.may_target(self.actor, self.target, hostile=False)
        )

    def test_the_cap_reads_the_same_whatever_the_alliance_relationship(self):
        # R10.7: the ledger keys on target identity and knows nothing about
        # alliances, so membership grants no exemption.
        self._resolve(self.cap)
        unaffiliated = self.system.escalation_remaining(self.actor, self.target)
        allied = _system(
            registry=self.registry,
            clock=self.clock,
            alliance=_SimpleAlliance(pairs=[(self.actor, self.target)]),
        )

        self.assertEqual(
            allied.escalation_remaining(self.actor, self.target), unaffiliated
        )
        self.assertGreater(unaffiliated, 0)

    def test_the_structural_refusals_still_come_first(self):
        # The escalation cap is a TIMING refusal, so it sits behind the shield
        # and the allied-target refusal a player can act on immediately.
        newcomer = FakePlayer(key="New", level=max(1, self.shield - 1), player_id=4)
        self._resolve(self.cap, target=newcomer)

        self.assertEqual(
            self.system.may_target(self.actor, newcomer), MSG_VECTOR_TARGET_SHIELDED
        )


# ================================================================== #
#  Requirements 9.4, 9.5 — the Counter_Web advantage multiplier
# ================================================================== #

class TestCounterMultiplier(unittest.TestCase):
    """One lookup, clamped into ``[1.0, cap]``, never a product of a chain."""

    def setUp(self):
        self.registry = fixture_registry()
        self.cap = self.registry.balance.counter_advantage_cap
        self.system = _system(registry=self.registry)

    def _with_web(self, web):
        """A system over a Counter_Web written straight onto the registry.

        Bypasses ``make_registry``'s normalization on purpose: these cases are
        exactly the shapes the loader would never produce.
        """
        registry = fixture_registry()
        registry.counter_web = web
        return _system(registry=registry)

    def test_an_edge_the_web_names_is_worth_the_cap(self):
        # The shipped web declares each edge as a bare Branch name, so the
        # magnitude defaults to the ceiling (R9.4).
        self.assertEqual(
            self.system.counter_multiplier("weapons", "defense"), self.cap
        )

    def test_a_pair_the_web_names_no_edge_between_is_exactly_neutral(self):
        # "weapons" counters "defense" and nothing else.
        self.assertEqual(self.system.counter_multiplier("weapons", "bio"), 1.0)

    def test_the_edge_is_directional(self):
        self.assertEqual(self.system.counter_multiplier("defense", "weapons"), 1.0)

    def test_a_branch_holds_no_advantage_over_itself(self):
        self.assertEqual(self.system.counter_multiplier("weapons", "weapons"), 1.0)

    def test_an_advantage_chain_does_not_compound(self):
        # R9.5: the shipped web is a cycle, so weapons -> defense -> bio is a
        # two-hop path. Only the direct edge is ever asked for, so the far end
        # of the chain is neutral and the near end is one capped value — there
        # is no reading at which the two multiply.
        self.assertEqual(CANONICAL_COUNTER_WEB["defense"], ["bio"])

        self.assertEqual(
            self.system.counter_multiplier("weapons", "defense"), self.cap
        )
        self.assertEqual(self.system.counter_multiplier("weapons", "bio"), 1.0)

    def test_a_branch_with_two_advantages_still_contributes_one(self):
        # R9.3 allows an out-degree of two; each target still resolves to a
        # single capped value rather than to the pair's product.
        system = self._with_web({"weapons": ("defense", "bio")})

        for target in ("defense", "bio"):
            with self.subTest(target=target):
                self.assertEqual(
                    system.counter_multiplier("weapons", target), self.cap
                )

    def test_an_empty_web_leaves_every_pair_neutral(self):
        # An absent branches.yaml is the inert case: no Branch counters any other.
        system = self._with_web({})

        self.assertEqual(system.counter_multiplier("weapons", "defense"), 1.0)

    def test_the_cap_is_read_per_call_so_a_retune_lands(self):
        self.registry.balance.counter_advantage_cap = 2.5

        self.assertEqual(self.system.counter_multiplier("weapons", "defense"), 2.5)

    def test_a_cap_of_one_leaves_a_named_edge_neutral(self):
        self.registry.balance.counter_advantage_cap = 1.0

        self.assertEqual(self.system.counter_multiplier("weapons", "defense"), 1.0)

    def test_a_cap_below_one_cannot_become_a_penalty(self):
        # The clamp's lower bound: a mis-authored ceiling is neutral, never a
        # multiplier that scales a magnitude DOWN (R9.4).
        self.registry.balance.counter_advantage_cap = 0.4

        self.assertEqual(self.system.counter_multiplier("weapons", "defense"), 1.0)

    def test_a_non_finite_cap_grants_no_immunity(self):
        for cap in (float("inf"), float("nan")):
            with self.subTest(cap=cap):
                self.registry.balance.counter_advantage_cap = cap

                self.assertEqual(
                    self.system.counter_multiplier("weapons", "defense"), 1.0
                )

    def test_a_per_edge_magnitude_is_clamped_to_the_cap(self):
        # The seam a future per-edge value arrives through: a value inside the
        # ceiling is honored, one above it is capped, and an unreadable one falls
        # back to the ceiling like a bare Branch name does (R9.4).
        system = self._with_web(
            {"weapons": {"defense": 1.1, "bio": 99.0, "cyber": "strong"}}
        )

        self.assertAlmostEqual(system.counter_multiplier("weapons", "defense"), 1.1)
        self.assertEqual(system.counter_multiplier("weapons", "bio"), self.cap)
        self.assertEqual(system.counter_multiplier("weapons", "cyber"), self.cap)

    def test_a_garbage_web_entry_reads_as_no_edge(self):
        # R14.8, and the reason a bare string is refused: "in" over a string
        # matches a SUBSTRING, which would name an edge nobody declared.
        for garbage in (None, 0, "defense", ("",), {}):
            with self.subTest(garbage=garbage):
                system = self._with_web({"weapons": garbage})

                self.assertEqual(
                    system.counter_multiplier("weapons", "defense"), 1.0
                )

    def test_a_registry_that_cannot_answer_is_neutral(self):
        system = self._with_web("not a web")

        self.assertEqual(system.counter_multiplier("weapons", "defense"), 1.0)

    def test_unresolvable_input_answers_exactly_one(self):
        for actor, target in (
            (None, "defense"), ("weapons", None), ("", "defense"),
            ("weapons", "  "), (None, None), (7, "defense"),
        ):
            with self.subTest(actor=actor, target=target):
                self.assertEqual(
                    self.system.counter_multiplier(actor, target), 1.0
                )


# ================================================================== #
#  Requirement 8.8 — the Response_Window floor
# ================================================================== #

class TestResponseWindow(unittest.TestCase):
    """``max(floor, base - reduction)``, for every reduction there is."""

    def setUp(self):
        self.registry = fixture_registry()
        self.floor = self.registry.balance.minimum_response_window_ticks
        self.system = _system(registry=self.registry)

    def test_a_window_above_the_floor_is_returned_as_asked(self):
        self.assertEqual(self.system.response_window(self.floor + 15), self.floor + 15)

    def test_a_reduction_shortens_the_window(self):
        # R9.4's second permitted form: an advantage may change a timing.
        self.assertEqual(
            self.system.response_window(self.floor + 15, 5), self.floor + 10
        )

    def test_a_reduction_cannot_take_the_window_below_the_floor(self):
        self.assertEqual(self.system.response_window(self.floor + 15, 999), self.floor)

    def test_a_base_below_the_floor_is_raised_to_it(self):
        self.assertEqual(self.system.response_window(1), self.floor)

    def test_a_negative_reduction_lengthens_the_window(self):
        # Absurd, and the floor claim survives it because the floor is a max
        # rather than a subtraction.
        self.assertEqual(
            self.system.response_window(self.floor + 15, -5), self.floor + 20
        )

    def test_the_floor_is_read_per_call_so_a_retune_lands(self):
        self.registry.balance.minimum_response_window_ticks = self.floor + 7

        self.assertEqual(self.system.response_window(2), self.floor + 7)

    def test_an_unconfigured_floor_leaves_the_window_as_asked(self):
        for value in (0, -5):
            with self.subTest(floor=value):
                self.registry.balance.minimum_response_window_ticks = value

                self.assertEqual(self.system.response_window(3), 3)

    def test_the_window_is_never_negative(self):
        self.registry.balance.minimum_response_window_ticks = 0

        self.assertEqual(self.system.response_window(4, 10), 0)
        self.assertEqual(self.system.response_window(-8), 0)

    def test_neither_argument_raises_when_it_cannot_be_read(self):
        # R15.3, in the direction that leaves the target MORE warning: an
        # unreadable base falls back to the floor, an unreadable reduction to
        # no reduction at all.
        for base in (None, "soon", object(), float("inf")):
            with self.subTest(base=base):
                self.assertEqual(self.system.response_window(base), self.floor)
        for reduction in (None, "lots", object()):
            with self.subTest(reduction=reduction):
                self.assertEqual(
                    self.system.response_window(self.floor + 9, reduction),
                    self.floor + 9,
                )


# ================================================================== #
#  Requirement 15.9 — vector registration
# ================================================================== #

class TestRegisterVector(unittest.TestCase):
    """One duck-typed call keys a Vector_System by its own Operation_Kind."""

    def setUp(self):
        self.system = _system()

    def test_a_vector_is_keyed_by_its_own_operation_kind(self):
        # Proven through the two readers of the registry rather than through the
        # mapping: the in-flight count finds the records under "trap" and under
        # no other kind, and the fan-out reaches the vector.
        player = FakePlayer(key="Owner", planet=HOME, player_id=42)
        _register(self.system, _Vector("trap", [_record(player)]))
        ticker = _register(self.system, _TickVector("strategic_strike"))

        self.assertEqual(self.system.in_flight_count(player, "trap"), 1)
        self.assertEqual(self.system.in_flight_count(player, "strategic_strike"), 0)
        self.system.process_tick(9)
        self.assertEqual(ticker.ticks, [9])

    def test_registration_answers_none_because_the_caller_holds_the_vector(self):
        self.assertIsNone(self.system.register_vector(_TickVector("trap")))

    def test_every_registered_kind_is_kept_in_registration_order(self):
        first = _register(self.system, _TickVector("trap"))
        second = _register(self.system, _TickVector("contagion"))
        seen = []
        third = _register(
            self.system, _TickVector("intrusion", on_advance=lambda: seen.append(3))
        )
        first._on_advance = lambda: seen.append(1)
        second._on_advance = lambda: seen.append(2)

        self.system.process_tick(4)

        self.assertEqual(seen, [1, 2, 3])
        for vector in (first, second, third):
            with self.subTest(kind=vector.operation_kind):
                self.assertEqual(vector.ticks, [4])

    def test_re_registering_a_kind_replaces_it_rather_than_doubling_it(self):
        # A kind has exactly one owning Vector_System, so a second registration
        # is a rewire — and the fan-out must not advance one kind twice.
        old = _register(self.system, _TickVector("trap"))
        new = _register(self.system, _TickVector("trap"))

        self.system.process_tick(2)

        self.assertEqual(old.ticks, [])
        self.assertEqual(new.ticks, [2])

    def test_a_replacement_keeps_the_position_the_first_registration_took(self):
        seen = []
        _register(self.system, _TickVector("trap", on_advance=lambda: seen.append("a")))
        _register(
            self.system, _TickVector("contagion", on_advance=lambda: seen.append("b"))
        )
        _register(self.system, _TickVector("trap", on_advance=lambda: seen.append("A")))

        self.system.process_tick(1)

        self.assertEqual(seen, ["A", "b"])

    def test_a_vector_naming_no_kind_is_not_registered(self):
        # R15.3: a mis-wired vector is a logged no-op, so the composition root
        # still finishes wiring the rest.
        for kind in (None, "", "   "):
            with self.subTest(kind=kind):
                system = _system()
                system.register_vector(_TickVector(kind))

                self.assertEqual(system._vectors, {})

    def test_a_vector_whose_kind_cannot_be_read_is_not_registered(self):
        nameless = _NamelessVector()
        self.system.register_vector(nameless)
        self.system.register_vector(SimpleNamespace())
        self.system.register_vector(None)

        self.assertEqual(self.system._vectors, {})
        self.system.process_tick(1)
        self.assertEqual(nameless.ticks, [])


# ================================================================== #
#  Requirements 8.10, 15.9 — the tick fan-out
# ================================================================== #

class TestProcessTick(unittest.TestCase):
    """Every registered vector advances, and each one is isolated (R8.10)."""

    def setUp(self):
        self.system = _system()

    def test_an_empty_registry_is_a_no_op(self):
        # The shipped state of this feature: no Vector_System registered, so the
        # step iterates nothing and the operation half stays inert.
        self.assertIsNone(self.system.process_tick(1))

    def test_each_vector_is_advanced_once_per_tick_with_the_tick_number(self):
        vector = _register(self.system, _TickVector("trap"))

        self.system.process_tick(7)
        self.system.process_tick(8)

        self.assertEqual(vector.ticks, [7, 8])

    def test_a_vector_that_raises_leaves_every_other_vector_advanced(self):
        # R8.10, at the OUTER ring: the driver isolates each operation, this
        # isolates each vector, so neither one can stop the rest.
        before = _register(self.system, _TickVector("trap"))
        broken = _register(self.system, _TickVector("contagion", broken=True))
        after = _register(self.system, _TickVector("intrusion"))

        self.system.process_tick(5)

        self.assertEqual(before.ticks, [5])
        self.assertEqual(broken.ticks, [5])
        self.assertEqual(after.ticks, [5])

    def test_a_vector_that_raises_is_advanced_again_on_the_next_tick(self):
        broken = _register(self.system, _TickVector("trap", broken=True))

        self.system.process_tick(1)
        self.system.process_tick(2)

        self.assertEqual(broken.ticks, [1, 2])

    def test_a_vector_exposing_no_advance_all_is_stepped_over(self):
        # R15.2: an unwired collaborator degrades to a logged skip.
        mute = _register(self.system, _TickVector("trap", advances=False))
        live = _register(self.system, _TickVector("contagion"))

        self.system.process_tick(3)

        self.assertFalse(hasattr(mute, "advance_all"))
        self.assertEqual(live.ticks, [3])

    def test_an_unreadable_tick_number_is_passed_as_zero_and_still_fans_out(self):
        # A tick advances every operation by exactly one whatever it is NUMBERED,
        # so an unreadable number must not cost the vectors their tick.
        vector = _register(self.system, _TickVector("trap"))

        for value in (None, "soon", object(), float("nan")):
            with self.subTest(tick=value):
                vector.ticks.clear()
                self.system.process_tick(value)

                self.assertEqual(vector.ticks, [0])

    def test_a_vector_registered_mid_tick_does_not_disturb_the_walk(self):
        # The fan-out walks a snapshot, so a registration from inside an
        # ``advance_all`` cannot mutate the mapping being iterated.
        late = _TickVector("intrusion")
        early = _register(
            self.system,
            _TickVector("trap", on_advance=lambda: self.system.register_vector(late)),
        )
        other = _register(self.system, _TickVector("contagion"))

        self.system.process_tick(6)

        self.assertEqual(early.ticks, [6])
        self.assertEqual(other.ticks, [6])
        self.assertEqual(late.ticks, [])          # it joins from the next tick on

        self.system.process_tick(7)

        self.assertEqual(late.ticks, [7])


if __name__ == "__main__":  # pragma: no cover - convenience runner
    unittest.main()
