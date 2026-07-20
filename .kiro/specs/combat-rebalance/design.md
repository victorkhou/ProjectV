# Combat & Defense Rebalance — Steering Doc

> **Status:** LIVING DESIGN DOC — Phase 0 SHIPPED + §6 death-loss SHIPPED; Phase 1
> PARTIAL (rank-gap done; anti-snowball caps NOT built); Phases 2–5 designed & ready.
> **Goal:** build a balanced strategy game that is fun to play and fun to fight.
> **Last updated:** 2026-07-20 (audit reconciliation — re-grounded every code-claim
> against the real code; corrected stale §1/§2a present-tense framing of already-
> shipped fixes; fixed slot-count, AGGREGATED_STATS, planet_access, and Forge-L11
> claims; locked Elysium = Aether; corrected the §5 agent online-gate).
> Sections 1–5 capture the evaluation + adversarial design passes; the SHIPPED and
> SETTLED DECISIONS blocks and the CONSOLIDATED ROADMAP are the authoritative
> current state — where an older §3–§5 detail conflicts, the top blocks win.

## The two binding principles (govern every change here)

1. **Always a counter, both ways.** Every attack type must have a defense, AND
   every defense must have an attack that answers it. No unbeatable build.
2. **Never ~2× without counterplay.** No upgrade / tech / rank / gear / alliance
   perk may make a player ~2× stronger or deal ~2× damage vs a new player
   without a defense against it. A skilled new player must be able to beat a
   maxed player. Progression = quality-of-life + access + *slight* combat
   bonuses only; **skill decides fights, not upgrades.**

Every proposal below was stress-tested by an adversarial verifier against these
two rules. Verdicts: **sound** (passes as written), **needs-adjustment** (idea is
sound; a number/wiring detail must change — the fix is recorded), **reject**.

---

## SHIPPED (this session)

| Commit | Fix | Phase |
|--------|-----|-------|
| `5d1522e` | Chip-damage floor — closes the flat-DR immunity wall | 0 |
| `e1117df` | Turret damage scales with level (wired dead `turret_level_bonus`) | 0 |
| `9aa4157` | Freely-craftable essentials are Terra-craftable (forward-dep bug) | 0 |
| `294ca7a` | Rank-gap PvP protection (anti-ganking, aggressor-aware) | 1 |
| `b173382` | Full-loss-on-death + Respawn Beacon recovery (§6) | — |

> **Phase 1 is PARTIAL:** the rank-gap penalty (`294ca7a`) shipped, but the
> anti-snowball caps (DR soft-cap + aggregate permanent-bonus cap, §2a) are **NOT
> built** — no clamp exists in `_get_attacker_bonus` / `_get_target_armor_reduction`.
> Those caps are scheduled to land with the Phase-3 damage-type accessors (see the
> Open-questions light-aggregate-cap note).

## SETTLED DECISIONS (user, 2026-07-20) — drive the remaining build

**Damage types** (§3a):
- Ship **Fire (burn DoT)**, **Psychic (physical-armor bypass)**, **Blast (armor
  shred)** — physical stays the current model. *Sound/crit is NOT shipped (no
  crit system exists).*
- **50% per-axis mitigation cap** (no global budget) — each resist type caps at
  50%, so no single axis exceeds 2× EHP; stacking across types is allowed.
- **Baseline resist for ALL players at spawn** — a small innate resist to each
  type so resists are a veteran *edge*, not a wall a newbie can't touch.

**Planet re-map** (§3.5):
- **Biomass sink = consumables** (medkits/stims/cleanse). Universal, constant
  demand → the strongest permanent Terra round-trip pull.
- **Pacing: spread the ladder gates evenly, Terra as home for L1–20.** Terra is
  the L1–20 home band; the other 5 ladder planets + Citadel spread evenly across
  the rest of the curve (revise the earlier Terra-L1/Forge-L11/... spacing so no
  28-level dead stretch; Citadel still the top battleground). *Recompute the full
  ladder to honor "Terra 1–20, everything else follows evenly."*
- **Travel friction = cost/cooldown** on inter-planet hops (returning to farm is
  deliberate, not spammy) — in addition to the shipped rank-gap protection.

**Final ladder + names (locked 2026-07-20):**

| Planet | Gate | Rank | Signature resource | Role |
|--------|------|------|--------------------|------|
| **Terra** | L1 (home band 1–20) | Recruit | Wood/Stone/Iron + **Biomass** (exclusive) | Start home |
| **Forge** | L21 | Sergeant | Energy, Circuits | Industrial |
| **Tundra** | L33 | Lieutenant | **Cryogen** | Frozen |
| **Inferno** | L46 | Major | **Magmite** (feeds FIRE gear) | Volcanic |
| **Elysium** | L58 | Colonel | **Aether** | **Endgame home** (major bases) |
| **Citadel** | L70 | Brigadier | **Nexium** | **Battleground** (raid) |
| Space | off-ladder hub | — | *(optional, excluded)* | Travel hub |

Roughly 12-level gaps between the five upper planets (12/13/12/12) plus the
L20→L21 home-band handoff; no dead stretch; honors Terra-home-1–20,
Citadel=Brigadier L70, Elysium below Citadel. **Biomass** = the permanent Terra
round-trip anchor (feeds consumables/medkits). Elysium's signature = **Aether**
(locked 2026-07-20).

---

## 6. Full-loss-on-death + Respawn building  *(✅ SHIPPED 2026-07-20)*

**Implemented.** On player defeat, `_handle_player_defeat` calls the injected
`EquipmentSystem.apply_death_loss`: strips ALL equipped gear + Supply_Bag +
carried resources; a same-planet **Respawn Beacon** (`RB`, `respawn_point`
capability, rank 2, cheap Terra basics, upgradable L1–5) recovers a
building-level fraction into `db.recovery_stash` (55%→95% via
`RESPAWN_RECOVERY_BY_LEVEL`; per-item probabilistic + floor(pct×resource)). No
beacon on the death planet = total loss. `collect`/`recover` command
(`collect_recovery`) pulls the stash back — supplies to the bag, gear to
inventory, resources up to carry weight (leftover stays). New `SPAWN_RESPAWN`
option (first/default) redeploys the player at their beacon. Base storage
(HQ/Vault) is untouched — death strips the character, not the base. Tests:
`TestDeathLoss` (8 unit) + a live-boot round-trip on real objects; combat help
"Death & Recovery" section added.

**Original design (locked, for reference):**

**Death now costs everything.** On player defeat, ALL equipped gear, Supply_Bag
items, and carried resources are lost. A **Respawn building** recovers a
building-level-scaled fraction of what you were carrying, deposited AT the
building for you to collect on respawn. This makes power genuinely
attainable-and-losable (the design's preferred form) and raises the stakes of
every fight without a permanent-progression penalty.

**Locked decisions:**
- **Total loss on death** — equipment (all slots) + Supply_Bag + all carried
  resources are stripped from the victim in `_handle_player_defeat`.
- **Recovery location = the Respawn building.** The recovered portion is
  deposited into the building's store; the player collects it when they respawn
  there. The lost portion is destroyed (no ground drop in v1).
- **No building = total loss.** The Respawn building IS the safety net; without
  one you lose 100%. Strong incentive to build one early.
- **Recovery scales with BUILDING level (not player level):** **L1 55% → L2 65%
  → L3 75% → L4 85% → L5 95%** (linear +10%/level). Upgrading the building is the
  recovery-upgrade path.
- **Recovery method = per-item probabilistic + % of resources:** each held item
  is recovered with probability = the level %; each resource stack recovers
  floor(pct × amount). Variance on gear, smooth on resources.
- **The building:** early + cheap + upgradable L1–5, **intended one per planet**
  (⚠ NOT yet enforced — no `MAX_*_PER_PLANET` cap for `respawn_point`;
  `_find_respawn_building` just returns the first same-planet beacon. Add a cap
  mirroring `MAX_SHIELD_GENERATORS_PER_PLANET` if the limit must bind), sets the
  player's respawn point on that planet (integrates with `spawn_resolver` —
  likely a new `SPAWN_RESPAWN_BUILDING` option or making it the HQ-tier default).
  New capability e.g. `respawn_point` / `item_recovery`.

**Open sub-questions (resolve at build time, not blocking):**
- Building name/abbreviation (e.g. "Cloning Bay" / "Med-Bay" conflict? use a new
  abbr like `RB`/`CB`). — pick when authoring buildings.yaml.
- Does recovery deposit interact with the building's storage cap? (probably a
  dedicated recovery-stash, not the shared storage pool.)
- Resource loss vs the existing Vault "protected while offline" rule — Vault
  resources are base storage, NOT carried; only CARRIED resources are lost. Keep
  Vault/HQ stored resources safe (death strips the character, not the base).

**Balance-principle fit:** pure loseable-power mechanic; no 2× concern (it
*removes* power on death, never grants it). Counterplay is symmetric (don't die /
kill them first). New-player safety: pair with a cheap early building; the
per-planet respawn + 55% floor at L1 keeps early death from being ruinous.

---

## 1. Evaluation of the current system

> **⚠ HISTORICAL SNAPSHOT (pre-Phase-0).** This section is the *original diagnosis*
> that motivated the Phase-0 fixes. The two 🔴 immunity-wall weaknesses and the
> turret "dead code" weakness have since been **FIXED** (chip floor `5d1522e`,
> turret scaling `e1117df` — see SHIPPED). The scorecard ratios below are the
> pre-fix numbers; the "FIXED by" annotations show the current bounded values.
> Kept for the rationale, not as a description of live behavior.

### How combat works today
Single damage formula, single choke point — `_calculate_damage`
([combat_engine.py:921-998](../../../mygame/world/systems/combat_engine.py)). The
**original** floor was a bare `max(0, …)`:

```
# ORIGINAL (pre-5d1522e): net_damage = max(0, weapon_dmg + attacker_bonus − target_DR)
# CURRENT (shipped):      dealt = max(ceil(raw * chip_fraction), raw − armor, 0)
```

Every source — melee, ranged, turret, guard, bomb — routes through
`_apply_damage`, which spends a target's **shield** before **HP**. All
progression bonuses are **flat-additive** (never multiplicative). HP is a flat
**100** for everyone; no level-based HP scaling. Tick = 1 second.

| Axis | Range | Notes |
|------|-------|-------|
| Weapon damage | 10 (knife) → 50 (sniper) | grenades/mines 40–90 flat AoE |
| Max attacker bonus | **+16 permanent** (tech +10, alliance +6) + up to ~13.5 from stacked powerups | flat, additive |
| Max target DR | **+38** (armor 26 + tech 5 + alliance 7) | flat, additive |
| Turret / guard | 15 / 10–15 | *(pre-fix: turret never scaled; FIXED `e1117df` → L1 15 … L5 27)* |

Timing: player `attack`/`shoot` resolve **instantly** (1s wall-clock cooldown);
turrets fire on the tick *after* combat resolution (a 1-tick dodge window);
locked shots hit at 0.9 accuracy after a 3-tick lock that breaks on movement;
directional shots 0.7. HP regen 0.5/s.

### Rank gates & progression timing (XP to reach gear tiers)
| Gate | Unlocks | XP | ≈ kills |
|------|---------|-----|--------|
| Corporal L11 | sniper_rifle, first tech | 1,038 | 10 |
| Sergeant L16 | plasma_rifle, improved_armor | 2,882 | 29 |
| Lieutenant L29 | advanced_weapons (+10 dmg) | 18,518 | 185 |
| Captain L37 | **power_armor (+15 DR)**, rapid_prod | 35,082 | **351** |

### Strengths
- **Offense is well-tempered.** Max permanent damage bonus is only +16 → top-tier
  raw-damage ratio ~2.6×, near the ceiling and answerable by armor. No
  multiplicative runaway.
- **Real skill layer independent of stats:** LOS/walls, cover inside buildings,
  the turret dodge window, lock-break-on-movement, kiting, in-combat move-lag.
- **Explosives are the equalizer:** land_mine (60) & frag (40) are *freely
  craftable* and their flat blast punches through even 38 DR.
- Consistent owner attribution; flat 100 HP avoids a second inflation axis.

### Weaknesses — the imbalance was entirely on the DEFENSE axis
- ✅ **FIXED (`5d1522e`) — the flat-DR immunity wall (was THE core violation).**
  *Pre-fix:* damage floored at **0** and DR is flat-additive, so any DR ≥ a
  weapon's raw damage = total invulnerability (assault_rifle 25 did **literally
  zero** to a 38-DR defender, who also regened 0.5 HP/s — unkillable *and*
  healing). *Now:* the chip floor guarantees ≥ `ceil(raw × 0.5)` always lands, so
  assault_rifle 25 deals **13** to that defender (EHP ≈ 7.7 hits, finite).
- ✅ **FIXED (same floor) — the mid-game immunity threshold.** *Pre-fix:* full
  modern armor set (16 DR) + improved_armor tech (5) + alliance combat_armor L2
  (5) = **26 DR** already zeroed the assault rifle. *Now:* the chip floor applies
  regardless of DR, so no armor total grants immunity.
- ✅ **FIXED (`e1117df`) — turret scaling.** *Pre-fix:* `turret_level_bonus` was
  dead code (`get_turret_damage` never called), so turrets dealt a flat 15
  forever. *Now:* `process_turrets` scales the synthetic weapon by level
  (L1 15 → L5 27). Guards (10–15) still don't scale by design.
- 🟠 **STILL OPEN — Buildings get 0 DR**, so high-amount mines breach any wall;
  short-fuse bombs are structurally undisarmable (fuse decrements before the
  disarm timer). (§3a's decision: building **SHIELD**, not armor, is the blast
  defense — this is the intended structure, not a bug to floor.)

### Principle scorecard *(pre-chip-floor; see "now" column for shipped values)*
| Comparison | Pre-fix ratio | Now (chip floor) | Verdict |
|------------|------|------|---------|
| Raw damage: maxed attacker vs newbie's best gun | 2.64× | 2.64× (unchanged; offense untouched) | tolerable (armor counters) |
| Effective HP: 38-DR target vs 0-DR, vs sniper 50 | **4.16×** | **2.0×** (25 vs 50 dmg/hit) | ✅ within 2× |
| Effective HP: 38-DR target vs newbie's assault_rifle 25 | **∞ (immune)** | **~1.9×** (13 vs 25 dmg/hit) | ✅ no longer immune |

**Bottom line (original):** the danger of a flat-additive model with a zero floor
is exactly what the user anticipated — armor stopped being an *advantage* and
became an on/off *invulnerability switch*. **This is now fixed** — the structural
fix (the chip floor) shipped in `5d1522e`; armor is once again a bounded advantage
(≤2× EHP), never immunity.

---

## 2. New technologies / upgrades / classes + anti-snowball (catalog)

25 entries, **0 rejected**, 4 fully sound. "needs-adjustment" = a wiring/number
fix noted, not a balance failure.

### 2a. Anti-snowball / balance-fixes (the keystone)
- **✅ SHIPPED (`5d1522e`) — Mitigation Cap + Chip Floor.** The `max(0, …)` in
  `_calculate_damage` ([combat_engine.py:978-989](../../../mygame/world/systems/combat_engine.py))
  is now `dealt = max(ceil(raw × chip_fraction), raw − armor, 0)` with a **50%**
  floor (tunable `chip_damage_min_fraction`, default 0.5; 0 = kill switch).
  Damage reduction can no longer grant total immunity — it caps at halving. Scales
  off the *attacker's* weapon, so it never buffs damage vs a low-DR newbie; only
  ever *weakens the strongest defender* (∞ EHP → finite). **This single change
  restored "skill can overcome progression"** — it was the highest-priority fix.
- **Diminishing-returns soft cap on stacked DR** (needs-adj, **NOT built**): each
  DR point past 12 worth only half, so armor curves instead of hitting the
  invulnerability breakpoint. *(Largely subsumed by the shipped chip floor; a
  soft-cap is now belt-and-suspenders, not load-bearing.)*
- **Cap aggregate permanent (tech+perk) flat bonuses** (needs-adj, **NOT built** —
  scheduled Phase 3): clamp non-gear flat contribution to a small ceiling on both
  axes. See the Open-questions light-aggregate-cap note.
- **PvP gear-drop on death + underdog bounty** (needs-adj): victim drops equipped
  gear on defeat, drop-chance *rises when the victim outranks the killer* →
  attainable-and-losable power, catch-up for underdogs.
- **Kill-XP decay vs down-rank victims** (needs-adj): less XP for killing far
  below your rank → removes newbie-farming as a snowball engine. (Pairs with the
  rank-gap attack penalty in §3.)

### 2b. New technologies (permanent → must be QoL or small/conditional)
- **✅ SOUND — Logistics Network** (Sergeant L16): +carry cap, pure logistics, zero
  combat effect.
- **Salvage Protocols** (needs-adj): −15% craft cost. Pure economy. *Wiring
  landmine flagged:* the reader MUST use `get_tech_bonus(..., default=1.0)` — a
  copied `default=0.0` would make all gear free.
- **Wide-Band Radar** (needs-adj): reveals armed bombs/mines (the real new
  capability; "reveal enemy players" is already done by fog). Needs a new
  FogOfWar consumer — `detection_radius` is otherwise a dead no-op.
- **Automated Repair Bay** (needs-adj): ×1.5 building repair speed; countered by
  DPS/AoE exceeding the repair rate + cutting resource supply.
- **Munitions Handling** (needs-adj): faster reload (DPS uptime, not per-hit dmg).
- **Armor-Piercing Rounds** (needs-adj, Lieutenant L29): CAPPED gun armor-pen (ignore
  up to ~8 flat DR) — the turtle-breaker tech. Countered by shields (pen doesn't
  bypass them) + Reactive Plating.
- **Reactive Plating** (needs-adj, Captain L37): reduces incoming armor-pen *only*
  (adds no base DR, so it doesn't deepen the immunity wall). Uses Nexium — ties
  into planet economy.

### 2c. New upgrades / gear / consumables (loseable — preferred power form)
- **✅ SOUND — Tungsten AP Rounds** (ammo): shot ignores 50% of target's flat DR
  before the floor. The loseable, newbie-accessible answer to armor turtles.
- **Composite Plating** (needs-adj): back-slot **percentage** mitigation (15%)
  applied *after* flat DR — can never grant total immunity (always leaves %
  through). The right shape for a defensive item.
- **Point-Defense Interceptor** (needs-adj): one-shot consumable, negates a fixed
  chunk of the next AoE. Countered by staggered/multiple explosives.
- **Smoke Grenade** (needs-adj): LOS-blocking cloud, breaks locks. Skill tool.
- **Grappling Hook** (needs-adj): 3-charge mobility, cross walls. Telegraphed
  arrival tile is the counter.
- **Incendiary Rounds** (needs-adj): small burn DoT resolved *after* DR, so it
  lands independent of the target's armor total (a chip-through-armor vector even
  vs a high-DR turtle). Countered by medkit/regen.
- **Holographic Decoy** (needs-adj, large): pulls turret/guard/lock aggro; lets a
  raider peel static defenses. AoE hits real+decoy alike.

### 2d. Classes — sidegrades (a strength paired with a weakness), never a power tier
> **⚠ PREREQUISITE — the class layer is cosmetic-only today.** `classes.yaml` +
> `ClassDef` carry key/name/description with **no `stat_modifiers`**, and
> `db.player_class` has **zero combat/equipment consumers** (it's a stored label
> only). So *every* ability below — including the ✅-"standout" Ranger lock-gated
> DR bypass — is **new engine work** (a class-mechanics system + hooks), not a
> data/stat tweak. "✅ SOUND" here means *balance-sound*, not *ready-to-wire*.
> Build the class-mechanics substrate first (Phase 5 dependency).

- **✅ SOUND (balance) — Ranger, Weak-Point Marksman:** fully-locked shot ignores
  ~12 flat DR — the **skill-gated** turtle-breaker (must hold a lock, stand
  still). Countered by closing distance / breaking LOS. *Standout — but its core
  lock-based armor-pen mechanic is entirely unimplemented.*
- **Vanguard, Line-Breaker** (needs-adj): +15 HP, faster in-combat move; −0.15
  accuracy, −1 range. Bruiser that must close. Countered by kiting.
- **Engineer, Fortifier** (needs-adj): faster/cheaper build & repair, sees farther;
  weak duelist (−5 dmg/hit). Countered by rushing it in person.
- **Commander, Field Officer** (needs-adj): +1 agent, small non-stacking aura
  (+2 dmg/+1 speed) that **collapses when the fragile Commander dies.**
- **Medic, Field Surgeon** (new, needs-adj): +50% medkit, heal adjacent allies,
  1.5× regen; −4 dmg. Beaten by alpha-strike/burst.
- **Saboteur, Infiltrator** (new, needs-adj): +20 explosive dmg **vs buildings
  only**, short fuses; fragile 85-HP open-field duelist. The anti-turtle-fortress
  answer. Countered by detection + guards.

---

## 3. Damage types, planet segregation, rank-gap penalty  *(COMPLETE — workflow wf_e891ff42-4bd)*

22 proposals, **0 rejected**, 1 fully sound. The verifiers found real, important
design constraints — the "needs-adjustment" verdicts here are substantive, not
cosmetic. **Key lessons that must shape implementation:**

### Damage-type system — the hard rules the verify pass established
- **Backbone is sound:** branch `_calculate_damage` on a weapon `damage_type`
  (default `physical`), refactor `_get_target_armor_reduction`
  ([combat_engine.py](../../../mygame/world/systems/combat_engine.py)) → a typed
  `_get_target_resist`. **Corrected mechanism:** `get_stat_total`
  ([equipment_handler.py:209](../../../mygame/world/systems/equipment_handler.py),
  NOT equipment_system) already sums **any** stat key it's passed across **all
  equipped gear** (the 9 armor-bearing body slots + weapon + accessory = 11
  slots, not "5 armor slots"), and the schema applies **no key allowlist** — so a
  typed resist works with **no `AGGREGATED_STATS` edit**. ⚠ `AGGREGATED_STATS`
  ([constants.py:110](../../../mygame/world/constants.py)) is **inert
  documentation** (only a property test reads it) — editing it does nothing
  functional. The actual work is: (a) a new `get_stat_total('fire_resist')`-style
  call inside the renamed resist method, and (b) the physical branch keeps today's
  flat model while other types read their OWN resist and nothing else. (Composite
  Plating's post-DR % step is likewise NEW engine code, not free aggregation.)
- 🔴 **% mitigation cap must be ≤ 50%, NOT 75%.** A 75% cap = 4× effective HP on
  that axis = a >2× progression violation. Cap each %-resist axis at 50% so no
  single axis exceeds 2× EHP.
- ⚠ **Multi-axis resist stacking (verifier concern; SETTLED lighter).** The
  verify pass warned that stat keys alone let one set carry fire+psychic+blast+DR
  = a capped full-spectrum turtle, and proposed a global resist budget / slot
  exclusivity. **User decision: 50% per-axis cap only, NO global budget** — so a
  turtle can partially cover several types, but every axis still lets ≥50%
  through, and the FIRE/PSYCHIC/BLAST types each bypass or ignore physical DR, so
  the chip-floored physical hit + a type the turtle under-covered always gets
  through. Baseline resist for all keeps the newbie side fair. (Revisit only if
  playtesting shows spread-coverage turtles are oppressive.)
- 🔴 **New players need baseline resist gear at spawn (ungated),** or resists
  become a veteran-only power axis and "progression is physical-only" is false.
- 🔴 **Type selection must be informed, not blind:** no command currently reveals
  an opponent's worn armor. Add loadout-scouting OR make the combat log report
  type effectiveness on hit — else "pick the type they're exposed to" is a guess.
- ⚠ **`sound`/crit type is non-functional** — there is NO combat crit system
  (only harvest_crit). Either drop sound, or make it a flat armor-piercing type
  with a flat `sound_resist` (mirrors blast). Do not reference crits.
- ✅ **Fire + burn DoT** (needs a small EffectSystem tick) and ✅ **Blast +
  armor-durability degradation** verified counter/2×-clean.
- ✅ **Blast shreds player ARMOR only; building SHIELD stays the blast defense**
  (resolves the "buildings get 0 DR" question — shields, not armor, answer blast
  on structures).
- **Universal minimum-damage floor** (the §2a chip floor) is called out again
  here as *the* actual fix for total immunity — do it regardless of damage types.

### Planet segregation
- ✅ Approach: **planets.yaml LEVEL gate = single source of truth.** Clarification
  from the audit: `ranks.yaml planet_access` is **already dead data** — it's
  loaded into `RankDef` and schema-validated, but has **no runtime consumer**. The
  only executed gate is `can_access_planet`
  ([rank_system.py:246](../../../mygame/world/systems/rank_system.py)), which reads
  solely the planets.yaml level `rank_requirement`. So this is "delete or
  regenerate stale data," not "resolve a live competing gate."
- `CmdTravel` enforcing `can_access_planet` is the missing wiring (zero callers today).
- ⚠ A hard cross-band **attack block** risks a 2× problem / grief vector; prefer
  **intra-band level-gap damage attenuation** (a defense that always leaves the
  newbie able to fight back) over a hard block.

### Rank-gap attack penalty
- ✅ **SOUND — Owner-attributed unit penalty with base-defense exemption:** the
  penalty keys off the OWNING PLAYER on both sides (not raw unit/NPC level), and
  a defender's OWN turrets/guards defending their base are exempt (so garrison
  still works against a lower-ranked raider who attacked them).
- Graduated, floored outgoing-damage penalty (never to zero — self-defense
  preserved) + XP/loot penalty on lopsided kills to kill the farm incentive.
- ⚠ First-striker/aggressor stamp must be alt-proof and pairwise.

---

### 3.5 Planet/resource/rank RE-MAP  *(FINALIZED 2026-07-20 — decisions locked, audit re-derived)*

**The final ladder** (single source of truth = planets.yaml LEVEL; ranks.yaml
`planet_access` is dead data → delete/regenerate). The **Rank** column names the
rank a player IS at the gate level per `RANK_BANDS`
([constants.py](../../../mygame/world/constants.py)) — it is a label, NOT a
"band-low" rule (only Terra/Inferno/Citadel happen to land on a band-low). This is
the even-spaced ladder from the SETTLED decisions (Terra home L1–20, Citadel L70):

| # | Planet | Gate | Rank @ gate | Resources (▲ = new this rung) | Role |
|---|--------|------|------|-------------------------------|------|
| 1 | **Terra** | **L1** (home band 1–20) | Recruit | Wood, Stone, Iron, ▲**Biomass** (exclusive) | Start home |
| 2 | **Forge** | **L21** | Sergeant | Iron, ▲**Energy**, ▲**Circuits** | Industrial |
| 3 | **Tundra** | **L33** | Lieutenant | Stone, Iron, ▲**Cryogen** | Frozen |
| 4 | **Inferno** | **L46** | Major | Iron, Energy, ▲**Magmite** (feeds FIRE gear §3a) | Volcanic |
| 5 | **Elysium** | **L58** | Colonel | Wood, Stone, Iron, Energy, Circuits, ▲**Aether** — **NOT Biomass** | **Endgame home** (major bases) |
| 6 | **Citadel** | **L70** | Brigadier | Iron, Energy, Circuits, ▲**Nexium** | **Battleground** (raid, not staged) |
| — | **Space** | off-ladder hub | — | *(optional, excluded from progression)* | Travel hub |

Gaps 20→21→33→46→58→70 = 1 / 12 / 13 / 12 / 12: **~12-level spacing between the
five upper planets** plus the L20→L21 home-band handoff — **no dead stretch** (the
old L29→L57 gap is gone). ✅ Honors Terra-home-1–20, Citadel=Brigadier L70, Elysium
below Citadel.

**New resource types required** (each needs a `RESOURCE_TYPES` entry in
constants.py + a `resource_weights` value in balance.yaml + a terrain-tile
re-type). Beyond today's 6 (Wood/Stone/Iron/Energy/Circuits/Nexium), add **4**
(Biomass, Cryogen, Magmite, Aether):
- **Biomass** (Terra) — permanently-exclusive; re-type a null Terra tile
  (Plains/Dirt — both exist and yield nothing today). **Sink = consumables**
  (medkits/stims/cleanse) → constant universal demand, the permanent Terra
  round-trip anchor. Graduation push-throttles MUST exempt it.
- **Cryogen** (Tundra L33) — re-type Hot_Spring/Frozen_Lake (both null today).
  Gates a cold sidegrade (slow-field / cryo ammo).
- **Magmite** (Inferno L46) — re-type Sulfur_Pit/Lava_Flow (both null today).
  Gates the FIRE damage-type gear (§3a). ⚠ inert until the Phase-3 fire gear ships.
- **Aether** (Elysium L58) — the endgame-home signature; gates end-tier
  **sidegrade utility** (QoL/lateral, never raw power). ⚠ **no existing tile to
  re-type** — its source terrain must be authored with the net-new Elysium planet
  (unlike the other 3, which re-type existing null tiles).

**Dependency audit — RE-DERIVED against the final ladder** (Energy/Circuits now
at Forge **L21**, Cryogen L33, Magmite L46, Nexium L70). Only the **Energy/
Circuits @ L21** boundary breaks anything; Cryogen/Magmite/Nexium gates already
sit above their planet. 8 broken elements + the LOCKED per-item fix:

| Element | Unlocks | Needs (avail L) | Fix (LOCKED) |
|---------|---------|-----------------|--------------|
| **plasma_rifle** | Sergeant L16 | Energy+Circuits (21) | **Gate → L21+** (futuristic = unlocks with Energy) |
| **plasma_grenade** | Sergeant L16 | Energy+Circuits (21) | **Gate → L21+** |
| **energy_cell** | none (L1) | Energy (21) | **Gate → L21+** (literally an energy item) |
| **combat_stim** | none (L1) | Energy+Circuits (21) | **Gate → L21+** |
| **Shield Generator (SG)** | L15 | Energy+Circuits (21) | **Re-cost to basics** (keep early defense) |
| **Radar (RD)** | L9 | Energy (21) | **Re-cost to basics** (keep early utility) |
| **Medbay (MB)** | L18 | Energy (21) | **Nudge gate → L21** (already near) |
| **Relay (RL)** | L15 | Energy (21) | **Nudge gate → L21** (already near) |

Already-clean (gate ≥ resource): jetpack/proximity_mine (Staff_Sgt L22),
power_armor (Captain L37), advanced_weapons tech (Lieutenant L29), and everything
Cryogen/Magmite/Nexium-costed. The Phase-0 shipped fix already handled the
`rank=none` **medkit/frag/land_mine** (re-costed Energy→Stone).

**Gate-conflict resolution:** planets.yaml `rank_requirement` (level) is the
single source of truth; regenerate ranks.yaml `planet_access` from it, or derive
access from the level gate via `can_access_planet`.

**Remaining open:** none — all names, gates, and per-item fixes are locked.

> **Original captured intent (pre-remap), retained for reference:**

### 3a. Damage types (the user's flagship idea — structural fix for the immunity wall)
> **SETTLED: 4 types ship (Physical/Fire/Psychic/Blast). Sound is CUT** — there
> is no combat crit system (only harvest_crit), so its "bypass on crit" mechanic
> has nothing to hook. Do not reference crits. The strikethrough bullet below is
> retained only to record why it was dropped.

Splitting damage into types with **type-specific resists** means no single armor
stat grants universal immunity — a slot/weight/cost budget forces every build to
specialize, always leaving a hole a skilled attacker can exploit (satisfies
principle 1: every defense has an attack that answers it).

- **Physical / regular** — reduced by normal armor DR (current model).
- **Fire** — applies a burn DoT to players; needs fire resist; partially ignores
  physical DR.
- ~~**Sound / sonic** — high crit/headshot chance; bypasses armor on crit;
  countered by ear protection.~~ **CUT (no crit system).** If ever revived, make
  it a flat armor-piercing type with a flat `sound_resist` (mirrors Blast), never
  crit-based.
- **Psychic** — bypasses physical armor entirely; only psychic-specific armor
  resists it (a pure physical turtle is fully exposed unless they also carry
  psychic gear).
- **Blast** (bombs) — *degrades/destroys* non-blast armor durability rather than
  just being reduced by it; countered only by blast plating. Makes explosives an
  armor-**shredding** vector, not just high-flat-damage.

### 3b. Planet segregation (limit seasoned-vs-new contact)
- `can_access_planet` exists ([rank_system.py:246](../../../mygame/world/systems/rank_system.py))
  but is **UNWIRED** — no travel command, zero callers.
- **Stale data to clean up (not a live conflict):** planets.yaml gates by LEVEL
  (the only executed gate); ranks.yaml `planet_access` is loaded but **read by
  nothing**. Delete or regenerate it from the level gate — it competes with
  nothing at runtime.
- Design a player-facing `travel`/`warp` command that enforces access; decide
  the segregation model (home-planet band, soft/hard block on returning to farm
  newbies) — but see §4/§5: return trips are *wanted*, just economic not PvP.

### 3c. Rank-gap attack penalty  *(✅ DONE — Phase 1)*
- **Shipped.** `_rank_gap_damage_mult` in `_calculate_damage`: when an attacking
  player outranks their player target by ≥ `rank_gap_penalty_threshold` (10)
  levels, outgoing damage scales down linearly from 1.0 to
  `rank_gap_min_damage_mult` (0.25) over `rank_gap_full_penalty_span` (30) — and
  kill XP scales to `rank_gap_xp_loot_mult` (0.25). **Never to zero** (min-1
  floor → always killable). Attributed via the OWNING player on both sides (so
  agents/turrets inherit their owner's rank); PvE / enemy-NPC / ownerless and
  friendly-fire are exempt; disabled at threshold 0.
- **Aggressor exemption:** an `db.aggressors = {player_id: expiry_tick}` map is
  stamped on the target of every PvP combat action (hit or miss). If the
  lower-ranked player struck first, the higher player's return fire is undamped.
- Tests: `TestRankGapPenalty` (7 unit) + a live-boot test proving the
  `db.aggressors` dict round-trips on a real Evennia object.

---

## 4. Planet graduation incentives  *(COMPLETE — workflow wf_06265cbd-55b)*

12 mechanics, **0 rejected**, all needs-adjustment (ideas sound; wiring/number
fixes noted). The data already has a **latent resource tier ladder**: Terra =
Wood/Stone/Iron (basic) → Forge/Space add Energy/Circuits (mid) → **Citadel is
the only Nexium source** (top; Nexium gates the best alliance perks).

### ⭐ The cross-cutting fix (applies to EVERY push/decay mechanic)
**Key the "outgrown" penalty off graduation-ELIGIBILITY (the *next* planet's
gate), not the current planet's own requirement.** Define
`world.utils.outgrown_factor(player)`: find the lowest planet whose
`rank_requirement` is strictly greater than the player's current planet; if the
player's level hasn't reached that next gate they are a legitimate resident →
factor **1.0**. Only a player who *could* graduate but is camping gets throttled.
This is the safeguard that prevents throttling genuine new players — the verify
pass demanded it on every push mechanic.

### PULL — make the next planet attractive
- **Signature Resource Ladder** (needs-adj): each planet is the sole source of
  one signature resource gating a **sidegrade** gear/tech family (fixes the flaw
  that Tundra L33 yields the *same* Wood/Stone/Iron as Terra L1). ⚠ Two
  corrections: (1) the new resource names (locked: **Biomass/Cryogen/Magmite/
  Aether**) MUST be added to `RESOURCE_TYPES` (constants.py) + `resource_weights`
  (balance.yaml) or schema validation rejects the whole registry; (2) "distinct
  properties" (shield-
  pierce/DoT/slow) are a **combat-engine feature**, not small — ship v1 using
  only stat_modifiers the engine already reads (damage/range/DR/move_speed), and
  treat novel properties as §3a damage-type work with paired defenses.
- **Ascending Yield Tiers** (needs-adj): higher planets give more yield/action
  (today yield/respawn are single global constants). Ship `yield_scale` only;
  `respawn_scale` is inert until node depletion is wired.
- **Planet-Tier NPC Bases & Rare-Gear Scaling** (needs-adj): tougher bases +
  higher `rare_gear_chance` up top; some rares drop *only* from Citadel-tier
  fortresses. Confined spatially so Terra keeps its gentle 0.03/0.08 rolls.
- **Nexium Economy Sink — Refinery/Converter** (needs-adj): Nexium's **only sink
  today is one-time alliance-perk activation** (alliance_perks.yaml costs it;
  `activate_perk` in alliance_system.py deducts it) — so it's not *inert*, it just
  lacks a **recurring** sink. Add one (convert bulk lower-tier → scarce higher-
  tier, or a Nexium-cost upgradable building). ⚠ Do NOT let it *output* Nexium
  (no compounding); make it a recurring sink, not a one-time build tax.

### PUSH — make camping the old planet unrewarding (all gated by the ⭐ fix above)
- **Outgrown-Planet XP Falloff** (needs-adj): XP for all actions scaled by the
  outgrown factor. Apply in EXACTLY ONE choke point (`RankSystem.award_xp`).
  1.0 for the whole current-tier band + 5 levels of grace.
- **Basic-Resource Yield Taper** (needs-adj): outgrown players harvest less
  Wood/Stone/Iron (upgrade costs scale, so they must move up for bulk). Fix:
  key off graduation eligibility, basic-resources only, outgrown planets only.
- **Down-Tier NPC-Base Loot Decay** (needs-adj): gear/rare/HQ-XP rolls decay for
  campers farming respawning low-planet outposts.
- **Rank-Gap Combat Attenuation** (needs-adj) — *this is also §3c*: high attacker
  vs much-lower target → damage AND kill XP/loot scale down (to ~25%) UNLESS the
  lower player initiated. ⚠ Compute the gap from the **owning player** on both
  sides (not the raw unit/building/NPC level) and scope to genuine PvP.

### RISK/REWARD + EXCLUSIVE CONTENT
- **Planet Danger Tiers** (needs-adj, large): wire the already-named-but-inert
  hazard terrain (Lava_Flow, Radiation_Zone, etc.) + per-planet NPC scaling, so
  higher planets are a place you go to *fight* for reward. ⚠ Only tag
  null-resource tiles as hazards so no planet's own harvest is throttled.
- **Planet-exclusive buildings & Nexium techs** (needs-adj): via the unused
  `required_terrain` hook — content you can only build/research where you've
  graduated. ⚠ Nexium techs must NOT reuse `production_multiplier` (stacks
  multiplicatively with Rapid Production → 2.25×); confine to QoL keys.
- **Cross-planet refinement sink** (needs-adj) — *see §5*: formalize that
  Forge/Space/Citadel produce NO Wood/Stone, so a graduated player's Terra
  Extractors become the feedstock line for the new base. ⚠ ship as (1) surfacing
  the existing import dependency + (2) a Refinery that does NOT output Nexium.
- **Veteran diminishing returns + round-trip friction** (needs-adj): the answer
  to one-way vs round-trip — gentle harvest taper for out-levelers + unify the
  planets.yaml/ranks.yaml gate conflict. Keyed to the next planet's gate.

---

## 5. Cross-planet economy via agents (the user's refinement — key architectural finding)

**Graduation is round-trip, not one-way.** A higher-ranked player still needs
Terra's basic resources (Wood/Stone/Iron) to build future bases, so they return —
but their lower-planet presence is **economic (resource gathering), not PvP
ganking.** Agents run this: harvest **and** defense on lower planets while the
owner operates up top. The rank-gap penalty (§3c) removes the *reason* to gank;
resource dependency preserves the *reason* to visit.

**Architectural finding (verified in code, corrected 2026-07-20):**
- Agents **already tick cross-planet, with NO planet gate.**
  `agent_system.process_tick` (typeclasses/scripts.py `agent_processing` step,
  registered unconditionally) is fed the FULL agent roster via `_get_all_agents`
  — **not** gated by owner *planet*. A harvester left on Terra keeps working while
  the owner plays on Elysium. → cross-*planet* is **already supported**.
- ⚠ **BUT there IS an owner-ONLINE gate** (the earlier "no gate anywhere" claim
  was wrong). `HarvesterScript.at_repeat`
  ([agent_scripts.py:156-161](../../../mygame/typeclasses/agent_scripts.py)) checks
  `owner.has_account` and **returns without producing while the owner is offline**
  ("resources would just accumulate unprotected and get cleaned on next disconnect
  anyway"). So the cross-planet economy works **while you're logged in on any
  planet, NOT while logged off.**
- ✅ **DECISION (2026-07-20): keep the online gate.** No passive/offline
  stockpiling — production tracks a live session. This is deliberate (avoids the
  unprotected-overnight-stockpile problem); revisit only if a passive economy is
  later wanted (would need an offline-accumulation + raid/protection design).
- **Only missing piece: `CmdTravel`** to place agents on lower planets (Phase 2).
  No new per-agent-tick mechanic required.

---

## CONSOLIDATED IMPLEMENTATION ROADMAP

Ordered by dependency (what unblocks what) and by
"fix-the-violation-before-adding-content." Each phase is independently shippable
and testable. Effort tags from the verified proposals.

### Phase 0 — Keystone bug fixes *(✅ SHIPPED)*
These restored the core principle (the combat model *was* broken; it no longer is)
and shipped before any new content.
- **[S] ✅ DONE — Mitigation Cap + Chip Floor** (§2a) — `max(0,…)` →
  `max(ceil(raw*fraction), raw−armor, 0)` at combat_engine.py `_calculate_damage`.
  Kills the flat-DR immunity wall (∞ EHP → finite ~2×). New tunable
  `chip_damage_min_fraction` (default 0.5; 0 = kill switch) in BalanceConfig +
  balance.yaml + schema validator. Tests: `TestChipFloor` (7 unit) + updated
  Property 2/12 + a live-boot test (real armor via the equipment handler).
  *The single highest-value change — the immunity wall is closed.*
- **[S] ✅ DONE — Wire the dead turret damage-scaling** — `turret_level_bonus`
  was declared but `ResourceSystem.get_turret_damage` was never called from the
  tick, so every turret dealt a flat 15 regardless of level. `process_turrets`
  now scales the synthetic weapon's damage via that (now-shared) formula: L1=15,
  L3=21, L5=27 (1.8×, under the 2× ceiling). Base defenses finally improve with
  investment. Test: `test_turret_damage_scales_with_level`.
- **[S] ✅ PARTIAL — Forward-dependency recipe fixes** (§3.5 audit) — the
  `rank=none` essentials **medkit, frag_grenade, land_mine** were re-costed to
  Terra basics (Energy → Stone, same magnitude), so a Recruit can craft them on
  the spawn planet. A regression guard
  (`test_freely_craftable_items_need_only_starter_planet_resources`) enforces
  the invariant going forward, deriving starter resources from the real terrain
  data. **Still pending (resolved by the Phase 2 re-map, not re-costed):**
  `energy_cell` (futuristic-classed) + `combat_stim` (modern, Medbay-produced) are
  Energy/Circuits-costed supplies — the Phase-2 re-map **gates them → L21+** (where
  Forge first makes Energy/Circuits reachable); re-costing an "energy cell" to wood
  would be nonsensical. The guard allowlists these two. *(Correction: the ladder
  puts Forge at **L21**, not the earlier-draft L11; and only `energy_cell` is
  futuristic — `combat_stim` is a modern consumable.)*

### Phase 1 — New-player protection *(PARTIAL; depends on Phase 0)*
- **[S] ✅ DONE — Rank-gap attack penalty** (§3c, `294ca7a`) — the owner-attributed
  version with base-defense exemption; floored (never zero → self-defense
  preserved).
- **[S] ✅ DONE (XP only) — Kill-XP decay vs down-rank victims** (§2a/§4) — kill XP
  scales by `rank_gap_xp_loot_mult`; removes newbie-farming as a snowball engine.
  ⚠ The **loot** half is NOT wired — the defeat handler transfers no loot to the
  killer at all (death-loss destroys stripped gear, no ground drop), so the "loot
  penalty" and the PvP gear-drop-on-death proposal (§2a) remain unshipped despite
  the `rank_gap_xp_loot_mult` name.
- **[S] ❌ NOT BUILT — Anti-snowball caps** (§2a) — DR soft-cap + aggregate
  permanent-bonus cap. No clamp exists in `_get_attacker_bonus` /
  `_get_target_armor_reduction`. Scheduled to land with the Phase-3 damage-type
  accessors (see Open-questions light-aggregate-cap). Largely belt-and-suspenders
  now that the chip floor is shipped.

### Phase 2 — Planet re-map + travel *(medium; the structural backbone)*
Do the re-map as ONE coordinated change (gates + resources + recipe audit move together).
- **[S] Resolve gate conflict** — planets.yaml level = source of truth; regen
  ranks.yaml `planet_access`.
- **[M] Apply the final ladder** (§3.5) — gates Terra 1–20 / Forge L21 / Tundra
  L33 / Inferno L46 / Elysium L58 / Citadel L70; **4 new resources**
  (Biomass/Cryogen/Magmite/**Aether**); ⚠ **Elysium is a net-new planet** (author
  its entry + z_level + spawn + 8 terrain tiles, incl. Aether's source tile);
  Tundra de-duplication, Space → off-ladder hub, Citadel → L51→L70.
- **[S] Apply the LOCKED forward-dep fixes** (§3.5 audit): gate plasma_rifle/
  plasma_grenade/energy_cell/combat_stim → L21+; re-cost Shield Generator + Radar
  to basics; nudge Medbay + Relay gates → L21.
- **[M] `CmdTravel`** enforcing `can_access_planet` + travel cost/cooldown
  friction — the missing wiring that makes graduation real. Space = the hub it
  routes through. ✅ cross-planet agents already tick (§5, verified) — no extra work.

### Phase 3 — Damage-type system *(medium–large; depends on Phase 0 floor)*
Ship incrementally, physical-first (default `physical` = zero-risk migration).
- **[M] Type-aware backbone** — branch `_calculate_damage`, typed
  `_get_target_resist` calling `get_stat_total('<type>_resist')` (any key sums
  across gear for free; **no `AGGREGATED_STATS` edit needed** — that tuple is
  inert docs). Fold the **aggregate permanent-bonus cap** in here (same accessors;
  the unbuilt Phase-1 anti-snowball item). **Enforce (SETTLED):** 50% per-axis cap
  (NO global budget), baseline resist for ALL at spawn, and a loadout-scouting
  readout / on-hit effectiveness so type choice is informed.
- **[M] Fire + burn DoT** (needs a small EffectSystem tick) — pairs w/ Magmite.
- **[S] Psychic** (physical-armor bypass) · **[L] Blast** (armor-durability;
  building SHIELD stays the blast defense). *Sound is NOT shipped (no crit system).*

### Phase 4 — Graduation economy *(medium; depends on Phase 2)*
- **[S] Signature resource sinks** — make each planet's new resource *pull* the
  player (esp. **Biomass** as the permanent Terra round-trip anchor; **Nexium**
  sink so it's not inert).
- **[M] Ascending yields + outgrown-planet diminishing returns** — ⚠ key the
  throttle off graduation-eligibility (next gate), and **exempt Biomass** or it
  breaks the round-trip.
- **[M/L] Planet-tier NPC bases, rare-gear scaling, danger tiers** — earn the
  higher-planet rewards.

### Phase 5 — Content depth *(as desired; mostly independent)*
Turtle-breakers (Tungsten AP rounds, Ranger Marksman, AP-rounds tech), the
sidegrade classes, loseable-gear catalog, QoL techs (Logistics Network), the
new attack vectors (EMP, smoke, grappling, decoy). ⚠ **Prerequisite for all
class abilities:** the class layer is cosmetic-only today (`ClassDef`/classes.yaml
carry no `stat_modifiers`; `db.player_class` is unread by combat) — build a
class-mechanics substrate first. The ✅ marks here mean *balance-verified*, not
*already-wired* (nothing in §2/§5 combat content is shipped yet).

### Cross-cutting invariants (apply in every phase)
1. **50% per-axis % mitigation, NO global budget** (SETTLED); baseline resist for
   all keeps the newbie side fair (no new immunity wall).
2. Rank-gap / attenuation penalties key off the **owning player**, exempt a
   defender's own base defenses.
3. Graduation push-throttles key off graduation-eligibility and **exempt Biomass**
   (the Terra round-trip anchor).
4. New players start self-sufficient (Terra-craftable essentials, baseline resist).
5. Every new attack ships with its named defense; every permanent bonus stays
   flat + small + under 2×.

## Open questions (for you)
**None — the spec is fully decided.** All resolved (2026-07-20):
- ✅ Planet + resource names: **Elysium** (home), **Biomass** (Terra), **Cryogen**
  (Tundra), **Magmite** (Inferno), **Aether** (Elysium — locked). Space = off-ladder hub.
- ✅ **Biomass sink = consumables** (medkits/stims).
- ✅ **Pacing:** ~12-level gaps between the five upper planets (12/13/12/12) plus
  the L20→L21 home-band handoff (Terra home 1–20 → Forge 21 → Tundra 33 → Inferno
  46 → Elysium 58 → Citadel 70); the old 28-level dead stretch is gone.
- ✅ **Agent economy: online-gated** (§5) — no offline/passive stockpiling.
- ✅ **Travel friction = cost/cooldown** (+ shipped rank-gap protection).
- ✅ **Forward-dep audit** re-derived + per-item fixes LOCKED (§3.5).
- ✅ **Cross-planet agents** already tick without an online player (§5 verified).
- ✅ **Resist stacking:** 50% per-axis cap, no global budget.
- ✅ **Permanent tech/perk bonuses:** LIGHT AGGREGATE CAP (clamp total non-gear
  flat bonus per axis, e.g. dmg ≤ 6 / DR ≤ 6) — a ~5-line safety rail in
  `_get_attacker_bonus` / `_get_target_resist`, not a conversion to loseable gear.
  Belt-and-suspenders behind the shipped chip floor + rank-gap penalty; build it
  alongside the damage-type backbone (Phase 3, same accessors).
