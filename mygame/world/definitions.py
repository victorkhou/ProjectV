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
    #: Optional deed-gate (R9): if set, the player must have this deed in
    #: db.deeds at or above unlock_deed_count to construct the building.
    unlock_deed: str | None = None
    #: Required deed count for the unlock_deed gate (default 1 = boolean).
    unlock_deed_count: int = 1

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
    damage_type: str = "physical"  # physical|fire|psychic|blast (Phase 3)
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
    """Definition for a selectable player class (state 3.2 + Phase 5 substrate).

    Each class is a **sidegrade**: a strength paired with a weakness, never a
    power tier. The ``stat_modifiers`` dict applies flat additive bonuses or
    penalties to the player's combat stats (read in CombatEngine via a class-
    modifier accessor). A class with +damage always pays with -HP/-accuracy/etc.

    ``key`` persists on ``db.player_class``; ``name`` + ``description`` drive
    the selection menu and score display.
    """

    key: str
    name: str
    description: str = ""
    stat_modifiers: dict[str, float] = field(default_factory=dict)


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
    #: Loot per resource: fixed int or [min, max] range drawn uniformly (R8.1).
    loot: dict = field(default_factory=dict)
    #: Per-guard-kill mini-drop chance (R8.2); overrides balance default.
    guard_loot_chance: float | None = None
    #: Mini-drop amount: fixed int or [min, max] range.
    guard_loot_amount: int | list | None = None
    #: Gear-drop chance on HQ destruction (R8.3); overrides balance default.
    gear_drop_chance: float | None = None
    #: Rare gear-drop chance on HQ destruction (R8.4).
    rare_gear_chance: float | None = None
    #: Item keys eligible for the gear roll.
    gear_pool: list[str] = field(default_factory=list)
    #: Item keys eligible for the rare roll.
    rare_pool: list[str] = field(default_factory=list)


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

    turret_damage: int = 15
    turret_radius: int = 10
    xp_kill: int = 100
    xp_building_destroy: int = 50
    xp_death_loss: int = 50
    # --- Economy XP (early-game rebalance R1) ---
    xp_build_complete: int = 30
    xp_upgrade_complete: int = 30
    xp_harvest_action: int = 1
    xp_agent_trained: int = 40
    gather_amount: int = 1
    player_default_health: int = 100
    resource_respawn_ticks: int = 30
    combat_lockout_ticks: int = 5
    #: Chip-floor fraction: a landed hit always deals at least this fraction of
    #: its raw output (weapon damage + attacker bonus), so armor can never grant
    #: total immunity — it caps at absorbing (1 - fraction). Default 0.5 bounds
    #: effective HP from armor at ~2x. Set to 0 to disable (revert to a plain
    #: max(0, …) floor). See CombatEngine._calculate_damage.
    chip_damage_min_fraction: float = 0.5
    # --- Rank-gap PvP protection (anti-ganking, new-player protection) --- #
    #: When an attacking player outranks their player target by at least
    #: rank_gap_penalty_threshold LEVELS, the attacker's outgoing damage AND the
    #: kill XP/loot are scaled down — UNLESS the lower-ranked player initiated
    #: (struck first this engagement). Protects new players from being farmed by
    #: veterans without removing self-defense or consensual PvP. The damage
    #: multiplier drops linearly with the gap from 1.0 to rank_gap_min_damage_mult
    #: (never 0 — a defender can always be hurt/killed). Set threshold to 0 to
    #: disable the whole mechanic. See CombatEngine._rank_gap_damage_mult.
    rank_gap_penalty_threshold: int = 10   # level gap that starts the penalty
    rank_gap_full_penalty_span: int = 30   # extra gap over threshold → min mult
    rank_gap_min_damage_mult: float = 0.25  # damage floor multiplier at max gap
    rank_gap_xp_loot_mult: float = 0.25    # kill XP/loot kept on a lopsided kill
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
    #: Building repair rate, as a PERCENT of max HP restored per tick, and (by
    #: the same fraction) the per-tick resource cost as a percent of the
    #: building's cumulative investment — its base build cost PLUS every upgrade
    #: cost up to its current level. Repair is active-presence like construction
    #: (stay on the tile, or an Engineer works it). Default 5%/tick → 20 ticks
    #: from 0 to full, and a full repair costs 100% of what was invested.
    #: Buildings don't passively heal (unlike players/agents) — repair is the
    #: only way to restore building HP.
    repair_hp_percent_per_tick: float = 5.0
    #: 'disarm' — a multi-tick attempt on a ticking bomb. The bomb's own fuse
    #: keeps counting down during the attempt (it explodes if the fuse hits 0
    #: first). After ``bomb_disarm_ticks_min..max`` ticks a single roll decides:
    #: with probability ``bomb_disarm_base_success`` (default 0.7) the bomb is
    #: safely removed, else it detonates immediately. Future tech / equipment /
    #: class bonuses raise the chance (clamped to [0,1]) and shorten the ticks.
    bomb_disarm_base_success: float = 0.7
    bomb_disarm_ticks_min: int = 2
    bomb_disarm_ticks_max: int = 10
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
    #: Base training time in ticks (90s at 1 tick/s — early-game R3.3).
    base_training_ticks: int = 90
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
    #: log rule). Defaults to 30 minutes (1800s) — far longer than the ~60s combat
    #: timer (``COMBAT_TIMER_DURATION`` ticks), so pulling the plug mid-fight can't
    #: dodge an active fight (the dropped body outlives any combat timer). Only
    #: used when the lobby lifecycle flow is enabled.
    linkdead_grace_seconds: float = 1800.0

    # --- NPC base spawner + elimination (PvE NPC bases, Phase 5) ------ #
    #: XP awarded for destroying an NPC base's HQ (the whole base is wiped) —
    #: far more than xp_building_destroy=50, so raiding is decisively rewarding.
    xp_hq_destroy: int = 300
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

    # --- Alliances -------------------------------------------------------- #
    #: Minimum Entity_Level to FOUND an alliance.
    alliance_found_min_level: int = 10
    #: Minimum Entity_Level to JOIN an alliance (accept/apply/open-join).
    alliance_join_min_level: int = 5
    #: Max members (leader + officers + members) an alliance may hold.
    alliance_max_members: int = 10
    #: Max officers a Leader may promote (keeps withdraw/kick privilege scarce).
    alliance_max_officers: int = 3
    #: Max characters in an alliance tag.
    alliance_tag_max_len: int = 5
    #: Days a Leader must be offline before an Officer may `claim` leadership
    #: (judged on-demand from last-seen data; no timer).
    alliance_leader_absence_days: int = 7
    #: Days a pending invite lives before it expires and is purged.
    alliance_invite_expiry_days: int = 7
    #: Ticks an inviter must wait between invites to the SAME target (and after
    #: that target declines) — anti-harassment throttle.
    alliance_invite_cooldown_ticks: int = 600
    #: Ticks a player must wait after leaving/being kicked before joining any
    #: alliance again (blunts serial-hop-for-fog-intel).
    alliance_rejoin_cooldown_ticks: int = 1800
    #: Ticks between a Leader's rename/retag operations.
    alliance_rename_cooldown_ticks: int = 3600
    #: Per-window cap on the quantity of any single resource an OFFICER may
    #: withdraw from the treasury (a Leader withdraw bypasses the cap).
    alliance_withdraw_cap_per_window: int = 500
    #: Length (ticks) of the rolling withdrawal-cap window.
    alliance_withdraw_window_ticks: int = 3600
    #: Max rows the cross-alliance leaderboard shows.
    alliance_leaderboard_top_n: int = 20
    #: Composite-score weights. Score per member = level * w_level +
    #: decayed_pvp_kills * w_kills_pvp + decayed_pve_kills * w_kills_pve +
    #: buildings * w_buildings. PvP outweighs PvE. First-guess, tune live.
    alliance_score_w_level: float = 1.0
    alliance_score_w_kills_pvp: float = 3.0
    alliance_score_w_kills_pve: float = 1.0
    alliance_score_w_buildings: float = 1.5
    #: Leaderboard kill-tally decay: on each read/increment a tally is multiplied
    #: by factor ** (elapsed_ticks / decay_interval_ticks), so old kills fade and
    #: the board reflects recent activity. factor in (0, 1]; 1.0 disables decay.
    alliance_score_decay_factor: float = 0.98
    alliance_score_decay_interval_ticks: int = 600
    #: Alliance_Level tier table: SUM of member Entity_Levels -> tier (1..N).
    #: Keys are the minimum summed member level for that tier. Chosen over an
    #: average so a bigger active alliance climbs faster; the top threshold is
    #: reachable by a realistic mid-size roster, NOT the ~600 theoretical max.
    #: The number of tiers here is the MAX Alliance_Level. First-guess; tune live.
    alliance_level_thresholds: dict[int, int] = field(default_factory=lambda: {
        0: 1, 40: 2, 100: 3, 180: 4, 280: 5,
    })

    # --- Progression curve tunables (R14/D11) -------------------------- #
    #: L1→L2 XP delta anchor.
    xp_curve_base_delta: int = 40
    #: Per-level growth factor for levels 2..knee_level (+20%).
    xp_curve_early_ratio: float = 1.2
    #: Per-level growth factor for levels above knee_level (+5%).
    xp_curve_late_ratio: float = 1.05
    #: Level at which the growth rate transitions from early to late.
    xp_curve_knee_level: int = 20

    # --- Variable rewards (early-game rebalance R7, R8) --------------- #
    #: Chance per manual harvest action of a "Rich vein!" critical (×5 yield).
    harvest_crit_chance: float = 0.05
    #: Yield multiplier on a harvest crit.
    harvest_crit_multiplier: int = 5
    #: Chance per NPC-guard kill of a resource mini-drop.
    guard_loot_chance: float = 0.4
    #: Resource units dropped per guard mini-drop.
    guard_loot_amount: int = 10
    #: Chance of a gear drop on NPC-HQ destruction (outpost default).
    gear_drop_chance: float = 0.15
    #: Chance of a rare gear drop on NPC-HQ destruction (outpost default).
    rare_gear_chance: float = 0.03

    # --- Agent rebalance (R3, R5) ------------------------------------- #
    #: Fog-of-war vision radius around patrolling scout agents.
    scout_vision_radius: int = 5

    # --- Shield Generator (defensive building) ------------------------ #
    # A Shield Generator projects a regenerating damage-absorbing shield onto
    # the owner's buildings within a Chebyshev radius, scaled by its level:
    #   radius       = shield_base_radius + (level - 1)          (L1 = 2 → 5x5)
    #   shield_frac  = shield_hp_fraction * level                (L1 = 25% of the
    #                  covered building's hp_max; L4 = 100%)
    # Overlapping generators do NOT stack — a building takes the single largest
    # covering shield (ShieldSystem uses max). Shields regenerate
    # shield_regen_percent of shield_max every shield_regen_interval_ticks.
    shield_base_radius: int = 2          # Chebyshev radius at level 1 (5x5 area)
    shield_hp_fraction: float = 0.25     # shield = frac * level * building hp_max
    shield_regen_percent: float = 1.0    # % of shield_max restored per interval
    shield_regen_interval_ticks: int = 5 # ticks between shield-regen ticks

    # --- Damage types (§3, Phase 3) ---------------------------------------- #
    #: Baseline typed-resist given to ALL players at spawn (flat points). Ensures
    #: new players have token protection against fire/psychic/blast so typed
    #: damage isn't a veteran-only wall. Small (2–3 is appropriate at the current
    #: weapon scale). Applies to all non-physical types equally.
    baseline_resist: float = 2.0
    #: Fire burn DoT: fraction of the hit's raw damage dealt per tick as burn.
    fire_burn_fraction: float = 0.2  # 20% of raw per burn tick
    #: Number of ticks the burn lasts after a fire hit.
    fire_burn_ticks: int = 3
    #: Blast armor-shred: flat DR removed from the target per blast hit.
    #: Stacks additively; makes subsequent physical hits hurt more.
    blast_shred_per_hit: int = 5
    #: Blast armor-shred recovery: DR restored per tick (shred is not permanent —
    #: it decays so a target recovers after the blast assault ends).
    blast_shred_decay_per_tick: int = 1

    #: Aggregate permanent-bonus cap (§2a anti-snowball): the maximum total flat
    #: bonus from tech + alliance perks on the DAMAGE axis. Gear bonus is
    #: UNCAPPED (loseable power). 0 = no cap (disabled). At the current weapon
    #: scale (10–50 base), a cap of 6 keeps the permanent edge under ~25% of
    #: base and well below the 2× violation threshold.
    perm_bonus_cap_damage: float = 6.0
    #: Same cap for the DEFENSE (damage_reduction) axis.
    perm_bonus_cap_dr: float = 6.0

    # --- Cross-planet travel (§7) ---------------------------------------- #
    #: Cooldown in ticks between successive launches from a pad.
    travel_cooldown_ticks: int = 300       # 5 min at 1 tick/s
    #: Reduced cooldown when you own a base (HQ/Beacon) on both endpoints.
    travel_cooldown_owned_ticks: int = 120  # 2 min — rewards established routes
    #: Manifest carry-weight capacity per Launch Pad level.
    travel_manifest_weight_per_level: int = 200  # L1=200, L5=1000
    #: Extra fuel cells consumed per agent in the manifest.
    travel_fuel_per_agent: int = 1
    #: Base fuel cells consumed per single-rung hop (Basic Fuel).
    travel_fuel_per_hop: int = 1
