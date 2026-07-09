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


if __name__ == "__main__":
    unittest.main()
