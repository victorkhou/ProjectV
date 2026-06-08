# Requirements Document

## Introduction

This feature consolidates scattered admin and game commands into unified noun+verb subcommand patterns. Ten admin commands are replaced by four grouped commands (`@building`, `@agent`, `@resource`, `@player`), and six game-facing agent commands are replaced by one unified `agent` command with subcommands. Old standalone command classes are deleted. No game logic changes — this is a pure UX/interface refactoring.

## Glossary

- **Command_Router**: A top-level Evennia command class that parses the first argument as a subcommand verb and dispatches to the appropriate handler method.
- **Subcommand**: The first positional argument after the command key that selects which action to perform (e.g., `spawn` in `@building spawn HQ`).
- **Admin_Command**: A command restricted to Builder+ or Admin+ permission level, prefixed with `@`.
- **Game_Command**: A command available to all players, using the `GameCommand` base class with prefix matching.
- **Agent_System**: The existing game system that manages agent NPCs (creation, assignment, patrol, training, stopping).
- **Caller**: The player character object that invoked the command.
- **Permission_Level**: The Evennia permission hierarchy — Player < Helper < Builder < Admin < Developer.

## Requirements

### Requirement 1: Admin Building Command Router

**User Story:** As an admin, I want a single `@building` command with `spawn` and `destroy` subcommands, so that building management is grouped under one intuitive command.

#### Acceptance Criteria

1. WHEN the Caller types `@building spawn <type> [owner=<name>] [level=<N>]`, THE Command_Router SHALL delegate to the existing building spawn logic and create the building at the Caller's current tile.
2. WHEN the Caller types `@building destroy`, THE Command_Router SHALL remove the building at the Caller's current tile coordinates without refunding resources (admin override). IF multiple buildings exist at the tile, THE Command_Router SHALL remove the first one found.
3. WHEN the Caller types `@building` with no Subcommand, THE Command_Router SHALL display help text listing all available subcommands (`spawn`, `destroy`).
4. WHEN the Caller types `@building <invalid_subcommand>`, THE Command_Router SHALL display an error message listing the valid subcommands.
5. THE Command_Router SHALL require Builder+ Permission_Level for the `@building` command.

### Requirement 2: Admin Agent Command Router

**User Story:** As an admin, I want a single `@agent` command with `create`, `destroy`, and `list` subcommands, so that admin agent management is grouped under one command.

#### Acceptance Criteria

1. WHEN the Caller types `@agent create <player> [count]`, THE Command_Router SHALL delegate to the existing agent creation logic, bypassing cost and timer.
2. WHEN the Caller types `@agent destroy <id> <player>`, THE Command_Router SHALL delegate to the existing agent destruction logic for the specified agent belonging to the target player.
3. WHEN the Caller types `@agent destroy training <player>`, THE Command_Router SHALL delegate to the existing training-state clearing logic for the target player.
4. WHEN the Caller types `@agent list <player>`, THE Command_Router SHALL delegate to the existing admin agent listing logic for the target player.
5. WHEN the Caller types `@agent` with no Subcommand, THE Command_Router SHALL display help text listing all available subcommands (`create`, `destroy`, `list`).
6. WHEN the Caller types `@agent <invalid_subcommand>`, THE Command_Router SHALL display an error message listing the valid subcommands.
7. THE Command_Router SHALL require Admin+ Permission_Level for the `create` and `destroy` subcommands.
8. THE Command_Router SHALL require Builder+ Permission_Level for the `list` subcommand.

### Requirement 3: Admin Resource Command Router

**User Story:** As an admin, I want a single `@resource` command with `give` and `reset` subcommands, so that resource management is grouped under one command.

#### Acceptance Criteria

1. WHEN the Caller types `@resource give <type> <amount> [player]`, THE Command_Router SHALL delegate to the existing resource-giving logic for the target player.
2. WHEN the Caller types `@resource reset [player]`, THE Command_Router SHALL delegate to the existing resource-reset logic.
3. WHEN the Caller types `@resource` with no Subcommand, THE Command_Router SHALL display help text listing all available subcommands (`give`, `reset`).
4. WHEN the Caller types `@resource <invalid_subcommand>`, THE Command_Router SHALL display an error message listing the valid subcommands.
5. THE Command_Router SHALL require Builder+ Permission_Level for the `give` subcommand.
6. THE Command_Router SHALL require Admin+ Permission_Level for the `reset` subcommand.

### Requirement 4: Admin Player Command Router

**User Story:** As an admin, I want a single `@player` command with `level` and `rank` subcommands, so that player attribute management is grouped under one command.

#### Acceptance Criteria

1. WHEN the Caller types `@player level <N> [player]`, THE Command_Router SHALL delegate to the existing level-setting logic for the target player.
2. WHEN the Caller types `@player rank <N> [player]`, THE Command_Router SHALL delegate to the existing rank-setting logic for the target player.
3. WHEN the Caller types `@player` with no Subcommand, THE Command_Router SHALL display help text listing all available subcommands (`level`, `rank`).
4. WHEN the Caller types `@player <invalid_subcommand>`, THE Command_Router SHALL display an error message listing the valid subcommands.
5. THE Command_Router SHALL require Admin+ Permission_Level for the `@player` command.

### Requirement 5: Game Agent Command Router

**User Story:** As a player, I want a single `agent` command with subcommands for all agent actions, so that agent management has a consistent and discoverable interface.

#### Acceptance Criteria

1. WHEN the Caller types `agent list`, THE Command_Router SHALL delegate to the existing agent listing logic showing the Caller's own agents.
2. WHEN the Caller types `agent assign <id> [role]`, THE Command_Router SHALL delegate to the existing agent assignment logic.
3. WHEN the Caller types `agent unassign <id>`, THE Command_Router SHALL delegate to the existing agent unassignment logic.
4. WHEN the Caller types `agent train`, THE Command_Router SHALL delegate to the existing agent training logic.
5. WHEN the Caller types `agent patrol <id> <waypoints...>`, THE Command_Router SHALL delegate to the existing patrol route-setting logic.
6. WHEN the Caller types `agent patrol <id> clear`, THE Command_Router SHALL delegate to the existing patrol route-clearing logic.
7. WHEN the Caller types `agent stop <id>`, THE Command_Router SHALL delegate to the existing agent stop logic.
8. WHEN the Caller types `agent` with no Subcommand, THE Command_Router SHALL display help text listing all available subcommands (`list`, `assign`, `unassign`, `train`, `patrol`, `stop`).
9. WHEN the Caller types `agent <invalid_subcommand>`, THE Command_Router SHALL display an error message listing the valid subcommands.
10. THE Command_Router SHALL use the Game_Command base class to inherit prefix matching behavior.

### Requirement 6: Subcommand Dispatch Pattern

**User Story:** As a developer, I want a consistent dispatch pattern for all Command_Router classes, so that adding new subcommands is straightforward and the codebase stays uniform.

#### Acceptance Criteria

1. THE Command_Router SHALL parse the first whitespace-delimited token of the argument string as the Subcommand verb.
2. THE Command_Router SHALL pass the remaining argument string (after the Subcommand verb) to the handler method.
3. WHEN the Subcommand verb matches a registered handler, THE Command_Router SHALL invoke that handler.
4. WHEN the Subcommand verb does not match any registered handler, THE Command_Router SHALL display an error message that includes the list of valid subcommands.
5. WHEN no Subcommand verb is provided, THE Command_Router SHALL display help text that lists each Subcommand with a brief description.
6. THE Command_Router SHALL perform case-insensitive matching on the Subcommand verb.

### Requirement 7: Command Registration Update

**User Story:** As a developer, I want the command set registration to reflect the new consolidated commands, so that the old standalone classes are replaced cleanly.

#### Acceptance Criteria

1. THE CharacterCmdSet SHALL register the new `@building`, `@agent`, `@resource`, and `@player` Admin_Command routers in place of the ten replaced standalone commands.
2. THE CharacterCmdSet SHALL register the new `agent` Game_Command router in place of the six replaced standalone agent commands.
3. THE CharacterCmdSet SHALL continue to register standalone commands that are not consolidated (`@teleport`, `@reloaddata`, `@clearfog`, `@purgerooms`, `@migrate`).
4. THE old standalone command classes (`CmdSpawnBuilding`, `CmdCreateAgent`, `CmdDestroyAgent`, `CmdListAgents`, `CmdGiveResource`, `CmdResetResources`, `CmdSetLevel`, `CmdSetRank`, `CmdAgents`, `CmdAssign`, `CmdUnassign`, `CmdTrain`, `CmdPatrol`, `CmdStopAgent`) SHALL be deleted.

### Requirement 8: Admin Action Logging

**User Story:** As an admin, I want all consolidated admin commands to continue logging operator actions, so that the audit trail is preserved.

#### Acceptance Criteria

1. WHEN an Admin_Command Subcommand executes successfully, THE Command_Router SHALL log the operator name, the full command (including Subcommand), and the target to the admin logger.
2. WHEN an Admin_Command Subcommand is denied due to insufficient Permission_Level, THE Command_Router SHALL display a permission-denied message to the Caller.
