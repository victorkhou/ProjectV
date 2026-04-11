"""
Unit tests for BuildingSystem.

Tests construction validation chain, upgrade flow, destruction,
and offline protection transitions.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.8, 3.9, 3.10, 3.11,
              4.2, 4.4, 5.3, 5.4, 5.5, 5.6, 5.7
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

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.building_system import BuildingSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BuildingDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

RESOURCE_TYPES = (
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
)

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self):
        self.combat_lockout_tick = 0

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""

    def __init__(self, name="TestPlayer", resources=None, buildings=None, location=None):
        self.key = name
        self.db = FakeDB()
        self._resources = {r: 0 for r in RESOURCE_TYPES}
        if resources:
            self._resources.update(resources)
        self._buildings = buildings or []
        self.location = location

    def get_resource(self, resource_type: str) -> int:
        return self._resources.get(resource_type, 0)

    def add_resource(self, resource_type: str, amount: int) -> None:
        self._resources[resource_type] = self._resources.get(resource_type, 0) + amount

    def has_resources(self, costs: dict[str, int]) -> bool:
        return all(self._resources.get(r, 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs: dict[str, int]) -> bool:
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            self._resources[r] = self._resources.get(r, 0) - amt
        return True

    def get_buildings(self) -> list:
        return list(self._buildings)

class FakeAttributes:
    """Simulates Evennia's Attribute handler."""
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value

    def has(self, key):
        return key in self._data

class FakeBuilding:
    """Lightweight stand-in for a Building object."""

    def __init__(self, building_type="HQ", owner=None, level=1, hp=500, hp_max=500, offline=False):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": owner,
            "building_level": level,
            "hp": hp,
            "hp_max": hp_max,
            "offline": offline,
        })
        self._deleted = False

    @property
    def owner(self):
        return self.attributes.get("owner")

    @property
    def building_level(self):
        return self.attributes.get("building_level", default=1)

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))

    def set_offline(self, state: bool):
        self.attributes.add("offline", state)

    @property
    def location(self):
        return None

    def delete(self):
        self._deleted = True

class FakeTile:
    """Lightweight stand-in for an OverworldRoom tile."""

    def __init__(self, terrain_type="Plains", building=None, xyz=(0, 0, "earth")):
        self._terrain_type = terrain_type
        self._building = building
        self.x = xyz[0]
        self.y = xyz[1]

    @property
    def terrain_type(self):
        return self._terrain_type

    @property
    def building(self):
        return self._building

def _make_registry_with_buildings() -> DataRegistry:
    """Create a DataRegistry with test building definitions."""
    registry = DataRegistry()
    registry.buildings = {
        "HQ": BuildingDef(
            name="Headquarters", abbreviation="HQ",
            cost={"Straw": 50, "Wood": 50, "Stone": 30},
            max_health=500, requires_hq=False, required_terrain=None,
            category="headquarters", produces=None,
            unlocks=["MM", "QQ"], map_symbol="HQ",
        ),
        "MM": BuildingDef(
            name="Mill", abbreviation="MM",
            cost={"Straw": 20, "Wood": 10},
            max_health=150, requires_hq=True, required_terrain="Plains",
            category="resource", produces="Straw",
            unlocks=[], map_symbol="MM",
        ),
        "QQ": BuildingDef(
            name="Quarry", abbreviation="QQ",
            cost={"Wood": 20, "Stone": 10},
            max_health=200, requires_hq=True, required_terrain="Rock",
            category="resource", produces="Stone",
            unlocks=[], map_symbol="QQ",
        ),
        "VV": BuildingDef(
            name="Turret", abbreviation="VV",
            cost={"Iron": 50, "Stone": 40, "Wood": 20},
            max_health=300, requires_hq=True, required_terrain=None,
            category="defense", produces=None,
            unlocks=[], map_symbol="VV",
        ),
        "AA": BuildingDef(
            name="Armory", abbreviation="AA",
            cost={"Iron": 40, "Wood": 30, "Stone": 20},
            max_health=200, requires_hq=True, required_terrain=None,
            category="equipment", produces="weapon",
            unlocks=[], map_symbol="AA",
        ),
    }
    return registry

def _make_building_system(
    registry=None, event_bus=None, build_range=10, current_tick=0,
) -> tuple[BuildingSystem, list, EventBus]:
    """Create a BuildingSystem with a fake building factory."""
    if registry is None:
        registry = _make_registry_with_buildings()
    if event_bus is None:
        event_bus = EventBus()

    created_buildings = []

    def fake_create(building_def, tile, owner):
        b = FakeBuilding(
            building_type=building_def.abbreviation,
            owner=owner,
            level=1,
            hp=building_def.max_health,
            hp_max=building_def.max_health,
        )
        created_buildings.append(b)
        # Simulate placing on tile
        tile._building = b
        return b

    system = BuildingSystem(
        registry=registry,
        event_bus=event_bus,
        create_building_func=fake_create,
        build_range=build_range,
        current_tick_func=lambda: current_tick,
    )
    return system, created_buildings, event_bus

# -------------------------------------------------------------- #
#  Construction Tests
# -------------------------------------------------------------- #

class TestConstructHQ(unittest.TestCase):
    """Test constructing a Headquarters."""

    def test_construct_hq_success(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "HQ")
        self.assertTrue(ok)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].attributes.get("building_type"), "HQ")

    def test_construct_hq_deducts_resources(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, _, _ = _make_building_system()
        system.construct(player, tile, "HQ")
        self.assertEqual(player.get_resource("Straw"), 50)
        self.assertEqual(player.get_resource("Wood"), 50)
        self.assertEqual(player.get_resource("Stone"), 70)

    def test_construct_hq_insufficient_resources(self):
        player = FakePlayer(resources={"Straw": 10, "Wood": 10, "Stone": 10})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "HQ")
        self.assertFalse(ok)
        self.assertIn("Insufficient resources", msg)
        self.assertEqual(len(created), 0)

class TestConstructRequiresHQ(unittest.TestCase):
    """Test HQ prerequisite enforcement."""

    def test_non_hq_building_without_hq_rejected(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100})
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertFalse(ok)
        self.assertIn("Headquarters", msg)
        self.assertEqual(len(created), 0)

    def test_non_hq_building_with_hq_succeeds(self):
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100},
            buildings=[hq],
        )
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertTrue(ok)
        self.assertEqual(len(created), 1)

class TestConstructTerrainValidation(unittest.TestCase):
    """Test terrain matching for resource buildings."""

    def test_resource_building_wrong_terrain_rejected(self):
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100},
            buildings=[hq],
        )
        tile = FakeTile(terrain_type="Forest")  # MM requires Plains
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertFalse(ok)
        self.assertIn("Plains", msg)

    def test_resource_building_correct_terrain_succeeds(self):
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100},
            buildings=[hq],
        )
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertTrue(ok)

    def test_no_terrain_requirement_any_terrain_ok(self):
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Iron": 100, "Stone": 100, "Wood": 100},
            buildings=[hq],
        )
        tile = FakeTile(terrain_type="Forest")  # VV has no terrain requirement
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "VV")
        self.assertTrue(ok)

class TestConstructTileEmpty(unittest.TestCase):
    """Test tile occupancy check."""

    def test_occupied_tile_rejected(self):
        existing = FakeBuilding(building_type="MM")
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100},
            buildings=[hq],
        )
        tile = FakeTile(terrain_type="Plains", building=existing)
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertFalse(ok)
        self.assertIn("already contains", msg)

class TestConstructBuildRange(unittest.TestCase):
    """Test build range validation."""

    def test_out_of_range_rejected(self):
        player_tile = FakeTile(xyz=(0, 0, "earth"))
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100, "Stone": 100},
            location=player_tile,
        )
        far_tile = FakeTile(terrain_type="Plains", xyz=(20, 20, "earth"))
        system, created, _ = _make_building_system(build_range=10)
        ok, msg = system.construct(player, far_tile, "HQ")
        self.assertFalse(ok)
        self.assertIn("too far", msg)

    def test_in_range_succeeds(self):
        player_tile = FakeTile(xyz=(5, 5, "earth"))
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100, "Stone": 100},
            location=player_tile,
        )
        near_tile = FakeTile(terrain_type="Plains", xyz=(8, 8, "earth"))
        system, created, _ = _make_building_system(build_range=10)
        ok, msg = system.construct(player, near_tile, "HQ")
        self.assertTrue(ok)

class TestConstructCombatLockout(unittest.TestCase):
    """Test combat lockout prevents building."""

    def test_combat_lockout_rejects_build(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        player.db.combat_lockout_tick = 10  # Locked out until tick 10
        tile = FakeTile()
        # Current tick is 5, lockout until 10
        system, created, _ = _make_building_system(current_tick=5)
        ok, msg = system.construct(player, tile, "HQ")
        self.assertFalse(ok)
        self.assertIn("combat", msg.lower())

    def test_expired_lockout_allows_build(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        player.db.combat_lockout_tick = 5  # Locked out until tick 5
        tile = FakeTile()
        # Current tick is 10, lockout expired
        system, created, _ = _make_building_system(current_tick=10)
        ok, msg = system.construct(player, tile, "HQ")
        self.assertTrue(ok)

class TestConstructEvents(unittest.TestCase):
    """Test event publishing on construction."""

    def test_building_constructed_event_published(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        events = []
        event_bus = EventBus()
        event_bus.subscribe("building_constructed", lambda **kw: events.append(kw))
        system, _, _ = _make_building_system(event_bus=event_bus)
        system.construct(player, tile, "HQ")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["player"], player)

class TestConstructUnknownType(unittest.TestCase):
    """Test unknown building type."""

    def test_unknown_type_rejected(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, _, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "ZZ")
        self.assertFalse(ok)
        self.assertIn("Unknown", msg)

# -------------------------------------------------------------- #
#  Upgrade Tests
# -------------------------------------------------------------- #

class TestUpgrade(unittest.TestCase):
    """Test building upgrade flow."""

    def _make_resource_building(self, player, level=1):
        return FakeBuilding(
            building_type="MM", owner=player, level=level,
            hp=150, hp_max=150,
        )

    def test_upgrade_success(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100})
        building = self._make_resource_building(player, level=1)
        system, _, _ = _make_building_system()
        ok, msg = system.upgrade(player, building)
        self.assertTrue(ok)
        self.assertEqual(building.building_level, 2)

    def test_upgrade_deducts_correct_cost(self):
        # MM cost: Straw=20, Wood=10. Target level 2 -> cost * 2
        player = FakePlayer(resources={"Straw": 100, "Wood": 100})
        building = self._make_resource_building(player, level=1)
        system, _, _ = _make_building_system()
        system.upgrade(player, building)
        # Cost: Straw=40, Wood=20
        self.assertEqual(player.get_resource("Straw"), 60)
        self.assertEqual(player.get_resource("Wood"), 80)

    def test_upgrade_level_3_cost(self):
        # MM cost: Straw=20, Wood=10. Target level 3 -> cost * 3
        player = FakePlayer(resources={"Straw": 200, "Wood": 200})
        building = self._make_resource_building(player, level=2)
        system, _, _ = _make_building_system()
        system.upgrade(player, building)
        # Cost: Straw=60, Wood=30
        self.assertEqual(player.get_resource("Straw"), 140)
        self.assertEqual(player.get_resource("Wood"), 170)

    def test_upgrade_max_level_rejected(self):
        player = FakePlayer(resources={"Straw": 1000, "Wood": 1000})
        building = self._make_resource_building(player, level=5)
        system, _, _ = _make_building_system()
        ok, msg = system.upgrade(player, building)
        self.assertFalse(ok)
        self.assertIn("maximum level", msg)

    def test_upgrade_insufficient_resources(self):
        player = FakePlayer(resources={"Straw": 5, "Wood": 5})
        building = self._make_resource_building(player, level=1)
        system, _, _ = _make_building_system()
        ok, msg = system.upgrade(player, building)
        self.assertFalse(ok)
        self.assertIn("Insufficient resources", msg)

    def test_upgrade_non_resource_building_rejected(self):
        player = FakePlayer(resources={"Iron": 1000, "Stone": 1000, "Wood": 1000})
        building = FakeBuilding(building_type="VV", owner=player, level=1)
        system, _, _ = _make_building_system()
        ok, msg = system.upgrade(player, building)
        self.assertFalse(ok)
        self.assertIn("resource buildings", msg.lower())

    def test_upgrade_not_owned_rejected(self):
        owner = FakePlayer(name="Owner")
        other = FakePlayer(name="Other")
        building = self._make_resource_building(owner, level=1)
        system, _, _ = _make_building_system()
        ok, msg = system.upgrade(other, building)
        self.assertFalse(ok)
        self.assertIn("do not own", msg)

    def test_upgrade_publishes_event(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100})
        building = self._make_resource_building(player, level=1)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("building_upgraded", lambda **kw: events.append(kw))
        system, _, _ = _make_building_system(event_bus=event_bus)
        system.upgrade(player, building)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["old_level"], 1)
        self.assertEqual(events[0]["new_level"], 2)

# -------------------------------------------------------------- #
#  Destruction Tests
# -------------------------------------------------------------- #

class TestDestroy(unittest.TestCase):
    """Test building destruction."""

    def test_destroy_publishes_event(self):
        player = FakePlayer()
        building = FakeBuilding(building_type="MM", owner=player)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("building_destroyed", lambda **kw: events.append(kw))
        system, _, _ = _make_building_system(event_bus=event_bus)
        system.destroy(building, attacker=player)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["building"], building)

    def test_destroy_calls_delete(self):
        building = FakeBuilding(building_type="MM")
        system, _, _ = _make_building_system()
        system.destroy(building)
        self.assertTrue(building._deleted)

# -------------------------------------------------------------- #
#  Offline Protection Tests
# -------------------------------------------------------------- #

class TestOfflineProtection(unittest.TestCase):
    """Test offline building protection transitions."""

    def test_set_buildings_offline(self):
        player = FakePlayer()
        b1 = FakeBuilding(building_type="MM", owner=player)
        b2 = FakeBuilding(building_type="QQ", owner=player)
        player._buildings = [b1, b2]
        system, _, _ = _make_building_system()
        system.set_player_buildings_offline(player, True)
        self.assertTrue(b1.is_offline)
        self.assertTrue(b2.is_offline)

    def test_set_buildings_online(self):
        player = FakePlayer()
        b1 = FakeBuilding(building_type="MM", owner=player, offline=True)
        b2 = FakeBuilding(building_type="QQ", owner=player, offline=True)
        player._buildings = [b1, b2]
        system, _, _ = _make_building_system()
        system.set_player_buildings_offline(player, False)
        self.assertFalse(b1.is_offline)
        self.assertFalse(b2.is_offline)

if __name__ == "__main__":
    unittest.main()
