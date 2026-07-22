# Requirements Document

## Introduction

This spec covers stages 1–2 of a codebase hardening program for the Evennia-based RTS/combat MUD
in `mygame/` (~44k lines). A principal-engineer review identified systemic weaknesses; this spec
addresses only the low-risk, mechanical foundations:

- **Stage 1a — Comment hygiene sweep**: rewrite or delete ~48 production-code comments that
  narrate development history and tribal knowledge instead of documenting present-tense intent.
- **Stage 1b — Small shared reusable helpers**: consolidate two duplicated player-by-name
  resolution implementations, introduce a coordinate accessor helper, and route all raw
  in-combat reads through the existing `player_in_combat` helper.
- **Stage 2 — Services facade**: introduce `world/services.py` as the single accessor for
  installed game systems, migrate all inline `game_systems` imports (currently 25) to it, and
  retire the per-character `ndb.systems` lookup path.

Later stages of the hardening program (strict exception policy, FakeEntity test-double
migration, constructor injection, `game_commands` split) are explicitly out of scope and will be
separate specs; this spec lays the ground they build on. The overriding invariant is zero
player-visible behavior change: the full test suite (2783 tests, run via
`python -m pytest mygame -q` from the repo root) must remain green after each stage.

## Glossary

- **Codebase**: The game source under `mygame/` in the ProjectV repository, excluding test files
  unless a requirement states otherwise.
- **Production_Code**: Files under `mygame/` that are not test files (not under test directories
  and not named `test_*.py`).
- **Historical_Comment**: A comment in Production_Code that references past code iterations, old
  bugs, fix narratives, or removed code (e.g. "pre-fix", "the reported bug", "FIXED by",
  "the removed per-tile building attribute"), and is not understandable without knowledge of
  prior versions of the code.
- **Rationale_Comment**: A comment that documents a present-tense invariant or design constraint
  that restricts how future code may change (e.g. "deliberately does NOT publish
  BUILDING_DESTROYED — that event triggers base-elimination").
- **Player_Resolver**: The single shared helper in `world/utils` that resolves a player
  character by name, replacing the two duplicate implementations in
  `commands/command_router.py` (`resolve_player`) and `commands/alliance_commands.py`
  (`_resolve_player`). The Player_Resolver accepts a `global_search: bool = False` parameter
  selecting Evennia's local (default) or global search scope, because the two call sites
  deliberately use different scopes.
- **Coordinate_Accessor**: The helper in `world/utils` (e.g. `coords_of(entity)`) that returns
  an entity's overworld coordinates as `(x, y, planet)` or `None` when coordinates are absent.
  The existing `get_coords(obj)` helper in `world/utils` (which returns `(x, y)` without the
  planet) is either absorbed into the Coordinate_Accessor or delegates to it.
- **Combat_Check_Helper**: The existing function `world/combat_timer.player_in_combat`, the
  single authoritative read-side check for whether a character is in combat.
- **Services_Facade**: The new module `world/services.py` that holds the installed systems dict
  and exposes accessor functions (e.g. `get_service(name)`, `get_registry()`, `get_balance()`)
  plus an `install(systems: dict)` function.
- **Composition_Root**: The function `initialize_game()` in `server/conf/game_init.py`, the one
  place where game systems are constructed and wired together.
- **System_Lookup_Helpers**: The functions `get_system`, `get_game_systems`, and
  `require_system` in `world/utils`.
- **Ndb_Systems_Path**: The legacy lookup mechanism where `get_system` checks
  `caller.ndb.systems` before falling back to the global `game_systems` dict.
- **Attribute_Convention**: The binding rule that `obj.db.x` is the primary way to read Evennia
  persistent attributes, with `attributes.get()` reserved for non-None defaults or dynamic keys.
- **Test_Suite**: The full pytest suite run via `python -m pytest mygame -q` from the repo root,
  currently 2783 passing tests.
- **Developer**: A maintainer of the Codebase.

## Requirements

### Requirement 1: Comment Hygiene Sweep

**User Story:** As a Developer, I want production-code comments to state present-tense intent
and invariants instead of development history, so that I can understand the code without
knowledge of past iterations, old bugs, or tribal knowledge.

#### Acceptance Criteria

1. WHEN the sweep is complete, THE Production_Code SHALL contain zero Historical_Comments in
   both comment text and docstring text, except any Historical_Comment whose removal or rewrite
   would cause a test failure; THE Developer SHALL record each such exception.
2. IF a Historical_Comment describes a constraint that still restricts future changes, THEN THE
   Developer SHALL rewrite the comment as a present-tense Rationale_Comment that preserves the
   constraint's meaning.
3. IF a Historical_Comment references removed code, an old bug, or a fix narrative and carries
   no surviving constraint, THEN THE Developer SHALL delete the comment.
4. IF it is ambiguous whether a Historical_Comment carries a surviving constraint, THEN THE
   Developer SHALL rewrite the comment as a Rationale_Comment rather than delete it.
5. WHEN the sweep is complete, THE Production_Code SHALL retain every Rationale_Comment that
   existed before the sweep, and each retained Rationale_Comment SHALL state the same
   constraint that it stated before the sweep.
6. WHEN the sweep is complete, THE Production_Code SHALL differ from its state immediately
   before Stage 1a began only in comment text, docstring text, and whole-line deletions of
   comment-only lines, with zero changes to executable code.
7. WHEN the sweep is complete, THE Test_Suite SHALL pass with zero failing tests and zero
   erroring tests.

### Requirement 2: Shared Player-by-Name Resolution Helper

**User Story:** As a Developer, I want a single shared player-by-name resolution helper, so that
name lookup behavior is defined in one place instead of two divergent copies.

#### Acceptance Criteria

1. THE Codebase SHALL provide the Player_Resolver as exactly one implementation, located in
   `world/utils`.
2. WHEN a caller in `commands/command_router.py` or `commands/alliance_commands.py` resolves a
   player by name, THE caller SHALL invoke the Player_Resolver rather than a locally defined
   resolution routine.
3. WHEN the Player_Resolver is adopted at a call site AND the call site passes the search scope
   matching its previous local implementation (local scope for `commands/command_router.py`,
   global scope for `commands/alliance_commands.py`), THE call site SHALL produce the same
   resolution result (the same resolved player object, or the same failure indication) and the
   same caller-visible messages as its previous local implementation for each of the following
   input classes: a name matching exactly one player, a name matching no player, a name matching
   more than one player, and an empty or missing name.
4. WHEN the consolidation is complete, THE Codebase SHALL contain zero player-by-name resolution
   function definitions in the `mygame` directory outside the Player_Resolver, verified by
   inspection of `commands/command_router.py` and `commands/alliance_commands.py`.
5. WHEN the consolidation is verified, THE Test_Suite (`python -m pytest mygame -q`) SHALL
   complete with zero failed tests and zero errored tests.
6. IF a given name cannot be resolved to a player, THEN THE Player_Resolver SHALL return a
   failure indication to its caller without raising an unhandled exception.
7. WHEN the Player_Resolver is added, THE Test_Suite SHALL include unit tests for the
   Player_Resolver covering the exactly-one-match, no-match, multiple-match, and empty-name
   input classes.
8. THE Player_Resolver SHALL accept a `global_search` parameter that defaults to False and
   selects between Evennia's local and global search scopes, and each converted call site
   SHALL pass the search scope that its previous local implementation used.

### Requirement 3: Coordinate Accessor Helper

**User Story:** As a Developer, I want a single coordinate accessor helper, so that the ~80
scattered coordinate-read sites in Production_Code (both the direct
`getattr(entity.db, "coord_x", None)` form and the nested
`getattr(getattr(entity, "db", None), "coord_x", None)` form) are replaced by one named,
self-documenting function.

#### Acceptance Criteria

1. THE Codebase SHALL provide the Coordinate_Accessor as a single implementation in
   `world/utils`.
2. WHEN the Coordinate_Accessor is called with an entity whose `coord_x` and `coord_y`
   persistent attributes are both non-None, THE Coordinate_Accessor SHALL return the tuple
   `(x, y, planet)` read from the entity's `coord_x`, `coord_y`, and `coord_planet` persistent
   attributes, where `planet` is the stored `coord_planet` value or `None` when `coord_planet`
   is unset.
3. IF the Coordinate_Accessor is called with an entity whose `coord_x` or `coord_y` persistent
   attribute is absent or None, or with an object that has no `db` attribute handler, THEN THE
   Coordinate_Accessor SHALL return `None` without raising an exception.
4. WHEN a Production_Code site reads an entity's coordinates with a `None` default via either
   the direct `getattr(entity.db, "coord_x", None)` form or the nested
   `getattr(getattr(entity, "db", None), "coord_x", None)` form, THE site SHALL be converted
   to call the Coordinate_Accessor.
5. THE conversion SHALL leave sites that read coordinate attributes with a non-None default
   (e.g. `getattr(entity.db, "coord_x", 0)` or a `"?"` default) unchanged.
6. WHEN a site is converted to the Coordinate_Accessor, THE site SHALL preserve its previous
   observable behavior for entities with both coordinates set, entities with either coordinate
   absent or None, and entities whose `coord_planet` is unset.
7. WHEN the adoption is complete, THE Production_Code SHALL contain zero occurrences of the
   direct `getattr(entity.db, "coord_x", None)` read pattern and zero occurrences of the
   nested `getattr(getattr(entity, "db", None), "coord_x", None)` read pattern outside the
   Coordinate_Accessor's own implementation.
8. WHEN the adoption is complete, THE Test_Suite SHALL pass with all tests passing.
9. WHEN the Coordinate_Accessor is added, THE existing `get_coords` helper in `world/utils`
   SHALL either be absorbed into the Coordinate_Accessor or delegate to it, so that the
   Codebase contains exactly one coordinate-reading implementation.

### Requirement 4: Single In-Combat Check

**User Story:** As a Developer, I want every read-side in-combat check to go through the
existing `player_in_combat` helper, so that the combat-timer tick comparison is implemented in
exactly one place.

#### Acceptance Criteria

1. WHEN a Production_Code site reads `db.combat_timer_expires` to produce a boolean in-combat
   decision that gates a command or action (whether implemented as an expiry-versus-tick
   comparison or as an expiry-greater-than-zero comparison), THE site SHALL be converted to
   call the Combat_Check_Helper.
2. WHEN `CmdRecall` in `commands/game_commands.py` checks whether a character is in combat, THE
   `CmdRecall` command SHALL delegate the check to the Combat_Check_Helper instead of
   re-implementing the tick comparison, and SHALL continue to reject the recall with a message
   indicating recall is blocked during combat when the Combat_Check_Helper reports in-combat.
3. WHEN a site is converted to the Combat_Check_Helper and the current game tick is readable,
   THE site SHALL report not-in-combat for `db.combat_timer_expires` values of 0, unset, or
   None, and SHALL report in-combat for values strictly greater than the current game tick,
   matching the site's pre-conversion in-combat decision for those states.
4. THE conversion SHALL leave unchanged all sites that write `db.combat_timer_expires`,
   including the deploy-time reset in `commands/lifecycle_commands.py` and the per-tick expiry
   reset performed by the game tick script in `typeclasses/scripts.py`.
5. THE conversion SHALL leave unchanged sites that read the raw `db.combat_timer_expires` value
   for a purpose other than a boolean in-combat decision, such as the status display that
   computes remaining combat time from the expiry tick.
6. IF the current game tick cannot be read when the Combat_Check_Helper is invoked, THEN THE
   Combat_Check_Helper SHALL report in-combat for any positive `db.combat_timer_expires` value,
   so that converted gates fail closed.
7. WHEN the conversion is complete, THE Production_Code SHALL contain no site outside the
   Combat_Check_Helper that compares `db.combat_timer_expires` against the game tick to produce
   an in-combat decision.
8. WHEN the conversion is complete, THE Test_Suite (`python -m pytest mygame -q`) SHALL exit
   with status code 0, reporting zero test failures and zero collection errors.

### Requirement 5: Services Facade Module

**User Story:** As a Developer, I want a single services facade module that owns the installed
systems dict, so that system access has one well-defined entry point instead of scattered
imports of a mutable module-level dict.

#### Acceptance Criteria

1. THE Codebase SHALL provide the Services_Facade at `world/services.py`.
2. WHEN `install(systems)` is called, THE Services_Facade SHALL store the provided systems dict
   as the current installed systems mapping, replacing any previously installed mapping.
3. THE Services_Facade SHALL expose a `get_service(name)` accessor that returns the installed
   system registered under `name`, or `None` when no system is registered under `name`.
4. THE Services_Facade SHALL expose a `get_registry()` accessor that returns the installed
   system registered under the name `registry`.
5. WHEN `initialize_game()` finishes populating the systems dict, THE Composition_Root SHALL
   call `install(systems)` on the Services_Facade before `initialize_game()` returns.
6. IF `get_service(name)` is called before `install()` has run, THEN THE Services_Facade SHALL
   return `None`.
7. THE Services_Facade SHALL expose a `get_balance()` accessor that returns the balance
   configuration held by the installed registry.
8. IF a named accessor is called before `install()` has run, or when the system it depends on is
   absent from the installed mapping, THEN THE Services_Facade SHALL return `None`.
9. THE Services_Facade SHALL contain zero imports from `server.conf.game_init`.

### Requirement 6: Migration of Inline game_systems Imports

**User Story:** As a Developer, I want all inline `from server.conf.game_init import
game_systems` imports (currently 25) outside the composition root migrated to the
Services_Facade, so that production code depends on one accessor instead of reaching into the
init module.

#### Acceptance Criteria

1. WHEN the migration is complete, THE Production_Code outside the Composition_Root SHALL
   contain zero import statements, whether module-level or function-level, that import
   `game_systems` from `server.conf.game_init`.
2. WHEN Production_Code outside the Composition_Root retrieves an installed system, THE
   Production_Code SHALL obtain the system through the Services_Facade, using `get_service(name)`
   or a named accessor.
3. WHEN a migrated site retrieves a system that is installed in the Services_Facade, THE
   migrated site SHALL produce the same caller-visible result (return values and player-visible
   messages) that it produced before migration.
4. IF the Services_Facade returns `None` for a system requested by a migrated site, THEN THE
   migrated site SHALL produce the same caller-visible result that it produced before migration
   when the inline import raised `ImportError` or `AttributeError`, or when the requested key was
   absent from the `game_systems` dict.
5. WHEN a test previously injected systems by substituting the `server.conf.game_init` module or
   its `game_systems` dict, THE test SHALL inject systems by calling `install()` on the
   Services_Facade.
6. WHEN both the migration changes and a full Test_Suite execution have completed, THE
   Developer SHALL validate migration success against that Test_Suite result, and THE
   Test_Suite SHALL pass with zero failing tests, zero erroring tests, and at least 2783
   passing tests.

### Requirement 7: Retirement of the ndb.systems Lookup Path

**User Story:** As a Developer, I want the per-character `ndb.systems` lookup path removed, so
that system lookup has a single source of truth and tests inject systems through the same
facade production code uses.

#### Acceptance Criteria

1. WHEN the retirement is complete AND `install()` has been called on the Services_Facade with
   a systems dict, THE `get_system` function SHALL return, for any system name, the identical
   object that the Services_Facade `get_service(name)` returns for that name, including
   returning `None` for a name with no installed system.
2. WHEN the retirement is complete, THE `get_system` function SHALL contain zero reads of
   `caller.ndb.systems` AND SHALL preserve its existing two-argument public signature
   `get_system(caller, system_name)` so that existing call sites require no signature changes;
   WHILE the retirement is in progress, interim states MAY retain `caller.ndb.systems` reads.
3. WHEN the retirement is complete, THE Production_Code SHALL contain zero writes of
   `ndb.systems` for the purpose of system lookup, including removal of the tick-script wiring
   at `server/conf/game_init.py` line 794 (`tick_script.ndb.systems = systems`).
4. WHEN a test in the five files that previously injected systems via `ndb.systems`
   (`commands/tests/test_travel_commands.py`, `commands/tests/test_game_commands.py`,
   `commands/tests/test_admin_routers.py`, `tests/test_live_boot_smoke.py`,
   `world/systems/tests/test_combat_engine.py`) injects systems, THE test SHALL inject them by
   calling `install()` on the Services_Facade.
5. WHEN the retirement is complete, THE `require_system` function SHALL preserve its existing
   caller-visible failure behavior: messaging the caller with the format "{label} unavailable."
   where an omitted label defaults to the system name with underscores replaced by spaces and
   the first letter capitalized (e.g. "agent_system" → "Agent system unavailable."), and
   returning `None`.
6. WHEN the retirement is complete, THE Test_Suite (`python -m pytest mygame -q`) SHALL exit
   with status 0 and zero failed or errored tests.
7. WHEN the retirement is complete, THE `get_game_systems` function SHALL return the systems
   dict installed on the Services_Facade, and SHALL return an empty dict when `install()` has
   not run.
8. WHEN the tick script (`GameTickScript`) executes a tick after the retirement, THE tick
   script SHALL resolve each system it uses through the Services_Facade (directly or via the
   System_Lookup_Helpers) AND SHALL preserve its previous per-tick observable behavior, which
   requires rewriting `GameTickScript._get_systems()` in `typeclasses/scripts.py` to obtain
   systems from the Services_Facade instead of `ndb.systems`/`db.systems`.
9. WHEN a test calls `install()` on the Services_Facade, THE test SHALL restore the previously
   installed state during teardown, so that no injected system remains visible to any
   subsequently executed test.

### Requirement 8: Attribute Access Convention

**User Story:** As a Developer, I want a single binding convention for reading Evennia
persistent attributes in all code this spec touches, so that attribute access is uniform and
self-documenting.

#### Acceptance Criteria

1. WHEN a change made under this spec modifies the executable code of a line that reads a
   persistent attribute using a static string-literal key and either no default or a default of
   `None` (including reads in the `getattr(obj.db, "x", None)` and `obj.attributes.get("x")`
   forms), THE changed line SHALL read the attribute via the `obj.db.x` access form.
2. WHEN a change made under this spec modifies the executable code of a line that reads a
   persistent attribute with a default value other than `None`, or with a key that is not a
   string literal at the call site, THE changed line SHALL read the attribute via the
   `attributes.get()` access form with the default value or computed key passed explicitly.
3. THE Attribute_Convention SHALL apply only to lines whose executable code is modified by this
   spec's changes.
4. IF a line is modified only in its comment or docstring text, THEN THE line's attribute access
   form SHALL remain unchanged.
5. THE Developer SHALL NOT modify a line solely to apply the Attribute_Convention to code
   outside this spec's scope.
6. WHEN a line's attribute access form is converted to comply with the Attribute_Convention,
   THE converted line SHALL return the same value as the previous form both when the attribute
   is present and when the attribute is absent.

### Requirement 9: Behavior Preservation and Verification

**User Story:** As a Developer, I want each stage verified against the full test suite with zero
player-visible behavior change, so that this refactoring is safe to land incrementally.

#### Acceptance Criteria

1. WHEN Stage 1a is complete, THE Test_Suite SHALL pass with zero failing tests and zero
   erroring tests.
2. WHEN Stage 1b is complete, THE Test_Suite SHALL pass with zero failing tests, zero erroring
   tests, and at least 2783 passing tests.
3. WHEN Stage 2 is complete, THE Test_Suite SHALL pass with zero failing tests, zero erroring
   tests, and at least 2783 passing tests.
4. IF the Test_Suite reports any failing or erroring test for the current stage, THEN THE
   Developer SHALL NOT begin the next stage until the Test_Suite passes for the current stage
   with zero failing and zero erroring tests.
5. THE changes in this spec SHALL preserve all player-visible command output, game messages,
   and game-state transitions, verified by all pre-existing test assertions passing unmodified,
   except for the test injection-mechanism changes mandated by Requirement 7 criteria 4 and 9.
6. THE Developer SHALL NOT delete, skip, or weaken any pre-existing test to achieve a passing
   suite, except for the injection-mechanism changes mandated by Requirement 7.
7. THE changes in this spec SHALL keep every modified line at most 100 characters long, per the
   `max-line-length` limit defined in `.flake8`.
