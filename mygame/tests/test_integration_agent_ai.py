"""
Integration tests for Agent AI — Pathfinding & Autonomous Movement.

End-to-end tests covering full delivery loops, patrol cycling,
MovementSystem integration, server restart recovery, AgentSystem
path-based assignment, and pathfinding throttle.

Validates: Requirements 2.1, 3.2, 3.3, 4.1, 4.4, 5.4, 5.5, 6.3
"""

from mygame.conftest import _ensure_evennia_stubs
_ensure_evennia_stubs()

from mygame.world.pathfinding import find_path
from mygame.typeclasses.npcs import NPC
from mygame.typeclasses.agent_scripts import PatrolBehavior, DeliveryBehavior
from mygame.world.systems.movement_system import MovementSystem
from mygame.world.constants import (
    DEFAULT_CARRY_CAPACITY,
    HARVESTER_LADEN_DELAY,
    HARVESTER_EMPTY_DELAY,
)


# ------------------------------------------------------------------ #
#  Helpers (same mock patterns as test_agent_ai_unit.py)
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
        self.contents = []

    def move_entity(self, obj, new_x, new_y):
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y

    def get_objects_at(self, x, y, type_tag=None):
        return []


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
    npc.key = overrides.pop("key", "test_npc")
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
    """PatrolBehavior with stubbed pathfinding."""

    def __init__(self, npc, path_fn=None):
        self.obj = npc
        self.key = "patrol_behavior"
        self._path_fn = path_fn

    @staticmethod
    def _default_path(npc, start, goal):
        """Simple straight-line path for testing."""
        if start == goal:
            return []
        path = []
        cx, cy = start
        gx, gy = goal
        while cx != gx:
            cx += 1 if gx > cx else -1
            path.append((cx, cy))
        while cy != gy:
            cy += 1 if gy > cy else -1
            path.append((cx, cy))
        return path

    def _advance_to_next_waypoint(self, npc):
        original = PatrolBehavior._compute_path
        fn = self._path_fn if self._path_fn is not None else self._default_path
        PatrolBehavior._compute_path = staticmethod(fn)
        try:
            super()._advance_to_next_waypoint(npc)
        finally:
            PatrolBehavior._compute_path = original


# ================================================================== #
#  1. Full delivery loop (Req 4.1, 4.4)
# ================================================================== #

class TestFullDeliveryLoop:
    """Harvester cycles Extractor → Vault → Extractor over multiple ticks.

    Validates: Requirements 4.1, 4.4
    """

    def setup_method(self, method):
        """Register a DataRegistry singleton so storage capabilities resolve.

        Delivery target selection branches on the ``storage`` capability
        (resolved via the singleton) rather than a hardcoded building type.
        """
        from world.data_registry import DataRegistry
        from world.definitions import BuildingDef
        registry = DataRegistry()
        registry.buildings = {
            "VT": BuildingDef(
                name="Vault", abbreviation="VT", cost={"Stone": 25},
                max_health=400, requires_hq=True, required_terrain=None,
                category="storage", produces=None,
                capabilities=frozenset({"storage", "primary_storage"}),
            ),
            "EX": BuildingDef(
                name="Extractor", abbreviation="EX", cost={"Wood": 15},
                max_health=200, requires_hq=True, required_terrain=None,
                category="resource", produces=None,
                capabilities=frozenset({"harvestable"}),
            ),
        }
        DataRegistry.set_instance(registry)

    def teardown_method(self, method):
        from world.data_registry import DataRegistry
        DataRegistry.set_instance(None)

    def test_full_delivery_cycle(self):
        """Simulate: pick up → path to Vault → deposit → path back → arrive.

        Verifies resources transferred to owner and NPC returns to idle.
        """
        owner = _MockOwner()
        extractor = _MockBuilding("EX", 0, 0, owner=owner)
        vault = _MockBuilding("VT", 5, 0, owner=owner)

        drops = [_MockResourceDrop("Iron", 30)]

        class _DeliveryRoom(_FakeRoom):
            def __init__(self, drops_list, buildings):
                super().__init__()
                self._drops = list(drops_list)
                self.contents = list(buildings)

            def get_objects_at(self, x, y, type_tag=None):
                return list(self._drops)

        room = _DeliveryRoom(drops, [vault])

        npc = _make_npc(
            location=room,
            coord_x=0,
            coord_y=0,
            owner=owner,
            role_target=extractor,
            delivery_state="idle",
            carried_resources={},
            carry_capacity=DEFAULT_CARRY_CAPACITY,
        )

        behavior = DeliveryBehavior.__new__(DeliveryBehavior)
        behavior.obj = npc

        # --- Phase 1: idle → picking_up (pick up resources) ---
        behavior._try_pick_up(npc)
        assert npc.db.delivery_state == "picking_up"
        assert npc.db.carried_resources.get("Iron") == 30
        assert drops[0].db.amount == 0  # drop consumed

        # --- Phase 2: picking_up → delivering (path to Vault) ---
        # Mock _compute_path to return a simple path
        original_compute = PatrolBehavior._compute_path

        @staticmethod
        def mock_path(npc_arg, start, goal):
            path = []
            cx, cy = start
            gx, gy = goal
            while cx != gx:
                cx += 1 if gx > cx else -1
                path.append((cx, cy))
            while cy != gy:
                cy += 1 if gy > cy else -1
                path.append((cx, cy))
            return path

        PatrolBehavior._compute_path = mock_path
        try:
            behavior._start_delivery(npc)
        finally:
            PatrolBehavior._compute_path = original_compute

        assert npc.db.delivery_state == "delivering"
        assert npc.db.movement_delay == HARVESTER_LADEN_DELAY
        assert len(npc.db.movement_queue) == 5  # 5 steps from (0,0) to (5,0)

        # --- Phase 3: Simulate movement ticks to reach Vault ---
        # HARVESTER_LADEN_DELAY=2, so NPC moves only on even ticks (tick%2==0)
        tick = 0
        while npc.db.movement_queue:
            tick += 1
            npc.advance_movement(tick)
        assert npc.db.coord_x == 5
        assert npc.db.coord_y == 0
        assert npc.db.movement_queue == []

        # --- Phase 4: delivering → returning (deposit and path back) ---
        PatrolBehavior._compute_path = mock_path
        try:
            behavior._deposit_and_return(npc)
        finally:
            PatrolBehavior._compute_path = original_compute

        assert npc.db.delivery_state == "returning"
        assert npc.db.movement_delay == HARVESTER_EMPTY_DELAY
        assert npc.db.carried_resources == {}
        assert owner.resources.get("Iron") == 30  # deposited!
        assert len(npc.db.movement_queue) == 5  # 5 steps back

        # --- Phase 5: Simulate movement ticks to return to Extractor ---
        # HARVESTER_EMPTY_DELAY=1, so NPC moves every tick
        while npc.db.movement_queue:
            tick += 1
            npc.advance_movement(tick)
        assert npc.db.coord_x == 0
        assert npc.db.coord_y == 0
        assert npc.db.movement_queue == []

        # --- Phase 6: returning → idle ---
        behavior._arrived_at_extractor(npc)
        assert npc.db.delivery_state == "idle"
        assert npc.db.activity_status == "Idle"


# ================================================================== #
#  2. Full patrol loop (Req 3.2, 3.3)
# ================================================================== #

class TestFullPatrolLoop:
    """Guard cycles through 3 waypoints and wraps back to the first.

    Validates: Requirements 3.2, 3.3
    """

    def test_patrol_cycles_through_all_waypoints_and_wraps(self):
        """Create guard with 3 waypoints, simulate full cycle + wrap."""
        room = _FakeRoom()
        npc = _make_npc(
            location=room,
            coord_x=0,
            coord_y=0,
            patrol_route=[[3, 0], [3, 3], [0, 3]],
            patrol_waypoint_index=0,
        )

        script = _MockPatrolScript(npc)

        # --- Waypoint 0: path to (3,0) ---
        script.at_repeat()
        assert npc.db.patrol_waypoint_index == 1  # next target after (3,0)
        assert len(npc.db.movement_queue) > 0

        # Simulate movement to (3,0)
        while npc.db.movement_queue:
            npc.advance_movement(tick_number=1)
        assert npc.db.coord_x == 3
        assert npc.db.coord_y == 0

        # --- Waypoint 1: path to (3,3) ---
        script.at_repeat()
        assert npc.db.patrol_waypoint_index == 2
        while npc.db.movement_queue:
            npc.advance_movement(tick_number=1)
        assert npc.db.coord_x == 3
        assert npc.db.coord_y == 3

        # --- Waypoint 2: path to (0,3) ---
        script.at_repeat()
        assert npc.db.patrol_waypoint_index == 0  # wraps back!
        while npc.db.movement_queue:
            npc.advance_movement(tick_number=1)
        assert npc.db.coord_x == 0
        assert npc.db.coord_y == 3

        # --- Wrap: back to waypoint 0 (3,0) ---
        script.at_repeat()
        assert npc.db.patrol_waypoint_index == 1  # cycling again
        assert len(npc.db.movement_queue) > 0


# ================================================================== #
#  3. MovementSystem integration (Req 2.1, 5.4)
# ================================================================== #

class TestMovementSystemIntegration:
    """MovementSystem processes multiple NPCs over multiple ticks.

    Validates: Requirements 2.1, 5.4
    """

    def test_process_movement_advances_multiple_npcs(self):
        """Register NPCs, call process_movement, verify they advance."""
        room = _FakeRoom()
        ms = MovementSystem(max_paths_per_tick=10)
        ms._initialized = True  # skip lazy init

        npc1 = _make_npc(
            location=room,
            coord_x=0, coord_y=0,
            movement_queue=[[1, 0], [2, 0], [3, 0]],
        )
        npc2 = _make_npc(
            location=room,
            coord_x=10, coord_y=10,
            movement_queue=[[11, 10], [12, 10]],
        )

        ms.register_moving(npc1)
        ms.register_moving(npc2)

        # Tick 1
        ms.process_movement(tick_number=1)
        assert npc1.db.coord_x == 1
        assert npc2.db.coord_x == 11

        # Tick 2
        ms.process_movement(tick_number=2)
        assert npc1.db.coord_x == 2
        assert npc2.db.coord_x == 12
        # npc2 should be unregistered (queue empty)
        assert npc2 not in ms._moving_npcs

        # Tick 3
        ms.process_movement(tick_number=3)
        assert npc1.db.coord_x == 3
        assert npc1 not in ms._moving_npcs  # also done


# ================================================================== #
#  4. Server restart recovery (Req 5.4, 5.5)
# ================================================================== #

class TestServerRestartRecovery:
    """Persisted state resumes correctly after simulated restart.

    Validates: Requirements 5.4, 5.5
    """

    def test_ensure_initialized_rebuilds_moving_set(self):
        """Create MovementSystem, add NPCs, simulate restart, verify rebuild."""
        room = _FakeRoom()

        npc1 = _make_npc(
            location=room,
            coord_x=0, coord_y=0,
            movement_queue=[[1, 0], [2, 0]],
        )
        npc1.tags.add("npc", category="object_type")

        npc2 = _make_npc(
            location=room,
            coord_x=5, coord_y=5,
            movement_queue=[],  # empty queue — should NOT be in moving set
        )
        npc2.tags.add("npc", category="object_type")

        npc3 = _make_npc(
            location=room,
            coord_x=10, coord_y=10,
            movement_queue=[[11, 10]],
        )
        npc3.tags.add("npc", category="object_type")

        # Simulate restart: create a new MovementSystem
        import sys
        import types

        # Mock search_object_by_tag to return our NPCs
        mock_search_mod = types.ModuleType("evennia.utils.search")
        mock_search_mod.search_object_by_tag = lambda key, category=None: [npc1, npc2, npc3]
        sys.modules["evennia.utils.search"] = mock_search_mod

        try:
            ms = MovementSystem()
            # _ensure_initialized is called lazily on first process_movement
            ms._ensure_initialized()

            # npc1 and npc3 have non-empty queues → should be in moving set
            assert npc1 in ms._moving_npcs
            assert npc3 in ms._moving_npcs
            # npc2 has empty queue → should NOT be in moving set
            assert npc2 not in ms._moving_npcs
            assert ms._initialized is True
        finally:
            if "evennia.utils.search" in sys.modules:
                del sys.modules["evennia.utils.search"]

    def test_patrol_resumes_from_persisted_waypoint_index(self):
        """Patrol resumes from persisted patrol_waypoint_index after restart.

        Validates: Requirement 5.5
        """
        room = _FakeRoom()
        npc = _make_npc(
            location=room,
            coord_x=3, coord_y=0,
            patrol_route=[[3, 0], [3, 3], [0, 3]],
            patrol_waypoint_index=2,  # persisted: should target waypoint 2
            movement_queue=[],
        )

        script = _MockPatrolScript(npc)
        script.at_repeat()

        # Should path to waypoint index 2 = (0, 3)
        assert len(npc.db.movement_queue) > 0
        # After setting path, index advances to 0 (wrap)
        assert npc.db.patrol_waypoint_index == 0


# ================================================================== #
#  5. Pathfinding throttle (Req 6.3)
# ================================================================== #

class TestPathfindingThrottle:
    """Submit 15 requests with max_paths_per_tick=10, verify throttle.

    Validates: Requirement 6.3
    """

    def test_throttle_processes_only_max_per_tick(self):
        """15 requests submitted, only 10 processed, 5 deferred."""
        ms = MovementSystem(max_paths_per_tick=10)
        ms._initialized = True

        results = []

        for i in range(15):
            ms.request_path(
                npc=f"npc_{i}",
                start=(0, 0),
                goal=(1, 0),
                on_complete=lambda path, idx=i: results.append(idx),
            )

        assert len(ms._pending_requests) == 15

        # Process — should handle at most 10
        ms.process_pathfinding()

        assert len(results) == 10
        assert len(ms._pending_requests) == 5  # 5 deferred

        # Process again — remaining 5
        ms.reset_tick()
        ms.process_pathfinding()

        assert len(results) == 15
        assert len(ms._pending_requests) == 0

    def test_throttle_deferred_requests_preserve_order(self):
        """Deferred requests are processed in FIFO order on next tick."""
        ms = MovementSystem(max_paths_per_tick=3)
        ms._initialized = True

        order = []

        for i in range(7):
            ms.request_path(
                npc=f"npc_{i}",
                start=(0, 0),
                goal=(1, 0),
                on_complete=lambda path, idx=i: order.append(idx),
            )

        # Tick 1: process first 3
        ms.process_pathfinding()
        assert order == [0, 1, 2]
        assert len(ms._pending_requests) == 4

        # Tick 2: process next 3
        ms.reset_tick()
        ms.process_pathfinding()
        assert order == [0, 1, 2, 3, 4, 5]
        assert len(ms._pending_requests) == 1

        # Tick 3: process last 1
        ms.reset_tick()
        ms.process_pathfinding()
        assert order == [0, 1, 2, 3, 4, 5, 6]
        assert len(ms._pending_requests) == 0
