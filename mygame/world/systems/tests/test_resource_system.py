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
            capabilities=frozenset({"harvestable", "upgradable"}),
        ),
        "QQ": BuildingDef(
            name="Quarry", abbreviation="QQ",
            cost={"Wood": 20, "Stone": 10},
            max_health=200, requires_hq=True, required_terrain="Rock",
            category="resource", produces="Stone", unlocks=[], map_symbol="QQ",
            capabilities=frozenset({"harvestable", "upgradable"}),
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


# -------------------------------------------------------------- #
#  Enhanced Fakes for active-presence & Extractor tests
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db handler (attribute-style access)."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakePlayerWithDB(FakePlayer):
    """FakePlayer extended with a db handler for active-presence state."""

    def __init__(self, name="TestPlayer", resources=None, location=None):
        super().__init__(name=name, resources=resources)
        self.db = FakeDB(
            activity_state="idle",
            activity_target=None,
            activity_progress=0,
            coord_x=0,
            coord_y=0,
        )
        self.location = location


class FakeExtractor:
    """Lightweight stand-in for an Extractor building with inventory."""

    def __init__(self, building_type="EX", owner=None, level=1,
                 offline=False, assigned_agent=None, resource_inventory=None):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": owner,
            "building_level": level,
            "offline": offline,
            "assigned_agent": assigned_agent,
            "resource_inventory": resource_inventory if resource_inventory is not None else {},
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


class FakeAgent:
    """Lightweight stand-in for an NPC agent."""

    def __init__(self, role="harvester", incapacitated=False):
        self.db = FakeDB(role=role, incapacitated=incapacitated)


def _make_extractor_registry() -> DataRegistry:
    """Create a DataRegistry with an Extractor building definition."""
    registry = _make_registry()
    registry.buildings["EX"] = BuildingDef(
        name="Extractor", abbreviation="EX",
        cost={"Wood": 20, "Stone": 10},
        max_health=200, requires_hq=True, required_terrain="Forest",
        category="resource", produces="Wood", unlocks=[], map_symbol="EX",
        storage_capacity=100,
        capabilities=frozenset(
            {"harvestable", "upgradable", "requires_resource_terrain"}
        ),
    )
    return registry


# -------------------------------------------------------------- #
#  start_harvest Tests
# -------------------------------------------------------------- #

class TestStartHarvest(unittest.TestCase):
    """Test start_harvest sets player into harvesting state."""

    def test_start_harvest_success(self):
        tile = FakeTile(
            terrain_type="Forest",
            resource_node={"resource_type": "Wood", "depleted": False},
        )
        player = FakePlayerWithDB(location=tile)
        system, _ = _make_system()
        ok, msg = system.start_harvest(player, tile)
        self.assertTrue(ok)
        self.assertEqual(player.db.activity_state, "harvesting")
        self.assertIs(player.db.activity_target, tile)
        self.assertEqual(player.db.activity_progress, 0)

    def test_start_harvest_no_node(self):
        tile = FakeTile(terrain_type="Plains")
        player = FakePlayerWithDB(location=tile)
        system, _ = _make_system()
        ok, msg = system.start_harvest(player, tile)
        self.assertFalse(ok)
        self.assertIn("No resource node", msg)

    def test_start_harvest_depleted(self):
        tile = FakeTile(
            terrain_type="Forest",
            resource_node={"resource_type": "Wood", "depleted": True},
        )
        player = FakePlayerWithDB(location=tile)
        system, _ = _make_system()
        ok, msg = system.start_harvest(player, tile)
        self.assertFalse(ok)
        self.assertIn("depleted", msg)

    def test_start_harvest_no_resource_type(self):
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={"resource_type": None, "depleted": False},
        )
        player = FakePlayerWithDB(location=tile)
        system, _ = _make_system()
        ok, msg = system.start_harvest(player, tile)
        self.assertFalse(ok)


# -------------------------------------------------------------- #
#  process_harvest_tick Tests
# -------------------------------------------------------------- #

class TestProcessHarvestTick(unittest.TestCase):
    """Test active-presence harvest tick processing."""

    def _setup_harvesting_player(self):
        tile = FakeTile(
            terrain_type="Forest",
            resource_node={"resource_type": "Wood", "depleted": False},
        )
        player = FakePlayerWithDB(location=tile)
        player.db.activity_state = "harvesting"
        player.db.activity_target = tile
        player.db.activity_progress = 0
        return player, tile

    def test_yields_on_cooldown(self):
        """Resources yielded every balance.harvest_cooldown_ticks ticks, dropped on tile."""
        player, tile = self._setup_harvesting_player()
        system, _ = _make_system()

        # Ticks 1-3: no yield (cooldown is 4)
        for _ in range(3):
            self.assertFalse(system.process_harvest_tick(player))

        # Tick 4: yield (1 unit per 4 ticks on raw terrain, dropped on tile)
        self.assertTrue(system.process_harvest_tick(player))
        inv = ResourceSystem.get_tile_inventory(tile)
        self.assertEqual(inv.get("Wood", 0), 1)

    def test_multiple_cycles(self):
        """Resources accumulate on tile over multiple cooldown cycles."""
        player, tile = self._setup_harvesting_player()
        system, _ = _make_system()

        for _ in range(8):
            system.process_harvest_tick(player)

        # 2 full cycles × 1 unit = 2
        inv = ResourceSystem.get_tile_inventory(tile)
        self.assertEqual(inv.get("Wood", 0), 2)

    def test_not_harvesting_state(self):
        player = FakePlayerWithDB()
        player.db.activity_state = "idle"
        system, _ = _make_system()
        self.assertFalse(system.process_harvest_tick(player))

    def test_player_moved_away(self):
        """Harvest pauses when player is not on the target tile."""
        player, tile = self._setup_harvesting_player()
        player.location = None  # moved away
        system, _ = _make_system()
        self.assertFalse(system.process_harvest_tick(player))
        # State preserved (paused, not reset)
        self.assertEqual(player.db.activity_state, "harvesting")

    def test_depleted_node_resets_state(self):
        tile = FakeTile(
            terrain_type="Forest",
            resource_node={"resource_type": "Wood", "depleted": True},
        )
        player = FakePlayerWithDB(location=tile)
        player.db.activity_state = "harvesting"
        player.db.activity_target = tile
        system, _ = _make_system()
        self.assertFalse(system.process_harvest_tick(player))
        self.assertEqual(player.db.activity_state, "idle")

    def test_publishes_event_on_yield(self):
        player, tile = self._setup_harvesting_player()
        events = []
        event_bus = EventBus()
        event_bus.subscribe("resource_gathered", lambda **kw: events.append(kw))
        system = ResourceSystem(_make_registry(), event_bus)

        # Cooldown is 4 ticks, so tick 4 yields
        for _ in range(4):
            system.process_harvest_tick(player)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["resource_type"], "Wood")
        self.assertEqual(events[0]["amount"], 1)

    def test_harvest_drop_notifies_player_end_to_end(self):
        """The harvest_drop notification renders through the real presenter
        (guards producer->formatter key drift for this kind)."""
        from mygame.world.presenters.test_support import attach_presenter

        player, tile = self._setup_harvesting_player()
        messages = []
        player.msg = lambda m: messages.append(m)
        event_bus = EventBus()
        attach_presenter(event_bus)
        system = ResourceSystem(_make_registry(), event_bus)

        for _ in range(4):  # cooldown is 4 ticks -> yields + drops on tick 4
            system.process_harvest_tick(player)

        self.assertTrue(any("dropped" in m for m in messages))


# -------------------------------------------------------------- #
#  Extractor Inventory Tests
# -------------------------------------------------------------- #

class TestExtractorInventory(unittest.TestCase):
    """Test Extractor storage capacity and inventory management."""

    def test_capacity_level_1(self):
        self.assertEqual(ResourceSystem.get_extractor_capacity(1), 100)

    def test_capacity_level_3(self):
        # 100 + 50 × (3-1) = 200
        self.assertEqual(ResourceSystem.get_extractor_capacity(3), 200)

    def test_capacity_level_5(self):
        # 100 + 50 × (5-1) = 300
        self.assertEqual(ResourceSystem.get_extractor_capacity(5), 300)

    def test_add_to_inventory(self):
        building = FakeExtractor(level=1)
        added = ResourceSystem.add_to_extractor_inventory(building, "Wood", 50, 1)
        self.assertEqual(added, 50)
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertEqual(inv["Wood"], 50)

    def test_add_respects_capacity(self):
        building = FakeExtractor(level=1, resource_inventory={"Wood": 80})
        # Capacity is 100, only 20 space left
        added = ResourceSystem.add_to_extractor_inventory(building, "Wood", 50, 1)
        self.assertEqual(added, 20)
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertEqual(inv["Wood"], 100)

    def test_add_at_full_capacity(self):
        building = FakeExtractor(level=1, resource_inventory={"Wood": 100})
        added = ResourceSystem.add_to_extractor_inventory(building, "Wood", 10, 1)
        self.assertEqual(added, 0)

    def test_stored_amount(self):
        building = FakeExtractor(
            level=1, resource_inventory={"Wood": 30, "Stone": 20}
        )
        self.assertEqual(ResourceSystem.get_extractor_stored_amount(building), 50)

    def test_empty_inventory(self):
        building = FakeExtractor(level=1)
        self.assertEqual(ResourceSystem.get_extractor_stored_amount(building), 0)
        self.assertEqual(ResourceSystem.get_extractor_inventory(building), {})


# -------------------------------------------------------------- #
#  process_extractor_production Tests
# -------------------------------------------------------------- #

class TestProcessExtractorProduction(unittest.TestCase):
    """Test Harvester agent production via Extractors."""

    def test_production_with_harvester(self):
        player = FakePlayer()
        agent = FakeAgent(role="harvester")
        building = FakeExtractor(
            building_type="EX", owner=player, level=1, assigned_agent=agent
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        # base_rate=1, level 1 → 1 × (1 + 0.25×0) = 1
        self.assertEqual(inv.get("Wood", 0), 1)

    def test_production_scales_with_level(self):
        player = FakePlayer()
        agent = FakeAgent(role="harvester")
        building = FakeExtractor(
            building_type="EX", owner=player, level=3, assigned_agent=agent
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        # base_rate=1, level 3 → 1 × (1 + 0.25×2) = 1.5 → int(1.5) = 1, max(1,1) = 1
        self.assertEqual(inv.get("Wood", 0), 1)

    def test_production_no_agent(self):
        player = FakePlayer()
        building = FakeExtractor(
            building_type="EX", owner=player, level=1, assigned_agent=None
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertEqual(sum(inv.values()), 0)

    def test_production_incapacitated_agent(self):
        player = FakePlayer()
        agent = FakeAgent(role="harvester", incapacitated=True)
        building = FakeExtractor(
            building_type="EX", owner=player, level=1, assigned_agent=agent
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertEqual(sum(inv.values()), 0)

    def test_production_wrong_role(self):
        player = FakePlayer()
        agent = FakeAgent(role="soldier")
        building = FakeExtractor(
            building_type="EX", owner=player, level=1, assigned_agent=agent
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertEqual(sum(inv.values()), 0)

    def test_production_pauses_at_capacity(self):
        """Resources now accumulate without capacity limit (floor drops)."""
        player = FakePlayer()
        agent = FakeAgent(role="harvester")
        building = FakeExtractor(
            building_type="EX", owner=player, level=1,
            assigned_agent=agent, resource_inventory={"Wood": 100},
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertGreater(inv["Wood"], 100)  # resources accumulate freely

    def test_production_skips_offline(self):
        player = FakePlayer()
        agent = FakeAgent(role="harvester")
        building = FakeExtractor(
            building_type="EX", owner=player, level=1,
            assigned_agent=agent, offline=True,
        )
        registry = _make_extractor_registry()
        system, _ = _make_system(registry=registry)
        system.process_extractor_production([building])
        inv = ResourceSystem.get_extractor_inventory(building)
        self.assertEqual(sum(inv.values()), 0)

    def test_production_publishes_event(self):
        player = FakePlayer()
        agent = FakeAgent(role="harvester")
        building = FakeExtractor(
            building_type="EX", owner=player, level=1, assigned_agent=agent
        )
        events = []
        event_bus = EventBus()
        event_bus.subscribe("resource_gathered", lambda **kw: events.append(kw))
        registry = _make_extractor_registry()
        system = ResourceSystem(registry, event_bus)
        system.process_extractor_production([building])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["resource_type"], "Wood")
