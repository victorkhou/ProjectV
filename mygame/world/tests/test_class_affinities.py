"""Unit tests for ClassDef terrain affinities (terrain-strategy task 1.8).

Covers the fail-fast affinity validation contract escalation in
``DataRegistry._load_classes``: per-entry checks (known terrain type, valid
kind, numeric non-bool adjustment), error collection across all classes, and
the sidegrade rule (positive affinity requires an offsetting weakness and a
non-empty description). Property tests for load round-trip and collective
failure live in test_prop_terrain_modifiers.py (tasks 1.10/1.11).
"""

import os
import shutil
import tempfile

import pytest
import yaml

from mygame.world.data_registry import DataRegistry, DataRegistryError


def _as_tuples(affinities):
    """Compare by field values: the registry may build TerrainAffinity from a
    differently-imported module object (world. vs mygame.world. path), which
    breaks direct dataclass equality across the import-path split."""
    return [(a.terrain_type, a.kind, a.adjustment) for a in affinities]

# ------------------------------------------------------------------ #
#  Minimal valid fixture data (self-contained on purpose)
# ------------------------------------------------------------------ #

_BUILDINGS = [
    {
        "name": "Headquarters", "abbreviation": "HQ",
        "cost": {"Wood": 100}, "max_health": 500, "requires_hq": False,
        "required_terrain": None, "category": "headquarters", "produces": None,
        "unlocks": [], "map_symbol": "HQ", "build_time_seconds": 180,
        "max_level": 5, "rank_requirement": 1, "requires_agent": False,
        "storage_capacity": 0,
    },
]

_ITEMS = {"items": [], "production_map": {}}

_RANKS = [
    {"name": "Recruit", "level": 1, "xp_threshold": 0, "unlocks": [],
     "agent_cap": 2, "planet_access": ["terra"]},
]

_TECHNOLOGIES = []

_POWERUPS = []

_TERRAIN = {
    "terrain": [
        {"terrain_type": "Plains", "map_symbol": "PP", "passable": True},
        {"terrain_type": "Forest", "map_symbol": "FF", "passable": True},
    ],
    "planets": [
        {"name": "Earth", "planet_type": "Earth_Planet",
         "terrain_types": ["Plains", "Forest"]},
    ],
}

_ABILITY_GATES = [
    {"key": "delivery", "required_level": 5},
]


@pytest.fixture
def data_dir():
    """Temp data dir with minimal valid required files (no classes.yaml)."""
    tmpdir = tempfile.mkdtemp()
    defs = os.path.join(tmpdir, "definitions")
    os.makedirs(defs)
    _write(os.path.join(defs, "buildings.yaml"), _BUILDINGS)
    _write(os.path.join(defs, "items.yaml"), _ITEMS)
    _write(os.path.join(defs, "ranks.yaml"), _RANKS)
    _write(os.path.join(defs, "technologies.yaml"), _TECHNOLOGIES)
    _write(os.path.join(defs, "powerups.yaml"), _POWERUPS)
    _write(os.path.join(defs, "terrain.yaml"), _TERRAIN)
    _write(os.path.join(defs, "ability_gates.yaml"), _ABILITY_GATES)
    yield tmpdir
    shutil.rmtree(tmpdir)


def _write(path: str, data) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


def _write_classes(data_dir: str, classes: list) -> None:
    _write(
        os.path.join(data_dir, "definitions", "classes.yaml"),
        {"classes": classes},
    )


def _load(data_dir: str) -> DataRegistry:
    reg = DataRegistry()
    reg.load_all(data_dir)
    return reg


# A balanced class: positive affinity offset by a negative one, named weakness.
_RANGER = {
    "key": "ranger",
    "name": "Ranger",
    "description": "At home in forests, exposed on plains.",
    "terrain_affinities": [
        {"terrain_type": "Forest", "kind": "movement", "adjustment": 1},
        {"terrain_type": "Plains", "kind": "defense", "adjustment": -2},
    ],
}


class TestAffinityLoading:
    def test_class_without_affinities_defaults_to_empty_list(self, data_dir):
        _write_classes(data_dir, [{"key": "vanguard", "name": "Vanguard"}])
        reg = _load(data_dir)
        assert reg.classes["vanguard"].terrain_affinities == []

    def test_valid_affinities_load_round_trip(self, data_dir):
        _write_classes(data_dir, [_RANGER])
        reg = _load(data_dir)
        assert _as_tuples(reg.classes["ranger"].terrain_affinities) == [
            ("Forest", "movement", 1.0),
            ("Plains", "defense", -2.0),
        ]

    def test_negative_stat_modifier_satisfies_sidegrade(self, data_dir):
        _write_classes(data_dir, [{
            "key": "scout", "name": "Scout",
            "description": "Sharp-eyed but frail.",
            "stat_modifiers": {"damage_reduction": -2},
            "terrain_affinities": [
                {"terrain_type": "Forest", "kind": "vision", "adjustment": 2},
            ],
        }])
        reg = _load(data_dir)
        assert _as_tuples(reg.classes["scout"].terrain_affinities) == [
            ("Forest", "vision", 2.0),
        ]


class TestAffinityValidationFailsFast:
    def test_unknown_terrain_bad_kind_and_bool_all_collected(self, data_dir):
        _write_classes(data_dir, [
            {"key": "alpha", "name": "Alpha", "description": "d",
             "terrain_affinities": [
                 {"terrain_type": "Swamp", "kind": "vision", "adjustment": -1},
             ]},
            {"key": "beta", "name": "Beta", "description": "d",
             "terrain_affinities": [
                 {"terrain_type": "Forest", "kind": "stealth", "adjustment": -1},
                 {"terrain_type": "Forest", "kind": "defense", "adjustment": True},
             ]},
        ])
        with pytest.raises(DataRegistryError) as exc:
            _load(data_dir)
        msg = str(exc.value)
        assert "alpha" in msg and "Swamp" in msg
        assert "beta" in msg and "stealth" in msg
        assert "non-numeric adjustment" in msg

    def test_non_numeric_string_adjustment_fails(self, data_dir):
        _write_classes(data_dir, [{
            "key": "gamma", "name": "Gamma", "description": "d",
            "terrain_affinities": [
                {"terrain_type": "Forest", "kind": "movement", "adjustment": "1"},
            ],
        }])
        with pytest.raises(DataRegistryError, match="non-numeric adjustment"):
            _load(data_dir)

    def test_non_list_affinities_fails(self, data_dir):
        _write_classes(data_dir, [{
            "key": "delta", "name": "Delta", "description": "d",
            "terrain_affinities": "Forest",
        }])
        with pytest.raises(DataRegistryError, match="must be a list"):
            _load(data_dir)

    def test_reload_keeps_current_classes_on_failure(self, data_dir):
        _write_classes(data_dir, [_RANGER])
        reg = _load(data_dir)
        _write_classes(data_dir, [{
            "key": "bad", "name": "Bad", "description": "d",
            "terrain_affinities": [
                {"terrain_type": "Nowhere", "kind": "vision", "adjustment": 1},
            ],
        }])
        ok, errors = reg.reload_all()
        assert not ok
        assert "ranger" in reg.classes and "bad" not in reg.classes


class TestSidegradeRule:
    def test_positive_only_class_fails_naming_class(self, data_dir):
        _write_classes(data_dir, [{
            "key": "hulk", "name": "Hulk", "description": "Strong everywhere.",
            "terrain_affinities": [
                {"terrain_type": "Forest", "kind": "defense", "adjustment": 3},
            ],
        }])
        with pytest.raises(DataRegistryError, match="hulk"):
            _load(data_dir)

    def test_positive_with_empty_description_fails(self, data_dir):
        _write_classes(data_dir, [{
            "key": "mute", "name": "Mute",
            "terrain_affinities": [
                {"terrain_type": "Forest", "kind": "vision", "adjustment": 1},
                {"terrain_type": "Plains", "kind": "vision", "adjustment": -1},
            ],
        }])
        with pytest.raises(DataRegistryError, match="description"):
            _load(data_dir)

    def test_negative_only_class_needs_no_offset(self, data_dir):
        _write_classes(data_dir, [{
            "key": "clumsy", "name": "Clumsy",
            "terrain_affinities": [
                {"terrain_type": "Forest", "kind": "movement", "adjustment": -1},
            ],
        }])
        reg = _load(data_dir)
        assert "clumsy" in reg.classes
