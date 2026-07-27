"""
Property-based tests for the Resolution_Engine (unified-admin-crud task 1.5).

# Feature: unified-admin-crud, Property 5: Resolution determinism

For generated tokens, cached lists, and registry states, resolving the
same token against the same state always yields the same result; a token
matching multiple candidates always errors — resolution never guesses.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 10.1, 10.2**
"""

import re
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.admin.resolution import (
    Resolution,
    resolve_index_token,
    resolve_instance_token,
    resolve_player_scope,
)
from mygame.world.admin.types import InstanceRow

# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

#: Tiny alphabet so generated keys/names collide often — collisions are
#: exactly what forces the ambiguity paths (Requirement 2.3). Mixed case
#: exercises the case-sensitive-key vs case-insensitive-name/prefix split.
_IDENT = st.text(alphabet="abAB", min_size=1, max_size=3)


@st.composite
def _rows(draw, min_size=0, max_size=6):
    """A list of InstanceRow with overlapping keys/names and unique refs.

    Refs are plain strings: distinct per row (so a resolved target is
    attributable), comparable (so Resolution equality is meaningful), and
    without a ``pk`` attribute (so the default staleness check treats
    every cached row as live).
    """
    idents = draw(
        st.lists(st.tuples(_IDENT, _IDENT), min_size=min_size,
                 max_size=max_size)
    )
    return [
        InstanceRow(index=i + 1, key=key, name=name,
                    summary=f"{key} ({name})", ref=f"ref-{i}-{key}")
        for i, (key, name) in enumerate(idents)
    ]


@st.composite
def _token_for(draw, rows):
    """A target token biased toward the interesting resolution paths:
    ``#N`` in and out of range, exact keys, names with scrambled case,
    prefixes of keys/names, and garbage."""
    choices = ["index", "garbage"]
    if rows:
        choices += ["key", "name", "prefix"]
    kind = draw(st.sampled_from(choices))

    if kind == "index":
        return f"#{draw(st.integers(min_value=0, max_value=len(rows) + 3))}"
    if kind == "key":
        return draw(st.sampled_from(rows)).key
    if kind == "name":
        name = draw(st.sampled_from(rows)).name
        # Scramble case: the name tier must match case-insensitively.
        return "".join(
            c.upper() if draw(st.booleans()) else c.lower() for c in name
        )
    if kind == "prefix":
        row = draw(st.sampled_from(rows))
        source = row.key if draw(st.booleans()) else row.name
        length = draw(st.integers(min_value=1, max_value=len(source)))
        prefix = source[:length]
        return "".join(
            c.upper() if draw(st.booleans()) else c.lower() for c in prefix
        )
    # Garbage: may accidentally hit a tier or the #N form — both fine,
    # the oracle below routes on the token's actual shape.
    return draw(st.text(alphabet="abABxy#12", min_size=1, max_size=5))


@st.composite
def _state(draw):
    """One full resolution state: candidates, an optional List_Cache
    (None = no ``list`` ran yet), and a token to resolve against them."""
    candidates = draw(_rows())
    cache = draw(st.none() | _rows())
    token = draw(_token_for(candidates + (cache or [])))
    return candidates, cache, token


_INDEX_RE = re.compile(r"^#(\d+)$")


def _oracle_tier_matches(token, candidates):
    """The Requirement 2.2 tier grammar, independently restated: first
    tier yielding any candidate wins — case-sensitive exact key, then
    case-insensitive exact name, then case-insensitive prefix over both."""
    lowered = token.lower()
    tier1 = [r for r in candidates if r.key == token]
    if tier1:
        return tier1
    tier2 = [r for r in candidates if r.name.lower() == lowered]
    if tier2:
        return tier2
    return [
        r for r in candidates
        if r.key.lower().startswith(lowered)
        or r.name.lower().startswith(lowered)
    ]


# ------------------------------------------------------------------ #
#  Property 5: Resolution determinism
# ------------------------------------------------------------------ #

class TestProperty5ResolutionDeterminism:
    """# Feature: unified-admin-crud, Property 5: Resolution determinism

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 10.1, 10.2**
    """

    @settings(max_examples=25)
    @given(state=_state())
    def test_prop_same_inputs_same_result(self, state):
        """Requirement 2.5: identical (token, cache, candidates) inputs
        always produce an identical Resolution — success or failure."""
        candidates, cache, token = state
        first = resolve_instance_token(token, rows=cache,
                                       candidates=candidates)
        second = resolve_instance_token(token, rows=cache,
                                        candidates=candidates)
        assert isinstance(first, Resolution)
        assert first == second

    @settings(max_examples=25)
    @given(state=_state())
    def test_prop_ambiguity_always_errors_never_guesses(self, state):
        """Requirements 2.2, 2.3: a non-index token resolves through the
        tier order, and whenever the first yielding tier holds more than
        one candidate the result is an error listing every candidate —
        a target is never selected."""
        candidates, cache, token = state
        if _INDEX_RE.match(token):
            return  # index tokens are the next test's subject
        result = resolve_instance_token(token, rows=cache,
                                        candidates=candidates)
        expected = _oracle_tier_matches(token, candidates)
        if len(expected) > 1:
            assert not result.ok
            assert result.target is None
            assert result.error
            # Every ambiguous candidate is listed (key and, when it
            # differs, name) in both the message and the candidates field.
            assert len(result.candidates) == len(expected)
            for row in expected:
                assert row.key in result.error
                if row.name != row.key:
                    assert row.name in result.error
        elif len(expected) == 1:
            assert result.ok
            assert result.target == expected[0].ref
        else:
            assert not result.ok
            assert result.target is None

    @settings(max_examples=25)
    @given(
        cache=st.none() | _rows(),
        n=st.integers(min_value=0, max_value=10),
    )
    def test_prop_index_token_semantics(self, cache, n):
        """Requirements 2.1, 10.1, 10.2: ``#N`` resolves 1-based into the
        cached rows exactly when a cache exists and N is in range; no
        cache always errors (run `list` first), out-of-range always
        errors — deterministically."""
        result = resolve_index_token(n, cache)
        again = resolve_index_token(n, cache)
        assert result == again

        if cache is None:
            assert not result.ok
            assert "list" in result.error
        elif 1 <= n <= len(cache):
            assert result.ok
            assert result.target == cache[n - 1].ref
        else:
            assert not result.ok
            assert result.target is None

    @settings(max_examples=25)
    @given(state=_state())
    def test_prop_ok_target_always_from_candidate_set(self, state):
        """A successful resolution always yields a ref drawn from the
        candidate set (non-index tokens) or the cached rows (#N) — never
        a fabricated target."""
        candidates, cache, token = state
        result = resolve_instance_token(token, rows=cache,
                                        candidates=candidates)
        if not result.ok:
            return
        valid_refs = {r.ref for r in candidates}
        valid_refs |= {r.ref for r in (cache or [])}
        assert result.target in valid_refs

    @settings(max_examples=25)
    @given(
        player_keys=st.lists(_IDENT, max_size=5),
        token=st.none() | _IDENT,
    )
    def test_prop_player_scope_determinism(self, player_keys, token):
        """Requirement 2.4 (+2.5): the trailing [player] scope defaults
        to the caller when omitted; a supplied token scopes to exactly
        one matching player or errors — and repeated resolution against
        the same player registry is identical."""

        @dataclass(frozen=True)
        class _Player:
            key: str

        class _Caller:
            id = 42

            def __init__(self, players):
                self._players = players

            def search(self, tok, quiet=True, global_search=True):
                lowered = tok.lower()
                return [p for p in self._players
                        if p.key.lower() == lowered]

        players = [_Player(k) for k in player_keys]
        caller = _Caller(players)

        first = resolve_player_scope(caller, token)
        second = resolve_player_scope(caller, token)
        assert first == second

        if token is None or not token.strip():
            assert first.ok
            assert first.target is caller
        else:
            matches = [p for p in players
                       if p.key.lower() == token.lower()]
            if len(matches) == 1:
                assert first.ok
                assert first.target == matches[0]
            else:
                assert not first.ok
                assert token in first.error
