# Design Document

## Overview

NPC outposts and fortresses — enemy bases scattered across the procedural map that players can raid for XP and loot. Built on the existing combat engine, building system, and NPC typeclass. Every defensive system introduced (turret fix, guard AI, base-elimination consequence) is ownership-generic and immediately benefits PvP too.

The feature decomposes into six independent, incrementally-shippable phases. Each produces a working, testable increment. The phases are ordered by dependency (turret fix unblocks guard AI; base-deactivation predicate is cheap and broadly useful; guard AI needs enemy NPCs to fight; spawner needs all prior systems to place a functional base).

## Architecture

### Ownership model: Sentinel Characters

Each NPC base is owned by a dedicated **Sentinel Character** — an Evennia `DefaultCharacter` created at spawn, never puppeted, whose `.id` serves as the ownership key for all base buildings and guards. This reuses the existing `db.owner` reference and `is_owner(.id)` comparison everywhere without inventing a faction system.

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

This gates (by live query, no stored state):
- `process_turrets` — skip buildings whose owner fails the check
- `GuardCombatSystem.process_tick` — skip guards whose owner fails
- `EquipmentSystem.process_production` — skip buildings whose owner fails
- Building commands (craft, research, deposit, withdraw, closeexit/openexit, assign/unassign) — reject with a message

When the player rebuilds an HQ (construction completes → the building exists → predicate flips to True), everything reactivates next tick. Zero per-building bookkeeping.

### Guard combat AI

A new `GuardCombatSystem(BaseSystem)` in `world/systems/guard_combat_system.py`, registered as tick step `"guard_combat"` between `npc_movement` and `combat_resolution` in `TICK_STEP_ORDER`.

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
- **Regression:** all 1922 existing tests remain green. Turret fix verified by updating existing turret tests to use `"TU"` and checking they still pass.
- **AST guard:** new systems must satisfy the existing layering invariant (framework-free `world/systems/` classes, no Evennia imports).

## Correctness Properties (traceable)

1. **C1:** A player destroying an NPC building always earns `xp_building_destroy` (is_owner guard: sentinel.id ≠ player.id → True). _(Req 4.4, 6.3)_
2. **C2:** A player destroying an NPC HQ earns `xp_building_destroy + xp_hq_destroy`. _(Req 6.3)_
3. **C3:** A deactivated player base reactivates the tick after a new HQ completes. _(Req 2.3)_
4. **C4:** A guard never attacks its own owner. _(Req 3.4)_
5. **C5:** A turret never fires when its owner has no HQ. _(Req 1.5)_
6. **C6:** Enemy NPCs at 0 HP are deleted, not respawned. _(Req 4.2, 4.3)_
7. **C7:** Player agents at 0 HP are still respawned (unchanged). _(Req 12.3)_
8. **C8:** Cleared NPC bases respawn after exactly `outpost_respawn_ticks`. _(Req 7.3)_
9. **C9:** NPC base buildings render as enemy (red) on the map. _(Req 11.2)_
10. **C10:** Sentinel Characters never appear in `who` or receive notifications. _(Req 5.6)_
