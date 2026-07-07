"""
Unit tests for the PlayerNotifier port, its Evennia adapter, and the
domain-side BaseSystem.notify event emission.

The PlayerNotifier is the transport the NotificationPresenter delivers through;
its adapter absorbs None/missing-sink/error guarding so a bad sink never
crashes a tick. BaseSystem.notify only emits a structured event — the domain
neither formats nor sends text.
"""

from mygame.world.core.ports.player_notifier import PlayerNotifier
from mygame.world.adapters.evennia_player_notifier import EvenniaPlayerNotifier
from mygame.world.systems.base_system import BaseSystem


class TestPlayerNotifierPort:
    def test_abstract(self):
        try:
            PlayerNotifier()
        except TypeError:
            return
        raise AssertionError("PlayerNotifier should be abstract")


class TestBaseSystemNotify:
    """BaseSystem.notify emits a structured PLAYER_NOTIFICATION event.

    The domain no longer formats or delivers text: it publishes
    (player, kind, data) and the NotificationPresenter renders it.
    """

    def test_notify_publishes_event(self):
        from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION

        bus = EventBus()
        captured = []
        bus.subscribe(PLAYER_NOTIFICATION, lambda **kw: captured.append(kw))
        system = BaseSystem(registry=None, event_bus=bus)

        player = object()
        system.notify(player, "harvest_drop", amount=5, resource_type="Wood")

        assert len(captured) == 1
        assert captured[0]["player"] is player
        assert captured[0]["kind"] == "harvest_drop"
        assert captured[0]["data"] == {"amount": 5, "resource_type": "Wood"}

    def test_notify_none_player_is_dropped(self):
        from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION

        bus = EventBus()
        captured = []
        bus.subscribe(PLAYER_NOTIFICATION, lambda **kw: captured.append(kw))
        system = BaseSystem(registry=None, event_bus=bus)

        system.notify(None, "harvest_drop", amount=5)
        assert captured == []


class TestEvenniaPlayerNotifierGuarding:
    def test_none_player_is_noop(self):
        # Must not raise.
        EvenniaPlayerNotifier().notify(None, "x")

    def test_player_without_msg_is_noop(self):
        EvenniaPlayerNotifier().notify(object(), "x")

    def test_delivers_to_msg_sink(self):
        class _P:
            def __init__(self):
                self.got = []

            def msg(self, m):
                self.got.append(m)

        p = _P()
        EvenniaPlayerNotifier().notify(p, "ping")
        assert p.got == ["ping"]

    def test_msg_error_is_swallowed(self):
        class _Boom:
            def msg(self, m):
                raise RuntimeError("transport down")

        # Must not propagate — combat/ticks never break on a bad sink.
        EvenniaPlayerNotifier().notify(_Boom(), "x")
