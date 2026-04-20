# Requirements Document

## Introduction

This feature adds grid-based pathfinding and autonomous movement to all NPCs in the game world, then layers agent-specific behaviors (patrol routes, resource delivery) on top. Currently, player-owned agents teleport to their assigned building and remain stationary, while enemy and vendor NPCs have no movement capability at all. This feature gives every NPC the ability to navigate the tile grid around obstacles one step per tick. For player-owned agents specifically, it adds patrol waypoint cycling for guards/scouts and autonomous Extractor-to-Vault resource delivery for harvesters. The movement system is designed as a general NPC capability so that the upcoming Combat & PvP feature can reuse it for enemy AI, soldier chase/retreat, and other combat movement behaviors.

## Glossary

- **NPC**: Any non-player character in the game world (extends CombatEntity and GameEntity). Includes player-owned agents, enemies, and vendors. All NPCs share the same movement infrastructure.
- **Pathfinder**: The module responsible for computing a sequence of tile coordinates from a start position to a goal position on the PlanetRoom grid, avoiding impassable terrain and obstacles. Usable by any NPC.
- **Agent**: A player-owned NPC with a role (harvester, engineer, soldier, guard, scout, medic) and optional role_target building. Agents are a subset of NPCs managed by the AgentSystem.
- **Movement_Queue**: An ordered list of (x, y) coordinate pairs stored on an NPC, representing the tiles the NPC will traverse one step per game tick.
- **Patrol_Route**: A cyclic ordered list of (x, y) waypoints that a guard or scout Agent traverses repeatedly.
- **Delivery_Task**: An autonomous behavior where a harvester Agent picks up ResourceDrop objects at its assigned Extractor and carries them to a designated storage building (Vault or HQ).
- **Storage_Building**: A Vault (VT) or HQ building that can receive delivered resources.
- **PlanetRoom**: The shared room for an entire planet where all entities reside, using coord_x/coord_y for position.
- **CoordinateIndex**: The O(1) spatial lookup structure on PlanetRoom for querying objects by coordinate.
- **TerrainGenerator**: The deterministic module that provides terrain type (including passability) for any (x, y) coordinate.
- **GameTickScript**: The persistent script that drives all game systems once per second.
- **Move_Entity**: The PlanetRoom method that atomically updates an object's coordinates and the CoordinateIndex.
- **Movement_Speed**: The number of game ticks between each movement step for an NPC. A speed of 1 means one tile per tick; a speed of 2 means one tile every two ticks.
- **Carry_Capacity**: The maximum total resource units a harvester Agent can transport in a single delivery trip.
- **Activity_Status**: A human-readable string on each Agent describing its current behavior, visible to the owning player.

## Requirements

### Requirement 1: Grid Pathfinding

**User Story:** As a game system, I want to compute walkable paths between two tile coordinates on a planet, so that any NPC can navigate around impassable terrain and obstacles.

#### Acceptance Criteria

1. WHEN a path is requested from (start_x, start_y) to (goal_x, goal_y), THE Pathfinder SHALL return an ordered list of (x, y) coordinates representing a valid path where each consecutive pair of coordinates differs by exactly 1 in either the x or y axis (4-directional adjacency).
2. THE Pathfinder SHALL treat tiles with impassable terrain (TerrainDef.passable is False) as unwalkable.
3. WHEN no valid path exists between start and goal, THE Pathfinder SHALL return an empty list.
4. THE Pathfinder SHALL accept a maximum search radius parameter and terminate the search when the explored area exceeds the radius, returning an empty list.
5. WHEN start and goal are the same coordinate, THE Pathfinder SHALL return an empty list.
6. THE Pathfinder SHALL only produce paths containing coordinates within the planet's valid bounds (0 <= x < width, 0 <= y < height).
7. FOR ALL valid paths returned by the Pathfinder, the path length SHALL be equal to the Manhattan distance between start and goal when no obstacles exist between them (shortest-path optimality on open terrain).
8. FOR ALL valid paths returned by the Pathfinder, parsing the path into coordinate pairs and re-running the Pathfinder on each consecutive pair SHALL produce a single-step sub-path (round-trip consistency).

### Requirement 2: NPC Movement Execution

**User Story:** As a game system, I want all NPCs to move along a computed path one tile per game tick, so that NPC movement is visible, consistent with the tick-based game loop, and reusable across agents, enemies, and vendors.

#### Acceptance Criteria

1. WHILE an NPC has a non-empty Movement_Queue, THE GameTickScript SHALL advance the NPC by one step per tick by calling PlanetRoom.move_entity with the next coordinate from the queue.
2. WHEN the NPC reaches the final coordinate in the Movement_Queue, THE NPC SHALL clear the Movement_Queue and invoke an on_arrival callback (if defined) so that role-specific behavior can resume.
3. IF an NPC's next step in the Movement_Queue targets an impassable tile (terrain changed or new obstacle placed), THEN THE NPC SHALL halt movement, clear the Movement_Queue, and remain at the current position.
4. WHILE an NPC is incapacitated, THE NPC SHALL not advance along the Movement_Queue.
5. THE NPC movement processing SHALL use PlanetRoom.move_entity to update coordinates, ensuring the CoordinateIndex remains consistent.
6. WHEN a player-owned Agent is assigned to a building via AgentSystem.assign_agent, THE AgentSystem SHALL compute a path from the Agent's current position to the building's coordinates and populate the Agent's Movement_Queue instead of teleporting.
7. THE Movement_Queue and movement processing SHALL be implemented on the NPC base typeclass so that enemy and vendor NPCs can use the same infrastructure in future features.
8. WHEN an NPC's next step enters a tile occupied by a building with a closed exit facing the NPC's approach direction, THE NPC SHALL treat that tile as blocked, clear the Movement_Queue, and recompute a path avoiding that tile.
9. THE NPC SHALL not pre-check building exit states during pathfinding; closed exits SHALL only be detected at movement time when the NPC attempts to step onto the tile.

### Requirement 3: Patrol Routes for Guards and Scouts

**User Story:** As a player, I want to define patrol routes for my guard and scout agents, so that they autonomously move between waypoints around my base.

#### Acceptance Criteria

1. WHEN a player assigns a patrol route to a guard or scout Agent, THE AgentSystem SHALL store the Patrol_Route as an ordered list of (x, y) waypoints on the Agent.
2. WHILE a guard or scout Agent has a Patrol_Route and is not incapacitated, THE Agent SHALL compute a path to the next waypoint and populate the Movement_Queue when the current Movement_Queue is empty.
3. WHEN a patrolling Agent reaches the final waypoint in the Patrol_Route, THE Agent SHALL cycle back to the first waypoint.
4. IF a waypoint in the Patrol_Route becomes unreachable (no valid path), THEN THE Agent SHALL skip the unreachable waypoint and proceed to the next reachable waypoint in the route.
5. IF all waypoints in the Patrol_Route are unreachable, THEN THE Agent SHALL remain at the current position and retry on the next tick.
6. WHEN a patrol route is cleared or the Agent is unassigned, THE Agent SHALL stop patrolling and clear the Movement_Queue.
7. THE Patrol_Route SHALL contain at least 2 waypoints and at most 10 waypoints.
8. WHEN a player specifies a waypoint with coordinates outside the planet bounds, THE AgentSystem SHALL reject the patrol route and return an error message.

### Requirement 4: Autonomous Resource Delivery

**User Story:** As a player, I want my harvester agents to automatically deliver resources from their Extractor to a Vault or HQ, so that resources do not pile up at the Extractor uncollected.

#### Acceptance Criteria

1. WHEN a harvester Agent is assigned to an Extractor and a Storage_Building (Vault or HQ) exists for the same owner, THE Agent SHALL autonomously cycle between the Extractor and the Storage_Building to deliver resources.
2. WHILE the harvester Agent is at the Extractor's coordinates and ResourceDrop objects exist at those coordinates, THE Agent SHALL pick up the ResourceDrop objects (adding resources to a carried inventory on the Agent).
3. WHEN the harvester Agent has picked up resources, THE Agent SHALL compute a path to the nearest Storage_Building owned by the same player and populate the Movement_Queue.
4. WHEN the harvester Agent arrives at the Storage_Building's coordinates, THE Agent SHALL deposit all carried resources into the owning player's resource pool.
5. WHEN the harvester Agent has deposited resources, THE Agent SHALL compute a path back to the assigned Extractor and populate the Movement_Queue.
6. IF no Storage_Building exists for the owning player, THEN THE Agent SHALL remain at the Extractor and continue producing resources without delivery.
7. IF the path between the Extractor and the Storage_Building becomes blocked, THEN THE Agent SHALL wait at the current position and retry pathfinding on the next tick.
8. THE harvester Agent SHALL continue producing resources via HarvesterScript while at the Extractor's coordinates, pausing production only while in transit.

### Requirement 5: NPC Movement State Management

**User Story:** As a game system, I want NPC movement state to be persisted and recoverable, so that all NPCs resume their movement behavior after a server restart.

#### Acceptance Criteria

1. THE NPC SHALL store the Movement_Queue as a persistent Evennia Attribute (db.movement_queue).
2. THE Agent SHALL store the Patrol_Route as a persistent Evennia Attribute (db.patrol_route).
3. THE Agent SHALL store the current delivery state (idle, picking_up, delivering, returning) as a persistent Evennia Attribute (db.delivery_state).
4. WHEN the server restarts, THE GameTickScript SHALL resume processing NPC movement from the persisted Movement_Queue without recomputing paths.
5. WHEN the server restarts, THE patrolling Agents SHALL resume their patrol cycle from the persisted Patrol_Route and current waypoint index.

### Requirement 6: Pathfinder Performance Constraints

**User Story:** As a game system, I want pathfinding to complete within bounded time, so that the game tick does not stall when many NPCs compute paths simultaneously.

#### Acceptance Criteria

1. THE Pathfinder SHALL use the A* algorithm with Manhattan distance as the heuristic.
2. THE Pathfinder SHALL accept a configurable maximum node expansion limit (default: 500 nodes) and return an empty path when the limit is exceeded.
3. WHEN multiple NPCs request paths in the same tick, THE GameTickScript SHALL process at most a configurable number of pathfinding requests per tick (default: 10) and defer remaining requests to subsequent ticks.
4. THE Pathfinder SHALL query terrain passability from TerrainGenerator using the planet's CoordinateSpaceDef, avoiding per-tile database lookups.
5. THE Pathfinder SHALL treat tiles occupied by offline buildings as impassable.

### Requirement 7: Delivery Target Selection

**User Story:** As a game system, I want harvester agents to select the best delivery target, so that resources are delivered efficiently.

#### Acceptance Criteria

1. WHEN selecting a delivery target, THE Agent SHALL prefer the nearest Storage_Building (by Manhattan distance) owned by the same player.
2. IF multiple Storage_Buildings are equidistant, THE Agent SHALL prefer a Vault (VT) over HQ.
3. WHEN the previously selected Storage_Building is destroyed or goes offline, THE Agent SHALL select a new Storage_Building on the next delivery cycle.
4. IF the owning player builds a new Storage_Building closer to the Extractor, THE Agent SHALL switch to the closer target on the next delivery cycle.

### Requirement 8: NPC Movement Speed

**User Story:** As a game system, I want different NPC roles to move at different speeds, so that scouts are fast, laden harvesters are slow, and the system supports varied movement rates for future combat behaviors.

#### Acceptance Criteria

1. THE NPC SHALL have a configurable movement_speed attribute representing the number of ticks between each movement step (1 = every tick, 2 = every other tick).
2. THE default movement_speed for all NPCs SHALL be 1 (one tile per tick).
3. WHILE a scout Agent is patrolling, THE scout Agent SHALL have a movement_speed of 1.
4. WHILE a harvester Agent is carrying resources (delivery_state is "delivering"), THE harvester Agent SHALL have a movement_speed of 2 (one tile every 2 ticks).
5. WHILE a harvester Agent is returning empty to the Extractor (delivery_state is "returning"), THE harvester Agent SHALL have a movement_speed of 1.
6. THE GameTickScript SHALL only advance an NPC's Movement_Queue when the current tick modulo the NPC's movement_speed equals zero.
7. THE movement_speed attribute SHALL be stored as a persistent Evennia Attribute (db.movement_speed) so it survives server restarts.
8. WHEN an NPC has a "speed" stat modifier from equipped items (via EquipmentHandler), THE NPC movement system SHALL add the modifier to the base movement_speed (a positive speed modifier reduces ticks between steps, minimum 1).

### Requirement 9: Agent Carry Capacity

**User Story:** As a game system, I want harvester agents to have a limited carry capacity, so that delivery trips are balanced and agents make multiple trips for large resource stockpiles.

#### Acceptance Criteria

1. THE harvester Agent SHALL have a carry_capacity attribute representing the maximum total resource units the Agent can carry at once (default: 50).
2. WHEN the harvester Agent picks up ResourceDrop objects at the Extractor, THE Agent SHALL pick up resources up to the carry_capacity limit and leave any excess on the ground.
3. THE carried resources SHALL be stored as a persistent Evennia Attribute (db.carried_resources) as a dict mapping resource type to amount.
4. WHEN the harvester Agent deposits resources at the Storage_Building, THE Agent SHALL transfer all carried resources to the owning player's resource pool and set carried_resources to an empty dict.
5. IF the harvester Agent is incapacitated while carrying resources, THEN THE Agent SHALL drop all carried resources as a ResourceDrop at the Agent's current coordinates.

### Requirement 10: Agent Status Visibility

**User Story:** As a player, I want to see what my agents are currently doing, so that I can monitor their behavior and make informed decisions about reassignment.

#### Acceptance Criteria

1. THE Agent SHALL maintain a human-readable activity_status string attribute (db.activity_status) describing the current behavior (e.g., "Moving to Vault (3 tiles)", "Harvesting at Extractor", "Delivering 15 Iron", "Patrolling waypoint 2/5").
2. WHEN the Agent's movement state or delivery state changes, THE Agent SHALL update the activity_status string.
3. WHEN a player uses the agents list command, THE AgentSystem SHALL include each Agent's activity_status in the output.
4. WHEN a player looks at a tile containing one of their Agents, THE PlanetRoom SHALL include the Agent's activity_status in the tile description.

### Requirement 11: Agent Movement Cancellation

**User Story:** As a player, I want to stop an agent mid-movement, so that I can redirect or reassign agents without waiting for them to reach their destination.

#### Acceptance Criteria

1. WHEN a player issues a stop command for an Agent, THE AgentSystem SHALL clear the Agent's Movement_Queue and set the Agent to idle at the current position.
2. WHEN a player reassigns an Agent to a new building while the Agent is in transit, THE AgentSystem SHALL clear the current Movement_Queue and compute a new path from the Agent's current position to the new building.
3. WHEN a player clears a patrol route while the Agent is in transit to a waypoint, THE Agent SHALL stop at the current position and clear the Movement_Queue.
4. WHEN a harvester Agent's movement is cancelled while carrying resources, THE Agent SHALL retain the carried resources and remain at the current position until given a new instruction.
