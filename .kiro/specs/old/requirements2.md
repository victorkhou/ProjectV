# Requirements Document

## Introduction

The multiplayer text adventure game server currently has game balance values, entity definitions, server settings, and player data storage all hardcoded in Python source files. This feature extracts all configurable and growable data into external configuration files, introduces a proper database backend for player persistence, adds quality-of-life player commands, implements ANSI color formatting, introduces a trust-level-based admin system, adds operational tooling (structured logging, metrics, hot-reload), and improves scalability through world chunking, async database access, periodic saves, and a plugin-based command architecture.

## Glossary

- **Config_Loader**: The module responsible for reading, validating, and providing access to server and game configuration from external files.
- **Data_Loader**: The module responsible for reading, validating, and providing access to game entity definitions (buildings, weapons, ranks, etc.) from external data files.
- **Player_Database**: The SQLite-based storage backend for persisting player data.
- **Server_Config**: The external configuration file containing server operational settings (host, port, world size, tick interval, data directory).
- **Game_Balance_Config**: The section of configuration containing tunable numeric values for game balance (production scaling, turret damage, XP awards, gather amounts, turret radius).
- **Building_Definition**: A data-file entry describing a building type's name, abbreviation, cost, health, terrain requirement, category, production output, and tech-tree unlocks.
- **Weapon_Definition**: A data-file entry describing a weapon's name, damage, ammo cost, and classification.
- **Rank_Definition**: A data-file entry describing a rank's name, level, XP threshold, and unlocks.
- **Command_Definition**: A data-file entry describing a recognized command's name, optional aliases, and metadata.
- **Command_Alias**: A short string (e.g., "i", "inv") defined in a Command_Definition that resolves to the parent command name.
- **Prefix_Match**: The process of resolving a partial input string to a command by checking if it is a unique prefix of exactly one command name.
- **Equipment_Definition**: A data-file entry describing equipment produced by Armory or Armorer buildings.
- **Terrain_Definition**: A data-file entry mapping a terrain type to its gatherable resource and description text.
- **Data_File**: A YAML or JSON file stored in a designated data directory that contains entity definitions.
- **Schema_Validator**: The component that validates Data_File contents against expected schemas before the game uses the data.
- **Trust_Level**: An integer (1–5) assigned to each player account that determines access to administrative commands. Level 1 is a regular player; level 5 has full administrative access.
- **Admin_Command**: A command restricted to players with a Trust_Level above 1. Each Admin_Command specifies the minimum Trust_Level required to execute it.
- **ANSI_Formatter**: The component responsible for wrapping output text in ANSI escape codes for color and style, with a fallback to plain text for clients that do not support ANSI.
- **ASCII_Map**: A text-based grid rendering of rooms surrounding the player's current position, showing terrain symbols, buildings, and player indicators.
- **Event_Bus**: A publish-subscribe system that decouples game systems by allowing them to emit and listen for named events (e.g., player_moved, building_destroyed) without direct method calls.
- **Command_Plugin**: A self-contained module that registers one or more commands with the Command_Dispatcher, replacing centralized handler registration.
- **World_Chunk**: A rectangular sub-region of the world grid that is loaded and processed independently, allowing the server to skip inactive areas during tick processing.
- **Metrics_Endpoint**: A lightweight HTTP endpoint or log-based output that exposes server operational metrics (player count, commands per second, tick duration).
- **Hot_Reload**: The ability to re-read and re-validate Data_Files at runtime without restarting the server, applying updated definitions to the running game.
- **MCCP2**: MUD Client Compression Protocol version 2, a telnet option (option code 86, COMPRESS2) that enables zlib compression of server-to-client data. Negotiated via standard IAC WILL/DO sequences, with compression starting after an IAC SB COMPRESS2 IAC SE subnegotiation.

## Requirements

### Requirement 1: Server Configuration File

**User Story:** As a server operator, I want server operational settings defined in an external configuration file, so that I can change host, port, world size, and tick interval without modifying Python source code.

#### Acceptance Criteria

1. THE Config_Loader SHALL load Server_Config from a YAML file at a configurable path (defaulting to `config/server.yaml`).
2. WHEN the Server_Config file is missing, THE Config_Loader SHALL use hardcoded default values for all server settings and log a warning.
3. WHEN the Server_Config file contains an invalid value (wrong type, out-of-range), THEN THE Config_Loader SHALL raise a descriptive validation error at startup identifying the invalid field and expected type.
4. THE Server_Config SHALL support the following settings: host (string), port (integer), world_size (integer), tick_interval (float), and data_dir (string).
5. WHEN the Server_Config file contains unknown keys, THE Config_Loader SHALL ignore the unknown keys and log a warning for each.

### Requirement 2: Game Balance Configuration

**User Story:** As a game designer, I want game balance values defined in an external configuration file, so that I can tune gameplay without modifying Python source code.

#### Acceptance Criteria

1. THE Config_Loader SHALL load Game_Balance_Config from a YAML file at a configurable path (defaulting to `config/balance.yaml`).
2. THE Game_Balance_Config SHALL include: production_scaling (mapping of building level to output amount), turret_damage (integer), turret_radius (integer), xp_building_destruction (integer), xp_player_elimination (integer), xp_elimination_penalty (integer), gather_amount (integer), and player_default_health (integer).
3. WHEN the Game_Balance_Config file is missing, THE Config_Loader SHALL use hardcoded default values matching the current game behavior and log a warning.
4. WHEN the Game_Balance_Config file contains an invalid value, THEN THE Config_Loader SHALL raise a descriptive validation error at startup.
5. THE Config_Loader SHALL make all Game_Balance_Config values accessible to game systems at runtime without requiring those systems to read files directly.

### Requirement 3: Building Definitions Data File

**User Story:** As a game designer, I want building types defined in an external data file, so that I can add, remove, or modify buildings without changing Python source code.

#### Acceptance Criteria

1. THE Data_Loader SHALL load Building_Definition entries from a YAML file at a configurable path (defaulting to `data/definitions/buildings.yaml`).
2. WHEN the buildings Data_File is loaded, THE Schema_Validator SHALL verify that each Building_Definition contains: name (string), abbreviation (string), cost (mapping of resource name to integer), max_health (positive integer), requires_hq (boolean), required_terrain (terrain type string or null), category (one of "headquarters", "resource", "equipment", "defense"), produces (string or null), and unlocks (list of abbreviation strings).
3. WHEN a Building_Definition references a required_terrain value that does not match any Terrain_Definition, THEN THE Schema_Validator SHALL report a validation error identifying the invalid terrain reference.
4. WHEN a Building_Definition references an unlock abbreviation that does not exist in the loaded Building_Definition set, THEN THE Schema_Validator SHALL report a validation error identifying the dangling reference.
5. THE Data_Loader SHALL replace the hardcoded BUILDING_TYPES dictionary in src/models.py with data loaded from the buildings Data_File.
6. WHEN the buildings Data_File is missing, THEN THE Data_Loader SHALL raise an error at startup because building definitions are required for the game to function.

### Requirement 4: Weapon Definitions Data File

**User Story:** As a game designer, I want weapon types defined in an external data file, so that I can add new weapons or adjust damage values without changing Python source code.

#### Acceptance Criteria

1. THE Data_Loader SHALL load Weapon_Definition entries from a YAML file at a configurable path (defaulting to `data/definitions/weapons.yaml`).
2. WHEN the weapons Data_File is loaded, THE Schema_Validator SHALL verify that each Weapon_Definition contains: name (string), key (string identifier), damage (positive integer), ammo_cost (mapping of resource name to integer, or null), and classification (string, defaulting to "modern").
3. THE Data_Loader SHALL replace the hardcoded WEAPONS dictionary in src/combat.py with data loaded from the weapons Data_File.
4. WHEN the weapons Data_File is missing, THEN THE Data_Loader SHALL raise an error at startup because weapon definitions are required for the game to function.

### Requirement 5: Rank Definitions Data File

**User Story:** As a game designer, I want rank levels and XP thresholds defined in an external data file, so that I can adjust progression without changing Python source code.

#### Acceptance Criteria

1. THE Data_Loader SHALL load Rank_Definition entries from a YAML file at a configurable path (defaulting to `data/definitions/ranks.yaml`).
2. WHEN the ranks Data_File is loaded, THE Schema_Validator SHALL verify that each Rank_Definition contains: name (string), level (non-negative integer), xp_threshold (non-negative integer), and unlocks (list of strings).
3. WHEN the ranks Data_File is loaded, THE Schema_Validator SHALL verify that rank levels are unique and that xp_threshold values are strictly increasing with level.
4. THE Data_Loader SHALL replace the hardcoded RANK_LEVELS list in src/models.py with data loaded from the ranks Data_File.
5. WHEN the ranks Data_File is missing, THEN THE Data_Loader SHALL raise an error at startup because rank definitions are required for the game to function.

### Requirement 6: Equipment Definitions Data File

**User Story:** As a game designer, I want equipment lists (armory weapons, armorer defense items) defined in an external data file, so that I can expand equipment options without changing Python source code.

#### Acceptance Criteria

1. THE Data_Loader SHALL load Equipment_Definition entries from a YAML file at a configurable path (defaulting to `data/definitions/equipment.yaml`).
2. THE Equipment_Definition entries SHALL be organized by producing building type (e.g., "AA" for armory weapons, "AR" for armorer defense items).
3. WHEN the equipment Data_File is loaded, THE Schema_Validator SHALL verify that each producing building type key matches a loaded Building_Definition abbreviation with category "equipment".
4. THE Data_Loader SHALL replace the hardcoded ARMORY_WEAPONS and ARMORER_DEFENSE lists in src/buildings.py with data loaded from the equipment Data_File.
5. WHEN the equipment Data_File is missing, THEN THE Data_Loader SHALL raise an error at startup because equipment definitions are required for the game to function.

### Requirement 7: Terrain Definitions Data File

**User Story:** As a game designer, I want terrain types, their resource mappings, and descriptions defined in an external data file, so that I can add new terrain types without changing Python source code.

#### Acceptance Criteria

1. THE Data_Loader SHALL load Terrain_Definition entries from a YAML file at a configurable path (defaulting to `data/definitions/terrain.yaml`).
2. WHEN the terrain Data_File is loaded, THE Schema_Validator SHALL verify that each Terrain_Definition contains: type (string), resource (string), and description (string).
3. THE Data_Loader SHALL replace the hardcoded TERRAIN_RESOURCES dictionary and TERRAIN_DESCRIPTIONS dictionary with data loaded from the terrain Data_File.
4. WHEN the terrain Data_File is missing, THEN THE Data_Loader SHALL raise an error at startup because terrain definitions are required for world generation.

### Requirement 8: Command Definitions Data File

**User Story:** As a developer, I want the set of recognized commands defined in an external data file with optional aliases, so that new commands can be registered without modifying the command parser source code.

#### Acceptance Criteria

1. THE Data_Loader SHALL load Command_Definition entries from a YAML file at a configurable path (defaulting to `data/definitions/commands.yaml`).
2. WHEN the commands Data_File is loaded, THE Schema_Validator SHALL verify that each Command_Definition contains: name (string), and optionally aliases (list of strings), description (string), and usage (string).
3. THE Data_Loader SHALL replace the hardcoded KNOWN_COMMANDS set in src/command_parser.py with command names and aliases loaded from the commands Data_File.
4. WHEN the commands Data_File is missing, THEN THE Data_Loader SHALL raise an error at startup because command definitions are required for the game to function.
5. WHEN a Command_Definition includes an aliases list, THE Command_Parser SHALL recognize each alias as equivalent to the command name (e.g., alias "i" resolves to command "inventory").

### Requirement 13: Command Shorthand and Prefix Matching

**User Story:** As a player, I want to type abbreviated commands (like "i" for "inventory" or "att" for "attack"), so that I can interact with the game faster without typing full command names.

#### Acceptance Criteria

1. WHEN a player submits input that exactly matches a command name or alias, THE Command_Parser SHALL resolve it to that command.
2. WHEN a player submits input that is a unique prefix of exactly one command name, THE Command_Parser SHALL resolve it to that command (e.g., "inv" resolves to "inventory" if no other command starts with "inv").
3. WHEN a player submits input that is a prefix matching multiple command names, THE Command_Parser SHALL return an error message listing the ambiguous matches (e.g., "Ambiguous command 'me'. Did you mean: message, move?").
4. WHEN a player submits input that does not match any command name, alias, or unique prefix, THE Command_Parser SHALL return the existing unrecognized command error message.
5. THE Command_Parser SHALL check for exact alias matches before attempting prefix matching, so that a defined alias always takes priority over prefix resolution.

### Requirement 9: SQLite Player Database Backend

**User Story:** As a server operator, I want player data stored in a SQLite database instead of individual JSON files, so that player persistence is reliable, queryable, and scales with the number of players.

#### Acceptance Criteria

1. THE Player_Database SHALL store all player fields currently serialized in JSON (username, password_hash, x, y, health, max_health, inventory, equipped_weapon, available_weapons, available_buildings, xp, rank_level, has_hq, unlocked_bonuses) in a SQLite database file.
2. THE Player_Database SHALL create the database schema automatically on first startup when the database file does not exist.
3. WHEN a player is saved, THE Player_Database SHALL persist the player data within a single database transaction.
4. WHEN a player is loaded, THE Player_Database SHALL deserialize stored data back into a Player object with all fields intact.
5. IF a database write fails, THEN THE Player_Database SHALL log the error and raise an exception without leaving partial data committed.
6. THE Player_Database SHALL support loading all players for startup initialization, matching the current load_all_players behavior.
7. THE Player_Database SHALL support checking whether a player exists by username, matching the current player_exists behavior.

### Requirement 10: Player Data Migration

**User Story:** As a server operator, I want existing JSON player files automatically migrated to the new SQLite database, so that no player data is lost during the upgrade.

#### Acceptance Criteria

1. WHEN the Player_Database is initialized and the database is empty, THE Player_Database SHALL check for existing JSON player files in the legacy data/players directory.
2. WHEN legacy JSON player files are found, THE Player_Database SHALL import each valid player file into the SQLite database.
3. IF a legacy JSON player file is corrupted or invalid, THEN THE Player_Database SHALL skip the corrupted file, log a warning identifying the file, and continue migrating remaining files.
4. WHEN migration completes successfully, THE Player_Database SHALL log the count of migrated players.

### Requirement 11: Data File Parsing and Pretty-Printing

**User Story:** As a developer, I want a YAML parser and serializer for game data files, so that definitions can be reliably read and written in a human-friendly format.

#### Acceptance Criteria

1. THE Data_Loader SHALL parse YAML Data_Files into Python data structures (dictionaries and lists).
2. THE Data_Loader SHALL serialize Python data structures back into valid YAML format for writing Data_Files.
3. FOR ALL valid data structures, parsing a Data_File then serializing the result then parsing again SHALL produce an equivalent data structure (round-trip property).

### Requirement 12: Centralized Data Registry

**User Story:** As a developer, I want a single registry that holds all loaded game definitions, so that game systems can access building types, weapons, ranks, and other definitions from one place without importing module-level constants.

#### Acceptance Criteria

1. THE Data_Loader SHALL provide a centralized registry object that holds all loaded definitions (buildings, weapons, ranks, equipment, terrain, commands) and configuration (server, balance).
2. WHEN the game starts up, THE Data_Loader SHALL load and validate all Data_Files and configuration before any game system is initialized.
3. THE centralized registry SHALL be injectable into game systems (BuildingSystem, CombatSystem, RankSystem, ResourceSystem, CommandParser) replacing direct imports of module-level constants.
4. IF any required Data_File fails validation, THEN THE Data_Loader SHALL prevent game startup and report all validation errors.

### Requirement 14: Who Command

**User Story:** As a player, I want to type "who" to see a list of all connected players with their high-level stats, so that I can see who's online and how they compare.

#### Acceptance Criteria

1. WHEN a player issues a "who" command, THE Game_Server SHALL display a list of all currently connected players.
2. FOR EACH connected player in the list, THE Game_Server SHALL display the player's username, current rank name, XP total, and current room coordinates.
3. THE "who" command output SHALL sort players by rank level in descending order (highest rank first).
4. WHEN no other players are connected, THE Game_Server SHALL display a message indicating the player is the only one online.
5. THE "who" command SHALL be included in the commands Data_File with alias "w".

### Requirement 15: ASCII Map Command

**User Story:** As a player, I want to type "map" to see a small ASCII grid of the area around me, so that I can orient myself without memorizing coordinates.

#### Acceptance Criteria

1. WHEN a player issues a "map" command, THE Game_Server SHALL display an ASCII_Map grid centered on the player's current position.
2. THE ASCII_Map SHALL show a configurable radius of rooms around the player (defaulting to 5 rooms in each direction).
3. EACH cell in the ASCII_Map SHALL display a single-character symbol representing the room's terrain type (e.g., "P" for Plains, "F" for Forest, "M" for Mountain, "R" for Rock, "~" for Mud).
4. WHEN a room contains a building, THE ASCII_Map SHALL display the building's abbreviation character instead of the terrain symbol.
5. THE ASCII_Map SHALL mark the player's current position with a distinct indicator (e.g., "@").
6. THE ASCII_Map SHALL mark rooms containing other players with a distinct indicator (e.g., "*").
7. THE "map" command SHALL be included in the commands Data_File with alias "m".

### Requirement 16: Score/Stats Command

**User Story:** As a player, I want to type "score" to see my own stats at a glance, so that I can track my progress without checking multiple commands.

#### Acceptance Criteria

1. WHEN a player issues a "score" command, THE Game_Server SHALL display the player's username, current rank name and level, XP total, health (current/max), equipped weapon name, and total building count.
2. THE building count SHALL reflect the number of buildings the player currently owns on the world map.
3. THE "score" command SHALL be included in the commands Data_File with aliases "sc" and "stats".

### Requirement 17: Buildings List Command

**User Story:** As a player, I want to type "buildings" to see a list of all my buildings with their locations and levels, so that I can manage my territory.

#### Acceptance Criteria

1. WHEN a player issues a "buildings" command, THE Game_Server SHALL display a list of all buildings owned by the player.
2. FOR EACH building in the list, THE Game_Server SHALL display the building type name, abbreviation, room coordinates, current level (for Resource_Buildings), and current health.
3. WHEN the player owns no buildings, THE Game_Server SHALL display a message indicating the player has no buildings.
4. THE "buildings" command SHALL be included in the commands Data_File with alias "bl".

### Requirement 18: Directional Movement Shortcuts

**User Story:** As a player, I want to type "n", "s", "e", or "w" as standalone commands to move in that direction, so that navigation is faster.

#### Acceptance Criteria

1. THE commands Data_File SHALL define "n", "s", "e", and "w" as aliases for the "move" command.
2. WHEN a player issues "n", "s", "e", or "w" without arguments, THE Command_Parser SHALL resolve the alias to "move" and THE Game_Server SHALL treat the alias letter as the direction argument (north, south, east, west respectively).
3. THE directional shortcuts SHALL support the same movement validation as the full "move" command (bounds checking, offline building blocking).

### Requirement 19: Room Resource Respawn

**User Story:** As a player, I want room resources to regenerate over time, so that gathering remains viable as the game progresses.

#### Acceptance Criteria

1. THE Game_Balance_Config SHALL include a resource_respawn_ticks setting (positive integer) defining how many Game_Ticks must pass before a depleted room's gatherable resource resets to 1.
2. ON EACH Game_Tick, THE Game_Server SHALL decrement a respawn counter for each depleted room (gatherable_resource == 0) and restore gatherable_resource to 1 when the counter reaches zero.
3. WHEN a room contains a Resource_Building, THE Game_Server SHALL NOT apply the respawn timer to that room (Resource_Buildings handle production independently).
4. THE default resource_respawn_ticks value SHALL be 30 (5 minutes at 10-second ticks).

### Requirement 20: ANSI Color Formatting

**User Story:** As a player, I want different message types displayed in distinct colors, so that I can quickly distinguish combat alerts from chat messages and system notifications.

#### Acceptance Criteria

1. THE ANSI_Formatter SHALL apply color codes to output messages based on message category: combat (red), chat (cyan), system/notification (yellow), room description (green), error (bright red), and prompt (white).
2. THE Server_Config SHALL include an ansi_colors setting (boolean, defaulting to true) that enables or disables ANSI color output globally.
3. WHEN ansi_colors is disabled, THE ANSI_Formatter SHALL output plain text without any escape codes.
4. THE ANSI_Formatter SHALL use standard ANSI SGR escape sequences compatible with common telnet clients (PuTTY, Windows Telnet, Linux terminal).

### Requirement 21: Trust Level System

**User Story:** As a server operator, I want a trust level system that controls access to administrative commands, so that I can grant escalating privileges to trusted players without giving everyone full admin access.

#### Acceptance Criteria

1. THE Player_Database SHALL store a trust_level integer field for each player, defaulting to 1 for new accounts.
2. THE Trust_Level system SHALL define 5 levels: Level 1 (regular player), Level 2 (moderator — can mute/warn players), Level 3 (game master — can teleport, spawn items, view any player stats), Level 4 (senior admin — can kick players, modify trust levels up to level 3, hot-reload data files), Level 5 (server owner — full access including shutdown, modify any trust level, direct database access).
3. EACH Admin_Command SHALL specify a minimum_trust_level in its Command_Definition.
4. WHEN a player issues an Admin_Command and the player's Trust_Level is below the command's minimum_trust_level, THE Game_Server SHALL reject the command with a message indicating insufficient privileges.
5. THE Trust_Level for a player SHALL only be modifiable by a player with a strictly higher Trust_Level than the target player's current level.

### Requirement 22: Admin Commands

**User Story:** As an administrator, I want commands to manage the server and players, so that I can moderate gameplay and handle operational tasks.

#### Acceptance Criteria

1. THE Game_Server SHALL provide a "kick" Admin_Command (minimum_trust_level 4) that disconnects a target player by username, saves their state, and sends a Global_Notification.
2. THE Game_Server SHALL provide a "broadcast" Admin_Command (minimum_trust_level 2) that sends a server-wide message prefixed with "[ADMIN]".
3. THE Game_Server SHALL provide a "teleport" Admin_Command (minimum_trust_level 3) that moves the issuing player or a target player to specified coordinates.
4. THE Game_Server SHALL provide a "give" Admin_Command (minimum_trust_level 3) that adds a specified quantity of a resource to a target player's inventory.
5. THE Game_Server SHALL provide a "setlevel" Admin_Command (minimum_trust_level 4) that changes a target player's Trust_Level, subject to the restriction in Requirement 21 Acceptance Criterion 5.
6. THE Game_Server SHALL provide a "inspect" Admin_Command (minimum_trust_level 3) that displays all stats and inventory of a target player.
7. ALL Admin_Commands SHALL be defined in the commands Data_File with their minimum_trust_level specified.

### Requirement 23: Hot-Reload of Data Files

**User Story:** As a server operator, I want to reload game definition files at runtime without restarting the server, so that balance changes and new content can be applied immediately.

#### Acceptance Criteria

1. THE Game_Server SHALL provide a "reload" Admin_Command (minimum_trust_level 4) that triggers a Hot_Reload of all Data_Files.
2. WHEN a Hot_Reload is triggered, THE Data_Loader SHALL re-read and re-validate all Data_Files from disk.
3. IF all Data_Files pass validation, THE Data_Loader SHALL replace the current centralized registry contents with the newly loaded data and log a success message.
4. IF any Data_File fails validation during Hot_Reload, THE Data_Loader SHALL reject the entire reload, keep the current data intact, and report the validation errors to the player who issued the command.

### Requirement 24: Structured Logging

**User Story:** As a server operator, I want structured JSON log output, so that logs can be easily parsed and aggregated by monitoring tools.

#### Acceptance Criteria

1. THE Server_Config SHALL include a log_format setting with values "text" (default, human-readable) or "json" (structured JSON lines).
2. WHEN log_format is "json", THE Game_Server SHALL output each log entry as a single JSON object containing: timestamp, level, logger name, message, and any additional context fields (player username, command, event type).
3. WHEN log_format is "text", THE Game_Server SHALL output logs in the current human-readable format.
4. THE Game_Server SHALL log the following events with structured context: player login, player logout, command executed, combat action, building constructed, building destroyed, rank change, and server lifecycle events (startup, shutdown, tick error).

### Requirement 25: Server Metrics

**User Story:** As a server operator, I want to monitor server health metrics, so that I can detect performance issues and track usage.

#### Acceptance Criteria

1. THE Game_Server SHALL track the following metrics: connected_players (gauge), commands_processed (counter), tick_duration_ms (histogram), player_logins (counter), player_logouts (counter), and errors (counter).
2. THE Server_Config SHALL include a metrics_enabled setting (boolean, defaulting to false) and a metrics_log_interval setting (integer seconds, defaulting to 60).
3. WHEN metrics_enabled is true, THE Game_Server SHALL log a metrics summary at the configured interval containing all tracked metric values.
4. THE metrics system SHALL be lightweight and SHALL NOT add more than 1ms of overhead per Game_Tick.

### Requirement 26: World Chunking

**User Story:** As a server operator, I want the server to only process active regions of the world during each tick, so that large worlds don't cause tick slowdowns.

#### Acceptance Criteria

1. THE Game_Server SHALL divide the world grid into rectangular World_Chunks of configurable size (defaulting to 10x10 rooms per chunk).
2. A World_Chunk SHALL be considered active if at least one online player is located within the chunk or within one chunk radius of it.
3. DURING each Game_Tick, THE Game_Server SHALL only process resource production, equipment production, and turret attacks for rooms within active World_Chunks.
4. WHEN a player moves into an inactive World_Chunk, THE Game_Server SHALL activate that chunk and its neighbors.
5. THE World_Chunk size SHALL be configurable via the Server_Config.

### Requirement 27: Async Database Access

**User Story:** As a developer, I want database operations to be non-blocking, so that saving or loading player data does not stall the game loop.

#### Acceptance Criteria

1. THE Player_Database SHALL use aiosqlite (or equivalent async SQLite wrapper) for all database operations.
2. ALL Player_Database methods (save_player, load_player, load_all_players, player_exists) SHALL be async and SHALL NOT block the asyncio event loop.
3. THE Player_Database SHALL maintain a single connection pool that is reused across operations.

### Requirement 28: Connection Rate Limiting

**User Story:** As a server operator, I want to limit the rate of incoming connections, so that the server is protected from connection flooding.

#### Acceptance Criteria

1. THE Server_Config SHALL include a max_connections_per_minute setting (positive integer, defaulting to 30) and a max_concurrent_connections setting (positive integer, defaulting to 100).
2. WHEN the number of new connections in the last 60 seconds exceeds max_connections_per_minute, THE TelnetServer SHALL reject new connections with a brief message and close them immediately.
3. WHEN the number of concurrent active sessions reaches max_concurrent_connections, THE TelnetServer SHALL reject new connections with a brief message and close them immediately.
4. THE TelnetServer SHALL log each rejected connection with the reason.

### Requirement 29: Periodic World and Player Saves

**User Story:** As a server operator, I want player and world state saved periodically, so that a server crash doesn't lose significant progress.

#### Acceptance Criteria

1. THE Server_Config SHALL include a save_interval_ticks setting (positive integer, defaulting to 30, meaning every 5 minutes at 10-second ticks).
2. EVERY save_interval_ticks Game_Ticks, THE Game_Server SHALL save all connected player states and the world state to the database/disk.
3. THE periodic save SHALL run asynchronously and SHALL NOT block the Game_Tick processing.
4. IF a periodic save fails, THE Game_Server SHALL log the error and retry on the next save interval.

### Requirement 30: Player Save Debouncing

**User Story:** As a developer, I want player saves batched together, so that rapid disconnects or state changes don't cause excessive database writes.

#### Acceptance Criteria

1. THE Player_Database SHALL support a batch_save method that accepts a list of Player objects and persists them in a single database transaction.
2. WHEN multiple players disconnect within a short window (e.g., server shutdown), THE Game_Server SHALL use batch_save instead of individual save calls.
3. THE batch_save method SHALL be atomic — either all players are saved or none are, with the error logged.

### Requirement 31: Event Bus

**User Story:** As a developer, I want game systems to communicate through an event bus, so that adding new reactions to game events doesn't require modifying existing systems.

#### Acceptance Criteria

1. THE Game_Server SHALL provide an Event_Bus that supports publishing named events with arbitrary data payloads.
2. THE Event_Bus SHALL support subscribing handler functions to specific event names.
3. THE Event_Bus SHALL deliver events to all subscribed handlers asynchronously.
4. THE following events SHALL be published: player_login, player_logout, player_moved, player_eliminated, building_constructed, building_destroyed, building_upgraded, rank_promoted, rank_demoted, command_executed, and tick_completed.
5. EXISTING notification, XP award, and building state logic SHALL be refactored to use Event_Bus subscriptions instead of direct method calls where practical.

### Requirement 32: Command Plugin Architecture

**User Story:** As a developer, I want each command implemented as a self-contained plugin module, so that adding new commands doesn't require modifying a central registration method.

#### Acceptance Criteria

1. THE Game_Server SHALL define a Command_Plugin interface that each command module implements, specifying: command name, handler function, and optional setup/teardown hooks.
2. THE Game_Server SHALL auto-discover Command_Plugin modules from a designated directory (e.g., `src/commands/`).
3. EACH Command_Plugin SHALL register itself with the Command_Dispatcher during discovery without requiring changes to the Game class or server.py.
4. THE existing `_register_handlers` method in the Game class SHALL be replaced by the plugin auto-discovery mechanism.

### Requirement 33: MCCP2 Compression

**User Story:** As a player on a slow connection, I want the server to compress outgoing data using the MUD Client Compression Protocol, so that bandwidth usage is reduced by 70-90% and the game feels more responsive.

#### Acceptance Criteria

1. WHEN a client connects, THE TelnetServer SHALL offer MCCP2 compression by sending IAC WILL COMPRESS2 (telnet option 86) during the initial telnet negotiation.
2. WHEN a client responds with IAC DO COMPRESS2, THE TelnetServer SHALL send the subnegotiation sequence IAC SB COMPRESS2 IAC SE, then begin compressing all subsequent outgoing data using a zlib compression stream.
3. WHEN a client responds with IAC DONT COMPRESS2 or does not respond to the offer, THE TelnetServer SHALL continue sending uncompressed data as normal.
4. THE compression stream SHALL use Python's built-in `zlib.compressobj()` with default compression level and flush each write with `Z_SYNC_FLUSH` to ensure data is delivered promptly without waiting for buffer fill.
5. THE Server_Config SHALL include an mccp_enabled setting (boolean, defaulting to true) that allows the server operator to globally disable MCCP2 negotiation.
6. WHEN mccp_enabled is false, THE TelnetServer SHALL NOT offer COMPRESS2 to any client.
7. THE Session object SHALL track whether MCCP2 is active for each connection, and the `send()` method SHALL route outgoing bytes through the zlib compressor only when MCCP2 has been negotiated for that session.
8. IF the compression stream encounters an error, THE TelnetServer SHALL log the error, disable compression for that session, and continue sending uncompressed data.
