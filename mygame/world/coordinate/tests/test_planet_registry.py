"""Unit tests for PlanetRegistry."""

import os
import shutil
import tempfile

import pytest
import yaml

from mygame.world.coordinate.planet_registry import PlanetRegistry, PlanetRegistryError


# ------------------------------------------------------------------ #
#  Fixture helpers — minimal valid YAML data
# ------------------------------------------------------------------ #

VALID_PLANETS = {
    "planets": [
        {
            "planet_key": "earth_planet",
            "planet_type": "earth",
            "width": 100,
            "height": 100,
            "terrain_seed": 42,
            "terrain_weights": {"Plains": 0.35, "Forest": 0.25, "Dirt": 0.15, "Rock": 0.15, "Mountain": 0.10},
            "persistence_type": "static",
            "spawn_x": 50,
            "spawn_y": 50,
            "default_planet": True,
        },
        {
            "planet_key": "space",
            "planet_type": "space",
            "width": 200,
            "height": 200,
            "terrain_seed": 99,
            "terrain_weights": {},
            "persistence_type": "dynamic",
            "spawn_x": 100,
            "spawn_y": 100,
        },
    ]
}


def _write_yaml(path: str, data) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


# ------------------------------------------------------------------ #
#  Fixture: write YAML to a temp file
# ------------------------------------------------------------------ #

@pytest.fixture
def planets_yaml():
    """Create a temp YAML file with valid planet definitions."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "planets.yaml")
    _write_yaml(path, VALID_PLANETS)
    yield path
    shutil.rmtree(tmpdir)


@pytest.fixture
def registry(planets_yaml):
    """Return a PlanetRegistry loaded with valid data."""
    reg = PlanetRegistry()
    reg.load_from_yaml(planets_yaml)
    return reg


# ------------------------------------------------------------------ #
#  Tests: successful loading
# ------------------------------------------------------------------ #

class TestLoadFromYaml:
    def test_loads_all_planets(self, registry):
        assert "earth_planet" in registry.list_planets()
        assert "space" in registry.list_planets()

    def test_planet_count(self, registry):
        assert len(registry.list_planets()) == 2

    def test_earth_space_fields(self, registry):
        space = registry.get_space("earth_planet")
        assert space.planet_key == "earth_planet"
        assert space.planet_type == "earth"
        assert space.width == 100
        assert space.height == 100
        assert space.terrain_seed == 42
        assert space.spawn_x == 50
        assert space.spawn_y == 50
        assert space.default_planet is True

    def test_dynamic_space_fields(self, registry):
        space = registry.get_space("space")
        assert space.persistence_type == "dynamic"
        assert space.width == 200
        assert space.height == 200
        assert space.terrain_weights == {}

    def test_default_persistence_type_is_static(self):
        """When persistence_type is omitted, it defaults to 'static'."""
        data = {
            "planets": [
                {
                    "planet_key": "test_planet",
                    "planet_type": "earth",
                    "width": 10,
                    "height": 10,
                    "terrain_seed": 1,
                    "terrain_weights": {"Plains": 1.0},
                }
            ]
        }
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "planets.yaml")
            _write_yaml(path, data)
            reg = PlanetRegistry()
            reg.load_from_yaml(path)
            assert reg.get_space("test_planet").persistence_type == "static"
        finally:
            shutil.rmtree(tmpdir)

    def test_default_noise_cell_size(self, registry):
        space = registry.get_space("earth_planet")
        assert space.terrain_noise_cell_size == 8


# ------------------------------------------------------------------ #
#  Tests: get_space
# ------------------------------------------------------------------ #

class TestGetSpace:
    def test_returns_correct_space(self, registry):
        space = registry.get_space("earth_planet")
        assert space.planet_key == "earth_planet"

    def test_unknown_key_raises_key_error(self, registry):
        with pytest.raises(KeyError):
            registry.get_space("mars")


# ------------------------------------------------------------------ #
#  Tests: list_planets
# ------------------------------------------------------------------ #

class TestListPlanets:
    def test_returns_all_keys(self, registry):
        keys = registry.list_planets()
        assert set(keys) == {"earth_planet", "space"}

    def test_empty_registry(self):
        reg = PlanetRegistry()
        assert reg.list_planets() == []


# ------------------------------------------------------------------ #
#  Tests: is_valid_coordinate
# ------------------------------------------------------------------ #

class TestIsValidCoordinate:
    def test_origin_is_valid(self, registry):
        assert registry.is_valid_coordinate(0, 0, "earth_planet") is True

    def test_max_corner_is_valid(self, registry):
        assert registry.is_valid_coordinate(99, 99, "earth_planet") is True

    def test_at_width_boundary_is_invalid(self, registry):
        assert registry.is_valid_coordinate(100, 0, "earth_planet") is False

    def test_at_height_boundary_is_invalid(self, registry):
        assert registry.is_valid_coordinate(0, 100, "earth_planet") is False

    def test_negative_x_is_invalid(self, registry):
        assert registry.is_valid_coordinate(-1, 0, "earth_planet") is False

    def test_negative_y_is_invalid(self, registry):
        assert registry.is_valid_coordinate(0, -1, "earth_planet") is False

    def test_mid_coordinate_is_valid(self, registry):
        assert registry.is_valid_coordinate(50, 50, "earth_planet") is True

    def test_unknown_planet_raises_key_error(self, registry):
        with pytest.raises(KeyError):
            registry.is_valid_coordinate(0, 0, "nonexistent")

    def test_space_planet_bounds(self, registry):
        # space is 200x200
        assert registry.is_valid_coordinate(199, 199, "space") is True
        assert registry.is_valid_coordinate(200, 0, "space") is False


# ------------------------------------------------------------------ #
#  Tests: validation errors
# ------------------------------------------------------------------ #

class TestValidationErrors:
    def _load_planets(self, planets_list):
        """Helper: write a planets YAML and attempt to load it."""
        data = {"planets": planets_list}
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "planets.yaml")
            _write_yaml(path, data)
            reg = PlanetRegistry()
            reg.load_from_yaml(path)
            return reg
        finally:
            shutil.rmtree(tmpdir)

    def test_duplicate_planet_key_raises(self):
        planets = [
            {"planet_key": "dup", "planet_type": "earth", "width": 10, "height": 10,
             "terrain_seed": 1, "terrain_weights": {"Plains": 1.0}},
            {"planet_key": "dup", "planet_type": "earth", "width": 20, "height": 20,
             "terrain_seed": 2, "terrain_weights": {"Plains": 1.0}},
        ]
        with pytest.raises(PlanetRegistryError, match="Duplicate planet_key"):
            self._load_planets(planets)

    def test_zero_width_raises(self):
        planets = [
            {"planet_key": "bad", "planet_type": "earth", "width": 0, "height": 10,
             "terrain_seed": 1, "terrain_weights": {"Plains": 1.0}},
        ]
        with pytest.raises(PlanetRegistryError, match="width must be a positive integer"):
            self._load_planets(planets)

    def test_negative_height_raises(self):
        planets = [
            {"planet_key": "bad", "planet_type": "earth", "width": 10, "height": -5,
             "terrain_seed": 1, "terrain_weights": {"Plains": 1.0}},
        ]
        with pytest.raises(PlanetRegistryError, match="height must be a positive integer"):
            self._load_planets(planets)

    def test_missing_planet_key_raises(self):
        planets = [
            {"planet_type": "earth", "width": 10, "height": 10,
             "terrain_seed": 1, "terrain_weights": {"Plains": 1.0}},
        ]
        with pytest.raises(PlanetRegistryError, match="missing 'planet_key'"):
            self._load_planets(planets)

    def test_missing_file_raises(self):
        reg = PlanetRegistry()
        with pytest.raises(PlanetRegistryError, match="not found"):
            reg.load_from_yaml("/nonexistent/path/planets.yaml")

    def test_missing_planets_key_raises(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "planets.yaml")
            _write_yaml(path, {"something_else": []})
            reg = PlanetRegistry()
            with pytest.raises(PlanetRegistryError, match="must contain a 'planets' key"):
                reg.load_from_yaml(path)
        finally:
            shutil.rmtree(tmpdir)

    def test_terrain_weights_sum_zero_raises(self):
        planets = [
            {"planet_key": "bad", "planet_type": "earth", "width": 10, "height": 10,
             "terrain_seed": 1, "terrain_weights": {"Plains": 0.0, "Forest": 0.0}},
        ]
        with pytest.raises(PlanetRegistryError, match="terrain_weights sum must be > 0"):
            self._load_planets(planets)
