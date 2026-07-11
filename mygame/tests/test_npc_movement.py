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

    def test_arrival_status_applied_on_completion(self):
        """A pending arrival_status (e.g. 'Working' for a building assignment) is
        applied when the queue drains — NOT overwritten with plain 'Idle'.

        Regression: an engineer assigned to an Armory walks there and, on
        arrival, must read 'Working' (it has no per-tick status setter of its
        own), not 'Idle', while its building produces items.
        """
        room = FakeRoom()
        npc = _make_npc(location=room, movement_queue=[[5, 5]],
                        arrival_status="Working")
        npc.advance_movement(tick_number=1)
        assert npc.db.movement_queue == []
        assert npc.db.activity_status == "Working"
        # Consumed so it doesn't leak into a later, unrelated move.
        assert npc.db.arrival_status is None

    def test_arrival_defaults_to_idle_without_pending_status(self):
        """With no pending arrival_status, completion resets to Idle (patrol/
        return legs that carry no intended arrival status)."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_queue=[[5, 5]],
                        activity_status="Moving (1 tiles)")
        npc.advance_movement(tick_number=1)
        assert npc.db.activity_status == "Idle"

    def test_set_movement_queue_clears_stale_arrival_status(self):
        """A new leg started via set_movement_queue clears any prior
        arrival_status so it can't leak from a previous move."""
        npc = _make_npc(arrival_status="Working")
        npc.set_movement_queue([(1, 1), (2, 2)])
        assert npc.db.arrival_status is None

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


# ------------------------------------------------------------------ #
#  Tests: Equipment speed modifier (Req 8.8)
# ------------------------------------------------------------------ #

class _FakeEquipment:
    """Minimal EquipmentHandler stub returning a fixed move_speed total."""

    def __init__(self, move_speed=0):
        self._move_speed = move_speed

    def get_stat_total(self, stat_name):
        if stat_name == "move_speed":
            return self._move_speed
        return 0


class TestAdvanceMovementEquipmentModifier:
    """advance_movement applies a move_speed modifier via compute_effective_delay.

    Validates: Requirements 8.8
    """

    def test_positive_modifier_speeds_up_movement(self):
        """A +1 move_speed turns a base delay of 2 into an effective delay of 1,
        so the NPC advances on an odd tick it would otherwise skip."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_delay=2,
                        coord_x=5, coord_y=5, movement_queue=[[5, 6]])
        npc._equipment_handler = _FakeEquipment(move_speed=1)

        # base_delay=2 would skip tick 1; with modifier effective delay=1
        result = npc.advance_movement(tick_number=1)
        assert result is True
        assert npc.db.movement_queue == []

    def test_zero_modifier_preserves_base_delay(self):
        """With no move_speed bonus, base delay gating is unchanged."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_delay=2,
                        coord_x=5, coord_y=5, movement_queue=[[5, 6]])
        npc._equipment_handler = _FakeEquipment(move_speed=0)

        # delay=2 still skips tick 1
        result = npc.advance_movement(tick_number=1)
        assert result is False
        assert npc.db.movement_queue == [[5, 6]]

    def test_no_equipment_handler_defaults_to_base_delay(self):
        """Missing/failed equipment lookup yields a 0 modifier (no crash)."""
        room = FakeRoom()
        npc = _make_npc(location=room, movement_delay=1,
                        coord_x=5, coord_y=5, movement_queue=[[5, 6]])
        # _get_move_speed_modifier falls back to 0 when equipment is unusable
        npc._equipment_handler = None
        result = npc.advance_movement(tick_number=1)
        assert result is True
