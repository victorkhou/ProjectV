"""Unit tests for DataRegistry."""

import os
import shutil
import tempfile

import pytest
import yaml

from mygame.world.data_registry import DataRegistry, DataRegistryError


# ------------------------------------------------------------------ #
#  Fixture helpers — minimal valid YAML data
# ------------------------------------------------------------------ #

VALID_BUILDINGS = [
    {
        "name": "Headquarters",
        "abbreviation": "HQ",
        "cost": {"wood": 100, "stone": 100},
        "max_health": 500,
        "requires_hq": False,
        "required_terrain": None,
        "category": "headquarters",
        "produces": None,
        "unlocks": ["MM"],
        "map_symbol": "HQ",
    },
    {
        "name": "Mill",
        "abbreviation": "MM",
        "cost": {"straw": 50},
        "max_health": 200,
        "requires_hq": True,
        "required_terrain": "Plains",
        "category": "resource",
        "produces": "Straw",
        "unlocks": [],
        "map_symbol": "MM",
    },
    {
        "name": "Armory",
        "abbreviation": "AA",
        "cost": {"iron": 80},
        "max_health": 300,
        "requires_hq": True,
        "required_terrain": None,
        "category": "equipment",
        "produces": None,
        "unlocks": [],
        "map_symbol": "AA",
    },
]

VALID_ITEMS = {
    "items": [
        {
            "key": "combat_knife",
            "name": "Combat Knife",
            "slot": "weapon",
            "stat_modifiers": {"damage": 10, "range": 1},
            "ammo_cost": None,
            "classification": "modern",
            "required_rank": None,
        },
        {
            "key": "kevlar_vest",
            "name": "Kevlar Vest",
            "slot": "armor",
            "stat_modifiers": {"damage_reduction": 5},
            "ammo_cost": None,
            "classification": "modern",
            "required_rank": None,
        },
    ],
    "production_map": {
        "AA": ["combat_knife"],
    },
}

VALID_RANKS = [
    {"name": "Recruit", "level": 1, "xp_threshold": 0, "unlocks": []},
    {"name": "Private", "level": 2, "xp_threshold": 100, "unlocks": []},
    {"name": "Corporal", "level": 3, "xp_threshold": 300, "unlocks": []},
    {"name": "Sergeant", "level": 4, "xp_threshold": 600, "unlocks": []},
]

VALID_TECHNOLOGIES = [
    {
        "name": "Reinforced Walls",
        "key": "reinforced_walls",
        "required_rank": "Sergeant",
        "resource_cost": {"stone": 200},
        "research_ticks": 60,
        "effect_type": "stat_bonus",
        "effect_value": {"stat": "max_hp", "bonus": 50},
    },
]

VALID_POWERUPS = [
    {
        "name": "Adrenaline Rush",
        "key": "adrenaline_rush",
        "required_rank": "Corporal",
        "effect_type": "damage_bonus",
        "effect_value": 1.5,
        "duration_ticks": 30,
        "cooldown_ticks": 120,
    },
]

VALID_TERRAIN = {
    "terrain": [
        {"terrain_type": "Plains", "map_symbol": "PP", "resource_type": "Straw", "passable": True},
        {"terrain_type": "Forest", "map_symbol": "FF", "resource_type": "Wood", "passable": True},
    ],
    "planets": [
        {"name": "Earth", "planet_type": "Earth_Planet", "terrain_types": ["Plains", "Forest"]},
    ],
}

VALID_BALANCE = {
    "production_scaling": {1: 10, 2: 50, 3: 150, 4: 400, 5: 1000},
    "turret_damage": 15,
    "turret_radius": 10,
    "xp_kill": 100,
    "xp_building_destroy": 50,
    "xp_damage": 0.1,
    "xp_death_loss": 50,
    "gather_amount": 1,
    "player_default_health": 100,
    "resource_respawn_ticks": 30,
    "combat_lockout_ticks": 5,
    "tick_interval": 1.0,
    "chunk_size": 10,
    "save_interval": 30,
    "metrics_enabled": False,
    "metrics_interval": 60,
}


# ------------------------------------------------------------------ #
#  Fixture: write YAML files to a temp directory
# ------------------------------------------------------------------ #

@pytest.fixture
def data_dir():
    """Create a temp directory with all valid YAML files."""
    tmpdir = tempfile.mkdtemp()
    defs = os.path.join(tmpdir, "definitions")
    conf = os.path.join(tmpdir, "config")
    os.makedirs(defs)
    os.makedirs(conf)

    _write_yaml(os.path.join(defs, "buildings.yaml"), VALID_BUILDINGS)
    _write_yaml(os.path.join(defs, "items.yaml"), VALID_ITEMS)
    _write_yaml(os.path.join(defs, "ranks.yaml"), VALID_RANKS)
    _write_yaml(os.path.join(defs, "technologies.yaml"), VALID_TECHNOLOGIES)
    _write_yaml(os.path.join(defs, "powerups.yaml"), VALID_POWERUPS)
    _write_yaml(os.path.join(defs, "terrain.yaml"), VALID_TERRAIN)
    _write_yaml(os.path.join(conf, "balance.yaml"), VALID_BALANCE)

    yield tmpdir
    shutil.rmtree(tmpdir)


@pytest.fixture
def data_dir_no_balance():
    """Create a temp directory with all required files but no balance config."""
    tmpdir = tempfile.mkdtemp()
    defs = os.path.join(tmpdir, "definitions")
    os.makedirs(defs)

    _write_yaml(os.path.join(defs, "buildings.yaml"), VALID_BUILDINGS)
    _write_yaml(os.path.join(defs, "items.yaml"), VALID_ITEMS)
    _write_yaml(os.path.join(defs, "ranks.yaml"), VALID_RANKS)
    _write_yaml(os.path.join(defs, "technologies.yaml"), VALID_TECHNOLOGIES)
    _write_yaml(os.path.join(defs, "powerups.yaml"), VALID_POWERUPS)
    _write_yaml(os.path.join(defs, "terrain.yaml"), VALID_TERRAIN)

    yield tmpdir
    shutil.rmtree(tmpdir)


def _write_yaml(path: str, data) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


# ------------------------------------------------------------------ #
#  Tests: successful loading
# ------------------------------------------------------------------ #

class TestLoadAll:
    def test_loads_all_definitions(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        assert "HQ" in reg.buildings
        assert "MM" in reg.buildings
        assert "combat_knife" in reg.items
        assert "kevlar_vest" in reg.items
        assert len(reg.ranks) == 4
        assert "reinforced_walls" in reg.technologies
        assert "adrenaline_rush" in reg.powerups
        assert "Plains" in reg.terrain
        assert "Earth" in reg.planets

    def test_buildings_populated_correctly(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        hq = reg.get_building("HQ")
        assert hq.name == "Headquarters"
        assert hq.max_health == 500
        assert hq.requires_hq is False
        assert hq.cost == {"wood": 100, "stone": 100}

    def test_items_populated_correctly(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        knife = reg.get_item("combat_knife")
        assert knife.slot == "weapon"
        assert knife.stat_modifiers["damage"] == 10

    def test_ranks_sorted_by_level(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        levels = [r.level for r in reg.ranks]
        assert levels == sorted(levels)

    def test_production_map_loaded(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        assert "AA" in reg.item_production_map
        assert "combat_knife" in reg.item_production_map["AA"]

    def test_balance_loaded(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        assert reg.balance.turret_damage == 15
        assert reg.balance.production_scaling[5] == 1000


# ------------------------------------------------------------------ #
#  Tests: missing files
# ------------------------------------------------------------------ #

class TestMissingFiles:
    def test_missing_required_file_aborts(self, data_dir):
        os.remove(os.path.join(data_dir, "definitions", "buildings.yaml"))
        reg = DataRegistry()
        with pytest.raises(DataRegistryError, match="missing required definition files"):
            reg.load_all(data_dir)

    def test_missing_balance_uses_defaults(self, data_dir_no_balance):
        reg = DataRegistry()
        reg.load_all(data_dir_no_balance)

        defaults = reg.balance
        assert defaults.turret_damage == 15
        assert defaults.production_scaling == {1: 10, 2: 50, 3: 150, 4: 400, 5: 1000}

    def test_missing_balance_logs_warning(self, data_dir_no_balance, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="mygame.data_registry"):
            reg = DataRegistry()
            reg.load_all(data_dir_no_balance)
        assert "using hardcoded defaults" in caplog.text.lower()


# ------------------------------------------------------------------ #
#  Tests: getter methods
# ------------------------------------------------------------------ #

class TestGetters:
    @pytest.fixture(autouse=True)
    def setup_registry(self, data_dir):
        self.reg = DataRegistry()
        self.reg.load_all(data_dir)

    def test_get_building(self):
        b = self.reg.get_building("HQ")
        assert b.abbreviation == "HQ"

    def test_get_building_missing_raises(self):
        with pytest.raises(KeyError):
            self.reg.get_building("ZZ")

    def test_get_item(self):
        i = self.reg.get_item("combat_knife")
        assert i.name == "Combat Knife"

    def test_get_item_missing_raises(self):
        with pytest.raises(KeyError):
            self.reg.get_item("nonexistent")

    def test_get_items_for_slot(self):
        weapons = self.reg.get_items_for_slot("weapon")
        assert len(weapons) == 1
        assert weapons[0].key == "combat_knife"

    def test_get_items_for_slot_empty(self):
        gadgets = self.reg.get_items_for_slot("gadget")
        assert gadgets == []

    def test_get_items_for_building(self):
        items = self.reg.get_items_for_building("AA")
        assert len(items) == 1
        assert items[0].key == "combat_knife"

    def test_get_items_for_building_unknown(self):
        items = self.reg.get_items_for_building("ZZ")
        assert items == []

    def test_get_rank_for_xp_lowest(self):
        rank = self.reg.get_rank_for_xp(0)
        assert rank.name == "Recruit"

    def test_get_rank_for_xp_exact_threshold(self):
        rank = self.reg.get_rank_for_xp(100)
        assert rank.name == "Private"

    def test_get_rank_for_xp_between_thresholds(self):
        rank = self.reg.get_rank_for_xp(250)
        assert rank.name == "Private"

    def test_get_rank_for_xp_high(self):
        rank = self.reg.get_rank_for_xp(9999)
        assert rank.name == "Sergeant"

    def test_get_rank_by_name(self):
        rank = self.reg.get_rank_by_name("Corporal")
        assert rank.level == 3

    def test_get_rank_by_name_missing_raises(self):
        with pytest.raises(KeyError):
            self.reg.get_rank_by_name("General")

    def test_get_technologies_for_rank(self):
        # Sergeant is level 4, reinforced_walls requires Sergeant
        techs = self.reg.get_technologies_for_rank(4)
        assert len(techs) == 1
        assert techs[0].key == "reinforced_walls"

    def test_get_technologies_for_rank_too_low(self):
        techs = self.reg.get_technologies_for_rank(1)
        assert len(techs) == 0

    def test_get_powerups_for_rank(self):
        # Corporal is level 3, adrenaline_rush requires Corporal
        powerups = self.reg.get_powerups_for_rank(3)
        assert len(powerups) == 1
        assert powerups[0].key == "adrenaline_rush"

    def test_get_powerups_for_rank_too_low(self):
        powerups = self.reg.get_powerups_for_rank(1)
        assert len(powerups) == 0

    def test_get_terrain(self):
        t = self.reg.get_terrain("Plains")
        assert t.map_symbol == "PP"

    def test_get_terrain_missing_raises(self):
        with pytest.raises(KeyError):
            self.reg.get_terrain("Lava")

    def test_get_planet(self):
        p = self.reg.get_planet("Earth")
        assert "Plains" in p.terrain_types

    def test_get_planet_missing_raises(self):
        with pytest.raises(KeyError):
            self.reg.get_planet("Mars")


# ------------------------------------------------------------------ #
#  Tests: hot-reload
# ------------------------------------------------------------------ #

class TestReload:
    def test_reload_success_swaps_data(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert reg.balance.turret_damage == 15

        # Modify balance file
        balance_path = os.path.join(data_dir, "config", "balance.yaml")
        new_balance = dict(VALID_BALANCE)
        new_balance["turret_damage"] = 99
        _write_yaml(balance_path, new_balance)

        success, errors = reg.reload_all()
        assert success is True
        assert errors == []
        assert reg.balance.turret_damage == 99

    def test_reload_failure_preserves_data(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)
        original_buildings = dict(reg.buildings)

        # Corrupt a required file
        os.remove(os.path.join(data_dir, "definitions", "buildings.yaml"))

        success, errors = reg.reload_all()
        assert success is False
        assert len(errors) > 0
        # Original data preserved
        assert reg.buildings == original_buildings
