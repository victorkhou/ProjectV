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
#  Rank / Level progression (early-game rebalance R14/D11)
# ------------------------------------------------------------------ #

#: Total number of ranks (Recruit through Marshal)
NUM_RANKS = 12

#: Maximum player level — 100-level hybrid-curve ladder (R14.1).
MAX_LEVEL = 100

#: Rank number → (min_level, max_level) — WIDENING bands over the 100-level
#: ladder (R14.5). The first three bands stay 5 levels wide so Corporal still
#: begins at L11 (preserving the Lab L11 / tech-gate alignment); later bands
#: widen so late ranks are long-term goals. Marshal is the L100 capstone.
RANK_BANDS: dict[int, tuple[int, int]] = {
    1: (1, 5),      # Recruit
    2: (6, 10),     # Private
    3: (11, 15),    # Corporal
    4: (16, 21),    # Sergeant
    5: (22, 28),    # Staff_Sergeant
    6: (29, 36),    # Lieutenant
    7: (37, 45),    # Captain
    8: (46, 56),    # Major
    9: (57, 69),    # Colonel
    10: (70, 84),   # Brigadier
    11: (85, 99),   # General
    12: (100, 100), # Marshal (capstone — only maxed players hold it)
}

#: LEGACY — uniform rank width, retired as progression math by the R14 band
#: table. Kept ONLY for the pre-band `rank_level → level` fallback mapping in
#: `world.utils.get_player_level` migration paths; do NOT use in new code.
LEVELS_PER_RANK = 5

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
#: ``throwable`` = grenades (thrown in a direction, land, then fuse); ``mine`` =
#: mines (armed in place via ``arm``, then fuse). Both are "bombs": a fused AoE
#: explosive placed on a tile, differing only in how they're deployed.
SUPPLY_CATEGORIES = ("ammo", "consumable", "throwable", "mine")

#: The two bomb families (fused AoE explosives). A ``throwable`` item is a
#: grenade; a ``mine`` item is a mine. Used to gate the ``throw`` vs ``arm``
#: commands and to label a live bomb. Kept separate from the category tuple so a
#: future non-bomb throwable/mine wouldn't silently become a bomb.
BOMB_CATEGORIES = ("throwable", "mine")

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

#: Default throw range (Chebyshev) for a throwable whose effect declares none.
DEFAULT_THROW_RANGE = 4

#: Minimum Chebyshev distance a RANDOM respawn tile must keep from any building,
#: so a "random location" spawn drops the player in open ground — not next to
#: (or camped by) a base. A best-effort constraint: if the sampler can't find a
#: tile this far from every building within its attempt budget, it relaxes to
#: any in-bounds tile rather than dead-ending.
RANDOM_SPAWN_MIN_BUILDING_DISTANCE = 20

#: Bomb fuse bounds (WALL-CLOCK seconds, == ticks at 1 tick/s) used when a bomb's
#: effect does not declare its own ``fuse_min``/``fuse_max``/``fuse_default``.
#: The ``set <bomb> <seconds>`` command clamps the requested fuse to
#: [fuse_min, fuse_max]; a bomb thrown/armed without a set fuse is rejected.
DEFAULT_BOMB_FUSE_MIN = 1
DEFAULT_BOMB_FUSE_MAX = 30
DEFAULT_BOMB_FUSE = 3

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


#: Resting activity-status strings — what an agent shows when it is stationed
#: (not mid-action). These are DERIVED from the agent's role/assignment by
#: ``world.utils.resting_activity_status``; no code path should write them by
#: hand. Transient, moment-to-moment statuses (e.g. ``"Harvesting Wood"``,
#: ``"Patrol blocked — retrying"``) are still set imperatively by the role
#: scripts each tick and supersede the resting default.
ACTIVITY_IDLE = "Idle"          # no role, or role with nothing to do
ACTIVITY_WORKING = "Working"    # assigned to a building (engineer/harvester/...)
ACTIVITY_READY = "Ready"        # army role (soldier/medic) on standby, no building
ACTIVITY_RESERVE = "Reserve"    # benched by an owner demotion
ACTIVITY_INCAPACITATED = "Incapacitated"  # downed; awaiting recovery

# ------------------------------------------------------------------ #
#  Player lifecycle state machine
# ------------------------------------------------------------------ #

#: The persisted lifecycle states a PLAYER character moves through (stored on
#: ``db.player_state``). Unlike the agent ``ACTIVITY_*`` strings — which are
#: DERIVED from role/assignment by ``world.utils.resting_activity_status`` — a
#: player's lifecycle state is a true persisted FSM: transitions are discrete
#: events (login route, enter, death, disconnect), not computable from other
#: fields. The single WRITER is ``world.player_lifecycle.transition``; no other
#: code path assigns ``db.player_state`` directly.
#:
#: Three transient session/account phases (connecting, authenticated) are NOT
#: persisted here — they live in Evennia's built-in session FSM. Only the states
#: a character can DWELL in between commands are persisted:
PLAYER_STATE_SPAWNING = "spawning"    # picking class + spawn location (OOC)
PLAYER_STATE_LOBBY = "lobby"          # waiting to enter game; enter/quit (OOC)
PLAYER_STATE_PLAYING = "playing"      # puppeted, in the game world
PLAYER_STATE_LINKDEAD = "linkdead"    # connection dropped w/o quit; grace timer

#: Every valid persisted player state (used to validate a stored value).
PLAYER_STATES = frozenset({
    PLAYER_STATE_SPAWNING,
    PLAYER_STATE_LOBBY,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_LINKDEAD,
})

#: Human-readable label per state, for the admin ``who`` State column. Kept
#: alongside the state values (the ``ACTIVITY_*`` precedent) rather than in the
#: NotificationPresenter — those formatters own event-driven notification lines
#: only, not command/table output.
PLAYER_STATE_LABELS = {
    PLAYER_STATE_SPAWNING: "Spawning",
    PLAYER_STATE_LOBBY: "Lobby",
    PLAYER_STATE_PLAYING: "Playing",
    PLAYER_STATE_LINKDEAD: "Linkdead",
}

#: Display label per combat-unit kind, shared by the owner-attributed notification
#: lines (NotificationSystem._unit_suffix and NotificationPresenter's unit-attacked
#: formatter) so the "Turret"/"Agent"/"Building" wording can't drift between them.
UNIT_KIND_LABELS = {"turret": "Turret", "agent": "Agent", "building": "Building"}

#: Allowed transitions: state -> set of states reachable from it. Encodes the
#: spec's transition table (the game-side dwell states only; the socket/auth
#: phases are Evennia's session FSM and route INTO these). ``None`` (a brand-new
#: character with no state yet) may enter any initial state via the login
#: router, so it is handled specially by the lifecycle module, not listed here.
PLAYER_STATE_TRANSITIONS = {
    # In spawning you pick class + location, then advance to the lobby; a
    # disconnect keeps you spawning (re-login resumes selection).
    PLAYER_STATE_SPAWNING: {PLAYER_STATE_LOBBY, PLAYER_STATE_SPAWNING},
    # From the lobby you Enter (→ playing) or Quit (stay lobby); linger on
    # disconnect.
    PLAYER_STATE_LOBBY: {PLAYER_STATE_PLAYING, PLAYER_STATE_LOBBY},
    # In game: quit → lobby, death → spawning, unclean drop → linkdead.
    PLAYER_STATE_PLAYING: {
        PLAYER_STATE_LOBBY, PLAYER_STATE_SPAWNING, PLAYER_STATE_LINKDEAD,
    },
    # Linkdead: reconnect resumes play, killed-during-grace → spawning,
    # grace-expiry (alive) → lobby.
    PLAYER_STATE_LINKDEAD: {
        PLAYER_STATE_PLAYING, PLAYER_STATE_SPAWNING, PLAYER_STATE_LOBBY,
    },
}

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
#:   - ``turret``: a defensive building that auto-fires at nearby non-owner
#:     players each tick (see ``CombatEngine.process_turrets``).
HARVESTABLE = "harvestable"
UPGRADABLE = "upgradable"
REQUIRES_RESOURCE_TERRAIN = "requires_resource_terrain"
STORAGE = "storage"
PRIMARY_STORAGE = "primary_storage"
HEADQUARTERS = "headquarters"
COMBAT_BARRIER = "combat_barrier"
TURRET = "turret"

BUILDING_CAPABILITIES: frozenset[str] = frozenset({
    HARVESTABLE,
    UPGRADABLE,
    REQUIRES_RESOURCE_TERRAIN,
    STORAGE,
    PRIMARY_STORAGE,
    HEADQUARTERS,
    COMBAT_BARRIER,
    TURRET,
})

# ------------------------------------------------------------------ #
#  Alliances
# ------------------------------------------------------------------ #

#: The ranks a player can hold within an alliance, ordered Leader > Officer >
#: Member. Stored on ``db.alliance_rank``. Structural: the permission matrix and
#: the strictly-lower-rank kick check branch on these values.
ALLIANCE_RANK_LEADER = "leader"
ALLIANCE_RANK_OFFICER = "officer"
ALLIANCE_RANK_MEMBER = "member"

ALLIANCE_RANKS = (ALLIANCE_RANK_LEADER, ALLIANCE_RANK_OFFICER, ALLIANCE_RANK_MEMBER)

#: Rank -> integer weight, higher = more authority. Used for the
#: strictly-lower-rank kick guard and the succession seniority order.
ALLIANCE_RANK_ORDER = {
    ALLIANCE_RANK_LEADER: 3,
    ALLIANCE_RANK_OFFICER: 2,
    ALLIANCE_RANK_MEMBER: 1,
}

#: The perk categories. At most ONE perk may be active per category at a time
#: (no same-category stacking). Each maps to a concrete gameplay hook:
#:   - ``shared_vision``: fog-of-war union across PLAYING allies
#:   - ``shared_regen``: HP-regen multiplier for members
#:   - ``harvest_boost``: extractor active-presence yield multiplier
#:   - ``combat_damage``: flat additive damage bonus
#:   - ``combat_armor``: flat additive damage reduction
ALLIANCE_PERK_CATEGORIES = (
    "shared_vision",
    "shared_regen",
    "harvest_boost",
    "combat_damage",
    "combat_armor",
)

#: Reserved substrings an alliance name/tag may not contain (case-insensitive,
#: post-NFKC-normalization). Blocks impersonation of staff/system channels and
#: collisions with the reserved global chat channel key/aliases (Public/chat/pub).
ALLIANCE_NAME_DENYLIST = ("admin", "system", "staff", "public", "chat", "pub")

#: Sentinel stored in ``db.alliance_invite_ignore`` meaning "block ALL invites".
ALLIANCE_IGNORE_ALL = "all"

# ------------------------------------------------------------------ #
#  Deeds (early-game rebalance R9/D9)
# ------------------------------------------------------------------ #

#: Deed ids awarded by BASE_ELIMINATED (per NPC-base tier).
DEED_OUTPOST_CLEARED = "outpost_cleared"
DEED_FORTRESS_CLEARED = "fortress_cleared"

#: Human-readable descriptions for deed-gate refusal messages and the
#: [LOCKED: ...] suffix in the building list.
DEED_DESCRIPTIONS = {
    DEED_OUTPOST_CLEARED: "destroyed an NPC outpost",
    DEED_FORTRESS_CLEARED: "destroyed an NPC fortress",
}

# ------------------------------------------------------------------ #
#  Disconnect cleanup
# ------------------------------------------------------------------ #

#: Building types whose contents survive player disconnect.
#: Buildings with a `building_type` in this set are skipped during
#: the quit-cleanup loop in `CombatCharacter.at_pre_unpuppet`.
#: To protect a new storage building, add its two-letter abbreviation
#: here (e.g. "SB" for a future Storage Bunker).
PROTECTED_BUILDING_TYPES: set[str] = {"VT"}
