"""
Content-load + hot-reload tests for the real equipment/item definitions.

These tests load the REAL ``mygame/data`` definitions (not synthetic fixtures)
to prove the migrated + seeded equipment content (tasks 8.1-8.4) loads clean:
schema validation reports zero errors, cross-validation reports zero errors,
and the expected items / categories / weights / production map are present.

They also confirm the ``@reboot`` hot-reload path (DataRegistry.reload_all)
swaps the equipment content atomically: a valid reload fully applies the new
content, and a failed reload leaves the previous content wholly intact (no
partial state).

**Validates: Requirements 13.5, 13.6**
"""

import os
import shutil
import tempfile

import pytest
import yaml

from mygame.world.constants import (
    EQUIPMENT_SLOTS,
    GEAR_CATEGORIES,
    ITEM_CATEGORIES,
    SUPPLY_CATEGORIES,
    WEAPON_TYPES,
)
from mygame.world.data_registry import DataRegistry, DataRegistryError
from mygame.world.schema_validator import SchemaValidator


# ------------------------------------------------------------------ #
#  Locate the real data directory (mygame/data)
# ------------------------------------------------------------------ #
#  This file lives at mygame/world/tests/ ; the real definitions live at
#  mygame/data/ — two directories up, then into ``data``.
_REAL_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data")
)


# The full seeded/migrated item set authored in data/definitions/items.yaml.
EXPECTED_ITEM_KEYS = {
    # weapons (5)
    "combat_knife", "assault_rifle", "plasma_rifle", "sniper_rifle", "service_rifle",
    # armor (6)
    "combat_helmet", "kevlar_vest", "power_armor",
    "combat_gloves", "combat_greaves", "combat_boots",
    # accessories (3)
    "scope", "jetpack", "hauler_pack",
    # ammo (2)
    "rifle_rounds", "energy_cell",
    # consumables (2)
    "medkit", "combat_stim",
    # throwables / grenades (2)
    "frag_grenade", "plasma_grenade",
    # mines (2)
    "land_mine", "proximity_mine",
}


@pytest.fixture
def real_registry():
    """A DataRegistry loaded from the real mygame/data definitions."""
    reg = DataRegistry()
    reg.load_all(_REAL_DATA_DIR)
    return reg


# ================================================================== #
#  Requirement 13.5 — the migrated + seeded content loads clean
# ================================================================== #

class TestRealContentLoadsClean:
    """The real data loads with zero schema and zero cross-validation errors."""

    def test_real_data_dir_exists(self):
        # Guards against the path helper drifting if the tree is reorganised.
        assert os.path.isfile(os.path.join(_REAL_DATA_DIR, "definitions", "items.yaml"))

    def test_load_all_succeeds(self):
        """load_all() against the real definitions must not raise.

        load_all raises DataRegistryError on ANY schema or cross-validation
        error, so a clean return is itself proof of a valid content set.
        """
        reg = DataRegistry()
        reg.load_all(_REAL_DATA_DIR)  # would raise on any validation failure
        assert reg.items  # populated

    def test_zero_schema_errors_for_items(self):
        """validate_items reports no errors for the real items.yaml."""
        items_path = os.path.join(_REAL_DATA_DIR, "definitions", "items.yaml")
        with open(items_path, "r") as f:
            raw_items = yaml.safe_load(f)

        errors = SchemaValidator().validate_items(raw_items)
        assert errors == [], f"Schema errors in items.yaml: {errors}"

    def test_zero_cross_validation_errors(self, real_registry):
        """cross_validate over the fully-loaded real registry returns []."""
        errors = SchemaValidator().cross_validate(real_registry)
        assert errors == [], f"Cross-validation errors: {errors}"

    def test_all_expected_items_present(self, real_registry):
        """Every seeded/migrated item is present — no more, no less."""
        assert set(real_registry.items.keys()) == EXPECTED_ITEM_KEYS
        assert len(real_registry.items) == 22

    def test_every_item_has_valid_category(self, real_registry):
        for key, idef in real_registry.items.items():
            assert idef.category in ITEM_CATEGORIES, (
                f"item '{key}' has invalid category '{idef.category}'"
            )

    def test_every_item_has_a_weight(self, real_registry):
        """Every item declares a concrete, non-negative weight (Req 15.1)."""
        for key, idef in real_registry.items.items():
            assert isinstance(idef.weight, (int, float)), (
                f"item '{key}' weight is not numeric: {idef.weight!r}"
            )
            assert idef.weight >= 0, f"item '{key}' has negative weight {idef.weight}"

    def test_gear_items_have_valid_slots(self, real_registry):
        """Gear (armor/weapon/accessory) must sit in a canonical body slot."""
        for key, idef in real_registry.items.items():
            if idef.category in GEAR_CATEGORIES:
                assert idef.slot in EQUIPMENT_SLOTS, (
                    f"gear item '{key}' has slot '{idef.slot}' "
                    f"not in EQUIPMENT_SLOTS"
                )

    def test_weapons_declare_a_weapon_type(self, real_registry):
        for key, idef in real_registry.items.items():
            if idef.category == "weapon":
                assert idef.weapon_type in WEAPON_TYPES, (
                    f"weapon '{key}' has weapon_type '{idef.weapon_type}'"
                )

    def test_ranged_magazine_weapon_references_its_ammo(self, real_registry):
        """The magazine rifle points at a real ammo item (D5 reload reserve)."""
        service_rifle = real_registry.items["service_rifle"]
        assert service_rifle.weapon_type == "ranged"
        assert service_rifle.ammo_type == "rifle_rounds"
        assert service_rifle.magazine_size == 30

        ammo = real_registry.items[service_rifle.ammo_type]
        assert ammo.category == "ammo", (
            f"service_rifle ammo '{ammo.key}' is category '{ammo.category}', "
            f"expected 'ammo'"
        )

    def test_consumables_and_throwable_have_effects(self, real_registry):
        """Every consumable/throwable carries a usable effect block."""
        for key, idef in real_registry.items.items():
            if idef.category in ("consumable", "throwable"):
                assert idef.effect is not None, f"'{key}' has no effect"
                assert "type" in idef.effect, f"'{key}' effect lacks a type"

        # Spot-check the seeded effect shapes.
        assert real_registry.items["medkit"].effect["type"] == "heal"
        assert real_registry.items["combat_stim"].effect["type"] == "buff"
        assert real_registry.items["frag_grenade"].effect["type"] == "aoe_damage"

    def test_production_map_spans_ar_mb_lb(self, real_registry):
        """Production routes across Armory (AR), Medbay (MB), and Lab (LB)."""
        pmap = real_registry.item_production_map
        assert {"AR", "MB", "LB"} <= set(pmap.keys()), (
            f"production_map keys {set(pmap.keys())} miss AR/MB/LB"
        )

        # Every produced key names a real building and a real item.
        building_abbrs = set(real_registry.buildings.keys())
        produced = set()
        for abbr, keys in pmap.items():
            assert abbr in building_abbrs, f"production building '{abbr}' unknown"
            for k in keys:
                assert k in real_registry.items, (
                    f"production_map['{abbr}'] references unknown item '{k}'"
                )
                produced.add(k)

        # Supplies (ammo/consumable/throwable) and gear both get produced.
        supply_produced = {
            k for k in produced
            if real_registry.items[k].category in SUPPLY_CATEGORIES
        }
        gear_produced = {
            k for k in produced
            if real_registry.items[k].category in GEAR_CATEGORIES
        }
        assert supply_produced, "no supplies routed in production_map"
        assert gear_produced, "no gear routed in production_map"

    def test_freely_craftable_items_need_only_starter_planet_resources(self):
        """No-forward-dependency invariant for new players: every item with NO
        rank gate (required_rank is None) must be craftable from Terra-tier
        resources ONLY — the resources available on the default spawn planet.

        Otherwise a Recruit is shown a 'freely craftable' essential (medkit,
        frag grenade, land mine, ...) whose recipe needs a resource that only
        exists on a higher, rank-gated planet — an impossible craft. This guards
        the class of forward-dependency bug the re-map fixes (medkit once needed
        Energy, only on Forge). Starter resources are derived from the real
        terrain data so this stays correct if the planet resource map changes.
        """
        items_path = os.path.join(_REAL_DATA_DIR, "definitions", "items.yaml")
        terrain_path = os.path.join(_REAL_DATA_DIR, "definitions", "terrain.yaml")
        with open(items_path) as f:
            raw_items = yaml.safe_load(f)
        with open(terrain_path) as f:
            raw_terrain = yaml.safe_load(f)

        # The default spawn planet and its harvestable resources.
        starter_planet = "terra"
        starter_resources = {
            t["resource_type"]
            for t in raw_terrain["terrain"]
            if t.get("planet") == starter_planet and t.get("resource_type")
        }
        assert starter_resources, "no starter-planet resources found in terrain"

        offenders = []
        for it in raw_items["items"]:
            if it.get("required_rank") is not None:
                continue  # rank-gated items may need higher-planet resources
            for resource in (it.get("craft_cost") or {}):
                if resource not in starter_resources:
                    offenders.append(
                        f"{it['key']} needs '{resource}' (not on {starter_planet})"
                    )

        # All formerly-pending forward-dep items (energy_cell, combat_stim) are
        # now rank-gated (Staff_Sergeant) and skipped by the required_rank filter
        # above, so the allowed_pending set is retired.
        assert offenders == [], (
            "freely-craftable essentials require a non-starter-planet resource "
            f"(forward-dependency bug): {offenders}"
        )


# ================================================================== #
#  Requirement 13.6 — @reboot swaps content atomically
# ================================================================== #

def _copy_real_data(dst_root: str) -> None:
    """Copy the real definitions/config trees into a writable temp root."""
    shutil.copytree(
        os.path.join(_REAL_DATA_DIR, "definitions"),
        os.path.join(dst_root, "definitions"),
    )
    shutil.copytree(
        os.path.join(_REAL_DATA_DIR, "config"),
        os.path.join(dst_root, "config"),
    )


@pytest.fixture
def temp_real_data():
    """A writable copy of the real data tree (so reloads can mutate it)."""
    tmpdir = tempfile.mkdtemp()
    _copy_real_data(tmpdir)
    yield tmpdir
    shutil.rmtree(tmpdir)


class TestEquipmentContentHotReload:
    """The equipment content participates in the atomic hot-reload path."""

    def test_valid_reload_reloads_items_cleanly(self, temp_real_data):
        """A valid reload re-loads the full item set and applies edits."""
        reg = DataRegistry()
        reg.load_all(temp_real_data)
        assert set(reg.items.keys()) == EXPECTED_ITEM_KEYS

        # Retune a weight on disk (a realistic content edit), then reload.
        items_path = os.path.join(temp_real_data, "definitions", "items.yaml")
        with open(items_path, "r") as f:
            data = yaml.safe_load(f)
        for entry in data["items"]:
            if entry["key"] == "kevlar_vest":
                entry["weight"] = 12.5
                break
        with open(items_path, "w") as f:
            yaml.dump(data, f)

        success, errors = reg.reload_all()

        assert success is True, f"reload should succeed: {errors}"
        assert errors == []
        # Full item set still present — no items dropped by the swap.
        assert set(reg.items.keys()) == EXPECTED_ITEM_KEYS
        # New value applied.
        assert reg.items["kevlar_vest"].weight == 12.5

    def test_failed_reload_preserves_equipment_content(self, temp_real_data):
        """A broken items.yaml must leave the previous content fully intact."""
        reg = DataRegistry()
        reg.load_all(temp_real_data)

        before_keys = set(reg.items.keys())
        before_weight = reg.items["kevlar_vest"].weight
        before_service_ammo = reg.items["service_rifle"].ammo_type

        # Corrupt items.yaml with a schema-invalid category — reload must fail
        # WITHOUT partially applying anything.
        items_path = os.path.join(temp_real_data, "definitions", "items.yaml")
        with open(items_path, "r") as f:
            data = yaml.safe_load(f)
        data["items"][0]["category"] = "not_a_real_category"
        with open(items_path, "w") as f:
            yaml.dump(data, f)

        success, errors = reg.reload_all()

        assert success is False, "reload should fail on invalid category"
        assert errors
        # Atomic: the previously-loaded equipment content is unchanged.
        assert set(reg.items.keys()) == before_keys
        assert reg.items["kevlar_vest"].weight == before_weight
        assert reg.items["service_rifle"].ammo_type == before_service_ammo

    def test_reload_after_failure_recovers_cleanly(self, temp_real_data):
        """valid -> invalid -> valid: never leaves a mixed item set."""
        reg = DataRegistry()
        reg.load_all(temp_real_data)

        items_path = os.path.join(temp_real_data, "definitions", "items.yaml")

        # --- invalid reload (missing required file) ---
        os.remove(items_path)
        success, _ = reg.reload_all()
        assert success is False
        assert set(reg.items.keys()) == EXPECTED_ITEM_KEYS  # preserved

        # --- restore + valid reload ---
        _copy_real_data_items(items_path)
        success, errors = reg.reload_all()
        assert success is True, f"recovery reload should succeed: {errors}"
        assert set(reg.items.keys()) == EXPECTED_ITEM_KEYS


def _copy_real_data_items(dst_items_path: str) -> None:
    """Restore items.yaml from the real data tree into the temp copy."""
    shutil.copyfile(
        os.path.join(_REAL_DATA_DIR, "definitions", "items.yaml"),
        dst_items_path,
    )


class TestRealTurretCapability:
    """The REAL buildings.yaml wires the turret capability onto TU (and not HQ).

    This is the test that would have caught the original turret bug: unit tests
    hand-build a TU BuildingDef, so a misindented/omitted/misspelled
    ``capabilities: [turret]`` block in the real YAML would leave live turrets
    silently never firing while every unit test stayed green. Assert against the
    loaded real data directly.
    """

    def test_tu_has_turret_capability(self, real_registry):
        from mygame.world.constants import TURRET
        tu = real_registry.resolve_building("TU")
        assert tu is not None, "Turret (TU) must exist in the real data"
        assert tu.has_capability(TURRET), (
            "TU must carry the 'turret' capability or live turrets never fire"
        )

    def test_hq_does_not_have_turret_capability(self, real_registry):
        from mygame.world.constants import TURRET
        hq = real_registry.resolve_building("HQ")
        assert hq is not None
        assert not hq.has_capability(TURRET), (
            "HQ must NOT be turret-capable, else every HQ auto-fires"
        )

    def test_exactly_the_turret_is_turret_capable(self, real_registry):
        from mygame.world.constants import TURRET
        turret_caps = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(TURRET)
        ]
        assert turret_caps == ["TU"], (
            f"Exactly TU should be turret-capable, got {turret_caps}"
        )
