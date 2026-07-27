"""
Property-based tests for the definition overlay pipeline
(unified-admin-crud tasks 1.9/1.10).

# Feature: unified-admin-crud, Property 2: Overlay round-trip

For generated sequences of valid ``def set`` operations (modelled at the
component level as ``OverlayStore.set`` + ``DataRegistry`` reload against
a temp data directory), the merged registry reflects the last-set value,
flagged as overridden (present in ``OverlayStore.diff()``/``get()``); a
subsequent ``def reset`` restores exactly the base YAML value and clears
the flag; an empty overlay produces an empty diff.

**Validates: Requirements 5.2, 5.4, 5.5, 5.6**

More overlay properties (Property 3: merged-validation atomicity, task
1.10) are appended to this module as further test classes.
"""

import os
import shutil
import tempfile

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.admin.overlay_store import OverlayStore
from mygame.world.data_registry import DataRegistry

# Reuse the canonical minimal-valid YAML fixtures so the temp data dir
# stays in lockstep with the schemas the real registry enforces.
from mygame.world.tests.test_data_registry import (
    VALID_ABILITY_GATES,
    VALID_BALANCE,
    VALID_BUILDINGS,
    VALID_ITEMS,
    VALID_POWERUPS,
    VALID_RANKS,
    VALID_TECHNOLOGIES,
    VALID_TERRAIN,
)

# ------------------------------------------------------------------ #
#  Temp data dir (built per Hypothesis example — no function-scoped
#  pytest fixture, which Hypothesis would reuse across examples)
# ------------------------------------------------------------------ #


def _write_yaml(path: str, data) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


def _make_data_dir() -> str:
    """A fresh temp data directory holding all valid base YAML files."""
    tmpdir = tempfile.mkdtemp(prefix="prop_overlay_")
    defs = os.path.join(tmpdir, "definitions")
    conf = os.path.join(tmpdir, "config")
    os.makedirs(defs)
    os.makedirs(conf)
    _write_yaml(os.path.join(defs, "buildings.yaml"), VALID_BUILDINGS)
    _write_yaml(os.path.join(defs, "items.yaml"), VALID_ITEMS)
    _write_yaml(os.path.join(defs, "ranks.yaml"), VALID_RANKS)
    _write_yaml(os.path.join(defs, "technologies.yaml"), VALID_TECHNOLOGIES)
    _write_yaml(os.path.join(defs, "powerups.yaml"), VALID_POWERUPS)
    _write_yaml(os.path.join(defs, "terrain.yaml"), VALID_TERRAIN)
    _write_yaml(os.path.join(defs, "ability_gates.yaml"), VALID_ABILITY_GATES)
    _write_yaml(os.path.join(conf, "balance.yaml"), VALID_BALANCE)
    return tmpdir


# ------------------------------------------------------------------ #
#  Overridable fields under test
# ------------------------------------------------------------------ #

#: (domain, definition key, field, merged-value reader). All are real
#: schema-validated fields that stay valid for any positive integer, so
#: every generated ``def set`` is a *valid* operation (Property 2's
#: precondition; invalid payloads belong to Property 3).
_FIELDS = [
    ("buildings", "HQ", "max_health",
     lambda reg: reg.get_building("HQ").max_health),
    ("buildings", "MM", "max_health",
     lambda reg: reg.get_building("MM").max_health),
    ("items", "combat_knife", "max_stack",
     lambda reg: reg.get_item("combat_knife").max_stack),
]

#: Positive integers keep max_health (> 0) and max_stack (>= 1) valid.
_values = st.integers(min_value=1, max_value=999)

#: One operation: ("set", field index, value) or ("reset", field index).
_ops = st.lists(
    st.one_of(
        st.tuples(st.just("set"), st.integers(0, len(_FIELDS) - 1), _values),
        st.tuples(st.just("reset"), st.integers(0, len(_FIELDS) - 1)),
    ),
    min_size=1,
    max_size=6,
)


def _is_flagged(store: OverlayStore, domain: str, key: str, field: str) -> bool:
    """'Flagged as overridden' at the component level: the field appears
    in both ``OverlayStore.diff()`` and ``OverlayStore.get()`` (R5.4)."""
    in_diff = field in (store.diff().get(domain, {}).get(key) or {})
    in_get = field in store.get(domain, key)
    assert in_diff == in_get, (
        f"diff() and get() disagree about {domain}.{key}.{field}"
    )
    return in_diff


# ------------------------------------------------------------------ #
#  Property 2: Overlay round-trip
# ------------------------------------------------------------------ #


class TestOverlayRoundTrip:
    """Property 2 — Overlay round-trip.

    **Validates: Requirements 5.2, 5.4, 5.5, 5.6**
    """

    @given(ops=_ops)
    @settings(max_examples=25, deadline=None)
    def test_overlay_round_trip(self, ops):
        tmpdir = _make_data_dir()
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)
            store = OverlayStore(tmpdir)

            # R5.6: an empty overlay produces an empty diff.
            assert store.diff() == {}

            # Base YAML values, captured before any override exists.
            base = {i: read(reg) for i, (_, _, _, read) in enumerate(_FIELDS)}
            # Model: field index -> last-set value (absent = no override).
            model: dict[int, int] = {}

            for op in ops:
                if op[0] == "set":
                    _, idx, value = op
                    domain, key, field, _read = _FIELDS[idx]
                    store.set(domain, key, field, value)
                    ok, errors = reg.reload_all()
                    assert ok, f"reload after set failed: {errors}"
                    model[idx] = value
                else:
                    _, idx = op
                    domain, key, field, _read = _FIELDS[idx]
                    if idx not in model:
                        # reset-without-override is the R5.9 error path,
                        # covered by the task 1.7 unit tests — skip here.
                        continue
                    store.reset(domain, key, field)
                    ok, errors = reg.reload_all()
                    assert ok, f"reload after reset failed: {errors}"
                    del model[idx]

                # After every operation the merged registry reflects the
                # model exactly: last-set value where overridden (R5.2,
                # last write wins), base YAML value otherwise (R5.5) —
                # and the overridden flag tracks the model (R5.4, R5.5).
                for i, (domain, key, field, read) in enumerate(_FIELDS):
                    expected = model.get(i, base[i])
                    assert read(reg) == expected, (
                        f"{domain}.{key}.{field}: merged value {read(reg)!r} "
                        f"!= expected {expected!r} (overridden: {i in model})"
                    )
                    assert _is_flagged(store, domain, key, field) == (
                        i in model
                    ), (
                        f"{domain}.{key}.{field}: override flag disagrees "
                        f"with model (expected {i in model})"
                    )

            # Round-trip closure: reset every remaining override — the
            # base YAML values are restored exactly (R5.5) and the empty
            # overlay yields an empty diff again (R5.6).
            for i in sorted(model):
                domain, key, field, _read = _FIELDS[i]
                store.reset(domain, key, field)
            ok, errors = reg.reload_all()
            assert ok, f"final reload failed: {errors}"
            assert store.diff() == {}
            for i, (domain, key, field, read) in enumerate(_FIELDS):
                assert read(reg) == base[i], (
                    f"{domain}.{key}.{field}: base value not restored "
                    f"({read(reg)!r} != {base[i]!r})"
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------------------------------------------ #
#  Property 3: Merged-validation atomicity
# ------------------------------------------------------------------ #

#: Invalid override values the SchemaValidator reliably rejects for the
#: fields in _FIELDS: max_health requires an int > 0 (non-int or <= 0 is
#: an error); max_stack goes through _check_positive_int (non-int, bool,
#: or <= 0 is an error). Booleans are deliberately excluded from the
#: invalid pool for max_health (bool IS an int there), so validity is
#: decided uniformly by ``_is_valid_value`` below.
_invalid_values = st.one_of(
    st.integers(min_value=-999, max_value=0),
    st.sampled_from(["banana", "12x", "", "-5"]),
)

#: A payload is a (field index, value) pair; the value may be valid
#: (positive int) or invalid (non-positive int / non-int string).
_payloads = st.tuples(
    st.integers(0, len(_FIELDS) - 1),
    st.one_of(_values, _invalid_values),
)

#: 0–2 preliminary VALID sets to give the pre-command state a non-empty
#: overlay file sometimes, so rollback is exercised against both an
#: absent and a populated overlay.
_prelim_sets = st.lists(
    st.tuples(st.integers(0, len(_FIELDS) - 1), _values),
    min_size=0,
    max_size=2,
)


def _is_valid_value(value) -> bool:
    """True iff the SchemaValidator accepts *value* for every _FIELDS field."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _overlay_bytes(store: OverlayStore) -> bytes | None:
    """The overlay file's raw on-disk state; ``None`` when absent."""
    path = store.overlay_path
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


class TestMergedValidationAtomicity:
    """Property 3 — Merged-validation atomicity.

    For generated override payloads (valid or invalid), the modelled
    ``def set`` flow (``OverlayStore.set`` -> ``reload_all``; on failure
    the router's R6.5 rollback ``restore_snapshot``) either succeeds
    with the merged value live, or fails with BOTH the live registry
    and the overlay file equal to their pre-command state — never a
    partial application.

    **Validates: Requirements 6.4, 6.5**
    """

    @given(prelim=_prelim_sets, payload=_payloads)
    @settings(max_examples=25, deadline=None)
    def test_merged_validation_atomicity(self, prelim, payload):
        idx, value = payload
        tmpdir = _make_data_dir()
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)
            store = OverlayStore(tmpdir)

            # Establish an arbitrary (valid) pre-command overlay state.
            for p_idx, p_value in prelim:
                p_domain, p_key, p_field, _read = _FIELDS[p_idx]
                store.set(p_domain, p_key, p_field, p_value)
                ok, errors = reg.reload_all()
                assert ok, f"prelim reload failed: {errors}"

            # Pre-command snapshot of BOTH planes: live registry values
            # and the overlay file's exact on-disk state.
            pre_live = {
                i: read(reg) for i, (_, _, _, read) in enumerate(_FIELDS)
            }
            pre_overlay = _overlay_bytes(store)

            # --- the modelled `def set` command flow --------------- #
            domain, key, field, read = _FIELDS[idx]
            store.set(domain, key, field, value)  # takes pre-write snapshot
            ok, errors = reg.reload_all()
            if not ok:
                # R6.5: the router rolls the overlay back to the
                # pre-command snapshot when merged validation fails.
                store.restore_snapshot()
            # ------------------------------------------------------- #

            if _is_valid_value(value):
                # R6.4: valid payload -> reload succeeds, atomic swap,
                # the merged value is live.
                assert ok, (
                    f"valid payload {domain}.{key}.{field}={value!r} "
                    f"failed reload: {errors}"
                )
                assert read(reg) == value, (
                    f"{domain}.{key}.{field}: merged value {read(reg)!r} "
                    f"!= set value {value!r} after successful reload"
                )
                # Untargeted fields keep their pre-command values.
                for i, (d, k, f, r) in enumerate(_FIELDS):
                    if i != idx:
                        assert r(reg) == pre_live[i], (
                            f"{d}.{k}.{f}: untargeted field changed "
                            f"({r(reg)!r} != {pre_live[i]!r})"
                        )
            else:
                # R6.5: invalid payload -> reload fails, live registry
                # unchanged AND overlay file restored to its pre-command
                # state. No partial application on either plane.
                assert not ok, (
                    f"invalid payload {domain}.{key}.{field}={value!r} "
                    "unexpectedly passed merged validation"
                )
                assert errors, "failed reload reported no validator errors"
                for i, (d, k, f, r) in enumerate(_FIELDS):
                    assert r(reg) == pre_live[i], (
                        f"{d}.{k}.{f}: live registry changed after failed "
                        f"reload ({r(reg)!r} != {pre_live[i]!r})"
                    )
                assert _overlay_bytes(store) == pre_overlay, (
                    "overlay file does not equal its pre-command state "
                    "after rollback"
                )
                # The restored overlay must still reload cleanly.
                ok2, errors2 = reg.reload_all()
                assert ok2, (
                    f"reload after rollback failed: {errors2}"
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
