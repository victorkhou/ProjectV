"""
Unit tests for NPC movement engine methods.

Tests the movement attributes and methods added to the NPC typeclass:
- advance_movement(tick_number)
- set_movement_queue(path)
- clear_movement()
- at_movement_complete()

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 2.8, 2.9, 5.1, 8.1, 8.6
"""

import sys
import types

# Ensure Evennia stubs are loaded before importing game modules
from mygame.conftest import _ensure_evennia_stubs
_ensure_evennia_stubs()

from mygame.typeclasses.npcs import NPC


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

class FakeCoordIndex:
    """Minimal coordinate index stub."""
    def move(self, obj, old_x, old_y, new_x, new_y):
        pass


class FakeRoom:
    """Minimal PlanetRoom stub with move_entity."""
    def __init__(self):
        self.coord_index = FakeCoordIndex()
        self.move_calls = []

    def move_entity(self, obj, new_x, new_y):
        self.move_calls.append((obj, new_x, new_y))
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y


def _make_npc(**overrides):
    """Create an NPC instance with test defaults."""
    npc = NPC.__new__(NPC)
    npc.at_object_creation = lambda: None  # skip real creation

    # Manually set up the attribute store from conftest stubs
    from mygame.conftest import _ensure_evennia_stubs
    # The NPC inherits from DefaultObject which sets up _attr_store/db
    # We need to replicate that manually
    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class _FakeTags:
        def __init__(self):
            self._tags = {}
        def add(self, key, category=None):
            self._tags.setdefault(category, []).append(key)
        def get(self, key, category=None):
            return key in self._tags.get(category, [])

    store = _AttrStore()
    npc._attr_store = store
    npc.attributes = store
    npc.db = _DbProxy(store)
    npc.tags = _FakeTags()
    npc.key = "test_npc"
    npc.location = overrides.pop("location", None)

    # Set default NPC attributes
    npc.db.coord_x = overrides.pop("coord_x", 0)
    npc.db.coord_y = overrides.pop("coord_y", 0)
    npc.db.movement_queue = overrides.pop("movement_queue", [])
    npc.db.movement_delay = overrides.pop("movement_delay", 1)
    npc.db.activity_status = overrides.pop("activity_status", "Idle")
    npc.db.incapacitated = overrides.pop("incapacitated", False)
    npc.db.hp = overrides.pop("hp", 100)
    npc.db.hp_max = 100

    for k, v in overrides.items():
        setattr(npc.db, k, v)

    return npc


# ------------------------------------------------------------------ #
#  Tests: Default attributes
# ------------------------------------------------------------------ #

class TestNPCMovementDefaults:
    """Verify default movement attribute values."""

    def test_default_movement_queue_is_empty(self):
        npc = _make_npc()
        assert npc.db.movement_queue == []

    def test_default_movement_delay_is_one(self):
        npc = _make_npc()
        assert npc.db.movement_delay == 1

    def test_default_activity_status_is_idle(self):
        npc = _make_npc()
        assert npc.db.activity_status == "Idle"


# ------------------------------------------------------------------ #
#  Tests: set_movement_queue
# ------------------------------------------------------------------ #

class TestSetMovementQueue:
    """Verify set_movement_queue replaces the queue."""

    def test_sets_queue_from_tuples(self):
        npc = _make_npc()
        npc.set_movement_queue([(1, 2), (3, 4), (5, 6)])
        assert npc.db.movement_queue == [[1, 2], [3, 4], [5, 6]]

    def test_replaces_existing_queue(self):
        npc = _make_npc(movement_queue=[[10, 10]])
        npc.set_movement_queue([(1, 1)])
        assert npc.db.movement_queue == [[1, 1]]

    def test_empty_path_clears_queue(self):
        npc = _make_npc(movement_queue=[[1, 1]])
        npc.set_movement_queue([])
        assert npc.db.movement_queue == []


# ------------------------------------------------------------------ #
#  Tests: clear_movement
# ------------------------------------------------------------------ #

class TestClearMovement:
    """Verify clear_movement empties the queue."""

    def test_clears_non_empty_queue(self):
        npc = _make_npc(movement_queue=[[1, 2], [3, 4]])
        npc.clear_movement()
        assert npc.db.movement_queue == []

    def test_clears_already_empty_queue(self):
        npc = _make_npc()
        npc.clear_movement()
        assert npc.db.movement_queue == []


# ------------------------------------------------------------------ #
#  Tests: at_movement_complete
# ------------------------------------------------------------------ #

class TestAtMovementComplete:
    """Verify at_movement_complete is a no-op hook."""

    def test_does_not_raise(self):
        npc = _make_npc()
        npc.at_movement_complete()  # should not raise


# ------------------------------------------------------------------ #
#  Tests: advance_movement
# ------------------------------------------------------------------ #

class TestAdvanceMovement:
    """Verify advance_movement tick-driven movement logic."""

    def test_moves_one_step_on_aligned_tick(self):
        room = FakeRoom()
        npc = _make_npc(location=room, movement_queue=[[5, 5], [6, 5]])
        result = npc.advance_movement(tick_number=1)
        assert result is True
        assert npc.db.coord_x == 5
        assert npc.db.coord_y == 5
        assert npc.db.movement_queue == [[6, 5]]

    def test_skips_on_non_aligned_tick(self):
        """With delay=2, only even ticks should advance."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_delay=2,
                        movement_queue=[[5, 5]])
        result = npc.advance_movement(tick_number=1)
        assert result is False
        assert npc.db.movement_queue == [[5, 5]]

    def test_moves_on_aligned_tick_with_delay(self):
        """With delay=2, tick 2 should advance."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_delay=2,
                        movement_queue=[[5, 5]])
        result = npc.advance_movement(tick_number=2)
        assert result is True
        assert npc.db.coord_x == 5
        assert npc.db.coord_y == 5

    def test_skips_when_incapacitated(self):
        room = FakeRoom()
        npc = _make_npc(location=room, incapacitated=True,
                        movement_queue=[[5, 5]])
        result = npc.advance_movement(tick_number=1)
        assert result is False
        assert npc.db.movement_queue == [[5, 5]]  # queue preserved

    def test_skips_when_queue_empty(self):
        room = FakeRoom()
        npc = _make_npc(location=room)
        result = npc.advance_movement(tick_number=1)
        assert result is False

    def test_calls_move_entity_on_room(self):
        room = FakeRoom()
        npc = _make_npc(location=room, movement_queue=[[3, 4]])
        npc.advance_movement(tick_number=1)
        assert len(room.move_calls) == 1
        assert room.move_calls[0] == (npc, 3, 4)

    def test_calls_at_movement_complete_when_queue_exhausted(self):
        room = FakeRoom()
        npc = _make_npc(location=room, movement_queue=[[5, 5]])
        completed = []
        npc.at_movement_complete = lambda: completed.append(True)
        npc.advance_movement(tick_number=1)
        assert len(completed) == 1
        assert npc.db.movement_queue == []

    def test_does_not_call_at_movement_complete_when_queue_remains(self):
        room = FakeRoom()
        npc = _make_npc(location=room, movement_queue=[[5, 5], [6, 5]])
        completed = []
        npc.at_movement_complete = lambda: completed.append(True)
        npc.advance_movement(tick_number=1)
        assert len(completed) == 0
        assert npc.db.movement_queue == [[6, 5]]

    def test_full_queue_consumption(self):
        """Walk through a 3-step queue, verify final position and completion."""
        room = FakeRoom()
        path = [[1, 0], [2, 0], [3, 0]]
        npc = _make_npc(location=room, movement_queue=path)
        completed = []
        npc.at_movement_complete = lambda: completed.append(True)

        for tick in range(1, 4):
            npc.advance_movement(tick_number=tick)

        assert npc.db.coord_x == 3
        assert npc.db.coord_y == 0
        assert npc.db.movement_queue == []
        assert len(completed) == 1

    def test_fallback_direct_coord_update_without_room(self):
        """When location has no move_entity, coords update directly."""
        npc = _make_npc(location=None, movement_queue=[[7, 8]])
        result = npc.advance_movement(tick_number=1)
        assert result is True
        assert npc.db.coord_x == 7
        assert npc.db.coord_y == 8

    def test_delay_zero_treated_as_one(self):
        """movement_delay of 0 or None should be treated as 1."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_delay=0,
                        movement_queue=[[5, 5]])
        result = npc.advance_movement(tick_number=1)
        assert result is True
