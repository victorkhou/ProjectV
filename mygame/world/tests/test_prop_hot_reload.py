"""
Property-based tests for hot-reload atomicity.

**Validates: Requirements 26.2, 26.3, 26.4**

Property 27: Hot-reload atomicity
- After a successful reload, all registry data reflects the new files.
- After a failed reload (corrupted/missing file), all registry data is
  unchanged from before the reload attempt.
- For any sequence of valid→invalid→valid reloads, the registry state is
  always consistent (either fully old or fully new, never a mix).
"""

import os
import shutil
import tempfile

import yaml
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mygame.world.data_registry import DataRegistry


# ------------------------------------------------------------------ #
#  Baseline valid YAML data (from test_data_registry.py fixtures)
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
#  Helpers
# ------------------------------------------------------------------ #

def _write_yaml(path: str, data) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


def _create_data_dir(balance_overrides: dict | None = None) -> str:
    """Create a temp directory with all valid YAML files.

    Returns the path to the temp directory (caller must clean up).
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

    balance = dict(VALID_BALANCE)
    if balance_overrides:
        balance.update(balance_overrides)
    _write_yaml(os.path.join(conf, "balance.yaml"), balance)

    return tmpdir


def _snapshot_registry(reg: DataRegistry) -> dict:
    """Capture a snapshot of all mutable registry state for comparison."""
    return {
        "building_keys": sorted(reg.buildings.keys()),
        "building_healths": {k: v.max_health for k, v in reg.buildings.items()},
        "item_keys": sorted(reg.items.keys()),
        "rank_names": [r.name for r in reg.ranks],
        "tech_keys": sorted(reg.technologies.keys()),
        "powerup_keys": sorted(reg.powerups.keys()),
        "terrain_keys": sorted(reg.terrain.keys()),
        "ability_gate_keys": sorted(reg.ability_gates.keys()),
        "planet_keys": sorted(reg.planets.keys()),
        "turret_damage": reg.balance.turret_damage,
        "turret_radius": reg.balance.turret_radius,
        "xp_kill": reg.balance.xp_kill,
        "gather_amount": reg.balance.gather_amount,
        "player_default_health": reg.balance.player_default_health,
    }


# ------------------------------------------------------------------ #
#  Hypothesis strategies for valid balance modifications
# ------------------------------------------------------------------ #

balance_modification = st.fixed_dictionaries({
    "turret_damage": st.integers(min_value=1, max_value=500),
    "turret_radius": st.integers(min_value=1, max_value=100),
    "xp_kill": st.integers(min_value=1, max_value=1000),
    "gather_amount": st.integers(min_value=1, max_value=100),
    "player_default_health": st.integers(min_value=1, max_value=1000),
})

building_health_modification = st.integers(min_value=1, max_value=5000)


# ================================================================== #
#  Property 27: Hot-reload atomicity
# ================================================================== #

class TestProperty27SuccessfulReloadUpdatesAll:
    """After a successful reload, all registry data reflects the new files.

    **Validates: Requirements 26.2, 26.3**
    """

    @given(new_balance=balance_modification)
    @settings(max_examples=30, deadline=10000)
    def test_successful_reload_updates_balance(self, new_balance):
        """After a valid reload, all balance values reflect the new files."""
        tmpdir = _create_data_dir()
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            # Write new balance values
            updated_balance = dict(VALID_BALANCE)
            updated_balance.update(new_balance)
            _write_yaml(os.path.join(tmpdir, "config", "balance.yaml"), updated_balance)

            success, errors = reg.reload_all()

            assert success is True, f"Reload should succeed but got errors: {errors}"
            assert reg.balance.turret_damage == new_balance["turret_damage"]
            assert reg.balance.turret_radius == new_balance["turret_radius"]
            assert reg.balance.xp_kill == new_balance["xp_kill"]
            assert reg.balance.gather_amount == new_balance["gather_amount"]
            assert reg.balance.player_default_health == new_balance["player_default_health"]
        finally:
            shutil.rmtree(tmpdir)

    @given(new_hq_health=building_health_modification)
    @settings(max_examples=30, deadline=10000)
    def test_successful_reload_updates_buildings(self, new_hq_health):
        """After a valid reload, building definitions reflect the new files."""
        tmpdir = _create_data_dir()
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            # Modify HQ max_health
            updated_buildings = [dict(b) for b in VALID_BUILDINGS]
            updated_buildings[0]["max_health"] = new_hq_health
            _write_yaml(
                os.path.join(tmpdir, "definitions", "buildings.yaml"),
                updated_buildings,
            )

            success, errors = reg.reload_all()

            assert success is True, f"Reload should succeed but got errors: {errors}"
            assert reg.buildings["HQ"].max_health == new_hq_health
        finally:
            shutil.rmtree(tmpdir)


class TestProperty27FailedReloadPreservesAll:
    """After a failed reload, all registry data is unchanged.

    **Validates: Requirements 26.2, 26.4**
    """

    @given(new_balance=balance_modification)
    @settings(max_examples=30, deadline=10000)
    def test_corrupted_file_preserves_all_data(self, new_balance):
        """Corrupting a required file after valid load preserves all state."""
        tmpdir = _create_data_dir(balance_overrides=new_balance)
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            snapshot_before = _snapshot_registry(reg)

            # Corrupt a required file (write content that causes YAML parse error)
            buildings_path = os.path.join(tmpdir, "definitions", "buildings.yaml")
            with open(buildings_path, "w") as f:
                f.write("\t\tinvalid yaml with tabs")
            success, errors = reg.reload_all()

            assert success is False, "Reload should fail on corrupted file"
            snapshot_after = _snapshot_registry(reg)
            assert snapshot_before == snapshot_after, (
                "Registry state must be unchanged after failed reload"
            )
        finally:
            shutil.rmtree(tmpdir)

    @given(new_balance=balance_modification)
    @settings(max_examples=30, deadline=10000)
    def test_missing_file_preserves_all_data(self, new_balance):
        """Removing a required file after valid load preserves all state."""
        tmpdir = _create_data_dir(balance_overrides=new_balance)
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            snapshot_before = _snapshot_registry(reg)

            # Remove a required file
            os.remove(os.path.join(tmpdir, "definitions", "ranks.yaml"))

            success, errors = reg.reload_all()

            assert success is False, "Reload should fail on missing file"
            snapshot_after = _snapshot_registry(reg)
            assert snapshot_before == snapshot_after, (
                "Registry state must be unchanged after failed reload"
            )
        finally:
            shutil.rmtree(tmpdir)

    @given(new_balance=balance_modification)
    @settings(max_examples=30, deadline=10000)
    def test_schema_invalid_file_preserves_all_data(self, new_balance):
        """Writing schema-invalid data after valid load preserves all state."""
        tmpdir = _create_data_dir(balance_overrides=new_balance)
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            snapshot_before = _snapshot_registry(reg)

            # Write schema-invalid buildings (missing required fields)
            bad_buildings = [{"name": "Bad"}]  # missing abbreviation, cost, etc.
            _write_yaml(
                os.path.join(tmpdir, "definitions", "buildings.yaml"),
                bad_buildings,
            )

            success, errors = reg.reload_all()

            assert success is False, "Reload should fail on invalid schema"
            snapshot_after = _snapshot_registry(reg)
            assert snapshot_before == snapshot_after, (
                "Registry state must be unchanged after failed reload"
            )
        finally:
            shutil.rmtree(tmpdir)


class TestProperty27ValidInvalidValidSequence:
    """For any sequence of valid→invalid→valid reloads, the registry state
    is always consistent (either fully old or fully new, never a mix).

    **Validates: Requirements 26.2, 26.3, 26.4**
    """

    @given(
        balance_v1=balance_modification,
        balance_v2=balance_modification,
        hq_health_v1=building_health_modification,
        hq_health_v2=building_health_modification,
    )
    @settings(max_examples=30, deadline=15000)
    def test_valid_invalid_valid_sequence_is_consistent(
        self, balance_v1, balance_v2, hq_health_v1, hq_health_v2
    ):
        """Reload sequence: initial → valid v1 → invalid → valid v2.

        After each step the registry is fully consistent — never a mix
        of v1 and v2 data.
        """
        # Ensure v1 and v2 differ so we can detect mixed state
        assume(balance_v1["turret_damage"] != balance_v2["turret_damage"])
        assume(hq_health_v1 != hq_health_v2)

        tmpdir = _create_data_dir()
        try:
            reg = DataRegistry()
            reg.load_all(tmpdir)

            # --- Step 1: valid reload with v1 values ---
            updated_balance_v1 = dict(VALID_BALANCE)
            updated_balance_v1.update(balance_v1)
            _write_yaml(os.path.join(tmpdir, "config", "balance.yaml"), updated_balance_v1)

            updated_buildings_v1 = [dict(b) for b in VALID_BUILDINGS]
            updated_buildings_v1[0]["max_health"] = hq_health_v1
            _write_yaml(
                os.path.join(tmpdir, "definitions", "buildings.yaml"),
                updated_buildings_v1,
            )

            success, errors = reg.reload_all()
            assert success is True, f"v1 reload should succeed: {errors}"

            # Verify v1 state is fully applied
            assert reg.balance.turret_damage == balance_v1["turret_damage"]
            assert reg.buildings["HQ"].max_health == hq_health_v1

            snapshot_v1 = _snapshot_registry(reg)

            # --- Step 2: invalid reload (remove a required file) ---
            os.remove(os.path.join(tmpdir, "definitions", "items.yaml"))

            success, errors = reg.reload_all()
            assert success is False, "Invalid reload should fail"

            # Registry must still be fully v1
            snapshot_after_fail = _snapshot_registry(reg)
            assert snapshot_v1 == snapshot_after_fail, (
                "After failed reload, registry must still be fully v1"
            )

            # --- Step 3: valid reload with v2 values ---
            # Recreate items file (was removed in step 2)
            _write_yaml(os.path.join(tmpdir, "definitions", "items.yaml"), VALID_ITEMS)

            updated_balance_v2 = dict(VALID_BALANCE)
            updated_balance_v2.update(balance_v2)
            _write_yaml(os.path.join(tmpdir, "config", "balance.yaml"), updated_balance_v2)

            updated_buildings_v2 = [dict(b) for b in VALID_BUILDINGS]
            updated_buildings_v2[0]["max_health"] = hq_health_v2
            _write_yaml(
                os.path.join(tmpdir, "definitions", "buildings.yaml"),
                updated_buildings_v2,
            )

            success, errors = reg.reload_all()
            assert success is True, f"v2 reload should succeed: {errors}"

            # Verify v2 state is fully applied — no v1 remnants
            assert reg.balance.turret_damage == balance_v2["turret_damage"]
            assert reg.balance.turret_radius == balance_v2["turret_radius"]
            assert reg.balance.xp_kill == balance_v2["xp_kill"]
            assert reg.buildings["HQ"].max_health == hq_health_v2

            # Structural integrity: all definition categories still present
            assert len(reg.buildings) == len(VALID_BUILDINGS)
            assert len(reg.items) == len(VALID_ITEMS["items"])
            assert len(reg.ranks) == len(VALID_RANKS)
            assert len(reg.technologies) == len(VALID_TECHNOLOGIES)
            assert len(reg.powerups) == len(VALID_POWERUPS)
        finally:
            shutil.rmtree(tmpdir)
