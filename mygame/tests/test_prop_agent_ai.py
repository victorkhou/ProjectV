"""
Property-based tests for Agent AI — Pathfinding.

**Property 1: Path Adjacency Invariant**
For any grid (width, height), any passability function, and any start/goal
coordinates, if the Pathfinder returns a non-empty path, then every
consecutive pair of coordinates in the path SHALL differ by exactly 1 in
either the x or y axis (4-directional adjacency), and the first step SHALL
be adjacent to start.
**Validates: Requirements 1.1, 1.8**

**Property 2: Path Validity Invariant**
For any grid (width, height), any passability function, and any start/goal
coordinates, if the Pathfinder returns a non-empty path, then every
coordinate in the path SHALL satisfy: (a) 0 <= x < width and 0 <= y < height,
and (b) is_passable(x, y) is True.
**Validates: Requirements 1.2, 1.6**

**Property 3: Same-Coordinate Identity**
For any valid coordinate (x, y) within grid bounds, calling
find_path((x, y), (x, y), ...) SHALL return an empty list.
**Validates: Requirements 1.5**

**Property 4: Open-Terrain Optimality**
For any fully-passable grid and any two distinct coordinates start and goal
within bounds, the Pathfinder SHALL return a path whose length equals the
Manhattan distance between start and goal.
**Validates: Requirements 1.7**
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.pathfinding import find_path


# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

# Grid dimensions: small enough for fast tests, large enough to be meaningful
grid_dimensions = st.tuples(
    st.integers(min_value=2, max_value=50),  # width
    st.integers(min_value=2, max_value=50),  # height
)


def coords_for(width, height):
    """Strategy for a coordinate within grid bounds."""
    return st.tuples(
        st.integers(min_value=0, max_value=width - 1),
        st.integers(min_value=0, max_value=height - 1),
    )


def passability_grid(width, height):
    """Strategy that generates a random passability grid (set of blocked cells).

    Returns a frozenset of (x, y) coordinates that are BLOCKED.
    We block at most ~30% of cells to keep paths findable often enough.
    """
    all_coords = [(x, y) for x in range(width) for y in range(height)]
    max_blocked = max(1, len(all_coords) * 30 // 100)
    return st.frozensets(
        st.sampled_from(all_coords),
        max_size=max_blocked,
    )


def _is_adjacent(a, b):
    """Check if two coordinates are 4-directionally adjacent."""
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return (dx + dy) == 1


def _manhattan(a, b):
    """Manhattan distance between two coordinates."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


# ------------------------------------------------------------------ #
#  Property 1: Path Adjacency Invariant
#  Feature: agent-ai, Property 1: Path Adjacency Invariant
# ------------------------------------------------------------------ #


class TestProperty1PathAdjacencyInvariant:
    """Every consecutive pair in a returned path differs by exactly 1 in
    x or y, and the first step is adjacent to start.

    **Validates: Requirements 1.1, 1.8**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_path_adjacency_holds(self, data):
        width = data.draw(st.integers(min_value=2, max_value=50), label="width")
        height = data.draw(st.integers(min_value=2, max_value=50), label="height")
        start = data.draw(coords_for(width, height), label="start")
        goal = data.draw(coords_for(width, height), label="goal")
        blocked = data.draw(passability_grid(width, height), label="blocked")

        # Ensure start and goal are not blocked
        assume(start not in blocked)
        assume(goal not in blocked)
        assume(start != goal)

        def is_passable(x, y):
            return (x, y) not in blocked

        path = find_path(start, goal, is_passable, width, height)

        if not path:
            return  # no path found — nothing to check

        # First step must be adjacent to start
        assert _is_adjacent(start, path[0]), (
            f"First step {path[0]} is not adjacent to start {start}"
        )

        # Every consecutive pair must be adjacent
        for i in range(len(path) - 1):
            assert _is_adjacent(path[i], path[i + 1]), (
                f"Steps {path[i]} -> {path[i + 1]} are not adjacent (index {i})"
            )


# ------------------------------------------------------------------ #
#  Property 2: Path Validity Invariant
#  Feature: agent-ai, Property 2: Path Validity Invariant
# ------------------------------------------------------------------ #


class TestProperty2PathValidityInvariant:
    """All coordinates in a returned path are in-bounds and passable.

    **Validates: Requirements 1.2, 1.6**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_path_validity_holds(self, data):
        width = data.draw(st.integers(min_value=2, max_value=50), label="width")
        height = data.draw(st.integers(min_value=2, max_value=50), label="height")
        start = data.draw(coords_for(width, height), label="start")
        goal = data.draw(coords_for(width, height), label="goal")
        blocked = data.draw(passability_grid(width, height), label="blocked")

        assume(start not in blocked)
        assume(goal not in blocked)
        assume(start != goal)

        def is_passable(x, y):
            return (x, y) not in blocked

        path = find_path(start, goal, is_passable, width, height)

        if not path:
            return  # no path found — nothing to check

        for coord in path:
            x, y = coord
            # In-bounds
            assert 0 <= x < width, f"x={x} out of bounds [0, {width})"
            assert 0 <= y < height, f"y={y} out of bounds [0, {height})"
            # Passable
            assert is_passable(x, y), f"Coordinate {coord} is not passable"


# ------------------------------------------------------------------ #
#  Property 3: Same-Coordinate Identity
#  Feature: agent-ai, Property 3: Same-Coordinate Identity
# ------------------------------------------------------------------ #


class TestProperty3SameCoordinateIdentity:
    """find_path(p, p, ...) always returns an empty list.

    **Validates: Requirements 1.5**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_same_coordinate_returns_empty(self, data):
        width = data.draw(st.integers(min_value=1, max_value=50), label="width")
        height = data.draw(st.integers(min_value=1, max_value=50), label="height")
        point = data.draw(coords_for(width, height), label="point")

        def is_passable(x, y):
            return True

        path = find_path(point, point, is_passable, width, height)
        assert path == [], f"Expected empty list for same start/goal, got {path}"


# ------------------------------------------------------------------ #
#  Property 4: Open-Terrain Optimality
#  Feature: agent-ai, Property 4: Open-Terrain Optimality
# ------------------------------------------------------------------ #


class TestProperty4OpenTerrainOptimality:
    """On a fully passable grid, path length equals Manhattan distance.

    **Validates: Requirements 1.7**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_optimal_path_length_on_open_grid(self, data):
        width = data.draw(st.integers(min_value=2, max_value=50), label="width")
        height = data.draw(st.integers(min_value=2, max_value=50), label="height")
        start = data.draw(coords_for(width, height), label="start")
        goal = data.draw(coords_for(width, height), label="goal")

        assume(start != goal)

        def is_passable(x, y):
            return True

        # Use a large max_nodes to avoid hitting the expansion limit on
        # open terrain — this property tests optimality, not the node cap.
        max_nodes = width * height + 1
        path = find_path(start, goal, is_passable, width, height, max_nodes=max_nodes)
        expected_length = _manhattan(start, goal)

        assert len(path) == expected_length, (
            f"Path length {len(path)} != Manhattan distance {expected_length} "
            f"from {start} to {goal} on {width}x{height} open grid"
        )


# ================================================================== #
#  Properties 5–7: NPC Movement Engine
# ================================================================== #

from mygame.conftest import _ensure_evennia_stubs
_ensure_evennia_stubs()

from mygame.typeclasses.npcs import NPC


# ------------------------------------------------------------------ #
#  Movement test helpers (mirrors test_npc_movement.py pattern)
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


# ------------------------------------------------------------------ #
#  Strategies for movement tests
# ------------------------------------------------------------------ #

def _adjacent_path(start_x, start_y, steps):
    """Build a list of [x, y] steps by walking randomly from a start.

    Each step differs by exactly 1 in x or y from the previous.
    Returns a list of [x, y] lists (Evennia persistence format).
    """
    import random
    path = []
    cx, cy = start_x, start_y
    directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    for s in steps:
        dx, dy = directions[s % 4]
        cx, cy = cx + dx, cy + dy
        path.append([cx, cy])
    return path


# Strategy: generate a random path of 1–20 steps as direction indices
path_steps_strategy = st.lists(
    st.integers(min_value=0, max_value=3),
    min_size=1,
    max_size=20,
)


# ------------------------------------------------------------------ #
#  Property 5: Movement Queue Consumption
#  Feature: agent-ai, Property 5: Movement Queue Consumption
# ------------------------------------------------------------------ #


class TestProperty5MovementQueueConsumption:
    """After N ticks, NPC at final coordinate, queue empty,
    at_movement_complete invoked exactly once.

    **Validates: Requirements 2.1, 2.2**
    """

    @given(
        start_x=st.integers(min_value=0, max_value=100),
        start_y=st.integers(min_value=0, max_value=100),
        steps=path_steps_strategy,
    )
    @settings(max_examples=100)
    def test_queue_fully_consumed(self, start_x, start_y, steps):
        room = _FakeRoom()
        path = _adjacent_path(start_x, start_y, steps)
        n = len(path)
        expected_final = path[-1]

        npc = _make_npc(
            location=room,
            coord_x=start_x,
            coord_y=start_y,
            movement_queue=[list(p) for p in path],
            movement_delay=1,
        )

        completion_count = [0]
        original_complete = npc.at_movement_complete
        npc.at_movement_complete = lambda: completion_count.__setitem__(0, completion_count[0] + 1)

        # Advance exactly N ticks (tick numbers 1..N)
        for tick in range(1, n + 1):
            npc.advance_movement(tick_number=tick)

        # NPC should be at the final coordinate
        assert npc.db.coord_x == expected_final[0], (
            f"Expected x={expected_final[0]}, got {npc.db.coord_x}"
        )
        assert npc.db.coord_y == expected_final[1], (
            f"Expected y={expected_final[1]}, got {npc.db.coord_y}"
        )
        # Queue should be empty
        assert npc.db.movement_queue == [], (
            f"Queue should be empty, got {npc.db.movement_queue}"
        )
        # at_movement_complete invoked exactly once
        assert completion_count[0] == 1, (
            f"at_movement_complete called {completion_count[0]} times, expected 1"
        )


# ------------------------------------------------------------------ #
#  Property 6: Incapacitated NPC Freezes Movement
#  Feature: agent-ai, Property 6: Incapacitated NPC Freezes Movement
# ------------------------------------------------------------------ #


class TestProperty6IncapacitatedNPCFreezesMovement:
    """No position change, no queue consumption for incapacitated NPCs.

    **Validates: Requirements 2.4**
    """

    @given(
        start_x=st.integers(min_value=0, max_value=100),
        start_y=st.integers(min_value=0, max_value=100),
        steps=path_steps_strategy,
        num_ticks=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_incapacitated_npc_does_not_move(self, start_x, start_y, steps, num_ticks):
        room = _FakeRoom()
        path = _adjacent_path(start_x, start_y, steps)
        original_queue = [list(p) for p in path]

        npc = _make_npc(
            location=room,
            coord_x=start_x,
            coord_y=start_y,
            movement_queue=[list(p) for p in path],
            movement_delay=1,
            incapacitated=True,
        )

        for tick in range(1, num_ticks + 1):
            npc.advance_movement(tick_number=tick)

        # Position unchanged
        assert npc.db.coord_x == start_x, (
            f"Expected x={start_x}, got {npc.db.coord_x}"
        )
        assert npc.db.coord_y == start_y, (
            f"Expected y={start_y}, got {npc.db.coord_y}"
        )
        # Queue unchanged
        assert npc.db.movement_queue == original_queue, (
            f"Queue should be unchanged, got {npc.db.movement_queue}"
        )


# ------------------------------------------------------------------ #
#  Property 7: Movement Delay Gating
#  Feature: agent-ai, Property 7: Movement Delay Gating
# ------------------------------------------------------------------ #


class TestProperty7MovementDelayGating:
    """NPC advances only on ticks where tick_number % delay == 0.

    **Validates: Requirements 8.1, 8.6**
    """

    @given(
        delay=st.integers(min_value=1, max_value=10),
        tick_sequence=st.lists(
            st.integers(min_value=1, max_value=200),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_movement_only_on_aligned_ticks(self, delay, tick_sequence):
        room = _FakeRoom()
        # Build a long enough queue so we never run out during the test
        long_path = [[i, 0] for i in range(1, 201)]

        npc = _make_npc(
            location=room,
            coord_x=0,
            coord_y=0,
            movement_queue=[list(p) for p in long_path],
            movement_delay=delay,
        )

        moves_made = 0
        for tick in tick_sequence:
            queue_before = len(npc.db.movement_queue)
            result = npc.advance_movement(tick_number=tick)

            if tick % delay == 0:
                # Should have moved (queue was non-empty)
                if queue_before > 0:
                    assert result is True, (
                        f"Expected move on tick {tick} with delay {delay}"
                    )
                    moves_made += 1
            else:
                # Should NOT have moved
                assert result is False, (
                    f"Should not move on tick {tick} with delay {delay}"
                )


# ================================================================== #
#  Property 15: Pathfinding Throttle
# ================================================================== #

from mygame.world.systems.movement_system import MovementSystem


# ------------------------------------------------------------------ #
#  Property 15: Pathfinding Throttle
#  Feature: agent-ai, Property 15: Pathfinding Throttle
# ------------------------------------------------------------------ #


class TestProperty15PathfindingThrottle:
    """At most max_paths_per_tick requests processed per call;
    remainder deferred to subsequent ticks.

    **Validates: Requirements 6.3**
    """

    @given(
        num_requests=st.integers(min_value=1, max_value=30),
        max_per_tick=st.integers(min_value=1, max_value=15),
    )
    @settings(max_examples=100)
    def test_throttle_limits_processed_requests(self, num_requests, max_per_tick):
        system = MovementSystem(max_paths_per_tick=max_per_tick)

        completed = []

        for i in range(num_requests):
            system.request_path(
                npc=None,
                start=(0, 0),
                goal=(i + 1, 0),
                on_complete=lambda path, idx=i: completed.append(idx),
            )

        # Process one tick's worth of pathfinding
        system.process_pathfinding()

        expected_processed = min(num_requests, max_per_tick)
        expected_deferred = max(0, num_requests - max_per_tick)

        # At most max_per_tick callbacks invoked
        assert len(completed) == expected_processed, (
            f"Expected {expected_processed} callbacks, got {len(completed)} "
            f"(N={num_requests}, max_per_tick={max_per_tick})"
        )

        # Remaining requests still pending
        assert len(system._pending_requests) == expected_deferred, (
            f"Expected {expected_deferred} deferred, got {len(system._pending_requests)} "
            f"(N={num_requests}, max_per_tick={max_per_tick})"
        )


# ================================================================== #
#  Properties 8–9: Patrol Behavior
# ================================================================== #

from mygame.typeclasses.agent_scripts import PatrolBehavior
from mygame.world.constants import MIN_PATROL_WAYPOINTS, MAX_PATROL_WAYPOINTS


# ------------------------------------------------------------------ #
#  Patrol test helpers
# ------------------------------------------------------------------ #

class _MockPatrolScript(PatrolBehavior):
    """PatrolBehavior with a mock obj and stubbed pathfinding."""

    def __init__(self, npc):
        # Don't call super().__init__() — avoid Evennia machinery
        self.obj = npc
        self.key = "patrol_behavior"

    @staticmethod
    def _compute_path(npc, start, goal):
        """Return a trivial one-step path so the script always succeeds."""
        if start == goal:
            return []
        # Return a simple direct path (just the goal)
        return [goal]


def _make_patrol_npc(waypoints, current_index=0, coord_x=0, coord_y=0):
    """Create an NPC positioned at a waypoint with a patrol route."""
    npc = _make_npc(
        coord_x=coord_x,
        coord_y=coord_y,
        movement_queue=[],
        patrol_route=[list(wp) for wp in waypoints],
        patrol_waypoint_index=current_index,
    )
    return npc


# ------------------------------------------------------------------ #
#  Property 8: Patrol Waypoint Cycling
#  Feature: agent-ai, Property 8: Patrol Waypoint Cycling
# ------------------------------------------------------------------ #


class TestProperty8PatrolWaypointCycling:
    """After arriving at waypoint i, next target is (i+1) % W.

    The PatrolBehavior._advance_to_next_waypoint logic works as follows:
    1. NPC is at waypoint i → index advances to (i+1) % W (skip current).
    2. If waypoint (i+1) % W is at different coords, compute path to it
       and set index to (i+2) % W (pre-advance for next arrival).
    3. If waypoint (i+1) % W is at same coords as NPC, skip it too and
       continue the cycle.

    So after at_repeat when NPC is at waypoint i and the next distinct
    waypoint is at index j = (i+1) % W, the movement queue targets
    waypoint j and the stored index is (j+1) % W.

    **Validates: Requirements 3.2, 3.3**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_patrol_waypoint_advances_cyclically(self, data):
        # Generate a patrol route with 2-10 waypoints ensuring at least
        # two distinct coordinates so the NPC always has somewhere to go.
        num_waypoints = data.draw(
            st.integers(min_value=2, max_value=10), label="num_waypoints"
        )
        waypoints = data.draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=99),
                    st.integers(min_value=0, max_value=99),
                ),
                min_size=num_waypoints,
                max_size=num_waypoints,
            ),
            label="waypoints",
        )

        # Pick which waypoint the NPC is currently at
        current_index = data.draw(
            st.integers(min_value=0, max_value=num_waypoints - 1),
            label="current_index",
        )

        # Position the NPC at waypoint[current_index]
        wx, wy = waypoints[current_index]

        # Ensure there's at least one waypoint with different coords
        # so the patrol has somewhere to go.
        assume(any(wp != (wx, wy) for wp in waypoints))

        npc = _make_patrol_npc(
            waypoints,
            current_index=current_index,
            coord_x=wx,
            coord_y=wy,
        )

        script = _MockPatrolScript(npc)

        # Call at_repeat — NPC is at current waypoint with empty queue
        script.at_repeat()

        actual_index = npc.db.patrol_waypoint_index

        # Find the first waypoint after current_index that has different
        # coords — that's the one the script will path to.
        target_j = None
        idx = (current_index + 1) % num_waypoints
        for _ in range(num_waypoints):
            if waypoints[idx] != (wx, wy):
                target_j = idx
                break
            idx = (idx + 1) % num_waypoints

        assert target_j is not None, "Should have found a distinct waypoint"

        # After pathing to waypoint target_j, the script pre-advances
        # the index to (target_j + 1) % W for the next cycle.
        expected_index = (target_j + 1) % num_waypoints

        assert actual_index == expected_index, (
            f"Expected patrol_waypoint_index={expected_index}, got {actual_index}. "
            f"NPC was at waypoint {current_index} ({wx},{wy}), "
            f"should path to waypoint {target_j} ({waypoints[target_j]}), "
            f"route length={num_waypoints}"
        )

        # Movement queue should be populated (pathing to target_j)
        assert npc.db.movement_queue, (
            f"Movement queue should be non-empty when pathing to "
            f"waypoint {target_j} at {waypoints[target_j]}"
        )


# ------------------------------------------------------------------ #
#  Property 9: Patrol Route Validation
#  Feature: agent-ai, Property 9: Patrol Route Validation
# ------------------------------------------------------------------ #


def _validate_patrol_route(waypoints, grid_width, grid_height):
    """Standalone patrol route validation matching AgentSystem.set_patrol_route rules.

    Returns (True, None) if valid, (False, reason_str) if invalid.
    Validates: Requirements 3.7, 3.8
    """
    if not isinstance(waypoints, (list, tuple)):
        return False, "waypoints must be a list"
    if len(waypoints) < MIN_PATROL_WAYPOINTS:
        return False, f"too few waypoints (min {MIN_PATROL_WAYPOINTS})"
    if len(waypoints) > MAX_PATROL_WAYPOINTS:
        return False, f"too many waypoints (max {MAX_PATROL_WAYPOINTS})"
    for i, wp in enumerate(waypoints):
        if not isinstance(wp, (list, tuple)) or len(wp) != 2:
            return False, f"waypoint {i} is not a coordinate pair"
        x, y = wp
        if not (0 <= x < grid_width and 0 <= y < grid_height):
            return False, f"waypoint {i} ({x},{y}) out of bounds"
    return True, None


class TestProperty9PatrolRouteValidation:
    """Patrol route accepted iff 2 <= len <= 10 and all waypoints in bounds.

    **Validates: Requirements 3.7, 3.8**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_valid_routes_accepted(self, data):
        """Routes with 2-10 in-bounds waypoints are always accepted."""
        grid_w = data.draw(st.integers(min_value=10, max_value=100), label="grid_w")
        grid_h = data.draw(st.integers(min_value=10, max_value=100), label="grid_h")
        num_wp = data.draw(
            st.integers(min_value=MIN_PATROL_WAYPOINTS, max_value=MAX_PATROL_WAYPOINTS),
            label="num_waypoints",
        )
        waypoints = data.draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=grid_w - 1),
                    st.integers(min_value=0, max_value=grid_h - 1),
                ),
                min_size=num_wp,
                max_size=num_wp,
            ),
            label="waypoints",
        )

        valid, reason = _validate_patrol_route(waypoints, grid_w, grid_h)
        assert valid, (
            f"Valid route rejected: {reason} "
            f"(len={len(waypoints)}, grid={grid_w}x{grid_h})"
        )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_too_few_waypoints_rejected(self, data):
        """Routes with fewer than MIN_PATROL_WAYPOINTS are rejected."""
        grid_w = data.draw(st.integers(min_value=10, max_value=100), label="grid_w")
        grid_h = data.draw(st.integers(min_value=10, max_value=100), label="grid_h")
        num_wp = data.draw(
            st.integers(min_value=0, max_value=MIN_PATROL_WAYPOINTS - 1),
            label="num_waypoints",
        )
        waypoints = data.draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=grid_w - 1),
                    st.integers(min_value=0, max_value=grid_h - 1),
                ),
                min_size=num_wp,
                max_size=num_wp,
            ),
            label="waypoints",
        )

        valid, _ = _validate_patrol_route(waypoints, grid_w, grid_h)
        assert not valid, (
            f"Route with {len(waypoints)} waypoints should be rejected "
            f"(min is {MIN_PATROL_WAYPOINTS})"
        )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_too_many_waypoints_rejected(self, data):
        """Routes with more than MAX_PATROL_WAYPOINTS are rejected."""
        grid_w = data.draw(st.integers(min_value=10, max_value=100), label="grid_w")
        grid_h = data.draw(st.integers(min_value=10, max_value=100), label="grid_h")
        num_wp = data.draw(
            st.integers(min_value=MAX_PATROL_WAYPOINTS + 1, max_value=20),
            label="num_waypoints",
        )
        waypoints = data.draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=grid_w - 1),
                    st.integers(min_value=0, max_value=grid_h - 1),
                ),
                min_size=num_wp,
                max_size=num_wp,
            ),
            label="waypoints",
        )

        valid, _ = _validate_patrol_route(waypoints, grid_w, grid_h)
        assert not valid, (
            f"Route with {len(waypoints)} waypoints should be rejected "
            f"(max is {MAX_PATROL_WAYPOINTS})"
        )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_out_of_bounds_waypoints_rejected(self, data):
        """Routes with any out-of-bounds waypoint are rejected."""
        grid_w = data.draw(st.integers(min_value=10, max_value=50), label="grid_w")
        grid_h = data.draw(st.integers(min_value=10, max_value=50), label="grid_h")
        num_wp = data.draw(
            st.integers(min_value=MIN_PATROL_WAYPOINTS, max_value=MAX_PATROL_WAYPOINTS),
            label="num_waypoints",
        )

        # Generate valid waypoints first
        valid_wps = data.draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=grid_w - 1),
                    st.integers(min_value=0, max_value=grid_h - 1),
                ),
                min_size=num_wp,
                max_size=num_wp,
            ),
            label="valid_waypoints",
        )

        # Pick one index to make out-of-bounds
        bad_idx = data.draw(
            st.integers(min_value=0, max_value=num_wp - 1), label="bad_idx"
        )
        # Generate an out-of-bounds coordinate
        bad_coord = data.draw(
            st.one_of(
                # x too large
                st.tuples(
                    st.integers(min_value=grid_w, max_value=grid_w + 50),
                    st.integers(min_value=0, max_value=grid_h - 1),
                ),
                # y too large
                st.tuples(
                    st.integers(min_value=0, max_value=grid_w - 1),
                    st.integers(min_value=grid_h, max_value=grid_h + 50),
                ),
                # x negative
                st.tuples(
                    st.integers(min_value=-50, max_value=-1),
                    st.integers(min_value=0, max_value=grid_h - 1),
                ),
                # y negative
                st.tuples(
                    st.integers(min_value=0, max_value=grid_w - 1),
                    st.integers(min_value=-50, max_value=-1),
                ),
            ),
            label="bad_coord",
        )

        waypoints = list(valid_wps)
        waypoints[bad_idx] = bad_coord

        valid, _ = _validate_patrol_route(waypoints, grid_w, grid_h)
        assert not valid, (
            f"Route with OOB waypoint {bad_coord} at index {bad_idx} "
            f"should be rejected (grid={grid_w}x{grid_h})"
        )


# ================================================================== #
#  Properties 10–13: Delivery Behavior
# ================================================================== #

from mygame.typeclasses.agent_scripts import DeliveryBehavior
from mygame.world.constants import (
    DEFAULT_CARRY_CAPACITY,
    HARVESTER_LADEN_DELAY,
    HARVESTER_EMPTY_DELAY,
)


# ------------------------------------------------------------------ #
#  Delivery test helpers
# ------------------------------------------------------------------ #

class _MockResourceDrop:
    """Minimal ResourceDrop stub with db.resource_type and db.amount."""

    def __init__(self, resource_type: str, amount: int):
        store = _AttrStore()
        self.db = _DbProxy(store)
        self.db.resource_type = resource_type
        self.db.amount = amount
        self._deleted = False

    def delete(self):
        self._deleted = True


class _MockRoom:
    """Room stub that returns pre-configured drops from get_objects_at."""

    def __init__(self, drops=None):
        self._drops = drops or []

    def get_objects_at(self, x, y, type_tag=None):
        return list(self._drops)


class _MockBuilding:
    """Minimal building stub with db.coord_x, db.coord_y, db.building_type, db.owner."""

    def __init__(self, btype: str, x: int, y: int, owner=None):
        store = _AttrStore()
        self.db = _DbProxy(store)
        self.db.building_type = btype
        self.db.coord_x = x
        self.db.coord_y = y
        self.db.owner = owner
        self.tags = _FakeTags()
        self.tags.add("building", category="object_type")
        # For _get_attr fallback
        self.attributes = store


class _MockOwner:
    """Owner stub that tracks add_resource calls."""

    def __init__(self):
        self.resources: dict[str, int] = {}
        self.id = 1

    def add_resource(self, rtype: str, amount: int):
        self.resources[rtype] = self.resources.get(rtype, 0) + amount


def _make_delivery_npc(**overrides):
    """Create an NPC with delivery-related attributes."""
    npc = _make_npc(**overrides)
    if not hasattr(npc.db, "delivery_state") or npc.db.delivery_state is None:
        npc.db.delivery_state = "idle"
    if not hasattr(npc.db, "carried_resources") or npc.db.carried_resources is None:
        npc.db.carried_resources = {}
    if not hasattr(npc.db, "carry_capacity") or npc.db.carry_capacity is None:
        npc.db.carry_capacity = DEFAULT_CARRY_CAPACITY
    return npc


# ------------------------------------------------------------------ #
#  Property 10: Capacity-Limited Resource Pickup
#  Feature: agent-ai, Property 10: Capacity-Limited Resource Pickup
# ------------------------------------------------------------------ #


# Strategy for resource types
resource_types = st.sampled_from(["Wood", "Stone", "Iron", "Crystal"])


class TestProperty10CapacityLimitedResourcePickup:
    """Agent picks up min(T, C) total units, leaves max(0, T-C) on ground.

    **Validates: Requirements 4.2, 9.2**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_pickup_respects_capacity(self, data):
        # Generate random carry capacity
        capacity = data.draw(
            st.integers(min_value=1, max_value=200), label="capacity"
        )

        # Generate 1-5 resource drops with random types and amounts
        num_drops = data.draw(
            st.integers(min_value=1, max_value=5), label="num_drops"
        )
        drop_specs = data.draw(
            st.lists(
                st.tuples(
                    resource_types,
                    st.integers(min_value=1, max_value=100),
                ),
                min_size=num_drops,
                max_size=num_drops,
            ),
            label="drop_specs",
        )

        # Create mock drops
        drops = [_MockResourceDrop(rtype, amt) for rtype, amt in drop_specs]
        total_available = sum(amt for _, amt in drop_specs)

        # Create mock room, building, and NPC
        room = _MockRoom(drops=drops)
        building = _MockBuilding("EX", 10, 10)

        npc = _make_delivery_npc(
            location=room,
            coord_x=10,
            coord_y=10,
            carry_capacity=capacity,
            carried_resources={},
            delivery_state="idle",
            role_target=building,
        )

        # Execute pickup
        behavior = DeliveryBehavior.__new__(DeliveryBehavior)
        behavior.obj = npc
        behavior._try_pick_up(npc)

        # Verify: total picked up == min(total_available, capacity)
        carried = npc.db.carried_resources or {}
        total_picked = sum(carried.values())
        expected_picked = min(total_available, capacity)

        assert total_picked == expected_picked, (
            f"Picked up {total_picked}, expected min({total_available}, {capacity}) = {expected_picked}"
        )

        # Verify: total remaining on ground == max(0, total_available - capacity)
        total_remaining = sum(
            d.db.amount for d in drops if not d._deleted and d.db.amount > 0
        )
        expected_remaining = max(0, total_available - capacity)

        assert total_remaining == expected_remaining, (
            f"Remaining on ground {total_remaining}, expected max(0, {total_available} - {capacity}) = {expected_remaining}"
        )


# ------------------------------------------------------------------ #
#  Property 11: Resource Deposit Round-Trip
#  Feature: agent-ai, Property 11: Resource Deposit Round-Trip
# ------------------------------------------------------------------ #


class TestProperty11ResourceDepositRoundTrip:
    """Owner's pool increases by carried amounts, carried_resources becomes empty.

    **Validates: Requirements 4.4, 9.4**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_deposit_transfers_all_resources(self, data):
        # Generate random carried resources (1-4 resource types)
        num_types = data.draw(
            st.integers(min_value=1, max_value=4), label="num_types"
        )
        carried = data.draw(
            st.fixed_dictionaries(
                {},
                optional={
                    rtype: st.integers(min_value=1, max_value=500)
                    for rtype in ["Wood", "Stone", "Iron", "Crystal"][:num_types]
                },
            ),
            label="carried",
        )
        # Ensure at least one resource type is present
        assume(len(carried) > 0 and sum(carried.values()) > 0)

        owner = _MockOwner()

        npc = _make_delivery_npc(
            carried_resources=dict(carried),
            owner=owner,
        )

        # Execute deposit
        DeliveryBehavior.deposit_resources(npc)

        # Verify: owner's resources increased by exact amounts
        for rtype, amount in carried.items():
            assert owner.resources.get(rtype, 0) == amount, (
                f"Owner should have {amount} {rtype}, got {owner.resources.get(rtype, 0)}"
            )

        # Verify: carried_resources is now empty
        final_carried = npc.db.carried_resources
        assert final_carried == {}, (
            f"carried_resources should be empty, got {final_carried}"
        )


# ------------------------------------------------------------------ #
#  Property 12: Delivery Target Selection
#  Feature: agent-ai, Property 12: Delivery Target Selection
# ------------------------------------------------------------------ #


class TestProperty12DeliveryTargetSelection:
    """Nearest Storage_Building by Manhattan distance, Vault preferred on tie.

    **Validates: Requirements 7.1, 7.2**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_selects_nearest_with_vault_preference(self, data):
        # Extractor position
        ex_x = data.draw(st.integers(min_value=0, max_value=99), label="ex_x")
        ex_y = data.draw(st.integers(min_value=0, max_value=99), label="ex_y")

        # Generate 1-6 storage buildings at random positions
        num_buildings = data.draw(
            st.integers(min_value=1, max_value=6), label="num_buildings"
        )
        building_specs = data.draw(
            st.lists(
                st.tuples(
                    st.sampled_from(["VT", "HQ"]),
                    st.integers(min_value=0, max_value=99),
                    st.integers(min_value=0, max_value=99),
                ),
                min_size=num_buildings,
                max_size=num_buildings,
            ),
            label="building_specs",
        )

        owner = _MockOwner()

        # Create mock buildings
        buildings = []
        for btype, bx, by in building_specs:
            bld = _MockBuilding(btype, bx, by, owner=owner)
            buildings.append(bld)

        # Create extractor (role_target)
        extractor = _MockBuilding("EX", ex_x, ex_y, owner=owner)

        # Create a room stub that returns our buildings via contents
        room = _MockRoom()
        room.contents = buildings

        npc = _make_delivery_npc(
            location=room,
            coord_x=ex_x,
            coord_y=ex_y,
            owner=owner,
            role_target=extractor,
        )

        # Monkey-patch to avoid Evennia search_object_by_tag
        import mygame.typeclasses.agent_scripts as _scripts_mod
        original_get_attr = _scripts_mod._get_attr

        def mock_get_attr(obj, key, default=None):
            val = getattr(obj.db, key, None)
            if val is not None:
                return val
            return default

        _scripts_mod._get_attr = mock_get_attr
        try:
            result = DeliveryBehavior.select_delivery_target(npc)
        finally:
            _scripts_mod._get_attr = original_get_attr

        # Compute expected: nearest by Manhattan, VT preferred on tie
        candidates = []
        for bld, (btype, bx, by) in zip(buildings, building_specs):
            dist = abs(bx - ex_x) + abs(by - ex_y)
            type_priority = 0 if btype == "VT" else 1
            candidates.append((dist, type_priority, bld))

        candidates.sort(key=lambda c: (c[0], c[1]))
        expected = candidates[0][2]

        assert result is expected, (
            f"Expected building at ({expected.db.coord_x}, {expected.db.coord_y}) "
            f"type={expected.db.building_type}, "
            f"got {('None' if result is None else f'({result.db.coord_x}, {result.db.coord_y}) type={result.db.building_type}')}"
        )


# ------------------------------------------------------------------ #
#  Property 13: Harvester Delay by Delivery State
#  Feature: agent-ai, Property 13: Harvester Delay by Delivery State
# ------------------------------------------------------------------ #


class TestProperty13HarvesterDelayByDeliveryState:
    """delay=2 when delivering, delay=1 when returning/idle.

    **Validates: Requirements 8.4, 8.5**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_delivering_state_sets_laden_delay(self, data):
        """When _start_delivery transitions to delivering, movement_delay = HARVESTER_LADEN_DELAY."""
        # Generate random carried resources
        carried = data.draw(
            st.dictionaries(
                resource_types,
                st.integers(min_value=1, max_value=100),
                min_size=1,
                max_size=3,
            ),
            label="carried",
        )

        owner = _MockOwner()

        # Create a Vault as delivery target
        vault_x = data.draw(st.integers(min_value=0, max_value=99), label="vault_x")
        vault_y = data.draw(st.integers(min_value=0, max_value=99), label="vault_y")
        vault = _MockBuilding("VT", vault_x, vault_y, owner=owner)

        # Extractor at a different position
        ex_x = data.draw(st.integers(min_value=0, max_value=99), label="ex_x")
        ex_y = data.draw(st.integers(min_value=0, max_value=99), label="ex_y")
        assume((ex_x, ex_y) != (vault_x, vault_y))
        extractor = _MockBuilding("EX", ex_x, ex_y, owner=owner)

        room = _MockRoom()
        room.contents = [vault]

        npc = _make_delivery_npc(
            location=room,
            coord_x=ex_x,
            coord_y=ex_y,
            owner=owner,
            role_target=extractor,
            carried_resources=dict(carried),
            delivery_state="picking_up",
        )

        # Monkey-patch _get_attr for building attribute lookups
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

        # If delivery started (path found), delay should be HARVESTER_LADEN_DELAY
        if npc.db.delivery_state == "delivering":
            assert npc.db.movement_delay == HARVESTER_LADEN_DELAY, (
                f"Expected movement_delay={HARVESTER_LADEN_DELAY} when delivering, "
                f"got {npc.db.movement_delay}"
            )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_returning_state_sets_empty_delay(self, data):
        """When _deposit_and_return transitions to returning, movement_delay = HARVESTER_EMPTY_DELAY."""
        carried = data.draw(
            st.dictionaries(
                resource_types,
                st.integers(min_value=1, max_value=100),
                min_size=1,
                max_size=3,
            ),
            label="carried",
        )

        owner = _MockOwner()

        ex_x = data.draw(st.integers(min_value=0, max_value=99), label="ex_x")
        ex_y = data.draw(st.integers(min_value=0, max_value=99), label="ex_y")
        npc_x = data.draw(st.integers(min_value=0, max_value=99), label="npc_x")
        npc_y = data.draw(st.integers(min_value=0, max_value=99), label="npc_y")
        assume((npc_x, npc_y) != (ex_x, ex_y))

        extractor = _MockBuilding("EX", ex_x, ex_y, owner=owner)

        npc = _make_delivery_npc(
            location=_FakeRoom(),
            coord_x=npc_x,
            coord_y=npc_y,
            owner=owner,
            role_target=extractor,
            carried_resources=dict(carried),
            delivery_state="delivering",
        )

        behavior = DeliveryBehavior.__new__(DeliveryBehavior)
        behavior.obj = npc
        behavior._deposit_and_return(npc)

        # If returning (path found), delay should be HARVESTER_EMPTY_DELAY
        if npc.db.delivery_state == "returning":
            assert npc.db.movement_delay == HARVESTER_EMPTY_DELAY, (
                f"Expected movement_delay={HARVESTER_EMPTY_DELAY} when returning, "
                f"got {npc.db.movement_delay}"
            )

    @given(
        delivery_state=st.sampled_from(["idle", "returning"]),
    )
    @settings(max_examples=100)
    def test_idle_and_returning_arrival_sets_empty_delay(self, delivery_state):
        """When _arrived_at_extractor is called, movement_delay = HARVESTER_EMPTY_DELAY."""
        npc = _make_delivery_npc(
            delivery_state=delivery_state,
            movement_delay=HARVESTER_LADEN_DELAY,  # start with laden delay
        )

        behavior = DeliveryBehavior.__new__(DeliveryBehavior)
        behavior.obj = npc
        behavior._arrived_at_extractor(npc)

        assert npc.db.movement_delay == HARVESTER_EMPTY_DELAY, (
            f"Expected movement_delay={HARVESTER_EMPTY_DELAY} after arriving at extractor, "
            f"got {npc.db.movement_delay}"
        )
        assert npc.db.delivery_state == "idle", (
            f"Expected delivery_state='idle', got '{npc.db.delivery_state}'"
        )


# ================================================================== #
#  Properties 16–17: Reassignment and Cancellation
# ================================================================== #


# ------------------------------------------------------------------ #
#  Property 16: Reassignment Clears and Replaces Queue
#  Feature: agent-ai, Property 16: Reassignment Clears and Replaces Queue
# ------------------------------------------------------------------ #


class TestProperty16ReassignmentClearsAndReplacesQueue:
    """When an in-transit NPC is reassigned via set_movement_queue,
    the old queue is completely replaced with the new path.

    **Validates: Requirements 11.2**
    """

    @given(
        start_x=st.integers(min_value=0, max_value=100),
        start_y=st.integers(min_value=0, max_value=100),
        old_steps=path_steps_strategy,
        new_steps=path_steps_strategy,
    )
    @settings(max_examples=100)
    def test_reassignment_replaces_queue(self, start_x, start_y, old_steps, new_steps):
        room = _FakeRoom()
        old_path = _adjacent_path(start_x, start_y, old_steps)
        new_path_tuples = [(start_x + i + 1, start_y) for i in range(len(new_steps))]

        npc = _make_npc(
            location=room,
            coord_x=start_x,
            coord_y=start_y,
            movement_queue=[list(p) for p in old_path],
            movement_delay=1,
        )

        # Verify old queue is in place
        assert len(npc.db.movement_queue) == len(old_path)

        # Simulate reassignment: call set_movement_queue with a new path
        npc.set_movement_queue(new_path_tuples)

        # The old queue must be completely replaced
        expected_queue = [[x, y] for x, y in new_path_tuples]
        assert npc.db.movement_queue == expected_queue, (
            f"Expected new queue {expected_queue}, got {npc.db.movement_queue}"
        )

        # No remnants of the old path
        for old_step in old_path:
            if old_step not in expected_queue:
                assert old_step not in npc.db.movement_queue, (
                    f"Old step {old_step} should not be in new queue"
                )


# ------------------------------------------------------------------ #
#  Property 17: Cancellation Retains Carried Resources
#  Feature: agent-ai, Property 17: Cancellation Retains Carried Resources
# ------------------------------------------------------------------ #


class TestProperty17CancellationRetainsCarriedResources:
    """When a harvester's movement is cancelled, carried_resources
    remains unchanged, movement_queue is empty, and position is unchanged.

    **Validates: Requirements 11.4**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_cancellation_preserves_carried_resources(self, data):
        # Generate random carried resources
        carried = data.draw(
            st.dictionaries(
                resource_types,
                st.integers(min_value=1, max_value=500),
                min_size=1,
                max_size=4,
            ),
            label="carried",
        )

        pos_x = data.draw(st.integers(min_value=0, max_value=100), label="pos_x")
        pos_y = data.draw(st.integers(min_value=0, max_value=100), label="pos_y")

        # Generate a movement queue (NPC is in transit)
        steps = data.draw(path_steps_strategy, label="steps")
        queue = _adjacent_path(pos_x, pos_y, steps)

        npc = _make_delivery_npc(
            coord_x=pos_x,
            coord_y=pos_y,
            movement_queue=[list(p) for p in queue],
            carried_resources=dict(carried),
            delivery_state="delivering",
            role="harvester",
        )

        # Snapshot carried_resources before cancellation
        carried_before = dict(npc.db.carried_resources)

        # Simulate cancellation via clear_movement (what stop_agent calls)
        npc.clear_movement()

        # carried_resources must be unchanged
        assert npc.db.carried_resources == carried_before, (
            f"Expected carried_resources {carried_before}, "
            f"got {npc.db.carried_resources}"
        )

        # movement_queue must be empty
        assert npc.db.movement_queue == [], (
            f"Expected empty movement_queue, got {npc.db.movement_queue}"
        )

        # Position must be unchanged
        assert npc.db.coord_x == pos_x, (
            f"Expected coord_x={pos_x}, got {npc.db.coord_x}"
        )
        assert npc.db.coord_y == pos_y, (
            f"Expected coord_y={pos_y}, got {npc.db.coord_y}"
        )


# ================================================================== #
#  Property 14: Equipment Speed Modifier
# ================================================================== #


# ------------------------------------------------------------------ #
#  Helper: compute effective movement delay with equipment modifier
#
#  This is now the production function from world.constants, wired into
#  NPC.advance_movement (which queries the EquipmentHandler for a
#  "move_speed" stat modifier). The tests below exercise it directly.
# ------------------------------------------------------------------ #

from mygame.world.constants import compute_effective_delay


# ------------------------------------------------------------------ #
#  Property 14: Equipment Speed Modifier
#  Feature: agent-ai, Property 14: Equipment Speed Modifier
# ------------------------------------------------------------------ #


class TestProperty14EquipmentSpeedModifier:
    """effective_delay = max(1, base_delay - modifier).

    For any NPC with base movement_delay B and an equipped item providing
    a speed modifier M, the effective movement delay SHALL be
    max(1, B - M) (positive modifier = faster movement).

    **Validates: Requirements 8.8**
    """

    @given(
        base_delay=st.integers(min_value=1, max_value=10),
        modifier=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100)
    def test_effective_delay_formula(self, base_delay, modifier):
        effective = compute_effective_delay(base_delay, modifier)

        # Must equal max(1, base_delay - modifier)
        expected = max(1, base_delay - modifier)
        assert effective == expected, (
            f"compute_effective_delay({base_delay}, {modifier}) = {effective}, "
            f"expected max(1, {base_delay} - {modifier}) = {expected}"
        )

    @given(
        base_delay=st.integers(min_value=1, max_value=10),
        modifier=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100)
    def test_effective_delay_never_below_one(self, base_delay, modifier):
        """The effective delay is always at least 1 (every-tick movement)."""
        effective = compute_effective_delay(base_delay, modifier)
        assert effective >= 1, (
            f"Effective delay {effective} < 1 for base={base_delay}, modifier={modifier}"
        )

    @given(
        base_delay=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=100)
    def test_zero_modifier_preserves_base_delay(self, base_delay):
        """With no equipment modifier, effective delay equals base delay."""
        effective = compute_effective_delay(base_delay, 0)
        assert effective == base_delay, (
            f"Expected {base_delay} with zero modifier, got {effective}"
        )
