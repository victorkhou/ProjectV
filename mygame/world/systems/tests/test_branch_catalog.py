"""
Fixed-cardinality and membership unit tests for the shipped Branch catalog.

Feature: tech-tree-branch-foundation, design section "Testing Strategy /
Unit tests" — the fixed-cardinality and membership assertions.

These are claims about the SHIPPED data and constants, not generative
properties: the six-Branch vocabulary in ``world.constants``, the six
Operation_Kind entries and the Counter_Web in ``data/definitions/branches.yaml``,
the six sets of four per-kind ``BalanceConfig`` fields, the six hosting labs in
``data/definitions/buildings.yaml``, and the existing one-research-lab-per-planet
limit applied to all six of them. Because the subject is the shipped content,
every data assertion here reads the REAL ``mygame/data`` directory through
``DataRegistry.load_all`` (the pattern
``mygame/world/tests/test_equipment_content_load.py`` establishes) rather than a
synthetic fixture — a fixture would only re-assert what the property tests
already cover over generated catalogs.

**Validates: Requirements 1.1, 3.6, 7.2, 9.1, 12.1**
"""

import dataclasses
import os
import unittest

# Imported first: this module installs the Evennia stub block, so the project
# imports below resolve with ``evennia`` absent from ``sys.modules``. It also
# carries the shared framework-free fakes (its *fixture catalog* is synthetic
# and deliberately unused here — the claims under test are about shipped data).
from mygame.world.systems.tests.branch_strategies import (  # noqa: E402
    FakeBuilding,
    FakePlayer,
)

from mygame.world.constants import (  # noqa: E402
    BRANCH_DOCTRINE,
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    OPERATION_KINDS,
    RESEARCH_LAB,
    RESEARCH_TREES,
    RESOURCE_TYPES,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.systems.building_system import BuildingSystem  # noqa: E402

# ------------------------------------------------------------------ #
#  The real data directory (mygame/data)
# ------------------------------------------------------------------ #
#  This file lives at mygame/world/systems/tests/ ; the shipped definitions
#  live at mygame/data/ — three directories up, then into ``data``.
_REAL_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
)

#: The six Branches the catalog must express (requirements "Branch Overview").
EXPECTED_BRANCHES = frozenset({
    "weapons", "defense", "resource", "research", "bio", "cyber",
})

#: Branch -> the abbreviation of its hosting lab in ``buildings.yaml``.
#: ``cyber`` is hosted by ``SX``, not ``SG``: ``SG`` is the Shield Generator and
#: the registry keys buildings BY abbreviation, so a second ``SG`` would
#: silently replace it.
EXPECTED_LAB_ABBR = {
    "weapons": "WX",
    "defense": "DF",
    "resource": "RX",
    "research": "LB",
    "bio": "BX",
    "cyber": "SX",
}

#: The shipped Counter_Web cycle: one advantage and one disadvantage per Branch.
EXPECTED_COUNTER_CYCLE = (
    "weapons", "defense", "bio", "cyber", "resource", "research",
)

_REGISTRY: DataRegistry | None = None


def _real_registry() -> DataRegistry:
    """The shipped definitions, loaded once and shared by every test here."""
    global _REGISTRY
    if _REGISTRY is None:
        registry = DataRegistry()
        registry.load_all(_REAL_DATA_DIR)
        _REGISTRY = registry
    return _REGISTRY


# ================================================================== #
#  Requirement 1.1 — exactly six Branches in the vocabulary
# ================================================================== #

class TestBranchVocabularyCardinality(unittest.TestCase):
    """``RESEARCH_TREES`` / ``BRANCHES`` name exactly the six Branches.

    Branch and tree are one vocabulary seen from two angles, so the two names
    must stay the same tuple — a copy could drift.
    """

    def test_research_trees_holds_exactly_the_six_branches(self):
        self.assertEqual(len(RESEARCH_TREES), 6)
        self.assertEqual(len(set(RESEARCH_TREES)), 6, "duplicate tree name")
        self.assertEqual(set(RESEARCH_TREES), EXPECTED_BRANCHES)

    def test_branches_is_the_same_tuple_as_research_trees(self):
        self.assertIs(BRANCHES, RESEARCH_TREES)

    def test_the_three_branch_tables_key_exactly_the_six_branches(self):
        for name, table in (
            ("BRANCH_DOCTRINE", BRANCH_DOCTRINE),
            ("BRANCH_ROLE", BRANCH_ROLE),
            ("BRANCH_OPERATION_KIND", BRANCH_OPERATION_KIND),
        ):
            with self.subTest(table=name):
                self.assertEqual(set(table), set(BRANCHES))
                self.assertEqual(len(table), 6)

    def test_each_branch_owns_one_distinct_role_and_doctrine(self):
        # R7.11's bijection seen from the constants' side: six roles, six
        # doctrines, no value shared by two Branches.
        self.assertEqual(len(set(BRANCH_ROLE.values())), 6)
        self.assertEqual(len(set(BRANCH_DOCTRINE.values())), 6)

    def test_operation_kinds_derives_from_the_branch_map(self):
        self.assertEqual(OPERATION_KINDS, tuple(BRANCH_OPERATION_KIND.values()))
        self.assertEqual(len(set(OPERATION_KINDS)), 6)


# ================================================================== #
#  Requirement 7.2 — six Operation_Kind registry entries
# ================================================================== #

class TestShippedOperationKindRegistry(unittest.TestCase):
    """``branches.yaml`` binds six Operation_Kinds, one per Branch.

    Each entry names a distinct Branch and that Branch's one Carrier_Agent
    role, so no Branch is left without a Signature_Vector binding and no two
    vectors share a delivery role.
    """

    @classmethod
    def setUpClass(cls):
        cls.kinds = _real_registry().operation_kinds

    def test_registry_holds_exactly_the_six_kinds(self):
        self.assertEqual(len(self.kinds), 6)
        self.assertEqual(set(self.kinds), set(OPERATION_KINDS))

    def test_each_entry_keys_itself(self):
        for kind, kdef in self.kinds.items():
            with self.subTest(kind=kind):
                self.assertEqual(kdef.kind, kind)

    def test_each_entry_names_a_distinct_branch(self):
        branches = [kdef.branch for kdef in self.kinds.values()]
        self.assertEqual(len(set(branches)), 6)
        self.assertEqual(set(branches), set(BRANCHES))

    def test_each_entry_names_a_distinct_role_and_it_is_its_branchs_role(self):
        roles = [kdef.carrier_role for kdef in self.kinds.values()]
        self.assertEqual(len(set(roles)), 6)
        for kind, kdef in self.kinds.items():
            with self.subTest(kind=kind):
                self.assertEqual(kdef.carrier_role, BRANCH_ROLE[kdef.branch])

    def test_branch_to_kind_binding_matches_the_constant(self):
        for kind, kdef in self.kinds.items():
            with self.subTest(kind=kind):
                self.assertEqual(BRANCH_OPERATION_KIND[kdef.branch], kind)


# ================================================================== #
#  Requirement 12.1 — four BalanceConfig fields per Operation_Kind
# ================================================================== #

class TestShippedPerKindBalanceFields(unittest.TestCase):
    """Every Operation_Kind's four tunables exist and are well-formed.

    The registry entry names ``BalanceConfig`` FIELDS rather than holding the
    numbers, so the binding is only sound if each named field is a real,
    overridable dataclass field carrying a usable value.
    """

    @classmethod
    def setUpClass(cls):
        registry = _real_registry()
        cls.kinds = registry.operation_kinds
        cls.balance = registry.balance
        cls.field_names = {f.name for f in dataclasses.fields(BalanceConfig)}

    def test_each_kind_binds_four_distinctly_named_fields(self):
        named = []
        for kind, kdef in self.kinds.items():
            with self.subTest(kind=kind):
                # The documented naming convention, so a vector spec can derive
                # its field names from its kind rather than look them up.
                self.assertEqual(kdef.cost_field, f"{kind}_cost")
                self.assertEqual(kdef.cooldown_field, f"{kind}_cooldown_ticks")
                self.assertEqual(kdef.cap_field, f"{kind}_max_in_flight")
                self.assertEqual(kdef.agent_xp_field, f"agent_xp_{kind}")
            named.extend([
                kdef.cost_field, kdef.cooldown_field,
                kdef.cap_field, kdef.agent_xp_field,
            ])
        self.assertEqual(len(named), 24)
        self.assertEqual(len(set(named)), 24)

    def test_each_named_field_is_an_overridable_balance_field(self):
        for kind, kdef in self.kinds.items():
            for attr in ("cost_field", "cooldown_field", "cap_field",
                         "agent_xp_field"):
                field_name = getattr(kdef, attr)
                with self.subTest(kind=kind, field=field_name):
                    # A dataclass field, not just an attribute: that is what
                    # makes it settable from balance.yaml on a reload.
                    self.assertIn(field_name, self.field_names)
                    self.assertTrue(hasattr(self.balance, field_name))

    def test_each_per_use_cost_is_a_non_empty_resource_map(self):
        for kind, kdef in self.kinds.items():
            cost = getattr(self.balance, kdef.cost_field)
            with self.subTest(kind=kind):
                self.assertIsInstance(cost, dict)
                self.assertTrue(cost, "R12.1: every kind charges per use")
                for resource, amount in cost.items():
                    self.assertIn(resource, RESOURCE_TYPES)
                    self.assertIsInstance(amount, int)
                    self.assertGreater(amount, 0)

    def test_cooldown_cap_and_xp_are_usable_integers(self):
        for kind, kdef in self.kinds.items():
            with self.subTest(kind=kind):
                cooldown = getattr(self.balance, kdef.cooldown_field)
                cap = getattr(self.balance, kdef.cap_field)
                agent_xp = getattr(self.balance, kdef.agent_xp_field)
                for name, value in (
                    ("cooldown", cooldown), ("cap", cap), ("agent_xp", agent_xp),
                ):
                    self.assertIsInstance(value, int, name)
                    self.assertNotIsInstance(value, bool, name)
                self.assertGreater(cooldown, 0)
                self.assertGreaterEqual(cap, 1)
                self.assertGreaterEqual(agent_xp, 0)


# ================================================================== #
#  Requirement 9.1 — the shipped Counter_Web
# ================================================================== #

class TestShippedCounterWeb(unittest.TestCase):
    """The shipped web gives each Branch exactly one advantage and one
    disadvantage, so no Branch is doubly countered."""

    @classmethod
    def setUpClass(cls):
        cls.web = _real_registry().counter_web

    def test_web_keys_and_values_are_the_six_branches(self):
        self.assertEqual(set(self.web), set(BRANCHES))
        for branch, targets in self.web.items():
            with self.subTest(branch=branch):
                for target in targets:
                    self.assertIn(target, BRANCHES)

    def test_each_branch_holds_exactly_one_advantage(self):
        for branch, targets in self.web.items():
            with self.subTest(branch=branch):
                self.assertEqual(len(targets), 1)
                self.assertEqual(len(set(targets)), 1)

    def test_each_branch_carries_exactly_one_disadvantage(self):
        in_degree = {branch: 0 for branch in BRANCHES}
        for targets in self.web.values():
            for target in targets:
                in_degree[target] += 1
        for branch, count in in_degree.items():
            with self.subTest(branch=branch):
                self.assertEqual(count, 1)

    def test_no_branch_counters_itself(self):
        for branch, targets in self.web.items():
            with self.subTest(branch=branch):
                self.assertNotIn(branch, targets)

    def test_the_web_is_one_cycle_through_all_six_branches(self):
        # Out-degree 1 everywhere admits two disjoint 3-cycles; walking the
        # graph is what pins it to the single documented cycle.
        walk = [EXPECTED_COUNTER_CYCLE[0]]
        for _ in range(len(BRANCHES) - 1):
            walk.append(self.web[walk[-1]][0])
        self.assertEqual(tuple(walk), EXPECTED_COUNTER_CYCLE)
        self.assertEqual(self.web[walk[-1]][0], walk[0])


# ================================================================== #
#  Requirement 3.6 — six hosting labs, one per Branch
# ================================================================== #

class TestShippedBranchLabs(unittest.TestCase):
    """``buildings.yaml`` hosts each of the six Branches in exactly one lab."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()
        cls.labs = {
            abbr: bdef
            for abbr, bdef in cls.registry.buildings.items()
            if bdef.has_capability(RESEARCH_LAB)
        }

    def test_exactly_six_labs_ship(self):
        self.assertEqual(set(self.labs), set(EXPECTED_LAB_ABBR.values()))
        self.assertEqual(len(self.labs), 6)

    def test_tree_to_lab_is_a_bijection(self):
        hosted = {bdef.research_tree: abbr for abbr, bdef in self.labs.items()}
        self.assertEqual(len(hosted), 6, "two labs host the same Branch")
        self.assertEqual(hosted, EXPECTED_LAB_ABBR)

    def test_sg_remains_the_shield_generator(self):
        # The design named the cyber lab `SG`; the registry keys buildings by
        # abbreviation, so `SX` hosts cyber and `SG` is left alone.
        self.assertNotIn("SG", self.labs)
        self.assertEqual(self.registry.get_building("SG").name, "Shield Generator")


# ================================================================== #
#  Requirement 3.6 — the existing one-lab-per-planet limit covers all six
# ================================================================== #

class FakeTile:
    """Minimal stand-in for a placement tile (PlanetRoom)."""

    def __init__(self, x=5, y=5, planet="earth", terrain_type="Plains"):
        self._terrain_type = terrain_type
        self._building = None
        self.x = x
        self.y = y
        self.db = type("_Db", (), {
            "coord_x": x, "coord_y": y, "planet": planet, "coord_planet": planet,
        })()

    @property
    def terrain_type(self):
        return self._terrain_type

    @property
    def building(self):
        return self._building


def _make_building_system(registry) -> tuple[BuildingSystem, list]:
    """A ``BuildingSystem`` over *registry* with a recording building factory."""
    created: list = []

    def fake_create(building_def, tile, owner, x=None, y=None):
        building = FakeBuilding(
            building_type=building_def.abbreviation,
            owner=owner,
            hp=building_def.max_health,
            hp_max=building_def.max_health,
        )
        created.append(building)
        tile._building = building
        building.location = tile
        return building

    system = BuildingSystem(
        registry=registry,
        event_bus=EventBus(),
        create_building_func=fake_create,
        build_range=10,
        current_tick_func=lambda: 0,
    )
    return system, created


class TestOneResearchLabPerPlanetCoversAllSixLabs(unittest.TestCase):
    """The pre-existing one-research-lab-per-planet limit applies to all six.

    The limit is keyed on the ``research_lab`` capability, which every one of
    the six labs declares, so owning any lab must refuse every other one on the
    same planet — that refusal is what makes a Branch_Commitment exclusive
    (R3.6). No new gate is needed, and this is the test that says so.
    """

    LAB_ABBRS = tuple(EXPECTED_LAB_ABBR.values())
    REFUSAL = "one branch lab per planet"

    @classmethod
    def setUpClass(cls):
        cls.registry = _real_registry()

    def _player(self, owned_labs=(), planet="earth"):
        buildings = [FakeBuilding(building_type="HQ", planet=planet)]
        buildings += [
            FakeBuilding(building_type=abbr, planet=planet) for abbr in owned_labs
        ]
        return FakePlayer(
            resources={resource: 1000 for resource in RESOURCE_TYPES},
            buildings=buildings,
            planet=planet,
        )

    def test_each_owned_lab_refuses_the_other_five(self):
        for owned in self.LAB_ABBRS:
            for candidate in self.LAB_ABBRS:
                if candidate == owned:
                    continue
                with self.subTest(owned=owned, candidate=candidate):
                    player = self._player(owned_labs=[owned])
                    system, created = _make_building_system(self.registry)
                    ok, msg = system.construct(player, FakeTile(), candidate)
                    self.assertFalse(ok)
                    self.assertIn(self.REFUSAL, msg.lower())
                    self.assertEqual(created, [])

    def test_each_owned_lab_refuses_a_second_of_its_own_kind(self):
        for owned in self.LAB_ABBRS:
            with self.subTest(owned=owned):
                player = self._player(owned_labs=[owned])
                system, created = _make_building_system(self.registry)
                ok, msg = system.construct(player, FakeTile(), owned)
                self.assertFalse(ok)
                self.assertIn(self.REFUSAL, msg.lower())
                self.assertEqual(created, [])

    def test_the_gate_is_silent_when_no_lab_is_owned(self):
        # Guards the two loops above against passing for the wrong reason: with
        # no lab owned, whatever refuses a lab is a LATER gate (the level and
        # deed gates the shipped labs declare), never this one.
        for candidate in self.LAB_ABBRS:
            with self.subTest(candidate=candidate):
                player = self._player()
                system, _created = _make_building_system(self.registry)
                _ok, msg = system.construct(player, FakeTile(), candidate)
                self.assertNotIn(self.REFUSAL, msg.lower())


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
