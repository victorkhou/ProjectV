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
    """Definition for an equippable/usable item.

    Every field beyond ``key``/``name`` is defaulted so existing
    ``ItemDef(...)`` construction and ``GameItem`` attribute paths keep
    working. ``slot`` is required for Gear (armor/weapon/accessory) and left
    empty for Supplies (ammo/consumable/throwable).
    """

    key: str
    name: str
    slot: str = ""  # required for Gear; "" for Supplies
    category: str = "armor"  # armor|weapon|accessory|ammo|consumable|throwable
    stat_modifiers: dict[str, float] = field(default_factory=dict)
    # weapon
    weapon_type: str | None = None  # melee|ranged (weapon category only)
    ammo_type: str | None = None  # ammo item key the magazine holds (ranged)
    ammo_per_shot: int = 1
    magazine_size: int | None = None  # magazine capacity (ranged); weapon tracks db.loaded
    ammo_cost: dict[str, int] | None = None  # resource-pool cost (energy weapons)
    # crafting — resource cost to make one via the `craft` command at the
    # matching equipment building (Armory/Lab/Medbay). None/{} = not craftable.
    craft_cost: dict[str, int] | None = None
    # supply
    effect: dict | None = None  # {"type": ..., ...} for consumable/throwable
    max_stack: int = 99  # per-entry cap in the Supply_Bag
    # weight (carry capacity)
    weight: float = 1.0  # per-unit carried weight (>=0); gear + supplies
    # gating
    required_rank: str | None = None  # enforced on equip/use/throw
    classification: str = "modern"


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
class ClassDef:
    """Definition for a selectable player class (state 3.2).

    Selection + stored label only in this iteration — a class carries a
    display ``name``, a stable ``key`` (persisted on ``db.player_class``), and a
    short ``description`` shown in the selection menu. It has NO mechanical
    effect yet (no starting-loadout / stat fields), so the combat, equipment,
    and progression systems remain class-agnostic. Modeled on ``RankDef`` so the
    YAML + dataclass + DataRegistry pattern is ready to gain mechanics later
    (e.g. a ``starting_items`` or ``stat_modifiers`` field) without reshaping
    the selection flow.
    """

    key: str
    name: str
    description: str = ""


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
class TemplateBuildingDef:
    """One building in an NPC-base template: a type at an offset from the HQ."""

    building_type: str  # BuildingDef abbreviation (HQ, WL, TU, ...)
    offset: tuple[int, int] = (0, 0)  # (dx, dy) relative to the HQ tile
    hp: int | None = None  # override max HP; None = BuildingDef default
    level: int = 1


@dataclass
class TemplateGuardDef:
    """A group of guard NPCs in an NPC-base template."""

    role: str = "guard"  # "guard" (melee) or "soldier" (ranged)
    weapon_type: str = "melee"  # "melee" or "ranged"
    count: int = 1
    hp: int | None = None  # override guard HP; None = tier default from balance


@dataclass
class BaseTemplateDef:
    """A data-driven NPC-base layout (outpost / fortress / future tiers).

    Loaded from ``data/definitions/outposts.yaml`` by the DataRegistry and used
    by the OutpostSpawnerSystem to place a base: which buildings at what
    offsets, which guards, and the loot dropped when the HQ is destroyed.
    """

    tier: str  # template key ("outpost", "fortress", ...)
    display_name: str
    buildings: list[TemplateBuildingDef] = field(default_factory=list)
    guards: list[TemplateGuardDef] = field(default_factory=list)
    loot: dict[str, int] = field(default_factory=dict)


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
    # --- Passive HP regeneration (players and agents only) ------------ #
    #: HP regenerated per interval, as a PERCENT of the entity's hp_max.
    #: Default 1.0% every ``hp_regen_interval_ticks`` ticks (i.e. 1%/2 ticks).
    #: Applies to players and agents ONLY — buildings do NOT passively heal and
    #: must be repaired. Set to 0 to disable passive regen entirely. The
    #: effective rate is further scaled per-entity by ``db.regen_multiplier``
    #: (default 1.0), the hook future heal-rate tech / powerups adjust.
    hp_regen_percent: float = 1.0
    #: Ticks between passive-regen applications (the "per N ticks" period).
    hp_regen_interval_ticks: int = 2
    #: Building repair cost, as a fraction of the building's full construction
    #: cost to repair it from 0 HP to full. The actual `repair` charge scales
    #: with the fraction of HP missing, so repairing minor damage is cheap and a
    #: near-destroyed building costs up to this fraction of a rebuild. Buildings
    #: don't passively heal (unlike players/agents) — repair is the only way to
    #: restore building HP.
    repair_cost_fraction: float = 0.5
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

    # --- Carry-weight tuning (equipment/items feature, D7) ------------ #
    #: Per-unit carried weight for each resource. Keys are the canonical
    #: title-case ``RESOURCE_TYPES``; values are weights >= 0 (deliberately
    #: light). A resource absent from this map defaults to
    #: ``world.constants.DEFAULT_RESOURCE_WEIGHT``. Hot-tunable via balance.yaml.
    resource_weights: dict[str, float] = field(default_factory=lambda: {
        "Wood": 0.5, "Stone": 1.0, "Iron": 1.0,
        "Energy": 0.2, "Circuits": 0.3, "Nexium": 2.0,
    })

    # --- Equipment production (equipment/items feature) --------------- #
    #: Ticks between item yields for an equipment building (Armorer/Lab/
    #: Medbay). Production is gated on this cooldown, mirroring the harvest
    #: cooldown, so a building yields at most one item per this many ticks
    #: rather than one every tick.
    equipment_production_ticks: int = 30
    #: Cap on the number of un-equipped items a single owner may accumulate
    #: from production before it stalls, so an idle player's building cannot
    #: grow the object table without bound. 0 disables the cap.
    equipment_production_owner_cap: int = 50

    # --- Guard combat AI (PvE NPC bases feature, Phase 3) ------------- #
    #: Base damage for melee guards (outpost guards, role "guard"). Applied
    #: through the standard combat formula via a synthetic guard weapon.
    guard_melee_damage: int = 10
    #: Base damage for ranged guards (fortress soldiers, role "soldier").
    guard_ranged_damage: int = 15
    #: Weapon range (Chebyshev tiles) for ranged guards. Melee guards are
    #: always range 1 (any of the 8 adjacent tiles, incl. diagonals).
    guard_ranged_range: int = 4
    #: Detection distance (Chebyshev tiles) within which a guard acquires and
    #: attacks the nearest non-owner player each tick.
    guard_aggro_radius: int = 5

    # --- Ranged targeting: lock-on ('target') + shooting ('shoot') ---- #
    #: Ticks to fully lock onto an enemy with 'target'. The effective time is
    #: reduced by the weapon's ``lock_speed`` stat modifier (min 1 tick), so
    #: better gear locks faster. A completed lock grants ``accuracy_targeted``.
    target_lock_ticks: int = 3
    #: Baseline hit chance (0..1) for a shot at a LOCKED target, before the
    #: weapon's ``accuracy`` stat modifier is added. Clamped to [0, 1].
    accuracy_targeted: float = 0.9
    #: Baseline hit chance (0..1) for a DIRECTIONAL (unlocked) shot, before the
    #: weapon's ``accuracy`` stat modifier is added. Clamped to [0, 1].
    accuracy_directional: float = 0.7
    #: Default cooldown (WALL-CLOCK seconds) between a player's own instant
    #: attacks — direct ``attack`` and directional ``shoot`` resolve immediately
    #: (not tick-queued) and are throttled by this instead of the 1-second tick.
    #: A weapon may override it with an ``attack_cooldown`` stat modifier. Does
    #: NOT apply to turrets/guards/locked-tracking shots (those stay tick-queued).
    attack_cooldown_seconds: float = 1.0
    #: Linkdead grace window (WALL-CLOCK seconds): when a PLAYING player drops
    #: their connection without ``quit``, their character stays a live combat
    #: target for this long before it is removed to the lobby (the anti-combat-
    #: log rule). Tuned >= the combat lockout so pulling the plug can't dodge an
    #: active fight. Only used when the lobby lifecycle flow is enabled.
    linkdead_grace_seconds: float = 30.0

    # --- NPC base spawner + elimination (PvE NPC bases, Phase 5) ------ #
    #: XP awarded for destroying an NPC base's HQ (the whole base is wiped) —
    #: far more than xp_building_destroy=50, so raiding is decisively rewarding.
    xp_hq_destroy: int = 500
    #: Ticks before a cleared NPC base respawns at a fresh location (~10 min
    #: at 1 tick/s). 0 disables respawning.
    outpost_respawn_ticks: int = 600
    #: Outposts (small NPC bases) placed per planet at server init.
    outpost_count: int = 5
    #: Fortresses (large NPC bases) placed per planet at server init.
    fortress_count: int = 2
    #: HP for each outpost guard NPC (overridden per template if specified).
    outpost_guard_hp: int = 80
    #: HP for each fortress guard NPC (overridden per template if specified).
    fortress_guard_hp: int = 150

    # --- Tile (room) item-capacity caps --------------------------------- #
    #: Max loose ground items (Game_Item + Resource_Drop objects) a tile can
    #: hold before generation/drops are refused. These bound how many drop
    #: objects a single tile can accumulate. A tile's cap depends on what
    #: building (if any) sits on it — see ``world.utils.tile_item_capacity``:
    #:   * no building        -> ``room_capacity_empty``
    #:   * Vault / Extractor   -> ``room_capacity_per_storage_level`` x level
    #:   * any other building  -> ``room_capacity_building``
    #: Merging into an existing same-type drop does NOT add an object, so it is
    #: always allowed; only creating a NEW drop object is capped.
    #: An empty tile holds a single dropped item.
    room_capacity_empty: int = 1
    #: A tile with a non-storage/non-resource building holds up to this many.
    room_capacity_building: int = 10
    #: Per building-level cap for storage (Vault) and resource (Extractor)
    #: tiles: capacity = this x the building's level (e.g. 20 x Vault level).
    room_capacity_per_storage_level: int = 20
