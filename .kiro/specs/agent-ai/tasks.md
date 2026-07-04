# Implementation Plan: Agent AI — Pathfinding & Autonomous Movement

## Overview

Implement grid-based A* pathfinding and tick-driven NPC movement, then layer patrol and delivery behaviors on top. Built in five phases: standalone Pathfinder module, NPC movement engine with MovementSystem, behavior scripts (PatrolBehavior + DeliveryBehavior), AgentSystem integration with player commands, and final wiring/testing.

## Tasks

- [x] 1. Implement Pathfinder module
  - [x] 1.1 Create `world/pathfinding.py` with `find_path` function
    - Implement A* algorithm with Manhattan distance heuristic
    - Accept `start`, `goal`, `is_passable` callback, `width`, `height`, `max_nodes` parameters
    - Return ordered list of (x, y) from start (exclusive) to goal (inclusive)
    - Use 4-directional adjacency (N/S/E/W)
    - Return empty list when: start == goal, no path exists, node limit exceeded, goal impassable
    - Enforce bounds checking in neighbor generation (0 <= x < width, 0 <= y < height)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 6.1, 6.2_

  - [x] 1.2 Write property tests for Pathfinder (Properties 1–4)
    - **Property 1: Path Adjacency Invariant** — every consecutive pair differs by exactly 1 in x or y
    - **Validates: Requirements 1.1, 1.8**
    - **Property 2: Path Validity Invariant** — all coordinates in-bounds and passable
    - **Validates: Requirements 1.2, 1.6**
    - **Property 3: Same-Coordinate Identity** — find_path(p, p, ...) returns empty list
    - **Validates: Requirements 1.5**
    - **Property 4: Open-Terrain Optimality** — path length equals Manhattan distance on fully passable grid
    - **Validates: Requirements 1.7**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

  - [x] 1.3 Create `make_passability_checker` factory in `world/pathfinding.py`
    - Accept TerrainGenerator, DataRegistry, PlanetRoom, width, height
    - Check terrain passability via TerrainGenerator
    - Check for offline buildings via `PlanetRoom.get_buildings_at` (O(1) lookup)
    - Return `is_passable(x, y) -> bool` callable
    - _Requirements: 6.4, 6.5_

- [x] 2. Checkpoint — Verify Pathfinder
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Implement NPC Movement Engine
  - [x] 3.1 Add movement attributes and methods to `NPC` typeclass in `typeclasses/npcs.py`
    - Add persistent attributes: `db.movement_queue` (list, default []), `db.movement_delay` (int, default 1), `db.activity_status` (str, default "Idle")
    - Implement `advance_movement(tick_number)` — advance one step if `tick_number % movement_delay == 0`, call `PlanetRoom.move_entity`, clear queue and call `at_movement_complete` when done
    - Implement `set_movement_queue(path)` — replace queue, register with MovementSystem
    - Implement `clear_movement()` — clear queue, unregister from MovementSystem
    - Implement `at_movement_complete()` — no-op hook for subclass overrides
    - Halt and clear queue if next tile is impassable (dynamic obstacle detection)
    - Skip movement if NPC is incapacitated
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 2.8, 2.9, 5.1, 8.1, 8.2, 8.6, 8.7_

  - [x] 3.2 Write property tests for movement engine (Properties 5–7)
    - **Property 5: Movement Queue Consumption** — after N ticks, NPC at final coordinate, queue empty, at_movement_complete invoked once
    - **Validates: Requirements 2.1, 2.2**
    - **Property 6: Incapacitated NPC Freezes Movement** — no position change, no queue consumption
    - **Validates: Requirements 2.4**
    - **Property 7: Movement Delay Gating** — NPC advances only on ticks where tick_number % delay == 0
    - **Validates: Requirements 8.1, 8.6**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

  - [x] 3.3 Create `MovementSystem` in `world/systems/movement_system.py`
    - Maintain in-memory `_moving_npcs` set (same pattern as `agent_system._training_buildings`)
    - Implement `register_moving(npc)` and `unregister_moving(npc)`
    - Implement `process_movement(tick_number)` — iterate `_moving_npcs`, call `advance_movement`, remove NPCs with empty queues
    - Implement `_ensure_initialized()` — lazy rebuild from DB on first access after restart
    - Implement pathfinding throttle: `request_path`, `process_pathfinding`, `reset_tick` with configurable `max_paths_per_tick` (default 10)
    - Define `PathRequest` dataclass
    - _Requirements: 2.1, 5.4, 6.3_

  - [x] 3.4 Write property test for pathfinding throttle (Property 15)
    - **Property 15: Pathfinding Throttle** — at most max_paths_per_tick processed, remainder deferred
    - **Validates: Requirements 6.3**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

- [x] 4. Checkpoint — Verify Movement Engine
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Add movement constants to `world/constants.py`
  - Add: `DEFAULT_MOVEMENT_DELAY`, `SCOUT_MOVEMENT_DELAY`, `HARVESTER_LADEN_DELAY`, `HARVESTER_EMPTY_DELAY`, `MAX_PATHFINDING_NODES`, `MAX_PATHS_PER_TICK`, `MIN_PATROL_WAYPOINTS`, `MAX_PATROL_WAYPOINTS`, `DEFAULT_CARRY_CAPACITY`
  - _Requirements: 8.1, 8.3, 8.4, 8.5, 9.1, 3.7, 6.2, 6.3_

- [x] 6. Implement Behavior Scripts
  - [x] 6.1 Implement `PatrolBehavior` script in `typeclasses/agent_scripts.py`
    - Replace `GuardScript` and `ScoutScript` placeholder classes
    - Use polling pattern: `at_repeat` checks if `movement_queue` is empty, then advances `patrol_waypoint_index` and requests path to next waypoint via Pathfinder
    - Cycle waypoint index: `(i + 1) % len(patrol_route)`
    - Skip unreachable waypoints, retry all-unreachable next tick
    - Update `activity_status` on state changes (e.g., "Patrolling waypoint 2/5")
    - NPC attributes used: `db.patrol_route`, `db.patrol_waypoint_index`
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 10.1, 10.2_

  - [x] 6.2 Write property tests for patrol (Properties 8–9)
    - **Property 8: Patrol Waypoint Cycling** — after arriving at waypoint i, next target is (i+1) % W
    - **Validates: Requirements 3.2, 3.3**
    - **Property 9: Patrol Route Validation** — accepted iff 2 <= len <= 10 and all waypoints in bounds
    - **Validates: Requirements 3.7, 3.8**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

  - [x] 6.3 Implement `DeliveryBehavior` script in `typeclasses/agent_scripts.py`
    - Implement delivery FSM: idle → picking_up → delivering → returning → idle
    - `at_repeat` polling pattern: check `delivery_state` and `movement_queue` emptiness
    - `_try_pick_up`: check for ResourceDrops at Extractor coords, load up to `carry_capacity`
    - `_start_delivery`: select nearest Storage_Building, path to it, set `delivery_state = "delivering"`, set `movement_delay = HARVESTER_LADEN_DELAY`
    - `_deposit_and_return`: transfer `carried_resources` to owner's resource pool, path back to Extractor, set `delivery_state = "returning"`, set `movement_delay = HARVESTER_EMPTY_DELAY`
    - `_arrived_at_extractor`: transition to idle
    - `select_delivery_target`: prefer nearest Vault/HQ by Manhattan distance, prefer Vault over HQ on tie
    - Handle edge cases: no storage building (stay idle), path blocked (retry next tick), incapacitated (drop resources)
    - Update `activity_status` on state changes (e.g., "Delivering 15 Iron to Vault (3 tiles)")
    - NPC attributes used: `db.delivery_state`, `db.carried_resources`, `db.carry_capacity`, `db.delivery_target`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 7.1, 7.2, 7.3, 7.4, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 10.1, 10.2_

  - [x] 6.4 Write property tests for delivery (Properties 10–13)
    - **Property 10: Capacity-Limited Resource Pickup** — picks up min(T, C), leaves max(0, T-C) on ground
    - **Validates: Requirements 4.2, 9.2**
    - **Property 11: Resource Deposit Round-Trip** — player pool increases by carried amounts, carried_resources becomes empty dict
    - **Validates: Requirements 4.4, 9.4**
    - **Property 12: Delivery Target Selection** — nearest Storage_Building by Manhattan distance, Vault preferred on tie
    - **Validates: Requirements 7.1, 7.2**
    - **Property 13: Harvester Delay by Delivery State** — delay=2 when delivering, delay=1 when returning/idle
    - **Validates: Requirements 8.4, 8.5**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

  - [x] 6.5 Gate `HarvesterScript.at_repeat` by `delivery_state`
    - Add check at top of `HarvesterScript.at_repeat`: only produce when `delivery_state` is `"idle"` or `"picking_up"`
    - When `delivery_state` is `"delivering"` or `"returning"`, skip production (harvester in transit)
    - _Requirements: 4.8_

  - [x] 6.6 Update `ROLE_SCRIPT_MAP` in `typeclasses/agent_scripts.py`
    - Remove `GuardScript` and `ScoutScript` classes
    - Map `"guard"` → `PatrolBehavior`, `"scout"` → `PatrolBehavior`
    - Map `"harvester"` → `[HarvesterScript, DeliveryBehavior]` (list of scripts)
    - _Requirements: 3.1, 4.1_

- [x] 7. Checkpoint — Verify Behavior Scripts
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. AgentSystem integration and player commands
  - [x] 8.1 Update `_attach_behavior_script` in `agent_system.py` to handle list values in `ROLE_SCRIPT_MAP`
    - When a role maps to a list of script classes, attach all scripts in the list
    - _Requirements: 4.1_

  - [x] 8.2 Update `assign_agent` in `agent_system.py` to path instead of teleport
    - Compute path from agent's current position to building coordinates using Pathfinder
    - Set `movement_queue` via `NPC.set_movement_queue` instead of direct coordinate assignment
    - For harvesters, initialize `delivery_state = "idle"`, `carried_resources = {}`, `carry_capacity = DEFAULT_CARRY_CAPACITY`
    - _Requirements: 2.6, 4.1, 9.1, 9.3_

  - [x] 8.3 Update `unassign_agent` in `agent_system.py` to path back to HQ
    - Clear current movement queue, compute path to HQ, set new movement queue
    - Clear patrol route and delivery state
    - _Requirements: 3.6, 11.2_

  - [x] 8.4 Add `set_patrol_route(player, agent_id, waypoints)` to `AgentSystem`
    - Validate: agent exists, role is guard or scout, 2–10 waypoints, all within planet bounds
    - Store `patrol_route` and reset `patrol_waypoint_index` to 0 on the agent
    - _Requirements: 3.1, 3.7, 3.8_

  - [x] 8.5 Add `clear_patrol_route(player, agent_id)` to `AgentSystem`
    - Clear `patrol_route`, `patrol_waypoint_index`, and `movement_queue`
    - _Requirements: 3.6_

  - [x] 8.6 Add `stop_agent(player, agent_id)` to `AgentSystem`
    - Clear movement queue, set activity_status to "Idle"
    - Retain carried resources if harvester
    - _Requirements: 11.1, 11.3, 11.4_

  - [x] 8.7 Write property tests for reassignment and cancellation (Properties 16–17)
    - **Property 16: Reassignment Clears and Replaces Queue** — old queue replaced with new path from current position
    - **Validates: Requirements 11.2**
    - **Property 17: Cancellation Retains Carried Resources** — carried_resources unchanged after cancellation
    - **Validates: Requirements 11.4**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

  - [x] 8.8 Implement `CmdPatrol` command in `commands/agent_commands.py`
    - Usage: `patrol <agent_id> <x1>,<y1> <x2>,<y2> ...` and `patrol <agent_id> clear`
    - Parse waypoints, call `agent_system.set_patrol_route` or `clear_patrol_route`
    - _Requirements: 3.1, 3.6, 3.7, 3.8_

  - [x] 8.9 Implement `CmdStopAgent` command in `commands/agent_commands.py`
    - Usage: `stopagent <agent_id>`
    - Call `agent_system.stop_agent`
    - _Requirements: 11.1_

  - [x] 8.10 Update `CmdAgents` output to include `activity_status`
    - Show activity_status after each agent's role/location info
    - _Requirements: 10.3_

  - [x] 8.11 Register `CmdPatrol` and `CmdStopAgent` in `CharacterCmdSet` in `commands/default_cmdsets.py`
    - Import and add both commands to `at_cmdset_creation`
    - _Requirements: 3.1, 11.1_

- [x] 9. Wire MovementSystem into GameTickScript
  - [x] 9.1 Add `npc_movement` step to `_build_tick_steps` in `typeclasses/scripts.py`
    - Insert after `active_chunks`, before `agent_processing`
    - Call `movement_system.process_movement(tick_number)` and `movement_system.process_pathfinding()`
    - Call `movement_system.reset_tick()` at start of tick
    - _Requirements: 2.1, 6.3_

  - [x] 9.2 Register MovementSystem in game initialization
    - Create MovementSystem instance and add to `game_systems` dict
    - Wire NPC.set_movement_queue / clear_movement to call MovementSystem register/unregister
    - _Requirements: 2.1, 5.4_

  - [x] 9.3 Write property test for equipment speed modifier (Property 14)
    - **Property 14: Equipment Speed Modifier** — effective delay = max(1, base_delay - modifier)
    - **Validates: Requirements 8.8**
    - Test file: `mygame/tests/test_prop_agent_ai.py`

- [x] 10. Checkpoint — Verify full integration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Write unit tests for edge cases
  - Test file: `mygame/tests/test_agent_ai_unit.py`
  - Pathfinder edge cases: walled goal, node limit exceeded, single-tile grid
  - Dynamic obstacle detection: tile becomes impassable mid-movement
  - Patrol: unreachable waypoint skipping, all-unreachable retry, route clear during transit
  - Delivery: no storage building, storage destroyed mid-delivery, incapacitation resource drop
  - Activity status string updates on state transitions
  - Default attribute values (movement_delay=1, carry_capacity=50)
  - _Requirements: 1.3, 1.4, 2.3, 2.8, 3.4, 3.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.5, 9.5, 10.1, 10.2_

- [x] 12. Write integration tests
  - Test file: `mygame/tests/test_integration_agent_ai.py`
  - Full delivery loop: harvester cycles Extractor → Vault → Extractor over multiple ticks
  - Full patrol loop: guard cycles through waypoints and wraps
  - GameTickScript integration: movement step executes in correct order
  - Server restart recovery: persisted state resumes correctly
  - AgentSystem.assign_agent paths instead of teleporting
  - Throttle: 15 path requests in one tick, only 10 processed
  - _Requirements: 2.1, 3.2, 3.3, 4.1, 4.4, 5.4, 5.5, 6.3_

- [x] 13. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The Pathfinder (task 1) is pure Python with zero Evennia dependencies — testable in isolation
- MovementSystem uses the same in-memory tracking pattern as `agent_system._training_buildings`
- PatrolBehavior replaces GuardScript/ScoutScript in ROLE_SCRIPT_MAP
- DeliveryBehavior coexists with HarvesterScript; production gated by delivery_state
- All behavior scripts use the polling pattern (check queue emptiness in at_repeat)
- movement_delay (not movement_speed) — higher value = slower movement

## TODOs (post-implementation cleanup)

### Resolved

- ~~**Refactor delivery states to a `StrEnum`**~~ — Done. `DeliveryState(StrEnum)` now lives in `world/constants.py` and is used across `agent_scripts.py` and `agent_system.py`. Stored string values remain compatible.
- ~~**Refactor activity status strings to constants or an enum**~~ — Partially done. `ACTIVITY_IDLE` is extracted to `world/constants.py` and used for the idle status in `npcs.py`, `agent_scripts.py`, and `agent_system.py`. The transient status strings (e.g. `"Blocked — waiting"`, `"Patrol blocked — retrying"`, `"Delivering …"`) remain inline by design — they embed runtime values and aren't compared anywhere, so a constant adds little. Revisit only if they become matched-on state.
- ~~**Integrate equipment speed modifier into `NPC.advance_movement`**~~ — Done. `compute_effective_delay` is now the production function in `world/constants.py`; `NPC.advance_movement` queries the shared `equipment` handler (now on `CombatEntity`) for the `move_speed` stat total and applies `effective_delay = max(1, base_delay - modifier)`. Property 14 imports the production function; new integration tests in `test_npc_movement.py` exercise the wired-up path.

### Newly identified (not yet addressed)

- **`ROLE_SCRIPT_MAP["harvester"]` does not attach `DeliveryBehavior`** — Spec task 6.6 specified `"harvester" → [HarvesterScript, DeliveryBehavior]`, but the live map binds only `HarvesterScript`. As a result the fully-implemented `DeliveryBehavior` FSM (pickup → deliver → return) is never attached to harvester agents, so harvested resources pile up at the Extractor and are never auto-delivered to a Vault/HQ. Either wire the list mapping (and confirm `AgentSystem._attach_behavior_script` handles lists — task 8.1 says it does) or, if drop-only harvesting is now the intended design, delete `DeliveryBehavior` and the related delivery tests/constants.
- **Stale `test_agent_scripts.py` tests (4 failures)** — `TestHarvesterScript::test_produces_resources_into_extractor_inventory`, `::test_production_accumulates`, `::test_reads_resource_type_from_building_attr`, and `TestRoleScriptMap::test_harvester_maps_to_list` assert pre-`coordinate-room-refactor` behavior. `HarvesterScript` now spawns `ResourceDrop`s at the Extractor's coordinates via `ResourceSystem._spawn_resource_drop` rather than filling a building `resource_inventory` dict, and `ROLE_SCRIPT_MAP["harvester"]` is a single class. Update these tests to match current behavior (or fold into the `DeliveryBehavior` wiring decision above). These failures pre-date this cleanup pass.
