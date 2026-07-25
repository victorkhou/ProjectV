# Implementation Plan — Item Loot Economy

## Overview

An ARPG-style rolled-loot economy: every item instance rolls its stats, carries a
transparent Item Quality Score + rarity, hard content drops affixes, and a Blacksmith
lets players reroll / insert-modify / salvage. Built in **incrementally-shippable
phases** — each leaves the game working and tested. Cheapest, lowest-risk value ships
first (data-only); the roll engine (highest leverage) is the vertical slice; the
Blacksmith + economy loop is the payoff.

**Phases → PR mapping:**
- **Phase 0** (PR0): Data-only wins — no engine change. Ships value immediately.
- **Phase 1** (PR1): Roll engine + IQS core (vertical slice; loot rolls + display).
- **Phase 2** (PR2): Rarity + affixes (aggregating axes only — no new combat hooks).
- **Phase 3** (PR3): Enabling combat hooks — range resolution + poison DoT + build-cost consumer.
- **Phase 4** (PR4): Blacksmith building — inserts + reroll.
- **Phase 5** (PR5): Salvage economy (Blacksmith salvage) + Refinery + economy research.
- **Phase 6** (PR6): Remaining buildings (Sniper Nest, Watchtower, Field Hospital) + research + balance pass.

Each phase: full unit tests + a live Django boot smoke check + balance sim where
relevant. Balance numbers from design §9 are starting points to tune in-phase.

---

## Tasks

### Phase 0 — Data-only wins (no engine change)

- [x] 0.1 Add the shipped typed weapons to `production_map` so they're obtainable
  (R10.7): `incendiary_rifle`, `psi_blade`, `blast_launcher` → AR and/or LB catalogs
  in `data/definitions/items.yaml`. Verify each crafts end-to-end (gate 3
  `wrong_building` no longer fires). Test: craft each in the right building.
- [x] 0.2 **Reactive Plating** research (R11.3) — new tech, `effect_value:
  {damage_reduction: N}` (uses the EXISTING consumer at `combat_engine.py:2033`;
  clamped by `perm_bonus_cap_dr=6`). Data-only. Test: research → `tech_bonuses` →
  combat DR applied + capped.
- [x] 0.3 A few **accessory-style gear** pieces on aggregating axes (proof the
  "free" path works): e.g. a `back`/`accessory` item with `fire_resist`/`damage_bonus`
  in `stat_modifiers`. Test: `get_stat_total` picks them up; combat reflects them.

### Phase 1 — Roll engine + IQS (vertical slice)

- [x] 1.1 `ItemDef.roll_spec` field (defaulted None) + loader support in
  `data_registry` + schema validation of the `roll_spec` shape. Field-count test bump.
- [x] 1.2 `world/systems/loot_roller.py` — `roll_item(item_def, source_rarity_weight,
  crafted, rng)` returning rolled stat_modifiers (skew distribution, band clamp).
  Pure + RNG-injected + never-raise. Tests: determinism, clamp, skew shape, craft band.
- [x] 1.3 `GameItem` per-instance roll storage (`db.rolled_stats`, `db.iqs`,
  `db.rarity`) + `get_stat` prefers `rolled_stats`. Tests: rolled value read by
  combat's `_get_stat`/`get_stat_total`.
- [x] 1.4 `iqs.py` (or in loot_roller) — `compute_iqs(rolled, roll_spec)` weighted
  mean → 0–100. Tests: min→0, max→100, weighting.
- [x] 1.5 Wire the roller into the loot spawn paths (design §1.2):
  `base_elimination._spawn_gear_item` (base-tier rarity weight); the passive/agent
  production-drop spawner (`set_gear_drop_spawner` at the composition root — rolls
  in the lowest rarity bucket, no affixes); and `craft`/`_route_produced_item`
  (crafted=True). The PvP death drop does NOT roll — the R1.6 preservation
  contract: if the drop spawner creates a fresh `GameItem`, this wiring MUST copy
  the instance db across (`rolled_stats`, `affixes`, `rarity`, `iqs`, `inserts`).
  That copy is implementation work, not just a test. (Guard-kill gear drops are a
  NEW path — added in Phase 2, task 2.5.) Tests: each rolling path produces a
  rolled item; production drops are lowest-bucket/no-affix; PvP drop carries the
  exact prior rolls + inserts (no re-roll).
- [x] 1.6 Display: item name shows `[rarity? · IQS%]`; `inspect`/look shows per-stat
  `rolled (min–max)`. Tests: display strings; unrolled items show neutral.
- [x] 1.7 Author `roll_spec` for the core weapons/armor (assault_rifle, sniper_rifle,
  kevlar_vest, power_armor, etc.) per design §9 (base ≈ 60–70% of max). Live boot check.

### Phase 2 — Rarity + affixes (aggregating axes only)

- [x] 2.1 `data/definitions/affixes.yaml` registry + loader + load-time validation of
  affix `stat`/`proc` keys. Start with aggregating-axis affixes only (`damage_bonus`,
  `damage_reduction`, `<type>_resist`) — no `range`/`poison` yet (those need Phase 3).
- [x] 2.2 Rarity assignment: `RARITY_TABLE` per source bucket in balance; source
  weight from base tier; roll-floor clamp per rarity. Tests: statistical distribution
  shift by source; floor raises min roll.
- [x] 2.3 Affix draw (no-dup, budget by rarity) + magnitude roll + `GameItem.db.affixes`.
  Affixes on aggregating axes flow through `get_stat_total` with no combat change.
  Tests: budget per tier; no dupes; combat reflects affix DR/resist.
- [x] 2.4 IQS displayed-score = IQS_base + Σ affix.value; `recompute_iqs` single
  writer; name/inspect show affixes + color by rarity. Tests: score math; recompute.
- [x] 2.5 **NEW guard-kill gear-drop path** (R3.6, design §1.2): small-chance roll
  on guard elimination near `base_elimination.on_npc_eliminated`; new
  `guard_gear_drop_chance` balance tunable (+ schema allowlist + field-count bump);
  on success spawn via `spawn_gear_drop` using the lowest rarity source bucket
  (guard kill). Existing guard resource mini-drops stay unchanged alongside.
  Tests: chance gate honored (statistical); rolled item drawn from the lowest
  bucket; resource mini-drops still fire.

### Phase 3 — Enabling combat hooks

- [x] 3.1 **Range resolution** (R8): `combat_engine._resolve_weapon_range(attacker,
  weapon)` summing base(rolled)+tech(`weapon_range`)+tile(0 for now); replace the raw
  reads at `combat_engine.py:321`, `:456`, `targeting_system.py:70`. `max_weapon_range`
  cap. **Scope (decided, R8.1):** range is weapon-instance only (rolled + inserts on
  the weapon) + tech + tile — it never aggregates across equipped items. Tests: all
  three sites use it; cap; melee still 1; a `+range` affix on the weapon now works;
  an accessory carrying `range` in `stat_modifiers` has NO effect on combat range.
- [x] 3.2 **Poison DoT** (R9): `_apply_poison_dot` (mirror `_apply_fire_dot`) +
  dispatch branch in `_finalize_hit` + `tick_effects_on_entity` poison branch +
  `poison_dot_fraction`/`poison_dot_ticks` balance + schema allowlist. Tests: DoT
  ticks, kills route through `_handle_zero_hp`, `poison_resist`+baseline mitigate,
  regen counters a light DoT.
- [x] 3.3 **Build-cost tech consumer** (R11.1, §6.3): `get_build_cost`/`get_upgrade_cost`
  × `get_tech_bonus(owner,"build_cost_mult",1.0)`, floored (e.g. 0.6). Tests: tech
  reduces cost; floor; does not touch production_multiplier.
- [x] 3.4 Unlock the Phase-2 affix pool for `range` + `poison`-proc affixes now that
  the hooks exist. Tests: `of Reach` adds range in combat; `of the Viper` applies poison.

### Phase 4 — Blacksmith + inserts

- [x] 4.1 **Blacksmith (BS)** building def + `BLACKSMITH` capability so the bench is
  locatable. Tests: build; capability; CONSTRUCTION honors `requires_agent` +
  `requires_hq` (real BuildingDef construction requirements); bench USAGE gates on
  operational status (offline/mid-upgrade) — there is no active-HQ usage gate.
- [x] 4.2 `ItemDef.insert_effect` + `category:"insert"` + insert item defs
  (venom_coating, extended_barrel, incendiary_core, hollowpoint) + production_map.
- [x] 4.3 `insert <item> [weapon]` command + `EquipmetSystem.apply_insert`: mutate the
  equipped weapon instance (damage_type / range / stat), enforce slot limit
  (`1 + level//3`), record `db.inserts`, recompute IQS. Gate order mirrors craft:
  ownership → operational (offline/mid-upgrade) → rank → cost (no active-HQ gate).
  Tests: each insert type mutates + is read by combat; slot-limit refusal; persists
  on drop.
- [x] 4.4 `reroll <item>` command + `EquipmentSystem.reroll`: re-roll base stats (band,
  Blacksmith-level floor), charge Salvage+resources, re-stamp IQS. Tests: base-only
  reroll; floor rises with level; charges; affixes untouched.

### Phase 5 — Salvage economy

- [x] 5.1 `db.salvage` currency + accessors + display. Tests: add/spend/never-negative.
- [x] 5.2 `salvage <item>` **Blacksmith command** + `EquipmentSystem.salvage`: yield
  `round((base_salvage + iqs*salvage_per_iqs) * (1 + 0.125*(blacksmith_level - 1)))`
  (design §5/§4.4; L1 1.0× → L5 1.5× level multiplier), destroy item, credit Salvage
  (R7.1, R7.2). Tests: yield scales with IQS; yield monotonic non-decreasing in
  Blacksmith level; destroys; ownership.
- [x] 5.3 **Refinery (RF)** building — Nexium sink (`RESOURCE_CONVERTER` capability;
  MUST NOT output Nexium). Tests: convert rate/level; no Nexium output.
- [x] 5.4 Economy research: **Salvage Protocols** (cost mult consumer). Tests: reduces
  reroll/insert cost.
- [x] 5.5 Balance sim: Salvage in/out over a raid session ≈ neutral; reroll chase burns
  many mediocre drops (no inflation).

### Phase 6 — Positional/utility buildings + research + balance

- [x] 6.1 **Sniper Nest (SN)** + `RANGE_AURA` capability + `_tile_range_bonus` (reads
  the building on the attacker's tile, models `_building_on_tile`) feeding
  `_resolve_weapon_range`. +1..+3 by level, on-tile only (decided; adjacency is an
  explicit later extension, not shipped in this feature).
  Tests: range bonus only while on tile; level scaling; cap respected.
- [x] 6.2 **Watchtower (WT)** + `VISION_AURA` — level-scaled `sight_range` aura for
  owner (extend the fog sight read). Tests: vision aura; level scaling.
- [x] 6.3 **Field Hospital (FH)** + `HEAL_AURA` — HoT to owner on tile (new tick
  consumer, models HP-regen). Tests: heals on tile; not off tile; level scaling.
- [x] 6.4 Research: **Ballistics Optimization** (`weapon_range`), **Toxicology**
  (poison boost/unlock), **Efficient Construction** (build_cost_mult if not in P3),
  **Master Gunsmithing** (crafted IQS floor / affix chance). Tests: each consumer.
- [x] 6.5 Final balance pass: worst-case range stack ≤ cap; god-roll gear set vs new
  player still beatable (the ~2× principle); rarity cadence feels right; help text +
  integration tests; live boot.

---

## Notes

**Dependencies / ordering**

- Phase 0 is independent — ship anytime (it's the current uncommitted-safe quick win).
- Phase 1 is the keystone; Phases 2+ build on rolled instances + IQS.
- Phase 2 affixes are limited to aggregating axes UNTIL Phase 3 lands the range +
  poison hooks (2.1 explicitly excludes those; 3.4 unlocks them).
- The NEW guard-kill gear-drop path (2.5, R3.6) lands in Phase 2 because it needs
  the rarity source buckets from 2.2; the Phase-1 roller wiring (1.5) deliberately
  excludes it.
- Blacksmith (P4) needs the roll engine (P1) for reroll and P3 hooks for range/poison
  inserts; damage-type inserts for fire/psychic/blast work as soon as P4 lands (those
  types are already shipped).
- Salvage (P5) is a hard dependency of the *economy* being non-inflationary — do not
  ship rolled loot flood (P1–P2) to production for long without the P5 sink.
- Every phase updates the balance field-count test + schema allowlist as needed and
  runs the full suite + a live Django boot.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["0.1", "0.2", "1.1"] },
    { "id": 1, "tasks": ["0.3", "1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "1.7"] },
    { "id": 3, "tasks": ["1.5", "1.6"] },
    { "id": 4, "tasks": ["2.1", "2.2"] },
    { "id": 5, "tasks": ["2.3", "2.5"] },
    { "id": 6, "tasks": ["2.4", "3.1", "3.3"] },
    { "id": 7, "tasks": ["3.2"] },
    { "id": 8, "tasks": ["3.4", "4.1"] },
    { "id": 9, "tasks": ["4.2", "5.1"] },
    { "id": 10, "tasks": ["4.3"] },
    { "id": 11, "tasks": ["4.4"] },
    { "id": 12, "tasks": ["5.2"] },
    { "id": 13, "tasks": ["5.3", "5.4"] },
    { "id": 14, "tasks": ["5.5", "6.1"] },
    { "id": 15, "tasks": ["6.2", "6.4"] },
    { "id": 16, "tasks": ["6.3"] },
    { "id": 17, "tasks": ["6.5"] }
  ]
}
```
