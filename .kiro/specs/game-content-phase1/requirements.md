# Requirements Document — Game Content Phase 1: Core Game Loop

## Introduction

Phase 1 replaces the existing placeholder content in the RTS Combat Overworld MUD (built on Evennia) with the real game content that defines the core gameplay loop. This includes expanding from 3 planets to 6 zones (5 planets + Space), replacing 15 terrain types with 48, reworking building types into 12 purpose-driven types, replacing 22 ranks with 12 progression-gated ranks, introducing 6 named resources (replacing 8 generic ones), and adding the Agent system as the central scaling mechanic. The goal is to deliver a playable early-to-mid game loop: spawn → build HQ → harvest → train agents → expand → raid → rank up → unlock new planets.

## Glossary

- **Terrain_Generator**: The procedural terrain generation system (`terrain_generator.py`) that produces deterministic terrain for any (x, y) coordinate using hash-based value noise and cumulative weight thresholds.
- **Planet_Registry**: The configuration store (`planet_registry.py`) that holds CoordinateSpaceDef entries for each planet, validates coordinates, and resolves planet identifiers.
- **Data_Registry**: The central YAML-driven registry (`data_registry.py`) that loads and provides access to all game definitions (terrain, buildings, ranks, resources, items, technologies).
- **Resource_System**: The system (`resource_system.py`) that handles manual harvesting from terrain nodes, automated production from Extractor buildings, and depleted node respawn cycles.
- **Building_System**: The system (`building_system.py`) that validates prerequisites, terrain, resources, and combat lockout before allowing construction, upgrade, or demolition of buildings.
- **Rank_System**: The system (`rank_system.py`) that manages player rank progression based on Combat XP, handling promotion when XP meets thresholds and gating content unlocks.
- **Agent_System**: A new system that manages the player's pool of assignable agents (Harvester, Engineer, Soldier, Guard, Scout, Medic), their training, assignment, incapacitation, and respawn.
- **CombatEntity**: A shared base mixin or typeclass providing attributes common to all combat-capable entities (players and NPCs): `hp`, `hp_max`, `inventory` (resource dict), `equipment_slots`, `incapacitated` state, `respawn_timer`, `respawn_location`, and `get_structured_state()`. Both CombatCharacter and NPC inherit from CombatEntity.
- **CombatCharacter**: The player character typeclass (`characters.py`) that extends CombatEntity with player-specific state: XP, rank, coordinates, agent ownership, fog of war discovery, and account puppeting.
- **Agent**: An NPC game object (see NPC) with `npc_type` tag "agent" that is owned by a player and can be assigned to a role (Harvester, Engineer, Soldier, Guard, Scout, Medic). Agents inherit from NPC which inherits from CombatEntity.
- **NPC**: A game object typeclass extending CombatEntity with NPC-specific state: `owner` reference, `npc_type` tag, and scriptable behavior. The foundation for agents, enemies, and vendors.
- **Agent_Cap**: The maximum number of agents a player can have, determined by rank level.
- **Rank_Level**: A sub-division within each rank (1-5) that provides granular XP progress feedback. Levels are cosmetic and do not affect gameplay mechanics.
- **Extractor**: A building type that must be placed on resource terrain. A Harvester agent assigned to an Extractor generates that terrain's resource each game tick.
- **Academy**: A building type that trains new agents. Higher levels reduce training time.
- **Armory**: A building type where an Engineer agent crafts equipment. Higher levels unlock higher-tier recipes.
- **Turret**: A defensive building that auto-attacks enemies in range. Requires a Guard agent to operate.
- **Vault**: A building that protects a percentage of stored resources from raids.
- **Radar**: A building that extends map vision radius. Requires a Scout agent to operate.
- **HQ**: Headquarters building. Required first on each planet. Agent respawn point. Research hub. Limited to 1 per player per planet.
- **Lab**: Research building. Enables technology upgrades. One research project at a time per Lab. Requires an Engineer agent to operate.
- **Wall**: Passive defensive barrier. Blocks enemy movement. High HP. No agent required.
- **Barracks**: Military infrastructure. Each Barracks adds soldier slots to the player's army capacity.
- **Medbay**: Medical facility. Reduces agent incapacitation/respawn time. Medic agent enhances the effect.
- **Relay**: Defense amplifier. Boosts nearby Turret damage within a radius. No agent required.
- **Incapacitated**: An agent or player state where the entity is unavailable for a duration after being defeated in combat, then respawns at the owner's HQ.
- **Combat_Timer**: A 60-second countdown triggered when a player detects an enemy or takes damage. While active, the player cannot move through their own Walls.
- **Active_Presence**: The requirement for a player to remain on a tile in a specific state (building, harvesting) for progress to continue. Agents bypass this requirement by performing the action autonomously.
- **Nexium**: The rarest resource, found only in Citadel Vault Room terrain. Used for prestige equipment, faction upgrades, and endgame crafting.
- **YAML_Definition**: A data file in `mygame/data/definitions/` that declaratively defines game content (planets, terrain, buildings, ranks) loaded by the Data_Registry at startup.

## Requirements

### Requirement 1: Planet Definitions

**User Story:** As a player, I want to explore multiple distinct planets with unique themes and terrain, so that progression feels rewarding and each new planet offers a fresh experience.

#### Acceptance Criteria

1. THE Data_Registry SHALL load exactly 6 coordinate spaces from `planets.yaml`: Terra (500×500), Forge (400×400), Tundra (400×400), Inferno (300×300), Citadel (200×200), and Space (1000×1000).
2. WHEN the Planet_Registry loads `planets.yaml`, THE Planet_Registry SHALL validate that each planet entry contains a unique `planet_key`, positive `width` and `height`, a `terrain_seed`, and a `terrain_weights` map whose values sum to 1.0.
3. THE Planet_Registry SHALL store a `rank_requirement` field for each planet, specifying the minimum rank level required to access that planet.
4. WHEN a player attempts to travel to a planet, THE Planet_Registry SHALL reject the travel if the player's rank level is below that planet's `rank_requirement`.
5. THE `planets.yaml` file SHALL define Terra as the default planet with `default_planet: true` and a spawn point at the center of the map.
6. THE `planets.yaml` file SHALL assign each planet a unique `z_level` for teleport shorthand resolution.
7. FOR ALL planet definitions loaded from `planets.yaml`, serializing a CoordinateSpaceDef to YAML and parsing it back SHALL produce an equivalent CoordinateSpaceDef (round-trip property).

### Requirement 2: Terrain Definitions

**User Story:** As a player, I want each planet to have 8 unique terrain types with distinct symbols and resource yields, so that the world feels varied and exploration is meaningful.

#### Acceptance Criteria

1. THE Data_Registry SHALL load exactly 48 terrain definitions from `terrain.yaml`, with 8 terrain types per planet.
2. WHEN the Data_Registry loads `terrain.yaml`, THE Data_Registry SHALL validate that each terrain entry contains a `terrain_type` string, a 2-character `map_symbol`, an optional `resource_type`, and a `passable` boolean.
3. THE Terrain_Generator SHALL use the `terrain_weights` from a planet's CoordinateSpaceDef to select terrain types, where each weight represents the probability of that terrain appearing.
4. WHEN the Terrain_Generator generates terrain for coordinate (x, y) on a given planet, THE Terrain_Generator SHALL return the same terrain type for the same (x, y) and seed combination (deterministic generation).
5. FOR ALL coordinates within a planet's bounds, THE Terrain_Generator SHALL return a terrain type that is defined in that planet's `terrain_weights` map.
6. THE `terrain.yaml` file SHALL define resource associations as follows: Wood from Forest and Pine_Forest terrain, Stone from Rock, Permafrost, and Obsidian_Plain terrain, Iron from Mountain, Scrapyard, Ice_Cave, Scorched_Rock, Armory_Ruin, and Asteroid terrain, Energy from Power_Grid, Magma_Vent, Nebula, and Generator_Room terrain, Circuits from Circuit_Field, Debris, and Control_Room terrain, and Nexium from Vault_Room terrain only.
7. WHEN a terrain type has a non-null `resource_type`, THE Terrain_Generator SHALL include that resource type in the `get_terrain_and_resource` return value for coordinates matching that terrain.

### Requirement 3: Resource Definitions

**User Story:** As a player, I want a clear set of 6 named resources tied to specific terrain and planets, so that I understand what to gather and where to find it.

#### Acceptance Criteria

1. THE Data_Registry SHALL define exactly 6 resource types: Wood, Stone, Iron, Energy, Circuits, and Nexium.
2. WHEN a new CombatCharacter is created, THE CombatCharacter SHALL initialize with starting resources of 30 Wood, 20 Stone, 10 Iron, 0 Energy, 0 Circuits, and 0 Nexium.
3. THE CombatCharacter SHALL store resources as a dictionary keyed by resource type name, with integer values representing current amounts.
4. WHEN a player harvests from a terrain tile with a resource node, THE Resource_System SHALL add the configured `gather_amount` of that terrain's resource type to the player's inventory.
5. IF a player attempts to harvest from a terrain tile with no resource type, THEN THE Resource_System SHALL return a failure message indicating no resource is available.
6. WHEN a player deducts resources for building or crafting, THE CombatCharacter SHALL reject the deduction if any required resource amount exceeds the player's current stock.
7. FOR ALL resource operations (add then deduct the same amount), THE CombatCharacter SHALL return to the original resource state (round-trip property).

### Requirement 4: Rank Definitions

**User Story:** As a player, I want a 12-rank progression system with clear XP thresholds and meaningful unlocks at each rank, so that I have concrete goals to work toward.

#### Acceptance Criteria

1. THE Data_Registry SHALL load exactly 12 rank definitions from `ranks.yaml` with strictly increasing XP thresholds: Recruit (0), Private (200), Corporal (600), Sergeant (1500), Staff_Sergeant (3500), Lieutenant (7000), Captain (12000), Major (20000), Colonel (35000), Brigadier (55000), General (80000), Marshal (120000).
2. WHEN a player's Combat XP meets or exceeds the next rank's XP threshold, THE Rank_System SHALL promote the player to that rank.
3. THE Rank_System SHALL support multi-rank jumps when a single XP award crosses multiple thresholds.
4. WHEN a player's Combat XP drops below their current rank's XP threshold, THE Rank_System SHALL demote the player to the highest rank whose threshold they still meet.
5. WHEN a player is demoted, THE Rank_System SHALL publish a `rank_demoted` event containing the old rank and new rank.
6. WHEN a player is demoted, THE Rank_System SHALL reduce the player's agent cap to the new rank's cap. Any agents exceeding the new cap SHALL be placed in a "reserve" state — they cannot be assigned but are not lost. They become available again if the player re-ranks.
7. WHEN a player is demoted below a planet's rank requirement, THE player SHALL NOT be forcibly removed from that planet, but SHALL be unable to travel to it again until re-ranking.
8. WHEN a player is promoted, THE Rank_System SHALL publish a `rank_promoted` event containing the old rank and new rank.
9. THE Rank_System SHALL support multi-rank jumps in both directions when an XP change crosses multiple thresholds.
10. THE `ranks.yaml` file SHALL define an `agent_cap` field for each rank, specifying the maximum number of agents the player can have at that rank: Recruit (2), Private (3), Corporal (4), Sergeant (6), Staff_Sergeant (8), Lieutenant (10), Captain (12), Major (14), Colonel (16), Brigadier (17), General (19), Marshal (20).
11. THE `ranks.yaml` file SHALL define a `planet_access` list for each rank, specifying which planets the player can access at that rank.
12. THE `ranks.yaml` file SHALL define an `unlocks` list for each rank describing the key feature unlocked (e.g., "Extractor" at Rank 2, "Academy" at Rank 3, "Barracks" at Rank 3, "Armory" at Rank 4).
13. FOR ALL XP values from 0 to 120000, THE Rank_System SHALL resolve to exactly one rank where that XP is at or above the rank's threshold but below the next rank's threshold.

### Requirement 4b: Rank Levels (Sub-Rank Progression)

**User Story:** As a player, I want to see granular progress within each rank through a level system, so that I always have a near-term milestone to work toward even during long rank grinds.

#### Acceptance Criteria

1. EACH rank SHALL contain 5 levels (Level 1 through Level 5), where Level 1 is the entry point of the rank and Level 5 is the final level before promotion to the next rank.
2. THE Rank_System SHALL distribute the XP gap between two consecutive rank thresholds evenly across 5 levels. For example, if Rank 1 starts at 0 XP and Rank 2 starts at 200 XP, then Rank 1 Level 1 = 0 XP, Level 2 = 40 XP, Level 3 = 80 XP, Level 4 = 120 XP, Level 5 = 160 XP.
3. WHEN a player gains XP, THE Rank_System SHALL update the player's current level within their rank and notify the player if the level changed.
4. WHEN a player's level changes, THE Rank_System SHALL send a message to the player: "You are now {Rank Title} Level {N}."
5. THE `score` command SHALL display the player's rank title, current level, and XP progress toward the next level (e.g., "Staff Sergeant Level 3 | XP: 4200/4400 to Level 4").
6. THE level system SHALL NOT affect gameplay mechanics — agent caps, planet access, and unlocks are determined by rank only, not level.
7. FOR the final rank (Marshal at 120000 XP), THE Rank_System SHALL still compute 5 levels using a fixed interval of 10000 XP per level beyond the Marshal threshold, for cosmetic progression.

### Requirement 5: XP Sources

**User Story:** As a player, I want to earn XP through active combat and exploration, so that progression rewards engagement rather than passive play.

#### Acceptance Criteria

1. WHEN a player deals damage to another player's building, THE Rank_System SHALL award XP proportional to the damage dealt.
2. WHEN a player's agents incapacitate an enemy agent during base defense, THE Rank_System SHALL award XP to the defending player.
3. WHEN a player discovers a previously unexplored tile, THE Rank_System SHALL award exploration XP.
4. WHEN a player completes a technology research, THE Rank_System SHALL award research completion XP.
5. WHEN a player defeats a PvE enemy (NPC raider, creature, etc.), THE Rank_System SHALL award combat XP proportional to the enemy's difficulty.
6. THE Rank_System SHALL NOT award XP for passive harvesting or building construction.
7. WHEN XP is awarded or deducted, THE Rank_System SHALL log the amount, the player, and the reason.
8. WHEN all of a player's buildings on a planet are set to offline (full base wipe), THE Rank_System SHALL deduct 10% of the player's current XP.
9. WHEN a player's agents are incapacitated during a failed attack, THE Rank_System SHALL deduct a small amount of XP proportional to agents lost.
10. WHEN the player character (commander) is killed, THE Rank_System SHALL deduct 5% of the player's current XP. The player respawns at their HQ or at the location of death (player's choice).
11. THE Rank_System SHALL NOT reduce a player's XP below 0.

### Requirement 6: Building Definitions

**User Story:** As a player, I want 12 distinct building types with clear costs, build times, and upgrade paths, so that base construction involves meaningful strategic choices.

#### Acceptance Criteria

1. THE Data_Registry SHALL load exactly 12 building definitions from `buildings.yaml`: HQ, Extractor, Academy, Lab, Armory, Turret, Vault, Radar, Wall, Barracks, Medbay, and Relay.
2. WHEN the Data_Registry loads `buildings.yaml`, THE Data_Registry SHALL validate that each building entry contains `name`, `abbreviation`, `cost` (resource map), `build_time_seconds`, `max_level` (5 for all), `rank_requirement`, `max_health`, `category`, `requires_hq`, and `requires_agent` (boolean) fields.
3. THE Building_System SHALL enforce that a player must build an HQ on a planet before constructing any other building on that planet.
4. THE Building_System SHALL enforce that only one HQ per player per planet is allowed.
5. WHEN a player constructs a building, THE Building_System SHALL verify the player's rank meets or exceeds the building's `rank_requirement`.
6. WHEN a player or Engineer agent initiates construction, THE Building_System SHALL begin a construction timer. The player must remain in the "building" state (on the construction tile) for progress to continue. If the player leaves, the timer pauses. An Engineer agent assigned to the construction site progresses the timer autonomously without requiring the player's presence.
7. THE same active-presence mechanic SHALL apply to manual harvesting: the player must remain on the resource tile in the "harvesting" state for gathering to continue. An assigned Harvester agent harvests autonomously.
8. WHEN a building is upgraded, THE Building_System SHALL charge base cost multiplied by the target level and use the same active-presence or agent-based timer mechanism.
9. WHEN a building reaches 0 HP, THE Building_System SHALL set the building to offline status rather than destroying it.
10. WHEN a player repairs an offline building, THE Building_System SHALL charge 50% of the building's base construction cost and require an Engineer agent or player active-presence for the repair duration.
11. THE Extractor building SHALL require placement on a terrain tile that has a non-null resource type.
12. WHEN an Extractor is placed on a resource terrain tile, THE Extractor SHALL produce that terrain's specific resource type and store it in the Extractor's local inventory.
13. Resources stored in an Extractor's local inventory SHALL be lootable by enemy players during raids.
14. THE Vault building SHALL provide protected storage. Resources transferred to a Vault SHALL NOT be lootable by enemy players.
15. WHEN a Vault is set to offline (0 HP), THE resources inside SHALL be inaccessible to the owner until the Vault is repaired, but SHALL NOT be lootable.
16. THE Lab building SHALL enable technology research. One research project at a time per Lab. An Engineer agent is required to operate the Lab during research.
17. THE Wall building SHALL NOT require an agent to operate. Walls block enemy movement through the tile and have high HP. Walls are the cheapest building type.
18. THE Barracks building SHALL increase the player's army capacity. Without a Barracks, the player can assign up to 2 Soldiers (the commander + 1 agent). Each Barracks adds +2 additional soldier slots, +1 per Barracks level above 1.
19. THE Medbay building SHALL reduce agent incapacitation time. Each Medbay reduces respawn time by 15%. A Medic agent assigned to a Medbay increases the reduction to 25%. Multiple Medbays stack.
20. THE Relay building SHALL provide a defense bonus to all Turrets within a 5-tile radius. Each Relay adds +15% damage to nearby Turrets. Relays do not require an agent.
21. THE Building_System SHALL apply per-level bonuses: HQ (+20% HP), Extractor (+25% harvest rate, +50 storage capacity), Academy (-15% training time), Lab (-10% research time), Armory (unlock higher-tier recipes), Turret (+20% damage, +1 range), Vault (+20 storage capacity), Radar (+2 vision radius), Wall (+30% HP), Barracks (+1 soldier slot), Medbay (-5% additional respawn reduction), Relay (+1 tile radius).
22. THE Extractor SHALL have a storage capacity of 100 units. WHEN the Extractor's inventory reaches capacity, production SHALL pause until resources are transferred out. Higher levels increase capacity by +50 per level.
23. THE Vault SHALL have a base storage capacity of 100 total resource units. Higher levels increase capacity by +20 per level. A player SHALL be limited to a maximum of 3 Vault buildings per planet.
24. THE Wall SHALL allow the owning player and their agents to pass through freely outside of combat. Enemy movement through Wall tiles SHALL be blocked. During a combat timer (Requirement 17), the owning player's movement through their own Wall exits SHALL also be blocked.

### Requirement 6b: Resource Storage and Transfer

**User Story:** As a player, I want to pick up resources from buildings and drop them in my Vault using standard inventory commands, so that resource management feels natural.

#### Acceptance Criteria

1. Resources in building inventories (Extractors, Vaults, etc.) SHALL be represented as game objects that can be picked up and dropped using Evennia's standard `get` and `drop` commands.
2. WHEN a player is inside a building and issues `get <resource>`, `get all`, or `get all <resource>`, THE system SHALL move matching resource objects from the building's inventory to the player's personal inventory.
3. WHEN a player is inside a building and issues `drop <resource>`, `drop all`, or `drop all <resource>`, THE system SHALL move matching resource objects from the player's personal inventory into the building's inventory.
4. THE player's personal inventory (carried resources) SHALL be lootable on death/incapacitation (50% of carried resources dropped on the ground).
5. THE Vault SHALL only accept resource drops — it SHALL reject non-resource objects.
6. WHEN a player manually harvests resources on the overworld, THE resources SHALL be added directly to the player's personal inventory as game objects.

### Requirement 7: Shared Combat Entity Base

**User Story:** As a developer, I want players and NPCs to share a common base type for health, inventory, equipment, and incapacitation, so that combat logic, display, and state management are consistent and not duplicated.

#### Acceptance Criteria

1. THE system SHALL define a `CombatEntity` mixin or base class providing: `hp`, `hp_max`, `inventory` (resource dict), `equipment_slots` (dict), `incapacitated` (bool), `respawn_timer` (int ticks), and `respawn_location` (room reference or None).
2. THE `CombatEntity` SHALL provide methods: `take_damage(amount)`, `heal(amount)`, `is_alive()`, `incapacitate(respawn_ticks)`, `get_structured_state()`.
3. WHEN `take_damage` reduces `hp` to 0 or below, THE `CombatEntity` SHALL call `incapacitate()` with a configurable respawn duration.
4. WHEN `incapacitate()` is called, THE entity SHALL be marked as incapacitated, set a respawn timer, and become unable to act.
5. WHEN the respawn timer expires, THE entity SHALL be restored to full HP and moved to its `respawn_location`.
6. THE `CombatCharacter` typeclass SHALL extend both Evennia's `DefaultCharacter` and `CombatEntity`, adding player-specific state: XP, rank, coordinates, agent ownership, fog of war discovery, and account puppeting.
7. THE `NPC` typeclass SHALL extend both Evennia's `DefaultObject` and `CombatEntity`, adding NPC-specific state: `owner` (reference to owning player or None), `npc_type` tag (e.g., "agent", "enemy", "vendor").
8. THE `NPC` typeclass SHALL support attaching Evennia Scripts for behavior logic (e.g., harvesting loop, patrol route, combat AI).
9. THE `NPC` typeclass SHALL have a `location` that places it on a specific tile (OverworldRoom), in a building, or in the player's army (PlanetRoom at player coordinates).
10. THE `NPC` typeclass SHALL be taggable with `npc_type` category tags for efficient querying (e.g., find all agents owned by a player).
11. FOR ALL entities inheriting from `CombatEntity`, calling `take_damage(N)` then `heal(N)` SHALL return `hp` to its pre-damage value (round-trip property), capped at `hp_max`.

### Requirement 7b: Agent System

**User Story:** As a player, I want to train and assign agents to different roles, so that I can scale my base operations and military strength as I progress.

#### Acceptance Criteria

1. Agents SHALL be NPC game objects (Requirement 7) with `npc_type` tag "agent" and an `owner` reference to the player.
2. THE player character (commander) SHALL NOT be a separate agent NPC — the commander IS the CombatCharacter. The commander counts toward the agent cap as agent #1 but is the player's avatar, not an NPC object.
3. THE Agent_System SHALL enforce that the total number of agents (commander + trained NPCs) does not exceed the agent cap defined by the player's current rank.
4. THE Agent_System SHALL support 6 agent roles: Harvester, Engineer, Soldier, Guard, Scout, and Medic.
5. EACH agent SHALL have a sequential numeric ID assigned at creation (commander = 1, first trained = 2, etc.). IDs SHALL be permanent and never reused.
6. WHEN a player assigns an agent to a role, THE Agent_System SHALL set the agent NPC's `role` attribute and move it to the appropriate location (building tile for Harvester/Guard/Scout/Engineer, PlanetRoom for Soldier/Medic).
7. WHEN a player unassigns an agent, THE Agent_System SHALL clear the agent's role and move it to the player's HQ tile.
8. THE Agent_System SHALL allow reassignment of an agent from one role to another without a cooldown.
9. WHEN an agent NPC is incapacitated in combat, THE Agent_System SHALL use the CombatEntity's built-in incapacitation and respawn mechanism (Requirement 7).
10. WHEN a player issues a `list agents` command, THE Agent_System SHALL query all NPC objects tagged "agent" owned by the player and display their ID, role, location, and status.
11. THE Agent_System SHALL NOT allow assignment of an incapacitated or reserved agent to any role.
12. FOR ALL agent assignment and unassignment operations, the total count of active plus incapacitated plus reserved agents SHALL equal the roster size (invariant property).
13. WHEN a player is demoted and their agent count exceeds the new cap, THE agents with the highest IDs (most recently trained) SHALL enter "reserve" status first. Reserved agents retain their current role assignments (continuing to function) but cannot be reassigned to new roles until the player re-ranks.
14. WHEN the player is offline, ALL assigned agents SHALL continue performing their roles autonomously (Harvesters produce, Guards defend, etc.). Agent behavior scripts run on the game tick regardless of player connection status.

### Requirement 8: Agent Training

**User Story:** As a player, I want to train new agents at my Academy, so that I can grow my workforce and military over time.

#### Acceptance Criteria

1. WHEN a player initiates agent training at an Academy, THE Agent_System SHALL verify the player's current agent count is below the agent cap.
2. WHEN a player initiates agent training, THE Agent_System SHALL verify the player owns an Academy building that is online.
3. THE Agent_System SHALL charge a training cost in resources. The cost SHALL scale exponentially: agent N costs base_cost × N, where base_cost is 15 Wood, 10 Stone, 5 Iron. (Agent 2 = 30W/20S/10I, Agent 3 = 45W/30S/15I, etc.)
4. THE Agent_System SHALL set a training duration based on the Academy's level, where higher levels reduce training time by 15% per level. Base training time SHALL be 5 minutes.
5. WHEN the training duration completes, THE Agent_System SHALL add a new unassigned agent to the player's roster with a sequential ID.
6. IF a player attempts to train an agent when at the agent cap, THEN THE Agent_System SHALL return a failure message indicating the cap has been reached.
7. THE Agent_System SHALL allow only one agent to be in training per Academy at a time.

### Requirement 9: Agent Role — Harvester

**User Story:** As a player, I want to assign agents as Harvesters to Extractors, so that I earn passive resource income without manual gathering.

#### Acceptance Criteria

1. WHEN a Harvester agent is assigned to an Extractor, THE Resource_System SHALL produce the Extractor's resource type each game tick.
2. THE Resource_System SHALL scale Harvester production by the Extractor's level (+25% per level above 1).
3. WHEN a Harvester agent is unassigned from an Extractor, THE Resource_System SHALL stop producing resources from that Extractor.
4. THE Resource_System SHALL require exactly one Harvester agent per Extractor for production.
5. IF a player assigns a Harvester to a building that is not an Extractor, THEN THE Agent_System SHALL return a failure message.

### Requirement 10: Agent Role — Engineer

**User Story:** As a player, I want to assign agents as Engineers to construct and upgrade buildings, so that base expansion can happen autonomously while I do other things.

#### Acceptance Criteria

1. WHEN an Engineer agent is assigned to construct a building, THE Building_System SHALL progress the construction timer autonomously each game tick, without requiring the player's presence.
2. WHEN the construction timer completes, THE Building_System SHALL create the building on the target tile and return the Engineer to the unassigned pool.
3. IF the Engineer agent is unassigned or incapacitated during construction, THEN THE Building_System SHALL pause the construction timer. The player may resume by being present on the tile or assigning a new Engineer.
4. THE Building_System SHALL require an Engineer agent for building upgrades, using the same autonomous timer mechanism.
5. WHEN an Engineer is assigned to the Armory, THE Armory SHALL enable equipment crafting.
6. WHEN an Engineer is assigned to a Lab, THE Lab SHALL enable technology research.

### Requirement 11: Agent Role — Soldier and Medic

**User Story:** As a player, I want to assign agents as Soldiers and Medics to my army, so that I can attack other players' bases with a scaled force.

#### Acceptance Criteria

1. WHEN a Soldier agent is assigned to the army, THE Combat_Engine SHALL include that agent in attack calculations.
2. THE Combat_Engine SHALL scale attack damage based on the number of Soldier agents in the attacking army.
3. WHEN a Medic agent is assigned to the army, THE Combat_Engine SHALL heal Soldier agents after combat encounters.
4. WHEN a Soldier agent is defeated in combat, THE Agent_System SHALL mark the agent as incapacitated with a respawn timer.
5. THE Combat_Engine SHALL NOT allow an attack if the player has zero Soldier agents assigned to the army.
6. THE Combat_Engine SHALL limit the number of Soldier agents assignable to the army: base capacity of 2 (commander + 1 agent) without any Barracks, +2 per Barracks, +1 per Barracks level above 1.

### Requirement 12: Agent Role — Guard and Scout

**User Story:** As a player, I want to assign Guards to Turrets and Scouts to Radar, so that my base has automated defense and extended vision.

#### Acceptance Criteria

1. WHEN a Guard agent is assigned to a Turret, THE Turret SHALL activate and auto-attack enemies within range.
2. WHEN no Guard agent is assigned to a Turret, THE Turret SHALL remain inactive and not fire.
3. WHEN a Scout agent is assigned to a Radar, THE Radar SHALL extend the player's map vision radius by the Radar's level bonus (+2 per level).
4. WHEN no Scout agent is assigned to a Radar, THE Radar SHALL not contribute to the player's vision radius.
5. IF a player assigns a Guard to a building that is not a Turret, THEN THE Agent_System SHALL return a failure message.
6. IF a player assigns a Scout to a building that is not a Radar, THEN THE Agent_System SHALL return a failure message.

### Requirement 13: Agent Commands

**User Story:** As a player, I want intuitive commands to manage my agents, so that assignment feels natural based on context.

#### Acceptance Criteria

1. WHEN a player issues an `agents` command, THE command handler SHALL display a list of all agents with their id, role, target, and status.
2. WHEN a player is inside a building and issues `assign <agent_id>`, THE Agent_System SHALL infer the appropriate role from the building type (Extractor → Harvester, Turret → Guard, Radar → Scout, Armory → Engineer, Lab → Engineer, Medbay → Medic) and assign the agent to that building.
3. WHEN a player issues `assign <agent_id> <role>` for non-building roles (Soldier, Medic), THE Agent_System SHALL assign the agent to the player's army in that role.
4. WHEN a player issues an `unassign <agent_id>` command, THE Agent_System SHALL return the specified agent to the unassigned pool at the player's HQ.
5. WHEN a player issues a `train` command, THE Agent_System SHALL initiate agent training at the player's Academy.
6. IF a player issues `assign` without being inside a building and without specifying a role, THEN THE command handler SHALL display a usage hint.
7. IF a player issues `assign` inside a building that already has an agent assigned, THEN THE Agent_System SHALL return a message indicating the building is already staffed.

### Requirement 14: Starting Experience and Core Loop

**User Story:** As a new player, I want a guided early-game experience that teaches me the core loop through natural resource pressure, so that I learn to harvest, build, and scale without being told what to do.

#### Acceptance Criteria

1. WHEN a new CombatCharacter spawns, THE CombatCharacter SHALL be placed on Terra at the default spawn point with 30 Wood, 20 Stone, 10 Iron. The player character acts as agent #1 (the commander).
2. THE HQ SHALL cost 10 Wood, 10 Stone, 10 Iron — exactly the player's starting Iron supply, teaching that Iron is scarce and valuable.
3. THE Extractor SHALL cost 20 Wood, 10 Stone — the player's remaining starting resources (20 Wood, 10 Stone) cover exactly one Extractor after building the HQ.
4. AFTER building HQ + 1 Extractor, THE player SHALL have 0 resources remaining, requiring them to manually harvest to build a second Extractor.
5. THE second Extractor SHALL require ~2-3 minutes of manual harvesting to afford (20 Wood, 10 Stone). At 40 units/min raw throughput, the 2-3 minute estimate accounts for travel time between resource tiles.
6. THE Academy SHALL cost 40 Wood, 30 Stone, 15 Iron — deliberately more than the player can afford from 2 Extractors alone in the short term, requiring either patience (wait for Extractors to accumulate) or manual Iron harvesting from Mountain tiles.
7. THE Resource_System SHALL support manual harvesting at a rate of 2 units per harvest action, with a 3-second cooldown between harvests (~40 units per minute at maximum efficiency). The player must remain on the resource tile in the "harvesting" state for gathering to continue.
8. THE Extractor (Lv1) with a Harvester agent SHALL produce 5 units of its resource type per minute, accumulated fractionally across game ticks.
9. THE full early-game progression from spawn to functional base (HQ, 2 Extractors, Academy, 1 trained agent, Vault, Turret) SHALL take approximately 60 minutes of active play.
10. THE game loop SHALL support progression from manual survival (Ranks 1-3) to base management (Ranks 4-7) to strategic PvP (Ranks 8-12).

### Requirement 14b: Building Cost Table

**User Story:** As a developer, I want building costs calibrated to the economy pacing, so that early game feels tight but achievable and mid-game scales with Extractor output.

#### Acceptance Criteria

1. THE `buildings.yaml` SHALL define the following base construction costs, build times, and base HP:
   - HQ: 10 Wood, 10 Stone, 10 Iron — 3 min — 500 HP
   - Extractor: 20 Wood, 10 Stone — 2 min — 200 HP
   - Wall: 15 Wood, 10 Stone — 1 min — 800 HP
   - Academy: 40 Wood, 30 Stone, 15 Iron — 5 min — 300 HP
   - Lab: 30 Stone, 20 Iron — 4 min — 250 HP
   - Armory: 35 Stone, 25 Iron — 4 min — 300 HP
   - Turret: 30 Iron, 20 Energy — 4 min — 350 HP
   - Vault: 40 Stone, 25 Iron — 5 min — 400 HP
   - Barracks: 30 Wood, 20 Iron — 3 min — 300 HP
   - Medbay: 25 Stone, 20 Iron, 10 Circuits — 4 min — 250 HP
   - Radar: 25 Iron, 20 Circuits — 4 min — 200 HP
   - Relay: 30 Iron, 25 Energy, 15 Circuits — 5 min — 300 HP
2. UPGRADE costs SHALL be base cost multiplied by the target level (e.g., Lv2 = base × 2, Lv3 = base × 3).
3. REPAIR costs SHALL be 50% of the building's base construction cost.
4. THE Barracks SHALL have a rank requirement of 3 (Corporal).

### Requirement 17: Combat Timer

**User Story:** As a player, I want a combat timer that prevents me from fleeing through my own walls during an attack, so that base defense is a real commitment.

#### Acceptance Criteria

1. WHEN the player character sees an enemy player or NPC on their map (within vision radius), OR when any of the player's buildings or agents take damage, THE system SHALL start a 60-second combat timer on the player.
2. WHILE the combat timer is active, THE player SHALL NOT be able to move through their own Wall tiles (Walls block all movement, including the owner).
3. WHEN the combat timer expires without further combat triggers, THE player SHALL regain free movement through their own Walls.
4. EACH new combat event (damage taken, enemy spotted) SHALL reset the combat timer to 60 seconds.
5. THE combat timer SHALL be displayed to the player in the UI (e.g., "Combat: 45s").

### Requirement 18: Offline Behavior

**User Story:** As a player, I want my base to continue operating when I log out, so that I make progress even when I'm not playing.

#### Acceptance Criteria

1. WHEN a player logs out, ALL assigned agents SHALL continue performing their roles via game tick scripts (Harvesters produce, Guards defend, Extractors accumulate).
2. WHEN a player logs out, THE player's buildings SHALL remain on the map and be attackable by other players.
3. WHEN a player logs out, THE player's Turrets with Guard agents SHALL continue to auto-defend against attackers.
4. WHEN a player logs back in, THE system SHALL display a summary of events that occurred while offline (resources accumulated, attacks received, agents incapacitated, etc.).

### Requirement 15: YAML Definition Files

**User Story:** As a developer, I want all game content defined in YAML files loaded at startup, so that content can be tuned without code changes.

#### Acceptance Criteria

1. THE Data_Registry SHALL load planet definitions from `mygame/data/definitions/planets.yaml`.
2. THE Data_Registry SHALL load terrain definitions from `mygame/data/definitions/terrain.yaml`.
3. THE Data_Registry SHALL load building definitions from `mygame/data/definitions/buildings.yaml`.
4. THE Data_Registry SHALL load rank definitions from `mygame/data/definitions/ranks.yaml`.
5. IF a YAML definition file contains invalid or missing required fields, THEN THE Data_Registry SHALL raise a descriptive error at startup identifying the file and the validation failure.
6. FOR ALL YAML definition files, loading the file and re-serializing the parsed definitions back to YAML and re-loading SHALL produce equivalent definition objects (round-trip property).
7. WHEN the server starts, THE `game_init.py` module SHALL initialize all game systems using definitions loaded from YAML files and register them in the `game_systems` dictionary.

### Requirement 16: Backward Compatibility

**User Story:** As a developer, I want Phase 1 changes to be backward-compatible with existing systems, so that the webclient, fog of war, map rendering, and combat stubs continue to function.

#### Acceptance Criteria

1. WHEN terrain definitions are updated, THE Terrain_Generator SHALL continue to produce deterministic terrain using the same hash-based value noise algorithm.
2. WHEN planet definitions are updated, THE Planet_Registry SHALL continue to support `is_valid_coordinate`, `resolve_planet`, and `get_space` with the same interface.
3. WHEN building definitions are updated, THE Building_System SHALL continue to support `construct`, `upgrade`, and `destroy` with the same method signatures.
4. WHEN rank definitions are updated, THE Rank_System SHALL continue to support `award_xp`, `check_promotion`, and `get_rank` with the same method signatures.
5. THE CombatCharacter SHALL continue to expose `get_resource`, `add_resource`, `has_resources`, `deduct_resources`, and `get_buildings` with the same method signatures.
6. WHEN the resource type list changes from 8 types to 6 types, THE CombatCharacter SHALL migrate existing characters by mapping old resource types to new ones or zeroing removed types.

### Requirement 19: Agent and NPC Map Visibility

**User Story:** As a player, I want to see agents and NPCs on the map with distinct colors, so that I can assess the battlefield at a glance.

#### Acceptance Criteria

1. THE map renderer (both ASCII and graphical webclient) SHALL display NPC agents on the overworld map using a 2-character symbol (e.g., `ag` or agent role abbreviation).
2. THE player's own agents on the overworld SHALL be rendered in green.
3. Enemy agents (owned by other players) on visible tiles SHALL be rendered in red.
4. Neutral/unowned NPCs (future: vendors, creatures) SHALL be rendered in yellow.
5. WHEN a building tile contains any entity inside it (player, agent, or NPC), THE building symbol SHALL be rendered in dark blue instead of the normal building color, indicating occupancy without revealing whether the occupant is a player or agent.
6. THE display priority for overworld tiles SHALL be: player self (`@@` yellow) > enemy player (`**` red) > own agent (green) > enemy agent (red) > neutral NPC (yellow) > occupied building (dark blue) > unoccupied building (cyan own / red enemy) > terrain.
7. THE graphical webclient map_data_provider SHALL include agent data in the tile JSON: `"agents": [{"own": bool, "role": str}]` for visible tiles, enabling the Canvas renderer to draw agent markers.
8. AGENTS inside buildings SHALL NOT appear as separate symbols on the map — only the building's color change (dark blue) indicates occupancy.
