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
    # weapons (8)
    "combat_knife", "assault_rifle", "plasma_rifle", "sniper_rifle", "service_rifle",
    "incendiary_rifle", "psi_blade", "blast_launcher",
    # armor (6)
    "combat_helmet", "kevlar_vest", "power_armor",
    "combat_gloves", "combat_greaves", "combat_boots",
    # accessories (6)
    "scope", "jetpack", "hauler_pack",
    "thermal_cloak", "targeting_visor", "raider_talisman",
    # ammo (2)
    "rifle_rounds", "energy_cell",
    # fuel cells (2)
    "basic_fuel_cell", "premium_fuel_cell",
    # consumables (2)
    "medkit", "combat_stim",
    # throwables / grenades (2)
    "frag_grenade", "plasma_grenade",
    # mines (2)
    "land_mine", "proximity_mine",
    # inserts (4) — item-loot-economy task 4.2
    "venom_coating", "extended_barrel", "incendiary_core", "hollowpoint",
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
        assert len(real_registry.items) == 34

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


# ================================================================== #
#  item-loot-economy task 2.1 — real affixes.yaml loads clean
# ================================================================== #

class TestRealAffixesLoadClean:
    """The shipped affixes.yaml loads with zero errors and sane pools."""

    def test_real_affixes_file_exists(self):
        assert os.path.isfile(
            os.path.join(_REAL_DATA_DIR, "definitions", "affixes.yaml")
        )

    def test_zero_schema_errors_for_affixes(self):
        affix_path = os.path.join(_REAL_DATA_DIR, "definitions", "affixes.yaml")
        with open(affix_path, "r") as f:
            raw = yaml.safe_load(f)
        errors = SchemaValidator().validate_affixes(raw)
        assert errors == [], f"Schema errors in affixes.yaml: {errors}"

    def test_pools_accessible_via_registry(self, real_registry):
        """Both authored pools land on the registry for the affix draw (2.3)."""
        assert set(real_registry.affixes) == {"weapon", "armor"}
        assert real_registry.get_affix_pool("weapon")
        assert real_registry.get_affix_pool("armor")

    def test_every_authored_roll_spec_pool_is_defined(self, real_registry):
        """Every items.yaml roll_spec.affix_pool names a loaded pool, so the
        affix draw can never dead-end on a missing pool."""
        for key, idef in real_registry.items.items():
            spec = idef.roll_spec or {}
            pool = spec.get("affix_pool")
            if pool is not None:
                assert pool in real_registry.affixes, (
                    f"item '{key}' names affix_pool '{pool}' which is not "
                    f"defined in affixes.yaml"
                )

    def test_authored_affixes_use_known_axes(self, real_registry):
        """Every authored affix targets a known stat axis OR proc key
        (task 3.4 unlocked `range` + `proc: poison` alongside the Phase-2
        aggregating axes)."""
        from mygame.world.schema_validator import (
            AFFIX_PROC_KEYS,
            AFFIX_STAT_AXES,
        )

        for pool, entries in real_registry.affixes.items():
            for entry in entries:
                if "proc" in entry:
                    assert entry["proc"] in AFFIX_PROC_KEYS, (
                        f"affix '{entry['key']}' in pool '{pool}' declares "
                        f"unknown proc '{entry['proc']}'"
                    )
                    assert "stat" not in entry
                else:
                    assert entry["stat"] in AFFIX_STAT_AXES

    def test_authored_bands_within_design_range(self, real_registry):
        """Design §9: aggregating-axis magnitude bands are 2–6; `range` and
        proc bands are 1–3 (the spicy stats stay low + weighted rare)."""
        for pool, entries in real_registry.affixes.items():
            for entry in entries:
                spicy = "proc" in entry or entry.get("stat") == "range"
                lo_bound, hi_bound = (1, 3) if spicy else (2, 6)
                assert (
                    lo_bound <= entry["min"] <= entry["max"] <= hi_bound
                ), (
                    f"affix '{entry['key']}' band [{entry['min']}, "
                    f"{entry['max']}] outside the {lo_bound}-{hi_bound} "
                    f"design band"
                )

    def test_phase_three_affixes_authored(self, real_registry):
        """Task 3.4: `of Reach` (+range) and `of the Viper` (poison proc)
        exist in the weapon pool with the design §3.3 shapes."""
        weapon_pool = {e["key"]: e for e in real_registry.affixes["weapon"]}

        reach = weapon_pool["long"]
        assert reach["name"] == "of Reach"
        assert reach["stat"] == "range"
        assert (reach["min"], reach["max"]) == (1, 3)

        viper = weapon_pool["venomous"]
        assert viper["name"] == "of the Viper"
        assert viper["proc"] == "poison"
        assert "stat" not in viper
        assert (viper["min"], viper["max"]) == (1, 3)

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


# ================================================================== #
#  Item-loot-economy task 0.1 (R10.7) — typed weapons are obtainable
# ================================================================== #

# The shipped typed-damage weapons: defined with a craft_cost but (before the
# R10.7 fix) absent from every production_map catalog, so gate 3
# (``wrong_building``) fired in every building and they were uncraftable.
TYPED_WEAPON_KEYS = ("incendiary_rifle", "psi_blade", "blast_launcher")


class _AttrBag:
    """A tiny attribute bag standing in for an Evennia ``.db`` proxy."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _CraftPlayer:
    """Minimal crafting player: max level (passes every rank gate) + rich
    resource pool (passes the resource gate), so the tests below exercise
    the right-building gate against the REAL production_map."""

    def __init__(self):
        self.key = "Crafter"
        self.db = _AttrBag(level=100, resources={
            r: 10_000 for r in (
                "Iron", "Wood", "Stone", "Energy", "Circuits",
                "Magmite", "Aether", "Cryogen", "Biomass",
            )
        })

    def get_resource(self, resource):
        return int(self.db.resources.get(str(resource).title(), 0))

    def has_resources(self, costs):
        return all(
            self.db.resources.get(str(r).title(), 0) >= amt
            for r, amt in costs.items()
        )

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            key = str(r).title()
            self.db.resources[key] = self.db.resources.get(key, 0) - int(amt)
        return True

    def add_resource(self, resource, amount):
        key = str(resource).title()
        self.db.resources[key] = self.db.resources.get(key, 0) + int(amount)


class _CraftBuilding:
    """Minimal equipment building (owned, online, not under construction)."""

    def __init__(self, building_type, owner):
        self.key = building_type
        self.db = _AttrBag(building_type=building_type,
                           under_construction=False)
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return False


class TestTypedWeaponsCraftable:
    """The shipped typed weapons are routed in the REAL production_map and
    craft end-to-end at the Lab (item-loot-economy task 0.1 / R10.7).

    All three carry ``classification: futuristic``, so they route to the Lab
    (LB) per the catalog's routing rule (design §7 blesses "LB for all three
    as futuristic").

    **Validates: Requirements 10.6**
    """

    def test_typed_weapons_in_lab_catalog(self, real_registry):
        """Each typed weapon appears in the LB production catalog."""
        lb_catalog = set(real_registry.item_production_map.get("LB", []))
        for key in TYPED_WEAPON_KEYS:
            assert key in lb_catalog, (
                f"typed weapon '{key}' missing from the LB production_map "
                f"catalog — it is uncraftable (R10.7)"
            )

    def test_typed_weapons_produced_by_exactly_one_building(self, real_registry):
        """Catalog invariant: each item is produced by exactly one building."""
        for key in TYPED_WEAPON_KEYS:
            producers = [
                abbr for abbr, keys in real_registry.item_production_map.items()
                if key in keys
            ]
            assert producers == ["LB"], (
                f"typed weapon '{key}' should be produced by exactly LB, "
                f"got {producers}"
            )

    @pytest.mark.parametrize("weapon_key", TYPED_WEAPON_KEYS)
    def test_typed_weapon_crafts_end_to_end_in_lab(self, real_registry,
                                                   weapon_key):
        """`craft` succeeds at the Lab — gate 3 ``wrong_building`` no longer
        fires — and the crafted weapon lands in the player's inventory."""
        from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION
        from mygame.world.systems.equipment_system import EquipmentSystem

        event_bus = EventBus()
        notifications = []
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda event_name=None, player=None, kind=None, data=None,
            **_extra: notifications.append((kind, data or {})),
        )
        created = []
        system = EquipmentSystem(
            real_registry, event_bus,
            create_item_func=lambda idef, owner: created.append(idef.key),
        )
        player = _CraftPlayer()
        lab = _CraftBuilding("LB", owner=player)

        assert system.craft(player, weapon_key, lab) is True

        # The crafted gear item was created and the success notification fired.
        assert created == [weapon_key]
        kinds = [k for k, _ in notifications]
        assert "crafted" in kinds
        assert "craft_failed" not in kinds, (
            f"craft_failed fired: {notifications}"
        )

        # The craft_cost was actually deducted (end-to-end resource spend).
        craft_cost = real_registry.items[weapon_key].craft_cost
        for resource, amount in craft_cost.items():
            assert player.get_resource(resource) == 10_000 - amount

    @pytest.mark.parametrize("weapon_key", TYPED_WEAPON_KEYS)
    def test_typed_weapon_still_wrong_building_at_armory(self, real_registry,
                                                         weapon_key):
        """The weapons route to LB only — crafting at the Armory still fails
        with ``wrong_building`` (no accidental dual-routing)."""
        from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION
        from mygame.world.systems.equipment_system import EquipmentSystem

        event_bus = EventBus()
        notifications = []
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda event_name=None, player=None, kind=None, data=None,
            **_extra: notifications.append((kind, data or {})),
        )
        system = EquipmentSystem(real_registry, event_bus)
        player = _CraftPlayer()
        armory = _CraftBuilding("AR", owner=player)

        assert system.craft(player, weapon_key, armory) is False
        kind, data = notifications[-1]
        assert kind == "craft_failed"
        assert data.get("reason") == "wrong_building"


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


#: Every bomb in the game — the Munitions Plant's exclusive catalog.
BOMB_ITEM_KEYS = ("frag_grenade", "plasma_grenade", "land_mine",
                  "proximity_mine")


class TestMunitionsPlantIsTheBombWorks:
    """The Munitions Plant (MP) is the sole producer of every bomb.

    Same class of guard as the turret capability above: the routing lives
    entirely in ``items.yaml``'s ``production_map`` plus
    ``EQUIPMENT_BUILDING_TYPES``, so a data edit that stranded the bombs (or
    left them producible at two buildings) would make explosives uncraftable —
    or double-routed — while every unit test stayed green.
    """

    def test_mp_exists_as_a_production_building(self, real_registry):
        from mygame.world.systems.equipment_system import (
            EQUIPMENT_BUILDING_TYPES,
        )
        mp = real_registry.resolve_building("MP")
        assert mp is not None, "Munitions Plant (MP) must exist in the real data"
        assert "MP" in EQUIPMENT_BUILDING_TYPES, (
            "MP must be an equipment building or `craft` refuses every bomb "
            "with wrong_building and passive production never runs"
        )

    def test_mp_catalog_is_exactly_the_bombs(self, real_registry):
        catalog = set(real_registry.item_production_map.get("MP", []))
        assert catalog == set(BOMB_ITEM_KEYS), (
            f"MP should produce exactly the bombs, got {sorted(catalog)}"
        )

    def test_each_bomb_is_produced_only_by_mp(self, real_registry):
        for key in BOMB_ITEM_KEYS:
            producers = [
                abbr for abbr, keys in real_registry.item_production_map.items()
                if key in keys
            ]
            assert producers == ["MP"], (
                f"bomb '{key}' must be produced by MP alone, got {producers}"
            )

    def test_bombs_are_craftable_at_the_plant(self, real_registry):
        """Gate 3 (wrong_building) must pass for a bomb at the MP."""
        for key in BOMB_ITEM_KEYS:
            idef = real_registry.items[key]
            assert idef.craft_cost, f"bomb '{key}' needs a craft_cost"
            catalog = {
                d.key for d in real_registry.get_items_for_building("MP")
            }
            assert key in catalog

    def test_ungated_bombs_reachable_before_the_lab(self, real_registry):
        """The freely-craftable bombs must not sit behind a later gate.

        ``frag_grenade`` and ``land_mine`` are authored as Terra-craftable
        equalizers with no ``required_rank``; the building that makes them has
        to unlock no later than they do, and with no deed gate.
        """
        mp = real_registry.resolve_building("MP")
        lab = real_registry.resolve_building("LB")
        assert mp.unlock_deed is None, (
            "the bomb works must not be deed-gated — its two headline bombs "
            "are rank-free starter equipment"
        )
        assert mp.rank_requirement < lab.rank_requirement, (
            "MP should unlock before the Lab it took the bombs from"
        )
        for key in ("frag_grenade", "land_mine"):
            assert real_registry.items[key].required_rank is None


class TestSurveyArrayCapability:
    """The Survey Array (SA) must carry the ``outpost_survey`` capability.

    Same guard rationale as the turret above: ``survey`` locates its bench by
    CAPABILITY, so a dropped ``capabilities:`` block in the real YAML would make
    every survey action report ``wrong_building`` while unit tests using fake
    definitions stayed green.
    """

    def test_sa_has_the_survey_capability(self, real_registry):
        from mygame.world.constants import OUTPOST_SURVEY
        sa = real_registry.resolve_building("SA")
        assert sa is not None, "Survey Array (SA) must exist in the real data"
        assert sa.has_capability(OUTPOST_SURVEY), (
            "SA must carry 'outpost_survey' or the survey bench is unreachable"
        )

    def test_exactly_the_array_is_survey_capable(self, real_registry):
        from mygame.world.constants import OUTPOST_SURVEY
        capable = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(OUTPOST_SURVEY)
        ]
        assert capable == ["SA"], (
            f"Exactly SA should be survey-capable, got {capable}"
        )

    def test_sa_is_upgradable_because_level_scales_precision(self, real_registry):
        from mygame.world.constants import UPGRADABLE
        sa = real_registry.resolve_building("SA")
        assert sa.has_capability(UPGRADABLE), (
            "the array's level tightens the opening search box, so it must be "
            "upgradable or that scaling is unreachable"
        )

    def test_sa_is_not_a_production_building(self, real_registry):
        """The array is a bench, not a producer — it must have no catalog."""
        from mygame.world.systems.equipment_system import (
            EQUIPMENT_BUILDING_TYPES,
        )
        assert "SA" not in EQUIPMENT_BUILDING_TYPES
        assert "SA" not in real_registry.item_production_map


# ================================================================== #
#  Item-loot-economy task 0.3 — accessory gear on aggregating axes
# ================================================================== #

# The new accessory-style gear pieces proving the "free" aggregation path:
# stats carried in ``stat_modifiers`` on the aggregating axes
# (``damage_bonus`` / ``<type>_resist``) flow into combat with ZERO engine
# changes, because EquipmentHandler.get_stat_total sums every equipped item
# with no key allowlist.
ACCESSORY_GEAR_KEYS = ("thermal_cloak", "targeting_visor", "raider_talisman")

# The aggregating axes these items are allowed to carry (design §2 / R3.5).
_AGGREGATING_AXES = {"damage_bonus", "fire_resist"}


class _GearWearer:
    """Minimal combat-entity-shaped character carrying a REAL EquipmentHandler.

    ``db.combat_xp`` is set so ``world.utils.is_player`` recognizes it (the
    gate inside ``CombatEngine._get_target_typed_resist``); the handler falls
    back to the plain ``_equipment_slots`` dict since there is no Evennia
    Attribute handler here.
    """

    def __init__(self, name="Wearer"):
        self.key = name
        self.db = _AttrBag(combat_xp=0, active_powerups={})
        from mygame.world.systems.equipment_handler import EquipmentHandler
        self.equipment = EquipmentHandler(self)


def _make_combat_engine(registry):
    """A CombatEngine over the REAL registry (real balance config)."""
    from mygame.world.event_bus import EventBus
    from mygame.world.systems.combat_engine import CombatEngine
    return CombatEngine(
        registry=registry,
        event_bus=EventBus(),
        current_tick_func=lambda: 0,
    )


class TestAccessoryGearDataOnAggregatingAxes:
    """The task 0.3 accessory items load with the right shape and routing.

    **Validates: Requirements 3.5**
    """

    def test_accessories_defined_with_aggregating_axis_stats(self, real_registry):
        for key in ACCESSORY_GEAR_KEYS:
            idef = real_registry.items[key]
            assert idef.category == "accessory", (
                f"'{key}' should be category 'accessory', got '{idef.category}'"
            )
            assert idef.slot in EQUIPMENT_SLOTS, (
                f"'{key}' slot '{idef.slot}' not in EQUIPMENT_SLOTS"
            )
            assert idef.stat_modifiers, f"'{key}' carries no stat_modifiers"
            assert set(idef.stat_modifiers) <= _AGGREGATING_AXES, (
                f"'{key}' carries non-aggregating-axis stats: "
                f"{set(idef.stat_modifiers) - _AGGREGATING_AXES}"
            )

    def test_expected_stat_values(self, real_registry):
        items = real_registry.items
        assert items["thermal_cloak"].stat_modifiers == {"fire_resist": 4}
        assert items["targeting_visor"].stat_modifiers == {"damage_bonus": 2}
        assert items["raider_talisman"].stat_modifiers == {
            "damage_bonus": 1, "fire_resist": 2,
        }

    def test_accessories_routed_by_exactly_one_building(self, real_registry):
        """Each new accessory is obtainable: routed in exactly one catalog,
        matching its classification (modern → AR, futuristic → LB)."""
        pmap = real_registry.item_production_map
        expected_producer = {
            "thermal_cloak": "AR",
            "targeting_visor": "AR",
            "raider_talisman": "LB",
        }
        for key, expected in expected_producer.items():
            producers = [abbr for abbr, keys in pmap.items() if key in keys]
            assert producers == [expected], (
                f"accessory '{key}' should be produced by exactly "
                f"[{expected}], got {producers}"
            )


class TestAccessoryGearAggregatesIntoCombat:
    """Equipping the new gear feeds get_stat_total and the combat reads.

    Proves the "free" path end-to-end on the REAL definitions: equip →
    ``EquipmentHandler.get_stat_total`` sums the stats → the existing combat
    consumers (``_get_target_typed_resist`` for ``fire_resist``,
    ``_get_attacker_bonus`` for ``damage_bonus``) reflect them with no
    engine change.

    **Validates: Requirements 3.5**
    """

    def test_get_stat_total_picks_up_fire_resist(self, real_registry):
        wearer = _GearWearer()
        ok, _ = wearer.equipment.equip(real_registry.items["thermal_cloak"])
        assert ok
        assert wearer.equipment.get_stat_total("fire_resist") == 4.0

        # Different slots stack: back cloak + accessory talisman.
        ok, _ = wearer.equipment.equip(real_registry.items["raider_talisman"])
        assert ok
        assert wearer.equipment.get_stat_total("fire_resist") == 6.0

    def test_get_stat_total_picks_up_damage_bonus(self, real_registry):
        wearer = _GearWearer()
        wearer.equipment.equip(real_registry.items["targeting_visor"])
        wearer.equipment.equip(real_registry.items["raider_talisman"])
        assert wearer.equipment.get_stat_total("damage_bonus") == 3.0

    def test_combat_typed_resist_reads_accessory_fire_resist(self, real_registry):
        """_get_target_typed_resist builds `fire_resist` and sums the gear."""
        engine = _make_combat_engine(real_registry)
        target = _GearWearer("Target")
        target.equipment.equip(real_registry.items["thermal_cloak"])
        target.equipment.equip(real_registry.items["raider_talisman"])

        baseline = float(getattr(real_registry.balance, "baseline_resist", 0) or 0)
        resist = engine._get_target_typed_resist(target, "fire")
        assert resist == baseline + 6.0

        # The gear resists fire only — psychic sees just the baseline.
        assert engine._get_target_typed_resist(target, "psychic") == baseline

    def test_combat_attacker_bonus_reads_accessory_damage_bonus(self, real_registry):
        """_get_attacker_bonus aggregates gear damage_bonus (uncapped gear term)."""
        engine = _make_combat_engine(real_registry)
        attacker = _GearWearer("Attacker")
        attacker.equipment.equip(real_registry.items["targeting_visor"])
        attacker.equipment.equip(real_registry.items["raider_talisman"])

        # No tech/powerup/alliance/class terms on the bare wearer: the whole
        # bonus is the aggregated gear damage_bonus (2 + 1).
        assert engine._get_attacker_bonus(attacker) == 3.0


# ================================================================== #
#  Item-loot-economy task 1.7 — roll_spec authored for core weapons/armor
# ================================================================== #

# Every core weapon and armor piece carries a roll_spec (design §9). The
# schema shape itself is enforced at load (test_load_all_succeeds /
# test_zero_schema_errors_for_items above would fail on any invalid spec);
# these tests assert the CALIBRATION rules on the authored numbers.
ROLLED_WEAPON_KEYS = (
    "combat_knife", "assault_rifle", "plasma_rifle", "sniper_rifle",
    "service_rifle", "incendiary_rifle", "psi_blade", "blast_launcher",
)
ROLLED_ARMOR_KEYS = (
    "combat_helmet", "kevlar_vest", "power_armor",
    "combat_gloves", "combat_greaves", "combat_boots",
)

# Categories that must stay unrolled (R1.3): ammo (incl. fuel cells),
# consumables, throwables, mines, inserts.
_UNROLLED_CATEGORIES = {"ammo", "consumable", "throwable", "mine", "insert"}


class TestCoreGearRollSpecs:
    """The authored roll_spec content follows the design §9 calibration.

    **Validates: Requirements 1.1, 1.3, 6.1**
    """

    def test_all_core_weapons_and_armor_have_roll_specs(self, real_registry):
        for key in ROLLED_WEAPON_KEYS + ROLLED_ARMOR_KEYS:
            idef = real_registry.items[key]
            assert isinstance(idef.roll_spec, dict), (
                f"core gear '{key}' has no roll_spec"
            )
            assert idef.roll_spec.get("stats"), (
                f"'{key}' roll_spec has no stats bands"
            )

    def test_unrolled_categories_stay_unrolled(self, real_registry):
        """Ammo, fuel, consumables, throwables, mines never roll (R1.3)."""
        for key, idef in real_registry.items.items():
            if idef.category in _UNROLLED_CATEGORIES:
                assert idef.roll_spec is None, (
                    f"'{key}' (category '{idef.category}') must not carry a "
                    f"roll_spec (R1.3)"
                )

    def test_base_value_sits_strictly_inside_every_loot_band(self, real_registry):
        """§9: today's flat base is a 'good but not great' roll — it lies
        STRICTLY inside the loot band, so every rolled stat has both downside
        (a worse-than-today roll exists) and upside (a god-roll exists).
        Matches the design §1.1 worked example (assault_rifle: 25 in [18,30],
        5 in [4,7])."""
        for key in ROLLED_WEAPON_KEYS + ROLLED_ARMOR_KEYS:
            idef = real_registry.items[key]
            for stat, band in idef.roll_spec["stats"].items():
                base = idef.stat_modifiers.get(stat)
                assert base is not None, (
                    f"'{key}' rolls '{stat}' but has no base value for it"
                )
                assert band["min"] < base < band["max"], (
                    f"'{key}' base {stat}={base} not strictly inside loot "
                    f"band [{band['min']}, {band['max']}]"
                )

    def test_craft_band_contained_in_loot_band_and_tops_out_at_base(
            self, real_registry):
        """§9/R6.1: craft band ⊂ loot band; craft max == base (a crafted item
        never exceeds a good loot roll)."""
        for key in ROLLED_WEAPON_KEYS + ROLLED_ARMOR_KEYS:
            idef = real_registry.items[key]
            craft = idef.roll_spec.get("craft")
            assert craft, f"core gear '{key}' has no craft band"
            for stat, cband in craft.items():
                lband = idef.roll_spec["stats"][stat]
                assert lband["min"] <= cband["min"] <= cband["max"] <= lband["max"], (
                    f"'{key}' craft band for '{stat}' "
                    f"[{cband['min']}, {cband['max']}] not contained in loot "
                    f"band [{lband['min']}, {lband['max']}]"
                )
                base = idef.stat_modifiers[stat]
                assert cband["max"] == base, (
                    f"'{key}' craft max for '{stat}' is {cband['max']}, "
                    f"expected the base value {base}"
                )

    def test_weapons_weight_damage_over_range(self, real_registry):
        """§2.1: damage is the stat that matters on a weapon — its IQS weight
        strictly exceeds range's wherever both roll."""
        for key in ROLLED_WEAPON_KEYS:
            stats = real_registry.items[key].roll_spec["stats"]
            if "range" in stats:
                assert stats["damage"]["weight"] > stats["range"]["weight"], (
                    f"weapon '{key}' must weight damage above range"
                )

    def test_melee_weapons_do_not_roll_range(self, real_registry):
        """Combat forces melee to range 1 — rolling it would be dead data."""
        for key in ROLLED_WEAPON_KEYS:
            idef = real_registry.items[key]
            if idef.weapon_type == "melee":
                assert "range" not in idef.roll_spec["stats"], (
                    f"melee weapon '{key}' must not roll range"
                )

    def test_affix_pool_matches_category(self, real_registry):
        """Weapons draw from the 'weapon' pool, armor from 'armor' (Phase 2)."""
        for key in ROLLED_WEAPON_KEYS:
            pool = real_registry.items[key].roll_spec.get("affix_pool")
            assert pool == "weapon", f"weapon '{key}' affix_pool is {pool!r}"
        for key in ROLLED_ARMOR_KEYS:
            pool = real_registry.items[key].roll_spec.get("affix_pool")
            assert pool == "armor", f"armor '{key}' affix_pool is {pool!r}"


# ================================================================== #
#  Item-loot-economy task 4.1 — Blacksmith (BS) building + capability
# ================================================================== #

class TestRealBlacksmithDef:
    """The REAL buildings.yaml ships the Blacksmith bench (design §4.1/§7).

    Follows the TestRealTurretCapability pattern: assert against the loaded
    real data, so a misindented/omitted ``capabilities: [blacksmith]`` block
    in the YAML can't leave the bench unlocatable while unit tests stay green.

    **Validates: Requirements 4.1**
    """

    def test_bs_loads_with_bench_fields(self, real_registry):
        bs = real_registry.resolve_building("BS")
        assert bs is not None, "Blacksmith (BS) must exist in the real data"
        assert bs.name == "Blacksmith"
        assert bs.category == "equipment"
        assert bs.requires_hq is True
        # R4.4 deviation (documented in buildings.yaml, F4 review fix): bench
        # usage is gated on operational status only — NO code consumes
        # requires_agent, so declaring true was dead data falsely advertising
        # an agent gate. Flip both when a real consumer lands.
        assert bs.requires_agent is False
        assert bs.max_level == 5

    def test_bs_mirrors_lab_mid_tier_gate(self, real_registry):
        """Design §4.1: mid-tier rank gate 'e.g. level ~11 like Lab' — the BS
        mirrors the Lab's rank_requirement + deed gate exactly."""
        bs = real_registry.resolve_building("BS")
        lab = real_registry.resolve_building("LB")
        assert bs.rank_requirement == lab.rank_requirement == 11
        assert bs.unlock_deed == lab.unlock_deed
        assert bs.unlock_deed_count == lab.unlock_deed_count

    def test_bs_has_blacksmith_capability(self, real_registry):
        from mygame.world.constants import BLACKSMITH
        bs = real_registry.resolve_building("BS")
        assert bs.has_capability(BLACKSMITH), (
            "BS must carry the 'blacksmith' capability or the bench commands "
            "can never locate it"
        )

    def test_exactly_bs_is_blacksmith_capable(self, real_registry):
        from mygame.world.constants import BLACKSMITH
        bench_caps = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(BLACKSMITH)
        ]
        assert bench_caps == ["BS"], (
            f"Exactly BS should be blacksmith-capable, got {bench_caps}"
        )

    def test_blacksmith_in_building_capabilities_vocabulary(self):
        """The constant is registered in the controlled vocabulary, so the
        schema validator accepts the YAML (typos fail the load)."""
        from mygame.world.constants import BLACKSMITH, BUILDING_CAPABILITIES
        assert BLACKSMITH == "blacksmith"
        assert BLACKSMITH in BUILDING_CAPABILITIES

    def test_bs_is_a_pure_bench_not_a_production_building(self, real_registry):
        """Design decision (§4.1): the Blacksmith does NOT produce items — it
        is absent from EQUIPMENT_BUILDING_TYPES and from every production_map
        catalog, so passive production and `craft` can never route to it."""
        from mygame.world.systems.equipment_system import (
            EQUIPMENT_BUILDING_TYPES,
        )
        assert "BS" not in EQUIPMENT_BUILDING_TYPES
        assert real_registry.resolve_building("BS").produces is None
        assert "BS" not in real_registry.item_production_map, (
            "BS must not carry a production_map catalog (pure bench)"
        )


# ================================================================== #
#  Item-loot-economy task 5.3 — Refinery (RF) building + capability
# ================================================================== #

class TestRealRefineryDef:
    """The REAL buildings.yaml ships the Refinery — the Nexium sink (§7).

    Follows the TestRealBlacksmithDef pattern: assert against the loaded
    real data, so a misindented/omitted ``capabilities: [resource_converter]``
    block in the YAML can't leave the converter unlocatable while unit
    tests stay green.

    **Validates: Requirements 10.4, 10.5**
    """

    def test_rf_loads_with_converter_fields(self, real_registry):
        rf = real_registry.resolve_building("RF")
        assert rf is not None, "Refinery (RF) must exist in the real data"
        assert rf.name == "Refinery"
        assert rf.category == "economy"
        assert rf.requires_hq is True
        # R4.4 deviation (documented in buildings.yaml, F4 review fix): the
        # refine bench gates on operational status only — requires_agent has
        # no consumer, so true was a false advertisement (see the BS twin).
        assert rf.requires_agent is False
        assert rf.max_level == 5

    def test_rf_is_later_game_than_the_blacksmith(self, real_registry):
        """Nexium is late-game, so its sink gates later than the bench
        (rank 13 + a fortress deed vs the BS's 11 + outposts)."""
        rf = real_registry.resolve_building("RF")
        bs = real_registry.resolve_building("BS")
        assert rf.rank_requirement > bs.rank_requirement
        assert rf.unlock_deed == "fortress_cleared"

    def test_rf_has_resource_converter_capability(self, real_registry):
        from mygame.world.constants import RESOURCE_CONVERTER
        rf = real_registry.resolve_building("RF")
        assert rf.has_capability(RESOURCE_CONVERTER), (
            "RF must carry the 'resource_converter' capability or the "
            "refine command can never locate it"
        )

    def test_exactly_rf_is_converter_capable(self, real_registry):
        from mygame.world.constants import RESOURCE_CONVERTER
        converter_caps = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(RESOURCE_CONVERTER)
        ]
        assert converter_caps == ["RF"], (
            f"Exactly RF should be converter-capable, got {converter_caps}"
        )

    def test_resource_converter_in_capabilities_vocabulary(self):
        """The constant is registered in the controlled vocabulary, so the
        schema validator accepts the YAML (typos fail the load)."""
        from mygame.world.constants import (
            RESOURCE_CONVERTER,
            BUILDING_CAPABILITIES,
        )
        assert RESOURCE_CONVERTER == "resource_converter"
        assert RESOURCE_CONVERTER in BUILDING_CAPABILITIES

    def test_rf_is_a_converter_not_a_production_building(self, real_registry):
        """The Refinery consumes resources — it never PRODUCES anything
        (R10.4 anti-loop): absent from EQUIPMENT_BUILDING_TYPES, no
        produces, no production_map catalog."""
        from mygame.world.systems.equipment_system import (
            EQUIPMENT_BUILDING_TYPES,
        )
        assert "RF" not in EQUIPMENT_BUILDING_TYPES
        assert real_registry.resolve_building("RF").produces is None
        assert "RF" not in real_registry.item_production_map, (
            "RF must not carry a production_map catalog (pure converter)"
        )


# ================================================================== #
#  Item-loot-economy task 6.1 — Sniper Nest (SN) building + capability
# ================================================================== #

class TestRealSniperNestDef:
    """The REAL buildings.yaml ships the Sniper Nest range aura (§7, R10.1).

    Follows the TestRealBlacksmithDef pattern: assert against the loaded
    real data, so a misindented/omitted ``capabilities: [range_aura]``
    block in the YAML can't leave the aura inert while unit tests stay
    green.

    **Validates: Requirements 10.1**
    """

    def test_sn_loads_with_defense_fields(self, real_registry):
        sn = real_registry.resolve_building("SN")
        assert sn is not None, "Sniper Nest (SN) must exist in the real data"
        assert sn.name == "Sniper Nest"
        assert sn.category == "defense"
        assert sn.requires_hq is True
        assert sn.max_level == 5

    def test_sn_gates_later_than_the_turret(self, real_registry):
        """+range is the spiciest stat (no chip-floor analog), so the nest
        gates a step past the basic defense building."""
        sn = real_registry.resolve_building("SN")
        tu = real_registry.resolve_building("TU")
        assert sn.rank_requirement > tu.rank_requirement

    def test_sn_has_range_aura_capability(self, real_registry):
        from mygame.world.constants import RANGE_AURA
        sn = real_registry.resolve_building("SN")
        assert sn.has_capability(RANGE_AURA), (
            "SN must carry the 'range_aura' capability or "
            "_tile_range_bonus can never grant its bonus"
        )

    def test_sn_is_upgradable(self, real_registry):
        """Level scaling (+1..+3) is the point of the building, so it must
        be upgradable."""
        from mygame.world.constants import UPGRADABLE
        sn = real_registry.resolve_building("SN")
        assert sn.has_capability(UPGRADABLE)

    def test_exactly_sn_is_range_aura_capable(self, real_registry):
        from mygame.world.constants import RANGE_AURA
        aura_caps = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(RANGE_AURA)
        ]
        assert aura_caps == ["SN"], (
            f"Exactly SN should be range_aura-capable, got {aura_caps}"
        )

    def test_range_aura_in_capabilities_vocabulary(self):
        """The constant is registered in the controlled vocabulary, so the
        schema validator accepts the YAML (typos fail the load)."""
        from mygame.world.constants import RANGE_AURA, BUILDING_CAPABILITIES
        assert RANGE_AURA == "range_aura"
        assert RANGE_AURA in BUILDING_CAPABILITIES


# ================================================================== #
#  Item-loot-economy task 6.2 — Watchtower (WT) building + capability
# ================================================================== #

class TestRealWatchtowerDef:
    """The REAL buildings.yaml ships the Watchtower vision aura (§7, R10.2).

    Follows the TestRealSniperNestDef pattern: assert against the loaded
    real data, so a misindented/omitted ``capabilities: [vision_aura]``
    block in the YAML can't leave the aura inert while unit tests stay
    green.

    **Validates: Requirements 10.2**
    """

    def test_wt_loads_with_intelligence_fields(self, real_registry):
        wt = real_registry.resolve_building("WT")
        assert wt is not None, "Watchtower (WT) must exist in the real data"
        assert wt.name == "Watchtower"
        assert wt.category == "intelligence"
        assert wt.requires_hq is True
        assert wt.requires_agent is False, (
            "no agent required — the player mans the tower themselves"
        )
        assert wt.max_level == 5

    def test_wt_has_vision_aura_capability(self, real_registry):
        from mygame.world.constants import VISION_AURA
        wt = real_registry.resolve_building("WT")
        assert wt.has_capability(VISION_AURA), (
            "WT must carry the 'vision_aura' capability or "
            "_tile_vision_bonus can never grant its bonus"
        )

    def test_wt_is_upgradable(self, real_registry):
        """Level scaling (+1..+3 sight_range) is the point of the building,
        so it must be upgradable."""
        from mygame.world.constants import UPGRADABLE
        wt = real_registry.resolve_building("WT")
        assert wt.has_capability(UPGRADABLE)

    def test_exactly_wt_is_vision_aura_capable(self, real_registry):
        from mygame.world.constants import VISION_AURA
        aura_caps = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(VISION_AURA)
        ]
        assert aura_caps == ["WT"], (
            f"Exactly WT should be vision_aura-capable, got {aura_caps}"
        )

    def test_wt_gates_earlier_than_the_radar(self, real_registry):
        """Vision is lower-risk than +range or radar intel: the tower is the
        cheaper, earlier intelligence building."""
        wt = real_registry.resolve_building("WT")
        rd = real_registry.resolve_building("RD")
        assert wt.rank_requirement <= rd.rank_requirement

    def test_vision_aura_in_capabilities_vocabulary(self):
        """The constant is registered in the controlled vocabulary, so the
        schema validator accepts the YAML (typos fail the load)."""
        from mygame.world.constants import VISION_AURA, BUILDING_CAPABILITIES
        assert VISION_AURA == "vision_aura"
        assert VISION_AURA in BUILDING_CAPABILITIES


# ================================================================== #
#  Item-loot-economy task 6.3 — Field Hospital (FH) building + capability
# ================================================================== #

class TestRealFieldHospitalDef:
    """The REAL buildings.yaml ships the Field Hospital heal aura (§7, R10.3).

    Follows the TestRealWatchtowerDef pattern: assert against the loaded
    real data, so a misindented/omitted ``capabilities: [heal_aura]``
    block in the YAML can't leave the aura inert while unit tests stay
    green.

    **Validates: Requirements 10.3**
    """

    def test_fh_loads_with_medical_fields(self, real_registry):
        fh = real_registry.resolve_building("FH")
        assert fh is not None, "Field Hospital (FH) must exist in the real data"
        assert fh.name == "Field Hospital"
        assert fh.category == "medical"
        assert fh.requires_hq is True
        assert fh.requires_agent is False, (
            "no agent required — the player camps the tile themselves"
        )
        assert fh.max_level == 5

    def test_fh_has_heal_aura_capability(self, real_registry):
        from mygame.world.constants import HEAL_AURA
        fh = real_registry.resolve_building("FH")
        assert fh.has_capability(HEAL_AURA), (
            "FH must carry the 'heal_aura' capability or "
            "_tile_heal_bonus can never grant its heal"
        )

    def test_fh_is_upgradable(self, real_registry):
        """Level scaling (+1..+3 HP per regen interval) is the point of the
        building, so it must be upgradable."""
        from mygame.world.constants import UPGRADABLE
        fh = real_registry.resolve_building("FH")
        assert fh.has_capability(UPGRADABLE)

    def test_exactly_fh_is_heal_aura_capable(self, real_registry):
        from mygame.world.constants import HEAL_AURA
        aura_caps = [
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(HEAL_AURA)
        ]
        assert aura_caps == ["FH"], (
            f"Exactly FH should be heal_aura-capable, got {aura_caps}"
        )

    def test_fh_gates_earlier_than_the_medbay(self, real_registry):
        """The positional, self-serve healer arrives before the Medbay in
        the unlock cadence."""
        fh = real_registry.resolve_building("FH")
        mb = real_registry.resolve_building("MB")
        assert fh.rank_requirement <= mb.rank_requirement

    def test_heal_aura_in_capabilities_vocabulary(self):
        """The constant is registered in the controlled vocabulary, so the
        schema validator accepts the YAML (typos fail the load)."""
        from mygame.world.constants import HEAL_AURA, BUILDING_CAPABILITIES
        assert HEAL_AURA == "heal_aura"
        assert HEAL_AURA in BUILDING_CAPABILITIES


# ------------------------------------------------------------------ #
#  Construction fakes for the real-BS BuildingSystem tests
# ------------------------------------------------------------------ #

class _BuildAttrs:
    """Minimal Evennia Attribute-handler shape (get/add)."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value

    def has(self, key):
        return key in self._data


class _BuildFake:
    """Minimal live-building fake for construction tests."""

    def __init__(self, building_type, owner=None):
        self.key = building_type
        self.attributes = _BuildAttrs({
            "building_type": building_type,
            "owner": owner,
            "building_level": 1,
            "assigned_agent": None,
            "construction_progress": 0,
            "construction_total": 0,
        })

    @property
    def owner(self):
        return self.attributes.get("owner")


class _BuildPlayer:
    """Construction player: resources + level + deeds + owned buildings."""

    def __init__(self, level=100, deeds=None, buildings=None):
        self.key = "Builder"
        self.location = None
        self.db = _AttrBag(
            level=level,
            deeds=dict(deeds or {}),
            combat_lockout_tick=0,
            resources={},
        )
        self._resources = {
            r: 10_000 for r in ("Wood", "Stone", "Iron", "Straw")
        }
        self._buildings = list(buildings or [])

    def get_buildings(self):
        return list(self._buildings)

    def get_resource(self, resource):
        return self._resources.get(resource, 0)

    def has_resources(self, costs):
        return all(self._resources.get(r, 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            self._resources[r] = self._resources[r] - int(amt)
        return True

    def add_resource(self, resource, amount):
        self._resources[resource] = self._resources.get(resource, 0) + int(amount)


class _BuildTile:
    """Buildable Plains tile with no existing building."""

    def __init__(self):
        self.x = 0
        self.y = 0
        self.db = _AttrBag(coord_x=0, coord_y=0)
        self.building = None

    @property
    def terrain_type(self):
        return "Plains"


def _make_real_building_system(real_registry):
    """A BuildingSystem over the REAL registry with a fake factory."""
    from mygame.world.event_bus import EventBus
    from mygame.world.systems.building_system import BuildingSystem

    created = []

    def fake_create(building_def, tile, owner):
        b = _BuildFake(building_def.abbreviation, owner=owner)
        created.append(b)
        tile.building = b
        return b

    system = BuildingSystem(
        registry=real_registry,
        event_bus=EventBus(),
        create_building_func=fake_create,
        build_range=10,
        current_tick_func=lambda: 0,
    )
    return system, created


def _player_with_hq(**kwargs):
    """A _BuildPlayer that owns a completed HQ (passes requires_hq)."""
    player = _BuildPlayer(**kwargs)
    hq = _BuildFake("HQ", owner=player)
    player._buildings.append(hq)
    return player


class TestBlacksmithConstruction:
    """CONSTRUCTION honors the real BS def's requirements (task 4.1).

    Exercises ``BuildingSystem._validate_construction`` (via ``construct``)
    against the REAL loaded Blacksmith def — the requires_hq gate, the
    mid-tier rank gate, and the deed gate all fire from the real data.

    **Validates: Requirements 4.1**
    """

    _DEEDS = {"outpost_cleared": 3}

    def test_construct_refused_without_hq(self, real_registry):
        """requires_hq: true — no Headquarters, no Blacksmith."""
        system, created = _make_real_building_system(real_registry)
        player = _BuildPlayer(deeds=self._DEEDS)  # no HQ owned

        ok, msg = system.construct(player, _BuildTile(), "BS")

        assert ok is False
        assert "Headquarters" in msg
        assert created == []

    def test_construct_refused_below_rank_gate(self, real_registry):
        """rank_requirement 11 — a level-10 player is refused."""
        system, created = _make_real_building_system(real_registry)
        player = _player_with_hq(level=10, deeds=self._DEEDS)

        ok, msg = system.construct(player, _BuildTile(), "BS")

        assert ok is False
        assert "Level 11" in msg
        assert created == []

    def test_construct_refused_without_deeds(self, real_registry):
        """The Lab-mirrored deed gate holds for the BS too."""
        system, created = _make_real_building_system(real_registry)
        player = _player_with_hq(level=100, deeds={})

        ok, msg = system.construct(player, _BuildTile(), "BS")

        assert ok is False
        assert created == []

    def test_constructs_with_hq_rank_deeds_and_resources(self, real_registry):
        """All real construction requirements met → the BS builds, and the
        build cost is actually deducted."""
        system, created = _make_real_building_system(real_registry)
        player = _player_with_hq(level=100, deeds=self._DEEDS)

        ok, msg = system.construct(player, _BuildTile(), "BS")

        assert ok is True, f"construction refused: {msg}"
        assert len(created) == 1
        assert created[0].attributes.get("building_type") == "BS"
        cost = real_registry.resolve_building("BS").cost
        for resource, amount in cost.items():
            assert player.get_resource(resource) == 10_000 - amount

    def test_agent_drives_bs_construction_to_completion(self, real_registry):
        """An assigned agent advances a BS build via
        ``process_agent_construction`` and the finished bench comes out of
        ``under_construction`` (operational). (Driven by ``assigned_agent``,
        NOT the dead ``requires_agent`` field — F4 review fix.)"""
        system, created = _make_real_building_system(real_registry)
        player = _player_with_hq(level=100, deeds=self._DEEDS)

        ok, msg = system.start_construction(player, _BuildTile(), "BS")
        assert ok is True, f"start_construction refused: {msg}"
        bs = created[0]
        assert bs.attributes.get("under_construction") is True

        agent = _AttrBag(db=_AttrBag(incapacitated=False))
        bs.attributes.add("assigned_agent", agent)

        total = bs.attributes.get("construction_total")
        assert total == real_registry.resolve_building("BS").build_time_seconds
        for _ in range(total):
            system.process_agent_construction([bs])

        assert bs.attributes.get("construction_progress") == total
        assert bs.attributes.get("under_construction") is False


class TestBlacksmithBenchUsageGates:
    """Bench usage groundwork: operational-status gating only (task 4.1).

    The future ``insert``/``reroll``/``salvage`` commands gate on the SAME
    "is this building doing its job" check the craft gate uses (design §4.1):
    ``building_is_operational`` — offline or mid-upgrade means unusable.
    There is deliberately NO active-HQ usage gate.

    **Validates: Requirements 4.2, 4.4**
    """

    @staticmethod
    def _bs_instance(offline=False, under_construction=False):
        bs = _BuildFake("BS")
        bs.attributes.add("offline", offline)
        bs.attributes.add("under_construction", under_construction)
        return bs

    def test_online_completed_bs_is_operational(self):
        from mygame.world.utils import building_is_operational
        assert building_is_operational(self._bs_instance()) is True

    def test_offline_bs_is_not_operational(self):
        """Offline protection shuts the bench (mirrors craft gate 5)."""
        from mygame.world.utils import building_is_operational
        assert building_is_operational(self._bs_instance(offline=True)) is False

    def test_mid_upgrade_bs_is_not_operational(self):
        """An upgrading bench is inert until the upgrade completes."""
        from mygame.world.utils import building_is_operational
        bs = self._bs_instance(under_construction=True)
        assert building_is_operational(bs) is False

    def test_no_active_hq_usage_gate(self, real_registry):
        """Decided in design §4.1: bench USAGE has no active-HQ gate — an
        owner whose HQ is destroyed can still use a completed, online BS.
        The operational check consults only the building's own state."""
        from mygame.world.utils import building_is_operational
        owner = _BuildPlayer()  # owns NO HQ at all
        bs = self._bs_instance()
        bs.attributes.add("owner", owner)
        assert building_is_operational(bs) is True


class TestRefineryConstruction:
    """CONSTRUCTION honors the real RF def's requirements (task 5.3).

    Exercises ``BuildingSystem._validate_construction`` (via ``construct``)
    against the REAL loaded Refinery def — the requires_hq gate, the
    late-game rank gate (13), and the fortress-deed gate all fire from the
    real data; an ``assigned_agent`` drives the build like the BS.

    **Validates: Requirements 10.4**
    """

    _DEEDS = {"fortress_cleared": 1}

    @staticmethod
    def _rich_player(**kwargs):
        """A _BuildPlayer with an HQ and the RF's Circuits in stock."""
        player = _player_with_hq(**kwargs)
        player._resources["Circuits"] = 10_000
        return player

    def test_construct_refused_without_hq(self, real_registry):
        """requires_hq: true — no Headquarters, no Refinery."""
        system, created = _make_real_building_system(real_registry)
        player = _BuildPlayer(deeds=self._DEEDS)  # no HQ owned
        player._resources["Circuits"] = 10_000

        ok, msg = system.construct(player, _BuildTile(), "RF")

        assert ok is False
        assert "Headquarters" in msg
        assert created == []

    def test_construct_refused_below_rank_gate(self, real_registry):
        """rank_requirement 13 — a level-12 player is refused."""
        system, created = _make_real_building_system(real_registry)
        player = self._rich_player(level=12, deeds=self._DEEDS)

        ok, msg = system.construct(player, _BuildTile(), "RF")

        assert ok is False
        assert "Level 13" in msg
        assert created == []

    def test_construct_refused_without_fortress_deed(self, real_registry):
        """Nexium is late-game, so its sink gates on a fortress clear."""
        system, created = _make_real_building_system(real_registry)
        player = self._rich_player(level=100, deeds={})

        ok, msg = system.construct(player, _BuildTile(), "RF")

        assert ok is False
        assert created == []

    def test_constructs_with_hq_rank_deed_and_resources(self, real_registry):
        """All real construction requirements met → the RF builds, and the
        build cost (Circuits included) is actually deducted."""
        system, created = _make_real_building_system(real_registry)
        player = self._rich_player(level=100, deeds=self._DEEDS)

        ok, msg = system.construct(player, _BuildTile(), "RF")

        assert ok is True, f"construction refused: {msg}"
        assert len(created) == 1
        assert created[0].attributes.get("building_type") == "RF"
        cost = real_registry.resolve_building("RF").cost
        for resource, amount in cost.items():
            assert player.get_resource(resource) == 10_000 - amount

    def test_agent_drives_rf_construction_to_completion(self, real_registry):
        """An assigned agent advances an RF build via
        ``process_agent_construction`` and the finished converter comes out
        of ``under_construction`` (operational). (Driven by
        ``assigned_agent``, NOT the dead ``requires_agent`` field.)"""
        system, created = _make_real_building_system(real_registry)
        player = self._rich_player(level=100, deeds=self._DEEDS)

        ok, msg = system.start_construction(player, _BuildTile(), "RF")
        assert ok is True, f"start_construction refused: {msg}"
        rf = created[0]
        assert rf.attributes.get("under_construction") is True

        agent = _AttrBag(db=_AttrBag(incapacitated=False))
        rf.attributes.add("assigned_agent", agent)

        total = rf.attributes.get("construction_total")
        assert total == real_registry.resolve_building("RF").build_time_seconds
        for _ in range(total):
            system.process_agent_construction([rf])

        assert rf.attributes.get("construction_progress") == total
        assert rf.attributes.get("under_construction") is False


class TestSniperNestConstruction:
    """CONSTRUCTION honors the real SN def's requirements (task 6.1).

    Exercises ``BuildingSystem._validate_construction`` (via ``construct``)
    against the REAL loaded Sniper Nest def — the requires_hq gate and the
    rank gate (9, a step past the Turret) fire from the real data. Unlike
    the BS/RF there is NO deed gate, and ``requires_agent: false`` — the
    player mans the nest themselves.

    **Validates: Requirements 10.1**
    """

    def test_construct_refused_without_hq(self, real_registry):
        """requires_hq: true — no Headquarters, no Sniper Nest."""
        system, created = _make_real_building_system(real_registry)
        player = _BuildPlayer()  # no HQ owned

        ok, msg = system.construct(player, _BuildTile(), "SN")

        assert ok is False
        assert "Headquarters" in msg
        assert created == []

    def test_construct_refused_below_rank_gate(self, real_registry):
        """rank_requirement 9 — a level-8 player is refused."""
        system, created = _make_real_building_system(real_registry)
        player = _player_with_hq(level=8)

        ok, msg = system.construct(player, _BuildTile(), "SN")

        assert ok is False
        assert "Level 9" in msg
        assert created == []

    def test_constructs_with_hq_rank_and_resources(self, real_registry):
        """All real requirements met → the SN builds with NO deed needed
        (deeds empty), and the build cost is actually deducted."""
        system, created = _make_real_building_system(real_registry)
        player = _player_with_hq(level=9, deeds={})

        ok, msg = system.construct(player, _BuildTile(), "SN")

        assert ok is True, f"construction refused: {msg}"
        assert len(created) == 1
        assert created[0].attributes.get("building_type") == "SN"
        cost = real_registry.resolve_building("SN").cost
        for resource, amount in cost.items():
            assert player.get_resource(resource) == 10_000 - amount

    def test_requires_no_agent(self, real_registry):
        """requires_agent: false — the nest is player-manned; no agent
        drives its build (unlike the BS/RF)."""
        sn = real_registry.resolve_building("SN")
        assert sn.requires_agent is False


# ================================================================== #
#  Item-loot-economy task 4.2 — insert item defs + production routing
# ================================================================== #

# The four Blacksmith inserts (design §4.3) and their expected payloads.
INSERT_EXPECTATIONS = {
    "venom_coating": {"type": "damage_type", "value": "poison"},
    "extended_barrel": {"type": "range", "value": 2},
    "incendiary_core": {"type": "damage_type", "value": "fire"},
    "hollowpoint": {
        "type": "stat", "stat": "damage", "value": 4,
        "tradeoff": {"range": -1},
    },
}

# Classification routing convention: modern -> AR, futuristic -> LB.
INSERT_PRODUCERS = {
    "venom_coating": "LB",
    "extended_barrel": "AR",
    "incendiary_core": "LB",
    "hollowpoint": "AR",
}


class _SupplyBag:
    """Minimal Supply_Bag handler double: counted stacks via add_supply."""

    def __init__(self):
        self.supplies = {}

    def add_supply(self, key, amount, max_stack=99):
        current = self.supplies.get(key, 0)
        added = min(amount, max_stack - current)
        if added > 0:
            self.supplies[key] = current + added
        return max(0, added)


class TestInsertItemDefs:
    """The task 4.2 insert items load with the right shape and routing.

    **Validates: Requirements 5.1, 5.2**
    """

    def test_inserts_defined_with_expected_payloads(self, real_registry):
        for key, expected in INSERT_EXPECTATIONS.items():
            idef = real_registry.items[key]
            assert idef.category == "insert", (
                f"'{key}' should be category 'insert', got '{idef.category}'"
            )
            assert idef.insert_effect == expected, (
                f"'{key}' insert_effect {idef.insert_effect!r} != {expected!r}"
            )

    def test_inserts_are_slotless_supplies(self, real_registry):
        """Inserts are counted supplies, not gear: no slot, no weapon_type."""
        from mygame.world.constants import SUPPLY_CATEGORIES
        assert "insert" in SUPPLY_CATEGORIES
        for key in INSERT_EXPECTATIONS:
            idef = real_registry.items[key]
            assert idef.slot == "", f"insert '{key}' must not occupy a slot"
            assert idef.weapon_type is None
            assert idef.roll_spec is None, f"insert '{key}' must not roll"

    def test_inserts_are_rank_gated(self, real_registry):
        """Every insert recipe needs a non-Terra resource (Circuits/Magmite),
        so each must carry a rank gate to satisfy the freely-craftable
        starter-planet invariant."""
        for key in INSERT_EXPECTATIONS:
            assert real_registry.items[key].required_rank is not None, (
                f"insert '{key}' must be rank-gated (exotic craft_cost)"
            )

    def test_inserts_produced_by_exactly_one_building(self, real_registry):
        """Catalog invariant: each insert routes to exactly one building,
        following the classification convention (modern->AR, futuristic->LB)."""
        for key, expected_abbr in INSERT_PRODUCERS.items():
            producers = [
                abbr for abbr, keys in real_registry.item_production_map.items()
                if key in keys
            ]
            assert producers == [expected_abbr], (
                f"insert '{key}' should be produced by exactly "
                f"{expected_abbr}, got {producers}"
            )

    @pytest.mark.parametrize("insert_key", sorted(INSERT_EXPECTATIONS))
    def test_insert_crafts_end_to_end_in_its_building(self, real_registry,
                                                      insert_key):
        """`craft` succeeds at the routed building and the insert lands as a
        counted stack in the Supply_Bag (the SUPPLY_CATEGORIES routing)."""
        from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION
        from mygame.world.systems.equipment_system import EquipmentSystem

        event_bus = EventBus()
        notifications = []
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda event_name=None, player=None, kind=None, data=None,
            **_extra: notifications.append((kind, data or {})),
        )
        system = EquipmentSystem(real_registry, event_bus)
        player = _CraftPlayer()
        player.equipment = _SupplyBag()
        building = _CraftBuilding(INSERT_PRODUCERS[insert_key], owner=player)

        assert system.craft(player, insert_key, building) is True

        # The insert landed as a counted supply stack.
        assert player.equipment.supplies.get(insert_key) == 1
        kinds = [k for k, _ in notifications]
        assert "crafted" in kinds
        assert "craft_failed" not in kinds, (
            f"craft_failed fired: {notifications}"
        )

        # The craft_cost was actually deducted.
        craft_cost = real_registry.items[insert_key].craft_cost
        for resource, amount in craft_cost.items():
            assert player.get_resource(resource) == 10_000 - amount

    @pytest.mark.parametrize("insert_key", sorted(INSERT_EXPECTATIONS))
    def test_insert_wrong_building_refused(self, real_registry, insert_key):
        """Crafting an insert at the OTHER equipment building fails with
        ``wrong_building`` (no accidental dual-routing)."""
        from mygame.world.event_bus import EventBus, PLAYER_NOTIFICATION
        from mygame.world.systems.equipment_system import EquipmentSystem

        event_bus = EventBus()
        notifications = []
        event_bus.subscribe(
            PLAYER_NOTIFICATION,
            lambda event_name=None, player=None, kind=None, data=None,
            **_extra: notifications.append((kind, data or {})),
        )
        system = EquipmentSystem(real_registry, event_bus)
        player = _CraftPlayer()
        player.equipment = _SupplyBag()
        wrong = "AR" if INSERT_PRODUCERS[insert_key] == "LB" else "LB"
        building = _CraftBuilding(wrong, owner=player)

        assert system.craft(player, insert_key, building) is False
        kind, data = notifications[-1]
        assert kind == "craft_failed"
        assert data.get("reason") == "wrong_building"


# ================================================================== #
#  REGRESSION (review F1/H1) — base-tier rarity weights are REAL data
# ================================================================== #

class TestBaseTierRarityWeights:
    """Every base tier's rarity_weight loads from the REAL outposts.yaml and
    resolves to its intended rarity bucket.

    Regression guard: the original wiring read
    ``_tunable(template, "rarity_weight", 0.0)`` against a BaseTemplateDef
    that had NO such field and YAML that declared none — so every HQ-destroy
    drop rolled in the guard_kill bucket and Epic/Legendary loot was
    unobtainable in production, while a SimpleNamespace-based unit test
    (which fabricated the attribute) stayed green. This test uses the real
    dataclass + the real YAML + the real balance table, so a regression in
    ANY of the three layers fails here.
    """

    EXPECTED_BUCKETS = {
        "outpost": "outpost",
        "stronghold": "stronghold",
        "fortress": "fortress",
        "citadel": "citadel",
    }

    def test_every_tier_declares_a_positive_rarity_weight(self, real_registry):
        for tier in self.EXPECTED_BUCKETS:
            tpl = real_registry.get_base_template(tier)
            assert tpl is not None, f"tier '{tier}' missing from outposts.yaml"
            # getattr with NO default: the field must exist on the dataclass.
            weight = getattr(tpl, "rarity_weight")
            assert weight > 0, (
                f"tier '{tier}' rarity_weight={weight!r} — a zero/absent "
                "weight silently drops it into the guard_kill bucket"
            )

    def test_tiers_resolve_to_their_intended_buckets(self, real_registry):
        from mygame.world.systems.loot_roller import resolve_rarity_bucket

        table = real_registry.balance.rarity_table
        for tier, expected_bucket in self.EXPECTED_BUCKETS.items():
            tpl = real_registry.get_base_template(tier)
            bucket = resolve_rarity_bucket(float(tpl.rarity_weight), table)
            assert bucket == expected_bucket, (
                f"tier '{tier}' (weight {tpl.rarity_weight}) resolved to "
                f"bucket {bucket!r}, expected {expected_bucket!r}"
            )

    def test_citadel_bucket_can_pay_legendary(self, real_registry):
        # The point of the whole chain: the apex tier's bucket must actually
        # carry Legendary odds, or the loot chase is dead content.
        table = real_registry.balance.rarity_table
        citadel_weights = table["citadel"]["weights"]
        assert citadel_weights.get("legendary", 0) > 0


# ================================================================== #
#  research-lab-trees — the four specialized labs + tree wiring
# ================================================================== #

# The four research labs and the technology tree each hosts.
_LAB_TREE_BY_ABBR = {
    "LB": "research",
    "WX": "weapons",
    "DF": "defense",
    "RX": "resource",
}


class TestRealResearchLabDefs:
    """The REAL buildings.yaml ships four research labs, one per tech tree.

    Follows the TestRealBlacksmithDef pattern: assert against the loaded real
    data, so a misindented/omitted ``capabilities: [research_lab]`` or
    ``research_tree:`` line can't leave a lab unlocatable (or hosting the wrong
    tree) while unit tests stay green.
    """

    def test_all_four_labs_load(self, real_registry):
        for abbr in _LAB_TREE_BY_ABBR:
            bdef = real_registry.resolve_building(abbr)
            assert bdef is not None, f"lab {abbr} must exist in the real data"
            assert bdef.category == "research"

    def test_each_lab_declares_research_lab_capability(self, real_registry):
        from mygame.world.constants import RESEARCH_LAB
        for abbr in _LAB_TREE_BY_ABBR:
            bdef = real_registry.resolve_building(abbr)
            assert bdef.has_capability(RESEARCH_LAB), (
                f"lab {abbr} must carry the 'research_lab' capability"
            )

    def test_each_lab_hosts_the_expected_tree(self, real_registry):
        for abbr, tree in _LAB_TREE_BY_ABBR.items():
            bdef = real_registry.resolve_building(abbr)
            assert bdef.research_tree == tree, (
                f"lab {abbr} hosts {bdef.research_tree!r}, expected {tree!r}"
            )

    def test_exactly_four_buildings_are_research_labs(self, real_registry):
        from mygame.world.constants import RESEARCH_LAB
        labs = sorted(
            abbr for abbr, bdef in real_registry.buildings.items()
            if bdef.has_capability(RESEARCH_LAB)
        )
        assert labs == sorted(_LAB_TREE_BY_ABBR), (
            f"expected exactly the four labs, got {labs}"
        )

    def test_lab_capability_and_trees_in_vocabulary(self):
        """The capability + tree names are registered constants, so a typo in
        the YAML fails the load rather than silently disabling a lab."""
        from mygame.world.constants import (
            RESEARCH_LAB, BUILDING_CAPABILITIES, RESEARCH_TREES,
        )
        assert RESEARCH_LAB == "research_lab"
        assert RESEARCH_LAB in BUILDING_CAPABILITIES
        assert set(RESEARCH_TREES) == {"weapons", "defense", "resource",
                                       "research"}

    def test_non_lab_buildings_have_no_research_tree(self, real_registry):
        """Only labs may name a tree — every other building leaves it None
        (the schema forbids research_tree on a non-lab)."""
        from mygame.world.constants import RESEARCH_LAB
        for abbr, bdef in real_registry.buildings.items():
            if not bdef.has_capability(RESEARCH_LAB):
                assert bdef.research_tree is None, (
                    f"non-lab {abbr} must not set research_tree"
                )

    def test_labs_share_the_mid_tier_rank_and_deed_gate(self, real_registry):
        """All four labs gate at rank 11 with the 3-outpost deed (the LB's
        original gate, kept across the new labs)."""
        for abbr in _LAB_TREE_BY_ABBR:
            bdef = real_registry.resolve_building(abbr)
            assert bdef.rank_requirement == 11, abbr
            assert bdef.unlock_deed == "outpost_cleared", abbr
            assert bdef.unlock_deed_count == 3, abbr


class TestRealTechnologyTrees:
    """Every real technology is tagged with a valid tree, and each tree is
    hosted by exactly one lab (the tree<->lab bijection)."""

    def test_every_tech_has_a_valid_tree(self, real_registry):
        from mygame.world.constants import RESEARCH_TREES
        for key, tdef in real_registry.technologies.items():
            assert tdef.tree in RESEARCH_TREES, (
                f"tech {key} has invalid tree {tdef.tree!r}"
            )

    def test_every_tree_is_populated(self, real_registry):
        """No empty tree — each of the four has at least one tech, so no lab
        is a dead end."""
        from mygame.world.constants import RESEARCH_TREES
        trees = {t.tree for t in real_registry.technologies.values()}
        assert trees == set(RESEARCH_TREES)

    def test_get_technologies_for_tree_partitions_the_catalog(
            self, real_registry):
        """get_technologies_for_tree returns exactly that tree's techs, and the
        four trees partition the full catalog with no overlap or gap."""
        from mygame.world.constants import RESEARCH_TREES
        seen = set()
        total = 0
        for tree in RESEARCH_TREES:
            techs = real_registry.get_technologies_for_tree(tree)
            for t in techs:
                assert t.tree == tree
            keys = {t.key for t in techs}
            assert keys.isdisjoint(seen), "a tech appears in two trees"
            seen |= keys
            total += len(techs)
        assert seen == set(real_registry.technologies)
        assert total == len(real_registry.technologies)

    def test_research_lab_for_tree_returns_the_hosting_lab(self, real_registry):
        for abbr, tree in _LAB_TREE_BY_ABBR.items():
            lab = real_registry.research_lab_for_tree(tree)
            assert lab is not None, f"no lab hosts the {tree!r} tree"
            assert lab.abbreviation == abbr

    def test_research_lab_for_tree_unknown_tree_is_none(self, real_registry):
        assert real_registry.research_lab_for_tree("nonexistent") is None
