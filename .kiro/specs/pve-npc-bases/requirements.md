# Requirements Document

## Introduction

This feature introduces **NPC outposts and fortresses** — procedurally scattered enemy bases that give solo (and multiplayer) players something to fight, a reason to build defensively, and a goal beyond gathering. It builds on the existing combat engine, building system, NPC typeclass, and tick loop, layering in: enemy-owned structures to attack, guards that fight back, turrets that actually fire (fixing a pre-existing bug), a base-elimination mechanic that works for both PvE (instant wipe) and PvP (deactivation), and a spawner that keeps the world populated.

### Core game-loop contribution

Explore → find an NPC outpost → gear up → raid it (destroy guards, dodge turrets, breach walls, destroy the HQ) → earn XP + resource loot → it respawns elsewhere later → repeat at increasing difficulty. This retroactively makes the entire defensive building set (Walls, Turrets, Guards, repair) meaningful — both for the player's own base and for the NPC targets.

### PvP compatibility constraint

Every system introduced here (turret auto-fire, guard combat AI, base elimination, HQ-destruction consequences) SHALL work identically for player-owned bases in PvP — the only difference is the consequence of HQ loss (NPC: full wipe; player: deactivation until HQ rebuilt). Systems MUST NOT be NPC-specific; they are ownership-generic and the NPC/player fork happens only in the HQ-destruction consequence handler.

### Alignment with existing systems

- **Combat engine:** `mygame/world/systems/combat_engine.py` — `queue_attack`/`resolve_tick`/`process_turrets`/`_handle_building_destruction`/`_handle_player_defeat`. Damage formula: `max(0, weapon_damage + damage_bonus − damage_reduction)`, range by Manhattan distance.
- **Building system:** `mygame/world/systems/building_system.py` — `start_construction`, `_player_has_hq`, `HEADQUARTERS` capability, `BuildingDef` definitions.
- **NPC typeclass:** `mygame/typeclasses/npcs.py` — `NPC(CombatEntity, GameEntity)`, `_object_type_tag="npc"`, `at_combat_entity_init` gives HP/equipment/combat_xp.
- **Tick loop:** `mygame/typeclasses/scripts.py` — `TICK_STEP_ORDER`, `_build_tick_steps`, `tick_data` (online_players, buildings).
- **Ownership:** `world/utils.py:is_owner` compares `.id`; `db.owner` on buildings/NPCs.
- **Coordinate world:** `PlanetRoom` per planet, `CoordinateIndex`, `move_entity`, `get_buildings_at`, `get_players_at`.
- **Notifications:** `BaseSystem.notify(player, kind, **data)` → `NotificationPresenter._FORMATTERS`.
- **Balance:** `BalanceConfig` dataclass (`world/definitions.py`), `balance.yaml`, `SchemaValidator`.

### Resolved design decisions (confirmed with stakeholder)

- **D1 — HQ destruction consequence diverges by owner type.** PvE (NPC-owned HQ): entire base (all buildings + NPCs) is deleted instantly. PvP (player-owned HQ): base is deactivated (turrets stop, agents idle, building commands rejected) until a new HQ is built. The combat engine still deletes the HQ building at 0 HP in both cases; the fork is in the consequence handler.
- **D2 — Guard weapons are mixed by base tier.** Outpost guards: melee (range 1, moderate damage). Fortress guards: ranged (range 3–5, higher damage). Creates difficulty escalation.
- **D3 — HQ kill = instant wipe (PvE).** No mop-up of scattered guards; everything owned by that NPC sentinel is despawned immediately on HQ destruction.
- **D4 — Cleared outposts respawn after a cooldown.** A new base spawns at a random valid location after configurable `outpost_respawn_ticks`. The world never empties out.
- **D5 — NPC ownership via sentinel Characters.** Each NPC base is owned by a per-base sentinel Character (never puppeted). This gives turrets a friend/foe rule, renders buildings as "enemy" on the map, and makes `is_owner` guards work correctly (player.id ≠ sentinel.id → XP granted). Existing map renderer already colors non-owner buildings red.
- **D6 — Enemy NPC type for permanent death.** NPC outpost guards use `npc_type="enemy"`. At 0 HP they are deleted (not respawned like player agents). The combat engine's `_is_agent` check (`npc_type=="agent"`) does not match `"enemy"`, so a new branch is needed.
- **D7 — PvP "no HQ = base inert" predicate.** A single shared helper `owner_has_active_hq(owner)` gates turret fire, production, guard AI, and building-specific commands. When a player rebuilds their HQ, everything reactivates automatically — no per-building state change needed.
- **D8 — Turret targeting fix is a prerequisite.** `process_turrets` is currently gated on building_type `"VV"` but the live Turret abbreviation is `"TU"` (data mismatch). Additionally, `_get_nearby_players` does not exist on `PlanetRoom`. Both must be fixed for turrets to fire at all — for NPC fortresses AND player bases.
- **D9 — Guard AI is the same for NPC and player guards.** A guard/soldier agent whose owner has an active HQ and who detects a non-owner entity within aggro range queues an attack. Identical for player-assigned guards defending against raiders and NPC-base guards defending against players.

### Pre-existing bugs resolved by this feature

1. **Turret type mismatch:** `process_turrets` (`combat_engine.py:344`) checks `building_type != "VV"`, but the live Turret building abbreviation is `"TU"` (`buildings.yaml`). Live turrets never fire.
2. **Missing `get_nearby_players`:** `_get_nearby_players` (`combat_engine.py:864-877`) calls a method that doesn't exist on `PlanetRoom`. Turret targeting returns `[]` in the live game.

## Glossary

- **NPC_Base**: A cluster of buildings + guard NPCs owned by a single Sentinel_Character, placed on the map by the Outpost_Spawner. Represents either an Outpost (small) or a Fortress (large).
- **Outpost**: A small NPC_Base: an HQ + 1–2 utility buildings + 1–2 melee guards. Soloable at rank 1–3.
- **Fortress**: A large NPC_Base: an HQ + 4–6 buildings (Walls, Turrets, Armory) + 3–5 mixed guards. Requires rank 5+ / good gear.
- **Sentinel_Character**: A non-puppeted Evennia Character created solely to own an NPC_Base's buildings and guards. Gives `is_owner` a distinct `.id` for friend/foe and XP guards.
- **Enemy_NPC**: An NPC with `npc_type="enemy"` — a guard of an NPC_Base. Dies permanently (deleted at 0 HP), unlike player agents who respawn.
- **Guard_AI**: The system that gives guard/soldier NPCs target-acquisition and auto-attack behavior each tick. Works for both player guards and NPC guards.
- **Base_Elimination**: The consequence handler for HQ destruction. Diverges: PvE (full wipe + reward) vs. PvP (deactivation).
- **Base_Deactivation**: The PvP state where a player's base has no HQ — all turrets, production, guard AI, and building commands are inert. Clears the moment a new HQ is built.
- **Outpost_Spawner**: The system that places NPC_Bases on the map at init and respawns cleared ones after a cooldown.
- **Aggro_Radius**: The tile distance within which a guard detects and attacks a hostile. Balance-configurable.
- **Synthetic_Weapon**: A weapon-like data object (no Game_Item) used by turrets and NPC guards to participate in the combat engine's damage formula without needing an actual equipped item. Pattern already exists as `_TurretWeapon`.

## Requirements

### Requirement 1: Turret auto-fire fix (prerequisite)

**User Story:** As a player, I want my Turrets and NPC fortress Turrets to actually fire at nearby enemies, so that defensive buildings have purpose.

#### Acceptance Criteria

1. THE CombatEngine.process_turrets SHALL identify Turret buildings by the `turret` capability on their BuildingDef (resolved via the DataRegistry), NOT by a hardcoded building_type string.
2. THE PlanetRoom SHALL expose a `get_nearby_players(x, y, radius)` method that returns all player Characters within Manhattan distance `radius` of `(x, y)`, using the CoordinateIndex.
3. THE CombatEngine.process_turrets SHALL use `get_nearby_players` (or an equivalent spatial query) to find targets within `turret_radius` of a Turret building's coordinates.
4. THE turret owner-skip SHALL use `is_owner(target, turret_owner)` (by `.id`) rather than identity comparison.
5. THE turret SHALL NOT fire when its owner has no active HQ (Base_Deactivation gate — Requirement 6).
6. WHEN a Turret fires, THE CombatEngine SHALL resolve the damage through the standard pipeline (synthetic weapon, `_finalize_hit`, notifications), granting the target no XP for being hit (existing behavior).

### Requirement 2: "No HQ = base inert" deactivation (PvP)

**User Story:** As a player whose HQ was destroyed, I want my base to go inert (not be deleted) so I can rebuild and recover, while the raider gets the strategic advantage of disabling my defenses.

#### Acceptance Criteria

1. THE system SHALL expose a shared helper `owner_has_active_hq(owner, planet)` that returns True if *owner* owns a non-destroyed, non-offline Building with the `headquarters` capability on *planet*.
2. WHEN `owner_has_active_hq` returns False for a building's owner, THE following SHALL be inert:
   - Turret auto-fire (Requirement 1.5)
   - Agent/guard combat AI (Requirement 4)
   - Equipment production (per-tick production in `EquipmentSystem.process_production`)
   - Building-specific commands: `craft`, `research`, `deposit`, `withdraw`, `closeexit`, `openexit`, `assign`, `unassign`
3. WHEN the player builds a new HQ (construction completes), all gated systems SHALL reactivate automatically — no manual action or per-building reset required.
4. THE deactivation SHALL NOT delete, move, or change the HP of any surviving building or agent.
5. THE `build` command SHALL remain available even without an HQ, so the player can construct a new one (existing `requires_hq=False` on HQ BuildingDef).

### Requirement 3: Guard combat AI

**User Story:** As a player, I want guards (mine and NPC) to automatically attack nearby enemies, so that assigning a guard to my base actually defends it and NPC outposts fight back.

#### Acceptance Criteria

1. THE GuardCombatSystem SHALL be a new system in `world/systems/`, registered in `game_init.py` and wired as a tick step (placed before `combat_resolution` in `TICK_STEP_ORDER`).
2. EACH tick, for every NPC with role `"guard"` or `"soldier"` whose owner has an active HQ (Requirement 2.1):
   - THE system SHALL find the nearest non-owner player Character within `guard_aggro_radius` (balance-configurable) of the NPC's coordinates.
   - WHEN a target is found, THE system SHALL call `combat_engine.queue_attack(npc, target)`.
3. THE guard's attack SHALL use a Synthetic_Weapon (no physical Game_Item required):
   - Outpost guards (melee): range 1, `guard_melee_damage` from BalanceConfig.
   - Fortress guards (ranged): range matching the weapon definition, `guard_ranged_damage` from BalanceConfig.
4. THE guard SHALL NOT attack its own owner or other entities owned by the same owner (is_owner check).
5. THE guard SHALL NOT attack while incapacitated or at 0 HP.
6. THE GuardCombatSystem SHALL work identically for player-owned guards and NPC-owned guards — the only input is the NPC's `db.owner` and role.

### Requirement 4: Enemy NPC type and permanent death

**User Story:** As a player raiding an NPC outpost, I want the guards I kill to stay dead (not respawn like my agents), so that clearing a base feels like progress.

#### Acceptance Criteria

1. NPC_Base guards SHALL be created with `db.npc_type = "enemy"`.
2. THE CombatEngine._finalize_hit SHALL detect an enemy NPC at 0 HP: WHEN `_get_hp(target) <= 0` AND target has `db.npc_type == "enemy"`, THE engine SHALL delete the target (call `target.delete()`), grant the attacker `xp_kill` (same as a player kill), and publish a `NPC_ELIMINATED` event.
3. THE enemy NPC SHALL NOT be respawned (unlike player agents, which reset to full HP).
4. THE `xp_kill` award SHALL follow the same `is_owner` guard as player kills — only non-owner attackers receive XP.
5. THE enemy NPC's death SHALL bump the `agent_index` generation so the agent-roster cache is invalidated.

### Requirement 5: NPC base structure and sentinel ownership

**User Story:** As a player exploring the map, I want to encounter NPC outposts and fortresses that are clearly hostile, so that I can identify them as targets.

#### Acceptance Criteria

1. EACH NPC_Base SHALL be owned by a unique Sentinel_Character — an Evennia Character created at spawn time, never puppeted, whose sole purpose is to serve as the `db.owner` for that base's buildings and guards.
2. THE Sentinel_Character SHALL have a display name reflecting the base tier and a unique identifier (e.g. `"Outpost #7"`, `"Fortress Alpha"`).
3. NPC_Base buildings SHALL use standard BuildingDef types (HQ, WL, TU, EX, AR, BK) and be created via the building factory (reusing `EvenniaBuildingFactory.create_building`), so they appear on the map and participate in all existing systems (combat, turrets, chunking).
4. NPC_Base buildings SHALL render as "enemy" (dark red) on the player's map (existing behavior for non-owner buildings).
5. NPC_Base guards SHALL be created as NPC objects with `npc_type="enemy"`, owned by the Sentinel_Character, placed at the base's coordinates, and equipped with a Synthetic_Weapon appropriate to their tier (Requirement 3.3).
6. THE Sentinel_Character SHALL NOT appear in `who` listings, receive notifications, or count as an online player.

### Requirement 6: Base elimination — PvE (HQ destroyed = full wipe)

**User Story:** As a player who destroys an NPC outpost's HQ, I want the entire base to be eliminated instantly and to receive a significant reward, so that raiding feels decisive and rewarding.

#### Acceptance Criteria

1. THE system SHALL subscribe to the `BUILDING_DESTROYED` event.
2. WHEN the destroyed building has the `headquarters` capability AND its owner is a Sentinel_Character (NPC base):
   - ALL other buildings owned by that Sentinel on the same planet SHALL be deleted.
   - ALL NPCs (guards) owned by that Sentinel SHALL be deleted.
   - THE Sentinel_Character itself SHALL be deleted (cleanup).
3. THE destroying player SHALL be awarded:
   - `xp_hq_destroy` combat XP (a new balance tunable, default 500 — significantly more than `xp_building_destroy=50`).
   - A resource loot drop at the HQ's former coordinates: configurable per-tier amounts from a new `outpost_loot` / `fortress_loot` balance config.
4. A player-facing notification SHALL fire: `"[Combat] Outpost eliminated! +{xp} XP. Loot dropped at ({x},{y})."` (or Fortress equivalent).
5. THE `building_index` and `agent_index` generation counters SHALL be bumped appropriately so all caches are invalidated after the mass-delete.
6. THE base elimination SHALL NOT fire for player-owned HQs — those follow the deactivation path (Requirement 2).

### Requirement 7: NPC base spawner

**User Story:** As a player, I want the world to always have outposts and fortresses to raid, so that PvE content doesn't run out.

#### Acceptance Criteria

1. THE OutpostSpawner SHALL be a new system in `world/systems/`, registered in `game_init.py`.
2. AT server start (during `initialize_game`), THE spawner SHALL place NPC_Bases on each planet:
   - Count per planet: `outpost_count` and `fortress_count` (balance-configurable per-planet or global).
   - Placement: at valid, passable coordinates (checked via `PlanetRegistry.is_valid_coordinate` and terrain passability), with minimum distance from each other and from player-owned HQs.
3. WHEN an NPC_Base is eliminated (Requirement 6), THE spawner SHALL start a respawn cooldown (`outpost_respawn_ticks`, balance-configurable). After the cooldown expires, a new NPC_Base of the same tier SHALL be spawned at a fresh random valid location on the same planet.
4. THE spawner SHALL also expose an admin command (`@outpost spawn [type] [x y]`) for testing and manual placement.
5. THE spawner SHALL define **base templates** — data-driven layouts specifying which buildings, at what relative offsets, which guard types and counts, and at what levels. Templates are defined in YAML (`data/definitions/outposts.yaml`).
6. THE spawner SHALL track active NPC_Bases and pending respawn cooldowns via persistent Evennia Attributes on the spawner Script (so they survive restarts).

### Requirement 8: Base templates and difficulty tiers

**User Story:** As a player progressing through ranks, I want outposts to be easy (rank 1–3) and fortresses to be hard (rank 5+), so that there's always an appropriate challenge.

#### Acceptance Criteria

1. THE outpost template SHALL define a small base: 1 HQ (low HP), 0–2 Walls, 1–2 melee guards.
2. THE fortress template SHALL define a large base: 1 HQ (high HP), 2–4 Walls, 1–2 Turrets, 1 Armory or Barracks, 3–5 guards (mixed melee + ranged).
3. EACH NPC_Base building SHALL have its HP/level set by the template (not the default for player-built ones), allowing tuning of difficulty.
4. GUARD stats (HP, damage, aggro radius) SHALL be balance-configurable separately for outpost guards and fortress guards.
5. THE data model SHALL support future tier additions (e.g. a "Citadel" template) without code changes — template-driven, not hardcoded.

### Requirement 9: Balance configuration

**User Story:** As a developer, I want all PvE difficulty/reward numbers to be hot-tunable in balance.yaml, so that I can iterate on the feel without code changes.

#### Acceptance Criteria

1. THE following new fields SHALL be added to `BalanceConfig` (with defaults) and validated by `SchemaValidator`:
   - `xp_hq_destroy: int = 500` — XP awarded for destroying an NPC HQ.
   - `guard_melee_damage: int = 10` — base damage for outpost melee guards.
   - `guard_ranged_damage: int = 15` — base damage for fortress ranged guards.
   - `guard_ranged_range: int = 4` — range for fortress ranged guards.
   - `guard_aggro_radius: int = 5` — detection distance for guard AI.
   - `outpost_respawn_ticks: int = 600` — ticks before a cleared outpost re-spawns (~10 min at 1s/tick).
   - `outpost_count: int = 5` — outposts per planet at init.
   - `fortress_count: int = 2` — fortresses per planet at init.
   - `outpost_guard_hp: int = 80` — HP for outpost guards.
   - `fortress_guard_hp: int = 150` — HP for fortress guards.
2. THE `outpost_loot` and `fortress_loot` reward tables SHALL be defined in `data/definitions/outposts.yaml` (resource type → amount per tier).
3. ALL numeric tunables SHALL be readable from `registry.balance` at runtime (hot-reloadable via `@reboot`).

### Requirement 10: Notifications and player feedback

**User Story:** As a player, I want clear feedback when I engage an NPC base, so that the combat feels responsive and the reward is satisfying.

#### Acceptance Criteria

1. WHEN an NPC guard attacks the player, THE existing `attacked` notification SHALL fire (no new kind needed — guards attack through the standard combat pipeline).
2. WHEN the player kills an enemy NPC, a `npc_killed` notification SHALL fire: `"[Combat] Killed {name}. +{xp} XP."`.
3. WHEN the player destroys an NPC HQ, a `base_eliminated` notification SHALL fire (Requirement 6.4).
4. WHEN a player's own HQ is destroyed (PvP), a `base_deactivated` notification SHALL fire: `"[Alert] Your HQ was destroyed! Base deactivated — rebuild an HQ to restore operations."`.
5. WHEN the player rebuilds their HQ (completing construction), a `base_reactivated` notification SHALL fire: `"[Alert] HQ rebuilt! Base systems are back online."`.

### Requirement 11: Map and discovery integration

**User Story:** As a player, I want NPC bases to appear on my map as I explore, so that I can spot them through fog of war and plan my approach.

#### Acceptance Criteria

1. NPC_Base buildings SHALL be discovered through the existing fog-of-war system (a player whose vision_radius reaches the base reveals those tiles).
2. NPC_Base buildings SHALL render using the existing building symbols on the map (HQ, WL, TU, etc.) in the "enemy" color (dark red), as non-owner buildings already do.
3. NPC guards SHALL render on the map as NPCs do today (yellow neutral entities — or a new "hostile NPC" color if desired in a follow-up).
4. THE `scan` command SHALL list NPC buildings and guards on the player's tile, using existing display logic.

### Requirement 12: Backward compatibility and safety

**User Story:** As a developer, I want this feature to not break existing gameplay mechanics.

#### Acceptance Criteria

1. THE turret fix SHALL NOT change the XP formula or damage output of turrets — only the targeting resolution.
2. THE base-deactivation predicate SHALL NOT modify any building's `db.offline` attribute — it is a live query, not stored state.
3. Player agents (`npc_type="agent"`) SHALL continue to respawn at 0 HP; only `npc_type="enemy"` is deleted.
4. THE existing combat XP formulas (`xp_kill=100`, `xp_building_destroy=50`, `xp_death_loss=50`) SHALL remain unchanged for standard combat; only `xp_hq_destroy` is new.
5. THE `_player_has_hq` helper already exists in BuildingSystem; the new `owner_has_active_hq` SHALL be consistent with it (same capability check, same building enumeration).
6. THE full test suite (currently 1922 tests) SHALL remain green after each phase lands.
