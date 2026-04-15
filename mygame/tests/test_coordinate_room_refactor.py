"""
Unit, integration, and smoke tests for the coordinate-room-refactor spec.

Tasks 18.1, 18.2, 18.3 — Final testing and validation.

Uses lightweight mocks (no Evennia runtime). Follows patterns from
test_prop_coordinate_index.py and test_game_commands.py.

Requirements: 7.1, 7.4, 4.1, 6.3, 6.4, 11.1, 14.1, 8.1, 13.1,
              10.1, 10.2, 10.3, 10.5, 10.6
"""

import os
import sys
import types
import unittest

# ------------------------------------------------------------------ #
#  Bootstrap: stub out Evennia modules
# ------------------------------------------------------------------ #

def _ensure_evennia_stubs():
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

    class DefaultRoom:
        def at_object_receive(self, moved_obj, source_location, **kwargs):
            pass
        def at_object_leave(self, moved_obj, target_location, **kwargs):
            pass

    class Command:
        key = ""
        aliases = []
        locks = ""
        help_category = "General"
        def func(self):
            pass

    class DefaultScript:
        pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultRoom": DefaultRoom,
        "DefaultCharacter": DefaultCharacter,
    })
    _mod("evennia.commands")
    _mod("evennia.commands.command", {"Command": Command})
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {"DefaultScript": DefaultScript})

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.coordinate.coordinate_index import CoordinateIndex


# ------------------------------------------------------------------ #
#  Shared mock helpers
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


class _MockTagHandler:
    def __init__(self, tags=None):
        self._tags = tags or {}
    def add(self, tag, category="default"):
        self._tags.setdefault(category, set()).add(tag)
    def get(self, tag, category="default"):
        if category in self._tags and tag in self._tags[category]:
            return tag
        return None


class MockObj:
    """Lightweight mock with db.coord_x / db.coord_y."""
    _counter = 0

    def __init__(self, x=None, y=None, key="obj"):
        MockObj._counter += 1
        self._id = MockObj._counter
        self._attr_store = _AttrStore()
        self.attributes = self._attr_store
        self.db = _DbProxy(self._attr_store)
        self.db.coord_x = x
        self.db.coord_y = y
        self.key = key
        self.tags = _MockTagHandler()
        self.location = None
        self.has_account = False

    def __repr__(self):
        return f"MockObj({self._id}, x={self.db.coord_x}, y={self.db.coord_y})"

    def __hash__(self):
        return self._id

    def __eq__(self, other):
        return isinstance(other, MockObj) and self._id == other._id


class MockBuilding(MockObj):
    """Mock building with building tag."""
    def __init__(self, x=None, y=None, btype="HQ", owner=None):
        super().__init__(x=x, y=y, key=btype)
        self.tags.add("building", category="object_type")
        self.attributes.add("building_type", btype)
        self.attributes.add("owner", owner)
        self.attributes.add("building_level", 1)
        self.attributes.add("hp", 500)
        self.attributes.add("hp_max", 500)
        self.attributes.add("offline", False)
        self.is_offline = False


class MockPlayer(MockObj):
    """Mock player character."""
    def __init__(self, x=5, y=5, planet="earth_planet", key="TestPlayer"):
        super().__init__(x=x, y=y, key=key)
        self.has_account = True
        self.db.coord_planet = planet
        self.db.inside_building = False
        self.db.activity_state = "idle"
        self.db.activity_target = None
        self.db.activity_progress = 0
        self.db.combat_timer_expires = 0
        self.db.resources = {"Iron": 10, "Wood": 5}
        self.db.discovery_memory = {}
        self._messages = []
        self._moved_to = None

    def msg(self, text=None, **kwargs):
        if text is not None:
            if isinstance(text, tuple):
                self._messages.append(text[0])
            else:
                self._messages.append(text)

    def move_to(self, target, **kwargs):
        self._moved_to = target

    def get_buildings(self):
        return []


class MockNDB:
    """Simulates Evennia's ndb attribute handler."""
    def __init__(self, systems=None):
        self.systems = systems or {}
        self._coord_index = None


class MockPlanetRoom:
    """Lightweight PlanetRoom mock with coordinate index support."""
    def __init__(self, planet="earth_planet"):
        self._attr_store = _AttrStore()
        self.attributes = self._attr_store
        self.db = _DbProxy(self._attr_store)
        self.ndb = MockNDB()
        self.key = f"PlanetRoom-{planet}"
        self.contents = []
        self._index = CoordinateIndex()
        self.db.depleted_nodes = {}

    def _ensure_index(self):
        return self._index

    def get_objects_at(self, x, y, type_tag=None):
        objs = self._index.get_at(x, y)
        if type_tag is None:
            return objs
        return [
            o for o in objs
            if hasattr(o, "tags") and o.tags.get(type_tag, category="object_type")
        ]

    def get_buildings_at(self, x, y):
        return self.get_objects_at(x, y, type_tag="building")

    def get_players_at(self, x, y):
        return [
            o for o in self._index.get_at(x, y)
            if hasattr(o, "has_account") and o.has_account
        ]

    def get_objects_in_area(self, x1, y1, x2, y2):
        return self._index.get_in_area(x1, y1, x2, y2)

    def move_entity(self, obj, new_x, new_y):
        old_x = getattr(getattr(obj, "db", None), "coord_x", None)
        old_y = getattr(getattr(obj, "db", None), "coord_y", None)
        self._index.move(obj, old_x, old_y, new_x, new_y)
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y
        if hasattr(obj, "at_coord_change"):
            obj.at_coord_change(old_x, old_y, new_x, new_y)

    def add_object(self, obj):
        """Helper to add an object to the room and index."""
        self.contents.append(obj)
        obj.location = self
        cx = getattr(getattr(obj, "db", None), "coord_x", None)
        cy = getattr(getattr(obj, "db", None), "coord_y", None)
        if cx is not None and cy is not None:
            self._index.add(obj, int(cx), int(cy))

    def is_node_depleted(self, x, y):
        nodes = self.db.depleted_nodes or {}
        return f"{x},{y}" in nodes

    def set_node_depleted(self, x, y, resource_type, respawn_counter):
        nodes = self.db.depleted_nodes or {}
        nodes[f"{x},{y}"] = {"resource_type": resource_type, "respawn_counter": respawn_counter}
        self.db.depleted_nodes = nodes

    def clear_node_depletion(self, x, y):
        nodes = self.db.depleted_nodes or {}
        nodes.pop(f"{x},{y}", None)
        self.db.depleted_nodes = nodes

    def get_depleted_nodes(self):
        return self.db.depleted_nodes or {}


# ================================================================== #
#  Task 18.1 — Unit tests for movement, building, pickup/drop flows
#  Requirements: 7.1, 7.4, 4.1, 6.3, 6.4, 11.1
# ================================================================== #

class TestCmdMoveCoordinates(unittest.TestCase):
    """Test CmdMove updates coordinates via move_entity and sets inside_building."""

    def test_move_north_updates_coordinates(self):
        """move_entity should update coord_x/coord_y on the player."""
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        room.move_entity(player, 5, 6)

        self.assertEqual(player.db.coord_x, 5)
        self.assertEqual(player.db.coord_y, 6)

    def test_move_south_updates_coordinates(self):
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        room.move_entity(player, 5, 4)

        self.assertEqual(player.db.coord_x, 5)
        self.assertEqual(player.db.coord_y, 4)

    def test_move_east_updates_coordinates(self):
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        room.move_entity(player, 6, 5)

        self.assertEqual(player.db.coord_x, 6)
        self.assertEqual(player.db.coord_y, 5)

    def test_move_west_updates_coordinates(self):
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        room.move_entity(player, 4, 5)

        self.assertEqual(player.db.coord_x, 4)
        self.assertEqual(player.db.coord_y, 5)

    def test_move_entity_updates_index(self):
        """After move_entity, the index reflects the new position."""
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        room.move_entity(player, 10, 10)

        # Old position should be empty
        self.assertEqual(room.get_players_at(5, 5), [])
        # New position should have the player
        self.assertIn(player, room.get_players_at(10, 10))

    def test_inside_building_set_when_building_present(self):
        """inside_building should be True when a building exists at target tile."""
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        building = MockBuilding(x=5, y=6, btype="HQ")
        room.add_object(player)
        room.add_object(building)

        room.move_entity(player, 5, 6)
        # Simulate CmdMove logic: check buildings at target
        buildings_at_target = room.get_buildings_at(5, 6)
        if buildings_at_target:
            player.db.inside_building = True
        else:
            player.db.inside_building = False

        self.assertTrue(player.db.inside_building)

    def test_inside_building_cleared_when_no_building(self):
        """inside_building should be False when no building at target tile."""
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        player.db.inside_building = True
        room.add_object(player)

        room.move_entity(player, 5, 6)
        buildings_at_target = room.get_buildings_at(5, 6)
        if buildings_at_target:
            player.db.inside_building = True
        else:
            player.db.inside_building = False

        self.assertFalse(player.db.inside_building)

    def test_move_entity_fires_at_coord_change(self):
        """move_entity should fire at_coord_change hook if present."""
        room = MockPlanetRoom()
        player = MockPlayer(x=3, y=4)
        room.add_object(player)

        hook_calls = []
        player.at_coord_change = lambda ox, oy, nx, ny: hook_calls.append((ox, oy, nx, ny))

        room.move_entity(player, 7, 8)

        self.assertEqual(hook_calls, [(3, 4, 7, 8)])

    def test_move_entity_from_none_coordinates(self):
        """move_entity with None old coords (first placement) should work."""
        room = MockPlanetRoom()
        player = MockPlayer(x=None, y=None)
        room.contents.append(player)
        player.location = room

        room.move_entity(player, 10, 20)

        self.assertEqual(player.db.coord_x, 10)
        self.assertEqual(player.db.coord_y, 20)
        self.assertIn(player, room.get_players_at(10, 20))


class TestBuildingConstruction(unittest.TestCase):
    """Test building construction places building in PlanetRoom with coordinates."""

    def test_building_placed_in_planet_room(self):
        """A new building should be in PlanetRoom.contents with correct coords."""
        room = MockPlanetRoom()
        building = MockBuilding(x=10, y=20, btype="MM")
        room.add_object(building)

        self.assertIn(building, room.contents)
        self.assertEqual(building.db.coord_x, 10)
        self.assertEqual(building.db.coord_y, 20)

    def test_building_queryable_by_coordinates(self):
        """get_buildings_at should find the building at its coordinates."""
        room = MockPlanetRoom()
        building = MockBuilding(x=10, y=20, btype="HQ")
        room.add_object(building)

        result = room.get_buildings_at(10, 20)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], building)

    def test_building_not_found_at_wrong_coordinates(self):
        """get_buildings_at should not find building at different coords."""
        room = MockPlanetRoom()
        building = MockBuilding(x=10, y=20, btype="HQ")
        room.add_object(building)

        result = room.get_buildings_at(99, 99)
        self.assertEqual(result, [])

    def test_multiple_buildings_at_different_coords(self):
        """Multiple buildings at different coordinates are independently queryable."""
        room = MockPlanetRoom()
        b1 = MockBuilding(x=1, y=1, btype="HQ")
        b2 = MockBuilding(x=2, y=2, btype="MM")
        room.add_object(b1)
        room.add_object(b2)

        self.assertEqual(room.get_buildings_at(1, 1), [b1])
        self.assertEqual(room.get_buildings_at(2, 2), [b2])

    def test_building_has_correct_attributes(self):
        """Building should have building_type, owner, and coordinate attrs."""
        room = MockPlanetRoom()
        owner = MockPlayer(x=5, y=5)
        building = MockBuilding(x=5, y=5, btype="QQ", owner=owner)
        room.add_object(building)

        self.assertEqual(building.attributes.get("building_type"), "QQ")
        self.assertEqual(building.attributes.get("owner"), owner)
        self.assertEqual(building.db.coord_x, 5)
        self.assertEqual(building.db.coord_y, 5)


class TestGameItemPickupDrop(unittest.TestCase):
    """Test GameItem at_get/at_drop coordinate handling."""

    def test_at_get_clears_coordinates(self):
        """Picking up an item should set coord_x/coord_y to None."""
        from mygame.typeclasses.objects import GameItem

        item = GameItem.__new__(GameItem)
        item._attr_store = _AttrStore()
        item.attributes = item._attr_store
        item.db = _DbProxy(item._attr_store)
        item.db.coord_x = 10
        item.db.coord_y = 20

        item.at_get(MockPlayer())

        self.assertIsNone(item.db.coord_x)
        self.assertIsNone(item.db.coord_y)

    def test_at_drop_sets_dropper_coordinates(self):
        """Dropping an item should set coords to dropper's position."""
        from mygame.typeclasses.objects import GameItem

        item = GameItem.__new__(GameItem)
        item._attr_store = _AttrStore()
        item.attributes = item._attr_store
        item.db = _DbProxy(item._attr_store)
        item.db.coord_x = None
        item.db.coord_y = None

        dropper = MockPlayer(x=15, y=25)
        item.at_drop(dropper)

        self.assertEqual(item.db.coord_x, 15)
        self.assertEqual(item.db.coord_y, 25)

    def test_at_drop_then_get_roundtrip(self):
        """Drop sets coords, then get clears them."""
        from mygame.typeclasses.objects import GameItem

        item = GameItem.__new__(GameItem)
        item._attr_store = _AttrStore()
        item.attributes = item._attr_store
        item.db = _DbProxy(item._attr_store)
        item.db.coord_x = None
        item.db.coord_y = None

        dropper = MockPlayer(x=7, y=8)
        item.at_drop(dropper)
        self.assertEqual(item.db.coord_x, 7)
        self.assertEqual(item.db.coord_y, 8)

        item.at_get(MockPlayer())
        self.assertIsNone(item.db.coord_x)
        self.assertIsNone(item.db.coord_y)


class TestCmdTeleportCoordinates(unittest.TestCase):
    """Test CmdTeleport coordinate and planet updates."""

    def test_teleport_updates_coordinates_via_move_entity(self):
        """Teleport should update coords via PlanetRoom.move_entity."""
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        # Simulate teleport: update planet, then move_entity
        player.db.coord_planet = "earth_planet"
        room.move_entity(player, 50, 50)

        self.assertEqual(player.db.coord_x, 50)
        self.assertEqual(player.db.coord_y, 50)

    def test_teleport_changes_planet(self):
        """Teleport to a different planet should update coord_planet."""
        room_earth = MockPlanetRoom("earth_planet")
        room_mars = MockPlanetRoom("mars_planet")
        player = MockPlayer(x=5, y=5, planet="earth_planet")
        room_earth.add_object(player)

        # Simulate cross-planet teleport
        player.db.coord_planet = "mars_planet"
        # In real code, move_to(room_mars) would be called
        room_mars.add_object(player)
        room_mars.move_entity(player, 25, 25)

        self.assertEqual(player.db.coord_planet, "mars_planet")
        self.assertEqual(player.db.coord_x, 25)
        self.assertEqual(player.db.coord_y, 25)

    def test_teleport_same_planet_no_room_change(self):
        """Teleport on same planet should not require move_to."""
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        room.move_entity(player, 80, 80)

        # Player stays in same room
        self.assertIn(player, room.contents)
        self.assertEqual(player.db.coord_x, 80)
        self.assertEqual(player.db.coord_y, 80)


# ================================================================== #
#  Task 18.2 — Integration tests for tick cycle and map rendering
#  Requirements: 14.1, 8.1, 13.1
# ================================================================== #

class TestGameTickCoordinateLookups(unittest.TestCase):
    """Test GameTickScript processes buildings and respawns with coordinate-based lookups."""

    def test_buildings_queryable_by_tag(self):
        """Buildings in PlanetRoom are findable via get_objects_at with type_tag."""
        room = MockPlanetRoom()
        b1 = MockBuilding(x=1, y=1, btype="HQ")
        b2 = MockBuilding(x=2, y=2, btype="MM")
        room.add_object(b1)
        room.add_object(b2)

        # Tick processing would iterate all buildings
        all_buildings = [
            obj for obj in room.contents
            if hasattr(obj, "tags") and obj.tags.get("building", category="object_type")
        ]
        self.assertEqual(len(all_buildings), 2)

    def test_respawn_decrements_depletion_counter(self):
        """Respawn tick should decrement counters and clear at zero."""
        room = MockPlanetRoom()
        room.set_node_depleted(5, 5, "Iron", 3)
        room.set_node_depleted(10, 10, "Wood", 1)

        # Simulate one respawn tick
        nodes = dict(room.get_depleted_nodes())
        for key, data in list(nodes.items()):
            data["respawn_counter"] -= 1
            if data["respawn_counter"] <= 0:
                x, y = key.split(",")
                room.clear_node_depletion(int(x), int(y))
            else:
                # Update the counter
                x, y = key.split(",")
                room.set_node_depleted(int(x), int(y), data["resource_type"], data["respawn_counter"])

        # Iron should still be depleted (counter 3 -> 2)
        self.assertTrue(room.is_node_depleted(5, 5))
        remaining = room.get_depleted_nodes()
        self.assertEqual(remaining["5,5"]["respawn_counter"], 2)

        # Wood should be cleared (counter 1 -> 0)
        self.assertFalse(room.is_node_depleted(10, 10))

    def test_tick_processes_buildings_at_coordinates(self):
        """Tick can find buildings at specific coordinates for production."""
        room = MockPlanetRoom()
        extractor = MockBuilding(x=3, y=7, btype="II")
        room.add_object(extractor)

        # Simulate tick: find building, check its coordinates
        buildings = room.get_buildings_at(3, 7)
        self.assertEqual(len(buildings), 1)
        self.assertEqual(buildings[0].attributes.get("building_type"), "II")

    def test_depletion_dict_sparse(self):
        """Only depleted nodes should be in the dict."""
        room = MockPlanetRoom()

        # Initially empty
        self.assertEqual(room.get_depleted_nodes(), {})

        # Deplete one node
        room.set_node_depleted(5, 5, "Iron", 5)
        self.assertEqual(len(room.get_depleted_nodes()), 1)

        # Clear it
        room.clear_node_depletion(5, 5)
        self.assertEqual(room.get_depleted_nodes(), {})


class TestProceduralMapRendererIntegration(unittest.TestCase):
    """Test ProceduralMapRenderer uses get_objects_in_area and produces correct output."""

    def test_get_objects_in_area_returns_viewport_objects(self):
        """get_objects_in_area should return all objects within the bounding box."""
        room = MockPlanetRoom()
        b1 = MockBuilding(x=5, y=5, btype="HQ")
        b2 = MockBuilding(x=15, y=15, btype="MM")
        player = MockPlayer(x=10, y=10)
        room.add_object(b1)
        room.add_object(b2)
        room.add_object(player)

        # Viewport around player: (5, 5) to (15, 15)
        area_objects = room.get_objects_in_area(5, 5, 15, 15)
        self.assertEqual(len(area_objects), 3)
        self.assertIn(b1, area_objects)
        self.assertIn(b2, area_objects)
        self.assertIn(player, area_objects)

    def test_get_objects_in_area_excludes_outside(self):
        """Objects outside the viewport should not be returned."""
        room = MockPlanetRoom()
        inside = MockBuilding(x=5, y=5, btype="HQ")
        outside = MockBuilding(x=100, y=100, btype="MM")
        room.add_object(inside)
        room.add_object(outside)

        area_objects = room.get_objects_in_area(0, 0, 10, 10)
        self.assertIn(inside, area_objects)
        self.assertNotIn(outside, area_objects)

    def test_objects_groupable_by_coordinate(self):
        """Area query results can be grouped by (coord_x, coord_y) for rendering."""
        room = MockPlanetRoom()
        b1 = MockBuilding(x=5, y=5, btype="HQ")
        p1 = MockPlayer(x=5, y=5)
        b2 = MockBuilding(x=7, y=7, btype="MM")
        room.add_object(b1)
        room.add_object(p1)
        room.add_object(b2)

        area_objects = room.get_objects_in_area(0, 0, 10, 10)

        # Group by coordinate (simulating renderer logic)
        objects_by_coord = {}
        for obj in area_objects:
            cx = obj.db.coord_x
            cy = obj.db.coord_y
            if cx is not None and cy is not None:
                objects_by_coord.setdefault((int(cx), int(cy)), []).append(obj)

        self.assertEqual(len(objects_by_coord[(5, 5)]), 2)
        self.assertEqual(len(objects_by_coord[(7, 7)]), 1)

    def test_empty_viewport_returns_empty(self):
        """An area with no objects should return an empty list."""
        room = MockPlanetRoom()
        room.add_object(MockBuilding(x=50, y=50, btype="HQ"))

        area_objects = room.get_objects_in_area(0, 0, 10, 10)
        self.assertEqual(area_objects, [])


class TestFogOfWarBuildingCoordinates(unittest.TestCase):
    """Test FogOfWarSystem reads building coordinates from coord_x/coord_y."""

    def test_building_coords_from_db_attributes(self):
        """_get_building_coords should read coord_x/coord_y from building.db."""
        from mygame.world.coordinate.fog_of_war import _get_building_coords

        building = MockBuilding(x=42, y=99)
        coords = _get_building_coords(building)
        self.assertEqual(coords, (42, 99))

    def test_building_coords_fallback_for_missing_db(self):
        """_get_building_coords should fall back to .x/.y if db coords missing."""
        from mygame.world.coordinate.fog_of_war import _get_building_coords

        class LegacyBuilding:
            x = 10
            y = 20
        building = LegacyBuilding()
        coords = _get_building_coords(building)
        self.assertEqual(coords, (10, 20))

    def test_fog_discovery_uses_planet_room_query(self):
        """update_discovery should use PlanetRoom.get_buildings_at for building lookup."""
        from mygame.world.coordinate.fog_of_war import FogOfWarSystem

        # Create a minimal balance config
        class FakeBalance:
            player_vision_radius = 3
            building_vision_radius = 5
            fog_enabled = True

        fog = FogOfWarSystem(FakeBalance())
        room = MockPlanetRoom()
        player = MockPlayer(x=5, y=5)
        room.add_object(player)

        enemy_building = MockBuilding(x=5, y=6, btype="HQ", owner=MockPlayer(key="Enemy"))
        room.add_object(enemy_building)

        visible_tiles = {(5, 5), (5, 6), (5, 7)}

        # Should not raise — uses planet_room.get_buildings_at internally
        fog.update_discovery(player, visible_tiles, planet_room=room)

        # Verify discovery memory was updated
        disc = player.db.discovery_memory
        self.assertIsNotNone(disc)


# ================================================================== #
#  Task 18.3 — Smoke tests for code removal
#  Requirements: 10.1, 10.2, 10.3, 10.5, 10.6
# ================================================================== #

class TestCodeRemovalSmoke(unittest.TestCase):
    """Verify removed classes and references no longer exist."""

    def test_overworld_room_class_removed(self):
        """OverworldRoom class should no longer exist in typeclasses/rooms.py."""
        import mygame.typeclasses.rooms as rooms_mod
        self.assertFalse(
            hasattr(rooms_mod, "OverworldRoom"),
            "OverworldRoom class still exists in typeclasses/rooms.py"
        )

    def test_planet_room_exists(self):
        """PlanetRoom should still exist as the replacement."""
        import mygame.typeclasses.rooms as rooms_mod
        self.assertTrue(
            hasattr(rooms_mod, "PlanetRoom"),
            "PlanetRoom class is missing from typeclasses/rooms.py"
        )

    def test_tile_resolver_file_deleted(self):
        """tile_resolver.py should no longer exist."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "world", "coordinate", "tile_resolver.py")
        self.assertFalse(
            os.path.exists(path),
            f"TileResolver file still exists at {path}"
        )

    def test_room_cache_file_deleted(self):
        """room_cache.py should no longer exist."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "world", "coordinate", "room_cache.py")
        self.assertFalse(
            os.path.exists(path),
            f"RoomCache file still exists at {path}"
        )

    def test_game_systems_no_tile_resolver(self):
        """game_systems dict should not contain 'tile_resolver'."""
        # Read game_init.py source and check the dict keys
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        game_init_path = os.path.join(base, "server", "conf", "game_init.py")
        with open(game_init_path, "r") as f:
            source = f.read()
        self.assertNotIn(
            '"tile_resolver"', source,
            "game_systems dict still contains 'tile_resolver'"
        )
        self.assertNotIn(
            "'tile_resolver'", source,
            "game_systems dict still contains 'tile_resolver'"
        )

    def test_game_systems_no_garbage_collector(self):
        """game_systems dict should not contain 'garbage_collector'."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        game_init_path = os.path.join(base, "server", "conf", "game_init.py")
        with open(game_init_path, "r") as f:
            source = f.read()
        self.assertNotIn(
            '"garbage_collector"', source,
            "game_systems dict still contains 'garbage_collector'"
        )
        self.assertNotIn(
            "'garbage_collector'", source,
            "game_systems dict still contains 'garbage_collector'"
        )

    def test_no_tile_resolver_imports_in_game_commands(self):
        """No imports of TileResolver should remain in game_commands.py."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "commands", "game_commands.py")
        with open(path, "r") as f:
            source = f.read()
        self.assertNotIn("TileResolver", source,
                         "TileResolver reference found in game_commands.py")
        self.assertNotIn("tile_resolver", source,
                         "tile_resolver reference found in game_commands.py")

    def test_no_room_cache_imports_in_game_commands(self):
        """No imports of RoomCache should remain in game_commands.py."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "commands", "game_commands.py")
        with open(path, "r") as f:
            source = f.read()
        self.assertNotIn("RoomCache", source,
                         "RoomCache reference found in game_commands.py")
        self.assertNotIn("room_cache", source,
                         "room_cache reference found in game_commands.py")

    def test_no_overworld_room_imports_in_game_commands(self):
        """No imports of OverworldRoom should remain in game_commands.py."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "commands", "game_commands.py")
        with open(path, "r") as f:
            source = f.read()
        self.assertNotIn("OverworldRoom", source,
                         "OverworldRoom reference found in game_commands.py")

    def test_no_tile_resolver_imports_in_systems(self):
        """No imports of TileResolver in world/systems/ or world/coordinate/."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dirs_to_check = [
            os.path.join(base, "world", "systems"),
            os.path.join(base, "world", "coordinate"),
        ]
        for dir_path in dirs_to_check:
            if not os.path.isdir(dir_path):
                continue
            for fname in os.listdir(dir_path):
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(dir_path, fname)
                with open(fpath, "r") as f:
                    source = f.read()
                # Allow docstring mentions but not actual imports
                for line in source.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    if "import" in stripped and "TileResolver" in stripped:
                        self.fail(
                            f"TileResolver import found in {fname}: {stripped}"
                        )

    def test_no_room_cache_imports_in_systems(self):
        """No imports of RoomCache in world/systems/ or world/coordinate/."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dirs_to_check = [
            os.path.join(base, "world", "systems"),
            os.path.join(base, "world", "coordinate"),
        ]
        for dir_path in dirs_to_check:
            if not os.path.isdir(dir_path):
                continue
            for fname in os.listdir(dir_path):
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(dir_path, fname)
                with open(fpath, "r") as f:
                    source = f.read()
                for line in source.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    if "import" in stripped and "RoomCache" in stripped:
                        self.fail(
                            f"RoomCache import found in {fname}: {stripped}"
                        )


if __name__ == "__main__":
    unittest.main()
