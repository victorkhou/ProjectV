"""
Property-based tests for the world.utils system-lookup helpers.

**Validates: Requirements 7.1, 7.2, 7.5**

# Feature: refactor-foundations, Property 6: get_system agrees with
# get_service and ignores ndb.systems

For any installed mapping (including the never-installed state), any probe
name (present in or absent from the mapping), and any caller double — with no
``ndb`` at all, with an ``ndb`` lacking ``systems``, or with ``ndb.systems``
holding SAME-NAMED decoy system objects — ``get_system(caller, name)`` returns
the identical object ``services.get_service(name)`` returns for that name
(``None`` for a name with no installed system), and never returns a decoy.

Facade state is snapshotted and restored around every example via
``services.override``, so no example leaks state into another test.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.utils import get_system, require_system

# world/utils.py resolves lookups through the ``world.services`` module
# instance (its module-level ``from world import services``), which is a
# distinct module object from ``mygame.world.services``. Override the
# instance that get_system actually reads.
from world import services

# ------------------------------------------------------------------ #
#  Shared strategies and doubles (Property 7 will reuse these)
# ------------------------------------------------------------------ #

# System names: lowercase letters and underscores. Short, so present/absent
# probes and same-named decoys collide often enough to be exercised.
name_st = st.text(alphabet="abcdefgh_", min_size=1, max_size=8)

# Each installed value is a distinct sentinel object so ``is`` checks are
# meaningful (identity, not equality).
systems_dict_st = st.dictionaries(keys=name_st, values=st.builds(object), max_size=5)

# Facade state: never-installed (None) or an installed mapping (maybe empty).
installed_state_st = st.one_of(st.none(), systems_dict_st)

# Caller shapes: no ndb attribute at all, an ndb lacking ``systems``, or an
# ndb whose ``systems`` dict holds same-named decoy objects.
CALLER_KINDS = ("no_ndb", "ndb_without_systems", "ndb_with_decoy_systems")


class _NDB:
    """Bare namespace standing in for Evennia's per-object ndb handler."""


class _Caller:
    """Caller double recording ``msg()`` calls; ndb shape is set per example."""

    def __init__(self):
        self.messages = []

    def msg(self, text):
        self.messages.append(text)


def make_caller(kind, decoy_systems):
    """Build a caller double of the given shape (see CALLER_KINDS)."""
    caller = _Caller()
    if kind == "ndb_without_systems":
        caller.ndb = _NDB()
    elif kind == "ndb_with_decoy_systems":
        caller.ndb = _NDB()
        caller.ndb.systems = decoy_systems
    return caller


@st.composite
def lookup_case_st(draw):
    """Yield (installed, probe, caller_kind): probe present or absent."""
    installed = draw(installed_state_st)
    known = sorted(installed) if installed else []
    fresh = draw(name_st.filter(lambda name: name not in known))
    probe = draw(st.sampled_from(known + [fresh]))
    caller_kind = draw(st.sampled_from(CALLER_KINDS))
    return installed, probe, caller_kind


# ------------------------------------------------------------------ #
#  Property 6: get_system agrees with get_service, ignoring ndb.systems
# ------------------------------------------------------------------ #


class TestProperty6GetSystemAgreesWithGetService:
    """get_system returns exactly what get_service returns; decoys never win."""

    @given(case=lookup_case_st())
    @settings(max_examples=200)
    def test_get_system_agrees_with_get_service_and_ignores_ndb(self, case):
        installed, probe, caller_kind = case

        # Decoys cover every installed name AND the probe, so a lookup that
        # consulted ndb.systems would return a decoy even for absent probes.
        decoy_names = set(installed or ()) | {probe}
        decoys = {name: object() for name in decoy_names}
        caller = make_caller(caller_kind, decoys)

        with services.override(installed):
            expected = services.get_service(probe)
            result = get_system(caller, probe)

            # Agreement: the identical object get_service returns.
            assert result is expected

            # Explicit contract: installed object when present, else None.
            if installed is not None and probe in installed:
                assert result is installed[probe]
            else:
                assert result is None

            # Never a same-named decoy from ndb.systems.
            assert result is not decoys[probe]


# ------------------------------------------------------------------ #
#  Property 7: require_system failure message format
# ------------------------------------------------------------------ #

# Feature: refactor-foundations, Property 7: require_system failure
# message format

# require_system names: letters (both cases, to exercise .capitalize())
# and underscores.
require_name_st = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_",
    min_size=1,
    max_size=16,
)

# Optional labels: None (omitted — default derivation applies) or a
# non-empty string (an empty string is falsy and would fall back to the
# default, so it is not a "provided label" under the contract).
label_st = st.one_of(st.none(), st.text(min_size=1, max_size=20))


@st.composite
def missing_system_case_st(draw):
    """Yield (installed, name, label) with NO system installed for name.

    The facade state is never-installed (None), installed-empty ({}), or an
    installed mapping stripped of the probe name — in all three,
    ``get_service(name)`` is None, so ``require_system`` must fail.
    """
    name = draw(require_name_st)
    installed = draw(
        st.one_of(
            st.none(),
            st.just({}),
            systems_dict_st.map(
                lambda d: {k: v for k, v in d.items() if k != name}
            ),
        )
    )
    label = draw(label_st)
    return installed, name, label


class TestProperty7RequireSystemFailureMessage:
    """require_system returns None and sends exactly one formatted message."""

    @given(case=missing_system_case_st())
    @settings(max_examples=200)
    def test_failure_returns_none_and_msgs_exactly_once(self, case):
        """**Validates: Requirements 7.5**"""
        installed, name, label = case
        caller = _Caller()

        # world/utils reads the ``world.services`` module instance, so the
        # override must go through that instance (see module docstring).
        with services.override(installed):
            result = require_system(caller, name, label)

        assert result is None

        if label is not None:
            expected = f"{label} unavailable."
        else:
            expected = f"{name.replace('_', ' ').capitalize()} unavailable."

        # Exactly ONE message, with the exact expected text.
        assert caller.messages == [expected]
