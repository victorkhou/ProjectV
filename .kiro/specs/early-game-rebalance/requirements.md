# Requirements Document

Early-Game Rebalance & Agent Purpose

## Introduction

The current early-game fails against every compulsion-loop principle:

- **No immediate feedback:** The only XP sources are combat (kill / destroy /
  damage). A new player building their base — the activity that *is* the first
  hour — sees zero progress on the one visible bar. The bar is the most powerful
  dopamine channel in games, and we've disconnected it from the primary activity.

- **No Goldilocks cadence:** Unlock tiers cluster: level 1 dumps HQ/EX/AC/AR,
  then *nothing* until level 11 (Wall/Barracks), then another desert until 26+.
  Meanwhile a single outpost fight spikes the player from L1→L12. That's either
  boredom or a skip — never flow.

- **Agents are unreachable precisely when they'd matter:** Guard/Scout require
  Turret (L31) / Radar (L31). Delivery gates at agent-level 21 (requiring owner
  L22+). Patrol — the most intuitive concept — literally can't be invoked by a
  new player. Soldier/Medic are placeholder `pass` stubs exposed as valid roles.

- **No variable rewards:** Every harvest is 1 unit. Every outpost drops exactly
  `{Iron: 30, Stone: 20}`. Nothing ever rolls dice; the brain never anticipates.

- **Dead configuration:** `production_scaling` in balance.yaml is loaded,
  validated, and read by nothing. Ranks.yaml `unlocks` lists disagree with
  the enforced `rank_requirement` in buildings.yaml.

This spec restructures the first hour into a 30–60 minute onboarding arc where
(a) every action feeds progress, (b) a new unlock arrives every 1–3 minutes
early and every 5–7 minutes later, (c) agents become the player's primary base
automation companion within the first 15 minutes, and (d) variable rewards
inject surprise into the repetitive loops.

The guiding philosophy: **one level bar (economy XP finite + front-loaded,
combat XP renewable + risky), gates split by what they protect (self-facing =
level, world-facing = level + deed, upgrades = cost only).** This avoids dual
XP tracks while naturally transitioning from Act 1 (build) to Act 2 (fight).

## Glossary

- **Entity_Level**: The player's single visible level (1–100), derived from
  `db.combat_xp` via the hybrid formula curve (R14).
- **Economy_XP**: XP awarded for non-combat activity (building, upgrading,
  training, harvesting). Primarily one-time or self-capping — supply exhausts.
- **Combat_XP**: XP awarded for kills, damage, structure destruction. Renewable
  and risk-gated — the player must be vulnerable to earn.
- **Deed**: A milestone record in the player's deed store. Boolean deeds are
  one-time flags recording that the player accomplished something once; counted
  deeds track a per-deed count (deed-id → count) for gates that require
  repetition (e.g. `outposts_cleared_3`). Deeds gate world-facing unlocks on
  top of level, giving the player a "did the thing" moment.
- **Deed_Gate**: An optional field on a building definition (`unlock_deed`)
  requiring a specific Deed in addition to the level requirement.
- **Directive**: One step in an onboarding checklist. Each directive has a
  trigger event (from the existing EventBus), a small reward (resources + XP),
  and auto-advances on completion. The full sequence forms the player's Act 1
  spine.
- **Harvest_Crit**: A random ×5 yield event with ~5% probability per manual
  harvest action, with a "Rich vein!" notification.
- **Loot_Range**: An outpost/fortress loot value drawn uniformly from
  `[min, max]` instead of a fixed amount.
- **Gear_Drop**: A chance-based item drop from NPC guards/outpost destruction.
- **Scout_Vision**: Vision radius projected by a patrolling scout agent,
  expanding the fog-of-war along its route.

---

## Requirements

### Requirement 1: Economy XP — Every Builder Action Moves the Bar

**User Story:** As a new player, I want every building I construct, every
upgrade I complete, every harvest I perform, and every agent I train to award
me XP so my level bar moves during the primary first-hour activity.

#### Acceptance Criteria

1. WHEN a building construction completes, THE system SHALL award the owning
   player `xp_build_complete` XP via `RankSystem.award_xp` — on BOTH completion
   paths: player-present (`process_construction_tick`) and Engineer-agent
   (`process_agent_construction`). The award is one-time per building either
   way, and docking agent-completed builds would penalize the delegation this
   spec promotes. *(Resolved decision D1.)*
2. WHEN a building upgrade completes, THE system SHALL award the owning player
   a flat `xp_upgrade_complete` XP (30) — the same amount regardless of the new
   level. *(Resolved decision D6.)*
3. WHEN a player manually harvests a resource (each harvest action), THE system
   SHALL award `xp_harvest_action` XP (the trickle keeps the bar live during
   the repetitive loop without dominating — combat still outpaces it 10:1).
4. WHEN agent training completes, THE system SHALL award the owning player
   `xp_agent_trained` XP.
5. Economy XP amounts SHALL be tunable in `balance.yaml` without code changes.
6. Autonomous agent harvesting (HarvesterScript) SHALL NOT award player XP
   (it already awards *agent* XP). Automation produces resources, never player
   progression — this ensures combat remains the only scalable XP source.
7. The XP threshold curve SHALL be re-calibrated so that a pure builder reaches
   ~L8–9 in the first hour from economy XP alone, with a long-run no-combat
   ceiling of ~L13 (restated against the R14 hybrid curve; resolved decision
   D11). Upgrade XP supply is finite — 12 building types × 4
   upgrades × 30 = ~1,440 XP — and time-gated by exponential upgrade timers,
   so the ceiling is approached slowly, never farmed. One-time sources exhaust
   and the manual harvest trickle is bounded by cooldown. *(Resolved decision
   D6.)*

#### Balance Values (initial)

| Key | Value | Rationale |
|-----|-------|-----------|
| `xp_build_complete` | 30 | ~2 buildings = L2 (40 XP), satisfying ding cadence |
| `xp_upgrade_complete` | 30 | Flat per completed upgrade; lifetime supply ~1,440 XP (12 types × 4 upgrades), time-gated by upgrade timers |
| `xp_harvest_action` | 1 | ~900/hr manual ceiling vs ~800/10min combat — combat dominant |
| `xp_agent_trained` | 40 | Training is a 90s wait; reward matches the investment |
| `xp_hq_destroy` (retuned) | 300 | Down from 500; prevents one-fight L12 vault |

---

### Requirement 2: Unlock Cadence — One New Toy Every 1–3 Minutes Early

**User Story:** As a new player, I want to unlock a new building or capability
every few minutes in the first half hour, so there's always a visible "next"
on the horizon.

#### Acceptance Criteria

1. Building `rank_requirement` values SHALL be redistributed to deliver roughly
   one new unlock every 1–2 levels from L1 through L11, then every 3–4 levels
   thereafter.
2. The tier table SHALL be the single source of truth — `buildings.yaml`
   `rank_requirement` is authoritative; `ranks.yaml` `unlocks` lists SHALL be
   removed (or generated) to eliminate the current disagreement.
3. The HQ SHALL remain level 1 (available immediately from spawn).
4. No building SHALL gate higher than level 18 (Medbay/Relay), ensuring all
   building types are achievable within a reasonable play period.

#### Proposed Tier Redistribution

| Building | Current req | Proposed req | When reached (est.) |
|----------|-------------|--------------|---------------------|
| HQ | 1 | 1 | 0 min (spawn) |
| Extractor | 1 | 1 | 0 min |
| Academy | 1 | 1 | 0 min |
| Wall | 11 | **2** | ~3 min (first build dings L2) |
| Armory | 1 | **3** | ~5 min |
| Vault | 36 | **4** | ~8 min |
| Turret | 31 | **5** | ~10 min |
| Barracks | 11 | **7** | ~15 min |
| Radar | 31 | **9** | ~20 min |
| Lab | 26 | **11** | ~30 min (session-two hook) |
| Relay | 41 | **15** | ~45 min+ |
| Medbay | 46 | **18** | 60 min+ |

---

### Requirement 3: Agent Purpose — Harvesters and Delivery in Session One

**User Story:** As a new player training my first agent at minute 10, I want it
to immediately perform useful work — harvesting, then delivering — so agents
feel like my base automation companion, not an afterthought.

#### Acceptance Criteria

1. Agent cap ceiling SHALL be `max(1, owner_level)` (remove the `-1`), so a
   level 1 player's agent can reach effective level 1 and earn XP immediately.
2. The `delivery` ability gate SHALL be reduced from agent level 21 to **5**,
   reachable within session one (~15 min of harvester XP at 5 XP per cycle,
   4-tick cycles ≈ 75 cycles to reach agent L5 threshold).
3. Training time SHALL be reduced: `base_training_ticks: 90` (1.5 min at 1
   tick/s) from 300 (5 min). The Academy level reduction still applies,
   reaching ~45s at Academy L3.
4. The system SHALL NOT change agent XP award values — they already feel
   appropriate for an agent leveling at a slower pace than the player.

---

### Requirement 4: Agent Purpose — Guard/Scout Without Building Requirement

**User Story:** As a new player, I want to assign an agent as a guard or scout
and set a patrol route immediately, without needing a Turret (level 31) or
Radar (level 31) first.

#### Acceptance Criteria

1. Guard and Scout roles SHALL be changed to **army roles** (no building
   requirement), equivalent to how Soldier works today.
2. A player SHALL be able to `agent assign <id> guard` or `agent assign <id>
   scout` without a target building. The agent stays at its current location.
3. A guard/scout MAY optionally be assigned to a Turret/Radar for a **station
   bonus** (future enhancement — not in this spec's scope, but the architecture
   must not preclude it).
4. `agent patrol <id> <waypoints>` SHALL work for guard/scout agents as today,
   with no additional level or building gate beyond owning the agent.
5. GuardCombatSystem SHALL continue to drive auto-attack for guards on patrol
   (already true today — it reads `role == "guard"` regardless of building).

---

### Requirement 5: Agent Purpose — Scout Vision Projection

**User Story:** As a player who assigns a scout to patrol, I want the map to
reveal tiles along the scout's route, making the patrol visibly useful beyond
just "walking around."

#### Acceptance Criteria

1. `FogOfWar.get_visible_tiles` SHALL include a vision circle around each of
   the player's scout-role agents (radius = `scout_vision_radius`, default 5).
2. Vision SHALL only be projected for agents that are (a) role "scout", (b) not
   incapacitated, (c) in the PLAYING state's planet room (same location as the
   player's active play area).
3. The vision radius SHALL be tunable via `balance.yaml` (`scout_vision_radius`).
4. Non-scout agents (harvesters, guards, engineers) SHALL NOT project vision
   (their positions are at buildings, already covered by building vision).

---

### Requirement 6: Hide Unimplemented Roles (Soldier/Medic)

**User Story:** As a new player, I want the role list to show only roles that
actually do something, so I don't waste agents on placeholders.

#### Acceptance Criteria

1. `VALID_ROLES` (the player-facing list) SHALL exclude `soldier` and `medic`
   while their scripts contain only `pass` stubs.
2. The `AGENT_ROLES` registry SHALL retain the full entries (so the code isn't
   lost), but derive `VALID_ROLES` by filtering on a `hidden: bool` flag on
   `RoleSpec`.
3. Admin commands SHALL still be able to assign hidden roles for testing.
4. When Soldier/Medic scripts are implemented (out-of-scope), setting
   `hidden=False` re-exposes them — no other code change.

---

### Requirement 7: Variable Rewards — Harvest Crits

**User Story:** As a player manually harvesting, I want an occasional ×5 yield
burst ("Rich vein!") so the repetitive action contains surprise.

#### Acceptance Criteria

1. Each manual harvest action SHALL have a `harvest_crit_chance` probability
   (default 5%) of yielding `harvest_crit_multiplier` × normal amount (default
   ×5).
2. WHEN a crit triggers, THE system SHALL fire a player notification
   `"harvest_crit"` with the bonus amount, rendered as "|g[Rich vein!]
   +{amount} {resource}|n".
3. Crit chance and multiplier SHALL be tunable in `balance.yaml`.
4. Autonomous agent harvesting SHALL NOT crit — it's a player-presence reward,
   reinforcing that being at the keyboard matters.

---

### Requirement 8: Variable Rewards — Loot Ranges & Gear Drops

**User Story:** As a player raiding outposts, I want loot amounts to vary and
have a chance to find gear, so each raid is a mini slot-machine pull.

#### Acceptance Criteria

1. Outpost/fortress `loot` entries in `outposts.yaml` SHALL support range
   syntax: `Resource: [min, max]` drawn uniformly, alongside fixed
   `Resource: N` for backward compatibility.
2. Each guard kill SHALL have `guard_loot_chance` (default 40%) of dropping
   `guard_loot_amount` of a random resource from the base's loot table (mini-
   drop, instant gratification per kill).
3. On base HQ destruction, THE system SHALL roll `gear_drop_chance` (default
   15% outpost, 30% fortress) for a random item from a tier-appropriate pool
   (`outpost_gear_pool`, `fortress_gear_pool` — lists of item keys).
4. Capped rare drops: `rare_gear_chance` (3% outpost, 8% fortress) for a
   higher-tier item from `rare_gear_pool`.
5. All probabilities and pool names SHALL be defined in the outpost template
   YAML or `balance.yaml`, tunable without code.
6. Drop resolution SHALL use Python's `random` module; reproducibility is not
   required (this is not competitive ranked content).

---

### Requirement 9: Deed Gates — Proof of Activity for World-Facing Unlocks

**User Story:** As a game designer, I want certain powerful buildings (Barracks,
Lab) to require not just a level but proof that the player has done the relevant
activity, so power is earned through engagement, not passive XP farming.

#### Acceptance Criteria

1. `PlayerCharacter` SHALL carry a deed store (`db.deeds`) supporting BOTH
   boolean deeds (one-time flags) AND counted deeds (deed-id → count).
   *(Resolved decision D9.)*
2. Building definitions MAY include an optional `unlock_deed: <deed_id>` field.
   WHEN present AND the player lacks that deed (or hasn't reached its required
   count), construction is refused with a message naming the deed ("Requires:
   destroyed an outpost").
3. Deeds SHALL be awarded by subscribing to existing EventBus events:
   - BASE_ELIMINATED for an NPC outpost → boolean deed `"outpost_cleared"` AND
     increments the count behind `"outposts_cleared_3"`.
   - BASE_ELIMINATED for a fortress → deed `"fortress_cleared"` — recorded for
     future gates, but gating nothing in this spec. *(Resolved decision D9.)*
   - `BASE_ELIMINATED` publishes the acting entity as `attacker=`, so this
     subscription uses `player_key: attacker` with owner resolution per R10.8.
     *(Resolved decision D7.)*
   - Future deeds can be added data-driven without new code (event_name →
     deed_id mapping in a config file or constants).
4. The deed check SHALL be in `BuildingSystem._validate_construction` alongside
   the existing `rank_requirement` check.
5. `@building list` (the available-buildings display) SHALL mark deed-gated
   buildings the player hasn't unlocked with a `[LOCKED: <deed>]` suffix.

#### Proposed Deed Gates

| Building | Deed required | How earned |
|----------|---------------|------------|
| Barracks | `outpost_cleared` (count ≥ 1) | Destroy any NPC outpost HQ |
| Lab | `outposts_cleared_3` (count ≥ 3) | Destroy 3 NPC outpost HQs |
| All others | none | Level + cost only |

The Lab gate deliberately does NOT use `fortress_cleared`: fortresses
(post-LOS) are group/gear content, and a solo L11 gate on them would stall the
tech pillar. Three outposts instead drives the repeatable Act-2 PvE loop.
`fortress_cleared` remains a recorded deed for future gates. *(Resolved
decision D9.)*

---

### Requirement 10: Directive System — Onboarding Checklist

**User Story:** As a new player, I want a step-by-step checklist guiding me
through base setup, agent training, and first combat, with small rewards at
each step, so I always know what to do next.

#### Acceptance Criteria

1. A `DirectiveSystem` SHALL maintain a per-player ordered list of directives
   (`db.directives_progress`, an int index into the sequence).
2. Each directive SHALL have: `key`, `description`, `trigger_event`,
   `trigger_condition` (optional filter on event payload), `reward` (dict of
   resources + `xp`), and `next` (key of the following directive, or `null`).
3. WHEN the player is on directive N and the matching event fires with the
   condition satisfied, THE system SHALL:
   a. Award the reward (resources via `player.add_resources`, XP via
      `RankSystem.award_xp`).
   b. Send a notification: "|w[Directive complete]|n {description} — +{xp}
      XP{resource_summary}".
   c. Advance to directive N+1 and send: "|y[Next objective]|n
      {next_description}".
4. The directive sequence SHALL be defined in a YAML file
   (`data/definitions/directives.yaml`) — adding/reordering is a data change.
5. A `directives` command SHALL show current and completed steps.
6. Directive rewards are one-time per player (idempotent on repeated events).
7. The chain SHALL be dismissable as a whole — `directives off` silences all
   remaining notifications and forfeits their rewards; `directives on`
   re-enables from the current position. There is NO per-step skip: the chain
   is ordered, and steps a dismissed player performs naturally still complete
   silently (progress advances without notification or reward spam).
   *(Resolved decision D2.)*
8. Each directive entry MAY declare `player_key` (default `"player"`) naming
   the event-payload key that carries the acting entity. WHEN the resolved
   entity is an NPC/agent/turret rather than a player, THE system SHALL
   resolve credit to its owner (`db.owner`) — delegation is never penalized
   (consistent with D1). Specifically: `BASE_ELIMINATED` publishes
   `attacker=`, so directive 8 (and the R9.3 deed subscription) uses
   `player_key: attacker` with owner resolution. *(Resolved decision D7.)*
9. THE system SHALL publish four new EventBus events required by the chain:
   `AGENT_TRAINED`, `AGENT_ASSIGNED`, `ITEM_EQUIPPED`, and `PATROL_SET`.
   `SCOUT_REVEALED_TILES` is dropped — directive 10 retriggers on `PATROL_SET`
   with condition `role: scout` instead. *(Resolved decision D8.)*

#### Initial Directive Sequence

| # | Description | Trigger event | Reward |
|---|-------------|---------------|--------|
| 1 | Build your Headquarters | CONSTRUCTION_COMPLETED (HQ) | 15 XP |
| 2 | Build an Extractor on a resource tile | CONSTRUCTION_COMPLETED (EX) | 15 XP, +10 Wood |
| 3 | Train your first agent | AGENT_TRAINED (new event) | 30 XP, +10 Iron |
| 4 | Assign a harvester to your Extractor | AGENT_ASSIGNED (new event) | 15 XP |
| 5 | Build a Wall to protect your base | CONSTRUCTION_COMPLETED (WL) | 20 XP, +15 Stone |
| 6 | Equip a weapon from the Armory | ITEM_EQUIPPED (new event) | 20 XP |
| 7 | Assign a guard and set a patrol | PATROL_SET (new event, `role: guard`) | 20 XP |
| 8 | Destroy an NPC outpost | BASE_ELIMINATED (outpost, `player_key: attacker`) | 50 XP, +30 Iron |
| 9 | Upgrade your HQ to level 2 | BUILDING_UPGRADED (HQ, L2) | 40 XP |
| 10 | Explore with a scout patrol | PATROL_SET (new event, `role: scout`) | 25 XP |

Non-combat steps total 200 XP; step 8's combat reward stays at 50. *(Resolved
decisions D6, D8.)*

---

### Requirement 11: Housekeeping — Dead Config Cleanup

**User Story:** As a developer, I want configuration that is loaded but unused
removed, and conflicting gate sources reconciled, so the codebase doesn't
mislead future contributors.

#### Acceptance Criteria

1. `production_scaling` SHALL be removed from `balance.yaml`, `definitions.py`,
   and `schema_validator.py` (it is referenced by zero runtime code paths).
2. `ranks.yaml` `unlocks` lists SHALL be removed (or flagged as cosmetic/
   display-only) since `buildings.yaml` `rank_requirement` is the enforced
   gate.
3. Armory's `rank_requirement` (currently 1 but ranks.yaml says Staff Sergeant)
   SHALL be set to the proposed value (3) so there is no ambiguity.
4. `xp_damage` SHALL be removed from `balance.yaml`, `definitions.py`, and
   `schema_validator.py` — it is loaded and validated but referenced by zero
   game code, the same class of dead config as `production_scaling`.
   *(Resolved decision D10.)*
5. Gear pool keys (`gear_pool`, `rare_pool` in `outposts.yaml`) SHALL be
   validated against item definitions at load time, failing loudly on unknown
   keys, so a drop can never silently no-op. *(Resolved decision D10.)*

---

### Requirement 12: XP Supply-Shape Integrity

**User Story:** As a game designer, I want the economy XP sources to be
naturally self-limiting so a player cannot AFK-farm their way to max level
without engaging in combat.

#### Acceptance Criteria

1. `xp_build_complete` and `xp_upgrade_complete` are flat, one-time awards per
   building / per completed upgrade — bounded by the finite number of building
   slots and upgrade steps (~1,440 XP lifetime upgrade supply: 12 building
   types × 4 upgrades × 30), and time-gated by exponential upgrade timers.
   *(Resolved decision D6.)*
2. `xp_agent_trained` is bounded by agent cap (max 14 at highest rank).
3. `xp_harvest_action` is bounded by the harvest cooldown (4 ticks = 4s per
   action); a player manually harvesting non-stop earns ~900 XP/hr — comparable
   to a single outpost fight. This makes sustained manual harvesting a viable
   but slow alternative, never the optimal path.
4. Directive rewards are strictly one-time per player.
5. IF a future feature adds repeatable economy XP (e.g. trade), IT SHALL be
   gated behind a cooldown or diminishing-returns mechanism documented in its
   own spec.

---

### Requirement 13: Tech Repair — Research Must Be Real and Effective

**User Story:** As a player who reaches the Lab at L11, I want researching a
technology to be a meaningful choice with a real payoff, so the tech pillar is
an actual progression system rather than dead UI.

The tech system is broken twice over today:

- `RankSystem._unlock_for_rank` auto-grants every tech at-or-below the
  player's new rank for free on promotion (and `_revoke_above_rank` revokes on
  demotion, even paid-for techs) — making Lab research pointless.
- `TechLabSystem._apply_stat_bonus` only understands `{stat: ..., bonus: N}`
  payloads, while all five techs in `technologies.yaml` use different keys
  (`building_hp`, `damage`, `damage_reduction`, `sight_range`,
  `production_multiplier`) — so every tech effect is a no-op.

#### Acceptance Criteria

1. WHEN a player is promoted to a new rank, THE system SHALL NOT auto-grant
   technologies. Research at a Lab SHALL be the only tech-acquisition path.
2. WHEN a player is demoted, THE system SHALL NOT revoke researched
   technologies.
3. All five shipped tech effects SHALL actually apply — the effect-application
   path SHALL handle the shipped payload keys (`building_hp`, `damage`,
   `damage_reduction`, `sight_range`, `production_multiplier`).
4. Tech `required_rank` gates SHALL be re-aligned so the earliest tech is
   researchable at the new Lab level (L11, rank 3 Corporal), with the
   remaining techs spaced upward from there.
5. Existing players' `researched_techs` granted by the old auto-grant SHALL be
   left as-is — grandfathered, with no retroactive revocation.

---

### Requirement 14: Progression Ladder — 100 Levels, Hybrid Curve, Widening Rank Bands

**User Story:** As a player, I want a deep 100-level ladder whose early levels
ding fast and whose late levels are a real but always-reachable grind, so
progression stays top of mind for hundreds of hours without ever hitting a
mathematical wall.

Context: pure +20% compounding to L100 was evaluated and rejected —
compounding 20% over 99 levels is a ×69M growth factor (~13.8B XP, centuries
at current income). XP income is roughly flat (kills 100, outposts ~600), so
exponential cost curves stall around L45–50. *(Resolved decision D11.)*

#### Acceptance Criteria

1. THE system SHALL raise `MAX_LEVEL` from 60 to 100.
2. THE per-level XP cost SHALL follow a hybrid curve: the L1→L2 delta is
   40 XP (preserving this spec's economy-XP calibration); each delta grows
   +20% per level through L20 (`delta(n) = 40 × 1.2^(n−2)` for n ≤ 20, so
   `delta(20)` ≈ 1,066); thereafter each delta grows +5% per level
   (`delta(n) = delta(20) × 1.05^(n−20)` for n > 20).
3. Resulting cumulative checkpoints (for tuning reference — all derived from
   the formula, not hand-set): L10 ≈ 832 · L20 ≈ 6,190 · L30 ≈ 20,300 ·
   L50 ≈ 80,600 · L60 ≈ 141,000 · L100 ≈ 1.09M XP. At ~3,000 XP/hr sustained
   combat this is ~360 hours to cap, with endgame levels costing ~15–18 hours
   each.
4. Level thresholds SHALL be computed from this formula — a single source of
   truth in code/balance — replacing the current `ranks.yaml` `xp_threshold`
   interpolation and the `FINAL_RANK_XP_PER_LEVEL` constant.
5. THE system SHALL retain the 12 existing rank names over WIDENING level
   bands (rank is derived from level via per-rank bands, replacing the
   uniform `LEVELS_PER_RANK` assumption):

   | Rank | Level band |
   |------|-----------|
   | Recruit | L1–5 |
   | Private | L6–10 |
   | Corporal | L11–15 |
   | Sergeant | L16–21 |
   | Staff_Sergeant | L22–28 |
   | Lieutenant | L29–36 |
   | Captain | L37–45 |
   | Major | L46–56 |
   | Colonel | L57–69 |
   | Brigadier | L70–84 |
   | General | L85–99 |
   | Marshal | L100 (capstone — only maxed players hold it) |

   The first three bands stay at 5 levels so Corporal still begins at L11,
   preserving this spec's Lab (L11) and tech-gate alignment (R13.4).
6. Rank-derived systems (planet access, agent caps) SHALL keep their existing
   per-rank values; their effective level points shift with the bands (e.g.
   Citadel access at General now begins at L85 instead of L51) — an
   intentional stretching of long-term goals across the deeper ladder.
7. THE economy-XP calibration SHALL be restated against the new curve: the
   builder hour-one target remains ~L8–9 (thresholds L8 ≈ 517, L9 ≈ 660);
   the long-run no-combat ceiling becomes ~L13 (R1.7's ceiling claim is
   updated from ~L12 to ~L13 accordingly).
8. WHEN migrating existing players, THE system SHALL preserve their levels:
   stored XP maps onto the new curve (level recomputed from XP); IF that
   recomputation would lower an existing player's level, THEN THE system
   SHALL keep the higher of the two — no visible demotion from a rebalance.

---

## Resolved Design Decisions

The four original open questions were resolved (2026-07-18); a stakeholder
review the same day resolved six further gaps (D5–D10):

1. **D1 — Construction XP awards on both completion paths.** Player-present
   and Engineer-agent completion both award `xp_build_complete`. Supply is
   one-time per building regardless of path; presence-only would make
   assigning an Engineer a progression penalty, cutting against this spec's
   agent-promotion goal. *(Folded into R1.1.)*

2. **D2 — Directives are dismiss-all only.** `directives off` silences the
   remaining chain and forfeits its rewards; `directives on` re-enables from
   the current position. No per-step skip — the chain is ordered and skipping
   mid-sequence makes it incoherent; a dismissed player's natural actions
   still advance progress silently. *(Folded into R10.7.)*

3. **D3 — Player patrol guards reuse `guard_aggro_radius` (5).** One knob for
   all guards. If live play shows player guards need separate tuning, adding
   `player_guard_aggro_radius` later is a 3-line change — deferred until
   proven needed. *(No spec change; R4.5 stands as written.)*

4. **D4 — NPC-base difficulty scaling is out of scope.** Outpost/fortress
   difficulty stays fixed; the directive chain already points new players at
   outposts sized for them. Scaling touches spawner, combat, loot math, and
   respawn logic — a follow-up spec of its own, not a rider on this one.

5. **D5 — Fix the tech system inside this spec.** Rank promotion currently
   auto-grants every tech free (demotion revokes even paid ones) and every
   tech effect is a no-op due to a payload-key mismatch. Research becomes the
   only acquisition path, demotion never revokes, all five effects apply,
   `required_rank` gates re-align to the new Lab level (L11, Corporal), and
   old auto-granted techs are grandfathered. *(New Requirement 13.)*

6. **D6 — Flatten upgrade XP and retune supply.** `xp_upgrade_complete` is a
   flat 30 XP per completed upgrade (no ×new_level multiplier); directive XP
   is trimmed so the non-combat chain totals ~200 XP (step 8's combat reward
   stays 50); the honest builder curve is ~L8–9 in hour one with a long-run
   no-combat ceiling of ~L12 (finite, time-gated upgrade supply of ~1,440 XP).
   *(Folded into R1.2, R1.7, the R1 balance table, the R10 reward table, and
   R12.1.)*

7. **D7 — Directive payload adapter + owner credit.** Directive entries may
   declare `player_key` (default `"player"`) naming the payload key carrying
   the acting entity; NPC/agent/turret actors resolve credit to their owner
   (`db.owner`) — delegation is never penalized, consistent with D1.
   `BASE_ELIMINATED` publishes `attacker=`, so directive 8 and the R9.3 deed
   subscription use `player_key: attacker` with owner resolution. *(Folded
   into R10.8 and R9.3.)*

8. **D8 — Event coverage.** Four new EventBus events: `AGENT_TRAINED`,
   `AGENT_ASSIGNED`, `ITEM_EQUIPPED`, `PATROL_SET`. `SCOUT_REVEALED_TILES` is
   dropped; directive 10 retriggers on `PATROL_SET` with condition
   `role: scout`. *(Folded into R10.9 and the directive table.)*

9. **D9 — Lab deed becomes countable.** The deed store supports counted deeds
   (deed-id → count) alongside boolean deeds. Barracks keeps
   `outpost_cleared` (count ≥ 1); the Lab gates on `outposts_cleared_3`
   (destroy 3 NPC outpost HQs) instead of `fortress_cleared` — fortresses are
   group/gear content and a solo L11 gate would stall the tech pillar, while
   3 outposts drives the repeatable Act-2 PvE loop. `fortress_cleared`
   remains recorded but gates nothing in this spec. *(Folded into R9.)*

10. **D10 — Housekeeping additions.** `xp_damage` is removed from
    `balance.yaml`, `definitions.py`, and `schema_validator.py` (dead config,
    same class as `production_scaling`); gear pool keys (`gear_pool`,
    `rare_pool` in `outposts.yaml`) are validated against item definitions at
    load time, failing loudly on unknown keys. *(Folded into R11.4–R11.5.)*

11. **D11 — 100-level ladder with hybrid XP curve and widening rank bands
    (2026-07-18).** `MAX_LEVEL` rises from 60 to 100. Per-level XP deltas
    are anchored at 40 XP for L2 (preserving the economy-XP calibration),
    grow +20% per level through L20, then +5% per level to L100
    (~1.09M XP to cap, ~360 hours at sustained combat income). The 12
    existing rank names are retained over widening bands per the R14 band
    table, with Marshal as the L100 capstone. Rejected alternatives: pure
    20% compounding (a ×69M growth factor over 99 levels — a mathematical
    wall around L45–50 given roughly flat XP income) and 20 ranks × 5
    levels (discarded in favor of keeping the existing military ladder's
    prestige). *(New Requirement 14; R1.7's ceiling restated ~L12 → ~L13.)*
