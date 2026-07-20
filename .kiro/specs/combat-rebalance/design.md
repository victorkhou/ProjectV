# Combat & Defense Rebalance — Steering Doc

> **Status:** LIVING DESIGN DOC — for discussion & refinement, not yet implemented.
> **Goal:** build a balanced strategy game that is fun to play and fun to fight.
> **Last updated:** 2026-07-20.
> Nothing here is committed to code. This captures the evaluation and the design
> proposals generated across four adversarial design passes so we can reference
> and refine them.

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
| **Forge** | L21 | Staff_Sgt | Energy, Circuits | Industrial |
| **Tundra** | L33 | Lieutenant | **Cryogen** | Frozen |
| **Inferno** | L46 | Major | **Magmite** (feeds FIRE gear) | Volcanic |
| **Elysium** | L58 | Colonel | *(signature TBD)* | **Endgame home** (major bases) |
| **Citadel** | L70 | Brigadier | **Nexium** | **Battleground** (raid) |
| Space | off-ladder hub | — | *(optional, excluded)* | Travel hub |

Even ~12-level gaps, no dead stretch; honors Terra-home-1–20, Citadel=Brigadier
L70, Elysium below Citadel. **Biomass** = the permanent Terra round-trip anchor
(feeds consumables/medkits). ⚠ Elysium still needs a signature-resource name.

---

## 1. Evaluation of the current system

### How combat works today
Single damage formula, single choke point ([combat_engine.py:914](../../../mygame/world/systems/combat_engine.py)):

```
net_damage = max(0,  weapon_damage  +  attacker_bonus  −  target_damage_reduction)
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
| Turret / guard | 15 / 10–15 | turret never scales (dead code) |

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

### Weaknesses — the imbalance is entirely on the DEFENSE axis
- 🔴 **The flat-DR immunity wall (THE core violation).** Damage floors at **0**
  (not 1) and DR is flat-additive, so **any DR ≥ a weapon's raw damage = total
  invulnerability to that weapon.** A new player's best freely-craftable gun
  (assault_rifle 25) does **literally zero** damage to a 38-DR defender — no
  amount of skill changes it. Worse, that defender still regens 0.5 HP/s
  (unkillable *and* healing).
- 🔴 **Immunity threshold is mid-game, not endgame.** Kevlar (16) + improved_armor
  tech (5) + alliance combat_armor L2 (5) = **26 DR** already zeroes the assault
  rifle — no power armor needed.
- 🟠 **Base defenses rot.** Turrets (15) and guards (10–15) floored to 0–3 by just
  +12 DR, and `turret_level_bonus` is **dead code** (resource_system.py:320 never
  called) so turrets never improve.
- 🟠 **Buildings get 0 DR**, so high-amount mines breach any wall; short-fuse
  bombs are structurally undisarmable (fuse decrements before the disarm timer).

### Principle scorecard
| Comparison | Ratio | Verdict |
|------------|-------|---------|
| Raw damage: maxed attacker vs newbie's best gun | 2.64× | tolerable (armor counters) |
| Effective HP: 38-DR target vs 0-DR, vs sniper 50 | **4.16×** | ⚠ violation |
| Effective HP: 38-DR target vs newbie's assault_rifle 25 | **∞ (immune)** | 🔴 hard violation |

**Bottom line:** the danger of a flat-additive model with a zero floor is exactly
what the user anticipated — armor stops being an *advantage* and becomes an
on/off *invulnerability switch*. The fix is structural (below).

---

## 2. New technologies / upgrades / classes + anti-snowball (catalog)

25 entries, **0 rejected**, 4 fully sound. "needs-adjustment" = a wiring/number
fix noted, not a balance failure.

### 2a. Anti-snowball / balance-fixes (the keystone)
- **✅ SOUND — Mitigation Cap + Chip Floor.** Replace `max(0, …)` at
  combat_engine.py:915 with a floor equal to **50% of the attack's raw output**:
  `net = max((raw+1)//2, raw − eff_dr)`. Damage reduction can then never grant
  total immunity — it caps at halving. Scales off the *attacker's* weapon, so it
  never buffs damage vs a low-DR newbie; only ever *weakens the strongest
  defender* (∞ EHP → finite). **This single change restores "skill can overcome
  progression."** *This is the highest-priority fix.*
- **Diminishing-returns soft cap on stacked DR** (needs-adj): each DR point past
  12 worth only half, so armor curves instead of hitting the invulnerability
  breakpoint.
- **Cap aggregate permanent (tech+perk) flat bonuses** (needs-adj): clamp
  non-gear flat contribution to a small ceiling on both axes.
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
  lands even through the immunity wall. Countered by medkit/regen.
- **Holographic Decoy** (needs-adj, large): pulls turret/guard/lock aggro; lets a
  raider peel static defenses. AoE hits real+decoy alike.

### 2d. Classes — sidegrades (a strength paired with a weakness), never a power tier
- **✅ SOUND — Ranger, Weak-Point Marksman:** fully-locked shot ignores ~12 flat
  DR — the **skill-gated** answer to the immunity wall (must hold a lock, stand
  still). Countered by closing distance / breaking LOS. *Standout.*
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
  (default `physical`), refactor `_get_target_armor_reduction` → typed
  `_get_target_resist`. `get_stat_total` already sums any new stat key across the
  5 armor slots, so adding `fire_resist`/`psychic_resist`/`blast_plating` to
  `AGGREGATED_STATS` works for free. Physical keeps today's flat model; the other
  types read their OWN resist and nothing else.
- 🔴 **% mitigation cap must be ≤ 50%, NOT 75%.** A 75% cap = 4× effective HP on
  that axis = a >2× progression violation. Cap each %-resist axis at 50% so no
  single axis exceeds 2× EHP.
- 🔴 **Multi-axis resist stacking rebuilds the immunity wall.** Adding stat keys
  alone lets one full armor set carry fire+psychic+blast+DR = a capped full-
  spectrum turtle. **Required invariant:** resist stats mutually exclusive per
  slot OR a global resist budget (sum of all %-resists capped), schema-validated.
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
- ✅ Approach: **planets.yaml LEVEL gate = single source of truth; retire
  ranks.yaml `planet_access`** (or regenerate it from the level gate).
- `CmdTravel` enforcing `can_access_planet` is the missing wiring.
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

### 3.5 Planet/resource/rank RE-MAP  *(COMPLETE — derived directly; workflow stalled on API error)*

> Note: the fan-out workflow for this stalled on an infrastructure API error
> (no design output). This ladder was derived directly from the rank bands,
> current recipe costs, and gear gates — it is arithmetic + a dependency audit,
> fully checked below.

**The re-mapped ladder** (gate = the band-low level of the rank; single source
of truth = planets.yaml LEVEL, ranks.yaml `planet_access` regenerated to match):

| # | Planet | Rank (gate level) | Resources (▲ = new this rung) | Role |
|---|--------|-------------------|-------------------------------|------|
| 1 | **Terra** | Recruit **L1** | Wood, Stone, Iron, ▲**Biomass** (Terra-exclusive) | Start home |
| 2 | **Forge** | Corporal **L11** | Iron, ▲**Energy**, ▲**Circuits** | Industrial |
| 3 | **Tundra** | Sergeant **L16** | Stone, Iron, ▲**Cryogen** | Frozen |
| 4 | **Inferno** | Lieutenant **L29** | Iron, Energy, ▲**Pyronite** | Volcanic |
| 5 | **NEW HOME** *(design: e.g. "Verdant"/"Haven")* | Colonel **L57** | Wood, Stone, Iron, Energy, Circuits, ▲**Aureth** — but **NOT Biomass** | **Endgame home** (major bases) |
| 6 | **Citadel** | Brigadier **L70** | Iron, Energy, Circuits, ▲**Nexium** | **Battleground** (raid, not staged) |
| — | **Space** | off-ladder hub (reachable once travel unlocks) | ▲**Voidglass** (optional, excluded from progression) | Travel hub |

Gaps: 10 / 5 / 13 / 28 / 13. The **L29→L57 Inferno→NewHome gap (28)** is the one
soft spot — it's the mid-game plateau; the graduation *pull* rewards (§4) and
Space-hub content should live here to keep it engaging. (Alternative if that gap
feels dead: pull NewHome down to ~Major L46, gap 17/24 — a pacing tuning knob.)

**New resource types required** (each needs a `RESOURCE_TYPES` entry in
constants.py + a `resource_weights` value in balance.yaml + a terrain-tile
re-type). Beyond today's 6 (Wood/Stone/Iron/Energy/Circuits/Nexium), add **4**:
- **Biomass** (Terra) — the permanently-exclusive resource; re-type a null Terra
  tile (e.g. Plains/Dirt). Purpose: consumed by an early-and-ongoing sink (e.g.
  medkits/consumables, or an organic-tech line) so every player — including
  endgame — keeps a Terra agent-harvest running. **This is the round-trip anchor.**
- **Cryogen** (Tundra) — re-type Hot_Spring/Frozen_Lake. Gates a cold sidegrade
  (slow-field / cryo ammo) at ≥ Sergeant.
- **Pyronite** (Inferno) — re-type Sulfur_Pit/Lava_Flow. Gates the fire damage-type
  gear (§3a) at ≥ Lieutenant.
- **Aureth** (New Home) — the endgame-home signature; gates end-tier sidegrade
  utility at ≥ Colonel.
- (Space's **Voidglass** is optional side content — NOT counted in the mandatory 4.)

**Dependency audit — the forward-dependency bug this MUST fix.** Today's biggest
break: **freely-craftable (`rank=none`) items require Energy/Circuits, which don't
exist until L21.** A Recruit literally cannot craft these on Terra:

| Element | Gate | Needs | Provided at | Fix |
|---------|------|-------|-------------|-----|
| medkit, land_mine, frag_grenade, energy_cell | **none (L1)** | Energy | Forge L11 | Move these to a **Biomass/basic** recipe (Terra-craftable) OR gate them to Corporal L11 |
| combat_stim | none (L1) | Energy+Circuits | Forge L11 | Re-cost to basics, or gate to Corporal |
| Radar (RD) | L9 | Energy | Forge L11 | Move RD gate → L11, or re-cost to Iron-only |
| Relay (RL) | L15 | Energy | Forge L11 | OK once Forge=L11 (15≥11) ✓ |
| Shield Generator (SG) | L15 | Energy+Circuits | Forge L11 | OK once Forge=L11 ✓ |
| Medbay (MB) | L18 | Energy | Forge L11 | OK ✓ |
| plasma_rifle/grenade | Sergeant L16 | Energy+Circuits | Forge L11 | OK ✓ |
| jetpack, proximity_mine | Staff_Sgt L22 | Energy+Circuits | Forge L11 | OK ✓ |
| power_armor | Captain L37 | Energy+Circuits | Forge L11 | OK ✓ |
| advanced_weapons tech | Lieutenant L29 | Energy | Forge L11 | OK ✓ |

**Key insight:** moving **Forge to L11** (from L21) resolves *most* forward
dependencies automatically, because Energy/Circuits then arrive before nearly
everything that needs them. The only remaining fixes are the handful of
**`rank=none` items that need Energy** — either re-cost them to Terra basics
(preferred: keeps new players self-sufficient) or gate them to Corporal L11.

**Gate-conflict resolution:** planets.yaml `rank_requirement` (level) is the
single source of truth; regenerate ranks.yaml `planet_access` lists from it, or
delete them and derive access from the level gate via `can_access_planet`.

**Open design choices for you:**
- Names for the new planet + its resources (placeholders above).
- Which sink consumes **Biomass** (the round-trip anchor) — consumables? an
  organic-tech line? It must be something *endgame players still need*.
- The Inferno→NewHome pacing gap (accept + fill with content, or pull NewHome to ~L46).

> **Original captured intent (pre-remap), retained for reference:**

### 3a. Five damage types (the user's flagship idea — structural fix for the immunity wall)
Splitting damage into types with **type-specific resists** means no single armor
stat grants universal immunity — a slot/weight/cost budget forces every build to
specialize, always leaving a hole a skilled attacker can exploit (satisfies
principle 1: every defense has an attack that answers it).

- **Physical / regular** — reduced by normal armor DR (current model).
- **Fire** — applies a burn DoT to players; needs fire resist; partially ignores
  physical DR.
- **Sound / sonic** — high crit/headshot chance; bypasses armor on crit;
  countered by ear protection.
- **Psychic** — bypasses physical armor entirely; only psychic-specific armor
  resists it (a pure physical turtle is fully exposed unless they also carry
  psychic gear).
- **Blast** (bombs) — *degrades/destroys* non-blast armor durability rather than
  just being reduced by it; countered only by blast plating. Makes explosives an
  armor-**shredding** vector, not just high-flat-damage.

### 3b. Planet segregation (limit seasoned-vs-new contact)
- `can_access_planet` exists ([rank_system.py:246](../../../mygame/world/systems/rank_system.py))
  but is **UNWIRED** — no travel command, zero callers.
- **Data conflict to resolve:** planets.yaml gates by LEVEL, ranks.yaml
  `planet_access` gates by RANK NAME. Pick one source of truth.
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
  that Tundra L31 yields the *same* Wood/Stone/Iron as Terra L1). ⚠ Two
  corrections: (1) new resource names (Cryonite/Pyronite) MUST be added to
  `RESOURCE_TYPES` (constants.py) + `resource_weights` (balance.yaml) or schema
  validation rejects the whole registry; (2) "distinct properties" (shield-
  pierce/DoT/slow) are a **combat-engine feature**, not small — ship v1 using
  only stat_modifiers the engine already reads (damage/range/DR/move_speed), and
  treat novel properties as §3a damage-type work with paired defenses.
- **Ascending Yield Tiers** (needs-adj): higher planets give more yield/action
  (today yield/respawn are single global constants). Ship `yield_scale` only;
  `respawn_scale` is inert until node depletion is wired.
- **Planet-Tier NPC Bases & Rare-Gear Scaling** (needs-adj): tougher bases +
  higher `rare_gear_chance` up top; some rares drop *only* from Citadel-tier
  fortresses. Confined spatially so Terra keeps its gentle 0.03/0.08 rolls.
- **Nexium Economy Sink — Refinery/Converter** (needs-adj): gives the currently-
  inert Nexium a real recurring sink (convert bulk lower-tier → scarce higher-
  tier, or a Nexium-cost upgradable building). ⚠ Do NOT let it *output* Nexium
  (no compounding); make it a recurring sink not a one-time build tax.

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

**Architectural finding (verified in code):**
- Agents **already tick cross-planet.** `agent_system.process_tick`
  (typeclasses/scripts.py `agent_processing` step) is fed the FULL agent roster
  via `_get_all_agents`, gated only per-agent — **not** by owner
  presence/planet. A harvester left on Terra keeps working while the owner is on
  Citadel. → This vision is **close to already-supported, not from-scratch.**
- ⚠ **Caveat to verify:** the tick loop's `_compute_active_data`
  ([scripts.py:337](../../../mygame/typeclasses/scripts.py)) only activates world
  chunks on planets where an **online player currently stands**. Confirm whether
  left-behind-agent planets get chunks activated, or whether agents idle when no
  player is present on that planet. This is the make-or-break wiring question for
  the cross-planet agent economy.

---

## CONSOLIDATED IMPLEMENTATION ROADMAP

Ordered by dependency (what unblocks what) and by
"fix-the-violation-before-adding-content." Each phase is independently shippable
and testable. Effort tags from the verified proposals.

### Phase 0 — Keystone bug fixes *(small, do first, no dependencies)*
The combat model is broken *today*; these restore the core principle and can ship
before any new content.
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
  `energy_cell` + `combat_stim` are futuristic-tech supplies — the re-map moving
  Forge → L11 makes their Energy/Circuits reachable at their tier; re-costing an
  "energy cell" to wood would be nonsensical. The guard allowlists these two.

### Phase 1 — New-player protection *(small–medium; depends on Phase 0)*
- **[S] Rank-gap attack penalty** (§3c) — the ✅-sound owner-attributed version
  with base-defense exemption; floored (never zero → self-defense preserved).
- **[S] Kill-XP decay + loot penalty vs down-rank victims** (§2a/§4) — removes
  newbie-farming as a snowball engine.
- **[S] Anti-snowball fixes** (§2a) — DR soft-cap + permanent-bonus cap.

### Phase 2 — Planet re-map + travel *(medium; the structural backbone)*
Do the re-map as ONE coordinated change (gates + resources + recipe audit move together).
- **[S] Resolve gate conflict** — planets.yaml level = source of truth; regen
  ranks.yaml `planet_access`.
- **[M] Apply the re-mapped ladder** (§3.5) — new gates (Forge→L11 fixes most
  forward-deps), 4 new resources (Biomass/Cryogen/Pyronite/Aureth), Tundra/Space
  de-duplication, Citadel→L70.
- **[M] `CmdTravel`** enforcing `can_access_planet` — the missing wiring that
  makes graduation real. Space = the hub it routes through.
- **[verify] cross-planet agent economy** — confirm the tick loop activates
  chunks on planets with no online player (§5 open question) before relying on it.

### Phase 3 — Damage-type system *(medium–large; depends on Phase 0 floor)*
Ship incrementally, physical-first (default `physical` = zero-risk migration).
- **[M] Type-aware backbone** — branch `_calculate_damage`, typed
  `_get_target_resist`, new AGGREGATED_STATS keys. **Enforce the invariants:**
  ≤50% per-axis cap, a global resist-budget (no full-spectrum turtle), baseline
  resist gear at spawn, and a loadout-scouting readout (informed counterplay).
- **[M] Fire + burn DoT** (needs a small EffectSystem tick) — pairs w/ Pyronite.
- **[S] Psychic** (physical-armor bypass) · **[L] Blast** (armor-durability;
  building SHIELD stays the blast defense). *Drop or redefine `sound` — no crit
  system exists.*

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
Turtle-breakers (Tungsten AP rounds ✅, Ranger Marksman ✅, AP-rounds tech), the
sidegrade classes, loseable-gear catalog, QoL techs (Logistics Network ✅), the
new attack vectors (EMP, smoke, grappling, decoy).

### Cross-cutting invariants (apply in every phase)
1. ≤50% per-axis % mitigation; global resist budget (no new immunity wall).
2. Rank-gap / attenuation penalties key off the **owning player**, exempt a
   defender's own base defenses.
3. Graduation push-throttles key off graduation-eligibility and **exempt the
   Terra-exclusive resource**.
4. New players start self-sufficient (Terra-craftable essentials, baseline resist).
5. Every new attack ships with its named defense; every permanent bonus stays
   flat + small + under 2×.

## Open questions (for you)
- New planet + resource **names** (placeholders: Verdant/Haven; Biomass, Cryogen,
  Pyronite, Aureth, Voidglass).
- Which sink consumes **Biomass** (the round-trip anchor)? Must be something
  endgame players still need.
- **Inferno→NewHome pacing gap** (L29→L57 = 28 levels): fill with content, or
  pull NewHome down to ~Major L46?
- Round-trip **friction**: travel cost / agent-only / cooldown for returning to
  lower planets to harvest?
- Permanent tech combat bonuses: cap them, or convert to loseable gear?
