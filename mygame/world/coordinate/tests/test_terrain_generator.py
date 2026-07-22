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


# ------------------------------------------------------------------ #
#  Tests: noise equalization (realized frequencies match weights)
# ------------------------------------------------------------------ #

class TestEqualization:
    """The equalization step makes realized terrain frequencies track the
    configured weights, correcting the raw bilinear noise's bell-shaped bias
    that otherwise starves the first/last weight band and inflates the middle.
    """

    def _distribution(self, gen, w, h):
        from collections import Counter
        cnt = Counter()
        n = 0
        for y in range(h):
            for x in range(w):
                cnt[gen.get_terrain(x, y)] += 1
                n += 1
        return {k: v / n for k, v in cnt.items()}, n

    def test_realized_frequencies_track_weights(self):
        # Equal weights across 5 terrains → each should land near 20%, not the
        # ~2%/40%/2% spread the raw bell distribution produced.
        weights = {"Plains": 0.2, "Forest": 0.2, "Dirt": 0.2,
                   "Rock": 0.2, "Mountain": 0.2}
        gen = TerrainGenerator(_make_space(weights=weights, cell_size=14))
        dist, _ = self._distribution(gen, 100, 100)
        for terrain in weights:
            # within 8 points of the 20% target (raw noise missed by ~18)
            assert abs(dist.get(terrain, 0) - 0.20) < 0.08, (
                f"{terrain} at {dist.get(terrain, 0):.2%}, expected ~20%")

    def test_first_and_last_band_not_starved(self):
        # The first and last weight bands are exactly the ones the raw
        # distribution starved; equalization must give them meaningful share.
        weights = {"First": 0.25, "Mid": 0.5, "Last": 0.25}
        gen = TerrainGenerator(_make_space(weights=weights, cell_size=14))
        dist, _ = self._distribution(gen, 100, 100)
        assert dist.get("First", 0) > 0.12
        assert dist.get("Last", 0) > 0.12

    def test_equalization_preserves_determinism(self):
        # Same seed → identical results across two generators (the quantile
        # table is seed-derived, so it must not introduce nondeterminism).
        g1 = TerrainGenerator(_make_space(cell_size=14))
        g2 = TerrainGenerator(_make_space(cell_size=14))
        for x in range(30):
            for y in range(30):
                assert g1.get_terrain(x, y) == g2.get_terrain(x, y)

    def test_empty_weights_equalization_noop(self):
        # No weights → no quantile table; get_terrain still returns "unknown".
        gen = TerrainGenerator(_make_space(weights={}))
        assert gen.get_terrain(5, 5) == "unknown"


# ------------------------------------------------------------------ #
#  Tests: latitude-based terrain distribution
# ------------------------------------------------------------------ #

class _FakeTDef:
    def __init__(self, resource=None, lat_bias=0.0, lat_min=0.0):
        self.resource_type = resource
        self.latitude_bias = lat_bias
        self.latitude_min = lat_min


class _FakeRegistry:
    def __init__(self, defs):
        self._defs = defs

    def get_terrain(self, name):
        return self._defs[name]


class TestLatitudeDistribution:
    """Terrain with latitude_bias / latitude_min concentrates by latitude.

    lat = |2*y/H - 1|: 0 at the vertical middle (equator), 1 at top/bottom
    edges (poles).
    """

    def _gen(self, height=100):
        # Snow: strong pole bias + hard cutoff below lat 0.3 (no middle-30%).
        # Mountain: pole bias. Sand: equator bias. Plains: neutral filler.
        weights = {"Snow": 0.25, "Mountain": 0.25, "Sand": 0.25, "Plains": 0.25}
        defs = {
            "Snow": _FakeTDef(lat_bias=2.0, lat_min=0.3),
            "Mountain": _FakeTDef(lat_bias=1.5),
            "Sand": _FakeTDef(lat_bias=-1.5),
            "Plains": _FakeTDef(),
        }
        space = _make_space(weights=weights, cell_size=14)
        space.height = height
        reg = _FakeRegistry(defs)
        return TerrainGenerator(space, data_registry=reg), height

    def _freq(self, gen, terrain, y_lo, y_hi, w=100):
        n = 0
        hits = 0
        for y in range(y_lo, y_hi):
            for x in range(w):
                if gen.get_terrain(x, y) == terrain:
                    hits += 1
                n += 1
        return hits / n if n else 0.0

    def test_snow_absent_in_middle_30_percent(self):
        gen, h = self._gen()
        # Central 30% of rows = lat < 0.3. The exact boundary rows sit AT lat
        # 0.3 (edge of the middle band, still allowed), so assert on the
        # strictly-interior rows (0.36h .. 0.64h) where lat < 0.3 holds.
        lo, hi = int(0.36 * h), int(0.64 * h)
        assert self._freq(gen, "Snow", lo, hi) == 0.0

    def test_snow_present_at_poles(self):
        gen, h = self._gen()
        assert self._freq(gen, "Snow", 0, int(0.15 * h)) > 0.15

    def test_mountain_biased_poleward(self):
        gen, h = self._gen()
        pole = self._freq(gen, "Mountain", 0, int(0.15 * h))
        equator = self._freq(gen, "Mountain", int(0.42 * h), int(0.58 * h))
        assert pole > equator

    def test_sand_biased_equatorward(self):
        gen, h = self._gen()
        pole = self._freq(gen, "Sand", 0, int(0.15 * h))
        equator = self._freq(gen, "Sand", int(0.42 * h), int(0.58 * h))
        assert equator > pole

    def test_latitude_is_deterministic(self):
        g1, _ = self._gen()
        g2, _ = self._gen()
        for x in range(20):
            for y in range(20):
                assert g1.get_terrain(x, y) == g2.get_terrain(x, y)

    def test_no_latitude_rule_uses_flat_path(self):
        # All-neutral defs → _has_latitude_rule False → flat thresholds.
        weights = {"Plains": 0.5, "Rock": 0.5}
        defs = {"Plains": _FakeTDef(), "Rock": _FakeTDef()}
        space = _make_space(weights=weights, cell_size=14)
        gen = TerrainGenerator(space, data_registry=_FakeRegistry(defs))
        assert gen._has_latitude_rule is False
