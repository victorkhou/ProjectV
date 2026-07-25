# Requirements Document

## Introduction

The **Item Loot Economy** feature turns the flat item system into a **rolled-stat loot economy**: every
instance of an item (looted OR crafted) rolls its stats within per-stat bands, so
two Assault Rifles are rarely identical. A transparent **Item Quality Score (IQS,
0–100)** and a rarity tier are stamped on each instance and shown in its name and
details, so a player can rank a drop at a glance. Hard content (fortresses,
citadels) is the only source of god-rolls and **affixes** (bonus properties like
`+range`, `+fire_resist`, a poison proc), which creates a *search economy* — people
raid and trade to find higher-value items. A **Blacksmith** building lets players
manage that economy: apply **inserts** (permanent weapon modifications — add a
damage type, +range), **reroll** an item's stats, and **salvage** unwanted rolled
items into a currency that fuels the bench. New buildings (Sniper Nest,
Watchtower, Field Hospital, Refinery) and research round out the depth.

The design is grounded in the current engine (verified against source, 2026-07-24):

- **`EquipmentHandler.get_stat_total(stat)` sums a stat across ALL equipped items
  with no key allowlist** (`world/systems/equipment_handler.py:210`). So gear that
  contributes `damage_bonus`, `damage_reduction`, or any `<type>_resist` already
  aggregates into combat with **zero engine changes** — rolled affixes on those
  axes are free. `combat_engine._get_target_typed_resist` builds the resist key as
  `f"{damage_type}_resist"` (`combat_engine.py:1998`), so a *new* resist axis
  (e.g. `poison_resist`) also aggregates for free.
- **BUT weapon `range` is read from the weapon item alone** via
  `_get_stat(weapon_item, "range", 1)` (`combat_engine.py:321`, `:456`;
  `targeting_system.py:70`) — NOT `get_stat_total`. So a `+range` affix/insert/tech
  does not aggregate today and needs a small new range-resolution hook.
- **Research is a generic accumulator into `db.tech_bonuses`** (`tech_system.py:216`),
  but only six key families are actually *consumed*: `building_hp`, `damage` (capped
  at `perm_bonus_cap_damage=6`), `damage_reduction` (capped at `perm_bonus_cap_dr=6`),
  `sight_range`, `production_multiplier`, and the `terrain_affinity:{terrain_type}:{kind}`
  key family read by the `TerrainModifierSystem` (added by the terrain-strategy
  feature). Any other new tech key is silently ignored unless a consumer reads it.
  **There is no build-cost-reduction hook today.**
- **No socket/mod/rolled-stat/attachment system exists** (verified via full-repo
  search). A `GameItem`'s `stat_modifiers` are copied once from its `ItemDef` at
  spawn (`typeclasses/objects.py:641`) and are read-only thereafter. Rolling +
  per-instance modification is net-new — but the per-instance store (`GameItem.db`)
  and the read path (`get_stat`) already exist, so rolls live naturally on the item.
- **Loot already drops as unique `GameItem`s** via `spawn_gear_drop` from three
  existing paths — base elimination (`base_elimination._spawn_gear_item`),
  passive/agent production drops (the composition-root gear-drop spawner), and
  the PvP death drop — all of which are the injection points for the roller.
  Guard-kill gear drops are NOT an existing path: guards currently drop only
  resource mini-drops; this feature adds guard-kill gear drops as a new fourth
  path (R3).

The two binding principles from the combat-rebalance steering doc govern every
number here:
1. **Always a counter, both ways.**
2. **Never ~2× stronger without counterplay** — a skilled new player must still be
   able to beat a maxed one. Rolled gear is "loseable power" (dropped on death),
   which is the counter to its being uncapped; permanent (tech) bonuses stay under
   the +6 caps.

---

## Glossary

- **Roll** — the per-instance randomized value of a stat within its `[min, max]`
  band, stored on the `GameItem` (overriding the `ItemDef` base).
- **IQS (Item Quality Score)** — 0–100 legibility number summarizing how good an
  item's rolls are, weighted by each stat's value weight. Displayed to players.
- **Affix** — an optional bonus property beyond the item's base stats (`+range`,
  `+fire_resist`, poison proc). Affix count is gated by rarity.
- **Rarity** — Common / Uncommon / Rare / Epic / Legendary. Derives from roll
  quality + affix count; sets display color and the roll floor.
- **Insert** — a consumable that, at the Blacksmith, permanently modifies an
  equipped weapon (adds a damage type, adds +range). The "applied-modifier" model.
- **Salvage** — a fungible currency obtained by breaking down rolled items at the
  Blacksmith; scales with the salvaged item's IQS and the Blacksmith's building
  level. The economy sink + Blacksmith fuel.

---

## Requirements

### Requirement 1: Rolled item stats (per-instance)

**User Story:** As a player, I want two copies of the same item to differ, so that
finding a better copy is meaningful.

#### Acceptance Criteria

1. WHEN a `GameItem` is created from an `ItemDef` that declares a `roll_spec`, THEN
   each rollable stat SHALL be assigned a value drawn from its `[min, max]` band and
   written to the item's own `stat_modifiers` (per-instance), overriding the def base.
2. The roll distribution SHALL be **skewed toward the low end** (top rolls rare) via
   `rolled = min + (max − min) · U^k` (U uniform 0–1, `k` a tunable ≥1, default 2),
   so a near-max roll is genuinely scarce — that scarcity is the economy.
3. An `ItemDef` with no `roll_spec` SHALL produce a fixed item exactly as today
   (full backward compatibility; ammo, consumables, fuel remain unrolled).
4. Rolls SHALL apply to BOTH looted and crafted items (see R6 for the craft/loot
   band difference).
5. Rolling SHALL be deterministic under an injected RNG (for tests), never raise,
   and clamp any rolled value to its band.
6. WHEN a rolled item is dropped on PvP death, THE drop path SHALL preserve the
   dropped instance's existing rolls, affixes, rarity, IQS, and applied inserts —
   the per-item state carries with the instance and SHALL never be re-rolled by
   the drop.

### Requirement 2: Item Quality Score (IQS) + display

**User Story:** As a player, I want a single number telling me how good an item is,
so I can compare drops without doing math.

#### Acceptance Criteria

1. Each rolled item SHALL carry an `iqs` (0–100 integer) computed as
   `100 · Σ(wₛ · qₛ) / Σ wₛ` where `qₛ = (rolledₛ − minₛ)/(maxₛ − minₛ)` and `wₛ`
   is the stat's configured value weight.
2. Affixes SHALL contribute additional value to the displayed item score above the
   base-stat IQS (so a god-roll with strong affixes can read as a top-tier item),
   per a documented, formulaic rule (design §IQS).
3. The item's name/appearance SHALL show its rarity, IQS, and — on inspect — each
   rolled stat with its band and any affixes, e.g.
   `Assault Rifle [Rare · 73%] — Damage 27 (18–30), Range 6 (4–7) · +4 fire_resist`.
4. IQS SHALL be recomputed and re-stamped whenever the item's rolls or affixes
   change (reroll, insert applied).
5. A fixed (unrolled) item SHALL display no IQS/rarity (or a neutral marker), so the
   readout only appears where it is meaningful.

### Requirement 3: Affixes + rarity tiers

**User Story:** As a raider, I want rare drops to have special properties, so that
chasing high-end loot is worthwhile.

#### Acceptance Criteria

1. Rarity SHALL be one of Common / Uncommon / Rare / Epic / Legendary, each with a
   display color and a fixed **affix budget** (0 / 1 / 2 / 3 / 4).
2. Rarity SHALL be determined at spawn from a **source-weighted** roll: the drop
   source (guard kill < outpost < stronghold < fortress < citadel) shifts the
   rarity distribution upward — reusing the existing per-template `rare_pool`/tier
   scaling in `base_elimination` / `outpost_spawner`.
3. Higher rarity SHALL raise the **roll floor** (Epic/Legendary guarantee the base
   stats roll in the upper part of their band) AND grant the affix budget.
4. Affixes SHALL be drawn (no duplicates) from an item-category-appropriate **affix
   pool**; each affix has its own rolled magnitude band and value contribution.
5. Affixes on the **aggregating axes** (`damage_bonus`, `damage_reduction`,
   `<type>_resist`) SHALL work with no combat-engine change (they sum via
   `get_stat_total`). Affixes on **`range`** or that add a **damage type / DoT proc**
   require the hooks in R8/R9.
6. WHEN a guard NPC is eliminated, THE loot system SHALL roll a small gear-drop
   chance (a new balance tunable) and, on success, spawn a rolled item using the
   lowest rarity source bucket (guard kill). This is a NEW drop path — guards
   currently drop only resource mini-drops, and that resource mini-drop behavior
   SHALL remain unchanged alongside the new gear-drop roll.

### Requirement 4: Blacksmith building (upgrade + insert + reroll bench)

**User Story:** As a player, I want a workshop to improve my gear, so that loot I
find or craft can be tuned rather than just replaced.

#### Acceptance Criteria

1. A new **Blacksmith (BS)** building SHALL exist (equipment category, requires HQ,
   requires an assigned agent like other equipment buildings, level 1–5).
2. Standing in an owned, operational Blacksmith SHALL enable commands to:
   - **apply an insert** to the currently-equipped weapon (R5),
   - **reroll** a held/equipped rolled item's stats (consuming Salvage + resources),
   - **salvage** a rolled item into Salvage currency (R7).
3. Blacksmith **level** SHALL improve the bench: a higher reroll floor, more insert
   slots on a weapon, and a greater Salvage yield from salvaging the same item
   (exact curves in design §Blacksmith).
4. All Blacksmith actions SHALL respect ownership, building-operational status
   (offline-protection and mid-upgrade), rank gates, and resource/Salvage cost,
   mirroring the existing `craft` gate order (`equipment_system.craft`).
5. A reroll SHALL re-roll the item's base stats within its band (NOT its affixes by
   default) and re-stamp IQS; a documented higher-cost "reforge" MAY reroll affixes
   (design decides whether this ships in phase 1).

### Requirement 5: Inserts (applied weapon modifiers)

**User Story:** As a player, I want to permanently modify a weapon (add poison, more
range), so I can build toward a signature weapon.

#### Acceptance Criteria

1. An **insert** SHALL be a consumable item that, when applied at the Blacksmith to
   an equipped weapon, permanently mutates that weapon instance.
2. Insert effects SHALL include at least: **damage-type conversion** (physical →
   fire/psychic/blast/poison), **+range**, and a stat bump (e.g. +damage with a
   tradeoff). Damage-type conversion on existing types (fire/psychic/blast) reuses
   the shipped `damage_type` field; poison requires R9; +range requires R8.
3. A weapon SHALL have a bounded number of insert slots (default small, e.g. 1–2,
   possibly raised by Blacksmith level); applying beyond the limit SHALL be refused
   with a clear message.
4. Applied inserts SHALL persist on the weapon instance (`GameItem.db`), be reflected
   in its IQS/display, and be **stripped/kept per the death-loss rules** like any
   other property of the weapon (a modified weapon dropped on death carries its
   inserts).
5. Insert application SHALL be reversible only via a documented, costly path (or not
   at all) — decided in design; the default is irreversible to preserve value.

### Requirement 6: Craft vs loot tension

**User Story:** As a player, I want crafting to give a reliable floor and raiding to
give the jackpots, so both activities have a purpose.

#### Acceptance Criteria

1. Crafted items SHALL roll in a **tighter, lower band** than looted items and SHALL
   NOT roll affixes (Common/Uncommon only) — a reliable, affordable floor.
2. Looted items from hard content SHALL be the ONLY source of high rarity + affixes
   + god-rolls.
3. The IQS/rarity system SHALL make the craft-vs-loot difference visible (a crafted
   item reads as a modest fixed-ish score; a fortress drop can read Legendary).

### Requirement 7: Salvage economy (sink)

**User Story:** As a player, I want a use for the loot I don't want, so the flood of
low rolls still matters.

#### Acceptance Criteria

1. WHEN a player salvages a rolled item at the Blacksmith, THE Blacksmith SHALL
   break down the item into **Salvage** currency, yielding an amount that scales
   with BOTH the salvaged item's IQS AND the Blacksmith's building level (a
   high-IQS item salvages for more; a higher-level Blacksmith yields more from
   the same item).
2. WHEN the same item is salvaged at a higher-level Blacksmith, THE Salvage yield
   SHALL be greater than or equal to the yield at any lower-level Blacksmith
   (monotonic level scaling; the exact curve is defined in design §Blacksmith).
3. Salvage SHALL be a per-player counted currency (stored like a resource), spent on
   Blacksmith rerolls/inserts.
4. Salvaging SHALL destroy the source item and respect ownership/possession.
5. The Salvage yield curve and reroll/insert costs SHALL be tuned so the economy does
   NOT inflate (rolls flood in, Salvage is the drain) — design §Balance.

### Requirement 8: Weapon range bonus hook (enabling +range)

**User Story:** As a designer, I need +range to actually work in combat, so range
affixes/inserts/buildings/tech are possible (an enabling requirement).

#### Acceptance Criteria

1. Combat's effective weapon range SHALL be resolved through a single helper that
   adds, to the weapon instance's base `range` — INCLUDING its rolled stats and
   applied inserts — the sum of: (a) an owner tech bonus
   (`get_tech_bonus(owner,"weapon_range")`), and (b) a tile/building bonus (R10
   Sniper Nest). Range SHALL NOT aggregate across other equipped items (a `+range`
   roll on an accessory has no effect by design — range is the highest-risk stat
   and gets the narrowest surface). Melee stays forced to range 1.
2. This helper SHALL be used at BOTH combat range-validation sites
   (`combat_engine` queue + resolve) AND the targeting-system lock re-validation,
   so they never diverge.
3. Range bonuses SHALL be bounded (small, e.g. +1…+3 per source; design sets a soft
   ceiling) because range has no chip-floor equivalent and directly beats kiting.

### Requirement 9: Poison damage type + DoT (enabling)

**User Story:** As a designer, I need a poison damage type + DoT so venom inserts
and poison weapons are possible (an enabling requirement).

#### Acceptance Criteria

1. A `poison` damage type SHALL be supported: as a pure typed hit it already works
   (falls to the typed-resist branch, reads `poison_resist`, obeys the 50% chip
   floor). Poison SHALL additionally apply a **damage-over-time** effect modeled on
   the shipped fire burn (`_apply_fire_dot` → `db.active_effects` → `tick_effects_on_entity`).
2. Poison DoT magnitude/duration SHALL be new balance tunables
   (`poison_dot_fraction`, `poison_dot_ticks`), added to the schema-validator
   allowlist alongside `fire_burn_*`/`blast_shred_*`.
3. `poison_resist` gear/affixes SHALL mitigate it (aggregates for free via
   `get_stat_total`); `baseline_resist` applies as to all types.
4. Poison SHALL have a counter (regen/medkit out-heals a light DoT; `poison_resist`
   caps it), per the binding "always a counter" principle.

### Requirement 10: New buildings

**User Story:** As a player, I want new structures that create positional and
economic play, so bases are more than turret walls.

#### Acceptance Criteria

1. **Sniper Nest (SN)** — while its owner occupies the building's tile, grants a
   level-scaled weapon **+range** (L1 +1 … L5 +3) via the R8 hook. A NEW building
   capability (`range_aura` or similar) + the tile-occupant read. Positional, not
   permanent. Extending the bonus to adjacent tiles is an explicit possible later
   extension — not shipped in this feature.
2. **Watchtower (WT)** — level-scaled `sight_range` aura for the owner (uses the
   existing consumed `sight_range` key; small aura wiring).
3. **Field Hospital (FH)** — passive heal-over-time to the owner while on its tile
   (models the existing HP-regen path).
4. **Refinery (RF)** — the design-doc-blessed **Nexium sink**: converts surplus
   resources into Salvage/fuel; MUST NOT output Nexium (anti-loop).
5. Each new building's non-HP level scaling requires a capability constant + a
   scaling formula (there is no generic per-level stat engine); design specifies each.
6. **Data-only fix bundled here:** the typed weapons `incendiary_rifle`, `psi_blade`,
   `blast_launcher` are defined with `craft_cost` but absent from every
   `production_map`, so they are currently uncraftable — add them to AR/LB catalogs.

### Requirement 11: New research

**User Story:** As a player, I want research that meaningfully changes my economy and
combat within the balance caps.

#### Acceptance Criteria

1. **Efficient Construction** — reduces building resource cost by a %; requires a NEW
   consumer (`get_tech_bonus(owner,"build_cost_mult")`) in the build/upgrade cost
   path. MUST NOT reuse `production_multiplier` (which would stack multiplicatively).
2. **Salvage Protocols** — reduces reroll/insert (or craft) cost; ties to R7 economy.
3. **Reactive Plating** — `damage_reduction` toward the +6 cap (works today, data-only).
4. **Ballistics Optimization** — weapon `range` bonus via the R8 hook (needs R8).
5. **Toxicology** — unlocks/strengthens poison inserts (ties to R9).
6. **Master Gunsmithing** — rolled items the player crafts get a higher IQS floor /
   extra affix chance (ties to the roll engine).
7. Any new tech targeting an axis with no consumer SHALL either add the consumer or
   be dropped — no dead tech keys.

### Requirement 12: Backward compatibility & migration

**User Story:** As a developer, I want the new item fields to default safely, so
that existing items and saves keep working unchanged.

#### Acceptance Criteria

1. Existing items (no `roll_spec`, no `iqs`) SHALL continue to function; they read as
   fixed/neutral and are never retro-rolled.
2. Adding `roll_spec`/affix-pool/`iqs` fields SHALL default such that all current
   `ItemDef(...)` construction and `GameItem` attribute paths keep working
   (field-count tests updated deliberately).
3. The feature SHALL ship in phases where each phase leaves the game in a working,
   tested state (see tasks.md); the pure-data wins (R10.7, R11.3) ship first.

---

## Non-goals (this feature)

- No auction house / player-to-player trading UI (the "search economy" is served by
  drop scarcity + salvage; direct trading is a later feature).
- No crit system (explicitly cut in the combat-rebalance doc; do not add crits).
- No new equipment SLOTS (the 11-slot model is fixed; inserts modify the weapon
  in-place rather than adding slots).
- No retroactive rolling of already-owned items.
