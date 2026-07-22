"""
Property-based tests for terrain modifier loading.

# Feature: terrain-strategy, Property 1: Terrain modifier load round-trip with zero defaults

**Validates: Requirements 1.1, 1.2, 1.3, 1.5, 1.6**

For any terrain definition set where each of the three modifier fields
(vision_modifier, movement_modifier, defense_modifier) is independently a
valid number, missing, or null, ``load_all`` succeeds without validation
errors, and the TerrainDef returned by the registry's terrain lookup carries
exactly the provided numeric values, with every missing/null field defaulted
to zero.

# Feature: terrain-strategy, Property 2: Non-numeric terrain modifiers fail fast,
# collectively and atomically

**Validates: Requirements 1.4**

For any terrain definition set containing one or more non-numeric modifier
values (bool, string, list, ...), ``load_all`` raises ``DataRegistryError``
whose message identifies every offending terrain type and field name, and a
``reload_all`` against such data returns failure while leaving all currently
loaded registry data unchanged.

# Feature: terrain-strategy, Property 3: Base resolution equals the generator's
# terrain definition

**Validates: Requirements 2.1, 2.3, 2.5, 2.6**

For any real ``TerrainGenerator`` (seed, weights, coordinate) and in-memory
registry, ``resolve_base(planet, x, y)`` returns exactly the sign-preserving
clamped TerrainDef modifier values for ``generator.get_terrain(x, y)`` — and
``ZERO_MODIFIERS`` when no generator exists for the planet or the resolved
terrain type has no TerrainDef — regardless of any class or technology
affinity data present in the system.

# Feature: terrain-strategy, Property 4: Affinity summation

**Validates: Requirements 2.2, 6.2, 6.3, 6.7, 7.3, 7.4**

For any fake player with generated class terrain affinities and
``db.tech_bonuses`` content, each player-resolved modifier kind equals
``clamp(base + Σ matching class adjustments + Σ matching tech adjustments)``
— multiple class affinities for the same (terrain, kind) pair sum, and
technology contributions are exactly the ``db.tech_bonuses`` dict content
under the matching structured key — while kinds without any matching class
or technology affinity resolve to the clamped base value.

# Feature: terrain-strategy, Property 5: Resolution determinism

**Validates: Requirements 2.4**

For any fixed combination of planet, coordinate, terrain generator epoch,
player class, and completed terrain technologies, repeated resolution
queries — including queries interleaved with resolutions for other
coordinates and other players — return identical modifier values every time.

# Feature: terrain-strategy, Property 6: Sign-preserving clamp on every resolver output

**Validates: Requirements 9.2, 9.5**

For any input state and any non-negative per-kind balance bounds, every
value returned by ``resolve_base`` and ``resolve_for_player`` satisfies
``|value| <= bound(kind)``: totals within the bound pass through unchanged,
and totals exceeding the bound are replaced by the bound magnitude carrying
the total's sign (vision truncated toward zero after clamping).

# Feature: terrain-strategy, Property 13: Affinity load round-trip

**Validates: Requirements 6.1, 7.1**

For any valid class definition set with terrain affinity lists and any valid
terrain technology set, loading succeeds and the ``ClassDef.terrain_affinities``
entries and ``TechnologyDef.effect_value`` payloads read back from the registry
equal the yaml input (affinity adjustments coerced to float by the loader).

# Feature: terrain-strategy, Property 14: Affinity validation fails fast, collectively

**Validates: Requirements 6.5, 6.6, 7.5**

For any class definition set containing invalid affinity entries (unknown
terrain type, invalid kind, non-numeric adjustment) or unbalanced
positive-only classes, ``load_all`` raises ``DataRegistryError`` identifying
every invalid entry and every unbalanced class. For any technology set with
invalid terrain-affinity effect payloads, ``load_all`` raises
``DataRegistryError`` identifying every invalid entry of the first failing
validation phase (schema errors abort the load before the cross-file
unknown-terrain check runs).

# Feature: terrain-strategy, Property 15: Research completion records terrain adjustments

**Validates: Requirements 7.2, 7.6**

For any set of terrain technologies, completing research on each through the
real TechLabSystem writes its structured ``terrain_affinity:{terrain}:{kind}``
adjustments into ``db.tech_bonuses``, values for the same key summing across
technologies, and ``recompute_tech_bonuses`` reproduces the same dict from the
researched set (idempotent rebuild). An example-based check asserts reconnect
equivalence (Req 7.6): a new player object carrying the same persisted
``db.tech_bonuses`` dict resolves identical modifier values.
"""

import os
import shutil
from math import copysign

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from mygame.world.coordinate.terrain_generator import TerrainGenerator
from mygame.world.data_registry import DataRegistry, DataRegistryError
from mygame.world.definitions import (
    CoordinateSpaceDef,
    TechnologyDef,
    TerrainAffinity,
    TerrainDef,
)
from mygame.world.event_bus import EventBus
from mygame.world.systems.tech_system import TechLabSystem
from mygame.world.systems.terrain_modifiers import (
    ZERO_MODIFIERS,
    TerrainModifierSystem,
)
from mygame.world.tests.test_terrain_modifiers import (
    FakeDb,
    FakeGenerator,
    FakePlayer,
    FakeRegistry,
)
from mygame.world.tests.test_prop_hot_reload import (
    VALID_RANKS,
    VALID_TERRAIN,
    _create_data_dir,
    _snapshot_registry,
    _write_yaml,
)

# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

#: Sentinel marking a modifier field that is omitted from the yaml entry.
#: Distinct from None, which is written into the yaml as an explicit null.
MISSING = "<missing>"

MODIFIER_FIELDS = ("vision_modifier", "movement_modifier", "defense_modifier")

# Valid numeric values: ints and finite floats (NaN breaks equality checks
# and infinities are not meaningful modifier magnitudes).
numeric_value = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    st.floats(min_value=-1000.0, max_value=1000.0,
              allow_nan=False, allow_infinity=False),
)

# Each modifier field is independently: a valid number, missing, or null.
modifier_field = st.one_of(st.just(MISSING), st.none(), numeric_value)

terrain_modifiers = st.fixed_dictionaries({
    field: modifier_field for field in MODIFIER_FIELDS
})

# One independently generated modifier dict per baseline terrain type.
mods_by_terrain = st.fixed_dictionaries({
    entry["terrain_type"]: terrain_modifiers
    for entry in VALID_TERRAIN["terrain"]
})

ALL_MISSING = {field: MISSING for field in MODIFIER_FIELDS}


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _build_terrain_data(mods: dict) -> dict:
    """Build a terrain.yaml payload from the baseline fixtures plus the
    generated modifier fields (MISSING fields are omitted entirely)."""
    terrain_entries = []
    for base in VALID_TERRAIN["terrain"]:
        entry = dict(base)
        for field, value in mods[entry["terrain_type"]].items():
            if value is not MISSING:
                entry[field] = value
        terrain_entries.append(entry)
    return {
        "terrain": terrain_entries,
        "planets": [dict(p) for p in VALID_TERRAIN["planets"]],
    }


def _expected(value):
    """Missing/null defaults to zero; numeric values load exactly."""
    return 0 if value is MISSING or value is None else value


# ================================================================== #
#  Property 1: Terrain modifier load round-trip with zero defaults
# ================================================================== #

class TestProperty1TerrainModifierLoadRoundTrip:
    """**Validates: Requirements 1.1, 1.2, 1.3, 1.5, 1.6**"""

    @given(mods=mods_by_terrain)
    @example(mods={t["terrain_type"]: dict(ALL_MISSING)
                   for t in VALID_TERRAIN["terrain"]})
    @example(mods={t["terrain_type"]: {f: None for f in MODIFIER_FIELDS}
                   for t in VALID_TERRAIN["terrain"]})
    @settings(max_examples=100, deadline=10000)
    def test_load_round_trip_with_zero_defaults(self, mods):
        """load_all succeeds and TerrainDef carries exact values, with
        missing/null modifier fields defaulted to zero."""
        tmpdir = _create_data_dir()
        try:
            _write_yaml(
                os.path.join(tmpdir, "definitions", "terrain.yaml"),
                _build_terrain_data(mods),
            )

            reg = DataRegistry()
            # Req 1.1, 1.3, 1.5: valid numbers, missing, and null values all
            # load without a validation error (all-missing merely warns).
            reg.load_all(tmpdir)

            for terrain_type, fields in mods.items():
                # Req 1.6: modifiers are exposed via the registry lookup.
                tdef = reg.terrain[terrain_type]
                for field, value in fields.items():
                    loaded = getattr(tdef, field)
                    expected = _expected(value)
                    # Req 1.1 exact values; Req 1.2/1.3 zero defaults.
                    assert loaded == expected, (
                        f"{terrain_type}.{field}: expected {expected!r} "
                        f"(from yaml value {value!r}), got {loaded!r}"
                    )
        finally:
            shutil.rmtree(tmpdir)


# ================================================================== #
#  Property 2: Non-numeric terrain modifiers fail fast,
#  collectively and atomically
# ================================================================== #

TERRAIN_TYPES = tuple(e["terrain_type"] for e in VALID_TERRAIN["terrain"])

# Non-numeric modifier values (Req 1.4): booleans (int subclass — rejected
# explicitly), strings, and lists. String alphabet avoids characters yaml
# could reinterpret and cannot collide with the MISSING sentinel.
invalid_value = st.one_of(
    st.booleans(),
    st.text(alphabet="abcdefghij", min_size=1, max_size=8),
    st.lists(st.integers(min_value=0, max_value=9), min_size=1, max_size=3),
)

# A field in the invalid-set generator: valid number, missing, null, or bad.
mixed_field = st.one_of(st.just(MISSING), st.none(), numeric_value, invalid_value)


def _is_invalid(value) -> bool:
    """True when *value* is a non-numeric modifier value written to yaml."""
    return value is not MISSING and (
        isinstance(value, bool) or isinstance(value, (str, list))
    )


@st.composite
def mods_with_at_least_one_invalid(draw):
    """Modifier dicts per terrain type with >= 1 non-numeric value overall."""
    mods = {
        terrain_type: {field: draw(mixed_field) for field in MODIFIER_FIELDS}
        for terrain_type in TERRAIN_TYPES
    }
    # Force at least one offender so every generated set must fail the load.
    terrain_type = draw(st.sampled_from(TERRAIN_TYPES))
    field = draw(st.sampled_from(MODIFIER_FIELDS))
    mods[terrain_type][field] = draw(invalid_value)
    return mods


def _invalid_pairs(mods: dict) -> list[tuple[str, str]]:
    """Every (terrain_type, field) carrying a non-numeric value."""
    return [
        (terrain_type, field)
        for terrain_type, fields in mods.items()
        for field, value in fields.items()
        if _is_invalid(value)
    ]


def _terrain_modifier_snapshot(reg: DataRegistry) -> dict:
    """Terrain modifier values currently loaded, keyed by terrain type."""
    return {
        terrain_type: (
            tdef.vision_modifier, tdef.movement_modifier, tdef.defense_modifier
        )
        for terrain_type, tdef in reg.terrain.items()
    }


class TestProperty2NonNumericModifiersFailFast:
    """**Validates: Requirements 1.4**"""

    @given(mods=mods_with_at_least_one_invalid())
    @settings(max_examples=100, deadline=10000)
    def test_load_all_names_every_offending_terrain_and_field(self, mods):
        """load_all raises DataRegistryError identifying every offending
        terrain type and field name, collected across all definitions."""
        tmpdir = _create_data_dir()
        try:
            _write_yaml(
                os.path.join(tmpdir, "definitions", "terrain.yaml"),
                _build_terrain_data(mods),
            )

            reg = DataRegistry()
            with pytest.raises(DataRegistryError) as excinfo:
                reg.load_all(tmpdir)

            message = str(excinfo.value)
            for terrain_type, field in _invalid_pairs(mods):
                # Validator format:
                #   terrain[i] ('<terrain_type>'): <field> must be a number
                expected = f"('{terrain_type}'): {field} must be a number"
                assert expected in message, (
                    f"DataRegistryError must name {terrain_type}.{field}; "
                    f"missing {expected!r} in:\n{message}"
                )
        finally:
            shutil.rmtree(tmpdir)

    @given(mods=mods_with_at_least_one_invalid())
    @settings(max_examples=100, deadline=10000)
    def test_failed_reload_preserves_loaded_registry_data(self, mods):
        """reload_all against non-numeric modifier data fails and leaves all
        currently loaded registry data unchanged (atomicity)."""
        tmpdir = _create_data_dir()
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            snapshot_before = _snapshot_registry(reg)
            modifiers_before = _terrain_modifier_snapshot(reg)

            _write_yaml(
                os.path.join(tmpdir, "definitions", "terrain.yaml"),
                _build_terrain_data(mods),
            )

            success, errors = reg.reload_all()

            assert success is False, (
                "reload_all must fail on non-numeric terrain modifiers"
            )
            joined = "\n".join(errors)
            for terrain_type, field in _invalid_pairs(mods):
                expected = f"('{terrain_type}'): {field} must be a number"
                assert expected in joined, (
                    f"reload errors must name {terrain_type}.{field}; "
                    f"missing {expected!r} in:\n{joined}"
                )

            assert _snapshot_registry(reg) == snapshot_before, (
                "Registry state must be unchanged after failed reload"
            )
            assert _terrain_modifier_snapshot(reg) == modifiers_before, (
                "Terrain modifier values must be unchanged after failed reload"
            )
        finally:
            shutil.rmtree(tmpdir)


# ================================================================== #
#  Property 13: Affinity load round-trip
# ================================================================== #
# Feature: terrain-strategy, Property 13: Affinity load round-trip

AFFINITY_KINDS = ("vision", "movement", "defense")

RANK_NAMES = tuple(r["name"] for r in VALID_RANKS)

# A single class affinity yaml entry against the baseline terrain types.
affinity_entry = st.fixed_dictionaries({
    "terrain_type": st.sampled_from(TERRAIN_TYPES),
    "kind": st.sampled_from(AFFINITY_KINDS),
    "adjustment": numeric_value,
})

# Strictly negative adjustments, used to balance positive-only classes.
negative_adjustment = st.one_of(
    st.integers(min_value=-1000, max_value=-1),
    st.floats(min_value=-1000.0, max_value=-0.5,
              allow_nan=False, allow_infinity=False),
)


@st.composite
def balanced_class(draw, key: str) -> dict:
    """A valid class yaml entry honoring the sidegrade rule: any class with
    a positive-adjustment affinity also carries a negative-adjustment
    affinity or a negative stat_modifiers value, plus a non-empty
    description (only valid, balanced classes are generated)."""
    affinities = draw(st.lists(affinity_entry, min_size=0, max_size=4))
    entry = {
        "key": key,
        "name": key.capitalize(),
        "description": "Strong somewhere, weak somewhere else.",
    }
    has_positive = any(a["adjustment"] > 0 for a in affinities)
    has_negative = any(a["adjustment"] < 0 for a in affinities)
    if has_positive and not has_negative:
        if draw(st.booleans()):
            weakness = draw(affinity_entry)
            weakness["adjustment"] = draw(negative_adjustment)
            affinities.append(weakness)
        else:
            entry["stat_modifiers"] = {
                "damage_reduction": draw(st.integers(min_value=-10, max_value=-1)),
            }
    entry["terrain_affinities"] = affinities
    return entry


@st.composite
def class_definition_sets(draw) -> list[dict]:
    """A classes.yaml payload of 1-3 balanced classes with unique keys."""
    count = draw(st.integers(min_value=1, max_value=3))
    return [draw(balanced_class(f"class{i}")) for i in range(count)]


# effect_value keys of the structured "terrain_affinity:{terrain}:{kind}"
# form, restricted to baseline terrain types and valid kinds.
affinity_effect_key = st.builds(
    "terrain_affinity:{}:{}".format,
    st.sampled_from(TERRAIN_TYPES),
    st.sampled_from(AFFINITY_KINDS),
)

affinity_effect_value = st.dictionaries(
    affinity_effect_key, numeric_value, min_size=1, max_size=4,
)


@st.composite
def terrain_technology_sets(draw) -> list[dict]:
    """A technologies.yaml payload of 1-3 valid terrain technologies."""
    count = draw(st.integers(min_value=1, max_value=3))
    return [
        {
            "name": f"Terrain Tech {i}",
            "key": f"terrain_tech_{i}",
            "required_rank": draw(st.sampled_from(RANK_NAMES)),
            "resource_cost": {"Stone": 10 * (i + 1)},
            "research_ticks": 5,
            "effect_type": "terrain_affinity",
            "effect_value": draw(affinity_effect_value),
        }
        for i in range(count)
    ]


def _affinity_tuples(affinities) -> list[tuple]:
    """Compare TerrainAffinity by field values: the registry may build them
    from a differently-imported module object (world. vs mygame.world. import
    path), which breaks direct dataclass equality across the split."""
    return [(a.terrain_type, a.kind, a.adjustment) for a in affinities]


class TestProperty13AffinityLoadRoundTrip:
    """**Validates: Requirements 6.1, 7.1**"""

    @given(classes=class_definition_sets(), techs=terrain_technology_sets())
    @settings(max_examples=100, deadline=10000)
    def test_affinity_load_round_trip(self, classes, techs):
        """Valid class affinity lists and terrain technology sets load, and
        ClassDef.terrain_affinities / TechnologyDef.effect_value read back
        equal to the yaml input (adjustments coerced to float)."""
        tmpdir = _create_data_dir()
        try:
            _write_yaml(
                os.path.join(tmpdir, "definitions", "classes.yaml"),
                {"classes": classes},
            )
            _write_yaml(
                os.path.join(tmpdir, "definitions", "technologies.yaml"),
                techs,
            )

            reg = DataRegistry()
            # Req 6.1 / 7.1: valid affinity data loads without errors.
            reg.load_all(tmpdir)

            for entry in classes:
                cdef = reg.classes[entry["key"]]
                expected = [
                    (a["terrain_type"], a["kind"], float(a["adjustment"]))
                    for a in entry["terrain_affinities"]
                ]
                loaded = _affinity_tuples(cdef.terrain_affinities)
                # Req 6.1: affinities read back equal to the yaml input.
                assert loaded == expected, (
                    f"Class {entry['key']!r} affinities: expected "
                    f"{expected!r}, got {loaded!r}"
                )

            for entry in techs:
                tdef = reg.technologies[entry["key"]]
                # Req 7.1: effect_value reads back equal to the yaml input.
                assert tdef.effect_value == entry["effect_value"], (
                    f"Technology {entry['key']!r} effect_value: expected "
                    f"{entry['effect_value']!r}, got {tdef.effect_value!r}"
                )
        finally:
            shutil.rmtree(tmpdir)


# ================================================================== #
#  Property 14: Affinity validation fails fast, collectively
# ================================================================== #
# Feature: terrain-strategy, Property 14: Affinity validation fails fast, collectively

# Suffix alphabet cannot form yaml-reserved words (null/true/false/no/yes),
# so invalid strings round-trip through yaml unchanged.
_SAFE_SUFFIX = st.text(alphabet="abcdefghij", min_size=1, max_size=6)

# "zz_" prefix guarantees no collision with the baseline terrain types or
# with the valid affinity kinds.
unknown_terrain = _SAFE_SUFFIX.map(lambda s: "zz_" + s)
invalid_kind = _SAFE_SUFFIX.map(lambda s: "zz_" + s)

# Non-numeric adjustments/values: bool (int subclass, rejected explicitly),
# yaml null, and plain strings — all with stable reprs after yaml round-trip.
non_numeric_value = st.one_of(
    st.booleans(),
    st.none(),
    st.text(alphabet="abcdefghij", min_size=1, max_size=6),
)

# A class affinity entry whose three fields are independently valid or bad.
mixed_affinity_entry = st.fixed_dictionaries({
    "terrain_type": st.one_of(st.sampled_from(TERRAIN_TYPES), unknown_terrain),
    "kind": st.one_of(st.sampled_from(AFFINITY_KINDS), invalid_kind),
    "adjustment": st.one_of(numeric_value, non_numeric_value),
})


def _is_numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _expected_class_errors(classes: list[dict]) -> list[str]:
    """Mirror the registry's class affinity validation: the exact error line
    for every invalid affinity entry and every unbalanced class (Req 6.5/6.6).
    Only fully valid entries count toward the sidegrade-rule check."""
    errors = []
    for entry in classes:
        key = entry["key"]
        valid_adjustments = []
        for idx, spec in enumerate(entry["terrain_affinities"]):
            terrain_type = spec.get("terrain_type")
            kind = spec.get("kind")
            adjustment = spec.get("adjustment")
            valid = True
            if terrain_type not in TERRAIN_TYPES:
                errors.append(
                    f"Class '{key}': terrain_affinities[{idx}] names "
                    f"unknown terrain_type {terrain_type!r}."
                )
                valid = False
            if kind not in AFFINITY_KINDS:
                errors.append(
                    f"Class '{key}': terrain_affinities[{idx}] has invalid "
                    f"kind {kind!r} (expected one of vision, movement, defense)."
                )
                valid = False
            if not _is_numeric(adjustment):
                errors.append(
                    f"Class '{key}': terrain_affinities[{idx}] has "
                    f"non-numeric adjustment {adjustment!r}."
                )
                valid = False
            if valid:
                valid_adjustments.append(adjustment)
        has_positive = any(a > 0 for a in valid_adjustments)
        has_negative = any(a < 0 for a in valid_adjustments)
        stat_mods = entry.get("stat_modifiers") or {}
        has_negative_stat = any(
            _is_numeric(val) and val < 0 for val in stat_mods.values()
        )
        if has_positive and not (has_negative or has_negative_stat):
            errors.append(
                f"Class '{key}': has a positive terrain affinity but no "
                "offsetting negative affinity or negative stat_modifiers value."
            )
    return errors


@st.composite
def invalid_class_sets(draw) -> list[dict]:
    """1-3 classes with freely mixed valid/invalid affinity entries and
    optional stat_modifiers; guaranteed to carry at least one invalid
    affinity entry or one unbalanced positive-only class."""
    count = draw(st.integers(min_value=1, max_value=3))
    classes = []
    for i in range(count):
        entry = {
            "key": f"class{i}",
            "name": f"Class{i}",
            "description": "Strong somewhere, weak somewhere else.",
            "terrain_affinities": draw(
                st.lists(mixed_affinity_entry, min_size=0, max_size=4)
            ),
        }
        if draw(st.booleans()):
            entry["stat_modifiers"] = {
                "damage_reduction": draw(st.integers(min_value=-10, max_value=10)),
            }
        classes.append(entry)
    if not _expected_class_errors(classes):
        idx = draw(st.integers(min_value=0, max_value=count - 1))
        if draw(st.booleans()):
            # Inject an invalid entry (unknown terrain type).
            bad = draw(mixed_affinity_entry)
            bad["terrain_type"] = draw(unknown_terrain)
            classes[idx]["terrain_affinities"].append(bad)
        else:
            # Make the class unbalanced: one positive affinity, no offset.
            classes[idx].pop("stat_modifiers", None)
            classes[idx]["terrain_affinities"] = [{
                "terrain_type": draw(st.sampled_from(TERRAIN_TYPES)),
                "kind": draw(st.sampled_from(AFFINITY_KINDS)),
                "adjustment": draw(st.integers(min_value=1, max_value=100)),
            }]
    return classes


# --- Technology effect_value key strategies ----------------------- #

# Schema-valid key naming a terrain type absent from terrain.yaml: passes
# validate_technologies, flagged only by the cross_validate phase (Req 7.5).
unknown_terrain_key = st.builds(
    "terrain_affinity:{}:{}".format,
    unknown_terrain,
    st.sampled_from(AFFINITY_KINDS),
)

# Well-formed key with an invalid kind segment (schema phase).
bad_kind_key = st.builds(
    "terrain_affinity:{}:{}".format,
    st.one_of(st.sampled_from(TERRAIN_TYPES), unknown_terrain),
    invalid_kind,
)

# Keys that do not match 'terrain_affinity:{terrain_type}:{kind}' at all.
malformed_key = st.one_of(
    _SAFE_SUFFIX.map(lambda s: "zz_" + s),
    st.sampled_from(TERRAIN_TYPES).map(lambda t: f"terrain_affinity:{t}"),
    st.sampled_from(AFFINITY_KINDS).map(lambda k: f"terrain_affinity::{k}"),
)

mixed_effect_key = st.one_of(
    affinity_effect_key, unknown_terrain_key, bad_kind_key, malformed_key,
)

mixed_effect_value = st.dictionaries(
    mixed_effect_key,
    st.one_of(numeric_value, non_numeric_value),
    min_size=1,
    max_size=4,
)


def _parse_affinity_key(key: str):
    """Mirror SchemaValidator._parse_affinity_key for string keys."""
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "terrain_affinity" or not parts[1]:
        return None
    return parts[1], parts[2]


def _expected_tech_errors(techs: list[dict]) -> tuple[list[str], list[str]]:
    """Mirror the technology affinity validation, split by phase: schema
    errors (validate_technologies) and cross-file unknown-terrain errors
    (cross_validate). Schema errors abort the load first, so cross errors
    only surface when no schema error exists (Req 7.5)."""
    schema_errors: list[str] = []
    cross_errors: list[str] = []
    for i, tech in enumerate(techs):
        prefix = f"technologies[{i}]"
        for key, val in tech["effect_value"].items():
            parsed = _parse_affinity_key(key)
            if parsed is None:
                schema_errors.append(
                    f"{prefix}: effect_value key {key!r} must match "
                    "'terrain_affinity:{terrain_type}:{kind}'"
                )
            elif parsed[1] not in AFFINITY_KINDS:
                schema_errors.append(
                    f"{prefix}: effect_value key {key!r} has invalid kind "
                    f"{parsed[1]!r} (expected one of vision, movement, defense)"
                )
            if not _is_numeric(val):
                schema_errors.append(
                    f"{prefix}: effect_value[{key!r}] must be numeric, got {val!r}"
                )
            if parsed is not None and parsed[0] not in TERRAIN_TYPES:
                cross_errors.append(
                    f"technology '{tech['key']}': effect_value key {key!r} "
                    f"names unknown terrain type '{parsed[0]}'"
                )
    return schema_errors, cross_errors


@st.composite
def invalid_technology_sets(draw) -> list[dict]:
    """1-3 terrain technologies with freely mixed valid/invalid effect_value
    payloads; guaranteed to carry at least one invalid entry overall."""
    count = draw(st.integers(min_value=1, max_value=3))
    techs = [
        {
            "name": f"Terrain Tech {i}",
            "key": f"terrain_tech_{i}",
            "required_rank": draw(st.sampled_from(RANK_NAMES)),
            "resource_cost": {"Stone": 10 * (i + 1)},
            "research_ticks": 5,
            "effect_type": "terrain_affinity",
            "effect_value": draw(mixed_effect_value),
        }
        for i in range(count)
    ]
    schema_errors, cross_errors = _expected_tech_errors(techs)
    if not schema_errors and not cross_errors:
        idx = draw(st.integers(min_value=0, max_value=count - 1))
        key = draw(unknown_terrain_key)
        techs[idx]["effect_value"][key] = draw(numeric_value)
    return techs


class TestProperty14AffinityValidationFailsFast:
    """**Validates: Requirements 6.5, 6.6, 7.5**"""

    @given(classes=invalid_class_sets())
    @settings(max_examples=100, deadline=10000)
    def test_invalid_class_sets_name_every_error(self, classes):
        """load_all raises DataRegistryError identifying every invalid
        affinity entry and every unbalanced positive-only class, collected
        across all class definitions (Req 6.5, 6.6)."""
        expected = _expected_class_errors(classes)
        assert expected, "generator must produce at least one class error"

        tmpdir = _create_data_dir()
        try:
            _write_yaml(
                os.path.join(tmpdir, "definitions", "classes.yaml"),
                {"classes": classes},
            )

            reg = DataRegistry()
            with pytest.raises(DataRegistryError) as excinfo:
                reg.load_all(tmpdir)

            message = str(excinfo.value)
            for err in expected:
                assert err in message, (
                    f"DataRegistryError must identify every invalid entry and "
                    f"unbalanced class; missing {err!r} in:\n{message}"
                )
        finally:
            shutil.rmtree(tmpdir)

    @given(techs=invalid_technology_sets())
    @settings(max_examples=100, deadline=10000)
    def test_invalid_technology_sets_name_every_error(self, techs):
        """load_all raises DataRegistryError identifying every invalid
        terrain-technology entry of the failing phase: all schema errors
        (bad key format, bad kind, non-numeric value) collected across all
        technologies, or — when the payloads are schema-valid — all
        cross-file unknown-terrain errors (Req 7.5)."""
        schema_errors, cross_errors = _expected_tech_errors(techs)
        expected = schema_errors if schema_errors else cross_errors
        assert expected, "generator must produce at least one tech error"

        tmpdir = _create_data_dir()
        try:
            _write_yaml(
                os.path.join(tmpdir, "definitions", "technologies.yaml"),
                techs,
            )

            reg = DataRegistry()
            with pytest.raises(DataRegistryError) as excinfo:
                reg.load_all(tmpdir)

            message = str(excinfo.value)
            for err in expected:
                assert err in message, (
                    f"DataRegistryError must identify every invalid "
                    f"technology entry; missing {err!r} in:\n{message}"
                )
        finally:
            shutil.rmtree(tmpdir)


# ================================================================== #
#  Property 3: Base resolution equals the generator's terrain definition
# ================================================================== #
# Feature: terrain-strategy, Property 3: Base resolution equals the generator's
# terrain definition

#: Terrain-type pool for real-generator resolution cases (weights are equal;
#: which type resolves at (x, y) is driven by the generator's hash noise).
TERRAIN_POOL = ("Plains", "Forest", "Dirt", "Rock", "Mountain")

PLANET = "terra"

# Base modifier values straddle the FakeRegistry balance bounds (5 vision /
# 3.0 movement / 6.0 defense) so the clamped-equality assertion exercises
# both the pass-through and the clamping branches.
base_vision = st.integers(min_value=-12, max_value=12)
base_modifier = st.one_of(
    st.integers(min_value=-12, max_value=12),
    st.floats(min_value=-12.0, max_value=12.0,
              allow_nan=False, allow_infinity=False),
)

coordinate = st.integers(min_value=-100, max_value=1100)
seed_value = st.integers(min_value=0, max_value=2**31 - 1)

# Non-zero adjustments: any leak of affinity data into resolve_base would
# shift a resolved value away from the expected clamped TerrainDef value.
nonzero_adjustment = st.one_of(
    st.integers(min_value=-10, max_value=-1),
    st.integers(min_value=1, max_value=10),
).map(float)


def _make_terrain_def(terrain_type, vision, movement, defense):
    return TerrainDef(
        terrain_type=terrain_type,
        map_symbol=terrain_type[:2].upper(),
        vision_modifier=vision,
        movement_modifier=movement,
        defense_modifier=defense,
    )


def _make_generator(seed, terrain_types, registry):
    """A real TerrainGenerator over *terrain_types* with equal weights."""
    space = CoordinateSpaceDef(
        planet_key=PLANET,
        planet_type="earth",
        width=1000,
        height=1000,
        terrain_seed=seed,
        terrain_weights={t: 1.0 for t in terrain_types},
    )
    return TerrainGenerator(space, data_registry=registry)


def _mirror_clamp(total, bound):
    """Mirror of the resolver's sign-preserving clamp (Req 9.2 semantics)."""
    if abs(total) > bound:
        return copysign(bound, total)
    return total


def _expected_from_def(tdef, balance):
    """The clamped (vision, movement, defense) triple a TerrainDef yields."""
    return (
        int(_mirror_clamp(tdef.vision_modifier, balance.terrain_vision_bound)),
        float(_mirror_clamp(tdef.movement_modifier, balance.terrain_movement_bound)),
        float(_mirror_clamp(tdef.defense_modifier, balance.terrain_defense_bound)),
    )


@st.composite
def base_resolution_cases(draw):
    """(terrain_types, terrain defs, class affinities, seed, x, y) for one
    base-resolution query against a real TerrainGenerator."""
    terrain_types = draw(st.lists(
        st.sampled_from(TERRAIN_POOL),
        min_size=1, max_size=len(TERRAIN_POOL), unique=True,
    ))
    terrain = {
        t: _make_terrain_def(
            t, draw(base_vision), draw(base_modifier), draw(base_modifier),
        )
        for t in terrain_types
    }
    # Affinity data present in the system (Req 2.6): one non-zero class
    # affinity per terrain type guarantees a match for whatever resolves.
    affinities = [
        TerrainAffinity(
            terrain_type=t,
            kind=draw(st.sampled_from(AFFINITY_KINDS)),
            adjustment=draw(nonzero_adjustment),
        )
        for t in terrain_types
    ]
    seed = draw(seed_value)
    x = draw(coordinate)
    y = draw(coordinate)
    return terrain_types, terrain, affinities, seed, x, y


class TestProperty3BaseResolution:
    """**Validates: Requirements 2.1, 2.3, 2.5, 2.6**"""

    @given(case=base_resolution_cases())
    @settings(max_examples=100, deadline=10000)
    def test_base_resolution_equals_clamped_terrain_def(self, case):
        """resolve_base returns exactly the clamped TerrainDef values for
        generator.get_terrain(x, y), untouched by the class affinity data
        present in the registry (Req 2.1, 2.6)."""
        terrain_types, terrain, affinities, seed, x, y = case
        affinity_class = type("Cls", (), {"terrain_affinities": affinities})()
        registry = FakeRegistry(
            terrain=terrain, classes={"ranger": affinity_class},
        )
        generator = _make_generator(seed, terrain_types, registry)
        system = TerrainModifierSystem(registry, {PLANET: generator})

        result = system.resolve_base(PLANET, x, y)

        terrain_type = generator.get_terrain(x, y)
        expected = _expected_from_def(terrain[terrain_type], registry.balance)
        assert result.terrain_type == terrain_type, (
            f"resolve_base terrain_type: expected {terrain_type!r}, "
            f"got {result.terrain_type!r}"
        )
        assert (result.vision, result.movement, result.defense) == expected, (
            f"resolve_base({terrain_type!r} at ({x}, {y})): expected "
            f"{expected!r}, got "
            f"{(result.vision, result.movement, result.defense)!r}"
        )
        assert isinstance(result.vision, int), (
            "vision must be int-coerced after clamping"
        )

    @given(case=base_resolution_cases())
    @settings(max_examples=100, deadline=10000)
    def test_missing_generator_yields_zero_modifiers(self, case):
        """No generator for the requested planet resolves to all-zero
        modifiers, whatever affinity data exists (Req 2.3)."""
        terrain_types, terrain, affinities, seed, x, y = case
        affinity_class = type("Cls", (), {"terrain_affinities": affinities})()
        registry = FakeRegistry(
            terrain=terrain, classes={"ranger": affinity_class},
        )
        generator = _make_generator(seed, terrain_types, registry)
        system = TerrainModifierSystem(registry, {PLANET: generator})

        assert system.resolve_base("luna", x, y) is ZERO_MODIFIERS

    @given(case=base_resolution_cases(), data=st.data())
    @settings(max_examples=100, deadline=10000)
    def test_missing_terrain_def_yields_zero_modifiers(self, case, data):
        """Cross-file drift: a generator terrain type with no TerrainDef in
        the registry resolves to all-zero modifiers, while types that still
        have a TerrainDef resolve to their clamped values (Req 2.5)."""
        terrain_types, terrain, affinities, seed, x, y = case
        dropped = set(data.draw(
            st.lists(
                st.sampled_from(terrain_types),
                min_size=1, max_size=len(terrain_types), unique=True,
            ),
            label="dropped terrain defs",
        ))
        kept = {t: d for t, d in terrain.items() if t not in dropped}
        affinity_class = type("Cls", (), {"terrain_affinities": affinities})()
        registry = FakeRegistry(
            terrain=kept, classes={"ranger": affinity_class},
        )
        generator = _make_generator(seed, terrain_types, registry)
        system = TerrainModifierSystem(registry, {PLANET: generator})

        result = system.resolve_base(PLANET, x, y)

        terrain_type = generator.get_terrain(x, y)
        if terrain_type in dropped:
            assert result is ZERO_MODIFIERS, (
                f"terrain {terrain_type!r} has no TerrainDef; expected "
                f"ZERO_MODIFIERS, got {result!r}"
            )
        else:
            expected = _expected_from_def(kept[terrain_type], registry.balance)
            assert (result.vision, result.movement, result.defense) == expected


# ================================================================== #
#  Property 4: Affinity summation
# ================================================================== #
# Feature: terrain-strategy, Property 4: Affinity summation

# Numeric affinity adjustments (class and tech), straddling the FakeRegistry
# balance bounds so both the pass-through and clamping branches are hit.
affinity_adjustment = st.one_of(
    st.integers(min_value=-12, max_value=12),
    st.floats(min_value=-12.0, max_value=12.0,
              allow_nan=False, allow_infinity=False),
)

# A class affinity over the full terrain pool: entries matching the occupied
# terrain contribute; entries for other terrains must not (Req 6.3).
pool_affinity = st.builds(
    TerrainAffinity,
    terrain_type=st.sampled_from(TERRAIN_POOL),
    kind=st.sampled_from(AFFINITY_KINDS),
    adjustment=affinity_adjustment,
)

# Structured tech-bonus keys over the full terrain pool and valid kinds.
pool_tech_key = st.builds(
    "terrain_affinity:{}:{}".format,
    st.sampled_from(TERRAIN_POOL),
    st.sampled_from(AFFINITY_KINDS),
)


@st.composite
def affinity_summation_cases(draw):
    """(occupied terrain def, class affinities, db.tech_bonuses dict) for one
    player resolution query on a fixed-terrain fake generator."""
    occupied = draw(st.sampled_from(TERRAIN_POOL))
    tdef = _make_terrain_def(
        occupied, draw(base_vision), draw(base_modifier), draw(base_modifier),
    )
    affinities = draw(st.lists(pool_affinity, min_size=0, max_size=6))
    # Force multiple affinities for the same (occupied, kind) pair on some
    # runs so summation of same-pair matches is exercised (Req 6.7).
    if draw(st.booleans()):
        kind = draw(st.sampled_from(AFFINITY_KINDS))
        for _ in range(draw(st.integers(min_value=2, max_value=3))):
            affinities.append(TerrainAffinity(
                terrain_type=occupied,
                kind=kind,
                adjustment=draw(affinity_adjustment),
            ))
    # db.tech_bonuses content: structured keys for matching and non-matching
    # terrains (Req 7.3/7.4 — contributions equal exactly the dict content),
    # plus unrelated non-affinity keys the resolver must ignore.
    bonuses = draw(st.dictionaries(
        pool_tech_key, affinity_adjustment, min_size=0, max_size=6,
    ))
    if draw(st.booleans()):
        bonuses["damage_reduction"] = draw(affinity_adjustment)
    if draw(st.booleans()):
        bonuses[f"terrain_affinity:{occupied}:zz_badkind"] = draw(
            affinity_adjustment
        )
    return occupied, tdef, affinities, bonuses


def _player_expected(occupied, tdef, affinities, bonuses, balance):
    """Mirror of the player resolution pipeline: base + Σ matching class
    affinities (in list order) + the matching tech-bonus read per kind, then
    the sign-preserving clamp with vision int-coerced after clamping."""
    totals = {
        "vision": tdef.vision_modifier,
        "movement": tdef.movement_modifier,
        "defense": tdef.defense_modifier,
    }
    for affinity in affinities:
        if affinity.terrain_type == occupied:
            totals[affinity.kind] += affinity.adjustment
    for kind in AFFINITY_KINDS:
        totals[kind] += bonuses.get(f"terrain_affinity:{occupied}:{kind}", 0)
    return (
        int(_mirror_clamp(totals["vision"], balance.terrain_vision_bound)),
        float(_mirror_clamp(totals["movement"], balance.terrain_movement_bound)),
        float(_mirror_clamp(totals["defense"], balance.terrain_defense_bound)),
    )


def _resolve(occupied, tdef, affinities, bonuses):
    """Build a system around the fakes and resolve for the fake player."""
    affinity_class = type("Cls", (), {"terrain_affinities": affinities})()
    registry = FakeRegistry(
        terrain={occupied: tdef}, classes={"ranger": affinity_class},
    )
    system = TerrainModifierSystem(
        registry, {PLANET: FakeGenerator(occupied)},
    )
    player = FakePlayer(FakeDb(player_class="ranger", tech_bonuses=bonuses))
    return system.resolve_for_player(player, PLANET, 3, 4), registry


class TestProperty4AffinitySummation:
    """**Validates: Requirements 2.2, 6.2, 6.3, 6.7, 7.3, 7.4**"""

    @given(case=affinity_summation_cases())
    @settings(max_examples=100, deadline=10000)
    def test_player_resolution_equals_clamped_summed_totals(self, case):
        """Each player-resolved kind equals clamp(base + Σ matching class
        affinity adjustments + Σ matching tech adjustments), with multiple
        same-pair class matches summed (Req 2.2, 6.2, 6.7) and technology
        contributions exactly the db.tech_bonuses content (Req 7.3, 7.4)."""
        occupied, tdef, affinities, bonuses = case
        result, registry = _resolve(occupied, tdef, affinities, bonuses)

        expected = _player_expected(
            occupied, tdef, affinities, bonuses, registry.balance,
        )
        assert result.terrain_type == occupied
        assert (result.vision, result.movement, result.defense) == expected, (
            f"resolve_for_player on {occupied!r} with affinities "
            f"{affinities!r} and tech_bonuses {bonuses!r}: expected "
            f"{expected!r}, got "
            f"{(result.vision, result.movement, result.defense)!r}"
        )
        assert isinstance(result.vision, int), (
            "vision must be int-coerced after clamping"
        )

    @given(case=affinity_summation_cases())
    @settings(max_examples=100, deadline=10000)
    def test_non_matching_kinds_resolve_to_clamped_base(self, case):
        """Kinds with no class affinity match and no matching tech-bonus key
        resolve to exactly the clamped base modifier (Req 6.3, 7.3)."""
        occupied, tdef, affinities, bonuses = case
        result, registry = _resolve(occupied, tdef, affinities, bonuses)

        matched_kinds = {
            a.kind for a in affinities if a.terrain_type == occupied
        } | {
            kind for kind in AFFINITY_KINDS
            if f"terrain_affinity:{occupied}:{kind}" in bonuses
        }
        base_expected = dict(zip(
            AFFINITY_KINDS, _expected_from_def(tdef, registry.balance),
        ))
        resolved = {
            "vision": result.vision,
            "movement": result.movement,
            "defense": result.defense,
        }
        for kind in AFFINITY_KINDS:
            if kind not in matched_kinds:
                assert resolved[kind] == base_expected[kind], (
                    f"kind {kind!r} has no matching affinity; expected the "
                    f"clamped base {base_expected[kind]!r}, "
                    f"got {resolved[kind]!r}"
                )


# ================================================================== #
#  Property 5: Resolution determinism
# ================================================================== #
# Feature: terrain-strategy, Property 5: Resolution determinism

#: Interleaved noise-query kinds executed between fixed-key resolutions.
NOISE_KINDS = ("other_coord", "other_player", "other_both", "base_other")


def _make_epoch_generator(seed, terrain_types, registry, rotation_ticks, tick):
    """A real TerrainGenerator pinned to an arbitrary but fixed epoch.

    The epoch is part of the fixed resolution key (Req 2.4); it need not be
    zero, so dynamic planets advance once to ``tick // rotation_ticks`` at
    setup and every query in the test then runs at that same epoch.
    """
    space = CoordinateSpaceDef(
        planet_key=PLANET,
        planet_type="earth",
        width=1000,
        height=1000,
        terrain_seed=seed,
        terrain_weights={t: 1.0 for t in terrain_types},
        seed_rotation_ticks=rotation_ticks,
    )
    generator = TerrainGenerator(space, data_registry=registry)
    generator.advance_tick(tick)
    return generator


@st.composite
def determinism_cases(draw):
    """One fixed resolution key (planet, coordinate, epoch, class, completed
    techs) plus the interleaving noise: other coordinates, another player
    with its own class affinities and tech bonuses, and a query plan."""
    terrain_types = draw(st.lists(
        st.sampled_from(TERRAIN_POOL),
        min_size=1, max_size=len(TERRAIN_POOL), unique=True,
    ))
    terrain = {
        t: _make_terrain_def(
            t, draw(base_vision), draw(base_modifier), draw(base_modifier),
        )
        for t in terrain_types
    }
    return {
        "terrain_types": terrain_types,
        "terrain": terrain,
        "fixed_affinities": draw(st.lists(pool_affinity, min_size=0, max_size=4)),
        "other_affinities": draw(st.lists(pool_affinity, min_size=0, max_size=4)),
        "fixed_bonuses": draw(st.dictionaries(
            pool_tech_key, affinity_adjustment, min_size=0, max_size=4,
        )),
        "other_bonuses": draw(st.dictionaries(
            pool_tech_key, affinity_adjustment, min_size=0, max_size=4,
        )),
        "seed": draw(seed_value),
        "rotation_ticks": draw(st.integers(min_value=0, max_value=50)),
        "tick": draw(st.integers(min_value=0, max_value=10_000)),
        "x": draw(coordinate),
        "y": draw(coordinate),
        "other_coords": draw(st.lists(
            st.tuples(coordinate, coordinate), min_size=1, max_size=5,
        )),
        "noise_plan": draw(st.lists(
            st.sampled_from(NOISE_KINDS), min_size=1, max_size=12,
        )),
    }


class TestProperty5ResolutionDeterminism:
    """**Validates: Requirements 2.4**"""

    @given(case=determinism_cases())
    @settings(max_examples=100, deadline=10000)
    def test_repeated_interleaved_queries_return_identical_values(self, case):
        """Repeated queries for a fixed (planet, coordinate, epoch, class,
        completed techs) key return identical modifier values every time,
        even when interleaved with resolutions for other coordinates and
        other players (Req 2.4)."""
        fixed_class = type(
            "Cls", (), {"terrain_affinities": case["fixed_affinities"]},
        )()
        other_class = type(
            "Cls", (), {"terrain_affinities": case["other_affinities"]},
        )()
        registry = FakeRegistry(
            terrain=case["terrain"],
            classes={"ranger": fixed_class, "scout": other_class},
        )
        generator = _make_epoch_generator(
            case["seed"], case["terrain_types"], registry,
            case["rotation_ticks"], case["tick"],
        )
        system = TerrainModifierSystem(registry, {PLANET: generator})

        player = FakePlayer(FakeDb(
            player_class="ranger", tech_bonuses=case["fixed_bonuses"],
        ))
        other = FakePlayer(FakeDb(
            player_class="scout", tech_bonuses=case["other_bonuses"],
        ))
        x, y = case["x"], case["y"]

        ref_player = system.resolve_for_player(player, PLANET, x, y)
        ref_base = system.resolve_base(PLANET, x, y)

        for i, noise in enumerate(case["noise_plan"]):
            ox, oy = case["other_coords"][i % len(case["other_coords"])]
            if noise == "other_coord":
                system.resolve_for_player(player, PLANET, ox, oy)
            elif noise == "other_player":
                system.resolve_for_player(other, PLANET, x, y)
            elif noise == "other_both":
                system.resolve_for_player(other, PLANET, ox, oy)
            else:  # "base_other"
                system.resolve_base(PLANET, ox, oy)

            again_player = system.resolve_for_player(player, PLANET, x, y)
            again_base = system.resolve_base(PLANET, x, y)
            assert again_player == ref_player, (
                f"resolve_for_player at ({x}, {y}) after noise query "
                f"{noise!r} #{i}: expected {ref_player!r}, "
                f"got {again_player!r}"
            )
            assert again_base == ref_base, (
                f"resolve_base at ({x}, {y}) after noise query "
                f"{noise!r} #{i}: expected {ref_base!r}, got {again_base!r}"
            )


# ================================================================== #
#  Property 6: Sign-preserving clamp on every resolver output
# ================================================================== #
# Feature: terrain-strategy, Property 6: Sign-preserving clamp on every resolver output


class GeneratedBalance:
    """Balance fake carrying generated non-negative per-kind bounds
    (Req 9.1 shape), unlike FakeBalance's fixed 5 / 3.0 / 6.0 defaults."""

    def __init__(self, vision_bound, movement_bound, defense_bound):
        self.terrain_vision_bound = vision_bound
        self.terrain_movement_bound = movement_bound
        self.terrain_defense_bound = defense_bound

    def __repr__(self):
        return (
            f"GeneratedBalance(vision={self.terrain_vision_bound!r}, "
            f"movement={self.terrain_movement_bound!r}, "
            f"defense={self.terrain_defense_bound!r})"
        )


# Bounds small enough (0..15) that totals from affinity_summation_cases
# (base -12..12 plus several -12..12 adjustments) regularly land on both
# sides of the bound, exercising pass-through and clamping alike. Zero
# bounds are included: everything then clamps to +/-0.
nonneg_int_bound = st.integers(min_value=0, max_value=15)
nonneg_float_bound = st.one_of(
    st.integers(min_value=0, max_value=15).map(float),
    st.floats(min_value=0.0, max_value=15.0,
              allow_nan=False, allow_infinity=False),
)

# BalanceConfig typing: terrain_vision_bound is an int field, the movement
# and defense bounds are float fields.
generated_balances = st.builds(
    GeneratedBalance,
    nonneg_int_bound,
    nonneg_float_bound,
    nonneg_float_bound,
)


def _raw_player_totals(occupied, tdef, affinities, bonuses):
    """Unclamped per-kind totals of the player resolution pipeline:
    base + Σ matching class affinities + matching tech-bonus reads."""
    totals = {
        "vision": tdef.vision_modifier,
        "movement": tdef.movement_modifier,
        "defense": tdef.defense_modifier,
    }
    for affinity in affinities:
        if affinity.terrain_type == occupied:
            totals[affinity.kind] += affinity.adjustment
    for kind in AFFINITY_KINDS:
        totals[kind] += bonuses.get(f"terrain_affinity:{occupied}:{kind}", 0)
    return totals


def _assert_sign_preserving_clamp(result, totals, balance, context):
    """Assert Req 9.2 / 9.5 on one resolver output: every returned value
    satisfies |value| <= bound(kind); a total within the bound passes
    through unchanged, a total exceeding it becomes the bound magnitude
    with the total's sign — vision truncated toward zero after clamping."""
    resolved = {
        "vision": result.vision,
        "movement": result.movement,
        "defense": result.defense,
    }
    bounds = {
        "vision": balance.terrain_vision_bound,
        "movement": balance.terrain_movement_bound,
        "defense": balance.terrain_defense_bound,
    }
    for kind in AFFINITY_KINDS:
        value, total, bound = resolved[kind], totals[kind], bounds[kind]
        # Req 9.5: no consumer ever observes a value exceeding the bound.
        assert abs(value) <= bound, (
            f"{context}: {kind} value {value!r} exceeds bound {bound!r} "
            f"(unclamped total {total!r}, balance {balance!r})"
        )
        # Req 9.2: within bound -> total unchanged; exceeding -> bound
        # magnitude carrying the total's sign.
        if abs(total) <= bound:
            clamped = total
        else:
            clamped = copysign(bound, total)
        expected = int(clamped) if kind == "vision" else float(clamped)
        assert value == expected, (
            f"{context}: {kind} with total {total!r} and bound {bound!r}: "
            f"expected {expected!r}, got {value!r} (balance {balance!r})"
        )


class TestProperty6SignPreservingClamp:
    """**Validates: Requirements 9.2, 9.5**"""

    @given(case=affinity_summation_cases(), balance=generated_balances)
    @settings(max_examples=100, deadline=10000)
    def test_resolve_base_clamps_every_output(self, case, balance):
        """For any TerrainDef base modifiers and any non-negative bounds,
        every resolve_base value is the sign-preserving clamp of the base
        total: |value| <= bound(kind), within-bound totals unchanged,
        exceeding totals replaced by copysign(bound, total)."""
        occupied, tdef, affinities, bonuses = case
        registry = FakeRegistry(terrain={occupied: tdef})
        registry.balance = balance
        system = TerrainModifierSystem(
            registry, {PLANET: FakeGenerator(occupied)},
        )

        result = system.resolve_base(PLANET, 3, 4)

        totals = {
            "vision": tdef.vision_modifier,
            "movement": tdef.movement_modifier,
            "defense": tdef.defense_modifier,
        }
        _assert_sign_preserving_clamp(
            result, totals, balance, f"resolve_base on {occupied!r}",
        )

    @given(case=affinity_summation_cases(), balance=generated_balances)
    @settings(max_examples=100, deadline=10000)
    def test_resolve_for_player_clamps_every_output(self, case, balance):
        """For any input state (base modifiers, class affinities, tech
        bonuses) and any non-negative bounds, every resolve_for_player
        value is the sign-preserving clamp of the summed total."""
        occupied, tdef, affinities, bonuses = case
        affinity_class = type("Cls", (), {"terrain_affinities": affinities})()
        registry = FakeRegistry(
            terrain={occupied: tdef}, classes={"ranger": affinity_class},
        )
        registry.balance = balance
        system = TerrainModifierSystem(
            registry, {PLANET: FakeGenerator(occupied)},
        )
        player = FakePlayer(FakeDb(player_class="ranger", tech_bonuses=bonuses))

        result = system.resolve_for_player(player, PLANET, 3, 4)

        totals = _raw_player_totals(occupied, tdef, affinities, bonuses)
        _assert_sign_preserving_clamp(
            result, totals, balance, f"resolve_for_player on {occupied!r}",
        )


# ================================================================== #
#  Property 15: Research completion records terrain adjustments
# ================================================================== #
# Feature: terrain-strategy, Property 15: Research completion records terrain adjustments

# Adjustments restricted to integers and exact quarter values so float sums
# are exact and independent of summation order (recompute_tech_bonuses
# iterates the researched set, whose order is arbitrary).
exact_adjustment = st.one_of(
    st.integers(min_value=-100, max_value=100),
    st.integers(min_value=-400, max_value=400).map(lambda n: n / 4),
)

# Structured terrain-affinity effect payloads over the shared terrain pool.
exact_effect_value = st.dictionaries(
    pool_tech_key, exact_adjustment, min_size=1, max_size=4,
)


@st.composite
def terrain_tech_defs(draw) -> list[TechnologyDef]:
    """1-4 in-memory terrain TechnologyDefs with unique keys; on some runs a
    structured key is forced into two technologies so same-key summation
    across technologies is exercised (Req 7.2). ``required_rank`` is empty
    (the rank gate falls open) and ``resource_cost`` empty, keeping the
    research path free of rank and resource fixtures."""
    count = draw(st.integers(min_value=1, max_value=4))
    effects = [dict(draw(exact_effect_value)) for _ in range(count)]
    if count >= 2 and draw(st.booleans()):
        shared = draw(pool_tech_key)
        effects[0][shared] = draw(exact_adjustment)
        effects[1][shared] = draw(exact_adjustment)
    return [
        TechnologyDef(
            name=f"Terrain Tech {i}",
            key=f"terrain_tech_{i}",
            required_rank="",
            resource_cost={},
            research_ticks=draw(st.integers(min_value=1, max_value=3)),
            effect_type="terrain_affinity",
            effect_value=effects[i],
        )
        for i in range(count)
    ]


def _expected_tech_bonuses(techs) -> dict:
    """Mirror of the additive tech-bonus write: for every completed
    technology, each structured key accumulates its float-coerced value,
    so same-key values sum across technologies (Req 7.2)."""
    bonuses = {}
    for tdef in techs:
        for key, value in tdef.effect_value.items():
            bonuses[key] = bonuses.get(key, 0) + float(value)
    return bonuses


def _research_all(techs):
    """Drive the real TechLabSystem through research completion for every
    technology in *techs*; return the (system, player) pair afterwards."""
    registry = FakeRegistry(terrain={})
    registry.technologies = {t.key: t for t in techs}
    system = TechLabSystem(registry, EventBus())
    player = FakePlayer(FakeDb(player_class="ranger", tech_bonuses={}))
    for tdef in techs:
        ok, msg = system.start_research(player, tdef.key)
        assert ok, f"start_research({tdef.key!r}) failed: {msg}"
    for _ in range(max(t.research_ticks for t in techs)):
        system.process_tick()
    return system, player


class TestProperty15ResearchRecordsTerrainAdjustments:
    """**Validates: Requirements 7.2, 7.6**"""

    @given(techs=terrain_tech_defs())
    @settings(max_examples=100, deadline=10000)
    def test_completion_writes_summed_structured_keys(self, techs):
        """Completing research on each terrain technology writes its
        structured terrain_affinity:{terrain}:{kind} keys into
        db.tech_bonuses, with same-key values summed across all completed
        technologies (Req 7.2)."""
        _, player = _research_all(techs)

        assert player.db.researched_techs == {t.key for t in techs}, (
            "every started technology must complete its research"
        )
        expected = _expected_tech_bonuses(techs)
        assert player.db.tech_bonuses == expected, (
            f"db.tech_bonuses after completing "
            f"{[t.key for t in techs]!r}: expected {expected!r}, "
            f"got {player.db.tech_bonuses!r}"
        )

    @given(techs=terrain_tech_defs())
    @settings(max_examples=100, deadline=10000)
    def test_recompute_reproduces_the_same_dict(self, techs):
        """recompute_tech_bonuses rebuilds db.tech_bonuses from the
        researched set into exactly the dict written by research completion,
        and doing so repeatedly never drifts (idempotent rebuild, Req 7.2,
        7.6)."""
        system, player = _research_all(techs)
        after_research = dict(player.db.tech_bonuses)

        system.recompute_tech_bonuses(player)
        assert dict(player.db.tech_bonuses) == after_research, (
            f"recompute_tech_bonuses must reproduce the research-completion "
            f"dict {after_research!r}, got {player.db.tech_bonuses!r}"
        )

        system.recompute_tech_bonuses(player)
        assert dict(player.db.tech_bonuses) == after_research, (
            "a second recompute must not accumulate or drift"
        )

    def test_reconnect_resolves_from_the_same_persisted_dict(self):
        """Example (Req 7.6): resolution reads the same persisted
        db.tech_bonuses dict across sessions — a fresh player object
        carrying the dict written in an earlier session resolves identical
        modifier values, with no re-research."""
        techs = [
            TechnologyDef(
                name="Forest Warfare", key="forest_warfare",
                required_rank="", resource_cost={}, research_ticks=1,
                effect_type="terrain_affinity",
                effect_value={
                    "terrain_affinity:Forest:defense": 2,
                    "terrain_affinity:Forest:movement": 1,
                },
            ),
            TechnologyDef(
                name="Forest Scouting", key="forest_scouting",
                required_rank="", resource_cost={}, research_ticks=1,
                effect_type="terrain_affinity",
                effect_value={
                    "terrain_affinity:Forest:defense": 1,
                    "terrain_affinity:Forest:vision": 2,
                },
            ),
        ]
        _, session_one = _research_all(techs)

        forest = _make_terrain_def("Forest", 0, 0.0, 0.0)
        registry = FakeRegistry(terrain={"Forest": forest})
        resolver = TerrainModifierSystem(
            registry, {PLANET: FakeGenerator("Forest")},
        )

        first = resolver.resolve_for_player(session_one, PLANET, 3, 4)

        # "Reconnect": a new fake player object carrying the same persisted
        # tech_bonuses dict, as a later session would read it back.
        session_two = FakePlayer(FakeDb(
            player_class="ranger",
            tech_bonuses=session_one.db.tech_bonuses,
        ))
        second = resolver.resolve_for_player(session_two, PLANET, 3, 4)

        assert second == first, (
            f"reconnected session resolved {second!r}, "
            f"first session resolved {first!r}"
        )
        # The completed research genuinely applied (defense 2+1 summed).
        assert (first.vision, first.movement, first.defense) == (2, 1.0, 3.0)
