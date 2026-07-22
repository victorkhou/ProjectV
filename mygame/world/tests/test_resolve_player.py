"""
Unit tests for ``world.utils.resolve_player`` — the single Player_Resolver.

Covers the four input classes (exactly-one-match, no-match, multiple-match via
search-returning-None, empty/missing name) under both converted parameter
profiles, plus the scope-forwarding contract on the recorded ``search`` call:

* router defaults — ``resolve_player(caller, name)``: local scope
  (``caller.search(name)`` with no extra kwargs), additional
  "Could not find player '{name}'." on a None result, no empty-name guard.
* alliance kwargs — ``global_search=True, not_found_msg=None,
  empty_name_msg="Specify a player by name."``: global scope
  (``caller.search(name, global_search=True)``), search's own messaging only,
  falsy names short-circuit with the guard message and no search.

Evennia's ``caller.search`` self-messages and returns None on BOTH a miss and
a multi-match ambiguity, so the multiple-match class reaches the helper as a
scripted None — identical to no-match from the helper's point of view.

Validates: Requirements 2.3, 2.7, 2.8.
"""

import unittest

from world.utils import resolve_player

#: The exact keyword set every converted alliance_commands call site passes.
ALLIANCE_KWARGS = {
    "global_search": True,
    "not_found_msg": None,
    "empty_name_msg": "Specify a player by name.",
}


class _Caller:
    """Fake caller: scripted ``search`` result, recorded ``msg`` and search calls."""

    def __init__(self, search_result=None):
        self.messages = []
        self.search_calls = []
        self._search_result = search_result

    def msg(self, text):
        self.messages.append(text)

    def search(self, *args, **kwargs):
        self.search_calls.append((args, kwargs))
        return self._search_result


class _Target:
    """Stands in for a resolved player character."""


class TestRouterDefaults(unittest.TestCase):
    """resolve_player(caller, name) — the admin-router profile (Requirement 2.3)."""

    def test_exactly_one_match_returns_target_without_messaging(self):
        target = _Target()
        caller = _Caller(search_result=target)
        result = resolve_player(caller, "Bob")
        self.assertIs(result, target)
        self.assertEqual(caller.messages, [])

    def test_no_match_returns_none_and_sends_not_found(self):
        caller = _Caller(search_result=None)
        result = resolve_player(caller, "Bob")
        self.assertIsNone(result)
        self.assertEqual(caller.messages, ["Could not find player 'Bob'."])

    def test_multiple_match_none_from_search_returns_none_and_sends_not_found(self):
        # Evennia's search self-messages on ambiguity and returns None; the
        # router profile then adds its own message (previous double-message
        # behavior preserved).
        caller = _Caller(search_result=None)
        result = resolve_player(caller, "Bo")
        self.assertIsNone(result)
        self.assertEqual(caller.messages, ["Could not find player 'Bo'."])

    def test_empty_name_is_passed_to_search_unchanged(self):
        # No empty-name guard on the router profile: the empty string goes to
        # search (previous behavior), and the None result triggers the message.
        caller = _Caller(search_result=None)
        result = resolve_player(caller, "")
        self.assertIsNone(result)
        self.assertEqual(caller.search_calls, [(("",), {})])
        self.assertEqual(caller.messages, ["Could not find player ''."])


class TestAllianceKwargs(unittest.TestCase):
    """The alliance_commands profile (Requirement 2.3)."""

    def test_exactly_one_match_returns_target_without_messaging(self):
        target = _Target()
        caller = _Caller(search_result=target)
        result = resolve_player(caller, "Bob", **ALLIANCE_KWARGS)
        self.assertIs(result, target)
        self.assertEqual(caller.messages, [])

    def test_no_match_returns_none_with_no_extra_message(self):
        # not_found_msg=None: Evennia's search self-messages; the helper adds
        # nothing (previous alliance behavior preserved).
        caller = _Caller(search_result=None)
        result = resolve_player(caller, "Bob", **ALLIANCE_KWARGS)
        self.assertIsNone(result)
        self.assertEqual(caller.messages, [])

    def test_multiple_match_none_from_search_returns_none_with_no_extra_message(self):
        caller = _Caller(search_result=None)
        result = resolve_player(caller, "Bo", **ALLIANCE_KWARGS)
        self.assertIsNone(result)
        self.assertEqual(caller.messages, [])

    def test_empty_name_short_circuits_with_guard_message_and_no_search(self):
        caller = _Caller(search_result=_Target())
        result = resolve_player(caller, "", **ALLIANCE_KWARGS)
        self.assertIsNone(result)
        self.assertEqual(caller.messages, ["Specify a player by name."])
        self.assertEqual(caller.search_calls, [])

    def test_missing_name_none_short_circuits_like_empty(self):
        caller = _Caller(search_result=_Target())
        result = resolve_player(caller, None, **ALLIANCE_KWARGS)
        self.assertIsNone(result)
        self.assertEqual(caller.messages, ["Specify a player by name."])
        self.assertEqual(caller.search_calls, [])


class TestScopeForwarding(unittest.TestCase):
    """Recorded search-call shapes must match the previous sites (Requirement 2.8)."""

    def test_default_scope_is_local_with_no_extra_kwargs(self):
        caller = _Caller(search_result=_Target())
        resolve_player(caller, "Bob")
        self.assertEqual(caller.search_calls, [(("Bob",), {})])

    def test_global_search_true_forwards_the_global_kwarg(self):
        caller = _Caller(search_result=_Target())
        resolve_player(caller, "Bob", global_search=True)
        self.assertEqual(caller.search_calls, [(("Bob",), {"global_search": True})])

    def test_alliance_profile_uses_the_global_call_shape(self):
        caller = _Caller(search_result=_Target())
        resolve_player(caller, "Bob", **ALLIANCE_KWARGS)
        self.assertEqual(caller.search_calls, [(("Bob",), {"global_search": True})])


if __name__ == "__main__":
    unittest.main()
