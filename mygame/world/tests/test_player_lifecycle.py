"""
Unit tests for the player lifecycle state machine (world/player_lifecycle.py).

Covers the single-writer ``transition`` guard, the login router's resume rules,
death recording, and the linkdead grace helpers — the foundation the login /
disconnect / death hooks and the tick loop build on.
"""

import types
import unittest

from world.constants import (
    PLAYER_STATE_LINKDEAD,
    PLAYER_STATE_LOBBY,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_SPAWNING,
)
from world.event_bus import EventBus, PLAYER_STATE_CHANGED
from world import player_lifecycle as pl


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Player:
    def __init__(self, state=None, player_class=None):
        self.key = "Player"
        self.db = types.SimpleNamespace(
            player_state=state, player_class=player_class,
            death_x=None, death_y=None, death_planet=None,
            linkdead_until=0.0,
        )


class _Sink:
    def __init__(self):
        self.events = []  # (old, new, reason)

    def __call__(self, player=None, old_state=None, new_state=None, reason=None, **_):
        self.events.append((old_state, new_state, reason))


def _bus_with_sink():
    bus = EventBus()
    sink = _Sink()
    bus.subscribe(PLAYER_STATE_CHANGED, sink)
    return bus, sink


# -------------------------------------------------------------- #
#  transition — the single writer + guard
# -------------------------------------------------------------- #

class TestTransition(unittest.TestCase):
    def test_none_may_enter_any_state(self):
        bus, sink = _bus_with_sink()
        p = _Player(state=None)
        self.assertTrue(pl.transition(p, PLAYER_STATE_SPAWNING, event_bus=bus))
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertEqual(sink.events, [(None, PLAYER_STATE_SPAWNING, "")])

    def test_legal_edge_applies(self):
        bus, sink = _bus_with_sink()
        p = _Player(state=PLAYER_STATE_LOBBY)
        self.assertTrue(pl.transition(p, PLAYER_STATE_PLAYING, reason="enter",
                                      event_bus=bus))
        self.assertEqual(p.db.player_state, PLAYER_STATE_PLAYING)
        self.assertIn((PLAYER_STATE_LOBBY, PLAYER_STATE_PLAYING, "enter"),
                      sink.events)

    def test_illegal_edge_rejected(self):
        bus, sink = _bus_with_sink()
        # spawning -> playing is NOT a declared edge (must go via lobby).
        p = _Player(state=PLAYER_STATE_SPAWNING)
        self.assertFalse(pl.transition(p, PLAYER_STATE_PLAYING, event_bus=bus))
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)  # unchanged
        self.assertEqual(sink.events, [])  # nothing published

    def test_noop_self_transition_is_ok_and_silent(self):
        bus, sink = _bus_with_sink()
        p = _Player(state=PLAYER_STATE_PLAYING)
        self.assertTrue(pl.transition(p, PLAYER_STATE_PLAYING, event_bus=bus))
        self.assertEqual(sink.events, [])  # idempotent, no event

    def test_unknown_state_rejected(self):
        p = _Player(state=PLAYER_STATE_LOBBY)
        self.assertFalse(pl.transition(p, "bogus"))
        self.assertEqual(p.db.player_state, PLAYER_STATE_LOBBY)

    def test_death_edge_playing_to_spawning(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        self.assertTrue(pl.transition(p, PLAYER_STATE_SPAWNING, reason="death"))
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)


# -------------------------------------------------------------- #
#  route_on_login — the state-2 router
# -------------------------------------------------------------- #

class TestRouteOnLogin(unittest.TestCase):
    def test_new_character_routes_to_spawning(self):
        p = _Player(state=None)
        self.assertEqual(pl.route_on_login(p), PLAYER_STATE_SPAWNING)
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)

    def test_mid_spawn_resumes_spawning(self):
        p = _Player(state=PLAYER_STATE_SPAWNING)
        self.assertEqual(pl.route_on_login(p), PLAYER_STATE_SPAWNING)

    def test_existing_non_dead_resumes_lobby(self):
        p = _Player(state=PLAYER_STATE_LOBBY)
        self.assertEqual(pl.route_on_login(p), PLAYER_STATE_LOBBY)

    def test_playing_resumes_playing(self):
        # A crash left the character PLAYING; login resumes in place.
        p = _Player(state=PLAYER_STATE_PLAYING)
        self.assertEqual(pl.route_on_login(p), PLAYER_STATE_PLAYING)

    def test_linkdead_reconnects_to_playing_and_clears_timer(self):
        p = _Player(state=PLAYER_STATE_LINKDEAD)
        p.db.linkdead_until = 12345.0
        self.assertEqual(pl.route_on_login(p), PLAYER_STATE_PLAYING)
        self.assertEqual(p.db.player_state, PLAYER_STATE_PLAYING)
        self.assertEqual(p.db.linkdead_until, 0.0)  # grace cleared on reconnect


# -------------------------------------------------------------- #
#  Death
# -------------------------------------------------------------- #

class TestRecordDeath(unittest.TestCase):
    def test_records_tile_and_routes_to_spawning(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        pl.record_death(p, 12, 8, "terra")
        self.assertEqual((p.db.death_x, p.db.death_y, p.db.death_planet),
                         (12, 8, "terra"))
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)

    def test_death_from_linkdead_routes_to_spawning(self):
        # Killed during the linkdead grace window → spawning on reconnect.
        p = _Player(state=PLAYER_STATE_LINKDEAD)
        pl.record_death(p, 1, 2, "terra")
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)

    def test_bad_coords_stored_as_none(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        pl.record_death(p, None, None, "forge")
        self.assertIsNone(p.db.death_x)
        self.assertEqual(p.db.death_planet, "forge")


# -------------------------------------------------------------- #
#  Linkdead grace
# -------------------------------------------------------------- #

class TestLinkdead(unittest.TestCase):
    def test_begin_linkdead_sets_deadline_and_state(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        self.assertTrue(pl.begin_linkdead(p, now=100.0, grace_seconds=30.0))
        self.assertEqual(p.db.player_state, PLAYER_STATE_LINKDEAD)
        self.assertEqual(p.db.linkdead_until, 130.0)

    def test_not_expired_before_deadline(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        pl.begin_linkdead(p, now=100.0, grace_seconds=30.0)
        self.assertFalse(pl.is_linkdead_expired(p, now=129.9))

    def test_expired_at_or_after_deadline(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        pl.begin_linkdead(p, now=100.0, grace_seconds=30.0)
        self.assertTrue(pl.is_linkdead_expired(p, now=130.0))

    def test_not_expired_when_not_linkdead(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        self.assertFalse(pl.is_linkdead_expired(p, now=1e9))

    def test_expire_routes_to_lobby_and_clears_timer(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        pl.begin_linkdead(p, now=100.0, grace_seconds=30.0)
        self.assertTrue(pl.expire_linkdead(p))
        self.assertEqual(p.db.player_state, PLAYER_STATE_LOBBY)
        self.assertEqual(p.db.linkdead_until, 0.0)

    def test_corrupt_deadline_treated_as_expired(self):
        p = _Player(state=PLAYER_STATE_LINKDEAD)
        p.db.linkdead_until = "not-a-number"
        self.assertTrue(pl.is_linkdead_expired(p, now=1.0))


# -------------------------------------------------------------- #
#  Lobby / enter / spawning gate
# -------------------------------------------------------------- #

class TestLobbyAndEnter(unittest.TestCase):
    def test_enter_game_lobby_to_playing(self):
        p = _Player(state=PLAYER_STATE_LOBBY)
        self.assertTrue(pl.enter_game(p))
        self.assertEqual(p.db.player_state, PLAYER_STATE_PLAYING)

    def test_quit_playing_to_lobby(self):
        p = _Player(state=PLAYER_STATE_PLAYING)
        self.assertTrue(pl.to_lobby(p))
        self.assertEqual(p.db.player_state, PLAYER_STATE_LOBBY)

    def test_finish_spawning_requires_class(self):
        # No class chosen yet -> gate refuses, stays spawning.
        p = _Player(state=PLAYER_STATE_SPAWNING, player_class=None)
        self.assertFalse(pl.finish_spawning(p))
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)

    def test_finish_spawning_advances_once_class_chosen(self):
        p = _Player(state=PLAYER_STATE_SPAWNING, player_class="Vanguard")
        self.assertTrue(pl.finish_spawning(p))
        self.assertEqual(p.db.player_state, PLAYER_STATE_LOBBY)


# -------------------------------------------------------------- #
#  Reads
# -------------------------------------------------------------- #

class TestReads(unittest.TestCase):
    def test_get_state(self):
        self.assertEqual(pl.get_state(_Player(state=PLAYER_STATE_PLAYING)),
                         PLAYER_STATE_PLAYING)
        self.assertIsNone(pl.get_state(_Player(state=None)))

    def test_state_label(self):
        self.assertEqual(pl.state_label(PLAYER_STATE_PLAYING), "Playing")
        self.assertEqual(pl.state_label(PLAYER_STATE_LINKDEAD), "Linkdead")
        self.assertEqual(pl.state_label(None), "—")

    def test_full_lifecycle_walk(self):
        """A representative end-to-end walk: new -> spawn -> lobby -> play ->
        death -> spawn -> lobby -> play -> quit -> lobby."""
        p = _Player(state=None)
        self.assertEqual(pl.route_on_login(p), PLAYER_STATE_SPAWNING)
        p.db.player_class = "Vanguard"
        self.assertTrue(pl.finish_spawning(p))          # -> lobby
        self.assertTrue(pl.enter_game(p))               # -> playing
        pl.record_death(p, 5, 5, "terra")               # -> spawning
        self.assertEqual(p.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertTrue(pl.finish_spawning(p))          # -> lobby
        self.assertTrue(pl.enter_game(p))               # -> playing
        self.assertTrue(pl.to_lobby(p))                 # -> lobby (quit)
        self.assertEqual(p.db.player_state, PLAYER_STATE_LOBBY)


if __name__ == "__main__":
    unittest.main()
