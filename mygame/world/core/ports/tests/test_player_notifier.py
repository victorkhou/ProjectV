"""
Unit tests for the PlayerNotifier port and BaseSystem.notify_player.

Payoff: a system's per-player notifications can be captured by an injected fake
PlayerNotifier — no player.msg sink, no Evennia — and the adapter's guarding
means a None/sink-less player is a silent no-op rather than a crash.
"""

from mygame.world.core.ports.player_notifier import PlayerNotifier
from mygame.world.adapters.evennia_player_notifier import EvenniaPlayerNotifier
from mygame.world.systems.base_system import BaseSystem

# NOTE: BaseSystem imports its default adapter via the ``world.`` namespace,
# which is a DISTINCT module object from ``mygame.world.`` — so identity checks
# on the default notifier compare by class *name*, not isinstance.


class _FakePlayerNotifier(PlayerNotifier):
    def __init__(self):
        self.sent = []  # list of (player, message)

    def notify(self, player, message):
        self.sent.append((player, message))


class TestPlayerNotifierPort:
    def test_abstract(self):
        try:
            PlayerNotifier()
        except TypeError:
            return
        raise AssertionError("PlayerNotifier should be abstract")


class TestBaseSystemNotifyPlayer:
    def test_injected_notifier_captures_message(self):
        notifier = _FakePlayerNotifier()
        system = BaseSystem(registry=None, event_bus=None, player_notifier=notifier)
        player = object()
        system.notify_player(player, "hello")
        assert notifier.sent == [(player, "hello")]

    def test_defaults_to_evennia_adapter(self):
        system = BaseSystem(registry=None, event_bus=None)
        assert type(system._player_notifier).__name__ == "EvenniaPlayerNotifier"


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
