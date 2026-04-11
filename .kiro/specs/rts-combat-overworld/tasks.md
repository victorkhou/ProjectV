# Implementation Plan: RTS Combat Overworld

## Overview

This plan implements an RTS-inspired combat overworld game on the Evennia MU* framework. Implementation proceeds bottom-up: data layer and schema validation first, then core typeclasses, then game systems, then commands and presentation, and finally infrastructure scripts. Each task builds on previous ones so there is no orphaned code.

## Tasks

- [x] 1. Data layer: definition dataclasses, schema validation, and Data Registry
  - [x] 1.1 Create definition dataclasses (`world/definitions.py`)
    - Define `BuildingDef`, `ItemDef`, `RankDef`, `TechnologyDef`, `PowerupDef`, `TerrainDef`, `PlanetDef`, `BalanceConfig` as Python dataclasses
    - These are plain data containers used by the Data Registry and all game systems
    - _Requirements: 17.2, 18.2, 19.2, 20.2, 21.2, 21.3, 22.2, 24.2_

  - [x] 1.2 Create Schema Validator (`world/schema_validator.py`)
    - Implement `validate_buildings`, `validate_items`, `validate_ranks`, `validate_technologies`, `validate_powerups`, `validate_terrain`, `validate_balance`
    - Implement `cross_validate` for inter-file references (terrain→buildings, rank→techs, production_map→items/buildings)
    - Each method returns a list of error strings (empty = valid)
    - _Requirements: 17.3, 17.4, 17.5, 18.4, 19.3, 20.3, 21.4, 22.3, 24.4_

  - [x] 1.3 Write property test for schema validation
    - **Property 26: Schema validation catches invalid definitions**
    - **Validates: Requirements 17.2, 17.3, 17.4, 17.5, 18.4, 19.3, 20.3, 21.4, 22.3**

  - [x] 1.4 Create Data Registry (`world/data_registry.py`)
    - Implement `DataRegistry` class with `load_all`, `reload_all`, and all getter methods
    - Load YAML files, pass through SchemaValidator, populate registry dicts
    - Implement hot-reload with atomic swap on success, preserve on failure
    - Implement hardcoded defaults for missing balance config with logged warning
    - Abort startup on missing required definition files
    - _Requirements: 25.1, 25.2, 25.4, 17.1, 17.6, 18.1, 18.5, 19.1, 19.4, 20.1, 20.4, 21.1, 21.5, 22.1, 22.4, 24.1, 24.3_

  - [x] 1.5 Write property test for hot-reload atomicity
    - **Property 27: Hot-reload atomicity**
    - **Validates: Requirements 26.2, 26.3, 26.4**

  - [x] 1.6 Write unit tests for Data Registry loading
    - Test loading all definition files successfully
    - Test missing required file aborts startup
    - Test missing balance config uses defaults with warning
    - Test getter methods return correct definitions
    - _Requirements: 25.1, 25.2, 25.4, 24.3_

- [x] 2. Create YAML definition files
  - [x] 2.1 Create building definitions (`data/definitions/buildings.yaml`)
    - Define all 11 building types: HQ, MM, QQ, II, LL, KK, AA, AR, VV, TL, HV
    - Include name, abbreviation, cost, max_health, requires_hq, required_terrain, category, produces, unlocks, map_symbol
    - _Requirements: 4.1, 4.2, 4.3, 4.5, 4.6, 4.7, 17.1, 17.2_

  - [x] 2.2 Create item definitions (`data/definitions/items.yaml`)
    - Define weapon, armor, gadget items with slot, stat_modifiers, ammo_cost, classification, required_rank
    - Define production_map mapping building abbreviations to item keys
    - _Requirements: 18.1, 18.2, 18.3_

  - [x] 2.3 Create rank definitions (`data/definitions/ranks.yaml`)
    - Define all 22 ranks with name, level, xp_threshold (strictly increasing), unlocks
    - _Requirements: 7.1, 7.2, 19.1, 19.2_

  - [x] 2.4 Create technology definitions (`data/definitions/technologies.yaml`)
    - Define technologies with name, key, required_rank, resource_cost, research_ticks, effect_type, effect_value
    - _Requirements: 20.1, 20.2_

  - [x] 2.5 Create powerup definitions (`data/definitions/powerups.yaml`)
    - Define powerups with name, key, required_rank, effect_type, effect_value, duration_ticks, cooldown_ticks
    - _Requirements: 22.1, 22.2_

  - [x] 2.6 Create terrain and planet definitions (`data/definitions/terrain.yaml`)
    - Define Earth_Planet terrain (Plains, Mud, Forest, Rock, Mountain) and Industrial_Planet terrain (Power_Grid, Scrapyard, Circuit_Field, Ruins)
    - Define planet entries referencing terrain types
    - _Requirements: 1.2, 1.3, 1.4, 2.1, 2.2, 21.1, 21.2, 21.3_

  - [x] 2.7 Create balance configuration (`data/config/balance.yaml`)
    - Define production_scaling, turret_damage, turret_radius, xp values, gather_amount, player_default_health, resource_respawn_ticks, combat_lockout_ticks, tick_interval, chunk_size, save_interval, metrics settings
    - _Requirements: 24.1, 24.2_

- [x] 3. Checkpoint - Validate data layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Event Bus
  - [x] 4.1 Implement Event Bus (`world/event_bus.py`)
    - Wrap Evennia's `SignalsHandler` as a global singleton
    - Implement `publish`, `subscribe`, `unsubscribe`
    - Define all 15 event names from the design
    - _Requirements: 28.1, 28.2, 28.3_

  - [x] 4.2 Write property test for event bus delivery
    - **Property 28: Event bus publish-subscribe delivery**
    - **Validates: Requirements 28.1, 28.2**

- [x] 5. Core typeclasses
  - [x] 5.1 Implement OverworldRoom (`typeclasses/rooms.py`)
    - Extend `XYZRoom` with terrain_type (Tag), resource_node, building, planet_name properties
    - Implement `get_display_symbol` with priority logic, `at_object_receive`, `get_structured_state`
    - _Requirements: 1.1, 1.5, 1.8, 27.1_

  - [x] 5.2 Write property test for tile display symbol priority
    - **Property 1: Tile display symbol priority**
    - **Validates: Requirements 1.8**

  - [x] 5.3 Implement GameItem (`typeclasses/objects.py` — `GameItem`)
    - Extend `DefaultObject` with item_key, slot, stat_modifiers, ammo_cost, classification, required_rank attributes
    - Implement `item_def`, `slot`, `stat_modifiers`, `ammo_cost` properties and `get_stat` method
    - _Requirements: 18.6, 18.7_

  - [x] 5.4 Implement EquipmentHandler (`world/equipment_handler.py`)
    - Implement `equip`, `unequip`, `get_equipped`, `get_all_equipped`, `get_stat_total`, `get_slot_names`
    - Enforce one item per slot, auto-unequip on occupied slot, slot matching validation
    - Store equipped items as Attribute on character (`equipment_slots` → dict of slot → dbref)
    - _Requirements: 6.2, 6.17, 6.18_

  - [x] 5.5 Write property tests for EquipmentHandler
    - **Property 31: EquipmentHandler slot management**
    - **Property 32: Equip/unequip round-trip**
    - **Validates: Requirements 6.2, 6.17, 6.18**

  - [x] 5.6 Implement CombatCharacter (`typeclasses/characters.py`)
    - Extend `DefaultCharacter` with Traits: HP (GaugeTrait), combat_xp (CounterTrait), rank_level (StaticTrait), 8 resource CounterTraits
    - Initialize EquipmentHandler, active_powerups, powerup_cooldowns, researched_techs, combat_lockout_tick attributes
    - Implement resource helpers: `get_resource`, `add_resource`, `deduct_resources`, `has_resources`
    - Implement `get_buildings`, `get_structured_status`, `at_post_login`, `at_pre_disconnect`
    - _Requirements: 2.4, 7.8, 10.1, 10.4, 27.1_

  - [x] 5.7 Write property test for resource trait accounting
    - **Property 3: Resource trait accounting**
    - **Validates: Requirements 2.4**

  - [x] 5.8 Implement Building (`typeclasses/objects.py` — `Building`)
    - Extend `DefaultObject` with building_type, owner, building_level, offline attributes
    - HP as GaugeTrait initialized from building_def.max_health
    - Implement `building_def`, `owner`, `is_offline`, `building_level` properties
    - Implement `set_offline`, `take_damage`, `get_display_abbreviation`, `get_structured_state`
    - _Requirements: 3.6, 3.7, 3.8, 10.1, 10.5, 27.1_

- [x] 6. Checkpoint - Validate core typeclasses
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Building System
  - [x] 7.1 Implement Building System (`world/building_system.py`)
    - Implement `construct` with full validation chain: HQ requirement, terrain match, tile empty, build range, combat lockout, sufficient resources
    - Implement `upgrade` with level validation, cost formula (base_cost × target_level), max level 5
    - Implement `destroy` with event publishing and building removal
    - Implement `set_player_buildings_offline` for offline protection transitions
    - Publish `building_constructed`, `building_destroyed`, `building_upgraded` events
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.8, 3.9, 3.10, 3.11, 4.2, 4.4, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 7.2 Write property tests for Building System
    - **Property 6: HQ prerequisite enforcement**
    - **Property 7: Building construction resource deduction**
    - **Property 8: Terrain-restricted building placement**
    - **Property 9: Resource building level invariant**
    - **Property 11: Upgrade cost formula**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.6, 4.2, 4.4, 5.1, 5.4, 5.6, 5.7**

- [x] 8. Resource System
  - [x] 8.1 Implement Resource System (`world/resource_system.py`)
    - Implement `harvest` with resource node validation, terrain-to-resource mapping, 1-unit yield, depletion tracking
    - Implement `process_production` for active resource buildings (yield = `balance.production_scaling[level]`)
    - Implement `process_respawns` for depleted node respawn counters
    - Publish `resource_gathered` events
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 5.2, 5.8, 15.1, 15.2, 15.3, 15.4_

  - [x] 8.2 Write property tests for Resource System
    - **Property 4: Harvest yields correct resource type**
    - **Property 5: Resource node respawn cycle**
    - **Property 10: Resource production scales with level**
    - **Validates: Requirements 2.3, 2.6, 2.7, 5.2, 15.1, 15.2, 15.4**

- [x] 9. Combat Engine
  - [x] 9.1 Implement Combat Engine (`world/combat_engine.py`)
    - Implement `queue_attack` with range validation, ammo validation, self-attack prevention
    - Implement `resolve_tick` processing pending actions in FIFO order
    - Implement damage calculation: weapon damage + tech/powerup modifiers - armor damage_reduction (min 0)
    - Implement `_handle_player_defeat`: award XP to attacker, deduct XP from victim, respawn
    - Implement `_handle_building_destruction`: award XP, remove building, publish events
    - Implement `process_turrets`: auto-attack nearest hostile within turret_radius
    - Manage combat lockout state (combat_lockout_ticks)
    - Publish `combat_action`, `player_eliminated`, `building_destroyed` events
    - Notify targets of attack details
    - _Requirements: 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, 6.13, 6.14, 6.15, 6.16_

  - [x] 9.2 Write property tests for Combat Engine
    - **Property 12: Attack damage application**
    - **Property 13: Turret targets nearest hostile**
    - **Property 14: Player defeat consequences**
    - **Property 15: Combat lockout prevents building**
    - **Property 16: Attack resolution ordering**
    - **Validates: Requirements 6.1, 6.3, 6.4, 6.5, 6.6, 6.9, 6.10, 6.11, 6.16**

- [x] 10. Rank System
  - [x] 10.1 Implement Rank System (`world/rank_system.py`)
    - Implement `award_xp` and `deduct_xp` with promotion/demotion checks
    - Implement `check_promotion`: promote when XP ≥ next rank threshold, unlock techs/powerups
    - Implement `check_demotion`: demote when XP < current rank threshold, revoke techs/powerups
    - Publish `rank_promoted`, `rank_demoted` events
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10_

  - [x] 10.2 Write property tests for Rank System
    - **Property 17: Rank assignment from XP**
    - **Property 18: Rank-gated access consistency**
    - **Property 19: Strictly increasing rank thresholds**
    - **Validates: Requirements 7.2, 7.3, 7.5, 7.6, 7.7**

- [x] 11. Checkpoint - Validate core game systems
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Powerup System
  - [x] 12.1 Implement Powerup System (`world/powerup_system.py`)
    - Implement `activate` with rank check, cooldown check, apply effect to player stats
    - Implement `process_tick` to decrement durations, remove expired powerups, publish `powerup_expired`
    - Implement `get_active_powerups` and `get_stat_modifier`
    - Store active powerups and cooldowns as Attributes on CombatCharacter
    - Publish `powerup_activated` events
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [x] 12.2 Write property tests for Powerup System
    - **Property 20: Powerup activation and expiry round-trip**
    - **Property 21: Powerup cooldown enforcement**
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.5**

- [x] 13. Tech Lab System
  - [x] 13.1 Implement Tech Lab System (`world/tech_system.py`)
    - Implement `list_available` filtered by player rank
    - Implement `start_research` with rank check, resource deduction, timer start
    - Implement `process_tick` to decrement research timers, apply completed techs
    - Implement `apply_technology` for stat bonuses and building/item unlocks
    - Publish `technology_researched` events
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 13.2 Write unit tests for Tech Lab System
    - Test research timer countdown and completion
    - Test rank-gated research rejection
    - Test resource deduction on research start
    - Test technology effect application
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 14. Equipment System
  - [x] 14.1 Implement Equipment System (`world/equipment_system.py`)
    - Implement `process_production` for active equipment buildings (Armory, Armorer)
    - Look up producible items via `registry.get_items_for_building(building_abbr)`
    - Create GameItem instances with correct slot and stat_modifiers from item definitions
    - Add produced items to owner's inventory
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [x] 14.2 Write property test for equipment production
    - **Property 25: Equipment production per tick**
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4**

- [x] 15. Offline Building Protection
  - [x] 15.1 Implement offline protection logic
    - Wire `CombatCharacter.at_pre_disconnect` to call `building_system.set_player_buildings_offline(player, True)`
    - Wire `CombatCharacter.at_post_login` to call `building_system.set_player_buildings_offline(player, False)`
    - Ensure combat engine skips damage to offline buildings with notification
    - Ensure movement is blocked to tiles with offline buildings
    - Ensure production is suspended for offline buildings
    - Publish `player_login`, `player_logout` events
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 15.2 Write property test for offline building protection
    - **Property 22: Offline building protection round-trip**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

- [x] 16. Checkpoint - Validate all game systems
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. World Chunking
  - [x] 17.1 Implement World Chunk Manager (`world/chunking.py`)
    - Implement chunk coordinate calculation: `(x // chunk_size, y // chunk_size)`
    - Implement `get_active_chunks` based on online player positions (chunk + 1 radius)
    - Implement `get_tiles_in_chunks` and `get_buildings_in_chunks`
    - _Requirements: 31.1, 31.2, 31.3, 31.4, 31.5_

  - [x] 17.2 Write property tests for World Chunking
    - **Property 29: World chunk activation**
    - **Property 30: Chunk coordinate assignment**
    - **Validates: Requirements 31.1, 31.2, 31.3**

- [x] 18. Game Tick Script and Auto-Save
  - [x] 18.1 Implement Game Tick Script (`typeclasses/scripts.py` — `GameTickScript`)
    - Persistent DefaultScript with configurable interval (default 1s)
    - `at_repeat` orchestrates: active chunks → resource production → equipment production → combat resolution → turret attacks → powerup ticks → tech research ticks → resource respawns → publish tick_completed → record metrics
    - Each processing step wrapped in try/except for error resilience
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 18.2 Write property test for tick error resilience
    - **Property 23: Tick error resilience**
    - **Validates: Requirements 11.3**

  - [x] 18.3 Implement Auto-Save Script (`typeclasses/scripts.py` — `AutoSaveScript`)
    - Persistent DefaultScript with configurable interval (default 30 ticks)
    - Async save of all connected player states
    - Error handling: log and retry next interval
    - _Requirements: 32.1, 32.2, 32.3, 32.4_

- [x] 19. Presentation layer
  - [x] 19.1 Implement ASCII Map Renderer (`world/map_renderer.py`)
    - Implement `render` method: center on player, show tiles within sight range
    - Implement `get_tile_symbol` with display priority (player > building > terrain)
    - Implement Fog of War: tiles outside sight range show only terrain symbols
    - _Requirements: 1.7, 1.8, 1.9, 27.2, 27.4_

  - [x] 19.2 Write property test for Fog of War
    - **Property 2: Fog of War filtering**
    - **Validates: Requirements 1.9**

  - [x] 19.3 Implement Notification System (`world/notification_system.py`)
    - Subscribe to events: player_login, player_logout, player_eliminated, rank_promoted, rank_demoted
    - Broadcast formatted messages via `SESSION_HANDLER` to all connected sessions
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 28.4_

  - [x] 19.4 Implement Chat System (`world/chat_system.py`)
    - Implement `ensure_global_channel` using `ChannelDB.objects.get_or_create`
    - Implement `auto_subscribe` called from `at_post_login`
    - Override channel message formatting to include rank: `"[{rank}] {name}: {message}"`
    - Override DM formatting to include rank
    - Delegate local say to Evennia's built-in `say`, DMs to Evennia's `page`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8_

  - [x] 19.5 Write property test for chat message delivery scope
    - **Property 24: Chat message delivery scope**
    - **Validates: Requirements 13.3, 13.4, 13.5, 13.6, 13.8**

- [x] 20. Structured Logger and Metrics
  - [x] 20.1 Implement Structured Logger (`world/logging.py`)
    - Wrap Python's `logging` module with structured context fields
    - Each entry: timestamp, log level, logger name, event type, context key-value pairs
    - Support human-readable and JSON output formats
    - _Requirements: 29.1, 29.2, 29.3_

  - [x] 20.2 Implement Metrics Collector (`world/metrics.py`)
    - Track connected_players, commands_processed, tick_duration_ms, combat_actions, buildings_constructed, errors
    - Log summary at configurable interval (default 60s)
    - Lightweight: < 1ms overhead per tick
    - Enabled/disabled via `balance.metrics_enabled`
    - _Requirements: 30.1, 30.2, 30.3, 30.4_

- [x] 21. Checkpoint - Validate infrastructure and presentation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 22. Player commands
  - [x] 22.1 Implement game commands (`commands/game_commands.py`)
    - `move <direction>`: move to adjacent tile within one tick
    - `look` / `map`: display local ASCII map via MapRenderer
    - `harvest`: gather resource from current tile via ResourceSystem
    - `build <type>`: construct building via BuildingSystem
    - `upgrade <building>`: upgrade resource building via BuildingSystem
    - `attack <target>`: queue attack via CombatEngine
    - `equip <item>`: equip GameItem via EquipmentHandler
    - `unequip <slot>`: unequip item via EquipmentHandler
    - `research <tech>`: start research via TechLabSystem
    - `powerup <key>`: activate powerup via PowerupSystem
    - `status`: display player status (rank, XP, HP, resources, equipment, powerups)
    - `buildings`: list owned buildings with type, location, level, HP
    - `scan`: show visible entities within sight range
    - `technology`: list researched and available technologies
    - `inventory`: display resources and GameItems by slot
    - `chat <message>`: send to Global channel
    - `message <player> <text>`: delegate to Evennia's `page`
    - `say <message>`: delegate to Evennia's `say`
    - _Requirements: 1.6, 1.7, 1.10, 2.3, 2.5, 3.3, 5.3, 6.2, 6.3, 6.4, 6.8, 6.17, 8.2, 9.2, 13.3, 13.5, 13.8, 16.1, 16.2, 16.3, 16.4, 16.5_

  - [x] 22.2 Write unit tests for player commands
    - Test each command's validation and error messages
    - Test status/buildings/scan/technology/inventory output formatting
    - Test movement rejection on impassable tiles
    - _Requirements: 1.6, 1.10, 2.5, 3.4, 6.8, 6.12, 16.1, 16.2, 16.3, 16.4, 16.5_

- [x] 23. Admin commands
  - [x] 23.1 Implement admin commands (`commands/admin_commands.py`)
    - `@reloaddata`: trigger hot-reload of all YAML definition files, restricted to Builder+
    - `@giveresource <player> <resource> <amount>`: add resources to player trait counters, restricted to Builder+
    - Use Evennia's `perm()` lock function for access control
    - Log all executions with operator name, command, and target
    - Hook `@reloaddata` into Evennia's `at_server_reload()` for auto-revalidation
    - _Requirements: 26.1, 33.1, 33.2, 33.3, 33.4, 33.5_

  - [x] 23.2 Write unit tests for admin commands
    - Test permission checks (Builder+ required)
    - Test @reloaddata success and failure paths
    - Test @giveresource adds correct resource amounts
    - Test execution logging
    - _Requirements: 33.3, 33.4_

- [x] 24. Wire everything together: server startup and command sets
  - [x] 24.1 Create server startup initialization
    - Initialize DataRegistry and load all definitions at startup
    - Initialize EventBus singleton
    - Initialize all game systems with registry and event bus
    - Wire event subscribers (NotificationSystem, RankSystem XP awards, BuildingSystem offline transitions, TechTree evaluation)
    - Initialize ChatSystem and ensure Global channel exists
    - Start GameTickScript and AutoSaveScript
    - _Requirements: 25.2, 25.3, 28.4_

  - [x] 24.2 Register command sets
    - Add game commands to default character command set
    - Add admin commands to admin command set
    - Ensure all commands are available to appropriate permission levels
    - _Requirements: 33.3_

  - [x] 24.3 Write integration tests
    - Test full build → upgrade → production cycle
    - Test full combat → defeat → XP → rank change cycle
    - Test Tech Lab research → completion → effect application
    - Test login → build → logout → offline protection → login → resume
    - Test hot-reload with running game tick
    - _Requirements: 3.3, 5.2, 6.6, 7.3, 7.5, 8.3, 10.1, 10.4, 26.3_

- [x] 25. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 32 universal correctness properties from the design
- Unit tests validate specific examples and edge cases
- The design uses Python throughout — all code targets the Evennia framework (Python/Django)
- Evennia built-in commands (@tel, @boot, @examine, @perm, @ban, say, page) are used directly and not reimplemented
