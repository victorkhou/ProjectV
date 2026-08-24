"""
Property-based tests for the Technology Branch catalog.

Feature: tech-tree-branch-foundation (design section "Correctness Properties").

Implemented here:

- **Property 1**: Catalog validation reports exactly the reference violation
  set — Requirements 1.2, 1.3, 1.4, 1.5, 1.7, 2.3, 2.4, 2.7, 6.4, 6.5, 6.7,
  7.11, 9.2, 9.3, 9.12, 10.5, 12.4, 12.5.
- **Property 2**: A Branch's investment score is the weighted sum, and the
  parity flag is the tolerance comparison — Requirements 9.9, 9.10.
- **Property 3**: Definition fields round-trip through the loader with
  documented defaults — Requirements 2.1, 2.2, 6.1, 9.1, 11.5.
- **Property 4**: Registry accessors agree with a naive scan, with or without a
  global registry — Requirements 1.6, 2.6, 13.3, 15.4.
- **Property 26**: Balance-field validation reports exactly the reference
  violation set — Requirement 15.6.

That is every property the design's test-module table assigns to this file.

Every generator comes from ``branch_strategies``, which also installs the
Evennia stubs at import — hence its import is deliberately FIRST here, so this
module loads with ``evennia`` absent from ``sys.modules`` (R15.1).
"""

import math
import os
import shutil
import string
import tempfile
import unittest

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (
    BRANCH_COST_FIELDS,
    BRANCH_RANGE_FIELDS,
    CANONICAL_COUNTER_WEB,
    FIXTURE_BRANCH_BUILDING_ABBR,
    FIXTURE_BUILDING_ABBRS,
    FIXTURE_BUILDING_DICTS,
    FIXTURE_LAB_ABBR,
    FIXTURE_OPERATION_KINDS,
    FIXTURE_RANK_NAMES,
    FIXTURE_TECH_KEYS_BY_BRANCH,
    FIXTURE_TECHNOLOGY_DICTS,
    BranchDataset,
    branch_balance_dict_st,
    branch_st,
    building_def_dict_st,
    cost_map_st,
    counter_web_st,
    dataset_st,
    make_registry,
    optional_st,
    put_optional,
    role_branch_st,
    tech_def_dict_st,
)
from mygame.world.constants import (
    BRANCH_DOCTRINE,
    BRANCH_OPERATION_KIND,
    BRANCH_ROLE,
    BRANCHES,
    DEFAULT_RESOURCE_WEIGHT,
    MAX_LEVEL,
    OPERATION_KINDS,
    RESEARCH_LAB,
    RESEARCH_TREES,
    RESOURCE_TYPES,
)
from mygame.world.data_registry import _OPTIONAL_FILES, DataRegistry
from mygame.world.definitions import BalanceConfig
from mygame.world.event_bus import EventBus
from mygame.world.schema_validator import LATE_GAME_RESOURCES, SchemaValidator
from mygame.world.systems.branch_system import BranchSystem

# -------------------------------------------------------------- #
#  Local strategies: the two definition shapes branch_strategies
#  does not need to share (only this property loads them)
# -------------------------------------------------------------- #

#: A non-empty identifier: the shape every ``operations`` field must hold.
_name_st = st.text(alphabet=string.ascii_lowercase + "_", min_size=1, max_size=12)

#: Template Branch values: in-vocabulary Branches plus lowercase near-misses.
#: Restricted to an ASCII-lowercase alphabet (rather than reusing
#: ``noisy_branch_st``) so the YAML the loader reads back is byte-exact — this
#: property is about the loader's field mapping, not about PyYAML's escaping.
_template_branch_st = st.one_of(
    branch_st,
    st.text(alphabet=string.ascii_lowercase + "_", max_size=8),
)

#: NPC-base template tiers. Free-form keys in the real file; a small pool here
#: keeps the generated document a plain mapping.
_TEMPLATE_TIERS = ("outpost", "fortress", "citadel")


@st.composite
def _operations_dict_st(draw) -> dict:
    """Draw a well-formed ``branches.yaml`` ``operations`` section.

    Only well-formed entries: this property is the round-trip claim, and the
    loader's refusal of a missing/blank/unknown field is its own contract
    (``_parse_operation_kinds``), covered by the registry unit tests. A body may
    restate ``kind`` when it agrees with its key, so that path is drawn too.
    """
    kinds = draw(st.lists(st.sampled_from(OPERATION_KINDS), unique=True, max_size=6))
    operations: dict[str, dict] = {}
    for kind in kinds:
        spec = {
            "branch": draw(branch_st),
            "carrier_role": draw(_name_st),
            "cost_field": draw(_name_st),
            "cooldown_field": draw(_name_st),
            "cap_field": draw(_name_st),
            "agent_xp_field": draw(_name_st),
        }
        if draw(st.booleans()):
            spec["kind"] = kind
        operations[kind] = spec
    return operations


@st.composite
def _template_dict_st(draw) -> dict:
    """Draw an NPC-base-template YAML dict with ``branch`` present/null/absent."""
    entry: dict = {
        "display_name": draw(st.sampled_from(("Outpost", "Fortress", "Citadel"))),
        "buildings": [
            {"type": draw(st.sampled_from(FIXTURE_BUILDING_ABBRS)), "offset": [0, 0]}
        ],
        "guards": [],
    }
    put_optional(entry, "branch", draw(optional_st(_template_branch_st)))
    return entry


# ================================================================== #
#  Property 3
# ================================================================== #

# Feature: tech-tree-branch-foundation, Property 3: Definition fields round-trip
# through the loader with documented defaults
#
# **Validates: Requirements 2.1, 2.2, 6.1, 9.1, 11.5**
class TestProperty3DefinitionRoundTrip(unittest.TestCase):
    """The loader is a faithful, defaulting projection of the definition dicts.

    "Declared" means the KEY IS PRESENT — a key present and null is a declared
    null, and only an OMITTED key falls back to the documented default. For the
    two fields this feature adds to ``BuildingDef`` the two cases coincide
    (both yield ``None``, R2.2/R6.1); for the pre-existing ``TechnologyDef.tree``
    they do not, and the reference below states the loader's actual contract
    rather than assuming they agree.
    """

    @classmethod
    def setUpClass(cls):
        # One temp data root for the whole class: the NPC-base-template loader
        # reads a FILE (there is no dict-level entry point), and re-creating a
        # directory per example would dominate the run time.
        cls._data_dir = tempfile.mkdtemp(prefix="branch_catalog_prop3_")
        os.makedirs(os.path.join(cls._data_dir, "definitions"), exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._data_dir, ignore_errors=True)

    def _load_template(self, registry, tier, entry):
        """Write *entry* as the only NPC-base template and load it back."""
        path = os.path.join(self._data_dir, _OPTIONAL_FILES["outposts"])
        with open(path, "w") as handle:
            yaml.safe_dump({tier: entry}, handle)
        registry._load_base_templates(self._data_dir)

    @given(
        buildings=st.lists(
            building_def_dict_st,
            max_size=6,
            unique_by=lambda entry: entry["abbreviation"],
        ),
        technologies=st.lists(
            tech_def_dict_st, max_size=6, unique_by=lambda entry: entry["key"],
        ),
        web=counter_web_st,
        operations=_operations_dict_st(),
        tier=st.sampled_from(_TEMPLATE_TIERS),
        template=_template_dict_st(),
    )
    # deadline disabled: the NPC-base-template clause writes and re-reads a
    # small YAML file, and filesystem latency is not what this property measures.
    @settings(max_examples=100, deadline=None)
    def test_definition_fields_round_trip_with_documented_defaults(
        self, buildings, technologies, web, operations, tier, template,
    ):
        """**Validates: Requirements 2.1, 2.2, 6.1, 9.1, 11.5**"""
        registry = make_registry(buildings=buildings, technologies=technologies)

        # --- Buildings: branch + unlock_technology (R2.1, R2.2, R6.1) ---- #
        for entry in buildings:
            abbr = entry["abbreviation"]
            bdef = registry.get_building(abbr)
            for name in ("branch", "unlock_technology"):
                expected = entry[name] if name in entry else None
                self.assertEqual(
                    getattr(bdef, name), expected,
                    f"building '{abbr}': {name} did not round-trip",
                )
            if "branch" not in entry and "unlock_technology" not in entry:
                # The pre-feature shape: a Neutral_Building with no research
                # gate, which is every building shipped before this feature.
                self.assertIsNone(bdef.branch)
                self.assertIsNone(bdef.unlock_technology)

        # --- Technologies: the Branch a technology belongs to ------------- #
        for entry in technologies:
            key = entry["key"]
            tdef = registry.technologies[key]
            expected = entry["tree"] if "tree" in entry else "research"
            self.assertEqual(
                tdef.tree, expected, f"technology '{key}': tree did not round-trip",
            )

        # --- Counter_Web: a faithful projection of the file (R9.1) -------- #
        errors: list[str] = []
        loaded_web = DataRegistry._parse_counter_web(web, errors)
        self.assertEqual(errors, [], f"shape-valid Counter_Web reported {errors}")
        self.assertEqual(
            loaded_web, {source: tuple(targets) for source, targets in web.items()},
        )

        # --- Operation_Kind registry: every bound field round-trips ------- #
        errors = []
        kinds = DataRegistry._parse_operation_kinds(operations, errors)
        self.assertEqual(errors, [], f"well-formed operations reported {errors}")
        self.assertEqual(set(kinds), set(operations))
        for kind, spec in operations.items():
            kdef = kinds[kind]
            self.assertEqual(kdef.kind, kind)
            for name in (
                "branch", "carrier_role", "cost_field",
                "cooldown_field", "cap_field", "agent_xp_field",
            ):
                self.assertEqual(
                    getattr(kdef, name), spec[name],
                    f"operation '{kind}': {name} did not round-trip",
                )

        # --- Omitted sections and an absent file default to EMPTY (R9.1) -- #
        self.assertEqual(DataRegistry._parse_counter_web(None, errors), {})
        self.assertEqual(DataRegistry._parse_operation_kinds(None, errors), {})
        registry.counter_web = {"weapons": ("defense",)}
        registry.operation_kinds = dict(kinds)
        registry._load_branches(os.path.join(self._data_dir, "no_such_root"))
        self.assertEqual(registry.counter_web, {})
        self.assertEqual(registry.operation_kinds, {})

        # --- NPC-base template: the declared Branch (R11.5) --------------- #
        self._load_template(registry, tier, template)
        tdef = registry.base_templates[tier]
        self.assertEqual(
            tdef.branch, template["branch"] if "branch" in template else None,
            f"template '{tier}': branch did not round-trip",
        )


# ================================================================== #
#  Property 26
# ================================================================== #

#: The three Branch balance fields declared ``float``; every other scalar this
#: feature adds is declared ``int``. Restated from the design's Data Models
#: section rather than read off ``BalanceConfig``, so a field silently retyped
#: fails this property instead of quietly redefining what it validates.
_FLOAT_FIELDS = frozenset({
    "branch_reinstatement_cost_fraction",
    "counter_advantage_cap",
    "branch_cost_parity_tolerance",
})

#: The design's range table (§Data Models 6, R15.6), one predicate per field.
_RANGE_RULES = {
    "branch_reinstatement_cost_fraction": lambda v: 0.0 <= v <= 1.0,
    "minimum_response_window_ticks": lambda v: v >= 1,
    "counter_advantage_cap": lambda v: v >= 1.0,
    "branch_cost_parity_tolerance": lambda v: 0.0 < v <= 1.0,
    "new_player_vector_shield_level": lambda v: 1 <= v <= MAX_LEVEL,
    "escalation_window_ticks": lambda v: v >= 1,
    "escalation_cap": lambda v: v >= 1,
}


def _reference_causes(config: dict) -> set[tuple]:
    """Return the reference set of type and range violations in *config*.

    A "cause" is the field (and, inside a cost map, the resource) together with
    WHY it is invalid, so the comparison is over reasons rather than over
    message wording. Four cause kinds:

    - ``("type", field)`` — the value is not of the field's declared type.
    - ``("range", field)`` — a real, finite number outside the design's range,
      or a non-finite one (NaN/inf pass every ``isinstance`` check, so only an
      explicit finiteness test rejects them).
    - ``("unknown_resource", field, res)`` — a cost line naming no real resource.
    - ``("bad_amount", field, res)`` — a cost line whose amount is not a
      positive integer.

    Two deliberate silences, both of which the validator must match:

    - ``None`` means "not overridden", so the dataclass default stands and
      nothing is reported.
    - ``bool`` is a subclass of ``int``, so it satisfies an int/float type check
      and carries no magnitude a range rule could read. It is reported by
      neither pass.
    """
    causes: set[tuple] = set()
    for field, value in config.items():
        if value is None:
            continue
        if field in BRANCH_COST_FIELDS:
            if not isinstance(value, dict):
                causes.add(("type", field))
                continue
            for res, amount in value.items():
                if res not in RESOURCE_TYPES:
                    causes.add(("unknown_resource", field, res))
                if (
                    not isinstance(amount, int)
                    or isinstance(amount, bool)
                    or amount <= 0
                ):
                    causes.add(("bad_amount", field, res))
            continue
        if field in _FLOAT_FIELDS:
            well_typed = isinstance(value, (int, float))
        else:
            well_typed = isinstance(value, int)
        if not well_typed:
            causes.add(("type", field))
        rule = _RANGE_RULES.get(field)
        if (
            rule is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and not (math.isfinite(value) and rule(value))
        ):
            causes.add(("range", field))
    return causes


def _parse_cause(error: str) -> tuple:
    """Classify one ``validate_balance`` message into the cause it reports.

    Every message is ``balance.<locator>: <reason>``, where the locator is a
    bare field name or ``field['resource']``.
    """
    locator, separator, reason = error.partition(": ")
    assert separator and locator.startswith("balance."), f"unparsable error: {error}"
    locator = locator[len("balance."):]
    if locator.endswith("']") and "['" in locator:
        field, _, resource = locator[:-2].partition("['")
        if "unknown resource" in reason:
            return ("unknown_resource", field, resource)
        return ("bad_amount", field, resource)
    if reason.startswith("expected "):
        return ("type", locator)
    return ("range", locator)


# Feature: tech-tree-branch-foundation, Property 26: Balance-field validation
# reports exactly the reference violation set
#
# **Validates: Requirements 15.6**
class TestProperty26BalanceValidation(unittest.TestCase):
    """Every Branch balance violation, and only those, in one call."""

    def setUp(self):
        self.validator = SchemaValidator()

    @given(config=branch_balance_dict_st())
    @settings(max_examples=100)
    def test_balance_validation_reports_the_reference_violation_set(self, config):
        """**Validates: Requirements 15.6**"""
        errors = self.validator.validate_balance(config)
        reported = {_parse_cause(error) for error in errors}
        expected = _reference_causes(config)

        self.assertEqual(
            reported, expected,
            "validate_balance disagreed with the reference violation set:\n"
            f"  missing: {sorted(map(str, expected - reported))}\n"
            f"  extra:   {sorted(map(str, reported - expected))}",
        )
        # One call, one line per cause: nothing is reported twice, and nothing
        # is deferred to a second pass (R15.6's "collected error report").
        self.assertEqual(
            len(errors), len(reported), f"duplicate error lines in {errors}",
        )
        # The range table is what makes the seven cross-cutting fields more than
        # plain type checks, so assert it is actually exercised: every field the
        # reference flags for range is one of those seven.
        for cause in expected:
            if cause[0] == "range":
                self.assertIn(cause[1], BRANCH_RANGE_FIELDS)


# ================================================================== #
#  Property 1
# ================================================================== #
#
# A "cause" is (kind, locator...) — WHICH rule fired and WHAT it fired on,
# never the message wording. Two independent halves must agree with the
# reference: the per-building rules (`validate_buildings`, design rules 1-3
# plus the pre-existing research_tree vocabulary clauses that name the Branch
# a lab hosts) and the cross-file rules (`cross_validate`, design rules 4-12).
#
# The two table-only rules are ALSO driven through their injectable helpers
# (`_validate_branch_roles`, `_validate_counter_web`), because `cross_validate`
# resolves rule 9 against the live `BRANCH_ROLE` / `AGENT_ROLES` constants — a
# generated role map can only reach that rule through the helper.


#: Reasons a `validate_buildings` line may carry that belong to no Branch rule.
#: The generated definition dicts omit the four fields the pre-existing schema
#: hard-requires, so every entry carries this line; anything ELSE unrecognized
#: is reported as ``unclassified`` and fails the comparison rather than being
#: silently dropped.
_IGNORED_BUILDING_REASONS = ("missing required fields",)

#: Cross-file reasons, keyed by the message's subject, that belong to no Branch
#: rule. Same contract: this list is an allowlist, not a catch-all.
_IGNORED_CROSS_BUILDING_REASONS = (
    "required_terrain ", "cost resource ", "produces ", "unlocks ",
)
_IGNORED_CROSS_TECH_REASONS = (
    "required_rank ", "resource_cost ", "effect_value ",
)

#: ``Branch '<value>' <marker>`` → cause kind, in probe order. The two
#: Counter_Web out-degree messages share a prefix, so the "no Branch" variant
#: must be probed before the "over [...]" one.
_BRANCH_MESSAGE_MARKERS = (
    ("' is hosted by no research lab", "branch_no_lab"),
    ("' has no technology", "branch_no_tech"),
    ("' has no non-lab Branch_Building", "branch_no_building"),
    ("' has no Branch_Building gated behind an unlock_technology", "branch_no_gated"),
    ("' is hosted by multiple labs ", "branch_dup_lab"),
    ("' investment score ", "parity"),
    ("' owns Branch role(s) ", "branch_roles"),
    ("' holds a Counter_Web advantage over no Branch", "cw_no_out"),
    ("' holds a Counter_Web advantage over ", "cw_too_many"),
    ("' is countered by no Branch", "cw_no_in"),
    ("' Signature_Vector chain ", "late_game"),
)

#: ``validate_buildings`` reason fragment → cause kind, in probe order.
_BUILDING_MESSAGE_MARKERS = (
    ("unlock_technology must be a non-empty string", "bad_unlock"),
    ("declares unknown branch ", "unknown_branch"),
    ("hosts research_tree ", "lab_branch_mismatch"),
    ("a research_lab must declare a research_tree", "lab_tree_missing"),
    ("unknown research_tree ", "lab_tree_unknown"),
    (" set on a building without the ", "tree_without_lab"),
)


#: An entirely in-vocabulary Counter_Web with out-degrees from 0 to 4.
#: ``counter_web_st`` draws its targets from the noisy pool, so it almost never
#: lands three DISTINCT real Branches on one key — R9.3's out-degree ceiling
#: would go untested without this second pool alongside it.
_dense_counter_web_st = st.dictionaries(
    branch_st, st.lists(branch_st, max_size=4), max_size=6,
)

#: The two pools rule 10 is driven over: noisy (vocabulary violations) and dense
#: (degree violations).
_rule_10_web_st = st.one_of(counter_web_st, _dense_counter_web_st)


def _value_between(error: str, prefix: str, marker: str):
    """Return the text between *prefix* and *marker*, or ``None`` if absent.

    Locates a fixed prefix and a fixed marker rather than matching a pattern,
    because the value in the middle is generated: an out-of-vocabulary Branch is
    arbitrary text and may itself contain quotes, colons, or newlines. Searching
    for the marker AFTER the prefix makes the extraction exact anyway.
    """
    if not error.startswith(prefix):
        return None
    index = error.find(marker, len(prefix))
    if index < 0:
        return None
    return error[len(prefix):index]


def _parse_building_cause(error: str):
    """Classify one ``validate_buildings`` line, keyed by the entry's index.

    Returns ``None`` for a line no Branch rule produced, and
    ``("unclassified", error)`` for one this parser does not recognize — so a
    new or reworded Branch message surfaces as a mismatch instead of vanishing.
    """
    locator, separator, reason = error.partition(": ")
    if not separator or not locator.startswith("buildings["):
        return ("unclassified", error)
    index = int(locator[len("buildings["):-1])
    for fragment, kind in _BUILDING_MESSAGE_MARKERS:
        if fragment in reason:
            return (kind, index)
    if reason.startswith(_IGNORED_BUILDING_REASONS):
        return None
    return ("unclassified", error)


def _parse_cross_cause(error: str):
    """Classify one ``cross_validate`` line into the Branch cause it reports."""
    if error.startswith("Branch role '"):
        role = _value_between(error, "Branch role '", "' belongs to Branches ")
        if role is not None:
            return ("role_not_unique", role)
    if error.startswith("Branch '"):
        for marker, kind in _BRANCH_MESSAGE_MARKERS:
            value = _value_between(error, "Branch '", marker)
            if value is not None:
                return (kind, value)
    if error.startswith("Counter_Web names source '"):
        source = _value_between(
            error, "Counter_Web names source '", "', which is not one of ",
        )
        if source is not None:
            return ("cw_bad_source", source)
    if error.startswith("Counter_Web entry '"):
        itself = _value_between(error, "Counter_Web entry '", "' names itself")
        if itself is not None:
            return ("cw_self", itself)
        branch = _value_between(error, "Counter_Web entry '", "' names target '")
        if branch is not None:
            rest = error.split("' names target '", 1)[1]
            return ("cw_bad_target", branch, rest[:rest.rfind("', which is not one of ")])
    if error.startswith("building '"):
        abbr, _, reason = error[len("building '"):].partition("': ")
        if reason.startswith("unlock_technology "):
            if "not found in technology definitions" in reason:
                return ("unlock_missing", abbr)
            if "belongs to Branch " in reason:
                return ("unlock_branch_mismatch", abbr)
        elif reason.startswith("rank_requirement "):
            return ("rank_floor", abbr)
        elif reason.startswith(_IGNORED_CROSS_BUILDING_REASONS):
            return None
        return ("unclassified", error)
    if error.startswith("technology '"):
        key, _, reason = error[len("technology '"):].partition("': ")
        if reason.startswith("tree ") and "not a known tree" in reason:
            return ("tech_tree_unknown", key)
        if reason.startswith(_IGNORED_CROSS_TECH_REASONS):
            return None
        return ("unclassified", error)
    return ("unclassified", error)


def _reference_building_causes(entries) -> set[tuple]:
    """The per-building violations in *entries*, read off the YAML dicts.

    Design rules 1-3 (R2.3, R2.4, R6.1's type half) plus the three pre-existing
    clauses over ``research_tree`` — the field that names the Branch a lab hosts,
    so a typo there is a Branch error even though the rule predates the feature.

    Every clause is evaluated for every entry: the rules append rather than
    short-circuit, so one entry can carry several causes at once (R1.7).
    """
    causes: set[tuple] = set()
    for index, entry in enumerate(entries):
        capabilities = entry.get("capabilities")
        is_lab = RESEARCH_LAB in (
            capabilities if isinstance(capabilities, list) else []
        )
        tree = entry.get("research_tree")
        branch = entry.get("branch")
        unlock = entry.get("unlock_technology")

        # A lab hosts exactly one real tree; a non-lab hosts none.
        if is_lab:
            if tree is None:
                causes.add(("lab_tree_missing", index))
            elif tree not in RESEARCH_TREES:
                causes.add(("lab_tree_unknown", index))
        elif tree is not None:
            causes.add(("tree_without_lab", index))

        # Rule 1 — Branch_Affiliation vocabulary (R2.3).
        if branch is not None and branch not in BRANCHES:
            causes.add(("unknown_branch", index))
        # Rule 2 — a lab's Branch is absent or equals the Branch it hosts (R2.4).
        if is_lab and branch is not None and branch != tree:
            causes.add(("lab_branch_mismatch", index))
        # Rule 3 — unlock_technology, when present, is a non-empty string.
        if unlock is not None and (
            not isinstance(unlock, str) or not unlock.strip()
        ):
            causes.add(("bad_unlock", index))
    return causes


def _reference_role_causes(branch_role, role_branch) -> set[tuple]:
    """Rule 9: the role ↔ Branch bijection (R7.11).

    Every declaration from either table is an edge; the bijection is then the
    two degree conditions over that edge set. A role mapped to a falsy Branch
    declares nothing and contributes no edge.
    """
    branches_of_role: dict[str, set] = {}
    roles_of_branch: dict[str, set] = {branch: set() for branch in BRANCHES}

    def link(branch, role):
        branches_of_role.setdefault(role, set()).add(branch)
        roles_of_branch.setdefault(branch, set()).add(role)

    for branch, role in (branch_role or {}).items():
        link(branch, role)
    for role, branch in (role_branch or {}).items():
        if branch:
            link(branch, role)

    causes: set[tuple] = set()
    for role, branches in branches_of_role.items():
        if len(branches) != 1:
            causes.add(("role_not_unique", role))
    for branch, roles in roles_of_branch.items():
        if len(roles) != 1:
            causes.add(("branch_roles", branch))
    return causes


def _reference_counter_web_causes(counter_web) -> set[tuple]:
    """Rule 10: Counter_Web well-formedness (R9.2, R9.3, R9.12).

    Read as a set of ordered pairs. An empty web is the documented inert state
    (an absent ``branches.yaml``) and is exempt; a web that declares anything is
    claiming to be the balance web and must satisfy all four conditions. Only a
    source that IS one of the six has its targets inspected — an unknown source
    is reported once as a bad source and nothing is derived from its edges.
    """
    web = dict(counter_web or {})
    if not web:
        return set()

    def targets(source) -> list:
        raw = web.get(source) or ()
        if isinstance(raw, str) or not isinstance(
            raw, (list, tuple, set, frozenset)
        ):
            raw = (raw,)
        distinct: list = []
        for target in raw:
            if target not in distinct:
                distinct.append(target)
        return distinct

    causes: set[tuple] = set()
    # R9.12, source side.
    for source in web:
        if source not in BRANCHES:
            causes.add(("cw_bad_source", source))

    for branch in BRANCHES:
        declared = targets(branch)
        for target in declared:  # R9.12, target side.
            if target not in BRANCHES:
                causes.add(("cw_bad_target", branch, target))
        if branch in declared:
            causes.add(("cw_self", branch))
        advantages = [t for t in declared if t in BRANCHES and t != branch]
        if not advantages:  # R9.2, out-degree floor.
            causes.add(("cw_no_out", branch))
        elif len(advantages) > 2:  # R9.3, out-degree ceiling.
            causes.add(("cw_too_many", branch))
        # R9.2, in-degree floor: a Branch nothing counters is unbeatable.
        if not any(
            branch in targets(source) for source in BRANCHES if source != branch
        ):
            causes.add(("cw_no_in", branch))
    return causes


def _weigh(cost_map, weights) -> float:
    """Σ amount × the resource's weight over one cost map (R9.9).

    An unweighted resource weighs :data:`DEFAULT_RESOURCE_WEIGHT`. Accumulated
    in the map's own iteration order so the float matches the validator's bit
    for bit — the parity comparison is a strict inequality, and a reordered sum
    could disagree with it on a boundary.
    """
    if not isinstance(cost_map, dict):
        return 0.0
    total = 0.0
    for resource, amount in cost_map.items():
        try:
            total += float(amount) * float(
                weights.get(resource, DEFAULT_RESOURCE_WEIGHT)
            )
        except (TypeError, ValueError):
            continue
    return total


def _reference_investment_score(registry, branch: str, weights) -> float:
    """Rule 8's score: the Branch's whole weighted resource investment (R9.9).

    Its lab and Branch_Buildings' build costs plus its technologies' resource
    costs. A lab counts through ``research_tree`` even when it declares no
    ``branch`` of its own, and a lab declaring both counts exactly once.
    """
    score = 0.0
    for bdef in registry.buildings.values():
        if bdef.branch == branch or bdef.research_tree == branch:
            score += _weigh(bdef.cost, weights)
    for tdef in registry.technologies.values():
        if tdef.tree == branch:
            score += _weigh(tdef.resource_cost, weights)
    return score


def _live_role_branch() -> dict:
    """The role → Branch declarations rule 9 reads when nothing is injected.

    ``cross_validate`` calls ``_validate_branch_roles()`` with no arguments, so
    its rule-9 contribution comes from the live constants and not from the
    generated dataset. Resolved here exactly as the rule documents — a
    ``RoleSpec`` with no ``branch`` field yet declares nothing, which leaves the
    edge set equal to ``BRANCH_ROLE`` and the rule checking that constant's own
    bijection.
    """
    try:
        from typeclasses.agent_scripts import AGENT_ROLES
    except Exception:  # pragma: no cover - defensive: no role table importable
        return {}
    return {
        role: getattr(spec, "branch", None) for role, spec in AGENT_ROLES.items()
    }


def _reference_cross_causes(registry) -> set[tuple]:
    """The cross-file violations in a loaded *registry* (design rules 4-12).

    Mirrors the validator's two scoping decisions, because they are part of the
    contract and not incidental:

    - Rules 5, 6, 8, 12 and the coverage half of rule 4 are gated behind
      ``any_lab``. A dataset with no research lab is not claiming to be the
      shipped catalog (every minimal fixture that predates the feature is in
      that state), so it declares no Branch content to cover. The DUPLICATE half
      of rule 4 is unconditional: two labs hosting one Branch is ambiguous in
      any dataset.
    - A building whose ``branch`` is outside the six is skipped in the coverage
      tables, since rule 1 already reports it — one authoring mistake, one line.
    """
    causes: set[tuple] = set()

    # Rule 4's tables: which labs host which Branch.
    tree_to_labs: dict[str, list[str]] = {tree: [] for tree in RESEARCH_TREES}
    for abbr, bdef in registry.buildings.items():
        if RESEARCH_LAB not in bdef.capabilities:
            continue
        if bdef.research_tree in tree_to_labs:
            tree_to_labs[bdef.research_tree].append(abbr)

    # A technology's tree must be a real tree in ANY dataset — a typo makes it
    # researchable nowhere.
    trees_with_techs: set[str] = set()
    for key, tdef in registry.technologies.items():
        if tdef.tree not in RESEARCH_TREES:
            causes.add(("tech_tree_unknown", key))
        else:
            trees_with_techs.add(tdef.tree)

    # Each Branch's non-lab buildings, and the tech-gated subset the design
    # infers the Signature_Vector chain from (R6.7).
    branch_buildings: dict[str, list[str]] = {branch: [] for branch in BRANCHES}
    branch_gated: dict[str, list[str]] = {branch: [] for branch in BRANCHES}
    for abbr, bdef in registry.buildings.items():
        if RESEARCH_LAB in bdef.capabilities:
            continue
        if bdef.branch not in branch_buildings:
            continue
        branch_buildings[bdef.branch].append(abbr)
        if bdef.unlock_technology:
            branch_gated[bdef.branch].append(abbr)

    any_lab = any(labs for labs in tree_to_labs.values())
    if any_lab:
        for branch in BRANCHES:
            if not tree_to_labs[branch]:  # R1.4
                causes.add(("branch_no_lab", branch))
            if branch not in trees_with_techs:  # R1.5
                causes.add(("branch_no_tech", branch))
            if not branch_buildings[branch]:  # R2.7
                causes.add(("branch_no_building", branch))
            if not branch_gated[branch]:  # R6.7
                causes.add(("branch_no_gated", branch))

    for branch, labs in tree_to_labs.items():  # R1.3, unconditional
        if len(labs) > 1:
            causes.add(("branch_dup_lab", branch))

    # Rule 7 — the unlock FK (R6.4) and its Branch agreement (R6.5). A
    # Neutral_Building has no affiliation, so only the FK applies to it.
    for abbr, bdef in registry.buildings.items():
        unlock = bdef.unlock_technology
        if not unlock or not isinstance(unlock, str):
            continue
        tdef = registry.technologies.get(unlock)
        if tdef is None:
            causes.add(("unlock_missing", abbr))
            continue
        affiliation = bdef.branch or bdef.research_tree
        if affiliation is not None and tdef.tree != affiliation:
            causes.add(("unlock_branch_mismatch", abbr))

    # Rule 8 — investment-score parity (R9.9, R9.10). Skipped for an all-zero
    # catalog: with a mean of 0 the fractional deviation is undefined, and every
    # Branch is trivially equal anyway.
    if any_lab:
        weights = registry.balance.resource_weights
        tolerance = float(registry.balance.branch_cost_parity_tolerance)
        scores = {
            branch: _reference_investment_score(registry, branch, weights)
            for branch in BRANCHES
        }
        mean = sum(scores.values()) / len(BRANCHES)
        if mean > 0:
            for branch in BRANCHES:
                if abs(scores[branch] - mean) / mean > tolerance:
                    causes.add(("parity", branch))

    # Rule 9 — reads the live constants, not the dataset (see _live_role_branch).
    causes |= _reference_role_causes(BRANCH_ROLE, _live_role_branch())

    # Rule 10 — over the loaded Counter_Web.
    causes |= _reference_counter_web_causes(registry.counter_web)

    # Rule 11 — the Branch content rank floor (R10.5). Self-gating on "this
    # Branch has exactly one lab": with none or several there is no single floor
    # to compare against, and rule 4 already reports the ambiguity.
    for branch, labs in tree_to_labs.items():
        if len(labs) != 1:
            continue
        lab_rank = registry.buildings[labs[0]].rank_requirement
        if not isinstance(lab_rank, int) or isinstance(lab_rank, bool):
            continue  # a malformed rank is validate_buildings' error
        for abbr in branch_buildings[branch]:
            rank = registry.buildings[abbr].rank_requirement
            if not isinstance(rank, int) or isinstance(rank, bool):
                continue
            if rank < lab_rank:
                causes.add(("rank_floor", abbr))

    # Rule 12 — a late-game resource somewhere in the Signature_Vector chain
    # (R12.4, R12.5). An empty chain is rule 6's error, not this one's.
    if any_lab:
        for branch in BRANCHES:
            gated = branch_gated[branch]
            if not gated:
                continue
            reached: set[str] = set()
            for abbr in gated:
                reached.update(registry.buildings[abbr].cost or {})
            if not (reached & LATE_GAME_RESOURCES):
                causes.add(("late_game", branch))

    return causes


# Feature: tech-tree-branch-foundation, Property 1: Catalog validation reports
# exactly the reference violation set
#
# **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.7, 2.3, 2.4, 2.7, 6.4, 6.5,
# 6.7, 7.11, 9.2, 9.3, 9.12, 10.5, 12.4, 12.5**
class TestProperty1CatalogValidation(unittest.TestCase):
    """Every Branch catalog violation, and only those, in one load."""

    def setUp(self):
        self.validator = SchemaValidator()

    def _assert_causes(self, label, errors, expected, parse):
        """Assert the Branch causes *errors* reports are exactly *expected*."""
        parsed = [(error, parse(error)) for error in errors]
        branch_lines = [error for error, cause in parsed if cause is not None]
        reported = {cause for _, cause in parsed if cause is not None}

        self.assertEqual(
            reported, expected,
            f"{label} disagreed with the reference violation set:\n"
            f"  missing: {sorted(map(str, expected - reported))}\n"
            f"  extra:   {sorted(map(str, reported - expected))}",
        )
        # R1.7 — one load reports EVERY cause: the set equality above proves
        # nothing was deferred to a second pass, and this proves nothing was
        # reported twice, so the line count is the violation count.
        self.assertEqual(
            len(branch_lines), len(reported),
            f"{label} reported duplicate lines: {branch_lines}",
        )

    @given(dataset=dataset_st, web=_rule_10_web_st, role_branch=role_branch_st)
    @settings(max_examples=150)
    def test_catalog_validation_reports_the_reference_violation_set(
        self, dataset, web, role_branch,
    ):
        """**Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.7, 2.3, 2.4, 2.7,
        6.4, 6.5, 6.7, 7.11, 9.2, 9.3, 9.12, 10.5, 12.4, 12.5**"""
        buildings = [dict(entry) for entry in dataset.buildings]

        # --- The per-building rules (design rules 1-3) -------------------- #
        self._assert_causes(
            "validate_buildings",
            self.validator.validate_buildings(buildings),
            _reference_building_causes(dataset.buildings),
            _parse_building_cause,
        )

        # --- The cross-file rules (design rules 4-12) --------------------- #
        registry = dataset.registry()
        cross_errors = self.validator.cross_validate(registry)
        self._assert_causes(
            "cross_validate",
            cross_errors,
            _reference_cross_causes(registry),
            _parse_cross_cause,
        )
        # Validation is a pure read of the registry: a second load of the same
        # catalog reports the same thing, so "one load reports everything" is
        # not achieved by accumulating across calls.
        self.assertEqual(
            self.validator.cross_validate(registry), cross_errors,
            "cross_validate is not idempotent over an unchanged registry",
        )

        # --- Rule 9 over a generated role map (R7.11) --------------------- #
        # cross_validate resolves this rule against the live constants, so the
        # generated map can only reach it through the injectable helper.
        self._assert_causes(
            "_validate_branch_roles",
            self.validator._validate_branch_roles(
                branch_role=BRANCH_ROLE, role_branch=role_branch,
            ),
            _reference_role_causes(BRANCH_ROLE, role_branch),
            _parse_cross_cause,
        )

        # --- Rule 10 over a generated Counter_Web (R9.2, R9.3, R9.12) ----- #
        self._assert_causes(
            "_validate_counter_web",
            self.validator._validate_counter_web(web),
            _reference_counter_web_causes(web),
            _parse_cross_cause,
        )


# ================================================================== #
#  Property 2
# ================================================================== #
#
# Rule 8 stacks two claims, and each is checked against a different reference on
# purpose:
#
# - The arithmetic (R9.9) — what a Branch's score IS. Checked twice. Against
#   `_reference_investment_score` (Property 1's section, reused here) the
#   comparison is EXACT: that reference walks the loaded registry in the same
#   order the validator does, and bit-exactness is what makes the parity half
#   below meaningful, since rule 8's tolerance test is a strict `>` on a float
#   and a score one ulp out can flip a flag. Against `_independent_score` the
#   comparison is to a term-by-term `math.fsum` over the DRAWN cost maps, which
#   never consults the registry — that is the half able to catch a wrong
#   affiliation filter or a wrong unweighted-resource fallback rather than
#   agreeing with one.
# - The comparison (R9.10) — which Branches that score gets flagged. Checked as
#   set equality against the reference tolerance test, plus the claim that each
#   flagged line carries that Branch's score and the mean.
#
# The generated catalog is otherwise the valid fixture, so `any_lab` holds and
# rule 8 actually runs; the other rules' lines are filtered out by cause.

#: Lab abbreviation -> the Branch it hosts, and Branch_Building abbreviation ->
#: its Branch. Inverted from the fixture tables so a drawn cost map can be routed
#: onto the definition that owns it.
_BRANCH_OF_LAB: dict[str, str] = {
    abbr: branch for branch, abbr in FIXTURE_LAB_ABBR.items()
}
_BRANCH_OF_WORKS: dict[str, str] = {
    abbr: branch for branch, abbr in FIXTURE_BRANCH_BUILDING_ABBR.items()
}

#: Amounts no ``float()`` accepts. Rule 8 feeds a validator that must REPORT a
#: bad catalog rather than raise on one, so a malformed line contributes 0 and
#: the score is still a number. Nothing here is float-parseable on purpose:
#: ``"12"`` would be a perfectly good amount to ``float()``, and ``"nan"`` or
#: ``"inf"`` would poison every comparison downstream of the sum.
_MALFORMED_AMOUNTS: tuple = (None, "many", "", (), [3])

#: A cost map mixing well-formed amounts with amounts that cannot be weighed.
_noisy_cost_map_st = st.dictionaries(
    st.sampled_from(RESOURCE_TYPES),
    st.one_of(
        st.integers(min_value=1, max_value=500),
        st.sampled_from(_MALFORMED_AMOUNTS),
    ),
    max_size=4,
)

#: One cost line's worth of draw: a clean map, or one carrying malformed lines.
_parity_cost_map_st = st.one_of(cost_map_st, _noisy_cost_map_st)

#: A resource -> weight map, deliberately PARTIAL: every resource it omits is
#: scored at ``DEFAULT_RESOURCE_WEIGHT``, which is the fallback half of R9.9. A
#: zero weight is in range, so a wholly unpriced catalog is reachable.
_weight_map_st = st.dictionaries(
    st.sampled_from(RESOURCE_TYPES),
    st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    max_size=len(RESOURCE_TYPES),
)

#: Per Branch, the four cost maps that Branch owns: its lab's build cost, its
#: Branch_Building's build cost, and its two technologies' resource costs.
_branch_costs_st = st.fixed_dictionaries({
    branch: st.lists(_parity_cost_map_st, min_size=4, max_size=4)
    for branch in BRANCHES
})

#: Whether each Branch's lab restates its own ``branch`` field. R2.4 lets a lab
#: leave it absent, and rule 8 must still count that lab through
#: ``research_tree`` — a lab declaring both must count exactly once.
_lab_declares_branch_st = st.fixed_dictionaries(
    {branch: st.booleans() for branch in BRANCHES}
)

#: One draw in five blanks every cost map, which is the reliable way to reach a
#: mean of 0 — the case rule 8 documents as skipped (a fractional deviation from
#: 0 is undefined, and six equal scores are trivially in parity). Twenty-four
#: independently empty maps are reachable without it, but only by luck.
_zero_catalog_st = st.sampled_from((False, False, False, False, True))


def _independent_score(cost_maps, weights) -> float:
    """Σ amount × weight over *cost_maps*, summed independently of the registry.

    Knows from the generator which cost maps belong to the Branch, so it asserts
    the affiliation filter rather than restating it. ``math.fsum`` instead of a
    running total: this is the cross-check, and it should not reproduce the
    accumulation order of the thing it is checking.

    An amount that is not a real number contributes nothing — the same silence
    the validator keeps, since a malformed amount is ``validate_buildings``'
    error to report and not rule 8's. ``bool`` is deliberately NOT excluded:
    ``float(True)`` is 1.0, so the validator weighs it and so must this.
    """
    return math.fsum(
        float(amount) * float(weights.get(resource, DEFAULT_RESOURCE_WEIGHT))
        for cost_map in cost_maps
        for resource, amount in cost_map.items()
        if isinstance(amount, (int, float))
    )


def _parity_registry(costs, weights, tolerance, lab_declares_branch) -> DataRegistry:
    """Load the fixture catalog with *costs* substituted Branch by Branch.

    The three Neutral_Buildings keep their fixture costs and are deliberately
    left in: they belong to no Branch, so no Branch's score may include them.
    """
    buildings = []
    for entry in FIXTURE_BUILDING_DICTS:
        entry = dict(entry)
        abbr = entry["abbreviation"]
        if abbr in _BRANCH_OF_LAB:
            branch = _BRANCH_OF_LAB[abbr]
            entry["cost"] = costs[branch][0]
            if not lab_declares_branch[branch]:
                entry.pop("branch", None)
        elif abbr in _BRANCH_OF_WORKS:
            entry["cost"] = costs[_BRANCH_OF_WORKS[abbr]][1]
        buildings.append(entry)

    technologies = []
    for entry in FIXTURE_TECHNOLOGY_DICTS:
        entry = dict(entry)
        branch = entry["tree"]
        index = FIXTURE_TECH_KEYS_BY_BRANCH[branch].index(entry["key"])
        entry["resource_cost"] = costs[branch][2 + index]
        technologies.append(entry)

    return make_registry(
        buildings=buildings,
        technologies=technologies,
        counter_web=CANONICAL_COUNTER_WEB,
        operation_kinds=FIXTURE_OPERATION_KINDS,
        balance=BalanceConfig(
            resource_weights=dict(weights),
            branch_cost_parity_tolerance=tolerance,
        ),
    )


# Feature: tech-tree-branch-foundation, Property 2: A Branch's investment score
# is the weighted sum, and the parity flag is the tolerance comparison
#
# **Validates: Requirements 9.9, 9.10**
class TestProperty2InvestmentScoreParity(unittest.TestCase):
    """Rule 8 is a weighted sum and a tolerance test, and nothing besides."""

    def setUp(self):
        self.validator = SchemaValidator()

    @given(
        costs=_branch_costs_st,
        weights=_weight_map_st,
        tolerance=st.floats(min_value=0.01, max_value=1.0),
        lab_declares_branch=_lab_declares_branch_st,
        zero_catalog=_zero_catalog_st,
    )
    @settings(max_examples=100)
    def test_investment_score_is_the_weighted_sum_and_parity_is_the_tolerance(
        self, costs, weights, tolerance, lab_declares_branch, zero_catalog,
    ):
        """**Validates: Requirements 9.9, 9.10**"""
        if zero_catalog:
            costs = {branch: [{} for _ in range(4)] for branch in BRANCHES}
        registry = _parity_registry(costs, weights, tolerance, lab_declares_branch)

        # --- R9.9: the score is the weighted sum ------------------------- #
        scores: dict[str, float] = {}
        for branch in BRANCHES:
            score = self.validator._branch_investment_score(registry, branch)
            scores[branch] = score
            self.assertEqual(
                score, _reference_investment_score(registry, branch, weights),
                f"Branch '{branch}': score disagreed with the registry-walking "
                f"reference — a bit-exact match is what makes the strict '>' "
                f"tolerance comparison below reproducible",
            )
            expected = _independent_score(costs[branch], weights)
            self.assertAlmostEqual(
                score, expected, delta=max(1e-9, abs(expected) * 1e-9),
                msg=f"Branch '{branch}': score {score!r} is not the sum over its "
                    f"own cost lines {expected!r}",
            )

        # --- R9.10: the flag set is the tolerance comparison -------------- #
        mean = sum(scores.values()) / len(BRANCHES)
        expected_flagged = set()
        if mean > 0:
            expected_flagged = {
                branch for branch in BRANCHES
                if abs(scores[branch] - mean) / mean > tolerance
            }

        flagged: dict[str, str] = {}
        for error in self.validator.cross_validate(registry):
            # `_parse_cross_cause` returns None for a line no Branch rule owns.
            # The other rules' lines are expected here — a re-costed catalog
            # trips rule 12 freely — so they are skipped rather than asserted on.
            cause = _parse_cross_cause(error)
            if cause is not None and cause[0] == "parity":
                self.assertNotIn(cause[1], flagged, f"parity reported twice: {error}")
                flagged[cause[1]] = error

        self.assertEqual(
            set(flagged), expected_flagged,
            "rule 8 disagreed with the reference tolerance comparison "
            f"(mean {mean!r}, tolerance {tolerance!r}):\n"
            f"  missing: {sorted(expected_flagged - set(flagged))}\n"
            f"  extra:   {sorted(set(flagged) - expected_flagged)}\n"
            f"  scores:  {scores}",
        )
        # A flag a player's designer cannot act on is not a report: every flagged
        # line names the Branch, its score, and the mean (R9.10).
        for branch, error in flagged.items():
            self.assertIn(f"{scores[branch]:.2f}", error, f"score missing: {error}")
            self.assertIn(f"{mean:.2f}", error, f"mean missing: {error}")


# ================================================================== #
#  Property 4
# ================================================================== #
#
# The accessors resolve a Branch through the loaded definition MAPS; every
# reference below resolves it by walking the DRAWN definition DICTS. That is what
# makes this a cross-check rather than a restatement — a wrong field precedence
# (``research_tree`` winning over ``branch``), a hosting lab leaking into the
# affiliated-building list, a reordered scan, or a normalization applied on one
# layer only each show up as a mismatch.
#
# Two normalizations are deliberately kept apart, because the two layers do not
# share one:
#
# - The REGISTRY accessors compare a definition field EXACTLY:
#   ``research_lab_for_tree`` and ``get_technologies_for_tree`` are ``==``
#   filters, so a padded or blank value matches nothing.
# - The BRANCH_SYSTEM accessors compare the same field through its normalizer, so
#   a non-string, a blank string, and a whitespace-only string all collapse to
#   "no Branch" and a padded one is stripped.
#
# The two agree on every value the shipped catalog can hold; the references state
# each layer's own rule rather than assuming that agreement.


def _clean_field(value):
    """Return *value* as a non-empty stripped string, or ``None``.

    The normalization every ``BranchSystem`` identity answer passes through, so
    an absent field, a null field, a blank string, and a non-string all collapse
    to the same documented "no Branch" the accessors report (R15.3).
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _entry_capabilities(entry) -> frozenset:
    """The capability set the loader builds from one building dict."""
    return frozenset(entry.get("capabilities") or ())


def _entry_affiliation(entry):
    """The Branch a building dict belongs to: ``branch``, then ``research_tree``.

    The fallback is what makes a Branch_Lab belong to the Branch it *hosts* even
    when it omits the optional affiliation field — R2.4 requires the two to agree
    when both are set — and a dict declaring neither is a Neutral_Building (R2.2).
    """
    return (
        _clean_field(entry.get("branch"))
        or _clean_field(entry.get("research_tree"))
    )


def _entry_tree(entry):
    """The ``tree`` the loader records for one technology dict.

    An OMITTED key defaults to ``"research"``; a key present and null loads as
    the declared null — the same distinction Property 3 pins down.
    """
    return entry.get("tree", "research")


def _scan_lab_entry(buildings, branch):
    """The first lab dict hosting *branch*, or ``None``.

    ``research_tree`` is compared exactly and the first hit wins, which is
    ``DataRegistry.research_lab_for_tree``'s documented behavior over a catalog
    the validator has not accepted yet (the bijection makes it moot once it has).
    """
    for entry in buildings:
        if RESEARCH_LAB not in _entry_capabilities(entry):
            continue
        if entry.get("research_tree") == branch:
            return entry
    return None


def _scan_branch_buildings(buildings, branch):
    """The NON-LAB abbreviations affiliated with *branch*, in definition order.

    The hosting lab is deliberately absent: ``branch_buildings`` answers "what
    this commitment lets me build", and ``lab_for_branch`` reports the lab. (The
    Branch_Estate query counts the lab, but that is a question about *owned*
    buildings and not about the catalog.)
    """
    out = []
    for entry in buildings:
        if RESEARCH_LAB in _entry_capabilities(entry):
            continue
        if _entry_affiliation(entry) != branch:
            continue
        abbr = _clean_field(entry.get("abbreviation"))
        if abbr is not None:
            out.append(abbr)
    return out


def _scan_registry_tech_keys(technologies, branch):
    """The keys ``get_technologies_for_tree`` returns: an exact ``tree`` match."""
    return [entry["key"] for entry in technologies if _entry_tree(entry) == branch]


def _scan_branch_tech_keys(technologies, branch):
    """The keys the overview lists: a ``tree`` match through the normalizer."""
    out = []
    for entry in technologies:
        if _clean_field(_entry_tree(entry)) != branch:
            continue
        key = _clean_field(entry.get("key"))
        if key is not None:
            out.append(key)
    return out


def _scan_counter_web(raw):
    """Normalize a drawn Counter_Web the way the overview reads it.

    The registry keeps ``branches.yaml`` a faithful round-trip and leaves every
    content question to the validator, so the overview drops what the validator
    would have rejected — a name outside the six, a self-edge, a duplicate — and
    sorts what is left, keeping a player-facing projection free of garbage and
    deterministic. A source that normalizes onto an earlier one wins, matching a
    single pass over the mapping's own order.
    """
    web = {}
    for source, targets in (raw or {}).items():
        key = _clean_field(source)
        if key is None or key not in BRANCHES:
            continue
        if isinstance(targets, str) or not hasattr(targets, "__iter__"):
            continue
        web[key] = tuple(sorted({
            name
            for name in (_clean_field(target) for target in targets)
            if name is not None and name in BRANCHES and name != key
        }))
    return web


def _scan_overview(dataset):
    """The whole six-Branch overview, read off the drawn dicts (R13.3).

    One entry per Branch in canonical order, present even for a Branch the
    dataset loads nothing for — the overview describes all six or it is not an
    overview.
    """
    web = _scan_counter_web(dataset.counter_web)
    entries = []
    for branch in BRANCHES:
        lab = _scan_lab_entry(dataset.buildings, branch)
        entries.append({
            "branch": branch,
            "doctrine": BRANCH_DOCTRINE.get(branch),
            "lab": None if lab is None else _clean_field(lab.get("abbreviation")),
            "lab_name": None if lab is None else _clean_field(lab.get("name")),
            "role": BRANCH_ROLE.get(branch),
            "operation_kind": BRANCH_OPERATION_KIND.get(branch),
            "buildings": _scan_branch_buildings(dataset.buildings, branch),
            "technologies": _scan_branch_tech_keys(dataset.technologies, branch),
            "advantage_over": list(web.get(branch, ())),
            "countered_by": sorted(
                source for source, targets in web.items() if branch in targets
            ),
        })
    return entries


#: The ten keys ``branch_overview`` documents for every entry (R13.3). Restated
#: here rather than read off a result, so a key silently added or dropped fails.
_OVERVIEW_KEYS = frozenset({
    "branch", "doctrine", "lab", "lab_name", "role", "operation_kind",
    "buildings", "technologies", "advantage_over", "countered_by",
})

#: An abbreviation no draw can produce (``abbr_st`` is ASCII-uppercase only, and
#: every fixture abbreviation is two letters), so "the scan claims nothing the
#: definitions do not hold" is checkable with one deterministic lookup.
_ABSENT_ABBR = "__"

#: Branch -> the Branch the rival catalog's correspondingly-placed lab hosts.
#: Rotated by one, so the rival disagrees with the canonical catalog about every
#: Branch rather than only about the ones a draw happened to perturb.
_RIVAL_HOSTS = {
    branch: BRANCHES[(index + 1) % len(BRANCHES)]
    for index, branch in enumerate(BRANCHES)
}


def _rival_dataset() -> BranchDataset:
    """A second complete catalog that answers every accessor differently.

    The R15.4 half of the property needs a *conflicting* registry to install as
    the process-wide singleton, and needs it to be unmistakable: every
    abbreviation carries a digit and every technology key runs past twelve
    characters, neither of which ``dataset_st`` can draw. So a drawn catalog can
    never coincide with this one and mask an accessor that reads the singleton.
    """
    buildings: list[dict] = []
    technologies: list[dict] = []
    for index, branch in enumerate(BRANCHES):
        hosted = _RIVAL_HOSTS[branch]
        buildings.append({
            "name": f"Rival {hosted} Lab",
            "abbreviation": f"Q{index}",
            "cost": {"Iron": 50},
            "max_health": 300,
            "capabilities": [RESEARCH_LAB],
            "research_tree": hosted,
            "branch": hosted,
            "map_symbol": f"Q{index}",
        })
        buildings.append({
            "name": f"Rival {hosted} Works",
            "abbreviation": f"Y{index}",
            "cost": {"Iron": 30},
            "max_health": 200,
            "capabilities": [],
            "branch": hosted,
            "unlock_technology": f"rival_{hosted}_technology",
            "map_symbol": f"Y{index}",
        })
        technologies.append({
            "name": f"Rival {branch} Technology",
            "key": f"rival_{branch}_technology",
            "required_rank": FIXTURE_RANK_NAMES[0],
            "tree": branch,
        })
    return BranchDataset(
        buildings=tuple(buildings),
        technologies=tuple(technologies),
        counter_web={
            branch: [BRANCHES[(index + 2) % len(BRANCHES)]]
            for index, branch in enumerate(BRANCHES)
        },
    )


#: The conflicting catalog, built once — it never varies with a draw.
_RIVAL_DATASET = _rival_dataset()


# Feature: tech-tree-branch-foundation, Property 4: Registry accessors agree with
# a naive scan, with or without a global registry
#
# **Validates: Requirements 1.6, 2.6, 13.3, 15.4**
class TestProperty4RegistryAccessors(unittest.TestCase):
    """Branch identity is a linear scan of the INJECTED definitions, and nothing else.

    The R15.4 half is exercised in both directions, because clearing the
    singleton alone proves little: an accessor that reads
    ``DataRegistry.get_instance()`` and falls back to the injected registry would
    pass that test. So the same injected system is asked with no singleton
    installed AND with a conflicting one installed, and a system injected with
    the conflicting catalog is asked while the drawn one is the singleton. Each
    answer is compared to the scan over the catalog that system was INJECTED
    with, so a singleton read is a mismatch in one direction or the other.

    Every state change to the singleton is undone before the test returns, so no
    example can leak a registry into another module.
    """

    def setUp(self):
        # Captured rather than assumed to be None: this suite installs no
        # singleton (and no other Branch test module does either), but restoring
        # what was actually there cannot leak a registry in either direction.
        self._original_registry = DataRegistry.get_instance()

    def tearDown(self):
        DataRegistry.set_instance(self._original_registry)

    def _assert_scan_agreement(self, system, dataset, branch):
        """Assert every accessor on *system* equals the scan over *dataset*.

        Called once per singleton state. Each clause compares against a
        reference computed from the dataset alone — which is singleton-blind —
        so equality in every state also establishes that the answers are
        identical across states.
        """
        registry = system.registry
        buildings = dataset.buildings
        technologies = dataset.technologies

        # --- The hosting lab (R1.6) -------------------------------------- #
        lab_entry = _scan_lab_entry(buildings, branch)
        lab_def = registry.research_lab_for_tree(branch)
        if lab_entry is None:
            self.assertIsNone(
                lab_def,
                f"no drawn definition hosts '{branch}', yet the registry "
                f"reported {lab_def!r}",
            )
        else:
            self.assertIsNotNone(
                lab_def,
                f"'{lab_entry['abbreviation']}' hosts '{branch}' in the "
                f"definitions, yet the registry found no lab for it",
            )
            self.assertEqual(
                lab_def.abbreviation, lab_entry["abbreviation"],
                f"the lab the registry reports for '{branch}' is not the first "
                f"one the definitions declare",
            )
        self.assertEqual(
            system.lab_for_branch(branch),
            None if lab_entry is None else _clean_field(lab_entry["abbreviation"]),
            f"lab_for_branch('{branch}') disagreed with the definition scan",
        )

        # --- The affiliated Branch_Buildings, in definition order (R2.6) -- #
        self.assertEqual(
            system.branch_buildings(branch),
            _scan_branch_buildings(buildings, branch),
            f"branch_buildings('{branch}') disagreed with the definition scan "
            f"— the hosting lab is excluded and the order is the registry's own",
        )

        # --- The affiliation of every loaded definition (R2.6) ------------ #
        for entry in buildings:
            abbr = entry["abbreviation"]
            expected = _entry_affiliation(entry)
            self.assertEqual(
                system.branch_of_building(abbr), expected,
                f"branch_of_building('{abbr}') disagreed with its definition",
            )
            # The same question through a resolved definition object, and through
            # the case a player would type: all three are documented as accepted
            # and so must give one answer.
            self.assertEqual(
                system.branch_of_building(registry.get_building(abbr)), expected,
                f"branch_of_building disagreed with itself for '{abbr}' when "
                f"handed the definition rather than the abbreviation",
            )
            self.assertEqual(
                system.branch_of_building(abbr.lower()), expected,
                f"branch_of_building did not resolve '{abbr}' case-insensitively",
            )
        # "Exactly what the scan returns" includes claiming nothing for what the
        # definitions do not hold.
        self.assertIsNone(
            system.branch_of_building(_ABSENT_ABBR),
            "branch_of_building resolved an abbreviation no definition declares",
        )

        # --- The Branch of every loaded technology (R1.6) ----------------- #
        for entry in technologies:
            key = entry["key"]
            self.assertEqual(
                system.branch_of_technology(key), _clean_field(_entry_tree(entry)),
                f"branch_of_technology('{key}') disagreed with its definition",
            )
        self.assertEqual(
            [tdef.key for tdef in registry.get_technologies_for_tree(branch)],
            _scan_registry_tech_keys(technologies, branch),
            f"get_technologies_for_tree('{branch}') disagreed with the "
            f"definition scan",
        )

        # --- The Branch's Carrier_Agent role (R13.3) ---------------------- #
        # A constants read, so the answer is catalog-independent — which is the
        # claim, since this loop runs over two different catalogs.
        self.assertEqual(
            system.role_for_branch(branch), BRANCH_ROLE[branch],
            f"role_for_branch('{branch}') is not the role the Branch owns",
        )

        # --- The whole overview projection (R13.3) ------------------------ #
        overview = system.branch_overview()
        self.assertEqual(
            [entry["branch"] for entry in overview], list(BRANCHES),
            "branch_overview did not report all six Branches in canonical order",
        )
        for entry in overview:
            self.assertEqual(
                set(entry), set(_OVERVIEW_KEYS),
                f"the overview entry for '{entry.get('branch')}' does not carry "
                f"exactly the ten documented keys",
            )
        self.assertEqual(
            overview, _scan_overview(dataset),
            "branch_overview disagreed with the definition scan",
        )
        # The overview describes the catalog, not a caller, so it is stable
        # between calls.
        self.assertEqual(
            overview, system.branch_overview(),
            "branch_overview is not stable between calls",
        )

    @given(dataset=dataset_st, branch=branch_st)
    @settings(max_examples=100)
    def test_registry_accessors_agree_with_a_naive_scan(self, dataset, branch):
        """**Validates: Requirements 1.6, 2.6, 13.3, 15.4**"""
        registry = dataset.registry()
        rival = _RIVAL_DATASET.registry()
        # The references enumerate the DRAWN dicts while the accessors read maps
        # keyed by abbreviation and by technology key, so the comparison is only
        # meaningful while those keys are unique within a draw.
        self.assertEqual(
            len(registry.buildings), len(dataset.buildings),
            "the drawn buildings do not have unique abbreviations, so the "
            "definition scan cannot be compared to the registry's map",
        )
        self.assertEqual(
            len(registry.technologies), len(dataset.technologies),
            "the drawn technologies do not have unique keys",
        )

        system = BranchSystem(registry, EventBus())
        rival_system = BranchSystem(rival, EventBus())
        try:
            # R15.4 — with NO process-wide registry, then with a CONFLICTING one.
            for singleton in (None, rival):
                DataRegistry.set_instance(singleton)
                self.assertIs(
                    DataRegistry.get_instance(), singleton,
                    "the singleton was not installed, so this example would "
                    "prove nothing about R15.4",
                )
                self._assert_scan_agreement(system, dataset, branch)
            # The other direction: the injected catalog wins even when the
            # singleton holds the one the other system was built on.
            DataRegistry.set_instance(registry)
            self._assert_scan_agreement(rival_system, _RIVAL_DATASET, branch)
        finally:
            DataRegistry.set_instance(None)


if __name__ == "__main__":
    unittest.main()
