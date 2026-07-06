# Complexity Review & "How To Add/Modify" Guide

A holistic review of Project V's core subsystems — **commands, content
definitions (buildings/items/etc.), typeclasses (objects/players/NPCs/agents),
and world systems** — organized around one premise: **the number of places you
must edit to add one thing is the truest measure of a subsystem's complexity.**

Each section below doubles as a mini-README ("how to add X") whose *touchpoint
count* is the complexity meter. All `file:line` references are current.

> **Status:** This document reflects the codebase **after** the consolidation
> pass described in [Part 3](#part-3--consolidations-applied). Every 🔴/🟡
> finding from the original review has been addressed; the full suite (1450
> tests) passes. The "before → after" deltas are called out inline.

---

## Complexity scorecard (at a glance)

| To add one… | Files touched | Edit sites | Grade | Was |
|---|---|---|---|---|
| Player command | 2 | 3 | 🟢 B+ | B (boilerplate now shared) |
| Router subcommand | 1 | 2 | 🟢 A | A |
| Content definition (item/tech/…) | 1 | 1 | 🟢 A | A |
| **Building type (plain)** | 1 | 1 | 🟢 A | A |
| **Building type (with behavior)** | 1–2 | 1–2 | 🟢 A− | 🔴 D (was 8–12) |
| **Agent role/behavior** | 1–2 | 1–2 | 🟢 A− | 🔴 D (was 8–12) |
| A schema field on a definition | 3 | 3 | 🟡 C | C |
| A world system | 3 | 8 | 🟡 C | C |
| A tunable balance value | 1 | 1 | 🟢 A | A |

The three 🔴 workflows from the original review — *building with behavior*,
*agent role*, and (indirectly) the *AgentSystem god-module* — are gone. Behavior
is now data-declared (capability flags / role table) rather than keyed off
hardcoded abbreviation string literals scattered across the codebase.

---

# Part 1 — How to add / modify each component

## 1. Commands

### 1a. Add a standalone player command — **3 edit sites, 2 files** 🟢

Example: a `trade` command.

1. **Define the class** — [`commands/game_commands.py`](commands/game_commands.py)
   (append). Subclass `GameCommand`; set `key`, `aliases`, `help_category`, a
   docstring (this *is* the help text — see 1d), and `func()`.
2. **Import it** — [`commands/default_cmdsets.py:19`](commands/default_cmdsets.py#L19).
3. **Register it** — [`commands/default_cmdsets.py:57`](commands/default_cmdsets.py#L57)
   add `self.add(CmdTrade())`.

Plus a test. **Boilerplate is now shared** (was the 🟡 finding #5): the repeated
coord-lookup, building-lookup, and system-guard blocks are absorbed into three
`GameCommand` helpers ([`game_commands.py:199`](commands/game_commands.py#L199)):

- `self.require_system("building_system")` → the system or a messaged `None`.
- `self.require_coords()` → `(x, y)` ints or a messaged `None`.
- `self.buildings_here(x, y)` → buildings on the tile (modern + legacy paths).

A command's `func()` now reads as three guard-lines instead of ~15 lines of
copy-paste. `CmdBuild`, `CmdUpgrade`, `CmdDemolish`, `CmdHarvest`, `CmdAttack`,
`CmdResearch`, `CmdPowerup` all use them.

> ⚠️ **Prefix-matching gotcha (unchanged).** `GameCommand.match()`
> ([`game_commands.py:143`](commands/game_commands.py#L143)) matches any prefix
> of length ≥ 2, and `CMD_IGNORE_PREFIXES = "@&/+"` makes `open` collide with
> Evennia's builtin `@open` (hence the explicit `CmdOpen` removal in
> [`default_cmdsets.py:55`](commands/default_cmdsets.py#L55)). Check for prefix
> collisions when naming a command.

### 1b. Add a subcommand to an existing router — **2 edit sites, 1 file** 🟢

Add a `sub_<verb>` method + a `subcommands` dict entry in the router class (e.g.
[`agent_commands.py:362`](commands/agent_commands.py#L362)). The router
(`SubcommandDispatchMixin`, [`command_router.py:31`](commands/command_router.py#L31))
is already in the cmdset and supplies `require_system()`/`parse_int()` guards.

### 1c. Help text

Help comes from the command's `__doc__` automatically.
[`world/help_entries.py`](world/help_entries.py) holds only topic overviews, so a
new command needs no help edit unless you want a topic entry.

---

## 2. Content definitions (buildings, items, tech, powerups, ranks, terrain, planets)

### 2a. Add a *plain* definition — **1 edit site** 🟢

Add a YAML entry to the relevant file in
[`data/definitions/`](data/definitions/). The registry populator reads every
field via `entry.get(...)`, the dataclass in [`definitions.py`](world/definitions.py)
declares them, the schema validator auto-applies its rules, and cross-validation
checks foreign keys (terrain, resources, unlocks, **capabilities**) at load time
([`schema_validator.py`](world/schema_validator.py)). One-touch, validated.

### 2b. Add a building that needs *special behavior* — **1–2 edit sites** 🟢 *(was 🔴 D, 8–12 sites)*

Behavior is now **declared as capability flags** on the building, not keyed off
its abbreviation in scattered code. To make a building harvestable, storable,
upgradable, HQ-like, or a combat barrier, add the capability to its YAML entry:

```yaml
# data/definitions/buildings.yaml
- abbreviation: VT
  capabilities: [storage, primary_storage]
- abbreviation: EX
  capabilities: [harvestable, upgradable, requires_resource_terrain]
```

The capability vocabulary is defined once in
[`world/constants.py`](world/constants.py#L168) (`BUILDING_CAPABILITIES`), the
schema validator rejects unknown flags at load, and `BuildingDef.has_capability()`
([`definitions.py:34`](world/definitions.py#L34)) is the single query. Game code
branches on the capability:

| Behavior | Was (hardcoded) | Now |
|---|---|---|
| Harvester production | `building_type == "EX"` ×4 sites | `has_capability(HARVESTABLE)` |
| Upgradable | `category != "resource"` ×1 | `has_capability(UPGRADABLE)` |
| Extractor terrain rule | `abbreviation != "EX"` | `has_capability(REQUIRES_RESOURCE_TERRAIN)` |
| HQ (one per planet) | `abbreviation != "HQ"` | `has_capability(HEADQUARTERS)` |
| Storage delivery target | `bld_type in ("VT","HQ")` | `has_capability(STORAGE)` |
| Vault delivery preference | `bld_type == "VT"` | `has_capability(PRIMARY_STORAGE)` |
| Wall combat block | `btype == "WL"` | `has_capability(COMBAT_BARRIER)` |

For a *new* capability that needs new code, add the flag to
`BUILDING_CAPABILITIES` and one branch where the behavior lives — still far
fewer touchpoints than the old scatter. Live-object checks go through
`world.utils.building_has_capability()`
([`utils.py:146`](world/utils.py#L146)), which resolves the definition via the
`DataRegistry` singleton. This also **repurposed the previously-dead
`category` values** — behavior that used to be implicit is now explicit.

### 2c. Add a *field* to a definition — **3 edit sites** 🟡 *(unchanged)*

Dataclass field ([`definitions.py`](world/definitions.py)), validator rule
([`schema_validator.py`](world/schema_validator.py)), populator `entry.get(...)`
([`data_registry.py`](world/data_registry.py)). The three still align by hand —
an acceptable cost given the validators are per-type and explicit.

### 2d. Two-tier config boundary (structural vs. balance) 🟢

[`world/constants.py`](world/constants.py) = structural values that alter
validation/logic; [`data/config/balance.yaml`](data/config/balance.yaml) via
`BalanceConfig` = hot-tunable numbers. The prior leak is fixed:
`MAX_BUILDING_LEVEL` now lives in `constants.py`
([`constants.py`](world/constants.py)) and the schema validator enforces it as
the ceiling on a definition's `max_level`. `building_system.py` re-exports it for
compatibility.

### 2e. Typo-tolerant lookups 🟢 *(was 🟡 #12)*

`resolve_building` / `resolve_item` / `resolve_technology` / `resolve_powerup`
([`data_registry.py:423`](world/data_registry.py#L423)) all share one generic
`_resolve` (key **or** name, case/underscore-insensitive, `None` on miss), so
player-facing commands accept either the abbreviation or the human name for any
of these types.

---

## 3. Typeclasses (objects, players, NPCs, agents)

### 3a. Inheritance map (unchanged — the clean part)

```
DefaultObject (Evennia) ─ GameEntity ─┬─ GameItem / Building / ResourceDrop
                                       └─ NPC (CombatEntity + GameEntity)
DefaultCharacter (Evennia) ─ CombatCharacter (CombatEntity + DefaultCharacter)  ← player
CombatEntity  (mixin) — HP, equipment, incapacitation, XP/progression (shared by NPC + player)
```

Shared progression lives once in `CombatEntity`
([`combat_entity.py:43`](typeclasses/combat_entity.py#L43)). The redundant
`combat_xp/level/rank_level` re-init in `NPC.at_object_creation` is removed
([`npcs.py:45`](typeclasses/npcs.py#L45)) — `at_combat_entity_init()` is now the
single init path.

### 3b. Add a new agent role/behavior — **1–2 edit sites** 🟢 *(was 🔴 D, 8–12 sites)*

Role metadata is now **one table**, co-located with the Script classes:
`AGENT_ROLES` / `AGENT_ABILITIES` in
[`agent_scripts.py:848`](typeclasses/agent_scripts.py#L848). Add a role with one
`RoleSpec` entry (its script, script key, required buildings, army flag) and, if
new, its Script class:

```python
AGENT_ROLES = {
  "harvester": RoleSpec("harvester", HarvesterScript, "harvester_script", buildings=("EX",)),
  ...
}
```

Everything the system needs is **derived** from that table —
`VALID_ROLES`, `BUILDING_ROLE_MAP`, `ARMY_ROLES`, `ROLE_SCRIPT_MAP`,
`ABILITY_SCRIPT_MAP`, and the detach key-set (`ALL_BEHAVIOR_SCRIPT_KEYS`) — in
[`agent_constants.py`](world/systems/agent_constants.py) and
[`agent_scripts.py`](typeclasses/agent_scripts.py). The old hand-synced
hardcoded key list in `_detach_behavior_script` is gone; it now iterates the
derived set ([`agent_behavior.py:79`](world/systems/agent_behavior.py#L79)), so
the drift bug is structurally impossible.

### 3c. Attribute sprawl (unchanged, acknowledged) 🟡

Players/agents still carry ~20–25 loosely-typed `db.*` attributes with no
schema. Left as-is: a schema layer is a larger project than this pass, and the
values are exercised by the test suite. The triple-init concern (3c in the
original) is resolved for the combat fields (§3a).

### 3d. `PlanetRoom` — UI extracted 🟢 *(was 🟡 #8)*

The 157-line `_format_building_interior` is moved to
[`world/ui_formatters.py`](world/ui_formatters.py) (`format_building_interior`).
[`rooms.py`](typeclasses/rooms.py) keeps a one-line backward-compat re-export and
is now a spatial container + a thin `return_appearance` that *delegates*
rendering. Both callers (room appearance, `look`-inside) import from the new
module.

---

## 4. World systems

### 4a. Uniform construction 🟢 *(was 🟡 #10)*

Every system now inherits `BaseSystem(registry, event_bus)`
([`base_system.py`](world/systems/base_system.py)) and calls
`super().__init__(...)`, so the shared contract is enforced in one place. Systems
that need extra collaborators (tick clock, factory, build range) still accept
them as kwargs on top. `MovementSystem` remains the deliberate exception
(pathfinding-only, no registry/bus).

### 4b. Add a new system — **8 edit sites, 3 files** 🟡 *(unchanged)*

Class (inherit `BaseSystem`) → instantiate/register in
[`game_init.py`](server/conf/game_init.py) → tick wiring → event subscriptions →
event constants → balance values → optional YAML → tests. Inherent to adding a
cross-cutting system; the `BaseSystem` contract makes step 1 obvious.

### 4c. Tick order is declared data 🟢 *(was 🟡 #7)*

The per-tick execution order + its rationale is a module-level constant,
`TICK_STEP_ORDER` ([`scripts.py`](typeclasses/scripts.py)), documenting *why*
each step sits where it does (e.g. "powerups expire after this tick's combat").
`_build_tick_steps` registers available steps by name and emits them in that
declared order — reordering means editing the constant, not moving code, and a
missing system's step is simply skipped.

### 4d. Shared level helper 🟢 *(was 🔴 #4)*

The ~12-line "read `db.level`, fall back to `rank_level`" function existed 4×
verbatim. It's now one `world.utils.get_player_level(entity, default=...)`
([`utils.py:163`](world/utils.py#L163)); `RankSystem`, `TechLabSystem`,
`PowerupSystem`, and `AgentSystem` all delegate to it (each keeping its own
default). The stricter shared version also fixed a latent negative-level bug in
the agent path.

### 4e. `AgentSystem` split into focused units 🟢 *(was 🔴 #3)*

The 1,758-line god-module is decomposed by concern, combined via inheritance so
the public API and every `self.` call-site are unchanged:

| Module | Lines | Responsibility |
|---|---|---|
| [`agent_system.py`](world/systems/agent_system.py) | 899 | Facade: create/train/assign/query/tick orchestration |
| [`agent_progression.py`](world/systems/agent_progression.py) | 700 | Owner-cap, effective level, gated abilities, XP/death, roster view |
| [`agent_behavior.py`](world/systems/agent_behavior.py) | 179 | Behavior/ability Script attach/detach/resolve |
| [`agent_constants.py`](world/systems/agent_constants.py) | 53 | Shared logger + role-derived lookups (leaf, breaks cycles) |

`class AgentSystem(AgentProgressionMixin, AgentBehaviorMixin, BaseSystem)` — same
MRO, zero behavior change, but each concern now lives in a file you can hold in
your head.

### 4f. Event bus vs. direct calls (unchanged, acknowledged) 🟡

`CombatEngine` still reaches into `game_systems` to call
`agent_system.award_agent_xp()` directly on an agent kill (a synchronous
cap-ceiling award that can't be deferred). Left as a documented, deliberate
coupling rather than forcing it through the event bus.

---

# Part 2 — Remaining findings (all 🟡 or lower)

| # | Finding | Severity | Note |
|---|---|---|---|
| 1 | Adding a *definition field* still touches 3 places (dataclass/validator/populator) | 🟡 Med | Per-type explicit validators; acceptable |
| 2 | Adding a *system* is ~8 touchpoints | 🟡 Med | Inherent; `BaseSystem` eases step 1 |
| 3 | Loosely-typed `db.*` attributes, no schema | 🟡 Med | Larger project than this pass |
| 4 | `CombatEngine → AgentSystem` direct call bypasses the bus | 🟢 Low | Deliberate, documented |
| 5 | Turret detection uses `"VV"` while YAML Turret is `"TU"`; equipment uses `("AA","AR")` vs. YAML `AR` | 🟢 Low | Pre-existing, isolated in named constants — **not** scattered; left untouched to avoid behavior drift |

Every 🔴 High and most 🟡 Medium findings from the original review are resolved.

---

# Part 3 — Consolidations applied

Everything below shipped in this pass; the suite (1450 tests) stayed green
throughout.

1. **Building capability flags** — `capabilities:` in `buildings.yaml`,
   `BUILDING_CAPABILITIES` vocabulary in `constants.py`, `has_capability()` on
   `BuildingDef`, `building_has_capability()` in `utils.py`, schema validation.
   Replaced ~11 scattered abbreviation checks across 5 files; repurposed the dead
   `category` values.
2. **Single `AGENT_ROLES` / `AGENT_ABILITIES` table** — `RoleSpec`/`AbilitySpec`
   in `agent_scripts.py`; all role lookups + the detach key-set derived from it.
   Eliminated the hand-synced hardcoded script-key list.
3. **`get_player_level` shared helper** — `world/utils.py`; 4 verbatim copies
   deleted.
4. **`GameCommand` helpers** — `require_system` / `require_coords` /
   `buildings_here`; ~15 copy-paste blocks collapsed.
5. **`AgentSystem` split** — progression + behavior mixins + constants leaf;
   1,758 → 899-line facade.
6. **`TICK_STEP_ORDER`** — tick order + dependency rationale as declared data.
7. **UI extraction** — `format_building_interior` → `world/ui_formatters.py`;
   `PlanetRoom` is a spatial container again.
8. **Tidy-ups** — `MAX_BUILDING_LEVEL` → `constants.py` (+ validator ceiling);
   redundant `NPC` re-init removed; `resolve_item/technology/powerup` added;
   `BaseSystem` contract for all 8 systems.

## What's intentionally left

- Definition-field 3-way edit, new-system touchpoints, `db.*` schema, and the
  `CombatEngine→AgentSystem` coupling (Part 2) — each is either inherent, low
  value, or a larger project than a consolidation pass warrants.
- `PROTECTED_BUILDING_TYPES` stays its own small, well-tested constant rather
  than folding into capabilities — it was never scattered.
