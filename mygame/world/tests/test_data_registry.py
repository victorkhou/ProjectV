"""Unit tests for DataRegistry."""

import os
import shutil
import tempfile

import pytest
import yaml

from mygame.world.constants import LEVELS_PER_RANK, MAX_LEVEL
from mygame.world.data_registry import DataRegistry, DataRegistryError
from mygame.world.definitions import BalanceConfig


# ------------------------------------------------------------------ #
#  Fixture helpers — minimal valid YAML data
# ------------------------------------------------------------------ #

VALID_BUILDINGS = [
    {
        "name": "Headquarters",
        "abbreviation": "HQ",
        "cost": {"Wood": 100, "Stone": 100},
        "max_health": 500,
        "requires_hq": False,
        "required_terrain": None,
        "category": "headquarters",
        "produces": None,
        "unlocks": ["MM"],
        "map_symbol": "HQ",
        "build_time_seconds": 180,
        "max_level": 5,
        "rank_requirement": 1,
        "requires_agent": False,
        "storage_capacity": 0,
    },
    {
        "name": "Mill",
        "abbreviation": "MM",
        "cost": {"Wood": 50},
        "max_health": 200,
        "requires_hq": True,
        "required_terrain": "Plains",
        "category": "resource",
        "produces": "Wood",
        "unlocks": [],
        "map_symbol": "MM",
        "build_time_seconds": 120,
        "max_level": 5,
        "rank_requirement": 2,
        "requires_agent": False,
        "storage_capacity": 100,
    },
    {
        "name": "Armory",
        "abbreviation": "AA",
        "cost": {"Iron": 80},
        "max_health": 300,
        "requires_hq": True,
        "required_terrain": None,
        "category": "equipment",
        "produces": None,
        "unlocks": [],
        "map_symbol": "AA",
        "build_time_seconds": 240,
        "max_level": 5,
        "rank_requirement": 3,
        "requires_agent": True,
        "storage_capacity": 0,
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
            "slot": "torso",
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
    {"name": "Recruit", "level": 1, "xp_threshold": 0, "unlocks": [],
     "agent_cap": 2, "planet_access": ["terra"]},
    {"name": "Private", "level": 2, "xp_threshold": 100, "unlocks": [],
     "agent_cap": 3, "planet_access": ["terra"]},
    {"name": "Corporal", "level": 3, "xp_threshold": 300, "unlocks": [],
     "agent_cap": 4, "planet_access": ["terra"]},
    {"name": "Sergeant", "level": 4, "xp_threshold": 600, "unlocks": [],
     "agent_cap": 6, "planet_access": ["terra", "forge"]},
]

VALID_TECHNOLOGIES = [
    {
        "name": "Reinforced Walls",
        "key": "reinforced_walls",
        "required_rank": "Sergeant",
        "resource_cost": {"Stone": 200},
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
        {"terrain_type": "Plains", "map_symbol": "PP", "resource_type": "Stone", "passable": True},
        {"terrain_type": "Forest", "map_symbol": "FF", "resource_type": "Wood", "passable": True},
    ],
    "planets": [
        {"name": "Earth", "planet_type": "Earth_Planet", "terrain_types": ["Plains", "Forest"]},
    ],
}

VALID_ABILITY_GATES = [
    {"key": "delivery", "required_level": 21},
]

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
    _write_yaml(os.path.join(defs, "ability_gates.yaml"), VALID_ABILITY_GATES)
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
    _write_yaml(os.path.join(defs, "ability_gates.yaml"), VALID_ABILITY_GATES)

    yield tmpdir
    shutil.rmtree(tmpdir)


def _write_yaml(path: str, data) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


# Agent-XP balance values deliberately chosen to differ from every
# BalanceConfig default, so a passing assertion proves the value was sourced
# from the loaded config rather than a hardcoded literal/default.
CUSTOM_AGENT_XP = {
    "agent_xp_harvest": 7,        # default 5
    "agent_xp_delivery": 17,      # default 15
    "agent_xp_construction": 23,  # default 20
    "agent_xp_combat": 55,        # default 50
    "agent_xp_time_served": 3,    # default 0
    "agent_xp_death_loss": 29,    # default 25
}


@pytest.fixture
def data_dir_custom_agent_xp():
    """Temp data dir whose balance.yaml carries non-default agent-XP values."""
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
    _write_yaml(os.path.join(defs, "ability_gates.yaml"), VALID_ABILITY_GATES)

    custom_balance = dict(VALID_BALANCE)
    custom_balance.update(CUSTOM_AGENT_XP)
    _write_yaml(os.path.join(conf, "balance.yaml"), custom_balance)

    yield tmpdir
    shutil.rmtree(tmpdir)


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
        assert "delivery" in reg.ability_gates

    def test_buildings_populated_correctly(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        hq = reg.get_building("HQ")
        assert hq.name == "Headquarters"
        assert hq.max_health == 500
        assert hq.requires_hq is False
        assert hq.cost == {"Wood": 100, "Stone": 100}

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

    def test_resolve_building_by_abbreviation(self):
        assert self.reg.resolve_building("HQ").abbreviation == "HQ"

    def test_resolve_building_by_abbreviation_case_insensitive(self):
        assert self.reg.resolve_building("hq").abbreviation == "HQ"

    def test_resolve_building_by_full_name(self):
        # The reported bug: "build extractor" (the full name) must resolve.
        assert self.reg.resolve_building("Headquarters").abbreviation == "HQ"

    def test_resolve_building_by_name_case_insensitive(self):
        assert self.reg.resolve_building("headquarters").abbreviation == "HQ"

    def test_resolve_building_unknown_returns_none(self):
        assert self.reg.resolve_building("nonsense") is None

    def test_resolve_building_empty_returns_none(self):
        assert self.reg.resolve_building("") is None

    def test_resolve_building_by_unambiguous_name_prefix(self):
        # "head" is a unique prefix of "Headquarters" among HQ/Mill/Armory.
        assert self.reg.resolve_building("head").abbreviation == "HQ"

    def test_resolve_building_by_unambiguous_prefix_arm(self):
        assert self.reg.resolve_building("arm").abbreviation == "AA"

    def test_resolve_item_by_unambiguous_key_prefix(self):
        # "combat_kn" uniquely prefixes combat_knife (kevlar_vest differs).
        assert self.reg.resolve_item("combat_kn").key == "combat_knife"

    def test_resolve_item_by_unambiguous_name_prefix(self):
        assert self.reg.resolve_item("kev").key == "kevlar_vest"

    def test_resolve_item_exact_key_beats_prefix(self):
        # An exact key still wins outright (order: exact key, exact name, prefix).
        assert self.reg.resolve_item("combat_knife").key == "combat_knife"

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

    def test_get_ability_gate(self):
        gate = self.reg.get_ability_gate("delivery")
        assert gate.key == "delivery"
        assert gate.required_level == 21

    def test_get_ability_gate_missing_raises(self):
        with pytest.raises(KeyError):
            self.reg.get_ability_gate("teleport")

    def test_get_ability_gates(self):
        gates = self.reg.get_ability_gates()
        assert len(gates) == 1
        assert gates[0].key == "delivery"


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

    def test_reload_rebuilds_progression_thresholds(self, data_dir):
        """A ranks.yaml hot-reload must rebuild the shared level<->XP curve.

        Regression: reload_all previously swapped self.ranks but never called
        progression.build_thresholds, leaving CombatEntity.get_raw_level /
        RankSystem.level_for_xp deriving levels from the OLD curve until restart
        (design.md 'rebuilds world.progression thresholds on success').
        """
        # Import the SAME module object production mutates. The codebase uses
        # ``from world import progression`` (top-level ``world`` package), and
        # ``reload_all`` rebuilds that module's table — importing it as
        # ``mygame.world.progression`` would be a distinct module object with
        # its own (stale) ``_level_thresholds`` and mask the fix.
        from world import progression

        reg = DataRegistry()
        reg.load_all(data_dir)
        # Establish the baseline curve exactly as game_init does at startup.
        progression.build_thresholds(reg.ranks)

        # Level 6 is the first level of rank 2 (Private); its threshold equals
        # Private's xp_threshold (100 in VALID_RANKS).
        first_level_of_rank_two = (2 - 1) * LEVELS_PER_RANK + 1
        assert first_level_of_rank_two == 6
        assert progression.xp_for_level(6) == 100

        # Retune Private's xp_threshold and hot-reload. Kept below Corporal's
        # 300 so the monotonic-threshold rank validator still passes.
        ranks_path = os.path.join(data_dir, "definitions", "ranks.yaml")
        new_ranks = [dict(r) for r in VALID_RANKS]
        new_ranks[1]["xp_threshold"] = 250  # Private: 100 -> 250
        _write_yaml(ranks_path, new_ranks)

        success, errors = reg.reload_all()
        assert success is True
        assert errors == []

        # The shared curve must reflect the reloaded ranks, not the stale table.
        assert reg.get_rank_by_name("Private").xp_threshold == 250
        assert progression.xp_for_level(6) == 250

    def test_reload_survives_progression_import_failure(self, data_dir, monkeypatch):
        """A rebuild hiccup must not invalidate an otherwise-successful swap."""
        from world import progression

        reg = DataRegistry()
        reg.load_all(data_dir)

        def _boom(_ranks):
            raise RuntimeError("threshold build failed")

        monkeypatch.setattr(progression, "build_thresholds", _boom)

        balance_path = os.path.join(data_dir, "config", "balance.yaml")
        new_balance = dict(VALID_BALANCE)
        new_balance["turret_damage"] = 77
        _write_yaml(balance_path, new_balance)

        # The swap succeeds even though the threshold rebuild raised.
        success, errors = reg.reload_all()
        assert success is True
        assert errors == []
        assert reg.balance.turret_damage == 77


class TestSingleton:
    """DataRegistry.get_instance/set_instance process-wide accessor.

    Owner-agnostic helpers (world.progression, chat_system, agent_scripts)
    resolve the live registry through this accessor; a missing singleton must
    return None rather than raising an AttributeError.
    """

    def teardown_method(self):
        # Never leak a test registry into other tests' module-global state.
        DataRegistry.set_instance(None)

    def test_get_instance_defaults_to_none(self):
        DataRegistry.set_instance(None)
        assert DataRegistry.get_instance() is None

    def test_set_and_get_instance(self):
        reg = DataRegistry()
        DataRegistry.set_instance(reg)
        assert DataRegistry.get_instance() is reg

    def test_constructing_registry_does_not_usurp_singleton(self):
        reg = DataRegistry()
        DataRegistry.set_instance(reg)
        # A throwaway registry (as reload_all builds internally) must not
        # replace the registered live singleton.
        _temp = DataRegistry()
        assert DataRegistry.get_instance() is reg


# ------------------------------------------------------------------ #
#  Tests: gate data load and balance amounts (Task 1.7)
#  Requirements: 5.6, 6.4, 7.1, 7.2, 7.6, 7.7
# ------------------------------------------------------------------ #

class TestAbilityGateDerivation:
    """The delivery gate's required level is the first level of rank 5,
    derived from constants and clamped to MAX_LEVEL (Req 7.1, 7.2, 7.6, 7.7)."""

    def test_delivery_required_level_is_first_level_of_rank_five(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        # first level of rank 5 = (5 - 1) * LEVELS_PER_RANK + 1, clamped to MAX_LEVEL
        expected = min((5 - 1) * LEVELS_PER_RANK + 1, MAX_LEVEL)
        assert expected == 21  # guards against constant drift breaking the gate value

        gate = reg.get_ability_gate("delivery")
        assert gate.required_level == expected

    def test_delivery_required_level_within_valid_range(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        gate = reg.get_ability_gate("delivery")
        assert 1 <= gate.required_level <= MAX_LEVEL


class TestAgentXpSourcedFromBalance:
    """Every agent_xp_* amount is read from the balance config, not a
    hardcoded literal: loading non-default values surfaces them on
    registry.balance (Req 5.6, 6.4)."""

    AGENT_XP_FIELDS = (
        "agent_xp_harvest",
        "agent_xp_delivery",
        "agent_xp_construction",
        "agent_xp_combat",
        "agent_xp_time_served",
        "agent_xp_death_loss",
    )

    def test_agent_xp_values_loaded_from_balance_config(self, data_dir_custom_agent_xp):
        reg = DataRegistry()
        reg.load_all(data_dir_custom_agent_xp)

        for field, value in CUSTOM_AGENT_XP.items():
            assert getattr(reg.balance, field) == value

    def test_custom_agent_xp_values_differ_from_defaults(self):
        # Sanity check: the fixture's values are genuinely non-default, so the
        # load test above proves sourcing from config rather than the dataclass
        # default (which would pass even if loading were hardcoded).
        defaults = BalanceConfig()
        for field, value in CUSTOM_AGENT_XP.items():
            assert getattr(defaults, field) != value

    def test_all_six_agent_xp_fields_present_on_balance(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)

        for field in self.AGENT_XP_FIELDS:
            assert hasattr(reg.balance, field)
            assert isinstance(getattr(reg.balance, field), int)


# Economy tunables migrated from world.constants into BalanceConfig.
# Scalar values chosen to differ from every default so a passing assertion
# proves the value was sourced from the loaded YAML, not a dataclass default.
CUSTOM_ECONOMY = {
    "base_training_ticks": 222,                    # default 300
    "academy_training_reduction_per_level": 0.11,  # default 0.15
    "harvest_cooldown_ticks": 9,                   # default 4
    "harvest_yield_per_action": 2,                 # default 1
    "extractor_harvest_multiplier": 7,             # default 3
    "extractor_level_bonus": 0.33,                 # default 0.25
    "extractor_base_capacity": 120,                # default 100
    "extractor_capacity_per_level": 60,            # default 50
    "vault_base_capacity": 140,                    # default 100
    "vault_capacity_per_level": 25,                # default 20
    "upgrade_cost_base": 4,                        # default 2
    "upgrade_time_base": 5,                        # default 3
    "turret_level_bonus": 0.5,                     # default 0.20
    "demolish_refund_default": 0.35,               # default 0.40
}


@pytest.fixture
def data_dir_custom_economy():
    """Temp data dir whose balance.yaml carries non-default economy values.

    The nested-dict fields (base_training_cost, demolish_refund_rates) use
    STRING keys on purpose, to exercise the int-coercion path in
    DataRegistry._build_balance.
    """
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
    _write_yaml(os.path.join(defs, "ability_gates.yaml"), VALID_ABILITY_GATES)

    custom_balance = dict(VALID_BALANCE)
    custom_balance.update(CUSTOM_ECONOMY)
    custom_balance["base_training_cost"] = {"Wood": 99, "Stone": 88, "Iron": 77}
    custom_balance["demolish_refund_rates"] = {"1": 0.11, "2": 0.22, "3": 0.33}
    custom_balance["resource_weights"] = {"Wood": 0.9, "Nexium": 5.0}
    _write_yaml(os.path.join(conf, "balance.yaml"), custom_balance)

    yield tmpdir
    shutil.rmtree(tmpdir)


class TestEconomyTunablesSourcedFromBalance:
    """Economy constants migrated to BalanceConfig load from balance.yaml."""

    def test_scalar_economy_values_loaded(self, data_dir_custom_economy):
        reg = DataRegistry()
        reg.load_all(data_dir_custom_economy)

        for field, value in CUSTOM_ECONOMY.items():
            assert getattr(reg.balance, field) == value, field

    def test_custom_economy_values_differ_from_defaults(self):
        # Prove the fixture values are genuinely non-default so the load test
        # verifies config sourcing rather than passing on the dataclass default.
        defaults = BalanceConfig()
        for field, value in CUSTOM_ECONOMY.items():
            assert getattr(defaults, field) != value, field

    def test_base_training_cost_map_loaded(self, data_dir_custom_economy):
        reg = DataRegistry()
        reg.load_all(data_dir_custom_economy)

        assert reg.balance.base_training_cost == {"Wood": 99, "Stone": 88, "Iron": 77}

    def test_demolish_refund_rates_map_keys_coerced_to_int(self, data_dir_custom_economy):
        reg = DataRegistry()
        reg.load_all(data_dir_custom_economy)

        # YAML string keys must be coerced to int levels by _build_balance.
        assert reg.balance.demolish_refund_rates == {1: 0.11, 2: 0.22, 3: 0.33}

    def test_resource_weights_map_loaded(self, data_dir_custom_economy):
        reg = DataRegistry()
        reg.load_all(data_dir_custom_economy)

        # String resource keys stay verbatim; values load from balance.yaml.
        assert reg.balance.resource_weights == {"Wood": 0.9, "Nexium": 5.0}

    def test_defaults_used_when_balance_absent(self, data_dir_no_balance):
        reg = DataRegistry()
        reg.load_all(data_dir_no_balance)

        assert reg.balance.base_training_ticks == 300
        assert reg.balance.upgrade_cost_base == 2
        assert reg.balance.base_training_cost == {"Wood": 15, "Stone": 10, "Iron": 5}
        assert reg.balance.demolish_refund_rates[5] == 0.80
        assert reg.balance.resource_weights == {
            "Wood": 0.5, "Stone": 1.0, "Iron": 1.0,
            "Energy": 0.2, "Circuits": 0.3, "Nexium": 2.0,
        }

    def test_reload_repicks_up_economy_values(self, data_dir_custom_economy):
        # The migrated values must be hot-reloadable like the rest of balance.
        reg = DataRegistry()
        reg.load_all(data_dir_custom_economy)
        assert reg.balance.upgrade_cost_base == 4

        # Retune on disk, then reload_all should swap the new value in.
        conf = os.path.join(data_dir_custom_economy, "config", "balance.yaml")
        retuned = dict(VALID_BALANCE)
        retuned.update(CUSTOM_ECONOMY)
        retuned["upgrade_cost_base"] = 6
        _write_yaml(conf, retuned)

        ok, errors = reg.reload_all()
        assert ok, errors
        assert reg.balance.upgrade_cost_base == 6


# ------------------------------------------------------------------ #
#  Tests: NPC-base templates (PvE Phase 5, optional outposts.yaml)
# ------------------------------------------------------------------ #

_VALID_OUTPOSTS = {
    "outpost": {
        "display_name": "Outpost",
        "buildings": [
            {"type": "HQ", "offset": [0, 0], "hp": 200},
            {"type": "WL", "offset": [0, 1], "hp": 300},
        ],
        "guards": [{"role": "guard", "weapon_type": "melee", "count": 2}],
        "loot": {"Iron": 30, "Stone": 20},
    },
}


class TestBaseTemplates:
    def test_absent_outposts_yields_empty_templates(self, data_dir):
        """No outposts.yaml → empty template set (feature disabled), no error."""
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert reg.base_templates == {}
        assert reg.get_base_template("outpost") is None

    def test_templates_loaded_and_parsed(self, data_dir):
        _write_yaml(
            os.path.join(data_dir, "definitions", "outposts.yaml"),
            _VALID_OUTPOSTS,
        )
        reg = DataRegistry()
        reg.load_all(data_dir)

        tpl = reg.get_base_template("outpost")
        assert tpl is not None
        assert tpl.display_name == "Outpost"
        assert len(tpl.buildings) == 2
        hq = tpl.buildings[0]
        assert hq.building_type == "HQ"
        assert hq.offset == (0, 0)
        assert hq.hp == 200
        assert len(tpl.guards) == 1
        assert tpl.guards[0].role == "guard"
        assert tpl.guards[0].count == 2
        assert tpl.loot == {"Iron": 30, "Stone": 20}

    def test_malformed_template_skipped(self, data_dir):
        _write_yaml(
            os.path.join(data_dir, "definitions", "outposts.yaml"),
            {"outpost": _VALID_OUTPOSTS["outpost"], "broken": "not-a-dict"},
        )
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert "outpost" in reg.base_templates
        assert "broken" not in reg.base_templates

    def test_templates_swapped_on_reload(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert reg.base_templates == {}
        # Add templates on disk, then hot-reload picks them up.
        _write_yaml(
            os.path.join(data_dir, "definitions", "outposts.yaml"),
            _VALID_OUTPOSTS,
        )
        ok, errors = reg.reload_all()
        assert ok, errors
        assert "outpost" in reg.base_templates


# ------------------------------------------------------------------ #
#  Tests: player classes (state 3.2, optional classes.yaml)
# ------------------------------------------------------------------ #

_VALID_CLASSES = {
    "classes": [
        {"key": "vanguard", "name": "Vanguard", "description": "Front line."},
        {"key": "engineer", "name": "Engineer", "description": "Builder."},
    ],
}


class TestPlayerClasses:
    def test_absent_classes_yields_empty(self, data_dir):
        """No classes.yaml → empty class set (default fallback), no error."""
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert reg.classes == {}
        assert reg.get_class("vanguard") is None

    def test_classes_loaded_and_parsed(self, data_dir):
        _write_yaml(
            os.path.join(data_dir, "definitions", "classes.yaml"),
            _VALID_CLASSES,
        )
        reg = DataRegistry()
        reg.load_all(data_dir)
        cdef = reg.get_class("vanguard")
        assert cdef is not None
        assert cdef.name == "Vanguard"
        assert cdef.description == "Front line."
        assert set(reg.classes.keys()) == {"vanguard", "engineer"}

    def test_resolve_class_by_name_and_prefix(self, data_dir):
        _write_yaml(
            os.path.join(data_dir, "definitions", "classes.yaml"),
            _VALID_CLASSES,
        )
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert reg.resolve_class("Engineer").key == "engineer"
        assert reg.resolve_class("van").key == "vanguard"  # unambiguous prefix
        assert reg.resolve_class("zzz") is None

    def test_malformed_class_skipped(self, data_dir):
        _write_yaml(
            os.path.join(data_dir, "definitions", "classes.yaml"),
            {"classes": [{"key": "vanguard", "name": "Vanguard"},
                         {"name": "no-key-entry"}]},
        )
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert "vanguard" in reg.classes
        assert len(reg.classes) == 1  # the keyless entry was skipped

    def test_classes_swapped_on_reload(self, data_dir):
        reg = DataRegistry()
        reg.load_all(data_dir)
        assert reg.classes == {}
        _write_yaml(
            os.path.join(data_dir, "definitions", "classes.yaml"),
            _VALID_CLASSES,
        )
        ok, errors = reg.reload_all()
        assert ok, errors
        assert "vanguard" in reg.classes
