"""
Property-based tests for EventBus publish-subscribe delivery.

**Validates: Requirements 28.1, 28.2**

Property 28: Event bus publish-subscribe delivery
- Every subscriber receives every published event it subscribed to.
- Subscribers only receive events they subscribed to (no cross-talk).
- After unsubscribe, the callback is no longer called.
- Multiple subscribers to the same event all receive it.
- Publishing with no subscribers doesn't error.
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.event_bus import EventBus

# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

event_name_st = st.text(min_size=1, max_size=30, alphabet=st.characters(
    whitelist_categories=("L", "N"), whitelist_characters="_"
))

payload_value_st = st.one_of(
    st.integers(min_value=-10_000, max_value=10_000),
    st.text(min_size=0, max_size=20),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False),
)

payload_st = st.dictionaries(
    st.text(min_size=1, max_size=10, alphabet=st.characters(
        whitelist_categories=("L",)
    )).filter(lambda k: k != "event_name"),
    payload_value_st,
    min_size=0,
    max_size=5,
)


# ------------------------------------------------------------------ #
#  Property tests
# ------------------------------------------------------------------ #


class TestProperty28Delivery:
    """Every subscriber receives every published event it subscribed to."""

    @given(event_name=event_name_st, payload=payload_st)
    @settings(max_examples=50)
    def test_subscriber_receives_published_event(self, event_name, payload):
        """A subscribed callback is invoked with the correct payload."""
        bus = EventBus()
        received = []
        bus.subscribe(event_name, lambda **kw: received.append(kw))
        bus.publish(event_name, **payload)

        assert len(received) == 1
        for key, value in payload.items():
            assert received[0][key] == value
        assert received[0]["event_name"] == event_name


class TestProperty28NoCrossTalk:
    """Subscribers only receive events they subscribed to."""

    @given(
        event_a=event_name_st,
        event_b=event_name_st,
        payload=payload_st,
    )
    @settings(max_examples=50)
    def test_no_cross_talk_between_events(self, event_a, event_b, payload):
        """Publishing event_a does not trigger subscribers of event_b."""
        assume(event_a != event_b)
        bus = EventBus()
        received_b = []
        bus.subscribe(event_b, lambda **kw: received_b.append(kw))
        bus.publish(event_a, **payload)

        assert received_b == []


class TestProperty28Unsubscribe:
    """After unsubscribe, the callback is no longer called."""

    @given(event_name=event_name_st, payload=payload_st)
    @settings(max_examples=50)
    def test_unsubscribed_callback_not_invoked(self, event_name, payload):
        """Once unsubscribed, a callback receives no further events."""
        bus = EventBus()
        received = []
        cb = lambda **kw: received.append(kw)
        bus.subscribe(event_name, cb)
        bus.unsubscribe(event_name, cb)
        bus.publish(event_name, **payload)

        assert received == []


class TestProperty28MultipleSubscribers:
    """Multiple subscribers to the same event all receive it."""

    @given(
        event_name=event_name_st,
        num_subscribers=st.integers(min_value=1, max_value=10),
        payload=payload_st,
    )
    @settings(max_examples=50)
    def test_all_subscribers_receive_event(self, event_name, num_subscribers, payload):
        """Every subscriber callback is invoked exactly once."""
        bus = EventBus()
        buckets = [[] for _ in range(num_subscribers)]
        for bucket in buckets:
            bus.subscribe(event_name, lambda bucket=bucket, **kw: bucket.append(kw))
        bus.publish(event_name, **payload)

        for bucket in buckets:
            assert len(bucket) == 1
            for key, value in payload.items():
                assert bucket[0][key] == value


class TestProperty28NoSubscribers:
    """Publishing with no subscribers doesn't error."""

    @given(event_name=event_name_st, payload=payload_st)
    @settings(max_examples=50)
    def test_publish_no_subscribers_safe(self, event_name, payload):
        """Publishing to an event with zero subscribers raises no exception."""
        bus = EventBus()
        bus.publish(event_name, **payload)  # must not raise
