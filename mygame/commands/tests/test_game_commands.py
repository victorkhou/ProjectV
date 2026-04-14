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
    CmdAttack, CmdEquip, CmdUnequip, CmdResearch, CmdPowerup,
    CmdScore, CmdEquipment, CmdBuildings, CmdScan, CmdTechnology,
    CmdInventory, CmdMessage, CmdSay, CmdMap,
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
    """Simulates a tile/room."""
    def __init__(self, x=5, y=5, terrain_type="Plains", building=None,
                 contents=None, planet_name="earth"):
        self.x = x
        self.y = y
        self.terrain_type = terrain_type
        self.building = building
        self.planet_name = planet_name
        self.contents = contents or []
        self._messages = []

    def msg_contents(self, text=None, exclude=None, **kwargs):
        if text is not None:
            if isinstance(text, tuple):
                self._messages.append(text[0])
            else:
                self._messages.append(text)

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

        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                return None

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return 0 <= x < 100 and 0 <= y < 100

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
            "planet_registry": FakePlanetRegistry(),
        })
        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()
        self.assertEqual(caller.db.coord_x, 5)
        self.assertEqual(caller.db.coord_y, 6)

    def test_edge_of_map_rejected(self):
        """New path: out-of-bounds coordinate is rejected."""
        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                return None

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return 0 <= x < 100 and 0 <= y < 100

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
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

        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                room = FakeLocation(x=x, y=y)
                room.building = OfflineBuilding()
                return room

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
            "planet_registry": FakePlanetRegistry(),
        })
        cmd = _make_cmd(CmdMove, caller, " north")
        cmd.func()
        self.assertIsNone(caller._moved_to)
        self.assertTrue(any("offline" in m.lower() for m in caller._messages))

    def test_coord_attributes_updated_after_move(self):
        """New path: coord_x and coord_y are updated after successful move."""

        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                return None

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
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
        class WallTileResolver:
            def __init__(self, building):
                self._building = building
            def get_if_exists(self, x, y, planet):
                room = FakeLocation(x=x, y=y)
                room.building = self._building
                return room

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        return {
            "tile_resolver": WallTileResolver(wall_building),
            "planet_registry": FakePlanetRegistry(),
        }

    def test_wall_blocks_owner_during_combat_timer(self):
        """Req 17.2: Wall blocks owner movement while combat timer is active."""
        wall = self._make_wall_building(owner_id=42)
        systems = self._make_wall_systems(wall)
        caller = FakeCaller(systems=systems)
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
        caller = FakeCaller(systems=systems)
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
        caller = FakeCaller(systems=systems)
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
        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                return None

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
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
        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                return None

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
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
        class FakeTileResolver:
            def get_if_exists(self, x, y, planet):
                return None

        class FakePlanetRegistry:
            def is_valid_coordinate(self, x, y, planet):
                return True

        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
            "planet_registry": FakePlanetRegistry(),
        })
        caller.db.activity_state = "idle"

        cmd = _make_cmd(CmdMove, caller, " south")
        cmd.func()

        self.assertEqual(caller.db.activity_state, "idle")


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
        class FakeTileResolver:
            def resolve(self, x, y, planet):
                return FakeLocation(x=x, y=y)
        caller = FakeCaller(systems={
            "resource_system": FakeResourceSystem(),
            "tile_resolver": FakeTileResolver(),
        })
        cmd = _make_cmd(CmdHarvest, caller)
        cmd.func()
        self.assertTrue(any("harvesting" in m.lower() for m in caller._messages))

    def test_no_resource(self):
        class FakeResourceSystem:
            def start_harvest(self, player, tile):
                return False, "No resource node on this tile."
        class FakeTileResolver:
            def resolve(self, x, y, planet):
                return FakeLocation(x=x, y=y)
        caller = FakeCaller(systems={
            "resource_system": FakeResourceSystem(),
            "tile_resolver": FakeTileResolver(),
        })
        cmd = _make_cmd(CmdHarvest, caller)
        cmd.func()
        self.assertTrue(any("No resource" in m for m in caller._messages))

class TestCmdBuild(unittest.TestCase):
    def test_no_args(self):
        class FakeBuildingSystem:
            pass
        class FakeTileResolver:
            def resolve(self, x, y, planet):
                return FakeLocation(x=x, y=y)
        caller = FakeCaller(systems={
            "building_system": FakeBuildingSystem(),
            "tile_resolver": FakeTileResolver(),
        })
        cmd = _make_cmd(CmdBuild, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_success(self):
        class FakeBuildingSystem:
            def start_construction(self, player, tile, btype):
                return True, f"Construction of {btype} started (0/120s). Stay on the tile to continue."
        class FakeTileResolver:
            def resolve(self, x, y, planet):
                return FakeLocation(x=x, y=y)
        caller = FakeCaller(systems={
            "building_system": FakeBuildingSystem(),
            "tile_resolver": FakeTileResolver(),
        })
        cmd = _make_cmd(CmdBuild, caller, " hq")
        cmd.func()
        self.assertTrue(any("Construction" in m for m in caller._messages))

class TestCmdUpgrade(unittest.TestCase):
    def test_no_building_on_tile(self):
        class FakeTileResolver:
            def resolve(self, x, y, planet):
                return FakeLocation(x=x, y=y, building=None)
            def get_if_exists(self, x, y, planet):
                return FakeLocation(x=x, y=y, building=None)
        class FakeBuildingSystem:
            pass
        caller = FakeCaller(systems={
            "tile_resolver": FakeTileResolver(),
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

class TestCmdUnequip(unittest.TestCase):
    def test_no_args(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdUnequip, caller, "")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_empty_slot(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdUnequip, caller, " weapon")
        cmd.func()
        self.assertTrue(any("No item" in m for m in caller._messages))

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

class TestCmdScore(unittest.TestCase):
    def test_output_contains_key_fields(self):
        caller = FakeCaller()
        cmd = _make_cmd(CmdScore, caller)
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("HP:", output)
        self.assertIn("XP:", output)
        self.assertIn("Position:", output)

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


if __name__ == "__main__":
    unittest.main()
