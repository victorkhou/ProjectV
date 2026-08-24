"""
Property-based tests for Branch commitment, dormancy, and the Branch role gate.

Feature: tech-tree-branch-foundation (design section "Correctness Properties").

The five properties the design's test-module table assigns to this file, all
implemented here:

- **Property 5**: Branch_Commitment is the owned completed lab's Branch,
  independent of Operational state and of restarts — Requirements 3.1, 3.2,
  3.7, 3.8, 3.9, 5.10, 14.6.
- **Property 6**: Branch_Estate membership equals the reference scan, and the
  switch report equals the estate — Requirements 4.1, 4.2, 4.3, 4.5, 4.7, 13.4.
- **Property 7**: Applied tech bonuses equal the accumulation over the
  committed, non-pending techs — Requirements 5.1, 5.2, 5.3, 5.7, 5.10, 13.1,
  13.2.
- **Property 10**: A Branch_Building is Operational exactly when the base gate
  passes and its Branch is live — Requirements 5.4, 11.3.
- **Property 12**: The Branch role gate permits exactly matching commitments,
  and a lapse clears exactly the gated roles on that planet — Requirements 7.6,
  7.7, 7.8.

All five are one question asked by five consumers, which is why they share a
module. Properties 5 and 6 are the same scan asked two ways, and they disagree
on exactly one filter: a commitment needs a *completed* lab, an estate needs the
building merely to exist (R4.7). Reading them side by side is what keeps that
difference deliberate. The remaining three are that same commitment read by the
three systems that consume it — the bonus dict (7), a building's Operational
state (10), and the agent roster (12) — so each is stated against the reference
commitment rather than against ``BranchSystem.commitment``'s own answer, and a
commitment resolved wrongly fails Property 5 rather than quietly agreeing here.

Every generator is drawn from ``branch_strategies`` or composed from its
fixtures, and that module also installs the Evennia stubs at import — hence its
import is deliberately FIRST here, so this module loads with ``evennia`` absent
from ``sys.modules`` (R15.1). Every registry is built in memory and injected, so
no example needs the process-wide ``DataRegistry`` singleton (R15.4).
"""

import copy
import unittest
from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (
    FIXTURE_AGENT_ROLES,
    FIXTURE_BRANCH_BUILDING_ABBR,
    FIXTURE_BUILDING_DICTS,
    FIXTURE_LAB_ABBR,
    FIXTURE_NEUTRAL_ABBRS,
    FIXTURE_PLANETS,
    FIXTURE_TECH_KEYS_BY_BRANCH,
    FIXTURE_TECHNOLOGY_DICTS,
    TECH_BONUS_KEYS,
    FakeAttributes,
    FakeBuilding,
    FakeDB,
    FakePlayer,
    branch_st,
    fixture_registry,
    maybe_branch_st,
    owned_buildings_st,
    pending_set_st,
    researched_set_st,
)
from mygame.typeclasses.agent_scripts import AGENT_ROLES
from mygame.world.constants import (
    BRANCH_DOCTRINE,
    BRANCH_OPERATION_KIND,
    BRANCHES,
    HEADQUARTERS,
    RESEARCH_LAB,
)
from mygame.world.definitions import BalanceConfig
from mygame.world.event_bus import (
    BUILDING_DESTROYED,
    CONSTRUCTION_COMPLETED,
    PLAYER_NOTIFICATION,
    EventBus,
)
from mygame.world.systems.agent_system import (
    ABILITY_SCRIPT_KEYS,
    BUILDING_ROLE_MAP,
    GATED_BRANCH_ROLES,
    GATED_ROLE_FOR_BRANCH,
    UNGATED_BRANCH_ROLES,
    VALID_ROLES,
    AgentSystem,
)
from mygame.world.systems.branch_system import (
    MSG_BRANCH_SWITCH_BLOCKED,
    NOTIFY_BRANCH_DORMANCY,
    BranchSystem,
)
from mygame.world.systems.tech_system import TechLabSystem
from mygame.world.utils import building_is_operational

# ================================================================== #
#  Property 5
# ================================================================== #
#
# ``commitment`` resolves an owned lab through ``world.utils.owner_research_lab``
# and then reads that lab's DEFINITION; every reference below walks the drawn
# ``FakeBuilding`` roster and the fixture definition DICTS instead. That is what
# makes this a cross-check rather than a restatement — a filter added on
# ``offline``, a filter dropped on ``under_construction``, a planet scope lost, a
# reordered scan, or a cached answer each show up as a mismatch.
#
# The rule the reference states, and the whole rule, is **ownership of a
# completed lab**:
#
# - ``owner_research_lab`` filters on ``under_construction`` alone. It never
#   consults ``offline`` and never calls ``building_is_operational``, so an
#   offline, mid-upgrade, or hostile-suspended lab still commits its owner
#   (R3.9) — which is what makes suspending a lab withhold the lab's *function*
#   rather than the Branch's researched bonuses (R5.10, whose bonus half is
#   Property 7's). A half-built lab commits nobody.
# - A destroyed lab has left ``get_buildings()``, so its commitment leaves with
#   it and stays gone until a lab is completed there again (R3.8). Nothing
#   counts destructions.
# - Nothing is stored (R3.1), so the buildings ARE the record: a fresh system
#   over the same world answers identically (the restart half of R14.6) and a
#   changed building set changes the answer on the very next query, with no
#   invalidation call anywhere.
#
# The two layers are deliberately asked at every planet rather than only at the
# drawn one, because per-planet scoping (R3.7) is a claim about the *set* of
# answers and not about any single one.


#: The fixture building catalog keyed by abbreviation, so a reference can resolve
#: a placed building's ``building_type`` the way the system resolves it — through
#: a definition — without reaching into the registry the system was injected with.
_FIXTURE_ENTRY_BY_ABBR = {
    entry["abbreviation"]: entry for entry in FIXTURE_BUILDING_DICTS
}

#: The planets a query is aimed at: the three the roster spreads across, plus one
#: no building can stand on, so "no lab here" (R3.2) is drawn every example
#: rather than only when a roster happens to leave a planet empty.
_QUERY_PLANETS = FIXTURE_PLANETS + ("titan",)

#: Values ``has_commitment`` must answer ``False`` for instead of raising
#: (R15.3): a Branch outside the six, blank input, and non-strings.
_GARBAGE_BRANCHES = (None, "", "   ", "ZZ", "not_a_branch", 0, 17, [], {}, object())

#: Plain data, safe to deep-copy for a before/after comparison. Anything else is
#: a live object whose identity is the only thing stable enough to compare.
_PLAIN_TYPES = (str, bytes, bool, int, float, dict, list, tuple, set, frozenset)


class _ExplodingOwner:
    """An owner whose building roster cannot be read.

    The unreadable-world case R15.3 promises answers ``None`` rather than
    propagating: ``owner_research_lab`` swallows the failure, so a commitment
    query over a corrupt owner is "owns no lab".
    """

    def get_buildings(self):
        raise RuntimeError("roster on fire")


def _clean_field(value):
    """Return *value* as a non-empty stripped string, or ``None``.

    The normalization every ``BranchSystem`` identity answer passes through, so
    an absent field, a null field, a blank string, and a non-string all collapse
    to the same documented "no Branch" (R15.3).
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _entry_is_lab(entry) -> bool:
    """True when a building definition dict declares the ``research_lab`` capability.

    Capability, not abbreviation: that is the field ``owner_research_lab``
    filters on, so the two new labs are covered by declaring it and nothing
    else needs to know their names (R3.6).
    """
    return entry is not None and RESEARCH_LAB in (entry.get("capabilities") or ())


def _is_lab_type(building_type) -> bool:
    """True when *building_type* names a Branch_Lab in the fixture catalog."""
    return _entry_is_lab(_FIXTURE_ENTRY_BY_ABBR.get(building_type))


def _hosted_branch(building_type):
    """The Branch a Branch_Lab type hosts, or ``None``.

    ``research_tree`` (the Branch a lab hosts) wins and ``branch`` (the optional
    Branch_Affiliation) is the fallback, so a lab declaring only the affiliation
    still commits its owner — R2.4 makes the two equal whenever both are set.
    """
    entry = _FIXTURE_ENTRY_BY_ABBR.get(building_type)
    if entry is None:
        return None
    return (
        _clean_field(entry.get("research_tree"))
        or _clean_field(entry.get("branch"))
    )


def _planet_of(building):
    """The planet a placed building stands on, or ``None`` for "any planet".

    Mirrors :func:`world.utils._building_planet`: the room's ``planet_name``
    first, then the ``coord_planet`` attribute. ``None`` is the wildcard the
    ownership queries honour, so a building whose planet cannot be determined
    counts on every planet.
    """
    location = getattr(building, "location", None)
    if location is not None and getattr(location, "planet_name", None):
        return location.planet_name
    return building.attributes.get("coord_planet")


def _reference_lab(buildings, planet):
    """The FIRST owned completed Branch_Lab on *planet*, or ``None``.

    The reference for :func:`world.utils.owner_research_lab`, in its order:
    capability, then planet scope (``None`` planet meaning "any"), then
    ``under_construction``, first match wins. A half-built lab is *skipped*
    rather than terminal, so it neither commits its owner nor shadows a
    completed lab behind it.
    """
    for building in buildings:
        if not _is_lab_type(building.attributes.get("building_type")):
            continue
        if planet is not None and _planet_of(building) not in (None, planet):
            continue
        if building.attributes.get("under_construction"):
            continue
        return building
    return None


def _reference_commitment(buildings, planet):
    """The Branch *buildings* confer on *planet*, or ``None``.

    Two steps, in the implementation's own order: resolve the owned completed
    lab, then read the Branch that lab's definition hosts. Keeping them separate
    matters — a lab whose definition names no Branch answers ``None`` rather
    than falling through to the next lab.
    """
    lab = _reference_lab(buildings, planet)
    if lab is None:
        return None
    return _hosted_branch(lab.attributes.get("building_type"))


def _snapshot_value(value):
    """A comparable copy of one persisted attribute value."""
    if value is None or isinstance(value, _PLAIN_TYPES):
        return copy.deepcopy(value)
    return value


def _attr_snapshot(holder):
    """Every attribute on *holder*, copied so a later in-place write is visible."""
    return {
        key: _snapshot_value(value)
        for key, value in holder.attributes.all().items()
    }


def _world_snapshot(player):
    """Every attribute on *player* and on every building *player* owns.

    The whole persistent surface a commitment query could touch, so "the query
    writes nothing" (R3.1, R14.6) is checked against the world rather than
    against a list of attribute names this test would have to keep in sync.
    """
    return (
        _attr_snapshot(player),
        [_attr_snapshot(building) for building in player.get_buildings()],
    )


@st.composite
def _roster_st(draw):
    """A roster of owned buildings, some with no determinable planet.

    ``owned_buildings_st`` spreads its buildings across two or three named
    planets; a commitment query also has to answer for a building whose planet
    cannot be read, which counts on EVERY planet (R3.7). Blanking a minority of
    the planets keeps that case generated without making the wildcard the norm.
    """
    buildings = draw(owned_buildings_st)
    for building in buildings:
        if draw(st.sampled_from((False, False, False, True))):
            building.attributes.add("coord_planet", None)
    return buildings


# Feature: tech-tree-branch-foundation, Property 5: Branch_Commitment is the
# owned completed lab's Branch, independent of Operational state and of restarts
#
# **Validates: Requirements 3.1, 3.2, 3.7, 3.8, 3.9, 5.10, 14.6**
class TestProperty5CommitmentIsTheOwnedLabsBranch(unittest.TestCase):
    """A commitment is a query over owned buildings, and nothing else.

    Every clause is an assertion inside the one ``@given`` test below, because
    they are one claim seen from several sides: the answer equals the reference
    scan; it ignores the two flags that decide whether a building is
    *Operational* and follows the one flag that decides whether a building is
    *completed*; it survives a simulated restart; it tracks the building set
    with no invalidation call; and it writes nothing at all.

    The restart half is what a stored copy would fail: a second
    ``BranchSystem`` over a freshly loaded registry sees the same world and no
    handed-over state, which is exactly a server restart's starting position
    (R14.6).
    """

    def _assert_every_planet(self, system, player, buildings, message):
        """Assert *system* answers the reference on every planet, and return them.

        Returns the ``{planet: branch}`` mapping so a later clause can compare
        against the answers taken before it perturbed the world.
        """
        answers = {}
        for planet in _QUERY_PLANETS:
            answer = system.commitment(player, planet)
            self.assertEqual(
                answer, _reference_commitment(buildings, planet),
                f"{message}: commitment on '{planet}' disagreed with the scan "
                f"over the owned buildings",
            )
            self.assertIn(
                answer, (None,) + tuple(BRANCHES),
                f"{message}: commitment on '{planet}' answered {answer!r}, "
                f"which is neither one of the six Branches nor None",
            )
            answers[planet] = answer
        return answers

    @given(
        roster=_roster_st(),
        focus=st.sampled_from(_QUERY_PLANETS),
        occupied=st.one_of(st.none(), st.sampled_from(_QUERY_PLANETS)),
        incoming=branch_st,
    )
    @settings(max_examples=100)
    def test_commitment_is_the_owned_completed_labs_branch(
        self, roster, focus, occupied, incoming
    ):
        """**Validates: Requirements 3.1, 3.2, 3.7, 3.8, 3.9, 5.10, 14.6**"""
        system = BranchSystem(fixture_registry(), EventBus())
        player = FakePlayer(buildings=roster, planet=occupied)

        # --- Nothing is stored (R3.1, R14.6) ----------------------------- #
        # Taken FIRST, before any clause perturbs the world, so the only writes
        # it could catch are the system's own.
        before = _world_snapshot(player)
        answers = self._assert_every_planet(system, player, roster, "as drawn")
        for branch in BRANCHES:
            system.has_commitment(player, branch, focus)
        system.commitment(player)
        self.assertEqual(
            _world_snapshot(player), before,
            "a commitment query wrote to the world — the Branch_Commitment is "
            "derived from the owned buildings and holds no stored copy",
        )

        # The answer is a function of the world alone, so two consecutive asks
        # over an unchanged world agree. (A query that memoized its first answer
        # passes this and the clause above; the world-changes clauses below are
        # what catch it.)
        self.assertEqual(
            self._assert_every_planet(system, player, roster, "asked twice"),
            answers,
            "commitment is not stable between calls over an unchanged world",
        )

        # --- The default planet is the occupied one (R3.7) --------------- #
        self.assertEqual(
            system.commitment(player), _reference_commitment(roster, occupied),
            "commitment(player) did not scope itself to the planet the player "
            "occupies (db.coord_planet)",
        )
        if occupied is not None:
            self.assertEqual(
                system.commitment(player), answers[occupied],
                "commitment(player) and commitment(player, occupied_planet) "
                "disagreed",
            )

        # --- has_commitment is the same answer as a predicate ------------ #
        for planet in _QUERY_PLANETS:
            for branch in BRANCHES:
                self.assertEqual(
                    system.has_commitment(player, branch, planet),
                    answers[planet] == branch,
                    f"has_commitment('{branch}', '{planet}') disagreed with "
                    f"commitment, which answered {answers[planet]!r}",
                )
        for junk in _GARBAGE_BRANCHES:
            self.assertFalse(
                system.has_commitment(player, junk, focus),
                f"has_commitment answered True for {junk!r}, which is not one "
                f"of the six Branches",
            )

        # --- A restart changes nothing (R14.6) --------------------------- #
        # A second system over a registry loaded afresh from the same
        # definitions, handed no state by the first: a server restart's
        # starting position.
        restarted = BranchSystem(fixture_registry(), EventBus())
        self.assertEqual(
            self._assert_every_planet(restarted, player, roster, "after a restart"),
            answers,
            "a fresh BranchSystem over the same world answered differently, so "
            "some part of the commitment survives outside the buildings",
        )

        # --- Operational state is irrelevant (R3.9, R5.10) --------------- #
        # Both extremes of the two flags that decide whether a building is
        # Operational, with ``under_construction`` left alone. The lab is
        # verifiably NOT Operational at the first extreme, which is what keeps
        # this clause from passing vacuously.
        conferring = _reference_lab(roster, focus)
        for suspended in (True, False):
            for building in roster:
                building.attributes.add("offline", suspended)
                if suspended:
                    building.attributes.add(
                        "upgrade_target_level", building.building_level + 1
                    )
                else:
                    building.attributes.remove("upgrade_target_level")
            if suspended and conferring is not None:
                self.assertFalse(
                    building_is_operational(conferring),
                    "the guard failed: an offline, mid-upgrade lab is supposed "
                    "to be non-Operational, so this clause would prove nothing",
                )
            self.assertEqual(
                self._assert_every_planet(
                    system, player, roster,
                    f"with offline={suspended} on every building",
                ),
                answers,
                "commitment followed the Operational state — an offline, "
                "mid-upgrade, or suspended lab still commits its owner",
            )

        # --- Completion is the flag that decides, and the answer follows
        #     the world with no invalidation call (R3.2, R3.8, R14.6) ------ #
        # Each pass knocks the currently conferring lab back to half-built. A
        # cached answer, or a filter that stopped reading the flag, breaks here.
        while True:
            lab = _reference_lab(roster, focus)
            if lab is None:
                break
            lab.attributes.add("under_construction", True)
            self.assertEqual(
                system.commitment(player, focus),
                _reference_commitment(roster, focus),
                "commitment did not follow a lab becoming half-built on the "
                "very next query",
            )
        self.assertIsNone(
            system.commitment(player, focus),
            f"'{focus}' holds no completed lab, yet a commitment was reported "
            f"there",
        )
        for branch in BRANCHES:
            self.assertFalse(
                system.has_commitment(player, branch, focus),
                f"has_commitment('{branch}') answered True on a planet holding "
                f"no completed Branch_Lab",
            )

        # A lab completed there again commits its owner immediately (R3.8),
        # first in the roster so its Branch is the one the scan reaches first.
        rebuilt = FakeBuilding(
            building_type=FIXTURE_LAB_ABBR[incoming], planet=focus
        )
        player.set_buildings([rebuilt] + list(roster))
        self.assertEqual(
            system.commitment(player, focus), incoming,
            "a completed Branch_Lab did not confer its Branch on the next query",
        )
        self.assertTrue(
            system.has_commitment(player, incoming, focus),
            "has_commitment disagreed with a commitment just re-established",
        )
        self.assertEqual(
            restarted.commitment(player, focus), incoming,
            "a fresh BranchSystem disagreed about a commitment the world had "
            "just gained",
        )

        # Destroying it takes the commitment with it: a destroyed building has
        # left get_buildings(), and nothing counts destructions (R3.8).
        player.set_buildings(
            [b for b in player.get_buildings() if b is not rebuilt]
        )
        self.assertIsNone(
            system.commitment(player, focus),
            "a destroyed Branch_Lab left its commitment behind",
        )

        # --- An unresolvable input answers None, never raises (R15.3) ---- #
        for unreadable in (None, object(), SimpleNamespace(), _ExplodingOwner()):
            self.assertIsNone(
                system.commitment(unreadable),
                f"commitment({unreadable!r}) did not answer None",
            )
            self.assertFalse(
                system.has_commitment(unreadable, incoming),
                f"has_commitment({unreadable!r}) did not answer False",
            )


# ================================================================== #
#  Property 6
# ================================================================== #
#
# Two claims joined at one number. The first is about the ESTATE: membership
# equals a reference scan over the drawn roster — planet-scoped, the Branch's
# own lab counted in, a building still under construction counted in. The
# second is about the SWITCH REPORT: the gate refuses exactly when some other
# Branch's estate on the target planet is non-empty, and the refusal quotes
# that estate's count and its members' abbreviations and coordinates.
#
# Joining them is the point, because either half alone can pass while the pair
# is wrong. An estate query that quietly dropped half-built buildings would
# still agree with a refusal computed from that same dropped-filter query, and
# the player would be told a switch is clear while the build keeps failing. So
# the reference derives the estate from the ROSTER and the fixture definition
# DICTS, and the refusal is checked against *that* — never against the system's
# own estate answer.
#
# What the reference deliberately does not do:
#
# - It applies no completion filter and no Operational filter (R4.7). That is
#   the one place an estate and a commitment part ways, and the clause that
#   flips every flag asserts they part ways exactly there: with the whole
#   roster half-built the estates are untouched while the commitment is gone.
# - It counts no destructions (R4.6). A building removed from the roster is
#   simply absent from the next scan, which is why a demolition and a hostile
#   razing are one event here, and why an emptied estate frees the planet
#   (R4.3) with nothing to reset.
# - It reads the fixture dicts rather than the injected registry, so a
#   membership rule read out of the wrong field is a mismatch rather than a
#   shared mistake.
#
# The gate is taken out of ``construction_validators()`` rather than reached
# for by its private name, so what gets measured is the callable
# ``BuildingSystem`` actually splices into its chain. Nothing here charges
# anything: the resource snapshot taken before the first gate call and compared
# after the last is the half of "reports before charging any resources" (R13.4)
# that a test of this gate alone can state — the gate's *position* above
# ``_validate_resources`` is the splice's claim, not this one's.


#: Index of ``_validate_branch_switch`` among the three gates
#: ``construction_validators()`` returns, in the design's chain order
#: (affiliation, switch, unlock). Named rather than inlined so a reordering
#: reads as the contract change it would be.
_SWITCH_GATE_INDEX = 1

#: Fixture technology key -> the Branch that hosts it, so the dormancy figures
#: in the switch report have a reference that needs no registry read.
_FIXTURE_BRANCH_OF_TECH = {
    key: branch
    for branch, keys in FIXTURE_TECH_KEYS_BY_BRANCH.items()
    for key in keys
}


class _FakeTile:
    """A target tile that answers one planet and nothing else.

    ``BranchSystem._target_planet`` reads a tile's planet from its tags, then
    its ``db``, then ``planet_name``; a tile carrying only ``db.coord_planet``
    is the smallest thing that resolves. Using one keeps the gate's planet
    independent of the planet the player happens to occupy, which is what lets
    a single example aim the same request at four different planets.
    """

    def __init__(self, planet):
        self.db = SimpleNamespace(coord_planet=planet)


def _notification_sink(bus):
    """Subscribe to ``PLAYER_NOTIFICATION`` on *bus*; return the captured list.

    The sink only appends: :meth:`EventBus.publish` logs and swallows a
    subscriber's exception, so an assertion made inside one would vanish
    instead of failing the test.
    """
    notes = []
    bus.subscribe(
        PLAYER_NOTIFICATION,
        lambda event_name="", player=None, kind="", data=None, **_kw: notes.append(
            (player, kind, dict(data or {}))
        ),
    )
    return notes


def _affiliated_branch(building_type):
    """The Branch a building type BELONGS to, or ``None``.

    The estate's own resolution order, and the mirror of
    :func:`_hosted_branch`'s: the Branch_Affiliation (``branch``) wins, and the
    Branch a lab hosts (``research_tree``) is the fallback — so a Branch_Lab is
    a member of its own Branch's estate even when it declares no affiliation.
    R2.4 requires the two fields to agree whenever both are set, which is why
    the two orders answer alike over any valid catalog; stating the estate's
    order separately keeps it readable next to the commitment's rather than
    borrowing one for the other.
    """
    entry = _FIXTURE_ENTRY_BY_ABBR.get(building_type)
    if entry is None:
        return None
    return (
        _clean_field(entry.get("branch"))
        or _clean_field(entry.get("research_tree"))
    )


def _fixture_name(building):
    """The display name a placed building's definition carries, or ``None``."""
    entry = _FIXTURE_ENTRY_BY_ABBR.get(building.attributes.get("building_type"))
    if entry is None:
        return None
    return entry.get("name")


def _reference_estate(buildings, branch, planet):
    """The members of *branch*'s estate on *planet*, in the owner's own order.

    The whole membership rule, and nothing else: planet scope with ``None``
    meaning "any planet" on both sides, then the building's Branch. No
    ``under_construction`` filter and no ``offline`` filter — a half-built or
    suspended Branch_Building is a member (R4.7) — and no destruction
    bookkeeping, because a razed building is simply not in *buildings* (R4.6).
    """
    return [
        building
        for building in buildings
        if (planet is None or _planet_of(building) in (None, planet))
        and _affiliated_branch(building.attributes.get("building_type")) == branch
    ]


def _reference_conflicts(buildings, planet, incoming):
    """The non-empty estates on *planet* that block committing to *incoming*.

    Every Branch but the incoming one, in canonical Branch order, holding only
    the Branches with at least one member — so the mapping is falsy exactly
    when the switch is clear (R4.3). The incoming Branch is excluded because a
    player's own incoming estate never blocks the player.
    """
    conflicts = {}
    for branch in BRANCHES:
        if branch == incoming:
            continue
        members = _reference_estate(buildings, branch, planet)
        if members:
            conflicts[branch] = members
    return conflicts


def _reference_blocking(conflicts):
    """The ``(branch, abbr, name, x, y)`` tuples a refusal must report (R4.2).

    Sorted, so the comparison is one-to-one over the estate members without
    pinning an order the design does not state. The coordinates are what make
    it checkable member by member rather than only in aggregate: the roster
    generator gives every building a distinct pair, so two buildings of the
    same type in the same estate are still distinguishable.
    """
    return sorted(
        (
            branch,
            member.attributes.get("building_type"),
            _fixture_name(member),
            member.attributes.get("coord_x"),
            member.attributes.get("coord_y"),
        )
        for branch, members in conflicts.items()
        for member in members
    )


def _reported_blocking(refusal):
    """The same tuples, read off the refusal's structured payload."""
    return sorted(
        (
            entry.get("branch"),
            entry.get("building"),
            entry.get("building_name"),
            entry.get("x"),
            entry.get("y"),
        )
        for entry in refusal.data["blocking"]
    )


def _reference_dormant(researched, incoming):
    """*researched* grouped by Branch, excluding *incoming* (R4.8, R13.4).

    The figures the pre-charge report quotes: every recorded technology outside
    the incoming Branch is inert under the incoming commitment. Canonical
    Branch order, each list sorted, and only the Branches the player has a
    record in — so the mapping is falsy exactly when committing costs the
    player no bonuses.
    """
    grouped = {}
    for key in researched:
        branch = _FIXTURE_BRANCH_OF_TECH.get(key)
        if branch is None or branch == incoming:
            continue
        grouped.setdefault(branch, []).append(key)
    return {
        branch: sorted(grouped[branch])
        for branch in BRANCHES
        if branch in grouped
    }


# Feature: tech-tree-branch-foundation, Property 6: Branch_Estate membership
# equals the reference scan, and the switch report equals the estate
#
# **Validates: Requirements 4.1, 4.2, 4.3, 4.5, 4.7, 13.4**
class TestProperty6EstateMembershipAndTheSwitchReport(unittest.TestCase):
    """The estate is a scan over owned buildings, and the refusal quotes it.

    One ``@given`` test again, because the clauses are one claim seen from
    several sides: membership equals the scan on every planet for every Branch;
    the same answer survives a restart and follows the roster with no
    invalidation call; the gate refuses exactly while a conflicting estate
    stands and reports exactly its members; a half-built building is a member
    that blocks a switch while conferring no commitment; and emptying the
    estate — by demolition or by destruction, which are the same event to a
    query — frees the planet, one building at a time.
    """

    def _assert_every_estate(self, system, player, buildings, incoming, message):
        """Assert the three estate queries answer the reference everywhere.

        Every planet and every Branch rather than only the drawn pair, because
        per-planet scoping is a claim about the *set* of answers. Returns the
        ``{(planet, branch): members}`` mapping so a later clause can compare
        against the answers taken before it perturbed the world.
        """
        answers = {}
        for planet in _QUERY_PLANETS:
            for branch in BRANCHES:
                members = system.estate(player, branch, planet)
                expected = _reference_estate(buildings, branch, planet)
                self.assertEqual(
                    members, expected,
                    f"{message}: estate('{branch}', '{planet}') disagreed with "
                    f"the scan over the owned buildings",
                )
                self.assertEqual(
                    system.estate_count(player, branch, planet), len(expected),
                    f"{message}: estate_count('{branch}', '{planet}') disagreed "
                    f"with the number of members estate reported",
                )
                answers[(planet, branch)] = list(members)
            conflicts = system.conflicting_estates(player, planet, incoming)
            self.assertEqual(
                conflicts, _reference_conflicts(buildings, planet, incoming),
                f"{message}: the estates blocking a '{incoming}' lab on "
                f"'{planet}' disagreed with the scan",
            )
            self.assertEqual(
                list(conflicts), [b for b in BRANCHES if b in conflicts],
                f"{message}: the blocking estates were not reported in "
                f"canonical Branch order",
            )
            self.assertNotIn(
                incoming, conflicts,
                f"{message}: the incoming Branch's own estate was reported as "
                f"blocking the incoming Branch",
            )
        return answers

    def _assert_report(self, data, roster, focus, incoming, researched, message):
        """Assert one switch report carries the pre-charge figures (R4.8, R13.4).

        Shared by both outcomes on purpose: the refusal payload and the
        dormancy notification carry the same report, so a player learns what a
        switch costs whether or not something is still standing in the way.
        """
        self.assertEqual(
            data.get("planet"), focus,
            f"{message}: the report named a planet other than the target tile's",
        )
        self.assertEqual(
            data.get("incoming_branch"), incoming,
            f"{message}: the report named the wrong incoming Branch",
        )
        self.assertEqual(
            data.get("lab"), FIXTURE_LAB_ABBR[incoming],
            f"{message}: the report named the wrong lab",
        )
        outgoing = _reference_commitment(roster, focus)
        self.assertEqual(
            data.get("outgoing_branch"),
            outgoing if outgoing != incoming else None,
            f"{message}: the outgoing Branch disagreed with the commitment the "
            f"owned buildings confer on '{focus}'",
        )
        dormant = _reference_dormant(researched, incoming)
        self.assertEqual(
            data.get("dormant_count"), sum(len(keys) for keys in dormant.values()),
            f"{message}: the count of technologies that would go dormant "
            f"disagreed with the player's record outside '{incoming}'",
        )
        self.assertEqual(
            data.get("dormant_counts"),
            {branch: len(keys) for branch, keys in dormant.items()},
            f"{message}: the per-Branch dormancy counts disagreed with the "
            f"player's record",
        )
        self.assertEqual(
            data.get("dormant_technologies"),
            {branch: list(keys) for branch, keys in dormant.items()},
            f"{message}: the reported dormant technologies disagreed with the "
            f"player's record",
        )

    @given(
        roster=_roster_st(),
        focus=st.sampled_from(_QUERY_PLANETS),
        occupied=st.one_of(st.none(), st.sampled_from(_QUERY_PLANETS)),
        incoming=branch_st,
        offset=st.integers(min_value=0, max_value=len(BRANCHES) - 2),
        researched=researched_set_st,
    )
    @settings(max_examples=100)
    def test_estate_membership_and_the_switch_report(
        self, roster, focus, occupied, incoming, offset, researched
    ):
        """**Validates: Requirements 4.1, 4.2, 4.3, 4.5, 4.7, 13.4**"""
        bus = EventBus()
        notes = _notification_sink(bus)
        system = BranchSystem(fixture_registry(), bus)
        player = FakePlayer(
            buildings=roster, planet=occupied, resources={"Iron": 500},
        )
        player.db.researched_techs = set(researched)
        gates = system.construction_validators()
        self.assertEqual(
            len(gates), 3,
            "construction_validators() no longer returns the three Branch "
            "gates, so the switch gate cannot be identified by position",
        )
        gate = gates[_SWITCH_GATE_INDEX]
        tile = _FakeTile(focus)
        lab_abbr = FIXTURE_LAB_ABBR[incoming]

        # --- Membership equals the scan, and nothing is stored (R14.6) --- #
        before = _world_snapshot(player)
        resources_before = player.resource_snapshot()
        answers = self._assert_every_estate(
            system, player, roster, incoming, "as drawn"
        )
        self.assertEqual(
            _world_snapshot(player), before,
            "an estate query wrote to the world — a Branch_Estate is derived "
            "from the owned buildings and holds no stored copy",
        )
        self.assertEqual(
            self._assert_every_estate(
                system, player, roster, incoming, "asked twice"
            ),
            answers,
            "the estate is not stable between calls over an unchanged world",
        )

        # --- The default planet is the occupied one ---------------------- #
        for branch in BRANCHES:
            self.assertEqual(
                system.estate(player, branch),
                _reference_estate(roster, branch, occupied),
                f"estate('{branch}') did not scope itself to the planet the "
                f"player occupies (db.coord_planet)",
            )

        # --- A restart changes nothing (R14.6) -------------------------- #
        restarted = BranchSystem(fixture_registry(), EventBus())
        self.assertEqual(
            self._assert_every_estate(
                restarted, player, roster, incoming, "after a restart"
            ),
            answers,
            "a fresh BranchSystem over the same world reported a different "
            "estate, so some part of it survives outside the buildings",
        )

        # --- An unresolvable input answers the documented empty (R15.3) -- #
        for junk in _GARBAGE_BRANCHES:
            self.assertEqual(
                system.estate(player, junk, focus), [],
                f"estate({junk!r}) did not answer an empty list",
            )
            self.assertEqual(
                system.estate_count(player, junk, focus), 0,
                f"estate_count({junk!r}) did not answer 0",
            )
            self.assertEqual(
                system.conflicting_estates(player, focus, junk), {},
                f"conflicting_estates(incoming={junk!r}) did not answer {{}}",
            )
        for unreadable in (None, object(), SimpleNamespace(), _ExplodingOwner()):
            self.assertEqual(
                system.estate(unreadable, incoming, focus), [],
                f"estate over {unreadable!r} did not answer an empty list",
            )
            self.assertEqual(
                system.conflicting_estates(unreadable, focus, incoming), {},
                f"conflicting_estates over {unreadable!r} did not answer {{}}",
            )

        # --- The gate refuses exactly while an estate stands (R4.1, R4.3)  #
        conflicts = _reference_conflicts(roster, focus, incoming)
        notes.clear()
        gate_before = _world_snapshot(player)
        refusal = gate(player, lab_abbr, tile)
        self.assertEqual(
            refusal is None, not conflicts,
            f"a '{incoming}' lab request on '{focus}' was "
            f"{'refused' if refusal is not None else 'permitted'} while the "
            f"conflicting estates held {conflicts!r}",
        )
        self.assertEqual(
            _world_snapshot(player), gate_before,
            "the switch gate wrote to the world — it reports and refuses, and "
            "changes nothing",
        )
        if conflicts:
            # R4.1: the count. R4.2: each blocking building, by abbreviation
            # and coordinates. Both quoted from the estate, not from a
            # separate tally.
            self.assertEqual(
                str(refusal), MSG_BRANCH_SWITCH_BLOCKED,
                "the refusal did not answer the branch_switch_blocked message "
                "key",
            )
            blocking = _reference_blocking(conflicts)
            self.assertEqual(
                refusal.data["count"], len(blocking),
                "the refused count did not equal the number of buildings "
                "remaining in the conflicting estates",
            )
            self.assertEqual(
                list(refusal.data["branches"]), list(conflicts),
                "the refusal named the wrong blocking Branches, or named them "
                "out of canonical order",
            )
            self.assertEqual(
                refusal.data["counts"],
                {branch: len(members) for branch, members in conflicts.items()},
                "the per-Branch blocking counts disagreed with the estates",
            )
            for branch in conflicts:
                self.assertEqual(
                    refusal.data["counts"][branch],
                    system.estate_count(player, branch, focus),
                    f"the refusal's count for '{branch}' disagreed with "
                    f"estate_count, so the number a player is told and the "
                    f"number a demolish reports could diverge",
                )
            self.assertEqual(
                _reported_blocking(refusal), blocking,
                "the reported blocking buildings did not correspond one-to-one "
                "to the conflicting estates' members, by abbreviation and "
                "coordinates",
            )
            self._assert_report(
                refusal.data, roster, focus, incoming, researched, "the refusal"
            )
            self.assertEqual(
                notes, [],
                "a refused switch also published a dormancy notification — the "
                "refusal already carries the figures",
            )
        else:
            dormant_total = sum(
                len(keys)
                for keys in _reference_dormant(researched, incoming).values()
            )
            kinds = [kind for _player, kind, _data in notes]
            if dormant_total:
                # R4.8 / R13.4: the report a player gets before any charge.
                self.assertEqual(
                    kinds, [NOTIFY_BRANCH_DORMANCY],
                    f"a permitted switch abandoning {dormant_total} recorded "
                    f"technologies published {kinds!r} instead of one dormancy "
                    f"report",
                )
                self.assertIs(
                    notes[0][0], player,
                    "the dormancy report went to somebody other than the "
                    "requesting player",
                )
                self._assert_report(
                    notes[0][2], roster, focus, incoming, researched,
                    "the dormancy notification",
                )
            else:
                self.assertEqual(
                    kinds, [],
                    f"a switch costing the player no recorded technologies "
                    f"still published {kinds!r}",
                )

        # This gate is the Branch_Lab's question alone: a Branch_Building
        # request passes it untouched even while an estate conflicts, because
        # the affiliation gate is what answers for that one.
        self.assertIsNone(
            gate(player, FIXTURE_BRANCH_BUILDING_ABBR[incoming], tile),
            "the switch gate refused a non-lab building",
        )

        # --- A member need not be Operational, or finished (R4.7) -------- #
        # Every flag flipped at once: the estates must not move, and the
        # commitment must vanish. That divergence is the requirement — a
        # half-built building blocks a switch while conferring nothing.
        notes.clear()
        for building in roster:
            building.attributes.add("under_construction", True)
            building.attributes.add("offline", True)
        if roster:
            self.assertFalse(
                building_is_operational(roster[0]),
                "the guard failed: an offline, half-built building is supposed "
                "to be non-Operational, so this clause would prove nothing",
            )
        self.assertIsNone(
            system.commitment(player, focus),
            "a roster of half-built labs still conferred a commitment",
        )
        self.assertEqual(
            self._assert_every_estate(
                system, player, roster, incoming, "with every building unfinished"
            ),
            answers,
            "the estate followed the completion or Operational flags — a "
            "building under construction is a member (R4.7)",
        )
        unfinished = gate(player, lab_abbr, tile)
        self.assertEqual(
            unfinished is None, not conflicts,
            "the switch gate changed its answer once every building was "
            "unfinished, so a player could switch out from under a half-built "
            "estate",
        )
        if conflicts:
            self.assertEqual(
                _reported_blocking(unfinished), _reference_blocking(conflicts),
                "the refusal stopped reporting the unfinished members of the "
                "conflicting estates",
            )

        # --- A fresh half-built blocker joins the estate and blocks ------ #
        # Generated rather than hoped for: the drawn roster may hold no
        # conflicting building at all, and the clause above would then be
        # vacuous. This one always adds exactly one, of another Branch, on the
        # target planet, unfinished, at coordinates nothing else occupies.
        others = [branch for branch in BRANCHES if branch != incoming]
        blocker_branch = others[offset % len(others)]
        blocker_abbr = FIXTURE_BRANCH_BUILDING_ABBR[blocker_branch]
        blocker = FakeBuilding(
            building_type=blocker_abbr, planet=focus,
            under_construction=True, x=97, y=41,
        )
        current = list(roster) + [blocker]
        player.set_buildings(current)
        self.assertFalse(
            building_is_operational(blocker),
            "the guard failed: a half-built building is supposed to be "
            "non-Operational",
        )
        self.assertIn(
            blocker, system.estate(player, blocker_branch, focus),
            f"a half-built '{blocker_branch}' building was left out of its own "
            f"Branch_Estate (R4.7)",
        )
        blocked = gate(player, lab_abbr, tile)
        self.assertEqual(
            str(blocked), MSG_BRANCH_SWITCH_BLOCKED,
            f"a '{incoming}' lab was permitted while a half-built "
            f"'{blocker_branch}' building stood on '{focus}'",
        )
        self.assertEqual(
            _reported_blocking(blocked),
            _reference_blocking(_reference_conflicts(current, focus, incoming)),
            "the refusal did not report the half-built blocker with its own "
            "abbreviation and coordinates",
        )

        # --- Emptying the estate frees the planet, one building at a time  #
        # A removal is a demolition or a hostile razing indifferently — a
        # query counts neither (R4.6) — so this loop is both, and the count it
        # checks after each step is the one the demolish path quotes (R4.5).
        while True:
            standing = _reference_conflicts(current, focus, incoming)
            if not standing:
                break
            refused = gate(player, lab_abbr, tile)
            self.assertEqual(
                str(refused), MSG_BRANCH_SWITCH_BLOCKED,
                f"a '{incoming}' lab was permitted while {standing!r} still "
                f"stood on '{focus}'",
            )
            self.assertEqual(
                refused.data["count"],
                sum(len(members) for members in standing.values()),
                "the refused count did not follow the estates shrinking",
            )
            victim_branch, members = next(iter(standing.items()))
            victim = members[0]
            was = system.estate_count(player, victim_branch, focus)
            current = [b for b in current if b is not victim]
            player.set_buildings(current)
            self.assertEqual(
                system.estate_count(player, victim_branch, focus), was - 1,
                f"removing one '{victim_branch}' building did not shrink that "
                f"Branch_Estate by exactly one on the very next query (R4.5)",
            )
        self.assertIsNone(
            gate(player, lab_abbr, tile),
            f"'{focus}' holds no conflicting estate, yet a '{incoming}' lab "
            f"was still refused (R4.3)",
        )

        # --- Not one resource charged anywhere above (R13.4) ------------- #
        self.assertEqual(
            player.resource_snapshot(), resources_before,
            "the switch gate charged the player — the report and the refusal "
            "both precede any charge",
        )


# ================================================================== #
#  Property 12
# ================================================================== #
#
# Two claims over one table, ``GATED_BRANCH_ROLES``, read in the two directions
# the game reads it: role -> the Branch that must be live to assign it (R7.6,
# R7.7), and Branch -> the one role a lapse releases (R7.8).
#
# The gate half is measured against a resolver answering a DRAWN commitment
# rather than one derived from buildings, for two reasons. It is the only way to
# put one Branch on the agent's planet and another on its owner's and then
# assert which of the two the gate read — the planet that counts is the AGENT's,
# because an agent serves where it stands. And it keeps the claim about the
# gate: a refusal computed from a wrong commitment is Property 5's failure, not
# this one's. A later clause then wires the REAL ``BranchSystem`` over a lab
# roster and re-runs the same sweep, so the two are also checked to agree at the
# seam the composition root builds.
#
# What the reference deliberately does not gate:
#
# - Anything outside ``GATED_BRANCH_ROLES``. Every pre-feature role and `scout`
#   must pass under any commitment, including none — `scout` because gating it
#   would stop patrols that work today (the decided asymmetry recorded in
#   ``UNGATED_BRANCH_ROLES``), the rest because they never belonged to a Branch.
#   The sweep asserts the resolver is not even CONSULTED for them, which is the
#   difference between "not gated" and "gated, and happens to pass".
# - An unwired resolver, or one exposing no ``commitment``. Either is no gate at
#   all rather than a closed one: an assignment path must not become unusable
#   because a collaborator is absent (R15.3).
#
# The lapse half is exactness in five directions at once, which is why it is
# stated over a generated roster rather than a handful of cases: same-Branch
# agents on other planets, other Branches' agents on the same planet, another
# player's agents, `scout`s, and the Branch-free roles all have to come through
# a lapse untouched, while the gated role of that Branch on that planet goes
# back to unassigned THROUGH the existing teardown — behavior script detached,
# ``role_target`` cleared, the building's slot released — and not merely by
# having its role string blanked. Comparing a snapshot of the WHOLE roster is
# what states "untouched" without a list of cases this test would have to keep
# in sync, and the drawn roster may hold no releasable agent at all, so a final
# clause builds one that always does.


#: The planets an agent may stand on: the three the fixture roster spreads
#: across, plus ``None`` for an agent whose planet cannot be read — which the
#: gate passes straight through, so the resolver reads it as "the planet the
#: player occupies", the same fallback every other commitment read gets.
_AGENT_PLANETS = FIXTURE_PLANETS + (None,)

#: The player-facing roles the gate covers. Split out of ``VALID_ROLES`` rather
#: than listed, so a role added to the table is swept without editing this test.
_GATED_ROLES = tuple(role for role in VALID_ROLES if role in GATED_BRANCH_ROLES)

#: The rest of ``VALID_ROLES``: `scout` plus every pre-feature role. The gate
#: must never fire for any of them.
_FREE_ROLES = tuple(
    role for role in VALID_ROLES if role not in GATED_BRANCH_ROLES
)

#: Roles a roster entry may hold: the six Branch roles, two Branch-free ones,
#: and the unassigned state, so a lapse is asked about all three kinds at once.
_ROSTER_ROLES = FIXTURE_AGENT_ROLES + ("",)

#: Role -> a building abbreviation that role may be stationed at, for the roles
#: that require a target building. Inverted from ``BUILDING_ROLE_MAP`` — read
#: back to front so the map's FIRST abbreviation for a role wins — so the sweep
#: needs no second copy of the building/role pairing. A role absent from it is
#: an army role, assigned with no building at all.
_BUILDING_FOR_ROLE: dict[str, str] = {
    role: abbr for abbr, role in reversed(tuple(BUILDING_ROLE_MAP.items()))
}

#: Script class name -> the Evennia key that script registers under. The role
#: and ability tables' own binding, so a fake handler records the key a real
#: attach would and the detach's key match is a real match.
_SCRIPT_KEY_BY_CLASS_NAME: dict[str, str] = {
    spec.script.__name__: spec.script_key for spec in AGENT_ROLES.values()
}
_SCRIPT_KEY_BY_CLASS_NAME.update(ABILITY_SCRIPT_KEYS)

#: Spellings of a Branch name a lapse must read alike: the release normalizes
#: its argument, so the same set of agents is released however the trigger
#: spelled the Branch it lost.
_SPELLINGS = {
    "exact": lambda branch: branch,
    "upper": lambda branch: branch.upper(),
    "title": lambda branch: branch.title(),
    "padded": lambda branch: f"  {branch}  ",
}

#: A roster of ``(mine, planet, role, stationed)`` entries — the design's
#: "roster spread across planets with mixed roles", split between two owners.
#: ``stationed`` writes a ``role_target`` whatever the role is, including on the
#: army roles that normally carry none: the teardown has to clear the field it
#: finds rather than the field the role is supposed to have. The list is capped
#: small on purpose — the claim is about *exactness*, which a handful of agents
#: states as well as a hundred and generates far faster.
_agent_roster_st = st.lists(
    st.tuples(
        st.booleans(),
        st.sampled_from(_AGENT_PLANETS),
        st.sampled_from(_ROSTER_ROLES),
        st.booleans(),
    ),
    max_size=6,
)


class _FakeScript:
    """The slice of an attached Evennia Script the detach helper touches."""

    def __init__(self, key):
        self.key = key
        self.deleted = False

    def delete(self):
        self.deleted = True


class _FakeScriptHandler:
    """The slice of Evennia's ScriptHandler the attach/detach helpers drive.

    An agent without a ``scripts`` handler makes ``_detach_behavior_script``
    return early, which would leave the teardown clause asserting nothing — so
    the fake exists precisely to make that clause bite. ``add`` resolves the
    class it is handed to the key that script registers under, the same way the
    detach side resolves it, so a key match is a real match.
    """

    def __init__(self, keys=()):
        self._scripts = [_FakeScript(key) for key in keys]

    def all(self):
        return [script for script in self._scripts if not script.deleted]

    def add(self, script_cls, **_kwargs):
        name = getattr(script_cls, "__name__", "")
        self._scripts.append(
            _FakeScript(_SCRIPT_KEY_BY_CLASS_NAME.get(name, name))
        )

    def keys(self):
        return tuple(sorted(script.key for script in self.all()))


class _FakeAgent:
    """A framework-free agent NPC: a ``db`` handler and a scripts handler.

    Built on ``branch_strategies``' attribute fakes, so the persistence surface
    is the one every other Branch property drives.
    """

    def __init__(self, agent_id, owner, planet=None, role="", scripts=()):
        self.key = f"Agent #{agent_id}"
        self.attributes = FakeAttributes({
            "agent_id": agent_id,
            "owner": owner,
            "npc_type": "agent",
            "role": role,
            "role_target": None,
            "coord_planet": planet,
        })
        self.db = FakeDB(self.attributes)
        self.scripts = _FakeScriptHandler(scripts)

    def __repr__(self):  # pragma: no cover - diagnostics only
        return (
            f"_FakeAgent(#{self.db.agent_id}, role={self.db.role!r}, "
            f"planet={self.db.coord_planet!r})"
        )


class _FakeAgentRepository:
    """The ``AgentRepository`` port over an in-memory roster.

    Injected rather than defaulted, so no example needs an Evennia database —
    and ownership is compared by identity, which is what makes "another
    player's agents are untouched" a claim about the roster query too.
    """

    def __init__(self, agents):
        self._agents = agents

    def find_agents_for_owner(self, owner):
        return [agent for agent in self._agents if agent.db.owner is owner]

    def find_all_agents(self):
        return list(self._agents)

    def find_all_enemies(self):
        return []

    def find_training_buildings(self):
        return []


class _PlanetResolver:
    """A Branch resolver answering a per-planet commitment, recording each ask.

    Exactly the surface ``AgentSystem`` uses (``commitment(player, planet)``),
    answering a drawn value rather than deriving one. The recorded planets are
    what turn "the gate read the agent's planet" from an inference into an
    assertion.
    """

    def __init__(self, by_planet):
        self._by_planet = dict(by_planet)
        self.planets_asked: list = []

    def commitment(self, player, planet=None):
        self.planets_asked.append(planet)
        return self._by_planet.get(planet)


class _AgentWorld:
    """An ``AgentSystem`` over an in-memory roster, with no framework at all.

    Owns the agent-id bookkeeping because agent ids are per-owner: two players
    each numbering from 1 is the shape the release has to tell apart.
    """

    def __init__(self, registry, resolver=None):
        self.agents: list = []
        self.system = AgentSystem(
            registry=registry,
            event_bus=EventBus(),
            agent_repository=_FakeAgentRepository(self.agents),
        )
        self.system.set_branch_resolver(resolver)
        self._next_id: dict = {}

    def agent(self, owner, planet=None, role="", stationed=False):
        """Create, register, and return one agent already holding *role*.

        An agent in a role carries the behavior script that role registers, and
        a *stationed* one is also held by a building's ``assigned_agent`` — the
        two pieces of state ``unassign_agent`` tears down, so a release that
        took a shortcut around that path leaves one of them behind.
        """
        agent_id = self._next_id.get(id(owner), 0) + 1
        self._next_id[id(owner)] = agent_id
        spec = AGENT_ROLES.get(role)
        agent = _FakeAgent(
            agent_id, owner, planet=planet, role=role,
            scripts=(spec.script_key,) if spec is not None else (),
        )
        if stationed and role:
            station = FakeBuilding(building_type="EX", planet=planet)
            station.attributes.add("assigned_agent", agent)
            agent.db.role_target = station
        self.agents.append(agent)
        return agent


def _reference_required_branch(role, commitment):
    """The Branch a refusal must name, or ``None`` when *role* is permitted.

    The whole gate rule (R7.6, R7.7): a role in ``GATED_BRANCH_ROLES`` needs the
    commitment to BE that role's Branch, and everything else — every
    pre-feature role, and `scout` — needs nothing at all. A commitment of
    ``None`` matches no Branch, so a player committed to nothing commands none
    of the gated roles.
    """
    required = GATED_BRANCH_ROLES.get(role)
    if required is None or required == commitment:
        return None
    return required


def _reference_released(agents, owner, planet, branch):
    """The agents a lapse of *branch* on *planet* must release, and only those.

    The membership rule, in the release's own order: the one gated role that
    Branch commands, that owner's agents alone, standing on that planet alone.
    A Branch outside the five gated ones — including `research`, whose `scout`
    is exempt — releases nobody, and neither does a blank or non-string one
    (R15.3). ``None`` for *planet* falls back to the planet the owner occupies,
    the same way every other unscoped commitment read resolves.
    """
    wanted = (_clean_field(branch) or "").lower()
    role = GATED_ROLE_FOR_BRANCH.get(wanted)
    if role is None:
        return []
    if planet is None:
        planet = owner.db.coord_planet
    return [
        agent
        for agent in agents
        if agent.db.owner is owner
        and (agent.db.role or "").lower() == role
        and agent.db.coord_planet == planet
    ]


def _agent_snapshot(agent):
    """The agent state a release either clears or must leave exactly alone."""
    return (
        agent.db.role,
        agent.db.role_target,
        agent.db.coord_planet,
        agent.scripts.keys(),
    )


def _roster_snapshot(agents):
    """Every agent's state, keyed by identity, for an "untouched" comparison."""
    return {id(agent): _agent_snapshot(agent) for agent in agents}


# Feature: tech-tree-branch-foundation, Property 12: The Branch role gate
# permits exactly matching commitments, and a lapse clears exactly the gated
# roles on that planet
#
# **Validates: Requirements 7.6, 7.7, 7.8**
class TestProperty12RoleGateAndDormancyRelease(unittest.TestCase):
    """A Branch commands exactly its own role, exactly where it is live.

    One ``@given`` test, because the clauses are one claim seen from both ends
    of the same table: assignment opens exactly on a matching commitment and
    the refusal names the Branch required; the commitment read is the one on
    the AGENT's planet; the roles outside the gate never consult the resolver at
    all; an absent resolver is no gate; the real ``BranchSystem`` agrees with
    the stub at the seam; and a lapse releases exactly that Branch's gated role
    on exactly that planet, through the existing unassign teardown, leaving
    every other agent in the world byte-identical.
    """

    def _assert_gate(self, world, player, planet, commitment, message,
                     roles=VALID_ROLES, gated=True):
        """Assign a fresh agent to each of *roles* and assert the gate's answer.

        A fresh agent per role because a permitted assignment writes the role,
        so reusing one would make each outcome depend on the last. *gated* set
        false expects every role permitted, which is the no-resolver case.
        """
        for role in roles:
            agent = world.agent(player, planet=planet)
            abbr = _BUILDING_FOR_ROLE.get(role)
            station = (
                None if abbr is None
                else FakeBuilding(building_type=abbr, planet=planet)
            )
            ok, msg = world.system.assign_agent(
                player, agent.db.agent_id, role, station
            )
            required = (
                _reference_required_branch(role, commitment) if gated else None
            )
            self.assertEqual(
                ok, required is None,
                f"{message}: assigning '{role}' under a {commitment!r} "
                f"commitment was {'permitted' if ok else 'refused'} — {msg}",
            )
            if required is None:
                self.assertEqual(
                    agent.db.role, role,
                    f"{message}: '{role}' was permitted but the agent did not "
                    f"end up holding it",
                )
                continue
            # R7.7: the refusal names the Branch the role requires, and the
            # role it is about, so the player learns what to commit to.
            self.assertIn(
                required, msg,
                f"{message}: the refusal of '{role}' did not name the "
                f"'{required}' Branch that role requires",
            )
            self.assertIn(
                role, msg,
                f"{message}: the refusal did not name the role '{role}' it "
                f"was about",
            )
            # A refusal changes nothing: no role, no target, no script.
            self.assertEqual(
                (agent.db.role, agent.db.role_target, agent.scripts.keys()),
                ("", None, ()),
                f"{message}: '{role}' was refused, yet the agent was left "
                f"partly assigned to it",
            )

    @given(
        agent_planet=st.sampled_from(_AGENT_PLANETS),
        occupied=st.sampled_from(_AGENT_PLANETS),
        committed=maybe_branch_st,
        elsewhere=maybe_branch_st,
        roster=_agent_roster_st,
        lapsed=branch_st,
        spelling=st.sampled_from(sorted(_SPELLINGS)),
        planet_default=st.booleans(),
        focus=st.sampled_from(_AGENT_PLANETS),
        gated_lapse=st.sampled_from(sorted(GATED_ROLE_FOR_BRANCH)),
    )
    @settings(max_examples=100)
    def test_the_role_gate_and_the_dormancy_release(
        self, agent_planet, occupied, committed, elsewhere, roster, lapsed,
        spelling, planet_default, focus, gated_lapse,
    ):
        """**Validates: Requirements 7.6, 7.7, 7.8**"""
        registry = fixture_registry()

        # --- The gate is the commitment on the AGENT's planet (R7.6) ----- #
        # Two commitments in play, one on the agent's planet and one on its
        # owner's. Only the agent's may decide, so when the two differ this
        # clause fails for a gate that asked about the owner instead.
        resolver = _PlanetResolver(
            {occupied: elsewhere, agent_planet: committed}
        )
        gate_world = _AgentWorld(registry, resolver)
        player = FakePlayer(planet=occupied)

        self._assert_gate(
            gate_world, player, agent_planet, committed, "as drawn"
        )
        self.assertEqual(
            resolver.planets_asked, [agent_planet] * len(_GATED_ROLES),
            f"the gate consulted the resolver about "
            f"{resolver.planets_asked!r} — it must ask once per gated role, "
            f"and always about the agent's planet ({agent_planet!r})",
        )

        # The roles outside the gate did not merely pass — they were never
        # asked about. `scout` is among them by decided asymmetry, and the
        # guard below keeps that from being an empty claim.
        self.assertTrue(
            UNGATED_BRANCH_ROLES.issubset(_FREE_ROLES),
            "a role carrying a Branch but exempt from the gate is missing "
            "from the swept ungated roles, so its exemption is untested",
        )

        # --- No resolver at all is no gate (R15.3) ----------------------- #
        # Both shapes a deployment can present before the composition root
        # wires the Branch_System: nothing injected, and something injected
        # that answers no commitment.
        for unwired in (None, object()):
            gate_world.system.set_branch_resolver(unwired)
            self._assert_gate(
                gate_world, player, agent_planet, committed,
                f"with {unwired!r} as the resolver",
                roles=_GATED_ROLES, gated=False,
            )

        # Re-wired, the gate bites again — so the clause above passed because
        # nothing was gating, not because nothing can gate.
        gate_world.system.set_branch_resolver(resolver)
        mismatched = next(
            role for role, branch in sorted(GATED_BRANCH_ROLES.items())
            if branch != committed
        )
        rewired = gate_world.agent(player, planet=agent_planet)
        self.assertFalse(
            gate_world.system.assign_agent(
                player, rewired.db.agent_id, mismatched
            )[0],
            f"re-wiring the resolver left the gate open: '{mismatched}' was "
            f"assignable under a {committed!r} commitment",
        )

        # --- The real Branch_System agrees at the seam (R7.6) ------------ #
        # The same sweep with the actual collaborator, over labs standing on
        # the two planets, so the gate is measured against a commitment that
        # was DERIVED rather than declared.
        labs = []
        if elsewhere is not None:
            labs.append(FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[elsewhere], planet=occupied,
            ))
        if committed is not None:
            labs.append(FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[committed], planet=agent_planet,
            ))
        player.set_buildings(labs)
        gate_world.system.set_branch_resolver(
            BranchSystem(registry, EventBus())
        )
        # An agent with no planet of its own is asked about its owner's, which
        # is the fallback the resolver applies to a None planet.
        derived = _reference_commitment(
            labs, agent_planet if agent_planet is not None else occupied
        )
        self._assert_gate(
            gate_world, player, agent_planet, derived, "over real Branch_Labs"
        )

        # --- A lapse releases exactly that Branch's role there (R7.8) ---- #
        lapse_world = _AgentWorld(registry)
        mine = FakePlayer(key="Mine", planet=occupied)
        theirs = FakePlayer(key="Theirs", planet=occupied)
        for own, planet, role, stationed in roster:
            lapse_world.agent(
                mine if own else theirs, planet=planet, role=role,
                stationed=stationed,
            )
        agents = lapse_world.agents
        planet_arg = None if planet_default else focus
        expected = _reference_released(agents, mine, planet_arg, lapsed)
        released_ids = {id(agent) for agent in expected}
        before = _roster_snapshot(agents)
        stations = {id(agent): agent.db.role_target for agent in agents}

        released = lapse_world.system.unassign_branch_roles(
            mine, planet_arg, _SPELLINGS[spelling](lapsed)
        )

        self.assertEqual(
            released, len(expected),
            f"a '{lapsed}' lapse on {planet_arg!r} reported {released} "
            f"agent(s) released, against {len(expected)} holding that "
            f"Branch's role there",
        )
        for agent in expected:
            # The teardown is the existing unassign path, not a role-string
            # blank: script detached, target cleared, the building's slot let
            # go — anything less leaves a dormant Branch still commanding.
            self.assertEqual(
                (agent.db.role, agent.db.role_target, agent.scripts.keys()),
                ("", None, ()),
                f"{agent!r} was released without the existing unassign "
                f"teardown running over it",
            )
            station = stations[id(agent)]
            if station is not None:
                self.assertIsNone(
                    station.attributes.get("assigned_agent"),
                    f"{agent!r} was released but its building still holds it "
                    f"as the assigned agent",
                )
        for agent in agents:
            if id(agent) in released_ids:
                continue
            self.assertEqual(
                _agent_snapshot(agent), before[id(agent)],
                f"a '{lapsed}' lapse on {planet_arg!r} changed {agent!r}, "
                f"which holds neither that Branch's gated role nor a place on "
                f"that planet under that owner",
            )

        # A second lapse of the same Branch has nothing left to release, and
        # an unreadable Branch never had anything (R15.3).
        settled = _roster_snapshot(agents)
        for repeat in (lapsed,) + _GARBAGE_BRANCHES:
            self.assertEqual(
                lapse_world.system.unassign_branch_roles(
                    mine, planet_arg, repeat
                ),
                0,
                f"a lapse of {repeat!r} released agents that a lapse of "
                f"'{lapsed}' had either already released or must never touch",
            )
        self.assertEqual(
            _roster_snapshot(agents), settled,
            "a repeated or unreadable lapse changed the roster",
        )

        # --- The release is never empty by accident ---------------------- #
        # The drawn roster may hold no releasable agent at all, which would
        # leave every clause above vacuous. This one always builds a victim,
        # in a world of its own so the roster above cannot influence it, and
        # surrounds it with the four kinds of agent that must survive.
        focus_world = _AgentWorld(registry)
        owner = FakePlayer(key="Owner", planet=focus)
        rival = FakePlayer(key="Rival", planet=focus)
        role = GATED_ROLE_FOR_BRANCH[gated_lapse]
        away = next(planet for planet in _AGENT_PLANETS if planet != focus)
        victim = focus_world.agent(
            owner, planet=focus, role=role, stationed=True
        )
        scout = focus_world.agent(owner, planet=focus, role="scout")
        survivors = {
            "the same role on another planet":
                focus_world.agent(owner, planet=away, role=role),
            "another player's agent in the same role":
                focus_world.agent(rival, planet=focus, role=role),
            "a scout, which no lapse releases": scout,
            "a Branch-free harvester":
                focus_world.agent(
                    owner, planet=focus, role="harvester", stationed=True
                ),
        }
        others = {
            branch: focus_world.agent(
                owner, planet=focus, role=GATED_ROLE_FOR_BRANCH[branch],
            )
            for branch in sorted(GATED_ROLE_FOR_BRANCH)
            if branch != gated_lapse
        }
        station = victim.db.role_target

        self.assertEqual(
            focus_world.system.unassign_branch_roles(owner, focus, gated_lapse),
            1,
            f"a '{gated_lapse}' lapse on {focus!r} did not release the one "
            f"'{role}' agent standing there",
        )
        self.assertEqual(
            (victim.db.role, victim.db.role_target, victim.scripts.keys()),
            ("", None, ()),
            f"the released '{role}' agent kept part of its assignment",
        )
        self.assertIsNone(
            station.attributes.get("assigned_agent"),
            "the released agent's building still holds it as assigned",
        )
        for description, survivor in survivors.items():
            self.assertNotEqual(
                survivor.db.role, "",
                f"a '{gated_lapse}' lapse on {focus!r} also released "
                f"{description}",
            )
        for branch, agent in others.items():
            self.assertEqual(
                agent.db.role, GATED_ROLE_FOR_BRANCH[branch],
                f"a '{gated_lapse}' lapse released the '{branch}' Branch's "
                f"agent as well",
            )

        # `scout` is exempt from the release for the same reason it is exempt
        # from the gate: a lapsed Recon commitment leaves patrols running.
        exempt = [
            branch for branch in BRANCHES
            if GATED_ROLE_FOR_BRANCH.get(branch) is None
        ]
        self.assertEqual(
            len(exempt), 1,
            f"exactly one Branch's role is exempt from the release (the one "
            f"`scout` belongs to), but {exempt!r} are",
        )
        recon = exempt[0]
        self.assertEqual(
            focus_world.system.unassign_branch_roles(owner, focus, recon),
            0,
            f"a '{recon}' lapse released agents, though that Branch's role is "
            f"exempt from the release",
        )
        self.assertEqual(
            scout.db.role, "scout",
            f"a '{recon}' lapse stopped a scout patrolling",
        )


# ================================================================== #
#  Property 7
# ================================================================== #
#
# ``db.tech_bonuses`` is derived state with exactly three inputs — the researched
# record, the Branch_Commitment on the planet in question, and the Reinstatement
# pending set — and this property states the whole function of those three. The
# reference computes it from the DRAWN inputs and the fixture technology DICTS,
# never from the registry the systems were injected with and never from
# ``applied_technologies``' own answer, so a filter reading the wrong field, a
# dropped pending test, an accumulation that composes the wrong way, or a dict
# that is added to rather than rebuilt each show up as a mismatch.
#
# The accumulation rule is ``TechLabSystem._apply_tech_effect``'s, and the whole
# rule: ``production_multiplier`` composes MULTIPLICATIVELY starting from 1.0,
# every other payload key is ADDITIVE starting from 0, and a technology carrying
# no payload contributes nothing at all. The fixture's payloads make that
# accumulation **order-independent**, which is what lets this assert exact float
# equality rather than a tolerance: every additive value is a small integral
# float (so any sum of them is exact), and there are exactly two multiplicative
# ones, both in the same Branch, so at worst two factors compose — and IEEE
# multiplication is commutative. A third multiplicative fixture value would
# break that, so it is a property of the fixture and stated here deliberately.
#
# What the reference deliberately does not do:
#
# - It applies no Operational filter to the lab that confers the commitment. A
#   commitment follows ownership of a *completed* lab and nothing else
#   (Property 5), so an offline, mid-upgrade, or hostile-suspended lab keeps its
#   Branch's bonuses applied (R5.10). The clause that forces both extremes of
#   those flags asserts the dict does not move, and guards that the lab really is
#   non-Operational at one of them.
# - It touches no record. A recompute is a read of ``researched_techs`` and of
#   ``branch_reinstatement`` and a write of ``tech_bonuses`` alone: dormancy
#   suspends effects and erases no history (R5.3), and the pending set has a
#   single writer that is not this system (R15.5).
# - It never answers "the filter is empty" and "there is no filter" alike. With
#   no Branch resolver wired the rebuild must accumulate EVERY researched
#   technology, exactly as it did before this feature; an empty filter is the
#   legitimate answer for a player committed to nothing. Conflating the two would
#   silently zero every bonus in the game, so both are drawn every example.
#
# R13.1 and R13.2 are the same three inputs read by the technology view, so they
# are asserted against the same reference: the view's Branch, its researched
# list, its dormancy counts, and its Reinstatement fraction cannot disagree with
# the dict the recompute writes.


#: Fixture technology key -> its effect payload, read off the same YAML-shaped
#: dicts the injected registry was loaded from rather than out of the registry,
#: so a payload read through the wrong field is a mismatch rather than a shared
#: mistake. ``None`` for the one payload-less fixture technology.
_FIXTURE_EFFECT_BY_KEY: dict = {
    entry["key"]: entry.get("effect_value") for entry in FIXTURE_TECHNOLOGY_DICTS
}

#: Record entries no technology definition backs: a plausible retired key and the
#: blank the record normalizer must drop (R15.3). Both are recorded, neither
#: resolves to a Branch, so neither can be dormant in one — and neither
#: contributes a bonus, because the rebuild skips a key with no definition.
_UNBACKED_TECH_KEYS: tuple[str, ...] = ("retired_prototype", "")

#: The multiplicative payload key. Named rather than inlined so the one place the
#: reference departs from addition reads as the contract it mirrors.
_MULTIPLICATIVE_KEY = "production_multiplier"


def _reference_record(recorded) -> set[str]:
    """*recorded* as every Branch read sees it: non-blank strings only (R15.3)."""
    return {key for key in recorded if isinstance(key, str) and key.strip()}


def _reference_applied(recorded, live, pending) -> frozenset[str]:
    """The recorded keys whose effects are live under *live*, and only those.

    The whole filter (R5.1, R5.7), in the implementation's own order: a key
    outside the live commitment is dormant; a key inside it that still awaits its
    reduced-cost Reinstatement job is withheld; everything else applies. A key
    this catalog cannot place in a Branch **applies** — it cannot be dormant in a
    Branch it has none of — which is why the unbacked keys above are drawn.

    *pending* is passed whole rather than per-Branch: a key of any other Branch is
    already withheld as dormant before the pending test runs, so scoping it would
    change no answer.
    """
    applied = set()
    for key in _reference_record(recorded):
        branch = _FIXTURE_BRANCH_OF_TECH.get(key)
        if branch is None:
            applied.add(key)
            continue
        if branch != live:
            continue                                      # R5.1 — dormant
        if key in pending:
            continue                                      # R5.7 — still owed
        applied.add(key)
    return frozenset(applied)


def _reference_bonuses(keys) -> dict:
    """The bonus dict accumulating *keys*' payloads (R5.1, R13.3).

    ``TechLabSystem._apply_tech_effect``'s arithmetic, mirrored exactly:
    ``production_multiplier`` composes from 1.0 by multiplication and every other
    key from 0 by addition, a payload-less technology contributes nothing, and a
    key with no definition contributes nothing. Sorted only for determinism — see
    the section comment on why the fixture's values make the order irrelevant.
    """
    bonuses: dict = {}
    for key in sorted(keys):
        for name, value in (_FIXTURE_EFFECT_BY_KEY.get(key) or {}).items():
            if name == _MULTIPLICATIVE_KEY:
                bonuses[name] = bonuses.get(name, 1.0) * float(value)
            else:
                bonuses[name] = bonuses.get(name, 0) + float(value)
    return bonuses


def _reference_pending_attribute(pending) -> dict:
    """*pending* as the ``branch_reinstatement`` attribute's documented shape.

    ``{branch: [tech_key, ...]}``, grouped so the pending set of the live Branch
    is the only one a filter can read — which is the shape
    :meth:`BranchSystem._seed_reinstatement` writes. A key belonging to no
    Branch has no group to sit in and is dropped, so it stays applied.
    """
    grouped: dict = {}
    for key in sorted(_reference_record(pending)):
        branch = _FIXTURE_BRANCH_OF_TECH.get(key)
        if branch is None:
            continue
        grouped.setdefault(branch, []).append(key)
    return grouped


def _reference_view_researched(recorded, live) -> list[str]:
    """The keys the technology view lists as researched (R13.1).

    The record scoped to the live commitment, sorted. Empty for a player holding
    no commitment: what sits in the other Branches is reported as dormancy counts
    instead of being dropped (R13.2), and a key belonging to no Branch belongs to
    no view either.
    """
    if live is None:
        return []
    return sorted(
        key for key in _reference_record(recorded)
        if _FIXTURE_BRANCH_OF_TECH.get(key) == live
    )


def _record_snapshot(player) -> tuple:
    """The two persisted values a recompute must never write (R5.3, R15.5)."""
    return (
        set(player.db.researched_techs or ()),
        copy.deepcopy(player.db.branch_reinstatement),
    )


class _BlindResolver:
    """A wired Branch resolver that answers *no filter* rather than an empty one.

    The shape a resolver predating the dormancy filter presents: it exists, so
    ``TechLabSystem`` has something wired, but it has no answer — which must
    leave the rebuild unfiltered rather than empty.
    """

    def applied_technologies(self, player, planet=None):
        return None


class _ExplodingResolver:
    """A wired resolver whose filter raises.

    A rebuild runs inside a login and inside a tick, so a collaborator's failure
    has to degrade to the pre-feature unfiltered accumulation rather than raise
    out of either (R15.3).
    """

    def applied_technologies(self, player, planet=None):
        raise RuntimeError("filter on fire")


# Feature: tech-tree-branch-foundation, Property 7: Applied tech bonuses equal
# the accumulation over the committed, non-pending techs
#
# **Validates: Requirements 5.1, 5.2, 5.3, 5.7, 5.10, 13.1, 13.2**
class TestProperty7AppliedBonusesEqualTheCommittedAccumulation(unittest.TestCase):
    """The bonus dict is a function of the record, the commitment, and the owed.

    One ``@given`` test, because the clauses are one claim seen from several
    sides: the filter equals the reference partition and the dict equals the
    reference accumulation over it, on the planet asked about and on the planet
    the player occupies; a second recompute changes nothing, so the dict is
    rebuilt rather than added to; the record and the pending set come through
    untouched; the lab's Operational flags are irrelevant; the technology view
    quotes the same partition; an unwired resolver accumulates everything; a
    pending key is withheld and returns when it is cleared; and the commitment
    events rebuild the dict with nobody calling the recompute.
    """

    def _assert_dict(self, tech, player, planet, live, pending, record, message):
        """Recompute for *planet*, assert the dict equals the reference, twice.

        The second call is the load-bearing half: ``_apply_tech_effect``
        accumulates *onto* the dict it finds, so a rebuild that failed to clear
        it first would double every additive key and square the multiplier. Only
        an idempotent rebuild survives both calls.
        """
        expected = _reference_bonuses(_reference_applied(record, live, pending))
        for attempt in ("first", "second"):
            tech.recompute_tech_bonuses(player, planet)
            self.assertEqual(
                player.db.tech_bonuses, expected,
                f"{message}: the {attempt} recompute for planet {planet!r} did "
                f"not equal the accumulation over the {live!r} commitment's "
                f"non-pending recorded technologies",
            )
        return expected

    @given(
        record=researched_set_st,
        committed=maybe_branch_st,
        away_branch=branch_st,
        pending=pending_set_st,
        offline=st.booleans(),
        upgrading=st.booleans(),
        home=st.sampled_from(FIXTURE_PLANETS),
        unbacked=st.booleans(),
        fraction=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        focus_branch=branch_st,
    )
    @settings(max_examples=100)
    def test_applied_bonuses_equal_the_committed_accumulation(
        self, record, committed, away_branch, pending, offline, upgrading, home,
        unbacked, fraction, focus_branch,
    ):
        """**Validates: Requirements 5.1, 5.2, 5.3, 5.7, 5.10, 13.1, 13.2**"""
        registry = fixture_registry(
            balance=BalanceConfig(branch_reinstatement_cost_fraction=fraction)
        )
        bus = EventBus()
        notes = _notification_sink(bus)
        tech = TechLabSystem(registry, bus)
        branch = BranchSystem(registry, bus, tech_system=tech)
        tech.set_branch_resolver(branch)

        # The reference's additive/multiplicative split has to cover the whole
        # payload vocabulary, so a key added to it without a fixture technology
        # carrying it fails here rather than narrowing this property in silence.
        self.assertEqual(
            {
                name
                for effect in _FIXTURE_EFFECT_BY_KEY.values()
                for name in (effect or {})
            },
            set(TECH_BONUS_KEYS),
            "the fixture technologies no longer carry every payload key the "
            "tech system understands, so the accumulation is only partly tested",
        )
        self.assertIn(
            _MULTIPLICATIVE_KEY, TECH_BONUS_KEYS,
            "the multiplicative payload key is no longer part of the vocabulary",
        )

        away = next(planet for planet in FIXTURE_PLANETS if planet != home)
        record = set(record) | (set(_UNBACKED_TECH_KEYS) if unbacked else set())
        player = FakePlayer(key="Bonuses", planet=home)
        player.db.researched_techs = set(record)
        player.db.branch_reinstatement = _reference_pending_attribute(pending)

        home_lab = None
        if committed is not None:
            home_lab = FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[committed], planet=home,
                offline=offline, upgrading=upgrading, x=1, y=1,
            )
        roster = ([home_lab] if home_lab is not None else []) + [
            FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[away_branch], planet=away, x=2, y=2,
            )
        ]
        player.set_buildings(roster)

        # The world was built to confer the drawn commitments — asserted rather
        # than assumed, so a mismatch below is this property's failure and not a
        # broken fixture.
        self.assertEqual(
            (_reference_commitment(roster, home), _reference_commitment(roster, away)),
            (committed, away_branch),
            "the drawn roster does not confer the drawn commitments",
        )

        # --- The filter equals the reference partition (R5.1, R5.7) ------ #
        before = _record_snapshot(player)
        for planet, live in ((home, committed), (away, away_branch)):
            self.assertEqual(
                branch.applied_technologies(player, planet),
                _reference_applied(record, live, pending),
                f"the applied set on {planet!r} disagreed with the record "
                f"filtered to the {live!r} commitment and the pending set",
            )
        self.assertEqual(
            branch.applied_technologies(player),
            _reference_applied(record, committed, pending),
            "the applied set did not default to the planet the player occupies",
        )

        # --- The dict equals the accumulation over it, per planet -------- #
        # Named planets first, so "the bonuses follow the planet the recompute
        # was told about" is a claim about two different answers (R5.1, R5.2).
        self._assert_dict(tech, player, away, away_branch, pending, record, "as drawn")
        expected_home = self._assert_dict(
            tech, player, home, committed, pending, record, "as drawn"
        )
        tech.recompute_tech_bonuses(player)
        self.assertEqual(
            player.db.tech_bonuses, expected_home,
            "the recompute did not default to the planet the player occupies",
        )

        # --- The record and the pending set are untouched (R5.3, R15.5) -- #
        self.assertEqual(
            _record_snapshot(player), before,
            "a recompute wrote to the researched record or to the Reinstatement "
            "pending set — dormancy suspends effects and erases no history",
        )

        # --- The lab's Operational state is irrelevant (R5.10) ----------- #
        # Both extremes of the two flags that decide whether a building is
        # Operational, with the lab verifiably NOT Operational at the first —
        # which is what keeps this clause from passing vacuously.
        if home_lab is not None:
            for suspended in (True, False):
                home_lab.attributes.add("offline", suspended)
                if suspended:
                    home_lab.attributes.add("upgrade_target_level", 2)
                    self.assertFalse(
                        building_is_operational(home_lab),
                        "the guard failed: an offline, mid-upgrade lab is "
                        "supposed to be non-Operational",
                    )
                else:
                    home_lab.attributes.remove("upgrade_target_level")
                self._assert_dict(
                    tech, player, home, committed, pending, record,
                    f"with the lab offline={suspended}",
                )

        # --- The technology view quotes the same partition (R13.1, R13.2)  #
        notes.clear()
        view_before = _record_snapshot(player)
        view = tech.report_technology_view(player)
        researched = _reference_view_researched(record, committed)
        dormant = _reference_dormant(record, committed)
        self.assertEqual(
            (view["planet"], view["branch"]), (home, committed),
            "the view did not report the commitment on the occupied planet",
        )
        self.assertEqual(
            (view["doctrine"], view["operation_kind"]),
            (BRANCH_DOCTRINE.get(committed), BRANCH_OPERATION_KIND.get(committed)),
            "the view did not report the committed Branch's doctrine and "
            "Signature_Vector",
        )
        self.assertEqual(
            [entry["key"] for entry in view["researched"]], researched,
            "the view's researched list disagreed with the record scoped to the "
            "commitment",
        )
        self.assertEqual(
            view["reinstatement_pending"],
            [key for key in researched if key in pending],
            "the view's pending list disagreed with the keys still awaiting "
            "their Reinstatement job",
        )
        self.assertEqual(
            view["dormant"],
            [
                {
                    "branch": dormant_branch,
                    "doctrine": BRANCH_DOCTRINE.get(dormant_branch),
                    "count": len(keys),
                }
                for dormant_branch, keys in dormant.items()
            ],
            "the view's dormant Branches disagreed with the record held outside "
            "the commitment",
        )
        self.assertEqual(
            view["dormant_count"], sum(len(keys) for keys in dormant.values()),
            "the view's dormant total disagreed with its per-Branch counts",
        )
        self.assertEqual(
            view["reinstatement_fraction"], fraction,
            "the view did not quote the configured Reinstatement cost fraction",
        )
        for entry in view["available"]:
            self.assertEqual(
                _FIXTURE_BRANCH_OF_TECH.get(entry["key"]), committed,
                "the view offered a technology outside the committed Branch",
            )
            self.assertNotIn(
                entry["key"], record,
                "the view offered a technology the player has already recorded",
            )
        self.assertEqual(
            [(note[0], note[1]) for note in notes], [(player, "technology_view")],
            "the view did not publish exactly one structured technology_view "
            "notification to the player who asked",
        )
        self.assertEqual(
            notes[0][2], view,
            "the notification's payload and the returned view disagreed, so a "
            "caller and the presenter could read different figures",
        )
        self.assertEqual(
            (_record_snapshot(player), player.db.tech_bonuses),
            (view_before, expected_home),
            "asking for the technology view changed the player's record or "
            "bonuses — the view reads and recomputes nothing",
        )

        # --- No resolver at all is no FILTER, not an empty one ------------ #
        # The pre-feature behavior, which is what a minimal fixture and a
        # deployment without the Branch system get: every researched technology
        # accumulates, whatever the player is committed to.
        unfiltered = _reference_bonuses(record)
        for unwired in (None, object(), _BlindResolver(), _ExplodingResolver()):
            tech.set_branch_resolver(unwired)
            self.assertIsNone(
                tech._applied_technologies(player, home),
                f"{unwired!r} as the resolver produced a filter rather than the "
                f"documented 'no filter'",
            )
            tech.recompute_tech_bonuses(player)
            self.assertEqual(
                player.db.tech_bonuses, unfiltered,
                f"with {unwired!r} as the resolver the rebuild did not "
                f"accumulate every researched technology, as it did before this "
                f"feature",
            )

        # Re-wired, the filter bites again — so the clause above passed because
        # nothing was filtering, not because nothing can filter.
        tech.set_branch_resolver(branch)
        self._assert_dict(
            tech, player, home, committed, pending, record, "after re-wiring"
        )

        # --- A pending key is withheld, and returns when it clears (R5.7) - #
        # Generated rather than hoped for: the drawn record may hold nothing in
        # the drawn commitment, and every pending clause above would then be
        # vacuous. This one always records both of one Branch's technologies
        # under a live commitment to it, and withholds each subset in turn.
        keys = FIXTURE_TECH_KEYS_BY_BRANCH[focus_branch]
        solo = FakePlayer(key="Solo", planet=home)
        solo.db.researched_techs = set(keys)
        solo_lab = FakeBuilding(
            building_type=FIXTURE_LAB_ABBR[focus_branch], planet=home, x=3, y=3,
        )
        solo.set_buildings([solo_lab])
        for withheld in ((), keys[:1], keys[1:], keys):
            solo.db.branch_reinstatement = {focus_branch: list(withheld)}
            applied = frozenset(set(keys) - set(withheld))
            self.assertEqual(
                branch.applied_technologies(solo, home), applied,
                f"withholding {withheld!r} did not withhold exactly those keys",
            )
            tech.recompute_tech_bonuses(solo)
            self.assertEqual(
                solo.db.tech_bonuses, _reference_bonuses(applied),
                f"the dict under a {focus_branch!r} commitment with {withheld!r} "
                f"awaiting Reinstatement did not equal the accumulation over the "
                f"remaining recorded technologies",
            )
        self.assertEqual(
            solo.db.tech_bonuses, {},
            "every recorded technology of the committed Branch was awaiting "
            "Reinstatement, yet the dict was not empty",
        )

        # --- The commitment events rebuild the dict (R5.2, R3.8) --------- #
        # Nobody calls the recompute here: the Branch system subscribes the
        # events that can change the answer and asks for the rebuild itself.
        solo.db.branch_reinstatement = {}
        solo.set_buildings([])
        tech.recompute_tech_bonuses(solo)
        self.assertEqual(
            solo.db.tech_bonuses, {},
            f"a player owning no Branch_Lab still had {focus_branch!r} bonuses "
            f"applied",
        )
        solo.set_buildings([solo_lab])
        bus.publish(
            CONSTRUCTION_COMPLETED, player=solo, building=solo_lab, tile=None
        )
        self.assertEqual(
            solo.db.tech_bonuses, _reference_bonuses(keys),
            "a completed Branch_Lab did not bring its Branch's recorded "
            "technologies out of dormancy",
        )
        # The destruction event fires BEFORE the delete, so the dying lab is
        # still on the roster and the rebuild must read the world as it will be.
        bus.publish(BUILDING_DESTROYED, building=solo_lab, attacker=None, tile=None)
        self.assertEqual(
            solo.db.tech_bonuses, {},
            "a destroyed Branch_Lab left its Branch's bonuses applied",
        )
        self.assertEqual(
            set(solo.db.researched_techs), set(keys),
            "losing the lab erased the researched record instead of suspending "
            "its effects (R5.3)",
        )

        # --- An unreadable input answers empty, never raises (R15.3) ----- #
        for unreadable in (None, object(), SimpleNamespace(), _ExplodingOwner()):
            self.assertEqual(
                branch.applied_technologies(unreadable), frozenset(),
                f"applied_technologies({unreadable!r}) did not answer empty",
            )
            self.assertEqual(
                branch.dormant_branches(unreadable), {},
                f"dormant_branches({unreadable!r}) did not answer empty",
            )
            tech.recompute_tech_bonuses(unreadable)      # must not raise


# ================================================================== #
#  Property 10
# ================================================================== #
#
# One conjunction of three, and the property is that it is exactly those three:
#
# 1. the existing base gate, ``world.utils.building_is_operational``;
# 2. the Active_HQ_Rule — the owner holds a completed headquarters on that
#    planet (R11.3);
# 3. the Branch being live — no Branch_Affiliation, or the owner's commitment on
#    that planet equals it (R5.4).
#
# The second conjunct is **unconditional**: it applies to a Neutral_Building
# queried through the overlay too, not only to the buildings this feature
# introduces. That is the design's Property 10 formula as written, and task 6.1
# implemented it deliberately and reported the choice, so the reference states it
# the same way rather than quietly narrowing it to affiliated buildings. It is
# also why the Neutral clause below asserts a headless Neutral_Building reads
# non-Operational *here* while still reading Operational through the util: the
# overlay is strictly additive, and a caller that has not switched to it sees the
# pre-feature answer (R2.5, R10.8).
#
# The reference is computed from the drawn flags and the drawn roster — not by
# calling the util and not by calling ``commitment`` — so a conjunct dropped, a
# conjunct inverted, a planet scope lost, or the base gate silently modified is a
# mismatch. The base gate is *separately* pinned to ``not offline and not
# under_construction``, which is the claim that the util is unmodified: it must
# ignore the affiliation, the commitment, the headquarters, and the upgrade
# marker that ``FakeBuilding`` keeps independent of ``under_construction``.
#
# Because a drawn example can satisfy the conjunction for the wrong reason, the
# sweep is followed by a clause that always builds one fully Operational
# Branch_Building and then falsifies each conjunct in turn, restoring between
# each — so every conjunct is load-bearing in every example rather than only in
# the examples that happen to exercise it.


def _entry_is_hq(entry) -> bool:
    """True when a building definition dict declares the headquarters capability.

    Capability, not abbreviation — the field :func:`world.utils.owner_has_active_hq`
    filters on — so the reference answers about the same buildings the rule does.
    """
    return entry is not None and HEADQUARTERS in (entry.get("capabilities") or ())


#: The fixture Neutral_Building abbreviation declaring the headquarters
#: capability. Derived rather than spelled, so the reference and the Active_HQ
#: fixtures cannot disagree about which building satisfies R11.3.
_HQ_ABBR = next(
    abbr for abbr in FIXTURE_NEUTRAL_ABBRS
    if _entry_is_hq(_FIXTURE_ENTRY_BY_ABBR[abbr])
)

#: The fixture Neutral_Buildings declaring NO capability, so one can be owned
#: without satisfying the very rule it is being tested against.
_PLAIN_NEUTRAL_ABBRS: tuple[str, ...] = tuple(
    abbr for abbr in FIXTURE_NEUTRAL_ABBRS
    if not (_FIXTURE_ENTRY_BY_ABBR[abbr].get("capabilities") or ())
)

#: The four states the owner's headquarters can be in, which is the Active_HQ
#: conjunct's whole input: absent, half-built (does not count), standing on the
#: building's planet, and standing on another one.
_HQ_STATES: tuple[str, ...] = ("none", "unfinished", "here", "away")


def _reference_active_hq(buildings, planet) -> bool:
    """True when *buildings* holds a completed headquarters on *planet* (R11.3).

    The reference for :func:`world.utils.owner_has_active_hq`, in its order:
    capability, then planet scope (``None`` on either side meaning "any planet"),
    then completion. A half-built headquarters deliberately does not count — the
    build-time one-per-planet check counts it, this rule does not.
    """
    for building in buildings:
        entry = _FIXTURE_ENTRY_BY_ABBR.get(building.attributes.get("building_type"))
        if not _entry_is_hq(entry):
            continue
        if planet is not None and _planet_of(building) not in (None, planet):
            continue
        if building.attributes.get("under_construction"):
            continue
        return True
    return False


def _reference_base_gate(building) -> bool:
    """True when *building* passes the existing Operational gate.

    ``world.utils.building_is_operational``'s whole rule: not ``offline``, not
    ``under_construction``. Restated from the stored flags rather than delegated,
    so this test would notice the util growing a third condition — and stated
    without the ``upgrade_target_level`` marker, because the gate does not read
    it (a real upgrade sets ``under_construction`` as well).
    """
    return not building.attributes.get("offline") and not building.attributes.get(
        "under_construction"
    )


def _reference_operational(building, buildings, occupied) -> bool:
    """The design's Property 10 formula over the drawn world alone.

    All three conjuncts, scoped to **one** planet: the building's own, falling
    back to the planet its owner occupies, so the headquarters read and the
    commitment read can never answer about different planets. A planet that stays
    unresolvable is the "any planet" wildcard both underlying queries document.
    """
    if not _reference_base_gate(building):
        return False
    planet = _planet_of(building) or occupied
    if not _reference_active_hq(buildings, planet):
        return False                                      # R11.3
    affiliation = _affiliated_branch(building.attributes.get("building_type"))
    if affiliation is None:
        return True                                       # Neutral_Building
    return _reference_commitment(buildings, planet) == affiliation   # R5.4


# Feature: tech-tree-branch-foundation, Property 10: A Branch_Building is
# Operational exactly when the base gate passes and its Branch is live
#
# **Validates: Requirements 5.4, 11.3**
class TestProperty10OperationalOverlay(unittest.TestCase):
    """The overlay is the base gate AND an active HQ AND a live Branch, exactly.

    One ``@given`` test, because the clauses are one claim seen from several
    sides: the answer equals the three-way conjunction over the drawn world; the
    base gate is still the unmodified util, which answers True for a building the
    overlay calls dormant; each conjunct is load-bearing; a Neutral_Building's
    affiliation conjunct never fires while its headquarters conjunct still does;
    the scope is the building's own planet; a suspended lab keeps its Branch's
    buildings live; and an unreadable building is inert rather than an exception.
    """

    def _prober(self, branch_name, planet):
        """Return a fully Operational world: ``(player, target, lab, hq)``.

        One Branch_Building of *branch_name*, that Branch's lab, and a completed
        headquarters, all owned, all on *planet*, all live — the one configuration
        every conjunct passes, and therefore the only starting point from which
        falsifying one conjunct at a time proves anything.
        """
        lab = FakeBuilding(
            building_type=FIXTURE_LAB_ABBR[branch_name], planet=planet, x=6, y=6,
        )
        hq = FakeBuilding(building_type=_HQ_ABBR, planet=planet, x=7, y=7)
        target = FakeBuilding(
            building_type=FIXTURE_BRANCH_BUILDING_ABBR[branch_name],
            planet=planet, x=5, y=5,
        )
        player = FakePlayer(
            key="Prober", planet=planet, buildings=[lab, hq, target],
        )
        return player, target, lab, hq

    @given(
        affiliation=maybe_branch_st,
        as_lab=st.booleans(),
        neutral=st.sampled_from(FIXTURE_NEUTRAL_ABBRS),
        offline=st.booleans(),
        under_construction=st.booleans(),
        upgrading=st.booleans(),
        committed=maybe_branch_st,
        hq_state=st.sampled_from(_HQ_STATES),
        bplanet=st.one_of(st.none(), st.sampled_from(FIXTURE_PLANETS)),
        occupied=st.one_of(st.none(), st.sampled_from(FIXTURE_PLANETS)),
        probe=branch_st,
        offset=st.integers(min_value=0, max_value=len(BRANCHES) - 2),
    )
    @settings(max_examples=100)
    def test_a_branch_building_is_operational_when_its_branch_is_live(
        self, affiliation, as_lab, neutral, offline, under_construction, upgrading,
        committed, hq_state, bplanet, occupied, probe, offset,
    ):
        """**Validates: Requirements 5.4, 11.3**"""
        system = BranchSystem(fixture_registry(), EventBus())

        # --- The drawn configuration answers the conjunction ------------- #
        # The queried building is a Branch_Building, that Branch's lab, or a
        # Neutral_Building — the lab included because a lab belongs to the Branch
        # it hosts, so the overlay judges it by its own commitment.
        if affiliation is None:
            btype = neutral
        elif as_lab:
            btype = FIXTURE_LAB_ABBR[affiliation]
        else:
            btype = FIXTURE_BRANCH_BUILDING_ABBR[affiliation]
        queried = FakeBuilding(
            building_type=btype, planet=bplanet, offline=offline,
            under_construction=under_construction, upgrading=upgrading, x=3, y=4,
        )
        home = bplanet or occupied
        away = next(planet for planet in FIXTURE_PLANETS if planet != home)
        roster = []
        if committed is not None:
            roster.append(FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[committed], planet=home, x=1, y=1,
            ))
        if hq_state == "unfinished":
            roster.append(FakeBuilding(
                building_type=_HQ_ABBR, planet=home, under_construction=True,
                x=2, y=2,
            ))
        elif hq_state == "here":
            roster.append(FakeBuilding(building_type=_HQ_ABBR, planet=home, x=2, y=2))
        elif hq_state == "away":
            roster.append(FakeBuilding(building_type=_HQ_ABBR, planet=away, x=2, y=2))
        roster.append(queried)
        player = FakePlayer(key="Owner", planet=occupied, buildings=roster)

        before = _world_snapshot(player)
        expected = _reference_operational(queried, roster, occupied)
        for attempt in ("first", "second"):
            self.assertEqual(
                system.is_operational(queried), expected,
                f"the {attempt} overlay read of a {btype!r} building "
                f"(affiliation {affiliation!r}) on {bplanet!r} disagreed with the "
                f"conjunction of the base gate, an active HQ ({hq_state!r}), and "
                f"the {committed!r} commitment",
            )
        self.assertEqual(
            _world_snapshot(player), before,
            "the overlay wrote to the world — it is a query over the owned "
            "buildings and stores nothing",
        )

        # --- The base gate is still the unmodified util ------------------ #
        # It reads the two flags and nothing else: not the affiliation, not the
        # commitment, not the headquarters, not the upgrade marker.
        self.assertEqual(
            building_is_operational(queried), _reference_base_gate(queried),
            "world.utils.building_is_operational no longer answers 'not offline "
            "and not under_construction' — the Branch overlay was supposed to "
            "leave it alone",
        )

        # --- Every conjunct is load-bearing (never vacuous) -------------- #
        # A fully Operational Branch_Building, then one conjunct falsified at a
        # time with the other two held true, restoring between each.
        prober, target, lab, hq = self._prober(probe, FIXTURE_PLANETS[0])
        owned = prober.get_buildings()
        self.assertTrue(
            system.is_operational(target),
            f"a live '{probe}' Branch_Building whose owner holds that Branch's "
            f"lab and a completed HQ on the same planet was not Operational",
        )

        for flag in ("offline", "under_construction"):
            target.attributes.add(flag, True)
            self.assertFalse(
                system.is_operational(target),
                f"a Branch_Building with {flag}=True was Operational — the base "
                f"gate is still the first conjunct",
            )
            target.attributes.add(flag, False)
            self.assertTrue(
                system.is_operational(target),
                f"clearing {flag} did not restore the answer",
            )

        # R11.3: the Active_HQ_Rule, in each of the three ways it can fail.
        for replacement, why in (
            ((), "no headquarters at all"),
            (
                (FakeBuilding(
                    building_type=_HQ_ABBR, planet=FIXTURE_PLANETS[0],
                    under_construction=True, x=8, y=8,
                ),),
                "a half-built headquarters",
            ),
            (
                (FakeBuilding(
                    building_type=_HQ_ABBR, planet=FIXTURE_PLANETS[1], x=8, y=8,
                ),),
                "a headquarters on another planet",
            ),
        ):
            prober.set_buildings(
                [b for b in owned if b is not hq] + list(replacement)
            )
            self.assertFalse(
                system.is_operational(target),
                f"a Branch_Building was Operational with {why} — a player whose "
                f"base is inert operates no Branch_Building there (R11.3)",
            )
            self.assertTrue(
                building_is_operational(target),
                "the guard failed: the base gate is supposed to still pass here, "
                "so this clause would prove nothing about the HQ conjunct",
            )
        prober.set_buildings(owned)
        self.assertTrue(system.is_operational(target), "restoring the HQ failed")

        # R5.4: the Branch being live, both ways it can fail.
        rival = [b for b in BRANCHES if b != probe][offset % (len(BRANCHES) - 1)]
        for replacement, why in (
            ((), "no Branch_Commitment at all"),
            (
                (FakeBuilding(
                    building_type=FIXTURE_LAB_ABBR[rival],
                    planet=FIXTURE_PLANETS[0], x=9, y=9,
                ),),
                f"a '{rival}' commitment instead",
            ),
        ):
            prober.set_buildings(
                [b for b in owned if b is not lab] + list(replacement)
            )
            self.assertFalse(
                system.is_operational(target),
                f"a '{probe}' Branch_Building was Operational under {why} — a "
                f"dormant Branch's buildings perform no capability behaviour",
            )
            self.assertTrue(
                building_is_operational(target),
                "the guard failed: the util must still answer True for a dormant "
                "building, which is what makes the overlay additive",
            )
        prober.set_buildings(owned)
        self.assertTrue(system.is_operational(target), "restoring the lab failed")

        # --- A suspended lab keeps its Branch's buildings live (R5.10) ---- #
        # The crossover with Property 5: a commitment follows ownership of a
        # completed lab, not that lab's Operational state, so the lab can be
        # non-Operational while the buildings it keeps live are not.
        lab.attributes.add("offline", True)
        lab.attributes.add("upgrade_target_level", lab.building_level + 1)
        self.assertFalse(
            building_is_operational(lab),
            "the guard failed: an offline, mid-upgrade lab is supposed to be "
            "non-Operational",
        )
        self.assertTrue(
            system.is_operational(target),
            "suspending the Branch_Lab took its Branch's buildings down with it "
            "— a suspension withholds the lab's own function (R5.10)",
        )
        self.assertFalse(
            system.is_operational(lab),
            "an offline lab was Operational — the overlay never relaxes the base "
            "gate",
        )
        lab.attributes.add("offline", False)
        lab.attributes.remove("upgrade_target_level")

        # --- A Neutral_Building's affiliation conjunct never fires -------- #
        # Which is what bounds the overlay's blast radius to the buildings this
        # feature introduces (R2.5): under every commitment and under none, a
        # Neutral_Building's answer is the base gate AND the HQ rule. The HQ half
        # is deliberately still conditional on nothing — the design's formula
        # applies it to every building queried here.
        plain = FakeBuilding(
            building_type=_PLAIN_NEUTRAL_ABBRS[0], planet=FIXTURE_PLANETS[0],
            x=10, y=10,
        )
        neutral_owner = FakePlayer(key="Neutral", planet=FIXTURE_PLANETS[0])
        self.assertIsNone(
            _affiliated_branch(_PLAIN_NEUTRAL_ABBRS[0]),
            "the guard failed: the building this clause calls Neutral declares a "
            "Branch_Affiliation",
        )
        for commitment in (None,) + tuple(BRANCHES):
            labs = [] if commitment is None else [FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[commitment],
                planet=FIXTURE_PLANETS[0], x=11, y=11,
            )]
            for hq_present in (True, False):
                headquarters = [FakeBuilding(
                    building_type=_HQ_ABBR, planet=FIXTURE_PLANETS[0], x=12, y=12,
                )] if hq_present else []
                neutral_owner.set_buildings(labs + headquarters + [plain])
                self.assertEqual(
                    system.is_operational(plain), hq_present,
                    f"a Neutral_Building under a {commitment!r} commitment with "
                    f"hq={hq_present} did not answer the base gate AND the "
                    f"Active_HQ_Rule alone",
                )
                self.assertTrue(
                    building_is_operational(plain),
                    "the guard failed: the util must answer True for this "
                    "building throughout, so the overlay is strictly additive",
                )

        # --- The scope is the building's own planet (R3.7) ---------------- #
        # A headquarters on both planets and a lab on only one, so what differs
        # between the two buildings is the commitment and nothing else.
        here = FakeBuilding(
            building_type=FIXTURE_BRANCH_BUILDING_ABBR[probe],
            planet=FIXTURE_PLANETS[0], x=13, y=13,
        )
        there = FakeBuilding(
            building_type=FIXTURE_BRANCH_BUILDING_ABBR[probe],
            planet=FIXTURE_PLANETS[1], x=14, y=14,
        )
        # The owner needs no name of its own here: ``set_buildings`` stamps it
        # onto every building, which is both how the overlay resolves it and what
        # keeps it alive.
        FakePlayer(key="Scoped", planet=FIXTURE_PLANETS[0], buildings=[
            FakeBuilding(
                building_type=FIXTURE_LAB_ABBR[probe], planet=FIXTURE_PLANETS[0],
                x=15, y=15,
            ),
            FakeBuilding(
                building_type=_HQ_ABBR, planet=FIXTURE_PLANETS[0], x=16, y=16,
            ),
            FakeBuilding(
                building_type=_HQ_ABBR, planet=FIXTURE_PLANETS[1], x=17, y=17,
            ),
            here, there,
        ])
        self.assertTrue(
            system.is_operational(here),
            f"a '{probe}' building on the planet holding that Branch's lab was "
            f"not Operational",
        )
        self.assertFalse(
            system.is_operational(there),
            f"a '{probe}' building on a planet holding a headquarters but no "
            f"'{probe}' lab was Operational — a commitment is per-planet",
        )

        # --- An unreadable building is inert, never a raise (R15.3) ------- #
        # The callers are capability behaviours running inside a tick, so an
        # answer is required for every input.
        orphan = FakeBuilding(
            building_type=FIXTURE_BRANCH_BUILDING_ABBR[probe],
            planet=FIXTURE_PLANETS[0], x=18, y=18,
        )
        self.assertTrue(
            building_is_operational(orphan),
            "the guard failed: an ownerless building still passes the base gate",
        )
        self.assertFalse(
            system.is_operational(orphan),
            "a building carrying no resolvable owner was Operational",
        )
        for junk in (None, object(), SimpleNamespace(), 17, "ZW", _ExplodingOwner()):
            self.assertFalse(
                system.is_operational(junk),
                f"is_operational({junk!r}) did not answer False",
            )


if __name__ == "__main__":
    unittest.main()
