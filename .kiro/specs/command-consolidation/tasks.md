# Implementation Plan: Command Consolidation

## Overview

Refactor the command layer to consolidate standalone admin and game agent commands into grouped noun+verb routers. Create a `SubcommandRouter` base class, then build five router subclasses that extract logic from existing standalone commands. Update command registration and delete old classes.

## Tasks

- [x] 1. Create SubcommandRouter base class and router bases
  - [x] 1.1 Create `mygame/commands/command_router.py` with `SubcommandRouter` base class
    - Implement `func()` dispatch: parse verb via `_get_subcommand_and_args()`, look up in `subcommands` dict, check permission via `_check_sub_perm()`, invoke handler
    - Implement `_show_help()` to list all subcommands with descriptions
    - Implement `_show_error()` to show invalid verb message with valid subcommand list
    - Implement `_check_sub_perm()` for per-subcommand permission checking
    - Implement `_log_admin()` for admin audit logging
    - Case-insensitive verb matching (lowercase the parsed verb)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 8.2_

  - [x] 1.2 Create `AdminSubcommandRouter` in `command_router.py`
    - Inherit `SubcommandRouter`
    - Set `help_category = "Admin"` and `locks = "cmd:perm(Builder);view:perm(Builder)"`
    - _Requirements: 1.5, 2.7, 2.8, 3.5, 3.6, 4.5_

  - [x] 1.3 Create `GameSubcommandRouter` in `command_router.py`
    - Inherit `GameCommand` (for prefix matching) and override `func()` with dispatch logic from `SubcommandRouter`
    - Set `help_category = "Game"`
    - Import `GameCommand` from `commands.game_commands`
    - _Requirements: 5.10, 6.1, 6.2, 6.3_

  - [x] 1.4 Write property tests for SubcommandRouter dispatch (test_command_router.py)
    - **Property 1: Subcommand dispatch correctness** — for any registered verb and any args string, `func()` invokes the correct handler with the remaining args
    - **Validates: Requirements 6.1, 6.2, 6.3**
    - **Property 2: Invalid subcommand error** — for any string not in the subcommands dict, `func()` produces an error containing all valid subcommand names
    - **Validates: Requirements 6.4**
    - **Property 3: Case-insensitive verb matching** — for any case variation of a registered verb, `func()` dispatches to the same handler
    - **Validates: Requirements 6.6**

  - [x] 1.5 Write unit tests for SubcommandRouter base behavior (test_command_router.py)
    - Test help display when no subcommand provided
    - Test permission denied message when `_check_sub_perm` fails
    - Test `_log_admin` writes to the admin logger
    - _Requirements: 6.5, 8.1, 8.2_

- [x] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Implement admin routers in admin_commands.py
  - [x] 3.1 Implement `CmdAdminBuilding` router (`@building`)
    - Create class inheriting `AdminSubcommandRouter` with `key = "@building"`
    - Define `subcommands` dict with `spawn` (Builder+) and `destroy` (Builder+)
    - Extract `sub_spawn` handler logic from existing `CmdSpawnBuilding.func()`
    - Implement `sub_destroy` handler: find building at caller's tile, delete without refund
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 3.2 Implement `CmdAdminAgent` router (`@agent`)
    - Create class inheriting `AdminSubcommandRouter` with `key = "@agent"`
    - Define `subcommands` dict with `create` (Admin+), `destroy` (Admin+), `list` (Builder+)
    - Extract `sub_create` handler logic from existing `CmdCreateAgent.func()`
    - Extract `sub_destroy` handler logic from existing `CmdDestroyAgent.func()` (including `training` variant)
    - Extract `sub_list` handler logic from existing `CmdListAgents.func()`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 3.3 Implement `CmdAdminResource` router (`@resource`)
    - Create class inheriting `AdminSubcommandRouter` with `key = "@resource"`
    - Define `subcommands` dict with `give` (Builder+) and `reset` (Admin+)
    - Extract `sub_give` handler logic from existing `CmdGiveResource.func()`
    - Extract `sub_reset` handler logic from existing `CmdResetResources.func()`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 3.4 Implement `CmdAdminPlayer` router (`@player`)
    - Create class inheriting `AdminSubcommandRouter` with `key = "@player"`
    - Define `subcommands` dict with `level` (Admin+) and `rank` (Admin+)
    - Extract `sub_level` handler logic from existing `CmdSetLevel.func()`
    - Extract `sub_rank` handler logic from existing `CmdSetRank.func()`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 3.5 Delete old standalone admin command classes from admin_commands.py
    - Remove `CmdSpawnBuilding`, `CmdCreateAgent`, `CmdDestroyAgent`, `CmdListAgents`, `CmdGiveResource`, `CmdResetResources`, `CmdSetLevel`, `CmdSetRank`
    - Keep `CmdReloadData`, `CmdTeleport`, `CmdClearFog`, `CmdPurgeRooms`, `CmdMigrate` unchanged
    - Keep helper functions `_check_perm`, `_get_registry` (used by remaining commands and router handlers)
    - _Requirements: 7.4_

  - [x] 3.6 Write unit tests for admin routers (test_admin_routers.py)
    - Test `@building spawn` delegates to spawn logic
    - Test `@building destroy` removes building at caller's tile
    - Test `@agent create`, `@agent destroy`, `@agent destroy training`, `@agent list` delegation
    - Test `@resource give` and `@resource reset` delegation
    - Test `@player level` and `@player rank` delegation
    - Test per-subcommand permission enforcement (e.g., `@agent list` = Builder+, `@agent create` = Admin+)
    - Test admin logging on successful actions
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.7, 2.8, 3.1, 3.2, 3.5, 3.6, 4.1, 4.2, 4.5, 8.1_

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement game agent router in agent_commands.py
  - [x] 5.1 Implement `CmdAgent` router (`agent`)
    - Create class inheriting `GameSubcommandRouter` with `key = "agent"`
    - Define `subcommands` dict with `list`, `assign`, `unassign`, `train`, `patrol`, `stop` (all no perm — game commands)
    - Extract `sub_list` handler logic from existing `CmdAgents.func()`
    - Extract `sub_assign` handler logic from existing `CmdAssign.func()`
    - Extract `sub_unassign` handler logic from existing `CmdUnassign.func()`
    - Extract `sub_train` handler logic from existing `CmdTrain.func()`
    - Extract `sub_patrol` handler logic from existing `CmdPatrol.func()` (including `clear` variant)
    - Extract `sub_stop` handler logic from existing `CmdStopAgent.func()`
    - Keep `_get_current_building` helper function
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10_

  - [x] 5.2 Delete old standalone agent command classes from agent_commands.py
    - Remove `CmdAgents`, `CmdAssign`, `CmdUnassign`, `CmdTrain`, `CmdPatrol`, `CmdStopAgent`
    - _Requirements: 7.4_

  - [x] 5.3 Write unit tests for game agent router (test_agent_router.py)
    - Test `agent list`, `agent assign`, `agent unassign`, `agent train` delegation
    - Test `agent patrol <id> <waypoints>` and `agent patrol <id> clear` delegation
    - Test `agent stop <id>` delegation
    - Test help display and invalid subcommand error
    - Test that `CmdAgent` inherits `GameCommand` (prefix matching)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10_

- [x] 6. Update command registration in default_cmdsets.py
  - [x] 6.1 Update `CharacterCmdSet.at_cmdset_creation()` imports and registration
    - Import `CmdAdminBuilding`, `CmdAdminAgent`, `CmdAdminResource`, `CmdAdminPlayer` from `commands.admin_commands`
    - Import `CmdAgent` from `commands.agent_commands`
    - Remove imports of deleted standalone classes
    - Replace old `self.add()` calls with new router registrations
    - Keep registrations for `CmdReloadData`, `CmdTeleport`, `CmdClearFog`, `CmdPurgeRooms`, `CmdMigrate`
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 6.2 Write unit tests for command registration (test_command_router.py)
    - Verify new routers are registered in CharacterCmdSet
    - Verify old standalone classes are not registered
    - Verify unchanged standalone commands are still registered
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [x] 7. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific handler delegation and permission enforcement
- All handler logic is extracted from existing standalone commands — no new game logic
