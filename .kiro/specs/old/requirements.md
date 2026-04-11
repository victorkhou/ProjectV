# Requirements Document

## Introduction

A Python-based multiplayer text adventure game server that accepts client connections via telnet over TCP sockets. Players connect, authenticate with a login flow, explore a shared text-based world, interact with other players, and log out gracefully. The server runs a central game loop that processes player input, updates game state, and broadcasts relevant output to connected clients. The game incorporates real-time strategy elements set in a modern/futuristic war setting, where Players gather Resources to construct Buildings that unlock new features, technologies, and upgrades, and engage in combat using modern and futuristic Weapons against other Players and their Buildings. Players earn Experience_Points through combat achievements to progress through a Rank system that unlocks additional bonuses and technologies. When a Player logs out, the Player Buildings transition to an Offline_Building state that protects them from destruction.

## Glossary

- **Game_Server**: The Python-based TCP socket server that accepts and manages telnet client connections, runs the game loop, and maintains game state.
- **Player**: A connected and authenticated client participating in the text adventure.
- **Client**: A telnet connection to the Game_Server, which may or may not be authenticated as a Player.
- **Session**: The stateful connection between a Client and the Game_Server from connect to disconnect.
- **Game_Loop**: The central server loop that reads player input, processes commands, updates game state, and sends output to Players.
- **Room**: A discrete location within the game world that a Player can occupy and navigate between.
- **Command_Parser**: The component that interprets raw text input from a Player into executable game commands.
- **Terrain_Type**: A classification assigned to a Room describing its environment. Valid Terrain_Types are Plains, Mud, Forest, Rock, and Mountain.
- **Building**: A Player-constructed structure placed in a Room that provides features, technologies, upgrades, or defensive capabilities. Each Building has a material cost and a Health value.
- **Resource**: A gatherable material found in the game world that a Player collects and spends to construct Buildings or upgrade Resource_Buildings. Each Terrain_Type yields a specific Resource: Plains yield Straw, Mud yields Clay, Forest yields Wood, Rock yields Stone, and Mountain yields Iron.
- **Health**: A numeric value representing the structural integrity of a Building. WHEN Health reaches zero, the Building is destroyed.
- **Weapon**: An item a Player uses to deal damage to other Players or Buildings. Weapons range from modern firearms to futuristic energy-based armaments.
- **Damage**: A numeric value subtracted from a target's Health when an attack lands.
- **Technology_Tree**: The progression system that defines which Buildings unlock access to advanced Buildings, Weapons, and upgrades.
- **Experience_Points**: A numeric value earned by a Player through combat achievements such as destroying enemy Buildings and eliminating other Players. Experience_Points accumulate toward Rank progression.
- **Rank**: A levelling tier assigned to a Player based on accumulated Experience_Points. Higher Rank levels unlock new bonuses and technologies. A Player can lose Rank levels when Experience_Points decrease below the threshold for the current Rank.
- **Offline_Building**: The protected state a Building enters when the owning Player logs out or disconnects. An Offline_Building cannot be destroyed or entered by other Players.
- **Game_Tick**: A recurring server time interval configured to 10 seconds. Game_Tick drives periodic game events such as automated Resource production from Resource_Buildings.
- **Resource_Building**: A Player-constructed Building that automatically produces a specific Resource each Game_Tick. Resource_Buildings have a Building_Level that determines production yield.
- **Building_Level**: A numeric value representing the upgrade tier of a Resource_Building. Higher Building_Level increases the Resource production yield per Game_Tick. Building_Level starts at 1 upon construction and has a maximum value of 5.
- **Global_Notification**: A server-wide message broadcast by the Game_Server to all connected Players regardless of Room location. Global_Notifications announce significant game events such as Player logins, logouts, eliminations, and Rank changes.
- **Global_Chat**: A server-wide chat message sent by a Player using the "chat" command that is delivered to all connected Players regardless of Room location. Global_Chat messages include the sender Player name and Rank.
- **Direct_Message**: A private chat message sent by a Player using the "message" command to a specific target Player by name. A Direct_Message is visible only to the sending Player and the receiving Player. Direct_Messages include the sender Player name and Rank.
- **Headquarters**: The foundational Building (abbreviated HQ) that a Player must construct before any other Building type can be built. The Headquarters serves as the prerequisite for all other Buildings in the Technology_Tree.
- **Equipment_Building**: A category of Building that automatically generates equipment items each Game_Tick. Equipment_Buildings include the Armory (generates weapons) and the Armorer (generates defense equipment). Equipment_Buildings can be constructed on any Terrain_Type and require a Headquarters.
- **Defense_Building**: A category of Building that provides automated defensive capabilities. Defense_Buildings include the Turret. Defense_Buildings require a Headquarters.
- **Turret**: A Defense_Building (abbreviated VV) that automatically attacks enemy Players within a 10-Room radius each Game_Tick. Turrets can be constructed on any Terrain_Type and require a Headquarters.

## Requirements

### Requirement 1: Client Connection Management

**User Story:** As a player, I want to connect to the game server using a telnet client, so that I can access the multiplayer text adventure.

#### Acceptance Criteria

1. THE Game_Server SHALL accept incoming TCP socket connections on a configurable host and port.
2. WHEN a Client connects, THE Game_Server SHALL send a welcome message and a login prompt to the Client.
3. WHILE the Game_Server is running, THE Game_Server SHALL support multiple simultaneous Client connections.
4. IF a Client connection drops unexpectedly, THEN THE Game_Server SHALL clean up the associated Session and release resources.
5. THE Game_Server SHALL handle each Client connection without blocking other connected Clients.

### Requirement 2: Player Authentication

**User Story:** As a player, I want to log in with a username and password, so that my identity and progress are associated with my account.

#### Acceptance Criteria

1. WHEN a Client provides a username and password, THE Game_Server SHALL authenticate the credentials against stored Player data.
2. WHEN authentication succeeds, THE Game_Server SHALL transition the Client into an active Player Session and place the Player in a starting Room.
3. IF authentication fails, THEN THE Game_Server SHALL inform the Client of the failure and re-display the login prompt.
4. WHEN a new username is provided that does not exist, THE Game_Server SHALL offer the Client the option to create a new Player account.
5. THE Game_Server SHALL prevent multiple simultaneous Sessions for the same Player account.

### Requirement 3: Game Loop and Command Processing

**User Story:** As a player, I want to type commands and receive responses, so that I can interact with the game world.

#### Acceptance Criteria

1. WHILE a Player is in an active Session, THE Game_Loop SHALL continuously read input from the Player and process recognized commands.
2. WHEN a Player submits a command, THE Command_Parser SHALL parse the input text and identify the intended action.
3. IF a Player submits an unrecognized command, THEN THE Command_Parser SHALL return a helpful error message indicating the command is not understood.
4. WHEN a command is processed, THE Game_Server SHALL send the resulting output text to the Player within 1 second.
5. THE Game_Loop SHALL process commands from each Player independently without one Player blocking another.

### Requirement 4: World Navigation

**User Story:** As a player, I want to move between rooms in the game world, so that I can explore different areas.

#### Acceptance Criteria

1. THE Game_Server SHALL maintain a multi-dimensional array that represents the world. Each coordinate in the world map represents a room that the user can navigate to.
2. WHEN a Player issues a movement command specifying a valid exit direction, THE Game_Server SHALL move the Player to the destination Room and display the Room description.
3. IF a Player issues a movement command specifying an invalid exit direction, THEN THE Game_Server SHALL inform the Player that movement in that direction is not possible.
4. WHEN a Player enters a Room, THE Game_Server SHALL display the Room coordinates, description, visible items, and other Players present in the Room.
5. THE Game_Server SHALL assign each Room exactly one Terrain_Type or one Building.

### Requirement 5: Multiplayer Interaction

**User Story:** As a player, I want to see and communicate with other players in the same room, so that the game feels like a shared experience.

#### Acceptance Criteria

1. WHEN a Player enters a Room, THE Game_Server SHALL notify all other Players in that Room that the Player has arrived.
2. WHEN a Player leaves a Room, THE Game_Server SHALL notify all remaining Players in that Room that the Player has departed.
3. WHEN a Player issues a "say" command with a message, THE Game_Server SHALL broadcast the message to all Players in the same Room.
4. WHEN a Player requests a list of Players in the current Room, THE Game_Server SHALL display the names of all Players present.

### Requirement 6: Player Logout and Disconnection

**User Story:** As a player, I want to log out of the game gracefully, so that my session ends cleanly.

#### Acceptance Criteria

1. WHEN a Player issues a "quit" command, THE Game_Server SHALL save the Player state, notify other Players in the Room of the departure, and close the Session.
2. WHEN a Player disconnects, THE Game_Server SHALL send a farewell message to the Player before closing the connection.
3. IF a Player Session is terminated due to unexpected disconnection, THEN THE Game_Server SHALL save the Player state and remove the Player from the current Room.
4. WHEN a Player logs out or disconnects, THE Game_Server SHALL transition all Buildings owned by the Player to the Offline_Building state.
5. WHILE a Building is in the Offline_Building state, THE Game_Server SHALL prevent all other Players from dealing Damage to the Offline_Building.
6. WHILE a Building is in the Offline_Building state, THE Game_Server SHALL prevent all other Players from entering the Room occupied by the Offline_Building.
7. WHEN a Player logs in, THE Game_Server SHALL transition all Offline_Buildings owned by the Player back to the normal Building state.

### Requirement 7: Game State Persistence

**User Story:** As a player, I want my progress to be saved, so that I can resume where I left off when I reconnect.

#### Acceptance Criteria

1. WHEN a Player logs out or disconnects, THE Game_Server SHALL persist the Player state including current Room location.
2. WHEN a Player logs in, THE Game_Server SHALL restore the Player to the Room where the Player last logged out.
3. THE Game_Server SHALL store Player data in a file-based format that survives server restarts.

### Requirement 8: Server Lifecycle Management

**User Story:** As a server operator, I want to start and stop the game server cleanly, so that player data is not lost.

#### Acceptance Criteria

1. WHEN the Game_Server starts, THE Game_Server SHALL load the world definition and all persisted Player data.
2. WHEN the Game_Server receives a shutdown signal, THE Game_Server SHALL notify all connected Players, save all Player states, and close all connections before exiting.
3. IF the Game_Server encounters a fatal error, THEN THE Game_Server SHALL log the error details and attempt to save all Player states before exiting.

### Requirement 9: Building Construction

**User Story:** As a player, I want to gather resources and construct buildings, so that I can unlock new features, technologies, upgrades, and defenses for my territory.

#### Acceptance Criteria

1. THE Game_Server SHALL define a set of Resource types that a Player can gather from Rooms based on the Room Terrain_Type.
2. WHEN a Player issues a "gather" command in a Room containing available Resources, THE Game_Server SHALL add the gathered Resource quantity to the Player inventory.
3. THE Game_Server SHALL define a set of Building types, each with a unique material cost expressed as a list of Resource quantities. The complete Building tree and all Building types are defined in Requirement 15.
4. WHEN a Player issues a "build" command specifying a valid Building type and the Player possesses sufficient Resources and the Player has satisfied the Building prerequisite as defined in Requirement 15, THE Game_Server SHALL deduct the material cost from the Player inventory and place the Building in the current Room.
5. IF a Player issues a "build" command and the Player does not possess sufficient Resources, THEN THE Game_Server SHALL inform the Player of the missing Resources and their quantities.
6. IF a Player issues a "build" command for a Building type that requires a Headquarters and the Player has not constructed a Headquarters, THEN THE Game_Server SHALL inform the Player that a Headquarters must be built first.
7. THE Game_Server SHALL assign each Building a Health value upon construction based on the Building type.
8. WHEN a Building is constructed, THE Game_Server SHALL evaluate the Technology_Tree and unlock any new Building types, Weapon types, or upgrades that the constructed Building enables for the owning Player.
9. IF a Building Health value reaches zero, THEN THE Game_Server SHALL remove the Building from the Room and revoke any features or unlocks that depended solely on the destroyed Building.
10. WHEN a Player enters a Room containing a Building, THE Game_Server SHALL display the Building type, owning Player name, and current Health value.
11. THE Game_Server SHALL prevent a Player from constructing more than one Building in the same Room.
12. WHEN a Player issues an "upgrade" command targeting a Resource_Building the Player owns, THE Game_Server SHALL increase the Resource_Building Building_Level as defined in Requirement 12.
13. THE Game_Server SHALL require a Player to construct a Headquarters before constructing any other Building type as defined in Requirement 15.

### Requirement 10: Combat and Attacking

**User Story:** As a player, I want to attack other players and their buildings using modern and futuristic weapons, so that I can compete for territory and resources in the war setting.

#### Acceptance Criteria

1. THE Game_Server SHALL define a set of Weapon types with modern and futuristic classifications, each with a Damage value and an optional Resource cost per use.
2. WHEN a Player issues an "attack" command targeting another Player in the same Room, THE Game_Server SHALL calculate Damage based on the attacking Player equipped Weapon and subtract the Damage from the target Player Health.
3. WHEN a Player issues an "attack" command targeting a Building in the same Room, THE Game_Server SHALL calculate Damage based on the attacking Player equipped Weapon and subtract the Damage from the Building Health.
4. IF a Player issues an "attack" command and no valid target exists in the Room, THEN THE Game_Server SHALL inform the Player that the specified target is not present.
5. WHEN a Player attacks another Player, THE Game_Server SHALL notify the target Player of the attack, the attacking Player name, the Weapon used, and the Damage dealt.
6. WHEN a Player attacks a Building, THE Game_Server SHALL notify the Building owning Player of the attack, the attacking Player name, the Weapon used, and the Damage dealt.
7. IF a Player Health value reaches zero as a result of combat, THEN THE Game_Server SHALL remove the Player from the Room, respawn the Player at a designated starting Room, and restore the Player Health to the default value.
8. WHEN a Player issues an "equip" command specifying a valid Weapon, THE Game_Server SHALL set the specified Weapon as the Player active Weapon.
9. THE Game_Server SHALL prevent a Player from attacking the Player own Buildings.
10. WHEN a combat action consumes Resources as ammunition, THE Game_Server SHALL deduct the Resource cost from the attacking Player inventory before applying Damage.

### Requirement 11: Rank and Experience Progression

**User Story:** As a player, I want to earn experience points and gain ranks through combat, so that I can unlock new bonuses and technologies as I progress.

#### Acceptance Criteria

1. THE Game_Server SHALL assign each Player an Experience_Points value initialized to zero upon account creation.
2. THE Game_Server SHALL assign each Player a Rank level derived from the Player accumulated Experience_Points.
3. WHEN a Player destroys an enemy Building, THE Game_Server SHALL award Experience_Points to the Player based on the destroyed Building type.
4. WHEN a Player eliminates another Player by reducing the target Player Health to zero, THE Game_Server SHALL award Experience_Points to the attacking Player.
5. WHEN a Player accumulated Experience_Points reaches the threshold for the next Rank level, THE Game_Server SHALL promote the Player to the next Rank level and notify the Player of the promotion.
6. WHEN a Player is eliminated by another Player, THE Game_Server SHALL deduct Experience_Points from the eliminated Player.
7. IF a Player Experience_Points falls below the threshold for the Player current Rank level after a deduction, THEN THE Game_Server SHALL demote the Player to the appropriate lower Rank level and notify the Player of the demotion.
8. THE Game_Server SHALL define a set of Rank levels with ascending Experience_Points thresholds.
9. WHEN a Player reaches a new Rank level, THE Game_Server SHALL unlock bonuses and technologies associated with that Rank level for the Player.
10. IF a Player is demoted to a lower Rank level, THEN THE Game_Server SHALL revoke bonuses and technologies that require the lost Rank level.
11. THE Game_Server SHALL persist the Player Experience_Points and Rank level as part of the Player state.
12. THE Game_Server SHALL define the following Rank levels in ascending order: Recruit, Private, Private First Class, Specialist, Corporal, Sergeant, Staff Sergeant, Sergeant First Class, First Sergeant, Master Sergeant, Command Sergeant Major, Sergeant Major, 2nd Lieutenant, 1st Lieutenant, Captain, Major, Lieutenant Colonel, Colonel, Brigadier General, Major General, Lieutenant General, General.

### Requirement 12: Resource Gathering and Production

**User Story:** As a player, I want to gather terrain-specific resources, build resource-generating buildings, and upgrade them, so that I can produce resources efficiently and manage my inventory.

#### Acceptance Criteria

1. THE Game_Server SHALL define the following Terrain_Type-to-Resource mapping: Plains yields Straw, Mud yields Clay, Forest yields Wood, Rock yields Stone, and Mountain yields Iron.
2. WHEN a Player issues a "gather" command in a Room without a Resource_Building, THE Game_Server SHALL add 1 unit of the Resource corresponding to the Room Terrain_Type to the Player inventory, up to a maximum of 1 Resource available per Room at a time.
3. THE Game_Server SHALL define a Game_Tick interval of 10 seconds that drives periodic game events.
4. WHEN a Player issues an "inventory" command, THE Game_Server SHALL display the Player current Resource quantities for all Resource types.
5. WHEN a Player issues a "build" command specifying a Resource_Building type and the Player possesses sufficient Resources and the Player has constructed a Headquarters, THE Game_Server SHALL construct a Resource_Building at Building_Level 1 in the current Room.
6. WHILE a Resource_Building is active and the owning Player is online, THE Game_Server SHALL produce Resources per Game_Tick according to a scaling formula based on Building_Level, yielding up to a maximum of 1000 Resources per Game_Tick at Building_Level 5.
7. WHEN a Player issues an "upgrade" command targeting a Resource_Building the Player owns and the Player possesses sufficient Resources, THE Game_Server SHALL deduct the upgrade cost from the Player inventory and increase the Resource_Building Building_Level by 1.
8. THE Game_Server SHALL calculate the Resource cost for upgrading a Resource_Building as the base construction cost multiplied by the target Building_Level.
9. IF a Player issues an "upgrade" command and the Player does not possess sufficient Resources for the upgrade cost, THEN THE Game_Server SHALL inform the Player of the missing Resources and their quantities.
10. WHEN a Resource_Building Building_Level increases, THE Game_Server SHALL increase the Resource production yield per Game_Tick proportionally to the new Building_Level.
11. THE Game_Server SHALL persist the Player inventory including all Resource quantities as part of the Player state.
12. IF a Player issues a "gather" command in a Room with no valid Terrain_Type for resource collection, THEN THE Game_Server SHALL inform the Player that no resources are available in the current Room.
13. THE Game_Server SHALL enforce a maximum Building_Level of 5 for all Resource_Buildings.
14. IF a Player issues an "upgrade" command targeting a Resource_Building that is already at Building_Level 5, THEN THE Game_Server SHALL reject the upgrade and inform the Player that the Resource_Building is already at the maximum Building_Level.
15. WHILE a Room does not contain a Resource_Building, THE Game_Server SHALL limit the available gatherable Resources in that Room to 1 unit at a time.
16. WHEN a Resource_Building reaches Building_Level 5, THE Game_Server SHALL produce a maximum of 1000 Resources per Game_Tick for that Resource_Building.

### Requirement 13: Global Notification System

**User Story:** As a player, I want to receive server-wide announcements about significant game events, so that I stay informed about important happenings across the game world.

#### Acceptance Criteria

1. WHEN a Player logs in, THE Game_Server SHALL send a Global_Notification to all connected Players announcing the Player login.
2. WHEN a Player logs out or disconnects, THE Game_Server SHALL send a Global_Notification to all connected Players announcing the Player departure.
3. WHEN a Player eliminates another Player by reducing the target Player Health to zero, THE Game_Server SHALL send a Global_Notification to all connected Players announcing the elimination, including the attacking Player name and the eliminated Player name.
4. WHEN a Player is promoted to a higher Rank level, THE Game_Server SHALL send a Global_Notification to all connected Players announcing the Player promotion and the new Rank level.
5. WHEN a Player is demoted to a lower Rank level, THE Game_Server SHALL send a Global_Notification to all connected Players announcing the Player demotion and the new Rank level.
6. THE Game_Server SHALL deliver each Global_Notification to all connected Players regardless of the Room each Player currently occupies.

### Requirement 14: Chat System

**User Story:** As a player, I want to communicate with other players through global chat and private messages, so that I can coordinate, socialize, and strategize with others across the game world.

#### Acceptance Criteria

1. WHEN a Player issues a "chat" command with a message, THE Game_Server SHALL deliver the message as a Global_Chat to all connected Players regardless of Room location.
2. THE Game_Server SHALL include the sending Player name and Rank in each Global_Chat message.
3. WHEN a Player issues a "message" command specifying a target Player name and a message, THE Game_Server SHALL deliver the message as a Direct_Message visible only to the sending Player and the target Player.
4. THE Game_Server SHALL include the sending Player name and Rank in each Direct_Message.
5. IF a Player issues a "message" command specifying a target Player name that is not currently online, THEN THE Game_Server SHALL inform the sending Player that the target Player is not online.
6. WHEN a Player issues a "say" command with a message, THE Game_Server SHALL broadcast the message to all Players in the same Room as defined in Requirement 5 Acceptance Criterion 3.

### Requirement 15: Building Tree and Building Types

**User Story:** As a player, I want a structured building tree with a Headquarters prerequisite and specialized building types for resource generation, equipment production, and defense, so that I can strategically develop my territory with diverse capabilities.

#### Acceptance Criteria

1. THE Game_Server SHALL define the Headquarters (HQ) as the foundational Building type that must be constructed before any other Building type.
2. IF a Player issues a "build" command for any Building type other than Headquarters and the Player has not constructed a Headquarters, THEN THE Game_Server SHALL reject the construction and inform the Player that a Headquarters is required first.
3. THE Game_Server SHALL define the following Resource_Building types, each requiring a Headquarters and a specific Terrain_Type: Mill (MM) on Plains generating Straw, Quarry (QQ) on Rock generating Stone, Mine (II) on Mountain generating Iron, Lumberyard (LL) on Forest generating Wood, and Kiln (KK) on Mud generating Clay.
4. IF a Player issues a "build" command for a Resource_Building type and the Room Terrain_Type does not match the required Terrain_Type for that Resource_Building, THEN THE Game_Server SHALL reject the construction and inform the Player of the required Terrain_Type.
5. WHILE a Resource_Building is active and the owning Player is online, THE Game_Server SHALL generate the associated Resource for the owning Player on each Game_Tick as defined in Requirement 12.
6. THE Game_Server SHALL define the following Equipment_Building types, each requiring a Headquarters and constructible on any Terrain_Type: Armory (AA) generating weapons, and Armorer (AR) generating defense equipment.
7. WHILE an Equipment_Building is active and the owning Player is online, THE Game_Server SHALL generate the associated equipment for the owning Player on each Game_Tick.
8. THE Game_Server SHALL define the Turret (VV) as a Defense_Building type that requires a Headquarters and is constructible on any Terrain_Type.
9. WHILE a Turret is active and the owning Player is online, THE Game_Server SHALL attack enemy Players within a 10-Room radius on each Game_Tick.
10. THE Game_Server SHALL identify each Building type by the following abbreviations: Headquarters (HQ), Mill (MM), Quarry (QQ), Mine (II), Lumberyard (LL), Kiln (KK), Armory (AA), Armorer (AR), and Turret (VV).
