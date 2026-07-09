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

- [ ] 1. Turret auto-fire fix (Phase 1)
  - [ ] 1.1 Add `TURRET = "turret"` capability constant to `world/constants.py`
    - _Requirements: 1.1_
  - [ ] 1.2 Add `turret` to HQ's existing capability set AND the Turret BuildingDef in `buildings.yaml`
    - Verify: grep for the Turret entry in buildings.yaml and add `capabilities: [turret]`
    - _Requirements: 1.1_
  - [ ] 1.3 Replace `building_type != "VV"` in `combat_engine.process_turrets` with `building_has_capability(building, TURRET)`
    - _Requirements: 1.1, 12.1_
  - [ ] 1.4 Add `PlanetRoom.get_nearby_players(x, y, radius)` using CoordinateIndex spatial query + Manhattan filter
    - _Requirements: 1.2, 1.3_
  - [ ] 1.5 Update `process_turrets` to call `building.location.get_nearby_players(bx, by, turret_radius)` instead of the nonexistent `_get_nearby_players`
    - _Requirements: 1.3_
  - [ ] 1.6 Change turret owner-skip from `player is owner` to `is_owner(player, owner)`
    - _Requirements: 1.4_
  - [ ] 1.7 Update existing turret tests to use the real building type (`"TU"` / capability) and assert targeting via the new spatial query
    - _Requirements: 12.6_
  - [ ] 1.8 Add the deactivation gate (placeholder until Phase 2 lands): `if not owner_has_active_hq(owner, planet): continue`
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
    - Bump `agent_index` generation
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
