# Requirements Document

## Introduction

This document defines the requirements for replacing the Evennia XYZGrid contrib dependency with a custom procedural coordinate-based world system in the RTS Combat Overworld game. The current system uses XYZGrid to pre-generate every room and exit as database objects from an ASCII map string, which does not scale for large maps (100x100+), procedurally generated terrain, multiple planets with different sizes, or dynamic world expansion. The new system uses coordinate-based movement without exit objects, creates rooms on-demand when players visit tiles, determines terrain procedurally from a seed, supports two distinct room persistence models (Static and Dynamic), and maintains backward compatibility with all existing game systems (BuildingSystem, CombatEngine, ResourceSystem, RankSystem, PowerupSystem, TechLabSystem, EquipmentSystem, and all player commands). The system also introduces an RTS-style Fog of War with per-player discovery memory, vision radii for characters and buildings, and persistent enemy building tracking.

## Glossary

- **Coordinate_World**: The replacement world system that manages player positions and tile state using coordinate tuples instead of pre-generated XYZGrid rooms and exit objects.
- **Coordinate_Space**: A named two-dimensional grid associated with a single Planet, identified by a planet key string. Each Coordinate_Space has its own dimensions, terrain seed, generation parameters, and room persistence type (Static or Dynamic).
- **Static_Room**: An On_Demand_Room that persists on disk indefinitely once created. Static_Rooms are never garbage collected. Used for Coordinate_Spaces where players build permanent structures (e.g., Earth-like planets). Static_Rooms may be procedurally generated initially, but once created they persist until explicitly modified. (Future extension: terrain transformation events such as volcanoes, tectonic shifts, and man-made rivers may modify Static_Rooms — not specified in this document.)
- **Dynamic_Room**: An On_Demand_Room that is procedurally generated and eventually garbage collected when no longer relevant. Used for Coordinate_Spaces representing ephemeral environments (e.g., space, where planets, asteroids, and debris may eventually move). Dynamic_Rooms exist only while relevant.
- **Room_Persistence_Type**: A per-Coordinate_Space configuration value that determines whether rooms in that space are Static_Rooms or Dynamic_Rooms.
- **Terrain_Generator**: The component that deterministically computes the terrain type for any (x, y) coordinate within a Coordinate_Space using a seed value and noise-based algorithm, without requiring database storage.
- **Terrain_Seed**: An integer value associated with a Coordinate_Space that initializes the Terrain_Generator's noise function, ensuring identical terrain output for the same coordinate across all invocations.
- **On_Demand_Room**: An OverworldRoom database object created only when a Player_Character first visits a tile or when a Building is placed on a tile. On_Demand_Rooms are not pre-generated. Each On_Demand_Room is either a Static_Room or Dynamic_Room based on its Coordinate_Space's Room_Persistence_Type.
- **Room_Cache**: An in-memory lookup structure that holds references to recently accessed On_Demand_Room objects, enabling fast coordinate-to-room resolution without repeated database queries.
- **Garbage_Collection**: The process of removing Dynamic_Room database objects that have no Player_Characters present, no Buildings placed, and no other persistent state, reclaiming database resources. Garbage_Collection applies only to Dynamic_Rooms.
- **Tile_Resolver**: The component responsible for resolving a (x, y, planet) coordinate tuple to either an existing On_Demand_Room from the Room_Cache or database, or creating a new On_Demand_Room on demand.
- **Planet_Registry**: The configuration store holding all Coordinate_Space definitions, including planet key, dimensions, Terrain_Seed, terrain generation parameters, and Room_Persistence_Type.
- **Procedural_Map_Renderer**: The component that renders an ASCII map view from procedural terrain data without requiring On_Demand_Room objects to exist for every visible tile, applying RTS-style Fog_of_War rules.
- **OverworldRoom**: The room typeclass representing a single tile on the overworld. In the new system, OverworldRoom extends Evennia's DefaultRoom instead of XYZRoom, storing coordinates as Attributes.
- **Player_Character**: An Evennia Character typeclass (CombatCharacter) representing a player in the game world.
- **Building**: A persistent structure placed on an OverworldRoom tile by a Player_Character.
- **Game_Tick**: A recurring server-side interval during which game systems process updates.
- **Vision_Radius**: The tile distance within which a Player_Character or Building grants visibility. Player_Characters have a Vision_Radius of 10 tiles. Buildings owned by the Player_Character have a Vision_Radius of 7 tiles.
- **Fog_of_War**: An RTS-style visibility system where tiles outside all of a Player_Character's combined vision sources (character position and owned buildings) are obscured. Enemy Player_Characters are completely hidden in fog. Discovered enemy Buildings persist on the player's map but are not updated until vision is regained.
- **Discovery_Memory**: A per-player persistent data structure that tracks which tiles the Player_Character has previously had vision of, along with the last-seen state of enemy Buildings on those tiles. Discovery_Memory enables the RTS fog of war behavior where previously seen enemy Buildings remain visible on the map at their last-known state.
- **Discovered_Building_State**: A snapshot of an enemy Building's visible properties (building type abbreviation, owner, position) stored in a Player_Character's Discovery_Memory when the Building is first observed within vision. The snapshot is not updated until the Player_Character regains vision of that tile.

## Requirements

### Requirement 1: Coordinate-Based Movement Without Exit Objects

**User Story:** As a player, I want to move between tiles using directional commands without the system needing pre-generated exit objects, so that movement works seamlessly on maps of any size.

#### Acceptance Criteria

1. WHEN a Player_Character issues a movement command with a cardinal direction (north, south, east, west), THE Coordinate_World SHALL calculate the target coordinate by applying the directional offset to the Player_Character's current (x, y) position.
2. WHEN the target coordinate is within the bounds of the current Coordinate_Space, THE Coordinate_World SHALL resolve the target tile via the Tile_Resolver and move the Player_Character to the resolved On_Demand_Room.
3. IF a Player_Character issues a movement command and the target coordinate is outside the bounds of the current Coordinate_Space, THEN THE Coordinate_World SHALL reject the movement and notify the Player_Character that the edge of the map has been reached.
4. THE Coordinate_World SHALL store the Player_Character's current coordinates as (x, y, planet) Attributes on the Player_Character object, updated after each successful movement.
5. THE Coordinate_World SHALL not create or use Evennia Exit objects for overworld movement between tiles.
6. WHEN a Player_Character moves to a new tile, THE Coordinate_World SHALL display the destination tile's terrain type, resource node status, buildings present, and visible Player_Characters (subject to Fog_of_War rules).

### Requirement 2: On-Demand Room Creation with Static and Dynamic Types

**User Story:** As a system administrator, I want rooms to be created only when needed and to persist or be cleaned up based on the planet type, so that the database is not filled with unused room objects while permanent worlds retain their state.

#### Acceptance Criteria

1. WHEN the Tile_Resolver receives a coordinate lookup for a tile that has no existing On_Demand_Room in the database or Room_Cache, THE Tile_Resolver SHALL create a new On_Demand_Room with the correct coordinates, terrain type (from the Terrain_Generator), resource node data, and Room_Persistence_Type matching the Coordinate_Space configuration.
2. WHEN the Tile_Resolver receives a coordinate lookup for a tile that has an existing On_Demand_Room in the Room_Cache, THE Tile_Resolver SHALL return the cached room without a database query.
3. WHEN the Tile_Resolver receives a coordinate lookup for a tile that has an existing On_Demand_Room in the database but not in the Room_Cache, THE Tile_Resolver SHALL load the room from the database, add the room to the Room_Cache, and return the room.
4. WHEN a Building is placed on a tile that has no existing On_Demand_Room, THE Tile_Resolver SHALL create the On_Demand_Room before the Building is placed.
5. THE On_Demand_Room SHALL store its coordinates as Evennia Attributes: x (int), y (int), and planet (str).
6. THE On_Demand_Room SHALL store its terrain type as an Evennia Tag with category "terrain", consistent with the existing OverworldRoom interface.
7. THE On_Demand_Room SHALL initialize its resource node data Attribute based on the terrain-to-resource mapping for the Coordinate_Space's planet type.
8. THE On_Demand_Room SHALL store its Room_Persistence_Type (static or dynamic) as an Evennia Tag with category "persistence_type".
9. WHEN the Tile_Resolver creates a Static_Room, THE Tile_Resolver SHALL mark the room as persistent, ensuring the room is retained in the database indefinitely until explicitly modified or deleted by an administrative action.
10. WHEN the Tile_Resolver creates a Dynamic_Room, THE Tile_Resolver SHALL mark the room as eligible for Garbage_Collection when the room has no Player_Characters present and no Buildings placed.

### Requirement 3: Procedural Terrain Generation

**User Story:** As a game designer, I want terrain to be generated procedurally from a seed, so that large worlds have varied and consistent terrain without manual map authoring.

#### Acceptance Criteria

1. THE Terrain_Generator SHALL accept a coordinate (x, y) and a Terrain_Seed and return a deterministic terrain type string for that coordinate.
2. FOR ALL coordinates within a Coordinate_Space, THE Terrain_Generator SHALL produce the same terrain type for the same (x, y, Terrain_Seed) inputs across all invocations (determinism property).
3. THE Terrain_Generator SHALL use a noise-based algorithm (such as simplex noise or Perlin noise) to distribute terrain types across the coordinate grid with natural-looking clustering.
4. THE Terrain_Generator SHALL support configurable terrain type sets per planet type: Earth_Planet terrain types (Plains, Mud, Forest, Rock, Mountain) and Industrial_Planet terrain types (Power_Grid, Scrapyard, Circuit_Field, Ruins).
5. THE Terrain_Generator SHALL accept terrain distribution weights as configuration parameters, allowing game designers to control the relative frequency of each terrain type within a Coordinate_Space.
6. WHEN the Terrain_Generator produces a terrain type for a coordinate, THE Terrain_Generator SHALL also determine the associated resource type using the planet's terrain-to-resource mapping.

### Requirement 4: Room Caching and Dynamic Room Garbage Collection

**User Story:** As a system administrator, I want unused dynamic rooms to be cleaned up automatically while static rooms are preserved, so that the database stays lean for ephemeral spaces without losing permanent world state.

#### Acceptance Criteria

1. THE Room_Cache SHALL maintain an in-memory mapping of (x, y, planet) tuples to On_Demand_Room references for recently accessed tiles.
2. THE Room_Cache SHALL support a configurable maximum size, evicting least-recently-used entries when the limit is reached.
3. WHEN the Garbage_Collection process runs, THE Garbage_Collection process SHALL identify Dynamic_Rooms that have no Player_Characters present and no Buildings placed.
4. WHEN the Garbage_Collection process identifies an eligible Dynamic_Room, THE Garbage_Collection process SHALL delete the Dynamic_Room from the database and remove the room from the Room_Cache.
5. THE Garbage_Collection process SHALL run periodically on a configurable interval measured in Game_Ticks.
6. THE Garbage_Collection process SHALL not delete Dynamic_Rooms that contain Buildings, even when no Player_Characters are present.
7. THE Garbage_Collection process SHALL not delete Dynamic_Rooms that have been modified with custom descriptions or other persistent state beyond the default procedural values.
8. THE Garbage_Collection process SHALL not delete Static_Rooms under any circumstances. Static_Rooms are exempt from Garbage_Collection regardless of occupancy or building state.

### Requirement 5: Procedural Map Rendering with RTS Fog of War

**User Story:** As a player, I want to see an ASCII map of my surroundings with RTS-style fog of war that remembers discovered enemy buildings, so that the map displays correctly and I have strategic information about previously scouted areas.

#### Acceptance Criteria

1. WHEN a Player_Character requests a map view, THE Procedural_Map_Renderer SHALL render an ASCII grid centered on the Player_Character's current coordinates, showing tiles within the maximum extent of the Player_Character's combined vision sources.
2. THE Procedural_Map_Renderer SHALL render tiles that have no existing On_Demand_Room by querying the Terrain_Generator for the terrain type and displaying the terrain symbol.
3. THE Procedural_Map_Renderer SHALL render tiles that have an existing On_Demand_Room by reading the room's actual state (players, buildings, terrain) using the existing display priority: "@@" for self, "**" for other players, building abbreviation, then terrain symbol.
4. THE Procedural_Map_Renderer SHALL classify each tile into one of three visibility states: visible (within any vision source), fog (previously discovered but not currently visible), or unexplored (never seen).
5. WHILE a tile is in the visible state, THE Procedural_Map_Renderer SHALL display the tile's full current state including enemy Player_Characters, enemy Buildings, allied Buildings, resource nodes, and terrain.
6. WHILE a tile is in the fog state, THE Procedural_Map_Renderer SHALL display the terrain symbol and any Discovered_Building_State entries from the Player_Character's Discovery_Memory, but SHALL hide all enemy Player_Characters and NPCs on that tile.
7. WHILE a tile is in the unexplored state, THE Procedural_Map_Renderer SHALL display only the terrain symbol, hiding all entities.
8. THE Procedural_Map_Renderer SHALL render each tile as exactly two characters wide, consistent with the existing ASCII map format.
9. THE Procedural_Map_Renderer SHALL compute visibility by combining the Player_Character's Vision_Radius of 10 tiles around the character position with a Vision_Radius of 7 tiles around each Building owned by the Player_Character.

### Requirement 6: Multiple Planets as Separate Coordinate Spaces

**User Story:** As a game designer, I want to define multiple planets with different sizes, terrain, and room persistence types, so that the game world can span diverse environments with appropriate data lifecycle.

#### Acceptance Criteria

1. THE Planet_Registry SHALL store Coordinate_Space definitions, each identified by a unique planet key string (e.g., "earth_planet", "industrial_planet", "space").
2. EACH Coordinate_Space definition SHALL include: planet key, grid width, grid height, Terrain_Seed, planet type (determining terrain type set and resource mapping), terrain distribution weights, and Room_Persistence_Type (static or dynamic).
3. THE Coordinate_World SHALL isolate each Coordinate_Space so that coordinates in one planet do not conflict with coordinates in another planet.
4. WHEN a Player_Character is on a specific planet, THE Coordinate_World SHALL resolve all movement and tile lookups within that planet's Coordinate_Space.
5. THE Planet_Registry SHALL support adding new Coordinate_Space definitions without code changes, using YAML configuration files consistent with the existing Data_Registry pattern.
6. THE Coordinate_World SHALL support at least two concurrent Coordinate_Spaces with independent dimensions (e.g., a 100x100 Earth_Planet with static rooms and a 50x50 space region with dynamic rooms).

### Requirement 7: Backward Compatibility with Existing Game Systems

**User Story:** As a developer, I want all existing game systems to continue working with the new room system, so that the replacement does not break building, combat, resource, rank, or equipment functionality.

#### Acceptance Criteria

1. THE OverworldRoom typeclass SHALL expose the same public interface as the current XYZRoom-based OverworldRoom: terrain_type property, resource_node property, building property, planet_name property, get_display_symbol method, and get_structured_state method.
2. THE OverworldRoom typeclass SHALL extend Evennia's DefaultRoom instead of XYZRoom, storing x, y, and planet as Evennia Attributes instead of relying on XYZGrid's coordinate system.
3. THE BuildingSystem SHALL continue to validate terrain, tile occupancy, build range, and resources using the OverworldRoom interface without modification to the BuildingSystem's public API.
4. THE CombatEngine SHALL continue to resolve attacks using coordinate-based range calculations derived from room Attributes (x, y) without depending on XYZGrid's coordinate lookup.
5. THE ResourceSystem SHALL continue to harvest from resource nodes and process building production using the OverworldRoom's resource_node Attribute without modification to the ResourceSystem's public API.
6. THE WorldChunkManager SHALL continue to compute active chunks using (x, y) coordinates read from On_Demand_Room Attributes, maintaining the same chunk-based tick optimization.
7. THE GameTickScript SHALL continue to discover tiles and buildings using tag-based searches, with On_Demand_Rooms tagged appropriately for discovery.
8. WHEN a Player_Character logs in for the first time, THE Coordinate_World SHALL spawn the Player_Character at the configured spawn coordinates within the default Coordinate_Space, creating the On_Demand_Room at those coordinates if the room does not exist.

### Requirement 8: XYZGrid Dependency Removal

**User Story:** As a developer, I want to remove the XYZGrid dependency entirely, so that the project no longer requires scipy, ASCII map strings, or the evennia xyzgrid CLI commands.

#### Acceptance Criteria

1. THE OverworldRoom typeclass SHALL not import or extend any class from the evennia.contrib.grid.xyzgrid package.
2. THE CombatCharacter typeclass SHALL not import or reference any class from the evennia.contrib.grid.xyzgrid package, including the XYZRoom lookup used in the login hook.
3. THE settings.py configuration SHALL remove the EXTRA_LAUNCHER_COMMANDS entry for "xyzgrid" and the PROTOTYPE_MODULES entry for "evennia.contrib.grid.xyzgrid.prototypes".
4. THE overworld_map.py module SHALL be replaced or removed, eliminating the ASCII map string (EARTH_MAP), the EARTH_PROTOTYPES dictionary, and the XYMAP_DATA_LIST configuration.
5. THE project SHALL not require scipy as a dependency after the migration is complete.
6. THE CmdMove command SHALL resolve target tiles via the Tile_Resolver using coordinate arithmetic instead of relying on XYZGrid exit objects or XYZRoom.objects.get_xyz lookups.
7. THE OVERWORLD_SPAWN_COORDS setting SHALL use a (x, y, planet_key) tuple compatible with the Tile_Resolver instead of XYZGrid's (x, y, z_coord) format.

### Requirement 9: Tile Resolver Interface

**User Story:** As a developer, I want a single entry point for resolving coordinates to rooms, so that all game systems use a consistent mechanism for tile access.

#### Acceptance Criteria

1. THE Tile_Resolver SHALL expose a resolve(x, y, planet) method that returns an On_Demand_Room for the given coordinates, creating the room on demand if the room does not exist.
2. THE Tile_Resolver SHALL expose a get_if_exists(x, y, planet) method that returns an existing On_Demand_Room or None without creating a new room.
3. THE Tile_Resolver SHALL expose a get_or_generate_terrain(x, y, planet) method that returns the terrain type and resource type for a coordinate without creating a room, for use by the Procedural_Map_Renderer.
4. THE Tile_Resolver SHALL use the Room_Cache as the first lookup layer, the database as the second lookup layer, and the Terrain_Generator as the creation source.
5. WHEN the Tile_Resolver creates a new On_Demand_Room, THE Tile_Resolver SHALL tag the room with "overworld_tile" in category "room_type" for discovery by the GameTickScript.
6. WHEN the Tile_Resolver creates a new On_Demand_Room, THE Tile_Resolver SHALL assign the room a descriptive key in the format "{TerrainType} ({x},{y})" consistent with the existing naming convention.

### Requirement 10: Static and Dynamic Room Type Distinction

**User Story:** As a game designer, I want to configure whether a planet uses static or dynamic rooms, so that permanent worlds where players build are preserved while ephemeral spaces are cleaned up automatically.

#### Acceptance Criteria

1. THE Coordinate_Space configuration SHALL include a Room_Persistence_Type field with two valid values: "static" and "dynamic".
2. WHEN a Coordinate_Space is configured with Room_Persistence_Type "static", THE Tile_Resolver SHALL create all On_Demand_Rooms in that space as Static_Rooms that persist indefinitely in the database.
3. WHEN a Coordinate_Space is configured with Room_Persistence_Type "dynamic", THE Tile_Resolver SHALL create all On_Demand_Rooms in that space as Dynamic_Rooms eligible for Garbage_Collection.
4. THE Planet_Registry SHALL default to Room_Persistence_Type "static" for Earth_Planet type Coordinate_Spaces and "dynamic" for non-planetary Coordinate_Spaces (e.g., space regions).
5. THE Garbage_Collection process SHALL query the Room_Persistence_Type tag on each On_Demand_Room and skip all rooms tagged as "static".
6. THE Room_Persistence_Type of an existing On_Demand_Room SHALL not change after creation, even if the Coordinate_Space configuration is updated.

### Requirement 11: Fog of War Discovery Memory System

**User Story:** As a player, I want the game to remember which tiles I have explored and what enemy buildings I discovered, so that previously scouted enemy positions remain visible on my map even when I can no longer see them, like in classic RTS games.

#### Acceptance Criteria

1. THE Fog_of_War system SHALL maintain a Discovery_Memory for each Player_Character, stored as a persistent Attribute on the Player_Character object.
2. WHEN a tile enters a Player_Character's combined vision (within 10 tiles of the character or within 7 tiles of any owned Building), THE Fog_of_War system SHALL mark that tile as discovered in the Player_Character's Discovery_Memory.
3. WHEN a Player_Character gains vision of a tile containing an enemy Building, THE Fog_of_War system SHALL store a Discovered_Building_State snapshot in the Player_Character's Discovery_Memory, recording the Building's type abbreviation, owner, and position.
4. WHEN a Player_Character regains vision of a tile with a previously discovered enemy Building, THE Fog_of_War system SHALL update the Discovered_Building_State in the Discovery_Memory to reflect the Building's current state (including removal if the Building no longer exists).
5. WHILE a tile with a Discovered_Building_State is outside the Player_Character's current vision, THE Fog_of_War system SHALL continue to display the last-known Building state on the Player_Character's map without updating the Building's visual state.
6. WHILE a tile is outside the Player_Character's current vision, THE Fog_of_War system SHALL completely hide enemy Player_Characters and NPCs on that tile from the Player_Character's map view.
7. THE Discovery_Memory SHALL persist across Player_Character login and logout sessions.
8. THE Fog_of_War system SHALL compute a Player_Character's combined vision as the union of: a circle of radius 10 tiles centered on the Player_Character's current position, and a circle of radius 7 tiles centered on each Building owned by the Player_Character.
9. WHEN a Player_Character's owned Building is destroyed, THE Fog_of_War system SHALL remove that Building's vision contribution from the Player_Character's combined vision on the next map render.
