"""
Property-based tests for PlanetRoom proximity behaviours.

Feature: coordinate-room-refactor

Property 4: Proximity Message Delivery
Property 5: Coordinate-Scoped Pickup

These tests use lightweight mocks (no Evennia runtime) to verify the
core logic of msg_contents proximity filtering and at_pre_get
coordinate-scoped pickup.
"""

from hypothesis import given, settings
from hypothesis import strategies as st


# ------------------------------------------------------------------ #
#  Mock infrastructure (mirrors test_prop_coordinate_index.py)
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


class MockObj:
    """Lightweight mock with db.coord_x / db.coord_y."""

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


class MockPlayer(MockObj):
    """Mock player with has_account=True and a msg recorder."""

    def __init__(self, x, y):
        super().__init__(x=x, y=y)
        self.has_account = True
        self.received_messages = []

    def msg(self, text, **kwargs):
        self.received_messages.append(text)

    def __repr__(self):
        return (
            f"MockPlayer({self._id}, x={self.db.coord_x}, "
            f"y={self.db.coord_y})"
        )


# ------------------------------------------------------------------ #
#  Replicated PlanetRoom.msg_contents logic (from rooms.py)
# ------------------------------------------------------------------ #

def msg_contents(contents, text, exclude=None, from_obj=None, **kwargs):
    """Replicate PlanetRoom.msg_contents proximity filter.

    If from_obj has coordinates, only objects at the same (x, y) receive
    the message. Otherwise falls back to broadcasting to all contents.
    """
    if from_obj is not None and hasattr(from_obj, "db"):
        sx = getattr(from_obj.db, "coord_x", None)
        sy = getattr(from_obj.db, "coord_y", None)
        if sx is not None and sy is not None:
            exclude = exclude or []
            for obj in contents:
                if obj in exclude:
                    continue
                if hasattr(obj, "db"):
                    ox = getattr(obj.db, "coord_x", None)
                    oy = getattr(obj.db, "coord_y", None)
                    if ox == sx and oy == sy:
                        if hasattr(obj, "msg"):
                            obj.msg(text, **kwargs)
            return
    # Fallback: broadcast to all
    exclude = exclude or []
    for obj in contents:
        if obj in exclude:
            continue
        if hasattr(obj, "msg"):
            obj.msg(text, **kwargs)


# ------------------------------------------------------------------ #
#  Replicated GameEntity.at_pre_get logic (from objects.py)
# ------------------------------------------------------------------ #

def at_pre_get(obj, getter):
    """Replicate GameEntity.at_pre_get coordinate-scoped pickup.

    Returns True if pickup is allowed, False otherwise.
    """
    if obj.db.coord_x is None:
        return True  # not placed, allow
    gx = getattr(getattr(getter, "db", None), "coord_x", None)
    gy = getattr(getattr(getter, "db", None), "coord_y", None)
    if gx is None or gy is None:
        return False
    if int(gx) != int(obj.db.coord_x) or int(gy) != int(obj.db.coord_y):
        return False
    return True


# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

coords = st.tuples(st.integers(-10, 10), st.integers(-10, 10))


@st.composite
def player_list(draw):
    """Generate a list of MockPlayers at various coordinates."""
    n = draw(st.integers(min_value=1, max_value=15))
    players = []
    for _ in range(n):
        x, y = draw(coords)
        players.append(MockPlayer(x, y))
    return players


@st.composite
def objects_and_getter(draw):
    """Generate a list of MockObj items and a MockPlayer getter."""
    n = draw(st.integers(min_value=1, max_value=15))
    objs = []
    for _ in range(n):
        x, y = draw(coords)
        objs.append(MockObj(x, y))
    gx, gy = draw(coords)
    getter = MockPlayer(gx, gy)
    return objs, getter


# ================================================================== #
#  Property 4: Proximity Message Delivery
#  **Validates: Requirements 3.2, 3.6**
# ================================================================== #

@given(
    players=player_list(),
    sender_coord=coords,
    message=st.text(min_size=1, max_size=20),
)
@settings(max_examples=200)
def test_proximity_message_delivery(players, sender_coord, message):
    """Property 4: Proximity Message Delivery.

    For any PlanetRoom containing players at various coordinates, when
    msg_contents is called with a from_obj at (sx, sy), only players
    whose coord_x == sx and coord_y == sy receive the message. Players
    at different coordinates do not receive it.

    **Validates: Requirements 3.2, 3.6**
    """
    sx, sy = sender_coord
    sender = MockPlayer(sx, sy)

    # Clear any prior messages
    for p in players:
        p.received_messages.clear()

    # All contents = players + sender
    contents = list(players) + [sender]

    # Call the proximity-filtered msg_contents
    msg_contents(contents, message, exclude=[sender], from_obj=sender)

    for p in players:
        px = p.db.coord_x
        py = p.db.coord_y
        if px == sx and py == sy:
            # Player at same coordinates MUST have received the message
            assert message in p.received_messages, (
                f"Player at ({px}, {py}) should have received message "
                f"from sender at ({sx}, {sy}) but did not"
            )
        else:
            # Player at different coordinates MUST NOT have received it
            assert message not in p.received_messages, (
                f"Player at ({px}, {py}) should NOT have received message "
                f"from sender at ({sx}, {sy}) but did"
            )

    # Sender is excluded — should not receive own message
    assert message not in sender.received_messages, (
        "Sender should not receive their own message"
    )


# ================================================================== #
#  Property 5: Coordinate-Scoped Pickup
#  **Validates: Requirements 3.3**
# ================================================================== #

@given(data=objects_and_getter())
@settings(max_examples=200)
def test_coordinate_scoped_pickup(data):
    """Property 5: Coordinate-Scoped Pickup.

    For any set of objects at various coordinates and a player at
    (gx, gy), at_pre_get blocks pickup when coordinates differ and
    allows pickup when coordinates match.

    **Validates: Requirements 3.3**
    """
    objs, getter = data
    gx = getter.db.coord_x
    gy = getter.db.coord_y

    for obj in objs:
        ox = obj.db.coord_x
        oy = obj.db.coord_y
        allowed = at_pre_get(obj, getter)

        if ox is None:
            # Unplaced objects are always pickable
            assert allowed is True, (
                f"Object with coord_x=None should be pickable "
                f"but at_pre_get returned False"
            )
        elif int(ox) == int(gx) and int(oy) == int(gy):
            # Same coordinates — pickup allowed
            assert allowed is True, (
                f"Object at ({ox}, {oy}) should be pickable by "
                f"getter at ({gx}, {gy}) but at_pre_get returned False"
            )
        else:
            # Different coordinates — pickup blocked
            assert allowed is False, (
                f"Object at ({ox}, {oy}) should NOT be pickable by "
                f"getter at ({gx}, {gy}) but at_pre_get returned True"
            )
