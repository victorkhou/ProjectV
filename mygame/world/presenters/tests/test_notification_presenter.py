"""
Unit tests for NotificationPresenter.

Proves the format-table: each notification kind produces the expected string
from sample data, and the presenter delivers it to the correct player.
"""

from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION
from mygame.world.presenters.notification_presenter import NotificationPresenter


class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, player, message):
        self.sent.append((player, message))


class _Player:
    pass


def _make():
    bus = EventBus()
    notifier = _FakeNotifier()
    presenter = NotificationPresenter(bus, player_notifier=notifier)
    return bus, notifier, presenter


class TestFormatTable:
    def test_rank_level_up(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="rank_level_up",
                    data={"level": 7, "rank_name": "Private", "sub": 2})
        assert n.sent == [(p, "You are now Level 7 (Private 2)")]

    def test_building_progress_upgrade(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_progress",
                    data={"btype": "HQ", "target_level": 3, "progress": 10,
                          "total": 50, "remaining": 40})
        assert "Upgrading HQ to L3" in n.sent[0][1]
        assert "10/50s" in n.sent[0][1]

    def test_building_progress_construction(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_progress",
                    data={"btype": "EX", "target_level": None, "progress": 5,
                          "total": 20, "remaining": 15})
        assert "Constructing EX" in n.sent[0][1]

    def test_building_complete_upgrade(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_complete",
                    data={"building_type": "VT", "target_level": 4})
        assert "VT upgraded to level 4" in n.sent[0][1]

    def test_building_complete_new(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_complete",
                    data={"building_type": "EX", "target_level": None})
        assert "construction finished" in n.sent[0][1]

    def test_agent_training_complete(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="agent_training_complete",
                    data={"agent_id": 3})
        assert "Agent #3 training finished" in n.sent[0][1]
        assert "assign 3" in n.sent[0][1]

    def test_agent_training_progress(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="agent_training_progress",
                    data={"agent_id": 5, "remaining": 12})
        assert "Agent #5" in n.sent[0][1]
        assert "12s remaining" in n.sent[0][1]

    def test_harvest_drop(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="harvest_drop",
                    data={"amount": 20, "resource_type": "Iron"})
        assert "+20 Iron dropped" in n.sent[0][1]

    def test_attacked(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="attacked",
                    data={"attacker_name": "Rex", "weapon_name": "Axe", "damage": 15})
        msg = n.sent[0][1]
        assert "Rex" in msg
        assert "Axe" in msg
        assert "15" in msg

    def test_building_attacked(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="building_attacked",
                    data={"building_name": "Wall", "attacker_name": "Orc",
                          "weapon_name": "Club", "damage": 8})
        msg = n.sent[0][1]
        assert "Wall" in msg and "Orc" in msg and "8" in msg

    def test_ability_active(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="ability_active",
                    data={"key": "delivery", "agent_id": 7})
        assert "delivery" in n.sent[0][1]
        assert "now active" in n.sent[0][1]
        assert "#7" in n.sent[0][1]

    def test_ability_relocked(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="ability_relocked",
                    data={"key": "delivery", "agent_id": 7, "required": 21})
        msg = n.sent[0][1]
        assert "re-locked" in msg
        assert "21" in msg

    def test_ability_available(self):
        bus, n, _ = _make()
        p = _Player()
        bus.publish(PLAYER_NOTIFICATION, player=p, kind="ability_available",
                    data={"key": "delivery", "agent_id": 7})
        msg = n.sent[0][1]
        assert "available" in msg
        assert "agent ability 7 delivery on" in msg

    def test_unknown_kind_is_logged_not_delivered(self):
        bus, n, _ = _make()
        bus.publish(PLAYER_NOTIFICATION, player=_Player(), kind="unknown_xyz",
                    data={})
        assert n.sent == []
