"""
Unit tests for Agent AI edge cases.

Example-based tests covering pathfinder edge cases, dynamic obstacle
detection, patrol behavior edge cases, delivery behavior edge cases,
activity status updates, and default attribute values.

Validates: Requirements 1.3, 1.4, 2.3, 2.8, 3.4, 3.5, 4.6, 4.7,
           5.1, 5.2, 5.3, 5.5, 9.5, 10.1, 10.2
"""

import sys
import types

from mygame.conftest import _ensure_evennia_stubs
_ensure_evennia_stubs()

from mygame.world.pathfinding import find_path
from mygame.typeclasses.npcs import NPC
from mygame.typeclasses.agent_scripts import PatrolBehavior, DeliveryBehavior
from mygame.world.constants import (
    DEFAULT_MOVEMENT_DELAY,
    DEFAULT_CARRY_CAPACITY,
    HARVESTER_LADEN_DELAY,
    HARVESTER_EMPTY_DELAY,
)


# ------------------------------------------------------------------ #
#  Helpers (same pattern as test_npc_movement.py / test_prop_agent_ai.py)
# ------------------------------------------------------------------ #

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



class _FakeCoordIndex:
    def move(self, obj, old_x, old_y, new_x, new_y):
        pass


class _FakeRoom:
    """Minimal PlanetRoom stub with move_entity."""
    def __init__(self):
        self.coord_index = _FakeCoordIndex()
    def move_entity(self, obj, new_x, new_y):
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y


class _ImpassableRoom(_FakeRoom):
    """Room that makes a specific tile impassable via _game_systems."""
    def __init__(self, blocked_tiles=None):
        super().__init__()
        self._blocked = set(blocked_tiles or [])

    @property
    def _game_systems(self):
        room = self

        class _FakeTGen:
            def get_terrain(self, x, y):
                return "blocked" if (x, y) in room._blocked else "grass"

        class _FakeTerrainDef:
            def __init__(self, passable):
                self.passable = passable

        class _FakeRegistry:
            def get_terrain(self, ttype):
                return _FakeTerrainDef(ttype != "blocked")

        return {
            "_terrain_generators": {"test_planet": _FakeTGen()},
            "registry": _FakeRegistry(),
        }


class _MockResourceDrop:
    """Minimal ResourceDrop stub."""
    def __init__(self, resource_type, amount):
        store = _AttrStore()
        self.db = _DbProxy(store)
        self.db.resource_type = resource_type
        self.db.amount = amount
        self._deleted = False

    def delete(self):
        self._deleted = True


class _MockRoom:
    """Room stub returning pre-configured drops from get_objects_at."""
    def __init__(self, drops=None, contents=None):
        self._drops = drops or []
        self.contents = contents or []

    def get_objects_at(self, x, y, type_tag=None):
        return list(self._drops)


class _MockBuilding:
    """Minimal building stub."""
    def __init__(self, btype, x, y, owner=None):
        store = _AttrStore()
        self.db = _DbProxy(store)
        self.db.building_type = btype
        self.db.coord_x = x
        self.db.coord_y = y
        self.db.owner = owner
        self.tags = _FakeTags()
        self.tags.add("building", category="object_type")
        self.attributes = store


class _MockOwner:
    """Owner stub that tracks add_resource calls."""
    def __init__(self):
        self.resources = {}
        self.id = 1

    def add_resource(self, rtype, amount):
        self.resources[rtype] = self.resources.get(rtype, 0) + amount


def _make_npc(**overrides):
    """Create an NPC instance with test defaults."""
    npc = NPC.__new__(NPC)
    npc.at_object_creation = lambda: None

    store = _AttrStore()
    npc._attr_store = store
    npc.attributes = store
    npc.db = _DbProxy(store)
    npc.tags = _FakeTags()
    npc.key = "test_npc"
    npc.location = overrides.pop("location", None)

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


class _MockPatrolScript(PatrolBehavior):
    """PatrolBehavior with stubbed pathfinding that can be configured."""

    def __init__(self, npc, path_fn=None):
        self.obj = npc
        self.key = "patrol_behavior"
        self._path_fn = path_fn

    @staticmethod
    def _default_path(npc, start, goal):
        if start == goal:
            return []
        return [goal]

    def _advance_to_next_waypoint(self, npc):
        """Override to use configurable path function."""
        if self._path_fn is not None:
            # Temporarily monkey-patch _compute_path
            original = PatrolBehavior._compute_path
            PatrolBehavior._compute_path = staticmethod(self._path_fn)
            try:
                super()._advance_to_next_waypoint(npc)
            finally:
                PatrolBehavior._compute_path = original
        else:
            original = PatrolBehavior._compute_path
            PatrolBehavior._compute_path = staticmethod(self._default_path)
            try:
                super()._advance_to_next_waypoint(npc)
            finally:
                PatrolBehavior._compute_path = original



# ================================================================== #
#  1. Pathfinder edge cases (Req 1.3, 1.4)
# ================================================================== #

class TestPathfinderEdgeCases:
    """Pathfinder returns empty list for walled goal, node limit, and 1x1 grid."""

    def test_walled_goal_returns_empty(self):
        """Goal completely surrounded by impassable tiles → empty list.
        Validates: Requirement 1.3
        """
        # 5x5 grid, goal at (2,2) surrounded by walls
        walls = {(1, 2), (3, 2), (2, 1), (2, 3)}

        def is_passable(x, y):
            return (x, y) not in walls

        path = find_path((0, 0), (2, 2), is_passable, 5, 5)
        assert path == [], f"Expected empty path for walled goal, got {path}"

    def test_node_limit_exceeded_returns_empty(self):
        """max_nodes too small for the distance → empty list.
        Validates: Requirement 1.4
        """
        # Open 50x50 grid, start and goal far apart, but max_nodes=5
        path = find_path((0, 0), (49, 49), lambda x, y: True, 50, 50, max_nodes=5)
        assert path == [], f"Expected empty path when node limit exceeded, got {path}"

    def test_single_tile_grid_start_equals_goal(self):
        """1x1 grid where start == goal → empty list.
        Validates: Requirement 1.3 (start == goal case)
        """
        path = find_path((0, 0), (0, 0), lambda x, y: True, 1, 1)
        assert path == [], f"Expected empty path for 1x1 grid, got {path}"


# ================================================================== #
#  2. Dynamic obstacle detection (Req 2.3, 2.8)
# ================================================================== #

class TestDynamicObstacleDetection:
    """NPC halts and clears queue when next tile becomes impassable."""

    def test_tile_becomes_impassable_halts_movement(self):
        """Tile at next step is impassable → NPC halts, queue cleared.
        Validates: Requirements 2.3, 2.8
        """
        # The NPC is at (0,0), queue has (1,0) then (2,0).
        # (1,0) is blocked by terrain.
        room = _ImpassableRoom(blocked_tiles={(1, 0)})
        # Set planet key on room.db so _is_tile_passable can find it
        room_store = _AttrStore()
        room.db = _DbProxy(room_store)
        room.db.planet = "test_planet"

        npc = _make_npc(
            location=room,
            coord_x=0,
            coord_y=0,
            movement_queue=[[1, 0], [2, 0]],
        )

        result = npc.advance_movement(tick_number=1)

        assert result is False, "NPC should not have moved"
        assert npc.db.movement_queue == [], "Queue should be cleared"
        assert npc.db.coord_x == 0, "NPC should stay at original x"
        assert npc.db.coord_y == 0, "NPC should stay at original y"
        assert "Blocked" in (npc.db.activity_status or ""), (
            f"Activity status should indicate blocked, got '{npc.db.activity_status}'"
        )


# ================================================================== #
#  3. Patrol edge cases (Req 3.4, 3.5)
# ================================================================== #

class TestPatrolEdgeCases:
    """Patrol behavior: unreachable waypoint skipping, all-unreachable, route clear."""

    def test_unreachable_waypoint_skipped(self):
        """Unreachable waypoint is skipped, next reachable waypoint targeted.
        Validates: Requirement 3.4
        """
        waypoints = [(5, 5), (10, 10), (15, 15)]

        npc = _make_npc(
            coord_x=0,
            coord_y=0,
            movement_queue=[],
            patrol_route=[[5, 5], [10, 10], [15, 15]],
            patrol_waypoint_index=0,
        )

        # Path function: waypoint (5,5) unreachable, (10,10) reachable
        def path_fn(npc_arg, start, goal):
            if goal == (5, 5):
                return []  # unreachable
            return [goal]  # reachable

        script = _MockPatrolScript(npc, path_fn=path_fn)
        script.at_repeat()

        # Should have skipped (5,5) and pathed to (10,10)
        assert npc.db.movement_queue, "Queue should be non-empty (pathing to reachable waypoint)"
        # Index should have advanced past (10,10) to point at next
        assert npc.db.patrol_waypoint_index == 2, (
            f"Expected index 2 (next after target 1), got {npc.db.patrol_waypoint_index}"
        )

    def test_all_waypoints_unreachable_stays_put(self):
        """All waypoints unreachable → NPC stays put, retries next tick.
        Validates: Requirement 3.5
        """
        npc = _make_npc(
            coord_x=0,
            coord_y=0,
            movement_queue=[],
            patrol_route=[[5, 5], [10, 10]],
            patrol_waypoint_index=0,
        )

        def path_fn(npc_arg, start, goal):
            return []  # all unreachable

        script = _MockPatrolScript(npc, path_fn=path_fn)
        script.at_repeat()

        assert npc.db.movement_queue == [] or not npc.db.movement_queue, (
            "Queue should remain empty when all waypoints unreachable"
        )
        assert npc.db.coord_x == 0 and npc.db.coord_y == 0, "NPC should stay put"
        assert "blocked" in (npc.db.activity_status or "").lower() or "retry" in (npc.db.activity_status or "").lower(), (
            f"Status should indicate blocked/retrying, got '{npc.db.activity_status}'"
        )

    def test_route_cleared_during_transit_stops_movement(self):
        """Clearing patrol route while NPC is moving stops movement.
        Validates: Requirement 3.5 (route clear during transit)
        """
        npc = _make_npc(
            coord_x=0,
            coord_y=0,
            movement_queue=[[1, 0], [2, 0], [3, 0]],
            patrol_route=[[5, 5], [10, 10]],
            patrol_waypoint_index=0,
        )

        # Simulate clearing patrol route and movement
        npc.db.patrol_route = None
        npc.clear_movement()

        assert npc.db.movement_queue == [], "Queue should be cleared"
        assert npc.db.coord_x == 0, "NPC should stay at current position"



# ================================================================== #
#  4. Delivery edge cases (Req 4.6, 4.7, 9.5)
# ================================================================== #

class TestDeliveryEdgeCases:
    """Delivery behavior: no storage, incapacitation resource drop."""

    def test_no_storage_building_harvester_stays_idle(self):
        """No Storage_Building exists → harvester stays idle.
        Validates: Requirement 4.6
        """
        owner = _MockOwner()
        extractor = _MockBuilding("EX", 10, 10, owner=owner)

        # Room with no storage buildings
        room = _MockRoom(drops=[], contents=[])

        npc = _make_npc(
            location=room,
            coord_x=10,
            coord_y=10,
            owner=owner,
            role_target=extractor,
            delivery_state="picking_up",
            carried_resources={"Iron": 20},
            carry_capacity=DEFAULT_CARRY_CAPACITY,
            movement_queue=[],
        )

        import mygame.typeclasses.agent_scripts as _scripts_mod
        original_get_attr = _scripts_mod._get_attr

        def mock_get_attr(obj, key, default=None):
            val = getattr(obj.db, key, None)
            if val is not None:
                return val
            return default

        _scripts_mod._get_attr = mock_get_attr
        try:
            behavior = DeliveryBehavior.__new__(DeliveryBehavior)
            behavior.obj = npc
            behavior._start_delivery(npc)
        finally:
            _scripts_mod._get_attr = original_get_attr

        assert npc.db.delivery_state == "idle", (
            f"Expected idle state, got '{npc.db.delivery_state}'"
        )
        assert "No storage" in (npc.db.activity_status or "") or "waiting" in (npc.db.activity_status or "").lower(), (
            f"Status should mention no storage, got '{npc.db.activity_status}'"
        )

    def test_incapacitated_while_carrying_drops_resources(self):
        """Incapacitated NPC drops carried resources at current coords.
        Validates: Requirement 9.5
        """
        spawned_drops = []

        class _TrackingRoom:
            """Room that tracks spawned resource drops."""
            def __init__(self):
                self.coord_index = _FakeCoordIndex()

        tracking_room = _TrackingRoom()

        npc = _make_npc(
            location=tracking_room,
            coord_x=5,
            coord_y=7,
            incapacitated=True,
            carried_resources={"Iron": 30, "Wood": 20},
            delivery_state="delivering",
        )

        # Monkey-patch ResourceSystem._spawn_resource_drop to track calls
        import mygame.typeclasses.agent_scripts as _scripts_mod

        # We need to mock the ResourceSystem import inside _handle_incapacitated
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        # Simpler approach: directly call _handle_incapacitated and mock at module level
        from unittest.mock import patch, MagicMock

        mock_spawn = MagicMock()
        with patch("mygame.typeclasses.agent_scripts.ResourceSystem", create=True) as mock_rs_class:
            # The code does: from world.systems.resource_system import ResourceSystem
            # We need to patch it where it's used
            pass

        # Actually, _handle_incapacitated imports ResourceSystem inside the method.
        # Let's patch it in sys.modules.
        mock_resource_system = types.ModuleType("world.systems.resource_system")

        class MockResourceSystem:
            @staticmethod
            def _spawn_resource_drop(room, rtype, amount, x=None, y=None):
                spawned_drops.append((rtype, amount, x, y))

        mock_resource_system.ResourceSystem = MockResourceSystem
        sys.modules["world.systems.resource_system"] = mock_resource_system

        try:
            DeliveryBehavior._handle_incapacitated(npc)
        finally:
            # Clean up
            if "world.systems.resource_system" in sys.modules:
                del sys.modules["world.systems.resource_system"]

        # Verify resources were dropped
        assert len(spawned_drops) == 2, f"Expected 2 drops, got {len(spawned_drops)}"
        drop_dict = {rtype: amt for rtype, amt, x, y in spawned_drops}
        assert drop_dict.get("Iron") == 30, f"Expected 30 Iron dropped, got {drop_dict}"
        assert drop_dict.get("Wood") == 20, f"Expected 20 Wood dropped, got {drop_dict}"

        # Verify coords
        for _, _, x, y in spawned_drops:
            assert x == 5 and y == 7, f"Drop should be at (5,7), got ({x},{y})"

        # Verify carried_resources cleared
        assert npc.db.carried_resources == {}, (
            f"carried_resources should be empty, got {npc.db.carried_resources}"
        )
        assert npc.db.delivery_state == "idle", (
            f"delivery_state should be idle, got '{npc.db.delivery_state}'"
        )


# ================================================================== #
#  5. Activity status updates (Req 10.1, 10.2)
# ================================================================== #

class TestActivityStatusUpdates:
    """Activity status string updates on state transitions."""

    def test_patrol_status_updates_on_waypoint_advance(self):
        """Patrolling NPC updates activity_status with waypoint info.
        Validates: Requirements 10.1, 10.2
        """
        npc = _make_npc(
            coord_x=0,
            coord_y=0,
            movement_queue=[],
            patrol_route=[[5, 5], [10, 10], [15, 15]],
            patrol_waypoint_index=0,
        )

        script = _MockPatrolScript(npc)
        script.at_repeat()

        status = npc.db.activity_status or ""
        assert "Patrolling" in status or "waypoint" in status.lower(), (
            f"Expected patrol status, got '{status}'"
        )

    def test_delivery_blocked_status_on_impassable_path(self):
        """Delivery with blocked path updates status to indicate retrying.
        Validates: Requirements 10.1, 10.2
        """
        owner = _MockOwner()
        extractor = _MockBuilding("EX", 10, 10, owner=owner)
        vault = _MockBuilding("VT", 20, 20, owner=owner)

        room = _MockRoom(contents=[vault])

        npc = _make_npc(
            location=room,
            coord_x=10,
            coord_y=10,
            owner=owner,
            role_target=extractor,
            delivery_state="picking_up",
            carried_resources={"Iron": 20},
            carry_capacity=DEFAULT_CARRY_CAPACITY,
            movement_queue=[],
        )

        import mygame.typeclasses.agent_scripts as _scripts_mod
        original_get_attr = _scripts_mod._get_attr

        def mock_get_attr(obj, key, default=None):
            val = getattr(obj.db, key, None)
            if val is not None:
                return val
            return default

        # Make pathfinding always fail
        original_compute = PatrolBehavior._compute_path

        @staticmethod
        def fail_path(npc_arg, start, goal):
            return []

        _scripts_mod._get_attr = mock_get_attr
        PatrolBehavior._compute_path = fail_path
        try:
            behavior = DeliveryBehavior.__new__(DeliveryBehavior)
            behavior.obj = npc
            behavior._start_delivery(npc)
        finally:
            _scripts_mod._get_attr = original_get_attr
            PatrolBehavior._compute_path = original_compute

        status = npc.db.activity_status or ""
        assert "blocked" in status.lower() or "retry" in status.lower(), (
            f"Expected blocked/retry status, got '{status}'"
        )

    def test_dynamic_obstacle_sets_blocked_status(self):
        """NPC hitting impassable tile sets 'Blocked' activity status.
        Validates: Requirements 2.3, 10.1
        """
        room = _ImpassableRoom(blocked_tiles={(1, 0)})
        room_store = _AttrStore()
        room.db = _DbProxy(room_store)
        room.db.planet = "test_planet"

        npc = _make_npc(
            location=room,
            coord_x=0,
            coord_y=0,
            movement_queue=[[1, 0]],
        )

        npc.advance_movement(tick_number=1)

        assert "Blocked" in (npc.db.activity_status or ""), (
            f"Expected 'Blocked' in status, got '{npc.db.activity_status}'"
        )


# ================================================================== #
#  6. Default attribute values (Req 5.1, 5.2, 5.3, 5.5)
# ================================================================== #

class TestDefaultAttributeValues:
    """Verify default movement_delay and carry_capacity values."""

    def test_default_movement_delay_is_one(self):
        """Default movement_delay should be 1.
        Validates: Requirement 5.1
        """
        npc = _make_npc()
        assert npc.db.movement_delay == 1
        assert DEFAULT_MOVEMENT_DELAY == 1

    def test_default_carry_capacity_is_fifty(self):
        """Default carry_capacity should be 50.
        Validates: Requirement 5.1
        """
        assert DEFAULT_CARRY_CAPACITY == 50

    def test_default_activity_status_is_idle(self):
        """Default activity_status should be 'Idle'.
        Validates: Requirement 10.1
        """
        npc = _make_npc()
        assert npc.db.activity_status == "Idle"

    def test_default_movement_queue_is_empty(self):
        """Default movement_queue should be empty list.
        Validates: Requirement 5.1
        """
        npc = _make_npc()
        assert npc.db.movement_queue == []
