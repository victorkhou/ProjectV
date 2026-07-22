# Implementation Plan: Refactor Foundations

## Overview

Stages 1–2 of the hardening program for the Evennia MUD in `mygame/`: comment hygiene sweep
(Stage 1a), three shared helpers (Stage 1b), and the services facade with `ndb.systems`
retirement (Stage 2). The task order follows the design's Migration Strategy exactly — every
step ends with the full suite green (`python -m pytest mygame -q` from the repo root; zero
failures, zero errors; ≥2783 passing from Stage 1b on), and a stage does not begin until the
previous stage's gate passes. Every modified line stays ≤100 characters (`.flake8`).

Property-based tests use Hypothesis with `@settings(max_examples=100)` or higher, are
colocated in `mygame/world/tests/`, and each carries the tag comment
`# Feature: refactor-foundations, Property N: <title>`.

## Tasks

- [x] 1. Stage 1a: Comment hygiene sweep
  - [x] 1.1 Sweep the ~48 historical comments in production code
    - Locate historical comments via the design's search patterns ("pre-fix", "the reported
      bug", "TOCTOU", "belt-and-braces", "the removed per-tile building attribute",
      "FIXED by", "saw coord_x=None during create_object", etc.)
    - Apply the Component 1 decision procedure per comment: surviving constraint → rewrite as
      present-tense Rationale_Comment; pure history → delete (whole line when comment-only);
      ambiguous → rewrite rather than delete
    - Escape hatch: leave unchanged any comment/docstring asserted by a test; record each
      exception as a `file:line — reason` entry for the Stage 1a commit message
    - Retain every pre-existing Rationale_Comment with its constraint intact
    - Touch only comment and docstring text — zero changes to executable code, and no
      attribute-access-form changes on comment-only edits
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 8.4_

  - [x] 1.2 Run Stage 1a verification gates
    - Write the throwaway AST-equivalence script (not committed): for each touched file,
      parse before (`git show HEAD:...` / `git stash` copy) and after sources, normalize
      docstring nodes of Module/ClassDef/FunctionDef/AsyncFunctionDef bodies to a fixed
      placeholder, assert `ast.dump(before) == ast.dump(after)`
    - Grep gate: historical-comment search patterns return zero hits outside recorded
      exceptions
    - Run `python -m pytest mygame -q`; require zero failing and zero erroring tests
    - _Requirements: 1.1, 1.6, 1.7, 9.1_

- [x] 2. Checkpoint — Stage 1a complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Stage 1b step 1: Player_Resolver
  - [x] 3.1 Add `resolve_player` to `world/utils.py`
    - Implement per Component 2: signature
      `resolve_player(caller, name, *, global_search=False, not_found_msg="Could not find
      player '{name}'.", empty_name_msg=None)`
    - Empty-name short-circuit only when `empty_name_msg` is set; `hasattr(caller, "search")`
      guard for test doubles; local vs global search call shapes preserved exactly;
      `not_found_msg` formatted and sent on a None result when set; None return as the
      failure indication, never an unhandled exception
    - _Requirements: 2.1, 2.6, 2.8_

  - [x] 3.2 Write unit tests for `resolve_player` input classes
    - New file `mygame/world/tests/test_resolve_player.py`: fake caller with scripted
      `search` and recorded `msg`
    - Cover the four input classes (exactly-one-match, no-match, multiple-match via
      search-returns-None, empty/missing name) under both parameter profiles (router
      defaults, alliance kwargs), plus scope-forwarding assertions on recorded `search`
      kwargs (mandated by Requirement 2.7 — not optional)
    - _Requirements: 2.3, 2.7, 2.8_

  - [x] 3.3 Write property test for resolver totality
    - **Property 1: Resolver totality — unresolvable input never raises**
    - New file `mygame/world/tests/test_prop_resolve_player.py`; Hypothesis, min 100
      iterations; tag `# Feature: refactor-foundations, Property 1: Resolver totality —
      unresolvable input never raises`
    - Generate arbitrary name strings (empty, whitespace, unicode), `global_search` values,
      and message-parameter combinations; callers whose `search` returns None and callers
      without `search`; assert None return, no exception, and exact message contract
    - **Validates: Requirements 2.6**

  - [x] 3.4 Convert the 8 `admin_commands.py` call sites and delete the router method
    - Convert `self.resolve_player(player_name)` sites in `commands/admin_commands.py` to
      `resolve_player(self.caller, player_name)` (helper defaults reproduce router behavior)
    - Delete `AdminSubcommandRouter.resolve_player` from `commands/command_router.py`
    - _Requirements: 2.2, 2.3, 2.4_

  - [x] 3.5 Convert the 6 `alliance_commands.py` call sites and delete `_resolve_player`
    - Convert each site to `resolve_player(self.caller, args.strip(), global_search=True,
      not_found_msg=None, empty_name_msg="Specify a player by name.")`
    - Delete the local `_resolve_player` function
    - Gate: inspect both files for zero local resolver definitions; run
      `python -m pytest mygame -q` with zero failures/errors and ≥2783 passing
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.8_

- [x] 4. Stage 1b step 2: Coordinate_Accessor
  - [x] 4.1 Add `coords_of` to `world/utils.py` and re-point `get_coords`
    - Implement `coords_of(entity)` per Component 3: defensive nested-getattr read (the one
      sanctioned home of that pattern), returns `(x, y, planet)` with `planet` None when
      `coord_planet` unset, returns None when `db` is absent or either coordinate is
      absent/None, never raises, no coercion
    - Rewrite `get_coords(obj)` as a thin delegate returning `(int(x), int(y))` or None,
      preserving its exact existing contract
    - _Requirements: 3.1, 3.2, 3.3, 3.9_

  - [x] 4.2 Write property test for coords_of totality
    - **Property 2: coords_of correctness and None-safety across arbitrary entity shapes**
    - New file `mygame/world/tests/test_prop_coords_of.py`; Hypothesis, min 100 iterations;
      tag `# Feature: refactor-foundations, Property 2: coords_of correctness and
      None-safety across arbitrary entity shapes`
    - Generate entity shapes: no `db`, `db=None`, namespace `db` with any subset of
      `coord_x`/`coord_y`/`coord_planet` drawn from ints and None; assert no exception,
      `(x, y, planet)` exactly when both coordinates present and non-None, None otherwise
    - **Validates: Requirements 3.2, 3.3**

  - [x] 4.3 Write property test for get_coords projection
    - **Property 3: get_coords is the (x, y) projection of coords_of**
    - Add to `mygame/world/tests/test_prop_coords_of.py`; Hypothesis, min 100 iterations;
      tag `# Feature: refactor-foundations, Property 3: get_coords is the (x, y) projection
      of coords_of`
    - Reuse the Property 2 entity generator; assert `get_coords(entity)` equals
      `(int(x), int(y))` when `coords_of` returns a triple and None exactly when it returns
      None
    - **Validates: Requirements 3.9**

  - [x] 4.4 Convert the ~80 None-default coordinate-read sites file by file
    - In scope: ~42 direct `getattr(entity.db, "coord_x", None)`, ~27 nested
      `getattr(getattr(entity, "db", None), "coord_x", None)`, ~11 variants — convert to
      `coords_of(entity)` per the Component 3 conversion shapes (bind `_planet` or the third
      element as needed; presence checks use `coords_of(entity) is None`)
    - Leave non-None-default sites (`0`, `"?"` defaults) byte-identical
    - Flag for explicit review any site that reads a single coordinate independently rather
      than both-or-nothing (convert its read to `entity.db.coord_x` form per the
      Attribute_Convention only when its line is touched; do not silently change its miss
      condition)
    - Apply the Attribute_Convention to every modified executable line (static key +
      None/no default → `obj.db.x`; non-None default or dynamic key →
      `obj.attributes.get(...)`), verifying present/absent-value equivalence
    - Run the suite after each file or small batch to localize regressions
    - _Requirements: 3.4, 3.5, 3.6, 8.1, 8.2, 8.3, 8.5, 8.6_

  - [x] 4.5 Run coordinate conversion gates
    - Grep gate: zero direct or nested `coord_x`-with-None-default read patterns outside
      `coords_of`'s own implementation
    - Run `python -m pytest mygame -q`; zero failures/errors, ≥2783 passing
    - _Requirements: 3.7, 3.8_

- [x] 5. Stage 1b step 3: Single in-combat check
  - [x] 5.1 Convert the 3 combat-read gate sites to `player_in_combat`
    - Convert `commands/game_commands.py:438` (wall gate, `> 0` form), `:573` (move lag),
      and `:4604` (`CmdRecall` — keep the recall-blocked-during-combat message) to call
      `world/combat_timer.player_in_combat`
    - Leave all writes to `db.combat_timer_expires` unchanged (deploy-time reset in
      `commands/lifecycle_commands.py`, per-tick reset in `decrement_combat_timers` at
      `typeclasses/scripts.py:567` — Requirement 4.4), and leave non-boolean raw reads
      unchanged (status-display remaining-time read at `commands/game_commands.py:2737` —
      Requirement 4.5)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 5.2 Write property test for player_in_combat equivalence
    - **Property 4: player_in_combat is equivalent to the reference comparison, failing
      closed**
    - New file `mygame/world/tests/test_prop_player_in_combat.py`; Hypothesis, min 100
      iterations; tag `# Feature: refactor-foundations, Property 4: player_in_combat is
      equivalent to the reference comparison, failing closed`
    - Generate expiry values from {unset, None, 0, negative ints, positive ints} and
      current-tick values; patch the tick source (`_get_current_tick`) in returning and
      raising modes; assert `expiry_or_zero > 0 and expiry > current_tick` (returning mode),
      `expiry_or_zero > 0` (raising mode), and False for a char with no `db`
    - **Validates: Requirements 4.3, 4.6**

  - [x] 5.3 Run combat conversion gates
    - Grep gate: no `combat_timer_expires`-vs-tick comparison outside
      `world/combat_timer.py`; write sites and display reads byte-identical
    - Run `python -m pytest mygame -q`; exit status 0, zero failures, zero collection
      errors, ≥2783 passing
    - _Requirements: 4.7, 4.8, 9.2_

- [x] 6. Checkpoint — Stage 1b complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Stage 2 step 1–2: Services facade and composition-root wiring
  - [x] 7.1 Create `world/services.py`
    - Implement per Component 5: module-level `_systems: dict[str, Any] | None = None`
      (None distinguishes never-installed from installed-empty); `install(systems)` storing
      by reference; `get_service(name)` (None pre-install and for absent keys);
      `get_systems()` (empty dict pre-install); `get_registry()`; `get_balance()` (plain
      `getattr` on the registry object — not an Evennia persistent attribute);
      `override(systems)` context manager with snapshot/restore in `finally`
    - Stdlib-only imports; zero imports from `server.conf.game_init` — nothing imports the
      facade yet, so this step is zero behavior change
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6, 5.7, 5.8, 5.9_

  - [x] 7.2 Write unit tests for the facade named accessors
    - New file `mygame/world/tests/test_services.py`
    - `get_registry` present/absent; `get_balance` with a registry carrying `balance`, a
      registry without it, and no registry; pre-install behavior of every accessor
    - _Requirements: 5.4, 5.6, 5.7, 5.8_

  - [x] 7.3 Write property test for facade install/get round-trip
    - **Property 5: Facade install/get round-trip with replacement**
    - New file `mygame/world/tests/test_prop_services.py`; Hypothesis, min 100 iterations;
      tag `# Feature: refactor-foundations, Property 5: Facade install/get round-trip with
      replacement`
    - Generate sequences of installed dicts ending in `d` and probe keys (from `d`, earlier
      dicts, fresh); assert `get_service(key) is d[key]` when present else None (earlier
      dicts' unique keys return None, proving replacement); `get_systems()` returns `d`
      itself; pre-install `get_service` is None; snapshot/restore facade state around each
      example via `services.override`
    - **Validates: Requirements 5.2, 5.3, 5.6, 7.7**

  - [x] 7.4 Write property test for override restore round-trip
    - **Property 8: override restore round-trip**
    - Add to `mygame/world/tests/test_prop_services.py`; Hypothesis, min 100 iterations;
      tag `# Feature: refactor-foundations, Property 8: override restore round-trip`
    - Generate prior states (never-installed or arbitrary dict) and injected dicts; exit
      both normally and via a raised exception; assert facade state is identical (same
      object, including never-installed None) after exit and that `get_service` reflects
      the injected dict inside the body
    - **Validates: Requirements 7.9**

  - [x] 7.5 Wire `services.install(systems)` into `initialize_game()`
    - In `server/conf/game_init.py`, after the systems dict is fully populated and before
      `initialize_game()` returns: `from world import services; services.install(systems)`
    - Keep the module-level `game_systems` dict and the line-794 `ndb.systems` wiring in
      place for now (sanctioned dual-path interim state)
    - Run `python -m pytest mygame -q`; zero failures/errors
    - _Requirements: 5.5, 7.2_

- [x] 8. Stage 2 step 3: Migrate the 25 inline game_systems imports
  - [x] 8.1 Convert all inline `game_systems` imports outside the composition root to facade
        accessors
    - Per Component 9: replace each try/except inline-import block with a module-level
      `from world.services import get_service` (or `from world import services`) and a
      direct `get_service("name")` call; use named accessors (`get_registry`,
      `get_balance`) where the site reads those; whole-dict fetches use `get_systems()`
    - The facade's None return covers the previous `ImportError`/`AttributeError`/key-miss
      fallbacks — each site's existing None-handling stays unchanged
    - Apply the Attribute_Convention to any modified executable line that reads a persistent
      attribute
    - Run `python -m pytest mygame -q`; zero failures/errors, ≥2783 passing (the
      `ndb.systems` test-injection path is unaffected because these sites bypass
      `get_system`)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 8.1, 8.2, 8.3_

- [x] 9. Stage 2 steps 4–5: Test injection migration and ndb.systems retirement
  - [x] 9.1 Migrate the five injecting test files to `services.override`
    - Files: `commands/tests/test_travel_commands.py`,
      `commands/tests/test_game_commands.py`, `commands/tests/test_admin_routers.py`,
      `tests/test_live_boot_smoke.py`, `world/systems/tests/test_combat_engine.py`
    - unittest-style: enter `services.override({...})` in `setUp` and register the exit via
      `self.addCleanup` (guarantees restore even on partial setUp failure); pytest-style:
      fixture wrapping `with services.override({...})`
    - Fake system objects and `_NDB`/caller doubles keep their shapes; only the injection
      mechanism changes
    - `tests/test_live_boot_smoke.py` wraps its `initialize_game()` run in
      `services.override({})` (or equivalent snapshot/restore) so the boot's install does
      not leak past the test, and verifies `initialize_game()` installs into the facade
    - Run `python -m pytest mygame -q`; zero failures/errors, ≥2783 passing
    - _Requirements: 6.5, 7.4, 7.9, 9.5, 9.6_

  - [x] 9.2 Atomic ndb retirement: rewire lookup helpers, tick script, and composition root
    - In `world/utils.py` (Component 7): add module-level `from world import services`;
      rewrite `get_system(caller, system_name)` to `return
      services.get_service(system_name)` keeping the two-argument signature (`caller`
      unused, retained for compatibility); rewrite `get_game_systems()` to `return
      services.get_systems()`; leave `require_system` unchanged (its "{label} unavailable."
      message and default-label derivation already satisfy the requirement)
    - In `typeclasses/scripts.py` (Component 8): rewrite `GameTickScript._get_systems` to
      fetch `get_systems()` from the facade and `return systems or None` (preserving the
      falsy-skip contract); the dead `db.systems` fallback disappears
    - In `server/conf/game_init.py`: delete line 794 (`tick_script.ndb.systems = systems`)
    - All three edits land as one step so the tick script's systems source switches
      atomically
    - Run `python -m pytest mygame -q`; exit status 0, zero failures/errors, ≥2783 passing
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7, 7.8_

  - [x] 9.3 Write property test for get_system/get_service agreement
    - **Property 6: get_system agrees with get_service and ignores ndb.systems**
    - New file `mygame/world/tests/test_prop_system_lookup.py`; Hypothesis, min 100
      iterations; tag `# Feature: refactor-foundations, Property 6: get_system agrees with
      get_service and ignores ndb.systems`
    - Generate installed mappings, probe names (present/absent), and caller doubles
      including ones whose `ndb.systems` holds same-named decoy systems; assert
      `get_system(caller, name)` returns the identical object `get_service(name)` returns
      and never a decoy; snapshot/restore via `services.override`
    - **Validates: Requirements 7.1, 7.2**

  - [x] 9.4 Write property test for require_system failure message
    - **Property 7: require_system failure message format**
    - Add to `mygame/world/tests/test_prop_system_lookup.py`; Hypothesis, min 100
      iterations; tag `# Feature: refactor-foundations, Property 7: require_system failure
      message format`
    - Generate system names (letters and underscores) with no installed system and optional
      labels; assert None return and exactly one message: `f"{label} unavailable."` with a
      label, `f"{name.replace('_', ' ').capitalize()} unavailable."` without
    - **Validates: Requirements 7.5**

  - [x] 9.5 Write unit test for the GameTickScript._get_systems contract
    - New file `mygame/world/tests/test_tick_script_systems.py` (or extend the existing
      tick-script test module)
    - Assert `_get_systems()` returns the installed dict when non-empty and None when the
      facade is empty or not installed
    - _Requirements: 7.8_

- [x] 10. Stage 2 final gates
  - [x] 10.1 Run the final grep gates and full suite
    - Grep gates: zero `from server.conf.game_init import game_systems` outside
      `server/conf/game_init.py`; zero `ndb.systems` reads/writes for system lookup;
      `world/services.py` contains zero `server.conf` imports
    - Run `flake8` (or verify manually) that every modified line is ≤100 characters
    - Run `python -m pytest mygame -q`; zero failures, zero errors, ≥2783 passing; confirm
      no pre-existing test was deleted, skipped, or weakened beyond the five sanctioned
      injection-mechanism migrations
    - _Requirements: 5.9, 6.1, 6.6, 7.2, 7.3, 7.6, 9.3, 9.5, 9.6, 9.7_

- [x] 11. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP; task 3.2 (resolver
  unit tests) is NOT optional because Requirement 2.7 mandates those tests in the Test_Suite
- The stage/step ordering is mandatory (design Migration Strategy): each numbered step ends
  with the full suite green before the next begins, and interim dual-path states
  (`ndb.systems` alive through tasks 7.x–9.1) are sanctioned by Requirement 7.2
- Property tests use Hypothesis, min 100 iterations, tag format
  `# Feature: refactor-foundations, Property N: <title>`, colocated in `mygame/world/tests/`
- Site-conversion parity (Requirements 3.6, 6.3, 6.4) and the comment sweep are deliberately
  verified by the existing 2783-test suite plus the mechanical AST/grep gates, not by new
  properties
- Every modified line stays ≤100 characters per `.flake8` (Requirement 9.7)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5"] },
    { "id": 4, "tasks": ["4.1"] },
    { "id": 5, "tasks": ["4.2", "4.4"] },
    { "id": 6, "tasks": ["4.3", "4.5"] },
    { "id": 7, "tasks": ["5.1"] },
    { "id": 8, "tasks": ["5.2", "5.3"] },
    { "id": 9, "tasks": ["7.1"] },
    { "id": 10, "tasks": ["7.2", "7.3", "7.5"] },
    { "id": 11, "tasks": ["7.4", "8.1"] },
    { "id": 12, "tasks": ["9.1"] },
    { "id": 13, "tasks": ["9.2"] },
    { "id": 14, "tasks": ["9.3", "9.5"] },
    { "id": 15, "tasks": ["9.4"] },
    { "id": 16, "tasks": ["10.1"] }
  ]
}
```
