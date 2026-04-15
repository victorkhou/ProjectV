# Implementation Plan: Coordinate Room Refactor

## Overview

Eliminate the per-tile `OverworldRoom` model. All game entities live in a single `PlanetRoom` per planet, identified by `coord_x`/`coord_y` attributes. A `CoordinateIndex` on `PlanetRoom.ndb` provides O(1) spatial lookups. This removes `TileResolver`, `RoomCache`, and `RoomGarbageCollector`, simplifies movement (no room transitions), and unifies object placement under a coordinate-based model.

Implementation proceeds in seven phases: core infrastructure, system updates, command updates, rendering updates, tick processing, removal & migration, and testing.

## Tasks

- [x] 1. Core Infrastructure — CoordinateIndex and entity foundations
  - [x] 1.1 Create `CoordinateIndex` class in `mygame/world/coordinate/coordinate_index.py`
    - Implement `__init__`, `add`, `remove`, `move`, `get_at`, `get_in_area`, `clear`, `__len__`, `build_from_contents`
    - This is a standalone pure-data-structure class with no Evennia dependencies
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 2.7, 15.1, 15.2_

  - [x] 1.2 Write property test for CoordinateIndex add/remove/move invariant
    - **Property 1: Coordinate Index Invariant**
    - Use Hypothesis stateful testing with sequences of add/remove/move operations
    - Verify `get_at(x, y)` always returns exactly the objects at those coordinates
    - Verify rebuild from contents produces identical results
    - **Validates: Requirements 2.1, 2.5, 2.7, 2.8, 6.5, 6.6, 15.3, 15.5**

  - [x] 1.3 Write property test for type-filtered queries
    - **Property 2: Type-Filtered Query Correctness**
    - Generate mixed object types (building, player, agent, resource_drop, item) at various coordinates
    - Verify `get_buildings_at` returns exactly the building-tagged subset of `get_objects_at`
    - Verify `get_players_at` returns exactly the player-character subset
    - **Validates: Requirements 2.2, 2.3**

  - [x] 1.4 Write property test for area queries
    - **Property 3: Area Query Correctness**
    - Generate objects at random coordinates and random bounding boxes
    - Verify `get_objects_in_area(x1, y1, x2, y2)` returns exactly objects within bounds
    - **Validates: Requirements 2.4**

  - [x] 1.5 Extend `GameEntity.at_object_creation` in `mygame/typeclasses/objects.py`
    - Add `self.db.coord_x = None` and `self.db.coord_y = None` initialization
    - Add `at_pre_get` hook for proximity filtering (block pickup if getter not at same coordinates)
    - _Requirements: 1.1, 3.3_

  - [x] 1.6 Add `at_get` and `at_drop` hooks to `GameItem` in `mygame/typeclasses/objects.py`
    - `at_get`: set `coord_x` and `coord_y` to `None`
    - `at_drop`: set `coord_x` and `coord_y` to dropper's current coordinates
    - _Requirements: 3.4, 6.3, 6.4_

  - [x] 1.7 Update `NPC` to extend `GameEntity` in `mygame/typeclasses/npcs.py`
    - Change `NPC(CombatEntity, DefaultObject)` to `NPC(CombatEntity, GameEntity)`
    - Set `_object_type_tag = "npc"`
    - Call `super().at_object_creation()` before `at_combat_entity_init()`
    - _Requirements: 1.4, 1.5_

- [x] 2. Checkpoint — Verify core infrastructure
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Enhance PlanetRoom with coordinate index and query methods
  - [x] 3.1 Add `at_init`, `coord_index` property, `_rebuild_index`, and query methods to `PlanetRoom` in `mygame/typeclasses/rooms.py`
    - `at_init()`: clear `ndb._coord_index` to `None`
    - `coord_index` property: lazy-init, rebuild from contents on first access, log count
    - `get_objects_at(x, y, type_tag=None)`: query index, optionally filter by `object_type` tag
    - `get_buildings_at(x, y)`: convenience wrapper
    - `get_players_at(x, y)`: convenience wrapper
    - `get_objects_in_area(x1, y1, x2, y2)`: delegate to index
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 15.1, 15.2, 15.3_

  - [x] 3.2 Add `move_entity` method to `PlanetRoom`
    - Atomically update `coord_x`/`coord_y` and the index
    - Fire `at_coord_change(old_x, old_y, new_x, new_y)` hook on the object if it exists
    - Handle `None` old coordinates (first placement) by skipping removal
    - _Requirements: 2.5, 7.1_

  - [x] 3.3 Add `at_object_receive` and `at_object_leave` hooks to `PlanetRoom`
    - `at_object_receive`: add arriving object to coordinate index if it has coordinates
    - `at_object_leave`: remove departing object from coordinate index
    - Preserve existing tile-info display behavior for arriving players
    - _Requirements: 2.8, 6.5, 6.6_

  - [x] 3.4 Add resource node depletion dictionary to `PlanetRoom`
    - `db.depleted_nodes`: persistent dict keyed by `"x,y"` strings
    - `get_depleted_nodes()`, `set_node_depleted(x, y, resource_type, respawn_counter)`, `clear_node_depletion(x, y)`, `is_node_depleted(x, y)`
    - _Requirements: 9.1, 9.2, 9.5_


  - [x] 3.5 Write property test for proximity message delivery
    - **Property 4: Proximity Message Delivery**
    - Generate players at various coordinates in a PlanetRoom
    - Verify `msg_contents` with `from_obj` at `(sx, sy)` delivers only to players at same coordinates
    - **Validates: Requirements 3.2, 3.6**

  - [x] 3.6 Write property test for coordinate-scoped pickup
    - **Property 5: Coordinate-Scoped Pickup**
    - Generate objects and a player at various coordinates
    - Verify `at_pre_get` blocks pickup when coordinates differ, allows when same
    - **Validates: Requirements 3.3**

- [x] 4. Checkpoint — Verify PlanetRoom enhancements
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Update BuildingSystem for PlanetRoom placement
  - [x] 5.1 Update `BuildingSystem._validate_tile_empty` in `mygame/world/systems/building_system.py`
    - Accept PlanetRoom + coordinates; check `get_buildings_at(x, y)` instead of `tile.building`
    - _Requirements: 4.2, 14.4_

  - [x] 5.2 Update `BuildingSystem._default_create_building` to place buildings in PlanetRoom
    - Set `location=planet_room`, `coord_x=x`, `coord_y=y` on the building
    - Remove TileResolver dependency from construction flow
    - _Requirements: 4.1, 4.3_

  - [x] 5.3 Update `BuildingSystem._validate_build_range` to compare player coordinates to target coordinates directly
    - Read `coord_x`/`coord_y` from player and target, no OverworldRoom coordinate reads
    - _Requirements: 14.5_

  - [x] 5.4 Update `BuildingSystem._validate_extractor_terrain` to work without OverworldRoom
    - Query TerrainGenerator directly using coordinates, remove room-based fallbacks
    - _Requirements: 4.1_

- [x] 6. Update ResourceSystem for PlanetRoom
  - [x] 6.1 Update `spawn_resource_drop` in `mygame/typeclasses/objects.py`
    - Accept `planet_room, x, y, resource_type, amount` signature
    - Set `coord_x`/`coord_y` on the drop, merge only with drops at same `(x, y)` and type
    - Use `planet_room.get_objects_at(x, y, type_tag="resource_drop")` for merge check
    - _Requirements: 5.1, 5.2_

  - [x] 6.2 Update `ResourceSystem._spawn_resource_drop` in `mygame/world/systems/resource_system.py`
    - Call the updated `spawn_resource_drop` with explicit coordinates
    - _Requirements: 5.1, 5.4_

  - [x] 6.3 Update `ResourceSystem.start_harvest` and `process_harvest_tick`
    - Check depletion via `PlanetRoom.is_node_depleted(x, y)` and TerrainGenerator
    - Mark depletion via `PlanetRoom.set_node_depleted(x, y, ...)`
    - Spawn drops at player coordinates in PlanetRoom
    - _Requirements: 5.4, 9.3_

  - [x] 6.4 Update `ResourceSystem.process_respawns` to iterate PlanetRoom depletion dicts
    - Accept list of PlanetRoom objects instead of OverworldRoom tiles
    - Iterate `get_depleted_nodes()`, decrement counters, call `clear_node_depletion` when zero
    - _Requirements: 9.4, 14.3_

  - [x] 6.5 Write property test for resource drop merge correctness
    - **Property 8: Resource Drop Merge Correctness**
    - Generate drops at various coordinates with various types
    - Verify merge only occurs at same `(x, y)` and same `resource_type`
    - **Validates: Requirements 5.1, 5.2**

  - [x] 6.6 Write property test for resource pickup accounting
    - **Property 9: Resource Pickup Accounting**
    - Generate ResourceDrops with random amounts and types
    - Verify player balance increases by exactly the drop amount on pickup
    - **Validates: Requirements 6.1**

  - [x] 6.7 Write property test for depletion dictionary sparse invariant
    - **Property 11: Depletion Dictionary Sparse Invariant**
    - Generate sequences of deplete and respawn-tick operations
    - Verify dict only contains depleted entries, counters decrement, entries removed at zero
    - **Validates: Requirements 9.2, 9.4, 14.3**

- [x] 7. Update AgentSystem and HarvesterScript
  - [x] 7.1 Update `AgentSystem.assign_agent` in `mygame/world/systems/agent_system.py`
    - Use `PlanetRoom.move_entity(agent, building.db.coord_x, building.db.coord_y)` instead of `agent.move_to(building.location)`
    - _Requirements: 1.4, 14.7_

  - [x] 7.2 Update `AgentSystem.unassign_agent` to move agent to HQ coordinates via `move_entity`
    - _Requirements: 1.5_

  - [x] 7.3 Update `HarvesterScript._resolve_resource_type` in `mygame/typeclasses/agent_scripts.py`
    - Query TerrainGenerator using building's `coord_x`/`coord_y` instead of reading OverworldRoom `resource_node_data`
    - _Requirements: 14.6_

  - [x] 7.4 Update `HarvesterScript.at_repeat` to spawn drops at building coordinates in PlanetRoom
    - Use updated `spawn_resource_drop(planet_room, x, y, resource_type, amount)`
    - _Requirements: 5.3_

- [x] 8. Checkpoint — Verify system updates
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Simplify CmdMove
  - [x] 9.1 Rewrite `CmdMove.func` in `mygame/commands/game_commands.py`
    - Call `planet_room.move_entity(caller, tx, ty)` instead of updating `coord_x`/`coord_y` directly
    - Remove all TileResolver calls (`get_if_exists`, `resolve`)
    - Remove `_ensure_planet_room` method (player always stays in PlanetRoom)
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 9.2 Update building detection in CmdMove
    - Use `planet_room.get_buildings_at(tx, ty)` to detect buildings at target tile
    - Set `inside_building = True` if buildings present, `False` otherwise
    - Check wall/offline blocking via coordinate query instead of room lookup
    - _Requirements: 7.4, 7.5_

  - [x] 9.3 Update `_try_leave_building` in CmdMove
    - Query building at current coordinates via `planet_room.get_buildings_at(cx, cy)` instead of TileResolver
    - _Requirements: 7.4_

  - [x] 9.4 Update fog and map rendering calls in CmdMove
    - Remove TileResolver from fog update calls
    - Pass PlanetRoom to renderer instead of tile_resolver
    - _Requirements: 7.1_

- [ ] 10. Update remaining game commands
  - [x] 10.1 Update `CmdBuild` in `mygame/commands/game_commands.py`
    - Remove `_resolve_player_tile` usage; pass PlanetRoom + coordinates to `BuildingSystem.start_construction`
    - Check for existing building via `planet_room.get_buildings_at(x, y)` for resume logic
    - _Requirements: 4.1, 4.3_

  - [x] 10.2 Update `CmdHarvest` to use PlanetRoom queries
    - Remove `_resolve_player_tile`; check terrain and depletion via TerrainGenerator and PlanetRoom
    - _Requirements: 5.4, 9.3_

  - [x] 10.3 Update `CmdUpgrade` and `CmdDemolish` to use PlanetRoom queries
    - Find building via `planet_room.get_buildings_at(x, y)` instead of `tile.building`
    - `CmdDemolish`: delete building from PlanetRoom, no room deletion needed
    - _Requirements: 4.5_

  - [x] 10.4 Update `CmdLook` to show only objects at player coordinates
    - Use `planet_room.get_objects_at(x, y)` to filter visible objects, buildings, players, drops
    - _Requirements: 3.1_

  - [x] 10.5 Update `CmdScan` to read from PlanetRoom queries
    - Use `get_objects_at` instead of OverworldRoom state
    - _Requirements: 3.5_

- [x] 11. Update admin commands
  - [x] 11.1 Update `CmdTeleport` in `mygame/commands/admin_commands.py`
    - Use `PlanetRoom.move_entity` for coordinate updates when staying on same planet
    - Only call `move_to(planet_room)` when changing planets
    - Remove TileResolver fallback
    - _Requirements: 11.1_

  - [x] 11.2 Update `CmdSpawnBuilding` to create building in PlanetRoom at caller's coordinates
    - Set `coord_x`/`coord_y` on the building, remove TileResolver usage
    - _Requirements: 11.2, 4.4_

  - [x] 11.3 Update `CmdPurgeRooms` to delete legacy OverworldRoom objects
    - Change to delete all remaining OverworldRoom objects as migration cleanup
    - _Requirements: 11.3_

- [x] 12. Checkpoint — Verify command updates
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Update rendering systems
  - [x] 13.1 Update `ProceduralMapRenderer.render` in `mygame/world/coordinate/procedural_map_renderer.py`
    - Call `planet_room.get_objects_in_area(min_x, min_y, max_x, max_y)` once per render
    - Group results by coordinate to build tile display map
    - Remove `tile_resolver.preload_area` and `get_cached` calls
    - Read terrain from TerrainGenerator (unchanged)
    - _Requirements: 8.1, 8.2, 8.4, 8.5_

  - [x] 13.2 Update `MapDataProvider.get_map_data` in `mygame/world/coordinate/map_data_provider.py`
    - Use `planet_room.get_objects_in_area` for bulk query
    - Remove `tile_resolver.preload_area` and `get_cached` calls
    - Build tile data from grouped coordinate results
    - _Requirements: 8.3_

  - [x] 13.3 Update `FogOfWarSystem` in `mygame/world/coordinate/fog_of_war.py`
    - Read building coordinates from `building.db.coord_x`/`coord_y` directly
    - Use `PlanetRoom.get_buildings_at` for discovery updates instead of TileResolver
    - _Requirements: 13.1, 13.2_

- [x] 14. Update tick processing
  - [x] 14.1 Update `GameTickScript` in `mygame/typeclasses/scripts.py`
    - Remove `_get_all_tiles` method entirely
    - Update `_compute_active_data` to not filter tiles
    - Update resource respawn step to pass PlanetRoom objects to `process_respawns`
    - Remove garbage collector step from `_build_tick_steps`
    - _Requirements: 14.1, 14.2, 10.4_

  - [x] 14.2 Update `CombatCharacter.at_post_login` in `mygame/typeclasses/characters.py`
    - Ensure player is in correct PlanetRoom based on `coord_planet`
    - Remove `_enter_tile_room_if_exists` method entirely
    - Remove TileResolver fallback from `_ensure_overworld_position`
    - _Requirements: 7.6_

  - [x] 14.3 Update `CombatCharacter.at_pre_disconnect`
    - Query `PlanetRoom.get_objects_at(bx, by, type_tag="resource_drop")` for cleanup
    - Delete ResourceDrops at each owned non-Vault building's coordinates
    - _Requirements: 14.8_

- [x] 15. Checkpoint — Verify tick and rendering updates
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Removal and migration
  - [x] 16.1 Remove `OverworldRoom` class from `mygame/typeclasses/rooms.py`
    - Remove the entire class and the `_format_building_interior` helper (move helper if still needed by PlanetRoom)
    - _Requirements: 10.1_

  - [x] 16.2 Remove `TileResolver` class — delete `mygame/world/coordinate/tile_resolver.py`
    - _Requirements: 10.2_

  - [x] 16.3 Remove `RoomCache` class — delete `mygame/world/coordinate/room_cache.py`
    - _Requirements: 10.3_

  - [x] 16.4 Remove garbage collector references from `game_init` and `GameTickScript`
    - Remove `garbage_collector` from `game_systems` dict
    - Remove `tile_resolver` from `game_systems` dict
    - _Requirements: 10.4, 10.5, 10.6_

  - [x] 16.5 Create `@migraterooms` admin command in `mygame/commands/admin_commands.py`
    - Find all Building and ResourceDrop objects in OverworldRooms
    - Move them to corresponding PlanetRoom, set `coord_x`/`coord_y` from source room
    - Transfer `resource_node_data` to PlanetRoom's `depleted_nodes` dict
    - Delete empty OverworldRooms after migration
    - Report counts of migrated objects
    - Register in `CharacterCmdSet`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 16.6 Clean up all remaining `tile_resolver`/`OverworldRoom`/`RoomCache` imports
    - Update `mygame/commands/game_commands.py`: remove `_resolve_player_tile`, `_find_player_tile`, `_get_player_tile` methods
    - Update `mygame/commands/default_cmdsets.py` if needed
    - Update `mygame/world/utils.py`: remove OverworldRoom coordinate fallbacks in `get_coords`/`ensure_coords`
    - Update `mygame/world/coordinate/procedural_map_renderer.py`: remove TileResolver constructor param
    - Update `mygame/world/coordinate/map_data_provider.py`: remove TileResolver constructor param
    - Update `mygame/world/coordinate/fog_of_war.py`: remove TileResolver dependency
    - Scan all files for remaining `tile_resolver`, `OverworldRoom`, `RoomCache`, `room_cache` references
    - _Requirements: 10.7_

- [x] 17. Checkpoint — Verify removal and migration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Final testing and validation
  - [x] 18.1 Write unit tests for movement, building, and pickup/drop flows
    - Test CmdMove updates coordinates via `move_entity`, sets `inside_building` flag
    - Test building construction places building in PlanetRoom with coordinates
    - Test GameItem `at_get`/`at_drop` coordinate handling
    - Test CmdTeleport coordinate and planet updates
    - _Requirements: 7.1, 7.4, 4.1, 6.3, 6.4, 11.1_

  - [x] 18.2 Write integration tests for tick cycle and map rendering
    - Test GameTickScript processes buildings and respawns with coordinate-based lookups
    - Test ProceduralMapRenderer uses `get_objects_in_area` and produces correct output
    - Test FogOfWarSystem reads building coordinates from `coord_x`/`coord_y`
    - _Requirements: 14.1, 8.1, 13.1_

  - [x] 18.3 Write smoke tests for code removal
    - Verify `OverworldRoom` class no longer exists in `typeclasses/rooms.py`
    - Verify `TileResolver` class no longer exists in `world/coordinate/tile_resolver.py`
    - Verify `RoomCache` class no longer exists in `world/coordinate/room_cache.py`
    - Verify `game_systems` dict does not contain `tile_resolver` or `garbage_collector`
    - Verify no remaining imports of `TileResolver`, `RoomCache`, or `OverworldRoom` in game commands or systems
    - _Requirements: 10.1, 10.2, 10.3, 10.5, 10.6_

- [x] 19. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each phase
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The migration command (`@migraterooms`) should be run once on the live server after deployment
