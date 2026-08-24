"""
Unit tests for the BranchSystem identity, commitment, and estate surfaces.

Covers the six identity queries — ``branch_of_building``,
``branch_of_technology``, ``lab_for_branch``, ``branch_buildings``,
``role_for_branch``, and ``branch_overview`` — the two commitment queries
``commitment`` and ``has_commitment``, and the three estate queries ``estate``,
``estate_count``, and ``conflicting_estates``, on three axes:

* the right answer over a complete, valid six-Branch catalog,
* the documented EMPTY value (``None`` / ``[]`` / ``False``) for anything
  unresolvable, rather than a raise into the caller (R15.3),
* resolution through the INJECTED registry, with no process-wide
  ``DataRegistry`` singleton installed anywhere in this module (R15.4).

The commitment classes additionally pin the rule down to *ownership of a
completed lab and nothing else*: a suspended, offline, or mid-upgrade lab still
commits its owner (R3.9), a half-built one does not, a destroyed one stops, and
the answer is stored nowhere (R14.6).

The estate classes pin down the two membership rules that make an estate a
*different* question from a commitment and from the building catalog: the
Branch's own lab is a member, and a building still under construction is a
member (R4.7) — so a half-built lab blocks a switch while conferring no
commitment.

The Operational-overlay class pins down the three conjuncts of
``is_operational`` — the untouched ``world.utils.building_is_operational`` gate,
the Active_HQ_Rule (R11.3), and the building's Branch being live (R5.4) — one at
a time, including that a Neutral_Building's answer is the base gate's and that
the util itself keeps answering for the building's own state alone.

The construction-gate classes cover the three callables
``construction_validators`` hands ``BuildingSystem``, on the axes the gates are
*for*: which requests they refuse and which they let through, that every refusal
is a message KEY carrying the structured values its requirement asks be reported
(and not one composed sentence, R13.5), that a report which is not a refusal goes
out as a structured notification while the build proceeds (R4.8, R13.4), and that
an unresolvable input passes the gate rather than raising or blocking (R15.3).

The Reinstatement-bookkeeping class covers the only persisted player state this
feature introduces, on the axes the two attributes exist *for*: that a voluntary
demolition of a Branch's lab sets that Branch's abandoned bit and nothing else
does (R5.5), that a lab lost to hostile destruction writes nothing at all so
rebuilding it restores the Branch with no research (R5.9), that a lab completing
for an abandoned Branch seeds the pending set from the owner's recorded
technologies in that Branch and clears the bit, that the record itself is never
touched (R5.3), and that both writes follow read-copy-write against a hostile
attribute store that discards in-place mutation (R14.7, R15.5).

The recompute-trigger classes cover the four events that can change which Branch
bonuses apply — a Branch_Lab completing, one destroyed, one demolished, and the
owner arriving on another planet — on the axes a trigger is *for*: that each one
asks ``TechLabSystem.recompute_tech_bonuses`` for the right player and planet
(R5.2), that the two that end a commitment also release that Branch's agent roles
and the two that do not end one release nothing (R3.8, R7.8), that only a
Branch_Lab triggers anything at all, that the destruction trigger reads the world
as it will be *after* the delete the event precedes, and that a missing or broken
collaborator degrades to a logged no-op rather than raising into the event bus
(R15.2, R15.3).

Requirements: 1.6, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 4.1, 4.2, 4.3,
4.6, 4.7, 4.8, 5.2, 5.3, 5.4, 5.5, 5.7, 5.9, 6.2, 6.3, 7.8, 11.3, 13.3, 13.4,
13.5, 14.6, 14.7, 14.8, 15.1, 15.2, 15.3, 15.4, 15.5
"""

import unittest
from types import SimpleNamespace

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (  # noqa: E402
    CANONICAL_COUNTER_WEB,
    FIXTURE_BRANCH_BUILDING_ABBR,
    FIXTURE_BUILDING_DICTS,
    FIXTURE_LAB_ABBR,
    FIXTURE_NEUTRAL_ABBRS,
    FIXTURE_TECH_KEYS_BY_BRANCH,
    FIXTURE_TECHNOLOGY_DICTS,
    FakeBuilding,
    FakePlayer,
    fixture_registry,
    make_registry,
)
from mygame.world.constants import (  # noqa: E402
    BRANCH_DOCTRINE,
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    HEADQUARTERS,
)
from mygame.world.constants import (  # noqa: E402
    ATTR_BRANCH_ABANDONED,
    ATTR_BRANCH_REINSTATEMENT,
)
from mygame.world.event_bus import (  # noqa: E402
    BUILDING_DESTROYED,
    CONSTRUCTION_COMPLETED,
    PLAYER_MOVED,
    PLAYER_NOTIFICATION,
    EventBus,
)
from mygame.world.systems.branch_system import (  # noqa: E402
    MSG_BRANCH_LAB_REQUIRED,
    MSG_BRANCH_MISMATCH,
    MSG_BRANCH_SWITCH_BLOCKED,
    MSG_BRANCH_UNLOCK_REQUIRED,
    NOTIFY_BRANCH_DORMANCY,
    UNLOCK_DORMANT,
    UNLOCK_NOT_RESEARCHED,
    UNLOCK_REINSTATEMENT_PENDING,
    BranchRefusal,
    BranchSystem,
)
from mygame.world.systems.tech_system import TechLabSystem  # noqa: E402

#: The keys :meth:`BranchSystem.branch_overview` documents for every entry.
OVERVIEW_KEYS = {
    "branch",
    "doctrine",
    "lab",
    "lab_name",
    "role",
    "operation_kind",
    "buildings",
    "technologies",
    "advantage_over",
    "countered_by",
}

#: Values that must resolve to "no Branch" instead of raising: an unknown name,
#: blank input, and objects that are not definitions at all.
GARBAGE = (None, "", "   ", "ZZ", "not_a_branch", 0, 17, [], {}, object())


#: The planet the commitment fixtures build on, and a second one for the
#: per-planet scoping tests (R3.7).
HOME = "earth"
AWAY = "mars"

#: The fixture's two Neutral_Buildings the Operational overlay needs: the
#: headquarters-capability one the Active_HQ_Rule reads (R11.3), and a plain one
#: that belongs to no Branch. Both are pinned to the capability they carry by
#: ``TestIsOperational.test_the_fixture_hq_carries_the_headquarters_capability``,
#: so a fixture edit fails loudly instead of quietly disarming the overlay tests.
HQ_ABBR = "HQ"
NEUTRAL_ABBR = "WL"


def _system(registry=None) -> BranchSystem:
    """Build a BranchSystem over *registry* (the valid fixture by default)."""
    return BranchSystem(
        registry if registry is not None else fixture_registry(), EventBus()
    )


def _lab(branch, planet=HOME, **flags) -> FakeBuilding:
    """A Branch_Lab of *branch* on *planet*, with the state flags in *flags*.

    ``offline`` / ``under_construction`` / ``upgrading`` stay independent on the
    fake, which is what lets a test assert which flags the commitment answer
    does and does not depend on.
    """
    return FakeBuilding(
        building_type=FIXTURE_LAB_ABBR[branch], planet=planet, **flags
    )


def _branch_building(branch, planet=HOME, **flags) -> FakeBuilding:
    """A non-lab Branch_Building of *branch* on *planet*."""
    return FakeBuilding(
        building_type=FIXTURE_BRANCH_BUILDING_ABBR[branch], planet=planet, **flags
    )


def _hq(planet=HOME, **flags) -> FakeBuilding:
    """The fixture's headquarters-capability Neutral_Building on *planet*.

    What the Active_HQ_Rule (R11.3) reads: a completed one keeps its owner's
    base — and so its owner's Branch_Buildings — live.
    """
    return FakeBuilding(building_type=HQ_ABBR, planet=planet, **flags)


def _neutral(planet=HOME, **flags) -> FakeBuilding:
    """A NON-HQ Neutral_Building on *planet* — no Branch_Affiliation at all."""
    return FakeBuilding(building_type=NEUTRAL_ABBR, planet=planet, **flags)


def _owner(*buildings, planet=HOME) -> FakePlayer:
    """A player standing on *planet* who owns *buildings*."""
    return FakePlayer(buildings=list(buildings), planet=planet)


class TestBranchOfBuilding(unittest.TestCase):
    """``branch_of_building`` resolves an abbreviation or a definition."""

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)

    def test_lab_belongs_to_the_branch_it_hosts(self):
        for branch, abbr in FIXTURE_LAB_ABBR.items():
            with self.subTest(branch=branch):
                self.assertEqual(self.system.branch_of_building(abbr), branch)

    def test_branch_building_reports_its_affiliation(self):
        for branch, abbr in FIXTURE_BRANCH_BUILDING_ABBR.items():
            with self.subTest(branch=branch):
                self.assertEqual(self.system.branch_of_building(abbr), branch)

    def test_neutral_building_has_no_branch(self):
        for abbr in FIXTURE_NEUTRAL_ABBRS:
            with self.subTest(abbr=abbr):
                self.assertIsNone(self.system.branch_of_building(abbr))

    def test_definition_object_resolves_without_a_lookup(self):
        bdef = self.registry.get_building(FIXTURE_LAB_ABBR["bio"])
        self.assertEqual(self.system.branch_of_building(bdef), "bio")

    def test_abbreviation_is_case_insensitive(self):
        self.assertEqual(self.system.branch_of_building("bx"), "bio")

    def test_lab_omitting_the_affiliation_falls_back_to_the_hosted_tree(self):
        # A lab may declare only ``research_tree`` (R2.4 makes ``branch``
        # optional): it still belongs to the Branch it hosts.
        registry = make_registry(buildings=[{
            "name": "Bare Lab",
            "abbreviation": "QQ",
            "cost": {"Iron": 10},
            "max_health": 100,
            "requires_hq": True,
            "required_terrain": None,
            "category": "research",
            "produces": None,
            "capabilities": ["research_lab"],
            "research_tree": "cyber",
            "map_symbol": "QQ",
        }])
        self.assertEqual(_system(registry).branch_of_building("QQ"), "cyber")

    def test_unresolvable_input_answers_none(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertIsNone(self.system.branch_of_building(value))


class TestBranchOfTechnology(unittest.TestCase):
    """``branch_of_technology`` reads the technology's tree."""

    def setUp(self):
        self.system = _system()

    def test_every_fixture_technology_reports_its_branch(self):
        for branch, keys in FIXTURE_TECH_KEYS_BY_BRANCH.items():
            for key in keys:
                with self.subTest(key=key):
                    self.assertEqual(self.system.branch_of_technology(key), branch)

    def test_unknown_key_answers_none(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertIsNone(self.system.branch_of_technology(value))


class TestLabForBranch(unittest.TestCase):
    """``lab_for_branch`` resolves the one lab hosting a Branch."""

    def setUp(self):
        self.system = _system()

    def test_each_branch_resolves_to_its_hosting_lab(self):
        for branch, abbr in FIXTURE_LAB_ABBR.items():
            with self.subTest(branch=branch):
                self.assertEqual(self.system.lab_for_branch(branch), abbr)

    def test_unknown_branch_answers_none(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertIsNone(self.system.lab_for_branch(value))

    def test_empty_catalog_answers_none(self):
        empty = _system(make_registry())
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertIsNone(empty.lab_for_branch(branch))


class TestBranchBuildings(unittest.TestCase):
    """``branch_buildings`` lists the affiliated NON-lab buildings."""

    def setUp(self):
        self.system = _system()

    def test_each_branch_lists_its_branch_building(self):
        for branch, abbr in FIXTURE_BRANCH_BUILDING_ABBR.items():
            with self.subTest(branch=branch):
                self.assertEqual(self.system.branch_buildings(branch), [abbr])

    def test_hosting_lab_is_excluded(self):
        for branch, lab in FIXTURE_LAB_ABBR.items():
            with self.subTest(branch=branch):
                self.assertNotIn(lab, self.system.branch_buildings(branch))

    def test_neutral_buildings_belong_to_no_branch(self):
        listed = {
            abbr
            for branch in BRANCHES
            for abbr in self.system.branch_buildings(branch)
        }
        self.assertTrue(listed.isdisjoint(FIXTURE_NEUTRAL_ABBRS))

    def test_unknown_branch_answers_empty_list(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(self.system.branch_buildings(value), [])

    def test_empty_catalog_answers_empty_list(self):
        empty = _system(make_registry())
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertEqual(empty.branch_buildings(branch), [])


class TestRoleForBranch(unittest.TestCase):
    """``role_for_branch`` is the Branch-to-Carrier_Agent-role bijection."""

    def setUp(self):
        self.system = _system()

    def test_each_branch_owns_its_role(self):
        for branch, role in BRANCH_ROLE.items():
            with self.subTest(branch=branch):
                self.assertEqual(self.system.role_for_branch(branch), role)

    def test_roles_are_distinct_across_the_six_branches(self):
        roles = [self.system.role_for_branch(branch) for branch in BRANCHES]
        self.assertEqual(len(set(roles)), len(BRANCHES))

    def test_unknown_branch_answers_none(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertIsNone(self.system.role_for_branch(value))


class TestBranchOverview(unittest.TestCase):
    """``branch_overview`` is the six-entry catalog projection (R13.3)."""

    def setUp(self):
        self.system = _system()
        self.overview = self.system.branch_overview()

    def test_one_entry_per_branch_in_canonical_order(self):
        self.assertEqual([e["branch"] for e in self.overview], list(BRANCHES))

    def test_every_entry_declares_the_documented_keys(self):
        for entry in self.overview:
            with self.subTest(branch=entry["branch"]):
                self.assertEqual(set(entry), OVERVIEW_KEYS)

    def test_identity_fields_match_the_identity_queries(self):
        for entry in self.overview:
            branch = entry["branch"]
            with self.subTest(branch=branch):
                self.assertEqual(entry["doctrine"], BRANCH_DOCTRINE[branch])
                self.assertEqual(entry["role"], BRANCH_ROLE[branch])
                self.assertEqual(
                    entry["operation_kind"], BRANCH_OPERATION_KIND[branch]
                )
                self.assertEqual(entry["lab"], self.system.lab_for_branch(branch))
                self.assertEqual(
                    entry["buildings"], self.system.branch_buildings(branch)
                )

    def test_technologies_list_the_branchs_own_techs(self):
        for entry in self.overview:
            branch = entry["branch"]
            with self.subTest(branch=branch):
                self.assertEqual(
                    sorted(entry["technologies"]),
                    sorted(FIXTURE_TECH_KEYS_BY_BRANCH[branch]),
                )

    def test_counter_web_is_reported_in_both_directions(self):
        by_branch = {e["branch"]: e for e in self.overview}
        for branch, targets in CANONICAL_COUNTER_WEB.items():
            with self.subTest(branch=branch):
                self.assertEqual(by_branch[branch]["advantage_over"], sorted(targets))
        # The shipped cycle gives each Branch exactly one disadvantage, so no
        # Branch is doubly countered.
        for entry in self.overview:
            with self.subTest(branch=entry["branch"]):
                self.assertEqual(len(entry["countered_by"]), 1)
                self.assertNotIn(entry["branch"], entry["countered_by"])

    def test_empty_catalog_still_describes_all_six_branches(self):
        overview = _system(make_registry()).branch_overview()
        self.assertEqual([e["branch"] for e in overview], list(BRANCHES))
        for entry in overview:
            with self.subTest(branch=entry["branch"]):
                self.assertIsNone(entry["lab"])
                self.assertIsNone(entry["lab_name"])
                self.assertEqual(entry["buildings"], [])
                self.assertEqual(entry["technologies"], [])
                self.assertEqual(entry["advantage_over"], [])
                self.assertEqual(entry["countered_by"], [])
            # Identity that comes from the constants survives an empty catalog.
            self.assertEqual(entry["role"], BRANCH_ROLE[entry["branch"]])

    def test_malformed_counter_web_is_dropped_rather_than_reported(self):
        registry = fixture_registry()
        registry.counter_web = {
            "weapons": ("weapons", "bogus", "defense", "defense"),
            "not_a_branch": ("weapons",),
            42: ("defense",),
        }
        by_branch = {
            e["branch"]: e for e in _system(registry).branch_overview()
        }
        # Self-edge, unknown name, and duplicate all removed; the one legal
        # target survives.
        self.assertEqual(by_branch["weapons"]["advantage_over"], ["defense"])
        self.assertEqual(by_branch["defense"]["countered_by"], ["weapons"])


class TestCommitment(unittest.TestCase):
    """``commitment`` is the owned completed lab's Branch, and nothing else."""

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)

    def test_owned_lab_confers_its_branch(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                player = _owner(_lab(branch))
                self.assertEqual(self.system.commitment(player), branch)

    def test_no_buildings_means_no_commitment(self):
        self.assertIsNone(self.system.commitment(_owner()))

    def test_non_lab_buildings_confer_nothing(self):
        # A Branch_Building of the Branch and a Neutral_Building next to it: a
        # commitment comes from the LAB, so this player is committed to nothing.
        player = _owner(
            _branch_building("bio"),
            FakeBuilding(building_type=FIXTURE_NEUTRAL_ABBRS[0], planet=HOME),
        )
        self.assertIsNone(self.system.commitment(player))

    def test_half_built_lab_confers_no_commitment(self):
        player = _owner(_lab("bio", under_construction=True))
        self.assertIsNone(self.system.commitment(player))

    def test_offline_lab_still_confers_its_branch(self):
        # R3.9: commitment follows ownership, not the Operational state.
        player = _owner(_lab("bio", offline=True))
        self.assertEqual(self.system.commitment(player), "bio")

    def test_mid_upgrade_lab_still_confers_its_branch(self):
        player = _owner(_lab("cyber", upgrading=True))
        self.assertEqual(self.system.commitment(player), "cyber")

    def test_suspended_lab_still_confers_its_branch(self):
        # A Signals intrusion knocks the lab offline mid-upgrade: still owned,
        # still completed, so the Branch's bonuses stay live (R3.9, R5.10).
        player = _owner(_lab("weapons", offline=True, upgrading=True))
        self.assertEqual(self.system.commitment(player), "weapons")

    def test_destroyed_lab_leaves_no_commitment_until_one_completes(self):
        # R3.8, and R4.6's mechanism: a destroyed building leaves the owner's
        # building list, so the next query simply sees no lab.
        lab = _lab("defense")
        player = _owner(lab)
        self.assertEqual(self.system.commitment(player), "defense")

        player.set_buildings([])
        self.assertIsNone(self.system.commitment(player))

        player.set_buildings([_lab("defense")])
        self.assertEqual(self.system.commitment(player), "defense")

    def test_commitment_is_scoped_per_planet(self):
        # R3.7: one player, two planets, two different commitments.
        player = _owner(_lab("bio", planet=HOME), _lab("cyber", planet=AWAY))
        self.assertEqual(self.system.commitment(player, HOME), "bio")
        self.assertEqual(self.system.commitment(player, AWAY), "cyber")
        self.assertIsNone(self.system.commitment(player, "luna"))

    def test_default_planet_is_the_one_the_player_occupies(self):
        player = _owner(_lab("bio", planet=HOME), _lab("cyber", planet=AWAY))
        self.assertEqual(self.system.commitment(player), "bio")
        player.db.coord_planet = AWAY
        self.assertEqual(self.system.commitment(player), "cyber")

    def test_a_lab_on_another_planet_confers_nothing_here(self):
        player = _owner(_lab("bio", planet=AWAY), planet=HOME)
        self.assertIsNone(self.system.commitment(player))

    def test_commitment_stores_no_copy_of_itself(self):
        # R3.1 / R14.6: the buildings ARE the record. Nothing is written, so a
        # fresh system over the same world answers identically — which is what
        # makes a restart unable to desynchronize the answer.
        lab = _lab("resource")
        player = _owner(lab)
        before = (player.attributes.all(), lab.attributes.all())

        self.assertEqual(self.system.commitment(player), "resource")
        self.assertEqual(self.system.commitment(player), "resource")

        self.assertEqual((player.attributes.all(), lab.attributes.all()), before)
        self.assertEqual(_system(self.registry).commitment(player), "resource")

    def test_lab_type_absent_from_the_registry_answers_none(self):
        player = _owner(FakeBuilding(building_type="QQ", planet=HOME))
        self.assertIsNone(self.system.commitment(player))
        # Same world, an empty catalog: the answer comes from the INJECTED
        # registry, so it collapses to None rather than to a stale global one.
        self.assertIsNone(_system(make_registry()).commitment(_owner(_lab("bio"))))

    def test_lab_declaring_only_its_affiliation_still_confers_it(self):
        # A lab may omit the optional ``branch`` field (R2.4); the mirror case —
        # a lab carrying the affiliation and no ``research_tree`` — resolves the
        # same way, so commitment and Branch_Estate membership agree.
        registry = make_registry(buildings=[{
            "name": "Affiliated Lab",
            "abbreviation": "QQ",
            "cost": {"Iron": 10},
            "max_health": 100,
            "requires_hq": True,
            "required_terrain": None,
            "category": "research",
            "produces": None,
            "capabilities": ["research_lab"],
            "branch": "cyber",
            "map_symbol": "QQ",
        }])
        player = _owner(FakeBuilding(building_type="QQ", planet=HOME))
        self.assertEqual(_system(registry).commitment(player), "cyber")

    def test_unresolvable_player_answers_none(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertIsNone(self.system.commitment(value))
                self.assertIsNone(self.system.commitment(value, HOME))


class TestHasCommitment(unittest.TestCase):
    """``has_commitment`` is the "is this Branch live here" predicate."""

    def setUp(self):
        self.system = _system()

    def test_true_only_for_the_committed_branch(self):
        for committed in BRANCHES:
            player = _owner(_lab(committed))
            for branch in BRANCHES:
                with self.subTest(committed=committed, asked=branch):
                    self.assertEqual(
                        self.system.has_commitment(player, branch),
                        branch == committed,
                    )

    def test_no_commitment_matches_no_branch(self):
        player = _owner(_branch_building("bio"))
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertFalse(self.system.has_commitment(player, branch))

    def test_planet_scoped_like_commitment(self):
        player = _owner(_lab("bio", planet=HOME), _lab("cyber", planet=AWAY))
        self.assertTrue(self.system.has_commitment(player, "bio", HOME))
        self.assertFalse(self.system.has_commitment(player, "bio", AWAY))
        self.assertTrue(self.system.has_commitment(player, "cyber", AWAY))

    def test_unresolvable_branch_is_false(self):
        player = _owner(_lab("bio"))
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertFalse(self.system.has_commitment(player, value))

    def test_unresolvable_player_is_false(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertFalse(self.system.has_commitment(value, "bio"))


class TestEstate(unittest.TestCase):
    """``estate`` is the planet-scoped scan over the owner's buildings (R4.7)."""

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)

    def test_branch_building_is_a_member(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                building = _branch_building(branch)
                player = _owner(building)
                self.assertEqual(self.system.estate(player, branch), [building])

    def test_the_branchs_own_lab_is_a_member(self):
        # The deliberate difference from ``branch_buildings``, the catalog
        # query, which excludes the lab.
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                lab = _lab(branch)
                player = _owner(lab)
                self.assertEqual(self.system.estate(player, branch), [lab])
                self.assertNotIn(
                    FIXTURE_LAB_ABBR[branch], self.system.branch_buildings(branch)
                )

    def test_lab_and_branch_buildings_are_counted_together(self):
        lab = _lab("bio")
        works = _branch_building("bio")
        player = _owner(lab, works)
        self.assertEqual(self.system.estate(player, "bio"), [lab, works])

    def test_building_under_construction_is_a_member(self):
        # R4.7: a half-built Branch_Building blocks a switch.
        building = _branch_building("bio", under_construction=True)
        player = _owner(building)
        self.assertEqual(self.system.estate(player, "bio"), [building])

    def test_half_built_lab_is_a_member_while_conferring_no_commitment(self):
        # The estate and the commitment answer differently on purpose: the
        # estate needs the building to exist, the commitment needs it finished.
        lab = _lab("bio", under_construction=True)
        player = _owner(lab)
        self.assertEqual(self.system.estate(player, "bio"), [lab])
        self.assertIsNone(self.system.commitment(player))

    def test_offline_and_mid_upgrade_buildings_are_members(self):
        offline = _branch_building("cyber", offline=True)
        upgrading = _branch_building("cyber", upgrading=True)
        player = _owner(offline, upgrading)
        self.assertEqual(self.system.estate(player, "cyber"), [offline, upgrading])

    def test_neutral_buildings_belong_to_no_estate(self):
        player = _owner(
            *(FakeBuilding(building_type=abbr, planet=HOME)
              for abbr in FIXTURE_NEUTRAL_ABBRS)
        )
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertEqual(self.system.estate(player, branch), [])

    def test_other_branches_are_excluded(self):
        bio = _branch_building("bio")
        cyber = _branch_building("cyber")
        player = _owner(bio, cyber, _lab("weapons"))
        self.assertEqual(self.system.estate(player, "bio"), [bio])
        self.assertEqual(self.system.estate(player, "cyber"), [cyber])
        self.assertEqual(self.system.estate(player, "defense"), [])

    def test_estate_is_scoped_per_planet(self):
        here = _branch_building("bio", planet=HOME)
        there = _branch_building("bio", planet=AWAY)
        player = _owner(here, there)
        self.assertEqual(self.system.estate(player, "bio", HOME), [here])
        self.assertEqual(self.system.estate(player, "bio", AWAY), [there])
        self.assertEqual(self.system.estate(player, "bio", "luna"), [])

    def test_default_planet_is_the_one_the_player_occupies(self):
        here = _branch_building("bio", planet=HOME)
        there = _branch_building("bio", planet=AWAY)
        player = _owner(here, there)
        self.assertEqual(self.system.estate(player, "bio"), [here])
        player.db.coord_planet = AWAY
        self.assertEqual(self.system.estate(player, "bio"), [there])

    def test_building_with_no_resolvable_planet_counts_everywhere(self):
        # Matches ``owner_research_lab``: an undeterminable planet is the
        # wildcard, so an estate never loses a building to an unreadable room.
        homeless = _branch_building("bio", planet=None)
        player = _owner(homeless)
        for planet in (HOME, AWAY, None):
            with self.subTest(planet=planet):
                self.assertEqual(self.system.estate(player, "bio", planet), [homeless])

    def test_destruction_shrinks_the_estate_like_a_demolition(self):
        # R4.6 / R4.3: nothing counts destructions — the razed building has left
        # the owner's roster, so the next query is simply shorter, and an
        # emptied estate stops blocking a switch.
        first, second = _branch_building("bio"), _branch_building("bio")
        player = _owner(first, second)
        self.assertEqual(self.system.estate(player, "bio"), [first, second])

        player.set_buildings([second])
        self.assertEqual(self.system.estate(player, "bio"), [second])

        player.set_buildings([])
        self.assertEqual(self.system.estate(player, "bio"), [])

    def test_estate_stores_no_copy_of_itself(self):
        # R14.6: the buildings ARE the record, so a fresh system over the same
        # world answers identically and nothing is written anywhere.
        building = _branch_building("resource")
        player = _owner(building)
        before = (player.attributes.all(), building.attributes.all())

        self.assertEqual(self.system.estate(player, "resource"), [building])
        self.assertEqual(self.system.estate(player, "resource"), [building])

        self.assertEqual(
            (player.attributes.all(), building.attributes.all()), before
        )
        self.assertEqual(
            _system(self.registry).estate(player, "resource"), [building]
        )

    def test_returned_list_is_not_shared_between_calls(self):
        player = _owner(_branch_building("bio"))
        first = self.system.estate(player, "bio")
        first.clear()
        self.assertEqual(len(self.system.estate(player, "bio")), 1)

    def test_building_type_absent_from_the_registry_is_no_members(self):
        player = _owner(FakeBuilding(building_type="QQ", planet=HOME))
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertEqual(self.system.estate(player, branch), [])

    def test_membership_comes_from_the_injected_registry(self):
        # Same world, an empty catalog: no definition resolves, so no building
        # belongs to any estate (R15.4).
        empty = _system(make_registry())
        player = _owner(_lab("bio"), _branch_building("bio"))
        self.assertEqual(empty.estate(player, "bio"), [])
        self.assertEqual(self.system.estate(player, "bio"), player.get_buildings())

    def test_unresolvable_branch_answers_empty_list(self):
        player = _owner(_lab("bio"), _branch_building("bio"))
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(self.system.estate(player, value), [])

    def test_unresolvable_player_answers_empty_list(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(self.system.estate(value, "bio"), [])
                self.assertEqual(self.system.estate(value, "bio", HOME), [])


class TestEstateCount(unittest.TestCase):
    """``estate_count`` is the number the demolish and switch reports quote."""

    def setUp(self):
        self.system = _system()

    def test_count_matches_the_member_list(self):
        player = _owner(
            _lab("bio"),
            _branch_building("bio"),
            _branch_building("bio", under_construction=True),
            _branch_building("cyber"),
            FakeBuilding(building_type=FIXTURE_NEUTRAL_ABBRS[0], planet=HOME),
        )
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertEqual(
                    self.system.estate_count(player, branch),
                    len(self.system.estate(player, branch)),
                )
        self.assertEqual(self.system.estate_count(player, "bio"), 3)
        self.assertEqual(self.system.estate_count(player, "cyber"), 1)
        self.assertEqual(self.system.estate_count(player, "defense"), 0)

    def test_count_is_planet_scoped(self):
        player = _owner(
            _branch_building("bio", planet=HOME),
            _branch_building("bio", planet=AWAY),
            _branch_building("bio", planet=AWAY),
        )
        self.assertEqual(self.system.estate_count(player, "bio", HOME), 1)
        self.assertEqual(self.system.estate_count(player, "bio", AWAY), 2)

    def test_unresolvable_input_answers_zero(self):
        player = _owner(_branch_building("bio"))
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(self.system.estate_count(player, value), 0)
                self.assertEqual(self.system.estate_count(value, "bio"), 0)


class TestConflictingEstates(unittest.TestCase):
    """``conflicting_estates`` is what stands between a player and a switch."""

    def setUp(self):
        self.system = _system()

    def test_nothing_owned_conflicts_with_nothing(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertEqual(
                    self.system.conflicting_estates(_owner(), HOME, branch), {}
                )

    def test_the_incoming_branchs_own_estate_never_conflicts(self):
        # R4.3: a player's own buildings in the Branch being committed to are
        # not in the way, so a rebuilt lab of the same Branch is permitted.
        player = _owner(_lab("bio"), _branch_building("bio"))
        self.assertEqual(self.system.conflicting_estates(player, HOME, "bio"), {})

    def test_neutral_buildings_never_conflict(self):
        player = _owner(
            *(FakeBuilding(building_type=abbr, planet=HOME)
              for abbr in FIXTURE_NEUTRAL_ABBRS)
        )
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertEqual(
                    self.system.conflicting_estates(player, HOME, branch), {}
                )

    def test_another_branchs_estate_conflicts_with_its_members(self):
        lab = _lab("bio")
        works = _branch_building("bio")
        player = _owner(lab, works)
        self.assertEqual(
            self.system.conflicting_estates(player, HOME, "cyber"),
            {"bio": [lab, works]},
        )

    def test_every_other_branch_is_reported_in_canonical_order(self):
        owned = {branch: _branch_building(branch) for branch in BRANCHES}
        player = _owner(*owned.values())
        conflicts = self.system.conflicting_estates(player, HOME, "bio")
        self.assertEqual(
            list(conflicts), [b for b in BRANCHES if b != "bio"]
        )
        for branch, members in conflicts.items():
            with self.subTest(branch=branch):
                self.assertEqual(members, [owned[branch]])

    def test_a_building_under_construction_conflicts(self):
        # R4.7 read through the gate's own question: a half-built building of
        # another Branch is enough to block the switch.
        building = _branch_building("weapons", under_construction=True)
        player = _owner(building)
        self.assertEqual(
            self.system.conflicting_estates(player, HOME, "bio"),
            {"weapons": [building]},
        )

    def test_conflicts_are_planet_scoped(self):
        here = _branch_building("weapons", planet=HOME)
        there = _branch_building("defense", planet=AWAY)
        player = _owner(here, there)
        self.assertEqual(
            self.system.conflicting_estates(player, HOME, "bio"),
            {"weapons": [here]},
        )
        self.assertEqual(
            self.system.conflicting_estates(player, AWAY, "bio"),
            {"defense": [there]},
        )

    def test_default_planet_is_the_one_the_player_occupies(self):
        here = _branch_building("weapons", planet=HOME)
        there = _branch_building("defense", planet=AWAY)
        player = _owner(here, there)
        self.assertEqual(
            self.system.conflicting_estates(player, None, "bio"),
            {"weapons": [here]},
        )
        player.db.coord_planet = AWAY
        self.assertEqual(
            self.system.conflicting_estates(player, None, "bio"),
            {"defense": [there]},
        )

    def test_emptying_the_conflicting_estate_frees_the_switch(self):
        # R4.3: the gate's answer changes the moment the last blocker goes.
        blocker = _branch_building("weapons")
        player = _owner(blocker)
        self.assertTrue(self.system.conflicting_estates(player, HOME, "bio"))
        player.set_buildings([])
        self.assertEqual(self.system.conflicting_estates(player, HOME, "bio"), {})

    def test_members_agree_with_the_estate_query(self):
        player = _owner(_lab("weapons"), _branch_building("weapons"),
                        _branch_building("defense"))
        conflicts = self.system.conflicting_estates(player, HOME, "bio")
        for branch, members in conflicts.items():
            with self.subTest(branch=branch):
                self.assertEqual(members, self.system.estate(player, branch, HOME))
                self.assertEqual(
                    len(members), self.system.estate_count(player, branch, HOME)
                )

    def test_unresolvable_incoming_branch_answers_empty_map(self):
        # No Branch_Lab is being requested, so nothing is in the way (R15.3).
        player = _owner(_branch_building("weapons"))
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(
                    self.system.conflicting_estates(player, HOME, value), {}
                )

    def test_unresolvable_player_answers_empty_map(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(
                    self.system.conflicting_estates(value, HOME, "bio"), {}
                )


class TestIsOperational(unittest.TestCase):
    """``is_operational`` is the base gate AND the Active_HQ_Rule AND a live Branch.

    The three conjuncts are tested one at a time — each held true while the
    others are falsified — plus the two rules that make the overlay an overlay:
    ``world.utils.building_is_operational`` is left unmodified (so a dormant
    building still reads Operational through the util while reading
    non-Operational here), and a Neutral_Building's answer is the base gate's
    (R5.4, R11.3).
    """

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)

    def _committed(self, branch, *owned, planet=HOME, hq=True):
        """An owner committed to *branch* on *planet*, owning *owned* (plus an HQ)."""
        buildings = list(owned) + [_lab(branch, planet=planet)]
        if hq:
            buildings.append(_hq(planet))
        return _owner(*buildings, planet=planet)

    def test_the_fixture_carries_the_capabilities_the_overlay_reads(self):
        # Guard: if the fixture's HQ stopped declaring the capability, every
        # Active_HQ_Rule assertion below would pass for the wrong reason.
        self.assertTrue(
            self.registry.get_building(HQ_ABBR).has_capability(HEADQUARTERS)
        )
        self.assertFalse(
            self.registry.get_building(NEUTRAL_ABBR).has_capability(HEADQUARTERS)
        )
        self.assertIsNone(self.system.branch_of_building(NEUTRAL_ABBR))

    def test_a_live_branch_building_of_the_committed_branch_is_operational(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                building = _branch_building(branch)
                self._committed(branch, building)
                self.assertTrue(self.system.is_operational(building))

    def test_a_live_lab_is_operational_under_its_own_commitment(self):
        # The lab is a member of the Branch it hosts, so the overlay's third
        # conjunct is satisfied by the very building that satisfies it.
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                lab = _lab(branch)
                _owner(lab, _hq())
                self.assertTrue(self.system.is_operational(lab))

    def test_the_base_gate_still_decides(self):
        for flags in ({"offline": True}, {"under_construction": True},
                      {"offline": True, "under_construction": True}):
            with self.subTest(**flags):
                building = _branch_building("bio", **flags)
                self._committed("bio", building)
                self.assertFalse(self.system.is_operational(building))

    def test_a_neutral_building_answers_the_base_gate_alone(self):
        # The overlay adds no Branch condition to a building that belongs to no
        # Branch, whatever its owner is committed to — including nothing.
        for commitment in ("bio", None):
            with self.subTest(commitment=commitment):
                live, offline = _neutral(), _neutral(offline=True)
                if commitment is None:
                    _owner(live, offline, _hq())
                else:
                    self._committed(commitment, live, offline)
                self.assertTrue(self.system.is_operational(live))
                self.assertFalse(self.system.is_operational(offline))

    def test_a_dormant_branch_building_reports_non_operational(self):
        # R5.4: the owner holds no commitment matching the affiliation, so the
        # building performs no capability behaviour.
        uncommitted = _branch_building("bio")
        _owner(uncommitted, _hq())
        self.assertFalse(self.system.is_operational(uncommitted))

        mismatched = _branch_building("bio")
        self._committed("cyber", mismatched)
        self.assertFalse(self.system.is_operational(mismatched))

    def test_the_overlay_follows_the_lab_in_and_out_of_existence(self):
        # Derived, never stored: completing a lab makes the estate live and
        # losing it makes the estate dormant, with nothing to recompute (R3.8).
        building = _branch_building("bio")
        player = _owner(building, _hq())
        self.assertFalse(self.system.is_operational(building))

        player.set_buildings([building, _hq(), _lab("bio")])
        self.assertTrue(self.system.is_operational(building))

        player.set_buildings([building, _hq()])
        self.assertFalse(self.system.is_operational(building))

    def test_a_suspended_lab_keeps_its_branchs_buildings_operational(self):
        # R3.9 / R5.10 read through the overlay: an offline, mid-upgrade lab
        # still commits its owner, so the Branch stays live for the buildings
        # that depend on it — while the lab itself is inert by the base gate.
        building = _branch_building("weapons")
        lab = _lab("weapons", offline=True, upgrading=True)
        _owner(building, lab, _hq())
        self.assertTrue(self.system.is_operational(building))
        self.assertFalse(self.system.is_operational(lab))

    def test_the_overlay_is_scoped_per_planet(self):
        here = _branch_building("bio", planet=HOME)
        there = _branch_building("bio", planet=AWAY)
        _owner(here, there, _lab("bio", planet=HOME), _hq(HOME), _hq(AWAY))
        self.assertTrue(self.system.is_operational(here))
        self.assertFalse(self.system.is_operational(there))

    def test_the_active_hq_rule_gates_a_branch_building(self):
        # R11.3: no completed headquarters on the planet, no operating base.
        building = _branch_building("bio")
        player = self._committed("bio", building, hq=False)
        self.assertFalse(self.system.is_operational(building))

        owned = player.get_buildings()
        player.set_buildings(owned + [_hq(under_construction=True)])
        self.assertFalse(self.system.is_operational(building))   # half-built HQ

        player.set_buildings(owned + [_hq(AWAY)])
        self.assertFalse(self.system.is_operational(building))   # wrong planet

        player.set_buildings(owned + [_hq(HOME)])
        self.assertTrue(self.system.is_operational(building))

    def test_the_underlying_util_is_left_unmodified(self):
        # The overlay is additive: the shared value-based gate keeps answering
        # for the building's own state alone, so every pre-feature caller is
        # unaffected and only the consumers that ask this system see the Branch
        # and headquarters conditions.
        from mygame.world.utils import building_is_operational

        dormant = _branch_building("bio")
        _owner(dormant, _hq())
        headless = _branch_building("bio")
        self._committed("bio", headless, hq=False)

        for building in (dormant, headless):
            with self.subTest(building=building):
                self.assertTrue(building_is_operational(building))
                self.assertFalse(self.system.is_operational(building))

    def test_an_npc_base_is_judged_on_the_same_terms(self):
        # R11.6's precondition: a base template that fields a Branch's lab and
        # buildings (``BaseTemplateDef.branch``) operates them exactly as a
        # player does — the Sentinel owner enumerates buildings like any owner.
        building = _branch_building("defense")
        sentinel = _owner(building, _lab("defense"), _hq())
        sentinel.db.is_sentinel = True
        self.assertTrue(self.system.is_operational(building))

        sentinel.set_buildings([building, _hq()])
        self.assertFalse(self.system.is_operational(building))

    def test_an_ownerless_building_is_not_operational(self):
        # Nothing to read a commitment or a headquarters from, so the documented
        # empty answer is False rather than a raise (R15.3).
        self.assertFalse(
            self.system.is_operational(_branch_building("bio"))
        )
        self.assertFalse(self.system.is_operational(_neutral()))

    def test_unresolvable_input_answers_false(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertFalse(self.system.is_operational(value))

    def test_the_overlay_writes_nothing(self):
        building = _branch_building("bio")
        player = self._committed("bio", building)
        before = (player.attributes.all(), building.attributes.all())

        self.assertTrue(self.system.is_operational(building))
        self.assertTrue(self.system.is_operational(building))

        self.assertEqual(
            (player.attributes.all(), building.attributes.all()), before
        )


class TestDormantBranches(unittest.TestCase):
    """``dormant_branches`` counts the record held outside the commitment (R13.2).

    A Branch is dormant for a player when the record holds technologies in it but
    the player holds no commitment for it on the planet in question — so the
    count is the size of the history kept in a doctrine that is not running. The
    technology view quotes these counts beside the Reinstatement cost fraction.
    """

    def setUp(self):
        self.system = _system()

    def _recorder(self, *branches, buildings=(), planet=HOME):
        """A player on *planet* owning *buildings*, recorded in *branches*."""
        player = _owner(*buildings, planet=planet)
        player.db.researched_techs = {
            key
            for branch in branches
            for key in FIXTURE_TECH_KEYS_BY_BRANCH[branch]
        }
        return player

    def test_the_committed_branch_is_not_dormant(self):
        player = self._recorder("weapons", buildings=[_lab("weapons")])

        self.assertEqual(self.system.dormant_branches(player), {})

    def test_every_other_recorded_branch_is_dormant_with_its_count(self):
        player = self._recorder(
            "weapons", "bio", "cyber", buildings=[_lab("weapons")]
        )

        self.assertEqual(
            self.system.dormant_branches(player),
            {"bio": 2, "cyber": 2},
        )

    def test_no_commitment_leaves_every_recorded_branch_dormant(self):
        player = self._recorder("weapons", "research")

        self.assertEqual(
            self.system.dormant_branches(player),
            {"weapons": 2, "research": 2},
        )

    def test_the_counts_come_back_in_canonical_branch_order(self):
        player = self._recorder(*reversed(BRANCHES))

        self.assertEqual(list(self.system.dormant_branches(player)), list(BRANCHES))

    def test_a_branch_with_no_record_is_not_reported_at_all(self):
        player = self._recorder("bio", buildings=[_lab("weapons")])

        self.assertEqual(self.system.dormant_branches(player), {"bio": 2})

    def test_dormancy_is_scoped_to_the_planet_asked_about(self):
        # R3.7: the same record is dormant where the lab is not.
        player = self._recorder("weapons", buildings=[_lab("weapons", planet=HOME)])

        self.assertEqual(self.system.dormant_branches(player, HOME), {})
        self.assertEqual(
            self.system.dormant_branches(player, AWAY), {"weapons": 2}
        )

    def test_an_empty_record_reports_nothing_dormant(self):
        self.assertEqual(self.system.dormant_branches(_owner()), {})

    def test_a_suspended_lab_keeps_its_branch_out_of_dormancy(self):
        # R3.9/R5.10: commitment follows ownership, not Operational state.
        player = self._recorder(
            "weapons",
            buildings=[_lab("weapons", offline=True, upgrading=True)],
        )

        self.assertEqual(self.system.dormant_branches(player), {})

    def test_unresolvable_input_answers_empty(self):
        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertEqual(self.system.dormant_branches(value), {})

    def test_the_query_writes_nothing(self):
        player = self._recorder("weapons", "bio", buildings=[_lab("weapons")])
        before = player.attributes.all()

        self.system.dormant_branches(player)
        self.system.dormant_branches(player)

        self.assertEqual(player.attributes.all(), before)


class _Tile:
    """A target tile that reports its planet the way a PlanetRoom does."""

    def __init__(self, planet=HOME):
        self.planet_name = planet


class _DBTile:
    """A target tile carrying its planet on ``db.planet`` instead."""

    def __init__(self, planet=HOME):
        self.db = SimpleNamespace(planet=planet)


#: A Neutral_Building gated behind a ``weapons`` technology. Legal data: the
#: schema only requires an unlocking technology to EXIST when the building
#: declares no Branch (validator rule 7), and it is the one shape that reaches
#: the unlock gate while the affiliation gate has nothing to say — so it is how
#: the "researched but dormant" half of R6.2 is exercised in isolation.
GATED_NEUTRAL_ABBR = "ZN"
GATED_NEUTRAL_DICT = {
    "name": "Gated Depot",
    "abbreviation": GATED_NEUTRAL_ABBR,
    "cost": {"Iron": 20},
    "max_health": 200,
    "requires_hq": True,
    "required_terrain": None,
    "category": "utility",
    "produces": None,
    "capabilities": [],
    "unlock_technology": FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0],
    "rank_requirement": 1,
    "map_symbol": GATED_NEUTRAL_ABBR,
}


def _gated_neutral_registry():
    """The fixture catalog plus one tech-gated Neutral_Building."""
    return make_registry(
        buildings=FIXTURE_BUILDING_DICTS + (GATED_NEUTRAL_DICT,),
        technologies=FIXTURE_TECHNOLOGY_DICTS,
    )


class _FakeTechSystem:
    """A tech system exposing the researched record through an accessor."""

    def __init__(self, recorded=(), explode=False):
        self.recorded = set(recorded)
        self.explode = explode
        self.calls = 0

    def researched_techs(self, player):
        self.calls += 1
        if self.explode:
            raise RuntimeError("tech system exploded")
        return set(self.recorded)


def _gates(system):
    """Return the three gates by name, as ``construction_validators`` orders them."""
    affiliation, switch, unlock = system.construction_validators()
    return affiliation, switch, unlock


def _capture(bus):
    """Subscribe to PLAYER_NOTIFICATION on *bus* and return the captured list."""
    seen = []
    bus.subscribe(
        PLAYER_NOTIFICATION,
        lambda event_name="", player=None, kind="", data=None, **_kw: seen.append(
            (player, kind, dict(data or {}))
        ),
    )
    return seen


class TestConstructionValidators(unittest.TestCase):
    """``construction_validators`` is the whole surface BuildingSystem needs."""

    def setUp(self):
        self.system = _system()

    def test_three_gates_in_the_documented_chain_order(self):
        self.assertEqual(
            [gate.__name__ for gate in self.system.construction_validators()],
            [
                "_validate_branch_affiliation",
                "_validate_branch_switch",
                "_validate_unlock_technology",
            ],
        )

    def test_each_call_returns_a_fresh_list(self):
        first = self.system.construction_validators()
        first.clear()
        self.assertEqual(len(self.system.construction_validators()), 3)

    def test_every_gate_accepts_the_chains_validator_signature(self):
        # The splice is ``lambda: gate(player, building_def, tile, x=x, y=y)`` —
        # every gate must take exactly that, so 5.2 needs no per-gate wrapper.
        player = _owner(_lab("bio"))
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["bio"][0]}
        bdef = self.system.registry.get_building(FIXTURE_BRANCH_BUILDING_ABBR["bio"])
        for gate in self.system.construction_validators():
            with self.subTest(gate=gate.__name__):
                self.assertIsNone(gate(player, bdef, _Tile(), x=3, y=4))

    def test_every_gate_passes_a_neutral_building(self):
        # R2.5: a building that shipped before this feature is unaffected by
        # commitment, under every commitment and under none.
        for abbr in FIXTURE_NEUTRAL_ABBRS:
            for holder in (_owner(), *(_owner(_lab(b)) for b in BRANCHES)):
                with self.subTest(abbr=abbr, commitment=self.system.commitment(holder)):
                    for gate in self.system.construction_validators():
                        self.assertIsNone(gate(holder, abbr, _Tile()))

    def test_every_gate_answers_none_for_unresolvable_input(self):
        # R15.3: a garbage request is not this system's to refuse — it neither
        # raises nor blocks a build.
        for value in GARBAGE:
            with self.subTest(value=value):
                for gate in self.system.construction_validators():
                    self.assertIsNone(gate(_owner(), value, _Tile()))
                    self.assertIsNone(gate(value, value, value))


class TestBranchRefusal(unittest.TestCase):
    """A refusal is the message key, and it carries the structured data."""

    def test_the_string_value_is_the_message_key(self):
        refusal = BranchRefusal("some_key", count=2)
        self.assertIsInstance(refusal, str)
        self.assertEqual(refusal, "some_key")
        self.assertEqual(refusal.key, "some_key")
        self.assertEqual(str(refusal), "some_key")

    def test_a_refusal_is_truthy_so_the_existing_chain_refuses(self):
        self.assertTrue(BranchRefusal("some_key"))

    def test_the_chain_may_still_concatenate_it(self):
        self.assertEqual(BranchRefusal("k") + " [note]", "k [note]")

    def test_the_payload_is_reachable_and_unshared(self):
        first = BranchRefusal("k", count=1)
        second = BranchRefusal("k", count=2)
        self.assertEqual(first.data, {"count": 1})
        self.assertEqual(second.data, {"count": 2})
        self.assertIsNot(first.data, second.data)


class TestBranchAffiliationGate(unittest.TestCase):
    """The gate that requires a Branch_Building's Branch to be live (R3.3-3.5)."""

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)
        self.gate = _gates(self.system)[0]

    def test_matching_commitment_permits_the_build(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                player = _owner(_lab(branch))
                self.assertIsNone(
                    self.gate(
                        player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile()
                    )
                )

    def test_no_commitment_reports_the_lab_that_unlocks_it(self):
        # R3.4: the player is told which Branch_Lab to build.
        player = _owner()
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                refusal = self.gate(
                    player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile()
                )
                self.assertEqual(refusal, MSG_BRANCH_LAB_REQUIRED)
                self.assertEqual(refusal.data["required_branch"], branch)
                self.assertEqual(
                    refusal.data["required_lab"], FIXTURE_LAB_ABBR[branch]
                )
                self.assertEqual(
                    refusal.data["required_doctrine"], BRANCH_DOCTRINE[branch]
                )
                self.assertIsNone(refusal.data["current_branch"])
                self.assertEqual(
                    refusal.data["building"], FIXTURE_BRANCH_BUILDING_ABBR[branch]
                )

    def test_a_different_commitment_reports_both_branches(self):
        # R3.5: both the Branch held and the Branch required.
        player = _owner(_lab("bio"))
        refusal = self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["cyber"], _Tile())
        self.assertEqual(refusal, MSG_BRANCH_MISMATCH)
        self.assertEqual(refusal.data["current_branch"], "bio")
        self.assertEqual(refusal.data["current_doctrine"], BRANCH_DOCTRINE["bio"])
        self.assertEqual(refusal.data["required_branch"], "cyber")
        self.assertEqual(refusal.data["required_lab"], FIXTURE_LAB_ABBR["cyber"])

    def test_a_refusal_composes_no_prose(self):
        # R13.5: the value is a key, not a sentence — no spaces, no punctuation.
        refusals = [
            self.gate(_owner(), FIXTURE_BRANCH_BUILDING_ABBR["bio"], _Tile()),
            self.gate(
                _owner(_lab("bio")), FIXTURE_BRANCH_BUILDING_ABBR["cyber"], _Tile()
            ),
        ]
        for refusal in refusals:
            with self.subTest(refusal=refusal):
                self.assertNotIn(" ", refusal)
                self.assertEqual(refusal, refusal.key)

    def test_a_branch_lab_is_never_this_gates_business(self):
        # A lab CREATES a commitment, so gating it on holding one would make the
        # first commitment impossible.
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertIsNone(
                    self.gate(_owner(), FIXTURE_LAB_ABBR[branch], _Tile())
                )
                self.assertIsNone(
                    self.gate(
                        _owner(_lab("bio")), FIXTURE_LAB_ABBR[branch], _Tile()
                    )
                )

    def test_a_definition_object_and_an_abbreviation_agree(self):
        player = _owner()
        bdef = self.registry.get_building(FIXTURE_BRANCH_BUILDING_ABBR["bio"])
        by_def = self.gate(player, bdef, _Tile())
        by_abbr = self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _Tile())
        self.assertEqual(by_def, by_abbr)
        self.assertEqual(by_def.data, by_abbr.data)

    def test_the_gate_is_scoped_to_the_target_planet(self):
        # A commitment on one planet unlocks nothing on another (R3.7).
        player = _owner(_lab("bio", planet=HOME), planet=HOME)
        self.assertIsNone(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _Tile(HOME))
        )
        refusal = self.gate(
            player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _Tile(AWAY)
        )
        self.assertEqual(refusal, MSG_BRANCH_LAB_REQUIRED)
        self.assertEqual(refusal.data["planet"], AWAY)

    def test_the_planet_is_read_from_the_tiles_db_too(self):
        player = _owner(_lab("bio", planet=HOME), planet=HOME)
        self.assertIsNone(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _DBTile(HOME))
        )
        self.assertEqual(
            self.gate(
                player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _DBTile(AWAY)
            ).data["planet"],
            AWAY,
        )

    def test_an_unreadable_tile_falls_back_to_the_players_planet(self):
        player = _owner(_lab("bio", planet=HOME), planet=HOME)
        for tile in (None, object()):
            with self.subTest(tile=tile):
                self.assertIsNone(
                    self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], tile)
                )

    def test_a_suspended_lab_still_unlocks_its_branchs_buildings(self):
        # R3.9 / R5.10: commitment follows ownership, not the Operational state.
        player = _owner(_lab("bio", offline=True, upgrading=True))
        self.assertIsNone(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _Tile())
        )

    def test_a_half_built_lab_unlocks_nothing_yet(self):
        player = _owner(_lab("bio", under_construction=True))
        self.assertEqual(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["bio"], _Tile()),
            MSG_BRANCH_LAB_REQUIRED,
        )

    def test_the_gate_writes_nothing(self):
        player = _owner(_lab("bio"))
        before = player.attributes.all()
        self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR["cyber"], _Tile())
        self.assertEqual(player.attributes.all(), before)


class TestBranchSwitchGate(unittest.TestCase):
    """The gate that makes abandoning a Branch cost a teardown (R4.1, R4.2, R4.8)."""

    def setUp(self):
        self.registry = fixture_registry()
        self.bus = EventBus()
        self.system = BranchSystem(self.registry, self.bus)
        self.gate = _gates(self.system)[1]
        self.seen = _capture(self.bus)

    def test_a_non_lab_building_is_not_this_gates_business(self):
        player = _owner(_branch_building("weapons"))
        for abbr in FIXTURE_BRANCH_BUILDING_ABBR.values():
            with self.subTest(abbr=abbr):
                self.assertIsNone(self.gate(player, abbr, _Tile()))
        for abbr in FIXTURE_NEUTRAL_ABBRS:
            with self.subTest(abbr=abbr):
                self.assertIsNone(self.gate(player, abbr, _Tile()))

    def test_a_first_lab_on_a_clean_planet_passes_silently(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                self.assertIsNone(
                    self.gate(_owner(), FIXTURE_LAB_ABBR[branch], _Tile())
                )
        self.assertEqual(self.seen, [])

    def test_a_conflicting_estate_reports_its_count_and_its_members(self):
        # R4.1: the count. R4.2: each blocking building's abbreviation and coords.
        first = _branch_building("weapons", x=3, y=4)
        second = _branch_building("weapons", x=7, y=1)
        player = _owner(first, second)

        refusal = self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())

        self.assertEqual(refusal, MSG_BRANCH_SWITCH_BLOCKED)
        self.assertEqual(refusal.data["count"], 2)
        self.assertEqual(refusal.data["branches"], ["weapons"])
        self.assertEqual(refusal.data["counts"], {"weapons": 2})
        self.assertEqual(
            refusal.data["blocking"],
            [
                {
                    "branch": "weapons",
                    "building": FIXTURE_BRANCH_BUILDING_ABBR["weapons"],
                    "building_name": self.registry.get_building(
                        FIXTURE_BRANCH_BUILDING_ABBR["weapons"]
                    ).name,
                    "x": 3,
                    "y": 4,
                },
                {
                    "branch": "weapons",
                    "building": FIXTURE_BRANCH_BUILDING_ABBR["weapons"],
                    "building_name": self.registry.get_building(
                        FIXTURE_BRANCH_BUILDING_ABBR["weapons"]
                    ).name,
                    "x": 7,
                    "y": 1,
                },
            ],
        )
        self.assertEqual(refusal.data["incoming_branch"], "bio")

    def test_the_blocking_count_matches_the_estate_count(self):
        player = _owner(
            _lab("weapons"),
            _branch_building("weapons"),
            _branch_building("defense", under_construction=True),
        )
        refusal = self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())
        self.assertEqual(refusal.data["count"], 3)
        self.assertEqual(
            refusal.data["counts"],
            {
                "weapons": self.system.estate_count(player, "weapons", HOME),
                "defense": self.system.estate_count(player, "defense", HOME),
            },
        )
        # Canonical Branch order, so the report reads the same way every time.
        self.assertEqual(refusal.data["branches"], ["weapons", "defense"])

    def test_a_half_built_building_blocks_the_switch(self):
        # R4.7 through the gate: a partially built building is still a blocker.
        blocker = _branch_building("cyber", under_construction=True, x=2, y=2)
        player = _owner(blocker)
        refusal = self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())
        self.assertEqual(refusal.data["count"], 1)
        self.assertEqual(refusal.data["blocking"][0]["x"], 2)

    def test_the_incoming_branchs_own_buildings_never_block(self):
        # R4.3 through the gate: rebuilding a destroyed lab is permitted.
        player = _owner(_branch_building("bio"), _branch_building("bio"))
        self.assertIsNone(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))

    def test_emptying_the_estate_frees_the_switch(self):
        blocker = _branch_building("weapons")
        player = _owner(blocker)
        self.assertTrue(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))
        player.set_buildings([])
        self.assertIsNone(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))

    def test_the_refusal_also_carries_the_dormancy_figures(self):
        # R13.4 is one report covering both halves: what must be removed AND
        # what would go dormant.
        player = _owner(_branch_building("weapons"))
        player.db.researched_techs = set(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])
        refusal = self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())
        self.assertEqual(refusal.data["dormant_count"], 2)
        self.assertEqual(
            refusal.data["dormant_technologies"],
            {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
        )

    def test_an_empty_conflict_reports_the_dormant_technologies_and_passes(self):
        # R4.8: nothing stands in the way, so the build proceeds — and the count
        # of recorded technologies that go dormant is reported first.
        player = _owner()
        player.db.researched_techs = {
            FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0],
            FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][1],
            FIXTURE_TECH_KEYS_BY_BRANCH["cyber"][0],
        }

        self.assertIsNone(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))

        self.assertEqual(len(self.seen), 1)
        target, kind, data = self.seen[0]
        self.assertIs(target, player)
        self.assertEqual(kind, NOTIFY_BRANCH_DORMANCY)
        self.assertEqual(data["dormant_count"], 3)
        self.assertEqual(data["dormant_counts"], {"weapons": 2, "cyber": 1})
        self.assertEqual(
            data["dormant_technologies"],
            {
                "weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"]),
                "cyber": [FIXTURE_TECH_KEYS_BY_BRANCH["cyber"][0]],
            },
        )
        self.assertEqual(data["incoming_branch"], "bio")

    def test_a_blocked_switch_names_the_commitment_being_left(self):
        # The outgoing Branch is readable exactly while its lab still stands —
        # and a standing lab is itself a member of its Branch's estate, so this
        # is the blocked case by construction.
        player = _owner(_lab("weapons"), _branch_building("weapons"))
        refusal = self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())
        self.assertEqual(refusal.data["outgoing_branch"], "weapons")
        self.assertEqual(
            refusal.data["outgoing_doctrine"], BRANCH_DOCTRINE["weapons"]
        )

    def test_the_dormancy_report_names_no_outgoing_lab_once_none_stands(self):
        # The counterpart: with the outgoing lab already gone the commitment is
        # gone with it, and the RECORD is what the report is about.
        player = _owner()
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        self.assertIsNone(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))
        self.assertIsNone(self.seen[0][2]["outgoing_branch"])
        self.assertEqual(self.seen[0][2]["dormant_counts"], {"weapons": 1})

    def test_recorded_technologies_in_the_incoming_branch_are_not_dormant(self):
        player = _owner()
        player.db.researched_techs = set(FIXTURE_TECH_KEYS_BY_BRANCH["bio"])
        self.assertIsNone(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))
        self.assertEqual(self.seen, [])

    def test_nothing_to_report_publishes_nothing(self):
        player = _owner()
        self.assertIsNone(self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile()))
        self.assertEqual(self.seen, [])

    def test_the_report_precedes_any_charge_and_writes_nothing(self):
        # R4.8: the gate sits above the resource validation, so a player learns
        # the cost of the switch before a single resource moves — and the gate
        # itself neither charges nor records anything.
        player = _owner(_branch_building("weapons"), planet=HOME)
        player.add_resource("Iron", 500)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        before = (player.resource_snapshot(), player.attributes.all())

        self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())
        player.set_buildings([])
        self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile())

        self.assertEqual(
            (player.resource_snapshot(), player.attributes.all()), before
        )

    def test_conflicts_are_scoped_to_the_target_planet(self):
        here = _branch_building("weapons", planet=HOME)
        there = _branch_building("defense", planet=AWAY)
        player = _owner(here, there, planet=HOME)
        self.assertEqual(
            self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile(HOME)).data["branches"],
            ["weapons"],
        )
        self.assertEqual(
            self.gate(player, FIXTURE_LAB_ABBR["bio"], _Tile(AWAY)).data["branches"],
            ["defense"],
        )

    def test_a_broken_event_bus_neither_raises_nor_blocks(self):
        # R15.3: a report is not worth failing a construction attempt over.
        class _BrokenBus:
            def publish(self, *_args, **_kwargs):
                raise RuntimeError("bus exploded")

        system = BranchSystem(self.registry, _BrokenBus())
        player = _owner()
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        self.assertIsNone(
            _gates(system)[1](player, FIXTURE_LAB_ABBR["bio"], _Tile())
        )

    def test_a_lab_hosting_no_known_branch_is_not_gated(self):
        registry = make_registry(buildings=[{
            "name": "Rogue Lab",
            "abbreviation": "QQ",
            "cost": {"Iron": 10},
            "max_health": 100,
            "requires_hq": True,
            "required_terrain": None,
            "category": "research",
            "produces": None,
            "capabilities": ["research_lab"],
            "research_tree": "not_a_branch",
            "map_symbol": "QQ",
        }])
        gate = _gates(BranchSystem(registry, EventBus()))[1]
        self.assertIsNone(gate(_owner(), "QQ", _Tile()))


class TestUnlockTechnologyGate(unittest.TestCase):
    """The gate that requires the unlocking technology researched AND live (R6.2)."""

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)
        self.gate = _gates(self.system)[2]

    def _requester(self, branch, recorded=()):
        """A committed owner of *branch* whose record holds *recorded*."""
        player = _owner(_lab(branch))
        player.db.researched_techs = set(recorded)
        return player

    def test_a_building_naming_no_technology_is_ungated(self):
        # R6.1: every building shipped before this feature.
        player = self._requester("bio")
        for abbr in FIXTURE_NEUTRAL_ABBRS + tuple(FIXTURE_LAB_ABBR.values()):
            with self.subTest(abbr=abbr):
                self.assertIsNone(self.gate(player, abbr, _Tile()))

    def test_an_unresearched_technology_refuses_and_names_it(self):
        # R6.3: the technology and the Branch that hosts it.
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
                refusal = self.gate(
                    self._requester(branch),
                    FIXTURE_BRANCH_BUILDING_ABBR[branch],
                    _Tile(),
                )
                self.assertEqual(refusal, MSG_BRANCH_UNLOCK_REQUIRED)
                self.assertEqual(refusal.data["reason"], UNLOCK_NOT_RESEARCHED)
                self.assertEqual(refusal.data["technology"], required)
                self.assertEqual(refusal.data["branch"], branch)
                self.assertEqual(refusal.data["doctrine"], BRANCH_DOCTRINE[branch])
                self.assertEqual(refusal.data["lab"], FIXTURE_LAB_ABBR[branch])
                self.assertEqual(
                    refusal.data["technology_name"],
                    self.registry.technologies[required].name,
                )

    def test_researched_and_committed_permits_the_build(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
                self.assertIsNone(
                    self.gate(
                        self._requester(branch, [required]),
                        FIXTURE_BRANCH_BUILDING_ABBR[branch],
                        _Tile(),
                    )
                )

    def test_researched_but_dormant_refuses(self):
        # R6.2's second half: the record is kept, the effects are not applied,
        # so the building it unlocks stays locked.
        system = _system(_gated_neutral_registry())
        gate = _gates(system)[2]
        required = FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]
        for player in (
            _owner(),                      # no commitment at all
            _owner(_lab("bio")),           # committed to another Branch
        ):
            player.db.researched_techs = {required}
            with self.subTest(commitment=system.commitment(player)):
                refusal = gate(player, GATED_NEUTRAL_ABBR, _Tile())
                self.assertEqual(refusal, MSG_BRANCH_UNLOCK_REQUIRED)
                self.assertEqual(refusal.data["reason"], UNLOCK_DORMANT)
                self.assertEqual(refusal.data["branch"], "weapons")
                self.assertEqual(refusal.data["technology"], required)

    def test_the_dormant_gate_opens_the_moment_the_branch_is_committed(self):
        system = _system(_gated_neutral_registry())
        gate = _gates(system)[2]
        required = FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]
        player = _owner(_lab("weapons"))
        player.db.researched_techs = {required}
        self.assertIsNone(gate(player, GATED_NEUTRAL_ABBR, _Tile()))

    def test_a_pending_reinstatement_keeps_the_building_locked(self):
        # R5.7 read through the unlock gate: the effects return when the
        # reduced-cost job finishes, and so does the building.
        branch = "bio"
        required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
        player = self._requester(branch, [required])
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {branch: [required]})

        refusal = self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile())
        self.assertEqual(refusal, MSG_BRANCH_UNLOCK_REQUIRED)
        self.assertEqual(refusal.data["reason"], UNLOCK_REINSTATEMENT_PENDING)

        # The job completes: the key leaves the pending set and the gate opens.
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {branch: []})
        self.assertIsNone(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile())
        )

    def test_an_absent_pending_set_reads_as_empty(self):
        # R14.8: a player who never abandoned a Branch has the attribute absent,
        # so its documented default has to let a researched technology through.
        branch = "cyber"
        required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
        player = self._requester(branch, [required])
        self.assertIsNone(getattr(player.db, ATTR_BRANCH_REINSTATEMENT))
        self.assertIsNone(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile())
        )

    def test_a_malformed_pending_set_reads_as_empty(self):
        branch = "cyber"
        required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
        player = self._requester(branch, [required])
        for garbage in ("nonsense", 17, [required], {branch: required}, {branch: 3}):
            setattr(player.db, ATTR_BRANCH_REINSTATEMENT, garbage)
            with self.subTest(garbage=garbage):
                self.assertIsNone(
                    self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile())
                )

    def test_a_malformed_record_reads_as_nothing_researched(self):
        branch = "cyber"
        player = _owner(_lab(branch))
        for garbage in (None, "", 17, object()):
            player.db.researched_techs = garbage
            with self.subTest(garbage=garbage):
                self.assertEqual(
                    self.gate(
                        player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile()
                    ).data["reason"],
                    UNLOCK_NOT_RESEARCHED,
                )

    def test_the_record_comes_from_the_injected_tech_system_when_it_offers_one(self):
        branch = "bio"
        required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
        tech = _FakeTechSystem(recorded=[required])
        system = BranchSystem(self.registry, EventBus(), tech_system=tech)
        player = _owner(_lab(branch))          # the player's own record is empty
        self.assertIsNone(
            _gates(system)[2](
                player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile()
            )
        )
        self.assertGreaterEqual(tech.calls, 1)

    def test_a_failing_tech_system_falls_back_to_the_players_record(self):
        branch = "bio"
        required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
        tech = _FakeTechSystem(explode=True)
        system = BranchSystem(self.registry, EventBus(), tech_system=tech)
        player = self._requester(branch, [required])
        self.assertIsNone(
            _gates(system)[2](
                player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile()
            )
        )

    def test_the_gate_is_scoped_to_the_target_planet(self):
        branch = "bio"
        required = FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]
        player = self._requester(branch, [required])
        self.assertIsNone(
            self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile(HOME))
        )
        refusal = self.gate(
            player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile(AWAY)
        )
        self.assertEqual(refusal.data["reason"], UNLOCK_DORMANT)
        self.assertEqual(refusal.data["planet"], AWAY)

    def test_the_gate_writes_nothing(self):
        branch = "bio"
        player = self._requester(branch)
        before = player.attributes.all()
        self.gate(player, FIXTURE_BRANCH_BUILDING_ABBR[branch], _Tile())
        self.assertEqual(player.attributes.all(), before)


class _RaisingResolver:
    """A Branch resolver whose ``commitment`` blows up on every call."""

    def __init__(self):
        self.calls = 0

    def commitment(self, player, planet=None):
        self.calls += 1
        raise RuntimeError("resolver exploded")


class _FixedResolver:
    """A Branch resolver that answers *answer* regardless of the world."""

    def __init__(self, answer):
        self.answer = answer

    def commitment(self, player, planet=None):
        return self.answer


class TestOwnedResearchTreeForwards(unittest.TestCase):
    """``TechLabSystem.owned_research_tree`` forwards to the Branch resolver.

    The tree gate and the Branch_Commitment must be the same answer, so the
    tech system asks rather than derives — but only when a resolver is wired.
    Unwired, it keeps the pre-feature derivation so existing fixtures and an
    unwired deployment are unaffected.
    """

    def setUp(self):
        self.registry = fixture_registry()
        self.branch = _system(self.registry)
        self.tech = TechLabSystem(self.registry, EventBus())

    def test_unwired_keeps_the_pre_feature_derivation(self):
        self.assertIsNone(self.tech._branch)
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                player = _owner(_lab(branch))
                self.assertEqual(self.tech.owned_research_tree(player), branch)
        self.assertIsNone(self.tech.owned_research_tree(_owner()))

    def test_wired_answers_agree_with_the_commitment_query(self):
        self.tech.set_branch_resolver(self.branch)
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                player = _owner(_lab(branch))
                self.assertEqual(
                    self.tech.owned_research_tree(player),
                    self.branch.commitment(player),
                )

    def test_wired_answer_comes_FROM_the_resolver(self):
        # The player owns a bio lab; the resolver says cyber. A forwarder
        # reports cyber — a second local derivation would report bio.
        self.tech.set_branch_resolver(_FixedResolver("cyber"))
        self.assertEqual(self.tech.owned_research_tree(_owner(_lab("bio"))), "cyber")

    def test_a_failing_resolver_falls_back_instead_of_raising(self):
        resolver = _RaisingResolver()
        self.tech.set_branch_resolver(resolver)
        self.assertEqual(self.tech.owned_research_tree(_owner(_lab("bio"))), "bio")
        self.assertEqual(resolver.calls, 1)

    def test_unwiring_restores_the_local_derivation(self):
        self.tech.set_branch_resolver(_FixedResolver("cyber"))
        self.tech.set_branch_resolver(None)
        self.assertEqual(self.tech.owned_research_tree(_owner(_lab("bio"))), "bio")

    def test_a_suspended_lab_still_gates_research_to_its_tree(self):
        # R5.10 read through the forwarder: suspending the lab withholds the
        # lab's function, not the identity of the tree the owner is committed to.
        self.tech.set_branch_resolver(self.branch)
        player = _owner(_lab("bio", offline=True, upgrading=True))
        self.assertEqual(self.tech.owned_research_tree(player), "bio")


class TestNoCollaboratorsRequired(unittest.TestCase):
    """The identity surface needs nothing but the injected registry."""

    def test_every_identity_query_answers_with_no_collaborator_wired(self):
        system = BranchSystem(fixture_registry(), EventBus())
        self.assertEqual(system.branch_of_building(FIXTURE_LAB_ABBR["bio"]), "bio")
        self.assertEqual(
            system.branch_of_technology(FIXTURE_TECH_KEYS_BY_BRANCH["bio"][0]), "bio"
        )
        self.assertEqual(system.lab_for_branch("bio"), FIXTURE_LAB_ABBR["bio"])
        self.assertEqual(
            system.branch_buildings("bio"), [FIXTURE_BRANCH_BUILDING_ABBR["bio"]]
        )
        self.assertEqual(system.role_for_branch("bio"), BRANCH_ROLE["bio"])
        self.assertEqual(len(system.branch_overview()), len(BRANCHES))
        # Commitment too: it reads the owner's buildings and the injected
        # registry, so no collaborator system is involved.
        lab = _lab("bio")
        player = _owner(lab)
        self.assertEqual(system.commitment(player), "bio")
        self.assertTrue(system.has_commitment(player, "bio"))
        # And the estate queries: an owner's roster plus the injected registry.
        self.assertEqual(system.estate(player, "bio"), [lab])
        self.assertEqual(system.estate_count(player, "bio"), 1)
        self.assertEqual(
            system.conflicting_estates(player, HOME, "cyber"), {"bio": [lab]}
        )

    def test_collaborators_are_accepted_by_keyword(self):
        sentinel = object()
        system = BranchSystem(
            fixture_registry(),
            EventBus(),
            current_tick_func=lambda: 42,
            building_system=sentinel,
            tech_system=sentinel,
            agent_system=sentinel,
            alliance_system=sentinel,
            combat_engine=sentinel,
        )
        self.assertEqual(system._current_tick_func(), 42)
        for attr in (
            "_building_system", "_tech_system", "_agent_system",
            "_alliance_system", "_combat_engine",
        ):
            with self.subTest(attr=attr):
                self.assertIs(getattr(system, attr), sentinel)


# ------------------------------------------------------------------ #
#  Recompute triggers (R5.2, R3.8, R7.8)
# ------------------------------------------------------------------ #

class _RecordingTechSystem:
    """A TechLabSystem stand-in that records every recompute request.

    Deliberately exposes no ``researched_techs`` accessor, so the trigger's
    record read falls back to the player's own attribute — which is what the
    real ``TechLabSystem`` makes it do too.
    """

    def __init__(self, explode: bool = False):
        self.calls: list[tuple] = []
        self.explode = explode

    def recompute_tech_bonuses(self, player, planet=None):
        self.calls.append((player, planet))
        if self.explode:
            raise RuntimeError("recompute exploded")


class _RecordingAgentSystem:
    """An AgentSystem stand-in that records every Branch-role release."""

    def __init__(self, explode: bool = False):
        self.calls: list[tuple] = []
        self.explode = explode

    def unassign_branch_roles(self, player, planet, branch):
        self.calls.append((player, planet, branch))
        if self.explode:
            raise RuntimeError("release exploded")


class _NoSubscribeBus:
    """An event bus that can publish but not subscribe (a minimal fixture)."""

    def publish(self, *_args, **_kwargs):
        pass


def _triggered(registry=None, tech=None, agents=None, bus=None):
    """Return ``(system, tech, agents, bus)`` wired for the trigger tests."""
    bus = bus if bus is not None else EventBus()
    tech = tech if tech is not None else _RecordingTechSystem()
    agents = agents if agents is not None else _RecordingAgentSystem()
    system = BranchSystem(
        registry if registry is not None else fixture_registry(),
        bus,
        tech_system=tech,
        agent_system=agents,
    )
    return system, tech, agents, bus


class TestRecomputeTriggerWiring(unittest.TestCase):
    """The system subscribes its own triggers, the way every system here does."""

    def setUp(self):
        self.registry = fixture_registry()

    def test_all_three_events_reach_the_recompute(self):
        _unused, tech, _agents, bus = _triggered(self.registry)
        lab = _lab("defense")
        player = _owner(lab)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["defense"][0]}

        # The building triggers leave the planet DEFAULTED (None): the bonus
        # dict tracks the planet the owner OCCUPIES (R5.1), which the recompute
        # reads for itself, not the planet the building event happened on.
        bus.publish(CONSTRUCTION_COMPLETED, player=player, building=lab, tile=_Tile())
        self.assertEqual(tech.calls, [(player, None)])

        bus.publish(BUILDING_DESTROYED, attacker=None, building=lab, tile=_Tile())
        self.assertEqual(len(tech.calls), 2)

        # The arrival trigger is the one that NAMES a planet, because the
        # planet being arrived at is the occupied planet, already known.
        bus.publish(PLAYER_MOVED, player=player, planet=AWAY, old_planet=HOME)
        self.assertEqual(tech.calls[-1], (player, AWAY))

    def test_a_bus_that_cannot_subscribe_leaves_the_system_usable(self):
        # R15.3: a minimal fixture's bus is not a construction failure.
        system = BranchSystem(self.registry, _NoSubscribeBus())
        self.assertEqual(system.commitment(_owner(_lab("bio"))), "bio")

    def test_no_bus_at_all_leaves_the_system_usable(self):
        system = BranchSystem(self.registry, None)
        self.assertEqual(system.commitment(_owner(_lab("bio"))), "bio")

    def test_the_exclusion_scope_is_closed_between_events(self):
        # "Derive, do not store": between events nothing is held.
        system, _tech, _agents, _bus = _triggered(self.registry)
        self.assertIsNone(system._ignored_building)


class TestConstructionCompletedTrigger(unittest.TestCase):
    """A completed Branch_Lab establishes a commitment, so bonuses rebuild."""

    def setUp(self):
        self.registry = fixture_registry()

    def test_a_completed_lab_recomputes_for_that_owner_unscoped(self):
        # R5.1: the planet is left defaulted (None) so the rebuild reads the
        # planet the owner OCCUPIES at recompute time — a lab completing on a
        # planet the owner is not standing on must not re-scope the dict there.
        system, tech, _agents, _bus = _triggered(self.registry)
        lab = _lab("weapons")
        player = _owner(lab)

        system.on_construction_completed(player=player, building=lab, tile=_Tile())

        self.assertEqual(tech.calls, [(player, None)])

    def test_a_completed_lab_releases_no_agent_role(self):
        # R7.8 is about a Branch going DORMANT. A completion is the opposite.
        system, _tech, agents, _bus = _triggered(self.registry)
        lab = _lab("weapons")

        system.on_construction_completed(player=_owner(lab), building=lab)

        self.assertEqual(agents.calls, [])

    def test_the_owner_falls_back_to_the_buildings_own_owner(self):
        # The engineer path can complete a build with no player in the payload.
        system, tech, _agents, _bus = _triggered(self.registry)
        lab = _lab("cyber")
        player = _owner(lab)

        system.on_construction_completed(building=lab, tile=_Tile())

        self.assertEqual(tech.calls, [(player, None)])

    def test_a_branch_building_completing_triggers_nothing(self):
        # An estate decides no bonus, so only a lab can change the answer.
        system, tech, agents, _bus = _triggered(self.registry)
        works = _branch_building("weapons")

        system.on_construction_completed(player=_owner(works), building=works)

        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_a_neutral_building_completing_triggers_nothing(self):
        system, tech, agents, _bus = _triggered(self.registry)
        neutral = _neutral()

        system.on_construction_completed(player=_owner(neutral), building=neutral)

        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_the_recompute_is_never_scoped_to_the_events_planet(self):
        # R5.1: wherever the completed lab and its tile claim to be, the rebuild
        # is asked without a planet — the dict tracks the planet the owner
        # OCCUPIES, which the recompute reads for itself at recompute time.
        # (The event-planet fallback order still exists, but it scopes the
        # LAPSE on the destruction path, not the bonus rebuild.)
        system, tech, _agents, _bus = _triggered(self.registry)
        placeless = FakeBuilding(building_type=FIXTURE_LAB_ABBR["bio"], planet=None)
        player = _owner(placeless, planet=None)

        system.on_construction_completed(player=player, building=placeless,
                                        tile=_Tile(AWAY))
        self.assertEqual(tech.calls[-1], (player, None))

        roamer = _owner(placeless, planet=HOME)
        system.on_construction_completed(player=roamer, building=placeless)
        self.assertEqual(tech.calls[-1], (roamer, None))

    def test_an_unresolvable_payload_triggers_nothing_and_never_raises(self):
        system, tech, agents, _bus = _triggered(self.registry)
        for value in GARBAGE:
            with self.subTest(value=value):
                system.on_construction_completed(player=value, building=value,
                                                tile=value)
        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_a_missing_tech_system_is_a_no_op_rather_than_a_raise(self):
        # R15.2: an unwired collaborator degrades to a logged no-op.
        system = BranchSystem(self.registry, EventBus())
        lab = _lab("bio")
        system.on_construction_completed(player=_owner(lab), building=lab)

    def test_a_tech_system_without_the_recompute_is_a_no_op(self):
        system, _tech, agents, _bus = _triggered(self.registry, tech=object())
        lab = _lab("bio")
        system.on_construction_completed(player=_owner(lab), building=lab)
        self.assertEqual(agents.calls, [])

    def test_a_raising_recompute_does_not_raise_into_the_publisher(self):
        system, tech, _agents, bus = _triggered(
            self.registry, tech=_RecordingTechSystem(explode=True)
        )
        lab = _lab("bio")
        player = _owner(lab)

        system.on_construction_completed(player=player, building=lab)
        bus.publish(CONSTRUCTION_COMPLETED, player=player, building=lab)

        self.assertEqual(tech.calls, [(player, None), (player, None)])


class TestBuildingDestroyedTrigger(unittest.TestCase):
    """A Branch_Lab lost to an attack is the lapse path (R3.8, R5.2, R7.8)."""

    def setUp(self):
        self.registry = fixture_registry()

    def test_a_destroyed_lab_recomputes_and_releases_that_branchs_roles(self):
        # The two consequences carry different scopes on purpose: the RELEASE
        # is scoped to the planet the lab stood on (R7.8 — that is where the
        # commitment lapsed), while the bonus rebuild is unscoped (R5.1 — the
        # dict tracks the planet the owner occupies, read at recompute time).
        system, tech, agents, _bus = _triggered(self.registry)
        lab = _lab("bio")
        player = _owner(lab)

        system.on_building_destroyed(building=lab, tile=_Tile())

        self.assertEqual(tech.calls, [(player, None)])
        self.assertEqual(agents.calls, [(player, HOME, "bio")])   # R7.8

    def test_the_recompute_reads_the_world_as_it_will_be_after_the_delete(self):
        # BUILDING_DESTROYED fires BEFORE the delete, so the roster still holds
        # the dying lab. Without the exclusion the trigger would recompute the
        # bonuses of the very commitment it is reacting to the loss of.
        bus = EventBus()
        tech = TechLabSystem(self.registry, bus)
        system = BranchSystem(self.registry, bus, tech_system=tech)
        tech.set_branch_resolver(system)
        lab = _lab("weapons")
        player = _owner(lab)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 5.0})

        bus.publish(BUILDING_DESTROYED, attacker=None, building=lab, tile=_Tile())

        self.assertEqual(player.db.tech_bonuses, {})
        # The scope was the event's, not the system's: the roster is untouched,
        # so the commitment query answers from the world again.
        self.assertEqual(system.commitment(player), "weapons")
        self.assertIsNone(system._ignored_building)

    def test_the_exclusion_matches_a_different_object_for_the_same_row(self):
        # An owner's roster may hand back a different Python object for the same
        # database row than the event payload carries.
        system, _tech, agents, _bus = _triggered(self.registry)
        lab = _lab("cyber")
        lab.id = 4242
        player = _owner(lab)
        proxy = _lab("cyber")
        proxy.id = 4242
        proxy.attributes.add("owner", player)

        system.on_building_destroyed(building=proxy, tile=_Tile())

        self.assertEqual(agents.calls, [(player, HOME, "cyber")])

    def test_a_lab_lost_on_another_planet_leaves_this_planets_commitment(self):
        system, tech, agents, _bus = _triggered(self.registry)
        home_lab = _lab("weapons", planet=HOME)
        away_lab = _lab("defense", planet=AWAY)
        player = _owner(home_lab, away_lab)

        system.on_building_destroyed(building=away_lab)

        # The release is the away planet's (that is where "defense" lapsed);
        # the rebuild is unscoped, so it reads the OCCUPIED planet's commitment
        # (R5.1) — which is exactly what keeps this planet's bonuses standing.
        self.assertEqual(tech.calls, [(player, None)])
        self.assertEqual(agents.calls, [(player, AWAY, "defense")])

    def test_a_remote_loss_leaves_the_occupied_planets_bonuses_standing(self):
        """R5.1 end to end: the owner stands on HOME with a live weapons
        commitment; an enemy razes their defense lab on AWAY. The rebuild must
        keep HOME's bonuses — scoping it to AWAY (where the commitment just
        lapsed) would empty the dict out from under the player."""
        bus = EventBus()
        tech = TechLabSystem(self.registry, bus)
        system = BranchSystem(self.registry, bus, tech_system=tech)
        tech.set_branch_resolver(system)
        home_lab = _lab("weapons", planet=HOME)
        away_lab = _lab("defense", planet=AWAY)
        player = _owner(home_lab, away_lab, planet=HOME)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 5.0})

        bus.publish(BUILDING_DESTROYED, attacker=None, building=away_lab)

        self.assertEqual(player.db.tech_bonuses, {"damage": 5.0})

    def test_a_non_lab_destruction_triggers_nothing(self):
        system, tech, agents, _bus = _triggered(self.registry)
        works = _branch_building("weapons")
        _owner(_lab("weapons"), works)

        system.on_building_destroyed(building=works, tile=_Tile())

        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_a_half_built_lab_hosted_nothing_so_nothing_lapses(self):
        system, tech, agents, _bus = _triggered(self.registry)
        lab = _lab("bio", under_construction=True)
        _owner(lab)

        system.on_building_destroyed(building=lab, tile=_Tile())

        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_an_ownerless_lab_triggers_nothing(self):
        system, tech, agents, _bus = _triggered(self.registry)

        system.on_building_destroyed(building=_lab("bio"), tile=_Tile())

        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_an_unresolvable_payload_triggers_nothing_and_never_raises(self):
        system, tech, agents, _bus = _triggered(self.registry)
        for value in GARBAGE:
            with self.subTest(value=value):
                system.on_building_destroyed(building=value, attacker=value,
                                             tile=value)
        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_a_missing_agent_system_still_recomputes(self):
        # R15.2: the release degrades to a logged no-op; the rebuild still runs.
        tech = _RecordingTechSystem()
        system = BranchSystem(self.registry, EventBus(), tech_system=tech)
        lab = _lab("bio")
        player = _owner(lab)

        system.on_building_destroyed(building=lab)

        self.assertEqual(tech.calls, [(player, None)])

    def test_an_agent_system_without_the_release_method_still_recomputes(self):
        # The method lands with the agent-role task; until then the trigger is
        # already correct and tightens by itself.
        system, tech, _agents, _bus = _triggered(self.registry, agents=object())
        lab = _lab("bio")
        player = _owner(lab)

        system.on_building_destroyed(building=lab)

        self.assertEqual(tech.calls, [(player, None)])

    def test_a_raising_release_still_recomputes(self):
        # The release runs FIRST, so a raise there must not cost the rebuild.
        system, tech, agents, _bus = _triggered(
            self.registry, agents=_RecordingAgentSystem(explode=True)
        )
        lab = _lab("bio")
        player = _owner(lab)

        system.on_building_destroyed(building=lab)

        self.assertEqual(len(agents.calls), 1)
        self.assertEqual(tech.calls, [(player, None)])

    def test_the_destruction_path_writes_no_persistent_branch_state(self):
        # R5.9: a lab lost to an attack costs a rebuild, not research — which is
        # got by recording NOTHING here.
        system, _tech, _agents, _bus = _triggered(self.registry)
        lab = _lab("weapons")
        player = _owner(lab)
        before = dict(player.attributes.all())

        system.on_building_destroyed(building=lab, tile=_Tile())

        self.assertEqual(dict(player.attributes.all()), before)


class TestDemolishTrigger(unittest.TestCase):
    """A voluntarily demolished Branch_Lab is the same lapse, called directly.

    The demolish path publishes no ``BUILDING_DESTROYED``, so it arrives as a
    direct call — made AFTER the delete, which is why it reads the world as it
    already is and identifies the lab by its definition rather than an object.
    """

    def setUp(self):
        self.registry = fixture_registry()

    def test_demolishing_a_lab_recomputes_and_releases_that_branchs_roles(self):
        system, tech, agents, _bus = _triggered(self.registry)
        player = _owner()                    # the demolish already deleted the lab

        system.on_building_demolished(player, FIXTURE_LAB_ABBR["cyber"])

        self.assertEqual(tech.calls, [(player, None)])
        self.assertEqual(agents.calls, [(player, HOME, "cyber")])   # R7.8

    def test_a_definition_object_is_accepted_too(self):
        system, tech, _agents, _bus = _triggered(self.registry)
        bdef = self.registry.get_building(FIXTURE_LAB_ABBR["resource"])
        player = _owner()

        system.on_building_demolished(player, bdef)

        self.assertEqual(tech.calls, [(player, None)])

    def test_an_explicit_planet_is_honoured_by_the_lapse_alone(self):
        # An explicit planet scopes the LAPSE — which planet's roles release —
        # and never the bonus rebuild: the dict tracks the planet the player
        # OCCUPIES (R5.1), so an admin path demolishing remotely must not
        # re-scope it to the demolition's planet.
        system, tech, agents, _bus = _triggered(self.registry)
        player = _owner(planet=HOME)

        system.on_building_demolished(player, FIXTURE_LAB_ABBR["bio"], planet=AWAY)

        self.assertEqual(tech.calls, [(player, None)])
        self.assertEqual(agents.calls, [(player, AWAY, "bio")])

    def test_no_release_while_that_branch_is_still_committed_there(self):
        # The lapse fires on the commitment actually being gone, not on a
        # demolition having happened — so a lab of that Branch still standing
        # leaves its agents alone while the bonuses are still rebuilt.
        system, tech, agents, _bus = _triggered(self.registry)
        standing = _lab("cyber")
        player = _owner(standing)

        system.on_building_demolished(player, FIXTURE_LAB_ABBR["cyber"])

        self.assertEqual(agents.calls, [])
        self.assertEqual(tech.calls, [(player, None)])

    def test_demolishing_a_non_lab_triggers_nothing(self):
        system, tech, agents, _bus = _triggered(self.registry)
        player = _owner(_lab("weapons"))

        system.on_building_demolished(
            player, FIXTURE_BRANCH_BUILDING_ABBR["weapons"]
        )

        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_demolishing_a_neutral_building_triggers_nothing(self):
        system, tech, agents, _bus = _triggered(self.registry)
        for abbr in FIXTURE_NEUTRAL_ABBRS:
            with self.subTest(abbr=abbr):
                system.on_building_demolished(_owner(), abbr)
        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_an_unresolvable_definition_triggers_nothing(self):
        system, tech, agents, _bus = _triggered(self.registry)
        for value in GARBAGE:
            with self.subTest(value=value):
                system.on_building_demolished(value, value)
                system.on_building_demolished(_owner(), value)
        self.assertEqual((tech.calls, agents.calls), ([], []))

    def test_an_unresolvable_owner_never_raises(self):
        # R15.3: a garbage owner is not this trigger's to police — the
        # collaborators drop it themselves. What matters is that nothing raises.
        system, _tech, _agents, _bus = _triggered(self.registry)
        for value in GARBAGE:
            with self.subTest(value=value):
                system.on_building_demolished(value, FIXTURE_LAB_ABBR["bio"])

    def test_the_bonuses_actually_go_dormant_end_to_end(self):
        bus = EventBus()
        tech = TechLabSystem(self.registry, bus)
        system = BranchSystem(self.registry, bus, tech_system=tech)
        tech.set_branch_resolver(system)
        lab = _lab("weapons")
        player = _owner(lab)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 5.0})

        player.set_buildings([])                    # the demolish deleted it
        system.on_building_demolished(player, FIXTURE_LAB_ABBR["weapons"])

        self.assertEqual(player.db.tech_bonuses, {})
        self.assertEqual(player.db.researched_techs,
                         {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]})   # R5.3


class TestPlayerMovedTrigger(unittest.TestCase):
    """A commitment is per-planet, so arriving somewhere else changes the answer."""

    def setUp(self):
        self.registry = fixture_registry()

    def _traveller(self, branch="weapons"):
        player = _owner(_lab(branch, planet=HOME), planet=HOME)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH[branch][0]}
        return player

    def test_a_cross_planet_arrival_recomputes_for_the_arrival_planet(self):
        system, tech, _agents, _bus = _triggered(self.registry)
        player = self._traveller()

        system.on_player_moved(player=player, planet=AWAY, old_planet=HOME)

        self.assertEqual(tech.calls, [(player, AWAY)])

    def test_a_same_planet_move_recomputes_nothing(self):
        system, tech, _agents, _bus = _triggered(self.registry)
        player = self._traveller()

        system.on_player_moved(player=player, planet=HOME, old_planet=HOME)

        self.assertEqual(tech.calls, [])

    def test_an_unknown_origin_recomputes_rather_than_guessing(self):
        system, tech, _agents, _bus = _triggered(self.registry)
        player = self._traveller()

        system.on_player_moved(player=player, planet=AWAY)

        self.assertEqual(tech.calls, [(player, AWAY)])

    def test_a_mover_with_no_record_is_skipped(self):
        # An empty record accumulates to an empty dict either way — which is
        # also what keeps the agents that ride the same relocation primitive
        # from having a bonus dict written onto them.
        system, tech, _agents, _bus = _triggered(self.registry)
        player = _owner(_lab("weapons"), planet=HOME)

        system.on_player_moved(player=player, planet=AWAY, old_planet=HOME)

        self.assertEqual(tech.calls, [])

    def test_an_arrival_releases_no_agent_role(self):
        # Travelling changes no PLANET's commitment, so no Branch goes dormant.
        system, _tech, agents, _bus = _triggered(self.registry)
        player = self._traveller()

        system.on_player_moved(player=player, planet=AWAY, old_planet=HOME)

        self.assertEqual(agents.calls, [])

    def test_no_mover_triggers_nothing_and_never_raises(self):
        system, tech, _agents, _bus = _triggered(self.registry)
        for value in (None, *GARBAGE):
            with self.subTest(value=value):
                system.on_player_moved(player=value, planet=AWAY, old_planet=HOME)
        self.assertEqual(tech.calls, [])

    def test_the_bonuses_actually_follow_the_planet_end_to_end(self):
        bus = EventBus()
        tech = TechLabSystem(self.registry, bus)
        system = BranchSystem(self.registry, bus, tech_system=tech)
        tech.set_branch_resolver(system)
        player = _owner(_lab("weapons", planet=HOME), planet=HOME)
        player.db.researched_techs = {FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][0]}
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 5.0})

        player.db.coord_planet = AWAY
        bus.publish(PLAYER_MOVED, player=player, planet=AWAY, old_planet=HOME)
        self.assertEqual(player.db.tech_bonuses, {})

        # And back: the record was never touched, so the effects simply return.
        player.db.coord_planet = HOME
        bus.publish(PLAYER_MOVED, player=player, planet=HOME, old_planet=AWAY)
        self.assertEqual(player.db.tech_bonuses, {"damage": 5.0})


# ------------------------------------------------------------------ #
#  Reinstatement bookkeeping (R5.5, R5.9, R15.5)
# ------------------------------------------------------------------ #

class TestReinstatementBookkeeping(unittest.TestCase):
    """The only persisted player state this feature adds, and its single writer.

    Requirements 5.5 and 5.9 differ on one point only: a Branch abandoned
    *voluntarily* costs Reinstatement research on the way back, a Branch whose
    lab an enemy destroyed does not. After the fact the world cannot tell the two
    apart, so one bit is persisted at the one moment the distinction is known —
    which makes the interesting assertions as much about what is **not** written
    as about what is.
    """

    def setUp(self):
        self.registry = fixture_registry()

    # -- helpers ---------------------------------------------------- #

    def _player(self, *branches, hostile=False, planet=HOME, buildings=()):
        """A player whose record holds both fixture technologies of *branches*."""
        player = FakePlayer(planet=planet, hostile=hostile)
        player.db.researched_techs = {
            key
            for branch in branches
            for key in FIXTURE_TECH_KEYS_BY_BRANCH[branch]
        }
        player.set_buildings(list(buildings))
        return player

    @staticmethod
    def _abandoned(player):
        """The stored abandoned mapping, read by value."""
        return getattr(player.db, ATTR_BRANCH_ABANDONED)

    @staticmethod
    def _pending(player):
        """The stored Reinstatement mapping, read by value."""
        return getattr(player.db, ATTR_BRANCH_REINSTATEMENT)

    def _demolish(self, player, branch, system=None, planet=None):
        """Run the voluntary-demolition trigger for *branch*'s lab."""
        system = system if system is not None else _triggered(self.registry)[0]
        system.on_building_demolished(player, FIXTURE_LAB_ABBR[branch],
                                     planet=planet)
        return system

    def _complete(self, player, branch, system=None, planet=HOME):
        """Run the lab-completion trigger for *branch*'s lab on *planet*."""
        system = system if system is not None else _triggered(self.registry)[0]
        lab = _lab(branch, planet=planet)
        player.set_buildings([*player.get_buildings(), lab])
        system.on_construction_completed(player=player, building=lab)
        return system

    # -- the way out: the abandoned bit (R5.5) ---------------------- #

    def test_a_voluntary_demolition_sets_that_branchs_bit(self):
        player = self._player("weapons")

        self._demolish(player, "weapons")

        self.assertEqual(self._abandoned(player), {"weapons": True})

    def test_every_branch_records_its_own_abandonment(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                player = self._player(branch)
                self._demolish(player, branch)
                self.assertEqual(self._abandoned(player), {branch: True})

    def test_a_second_abandonment_keeps_the_first_bit(self):
        # The mapping is not planet-scoped, so a player may hold several bits.
        player = self._player("weapons", "bio")

        self._demolish(player, "weapons")
        self._demolish(player, "bio")

        self.assertEqual(self._abandoned(player), {"weapons": True, "bio": True})

    def test_abandoning_the_same_branch_twice_is_idempotent(self):
        player = self._player("cyber")

        self._demolish(player, "cyber")
        self._demolish(player, "cyber")

        self.assertEqual(self._abandoned(player), {"cyber": True})

    def test_the_demolition_leaves_the_researched_record_untouched(self):
        # R5.3: dormancy suspends effects and erases no history.
        player = self._player("weapons")
        before = set(player.db.researched_techs)

        self._demolish(player, "weapons")

        self.assertEqual(set(player.db.researched_techs), before)

    def test_demolishing_a_non_lab_writes_nothing(self):
        # Emptying an estate abandons no Branch; only losing the lab does.
        player = self._player("weapons", buildings=[_lab("weapons")])
        system = _triggered(self.registry)[0]

        system.on_building_demolished(
            player, FIXTURE_BRANCH_BUILDING_ABBR["weapons"]
        )

        self.assertIsNone(self._abandoned(player))

    def test_demolishing_a_neutral_building_writes_nothing(self):
        player = self._player("weapons")
        system = _triggered(self.registry)[0]

        for abbr in FIXTURE_NEUTRAL_ABBRS:
            with self.subTest(abbr=abbr):
                system.on_building_demolished(player, abbr)

        self.assertIsNone(self._abandoned(player))

    def test_no_bit_while_that_branch_is_still_committed_there(self):
        # What the bit marks is a Branch ABANDONED. A lab of that Branch still
        # standing means nothing was abandoned, so nothing is recorded.
        player = self._player("cyber", buildings=[_lab("cyber")])

        self._demolish(player, "cyber")

        self.assertIsNone(self._abandoned(player))

    def test_the_bit_is_written_with_no_collaborator_injected_at_all(self):
        # R15.2: the bookkeeping is this module's own, so it cannot depend on a
        # TechLabSystem or an AgentSystem being wired.
        system = BranchSystem(self.registry, EventBus())
        player = self._player("defense")

        system.on_building_demolished(player, FIXTURE_LAB_ABBR["defense"])

        self.assertEqual(self._abandoned(player), {"defense": True})

    # -- the way out: a hostile loss records NOTHING (R5.9) --------- #

    def test_a_hostile_destruction_writes_neither_attribute(self):
        system, _tech, _agents, _bus = _triggered(self.registry)
        lab = _lab("weapons")
        player = self._player("weapons")
        player.set_buildings([lab])

        system.on_building_destroyed(building=lab, tile=_Tile())

        self.assertIsNone(self._abandoned(player))
        self.assertIsNone(self._pending(player))

    def test_a_hostile_destruction_of_every_branchs_lab_writes_nothing(self):
        for branch in BRANCHES:
            with self.subTest(branch=branch):
                system, _tech, _agents, _bus = _triggered(self.registry)
                lab = _lab(branch)
                player = self._player(branch)
                player.set_buildings([lab])

                system.on_building_destroyed(building=lab, tile=_Tile())

                self.assertIsNone(self._abandoned(player))

    # -- the way back: seeding the pending set (R5.5) --------------- #

    def test_completing_an_abandoned_branchs_lab_seeds_the_record(self):
        player = self._player("weapons")
        system = self._demolish(player, "weapons")

        self._complete(player, "weapons", system=system)

        self.assertEqual(
            self._pending(player),
            {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
        )

    def test_the_seed_clears_the_bit(self):
        # One abandonment costs one round of Reinstatement, not one per lab.
        player = self._player("bio")
        system = self._demolish(player, "bio")

        self._complete(player, "bio", system=system)

        self.assertEqual(self._abandoned(player), {})

    def test_a_second_completion_seeds_nothing_more(self):
        player = self._player("bio")
        system = self._demolish(player, "bio")
        self._complete(player, "bio", system=system)
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {"bio": []})

        self._complete(player, "bio", system=system)

        self.assertEqual(self._pending(player), {"bio": []})

    def test_the_seed_is_filtered_to_the_abandoned_branch(self):
        # A record spanning two Branches seeds only the one being reinstated.
        player = self._player("weapons", "cyber")
        system = self._demolish(player, "weapons")

        self._complete(player, "weapons", system=system)

        self.assertEqual(
            self._pending(player),
            {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
        )

    def test_the_seed_leaves_the_researched_record_untouched(self):
        # R5.3: the pending set is an exclusion set, not a deletion.
        player = self._player("weapons")
        before = set(player.db.researched_techs)
        system = self._demolish(player, "weapons")

        self._complete(player, "weapons", system=system)

        self.assertEqual(set(player.db.researched_techs), before)

    def test_another_branchs_pending_list_survives_the_seed(self):
        player = self._player("weapons", "bio")
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {"bio": ["bio_core"]})
        system = self._demolish(player, "weapons")

        self._complete(player, "weapons", system=system)

        self.assertEqual(
            self._pending(player),
            {
                "bio": ["bio_core"],
                "weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"]),
            },
        )

    def test_an_abandoned_branch_with_no_record_seeds_an_empty_list(self):
        # Nothing to reinstate, but the bit must still be cleared so a later
        # hostile loss and rebuild does not inherit it.
        player = self._player()
        system = self._demolish(player, "research")

        self._complete(player, "research", system=system)

        self.assertEqual(self._pending(player), {"research": []})
        self.assertEqual(self._abandoned(player), {})

    def test_reabandoning_reseeds_the_whole_record(self):
        # R5.5 asks for a job per RECORDED technology, so a second abandonment
        # costs the keys already reinstated over again rather than only the rest.
        player = self._player("weapons")
        system = self._demolish(player, "weapons")
        self._complete(player, "weapons", system=system)
        # The first reinstatement job finished, leaving one key pending.
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT,
                {"weapons": [FIXTURE_TECH_KEYS_BY_BRANCH["weapons"][1]]})
        player.set_buildings([])

        self._demolish(player, "weapons", system=system)
        self._complete(player, "weapons", system=system)

        self.assertEqual(
            self._pending(player),
            {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
        )

    def test_completing_a_lab_of_a_branch_never_abandoned_writes_nothing(self):
        # R5.9: the destroyed-and-rebuilt case, and the first-commitment case.
        player = self._player("weapons")

        self._complete(player, "weapons")

        self.assertIsNone(self._pending(player))
        self.assertIsNone(self._abandoned(player))

    def test_a_lab_completing_for_a_different_branch_seeds_nothing(self):
        # The bit is per-Branch: committing elsewhere does not discharge it.
        player = self._player("weapons", "defense")
        system = self._demolish(player, "weapons")

        self._complete(player, "defense", system=system)

        self.assertIsNone(self._pending(player))
        self.assertEqual(self._abandoned(player), {"weapons": True})

    def test_a_branch_building_completing_seeds_nothing(self):
        player = self._player("weapons")
        system = self._demolish(player, "weapons")
        works = _branch_building("weapons")
        player.set_buildings([works])

        system.on_construction_completed(player=player, building=works)

        self.assertIsNone(self._pending(player))
        self.assertEqual(self._abandoned(player), {"weapons": True})

    # -- persistence discipline (R14.7, R14.8, R15.3) --------------- #

    def test_both_writes_survive_a_store_that_discards_in_place_mutation(self):
        # R14.7: a real Evennia attribute may hand back a serialized COPY, so a
        # read-modify-with-no-write-back is silently lost. The hostile store
        # reproduces that, which is what makes read-copy-write tested rather
        # than assumed.
        player = self._player("weapons", "bio", hostile=True)
        system = _triggered(self.registry)[0]

        system.on_building_demolished(player, FIXTURE_LAB_ABBR["weapons"])
        system.on_building_demolished(player, FIXTURE_LAB_ABBR["bio"])
        self.assertEqual(self._abandoned(player), {"weapons": True, "bio": True})

        self._complete(player, "weapons", system=system)
        self.assertEqual(
            self._pending(player),
            {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
        )
        self.assertEqual(self._abandoned(player), {"bio": True})

    def test_a_stored_value_of_the_wrong_shape_reads_as_the_default(self):
        # R14.8: anything that is not a mapping is the documented absent value,
        # and the write replaces it with a well-formed one rather than raising.
        for garbage in ("nonsense", 17, ["weapons"], object()):
            with self.subTest(garbage=garbage):
                player = self._player("weapons")
                setattr(player.db, ATTR_BRANCH_ABANDONED, garbage)

                self._demolish(player, "weapons")

                self.assertEqual(self._abandoned(player), {"weapons": True})

    def test_a_garbage_pending_container_is_replaced_by_the_seed(self):
        for garbage in ("nonsense", 17, ["weapons"], object()):
            with self.subTest(garbage=garbage):
                player = self._player("weapons")
                system = self._demolish(player, "weapons")
                setattr(player.db, ATTR_BRANCH_REINSTATEMENT, garbage)

                self._complete(player, "weapons", system=system)

                self.assertEqual(
                    self._pending(player),
                    {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
                )

    def test_an_unresolvable_definition_writes_nothing_and_never_raises(self):
        player = self._player("weapons")
        system = _triggered(self.registry)[0]

        for value in GARBAGE:
            with self.subTest(value=value):
                system.on_building_demolished(player, value)
                system.on_construction_completed(player=player, building=value)

        self.assertIsNone(self._abandoned(player))
        self.assertIsNone(self._pending(player))

    def test_an_unwritable_player_never_raises(self):
        # R15.3: a trigger must not raise into the event bus, whatever it is
        # handed. A player with no attribute store at all is the extreme case.
        system = _triggered(self.registry)[0]

        for value in (object(), SimpleNamespace(), 17, "player"):
            with self.subTest(value=value):
                system.on_building_demolished(value, FIXTURE_LAB_ABBR["bio"])

    # -- end to end: the two requirements the bit exists to separate - #

    def _wired(self):
        """A real TechLabSystem and BranchSystem sharing one event bus."""
        bus = EventBus()
        tech = TechLabSystem(self.registry, bus)
        system = BranchSystem(self.registry, bus, tech_system=tech)
        tech.set_branch_resolver(system)
        return bus, tech, system

    def test_a_lab_lost_to_an_attack_restores_the_branch_with_no_research(self):
        # R5.9, end to end: losing a lab is a REBUILD cost, not a research reset.
        bus, tech, _system = self._wired()
        lab = _lab("weapons")
        player = self._player("weapons", buildings=[lab])
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 8.0})

        bus.publish(BUILDING_DESTROYED, attacker=None, building=lab, tile=_Tile())
        self.assertEqual(player.db.tech_bonuses, {})

        rebuilt = _lab("weapons")
        player.set_buildings([rebuilt])
        bus.publish(CONSTRUCTION_COMPLETED, player=player, building=rebuilt,
                    tile=_Tile())

        self.assertEqual(player.db.tech_bonuses, {"damage": 8.0})   # no research
        self.assertIsNone(self._pending(player))

    def test_a_lab_abandoned_and_rebuilt_withholds_the_effects(self):
        # R5.5 / R5.7, end to end: the same rebuild after a VOLUNTARY walk-away
        # applies nothing until each Reinstatement job finishes.
        bus, tech, system = self._wired()
        player = self._player("weapons", buildings=[_lab("weapons")])
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 8.0})

        player.set_buildings([])                 # the demolish deleted the lab
        system.on_building_demolished(player, FIXTURE_LAB_ABBR["weapons"])
        self.assertEqual(player.db.tech_bonuses, {})

        rebuilt = _lab("weapons")
        player.set_buildings([rebuilt])
        bus.publish(CONSTRUCTION_COMPLETED, player=player, building=rebuilt,
                    tile=_Tile())

        # Committed again, and every recorded key withheld pending its job.
        self.assertEqual(system.commitment(player), "weapons")
        self.assertEqual(player.db.tech_bonuses, {})
        self.assertEqual(
            self._pending(player),
            {"weapons": sorted(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"])},
        )
        self.assertEqual(set(player.db.researched_techs),
                         set(FIXTURE_TECH_KEYS_BY_BRANCH["weapons"]))   # R5.3

        # The jobs complete: the keys leave the pending set and the effects land.
        setattr(player.db, ATTR_BRANCH_REINSTATEMENT, {"weapons": []})
        tech.recompute_tech_bonuses(player)
        self.assertEqual(player.db.tech_bonuses, {"damage": 8.0})


# ------------------------------------------------------------------ #
#  Reinstatement completion — the job's two questions (R5.7, R15.5)
# ------------------------------------------------------------------ #

class TestReinstatementCompletion(unittest.TestCase):
    """What the Reinstatement research job asks of the pending set's owner.

    ``TechLabSystem`` runs the job, but this module is the single writer of
    ``db.branch_reinstatement`` (R15.5), so the job asks rather than assigns: is
    this recorded key *reinstatable* rather than done
    (:meth:`~BranchSystem.reinstatement_pending`) before it starts, and clear it
    (:meth:`~BranchSystem.on_reinstatement_completed`) when it finishes. Both
    answers are per key and per Branch, the write touches nothing else, and
    neither raises for anything it is handed (R15.3).
    """

    CORE, ADV = FIXTURE_TECH_KEYS_BY_BRANCH["weapons"]

    def setUp(self):
        self.registry = fixture_registry()
        self.system = _system(self.registry)

    # -- helpers ---------------------------------------------------- #

    def _player(self, pending=None, branches=("weapons",), hostile=False,
                buildings=()):
        player = FakePlayer(planet=HOME, hostile=hostile,
                            buildings=list(buildings))
        player.db.researched_techs = {
            key
            for branch in branches
            for key in FIXTURE_TECH_KEYS_BY_BRANCH[branch]
        }
        if pending is not None:
            setattr(player.db, ATTR_BRANCH_REINSTATEMENT, pending)
        return player

    @staticmethod
    def _pending(player):
        return getattr(player.db, ATTR_BRANCH_REINSTATEMENT)

    # -- the read: is this key reinstatable? ------------------------ #

    def test_a_seeded_key_reads_as_pending(self):
        for branch, keys in FIXTURE_TECH_KEYS_BY_BRANCH.items():
            with self.subTest(branch=branch):
                player = self._player({branch: list(keys)}, branches=(branch,))
                for key in keys:
                    self.assertTrue(
                        self.system.reinstatement_pending(player, key)
                    )

    def test_a_key_of_another_branch_is_not_pending(self):
        # The answer is scoped by the TECHNOLOGY's Branch, so a key listed under
        # the wrong Branch is not pending — the list it sits in decides nothing.
        player = self._player({"weapons": [self.CORE, "bio_core"]},
                              branches=("weapons", "bio"))

        self.assertTrue(self.system.reinstatement_pending(player, self.CORE))
        self.assertFalse(self.system.reinstatement_pending(player, "bio_core"))

    def test_nothing_pending_reads_as_not_pending(self):
        player = self._player()
        self.assertIsNone(self._pending(player))

        for key in (self.CORE, self.ADV):
            self.assertFalse(self.system.reinstatement_pending(player, key))

    def test_an_empty_list_reads_as_not_pending(self):
        # Every job finished: the Branch is committed and nothing is owed.
        player = self._player({"weapons": []})

        self.assertFalse(self.system.reinstatement_pending(player, self.CORE))

    def test_an_unresolvable_key_is_not_pending(self):
        player = self._player({"weapons": [self.CORE]})

        for value in GARBAGE:
            with self.subTest(value=value):
                self.assertFalse(
                    self.system.reinstatement_pending(player, value)
                )

    def test_a_malformed_pending_container_reads_as_not_pending(self):
        for garbage in ("nonsense", 17, [self.CORE], {"weapons": self.CORE},
                        {"weapons": 3}):
            with self.subTest(garbage=garbage):
                player = self._player(garbage)
                self.assertFalse(
                    self.system.reinstatement_pending(player, self.CORE)
                )

    def test_an_unreadable_player_is_not_pending(self):
        for value in (None, object(), SimpleNamespace(), 17, "player"):
            with self.subTest(value=value):
                self.assertFalse(
                    self.system.reinstatement_pending(value, self.CORE)
                )

    # -- the write: one key leaves the pending set ------------------ #

    def test_completing_a_job_clears_only_that_key(self):
        player = self._player({"weapons": [self.CORE, self.ADV]})

        self.assertTrue(
            self.system.on_reinstatement_completed(player, self.CORE)
        )

        self.assertEqual(self._pending(player), {"weapons": [self.ADV]})
        self.assertFalse(self.system.reinstatement_pending(player, self.CORE))
        self.assertTrue(self.system.reinstatement_pending(player, self.ADV))

    def test_clearing_the_last_key_drops_the_branch_entirely(self):
        # "Everything reinstated" and "never abandoned" become the same stored
        # shape, so both read as nothing pending (R14.8).
        player = self._player({"weapons": [self.CORE]})

        self.assertTrue(
            self.system.on_reinstatement_completed(player, self.CORE)
        )

        self.assertEqual(self._pending(player), {})

    def test_another_branchs_pending_list_survives_the_clear(self):
        player = self._player(
            {"weapons": [self.CORE], "bio": ["bio_core", "bio_adv"]},
            branches=("weapons", "bio"),
        )

        self.system.on_reinstatement_completed(player, self.CORE)

        self.assertEqual(self._pending(player),
                         {"bio": ["bio_core", "bio_adv"]})

    def test_a_second_completion_reports_nothing_to_clear(self):
        player = self._player({"weapons": [self.CORE, self.ADV]})
        self.system.on_reinstatement_completed(player, self.CORE)

        self.assertFalse(
            self.system.on_reinstatement_completed(player, self.CORE)
        )

        self.assertEqual(self._pending(player), {"weapons": [self.ADV]})

    def test_the_clear_leaves_the_researched_record_untouched(self):
        # R5.3: the pending set is an exclusion set; the record is the history.
        player = self._player({"weapons": [self.CORE]})
        before = set(player.db.researched_techs)

        self.system.on_reinstatement_completed(player, self.CORE)

        self.assertEqual(set(player.db.researched_techs), before)

    def test_clearing_a_key_that_is_not_pending_writes_nothing(self):
        player = self._player()

        for value in (*GARBAGE, self.CORE, "bio_core"):
            with self.subTest(value=value):
                self.assertFalse(
                    self.system.on_reinstatement_completed(player, value)
                )

        self.assertIsNone(self._pending(player))

    def test_the_clear_survives_a_store_that_discards_in_place_mutation(self):
        # R14.7: read-copy-write, tested rather than assumed.
        player = self._player({"weapons": [self.CORE, self.ADV]}, hostile=True)

        self.system.on_reinstatement_completed(player, self.CORE)

        self.assertEqual(self._pending(player), {"weapons": [self.ADV]})

    def test_an_unwritable_player_never_raises(self):
        for value in (None, object(), SimpleNamespace(), 17, "player"):
            with self.subTest(value=value):
                self.assertFalse(
                    self.system.on_reinstatement_completed(value, self.CORE)
                )

    # -- the read, the write, and "applied" are one rule ------------ #

    def test_the_clear_makes_the_key_applied_again(self):
        # R5.7 end to end through the shared definition of "applied": while the
        # key is pending the unlock gate and the bonus filter both withhold it,
        # and clearing it is what lets both through.
        player = self._player({"weapons": [self.CORE]},
                              buildings=[_lab("weapons")])

        self.assertEqual(
            self.system._unapplied_reason(player, self.CORE),
            UNLOCK_REINSTATEMENT_PENDING,
        )
        self.assertNotIn(self.CORE, self.system.applied_technologies(player))

        self.system.on_reinstatement_completed(player, self.CORE)

        self.assertIsNone(self.system._unapplied_reason(player, self.CORE))
        self.assertIn(self.CORE, self.system.applied_technologies(player))


if __name__ == "__main__":
    unittest.main()
