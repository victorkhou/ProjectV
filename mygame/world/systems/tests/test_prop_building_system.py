"""
Property-based tests for BuildingSystem.

Property 6: HQ prerequisite enforcement
Property 7: Building construction resource deduction
Property 8: Terrain-restricted building placement
Property 9: Resource building level invariant
Property 11: Upgrade cost formula

Validates: Requirements 3.1, 3.2, 3.3, 3.6, 4.2, 4.4, 5.1, 5.4, 5.6, 5.7
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
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.building_system import BuildingSystem, MAX_BUILDING_LEVEL  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BuildingDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

ALL_RESOURCE_TYPES = [
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
]

# Terrain types used in tests
TERRAIN_TYPES = ["Plains", "Dirt", "Forest", "Rock", "Mountain",
                 "Power_Grid", "Scrapyard", "Circuit_Field", "Ruins"]

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self):
        self.combat_lockout_tick = 0

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""

    def __init__(self, name="TestPlayer", resources=None, buildings=None, location=None):
        self.key = name
        self.db = FakeDB()
        self._resources = {r: 0 for r in ALL_RESOURCE_TYPES}
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

    def __init__(self, building_type="HQ", owner=None, level=1,
                 hp=500, hp_max=500, offline=False):
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

# -------------------------------------------------------------- #
#  Test building definitions
# -------------------------------------------------------------- #

# HQ: no terrain requirement, no HQ prerequisite
HQ_DEF = BuildingDef(
    name="Headquarters", abbreviation="HQ",
    cost={"Straw": 50, "Wood": 50, "Stone": 30},
    max_health=500, requires_hq=False, required_terrain=None,
    category="headquarters", produces=None,
    unlocks=["MM", "QQ", "II", "LL", "KK"], map_symbol="HQ",
)

# Resource buildings with terrain requirements
RESOURCE_BUILDING_DEFS = {
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
    "II": BuildingDef(
        name="Mine", abbreviation="II",
        cost={"Wood": 30, "Stone": 20},
        max_health=250, requires_hq=True, required_terrain="Mountain",
        category="resource", produces="Iron", unlocks=[], map_symbol="II",
    ),
    "LL": BuildingDef(
        name="Lumberyard", abbreviation="LL",
        cost={"Straw": 15, "Wood": 15},
        max_health=150, requires_hq=True, required_terrain="Forest",
        category="resource", produces="Wood", unlocks=[], map_symbol="LL",
    ),
    "KK": BuildingDef(
        name="Kiln", abbreviation="KK",
        cost={"Wood": 20, "Clay": 10},
        max_health=150, requires_hq=True, required_terrain="Dirt",
        category="resource", produces="Clay", unlocks=[], map_symbol="KK",
    ),
}

# Non-resource buildings (no terrain requirement)
NON_RESOURCE_BUILDING_DEFS = {
    "VV": BuildingDef(
        name="Turret", abbreviation="VV",
        cost={"Iron": 50, "Stone": 40, "Wood": 20},
        max_health=300, requires_hq=True, required_terrain=None,
        category="defense", produces=None, unlocks=[], map_symbol="VV",
    ),
    "AA": BuildingDef(
        name="Armory", abbreviation="AA",
        cost={"Iron": 40, "Wood": 30, "Stone": 20},
        max_health=200, requires_hq=True, required_terrain=None,
        category="equipment", produces="weapon", unlocks=[], map_symbol="AA",
    ),
}

ALL_BUILDING_DEFS = {"HQ": HQ_DEF, **RESOURCE_BUILDING_DEFS, **NON_RESOURCE_BUILDING_DEFS}

def _make_registry() -> DataRegistry:
    """Create a DataRegistry with all test building definitions."""
    registry = DataRegistry()
    registry.buildings = dict(ALL_BUILDING_DEFS)
    return registry

def _make_system(registry=None, event_bus=None, current_tick=0):
    """Create a BuildingSystem with a fake building factory."""
    if registry is None:
        registry = _make_registry()
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
        tile._building = b
        return b

    system = BuildingSystem(
        registry=registry,
        event_bus=event_bus,
        create_building_func=fake_create,
        build_range=1000,  # Large range so range check doesn't interfere
        current_tick_func=lambda: current_tick,
    )
    return system, created_buildings

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def resource_building_strategy(draw):
    """Generate a random resource building abbreviation."""
    return draw(st.sampled_from(list(RESOURCE_BUILDING_DEFS.keys())))

@st.composite
def non_hq_building_strategy(draw):
    """Generate a random non-HQ building abbreviation (requires HQ)."""
    all_non_hq = list(RESOURCE_BUILDING_DEFS.keys()) + list(NON_RESOURCE_BUILDING_DEFS.keys())
    return draw(st.sampled_from(all_non_hq))

@st.composite
def building_level_strategy(draw):
    """Generate a valid building level (1-5)."""
    return draw(st.integers(min_value=1, max_value=MAX_BUILDING_LEVEL))

@st.composite
def sufficient_resources_strategy(draw, cost_dict):
    """Generate resources that are sufficient for a given cost, with some surplus."""
    resources = {}
    for resource, amount in cost_dict.items():
        surplus = draw(st.integers(min_value=0, max_value=500))
        resources[resource] = amount + surplus
    return resources

@st.composite
def terrain_strategy(draw):
    """Generate a random terrain type."""
    return draw(st.sampled_from(TERRAIN_TYPES))

# -------------------------------------------------------------- #
#  Property 6: HQ prerequisite enforcement
#  **Validates: Requirements 3.1, 3.2**
# -------------------------------------------------------------- #

class TestProperty6HQPrerequisite(unittest.TestCase):
    """Property 6: HQ prerequisite enforcement.

    For any building type where requires_hq is true, attempting to construct
    that building when the player has no Headquarters SHALL be rejected.
    Conversely, constructing a Headquarters SHALL always be allowed
    regardless of other buildings (given sufficient resources and valid tile).

    **Validates: Requirements 3.1, 3.2**
    """

    @given(building_abbr=non_hq_building_strategy())
    @settings(max_examples=100)
    def test_non_hq_without_hq_always_rejected(self, building_abbr):
        """Non-HQ buildings without HQ are always rejected."""
        building_def = ALL_BUILDING_DEFS[building_abbr]
        # Give player plenty of resources
        resources = {r: 10000 for r in ALL_RESOURCE_TYPES}
        player = FakePlayer(resources=resources, buildings=[])

        terrain = building_def.required_terrain or "Plains"
        tile = FakeTile(terrain_type=terrain)

        system, created = _make_system()
        ok, msg = system.construct(player, tile, building_abbr)

        self.assertFalse(ok, f"Building {building_abbr} should be rejected without HQ")
        self.assertIn("Headquarters", msg)
        self.assertEqual(len(created), 0)

    @given(surplus=st.integers(min_value=0, max_value=500))
    @settings(max_examples=100)
    def test_hq_construction_always_allowed(self, surplus):
        """HQ construction is always allowed with sufficient resources."""
        resources = {
            r: HQ_DEF.cost.get(r, 0) + surplus
            for r in ALL_RESOURCE_TYPES
        }
        player = FakePlayer(resources=resources, buildings=[])
        tile = FakeTile(terrain_type="Plains")

        system, created = _make_system()
        ok, msg = system.construct(player, tile, "HQ")

        self.assertTrue(ok, f"HQ should be constructible: {msg}")
        self.assertEqual(len(created), 1)

    @given(building_abbr=non_hq_building_strategy())
    @settings(max_examples=100)
    def test_non_hq_with_hq_allowed(self, building_abbr):
        """Non-HQ buildings with HQ present are allowed (given valid conditions)."""
        building_def = ALL_BUILDING_DEFS[building_abbr]
        resources = {r: 10000 for r in ALL_RESOURCE_TYPES}
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(resources=resources, buildings=[hq])

        terrain = building_def.required_terrain or "Plains"
        tile = FakeTile(terrain_type=terrain)

        system, created = _make_system()
        ok, msg = system.construct(player, tile, building_abbr)

        self.assertTrue(ok, f"Building {building_abbr} should succeed with HQ: {msg}")
        self.assertEqual(len(created), 1)

# -------------------------------------------------------------- #
#  Property 7: Building construction resource deduction
#  **Validates: Requirements 3.3, 3.6**
# -------------------------------------------------------------- #

class TestProperty7ResourceDeduction(unittest.TestCase):
    """Property 7: Building construction resource deduction.

    For any valid building construction, the player's resource counters
    SHALL decrease by exactly the building's defined cost.

    **Validates: Requirements 3.3, 3.6**
    """

    @given(
        building_abbr=st.sampled_from(list(ALL_BUILDING_DEFS.keys())),
        surplus=st.dictionaries(
            keys=st.sampled_from(ALL_RESOURCE_TYPES),
            values=st.integers(min_value=0, max_value=500),
            min_size=0, max_size=8,
        ),
    )
    @settings(max_examples=100)
    def test_resources_decrease_by_exact_cost(self, building_abbr, surplus):
        """After construction, resources decrease by exactly the defined cost."""
        building_def = ALL_BUILDING_DEFS[building_abbr]

        # Build resources: enough for the cost plus surplus
        resources = {r: 10000 for r in ALL_RESOURCE_TYPES}
        for r, extra in surplus.items():
            resources[r] = resources.get(r, 0) + extra

        # Set up player with HQ if needed
        buildings = []
        if building_def.requires_hq:
            buildings.append(FakeBuilding(building_type="HQ"))

        player = FakePlayer(resources=resources, buildings=buildings)

        # Record pre-construction resources
        pre_resources = {r: player.get_resource(r) for r in ALL_RESOURCE_TYPES}

        terrain = building_def.required_terrain or "Plains"
        tile = FakeTile(terrain_type=terrain)

        system, created = _make_system()
        ok, msg = system.construct(player, tile, building_abbr)

        self.assertTrue(ok, f"Construction should succeed: {msg}")

        # Verify exact deduction
        for r in ALL_RESOURCE_TYPES:
            expected_cost = building_def.cost.get(r, 0)
            expected_remaining = pre_resources[r] - expected_cost
            actual = player.get_resource(r)
            self.assertEqual(
                actual, expected_remaining,
                f"Resource {r}: expected {expected_remaining}, got {actual} "
                f"(pre={pre_resources[r]}, cost={expected_cost})"
            )

# -------------------------------------------------------------- #
#  Property 8: Terrain-restricted building placement
#  **Validates: Requirements 4.2, 4.4**
# -------------------------------------------------------------- #

class TestProperty8TerrainRestriction(unittest.TestCase):
    """Property 8: Terrain-restricted building placement.

    For any resource building type with a non-null required_terrain,
    construction SHALL succeed only on tiles whose terrain type matches
    required_terrain. Construction on any other terrain type SHALL be rejected.

    **Validates: Requirements 4.2, 4.4**
    """

    @given(
        building_abbr=resource_building_strategy(),
        wrong_terrain=terrain_strategy(),
    )
    @settings(max_examples=100)
    def test_wrong_terrain_rejected(self, building_abbr, wrong_terrain):
        """Resource buildings on wrong terrain are rejected."""
        building_def = RESOURCE_BUILDING_DEFS[building_abbr]
        assume(wrong_terrain != building_def.required_terrain)

        resources = {r: 10000 for r in ALL_RESOURCE_TYPES}
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(resources=resources, buildings=[hq])
        tile = FakeTile(terrain_type=wrong_terrain)

        system, created = _make_system()
        ok, msg = system.construct(player, tile, building_abbr)

        self.assertFalse(ok, f"{building_abbr} should fail on {wrong_terrain}")
        self.assertIn(building_def.required_terrain, msg)
        self.assertEqual(len(created), 0)

    @given(building_abbr=resource_building_strategy())
    @settings(max_examples=100)
    def test_correct_terrain_succeeds(self, building_abbr):
        """Resource buildings on correct terrain succeed."""
        building_def = RESOURCE_BUILDING_DEFS[building_abbr]

        resources = {r: 10000 for r in ALL_RESOURCE_TYPES}
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(resources=resources, buildings=[hq])
        tile = FakeTile(terrain_type=building_def.required_terrain)

        system, created = _make_system()
        ok, msg = system.construct(player, tile, building_abbr)

        self.assertTrue(ok, f"{building_abbr} should succeed on {building_def.required_terrain}: {msg}")
        self.assertEqual(len(created), 1)

# -------------------------------------------------------------- #
#  Property 9: Resource building level invariant
#  **Validates: Requirements 5.1, 5.6, 5.7**
# -------------------------------------------------------------- #

class TestProperty9LevelInvariant(unittest.TestCase):
    """Property 9: Resource building level invariant.

    For any resource building, the building level SHALL always be in the
    range [1, 5]. A newly constructed resource building SHALL have level 1.
    Upgrading a building at level L (where L < 5) SHALL result in level L+1.
    Upgrading at level 5 SHALL be rejected.

    **Validates: Requirements 5.1, 5.6, 5.7**
    """

    @given(building_abbr=resource_building_strategy())
    @settings(max_examples=100)
    def test_new_building_starts_at_level_1(self, building_abbr):
        """Newly constructed resource buildings start at level 1."""
        building_def = RESOURCE_BUILDING_DEFS[building_abbr]
        resources = {r: 10000 for r in ALL_RESOURCE_TYPES}
        hq = FakeBuilding(building_type="HQ")
        player = FakePlayer(resources=resources, buildings=[hq])
        tile = FakeTile(terrain_type=building_def.required_terrain)

        system, created = _make_system()
        ok, _ = system.construct(player, tile, building_abbr)
        self.assertTrue(ok)
        self.assertEqual(created[0].building_level, 1)

    @given(
        building_abbr=resource_building_strategy(),
        level=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=100)
    def test_upgrade_increments_level_by_one(self, building_abbr, level):
        """Upgrading at level L results in level L+1."""
        resources = {r: 100000 for r in ALL_RESOURCE_TYPES}
        player = FakePlayer(resources=resources)
        building = FakeBuilding(
            building_type=building_abbr, owner=player, level=level,
        )

        system, _ = _make_system()
        ok, msg = system.upgrade(player, building)

        self.assertTrue(ok, f"Upgrade from level {level} should succeed: {msg}")
        self.assertEqual(building.building_level, level + 1)
        self.assertGreaterEqual(building.building_level, 1)
        self.assertLessEqual(building.building_level, MAX_BUILDING_LEVEL)

    @given(building_abbr=resource_building_strategy())
    @settings(max_examples=100)
    def test_upgrade_at_max_level_rejected(self, building_abbr):
        """Upgrading at level 5 is rejected."""
        resources = {r: 100000 for r in ALL_RESOURCE_TYPES}
        player = FakePlayer(resources=resources)
        building = FakeBuilding(
            building_type=building_abbr, owner=player, level=MAX_BUILDING_LEVEL,
        )

        system, _ = _make_system()
        ok, msg = system.upgrade(player, building)

        self.assertFalse(ok)
        self.assertIn("maximum level", msg)
        self.assertEqual(building.building_level, MAX_BUILDING_LEVEL)

    @given(
        building_abbr=resource_building_strategy(),
        num_upgrades=st.integers(min_value=0, max_value=6),
    )
    @settings(max_examples=100)
    def test_level_always_in_range_after_upgrades(self, building_abbr, num_upgrades):
        """After any number of upgrade attempts, level stays in [1, 5]."""
        resources = {r: 1000000 for r in ALL_RESOURCE_TYPES}
        player = FakePlayer(resources=resources)
        building = FakeBuilding(
            building_type=building_abbr, owner=player, level=1,
        )

        system, _ = _make_system()
        for _ in range(num_upgrades):
            system.upgrade(player, building)

        self.assertGreaterEqual(building.building_level, 1)
        self.assertLessEqual(building.building_level, MAX_BUILDING_LEVEL)

# -------------------------------------------------------------- #
#  Property 11: Upgrade cost formula
#  **Validates: Requirements 5.4**
# -------------------------------------------------------------- #

class TestProperty11UpgradeCostFormula(unittest.TestCase):
    """Property 11: Upgrade cost formula.

    For any resource building at level L being upgraded to level L+1,
    the resource cost SHALL equal the building's base construction cost
    multiplied by 2^L (exponential scaling).

    **Validates: Requirements 5.4**
    """

    @given(
        building_abbr=resource_building_strategy(),
        level=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=100)
    def test_upgrade_cost_equals_base_times_power_of_two(self, building_abbr, level):
        """Upgrade cost = base_cost × 2^(target_level - 1)."""
        building_def = RESOURCE_BUILDING_DEFS[building_abbr]
        target_level = level + 1
        multiplier = 2 ** (target_level - 1)

        expected_cost = {
            r: amt * multiplier
            for r, amt in building_def.cost.items()
        }

        resources = {r: 1000000 for r in ALL_RESOURCE_TYPES}
        player = FakePlayer(resources=resources)
        building = FakeBuilding(
            building_type=building_abbr, owner=player, level=level,
        )

        pre_resources = {r: player.get_resource(r) for r in ALL_RESOURCE_TYPES}

        system, _ = _make_system()
        ok, msg = system.upgrade(player, building)

        self.assertTrue(ok, f"Upgrade should succeed: {msg}")

        for r in ALL_RESOURCE_TYPES:
            cost_for_r = expected_cost.get(r, 0)
            expected_remaining = pre_resources[r] - cost_for_r
            actual = player.get_resource(r)
            self.assertEqual(
                actual, expected_remaining,
                f"Resource {r}: expected {expected_remaining}, got {actual} "
                f"(pre={pre_resources[r]}, cost={cost_for_r}, "
                f"base={building_def.cost.get(r, 0)}, target_level={target_level})"
            )

    @given(
        building_abbr=resource_building_strategy(),
        level=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=100)
    def test_insufficient_for_formula_cost_rejected(self, building_abbr, level):
        """If player has less than base_cost × 2^(target_level-1), upgrade is rejected."""
        building_def = RESOURCE_BUILDING_DEFS[building_abbr]
        target_level = level + 1
        multiplier = 2 ** (target_level - 1)

        resources = {r: 1000000 for r in ALL_RESOURCE_TYPES}
        first_resource = next(iter(building_def.cost))
        needed = building_def.cost[first_resource] * multiplier
        resources[first_resource] = needed - 1

        player = FakePlayer(resources=resources)
        building = FakeBuilding(
            building_type=building_abbr, owner=player, level=level,
        )

        system, _ = _make_system()
        ok, msg = system.upgrade(player, building)

        self.assertFalse(ok)
        self.assertIn("Insufficient Resources", msg)

if __name__ == "__main__":
    unittest.main()
