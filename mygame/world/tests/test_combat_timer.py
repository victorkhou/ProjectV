"""
Unit tests for the combat timer subscriber (world/combat_timer.py).

Requirements: 17.1, 17.2, 17.3, 17.4, 17.5
"""

import unittest
from unittest.mock import patch

from world.combat_timer import (
    COMBAT_TIMER_DURATION,
    on_combat_action,
    subscribe_combat_timer,
)
from world.event_bus import (
    COMBAT_ACTION,
    COMBAT_TIMER_STARTED,
    PLAYER_NOTIFICATION,
    EventBus,
)


class _FakeDB:
    def __init__(self, combat_xp=0):
        self.combat_timer_expires = 0
        self.combat_xp = combat_xp


class _FakePlayer:
    def __init__(self):
        self.db = _FakeDB(combat_xp=100)


class _FakeBuilding:
    """Non-player entity (no combat_xp) — should NOT get a timer."""
    def __init__(self):
        self.db = type("db", (), {"combat_timer_expires": 0})()


class TestOnCombatAction(unittest.TestCase):
    """Test on_combat_action sets combat_timer_expires correctly."""

    def setUp(self):
        self.event_bus = EventBus()
        self.player = _FakePlayer()

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_sets_timer_on_player(self, _mock_tick):
        on_combat_action(self.event_bus, player=self.player)
        self.assertEqual(
            self.player.db.combat_timer_expires,
            100 + COMBAT_TIMER_DURATION,
        )

    @patch("world.combat_timer._get_current_tick", return_value=200)
    def test_resets_timer_on_subsequent_event(self, _mock_tick):
        self.player.db.combat_timer_expires = 150  # old timer
        on_combat_action(self.event_bus, player=self.player)
        self.assertEqual(
            self.player.db.combat_timer_expires,
            200 + COMBAT_TIMER_DURATION,
        )

    @patch("world.combat_timer._get_current_tick", return_value=50)
    def test_no_player_kwarg_is_noop(self, _mock_tick):
        on_combat_action(self.event_bus)  # no player
        # Nothing to assert — just ensure no exception

    def test_uses_current_tick_from_payload_without_db_query(self):
        """When the publisher supplies current_tick (CombatEngine does), the
        subscriber uses it and NEVER calls _get_current_tick (no per-hit
        search_script DB query)."""
        with patch("world.combat_timer._get_current_tick") as mock_tick:
            on_combat_action(
                self.event_bus, player=self.player, current_tick=777,
            )
            self.assertEqual(
                self.player.db.combat_timer_expires,
                777 + COMBAT_TIMER_DURATION,
            )
            mock_tick.assert_not_called()

    @patch("world.combat_timer._get_current_tick", return_value=42)
    def test_falls_back_to_lookup_when_payload_has_no_tick(self, mock_tick):
        """Publishers that don't supply current_tick (e.g. vision events) still
        work via the live lookup."""
        on_combat_action(self.event_bus, player=self.player)
        self.assertEqual(
            self.player.db.combat_timer_expires, 42 + COMBAT_TIMER_DURATION,
        )
        mock_tick.assert_called_once()

    @patch("world.combat_timer._get_current_tick", return_value=10)
    def test_publishes_combat_timer_started(self, _mock_tick):
        received = []
        self.event_bus.subscribe(
            COMBAT_TIMER_STARTED,
            lambda **kw: received.append(kw),
        )
        on_combat_action(self.event_bus, player=self.player)
        self.assertEqual(len(received), 1)
        self.assertIs(received[0]["player"], self.player)
        self.assertEqual(received[0]["expires"], 10 + COMBAT_TIMER_DURATION)

    @patch("world.combat_timer._get_current_tick", return_value=10)
    def test_notifies_player_on_entering_combat(self, _mock_tick):
        """A player NOT already in combat gets a 'combat_started' notification."""
        notes = []
        self.event_bus.subscribe(
            PLAYER_NOTIFICATION, lambda **kw: notes.append(kw)
        )
        on_combat_action(self.event_bus, player=self.player)
        started = [n for n in notes if n.get("kind") == "combat_started"]
        self.assertEqual(len(started), 1)
        self.assertIs(started[0]["player"], self.player)

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_no_reentry_notification_while_already_in_combat(self, _mock_tick):
        """A hit while already in combat resets the timer but does NOT re-notify."""
        # Timer already active (expires in the future relative to tick 100).
        self.player.db.combat_timer_expires = 100 + COMBAT_TIMER_DURATION
        notes = []
        self.event_bus.subscribe(
            PLAYER_NOTIFICATION, lambda **kw: notes.append(kw)
        )
        on_combat_action(self.event_bus, player=self.player)
        started = [n for n in notes if n.get("kind") == "combat_started"]
        self.assertEqual(started, [])
        # Timer still refreshed.
        self.assertEqual(
            self.player.db.combat_timer_expires, 100 + COMBAT_TIMER_DURATION
        )


class TestSubscribeCombatTimer(unittest.TestCase):
    """Test that subscribe_combat_timer wires the handler to COMBAT_ACTION."""

    @patch("world.combat_timer._get_current_tick", return_value=500)
    def test_combat_action_triggers_timer(self, _mock_tick):
        bus = EventBus()
        player = _FakePlayer()
        subscribe_combat_timer(bus)
        bus.publish(COMBAT_ACTION, player=player)
        self.assertEqual(
            player.db.combat_timer_expires,
            500 + COMBAT_TIMER_DURATION,
        )


class TestCombatEngineIntegration(unittest.TestCase):
    """Test that attacker/target kwargs from CombatEngine set timers."""

    def setUp(self):
        self.event_bus = EventBus()

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_attacker_player_gets_timer(self, _mock_tick):
        """Player attacker gets a combat timer."""
        attacker = _FakePlayer()
        target = _FakeBuilding()
        on_combat_action(self.event_bus, attacker=attacker, target=target)
        self.assertEqual(
            attacker.db.combat_timer_expires,
            100 + COMBAT_TIMER_DURATION,
        )

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_target_player_gets_timer(self, _mock_tick):
        """Player target gets a combat timer."""
        attacker = _FakeBuilding()  # turret
        target = _FakePlayer()
        on_combat_action(self.event_bus, attacker=attacker, target=target)
        self.assertEqual(
            target.db.combat_timer_expires,
            100 + COMBAT_TIMER_DURATION,
        )

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_both_players_get_timer(self, _mock_tick):
        """When both attacker and target are players, both get timers."""
        attacker = _FakePlayer()
        target = _FakePlayer()
        on_combat_action(self.event_bus, attacker=attacker, target=target)
        expected = 100 + COMBAT_TIMER_DURATION
        self.assertEqual(attacker.db.combat_timer_expires, expected)
        self.assertEqual(target.db.combat_timer_expires, expected)

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_attacker_owner_enters_combat(self, _mock_tick):
        """When A's turret (non-player attacker) hits player B, A's OWNING player
        (passed as attacker_owner) also enters combat — not just B."""
        turret = _FakeBuilding()          # non-player unit
        owner_a = _FakePlayer()           # player A behind the turret
        target_b = _FakePlayer()
        on_combat_action(self.event_bus, attacker=turret, target=target_b,
                         attacker_owner=owner_a)
        expected = 100 + COMBAT_TIMER_DURATION
        self.assertEqual(owner_a.db.combat_timer_expires, expected)
        self.assertEqual(target_b.db.combat_timer_expires, expected)

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_target_owner_enters_combat(self, _mock_tick):
        """When B attacks A's agent (non-player target), A's OWNING player
        (target_owner) enters combat too."""
        attacker_b = _FakePlayer()
        agent = _FakeBuilding()           # stands in for a non-player unit target
        owner_a = _FakePlayer()
        on_combat_action(self.event_bus, attacker=attacker_b, target=agent,
                         target_owner=owner_a)
        expected = 100 + COMBAT_TIMER_DURATION
        self.assertEqual(owner_a.db.combat_timer_expires, expected)
        self.assertEqual(attacker_b.db.combat_timer_expires, expected)

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_building_attacker_no_timer(self, _mock_tick):
        """Non-player entities (buildings) don't get combat timers."""
        building = _FakeBuilding()
        target = _FakePlayer()
        on_combat_action(self.event_bus, attacker=building, target=target)
        self.assertEqual(building.db.combat_timer_expires, 0)

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_no_attacker_no_target_is_noop(self, _mock_tick):
        """No crash when neither attacker nor target nor player is provided."""
        on_combat_action(self.event_bus, damage=50)

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_publishes_event_for_each_player(self, _mock_tick):
        """A COMBAT_TIMER_STARTED event is published for each player."""
        received = []
        self.event_bus.subscribe(
            COMBAT_TIMER_STARTED,
            lambda **kw: received.append(kw),
        )
        attacker = _FakePlayer()
        target = _FakePlayer()
        on_combat_action(self.event_bus, attacker=attacker, target=target)
        self.assertEqual(len(received), 2)

    @patch("world.combat_timer._get_current_tick", return_value=300)
    def test_end_to_end_via_event_bus(self, _mock_tick):
        """Full wiring: subscribe → publish COMBAT_ACTION → timers set."""
        bus = EventBus()
        subscribe_combat_timer(bus)
        attacker = _FakePlayer()
        target = _FakePlayer()
        bus.publish(COMBAT_ACTION, attacker=attacker, target=target, damage=25)
        expected = 300 + COMBAT_TIMER_DURATION
        self.assertEqual(attacker.db.combat_timer_expires, expected)
        self.assertEqual(target.db.combat_timer_expires, expected)


class TestPlayerInCombat(unittest.TestCase):
    """player_in_combat: True iff combat_timer_expires is strictly future."""

    def _char(self, expiry):
        c = _FakePlayer()
        c.db.combat_timer_expires = expiry
        return c

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_future_expiry_is_in_combat(self, _t):
        from world.combat_timer import player_in_combat
        self.assertTrue(player_in_combat(self._char(150)))

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_past_expiry_is_not_in_combat(self, _t):
        from world.combat_timer import player_in_combat
        self.assertFalse(player_in_combat(self._char(50)))

    @patch("world.combat_timer._get_current_tick", return_value=100)
    def test_zero_expiry_is_not_in_combat(self, _t):
        from world.combat_timer import player_in_combat
        self.assertFalse(player_in_combat(self._char(0)))

    def test_none_char_is_not_in_combat(self):
        from world.combat_timer import player_in_combat
        self.assertFalse(player_in_combat(None))

    @patch("world.combat_timer._get_current_tick", side_effect=RuntimeError("boom"))
    def test_lookup_failure_errs_toward_in_combat(self, _t):
        # A tick-lookup failure with a positive expiry blocks (safer default).
        from world.combat_timer import player_in_combat
        self.assertTrue(player_in_combat(self._char(50)))


if __name__ == "__main__":
    unittest.main()
