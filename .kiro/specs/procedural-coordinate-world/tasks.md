# Implementation Plan: Procedural Coordinate World

## Overview

This plan replaces the Evennia XYZGrid dependency with a custom procedural coordinate-based world system. Implementation proceeds bottom-up: data layer and config first, then core components (terrain generator, room cache, tile resolver), then modified typeclasses (OverworldRoom, CombatCharacter), then game systems (fog of war, map renderer, garbage collector), then commands (CmdMove), then wiring (settings, cmdsets, game_init), then cleanup (remove XYZGrid deps). Each task builds on previous ones so there is no orphaned code.

## Tasks

- [x] 1. Data layer: definitions, configuration, and Planet Registry
  - [x] 1.1 Add CoordinateSpaceDef dataclass and BalanceConfig fields (`world/definitions.py`)
    - Add `CoordinateSpaceDef` dataclass with fields: planet_key, planet_type, width, height, terrain_seed, terrain_noise_cell_size, terrain_weights, persistence_type, spawn_x, spawn_y, default_planet
    - Add new fields to existing `BalanceConfig`: player_vision_radius, building_vision_radius, room_cache_max_size, gc_interval_ticks, gc_min_age_ticks
    - _Requirements: 6.2, 10.1, 4.2, 4.5_

  - [x] 1.2 Create planets.yaml configuration (`data/definitions/planets.yaml`)
    - Define earth_planet (100x100, static, seed 42, Earth terrain weights)
    - Define industrial_planet (50x50, static, seed 7, Industrial terrain weights)
    - Define space (200x200, dynamic, seed 99, empty terrain weights)
    - _Requirements: 6.1, 6.2, 6.5, 6.6, 10.1, 10.4_

  - [x] 1.3 Add new balance values to balance.yaml (`data/config/balance.yaml`)
    - Add player_vision_radius: 10, building_vision_radius: 7, room_cache_max_size: 1000, gc_interval_ticks: 100, gc_min_age_ticks: 50
    - _Requirements: 4.2, 4.5, 5.9, 11.8_

  - [x] 1.4 Implement Planet Registry (`world/planet_registry.py`)
    - Implement `PlanetRegistry` class with `load_from_yaml`, `get_space`, `list_planets`, `is_valid_coordinate`
    - Load planets.yaml via the existing Data_Registry YAML pattern
    - Validate planet keys are unique, dimensions are positive, terrain weights sum > 0
    - _Requirements: 6.1, 6.2, 6.3, 6.5_

  - [x] 1.5 Write unit tests for Planet Registry
    - Test loading from YAML, default persistence types, coordinate validation
    - Test invalid planet key raises KeyError
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 2. Core components: Terrain Generator and Room Cache
  - [x] 2.1 Implement Terrain Generator (`world/terrain_generator.py`)
    - Implement `TerrainGenerator` class with `get_terrain`, `get_terrain_and_resource`, `_noise`, `_terrain_from_noise`
    - Hash-based value noise with bilinear interpolation using only Python stdlib
    - Cumulative weight thresholds for terrain type selection
    - No scipy or numpy dependency
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 2.2 Write property test for terrain determinism
    - **Property 1: Terrain generation determinism**
    - **Validates: Requirements 3.1, 3.2**

  - [x] 2.3 Write property test for terrain output validity
    - **Property 2: Terrain output is always in the configured terrain set**
    - **Validates: Requirements 3.4**

  - [x] 2.4 Write property test for terrain-resource mapping
    - **Property 3: Terrain-to-resource mapping consistency**
    - **Validates: Requirements 3.6, 9.3**


  - [x] 2.5 Implement Room Cache (`world/room_cache.py`)
    - Implement `RoomCache` class with `get`, `put`, `remove`, `clear`, `size` property
    - Use `collections.OrderedDict` for O(1) LRU eviction
    - max_size read from balance.room_cache_max_size
    - _Requirements: 2.2, 4.1, 4.2_

  - [x] 2.6 Write property test for cache round-trip
    - **Property 5: Room cache round-trip**
    - **Validates: Requirements 2.2, 4.1**

  - [x] 2.7 Write property test for cache LRU eviction
    - **Property 6: Room cache LRU eviction respects max size**
    - **Validates: Requirements 4.2**

- [x] 3. Checkpoint - Validate data layer and core components
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Tile Resolver and modified OverworldRoom
  - [x] 4.1 Modify OverworldRoom to extend DefaultRoom (`typeclasses/rooms.py`)
    - Change base class from `XYZRoom` to `DefaultRoom`
    - Remove `from evennia.contrib.grid.xyzgrid.xyzroom import XYZRoom`
    - Add `x`, `y`, `planet_name` properties reading from Attributes instead of XYZGrid
    - Keep all existing public interface methods: terrain_type, resource_node, building, get_display_symbol, get_structured_state, at_object_receive
    - Update `planet_name` to read from Attribute instead of xyz[2]
    - _Requirements: 7.1, 7.2, 8.1_

  - [x] 4.2 Implement Tile Resolver (`world/tile_resolver.py`)
    - Implement `TileResolver` class with `resolve`, `get_if_exists`, `get_or_generate_terrain`
    - Lookup order: cache → database (tag-based query) → create new room
    - `_create_room`: set Attributes (x, y, planet, resource_node_data), Tags (terrain, overworld_tile, persistence_type, coord_x, coord_y, coord_planet), key format "{TerrainType} ({x},{y})"
    - `_db_lookup`: query by coord_x, coord_y, coord_planet tags
    - Validate coordinates via PlanetRegistry.is_valid_coordinate, raise ValueError for out-of-bounds
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 4.3 Write property test for room creation correctness
    - **Property 4: Room creation produces correct attributes and tags**
    - **Validates: Requirements 2.1, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 9.5, 9.6, 10.2, 10.3**

  - [x] 4.4 Write property test for planet isolation
    - **Property 13: Planet coordinate spaces are isolated**
    - **Validates: Requirements 6.3, 6.4**

- [x] 5. Checkpoint - Validate tile resolver and room typeclass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Modified CombatCharacter and Fog of War
  - [x] 6.1 Modify CombatCharacter (`typeclasses/characters.py`)
    - Add coordinate tracking Attributes: coord_x, coord_y, coord_planet in at_object_creation
    - Add discovery_memory Attribute (dict) for Fog of War
    - Rewrite `_maybe_move_to_overworld` to use TileResolver + PlanetRegistry (default_planet spawn) instead of XYZRoom.objects.get_xyz
    - Remove all XYZRoom imports
    - _Requirements: 1.4, 7.8, 8.2, 11.1, 11.7_

  - [x] 6.2 Implement Fog of War System (`world/fog_of_war.py`)
    - Implement `FogOfWarSystem` with `get_visible_tiles`, `get_tile_visibility`, `update_discovery`, `get_discovered_buildings`
    - Implement `DiscoveredBuildingState` dataclass
    - Vision computation: union of Chebyshev-distance circles (player radius from balance, building radius from balance)
    - Discovery memory stored as persistent Attribute on CombatCharacter
    - _Requirements: 5.4, 5.5, 5.6, 5.7, 5.9, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_

  - [x] 6.3 Write property test for vision computation
    - **Property 10: Vision computation is the union of all vision source circles**
    - **Validates: Requirements 5.9, 11.8**

  - [x] 6.4 Write property test for fog hiding enemies
    - **Property 11: Fog tiles hide enemy players but show discovered buildings**
    - **Validates: Requirements 5.6, 11.5, 11.6**

  - [x] 6.5 Write property test for discovery memory
    - **Property 12: Discovery memory records all visible tiles and enemy building snapshots**
    - **Validates: Requirements 11.2, 11.3, 11.4**

- [x] 7. Procedural Map Renderer and Garbage Collector
  - [x] 7.1 Implement Procedural Map Renderer (`world/procedural_map_renderer.py`)
    - Implement `ProceduralMapRenderer` with `render` and `_get_tile_symbol`
    - Render from terrain generator for tiles without rooms (no room creation for rendering)
    - Three visibility states: visible (full state), fog (terrain + discovered buildings), unexplored (terrain only)
    - 2-char-per-tile format, display priority: @@ > ** > building abbr > terrain
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [x] 7.2 Implement Garbage Collector (`world/garbage_collector.py`)
    - Implement `RoomGarbageCollector` with `run` method
    - Query Dynamic_Rooms (tagged persistence_type=dynamic) with no players and no buildings
    - Skip rooms with custom modifications (description differs from default)
    - Never touch Static_Rooms
    - Remove deleted rooms from cache
    - interval_ticks and min_age_ticks from balance.yaml
    - _Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 10.5_

  - [x] 7.3 Write property test for GC never deletes static rooms
    - **Property 7: Garbage collection never deletes static rooms**
    - **Validates: Requirements 4.3, 4.6, 4.7, 4.8, 10.5**

- [x] 8. Checkpoint - Validate game systems
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Modified CmdMove and command wiring
  - [x] 9.1 Modify CmdMove (`commands/game_commands.py`)
    - Rewrite func() to use TileResolver instead of tile_lookup dict/callable
    - Read current (x, y, planet) from caller's coord_x, coord_y, coord_planet Attributes
    - Calculate target via coordinate arithmetic (dx, dy from DIRECTION_MAP)
    - Validate bounds via PlanetRegistry.is_valid_coordinate
    - Resolve target room via TileResolver.resolve
    - Check offline building blocking
    - Move player, update coord_x, coord_y Attributes on caller
    - Trigger FogOfWar.update_discovery after move
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 8.6_

  - [x] 9.2 Write property test for movement bounds
    - **Property 8: Movement respects coordinate space bounds**
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [x] 9.3 Write property test for coordinate attributes after move
    - **Property 9: Player coordinate attributes match location after movement**
    - **Validates: Requirements 1.4**

  - [x] 9.4 Write unit tests for CmdMove
    - Test movement in all four directions
    - Test edge-of-map rejection
    - Test offline building blocking
    - _Requirements: 1.1, 1.3, 1.6_

- [x] 10. Wiring: settings, cmdsets, and game_init
  - [x] 10.1 Update settings.py (`server/conf/settings.py`)
    - Remove EXTRA_LAUNCHER_COMMANDS xyzgrid entry
    - Remove PROTOTYPE_MODULES xyzgrid entry
    - Remove OVERWORLD_SPAWN_COORDS (now in planets.yaml)
    - Keep BASE_CHARACTER_TYPECLASS
    - _Requirements: 8.3, 8.7_

  - [x] 10.2 Update default_cmdsets.py (`commands/default_cmdsets.py`)
    - Remove `from evennia.contrib.grid.xyzgrid.commands import XYZGridCmdSet`
    - Remove `self.add(XYZGridCmdSet)` from CharacterCmdSet.at_cmdset_creation
    - _Requirements: 8.1, 8.6_

  - [x] 10.3 Wire systems in game_init or startup
    - Initialize PlanetRegistry, load planets.yaml
    - Initialize TerrainGenerator per planet
    - Initialize RoomCache with balance.room_cache_max_size
    - Initialize TileResolver with registry, generators, cache
    - Initialize FogOfWarSystem with balance config
    - Initialize ProceduralMapRenderer with tile_resolver, fog_system, terrain_generators
    - Initialize RoomGarbageCollector with cache and balance config
    - Wire GC into GameTickScript at gc_interval_ticks
    - Make tile_resolver, fog_system, map_renderer available to commands via game_systems dict
    - Update CmdMove look command to use ProceduralMapRenderer
    - _Requirements: 7.3, 7.4, 7.5, 7.6, 7.7_

- [x] 11. Cleanup: remove XYZGrid dependencies
  - [x] 11.1 Remove overworld_map.py (`world/overworld_map.py`)
    - Delete the file containing EARTH_MAP, EARTH_PROTOTYPES, XYMAP_DATA_LIST
    - _Requirements: 8.4_

  - [x] 11.2 Verify no XYZGrid imports remain
    - Grep all project files for `xyzgrid`, `XYZRoom`, `XYZGrid`
    - Ensure no remaining imports from evennia.contrib.grid.xyzgrid
    - Ensure scipy is not required
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 11.3 Write integration tests
    - Test BuildingSystem + TileResolver: construct building on new tile, verify room created
    - Test CombatEngine + new OverworldRoom: attack resolution with coordinate-based range
    - Test ResourceSystem + new OverworldRoom: harvest from procedurally generated resource node
    - Test WorldChunkManager + new rooms: chunk computation with Attribute-based coordinates
    - Test full movement → fog of war → map render cycle
    - _Requirements: 7.3, 7.4, 7.5, 7.6, 7.7_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 13 universal correctness properties from the design using Hypothesis
- Unit tests validate specific examples and edge cases
- The design uses Python throughout — all code targets the Evennia framework (Python/Django)
- Tests use the same Evennia stub approach as existing tests in `mygame/typeclasses/tests/`
- The existing overworld_map.py (XYZGrid ASCII map) is removed, not modified
