"""
Unit tests for NotificationSystem.

Payoff of the Notifier port: every event handler's broadcast is captured by an
injected fake Notifier — no Evennia, no SESSION_HANDLER, no monkeypatching of
the module-level ``world.utils.broadcast``.
"""

from mygame.world.event_bus import (
    EventBus,
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
