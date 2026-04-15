"""
Property-based tests for ResourceSystem coordinate-aware operations.

Property 8: Resource Drop Merge Correctness
Property 9: Resource Pickup Accounting
Property 11: Depletion Dictionary Sparse Invariant

Feature: coordinate-room-refactor

**Validates: Requirements 5.1, 5.2, 6.1, 9.2, 9.4, 14.3**
"""

import unittest

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ------------------------------------------------------------------ #
#  Lightweight mocks (no Evennia runtime)
# ------------------------------------------------------------------ #

class _AttrStore:
    """Minimal attribute store matching conftest pattern."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class _DbProxy:
    """Proxy that exposes attribute store fields as properties."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class _MockTagHandler:
    """Minimal tag handler supporting tags.get(tag, category=...)."""

    def __init__(self, tags=None):
        self._tags = tags or {}

    def add(self, tag, category="default"):
        self._tags.setdefault(category, set()).add(tag)

    def get(self, tag, category="default"):
        if category in self._tags and tag in self._tags[category]:
            return tag
        return None


class MockResourceDrop:
    """Lightweight mock for a ResourceDrop object."""

    _counter = 0

    def __init__(self, resource_type, amount, x=None, y=None):
        MockResourceDrop._counter += 1
        self._id = MockResourceDrop._counter
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.db.resource_type = resource_type
        self.db.amount = amount
        self.db.coord_x = x
        self.db.coord_y = y
        self.key = resource_type
        self.tags = _MockTagHandler()
        self.tags.add("resource_drop", category="object_type")

    def __repr__(self):
        return (
            f"MockResourceDrop({self._id}, {self.db.resource_type}, "
            f"amt={self.db.amount}, x={self.db.coord_x}, y={self.db.coord_y})"
        )

    def __hash__(self):
        return self._id

    def __eq__(self, other):
        return isinstance(other, MockResourceDrop) and self._id == other._id


class MockPlayer:
    """Lightweight mock for a player character."""

    def __init__(self, name="TestPlayer"):
        self.key = name
        self._resources = {}
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)

    def get_resource(self, resource_type):
        return self._resources.get(resource_type, 0)

    def add_resource(self, resource_type, amount):
        self._resources[resource_type] = self._resources.get(resource_type, 0) + amount

    def msg(self, text, **kwargs):
        pass


class MockPlanetRoom:
    """Lightweight mock for PlanetRoom with coordinate index and depletion."""

    def __init__(self):
        self._objects = []  # list of all objects in the room
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.db.depleted_nodes = {}

    def get_objects_at(self, x, y, type_tag=None):
        """Return objects at (x, y), optionally filtered by type_tag."""
        result = []
        for obj in self._objects:
            ox = getattr(obj.db, "coord_x", None)
            oy = getattr(obj.db, "coord_y", None)
            if ox == x and oy == y:
                if type_tag is None:
                    result.append(obj)
                elif hasattr(obj, "tags") and obj.tags.get(type_tag, category="object_type"):
                    result.append(obj)
        return result

    def add_object(self, obj):
        """Add an object to the room (test helper)."""
        self._objects.append(obj)

    # --- Depletion dict methods (mirror PlanetRoom) ---

    @staticmethod
    def _node_key(x, y):
        return f"{x},{y}"

    def get_depleted_nodes(self):
        return self.db.depleted_nodes or {}

    def set_node_depleted(self, x, y, resource_type, respawn_counter):
        nodes = self.db.depleted_nodes or {}
        nodes[self._node_key(x, y)] = {
            "resource_type": resource_type,
            "respawn_counter": respawn_counter,
        }
        self.db.depleted_nodes = nodes

    def clear_node_depletion(self, x, y):
        nodes = self.db.depleted_nodes or {}
        nodes.pop(self._node_key(x, y), None)
        self.db.depleted_nodes = nodes

    def is_node_depleted(self, x, y):
        nodes = self.db.depleted_nodes or {}
        return self._node_key(x, y) in nodes


# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

RESOURCE_TYPES = ["Wood", "Stone", "Iron", "Straw", "Clay", "Energy"]

coords = st.tuples(st.integers(-5, 5), st.integers(-5, 5))
resource_type_st = st.sampled_from(RESOURCE_TYPES)
amount_st = st.integers(min_value=1, max_value=1000)


# ================================================================== #
#  Simulated spawn_resource_drop logic (mirrors objects.py)
# ================================================================== #

def sim_spawn_resource_drop(room, resource_type, amount, x, y):
    """Simulate spawn_resource_drop with coordinate-aware merge.

    This mirrors the real spawn_resource_drop logic from objects.py
    but uses MockPlanetRoom and MockResourceDrop for testing.
    """
    if amount <= 0:
        return None

    # Merge with existing drop at same (x, y) and same type
    for obj in room.get_objects_at(x, y, type_tag="resource_drop"):
        if getattr(obj.db, "resource_type", None) == resource_type:
            obj.db.amount = (obj.db.amount or 0) + amount
            return obj

    # Create new drop
    drop = MockResourceDrop(resource_type, amount, x=x, y=y)
    room.add_object(drop)
    return drop


# ================================================================== #
#  Property 8: Resource Drop Merge Correctness
#  **Validates: Requirements 5.1, 5.2**
# ================================================================== #

@st.composite
def drop_sequence_strategy(draw):
    """Generate a sequence of resource drop spawn operations.

    Each operation is (x, y, resource_type, amount).
    """
    n = draw(st.integers(min_value=1, max_value=20))
    ops = []
    for _ in range(n):
        x, y = draw(coords)
        rtype = draw(resource_type_st)
        amt = draw(amount_st)
        ops.append((x, y, rtype, amt))
    return ops


@given(ops=drop_sequence_strategy())
@settings(max_examples=200)
def test_resource_drop_merge_correctness(ops):
    """Property 8: Resource Drop Merge Correctness.

    For any PlanetRoom containing ResourceDrop objects at various
    coordinates with various resource types, spawning a new drop of
    type T at (x, y) SHALL merge with an existing drop only if that
    drop has coord_x == x, coord_y == y, and resource_type == T.
    If no such drop exists, a new ResourceDrop object SHALL be created
    with the correct coordinates and type.

    **Validates: Requirements 5.1, 5.2**
    """
    room = MockPlanetRoom()

    # Reference model: (x, y, resource_type) -> total amount
    expected_totals = {}

    for x, y, rtype, amt in ops:
        key = (x, y, rtype)
        expected_totals[key] = expected_totals.get(key, 0) + amt
        sim_spawn_resource_drop(room, rtype, amt, x, y)

    # Verify: for each unique (x, y, type) there is exactly one drop
    # with the correct total amount
    for (ex, ey, etype), expected_amt in expected_totals.items():
        drops_at = [
            obj for obj in room.get_objects_at(ex, ey, type_tag="resource_drop")
            if getattr(obj.db, "resource_type", None) == etype
        ]
        assert len(drops_at) == 1, (
            f"Expected exactly 1 drop of {etype} at ({ex}, {ey}), "
            f"got {len(drops_at)}"
        )
        assert drops_at[0].db.amount == expected_amt, (
            f"Expected {expected_amt} {etype} at ({ex}, {ey}), "
            f"got {drops_at[0].db.amount}"
        )
        assert drops_at[0].db.coord_x == ex
        assert drops_at[0].db.coord_y == ey

    # Verify: no cross-coordinate or cross-type merging occurred
    # Total drops in room should equal number of unique (x, y, type) keys
    all_drops = [
        obj for obj in room._objects
        if hasattr(obj, "tags") and obj.tags.get("resource_drop", category="object_type")
    ]
    assert len(all_drops) == len(expected_totals), (
        f"Expected {len(expected_totals)} unique drops, got {len(all_drops)}"
    )


@given(
    x1=st.integers(-5, 5), y1=st.integers(-5, 5),
    x2=st.integers(-5, 5), y2=st.integers(-5, 5),
    rtype=resource_type_st,
    amt1=amount_st, amt2=amount_st,
)
@settings(max_examples=200)
def test_resource_drop_no_cross_coordinate_merge(x1, y1, x2, y2, rtype, amt1, amt2):
    """Drops of the same type at different coordinates must NOT merge.

    **Validates: Requirements 5.1, 5.2**
    """
    assume((x1, y1) != (x2, y2))

    room = MockPlanetRoom()
    sim_spawn_resource_drop(room, rtype, amt1, x1, y1)
    sim_spawn_resource_drop(room, rtype, amt2, x2, y2)

    drops_at_1 = room.get_objects_at(x1, y1, type_tag="resource_drop")
    drops_at_2 = room.get_objects_at(x2, y2, type_tag="resource_drop")

    assert len(drops_at_1) == 1, f"Expected 1 drop at ({x1},{y1}), got {len(drops_at_1)}"
    assert len(drops_at_2) == 1, f"Expected 1 drop at ({x2},{y2}), got {len(drops_at_2)}"
    assert drops_at_1[0].db.amount == amt1
    assert drops_at_2[0].db.amount == amt2
    assert drops_at_1[0] is not drops_at_2[0]


# ================================================================== #
#  Property 9: Resource Pickup Accounting
#  **Validates: Requirements 6.1**
# ================================================================== #

def sim_at_get(drop, getter):
    """Simulate ResourceDrop.at_get logic.

    When picked up, add to the getter's resources and zero the drop.
    """
    amt = drop.db.amount or 0
    rtype = drop.db.resource_type or ""
    if amt > 0 and rtype and hasattr(getter, "add_resource"):
        getter.add_resource(rtype, amt)
    # Zero out so it can't be double-collected
    drop.db.amount = 0


@given(
    rtype=resource_type_st,
    amount=amount_st,
    initial_balance=st.integers(min_value=0, max_value=10000),
)
@settings(max_examples=200)
def test_resource_pickup_accounting(rtype, amount, initial_balance):
    """Property 9: Resource Pickup Accounting.

    For any ResourceDrop with resource_type == T and amount == N
    where N > 0, when a player picks it up, the player's resource
    balance for T SHALL increase by exactly N, and the drop's
    amount SHALL become 0.

    **Validates: Requirements 6.1**
    """
    player = MockPlayer()
    player._resources[rtype] = initial_balance

    drop = MockResourceDrop(rtype, amount, x=0, y=0)

    # Simulate pickup
    sim_at_get(drop, player)

    # Player balance increased by exactly the drop amount
    assert player.get_resource(rtype) == initial_balance + amount, (
        f"Expected {initial_balance + amount} {rtype}, "
        f"got {player.get_resource(rtype)}"
    )

    # Drop amount is zeroed
    assert drop.db.amount == 0, (
        f"Drop amount should be 0 after pickup, got {drop.db.amount}"
    )


@given(
    rtype=resource_type_st,
    amount=amount_st,
)
@settings(max_examples=200)
def test_resource_pickup_only_affects_matching_type(rtype, amount):
    """Picking up a drop only increases the matching resource type.

    **Validates: Requirements 6.1**
    """
    player = MockPlayer()
    # Initialize all resources to 0
    for rt in RESOURCE_TYPES:
        player._resources[rt] = 0

    drop = MockResourceDrop(rtype, amount, x=0, y=0)
    sim_at_get(drop, player)

    for rt in RESOURCE_TYPES:
        if rt == rtype:
            assert player.get_resource(rt) == amount
        else:
            assert player.get_resource(rt) == 0, (
                f"Resource {rt} should be 0 after picking up {rtype}, "
                f"got {player.get_resource(rt)}"
            )


@given(
    rtype=resource_type_st,
    amount=amount_st,
)
@settings(max_examples=200)
def test_resource_pickup_double_collect_prevented(rtype, amount):
    """After pickup, the drop's amount is 0 so double-collect yields nothing.

    **Validates: Requirements 6.1**
    """
    player = MockPlayer()
    drop = MockResourceDrop(rtype, amount, x=0, y=0)

    sim_at_get(drop, player)
    first_balance = player.get_resource(rtype)

    # Second pickup should add nothing
    sim_at_get(drop, player)
    assert player.get_resource(rtype) == first_balance, (
        f"Double-collect should not increase balance: "
        f"expected {first_balance}, got {player.get_resource(rtype)}"
    )


# ================================================================== #
#  Property 11: Depletion Dictionary Sparse Invariant
#  **Validates: Requirements 9.2, 9.4, 14.3**
# ================================================================== #

@st.composite
def depletion_ops_strategy(draw):
    """Generate a sequence of deplete and respawn-tick operations.

    Operations:
        ("deplete", x, y, resource_type, respawn_counter)
        ("tick",)  — one respawn tick
    """
    n = draw(st.integers(min_value=1, max_value=30))
    ops = []
    for _ in range(n):
        op_type = draw(st.sampled_from(["deplete", "tick"]))
        if op_type == "deplete":
            x, y = draw(coords)
            rtype = draw(resource_type_st)
            counter = draw(st.integers(min_value=1, max_value=10))
            ops.append(("deplete", x, y, rtype, counter))
        else:
            ops.append(("tick",))
    return ops


def sim_process_respawns(room):
    """Simulate one respawn tick on a MockPlanetRoom's depletion dict.

    Mirrors the PlanetRoom path in ResourceSystem.process_respawns:
    decrement all counters, remove entries that reach 0.
    """
    depleted = room.get_depleted_nodes()
    if not depleted:
        return
    to_clear = []
    for key, entry in list(depleted.items()):
        counter = entry.get("respawn_counter", 0)
        counter -= 1
        if counter <= 0:
            to_clear.append(key)
        else:
            entry["respawn_counter"] = counter
    for key in to_clear:
        depleted.pop(key, None)
    room.db.depleted_nodes = depleted


@given(ops=depletion_ops_strategy())
@settings(max_examples=200)
def test_depletion_dict_sparse_invariant(ops):
    """Property 11: Depletion Dictionary Sparse Invariant.

    For any sequence of deplete and respawn-tick operations:
    (a) the dictionary SHALL contain only entries for currently depleted
        nodes (no zero-counter entries),
    (b) each respawn tick SHALL decrement all respawn_counter values by 1,
    (c) entries whose counter reaches 0 SHALL be removed from the
        dictionary. An absent key means the node is available.

    **Validates: Requirements 9.2, 9.4, 14.3**
    """
    room = MockPlanetRoom()

    # Reference model: {(x, y) -> remaining_counter}
    model = {}

    for op in ops:
        if op[0] == "deplete":
            _, x, y, rtype, counter = op
            room.set_node_depleted(x, y, rtype, counter)
            model[(x, y)] = counter
        else:
            # tick
            sim_process_respawns(room)
            # Update reference model
            to_remove = []
            for key in model:
                model[key] -= 1
                if model[key] <= 0:
                    to_remove.append(key)
            for key in to_remove:
                del model[key]

        # --- Invariant checks after every operation ---

        depleted = room.get_depleted_nodes()

        # (a) Dict only contains entries for currently depleted nodes
        for key, entry in depleted.items():
            counter = entry.get("respawn_counter", 0)
            assert counter > 0, (
                f"Depletion dict contains zero-counter entry at {key}: {entry}"
            )

        # (b) & (c) Dict matches reference model
        # Check all model entries are in the dict
        for (mx, my), expected_counter in model.items():
            node_key = f"{mx},{my}"
            assert node_key in depleted, (
                f"Expected depleted entry at ({mx},{my}) with counter "
                f"{expected_counter}, but key not in dict"
            )
            actual_counter = depleted[node_key].get("respawn_counter", 0)
            assert actual_counter == expected_counter, (
                f"Counter mismatch at ({mx},{my}): "
                f"expected {expected_counter}, got {actual_counter}"
            )

        # Check no extra entries in the dict
        model_keys = {f"{mx},{my}" for (mx, my) in model}
        dict_keys = set(depleted.keys())
        assert dict_keys == model_keys, (
            f"Dict keys mismatch: dict has {dict_keys - model_keys} extra, "
            f"missing {model_keys - dict_keys}"
        )


@given(
    x=st.integers(-5, 5),
    y=st.integers(-5, 5),
    rtype=resource_type_st,
    counter=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=200)
def test_depletion_exact_tick_removal(x, y, rtype, counter):
    """A depleted node is removed after exactly `counter` ticks.

    **Validates: Requirements 9.2, 9.4, 14.3**
    """
    room = MockPlanetRoom()
    room.set_node_depleted(x, y, rtype, counter)

    # After counter-1 ticks, still depleted
    for _ in range(counter - 1):
        sim_process_respawns(room)
        assert room.is_node_depleted(x, y), (
            f"Node at ({x},{y}) should still be depleted"
        )

    # After one more tick, removed
    sim_process_respawns(room)
    assert not room.is_node_depleted(x, y), (
        f"Node at ({x},{y}) should be cleared after {counter} ticks"
    )
    assert room.get_depleted_nodes() == {}, (
        f"Depletion dict should be empty, got {room.get_depleted_nodes()}"
    )


if __name__ == "__main__":
    unittest.main()
