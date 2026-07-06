"""
Unit tests for the Notifier port.

Demonstrates the payoff of the abstraction: a use case that broadcasts can be
tested with an in-memory fake, with no Evennia import or SESSION_HANDLER.
"""

from mygame.world.core.ports.notifier import Notifier


class FakeNotifier(Notifier):
    """Records broadcasts instead of sending them over a transport."""

    def __init__(self):
        self.sent = []

    def broadcast(self, message: str, cls: str = "game-chat") -> None:
        self.sent.append((message, cls))


class TestNotifierPort:
    def test_fake_records_broadcast(self):
        notifier = FakeNotifier()
        notifier.broadcast("hello world")
        assert notifier.sent == [("hello world", "game-chat")]

    def test_fake_records_custom_cls(self):
        notifier = FakeNotifier()
        notifier.broadcast("system down", cls="system")
        assert notifier.sent == [("system down", "system")]

    def test_notifier_is_abstract(self):
        # The port cannot be instantiated directly — it only defines the contract.
        try:
            Notifier()
        except TypeError:
            return
        raise AssertionError("Notifier should be abstract and non-instantiable")
