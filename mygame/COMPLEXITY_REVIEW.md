# Complexity Review & "How To Add/Modify" Guide

A holistic review of Project V's core subsystems — **commands, content
definitions (buildings/items/etc.), typeclasses (objects/players/NPCs/agents),
and world systems** — organized around one premise: **the number of places you
must edit to add one thing is the truest measure of a subsystem's complexity.**

Each section below doubles as a mini-README ("how to add X") whose *touchpoint
count* is the complexity meter. All `file:line` references are current.

> **Status:** Parts 1–5 reflect the codebase through **2026-07-10**. Since then
> several feature stacks landed — the **bomb system** (grenades/mines,
> breach-cover), the **player-lifecycle state machine** (spawning/lobby/linkdead,
> flagged off), **player classes**, and **admin index/prefix spawning** — plus a
> follow-up **consolidation pass** ([Part 6](#part-6--post-lifecycle-consolidation-pass-2026-07-14)).
> Parts 1–5 are preserved as-written for their "before → after" history; **read
> Part 6 for what changed after them** (including corrections to a few now-stale
> Part 5 claims). Test counts quoted inline in Parts 1–5 are point-in-time and
> no longer current — the suite is validated by CI, not by a number in this doc.

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
| **An equipment slot / item category** | 1 | 1–2 | 🟢 A | new — data‑only (constant + data) |
| **A resource weight** | 1 | 1 | 🟢 A | new — `balance.yaml` |
| **An item EFFECT type** | 3–4 | 4 | 🟡 C | new — tuple + validator + use/throw branch + presenter |
| **Restyle a player notification** | 1 | 1 | 🟢 A | 🟡 C (was inline in systems) |
| **A framework/DB swap for a port** | 1 | 1 | 🟢 A | 🔴 (was woven through systems) |

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

### 2f. Equipment / items touchpoints (from the equipment-items feature) 🟢/🟡

The equipment layer was designed so the *common* extensions stay data-only, and
the one genuinely cross-cutting extension is honestly flagged as not:

- **Add an equipment slot or item category — 🟢 data-only.** A slot is one entry
  in the `EQUIPMENT_SLOTS` tuple ([`world/constants.py`](world/constants.py)); a
  category is one entry in `ITEM_CATEGORIES` (plus `GEAR_CATEGORIES`/`SUPPLY_CATEGORIES`).
  The schema validator, the paperdoll, and `get_stat_total` all iterate the
  constant, so the new slot/category is picked up everywhere; individual items are
  then authored in [`items.yaml`](data/definitions/items.yaml). Constant + data,
  no logic edit.

- **Add a resource weight — 🟢 `balance.yaml`.** Per-resource carry weight is a
  hot-tunable entry in `BalanceConfig.resource_weights`
  ([`data/config/balance.yaml`](data/config/balance.yaml)); a resource absent from
  the map defaults to `DEFAULT_RESOURCE_WEIGHT`. One tunable value, no code.

- **Add an item EFFECT type — 🟡 *not* data-only.** Unlike a slot or category, a
  new `effect.type` (`heal`/`buff`/`aoe_damage` today) needs **four** edits: the
  `EFFECT_TYPES` tuple in [`constants.py`](world/constants.py), a validator rule for
  its effect payload in [`schema_validator.py`](world/schema_validator.py), a
  `use`/`throw` branch in [`equipment_system.py`](world/systems/equipment_system.py)
  that actually applies it, and (usually) a presenter kind for the outcome message.
  This is inherent: the three effect mechanics are genuinely different, so a
  handler-registry would only relocate the branch, not remove it. Flagged 🟡 rather
  than pretending it is data-only.

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

### 4f. Event bus vs. direct calls 🟢 *(was 🟡; resolved in Part 4)*

`CombatEngine` used to reach into the `game_systems` global to call
`agent_system.award_agent_xp()` directly on an agent kill. It now receives an
injected **XP‑awarder callable** (`combat_engine.set_agent_xp_awarder(lambda:
agent_system)`, wired in `game_init`), so the synchronous cap‑ceiling award still
happens inline (it can't be deferred through the bus) but the dependency is
inverted — `CombatEngine` no longer knows the service locator exists.

---

# Part 2 — Remaining findings (all 🟡 or lower)

| # | Finding | Severity | Note |
|---|---|---|---|
| 1 | Adding a *definition field* still touches 3 places (dataclass/validator/populator) | 🟡 Med | Per-type explicit validators; acceptable |
| 2 | Adding a *system* is ~8 touchpoints | 🟡 Med | Inherent; `BaseSystem` eases step 1 |
| 3 | Loosely-typed `db.*` attributes, no schema | 🟡 Med | Larger project than this pass |
| 4 | ~~`CombatEngine → AgentSystem` direct call bypasses the bus~~ | ✅ Resolved | Now an injected XP‑awarder callable (Part 4, §4f) |
| 5 | Turret detection uses `"VV"` while YAML Turret is `"TU"`; equipment uses `("AA","AR")` vs. YAML `AR` | 🟢 Low | Pre-existing, isolated in named constants — **not** scattered; left untouched to avoid behavior drift |

Every 🔴 High and most 🟡 Medium findings from the original review are resolved;
the framework‑coupling findings were closed by the DI pass ([Part 4](#part-4--dependency-inversion--clean-architecture-pass)).

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

- Definition-field 3-way edit, new-system touchpoints, and the `db.*` schema
  (Part 2) — each is either inherent, low value, or a larger project than a
  consolidation pass warrants.
- `PROTECTED_BUILDING_TYPES` stays its own small, well-tested constant rather
  than folding into capabilities — it was never scattered.

---

# Part 4 — Dependency-Inversion / Clean-Architecture pass

A follow-up pass took the codebase from "layered, but the systems still import
Evennia" to a **framework-free core** with the framework isolated behind ports.
The premise carries over: the truest measure of decoupling is *how much of the
core you must touch to swap the framework* — now **zero**, and enforced by a
test rather than a convention. The suite grew from 1450 → **1499 tests** across
this pass; it stayed green throughout.

### 4.1 The seam — ports / adapters / presenters

| Layer | Location | Rule |
|---|---|---|
| **Ports** | [`world/core/ports/`](world/core/ports/) | Abstract base classes, **stdlib only**. The contracts the core depends on. |
| **Adapters** | [`world/adapters/`](world/adapters/) | The **only** modules that import Evennia. Each implements a port. |
| **Presenters** | [`world/presenters/`](world/presenters/) | Turn domain events into player-facing text; deliver via a port. |
| **Composition root** | [`server/conf/game_init.py`](server/conf/game_init.py) | The one place that constructs adapters and injects them. |

Ports shipped: `Notifier`, `PlayerNotifier`, `DefinitionsProvider`,
`AgentRepository`/`AgentFactory`, `BuildingFactory`/`MovingEntityRepository`,
`TerrainProvider`. Each system takes its collaborator ports as constructor
kwargs with **lazy Evennia-backed defaults**, so production wiring is explicit at
the root while unit tests inject fakes and never touch a DB.

### 4.2 Presenter (Observer) for per-player messages 🟢

The ~11 per-player notification strings used to be inline f-strings scattered
across `rank_system`, `building_system`, `combat_engine`, `agent_system`,
`agent_progression`, and `resource_system`. They now flow through one seam:

```
system → BaseSystem.notify(player, kind, **data)
       → publish PLAYER_NOTIFICATION(player, kind, data)
       → NotificationPresenter looks up `kind` in its formatter table
       → delivers via the injected PlayerNotifier port
```

`world/systems/` now contains **zero** presentation strings. Restyling a message
is a one-line edit to
[`presenters/notification_presenter.py`](world/presenters/notification_presenter.py).
The string output was verified byte-for-byte identical to the old inline
versions (colour codes, em-dash, spacing, trailing punctuation).

### 4.3 Single `get_instance()` choke point 🟢

The old `DataRegistry.get_instance()` reaches (and `BalanceConfig.current`) are
funnelled through `default_definitions_provider()` / `default_balance()` in
[`adapters/registry_definitions_provider.py`](world/adapters/registry_definitions_provider.py).
Owner-agnostic helpers (`world.utils`, `chat_system`, `progression`) resolve a
hot-reload-safe `DefinitionsProvider` on demand instead of grabbing the
singleton directly.

### 4.4 The invariants are now tests, not conventions 🟢

- [`world/core/tests/test_layering_invariant.py`](world/core/tests/test_layering_invariant.py)
  — AST guard: core imports no `evennia`/`django`/`twisted`/`server.conf.game_init`;
  systems import no Evennia at module scope. This is the acceptance test for
  "framework swap = zero core changes."
- [`server/tests/test_game_init_names.py`](server/tests/test_game_init_names.py)
  — AST guard: every capitalized name *called* in `game_init` is imported/defined.
  Added after a real regression — the composition root called
  `EvenniaPlayerNotifier()` without importing it, which would `NameError` on boot
  and silently drop *all* player notifications. The live suite couldn't see it
  (it never runs `initialize_game()`); this guard now does.

### Part 4 scorecard

| Concern | Before | After |
|---|---|---|
| Evennia imports in `world/systems/` | many | **0** (enforced) |
| Presentation strings in `world/systems/` | ~11 scattered | **0** |
| `DataRegistry.get_instance()` reaches | scattered | 1 choke point |
| `CombatEngine → AgentSystem` global reach | direct | injected callable |
| "Framework swap touches core?" | yes | **no** (guard-tested) |

---

# Part 5 — Holistic review (2026-07-10): the wiring/reality gap

A Principal-level holistic pass (9 subsystem clusters, adversarially verified)
found that the *structural* consolidation claims in Parts 1–4 **hold** — but that
green-tests confidence is misplaced in one specific dimension: **the production
wiring path and the real Evennia object model are never exercised by an
integration test.** The result is a cluster of HIGH-severity bugs that are
invisible to the 2000+ test suite because the test fakes are, in the places that
matter, *higher-fidelity-than-real* (they raise `AttributeError` on a missing
`db.*` attribute; real Evennia does not).

## Holistic scorecard (1–10)

| Dimension | Score | One-line justification |
|---|---|---|
| Architectural alignment & extensibility | **7** | Ports/adapters seam, tick-order-as-data, capability flags, role table are genuinely good; but a domain system builds an adapter from the composition root (`building_system._legacy_terrain_provider`, `movement_system`) — an L4→L6 reach the guard can't see — and the "framework swap = zero core changes" claim is undercut by untested production wiring. |
| Code reuse & redundancy | **6** | Single-source role table and `get_player_level`/`building_has_capability` consolidations are real; but new/edge code re-introduced duplication: `_nearby_players` copy-pasted across two systems, three verbatim building-teardown blocks in `agent_system`, two parallel map renderers, `upgrade()` duplicating `start_upgrade`. |
| Performance & efficiency | **6** | Agent-index/building-index caching and the per-tick `active_hq_owner_ids` set are well-designed; but `AgentSystem.process_tick` and `DeliveryBehavior` still issue uncached global tag-scans in the 1s loop, `combat_timer` does a `search_script` DB query per hit, and the chunk perf-gate is itself broken (see below). |
| Readability & maintainability | **7** | Docstrings are thorough and CODING_STYLE-compliant; naming is clear; rationale comments are genuinely useful. Deductions: a few ~140-line multi-concern methods (`assign_agent`), and self-contradicting docs (the `at_repeat` docstring lists 10 tick steps; `TICK_STEP_ORDER` has 17). |
| Security & error handling | **5** | Ownership perm-locks and per-step/per-agent `try/except` isolation are solid; but several broad `except: pass` blocks mask *real* defects — most damningly `except (KeyError, AttributeError): pass` swallowing a call to a **non-existent** `registry.get_coord_space()`, and `is_player()` failing *open* on every real object. |

## Critical issues (must fix before production)

These are all CONFIRMED (independent trace + adversarial verifier). Full detail
in the top-level review; the load-bearing point is that **each one passes the
entire test suite.**

1. **`is_player()` fails open on every real Evennia object.**
   `world/utils.py:133` — `hasattr(entity.db, "combat_xp")`. Evennia's
   `DbHolder.__getattribute__` (`evennia/typeclasses/attributes.py:1453`) returns
   `None` for unset attributes and **never raises**, so `hasattr` is `True` for
   *any* object with a `.db` — buildings, items, drops. In `_finalize_hit`,
   `_is_player` is tested *before* `_is_building`, so a 0-HP building routes to
   `_handle_player_defeat` (respawn) instead of `_handle_building_destruction` →
   **buildings are never destroyed in production and `BUILDING_DESTROYED` never
   fires**, silently killing base-elimination. Tests pass because fake `db`
   objects raise on missing attrs (the E2E test added that on purpose). Fix:
   identify players by the `object_type`/character tag or an explicit
   `npc_type is None and has-account` check — never by `hasattr(db, "combat_xp")`.

2. **Tick clock frozen at 0 for three systems.** `server/conf/game_init.py:112–118`
   constructs `BuildingSystem`, `CombatEngine`, `PowerupSystem` **without**
   `current_tick_func` (only the spawner, L420, gets it); each defaults to
   `lambda: 0`. Powerups expire the tick after activation (expiry stamped against
   0, `process_tick` compares against the real tick); combat build-lockout math is
   frozen. Fix: inject `current_tick_func=_get_current_tick` into all three.

3. **Active-building list is empty whenever a player is online.**
   `typeclasses/scripts.py:330` reads `getattr(loc, "z")` for the planet and
   `world/chunking.py:118–119` reads `loc.x`/`loc.y` — but coordinates live on the
   *entity* (`db.coord_x/coord_y/coord_planet`), and `PlanetRoom` exposes none of
   `x/y/z`. So `_compute_active_data` returns `[]` in production →
   turret/production/combat tick steps get no buildings. Tests inject fakes with
   `.position`/`.x`/`.y`. Fix: resolve planet/coords from the entity's `db`, not
   the room object.

4. **Combat XP bypasses the progression pipeline.** `combat_engine._set_combat_xp`
   (L595) writes `db.combat_xp` directly; the engine never calls `award_xp()`/
   `recompute_progression()` nor routes through `RankSystem`, so no `LEVEL_CHANGED`
   fires. Kills grant XP that doesn't level you up, update the agent cap, unlock
   ranks, or notify — until an unrelated (harvest) award triggers a recompute.
   Fix: award combat/kill/base XP through `RankSystem.award_xp` like every other
   source.

5. **`registry.get_coord_space()` / `planet_def.coord_space` do not exist.**
   Referenced at `pathfinding.py:198` and `agent_system.py:826`; the surrounding
   `except (KeyError, AttributeError): pass` silently swallows the `AttributeError`
   and always falls back to 100×100 (pathfinding) / 256×256 (NPC passability,
   `npcs.py:233`). On any planet larger than those defaults, A*/passability use
   wrong bounds. Fix: add the real dimension lookup (or delete the dead branch and
   read dimensions from `PlanetRegistry`), and narrow the `except` so a missing
   method surfaces.

## Prioritized refactors (reuse / redundancy)

1. **Extract `_nearby_players` to `world/utils.py`** — currently copy-pasted
   verbatim in `combat_engine.py:491` and `guard_combat_system.py:347` (the
   docstring even claims it is "shared"). One home next to `get_coords`/`is_owner`.
2. **Extract building-assignment teardown + path-or-place** in `agent_system.py`
   (three verbatim copies at L272/L382/L570; place-block duplicated L331/L419).
3. **Collapse `upgrade()` into `start_upgrade()`** (`building_system.py:281`) — the
   instant path duplicates the timed path with divergent, staler state.
4. **Delete the dead room-based map renderer** (`procedural_map_renderer.py:403`)
   or make the live path reuse it — two ~90-line copies of the same priority logic
   already diverge (hardcoded `ag` glyph).
5. **Route the notification kind→formatter contract through a test** that asserts
   *every* kind emitted by *any* system has a formatter (currently only 2 of ~9
   emitting systems are scanned; a missing formatter silently drops the message —
   the doc itself flags this as "a real risk").
6. **Promote placement/grid magic numbers to `BalanceConfig`** — `_MIN_BASE_SEPARATION`,
   `_MAX_PLACEMENT_ATTEMPTS`, the 256 passability default — are structural tunables
   hardcoded against the stated `balance.yaml`-is-the-tuning-surface philosophy.

## Doc-claim audit (what Parts 1–4 got right vs. overstated)

- **Holds:** 0 module-scope Evennia imports in systems/core; presenter is the sole
  owner of player strings (0 `.msg(` literals in systems); `get_instance()` single
  choke point; `reload_all` validates-then-swaps; single-source role table;
  `get_player_level`/`building_has_capability` consolidations; `TICK_STEP_ORDER` as
  data; per-step tick isolation; all 8 systems inherit `BaseSystem`.
- **Partial / overstated:** "adapters constructed *only* at `game_init`" — two
  systems build a `TerrainProvider` from the composition root via a lazy import
  (documented as a "legacy fallback", but it *is* an L4→L6 reach the guard can't
  catch); "adding a definition field touches 3 *aligned* sites" — `ItemDef.classification`
  only touches 2 (no validator rule); "restyle a notification = 1 edit" is true, but
  the emit→formatter contract is not fully test-guarded.
- **Violated:** the ER-diagram note that soft resource refs are "never caught at
  load" is *correct as written*, but the same gap now silently swallows a call to a
  **non-existent registry method** (`get_coord_space`) — worse than a typo'd
  resource name because it's a dead code path, not just an unvalidated string.

## Fix status (two rounds)

**Round 1** fixed the 5 issues above. An adversarial re-review of the fix commit
then found the first pass was *incomplete* — the same overconfidence the review
warns about — and **round 2** closed the gaps:

- **A (fix #4 incomplete):** `base_elimination._award_xp` — the game's *largest*
  XP grant (`xp_hq_destroy`) — was still a raw `db.combat_xp` write and the
  handler had no RankSystem reference. The combat-engine fix missed its
  co-located sibling. Now routed through an injected RankSystem awarder.
- **B (fix #5 incomplete):** `npcs.py` `_is_tile_passable` still hardcoded
  256×256 (the *third* site, named in finding 5 but not patched), making every
  tile past 256 a movement dead-zone on larger planets. Now resolves via
  `PlanetRegistry.get_space()`.
- **C (fix #3 exposed a dormant bug):** `WorldChunkManager.get_active_chunks` /
  `get_buildings_in_chunks` ignore their `planet` argument. Harmless while the
  active-building list was empty; once fix #3 made it live, it caused
  cross-planet chunk activation and duplicate per-tick processing. Now filters
  by `db.coord_planet`.
- **D (fix #4 fragility):** the award/deduct fallback could double-apply XP if
  `RankSystem.award_xp` raised *after* mutating `combat_xp`. Now returns instead
  of falling through on a RankSystem error.
- **E (latent asymmetry):** an enemy NPC satisfies `is_player` (it carries
  `combat_xp`); only `_handle_player_defeat` guarded against it. The enemy-death,
  building-destruction, and base-elimination XP branches now guard too.

**Test-quality lessons (round 2):** the round-1 chunking test used a stub that
*reimplemented* the chunk logic, so it never ran the real `chunking.py` fix and
structurally couldn't catch bug C. Round-2 tests drive the **real**
`WorldChunkManager`, and add coverage for A (base-destroy XP routing) and B (NPC
passability past 256). Full suite after round 2: **2084 passing.**

---

# Part 6 — Post-lifecycle consolidation pass (2026-07-14)

Four feature stacks landed after Part 5 and were **not** covered by Parts 1–5:
the **bomb system** (grenades/mines, breach-cover blasts), the **player-lifecycle
state machine** (spawning → lobby → playing → linkdead, behind
`LOBBY_FLOW_ENABLED`, default off), **player classes** (selection + label), and
**admin index/prefix spawning** (`@item`/`@building`/`@outpost` by `#N` or
prefix). A Principal-level consolidation review (5 parallel reviewers, one
adversarial verifier per finding — 34 raised, 0 rejected) then ran over the
whole tree. This part records the review's confirmed findings and the **Tier-1
fixes applied**.

## New-subsystem touchpoints (scorecard additions)

| To add one… | Files | Sites | Grade | Note |
|---|---|---|---|---|
| **A player class** | 1 | 1 | 🟢 A | data-only: one entry in `data/definitions/classes.yaml`; `ClassDef` + optional-loader already generic. Selection/label only — no mechanics yet. |
| **A lifecycle state** | 2–3 | 3–4 | 🟡 C | value + label + transition edge in `constants.py`, plus a router/handler branch. Inherent to an FSM; the single-writer `player_lifecycle.transition` keeps writes in one place. |
| **A bomb type / variant** | 1 | 1 | 🟢 A | data-only: an item entry with `bomb_type` + fuse fields in `items.yaml`; `BombSystem` reads them. |
| **An admin spawn-by-index list** | 1 | 1–2 | 🟡 C→A− | each `@`-router still hand-rolls the index/prefix + numbered-list pattern (`admin_commands.py`); an `IndexedSpawnMixin` would make it data-only (open — see below). |

## Tier-1 fixes applied in this pass 🟢

1. **Deleted the dead `EquipmentSystem.throw` / `_resolve_throw_targets` /
   `_apply_aoe_damage` path** (+ its `set_area_damage_applier` injection at the
   composition root and its tests). It was a second, now-orphaned AoE
   implementation fully superseded by `BombSystem` (the live `throw` command
   routes to `bomb_system.throw_grenade`). This also retired the now-unemitted
   `bombed` notification kind + its `_fmt_bombed` formatter (dead-kind cleanup).
2. **Collapsed the linkdead-expiry stow block** in `scripts.py` into the
   existing `CombatCharacter.stow_from_world()` — the de-index-then-null-location
   logic was duplicated verbatim across `scripts.py` and `characters.py`.
3. **Deleted `admin_commands._get_registry`** — a byte-for-byte re-implementation
   of the already-imported `world.utils.get_system`; ~14 call sites now use
   `_get_system(caller, "registry")`.
4. **Removed the dead `_maybe_advance` guard** on `caller.ndb.spawn_choice`, an
   attribute nothing ever set (the gate is `db.pending_spawn_choice`).
5. **Deleted `ResourceDrop.at_pre_get`** — identical to the inherited
   `GameEntity.at_pre_get` (the `LiveBomb` always-refuse override stays).

## Corrections to now-stale Part 5 claims

- The **`_nearby_players` extraction is done**: `world.utils.nearby_players` is
  the single implementation; `CombatEngine`/`GuardCombatSystem` keep only thin
  static wrappers that delegate to it (a justified seam, not duplication).
- The **`at_repeat` / `TICK_STEP_ORDER` docstring drift is resolved**, and the
  count moved again — `TICK_STEP_ORDER` now has **20** entries (added
  `linkdead_expiry`). The `at_repeat` docstring correctly defers to the constant
  rather than re-listing a count, so it can't drift again.

## Tier-2 fixes applied in this pass 🟢

6. **Consolidated `CmdCloseExit`/`CmdOpenExit` onto `_resolve_exit_command`** —
   both now route through the shared helper (same as `CmdExit`/`exit`), removing
   ~40 lines of duplicated resolution + ownership + inside-building checks.
   **Bug fix**: added the `_base_active` gate to `_resolve_exit_command`, which
   `CmdExit` was missing — a latent bypass that let a player toggle exits via
   `exit <dir>` while their base was deactivated (only `closeexit`/`openexit`
   enforced the gate). Three regression tests pin this.
7. **Derived `schema_validator.py` balance field-lists from `BalanceConfig`** —
   `int_fields`/`float_fields`/`bool_fields` are now computed at module-import
   time via `dataclasses.fields(BalanceConfig)`. Adding a new `int`/`float`/`bool`
   field to the dataclass automatically validates it; no parallel list to maintain.
8. **Extracted `_resolve_storage_command`** for `CmdDeposit`/`CmdWithdraw` — the
   identical 5-step guard chain (get system → parse args → find storage building
   → ownership check → base-deactivation gate) now lives in one function; each
   command's `func` is reduced to a single delegation + one action call.

## Audit finds fixed alongside Tier-2 (2026-07-14) 🟢

An adversarial audit (5 parallel finders over the branch's feature slices, one
skeptic per finding) surfaced three more, all fixed:

9. **Hardened `DataRegistry._load_classes`** against a non-string `key`/
   `description` in `classes.yaml`. The docstring promised "malformed → skip,
   never block start," but only a *missing* key was caught; a non-string key hit
   `.title()`/dict-insert and a non-string description hit `.strip()` — both
   crashed **server start** (`AttributeError: 'int' object has no attribute
   'title'`). Now skips a bad key and coerces a bad description. Regression test
   added.
10. **Removed the dead `DEFAULT_THROW_RANGE` import** left in
    `equipment_system.py` after the Tier-1 `throw()` deletion (the constant is
    still live — only `bomb_system.py` uses it).
11. **Fixed the last stale `bombed`-kind reference** in an `apply_direct_hit`
    docstring (`combat_engine.py`) → "detonation summary".

## Open (deferred — verified real but risk > reward in this pass)

- **Two parallel map renderers** (`procedural_map_renderer.py` vs
  `map_data_provider.py`) still duplicate viewport/bounds/classification; and
  `_room_display_symbol` is fully dead. (Part 5's "two renderers" finding is
  still open.) Needs manual visual testing.
- **Combat kill-credit / defender-notification** logic repeats across the three
  defeat handlers and `_finalize_miss`/`_notify_target` → extract
  `_kill_credit` / `_notify_defender`. Deep interleaving across 6+ handlers;
  warrants its own adversarial pass.
- **Admin `IndexedSpawnMixin`**: after inspection only ~4 lines of resolution
  logic per command (each domain's index, list formatting, and spawn args differ
  enough that a mixin taking 3+ lambdas would add complexity without net gain).
  Not worthwhile.
- **`_fire_instant` helper**: 3 call sites × 7 lines, but pre-conditions diverge;
  the inline form reads clearly in context. Not worthwhile.
