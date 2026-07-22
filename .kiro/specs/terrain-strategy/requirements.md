# Requirements Document

## Introduction

This feature adds terrain-based gameplay modifiers so that the tile a player stands on — and the tiles a player builds a base on — matter strategically. Each terrain type gains a set of combat-relevant modifiers: a vision modifier (widening or narrowing fog-of-war reveal), a combat movement modifier (speeding up or slowing in-combat movement lag), and a defense modifier (adding or subtracting damage reduction). Player classes and researchable technologies interact with these modifiers, letting players specialize toward specific terrain playstyles.

The feature builds on existing systems: the deterministic per-coordinate TerrainGenerator, the FogOfWarSystem (Chebyshev vision circles with `sight_range` bonuses), the in-combat movement lag (`COMBAT_MOVE_LAG_TICKS` reduced by `move_speed`), the CombatEngine's `damage_reduction` pipeline with its chip-damage floor, the data-driven class sidegrade system (`ClassDef.stat_modifiers`), and the TechLab research system (`db.tech_bonuses`). Terrain modifiers are defined in data (terrain.yaml) and flow through the existing balance/registry facade. All definition validation in this feature follows the DataRegistry's existing fail-fast contract: validation errors are collected across all definitions, logged, and abort boot with a DataRegistryError, while a failed hot-reload atomically retains the currently loaded data.

## Glossary

- **Terrain_Modifier_System**: The new system component that resolves the terrain modifiers in effect for an entity at a given coordinate, combining the terrain's base modifiers with class and technology adjustments.
- **TerrainDef**: The existing data definition for a terrain type (terrain.yaml), extended by this feature with modifier fields.
- **Vision_Modifier**: An integer adjustment to an entity's fog-of-war vision radius contributed by the terrain the entity occupies. Positive widens vision; negative narrows it.
- **Movement_Modifier**: A numeric adjustment to an entity's in-combat movement lag contributed by the terrain of the destination tile the entity is moving onto, not the tile the entity currently occupies. Positive values reduce lag (faster); negative values increase lag (slower).
- **Defense_Modifier**: A numeric adjustment to an entity's damage reduction contributed by the terrain the entity occupies. Positive adds damage reduction; negative subtracts it.
- **FogOfWarSystem**: The existing system computing visible/fog/unexplored tile states from Chebyshev vision circles.
- **CombatEngine**: The existing system resolving attacks, including armor damage reduction with the chip-damage floor.
- **BuildingSystem**: The existing system validating and executing building construction.
- **TechLabSystem**: The existing system applying researched technology effects to `db.tech_bonuses`.
- **DataRegistry**: The existing registry loading data definitions (terrain, classes, technologies, balance) from YAML.
- **ClassDef**: The existing data definition for a selectable player class with `stat_modifiers`.
- **Terrain_Affinity**: A class or technology adjustment that changes how strongly a specific terrain modifier applies to a player (for example, a class that ignores movement penalties on Forest tiles).
- **In_Combat**: The existing player state where `combat_timer_expires` is in the future, gating movement lag.
- **Chip_Floor**: The existing CombatEngine rule guaranteeing a landed hit always deals at least a configured fraction of its raw damage regardless of total damage reduction.

## Requirements

### Requirement 1: Terrain Modifier Definitions

**User Story:** As a game designer, I want each terrain type to carry vision, movement, and defense modifiers defined in data, so that terrain balance can be tuned without code changes.

#### Acceptance Criteria

1. WHEN the DataRegistry loads the terrain definitions file, THE DataRegistry SHALL load a Vision_Modifier (an integer number of vision-radius tiles), a Movement_Modifier (a numeric adjustment expressed in movement-lag ticks), and a Defense_Modifier (a numeric damage-reduction adjustment) for each terrain type defined in that file.
2. IF a terrain definition omits a modifier field or provides a null value for a modifier field, while at least one other modifier field carries a valid numeric value, THEN THE DataRegistry SHALL default that modifier to zero for that terrain type and SHALL load the terrain definition without reporting a validation error.
3. IF a terrain definition omits or provides null values for all three modifier fields, THEN THE DataRegistry SHALL log a warning identifying the terrain type, and THE DataRegistry SHALL load the terrain definition with all three modifiers defaulted to zero. (A fully unmodified terrain is legal data, so this condition is a warning rather than a validation error and does not fail the load.)
4. IF a terrain definition contains a non-numeric modifier value (any value that is not an integer or decimal number, including boolean and string values), THEN THE DataRegistry SHALL report a validation error identifying the terrain type and each offending field name, and THE DataRegistry SHALL fail the load, collecting all validation errors across all terrain definitions before failing so that every error is reported at once. (This follows the DataRegistry's existing fail-fast convention of raising DataRegistryError rather than loading a partial terrain set.)
5. THE DataRegistry SHALL NOT report a validation error for a terrain definition whose modifier fields all carry valid numeric values.
6. THE DataRegistry SHALL expose the loaded Vision_Modifier, Movement_Modifier, and Defense_Modifier values as fields of the existing TerrainDef structure returned by the registry's terrain lookup, with values equal to those loaded from the terrain definitions file or the zero defaults, so that consumers read modifiers via the registry facade and never read the terrain definitions file directly.

### Requirement 2: Terrain Modifier Resolution

**User Story:** As a developer, I want a single resolution point that answers "what modifiers apply to this entity at this coordinate", so that all consumers (fog of war, movement, combat) behave consistently.

#### Acceptance Criteria

1. WHEN a consumer requests modifiers for a planet coordinate, THE Terrain_Modifier_System SHALL resolve the terrain type at that coordinate using the planet's terrain generator and return the terrain's Vision_Modifier, Movement_Modifier, and Defense_Modifier.
2. WHEN a player has one or more class or technology Terrain_Affinity entries matching the resolved terrain type, THE Terrain_Modifier_System SHALL add each matching affinity adjustment to the terrain's base modifier of the matching modifier kind (vision, movement, or defense), leaving modifier kinds without a matching affinity at their base values.
3. IF no terrain generator exists for the requested planet, THEN THE Terrain_Modifier_System SHALL return zero for all modifiers.
4. WHEN the Terrain_Modifier_System resolves modifiers for the same coordinate, planet, terrain generator epoch, player class, and set of completed terrain technologies, THE Terrain_Modifier_System SHALL return identical modifier values on every such query.
5. IF the terrain type resolved at a coordinate has no TerrainDef in the DataRegistry, THEN THE Terrain_Modifier_System SHALL return zero for all modifiers. (Fail-fast loading means invalid terrain definitions never reach a running game; this guard covers cross-file mismatches where a planet's terrain generator emits a terrain type not present in the terrain definitions file.)
6. WHEN a consumer requests modifiers for a non-player entity such as a building, THE Terrain_Modifier_System SHALL return the terrain's base modifiers without class or technology affinity adjustments.
7. THE Terrain_Modifier_System SHALL resolve Vision_Modifier and Defense_Modifier values against the terrain of the tile the entity occupies, and Movement_Modifier values against the terrain of the destination tile the entity is moving onto. (This tile-selection asymmetry is intentional: an entity sees and defends from where it stands, and slogs into the tile it enters.)

### Requirement 3: Terrain Vision Effects

**User Story:** As a player, I want the terrain I stand on to widen or narrow what I can see, so that scouting routes and base placement account for sightlines.

#### Acceptance Criteria

1. WHEN the FogOfWarSystem computes a player's vision circle, THE FogOfWarSystem SHALL add the player's resolved terrain Vision_Modifier (the Terrain_Modifier_System result for the terrain at the player's current coordinate, including class and technology Terrain_Affinity adjustments) to the player's vision radius before generating the Chebyshev vision circle.
2. WHEN a combined vision radius (base radius plus equipment, technology, and terrain adjustments) falls below the minimum vision radius configured in the balance configuration, THE FogOfWarSystem SHALL use the configured minimum vision radius for that vision circle, for both player and building vision circles.
3. WHEN the FogOfWarSystem computes a building's vision circle, THE FogOfWarSystem SHALL add the base Vision_Modifier of the terrain at the building's position to the building vision radius, without applying any player's class or technology Terrain_Affinity adjustments.
4. WHILE a player occupies a terrain with a negative Vision_Modifier, THE FogOfWarSystem SHALL report previously discovered tiles that fall outside the narrowed vision circle as fog rather than unexplored, retaining all previously discovered tiles in the player's discovery memory.
5. IF the FogOfWarSystem cannot obtain a terrain Vision_Modifier for a position because terrain modifier resolution fails or is unavailable, THEN THE FogOfWarSystem SHALL apply a Vision_Modifier of zero for that position and compute the vision circle using the remaining adjustments.
6. IF the balance configuration omits the minimum vision radius, THEN THE FogOfWarSystem SHALL use a default minimum vision radius of 1 tile.
7. WHEN a combined vision radius calculation produces a non-integer value, THE FogOfWarSystem SHALL truncate the value toward zero to a whole number of tiles before applying the minimum vision radius.

### Requirement 4: Terrain Combat Movement Effects

**User Story:** As a player, I want terrain to change how fast I can move during combat, so that choosing where to fight is a tactical decision.

#### Acceptance Criteria

1. WHILE a player is In_Combat, WHEN the player moves onto a tile, THE movement gate SHALL compute the player's effective movement lag by reducing the base combat movement lag by the sum of the player's equipment move_speed modifier and the destination terrain's Movement_Modifier as resolved by the Terrain_Modifier_System, and SHALL require that effective lag to elapse before the player's next permitted move.
2. IF the combined movement lag calculation produces a value below zero ticks, THEN THE movement gate SHALL use an effective movement lag of zero ticks, permitting the player's next move on the same tick.
3. WHILE a player is not In_Combat, THE movement gate SHALL apply no terrain movement lag adjustment and SHALL impose no movement lag between moves.
4. WHEN a player In_Combat attempts to move before the effective movement lag expires, THE movement gate SHALL block the move, leaving the player's position and pending lag unchanged.
5. IF delivering the wait message to the player fails, THEN THE movement gate SHALL still block the move until the effective movement lag expires.
6. WHEN the movement gate blocks a move due to an unexpired effective movement lag, THE movement gate SHALL message the player with the remaining wait expressed in ticks, and SHALL send no wait message when the remaining wait is zero ticks.
7. IF the destination terrain's Movement_Modifier cannot be resolved, THEN THE movement gate SHALL compute the effective movement lag using a terrain Movement_Modifier of zero.

### Requirement 5: Terrain Defense Effects

**User Story:** As a player, I want terrain to grant or reduce damage reduction, so that defending on favorable ground and building on defensible terrain are rewarded.

#### Acceptance Criteria

1. WHEN the CombatEngine computes a player target's physical damage reduction for an attack, THE CombatEngine SHALL add the Defense_Modifier resolved by the Terrain_Modifier_System for the target's position at the time the attack resolves, including the target's class and technology defense adjustments, to the target's damage reduction total.
2. THE CombatEngine SHALL apply the existing Chip_Floor after terrain Defense_Modifier adjustments so that a landed hit always deals at least the chip fraction of its raw damage regardless of the computed damage reduction total.
3. WHEN a building is the target of an attack with a physical damage type, THE CombatEngine SHALL add the terrain's base Defense_Modifier at the building's position, without class or technology adjustments, to the building's damage reduction total.
4. IF the combined damage reduction total including a negative terrain Defense_Modifier falls below zero, THEN THE CombatEngine SHALL use zero damage reduction so that a negative Defense_Modifier never increases damage beyond the attack's raw output.
5. WHEN the CombatEngine resolves an attack whose damage type is not physical, THE CombatEngine SHALL apply no terrain Defense_Modifier adjustment to that attack's damage calculation.

### Requirement 6: Class Terrain Affinities

**User Story:** As a player, I want my chosen class to interact with terrain (for example, a class that moves freely through forests or sees farther from mountains), so that class choice shapes my terrain strategy.

#### Acceptance Criteria

1. THE DataRegistry SHALL load zero or more optional Terrain_Affinity entries for each ClassDef from the class definitions file, each entry specifying a terrain type defined in the terrain definitions file, a modifier kind that is one of vision, movement, or defense, and a numeric adjustment.
2. WHEN a player whose class defines a Terrain_Affinity occupies a tile whose terrain type matches that affinity's terrain type, THE Terrain_Modifier_System SHALL add the affinity's numeric adjustment to the terrain's base modifier of the matching modifier kind, leaving the terrain's modifiers of other kinds unchanged.
3. WHEN a player occupies a tile for which the player's class defines no Terrain_Affinity matching that terrain type and modifier kind, THE Terrain_Modifier_System SHALL return the terrain's base modifier for that kind without class adjustment.
4. IF resolving a player's class Terrain_Affinity fails due to an internal error, THEN THE Terrain_Modifier_System SHALL apply the terrain's base modifiers without class adjustment.
5. IF a ClassDef contains one or more Terrain_Affinity entries with a positive adjustment, THEN the class definitions file SHALL also define, within the same ClassDef, at least one Terrain_Affinity entry with a negative adjustment or at least one negative stat_modifiers entry, and the ClassDef description text SHALL name that offsetting weakness.
6. IF a Terrain_Affinity entry specifies a terrain type not defined in the terrain definitions file, a modifier kind other than vision, movement, or defense, or a non-numeric adjustment, THEN THE DataRegistry SHALL report a validation error identifying the class and the invalid field, and THE DataRegistry SHALL fail the load, collecting all validation errors across all class definitions before failing.
7. WHEN a player's class defines more than one Terrain_Affinity matching the same terrain type and modifier kind, THE Terrain_Modifier_System SHALL sum all matching adjustments before adding the total to the terrain's base modifier of that kind.

### Requirement 7: Technology Terrain Effects

**User Story:** As a player, I want researchable technologies that improve my interaction with terrain, so that research choices deepen terrain strategy in the mid-to-late game.

#### Acceptance Criteria

1. THE DataRegistry SHALL load technology definitions whose effect grants a Terrain_Affinity adjustment, each specifying a terrain type, a modifier kind (vision, movement, or defense), and a numeric adjustment.
2. WHEN a player completes research of a terrain technology, THE TechLabSystem SHALL record the terrain adjustment in the player's persistent technology bonuses.
3. WHEN the Terrain_Modifier_System resolves modifiers for a player, THE Terrain_Modifier_System SHALL include terrain adjustments only from technologies the player has completed researching, applying zero adjustment from technologies that are in progress or not yet started.
4. WHEN a player has a class Terrain_Affinity and one or more technology adjustments for the same terrain type and modifier kind, THE Terrain_Modifier_System SHALL sum the class adjustment and all matching technology adjustments.
5. IF a technology definition contains an unknown terrain type, an invalid modifier kind, or a non-numeric adjustment, THEN THE DataRegistry SHALL report a validation error identifying the technology and field, and THE DataRegistry SHALL fail the load, collecting all validation errors across all technology definitions before failing.
6. WHEN a player reconnects in a later session, THE Terrain_Modifier_System SHALL apply the terrain adjustments from the player's previously completed research without requiring the player to research again.

### Requirement 8: Terrain Strategy Visibility

**User Story:** As a player, I want to see a terrain's modifiers before I fight or build on it, so that terrain choice is an informed strategic decision.

#### Acceptance Criteria

1. WHEN a player inspects a tile that is currently visible or present in the player's discovery memory, THE game SHALL display the tile's terrain type and the Vision_Modifier, Movement_Modifier, and Defense_Modifier values as resolved by the Terrain_Modifier_System for that player at that tile, including class Terrain_Affinity adjustments, completed technology adjustments, and balance bound clamping, rather than the raw terrain definition values.
2. WHEN a player views their character sheet or score display, THE game SHALL display the Vision_Modifier, Movement_Modifier, and Defense_Modifier values resolved by the Terrain_Modifier_System for the player at the player's current position, including any modifier whose resolved value is zero.
3. WHEN a player attempts to place a building, THE BuildingSystem SHALL include the Defense_Modifier resolved by the Terrain_Modifier_System for the target tile in the placement feedback for both accepted and rejected placement attempts.
4. IF a player inspects a tile that has never been discovered by that player (unexplored state), THEN THE game SHALL respond with an indication that the tile is unexplored and SHALL NOT reveal the tile's terrain type or any of its modifier values.

### Requirement 9: Balance Bounds

**User Story:** As a game designer, I want terrain modifier magnitudes bounded by balance configuration, so that terrain effects stay meaningful but never dominate equipment and skill.

#### Acceptance Criteria

1. THE DataRegistry SHALL load from the balance configuration one non-negative numeric bound per terrain modifier kind (vision, movement, and defense), each defining the maximum absolute value permitted for that kind's resolved total terrain adjustment.
2. WHEN the Terrain_Modifier_System resolves a total terrain adjustment (base plus class plus technology) whose absolute value exceeds the configured bound for its modifier kind, THE Terrain_Modifier_System SHALL replace that adjustment with the bound magnitude carrying the original adjustment's sign (for example, a total of -9 with a bound of 5 becomes -5), and SHALL leave unchanged any adjustment whose absolute value is within the bound (for example, a total of -6 with a bound of 8 stays -6).
3. WHEN the balance configuration omits the bound for one or more terrain modifier kinds, THE DataRegistry SHALL use the default bound for each omitted kind: 5 tiles for vision, 3 ticks for movement, and 6 damage-reduction points for defense.
4. IF a terrain modifier bound value in the balance configuration is non-numeric or negative, THEN THE DataRegistry SHALL report a validation error identifying the offending field, and THE DataRegistry SHALL fail the load, consistent with the existing balance configuration validation. (The defaults in criterion 3 apply only to omitted bounds; invalid values fail fast rather than falling back to defaults.)
5. THE Terrain_Modifier_System SHALL return only clamped adjustments to all consumers (FogOfWarSystem, movement gate, CombatEngine, and inspection displays) so that no consumer observes a terrain adjustment exceeding its kind's bound.

### Requirement 10: Unified Terrain Template

**User Story:** As a game designer, I want every planet built from the same terrain archetypes, so that base-building philosophy is consistent across planets while each planet keeps a distinct biome skin and signature resource.

#### Acceptance Criteria

1. THE terrain definitions SHALL give every planet at least three COMMON terrains whose vision, movement, and defense modifiers are all zero, and whose combined map weight is 40–50% of that planet's terrain distribution.
2. THE COMMON terrains of each planet, taken together, SHALL supply the Wood, Stone, and Iron resources that building construction costs consume, so that a base can be built on every planet.
3. THE terrain definitions SHALL give every planet one SIGHT terrain (positive vision, negative movement), one COVER terrain (negative vision, positive movement), one FORTRESS terrain (high positive defense, negative vision), one OPEN terrain (positive vision, negative defense), and at least one TREACHEROUS terrain (negative vision, movement, and defense).
4. THE Biomass resource SHALL be produced only by the Terra Dirt terrain and by no other terrain on any planet.
5. WHERE a terrain type is shared across more than one planet (a single TerrainDef referenced by multiple planets), THE shared TerrainDef SHALL carry a single set of modifiers and a single resource assignment used identically on every planet that references it.
6. THE resource assigned to each terrain SHALL be thematically consistent with that terrain (for example, Stone from rock/masonry tiles, Iron from ore/scrap/mountain tiles, Wood from forest/timber/salvage tiles) and SHALL NOT assign a resource that contradicts the terrain's nature.

### Requirement 11: Terrain Build Restriction

**User Story:** As a player, I want most buildings blocked from hostile terrain, so that hazardous tiles are a real placement constraint rather than free ground.

#### Acceptance Criteria

1. THE TerrainDef structure SHALL carry a boolean ``buildable`` field, defaulting to true when the terrain definitions file omits it, and loaded via the registry facade like other TerrainDef fields.
2. IF a terrain definition's ``buildable`` field is present and is not a boolean, THEN THE DataRegistry SHALL report a validation error identifying the terrain type and fail the load, consistent with the existing terrain validation.
3. WHEN a player attempts to construct a building on a tile whose resolved terrain has ``buildable`` false, THE BuildingSystem SHALL reject the construction with a message naming the terrain, and SHALL make no resource deduction or state change.
4. WHEN a player attempts to construct a building on a tile whose resolved terrain has ``buildable`` true, THE BuildingSystem SHALL apply its existing validation chain unchanged (the buildable check adds a restriction, never relaxes one).
5. IF the terrain type at the target tile cannot be resolved, or has no TerrainDef in the registry, THEN THE BuildingSystem SHALL treat the tile as buildable (fail-open), so legacy rooms and test doubles are unaffected.
6. THE TREACHEROUS terrain of every planet (Requirement 10 criterion 3) SHALL have ``buildable`` false, and every COMMON, SIGHT, COVER, FORTRESS, and OPEN terrain SHALL have ``buildable`` true.
