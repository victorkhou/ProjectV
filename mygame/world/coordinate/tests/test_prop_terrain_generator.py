"""
Property-based tests for TerrainGenerator.

Property 1: Terrain generation determinism — for any coordinate (x, y)
and any terrain seed, calling get_terrain(x, y) multiple times SHALL
always return the same terrain type string.

Property 2: Terrain output is always in the configured terrain set —
for any coordinate (x, y) within a Coordinate_Space, the terrain type
returned by the Terrain_Generator SHALL be a member of the planet's
configured terrain type set.

Property 3: Terrain-to-resource mapping consistency — for any coordinate
(x, y), the resource type returned by get_terrain_and_resource(x, y)
SHALL match the planet's terrain-to-resource mapping for the terrain
type at that coordinate.

**Validates: Requirements 3.1, 3.2, 3.4, 3.6, 9.3**
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

from mygame.world.definitions import CoordinateSpaceDef  # noqa: E402
from mygame.world.coordinate.terrain_generator import TerrainGenerator  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

EARTH_WEIGHTS = {
    "Plains": 0.35,
    "Forest": 0.25,
    "Dirt": 0.15,
    "Rock": 0.15,
    "Mountain": 0.10,
}

def _make_generator(seed: int, weights: dict[str, float] | None = None, cell_size: int = 8) -> TerrainGenerator:
    """Create a TerrainGenerator with the given seed and weights."""
    space = CoordinateSpaceDef(
        planet_key="test",
        planet_type="earth",
        width=1000,
        height=1000,
        terrain_seed=seed,
        terrain_noise_cell_size=cell_size,
        terrain_weights=weights if weights is not None else EARTH_WEIGHTS,
    )
    return TerrainGenerator(space)

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

coordinate_strategy = st.integers(min_value=-10_000, max_value=10_000)
seed_strategy = st.integers(min_value=0, max_value=2**31 - 1)
num_calls_strategy = st.integers(min_value=2, max_value=10)

# -------------------------------------------------------------- #
#  Property 1: Terrain generation determinism
#  **Validates: Requirements 3.1, 3.2**
# -------------------------------------------------------------- #

class TestProperty1TerrainDeterminism(unittest.TestCase):
    """Property 1: Terrain generation determinism.

    For any coordinate (x, y) and any terrain seed, calling
    get_terrain(x, y) multiple times SHALL always return the same
    terrain type string.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
        num_calls=num_calls_strategy,
    )
    @settings(max_examples=200)
    def test_get_terrain_returns_same_result_across_calls(self, x, y, seed, num_calls):
        """Calling get_terrain(x, y) N times with the same seed always returns the same string."""
        gen = _make_generator(seed)
        first_result = gen.get_terrain(x, y)
        for _ in range(num_calls - 1):
            self.assertEqual(
                gen.get_terrain(x, y),
                first_result,
                f"get_terrain({x}, {y}) with seed={seed} returned different results",
            )

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
    )
    @settings(max_examples=200)
    def test_separate_generators_same_seed_same_result(self, x, y, seed):
        """Two TerrainGenerators with the same seed produce identical terrain for the same coordinate."""
        gen1 = _make_generator(seed)
        gen2 = _make_generator(seed)
        self.assertEqual(
            gen1.get_terrain(x, y),
            gen2.get_terrain(x, y),
            f"Two generators with seed={seed} disagree at ({x}, {y})",
        )

# -------------------------------------------------------------- #
#  Additional strategies for Properties 2 & 3
# -------------------------------------------------------------- #

# Strategy: generate a non-empty terrain weights dict with 2-6 terrain types
_TERRAIN_POOL = [
    "Plains", "Forest", "Dirt", "Rock", "Mountain",
    "Power_Grid", "Scrapyard", "Circuit_Field", "Ruins",
]

terrain_weights_strategy = (
    st.lists(
        st.sampled_from(_TERRAIN_POOL),
        min_size=2,
        max_size=6,
        unique=True,
    )
    .flatmap(
        lambda names: st.tuples(
            st.just(names),
            st.lists(
                st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
                min_size=len(names),
                max_size=len(names),
            ),
        )
    )
    .map(lambda pair: dict(zip(pair[0], pair[1])))
)

# -------------------------------------------------------------- #
#  Property 2: Terrain output is always in the configured terrain set
#  **Validates: Requirements 3.4**
# -------------------------------------------------------------- #

class TestProperty2TerrainOutputValidity(unittest.TestCase):
    """Property 2: Terrain output is always in the configured terrain set.

    For any coordinate (x, y) within a Coordinate_Space, the terrain
    type returned by the Terrain_Generator SHALL be a member of the
    planet's configured terrain type set.

    **Validates: Requirements 3.4**
    """

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
    )
    @settings(max_examples=200)
    def test_terrain_in_earth_weights(self, x, y, seed):
        """get_terrain returns a key from the configured EARTH_WEIGHTS."""
        gen = _make_generator(seed)
        terrain = gen.get_terrain(x, y)
        self.assertIn(
            terrain,
            EARTH_WEIGHTS,
            f"get_terrain({x}, {y}) returned '{terrain}' which is not in EARTH_WEIGHTS",
        )

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
        weights=terrain_weights_strategy,
    )
    @settings(max_examples=200)
    def test_terrain_in_arbitrary_weights(self, x, y, seed, weights):
        """get_terrain returns a key from any arbitrary non-empty terrain weights dict."""
        gen = _make_generator(seed, weights=weights)
        terrain = gen.get_terrain(x, y)
        self.assertIn(
            terrain,
            weights,
            f"get_terrain({x}, {y}) returned '{terrain}' which is not in {set(weights)}",
        )

# -------------------------------------------------------------- #
#  Property 3: Terrain-to-resource mapping consistency
#  **Validates: Requirements 3.6, 9.3**
# -------------------------------------------------------------- #

# A known resource map matching EARTH_WEIGHTS terrain types.
EARTH_RESOURCE_MAP: dict[str, str | None] = {
    "Plains": None,
    "Forest": "wood",
    "Dirt": None,
    "Rock": "stone",
    "Mountain": "ore",
}

class TestProperty3TerrainResourceMapping(unittest.TestCase):
    """Property 3: Terrain-to-resource mapping consistency.

    For any coordinate (x, y), the resource type returned by
    get_terrain_and_resource(x, y) SHALL match the planet's
    terrain-to-resource mapping for the terrain type at that
    coordinate. If the terrain has no associated resource, the
    resource type SHALL be None.

    **Validates: Requirements 3.6, 9.3**
    """

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
    )
    @settings(max_examples=200)
    def test_resource_matches_terrain_mapping(self, x, y, seed):
        """get_terrain_and_resource returns a resource consistent with the resource map."""
        gen = _make_generator(seed)
        gen._set_resource_map(EARTH_RESOURCE_MAP)

        terrain, resource = gen.get_terrain_and_resource(x, y)
        expected_resource = EARTH_RESOURCE_MAP.get(terrain)
        self.assertEqual(
            resource,
            expected_resource,
            f"At ({x}, {y}): terrain='{terrain}', expected resource={expected_resource!r}, got {resource!r}",
        )

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
    )
    @settings(max_examples=200)
    def test_no_resource_map_returns_none(self, x, y, seed):
        """When no resource map is set, get_terrain_and_resource returns None for resource."""
        gen = _make_generator(seed)
        gen._set_resource_map({})

        terrain, resource = gen.get_terrain_and_resource(x, y)
        self.assertIsNone(
            resource,
            f"At ({x}, {y}): terrain='{terrain}', expected resource=None with empty map, got {resource!r}",
        )

    @given(
        x=coordinate_strategy,
        y=coordinate_strategy,
        seed=seed_strategy,
    )
    @settings(max_examples=200)
    def test_partial_resource_map(self, x, y, seed):
        """When only some terrains have resources, unmapped terrains return None."""
        partial_map: dict[str, str | None] = {"Forest": "wood"}
        gen = _make_generator(seed)
        gen._set_resource_map(partial_map)

        terrain, resource = gen.get_terrain_and_resource(x, y)
        expected = partial_map.get(terrain)
        self.assertEqual(
            resource,
            expected,
            f"At ({x}, {y}): terrain='{terrain}', expected {expected!r}, got {resource!r}",
        )

if __name__ == "__main__":
    unittest.main()
