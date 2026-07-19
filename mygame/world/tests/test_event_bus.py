"""
Unit tests for the EventBus module.

Validates: Requirements 28.1, 28.2, 28.3
"""

from mygame.world.event_bus import (
    EventBus,
    event_bus,
    ALL_EVENTS,
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
    COMBAT_TIMER_STARTED,
    POWERUP_ACTIVATED,
    POWERUP_EXPIRED,
    TECHNOLOGY_RESEARCHED,
    RESOURCE_GATHERED,
    TICK_COMPLETED,
)


class TestEventConstants:
    """Verify all event name constants are defined."""

    def test_all_events_has_correct_count(self):
        assert len(ALL_EVENTS) == 37

    def test_all_event_names_are_unique(self):
        assert len(set(ALL_EVENTS)) == len(ALL_EVENTS)

    def test_expected_events_present(self):
        expected = {
            "player_login",
            "player_logout",
            "player_moved",
            "player_eliminated",
            "player_state_changed",
            "npc_eliminated",
            "base_eliminated",
            "building_constructed",
            "building_destroyed",
            "building_upgraded",
            "construction_started",
            "construction_completed",
            "rank_promoted",
            "rank_demoted",
            "level_changed",
            "combat_action",
            "combat_timer_started",
            "powerup_activated",
            "powerup_expired",
            "technology_researched",
            "resource_gathered",
            "tick_completed",
            "player_notification",
            "agent_trained",
            "agent_assigned",
            "item_equipped",
            "patrol_set",
            "alliance_created",
            "alliance_member_joined",
            "alliance_member_left",
            "alliance_disbanded",
            "alliance_rank_changed",
            "alliance_perk_activated",
            "alliance_renamed",
            "alliance_request_created",
            "alliance_treasury_deposited",
            "alliance_treasury_withdrawn",
        }
        assert set(ALL_EVENTS) == expected


class TestEventBusPublishSubscribe:
    """Core publish/subscribe behaviour."""

    def test_subscriber_receives_event(self):
        bus = EventBus()
        received = []
        bus.subscribe("test_event", lambda **kw: received.append(kw))
        bus.publish("test_event", foo="bar")
        assert len(received) == 1
        assert received[0]["foo"] == "bar"
        assert received[0]["event_name"] == "test_event"

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe("evt", lambda **kw: a.append(kw))
        bus.subscribe("evt", lambda **kw: b.append(kw))
        bus.publish("evt", x=1)
        assert len(a) == 1
        assert len(b) == 1

    def test_subscriber_only_receives_subscribed_event(self):
        bus = EventBus()
        received = []
        bus.subscribe("alpha", lambda **kw: received.append(kw))
        bus.publish("beta", x=1)
        assert received == []

    def test_publish_with_no_subscribers_does_not_error(self):
        bus = EventBus()
        bus.publish("nobody_listening", data=42)  # should not raise

    def test_publish_passes_kwargs(self):
        bus = EventBus()
        received = []
        bus.subscribe("evt", lambda **kw: received.append(kw))
        bus.publish("evt", a=1, b="two", c=[3])
        payload = received[0]
        assert payload["a"] == 1
        assert payload["b"] == "two"
        assert payload["c"] == [3]


class TestEventBusUnsubscribe:
    """Unsubscribe behaviour."""

    def test_unsubscribed_callback_not_called(self):
        bus = EventBus()
        received = []
        cb = lambda **kw: received.append(kw)
        bus.subscribe("evt", cb)
        bus.unsubscribe("evt", cb)
        bus.publish("evt")
        assert received == []

    def test_unsubscribe_unknown_callback_is_silent(self):
        bus = EventBus()
        bus.unsubscribe("evt", lambda **kw: None)  # should not raise

    def test_unsubscribe_unknown_event_is_silent(self):
        bus = EventBus()
        bus.unsubscribe("nonexistent", lambda **kw: None)  # should not raise

    def test_duplicate_subscribe_is_noop(self):
        bus = EventBus()
        received = []
        cb = lambda **kw: received.append(1)
        bus.subscribe("evt", cb)
        bus.subscribe("evt", cb)
        bus.publish("evt")
        assert len(received) == 1  # called only once


class TestModuleSingleton:
    """Module-level singleton exists and works."""

    def test_singleton_is_event_bus_instance(self):
        assert isinstance(event_bus, EventBus)
