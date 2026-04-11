"""
Global publish-subscribe event bus for decoupled system communication.

Provides a simple pub-sub mechanism so game systems can communicate
without direct coupling. Subscribers register callbacks for named events;
publishers fire events with arbitrary keyword payloads.

Requirements: 28.1, 28.2, 28.3
"""

from collections import defaultdict
from typing import Callable

# ------------------------------------------------------------------ #
#  Event name constants (Requirement 28.3)
# ------------------------------------------------------------------ #

PLAYER_LOGIN = "player_login"
PLAYER_LOGOUT = "player_logout"
PLAYER_MOVED = "player_moved"
PLAYER_ELIMINATED = "player_eliminated"

BUILDING_CONSTRUCTED = "building_constructed"
BUILDING_DESTROYED = "building_destroyed"
BUILDING_UPGRADED = "building_upgraded"

RANK_PROMOTED = "rank_promoted"
RANK_DEMOTED = "rank_demoted"

COMBAT_ACTION = "combat_action"

POWERUP_ACTIVATED = "powerup_activated"
POWERUP_EXPIRED = "powerup_expired"

TECHNOLOGY_RESEARCHED = "technology_researched"

RESOURCE_GATHERED = "resource_gathered"

TICK_COMPLETED = "tick_completed"

ALL_EVENTS = (
    PLAYER_LOGIN,
    PLAYER_LOGOUT,
    PLAYER_MOVED,
    PLAYER_ELIMINATED,
    BUILDING_CONSTRUCTED,
    BUILDING_DESTROYED,
    BUILDING_UPGRADED,
    RANK_PROMOTED,
    RANK_DEMOTED,
    COMBAT_ACTION,
    POWERUP_ACTIVATED,
    POWERUP_EXPIRED,
    TECHNOLOGY_RESEARCHED,
    RESOURCE_GATHERED,
    TICK_COMPLETED,
)


class EventBus:
    """Global event bus for decoupled system communication.

    A lightweight publish-subscribe implementation. Game systems publish
    named events; subscribers react without coupling to the publisher.
    """

    def __init__(self):
        # event_name -> list of callback functions
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def publish(self, event_name: str, **kwargs) -> None:
        """Publish an event to all subscribers.

        If a subscriber raises, the exception is logged and remaining
        subscribers still receive the event.

        Args:
            event_name: The name of the event to publish.
            **kwargs: Arbitrary keyword payload forwarded to each subscriber.
        """
        for callback in self._subscribers.get(event_name, []):
            try:
                callback(event_name=event_name, **kwargs)
            except Exception:
                import logging
                logging.getLogger("mygame.event_bus").exception(
                    "Subscriber %r failed for event %s", callback, event_name
                )

    def subscribe(self, event_name: str, callback: Callable) -> None:
        """Register a callback for a named event.

        The same callback can be subscribed to multiple events.
        Subscribing the same callback to the same event twice is a no-op.

        Args:
            event_name: The event to listen for.
            callback: A callable invoked with ``(event_name=..., **payload)``.
        """
        if callback not in self._subscribers[event_name]:
            self._subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: Callable) -> None:
        """Remove a callback from a named event.

        Silently does nothing if the callback was not subscribed.

        Args:
            event_name: The event to stop listening for.
            callback: The previously registered callable.
        """
        try:
            self._subscribers[event_name].remove(callback)
        except ValueError:
            pass


# ------------------------------------------------------------------ #
#  Module-level singleton
# ------------------------------------------------------------------ #

event_bus = EventBus()
