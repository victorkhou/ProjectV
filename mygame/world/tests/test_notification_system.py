"""
Unit tests for NotificationSystem.

Payoff of the Notifier port: every event handler's broadcast is captured by an
injected fake Notifier — no Evennia, no SESSION_HANDLER, no monkeypatching of
the module-level ``world.utils.broadcast``.
"""

from mygame.world.event_bus import (
    EventBus,
    LEVEL_CHANGED,
    PLAYER_LOGIN,
    PLAYER_LOGOUT,
    PLAYER_ELIMINATED,
    RANK_PROMOTED,
    RANK_DEMOTED,
)
from mygame.world.core.ports.notifier import Notifier
from mygame.world.notification_system import NotificationSystem


class _FakeNotifier(Notifier):
    def __init__(self):
        self.sent = []

    def broadcast(self, message: str, cls: str = "game-chat") -> None:
        self.sent.append(message)


class _Player:
    def __init__(self, key):
        self.key = key


class _Rank:
    def __init__(self, name):
        self.name = name


def _make():
    bus = EventBus()
    notifier = _FakeNotifier()
    system = NotificationSystem(bus, notifier=notifier)
    return bus, notifier, system


class TestNotificationBroadcasts:
    def test_login_broadcast(self):
        bus, notifier, _ = _make()
        bus.publish(PLAYER_LOGIN, player=_Player("Alice"))
        assert any("Alice has logged in" in m for m in notifier.sent)

    def test_logout_broadcast(self):
        bus, notifier, _ = _make()
        bus.publish(PLAYER_LOGOUT, player=_Player("Bob"))
        assert any("Bob has logged out" in m for m in notifier.sent)

    def test_elimination_broadcast(self):
        bus, notifier, _ = _make()
        bus.publish(PLAYER_ELIMINATED, attacker=_Player("Ann"), victim=_Player("Vic"))
        assert any("Ann has eliminated Vic" in m for m in notifier.sent)

    def test_elimination_direct_player_kill_has_no_unit_suffix(self):
        """A player killing directly (owner == attacker, no unit kind) reads
        plainly, with no possessive suffix."""
        bus, notifier, _ = _make()
        ann = _Player("Ann")
        bus.publish(PLAYER_ELIMINATED, attacker=ann, victim=_Player("Vic"),
                    attacker_owner=ann, attacker_kind="")
        assert any("Ann has eliminated Vic" in m for m in notifier.sent)
        assert not any("'s" in m for m in notifier.sent)

    def test_elimination_by_turret_attributed_to_owner(self):
        """A turret kill is announced as 'A's Turret has eliminated B' — the
        owning player is the named killer, with the unit suffix."""
        bus, notifier, _ = _make()
        turret = _Player("TU-11-10")  # a non-player entity; only its key matters
        bus.publish(PLAYER_ELIMINATED, attacker=turret, victim=_Player("Bob"),
                    attacker_owner=_Player("Ann"), attacker_kind="turret")
        assert any("Ann's Turret has eliminated Bob" in m for m in notifier.sent)

    def test_elimination_by_agent_attributed_to_owner(self):
        """An agent kill is announced as 'A's Agent has eliminated B'."""
        bus, notifier, _ = _make()
        agent = _Player("Agent")
        bus.publish(PLAYER_ELIMINATED, attacker=agent, victim=_Player("Bob"),
                    attacker_owner=_Player("Ann"), attacker_kind="agent")
        assert any("Ann's Agent has eliminated Bob" in m for m in notifier.sent)

    def test_level_gain_broadcast(self):
        bus, notifier, _ = _make()
        bus.publish(LEVEL_CHANGED, player=_Player("Eve"), old_level=5, new_level=6)
        assert any("Eve reached level 6" in m for m in notifier.sent)

    def test_multi_level_gain_announces_the_new_level(self):
        bus, notifier, _ = _make()
        bus.publish(LEVEL_CHANGED, player=_Player("Fay"), old_level=3, new_level=6)
        assert any("Fay reached level 6" in m for m in notifier.sent)

    def test_level_drop_is_not_broadcast_as_a_gain(self):
        """A death XP loss lowers the level; it must not read as a level-up."""
        bus, notifier, _ = _make()
        bus.publish(LEVEL_CHANGED, player=_Player("Gus"), old_level=6, new_level=4)
        assert not any("reached level" in m for m in notifier.sent)

    def test_missing_old_level_still_announces_the_gain(self):
        """A payload without old_level (defensive) still announces the level."""
        bus, notifier, _ = _make()
        bus.publish(LEVEL_CHANGED, player=_Player("Hal"), new_level=2)
        assert any("Hal reached level 2" in m for m in notifier.sent)

    def test_promotion_broadcast(self):
        bus, notifier, _ = _make()
        bus.publish(RANK_PROMOTED, player=_Player("Cy"), new_rank=_Rank("Sergeant"))
        assert any("promoted to Sergeant" in m for m in notifier.sent)

    def test_demotion_broadcast(self):
        bus, notifier, _ = _make()
        bus.publish(RANK_DEMOTED, player=_Player("Dee"), new_rank=_Rank("Recruit"))
        assert any("demoted to Recruit" in m for m in notifier.sent)

    def test_defaults_to_evennia_notifier_when_none(self):
        # Constructing with no notifier must not raise (lazy EvenniaNotifier).
        system = NotificationSystem(EventBus())
        assert system._notifier is not None
