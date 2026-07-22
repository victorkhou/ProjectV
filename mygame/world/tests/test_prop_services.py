"""
Property-based tests for the world.services facade.

**Validates: Requirements 5.2, 5.3, 5.6, 7.7**

# Feature: refactor-foundations, Property 5: Facade install/get round-trip
# with replacement

For any sequence of dicts installed in order and ending with dict ``d``, and
any probe key (drawn from ``d``'s keys, earlier dicts' keys, or a fresh key):
``get_service(key)`` returns the identical object ``d[key]`` when ``key in d``
and None otherwise (keys unique to earlier installs return None, proving each
install replaces the previous mapping), and ``get_systems()`` returns ``d``
itself. Before any install, ``get_service`` returns None.

Facade state is snapshotted and restored around every example via
``services.override``, so no example leaks state into another test.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world import services

# ------------------------------------------------------------------ #
#  Strategies (shared: Property 8 will reuse the dict strategies)
# ------------------------------------------------------------------ #

# System names: short lowercase identifiers, so key collisions across the
# installed dicts in a sequence are common enough to exercise replacement.
key_st = st.text(alphabet="abcdefgh_", min_size=1, max_size=6)

# Each installed value is a distinct sentinel object so ``is`` checks are
# meaningful (identity, not equality).
systems_dict_st = st.dictionaries(keys=key_st, values=st.builds(object), max_size=5)

# A non-empty install sequence; the last dict is the live mapping ``d``.
install_sequence_st = st.lists(systems_dict_st, min_size=1, max_size=4)


@st.composite
def install_case_st(draw):
    """Yield (sequence, probe_key): probe drawn from any dict's keys or fresh."""
    sequence = draw(install_sequence_st)
    known_keys = sorted({key for d in sequence for key in d})
    fresh_key = draw(key_st.filter(lambda key: key not in known_keys))
    probe_key = draw(st.sampled_from(known_keys + [fresh_key]))
    return sequence, probe_key


# ------------------------------------------------------------------ #
#  Property 5: Facade install/get round-trip with replacement
# ------------------------------------------------------------------ #


class TestProperty5InstallGetRoundTrip:
    """install/get round-trip: last install wins; misses and pre-install are None."""

    @given(case=install_case_st())
    @settings(max_examples=200)
    def test_round_trip_with_replacement(self, case):
        sequence, probe_key = case

        # override(None) snapshots the current facade state and simulates the
        # never-installed state inside the body; the finally-restore keeps
        # examples (and other tests) isolated from each other.
        with services.override(None):
            # Pre-install: get_service is None before any install.
            assert services.get_service(probe_key) is None

            for d in sequence:
                services.install(d)
            final = sequence[-1]

            if probe_key in final:
                # Round-trip: the identical stored object comes back.
                assert services.get_service(probe_key) is final[probe_key]
            else:
                # Absent from the final dict -> None, even when an earlier
                # install carried the key (replacement, not merge).
                assert services.get_service(probe_key) is None

            # get_systems returns the installed dict itself (identity).
            assert services.get_systems() is final


# ------------------------------------------------------------------ #
#  Property 8: override restore round-trip
# ------------------------------------------------------------------ #

# Feature: refactor-foundations, Property 8: override restore round-trip
#
# For any prior facade state (never-installed None, or an arbitrary installed
# dict) and any injected dict, entering and exiting ``services.override``
# (whether the body completes normally or raises) leaves the facade state the
# identical object it was before entering (including the never-installed None
# state), while inside the body ``get_service`` reflects the injected dict.
#
# **Validates: Requirements 7.9**

# Prior facade state: never-installed (None) or an arbitrary installed dict.
prior_state_st = st.one_of(st.none(), systems_dict_st)

# A probe key that key_st can never generate (alphabet excludes "z"), so it
# is guaranteed absent from any injected dict.
_ABSENT_KEY = "zzz"


class _Boom(Exception):
    """Sentinel exception raised inside the override body."""


def _assert_body_sees_injected(injected):
    """Inside the body, get_service reflects exactly the injected dict."""
    for key, value in injected.items():
        assert services.get_service(key) is value
    assert services.get_service(_ABSENT_KEY) is None


class TestProperty8OverrideRestoreRoundTrip:
    """override restores the identical prior state on normal and raising exits."""

    @given(prior=prior_state_st, injected=systems_dict_st, raises=st.booleans())
    @settings(max_examples=200)
    def test_override_restore_round_trip(self, prior, injected, raises):
        # The outer override establishes the generated prior state and shields
        # the real facade state from this example (restored in its finally).
        with services.override(prior):
            assert services._systems is prior

            if raises:
                try:
                    with services.override(injected):
                        _assert_body_sees_injected(injected)
                        raise _Boom()
                except _Boom:
                    pass
                else:  # pragma: no cover - guards the test itself
                    raise AssertionError("_Boom was swallowed by override")
            else:
                with services.override(injected):
                    _assert_body_sees_injected(injected)

            # After exit (normal or exceptional): the facade state is the
            # identical object as before entering, including prior=None
            # (the never-installed state).
            assert services._systems is prior
