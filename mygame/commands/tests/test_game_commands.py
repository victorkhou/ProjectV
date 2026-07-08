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
    CmdMove, CmdHarvest, CmdBuild, CmdUpgrade,
    CmdAttack, CmdEquip, CmdUnequip, CmdUse, CmdThrow, CmdReload,
    CmdDeposit, CmdWithdraw,
    CmdResearch, CmdPowerup,
    CmdScore, CmdEquipment, CmdBuildings, CmdScan, CmdTechnology,
    CmdInventory, CmdMessage, CmdSay, CmdMap,
    CmdCloseExit, CmdOpenExit, CmdGet,
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

    def test_large_move_speed_never_drops_lag_below_one(self):
        """A huge move_speed clamps the lag to a minimum of 1 tick."""
        caller = self._make_caller(move_speed_modifier=99)
        caller.db.combat_timer_expires = 999

        with patch("world.combat_timer._get_current_tick", return_value=100):
            _make_cmd(CmdMove, caller, " north").func()

        self.assertEqual(caller.db.coord_y, 6)
        self.assertEqual(caller.db.next_move_tick, 100 + 1)


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

class TestCmdAttack(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdAttack, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_target_not_found(self):
        caller = FakeCaller(systems={"combat_engine": object()})
        cmd = _make_cmd(CmdAttack, caller, " ghost")
        cmd.func()
        self.assertTrue(any("Could not find" in m for m in caller._messages))

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

    def test_delegates_to_equipment_system(self):
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        cmd = _make_cmd(CmdUnequip, caller, " weapon")
        cmd.func()
        self.assertEqual(calls, [(caller, "weapon")])

    def test_slot_normalized_to_lowercase(self):
        """The slot token is lower-cased before delegation (Req 12.2).

        `EQUIPMENT_SLOTS` are lower-case, so `unequip WEAPON` must reach the
        system as `"weapon"` for the slot-membership check to succeed.
        """
        calls = []

        class FakeEquipmentSystem:
            def unequip(self, player, slot):
                calls.append((player, slot))
                return True

        caller = FakeCaller(systems={"equipment_system": FakeEquipmentSystem()})
        cmd = _make_cmd(CmdUnequip, caller, " WEAPON")
        cmd.func()
        self.assertEqual(calls, [(caller, "weapon")])


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

class TestCmdResearch(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdResearch, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_success(self):
        class FakeTechSystem:
            def start_research(self, player, key):
                return True, f"Started researching {key}."
        caller = FakeCaller(systems={"tech_system": FakeTechSystem()})
        cmd = _make_cmd(CmdResearch, caller, " adv_armor")
        cmd.func()
        self.assertTrue(any("Started" in m for m in caller._messages))

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

    def test_shows_aggregated_equipment_totals(self):
        caller = FakeCaller()
        caller.equipment.equip(
            _FakeEquipItem(
                "kevlar_vest", "torso",
                {"damage_reduction": 5, "move_speed": 2},
            )
        )
        cmd = _make_cmd(CmdScore, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Equipment totals:", output)
        self.assertIn("Armor: +5", output)
        self.assertIn("Move speed: +2", output)

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
        self.assertIn("Nothing visible", output)

    def test_scan_with_player(self):
        other = FakeCaller(name="Enemy")
        # Set matching coordinates so the player passes the filter
        other.db.coord_x = 5
        other.db.coord_y = 5
        caller = FakeCaller()
        caller.location.contents = [other]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Enemy", output)

    def test_scan_filters_players_by_coords(self):
        """Players at different coordinates should not appear in scan."""
        other = FakeCaller(name="FarAway")
        other.db.coord_x = 99
        other.db.coord_y = 99
        caller = FakeCaller()
        caller.location.contents = [other]
        cmd = _make_cmd(CmdScan, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("FarAway", output)
        self.assertIn("Nothing visible", output)

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
        owner = type("Owner", (), {"id": 7})()
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


if __name__ == "__main__":
    unittest.main()
