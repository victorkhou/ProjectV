# Implementation Plan: Game Content Phase 1 — Core Game Loop

## Overview

Implements the core game loop by extending existing systems (~85% reuse) and creating 5 new files. Ordered in 4 phases: Foundation (definitions, YAML, CombatEntity, NPC), Systems (building timer, active-presence, ranks, agents), Integration (commands, tick script, map rendering), and Testing (property-based + unit + integration tests). Each task builds on previous tasks with no orphaned code.

## Tasks

- [x] 1. Phase 1A — Foundation: Definition Extensions and YAML Content
  - [x] 1.1 Extend definition dataclasses with new fields
    - Add `build_time_seconds`, `max_level`, `rank_requirement`, `requires_agent`, `storage_capacity` to `BuildingDef` in `world/definitions.py` with backward-compatible defaults
    - Add `agent_cap`, `planet_access` to `RankDef` in `world/definitions.py` with backward-compatible defaults
    - Add `rank_requirement` to `CoordinateSpaceDef` in `world/definitions.py` with default `1`
    - _Requirements: 1.3, 6.2, 4.10, 4.11_

  - [x] 1.2 Update YAML definition files with Phase 1 content
    - Rewrite `data/definitions/planets.yaml` with 6 planets (Terra, Forge, Tundra, Inferno, Citadel, Space) including `rank_requirement` fields, terrain weights summing to 1.0, and Terra as default
    - Rewrite `data/definitions/terrain.yaml` with 48 terrain types (8 per planet) with correct resource associations (Wood, Stone, Iron, Energy, Circuits, Nexium)
    - Rewrite `data/definitions/ranks.yaml` with 12 ranks, XP thresholds, `agent_cap`, `planet_access`, and `unlocks` fields
    - Rewrite `data/definitions/buildings.yaml` with 12 building types including new fields (`build_time_seconds`, `max_level`, `rank_requirement`, `requires_agent`, `storage_capacity`, costs per Requirement 14b)
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 2.1, 2.6, 3.1, 4.1, 4.10, 4.11, 4.12, 6.1, 6.2, 14b.1_

  - [x] 1.3 Extend DataRegistry and SchemaValidator for new fields
    - Update `_populate_buildings` in `world/data_registry.py` to read new BuildingDef fields
    - Update `_populate_ranks` to read `agent_cap` and `planet_access`
    - Update `SchemaValidator` to validate new required fields in buildings and ranks
    - Update `PlanetRegistry.load_from_yaml` to read `rank_requirement` into CoordinateSpaceDef
    - Add cross-validation: terrain types in planet weights must exist in terrain definitions
    - _Requirements: 1.2, 2.2, 6.2, 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 1.4 Write property tests for definition round-trip and validation
    - **Property 1: Definition YAML Round-Trip** — serialize/deserialize CoordinateSpaceDef, TerrainDef, BuildingDef, RankDef
    - **Validates: Requirements 1.7, 15.6**
    - **Property 2: Definition Validation Rejects Invalid Input** — missing/invalid fields rejected, valid fields accepted
    - **Validates: Requirements 1.2, 2.2, 6.2**

  - [x] 1.5 Create CombatEntity mixin (`typeclasses/combat_entity.py`)
    - New file: pure Python mixin with no Evennia base class
    - Implement `at_combat_entity_init()`, `take_damage(amount)`, `heal(amount)`, `is_alive()`, `incapacitate(respawn_ticks)`, `tick_respawn()`, `get_structured_state()`
    - `take_damage` calls `incapacitate()` when hp reaches 0
    - `heal` caps at `hp_max`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 1.6 Write property test for CombatEntity damage/heal round-trip
    - **Property 10: CombatEntity Damage/Heal Round-Trip** — take_damage(N) then heal(N) restores hp, capped at hp_max
    - **Validates: Requirements 7.11**

  - [x] 1.7 Create NPC typeclass (`typeclasses/npcs.py`)
    - New file: `class NPC(CombatEntity, DefaultObject)` with `at_object_creation` calling `at_combat_entity_init()`
    - Set attributes: `owner`, `npc_type`, `agent_id`, `role`, `role_target`, `reserve`
    - Add tags: `("agent", "npc_type")`, `("player_<owner_id>", "agent_owner")`
    - _Requirements: 7.7, 7.8, 7.9, 7.10_

  - [x] 1.8 Refactor CombatCharacter to extend CombatEntity
    - Modify `typeclasses/characters.py`: `CombatCharacter(CombatEntity, DefaultCharacter)`
    - Call `at_combat_entity_init()` in `at_object_creation()`
    - Update resource types from 8 to 6: `{Wood: 30, Stone: 20, Iron: 10, Energy: 0, Circuits: 0, Nexium: 0}`
    - Add new attributes: `next_agent_id = 2`, `activity_state = "idle"`, `activity_target = None`, `activity_progress = 0`, `combat_timer_expires = 0`
    - Update `RESOURCE_TYPES` constant to match new 6 types
    - Preserve all existing method signatures for backward compatibility
    - _Requirements: 3.2, 3.3, 7.6, 14.1, 16.5, 16.6_


  - [x] 1.9 Write property tests for resources and rank resolution
    - **Property 6: Resource Add/Deduct Round-Trip** — add then deduct same amount returns to original state
    - **Validates: Requirements 3.7**
    - **Property 7: Resource Deduction Rejection Preserves State** — insufficient deduction fails without modifying state
    - **Validates: Requirements 3.6**
    - **Property 8: Rank Resolution Is a Total Function** — for any XP in [0, 120000], resolves to exactly one rank
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.9, 4.13**

- [x] 2. Checkpoint — Foundation verification
  - Ensure all tests pass, ask the user if questions arise.
  - Verify DataRegistry loads all 6 planets, 48 terrain types, 12 ranks, 12 buildings without errors
  - Verify CombatEntity mixin works with both CombatCharacter and NPC
  - Verify backward compatibility: existing TerrainGenerator, PlanetRegistry, BuildingSystem, RankSystem interfaces unchanged

- [x] 3. Phase 1B — Systems: Building Timer, Active-Presence, Ranks, Agents
  - [x] 3.1 Extend BuildingSystem with construction timer and active-presence
    - Add `start_construction(player, tile, building_abbr)` method that sets `activity_state = "building"` on the player and stores `construction_progress` / `construction_total` on the building
    - Add `process_construction_tick(player)` that increments progress when player is on the correct tile in "building" state
    - Add `process_agent_construction(buildings)` for Engineer agents progressing construction autonomously
    - Modify `construct()` to check `rank_requirement` from BuildingDef
    - Add building inventory support: `assigned_agent` attribute, `construction_progress`, `construction_total` attributes on Building objects
    - _Requirements: 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11_

  - [x] 3.2 Extend ResourceSystem with active-presence harvesting and Extractor inventory
    - Add `start_harvest(player, tile)` method that sets `activity_state = "harvesting"` on the player
    - Add `process_harvest_tick(player)` that yields resources when player is on resource tile in "harvesting" state (2 units per action, 3-second cooldown)
    - Add `process_extractor_production(buildings)` for Harvester agent production per tick, scaled by Extractor level (+25% per level)
    - Add Extractor inventory management: resource stack objects in building contents, storage capacity check (100 + 50 × (L-1))
    - _Requirements: 3.4, 3.5, 6.6, 6.7, 6.12, 6.22, 9.1, 9.2, 9.3, 9.4, 14.7, 14.8_

  - [x] 3.3 Write property tests for terrain, upgrade cost, and building bonuses
    - **Property 4: Terrain Generation Within Weight Map** — terrain type is in planet's weight map, resource included when non-null
    - **Validates: Requirements 2.3, 2.5, 2.7**
    - **Property 5: Terrain Generation Determinism** — same (x, y) and seed always returns same terrain
    - **Validates: Requirements 2.4**
    - **Property 16: Building Upgrade Cost Scaling** — upgrade cost = base_cost × target_level
    - **Validates: Requirements 6.8**
    - **Property 17: Per-Level Building Bonus Computation** — Extractor storage, Vault storage, Turret damage match formulas
    - **Validates: Requirements 6.21, 6.22, 6.23**
    - **Property 18: Harvester Production Scaling** — production = base_rate × (1 + 0.25 × (L-1))
    - **Validates: Requirements 9.2**

  - [x] 3.4 Extend RankSystem with sub-levels and agent cap integration
    - Add `get_sub_level(player)` method computing 5 sub-levels per rank from evenly spaced XP intervals
    - Add sub-level notification on XP change: "You are now {Rank} Level {N}"
    - Wire `rank_demoted` event to trigger agent reserve (prepare for AgentSystem integration)
    - Wire `rank_promoted` event to trigger agent restore
    - Add planet access gating: `can_access_planet(player, planet_key)` checking rank vs planet's `rank_requirement`
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4b.1, 4b.2, 4b.3, 4b.4, 4b.5, 4b.6, 4b.7_

  - [x] 3.5 Write property tests for sub-level XP distribution and rank gating
    - **Property 9: Sub-Level XP Distribution** — 5 sub-levels evenly spaced between consecutive rank thresholds
    - **Validates: Requirements 4b.2**
    - **Property 3: Planet Rank Gating** — travel allowed iff player rank >= planet rank_requirement
    - **Validates: Requirements 1.4, 6.5**

  - [x] 3.6 Create AgentSystem (`world/systems/agent_system.py`)
    - New file registered in `game_systems` dict
    - Implement `train_agent(player, academy_building)` — verify cap, charge scaled cost (base × N), set training timer
    - Implement `assign_agent(player, agent_id, role, target_building)` — validate role/building match, move NPC, attach behavior script
    - Implement `unassign_agent(player, agent_id)` — clear role, detach script, move to HQ
    - Implement `get_agents(player)`, `get_agent_by_id(player, agent_id)`, `get_agent_count(player)` — query by tag
    - Implement `handle_demotion(player, new_agent_cap)` — reserve highest-ID agents
    - Implement `handle_promotion(player, new_agent_cap)` — restore reserved agents
    - Implement `process_tick(tick_number)` — iterate all agent scripts
    - _Requirements: 7b.1, 7b.2, 7b.3, 7b.4, 7b.5, 7b.6, 7b.7, 7b.8, 7b.9, 7b.10, 7b.11, 7b.12, 7b.13, 7b.14, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [x] 3.7 Write property tests for agent system invariants
    - **Property 11: Agent Roster Invariant** — active + incapacitated + reserved = total roster size
    - **Validates: Requirements 7b.12**
    - **Property 12: Agent ID Sequentiality** — IDs strictly increasing, unique, never reused
    - **Validates: Requirements 7b.5**
    - **Property 13: Incapacitated/Reserved Agents Cannot Be Assigned** — assignment fails, state unchanged
    - **Validates: Requirements 7b.11**
    - **Property 14: Demotion Reserves Highest-ID Agents** — (N-M) highest-ID agents enter reserve
    - **Validates: Requirements 4.6, 7b.13**
    - **Property 15: Agent Training Cost Scaling** — agent N costs base_cost × N
    - **Validates: Requirements 8.3**

- [x] 4. Checkpoint — Systems verification
  - Ensure all tests pass, ask the user if questions arise.
  - Verify BuildingSystem construction timer works with player presence and Engineer agents
  - Verify ResourceSystem active-presence harvesting and Extractor production
  - Verify RankSystem sub-levels and planet access gating
  - Verify AgentSystem train/assign/unassign/demotion/promotion flows


- [x] 5. Phase 1C — Integration: Agent Scripts, Commands, Tick Script, Map Rendering
  - [x] 5.1 Create agent behavior scripts (`typeclasses/agent_scripts.py`)
    - New file with Evennia Script classes: `HarvesterScript`, `GuardScript`, `ScoutScript`, `EngineerScript`, `SoldierScript`, `MedicScript`
    - `HarvesterScript.at_repeat()`: read Extractor resource type, produce amount based on level, add to Extractor inventory
    - `GuardScript.at_repeat()`: activate Turret auto-attack on enemies in range
    - `ScoutScript.at_repeat()`: extend Radar vision radius
    - `EngineerScript.at_repeat()`: progress construction/research timers
    - `SoldierScript.at_repeat()`: participate in army combat calculations
    - `MedicScript.at_repeat()`: heal soldiers after combat, reduce respawn time at Medbay
    - _Requirements: 9.1, 10.1, 10.5, 10.6, 11.1, 11.3, 12.1, 12.3_

  - [x] 5.2 Create agent commands (`commands/agent_commands.py`)
    - New file with commands: `CmdAgents` (list all agents), `CmdAssign` (context-aware assignment), `CmdUnassign`, `CmdTrain`
    - `CmdAssign` infers role from building type when player is inside a building (Extractor→Harvester, Turret→Guard, Radar→Scout, Armory→Engineer, Lab→Engineer, Medbay→Medic)
    - `CmdAssign <id> <role>` for army roles (Soldier, Medic)
    - Register all commands in `commands/default_cmdsets.py` CharacterCmdSet
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [x] 5.3 Extend GameTickScript with agent and active-presence processing
    - Add agent processing step in `_build_tick_steps`: call `agent_system.process_tick(tick_number)`
    - Add active-presence step: for each online player in "building" or "harvesting" state, call the appropriate system's tick method
    - Add combat timer decrement step
    - Add Extractor production step (Harvester agents)
    - Wire AgentSystem into `game_systems` dict in `game_init.py`
    - _Requirements: 6.6, 6.7, 14.7, 14.8, 18.1, 18.3_

  - [x] 5.4 Extend game commands for active-presence and combat timer
    - Modify `CmdHarvest` to set `activity_state = "harvesting"` instead of instant harvest
    - Modify `CmdBuild` to set `activity_state = "building"` and start construction timer
    - Modify `CmdMove` to reset `activity_state` to "idle" on movement, and check `combat_timer_expires` before allowing Wall passage
    - Extend `CmdScore` to display sub-level, agent count, and combat timer
    - Add resource storage commands: extend `get`/`drop` for building inventory (Extractor, Vault)
    - _Requirements: 3.4, 6.6, 6.7, 6b.1, 6b.2, 6b.3, 6b.4, 6b.5, 6b.6, 6.24, 17.1, 17.2, 17.3, 17.4, 17.5, 4b.5_

  - [x] 5.5 Implement combat timer integration
    - Add `COMBAT_TIMER_STARTED` event constant to `world/event_bus.py`
    - Subscribe to vision events and damage events to start/reset combat timer on player
    - `CmdMove` checks `db.combat_timer_expires` vs current tick before allowing Wall passage
    - GameTickScript decrements combat timer naturally (tick-based expiry)
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_

  - [x] 5.6 Extend map rendering for NPC/agent visibility
    - Modify `ProceduralMapRenderer._colored_room()` to detect NPC objects in room contents after player check
    - Add display priority: own agent (`|g` green) > enemy agent (`|r` red) > neutral NPC (`|y` yellow) > occupied building (`|B` dark blue)
    - Agents inside buildings render as building abbreviation in dark blue, not separate symbols
    - Extend `MapDataProvider._visible_tile()` to include `"agents"` array and `"occupied"` flag in tile JSON
    - Update `map_renderer.js` to draw agent markers (green circles with role initial for own, red with `!` for enemy) and occupied buildings with dark blue background
    - Update `TERRAIN_COLORS` and `TERRAIN_SYMBOLS` in `map_renderer.js` for 48 new terrain types
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8_

  - [x] 5.7 Write property test for map display priority
    - **Property 19: Map Display Priority** — rendered symbol follows priority order for tiles with multiple entities
    - **Validates: Requirements 19.6, 19.5, 19.8**

  - [x] 5.8 Wire AgentSystem into game_init.py and event subscribers
    - Import and instantiate `AgentSystem` in `game_init.py`
    - Register as `"agent_system"` in `game_systems` dict
    - Subscribe `agent_system.handle_demotion` to `RANK_DEMOTED` event
    - Subscribe `agent_system.handle_promotion` to `RANK_PROMOTED` event
    - Add offline behavior: agent scripts continue running regardless of player connection status
    - _Requirements: 15.7, 18.1, 18.2, 18.3_

- [x] 6. Checkpoint — Integration verification
  - Ensure all tests pass, ask the user if questions arise.
  - Verify agent commands work end-to-end: train → assign → production → collection
  - Verify GameTickScript processes agents, active-presence, and combat timer
  - Verify map rendering shows agents with correct colors and priority
  - Verify combat timer blocks Wall passage and resets on combat events

- [x] 7. Phase 1D — Testing and Polish
  - [x] 7.1 Write unit tests for building system extensions
    - Test construction timer with player presence and Engineer agent
    - Test building offline on 0 HP (not destroyed)
    - Test repair cost = 50% of base
    - Test Extractor requires resource terrain
    - Test Vault rejects non-resource objects
    - Test HQ-first enforcement and one HQ per player per planet
    - Test rank requirement enforcement on construction
    - _Requirements: 6.3, 6.4, 6.5, 6.9, 6.10, 6.11, 6.14, 6.15_

  - [x] 7.2 Write unit tests for agent system
    - Test agent training flow: command → timer → completion
    - Test context-aware assignment (inside Extractor → Harvester, Turret → Guard, etc.)
    - Test agent cap enforcement
    - Test incapacitated agent cannot be assigned
    - Test reserved agent cannot be reassigned
    - Test offline agent behavior continues
    - _Requirements: 7b.3, 7b.6, 7b.8, 7b.11, 7b.14, 8.1, 8.6_

  - [x] 7.3 Write unit tests for combat timer and active-presence
    - Test combat timer starts on enemy detection and damage
    - Test Wall movement blocked during combat timer
    - Test combat timer resets on new combat events
    - Test combat timer expiry restores free movement
    - Test active-presence pauses on player movement
    - Test agent bypasses active-presence requirement
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 6.6, 6.7_

  - [x] 7.4 Write integration tests for full game loops
    - Test full game tick cycle with agents producing resources
    - Test construction flow: player presence → timer → completion
    - Test agent training → assignment → production → collection
    - Test rank up → new planet access → travel
    - Test demotion → agent reserve → re-rank → restore
    - Test YAML hot-reload preserves running game state
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.9_

  - [x] 7.5 Write backward compatibility tests
    - Test existing TerrainGenerator interface unchanged
    - Test existing PlanetRegistry interface unchanged
    - Test existing BuildingSystem construct/upgrade/destroy signatures unchanged
    - Test existing RankSystem award_xp/check_promotion/get_rank signatures unchanged
    - Test existing CombatCharacter resource methods unchanged
    - Test resource type migration (8 types → 6 types)
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6_

- [x] 8. Final checkpoint — Full verification
  - Ensure all tests pass, ask the user if questions arise.
  - Verify all 19 requirements are covered by implementation tasks
  - Verify all 19 correctness properties have corresponding property tests
  - Verify no orphaned or unwired code remains

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Hypothesis library, 100 examples minimum)
- Unit tests validate specific examples and edge cases
- ~85% of existing code is reused — most tasks extend existing files rather than creating new ones
- Only 5 genuinely new files: `combat_entity.py`, `npcs.py`, `agent_system.py`, `agent_scripts.py`, `agent_commands.py`
