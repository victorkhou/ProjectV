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

#: Maximum player level — 100-level hybrid-curve ladder.
MAX_LEVEL = 100

#: Rank number → (min_level, max_level). The first three bands are 5 levels
#: wide (Corporal starts at L11 for tech-gate alignment); later bands widen
#: so late ranks are long-term goals. Marshal is the L100 capstone.
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

#: Evennia's Limbo room ID (used to detect first-login characters)
LIMBO_ROOM_ID = 2

# ------------------------------------------------------------------ #
#  Resources
# ------------------------------------------------------------------ #

#: Canonical set of resource identifiers. Single source of truth shared by
#: player defaults and data registry cross-validation (rejects any reference
#: to a resource name outside this set). Adding a resource here changes what
#: the schema accepts.
RESOURCE_TYPES: tuple[str, ...] = (
    "Wood", "Stone", "Iron",
    "Energy", "Circuits", "Nexium",
    "Biomass", "Cryogen", "Magmite", "Aether",
)

# ------------------------------------------------------------------ #
#  Equipment & Items
# ------------------------------------------------------------------ #

#: The twelve canonical equipment slots. Single source of truth for the slot
#: vocabulary: the schema validator requires every Gear item's ``slot`` to be
#: a member of this tuple, and the equipment system rejects equipping outside it.
EQUIPMENT_SLOTS = ("head", "eyes", "face", "torso", "arms", "hands",
                   "legs", "feet", "back", "weapon_melee", "weapon_ranged",
                   "accessory")

#: Compatibility vocabulary for the pre-split equipment model. Persisted
#: ``GameItem.slot`` attributes and character ``equipment_slots`` mappings may
#: still contain ``weapon`` after an upgrade; runtime migration maps that key
#: through this table without replacing the item object.
LEGACY_WEAPON_SLOT = "weapon"
WEAPON_SLOT_BY_TYPE = {
    "melee": "weapon_melee",
    "ranged": "weapon_ranged",
}

#: Human-readable labels for the equipment slots, for player-facing text
#: (equip/unequip notifications, the paperdoll) — the raw keys read fine as
#: command arguments (``unequip weapon_melee``) but not as prose.
EQUIPMENT_SLOT_LABELS = {
    "head": "head",
    "eyes": "eyes",
    "face": "face",
    "torso": "torso",
    "arms": "arms",
    "hands": "hands",
    "legs": "legs",
    "feet": "feet",
    "back": "back",
    "weapon_melee": "melee weapon",
    "weapon_ranged": "ranged weapon",
    "accessory": "accessory",
}

#: Item categories stored as unique Game_Item objects in ``db.equipment_slots``
#: (one per slot).
GEAR_CATEGORIES = ("armor", "weapon", "accessory")

#: Item categories stored as counted stacks in the Supply_Bag ``db.supplies``.
#: ``throwable`` = grenades (thrown, land, fuse); ``mine`` = armed in place, fuse.
#: Both are "bombs": a fused AoE explosive differing only in deployment method.
#: ``insert`` = Blacksmith weapon-mod consumables consumed by the ``insert``
#: command. Stacks in the Supply_Bag (not Gear); not usable via ``use`` or
#: ``throw``.
SUPPLY_CATEGORIES = ("ammo", "consumable", "throwable", "mine", "insert")

#: The two bomb families (fused AoE explosives). A ``throwable`` is a grenade;
#: a ``mine`` is a mine. Gates the ``throw`` vs ``arm`` commands.
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
#: A new effect needs this tuple + a validator rule + a use/throw branch +
#: (usually) a presenter kind.
EFFECT_TYPES = ("heal", "buff", "aoe_damage")

#: Base carry weight (weight units); a holder's limit is
#: ``BASE_CARRY_WEIGHT + Σ carry_capacity(gear)``.
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

#: Raised HP ceiling granted to staff (Builder+) characters. Applied on login
#: (``CombatCharacter._ensure_admin_health``) so a staff operator can wade into
#: combat while testing without being downed by normal damage. Ordinary players
#: keep ``BalanceConfig.player_default_health`` (100). Not a balance tunable —
#: it's a fixed operator affordance, so it lives here, not in balance.yaml.
#: Ordinary players keep ``BalanceConfig.player_default_health`` (500).
ADMIN_BASE_HEALTH = 1000

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


def compute_combat_move_lag(base: int, move_speed: int, terrain_mod: float) -> int:
    """Player in-combat movement lag: ``max(0, int(base - move_speed - terrain_mod))``.

    Zero-floored: unlike :func:`compute_effective_delay` (which floors at 1 for
    agents), a fast, favorably-positioned player may move again on the same tick.
    ``int()`` truncates toward zero, so a fractional terrain modifier never
    grants more relief than a full tick.

    Args:
        base: Base combat movement lag in ticks (``COMBAT_MOVE_LAG_TICKS``).
        move_speed: Sum of ``move_speed`` stat modifiers from equipped items.
        terrain_mod: Destination tile's resolved terrain Movement_Modifier
            (positive reduces lag, negative increases it).

    Returns:
        Effective lag in whole ticks, floored at zero.

    Notes:
        Used by the player movement gate (``CmdMove``) only. Agents keep
        :func:`compute_effective_delay` and its minimum-1 floor — do not merge
        the two helpers.
    """
    return max(0, int(base - move_speed - terrain_mod))


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

#: Display label per combat-unit kind, shared by owner-attributed notification
#: lines so the "Turret"/"Agent"/"Building" wording can't drift between them.
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
    # From the lobby you Enter (→ playing), return to spawning when a selected
    # deployment target disappears, or Quit (stay lobby); linger on disconnect.
    PLAYER_STATE_LOBBY: {
        PLAYER_STATE_PLAYING, PLAYER_STATE_SPAWNING, PLAYER_STATE_LOBBY,
    },
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
#:   - ``shield_generator``: projects a regenerating damage-absorbing shield onto
#:     the owner's buildings within a level-scaled radius (see ``ShieldSystem``).
HARVESTABLE = "harvestable"
UPGRADABLE = "upgradable"
REQUIRES_RESOURCE_TERRAIN = "requires_resource_terrain"
STORAGE = "storage"
PRIMARY_STORAGE = "primary_storage"
HEADQUARTERS = "headquarters"
COMBAT_BARRIER = "combat_barrier"
TURRET = "turret"
SHIELD_GENERATOR = "shield_generator"
#: A building that serves as its owner's respawn point on its planet AND
#: recovers a level-scaled fraction of carried items/resources on death.
RESPAWN_POINT = "respawn_point"

#: A Launch Pad enables cross-planet travel: the player ``launch``es from here,
#: consuming fuel, routing through Space, and arriving at their Beacon/HQ on the
#: destination planet (or public spawn). Also supports manifest loading
#: (agents/cargo for cross-planet transport).
LAUNCH_PAD = "launch_pad"

#: The Blacksmith gear bench. Bench commands (``insert``/``reroll``/``salvage``)
#: locate the building the player is standing in. Does NOT produce items.
#: Usage gates on ownership + operational status.
BLACKSMITH = "blacksmith"

#: The Refinery resource converter. ``refine <resource> <amount>`` converts a
#: carried resource into Salvage at a building-level-scaled rate. The conversion
#: NEVER outputs Nexium or any other resource (anti-loop). Usage gates on
#: ownership + operational status.
RESOURCE_CONVERTER = "resource_converter"

#: The Sniper Nest range aura. While the building's OWNER stands on its tile,
#: grants a level-scaled weapon +range: ``1 + (level - 1) // 2`` → L1 +1,
#: L3 +2, L5 +3. Strictly ON-TILE and OWNER-ONLY. Clamped by max_weapon_range.
RANGE_AURA = "range_aura"

#: The Watchtower vision aura. While the building's OWNER stands on its tile,
#: grants a level-scaled sight_range bonus: ``1 + (level - 1) // 2`` → L1 +1,
#: L3 +2, L5 +3. Strictly ON-TILE and OWNER-ONLY.
VISION_AURA = "vision_aura"

#: The Field Hospital heal aura. While the building's OWNER (or owner's agent)
#: stands on its tile, grants a flat heal-over-time bonus per regen interval:
#: ``1 + (level - 1) // 2`` extra HP → L1 +1, L3 +2, L5 +3. Additive (not
#: scaled by regen_multiplier). Strictly ON-TILE and OWNER-ONLY.
HEAL_AURA = "heal_aura"

#: The Survey Array outpost-triangulation bench. Standing in your own
#: operational array, ``survey`` searches for an enemy NPC outpost on the
#: current planet, returning a search box the player narrows via successive
#: probes until the tile is pinpointed. Building LEVEL tightens the opening
#: box. Usage gates on ownership + operational status.
OUTPOST_SURVEY = "outpost_survey"

#: A research lab that hosts ONE technology tree. Research is gated on
#: OWNERSHIP, not location; a player may own only one lab per planet, so
#: choosing a tree is a strategic per-planet commitment.
RESEARCH_LAB = "research_lab"

BUILDING_CAPABILITIES: frozenset[str] = frozenset({
    HARVESTABLE,
    UPGRADABLE,
    REQUIRES_RESOURCE_TERRAIN,
    STORAGE,
    PRIMARY_STORAGE,
    HEADQUARTERS,
    COMBAT_BARRIER,
    TURRET,
    SHIELD_GENERATOR,
    RESPAWN_POINT,
    LAUNCH_PAD,
    BLACKSMITH,
    RESOURCE_CONVERTER,
    RANGE_AURA,
    VISION_AURA,
    HEAL_AURA,
    OUTPOST_SURVEY,
    RESEARCH_LAB,
})

#: The controlled vocabulary of technology TREES. Every technology declares one
#: ``tree`` (``TechnologyDef.tree``) and every research lab hosts exactly one.
#: A tree groups techs by domain so a specialized lab gates its own line of
#: research:
#:   - ``weapons``  — offense: weapon damage/range, crafted-gear quality.
#:   - ``defense``  — survivability: building HP, armor/damage-reduction.
#:   - ``resource`` — economy: production/build-cost/salvage efficiency.
#:   - ``research`` — the generalist tree: terrain affinities + utility techs
#:     (the original Lab's line — kept as ``research`` for continuity).
#: The schema validator rejects any tech ``tree`` or lab ``research_tree``
#: outside this set, so a typo fails at load.
RESEARCH_TREE_WEAPONS = "weapons"
RESEARCH_TREE_DEFENSE = "defense"
RESEARCH_TREE_RESOURCE = "resource"
RESEARCH_TREE_RESEARCH = "research"

RESEARCH_TREES: tuple[str, ...] = (
    RESEARCH_TREE_WEAPONS,
    RESEARCH_TREE_DEFENSE,
    RESEARCH_TREE_RESOURCE,
    RESEARCH_TREE_RESEARCH,
)

#: Fraction of carried items/resources a Respawn building recovers, by BUILDING
#: level (1-5). Per-item probabilistic (each item recovered with this chance) and
#: floor(pct x amount) of each resource stack. Reaches 95% at max building level;
#: a 55% floor at L1 keeps early death from being ruinous. Upgrading the building
#: is the recovery-upgrade path.
RESPAWN_RECOVERY_BY_LEVEL: dict[int, float] = {
    1: 0.55,
    2: 0.65,
    3: 0.75,
    4: 0.85,
    5: 0.95,
}

#: Max Shield Generators a player may build per planet (R: max 4 per planet,
#: per player). Tech research may raise this later.
MAX_SHIELD_GENERATORS_PER_PLANET = 4

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
#  Deeds
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
#  XP-gain reporting vocabulary
# ------------------------------------------------------------------ #
#
# ``RankSystem.award_xp`` emits a "+N XP" line so economy actions aren't
# silent. The reason strings below are the ones passed by the award call
# sites; they are INTERNAL identifiers and must never reach a player
# verbatim, hence the display map.

#: Reason -> player-facing label for the "+N XP" line. A reason absent from
#: this map renders as a bare "+N XP" rather than leaking its identifier.
XP_REASON_LABELS = {
    "build_complete": "construction",
    "upgrade_complete": "upgrade",
    "agent_trained": "training",
    "harvest_action": "harvesting",
    "combat": "combat",
    "base_destroy": "base destroyed",
    "directive": "directive",
}

#: Award reasons that must NOT emit a "+N XP" line, because the action's own
#: notification already quotes the XP — a second line would double-report and,
#: since those notifications carry the PRE-throttle figure while the award is
#: post-throttle, would contradict it on an outgrown planet:
#:   * ``combat``       -> ``npc_killed`` / player-defeat lines
#:   * ``base_destroy`` -> ``base_eliminated``
#:   * ``directive``    -> ``directive_complete``
#: ``harvest_action`` is suppressed for volume: +1 per action on a short
#: cooldown, already covered by its own drop/crit feedback.
XP_GAIN_SUPPRESSED_REASONS = frozenset({
    "harvest_action", "combat", "base_destroy", "directive",
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
