# Implementation Plan: Terrain Strategy

## Overview

Implementation follows the design's Migration and Rollout order exactly, with every field
neutral-by-default so the full suite stays green after each stage:

- **Stage A** — data definitions, validators, and balance fields (tasks 1–2)
- **Stage B** — TerrainModifierSystem and composition-root wiring (tasks 3–4)
- **Stage C** — consumers one at a time: fog of war, movement gate, combat engine,
  displays, placement feedback (tasks 5–10)
- **Stage D** — content: terrain.yaml modifiers, class affinities, terrain technologies
  (tasks 11–12)

Full-suite gate after each stage: `python -m pytest mygame -q` from the repo root, zero
failures/errors (2819 tests currently passing). All new/modified lines ≤100 chars (.flake8).

Property tests use Hypothesis with `@settings(max_examples=100)` or higher and the tag
comment `# Feature: terrain-strategy, Property N: <title>`. Registry-load properties follow
the `test_prop_hot_reload.py` tempfile pattern; runtime properties use fakes and
`services.override` — no Evennia database objects.

## Tasks

- [x] 1. Stage A: Data definitions, validators, and balance fields
  - [x] 1.1 Extend TerrainDef with modifier fields and load them in the registry
    - Add `vision_modifier: int = 0`, `movement_modifier: float = 0.0`,
      `defense_modifier: float = 0.0` to `TerrainDef` in `mygame/world/definitions.py`
    - In `_populate_terrain` (`mygame/world/data_registry.py`), read the three fields with
      missing/null → 0 semantics; when all three are missing/null, log a warning naming the
      terrain type and still load with zeros (the single warning-not-error case)
    - Expose the values through the existing registry terrain lookup so consumers never read
      terrain.yaml directly
    - _Requirements: 1.1, 1.2, 1.3, 1.6_

  - [x] 1.2 Add terrain modifier numeric validation to SchemaValidator
    - In `SchemaValidator.validate_terrain` (`mygame/world/schema_validator.py`), require each
      modifier field, when present and non-null, to be int or float and explicitly not bool
      (matching the existing `validate_ability_gates` pattern)
    - Append one error per offending field naming the terrain type and field into the shared
      error list so `load_all` raises a single `DataRegistryError` collecting all errors
    - _Requirements: 1.4, 1.5_

  - [x] 1.3 Write property test for terrain modifier load round-trip
    - **Property 1: Terrain modifier load round-trip with zero defaults**
    - New file `mygame/world/tests/test_prop_terrain_modifiers.py`; generate terrain yaml
      where each modifier field is independently a valid number, missing, or null; write into
      a tempfile tree seeded from the valid baseline fixtures (test_prop_hot_reload.py
      pattern); assert `load_all` succeeds and TerrainDef carries exact values with
      missing/null defaulted to zero
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.5, 1.6**

  - [x] 1.4 Write property test for non-numeric terrain modifiers failing fast
    - **Property 2: Non-numeric terrain modifiers fail fast, collectively and atomically**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; inject non-numeric values
      (bool, string, list) and assert `load_all` raises `DataRegistryError` naming every
      offending terrain type and field, and `reload_all` against such data fails while
      leaving currently loaded registry data unchanged
    - **Validates: Requirements 1.4**

  - [x] 1.5 Write unit test for the all-three-missing warning
    - Extend `mygame/world/tests/test_data_registry.py`: a terrain entry omitting all three
      modifier fields loads with zeros and logs exactly one warning naming the terrain type;
      no validation error is reported
    - _Requirements: 1.3_

  - [x] 1.6 Add terrain balance bounds and min vision radius to BalanceConfig
    - Add `terrain_vision_bound: int = 5`, `terrain_movement_bound: float = 3.0`,
      `terrain_defense_bound: float = 6.0`, `min_vision_radius: int = 1` to `BalanceConfig`
      in `mygame/world/definitions.py` (picked up by the generic `_build_balance` copy and
      `_BALANCE_INT_FIELDS`/`_BALANCE_FLOAT_FIELDS` validation)
    - Add all four fields to the validator's `non_negative_fields` list in
      `mygame/world/schema_validator.py` so negative or non-numeric values fail the load;
      defaults apply only to omitted fields
    - _Requirements: 9.1, 9.3, 9.4, 3.6_

  - [x] 1.7 Write unit tests for balance bound defaults and invalid bounds
    - Extend `mygame/world/tests/test_data_registry.py`: omitted bounds yield defaults
      (5 vision / 3.0 movement / 6.0 defense, `min_vision_radius` 1); non-numeric or negative
      bound values fail the load with an error naming the offending field
    - _Requirements: 9.1, 9.3, 9.4, 3.6_

  - [x] 1.8 Add ClassDef terrain affinities with fail-fast validation
    - Add frozen `TerrainAffinity(terrain_type, kind, adjustment)` dataclass and
      `terrain_affinities: list[TerrainAffinity]` (default `[]`) to `ClassDef` in
      `mygame/world/definitions.py`
    - In `_load_classes` (`mygame/world/data_registry.py`), parse the optional
      `terrain_affinities` list; validate per entry: terrain type exists in `self.terrain`,
      kind ∈ {vision, movement, defense}, adjustment numeric (non-bool); collect affinity
      errors across all classes and raise `DataRegistryError` when any exist, keeping the
      lenient contract for pre-existing class fields (contract escalation per design)
    - Enforce the sidegrade rule: any class with a positive-adjustment affinity must have a
      negative-adjustment affinity or a negative `stat_modifiers` value and a non-empty
      description, else a validation error names the class
    - _Requirements: 6.1, 6.5, 6.6_

  - [x] 1.9 Add terrain technology validation
    - In `validate_technologies` (`mygame/world/schema_validator.py`), validate
      `effect_type: terrain_affinity` entries: `effect_value` keys match
      `terrain_affinity:{terrain_type}:{kind}` with a valid kind and numeric (non-bool) value
    - Add the terrain-type existence check to the existing `cross_validate` step in
      `mygame/world/data_registry.py` (runs after all `_populate_*` calls)
    - Collect errors across all technology definitions and fail the load; no `TechnologyDef`
      structural change
    - _Requirements: 7.1, 7.5_

  - [x] 1.10 Write property test for affinity load round-trip
    - **Property 13: Affinity load round-trip**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; generate valid class affinity
      lists and terrain technology sets, load through a tempfile registry, assert
      `ClassDef.terrain_affinities` and `TechnologyDef.effect_value` read back equal the
      yaml input
    - **Validates: Requirements 6.1, 7.1**

  - [x] 1.11 Write property test for affinity validation failing fast
    - **Property 14: Affinity validation fails fast, collectively**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; generate class/technology sets
      with invalid affinity entries (unknown terrain, bad kind, non-numeric adjustment) or
      unbalanced positive-only classes; assert `load_all` raises `DataRegistryError`
      identifying every invalid entry and every unbalanced class
    - **Validates: Requirements 6.5, 6.6, 7.5**

- [x] 2. Checkpoint — Stage A green
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m pytest mygame -q` from the repo root; zero failures/errors. Shipped yaml
    files unchanged: expect only per-terrain all-three-missing warnings.

- [x] 3. Stage B: TerrainModifierSystem and wiring
  - [x] 3.1 Implement the TerrainModifierSystem resolver
    - New file `mygame/world/systems/terrain_modifiers.py`: frozen `TerrainModifiers`
      dataclass (`terrain_type`, `vision: int`, `movement: float`, `defense: float`),
      `ZERO_MODIFIERS`, and `TerrainModifierSystem(registry, terrain_generators)`
    - `resolve_base(planet, x, y)`: generator terrain lookup → TerrainDef base modifiers;
      `ZERO_MODIFIERS` when no generator for the planet or no TerrainDef for the terrain type
    - `resolve_for_player(player, planet, x, y)`: base plus summed matching class affinities
      (multiple matches sum) plus `db.tech_bonuses` reads under
      `terrain_affinity:{terrain}:{kind}` keys; any affinity-read failure degrades to base
      modifiers, logged once, never raises
    - Sign-preserving clamp (`copysign(bound, total)` when `abs(total) > bound`) applied
      inside the resolver on every return path; `vision` coerced to int after clamping
    - Resolver is coordinate-based and agnostic — occupied-vs-destination tile choice belongs
      to callers (do not unify the asymmetry)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 6.2, 6.3, 6.4, 6.7, 7.3, 7.4, 9.2, 9.5_

  - [x] 3.2 Wire the resolver at the composition root
    - In `initialize_game` (`mygame/server/conf/game_init.py`), construct
      `TerrainModifierSystem` after the registry and terrain generators exist and register it
      in the systems dict as `"terrain_modifier_system"` for `services.get_service` lookup
    - _Requirements: 2.1, 9.5_

  - [x] 3.3 Write property test for base resolution
    - **Property 3: Base resolution equals the generator's terrain definition**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; real `TerrainGenerator` plus
      in-memory registry; assert `resolve_base` returns the clamped TerrainDef values for
      `generator.get_terrain(x, y)`, and all-zero modifiers for missing generator or missing
      TerrainDef, regardless of any affinity data present
    - **Validates: Requirements 2.1, 2.3, 2.5, 2.6**

  - [x] 3.4 Write property test for affinity summation
    - **Property 4: Affinity summation**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; fake player with generated
      class affinities and `db.tech_bonuses` content; assert player-resolved value per kind
      equals `clamp(base + Σ matching class + Σ matching tech)` and non-matching kinds
      resolve to clamped base
    - **Validates: Requirements 2.2, 6.2, 6.3, 6.7, 7.3, 7.4**

  - [x] 3.5 Write property test for resolution determinism
    - **Property 5: Resolution determinism**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; repeated queries for a fixed
      (planet, coordinate, epoch, class, completed techs), interleaved with queries for other
      coordinates and players, return identical values every time
    - **Validates: Requirements 2.4**

  - [x] 3.6 Write property test for the sign-preserving clamp
    - **Property 6: Sign-preserving clamp on every resolver output**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; for any input state and
      non-negative bounds, every returned value satisfies `|value| <= bound(kind)`; within
      bound → equals total, exceeding bound → bound magnitude with the total's sign
    - **Validates: Requirements 9.2, 9.5**

  - [x] 3.7 Write property test for research recording terrain adjustments
    - **Property 15: Research completion records terrain adjustments**
    - In `mygame/world/tests/test_prop_terrain_modifiers.py`; drive the real TechLabSystem
      with fakes (test_tech_system.py style): completing each terrain technology writes its
      structured key into `db.tech_bonuses`, same-key values sum across technologies, and
      `recompute_tech_bonuses` reproduces the same dict (idempotent rebuild)
    - Include the example-based check that resolution reads the same `db.tech_bonuses` dict
      across sessions (reconnect equivalence)
    - **Validates: Requirements 7.2, 7.6**

  - [x] 3.8 Write unit tests for resolver fallback paths
    - New file `mygame/world/tests/test_terrain_modifiers.py`: no generator for planet →
      `ZERO_MODIFIERS`; terrain type without TerrainDef → `ZERO_MODIFIERS`; a class/tech
      affinity read that raises degrades to base modifiers without propagating and logs once
    - _Requirements: 2.3, 2.5, 6.4_

- [x] 4. Checkpoint — Stage B green
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m pytest mygame -q` from the repo root; new system has no consumers yet, so
    behavior is unchanged.

- [x] 5. Stage C1: Fog of war consumer
  - [x] 5.1 Apply terrain vision modifiers in FogOfWarSystem
    - In `mygame/world/coordinate/fog_of_war.py`, add `set_terrain_modifier_resolver(resolver)`
      (late-bound, mirroring `set_in_bounds_func`); inject it in `initialize_game`
      (`mygame/server/conf/game_init.py`) after the resolver is constructed
    - Player circle: `radius = base + sight_bonus + resolve_for_player(...).vision` at the
      occupied tile; truncate toward zero with `int()` then clamp to
      `max(min_vision_radius, radius)`
    - Building circle: `radius = building_vision_radius + resolve_base(planet, bx, by).vision`,
      base modifiers only, same truncate-then-minimum treatment
    - Fail-soft: unset resolver or a raise → terrain vision 0, circle computed from remaining
      adjustments, never raises; do not remove tiles from the discovery bitfield anywhere
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.7_

  - [x] 5.2 Write property test for the vision radius formula
    - **Property 7: Vision radius formula**
    - New file `mygame/world/tests/test_prop_terrain_consumers.py`; for any base radius,
      sight bonus, terrain vision modifier (integer or fractional), and configured minimum,
      the circle radius equals `max(min_vision_radius, int(base + sight_bonus + terrain))`
      for player circles (player-resolved, occupied tile) and building circles (base modifier,
      never affected by any player's affinities)
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.7**

  - [x] 5.3 Write property test for narrowed vision preserving discovery
    - **Property 8: Narrowed vision never forgets discovery**
    - In `mygame/world/tests/test_prop_terrain_consumers.py`; any discovery-update sequence
      followed by any circle narrowing: previously discovered tiles outside the new circle
      report `"fog"` — never `"unexplored"` — and the bitfield retains every prior tile
    - **Validates: Requirements 3.4**

  - [x] 5.4 Write unit tests for fog fallback and minimum radius
    - Extend `mygame/world/coordinate/tests/test_fog_of_war.py`: resolver unset → vision
      modifier 0; resolver raising → vision modifier 0 and no exception; omitted
      `min_vision_radius` in balance → default minimum of 1 applied to both player and
      building circles
    - _Requirements: 3.5, 3.6, 3.2_

- [x] 6. Stage C2: Movement gate consumer
  - [x] 6.1 Add the zero-floored combat move lag helper
    - Add `compute_combat_move_lag(base, move_speed, terrain_mod)` to
      `mygame/world/constants.py` returning `max(0, int(base - move_speed - terrain_mod))`
    - Keep `compute_effective_delay` (floor 1) untouched for agents — do not merge the two
      helpers; document the zero floor and truncation in the docstring
    - _Requirements: 4.1, 4.2_

  - [x] 6.2 Thread the destination tile through the movement gate
    - In `mygame/commands/game_commands.py`, change `_check_combat_move_lag` to accept
      `(caller, dest_x, dest_y)` and update CmdMove's call site in the same change
    - Resolve the destination tile's Movement_Modifier via
      `resolve_for_player(caller, planet, dest_x, dest_y).movement` (destination asymmetry);
      unresolvable or unwired resolver → 0
    - Compute effective lag with `compute_combat_move_lag`; out of combat: unchanged instant
      movement with stale lag cleared; blocked move leaves position and pending lag unchanged
    - Wait message includes the remaining wait in ticks (`next_move_tick - current_tick`,
      always > 0 on the blocked path); wrap `caller.msg(...)` in try/except so a delivery
      failure still blocks the move
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 2.7_

  - [x] 6.3 Write property test for the movement lag formula and asymmetry
    - **Property 9: In-combat movement lag formula with destination asymmetry**
    - New file `mygame/commands/tests/test_prop_terrain_movement.py`; independently generated
      occupied/destination terrain modifiers: a permitted in-combat move schedules
      `current_tick + max(0, int(COMBAT_MOVE_LAG_TICKS - move_speed - destination_modifier))`
      using the destination tile's modifier, not the occupied tile's; out-of-combat moves are
      always permitted with no lag scheduled
    - **Validates: Requirements 4.1, 4.2, 4.3, 2.7**

  - [x] 6.4 Write property test for blocked moves changing nothing
    - **Property 10: Blocked moves change nothing**
    - In `mygame/commands/tests/test_prop_terrain_movement.py`; any in-combat player with a
      future `next_move_tick`: the attempted move is rejected and coordinates and pending lag
      are exactly their pre-attempt values
    - **Validates: Requirements 4.4**

  - [x] 6.5 Write unit tests for movement gate edge cases
    - Extend `mygame/commands/tests/test_game_commands.py`: blocked-move message contains the
      remaining wait in ticks and no message is sent when remaining wait is zero; a
      `caller.msg` raise still blocks the move; unresolvable destination modifier computes
      lag with terrain 0; verify the two existing movement-lag tests remain valid
    - _Requirements: 4.5, 4.6, 4.7_

- [x] 7. Checkpoint — fog and movement consumers green
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m pytest mygame -q` from the repo root. Movement-lag caveat: the floor-0
    switch is behavior-identical for all shipped equipment (`base - speed >= 1`).

- [x] 8. Stage C3: Combat engine consumer
  - [x] 8.1 Apply terrain defense in the physical damage branch
    - In `_calculate_damage` (`mygame/world/systems/combat_engine.py`), physical branch only:
      `armor_reduction += self._terrain_defense(target)` then `max(0.0, armor_reduction)`;
      non-physical branches unchanged
    - New `_terrain_defense(target)` helper: player target →
      `resolve_for_player(target, planet, x, y).defense` at the target's position when the
      attack resolves; building target → `resolve_base(planet, x, y).defense` (base only, in
      `_calculate_damage` since `_get_target_armor_reduction` is player-only); any failure →
      0.0, guarded, never raises
    - Chip floor untouched and applied after the terrain-adjusted DR
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 2.7_

  - [x] 8.2 Write property test for the physical damage formula
    - **Property 11: Physical damage formula with terrain DR, chip floor, and zero floor**
    - In `mygame/world/tests/test_prop_terrain_consumers.py`; for any physical attack with
      positive raw output, damage equals
      `max(chip_floor, int(raw - max(0, other_DR + terrain_defense)), 0)` with
      player-resolved defense for player targets and base defense for buildings; damage never
      falls below `ceil(raw * chip_fraction)` and never exceeds `raw`
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 2.7**

  - [x] 8.3 Write property test for non-physical damage ignoring terrain
    - **Property 12: Non-physical damage ignores terrain**
    - In `mygame/world/tests/test_prop_terrain_consumers.py`; any non-physical attack
      computes identical damage whether terrain modifiers, class affinities, and terrain
      technologies are present or entirely absent
    - **Validates: Requirements 5.5**

- [x] 9. Stage C4: Display and placement surfaces
  - [x] 9.1 Show resolved modifiers in tile inspection, gated by discovery
    - At the coordinate-inspection read points in `mygame/commands/game_commands.py`, check
      the tile's discovery state via `fog_system.get_tile_visibility(...)` first: unexplored →
      respond only that the tile is unexplored, revealing no terrain type or modifier values
    - Otherwise display terrain type plus the three values from
      `resolve_for_player(caller, planet, x, y)` — clamped, affinity-adjusted, never raw
      TerrainDef fields
    - _Requirements: 8.1, 8.4_

  - [x] 9.2 Add a Terrain section to CmdScore
    - In `CmdScore` (`mygame/commands/game_commands.py`), show the three resolved values for
      the player's current tile, always printing all three including zeros
    - _Requirements: 8.2_

  - [x] 9.3 Include terrain defense in building placement feedback
    - In `mygame/world/systems/building_system.py`, append the target tile's
      `resolve_base(planet, x, y).defense` to both the acceptance message and every rejection
      message; inject the resolver at the composition root
      (`mygame/server/conf/game_init.py`) like the existing `terrain_provider`, with a
      services-lookup fallback
    - _Requirements: 8.3, 2.6, 5.3_

  - [x] 9.4 Write property test for inspection output and unexplored secrecy
    - **Property 16: Inspection shows resolved values and unexplored tiles leak nothing**
    - New file `mygame/commands/tests/test_prop_terrain_display.py`, asserting on captured
      `msg()` output: discovered/visible tiles show terrain type and the three
      resolver-produced values (asserted on states where resolved differs from raw
      TerrainDef); undiscovered tiles yield an unexplored indication with no terrain type and
      no modifier values
    - **Validates: Requirements 8.1, 8.4**

  - [x] 9.5 Write property test for score and placement surfaces
    - **Property 17: Score and placement surfaces render resolved values**
    - In `mygame/commands/tests/test_prop_terrain_display.py`; any resolved triple (including
      zeros) appears in full in the score display for the current tile; any placement attempt,
      accepted or rejected, includes the target tile's resolved defense modifier in feedback
    - **Validates: Requirements 8.2, 8.3**

- [x] 10. Checkpoint — all mechanics green before content
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m pytest mygame -q` from the repo root; all consumers wired, shipped data
    still neutral so gameplay formulas are unchanged.

- [x] 11. Stage D: Content population
  - [x] 11.1 Populate terrain.yaml with modifier values
    - Add `vision_modifier` / `movement_modifier` / `defense_modifier` values to terrain
      entries in `mygame/data/definitions/terrain.yaml` per the design's data model (e.g.
      Forest: vision -2, movement -1, defense 3); terrains left neutral may omit fields
      (accepting the 1.3 warning) or state zeros explicitly
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 11.2 Add class terrain affinities to classes.yaml
    - Add `terrain_affinities` lists to classes in `mygame/data/definitions/classes.yaml`
      (e.g. the Ranger example), each positive-affinity class carrying an offsetting negative
      affinity or negative stat_modifier with the weakness named in the description
    - _Requirements: 6.1, 6.5_

  - [x] 11.3 Add terrain technologies to technologies.yaml
    - Add technology entries with `effect_type: terrain_affinity` and structured
      `terrain_affinity:{terrain}:{kind}` effect_value keys to
      `mygame/data/definitions/technologies.yaml` (e.g. Forest Warfare)
    - _Requirements: 7.1_

- [x] 12. Final checkpoint — full suite green with content live
  - Ensure all tests pass, ask the user if questions arise.
  - Run `python -m pytest mygame -q` from the repo root; zero failures/errors, and confirm a
    clean load with no unexpected registry warnings beyond intentionally neutral terrains.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Stages land in the design's Migration and Rollout order; every field is neutral-by-default
  so each stage independently keeps the 2819-test suite green
- Property tests (Hypothesis, `@settings(max_examples=100)`+) implement the design's 17
  correctness properties, one sub-task per property, tagged
  `# Feature: terrain-strategy, Property N: <title>`
- Registry-load properties use the test_prop_hot_reload.py tempfile pattern; runtime
  properties use fakes and `services.override`, never Evennia database objects
- The occupied-vs-destination tile asymmetry (Req 2.7) lives in the consumers — do not unify
- Every modified line must respect the .flake8 100-character limit

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.5", "1.6"] },
    { "id": 2, "tasks": ["1.4", "1.7", "1.8"] },
    { "id": 3, "tasks": ["1.9", "3.1"] },
    { "id": 4, "tasks": ["1.10", "3.2", "3.8"] },
    { "id": 5, "tasks": ["1.11", "5.1", "6.1"] },
    { "id": 6, "tasks": ["3.3", "5.2", "5.4", "6.2"] },
    { "id": 7, "tasks": ["3.4", "5.3", "6.3", "6.5", "8.1"] },
    { "id": 8, "tasks": ["3.5", "6.4", "8.2", "9.1"] },
    { "id": 9, "tasks": ["3.6", "8.3", "9.2", "9.3", "9.4"] },
    { "id": 10, "tasks": ["3.7", "9.5"] },
    { "id": 11, "tasks": ["11.1", "11.2", "11.3"] }
  ]
}
```
