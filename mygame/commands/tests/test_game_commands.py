"""
Unit tests for player game commands.

Tests each command's validation, error messages, and output formatting
using mocked caller objects.

Requirements: 1.6, 1.10, 2.5, 3.4, 6.8, 6.12, 16.1, 16.2, 16.3,
              16.4, 16.5
"""

import sys
import types
import unittest
from unittest.mock import patch

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

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "")
            self.location = None

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "")
        def at_object_creation(self):
            pass
        def at_post_login(self, session=None, **kwargs):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": DefaultCharacter,
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

from mygame.commands.game_commands import (  # noqa: E402
    CmdMove, CmdHarvest, CmdBuild, CmdUpgrade, CmdRepair,
    CmdAttack, CmdTarget, CmdShoot,
    CmdEquip, CmdUnequip, CmdUse, CmdThrow, CmdReload, CmdCraft,
    CmdDeposit, CmdWithdraw,
    CmdResearch, CmdPowerup,
    CmdScore, CmdEquipment, CmdBuildings, CmdScan, CmdTechnology,
    CmdInventory, CmdMessage, CmdSay, CmdMap,
    CmdCloseExit, CmdOpenExit, CmdExit, CmdGet, CmdEnter, CmdLeave, CmdDrop,
    CmdSell, CmdJunk,
)

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self):
        self.combat_xp = 100
        self.rank_level = 3
        self.hp = 80
        self.hp_max = 100
        self.kills = 7
        self.deaths = 2
        self.resources = {"Iron": 10, "Wood": 5}
        self.researched_techs = {"basic_armor"}
        self.active_powerups = {}
        self.combat_lockout_tick = 0
        self.equipment_slots = {}
        self.coord_x = 5
        self.coord_y = 5
        self.coord_planet = "earth_planet"
        self.discovery_memory = {}

class FakeNDB:
    """Simulates Evennia's ndb attribute handler."""
    def __init__(self, systems=None):
        self.systems = systems or {}
        self.tile_lookup = None
        self.tile_lookup_dict = None

class FakeLocation:
    """Simulates a tile/room (PlanetRoom-compatible)."""
    def __init__(self, x=5, y=5, terrain_type="Plains", building=None,
                 contents=None, planet_name="earth"):
        self.x = x
        self.y = y
        self.terrain_type = terrain_type
        self.building = building
        self.planet_name = planet_name
        self.contents = contents or []
        self._messages = []
        self._buildings_by_coord = {}  # (x, y) -> [building, ...]
        self._objects_by_coord = {}  # (x, y) -> [obj, ...]

    def msg_contents(self, text=None, exclude=None, **kwargs):
        if text is not None:
            if isinstance(text, tuple):
                self._messages.append(text[0])
            else:
                self._messages.append(text)

    def move_entity(self, obj, new_x, new_y):
        """Simulate PlanetRoom.move_entity — update coords on the object."""
        if hasattr(obj, "db"):
            obj.db.coord_x = new_x
            obj.db.coord_y = new_y

    def get_buildings_at(self, x, y):
        """Return buildings registered at (x, y)."""
        return list(self._buildings_by_coord.get((x, y), []))

    def get_players_at(self, x, y):
        """Return player-like objects at (x, y)."""
        result = []
        for obj in self.contents:
            if hasattr(obj, "has_account") and obj.has_account:
                ox = getattr(getattr(obj, "db", None), "coord_x", None)
                oy = getattr(getattr(obj, "db", None), "coord_y", None)
                if ox is not None and oy is not None and int(ox) == x and int(oy) == y:
                    result.append(obj)
            elif hasattr(obj, "db") and hasattr(obj.db, "combat_xp"):
                ox = getattr(obj.db, "coord_x", None)
                oy = getattr(obj.db, "coord_y", None)
                if ox is not None and oy is not None and int(ox) == x and int(oy) == y:
                    result.append(obj)
        return result

    def get_objects_at(self, x, y, type_tag=None):
        """Return objects at (x, y), optionally filtered by type_tag."""
        return list(self._objects_by_coord.get((x, y), []))

    def get_objects_in_area(self, x1, y1, x2, y2):
        """Return contents whose coords fall in the [x1,x2]x[y1,y2] box.

        Mirrors PlanetRoom.get_objects_in_area for area lookups like 'scan'.
        """
        result = []
        for obj in self.contents:
            ox = getattr(getattr(obj, "db", None), "coord_x", None)
            oy = getattr(getattr(obj, "db", None), "coord_y", None)
            if ox is None or oy is None:
                continue
            if x1 <= int(ox) <= x2 and y1 <= int(oy) <= y2:
                result.append(obj)
        return result

class FakeCaller:
    """Simulates a player character (caller)."""
    def __init__(self, name="TestPlayer", location=None, systems=None):
        self.key = name
        self.db = FakeDB()
        self.ndb = FakeNDB(systems)
        self.location = location or FakeLocation()
        self._messages = []
        self._moved_to = None
        self._search_results = {}

    def msg(self, text=None, **kwargs):
        if text is not None:
            # Handle tuple form: (text_str, kwargs_dict)
            if isinstance(text, tuple):
                self._messages.append(text[0])
            else:
                self._messages.append(text)

    def move_to(self, target, **kwargs):
        self._moved_to = target

    def search(self, name, **kwargs):
        return self._search_results.get(name)

    def get_structured_status(self):
        return {
            "name": self.key,
            "hp": self.db.hp,
            "hp_max": self.db.hp_max,
            "rank_level": self.db.rank_level,
            "combat_xp": self.db.combat_xp,
            "resources": dict(self.db.resources),
            "active_powerups": dict(self.db.active_powerups),
        }

    def get_buildings(self):
        return []

    @property
    def equipment(self):
        if not hasattr(self, "_equipment"):
            from mygame.world.systems.equipment_handler import EquipmentHandler
            self._equipment = EquipmentHandler(self)
        return self._equipment

    def _ensure_resources(self):
        return self.db.resources

def _make_cmd(cmd_class, caller, args=""):
    """Create a command instance wired to a fake caller."""
    cmd = cmd_class()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    return cmd


def _hq_building(planet="earth_planet"):
    """An HQ-capability building for a caller's get_buildings() (base is active).

    Building-specific commands (deposit/withdraw/craft/research/exit toggles/
    agent assign) are gated on the owner having an active HQ. A test caller that
    should pass that gate needs one; attach via _give_hq(caller).
    """
    return types.SimpleNamespace(
        db=types.SimpleNamespace(building_type="HQ", under_construction=False),
        location=types.SimpleNamespace(planet_name=planet),
    )


def _give_hq(caller, planet="earth_planet"):
    """Give *caller* a completed HQ so owner_has_active_hq(caller) is True.

    Also requires the active DataRegistry singleton to have an HQ def with the
    headquarters capability (the command-layer gate resolves via the global
    provider). Returns the caller for chaining.
    """
    caller.get_buildings = lambda: [_hq_building(planet)]
    return caller


def _hq_building_def():
    """A headquarters-capability BuildingDef for test registries."""
    from world.definitions import BuildingDef
    return BuildingDef(
        name="Headquarters", abbreviation="HQ", cost={"Wood": 10},
        max_health=500, requires_hq=False, required_terrain=None,
        category="headquarters", produces=None,
        capabilities=frozenset({"headquarters"}),
    )

# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestCmdMove(unittest.TestCase):
    def setUp(self):
        """Register a DataRegistry singleton mapping WL → a combat_barrier.

        The Wall passage check now branches on the ``combat_barrier``
        capability (resolved via the DataRegistry singleton) rather than a
        hardcoded ``building_type == "WL"``.
        """
        from world.data_registry import DataRegistry
        from world.definitions import BuildingDef
        registry = DataRegistry()
        registry.buildings = {
            "WL": BuildingDef(
                name="Wall", abbreviation="WL", cost={"Stone": 5},
                max_health=600, requires_hq=True, required_terrain=None,
                category="defense", produces=None,
                capabilities=frozenset({"combat_barrier"}),
            ),
        }
        DataRegistry.set_instance(registry)

    def tearDown(self):
        from world.data_registry import DataRegistry
        DataRegistry.set_instance(None)

    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdMove, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_invalid_direction(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdMove, caller, " sideways")
        cmd.func()
        self.assertTrue(any("Unknown direction" in m for m in caller._messages))

    def test_no_systems_available(self):
        """When planet_registry is not wired, player gets an error message."""
        caller = FakeCaller()
        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()
        self.assertTrue(any("not available" in m.lower() for m in caller._messages))

    def test_successful_move_via_resolver(self):
        """New path: coordinates update on successful move (no room creation)."""

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return 0 <= x < 100 and 0 <= y < 100

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()
        self.assertEqual(caller.db.coord_x, 5)
        self.assertEqual(caller.db.coord_y, 6)

    def test_edge_of_map_rejected(self):
        """New path: out-of-bounds coordinate is rejected."""
        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return 0 <= x < 100 and 0 <= y < 100

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        caller.db.coord_x = 0
        caller.db.coord_y = 0
        cmd = _make_cmd(CmdMove, caller, " south")
        cmd.func()
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("edge" in m.lower() for m in caller._messages))

    def test_offline_building_blocks_via_resolver(self):
        """New path: offline building blocks movement."""
        class OfflineBuilding:
            is_offline = True

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        loc = FakeLocation()
        loc._buildings_by_coord[(5, 6)] = [OfflineBuilding()]
        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        }, location=loc)
        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("offline" in m.lower() for m in caller._messages))

    def test_coord_attributes_updated_after_move(self):
        """New path: coord_x and coord_y are updated after successful move."""

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        self.assertEqual(caller.db.coord_x, 5)
        self.assertEqual(caller.db.coord_y, 5)
        cmd = _make_cmd(CmdMove, caller, " east")
        cmd.func()
        self.assertEqual(caller.db.coord_x, 6)
        self.assertEqual(caller.db.coord_y, 5)

    def test_no_planet_attribute_rejects(self):
        """When coord_planet is empty and room has no coords, reject movement."""
        class FakePlanetRegistry:
            pass

        # Use a location with no coordinate properties so sync can't help
        class BareLocation:
            id = 99

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        caller.db.coord_planet = ""
        caller.location = BareLocation()
        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("Cannot determine" in m for m in caller._messages))

    # ---------------------------------------------------------- #
    #  Combat timer — Wall passage blocking (Req 17.1-17.4)
    # ---------------------------------------------------------- #

    def _make_wall_building(self, owner_id=42):
        """Create a fake Wall building owned by a specific player."""
        class _AttrStore:
            def __init__(self):
                self._data = {}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value

        class WallBuilding:
            is_offline = False
            def __init__(self, owner_id):
                self.attributes = _AttrStore()
                self.attributes.add("building_type", "WL")
                self.attributes.add("owner", type("Owner", (), {"id": owner_id})())
                self.attributes.add("closed_exits", set())
        return WallBuilding(owner_id)

    def _make_wall_systems(self, wall_building):
        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        return {
            "planet_registry": FakePlanetRegistry(),
        }

    def _make_wall_location(self, wall_building, target_x=5, target_y=6):
        """Create a FakeLocation with a wall building at the target coords."""
        loc = FakeLocation()
        loc._buildings_by_coord[(target_x, target_y)] = [wall_building]
        return loc

    def test_wall_blocks_owner_during_combat_timer(self):
        """Req 17.2: Wall blocks owner movement while combat timer is active."""
        wall = self._make_wall_building(owner_id=42)
        systems = self._make_wall_systems(wall)
        loc = self._make_wall_location(wall, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)
        caller.id = 42  # same as wall owner
        caller.db.combat_timer_expires = 999  # active timer
        old_x, old_y = caller.db.coord_x, caller.db.coord_y

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        # Movement should be blocked
        self.assertEqual(caller.db.coord_x, old_x)
        self.assertEqual(caller.db.coord_y, old_y)
        self.assertTrue(
            any("cannot pass" in m.lower() or "wall" in m.lower()
                for m in caller._messages)
        )

    def test_wall_allows_owner_when_combat_timer_expired(self):
        """Req 17.3: Wall allows owner movement when combat timer is 0."""
        wall = self._make_wall_building(owner_id=42)
        systems = self._make_wall_systems(wall)
        loc = self._make_wall_location(wall, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)
        caller.id = 42
        caller.db.combat_timer_expires = 0  # no active timer

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        # Movement should succeed
        self.assertEqual(caller.db.coord_y, 6)
        self.assertFalse(
            any("cannot pass" in m.lower() for m in caller._messages)
        )

    def test_wall_allows_non_owner_during_combat_timer(self):
        """Walls only block the owner during combat, not other players."""
        wall = self._make_wall_building(owner_id=42)
        systems = self._make_wall_systems(wall)
        loc = self._make_wall_location(wall, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)
        caller.id = 99  # different from wall owner
        caller.db.combat_timer_expires = 999  # active timer

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        # Non-owner should pass through (enemy wall blocking is separate)
        self.assertEqual(caller.db.coord_y, 6)

    # ---------------------------------------------------------- #
    #  Closed exits are symmetric: a closed face blocks entry too
    # ---------------------------------------------------------- #

    def _make_building_with_closed_exits(self, closed, owner=None):
        class _AttrStore:
            def __init__(self):
                self._data = {}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value

        class Building:
            is_offline = False
            def __init__(self):
                self.attributes = _AttrStore()
                self.attributes.add("building_type", "HQ")
                self.attributes.add("closed_exits", set(closed))
                self.attributes.add("owner", owner)
        return Building()

    def test_entering_through_closed_side_blocked(self):
        """Stepping onto a building's tile through its closed face is blocked.

        Building at (5,6) with its SOUTH exit closed. Moving north (from below)
        crosses the building's south face -> refused, and the player does not
        move onto the tile (so they can't then 'enter' by walking around).
        Closes the loophole where a combat-locked owner could re-enter from the
        opposite side. (Moving north checks the opposite/south face — you enter
        from the side you approach, mirroring the auto-enter check.)
        """
        building = self._make_building_with_closed_exits({"south"})
        systems = self._make_wall_systems(building)
        loc = self._make_wall_location(building, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)
        old_x, old_y = caller.db.coord_x, caller.db.coord_y

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        # Did not step onto the tile, did not enter.
        self.assertEqual((caller.db.coord_x, caller.db.coord_y), (old_x, old_y))
        self.assertFalse(getattr(caller.db, "inside_building", False))
        self.assertTrue(
            any("south exit is closed" in m.lower() for m in caller._messages)
        )

    def test_entering_through_open_side_allowed(self):
        """An OPEN face still admits entry — closing one side seals only it.

        Building at (5,6) with only its NORTH exit closed. Moving north crosses
        the (open) south face -> steps on and auto-enters. Proves closing one
        side doesn't seal the others.
        """
        building = self._make_building_with_closed_exits({"north"})
        systems = self._make_wall_systems(building)
        loc = self._make_wall_location(building, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        # Stepped onto the tile and auto-entered through the open south face.
        self.assertEqual((caller.db.coord_x, caller.db.coord_y), (5, 6))
        self.assertTrue(caller.db.inside_building)

    def test_admin_bypasses_closed_side_on_entry(self):
        """Admins are not blocked by a closed face (parity with leave/enter)."""
        building = self._make_building_with_closed_exits({"south"})
        systems = self._make_wall_systems(building)
        loc = self._make_wall_location(building, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)
        # is_admin() checks check_permstring("Builder").
        caller.check_permstring = lambda perm: True

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        self.assertEqual(caller.db.coord_y, 6)  # moved onto the tile (crossed south)

    def test_non_owner_walks_onto_tile_auto_enters(self):
        """Walking onto someone else's building tile auto-enters it.

        Buildings are not private — anyone who walks onto the tile through an
        open face steps inside (being inside is the default interaction).
        """
        other = type("Owner", (), {"id": 777})()
        building = self._make_building_with_closed_exits(set(), owner=other)
        systems = self._make_wall_systems(building)
        loc = self._make_wall_location(building, target_x=5, target_y=6)
        caller = FakeCaller(systems=systems, location=loc)
        caller.id = 1  # not the owner

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        # Moved onto the tile AND auto-entered (no owner gate).
        self.assertEqual((caller.db.coord_x, caller.db.coord_y), (5, 6))
        self.assertTrue(caller.db.inside_building)

    # ---------------------------------------------------------- #
    #  Active-presence pauses on movement (Req 6.6, 6.7)
    # ---------------------------------------------------------- #

    def test_movement_resets_building_activity_state(self):
        """Req 6.6: Moving away from a construction tile pauses building."""
        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        caller.db.activity_state = "building"
        caller.db.activity_target = "some_building"
        caller.db.activity_progress = 10

        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()

        self.assertEqual(caller.db.activity_state, "idle")
        self.assertIsNone(caller.db.activity_target)
        self.assertEqual(caller.db.activity_progress, 0)

    def test_movement_resets_harvesting_activity_state(self):
        """Req 6.7: Moving away from a harvest tile pauses harvesting."""
        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        caller.db.activity_state = "harvesting"
        caller.db.activity_target = "some_tile"
        caller.db.activity_progress = 5

        cmd = _make_cmd(CmdMove, caller, " east")
        cmd.func()

        self.assertEqual(caller.db.activity_state, "idle")
        self.assertIsNone(caller.db.activity_target)
        self.assertEqual(caller.db.activity_progress, 0)

    def test_movement_does_not_reset_idle_state(self):
        """Moving while idle should not cause errors or state changes."""
        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "planet_registry": FakePlanetRegistry(),
        })
        caller.db.activity_state = "idle"

        cmd = _make_cmd(CmdMove, caller, " south")
        cmd.func()

        self.assertEqual(caller.db.activity_state, "idle")


class TestCmdMoveCombatMoveSpeed(unittest.TestCase):
    """Equipment ``move_speed`` alleviates the in-combat movement lag (Req 6.1).

    Out of combat, player movement is always instant. In the combat state
    (``combat_timer_expires`` in the future) a base lag of
    ``COMBAT_MOVE_LAG_TICKS`` applies between steps, reduced by the player's
    equipment ``move_speed`` via ``compute_effective_delay`` — the same
    equipment-derived mechanism agents use.
    """

    class _FakePlanetRegistry:
        def is_valid_coordinate(self, x, y, planet):
            return 0 <= x < 100 and 0 <= y < 100

    def _make_caller(self, move_speed_modifier=None):
        caller = FakeCaller(systems={"planet_registry": self._FakePlanetRegistry()})
        if move_speed_modifier is not None:
            caller._get_move_speed_modifier = lambda: move_speed_modifier
        return caller

    def test_out_of_combat_movement_is_instant(self):
        """No combat timer → no lag; consecutive moves both succeed."""
        from world.constants import COMBAT_MOVE_LAG_TICKS  # noqa: F401
        caller = self._make_caller()
        caller.db.combat_timer_expires = 0  # not in combat

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()
            self.assertEqual(caller.db.coord_y, 6)
            _make_cmd(CmdMove, caller, " north").func()
            self.assertEqual(caller.db.coord_y, 7)

        # No pending lag is set when out of combat.
        self.assertIn(getattr(caller.db, "next_move_tick", 0) or 0, (0,))

    def test_in_combat_sets_base_lag_without_move_speed(self):
        """In combat with no move_speed, lag = COMBAT_MOVE_LAG_TICKS."""
        from world.constants import COMBAT_MOVE_LAG_TICKS
        caller = self._make_caller(move_speed_modifier=0)
        caller.db.combat_timer_expires = 999  # in combat

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()

        # First in-combat move succeeds and schedules the next move.
        self.assertEqual(caller.db.coord_y, 6)
        self.assertEqual(caller.db.next_move_tick, 100 + COMBAT_MOVE_LAG_TICKS)

    def test_move_speed_reduces_in_combat_lag(self):
        """A +1 move_speed shortens the in-combat lag (boosted rate)."""
        from world.constants import COMBAT_MOVE_LAG_TICKS
        caller = self._make_caller(move_speed_modifier=1)
        caller.db.combat_timer_expires = 999

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()

        self.assertEqual(caller.db.coord_y, 6)
        # Lag is one tick shorter than the base — the player moves sooner.
        self.assertEqual(caller.db.next_move_tick, 100 + (COMBAT_MOVE_LAG_TICKS - 1))
        self.assertLess(
            caller.db.next_move_tick, 100 + COMBAT_MOVE_LAG_TICKS
        )

    def test_in_combat_second_move_blocked_until_lag_elapses(self):
        """While the lag is pending, a second in-combat move is rejected."""
        caller = self._make_caller(move_speed_modifier=0)
        caller.db.combat_timer_expires = 999

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()  # succeeds → (5, 6)
            self.assertEqual(caller.db.coord_y, 6)
            # Still tick 100, lag not elapsed → blocked, coords unchanged.
            _make_cmd(CmdMove, caller, " north").func()

        self.assertEqual(caller.db.coord_y, 6)
        self.assertTrue(
            any("repositioning" in m.lower() for m in caller._messages)
        )

    def test_aborted_move_does_not_clear_inside_building(self):
        """TOCTOU regression: a move that is BLOCKED (combat lag) must not strip
        shelter — inside_building stays True since the player never left."""
        caller = self._make_caller(move_speed_modifier=0)
        caller.db.combat_timer_expires = 999  # in combat
        caller.db.inside_building = True       # sheltered in a building

        with patch("world.combat_timer._get_current_tick", return_value=100):
            # Lag not elapsed -> this move is blocked before move_entity.
            caller.db.next_move_tick = 200
            _make_cmd(CmdMove, caller, " north").func()

        # Move was blocked (coords unchanged) AND shelter preserved.
        self.assertEqual(caller.db.coord_y, 5)
        self.assertTrue(
            caller.db.inside_building,
            "an aborted move must not clear inside_building",
        )

    def test_edge_of_map_move_does_not_clear_inside_building(self):
        """A move blocked by the map edge also preserves shelter."""
        caller = self._make_caller()
        caller.db.combat_timer_expires = 0  # not in combat
        caller.db.inside_building = True
        caller.db.coord_x, caller.db.coord_y = 0, 0  # at the SW corner

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " south").func()  # off the map

        self.assertEqual((caller.db.coord_x, caller.db.coord_y), (0, 0))
        self.assertTrue(caller.db.inside_building)

    def test_successful_move_clears_inside_building(self):
        """A move that actually happens DOES leave the building (shelter drops)."""
        caller = self._make_caller()
        caller.db.combat_timer_expires = 0
        caller.db.inside_building = True

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()

        self.assertEqual(caller.db.coord_y, 6)
        self.assertFalse(caller.db.inside_building)

    def test_large_move_speed_never_drops_lag_below_one(self):
        """A huge move_speed clamps the lag to a minimum of 1 tick."""
        caller = self._make_caller(move_speed_modifier=99)
        caller.db.combat_timer_expires = 999

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()

        self.assertEqual(caller.db.coord_y, 6)
        self.assertEqual(caller.db.next_move_tick, 100 + 1)

    def test_admin_has_no_combat_move_lag(self):
        """Admins move freely in combat — no lag scheduled, moves not blocked."""
        caller = self._make_caller(move_speed_modifier=0)
        caller.db.combat_timer_expires = 999  # in combat
        caller.check_permstring = lambda perm: True  # is_admin() true

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()  # (5,5) -> (5,6)
            self.assertEqual(caller.db.coord_y, 6)
            # Second consecutive move also succeeds (no pending lag gate).
            _make_cmd(CmdMove, caller, " north").func()

        self.assertEqual(caller.db.coord_y, 7)
        self.assertEqual(getattr(caller.db, "next_move_tick", 0) or 0, 0)
        self.assertFalse(
            any("repositioning" in m.lower() for m in caller._messages)
        )


class TestCmdHarvest(unittest.TestCase):
    def test_no_system(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdHarvest, caller)
        cmd.func()
        self.assertTrue(any("unavailable" in m.lower() for m in caller._messages))

    def test_success(self):
        class FakeResourceSystem:
            def start_harvest(self, player, tile):
                return True, "You begin harvesting Iron. Stay on the tile to continue."
        caller = FakeCaller(systems={
            "resource_system": FakeResourceSystem(),
        })
        cmd = _make_cmd(CmdHarvest, caller)
        cmd.func()
        self.assertTrue(any("harvesting" in m.lower() for m in caller._messages))

    def test_no_resource(self):
        class FakeResourceSystem:
            def start_harvest(self, player, tile):
                return False, "No resource node on this tile."
        caller = FakeCaller(systems={
            "resource_system": FakeResourceSystem(),
        })
        cmd = _make_cmd(CmdHarvest, caller)
        cmd.func()
        self.assertTrue(any("No resource" in m for m in caller._messages))

class TestCmdBuild(unittest.TestCase):
    def test_no_args(self):
        class FakeBuildingSystem:
            pass
        caller = FakeCaller(systems={
            "building_system": FakeBuildingSystem(),
        })
        cmd = _make_cmd(CmdBuild, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_success(self):
        class FakeBuildingSystem:
            def start_construction(self, player, tile, btype, x=None, y=None):
                return True, f"Construction of {btype} started (0/120s). Stay on the tile to continue."
        caller = FakeCaller(systems={
            "building_system": FakeBuildingSystem(),
        })
        cmd = _make_cmd(CmdBuild, caller, " hq")
        cmd.func()
        self.assertTrue(any("Construction" in m for m in caller._messages))

class TestCmdUpgrade(unittest.TestCase):
    def test_no_building_on_tile(self):
        class FakeBuildingSystem:
            pass
        caller = FakeCaller(systems={
            "building_system": FakeBuildingSystem(),
        })
        cmd = _make_cmd(CmdUpgrade, caller, "")
        cmd.func()
        self.assertTrue(any("No building" in m for m in caller._messages))

class TestCmdRepair(unittest.TestCase):
    def test_system_unavailable(self):
        caller = FakeCaller()
        _make_cmd(CmdRepair, caller, "").func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_no_building_on_tile(self):
        caller = FakeCaller(systems={"building_system": object()})
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        _make_cmd(CmdRepair, caller, "").func()
        self.assertTrue(any("No building" in m for m in caller._messages))

    def test_delegates_to_building_system(self):
        calls = []

        class FakeBuildingSystem:
            def repair(self, player, building):
                calls.append((player, building))
                return True, "Repaired HQ +100 HP to 500/500 (cost: 5 Iron)."

        caller = FakeCaller(systems={"building_system": FakeBuildingSystem()})
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        building = types.SimpleNamespace(key="HQ", db=types.SimpleNamespace(building_type="HQ"))
        caller.location._buildings_by_coord[(5, 5)] = [building]
        _make_cmd(CmdRepair, caller, "").func()
        self.assertEqual(calls, [(caller, building)])
        self.assertTrue(any("Repaired HQ" in m for m in caller._messages))

    def test_rep_alias_registered(self):
        self.assertIn("rep", CmdRepair.aliases)


class TestCmdAttack(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdAttack, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_nothing_in_view_to_attack(self):
        """With no attackables in view, the command says so (it no longer
        searches the whole planet)."""
        caller = FakeCaller(systems={"combat_engine": object()})
        cmd = _make_cmd(CmdAttack, caller, " ghost")
        cmd.func()
        self.assertTrue(any("nothing in view" in m for m in caller._messages))

    def test_named_target_not_in_view(self):
        """A named target that isn't among the in-view attackables is rejected
        with a 'don't see it nearby' message — never targets across the map."""
        caller = FakeCaller(systems={"combat_engine": object()})
        # Put an unrelated attackable in view so the list is non-empty.
        caller.location.contents = [_FakeAttackable("Wolf", 5, 6)]
        cmd = _make_cmd(CmdAttack, caller, " ghost")
        cmd.func()
        self.assertTrue(any("don't see" in m for m in caller._messages))

    def test_attacks_in_view_target_by_name(self):
        """A named target within view is resolved and queued for attack."""
        queued = []

        class _Engine:
            def queue_attack(self, attacker, target):
                queued.append((attacker, target))
                return True, "Attack queued."

        caller = FakeCaller(systems={"combat_engine": _Engine()})
        guard = _FakeAttackable("Outpost #2 Guard-1", 5, 6)
        caller.location.contents = [guard]
        cmd = _make_cmd(CmdAttack, caller, " guard")
        cmd.func()
        self.assertEqual(len(queued), 1)
        self.assertIs(queued[0][1], guard)

    def test_attack_only_sees_in_view_not_whole_map(self):
        """A guard far outside vision radius is NOT targetable even by name —
        the target list is scoped to the player's view, not the whole planet."""
        queued = []

        class _Engine:
            def queue_attack(self, attacker, target):
                queued.append((attacker, target))
                return True, "Attack queued."

        caller = FakeCaller(systems={"combat_engine": _Engine()})
        faraway = _FakeAttackable("Outpost #9 Guard-1", 90, 90)
        caller.location.contents = [faraway]
        cmd = _make_cmd(CmdAttack, caller, " guard")
        cmd.func()
        self.assertEqual(queued, [])
        self.assertTrue(any("nothing in view" in m or "don't see" in m
                            for m in caller._messages))

    def test_long_range_weapon_extends_attack_reach_beyond_vision(self):
        """A weapon whose range exceeds base vision (e.g. a sniper rifle,
        range 12 > vision 10) can still target a foe within weapon range — the
        target search radius is max(vision, weapon range), not vision alone."""
        queued = []

        class _Engine:
            def queue_attack(self, attacker, target):
                queued.append(target)
                return True, "queued"

        caller = FakeCaller(systems={"combat_engine": _Engine()})
        # Equip a range-12 weapon (no registry → vision defaults to 10).
        caller.equipment.equip(_FakeRangedWeapon("sniper_rifle", weapon_range=12))
        # Foe at Chebyshev distance 12 — beyond vision (10) but within reach.
        caller.location.contents = [_FakeAttackable("Outpost #1 Soldier-1", 17, 5)]
        cmd = _make_cmd(CmdAttack, caller, " soldier")
        cmd.func()
        self.assertEqual(len(queued), 1)


class _FakeAttackable:
    """An in-view attackable NPC (carries combat_xp so is_player matches)."""
    def __init__(self, key, x, y):
        self.key = key
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, combat_xp=0, npc_type="enemy",
        )


class _FakeRangedWeapon:
    """A weapon Game_Item exposing slot + a range stat for reach tests."""
    def __init__(self, key, weapon_range):
        self.key = key
        self.slot = "weapon"
        self.stat_modifiers = {"range": weapon_range}

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class _FakeTargeting:
    """Records target/shoot interactions for command tests."""
    def __init__(self, ranged=True, locked=False, target=None, in_range=True):
        self._ranged = ranged
        self._locked = locked
        self._target = target
        self._in_range = in_range
        self.acquired = []
        self.cleared = []

    def get_ranged_weapon(self, player):
        return object() if self._ranged else None

    def weapon_range(self, weapon):
        return 8

    def in_weapon_range(self, player, target, weapon):
        return self._in_range

    def clear_lock(self, player, reason=None):
        self.cleared.append(reason)
        self._target = None

    def acquire(self, player, target):
        self.acquired.append(target)
        return True, ""

    def get_target(self, player):
        return self._target

    def is_locked(self, player):
        return self._locked

    def targeted_accuracy(self, weapon):
        return 0.8

    def directional_accuracy(self, weapon):
        return 0.5


class _RecordingEngine:
    def __init__(self):
        self.calls = []

    def queue_attack(self, attacker, target, weapon=None, accuracy=None):
        self.calls.append((target, accuracy))
        return True, ""


class TestCmdTarget(unittest.TestCase):
    def test_requires_ranged_weapon(self):
        tg = _FakeTargeting(ranged=False)
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": object()})
        caller.location.contents = [_FakeAttackable("Guard", 5, 6)]
        cmd = _make_cmd(CmdTarget, caller, " guard")
        cmd.func()
        self.assertTrue(any("ranged weapon" in m for m in caller._messages))
        self.assertEqual(tg.acquired, [])

    def test_locks_onto_in_view_enemy(self):
        tg = _FakeTargeting(ranged=True)
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": object()})
        guard = _FakeAttackable("Outpost #1 Guard-1", 5, 6)
        caller.location.contents = [guard]
        cmd = _make_cmd(CmdTarget, caller, " guard")
        cmd.func()
        self.assertEqual(tg.acquired, [guard])

    def test_no_args_shows_usage(self):
        tg = _FakeTargeting()
        caller = FakeCaller(systems={"targeting_system": tg})
        cmd = _make_cmd(CmdTarget, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


class TestCmdShoot(unittest.TestCase):
    def test_requires_ranged_weapon(self):
        tg = _FakeTargeting(ranged=False)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, "")
        cmd.func()
        self.assertTrue(any("ranged weapon" in m for m in caller._messages))
        self.assertEqual(engine.calls, [])

    def test_shoot_locked_target_uses_targeted_accuracy(self):
        target = _FakeAttackable("Guard", 3, 0)
        tg = _FakeTargeting(ranged=True, locked=True, target=target)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, "")
        cmd.func()
        self.assertEqual(engine.calls, [(target, 0.8)])

    def test_shoot_locked_out_of_range_breaks_lock_no_shot(self):
        """If the locked target stepped out of range this tick, 'shoot' refuses
        with feedback and clears the lock instead of wasting ammo silently."""
        target = _FakeAttackable("Guard", 30, 0)
        tg = _FakeTargeting(ranged=True, locked=True, target=target,
                            in_range=False)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, "")
        cmd.func()
        self.assertEqual(engine.calls, [])  # no shot queued
        self.assertEqual(tg.cleared, ["out_of_range"])
        self.assertTrue(any("out of range" in m for m in caller._messages))

    def test_shoot_no_lock_no_dir_prompts(self):
        tg = _FakeTargeting(ranged=True, locked=False, target=None)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, "")
        cmd.func()
        self.assertEqual(engine.calls, [])
        self.assertTrue(any("No target locked" in m for m in caller._messages))

    def test_shoot_still_locking_holds_fire(self):
        target = _FakeAttackable("Guard", 3, 0)
        tg = _FakeTargeting(ranged=True, locked=False, target=target)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, "")
        cmd.func()
        self.assertEqual(engine.calls, [])
        self.assertTrue(any("locking on" in m.lower() for m in caller._messages))

    def test_shoot_directional_hits_first_in_line_at_directional_accuracy(self):
        tg = _FakeTargeting(ranged=True)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        # Caller at (5,5); a foe two tiles north at (5,7).
        foe = _FakeAttackable("Guard", 5, 7)
        caller.location._objects_by_coord[(5, 7)] = [foe]
        cmd = _make_cmd(CmdShoot, caller, " north")
        cmd.func()
        self.assertEqual(engine.calls, [(foe, 0.5)])

    def test_shoot_directional_nothing_in_line(self):
        tg = _FakeTargeting(ranged=True)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, " north")
        cmd.func()
        self.assertEqual(engine.calls, [])
        self.assertTrue(any("line of fire" in m for m in caller._messages))

    def test_shoot_bad_direction(self):
        tg = _FakeTargeting(ranged=True)
        engine = _RecordingEngine()
        caller = FakeCaller(systems={"targeting_system": tg,
                                     "combat_engine": engine})
        cmd = _make_cmd(CmdShoot, caller, " up")
        cmd.func()
        self.assertEqual(engine.calls, [])


class TestCmdEquip(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdEquip, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_item_not_found(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdEquip, caller, " sword")
        cmd.func()
        self.assertTrue(any("Could not find" in m for m in caller._messages))

    def test_system_unavailable(self):
        caller = FakeCaller()
        caller._search_results = {"sword": object()}
        cmd = _make_cmd(CmdEquip, caller, " sword")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_delegates_to_equipment_system(self):
        calls = []

        class FakeEquipmentSystem:
            def equip(self, player, item):
                calls.append((player, item))
                return True

        item = object()
        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        caller._search_results = {"sword": item}
        cmd = _make_cmd(CmdEquip, caller, " sword")
        cmd.func()
        self.assertEqual(calls, [(caller, item)])

    def test_wear_alias_registered(self):
        self.assertIn("wear", CmdEquip.aliases)

    def test_equip_all_delegates_sorted_loose_items(self):
        """equip all delegates to equipment_system.equip_all with items
        sorted deterministically by item_key (not contents order)."""
        calls = []

        class FakeEquipmentSystem:
            def equip(self, player, item):
                return True
            def equip_all(self, player, items):
                calls.append(items)
                return len(items)

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        # NOTE: contents order is helmet-then-rifle, but item_key sort makes
        # assault_rifle < combat_helmet, so the delegate receives rifle first.
        helmet = types.SimpleNamespace(
            key="Combat Helmet",
            db=types.SimpleNamespace(item_key="combat_helmet", count=None),
        )
        rifle = types.SimpleNamespace(
            key="Assault Rifle",
            db=types.SimpleNamespace(item_key="assault_rifle", count=None),
        )
        caller.contents = [helmet, rifle]
        _make_cmd(CmdEquip, caller, " all").func()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], [rifle, helmet])  # sorted by item_key

    def test_equip_all_excludes_supply_drops(self):
        calls = []

        class FakeEquipmentSystem:
            def equip(self, player, item):
                return True
            def equip_all(self, player, items):
                calls.append(items)
                return len(items)

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        rifle = types.SimpleNamespace(
            key="Assault Rifle",
            db=types.SimpleNamespace(item_key="assault_rifle", count=None),
        )
        ammo_drop = types.SimpleNamespace(
            key="Ammo",
            db=types.SimpleNamespace(item_key="rifle_rounds", count=30),
        )
        caller.contents = [rifle, ammo_drop]
        _make_cmd(CmdEquip, caller, " all").func()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], [rifle])  # counted supply drop excluded

    def test_equip_all_when_nothing_carried(self):
        calls = []

        class FakeEquipmentSystem:
            def equip(self, player, item):
                calls.append(item)
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        caller.contents = []
        _make_cmd(CmdEquip, caller, " all").func()
        self.assertEqual(calls, [])
        self.assertTrue(any("no carried gear" in m.lower() for m in caller._messages))

class TestCmdUnequip(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdUnequip, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_system_unavailable(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdUnequip, caller, " weapon")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    @staticmethod
    def _equip_item(caller, slot, name, key=None):
        """Put a simple item into an occupied slot on the fake caller."""
        item = types.SimpleNamespace(slot=slot, name=name, key=key or name)
        caller.equipment.equip(item)
        return item

    def test_delegates_by_slot_name(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        self._equip_item(caller, "weapon", "Assault Rifle", "assault_rifle")
        cmd = _make_cmd(CmdUnequip, caller, " weapon")
        cmd.func()
        self.assertEqual(calls, [(caller, "weapon")])

    def test_slot_name_case_insensitive(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        self._equip_item(caller, "weapon", "Assault Rifle", "assault_rifle")
        cmd = _make_cmd(CmdUnequip, caller, " WEAPON")
        cmd.func()
        self.assertEqual(calls, [(caller, "weapon")])

    def test_delegates_by_item_name(self):
        # UX #1: accept the item's name, resolving it to its slot.
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        self._equip_item(caller, "weapon", "Assault Rifle", "assault_rifle")
        # By display name (case/space-insensitive) and by key both resolve.
        _make_cmd(CmdUnequip, caller, " assault rifle").func()
        _make_cmd(CmdUnequip, caller, " assault_rifle").func()
        self.assertEqual(calls, [(caller, "weapon"), (caller, "weapon")])

    def test_no_match_reports_and_does_not_delegate(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        # Nothing equipped in 'head', and no item named 'boots'.
        _make_cmd(CmdUnequip, caller, " boots").func()
        self.assertEqual(calls, [])
        self.assertTrue(any("nothing equipped" in m.lower() for m in caller._messages))

    def test_delegates_by_partial_item_name(self):
        # The fix: "unequip assault" resolves to the Assault Rifle's slot,
        # matching the leniency of "equip assault".
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        self._equip_item(caller, "weapon", "Assault Rifle", "assault_rifle")
        _make_cmd(CmdUnequip, caller, " assault").func()
        self.assertEqual(calls, [(caller, "weapon")])

    def test_ambiguous_partial_name_reports_and_does_not_delegate(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        # Two equipped items both start with "combat".
        self._equip_item(caller, "head", "Combat Helmet", "combat_helmet")
        self._equip_item(caller, "torso", "Combat Vest", "combat_vest")
        _make_cmd(CmdUnequip, caller, " combat").func()
        self.assertEqual(calls, [])
        self.assertTrue(any("more than one" in m.lower() for m in caller._messages))

    def test_unequip_all_clears_every_slot(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append(slot)
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        self._equip_item(caller, "weapon", "Assault Rifle", "assault_rifle")
        self._equip_item(caller, "head", "Combat Helmet", "combat_helmet")
        _make_cmd(CmdUnequip, caller, " all").func()
        self.assertEqual(sorted(calls), ["head", "weapon"])

    def test_unequip_all_when_nothing_equipped(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append(slot)
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        _make_cmd(CmdUnequip, caller, " all").func()
        self.assertEqual(calls, [])
        self.assertTrue(any("nothing equipped" in m.lower() for m in caller._messages))

    def test_remove_alias_registered(self):
        self.assertIn("remove", CmdUnequip.aliases)


class _FakeItemDef:
    """Minimal Item_Def stand-in exposing a canonical ``key``."""
    def __init__(self, key):
        self.key = key


class _FakeRegistry:
    """Registry stub whose ``resolve_item`` maps tokens to Item_Defs.

    Matching mirrors the real registry's space/underscore-insensitive,
    case-insensitive behavior against a small key set.
    """
    def __init__(self, keys):
        self._keys = set(keys)

    def resolve_item(self, token):
        if not token:
            return None
        norm = token.strip().lower().replace("_", " ")
        for key in self._keys:
            if key.lower().replace("_", " ") == norm:
                return _FakeItemDef(key)
        return None


class _FakeTarget:
    """A searchable target carrying coordinates on its ``db``."""
    def __init__(self, x, y):
        self.db = FakeDB()
        self.db.coord_x = x
        self.db.coord_y = y


class TestCmdUse(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdUse, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_system_unavailable(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdUse, caller, " medkit")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_unknown_item(self):
        caller = FakeCaller(systems={
            "equipment_system": object(),
            "registry": _FakeRegistry({"medkit"}),
        })
        cmd = _make_cmd(CmdUse, caller, " nonsense")
        cmd.func()
        self.assertTrue(any("Unknown item" in m for m in caller._messages))

    def test_delegates_to_equipment_system(self):
        calls = []

        class FakeEquipmentSystem:
            def use(self, player, item_key):
                calls.append((player, item_key))
                return True

        caller = FakeCaller(systems={
            "equipment_system": FakeEquipmentSystem(),
            "registry": _FakeRegistry({"medkit"}),
        })
        cmd = _make_cmd(CmdUse, caller, " medkit")
        cmd.func()
        self.assertEqual(calls, [(caller, "medkit")])


class TestCmdThrow(unittest.TestCase):
    def _system(self, calls):
        class FakeEquipmentSystem:
            def throw(self, player, item_key, tx, ty):
                calls.append((player, item_key, tx, ty))
                return True
        return FakeEquipmentSystem()

    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdThrow, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_missing_target(self):
        caller = FakeCaller(systems={
            "equipment_system": self._system([]),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_explicit_coordinates(self):
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade 12 8")
        cmd.func()
        self.assertEqual(calls, [(caller, "frag_grenade", 12, 8)])

    def test_negative_coordinates(self):
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade -3 -4")
        cmd.func()
        self.assertEqual(calls, [(caller, "frag_grenade", -3, -4)])

    def test_multiword_item_name_with_coords(self):
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        cmd = _make_cmd(CmdThrow, caller, " frag grenade 1 2")
        cmd.func()
        self.assertEqual(calls, [(caller, "frag_grenade", 1, 2)])

    def test_target_name_resolves_to_coords(self):
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        caller._search_results = {"goblin": _FakeTarget(7, 9)}
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade goblin")
        cmd.func()
        self.assertEqual(calls, [(caller, "frag_grenade", 7, 9)])

    def test_target_not_found(self):
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade ghost")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(any("Could not find" in m for m in caller._messages))

    def test_unknown_item(self):
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        cmd = _make_cmd(CmdThrow, caller, " rock 1 2")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(any("Unknown item" in m for m in caller._messages))

    def test_target_found_but_missing_coordinates(self):
        """A resolvable target with no coordinates aborts before delegating."""
        calls = []
        caller = FakeCaller(systems={
            "equipment_system": self._system(calls),
            "registry": _FakeRegistry({"frag_grenade"}),
        })
        # Target resolves via search but carries no coord_x/coord_y.
        caller._search_results = {"blob": types.SimpleNamespace(db=types.SimpleNamespace())}
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade blob")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(
            any("Cannot determine the position" in m for m in caller._messages)
        )

    def test_system_unavailable(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdThrow, caller, " frag_grenade 1 2")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))


class TestCmdReload(unittest.TestCase):
    def test_system_unavailable(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdReload, caller, "")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_delegates_to_equipment_system(self):
        calls = []

        class FakeEquipmentSystem:
            def reload(self, player):
                calls.append(player)
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        cmd = _make_cmd(CmdReload, caller, "")
        cmd.func()
        self.assertEqual(calls, [caller])


class TestCmdCraft(unittest.TestCase):
    def _caller_in_building(self, equipment_system, building_type="AR"):
        caller = FakeCaller(systems={"equipment_system": equipment_system})
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        building = types.SimpleNamespace(
            key=building_type,
            db=types.SimpleNamespace(building_type=building_type),
        )
        caller.location._buildings_by_coord[(5, 5)] = [building]
        return caller, building

    def test_system_unavailable(self):
        caller = FakeCaller()
        _make_cmd(CmdCraft, caller, " assault_rifle").func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_delegates_to_equipment_system_with_building(self):
        calls = []

        class FakeEquipmentSystem:
            def craft(self, player, token, building):
                calls.append((player, token, building))
                return True

        eqsys = FakeEquipmentSystem()
        caller, building = self._caller_in_building(eqsys)
        _make_cmd(CmdCraft, caller, " assault_rifle").func()
        self.assertEqual(calls, [(caller, "assault_rifle", building)])

    def test_no_arg_lists_craftable(self):
        # With a registry + AR building, a bare 'craft' lists the catalog.
        from world.data_registry import DataRegistry
        from world.definitions import ItemDef

        registry = DataRegistry()
        registry.items = {
            "assault_rifle": ItemDef(key="assault_rifle", name="Assault Rifle",
                                     slot="weapon", category="weapon",
                                     craft_cost={"Iron": 25}),
        }
        registry.item_production_map = {"AR": ["assault_rifle"]}

        class FakeEquipmentSystem:
            def craft(self, *a):
                raise AssertionError("should not craft on bare command")

        caller, _b = self._caller_in_building(FakeEquipmentSystem())
        caller.ndb.systems["registry"] = registry
        _make_cmd(CmdCraft, caller, "").func()
        output = "\n".join(caller._messages)
        self.assertIn("assault_rifle", output)
        self.assertIn("25 Iron", output)

    def test_no_arg_outside_building_guides(self):
        caller = FakeCaller(systems={"equipment_system": object()})
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        # No building registered here.
        _make_cmd(CmdCraft, caller, "").func()
        output = "\n".join(caller._messages).lower()
        self.assertIn("equipment building", output)

    def test_make_alias_registered(self):
        self.assertIn("make", CmdCraft.aliases)


class TestCmdEnterLeave(unittest.TestCase):
    """enter / leave a building without moving off the tile."""

    def _building(self, offline=False, owner=None):
        return types.SimpleNamespace(
            key="Armory",
            is_offline=offline,
            db=types.SimpleNamespace(
                building_type="AR", closed_exits=None, owner=owner
            ),
        )

    def _caller_on_building(self, building):
        caller = FakeCaller()
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        caller.db.inside_building = False
        caller.location._buildings_by_coord[(5, 5)] = [building]
        return caller

    def test_enter_does_not_crash_and_sets_flag(self):
        """Regression: 'enter' used to call CmdMove._update_fog_and_render,
        which doesn't exist on CmdEnter, raising AttributeError."""
        caller = self._caller_on_building(self._building())
        _make_cmd(CmdEnter, caller, "").func()
        self.assertTrue(caller.db.inside_building)

    def test_enter_then_leave_then_reenter(self):
        """The full leave -> re-enter cycle from the bug report works."""
        building = self._building()
        caller = self._caller_on_building(building)
        _make_cmd(CmdEnter, caller, "").func()
        self.assertTrue(caller.db.inside_building)
        _make_cmd(CmdLeave, caller, "").func()
        self.assertFalse(caller.db.inside_building)
        _make_cmd(CmdEnter, caller, "").func()  # must not raise
        self.assertTrue(caller.db.inside_building)

    def test_enter_no_building_here(self):
        caller = FakeCaller()
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        caller.db.inside_building = False
        _make_cmd(CmdEnter, caller, "").func()
        self.assertFalse(caller.db.inside_building)
        self.assertTrue(any("no building here" in m.lower() for m in caller._messages))

    def test_enter_when_already_inside(self):
        building = self._building()
        caller = self._caller_on_building(building)
        caller.db.inside_building = True
        _make_cmd(CmdEnter, caller, "").func()
        self.assertTrue(any("already inside" in m.lower() for m in caller._messages))

    def test_non_owner_can_enter_building(self):
        """Buildings are not private: a non-owner can 'enter' someone else's."""
        other = types.SimpleNamespace(id=999)  # a different owner
        building = self._building(owner=other)
        caller = self._caller_on_building(building)
        caller.id = 1  # not the owner
        _make_cmd(CmdEnter, caller, "").func()
        self.assertTrue(caller.db.inside_building)

    def test_non_owner_can_leave_building(self):
        """A non-owner inside can step back out (no owner gate on the door)."""
        other = types.SimpleNamespace(id=999)
        building = self._building(owner=other)
        caller = self._caller_on_building(building)
        caller.id = 1  # not the owner
        caller.db.inside_building = True
        _make_cmd(CmdLeave, caller, "").func()
        self.assertFalse(caller.db.inside_building)

    def test_enter_sealed_building_blocked(self):
        """A fully-sealed building (all exits closed) can't be entered."""
        building = self._building()
        building.db.closed_exits = {"north", "south", "east", "west"}
        caller = self._caller_on_building(building)
        _make_cmd(CmdEnter, caller, "").func()
        self.assertFalse(caller.db.inside_building)
        self.assertTrue(any("sealed" in m.lower() for m in caller._messages))

    def test_enter_blocked_in_combat(self):
        """You can't manually enter a building while in combat."""
        building = self._building()
        caller = self._caller_on_building(building)
        caller.db.combat_timer_expires = 999  # in combat
        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdEnter, caller, "").func()
        self.assertFalse(caller.db.inside_building)
        self.assertTrue(any("in combat" in m.lower() for m in caller._messages))

    def test_leave_blocked_in_combat(self):
        """You can't manually leave a building while in combat."""
        building = self._building()
        caller = self._caller_on_building(building)
        caller.db.inside_building = True
        caller.db.combat_timer_expires = 999  # in combat
        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdLeave, caller, "").func()
        self.assertTrue(caller.db.inside_building)  # still inside (blocked)
        self.assertTrue(any("in combat" in m.lower() for m in caller._messages))

    def test_enter_allowed_when_combat_expired(self):
        """An expired combat timer no longer blocks entry."""
        building = self._building()
        caller = self._caller_on_building(building)
        caller.db.combat_timer_expires = 50  # expiry in the past
        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdEnter, caller, "").func()
        self.assertTrue(caller.db.inside_building)


class _StorageCommandBase(unittest.TestCase):
    """Shared setup for deposit/withdraw command tests (Req 12.8, 16.3).

    Publishes a DataRegistry singleton mapping ``VT`` to a ``storage``-capability
    building so ``find_storage_building`` (via ``building_has_capability``)
    resolves. Builds a caller standing at (5, 5) with the storage building
    co-located there.
    """

    def setUp(self):
        from world.data_registry import DataRegistry
        from world.definitions import BuildingDef
        registry = DataRegistry()
        registry.buildings = {
            "VT": BuildingDef(
                name="Vault", abbreviation="VT", cost={"Stone": 5},
                max_health=600, requires_hq=True, required_terrain=None,
                category="storage", produces=None,
                capabilities=frozenset({"storage"}),
            ),
            # HQ so the storage owner passes the base-deactivation gate.
            "HQ": _hq_building_def(),
        }
        DataRegistry.set_instance(registry)

    def tearDown(self):
        from world.data_registry import DataRegistry
        DataRegistry.set_instance(None)

    @staticmethod
    def _storage_building(btype="VT", owner=None):
        return types.SimpleNamespace(
            db=types.SimpleNamespace(building_type=btype, owner=owner)
        )

    def _caller_with_storage(self, system, btype="VT"):
        """Caller standing at a storage building THEY OWN (deposit/withdraw
        require ownership). Owner match is by shared ``.id``."""
        loc = FakeLocation()
        caller = FakeCaller(systems={"equipment_system": system}, location=loc)
        caller.id = 7
        _give_hq(caller)  # owner has an active HQ (base not deactivated)
        building = self._storage_building(btype, owner=caller)
        loc._buildings_by_coord[(5, 5)] = [building]
        return caller, building


class TestCmdDeposit(_StorageCommandBase):
    """The ``deposit <resource> <amount>`` command (Req 12.8, 16.3, 16.9)."""

    class _FakeEquipmentSystem:
        def __init__(self, calls):
            self._calls = calls

        def deposit(self, player, building, resource, amount):
            self._calls.append((player, building, resource, amount))
            return amount

    def test_system_unavailable(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdDeposit, caller, " iron 100")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_usage_guard_no_args(self):
        caller, _ = self._caller_with_storage(object())
        cmd = _make_cmd(CmdDeposit, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_usage_guard_non_int_amount(self):
        caller, _ = self._caller_with_storage(object())
        cmd = _make_cmd(CmdDeposit, caller, " iron lots")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_no_storage_building_here(self):
        calls = []
        caller = FakeCaller(
            systems={"equipment_system": self._FakeEquipmentSystem(calls)},
            location=FakeLocation(),
        )
        cmd = _make_cmd(CmdDeposit, caller, " iron 100")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(any("No storage building" in m for m in caller._messages))

    def test_not_owner_rejected(self):
        # Another player's building at the same tile — blocked.
        calls = []
        other_owner = types.SimpleNamespace(id=999)
        loc = FakeLocation()
        building = self._storage_building("VT", owner=other_owner)
        loc._buildings_by_coord[(5, 5)] = [building]
        caller = FakeCaller(
            systems={"equipment_system": self._FakeEquipmentSystem(calls)},
            location=loc,
        )
        caller.id = 7
        cmd = _make_cmd(CmdDeposit, caller, " iron 100")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(any("do not own" in m for m in caller._messages))

    def test_blocked_when_base_deactivated(self):
        """No depositing while the owner's base has no HQ (deactivated)."""
        calls = []
        caller, _building = self._caller_with_storage(
            self._FakeEquipmentSystem(calls)
        )
        caller.get_buildings = lambda: []  # HQ destroyed -> deactivated
        cmd = _make_cmd(CmdDeposit, caller, " iron 100")
        cmd.func()
        self.assertEqual(calls, [])  # system not called
        self.assertTrue(
            any("deactivated" in m.lower() for m in caller._messages)
        )

    def test_delegates_and_title_cases_resource(self):
        calls = []
        caller, building = self._caller_with_storage(
            self._FakeEquipmentSystem(calls)
        )
        cmd = _make_cmd(CmdDeposit, caller, " iron 100")
        cmd.func()
        self.assertEqual(calls, [(caller, building, "Iron", 100)])

    def test_bare_resource_means_all(self):
        # `deposit iron` (no amount) → amount None ("all held"), per Req 12.8.
        calls = []
        caller, building = self._caller_with_storage(
            self._FakeEquipmentSystem(calls)
        )
        cmd = _make_cmd(CmdDeposit, caller, " iron")
        cmd.func()
        self.assertEqual(calls, [(caller, building, "Iron", None)])

    def test_all_keyword_means_all(self):
        # `deposit iron all` → amount None ("all held"), per Req 12.8.
        calls = []
        caller, building = self._caller_with_storage(
            self._FakeEquipmentSystem(calls)
        )
        cmd = _make_cmd(CmdDeposit, caller, " iron all")
        cmd.func()
        self.assertEqual(calls, [(caller, building, "Iron", None)])

    def test_non_positive_amount_rejected(self):
        calls = []
        caller, _ = self._caller_with_storage(self._FakeEquipmentSystem(calls))
        cmd = _make_cmd(CmdDeposit, caller, " iron -5")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_composes_no_action_string(self):
        calls = []
        caller, _ = self._caller_with_storage(self._FakeEquipmentSystem(calls))
        cmd = _make_cmd(CmdDeposit, caller, " Iron 100")
        cmd.func()
        # Presenter owns the outcome message; the command emits nothing.
        self.assertEqual(caller._messages, [])


class TestCmdWithdraw(_StorageCommandBase):
    """The ``withdraw <resource> <amount>`` command (Req 12.8, 16.4)."""

    class _FakeEquipmentSystem:
        def __init__(self, calls):
            self._calls = calls

        def withdraw(self, player, building, resource, amount):
            self._calls.append((player, building, resource, amount))
            return amount

    def test_system_unavailable(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdWithdraw, caller, " iron 100")
        cmd.func()
        self.assertTrue(any("unavailable" in m for m in caller._messages))

    def test_usage_guard_no_args(self):
        caller, _ = self._caller_with_storage(object())
        cmd = _make_cmd(CmdWithdraw, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_usage_guard_non_int_amount(self):
        caller, _ = self._caller_with_storage(object())
        cmd = _make_cmd(CmdWithdraw, caller, " iron heaps")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_no_storage_building_here(self):
        calls = []
        caller = FakeCaller(
            systems={"equipment_system": self._FakeEquipmentSystem(calls)},
            location=FakeLocation(),
        )
        cmd = _make_cmd(CmdWithdraw, caller, " iron 100")
        cmd.func()
        self.assertEqual(calls, [])
        self.assertTrue(any("No storage building" in m for m in caller._messages))

    def test_delegates_and_title_cases_resource(self):
        calls = []
        caller, building = self._caller_with_storage(
            self._FakeEquipmentSystem(calls)
        )
        cmd = _make_cmd(CmdWithdraw, caller, " iron 100")
        cmd.func()
        self.assertEqual(calls, [(caller, building, "Iron", 100)])

    def test_bare_and_all_mean_all(self):
        # `withdraw iron` and `withdraw iron all` → amount None, per Req 12.8.
        for args in (" iron", " iron all"):
            calls = []
            caller, building = self._caller_with_storage(
                self._FakeEquipmentSystem(calls)
            )
            cmd = _make_cmd(CmdWithdraw, caller, args)
            cmd.func()
            self.assertEqual(calls, [(caller, building, "Iron", None)], args)


class TestCmdEquipment(unittest.TestCase):
    """The paperdoll ``equipment`` command (Req 12.3, 12.9)."""

    class _FakeItem:
        def __init__(self, key, stat_modifiers=None, weapon_type=None,
                     magazine_size=None, loaded=None):
            self.key = key
            self.stat_modifiers = stat_modifiers or {}
            self.weapon_type = weapon_type
            self.magazine_size = magazine_size
            if loaded is not None:
                self.db = types.SimpleNamespace(loaded=loaded)

        def get_stat(self, stat_name, default=0):
            return float(self.stat_modifiers.get(stat_name, default))

    def test_lists_all_eleven_slots_including_empties(self):
        from world.constants import EQUIPMENT_SLOTS

        caller = FakeCaller()
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        # Every slot appears; empty ones are marked "(empty)".
        for slot in EQUIPMENT_SLOTS:
            self.assertIn(f"[{slot}]", out)
        self.assertEqual(out.count("(empty)"), len(EQUIPMENT_SLOTS))

    def test_occupied_slot_shows_item_and_mods(self):
        caller = FakeCaller()
        helmet = self._FakeItem("combat helmet", {"damage_reduction": 2})
        caller._equipment_slots = {"head": helmet}
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        self.assertIn("[head] combat helmet", out)
        self.assertIn("damage_reduction: +2", out)
        # head is no longer empty; the other ten slots are.
        self.assertEqual(out.count("(empty)"), 10)

    def test_ranged_weapon_shows_ammo_count(self):
        caller = FakeCaller()
        rifle = self._FakeItem(
            "rifle", {"damage": 10}, weapon_type="ranged",
            magazine_size=30, loaded=24,
        )
        caller._equipment_slots = {"weapon": rifle}
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        self.assertIn("Ammo: 24/30", out)

    def test_melee_weapon_has_no_ammo_line(self):
        caller = FakeCaller()
        blade = self._FakeItem("blade", {"damage": 5}, weapon_type="melee")
        caller._equipment_slots = {"weapon": blade}
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        self.assertNotIn("Ammo:", out)

    def test_totals_shown(self):
        caller = FakeCaller()
        caller._equipment_slots = {
            "head": self._FakeItem("helm", {"damage_reduction": 3, "sight_range": 1}),
            "hands": self._FakeItem("gloves", {"damage_bonus": 2, "move_speed": 1}),
        }
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        self.assertIn("Armor (damage_reduction): +3", out)
        self.assertIn("Damage: +2", out)
        self.assertIn("Move speed: +1", out)
        self.assertIn("Sight range: +1", out)

    def test_max_hp_total_shown_when_gear_grants_it(self):
        caller = FakeCaller()
        caller._equipment_slots = {
            "torso": self._FakeItem("vitality vest", {"max_hp": 50}),
        }
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        self.assertIn("Max HP: +50", out)

    def test_max_hp_total_hidden_when_no_gear_grants_it(self):
        caller = FakeCaller()
        caller._equipment_slots = {
            "head": self._FakeItem("helm", {"damage_reduction": 3}),
        }
        cmd = _make_cmd(CmdEquipment, caller, "")
        cmd.func()
        out = "\n".join(caller._messages)
        self.assertNotIn("Max HP", out)

class TestCmdResearch(unittest.TestCase):
    def setUp(self):
        # research is gated on the caller having an active HQ; register an HQ
        # def so owner_has_active_hq resolves the capability via the singleton.
        from world.data_registry import DataRegistry
        registry = DataRegistry()
        registry.buildings = {"HQ": _hq_building_def()}
        DataRegistry.set_instance(registry)

    def tearDown(self):
        from world.data_registry import DataRegistry
        DataRegistry.set_instance(None)

    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdResearch, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_success(self):
        class FakeTechSystem:
            def start_research(self, player, key):
                return True, f"Started researching {key}."
        caller = _give_hq(FakeCaller(systems={"tech_system": FakeTechSystem()}))
        cmd = _make_cmd(CmdResearch, caller, " adv_armor")
        cmd.func()
        self.assertTrue(any("Started" in m for m in caller._messages))

    def test_blocked_when_base_deactivated(self):
        """No research while the caller's base has no HQ."""
        class FakeTechSystem:
            def start_research(self, player, key):
                raise AssertionError("should not research when deactivated")
        caller = FakeCaller(systems={"tech_system": FakeTechSystem()})
        # No get_buildings HQ -> deactivated.
        cmd = _make_cmd(CmdResearch, caller, " adv_armor")
        cmd.func()
        self.assertTrue(any("deactivated" in m.lower() for m in caller._messages))

class TestCmdPowerup(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdPowerup, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_success(self):
        class FakePowerupSystem:
            def activate(self, player, key):
                return True, f"Activated {key}."
        caller = FakeCaller(systems={"powerup_system": FakePowerupSystem()})
        cmd = _make_cmd(CmdPowerup, caller, " rage")
        cmd.func()
        self.assertTrue(any("Activated" in m for m in caller._messages))

class _FakeEquipItem:
    """Minimal equippable item exposing a slot and stat modifiers."""
    def __init__(self, key, slot, stat_modifiers):
        self.key = key
        self.slot = slot
        self.stat_modifiers = dict(stat_modifiers)


class TestCmdScore(unittest.TestCase):
    def test_output_contains_key_fields(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdScore, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("HP:", output)
        self.assertIn("XP:", output)
        self.assertIn("Position:", output)

    def test_shows_kill_and_death_tally(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdScore, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Kills: 7", output)
        self.assertIn("Deaths: 2", output)

    def test_shows_aggregated_equipment_totals(self):
        caller = FakeCaller()
        caller.equipment.equip(
            _FakeEquipItem(
                "kevlar_vest", "torso",
                {"damage_reduction": 5, "move_speed": 2, "max_hp": 40},
            )
        )
        cmd = _make_cmd(CmdScore, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Equipment totals:", output)
        self.assertIn("Armor: +5", output)
        self.assertIn("Move speed: +2", output)
        self.assertIn("Max HP: +40", output)

    def test_hides_totals_when_no_equipment(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdScore, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("Equipment totals:", output)

class TestCmdBuildings(unittest.TestCase):
    def test_no_buildings(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdBuildings, caller)
        cmd.func()
        self.assertTrue(any("no buildings" in m.lower() for m in caller._messages))

    def test_with_buildings(self):
        class FakeAttrs:
            def __init__(self):
                self._data = {"building_type": "MM", "building_level": 2, "hp": 400, "hp_max": 500}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def has(self, key):
                return key in self._data
        class FakeBuilding:
            def __init__(self):
                self.attributes = FakeAttrs()
                self.location = FakeLocation(x=3, y=4)
        caller = FakeCaller()
        caller.get_buildings = lambda: [FakeBuilding()]
        cmd = _make_cmd(CmdBuildings, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("MM", output)
        self.assertIn("Lv2", output)

class TestCmdScan(unittest.TestCase):
    def test_empty_scan(self):
        caller = FakeCaller()
        caller.location.contents = []
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Nothing else visible", output)

    def test_scan_shows_player_in_range(self):
        # UX #2: scan reports entities within the vision radius (not just the
        # caller's own tile). Enemy a few tiles away is now visible.
        other = FakeCaller(name="Enemy")
        other.db.coord_x = 8   # caller is at (5,5); 3 tiles away, within radius
        other.db.coord_y = 5
        caller = FakeCaller()
        caller.location.contents = [other]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Enemy", output)
        self.assertIn("(8,5)", output)

    def test_scan_excludes_beyond_vision_radius(self):
        """Entities past the vision radius are not reported."""
        other = FakeCaller(name="FarAway")
        other.db.coord_x = 99
        other.db.coord_y = 99
        caller = FakeCaller()
        caller.location.contents = [other]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("FarAway", output)
        self.assertIn("Nothing else visible", output)

    def test_scan_labels_enemy_guard(self):
        """An NPC-base guard (sentinel owner) is tagged [Enemy] in the scan."""
        sentinel = types.SimpleNamespace(
            key="Outpost #1",
            db=types.SimpleNamespace(is_sentinel=True),
        )
        guard = types.SimpleNamespace(
            key="Guard (Outpost #1)",
            db=types.SimpleNamespace(
                coord_x=6, coord_y=5, combat_xp=0,  # combat_xp -> is_player True
                npc_type="enemy", owner=sentinel,
            ),
        )
        caller = FakeCaller()
        caller.location.contents = [guard]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("[Enemy]", output)
        self.assertIn("Guard (Outpost #1)", output)

    def test_scan_labels_enemy_building(self):
        """An NPC-base building (sentinel owner) is tagged [Enemy]."""
        sentinel = types.SimpleNamespace(
            key="Outpost #1",
            db=types.SimpleNamespace(is_sentinel=True),
        )
        building = types.SimpleNamespace(
            key="Headquarters",
            owner=sentinel,
            db=types.SimpleNamespace(
                coord_x=6, coord_y=5, building_type="HQ", owner=sentinel,
            ),
        )
        caller = FakeCaller()
        caller.location.contents = [building]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("[Enemy]", output)
        self.assertIn("HQ", output)

    def test_scan_does_not_label_player_building(self):
        """Another player's building is NOT tagged [Enemy] (no owner-!=-caller
        mislabeling in PvP) — only sentinel-owned bases get the tag."""
        other_player = types.SimpleNamespace(
            key="Rival",
            db=types.SimpleNamespace(combat_xp=200),  # a player, not a sentinel
        )
        building = types.SimpleNamespace(
            key="Rival HQ",
            owner=other_player,
            db=types.SimpleNamespace(
                coord_x=6, coord_y=5, building_type="HQ", owner=other_player,
            ),
        )
        caller = FakeCaller()
        caller.location.contents = [building]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("[Enemy]", output)
        self.assertIn("HQ", output)

class TestCmdTechnology(unittest.TestCase):
    def test_shows_researched(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdTechnology, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("basic_armor", output)

class TestCmdInventory(unittest.TestCase):
    def test_shows_resources(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Iron", output)

    def test_empty_inventory(self):
        caller = FakeCaller()
        caller.db.resources = {}
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Empty", output)

    def test_shows_supplies_and_carry(self):
        """Supply_Bag counts and the carry line render (Req 12.4, 12.9)."""
        class FakeEquipmentSystem:
            def carried_weight(self, player):
                return 340.0
            def carry_limit(self, player):
                return 1000.0

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        caller.equipment.add_supply("medkit", 3)
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Supplies:", output)
        self.assertIn("medkit", output)
        self.assertIn("Carry: 340/1000", output)

    def test_admin_infinite_carry_limit(self):
        """An admin's unbounded limit renders as the infinity glyph (Req 15.6)."""
        class FakeEquipmentSystem:
            def carried_weight(self, player):
                return 12.0
            def carry_limit(self, player):
                return float("inf")

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Carry: 12/\u221e", output)

    def test_carry_line_skipped_when_system_unavailable(self):
        """No equipment_system → the carry line degrades away, no error."""
        caller = FakeCaller()
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("Carry:", output)

    def test_shows_carried_unequipped_gear(self):
        """Loose Gear in contents is listed (the spawned-rifle bug)."""
        caller = FakeCaller()
        # A GameItem-like: has item_key, no db.count (not a supply drop).
        rifle = types.SimpleNamespace(
            key="Assault Rifle",
            db=types.SimpleNamespace(item_key="assault_rifle", count=None),
        )
        caller.contents = [rifle]
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Carried gear:", output)
        self.assertIn("assault_rifle", output)

    def test_carried_gear_excludes_equipped_and_supply_drops(self):
        """Equipped items and counted supply drops are NOT in Carried gear."""
        caller = FakeCaller()
        equipped_rifle = types.SimpleNamespace(
            key="Equipped Rifle",
            slot="weapon",
            db=types.SimpleNamespace(item_key="equipped_rifle", count=None),
        )
        supply_drop = types.SimpleNamespace(
            key="Ammo Drop",
            db=types.SimpleNamespace(item_key="rifle_rounds", count=30),
        )
        loose = types.SimpleNamespace(
            key="Loose Helmet",
            db=types.SimpleNamespace(item_key="combat_helmet", count=None),
        )
        caller.contents = [equipped_rifle, supply_drop, loose]
        # Equip the rifle so it's excluded from the loose list.
        caller.equipment.equip(equipped_rifle)
        cmd = _make_cmd(CmdInventory, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("combat_helmet", output)          # loose gear listed
        self.assertNotIn("equipped_rifle", output)       # equipped excluded
        # The supply drop's key is a supply, not loose gear — not in Carried gear.
        carried_section = output.split("Carried gear:")[-1]
        self.assertNotIn("rifle_rounds", carried_section.split("Supplies:")[0]
                         if "Supplies:" in carried_section else carried_section)

class TestCmdMessage(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdMessage, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_target_not_found(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdMessage, caller, " nobody hello")
        cmd.func()
        self.assertTrue(any("Could not find" in m for m in caller._messages))

    def test_success(self):
        target = FakeCaller(name="Bob")
        caller = FakeCaller()
        caller._search_results["Bob"] = target
        cmd = _make_cmd(CmdMessage, caller, " Bob hey there")
        cmd.func()
        self.assertTrue(any("hey there" in m for m in target._messages))
        self.assertTrue(any("You message" in m for m in caller._messages))

class TestCmdSay(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdSay, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_success(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdSay, caller, " hello room")
        cmd.func()
        self.assertTrue(any("You say" in m for m in caller._messages))
        # msg_contents is called with from_obj for proximity filtering
        self.assertTrue(any("hello room" in m for m in caller.location._messages))


class TestCmdMap(unittest.TestCase):
    """Tests for the map command."""

    def test_no_renderer_available(self):
        """When procedural_map_renderer is not wired, no crash."""
        caller = FakeCaller()
        cmd = _make_cmd(CmdMap, caller, "")
        cmd.func()
        # No error — just no map output

    def test_no_planet(self):
        """When player has no coord_planet, no crash."""
        class BareLocation:
            id = 99

        caller = FakeCaller(systems={"procedural_map_renderer": object()})
        caller.db.coord_planet = ""
        caller.location = BareLocation()
        cmd = _make_cmd(CmdMap, caller, "")
        cmd.func()
        # No error — just no map output

    def test_syncs_coords_from_room(self):
        """When coord_planet is empty but room has coords, sync and render."""
        class FakeRenderer:
            def render(self, player, buildings):
                return "Pl Fo"

        caller = FakeCaller(systems={"procedural_map_renderer": FakeRenderer()})
        caller.db.coord_planet = ""
        # FakeCaller's default location has x=5, y=5, planet_name="earth"
        cmd = _make_cmd(CmdMap, caller, "")
        cmd.func()
        self.assertEqual(caller.db.coord_planet, "earth")
        self.assertTrue(any("Pl Fo" in m for m in caller._messages))

    def test_renders_map(self):
        """When renderer is available, render and display the map."""
        class FakeRenderer:
            def render(self, player, buildings):
                return "Pl Fo Ro\nMu Pl Fo"

        caller = FakeCaller(systems={"procedural_map_renderer": FakeRenderer()})
        cmd = _make_cmd(CmdMap, caller, "")
        cmd.func()
        self.assertTrue(any("Pl Fo Ro" in m for m in caller._messages))

    def test_empty_map(self):
        """When renderer returns empty string, no crash."""
        class FakeRenderer:
            def render(self, player, buildings):
                return ""

        caller = FakeCaller(systems={"procedural_map_renderer": FakeRenderer()})
        cmd = _make_cmd(CmdMap, caller, "")
        cmd.func()
        # No error — just no map output


class TestExitCommands(unittest.TestCase):
    """closeexit / openexit resolve the building via coordinates.

    Regression: both commands previously relied on a tile-lookup stub that
    always returned None and always failed with "Cannot determine your
    position" even when inside an owned building.
    """

    def setUp(self):
        # Exit toggles are gated on the owner having an active HQ; register an
        # HQ def so owner_has_active_hq resolves the capability via the singleton.
        from world.data_registry import DataRegistry
        registry = DataRegistry()
        registry.buildings = {"HQ": _hq_building_def()}
        DataRegistry.set_instance(registry)

    def tearDown(self):
        from world.data_registry import DataRegistry
        DataRegistry.set_instance(None)

    def _make_owned_building(self, owner):
        class _AttrStore:
            def __init__(self):
                self._data = {}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value

        class Building:
            def __init__(self, owner):
                self.attributes = _AttrStore()
                self.attributes.add("building_type", "HQ")
                self.attributes.add("owner", owner)
                self.attributes.add("closed_exits", set())
        return Building(owner)

    def _setup(self, cmd_class, args):
        # Owner has a completed HQ so its base is active (exit toggles allowed).
        owner = type("Owner", (), {"id": 7})()
        owner.db = types.SimpleNamespace(coord_planet="earth_planet")
        owner.get_buildings = lambda: [_hq_building()]
        building = self._make_owned_building(owner)
        loc = FakeLocation()
        loc._buildings_by_coord[(5, 5)] = [building]  # caller default coords
        caller = FakeCaller(location=loc)
        caller.id = 7                       # matches building owner
        caller.db.inside_building = True
        cmd = _make_cmd(cmd_class, caller, args)
        return cmd, caller, building

    def test_closeexit_closes_when_inside_owned_building(self):
        cmd, caller, building = self._setup(CmdCloseExit, "north")
        cmd.func()
        msgs = " ".join(caller._messages)
        self.assertNotIn("Cannot determine your position", msgs)
        self.assertIn("Closed the north exit", msgs)
        self.assertIn("north", building.attributes.get("closed_exits"))

    def test_openexit_opens_a_closed_exit(self):
        cmd, caller, building = self._setup(CmdOpenExit, "north")
        building.attributes.add("closed_exits", {"north"})
        cmd.func()
        msgs = " ".join(caller._messages)
        self.assertNotIn("Cannot determine your position", msgs)
        self.assertIn("Opened the north exit", msgs)

    def test_closeexit_requires_inside_building(self):
        cmd, caller, _ = self._setup(CmdCloseExit, "north")
        caller.db.inside_building = False
        cmd.func()
        self.assertIn(
            "must be inside a building", " ".join(caller._messages)
        )

    def test_closeexit_no_building_at_coords(self):
        loc = FakeLocation()  # no building registered at (5,5)
        caller = FakeCaller(location=loc)
        caller.id = 7
        caller.db.inside_building = True
        cmd = _make_cmd(CmdCloseExit, caller, "north")
        cmd.func()
        self.assertIn("No building here", " ".join(caller._messages))

    def test_exit_toggles_closed_then_open(self):
        # UX #5: 'exit <dir>' closes an open exit and opens a closed one.
        cmd, caller, building = self._setup(CmdExit, "north")
        cmd.func()  # open -> closed
        self.assertIn("Closed the north exit", " ".join(caller._messages))
        self.assertIn("north", building.attributes.get("closed_exits"))

        # A second toggle re-opens it.
        cmd2 = _make_cmd(CmdExit, caller, "north")
        cmd2.func()
        self.assertIn("Opened the north exit", " ".join(caller._messages))
        self.assertNotIn("north", building.attributes.get("closed_exits"))

    def test_exit_accepts_abbreviated_direction(self):
        cmd, caller, building = self._setup(CmdExit, "e")
        cmd.func()
        self.assertIn("east", building.attributes.get("closed_exits"))

    def test_exit_requires_ownership(self):
        cmd, caller, building = self._setup(CmdExit, "north")
        caller.id = 999  # not the owner (id 7)
        cmd.func()
        self.assertIn("do not own", " ".join(caller._messages))


class _FakeDrop:
    """Minimal gettable object at the caller's coordinates."""
    def __init__(self, key="Wood", x=5, y=5):
        self.key = key
        self._x, self._y = x, y
        self.got = False
    def at_pre_get(self, getter, **kw):
        return True
    def move_to(self, dest, **kw):
        self.got = True
    def at_get(self, getter, **kw):
        pass


class TestGetInterruptsActivity(unittest.TestCase):
    """`get` is a physical action and interrupts active-presence work.

    Requested behavior: picking things up should stop harvesting/building
    (matching movement); info-only commands must not.
    """

    def _harvesting_caller_with_drop(self):
        loc = FakeLocation()
        drop = _FakeDrop(key="Wood", x=5, y=5)
        loc._objects_by_coord[(5, 5)] = [drop]
        caller = FakeCaller(location=loc)
        caller.db.activity_state = "harvesting"
        caller.db.activity_target = loc
        caller.db.activity_progress = 3
        return caller, drop

    def test_get_interrupts_harvesting(self):
        caller, drop = self._harvesting_caller_with_drop()
        cmd = _make_cmd(CmdGet, caller, "Wood")
        cmd.func()
        self.assertEqual(caller.db.activity_state, "idle")
        self.assertIsNone(caller.db.activity_target)
        self.assertEqual(caller.db.activity_progress, 0)
        self.assertTrue(drop.got)  # pickup still happened
        self.assertTrue(any("interrupted" in m.lower() for m in caller._messages))

    def test_get_all_interrupts_harvesting(self):
        caller, drop = self._harvesting_caller_with_drop()
        cmd = _make_cmd(CmdGet, caller, "all")
        cmd.func()
        self.assertEqual(caller.db.activity_state, "idle")
        self.assertTrue(drop.got)

    def test_get_interrupts_building(self):
        loc = FakeLocation()
        loc._objects_by_coord[(5, 5)] = [_FakeDrop(key="Wood")]
        caller = FakeCaller(location=loc)
        caller.db.activity_state = "building"
        caller.db.activity_target = "some_building"
        caller.db.activity_progress = 10
        cmd = _make_cmd(CmdGet, caller, "Wood")
        cmd.func()
        self.assertEqual(caller.db.activity_state, "idle")

    def test_get_when_idle_is_noop_no_message(self):
        loc = FakeLocation()
        loc._objects_by_coord[(5, 5)] = [_FakeDrop(key="Wood")]
        caller = FakeCaller(location=loc)
        caller.db.activity_state = "idle"
        cmd = _make_cmd(CmdGet, caller, "Wood")
        cmd.func()
        # No interrupt notice when nothing was in progress.
        self.assertFalse(any("interrupted" in m.lower() for m in caller._messages))
        self.assertEqual(caller.db.activity_state, "idle")


# -------------------------------------------------------------- #
#  CmdDrop — tile capacity cap + drop all
# -------------------------------------------------------------- #

class _FakeInvItem:
    """A droppable inventory item that lands on the tile when dropped."""
    def __init__(self, key):
        self.key = key
        self.db = types.SimpleNamespace(coord_x=None, coord_y=None)
        self.location = None

    def at_pre_drop(self, dropper, **kw):
        return True

    def move_to(self, dest, **kw):
        self.location = dest
        return True

    def at_drop(self, dropper, **kw):
        self.db.coord_x = getattr(dropper.db, "coord_x", None)
        self.db.coord_y = getattr(dropper.db, "coord_y", None)


class _CapTile(FakeLocation):
    """A tile whose coord_index.add records items so tile_object_count sees them,
    making the item-capacity cap real in-test."""

    class _Index:
        def __init__(self, tile):
            self._tile = tile

        def add(self, obj, x, y):
            self._tile._objects_by_coord.setdefault((x, y), []).append(obj)

    @property
    def coord_index(self):
        return _CapTile._Index(self)


class TestDropCapacity(unittest.TestCase):
    """drop / drop all honor the tile item-capacity cap (default empty tile=1)."""

    def _caller_with_items(self, tile, *item_keys):
        caller = FakeCaller(location=tile)
        caller.db.coord_x = 5
        caller.db.coord_y = 5
        items = [_FakeInvItem(k) for k in item_keys]
        caller.contents = list(items)  # what CmdDrop iterates
        return caller, items

    def test_drop_onto_empty_tile_succeeds_and_indexes(self):
        tile = _CapTile()
        caller, (knife,) = self._caller_with_items(tile, "Combat Knife")
        _make_cmd(CmdDrop, caller, "Combat Knife").func()
        self.assertIn(knife, tile.get_objects_at(5, 5))
        self.assertEqual(knife.db.coord_x, 5)
        self.assertTrue(any("drop" in m.lower() for m in caller._messages))

    def test_second_drop_on_full_empty_tile_is_refused(self):
        tile = _CapTile()
        # Empty-tile cap is 1: pre-fill the tile with one loose item.
        tile._objects_by_coord[(5, 5)] = [_FakeInvItem("Rock")]
        caller, (knife,) = self._caller_with_items(tile, "Combat Knife")
        _make_cmd(CmdDrop, caller, "Combat Knife").func()
        self.assertNotIn(knife, tile.get_objects_at(5, 5))
        self.assertTrue(any("full" in m.lower() for m in caller._messages))

    def test_drop_all_fills_to_capacity_and_keeps_the_rest(self):
        tile = _CapTile()  # empty tile, cap = 1
        caller, items = self._caller_with_items(tile, "Knife", "Medkit", "Rifle")
        _make_cmd(CmdDrop, caller, "all").func()
        on_tile = tile.get_objects_at(5, 5)
        self.assertEqual(len(on_tile), 1, "only cap-many items should drop")
        self.assertTrue(
            any("stay in your inventory" in m for m in caller._messages)
        )


class TestGetAllAndLookMessages(unittest.TestCase):
    """get all reports what it picked up; look lists dropped items on the tile."""

    def test_get_all_reports_picked_up_items(self):
        loc = FakeLocation()
        loc._objects_by_coord[(5, 5)] = [_FakeDrop(key="Wood"), _FakeDrop(key="Iron")]
        caller = FakeCaller(location=loc)
        _make_cmd(CmdGet, caller, "all").func()
        # A success message names the picked-up items (previously silent).
        self.assertTrue(
            any("pick up" in m.lower() for m in caller._messages),
            f"get all should report what it picked up; got {caller._messages}",
        )

    def test_get_all_nothing_here_message(self):
        loc = FakeLocation()  # empty tile
        caller = FakeCaller(location=loc)
        _make_cmd(CmdGet, caller, "all").func()
        self.assertTrue(
            any("nothing to pick up" in m.lower() for m in caller._messages)
        )

    def test_tile_summary_lists_dropped_items(self):
        from mygame.commands.game_commands import _show_tile_summary

        class _Item:
            def __init__(self, key):
                self.key = key
                self.db = types.SimpleNamespace(count=None)
                self.tags = _ItemTags()

        class _ItemTags:
            # Mirror Evennia's TagHandler.get: key is optional/keyword too, so a
            # category-only call (tags.get(category="npc_type")) works.
            def get(self, key=None, category=None):
                return "item" if (key == "item" and category == "object_type") else None

        loc = FakeLocation()
        knife = _Item("Combat Knife")
        # get_objects_at(type_tag="item") must return the item.
        loc._objects_by_coord[(5, 5)] = [knife]
        caller = FakeCaller(location=loc)

        _show_tile_summary(caller, loc)
        self.assertTrue(
            any("Combat Knife" in m for m in caller._messages),
            f"look/tile summary should list dropped items; got {caller._messages}",
        )

    def test_tile_summary_lists_hostile_npcs(self):
        """An enemy guard on the caller's tile shows under 'Hostiles here' with
        an [Enemy] tag — previously invisible to look/move."""
        from mygame.commands.game_commands import _show_tile_summary

        class _NpcTags:
            def get(self, key=None, category=None):
                return "enemy" if category == "npc_type" else None

        class _Sentinel:
            def __init__(self):
                self.db = types.SimpleNamespace(is_sentinel=True)

            @property
            def attributes(self):
                class _A:
                    @staticmethod
                    def get(k, default=None):
                        return True if k == "is_sentinel" else default
                return _A()

        class _Guard:
            def __init__(self, key, owner):
                self.key = key
                self.tags = _NpcTags()
                self.db = types.SimpleNamespace(
                    owner=owner, role="guard", agent_id=1,
                    coord_x=5, coord_y=5,
                )

        loc = FakeLocation()
        guard = _Guard("Outpost #1 Guard-1", _Sentinel())
        loc._objects_by_coord[(5, 5)] = [guard]
        caller = FakeCaller(location=loc)

        _show_tile_summary(caller, loc)
        out = "\n".join(caller._messages)
        self.assertIn("Hostiles here:", out)
        self.assertIn("Outpost #1 Guard-1", out)
        self.assertIn("[Enemy]", out)


class _InvGear:
    """A carried, unequipped gear item (key + db.item_key, no count)."""
    def __init__(self, key, item_key=None):
        self.key = key
        self.db = types.SimpleNamespace(item_key=item_key or key, count=None)


class _RecordingEquip:
    """Captures sell_item/junk_item calls for command-level assertions."""
    def __init__(self):
        self.sold = []

    def sell_item(self, player, item):
        self.sold.append(item)
        return True

    def junk_item(self, player, item):
        self.sold.append(item)
        return True


class TestSellJunkResolution(unittest.TestCase):
    """sell/junk resolve duplicate-named gear (interchangeable) and give an
    actionable message only for genuinely different item types."""

    def _caller(self, *items):
        eq = _RecordingEquip()
        caller = FakeCaller(systems={"equipment_system": eq})
        caller.contents = list(items)
        return caller, eq

    def test_duplicate_named_items_sell_one_without_ambiguity(self):
        # Three IDENTICAL boots — 'sell boot' must act on one, not error.
        caller, eq = self._caller(
            _InvGear("Combat Boots", "combat_boots"),
            _InvGear("Combat Boots", "combat_boots"),
            _InvGear("Combat Boots", "combat_boots"),
        )
        _make_cmd(CmdSell, caller, "boot").func()
        self.assertEqual(len(eq.sold), 1, "should sell exactly one of the duplicates")
        self.assertFalse(
            any("more specific" in m or "several kinds" in m for m in caller._messages),
            f"identical items must not be ambiguous; got {caller._messages}",
        )

    def test_different_types_give_actionable_ambiguity_message(self):
        # 'combat' matches two DIFFERENT item types — name them + show a fix.
        caller, eq = self._caller(
            _InvGear("Combat Boots", "combat_boots"),
            _InvGear("Combat Helmet", "combat_helmet"),
        )
        _make_cmd(CmdSell, caller, "combat").func()
        self.assertEqual(eq.sold, [], "ambiguous match must not sell anything")
        msg = caller._messages[-1]
        self.assertIn("Combat Boots", msg)
        self.assertIn("Combat Helmet", msg)
        self.assertIn("sell", msg.lower())  # concrete next step

    def test_no_match_reports_not_carrying(self):
        caller, eq = self._caller(_InvGear("Combat Boots", "combat_boots"))
        _make_cmd(CmdJunk, caller, "rifle").func()
        self.assertEqual(eq.sold, [])
        self.assertTrue(any("aren't carrying" in m for m in caller._messages))


if __name__ == "__main__":
    unittest.main()
