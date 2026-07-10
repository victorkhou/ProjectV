# Design Document

## Overview

NPC outposts and fortresses — enemy bases scattered across the procedural map that players can raid for XP and loot. Built on the existing combat engine, building system, and NPC typeclass. Every defensive system introduced (turret fix, guard AI, base-elimination consequence) is ownership-generic and immediately benefits PvP too.

The feature decomposes into six independent, incrementally-shippable phases. Each produces a working, testable increment. The phases are ordered by dependency (turret fix unblocks guard AI; base-deactivation predicate is cheap and broadly useful; guard AI needs enemy NPCs to fight; spawner needs all prior systems to place a functional base).

## Architecture

### Ownership model: Sentinel Characters

Each NPC base is owned by a dedicated **Sentinel Character** created at spawn, never puppeted, whose `.id` serves as the ownership key for all base buildings and guards. This reuses the existing `db.owner` reference and `is_owner(.id)` comparison everywhere without inventing a faction system.

**Typeclass: must be the game `Character`, not a bare `DefaultCharacter`.** The Sentinel MUST be created from the game's `Character`/`CombatCharacter` typeclass (or a dedicated `SentinelCharacter` subclass of it), NOT a plain Evennia `DefaultCharacter`. The reason is a real correctness dependency: the base-deactivation predicate `owner_has_active_hq` (and the existing `BuildingSystem._get_player_buildings`) enumerate an owner's buildings via `owner.get_buildings()`, and `get_buildings()` is defined ONLY on the game's `Character` typeclass in `typeclasses/characters.py` (it queries `ObjectDB` for objects whose `owner` attribute == self). A bare `DefaultCharacter` has no `get_buildings()` method, so `owner_has_active_hq(sentinel, planet)` would always return False — and guard AI and turrets on NPC bases would NEVER activate. `get_buildings()` is precisely the enumeration path `owner_has_active_hq` depends on, which is why `DefaultCharacter` is insufficient and the game `Character` (which inherits `get_buildings()`) is required.

To satisfy Requirement 5.6, the Sentinel (as a `Character` subclass) is flagged/overridden so that it is never puppeted, never appears in `who`, never receives notifications, and never counts as an online player — it inherits `get_buildings()` for ownership enumeration while being inert as a "player" in every other respect.

```
Sentinel Character ("Outpost #7")
  ├── Building: HQ (owner=sentinel)
  ├── Building: Wall (owner=sentinel)
  ├── Building: Turret (owner=sentinel)
  ├── NPC Guard #1 (owner=sentinel, npc_type="enemy", role="guard")
  └── NPC Guard #2 (owner=sentinel, npc_type="enemy", role="soldier")
```

**Why a Character, not a plain Object?** The `is_owner` helper and all owner-reading code (`get_building_attr(building, "owner")`, `Building.owner` property) expect the owner to have an `.id` — which Characters have. The map renderer, combat engine, and turret targeting all derive friend/foe from `owner.id` comparison, so a Sentinel with a distinct id naturally makes everything render and behave as "enemy."

### The HQ-destruction fork

```
BUILDING_DESTROYED event fires
  └── Is the building an HQ (headquarters capability)?
       ├── YES + owner is a Sentinel → PvE path (Requirement 6):
       │     1. Delete all buildings owned by sentinel
       │     2. Delete all NPCs owned by sentinel
       │     3. Delete sentinel
       │     4. Award xp_hq_destroy + loot drop
       │     5. Queue respawn cooldown
       └── YES + owner is a Player → PvP path (Requirement 2):
             1. Notification: "Base deactivated!"
             2. (No deletion — the predicate owner_has_active_hq now returns False,
                 so all gated systems go inert automatically)
```

### Base-deactivation predicate (PvP)

A single, cheap, shared function:

```python
def owner_has_active_hq(owner: Any, planet: str) -> bool:
    """True if owner has a non-destroyed HQ on this planet."""
    for building in get_player_buildings(owner):
        if building on planet and has_capability(HEADQUARTERS):
            return True
    return False
```

**Planet filtering:** `owner.get_buildings()` is NOT planet-filtered — it returns every building the owner has across all planets. The helper MUST therefore filter by each building's planet inside the loop (as shown by the `building on planet` guard above), rather than assuming the enumeration is already scoped.

**Share one enumeration helper with `_player_has_hq` (Req 12.5):** an equivalent HQ check already exists as `BuildingSystem._player_has_hq`. To satisfy Requirement 12.5 and avoid logic drift, the new `owner_has_active_hq` in `world/utils.py` SHOULD share a single underlying capability/enumeration helper with `_player_has_hq` rather than duplicating the "enumerate buildings → filter by planet → check HEADQUARTERS capability" logic in two places.

This gates (by live query, no stored state):
- `process_turrets` — skip buildings whose owner fails the check
- `GuardCombatSystem.process_tick` — skip guards whose owner fails
- `EquipmentSystem.process_production` — skip buildings whose owner fails
- Building commands (craft, research, deposit, withdraw, closeexit/openexit, assign/unassign) — reject with a message

When the player rebuilds an HQ (construction completes → the building exists → predicate flips to True), everything reactivates next tick. Zero per-building bookkeeping.

### Guard combat AI

A new `GuardCombatSystem(BaseSystem)` in `world/systems/guard_combat_system.py`, registered as tick step `"guard_combat"` between `npc_movement` and `combat_resolution` in `TICK_STEP_ORDER`.

**Tick timing note (intentional asymmetry — do not "fix"):** the real tick step for turrets is named `turret_attacks` and runs AFTER `combat_resolution`, so turret-queued shots resolve on the NEXT tick. By contrast, `guard_combat` is placed BEFORE `combat_resolution`, so guard-queued attacks resolve in the SAME tick. This difference between guards (same-tick resolution) and turrets (next-tick resolution) is intentional and expected — it is flagged here so it is not mistaken for a bug and "fixed" later.

**Roster-feed dependency (Phase 3 → Phase 5):** the tick step feeds the system from the cached agent roster (`_get_all_agents`, an `npc_type="agent"` tag search). Enemy-base guards use `npc_type="enemy"` and therefore are NOT in that roster — Phase 5 must widen the feed (include enemy NPCs) for NPC outposts to fight back. The `GuardCombatSystem` itself is already ownership-generic; only the roster passed to `process_tick` needs extending.

Per tick:
1. Gather all NPCs with role `"guard"` or `"soldier"` (from the agent-roster cache).
2. For each, check `owner_has_active_hq(npc.db.owner, planet)` — skip if False.
3. Find nearby non-owner players within `guard_aggro_radius` (spatial query on PlanetRoom).
4. Pick nearest; call `combat_engine.queue_attack(npc, nearest_player)` with a synthetic weapon.
5. The queued attack resolves in the same tick's `combat_resolution` step.

Synthetic weapons follow the existing `_TurretWeapon` pattern — a lightweight object with `damage`, `range`, and `weapon_type` attributes, no ammo tracking.

```python
class _GuardWeapon:
    """Synthetic weapon for NPC guards (same pattern as _TurretWeapon)."""
    def __init__(self, damage, weapon_range, weapon_type="melee"):
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.weapon_type = weapon_type
        self.ammo_type = None
        self.ammo_cost = None
        self.ammo_per_shot = 0
        self.magazine_size = None
    def get_stat(self, name, default=0):
        return self.stat_modifiers.get(name, default)
```

### Enemy NPC permanent death

A new branch in `_finalize_hit` (combat_engine.py):

```python
# After checking _is_building → _handle_building_destruction
# Before/alongside checking _is_player → _handle_player_defeat
if self._is_enemy_npc(target):
    self._handle_enemy_death(target, attacker)
    return

# Where _is_enemy_npc checks db.npc_type == "enemy"
# And _handle_enemy_death: award xp_kill, publish NPC_ELIMINATED, target.delete()
```

This sits above the `_is_player` check because enemy NPCs also satisfy `_is_player` (they have `db.combat_xp`). The ordering ensures enemies die permanently rather than being respawned by `_handle_player_defeat`.

### Outpost spawner

A new `OutpostSpawnerSystem` in `world/systems/outpost_spawner.py`:
- `spawn_initial(planet)` — called at server start, places `outpost_count` outposts + `fortress_count` fortresses.
- `process_respawns(tick_number)` — checked periodically, spawns any pending re-spawns whose cooldown expired.
- Wired as a tick step (`"outpost_respawn"`) placed after `resource_respawns` (low priority, infrequent).

**Placement algorithm:**
1. Generate a candidate `(x, y)` on the planet.
2. Reject if: not passable terrain, within `min_distance_from_player_hq` of any player HQ, within `min_distance_between_outposts` of another NPC base, or already has a building.
3. Accept and place using the standard 3-step primitive (`create_object` + set coords + `coord_index.add`).
4. For multi-tile bases (fortress), place buildings at relative offsets from the HQ tile.

**Template data (`data/definitions/outposts.yaml`):**

```yaml
outpost:
  buildings:
    - type: HQ
      offset: [0, 0]
      hp: 200
    - type: WL
      offset: [0, 1]
      hp: 300
  guards:
    - role: guard
      weapon_type: melee
      count: 2

fortress:
  buildings:
    - type: HQ
      offset: [0, 0]
      hp: 600
    - type: WL
      offset: [-1, 0]
      hp: 600
    - type: WL
      offset: [1, 0]
      hp: 600
    - type: TU
      offset: [0, 1]
      hp: 400
    - type: TU
      offset: [0, -1]
      hp: 400
  guards:
    - role: guard
      weapon_type: melee
      count: 2
    - role: soldier
      weapon_type: ranged
      count: 3
  loot:
    Iron: 100
    Stone: 80
    Energy: 50
```

### Turret fix

Two changes in `combat_engine.py`:
1. Replace `building_type != "VV"` with a capability check: `if not building_has_capability(building, TURRET): continue` (using the existing capability infrastructure).
2. Replace `_get_nearby_players(building_loc, turret_radius)` with a spatial query on the PlanetRoom: `building.location.get_nearby_players(bx, by, turret_radius)`.

And one addition to `PlanetRoom`:
```python
def get_nearby_players(self, x: int, y: int, radius: int) -> list:
    """Return player Characters within Manhattan distance radius of (x, y)."""
    return [
        obj for obj in self.get_objects_in_area(
            x - radius, y - radius, x + radius, y + radius
        )
        if hasattr(obj, "has_account") and obj.has_account
        and abs(int(getattr(obj.db, "coord_x", x)) - x)
          + abs(int(getattr(obj.db, "coord_y", y)) - y) <= radius
    ]
```

**Signature reconciliation (must not be left inconsistent):** the EXISTING combat hook `CombatEngine._get_nearby_players(location, radius)` calls `location.get_nearby_players(radius)` — a 1-arg contract that existing turret tests rely on via fakes. The new `PlanetRoom.get_nearby_players(x, y, radius)` above uses a 3-arg signature, so the two contracts conflict. The design must reconcile this rather than leave both in place, by choosing one of:
- **(a)** Update `process_turrets` to call the 3-arg `PlanetRoom.get_nearby_players(x, y, radius)` method directly, AND update the affected turret tests/fakes to the 3-arg signature; or
- **(b)** Retire the old 1-arg `_get_nearby_players` hook entirely, migrating any remaining callers and their test fakes to the 3-arg method.

Either way, the leftover 1-arg `_get_nearby_players` hook and its test fakes MUST be reconciled with the new 3-arg method — they cannot coexist with mismatched signatures.

## Components and Interfaces

This section summarizes the new and modified components described above. It is an index of responsibilities and interfaces; details live in the Architecture section.

- **CombatEngine (modified, `combat_engine.py`)** — `process_turrets` fixed to gate on the `TURRET` capability and use the 3-arg spatial query; new `_is_enemy_npc(target)` (checks `db.npc_type == "enemy"`) and `_handle_enemy_death(target, attacker)` (awards `xp_kill`, publishes `NPC_ELIMINATED`, deletes the target) branch in `_finalize_hit`, ordered above the `_is_player` check.
- **PlanetRoom.get_nearby_players(x, y, radius) (new method)** — returns player Characters within Manhattan `radius` of `(x, y)` via a spatial area query; replaces the legacy 1-arg turret lookup.
- **owner_has_active_hq(owner, planet) (new, `world/utils.py`)** — cheap live predicate returning True when the owner has a non-destroyed HQ on the given planet; shares one enumeration helper with `BuildingSystem._player_has_hq`. Gates turrets, guard AI, production, and building commands.
- **GuardCombatSystem (new, `world/systems/guard_combat_system.py`)** — `BaseSystem` registered as tick step `"guard_combat"` (before `combat_resolution`); acquires nearby non-owner players within `guard_aggro_radius`, skips guards whose owner fails `owner_has_active_hq`, and queues attacks via the combat engine.
- **_GuardWeapon (new)** — synthetic, ammo-free weapon object (same pattern as `_TurretWeapon`) exposing `damage`, `range`, and `weapon_type` for guard-queued attacks.
- **BaseEliminationHandler (new)** — `BUILDING_DESTROYED` subscriber implementing the HQ-destruction fork: PvE path (wipe sentinel buildings/NPCs, award `xp_hq_destroy` + loot, queue respawn) vs PvP path (notify, let `owner_has_active_hq` gate systems inert); publishes `BASE_ELIMINATED`.
- **OutpostSpawnerSystem (new, `world/systems/outpost_spawner.py`)** — `spawn_initial(planet)` places outposts/fortresses at server start; `process_respawns(tick_number)` (tick step `"outpost_respawn"`) spawns pending respawns whose cooldown expired, using the placement validation algorithm and `outposts.yaml` templates.
- **SentinelCharacter (new typeclass)** — a `Character`/`CombatCharacter` subclass owning each NPC base; inherits `get_buildings()` for ownership enumeration while being never puppeted, never listed in `who`, and never treated as an online player.

## Data Models

### New BalanceConfig fields (`world/definitions.py`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `xp_hq_destroy` | int | 500 | XP for destroying an NPC HQ |
| `guard_melee_damage` | int | 10 | Outpost guard damage |
| `guard_ranged_damage` | int | 15 | Fortress guard damage |
| `guard_ranged_range` | int | 4 | Fortress guard weapon range |
| `guard_aggro_radius` | int | 5 | Guard detection distance |
| `outpost_respawn_ticks` | int | 600 | Ticks before cleared base respawns |
| `outpost_count` | int | 5 | Outposts per planet at init |
| `fortress_count` | int | 2 | Fortresses per planet at init |
| `outpost_guard_hp` | int | 80 | HP per outpost guard |
| `fortress_guard_hp` | int | 150 | HP per fortress guard |

### New constants (`world/constants.py`)

```python
TURRET = "turret"  # building capability for turret auto-fire
# (HEADQUARTERS, COMBAT_BARRIER, STORAGE already exist)
```

### New event kinds

| Event | Publisher | Data |
|-------|-----------|------|
| `NPC_ELIMINATED` | CombatEngine | `attacker`, `victim`, `tile` |
| `BASE_ELIMINATED` | BaseEliminationHandler | `attacker`, `sentinel`, `tier`, `planet`, `x`, `y` |

### New notification kinds

| Kind | Formatter | Example output |
|------|-----------|----------------|
| `npc_killed` | `_fmt_npc_killed` | `[Combat] Killed Guard #2. +100 XP.` |
| `base_eliminated` | `_fmt_base_eliminated` | `[Combat] Outpost eliminated! +500 XP. Loot dropped at (34, 67).` |
| `base_deactivated` | `_fmt_base_deactivated` | `[Alert] Your HQ was destroyed! Base deactivated — rebuild an HQ.` |
| `base_reactivated` | `_fmt_base_reactivated` | `[Alert] HQ rebuilt! Base systems are back online.` |

## Error Handling

- **Spawner placement failure:** if no valid coordinate is found after N attempts (e.g. 100), skip that base and log a warning — the planet is too crowded. Reduce counts gracefully.
- **Sentinel cleanup:** if a sentinel's last building is destroyed outside the HQ-destruction path (e.g. admin delete), a periodic GC pass or the spawner's bookkeeping detects orphaned sentinels and cleans them up.
- **Guard without an owner:** if `db.owner` is None on a guard (shouldn't happen), the guard AI skips it (already guarded by `owner_has_active_hq(None, ...)` → False).
- **Exception isolation:** guard AI and spawner run inside the tick loop's per-step exception isolation (`scripts.py:127-134`), so a failure in one doesn't halt the game.

## Testing Strategy

- **Unit tests per system:** GuardCombatSystem (target acquisition, range, owner-skip, deactivation skip, weapon assignment), BaseEliminationHandler (PvE wipe vs PvP deactivation, reward calculation), OutpostSpawner (placement validation, respawn cooldown, template parsing).
- **Integration tests:** full combat loop with a player attacking an NPC base — guard fires back, turret fires, player destroys HQ, base wipes, XP awarded.
- **Property tests:** guard never attacks own owner; turret never fires when deactivated; respawn cooldown honored; placement always valid.
- **Regression:** the full existing test suite remains green. Turret fix verified by updating existing turret tests to use `"TU"` and checking they still pass.
- **AST guard:** new systems must satisfy the existing layering invariant (framework-free `world/systems/` classes, no Evennia imports).

## Correctness Properties

### Property 1: C1 — A player destroying an NPC building always earns `xp_building_destroy` (is_owner guard: sentinel.id ≠ player.id → True).

**Validates: Requirements 4.4, 6.3**

### Property 2: C2 — A player destroying an NPC HQ earns `xp_building_destroy + xp_hq_destroy`.

**Validates: Requirements 6.3**

### Property 3: C3 — A deactivated player base reactivates the tick after a new HQ completes.

**Validates: Requirements 2.3**

### Property 4: C4 — A guard never attacks its own owner.

**Validates: Requirements 3.4**

### Property 5: C5 — A turret never fires when its owner has no HQ.

**Validates: Requirements 1.5**

### Property 6: C6 — Enemy NPCs at 0 HP are deleted, not respawned.

**Validates: Requirements 4.2, 4.3**

### Property 7: C7 — Player agents at 0 HP are still respawned (unchanged).

**Validates: Requirements 12.3**

### Property 8: C8 — Cleared NPC bases respawn after exactly `outpost_respawn_ticks`.

**Validates: Requirements 7.3**

### Property 9: C9 — NPC base buildings render as enemy (red) on the map.

**Validates: Requirements 11.2**

### Property 10: C10 — Sentinel Characters never appear in `who` or receive notifications.

**Validates: Requirements 5.6**
