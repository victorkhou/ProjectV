"""
Global publish-subscribe event bus for decoupled system communication.

Provides a simple pub-sub mechanism so game systems can communicate
without direct coupling. Subscribers register callbacks for named events;
publishers fire events with arbitrary keyword payloads.

"""

from collections import defaultdict
from typing import Callable

# ------------------------------------------------------------------ #
#  Event name constants
# ------------------------------------------------------------------ #

PLAYER_LOGIN = "player_login"
PLAYER_LOGOUT = "player_logout"
PLAYER_MOVED = "player_moved"
PLAYER_ELIMINATED = "player_eliminated"
# A player's lifecycle state changed (world.player_lifecycle.transition).
# Payload: player, old_state, new_state, reason
PLAYER_STATE_CHANGED = "player_state_changed"
# An enemy NPC (npc_type="enemy") was killed and permanently deleted.
# Payload: attacker, victim, tile
NPC_ELIMINATED = "npc_eliminated"
# An NPC base was eliminated (its HQ destroyed → whole base wiped, PvE).
# Payload: attacker, sentinel, tier, planet, x, y
BASE_ELIMINATED = "base_eliminated"

BUILDING_CONSTRUCTED = "building_constructed"
BUILDING_DESTROYED = "building_destroyed"
BUILDING_UPGRADED = "building_upgraded"
CONSTRUCTION_STARTED = "construction_started"
CONSTRUCTION_COMPLETED = "construction_completed"

RANK_PROMOTED = "rank_promoted"
RANK_DEMOTED = "rank_demoted"

# Payload: player, old_level, new_level
LEVEL_CHANGED = "level_changed"

COMBAT_ACTION = "combat_action"
COMBAT_TIMER_STARTED = "combat_timer_started"

POWERUP_ACTIVATED = "powerup_activated"
POWERUP_EXPIRED = "powerup_expired"

TECHNOLOGY_RESEARCHED = "technology_researched"

RESOURCE_GATHERED = "resource_gathered"

TICK_COMPLETED = "tick_completed"

# A single player-facing notification. Domain systems emit this with a
# ``player``, a ``kind`` (one of NotificationPresenter's known kinds), and the
# ``data`` needed to format the line; the NotificationPresenter owns the string
# formatting and delivery. Keeps presentation out of the domain.
PLAYER_NOTIFICATION = "player_notification"

# --- Early-game directives (D8) --- new events the onboarding chain triggers on.
AGENT_TRAINED = "agent_trained"        # player, agent_id
AGENT_ASSIGNED = "agent_assigned"      # player, agent_id, role
ITEM_EQUIPPED = "item_equipped"        # player, item_key, slot
PATROL_SET = "patrol_set"              # player, agent_id, role

# --- Alliances --- (all published by world.systems.alliance_system.AllianceSystem)
# Payload conventions noted per event; publishing is best-effort (swallowed on
# error) so telemetry never breaks a membership mutation.
ALLIANCE_CREATED = "alliance_created"              # alliance_id, leader
ALLIANCE_MEMBER_JOINED = "alliance_member_joined"  # alliance_id, player
ALLIANCE_MEMBER_LEFT = "alliance_member_left"      # alliance_id, player
ALLIANCE_DISBANDED = "alliance_disbanded"          # alliance_id
ALLIANCE_RANK_CHANGED = "alliance_rank_changed"    # alliance_id, member, new_rank
ALLIANCE_PERK_ACTIVATED = "alliance_perk_activated"  # alliance_id, perk_key, level
ALLIANCE_RENAMED = "alliance_renamed"              # alliance_id, old, new
ALLIANCE_REQUEST_CREATED = "alliance_request_created"  # alliance_id, requester
ALLIANCE_TREASURY_DEPOSITED = "alliance_treasury_deposited"  # alliance_id, actor, amounts
ALLIANCE_TREASURY_WITHDRAWN = "alliance_treasury_withdrawn"  # alliance_id, actor, amounts

ALL_EVENTS = (
    PLAYER_LOGIN,
    PLAYER_LOGOUT,
    PLAYER_MOVED,
    PLAYER_ELIMINATED,
    PLAYER_STATE_CHANGED,
    NPC_ELIMINATED,
    BASE_ELIMINATED,
    BUILDING_CONSTRUCTED,
    BUILDING_DESTROYED,
    BUILDING_UPGRADED,
    CONSTRUCTION_STARTED,
    CONSTRUCTION_COMPLETED,
    RANK_PROMOTED,
    RANK_DEMOTED,
    LEVEL_CHANGED,
    COMBAT_ACTION,
    COMBAT_TIMER_STARTED,
    POWERUP_ACTIVATED,
    POWERUP_EXPIRED,
    TECHNOLOGY_RESEARCHED,
    RESOURCE_GATHERED,
    TICK_COMPLETED,
    PLAYER_NOTIFICATION,
    AGENT_TRAINED,
    AGENT_ASSIGNED,
    ITEM_EQUIPPED,
    PATROL_SET,
    ALLIANCE_CREATED,
    ALLIANCE_MEMBER_JOINED,
    ALLIANCE_MEMBER_LEFT,
    ALLIANCE_DISBANDED,
    ALLIANCE_RANK_CHANGED,
    ALLIANCE_PERK_ACTIVATED,
    ALLIANCE_RENAMED,
    ALLIANCE_REQUEST_CREATED,
    ALLIANCE_TREASURY_DEPOSITED,
    ALLIANCE_TREASURY_WITHDRAWN,
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
