"""
Property-based tests for resolve_player totality.

**Validates: Requirements 2.6**

# Feature: refactor-foundations, Property 1: Resolver totality — unresolvable input never raises

For any name string (empty, whitespace, unicode), any search scope, and any
message-parameter combination, when the caller's ``search`` cannot resolve the
name (returns None) or the caller has no ``search`` at all, ``resolve_player``
returns None without raising, and sends exactly the messages its contract
promises:

* empty name + ``empty_name_msg`` set -> exactly that message, no search call
* None result + ``not_found_msg`` set -> ``not_found_msg.format(name=name)``
* ``not_found_msg`` None -> no extra message from the helper
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.utils import resolve_player

# ------------------------------------------------------------------ #
#  Test doubles
# ------------------------------------------------------------------ #


class RecordingCallerNoMatch:
    """Caller whose ``search`` always fails to resolve (returns None)."""

    def __init__(self):
        self.msgs = []
        self.search_calls = []

    def msg(self, text):
        self.msgs.append(text)

    def search(self, name, **kwargs):
        self.search_calls.append((name, kwargs))
        return None


class RecordingCallerNoSearch:
    """Caller test double without a ``search`` attribute."""

    def __init__(self):
        self.msgs = []
        self.search_calls = []  # stays empty; present for uniform assertions

    def msg(self, text):
        self.msgs.append(text)


# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

# Arbitrary unicode names, biased toward the interesting classes: empty and
# whitespace-only strings alongside general unicode text.
name_st = st.one_of(
    st.just(""),
    st.text(alphabet=" \t\n\r", min_size=1, max_size=5),
    st.text(min_size=0, max_size=30),
)

# Message templates that are safe to str.format(name=...): either brace-free
# text or brace-free text with a literal "{name}" placeholder spliced in.
_brace_free = st.text(max_size=20).map(
    lambda s: s.replace("{", "").replace("}", "")
)
not_found_msg_st = st.one_of(
    st.none(),
    _brace_free,
    st.tuples(_brace_free, _brace_free).map(lambda p: p[0] + "{name}" + p[1]),
)

empty_name_msg_st = st.one_of(st.none(), st.text(max_size=30))

caller_factory_st = st.sampled_from(
    [RecordingCallerNoMatch, RecordingCallerNoSearch]
)


# ------------------------------------------------------------------ #
#  Property 1: Resolver totality — unresolvable input never raises
# ------------------------------------------------------------------ #


class TestProperty1ResolverTotality:
    """Unresolvable input: None return, no exception, exact message contract."""

    @given(
        name=name_st,
        global_search=st.booleans(),
        not_found_msg=not_found_msg_st,
        empty_name_msg=empty_name_msg_st,
        caller_factory=caller_factory_st,
    )
    @settings(max_examples=200)
    def test_unresolvable_input_never_raises(
        self, name, global_search, not_found_msg, empty_name_msg, caller_factory
    ):
        caller = caller_factory()

        result = resolve_player(
            caller,
            name,
            global_search=global_search,
            not_found_msg=not_found_msg,
            empty_name_msg=empty_name_msg,
        )

        # Totality: the failure indication is the None return, never a raise.
        assert result is None

        if not name and empty_name_msg is not None:
            # Empty-name short-circuit: exactly that message, no search call.
            assert caller.msgs == [empty_name_msg]
            assert caller.search_calls == []
        else:
            # Search runs only when the caller provides it, with the exact
            # previous call shape for each scope.
            if isinstance(caller, RecordingCallerNoMatch):
                expected_kwargs = {"global_search": True} if global_search else {}
                assert caller.search_calls == [(name, expected_kwargs)]
            else:
                assert caller.search_calls == []
            if not_found_msg is not None:
                # None result + not_found_msg set: the formatted message.
                assert caller.msgs == [not_found_msg.format(name=name)]
            else:
                # not_found_msg None: no extra message from the helper.
                assert caller.msgs == []
