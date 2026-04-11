"""
Property-based and unit tests for CmdMove movement system.

Property 8: Movement respects coordinate space bounds — for any player
position (x, y) and direction, if the target coordinate (x+dx, y+dy) is
within the Coordinate_Space bounds, movement SHALL succeed. If the target
is outside bounds, movement SHALL be rejected.

Property 9: Player coordinate attributes match location after movement —
for any successful movement to coordinate (tx, ty), the Player_Character's
coord_x and coord_y Attributes SHALL equal tx and ty respectively.

Unit tests: movement in all four directions, edge-of-map rejection,
offline building blocking.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4**
"""

import sys
import types
import unittest

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.command", {
        "Command": type("Command", (), {"func": lambda self: None}),
    })
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {
        "DefaultScript": type("DefaultScript", (), {}),
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.commands.game_commands import CmdMove  # noqa: E402

# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, coord_x=5, coord_y=5, coord_planet="earth_planet"):
        self.coord_x = coord_x
        self.coord_y = coord_y
        self.coord_planet = coord_planet
        self.combat_xp = 100
        self.rank_level = 3
        self.hp = 80
        self.hp_max = 100
        self.resources = {"Iron": 10}
        self.researched_techs = set()
        self.active_powerups = {}
        self.combat_lockout_tick = 0
        self.equipment_slots = {}
        self.discovery_memory = {}

class FakeNDB:
    """Simulates Evennia's ndb attribute handler."""
    def __init__(self, systems=None):
        self.systems = systems or {}
        self.tile_lookup = None

class FakeLocation:
    """Simulates a tile/room."""
    def __init__(self, x=5, y=5, building=None):
        self.x = x
        self.y = y
        self.building = building
        self.contents = []
        self._messages = []

    def msg_contents(self, text, exclude=None):
        self._messages.append(text)

class FakeCaller:
    """Simulates a player character (caller)."""
    def __init__(self, coord_x=5, coord_y=5, coord_planet="earth_planet",
                 systems=None):
        self.key = "TestPlayer"
        self.db = FakeDB(coord_x, coord_y, coord_planet)
        self.ndb = FakeNDB(systems)
        self.location = FakeLocation(coord_x, coord_y)
        self._messages = []
        self._moved_to = None

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def move_to(self, target, **kwargs):
        self._moved_to = target

    def get_buildings(self):
        return []

class FakePlanetRegistry:
    """Simulates PlanetRegistry with configurable bounds."""
    def __init__(self, width=100, height=100):
        self._width = width
        self._height = height

    def is_valid_coordinate(self, x, y, planet):
        return 0 <= x < self._width and 0 <= y < self._height

class FakeTileResolver:
    """Simulates TileResolver that returns FakeLocation rooms."""
    def __init__(self):
        self._rooms = {}
        self._default_building = None

    def resolve(self, x, y, planet):
        if (x, y) in self._rooms:
            return self._rooms[(x, y)]
        room = FakeLocation(x=x, y=y, building=self._default_building)
        self._rooms[(x, y)] = room
        return room

def _make_cmd(caller, args=""):
    """Create a CmdMove instance wired to a fake caller."""
    cmd = CmdMove()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    return cmd

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

GRID_SIZE = 100

# Positions within a 100x100 grid
position_strategy = st.integers(min_value=0, max_value=GRID_SIZE - 1)

# All four cardinal directions
direction_strategy = st.sampled_from(["north", "south", "east", "west"])

DIRECTION_DELTAS = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
}

# -------------------------------------------------------------- #
#  Property 8: Movement respects coordinate space bounds
#  **Validates: Requirements 1.1, 1.2, 1.3**
# -------------------------------------------------------------- #

class TestProperty8MovementBounds(unittest.TestCase):
    """Property 8: Movement respects coordinate space bounds.

    For any player position (x, y) and direction, if the target
    coordinate (x+dx, y+dy) is within the Coordinate_Space bounds
    (0 <= tx < width, 0 <= ty < height), movement SHALL succeed.
    If the target is outside bounds, movement SHALL be rejected.

    **Validates: Requirements 1.1, 1.2, 1.3**
    """

    @given(
        x=position_strategy,
        y=position_strategy,
        direction=direction_strategy,
    )
    @settings(max_examples=200)
    def test_in_bounds_movement_succeeds(self, x, y, direction):
        """If target is within bounds, caller._moved_to is not None."""
        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy
        assume(0 <= tx < GRID_SIZE and 0 <= ty < GRID_SIZE)

        resolver = FakeTileResolver()
        registry = FakePlanetRegistry(width=GRID_SIZE, height=GRID_SIZE)
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, f" {direction}")
        cmd.func()

        self.assertIsNotNone(
            caller._moved_to,
            f"Movement from ({x},{y}) {direction} to ({tx},{ty}) should succeed "
            f"but caller._moved_to is None. Messages: {caller._messages}",
        )

    @given(data=st.data())
    @settings(max_examples=200)
    def test_out_of_bounds_movement_rejected(self, data):
        """If target is outside bounds, caller._moved_to is None and 'edge' message shown."""
        # Generate edge positions that guarantee out-of-bounds movement
        edge_case = data.draw(st.sampled_from([
            # Moving south from y=0
            (st.integers(min_value=0, max_value=GRID_SIZE - 1), st.just(0), st.just("south")),
            # Moving north from y=max
            (st.integers(min_value=0, max_value=GRID_SIZE - 1), st.just(GRID_SIZE - 1), st.just("north")),
            # Moving west from x=0
            (st.just(0), st.integers(min_value=0, max_value=GRID_SIZE - 1), st.just("west")),
            # Moving east from x=max
            (st.just(GRID_SIZE - 1), st.integers(min_value=0, max_value=GRID_SIZE - 1), st.just("east")),
        ]))
        x = data.draw(edge_case[0])
        y = data.draw(edge_case[1])
        direction = data.draw(edge_case[2])

        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy

        resolver = FakeTileResolver()
        registry = FakePlanetRegistry(width=GRID_SIZE, height=GRID_SIZE)
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, f" {direction}")
        cmd.func()

        self.assertIsNone(
            caller._moved_to,
            f"Movement from ({x},{y}) {direction} to ({tx},{ty}) should be rejected "
            f"but caller._moved_to is {caller._moved_to}",
        )
        self.assertTrue(
            any("edge" in m.lower() for m in caller._messages),
            f"Expected 'edge' message for out-of-bounds move from ({x},{y}) {direction}. "
            f"Messages: {caller._messages}",
        )

# -------------------------------------------------------------- #
#  Property 9: Player coordinate attributes match location after move
#  **Validates: Requirements 1.4**
# -------------------------------------------------------------- #

class TestProperty9CoordAttributesAfterMove(unittest.TestCase):
    """Property 9: Player coordinate attributes match location after movement.

    For any successful movement to coordinate (tx, ty), the
    Player_Character's coord_x and coord_y Attributes SHALL equal
    tx and ty respectively.

    **Validates: Requirements 1.4**
    """

    @given(
        x=position_strategy,
        y=position_strategy,
        direction=direction_strategy,
    )
    @settings(max_examples=200)
    def test_coord_attributes_updated_after_move(self, x, y, direction):
        """After a successful move, coord_x and coord_y match the target."""
        dx, dy = DIRECTION_DELTAS[direction]
        tx, ty = x + dx, y + dy
        # Only test in-bounds moves (successful moves)
        assume(0 <= tx < GRID_SIZE and 0 <= ty < GRID_SIZE)

        resolver = FakeTileResolver()
        registry = FakePlanetRegistry(width=GRID_SIZE, height=GRID_SIZE)
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, f" {direction}")
        cmd.func()

        # Verify move succeeded
        self.assertIsNotNone(caller._moved_to)

        # Verify coordinate attributes
        self.assertEqual(
            caller.db.coord_x, tx,
            f"After moving {direction} from ({x},{y}), coord_x should be {tx} "
            f"but got {caller.db.coord_x}",
        )
        self.assertEqual(
            caller.db.coord_y, ty,
            f"After moving {direction} from ({x},{y}), coord_y should be {ty} "
            f"but got {caller.db.coord_y}",
        )

# -------------------------------------------------------------- #
#  Unit tests for CmdMove (Task 9.4)
#  **Validates: Requirements 1.1, 1.3, 1.6**
# -------------------------------------------------------------- #

class TestCmdMoveDirections(unittest.TestCase):
    """Test movement in all four cardinal directions via resolver."""

    def _move(self, direction, start_x=50, start_y=50):
        """Helper: move from (start_x, start_y) in the given direction."""
        resolver = FakeTileResolver()
        registry = FakePlanetRegistry()
        caller = FakeCaller(
            coord_x=start_x, coord_y=start_y,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, f" {direction}")
        cmd.func()
        return caller

    def test_move_north(self):
        caller = self._move("north")
        self.assertIsNotNone(caller._moved_to)
        self.assertEqual(caller.db.coord_x, 50)
        self.assertEqual(caller.db.coord_y, 51)

    def test_move_south(self):
        caller = self._move("south")
        self.assertIsNotNone(caller._moved_to)
        self.assertEqual(caller.db.coord_x, 50)
        self.assertEqual(caller.db.coord_y, 49)

    def test_move_east(self):
        caller = self._move("east")
        self.assertIsNotNone(caller._moved_to)
        self.assertEqual(caller.db.coord_x, 51)
        self.assertEqual(caller.db.coord_y, 50)

    def test_move_west(self):
        caller = self._move("west")
        self.assertIsNotNone(caller._moved_to)
        self.assertEqual(caller.db.coord_x, 49)
        self.assertEqual(caller.db.coord_y, 50)

class TestCmdMoveEdgeRejection(unittest.TestCase):
    """Test that movement at map edges is rejected."""

    def _move_at_edge(self, x, y, direction):
        resolver = FakeTileResolver()
        registry = FakePlanetRegistry(width=100, height=100)
        caller = FakeCaller(
            coord_x=x, coord_y=y,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, f" {direction}")
        cmd.func()
        return caller

    def test_north_edge(self):
        caller = self._move_at_edge(50, 99, "north")
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("edge" in m.lower() for m in caller._messages))

    def test_south_edge(self):
        caller = self._move_at_edge(50, 0, "south")
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("edge" in m.lower() for m in caller._messages))

    def test_east_edge(self):
        caller = self._move_at_edge(99, 50, "east")
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("edge" in m.lower() for m in caller._messages))

    def test_west_edge(self):
        caller = self._move_at_edge(0, 50, "west")
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("edge" in m.lower() for m in caller._messages))

class TestCmdMoveOfflineBuildingBlocking(unittest.TestCase):
    """Test that offline buildings block movement."""

    def test_offline_building_blocks_move(self):
        class OfflineBuilding:
            is_offline = True

        resolver = FakeTileResolver()
        resolver._default_building = OfflineBuilding()
        registry = FakePlanetRegistry()
        caller = FakeCaller(
            coord_x=50, coord_y=50,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, " north")
        cmd.func()

        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("offline" in m.lower() for m in caller._messages))

    def test_online_building_does_not_block(self):
        class OnlineBuilding:
            is_offline = False

        resolver = FakeTileResolver()
        resolver._default_building = OnlineBuilding()
        registry = FakePlanetRegistry()
        caller = FakeCaller(
            coord_x=50, coord_y=50,
            systems={
                "tile_resolver": resolver,
                "planet_registry": registry,
            },
        )
        cmd = _make_cmd(caller, " north")
        cmd.func()

        self.assertIsNotNone(caller._moved_to)

if __name__ == "__main__":
    unittest.main()
