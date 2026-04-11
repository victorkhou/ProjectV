"""Unit tests for TerrainGenerator."""

import pytest

from mygame.world.definitions import CoordinateSpaceDef
from mygame.world.coordinate.terrain_generator import TerrainGenerator


# ------------------------------------------------------------------ #
#  Fixture helpers
# ------------------------------------------------------------------ #

EARTH_WEIGHTS = {
    "Plains": 0.35,
    "Forest": 0.25,
    "Dirt": 0.15,
    "Rock": 0.15,
    "Mountain": 0.10,
}

EARTH_RESOURCE_MAP = {
    "Plains": "Straw",
    "Forest": "Wood",
    "Dirt": "Clay",
    "Rock": "Stone",
    "Mountain": "Iron",
}

INDUSTRIAL_WEIGHTS = {
    "Power_Grid": 0.30,
    "Scrapyard": 0.30,
    "Circuit_Field": 0.25,
    "Ruins": 0.15,
}

INDUSTRIAL_RESOURCE_MAP = {
    "Power_Grid": "Energy",
    "Scrapyard": "Metals",
    "Circuit_Field": "Circuits",
    "Ruins": None,
}


def _make_space(
    weights=None, seed=42, cell_size=8, planet_key="test", planet_type="earth"
):
    return CoordinateSpaceDef(
        planet_key=planet_key,
        planet_type=planet_type,
        width=100,
        height=100,
        terrain_seed=seed,
        terrain_noise_cell_size=cell_size,
        terrain_weights=weights if weights is not None else EARTH_WEIGHTS,
    )


@pytest.fixture
def earth_gen():
    gen = TerrainGenerator(_make_space())
    gen._set_resource_map(EARTH_RESOURCE_MAP)
    return gen


@pytest.fixture
def industrial_gen():
    gen = TerrainGenerator(_make_space(weights=INDUSTRIAL_WEIGHTS, seed=7))
    gen._set_resource_map(INDUSTRIAL_RESOURCE_MAP)
    return gen


# ------------------------------------------------------------------ #
#  Tests: determinism
# ------------------------------------------------------------------ #

class TestDeterminism:
    def test_same_coords_same_result(self, earth_gen):
        assert earth_gen.get_terrain(10, 20) == earth_gen.get_terrain(10, 20)

    def test_deterministic_across_many_calls(self, earth_gen):
        results = {earth_gen.get_terrain(42, 42) for _ in range(50)}
        assert len(results) == 1

    def test_different_coords_can_differ(self, earth_gen):
        """Not all coordinates produce the same terrain."""
        terrains = {earth_gen.get_terrain(x, y) for x in range(20) for y in range(20)}
        assert len(terrains) > 1


# ------------------------------------------------------------------ #
#  Tests: terrain output validity
# ------------------------------------------------------------------ #

class TestTerrainValidity:
    def test_earth_terrains_in_valid_set(self, earth_gen):
        valid = set(EARTH_WEIGHTS.keys())
        for x in range(25):
            for y in range(25):
                assert earth_gen.get_terrain(x, y) in valid

    def test_industrial_terrains_in_valid_set(self, industrial_gen):
        valid = set(INDUSTRIAL_WEIGHTS.keys())
        for x in range(25):
            for y in range(25):
                assert industrial_gen.get_terrain(x, y) in valid

    def test_empty_weights_returns_unknown(self):
        gen = TerrainGenerator(_make_space(weights={}))
        assert gen.get_terrain(0, 0) == "unknown"


# ------------------------------------------------------------------ #
#  Tests: noise function
# ------------------------------------------------------------------ #

class TestNoise:
    def test_noise_in_range(self, earth_gen):
        for x in range(50):
            for y in range(50):
                n = earth_gen._noise(x, y)
                assert 0.0 <= n < 1.0, f"Noise {n} out of [0,1) at ({x},{y})"

    def test_noise_deterministic(self, earth_gen):
        assert earth_gen._noise(5, 10) == earth_gen._noise(5, 10)

    def test_noise_varies_across_grid(self, earth_gen):
        values = {earth_gen._noise(x, y) for x in range(20) for y in range(20)}
        assert len(values) > 1


# ------------------------------------------------------------------ #
#  Tests: terrain-to-resource mapping
# ------------------------------------------------------------------ #

class TestTerrainAndResource:
    def test_resource_matches_terrain(self, earth_gen):
        terrain, resource = earth_gen.get_terrain_and_resource(10, 20)
        assert terrain in EARTH_WEIGHTS
        assert resource == EARTH_RESOURCE_MAP[terrain]

    def test_ruins_has_no_resource(self, industrial_gen):
        """Ruins terrain has resource_type=None."""
        # Find a coordinate that produces Ruins
        for x in range(200):
            for y in range(200):
                t, r = industrial_gen.get_terrain_and_resource(x, y)
                if t == "Ruins":
                    assert r is None
                    return
        pytest.skip("No Ruins terrain found in search range")

    def test_resource_none_when_not_in_map(self):
        gen = TerrainGenerator(_make_space(weights={"Plains": 1.0}))
        # Don't set resource map — resource_map is empty
        _, resource = gen.get_terrain_and_resource(0, 0)
        assert resource is None


# ------------------------------------------------------------------ #
#  Tests: edge cases
# ------------------------------------------------------------------ #

class TestEdgeCases:
    def test_cell_size_zero_clamped_to_one(self):
        gen = TerrainGenerator(_make_space(cell_size=0))
        # Should not raise
        t = gen.get_terrain(5, 5)
        assert t in EARTH_WEIGHTS

    def test_cell_size_one(self):
        gen = TerrainGenerator(_make_space(cell_size=1))
        t = gen.get_terrain(5, 5)
        assert t in EARTH_WEIGHTS

    def test_large_coordinates(self, earth_gen):
        t = earth_gen.get_terrain(99999, 99999)
        assert t in EARTH_WEIGHTS

    def test_different_seeds_produce_different_terrain(self):
        gen1 = TerrainGenerator(_make_space(seed=1))
        gen2 = TerrainGenerator(_make_space(seed=999))
        # Over a grid, different seeds should produce at least some different results
        diffs = sum(
            1 for x in range(20) for y in range(20)
            if gen1.get_terrain(x, y) != gen2.get_terrain(x, y)
        )
        assert diffs > 0

    def test_single_terrain_weight(self):
        gen = TerrainGenerator(_make_space(weights={"Plains": 1.0}))
        for x in range(10):
            for y in range(10):
                assert gen.get_terrain(x, y) == "Plains"
