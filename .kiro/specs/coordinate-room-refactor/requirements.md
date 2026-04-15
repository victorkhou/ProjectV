# Requirements Document

## Introduction

This specification defines the refactoring of the room architecture to eliminate OverworldRoom entirely. All game objects (buildings, resource drops, agents, items) will coexist in a single PlanetRoom per planet, identified by coordinate attributes (coord_x, coord_y). This removes the TileResolver, RoomCache, and RoomGarbageCollector subsystems, simplifies player movement (no room transitions), and unifies object placement under a coordinate-based model. The inside_building flag is retained as a UI toggle for the building detail panel.

## Glossary

- **PlanetRoom**: A single Evennia Room instance shared by all entities on a given planet. One PlanetRoom exists per planet.
- **GameEntity**: Base typeclass for all game-world objects (buildings, items, drops). Extended with coord_x/coord_y attributes.
- **Building**: A GameEntity subclass representing a player-constructed structure, now placed directly in PlanetRoom with coordinate attributes.
- **ResourceDrop**: A GameEntity subclass representing harvestable resource stacks on the ground, placed in PlanetRoom with coordinate attributes.
- **Coordinate_Attributes**: The pair of Evennia Attributes (coord_x, coord_y) stored on any object to identify its tile position within a PlanetRoom.
- **Coordinate_Filter**: A method on PlanetRoom that returns all objects at a given (x, y) position by filtering contents on Coordinate_Attributes.
- **Proximity_Filter**: Logic applied to Evennia's look, get, say, and other commands so players only perceive objects sharing their coordinates.
- **TerrainGenerator**: Procedural system that computes terrain type and resource availability for any (x, y) coordinate. Remains unchanged.
- **Inside_Building_Flag**: A boolean attribute (inside_building) on the player character that toggles the building detail panel in the UI without changing the player's room.
- **Migration_Script**: A one-time admin command or startup routine that moves existing Building and ResourceDrop objects from OverworldRooms into PlanetRoom, setting their Coordinate_Attributes from the source room.

## Requirements

### Requirement 1: GameEntity Coordinate Attributes

**User Story:** As a developer, I want all game entities to carry coord_x and coord_y attributes, so that every object's tile position is tracked independently of its containing room.

#### Acceptance Criteria

1. THE GameEntity SHALL store coord_x and coord_y as persistent Evennia Attributes initialized to None at creation.
2. THE Building SHALL set coord_x and coord_y to the target tile coordinates when constructed.
3. THE ResourceDrop SHALL set coord_x and coord_y to the tile coordinates of the drop location when spawned.
4. WHEN an NPC agent is placed at a building, THE AgentSystem SHALL set the agent's coord_x and coord_y to match the building's coordinates.
5. WHEN an NPC agent is unassigned, THE AgentSystem SHALL retain the agent's coord_x and coord_y at the HQ building's coordinates.

### Requirement 2: PlanetRoom Coordinate Filter

**User Story:** As a developer, I want PlanetRoom to provide efficient coordinate-based filtering of its contents, so that game systems can query objects at a specific tile without scanning all room contents linearly every time.

#### Acceptance Criteria

1. THE PlanetRoom SHALL provide a get_objects_at(x, y) method that returns all objects in its contents whose coord_x equals x and coord_y equals y.
2. THE PlanetRoom SHALL provide a get_buildings_at(x, y) method that returns only Building objects at the given coordinates.
3. THE PlanetRoom SHALL provide a get_players_at(x, y) method that returns only player characters at the given coordinates.
4. THE PlanetRoom SHALL provide a get_objects_in_area(x1, y1, x2, y2) method that returns all objects whose coordinates fall within the bounding box, for efficient map viewport rendering.
5. THE PlanetRoom SHALL provide a move_entity(obj, new_x, new_y) method that atomically updates an object's coord_x/coord_y and the coordinate index, without triggering Evennia's move_to. This is the required way to update coordinates for objects already in the room (e.g., player movement).
6. WHEN PlanetRoom.contents contains more than 100 objects, THE Coordinate_Filter SHALL return results without requiring a full linear scan of all contents for each query (e.g., by maintaining a coordinate index).
7. THE coordinate index SHALL be stored on PlanetRoom.ndb (non-persistent). It SHALL be lazily rebuilt from PlanetRoom.contents on first access after a server restart or @reload.
8. WHEN an object is added to or removed from PlanetRoom, THE PlanetRoom SHALL update its coordinate index via at_object_receive and at_object_leave hooks.

### Requirement 3: Proximity-Filtered Commands

**User Story:** As a player, I want look, get, say, and other interaction commands to show only objects at my current tile, so that I do not see the entire planet's contents.

#### Acceptance Criteria

1. WHEN a player executes the look command without arguments, THE CmdLook SHALL display only objects, buildings, players, and resource drops at the player's (coord_x, coord_y) coordinates.
2. WHEN a player executes the say command, THE PlanetRoom SHALL deliver the message only to players at the same (coord_x, coord_y) as the speaker.
3. WHEN a player executes the get command, THE command SHALL only allow picking up objects at the player's (coord_x, coord_y) coordinates. This SHALL be enforced by overriding PlanetRoom's search method to filter candidates by the searcher's coordinates when the search is location-scoped.
4. WHEN a player executes the drop command, THE dropped object SHALL be placed in the PlanetRoom with coord_x and coord_y set to the player's current coordinates. This SHALL be enforced via a GameItem.at_drop hook or a CmdDrop override.
5. WHEN a player executes the scan command, THE CmdScan SHALL read building and resource data from PlanetRoom.get_objects_at rather than from an OverworldRoom.
6. THE PlanetRoom.msg_contents method SHALL continue to filter messages by sender coordinates, delivering only to players at the same tile.

### Requirement 4: Building Placement in PlanetRoom

**User Story:** As a player, I want to construct buildings that are placed directly in PlanetRoom with coordinate attributes, so that buildings persist without requiring per-tile rooms.

#### Acceptance Criteria

1. WHEN a player constructs a building, THE BuildingSystem SHALL create the Building object with its location set to the PlanetRoom and coord_x/coord_y set to the target tile coordinates.
2. WHEN a player constructs a building, THE BuildingSystem SHALL validate that no other Building exists at the same (coord_x, coord_y) in the PlanetRoom.
3. THE BuildingSystem SHALL no longer call TileResolver.resolve to create an OverworldRoom for building placement.
4. WHEN the admin command @spawnbuilding is executed, THE CmdSpawnBuilding SHALL place the building in the PlanetRoom at the caller's coordinates.
5. WHEN a building is demolished, THE CmdDemolish SHALL delete the Building object from PlanetRoom without deleting any room.

### Requirement 5: Resource Drop Placement in PlanetRoom

**User Story:** As a developer, I want resource drops to be spawned directly in PlanetRoom with coordinate attributes, so that harvesting and Extractor production work without per-tile rooms.

#### Acceptance Criteria

1. WHEN a resource drop is spawned, THE spawn_resource_drop function SHALL set the drop's location to PlanetRoom and set coord_x/coord_y to the tile coordinates.
2. WHEN merging resource drops, THE spawn_resource_drop function SHALL only merge with existing ResourceDrop objects at the same (coord_x, coord_y) and same resource_type.
3. WHEN a HarvesterScript produces resources, THE HarvesterScript SHALL spawn the ResourceDrop in the PlanetRoom at the Extractor building's coordinates.
4. WHEN a player harvests manually, THE ResourceSystem SHALL spawn the ResourceDrop in the PlanetRoom at the player's coordinates.

### Requirement 6: Object Pickup and Drop Coordinate Handling

**User Story:** As a player, I want picking up and dropping objects to correctly manage their coordinates, so that objects on the ground have tile positions and objects in my inventory do not.

#### Acceptance Criteria

1. WHEN a player picks up a ResourceDrop, THE ResourceDrop.at_get hook SHALL convert the resource amount into the player's inventory (via add_resource), zero the amount, and schedule the object for deletion. ResourceDrops are fungible quantities, not persistent inventory objects.
2. WHEN a player picks up a GameItem (equipment), THE GameItem SHALL move to the player's inventory (location = player) via Evennia's default move_to. The coordinate index SHALL remove the item automatically via PlanetRoom.at_object_leave.
3. WHEN a GameItem is in a player's inventory, THE GameItem's coord_x and coord_y SHALL be set to None by a GameItem.at_get hook, indicating it is not on the ground.
4. WHEN a player drops a GameItem, THE GameItem.at_drop hook SHALL set coord_x and coord_y to the dropping player's current coordinates before the object is placed in the PlanetRoom.
5. WHEN an object leaves a PlanetRoom (via move_to or deletion), THE PlanetRoom.at_object_leave hook SHALL remove the object from the coordinate index.
6. WHEN an object enters a PlanetRoom (via move_to), THE PlanetRoom.at_object_receive hook SHALL add the object to the coordinate index using its coord_x and coord_y attributes.

### Requirement 7: Player Movement Simplification

**User Story:** As a player, I want movement to update only my coordinate attributes without changing rooms, so that movement is fast and does not create database objects.

#### Acceptance Criteria

1. WHEN a player moves in a direction, THE CmdMove SHALL call PlanetRoom.move_entity(player, new_x, new_y) to atomically update coordinates and the coordinate index, without calling Evennia's move_to.
2. THE CmdMove SHALL obtain the PlanetRoom reference via the player's current location (player.location), which is always a PlanetRoom.
3. THE CmdMove SHALL no longer call TileResolver to resolve or create OverworldRooms.
4. WHEN a player moves to a tile with a building, THE CmdMove SHALL check PlanetRoom.get_buildings_at(new_x, new_y) and set inside_building to True if a building is present, displaying the building interior panel.
5. WHEN a player moves away from a building tile, THE CmdMove SHALL set inside_building to False.
6. WHEN a player logs in, THE CombatCharacter.at_post_login SHALL ensure the player is in the correct PlanetRoom (based on coord_planet) without resolving OverworldRooms. If the player has no PlanetRoom location, move them to the default planet's PlanetRoom at spawn coordinates.
7. THE CmdMove SHALL continue to validate movement bounds via PlanetRegistry.is_valid_coordinate.

### Requirement 8: Map Rendering from Coordinates

**User Story:** As a player, I want the map to render buildings and entities by reading their coordinate attributes from PlanetRoom, so that the map displays correctly without OverworldRooms.

#### Acceptance Criteria

1. THE ProceduralMapRenderer SHALL call PlanetRoom.get_objects_in_area(x1, y1, x2, y2) once per render to retrieve all buildings, players, and agents in the viewport, instead of calling get_buildings_at per tile.
2. THE ProceduralMapRenderer SHALL group the area query results by coordinate to build the tile display map.
3. THE MapDataProvider SHALL query PlanetRoom coordinate filter methods instead of TileResolver for building, player, and agent data.
4. THE ProceduralMapRenderer SHALL no longer call TileResolver.preload_area.
5. THE ProceduralMapRenderer SHALL continue to read terrain data from TerrainGenerator, not from room attributes.

### Requirement 9: Resource Node Data Storage

**User Story:** As a developer, I want resource node depletion and respawn state to be stored on PlanetRoom keyed by coordinates, so that resource node state persists without OverworldRooms.

#### Acceptance Criteria

1. THE PlanetRoom SHALL store resource node depletion state in a persistent dictionary attribute keyed by (x, y) coordinate tuples, with values containing resource_type, depleted status, and respawn_counter.
2. THE dictionary SHALL only contain entries for currently depleted nodes. WHEN a node's respawn completes (depleted becomes False), THE entry SHALL be removed from the dictionary. An absent entry means the node is available (TerrainGenerator is the source of truth for node existence).
3. WHEN a player harvests a resource node, THE ResourceSystem SHALL add or update the node's entry in PlanetRoom's depletion dictionary.
4. WHEN the GameTickScript processes resource respawns, THE ResourceSystem.process_respawns SHALL iterate over PlanetRoom's depletion dictionary entries, decrement counters, and remove entries that reach zero.
5. THE TerrainGenerator SHALL remain the authoritative source for which coordinates have resource nodes; the PlanetRoom dictionary SHALL only track runtime depletion state.

### Requirement 10: Removal of OverworldRoom, TileResolver, and RoomCache

**User Story:** As a developer, I want to remove OverworldRoom, TileResolver, RoomCache, and RoomGarbageCollector, so that the codebase has a single room model and no unused subsystems.

#### Acceptance Criteria

1. THE codebase SHALL remove the OverworldRoom class from typeclasses/rooms.py.
2. THE codebase SHALL remove the TileResolver class from world/coordinate/tile_resolver.py.
3. THE codebase SHALL remove the RoomCache class from world/coordinate/room_cache.py.
4. THE codebase SHALL remove the RoomGarbageCollector initialization and tick processing from game_init.py and GameTickScript.
5. THE game_init.py SHALL no longer instantiate TileResolver, RoomCache, or RoomGarbageCollector.
6. THE game_systems dictionary SHALL no longer contain tile_resolver or garbage_collector entries.
7. WHEN any game system or command previously referenced tile_resolver, THE code SHALL be updated to use PlanetRoom coordinate filter methods or TerrainGenerator directly.

### Requirement 11: Admin Command Updates

**User Story:** As an admin, I want admin commands to work with the coordinate-based architecture, so that teleportation, building spawning, and room management function correctly.

#### Acceptance Criteria

1. WHEN an admin executes @teleport, THE CmdTeleport SHALL update the caller's coord_x, coord_y, and coord_planet attributes and move the caller to the correct PlanetRoom without resolving OverworldRooms.
2. WHEN an admin executes @spawnbuilding, THE CmdSpawnBuilding SHALL create the building in the PlanetRoom at the caller's coordinates without resolving an OverworldRoom.
3. THE CmdPurgeRooms SHALL be updated to delete any remaining legacy OverworldRoom objects from the database as a migration cleanup tool.
4. WHEN an admin executes @clearfog, THE CmdClearFog SHALL continue to clear the player's discovery_memory attribute without changes.

### Requirement 12: Backward Compatibility and Migration

**User Story:** As a developer, I want a migration path for existing OverworldRoom data, so that buildings and resource state created before the refactor are preserved.

#### Acceptance Criteria

1. THE Migration_Script SHALL find all existing Building objects located in OverworldRooms and move them to the corresponding PlanetRoom, setting coord_x and coord_y from the source OverworldRoom's x and y attributes.
2. THE Migration_Script SHALL find all existing ResourceDrop objects in OverworldRooms and move them to the corresponding PlanetRoom with correct Coordinate_Attributes.
3. THE Migration_Script SHALL transfer resource_node_data from OverworldRooms to the PlanetRoom's coordinate-keyed dictionary.
4. WHEN the migration completes, THE Migration_Script SHALL report the count of migrated buildings, resource drops, and resource nodes.
5. IF an OverworldRoom contains no remaining objects after migration, THEN THE Migration_Script SHALL delete the empty OverworldRoom.

### Requirement 13: Fog of War Compatibility

**User Story:** As a player, I want fog of war discovery and visibility to work with the coordinate-based architecture, so that map exploration is preserved.

#### Acceptance Criteria

1. THE FogOfWarSystem.update_discovery SHALL record discovered buildings by reading Building objects from PlanetRoom at visible coordinates instead of from OverworldRooms.
2. THE FogOfWarSystem.get_visible_tiles SHALL continue to compute visibility from player coordinates and building coordinates without depending on OverworldRooms.
3. THE discovery_memory attribute on players SHALL continue to store discovered tile and building data in the same format.

### Requirement 14: GameTickScript and System Integration

**User Story:** As a developer, I want the GameTickScript and all game systems to operate on PlanetRoom contents filtered by coordinates, so that tick processing works without OverworldRooms.

#### Acceptance Criteria

1. THE GameTickScript._get_all_buildings SHALL query Building objects from PlanetRoom contents or via tag-based search, not from OverworldRoom contents.
2. THE GameTickScript SHALL no longer call _get_all_tiles to retrieve OverworldRoom objects.
3. WHEN the ResourceSystem.process_respawns is called, THE system SHALL iterate over PlanetRoom resource node depletion dictionaries instead of OverworldRoom tile lists.
4. WHEN the BuildingSystem validates tile emptiness, THE _validate_tile_empty method SHALL check PlanetRoom.get_buildings_at(x, y) instead of checking OverworldRoom.building property.
5. WHEN the BuildingSystem validates build range, THE _validate_build_range method SHALL compare player coordinates to target coordinates directly without reading coordinates from an OverworldRoom.
6. WHEN the HarvesterScript resolves the resource type for an Extractor, THE script SHALL query the TerrainGenerator or the building's resource_type attribute, not the OverworldRoom's resource_node_data.
7. WHEN the AgentSystem assigns an agent to a building, THE system SHALL call PlanetRoom.move_entity(agent, building.coord_x, building.coord_y) to update the agent's coordinates and index position. No Evennia move_to is needed since the agent is already in the PlanetRoom.
8. WHEN a player disconnects, THE CombatCharacter.at_pre_disconnect SHALL query PlanetRoom for ResourceDrop objects at each owned non-Vault building's coordinates and delete them, instead of iterating OverworldRoom contents.

### Requirement 15: Performance Safeguards

**User Story:** As a developer, I want coordinate filtering to perform efficiently even with thousands of objects in a PlanetRoom, so that tick processing and command response times remain acceptable.

#### Acceptance Criteria

1. THE PlanetRoom coordinate index SHALL be stored on ndb (non-persistent) and support O(1) average-case lookup by (x, y) coordinate pair using a dict keyed by (x, y) tuples.
2. WHEN a PlanetRoom contains 1000 or more objects, THE get_objects_at method SHALL return results without iterating over all contents.
3. THE coordinate index SHALL be lazily rebuilt from PlanetRoom.contents on first access after a server restart or @reload. The rebuild SHALL log the number of objects indexed.
4. IF the coordinate index becomes inconsistent with PlanetRoom.contents (e.g., an object is found in the index but not in contents), THEN THE PlanetRoom SHALL fall back to a linear scan and log a warning.
5. THE coordinate index SHALL be covered by property-based tests verifying the invariant: for any sequence of add/remove/move operations, get_objects_at(x, y) returns exactly the objects whose coord_x == x and coord_y == y and whose location is the PlanetRoom.
