"""
Unit tests for Resolution_Engine edge cases.

Tests:
- `#0` (below range) and `#N` past the end of the cache → valid-range error
- Stale cache: a row whose live object no longer exists (pk-None convention
  and the injectable is_stale hook) → stale message, re-run `list`
- Empty cache (a `list` ran but matched nothing) → no-valid-indexes error
- No cache at all (no `list` ran yet) → instruction to run `list` first
- Ambiguous prefix → error listing all matching candidates, no guess
- Trailing [player] scoping: default to caller when omitted, unresolvable
  player token error, multiple-player-match error

Requirements: 2.3, 2.7, 2.8, 2.9, 10.1, 10.2
"""

import unittest

from mygame.world.admin.resolution import (
    ListCache,
    resolve_index_token,
    resolve_instance_token,
    resolve_player_scope,
)
from mygame.world.admin.types import InstanceRow


class _LiveRef:
    """A live object handle: has a non-None pk (Evennia convention)."""

    def __init__(self, pk=1):
        self.pk = pk


class _NoPkRef:
    """A handle without a pk attribute (test double / non-DB object)."""


def _row(index, key, name=None, ref=None):
    return InstanceRow(
        index=index,
        key=key,
        name=name if name is not None else key,
        summary=f"{key} summary",
        ref=ref if ref is not None else _LiveRef(pk=index),
    )


class TestIndexRangeErrors(unittest.TestCase):
    """Requirements 2.7, 10.6 — out-of-range #N states the valid range."""

    def setUp(self):
        self.rows = [_row(1, "alpha"), _row(2, "beta"), _row(3, "gamma")]

    def test_index_zero_is_below_range(self):
        result = resolve_index_token(0, self.rows)
        self.assertFalse(result.ok)
        self.assertIsNone(result.target)
        self.assertIn("#0", result.error)
        self.assertIn("#1–#3", result.error)

    def test_index_past_end_of_cache(self):
        result = resolve_index_token(4, self.rows)
        self.assertFalse(result.ok)
        self.assertIsNone(result.target)
        self.assertIn("#4", result.error)
        self.assertIn("#1–#3", result.error)

    def test_out_of_range_via_token_grammar(self):
        # The same errors surface through the #N token path.
        result = resolve_instance_token("#0", rows=self.rows, candidates=[])
        self.assertFalse(result.ok)
        self.assertIn("#1–#3", result.error)

    def test_in_range_index_resolves(self):
        result = resolve_index_token(2, self.rows)
        self.assertTrue(result.ok)
        self.assertIs(result.target, self.rows[1].ref)


class TestNoCacheAndEmptyCache(unittest.TestCase):
    """Requirement 10.1 — #N with no cache instructs to run `list` first;
    an empty cache (a `list` ran, matched nothing) has no valid indexes."""

    def test_no_cache_at_all_instructs_list_first(self):
        result = resolve_index_token(1, None)
        self.assertFalse(result.ok)
        self.assertIn("run `list` first", result.error)

    def test_empty_cache_has_no_valid_indexes(self):
        result = resolve_index_token(1, [])
        self.assertFalse(result.ok)
        self.assertIn("no valid indexes", result.error)
        self.assertIn("re-run `list`", result.error)

    def test_no_cache_via_list_cache_lookup(self):
        # The adapter path: pull rows from the cache (None when no `list`
        # ran yet for this caller/entity) and run the pure grammar over them.
        caller = _LiveRef(pk=42)
        cache = ListCache()
        rows = cache.get(caller, "item")  # never stored → None
        result = resolve_instance_token("#1", rows=rows, candidates=[])
        self.assertFalse(result.ok)
        self.assertIn("run `list` first", result.error)

    def test_empty_cache_via_list_cache_lookup(self):
        caller = _LiveRef(pk=42)
        cache = ListCache()
        cache.store(caller, "item", [])  # `list` ran, matched nothing
        rows = cache.get(caller, "item")
        result = resolve_instance_token("#1", rows=rows, candidates=[])
        self.assertFalse(result.ok)
        self.assertIn("no valid indexes", result.error)


class TestStaleCache(unittest.TestCase):
    """Requirement 10.2 — a cached row whose object no longer exists is
    reported stale with an instruction to re-run `list`."""

    def test_pk_none_ref_is_stale(self):
        # Deleted Evennia objects keep the handle but null out pk.
        deleted = _LiveRef(pk=None)
        rows = [_row(1, "ghost", ref=deleted)]
        result = resolve_index_token(1, rows)
        self.assertFalse(result.ok)
        self.assertIn("stale", result.error)
        self.assertIn("re-run `list`", result.error)
        self.assertIn("ghost", result.error)

    def test_none_ref_is_stale(self):
        # The _row helper substitutes a live ref for None, so build the
        # ref=None row explicitly.
        rows = [InstanceRow(index=1, key="gone", name="gone",
                            summary="gone", ref=None)]
        result = resolve_index_token(1, rows)
        self.assertFalse(result.ok)
        self.assertIn("stale", result.error)

    def test_ref_without_pk_attribute_assumed_live(self):
        ref = _NoPkRef()
        rows = [_row(1, "double", ref=ref)]
        result = resolve_index_token(1, rows)
        self.assertTrue(result.ok)
        self.assertIs(result.target, ref)

    def test_custom_is_stale_hook_overrides_default(self):
        live = _LiveRef(pk=7)
        rows = [_row(1, "flagged", ref=live)]
        result = resolve_index_token(1, rows, is_stale=lambda ref: True)
        self.assertFalse(result.ok)
        self.assertIn("stale", result.error)


class TestAmbiguousPrefix(unittest.TestCase):
    """Requirement 2.3 — multiple candidates at the first matching tier is
    an error listing every candidate; resolution never guesses."""

    def test_ambiguous_prefix_lists_all_candidates(self):
        candidates = [
            _row(1, "sword_iron", "Iron Sword"),
            _row(2, "sword_steel", "Steel Sword"),
            _row(3, "shield_oak", "Oak Shield"),
        ]
        result = resolve_instance_token("sword", rows=None,
                                        candidates=candidates)
        self.assertFalse(result.ok)
        self.assertIsNone(result.target)
        self.assertIn("ambiguous", result.error)
        self.assertIn("sword_iron", result.error)
        self.assertIn("sword_steel", result.error)
        self.assertNotIn("shield_oak", result.error)
        # Programmatic candidate list carries both matches too.
        self.assertEqual(len(result.candidates), 2)

    def test_unambiguous_prefix_resolves(self):
        candidates = [
            _row(1, "sword_iron", "Iron Sword"),
            _row(2, "shield_oak", "Oak Shield"),
        ]
        result = resolve_instance_token("swo", rows=None,
                                        candidates=candidates)
        self.assertTrue(result.ok)
        self.assertIs(result.target, candidates[0].ref)

    def test_no_match_at_any_tier_is_not_found(self):
        # Requirement 2.8 — the not-found error names the token.
        candidates = [_row(1, "sword_iron", "Iron Sword")]
        result = resolve_instance_token("axe", rows=None,
                                        candidates=candidates)
        self.assertFalse(result.ok)
        self.assertIn("'axe'", result.error)
        self.assertIn("No match", result.error)


class _FakeCaller:
    """Caller double with the quiet global search used for player scoping."""

    def __init__(self, matches=None):
        self.pk = 99
        self._matches = matches if matches is not None else []

    def search(self, token, quiet=True, global_search=True):
        return list(self._matches)


class _FakePlayer:
    def __init__(self, key):
        self.key = key


class TestPlayerScoping(unittest.TestCase):
    """Requirements 2.4, 2.9 — trailing [player] defaults to the caller;
    a token not resolving to exactly one player is an error naming it."""

    def test_omitted_player_defaults_to_caller(self):
        caller = _FakeCaller()
        result = resolve_player_scope(caller, None)
        self.assertTrue(result.ok)
        self.assertIs(result.target, caller)

    def test_blank_player_defaults_to_caller(self):
        caller = _FakeCaller()
        result = resolve_player_scope(caller, "   ")
        self.assertTrue(result.ok)
        self.assertIs(result.target, caller)

    def test_single_match_resolves_to_that_player(self):
        bob = _FakePlayer("Bob")
        caller = _FakeCaller(matches=[bob])
        result = resolve_player_scope(caller, "bob")
        self.assertTrue(result.ok)
        self.assertIs(result.target, bob)

    def test_unresolvable_player_token_errors_naming_token(self):
        caller = _FakeCaller(matches=[])
        result = resolve_player_scope(caller, "nobody")
        self.assertFalse(result.ok)
        self.assertIsNone(result.target)
        self.assertIn("'nobody'", result.error)

    def test_multiple_player_matches_error(self):
        caller = _FakeCaller(matches=[_FakePlayer("Bob"), _FakePlayer("Bobby")])
        result = resolve_player_scope(caller, "bob")
        self.assertFalse(result.ok)
        self.assertIsNone(result.target)
        self.assertIn("'bob'", result.error)
        self.assertIn("more than one", result.error)
        self.assertIn("Bob", result.error)
        self.assertIn("Bobby", result.error)


if __name__ == "__main__":
    unittest.main()
