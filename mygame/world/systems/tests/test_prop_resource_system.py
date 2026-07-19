"""
Property-based tests for ResourceSystem.

Property 4: Harvest yields correct resource type
Property 5: Resource node respawn cycle
Property 10: Resource production scales with level

Validates: Requirements 2.3, 2.6, 2.7, 5.2, 15.1, 15.2, 15.4
"""

import sys
import types
import unittest

from hypothesis import given, settings
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
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.resource_system import ResourceSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig,
    BuildingDef,
    TerrainDef,
)
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

ALL_RESOURCE_TYPES = [
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
]

# Terrain-to-resource mapping (from terrain.yaml)
TERRAIN_RESOURCE_MAP = {
    "Plains": "Straw",
    "Dirt": "Clay",
    "Forest": "Wood",
    "Rock": "Stone",
    "Mountain": "Iron",
    "Power_Grid": "Energy",
    "Scrapyard": "Metals",
    "Circuit_Field": "Circuits",
}

# Terrains that have resources
RESOURCE_TERRAINS = list(TERRAIN_RESOURCE_MAP.keys())

PRODUCTION_SCALING = {1: 10, 2: 50, 3: 150, 4: 400, 5: 1000}

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
        self._resources = {r: 0 for r in ALL_RESOURCE_TYPES}
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

# Resource building definitions
_HARVEST_CAPS = frozenset({"harvestable", "upgradable"})
RESOURCE_BUILDING_DEFS = {
    "MM": BuildingDef(
        name="Mill", abbreviation="MM",
        cost={"Straw": 20, "Wood": 10},
        max_health=150, requires_hq=True, required_terrain="Plains",
        category="resource", produces="Straw", unlocks=[], map_symbol="MM",
        capabilities=_HARVEST_CAPS,
    ),
    "QQ": BuildingDef(
        name="Quarry", abbreviation="QQ",
        cost={"Wood": 20, "Stone": 10},
        max_health=200, requires_hq=True, required_terrain="Rock",
        category="resource", produces="Stone", unlocks=[], map_symbol="QQ",
        capabilities=_HARVEST_CAPS,
    ),
    "II": BuildingDef(
        name="Mine", abbreviation="II",
        cost={"Wood": 30, "Stone": 20},
        max_health=250, requires_hq=True, required_terrain="Mountain",
        category="resource", produces="Iron", unlocks=[], map_symbol="II",
        capabilities=_HARVEST_CAPS,
    ),
    "LL": BuildingDef(
        name="Lumberyard", abbreviation="LL",
        cost={"Straw": 15, "Wood": 15},
        max_health=150, requires_hq=True, required_terrain="Forest",
        category="resource", produces="Wood", unlocks=[], map_symbol="LL",
        capabilities=_HARVEST_CAPS,
    ),
    "KK": BuildingDef(
        name="Kiln", abbreviation="KK",
        cost={"Wood": 20, "Clay": 10},
        max_health=150, requires_hq=True, required_terrain="Dirt",
        category="resource", produces="Clay", unlocks=[], map_symbol="KK",
        capabilities=_HARVEST_CAPS,
    ),
}

def _make_registry(
    gather_amount: int = 1,
    respawn_ticks: int = 30,
) -> DataRegistry:
    """Create a DataRegistry with test definitions."""
    registry = DataRegistry()
    registry.balance = BalanceConfig(
        gather_amount=gather_amount,
        resource_respawn_ticks=respawn_ticks,
        harvest_crit_chance=0.0,  # deterministic drop counts in tests
    )
    registry.terrain = {
        t: TerrainDef(terrain_type=t, map_symbol=t[:2], resource_type=r)
        for t, r in TERRAIN_RESOURCE_MAP.items()
    }
    registry.buildings = dict(RESOURCE_BUILDING_DEFS)
    return registry

def _make_system(registry=None, event_bus=None):
    """Create a ResourceSystem with optional overrides."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    return ResourceSystem(registry, event_bus), event_bus

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def terrain_with_resource_strategy(draw):
    """Generate a terrain type that has an associated resource."""
    terrain = draw(st.sampled_from(RESOURCE_TERRAINS))
    return terrain, TERRAIN_RESOURCE_MAP[terrain]

@st.composite
def respawn_ticks_strategy(draw):
    """Generate a respawn tick count (1-100)."""
    return draw(st.integers(min_value=1, max_value=100))

# -------------------------------------------------------------- #
#  Property 4: Harvest yields correct resource type
#  **Validates: Requirements 2.3**
# -------------------------------------------------------------- #

class TestProperty4HarvestYieldsCorrectResource(unittest.TestCase):
    """Property 4: Harvest yields correct resource type.

    For any terrain type with an associated resource, harvesting a
    non-depleted resource node on that terrain SHALL yield exactly
    the resource type matching the terrain, and the player's counter
    for that resource SHALL increase by gather_amount.

    **Validates: Requirements 2.3**
    """

    @given(
        terrain_resource=terrain_with_resource_strategy(),
        initial_amount=st.integers(min_value=0, max_value=10000),
    )
    @settings(max_examples=100)
    def test_harvest_yields_terrain_resource(self, terrain_resource, initial_amount):
        """Harvesting yields the resource matching the tile's terrain."""
        terrain, expected_resource = terrain_resource

        player = FakePlayer(resources={expected_resource: initial_amount})
        tile = FakeTile(
            terrain_type=terrain,
            resource_node={
                "resource_type": expected_resource,
                "depleted": False,
                "respawn_counter": 0,
            },
        )

        system, _ = _make_system()
        ok, msg = system.harvest(player, tile)

        self.assertTrue(ok, f"Harvest should succeed on {terrain}: {msg}")
        self.assertEqual(
            player.get_resource(expected_resource),
            initial_amount + 1,
            f"Expected {expected_resource} to increase by gather_amount (1)",
        )

    @given(
        terrain_resource=terrain_with_resource_strategy(),
    )
    @settings(max_examples=100)
    def test_harvest_only_changes_matching_resource(self, terrain_resource):
        """Harvesting only increases the matching resource, not others."""
        terrain, expected_resource = terrain_resource

        player = FakePlayer()
        tile = FakeTile(
            terrain_type=terrain,
            resource_node={
                "resource_type": expected_resource,
                "depleted": False,
                "respawn_counter": 0,
            },
        )

        system, _ = _make_system()
        system.harvest(player, tile)

        for r in ALL_RESOURCE_TYPES:
            if r == expected_resource:
                self.assertEqual(player.get_resource(r), 1)
            else:
                self.assertEqual(
                    player.get_resource(r), 0,
                    f"Resource {r} should be unchanged after harvesting {expected_resource}",
                )

    @given(terrain_resource=terrain_with_resource_strategy())
    @settings(max_examples=100)
    def test_harvest_depletes_node(self, terrain_resource):
        """After harvest, the node is marked depleted."""
        terrain, expected_resource = terrain_resource

        player = FakePlayer()
        tile = FakeTile(
            terrain_type=terrain,
            resource_node={
                "resource_type": expected_resource,
                "depleted": False,
                "respawn_counter": 0,
            },
        )

        system, _ = _make_system()
        system.harvest(player, tile)

        node = tile.attributes.get("resource_node_data")
        self.assertTrue(node["depleted"])

# -------------------------------------------------------------- #
#  Property 5: Resource node respawn cycle
#  **Validates: Requirements 2.6, 2.7, 15.1, 15.2, 15.4**
# -------------------------------------------------------------- #

class TestProperty5RespawnCycle(unittest.TestCase):
    """Property 5: Resource node respawn cycle.

    A depleted resource node with respawn_counter = N SHALL become
    non-depleted after exactly N calls to process_respawns. The counter
    SHALL decrement by exactly 1 each tick.

    **Validates: Requirements 2.6, 2.7, 15.1, 15.2, 15.4**
    """

    @given(respawn_ticks=respawn_ticks_strategy())
    @settings(max_examples=100)
    def test_respawn_after_exact_ticks(self, respawn_ticks):
        """A depleted node respawns after exactly respawn_ticks ticks."""
        tile = FakeTile(
            terrain_type="Plains",
            resource_node={
                "resource_type": "Straw",
                "depleted": True,
                "respawn_counter": respawn_ticks,
            },
        )

        system, _ = _make_system()

        # Process N-1 ticks: should still be depleted
        for i in range(respawn_ticks - 1):
            system.process_respawns([tile])
            node = tile.attributes.get("resource_node_data")
            self.assertTrue(
                node["depleted"],
                f"Node should still be depleted after {i + 1} ticks "
                f"(needs {respawn_ticks})",
            )
            self.assertEqual(
                node["respawn_counter"],
                respawn_ticks - (i + 1),
                f"Counter should be {respawn_ticks - (i + 1)} after {i + 1} ticks",
            )

        # Process the Nth tick: should be restored
        system.process_respawns([tile])
        node = tile.attributes.get("resource_node_data")
        self.assertFalse(
            node["depleted"],
            f"Node should be restored after exactly {respawn_ticks} ticks",
        )
        self.assertEqual(node["respawn_counter"], 0)

    @given(
        terrain_resource=terrain_with_resource_strategy(),
        respawn_ticks=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_harvest_then_respawn_cycle(self, terrain_resource, respawn_ticks):
        """Full cycle: harvest depletes, then respawns after configured ticks."""
        terrain, resource = terrain_resource

        registry = _make_registry(respawn_ticks=respawn_ticks)
        system = ResourceSystem(registry, EventBus())

        player = FakePlayer()
        tile = FakeTile(
            terrain_type=terrain,
            resource_node={
                "resource_type": resource,
                "depleted": False,
                "respawn_counter": 0,
            },
        )

        # Harvest depletes the node
        ok, _ = system.harvest(player, tile)
        self.assertTrue(ok)
        node = tile.attributes.get("resource_node_data")
        self.assertTrue(node["depleted"])
        self.assertEqual(node["respawn_counter"], respawn_ticks)

        # Process exactly respawn_ticks ticks
        for _ in range(respawn_ticks):
            system.process_respawns([tile])

        node = tile.attributes.get("resource_node_data")
        self.assertFalse(node["depleted"])

if __name__ == "__main__":
    unittest.main()
