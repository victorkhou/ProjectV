"""
Property-based tests for CoordinateIndex add/remove/move invariant.

Feature: coordinate-room-refactor, Property 1: Coordinate Index Invariant

For any sequence of add, remove, and move operations, get_at(x, y)
always returns exactly the objects at those coordinates. Furthermore,
rebuilding from contents produces identical results.

**Validates: Requirements 2.1, 2.5, 2.7, 2.8, 6.5, 6.6, 15.3, 15.5**
"""

import unittest

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, rule, invariant

from mygame.world.coordinate.coordinate_index import CoordinateIndex


# ------------------------------------------------------------------ #
#  Mock object with db.coord_x / db.coord_y (mirrors conftest _DbProxy)
# ------------------------------------------------------------------ #

class _AttrStore:
    """Minimal attribute store matching conftest pattern."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class _DbProxy:
    """Proxy that exposes attribute store fields as properties."""

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class MockObj:
    """Lightweight mock with db.coord_x / db.coord_y for index testing."""

    _counter = 0

    def __init__(self, x=None, y=None):
        MockObj._counter += 1
        self._id = MockObj._counter
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.db.coord_x = x
        self.db.coord_y = y

    def __repr__(self):
        return f"MockObj({self._id}, x={self.db.coord_x}, y={self.db.coord_y})"

    def __hash__(self):
        return self._id

    def __eq__(self, other):
        return isinstance(other, MockObj) and self._id == other._id


# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

coords = st.tuples(st.integers(-10, 10), st.integers(-10, 10))


# ================================================================== #
#  Stateful test: Coordinate Index Invariant
#  **Validates: Requirements 2.1, 2.5, 2.7, 2.8, 6.5, 6.6, 15.3, 15.5**
# ================================================================== #

class CoordinateIndexStateMachine(RuleBasedStateMachine):
    """Stateful property test for CoordinateIndex.

    Feature: coordinate-room-refactor, Property 1: Coordinate Index Invariant

    Maintains a reference model (dict mapping (x,y) -> set of objects)
    alongside the real CoordinateIndex. Every operation is applied to both,
    and invariants are checked after each step.

    **Validates: Requirements 2.1, 2.5, 2.7, 2.8, 6.5, 6.6, 15.3, 15.5**
    """

    def __init__(self):
        super().__init__()
        self.index = CoordinateIndex()
        # Reference model: (x, y) -> set of MockObj
        self.model: dict[tuple[int, int], set] = {}
        # Track all live objects and their current coordinates
        self.obj_coords: dict[MockObj, tuple[int, int]] = {}

    objects = Bundle("objects")

    @rule(target=objects, coord=coords)
    def add_object(self, coord):
        """Add a new object at the given coordinate."""
        x, y = coord
        obj = MockObj(x=x, y=y)
        self.index.add(obj, x, y)
        self.model.setdefault((x, y), set()).add(obj)
        self.obj_coords[obj] = (x, y)
        return obj

    @rule(obj=objects)
    def remove_object(self, obj):
        """Remove an existing object from the index."""
        if obj not in self.obj_coords:
            # Already removed — exercise the silent-ignore path
            self.index.remove(obj, 0, 0)
            return
        x, y = self.obj_coords[obj]
        self.index.remove(obj, x, y)
        self.model[(x, y)].discard(obj)
        if not self.model[(x, y)]:
            del self.model[(x, y)]
        del self.obj_coords[obj]

    @rule(obj=objects, new_coord=coords)
    def move_object(self, obj, new_coord):
        """Move an existing object to a new coordinate."""
        new_x, new_y = new_coord
        if obj not in self.obj_coords:
            # Object was removed; move with None old coords (first placement)
            self.index.move(obj, None, None, new_x, new_y)
            self.model.setdefault((new_x, new_y), set()).add(obj)
            self.obj_coords[obj] = (new_x, new_y)
            obj.db.coord_x = new_x
            obj.db.coord_y = new_y
            return
        old_x, old_y = self.obj_coords[obj]
        self.index.move(obj, old_x, old_y, new_x, new_y)
        # Update reference model
        self.model[(old_x, old_y)].discard(obj)
        if not self.model[(old_x, old_y)]:
            del self.model[(old_x, old_y)]
        self.model.setdefault((new_x, new_y), set()).add(obj)
        self.obj_coords[obj] = (new_x, new_y)
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y

    @invariant()
    def index_matches_model(self):
        """get_at(x, y) returns exactly the objects the model expects."""
        # Check every coordinate that should have objects
        for (x, y), expected_objs in self.model.items():
            actual = set(self.index.get_at(x, y))
            assert actual == expected_objs, (
                f"Mismatch at ({x}, {y}): "
                f"expected {expected_objs}, got {actual}"
            )
        # Check total count matches
        expected_total = sum(len(s) for s in self.model.values())
        assert len(self.index) == expected_total, (
            f"Total count mismatch: expected {expected_total}, "
            f"got {len(self.index)}"
        )

    @invariant()
    def rebuild_matches_accumulated(self):
        """build_from_contents produces the same index as accumulated ops."""
        # Collect all live objects with their current coordinates
        all_objs = list(self.obj_coords.keys())
        rebuilt = CoordinateIndex.build_from_contents(all_objs)

        for (x, y), expected_objs in self.model.items():
            rebuilt_at = set(rebuilt.get_at(x, y))
            assert rebuilt_at == expected_objs, (
                f"Rebuild mismatch at ({x}, {y}): "
                f"expected {expected_objs}, got {rebuilt_at}"
            )
        assert len(rebuilt) == len(self.index), (
            f"Rebuild total mismatch: rebuilt={len(rebuilt)}, "
            f"index={len(self.index)}"
        )


# Wrap the state machine as a standard unittest for pytest discovery
TestCoordinateIndexInvariant = CoordinateIndexStateMachine.TestCase
TestCoordinateIndexInvariant.settings = settings(
    max_examples=100,
    stateful_step_count=30,
)



# ================================================================== #
#  Mock objects for type-filtered query testing
# ================================================================== #

class _MockTagHandler:
    """Minimal tag handler supporting tags.get(tag, category=...)."""

    def __init__(self, tags: dict[str, set[str]] | None = None):
        # category -> set of tag values
        self._tags: dict[str, set[str]] = tags or {}

    def add(self, tag: str, category: str = "default") -> None:
        self._tags.setdefault(category, set()).add(tag)

    def get(self, tag: str, category: str = "default") -> str | None:
        """Return the tag value if present in the category, else None."""
        if category in self._tags and tag in self._tags[category]:
            return tag
        return None


class TypedMockObj(MockObj):
    """MockObj extended with a tag handler and optional has_account flag."""

    _type_counter = 0

    def __init__(self, x, y, object_type_tag=None, is_player=False):
        super().__init__(x=x, y=y)
        self.tags = _MockTagHandler()
        if object_type_tag:
            self.tags.add(object_type_tag, category="object_type")
        # Player characters have has_account = True
        self.has_account = is_player

    def __repr__(self):
        tag_info = ""
        if self.tags._tags.get("object_type"):
            tag_info = f", tags={self.tags._tags['object_type']}"
        acct = f", player={self.has_account}" if self.has_account else ""
        return (
            f"TypedMockObj({self._id}, x={self.db.coord_x}, "
            f"y={self.db.coord_y}{tag_info}{acct})"
        )


# ================================================================== #
#  Simulated PlanetRoom query methods (from design.md)
# ================================================================== #

def _get_objects_at(index, x, y, type_tag=None):
    """Simulate PlanetRoom.get_objects_at."""
    objs = index.get_at(x, y)
    if type_tag is None:
        return objs
    return [
        o for o in objs
        if hasattr(o, "tags") and o.tags.get(type_tag, category="object_type")
    ]


def _get_buildings_at(index, x, y):
    """Simulate PlanetRoom.get_buildings_at."""
    return _get_objects_at(index, x, y, type_tag="building")


def _get_players_at(index, x, y):
    """Simulate PlanetRoom.get_players_at."""
    return [
        o for o in index.get_at(x, y)
        if hasattr(o, "has_account") and o.has_account
    ]


# ================================================================== #
#  Strategies for typed objects
# ================================================================== #

OBJECT_TYPES = ["building", "player", "npc", "resource_drop", "item"]


@st.composite
def typed_object_list(draw):
    """Generate a list of TypedMockObj with random types and coordinates."""
    n = draw(st.integers(min_value=0, max_value=20))
    objs = []
    for _ in range(n):
        x = draw(st.integers(-5, 5))
        y = draw(st.integers(-5, 5))
        obj_type = draw(st.sampled_from(OBJECT_TYPES))
        if obj_type == "player":
            obj = TypedMockObj(x, y, object_type_tag=None, is_player=True)
        else:
            obj = TypedMockObj(x, y, object_type_tag=obj_type, is_player=False)
        objs.append(obj)
    return objs


# ================================================================== #
#  Property 2: Type-Filtered Query Correctness
#  **Validates: Requirements 2.2, 2.3**
# ================================================================== #

from hypothesis import given


@given(objects=typed_object_list(), query_coord=coords)
@settings(max_examples=200)
def test_type_filtered_query_correctness(objects, query_coord):
    """Property 2: Type-Filtered Query Correctness.

    For any PlanetRoom containing a mix of object types at various
    coordinates, get_buildings_at returns exactly the building-tagged
    subset of get_objects_at, and get_players_at returns exactly the
    player-character subset.

    **Validates: Requirements 2.2, 2.3**
    """
    qx, qy = query_coord

    # Build the index from the generated objects
    index = CoordinateIndex()
    for obj in objects:
        index.add(obj, obj.db.coord_x, obj.db.coord_y)

    # --- All objects at the queried coordinate ---
    all_at = _get_objects_at(index, qx, qy)

    # --- Buildings: must be exactly the building-tagged subset ---
    buildings_at = _get_buildings_at(index, qx, qy)
    expected_buildings = [
        o for o in all_at
        if hasattr(o, "tags") and o.tags.get("building", category="object_type")
    ]
    assert set(buildings_at) == set(expected_buildings), (
        f"get_buildings_at({qx}, {qy}) mismatch: "
        f"got {buildings_at}, expected {expected_buildings}"
    )

    # --- Players: must be exactly the has_account subset ---
    players_at = _get_players_at(index, qx, qy)
    expected_players = [
        o for o in all_at
        if hasattr(o, "has_account") and o.has_account
    ]
    assert set(players_at) == set(expected_players), (
        f"get_players_at({qx}, {qy}) mismatch: "
        f"got {players_at}, expected {expected_players}"
    )

    # --- Cross-check: buildings and players are disjoint ---
    # (players don't have building tags, buildings don't have has_account=True)
    assert set(buildings_at).isdisjoint(set(players_at)), (
        f"Buildings and players overlap at ({qx}, {qy}): "
        f"buildings={buildings_at}, players={players_at}"
    )

    # --- Subset check: both are subsets of all_at ---
    assert set(buildings_at).issubset(set(all_at)), (
        f"Buildings not a subset of all objects at ({qx}, {qy})"
    )
    assert set(players_at).issubset(set(all_at)), (
        f"Players not a subset of all objects at ({qx}, {qy})"
    )


# ================================================================== #
#  Property 3: Area Query Correctness
#  **Validates: Requirements 2.4**
# ================================================================== #


@st.composite
def bounding_box(draw):
    """Generate a bounding box (x1, y1, x2, y2) where x1 <= x2 and y1 <= y2."""
    x1 = draw(st.integers(-10, 10))
    x2 = draw(st.integers(-10, 10))
    y1 = draw(st.integers(-10, 10))
    y2 = draw(st.integers(-10, 10))
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


@given(
    objects=st.lists(
        st.builds(MockObj, x=st.integers(-10, 10), y=st.integers(-10, 10)),
        min_size=0,
        max_size=30,
    ),
    bbox=bounding_box(),
)
@settings(max_examples=200)
def test_area_query_correctness(objects, bbox):
    """Property 3: Area Query Correctness.

    For any set of objects at various coordinates and any bounding box
    (x1, y1, x2, y2), get_in_area returns exactly the objects whose
    coord_x is in [x1, x2] and coord_y is in [y1, y2].

    **Validates: Requirements 2.4**
    """
    x1, y1, x2, y2 = bbox

    # Build the index
    index = CoordinateIndex()
    for obj in objects:
        index.add(obj, obj.db.coord_x, obj.db.coord_y)

    # Query the index
    actual = index.get_in_area(x1, y1, x2, y2)

    # Brute-force reference: filter all objects whose coords are within bounds
    expected = [
        obj for obj in objects
        if x1 <= obj.db.coord_x <= x2 and y1 <= obj.db.coord_y <= y2
    ]

    # Compare as sets (order doesn't matter)
    assert set(actual) == set(expected), (
        f"Area query mismatch for bbox ({x1}, {y1}, {x2}, {y2}): "
        f"got {len(actual)} objects, expected {len(expected)}"
    )

    # Also verify counts match (catches duplicates)
    assert len(actual) == len(expected), (
        f"Area query count mismatch for bbox ({x1}, {y1}, {x2}, {y2}): "
        f"got {len(actual)}, expected {len(expected)}"
    )


if __name__ == "__main__":
    unittest.main()
