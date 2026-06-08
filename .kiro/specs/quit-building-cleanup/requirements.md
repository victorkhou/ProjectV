# Requirements Document

## Introduction

When a player quits (disconnects), all items inside their buildings are destroyed except for items stored in protected buildings (currently only the Vault). This includes ResourceDrop objects on building tiles and resource_inventory data stored on Extractor buildings. The existing `at_pre_disconnect` hook on CombatCharacter already deletes ResourceDrop objects from non-Vault building tiles, but it does not clear the `resource_inventory` attribute on Extractors or handle other non-resource objects. This feature extends the quit cleanup to cover all item types across all unprotected buildings, and introduces a configurable `PROTECTED_BUILDING_TYPES` constant so future storage buildings can be added without code changes.

## Glossary

- **Cleanup_System**: The logic within `CombatCharacter.at_pre_disconnect` that iterates over owned buildings and removes items on player disconnect.
- **Building**: An Evennia object of typeclass `Building` with a `building_type` attribute (two-letter abbreviation: HQ, EX, AC, LB, AR, TU, VT, RD, WL, BK, MB, RL) and an `owner` attribute referencing the owning CombatCharacter.
- **Protected_Building_Types**: A configurable set of `building_type` codes whose contents are preserved across player disconnects. Currently contains `{"VT"}` (Vault). Future storage buildings can be added to this set without code changes.
- **Vault**: A Building with `building_type` equal to `"VT"`. Currently the only Protected_Building_Type.
- **Extractor**: A Building with `building_type` equal to `"EX"`. Extractors store harvested resources in a `resource_inventory` attribute (a `dict[str, int]` mapping resource type names to amounts).
- **ResourceDrop**: An Evennia object of typeclass `ResourceDrop` tagged with `resource_drop` (category `object_type`). Represents a stack of resources placed on a PlanetRoom tile at specific coordinates.
- **PlanetRoom**: The room typeclass that holds buildings and resource drops, supporting coordinate-based queries via `get_objects_at(x, y, type_tag=...)`.
- **CombatCharacter**: The player character typeclass that owns buildings and implements the `at_pre_disconnect` hook.
- **Building_Tile**: The coordinate position `(coord_x, coord_y)` within a PlanetRoom where a Building is located. Objects at those coordinates are considered "inside" the building.

## Requirements

### Requirement 1: Destroy Resource Drops on Unprotected Building Tiles at Disconnect

**User Story:** As a game designer, I want resource drops on unprotected building tiles to be destroyed when the owning player disconnects, so that resources do not persist unprotected while the player is offline.

#### Acceptance Criteria

1. WHEN a CombatCharacter disconnects, THE Cleanup_System SHALL iterate over all Buildings owned by that CombatCharacter.
2. WHILE iterating over owned Buildings, THE Cleanup_System SHALL skip any Building whose `building_type` is in the Protected_Building_Types set.
3. WHEN an unprotected Building is found, THE Cleanup_System SHALL query the PlanetRoom for all ResourceDrop objects at the Building's `(coord_x, coord_y)` coordinates and delete each one.
4. IF a Building has no valid coordinates or no PlanetRoom location, THEN THE Cleanup_System SHALL skip that Building without error.

### Requirement 2: Clear Extractor Resource Inventories at Disconnect

**User Story:** As a game designer, I want Extractor resource inventories to be cleared when the owning player disconnects, so that stored resources inside Extractors do not persist unprotected while the player is offline.

#### Acceptance Criteria

1. WHEN an unprotected Building with `building_type` `"EX"` (Extractor) is processed during disconnect cleanup, THE Cleanup_System SHALL reset the Building's `resource_inventory` attribute to an empty dictionary.
2. THE Cleanup_System SHALL clear the `resource_inventory` attribute using the same accessor pattern used by `ResourceSystem._set_extractor_inventory` (Evennia `attributes.add` or `db` fallback).
3. IF the Extractor has no `resource_inventory` attribute set, THEN THE Cleanup_System SHALL skip it without error.

### Requirement 3: Protected Building Contents Preserved at Disconnect

**User Story:** As a player, I want items stored in my protected buildings (e.g. Vault) to survive when I disconnect, so that I have a safe place to protect my resources.

#### Acceptance Criteria

1. WHEN a CombatCharacter disconnects, THE Cleanup_System SHALL leave all ResourceDrop objects at Protected_Building_Types coordinates untouched.
2. WHEN a CombatCharacter disconnects, THE Cleanup_System SHALL leave the `resource_inventory` attribute on Protected_Building_Types untouched.
3. FOR ALL disconnect events, the count of ResourceDrop objects at protected building coordinates before disconnect SHALL equal the count after disconnect (preservation invariant).

### Requirement 4: Graceful Error Handling During Cleanup

**User Story:** As a game designer, I want the cleanup process to handle errors gracefully, so that a failure cleaning one building does not prevent cleanup of remaining buildings or block the disconnect flow.

#### Acceptance Criteria

1. IF an error occurs while cleaning a single Building, THEN THE Cleanup_System SHALL log the error at debug level and continue processing the remaining Buildings.
2. IF an error occurs during the entire cleanup process, THEN THE Cleanup_System SHALL log the error and allow the disconnect to proceed without interruption.
3. THE Cleanup_System SHALL publish the `player_logout` event on the EventBus after cleanup completes, regardless of whether any cleanup errors occurred.

### Requirement 5: Cleanup Covers All Non-Resource Objects on Building Tiles

**User Story:** As a game designer, I want all objects on unprotected building tiles (not just resource drops) to be destroyed on disconnect, so that no items persist unprotected inside buildings.

#### Acceptance Criteria

1. WHEN an unprotected Building is processed during disconnect cleanup, THE Cleanup_System SHALL delete all GameEntity objects located at the Building's coordinates, excluding the Building object itself.
2. THE Cleanup_System SHALL identify objects at building coordinates using `PlanetRoom.get_objects_at(x, y)` without a type_tag filter, then exclude the Building from the deletion set.
3. IF the PlanetRoom does not support `get_objects_at`, THEN THE Cleanup_System SHALL fall back to iterating room contents and matching coordinates.

### Requirement 6: Protected Building Types Configurable via Constant

**User Story:** As a developer, I want the set of protected building types to be defined in a single constant, so that adding future storage buildings requires only a one-line change.

#### Acceptance Criteria

1. THE Cleanup_System SHALL read the set of protected building types from a constant (e.g. `PROTECTED_BUILDING_TYPES`) defined in `world/constants.py`.
2. THE constant SHALL initially contain `{"VT"}`.
3. THE Cleanup_System SHALL NOT contain any hardcoded building type strings for skip logic — all skip decisions SHALL reference the constant.
