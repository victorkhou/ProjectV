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
    """Definition for a building type.

    ``capabilities`` is the data-driven behavior vocabulary (see
    ``world.constants.BUILDING_CAPABILITIES``). Game systems branch on
    capabilities rather than on the two-letter ``abbreviation`` so adding a
    building with existing behavior is a YAML edit, not a code change. Use
    :meth:`has_capability` to test membership.
    """

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
    build_time_seconds: int = 120
    max_level: int = 5
    rank_requirement: int = 1
    requires_agent: bool = False
    storage_capacity: int = 0
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def has_capability(self, capability: str) -> bool:
        """Return True if this building declares the given capability flag."""
        return capability in self.capabilities


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
    agent_cap: int = 2
    planet_access: list[str] = field(default_factory=list)


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
    rank_requirement: int = 1


@dataclass
class AbilityGateDef:
    """Definition for a data-driven ability gate.

    Maps a named gated ability to the entity level required to unlock it.
    """

    key: str
    required_level: int  # 1..MAX_LEVEL inclusive


@dataclass
class BalanceConfig:
    """Game balance configuration values.

    This dataclass is the single source of truth for every hot-tunable
    balance number: the field defaults below are the canonical values, and
    ``data/config/balance.yaml`` overrides any subset of them at load (and on
    ``@reload``).  Values that change *validation bounds* or *code contracts*
    (rank/level counts, the ``DeliveryState`` enum, pathfinding node caps)
    intentionally stay in ``world.constants`` instead — those are structural,
    not balance.
    """

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
    agent_xp_harvest: int = 5
    agent_xp_delivery: int = 15
    agent_xp_construction: int = 20
    agent_xp_combat: int = 50
    agent_xp_time_served: int = 0
    agent_xp_death_loss: int = 25

    # --- Agent training (migrated from world.constants) --------------- #
    #: Base training cost per resource for agent #N (cost = base × N).
    base_training_cost: dict[str, int] = field(default_factory=lambda: {
        "Wood": 15, "Stone": 10, "Iron": 5,
    })
    #: Base training time in ticks (5 minutes at 1 tick/s).
    base_training_ticks: int = 300
    #: Training time reduction per Academy level (fraction, 0.15 = 15%).
    academy_training_reduction_per_level: float = 0.15

    # --- Resource harvesting & production (migrated) ------------------ #
    #: Ticks between harvest yields (player active-presence).
    harvest_cooldown_ticks: int = 4
    #: Units yielded per harvest action on raw terrain.
    harvest_yield_per_action: int = 1
    #: Multiplier when harvesting at an Extractor (vs raw terrain).
    extractor_harvest_multiplier: int = 3
    #: Per-level Extractor production bonus: base × (1 + BONUS × (level-1)).
    extractor_level_bonus: float = 0.25
    #: Base Extractor storage capacity at level 1.
    extractor_base_capacity: int = 100
    #: Additional Extractor storage per level above 1.
    extractor_capacity_per_level: int = 50
    #: Base Vault storage capacity at level 1.
    vault_base_capacity: int = 100
    #: Additional Vault storage per level above 1.
    vault_capacity_per_level: int = 20

    # --- Building scaling (migrated) ---------------------------------- #
    #: Upgrade cost multiplier base: cost = base_cost × COST_BASE^(level-1).
    upgrade_cost_base: int = 2
    #: Upgrade time multiplier base: time = build_time × TIME_BASE^(level-1).
    upgrade_time_base: int = 3
    #: Per-level Turret damage bonus: base × (1 + BONUS × (level-1)).
    turret_level_bonus: float = 0.20
    #: Demolish refund rate by building level (fraction of invested cost).
    demolish_refund_rates: dict[int, float] = field(default_factory=lambda: {
        1: 0.40, 2: 0.50, 3: 0.60, 4: 0.70, 5: 0.80,
    })
    #: Default demolish refund rate for levels not in the table.
    demolish_refund_default: float = 0.40

    @classmethod
    def current(cls) -> "BalanceConfig":
        """Return the live balance config, or defaults when none is registered.

        The accessor for hot-tunable balance from code paths that lack a
        ``DataRegistry`` reference (e.g. Evennia scripts and the class-level
        ``ResourceSystem`` helpers). Returns the registered singleton's balance
        when a server is running, and a default ``BalanceConfig()`` otherwise
        (e.g. the fast unit-test suite, which registers no singleton). The
        ``DataRegistry`` import is lazy to avoid a definitions→registry cycle.

        Deprecated:
            Reaches the process-wide ``DataRegistry`` singleton, which hides
            the dependency and leaks global state between tests. Prefer
            depending on
            ``world.core.ports.definitions_provider.DefinitionsProvider`` and
            reading ``provider.balance`` (injected via
            ``RegistryDefinitionsProvider`` at the composition root). Retained
            until the remaining script/static-helper callers are migrated.
        """
        try:
            from world.data_registry import DataRegistry

            registry = DataRegistry.get_instance()
            if registry is not None:
                return registry.balance
        except Exception:
            pass
        return cls()
