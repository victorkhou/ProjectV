"""
Property-based tests for agent-progression.

Feature: agent-progression, Property 13: Ability-gate schema validation —
``validate_ability_gates`` reports an error iff an entry is invalid (missing
``key``/``required_level``, empty/non-string ``key``, non-int
``required_level``, or out of ``1..MAX_LEVEL``), and reports the duplicate key
for any list with a repeated ``key``.

**Validates: Requirements 7.3, 7.4, 7.5**
"""

import sys
import types

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules so the fast suite runs
#  without a live server.
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.constants import MAX_LEVEL  # noqa: E402
from mygame.world.schema_validator import SchemaValidator  # noqa: E402

validator = SchemaValidator()


# -------------------------------------------------------------- #
#  Strategies
# -------------------------------------------------------------- #

# Keys are non-empty strings drawn from a small ASCII alphabet so duplicate
# collisions are reachable and input generation stays fast (a broad
# st.characters(whitelist_categories=...) strategy scans the whole Unicode
# space and trips Hypothesis's too_slow health check).
gate_key = st.text(
    min_size=1, max_size=8,
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
)
valid_required_level = st.integers(min_value=1, max_value=MAX_LEVEL)


def valid_gate(key=None):
    """A single structurally-valid ability-gate dict."""
    return st.fixed_dictionaries({
        "key": st.just(key) if key is not None else gate_key,
        "required_level": valid_required_level,
    })


@st.composite
def valid_gate_list(draw, min_size=0, max_size=5):
    """A list of valid gates with UNIQUE keys (the only valid shape).

    Uses ``st.lists(unique=True)`` rather than rejection-sampling unique keys
    in a loop, which keeps Hypothesis input generation fast.
    """
    keys = draw(st.lists(gate_key, min_size=min_size, max_size=max_size, unique=True))
    return [draw(valid_gate(key=k)) for k in keys]


# ================================================================== #
#  PROPERTY 13 — valid lists produce no errors
# ================================================================== #

class TestProperty13ValidGates:
    """Feature: agent-progression, Property 13 — valid ⇒ no errors."""

    @given(gates=valid_gate_list())
    @settings(max_examples=200)
    def test_valid_gate_list_produces_no_errors(self, gates):
        """**Validates: Requirements 7.3, 7.4, 7.5**"""
        errors = validator.validate_ability_gates(gates)
        assert errors == [], f"Valid gate list produced errors: {errors}"

    @given(key=gate_key, level=valid_required_level)
    @settings(max_examples=200)
    def test_single_valid_gate_boundaries(self, key, level):
        """Boundary levels 1..MAX_LEVEL are accepted. **Validates: Requirements 7.4**"""
        errors = validator.validate_ability_gates([{"key": key, "required_level": level}])
        assert errors == []


# ================================================================== #
#  PROPERTY 13 — invalid entries always produce errors
# ================================================================== #

class TestProperty13InvalidGates:
    """Feature: agent-progression, Property 13 — invalid ⇒ error reported."""

    @given(gates=valid_gate_list(min_size=1, max_size=4),
           field=st.sampled_from(["key", "required_level"]))
    @settings(max_examples=200)
    def test_missing_required_field_produces_error(self, gates, field):
        """Dropping a required field is reported. **Validates: Requirements 7.3**"""
        del gates[-1][field]
        errors = validator.validate_ability_gates(gates)
        assert len(errors) > 0, f"Missing '{field}' should produce an error"

    @given(gates=valid_gate_list(min_size=1, max_size=4),
           bad_key=st.one_of(
               st.just(""),
               st.integers(),
               st.none(),
               st.booleans(),
               st.floats(allow_nan=False),
           ))
    @settings(max_examples=200)
    def test_empty_or_non_string_key_produces_error(self, gates, bad_key):
        """Empty / non-string keys are reported. **Validates: Requirements 7.4**"""
        gates[-1]["key"] = bad_key
        errors = validator.validate_ability_gates(gates)
        assert len(errors) > 0, f"key={bad_key!r} should produce an error"

    @given(gates=valid_gate_list(min_size=1, max_size=4),
           bad_level=st.one_of(
               st.text(min_size=1, max_size=4),
               st.none(),
               st.booleans(),
               st.floats(allow_nan=False),
           ))
    @settings(max_examples=200)
    def test_non_int_required_level_produces_error(self, gates, bad_level):
        """Non-int required_level (incl. bool) is reported. **Validates: Requirements 7.4**"""
        gates[-1]["required_level"] = bad_level
        errors = validator.validate_ability_gates(gates)
        assert len(errors) > 0, f"required_level={bad_level!r} should produce an error"

    @given(gates=valid_gate_list(min_size=1, max_size=4),
           bad_level=st.one_of(
               st.integers(max_value=0),
               st.integers(min_value=MAX_LEVEL + 1, max_value=MAX_LEVEL + 1000),
           ))
    @settings(max_examples=200)
    def test_out_of_range_required_level_produces_error(self, gates, bad_level):
        """required_level outside 1..MAX_LEVEL is reported. **Validates: Requirements 7.4**"""
        gates[-1]["required_level"] = bad_level
        errors = validator.validate_ability_gates(gates)
        assert len(errors) > 0, f"required_level={bad_level} out of range should produce an error"

    @given(non_dict=st.one_of(st.text(), st.integers(), st.none(), st.lists(st.integers())))
    @settings(max_examples=200)
    def test_non_dict_entry_produces_error(self, non_dict):
        """A non-dict entry is reported. **Validates: Requirements 7.3**"""
        errors = validator.validate_ability_gates([non_dict])
        assert len(errors) > 0, f"Non-dict entry {non_dict!r} should produce an error"

    @given(bad_input=st.one_of(
        st.text(), st.integers(), st.none(),
        st.dictionaries(st.text(), st.text()),
    ))
    @settings(max_examples=200)
    def test_non_list_top_level_produces_error(self, bad_input):
        """A non-list top level is reported. **Validates: Requirements 7.3**"""
        assume(not isinstance(bad_input, list))
        errors = validator.validate_ability_gates(bad_input)
        assert len(errors) > 0, "Non-list top-level input should produce an error"


# ================================================================== #
#  PROPERTY 13 — duplicate keys reported by name
# ================================================================== #

class TestProperty13DuplicateKeys:
    """Feature: agent-progression, Property 13 — duplicate key reported by name."""

    @given(dup_key=gate_key,
           level_a=valid_required_level,
           level_b=valid_required_level,
           middle=st.lists(valid_required_level, min_size=0, max_size=3))
    @settings(max_examples=200)
    def test_duplicate_key_reported_by_name(self, dup_key, level_a, level_b, middle):
        """A repeated key is reported and the duplicate name appears in the error.

        **Validates: Requirements 7.5**
        """
        # Build a list whose first and last entries share ``dup_key``. Middle
        # entries use a distinct key so the only invalidity is the duplicate.
        other_key = dup_key + "_x"
        gates = [{"key": dup_key, "required_level": level_a}]
        gates += [{"key": other_key, "required_level": lv} for lv in middle]
        gates.append({"key": dup_key, "required_level": level_b})
        errors = validator.validate_ability_gates(gates)
        assert len(errors) > 0, "Duplicate key should produce an error"
        assert any(dup_key in e for e in errors), (
            f"Duplicate key '{dup_key}' should be named in errors: {errors}"
        )


# ================================================================== #
#  PROPERTY 2 — Level/rank curve correctness & player
#  backward-compatibility.
#
#  Feature: agent-progression, Property 2: Level/rank curve correctness
#  and player backward-compatibility — ``level_for_xp(xp)`` is the highest
#  level whose ``ranks.yaml``-derived threshold is <= xp (monotonic
#  non-decreasing; ``threshold[level] <= xp < threshold[level+1]`` for
#  non-max levels, clamped 1..MAX_LEVEL) and matches the refactored
#  ``RankSystem.award_xp`` level path exactly.
#
#  **Validates: Requirements 3.1, 3.2, 3.3, 4.1**
# ================================================================== #

import os  # noqa: E402

import yaml  # noqa: E402

from mygame.world import progression  # noqa: E402
from mygame.world.progression import build_thresholds, level_for_xp  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.systems.rank_system import RankSystem  # noqa: E402

_DATA_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "data", "definitions"
)


def _load_registry_with_ranks() -> DataRegistry:
    """Create a DataRegistry populated with the real ranks from ranks.yaml."""
    registry = DataRegistry()
    with open(os.path.join(_DATA_DIR, "ranks.yaml"), "r") as f:
        raw = yaml.safe_load(f)
    registry._populate_ranks(raw)
    return registry


# Load real ranks once and build the module-level threshold table from them.
_REGISTRY = _load_registry_with_ranks()
# THRESHOLDS[1..MAX_LEVEL] is the canonical level->XP table; index 0 unused.
THRESHOLDS = build_thresholds(_REGISTRY.ranks)
# A RankSystem built from the SAME ranks, for the "matches RankSystem" property.
_RANK_SYSTEM = RankSystem(_REGISTRY, EventBus())

# Highest meaningful XP: a bit past the final-level threshold so the MAX_LEVEL
# plateau is exercised.
_MAX_XP = THRESHOLDS[MAX_LEVEL] + 100_000

xp_st = st.integers(min_value=0, max_value=_MAX_XP)


class TestProperty2Monotonic:
    """Feature: agent-progression, Property 2 — monotonic non-decreasing."""

    @given(xp_a=xp_st, xp_b=xp_st)
    @settings(max_examples=200)
    def test_level_for_xp_is_monotonic_non_decreasing(self, xp_a, xp_b):
        """More XP never lowers the level. **Validates: Requirements 3.2, 4.1**"""
        lo, hi = sorted((xp_a, xp_b))
        assert level_for_xp(lo) <= level_for_xp(hi), (
            f"level_for_xp({lo})={level_for_xp(lo)} > "
            f"level_for_xp({hi})={level_for_xp(hi)}"
        )

    @given(xp=xp_st)
    @settings(max_examples=200)
    def test_level_is_clamped_to_valid_range(self, xp):
        """Result always lands in 1..MAX_LEVEL. **Validates: Requirements 3.2**"""
        lvl = level_for_xp(xp)
        assert 1 <= lvl <= MAX_LEVEL


class TestProperty2ThresholdBracketing:
    """Feature: agent-progression, Property 2 — threshold[L] <= xp < threshold[L+1]."""

    @given(data=st.data(), level=st.integers(min_value=1, max_value=MAX_LEVEL))
    @settings(max_examples=200)
    def test_xp_in_bracket_resolves_to_that_level(self, data, level):
        """For non-max levels, xp in [threshold[L], threshold[L+1]) ⇒ level L.

        For the max level, any xp >= threshold[MAX_LEVEL] ⇒ MAX_LEVEL.

        **Validates: Requirements 3.1, 3.2, 4.1**
        """
        lower = THRESHOLDS[level]
        if level < MAX_LEVEL:
            upper = THRESHOLDS[level + 1]
            # Real ranks.yaml thresholds are strictly increasing, so the
            # half-open bracket is non-empty; guard just in case.
            assume(upper > lower)
            xp = data.draw(st.integers(min_value=lower, max_value=upper - 1))
        else:
            xp = data.draw(st.integers(min_value=lower, max_value=_MAX_XP))
        assert level_for_xp(xp) == level, (
            f"xp={xp} in bracket for level {level} resolved to "
            f"{level_for_xp(xp)} (threshold={lower})"
        )

    @given(xp=xp_st)
    @settings(max_examples=200)
    def test_resolved_level_brackets_the_xp(self, xp):
        """The resolved level's own bracket always contains the xp.

        **Validates: Requirements 3.1, 3.2**
        """
        lvl = level_for_xp(xp)
        assert THRESHOLDS[lvl] <= xp
        if lvl < MAX_LEVEL:
            assert xp < THRESHOLDS[lvl + 1]


class TestProperty2BackwardCompatibility:
    """Feature: agent-progression, Property 2 — matches RankSystem level path."""

    @given(xp=xp_st)
    @settings(max_examples=200)
    def test_matches_rank_system_level_for_xp(self, xp):
        """progression.level_for_xp equals RankSystem.level_for_xp for all xp.

        Both derive from the same ranks.yaml table, so the shared helper and
        the (refactored) RankSystem award-xp level path must agree exactly.

        **Validates: Requirements 3.3, 4.1**
        """
        # Re-assert the real-ranks curve on the process-global table that
        # RankSystem delegates to (other test modules may have repointed it).
        _RANK_SYSTEM._rebuild_thresholds()
        assert level_for_xp(xp) == _RANK_SYSTEM.level_for_xp(xp)

    @given(xp=xp_st)
    @settings(max_examples=200)
    def test_matches_rank_system_xp_for_level_roundtrip(self, xp):
        """xp_for_level/level_for_xp agree with RankSystem on the same table.

        **Validates: Requirements 3.3, 4.1**
        """
        # Re-assert the real-ranks curve on the process-global table that
        # RankSystem delegates to (other test modules may have repointed it).
        _RANK_SYSTEM._rebuild_thresholds()
        lvl = level_for_xp(xp)
        assert progression.xp_for_level(lvl) == _RANK_SYSTEM.xp_for_level(lvl)


# ================================================================== #
#  PROPERTY 1 — Progression derivation invariant.
#
#  Feature: agent-progression, Property 1: Progression derivation
#  invariant — after every ``award_xp``/``deduct_xp``, ``db.combat_xp`` is
#  a non-negative int, ``db.level == progression.level_for_xp(db.combat_xp)``
#  in ``1..MAX_LEVEL``, and ``db.rank_level == rank_from_level(db.level)`` in
#  ``1..NUM_RANKS``.
#
#  **Validates: Requirements 1.2, 1.3, 1.4, 1.7, 3.4, 3.5, 3.6, 6.3**
# ================================================================== #

from mygame.world.constants import NUM_RANKS  # noqa: E402
from mygame.world.systems.rank_system import rank_from_level  # noqa: E402
from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402


# -------------------------------------------------------------- #
#  Minimal Evennia-style db harness (mirrors test_combat_entity.py)
# -------------------------------------------------------------- #

class _AttrStore:
    """Minimal Evennia-style attribute store."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class _DbProxy:
    """Minimal proxy mimicking Evennia's db handler."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class _ProgressionHost(CombatEntity):
    """Fake host providing ``self.db`` like an Evennia typeclass.

    Only the progression slice of ``CombatEntity`` is exercised; the host
    initializes the combat-entity state and then we drive ``award_xp`` /
    ``deduct_xp`` directly.
    """

    def __init__(self):
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.at_combat_entity_init()


def _make_host() -> _ProgressionHost:
    return _ProgressionHost()


def _assert_invariant(entity) -> None:
    """Assert Property 1 holds for the current state of *entity*."""
    xp = entity.db.combat_xp
    # combat_xp is a non-negative int (bool is not an acceptable int here).
    assert isinstance(xp, int) and not isinstance(xp, bool), (
        f"combat_xp must be an int, got {type(xp)!r}"
    )
    assert xp >= 0, f"combat_xp must be non-negative, got {xp}"

    # db.level is the derived level, clamped to 1..MAX_LEVEL.
    assert entity.db.level == level_for_xp(xp), (
        f"db.level={entity.db.level} != level_for_xp({xp})={level_for_xp(xp)}"
    )
    assert 1 <= entity.db.level <= MAX_LEVEL, (
        f"db.level={entity.db.level} out of 1..{MAX_LEVEL}"
    )

    # db.rank_level is the rank for the derived level, in 1..NUM_RANKS.
    assert entity.db.rank_level == rank_from_level(entity.db.level), (
        f"db.rank_level={entity.db.rank_level} != "
        f"rank_from_level({entity.db.level})={rank_from_level(entity.db.level)}"
    )
    assert 1 <= entity.db.rank_level <= NUM_RANKS, (
        f"db.rank_level={entity.db.rank_level} out of 1..{NUM_RANKS}"
    )


# Operation strategy: (op, amount). ``amount`` spans non-positive values too
# so the no-op branches of award/deduct are exercised. Simple ``st.integers``
# keeps generation fast (no slow text/character strategies).
_op_amount = st.tuples(
    st.sampled_from(["award", "deduct"]),
    st.integers(min_value=-50, max_value=_MAX_XP // 4),
)
_op_sequence = st.lists(_op_amount, min_size=1, max_size=40)


class TestProperty1DerivationInvariant:
    """Feature: agent-progression, Property 1 — derivation invariant."""

    @given(ops=_op_sequence)
    @settings(max_examples=200)
    def test_invariant_holds_after_each_mutation(self, ops):
        """After every award/deduct the derived state stays consistent.

        **Validates: Requirements 1.2, 1.3, 1.4, 1.7, 3.4, 3.5, 3.6, 6.3**
        """
        entity = _make_host()
        # Initial state must already satisfy the invariant.
        _assert_invariant(entity)
        for op, amount in ops:
            if op == "award":
                entity.award_xp(amount)
            else:
                entity.deduct_xp(amount)
            _assert_invariant(entity)

    @given(amount=st.integers(min_value=1, max_value=_MAX_XP // 4))
    @settings(max_examples=200)
    def test_single_award_satisfies_invariant(self, amount):
        """A single positive award lands on a consistent derived level/rank.

        **Validates: Requirements 1.2, 1.3, 1.4, 3.4**
        """
        entity = _make_host()
        entity.award_xp(amount)
        assert entity.db.combat_xp == amount
        _assert_invariant(entity)

    @given(
        start=st.integers(min_value=0, max_value=_MAX_XP // 4),
        amount=st.integers(min_value=1, max_value=_MAX_XP // 4),
    )
    @settings(max_examples=200)
    def test_deduct_floors_at_zero_and_holds_invariant(self, start, amount):
        """Deduction never drives combat_xp negative and keeps the invariant.

        **Validates: Requirements 1.6, 1.7, 3.4, 6.3**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        entity.deduct_xp(amount)
        assert entity.db.combat_xp == max(0, start - amount)
        _assert_invariant(entity)


# ================================================================== #
#  PROPERTY 3 — Award/deduct arithmetic with zero floor.
#
#  Feature: agent-progression, Property 3: Award/deduct arithmetic with
#  zero floor — ``award_xp(amount)`` adds exactly ``amount`` when ``> 0``
#  else no-op; ``deduct_xp(amount)`` yields ``max(0, start - amount)`` when
#  ``> 0`` else no-op; death loss yields ``max(0, start - agent_xp_death_loss)``.
#
#  **Validates: Requirements 1.5, 1.6, 5.7, 6.1, 6.2**
# ================================================================== #

from mygame.world.definitions import BalanceConfig  # noqa: E402

# The configured death-loss amount (BalanceConfig default = 25). Reading it
# from the dataclass rather than hardcoding keeps the test in lockstep with
# the configured value.
_DEATH_LOSS = BalanceConfig().agent_xp_death_loss

# Amounts span non-positive values so the no-op branches are exercised. Only
# fast st.integers strategies are used (no slow text/character generation).
_amount_st = st.integers(min_value=-50, max_value=_MAX_XP // 4)
_start_st = st.integers(min_value=0, max_value=_MAX_XP // 4)


class TestProperty3Award:
    """Feature: agent-progression, Property 3 — award_xp arithmetic."""

    @given(start=_start_st, amount=_amount_st)
    @settings(max_examples=200)
    def test_award_adds_exactly_amount_or_is_noop(self, start, amount):
        """award_xp adds exactly ``amount`` when > 0, else leaves XP unchanged.

        **Validates: Requirements 1.5, 5.7**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        assert entity.db.combat_xp == start

        returned = entity.award_xp(amount)
        expected = start + amount if amount > 0 else start
        assert entity.db.combat_xp == expected, (
            f"award_xp({amount}) from {start} gave {entity.db.combat_xp}, "
            f"expected {expected}"
        )
        # award_xp returns the resulting combat_xp.
        assert returned == expected
        _assert_invariant(entity)

    @given(start=_start_st, amount=st.integers(min_value=-50, max_value=0))
    @settings(max_examples=200)
    def test_award_non_positive_is_noop(self, start, amount):
        """A non-positive award amount never changes combat_xp.

        **Validates: Requirements 1.5, 5.7**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        before = entity.db.combat_xp
        entity.award_xp(amount)
        assert entity.db.combat_xp == before
        _assert_invariant(entity)


class TestProperty3Deduct:
    """Feature: agent-progression, Property 3 — deduct_xp arithmetic with floor."""

    @given(start=_start_st, amount=_amount_st)
    @settings(max_examples=200)
    def test_deduct_yields_max_zero_or_is_noop(self, start, amount):
        """deduct_xp yields ``max(0, start - amount)`` when > 0, else no-op.

        **Validates: Requirements 1.6, 6.2**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        assert entity.db.combat_xp == start

        returned = entity.deduct_xp(amount)
        expected = max(0, start - amount) if amount > 0 else start
        assert entity.db.combat_xp == expected, (
            f"deduct_xp({amount}) from {start} gave {entity.db.combat_xp}, "
            f"expected {expected}"
        )
        assert returned == expected
        assert entity.db.combat_xp >= 0
        _assert_invariant(entity)

    @given(start=_start_st, amount=st.integers(min_value=-50, max_value=0))
    @settings(max_examples=200)
    def test_deduct_non_positive_is_noop(self, start, amount):
        """A non-positive deduct amount never changes combat_xp.

        **Validates: Requirements 1.6**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        before = entity.db.combat_xp
        entity.deduct_xp(amount)
        assert entity.db.combat_xp == before
        _assert_invariant(entity)

    @given(start=_start_st, over=st.integers(min_value=0, max_value=_MAX_XP // 4))
    @settings(max_examples=200)
    def test_deduct_at_or_beyond_start_floors_at_zero(self, start, over):
        """Deducting at least the full balance floors combat_xp at exactly 0.

        **Validates: Requirements 1.6, 6.2**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        # Deduct an amount >= start (only meaningful when start > 0).
        amount = start + over
        if amount <= 0:
            amount = 1  # ensure a positive deduction is actually applied
        entity.deduct_xp(amount)
        assert entity.db.combat_xp == 0
        _assert_invariant(entity)


class TestProperty3DeathLoss:
    """Feature: agent-progression, Property 3 — death loss with zero floor."""

    @given(start=_start_st)
    @settings(max_examples=200)
    def test_death_loss_yields_max_zero_start_minus_loss(self, start):
        """Deducting the configured death loss yields ``max(0, start - loss)``.

        Mirrors ``AgentSystem.apply_agent_death_loss`` which calls
        ``deduct_xp(agent_xp_death_loss)``.

        **Validates: Requirements 6.1, 6.2**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        assert entity.db.combat_xp == start

        entity.deduct_xp(_DEATH_LOSS)
        assert entity.db.combat_xp == max(0, start - _DEATH_LOSS), (
            f"death loss from {start} gave {entity.db.combat_xp}, "
            f"expected {max(0, start - _DEATH_LOSS)}"
        )
        assert entity.db.combat_xp >= 0
        _assert_invariant(entity)

    @given(start=st.integers(min_value=0, max_value=_DEATH_LOSS))
    @settings(max_examples=200)
    def test_death_loss_below_loss_floors_at_zero(self, start):
        """When start <= death loss, death loss drives combat_xp to exactly 0.

        **Validates: Requirements 6.2**
        """
        entity = _make_host()
        if start > 0:
            entity.award_xp(start)
        entity.deduct_xp(_DEATH_LOSS)
        assert entity.db.combat_xp == 0
        _assert_invariant(entity)


# ================================================================== #
#  PROPERTY 4 — Per-entity independence and owner-agnostic derivation.
#
#  Feature: agent-progression, Property 4: Per-entity independence and
#  owner-agnostic derivation — mutating one entity's ``combat_xp`` leaves
#  another entity's ``combat_xp``/``level``/``rank_level`` unchanged, and
#  ``get_raw_level()`` is identical for a fixed ``combat_xp`` regardless of
#  owner presence/identity.
#
#  **Validates: Requirements 2.1, 2.2, 2.3, 14.9**
# ================================================================== #

# Fast integer-only strategies (no slow text/character generation).
_xp_amount_st = st.integers(min_value=1, max_value=_MAX_XP // 4)
_xp_value_st = st.integers(min_value=0, max_value=_MAX_XP)
# Operation/amount pairs reuse the award/deduct vocabulary from Property 1.
_indep_ops = st.lists(
    st.tuples(
        st.sampled_from(["award", "deduct"]),
        st.integers(min_value=-50, max_value=_MAX_XP // 4),
    ),
    min_size=1,
    max_size=20,
)


class TestProperty4PerEntityIndependence:
    """Feature: agent-progression, Property 4 — per-entity independence."""

    @given(start_a=st.integers(min_value=0, max_value=_MAX_XP // 4),
           start_b=st.integers(min_value=0, max_value=_MAX_XP // 4),
           ops=_indep_ops)
    @settings(max_examples=200)
    def test_mutating_one_entity_leaves_the_other_unchanged(self, start_a, start_b, ops):
        """Awarding/deducting on entity A never touches entity B's state.

        Two independent hosts are created and seeded; every mutation is
        applied to A only, and B's ``combat_xp``/``level``/``rank_level`` are
        asserted unchanged after each step.

        **Validates: Requirements 2.1, 2.2**
        """
        entity_a = _make_host()
        entity_b = _make_host()
        if start_a > 0:
            entity_a.award_xp(start_a)
        if start_b > 0:
            entity_b.award_xp(start_b)

        # Snapshot B's full progression state before any mutation of A.
        b_xp = entity_b.db.combat_xp
        b_level = entity_b.db.level
        b_rank = entity_b.db.rank_level

        for op, amount in ops:
            if op == "award":
                entity_a.award_xp(amount)
            else:
                entity_a.deduct_xp(amount)
            # B is fully untouched by any mutation applied to A.
            assert entity_b.db.combat_xp == b_xp, (
                f"entity B combat_xp changed: {entity_b.db.combat_xp} != {b_xp}"
            )
            assert entity_b.db.level == b_level, (
                f"entity B level changed: {entity_b.db.level} != {b_level}"
            )
            assert entity_b.db.rank_level == b_rank, (
                f"entity B rank_level changed: {entity_b.db.rank_level} != {b_rank}"
            )

    @given(start_a=st.integers(min_value=0, max_value=_MAX_XP // 4),
           start_b=st.integers(min_value=0, max_value=_MAX_XP // 4),
           award_a=_xp_amount_st)
    @settings(max_examples=200)
    def test_entity_state_is_not_shared_across_instances(self, start_a, start_b, award_a):
        """Each instance owns its own combat_xp; no class-level sharing.

        After seeding both entities and then awarding only to A, B retains
        exactly its own seeded value (mutual independence in both directions).

        **Validates: Requirements 2.1, 2.2**
        """
        entity_a = _make_host()
        entity_b = _make_host()
        if start_a > 0:
            entity_a.award_xp(start_a)
        if start_b > 0:
            entity_b.award_xp(start_b)

        entity_a.award_xp(award_a)

        assert entity_a.db.combat_xp == start_a + award_a
        assert entity_b.db.combat_xp == start_b
        # B's derived state still matches its own (unchanged) XP.
        assert entity_b.db.level == level_for_xp(start_b)
        assert entity_b.db.rank_level == rank_from_level(level_for_xp(start_b))


class TestProperty4OwnerAgnosticDerivation:
    """Feature: agent-progression, Property 4 — owner-agnostic derivation."""

    @given(xp=_xp_value_st)
    @settings(max_examples=200)
    def test_get_raw_level_ignores_owner_attribute(self, xp):
        """get_raw_level() depends only on combat_xp, never on ``db.owner``.

        For a fixed ``combat_xp``, the raw level is identical whether ``owner``
        is absent, ``None``, or set to an arbitrary fake object — the mixin
        derives ``Raw_Level`` purely from the entity's own XP (Req 14.9).

        **Validates: Requirements 2.3, 14.9**
        """
        # Baseline: no owner attribute set at all.
        baseline = _make_host()
        if xp > 0:
            baseline.award_xp(xp)
        expected_level = baseline.get_raw_level()

        fake_owner = object()
        for owner_value in (None, fake_owner, "commander-1", 12345):
            entity = _make_host()
            if xp > 0:
                entity.award_xp(xp)
            entity.db.owner = owner_value
            assert entity.get_raw_level() == expected_level, (
                f"get_raw_level() varied with owner={owner_value!r}: "
                f"{entity.get_raw_level()} != {expected_level}"
            )
            # The derivation must match the pure XP curve too.
            assert entity.get_raw_level() == level_for_xp(xp)

    @given(xp=_xp_value_st)
    @settings(max_examples=200)
    def test_raw_rank_and_level_unaffected_by_changing_owner(self, xp):
        """Changing ``db.owner`` on a single entity never alters its derivation.

        **Validates: Requirements 2.3, 14.9**
        """
        entity = _make_host()
        if xp > 0:
            entity.award_xp(xp)

        before_level = entity.get_raw_level()
        before_rank = entity.get_raw_rank()

        for owner_value in (None, object(), "owner-x"):
            entity.db.owner = owner_value
            assert entity.get_raw_level() == before_level
            assert entity.get_raw_rank() == before_rank
            # Stored progression state is likewise owner-agnostic.
            assert entity.db.level == before_level
            assert entity.db.rank_level == before_rank


# ================================================================== #
#  PROPERTY 16 — Rank-event emission on boundary crossings.
#
#  Feature: agent-progression, Property 16: Rank-event emission on
#  boundary crossings — ``RANK_PROMOTED`` fires (old rank, new rank, new
#  agent cap) iff the derived rank increased, ``RANK_DEMOTED`` fires iff it
#  decreased, and no rank event fires when rank is unchanged.
#
#  **Validates: Requirements 4.3, 4.4**
# ================================================================== #

from mygame.world.definitions import RankDef  # noqa: E402
from mygame.world.event_bus import RANK_PROMOTED, RANK_DEMOTED  # noqa: E402


# -- A small, self-contained rank registry + curve (mirrors the approach
#    in world/systems/tests/test_rank_system.py). Five ranks give a max
#    player level of 5 * LEVELS_PER_RANK = 25, with distinct agent caps so
#    the event payload's ``new_agent_cap`` can be asserted. --------------- #

def _make_prop16_ranks():
    """Five ranks with strictly-increasing thresholds and distinct caps."""
    return [
        RankDef(name="Recruit", level=1, xp_threshold=0, agent_cap=2),
        RankDef(name="Private", level=2, xp_threshold=100, agent_cap=3),
        RankDef(name="Corporal", level=3, xp_threshold=300, agent_cap=4),
        RankDef(name="Sergeant", level=4, xp_threshold=600, agent_cap=6),
        RankDef(name="Captain", level=5, xp_threshold=1000, agent_cap=8),
    ]


def _make_prop16_registry() -> DataRegistry:
    registry = DataRegistry()
    registry.ranks = _make_prop16_ranks()
    registry.technologies = {}
    registry.powerups = {}
    return registry


class _RankEventPlayer(CombatEntity):
    """A player stand-in mixing in the real ``CombatEntity`` so it exposes
    the ``award_xp``/``deduct_xp`` progression methods ``RankSystem``
    delegates to. Provides an Evennia-style ``db`` via the harness store."""

    def __init__(self, combat_xp=0, level=1, rank_level=1):
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.key = "Prop16Player"
        self.db.combat_xp = combat_xp
        self.db.level = level
        self.db.rank_level = rank_level
        self.db.researched_techs = set()


def _make_prop16_system():
    """Build a RankSystem on a fresh EventBus, capturing rank events.

    Returns ``(system, bus, promoted, demoted)`` where ``promoted`` and
    ``demoted`` are lists that accumulate the event payload kwargs. The
    threshold table is forced to this registry's curve so the test is
    independent of any table left behind by another module.
    """
    registry = _make_prop16_registry()
    bus = EventBus()
    promoted: list[dict] = []
    demoted: list[dict] = []
    bus.subscribe(RANK_PROMOTED, lambda **kw: promoted.append(kw))
    bus.subscribe(RANK_DEMOTED, lambda **kw: demoted.append(kw))
    system = RankSystem(registry=registry, event_bus=bus)
    system._rebuild_thresholds()
    return system, bus, promoted, demoted


# The five-rank curve maxes out at level 25 (1000 XP for Captain). Keep XP
# amounts comfortably inside the meaningful range; fast st.integers only.
_PROP16_MAX_XP = 2000
_prop16_xp = st.integers(min_value=0, max_value=_PROP16_MAX_XP)
_prop16_amount = st.integers(min_value=1, max_value=_PROP16_MAX_XP)


class TestProperty16RankEventEmission:
    """Feature: agent-progression, Property 16 — rank-event emission on
    boundary crossings."""

    @given(start_xp=_prop16_xp, amount=_prop16_amount)
    @settings(max_examples=200)
    def test_award_fires_promoted_iff_rank_increased(self, start_xp, amount):
        """An award fires ``RANK_PROMOTED`` iff the derived rank rose, and
        never fires ``RANK_DEMOTED``.

        Captures the rank before and after the award and asserts the event
        emission matches the rank delta exactly.

        **Validates: Requirements 4.3**
        """
        system, _bus, promoted, demoted = _make_prop16_system()
        player = _RankEventPlayer(combat_xp=start_xp)
        # Sync stored level/rank to the start XP without firing events for
        # the seed (use the entity's own recompute, not the system).
        player.recompute_progression()

        old_rank = rank_from_level(player.db.level)
        system.award_xp(player, amount, "test")
        new_rank = rank_from_level(player.db.level)

        if new_rank > old_rank:
            assert len(promoted) == 1, (
                f"rank {old_rank}->{new_rank} should fire exactly one "
                f"RANK_PROMOTED, got {len(promoted)}"
            )
            assert len(demoted) == 0
        else:
            # Award never lowers XP, so rank can only rise or stay.
            assert new_rank == old_rank
            assert len(promoted) == 0, (
                f"rank unchanged at {old_rank} but RANK_PROMOTED fired"
            )
        assert len(demoted) == 0, "award must never fire RANK_DEMOTED"

    @given(start_xp=_prop16_xp, amount=_prop16_amount)
    @settings(max_examples=200)
    def test_deduct_fires_demoted_iff_rank_decreased(self, start_xp, amount):
        """A deduction fires ``RANK_DEMOTED`` iff the derived rank fell, and
        never fires ``RANK_PROMOTED``.

        **Validates: Requirements 4.4**
        """
        system, _bus, promoted, demoted = _make_prop16_system()
        player = _RankEventPlayer(combat_xp=start_xp)
        player.recompute_progression()

        old_rank = rank_from_level(player.db.level)
        system.deduct_xp(player, amount)
        new_rank = rank_from_level(player.db.level)

        if new_rank < old_rank:
            assert len(demoted) == 1, (
                f"rank {old_rank}->{new_rank} should fire exactly one "
                f"RANK_DEMOTED, got {len(demoted)}"
            )
            assert len(promoted) == 0
        else:
            # Deduction never raises XP, so rank can only fall or stay.
            assert new_rank == old_rank
            assert len(demoted) == 0, (
                f"rank unchanged at {old_rank} but RANK_DEMOTED fired"
            )
        assert len(promoted) == 0, "deduct must never fire RANK_PROMOTED"

    @given(start_xp=_prop16_xp, amount=_prop16_amount)
    @settings(max_examples=200)
    def test_promoted_payload_has_old_new_rank_and_agent_cap(self, start_xp, amount):
        """When ``RANK_PROMOTED`` fires, the payload carries the old rank, new
        rank, and the new rank's agent cap.

        **Validates: Requirements 4.3**
        """
        system, _bus, promoted, _demoted = _make_prop16_system()
        player = _RankEventPlayer(combat_xp=start_xp)
        player.recompute_progression()

        old_rank = rank_from_level(player.db.level)
        system.award_xp(player, amount, "test")
        new_rank = rank_from_level(player.db.level)
        assume(new_rank > old_rank)  # only inspect payloads when promotion fired

        assert len(promoted) == 1
        payload = promoted[0]
        # Old/new RankDef carry the boundary ranks by level (rank number).
        assert payload["old_rank"] is not None
        assert payload["new_rank"] is not None
        assert payload["old_rank"].level == old_rank
        assert payload["new_rank"].level == new_rank
        # new_agent_cap equals the new rank's configured agent_cap.
        assert payload["new_agent_cap"] == payload["new_rank"].agent_cap

    @given(start_xp=_prop16_xp, amount=_prop16_amount)
    @settings(max_examples=200)
    def test_demoted_payload_has_old_new_rank_and_agent_cap(self, start_xp, amount):
        """When ``RANK_DEMOTED`` fires, the payload carries the old rank, new
        rank, and the new rank's agent cap.

        **Validates: Requirements 4.4**
        """
        system, _bus, _promoted, demoted = _make_prop16_system()
        player = _RankEventPlayer(combat_xp=start_xp)
        player.recompute_progression()

        old_rank = rank_from_level(player.db.level)
        system.deduct_xp(player, amount)
        new_rank = rank_from_level(player.db.level)
        assume(new_rank < old_rank)  # only inspect payloads when demotion fired

        assert len(demoted) == 1
        payload = demoted[0]
        assert payload["old_rank"] is not None
        assert payload["new_rank"] is not None
        assert payload["old_rank"].level == old_rank
        assert payload["new_rank"].level == new_rank
        assert payload["new_agent_cap"] == payload["new_rank"].agent_cap

    @given(start_xp=_prop16_xp,
           op=st.sampled_from(["award", "deduct"]),
           amount=st.integers(min_value=0, max_value=_PROP16_MAX_XP))
    @settings(max_examples=200)
    def test_no_rank_event_when_rank_unchanged(self, start_xp, op, amount):
        """No rank event fires when the operation leaves the rank unchanged
        (including no-op zero amounts and intra-rank level changes).

        **Validates: Requirements 4.3, 4.4**
        """
        system, _bus, promoted, demoted = _make_prop16_system()
        player = _RankEventPlayer(combat_xp=start_xp)
        player.recompute_progression()

        old_rank = rank_from_level(player.db.level)
        if op == "award":
            system.award_xp(player, amount, "test")
        else:
            system.deduct_xp(player, amount)
        new_rank = rank_from_level(player.db.level)

        if new_rank == old_rank:
            assert len(promoted) == 0 and len(demoted) == 0, (
                f"rank unchanged at {old_rank} but a rank event fired: "
                f"promoted={len(promoted)} demoted={len(demoted)}"
            )
