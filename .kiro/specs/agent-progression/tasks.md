# Implementation Plan: Agent Progression — Shared Entity Leveling & Ability Gating

## Overview

Generalize XP/level/rank progression out of the player-only `RankSystem` onto the shared `CombatEntity` mixin, then layer owner-capped agent progression and data-driven ability gating on top. Built bottom-up so nothing is orphaned: (1) the ability-gate + agent-XP data pipeline, (2) the pure-Python `world/progression.py` threshold helper, (3) `Entity_Progression` on `CombatEntity`, (4) the `RankSystem` delegation refactor + `LEVEL_CHANGED` event, (5) NPC init, (6) the `AgentSystem` owner cap and freeze, (7) gate evaluation + enabled-ability state, (8) the `agent ability` command backends, (9) XP-award call sites, (10) owner-level-change wiring, (11) roster display, (12) stale-test reconciliation, and (13) final integration. Property tests validate all 18 correctness properties from the design; almost all work extends existing files.

## Tasks

- [x] 1. Ability-gate and agent-XP data pipeline
  - [x] 1.1 Add `AbilityGateDef` dataclass and `BalanceConfig` agent-XP fields to `world/definitions.py`
    - Define `@dataclass AbilityGateDef` with `key: str` and `required_level: int` (1..MAX_LEVEL)
    - Extend `BalanceConfig` with `agent_xp_harvest` (default 5), `agent_xp_delivery` (15), `agent_xp_construction` (20), `agent_xp_combat` (50), `agent_xp_time_served` (0), `agent_xp_death_loss` (25)
    - _Requirements: 5.6, 5.8, 6.4, 7.2_

  - [x] 1.2 Create `mygame/data/definitions/ability_gates.yaml`
    - Add a single `delivery` entry with `required_level: 21` (first level of rank 5 = `(5-1)*LEVELS_PER_RANK + 1`, clamped to MAX_LEVEL)
    - Document the derivation and tunability in a header comment
    - _Requirements: 7.1, 7.6, 7.7_

  - [x] 1.3 Implement `SchemaValidator.validate_ability_gates(data)` in `world/schema_validator.py`
    - Return `list[str]` mirroring existing validators
    - Enforce: top-level list; each entry a dict; required fields `{"key", "required_level"}`; `key` non-empty string; `required_level` int in `1..MAX_LEVEL`; duplicate `key` reported by name
    - _Requirements: 7.3, 7.4, 7.5_

  - [x] 1.4 Extend `validate_balance` / `_load_balance` for agent-XP fields in `world/schema_validator.py` and `world/data_registry.py`
    - Add the six `agent_xp_*` keys to `validate_balance` `int_fields`
    - Read each in `_load_balance` with the new defaults
    - _Requirements: 5.6, 5.7, 6.4_

  - [x] 1.5 Wire ability-gate loading into `DataRegistry` in `world/data_registry.py`
    - Add `definitions/ability_gates.yaml` to `_REQUIRED_FILES`
    - Implement `_populate_ability_gates`, store `self.ability_gates: dict[str, AbilityGateDef]`, expose `get_ability_gate(key)` and `get_ability_gates() -> list`
    - Validate via `SchemaValidator.validate_ability_gates` at load time and include `ability_gates` in the atomic `reload_all` swap
    - _Requirements: 7.1, 7.2, 13.1_

  - [x] 1.6 Write property test for ability-gate schema validation
    - **Property 13: Ability-gate schema validation** — `validate_ability_gates` reports an error iff an entry is invalid (missing `key`/`required_level`, empty/non-string `key`, non-int `required_level`, or out of `1..MAX_LEVEL`), and reports the duplicate key for any list with a repeated `key`
    - **Validates: Requirements 7.3, 7.4, 7.5**
    - Test file: `mygame/world/tests/test_prop_progression.py`

  - [x] 1.7 Write unit tests for gate data load and balance amounts
    - Assert `ability_gates["delivery"].required_level == min((5-1)*LEVELS_PER_RANK + 1, MAX_LEVEL) == 21`
    - Assert each `agent_xp_*` amount is sourced from balance (not a hardcoded literal)
    - _Requirements: 5.6, 6.4, 7.1, 7.2, 7.6, 7.7_

- [x] 2. Implement `world/progression.py` threshold helper
  - [x] 2.1 Create `world/progression.py` with the precomputed threshold table
    - Top-level import of `world.constants` only; lazy-import `rank_from_level` inside `rank_for_level` to avoid the `rank_system` ↔ `progression` circular import
    - Implement `build_thresholds(ranks)` reproducing `RankSystem._rebuild_thresholds` exactly (per-rank linear interpolation across `LEVELS_PER_RANK`; final rank uses `FINAL_RANK_XP_PER_LEVEL`); idempotent
    - Implement `is_initialized()`, `level_for_xp(xp)` (highest level whose threshold ≤ xp, clamped 1..MAX_LEVEL), `xp_for_level(level)`, `rank_for_level(level)`
    - Fallback when uninitialized: lazily attempt `DataRegistry.get_instance()`; if unavailable return `1`
    - _Requirements: 3.1, 3.2, 3.5, 3.6_

  - [x] 2.2 Write property test for the level/rank curve and player backward-compatibility
    - **Property 2: Level/rank curve correctness and player backward-compatibility** — `level_for_xp(xp)` is the highest level whose `ranks.yaml`-derived threshold is ≤ xp (monotonic non-decreasing, `threshold[level] <= xp < threshold[level+1]` for non-max levels), and matches the refactored `RankSystem.award_xp` level path exactly
    - **Validates: Requirements 3.1, 3.2, 3.3, 4.1**
    - Test file: `mygame/world/tests/test_prop_progression.py`

- [x] 3. Implement `Entity_Progression` on `CombatEntity`
  - [x] 3.1 Add progression state and methods to `typeclasses/combat_entity.py`
    - In `at_combat_entity_init`, initialize `db.combat_xp = 0`, `db.level = 1`, `db.rank_level = 1`
    - Implement `award_xp(amount)` (positive-only add, else no-op), `deduct_xp(amount)` (floored at 0, else no-op), each reading `self.db.combat_xp or 0` and calling `recompute_progression`
    - Implement `recompute_progression()` (writes `db.level = level_for_xp(...)` and `db.rank_level = rank_from_level(db.level)` on every change), `get_raw_level()`, `get_raw_rank()`, `get_combat_xp()` (0 fallback)
    - Stay pure Python and owner-agnostic (no owner/cap/gate logic)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.1, 2.3, 3.3, 3.4, 5.7, 6.2, 6.3, 12.2, 12.3, 14.9, 14.10_

  - [x] 3.2 Write property test for the progression derivation invariant
    - **Property 1: Progression derivation invariant** — after every `award_xp`/`deduct_xp`, `db.combat_xp` is a non-negative int, `db.level == progression.level_for_xp(db.combat_xp)` in `1..MAX_LEVEL`, and `db.rank_level == rank_from_level(db.level)` in `1..NUM_RANKS`
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.7, 3.4, 3.5, 3.6, 6.3**
    - Test file: `mygame/world/tests/test_prop_progression.py`

  - [x] 3.3 Write property test for award/deduct arithmetic with zero floor
    - **Property 3: Award/deduct arithmetic with zero floor** — `award_xp(amount)` adds exactly `amount` when `> 0` else no-op; `deduct_xp(amount)` yields `max(0, start - amount)` when `> 0` else no-op; death loss yields `max(0, start - agent_xp_death_loss)`
    - **Validates: Requirements 1.5, 1.6, 5.7, 6.1, 6.2**
    - Test file: `mygame/world/tests/test_prop_progression.py`

  - [x] 3.4 Write property test for per-entity independence and owner-agnostic derivation
    - **Property 4: Per-entity independence and owner-agnostic derivation** — mutating one entity's `combat_xp` leaves another entity's `combat_xp`/`level`/`rank_level` unchanged, and `get_raw_level()` is identical for a fixed `combat_xp` regardless of owner presence/identity
    - **Validates: Requirements 2.1, 2.2, 2.3, 14.9**
    - Test file: `mygame/world/tests/test_prop_progression.py`

- [x] 4. Checkpoint — Verify shared progression core
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Refactor `RankSystem` to delegate and publish `LEVEL_CHANGED`
  - [x] 5.1 Add `LEVEL_CHANGED` to `world/event_bus.py`
    - Define `LEVEL_CHANGED = "level_changed"`, add to `ALL_EVENTS`; payload `player`, `old_level`, `new_level`
    - _Requirements: 15.5_

  - [x] 5.2 Delegate curve and XP mutation in `world/systems/rank_system.py`
    - Make `_rebuild_thresholds`, `level_for_xp`, `xp_for_level` thin wrappers over `world.progression`; `__init__` calls `progression.build_thresholds(registry.ranks)` if not initialized
    - Rework `award_xp(player, amount, reason="")` and `deduct_xp(player, amount)` to capture `old_level`, call `player.award_xp` / `player.deduct_xp`, then `_sync_level(player, old_level)`
    - Keep module-level `rank_from_level` / `level_range_for_rank` defined here (reused by `progression.py` and `AgentSystem`)
    - _Requirements: 4.1, 4.2_

  - [x] 5.3 Preserve player semantics and publish `LEVEL_CHANGED` in `_sync_level`
    - Retain `_get_level` legacy rule, `db.level`/`db.rank_level` writes, the player level-change message, `RANK_PROMOTED`/`RANK_DEMOTED` with `new_agent_cap`, and `_unlock_for_rank`/`_revoke_above_rank`
    - At the end of `_sync_level`, when `new_level != old_level`, publish `LEVEL_CHANGED` (after rank-event handling so reserve/restore is applied first)
    - _Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 15.5_

  - [x] 5.4 Write property test for rank-event emission on boundary crossings
    - **Property 16: Rank-event emission on boundary crossings** — `RANK_PROMOTED` fires (old rank, new rank, new agent cap) iff the derived rank increased, `RANK_DEMOTED` fires iff it decreased, and no rank event fires when rank is unchanged
    - **Validates: Requirements 4.3, 4.4**
    - Test file: `mygame/world/tests/test_prop_progression.py`

  - [x] 5.5 Write unit tests for preserved player behavior
    - Assert `db.combat_xp`/`db.level`/`db.rank_level` meanings unchanged, level-change message fires, legacy `rank_level`→`level` derivation, and tech unlock/revoke on rank change
    - _Requirements: 4.2, 4.5, 4.6, 4.7_

- [x] 6. Initialize agent progression and owner-cap + freeze in `AgentSystem`
  - [x] 6.1 Initialize progression and enabled-ability state on NPC creation in `typeclasses/npcs.py`
    - In `at_object_creation`, ensure `at_combat_entity_init()` runs and explicitly default `combat_xp=0`, `level=1`, `rank_level=1`, and `db.enabled_abilities = []`
    - _Requirements: 12.1_

  - [x] 6.2 Implement owner-cap helpers in `world/systems/agent_system.py`
    - `get_owner_level(agent)` (reuse `RankSystem._get_level` rule; default 1 when owner missing)
    - `compute_effective_level(agent)` = `max(1, min(get_raw_level(), owner_level - 1))`
    - `get_cap_ceiling(agent)` = `max(1, owner_level - 1)`
    - _Requirements: 14.1, 14.2, 14.3, 14.5, 14.6, 14.7_

  - [x] 6.3 Implement enabled-ability accessors in `world/systems/agent_system.py`
    - `get_enabled_abilities(agent)` reads `agent.db.enabled_abilities` (absent/None → empty set; sticky, independent of attach state)
    - `_set_enabled_abilities(agent, keys)` persists back as a list
    - _Requirements: 12.4, 17.1_

  - [x] 6.4 Implement freeze-aware `award_agent_xp` and `apply_agent_death_loss`
    - `award_agent_xp(agent, source)` computes effective level + cap ceiling first; if `agent.db.level >= cap_ceiling` skip entirely (no surplus); else look up amount from balance by source key, call `agent.award_xp`, then re-evaluate effective level + gated abilities
    - `apply_agent_death_loss(agent)` calls `agent.deduct_xp(balance.agent_xp_death_loss)` then re-evaluates (never frozen)
    - _Requirements: 5.7, 5.9, 5.10, 6.1, 6.2, 6.3, 14.4, 14.6, 14.8_

  - [x] 6.5 Write property test for the effective-level formula
    - **Property 5: Effective-level formula** — `compute_effective_level` returns `max(1, min(Raw_Level, owner_level - 1))`, always ≥ 1, strictly less than `owner_level` when `owner_level > 1`, and equals 1 when `owner_level == 1`
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.10**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 6.6 Write property test for XP award frozen at the cap ceiling
    - **Property 6: XP award frozen at the cap ceiling** — for any agent at its `Cap_Ceiling` (`agent.db.level >= max(1, owner_level - 1)`) and any source, `award_agent_xp` leaves `combat_xp`/`level`/`rank_level` unchanged
    - **Validates: Requirements 5.9, 14.4**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 6.7 Write property test for XP award resuming when the ceiling rises
    - **Property 7: XP award resumes when the ceiling rises** — for an agent frozen at its ceiling, after the owner level rises so `Cap_Ceiling` exceeds the agent's level, the next `award_agent_xp` with a positive amount strictly increases `combat_xp` (no banked surplus)
    - **Validates: Requirements 5.10, 14.8**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 6.8 Write property test for the effective-level clamp on owner demotion
    - **Property 8: Effective-level clamp on owner demotion never strips XP** — after any owner-level decrease, `Effective_Level == max(1, min(Raw_Level, new_owner_level - 1))` while `combat_xp`/`level`/`rank_level` remain unchanged
    - **Validates: Requirements 10.1, 14.1, 14.7, 15.1**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 6.9 Write property test for legacy-agent defaulting and first-mutation persistence
    - **Property 14: Legacy agent defaulting and first-mutation persistence** — for an agent lacking progression attrs, `get_combat_xp()==0`, `get_raw_level()==1`, `get_enabled_abilities()==set()`; after the first `award_xp`/`deduct_xp`, all three attrs are present and consistent (`level == level_for_xp(combat_xp)`)
    - **Validates: Requirements 12.2, 12.3, 12.4**
    - Test file: `mygame/tests/test_prop_agent_system.py`

- [x] 7. Checkpoint — Verify owner cap and freeze
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement gate evaluation and ability-script resolution in `AgentSystem`
  - [x] 8.1 Add `ABILITY_SCRIPT_MAP` and revert `ROLE_SCRIPT_MAP["harvester"]` in `typeclasses/agent_scripts.py`
    - Add `ABILITY_SCRIPT_MAP = {"delivery": DeliveryBehavior}`
    - Revert `ROLE_SCRIPT_MAP["harvester"]` to `HarvesterScript` only (delivery is gate- and enablement-driven, not role-driven)
    - _Requirements: 8.6, 13.2, 13.3_

  - [x] 8.2 Implement script resolution and idempotent attach/detach helpers in `world/systems/agent_system.py`
    - `resolve_ability_script(key)` returns `ABILITY_SCRIPT_MAP.get(key)` (None → unresolved)
    - `_attach_single_script(agent, script_cls)` checks existing scripts by `key` before adding (no duplicate); initializes `delivery_state = DeliveryState.IDLE` when attaching `DeliveryBehavior`
    - `_detach_single_script(agent, script_key)` removes only the named gated script, leaving `HarvesterScript` in place
    - _Requirements: 9.3, 9.4, 13.4_

  - [x] 8.3 Implement `evaluate_gated_abilities` in `world/systems/agent_system.py`
    - For each gate: `available = effective_level >= required`, `enabled = key in get_enabled_abilities(agent)`, `script_cls = resolve_ability_script(key)`
    - Unresolved key → log once at warning and skip; two-condition attach (`available AND enabled`) → attach + init + notify "now active"; attached but not wanted → detach, notify re-lock only when caused by level drop (not available); available-but-not-enabled and unattached → mark available + notify how to enable (once); else no-op
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 12.5, 12.6, 13.4, 15.2, 15.3, 15.4, 17.3, 17.4_

  - [x] 8.4 Make `_attach_behavior_script` gate-aware for harvesters in `world/systems/agent_system.py`
    - Always attach `HarvesterScript`, then call `evaluate_gated_abilities(agent)` to conditionally attach `DeliveryBehavior` from `Effective_Level` + enabled set
    - Retain the list-handling path for any role mapping to a list; ensure `assign_agent` reserve-restore path attaches delivery iff effective ≥ gate AND enabled
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6, 10.3, 10.4, 12.6_

  - [x] 8.5 Write property test for gate attachment on role apply
    - **Property 9: Gate attachment matches effective level AND enabled state on role apply** — after the harvester role is applied, `DeliveryBehavior` attaches iff `Effective_Level >= delivery required` AND `delivery` is enabled; `HarvesterScript` always attaches
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.5, 8.6, 10.4, 12.5**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 8.6 Write property test for gate-evaluation convergence and idempotence
    - **Property 10: Gate evaluation convergence and idempotence (available AND enabled)** — repeated `evaluate_gated_abilities` calls leave a gated script attached iff `Effective_Level >= required` AND enabled (exactly one instance, no duplicates), retain `HarvesterScript` across a delivery detach, and emit the available/now-active/re-locked notifications exactly on their respective transitions
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.8, 14.6, 15.2, 15.3, 15.4**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 8.7 Write property test for reserve/stop independence
    - **Property 11: Progression survives reserve/stop and is cap/reserve-independent** — reserve/stop/unassign leave `combat_xp`/`level`/`rank_level`/enabled set unchanged, and for fixed `combat_xp`+owner level+enabled set the `Effective_Level` and per-ability status are identical regardless of reserve/stopped status
    - **Validates: Requirements 10.1, 10.2, 10.3**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 8.8 Write property test for gate extensibility and unresolved-key safety
    - **Property 15: Gate extensibility and unresolved-key safety (generic across keys)** — for added valid gates whose keys map to a script, evaluation and enable/disable/status operate purely on `Effective_Level >= required` + enabled set with no `delivery`-specific behavior; for a gate whose key has no script, evaluation attaches nothing, logs the key, and leaves the agent otherwise unchanged
    - **Validates: Requirements 13.1, 13.2, 13.4, 13.5**
    - Test file: `mygame/tests/test_prop_agent_system.py`

- [x] 9. Implement and wire the `agent ability` command
  - [x] 9.1 Implement enable/disable/status backends in `world/systems/agent_system.py`
    - `enable_ability(player, agent_id, key)`: validate ownership (not found → reject) and known gate key (unknown → reject); if `effective_level >= required` add key + attach script + init state + confirm, else reject with required level and do not record/attach
    - `disable_ability(player, agent_id, key)`: validate ownership + known key; clear key, detach via `_detach_single_script` (HarvesterScript stays), confirm
    - `get_ability_status(player, agent_id)`: validate ownership; per gate report `locked (Lv N)` / `available` / `enabled`
    - _Requirements: 13.5, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 17.2, 17.5_

  - [x] 9.2 Add the `ability` subcommand to `CmdAgent` in `commands/agent_commands.py`
    - Add `sub_ability` handler + `subcommands["ability"]` entry on the existing `GameSubcommandRouter`
    - `agent ability <id> <key> on|off` delegates to `enable_ability`/`disable_ability`; `agent ability <id>` delegates to `get_ability_status`; otherwise show usage. Keep the handler logic-free (all rules in `AgentSystem`)
    - _Requirements: 16.1_

  - [x] 9.3 Write property test for ability enablement command behavior
    - **Property 17: Ability enablement command behavior** — `enable_ability` records the key and attaches the script (initializing state) iff `Effective_Level >= required`, else rejects with the required level and neither records nor attaches; `disable_ability` clears the key and detaches that script while leaving `HarvesterScript` in place
    - **Validates: Requirements 16.2, 16.3, 16.4, 9.6**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 9.4 Write property test for sticky enablement across forced detach
    - **Property 18: Sticky enablement persists across forced detach and drives auto re-attach** — with an ability enabled, a drop below the gate detaches but retains the flag and a later rise auto-re-attaches with no new command; after `disable_ability` clears the flag, a rise does not re-attach until re-enabled
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.5**
    - Test file: `mygame/tests/test_prop_agent_system.py`

  - [x] 9.5 Write unit tests for unknown-key and unowned-agent rejection
    - Assert `agent ability` rejects an unknown ability key and an agent id the player does not own
    - _Requirements: 16.6, 16.7_

- [x] 10. Checkpoint — Verify gating and ability command
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Wire XP award call sites
  - [x] 11.1 Award harvest XP in `HarvesterScript` (`typeclasses/agent_scripts.py`)
    - After a successful resource drop, call the module helper `_award_agent_xp(npc, "harvest")` wrapping the lazy `game_systems["agent_system"].award_agent_xp` lookup
    - _Requirements: 5.1_

  - [x] 11.2 Award delivery XP in `DeliveryBehavior` (`typeclasses/agent_scripts.py`)
    - After `deposit_resources` in `_deposit_and_return`, award `"delivery"`
    - _Requirements: 5.2_

  - [x] 11.3 Award construction XP in `EngineerScript` (`typeclasses/agent_scripts.py`)
    - In `_complete_construction`/`_complete_research`, award `"construction"`
    - _Requirements: 5.3_

  - [x] 11.4 Award combat XP and apply death loss in `CombatEngine`
    - On defeat handling: when the attacker is an agent award `"combat"`; when an agent victim is defeated call `apply_agent_death_loss`
    - _Requirements: 5.4, 6.1_

  - [x] 11.5 Award time-served XP and add defensive re-eval in `AgentSystem.process_tick`
    - For each actively-assigned, non-reserved, non-incapacitated agent award `"time_served"` once per tick (zero amount → no-op; frozen at ceiling short-circuits)
    - Wrap each agent's award + `evaluate_gated_abilities` in a per-agent try/except so a single bad agent never halts the tick
    - _Requirements: 5.5, 5.8, 5.9_

- [x] 12. Wire owner-level-change re-evaluation and server init
  - [x] 12.1 Implement `on_owner_level_changed` in `world/systems/agent_system.py`
    - For each owned agent: recompute `Cap_Ceiling` and call `evaluate_gated_abilities`; a rise crossing a gate marks available + notifies (no attach) unless enabled (then attach + notify active); a drop below a gate detaches but retains the enabled flag and notifies re-lock
    - _Requirements: 14.7, 14.8, 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 12.2 Wire initialization and subscriptions in `server/conf/game_init.py`
    - After `registry.load_all(...)` call `progression.build_thresholds(registry.ranks)`
    - Subscribe `LEVEL_CHANGED` → `agent_system.on_owner_level_changed`; keep existing `RANK_PROMOTED`/`RANK_DEMOTED` reserve/restore subscriptions
    - _Requirements: 15.5_

  - [x] 12.3 Write integration test for owner-level-change flow
    - `LEVEL_CHANGED` publication → `on_owner_level_changed` → available-but-not-enabled notification, then enable → attach; and a level drop → detach keeping `HarvesterScript`
    - _Requirements: 15.1, 15.2, 15.3, 15.4_

- [x] 13. Implement roster progression display
  - [x] 13.1 Implement `get_agent_progression_view` in `world/systems/agent_system.py`
    - Return `{effective_level, rank_name, ability_status: {key: 'locked:N'|'available'|'enabled'}, capped_by_commander: raw_level > effective_level}`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 14.5_

  - [x] 13.2 Render progression in the roster `sub_list` in `commands/agent_commands.py`
    - Add `Lv {effective_level} {rank_name}`, the per-ability status segment (or `no abilities` when none qualify), and a `|y[capped]|n` marker when `capped_by_commander`
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [x] 13.3 Write property test for roster progression view consistency
    - **Property 12: Roster progression view consistency** — `get_agent_progression_view` reports `effective_level == compute_effective_level(agent)`, `capped_by_commander` true iff `Raw_Level > Effective_Level`, and `ability_status` assigning each gate `enabled`/`available`/`locked (with required level)` per the enabled set and effective level
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 14.5, 16.5**
    - Test file: `mygame/tests/test_prop_agent_system.py`

- [x] 14. Reconcile stale `test_agent_scripts.py` tests (mandatory)
  - [x] 14.1 Update `ROLE_SCRIPT_MAP`/`ABILITY_SCRIPT_MAP` tests in `typeclasses/tests/test_agent_scripts.py`
    - Change `test_harvester_maps_to_list` to assert `ROLE_SCRIPT_MAP["harvester"] is HarvesterScript`; add `ABILITY_SCRIPT_MAP["delivery"] is DeliveryBehavior`; assert `DeliveryBehavior` attaches only via `evaluate_gated_abilities` when at/above gate AND enabled
    - _Requirements: 8.6, 13.2, 13.3_

  - [x] 14.2 Reconcile the four extractor-inventory `TestHarvesterScript` tests in `typeclasses/tests/test_agent_scripts.py`
    - Update `test_produces_resources_into_extractor_inventory`, the level-scaling test, the energy test, and `test_production_accumulates` to assert on spawned `ResourceDrop`s (production-only, Req 8.3), confirming a sub-threshold harvester produces drops with no delivery state
    - _Requirements: 8.3, 8.4_

  - [x] 14.3 Update gate-driven harvester tests to set the enabled flag in `typeclasses/tests/test_agent_scripts.py`
    - Any test expecting `DeliveryBehavior` to attach must add `delivery` to `db.enabled_abilities` (or call `enable_ability`); add an at/above-gate-but-not-enabled case asserting production-only and that the player was notified the ability is available
    - _Requirements: 8.2, 8.3, 9.1_

  - [x] 14.4 Add `agent ability` command and backend coverage in `mygame/tests/test_agent_router.py`
    - Cover enable-at/above-gate attaches+records, enable-below-gate rejects with required level (no attach), disable detaches `DeliveryBehavior` keeping `HarvesterScript`, status reports locked/available/enabled, unknown key and unowned agent rejected
    - _Requirements: 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_

- [x] 15. Final checkpoint and integration tests
  - [x] 15.1 Write integration tests for the wired flows
    - Test file: `mygame/tests/test_integration_agent_progression.py`
    - Freeze-then-resume award across an owner level-up; reserve/restore preserves progression + enabled set; reassign attaches delivery iff effective ≥ gate AND enabled; RankSystem promotion still fires `RANK_PROMOTED` + reserve handling
    - _Requirements: 5.9, 5.10, 10.1, 10.2, 10.4, 4.3, 14.8_

  - [x] 15.2 Final checkpoint — Ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

## Notes

- Property tests validate the design's universal correctness properties (Properties 1–18); every numbered property has a corresponding sub-task that names the property, restates it, and lists the requirements it validates.
- Property tests follow the existing Hypothesis conventions: `test_prop_*.py` files, `@settings(max_examples=...)` of at least 100 (match the existing 200 where cheap), the `Feature: agent-progression, Property {n}: {text}` tag comment, and the Evennia-stub `conftest`/`_ensure_evennia_stubs()` bootstrap so the fast suite runs without a live server.
- Most work extends existing files. The only genuinely new files are `world/progression.py`, `mygame/data/definitions/ability_gates.yaml`, the `AbilityGateDef` dataclass (added to the existing `world/definitions.py`), and the new `test_prop_*.py` / integration test files.
- The stale-test reconciliation in task 14 is mandatory, not optional: the gate-driven harvester change invalidates existing assumptions in `test_agent_scripts.py`, and leaving them unreconciled produces red (or wrongly-green) tests.
- `Effective_Level` and `Cap_Ceiling` are always computed on demand, never stored, so they can never go stale; `CombatEntity` stays owner-agnostic and the freeze decision lives entirely in `AgentSystem`.
- Each task references specific requirements for traceability, and checkpoints ensure incremental validation between phases.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "2.1", "5.1"] },
    { "id": 2, "tasks": ["1.5", "1.6", "3.1"] },
    { "id": 3, "tasks": ["1.7", "2.2", "5.2", "6.1"] },
    { "id": 4, "tasks": ["3.2", "5.3", "6.2"] },
    { "id": 5, "tasks": ["3.3", "5.5", "6.3"] },
    { "id": 6, "tasks": ["3.4", "6.4", "6.5", "8.1"] },
    { "id": 7, "tasks": ["5.4", "6.6", "8.2", "11.1"] },
    { "id": 8, "tasks": ["6.7", "8.3", "11.2"] },
    { "id": 9, "tasks": ["6.8", "8.4", "11.3"] },
    { "id": 10, "tasks": ["6.9", "9.1", "11.4"] },
    { "id": 11, "tasks": ["8.5", "9.2", "11.5"] },
    { "id": 12, "tasks": ["8.6", "9.5", "12.1"] },
    { "id": 13, "tasks": ["8.7", "12.2", "13.1"] },
    { "id": 14, "tasks": ["8.8", "12.3", "13.2"] },
    { "id": 15, "tasks": ["9.3", "14.1", "14.4"] },
    { "id": 16, "tasks": ["9.4", "14.2", "15.1"] },
    { "id": 17, "tasks": ["13.3", "14.3"] }
  ]
}
```
