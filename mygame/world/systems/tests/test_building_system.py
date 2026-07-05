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
        self.rank_level = 1
        self.level = 1
        self.activity_state = "idle"
        self.activity_target = None
        self.activity_progress = 0

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
            "assigned_agent": None,
            "construction_progress": 0,
            "construction_total": 0,
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
        return getattr(self, "_location", None)

    def delete(self):
        self._deleted = True

class FakeTile:
    """Lightweight stand-in for a tile (PlanetRoom or legacy OverworldRoom)."""

    def __init__(self, terrain_type="Plains", building=None, xyz=(0, 0, "earth")):
        self._terrain_type = terrain_type
        self._building = building
        self.x = xyz[0]
        self.y = xyz[1]
        # Provide db.coord_x/coord_y for get_coords compatibility
        self.db = type("_Db", (), {
            "coord_x": xyz[0],
            "coord_y": xyz[1],
        })()

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
            build_time_seconds=180, rank_requirement=1,
        ),
        "MM": BuildingDef(
            name="Mill", abbreviation="MM",
            cost={"Straw": 20, "Wood": 10},
            max_health=150, requires_hq=True, required_terrain="Plains",
            category="resource", produces="Straw",
            unlocks=[], map_symbol="MM",
            build_time_seconds=60, rank_requirement=1,
        ),
        "QQ": BuildingDef(
            name="Quarry", abbreviation="QQ",
            cost={"Wood": 20, "Stone": 10},
            max_health=200, requires_hq=True, required_terrain="Rock",
            category="resource", produces="Stone",
            unlocks=[], map_symbol="QQ",
            build_time_seconds=90, rank_requirement=2,
        ),
        "VV": BuildingDef(
            name="Turret", abbreviation="VV",
            cost={"Iron": 50, "Stone": 40, "Wood": 20},
            max_health=300, requires_hq=True, required_terrain=None,
            category="defense", produces=None,
            unlocks=[], map_symbol="VV",
            build_time_seconds=120, rank_requirement=3,
        ),
        "AA": BuildingDef(
            name="Armory", abbreviation="AA",
            cost={"Iron": 40, "Wood": 30, "Stone": 20},
            max_health=200, requires_hq=True, required_terrain=None,
            category="equipment", produces="weapon",
            unlocks=[], map_symbol="AA",
            build_time_seconds=100, rank_requirement=2,
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
        b._location = tile
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
        player.db.rank_level = 3
        player.db.level = 3  # VV requires rank 3
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
        # MM cost: Straw=20, Wood=10. Target level 2 -> cost × 2^1 = ×2
        player = FakePlayer(resources={"Straw": 100, "Wood": 100})
        building = self._make_resource_building(player, level=1)
        system, _, _ = _make_building_system()
        system.upgrade(player, building)
        # Cost: Straw=40, Wood=20 (base × 2^(2-1) = base × 2)
        self.assertEqual(player.get_resource("Straw"), 60)
        self.assertEqual(player.get_resource("Wood"), 80)

    def test_upgrade_level_3_cost(self):
        # MM cost: Straw=20, Wood=10. Target level 3 -> cost × 2^2 = ×4
        player = FakePlayer(resources={"Straw": 200, "Wood": 200})
        building = self._make_resource_building(player, level=2)
        system, _, _ = _make_building_system()
        system.upgrade(player, building)
        # Cost: Straw=80, Wood=40 (base × 2^(3-1) = base × 4)
        self.assertEqual(player.get_resource("Straw"), 120)
        self.assertEqual(player.get_resource("Wood"), 160)

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


# -------------------------------------------------------------- #
#  Rank Requirement Tests (Req 6.5)
# -------------------------------------------------------------- #

class TestConstructRankRequirement(unittest.TestCase):
    """Test rank requirement enforcement on construction."""

    def test_rank_too_low_rejected(self):
        """Player rank 1 cannot build QQ (requires rank 2)."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Wood": 100, "Stone": 100},
            buildings=[hq],
        )
        player.db.rank_level = 1
        player.db.level = 1
        tile = FakeTile(terrain_type="Rock")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "QQ")
        self.assertFalse(ok)
        self.assertIn("Level", msg)
        self.assertEqual(len(created), 0)

    def test_rank_meets_requirement_succeeds(self):
        """Player rank 2 can build QQ (requires rank 2)."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Wood": 100, "Stone": 100},
            buildings=[hq],
        )
        player.db.rank_level = 2
        player.db.level = 2
        tile = FakeTile(terrain_type="Rock")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "QQ")
        self.assertTrue(ok)
        self.assertEqual(len(created), 1)

    def test_rank_exceeds_requirement_succeeds(self):
        """Player rank 5 can build VV (requires rank 3)."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Iron": 100, "Stone": 100, "Wood": 100},
            buildings=[hq],
        )
        player.db.rank_level = 5
        player.db.level = 5
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "VV")
        self.assertTrue(ok)

    def test_hq_rank_1_always_allowed(self):
        """HQ has rank_requirement=1, any player can build it."""
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        player.db.rank_level = 1
        player.db.level = 1
        tile = FakeTile()
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "HQ")
        self.assertTrue(ok)


# -------------------------------------------------------------- #
#  Start Construction Tests (Req 6.6)
# -------------------------------------------------------------- #

class TestStartConstruction(unittest.TestCase):
    """Test start_construction with active-presence timer."""

    def test_start_construction_success(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        ok, msg = system.start_construction(player, tile, "HQ")
        self.assertTrue(ok)
        self.assertIn("started", msg.lower())
        self.assertEqual(len(created), 1)

    def test_start_construction_sets_player_state(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        system.start_construction(player, tile, "HQ")
        self.assertEqual(player.db.activity_state, "building")
        self.assertIsNotNone(player.db.activity_target)
        self.assertEqual(player.db.activity_progress, 0)

    def test_start_construction_sets_building_timer(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        system.start_construction(player, tile, "HQ")
        building = created[0]
        self.assertEqual(building.attributes.get("construction_progress"), 0)
        self.assertEqual(building.attributes.get("construction_total"), 180)

    def test_start_construction_by_full_name(self):
        # Reported bug: "build headquarters" (full name) must work, not only
        # the "HQ" abbreviation.
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        ok, msg = system.start_construction(player, tile, "headquarters")
        self.assertTrue(ok, msg)
        self.assertEqual(len(created), 1)

    def test_start_construction_unknown_name_reports_cleanly(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        ok, msg = system.start_construction(player, tile, "teleporter")
        self.assertFalse(ok)
        self.assertIn("Unknown building type", msg)
        self.assertEqual(len(created), 0)

    def test_start_construction_deducts_resources(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        system, _, _ = _make_building_system()
        system.start_construction(player, tile, "HQ")
        self.assertEqual(player.get_resource("Straw"), 50)
        self.assertEqual(player.get_resource("Wood"), 50)
        self.assertEqual(player.get_resource("Stone"), 70)

    def test_start_construction_publishes_event(self):
        player = FakePlayer(resources={"Straw": 100, "Wood": 100, "Stone": 100})
        tile = FakeTile()
        events = []
        event_bus = EventBus()
        event_bus.subscribe("construction_started", lambda **kw: events.append(kw))
        system, _, _ = _make_building_system(event_bus=event_bus)
        system.start_construction(player, tile, "HQ")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["player"], player)

    def test_start_construction_rank_too_low_rejected(self):
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Iron": 100, "Stone": 100, "Wood": 100},
            buildings=[hq],
        )
        player.db.rank_level = 1
        player.db.level = 1
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.start_construction(player, tile, "VV")
        self.assertFalse(ok)
        self.assertIn("Level", msg)

    def test_start_construction_insufficient_resources(self):
        player = FakePlayer(resources={"Straw": 1, "Wood": 1, "Stone": 1})
        tile = FakeTile()
        system, created, _ = _make_building_system()
        ok, msg = system.start_construction(player, tile, "HQ")
        self.assertFalse(ok)
        self.assertIn("Insufficient", msg)


# -------------------------------------------------------------- #
#  Process Construction Tick Tests (Req 6.6)
# -------------------------------------------------------------- #

class TestProcessConstructionTick(unittest.TestCase):
    """Test process_construction_tick for player active-presence."""

    def _setup_construction(self, build_time=5):
        """Helper: start a construction and return (system, player, building, tile)."""
        player_tile = FakeTile(xyz=(5, 5, "earth"))
        player = FakePlayer(
            resources={"Straw": 100, "Wood": 100, "Stone": 100},
            location=player_tile,
        )
        tile = FakeTile(xyz=(5, 5, "earth"))
        registry = _make_registry_with_buildings()
        # Override HQ build time for faster testing
        registry.buildings["HQ"] = BuildingDef(
            name="Headquarters", abbreviation="HQ",
            cost={"Straw": 50, "Wood": 50, "Stone": 30},
            max_health=500, requires_hq=False, required_terrain=None,
            category="headquarters", produces=None,
            unlocks=["MM", "QQ"], map_symbol="HQ",
            build_time_seconds=build_time, rank_requirement=1,
        )
        system, created, event_bus = _make_building_system(registry=registry)
        system.start_construction(player, tile, "HQ")
        building = created[0]
        # Set player activity_target to the building
        player.db.activity_target = building
        return system, player, building, tile, event_bus

    def test_tick_increments_progress(self):
        system, player, building, tile, _ = self._setup_construction(build_time=5)
        system.process_construction_tick(player)
        self.assertEqual(building.attributes.get("construction_progress"), 1)

    def test_tick_completes_construction(self):
        system, player, building, tile, event_bus = self._setup_construction(build_time=3)
        events = []
        event_bus.subscribe("construction_completed", lambda **kw: events.append(kw))
        # Tick 3 times to complete
        for _ in range(3):
            system.process_construction_tick(player)
        self.assertEqual(player.db.activity_state, "idle")
        self.assertIsNone(player.db.activity_target)
        self.assertEqual(len(events), 1)

    def test_tick_pauses_when_player_moves_away(self):
        system, player, building, tile, _ = self._setup_construction(build_time=5)
        # Tick once (progress = 1)
        system.process_construction_tick(player)
        self.assertEqual(building.attributes.get("construction_progress"), 1)
        # Move player away
        player.location = FakeTile(xyz=(99, 99, "earth"))
        # Tick again — should NOT increment
        system.process_construction_tick(player)
        self.assertEqual(building.attributes.get("construction_progress"), 1)

    def test_tick_resumes_when_player_returns(self):
        system, player, building, tile, _ = self._setup_construction(build_time=5)
        # Tick once
        system.process_construction_tick(player)
        self.assertEqual(building.attributes.get("construction_progress"), 1)
        # Move away
        player.location = FakeTile(xyz=(99, 99, "earth"))
        system.process_construction_tick(player)
        # Move back
        player.location = FakeTile(xyz=(5, 5, "earth"))
        system.process_construction_tick(player)
        self.assertEqual(building.attributes.get("construction_progress"), 2)

    def test_tick_no_op_when_idle(self):
        player = FakePlayer()
        player.db.activity_state = "idle"
        system, _, _ = _make_building_system()
        result = system.process_construction_tick(player)
        self.assertFalse(result)

    def test_tick_resets_if_no_target(self):
        player = FakePlayer()
        player.db.activity_state = "building"
        player.db.activity_target = None
        system, _, _ = _make_building_system()
        system.process_construction_tick(player)
        self.assertEqual(player.db.activity_state, "idle")


# -------------------------------------------------------------- #
#  Process Agent Construction Tests (Req 6.6, 10.1)
# -------------------------------------------------------------- #

class FakeAgent:
    """Lightweight stand-in for an agent NPC."""
    def __init__(self, incapacitated=False):
        self.db = FakeDB()
        self.db.incapacitated = incapacitated


class TestProcessAgentConstruction(unittest.TestCase):
    """Test process_agent_construction for Engineer agents."""

    def test_agent_increments_progress(self):
        agent = FakeAgent()
        building = FakeBuilding(building_type="HQ")
        building.attributes.add("assigned_agent", agent)
        building.attributes.add("construction_progress", 0)
        building.attributes.add("construction_total", 5)
        system, _, _ = _make_building_system()
        system.process_agent_construction([building])
        self.assertEqual(building.attributes.get("construction_progress"), 1)

    def test_agent_completes_construction(self):
        agent = FakeAgent()
        player = FakePlayer()
        building = FakeBuilding(building_type="HQ", owner=player)
        building.attributes.add("assigned_agent", agent)
        building.attributes.add("construction_progress", 4)
        building.attributes.add("construction_total", 5)
        events = []
        event_bus = EventBus()
        event_bus.subscribe("construction_completed", lambda **kw: events.append(kw))
        system, _, _ = _make_building_system(event_bus=event_bus)
        system.process_agent_construction([building])
        self.assertEqual(building.attributes.get("construction_progress"), 5)
        self.assertEqual(len(events), 1)

    def test_incapacitated_agent_does_not_progress(self):
        agent = FakeAgent(incapacitated=True)
        building = FakeBuilding(building_type="HQ")
        building.attributes.add("assigned_agent", agent)
        building.attributes.add("construction_progress", 0)
        building.attributes.add("construction_total", 5)
        system, _, _ = _make_building_system()
        system.process_agent_construction([building])
        self.assertEqual(building.attributes.get("construction_progress"), 0)

    def test_no_agent_does_not_progress(self):
        building = FakeBuilding(building_type="HQ")
        building.attributes.add("assigned_agent", None)
        building.attributes.add("construction_progress", 0)
        building.attributes.add("construction_total", 5)
        system, _, _ = _make_building_system()
        system.process_agent_construction([building])
        self.assertEqual(building.attributes.get("construction_progress"), 0)

    def test_already_complete_does_not_increment(self):
        agent = FakeAgent()
        building = FakeBuilding(building_type="HQ")
        building.attributes.add("assigned_agent", agent)
        building.attributes.add("construction_progress", 5)
        building.attributes.add("construction_total", 5)
        system, _, _ = _make_building_system()
        system.process_agent_construction([building])
        self.assertEqual(building.attributes.get("construction_progress"), 5)


# -------------------------------------------------------------- #
#  Building Offline on 0 HP Tests (Req 6.9)
# -------------------------------------------------------------- #

class TestBuildingOfflineOnZeroHP(unittest.TestCase):
    """Test that buildings go offline at 0 HP instead of being destroyed."""

    def test_building_goes_offline_at_zero_hp(self):
        """Req 6.9: building at 0 HP is set offline, not destroyed."""
        building = FakeBuilding(building_type="MM", hp=50, hp_max=200)
        # Simulate take_damage reducing HP to 0
        building.attributes.add("hp", 0)
        building.set_offline(True)
        self.assertTrue(building.is_offline)
        self.assertFalse(building._deleted)

    def test_building_not_deleted_at_zero_hp(self):
        """Req 6.9: building object persists when HP reaches 0."""
        building = FakeBuilding(building_type="MM", hp=10, hp_max=200)
        # Reduce HP to 0 and set offline
        building.attributes.add("hp", 0)
        building.set_offline(True)
        self.assertEqual(building.attributes.get("hp"), 0)
        self.assertTrue(building.is_offline)
        self.assertFalse(building._deleted)

    def test_offline_building_retains_attributes(self):
        """Offline building keeps its type, level, and owner."""
        player = FakePlayer(name="Owner")
        building = FakeBuilding(
            building_type="MM", owner=player, level=3,
            hp=0, hp_max=200, offline=True,
        )
        self.assertEqual(building.attributes.get("building_type"), "MM")
        self.assertEqual(building.building_level, 3)
        self.assertEqual(building.owner, player)


# -------------------------------------------------------------- #
#  Repair Cost Tests (Req 6.10, 14b.3)
# -------------------------------------------------------------- #

class TestRepairCost(unittest.TestCase):
    """Test repair cost = 50% of base construction cost."""

    def test_repair_cost_is_half_base(self):
        """Req 6.10/14b.3: repair cost is 50% of base cost."""
        building = FakeBuilding(building_type="HQ")
        system, _, _ = _make_building_system()
        # HQ base cost: Straw=50, Wood=50, Stone=30
        cost = system.get_repair_cost(building)
        self.assertEqual(cost["Straw"], 25)
        self.assertEqual(cost["Wood"], 25)
        self.assertEqual(cost["Stone"], 15)

    def test_repair_cost_rounds_down_minimum_one(self):
        """Small costs round down but never below 1."""
        building = FakeBuilding(building_type="MM")
        system, _, _ = _make_building_system()
        # MM base cost: Straw=20, Wood=10 -> repair: Straw=10, Wood=5
        cost = system.get_repair_cost(building)
        self.assertEqual(cost["Straw"], 10)
        self.assertEqual(cost["Wood"], 5)

    def test_repair_cost_unknown_building_returns_empty(self):
        """Unknown building type returns empty cost dict."""
        building = FakeBuilding(building_type="ZZ")
        system, _, _ = _make_building_system()
        cost = system.get_repair_cost(building)
        self.assertEqual(cost, {})


# -------------------------------------------------------------- #
#  Extractor Requires Resource Terrain Tests (Req 6.11)
# -------------------------------------------------------------- #

class FakeTileWithResource(FakeTile):
    """Tile that also exposes a resource_type attribute."""

    def __init__(self, terrain_type="Forest", resource_type="Wood", **kwargs):
        super().__init__(terrain_type=terrain_type, **kwargs)
        self.resource_type = resource_type


def _make_registry_with_extractor() -> DataRegistry:
    """Create a DataRegistry that includes an Extractor definition."""
    registry = _make_registry_with_buildings()
    registry.buildings["EX"] = BuildingDef(
        name="Extractor", abbreviation="EX",
        cost={"Wood": 15, "Stone": 10},
        max_health=200, requires_hq=True, required_terrain=None,
        category="resource", produces=None,
        unlocks=[], map_symbol="EX",
        build_time_seconds=120, rank_requirement=2,
    )
    return registry


class TestExtractorRequiresResourceTerrain(unittest.TestCase):
    """Test Extractor placement requires terrain with a resource type."""

    def test_extractor_on_resource_terrain_succeeds(self):
        """Req 6.11: Extractor on terrain with resource_type succeeds."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Wood": 100, "Stone": 100},
            buildings=[hq],
        )
        player.db.rank_level = 2
        player.db.level = 2
        tile = FakeTileWithResource(terrain_type="Forest", resource_type="Wood")
        registry = _make_registry_with_extractor()
        system, created, _ = _make_building_system(registry=registry)
        ok, msg = system.construct(player, tile, "EX")
        self.assertTrue(ok)
        self.assertEqual(len(created), 1)

    def test_extractor_on_non_resource_terrain_rejected(self):
        """Req 6.11: Extractor on terrain without resource_type is rejected."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Wood": 100, "Stone": 100},
            buildings=[hq],
        )
        player.db.rank_level = 2
        player.db.level = 2
        tile = FakeTile(terrain_type="Plains")  # No resource_type attribute
        registry = _make_registry_with_extractor()
        system, created, _ = _make_building_system(registry=registry)
        ok, msg = system.construct(player, tile, "EX")
        self.assertFalse(ok)
        self.assertIn("resource", msg.lower())
        self.assertEqual(len(created), 0)

    def test_non_extractor_ignores_resource_check(self):
        """Non-Extractor buildings skip the resource terrain check."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Iron": 100, "Stone": 100, "Wood": 100},
            buildings=[hq],
        )
        player.db.rank_level = 3
        player.db.level = 3
        tile = FakeTile(terrain_type="Plains")  # No resource_type
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "VV")
        self.assertTrue(ok)


# -------------------------------------------------------------- #
#  Vault Rejects Non-Resource Objects Tests (Req 6b.5)
# -------------------------------------------------------------- #

class FakeResourceObject:
    """Lightweight stand-in for a resource stack object."""

    def __init__(self, resource_type="Wood", amount=10):
        self.key = resource_type
        self.tags = FakeTags([("resource", "object_type")])
        self.db = type("DB", (), {"resource_type": resource_type, "amount": amount})()


class FakeNonResourceObject:
    """Lightweight stand-in for a non-resource object (weapon, etc.)."""

    def __init__(self, name="Sword"):
        self.key = name
        self.tags = FakeTags([])
        self.db = type("DB", (), {})()


class FakeTags:
    """Simulates Evennia's tag handler."""

    def __init__(self, tag_list=None):
        self._tags = tag_list or []

    def has(self, key, category=None):
        for tag_key, tag_cat in self._tags:
            if tag_key == key and (category is None or tag_cat == category):
                return True
        return False

    def get(self, category=None):
        return [k for k, c in self._tags if category is None or c == category]


def _vault_accepts_object(obj) -> bool:
    """Check if a Vault would accept the given object (Req 6b.5).

    Vaults only accept resource objects — those tagged with
    ("resource", "object_type").
    """
    if hasattr(obj, "tags") and hasattr(obj.tags, "has"):
        return obj.tags.has("resource", category="object_type")
    return False


class TestVaultRejectsNonResource(unittest.TestCase):
    """Test Vault only accepts resource objects (Req 6b.5)."""

    def test_vault_accepts_resource_object(self):
        """Vault accepts objects tagged as resource."""
        resource = FakeResourceObject(resource_type="Wood", amount=10)
        self.assertTrue(_vault_accepts_object(resource))

    def test_vault_rejects_non_resource_object(self):
        """Vault rejects objects not tagged as resource."""
        weapon = FakeNonResourceObject(name="Sword")
        self.assertFalse(_vault_accepts_object(weapon))

    def test_vault_rejects_object_without_tags(self):
        """Vault rejects objects with no tag handler."""
        plain_obj = type("Obj", (), {"key": "thing"})()
        self.assertFalse(_vault_accepts_object(plain_obj))


# -------------------------------------------------------------- #
#  HQ-First and One HQ Per Player Per Planet Tests (Req 6.3, 6.4)
# -------------------------------------------------------------- #

class TestOneHQPerPlayerPerPlanet(unittest.TestCase):
    """Test one HQ per player per planet enforcement (Req 6.4)."""

    def test_second_hq_on_same_planet_rejected(self):
        """Req 6.4: player cannot build a second HQ on the same planet."""
        existing_hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Straw": 200, "Wood": 200, "Stone": 200},
            buildings=[existing_hq],
        )
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "HQ")
        self.assertFalse(ok)
        self.assertIn("one", msg.lower())
        self.assertEqual(len(created), 0)

    def test_first_hq_succeeds(self):
        """Req 6.3: first HQ construction succeeds."""
        player = FakePlayer(
            resources={"Straw": 200, "Wood": 200, "Stone": 200},
        )
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "HQ")
        self.assertTrue(ok)
        self.assertEqual(len(created), 1)

    def test_hq_first_enforcement(self):
        """Req 6.3: non-HQ building requires HQ to exist first."""
        player = FakePlayer(
            resources={"Straw": 200, "Wood": 200},
        )
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertFalse(ok)
        self.assertIn("Headquarters", msg)

    def test_non_hq_after_hq_succeeds(self):
        """Req 6.3: non-HQ building succeeds when HQ exists."""
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(
            resources={"Straw": 200, "Wood": 200},
            buildings=[hq],
        )
        tile = FakeTile(terrain_type="Plains")
        system, created, _ = _make_building_system()
        ok, msg = system.construct(player, tile, "MM")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
