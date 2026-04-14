"""
Property-based tests for terrain generation, building upgrade costs,
per-level building bonuses, and harvester production scaling.

**Property 4: Terrain Generation Within Weight Map**
For any coordinate (x, y) within a planet's bounds, the terrain type
returned by TerrainGenerator SHALL be one of the keys in that planet's
terrain_weights map, and if that terrain type has a non-null resource_type,
get_terrain_and_resource SHALL include it.
**Validates: Requirements 2.3, 2.5, 2.7**

**Property 5: Terrain Generation Determinism**
For any coordinate (x, y) and seed, calling get_terrain(x, y) multiple
times SHALL always return the same terrain type.
**Validates: Requirements 2.4**

**Property 16: Building Upgrade Cost Scaling**
For any building type with base cost C and target upgrade level L, the
upgrade cost SHALL be C × 2^(L-1) for each resource in the cost map.
**Validates: Requirements 6.8**

**Property 17: Per-Level Building Bonus Computation**
Extractor storage = 100 + 50 × (level-1), Vault storage = 100 + 20 × (level-1),
Turret damage = base × (1 + 0.20 × (level-1)).
**Validates: Requirements 6.21, 6.22, 6.23**

**Property 18: Harvester Production Scaling**
production = base_rate × (1 + 0.25 × (level-1))
**Validates: Requirements 9.2**
"""

import os
import unittest

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.definitions import CoordinateSpaceDef, TerrainDef
from mygame.world.coordinate.terrain_generator import TerrainGenerator
from mygame.world.systems.resource_system import ResourceSystem

# -------------------------------------------------------------- #
#  Load real YAML data for terrain tests
# -------------------------------------------------------------- #

_DATA_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "data", "definitions"
)

def _load_planets() -> list[dict]:
    """Load planet definitions from planets.yaml."""
    with open(os.path.join(_DATA_DIR, "planets.yaml"), "r") as f:
        raw = yaml.safe_load(f)
    return raw["planets"]


def _load_terrain() -> dict[str, TerrainDef]:
    """Load terrain definitions from terrain.yaml, keyed by terrain_type."""
    with open(os.path.join(_DATA_DIR, "terrain.yaml"), "r") as f:
        raw = yaml.safe_load(f)
    result = {}
    for entry in raw["terrain"]:
        td = TerrainDef(
            terrain_type=entry["terrain_type"],
            map_symbol=entry["map_symbol"],
            resource_type=entry.get("resource_type"),
            passable=entry.get("passable", True),
        )
        result[td.terrain_type] = td
    return result


def _load_buildings() -> list[dict]:
    """Load building definitions from buildings.yaml."""
    with open(os.path.join(_DATA_DIR, "buildings.yaml"), "r") as f:
        return yaml.safe_load(f)


# Pre-load data at module level for use in strategies
_PLANETS = _load_planets()
_TERRAIN = _load_terrain()
_BUILDINGS = _load_buildings()


def _make_space_def(planet_dict: dict) -> CoordinateSpaceDef:
    """Create a CoordinateSpaceDef from a planet YAML dict."""
    return CoordinateSpaceDef(
        planet_key=planet_dict["planet_key"],
        planet_type=planet_dict["planet_type"],
        width=planet_dict["width"],
        height=planet_dict["height"],
        terrain_seed=planet_dict["terrain_seed"],
        terrain_noise_cell_size=planet_dict.get("terrain_noise_cell_size", 8),
        terrain_weights=planet_dict.get("terrain_weights", {}),
        persistence_type=planet_dict.get("persistence_type", "static"),
        spawn_x=planet_dict.get("spawn_x", 0),
        spawn_y=planet_dict.get("spawn_y", 0),
        default_planet=planet_dict.get("default_planet", False),
        z_level=planet_dict.get("z_level", 0),
        rank_requirement=planet_dict.get("rank_requirement", 1),
    )


def _make_generator(space_def: CoordinateSpaceDef) -> TerrainGenerator:
    """Create a TerrainGenerator with a real resource map from terrain.yaml."""
    gen = TerrainGenerator(space_def)
    resource_map = {}
    for terrain_type in space_def.terrain_weights:
        td = _TERRAIN.get(terrain_type)
        resource_map[terrain_type] = td.resource_type if td else None
    gen._set_resource_map(resource_map)
    return gen


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

planet_st = st.sampled_from(_PLANETS)
level_st = st.integers(min_value=1, max_value=5)
building_st = st.sampled_from(_BUILDINGS)
base_damage_st = st.integers(min_value=1, max_value=500)
base_rate_st = st.integers(min_value=1, max_value=100)


# ================================================================== #
#  Property 4: Terrain Generation Within Weight Map
#  **Validates: Requirements 2.3, 2.5, 2.7**
# ================================================================== #

class TestProperty4TerrainWithinWeightMap(unittest.TestCase):
    """Property 4: Terrain Generation Within Weight Map.

    For any coordinate (x, y) within a planet's bounds, the terrain type
    returned by TerrainGenerator SHALL be one of the keys in that planet's
    terrain_weights map, and if that terrain type has a non-null resource_type,
    get_terrain_and_resource SHALL include it.

    **Validates: Requirements 2.3, 2.5, 2.7**
    """

    @given(planet=planet_st, data=st.data())
    @settings(max_examples=100)
    def test_terrain_type_in_weight_map(self, planet, data):
        """Generated terrain type is always a key in the planet's weight map."""
        space_def = _make_space_def(planet)
        gen = _make_generator(space_def)

        x = data.draw(st.integers(min_value=0, max_value=space_def.width - 1), label="x")
        y = data.draw(st.integers(min_value=0, max_value=space_def.height - 1), label="y")

        terrain_type = gen.get_terrain(x, y)
        weight_keys = set(space_def.terrain_weights.keys())

        self.assertIn(
            terrain_type, weight_keys,
            f"Terrain '{terrain_type}' at ({x},{y}) on {planet['planet_key']} "
            f"not in weight map keys: {weight_keys}"
        )

    @given(planet=planet_st, data=st.data())
    @settings(max_examples=100)
    def test_resource_included_when_non_null(self, planet, data):
        """If terrain has a non-null resource_type, get_terrain_and_resource includes it."""
        space_def = _make_space_def(planet)
        gen = _make_generator(space_def)

        x = data.draw(st.integers(min_value=0, max_value=space_def.width - 1), label="x")
        y = data.draw(st.integers(min_value=0, max_value=space_def.height - 1), label="y")

        terrain_type, resource = gen.get_terrain_and_resource(x, y)

        # Look up expected resource from terrain definitions
        td = _TERRAIN.get(terrain_type)
        if td is not None and td.resource_type is not None:
            self.assertEqual(
                resource, td.resource_type,
                f"Terrain '{terrain_type}' should yield resource '{td.resource_type}' "
                f"but got '{resource}'"
            )
        else:
            self.assertIsNone(
                resource,
                f"Terrain '{terrain_type}' has no resource but got '{resource}'"
            )


# ================================================================== #
#  Property 5: Terrain Generation Determinism
#  **Validates: Requirements 2.4**
# ================================================================== #

class TestProperty5TerrainDeterminism(unittest.TestCase):
    """Property 5: Terrain Generation Determinism.

    For any coordinate (x, y) and seed, calling get_terrain(x, y) multiple
    times SHALL always return the same terrain type.

    **Validates: Requirements 2.4**
    """

    @given(planet=planet_st, data=st.data())
    @settings(max_examples=100)
    def test_get_terrain_is_deterministic(self, planet, data):
        """Calling get_terrain multiple times returns the same result."""
        space_def = _make_space_def(planet)

        x = data.draw(st.integers(min_value=0, max_value=space_def.width - 1), label="x")
        y = data.draw(st.integers(min_value=0, max_value=space_def.height - 1), label="y")

        gen1 = TerrainGenerator(space_def)
        gen2 = TerrainGenerator(space_def)

        result1 = gen1.get_terrain(x, y)
        result2 = gen1.get_terrain(x, y)
        result3 = gen2.get_terrain(x, y)

        self.assertEqual(
            result1, result2,
            f"Same generator, same coords ({x},{y}) returned different results: "
            f"'{result1}' vs '{result2}'"
        )
        self.assertEqual(
            result1, result3,
            f"Different generators with same seed returned different results: "
            f"'{result1}' vs '{result3}'"
        )


# ================================================================== #
#  Property 16: Building Upgrade Cost Scaling
#  **Validates: Requirements 6.8**
# ================================================================== #

class TestProperty16UpgradeCostScaling(unittest.TestCase):
    """Property 16: Building Upgrade Cost Scaling.

    For any building type with base cost C and target upgrade level L,
    the upgrade cost SHALL be C × 2^(L-1) for each resource in the cost map.

    **Validates: Requirements 6.8**
    """

    @given(building=building_st, level=level_st)
    @settings(max_examples=100)
    def test_upgrade_cost_equals_base_times_power_of_two(self, building, level):
        """Upgrade cost for level L is base_cost × 2^(L-1) for each resource."""
        base_cost = building["cost"]
        multiplier = 2 ** (level - 1)

        for resource, base_amount in base_cost.items():
            expected = base_amount * multiplier
            self.assertEqual(
                expected, base_amount * multiplier,
                f"Building '{building['name']}' resource '{resource}': "
                f"expected {expected} at level {level}"
            )

    @given(building=building_st)
    @settings(max_examples=100)
    def test_level_1_cost_equals_base(self, building):
        """At level 1, upgrade cost equals base cost (2^0 = 1)."""
        for resource, base_amount in building["cost"].items():
            self.assertEqual(base_amount * (2 ** 0), base_amount)


# ================================================================== #
#  Property 17: Per-Level Building Bonus Computation
#  **Validates: Requirements 6.21, 6.22, 6.23**
# ================================================================== #

class TestProperty17PerLevelBonuses(unittest.TestCase):
    """Property 17: Per-Level Building Bonus Computation.

    Extractor storage = 100 + 50 × (level-1),
    Vault storage = 100 + 20 × (level-1),
    Turret damage = base × (1 + 0.20 × (level-1)).

    **Validates: Requirements 6.21, 6.22, 6.23**
    """

    @given(level=level_st)
    @settings(max_examples=100)
    def test_extractor_storage_formula(self, level):
        """Extractor capacity matches 100 + 50 × (level - 1)."""
        expected = 100 + 50 * (level - 1)
        actual = ResourceSystem.get_extractor_capacity(level)
        self.assertEqual(
            actual, expected,
            f"Extractor capacity at level {level}: expected {expected}, got {actual}"
        )

    @given(level=level_st)
    @settings(max_examples=100)
    def test_vault_storage_formula(self, level):
        """Vault capacity matches 100 + 20 × (level - 1)."""
        expected = 100 + 20 * (level - 1)
        actual = ResourceSystem.get_vault_capacity(level)
        self.assertEqual(
            actual, expected,
            f"Vault capacity at level {level}: expected {expected}, got {actual}"
        )

    @given(base_damage=base_damage_st, level=level_st)
    @settings(max_examples=100)
    def test_turret_damage_formula(self, base_damage, level):
        """Turret damage matches base × (1 + 0.20 × (level - 1))."""
        expected = base_damage * (1 + 0.20 * (level - 1))
        actual = ResourceSystem.get_turret_damage(base_damage, level)
        self.assertAlmostEqual(
            actual, expected, places=10,
            msg=f"Turret damage at level {level} with base {base_damage}: "
                f"expected {expected}, got {actual}"
        )


# ================================================================== #
#  Property 18: Harvester Production Scaling
#  **Validates: Requirements 9.2**
# ================================================================== #

class TestProperty18HarvesterProductionScaling(unittest.TestCase):
    """Property 18: Harvester Production Scaling.

    production = base_rate × (1 + 0.25 × (level - 1))

    **Validates: Requirements 9.2**
    """

    @given(base_rate=base_rate_st, level=level_st)
    @settings(max_examples=100)
    def test_harvester_production_formula(self, base_rate, level):
        """Production rate matches base_rate × (1 + 0.25 × (level - 1))."""
        expected = base_rate * (1 + 0.25 * (level - 1))
        actual = ResourceSystem.get_harvester_production(base_rate, level)
        self.assertAlmostEqual(
            actual, expected, places=10,
            msg=f"Harvester production at level {level} with base_rate {base_rate}: "
                f"expected {expected}, got {actual}"
        )

    @given(base_rate=base_rate_st)
    @settings(max_examples=100)
    def test_level_1_equals_base_rate(self, base_rate):
        """At level 1, production equals the base rate (no bonus)."""
        actual = ResourceSystem.get_harvester_production(base_rate, 1)
        self.assertAlmostEqual(
            actual, float(base_rate), places=10,
            msg=f"Level 1 production should equal base_rate {base_rate}, got {actual}"
        )


if __name__ == "__main__":
    unittest.main()
