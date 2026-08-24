"""
Property-based tests for the Reinstatement half of Branch dormancy.

Feature: tech-tree-branch-foundation (design section "Correctness Properties").

The two properties the design's test-module table assigns to this file, both
implemented here:

- **Property 8**: A Reinstatement job costs the defined values scaled by the
  configured fraction — Requirements 5.6.
- **Property 9**: Reinstatement is required after abandonment and not after
  destruction — Requirements 5.5, 5.9.

They share a module because they are the price and the trigger of one thing.
Requirements 5.5 and 5.9 differ on a single point — a Branch abandoned
*voluntarily* costs Reinstatement research on the way back, a Branch whose lab
an enemy destroyed does not — and after the fact the world cannot tell the two
apart, so one persisted bit carries the distinction from the moment it is known
to the moment it is charged. Property 9 is about that bit deciding *whether* a
job is owed; Property 8 is about what the job then costs.

Neither property writes ``db.branch_abandoned`` or ``db.branch_reinstatement``.
``BranchSystem`` is their single writer (R15.5), so a pending set is established
here the way the game establishes it: abandon the Branch's lab, then build it
again. A test that assigned the attribute would be asserting against its own
fixture rather than against the bookkeeping, and would keep passing if the
bookkeeping stopped running.

Every generator is drawn from ``branch_strategies`` or composed from its
fixtures, and that module also installs the Evennia stubs at import — hence its
import is deliberately FIRST here, so this module loads with ``evennia`` absent
from ``sys.modules`` (R15.1). Every registry is built in memory and injected, so
no example needs the process-wide ``DataRegistry`` singleton (R15.4).
"""

import unittest
from typing import Any, NamedTuple

from hypothesis import given, settings
from hypothesis import strategies as st

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (
    CANONICAL_COUNTER_WEB,
    FIXTURE_BUILDING_DICTS,
    FIXTURE_LAB_ABBR,
    FIXTURE_OPERATION_KINDS,
    FIXTURE_TECH_KEYS,
    FIXTURE_TECH_KEYS_BY_BRANCH,
    FIXTURE_TECHNOLOGY_DICTS,
    FakeBuilding,
    FakePlayer,
    branch_st,
    cost_map_st,
    fixture_registry,
    make_registry,
    researched_set_st,
)
from mygame.world.constants import (
    ATTR_BRANCH_ABANDONED,
    ATTR_BRANCH_REINSTATEMENT,
    RESOURCE_TYPES,
)
from mygame.world.definitions import BalanceConfig
from mygame.world.event_bus import (
    BUILDING_DESTROYED,
    CONSTRUCTION_COMPLETED,
    EventBus,
)
from mygame.world.systems.branch_system import BranchSystem
from mygame.world.systems.tech_system import TechLabSystem

#: The one planet both properties play out on. Reinstatement bookkeeping is
#: keyed by Branch and is deliberately *not* planet-scoped, so a second planet
#: would add a dimension neither property is about — the per-planet scoping of a
#: commitment is Property 5's claim.
HOME = "earth"

#: Fixture technology key -> the Branch hosting it. The reference for "the
#: recorded technologies of that Branch", answered from the fixture tables so it
#: needs no read of the registry the systems were injected with.
_FIXTURE_BRANCH_OF_TECH: dict[str, str] = {
    key: branch
    for branch, keys in FIXTURE_TECH_KEYS_BY_BRANCH.items()
    for key in keys
}

#: Enough of every resource that no example is refused for affordability — the
#: charge is what is being measured, not the have/need breakdown.
_DEEP_POCKETS = 1_000_000


def _lab(branch: str) -> FakeBuilding:
    """A completed Branch_Lab of *branch* standing on :data:`HOME`."""
    return FakeBuilding(building_type=FIXTURE_LAB_ABBR[branch], planet=HOME)


def _committed_player(record: Any = (), resources: Any = None) -> FakePlayer:
    """A player on :data:`HOME` whose record holds *record*."""
    player = FakePlayer(
        key="Reinstater",
        planet=HOME,
        resources=resources,
    )
    player.db.researched_techs = set(record)
    player.db.tech_bonuses = {}
    return player


def _wire(registry: Any) -> tuple[Any, Any, Any]:
    """Return ``(bus, tech, branch)`` wired the way the composition root wires them.

    The Branch system is the tech system's Branch resolver and the tech system is
    the Branch system's recompute collaborator, which is the pairing that makes
    the Reinstatement job and the pending set two halves of one mechanism: the
    job asks whether a key is owed, and the completion asks the pending set's
    owner to clear it (R15.5).
    """
    bus = EventBus()
    tech = TechLabSystem(registry, bus)
    branch = BranchSystem(registry, bus, tech_system=tech)
    tech.set_branch_resolver(branch)
    return bus, tech, branch


# ================================================================== #
#  Property 8
# ================================================================== #
#
# The claim is arithmetic, so the interesting question is what the reference
# compares against. Three answers, all in the one test below:
#
# 1. **The formula.** Per resource line, the defined amount times the fraction,
#    rounded to the nearest whole unit, floored at one; the duration likewise,
#    floored at one tick. Stated once here and once in ``tech_system``, which
#    catches a fraction read from the wrong balance field, a floor applied to
#    the map instead of to each line, a truncation where the design asks for a
#    round, and a duration left unscaled.
# 2. **The defined values, measured.** "The technology's defined resource cost"
#    is not read off the definition and asserted against itself — it is measured
#    by running a FIRST-TIME research job for the same technology in the same
#    world. That is what makes "scaled" a relative claim: a discount applied to
#    both kinds of job, or to neither, fails here even though it satisfies the
#    formula.
# 3. **The fraction as the knob.** The same job priced at two drawn fractions
#    charges in the same order as the fractions, and at a fraction of exactly
#    1.0 charges the defined values — the identity ``BalanceConfig`` documents.
#    A hard-coded 0.5 passes clause 1 for exactly one drawn fraction and fails
#    this one for almost all of them.
#
# Every charge is read as the *delta on the player's resource counters* rather
# than from the cost map the code built, so a job that computed the right price
# and deducted something else is a mismatch. The job is started through
# ``start_research`` — the real entry point, with its real gates — over a pending
# set the Branch system seeded through its own abandon-and-rebuild path.


def _reference_line(amount: int, fraction: float) -> int:
    """One resource line of a Reinstatement job's charge (R5.6).

    The defined amount times the fraction, rounded to the nearest whole unit,
    with a floor of one: a cheap technology is discounted but never free.
    ``round`` is Python's own, so a tie lands on the even value — the rounding
    the design's formula documents, ties included.
    """
    return max(1, int(round(amount * fraction)))


def _reference_cost(cost: dict, fraction: float) -> dict:
    """*cost* scaled line by line, keeping every line the defined map declares."""
    return {
        resource: _reference_line(amount, fraction)
        for resource, amount in cost.items()
    }


def _reference_ticks(ticks: int, fraction: float) -> int:
    """A Reinstatement job's duration: *ticks* scaled, floored at one tick."""
    return max(1, int(round(ticks * fraction)))


def _priced_registry(
    branch: str, cost: dict, ticks: int, fraction: float
) -> Any:
    """The fixture catalog with one technology repriced, at *fraction*.

    Only the first technology of *branch* is touched, so the rest of the catalog
    stays the valid six-Branch fixture the labs and the commitment need. The
    fraction rides on the injected ``BalanceConfig``, which is where the system
    reads it from — there is no second source to disagree with.
    """
    key = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
    technologies = []
    for entry in FIXTURE_TECHNOLOGY_DICTS:
        entry = dict(entry)
        if entry["key"] == key:
            entry["resource_cost"] = dict(cost)
            entry["research_ticks"] = ticks
        technologies.append(entry)
    return make_registry(
        buildings=FIXTURE_BUILDING_DICTS,
        technologies=technologies,
        counter_web=CANONICAL_COUNTER_WEB,
        operation_kinds=FIXTURE_OPERATION_KINDS,
        balance=BalanceConfig(branch_reinstatement_cost_fraction=fraction),
    )


class _Job(NamedTuple):
    """What one started research job charged, and what it queued."""

    ok: bool
    message: str
    #: Resource -> units actually deducted from the player's counters.
    charged: dict
    #: The queue entry the job created, or ``None`` when none was created.
    entry: dict | None
    #: Whether the key was owed a Reinstatement job *before* it was started.
    owed: bool


def _start_job(
    branch: str, cost: dict, ticks: int, fraction: float, reinstating: bool
) -> _Job:
    """Price one research job in a world where the fraction is *fraction*.

    Two shapes, one code path. With *reinstating* the player's record already
    holds the technology and the Branch was abandoned and rebuilt, so
    ``start_research`` opens a Reinstatement job; without it the record is empty
    and the same call opens a first-time job at the defined price. Running both
    through the same helper is what makes their charges comparable.

    The pending set is seeded by demolishing the lab and completing it again —
    ``BranchSystem`` writing its own attributes (R15.5). Nothing here assigns
    ``db.branch_reinstatement``.
    """
    registry = _priced_registry(branch, cost, ticks, fraction)
    bus, tech, system = _wire(registry)
    key = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
    player = _committed_player(
        record=(key,) if reinstating else (),
        resources={resource: _DEEP_POCKETS for resource in RESOURCE_TYPES},
    )
    lab = _lab(branch)
    if reinstating:
        # Abandon the Branch: the lab is gone from the roster before the trigger
        # fires, which is the order the demolish command makes it in.
        player.set_buildings([])
        system.on_building_demolished(
            player, FIXTURE_LAB_ABBR[branch], planet=HOME
        )
        # Build it again: the completion turns the abandoned bit into the
        # pending set the job is charged for.
        player.set_buildings([lab])
        bus.publish(CONSTRUCTION_COMPLETED, player=player, building=lab)
    else:
        player.set_buildings([lab])
    owed = system.reinstatement_pending(player, key)
    before = player.resource_snapshot()
    ok, message = tech.start_research(player, key)
    charged = {
        resource: before[resource] - player.get_resource(resource)
        for resource in RESOURCE_TYPES
        if before[resource] != player.get_resource(resource)
    }
    entry = dict(tech._active_research[-1]) if tech._active_research else None
    return _Job(ok=ok, message=message, charged=charged, entry=entry, owed=owed)


# Feature: tech-tree-branch-foundation, Property 8: A Reinstatement job costs
# the defined values scaled by the configured fraction
#
# **Validates: Requirements 5.6**
class TestProperty8ReinstatementJobPricing(unittest.TestCase):
    """The price of coming back is the defined price times the configured fraction.

    One ``@given`` test, because the clauses are one claim measured three ways:
    the charge equals the scaled formula, it equals the *measured* defined values
    when the fraction is 1.0 and when the job is a first-time one, and it moves
    with the fraction. The bounds clauses in between state what the formula is
    for — never free, never dearer than the defined price, never more than a
    rounding away from the arithmetic — so a passing example is readable as a
    price and not only as an equality.
    """

    def _assert_within_rounding(self, scaled, exact, floored_at_one, where):
        """A scaled value is its arithmetic, rounded — or the floor of one."""
        self.assertTrue(
            scaled == 1 or abs(scaled - exact) <= 0.5,
            f"{where}: {scaled} is neither the floor of one nor within a "
            f"rounding of {exact}",
        )
        self.assertGreaterEqual(
            scaled, 1, f"{where}: a Reinstatement job may be discounted, never free"
        )
        self.assertLessEqual(
            scaled, floored_at_one,
            f"{where}: {scaled} is dearer than the defined "
            f"{floored_at_one} at a fraction of at most 1.0",
        )

    @given(
        branch=branch_st,
        cost=cost_map_st,
        ticks=st.integers(min_value=1, max_value=500),
        fraction=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        other_fraction=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_a_reinstatement_job_costs_the_scaled_defined_values(
        self, branch, cost, ticks, fraction, other_fraction
    ):
        """**Validates: Requirements 5.6**"""
        job = _start_job(branch, cost, ticks, fraction, reinstating=True)

        # The fixture has to have actually owed the job, or every clause below
        # would be measuring a first-time research job at full price.
        self.assertTrue(
            job.owed,
            "the abandon-and-rebuild fixture did not leave the technology "
            "awaiting Reinstatement, so this example would prove nothing",
        )
        self.assertTrue(job.ok, f"the Reinstatement job was refused: {job.message}")
        self.assertIsNotNone(job.entry, "the Reinstatement job queued nothing")
        self.assertIs(
            job.entry["reinstatement"], True,
            "the queued job is not marked as a Reinstatement, so it would "
            "complete as a first-time research job",
        )

        # --- The charge is the defined cost scaled line by line (R5.6) ---- #
        self.assertEqual(
            job.charged, _reference_cost(cost, fraction),
            f"the resources deducted at a fraction of {fraction!r} are not the "
            f"defined cost {cost!r} scaled line by line",
        )
        self.assertEqual(
            set(job.charged), set(cost),
            "scaling changed WHICH resources a Reinstatement job charges",
        )
        for resource, amount in cost.items():
            self._assert_within_rounding(
                job.charged[resource], amount * fraction, amount,
                f"the {resource} line at a fraction of {fraction!r}",
            )

        # --- The duration is the defined one scaled the same way (R5.6) --- #
        self.assertEqual(
            job.entry["ticks_remaining"], _reference_ticks(ticks, fraction),
            f"the queued duration at a fraction of {fraction!r} is not the "
            f"defined {ticks} ticks scaled by it",
        )
        self._assert_within_rounding(
            job.entry["ticks_remaining"], ticks * fraction, ticks,
            f"the duration at a fraction of {fraction!r}",
        )

        # --- "The defined values", measured rather than assumed ----------- #
        # The same technology in the same world, researched for the first time:
        # this is what the Reinstatement job is a fraction OF, so a discount
        # applied to every research job (or to none) fails here.
        full = _start_job(branch, cost, ticks, fraction, reinstating=False)
        self.assertTrue(full.ok, f"the first-time job was refused: {full.message}")
        self.assertFalse(
            full.owed, "the first-time fixture was owed a Reinstatement job"
        )
        self.assertIs(
            full.entry["reinstatement"], False,
            "a first-time research job was marked as a Reinstatement",
        )
        self.assertEqual(
            full.charged, dict(cost),
            "a first-time research job did not charge the technology's defined "
            "resource cost — the Reinstatement fraction leaked onto it",
        )
        self.assertEqual(
            full.entry["ticks_remaining"], ticks,
            "a first-time research job did not take the technology's defined "
            "duration",
        )

        # --- A fraction of exactly 1.0 is the defined price --------------- #
        whole = _start_job(branch, cost, ticks, 1.0, reinstating=True)
        self.assertTrue(whole.ok, f"the job at 1.0 was refused: {whole.message}")
        self.assertEqual(
            (whole.charged, whole.entry["ticks_remaining"]), (dict(cost), ticks),
            "a Reinstatement fraction of 1.0 did not price the job at the "
            "technology's defined cost and duration",
        )

        # --- The fraction is the knob, and it points the right way -------- #
        other = _start_job(branch, cost, ticks, other_fraction, reinstating=True)
        self.assertTrue(other.ok, f"the second job was refused: {other.message}")
        cheap, dear = (
            (job, other) if fraction <= other_fraction else (other, job)
        )
        for resource in cost:
            self.assertLessEqual(
                cheap.charged[resource], dear.charged[resource],
                f"the {resource} line did not rise with the Reinstatement "
                f"fraction ({fraction!r} vs {other_fraction!r})",
            )
        self.assertLessEqual(
            cheap.entry["ticks_remaining"], dear.entry["ticks_remaining"],
            f"the duration did not rise with the Reinstatement fraction "
            f"({fraction!r} vs {other_fraction!r})",
        )


# ================================================================== #
#  Property 9
# ================================================================== #
#
# A sequence property, and the reference is a four-field model of the design's
# own state machine: is a lab standing, was the last loss a demolition or a
# destruction, which Branches carry the abandoned bit, what is pending. The
# model is walked beside the real systems and compared after **every** event, so
# a bit set one event too early, an abandoned bit that survives the seed it paid
# for, a seed that fires on a completion nothing preceded, or a pending list
# built from the wrong Branch's record all fail at the step that caused them
# rather than at the end.
#
# Two things the driver deliberately does not do:
#
# - It does not deliver a demolition or a destruction while no lab is standing.
#   The command layer fires the demolish trigger for a building it has just
#   deleted, and ``BUILDING_DESTROYED`` for one that is about to be; neither can
#   arrive for a lab that is not there, and inventing that call would be
#   asserting against a state the game cannot reach.
# - It never assigns ``db.branch_abandoned`` or ``db.branch_reinstatement``.
#   Both are written by ``BranchSystem`` alone (R15.5), so the events are the
#   only input and the attributes are pure output.
#
# The design's sentence — "empty when it was a destruction" — is a claim about a
# Branch with no *outstanding* debt. A player who abandons a Branch, rebuilds,
# and then loses that lab to an attack without ever finishing the Reinstatement
# jobs still owes them, and the implementation rightly carries them: the second
# rebuild writes nothing rather than clearing a debt nobody paid. So the driver
# runs each sequence twice. In the **carried** run the claim is the general one
# — a rebuild after a demolition seeds the Branch's recorded technologies, and a
# rebuild after a destruction changes nothing. In the **settled** run every
# Reinstatement job is completed as soon as it is owed (through
# ``on_reinstatement_completed``, the pending set's own clearing path), which is
# the state the design's sentence describes, and there the sentence is asserted
# verbatim in both directions.
#
# What the bookkeeping is FOR is asserted at every step too: the set of
# technologies whose effects are live equals the Branch's record minus what is
# still owed, so "rebuilding after an attack restores the Branch with no
# research" (R5.9) is checked as an effect and not only as an empty list.

#: The four lifecycle events the design names. ``commit`` and ``rebuild`` are one
#: world event under two names — a Branch_Lab completing — and the driver treats
#: them identically on purpose: nothing about the completion itself decides
#: whether Reinstatement is owed, only the loss that preceded it does.
_LIFECYCLE_EVENTS = ("commit", "demolish", "destroy", "rebuild")

#: Appended to every drawn sequence, so both halves of the "if and only if" are
#: exercised in every example whatever was drawn: a lab stands, is abandoned and
#: rebuilt (Reinstatement owed), then is destroyed and rebuilt (nothing owed).
_CANONICAL_TAIL = ("commit", "demolish", "rebuild", "destroy", "rebuild")


class _LabLifecycle:
    """One player, one Branch, one planet, and the real triggers in between.

    Holds the reference model beside the live systems. Each ``apply`` delivers
    one lifecycle event to the real ``BranchSystem``, advances the model, and
    asserts the two agree — including the ``if and only if`` clause at every lab
    completion, which is the property's own sentence.
    """

    def __init__(self, tc: unittest.TestCase, branch: str, record: Any, settle: bool):
        self.tc = tc
        self.branch = branch
        self.record = frozenset(record)
        self.settle = settle
        self.bus, self.tech, self.system = _wire(fixture_registry())
        self.player = _committed_player(record=record)
        #: The standing lab, or ``None`` — the model's "is a lab there" field.
        self.lab: Any = None
        #: ``"demolish"``, ``"destroy"``, or ``None`` once a completion has
        #: answered the loss. The one field the whole property turns on.
        self.last_loss: str | None = None
        #: The model of ``db.branch_abandoned`` and ``db.branch_reinstatement``.
        self.abandoned: dict = {}
        self.pending: dict = {}

    # -- the reference ---------------------------------------------- #

    @property
    def owed(self) -> list[str]:
        """This Branch's recorded technologies: what a rebuild must seed (R5.5).

        Read off the fixture Branch table rather than the injected registry, so a
        pending list built from the wrong Branch — or from the whole record — is
        a mismatch rather than a shared mistake.
        """
        return sorted(
            key for key in self.record
            if _FIXTURE_BRANCH_OF_TECH.get(key) == self.branch
        )

    def stored(self, attr: str) -> dict:
        """One persisted mapping, with its documented absent default (R14.8).

        An attribute never written and an attribute written empty both read as
        ``{}`` — "nothing abandoned, nothing owed" has one meaning here, which is
        what lets the model be compared against the store as a whole.
        """
        return dict(getattr(self.player.db, attr) or {})

    def stored_pending(self) -> list[str]:
        """This Branch's pending list as stored, or ``[]`` when it has none."""
        return list(self.stored(ATTR_BRANCH_REINSTATEMENT).get(self.branch, []))

    # -- the events ------------------------------------------------- #

    def apply(self, event: str, where: str) -> None:
        """Deliver one lifecycle event, advance the model, and compare."""
        if event in ("commit", "rebuild"):
            self._complete(where)
        elif self.lab is not None and event == "demolish":
            self._demolish()
        elif self.lab is not None and event == "destroy":
            self._destroy()
        self._assert_state(where)

    def _complete(self, where: str) -> None:
        """A Branch_Lab of this Branch completes: the return half (R5.5, R5.9).

        With a lab already standing this is an upgrade completing, which is worth
        delivering: the abandoned bit is not set while the commitment stands, so
        the completion must seed nothing.
        """
        loss = self.last_loss
        before = self.stored_pending()
        if self.lab is None:
            self.lab = _lab(self.branch)
            self.player.set_buildings([self.lab])
        self.bus.publish(
            CONSTRUCTION_COMPLETED, player=self.player, building=self.lab
        )
        if self.abandoned.pop(self.branch, False):
            self.pending[self.branch] = self.owed
        self.last_loss = None

        seeded = self.stored_pending()
        if loss == "demolish":
            # R5.5 — the way back from a voluntary abandonment is a job per
            # recorded technology of that Branch.
            self.tc.assertEqual(
                seeded, self.owed,
                f"{where}: a lab rebuilt after a voluntary demolition did not "
                f"leave this Branch's recorded technologies awaiting "
                f"Reinstatement",
            )
        else:
            # R5.9 — a lab lost to an attack (or a first commitment, or an
            # upgrade) writes nothing at all, so nothing new is owed.
            self.tc.assertEqual(
                seeded, before,
                f"{where}: a lab completion that no voluntary demolition "
                f"preceded changed what this Branch owes",
            )
            if self.settle:
                self.tc.assertEqual(
                    seeded, [],
                    f"{where}: a lab rebuilt after a destruction requires "
                    f"Reinstatement research",
                )
        if self.settle and self.owed:
            # The design's sentence, both directions, in the state it describes.
            self.tc.assertEqual(
                seeded == self.owed, loss == "demolish",
                f"{where}: this Branch's pending set equals its recorded "
                f"technologies ({seeded == self.owed}) but the preceding loss "
                f"being a voluntary demolition is {loss == 'demolish'}",
            )
        if self.settle:
            self._settle(where)

    def _demolish(self) -> None:
        """The owner demolishes the standing lab: the one moment R5.5 is known.

        The trigger arrives as a direct call after the delete, so the lab leaves
        the roster first — which is also what makes the commitment lapse the
        system reads to decide whether anything was abandoned at all.
        """
        self.player.set_buildings([])
        self.lab = None
        self.system.on_building_demolished(
            self.player, FIXTURE_LAB_ABBR[self.branch], planet=HOME
        )
        self.abandoned[self.branch] = True
        self.last_loss = "demolish"

    def _destroy(self) -> None:
        """An enemy razes the standing lab: the path that must write nothing.

        ``BUILDING_DESTROYED`` fires *before* the delete, so the dying lab is
        still on the roster when the event is published — the ordering the
        subscriber is written against.
        """
        dying = self.lab
        self.bus.publish(BUILDING_DESTROYED, building=dying, attacker=None)
        self.player.set_buildings([])
        self.lab = None
        dying.delete()
        self.last_loss = "destroy"

    def _settle(self, where: str) -> None:
        """Complete every Reinstatement job this Branch owes, right now.

        Through ``on_reinstatement_completed`` — what the research job calls when
        its countdown ends, and the only path that empties the pending set — so
        the settled run pays the debt the way the game pays it.
        """
        for key in self.stored_pending():
            self.tc.assertTrue(
                self.system.on_reinstatement_completed(self.player, key),
                f"{where}: completing the Reinstatement of {key!r} cleared "
                f"nothing, though it was owed",
            )
        if self.pending.get(self.branch):
            del self.pending[self.branch]

    # -- the comparison --------------------------------------------- #

    def _assert_state(self, where: str) -> None:
        """Assert the whole persisted surface equals the model."""
        self.tc.assertEqual(
            self.stored(ATTR_BRANCH_ABANDONED), self.abandoned,
            f"{where}: the abandoned bits disagree with the model — only a "
            f"voluntary demolition sets one, and a seed clears it",
        )
        self.tc.assertEqual(
            self.stored(ATTR_BRANCH_REINSTATEMENT), self.pending,
            f"{where}: the pending Reinstatement sets disagree with the model",
        )
        self.tc.assertEqual(
            set(self.player.db.researched_techs), set(self.record),
            f"{where}: the researched record changed — abandoning a Branch "
            f"suspends its effects and erases no history (R5.3)",
        )

        # The predicate the research job asks before refusing a recorded key as
        # "already researched" reads the same store, for every Branch's keys.
        for key in FIXTURE_TECH_KEYS:
            expected = key in self.stored(ATTR_BRANCH_REINSTATEMENT).get(
                _FIXTURE_BRANCH_OF_TECH[key], []
            )
            self.tc.assertEqual(
                self.system.reinstatement_pending(self.player, key), expected,
                f"{where}: reinstatement_pending({key!r}) disagreed with the "
                f"stored pending set",
            )

        # What the bookkeeping is for: with the lab standing, this Branch's
        # effects are live except for what is still owed — so a rebuild after a
        # destruction restores them with no research at all (R5.9).
        live = set()
        if self.lab is not None:
            live = set(self.owed) - set(self.pending.get(self.branch, ()))
        self.tc.assertEqual(
            self.system.applied_technologies(self.player, HOME), frozenset(live),
            f"{where}: the technologies whose effects are live are not this "
            f"Branch's record minus what is still awaiting Reinstatement",
        )


# Feature: tech-tree-branch-foundation, Property 9: Reinstatement is required
# after abandonment and not after destruction
#
# **Validates: Requirements 5.5, 5.9**
class TestProperty9ReinstatementFollowsAbandonment(unittest.TestCase):
    """Whether coming back costs research is decided by how the lab was lost.

    One ``@given`` test walking one drawn event sequence four times — the record
    as drawn and the record guaranteed to hold one of this Branch's technologies,
    each with the debt carried and with the debt settled — because the clauses
    are one claim about one state machine and the interesting failures live in
    the orderings rather than in any single step.
    """

    @given(
        events=st.lists(
            st.sampled_from(_LIFECYCLE_EVENTS), min_size=1, max_size=8
        ),
        record=researched_set_st,
        branch=branch_st,
    )
    @settings(max_examples=100)
    def test_reinstatement_is_owed_after_a_demolition_and_not_a_destruction(
        self, events, record, branch
    ):
        """**Validates: Requirements 5.5, 5.9**"""
        sequence = tuple(events) + _CANONICAL_TAIL
        # The record as drawn covers the Branch holding nothing; anchoring one of
        # its technologies is what makes the "if and only if" observable, since
        # an empty record makes both sides of it empty.
        anchored = set(record) | {FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]}

        for label, held in (("as drawn", record), ("anchored", anchored)):
            for settle in (False, True):
                world = _LabLifecycle(self, branch, held, settle=settle)
                mode = "settled" if settle else "carried"
                for index, event in enumerate(sequence):
                    world.apply(
                        event, f"{label}, {mode} run, step {index} ({event})"
                    )


if __name__ == "__main__":
    unittest.main()
