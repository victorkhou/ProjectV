# Design Document: Early-Game Rebalance & Agent Purpose

## Overview

This design restructures the first-hour experience by adding economy XP,
redistribution of building unlock tiers, making agents immediately purposeful
(guard/scout as army roles, delivery gate lowered, cap unfrozen), injecting
variable rewards into the harvest and PvE combat loops, and layering a
directive system on top of the existing EventBus.

**Guiding constraints:**
- One player level bar. Economy XP is finite/front-loaded; combat XP is
  renewable/risky. No dual-track.
- Gates split by what they protect: self-facing → level; world-facing → level +
  deed; upgrades → cost only.
- Minimal new systems — reuse EventBus, balance.yaml, existing schemas.
- Agent changes are role-table + balance data; no new Script classes.

---

## Architecture

### XP Flow (extended)

```
                         ┌─────────────────────────────────────────────┐
                         │           RankSystem.award_xp               │
                         │  (single choke point for all player XP)     │
                         └──────────▲──────────▲──────────▲────────────┘
                                    │          │          │
                ┌───────────────────┤          │          ├────────────────────┐
                │                   │          │          │                    │
     CombatEngine          BuildingSystem   ResourceSystem   AgentSystem   DirectiveSystem
   (kill/dmg/destroy)    (build/upgrade XP)  (harvest XP)  (train XP)   (directive rewards)
```

All economy XP routes through the existing `RankSystem.award_xp(player, amount,
reason=...)` — no new XP pathway. The `reason` parameter (already present)
differentiates sources for analytics.

### Progression Supply Curve

```
XP/hr
 ^
 │                           ╱ combat (renewable, risky)
 │                         ╱
 │   ___economy one-time__╱____________________________________
 │  /                    •    harvest trickle ceiling (~900/hr)
 │ / (building/upgrade/  •
 │/   train/directives)  •
 └────────────────────────────────────────────────────────────> time
        0-30min           30-60min         ongoing
```

Economy sources naturally exhaust: the non-combat directive chain totals ~200
one-time XP, build/train awards are one-time per building/agent, and the flat
upgrade award (D6) has a finite lifetime supply of ~1,440 XP (12 building
types × 4 upgrades × 30), time-gated by exponential upgrade timers. The
harvest trickle tops at ~15 XP/min of continuous clicking. An honest builder
reaches ~L8–9 in hour one, with a long-run no-combat ceiling of ~L13; the
curve crosses into combat-dominant territory as the one-time supply thins —
the "Act 2" transition.

---

## Components and Interfaces

### 1. `data/config/balance.yaml` — New Economy XP Keys

```yaml
# --- Economy XP (Req 1) ---
xp_build_complete: 30
xp_upgrade_complete: 30        # flat per completed upgrade (D6 — no ×new_level)
xp_harvest_action: 1
xp_agent_trained: 40
xp_hq_destroy: 300             # down from 500 (flatten one-fight vault)

# --- Agent rebalance (Req 3, 4) ---
base_training_ticks: 90        # down from 300 (90s vs 5min)
scout_vision_radius: 5         # tiles visible around a patrolling scout

# --- Variable rewards (Req 7, 8) ---
harvest_crit_chance: 0.05      # 5% per manual harvest
harvest_crit_multiplier: 5     # ×5 yield on crit

# --- Guard per-kill mini-drops (Req 8.2) ---
guard_loot_chance: 0.4         # 40% per guard kill
guard_loot_amount: 10          # resource units dropped

# --- Gear drops on HQ destruction (Req 8.3-4) ---
# (per-template in outposts.yaml; these are fallback defaults)
gear_drop_chance: 0.15
rare_gear_chance: 0.03
```

### 2. `data/definitions/buildings.yaml` — Retiered Rank Requirements

Level values changed per Req 2. `unlock_deed` added as an optional field
(Req 9):

```yaml
- name: Barracks
  rank_requirement: 7
  unlock_deed: outpost_cleared    # requires deed (count ≥ 1)
  ...

- name: Lab
  rank_requirement: 11
  unlock_deed: outpost_cleared    # counted gate (D9): destroy 3 outposts
  unlock_deed_count: 3
  ...
```

**Counted-gate representation (D9):** the design uses an explicit
`unlock_deed_count` field (default 1) alongside `unlock_deed`, rather than
parsing a count suffix out of a composite deed id. The requirements' alias
`outposts_cleared_3` resolves to base deed `outpost_cleared` with required
count 3. Explicit fields keep the YAML self-describing and avoid fragile
string parsing; boolean deeds are simply count ≥ 1.

### 3. `world/systems/building_system.py` — Economy XP Awards (Req 1)

**`_complete_construction`** and **`_complete_upgrade`** gain a tail call:

```python
# After marking building live:
self._award_economy_xp(player, "build_complete")

# In _complete_upgrade (D6: flat award, no ×new_level):
self._award_economy_xp(player, "upgrade_complete")
```

**Both completion paths award (D1):** the player-present path
(`process_construction_tick` → `_complete_construction(player, building)`)
already holds the player; the Engineer path (`process_agent_construction` /
`EngineerScript._complete_construction`) resolves the owner from
`building.db.owner` and awards the same amount. One-time per building either
way — no farm surface, no delegation penalty.

**`_award_economy_xp`** is a thin helper:

```python
def _award_economy_xp(self, player, reason, amount=None):
    if amount is None:
        amount = getattr(self.registry.balance, f"xp_{reason}", 0)
    if amount <= 0 or player is None:
        return
    rank_system = self._rank_system
    if rank_system is not None:
        rank_system.award_xp(player, amount, reason=reason)
```

**`_validate_construction`** gains a deed check (Req 9). `db.deeds` is a dict
(deed-id → count, D9); the required count comes from the optional
`unlock_deed_count` field (default 1):

```python
unlock_deed = getattr(building_def, "unlock_deed", None)
if unlock_deed:
    required = getattr(building_def, "unlock_deed_count", 1) or 1
    deeds = getattr(getattr(player, "db", None), "deeds", None) or {}
    if deeds.get(unlock_deed, 0) < required:
        return None, f"Requires: {DEED_DESCRIPTIONS.get(unlock_deed, unlock_deed)}"
```

### 4. `world/systems/resource_system.py` — Harvest XP + Crit (Req 1.3, 7)

In `process_harvest_tick`, after a successful yield:

```python
# Economy XP trickle (Req 1.3)
self._award_economy_xp(player, "harvest_action")

# Harvest crit roll (Req 7)
import random
bal = self.registry.balance
if random.random() < bal.harvest_crit_chance:
    bonus = amount * (bal.harvest_crit_multiplier - 1)
    # spawn extra drop
    self._spawn_resource_drop(location, resource, bonus, x=x, y=y)
    self.notify(player, "harvest_crit", amount=bonus, resource=resource)
```

### 5. `world/systems/agent_system.py` — Train XP + Cap Ceiling Fix (Req 1.4, 3)

In `complete_training`, after creating the agent:

```python
self._award_economy_xp(player, "agent_trained")
```

### 6. `world/systems/agent_progression.py` — Cap Ceiling Change (Req 3.1)

```python
def get_cap_ceiling(self, agent: Any) -> int:
    # Was: max(1, owner_level - 1) — frozen at L1 for new players
    return max(1, self.get_owner_level(agent))
```

### 7. `data/definitions/ability_gates.yaml` — Delivery Gate (Req 3.2)

```yaml
- key: delivery
  required_level: 5    # was 21
```

### 8. `typeclasses/agent_scripts.py` — Role Table Changes (Req 4, 6)

```python
@dataclass(frozen=True)
class RoleSpec:
    name: str
    script: type
    script_key: str
    buildings: tuple[str, ...] = ()
    army: bool = False
    hidden: bool = False   # NEW: hidden roles excluded from VALID_ROLES

AGENT_ROLES: dict[str, RoleSpec] = {
    "harvester": RoleSpec("harvester", HarvesterScript, "harvester_script", buildings=("EX",)),
    "engineer": RoleSpec("engineer", EngineerScript, "engineer_script", buildings=("AR", "LB")),
    "soldier": RoleSpec("soldier", SoldierScript, "soldier_script", army=True, hidden=True),
    "guard": RoleSpec("guard", PatrolBehavior, "patrol_behavior", army=True),  # was buildings=("TU",)
    "scout": RoleSpec("scout", PatrolBehavior, "patrol_behavior", army=True),  # was buildings=("RD",)
    "medic": RoleSpec("medic", MedicScript, "medic_script", army=True, hidden=True),
}
```

Derived `VALID_ROLES` gains a filter:
```python
VALID_ROLES: tuple[str, ...] = tuple(
    spec.name for spec in _AGENT_ROLES.values() if not spec.hidden
)
```

### 9. `world/coordinate/fog_of_war.py` — Scout Vision (Req 5)

In `get_visible_tiles`, after the building-vision loop:

```python
# Scout-agent vision circles (Req 5)
scout_radius = getattr(self, "_scout_vision_radius", 0)
if scout_radius > 0:
    for agent in player_scouts:
        if _is_scout_active(agent):
            ax, ay = _get_coord(agent, "coord_x"), _get_coord(agent, "coord_y")
            _add_chebyshev_circle(visible, ax, ay, scout_radius)
```

The caller (map_data_provider / GameTickScript) passes `player_scouts` — the
player's agents with `role == "scout"` and not incapacitated, same-planet.
`_scout_vision_radius` is set from balance at system init.

### 10. `data/definitions/outposts.yaml` — Loot Ranges + Gear (Req 8)

```yaml
outpost:
  loot:
    Iron: [20, 40]          # range syntax
    Stone: [10, 25]
  gear_drop_chance: 0.15
  rare_gear_chance: 0.03
  gear_pool: [combat_knife, combat_helmet, kevlar_vest]
  rare_pool: [sniper_rifle, jetpack]
  guard_loot_chance: 0.4
  guard_loot_amount: [5, 15]
```

### 11. `world/systems/base_elimination.py` — Loot Resolution (Req 8)

The existing `_drop_loot` method gains range resolution and gear rolls:

```python
def _drop_loot(self, template, location, x, y, attacker):
    import random
    for resource, spec in template.loot.items():
        if isinstance(spec, list):
            amount = random.randint(spec[0], spec[1])
        else:
            amount = spec
        self._spawn_drop(location, resource, amount, x, y)

    # Gear drop
    if random.random() < template.get("gear_drop_chance", 0):
        pool = template.get("gear_pool", [])
        if pool:
            item_key = random.choice(pool)
            self._spawn_gear(location, item_key, x, y)

    # Rare gear
    if random.random() < template.get("rare_gear_chance", 0):
        pool = template.get("rare_pool", [])
        if pool:
            item_key = random.choice(pool)
            self._spawn_gear(location, item_key, x, y)
```

### 12. `world/systems/directive_system.py` (NEW) — Onboarding (Req 10)

A `BaseSystem` subclass. Subscribes to EventBus events listed in the
directives YAML. On event → checks if the player is on the matching directive
→ validates condition → awards → advances.

```python
class DirectiveSystem(BaseSystem):
    def __init__(self, registry, event_bus):
        super().__init__(registry, event_bus)
        self._directives = registry.directives  # ordered list from YAML
        self._subscribe_all()

    def _subscribe_all(self):
        events = {d["trigger_event"] for d in self._directives}
        for event in events:
            self.event_bus.subscribe(event, self._on_event)

    def _on_event(self, event_name, **kwargs):
        for directive in self._by_event.get(event_name, ()):
            player = self._resolve_player(directive, kwargs)
            if player is None:
                continue  # resolved actor is not a player — discard
            idx = getattr(player.db, "directives_progress", 0) or 0
            if idx >= len(self._directives):
                continue  # all done
            if self._directives[idx] is not directive:
                continue  # player isn't on this step
            if not self._check_condition(directive, kwargs):
                continue
            self._complete_directive(player, directive, idx)

    def _resolve_player(self, directive, payload):
        """Payload adapter (D7). Each directive may declare `player_key`
        (default "player") naming the payload key that carries the acting
        entity. NPC/agent/turret actors resolve to their owner via
        `db.owner` — delegation is never penalized (consistent with D1).
        Events whose resolved actor is not a player are discarded."""
        actor = payload.get(directive.get("player_key", "player"))
        if actor is not None and not _is_player(actor):
            actor = getattr(getattr(actor, "db", None), "owner", None)
        return actor if actor is not None and _is_player(actor) else None
```

**New EventBus events (D8):** the chain requires four new events published by
the relevant systems: `AGENT_TRAINED`, `AGENT_ASSIGNED`, `ITEM_EQUIPPED`, and
`PATROL_SET`. `SCOUT_REVEALED_TILES` is dropped — directive 10 triggers on
`PATROL_SET` with condition `role: scout` instead.

**Deed subscription reuse (R9.3):** the BASE_ELIMINATED → deed award
subscription uses the same resolution helper with `player_key: attacker`.

### 13. Player Data Model Additions

In `PLAYER_DEFAULTS` (`typeclasses/characters.py`):

```python
"deeds": {},                       # Req 9 (D9): deed-id → count; boolean = count ≥ 1
"directives_progress": 0,         # Req 10
"directives_muted": False,        # Req 10.7 (D2): dismiss-all flag
```

**Dismiss-all semantics (D2):** when `directives_muted` is True, `_on_event`
still matches and advances `directives_progress` (so a returning player isn't
stuck mid-chain) but skips BOTH the reward and the notifications. `directives
off` sets the flag; `directives on` clears it, resuming rewards from the
current position. Rewards forfeited while muted are not retroactively paid.

### 14. `data/definitions/directives.yaml` (NEW)

```yaml
- key: build_hq
  description: "Build your Headquarters"
  trigger_event: construction_completed
  condition:
    building_type: HQ
  reward:
    xp: 15

- key: build_extractor
  description: "Build an Extractor on a resource tile"
  trigger_event: construction_completed
  condition:
    building_type: EX
  reward:
    xp: 15
    Wood: 10

# ... (full sequence per Req 10 table; non-combat steps total 200 XP — D6)

- key: destroy_outpost
  description: "Destroy an NPC outpost"
  trigger_event: base_eliminated
  player_key: attacker          # D7: BASE_ELIMINATED publishes attacker=
  condition:
    base_kind: outpost
  reward:
    xp: 50
    Iron: 30

- key: scout_patrol
  description: "Explore with a scout patrol"
  trigger_event: patrol_set     # D8: replaces SCOUT_REVEALED_TILES
  condition:
    role: scout
  reward:
    xp: 25
```

### 15. Tech Repair — Research Becomes Real (Req 13, D5)

**`world/systems/rank_system.py` — rank paths stop touching techs.**
Remove the `_unlock_for_rank` tech auto-grant and the `_revoke_above_rank`
tech revocation calls from the promotion/demotion paths. Rank stays cosmetic
with respect to technologies — planet access and agent caps are unchanged.
Research at a Lab becomes the only tech-acquisition path.

**`world/systems/tech_lab_system.py` — `_apply_stat_bonus` →
`_apply_tech_effect`.** The effect-application path handles the five shipped
payload keys. Chosen mechanism: research writes per-player bonuses into a
`db.tech_bonuses` dict on the player, read at the existing computation points:

| Payload key | Consumer (read point) |
|-------------|-----------------------|
| `building_hp` | hp_max computation for the owner's buildings |
| `damage` | CombatEngine `_get_attacker_bonus` |
| `damage_reduction` | CombatEngine armor-reduction path |
| `sight_range` | FogOfWar vision-radius path |
| `production_multiplier` | equipment/extractor production path |

```python
def _apply_tech_effect(self, player, tech_def):
    bonuses = player.db.tech_bonuses or {}
    for key, value in tech_def.effect_value.items():
        if key == "production_multiplier":
            bonuses[key] = bonuses.get(key, 1.0) * value
        else:
            bonuses[key] = bonuses.get(key, 0) + value
    player.db.tech_bonuses = bonuses
```

**Rationale for the `db.tech_bonuses` design:** the alternative — mutating
stats in place wherever each is stored — would have to touch every existing
building at research time plus every future construction/spawn path, and
would need inverse logic if a tech is ever removed. A single dict read at the
five existing computation points touches the fewest systems and is trivially
idempotent: `db.tech_bonuses` can be recomputed from `researched_techs` at
load.

**`data/definitions/technologies.yaml` — `required_rank` re-alignment
(Req 13.4):** earliest tech researchable at the new Lab gate (L11 = rank 3,
Corporal), remaining four spaced upward:

| Tech | Old rank | New rank |
|------|----------|----------|
| Reinforced Walls | Sergeant | **Corporal** (rank 3 — matches Lab at L11) |
| Improved Armor | Staff_Sergeant | **Sergeant** |
| Extended Range | Lieutenant | **Staff_Sergeant** |
| Advanced Weapons | Captain | **Lieutenant** |
| Rapid Production | Major | **Captain** |

**Migration (Req 13.5):** existing `researched_techs` granted by the old
auto-grant are grandfathered — left as-is, no retroactive revocation. Since
`db.tech_bonuses` is derived from `researched_techs`, grandfathered techs
gain their real effects on first recompute.

### 16. Housekeeping — Dead Config & Pool Validation (Req 11, D10)

- `production_scaling` **and** `xp_damage` are removed from `balance.yaml`,
  `definitions.py`, and `schema_validator.py` — both are loaded and validated
  but referenced by zero runtime code paths.
- SchemaValidator / registry load gains a validation pass over `gear_pool`
  and `rare_pool` keys in `outposts.yaml` against item definitions — unknown
  keys fail loudly at load time, so a drop roll can never silently no-op.

### 17. Progression Ladder — Hybrid Curve + Widening Bands (Req 14, D11)

**`world/constants.py` — ceiling raised, uniform-width math retired.**
`MAX_LEVEL` goes 60 → 100. `LEVELS_PER_RANK` and `FINAL_RANK_XP_PER_LEVEL`
are retired as progression math: widening bands replace the uniform rank
width, and the formula replaces threshold interpolation. Note:
`world/utils.get_player_level`'s legacy `rank_level → level` fallback mapping
(`(rank−1) × LEVELS_PER_RANK + 1`) must be updated to map through the band
table's start levels instead.

**Threshold formula — single source of truth (e.g. `world/progression.py`):**

```python
def xp_delta(level: int) -> int:
    """XP needed to go from level-1 to level (hybrid curve, D11)."""
    if level <= 1: return 0
    if level <= 20: return round(40 * 1.2 ** (level - 2))
    return round(xp_delta(20) * 1.05 ** (level - 20))

def xp_threshold(level: int) -> int:
    return sum(xp_delta(n) for n in range(2, level + 1))
```

The implementation may memoize/precompute a 100-entry table at import;
thresholds are integers, monotonically increasing by construction.

**Rank bands.** A `RANK_BANDS` table (rank number → (min_level, max_level))
per the R14 band table; `rank_from_level(level)` becomes a band lookup.
`ranks.yaml` keeps rank names/`agent_cap`/`planet_access` but its
`xp_threshold` fields are removed or become derived/display-only (consistent
with R2.2's treatment of `unlocks`).

**Migration (R14.8).** On login (`ensure_attributes` or a level-sync path),
recompute level from stored `combat_xp` via the new curve; keep
`max(stored_level, recomputed_level)` so no player is visibly demoted.

**Downstream notes.** Planet-access and agent-cap per-rank values are
unchanged (their effective levels shift with the bands — intentional);
building gates all reference levels ≤ 18, unaffected; the alliance
level-threshold table sums member levels and may be retuned later (out of
scope).

---

## Data Models

### balance.yaml additions (all `int` or `float`)

| Field | Type | Default |
|-------|------|---------|
| `xp_build_complete` | int | 30 |
| `xp_upgrade_complete` | int | 30 |
| `xp_harvest_action` | int | 1 |
| `xp_agent_trained` | int | 40 |
| `scout_vision_radius` | int | 5 |
| `harvest_crit_chance` | float | 0.05 |
| `harvest_crit_multiplier` | int | 5 |
| `guard_loot_chance` | float | 0.4 |
| `guard_loot_amount` | int | 10 |
| `gear_drop_chance` | float | 0.15 |
| `rare_gear_chance` | float | 0.03 |

### buildings.yaml additions (per-entry, optional)

| Field | Type | Default |
|-------|------|---------|
| `unlock_deed` | str \| null | null |
| `unlock_deed_count` | int | 1 |

### outposts.yaml additions (per-template)

| Field | Type |
|-------|------|
| `gear_drop_chance` | float |
| `rare_gear_chance` | float |
| `gear_pool` | list[str] |
| `rare_pool` | list[str] |
| `guard_loot_chance` | float |
| `guard_loot_amount` | int \| [int, int] |

### Player `db.*` additions

| Attribute | Type | Default |
|-----------|------|---------|
| `deeds` | dict[str, int] | `{}` (deed-id → count; boolean = count ≥ 1) |
| `directives_progress` | int | 0 |
| `tech_bonuses` | dict[str, int \| float] | `{}` (derived from `researched_techs`) |

### Progression constants and tunables (Req 14, D11)

| Field | Type | Value | Lives in |
|-------|------|-------|----------|
| `MAX_LEVEL` | int | 100 | `constants.py` |
| `RANK_BANDS` | dict[int, tuple[int, int]] | rank number → (min_level, max_level), per R14 band table | `constants.py` |
| `xp_curve_base_delta` | int | 40 | `balance.yaml` |
| `xp_curve_early_ratio` | float | 1.2 | `balance.yaml` |
| `xp_curve_late_ratio` | float | 1.05 | `balance.yaml` |
| `xp_curve_knee_level` | int | 20 | `balance.yaml` |

**Placement rationale:** the ratios and knee live in `balance.yaml` because
tuning them is a live-balance activity — the whole curve reshapes without a
code change. `MAX_LEVEL` and `RANK_BANDS` live in `constants.py` because
gates and code structure depend on them (band lookup, level-cap checks, the
`get_player_level` fallback) — they are structure, not tuning knobs.

---

## Error Handling

- Economy XP award failures (missing RankSystem, None player) are guarded the
  same way combat XP already is: silent no-op + log.
- Harvest crit `random.random()` is stdlib; no external dependency.
- Deed check produces a user-facing "Requires: X" message, same pattern as
  the existing rank-requirement refusal.
- DirectiveSystem event handler wraps in try/except per-player (one player's
  failure doesn't block others on the same event).
- Loot range with `min > max` (misconfigured YAML) is clamped: `randint(min(a,b), max(a,b))`.

---

## Correctness Properties

### Property 1: Economy XP is one-time or cooldown-bounded

**Validates: Requirements 1.1, 1.2, 12.1, 12.2, 12.3, 12.4**

Each economy XP source has a natural supply cap:
- Build: one-time flat award per building. Upgrade: flat 30 XP per completed
  upgrade (D6, no ×new_level) — lifetime supply ~1,440 XP (12 building types
  × 4 upgrades), time-gated by exponential upgrade timers.
- Agent trained: one event per agent slot × cap.
- Harvest action: bounded by `harvest_cooldown_ticks` (4s per action max).
- Directives: strictly once per player per step.

Corollary: no AFK pathway to max level through economy XP alone.

### Property 2: Single XP choke point preserved

**Validates: Requirements 1.1, 1.5**

All new XP routes through `RankSystem.award_xp`. No new `player.db.combat_xp +=`
writes. Level/rank sync and events fire exactly as they do for combat XP.

### Property 3: Agent role changes are backward-compatible

**Validates: Requirements 4.1, 4.3**

- Existing agents with `role == "guard"` assigned to a Turret continue working
  (GuardCombatSystem reads role, not building assignment).
- Making guard/scout army roles means they *don't require* a building, but
  they still *accept* one (the `BUILDING_ROLE_MAP` entry stays for optional
  station assignment).

### Property 4: Deed gates are additive, not replacing

**Validates: Requirements 9.2**

- If `unlock_deed` is null/absent, only the level gate applies (current behavior).
- No existing building gains a deed gate that would block a player who previously
  unlocked it by level alone — migration: `ensure_attributes` seeds `deeds` as
  `{}` (dict, D9), and existing players above the level for Barracks/Lab can
  build them at any time (deeds are earned retroactively on their next relevant
  event, or an admin grants them).

### Property 5: All five tech payload keys take effect

**Validates: Requirements 13.3**

For any researched technology, its `effect_value` payload key (`building_hp`,
`damage`, `damage_reduction`, `sight_range`, `production_multiplier`) is
recorded in `db.tech_bonuses` and read by its consumer path — no shipped tech
is a no-op.

### Property 6: Rank changes never touch technologies

**Validates: Requirements 13.1, 13.2**

For any promotion, no technology is granted; for any demotion, no researched
technology is revoked. Research at a Lab is the only acquisition path;
`researched_techs` is monotone under rank changes.

### Property 7: Directive credit resolves to the owner

**Validates: Requirements 10.8**

For any directive-matching event whose acting entity (per the directive's
`player_key`) is an NPC/agent/turret, credit resolves to the entity's owner
via `db.owner`; any event whose resolved actor is not a player is discarded
without side effects.

### Property 8: Gear pool keys always resolve to real items

**Validates: Requirements 11.5**

For any `gear_pool`/`rare_pool` entry that passes load-time validation, the
key resolves to a defined item — an unknown key fails loudly at load, so a
drop roll can never select a nonexistent item.

### Property 9: Threshold curve is monotone, anchored, and never demotes

**Validates: Requirements 14.2, 14.4, 14.8**

For all levels 1..100, `xp_threshold(level)` is strictly monotonically
increasing; the L2 threshold is exactly 40; and for any stored
(level, combat_xp) pair, migration yields
`max(stored_level, recomputed_level)` — a player's visible level is never
lowered by the rebalance.

---

## Testing Strategy

Unit tests (pytest, mocked where noted):

- **Economy XP awards** — mock RankSystem; assert `award_xp` amounts and
  `reason` values for build/upgrade/harvest/train, on BOTH construction
  completion paths (player-present and Engineer-agent).
- **Harvest crit** — seeded RNG; verify crit triggers spawn the bonus drop
  and fire the `harvest_crit` notification at the configured rate/multiplier.
- **Loot ranges + gear/rare rolls** — range syntax draws within `[min, max]`,
  fixed values unchanged; gear and rare pools roll independently and only
  select keys from the configured pools.
- **Counted deed gate** — construction refused below the required count with
  the "Requires: X" message; granted at/above the count; absent
  `unlock_deed` leaves level-only gating intact.
- **Directive flow** — advance on matching event/condition, one-time reward,
  mute semantics (progress advances, reward/notifications skipped), and the
  payload adapter resolving `attacker` → owner.
- **Tech effects** — all five payload keys (`building_hp`, `damage`,
  `damage_reduction`, `sight_range`, `production_multiplier`) land in
  `db.tech_bonuses` and are read by their consumer paths.
- **Rank/tech isolation** — promotion grants no techs; demotion revokes none;
  `researched_techs` unchanged across rank transitions.
- **Scout vision** — visible-tile set is the union of building vision and
  active-scout circles; incapacitated/off-planet/non-scout agents project
  nothing.
- **Cap ceiling** — `get_cap_ceiling` returns `max(1, owner_level)`.
- **Role hiding** — `VALID_ROLES` excludes hidden roles; `AGENT_ROLES`
  retains full entries; admin assignment of hidden roles still works.
- **Config guardrails** — field-count tests for `BalanceConfig` changes
  (added economy/reward keys present, `production_scaling` and `xp_damage`
  removed) and load-time gear-pool key validation failing loudly on unknown
  keys.
- **Threshold formula** — knee continuity at L20/21 (the L21 delta derives
  from `delta(20)`, no discontinuity), monotonicity property test over
  1..100, and checkpoint spot values (L2 = 40, L3 = 88, L9 = 660,
  L20 = 6,190, L100 ≈ 1.09M).
- **Band lookup** — `rank_from_level` maps band edges correctly: Corporal
  begins at L11; Marshal only at L100.
- **Migration keeps-higher-level rule** — recomputing level from stored XP
  never lowers a player's visible level (`max(stored, recomputed)`).

Smoke test:

- **Live-boot pass** — mirror the tasks' Verification section: boot the
  server, run through the early-game loop (build, train, harvest, patrol,
  outpost raid, research) and confirm awards, directives, and unlocks fire
  in-game.

---

## XP Threshold Re-calibration Reference

Thresholds are derived from the hybrid formula (Component 17, Req 14/D11) —
40 XP at L2, deltas +20%/level through L20, then +5%/level to L100. Selected
checkpoints:

| Level | XP threshold (hybrid curve) | Target minutes (builder) |
|-------|-----------------------------|--------------------------|
| 1 | 0 | 0 (spawn) |
| 2 | 40 | ~2–3 min (HQ + EX = 60 XP) |
| 3 | 88 | |
| 5 | 215 | ~15 min |
| 8 | 517 | ~45 min |
| 9 | 660 | ~60 min (hour-one builder target) |
| 10 | 832 | |
| 11 | 1,038 | session two (Lab hook) |
| 13 | 1,585 | ~no-combat ceiling |
| 20 | 6,190 | ~2 hrs combat |
| 30 | ≈ 20,300 | |
| 50 | ≈ 80,600 | |
| 60 | ≈ 141,000 | |
| 100 | ≈ 1.09M | ~360 hrs (endgame levels ~15–18 hrs each) |

Note: the L2 delta of 40 XP is deliberately identical to the old curve, so
all economy-XP calibration in this spec carries over unchanged.
