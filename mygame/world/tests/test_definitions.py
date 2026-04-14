"""Unit tests for definition dataclasses."""

from dataclasses import asdict, fields
from mygame.world.definitions import (
    BuildingDef,
    ItemDef,
    RankDef,
    TechnologyDef,
    PowerupDef,
    TerrainDef,
    PlanetDef,
    CoordinateSpaceDef,
    BalanceConfig,
)


class TestBuildingDef:
    def test_required_fields(self):
        b = BuildingDef(
            name="Headquarters",
            abbreviation="HQ",
            cost={"wood": 100, "stone": 50},
            max_health=500,
            requires_hq=False,
            required_terrain=None,
            category="headquarters",
            produces=None,
        )
        assert b.name == "Headquarters"
        assert b.abbreviation == "HQ"
        assert b.cost == {"wood": 100, "stone": 50}
        assert b.max_health == 500
        assert b.requires_hq is False
        assert b.required_terrain is None
        assert b.category == "headquarters"
        assert b.produces is None

    def test_defaults(self):
        b = BuildingDef(
            name="Mill", abbreviation="MM", cost={"straw": 20},
            max_health=200, requires_hq=True, required_terrain="Plains",
            category="resource", produces="Straw",
        )
        assert b.unlocks == []
        assert b.map_symbol == "??"

    def test_with_unlocks(self):
        b = BuildingDef(
            name="Tech Lab", abbreviation="TL", cost={"iron": 80},
            max_health=300, requires_hq=True, required_terrain=None,
            category="research", produces=None,
            unlocks=["adv_armor", "turret_mk2"], map_symbol="TL",
        )
        assert b.unlocks == ["adv_armor", "turret_mk2"]
        assert b.map_symbol == "TL"


class TestItemDef:
    def test_weapon_item(self):
        i = ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon",
            stat_modifiers={"damage": 25.0, "range": 3.0},
            ammo_cost={"iron": 1}, classification="modern",
            required_rank="Private",
        )
        assert i.slot == "weapon"
        assert i.stat_modifiers["damage"] == 25.0
        assert i.ammo_cost == {"iron": 1}
        assert i.required_rank == "Private"

    def test_defaults(self):
        i = ItemDef(key="basic_vest", name="Basic Vest", slot="armor")
        assert i.stat_modifiers == {}
        assert i.ammo_cost is None
        assert i.classification == "modern"
        assert i.required_rank is None


class TestRankDef:
    def test_basic(self):
        r = RankDef(name="Sergeant", level=5, xp_threshold=500)
        assert r.name == "Sergeant"
        assert r.level == 5
        assert r.xp_threshold == 500
        assert r.unlocks == []

    def test_with_unlocks(self):
        r = RankDef(name="Captain", level=14, xp_threshold=5000,
                    unlocks=["orbital_strike"])
        assert r.unlocks == ["orbital_strike"]


class TestTechnologyDef:
    def test_basic(self):
        t = TechnologyDef(
            name="Advanced Armor", key="adv_armor",
            required_rank="Sergeant",
            resource_cost={"iron": 50, "circuits": 20},
            research_ticks=20, effect_type="stat_bonus",
            effect_value={"damage_reduction": 5},
        )
        assert t.key == "adv_armor"
        assert t.resource_cost == {"iron": 50, "circuits": 20}
        assert t.effect_value == {"damage_reduction": 5}

    def test_defaults(self):
        t = TechnologyDef(name="Basic Tech", key="basic", required_rank="Recruit")
        assert t.resource_cost == {}
        assert t.research_ticks == 10
        assert t.effect_type == ""
        assert t.effect_value is None


class TestPowerupDef:
    def test_basic(self):
        p = PowerupDef(
            name="Damage Boost", key="dmg_boost",
            required_rank="Corporal", effect_type="damage_bonus",
            effect_value=1.5, duration_ticks=30, cooldown_ticks=120,
        )
        assert p.effect_value == 1.5
        assert p.duration_ticks == 30
        assert p.cooldown_ticks == 120


class TestTerrainDef:
    def test_with_resource(self):
        t = TerrainDef(terrain_type="Plains", map_symbol="PP",
                       resource_type="Straw")
        assert t.resource_type == "Straw"
        assert t.passable is True

    def test_impassable(self):
        t = TerrainDef(terrain_type="Void", map_symbol="XX", passable=False)
        assert t.passable is False
        assert t.resource_type is None


class TestPlanetDef:
    def test_basic(self):
        p = PlanetDef(
            name="Earth_Planet", planet_type="Earth_Planet",
            terrain_types=["Plains", "Dirt", "Forest", "Rock", "Mountain"],
        )
        assert len(p.terrain_types) == 5
        assert "Forest" in p.terrain_types

    def test_defaults(self):
        p = PlanetDef(name="Empty", planet_type="Test")
        assert p.terrain_types == []


class TestCoordinateSpaceDef:
    def test_required_fields(self):
        cs = CoordinateSpaceDef(
            planet_key="earth_planet",
            planet_type="earth",
            width=100,
            height=100,
            terrain_seed=42,
        )
        assert cs.planet_key == "earth_planet"
        assert cs.planet_type == "earth"
        assert cs.width == 100
        assert cs.height == 100
        assert cs.terrain_seed == 42

    def test_defaults(self):
        cs = CoordinateSpaceDef(
            planet_key="test", planet_type="earth",
            width=10, height=10, terrain_seed=1,
        )
        assert cs.terrain_noise_cell_size == 8
        assert cs.terrain_weights == {}
        assert cs.persistence_type == "static"
        assert cs.spawn_x == 0
        assert cs.spawn_y == 0
        assert cs.default_planet is False

    def test_custom_values(self):
        cs = CoordinateSpaceDef(
            planet_key="space", planet_type="space",
            width=200, height=200, terrain_seed=99,
            terrain_noise_cell_size=16,
            terrain_weights={"Plains": 0.5, "Forest": 0.5},
            persistence_type="dynamic",
            spawn_x=100, spawn_y=100,
            default_planet=True,
        )
        assert cs.persistence_type == "dynamic"
        assert cs.terrain_weights == {"Plains": 0.5, "Forest": 0.5}
        assert cs.spawn_x == 100
        assert cs.default_planet is True


class TestBalanceConfig:
    def test_defaults(self):
        bc = BalanceConfig()
        assert bc.production_scaling == {1: 10, 2: 50, 3: 150, 4: 400, 5: 1000}
        assert bc.turret_damage == 15
        assert bc.turret_radius == 10
        assert bc.xp_kill == 100
        assert bc.xp_building_destroy == 50
        assert bc.xp_damage == 0.1
        assert bc.xp_death_loss == 50
        assert bc.gather_amount == 1
        assert bc.player_default_health == 100
        assert bc.resource_respawn_ticks == 30
        assert bc.combat_lockout_ticks == 5
        assert bc.tick_interval == 1.0
        assert bc.chunk_size == 10
        assert bc.save_interval == 30
        assert bc.metrics_enabled is False
        assert bc.metrics_interval == 60
        assert bc.player_vision_radius == 10
        assert bc.building_vision_radius == 7
        assert bc.room_cache_max_size == 1000
        assert bc.gc_interval_ticks == 100
        assert bc.gc_min_age_ticks == 50

    def test_custom_values(self):
        bc = BalanceConfig(
            turret_damage=25, player_default_health=200,
            metrics_enabled=True,
        )
        assert bc.turret_damage == 25
        assert bc.player_default_health == 200
        assert bc.metrics_enabled is True


class TestDataclassContracts:
    """Verify all definitions are proper dataclasses with expected field counts."""

    def test_building_def_field_count(self):
        assert len(fields(BuildingDef)) == 15

    def test_item_def_field_count(self):
        assert len(fields(ItemDef)) == 7

    def test_rank_def_field_count(self):
        assert len(fields(RankDef)) == 6

    def test_technology_def_field_count(self):
        assert len(fields(TechnologyDef)) == 7

    def test_powerup_def_field_count(self):
        assert len(fields(PowerupDef)) == 7

    def test_terrain_def_field_count(self):
        assert len(fields(TerrainDef)) == 4

    def test_planet_def_field_count(self):
        assert len(fields(PlanetDef)) == 3

    def test_balance_config_field_count(self):
        assert len(fields(BalanceConfig)) == 22

    def test_coordinate_space_def_field_count(self):
        assert len(fields(CoordinateSpaceDef)) == 14

    def test_all_serializable_via_asdict(self):
        """All defs should be convertible to dicts for YAML round-tripping."""
        b = BuildingDef("HQ", "HQ", {}, 500, False, None, "hq", None)
        assert isinstance(asdict(b), dict)

        i = ItemDef("k", "n", "weapon")
        assert isinstance(asdict(i), dict)

        bc = BalanceConfig()
        assert isinstance(asdict(bc), dict)
