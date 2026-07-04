# Requirements Document

## Introduction

This feature introduces a shared experience-and-rank progression mechanism that lives on the `CombatEntity` base class (`mygame/typeclasses/combat_entity.py`), so that any entity type that mixes in `CombatEntity` — players (`CombatCharacter`), NPC agents (`NPC`), and future combat entities — inherits the same leveling behavior. The leveling mechanic is generalized out of the player-specific `RankSystem` into shared entity-level state and behavior, rather than building a separate parallel system for agents.

Today, only players progress: the `RankSystem` (`mygame/world/systems/rank_system.py`) reads and writes `db.combat_xp`, the derived `db.level` (1–60), and the cosmetic `db.rank_level` (1–12, one rank per five levels), using XP thresholds from `data/definitions/ranks.yaml`. This feature moves the core XP-to-level-to-rank computation onto `CombatEntity` so the exact same state attributes (`db.combat_xp`, `db.level`, `db.rank_level`) and progression methods are available on every combat entity instance, each tracked independently per entity. The player `RankSystem` is refactored to delegate to the shared mechanism while preserving all existing player behavior, attributes, and `RANK_PROMOTED`/`RANK_DEMOTED` events.

On top of this shared progression, the feature adds rank/level-based ability gating for entities. The first concrete gated ability is the autonomous `DeliveryBehavior` (`mygame/typeclasses/agent_scripts.py`). Reaching a gate's required level makes a gated ability **available** for an agent but does not attach it automatically: the owning player is notified that the ability is available and how to enable it, and the player must intentionally enable the ability for that specific agent before the behavior script attaches. The gated behavior script attaches if and only if both the agent's `Effective_Level` meets or exceeds the gate's required level and the player has explicitly enabled that ability for that agent. Below the threshold, or while the ability is available but not enabled, a harvester only produces resource drops at its Extractor (current behavior) and the player collects them manually; once unlocked and enabled, the delivery loop attaches and the agent ferries resources to a Vault/HQ autonomously. Enablement is sticky: once a player enables an ability for an agent, that choice persists, so a level-driven detach (for example owner demotion) automatically re-attaches the script if and when the agent re-qualifies, without a second player command. The gating mechanism is generic and data-driven: additional gated abilities (intentionally left to-be-defined) are added later through validated data rather than progression-code changes, and the enable/disable mechanism works for any gated ability key.

### Alignment with existing systems

- Shared combat base: `mygame/typeclasses/combat_entity.py` (`CombatEntity`), the pure-Python mixin already shared by `CombatCharacter` (players) and `NPC` (agents). This is where progression state and methods are added.
- Player progression: `mygame/world/systems/rank_system.py` (`RankSystem`) — refactored to delegate to the shared mechanism; thresholds from `data/definitions/ranks.yaml` linearly interpolated within each rank; level-to-rank via `rank_from_level`.
- Agents: `mygame/typeclasses/npcs.py` (`NPC`), managed by `mygame/world/systems/agent_system.py` (`AgentSystem`).
- Behavior scripts and role mapping: `mygame/typeclasses/agent_scripts.py` (`HarvesterScript`, `DeliveryBehavior`, `ROLE_SCRIPT_MAP`, `AgentSystem._attach_behavior_script`, which already supports a role mapping to a list of scripts).
- Data-driven definitions: `DataRegistry` (`world/data_registry.py`) validated by `SchemaValidator` (`world/schema_validator.py`); tuning constants in `world/constants.py`.
- Tick loop: `GameTickScript` in `mygame/typeclasses/scripts.py`; agent scripts have `interval = 0`.

### Resolved design decisions (assumptions for initial draft)

These initial decisions can be changed during review:

- **Shared state attributes**: The shared mechanism uses the existing player attribute names (`db.combat_xp`, `db.level`, `db.rank_level`) on `CombatEntity` for all entity types, rather than introducing parallel agent-only attributes. Each entity instance tracks its own values independently.
- **Shared curve**: Agents reuse the same `Rank_Table` (`ranks.yaml`) thresholds and the same level-to-rank rule as players ("similar to the player" interpreted as identical curve, separate per-entity state).
- **Player refactor**: `RankSystem` is refactored to delegate XP/level/rank computation to the shared `CombatEntity` mechanism. Existing player attributes, messages, tech unlock/revoke, and `RANK_PROMOTED`/`RANK_DEMOTED` events are preserved (backward compatible).
- **Agent XP sources**: Agents earn XP from doing their assigned jobs (harvest production, delivery completion, construction/research completion, combat kills) plus an optional per-tick time-served amount. All amounts are data-driven and any may be zero.
- **Delivery unlock level**: `delivery` unlocks at the first level of rank 5 (level 21), defined in data and tunable.
- **Owner-level cap (strictly below)**: An Agent's Effective_Level is capped strictly below its owning Player's Entity_Level — `Effective_Level = max(1, min(Raw_Level, owner Entity_Level − 1))`. The cap is applied at level granularity (level is authoritative; rank is derived). An Agent can never reach or exceed its commander's level. Players have no owner cap.
- **No XP banking — gain frozen at cap ceiling**: An Agent does not bank surplus `db.combat_xp` past the owner cap. WHILE an Agent's Effective_Level has reached the owner-cap ceiling (its level equals owner Entity_Level − 1, the maximum the strictly-below cap allows), `Agent_System` does not award further `Combat_XP` to that Agent — XP gain is frozen at the ceiling and no surplus accumulates. When the owning Player later levels up (the ceiling rises), the Agent may again earn XP up to the new ceiling; effort spent while frozen is not banked or recovered, which is the accepted tradeoff. The `Effective_Level` clamp formula `Effective_Level = max(1, min(Raw_Level, owner Entity_Level − 1))` is retained solely for the owner-DEMOTION edge case: when the owning Player loses levels, an Agent's stored level can temporarily exceed the new ceiling, and the Agent's earned XP must not be stripped. The clamp therefore still bounds the level used for gating and display, even though XP no longer accumulates past the ceiling at award time. The cap is applied by `Agent_System` (which knows the owner), not by the owner-agnostic `Entity_Progression` mechanism, which computes only Raw_Level from the entity's own XP.
- **Gated abilities are intentionally enabled by the player (no auto-enable on unlock)**: Reaching a gate's required level makes an ability available and notifies the owning Player how to enable it, but does not attach the behavior script. Each Agent stores which gated ability keys the Player has explicitly enabled for it (default: none). The gated behavior script attaches if and only if the Agent's Effective_Level meets or exceeds the gate's required level AND the Player has enabled that ability for that Agent. Enablement is sticky (decision A1): the per-Agent enabled flag persists independently of attach/detach, so the first enablement is always an explicit player command, and only re-attachment after a forced level-driven detach is automatic, honoring the Player's prior choice.
- **Re-evaluation on owner level change**: Because the cap is level-based rather than rank-boundary-based, a Player's owned Agents have their Effective_Level and gated-ability attachments re-evaluated on any change to the Player's Entity_Level, not only on rank-boundary (promotion/demotion) crossings. A plain level change between rank boundaries also triggers re-evaluation (an implementation may introduce a `LEVEL_CHANGED` signal for this purpose). A level rise that crosses a gate marks the ability available and notifies the Player but does not auto-attach unless the ability is already enabled for that Agent; an already-enabled ability auto-attaches when it re-qualifies; a level drop below a gate detaches the script.
- **Dynamic detach on cap drop**: When an Agent's Effective_Level drops below a `Gated_Ability`'s required level (for example because the owning Player was de-leveled below the threshold), `Agent_System` detaches the corresponding gated behavior script and notifies the owning Player that the named ability has re-locked for the identified Agent, mirroring the player tech-revoke behavior. The Agent's enabled flag is retained so the ability re-attaches automatically if and when the Agent re-qualifies.

## Glossary

- **Combat_Entity**: The shared mixin `typeclasses.combat_entity.CombatEntity`, included by both `CombatCharacter` (players) and `NPC` (agents).
- **Entity_Progression**: The shared progression mechanism (state plus methods) added to `Combat_Entity`, providing XP accumulation, level derivation, rank derivation, and ability-gate evaluation for any entity that mixes in `Combat_Entity`.
- **Combat_XP**: The cumulative experience value stored per entity in `db.combat_xp` as a non-negative integer.
- **Entity_Level**: The derived numeric progression value (1–`MAX_LEVEL`) stored per entity in `db.level`. For an Agent this is the owner-agnostic Raw_Level; see Effective_Level for the value used in gating and display.
- **Raw_Level**: An entity's `Entity_Level` derived solely from that entity's own `Combat_XP` by `Entity_Progression`, computed without reference to any owning player. For Players, Raw_Level is the authoritative level; for Agents it is the uncapped level before the owner cap is applied.
- **Effective_Level**: The owner-capped level used for ability gating and roster display, computed by `Agent_System` as `max(1, min(Raw_Level, owner Entity_Level − 1))`. An Agent's Effective_Level is always strictly less than its owning Player's Entity_Level, with a floor of 1. A Player's Effective_Level equals the Player's Entity_Level (Players have no owner cap). The clamp is retained for the owner-demotion edge case so that earned XP is never stripped when the owner loses levels.
- **Cap_Ceiling**: The maximum Effective_Level the owner cap permits for an Agent, equal to `owner Entity_Level − 1` (floored at 1). While an Agent's level has reached the Cap_Ceiling, `Agent_System` awards no further `Combat_XP` to that Agent (XP gain is frozen; no surplus is banked).
- **Enabled_Ability**: A `Gated_Ability` that the owning Player has explicitly turned on for a specific Agent. Each Agent stores its set of Enabled_Ability keys (default: none). The enabled flag is sticky and persists independently of whether the behavior script is currently attached.
- **Available_Ability**: A `Gated_Ability` whose required level an Agent's Effective_Level meets or exceeds (unlocked) but which the Player has not yet enabled for that Agent. An Available_Ability is offered to the Player to enable but its behavior script is not attached.
- **Entity_Rank**: The derived cosmetic rank number (1–`NUM_RANKS`) stored per entity in `db.rank_level`, computed from `Entity_Level` via `rank_from_level`.
- **Rank_Table**: The rank definition data loaded from `data/definitions/ranks.yaml` into `Data_Registry.ranks`, providing per-rank XP thresholds used (with linear interpolation between ranks) to derive `Entity_Level` and `Entity_Rank`.
- **Player**: A `CombatCharacter` entity controlled by a human account.
- **Agent**: A player-owned NPC (`typeclasses.npcs.NPC` with `db.npc_type == "agent"`).
- **Rank_System**: `RankSystem` (`world/systems/rank_system.py`), the player-facing progression service, refactored to delegate to `Entity_Progression`.
- **Agent_System**: `AgentSystem` (`world/systems/agent_system.py`), which handles agent training, assignment, unassignment, reserve, stop, and per-tick processing, and attaches behavior scripts.
- **Ability_Gate**: A data-driven entry mapping a named gated ability to a required `Entity_Level`.
- **Ability_Gate_Registry**: The validated, data-sourced collection of all `Ability_Gate` entries, exposed via `Data_Registry`.
- **Gated_Ability**: An entity behavior that attaches only when both conditions hold: the entity's `Effective_Level` meets or exceeds the ability's required level, and (for an Agent) the owning Player has enabled that ability for that Agent. The first `Gated_Ability` is `delivery`.
- **Delivery_Ability**: The `Gated_Ability` keyed `delivery`, implemented by `Delivery_Behavior`, that ferries resources from an Extractor to a Vault/HQ.
- **Harvester_Script**: `HarvesterScript`, which produces resource drops at an Extractor each tick.
- **Delivery_Behavior**: `DeliveryBehavior`, the FSM (idle → picking_up → delivering → returning) that performs autonomous delivery.
- **Schema_Validator**: `SchemaValidator` (`world/schema_validator.py`), which validates YAML definition data at load time.
- **Data_Registry**: `DataRegistry` (`world/data_registry.py`), which loads and exposes validated definition data.
- **Reserve**: The state where an agent is benched because the owning player's agent cap (a function of player rank) is exceeded.
- **MAX_LEVEL**: The maximum entity level (`NUM_RANKS` × `LEVELS_PER_RANK` = 60), from `world/constants.py`.
- **NUM_RANKS**: The total number of ranks (12), from `world/constants.py`.

## Requirements

### Requirement 1: Shared progression mechanism on the combat entity base

**User Story:** As a developer, I want experience-and-rank progression implemented on the shared combat entity base, so that players, agents, and future combat entities all use one progression mechanism.

#### Acceptance Criteria

1. THE Entity_Progression SHALL be defined on the Combat_Entity base type so that every entity mixing in Combat_Entity inherits the same progression state and methods.
2. THE Entity_Progression SHALL store an entity's experience in the entity attribute `db.combat_xp` as a non-negative integer.
3. THE Entity_Progression SHALL store an entity's derived level in the entity attribute `db.level` as an integer between 1 and `MAX_LEVEL` inclusive.
4. THE Entity_Progression SHALL store an entity's derived rank number in the entity attribute `db.rank_level` as an integer between 1 and `NUM_RANKS` inclusive.
5. THE Entity_Progression SHALL expose a method on Combat_Entity to award a positive experience amount that increases `db.combat_xp`.
6. THE Entity_Progression SHALL expose a method on Combat_Entity to deduct an experience amount that decreases `db.combat_xp`.
7. THE Entity_Progression SHALL keep `db.combat_xp` at or above zero at all times.

### Requirement 2: Per-entity independent progression state

**User Story:** As a commander, I want each entity to track its own experience and level, so that one entity's progression never affects another's.

#### Acceptance Criteria

1. THE Entity_Progression SHALL maintain `db.combat_xp`, `db.level`, and `db.rank_level` per entity instance.
2. WHEN one entity's `db.combat_xp` changes, THE Entity_Progression SHALL leave every other entity's `db.combat_xp`, `db.level`, and `db.rank_level` unchanged.
3. THE Entity_Progression SHALL derive a given entity's `Entity_Level` and `Entity_Rank` solely from that same entity's `db.combat_xp`.

### Requirement 3: Level and rank derivation from the shared rank table

**User Story:** As a designer, I want every entity's level and rank to follow the same data-driven curve, so that progression is consistent and tunable from one definition source.

#### Acceptance Criteria

1. THE Entity_Progression SHALL derive `Entity_Level` from `Combat_XP` using the `Rank_Table` XP thresholds loaded from `data/definitions/ranks.yaml`.
2. THE Entity_Progression SHALL derive `Entity_Level` as the highest level whose `Rank_Table` XP threshold is less than or equal to `Combat_XP`.
3. THE Entity_Progression SHALL derive `Entity_Rank` from `Entity_Level` using the same level-to-rank computation applied to players (`rank_from_level`).
4. WHEN an entity's `db.combat_xp` changes, THE Entity_Progression SHALL recompute `db.level` and `db.rank_level` on every change, regardless of whether the resulting values differ.
5. WHEN `Combat_XP` is below the threshold for level 2, THE Entity_Progression SHALL set `Entity_Level` to 1.
6. WHEN `Combat_XP` meets or exceeds the threshold for `MAX_LEVEL`, THE Entity_Progression SHALL set `Entity_Level` to `MAX_LEVEL`.

### Requirement 4: Player progression backward compatibility

**User Story:** As an existing player, I want my level, experience, rank, and promotions to keep working after the refactor, so that the shared mechanism is invisible to me.

#### Acceptance Criteria

1. THE Rank_System SHALL delegate experience award, experience deduction, and level/rank recomputation to the Entity_Progression mechanism on Combat_Entity.
2. THE Rank_System SHALL continue to read and write the player attributes `db.combat_xp`, `db.level`, and `db.rank_level` using the same meanings as before this feature.
3. WHEN a player's derived rank increases, THE Rank_System SHALL publish a `RANK_PROMOTED` event with the old rank, new rank, and new agent cap.
4. WHEN a player's derived rank decreases, THE Rank_System SHALL publish a `RANK_DEMOTED` event with the old rank, new rank, and new agent cap.
5. WHEN a player's level changes, THE Rank_System SHALL notify the player with a message identifying the new level and rank name.
6. WHERE a legacy player has `db.rank_level` set but no `db.level`, THE Rank_System SHALL derive the player's level from the stored rank using the existing backward-compatibility rule.
7. WHEN a player's rank increases or decreases, THE Rank_System SHALL apply the existing technology unlock and revoke behavior for the new rank.

### Requirement 5: Agent experience sources

**User Story:** As a commander, I want my agents to earn experience by doing their jobs, so that active agents progress toward unlocking abilities.

#### Acceptance Criteria

1. WHEN a harvester agent produces a resource drop at an Extractor, THE Agent_System SHALL award the agent the configured harvest-production XP amount through Entity_Progression.
2. WHEN a harvester agent completes a delivery to a Vault or HQ, THE Agent_System SHALL award the agent the configured delivery-completion XP amount through Entity_Progression.
3. WHEN an engineer agent completes a construction or research task, THE Agent_System SHALL award the agent the configured construction-completion XP amount through Entity_Progression.
4. WHEN a combat agent defeats an entity, THE Agent_System SHALL award the agent the configured combat XP amount through Entity_Progression.
5. WHILE an agent is actively assigned to a role, THE Agent_System SHALL award the agent the configured time-served XP amount once per tick through Entity_Progression.
6. THE Agent_System SHALL source every agent XP award amount from `Data_Registry` configuration values rather than hardcoded literals.
7. IF an agent XP award amount is zero or negative, THEN THE Entity_Progression SHALL leave `db.combat_xp` unchanged.
8. WHERE the configured time-served XP amount is zero, THE Agent_System SHALL grant no time-served progression to actively assigned agents.
9. WHILE an Agent's `Effective_Level` has reached its `Cap_Ceiling` (the Agent's level equals `owner Entity_Level − 1`), THE Agent_System SHALL NOT award further `Combat_XP` to that Agent, so that XP gain is frozen at the ceiling and no surplus accumulates.
10. WHEN the owning Player levels up such that an Agent's `Cap_Ceiling` rises above the Agent's current `Effective_Level`, THE Agent_System SHALL resume awarding configured `Combat_XP` to that Agent up to the new `Cap_Ceiling`.

### Requirement 6: Experience loss on agent death

**User Story:** As a commander, I want losing an agent in combat to carry a progression cost, so that agent survival matters.

#### Acceptance Criteria

1. WHEN an agent is defeated in combat, THE Agent_System SHALL deduct the configured agent death XP amount from the agent's `db.combat_xp` through Entity_Progression.
2. IF deducting the agent death XP amount would reduce `db.combat_xp` below zero, THEN THE Entity_Progression SHALL set `db.combat_xp` to zero.
3. WHEN an agent's `db.combat_xp` is reduced on death, THE Entity_Progression SHALL recompute `db.level` and `db.rank_level` from the new `db.combat_xp`.
4. THE Agent_System SHALL source the agent death XP amount from `Data_Registry` configuration.

### Requirement 7: Data-driven ability gate definitions

**User Story:** As a designer, I want ability gates defined in validated data, so that I can add and tune gated abilities without changing progression code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load `Ability_Gate` entries from a YAML definition source under `mygame/data/definitions/`.
2. THE Data_Registry SHALL expose each `Ability_Gate` with an ability key and a required `Entity_Level`.
3. WHEN ability gate data is loaded, THE Schema_Validator SHALL verify that each entry has a non-empty string ability key and a required `Entity_Level` that is an integer between 1 and `MAX_LEVEL` inclusive.
4. IF an ability gate entry has a duplicate ability key, THEN THE Schema_Validator SHALL report a validation error identifying the duplicate key.
5. IF an ability gate entry is missing a required field or has a field of the wrong type, THEN THE Schema_Validator SHALL report a validation error identifying the entry and field.
6. THE Ability_Gate_Registry SHALL include an entry with ability key `delivery` whose required `Entity_Level` corresponds to the first level of rank 5.
7. IF the first level of rank 5 exceeds `MAX_LEVEL`, THEN THE Ability_Gate_Registry SHALL cap the `delivery` required `Entity_Level` at `MAX_LEVEL`.

### Requirement 8: Delivery ability gating

**User Story:** As a commander, I want autonomous delivery to unlock only after a harvester agent has gained enough experience, so that delivery is a progression reward rather than a default.

#### Acceptance Criteria

1. WHILE a harvester agent's `Effective_Level` is below the `delivery` `Ability_Gate` required level, THE Agent_System SHALL attach only `Harvester_Script` to the agent so that it produces resource drops at the Extractor without delivering.
2. WHILE a harvester agent's `Effective_Level` meets or exceeds the `delivery` `Ability_Gate` required level but the owning player has not enabled the `delivery` ability for that agent, THE Agent_System SHALL attach only `Harvester_Script` to the agent so that it produces resource drops at the Extractor without delivering.
3. WHERE a harvester agent's `Effective_Level` meets or exceeds the `delivery` `Ability_Gate` required level AND the owning player has enabled the `delivery` ability for that agent, THE Agent_System SHALL attach both `Harvester_Script` and `Delivery_Behavior` to the agent when the harvester role is applied.
4. WHILE the `delivery` ability is not attached for a harvester agent, THE Harvester_Script SHALL continue to produce resource drops at the Extractor coordinates for manual collection.
5. THE Agent_System SHALL attach `Delivery_Behavior` for a harvester agent if and only if the agent's `Effective_Level` meets or exceeds the `delivery` `Ability_Gate` required level AND the owning player has enabled the `delivery` ability for that agent.
6. THE Agent_System SHALL determine which behavior scripts a role attaches by combining the role-to-script mapping with the `Ability_Gate_Registry` evaluated against the agent's `Effective_Level` and the agent's per-ability enabled state.

### Requirement 9: Dynamic ability unlock and re-lock while assigned

**User Story:** As a commander, I want a harvester to become eligible for delivery as soon as it reaches the unlock level while on the job, to begin delivering once I enable the ability, and to stop delivering if it later falls below that level, so that I control ability changes and see them immediately without re-assigning it.

#### Acceptance Criteria

1. WHEN an assigned harvester agent's `Effective_Level` rises to meet or exceed the `delivery` `Ability_Gate` required level, THE Agent_System SHALL mark the `delivery` ability available for that agent and notify the owning player that the named ability is available for the identified agent together with the command to enable it, WITHOUT attaching `Delivery_Behavior`.
2. WHEN an assigned harvester agent's `Effective_Level` meets or exceeds the `delivery` `Ability_Gate` required level AND the `delivery` ability is already enabled for that agent, THE Agent_System SHALL attach `Delivery_Behavior` to that agent without requiring reassignment.
3. WHEN `Delivery_Behavior` is attached to an already-assigned harvester agent, THE Agent_System SHALL initialize the agent's delivery state to idle.
4. IF a harvester agent already has `Delivery_Behavior` attached, THEN THE Agent_System SHALL leave the existing script in place without attaching a duplicate.
5. WHEN an assigned agent's `Effective_Level` drops below a `Gated_Ability`'s `Ability_Gate` required level, THE Agent_System SHALL detach that ability's behavior script from the agent without requiring reassignment.
6. WHEN a player disables a `Gated_Ability` for an assigned agent, THE Agent_System SHALL detach that ability's behavior script from the agent without requiring reassignment.
7. WHEN a `Gated_Ability`'s behavior script is detached because the agent's `Effective_Level` dropped below the required level, THE Agent_System SHALL notify the owning player that the named ability has re-locked for the identified agent.
8. IF a `Gated_Ability`'s behavior script is not currently attached to an agent whose `Effective_Level` is below that gate's required level, THEN THE Agent_System SHALL leave the agent unchanged without attempting a detach.

### Requirement 10: Independence from reserve, stop, and player demotion

**User Story:** As a commander, I want an agent's experience to persist through reserve and stop actions, so that benching or pausing an agent never erases its progression.

#### Acceptance Criteria

1. WHEN an agent is placed in `Reserve` due to player demotion, THE Agent_System SHALL retain the agent's `db.combat_xp`, `db.level`, and `db.rank_level` unchanged.
2. WHEN an agent is stopped or unassigned, THE Agent_System SHALL retain the agent's `db.combat_xp`, `db.level`, and `db.rank_level` unchanged.
3. THE Agent_System SHALL derive an agent's eligibility for `Gated_Ability` attachment from the agent's `Effective_Level`, independent of the owning player's agent-count cap and the agent's `Reserve` or stopped status.
4. WHEN a reserved agent is restored to active duty and reassigned to the harvester role, THE Agent_System SHALL attach `Delivery_Behavior` if and only if the agent's recomputed `Effective_Level` meets or exceeds the `delivery` `Ability_Gate` required level AND the `delivery` ability is enabled for that agent.

### Requirement 11: Roster display of agent progression

**User Story:** As a commander, I want to see each agent's level, rank, and unlocked abilities in the roster, so that I can track progression and plan assignments.

#### Acceptance Criteria

1. WHEN a player views the agent roster, THE Agent_System SHALL display each agent's `Effective_Level` and the `Entity_Rank` name derived from that `Effective_Level` alongside the agent's existing role and status information.
2. WHEN a player views the agent roster, THE Agent_System SHALL display, for each agent, each `Gated_Ability` key's state as one of: locked with the required level, available, or enabled.
3. IF an agent meets the required level for no `Gated_Ability`, THEN THE Agent_System SHALL indicate that no gated abilities are available or enabled for that agent.
4. WHILE an agent's `Raw_Level` exceeds its `Effective_Level` because the owner-level cap is in effect, THE Agent_System SHALL display a marker indicating that the agent's progression is currently capped by the commander's level.

### Requirement 12: Agent initialization and backward compatibility

**User Story:** As a commander with agents created before this feature, I want my existing agents to keep working with sensible defaults, so that the update does not break my roster.

#### Acceptance Criteria

1. WHEN a new agent is created, THE Agent_System SHALL initialize `db.combat_xp` to zero, `db.level` to 1, `db.rank_level` to 1, and the agent's set of enabled `Gated_Ability` keys to empty.
2. WHERE an existing agent has no `db.combat_xp` attribute, THE Entity_Progression SHALL treat the agent's experience as zero.
3. WHERE an existing agent has no `db.level` attribute, THE Entity_Progression SHALL treat the agent's level as 1.
4. WHERE an existing agent has no stored enabled-ability state, THE Agent_System SHALL treat the agent as having no `Gated_Ability` enabled.
5. WHEN an existing agent without progression attributes first earns or loses experience, THE Entity_Progression SHALL initialize and persist `db.combat_xp`, `db.level`, and `db.rank_level`.
6. WHERE an existing harvester agent whose `Effective_Level` is below the `delivery` `Ability_Gate` required level is processed, THE Agent_System SHALL leave it as production-only without attaching `Delivery_Behavior`.

### Requirement 13: Extensibility for future gated abilities

**User Story:** As a designer, I want the gating mechanism to support new abilities through data alone, so that future entity abilities can be gated without modifying progression code.

#### Acceptance Criteria

1. THE Ability_Gate_Registry SHALL support an arbitrary number of `Ability_Gate` entries beyond `delivery`.
2. WHEN a new `Ability_Gate` entry is added to the data source with a valid ability key and required `Entity_Level`, THE Agent_System SHALL evaluate that gate against entity levels without requiring changes to progression logic.
3. THE Agent_System SHALL resolve each `Gated_Ability` key to its behavior script through a mapping that can be extended to register new ability scripts.
4. IF an `Ability_Gate` entry references an ability key that has no registered behavior script, THEN THE Agent_System SHALL skip attaching a script for that ability and SHALL log the unresolved ability key.
5. THE Agent_System SHALL provide the ability enable, disable, and status-display mechanisms generically for any `Gated_Ability` key, without behavior specific to the `delivery` ability.

### Requirement 14: Agent effective level bounded by owner level

**User Story:** As a commander, I want each of my agents to remain below my own level, so that an agent can never out-rank me or unlock gated abilities I have not yet reached myself.

#### Acceptance Criteria

1. THE Agent_System SHALL compute an Agent's `Effective_Level` as `max(1, min(Raw_Level, owner Entity_Level − 1))`, where `Raw_Level` is the Agent's `Entity_Level` derived by `Entity_Progression` from the Agent's own `Combat_XP` and `owner Entity_Level` is the owning Player's `db.level`.
2. THE Agent_System SHALL apply the owner-level cap such that an Agent's `Effective_Level` is strictly less than the owning Player's `Entity_Level`.
3. WHERE an Agent is owned by a Player whose `Entity_Level` is 1, THE Agent_System SHALL set the Agent's `Effective_Level` to 1.
4. WHILE an Agent's `Effective_Level` has reached its `Cap_Ceiling` (the Agent's level equals `owner Entity_Level − 1`), THE Agent_System SHALL NOT award further `Combat_XP` to that Agent, so that no surplus accumulates past the ceiling.
5. THE Agent_System SHALL use each Agent's `Effective_Level`, rather than its `Raw_Level`, for ability-gate evaluation and roster display.
6. WHEN an Agent earns or loses `Combat_XP`, THE Agent_System SHALL re-evaluate that Agent's `Effective_Level` from the Agent's updated `Raw_Level` and the owning Player's current `Entity_Level`.
7. WHEN the owning Player's `Entity_Level` changes, THE Agent_System SHALL re-evaluate the `Effective_Level` of each Agent owned by that Player.
8. WHEN the owning Player's `Entity_Level` increases, THE Agent_System SHALL raise each owned Agent's `Cap_Ceiling` accordingly and resume awarding `Combat_XP` to each owned Agent up to the new `Cap_Ceiling`, with no banked surplus realized from the period the Agent was frozen.
9. THE Agent_System SHALL apply the owner-level cap, and THE Entity_Progression SHALL remain owner-agnostic by computing only `Raw_Level` from an entity's own `Combat_XP` without reference to any owning Player.
10. THE Entity_Progression SHALL apply no owner-level cap to a Player's own `Entity_Level`.

### Requirement 15: Re-evaluation of gated abilities on owner level change

**User Story:** As a commander, I want my agents' unlocked abilities to update whenever my level changes, so that ability access stays consistent with the owner cap even between rank boundaries.

#### Acceptance Criteria

1. WHEN the owning Player's `Entity_Level` changes by any amount, THE Agent_System SHALL re-evaluate gated-ability attachments for each Agent owned by that Player against the Agent's recomputed `Effective_Level` and the Agent's per-ability enabled state.
2. WHEN the owning Player's `Entity_Level` increases such that an owned Agent's `Effective_Level` newly meets or exceeds a `Gated_Ability`'s required level AND that ability is not enabled for the Agent, THE Agent_System SHALL mark the ability available and notify the Player that the named ability is available for the identified Agent together with the command to enable it, WITHOUT attaching the behavior script.
3. WHEN the owning Player's `Entity_Level` increases such that an owned Agent's `Effective_Level` newly meets or exceeds a `Gated_Ability`'s required level AND that ability is already enabled for the Agent, THE Agent_System SHALL attach that ability's behavior script and notify the Player that the named ability is now active for the identified Agent.
4. WHEN the owning Player's `Entity_Level` decreases such that an owned Agent's `Effective_Level` falls below a `Gated_Ability`'s required level, THE Agent_System SHALL detach that ability's behavior script, retain the Agent's enabled flag for that ability, and notify the Player that the named ability has re-locked for the identified Agent.
5. THE Agent_System SHALL re-evaluate owned Agents on any owning Player `Entity_Level` change, including changes that do not cross a rank boundary and therefore do not emit a `RANK_PROMOTED` or `RANK_DEMOTED` event.

### Requirement 16: Per-agent ability enablement command

**User Story:** As a commander, I want to intentionally turn a gated ability on or off for a specific agent and view each ability's status, so that abilities only activate when I choose and I am never surprised by an automatic behavior change.

#### Acceptance Criteria

1. THE Agent_System SHALL extend the existing `agent` noun-and-verb command router with an ability enablement command of the form `agent ability <id> <key> on|off` and an ability status command of the form `agent ability <id>`.
2. WHEN a player enables an ability for an agent whose `Effective_Level` meets or exceeds the ability's `Ability_Gate` required level, THE Agent_System SHALL record the ability as enabled for that agent, attach the corresponding behavior script, and confirm the action to the player.
3. IF a player attempts to enable an ability whose `Ability_Gate` required level the agent's `Effective_Level` does not yet meet, THEN THE Agent_System SHALL reject the request, inform the player of the required level, and SHALL NOT attach the behavior script.
4. WHEN a player disables an ability for an agent, THE Agent_System SHALL clear the enabled state for that ability, detach the corresponding behavior script while leaving other attached scripts such as `Harvester_Script` in place, and confirm the action to the player.
5. WHEN a player views ability status for an agent, THE Agent_System SHALL show each `Gated_Ability` key's state as one of: locked with the required level, available, or enabled.
6. IF a player issues the ability command for an ability key that has no `Ability_Gate` entry, THEN THE Agent_System SHALL reject the request and inform the player that the ability key is unknown.
7. IF a player issues the ability command for an agent identifier the player does not own, THEN THE Agent_System SHALL reject the request and inform the player that the agent was not found.

### Requirement 17: Sticky ability enablement across attach and detach

**User Story:** As a commander, I want my decision to enable an ability to persist through level-driven detaches, so that the ability comes back automatically when the agent re-qualifies without me re-issuing the command.

#### Acceptance Criteria

1. THE Agent_System SHALL persist each agent's per-ability enabled flag independently of whether the corresponding behavior script is currently attached.
2. THE Agent_System SHALL require the first enablement of a `Gated_Ability` for an agent to be an explicit player command.
3. WHILE a `Gated_Ability` is enabled for an agent, WHEN the agent's `Effective_Level` rises to meet or exceed that ability's `Ability_Gate` required level, THE Agent_System SHALL attach the corresponding behavior script automatically without an additional player command.
4. WHILE a `Gated_Ability` is enabled for an agent, WHEN the agent's `Effective_Level` falls below that ability's `Ability_Gate` required level, THE Agent_System SHALL detach the corresponding behavior script and retain the agent's enabled flag for that ability.
5. WHEN a player disables a `Gated_Ability` for an agent, THE Agent_System SHALL clear the enabled flag so that the ability does not re-attach automatically until the player enables it again.
