"""
Unit tests for ResourceSystem.

Tests harvest validation, production processing, and respawn cycles.

Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 5.2, 5.8, 15.1, 15.2,
              15.3, 15.4
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

from mygame.world.systems.resource_system import ResourceSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig, BuildingDef, TerrainDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

RESOURCE_TYPES = (
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
)

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

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""

    def __init__(self, name="TestPlayer", resources=None):
        self.key = name
        self._resources = {r: 0 for r in RESOURCE_TYPES}
        if resources:
            self._resources.update(resources)

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

class FakeTile:
    """Lightweight stand-in for an OverworldRoom tile."""

    def __init__(self, terrain_type="Plains", resource_node=None):
        self._terrain_type = terrain_type
        self.attributes = FakeAttributes()
        if resource_node is not None:
            self.attributes.add("resource_node_data", resource_node)

    @property
    def terrain_type(self):
        return self._terrain_type

    @property
    def resource_node(self):
        return self.attributes.get("resource_node_data", default=None)

class FakeBuilding:
    """Lightweight stand-in for a Building object."""

    def __init__(self, building_type="MM", owner=None, level=1, offline=False):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": owner,
            "building_level": level,
            "offline": offline,
        })
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def building_level(self):
        return self.attributes.get("building_level", default=1)

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))

def _make_registry() -> DataRegistry:
    """Create a DataRegistry with test definitions."""
    registry = DataRegistry()
    registry.balance = BalanceConfig(
        gather_amount=1,
        resource_respawn_ticks=30,
        production_scaling={1: 10, 2: 50, 3: 150, 4: 400, 5: 1000},
    )
    registry.terrain = {
        "Plains": TerrainDef(terrain_type="Plains", map_symbol="PP", resource_type="Straw"),
        "Forest": TerrainDef(terrain_type="Forest", map_symbol="FF", resource_type="Wood"),
        "Rock": TerrainDef(terrain_type="Rock", map_symbol="RR", resource_type="Stone"),
    }
    registry.buildings = {
        "MM": BuildingDef(
            name="Mill", abbreviation="MM",
            cost={"Straw": 20, "Wood": 10},
            max_health=150, requires_hq=True, required_terrain="Plains",
            category="resource", produces="Straw", unlocks=[], map_symbol="MM",
        ),
        "QQ": BuildingDef(
            name="Quarry", abbreviation="QQ",
            cost={"Wood": 20, "Stone": 10},
            max_health=200, requires_hq=True, required_terrain="Rock",
            category="resource", produces="Stone", unlocks=[], map_symbol="QQ",
        ),
        "VV": BuildingDef(
            name="Turret", abbreviation="VV",
            cost={"Iron": 50, "Stone": 40, "Wood": 20},
            max_health=300, requires_hq=True, required_terrain=None,
            category="defense", produces=None, unlocks=[], map_symbol="VV",
        ),
    }
    return registry

def _make_system(registry=None, event_bus=None):
    """Create a ResourceSystem with optional overrides."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    return ResourceSystem(registry, event_bus), event_bus

# -------------------------------------------------------------- #
#  Harvest Tests
# -------------------------------------------------------------- #

class TestHarvestSuccess(unittest.TestCase):
    """Test successful resource harvesting."""

    def test_harvest_yields_correct_resource(self):
        player = FakePlayer()
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": False, "respawn_counter": 0},
        )
        system, _ = _make_system()
        ok, msg = system.harvest(player, tile)
        self.assertTrue(ok)
        self.assertEqual(player.get_resource("Straw"), 1)

    def test_harvest_marks_node_depleted(self):
        player = FakePlayer()
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": False, "respawn_counter": 0},
        )
        system, _ = _make_system()
        system.harvest(player, tile)
        node = tile.attributes.get("resource_node_data")
        self.assertTrue(node["depleted"])
        self.assertEqual(node["respawn_counter"], 30)

    def test_harvest_publishes_event(self):
        player = FakePlayer()
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": False, "respawn_counter": 0},
        )
        events = []
        event_bus = EventBus()
        event_bus.subscribe("resource_gathered", lambda **kw: events.append(kw))
        system = ResourceSystem(_make_registry(), event_bus)
        system.harvest(player, tile)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["resource_type"], "Straw")
        self.assertEqual(events[0]["amount"], 1)
        self.assertEqual(events[0]["player"], player)

class TestHarvestFailures(unittest.TestCase):
    """Test harvest rejection cases."""

    def test_no_resource_node(self):
        player = FakePlayer()
        tile = FakeTile(terrain_type="Plains")
        system, _ = _make_system()
        ok, msg = system.harvest(player, tile)
        self.assertFalse(ok)
        self.assertIn("No resource node", msg)

    def test_depleted_node(self):
        player = FakePlayer()
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": True, "respawn_counter": 15},
        )
        system, _ = _make_system()
        ok, msg = system.harvest(player, tile)
        self.assertFalse(ok)
        self.assertIn("depleted", msg)

# -------------------------------------------------------------- #
#  Production Tests
# -------------------------------------------------------------- #

class TestProcessProduction(unittest.TestCase):
    """Test automated resource building production."""

    def test_production_adds_resources_to_owner(self):
        player = FakePlayer()
        building = FakeBuilding(building_type="MM", owner=player, level=1)
        system, _ = _make_system()
        system.process_production([building])
        self.assertEqual(player.get_resource("Straw"), 10)

    def test_production_scales_with_level(self):
        player = FakePlayer()
        building = FakeBuilding(building_type="MM", owner=player, level=3)
        system, _ = _make_system()
        system.process_production([building])
        self.assertEqual(player.get_resource("Straw"), 150)

    def test_production_skips_offline_buildings(self):
        player = FakePlayer()
        building = FakeBuilding(building_type="MM", owner=player, level=1, offline=True)
        system, _ = _make_system()
        system.process_production([building])
        self.assertEqual(player.get_resource("Straw"), 0)

    def test_production_skips_non_resource_buildings(self):
        player = FakePlayer()
        building = FakeBuilding(building_type="VV", owner=player, level=1)
        system, _ = _make_system()
        system.process_production([building])
        # Turret doesn't produce resources
        for r in RESOURCE_TYPES:
            self.assertEqual(player.get_resource(r), 0)

    def test_production_multiple_buildings(self):
        player = FakePlayer()
        b1 = FakeBuilding(building_type="MM", owner=player, level=1)
        b2 = FakeBuilding(building_type="QQ", owner=player, level=2)
        system, _ = _make_system()
        system.process_production([b1, b2])
        self.assertEqual(player.get_resource("Straw"), 10)
        self.assertEqual(player.get_resource("Stone"), 50)

# -------------------------------------------------------------- #
#  Respawn Tests
# -------------------------------------------------------------- #

class TestProcessRespawns(unittest.TestCase):
    """Test resource node respawn cycle."""

    def test_respawn_decrements_counter(self):
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": True, "respawn_counter": 10},
        )
        system, _ = _make_system()
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertEqual(node["respawn_counter"], 9)
        self.assertTrue(node["depleted"])

    def test_respawn_restores_at_zero(self):
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": True, "respawn_counter": 1},
        )
        system, _ = _make_system()
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertFalse(node["depleted"])
        self.assertEqual(node["respawn_counter"], 0)

    def test_respawn_skips_non_depleted(self):
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": False, "respawn_counter": 0},
        )
        system, _ = _make_system()
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertFalse(node["depleted"])

    def test_respawn_skips_tiles_without_nodes(self):
        tile = FakeTile(terrain_type="Plains")
        system, _ = _make_system()
        # Should not raise
        system.process_respawns([tile])

    def test_full_respawn_cycle(self):
        """A node depleted with counter=3 respawns after exactly 3 ticks."""
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": "Straw", "depleted": True, "respawn_counter": 3},
        )
        system, _ = _make_system()

        # Tick 1: counter 3 -> 2
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertTrue(node["depleted"])
        self.assertEqual(node["respawn_counter"], 2)

        # Tick 2: counter 2 -> 1
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertTrue(node["depleted"])
        self.assertEqual(node["respawn_counter"], 1)

        # Tick 3: counter 1 -> 0, restored
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertFalse(node["depleted"])
        self.assertEqual(node["respawn_counter"], 0)

if __name__ == "__main__":
    unittest.main()
