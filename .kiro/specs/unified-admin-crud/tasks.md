# Implementation Plan — Unified Admin CRUD

## Overview

Unify admin CRUD around three pillars: a shared **EntityAdapter layer** (per-entity
descriptors: resolution, field schemas with bounds/perms, CRUD hooks into the real
system paths), an **enforced verb grammar** in a new `EntityAdminRouter` base class
(extending the existing `SubcommandDispatchMixin`/`AdminSubcommandRouter` in
`commands/command_router.py`), and **overlay-backed definition CRUD**
(`data/definitions_overrides.yaml` merged over base YAML inside the existing
`DataRegistry.load_all` → `SchemaValidator` → atomic-swap pipeline).

Built in the design's **phased migration plan** — every phase leaves the game working
with the full suite green, and existing admin tests keep passing via migration
aliases:

- **Phase 0**: scaffolding — adapter protocol, `AdapterRegistry` with startup
  verb-coverage enforcement, `EntityAdminRouter` base, `OverlayStore` + `load_all`
  merge step + serialization lock. No router behavior changes yet.
- **Phase 1**: `@item` pilot — the most complete existing router migrates first and
  validates the whole design end-to-end.
- **Phase 2**: coverage gaps — `@building`/`@agent` gain `show`+`set`; NEW `@tech`
  router.
- **Phase 3**: remaining routers — `@outpost`, `@alliance`, `@player`, `@stat`,
  `@resource`.
- **Phase 4**: def-only surfaces — NEW `@powerup`, `@terrain`, `@planet` (read-only).
- Finish: admin help entries for the new grammar + final integration pass.

Property-based tests use **Hypothesis with `max_examples=25`** (current project
convention — see `world/systems/tests/test_prop_loot_roller.py`,
`tests/test_prop_terrain_and_buildings.py`). The six correctness properties from the
design attach to the tasks implementing the relevant behavior.

New adapter-layer code lives in a new `world/admin/` package (pure addition to
`mygame/`; `world/adapters/` is taken by the existing hexagonal-port adapters and is
unrelated).

---

## Tasks

- [x] 1. Phase 0 — Scaffolding (adapter layer, router base, overlay store, merge step)

  - [x] 1.1 Create the `world/admin/` package with the adapter-layer core types
    - `world/admin/types.py`: frozen dataclasses `FieldSpec` (name, kind,
      min/max, `perm`, `dynamic_bounds`, `enum_values`), `InstanceRow`, `SetResult`
      (with the `clamped == (applied != requested)` contract), `ShowReport`
      (incl. `staleness_note`), and the `EntityAdapter` Protocol (CORE_VERBS
      constant, `supported_verbs`, `opt_outs`, `extra_verbs`, `aliases`,
      instance/definition field schemas, CRUD hooks, `def_registry_dict`,
      `def_resolve`)
    - Match the frozen-dataclass style of `world/data_registry.py`
    - _Requirements: 3.1_

  - [x] 1.2 Implement `AdapterRegistry` with startup verb-coverage enforcement
    - `world/admin/adapter_registry.py`: `register` raises at registration time
      when an adapter neither supports nor opts out (with a reason non-empty after
      trimming) of every core verb, naming each unaccounted-for verb; rejects
      extra-verb/alias names colliding with core verbs; `get`/`all` lookups
    - Registration happens at server start (wire from `server/conf/game_init.py`)
      so a bad adapter fails before its command is invocable
    - _Requirements: 1.1, 1.2, 1.3, 1.7_

  - [x] 1.3 Write property test for verb-grammar uniformity
    - **Property 4: Verb-grammar uniformity** — for every adapter in
      `AdapterRegistry.all()` (and for generated synthetic adapters), the adapter
      supports or explicitly opts out (non-empty reason) of every core verb;
      incomplete adapters are rejected at registration
    - Hypothesis, `max_examples=25`, in `world/admin/tests/test_prop_adapter_registry.py`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.5**

  - [x] 1.4 Implement the Resolution_Engine and List_Cache
    - `world/admin/resolution.py`: uniform token grammar — `#N` (1-based index
      into the caller's per-entity List_Cache, with out-of-range and no-cache
      errors), then case-sensitive exact key, case-insensitive exact name,
      case-insensitive prefix over keys+names; multiple candidates at the first
      matching tier → error listing all candidates, never a guess; no match →
      not-found error
    - Trailing `[player]` scoping (defaults to caller; unresolvable player token →
      error); def-scope resolution delegates to the existing `DataRegistry`
      resolvers (`resolve_item`, `resolve_building`, `resolve_technology`,
      `resolve_powerup` in `world/data_registry.py`)
    - Per-caller, per-entity-type List_Cache replaced on each `list`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 10.1, 10.2, 10.6, 10.7_

  - [x] 1.5 Write property test for resolution determinism
    - **Property 5: Resolution determinism** — for generated tokens, cached lists,
      and registry states, resolving the same token against the same state always
      yields the same result; a token matching multiple candidates always errors
    - Hypothesis, `max_examples=25`, in `world/admin/tests/test_prop_resolution.py`
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 10.1, 10.2**

  - [x] 1.6 Write unit tests for Resolution_Engine edge cases
    - `#0`, `#N` past end, stale cache, empty cache, ambiguous prefix listing,
      player-scoping default and unresolvable player
    - _Requirements: 2.3, 2.7, 2.8, 2.9, 10.1, 10.2_

  - [x] 1.7 Implement `OverlayStore`
    - `world/admin/overlay_store.py`: sole writer of
      `data/definitions_overrides.yaml` (header comment: managed by
      `def set`/`def reset`, do not hand-edit while server runs); `get`, `set`
      (replace, never duplicate), `reset` (field or whole key; error when no
      override exists), `diff` (domain→key→fields), `merge_into(raw, domain)`
    - Atomic writes (temp file + rename), pre-write snapshot + `restore_snapshot`
      for rollback; absent file → empty overlay; unparseable file → error and
      writes rejected until repaired; `yaml.safe_load` per the existing
      `data_registry.py` pattern
    - _Requirements: 5.1, 5.2, 5.3, 5.9, 5.10, 5.11_

  - [x] 1.8 Wire the overlay merge step into `DataRegistry.load_all` and add the serialization lock
    - In `world/data_registry.py`, apply `OverlayStore.merge_into(raw, domain)` to
      each raw YAML document after read and before `SchemaValidator`
      (`world/schema_validator.py`) runs — merged data goes through the same
      schemas and `cross_validate` with no rule relaxed; `reload_all`'s
      temp-registry + atomic-swap mechanism is reused unchanged
    - Single lock serializing overlay-write + reload sequences so concurrent
      `def set`/`def reset` commands queue in arrival order, each executing
      against its predecessor's resulting state
    - Extend the existing `DataRegistry` reload tests: overlay applied before
      validation; failed merged validation leaves the live registry untouched
    - _Requirements: 6.1, 6.2, 6.6_

  - [x] 1.9 Write property test for overlay round-trip
    - **Property 2: Overlay round-trip** — for generated sequences of valid
      `def set` operations (against a temp data directory), the merged registry
      reflects the last-set value flagged as overridden; a subsequent `def reset`
      restores exactly the base YAML value and clears the flag
    - Hypothesis, `max_examples=25`, in `world/admin/tests/test_prop_overlay.py`
    - **Validates: Requirements 5.2, 5.4, 5.5, 5.6**

  - [x] 1.10 Write property test for merged-validation atomicity
    - **Property 3: Merged-validation atomicity** — for generated override
      payloads (valid or invalid), either the reload succeeds and the merged value
      is live, or it fails and BOTH the live registry and the overlay file equal
      their pre-command state; no partial application
    - Hypothesis, `max_examples=25`, in `world/admin/tests/test_prop_overlay.py`
    - **Validates: Requirements 6.4, 6.5**

  - [x] 1.11 Implement `EntityAdminRouter` base — dispatch scaffolding, read verbs, aliases, opt-outs
    - New subclass of `AdminSubcommandRouter` in `commands/command_router.py`:
      auto-build the `subcommands` dict from the adapter's grammar contract
      (core verbs + `extra_verbs` + `aliases`); Builder floor unchanged
      (`locks = "cmd:perm(Builder)"`); read verbs (`list`, `show`, `def list`,
      `def show`, `def diff`) at Builder
    - Shared `list [filter]` handler: indexed rows, replaces the List_Cache
      (empty result → no-instances message + empty cache); shared `show` handler:
      identity header, state lines, modifiable-fields block rendered as
      `field: value [min–max] (perm)` with `*override*` flags
    - Alias dispatch to the canonical handler with identical state changes,
      perm outcomes, output, and audit entries, plus a one-line deprecation note
      naming both spellings; opted-out verb → declared reason + pointer to the
      supported path, no state change; unknown verb → error listing available
      verbs, no state change
    - _Requirements: 1.4, 1.5, 1.6, 1.8, 4.1, 4.3, 4.6, 8.1, 8.2, 11.1, 11.2_

  - [x] 1.12 Implement the shared mutating verbs — `spawn`, `set`, `destroy`
    - `spawn <def> [kwargs] [player]`: def-token resolution (error naming the
      token when unresolved, nothing created), create through the adapter's
      existing creation path, report the created identity; creation/deletion path
      failure → error, no further state change
    - `set <target> <field> <value>`: unknown field → error naming valid fields;
      kind-coercion failure → error stating expected kind; enum violation → error
      listing valid values; static/dynamic bounds computed from current entity
      state; out-of-bounds → clamp to nearest bound with a note stating applied
      value + bounds (D2); in-bounds/unbounded → apply unchanged; write through
      the entity's single-writer path, failure → error with pre-command state
      retained
    - `destroy <target>`: delete through the existing deletion path with
      confirmation of the destroyed instance; multi-target destroy shows
      count + identities and deletes nothing before explicit confirmation
    - Per-field perm escalation (checked after verb-level perm, before bounds);
      verb-tier overrides per adapter; insufficient tier → full rejection naming
      the required tier, no state change, nothing written to the overlay
    - Audit via the existing `_log_admin`: exactly one entry per successful
      mutation (operator, canonical verb, entity, target, and for field writes
      requested + applied values, distinguishable on clamp); audit-write failure
      leaves the mutation applied and notes the failure in the response
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 3.10, 4.2, 4.4, 4.5, 4.7, 4.8, 8.4, 8.5, 8.6, 8.7, 9.1, 9.3, 9.4_

  - [x] 1.13 Write property test for the bounded-set invariant
    - **Property 1: Bounded-set invariant** — for generated adapters, `FieldSpec`s
      (static, dynamic, and unbounded bounds), and input values, the applied value
      always lands within the field's bounds and `clamped` is true iff
      `applied != requested`
    - Hypothesis, `max_examples=25`, in `commands/tests/test_prop_admin_set.py`
    - **Validates: Requirements 3.2, 3.3, 3.4, 7.6**

  - [x] 1.14 Write property test for set idempotence at bounds
    - **Property 6: Set idempotence at bounds** — for generated fields and values,
      applying `set` twice with the same value yields the same final state as
      once (no cumulative drift through clamp/re-stamp paths)
    - Hypothesis, `max_examples=25`, in `commands/tests/test_prop_admin_set.py`
    - **Validates: Requirements 3.6, 7.6**

  - [x] 1.15 Implement the `def` sub-dispatch and the `def set`/`def reset` flow
    - `def list` (merged registry), `def show` (merged values, overridden fields
      flagged, live-instances-retain-stamped-values note), `def diff` (key/field/
      base/override rows; empty overlay → empty diff) at Builder; `def set`/
      `def reset` at Admin on every entity
    - `def set`: field must be in the adapter's definition Field_Spec schema
      (else error, overlay untouched); overlay write → serialized `reload_all`
      covering all domains → respond only after the outcome is known; success →
      atomic swap + before→after merged values reported; failure (validation,
      parse, or I/O) → live registry unchanged, overlay restored to pre-command
      snapshot, validator errors relayed; overlay-write failure → no reload,
      overlay unchanged, error returned
    - `def reset` with no existing override → error, overlay untouched
    - Audit entries record the reload outcome (applied, or rolled back)
    - _Requirements: 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 6.3, 6.4, 6.5, 6.7, 6.8, 8.3, 9.2_

  - [x] 1.16 Write unit tests for the `EntityAdminRouter` base
    - Alias dispatch + deprecation note, opt-out messaging, unknown-verb listing,
      `def` sub-dispatch, per-field perm escalation, bulk-destroy confirmation,
      audit-failure note — built on the `commands/tests/test_command_router.py`
      router-testing patterns, in `commands/tests/test_admin_routers.py`
    - _Requirements: 1.5, 1.8, 4.5, 8.4, 8.5, 9.4, 11.1, 11.2_

- [x] 2. Checkpoint — Phase 0
  - Ensure all tests pass (full suite: Phase 0 is additive, no router behavior
    changes; existing admin tests in `commands/tests/` must be untouched-green),
    ask the user if questions arise.

- [x] 3. Phase 1 — `@item` pilot

  - [x] 3.1 Implement `ItemAdapter`
    - `world/admin/adapters/item_adapter.py`: instance fields with dynamic bounds
      derived from `ItemDef.roll_spec` bands; every roll-field write re-stamps IQS
      through the existing `recompute_iqs` path in `world/systems/loot_roller.py`
      before the success response; player-scoped instance resolution (holdings,
      default caller); `def_registry_dict` → `DataRegistry.items`, `def_resolve` →
      `resolve_item`; definition Field_Spec schema against real `ItemDef` fields
    - Register in `AdapterRegistry` at startup
    - _Requirements: 2.4, 3.1, 3.4, 3.5, 7.6_

  - [x] 3.2 Migrate the `@item` router to `EntityAdminRouter`
    - In `commands/admin_commands.py`: `stats`→`show` alias; `list` now lists
      instances with the pointer that definition listing moved to `def list`
      (old def-list meaning moves to `def list`); full def scope; NEW `destroy`;
      existing `set` band-clamp + `recompute_iqs` behavior preserved through the
      shared handler; instance `show` staleness notes for stamped attributes
      differing from the current merged def
    - Existing `@item` tests in `commands/tests/test_admin_commands.py` /
      `test_admin_routers.py` keep passing via the aliases
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 7.6, 10.3, 10.4, 11.1, 11.2, 11.4, 11.5, 11.6_

  - [x] 3.3 Write unit tests for the `@item` pilot
    - IQS re-stamp on roll-field `set` (and its idempotence), dynamic-band clamp
      notes, `stats` alias output identical to `show` plus deprecation note,
      `list` instance rows + def-list pointer, staleness note rendering
    - _Requirements: 7.6, 10.3, 11.1, 11.2, 11.4_

  - [x] 3.4 Write integration tests for the definition plane end-to-end
    - `@item def set` → reload → instance lazy re-read via the `item_def`
      property picks up the override while stamped attributes stay unmodified;
      invalid override end-to-end (validator errors relayed, overlay rolled back,
      subsequent reload still clean); `def reset` restores base; `def diff`
    - In `commands/tests/test_admin_routers.py`
    - _Requirements: 5.5, 5.6, 6.4, 6.5, 10.5_

- [x] 4. Checkpoint — Phase 1
  - Ensure all tests pass (full suite green; live boot smoke via
    `tests/test_live_boot_smoke.py`), ask the user if questions arise.

- [x] 5. Phase 2 — Coverage gaps (`@building`, `@agent`, NEW `@tech`)

  - [x] 5.1 Implement `BuildingAdapter` and migrate the `@building` router
    - `world/admin/adapters/building_adapter.py`: NEW `show` and `set` (integer
      `level` Field_Spec with static bounds 1–5, `hp`, etc.), writes through the
      existing building system update paths; keep the `open` extra verb; old
      `list` def-meaning aliased to `def list` with the moved-to pointer; full
      def scope via `DataRegistry.buildings`/`resolve_building`
    - Migrate the router in `commands/admin_commands.py`; existing `@building`
      tests keep passing
    - _Requirements: 7.2, 11.4, 11.6_

  - [x] 5.2 Implement `AgentAdapter` and migrate the `@agent` router
    - `world/admin/adapters/agent_adapter.py`: NEW `show` and `set`; writes via
      `AgentSystem`; def scope opted out with the reason that agents have no YAML
      definition domain; `create`→`spawn` alias
    - Migrate the router (`commands/agent_commands.py`); existing tests in
      `commands/tests/test_agent_router.py` keep passing via the alias
    - _Requirements: 7.3, 11.5, 11.6_

  - [x] 5.3 Implement `TechnologyAdapter` and the NEW `@tech` router
    - `world/admin/adapters/tech_adapter.py` + router in
      `commands/admin_commands.py`: `list` of technologies granted to the
      trailing `[player]` (default caller); `grant` mapped to spawn — adds through
      the existing research path and recomputes derived tech bonuses before the
      success response; `revoke` mapped to destroy — removes + recomputes;
      already-granted grant / not-held revoke → error stating the player's
      current grant state, no state change; `show`; instance `set` opted out
      (no modifiable per-instance fields); full def scope via
      `DataRegistry.technologies`/`resolve_technology`
    - _Requirements: 7.1, 7.7, 7.8, 7.9_

  - [x] 5.4 Write unit tests for the Phase 2 routers
    - `@building set level` clamp at 1–5; `@agent create` alias equivalence +
      def-scope opt-out message; `@tech grant`/`revoke` round-trip with bonus
      recompute and double-grant/absent-revoke errors
    - In `commands/tests/test_admin_routers.py` / `test_agent_router.py`
    - _Requirements: 7.1, 7.2, 7.3, 7.9_

- [x] 6. Checkpoint — Phase 2
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Phase 3 — Remaining routers (`@outpost`, `@alliance`, `@player`, `@stat`, `@resource`)

  - [x] 7.1 Migrate the `@outpost` router
    - `OutpostAdapter`: `list` keeps its instance meaning; NEW `show`, `set`,
      `destroy` through the outpost spawner paths; `tiers`→`def list` alias; def
      scope over the base templates (tiers domain)
    - Router in `commands/admin_commands.py`
    - _Requirements: 11.5, 11.6_

  - [x] 7.2 Migrate the `@alliance` router
    - `AllianceAdapter`: `inspect`→`show`, `disband`→`destroy` aliases; NEW `set`
      writing exclusively through the `AllianceSystem` single-writer; `spawn`
      opted out (alliances are founded by players — pointer to the player-facing
      path); no YAML defs (perks catalog read-only via `def list`); keep `kick`,
      `transfer`, `rename` extra verbs
    - Router in `commands/alliance_commands.py`; existing tests in
      `commands/tests/test_alliance_commands.py` keep passing via aliases
    - _Requirements: 1.5, 3.5, 11.5, 11.6_

  - [x] 7.3 Migrate the `@player` router
    - `PlayerAdapter`: NEW `show`; `set` with `level` (static bounds 1–100) and
      `rank` (enum Field_Spec); old `level`/`rank` verb forms aliased to their
      `set` equivalents; `spawn` opted out (players register); `destroy` opted
      out with a pointer to the existing `obliterate` flow
    - Router in `commands/admin_commands.py`
    - _Requirements: 1.5, 11.5, 11.6_

  - [x] 7.4 Migrate the `@stat` router
    - `StatAdapter`: `show` + `set` over the EXISTING allowlisted fields; old
      `hp`/`maxhp`/`xp` verb forms kept as aliases to `set <target> <field>`;
      `list`/`spawn`/`destroy`/def scope opted out with reasons
    - Router in `commands/admin_commands.py`
    - _Requirements: 1.5, 11.5, 11.6_

  - [x] 7.5 Migrate the `@resource` router
    - `ResourceAdapter`: `give`→`spawn` alias (grant semantics); NEW `show`
      (balances readout); `set` on balances; keep the `reset` extra verb;
      `list`/`destroy`/def scope opted out with reasons
    - Router in `commands/admin_commands.py`
    - _Requirements: 1.5, 11.5, 11.6_

  - [x] 7.6 Write unit tests for the Phase 3 routers
    - Alias equivalence + deprecation notes for all five routers; `@alliance`
      writes observed through `AllianceSystem`; `@player level` clamp at 1–100 and
      rank enum error; opt-out reasons surfaced verbatim
    - In `commands/tests/test_admin_routers.py` / `test_alliance_commands.py`
    - _Requirements: 3.9, 11.1, 11.2, 11.5_

- [x] 8. Phase 4 — Def-only surfaces (`@powerup`, `@terrain`, `@planet`)

  - [x] 8.1 Implement the NEW `@powerup` and `@terrain` def-only routers
    - Adapters with all instance verbs opted out and full def scope
      (`def list`/`def show`/`def set`/`def reset`/`def diff`) via
      `DataRegistry.powerups`/`resolve_powerup` and the terrain domain; routers in
      `commands/admin_commands.py`
    - _Requirements: 7.4_

  - [x] 8.2 Implement the NEW `@planet` def-read-only router
    - Adapter serving `def list`/`def show` straight from `PlanetRegistry`
      (`world/coordinate/planet_registry.py`); all other core verbs including
      `def set`/`def reset`/`def diff` opted out with the reason that planets are
      not hot-reloadable (edit `planets.yaml` and restart)
    - Router in `commands/admin_commands.py`
    - _Requirements: 7.5_

  - [x] 8.3 Write unit tests for the def-only surfaces
    - `@powerup`/`@terrain` def set → overlay → reload round-trip; `@planet`
      def show from `PlanetRegistry` and the not-hot-reloadable opt-out message
      on `def set`
    - _Requirements: 7.4, 7.5_

- [x] 9. Help text and final integration pass

  - [x] 9.1 Update admin help entries for the unified grammar
    - Update `world/help_entries.py` and router help text: the core verb table,
      the `def` scope, `#N`/key/name/prefix target grammar, and a
      legacy-spellings section on every command with installed aliases, pairing
      each alias with its canonical spelling; opt-outs listed with their reasons
    - _Requirements: 11.3_

  - [x] 9.2 Write the final cross-entity integration tests
    - Grammar sweep across every registered adapter: each core verb either
      dispatches or returns its opt-out reason; alias end-to-end (old spelling →
      identical effect + deprecation note) for the full alias matrix; audit
      entries present for every mutating verb; full suite + live boot smoke
      (`tests/test_live_boot_smoke.py`)
    - _Requirements: 1.4, 1.5, 9.1, 11.1, 11.5_
    - Done: `commands/tests/test_admin_grammar_sweep.py` (18 tests / 313
      subtests) — Part 1 sweeps all 12 `register_all()` adapters (support-or-
      opt-out per core verb, opt-out reasons shown verbatim, def-write Admin
      pin); Part 2 asserts the full 11-alias / 7-adapter matrix installs and,
      driven through each real router, emits the one-line deprecation note and
      routes to the canonical (incl. the value-first `@player level/rank`,
      `@stat hp/maxhp/xp` reshape overrides); Part 3 proves each mutating verb
      writes exactly one audit entry (reads write none). Plus a live-boot
      assertion (`test_initialize_game_registers_every_admin_adapter`) that the
      composition root registers all 12 adapters on real boot. Full suite green
      (4506 passed, 1 skipped); live boot smoke clean under `EVENNIA_REAL_BOOT=1`.

- [x] 10. Final checkpoint
  - Ensure all tests pass (entire suite green, live boot smoke clean), ask the
    user if questions arise.
  - Done: full stubbed suite green (4506 passed, 1 skipped — the live-boot
    module self-skip under the stubbed runner); live boot smoke clean under the
    real-boot escape hatch. No open questions — the unified admin CRUD spec is
    fully implemented and verified.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Phase ordering is the design's migration plan: every phase ships independently,
  the game stays working, and old spellings never break mid-migration (aliases
  install in the same phase their router migrates — Requirement 11.6)
- Property tests (Hypothesis, `max_examples=25`) validate the design's six
  correctness properties; unit tests cover specific examples and edge cases
- `@outpost`, `@player`, `@stat`, `@resource`, `@tech`, `@powerup`, `@terrain`,
  and `@planet` router work all touches `commands/admin_commands.py`, so those
  tasks are serialized in the dependency graph; `@agent`
  (`commands/agent_commands.py`) and `@alliance` (`commands/alliance_commands.py`)
  can run in parallel with them
- Checkpoints ensure incremental validation — the full suite must be green at the
  end of every phase

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "1.7"] },
    { "id": 2, "tasks": ["1.3", "1.5", "1.6", "1.8"] },
    { "id": 3, "tasks": ["1.9", "1.11"] },
    { "id": 4, "tasks": ["1.10", "1.12"] },
    { "id": 5, "tasks": ["1.13", "1.14", "1.15"] },
    { "id": 6, "tasks": ["1.16", "3.1"] },
    { "id": 7, "tasks": ["3.2"] },
    { "id": 8, "tasks": ["3.3", "3.4"] },
    { "id": 9, "tasks": ["5.1", "5.2"] },
    { "id": 10, "tasks": ["5.3"] },
    { "id": 11, "tasks": ["5.4"] },
    { "id": 12, "tasks": ["7.1", "7.2"] },
    { "id": 13, "tasks": ["7.3"] },
    { "id": 14, "tasks": ["7.4"] },
    { "id": 15, "tasks": ["7.5"] },
    { "id": 16, "tasks": ["7.6"] },
    { "id": 17, "tasks": ["8.1"] },
    { "id": 18, "tasks": ["8.2"] },
    { "id": 19, "tasks": ["8.3", "9.1"] },
    { "id": 20, "tasks": ["9.2"] }
  ]
}
```
