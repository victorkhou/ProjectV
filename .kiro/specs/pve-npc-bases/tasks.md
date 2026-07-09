# Implementation Plan: NPC Outposts & Fortresses

## Overview

Enemy bases on the map — outposts (easy) and fortresses (hard) — that players raid for XP and loot.
Built in six incrementally-shippable phases. Each phase is independently testable and produces a
working game state. Every defensive system (turrets, guards, base-elimination) is ownership-generic
and immediately benefits PvP as well as PvE.

**Phases → PR mapping:**
- **Phase 1** (PR1): Turret fix + `get_nearby_players` — prerequisite; fixes a live bug; PvP benefit immediate.
- **Phase 2** (PR2): "No HQ = base inert" predicate + deactivation messaging — PvP core mechanic.
- **Phase 3** (PR3): Guard combat AI — PvP-ready; guards defend player bases AND NPC bases.
- **Phase 4** (PR4): Enemy NPC type + permanent death — PvE combat resolution.
- **Phase 5** (PR5): Base elimination handler + spawner + templates — full PvE loop.
- **Phase 6** (PR6): Balance tuning, help, and final integration tests.

## Tasks

- [x] 1. Turret auto-fire fix (Phase 1)
  - [x] 1.1 Add `TURRET = "turret"` capability constant to `world/constants.py`
    - _Requirements: 1.1_
  - [x] 1.2 Add a `capabilities: [turret]` field to the Turret BuildingDef (abbreviation `TU`) in `buildings.yaml`
    - Add the capability ONLY to the Turret entry — do NOT add `turret` to the HQ. `process_turrets` iterates every building with the `turret` capability, so tagging the HQ would make every HQ auto-fire like a turret.
    - Verify: grep for the `TU` / Turret entry in buildings.yaml; it currently has NO `capabilities` field, so add one with value `[turret]`
    - _Requirements: 1.1_
  - [x] 1.3 Replace `building_type != "VV"` in `combat_engine.process_turrets` with `building_has_capability(building, TURRET)`
    - _Requirements: 1.1, 12.1_
  - [x] 1.4 Add `PlanetRoom.get_nearby_players(x, y, radius)` using CoordinateIndex spatial query + Manhattan filter
    - _Requirements: 1.2, 1.3_
  - [x] 1.5 Update `process_turrets` to call `building.location.get_nearby_players(bx, by, turret_radius)` instead of the nonexistent `_get_nearby_players`
    - _Requirements: 1.3_
  - [x] 1.6 Change turret owner-skip from `player is owner` to `is_owner(player, owner)`
    - _Requirements: 1.4_
  - [x] 1.7 Update existing turret tests to use the real building type (`"TU"` / capability) and assert targeting via the new spatial query
    - Reconcile the `get_nearby_players` signature change: the existing turret test fakes define a 1-arg `get_nearby_players(self, radius)` (in `test_combat_engine.py` and `test_prop_combat_engine.py`), which conflicts with the new 3-arg `PlanetRoom.get_nearby_players(x, y, radius)`. Update these fakes to the 3-arg signature (or retire the old 1-arg `_get_nearby_players` hook and migrate its callers/fakes) so the two contracts cannot coexist with mismatched signatures.
    - _Requirements: 12.6_
  - [x] 1.8 Add the deactivation gate (placeholder until Phase 2 lands): `if not owner_has_active_hq(owner, planet): continue`
    - Can stub `owner_has_active_hq` to always return True for now; Phase 2 wires the real check
    - _Requirements: 1.5_

- [ ] 2. Base-deactivation predicate (Phase 2)
  - [ ] 2.1 Implement `owner_has_active_hq(owner, planet)` in `world/utils.py`
    - Queries the owner's buildings for a headquarters capability on the given planet
    - _Requirements: 2.1, 2.5_
  - [ ] 2.2 Gate `process_turrets` on `owner_has_active_hq` (replace Phase 1 placeholder)
    - _Requirements: 1.5, 2.2_
  - [ ] 2.3 Gate `EquipmentSystem.process_production` — skip buildings whose owner fails the check
    - _Requirements: 2.2_
  - [ ] 2.4 Gate building commands (craft, research, deposit, withdraw, closeexit, openexit, assign, unassign) — reject with "Your base is deactivated — rebuild an HQ."
    - _Requirements: 2.2_
  - [ ] 2.5 Add `base_deactivated` notification (fires in the existing `_handle_building_destruction` when a player HQ is destroyed)
    - _Requirements: 10.4_
  - [ ] 2.6 Add `base_reactivated` notification (fires when HQ construction completes for a player whose base was inert)
    - _Requirements: 10.5, 2.3_
  - [ ] 2.7 Tests: deactivation predicate unit tests, gated systems reject when no HQ, reactivation on HQ rebuild, notification assertions
    - _Requirements: 12.6_

- [ ] 3. Guard combat AI (Phase 3)
  - [ ] 3.1 Create `world/systems/guard_combat_system.py` with `GuardCombatSystem(BaseSystem)`
    - `process_tick(tick_number, buildings, online_players)` — main per-tick method
    - _Requirements: 3.1_
  - [ ] 3.2 Register in `game_init.py` and add `"guard_combat"` to `TICK_STEP_ORDER` (before `combat_resolution`)
    - _Requirements: 3.1_
  - [ ] 3.3 Wire in `_build_tick_steps`: gather NPCs with role guard/soldier, call process_tick
    - _Requirements: 3.1_
  - [ ] 3.4 Implement target acquisition: `get_nearby_players(npc_x, npc_y, aggro_radius)`, exclude owner, pick nearest
    - _Requirements: 3.2, 3.4_
  - [ ] 3.5 Implement `_GuardWeapon` synthetic weapon (melee + ranged variants with configurable damage/range)
    - _Requirements: 3.3_
  - [ ] 3.6 Gate on `owner_has_active_hq` — skip guards whose owner's base is deactivated
    - _Requirements: 3.2_
  - [ ] 3.7 Skip incapacitated / 0-HP guards
    - _Requirements: 3.5_
  - [ ] 3.8 Add balance fields to `BalanceConfig`: `guard_melee_damage`, `guard_ranged_damage`, `guard_ranged_range`, `guard_aggro_radius`
    - _Requirements: 9.1_
  - [ ] 3.9 Tests: guard targets nearest non-owner, skips own owner, skips deactivated, range/damage correct, melee vs ranged, no attack when incapacitated
    - _Requirements: 12.6_

- [ ] 4. Enemy NPC type + permanent death (Phase 4)
  - [ ] 4.1 Add `_is_enemy_npc(target)` helper to CombatEngine: checks `db.npc_type == "enemy"`
    - _Requirements: 4.1_
  - [ ] 4.2 Add `_handle_enemy_death(target, attacker)` to CombatEngine
    - Award `xp_kill` to non-owner attacker (same is_owner guard as player kills)
    - Publish `NPC_ELIMINATED` event
    - Delete target (`target.delete()`)
    - Bump `agent_index` generation — note: `NPC.at_object_delete` already bumps the `agent_index` generation on `delete()`, so an explicit bump here is only needed if the deletion path differs; keep it idempotent / non-duplicated
    - _Requirements: 4.2, 4.3, 4.4, 4.5_
  - [ ] 4.3 Insert the `_is_enemy_npc` check in `_finalize_hit` BEFORE the `_is_player` check, so enemy NPCs die permanently
    - _Requirements: 4.2, 4.3_
  - [ ] 4.4 Add `npc_killed` notification kind + formatter
    - _Requirements: 10.2_
  - [ ] 4.5 Tests: enemy NPC deleted at 0 HP, xp_kill awarded, player agents still respawn (regression), is_owner guard works
    - _Requirements: 12.3, 12.6_

- [ ] 5. NPC base spawner + base elimination (Phase 5)
  - [ ] 5.1 Create `world/systems/outpost_spawner.py` with `OutpostSpawnerSystem(BaseSystem)`
    - _Requirements: 7.1_
  - [ ] 5.2 Define base template data model and load from `data/definitions/outposts.yaml`
    - _Requirements: 7.5, 8.1, 8.2, 8.5_
  - [ ] 5.3 Implement Sentinel_Character creation (non-puppeted, distinct id, display name)
    - _Requirements: 5.1, 5.2, 5.6_
  - [ ] 5.4 Implement placement algorithm (valid coords, min distance, passability check)
    - _Requirements: 7.2_
  - [ ] 5.5 Implement `spawn_base(planet, template, coords)` — creates sentinel, buildings (via factory), guards
    - _Requirements: 5.3, 5.4, 5.5, 8.3, 8.4_
  - [ ] 5.6 Implement `spawn_initial(planet)` — places outpost_count + fortress_count bases at init
    - _Requirements: 7.2_
  - [ ] 5.7 Wire into `initialize_game()` — call `spawn_initial` for each planet after rooms are created
    - _Requirements: 7.2_
  - [ ] 5.8 Implement base-elimination handler: subscribe to BUILDING_DESTROYED, detect NPC HQ, wipe base, award XP + loot
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_
  - [ ] 5.9 Implement respawn cooldown: track eliminated bases, re-spawn at a new location after `outpost_respawn_ticks`
    - Wire as a periodic check in the `"outpost_respawn"` tick step
    - _Requirements: 7.3_
  - [ ] 5.10 Add admin command `@outpost spawn [type] [x y]` for testing
    - _Requirements: 7.4_
  - [ ] 5.11 Add balance fields: `xp_hq_destroy`, `outpost_respawn_ticks`, `outpost_count`, `fortress_count`, `outpost_guard_hp`, `fortress_guard_hp`
    - _Requirements: 9.1_
  - [ ] 5.12 Add `base_eliminated` notification kind + formatter
    - _Requirements: 10.3, 6.4_
  - [ ] 5.13 Tests: base wipe on NPC HQ destruction, XP + loot awarded, PvP path doesn't wipe, respawn after cooldown, placement validity, template parsing
    - _Requirements: 6.6, 12.6_

- [ ] 6. Help, polish, and integration (Phase 6)
  - [ ] 6.1 Help entries: "outposts" topic (what they are, how to find/raid them), "combat" topic updated with guard AI and base-elimination info
    - _Requirements: 11.4_
  - [ ] 6.2 Update `scan` to label NPC buildings/guards distinctly (e.g. "[Enemy]" prefix)
    - _Requirements: 11.4_
  - [ ] 6.3 End-to-end integration test: spawn outpost, player approaches, guards attack, player destroys HQ, base wipes, XP awarded, respawn queued
    - _Requirements: 12.6_
  - [ ] 6.4 Verify map rendering: NPC buildings red, guards visible through fog
    - _Requirements: 11.1, 11.2, 11.3_
  - [ ] 6.5 Final balance pass: tune HP, damage, aggro radius, loot amounts based on playtesting
    - _Requirements: 9.1, 9.2, 9.3_
  - [ ] 6.6 Bump field-count contract tests for any new BalanceConfig fields
    - _Requirements: 12.6_

## Task Dependency Graph

```mermaid
graph TD
    1[1. Turret auto-fire fix]
    2[2. Base-deactivation predicate]
    3[3. Guard combat AI]
    4[4. Enemy NPC type + permanent death]
    5[5. NPC base spawner + base elimination]
    6[6. Help, polish, and integration]

    1 --> 2
    2 --> 3
    1 --> 3
    2 --> 5
    3 --> 5
    4 --> 5
    1 --> 6
    2 --> 6
    3 --> 6
    4 --> 6
    5 --> 6
```

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "4"] },
    { "id": 2, "tasks": ["3"] },
    { "id": 3, "tasks": ["5"] },
    { "id": 4, "tasks": ["6"] }
  ]
}
```

**Key cross-task dependencies:**
- **Phase 1 → Phase 2**: Task 1.8 adds the deactivation gate with a stubbed `owner_has_active_hq` (always returns True). Task 2.1 implements the real predicate and task 2.2 replaces the Phase 1 placeholder.
- **Phase 1 → Phase 3**: Task 3.4 (guard target acquisition) reuses `PlanetRoom.get_nearby_players` introduced in task 1.4.
- **Phase 2 → Phase 3**: Task 3.6 gates guards on the real `owner_has_active_hq` from task 2.1.
- **Phases 2/3/4 → Phase 5**: The spawner (Phase 5) needs a functional base — deactivation semantics (Phase 2), defending guards (Phase 3), and enemy-NPC permanent death (Phase 4) must all exist before a placed base behaves correctly.
- **Phase 4** is an independent combat-engine change (enemy NPC death) that has no upstream dependency beyond the base combat engine; it can proceed in parallel with Phases 1–3 but is required before Phase 5.
- **All prior → Phase 6**: Help, polish, and end-to-end integration depend on every preceding phase being in place.

## Notes

- Each phase is independently shippable as its own PR (see the Phases → PR mapping in the Overview).
- Every defensive system (turrets, guards, base-elimination) is ownership-generic, so each phase benefits PvP as well as PvE.
- The full existing test suite must remain green after each phase — no phase may regress prior behavior.
