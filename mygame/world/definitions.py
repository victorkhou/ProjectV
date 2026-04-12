"""
Definition dataclasses for the RTS Combat Overworld game.

These are plain data containers loaded from YAML definition files by the
Data Registry. They are used by all game systems to access entity data
without hardcoded constants.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BuildingDef:
    """Definition for a building type."""

    name: str
    abbreviation: str
    cost: dict[str, int]
    max_health: int
    requires_hq: bool
    required_terrain: str | None
    category: str
    produces: str | None
    unlocks: list[str] = field(default_factory=list)
    map_symbol: str = "??"


@dataclass
class ItemDef:
    """Definition for an equippable/usable item."""

    key: str
    name: str
    slot: str
    stat_modifiers: dict[str, float] = field(default_factory=dict)
    ammo_cost: dict[str, int] | None = None
    classification: str = "modern"
    required_rank: str | None = None


@dataclass
class RankDef:
    """Definition for a military rank level."""

    name: str
    level: int
    xp_threshold: int
    unlocks: list[str] = field(default_factory=list)


@dataclass
class TechnologyDef:
    """Definition for a researchable technology."""

    name: str
    key: str
    required_rank: str
    resource_cost: dict[str, int] = field(default_factory=dict)
    research_ticks: int = 10
    effect_type: str = ""
    effect_value: Any = None


@dataclass
class PowerupDef:
    """Definition for a temporary combat powerup."""

    name: str
    key: str
    required_rank: str
    effect_type: str
    effect_value: float
    duration_ticks: int
    cooldown_ticks: int


@dataclass
class TerrainDef:
    """Definition for a terrain type."""

    terrain_type: str
    map_symbol: str
    resource_type: str | None = None
    passable: bool = True


@dataclass
class PlanetDef:
    """Definition for a planet type."""

    name: str
    planet_type: str
    terrain_types: list[str] = field(default_factory=list)


@dataclass
class CoordinateSpaceDef:
    """Definition for a single planet's coordinate space."""

    planet_key: str
    planet_type: str
    width: int
    height: int
    terrain_seed: int
    terrain_noise_cell_size: int = 8
    terrain_weights: dict[str, float] = field(default_factory=dict)
    persistence_type: str = "static"
    spawn_x: int = 0
    spawn_y: int = 0
    default_planet: bool = False
    z_level: int = 0
    seed_rotation_ticks: int = 0  # 0 = never rotate; >0 = reshuffle every N ticks


@dataclass
class BalanceConfig:
    """Game balance configuration values."""

    production_scaling: dict[int, int] = field(default_factory=lambda: {
        1: 10, 2: 50, 3: 150, 4: 400, 5: 1000
    })
    turret_damage: int = 15
    turret_radius: int = 10
    xp_kill: int = 100
    xp_building_destroy: int = 50
    xp_damage: float = 0.1
    xp_death_loss: int = 50
    gather_amount: int = 1
    player_default_health: int = 100
    resource_respawn_ticks: int = 30
    combat_lockout_ticks: int = 5
    tick_interval: float = 1.0
    chunk_size: int = 10
    save_interval: int = 30
    metrics_enabled: bool = False
    metrics_interval: int = 60
    player_vision_radius: int = 10
    building_vision_radius: int = 7
    room_cache_max_size: int = 1000
    gc_interval_ticks: int = 100
    gc_min_age_ticks: int = 50
    map_border_tiles: int = 5
