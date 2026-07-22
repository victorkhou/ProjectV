"""
Property-based tests for player_in_combat equivalence.

**Validates: Requirements 4.3, 4.6**

# Feature: refactor-foundations, Property 4: player_in_combat is equivalent to
# the reference comparison, failing closed

For any ``db.combat_timer_expires`` value drawn from {unset, None, 0, negative
ints, positive ints} and any current tick, ``player_in_combat``:

- with a readable tick source, returns exactly
  ``expiry_or_zero > 0 and expiry > current_tick`` (Requirement 4.3);
- with a tick source that raises, fails closed: returns exactly
  ``expiry_or_zero > 0`` (Requirement 4.6);
- returns False for a char with no ``db`` handler, in both modes.
"""

from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world import combat_timer
from mygame.world.combat_timer import player_in_combat

# ------------------------------------------------------------------ #
#  Char test doubles
# ------------------------------------------------------------------ #

# Sentinel marking "attribute never set on the namespace" (distinct from None).
UNSET = object()


class NoDbChar:
    """Char shape with no ``db`` attribute at all."""


class DbNoneChar:
    """Char whose ``db`` attribute is None."""

    db = None


class Char:
    """Char whose ``db`` namespace optionally carries combat_timer_expires."""

    def __init__(self, expiry):
        class _Db:
            pass

        self.db = _Db()
        if expiry is not UNSET:
            self.db.combat_timer_expires = expiry


def _expiry_or_zero(expiry):
    """The reference normalization: unset/None -> 0, matching ``expiry or 0``."""
    if expiry is UNSET or expiry is None:
        return 0
    return expiry


# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

expiry_st = st.one_of(
    st.just(UNSET),
    st.none(),
    st.just(0),
    st.integers(max_value=-1),
    st.integers(min_value=1),
)

tick_st = st.integers(min_value=0)


# ------------------------------------------------------------------ #
#  Property 4: player_in_combat is equivalent to the reference
#  comparison, failing closed
# ------------------------------------------------------------------ #


class TestProperty4PlayerInCombatEquivalence:
    """player_in_combat matches the reference comparison in both tick modes."""

    @given(expiry=expiry_st, current_tick=tick_st)
    @settings(max_examples=200)
    def test_returning_mode_matches_reference_comparison(self, expiry, current_tick):
        char = Char(expiry)
        with mock.patch.object(
            combat_timer, "_get_current_tick", return_value=current_tick
        ):
            result = player_in_combat(char)

        expiry_or_zero = _expiry_or_zero(expiry)
        # Reference: in combat iff expiry is positive and strictly in the future.
        assert result == (expiry_or_zero > 0 and expiry_or_zero > current_tick)

    @given(expiry=expiry_st)
    @settings(max_examples=200)
    def test_raising_mode_fails_closed(self, expiry):
        char = Char(expiry)
        with mock.patch.object(
            combat_timer, "_get_current_tick", side_effect=RuntimeError("tick unavailable")
        ):
            result = player_in_combat(char)

        # Fail closed: any positive expiry counts as in-combat when the tick
        # source raises; non-positive/unset/None never do.
        assert result == (_expiry_or_zero(expiry) > 0)

    @given(char=st.sampled_from([NoDbChar, DbNoneChar]).map(lambda cls: cls()),
           current_tick=tick_st)
    @settings(max_examples=100)
    def test_char_without_db_is_false_in_both_modes(self, char, current_tick):
        with mock.patch.object(
            combat_timer, "_get_current_tick", return_value=current_tick
        ):
            assert player_in_combat(char) is False

        with mock.patch.object(
            combat_timer, "_get_current_tick", side_effect=RuntimeError("tick unavailable")
        ):
            assert player_in_combat(char) is False
