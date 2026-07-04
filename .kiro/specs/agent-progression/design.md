# Design Document

## Overview

This feature generalizes experience/level/rank progression out of the player-only
`RankSystem` and onto the shared `CombatEntity` mixin
(`mygame/typeclasses/combat_entity.py`), so that players (`CombatCharacter`),
NPC agents (`NPC`), and any future combat entity share **one** progression
mechanism while each tracks its own state independently in the existing
attributes `db.combat_xp`, `db.level`, and `db.rank_level`.

Five design decisions are locked and shape the whole document:

1. **Strictly-below owner cap.** An Agent's `Effective_Level` is
   `max(1, min(Raw_Level, owner.db.level - 1))`. An agent can never reach or
   exceed its commander's level. Players have no owner cap.
2. **No XP banking — gain frozen at the cap ceiling.** An agent's `Cap_Ceiling`
   is `owner Entity_Level - 1` (floored at 1). WHILE an agent's level has
   reached its `Cap_Ceiling`, `AgentSystem` awards **no** further `combat_xp` to
   that agent — XP gain is frozen at the ceiling and no surplus accumulates.
   When the owner later levels up (the ceiling rises), the agent resumes earning
   up to the new ceiling; nothing banked is realized, because nothing was
   banked. The `Effective_Level` clamp `max(1, min(Raw_Level, owner_level - 1))`
   is **retained solely for the owner-DEMOTION edge case**: when an owner loses
   levels, an agent's stored level can exceed the new ceiling, and the clamp
   bounds the level used for gating and display without stripping earned XP.
   The freeze decision lives in `AgentSystem` (which knows the owner); the
   owner-agnostic `CombatEntity` still simply adds XP whenever it is called.
3. **Re-evaluation on any owner level change.** Because the cap is level-based
   (not rank-boundary based), owned agents are re-evaluated on *every* change
   to the owner's `db.level`, including changes that do not cross a rank
   boundary. A new `LEVEL_CHANGED` event carries this signal.
4. **Dynamic detach on cap drop.** When an agent's `Effective_Level` falls
   below a gated ability's required level, the corresponding behavior script is
   detached and the owner is notified, mirroring dynamic attach and the
   existing player tech-revoke behavior. The agent's enabled flag is retained so
   the ability re-attaches automatically if and when the agent re-qualifies.
5. **Explicit, sticky ability enablement (no auto-attach on unlock).** Reaching
   a gate's required level makes an ability **available** and notifies the owner
   how to enable it; it does **not** attach the behavior script. Each agent
   stores a set of explicitly-enabled gated-ability keys (default empty). A
   gated script attaches iff `Effective_Level >= required_level` **AND** the
   ability key is in the agent's enabled set. The enabled flag is sticky: the
   first enablement is always an explicit player command, and only
   re-attachment after a forced level-driven detach is automatic. A player
   disable clears the flag.

On top of shared progression, the feature adds **data-driven ability gating**.
The first gated ability is the autonomous `DeliveryBehavior` for harvesters,
unlocking at the first level of rank 5 (level 21), defined in a new validated
YAML source. Below the gate — or at/above the gate but not yet enabled by the
player — a harvester runs `HarvesterScript` only (produces drops at the
Extractor for manual collection). Only when the agent's `Effective_Level` meets
the gate **and** the player has enabled `delivery` for that agent do both
`HarvesterScript` and `DeliveryBehavior` attach. The gate mechanism is generic:
new gated abilities are added through data plus a script-resolution registry
entry, and the enable/disable/status mechanism works for any ability key, with
no changes to progression logic.

### Key architectural problem and resolution

`CombatEntity` is a pure-Python mixin with **no** Evennia base and must not
import Evennia or `RankSystem` (circular import / layering risk). Yet it must
derive `Raw_Level` from `db.combat_xp` using the same `ranks.yaml`-driven curve
that `RankSystem` uses today via its private `_rebuild_thresholds` /
`level_for_xp`.

Resolution: extract the level-threshold-table computation into a new
module-level helper, **`world/progression.py`**, that holds a precomputed
threshold table built from `DataRegistry.ranks` and the existing constants
(`LEVELS_PER_RANK`, `MAX_LEVEL`, `FINAL_RANK_XP_PER_LEVEL`). Both `CombatEntity`
and `RankSystem` derive levels from this single helper. The helper is pure
Python (no Evennia), takes/derives a registry reference, and is initialized once
at server start. `RankSystem._rebuild_thresholds` / `level_for_xp` /
`xp_for_level` become thin delegations; the module-level `rank_from_level` /
`level_range_for_rank` functions stay in `rank_system.py` and are reused
(re-exported) by the helper to avoid duplicating the level→rank rule.

## Architecture

### Layering

```
Command controllers (agent_commands.py / command_router)
        │ thin delegation
        ▼
Game systems (plain Python)
   RankSystem ──────────────┐         AgentSystem ───────────────┐
   (player progression,     │         (owner cap + cap ceiling,   │
    events, tech, messages) │          gate eval vs enabled set,  │
        │ delegates         │          XP award (frozen at cap),  │
        ▼                   │          enable/disable/status cmd, │
   world/progression.py  ◄──┘          dynamic attach/detach,     │
   (threshold table,                   roster display)            │
    level_for_xp,             ┌───────────────┘                   │
    rank_from_level reuse)    ▼                                    ▼
        ▲             CombatEntity (Entity_Progression)    DataRegistry
        │ used by     award_xp / deduct_xp / recompute      .ability_gates
        └──────────── _raw_level / get_raw_rank             .balance (agent XP)
                      (owner-agnostic, pure Python;               ▲
                       always adds XP when called)                │ validated by
EventBus ── LEVEL_CHANGED / RANK_PROMOTED / RANK_DEMOTED     SchemaValidator
   │  (published by RankSystem)                              .validate_ability_gates
   └─ subscribed by AgentSystem (re-evaluate owned agents)
                                                            GameTickScript
                                                            drives AgentSystem.process_tick
                                                            (per-tick XP + gate re-eval)
```

### Component responsibilities

- **`CombatEntity` (Entity_Progression)** — owner-agnostic. Owns `db.combat_xp`,
  `db.level`, `db.rank_level`. Awards/deducts XP (floor 0), recomputes
  `Raw_Level` and `Entity_Rank` from its own XP via `world/progression.py`. Knows
  nothing about owners, caps, or gates.
- **`world/progression.py`** — pure-Python module-level helper. Builds the
  level→XP threshold table from `DataRegistry.ranks` (preserving the existing
  linear-interpolation curve), exposes `level_for_xp`, `xp_for_level`,
  `rank_from_level`. Single source of truth for the curve.
- **`RankSystem`** — player-facing. Delegates XP mutation/level derivation to
  `CombatEntity` + `world/progression.py`. Retains: `RANK_PROMOTED` /
  `RANK_DEMOTED` (rank boundaries), the new `LEVEL_CHANGED` publication, player
  level-change messages, `_unlock_for_rank` / `_revoke_above_rank`, the legacy
  `_get_level` rule, and `get_status` display.
- **`AgentSystem`** — owner-aware. Computes `Effective_Level` and `Cap_Ceiling`,
  evaluates gated abilities against `Effective_Level` **and** the agent's
  enabled-ability set, dynamically attaches/detaches behavior scripts, awards
  agent XP at job/tick/death sites (data-driven amounts) **but freezes awards at
  the cap ceiling**, re-evaluates owned agents on owner level change, owns the
  enable/disable/status ability commands, and renders the roster. The freeze
  decision (whether to call `agent.award_xp` at all) lives here, not in
  `CombatEntity`.
- **`DataRegistry` / `SchemaValidator`** — load and validate the new
  `ability_gates.yaml` into an `Ability_Gate_Registry`; extend `BalanceConfig`
  with agent-XP fields and `validate_balance` accordingly.
- **`EventBus`** — adds `LEVEL_CHANGED`.
- **`GameTickScript`** — already calls `AgentSystem.process_tick`; that method
  gains per-tick time-served XP and gate re-evaluation.

### Sequence: agent earns XP, frozen at the cap ceiling

```mermaid
sequenceDiagram
    participant Tick as GameTickScript
    participant AS as AgentSystem
    participant CE as CombatEntity (agent)
    participant PR as world.progression
    Tick->>AS: process_tick(n)
    AS->>AS: award_agent_xp(agent, "harvest")
    AS->>AS: eff = compute_effective_level(agent); ceiling = get_cap_ceiling(agent)
    alt agent level >= ceiling  (frozen)
        Note over AS: at Cap_Ceiling → skip award entirely (no-op).<br/>No surplus accumulates; agent.award_xp NOT called.
    else below ceiling  (earning)
        AS->>CE: agent.award_xp(amount)  (amount from balance)
        CE->>CE: db.combat_xp += amount
        CE->>PR: level_for_xp(db.combat_xp)
        PR-->>CE: raw_level
        CE->>CE: db.level = raw_level; db.rank_level = rank_from_level(raw_level)
        AS->>AS: evaluate_gated_abilities(agent)  (eff + enabled set)
    end
```

The FREEZE decision lives in `AgentSystem`: it computes the cap ceiling and
decides whether to call `agent.award_xp` at all. `CombatEntity` stays
owner-agnostic — it simply adds XP whenever it is called.

### Sequence: owner level changes (rank-boundary AND in-between)

```mermaid
sequenceDiagram
    participant RS as RankSystem
    participant EB as EventBus
    participant AS as AgentSystem
    RS->>RS: _sync_level(player, old_level)
    RS->>RS: player.db.level = new_level
    alt level changed at all
        RS->>EB: publish(LEVEL_CHANGED, player, old_level, new_level)
        EB->>AS: on_owner_level_changed(player, old_level, new_level)
        AS->>AS: for each owned agent: recompute eff + Cap_Ceiling
        Note over AS: rise crossing gate + enabled → attach + notify active;<br/>rise crossing gate + not enabled → mark available + notify how to enable (no attach);<br/>drop below gate → detach, RETAIN enabled flag, notify re-lock;<br/>ceiling rose → award_agent_xp resumes earning up to new ceiling
    end
    alt rank boundary crossed
        RS->>EB: publish(RANK_PROMOTED/RANK_DEMOTED, ..., new_agent_cap)
        EB->>AS: handle_promotion/handle_demotion (reserve/restore — unchanged)
    end
```

Both subscriptions fire independently. `LEVEL_CHANGED` handles gate
re-evaluation (Req 15.4); `RANK_*` continue to handle reserve/restore (cap
counts). This keeps progression-gate logic decoupled from reserve logic.

## Components and Interfaces

### 1. `world/progression.py` (new module-level helper)

Pure Python. No Evennia imports. Holds the precomputed threshold table.

```python
# world/progression.py
from world.constants import MAX_LEVEL, LEVELS_PER_RANK, FINAL_RANK_XP_PER_LEVEL
# reuse the level→rank rule rather than duplicating it
from world.systems.rank_system import rank_from_level, level_range_for_rank

_level_thresholds: list[int] = []   # index 0 unused; 1..MAX_LEVEL

def build_thresholds(ranks) -> list[int]:
    """Build & cache the level→XP threshold table from registry ranks.

    Reproduces RankSystem._rebuild_thresholds EXACTLY: per rank, linearly
    interpolate LEVELS_PER_RANK levels between consecutive rank xp_thresholds;
    final rank uses FINAL_RANK_XP_PER_LEVEL per level.
    Idempotent; call once at server start and on hot-reload.
    """

def is_initialized() -> bool: ...

def level_for_xp(xp: int) -> int:
    """Highest level whose threshold <= xp (clamped 1..MAX_LEVEL)."""

def xp_for_level(level: int) -> int:
    """XP threshold to reach level (clamped 1..MAX_LEVEL)."""

def rank_for_level(level: int) -> int:
    """Delegates to rank_from_level (re-exported for callers)."""
```

Notes:
- To avoid a circular import (`rank_system` → `progression` → `rank_system`),
  `rank_from_level`/`level_range_for_rank` remain *defined* in `rank_system.py`
  (they depend only on `constants`), and `progression.py` imports them. The
  reverse dependency (`rank_system` calling threshold functions) is satisfied by
  `rank_system` importing `progression` lazily inside methods, or—preferred—by
  `progression.py` importing `rank_from_level` lazily inside `rank_for_level`
  and at the top only importing `constants`. The design uses a **lazy import of
  `rank_from_level` inside `progression.rank_for_level`** plus top-level
  `constants` import, so module import order is safe either way.
- Initialization: `build_thresholds(registry.ranks)` is called from
  `server/conf/game_init.py` right after `registry.load_all(...)`, and again
  inside `DataRegistry.reload_all` on successful swap (hot-reload).
- Fallback for tests / uninitialized state: if `is_initialized()` is `False`,
  `level_for_xp` lazily attempts `DataRegistry.get_instance()` to build the
  table; if that is unavailable it returns `1` (treat as level 1). This keeps
  `CombatEntity` usable in the Evennia-stub test suite.

### 2. `CombatEntity` (Entity_Progression) — `typeclasses/combat_entity.py`

New state initializer and methods added to the existing mixin. Stays pure
Python and owner-agnostic.

```python
def at_combat_entity_init(self):
    ...  # existing hp/equipment init
    # Progression state (Req 1.2-1.4, 12.1)
    self.db.combat_xp = 0
    self.db.level = 1
    self.db.rank_level = 1

# --- XP mutation (Req 1.5, 1.6, 1.7, 5.7, 6.2) ---
def award_xp(self, amount: int) -> int:
    """Add positive XP to db.combat_xp, recompute level/rank.
    Non-positive amount is a no-op (returns current xp). Returns new combat_xp."""

def deduct_xp(self, amount: int) -> int:
    """Subtract XP, floored at 0, recompute level/rank.
    Non-positive amount is a no-op. Returns new combat_xp."""

# --- derivation (Req 3.1-3.6) ---
def recompute_progression(self) -> None:
    """Recompute db.level (=raw_level) and db.rank_level from db.combat_xp.
    Called on every XP change, regardless of whether values differ (Req 3.4)."""

def get_raw_level(self) -> int:
    """Raw_Level: highest level whose threshold <= combat_xp (Req 3.2, 3.5, 3.6).
    Reads db.combat_xp with a 0 default (Req 12.2); never references an owner."""

def get_raw_rank(self) -> int:
    """rank_from_level(get_raw_level()) (Req 3.3)."""

def get_combat_xp(self) -> int:
    """db.combat_xp with 0 fallback for legacy entities (Req 12.2)."""
```

Behavioral details:
- `award_xp` / `deduct_xp` read `self.db.combat_xp or 0` (handles legacy/`None`),
  mutate, clamp `deduct` at 0, then call `recompute_progression`, which writes
  `db.combat_xp`, `db.level`, `db.rank_level` — initializing them for legacy
  entities on first earn/loss (Req 12.4).
- `get_raw_level` uses `world.progression.level_for_xp(self.get_combat_xp())`.
- No owner logic anywhere here (Req 14.9).

### 3. `RankSystem` refactor — `world/systems/rank_system.py`

**Delegates** (curve + XP mutation), **retains** (player semantics + events).

Changed:
- `_rebuild_thresholds`, `level_for_xp`, `xp_for_level` → delegate to
  `world.progression` (keep methods as thin wrappers so existing callers and
  tests keep working). `__init__` calls `progression.build_thresholds(registry.ranks)`
  if not already initialized.
- `award_xp(player, amount, reason="")` → captures `old_level = self._get_level(player)`,
  calls `player.award_xp(amount)` (the `CombatEntity` method) instead of mutating
  `db.combat_xp` directly, then `self._sync_level(player, old_level)`.
- `deduct_xp(player, amount)` → same pattern using `player.deduct_xp(amount)`.

Retained / extended:
- `_get_level` legacy rule (rank_level→level) unchanged (Req 4.6).
- `_sync_level` keeps: writing `db.level`/`db.rank_level` (now redundant with
  `CombatEntity` but harmless and preserves behavior when XP set directly), the
  player level-change `msg` (Req 4.5), `RANK_PROMOTED`/`RANK_DEMOTED` with
  `new_agent_cap` (Req 4.3, 4.4), `_unlock_for_rank`/`_revoke_above_rank`
  (Req 4.7).
- **New:** at the end of `_sync_level`, if `new_level != old_level`, publish
  `LEVEL_CHANGED`:

```python
if new_level != old_level:
    self.event_bus.publish(
        LEVEL_CHANGED, player=player,
        old_level=old_level, new_level=new_level,
    )
```

  This fires on *every* level change, including non-rank-boundary changes
  (Req 15.4), and is published after rank-event handling so reserve/restore (cap
  changes) is applied before gate re-evaluation reads the new state.
- `module functions rank_from_level`, `level_range_for_rank` stay here (reused by
  `progression.py`, `AgentSystem`, admin/game commands).

### 4. `AgentSystem` — `world/systems/agent_system.py`

New owner-cap, gate-evaluation, XP-award, and roster methods.

```python
# --- owner cap (Req 14) ---
def compute_effective_level(self, agent) -> int:
    """max(1, min(raw_level, owner_level - 1)).
    raw_level = agent.get_raw_level(); owner_level = owner.db.level (or via _get_level).
    If owner is None/missing → conservative floor of 1 (see Error Handling).
    Retained for gating/display, including the owner-demotion edge case where a
    stored raw level can exceed the new ceiling (Req 14.1, 14.5)."""

def get_owner_level(self, agent) -> int:
    """Resolve owning player's Entity_Level; reuse RankSystem._get_level rule
    for legacy owners; default 1 when owner missing."""

def get_cap_ceiling(self, agent) -> int:
    """Cap_Ceiling = max(1, owner_level - 1): the maximum Effective_Level the
    owner cap permits. Used to decide whether XP awards are frozen (Req 14.4)."""

# --- enabled-ability state (Req 12.1, 12.4, 17) ---
def get_enabled_abilities(self, agent) -> set:
    """Return the agent's stored set of enabled gated-ability keys.
    Reads agent.db.enabled_abilities (a persisted list); absent/None → empty set
    (legacy default, Req 12.4). Sticky and independent of attach state (Req 17.1)."""

def _set_enabled_abilities(self, agent, keys) -> None:
    """Persist the enabled-ability set back to agent.db.enabled_abilities as a list."""

# --- gate evaluation (Req 8, 9, 12.5, 13, 15, 17) ---
def evaluate_gated_abilities(self, agent, notify=True) -> None:
    """For each Ability_Gate in registry.ability_gates:
        required  = gate.required_level
        available = compute_effective_level(agent) >= required
        enabled   = gate.key in get_enabled_abilities(agent)
        script_cls = resolve_ability_script(gate.key)   # may be None
        if script_cls is None: log unresolved key, skip (Req 13.4); continue
        attached = _has_script(agent, script_cls.key)
        want = available and enabled
        if want and not attached:    attach + init state + notify "now active" (Req 9.2, 15.3, 17.3)
        elif attached and not want:  detach + (notify re-lock only when caused by
                                     level drop, i.e. not available) (Req 9.5, 9.6, 9.7, 15.4, 17.4)
        elif available and not enabled and not attached:
                                     mark available + notify how to enable, once
                                     (no attach) (Req 9.1, 15.2)
        else: no-op (Req 9.3, 9.8)."""

# --- ability enable/disable/status command backends (Req 16, 17) ---
def enable_ability(self, player, agent_id, key) -> str:
    """Validate ownership (Req 16.7) and that key is a known gate (Req 16.6).
    If effective_level >= required: add key to enabled set, attach script + init
    state, return confirmation (Req 16.2, 17.2). If below the gate: reject with
    the required level and do NOT attach or record (Req 16.3)."""

def disable_ability(self, player, agent_id, key) -> str:
    """Validate ownership + known key. Clear key from enabled set, detach that
    ability's script via _detach_single_script (HarvesterScript stays), confirm
    (Req 16.4, 9.6, 17.5)."""

def get_ability_status(self, player, agent_id) -> str | dict:
    """Validate ownership + agent exists. For each gate report state:
    'locked (Lv N)' when effective < required; 'available' when
    effective >= required but key not enabled; 'enabled' when key enabled
    (Req 16.5)."""

def award_agent_xp(self, agent, source: str) -> None:
    """FREEZE-AWARE award. Compute effective level + cap ceiling FIRST; if the
    agent's level already equals (or exceeds) its Cap_Ceiling, skip the award
    entirely — no surplus accumulates (Req 5.9, 14.4). Otherwise look up amount
    from balance by source key, call agent.award_xp(amount), then recompute
    effective level + evaluate_gated_abilities(agent) (Req 14.6). When the owner
    later raises the ceiling, awards resume on the next earning event (Req 5.10,
    14.8)."""

def apply_agent_death_loss(self, agent) -> None:
    """agent.deduct_xp(balance.agent_xp_death_loss); recompute + re-eval (Req 6).
    Death loss is never frozen (it reduces XP, never adds past the ceiling)."""

# --- owner level change (Req 14.7, 14.8, 15) ---
def on_owner_level_changed(self, player, old_level, new_level) -> None:
    """For each agent in get_agents(player): recompute Cap_Ceiling and call
    evaluate_gated_abilities(agent). A rise that newly crosses a gate marks the
    ability available + notifies (no attach) unless already enabled (then
    attaches + notifies active); a drop below a gate detaches but retains the
    enabled flag. Subscribed to LEVEL_CHANGED in game_init."""

# --- roster (Req 11) ---
def get_agent_progression_view(self, agent) -> dict:
    """{'effective_level', 'rank_name',
        'ability_status': {key: 'locked:N' | 'available' | 'enabled'},
        'capped_by_commander': raw_level > effective_level}."""
```

Helpers / changes:
- `resolve_ability_script(key)` uses a new extensible mapping
  `ABILITY_SCRIPT_MAP` (see Data Models). Returns the script class or `None`.
- `_attach_behavior_script(agent, role)` becomes **gate-aware** for harvesters:
  instead of relying on `ROLE_SCRIPT_MAP["harvester"]` being a static list, the
  attach path always attaches `HarvesterScript`, then calls
  `evaluate_gated_abilities(agent)` to conditionally attach `DeliveryBehavior`
  based on `Effective_Level` **and** the enabled set (Req 8.1, 8.2, 8.3, 8.5,
  8.6, 10.4, 12.5). `ROLE_SCRIPT_MAP` reverts `harvester` to `HarvesterScript`
  only; delivery is no longer unconditionally mapped to the role.
- New idempotent attach helper `_attach_single_script(agent, script_cls)` checks
  existing scripts by `key` before adding (Req 9.4) and initializes
  `delivery_state = DeliveryState.IDLE` when attaching `DeliveryBehavior`
  dynamically (Req 9.3).
- New `_detach_single_script(agent, script_key)` removes only the named gated
  script (distinct from the existing `_detach_behavior_script`, which removes all
  behavior scripts on reassignment). Used by gate re-lock and player disable so
  `HarvesterScript` stays attached when only `delivery` detaches (Req 9.5, 9.6,
  16.4).
- `assign_agent` keeps calling `_detach_behavior_script` then
  `_attach_behavior_script`; the latter now drives gate evaluation, so reserve
  restore + reassign attaches `DeliveryBehavior` iff effective ≥ gate **and** the
  ability is enabled (Req 10.4).
- XP-award call sites (all routed through the freeze-aware `award_agent_xp`):
  - Harvest production: in `HarvesterScript.at_repeat` after a successful drop,
    call back into AgentSystem via `game_systems["agent_system"].award_agent_xp(npc, "harvest")`
    (Req 5.1). The script already imports game systems lazily; a thin
    `_award_agent_xp(npc, source)` module helper in `agent_scripts.py` wraps the
    lookup so the script stays decoupled.
  - Delivery completion: in `DeliveryBehavior._deposit_and_return` after
    `deposit_resources`, award `"delivery"` (Req 5.2).
  - Construction/research completion: in `EngineerScript._complete_construction`
    / `_complete_research`, award `"construction"` (Req 5.3).
  - Combat kill: in `CombatEngine` defeat handling, when attacker is an agent
    award `"combat"` (Req 5.4); when an agent victim is defeated call
    `apply_agent_death_loss` (Req 6.1).
  - Time-served: in `AgentSystem.process_tick`, for each actively-assigned agent
    (has a non-empty `role`, not reserved/incapacitated) award `"time_served"`
    once per tick (Req 5.5); zero amount → no-op via `CombatEntity.award_xp`
    (Req 5.8, 7.7). The freeze check in `award_agent_xp` short-circuits this for
    agents already at their ceiling (Req 5.9).
- `process_tick` also performs a lightweight per-agent
  `evaluate_gated_abilities(agent)` guarded by try/except so an agent whose
  effective level changed via direct XP edits still converges (defensive;
  primary paths are award_agent_xp and on_owner_level_changed).

### 5. `NPC` — `typeclasses/npcs.py`

`at_object_creation` calls `at_combat_entity_init()` (already does), which now
initializes progression attrs; add explicit defaults there too for clarity and
to satisfy Req 12.1 (`combat_xp=0, level=1, rank_level=1`). Additionally,
agent creation initializes the per-agent enabled-ability set to empty —
`db.enabled_abilities = []` (Req 12.1). Legacy agents created before this
feature are handled by `CombatEntity.get_combat_xp` defaulting and lazy
persistence on first XP change (Req 12.2-12.4), and by
`AgentSystem.get_enabled_abilities` treating absent/None `enabled_abilities` as
an empty set (Req 12.4).

### 6. Ability-gate data pipeline

- **`AbilityGateDef`** dataclass in `world/definitions.py`.
- **`DataRegistry`**: load `definitions/ability_gates.yaml` (new entry in
  `_REQUIRED_FILES`), `_populate_ability_gates`, expose
  `self.ability_gates: dict[str, AbilityGateDef]` and getter
  `get_ability_gate(key)` / `get_ability_gates() -> list`. Validate via
  `SchemaValidator.validate_ability_gates`. Include in `reload_all` swap.
- **`SchemaValidator.validate_ability_gates(data)`** — see Data Models for rules.
- **`BalanceConfig`** gains agent-XP fields; `validate_balance` gains those int
  fields; `_load_balance` reads them.

### 7. Ability → script resolution mapping

A new extensible registry in `world/systems/agent_system.py` (or
`agent_scripts.py`, co-located with the script classes):

```python
# agent_scripts.py
ABILITY_SCRIPT_MAP: dict[str, type] = {
    "delivery": DeliveryBehavior,
}
```

`AgentSystem.resolve_ability_script(key)` returns
`ABILITY_SCRIPT_MAP.get(key)`. Adding a future gated ability = add a YAML entry
+ a map entry; no progression-logic change (Req 13.1-13.3). Unresolved key →
`None` → skip + log (Req 13.4).

### 8. EventBus — `world/event_bus.py`

Add `LEVEL_CHANGED = "level_changed"` and include in `ALL_EVENTS`. Payload:
`player`, `old_level`, `new_level`.

### 9. Wiring — `server/conf/game_init.py`

- After `registry.load_all(...)`: `progression.build_thresholds(registry.ranks)`.
- Subscribe `LEVEL_CHANGED` →
  `agent_system.on_owner_level_changed(kw["player"], kw["old_level"], kw["new_level"])`.
- Keep existing `RANK_PROMOTED`/`RANK_DEMOTED` subscriptions (reserve/restore).

### 10. Roster display & ability command — `commands/agent_commands.py`

**Roster (`sub_list`).** For each agent, query
`agent_system.get_agent_progression_view(agent)` and add to the existing line:
`Lv {effective_level} {rank_name}`, a per-ability **status** segment derived
from `ability_status` (each gate shown as `delivery: locked Lv21` /
`delivery: available` / `delivery: enabled`, or `no abilities` when the agent
qualifies for none — Req 11.2, 11.3), and a `|y[capped]|n` marker when
`capped_by_commander` is `True` (Req 11.4). Display uses `Effective_Level`, not
`Raw_Level` (Req 11.1, 11.2, 14.5). This replaces the earlier simple
"unlocked_abilities" list with explicit per-ability state.

**New `ability` subcommand (Req 16).** Extend the existing `CmdAgent`
`GameSubcommandRouter` (the `agent` router already exposing
`list/assign/unassign/train/patrol/stop`) with a `sub_ability` handler and a
registration entry in the `subcommands` dict:

```python
def sub_ability(self, args):
    """agent ability <id> <key> on|off   — enable/disable a gated ability
       agent ability <id>                 — show per-ability status."""
    caller = self.caller
    agent_system = _get_system(caller, "agent_system")
    if agent_system is None:
        caller.msg("Agent system unavailable.")
        return
    parts = args.split()
    if len(parts) == 1:                         # status form (Req 16.5)
        caller.msg(agent_system.get_ability_status(caller, parts[0]))
        return
    if len(parts) == 3 and parts[2].lower() in ("on", "off"):
        agent_id, key, toggle = parts[0], parts[1], parts[2].lower()
        if toggle == "on":                      # Req 16.2 / 16.3
            caller.msg(agent_system.enable_ability(caller, agent_id, key))
        else:                                   # Req 16.4
            caller.msg(agent_system.disable_ability(caller, agent_id, key))
        return
    caller.msg("Usage: agent ability <id> [<key> on|off]")

subcommands = {
    ...
    "ability": (sub_ability, "Enable/disable or view a gated ability", ""),
}
```

The handler is a thin delegator: ownership checks (Req 16.7), unknown-key
rejection (Req 16.6), gate-level rejection on enable-below-gate (Req 16.3),
attach-on-enable (Req 16.2), detach-keeping-HarvesterScript on disable
(Req 16.4), and status formatting (Req 16.5) all live in the `AgentSystem`
methods `enable_ability` / `disable_ability` / `get_ability_status`, keeping the
command layer logic-free and the enable/disable/status mechanism generic across
ability keys (Req 13.5).

## Data Models

### `AbilityGateDef` (new, `world/definitions.py`)

```python
@dataclass
class AbilityGateDef:
    """Definition for a data-driven ability gate."""
    key: str            # non-empty ability key, e.g. "delivery"
    required_level: int # 1..MAX_LEVEL inclusive
```

### `ability_gates.yaml` (new, `mygame/data/definitions/ability_gates.yaml`)

```yaml
# Ability gate definitions.
# required_level is an Entity_Level (1..MAX_LEVEL).
# delivery unlocks at the first level of rank 5 = (5-1)*5 + 1 = 21,
# capped at MAX_LEVEL (Req 6.6, 6.7).
- key: delivery
  required_level: 21
```

The `delivery` value (21) is produced by `(RANK_5 - 1) * LEVELS_PER_RANK + 1`
and clamped to `MAX_LEVEL`; encoded directly in data and tunable. Validation
enforces `1 <= required_level <= MAX_LEVEL`.

### `validate_ability_gates(data)` rules (`SchemaValidator`)

Mirrors existing validators (returns `list[str]`):
- `data` must be a list; else single type error.
- Each entry must be a dict; else `ability_gates[i]: expected dict...`.
- Required fields `{"key", "required_level"}`; missing → error naming entry +
  field (Req 7.5).
- `key` must be a non-empty string (Req 7.3); else error.
- `required_level` must be an `int` in `1..MAX_LEVEL` inclusive (Req 7.3);
  wrong type or out of range → error naming entry + field (Req 7.5).
- Duplicate `key` across entries → error naming the duplicate key (Req 7.4).

### `BalanceConfig` additions (`world/definitions.py`)

```python
# agent XP sources (all data-driven; any may be 0 → no-op) — Req 5.6, 5.7, 6.4
agent_xp_harvest: int = 5
agent_xp_delivery: int = 15
agent_xp_construction: int = 20
agent_xp_combat: int = 50
agent_xp_time_served: int = 0      # default 0 → no time-served progression (Req 5.8)
agent_xp_death_loss: int = 25
```

`validate_balance` adds these six keys to `int_fields`. `_load_balance` reads
each with the new defaults. `AgentSystem` maps source key → field:
`{"harvest": agent_xp_harvest, "delivery": agent_xp_delivery,
"construction": agent_xp_construction, "combat": agent_xp_combat,
"time_served": agent_xp_time_served}`; death uses `agent_xp_death_loss`.

### Per-entity attributes (read/written)

| Attribute | Entity | Read by | Written by |
|-----------|--------|---------|------------|
| `db.combat_xp` | player + agent | progression, RankSystem, AgentSystem, CombatEngine | `CombatEntity.award_xp/deduct_xp` |
| `db.level` | player + agent | RankSystem, AgentSystem (raw), roster | `CombatEntity.recompute_progression`, RankSystem._sync_level |
| `db.rank_level` | player + agent | display, legacy `_get_level` | `CombatEntity.recompute_progression`, RankSystem._sync_level |
| `db.owner` | agent | AgentSystem cap, scripts | AgentSystem (creation) |
| `db.role` | agent | time-served gate, scripts | AgentSystem assign/unassign/stop |
| `db.enabled_abilities` | agent | AgentSystem gate eval, status, roster | AgentSystem enable/disable; NPC init |
| `db.delivery_state` | agent | DeliveryBehavior | gate attach (init IDLE), DeliveryBehavior |

`Effective_Level` is **computed, never stored** — always derived from
`agent.get_raw_level()` and the owner's current `db.level` so it can never go
stale (Req 14.1, 14.5). `Cap_Ceiling` (`max(1, owner_level - 1)`) is likewise
computed on demand, never stored.

`db.enabled_abilities` is a **persisted list of ability keys** (treated as a
set), default empty. It holds the gated abilities the player has explicitly
enabled for that agent. It is sticky: it persists independently of whether the
behavior script is currently attached (Req 17.1), is only first populated by an
explicit player enable command (Req 17.2), is retained across a level-driven
forced detach (Req 9.5/15.4/17.4), and is cleared only by a player disable
(Req 16.4/17.5). Absent/None is treated as empty for legacy agents (Req 12.4).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all
valid executions of a system — essentially, a formal statement about what the
system should do. Properties serve as the bridge between human-readable
specifications and machine-verifiable correctness guarantees.*

This feature is well suited to property-based testing: the progression math,
the owner-cap function, gate evaluation, and schema validation are pure
functions over large input spaces (XP amounts, raw/owner level combinations,
gate definitions). The properties below are consolidated per the prework
reflection to remove redundancy; each provides unique validation value.

### Property 1: Progression derivation invariant

*For any* entity and *any* sequence of `award_xp` / `deduct_xp` operations,
after every operation `db.combat_xp` is a non-negative integer, `db.level`
equals `progression.level_for_xp(db.combat_xp)` and lies in `1..MAX_LEVEL`, and
`db.rank_level` equals `rank_from_level(db.level)` and lies in `1..NUM_RANKS`.

**Validates: Requirements 1.2, 1.3, 1.4, 1.7, 3.4, 3.5, 3.6, 6.3**

### Property 2: Level/rank curve correctness and player backward-compatibility

*For any* XP value, `progression.level_for_xp(xp)` is the highest level whose
`ranks.yaml`-derived threshold is `<= xp` (monotonic non-decreasing in `xp`,
with `threshold[level] <= xp < threshold[level+1]` for non-max levels), and the
level produced through the refactored `RankSystem.award_xp` path equals
`progression.level_for_xp(xp)` — i.e. the shared curve reproduces the existing
player curve exactly.

**Validates: Requirements 3.1, 3.2, 3.3, 4.1**

### Property 3: Award/deduct arithmetic with zero floor

*For any* starting `combat_xp` and *any* amount, `award_xp(amount)` increases
`combat_xp` by exactly `amount` when `amount > 0` and leaves it unchanged
otherwise; `deduct_xp(amount)` sets `combat_xp` to `max(0, start - amount)` when
`amount > 0` and leaves it unchanged otherwise; agent death loss reduces
`combat_xp` to `max(0, start - agent_xp_death_loss)`.

**Validates: Requirements 1.5, 1.6, 5.7, 6.1, 6.2**

### Property 4: Per-entity independence and owner-agnostic derivation

*For any* two distinct entities, mutating one entity's `combat_xp` leaves the
other entity's `combat_xp`, `level`, and `rank_level` unchanged; and for *any*
fixed `combat_xp`, `get_raw_level()` is identical regardless of whether the
entity has an owner, a different owner, or no owner.

**Validates: Requirements 2.1, 2.2, 2.3, 14.9**

### Property 5: Effective-level formula

*For any* agent `Raw_Level` in `1..MAX_LEVEL` and *any* owner `Entity_Level` in
`1..MAX_LEVEL`, `compute_effective_level` returns
`max(1, min(Raw_Level, owner_level - 1))`; the result is always `>= 1`; it is
strictly less than `owner_level` whenever `owner_level > 1`; and it equals `1`
when `owner_level == 1`.

**Validates: Requirements 14.1, 14.2, 14.3, 14.10**

### Property 6: XP award frozen at the cap ceiling

*For any* agent whose `Entity_Level` has reached its `Cap_Ceiling`
(`agent.db.level >= max(1, owner_level - 1)`) and *any* XP `source`, calling
`award_agent_xp(agent, source)` leaves the agent's `combat_xp`, `level`, and
`rank_level` unchanged (the award is skipped; no surplus accumulates).

**Validates: Requirements 5.9, 14.4**

### Property 7: XP award resumes when the ceiling rises

*For any* agent frozen at its `Cap_Ceiling`, after the owner's `Entity_Level`
increases so that the agent's `Cap_Ceiling` is strictly greater than the agent's
current `Entity_Level`, the next `award_agent_xp(agent, source)` with a positive
configured amount strictly increases the agent's `combat_xp` (earning resumes up
to the new ceiling, with no banked surplus realized from the frozen period).

**Validates: Requirements 5.10, 14.8**

### Property 8: Effective-level clamp on owner demotion never strips XP

*For any* agent and *any* decrease in the owner's `Entity_Level`, after
re-evaluation the agent's `Effective_Level` equals
`max(1, min(Raw_Level, new_owner_level - 1))` (clamped down where the stored
level exceeds the new ceiling), while the agent's `combat_xp`, `level`, and
`rank_level` are left unchanged by the demotion.

**Validates: Requirements 10.1, 14.1, 14.7, 15.1**

### Property 9: Gate attachment matches effective level AND enabled state on role apply

*For any* harvester agent, after the harvester role is applied,
`DeliveryBehavior` is attached if and only if **both** the agent's
`Effective_Level` meets or exceeds the `delivery` gate's required level **and**
the `delivery` key is in the agent's enabled-ability set; `HarvesterScript` is
always attached regardless of the gate or enabled state.

**Validates: Requirements 8.1, 8.2, 8.3, 8.5, 8.6, 10.4, 12.5**

### Property 10: Gate evaluation convergence and idempotence (available AND enabled)

*For any* agent and *any* transition of its `Effective_Level` (driven by its own
XP change or by an owner level change) and *any* enabled-ability set, repeated
calls to `evaluate_gated_abilities` leave a gated ability's behavior script
attached if and only if `Effective_Level >= required_level` **AND** the ability
key is enabled: exactly one instance when both hold (no duplicates), none
otherwise; `HarvesterScript` is retained across a delivery detach; the owner is
notified that the ability is *available with how to enable it* exactly on the
transition into the available-but-not-enabled state (without attaching), that it
is *now active* exactly on an attach transition, and that it has *re-locked*
exactly on a level-drop detach transition.

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.8, 14.6, 15.2, 15.3, 15.4**

### Property 11: Progression survives reserve/stop and is cap/reserve-independent

*For any* agent, the reserve, stop, and unassign operations leave
`combat_xp`, `level`, `rank_level`, and the enabled-ability set unchanged; and
*for any* fixed `combat_xp`, owner level, and enabled-ability set, the agent's
`Effective_Level` and its per-ability availability/enabled status are identical
regardless of the agent's reserve or stopped status.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 12: Roster progression view consistency

*For any* agent, `get_agent_progression_view` reports an `effective_level` equal
to `compute_effective_level(agent)`, a `capped_by_commander` flag that is true
if and only if `Raw_Level > Effective_Level`, and an `ability_status` map that
assigns each gate key exactly `enabled` when the key is in the enabled set,
`available` when `Effective_Level >= required_level` but the key is not enabled,
and `locked` (with the gate's required level) otherwise.

**Validates: Requirements 11.1, 11.2, 11.3, 11.4, 14.5, 16.5**

### Property 13: Ability-gate schema validation

*For any* generated ability-gate entry, `validate_ability_gates` reports an
error if and only if the entry is invalid (missing `key`/`required_level`,
empty or non-string `key`, non-integer `required_level`, or `required_level`
outside `1..MAX_LEVEL`); and *for any* list containing a duplicated `key`, it
reports an error identifying that key.

**Validates: Requirements 7.3, 7.4, 7.5**

### Property 14: Legacy agent defaulting and first-mutation persistence

*For any* agent lacking progression attributes, `get_combat_xp()` returns `0`,
`get_raw_level()` returns `1`, and `get_enabled_abilities()` returns the empty
set; and after the first `award_xp`/`deduct_xp`, `combat_xp`, `level`, and
`rank_level` are all present and mutually consistent
(`level == level_for_xp(combat_xp)`).

**Validates: Requirements 12.2, 12.3, 12.4**

### Property 15: Gate extensibility and unresolved-key safety (generic across keys)

*For any* ability-gate registry containing additional valid gates whose keys map
to a registered behavior script, evaluation and the enable/disable/status
mechanisms operate purely as a function of `Effective_Level >= required_level`
and the enabled set, identically for every key with no `delivery`-specific
behavior and no progression-code change; and *for any* gate whose key has no
registered script, evaluation attaches nothing for that key, logs the unresolved
key, and leaves the agent otherwise unchanged.

**Validates: Requirements 13.1, 13.2, 13.4, 13.5**

### Property 16: Rank-event emission on boundary crossings

*For any* player XP transition, `RANK_PROMOTED` fires (with old rank, new rank,
and new agent cap) if and only if the derived rank increased, and
`RANK_DEMOTED` fires (with the same payload shape) if and only if the derived
rank decreased; no rank event fires when the rank is unchanged.

**Validates: Requirements 4.3, 4.4**

### Property 17: Ability enablement command behavior

*For any* agent and *any* gate, `enable_ability(player, agent_id, key)` records
the key in the enabled set and attaches the gate's behavior script (initializing
its state) if and only if the agent's `Effective_Level >= required_level`;
otherwise it rejects the request, informs the player of the required level, and
neither records the key nor attaches the script. *For any* agent,
`disable_ability(player, agent_id, key)` clears the key from the enabled set and
detaches that ability's behavior script while leaving `HarvesterScript` (and any
other attached scripts) in place.

**Validates: Requirements 16.2, 16.3, 16.4, 9.6**

### Property 18: Sticky enablement persists across forced detach and drives auto re-attach

*For any* agent with an ability enabled, when its `Effective_Level` falls below
the gate's required level the behavior script detaches but the enabled flag is
retained, and a subsequent rise back to or above the required level
auto-re-attaches the script with no additional player command; whereas after
`disable_ability` clears the flag, a rise to or above the required level does
**not** re-attach the script until the player enables it again.

**Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.5**

## Error Handling

- **Owner missing / `None` (Req 14, conservative cap).** When an agent has no
  `db.owner` or the owner has no resolvable `db.level`, `get_owner_level`
  returns `1`, so `compute_effective_level` yields `1`. This is the most
  conservative outcome (no gated ability unlocks for an orphaned agent) and
  never raises. Logged at debug.
- **Unresolved ability key (Req 13.4).** `resolve_ability_script` returns
  `None`; `evaluate_gated_abilities` skips attachment for that gate, logs
  `"Unresolved ability gate key: %s"` once per evaluation at warning level, and
  continues evaluating other gates.
- **Legacy agents lacking progression attrs (Req 12.2-12.4).**
  `CombatEntity.get_combat_xp` treats `None`/absent as `0`; `award_xp`/
  `deduct_xp` read `self.db.combat_xp or 0`, then `recompute_progression`
  persists all three attributes. No migration script required; attributes
  self-heal on first XP event. Reads before any event use the defaults.
- **Legacy agents lacking `enabled_abilities` (Req 12.4).**
  `AgentSystem.get_enabled_abilities` treats absent/`None` as the empty set, so
  legacy agents have no gated ability enabled and no behavior script attaches
  until the player explicitly enables one. The attribute is created and
  persisted on the first `enable_ability` call.
- **Frozen-award short-circuit (Req 5.9, 14.4).** `award_agent_xp` computes the
  cap ceiling before awarding and returns without calling `agent.award_xp` when
  the agent is at/above its ceiling; this is a pure no-op (no XP, no level
  recompute, no surplus), and never raises when the owner is missing because
  `get_owner_level`/`get_cap_ceiling` floor at 1.
- **Hot-reload of ability gates / ranks.** `DataRegistry.reload_all` validates
  into a temporary registry and atomically swaps `ability_gates` and `ranks`;
  on success it rebuilds `world.progression` thresholds. On validation failure
  the current data and threshold table are preserved (existing reload contract).
  Gate re-evaluation after reload happens on the next owner/agent XP event or
  tick; no agent is left with a stale script because `process_tick` re-converges.
- **Tick-loop resilience.** `AgentSystem.process_tick` wraps each agent's
  XP-award and `evaluate_gated_abilities` in a per-agent `try/except` that logs
  via `logger.exception` and continues, mirroring the existing per-script
  guard and `process_movement` resilience. A single bad agent never halts the
  tick. `EventBus.publish` already isolates subscriber exceptions, so a failing
  `on_owner_level_changed` cannot break `RankSystem`.
- **Uninitialized threshold table (tests).** If `world.progression` is not yet
  initialized, `level_for_xp` lazily builds from `DataRegistry.get_instance()`
  when available and otherwise returns `1`, so `CombatEntity` never raises in the
  Evennia-stub suite.
- **Direct XP edits / admin commands.** Admin `@level`/`@rank` set
  `db.level`/`db.combat_xp` directly; `RankSystem.check_promotion`/`_sync_level`
  publish `LEVEL_CHANGED`, and `process_tick`'s defensive re-evaluation ensures
  owned agents converge even when XP is set out-of-band.

## Testing Strategy

### Dual approach

- **Property tests (Hypothesis)** verify the universal properties P1–P18 above.
  They live in `mygame/tests/` (and `mygame/world/tests/`) in files prefixed
  `test_prop_`, matching the existing convention (e.g.
  `test_prop_agent_system.py`). Each property test:
  - runs a minimum of **100 iterations** (`@settings(max_examples=...)`; existing
    suites use 200 — match that where cheap),
  - carries a tag comment in the format
    **`Feature: agent-progression, Property {n}: {property text}`**, and
  - references the requirements it validates in the docstring.
- **Unit / example tests** cover specific behaviors not suited to PBT:
  Req 1.1 (structural — both typeclasses expose progression API), 4.2/4.5/4.7
  (player attr meanings, level-change message, tech unlock/revoke), 5.6/6.4
  (amounts sourced from balance), 7.1/7.2/7.6/7.7 (gate data loaded and the
  `delivery` entry equals `min((5-1)*5+1, MAX_LEVEL) == 21`), 8.3 (sub-threshold
  or unlocked-but-not-enabled harvester still produces a drop), 9.3 (dynamic
  attach initializes `delivery_state` to IDLE), 12.1 (new agent inits 0/1/1 and
  empty `enabled_abilities`), 13.3 (mapping extensibility), 16.6 (unknown
  ability key rejected), and 16.7 (unowned/missing agent rejected).
- **Integration tests** (1–3 examples) cover the wired flows: `LEVEL_CHANGED`
  publication → `on_owner_level_changed` → dynamic attach/detach and the
  available-but-not-enabled notification; the freeze-then-resume award path
  across an owner level-up; the `agent ability <id> <key> on|off` command
  round-trip (enable attaches, disable detaches keeping `HarvesterScript`); and
  the RankSystem promotion path still firing `RANK_PROMOTED` + reserve handling.

### Test infrastructure

Reuse the existing **Evennia-stub conftest pattern** (`mygame/conftest.py` plus
the per-file `_ensure_evennia_stubs()` bootstrap seen in
`test_prop_agent_system.py`) so the fast suite runs without a live server.
Progression math, the owner-cap function, gate evaluation against fake
NPC/owner objects, and schema validation all run under stubs. Where
`agent.scripts.add` is unavailable under stubs, gate-attach properties use a
fake `scripts` handler (list-backed with `.add`/`.all`/`delete` honoring
`key`) so attach/detach/idempotence are observable — consistent with the
existing `FakeNPC`/`FakeDB` helpers.

Property-based tests can fail by finding counterexamples; treat any Hypothesis
counterexample as a real defect to fix (or a property/spec to correct), not as
flakiness.

### Reconciling stale `test_agent_scripts.py` tests

Making harvester attachment gate-driven changes two existing assumptions, so the
following must be reconciled when implementing:

1. **`ROLE_SCRIPT_MAP["harvester"]` shape.** `test_harvester_maps_to_list`
   currently asserts `ROLE_SCRIPT_MAP["harvester"] == [HarvesterScript,
   DeliveryBehavior]`. Under the new design `harvester` maps to `HarvesterScript`
   only (delivery is gate-driven *and* enablement-driven, not role-driven).
   Update this test to assert `ROLE_SCRIPT_MAP["harvester"] is HarvesterScript`,
   and add coverage that `ABILITY_SCRIPT_MAP["delivery"] is DeliveryBehavior` and
   that attachment of `DeliveryBehavior` is driven by `evaluate_gated_abilities`
   only when the agent is both at/above the gate **and** has `delivery` in its
   enabled set.
2. **Extractor-inventory-vs-drops harvester tests.** The four
   `TestHarvesterScript` tests that assert into `tile.db.resource_inventory`
   (`test_produces_resources_into_extractor_inventory`, the level-scaling test,
   the energy test, `test_production_accumulates`) reflect the older
   "inventory" model; current `HarvesterScript` spawns `ResourceDrop`s via
   `ResourceSystem._spawn_resource_drop`. These are already partially stale and
   must be reconciled to assert on spawned drops (production-only behavior,
   Req 8.3) rather than `resource_inventory`, and to confirm that a
   sub-threshold harvester produces drops without any delivery state.
3. **Gate-driven harvester tests must set the enabled flag.** Any test that
   expects `DeliveryBehavior` to attach for a high-level harvester must now also
   add `delivery` to the agent's `db.enabled_abilities` (or go through
   `enable_ability`), because reaching the gate alone no longer attaches the
   script. Tests for the at/above-gate-but-not-enabled case must assert
   production-only (no delivery) and that the player was notified the ability is
   available.
4. **New `agent ability` command coverage.** Add unit tests for the new
   `sub_ability` router handler and the `AgentSystem.enable_ability` /
   `disable_ability` / `get_ability_status` backends: enable-at/above-gate
   attaches and records; enable-below-gate rejects with the required level and
   does not attach; disable detaches `DeliveryBehavior` while keeping
   `HarvesterScript`; status reports locked/available/enabled; unknown key and
   unowned agent are rejected (Req 16).

These reconciliations are tracked as explicit tasks in the implementation plan so
the gate-driven change does not silently leave red/again-green-but-wrong tests.

## Backward Compatibility & Migration

- **Legacy agents without `combat_xp`/`level`/`rank_level`.** Handled at read
  time by `CombatEntity.get_combat_xp` (defaults `0`) and `get_raw_level`
  (defaults level `1`); attributes are created and persisted on the first
  `award_xp`/`deduct_xp` (Req 12.2-12.4). No bulk migration is run.
- **Legacy agents without `enabled_abilities`.** Treated as an empty enabled set
  by `AgentSystem.get_enabled_abilities` (Req 12.4); no gated behavior attaches
  until the player explicitly enables it, matching the pre-feature
  production-only behavior. The attribute is created on first `enable_ability`.
- **No XP banking.** This feature deliberately does **not** bank surplus XP past
  the owner cap. Awards are frozen at the `Cap_Ceiling` and resume when the owner
  levels up; effort spent while frozen is not recovered. The `Effective_Level`
  clamp is retained only so an owner demotion never strips an agent's earned XP.
- **Legacy players (`rank_level` → `level`).** `RankSystem._get_level` keeps the
  existing rule: when `db.level` is absent but `db.rank_level` (1-12) is present,
  level is `(rank_level - 1) * LEVELS_PER_RANK + 1` (Req 4.6). `CombatCharacter`
  already migrates this in its `at_object_creation` defaults loop; that path is
  unchanged.
- **Preserved RankSystem behavior.** All player-visible behavior is retained:
  same `db.combat_xp`/`db.level`/`db.rank_level` semantics (Req 4.2),
  `RANK_PROMOTED`/`RANK_DEMOTED` with `new_agent_cap` (Req 4.3, 4.4),
  level-change messages (Req 4.5), and `_unlock_for_rank`/`_revoke_above_rank`
  tech behavior on rank change (Req 4.7). The new `LEVEL_CHANGED` event is
  additive and does not alter existing subscribers.
- **Harvester production-only fallback.** Harvesters attach `HarvesterScript`
  only and continue producing drops at the Extractor for manual collection
  whenever delivery is not active — that is, when the effective level is below
  the `delivery` gate (including legacy agents and agents capped by a low-level
  commander) **or** when the gate is met but the player has not enabled
  `delivery` for that agent (Req 8.1, 8.2, 8.3, 12.5) — exactly the pre-feature
  behavior. Delivery is purely additive on top, and only after an explicit
  player enable.
- **`ROLE_SCRIPT_MAP` change.** Reverting `harvester` to a single script is
  backward-compatible for all other roles (engineer/guard/scout/soldier/medic
  unchanged); only harvester delivery becomes gate-driven, and the
  `_attach_behavior_script` list-handling path is retained for any role that
  still maps to a list in the future.
