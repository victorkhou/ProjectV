"""
Central game constants for the RTS Combat Overworld.

This module holds *structural* constants — values that define code contracts,
validation bounds, enums, and identities rather than pure game balance.
Import from here instead of hardcoding values in system files.

Hot-tunable *balance* numbers (training/harvest/capacity/upgrade/turret/
demolish scaling, XP awards, vision radii, …) live in
``world.definitions.BalanceConfig`` and ``data/config/balance.yaml`` so they
can be retuned via ``@reload`` without a restart. When adding a new value, ask:
does changing it alter validation/logic (→ here) or just tuning (→ balance)?

Grouped by system:
- Rank / Level progression
- Agent training (message cadence only)
- Building scaling (message cadence only)
- Combat
- NPC movement & Agent AI
- Agent state enums / status strings
"""

from enum import StrEnum

# ------------------------------------------------------------------ #
#  Rank / Level progression
# ------------------------------------------------------------------ #

#: Total number of ranks (Recruit through Marshal)
NUM_RANKS = 12

#: Player levels per rank
LEVELS_PER_RANK = 5

#: Maximum player level (NUM_RANKS × LEVELS_PER_RANK)
MAX_LEVEL = NUM_RANKS * LEVELS_PER_RANK

#: XP interval per level within the final rank (no next rank to interpolate)
FINAL_RANK_XP_PER_LEVEL = 10_000

#: Evennia's Limbo room ID (used to detect first-login characters)
LIMBO_ROOM_ID = 2

# ------------------------------------------------------------------ #
#  Resources
# ------------------------------------------------------------------ #

#: Canonical set of resource identifiers. Single source of truth, shared by
#: player defaults (``typeclasses.characters`` re-exports this) and the data
#: registry's cross-validation (which rejects any building/item/tech/terrain
#: reference to a resource name outside this set). Structural, not balance:
#: adding a resource here changes what the schema accepts.
RESOURCE_TYPES: tuple[str, ...] = (
    "Wood", "Stone", "Iron",
    "Energy", "Circuits", "Nexium",
)

# ------------------------------------------------------------------ #
#  Equipment & Items
# ------------------------------------------------------------------ #

#: The eleven canonical equipment slots (nine armor-bearing body slots plus
#: ``weapon`` and ``accessory``). Single source of truth for the slot
#: vocabulary: the schema validator requires every Gear item's ``slot`` to be a
#: member of this tuple, and the equipment system rejects equipping into any
#: slot outside it. Structural, not balance: adding a slot is a constant edit.
EQUIPMENT_SLOTS = ("head", "eyes", "face", "torso", "arms", "hands",
                   "legs", "feet", "back", "weapon", "accessory")

#: Item categories stored as unique Game_Item objects in ``db.equipment_slots``
#: (one per slot).
GEAR_CATEGORIES = ("armor", "weapon", "accessory")

#: Item categories stored as counted stacks in the Supply_Bag ``db.supplies``.
SUPPLY_CATEGORIES = ("ammo", "consumable", "throwable")

#: The full controlled vocabulary of item categories. The schema validator
#: rejects any item whose ``category`` is outside this set.
ITEM_CATEGORIES = GEAR_CATEGORIES + SUPPLY_CATEGORIES

#: Valid ``weapon_type`` values for a ``weapon``-category item.
WEAPON_TYPES = ("melee", "ranged")

#: Stat keys that aggregate across gear via ``get_stat_total()``.
#: NB: the ``carry_capacity`` GEAR STAT (a weight delta added to the limit) is
#: unrelated to the per-agent ``npc.db.carry_capacity`` delivery-load COUNT
#: budget in agent_scripts.py — same word, different unit and owner.
AGGREGATED_STATS = ("damage_reduction", "damage_bonus", "move_speed",
                    "sight_range", "carry_capacity", "max_hp", "accuracy")

#: Valid Item_Effect ``type`` values for consumable/throwable items.
#: NOT data-only: a new effect needs this tuple + a validator rule + a
#: use/throw branch + (usually) a presenter kind. The three mechanics are
#: genuinely different; a handler-registry would only relocate the branch, not
#: remove it. See COMPLEXITY_REVIEW touchpoint row.
EFFECT_TYPES = ("heal", "buff", "aoe_damage")

#: Base carry weight (weight units); a holder's limit is
#: ``BASE_CARRY_WEIGHT + Σ carry_capacity(gear)``. Structural (it gates the
#: carry-limit correctness property), so it lives here rather than in balance.
BASE_CARRY_WEIGHT = 1000

#: Per-unit weight for a resource absent from ``BalanceConfig.resource_weights``.
DEFAULT_RESOURCE_WEIGHT = 1.0

#: Default throw range (Manhattan) for a throwable whose effect declares none.
DEFAULT_THROW_RANGE = 4

# ------------------------------------------------------------------ #
#  Agent training
# ------------------------------------------------------------------ #

#: Seconds between training progress messages
TRAINING_PROGRESS_INTERVAL = 5

# NOTE: Training *balance* (base cost, base ticks, per-level reduction) now
# lives in ``BalanceConfig`` (data/config/balance.yaml) so it is hot-tunable
# via @reload. See ``world.definitions.BalanceConfig``.

# ------------------------------------------------------------------ #
#  Building scaling
# ------------------------------------------------------------------ #

#: Seconds between construction progress messages
CONSTRUCTION_PROGRESS_INTERVAL = 5

#: Maximum level any building can be upgraded to. Structural bound: it caps the
#: ``max_level`` a definition may declare and gates the upgrade path. (The
#: *cost/time* of each upgrade is balance and lives in ``BalanceConfig``.)
MAX_BUILDING_LEVEL = 5

# NOTE: Resource/harvest, storage-capacity, upgrade-scaling, turret-bonus and
# demolish-refund balance now live in ``BalanceConfig``
# (data/config/balance.yaml), hot-tunable via @reload.

# ------------------------------------------------------------------ #
#  Combat
# ------------------------------------------------------------------ #

#: Ticks a player's combat timer runs after a COMBAT_ACTION event. While
#: active, the player cannot pass through Walls (set by world.combat_timer).
COMBAT_TIMER_DURATION = 60

# ------------------------------------------------------------------ #
#  NPC Movement & Agent AI
# ------------------------------------------------------------------ #

#: movement_delay = ticks between steps. 1 = fastest (every tick),
#: 2 = every other tick. Higher value = slower movement.
#: Named "delay" not "speed" to avoid the counterintuitive
#: "higher speed = slower" confusion.

#: Default movement delay for all NPCs (every tick — fastest)
DEFAULT_MOVEMENT_DELAY = 1

#: Scout movement delay (fastest)
SCOUT_MOVEMENT_DELAY = 1

#: Harvester movement delay when carrying resources (every 2 ticks)
HARVESTER_LADEN_DELAY = 2

#: Harvester movement delay when returning empty (every tick)
HARVESTER_EMPTY_DELAY = 1

#: A* node expansion limit
MAX_PATHFINDING_NODES = 500

#: Maximum pathfinding requests processed per tick
MAX_PATHS_PER_TICK = 10

#: Minimum waypoints in a patrol route
MIN_PATROL_WAYPOINTS = 2

#: Maximum waypoints in a patrol route
MAX_PATROL_WAYPOINTS = 10

#: Default resource carry capacity for harvesters (resource units)
DEFAULT_CARRY_CAPACITY = 50

#: Base movement lag (ticks between steps) applied to a PLAYER while in the
#: combat state (``combat_timer_expires`` in the future). Out of combat,
#: player movement is always instant (this lag does not apply). Equipment
#: ``move_speed`` alleviates the lag via ``compute_effective_delay`` — the same
#: equipment-derived mechanism agents use for their per-tick movement delay.
COMBAT_MOVE_LAG_TICKS = 2


def compute_effective_delay(base_delay: int, speed_modifier: int) -> int:
    """Compute effective movement delay accounting for an equipment speed modifier.

    A positive ``speed_modifier`` reduces the delay (makes the NPC faster).
    The result is clamped to a minimum of 1 (every-tick movement) so a large
    modifier can never stop or reverse movement.

    Args:
        base_delay: The NPC's base ``movement_delay`` (>= 1).
        speed_modifier: Sum of ``move_speed`` stat modifiers from equipped items.

    Returns:
        Effective delay: ``max(1, base_delay - speed_modifier)``.

    Notes:
        Used by ``NPC.advance_movement`` (per-tick agent stepping) and by
        ``CmdMove`` for the in-combat player movement lag.
    """
    return max(1, base_delay - speed_modifier)


# ------------------------------------------------------------------ #
#  Agent state enums / status strings
# ------------------------------------------------------------------ #


class DeliveryState(StrEnum):
    """Finite states for the harvester delivery FSM.

    ``StrEnum`` members compare equal to their plain-string value
    (``DeliveryState.IDLE == "idle"``) and serialize to that string, so
    Evennia attribute persistence is unaffected and legacy stored values
    remain compatible.
    """

    IDLE = "idle"
    PICKING_UP = "picking_up"
    DELIVERING = "delivering"
    RETURNING = "returning"


#: Default/idle activity status shown in agent rosters and the map view.
ACTIVITY_IDLE = "Idle"

# ------------------------------------------------------------------ #
#  Building capabilities
# ------------------------------------------------------------------ #

#: The controlled vocabulary of building *capability* flags. A building
#: declares zero or more of these in ``buildings.yaml`` (``capabilities: [...]``)
#: and game code branches on the capability rather than on a hardcoded
#: abbreviation (``if bdef.abbreviation == "EX"`` → ``if bdef.has_capability(
#: HARVESTABLE)``). This keeps building behavior data-driven: adding a building
#: that harvests, stores, or blocks movement is a YAML edit, not a code change
#: scattered across systems. The schema validator rejects any capability not in
#: this set, so typos fail at load time.
#:
#: Meaning of each flag:
#:   - ``harvestable``: a resource-producing Extractor. Harvester agents target
#:     it, it produces on a tick cooldown, and its inventory is cleared on the
#:     owner's disconnect.
#:   - ``upgradable``: may be upgraded to a higher level (raises output/capacity).
#:   - ``requires_resource_terrain``: must be placed on a tile that has a
#:     resource (enforced at construction).
#:   - ``storage``: a valid drop-off for a Harvester delivering resources.
#:   - ``primary_storage``: a *dedicated* store, preferred over other ``storage``
#:     buildings as a delivery target on a distance tie.
#:   - ``headquarters``: the player's HQ — limited to one per planet and the
#:     prerequisite that satisfies other buildings' ``requires_hq``.
#:   - ``combat_barrier``: a Wall that blocks its own owner from passing while
#:     the owner has an active combat timer.
HARVESTABLE = "harvestable"
UPGRADABLE = "upgradable"
REQUIRES_RESOURCE_TERRAIN = "requires_resource_terrain"
STORAGE = "storage"
PRIMARY_STORAGE = "primary_storage"
HEADQUARTERS = "headquarters"
COMBAT_BARRIER = "combat_barrier"

BUILDING_CAPABILITIES: frozenset[str] = frozenset({
    HARVESTABLE,
    UPGRADABLE,
    REQUIRES_RESOURCE_TERRAIN,
    STORAGE,
    PRIMARY_STORAGE,
    HEADQUARTERS,
    COMBAT_BARRIER,
})

# ------------------------------------------------------------------ #
#  Disconnect cleanup
# ------------------------------------------------------------------ #

#: Building types whose contents survive player disconnect.
#: Buildings with a `building_type` in this set are skipped during
#: the quit-cleanup loop in `CombatCharacter.at_pre_unpuppet`.
#: To protect a new storage building, add its two-letter abbreviation
#: here (e.g. "SB" for a future Storage Bunker).
PROTECTED_BUILDING_TYPES: set[str] = {"VT"}
