"""Unit tests for SchemaValidator."""

from mygame.world.schema_validator import SchemaValidator


def make_valid_building(**overrides):
    base = {
        "name": "Headquarters",
        "abbreviation": "HQ",
        "cost": {"wood": 100, "stone": 50},
        "max_health": 500,
        "requires_hq": False,
        "category": "headquarters",
        "map_symbol": "HQ",
        "build_time_seconds": 180,
        "max_level": 5,
        "rank_requirement": 1,
        "requires_agent": False,
        "storage_capacity": 0,
    }
    base.update(overrides)
    return base


def make_valid_item(**overrides):
    base = {"key": "rifle", "name": "Rifle", "slot": "weapon"}
    base.update(overrides)
    return base


def make_valid_rank(level=1, xp=0, **overrides):
    base = {
        "name": f"Rank{level}",
        "level": level,
        "xp_threshold": xp,
        "agent_cap": 2,
        "planet_access": ["terra"],
    }
    base.update(overrides)
    return base


def make_valid_tech(**overrides):
    base = {
        "name": "Adv Armor",
        "key": "adv_armor",
        "required_rank": "Sergeant",
        "resource_cost": {"iron": 50},
        "research_ticks": 10,
    }
    base.update(overrides)
    return base


def make_valid_powerup(**overrides):
    base = {
        "name": "Damage Boost",
        "key": "dmg_boost",
        "required_rank": "Corporal",
        "effect_type": "damage_bonus",
        "effect_value": 1.5,
        "duration_ticks": 30,
        "cooldown_ticks": 120,
    }
    base.update(overrides)
    return base


def make_valid_ability_gate(**overrides):
    base = {"key": "delivery", "required_level": 21}
    base.update(overrides)
    return base


class TestValidateBuildings:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid_buildings(self):
        assert self.v.validate_buildings([make_valid_building()]) == []

    def test_not_a_list(self):
        errs = self.v.validate_buildings({"bad": True})
        assert len(errs) == 1
        assert "expected a list" in errs[0]

    def test_missing_required_fields(self):
        errs = self.v.validate_buildings([{"name": "X"}])
        assert any("missing required fields" in e for e in errs)

    def test_abbreviation_wrong_length(self):
        errs = self.v.validate_buildings([make_valid_building(abbreviation="ABC")])
        assert any("abbreviation must be 2 characters" in e for e in errs)

    def test_cost_not_positive_int(self):
        errs = self.v.validate_buildings([make_valid_building(cost={"wood": -5})])
        assert any("positive integer" in e for e in errs)

    def test_cost_not_int(self):
        errs = self.v.validate_buildings([make_valid_building(cost={"wood": 1.5})])
        assert any("positive integer" in e for e in errs)

    def test_max_health_zero(self):
        errs = self.v.validate_buildings([make_valid_building(max_health=0)])
        assert any("max_health must be > 0" in e for e in errs)

    def test_max_health_not_int(self):
        errs = self.v.validate_buildings([make_valid_building(max_health="high")])
        assert any("max_health must be an integer" in e for e in errs)

    def test_map_symbol_wrong_length(self):
        errs = self.v.validate_buildings([make_valid_building(map_symbol="ABC")])
        assert any("map_symbol must be 2 characters" in e for e in errs)

    def test_entry_not_dict(self):
        errs = self.v.validate_buildings(["not_a_dict"])
        assert any("expected dict" in e for e in errs)


class TestValidateItems:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid_items(self):
        data = {"items": [make_valid_item()], "production_map": {}}
        assert self.v.validate_items(data) == []

    def test_not_a_dict(self):
        errs = self.v.validate_items([])
        assert any("expected a dict" in e for e in errs)

    def test_missing_required_fields(self):
        data = {"items": [{"key": "x"}]}
        errs = self.v.validate_items(data)
        assert any("missing required fields" in e for e in errs)

    def test_stat_modifiers_not_numeric(self):
        data = {"items": [make_valid_item(stat_modifiers={"damage": "high"})]}
        errs = self.v.validate_items(data)
        assert any("must be numeric" in e for e in errs)

    def test_ammo_cost_not_positive_int(self):
        data = {"items": [make_valid_item(ammo_cost={"iron": 0})]}
        errs = self.v.validate_items(data)
        assert any("positive integer" in e for e in errs)

    def test_ammo_cost_not_dict(self):
        data = {"items": [make_valid_item(ammo_cost="bad")]}
        errs = self.v.validate_items(data)
        assert any("ammo_cost must be a dict" in e for e in errs)

    def test_stat_modifiers_not_dict(self):
        data = {"items": [make_valid_item(stat_modifiers="bad")]}
        errs = self.v.validate_items(data)
        assert any("stat_modifiers must be a dict" in e for e in errs)


class TestValidateRanks:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid_ranks(self):
        data = [
            make_valid_rank(1, 0),
            make_valid_rank(2, 100),
            make_valid_rank(3, 300),
        ]
        assert self.v.validate_ranks(data) == []

    def test_not_a_list(self):
        errs = self.v.validate_ranks({})
        assert any("expected a list" in e for e in errs)

    def test_missing_fields(self):
        errs = self.v.validate_ranks([{"name": "X"}])
        assert any("missing required fields" in e for e in errs)

    def test_duplicate_levels(self):
        data = [make_valid_rank(1, 0), make_valid_rank(1, 100, name="Dup")]
        errs = self.v.validate_ranks(data)
        assert any("duplicate level" in e for e in errs)

    def test_xp_not_strictly_increasing(self):
        data = [
            make_valid_rank(1, 0),
            make_valid_rank(2, 100),
            make_valid_rank(3, 50),  # not increasing
        ]
        errs = self.v.validate_ranks(data)
        assert any("must be greater than" in e for e in errs)

    def test_negative_level(self):
        errs = self.v.validate_ranks([make_valid_rank(-1, 0)])
        assert any("positive integer" in e for e in errs)


class TestValidateTechnologies:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid(self):
        assert self.v.validate_technologies([make_valid_tech()]) == []

    def test_not_a_list(self):
        errs = self.v.validate_technologies("bad")
        assert any("expected a list" in e for e in errs)

    def test_missing_fields(self):
        errs = self.v.validate_technologies([{"name": "X"}])
        assert any("missing required fields" in e for e in errs)

    def test_research_ticks_zero(self):
        errs = self.v.validate_technologies([make_valid_tech(research_ticks=0)])
        assert any("research_ticks must be > 0" in e for e in errs)

    def test_research_ticks_not_int(self):
        errs = self.v.validate_technologies([make_valid_tech(research_ticks="slow")])
        assert any("research_ticks must be an integer" in e for e in errs)


class TestValidatePowerups:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid(self):
        assert self.v.validate_powerups([make_valid_powerup()]) == []

    def test_not_a_list(self):
        errs = self.v.validate_powerups(42)
        assert any("expected a list" in e for e in errs)

    def test_missing_fields(self):
        errs = self.v.validate_powerups([{"name": "X"}])
        assert any("missing required fields" in e for e in errs)

    def test_duration_ticks_zero(self):
        errs = self.v.validate_powerups([make_valid_powerup(duration_ticks=0)])
        assert any("duration_ticks must be > 0" in e for e in errs)

    def test_cooldown_ticks_zero(self):
        errs = self.v.validate_powerups([make_valid_powerup(cooldown_ticks=0)])
        assert any("cooldown_ticks must be > 0" in e for e in errs)


class TestValidateAbilityGates:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid(self):
        assert self.v.validate_ability_gates([make_valid_ability_gate()]) == []

    def test_not_a_list(self):
        errs = self.v.validate_ability_gates({"key": "delivery"})
        assert any("expected a list" in e for e in errs)

    def test_entry_not_dict(self):
        errs = self.v.validate_ability_gates(["delivery"])
        assert any("expected dict" in e for e in errs)

    def test_missing_required_fields(self):
        errs = self.v.validate_ability_gates([{"key": "delivery"}])
        assert any("missing required fields" in e for e in errs)
        assert any("required_level" in e for e in errs)

    def test_key_empty_string(self):
        errs = self.v.validate_ability_gates([make_valid_ability_gate(key="")])
        assert any("key must be a non-empty string" in e for e in errs)

    def test_key_not_string(self):
        errs = self.v.validate_ability_gates([make_valid_ability_gate(key=5)])
        assert any("key must be a non-empty string" in e for e in errs)

    def test_duplicate_key(self):
        data = [
            make_valid_ability_gate(key="delivery", required_level=21),
            make_valid_ability_gate(key="delivery", required_level=30),
        ]
        errs = self.v.validate_ability_gates(data)
        assert any("duplicate key 'delivery'" in e for e in errs)

    def test_required_level_not_int(self):
        errs = self.v.validate_ability_gates(
            [make_valid_ability_gate(required_level="high")]
        )
        assert any("required_level must be an integer" in e for e in errs)

    def test_required_level_bool_rejected(self):
        errs = self.v.validate_ability_gates(
            [make_valid_ability_gate(required_level=True)]
        )
        assert any("required_level must be an integer" in e for e in errs)

    def test_required_level_below_range(self):
        errs = self.v.validate_ability_gates(
            [make_valid_ability_gate(required_level=0)]
        )
        assert any("must be between 1 and" in e for e in errs)

    def test_required_level_above_range(self):
        from mygame.world.constants import MAX_LEVEL
        errs = self.v.validate_ability_gates(
            [make_valid_ability_gate(required_level=MAX_LEVEL + 1)]
        )
        assert any("must be between 1 and" in e for e in errs)

    def test_required_level_boundaries_valid(self):
        from mygame.world.constants import MAX_LEVEL
        data = [
            make_valid_ability_gate(key="low", required_level=1),
            make_valid_ability_gate(key="high", required_level=MAX_LEVEL),
        ]
        assert self.v.validate_ability_gates(data) == []


class TestValidateTerrain:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid(self):
        data = {
            "terrain": [
                {"terrain_type": "Plains", "map_symbol": "PP"},
                {"terrain_type": "Forest", "map_symbol": "FF"},
            ],
            "planets": [
                {"name": "Earth", "terrain_types": ["Plains", "Forest"]},
            ],
        }
        assert self.v.validate_terrain(data) == []

    def test_not_a_dict(self):
        errs = self.v.validate_terrain([])
        assert any("expected a dict" in e for e in errs)

    def test_missing_required_fields(self):
        data = {"terrain": [{"terrain_type": "Plains"}]}
        errs = self.v.validate_terrain(data)
        assert any("missing required fields" in e for e in errs)

    def test_map_symbol_wrong_length(self):
        data = {"terrain": [{"terrain_type": "Plains", "map_symbol": "PPP"}]}
        errs = self.v.validate_terrain(data)
        assert any("map_symbol must be 2 characters" in e for e in errs)

    def test_planet_references_invalid_terrain(self):
        data = {
            "terrain": [{"terrain_type": "Plains", "map_symbol": "PP"}],
            "planets": [{"name": "Earth", "terrain_types": ["Plains", "Void"]}],
        }
        errs = self.v.validate_terrain(data)
        assert any("'Void' not found" in e for e in errs)


class TestValidateBalance:
    def setup_method(self):
        self.v = SchemaValidator()

    def test_valid(self):
        data = {
            "turret_damage": 15,
            "tick_interval": 1.0,
            "metrics_enabled": False,
            "production_scaling": {1: 10, 2: 50, 3: 150, 4: 400, 5: 1000},
        }
        assert self.v.validate_balance(data) == []

    def test_not_a_dict(self):
        errs = self.v.validate_balance("bad")
        assert any("expected a dict" in e for e in errs)

    def test_int_field_wrong_type(self):
        errs = self.v.validate_balance({"turret_damage": "high"})
        assert any("expected int" in e for e in errs)

    def test_float_field_wrong_type(self):
        errs = self.v.validate_balance({"tick_interval": "fast"})
        assert any("expected float" in e for e in errs)

    def test_bool_field_wrong_type(self):
        errs = self.v.validate_balance({"metrics_enabled": "yes"})
        assert any("expected bool" in e for e in errs)

    def test_production_scaling_invalid_key(self):
        errs = self.v.validate_balance({"production_scaling": {0: 10}})
        assert any("key must be 1-5" in e for e in errs)

    def test_production_scaling_key_too_high(self):
        errs = self.v.validate_balance({"production_scaling": {6: 10}})
        assert any("key must be 1-5" in e for e in errs)

    def test_production_scaling_value_not_int(self):
        errs = self.v.validate_balance({"production_scaling": {1: "ten"}})
        assert any("expected int" in e for e in errs)

    def test_production_scaling_not_dict(self):
        errs = self.v.validate_balance({"production_scaling": [1, 2, 3]})
        assert any("expected dict" in e for e in errs)

    def test_int_accepts_valid(self):
        """Int fields should accept valid ints without error."""
        errs = self.v.validate_balance({"turret_damage": 25, "chunk_size": 10})
        assert errs == []

    def test_float_accepts_int(self):
        """Float fields should accept int values too."""
        errs = self.v.validate_balance({"tick_interval": 2})
        assert errs == []

    # --- Migrated economy tunables ---------------------------------- #

    def test_migrated_scalar_fields_valid(self):
        """The migrated economy scalars validate with correct types."""
        errs = self.v.validate_balance({
            "base_training_ticks": 300,
            "harvest_cooldown_ticks": 4,
            "extractor_harvest_multiplier": 3,
            "upgrade_cost_base": 2,
            "academy_training_reduction_per_level": 0.15,
            "extractor_level_bonus": 0.25,
            "turret_level_bonus": 0.20,
            "demolish_refund_default": 0.40,
        })
        assert errs == []

    def test_base_training_cost_valid(self):
        errs = self.v.validate_balance(
            {"base_training_cost": {"Wood": 15, "Stone": 10}}
        )
        assert errs == []

    def test_base_training_cost_rejects_non_positive(self):
        errs = self.v.validate_balance({"base_training_cost": {"Wood": 0}})
        assert any("positive integer" in e for e in errs)

    def test_base_training_cost_rejects_non_dict(self):
        errs = self.v.validate_balance({"base_training_cost": [15, 10]})
        assert any("expected dict" in e for e in errs)

    def test_demolish_refund_rates_valid(self):
        errs = self.v.validate_balance(
            {"demolish_refund_rates": {1: 0.4, 2: 0.5, "3": 0.6}}
        )
        assert errs == []

    def test_demolish_refund_rates_rejects_bad_level(self):
        errs = self.v.validate_balance({"demolish_refund_rates": {9: 0.4}})
        assert any("key must be 1-5" in e for e in errs)

    def test_demolish_refund_rates_rejects_non_numeric_rate(self):
        errs = self.v.validate_balance({"demolish_refund_rates": {1: "half"}})
        assert any("expected number" in e for e in errs)

    def test_coordinate_world_fields_type_checked(self):
        """Vision/GC knobs read generically by _build_balance are validated."""
        for field in ("player_vision_radius", "building_vision_radius",
                      "room_cache_max_size", "gc_interval_ticks",
                      "gc_min_age_ticks", "map_border_tiles"):
            errs = self.v.validate_balance({field: "not-an-int"})
            assert any(field in e and "expected int" in e for e in errs), field

    def test_coordinate_world_fields_accept_valid_ints(self):
        errs = self.v.validate_balance({
            "player_vision_radius": 12,
            "building_vision_radius": 8,
            "room_cache_max_size": 500,
            "gc_interval_ticks": 200,
            "gc_min_age_ticks": 25,
            "map_border_tiles": 5,
        })
        assert errs == []


class TestCrossValidate:
    """Test cross_validate using a mock registry object."""

    def setup_method(self):
        self.v = SchemaValidator()

    def _make_registry(self, **overrides):
        """Create a minimal mock registry with valid cross-references."""
        from types import SimpleNamespace
        from mygame.world.definitions import (
            BuildingDef, ItemDef, RankDef, TechnologyDef, PowerupDef, PlanetDef,
            TerrainDef,
        )

        defaults = {
            "terrain": {
                "Plains": TerrainDef(terrain_type="Plains", map_symbol=".."),
                "Forest": TerrainDef(
                    terrain_type="Forest", map_symbol="ff", resource_type="Wood",
                ),
            },
            "ranks": [
                RankDef(name="Recruit", level=1, xp_threshold=0),
                RankDef(name="Sergeant", level=5, xp_threshold=500),
            ],
            "buildings": {
                "HQ": BuildingDef(
                    name="HQ", abbreviation="HQ", cost={}, max_health=500,
                    requires_hq=False, required_terrain=None,
                    category="headquarters", produces=None,
                ),
                "MM": BuildingDef(
                    name="Mill", abbreviation="MM", cost={"Wood": 10}, max_health=200,
                    requires_hq=True, required_terrain="Plains",
                    category="resource", produces="Wood",
                ),
                "AA": BuildingDef(
                    name="Armory", abbreviation="AA", cost={}, max_health=200,
                    requires_hq=True, required_terrain=None,
                    category="equipment", produces=None,
                ),
            },
            "items": {
                "rifle": ItemDef(key="rifle", name="Rifle", slot="weapon",
                                 required_rank="Recruit"),
            },
            "technologies": {
                "adv_armor": TechnologyDef(
                    name="Adv Armor", key="adv_armor",
                    required_rank="Sergeant",
                ),
            },
            "powerups": {
                "dmg_boost": PowerupDef(
                    name="Damage Boost", key="dmg_boost",
                    required_rank="Recruit", effect_type="damage_bonus",
                    effect_value=1.5, duration_ticks=30, cooldown_ticks=120,
                ),
            },
            "item_production_map": {
                "AA": ["rifle"],
            },
            "planets": {
                "Earth": PlanetDef(
                    name="Earth", planet_type="Earth_Planet",
                    terrain_types=["Plains", "Forest"],
                ),
            },
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_valid_cross_references(self):
        reg = self._make_registry()
        assert self.v.cross_validate(reg) == []

    def test_building_invalid_terrain(self):
        from mygame.world.definitions import BuildingDef
        reg = self._make_registry(buildings={
            "MM": BuildingDef(
                name="Mill", abbreviation="MM", cost={}, max_health=200,
                requires_hq=True, required_terrain="Void",
                category="resource", produces="Straw",
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("required_terrain 'Void'" in e for e in errs)

    def test_item_invalid_rank(self):
        from mygame.world.definitions import ItemDef
        reg = self._make_registry(items={
            "rifle": ItemDef(key="rifle", name="Rifle", slot="weapon",
                             required_rank="General"),
        })
        errs = self.v.cross_validate(reg)
        assert any("required_rank 'General'" in e for e in errs)

    def test_tech_invalid_rank(self):
        from mygame.world.definitions import TechnologyDef
        reg = self._make_registry(technologies={
            "adv": TechnologyDef(
                name="Adv", key="adv", required_rank="Admiral",
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("required_rank 'Admiral'" in e for e in errs)

    def test_powerup_invalid_rank(self):
        from mygame.world.definitions import PowerupDef
        reg = self._make_registry(powerups={
            "boost": PowerupDef(
                name="Boost", key="boost", required_rank="Marshal",
                effect_type="damage", effect_value=1.0,
                duration_ticks=10, cooldown_ticks=60,
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("required_rank 'Marshal'" in e for e in errs)

    def test_production_map_invalid_building(self):
        reg = self._make_registry(item_production_map={"ZZ": ["rifle"]})
        errs = self.v.cross_validate(reg)
        assert any("building abbreviation 'ZZ'" in e for e in errs)

    def test_production_map_invalid_item(self):
        reg = self._make_registry(item_production_map={"AA": ["nonexistent"]})
        errs = self.v.cross_validate(reg)
        assert any("item key 'nonexistent'" in e for e in errs)

    # --- Resource-name references (implicit RESOURCE_TYPES set) ------- #

    def test_building_cost_invalid_resource(self):
        from mygame.world.definitions import BuildingDef
        reg = self._make_registry(buildings={
            "MM": BuildingDef(
                name="Mill", abbreviation="MM", cost={"Unobtanium": 5},
                max_health=200, requires_hq=True, required_terrain="Plains",
                category="resource", produces="Wood",
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("cost resource 'Unobtanium'" in e for e in errs)

    def test_building_produces_invalid_resource(self):
        from mygame.world.definitions import BuildingDef
        reg = self._make_registry(buildings={
            "MM": BuildingDef(
                name="Mill", abbreviation="MM", cost={"Wood": 5}, max_health=200,
                requires_hq=True, required_terrain="Plains",
                category="resource", produces="Fairydust",
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("produces 'Fairydust'" in e for e in errs)

    def test_item_ammo_cost_invalid_resource(self):
        from mygame.world.definitions import ItemDef
        reg = self._make_registry(items={
            "rifle": ItemDef(key="rifle", name="Rifle", slot="weapon",
                             ammo_cost={"Plutonium": 1}),
        })
        errs = self.v.cross_validate(reg)
        assert any("ammo_cost resource 'Plutonium'" in e for e in errs)

    def test_tech_resource_cost_invalid_resource(self):
        from mygame.world.definitions import TechnologyDef
        reg = self._make_registry(technologies={
            "adv": TechnologyDef(
                name="Adv", key="adv", required_rank="Sergeant",
                resource_cost={"Mithril": 3},
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("resource_cost resource 'Mithril'" in e for e in errs)

    def test_terrain_invalid_resource_type(self):
        from mygame.world.definitions import TerrainDef
        reg = self._make_registry(terrain={
            "Plains": TerrainDef(terrain_type="Plains", map_symbol=".."),
            "Weird": TerrainDef(
                terrain_type="Weird", map_symbol="ww", resource_type="Adamantium",
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("resource_type 'Adamantium'" in e for e in errs)

    def test_none_unlocks_does_not_raise(self):
        """A building with unlocks=None must not crash cross_validate.

        yaml `unlocks: null` populates None (key present, not the [] default).
        The loop must be None-safe like the sibling resource checks.
        """
        from mygame.world.definitions import BuildingDef
        reg = self._make_registry(buildings={
            "HQ": BuildingDef(
                name="HQ", abbreviation="HQ", cost={}, max_health=500,
                requires_hq=False, required_terrain=None,
                category="headquarters", produces=None, unlocks=None,
            ),
        })
        reg.item_production_map = {}
        # Must return a (possibly empty) list, not raise TypeError.
        errs = self.v.cross_validate(reg)
        assert isinstance(errs, list)

    def test_scalar_resource_cost_reported_not_raised(self):
        """A scalar tech resource_cost yields a clean error, not a TypeError."""
        from mygame.world.definitions import TechnologyDef
        reg = self._make_registry(technologies={
            "adv": TechnologyDef(
                name="Adv", key="adv", required_rank="Sergeant",
                resource_cost=100,  # scalar, not a mapping
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("resource_cost must be a mapping" in e for e in errs)

    def test_valid_resource_references_pass(self):
        """Costs/ammo/tech/terrain referencing known resources produce no error."""
        from mygame.world.definitions import BuildingDef, ItemDef
        reg = self._make_registry(
            buildings={
                "MM": BuildingDef(
                    name="Mill", abbreviation="MM",
                    cost={"Wood": 5, "Iron": 2}, max_health=200,
                    requires_hq=True, required_terrain="Plains",
                    category="resource", produces="Energy",
                ),
            },
            items={
                "rifle": ItemDef(key="rifle", name="Rifle", slot="weapon",
                                 ammo_cost={"Circuits": 1}),
            },
            item_production_map={},
        )
        assert self.v.cross_validate(reg) == []

    # --- Building unlocks → valid abbreviations ----------------------- #

    def test_building_unlocks_invalid_abbreviation(self):
        from mygame.world.definitions import BuildingDef
        reg = self._make_registry(buildings={
            "HQ": BuildingDef(
                name="HQ", abbreviation="HQ", cost={}, max_health=500,
                requires_hq=False, required_terrain=None,
                category="headquarters", produces=None,
                unlocks=["EX", "ZZ"],  # ZZ is not a building
            ),
            "EX": BuildingDef(
                name="Extractor", abbreviation="EX", cost={}, max_health=200,
                requires_hq=True, required_terrain=None,
                category="resource", produces=None,
            ),
        })
        errs = self.v.cross_validate(reg)
        assert any("unlocks 'ZZ'" in e for e in errs)

    def test_building_unlocks_valid_abbreviation_passes(self):
        from mygame.world.definitions import BuildingDef
        reg = self._make_registry(buildings={
            "HQ": BuildingDef(
                name="HQ", abbreviation="HQ", cost={}, max_health=500,
                requires_hq=False, required_terrain=None,
                category="headquarters", produces=None,
                unlocks=["EX"],
            ),
            "EX": BuildingDef(
                name="Extractor", abbreviation="EX", cost={}, max_health=200,
                requires_hq=True, required_terrain=None,
                category="resource", produces=None,
            ),
        })
        # No unlocks-related error (item_production_map default AA is fine here
        # since AA is absent — clear it to avoid unrelated noise).
        reg.item_production_map = {}
        errs = self.v.cross_validate(reg)
        assert not any("unlocks" in e for e in errs)
