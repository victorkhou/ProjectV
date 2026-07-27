"""
Property-based tests for the EntityAdminRouter bounded ``set`` verb
(unified-admin-crud tasks 1.13/1.14).

# Feature: unified-admin-crud, Property 1: Bounded-set invariant

For generated adapters, FieldSpecs (static, dynamic, and unbounded
bounds), and input values, the applied value always lands within the
field's bounds and ``clamped`` is true iff ``applied != requested``.

**Validates: Requirements 3.2, 3.3, 3.4, 7.6**

Module-level strategies and helpers are shared: task 1.14 appends its
Property 6 (set idempotence at bounds) test class to this same module.
"""

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.commands.command_router import (
    EntityAdminRouter,
    clamp_field_value,
    coerce_field_value,
)
from world.admin.adapter_registry import AdapterRegistry
from world.admin.resolution import Resolution
from world.admin.types import FieldSpec, InstanceRow, ShowReport

# ------------------------------------------------------------------ #
#  Shared strategies and helpers (used by tasks 1.13 and 1.14)
# ------------------------------------------------------------------ #

#: Bounds live in a narrower band than requested values so generated
#: requests routinely land below, inside, and above the bounds.
_INT_BOUND = st.integers(min_value=-1_000, max_value=1_000)
_FLOAT_BOUND = st.floats(min_value=-1_000, max_value=1_000,
                         allow_nan=False, allow_infinity=False)
_INT_VALUE = st.integers(min_value=-5_000, max_value=5_000)
_FLOAT_VALUE = st.floats(min_value=-5_000, max_value=5_000,
                         allow_nan=False, allow_infinity=False)


def _value_strategy(kind):
    return _INT_VALUE if kind == "int" else _FLOAT_VALUE


def _bound_strategy(kind):
    return _INT_BOUND if kind == "int" else _FLOAT_BOUND


class BoundedEntity:
    """A live-entity double carrying its own dynamic-bounds state."""

    def __init__(self, lo, hi, **fields):
        self.lo = lo
        self.hi = hi
        for name, value in fields.items():
            setattr(self, name, value)


def _dynamic_bounds(entity):
    """The dynamic-bounds callable: (lo, hi) from current entity state."""
    return (entity.lo, entity.hi)


@st.composite
def static_spec(draw):
    """A FieldSpec with kind int/float and one of the four static bound
    shapes: min only, max only, both (lo <= hi), or neither (unbounded)."""
    kind = draw(st.sampled_from(["int", "float"]))
    bound = _bound_strategy(kind)
    shape = draw(st.sampled_from(["none", "min", "max", "both"]))
    lo = draw(bound) if shape in ("min", "both") else None
    hi = draw(bound) if shape in ("max", "both") else None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return FieldSpec(name="field", kind=kind, min_value=lo, max_value=hi,
                     perm="Builder")


@st.composite
def static_case(draw):
    """(spec, requested) with static/unbounded bounds."""
    spec = draw(static_spec())
    requested = draw(_value_strategy(spec.kind))
    return spec, requested


@st.composite
def dynamic_case(draw):
    """(spec, entity, requested) where the spec's bounds derive from the
    generated entity's state (lo <= hi ensured). The spec also carries
    deliberately different static bounds to prove dynamic takes
    precedence."""
    kind = draw(st.sampled_from(["int", "float"]))
    bound = _bound_strategy(kind)
    a, b = draw(bound), draw(bound)
    lo, hi = (a, b) if a <= b else (b, a)
    entity = BoundedEntity(lo, hi)
    spec = FieldSpec(name="field", kind=kind,
                     min_value=-999_999, max_value=999_999,
                     perm="Builder", dynamic_bounds=_dynamic_bounds)
    requested = draw(_value_strategy(kind))
    return spec, entity, requested


def assert_bounded_set_invariant(spec, entity, requested):
    """The Property 1 contract, asserted for one (spec, entity, value):

    - ``applied`` lands within [lo, hi] wherever a bound exists (R3.2)
    - in-bounds/unbounded requests pass through unchanged (R3.3)
    - ``clamped == (applied != requested)`` (the SetResult contract)

    Returns (applied, clamped, lo, hi) for further assertions.
    """
    applied, clamped, lo, hi = clamp_field_value(spec, entity, requested)
    if lo is not None:
        assert applied >= lo
    if hi is not None:
        assert applied <= hi
    assert clamped == (applied != requested)
    in_bounds = ((lo is None or requested >= lo)
                 and (hi is None or requested <= hi))
    if in_bounds:
        assert applied == requested
        assert not clamped
    return applied, clamped, lo, hi


# ------------------------------------------------------------------ #
#  Property 1: Bounded-set invariant — the pure clamp helper
# ------------------------------------------------------------------ #

class TestProperty1BoundedSetInvariant:
    """# Feature: unified-admin-crud, Property 1: Bounded-set invariant

    **Validates: Requirements 3.2, 3.3, 3.4, 7.6**
    """

    @settings(max_examples=25)
    @given(case=static_case())
    def test_prop_static_bounds_applied_within_and_clamped_iff(self, case):
        """Requirements 3.2, 3.3: for every static bound shape (min only,
        max only, both, neither) the applied value lands within the
        declared bounds, in-bounds values pass through unchanged, and
        ``clamped`` is true iff ``applied != requested``."""
        spec, requested = case
        _, _, lo, hi = assert_bounded_set_invariant(spec, None, requested)
        # Static bounds reported back are exactly the spec's declaration.
        assert lo == spec.min_value
        assert hi == spec.max_value

    @settings(max_examples=25)
    @given(
        kind=st.sampled_from(["int", "float"]),
        requested=_INT_VALUE,
    )
    def test_prop_unbounded_passes_through_unchanged(self, kind, requested):
        """Requirement 3.3: a fully unbounded FieldSpec never alters the
        requested value and never reports a clamp."""
        spec = FieldSpec(name="field", kind=kind, perm="Builder")
        applied, clamped, lo, hi = clamp_field_value(spec, None, requested)
        assert applied == requested
        assert clamped is False
        assert (lo, hi) == (None, None)

    @settings(max_examples=25)
    @given(case=dynamic_case())
    def test_prop_dynamic_bounds_computed_from_entity_state(self, case):
        """Requirement 3.4: dynamic bounds are computed from the target
        entity's current state (taking precedence over static bounds),
        and the invariant holds within them."""
        spec, entity, requested = case
        _, _, lo, hi = assert_bounded_set_invariant(spec, entity, requested)
        assert (lo, hi) == (entity.lo, entity.hi)

    @settings(max_examples=25)
    @given(
        case=dynamic_case(),
        new_lo=_FLOAT_BOUND,
        new_hi=_FLOAT_BOUND,
    )
    def test_prop_dynamic_bounds_track_entity_mutation(self, case, new_lo,
                                                       new_hi):
        """Requirement 3.4: mutating the entity's state between clamps
        changes the effective bounds — they are re-derived per call, not
        captured once."""
        spec, entity, requested = case
        if new_lo > new_hi:
            new_lo, new_hi = new_hi, new_lo
        entity.lo, entity.hi = new_lo, new_hi
        _, _, lo, hi = assert_bounded_set_invariant(spec, entity, requested)
        assert (lo, hi) == (new_lo, new_hi)


# ------------------------------------------------------------------ #
#  Property 1 end-to-end: the shared ``set`` handler through the router
# ------------------------------------------------------------------ #

_CALLER_IDS = itertools.count(50_000)


class _PropCaller:
    """Minimal Builder-tier caller double."""

    def __init__(self):
        self.id = next(_CALLER_IDS)
        self.key = "PropAdmin"
        self.messages = []

    def msg(self, text, **kwargs):
        self.messages.append(text)

    def check_permstring(self, perm):
        return perm == "Builder"


class _PropAdapter:
    """Toy adapter exercising only the ``set`` path (full verb coverage
    to satisfy AdapterRegistry registration)."""

    entity_key = "prop1"
    def_domain = "prop1s"
    supported_verbs = frozenset({
        "list", "spawn", "show", "set", "destroy",
        "def list", "def show", "def set", "def reset", "def diff",
    })
    opt_outs = {}
    extra_verbs = {}
    aliases = {}

    def __init__(self, fields, toy):
        self.fields = fields
        self.toy = toy

    def list_instances(self, caller, filter_str):
        return [InstanceRow(index=1, key="toy", name="Toy",
                            summary="Toy", ref=self.toy)]

    def resolve_instance(self, caller, token):
        return Resolution(ok=True, target=self.toy)

    def instance_fields(self):
        return dict(self.fields)

    def definition_fields(self):
        return {}

    def create(self, caller, def_token, kwargs):
        raise NotImplementedError

    def read(self, caller, instance):
        return ShowReport(header="Toy", state_lines=[], fields=[])

    def update(self, caller, instance, field, value):
        setattr(instance, field, value)
        return None

    def delete(self, caller, instance):
        raise NotImplementedError

    def def_registry_dict(self):
        return {}

    def def_resolve(self, token):
        return None


class _PropRouter(EntityAdminRouter):
    key = "@prop1"
    adapter_key = "prop1"
    registry = None

    def _adapter_registry(self):
        return self.registry

    def _log_admin(self, verb, detail):
        pass


def run_set_through_router(spec, entity, raw_value):
    """Drive ``set toy field <raw_value>`` end-to-end; returns
    (entity, joined output)."""
    adapter = _PropAdapter({spec.name: spec}, entity)
    registry = AdapterRegistry()
    registry.register(adapter)
    cmd = _PropRouter()
    cmd.registry = registry
    cmd.caller = _PropCaller()
    cmd.args = f" set toy {spec.name} {raw_value}"
    cmd.func()
    return entity, "\n".join(cmd.caller.messages)


class TestProperty1EndToEnd:
    """# Feature: unified-admin-crud, Property 1: Bounded-set invariant
    (end-to-end through the shared ``set`` handler and SetResult contract)

    **Validates: Requirements 3.2, 3.3, 3.4, 7.6**
    """

    @settings(max_examples=25)
    @given(
        a=_INT_BOUND,
        b=_INT_BOUND,
        requested=_INT_VALUE,
    )
    def test_prop_router_set_lands_in_bounds_with_clamp_note_iff(
            self, a, b, requested):
        """The full ``set`` path applies a value within the static bounds
        and the response carries a clamp note exactly when the applied
        value differs from the requested one (R3.2, R3.3, D2)."""
        lo, hi = (a, b) if a <= b else (b, a)
        spec = FieldSpec(name="level", kind="int", min_value=lo,
                         max_value=hi, perm="Builder")
        entity = BoundedEntity(lo, hi, level=lo)
        entity, out = run_set_through_router(spec, entity, requested)

        assert lo <= entity.level <= hi
        if lo <= requested <= hi:
            assert entity.level == requested
            assert "clamped" not in out
        else:
            assert entity.level in (lo, hi)  # nearest bound
            assert "clamped" in out

    @settings(max_examples=25)
    @given(
        a=_FLOAT_BOUND,
        b=_FLOAT_BOUND,
        requested=_FLOAT_VALUE,
    )
    def test_prop_router_set_dynamic_bounds_from_entity(self, a, b,
                                                        requested):
        """The full ``set`` path computes dynamic bounds from the target
        entity's current state before clamping (R3.4)."""
        lo, hi = (a, b) if a <= b else (b, a)
        spec = FieldSpec(name="power", kind="float", perm="Builder",
                         dynamic_bounds=_dynamic_bounds)
        entity = BoundedEntity(lo, hi, power=lo)
        # Float round-trip: repr(float) → coerce_field_value is exact.
        coerced, err = coerce_field_value(spec, repr(requested))
        assert err is None and coerced == requested
        entity, out = run_set_through_router(spec, entity, repr(requested))

        assert lo <= entity.power <= hi
        if lo <= requested <= hi:
            assert entity.power == requested
            assert "clamped" not in out
        else:
            assert entity.power in (lo, hi)
            assert "clamped" in out


# ------------------------------------------------------------------ #
#  Property 6: Set idempotence at bounds
# ------------------------------------------------------------------ #

class TestProperty6SetIdempotenceAtBounds:
    """# Feature: unified-admin-crud, Property 6: Set idempotence at bounds

    For generated fields and values, applying ``set`` twice with the
    same value yields the same final state as once — no cumulative
    drift through clamp/re-stamp paths.

    **Validates: Requirements 3.6, 7.6**
    """

    # -------------------------------------------------------------- #
    #  (a) The pure clamp helper is a fixed point on applied values
    # -------------------------------------------------------------- #

    @settings(max_examples=25)
    @given(case=static_case())
    def test_prop_clamp_fixed_point_static_bounds(self, case):
        """Requirement 3.6: for every static bound shape, re-clamping
        the already-applied value yields the same value with
        ``clamped=False`` — one clamp reaches the fixed point."""
        spec, requested = case
        applied, _, _, _ = clamp_field_value(spec, None, requested)
        reapplied, reclamped, _, _ = clamp_field_value(spec, None, applied)
        assert reapplied == applied
        assert reclamped is False

    @settings(max_examples=25)
    @given(case=dynamic_case())
    def test_prop_clamp_fixed_point_dynamic_bounds(self, case):
        """Requirements 3.6, 7.6: with dynamic bounds derived from the
        entity's current state, re-clamping the applied value is a
        no-op (same value, no clamp reported)."""
        spec, entity, requested = case
        applied, _, _, _ = clamp_field_value(spec, entity, requested)
        reapplied, reclamped, _, _ = clamp_field_value(spec, entity, applied)
        assert reapplied == applied
        assert reclamped is False

    # -------------------------------------------------------------- #
    #  (b) End-to-end: the router ``set`` handler applied twice
    # -------------------------------------------------------------- #

    @settings(max_examples=25)
    @given(
        kind=st.sampled_from(["int", "float"]),
        data=st.data(),
    )
    def test_prop_router_set_twice_static_bounds_same_state(self, kind,
                                                            data):
        """Requirement 3.6: running the same ``set`` command twice with
        the same raw value against static bounds leaves the entity's
        field in exactly the state reached after the first run (int and
        float kinds)."""
        bound = _bound_strategy(kind)
        a, b = data.draw(bound), data.draw(bound)
        lo, hi = (a, b) if a <= b else (b, a)
        requested = data.draw(_value_strategy(kind))
        raw = repr(requested)  # exact round-trip for int and float

        spec = FieldSpec(name="level", kind=kind, min_value=lo,
                         max_value=hi, perm="Builder")
        entity = BoundedEntity(lo, hi, level=lo)

        entity, _ = run_set_through_router(spec, entity, raw)
        state_after_once = entity.level
        entity, _ = run_set_through_router(spec, entity, raw)

        assert entity.level == state_after_once
        assert lo <= entity.level <= hi

    @settings(max_examples=25)
    @given(
        kind=st.sampled_from(["int", "float"]),
        data=st.data(),
    )
    def test_prop_router_set_twice_dynamic_bounds_same_state(self, kind,
                                                             data):
        """Requirements 3.6, 7.6: running the same ``set`` command twice
        with the same raw value against entity-derived dynamic bounds
        leaves the field in the state reached after the first run — the
        clamp/re-derive path introduces no cumulative drift (int and
        float kinds)."""
        bound = _bound_strategy(kind)
        a, b = data.draw(bound), data.draw(bound)
        lo, hi = (a, b) if a <= b else (b, a)
        requested = data.draw(_value_strategy(kind))
        raw = repr(requested)

        spec = FieldSpec(name="power", kind=kind, perm="Builder",
                         dynamic_bounds=_dynamic_bounds)
        entity = BoundedEntity(lo, hi, power=lo)

        entity, _ = run_set_through_router(spec, entity, raw)
        state_after_once = entity.power
        entity, _ = run_set_through_router(spec, entity, raw)

        assert entity.power == state_after_once
        assert lo <= entity.power <= hi
