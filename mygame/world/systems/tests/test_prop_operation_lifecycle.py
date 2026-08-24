"""
Property-based tests for the Vector_Operation lifecycle.

Feature: tech-tree-branch-foundation (design section "Correctness Properties").

All eleven of the properties the design's test-module table assigns to this
file, implemented here:

- **Property 13**: The validation chain refuses at the earliest failing check,
  with exactly one reason — Requirements 6.6, 7.3, 8.3, 8.4, 11.9, 15.2.
- **Property 14**: Resources are conserved unless an operation reaches Pending —
  Requirements 4.8, 8.4, 8.5, 8.6, 12.2, 12.6.
- **Property 24**: Every request and every public query returns a value and
  raises nothing — Requirements 8.24, 15.3.
- **Property 15**: A terminal state is final, and each event drives the expected
  transition — Requirements 8.1, 8.2, 8.11, 8.13, 8.16, 8.17, 8.18, 11.4.
- **Property 16**: Suspension delays rather than restarts, and one tick advances
  by exactly one — Requirements 8.9, 8.14, 8.15.
- **Property 17**: The Response_Window never falls below the floor, whatever the
  reduction — Requirements 8.8, 9.4, 11.6.
- **Property 11**: Carrier eligibility is the conjunction of the four conditions
  — Requirements 7.1, 7.5.
- **Property 18**: A Counter_Web advantage is bounded and never compounds —
  Requirements 9.4, 9.5.
- **Property 19**: Cooldown and in-flight counts equal their reference
  computations, and refusals report them — Requirements 8.19, 8.20.
- **Property 20**: The escalation cap and the new-player shield hold regardless
  of the alliance relationship — Requirements 10.4, 10.6, 10.7.
- **Property 25**: An area effect reaches every entity in the area, allied or not
  — Requirements 11.10.

The shared fixtures below are written for the whole file rather than for any one
property, so each group extends the fixture section instead of growing a second
copy of it. One ``@given`` test per property, with each property's clauses
asserted inside that one test, as the design's Testing Strategy requires.

**Why the request three share one test module.** They are three readings of the
same control flow. Property 13 is the chain read as an *order*: which check
answers first. Property 14 is the same chain read as a *ledger*: what the walk
costs the requester. Property 24 is the same chain read as a *signature*: that it
answers at all, for every input, from every entry point on the driver. A change
to ``request`` that satisfies one and breaks another is the failure mode the
three together are here to catch, so they are read side by side.

**And why the lifecycle three sit beside them.** Properties 15, 16, and 17 are
the same relationship one half of the contract later: 15 is the state machine
read as a *graph* (which event leads where, and that the four sinks are sinks),
16 is the same machine read as a *clock* (that a pause costs exactly its own
duration and an advance exactly one tick), and 17 is that clock read as a
*promise to the target* (that a Counter_Web reduction can never take the warning
below the configured floor). All three drive the *same* placed operation through
the *same* driver the request three built, which is why they share its fixtures
rather than standing up a second world.

**And why the services four close the file.** Properties 11, 19, and 20 are the
same contract read from *underneath*: the request three measured the driver's
walk over services held steady, and these measure the **services themselves** —
the carrier conjunction, the two limit ledgers, and the two protection gates — so
that "the chain asked the right question" and "the answer was right" are both
claims this file makes rather than one it makes and one it assumes. Property 18 is
the one service with no state at all (a lookup and a clamp), and Property 25 is
the audience the resolution reads out of the world, which is where R11.10's
indiscriminate area effect either stays indiscriminate or quietly does not.
**Three of the four therefore run against a plain, real ``BranchSystem``** rather
than against :class:`_BranchServices`, which overrides exactly the services they
are about — see that class's own warning, and :func:`_real_system` below.

**The fixtures are local, deliberately.** ``test_operation_contract.py`` holds a
working fixture set for exactly this subject (``ChainRegistry``,
``FakeBranchSystem``, ``ChainVector`` and its variants), and it is *not* imported
here: importing one test module from another couples their collection order and
makes each module's fixtures part of the other's contract, which
``test_prop_operation_persistence.py`` already declined for the same reason.
Everything below is either drawn from the shared ``branch_strategies`` module —
which exists for exactly this — or rebuilt here in the shape these eleven
properties need. The one place this module deliberately *does not* rebuild is
resource arithmetic: :class:`_BranchServices` subclasses the **real**
``BranchSystem`` and inherits ``charge``, ``refund``, and ``resource_shortfall``
untouched, because Property 14 is a claim about a player's resource map and a
double that reimplemented the arithmetic would measure the double.

Every shared generator is drawn from ``branch_strategies``, and that module also
installs the Evennia stubs at import — hence its import is deliberately FIRST
here, so this module loads with ``evennia`` absent from ``sys.modules`` (R15.1).
Every registry is built in memory and injected, so no example needs the
process-wide ``DataRegistry`` singleton (R15.4).
"""

import ast
import inspect
import unittest
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (
    CANONICAL_COUNTER_WEB,
    FAULT_POINTS,
    FIXTURE_PLANETS,
    LIFECYCLE_EVENTS,
    OPERATION_CHECK_ORDER,
    OPERATION_STATE_VALUES,
    POST_CHARGE_FAULT_POINTS,
    TERMINAL_STATE_VALUES,
    FakeAttributes,
    FakeBuilding,
    FakeDB,
    FakePlayer,
    HostileFakeAttributes,
    agent_state_st,
    branch_st,
    cost_map_st,
    counter_web_st,
    event_sequence_st,
    fixture_registry,
    make_registry,
    record_st,
    tick_st,
)
from mygame.world.constants import (
    ATTR_VECTOR_COOLDOWNS,
    ATTR_VECTOR_ESCALATION,
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    MAX_LEVEL,
    OPERATION_KINDS,
)
from mygame.world.definitions import BalanceConfig
from mygame.world.event_bus import (
    BASE_ELIMINATED,
    BUILDING_DESTROYED,
    PLAYER_ELIMINATED,
    PLAYER_NOTIFICATION,
)
from mygame.world.systems import operation_contract
from mygame.world.systems.base_system import BaseSystem
from mygame.world.systems.branch_system import (
    MSG_VECTOR_ESCALATION_LIMIT,
    MSG_VECTOR_TARGET_ALLIED,
    MSG_VECTOR_TARGET_SHIELDED,
    BranchRefusal,
    BranchSystem,
)
from mygame.world.systems.operation_contract import (
    CANCEL_BASE_ELIMINATED,
    CANCEL_CARRIER_KILLED,
    CANCEL_ORIGIN_LOST,
    MSG_VECTOR_CARRIER_REQUIRED,
    MSG_VECTOR_COOLDOWN,
    MSG_VECTOR_IN_FLIGHT_CAP,
    MSG_VECTOR_INSUFFICIENT_RESOURCES,
    NOTIFY_VECTOR_HIT,
    NOTIFY_VECTOR_INCOMING,
    NOTIFY_VECTOR_RESOLVED,
    ORIGIN_NOT_OPERATIONAL,
    SUSPEND_CARRIER_UNAVAILABLE,
    SUSPEND_COMMITMENT_LAPSED,
    OperationDriver,
    OperationOutcome,
    OperationRecord,
    OperationState,
    _read_records,
)

# ================================================================== #
#  Shared vocabulary and fixtures
# ================================================================== #
#
# Written for the whole module: Properties 11 and 15 through 25 land here too, so
# a later task extends this section rather than starting a second one. Nothing
# below is generated — the *world* is fixed and the *inputs* are drawn, which is
# what lets a reference computation state the expected answer.

#: The one Branch every fixture here speaks for, and the Operation_Kind and
#: Carrier_Agent role the shipped constants bind to it. Derived rather than
#: spelled, so a renamed kind or role reaches this module.
BRANCH = "weapons"
KIND = BRANCH_OPERATION_KIND[BRANCH]
ROLE = BRANCH_ROLE[BRANCH]

#: The planet the fixture world lives on, from the shared planet pool.
PLANET = FIXTURE_PLANETS[0]

#: The originating Branch_Building, and this Branch's lab. Synthetic
#: abbreviations, so a fixture building can never be mistaken for a shipped one.
ORIGIN_ABBR = "ZO"
ORIGIN_NAME = "Ordnance Works"
LAB_ABBR = "ZL"

#: The technology gating the originating building (R6.2).
UNLOCK_TECH = "ordnance_theory"

#: The Balance_Config field the Operation_Kind registry entry *binds* the per-use
#: cost to, and the ``<kind>_cost`` convention that is the fallback when no entry
#: exists. Deliberately different names: :data:`DECOY_COST` is parked on the
#: convention field whenever the binding is declared, so a driver that ignored
#: the binding and read the convention would charge the decoy and fail.
COST_FIELD = "ordnance_per_use_cost"
CONVENTION_COST_FIELD = f"{KIND}_cost"

#: A cost no drawn ``cost_map_st`` can produce (its amounts stop at 500), so it
#: is recognizable as the wrong answer wherever it turns up.
DECOY_COST: dict[str, int] = {"Nexium": 999}

#: The per-use cost the fixture registry configures unless a test names another
#: (R12.1). Non-empty on purpose: a kind with no configured cost has nothing to
#: refuse over and nothing to charge, so a zero-cost default would make the
#: ``resources`` check unreachable.
KIND_COST: dict[str, int] = {"Iron": 25}

#: The Response_Window floor the fixture balance is tuned to (R8.8). Not the
#: shipped default of 5: a distinct value tells a floor that was read from
#: Balance_Config apart from one that was hard-coded.
FLOOR = 7

#: The cooldown a forced ``cooldown`` refusal reports (R8.19).
COOLDOWN_TICKS = 6

#: The in-flight count the fixture Branch_System reports (R8.20). A forced
#: ``in_flight`` refusal sets the cap equal to it.
IN_FLIGHT_COUNT = 3

#: The nine check names, as a set, for the "exactly one reason" clause of
#: Property 13. ``OPERATION_CHECK_ORDER`` is the shared by-value copy and
#: ``OperationDriver._CHECK_ORDER`` is the authority; the two are cross-checked
#: in ``test_operation_contract.py``, so this module reads the shared one.
CHECK_NAMES: frozenset[str] = frozenset(OPERATION_CHECK_ORDER)

#: The keys the have-and-need refusal carries (R12.3). Both places a request can
#: be refused over a cost — the ``resources`` pre-check and the whole-or-none
#: charge that is the authority — report this same shape, which is what lets a
#: caller read one breakdown without knowing which of the two refused.
INSUFFICIENT_DETAIL_KEYS: frozenset[str] = frozenset(
    {"message", "cost", "resources", "missing", "kind"}
)

#: The alliance a forced ``target`` refusal names (R11.9).
ALLIANCE_NAME = "Iron Concord"

#: The refusal a forced ``target`` check answers with: R11.9's allied target,
#: reached through ``BranchSystem.may_target`` exactly as the driver reaches it.
ALLIED_REFUSAL = BranchRefusal(MSG_VECTOR_TARGET_ALLIED, alliance=ALLIANCE_NAME)

#: The Branch a forced ``commitment`` refusal reports the requester as holding.
RIVAL_BRANCH = "defense"

#: R8.4's second half, check by check: **the value required to pass**. Each entry
#: is the subset of the refusal detail the requirement asks be reported, so a
#: refusal that named the right check but reported nothing actionable fails.
#: Written from the requirements rather than read off the driver.
REQUIRED_VALUE: dict[str, dict] = {
    # R15.2 — which collaborator is not injected.
    "collaborators": {"collaborator": "combat_engine", "missing": ["combat_engine"]},
    # R8.3 — the Branch required, the lab that establishes it, the one held.
    "commitment": {
        "required_branch": BRANCH,
        "required_lab": LAB_ABBR,
        "current_branch": RIVAL_BRANCH,
    },
    # R11.3, R5.4 — which of the origin's three conditions failed, and where.
    "origin": {"reason": ORIGIN_NOT_OPERATIONAL, "building": ORIGIN_ABBR},
    # R6.6 — the unlocking technology and the Branch hosting it.
    "unlock": {"technology": UNLOCK_TECH, "branch": BRANCH},
    # R7.3 — the Carrier_Agent role.
    "carrier": {"role": ROLE, "branch": BRANCH},
    # R11.9 — the alliance protecting the target, carried through from the gate.
    "target": {"message": MSG_VECTOR_TARGET_ALLIED, "alliance": ALLIANCE_NAME},
    # R8.19 — the remaining cooldown ticks.
    "cooldown": {"remaining_ticks": COOLDOWN_TICKS, "building": ORIGIN_ABBR},
    # R8.20 — the current count and the cap.
    "in_flight": {"count": IN_FLIGHT_COUNT, "cap": IN_FLIGHT_COUNT},
    # R12.3 — the have-and-need breakdown for every required resource.
    "resources": {
        "cost": KIND_COST,
        "resources": {"Iron": {"have": 0, "need": KIND_COST["Iron"]}},
        "missing": {"Iron": {"have": 0, "need": KIND_COST["Iron"]}},
    },
}

#: "This fixture argument was not given", as distinct from being given ``None``.
#: The carrier needs the distinction: ``None`` is the *answer* a forced
#: ``carrier`` refusal is configured with (R7.3), so it cannot also mean "use the
#: default".
_UNSET = object()

#: The five hooks a vector spec MUST implement, and the five it may. Written out
#: rather than derived, because "a vector implements exactly five things" is the
#: contract (design §4.10) and a sixth appearing must fail Property 24's
#: completeness clause rather than be absorbed by it.
REQUIRED_HOOKS: tuple[str, ...] = (
    "validate_target",
    "build_record",
    "on_resolve",
    "persistence_owner",
    "discover_records",
)
OPTIONAL_HOOKS: tuple[str, ...] = (
    "on_expire",
    "on_suspend",
    "on_resume",
    "on_cancel",
    "on_discard",
)


class _Bus:
    """An EventBus that records what it published and dispatches to subscribers.

    Recording is what lets a refusal be shown to have notified nobody (R8.4);
    dispatching is what lets the driver's three world-event subscriptions be
    exercised the way the real bus reaches them, in the same
    ``callback(event_name=..., **payload)`` shape ``world.event_bus.EventBus``
    calls them in.
    """

    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.subscribers: dict[str, list] = {}

    def subscribe(self, event, callback):
        self.subscribers.setdefault(event, []).append(callback)

    def publish(self, event, **data):
        self.published.append((event, data))
        for callback in list(self.subscribers.get(event, ())):
            callback(event_name=event, **data)

    def notifications(self):
        """Return only the player notifications, which is what a lifecycle emits."""
        return [data for event, data in self.published if event == PLAYER_NOTIFICATION]


class _Registry:
    """A DataRegistry stand-in holding only what the driver reads (R15.4).

    Two building definitions, one Operation_Kind binding, and the Balance_Config
    fields that binding names — so a test can retune the cost, drop the binding,
    or blank the unlock technology and watch the driver follow.

    Args:
        cost: The per-use resource cost to configure. Defaults to
            :data:`KIND_COST`; pass ``{}`` for a kind that costs nothing.
        binding: ``"declared"`` puts the cost on :data:`COST_FIELD` and names
            that field in the Operation_Kind entry, parking :data:`DECOY_COST` on
            the ``<kind>_cost`` convention; ``"fallback"`` drops the entry
            entirely and puts the cost on the convention field. Both must charge
            the same amount, which is how "``_resource_cost`` reads the
            ``cost_field`` binding with a ``<kind>_cost`` fallback" is measured.
        unlock: The originating building's unlocking technology (R6.1).
        floor: ``minimum_response_window_ticks`` (R8.8).
    """

    def __init__(
        self, cost=KIND_COST, binding="declared", unlock=UNLOCK_TECH, floor=FLOOR
    ):
        self.buildings = {
            ORIGIN_ABBR: SimpleNamespace(
                abbreviation=ORIGIN_ABBR,
                name=ORIGIN_NAME,
                branch=BRANCH,
                unlock_technology=unlock,
            ),
            LAB_ABBR: SimpleNamespace(
                abbreviation=LAB_ABBR,
                name="Weapons Lab",
                branch=BRANCH,
                unlock_technology=None,
            ),
        }
        lines = dict(cost or {})
        if binding == "declared":
            self.operation_kinds = {
                KIND: SimpleNamespace(
                    kind=KIND,
                    branch=BRANCH,
                    carrier_role=ROLE,
                    cost_field=COST_FIELD,
                    cooldown_field=f"{KIND}_cooldown_ticks",
                    cap_field=f"{KIND}_max_in_flight",
                    agent_xp_field=f"agent_xp_{KIND}",
                ),
            }
            fields = {COST_FIELD: lines, CONVENTION_COST_FIELD: dict(DECOY_COST)}
        else:
            self.operation_kinds = {}
            fields = {COST_FIELD: dict(DECOY_COST), CONVENTION_COST_FIELD: lines}
        self.balance = SimpleNamespace(
            minimum_response_window_ticks=floor, **fields
        )


class _BranchServices(BranchSystem):
    """Every Branch service the driver asks, over one recording double (R15.8).

    Subclasses the **real** ``BranchSystem`` and overrides only the thirteen
    services that answer a question about the *world* — a commitment, a
    building's Operational state, a roster, a ledger — so a test configures an
    answer instead of building a world to imply it.

    The other four are the **shipped** implementations, recorded on the way
    through: ``charge``, ``refund``, and ``resource_shortfall``, because Property
    14 is a claim about a player's resource map and a double that reimplemented
    resource arithmetic would measure the double; and ``response_window``,
    because R8.8's floor is the shared service's own ``max``.

    Every call is recorded, which is what lets a refused request be shown to have
    called no service that writes (R8.4).

    **A property about a service this class overrides must not use this class.**
    The overrides exist so a property about the *driver* can hold a service
    steady; a property about the cooldown ledger, the in-flight count, or the
    escalation window is a property about ``BranchSystem`` itself and belongs
    against a plain one (Property 24 below constructs an unwired ``BranchSystem``
    directly for exactly that reason).
    """

    def __init__(
        self,
        registry,
        event_bus,
        commitment_answer=BRANCH,
        operational=True,
        applied=(UNLOCK_TECH,),
        role=ROLE,
        carrier=_UNSET,
        target_refusal=None,
        cooldown=0,
        count=IN_FLIGHT_COUNT,
        cap=0,
        readable_stock=True,
    ):
        super().__init__(registry, event_bus)
        self.commitment_answer = commitment_answer
        self.operational = operational
        self.applied = frozenset(applied)
        self.role = role
        self.carrier = (
            SimpleNamespace(key=ROLE, id=77) if carrier is _UNSET else carrier
        )
        self.target_refusal = target_refusal
        self.cooldown = cooldown
        self.count = count
        self.cap = cap
        self.readable_stock = readable_stock
        self.calls: list[tuple] = []

    # --- the thirteen world-facing services, as configured answers ----- #

    def commitment(self, player, planet=None):
        self.calls.append(("commitment", player, planet))
        return self.commitment_answer

    def lab_for_branch(self, branch):
        self.calls.append(("lab_for_branch", branch))
        return LAB_ABBR if branch == BRANCH else None

    def branch_of_technology(self, tech_key):
        self.calls.append(("branch_of_technology", tech_key))
        return BRANCH

    def role_for_branch(self, branch):
        self.calls.append(("role_for_branch", branch))
        return self.role

    def is_operational(self, building):
        self.calls.append(("is_operational", building))
        return self.operational

    def applied_technologies(self, player, planet=None):
        self.calls.append(("applied_technologies", player, planet))
        return self.applied

    def eligible_carrier(self, player, role, planet=None):
        self.calls.append(("eligible_carrier", player, role, planet))
        return self.carrier if role == self.role else None

    def may_target(self, actor, target, hostile=True):
        self.calls.append(("may_target", actor, target, hostile))
        return self.target_refusal

    def cooldown_remaining(self, building, kind):
        self.calls.append(("cooldown_remaining", building, kind))
        return self.cooldown

    def in_flight_count(self, player, kind, planet=None):
        self.calls.append(("in_flight_count", player, kind, planet))
        return self.count

    def in_flight_cap(self, kind):
        self.calls.append(("in_flight_cap", kind))
        return self.cap

    def note_cooldown(self, building, kind):
        self.calls.append(("note_cooldown", building, kind))

    def note_escalation(self, actor, target):
        self.calls.append(("note_escalation", actor, target))

    # --- the four shipped services, recorded on the way through -------- #

    def charge(self, player, cost):
        self.calls.append(("charge", player, dict(cost)))
        return super().charge(player, cost)

    def refund(self, player, cost):
        self.calls.append(("refund", player, dict(cost)))
        return super().refund(player, cost)

    def resource_shortfall(self, player, cost):
        self.calls.append(("resource_shortfall", player, dict(cost)))
        if not self.readable_stock:
            return {}          # a stock nobody can read: the charge decides
        return super().resource_shortfall(player, cost)

    def response_window(self, base_ticks, reduction=0):
        self.calls.append(("response_window", base_ticks, reduction))
        return super().response_window(base_ticks, reduction)

    # --- what a test asks of the record -------------------------------- #

    def called(self, service):
        """Return True when *service* was asked at least once."""
        return any(entry[0] == service for entry in self.calls)

    def calls_to(self, service):
        """Return every recorded call to *service*, in order."""
        return [entry for entry in self.calls if entry[0] == service]


class _DurableOwner:
    """The durable owner a vector nominates (R14.1), and nothing more.

    An ``attributes`` handler is the entire surface the persistence pair
    requires. The handler is **hostile** — it copies on the way out and on the
    way in — so nothing here can appear to persist by mutating a container it
    read (R14.7), and "a refused request persisted nothing" is evidence rather
    than an assumption.
    """

    def __init__(self):
        self.attributes = HostileFakeAttributes()


class _Combat:
    """The CombatEngine reduced to its single-hit entry point (R8.23)."""

    def __init__(self, damage=9):
        self.damage = damage
        self.hits: list[dict] = []

    def apply_direct_hit(
        self, attacker, target, weapon_item, include_attacker_bonus=True,
        current_tick=None,
    ):
        self.hits.append({"attacker": attacker, "target": target})
        return self.damage


def _raising_check(ctx):
    """A check that cannot answer — the R15.3 case for the driver's own code."""
    raise RuntimeError("this check cannot be evaluated")


class _Vector(OperationDriver, BaseSystem):
    """A conforming vector in the composed shape design §4.10 declares.

    The five required hooks, one durable owner, one combat engine, and a spy on
    the check walk: ``_run_check`` records the name it was asked and then
    delegates to the shipped one, so the walk is *observed* rather than replaced
    and every check under it is the real check.
    """

    operation_kind = KIND
    branch = BRANCH
    _required_collaborators = ("combat_engine",)

    def __init__(
        self, registry, event_bus, branch_system=None, combat=True, owner=None
    ):
        super().__init__(registry, event_bus, branch_system=branch_system)
        self.owner = _DurableOwner() if owner is None else owner
        self._combat_engine = _Combat() if combat else None
        self.ticks = 20
        self.lifetime = None
        self.fail_build = False
        self.terminal_record = False
        self.built: list[OperationRecord] = []
        self.resolved: list[str] = []
        self.ran: list[str] = []

    def _run_check(self, name, ctx):
        self.ran.append(name)
        return super()._run_check(name, ctx)

    # --- the five required hooks --------------------------------------- #

    def validate_target(self, ctx):
        return None

    def build_record(self, ctx):
        if self.fail_build:
            raise RuntimeError("this operation's record cannot be built")
        record = OperationRecord(
            kind=self.operation_kind,
            owner_ref=ctx.player,
            building_ref=ctx.building,
            carrier_ref=ctx.carrier,
            planet=ctx.planet,
            target_x=ctx.target_x,
            target_y=ctx.target_y,
            ticks_remaining=self.ticks,
            lifetime_remaining=self.lifetime,
        )
        if self.terminal_record:
            # Nothing raised, and yet the operation cannot enter Pending: the
            # single state writer declines to move a terminal record (R8.2). The
            # quiet fourth post-charge failure point, and it must refund too.
            record.state = str(OperationState.RESOLVED)
        self.built.append(record)
        return record

    def on_resolve(self, record):
        self.resolved.append(record.op_id)

    def persistence_owner(self, record):
        return self.owner

    def discover_records(self, planet_rooms):
        return [self.owner]


class _BareDriver(OperationDriver):
    """A vector that declares nothing and implements nothing.

    Constructed with no registry, no event bus, and no Branch_System, so every
    read the driver makes degrades and every required hook raises
    ``NotImplementedError``. Property 24's subject at its harshest: the claim is
    that the driver's own entry points still answer over it.
    """


def _world(registry=None, resources=None, combat=True, vector_cls=None, **answers):
    """Return a vector wired for the happy path, with its player and building.

    Every service answers "yes" and every collaborator is injected, so a test
    breaks exactly one thing and the chain's single refusal names it.

    Args:
        registry: The :class:`_Registry` to inject; a fresh default one when
            ``None``.
        resources: The requesting player's starting stock.
        combat: Whether the declared ``combat_engine`` collaborator is injected
            (R15.2).
        vector_cls: The vector class to construct — :class:`_Vector` by default,
            and the extension point a property that needs a conforming vector
            with more instrumentation reaches through (:class:`_LifecycleVector`
            is the one the lifecycle and timing properties pass).
        **answers: The configured service answers, passed to
            :class:`_BranchServices`.
    """
    bus = _Bus()
    registry = _Registry() if registry is None else registry
    player = FakePlayer(resources=resources, planet=PLANET)
    building = FakeBuilding(
        building_type=ORIGIN_ABBR, owner=player, planet=PLANET, x=3, y=4
    )
    branch = _BranchServices(registry, bus, **answers)
    vector = (vector_cls or _Vector)(
        registry, bus, branch_system=branch, combat=combat
    )
    return SimpleNamespace(
        bus=bus, registry=registry, player=player, building=building,
        branch=branch, vector=vector,
    )


def _send(world, **extra):
    """Send one request through *world*'s vector, with the happy-path params."""
    params = {"building": world.building, "x": 3, "y": 4}
    params.update(extra)
    return world.vector.request(world.player, **params)


def _check_names_in(value):
    """Return every one of the nine check names appearing as a string in *value*.

    Values only, never keys: the have-and-need breakdown carries a ``resources``
    *key* by design (R12.3), which is not the chain naming a second reason. So a
    refusal "carries exactly one check name" is measured over what it *says*, and
    a detail that named a second check would fail.
    """
    if isinstance(value, str):
        return {value} & CHECK_NAMES
    found: set[str] = set()
    if isinstance(value, Mapping):
        for item in value.values():
            found |= _check_names_in(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            found |= _check_names_in(item)
    return found


def _asked_services():
    """Return every Branch service name the shipped driver asks by literal.

    An AST scan of ``operation_contract.py`` for ``self._ask("<name>", ...)``, so
    "the services a vector consumes" is read off the code that consumes them
    rather than restated. Property 24 asserts this set is exactly the set
    :class:`_BranchServices` answers, which is what stops a newly consumed
    service from silently taking its guarded default in every property here.
    """
    tree = ast.parse(inspect.getsource(operation_contract))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = node.func
        if not isinstance(callee, ast.Attribute) or callee.attr != "_ask":
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


def public_method_names():
    """Return every public method ``OperationDriver`` itself declares."""
    return frozenset(
        name for name, value in vars(OperationDriver).items()
        if not name.startswith("_") and callable(value)
    )


# ------------------------------------------------------------------ #
#  Shared fixtures for the lifecycle half (Properties 15, 16, 17)
# ------------------------------------------------------------------ #
#
# The request three above need a world only up to the moment an operation is
# placed. The lifecycle three need it AFTERWARDS: a placed, tracked, live
# operation with a carrier the polled conditions can read, a target whose owner
# is an audience, and this Branch's lab standing so its destruction is a
# commitment lapsing. So the three additions here are a Carrier_Agent fake, a
# vector that records its hooks, and the staging/placement pair that puts one
# operation in flight.

#: The originating player's display name, and the name of the player the fixture
#: operation is aimed at. Distinct, so a notification payload's ``attacker_name``
#: is legible and the two audiences cannot be confused for one another.
ATTACKER_NAME = "Vex"
DEFENDER_NAME = "Mira"

#: A second planet, from the shared pool: R8.18's suspension and R11.4's
#: cancellation are both per-planet, so an operation somewhere else is what shows
#: the scoping is real.
OTHER_PLANET = FIXTURE_PLANETS[1]

#: The four hooks Property 15 counts. Each reports reaching a *terminal* state,
#: so at most one of them can ever fire for one operation, exactly once — which
#: is a sharper claim than "no hook fires more than once" and implies it.
#: ``on_suspend`` and ``on_resume`` are deliberately absent: a pause may happen
#: any number of times.
TERMINAL_HOOKS: tuple[str, ...] = (
    "on_resolve", "on_expire", "on_cancel", "on_discard",
)

#: Terminal state -> the hook that reports reaching it. The mapping is what lets
#: "the hook that fired matches the state reached" be asserted rather than just
#: "some hook fired".
TERMINAL_HOOK_FOR_STATE: dict[str, str] = {
    str(OperationState.RESOLVED): "on_resolve",
    str(OperationState.EXPIRED): "on_expire",
    str(OperationState.CANCELLED): "on_cancel",
    str(OperationState.DISCARDED): "on_discard",
}


class _Carrier:
    """A Carrier_Agent the polled lifecycle conditions can actually read.

    :class:`_BranchServices` answers the ``carrier`` check with a bare namespace,
    which is enough for a *request* — the chain only asks whether an eligible
    agent exists. The tick advance asks this one whether it is **in reserve**,
    **incapacitated** (R8.14), or **dead** (R8.16), and every one of those reads
    goes through the object's own attribute handler, so a carrier a condition can
    judge needs the handler. Reached duck-typed by the same attribute names every
    other consumer reads them by, so a benched agent is benched for a
    Vector_Operation exactly when it is benched for its behaviour script.
    """

    _next_id = 900

    def __init__(self, reserve=False, incapacitated=False, hp=10):
        _Carrier._next_id += 1
        self.id = _Carrier._next_id
        self.key = ROLE
        self.attributes = FakeAttributes({
            "reserve": reserve, "incapacitated": incapacitated, "hp": hp,
        })
        self.db = FakeDB(self.attributes)

    def bench(self, benched=True):
        """Put this agent into reserve, or bring it back (R8.14, R8.15)."""
        self.db.reserve = bool(benched)


class _LifecycleVector(_Vector):
    """The conforming vector the lifecycle and timing properties drive.

    Two additions to :class:`_Vector`, and nothing else — it is the same vector
    the request properties measure, so a lifecycle claim is made about the driver
    the request claims were made about:

    * the record **names its target** (``target_ref``), which is what gives the
      notification points a non-empty audience: R8.7's warning goes to the
      players the effect would reach, and with no target and no room there are
      none, so Property 17's "identical notification set" clause would compare
      two empty lists;
    * **every hook is recorded**, so Property 15's "no hook fires more than
      once" is measured rather than assumed. The required ``on_resolve`` still
      runs :class:`_Vector`'s own body, so the resolution record is unchanged.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hooks: list[str] = []

    def build_record(self, ctx):
        record = super().build_record(ctx)
        record.target_ref = ctx.target
        return record

    def on_resolve(self, record):
        self.hooks.append("on_resolve")
        super().on_resolve(record)

    def on_expire(self, record):
        self.hooks.append("on_expire")

    def on_suspend(self, record):
        self.hooks.append("on_suspend")

    def on_resume(self, record):
        self.hooks.append("on_resume")

    def on_cancel(self, record):
        self.hooks.append("on_cancel")

    def on_discard(self, record):
        self.hooks.append("on_discard")

    def fired(self, names):
        """Return the hooks in *names* that ran, in the order they ran."""
        return [hook for hook in self.hooks if hook in tuple(names)]


class _SupportingVector(_LifecycleVector):
    """A vector whose Signature_Vector supports rather than attacks.

    The one override design §4.5 sanctions: a supporting operation has no target
    to warn, so R8.8's Response_Window floor does not apply to it — and it is
    therefore the configuration in which "a resume restores exactly the ticks
    held at suspension" (R8.15) can be stated without the floor's ``max``
    lengthening the answer. Property 16 draws it for that reason.
    """

    def _is_hostile(self, record):
        return False


def _staged(registry=None, vector_cls=_LifecycleVector, **answers):
    """Return a world ready to place one operation, with nothing placed yet.

    Split from :func:`_place` because Property 17 has to configure the
    Response_Window *before* the request is made: the floor is applied at the
    single point a record enters Pending, so a reduction installed afterwards
    would be measuring the resume path only.
    """
    answers.setdefault("carrier", _Carrier())
    world = _world(
        registry=registry,
        resources={"Iron": 500, "Circuits": 500},
        vector_cls=vector_cls,
        **answers,
    )
    world.player.key = ATTACKER_NAME
    world.carrier = world.branch.carrier
    world.defender = FakePlayer(key=DEFENDER_NAME, planet=PLANET)
    world.target = FakeBuilding(
        building_type=ORIGIN_ABBR, owner=world.defender, planet=PLANET, x=3, y=4
    )
    #: This Branch's lab, standing on the same planet as the operation. Not the
    #: originating building: destroying the *lab* is the owner's
    #: Branch_Commitment lapsing (R8.18), which suspends, while destroying the
    #: *origin* cancels (R8.17) — two different transitions through one event.
    world.lab = FakeBuilding(
        building_type=LAB_ABBR, owner=world.player, planet=PLANET, x=1, y=1
    )
    return world


def _place(world, ticks=None, lifetime=None, hostile=True):
    """Place one operation in *world* and return the world holding it.

    ``world.clock`` is the clock the placed record actually carries, read off the
    record rather than assumed: the request's own ``hostile`` decides whether
    R8.8's floor raised what the vector asked for.
    """
    if ticks is not None:
        world.vector.ticks = ticks
    world.vector.lifetime = lifetime
    world.outcome = _send(world, target=world.target, hostile=hostile)
    world.record = world.vector.tracked_records()[0]
    world.clock = world.record.ticks_remaining
    return world


def _placed(registry=None, ticks=None, lifetime=None, hostile=True, **answers):
    """Return a world holding one placed, tracked, live Vector_Operation."""
    return _place(
        _staged(registry=registry, **answers),
        ticks=ticks, lifetime=lifetime, hostile=hostile,
    )


def _bystander(world, op_id, owner=None, planet=PLANET, ticks=50):
    """Track a second operation in *world*, and return it.

    The control for every "for exactly those operations" clause: the event
    handlers are keyed on the records this vector tracks, so a claim that one of
    them cancelled the right operation is only half a claim until another
    operation is shown to have been left alone. Its carrier and its originating
    building are its own, so no match can reach it by accident.
    """
    record = OperationRecord(
        op_id=op_id,
        kind=KIND,
        owner_ref=world.player if owner is None else owner,
        building_ref=FakeBuilding(
            building_type=ORIGIN_ABBR, owner=world.player, planet=planet
        ),
        carrier_ref=_Carrier(),
        planet=planet,
        target_x=1,
        target_y=1,
        ticks_remaining=ticks,
    )
    world.vector._track(record)
    return record


# ================================================================== #
#  Property 13
# ================================================================== #
#
# The claim has three parts and they are not the same claim: the refusal names
# the check that is EARLIEST in the declared order among those failing, it
# carries EXACTLY ONE check name, and NOTHING changed. A driver that refused the
# last failing check would satisfy the second and third; one that refused
# everything would satisfy all three except at the empty subset — which is why
# ``check_subset_st`` includes the empty set and why the bottom of the lattice is
# asserted to be an accepted operation.
#
# The subset is forced THREE ways in the one test, because the "exactly one
# reason" clause has to hold through each and they are different code paths:
#
# 1. **The world says no.** Each forced check is made to fail through the
#    configuration a live game would present it with — a collaborator unwired
#    (R15.2), a Branch_Commitment that does not match, an originating building
#    that is not Operational, an unlocking technology not applied (R6.6), no
#    eligible Carrier_Agent (R7.3), an allied target (R11.9), a cooldown still
#    running, the in-flight cap reached, a stock that cannot pay. This is the
#    real chain, running its real checks, and the refusal each one reports is the
#    value the requirement asks be reported.
# 2. **The check is missing**, which only a subclass deleting one can cause. It
#    refuses in that check's name with ``reason="check_missing"``.
# 3. **The check raises**, which for ``target`` includes a vector's own
#    ``validate_target`` hook raising. It refuses in that check's name with
#    ``reason="check_failed"``.
#
# The same earliest-name computation is asserted through all three, so the order
# is shown to be a property of ``request``'s walk rather than of any one check.
#
# Two behaviours the world configuration leans on deliberately, rather than
# around:
#
# - an ``in_flight`` cap below 1 is **unbounded**, not a lockout, so an unforced
#   ``in_flight`` check passes with a count of three against a cap of zero;
# - a vector declaring **no** Branch matches no commitment and therefore refuses
#   every request, which the final clause asserts by re-running the same subset
#   against a blank-Branch vector: the earliest failing check becomes the
#   earliest of the subset and ``commitment``.


def _forced_world(checks, blank_branch=False):
    """Return a world configured so exactly the checks in *checks* fail.

    The resource line is decided at construction because a player's stock cannot
    be lowered after the fact: a forced ``resources`` check starts the player at
    nothing, and every other configuration starts them able to pay.
    """
    answers = {}
    if "commitment" in checks:
        answers["commitment_answer"] = RIVAL_BRANCH
    if "origin" in checks:
        answers["operational"] = False
    if "unlock" in checks:
        answers["applied"] = frozenset()
    if "carrier" in checks:
        answers["carrier"] = None
    if "target" in checks:
        answers["target_refusal"] = ALLIED_REFUSAL
    if "cooldown" in checks:
        answers["cooldown"] = COOLDOWN_TICKS
    # R8.20: a cap at or below the count refuses; a cap below 1 is UNBOUNDED, so
    # the unforced reading is a count of three against no cap at all.
    answers["cap"] = IN_FLIGHT_COUNT if "in_flight" in checks else 0
    resources = None if "resources" in checks else {"Iron": 500, "Circuits": 500}
    world = _world(
        resources=resources,
        combat="collaborators" not in checks,       # R15.2: the declared one
        **answers,
    )
    if blank_branch:
        world.vector.branch = ""
    return world


class _EarliestRefusal:
    """The reference computation Property 13 measures the chain against."""

    @staticmethod
    def among(failing):
        """Return the check that is earliest in the declared order, or ``None``."""
        for name in OPERATION_CHECK_ORDER:
            if name in failing:
                return name
        return None


# Feature: tech-tree-branch-foundation, Property 13: The validation chain refuses
# at the earliest failing check, with exactly one reason
#
# **Validates: Requirements 6.6, 7.3, 8.3, 8.4, 11.9, 15.2**
class TestProperty13EarliestFailingCheck(unittest.TestCase):
    """One input, one reason — the earliest failing check, and nothing touched."""

    def _assert_one_reason(self, outcome, expected, where):
        """The refusal names *expected*, and names no other check anywhere."""
        self.assertIsInstance(outcome, OperationOutcome)
        self.assertFalse(outcome.ok, f"{where}: the request should have been refused")
        self.assertEqual(
            outcome.check, expected,
            f"{where}: refused {outcome.check!r}, not the earliest failing check",
        )
        self.assertIsNone(outcome.state, f"{where}: a refusal creates no operation")
        self.assertIsNone(outcome.op_id, f"{where}: a refusal creates no identity")
        named = {outcome.check} | _check_names_in(outcome.detail)
        self.assertEqual(
            named, {expected},
            f"{where}: the refusal carries {sorted(named)}, not exactly one reason",
        )

    def _assert_reports_the_required_value(self, outcome, expected):
        """R8.4's second half: the value required to pass the failing check."""
        detail = outcome.detail or {}
        for key, value in REQUIRED_VALUE[expected].items():
            self.assertIn(
                key, detail,
                f"the {expected} refusal must report {key!r}, the value required "
                "to pass it (R8.4)",
            )
            self.assertEqual(
                detail[key], value,
                f"the {expected} refusal reported {key}={detail[key]!r}",
            )
        self.assertEqual(
            detail.get("kind"), KIND,
            "every refusal names the Operation_Kind that produced it",
        )

    def _assert_stopped_at(self, world, expected, where):
        """The walk asked every check up to *expected* and not one after it."""
        stop = OPERATION_CHECK_ORDER.index(expected) + 1
        self.assertEqual(
            world.vector.ran, list(OPERATION_CHECK_ORDER[:stop]),
            f"{where}: the chain did not stop at the first failing check",
        )

    def _assert_nothing_changed(self, world, start, where):
        """R8.4: every player-owned and world-owned state is as it was."""
        self.assertEqual(
            world.player.resource_snapshot(), start,
            f"{where}: a refused request changed the player's resources",
        )
        self.assertEqual(
            world.vector.tracked_records(), [],
            f"{where}: a refused request tracked an operation",
        )
        self.assertEqual(
            _read_records(world.vector.owner), [],
            f"{where}: a refused request persisted an operation",
        )
        self.assertEqual(
            world.vector.built, [],
            f"{where}: a refused request built a record",
        )
        for service in ("charge", "refund", "note_cooldown", "note_escalation"):
            self.assertFalse(
                world.branch.called(service),
                f"{where}: a refused request called the {service} service",
            )
        self.assertEqual(
            world.bus.notifications(), [],
            f"{where}: a refused request notified somebody",
        )

    def _assert_accepted(self, world, where):
        """The bottom of the lattice: nothing forced to fail must be accepted."""
        outcome = _send(world)
        self.assertTrue(
            outcome.ok, f"{where}: nothing was forced to fail, so this must pass"
        )
        self.assertEqual(outcome.state, str(OperationState.PENDING))
        self.assertEqual(
            world.vector.ran, list(OPERATION_CHECK_ORDER),
            f"{where}: an accepted request must have run every check",
        )
        return outcome

    @given(forced=st.sets(st.sampled_from(OPERATION_CHECK_ORDER)))
    @settings(max_examples=100)
    def test_the_chain_refuses_the_earliest_failing_check_with_one_reason(self, forced):
        """**Validates: Requirements 6.6, 7.3, 8.3, 8.4, 11.9, 15.2**"""
        expected = _EarliestRefusal.among(forced)

        # -- 1. The world says no: the real checks, on real configuration ---- #
        world = _forced_world(forced)
        start = world.player.resource_snapshot()
        if expected is None:
            self._assert_accepted(world, "the world says no")
        else:
            outcome = _send(world)
            self._assert_one_reason(outcome, expected, "the world says no")
            self._assert_stopped_at(world, expected, "the world says no")
            self._assert_nothing_changed(world, start, "the world says no")
            self._assert_reports_the_required_value(outcome, expected)

        # -- 2. The check is missing (reason="check_missing") ---------------- #
        world = _forced_world(())
        start = world.player.resource_snapshot()
        for name in forced:
            setattr(world.vector, f"_check_{name}", None)
        if expected is None:
            self._assert_accepted(world, "the check is missing")
        else:
            outcome = _send(world)
            self._assert_one_reason(outcome, expected, "the check is missing")
            self._assert_stopped_at(world, expected, "the check is missing")
            self._assert_nothing_changed(world, start, "the check is missing")
            self.assertEqual(outcome.detail["reason"], "check_missing")

        # -- 3. The check raises (reason="check_failed") --------------------- #
        world = _forced_world(())
        start = world.player.resource_snapshot()
        for name in forced:
            setattr(world.vector, f"_check_{name}", _raising_check)
        if expected is None:
            self._assert_accepted(world, "the check raises")
        else:
            outcome = _send(world)
            self._assert_one_reason(outcome, expected, "the check raises")
            self._assert_stopped_at(world, expected, "the check raises")
            self._assert_nothing_changed(world, start, "the check raises")
            self.assertEqual(outcome.detail["reason"], "check_failed")

        # -- 4. A vector declaring no Branch matches no commitment ---------- #
        # A blank ``branch`` is a mis-declared vector, and the degrade-to-refusal
        # direction makes that visible rather than exempting it from the one gate
        # every operation shares. The earliest failing check is therefore the
        # earliest of the forced subset and ``commitment``.
        world = _forced_world(forced, blank_branch=True)
        start = world.player.resource_snapshot()
        blank_expected = _EarliestRefusal.among(set(forced) | {"commitment"})
        outcome = _send(world)
        self._assert_one_reason(outcome, blank_expected, "no Branch declared")
        self._assert_stopped_at(world, blank_expected, "no Branch declared")
        self._assert_nothing_changed(world, start, "no Branch declared")


# ================================================================== #
#  Property 14
# ================================================================== #
#
# The property is stated over the OUTCOME, not over the fault: whatever went
# wrong, a request that did not end in an accepted Pending operation leaves the
# resource map byte-identical, and one that did reduces it by exactly the cost —
# and by nothing at all for an NPC base. So the test derives its expectation from
# the outcome the driver answered, and separately asserts that the injected fault
# produced the outcome it was supposed to, which is what stops the property from
# passing vacuously on a driver that refused everything.
#
# The fault pool is the shared :data:`FAULT_POINTS` plus one more. The three
# shared post-charge points are the ones R8.6 exists for — the vector's hook, the
# tracking, and the persist inside the state write — and there is a quieter
# fourth: a record ``build_record`` hands back already **terminal**, which the
# single state writer declines to move (R8.2) without raising at all. It reaches
# the same refund path by a different route, so it is drawn alongside them rather
# than left to the unit tests.
#
# Two dimensions the property leans on deliberately:
#
# - **Which Balance_Config field the cost comes from.** ``_resource_cost`` reads
#   the Operation_Kind entry's ``cost_field`` binding, with the ``<kind>_cost``
#   convention as the fallback. Both spellings are drawn, and the unused one
#   always holds :data:`DECOY_COST`, so a driver that read the wrong field would
#   charge 999 Nexium and fail the conservation clause.
# - **Whether the shortfall breakdown is readable.** The ``resources`` pre-check
#   passes when it cannot read a breakdown, because the whole-or-none charge is
#   the authority (R12.2). That is the only way the ``charge`` fault point is
#   reachable at all, and it is the reason a refused charge has to answer the
#   ``resources`` check in the *same* have-and-need shape the pre-check reports
#   (R12.3) — asserted here as one fixed key set that both paths produce.

#: The fault points a request can be injected at, in request order: the shared
#: pool plus the terminal record the single state writer declines to move.
LIFECYCLE_FAULT_POINTS: tuple[str, ...] = (*FAULT_POINTS, "terminal_record")

#: The four that land AFTER the cost is charged — the ones a refund answers for
#: (R8.6). The three the shared module names, plus the quiet fourth.
REFUNDABLE_FAULT_POINTS: tuple[str, ...] = (
    *POST_CHARGE_FAULT_POINTS, "terminal_record",
)


def _affordable(holdings, cost, short):
    """Return the resource map a Property 14 player starts with.

    *short* asks for a player who cannot pay: every cost line is pushed below its
    need, so the **real** whole-or-none charge refuses and R12.2's "no partial
    deduction" is what the conservation clause then measures. Otherwise every
    cost line is covered on top of the drawn holdings, so the charge succeeds and
    the reduction is exactly the cost.
    """
    resources = dict(holdings)
    for resource, amount in cost.items():
        have = resources.get(resource, 0)
        if short:
            resources[resource] = min(have, max(0, amount - 1))
        else:
            resources[resource] = have + amount
    return resources


# Feature: tech-tree-branch-foundation, Property 14: Resources are conserved
# unless an operation reaches Pending
#
# **Validates: Requirements 4.8, 8.4, 8.5, 8.6, 12.2, 12.6**
class TestProperty14ResourceConservation(unittest.TestCase):
    """No Vector_Operation both charges and fails, whatever went wrong."""

    @given(
        cost=cost_map_st,
        holdings=cost_map_st,
        owner_kind=st.sampled_from(("player", "npc")),
        npc_marker=st.sampled_from(("is_sentinel", "npc_type")),
        fault=st.sampled_from(LIFECYCLE_FAULT_POINTS),
        neutralized=st.sampled_from(OPERATION_CHECK_ORDER),
        binding=st.sampled_from(("declared", "fallback")),
        readable_stock=st.booleans(),
    )
    @settings(max_examples=100)
    def test_a_request_charges_exactly_once_or_not_at_all(
        self, cost, holdings, owner_kind, npc_marker, fault, neutralized,
        binding, readable_stock,
    ):
        """**Validates: Requirements 4.8, 8.4, 8.5, 8.6, 12.2, 12.6**"""
        npc = owner_kind == "npc"
        world = _world(
            registry=_Registry(cost=cost, binding=binding),
            resources=_affordable(holdings, cost, short=fault == "charge"),
            readable_stock=readable_stock,
        )
        if npc:
            # R12.6: both markers ``BranchSystem`` itself reads, read here the
            # same way, so neither spelling of an NPC base is charged.
            marker = True if npc_marker == "is_sentinel" else "raider_camp"
            setattr(world.player.db, npc_marker, marker)
        if fault == "check":
            setattr(world.vector, f"_check_{neutralized}", None)
        elif fault == "build_record":
            world.vector.fail_build = True
        elif fault == "track":
            world.vector._track = lambda record: 1 / 0
        elif fault == "persist":
            world.vector._persist = lambda record: 1 / 0
        elif fault == "terminal_record":
            world.vector.terminal_record = True

        # The reference cost: what R12.1 says this request costs, and what R12.6
        # says it costs an NPC base instead.
        expected_cost = (
            {} if npc else {r: a for r, a in cost.items() if a > 0}
        )
        start = world.player.resource_snapshot()

        outcome = _send(world)
        after = world.player.resource_snapshot()

        # -- 1. The conservation claim, stated over the outcome ------------- #
        self.assertIsInstance(outcome, OperationOutcome)
        if outcome.ok:
            self.assertEqual(outcome.state, str(OperationState.PENDING))
            self.assertEqual(
                after,
                {r: have - expected_cost.get(r, 0) for r, have in start.items()},
                "an accepted operation must cost exactly the configured amount",
            )
            placed = world.vector.tracked_records()
            self.assertEqual(len(placed), 1)
            self.assertEqual(
                placed[0].charged, expected_cost,
                "the accepted record must carry what it was charged, for the refund",
            )
        else:
            self.assertEqual(
                after, start,
                f"a request that ended {outcome.check!r} changed the resource map",
            )
            self.assertEqual(world.vector.tracked_records(), [])
            self.assertEqual(_read_records(world.vector.owner), [])
            self.assertFalse(world.branch.called("note_cooldown"))

        # -- 2. An NPC base is charged nothing, and asked nothing (R12.6) ---- #
        if npc:
            self.assertEqual(after, start, "an NPC base's operation must be free")
            self.assertFalse(world.branch.called("charge"))
            self.assertFalse(world.branch.called("resource_shortfall"))

        # -- 3. The fault produced the outcome it was supposed to ------------ #
        # A free operation is never charged at all, so the ``charge`` fault has
        # nothing to bite on and the request passes — which is itself part of the
        # claim (R12.6: an NPC base's operation is free, and free means placed).
        charged_something = bool(expected_cost)
        if fault == "none" or (fault == "charge" and not charged_something):
            self.assertTrue(
                outcome.ok, f"{fault!r} with a cost of {expected_cost} must pass"
            )
        elif fault == "check":
            self.assertEqual(outcome.check, neutralized)
        elif fault == "charge":
            # R12.3, and behaviour the pre-check and the charge must share: one
            # have-and-need shape, whichever of the two refused.
            self.assertEqual(outcome.check, "resources")
            self.assertEqual(set(outcome.detail), INSUFFICIENT_DETAIL_KEYS)
            self.assertEqual(
                outcome.detail["message"], MSG_VECTOR_INSUFFICIENT_RESOURCES
            )
            self.assertEqual(outcome.detail["cost"], expected_cost)
            self.assertEqual(outcome.detail["kind"], KIND)
            self.assertEqual(
                world.branch.called("charge"), not readable_stock,
                "an unreadable breakdown must leave the charge as the authority",
            )
        else:
            # R8.6: charged, and never reached Pending.
            self.assertEqual(outcome.check, "pending_entry")
            self.assertIsNone(outcome.state)
            self.assertEqual(
                outcome.detail, {"kind": KIND, "refunded": expected_cost}
            )

        # -- 4. The refund is the whole charge, exactly once (R8.6) ---------- #
        charges = world.branch.calls_to("charge")
        refunds = world.branch.calls_to("refund")
        if fault in REFUNDABLE_FAULT_POINTS and charged_something:
            self.assertEqual(len(charges), 1)
            self.assertEqual(len(refunds), 1, "the refund must happen exactly once")
            self.assertEqual(
                refunds[0][2], charges[0][2],
                "the refund must be the whole charged amount, not part of it",
            )
        else:
            self.assertEqual(
                refunds, [],
                f"{fault!r} refunded {refunds}, but nothing was owed back",
            )


# ================================================================== #
#  Property 24
# ================================================================== #
#
# "Every public query" is only a claim if the surface is enumerated, so the
# subject here is a TABLE keyed by method name — the same shape
# ``test_branch_architecture.query_answers`` uses — and the completeness clause
# asserts that table plus the ten hooks is exactly ``OperationDriver``'s public
# surface. A method added to the driver without being classified fails there
# rather than quietly escaping the guard.
#
# Three flavours of driver are asked the whole table on every example, because
# R15.3's claim is about the degraded cases as much as the wired one:
#
# * **wired** — every collaborator injected and every service answering;
# * **unwired** — no Branch_System at all, which R15.2 says degrades to a refusal;
# * **bare** — a driver with no registry, no event bus, no Branch_System, and
#   every required hook unimplemented. Its hooks raise ``NotImplementedError`` by
#   design, which is exactly why the driver guards every call site: the last
#   clause asserts both halves of that, because "the hook raises" and "the entry
#   point still answers" are the two things that together make R15.3 true.
#
# The Branch_System half of the same claim is asserted over the **real**
# ``BranchSystem``, unwired, for exactly the seventeen services the driver
# consumes — read off the shipped module by an AST scan of its ``_ask`` call
# sites, so the list cannot drift from the code that asks.

#: The documented return type of each public driver entry point. ``request`` and
#: ``cancel`` answer an ``OperationOutcome``; the transitions answer whether they
#: happened; the three world-event handlers and the rebuild answer a count;
#: ``advance_all`` answers nothing at all.
DRIVER_ANSWER_TYPES: dict[str, object] = {
    "request": OperationOutcome,
    "tracked_records": list,
    "advance_all": type(None),
    "suspend": bool,
    "resume": bool,
    "cancel": OperationOutcome,
    "rebuild": int,
    "handle_player_eliminated": int,
    "handle_building_destroyed": int,
    "handle_base_eliminated": int,
    "apply_hit": int,
    "apply_effect": bool,
}

#: Marks a service whose documented answer is "whatever the world holds", so
#: only "it answered, and did not raise" can be asserted about its type.
ANY_TYPE = object()

#: The documented return type of each Branch service the driver consumes. A
#: ``BranchRefusal`` **is** a ``str`` (the message key) carrying its structured
#: payload on ``data``, which is why the gates are typed ``(str, None)``.
BRANCH_ANSWER_TYPES: dict[str, object] = {
    "commitment": (str, type(None)),
    "lab_for_branch": (str, type(None)),
    "branch_of_technology": (str, type(None)),
    "role_for_branch": (str, type(None)),
    "is_operational": bool,
    "applied_technologies": frozenset,
    "eligible_carrier": ANY_TYPE,
    "may_target": (str, type(None)),
    "cooldown_remaining": int,
    "in_flight_count": int,
    "in_flight_cap": int,
    "resource_shortfall": dict,
    "response_window": int,
    "charge": bool,
    "refund": type(None),
    "note_cooldown": type(None),
    "note_escalation": type(None),
}

#: The six request parameters the driver reads by name (see
#: ``OperationContext``). Drawn adversarially alongside random keys, because a
#: mapping of random text would practically never name one of them and the
#: parameters the driver actually reads are where a wrong type would land.
DRIVER_PARAMS: tuple[str, ...] = ("building", "target", "x", "y", "hostile", "planet")

#: Wrong types, ``None``, empty, and adversarially large values — the input space
#: the property names. ``st.builds(object)`` mints a fresh object with no
#: attributes at all, which is the shape every duck-typed read must survive.
adversarial_st = st.one_of(
    st.none(),
    st.booleans(),
    st.text(max_size=8),
    st.integers(),
    st.sampled_from((10 ** 60, -(10 ** 60))),
    st.floats(allow_nan=True, allow_infinity=True),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
    st.builds(object),
)


@st.composite
def _request_params(draw):
    """Draw the parameter mapping a request is made with.

    Random keys plus an independently drawn subset of the six the driver reads.
    ``player`` is excluded by construction: it is ``request``'s positional
    argument, so a key of that name is a caller error (``TypeError`` at the call
    site) rather than a parameter the driver could answer about.
    """
    params = dict(draw(
        st.dictionaries(st.text(max_size=6), adversarial_st, max_size=4)
    ))
    for name in DRIVER_PARAMS:
        if draw(st.booleans()):
            params[name] = draw(adversarial_st)
    params.pop("player", None)
    return params


# Feature: tech-tree-branch-foundation, Property 24: Every request and every
# public query returns a value and raises nothing
#
# **Validates: Requirements 8.24, 15.3**
class TestProperty24EverythingAnswers(unittest.TestCase):
    """A command layer reads a result; it never guards a call."""

    def _answer(self, label, call):
        """Return *call*'s answer, failing the test if it raised (R15.3)."""
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - R15.3: it must answer instead
            self.fail(f"{label} raised {type(error).__name__}: {error}")

    def _assert_type(self, label, answer, expected):
        if expected is ANY_TYPE:
            return
        self.assertIsInstance(
            answer, expected,
            f"{label} answered a {type(answer).__name__}, not its documented type",
        )

    def _driver_answers(self, vector, record, adversary, params):
        """Answer every public driver entry point over adversarial input.

        Each state-mutating call gets its **own copy** of the drawn record, so
        every one is exercised on the drawn lifecycle state rather than on
        whatever the previous call left behind.
        """
        vector._track(replace(record))
        return {
            "request": self._answer(
                "request", lambda: vector.request(adversary, **params)
            ),
            "tracked_records": self._answer(
                "tracked_records", vector.tracked_records
            ),
            "advance_all": self._answer(
                "advance_all", lambda: vector.advance_all(adversary)
            ),
            "suspend": self._answer(
                "suspend", lambda: vector.suspend(replace(record), adversary)
            ),
            "resume": self._answer(
                "resume", lambda: vector.resume(replace(record))
            ),
            "cancel": self._answer(
                "cancel", lambda: vector.cancel(replace(record), adversary)
            ),
            "rebuild": self._answer("rebuild", lambda: vector.rebuild(adversary)),
            "handle_player_eliminated": self._answer(
                "handle_player_eliminated",
                lambda: vector.handle_player_eliminated(adversary, victim=adversary),
            ),
            "handle_building_destroyed": self._answer(
                "handle_building_destroyed",
                lambda: vector.handle_building_destroyed(
                    adversary, building=adversary
                ),
            ),
            "handle_base_eliminated": self._answer(
                "handle_base_eliminated",
                lambda: vector.handle_base_eliminated(
                    adversary, sentinel=adversary, sentinel_id=adversary,
                    planet=adversary,
                ),
            ),
            "apply_hit": self._answer(
                "apply_hit",
                lambda: vector.apply_hit(replace(record), adversary, adversary),
            ),
            "apply_effect": self._answer(
                "apply_effect",
                lambda: vector.apply_effect(replace(record), adversary, adversary),
            ),
        }

    def _assert_outcome_is_readable(self, outcome, label):
        """R8.24: the outcome names the resulting lifecycle state or the refusal."""
        self.assertIsInstance(outcome, OperationOutcome)
        if outcome.ok:
            self.assertIn(
                str(outcome.state), OPERATION_STATE_VALUES,
                f"{label} accepted without naming a lifecycle state",
            )
            self.assertTrue(outcome.op_id, f"{label} accepted without an identity")
        else:
            self.assertIsInstance(outcome.check, str)
            self.assertTrue(outcome.check, f"{label} refused without naming a check")
            if outcome.detail is not None:
                self.assertIsInstance(outcome.detail, dict)

    @given(
        record=record_st,
        params=_request_params(),
        adversary=adversarial_st,
        real_player=st.booleans(),
        live_refs=st.booleans(),
    )
    @settings(max_examples=100)
    def test_every_entry_point_answers_and_never_raises(
        self, record, params, adversary, real_player, live_refs
    ):
        """**Validates: Requirements 8.24, 15.3**"""
        # -- 0. The table is the whole public surface, not a selection ------- #
        classified = (
            frozenset(DRIVER_ANSWER_TYPES)
            | frozenset(REQUIRED_HOOKS)
            | frozenset(OPTIONAL_HOOKS)
        )
        self.assertEqual(
            classified, public_method_names(),
            "a public OperationDriver method is neither in the answer table nor "
            "named as a hook, so Property 24 does not cover it — add it to "
            "DRIVER_ANSWER_TYPES (an entry point) or to the hook tuples",
        )
        # And the Branch service table is exactly what the shipped driver asks,
        # so a newly consumed service cannot quietly take its guarded default.
        self.assertEqual(
            frozenset(BRANCH_ANSWER_TYPES), frozenset(_asked_services()),
            "the Branch services the driver asks and the services this property "
            "answers for have drifted apart",
        )

        # -- 1. Every driver entry point, over three flavours of driver ------ #
        for flavour in ("wired", "unwired", "bare"):
            world = _world(resources={"Iron": 500, "Circuits": 500})
            if flavour == "wired":
                vector = world.vector
            elif flavour == "unwired":
                vector = _Vector(world.registry, world.bus, branch_system=None)
            else:
                vector = _BareDriver()
            subject = replace(record)
            if live_refs:
                subject = replace(
                    subject,
                    owner_ref=world.player,
                    building_ref=world.building,
                    carrier_ref=world.branch.carrier,
                    target_ref=world.building,
                )
            sent = dict(params)
            if real_player:
                sent.setdefault("building", world.building)
            actor = world.player if real_player else adversary

            answers = self._driver_answers(vector, subject, actor, sent)
            for name, expected in DRIVER_ANSWER_TYPES.items():
                self._assert_type(f"{flavour}.{name}", answers[name], expected)
            self._assert_outcome_is_readable(answers["request"], f"{flavour}.request")
            self._assert_outcome_is_readable(answers["cancel"], f"{flavour}.cancel")
            # A refusal names a check of the chain, the Pending entry, or the
            # last-resort net around the whole request — never a blank.
            if not answers["request"].ok:
                self.assertIn(
                    answers["request"].check,
                    CHECK_NAMES | {"pending_entry", "request"},
                    f"{flavour}.request refused something that is not a check",
                )

        # -- 2. The hooks: the reason every call site above is guarded -------- #
        bare = _BareDriver()
        for name in REQUIRED_HOOKS:
            with self.assertRaises(NotImplementedError, msg=f"the {name} hook"):
                getattr(bare, name)(replace(record))
        for name in OPTIONAL_HOOKS:
            self.assertIsNone(
                self._answer(
                    f"bare.{name}",
                    lambda n=name: getattr(bare, n)(replace(record)),
                ),
                f"the optional hook {name} must default to a no-op",
            )

        # -- 3. Every Branch service the driver consumes, unwired ------------ #
        system = BranchSystem(fixture_registry(), _Bus())
        calls = {
            "commitment": lambda: system.commitment(adversary, adversary),
            "lab_for_branch": lambda: system.lab_for_branch(adversary),
            "branch_of_technology": lambda: system.branch_of_technology(adversary),
            "role_for_branch": lambda: system.role_for_branch(adversary),
            "is_operational": lambda: system.is_operational(adversary),
            "applied_technologies": lambda: system.applied_technologies(
                adversary, adversary
            ),
            "eligible_carrier": lambda: system.eligible_carrier(
                adversary, adversary, adversary
            ),
            "may_target": lambda: system.may_target(
                adversary, adversary, hostile=adversary
            ),
            "cooldown_remaining": lambda: system.cooldown_remaining(
                adversary, adversary
            ),
            "in_flight_count": lambda: system.in_flight_count(
                adversary, adversary, adversary
            ),
            "in_flight_cap": lambda: system.in_flight_cap(adversary),
            "resource_shortfall": lambda: system.resource_shortfall(
                adversary, adversary
            ),
            "response_window": lambda: system.response_window(adversary, adversary),
            "charge": lambda: system.charge(adversary, adversary),
            "refund": lambda: system.refund(adversary, adversary),
            "note_cooldown": lambda: system.note_cooldown(adversary, adversary),
            "note_escalation": lambda: system.note_escalation(adversary, adversary),
        }
        self.assertEqual(frozenset(calls), frozenset(BRANCH_ANSWER_TYPES))
        for name, call in calls.items():
            answer = self._answer(f"BranchSystem.{name}", call)
            self._assert_type(
                f"BranchSystem.{name}", answer, BRANCH_ANSWER_TYPES[name]
            )


# ================================================================== #
#  Property 15
# ================================================================== #
#
# Three claims, and the third is what keeps the first two from passing
# vacuously:
#
# 1. **The state is always one of the six** (R8.1). Structural, because
#    ``_transition`` is the single writer and it declines a name outside the six
#    — so this clause is the guard that the single writer stays the only writer.
# 2. **A terminal state is final** (R8.2): once the record reaches Resolved,
#    Expired, Cancelled, or Discarded, no later event changes its state or either
#    of its clocks, and no terminal hook fires a second time. Asserted twice
#    over, and deliberately: once against the values the record held before the
#    event (which needs no model at all), and once against the reference model,
#    which freezes for the same reason.
# 3. **Each event drives the transition the contract declares.** A driver that
#    ignored every event would satisfy the first two clauses completely. So the
#    ten events that are not a tick are checked against the state the design's
#    §4.1 diagram gives them, and a tick is checked against a reference reading
#    of §4.7's order.
#
# The events are delivered the way the game delivers them, which is not the same
# way for all of them:
#
# * the four **announced** conditions — a slain carrier, the originating building
#   destroyed, this Branch's lab destroyed, a whole base wiped — are PUBLISHED on
#   the same bus the driver subscribed to in its constructor, so the three
#   subscriptions are exercised through their own wiring rather than by calling
#   the handlers directly. The carrier is published as a victim that is still
#   ALIVE, because ``CombatEngine`` respawns a slain agent before it publishes:
#   that is precisely why R8.16 needs a subscription and cannot be polled.
# * the tick and the six **requested** transitions are called on the driver.
#
# A tick is delivered as ``_advance_one``, the per-operation body of one tick,
# because that is where R8.2's guard lives; ``advance_all`` is the fan-out around
# it and is asserted separately at the foot of the test, over a record that has
# already settled.
#
# Two operations ride along untouched as the control for R11.4's "for exactly
# those operations whose building was removed" and R8.18's per-planet scope: one
# of the same owner on another planet, one of another owner on this planet.

#: Event -> the state the contract declares it drives an operation to (design
#: §4.1). ``tick`` is absent because its destination depends on the two clocks,
#: which is the one thing in this vocabulary that is not a fixed edge.
LIFECYCLE_EVENT_TARGET_STATE: dict[str, str] = {
    "suspend": str(OperationState.SUSPENDED),
    "resume": str(OperationState.PENDING),
    "resolve": str(OperationState.RESOLVED),
    "expire": str(OperationState.EXPIRED),
    "cancel": str(OperationState.CANCELLED),
    "discard": str(OperationState.DISCARDED),
    "carrier_killed": str(OperationState.CANCELLED),        # R8.16
    "building_lost": str(OperationState.CANCELLED),         # R8.17
    "commitment_lost": str(OperationState.SUSPENDED),       # R8.18
    "base_eliminated": str(OperationState.CANCELLED),       # R11.4
}


#: The four **announced** conditions -> the reason KEY the notification that
#: reports them must carry. R8.16, R8.17, and R11.4 each ask that the operation's
#: owner be told of the cancellation, and the reason is how they learn *which*
#: collaborator was lost; R8.18's suspension names its cause the same way. A key,
#: never a sentence (R13.5).
LIFECYCLE_EVENT_REASON: dict[str, str] = {
    "carrier_killed": CANCEL_CARRIER_KILLED,
    "building_lost": CANCEL_ORIGIN_LOST,
    "commitment_lost": SUSPEND_COMMITMENT_LAPSED,
    "base_eliminated": CANCEL_BASE_ELIMINATED,
}


def _wipe_base(world, _tick):
    """Publish a base elimination naming the Sentinel's PRE-DELETE id (R11.4).

    By the time this event is published the base's buildings *and* its Sentinel
    have been deleted, so the record's ``building_ref`` no longer resolves and
    the Sentinel's own ``id`` reads as ``None`` — which is why the payload
    carries the id it held before the delete, and why the handler matches on
    ownership rather than on the origin the ``BUILDING_DESTROYED`` handler
    matches on.
    """
    world.bus.publish(
        BASE_ELIMINATED,
        sentinel=SimpleNamespace(id=None, pk=None),
        sentinel_id=world.player.id,
        planet=PLANET,
    )


#: How each lifecycle event reaches the operation. A dict rather than a chain of
#: branches so the vocabulary can be asserted **complete**: a new event added to
#: ``LIFECYCLE_EVENTS`` with no delivery here fails Property 15's first clause
#: rather than being silently skipped.
LIFECYCLE_DELIVERY: dict = {
    "tick": lambda world, tick: world.vector._advance_one(world.record, tick),
    "suspend": lambda world, _tick: world.vector.suspend(
        world.record, SUSPEND_CARRIER_UNAVAILABLE
    ),
    "resume": lambda world, _tick: world.vector.resume(world.record),
    "resolve": lambda world, _tick: world.vector._resolve(world.record),
    "expire": lambda world, _tick: world.vector._expire(world.record),
    "cancel": lambda world, _tick: world.vector.cancel(
        world.record, CANCEL_ORIGIN_LOST
    ),
    "discard": lambda world, _tick: world.vector._discard(
        world.record, ("owner_ref",)
    ),
    # Published, not called: the driver's own subscriptions are the subject.
    "carrier_killed": lambda world, _tick: world.bus.publish(
        PLAYER_ELIMINATED, victim=world.carrier
    ),
    "building_lost": lambda world, _tick: world.bus.publish(
        BUILDING_DESTROYED, building=world.building
    ),
    # This Branch's LAB, not the origin: the commitment lapsing (R8.18).
    "commitment_lost": lambda world, _tick: world.bus.publish(
        BUILDING_DESTROYED, building=world.lab
    ),
    "base_eliminated": _wipe_base,
}


class _LifecycleModel:
    """The contract's own reading of one operation's lifecycle, as a reference.

    Written from the requirements rather than from the driver: each transition
    below cites the criterion that declares it, and the tick reproduces the
    **order** design §4.7 fixes — the pause before the clock, the resume before
    the progress, and the bounded lifetime before the effect clock, so an
    operation that runs out of life on the tick its effect would land Expires
    rather than Resolving (R8.13 is a deadline, and a deadline a tie could beat
    would not be one).

    Neither pausing *condition* varies while this model runs: the fixture's
    carrier is never benched and its ``commitment`` service keeps answering this
    Branch, so a suspension here is always something an event did and a later
    tick is free to resume it. Property 16 is the one that varies the conditions.
    """

    def __init__(self, record, floor):
        self.state = str(record.state)
        self.ticks = int(record.ticks_remaining)
        self.lifetime = record.lifetime_remaining
        self.snapshot = record.suspended_ticks
        self.floor = floor
        self.hooks: list[str] = []

    @property
    def settled(self):
        """Whether a terminal state has been reached (R8.2)."""
        return self.state in TERMINAL_STATE_VALUES

    @property
    def suspended(self):
        return self.state == str(OperationState.SUSPENDED)

    def _settle(self, state):
        self.state = str(state)
        self.hooks.append(TERMINAL_HOOK_FOR_STATE[self.state])

    def _suspend(self):
        """R8.14, R8.18, and R8.15's snapshot. Idempotent, like the driver's."""
        if self.suspended:
            return
        self.snapshot = max(0, self.ticks)
        self.state = str(OperationState.SUSPENDED)

    def _resume(self):
        """R8.15: the ticks held on suspension, re-floored for R8.8."""
        held = self.ticks if self.snapshot is None else max(0, self.snapshot)
        self.ticks = max(self.floor, held)
        self.snapshot = None
        self.state = str(OperationState.PENDING)

    def _tick(self):
        if self.suspended:
            self._resume()                                  # R8.15
        if self.lifetime is not None:
            self.lifetime -= 1
            if self.lifetime <= 0:
                self._settle(OperationState.EXPIRED)        # R8.13
                return
        self.ticks -= 1
        if self.ticks <= 0:
            self._settle(OperationState.RESOLVED)           # R8.11

    def apply(self, event):
        """Move the model as *event* declares, or not at all once terminal."""
        if self.settled:
            return                                          # R8.2
        if event == "tick":
            self._tick()
        elif event in ("suspend", "commitment_lost"):
            self._suspend()
        elif event == "resume":
            if self.suspended:
                self._resume()
        else:
            self._settle(LIFECYCLE_EVENT_TARGET_STATE[event])


# Feature: tech-tree-branch-foundation, Property 15: A terminal state is final,
# and each event drives the expected transition
#
# **Validates: Requirements 8.1, 8.2, 8.11, 8.13, 8.16, 8.17, 8.18, 11.4**
class TestProperty15TerminalFinality(unittest.TestCase):
    """Four sinks, ten edges, and no event that reaches past a sink."""

    def _assert_frozen(self, record, before, where):
        """R8.2: a terminal record's state and both clocks are untouchable."""
        self.assertEqual(
            (
                str(record.state),
                record.ticks_remaining,
                record.lifetime_remaining,
            ),
            before,
            f"{where}: an event moved an operation that had already settled",
        )

    def _assert_declared_transition(self, record, event, before_state, where):
        """R8.1's edges: each event drives the state the contract declares."""
        declared = LIFECYCLE_EVENT_TARGET_STATE[event]
        if event == "resume" and before_state != str(OperationState.SUSPENDED):
            declared = before_state          # nothing was paused, so nothing resumes
        self.assertEqual(
            str(record.state), declared,
            f"{where}: {event!r} left the operation {record.state!r}, not {declared!r}",
        )

    def _assert_matches_model(self, record, model, where):
        """The reference reading of the state and of both clocks."""
        self.assertEqual(str(record.state), model.state, f"{where}: the state")
        self.assertEqual(
            record.ticks_remaining, model.ticks, f"{where}: the effect clock"
        )
        self.assertEqual(
            record.lifetime_remaining, model.lifetime, f"{where}: the lifetime"
        )
        self.assertEqual(
            record.suspended_ticks, model.snapshot, f"{where}: the snapshot"
        )

    def _assert_untouched(self, record, where):
        """A bystander operation is still Pending on the clock it was given."""
        self.assertEqual(
            (str(record.state), record.ticks_remaining),
            (str(OperationState.PENDING), 50),
            f"{where}: an operation that was no part of the event changed",
        )

    @given(events=event_sequence_st, record=record_st)
    @settings(max_examples=200)
    def test_terminal_state_is_final(self, events, record):
        """**Validates: Requirements 8.1, 8.2, 8.11, 8.13, 8.16, 8.17, 8.18, 11.4**"""
        # -- 0. The vocabulary is delivered whole, not in part ---------------- #
        self.assertEqual(
            frozenset(LIFECYCLE_DELIVERY), frozenset(LIFECYCLE_EVENTS),
            "a lifecycle event has no delivery here, so Property 15 does not "
            "drive it — add it to LIFECYCLE_DELIVERY",
        )
        self.assertEqual(
            frozenset(LIFECYCLE_EVENT_TARGET_STATE) | {"tick"},
            frozenset(LIFECYCLE_EVENTS),
            "a lifecycle event names no declared destination",
        )

        # The drawn record supplies the LIFECYCLE SHAPE — the state and both
        # clocks, including the terminal states and the zero clocks — while the
        # placed operation supplies the live references the transitions and the
        # three subscriptions need. Mutated in place rather than replaced, so the
        # record under test is the one the vector is really tracking.
        world = _placed()
        subject = world.record
        subject.state = str(record.state)
        subject.ticks_remaining = record.ticks_remaining
        subject.lifetime_remaining = record.lifetime_remaining
        subject.suspended_ticks = record.suspended_ticks
        model = _LifecycleModel(subject, FLOOR)
        elsewhere = _bystander(world, "op-elsewhere", planet=OTHER_PLANET)
        stranger = _bystander(
            world, "op-stranger", owner=FakePlayer(key="Rival", planet=PLANET)
        )

        settled = model.settled
        for index, event in enumerate(events):
            where = f"event {index} ({event})"
            before = (
                str(subject.state),
                subject.ticks_remaining,
                subject.lifetime_remaining,
            )
            told = len(world.bus.notifications())
            model.apply(event)
            LIFECYCLE_DELIVERY[event](world, index)
            fresh = world.bus.notifications()[told:]

            # -- 1. R8.1: one of the six declared states, always ------------- #
            self.assertIn(
                str(subject.state), OPERATION_STATE_VALUES,
                f"{where}: {subject.state!r} is not a lifecycle state",
            )
            # -- 2. R8.2: a terminal state is final -------------------------- #
            if settled:
                self._assert_frozen(subject, before, where)
            # -- 3. Each event drives the declared transition ---------------- #
            elif event != "tick":
                self._assert_declared_transition(subject, event, before[0], where)
            self._assert_matches_model(subject, model, where)

            # -- 3b. And the owner is told which collaborator was lost ------- #
            moved = before[0] != str(subject.state)
            if event in LIFECYCLE_EVENT_REASON and moved:
                self.assertIn(
                    LIFECYCLE_EVENT_REASON[event],
                    [entry["data"].get("reason") for entry in fresh],
                    f"{where}: the owner was not told what ended or paused it",
                )
            if event == "commitment_lost" and not moved and not settled:
                # Suspending an already-suspended operation is quiet: the tick
                # asks on every tick the condition holds, and an owner told once
                # should not be told again.
                self.assertEqual(
                    fresh, [], f"{where}: a re-suspend notified the owner again"
                )
            settled = model.settled

        # -- 4. At most one terminal hook, and it names the state reached ----- #
        fired = world.vector.fired(TERMINAL_HOOKS)
        self.assertEqual(
            fired, model.hooks,
            "the terminal hooks that fired are not the ones the transitions taken "
            "call for",
        )
        self.assertLessEqual(
            len(fired), 1, f"{fired} fired, but an operation settles once (R8.2)"
        )
        if model.hooks:
            # It settled DURING the sequence, so exactly its own hook ran. A
            # record drawn already terminal runs none at all, which is the same
            # claim read from the other side.
            self.assertEqual(
                fired, [TERMINAL_HOOK_FOR_STATE[model.state]],
                f"reaching {model.state!r} must run exactly its own hook",
            )

        # -- 5. Only the operations the event named were touched ------------- #
        # R11.4 cancels the operations whose building was removed, and R8.18
        # suspends the owner's operations ON THAT PLANET: an operation of the
        # same owner elsewhere and one of another owner here are neither.
        self._assert_untouched(elsewhere, "the same owner on another planet")
        self._assert_untouched(stranger, "another owner on this planet")

        # -- 6. The tick fan-out advances a settled record no further -------- #
        if model.settled:
            frozen = (
                str(subject.state),
                subject.ticks_remaining,
                subject.lifetime_remaining,
            )
            world.vector.advance_all(len(events))
            self._assert_frozen(subject, frozen, "advance_all")
            self.assertNotIn(
                subject, world.vector.tracked_records(),
                "a settled operation must leave the tick loop",
            )

        # -- 7. The two POLLED conditions, which no event announces ---------- #
        # R8.14's benched carrier and R8.18's lapsed commitment are read off the
        # world by the tick itself, because nothing publishes either one. Both
        # suspend, and neither spends a tick of the clock doing it.
        polled = _placed(ticks=FLOOR + 3)
        paused_at = polled.record.ticks_remaining
        polled.carrier.bench()
        self.assertTrue(polled.vector._advance_one(polled.record, 0))
        self.assertEqual(str(polled.record.state), str(OperationState.SUSPENDED))
        self.assertEqual(polled.record.ticks_remaining, paused_at)
        polled.carrier.bench(False)
        self.assertTrue(polled.vector.resume(polled.record))
        polled.branch.commitment_answer = RIVAL_BRANCH
        self.assertTrue(polled.vector._advance_one(polled.record, 1))
        self.assertEqual(str(polled.record.state), str(OperationState.SUSPENDED))
        self.assertEqual(polled.record.ticks_remaining, paused_at)


# ================================================================== #
#  Property 16
# ================================================================== #
#
# The claim is arithmetic, and it has an interaction with R8.8 that has to be
# stated rather than dodged.
#
# **The floor and the snapshot.** ``resume`` restores the snapshot and then
# re-floors a hostile window through ``_floor_response_window`` (design §4.5,
# and ``test_a_resumed_hostile_window_is_re_floored`` pins the exact reading).
# The floor is a ``max``, so it can only ever LENGTHEN the clock — which means a
# **hostile** operation suspended with fewer ticks left than
# ``minimum_response_window_ticks`` resumes at the FLOOR, not at the snapshot.
# That is consistent with R8.15 rather than a restart of it: the restored value
# is the snapshot, and a floor is a floor. But it does mean "the ticks after a
# resume equal the ticks at the corresponding suspension" is only literally true
# where the floor cannot reach, so the property is stated in the general form
# ``max(applicable floor, snapshot)`` and drawn over three configurations:
#
# * **supporting** — a vector whose ``_is_hostile`` is ``False``, which design
#   §4.5 exempts from the floor outright ("no window to protect");
# * **floorless** — hostile, with ``minimum_response_window_ticks`` configured to
#   zero;
# * **floored** — hostile, at the fixture's floor of seven, where the
#   lengthening is real and is asserted as such.
#
# In the first two the general form collapses to the literal equality, so R8.15
# is asserted in its own words there and the third is where the interaction is
# pinned.
#
# **What "advance" means while paused.** R8.14 says that WHILE the carrier is
# incapacitated or in reserve the operation is Suspended and advances no further,
# so a drawn ``suspend`` benches the carrier and a drawn ``resume`` brings it
# back. That is what makes "no advance while Suspended changes it" a claim about
# a held pause rather than about a state label: an advance whose pausing
# condition has lifted resumes the operation and then takes that tick's progress,
# which is the documented order and not a violation.

#: The three inputs Property 16 sequences (design: ``st.lists(st.sampled_from(
#: ("advance", "suspend", "resume")))``).
TIMING_OPS: tuple[str, ...] = ("advance", "suspend", "resume")

#: Configuration -> whether the operation is hostile, and the floor its registry
#: configures. The applicable floor is zero for the two the floor cannot reach.
TIMING_CONFIGS: dict[str, tuple[bool, int]] = {
    "supporting": (False, FLOOR),
    "floorless": (True, 0),
    "floored": (True, FLOOR),
}


def _timing_world(config, clock):
    """Return a world holding one operation in the timing *config*."""
    hostile, floor = TIMING_CONFIGS[config]
    vector_cls = _LifecycleVector if hostile else _SupportingVector
    world = _staged(registry=_Registry(floor=floor), vector_cls=vector_cls)
    world.floor = floor if hostile else 0
    return _place(world, ticks=clock, hostile=hostile)


class _TimingModel:
    """The reference clock: what a pause costs, and what a tick costs.

    Written from R8.9, R8.14, and R8.15 — one tick is one decrement, a held pause
    is no decrement at all, and a resume restores the snapshot (floored for a
    hostile window, R8.8).
    """

    def __init__(self, clock, floor):
        self.state = str(OperationState.PENDING)
        self.ticks = int(clock)
        self.snapshot = None
        self.floor = floor
        self.benched = False

    @property
    def settled(self):
        return self.state in TERMINAL_STATE_VALUES

    @property
    def suspended(self):
        return self.state == str(OperationState.SUSPENDED)

    def resumed_clock(self):
        """R8.15 with R8.8: the snapshot, and never below the floor."""
        held = self.ticks if self.snapshot is None else max(0, self.snapshot)
        return max(self.floor, held)

    def suspend(self):
        if self.settled or self.suspended:
            return
        self.snapshot = max(0, self.ticks)
        self.state = str(OperationState.SUSPENDED)

    def resume(self):
        if self.settled or not self.suspended:
            return
        self.ticks = self.resumed_clock()
        self.snapshot = None
        self.state = str(OperationState.PENDING)

    def advance(self):
        if self.settled:
            return
        if self.benched:
            self.suspend()          # R8.14: the pause precedes the clock
            return
        self.resume()               # R8.15: then this tick's own progress
        self.ticks -= 1             # R8.9: exactly one
        if self.ticks <= 0:
            self.state = str(OperationState.RESOLVED)


# Feature: tech-tree-branch-foundation, Property 16: Suspension delays rather
# than restarts, and one tick advances by exactly one
#
# **Validates: Requirements 8.9, 8.14, 8.15**
class TestProperty16SuspensionDelays(unittest.TestCase):
    """A pause costs its own duration, and a tick costs exactly one."""

    def _apply(self, world, model, op, tick, stray=False):
        """Deliver one timing input to the operation and to the model alike."""
        if op == "suspend":
            world.carrier.bench()                       # R8.14: and it HOLDS
            model.benched = True
            world.vector.suspend(world.record, SUSPEND_CARRIER_UNAVAILABLE)
            model.suspend()
            if stray and model.suspended and model.snapshot is not None:
                # A stray write to a paused clock — a hand-edited record, or a
                # vector poking the record it was handed. Nothing sanctions it,
                # and R8.15's snapshot is exactly what makes it recoverable: it
                # is what makes "the ticks held at suspension" a different
                # number from "the ticks it happens to hold now", and therefore
                # a claim at all.
                world.record.ticks_remaining = model.snapshot + 5
                model.ticks = model.snapshot + 5
        elif op == "resume":
            world.carrier.bench(False)
            model.benched = False
            world.vector.resume(world.record)
            model.resume()
        else:
            world.vector._advance_one(world.record, tick)
            model.advance()

    def _run_to_resolution(self, world, pause):
        """Bench the carrier for *pause* ticks, then tick until it resolves.

        Returns the total number of ticks the operation took from placement to
        Resolved, which is the figure R8.15's "so that suspension delays a
        Vector_Operation rather than restarting it" is measured by.
        """
        world.carrier.bench(pause > 0)
        limit = pause + world.record.ticks_remaining + world.floor + 8
        for elapsed in range(limit):
            if elapsed == pause:
                world.carrier.bench(False)
            world.vector._advance_one(world.record, elapsed)
            if str(world.record.state) == str(OperationState.RESOLVED):
                return elapsed + 1
        return None

    @given(
        clock=st.integers(min_value=1, max_value=200),
        ops=st.lists(st.sampled_from(TIMING_OPS), max_size=25),
        config=st.sampled_from(tuple(TIMING_CONFIGS)),
        pause=st.integers(min_value=0, max_value=6),
        stray=st.booleans(),
    )
    @settings(max_examples=200)
    def test_a_suspension_delays_and_a_tick_costs_one(
        self, clock, ops, config, pause, stray
    ):
        """**Validates: Requirements 8.9, 8.14, 8.15**"""
        world = _timing_world(config, clock)
        floor = world.floor
        model = _TimingModel(world.record.ticks_remaining, floor)

        # -- 0. What the operation entered Pending holding (R8.8) ------------ #
        # The floor may have raised what the vector asked for, so "the original
        # count" is read off the record rather than assumed to be the drawn one.
        self.assertEqual(world.clock, max(floor, clock))

        for index, op in enumerate(ops):
            where = f"op {index} ({op}) in the {config} configuration"
            record = world.record
            before_state = str(record.state)
            before_ticks = record.ticks_remaining
            before_snapshot = record.suspended_ticks
            held = model.benched
            self._apply(world, model, op, index, stray=stray)

            # -- 1. The reference clock, input by input ---------------------- #
            self.assertEqual(str(record.state), model.state, f"{where}: the state")
            self.assertEqual(
                record.ticks_remaining, model.ticks, f"{where}: the effect clock"
            )
            self.assertEqual(
                record.suspended_ticks, model.snapshot, f"{where}: the snapshot"
            )

            live = before_state not in TERMINAL_STATE_VALUES
            paused = before_state == str(OperationState.SUSPENDED)

            # -- 2. R8.14: no advance while the pause HOLDS changes anything -- #
            if op == "advance" and held and live:
                self.assertEqual(
                    (str(record.state), record.ticks_remaining),
                    (str(OperationState.SUSPENDED), before_ticks),
                    f"{where}: a paused operation advanced",
                )

            # -- 3. R8.9: an advance of a Pending operation costs exactly one - #
            if op == "advance" and live and not held and not paused:
                self.assertEqual(
                    record.ticks_remaining, before_ticks - 1,
                    f"{where}: one tick moved the clock by "
                    f"{before_ticks - record.ticks_remaining}, not by one",
                )

            # -- 4. R8.15: a resume restores the ticks held on suspension ----- #
            if op == "resume" and live and paused:
                snapshot = before_snapshot if before_snapshot is not None else before_ticks
                self.assertEqual(
                    record.ticks_remaining, max(floor, snapshot),
                    f"{where}: resumed on a clock that is neither the snapshot "
                    "nor the floor",
                )
                self.assertGreaterEqual(
                    record.ticks_remaining, snapshot,
                    f"{where}: a suspension shortened the operation's clock",
                )
                if floor <= snapshot:
                    # The floor cannot reach, so R8.15 in its own words: the
                    # ticks after the resume ARE the ticks held at suspension.
                    self.assertEqual(record.ticks_remaining, snapshot, where)
                else:
                    # And where it can, this is the whole of the interaction: the
                    # floor lengthens the warning R8.8 promises the target, and
                    # never shortens it.
                    self.assertEqual(record.ticks_remaining, floor, where)

        # -- 5. The total elapsed ticks are the clock plus the pause --------- #
        # One operation, benched for a drawn number of ticks and then released:
        # the ticks it takes to resolve are the ticks it carried plus the ticks
        # it spent paused, and not one more.
        run = _timing_world(config, clock)
        carried = run.record.ticks_remaining
        self.assertEqual(
            self._run_to_resolution(run, pause), carried + pause,
            f"a {pause}-tick pause on a {carried}-tick operation did not delay it "
            "by exactly the pause",
        )


# ================================================================== #
#  Property 17
# ================================================================== #
#
# R8.8 is a promise to the *target*: whatever the vector asks for and whatever a
# Counter_Web Response_Window reduction (R9.4's second permitted form) takes off,
# a hostile operation gives its target at least
# ``minimum_response_window_ticks`` of warning. The floor is a ``max`` and not a
# subtraction, which is what lets the claim be stated unconditionally over every
# reduction value — negative (which lengthens), zero, and absurdly large (which
# leaves the floor exactly, never a negative window).
#
# A reduction can enter from either side of the boundary, and both are drawn:
#
# * **vector** — the vector subtracts the reduction itself and asks for an
#   already-reduced window, which is the ordinary shape: it knows its own
#   Counter_Web advantage and hands the driver the number it wants;
# * **service** — the vector delegates the arithmetic to the shared
#   ``BranchSystem.response_window(base, reduction)``, which is what that
#   signature is for.
#
# Both must land on ``max(floor, base - reduction)``, because the clamp is at the
# driver's boundary rather than inside either caller. Both also resolve the
# reduction exactly ONCE per request, which is the shape R9.5 asks for: the
# resume path re-floors through the same helper, and a reduction re-applied on
# every re-floor would shrink the window a tick at a time — the failure this
# pair of routes is here to catch.
#
# The owner kind is **not** drawn: R11.6's clause is that the window and the
# notification set are IDENTICAL for a player-owned and an NPC-base-owned
# operation, and identity needs both in the same example. So each example places
# the same operation twice, changing nothing but the marker that makes its owner
# an NPC base, and compares.


class _DelegatingVector(_LifecycleVector):
    """A vector that asks the SHARED service for its own reduced window.

    The second of the two ways a Counter_Web Response_Window reduction can enter:
    the vector hands ``BranchSystem.response_window`` the base **and** the
    reduction — which is what that signature is for — rather than doing the
    subtraction itself. The reduction is resolved **once per request**, which is
    R9.5's non-compounding reading of an advantage, and the driver's own floor
    then clamps whatever came back.
    """

    reduction = 0

    def build_record(self, ctx):
        record = super().build_record(ctx)
        record.ticks_remaining = self._branch.response_window(
            record.ticks_remaining, self.reduction
        )
        return record


def _notification_log(world):
    """Return ``(recipient name, kind, payload)`` for every notification sent.

    The recipient is reduced to its display name because the two worlds R11.6 is
    compared over hold different player objects: what the requirement asks be
    identical is who was told what, not which objects were used to tell them.
    """
    return [
        (getattr(entry["player"], "key", None), entry["kind"], entry["data"])
        for entry in world.bus.notifications()
    ]


# Feature: tech-tree-branch-foundation, Property 17: The Response_Window never
# falls below the floor, whatever the reduction
#
# **Validates: Requirements 8.8, 9.4, 11.6**
class TestProperty17ResponseWindowFloor(unittest.TestCase):
    """An advantage changes a timing; it never removes the warning."""

    def _assert_floored(self, world, expected, floor, where):
        """The window is the reference figure, and never below the floor."""
        self.assertEqual(
            world.record.ticks_remaining, expected,
            f"{where}: the window is {world.record.ticks_remaining}, not "
            f"max(floor, base - reduction) = {expected}",
        )
        self.assertGreaterEqual(
            world.record.ticks_remaining, floor,
            f"{where}: the window fell below the configured floor",
        )

    def _cycle(self, world, where):
        """Suspend and resume once, asserting both transitions happened."""
        self.assertTrue(
            world.vector.suspend(world.record, SUSPEND_CARRIER_UNAVAILABLE),
            f"{where}: the operation could not be suspended",
        )
        self.assertTrue(
            world.vector.resume(world.record),
            f"{where}: the operation could not be resumed",
        )

    def _place_windowed(self, base, reduction, floor, route, npc):
        """Place one hostile operation whose window *route* reduced."""
        world = _staged(
            registry=_Registry(floor=floor),
            vector_cls=_DelegatingVector if route == "service" else _LifecycleVector,
        )
        if npc:
            # R12.6's marker, and R11.6's subject: the same gates, the same
            # notifications, and the same Response_Window as a player's.
            world.player.db.is_sentinel = True
        if route == "service":
            world.vector.reduction = reduction
            return _place(world, ticks=base, hostile=True)
        return _place(world, ticks=base - reduction, hostile=True)

    @given(
        base=st.integers(min_value=0, max_value=500),
        reduction=st.integers(min_value=-1000, max_value=1000),
        floor=st.integers(min_value=1, max_value=50),
        route=st.sampled_from(("vector", "service")),
    )
    @settings(max_examples=100)
    def test_the_window_never_falls_below_the_floor(self, base, reduction, floor, route):
        """**Validates: Requirements 8.8, 9.4, 11.6**"""
        expected = max(floor, base - reduction)
        placed = {}
        for owner_kind in ("player", "npc"):
            where = f"{owner_kind}-owned, reduced by the {route}"
            world = self._place_windowed(base, reduction, floor, route, owner_kind == "npc")
            placed[owner_kind] = world

            # -- 1. On entering Pending (R8.8, R9.4) ------------------------- #
            self._assert_floored(world, expected, floor, f"{where}: on entry")
            self.assertTrue(
                world.branch.called("response_window"),
                f"{where}: the window was not read through the shared service",
            )
            # The warning the target was given quotes the floored clock, because
            # R8.8 measures the window from this notification to the effect.
            warnings = [
                entry["data"] for entry in world.bus.notifications()
                if entry["kind"] == NOTIFY_VECTOR_INCOMING
            ]
            self.assertEqual(
                [warning["ticks"] for warning in warnings], [expected],
                f"{where}: the target was warned of a window it does not have",
            )

            # -- 2. And after every resume ----------------------------------- #
            for cycle in (1, 2):
                self._cycle(world, f"{where}: resume {cycle}")
                self._assert_floored(world, expected, floor, f"{where}: resume {cycle}")

            # -- 3. A window that has RUN BELOW the floor gets it back ------- #
            # Written down rather than advanced down: the arithmetic of the
            # advance is Property 16's claim, and only the floor is this one's.
            world.record.ticks_remaining = below = max(1, floor - 1)
            self._cycle(world, f"{where}: below the floor")
            self._assert_floored(
                world, max(floor, below), floor, f"{where}: below the floor"
            )

        # -- 4. R11.6: identical window, identical notification set ---------- #
        self.assertEqual(
            placed["player"].record.ticks_remaining,
            placed["npc"].record.ticks_remaining,
            "an NPC base's operation gave its target a different window",
        )
        self.assertEqual(
            _notification_log(placed["player"]), _notification_log(placed["npc"]),
            "an NPC base's operation notified a different set of players",
        )
        self.assertFalse(
            placed["npc"].branch.called("charge"),
            "an NPC base's operation must still cost nothing (R12.6)",
        )


# ------------------------------------------------------------------ #
#  Shared fixtures for the services half (Properties 11, 19, 20, 25)
# ------------------------------------------------------------------ #
#
# The two halves above hold a *service* steady and measure the driver. These
# four turn that around: the subject is the service, so :class:`_BranchServices`
# is the wrong instrument for three of them — it overrides ``eligible_carrier``,
# ``cooldown_remaining``, ``in_flight_count``, ``in_flight_cap``, and
# ``may_target``, which are precisely the answers Properties 11, 19, and 20 are
# about. Those three run against a **plain, real** ``BranchSystem``
# (:func:`_real_system`), exactly as Property 24 already constructs one to make
# its claim about the shipped services rather than about a double of them.
#
# Where a property's claim reaches *through* the driver — "no Vector_Operation
# reaches the Pending state without a carrier" (R7.1), "a request is refused"
# (R8.19, R8.20) — the double is used as a **relay**: the real system computes
# the answer, and that answer is handed to :class:`_BranchServices` as its
# configured one. The eligibility, the cooldown figure, and the count are then
# still the shipped computations, and the double only carries them to the check
# that consumes them.
#
# So the additions here are the collaborators a real ``BranchSystem`` takes by
# injection — a clock, a roster, an alliance — plus a room and an entity for the
# area audience, which nothing above provides because ``_staged`` deliberately
# leaves ``location`` unset.

#: Operation_Kind -> the Carrier_Agent role it requires (R7.2, R7.4). Derived
#: from the shipped constants rather than spelled out, so a role retargeted in
#: ``constants.py`` reaches this module instead of being restated against it.
KIND_CARRIER_ROLE: dict[str, str] = {
    BRANCH_OPERATION_KIND[branch]: BRANCH_ROLE[branch] for branch in BRANCHES
}

#: The alliance the originating player belongs to in the fixtures that need one,
#: and a second one a *pact* ally belongs to. Two ids rather than one, because
#: "alliance member" and "ally" are different shapes of the same relationship and
#: R10.7 is a claim about both.
ALLIANCE_ID = 4242
RIVAL_ALLIANCE_ID = 7373


class _Clock:
    """The injected tick source, driven by the test rather than by a tick script.

    Every ledger in ``BranchSystem`` reads the tick through the **injected**
    callable and never through a module-level call (R15.1), which is the seam
    that lets a property place an entry at one tick and read it at another
    without waiting for anything.
    """

    def __init__(self, tick: int = 0):
        self.tick = int(tick)

    def __call__(self) -> int:
        return self.tick


class _Agent:
    """A Carrier_Agent candidate carrying exactly the four eligibility flags.

    Aliveness is expressed as ``hp`` rather than as an ``is_alive()`` method on
    purpose: the shipped predicate prefers the method and falls back to the
    attribute, and the attribute is the half a minimal object — an agent read
    back out of a database, a fake in a test — actually has. Every flag is
    reached by the same duck-typed name every other consumer reads it by, so an
    agent that is benched for its behaviour script is benched for a
    Vector_Operation too.
    """

    _next_id = 800

    def __init__(
        self, owner=None, role=ROLE, planet=PLANET, alive=True,
        in_reserve=False, incapacitated=False,
    ):
        _Agent._next_id += 1
        self.id = _Agent._next_id
        self.key = f"Agent #{self.id}"
        self.attributes = FakeAttributes({
            "owner": owner,
            "npc_type": "agent",
            "role": role,
            "coord_planet": planet,
            "hp": 100 if alive else 0,
            "reserve": in_reserve,
            "incapacitated": incapacitated,
        })
        self.db = FakeDB(self.attributes)


class _Roster:
    """The AgentSystem stand-in ``eligible_carrier`` reads a roster through.

    One method, because one method is the whole of what the shipped lookup asks
    for: ``get_agents(player)``. Ownership is by identity, so an agent belongs to
    the player it was built for and to nobody else, and the scan order is the
    list's own — which is what makes "the first eligible agent, not an arbitrary
    one" a claim a test can make.
    """

    def __init__(self, agents=()):
        self.agents = list(agents)
        self.asked: list = []

    def get_agents(self, player):
        self.asked.append(player)
        return [agent for agent in self.agents if agent.db.owner is player]


class _Alliance:
    """An AllianceSystem stand-in: the predicate, and the summary a refusal quotes.

    The two reads the targeting gates make of the injected collaborator. Injected
    rather than left to the ``world.utils.are_allied`` fallback deliberately: that
    fallback requires a *real* player character (it checks ``combat_xp``, the NPC
    markers, and a live alliance record), so a fake that relied on it would be
    testing the fallback's structural player test rather than the gate.
    """

    def __init__(self, allied=False, name=ALLIANCE_NAME, tag="ICD"):
        self.allied = bool(allied)
        self.name = name
        self.tag = tag
        self.asked: list[tuple] = []

    def are_allied(self, first, second):
        self.asked.append((first, second))
        return self.allied and first is not second

    def alliance_summary(self, alliance_id, **_kwargs):
        return {"name": self.name, "tag": self.tag}


def _balance(**fields) -> BalanceConfig:
    """Return a ``BalanceConfig`` with *fields* overridden and the rest shipped.

    The **real** dataclass, not a namespace, for one reason: every field name a
    property tunes is checked at construction, so a knob renamed in
    ``definitions.py`` fails here as a ``TypeError`` rather than as a silently
    ignored keyword that leaves the shipped default in place and the property
    passing against the wrong number.
    """
    return BalanceConfig(**fields)


def _real_system(balance=None, clock=None, agents=None, alliance=None):
    """Return a PLAIN ``BranchSystem`` over the fixture catalog (R15.1, R15.4).

    No overrides and no double: the subject of Properties 11, 19, and 20 is the
    shipped service, and :class:`_BranchServices` overrides every one of them.
    Every collaborator arrives by injection and each is optional, so a property
    wires exactly the ones its service reads and leaves the rest absent — which
    is also the degraded shape R15.2 requires to answer rather than raise.

    Args:
        balance: The :class:`BalanceConfig` the knobs are read from, or ``None``
            for the shipped defaults.
        clock: The tick source (:class:`_Clock`), or ``None`` for the constant
            zero the constructor falls back to.
        agents: The AgentSystem stand-in (:class:`_Roster`) the carrier lookup
            reads its roster through.
        alliance: The AllianceSystem stand-in (:class:`_Alliance`) the targeting
            gates ask.
    """
    return BranchSystem(
        fixture_registry(balance=balance),
        _Bus(),
        current_tick_func=clock,
        agent_system=agents,
        alliance_system=alliance,
    )


def _ref_id(ref):
    """Return the player id *ref* spells, or ``None``.

    The reference computation's half of ``BranchSystem._owner_matches``: a
    persisted ``owner_ref`` is a **value**, so ``record_st`` draws it as an
    integer id, as the ``#dbref`` string spelling the same id, or as ``None``.
    All three must resolve to the same owner, which is exactly what an in-flight
    count restricted to one player has to get right.

    ``bool`` is excluded even though it is an ``int``: ``True`` is not a database
    id, and counting it as one would be the reference agreeing with a bug.
    """
    if isinstance(ref, bool) or ref is None:
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        spelled = ref.strip().lstrip("#")
        return int(spelled) if spelled.isdigit() else None
    return None


def _ids(entities):
    """Return the identity set of *entities*, the way the driver de-duplicates.

    ``.id`` where there is one and the object's own identity otherwise, matching
    ``OperationDriver._identity_key`` — so "the same audience" means the same
    *players*, not the same list of object references.
    """
    return frozenset(
        getattr(entity, "id", None) if getattr(entity, "id", None) is not None
        else id(entity)
        for entity in entities
    )



# ================================================================== #
#  Property 11
# ================================================================== #
#
# R7.5 names four conditions and R7.1 says no Vector_Operation resolves without
# an agent satisfying them. "Conjunction" is the whole claim, and it has two
# halves that a weaker test would let stand in for one another:
#
# * **iff, not if.** All sixteen truth combinations of the four flags are
#   reachable (``agent_state_st`` draws each independently), so a lookup that
#   ignored one conjunct — a benched agent still answering, a corpse still
#   answering — fails on the combination that isolates it.
# * **and each conjunct is separately necessary.** For every example where the
#   agent *is* eligible, the same agent is rebuilt four times with exactly one
#   condition broken, and each of the four must answer ``None``. That is a
#   sharper claim than the iff alone: it names which conjunct is missing.
#
# The role conjunct is swept rather than sampled. The drawn Operation_Kind is
# what the driver clause uses, but every one of the six kinds' required roles is
# asked of the same roster in the same example — so a drawn role that is a gated
# Branch role matches exactly one kind and mismatches the other five, and both
# directions of "assigned to the role the Operation_Kind requires" get exercised
# on every example instead of on the one in nine where a sampled kind happens to
# agree with a sampled role.
#
# Two dimensions beyond the four flags, because both are documented behaviour of
# the same lookup and neither is a fifth conjunct:
#
# * **the roster is scanned in order, past the ineligible.** A drawn ``decoy``
#   puts a dead agent in the required role *ahead* of the drawn one, and the
#   answer must not change: eligibility is a filter, not a first-match-and-stop.
# * **the scan is planet-scoped.** The same agent standing on another planet is
#   eligible for nothing, so a per-planet operation cannot borrow a body from
#   somewhere else.
#
# R7.1's half runs through the driver, with the double **relaying** the real
# system's verdict (see the fixture note above): the shipped conjunction decides,
# and the claim measured is that a request with no eligible carrier is refused at
# ``carrier`` and places nothing.

#: The four conditions R7.5 names, each with the single change that breaks it.
#: Written as breakages rather than as flags so a failure message says which
#: condition stopped being necessary.
CARRIER_BREAKAGES: dict[str, dict] = {
    "alive": {"alive": False},
    "assigned to the role": {"role": None},
    "active outside reserve": {"in_reserve": True},
    "free of incapacitation": {"incapacitated": True},
}


def _eligible_by_reference(state, role):
    """Return whether *state* satisfies R7.5's four conditions for *role*.

    The reference computation, written from the requirement: eligibility is the
    conjunction, so this is one ``and`` of four independent reads and nothing
    else — no ordering, no precedence, and no early answer.
    """
    return bool(
        state.alive
        and state.role is not None
        and state.role.lower() == role.lower()
        and not state.in_reserve
        and not state.incapacitated
    )


def _carrier_world(state, role=ROLE, planet=PLANET, decoy=False):
    """Return ``(system, player, agent)`` for one drawn agent state.

    The system is a **plain** ``BranchSystem`` (:func:`_real_system`), so the
    conjunction under test is the shipped one.
    """
    player = FakePlayer(key=ATTACKER_NAME, planet=PLANET)
    agent = _Agent(
        owner=player,
        role=state.role,
        planet=planet,
        alive=state.alive,
        in_reserve=state.in_reserve,
        incapacitated=state.incapacitated,
    )
    roster = [agent]
    if decoy:
        # Ahead of the drawn agent, in the same role, and dead: the scan must
        # step over it rather than stop at it.
        roster.insert(0, _Agent(owner=player, role=state.role, planet=planet, alive=False))
    return _real_system(agents=_Roster(roster)), player, agent


# Feature: tech-tree-branch-foundation, Property 11: Carrier eligibility is the
# conjunction of the four conditions
#
# **Validates: Requirements 7.1, 7.5**
class TestProperty11CarrierEligibility(unittest.TestCase):
    """Four conditions, all of them necessary, and no operation without a body."""

    def _assert_conjunction(self, state, decoy):
        """Every kind's required role, against one roster. R7.5."""
        system, player, agent = _carrier_world(state, decoy=decoy)
        for kind in OPERATION_KINDS:
            role = KIND_CARRIER_ROLE[kind]
            expected = _eligible_by_reference(state, role)
            answer = system.eligible_carrier(player, role, PLANET)
            self.assertEqual(
                answer is agent, expected,
                f"{kind} requires {role!r}: an agent {state} was answered "
                f"{answer!r}, and the conjunction says {expected}",
            )
            if not expected:
                self.assertIsNone(
                    answer, f"{kind}: an ineligible roster must answer None (R7.1)"
                )

    def _assert_each_condition_is_necessary(self, state, role):
        """Break exactly one conjunct at a time; each break must refuse. R7.5."""
        for label, override in CARRIER_BREAKAGES.items():
            spoiled = dict(
                role=state.role,
                alive=state.alive,
                in_reserve=state.in_reserve,
                incapacitated=state.incapacitated,
            )
            spoiled.update(override)
            player = FakePlayer(key=ATTACKER_NAME, planet=PLANET)
            agent = _Agent(owner=player, planet=PLANET, **spoiled)
            system = _real_system(agents=_Roster([agent]))
            self.assertIsNone(
                system.eligible_carrier(player, role, PLANET),
                f"an agent that is not {label} is not an eligible Carrier_Agent",
            )

    @given(
        state=agent_state_st,
        kind=st.sampled_from(OPERATION_KINDS),
        decoy=st.booleans(),
    )
    @settings(max_examples=100)
    def test_carrier_eligibility_is_the_conjunction_of_four_conditions(
        self, state, kind, decoy
    ):
        """**Validates: Requirements 7.1, 7.5**"""
        # -- 0. The role vocabulary is the shipped bijection ----------------- #
        self.assertEqual(
            frozenset(KIND_CARRIER_ROLE), frozenset(OPERATION_KINDS),
            "an Operation_Kind names no Carrier_Agent role (R7.2)",
        )

        # -- 1. R7.5: the conjunction, over every kind's required role -------- #
        self._assert_conjunction(state, decoy)

        # -- 2. R7.5: and each of the four conditions is separately necessary - #
        role = KIND_CARRIER_ROLE[kind]
        if _eligible_by_reference(state, role):
            self._assert_each_condition_is_necessary(state, role)
            # The stored role is compared case-insensitively, so the role table's
            # vocabulary and a hand-set attribute agree about the same agent.
            shouting = state._replace(role=state.role.upper())
            system, player, agent = _carrier_world(shouting)
            self.assertIs(
                system.eligible_carrier(player, role, PLANET), agent,
                "the role comparison must not depend on the stored casing",
            )

        # -- 3. The scan is planet-scoped ------------------------------------ #
        # An agent elsewhere is a body somewhere else, so it carries nothing
        # here — the same per-planet scoping every other answer in this feature
        # has.
        away, away_player, _agent = _carrier_world(state, planet=OTHER_PLANET)
        self.assertIsNone(
            away.eligible_carrier(away_player, role, PLANET),
            "an agent on another planet is no Carrier_Agent for this one",
        )

        # -- 4. R7.1: no Vector_Operation reaches Pending without one --------- #
        # The verdict is the SHIPPED one, relayed into the driver's carrier
        # service, so what is measured here is the driver honouring it.
        system, player, agent = _carrier_world(state, decoy=decoy)
        verdict = system.eligible_carrier(player, ROLE, PLANET)
        self.assertEqual(verdict is agent, _eligible_by_reference(state, ROLE))
        world = _world(
            resources={"Iron": 500, "Circuits": 500}, carrier=verdict
        )
        outcome = _send(world)
        self.assertEqual(
            outcome.ok, verdict is not None,
            "an operation reached Pending on the strength of no eligible carrier",
        )
        if verdict is None:
            self.assertEqual(outcome.check, "carrier")
            self.assertEqual(outcome.detail["message"], MSG_VECTOR_CARRIER_REQUIRED)
            for key, value in REQUIRED_VALUE["carrier"].items():
                self.assertEqual(
                    outcome.detail[key], value,
                    f"the carrier refusal must report {key!r} (R7.3)",
                )
            self.assertEqual(world.vector.tracked_records(), [])
            self.assertEqual(_read_records(world.vector.owner), [])
        else:
            self.assertEqual(outcome.state, str(OperationState.PENDING))
            self.assertIs(
                world.vector.tracked_records()[0].carrier_ref, agent,
                "the placed record must name the carrier the check found",
            )


# ================================================================== #
#  Property 18
# ================================================================== #
#
# R9.4 bounds an advantage and R9.5 forbids compounding, and the second is the
# one a test has to work at, because the clamp hides it: with an edge worth the
# cap, a product of two edges is ``cap * cap``, which the ceiling flattens back
# to ``cap``. A property that only ever declared edges as bare Branch names would
# therefore pass over an implementation that multiplied.
#
# So the graph is drawn in **both** shapes the loader and this lookup accept:
#
# * a **list** of target Branch names — every edge the shipped ``branches.yaml``
#   expresses, each worth the cap; and
# * a **mapping** of target Branch to a declared magnitude, which is the seam a
#   per-pair value arrives through. With a magnitude strictly between ``1.0`` and
#   the cap, a product of two is *visible*: ``1.1`` and ``1.21`` are different
#   answers, and only one of them is a single lookup.
#
# Three more shapes are asserted in the same test because each is a way a graph
# could smuggle a second multiplication in:
#
# * **duplicates** — the same target named three times must be worth one edge;
# * **self-edges** — a Branch naming itself must still be one capped value;
# * **paths** — ``actor -> mid -> target`` with no direct edge must be exactly
#   ``1.0``, which is the whole of "advantages do not compound" stated as a
#   graph question rather than as an arithmetic one. The shipped six-Branch cycle
#   is swept pair by pair for the same reason: thirty of its thirty-six pairs are
#   reachable only by walking two or more edges, and every one of them must be
#   neutral.


def _counter_reference(web, actor, target, cap):
    """Return the multiplier R9.4 and R9.5 declare for one pair.

    One lookup and one clamp, written from the requirement: the web either names
    an edge from *actor* to *target* or it does not, and the answer is the
    declared magnitude of that one edge — the cap for an edge declared as a bare
    Branch name — clamped into ``[1.0, cap]``. There is no path walk here because
    there is no path walk in the contract.
    """
    edges = web.get(actor) if hasattr(web, "get") else None
    if edges is None or isinstance(edges, str) or not hasattr(edges, "__contains__"):
        return 1.0
    if target not in edges:
        return 1.0
    declared = cap
    getter = getattr(edges, "get", None)
    if callable(getter):
        try:
            declared = float(getter(target))
        except (OverflowError, TypeError, ValueError):
            declared = cap
    return max(1.0, min(cap, declared))


def _counter_system(web, cap, normalize=True):
    """Return a plain ``BranchSystem`` over *web* with *cap* configured.

    Args:
        web: The Counter_Web graph.
        cap: ``counter_advantage_cap``.
        normalize: ``True`` routes the graph through the loader's own
            normalization (``{branch: (branch, ...)}``), which is the shape a
            ``branches.yaml`` edge list loads as. ``False`` assigns the graph
            **as given**, which is the only way a per-edge *mapping* survives:
            the loader coerces every edge container to a tuple, so a mapping
            passed through it would arrive as a tuple of its own keys and the
            declared magnitudes would be lost.
    """
    balance = _balance(counter_advantage_cap=cap)
    if normalize:
        return BranchSystem(make_registry(counter_web=web, balance=balance), _Bus())
    registry = make_registry(balance=balance)
    registry.counter_web = web
    return BranchSystem(registry, _Bus())


# Feature: tech-tree-branch-foundation, Property 18: A Counter_Web advantage is
# bounded and never compounds
#
# **Validates: Requirements 9.4, 9.5**
class TestProperty18CounterAdvantageBounded(unittest.TestCase):
    """One edge, one clamp — a change of magnitude, never immunity."""

    def _assert_bounded(self, system, actor, target, expected, cap, where):
        """The answer is the reference figure, and inside ``[1.0, cap]`` (R9.4)."""
        answer = system.counter_multiplier(actor, target)
        self.assertEqual(
            answer, expected,
            f"{where}: {actor!r} over {target!r} resolved {answer!r}, not {expected!r}",
        )
        self.assertGreaterEqual(
            answer, 1.0, f"{where}: an advantage became a penalty"
        )
        self.assertLessEqual(
            answer, max(1.0, cap),
            f"{where}: an advantage of {answer!r} is beyond the configured cap",
        )
        return answer

    @given(
        web=counter_web_st,
        actor=branch_st,
        target=branch_st,
        cap=st.floats(
            min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False
        ),
        magnitude=st.floats(
            min_value=0.5, max_value=4.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_a_counter_advantage_is_bounded_and_never_compounds(
        self, web, actor, target, cap, magnitude
    ):
        """**Validates: Requirements 9.4, 9.5**"""
        # -- 1. The drawn graph: bounded, and exactly 1.0 with no edge -------- #
        loaded = {key: tuple(values) for key, values in web.items()}
        system = _counter_system(web, cap)
        expected = _counter_reference(loaded, actor, target, cap)
        answer = self._assert_bounded(
            system, actor, target, expected, cap, "the drawn graph"
        )
        named = target in loaded.get(actor, ())
        self.assertEqual(
            answer == 1.0, not named or cap == 1.0,
            "the multiplier is exactly 1.0 if and only if the web names no edge",
        )

        # -- 2. Duplicates and a self-edge are still ONE capped value (R9.5) -- #
        crowded = {**web, actor: [target] * 3 + [actor]}
        crowded_system = _counter_system(crowded, cap)
        self._assert_bounded(
            crowded_system, actor, target, cap, cap, "three duplicate edges"
        )
        self._assert_bounded(
            crowded_system, actor, actor, cap, cap, "a self-edge"
        )

        # -- 3. A declared magnitude below the cap: a product would SHOW ------ #
        # The clamp hides ``cap * cap``; it does not hide ``m * m`` for an ``m``
        # the ceiling still admits. This is the clause that makes R9.5 falsifiable.
        graded = {actor: {target: magnitude, "bio": magnitude}}
        expected_graded = max(1.0, min(cap, magnitude))
        self.assertEqual(
            _counter_reference(graded, actor, target, cap), expected_graded,
            "the reference computation disagrees with itself about one edge",
        )
        self._assert_bounded(
            _counter_system(graded, cap, normalize=False), actor, target,
            expected_graded, cap, "a declared magnitude",
        )

        # -- 4. A path is not an edge: two hops multiply to nothing (R9.5) ---- #
        mid = next(
            branch for branch in BRANCHES if branch not in (actor, target)
        )
        chained = {actor: [mid], mid: [target]}
        self.assertEqual(
            _counter_system(chained, cap).counter_multiplier(actor, target), 1.0,
            "a two-hop path granted an advantage, so advantages compound",
        )

        # -- 5. The shipped cycle, pair by pair ------------------------------ #
        # One advantage per Branch, so thirty of the thirty-six pairs are
        # reachable only by walking the cycle — and every one of them is neutral.
        shipped = _counter_system(CANONICAL_COUNTER_WEB, cap)
        for first in BRANCHES:
            for second in BRANCHES:
                edge = second in CANONICAL_COUNTER_WEB[first]
                self._assert_bounded(
                    shipped, first, second, cap if edge else 1.0, cap,
                    "the shipped cycle",
                )

        # -- 6. A cap nobody can read leaves every edge neutral (R9.4) ------- #
        # The clamp's lower bound of 1.0 is what makes an unreadable knob inert
        # rather than a penalty, which is the direction a balance knob must fail.
        blind = BranchSystem(
            make_registry(counter_web={actor: [target]}, balance=SimpleNamespace()),
            _Bus(),
        )
        self.assertEqual(blind.counter_multiplier(actor, target), 1.0)


# ================================================================== #
#  Property 19
# ================================================================== #
#
# Two ledgers, one property, because R8.19 and R8.20 make the same shape of
# claim: a limit is a *computation over stored state*, and a refusal is that
# computation **reported**. So each half is asserted twice — once against a
# reference computation of the figure, and once against the refusal that carries
# it — and a driver that refused correctly while reporting a different number
# fails the second even though it passed the first.
#
# **The cooldown half.** ``note_cooldown`` at tick ``T`` and a read at ``T + E``
# must answer ``max(0, L - E)``, and a request must be refused exactly while that
# is positive. The drawn elapsed count comes from ``tick_st``, which reaches far
# past any configured length; the boundary values ``L - 1``, ``L``, and ``L + 1``
# are swept alongside it in the same example, because the whole of "strictly less
# than" lives in those three ticks and a pool that spans a hundred thousand of
# them would visit the interesting ones rarely.
#
# Two behaviours of the same ledger are asserted beside the arithmetic, both
# because R8.19 says *per originating building per Operation_Kind* and a shared
# tally would satisfy the arithmetic while breaking the scope:
#
# * a second building that has run nothing is ready, and
# * a second kind on the *same* building is ready.
#
# And one deliberate departure from ``ready_at - now``: the answer is **clamped
# to the configured length**, so a stored ``ready_at`` far in the future reads as
# one cooldown rather than as a lockout of hundreds of ticks. That is a
# self-healing ledger rather than an approximation, so it is asserted as the
# documented reading rather than left as a gap the reference works around.
#
# **The in-flight half.** The count is a *scan*, not a tally — the vector's own
# non-terminal records are the count — so the reference is that scan written from
# R8.20's four restrictions: this player, this kind, this planet, non-terminal.
# The owner id is picked out of the drawn records themselves rather than fixed,
# so a drawn list reliably contains records that match and records that do not,
# and the three reference shapes a persisted ``owner_ref`` takes (an id, a
# ``#dbref``, ``None``) all have to resolve to the same owner.
#
# A cap below ``1`` is **unbounded**, which is R8.20's reading of an unconfigured
# knob and the one case where a large count must still be accepted.

#: The cooldown boundary R8.19's "strictly less than" turns on, as offsets from
#: the configured length. Swept in every example alongside the drawn elapsed
#: count, so the three ticks that decide the comparison are always visited.
COOLDOWN_BOUNDARY_OFFSETS: tuple[int, ...] = (-1, 0, 1)


def _reference_cooldown(length, elapsed, stored_ahead=None):
    """Return the ticks R8.19 says remain, from the requirement's own arithmetic.

    ``length - elapsed`` while that is positive and ``0`` afterwards — and never
    more than the configured length, which is the ledger's self-healing clamp for
    a ``ready_at`` written against a clock that could not be read.

    Args:
        length: The configured cooldown length.
        elapsed: Ticks since the cooldown was noted.
        stored_ahead: The stored ``ready_at`` as an offset from *now*, for the
            hand-edited case. ``None`` means "noted normally", where the offset
            is ``length - elapsed``.
    """
    ahead = (length - elapsed) if stored_ahead is None else stored_ahead
    return max(0, min(ahead, max(0, length)))


def _reference_in_flight(records, player_id, kind, planet):
    """Return R8.20's own scan of *records*: this player, kind, planet, non-terminal.

    Written from the requirement rather than from the service. Two conventions
    the requirement leaves to the framework are spelled out here because the
    service documents them: a record whose planet cannot be read counts on every
    planet (the estate scan's own rule), and a record whose state cannot be read
    counts, because a tracked record is in flight until it says otherwise.
    """
    count = 0
    for record in records:
        record_kind = record.kind
        if record_kind is not None and record_kind != kind:
            continue
        if str(record.state) in TERMINAL_STATE_VALUES:
            continue
        if _ref_id(record.owner_ref) != player_id:
            continue
        if planet is not None and record.planet not in (None, planet):
            continue
        count += 1
    return count


class _CountedVector:
    """A Vector_System stand-in whose tracked records are the in-flight count.

    Exactly the two things the count reaches for, duck-typed: the Operation_Kind
    that keys the registration, and the public ``tracked_records`` accessor it
    prefers over the driver's private list.
    """

    def __init__(self, kind, records=()):
        self.operation_kind = kind
        self._records = list(records)

    def tracked_records(self):
        return list(self._records)


# Feature: tech-tree-branch-foundation, Property 19: Cooldown and in-flight
# counts equal their reference computations, and refusals report them
#
# **Validates: Requirements 8.19, 8.20**
class TestProperty19LedgersEqualTheirReference(unittest.TestCase):
    """A limit is a computation, and a refusal is that computation reported."""

    def _assert_cooldown_refusal(self, remaining, length, elapsed, where):
        """The driver refuses exactly while the ledger says wait. R8.19."""
        world = _world(
            resources={"Iron": 500, "Circuits": 500}, cooldown=remaining
        )
        outcome = _send(world)
        refused = not outcome.ok and outcome.check == "cooldown"
        self.assertEqual(
            refused, elapsed < length,
            f"{where}: refused={refused} with {elapsed} of {length} ticks elapsed",
        )
        if refused:
            self.assertEqual(outcome.detail["message"], MSG_VECTOR_COOLDOWN)
            self.assertEqual(
                outcome.detail["remaining_ticks"], length - elapsed,
                f"{where}: the refusal must report exactly length - elapsed",
            )
            self.assertEqual(outcome.detail["building"], ORIGIN_ABBR)
            self.assertEqual(world.vector.tracked_records(), [])

    def _assert_in_flight_refusal(self, count, cap, where):
        """The driver refuses exactly while the count has reached the cap. R8.20."""
        world = _world(
            resources={"Iron": 500, "Circuits": 500}, count=count, cap=cap
        )
        outcome = _send(world)
        refused = not outcome.ok and outcome.check == "in_flight"
        self.assertEqual(
            refused, cap >= 1 and count >= cap,
            f"{where}: refused={refused} with {count} in flight against a cap of {cap}",
        )
        if refused:
            self.assertEqual(outcome.detail["message"], MSG_VECTOR_IN_FLIGHT_CAP)
            self.assertEqual(outcome.detail["count"], count, f"{where}: the count")
            self.assertEqual(outcome.detail["cap"], cap, f"{where}: the cap")
            self.assertEqual(world.vector.tracked_records(), [])

    @given(
        placed_at=tick_st,
        elapsed=tick_st,
        length=st.integers(min_value=0, max_value=200),
        records=st.lists(record_st, max_size=8),
        cap=st.integers(min_value=1, max_value=10),
        kind=st.sampled_from(OPERATION_KINDS),
        planet=st.one_of(st.none(), st.sampled_from(FIXTURE_PLANETS)),
        owner_index=st.integers(min_value=0, max_value=7),
    )
    @settings(max_examples=100)
    def test_the_ledgers_equal_their_reference_and_refusals_report_them(
        self, placed_at, elapsed, length, records, cap, kind, planet, owner_index
    ):
        """**Validates: Requirements 8.19, 8.20**"""
        other_kind = next(name for name in OPERATION_KINDS if name != kind)

        # ---------------- R8.19: the cooldown ledger ---------------------- #
        clock = _Clock(placed_at)
        system = _real_system(
            clock=clock, balance=_balance(**{f"{kind}_cooldown_ticks": length})
        )
        building = FakeBuilding(building_type=ORIGIN_ABBR, planet=PLANET)
        idle = FakeBuilding(building_type=ORIGIN_ABBR, planet=PLANET)
        system.note_cooldown(building, kind)

        steps = sorted({elapsed} | {
            max(0, length + offset) for offset in COOLDOWN_BOUNDARY_OFFSETS
        })
        for step in steps:
            where = f"{step} of {length} ticks elapsed"
            clock.tick = placed_at + step
            remaining = system.cooldown_remaining(building, kind)

            # -- 1. The figure equals the reference computation -------------- #
            self.assertEqual(
                remaining, _reference_cooldown(length, step),
                f"{where}: the remaining ticks are not max(0, length - elapsed)",
            )
            # -- 2. R8.19's scope: per building, per Operation_Kind ---------- #
            self.assertEqual(
                system.cooldown_remaining(idle, kind), 0,
                f"{where}: a building that has run nothing is on cooldown",
            )
            self.assertEqual(
                system.cooldown_remaining(building, other_kind), 0,
                f"{where}: one kind's cooldown reached another kind",
            )

        # -- 3. And the refusal reports exactly that figure ----------------- #
        # Two of the swept steps drive the driver — one on each side of the
        # boundary — because the refusal is the same report whichever tick it is
        # made on, and a request per tick would only repeat it.
        for step in sorted({elapsed, max(0, length - 1), length}):
            clock.tick = placed_at + step
            self._assert_cooldown_refusal(
                system.cooldown_remaining(building, kind), length, step,
                f"cooldown at +{step}",
            )

        # -- 4. The self-healing clamp, as documented behaviour ------------- #
        # A ``ready_at`` far in the future — what a clock that could not be read
        # leaves behind — reads as one cooldown, never as a longer lockout.
        clock.tick = placed_at
        setattr(
            building.db, ATTR_VECTOR_COOLDOWNS,
            {kind: placed_at + length + 10_000},
        )
        self.assertEqual(
            system.cooldown_remaining(building, kind),
            _reference_cooldown(length, 0, stored_ahead=length + 10_000),
            "a far-future ready_at must clamp to the configured length",
        )

        # ---------------- R8.20: the in-flight count ---------------------- #
        owners = sorted({
            ref for ref in (_ref_id(record.owner_ref) for record in records)
            if ref is not None
        })
        # An owner drawn FROM the records, so a drawn list reliably holds both
        # matching and non-matching ones; a list naming no resolvable owner falls
        # back to an id nothing can match, which is the empty-count case.
        player_id = owners[owner_index % len(owners)] if owners else 9_001
        player = FakePlayer(key=ATTACKER_NAME, planet=PLANET, player_id=player_id)
        counter = _real_system(
            clock=_Clock(0), balance=_balance(**{f"{kind}_max_in_flight": cap})
        )
        counter.register_vector(_CountedVector(kind, records))

        scope = planet if planet is not None else PLANET
        expected_count = _reference_in_flight(records, player_id, kind, scope)

        # -- 5. The count equals the reference scan -------------------------- #
        self.assertEqual(
            counter.in_flight_count(player, kind, planet), expected_count,
            "the in-flight count is not the reference scan over the tracked records",
        )
        self.assertEqual(counter.in_flight_cap(kind), cap)
        # A kind no vector owns has nothing in flight, whatever else is tracked.
        self.assertEqual(counter.in_flight_count(player, other_kind, planet), 0)
        # And the count is scoped to ONE player: a stranger holds none of these.
        stranger = FakePlayer(key="Rival", planet=PLANET, player_id=-player_id)
        self.assertEqual(counter.in_flight_count(stranger, kind, planet), 0)

        # -- 6. And the refusal reports both figures ------------------------- #
        self._assert_in_flight_refusal(expected_count, cap, "the drawn count")
        # R8.20's reading of an unconfigured knob: below 1 is UNBOUNDED, so even
        # a count above every drawn cap is accepted.
        self._assert_in_flight_refusal(expected_count + cap, 0, "no cap configured")


# ================================================================== #
#  Property 20
# ================================================================== #
#
# Two gates and one claim about both: neither of them can be softened by who the
# two players are to one another (R10.7).
#
# **What "identical regardless of the relationship" means here, precisely.**
# R11.9 puts a *third* gate between the two this property is about: a hostile
# operation naming an allied entity is refused, and refused for that reason. So
# an allied pair under the shield level and under the escalation cap is still
# refused — by R11.9, not by either gate here — and the literal reading "the
# outcome is identical" would be a claim against R11.9 rather than about R10.7.
# R10.7's own words are that every gate applies to members and allies *on the
# same terms*, which is what this test asserts, in three parts:
#
# 1. **The shield is identical, payload and all.** It is evaluated FIRST, ahead of
#    the allied refusal, so a shielded target answers with the qualifying level
#    whatever the relationship — the whole refusal, compared field by field
#    across unaffiliated, member, and ally.
# 2. **The escalation ledger is identical.** ``escalation_remaining`` is keyed on
#    target identity and nothing else, so the count, the cap, the window, and the
#    remaining ticks are the same three times over: alliance membership grants no
#    exemption from the escalation limit.
# 3. **And an alliance is never a softening.** A refusal in the unaffiliated case
#    is a refusal in both allied cases too, so no relationship can turn a "no"
#    into a "yes". The iff — refused exactly when shielded or capped — is asserted
#    where R11.9 does not overlap it, which is the unaffiliated case.
#
# **The ledger's reference is a simulation, not a filter**, and deliberately: the
# window is pruned on **write** as well as on read, and a write prunes against
# the clock *at that moment*, so an entry dated in the future relative to a later
# write is dropped by it. The drawn tick sequence is unsorted, which is exactly
# the clock-went-backwards case the pruning exists for, so the reference replays
# the writes rather than filtering the drawn list.
#
# The relationship is **swept, not drawn**: "identical across the three" needs all
# three in one example, the same reason Property 17 places its operation twice
# rather than drawing an owner kind.

#: The three relationships R10.7 names. In this game an alliance member *is* an
#: ally — one pointer, one predicate — so the two allied shapes differ in the
#: stored alliance rather than in the rule: a ``member`` shares the actor's
#: alliance id, while an ``ally`` belongs to a second alliance in a pact with it.
TARGET_RELATIONSHIPS: tuple[str, ...] = ("none", "member", "ally")


def _escalation_reference(ticks, now, window, cap):
    """Return R10.6's own reading of one actor-target ledger.

    Replays the writes the way the ledger stores them — each ``note_escalation``
    prunes to the window against the clock it was called on, then appends — and
    then prunes once more for the read at *now*. Returns
    ``(count, remaining)``, where ``remaining`` is the ticks until the entry a
    freed slot is waiting on ages out, and ``0`` while the actor is under the cap.
    """
    stored: list[int] = []
    for tick in ticks:
        stored = sorted(
            [entry for entry in stored if 0 <= tick - entry < window] + [tick]
        )
    entries = [entry for entry in stored if 0 <= now - entry < window]
    if len(entries) < cap:
        return len(entries), 0
    waiting_on = entries[len(entries) - cap]
    return len(entries), max(0, waiting_on + window - now)


def _escalation_world(relationship, level, shield, cap, window):
    """Return ``(system, actor, target, clock)`` for one relationship."""
    clock = _Clock(0)
    system = _real_system(
        clock=clock,
        alliance=_Alliance(allied=relationship != "none"),
        balance=_balance(
            new_player_vector_shield_level=shield,
            escalation_cap=cap,
            escalation_window_ticks=window,
        ),
    )
    actor = FakePlayer(key=ATTACKER_NAME, planet=PLANET, player_id=101)
    target = FakePlayer(key=DEFENDER_NAME, planet=PLANET, level=level, player_id=202)
    if relationship == "member":
        actor.db.player_alliance = ALLIANCE_ID
        target.db.player_alliance = ALLIANCE_ID
    elif relationship == "ally":
        actor.db.player_alliance = ALLIANCE_ID
        target.db.player_alliance = RIVAL_ALLIANCE_ID
    return system, actor, target, clock


# Feature: tech-tree-branch-foundation, Property 20: The escalation cap and the
# new-player shield hold regardless of the alliance relationship
#
# **Validates: Requirements 10.4, 10.6, 10.7**
class TestProperty20ShieldAndEscalationHold(unittest.TestCase):
    """An alliance changes who you may hit; it never changes a protection gate."""

    def _assert_shielded(self, refusal, level, shield, where):
        """R10.4: refused, reporting the level at which the target becomes valid."""
        self.assertIsNotNone(refusal, f"{where}: a target below the shield level")
        self.assertEqual(
            str(refusal), MSG_VECTOR_TARGET_SHIELDED,
            f"{where}: refused {str(refusal)!r}, not the new-player shield",
        )
        self.assertEqual(refusal.data["required_level"], shield, f"{where}: the level")
        self.assertEqual(refusal.data["target_level"], level, f"{where}: the target")

    def _assert_escalation(self, refusal, count, cap, window, remaining, where):
        """R10.6: refused, reporting the ticks until a slot frees."""
        self.assertEqual(
            str(refusal), MSG_VECTOR_ESCALATION_LIMIT,
            f"{where}: refused {str(refusal)!r}, not the escalation cap",
        )
        self.assertEqual(refusal.data["remaining_ticks"], remaining, f"{where}: ticks")
        self.assertGreaterEqual(
            remaining, 1,
            f"{where}: an entry inside the window cannot have aged out already",
        )
        self.assertEqual(refusal.data["count"], count, f"{where}: the count")
        self.assertEqual(refusal.data["cap"], cap, f"{where}: the cap")
        self.assertEqual(refusal.data["window"], window, f"{where}: the window")

    @given(
        ticks=st.lists(tick_st, max_size=8),
        now=tick_st,
        window=st.integers(min_value=1, max_value=5_000),
        cap=st.integers(min_value=1, max_value=10),
        level=st.integers(min_value=1, max_value=MAX_LEVEL),
        shield=st.integers(min_value=1, max_value=MAX_LEVEL),
    )
    @settings(max_examples=100)
    def test_the_shield_and_the_cap_hold_whatever_the_alliance(
        self, ticks, now, window, cap, level, shield
    ):
        """**Validates: Requirements 10.4, 10.6, 10.7**"""
        count, remaining = _escalation_reference(ticks, now, window, cap)
        capped = remaining > 0
        shielded = level < shield
        answers: dict[str, tuple] = {}

        for relationship in TARGET_RELATIONSHIPS:
            where = f"{relationship}"
            system, actor, target, clock = _escalation_world(
                relationship, level, shield, cap, window
            )
            for tick in ticks:
                clock.tick = tick
                system.note_escalation(actor, target)
            clock.tick = now

            # -- 1. R10.6: the ledger equals its reference computation ------- #
            answered = system.escalation_remaining(actor, target)
            self.assertEqual(
                answered, remaining,
                f"{where}: the escalation ledger is not the reference replay",
            )
            # And it is the ATTACKER's ledger: the target stores nothing.
            self.assertIsNone(
                getattr(target.db, ATTR_VECTOR_ESCALATION),
                f"{where}: the escalation ledger was written on the target",
            )

            refusal = system.may_target(actor, target, hostile=True)
            answers[relationship] = (refusal, answered)

            # -- 2. Either gate firing refuses, whatever the relationship ---- #
            if shielded or capped:
                self.assertIsNotNone(
                    refusal,
                    f"{where}: shielded={shielded} capped={capped} was permitted",
                )

            # -- 3. R10.4 first, so a shielded target reads the same three ways #
            if shielded:
                self._assert_shielded(refusal, level, shield, where)
            elif relationship == "none":
                # The one case R11.9 does not overlap, so the iff can be stated:
                # refused exactly when the cap is reached, and nothing else.
                self.assertEqual(
                    refusal is not None, capped,
                    f"{where}: refused={refusal is not None}, capped={capped}",
                )
                if capped:
                    self._assert_escalation(
                        refusal, count, cap, window, remaining, where
                    )
            else:
                # R11.9's own gate, which R10.7 does not exempt an ally from: the
                # refusal still names the alliance protecting the target.
                self.assertEqual(str(refusal), MSG_VECTOR_TARGET_ALLIED, where)
                self.assertEqual(
                    refusal.data["alliance"],
                    ALLIANCE_ID if relationship == "member" else RIVAL_ALLIANCE_ID,
                    f"{where}: the refusal must name the protecting alliance",
                )

        # -- 4. R10.7: identical across unaffiliated, member, and ally ------- #
        self.assertEqual(
            {answers[name][1] for name in TARGET_RELATIONSHIPS}, {remaining},
            "the escalation ledger differed by alliance relationship",
        )
        if shielded:
            self.assertEqual(
                [(str(answers[name][0]), answers[name][0].data)
                 for name in TARGET_RELATIONSHIPS],
                [(MSG_VECTOR_TARGET_SHIELDED, answers["none"][0].data)] * 3,
                "a shielded target answered differently to an ally",
            )
        # And no relationship ever turns a refusal into a permission.
        if answers["none"][0] is not None:
            for name in ("member", "ally"):
                self.assertIsNotNone(
                    answers[name][0],
                    f"{name}: an alliance softened a gate that had refused",
                )


# ================================================================== #
#  Property 25
# ================================================================== #
#
# R11.10 is a claim about what an area effect does **not** do: it does not ask
# who owns what. So the property places entities around the resolution coordinate
# with owners drawn from the originating player, an ally of theirs, and an
# unaffiliated player, and asserts the affected set is *exactly* the entities
# inside the radius — the originator's own and their ally's included.
#
# The alliance is **real data**, not a label: both players carry the same
# ``player_alliance``, which is the pointer ``world.utils.are_allied`` and the
# targeting gates compare. And the last clause flips it, asserting the audience is
# unchanged — because the audience code asks no alliance question at all, and that
# absence is the requirement.
#
# **The room fake is deliberately generous.** For the Chebyshev metric the
# bounding box the driver asks for *is* the ball it then filters to, so a room
# that answered only the box would make the driver's own distance filter
# unobservable — the filter could be deleted and this property would still pass.
# So ``_Room.get_objects_in_area`` answers every object it holds, whatever bounds
# it was handed, and records those bounds: the exclusion of a far entity is then
# the driver's own arithmetic, and the box it asked for is asserted separately.
#
# Both halves of R8.12's audience are exercised, because the requirement's
# "each entity within the affected area" covers a player standing in the blast as
# much as a building sitting in it: the placements feed the area query, and the
# three players stand on fixed tiles at Chebyshev 0, 2, and 9 from the centre — so
# every drawn radius admits a different subset of them, and a player who both owns
# an affected entity and stands on an affected tile is the de-duplication case.

#: The coordinate every fixture request aims at (``_send``'s own ``x`` and ``y``),
#: and therefore the centre of the area this property measures.
CENTER_X, CENTER_Y = 3, 4

#: The three owner kinds the placements draw from -> the tile that owner's PLAYER
#: stands on, as an offset from the centre. Chebyshev 0, 2, and 9: with a radius
#: drawn from 0 through 8, the first is always inside, the second crosses the
#: boundary as the radius grows, and the third is always outside.
STANDING_OFFSETS: dict[str, tuple[int, int]] = {
    "self": (0, 0),
    "ally": (2, -1),
    "enemy": (9, 9),
}


class _Room:
    """A PlanetRoom stand-in answering the two tile queries the audience asks.

    Recognized **duck-typed** by ``get_players_at`` and ``get_objects_in_area``,
    which is exactly how ``OperationDriver._tile_queryable`` recognizes a real
    ``PlanetRoom`` — so this fake is the cross-module contract rather than a
    restatement of the room's internals.

    ``get_objects_in_area`` answers **everything it holds**, ignoring the bounds
    it was asked for. That is the point: for the Chebyshev metric the bounding box
    equals the ball, so a room that filtered by the box would leave the driver's
    own radius filter with nothing to do and nothing to get wrong. The bounds are
    recorded instead, so "the right box was asked for" stays a separate claim.
    """

    def __init__(self):
        self.players: dict[tuple[int, int], list] = {}
        self.objects: list = []
        self.area_calls: list[tuple] = []

    def stand(self, player, x, y):
        """Stand *player* on tile ``(x, y)`` — the tile-sweep half of R8.12."""
        self.players.setdefault((int(x), int(y)), []).append(player)
        return player

    def place(self, entity):
        """Put *entity* in the room at its own coordinates, and return it."""
        self.objects.append(entity)
        return entity

    def get_players_at(self, x, y):
        return list(self.players.get((int(x), int(y)), ()))

    def get_objects_in_area(self, x1, y1, x2, y2):
        self.area_calls.append((x1, y1, x2, y2))
        return list(self.objects)


class _AreaEntity:
    """An entity in the area: an identity, an owner, a tile, and its room.

    The ``location`` is what makes the room reachable at all: the driver holds no
    world reference of its own (R15.1), so it finds the room through the record's
    references and their locations.
    """

    _next_id = 700

    def __init__(self, owner=None, x=CENTER_X, y=CENTER_Y, room=None):
        _AreaEntity._next_id += 1
        self.id = _AreaEntity._next_id
        self.key = f"Entity #{self.id}"
        self.location = room
        self.db = SimpleNamespace(owner=owner, coord_x=x, coord_y=y)


class _AreaVector(_LifecycleVector):
    """A vector whose record carries an effect RADIUS as well as its target.

    The one field the area is read from that :class:`_LifecycleVector` leaves at
    its default. A vector's own arithmetic decides it — this one is told.
    """

    radius = 0

    def build_record(self, ctx):
        record = super().build_record(ctx)
        record.radius = self.radius
        return record


def _within(dx, dy, radius):
    """Return whether ``(dx, dy)`` is inside *radius* under the Chebyshev metric.

    The same metric every other spatial reach in this game uses, so a
    Vector_Operation's area matches a bomb blast's rather than introducing a
    second notion of "nearby".
    """
    return max(abs(dx), abs(dy)) <= radius


def _area_world(radius, placements):
    """Return a world holding one placed operation with an area around it.

    The originating player and the ally are **actually allied** (one shared
    ``player_alliance``), and the target entity belongs to the unaffiliated
    player, so every one of the three owner kinds is present in every example
    before a single placement is drawn.
    """
    world = _staged(vector_cls=_AreaVector)
    world.room = _Room()
    world.vector.radius = radius
    world.ally = FakePlayer(key="Kesh", planet=PLANET)
    world.player.db.player_alliance = ALLIANCE_ID
    world.ally.db.player_alliance = ALLIANCE_ID
    world.owners = {
        "self": world.player, "ally": world.ally, "enemy": world.defender,
    }
    #: The named target: affected by definition, whatever the radius (R11.10).
    world.target = world.room.place(
        _AreaEntity(owner=world.defender, room=world.room)
    )
    world.placements = []
    for dx, dy, kind in placements:
        world.placements.append((
            world.room.place(_AreaEntity(
                owner=world.owners[kind],
                x=CENTER_X + dx, y=CENTER_Y + dy, room=world.room,
            )),
            dx, dy, kind,
        ))
    for kind, (dx, dy) in STANDING_OFFSETS.items():
        world.room.stand(world.owners[kind], CENTER_X + dx, CENTER_Y + dy)
    return _place(world)


def _reference_area(world, radius):
    """Return ``(affected entities, audience)`` as R11.10 and R8.12 declare them.

    The affected set is the named target plus every placed entity inside the
    radius — with **no ownership or alliance term anywhere in the computation**,
    which is what makes it a reference for R11.10 rather than a restatement of the
    code. The audience is the owners of those entities together with the players
    standing on a tile the effect reaches.
    """
    affected = [world.target] + [
        entity for entity, dx, dy, _kind in world.placements
        if _within(dx, dy, radius)
    ]
    occupants = [
        world.owners[kind] for kind, (dx, dy) in STANDING_OFFSETS.items()
        if _within(dx, dy, radius)
    ]
    owners = [getattr(entity, "db").owner for entity in affected]
    return affected, [*owners, *occupants]


def _sent_to(entries, kind):
    """Return the identity set of the players *kind* was published to."""
    return _ids([entry["player"] for entry in entries if entry["kind"] == kind])


# Feature: tech-tree-branch-foundation, Property 25: An area effect reaches every
# entity in the area, allied or not
#
# **Validates: Requirements 11.10**
class TestProperty25AreaEffectIsIndiscriminate(unittest.TestCase):
    """The area is a distance, never a friend-or-foe question."""

    @given(
        placements=st.lists(
            st.tuples(
                st.integers(min_value=-10, max_value=10),
                st.integers(min_value=-10, max_value=10),
                st.sampled_from(tuple(STANDING_OFFSETS)),
            ),
            max_size=8,
        ),
        radius=st.integers(min_value=0, max_value=8),
    )
    @settings(max_examples=100)
    def test_an_area_effect_reaches_every_entity_in_the_area(
        self, placements, radius
    ):
        """**Validates: Requirements 11.10**"""
        world = _area_world(radius, placements)
        record = world.record
        self.assertEqual(record.radius, radius)
        affected, audience = _reference_area(world, radius)

        # -- 1. The affected set is exactly the entities inside the radius ---- #
        answered = world.vector._affected_entities(record)
        self.assertEqual(
            _ids(answered), _ids(affected),
            "the affected set is not exactly the entities within the radius",
        )
        self.assertEqual(
            len(answered), len(_ids(answered)),
            "an entity was reported twice; the affected set must be de-duplicated",
        )

        # -- 2. R11.10, stated in its own terms, side by side ---------------- #
        # Nothing is excluded on the grounds of ownership or alliance: an entity
        # the ORIGINATING player owns and an entity their ALLY owns are inside the
        # area on exactly the same terms as an enemy's.
        for entity, dx, dy, kind in world.placements:
            inside = _within(dx, dy, radius)
            self.assertEqual(
                entity.id in _ids(answered), inside,
                f"a {kind}-owned entity at ({dx}, {dy}) is "
                f"{'inside' if inside else 'outside'} a radius of {radius}, and "
                "ownership must not decide it",
            )
        self.assertIn(
            world.target.id, _ids(answered),
            "the named target is affected by definition, whatever the radius",
        )

        # -- 3. The box the room was asked for ------------------------------- #
        self.assertEqual(
            world.room.area_calls[-1],
            (CENTER_X - radius, CENTER_Y - radius, CENTER_X + radius, CENTER_Y + radius),
            "the effect area query did not cover the radius around the centre",
        )

        # -- 4. R8.12's audience: affected owners plus tile occupants --------- #
        reached = world.vector._resolution_audience(record)
        self.assertEqual(
            _ids(reached), _ids(audience),
            "the resolution audience is not the owners plus the tile occupants",
        )
        self.assertEqual(
            len(reached), len(_ids(reached)),
            "a player was notified twice; the audience must be de-duplicated",
        )
        self.assertIn(
            world.player.id, _ids(reached),
            "the originating player stands at the centre, so the effect reaches "
            "them too (R11.10)",
        )

        # -- 5. The alliance is not a term in the answer --------------------- #
        # Flipping the relationship changes the world's data and nothing else,
        # because the audience never asks the question.
        world.ally.db.player_alliance = RIVAL_ALLIANCE_ID
        self.assertEqual(
            _ids(world.vector._resolution_audience(record)), _ids(audience),
            "the audience changed when the alliance did, so it filters on it",
        )
        world.ally.db.player_alliance = ALLIANCE_ID      # allied again, for below

        # -- 6. And the resolution tells all of them (R8.12, R11.10) --------- #
        told = len(world.bus.notifications())
        self.assertTrue(world.vector._resolve(record))
        fresh = world.bus.notifications()[told:]
        self.assertEqual(
            _sent_to(fresh, NOTIFY_VECTOR_RESOLVED), _ids([world.player]),
            "the owner reads their own operation landing, and only they do",
        )
        self.assertEqual(
            _sent_to(fresh, NOTIFY_VECTOR_HIT),
            _ids(audience) - _ids([world.player]),
            "the players the effect reached were not all told it landed on them",
        )


if __name__ == "__main__":
    unittest.main()
