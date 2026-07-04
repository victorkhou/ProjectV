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
        Validates agent-ai Requirement 8.8. Used by ``NPC.advance_movement``.
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
#  Disconnect cleanup
# ------------------------------------------------------------------ #

#: Building types whose contents survive player disconnect.
#: Buildings with a `building_type` in this set are skipped during
#: the quit-cleanup loop in `CombatCharacter.at_pre_unpuppet`.
#: To protect a new storage building, add its two-letter abbreviation
#: here (e.g. "SB" for a future Storage Bunker).
PROTECTED_BUILDING_TYPES: set[str] = {"VT"}
