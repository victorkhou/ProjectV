"""
Central game constants for the RTS Combat Overworld.

All tuning knobs, scaling factors, and magic numbers live here.
Import from this module instead of hardcoding values in system files.

Grouped by system:
- Rank / Level progression
- Agent training
- Resource harvesting & production
- Building scaling
- Combat
"""

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
#  Agent training
# ------------------------------------------------------------------ #

#: Base training cost per resource for agent #N (cost = base × N)
BASE_TRAINING_COST: dict[str, int] = {
    "Wood": 15,
    "Stone": 10,
    "Iron": 5,
}

#: Base training time in ticks (5 minutes at 1 tick/s)
BASE_TRAINING_TICKS = 300

#: Training time reduction per Academy level (15% per level)
ACADEMY_TRAINING_REDUCTION_PER_LEVEL = 0.15

#: Seconds between training progress messages
TRAINING_PROGRESS_INTERVAL = 5

# ------------------------------------------------------------------ #
#  Resource harvesting & production
# ------------------------------------------------------------------ #

#: Ticks between harvest yields (player active-presence)
HARVEST_COOLDOWN_TICKS = 4

#: Units yielded per harvest action on raw terrain
HARVEST_YIELD_PER_ACTION = 1

#: Multiplier when harvesting at an Extractor (vs raw terrain)
EXTRACTOR_HARVEST_MULTIPLIER = 6

#: Per-level bonus for Extractor production: base × (1 + BONUS × (level-1))
EXTRACTOR_LEVEL_BONUS = 0.25

#: Base Extractor storage capacity at level 1
EXTRACTOR_BASE_CAPACITY = 100

#: Additional Extractor storage per level above 1
EXTRACTOR_CAPACITY_PER_LEVEL = 50

#: Base Vault storage capacity at level 1
VAULT_BASE_CAPACITY = 100

#: Additional Vault storage per level above 1
VAULT_CAPACITY_PER_LEVEL = 20

# ------------------------------------------------------------------ #
#  Building scaling
# ------------------------------------------------------------------ #

#: Upgrade cost multiplier base: cost = base_cost × COST_BASE^(level-1)
UPGRADE_COST_BASE = 2

#: Upgrade time multiplier base: time = build_time × TIME_BASE^(level-1)
UPGRADE_TIME_BASE = 3

#: Seconds between construction progress messages
CONSTRUCTION_PROGRESS_INTERVAL = 5

#: Demolish refund rates by building level (fraction of invested cost)
DEMOLISH_REFUND_RATES: dict[int, float] = {
    1: 0.40,
    2: 0.50,
    3: 0.60,
    4: 0.70,
    5: 0.80,
}

#: Default refund rate for levels not in the table
DEMOLISH_REFUND_DEFAULT = 0.40

# ------------------------------------------------------------------ #
#  Combat
# ------------------------------------------------------------------ #

#: Per-level bonus for Turret damage: base × (1 + BONUS × (level-1))
TURRET_LEVEL_BONUS = 0.20
