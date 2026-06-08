# Implementation Plan: Quit Building Cleanup

## Overview

Extend `CombatCharacter.at_pre_disconnect` to comprehensively destroy all unprotected building contents on player disconnect. Changes are localized to `world/constants.py` (new constant) and `typeclasses/characters.py` (refactored method + three helper functions), with a new test file for property-based and unit tests.

## Tasks

- [x] 1. Add `PROTECTED_BUILDING_TYPES` constant to `world/constants.py`
  - Add `PROTECTED_BUILDING_TYPES: set[str] = {"VT"}` to `world/constants.py`
  - Include a comment explaining the constant's purpose and how to extend it
  - _Requirements: 6.1, 6.2_

- [x] 2. Implement helper functions and refactor `at_pre_disconnect`
  - [x] 2.1 Implement `_get_building_type` helper in `typeclasses/characters.py`
    - Extract building type lookup into a standalone function: `_get_building_type(building) -> str | None`
    - Use `attributes.get` with `db` fallback, matching the existing accessor pattern
    - _Requirements: 1.1, 1.2_

  - [x] 2.2 Implement `_clear_extractor_inventory` helper in `typeclasses/characters.py`
    - Create `_clear_extractor_inventory(building) -> None`
    - Reset `resource_inventory` to `{}` using `attributes.add` with `db` fallback
    - Match the accessor pattern used by `ResourceSystem._set_extractor_inventory`
    - Guard against missing `resource_inventory` attribute (skip without error)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 2.3 Implement `_delete_objects_at_building` helper in `typeclasses/characters.py`
    - Create `_delete_objects_at_building(building) -> None`
    - Resolve `(coord_x, coord_y)` and `location` (PlanetRoom) from the building
    - Call `room.get_objects_at(x, y)` without a `type_tag` filter to get all objects
    - Exclude the building itself from the deletion set
    - Call `obj.delete()` on each remaining object
    - Implement fallback: iterate `room.contents` matching coordinates if `get_objects_at` is unavailable
    - Skip buildings with no valid coordinates or no room location
    - _Requirements: 1.3, 1.4, 5.1, 5.2, 5.3_

  - [x] 2.4 Refactor `CombatCharacter.at_pre_disconnect` to use constant and helpers
    - Import `PROTECTED_BUILDING_TYPES` from `world.constants`
    - Replace hardcoded `"VT"` check with `btype in PROTECTED_BUILDING_TYPES`
    - Use `_get_building_type`, `_clear_extractor_inventory`, `_delete_objects_at_building` helpers
    - Wrap each building's cleanup in individual `try/except Exception` with `logger.debug`
    - Wrap the entire building loop in an outer `try/except Exception`
    - Ensure `player_logout` event is always published after cleanup (in its own try/except)
    - Remove any hardcoded building type strings from skip logic
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 3.1, 3.2, 4.1, 4.2, 4.3, 5.1, 6.1, 6.3_

- [x] 3. Checkpoint
  - Ensure all code changes are syntactically correct and consistent with the design, ask the user if questions arise.

- [x] 4. Write tests for quit building cleanup
  - [x] 4.1 Write property test: Protected building preservation (Property 1)
    - **Property 1: Protected building preservation**
    - Generate random buildings (mix of protected/unprotected types) with random objects at each tile and random `resource_inventory` dicts
    - Assert objects at protected tiles remain unchanged and inventory is unchanged after cleanup
    - Use Hypothesis with `@settings(max_examples=100)` and the Evennia stub pattern (FakeBuilding, FakePlanetRoom, FakeGameEntity)
    - **Validates: Requirements 1.2, 3.1, 3.2, 3.3**

  - [x] 4.2 Write property test: Unprotected building tile cleanup (Property 2)
    - **Property 2: Unprotected building tile cleanup**
    - Generate random unprotected buildings with random objects at tiles
    - Assert all non-building objects are deleted and the building itself survives
    - **Validates: Requirements 1.3, 5.1**

  - [x] 4.3 Write property test: Extractor inventory cleared (Property 3)
    - **Property 3: Extractor inventory cleared**
    - Generate random Extractors with random `dict[str, int]` inventories
    - Assert `resource_inventory == {}` after cleanup
    - **Validates: Requirements 2.1**

  - [x] 4.4 Write property test: Error isolation across buildings (Property 4)
    - **Property 4: Error isolation across buildings**
    - Generate random building lists with one injected failure at a random position
    - Assert buildings processed after the failing building still have their cleanup applied
    - **Validates: Requirements 4.1**

  - [x] 4.5 Write property test: Logout event always fires (Property 5)
    - **Property 5: Logout event always fires**
    - Generate random cleanup scenarios (normal, per-building error, total failure)
    - Assert `PLAYER_LOGOUT` event is published in all cases
    - **Validates: Requirements 4.2, 4.3**

  - [x] 4.6 Write unit tests for helpers and constant
    - Test `PROTECTED_BUILDING_TYPES` constant equals `{"VT"}`
    - Test `_get_building_type` returns correct type and handles missing attribute
    - Test `_clear_extractor_inventory` resets inventory and handles missing attribute gracefully
    - Test `_delete_objects_at_building` deletes all objects except building, handles missing coords, and uses fallback when `get_objects_at` is unavailable
    - Test building with `None` coordinates is skipped without error
    - Test Extractor with no `resource_inventory` attribute is skipped without error
    - _Requirements: 1.4, 2.2, 2.3, 5.3, 6.2_

- [x] 5. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All changes are in `world/constants.py` and `typeclasses/characters.py`; tests go in a new test file
