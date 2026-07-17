# Requirements Document

## Introduction

The alliance feature introduces the game's first many-players-to-one-group
construct. Before this feature, friend/foe was decided everywhere by a single
predicate — `world.utils.is_owner`, comparing `.id` — and there was no notion of
two distinct players being on the same side (the Sentinel typeclass docstring
explicitly records that there is "no new faction system"). Bases, targeting,
vision, XP, and stats were all strictly per-owner.

This feature adds an **Alliance**: a named, tagged group of up to a configured
number of players with a Leader / Officer / Member role hierarchy, a shared
resource **Alliance_Treasury**, an aggregate **Alliance_Level** derived from
member activity that unlocks **Perk_Tier**s, and a composite-power-score
**Leaderboard**. It threads a second side-predicate — `world.utils.are_allied` —
through only the seams the design requires, mirroring the established
single-authority `is_owner` precedent.

The integration is deliberately **shallow**: bases stay strictly per-owner
(`owner_has_active_hq` / `active_hq_owner_ids` are unchanged — an ally's HQ does
not power your base, and there is no building in ally territory or shared use of
an ally's Academy/Lab/Armory). An alliance affects exactly five things: XP
attribution and automated-targeting (combat), shared vision, stat perks,
membership/treasury, and the leaderboard. Nothing else.

Friendly fire is **hybrid**: automated units (turrets, guards/agents) never fire
on allies (target acquisition skips them), but a player MAY still manually
attack an ally or catch one in a bomb blast — the shot lands and damage is not
floored — it simply grants no XP and does not feed the killer's alliance
leaderboard tally. Betrayal is possible but unrewarded.

Requirements 15–16 capture command/event integration and the known constraints
and backlog, mirroring the player-lifecycle spec's R12/R13 blocks. Requirements
17–23 extend the base feature with the resolved design decisions: two-sided join
requests + an open-join toggle + outsider info (R17), the invite inbox/expiry and
anti-abuse throttling (R18), the name/tag policy and rename/retag verbs (R19),
alliance-tag visibility across the four player-facing surfaces (R20), the explicit
per-view column/scope contract (R21), combat-gating of the friend/foe-changing
verbs (R22), and the administrative alliance router (R23).

## Glossary

- **Alliance**: A named, tagged group of players sharing a side. Identified by a
  stable integer `alliance_id` (always `>= 1`, never `0`). Its non-derivable
  state (name, tag, leader, treasury, active perks, pending invites, pending
  requests, open-join flag) lives in one **Alliance_Record**.
- **Alliance_Record**: One alliance's persisted state, a dict stored under
  `AllianceRegistry.db.alliances[alliance_id]`. Fields: `id`, `name`, `tag`,
  `leader_id`, `officer_ids`, `member_ids`, `treasury`, `active_perks`,
  `pending_invites`, `pending_requests`, `open_join`, `withdraw_window`,
  `created_tick`, `renamed_tick`.
- **Alliance_Registry**: `world/systems/alliance_system.py::AllianceRegistry` —
  a persistent global `DefaultScript` (`persistent=True`, `self.db.alliances`,
  `self.db.next_alliance_id`) holding every Alliance_Record. The
  rebuildable-index idiom is used to reconcile rosters from **Member_Pointer**s.
- **Alliance_System**: `world/systems/alliance_system.py::AllianceSystem`
  (`BaseSystem` subclass, registered in `server/conf/game_init.py`'s
  `game_systems`). The SINGLE WRITER of every Alliance_Record and every
  Member_Pointer — all founding, invite, request, join, leave, kick, disband,
  transfer, claim, rename, treasury, and perk mutations route through it.
- **Member_Pointer**: The per-`CombatCharacter` attributes `db.player_alliance`
  (the `alliance_id` the character belongs to, or `None`) and `db.alliance_rank`
  (the character's **Alliance_Rank**, or `None`). Seeded in `PLAYER_DEFAULTS`
  (`typeclasses/characters.py`).
- **Alliance_Rank**: One of `ALLIANCE_RANKS` = {`"leader"`, `"officer"`,
  `"member"`} (in `world/constants.py`), stored in `db.alliance_rank`.
- **Are_Allied**: `world.utils.are_allied(a, b) -> bool` — the single ally
  predicate, added alongside `is_owner`. True iff `a` and `b` resolve to two
  DISTINCT REAL players (each `has_account` True, not a Sentinel, `npc_type`
  `None`; distinct `.id`, mirroring `is_owner`'s idmapper-safe comparison) who
  share the same non-`None` `db.player_alliance` that STILL resolves to a live
  Alliance_Record. A `has_account`-False holder is never treated as an ally (C8).
- **Owning_Player**: The responsible player behind a combat entity, resolved by
  `CombatEngine._owning_player` (a player is its own owner; a turret/agent/
  building/bomb resolves to its owner; an enemy NPC resolves to `None`).
  Are_Allied in combat is always evaluated against Owning_Players, never raw
  units.
- **Friendly_Fire**: Direct, player-inflicted damage on an ally (a manual
  attack or a player-armed bomb blast). Permitted and not floored, but grants no
  XP and no leaderboard credit. Automated fire is never Friendly_Fire — it is
  suppressed at target acquisition.
- **Alliance_Treasury**: A shared resource pool stored as
  `Alliance_Record["treasury"]` (a `{resource_type: int}` dict, same resource
  types as `db.resources`). Deposited into and withdrawn from via the
  Alliance_System using the read-modify-reassign pattern. Capacity is UNCAPPED in
  v1 (documented, R16.11).
- **Withdrawal_Cap**: The per-window ceiling on Officer withdrawals
  (`balance.alliance_withdraw_cap_per_window` over
  `balance.alliance_withdraw_window_ticks`), tracked per Alliance_Record in
  `withdraw_window`. A Leader withdraw bypasses the cap (B1).
- **Alliance_Level**: An integer derived from aggregate member activity by
  `AllianceSystem.compute_alliance_level`. It UNLOCKS Perk_Tiers; it is never
  stored as authoritative state (it is recomputed on read, and MAY be memoized
  and invalidated — never treated as source of truth). It is bounded above by the
  number of Perk_Tiers (B4).
- **Perk_Tier**: A level threshold that makes a set of **Alliance_Perk**s
  available for activation. Defined in `data/definitions/alliance_perks.yaml`
  and validated on load. The count of Perk_Tiers is the max Alliance_Level.
- **Alliance_Perk**: A named, activatable buff belonging to exactly one
  **Perk_Category** (shared vision, regen, harvest, combat damage, combat armor)
  with a level gate, a treasury activation cost, and 2–3 upgrade levels. Once
  activated it is recorded in `Alliance_Record["active_perks"]`.
- **Perk_Category**: One of the five effect families
  {`shared_vision`, `shared_regen`, `harvest_boost`, `combat_damage`,
  `combat_armor`}. At most ONE Alliance_Perk may be active per category — no
  same-category stacking (C2).
- **Active_Perk**: An Alliance_Perk currently recorded in
  `Alliance_Record["active_perks"]` and therefore in effect for every member.
- **Alliance_Score**: The composite power score
  `sum over scored roster of (member_level * w_level +
  member_scored_kills_pvp * w_kills_pvp + member_scored_kills_pve * w_kills_pve +
  member_buildings * w_buildings)`, where the weights are BalanceConfig scalars
  and the kill terms are read AFTER lazy decay (see Scored_Kills). PvP kills
  outweigh PvE kills (`w_kills_pvp` default `3.0` > `w_kills_pve` default `1.0`).
  Ranks alliances on the **Leaderboard**.
- **Scored_Kills**: The leaderboard-eligible kill tallies, SPLIT into two
  per-character floats: `db.scored_kills_pvp` (incremented on the
  `_handle_player_defeat` XP-award branch) and `db.scored_kills_pve` (incremented
  on the `_handle_enemy_death` XP-award branch). Both are stored as floats with a
  per-character `db.last_kill_decay_tick` and are subject to **Score_Decay**: on
  any read or increment they are first lazily decayed, then (for an increment)
  bumped. Distinct from the cosmetic `db.kills`, which counts every defeat
  (Friendly_Fire included) and never decays.
- **Score_Decay**: Lazy exponential decay of both Scored_Kills tallies so the
  board reflects recent activity: on read/increment, multiply each tally by
  `balance.alliance_score_decay_factor` (default `0.98`) raised to the number of
  `balance.alliance_score_decay_interval_ticks` elapsed since
  `db.last_kill_decay_tick`, then update `last_kill_decay_tick`. No global sweep.
- **Member_Board**: The within-alliance ranking of a single alliance's members
  by the same per-member Alliance_Score terms. `RankSystem.get_status` supplies
  the ranking basis, but the DISPLAYED columns are the R21.4 set — rank / level /
  scored_kills (PvP+PvE) / online + last-seen — NOT `combat_xp`.
- **Leaderboard**: The cross-alliance ranking of alliances by Alliance_Score,
  truncated to `balance.alliance_leaderboard_top_n` rows for display.
- **Member_Resolver**: The `AllianceSystem` helper that resolves a stored member
  `.id` to a live character object (via `evennia.search_object` /
  `ObjectDB.objects.filter(id=...)`), returning `None` on miss. All derivation
  (level, score, kills, buildings) reads objects through this resolver.
- **Entity_Level**: A player's level, read via the single source of truth
  `world.utils.get_player_level(entity, default=1)`. Used for every level gate.
- **Join_Request**: An INBOUND request to join, created by an outsider via the
  `apply` (alias `request`) verb and stored in
  `Alliance_Record["pending_requests"]`. An Officer-or-higher accepts it (two-
  sided consent), reusing the pending-invite plumbing as a request queue (B2).
- **Open_Join**: The Leader-only boolean `Alliance_Record["open_join"]` (default
  `False`). WHILE it is `True`, an outsider may `join <tag>` WITHOUT an invite or
  request (still subject to the level gate, member cap, and one-alliance
  invariant) (B2).
- **Ignore_List**: The per-`CombatCharacter` `db.alliance_invite_ignore` set of
  blocked inviter ids (or the sentinel `"all"`), set via the `ignore` verb, so a
  player can refuse invites (C17).
- **Alliance_Claim**: An Officer's `claim` action to take leadership once the
  current Leader has been offline longer than
  `balance.alliance_leader_absence_days`, judged on-demand from existing last-seen
  data (no timer) (A6).

## Requirements

### Requirement 1: Single-Writer Alliance Authority

**User Story:** As a game developer, I want exactly one authority to write
alliance state and one predicate to decide "are these two on the same side," so
that no two code paths can drive conflicting membership or a divergent friend/foe
answer.

#### Acceptance Criteria

1. THE Alliance_System SHALL be the only code path that writes an
   Alliance_Record or a Member_Pointer (`db.player_alliance`,
   `db.alliance_rank`).
2. THE Are_Allied predicate SHALL be the only code path that decides whether two
   entities are allied, mirroring the single-authority `is_owner` precedent in
   `world/utils.py`.
3. WHEN `are_allied(a, b)` is called, THE predicate SHALL return `True` only if
   `a` and `b` resolve to two DISTINCT players who both hold the same
   non-`None` `db.player_alliance`; sameness SHALL be decided the same way as
   `is_owner` — if both have non-`None` `.id`, compare ids (equal ⇒ same player
   ⇒ `False`), else fall back to identity (`a is b`) — so two same-PK instances
   after an idmapper flush are treated as the same player.
4. WHEN either argument to Are_Allied has no `db`, a `None`
   `db.player_alliance`, or is the same player as the other, THE predicate SHALL
   return `False` (value-based reads only — never `hasattr` on `db`, and never
   truthiness on `player_alliance`; use `is None` / `==` so `alliance_id == 0`
   can never be mistaken for "no alliance").
5. WHERE Are_Allied is consulted in combat, THE caller SHALL evaluate it against
   the Owning_Players (via `_owning_player`), not the raw combat units.
6. IF the Alliance_Registry singleton is unavailable, THEN Are_Allied SHALL
   return `False` (fail toward "not allied", so a lookup failure never suppresses
   legitimate hostile targeting).
7. THE Are_Allied predicate SHALL return `True` only if the shared `alliance_id`
   still resolves to a live Alliance_Record via the registry; IF the id does not
   resolve (e.g. a stale Member_Pointer left by a disband while a member was
   offline, see R4.4/R16.6), THEN Are_Allied SHALL return `False`, so two
   holders of a dead alliance id are never treated as allies.
8. THE Are_Allied predicate SHALL return `False` (belt-and-braces) for any holder
   that is not a real player character — a `has_account`-False holder, a Sentinel,
   or an entity whose `npc_type` is not `None` — so an NPC base owner can never be
   treated as an ally even if a stray pointer were written (C8).

### Requirement 2: Alliance Founding

**User Story:** As a player of sufficient level, I want to found an alliance with
a name and tag, so that I can lead a group of players on a shared side.

#### Acceptance Criteria

1. WHEN a player who is not already in an alliance and whose Entity_Level is at
   least `balance.alliance_found_min_level` (default `10`) issues the found
   command with a valid name and tag, THE Alliance_System SHALL create a new
   Alliance_Record with a fresh `alliance_id` (from `next_alliance_id`), set the
   founder's Member_Pointer to that id with Alliance_Rank `"leader"`, and publish
   `ALLIANCE_CREATED`. Founding is FREE in v1 (no treasury or resource cost;
   documented asymmetry / squatting risk, R16.11).
2. IF the founding player's Entity_Level is below
   `balance.alliance_found_min_level`, THEN THE Alliance_System SHALL refuse and
   report the required level, and SHALL write nothing.
3. IF the founding player already has a non-`None` `db.player_alliance`, THEN THE
   Alliance_System SHALL refuse (one-alliance-per-player, see R6) and write
   nothing.
4. THE alliance name SHALL be validated per the Name/Tag policy (R19): NFKC-
   normalized ASCII alphanumeric plus single interior spaces (trimmed and
   collapsed), non-empty, Evennia color/markup codes disallowed, not colliding
   (after normalization) with any reserved substring or an existing name; the tag
   SHALL likewise be NFKC-normalized ASCII alphanumeric only, non-empty,
   length-bounded by `balance.alliance_tag_max_len` (default `5`), and unique
   after normalization; IF a collision, empty/blank value, disallowed character,
   markup code, reserved substring, or bound violation occurs, THEN THE
   Alliance_System SHALL refuse and write nothing.
5. WHEN an Alliance_Record is created, THE Alliance_System SHALL initialize
   `treasury` to an empty pool, `active_perks` to empty, `officer_ids` /
   `member_ids` / `pending_invites` / `pending_requests` to empty collections,
   `open_join` to `False`, `withdraw_window` to an empty accumulator, and
   `created_tick` to the current tick.
6. THE founding command SHALL reject any founder that is not a real player
   character (`has_account` True, not a Sentinel, `npc_type` `None`), enforced in
   the single writer (C8).

### Requirement 3: Membership — Invitations

**User Story:** As an officer, I want to invite players and let them accept or
decline, so that membership is consensual and controlled.

#### Acceptance Criteria

1. WHEN an Officer-or-higher member invites a target REAL player (C8) who is not
   in an alliance, has not blocked the inviter via the Ignore_List (R18), and is
   not throttled (R18), THE Alliance_System SHALL record the target's id plus an
   `expiry_tick` (`now + balance.alliance_invite_expiry_days`) in the
   Alliance_Record's `pending_invites` and notify the target.
2. WHEN the invited player accepts AND the alliance is below its member cap
   (R6), THE Alliance_System SHALL move the player's id from `pending_invites`
   into `member_ids`, set their Member_Pointer with Alliance_Rank `"member"`,
   and publish `ALLIANCE_MEMBER_JOINED`.
3. WHEN the invited player declines, THE Alliance_System SHALL remove their id
   from `pending_invites`, change no Member_Pointer, and start a post-decline
   suppression window before the same inviter may re-invite the same target (R18).
4. IF a target already belongs to an alliance, THEN THE Alliance_System SHALL
   refuse the invite and write nothing.
5. IF the accepting player's Entity_Level is below
   `balance.alliance_join_min_level` (default `5`), THEN THE Alliance_System
   SHALL refuse the join and remove the stale invite.
6. THE invite and accept commands SHALL be usable while the inviter/invitee is
   in the lobby (`player_state == LOBBY`), as members of the mutating-lobby verb
   set (R15.2); they SHALL be refused in `SPAWNING` with "finish choosing your
   character first" (C6).
7. WHEN a player successfully joins any alliance, THE Alliance_System SHALL purge
   that player's id from the `pending_invites` AND `pending_requests` of EVERY
   other Alliance_Record, so a stale invite or request can never later re-activate;
   a duplicate invite to a player who already has a pending invite in the same
   alliance SHALL be idempotent (refreshing its `expiry_tick`).
8. IF the referenced Alliance_Record no longer exists (e.g. disbanded between
   invite and accept) OR the accepting player's id is not in its
   `pending_invites` OR the invite's `expiry_tick` has passed, THEN accept SHALL
   refuse and change no Member_Pointer, purging the expired invite.
9. THE accept and decline verbs SHALL accept a TAG or a list-index from the
   invite inbox (R18), not only a raw alliance id.

### Requirement 4: Membership — Leave, Kick, Disband, Transfer, Succession, Claim

**User Story:** As a member or leader, I want to leave, remove others, hand off
leadership, or disband, and I want the alliance to survive a leader who vanishes,
so that the roster can change cleanly over the alliance's life.

#### Acceptance Criteria

1. WHEN any member issues leave AND they are not the Leader, THE Alliance_System
   SHALL remove their id from the Alliance_Record roster, clear their
   Member_Pointer to `None`, start the rejoin cooldown (R18), and publish
   `ALLIANCE_MEMBER_LEFT`.
2. WHEN an Officer-or-higher kicks a member of STRICTLY LOWER Alliance_Rank, THE
   Alliance_System SHALL remove the target from the roster, clear the
   target's Member_Pointer, and start the target's rejoin cooldown (R18).
3. IF a kick targets a member of equal or higher Alliance_Rank, THEN THE
   Alliance_System SHALL refuse and write nothing.
4. WHEN the Leader disbands, THE Alliance_System SHALL even-split any treasury
   across the current roster (R7.5), clear the Member_Pointer of every member
   (roster-wide), destroy the alliance channel (R14.6), delete the
   Alliance_Record, and publish `ALLIANCE_DISBANDED`.
5. WHEN the Leader transfers leadership to an existing member, THE
   Alliance_System SHALL set the target's Alliance_Rank to `"leader"`, demote
   the former Leader to `"officer"`, update `leader_id`, and publish
   `ALLIANCE_RANK_CHANGED`.
6. IF the Leader issues leave without transferring first AND the roster has other
   members, THEN THE Alliance_System SHALL refuse and instruct them to transfer
   or disband; WHERE the Leader is the sole member, leave SHALL be treated as
   disband.
7. WHEN reconciliation runs (R14.5) AND `leader_id` does not resolve to a live
   character whose Member_Pointer places them in this alliance (e.g. the Leader
   deleted their character via chardelete or is permanently absent), THE
   Alliance_System SHALL auto-promote the highest-ranked remaining member (an
   Officer, else the earliest-joined Member) to Leader and publish
   `ALLIANCE_RANK_CHANGED`; IF no member resolves at all, THE Alliance_System
   SHALL even-split any treasury per R7.5 and disband the alliance. Character
   deletion of ANY member SHALL be routed through the Alliance_System as an
   implicit leave, never left as an orphaned pointer. An Officer MAY also proactively
   take leadership via `claim` (R4.8) without waiting for reconciliation.
8. WHEN an Officer issues `claim` AND the current Leader has been offline (by the
   existing last-seen data) longer than `balance.alliance_leader_absence_days`
   (default `7`), THE Alliance_System SHALL, judged on-demand with NO timer,
   promote the claiming Officer to Leader, demote the former Leader to `"officer"`,
   update `leader_id`, and publish `ALLIANCE_RANK_CHANGED`; IF the Leader has NOT
   been absent that long, THEN THE Alliance_System SHALL refuse and report the
   remaining absence time (A6).

### Requirement 5: Roles and Permissions

**User Story:** As a leader, I want a role hierarchy that gates each action, so
that power over the alliance is distributed deliberately.

#### Acceptance Criteria

1. THE Alliance_Rank set SHALL be exactly {Leader, Officer, Member}, ordered
   Leader > Officer > Member.
2. THE Alliance_System SHALL restrict to the Leader ONLY: disband,
   promote/demote, transfer leadership, activate/upgrade perks, the `open` toggle
   (R17), rename/retag (R19), and a capped-override withdraw (R7.7).
3. THE Alliance_System SHALL allow Officer-or-higher: invite, accept Join_Requests
   (R17), kick members of strictly lower rank, withdraw from the Alliance_Treasury
   subject to the Withdrawal_Cap (R7.7), and claim leadership from an absent Leader
   (R4.8).
4. THE Alliance_System SHALL allow any Member: deposit to the treasury, use
   alliance chat (R15.8), and leave.
5. WHEN a member issues an action their Alliance_Rank does not permit, THE
   Alliance_System SHALL refuse, report the required rank, and write nothing.
6. WHEN the Leader promotes a Member to Officer or demotes an Officer to Member,
   THE Alliance_System SHALL update only that member's Alliance_Rank and publish
   `ALLIANCE_RANK_CHANGED`.
7. IF a promotion would raise the number of Officers above
   `balance.alliance_max_officers` (default `3`), THEN THE Alliance_System SHALL
   refuse the promotion and report the officer cap, writing nothing (C4).

### Requirement 6: One-Alliance-Per-Player and Member Cap

**User Story:** As a game designer, I want each player in at most one alliance and
each alliance size-bounded, so that alliances remain distinct, balanced sides.

#### Acceptance Criteria

1. THE Alliance_System SHALL maintain the invariant that a player's
   `db.player_alliance` names at most one alliance at any time.
2. IF any founding, accept, request-accept, open-join, or join path would give a
   player a second alliance, THEN THE Alliance_System SHALL refuse and write
   nothing.
3. WHEN an accept/join would exceed `balance.alliance_max_members` (default
   `10`), THE Alliance_System SHALL refuse and report the alliance is full.
4. THE member count SHALL be the size of the Alliance_Record roster
   (`leader + officers + members`), reconciled against Member_Pointers (R14). The
   member count and cap count the FULL live roster regardless of `player_state`
   (C5).
5. WHEN a member's Member_Pointer and the Alliance_Record roster disagree (e.g.
   after a crash mid-write), THE Alliance_System SHALL treat the Member_Pointer
   as authoritative for that player and repair the roster on next reconciliation.
6. THE `next_alliance_id` counter SHALL initialize to `1` (never `0`) and be
   strictly increasing, mirroring `next_agent_id`; every membership presence
   check SHALL use `is None` / `== alliance_id`, never truthiness, so a
   legitimate `alliance_id == 0` can never arise nor be misread as "no alliance".

### Requirement 7: Shared Treasury

**User Story:** As a member, I want to pool resources into a shared treasury that
funds perks, so that the alliance can invest collectively.

#### Acceptance Criteria

1. WHEN a member deposits resources they possess, THE Alliance_System SHALL
   RE-READ the treasury immediately before the write-back (C9), add them to the
   Alliance_Treasury FIRST (read-modify-reassign: read → mutate a plain copy →
   write back), THEN deduct them from the member's `db.resources` (via
   `deduct_resources`); IF the member deduction fails, THE Alliance_System SHALL
   roll back the treasury add in the same call so no resources are created; on
   success THE Alliance_System SHALL publish `ALLIANCE_TREASURY_DEPOSITED`
   (actor + amounts) to the alliance channel (R7.8).
2. WHEN an Officer-or-higher withdraws resources the treasury holds AND the
   Withdrawal_Cap permits (R7.7), THE Alliance_System SHALL RE-READ the treasury
   immediately before the write-back (C9), subtract them from the
   Alliance_Treasury FIRST, THEN add them to the withdrawer's `db.resources`; IF
   the credit fails, THE Alliance_System SHALL roll back the treasury subtraction;
   on success THE Alliance_System SHALL publish `ALLIANCE_TREASURY_WITHDRAWN`
   (actor + amounts) to the alliance channel (R7.8).
3. THE Alliance_Treasury balance for every resource type SHALL never go negative;
   IF a withdrawal or perk activation would drive any balance below zero, THEN
   THE Alliance_System SHALL refuse the whole operation atomically and write
   nothing.
4. WHEN a deposit is attempted for resources the member does not have, THE
   Alliance_System SHALL refuse and change neither the member nor the treasury.
5. WHEN an alliance disbands (by Leader disband or by no-member succession, R4.7)
   with a non-empty treasury, THE Alliance_System SHALL EVEN-SPLIT the treasury
   across the CURRENT roster — crediting each resolved member's `db.resources`
   via `add_resource` with an equal integer share — and credit any remainder from
   non-even division to the Leader (A1). This REPLACES the former discard rule.
6. FOR any single deposit or withdraw call (including its failure/rollback
   branch), THE total quantity of each resource type across (member `db.resources`
   + Alliance_Treasury) SHALL be conserved — no dupe, no loss. For an even-split
   disband, THE total across (all credited members) SHALL equal the pre-split
   treasury (share*count + remainder).
7. WHEN an Officer withdraws, THE cumulative withdrawn quantity within the current
   `balance.alliance_withdraw_window_ticks` window SHALL NOT exceed
   `balance.alliance_withdraw_cap_per_window`; IF a withdrawal would exceed the
   cap, THEN THE Alliance_System SHALL refuse it and report the remaining
   allowance, UNLESS the actor is the Leader (a Leader withdraw bypasses the cap).
   THE window accumulator (`withdraw_window`) SHALL reset once the window elapses
   (B1).
8. WHENEVER treasury moves (deposit or withdraw), THE Alliance_System SHALL
   publish an audit event carrying the actor and the amounts
   (`ALLIANCE_TREASURY_DEPOSITED` / `ALLIANCE_TREASURY_WITHDRAWN`) and broadcast a
   corresponding system line to the alliance channel (B1, ties to R15.9).

### Requirement 8: Alliance Level Derivation

**User Story:** As a game designer, I want an alliance's level to reflect its
members' aggregate activity, so that active alliances unlock stronger perks.

#### Acceptance Criteria

1. THE Alliance_Level SHALL be DERIVED (never stored authoritatively) by
   `AllianceSystem.compute_alliance_level` from the roster's aggregate activity:
   the SUM of member Entity_Levels (resolved via the Member_Resolver and read
   with `get_player_level`) mapped through `balance.alliance_level_thresholds`
   (the calibrated tier table in design §9). The SUM metric is chosen over an
   average deliberately (a bigger active alliance climbs faster; small alliances
   still reach low tiers via the calibrated table, B4).
2. THE Alliance_Level SHALL NOT be treated as source of truth; it MAY be memoized
   and the memo invalidated on the `LEVEL_CHANGED` event or any roster change,
   but a read SHALL always be able to recompute it from the live roster (no
   contradiction between "derived" and any cache).
3. THE Alliance_Level SHALL be read from the same single source of truth as every
   other level gate — member levels via `world.utils.get_player_level` — so the
   "which level is this" rule cannot drift.
4. IF a member's stored level is non-numeric or unreadable (including an
   unresolvable id), THEN `compute_alliance_level` SHALL treat that member as the
   default level rather than raising (mirroring `get_player_level`'s coercion).
5. THE Alliance_Level SHALL be a non-negative integer, SHALL be monotonic in
   aggregate member activity (more aggregate levels never lowers the tier), and
   SHALL be CAPPED at the number of Perk_Tiers defined in
   `alliance_level_thresholds` / `alliance_perks.yaml` (B4) — aggregate activity
   beyond the top threshold never yields a level above the top tier.

### Requirement 9: Perk Tiers — Unlock and Activation

**User Story:** As a leader, I want alliance level to unlock perk tiers and the
treasury to pay to activate them, so that both collective activity and collective
investment gate our power.

#### Acceptance Criteria

1. THE Alliance_System SHALL apply BOTH gates to every Alliance_Perk: the
   Alliance_Level must meet the perk's `Perk_Tier` threshold (UNLOCK) AND the
   Alliance_Treasury must pay the perk's activation cost (ACTIVATE).
2. WHEN the Leader activates an unlocked perk AND the Alliance_Treasury can pay
   its cost, THE Alliance_System SHALL deduct the cost (R7.3 atomicity), record
   the perk in `active_perks`, and publish `ALLIANCE_PERK_ACTIVATED`.
3. IF the Alliance_Level does not meet the perk's tier threshold, THEN THE
   Alliance_System SHALL refuse activation and report the required level, even if
   the treasury could pay.
4. IF the Alliance_Treasury cannot pay the activation cost, THEN THE
   Alliance_System SHALL refuse and report the shortfall, even if the tier is
   unlocked.
5. WHEN a perk supports upgrade levels, THE Alliance_System SHALL charge the
   next-level cost and require the next-level tier, applying both gates again.
6. WHEN the Alliance_Level later drops below a perk's tier (e.g. members left),
   THE already-activated perk SHALL remain active — activation is GRANDFATHERED
   and never revoked retroactively. This is the settled intended behavior (A2),
   not a backlog item.
7. THE Alliance_System SHALL allow at most ONE Active_Perk per Perk_Category; IF
   activating a perk in a category that already has an Active_Perk (other than a
   next-level upgrade of that same perk) is attempted, THEN THE Alliance_System
   SHALL refuse — there is no same-category stacking (C2).

### Requirement 10: Concrete Perk Effects

**User Story:** As a member, I want activated perks to actually change gameplay,
so that alliance investment produces real, shared benefits.

#### Acceptance Criteria

1. WHERE a "shared vision" perk is active, THE fog-of-war computation for a
   member SHALL union the member's own visible tiles with each allied member's
   own visible-tile set (see R12.1 for the exact mechanism), so allies extend
   each other's visible tiles. THE union SHALL include only allied members whose
   `player_state == PLAYING` (a live coord position only exists while deployed) —
   an offline/lobby ally contributes no vision (C5).
2. WHERE a "shared regen" perk is active, THE Alliance_System SHALL register a
   `(entity)->float` MULTIPLIER provider via
   `RegenSystem.add_modifier_provider` that returns the perk multiplier for
   entities whose Owning_Player is a member, and `1.0` otherwise.
3. WHERE a "harvest" perk is active, THE member active-presence harvest yield in
   `resource_system.process_harvest_tick` (the site that reads
   `extractor_harvest_multiplier`, at the extractor-bonus branch) SHALL be scaled
   by the perk's OWN multiplier value applied ON TOP of the existing
   `extractor_harvest_multiplier` factor; the perk SHALL NEVER reuse or overwrite
   the `extractor_harvest_multiplier` balance key (C1). Agent-driven passive
   extractor production (`resource_system.process_extractor_production`, which
   scales `gather_amount` and does NOT read `extractor_harvest_multiplier`) is
   explicitly OUT of scope for the harvest perk in v1; no other harvest site is in
   scope.
4. WHERE a "combat" perk (damage bonus or damage reduction) is active, THE combat
   aggregation sites SHALL add the perk term as a FLAT ADDITIVE term LIVE for a
   member by looking up `AllianceSystem.perk_multiplier(owning_player, ...)`: a
   damage bonus is added at `CombatEngine._get_attacker_bonus`, and a damage
   reduction is added at `CombatEngine._get_target_armor_reduction`. THE combat
   perk SHALL NOT be applied via `PowerupSystem.apply_timed_effect` or any write
   onto `db.active_powerups`, because that would persist past membership and is
   not consumed by the armor path (violating R10.5 / Property 12).
5. WHEN a player leaves or is removed from an alliance, THE Alliance_System SHALL
   ensure that player no longer receives any Active_Perk effect on the next
   evaluation (perk effects are membership-derived, never copied onto the
   member's own `db`).
6. THE perk effects SHALL NOT touch base ownership: `owner_has_active_hq` and
   `active_hq_owner_ids` remain UNCHANGED, no perk enables building in ally
   territory, and no perk shares an ally's Academy/Lab/Armory (shallow
   integration).

### Requirement 11: Friendly Fire — Hybrid Rule

**User Story:** As a player, I want automated defenses to never shoot my allies
but to still be able to betray an ally by hand, so that alliances are safe from
turret crossfire yet betrayal remains a deliberate, unrewarded choice.

#### Acceptance Criteria

1. WHEN a turret acquires targets in `CombatEngine.process_turrets`, THE
   targeting loop SHALL skip a candidate player who is allied to the turret's
   owner — adding `are_allied(player, owner)` alongside the existing
   `is_owner(player, owner)` continue-guard — so a turret never fires on an ally.
2. WHEN a guard/agent acquires a target in
   `GuardCombatSystem._acquire_target`, THE loop SHALL likewise skip a candidate
   allied to the NPC's owner (add `are_allied` alongside the `is_owner` skip), so
   automated guards never fire on an ally.
3. THE manual attack path (`CombatEngine._prepare_attack`) SHALL NOT be blocked
   for allied targets: a player MAY manually attack an ally or catch one in a
   player-armed bomb blast, the shot lands, and damage is NOT floored.
4. WHEN a player manually damages or kills an ally, THE reward guards SHALL treat
   the allied victim as Friendly_Fire and grant NO XP, evaluating Are_Allied
   against the victim's OWNING PLAYER (never a raw `db.owner`, which is `None`
   for a player):
   (a) in `_handle_player_defeat`, the `own_victim` guard SHALL be extended to
   also match `attacker_owner is not None and are_allied(attacker_owner,
   self._owning_player(victim))`;
   (b) in `_handle_building_destruction`, the code SHALL first resolve
   `attacker_owner = self._owning_player(attacker)` (it does not today) and the
   `own_building` guard SHALL be extended to also match `attacker_owner is not
   None and are_allied(attacker_owner, owner)`.
5. WHEN a player kills an ally, THE kill SHALL NOT increment either of the
   killer's Scored_Kills tallies (`db.scored_kills_pvp` / `db.scored_kills_pve`)
   and SHALL NOT contribute to any alliance's Alliance_Score, even though the
   cosmetic `db.kills` tally may still increment. Scored_Kills SHALL be
   incremented ONLY on a non-friendly XP-reward kill, at the matching reward
   path: `_handle_player_defeat`'s award branch increments `db.scored_kills_pvp`
   (PvP), and `_handle_enemy_death`'s award branch increments
   `db.scored_kills_pve` (PvE); friendly-fire and self-kills increment neither.
   Each increment first applies Score_Decay (glossary) to the tally.
6. THE manual attack target resolution used by players
   (`game_commands._attackables_in_view` / `_resolve_attack_target`) SHALL remain
   UNFILTERED by alliance, so a player can still deliberately target an ally.

### Requirement 12: Shared Vision Integration

**User Story:** As a member, I want allied vision merged into mine without allied
structures being mistaken for enemy discoveries, so that shared sight is helpful
and not misleading.

#### Acceptance Criteria

1. WHEN the shared-vision perk is active, THE member's visible-tile set SHALL be
   the union of the member's own `FogOfWarSystem.get_visible_tiles(member,
   member_buildings)` result with, for each allied member whose
   `player_state == PLAYING` (C5), that ally's own
   `get_visible_tiles(ally, ally_buildings)` result — so each live ally's POSITION
   is drawn at `player_vision_radius` and each ally's BUILDINGS at
   `building_vision_radius`. Allied positions SHALL NOT be passed through the
   building-list argument (wrong radius), and no new required positional
   parameter is assumed on the existing signature.
2. WHEN `FogOfWarSystem.update_discovery` records buildings on visible tiles, THE
   discovery logic SHALL NOT flag an allied member's building as an enemy
   discovery — an allied building is treated like the member's own at the
   enemy-flag check that currently records a building only when `owner is not
   player and owner_name != player_key`.
3. IF the shared-vision perk is not active, THEN a member's vision SHALL be
   exactly the pre-feature per-owner vision (no allied union).
4. THE shared-vision union SHALL be recomputed from live membership each time it
   is evaluated, so a member who leaves immediately stops contributing to and
   receiving LIVE shared vision on the next evaluation. Fog already discovered
   via ally vision (and any enemy-building intel written to `buildings_mem`
   through an ally's circle) persists as normal discovered memory after leaving —
   this residual-memory retention is the settled intended behavior (A3, R16.7),
   NOT scrubbed on leave.

### Requirement 13: Composite-Score Leaderboard and Member Board

**User Story:** As a competitive player, I want alliances ranked by a composite
power score and a within-alliance member board, so that collective and individual
standing are both visible.

#### Acceptance Criteria

1. THE Alliance_Score SHALL be computed as
   `sum over scored roster of (member_level * balance.alliance_score_w_level +
   member_scored_kills_pvp * balance.alliance_score_w_kills_pvp +
   member_scored_kills_pve * balance.alliance_score_w_kills_pve +
   member_buildings * balance.alliance_score_w_buildings)`, where each roster id
   is resolved to a live object via the Member_Resolver (R13.7), member_level is
   `get_player_level`, member_scored_kills_pvp/pve are the DECAYED (Score_Decay)
   `db.scored_kills_pvp` / `db.scored_kills_pve`, and member_buildings is the
   count from `get_buildings()`. PvP kills outweigh PvE kills (default weights
   `3.0` vs `1.0`) and old kills decay over time.
2. THE Leaderboard SHALL rank alliances by descending Alliance_Score with a
   deterministic tiebreak (alliance_id ascending), so the same state always
   produces the same ordering; the display SHALL be truncated to
   `balance.alliance_leaderboard_top_n` rows (R21).
3. THE Member_Board SHALL rank a single alliance's members using the same
   per-member terms; `RankSystem.get_status` supplies the ranking basis, but the
   DISPLAYED columns SHALL be the R21.4 set — rank / level / scored_kills
   (PvP+PvE) / online + last-seen — and SHALL NOT include `combat_xp`.
4. THE kills terms SHALL read the DECAYED Scored_Kills tallies
   (`db.scored_kills_pvp` / `db.scored_kills_pve`), NOT the cosmetic `db.kills`,
   so a betrayal kill on an ally (which grants no Scored_Kills, R11.5) never
   inflates an alliance's standing, and stale kills decay out of the ranking.
5. IF a roster member's stats are unreadable OR its id does not resolve to a live
   object, THEN the leaderboard computation SHALL treat that member's terms as
   zero rather than raising.
6. THE weights `w_level`, `w_kills_pvp`, `w_kills_pve`, `w_buildings`, the decay
   factor/interval, and the leaderboard top-N SHALL be BalanceConfig scalars
   overridable in `data/config/balance.yaml`.
7. THE score/level derivation SHALL score ONLY members whose live Member_Pointer
   still equals this `alliance_id` (reconcile-then-score, or filter each roster id
   by re-reading its pointer), so a ghost member left in a roster by a
   crash-orphaned path — or a member now in a rival alliance — contributes zero
   and never inflates the score. Id→object resolution SHALL use the
   Member_Resolver, returning `None` (scored as zero) on miss.

### Requirement 14: Persistence and Data Model

**User Story:** As a game developer, I want alliance state persisted correctly
under Evennia's attribute model, so that rosters survive restarts and are
rebuildable from member pointers.

#### Acceptance Criteria

1. THE non-derivable alliance state SHALL live in the persistent
   Alliance_Registry `DefaultScript` (`self.persistent=True`,
   `self.db.alliances`, `self.db.next_alliance_id`), and the per-player
   Member_Pointer SHALL live on the `CombatCharacter` (`db.player_alliance`,
   `db.alliance_rank`), seeded in `PLAYER_DEFAULTS`.
2. THE roster SHALL be rebuildable by enumerating Member_Pointers via
   `evennia.utils.search.search_object_attribute(key="player_alliance",
   value=alliance_id)` — NOT a `db_strvalue` ORM filter (a plain
   `db.player_alliance = <int>` pickles into `db_value`, leaving `db_strvalue`
   `None`, so a strvalue filter matches nothing on a real DB).
3. THE treasury, roster collections, `active_perks`, `pending_invites`,
   `pending_requests`, and `withdraw_window` SHALL be mutated with
   read-modify-reassign (read, coalesce `None`→empty, mutate a plain copy, write
   back) because in-place mutation of a `SaverDict`/`SaverSet` is unreliable.
4. THE code SHALL use VALUE-based checks for every `db` read (never `hasattr` on
   `db`), because a `DbHolder` returns `None` for unset attributes so `hasattr`
   is always `True`.
5. THE Alliance_System SHALL reconcile the Alliance_Record roster against
   Member_Pointers on registry load (and on demand), rebuilding any roster whose
   members disagree, treating each Member_Pointer as authoritative for its own
   player. Reconciliation cadence is on-load + on-demand only (no timer, R16.6).
6. THE alliance chat channel SHALL be an Account-level channel keyed by the
   IMMUTABLE `alliance_<id>` with NO player-facing alias (C7), delivering only
   through the `chat` verb (sidestepping the reserved Public/chat/pub channel and
   surviving rename); roster/treasury/perk data stays on the Character/registry —
   account-level DB writes SHALL NOT be performed from a character puppet hook
   (that corrupts the test DB rollback). On disband the channel SHALL be DESTROYED
   (unsubscribe all members, then delete the channel object) (A5).
7. WHEN reconciliation rebuilds a roster, THE Alliance_System SHALL reconstruct
   `leader_id` / `officer_ids` / `member_ids` from the per-player
   `db.alliance_rank` Member_Pointers of the enumerated members; IF zero members
   resolve to rank `"leader"`, THE Alliance_System SHALL invoke succession (R4.7);
   IF multiple resolve to `"leader"`, THE Alliance_System SHALL keep the one
   matching `leader_id` (else the earliest-joined) and demote the rest to
   `"officer"`.
8. THE never-negative treasury guarantee relies on Evennia's single-threaded
   command serialization; to survive a future async path, deposit/withdraw SHALL
   re-read the treasury immediately before the write-back (C9, R7.1/R7.2).
9. THE data model SHALL assume `MAX_NR_CHARACTERS == 1` — multi-character-per-
   account is out of scope; Member_Pointers are per-character while the alliance
   chat subscription and the one-alliance invariant are framed per-account (C9,
   documented in R16.10).

### Requirement 15: Commands and Event Integration

**User Story:** As a player, I want one `alliance` command with verbs and the
right events fired, so that the feature is usable and observable.

#### Acceptance Criteria

1. THE alliance command SHALL be a subcommand router
   (`commands/alliance_commands.py`, extending `GameSubcommandRouter`) exposing
   verbs: `found`, `invite`, `accept`, `decline`, `invites`, `apply` (alias
   `request`), `open`, `join`, `leave`, `kick`, `promote`, `demote`, `transfer`,
   `claim`, `disband`, `deposit`, `withdraw`, `chat`, `info`, `perks`, `activate`,
   `rename`, `retag`, `ignore`, `board`, and `leaderboard`, and be registered in
   `commands/default_cmdsets.py`. THE `join <tag>` verb SHALL route to
   `AllianceSystem.join_open` (open-join without invite/request, R17.4). THE
   `board` verb SHALL render the within-alliance
   Member_Board (`member_board(alliance_id)`); THE `leaderboard` verb SHALL render
   the cross-alliance ranking (`leaderboard()`).
2. THE command SHALL declare per-verb availability and override `at_pre_cmd` to
   parse the verb FIRST: the MUTATING-lobby verbs
   (`found`, `invite`, `accept`, `decline`, `apply`, `join`) SHALL require
   `player_state == LOBBY` and be refused in `SPAWNING` with "finish choosing your
   character first"; the READ-ONLY trio (`info`, `board`, `leaderboard`) SHALL be
   allowed from LOBBY or SPAWNING; all remaining verbs (`deposit`, `withdraw`,
   `kick`, `promote`, `demote`, `transfer`, `disband`, `activate`, `chat`,
   `perks`, `leave`, `open`, `claim`, `rename`, `retag`, `ignore`, `invites`)
   SHALL be refused when issued from the lobby with an "available in-game only"
   message (C6). (A single class-level `available_out_of_game` flag cannot express
   per-verb availability, so the gate is verb-aware.)
3. WHEN any roster-changing, treasury, or rename action succeeds, THE
   Alliance_System SHALL publish the corresponding event (`ALLIANCE_CREATED`,
   `ALLIANCE_MEMBER_JOINED`, `ALLIANCE_MEMBER_LEFT`, `ALLIANCE_DISBANDED`,
   `ALLIANCE_PERK_ACTIVATED`, `ALLIANCE_RANK_CHANGED`, `ALLIANCE_RENAMED`,
   `ALLIANCE_REQUEST_CREATED`, `ALLIANCE_TREASURY_DEPOSITED`,
   `ALLIANCE_TREASURY_WITHDRAWN`) on the `EventBus`.
4. IF publishing an alliance event raises, THEN THE Alliance_System SHALL swallow
   the error (telemetry never breaks a mutation) and still report the mutation as
   successful, mirroring the transition-function precedent.
5. THE Alliance_System MAY subscribe to `LEVEL_CHANGED` and `PLAYER_ELIMINATED`
   solely to invalidate the optional Alliance_Level memo (R8.2); it SHALL NOT
   mutate a Member_Pointer as a side effect of those events. IF no memo is kept,
   THE subscription SHALL be omitted rather than left purposeless.
6. WHEN a verb is invoked by a player not in an alliance (except `found`,
   `accept`, `decline`, `invites`, `apply`, `join`, `open` where noted, `info`,
   `board`, `leaderboard`, `ignore`), THE command SHALL report "you are not in an
   alliance" and change nothing.
7. WHEN promote, demote, transfer, or claim changes a rank or `leader_id`, THE
   Alliance_System SHALL publish `ALLIANCE_RANK_CHANGED` (with alliance_id and the
   affected member) so leadership/rank changes are observable.
8. WHEN a Member uses the `chat` verb, THE message SHALL be delivered only to the
   alliance's members via the Account-level `alliance_<id>` channel (R14.6); THE
   Alliance_System SHALL subscribe an account to that channel on join and
   unsubscribe on leave/kick/disband, so channel membership tracks the roster.
9. WHEN a member join/leave/kick/promote/demote/perk-activate/disband/treasury
   movement occurs, THE Alliance_System SHALL broadcast a system line on the
   alliance channel AND direct-message the specifically affected player, wired off
   the events already published (C12).

### Requirement 16: Known Constraints and Backlog

**User Story:** As a maintainer, I want the alliance feature's deliberate
simplifications and open edges recorded, so that they are tracked rather than
rediscovered.

#### Acceptance Criteria

1. THE integration depth SHALL remain shallow: an ally's HQ does NOT power your
   base, there is no building in ally territory, and no shared use of an ally's
   Academy/Lab/Armory. `owner_has_active_hq` / `active_hq_owner_ids` are
   UNCHANGED. Deepening integration is explicitly out of scope.
2. THE disband-with-treasury behavior SHALL EVEN-SPLIT the pooled resources across
   the current roster with the non-even remainder to the Leader (A1, R7.5). This
   is the settled policy (no longer a discard). REMAINING KNOWN CONSTRAINT: because
   withdraw caps (R7.7) were chosen over a window-split, a Leader can still kick
   everyone then disband to keep the whole split — this is a documented,
   accepted residual risk, not a solved problem.
3. THE perk-downgrade behavior SHALL leave an already-activated perk active even
   if the Alliance_Level later drops below its tier (GRANDFATHERED). This is the
   settled intended behavior (A2), no longer backlog.
4. THE `player_class` and alliance features SHALL remain independent: alliances
   grant no class-specific perk and class remains cosmetic (per the
   player-lifecycle spec R13.5) until a class-effects feature is specified.
5. THE friendly-fire rule SHALL be exactly: automated (turret/guard/agent) fire
   is suppressed against allies; manual player-inflicted fire is permitted but
   grants no XP and no leaderboard credit. No other combat path changes.
6. THE reconciliation of rosters vs Member_Pointers SHALL be best-effort on load
   and on demand (no timer); a real-time transactional guarantee across a crash
   mid-write is out of scope (the Member_Pointer is the tiebreaker, R6.5). An
   indefinitely-offline Leader is repaired by reconcile succession (R4.7) or an
   Officer `claim` (R4.8), NOT a timer. Treasury deposit/withdraw is protected by
   ordered writes + in-call rollback + a pre-write-back re-read (R7.1/R7.2/C9),
   not a cross-object transaction.
7. THE fog-of-war residual: tiles discovered and enemy-building intel recorded
   through an ally's vision circle SHALL persist as ordinary discovered memory
   after a member leaves (A3). This is the settled intended behavior, no longer
   backlog.
8. THE score weights (`w_level=1.0`, `w_kills_pvp=3.0`, `w_kills_pve=1.0`,
   `w_buildings=1.5`), the decay knobs, the perk catalog costs/values, and the
   `alliance_level_thresholds` tier table are all FIRST-GUESS and flagged for live
   balance tuning.
9. THE never-negative treasury guarantee relies on Evennia's single-threaded
   command serialization; a pre-write-back re-read (C9) is added so the guarantee
   survives a future async path, but a full cross-object transaction is out of
   scope.
10. THE membership model assumes `MAX_NR_CHARACTERS == 1`: multi-character-per-
    account is out of scope; Member_Pointers are per-character while the chat
    subscription and one-alliance invariant are framed per-account (C9).
11. THE Alliance_System SHALL make founding FREE and the Alliance_Treasury
    UNCAPPED in v1 (documented asymmetry and squatting risk);
    `balance.alliance_max_officers` (default `3`) SHALL bound promotions (C4). A
    founding cost and a treasury capacity are backlog.

### Requirement 17: Membership — Requests, Open Join, and Outsider Info

**User Story:** As a player looking for an alliance, I want to request to join,
join open alliances directly, and inspect an alliance before joining, so that
recruitment is two-sided and discoverable.

#### Acceptance Criteria

1. WHEN an outsider (not in an alliance) issues `apply <name|tag>` (alias
   `request`), THE Alliance_System SHALL record the outsider's id in the target
   Alliance_Record's `pending_requests` (an INBOUND request queue reusing the
   pending-invite plumbing) and publish `ALLIANCE_REQUEST_CREATED` (B2).
2. WHEN an Officer-or-higher accepts a pending Join_Request AND the alliance is
   below its member cap (R6) AND the requester's Entity_Level meets
   `balance.alliance_join_min_level`, THE Alliance_System SHALL move the
   requester's id from `pending_requests` into `member_ids`, set their
   Member_Pointer with Alliance_Rank `"member"`, and publish
   `ALLIANCE_MEMBER_JOINED` (two-sided consent).
3. WHEN an outsider issues `info <name|tag>`, THE command SHALL render the
   outsider-visible view (name/tag/leader/member-count/level/active-perks, NOT
   treasury) per R21.
4. WHEN the Leader issues `open`, THE Alliance_System SHALL toggle
   `Alliance_Record["open_join"]`; WHILE `open_join` is `True`, an outsider who
   issues `join <tag>` SHALL be admitted WITHOUT an invite or request, still
   subject to the level gate (R3.5), the member cap (R6.3), and the one-alliance
   invariant (R6.2) (B2).
5. THE invite-only path (R3) SHALL continue to work unchanged when `open_join` is
   `False`; `apply`/`request` remains available whether or not the alliance is
   open.
6. THE `apply`/`request` verb SHALL reject any actor that is not a real player
   character (C8) and SHALL be subject to the same throttling (R18) as invites
   where a per-target request cooldown applies.

### Requirement 18: Invite Inbox, Expiry, and Throttling

**User Story:** As a player, I want to review my pending invites and be shielded
from invite spam, so that joining is convenient and abuse is limited.

#### Acceptance Criteria

1. WHEN a player issues `invites`, THE command SHALL list each pending invite's
   alliance name, tag, and id (the invite inbox); accept/decline SHALL accept a
   TAG or a list-index from that inbox, not only a raw id (C10, R3.9).
2. WHEN a player logs in with pending invites, THE Alliance_System SHALL REPLAY
   the pending invites to the player (C10).
3. THE pending invites SHALL EXPIRE after `balance.alliance_invite_expiry_days`
   (default `7`): each pending invite stores an `expiry_tick`, and an expired
   invite SHALL be skipped and purged on read/accept rather than honored (C10,
   R3.8).
4. THE Alliance_System SHALL enforce a per-target invite COOLDOWN
   (`balance.alliance_invite_cooldown_ticks`) and a post-decline SUPPRESSION
   window before the same inviter may re-invite the same target; an invite issued
   inside either window SHALL be refused (C17, R3.3).
5. WHEN a player issues `ignore <tag|all>`, THE Alliance_System SHALL add the
   named inviter (or the `"all"` sentinel) to the player's Ignore_List
   (`db.alliance_invite_ignore`); an invite from an ignored inviter (or any invite
   while `"all"` is set) SHALL be refused (C17).
6. THE Alliance_System SHALL enforce a modest global REJOIN cooldown
   (`balance.alliance_rejoin_cooldown_ticks`) after any leave or kick before the
   same player may join/accept/request into ANY alliance, which also blunts the
   serial-hop-for-fog-intel exploit (C17, R12.4).

### Requirement 19: Name/Tag Policy and Rename

**User Story:** As a leader, I want a clear name/tag policy and the ability to
rename, so that identities are clean, non-impersonating, and correctable.

#### Acceptance Criteria

1. THE alliance NAME SHALL be NFKC-normalized ASCII alphanumeric plus single
   interior spaces (surrounding whitespace trimmed, runs of spaces collapsed);
   THE TAG SHALL be NFKC-normalized ASCII alphanumeric only; Evennia color/markup
   codes SHALL be explicitly DISALLOWED in both (C13).
2. THE Alliance_System SHALL reject a name or tag whose normalized form contains
   any reserved substring from a small denylist (`admin`, `system`, `staff`,
   `public`, `chat`, `pub`) (C13).
3. THE uniqueness check for name and tag SHALL be performed AFTER NFKC
   normalization, so homoglyph impersonation of an existing name/tag is rejected
   (C13).
4. WHEN the Leader issues `rename <new-name>` or `retag <new-tag>` AND the
   rename cooldown (`balance.alliance_rename_cooldown_ticks` since `renamed_tick`)
   has elapsed, THE Alliance_System SHALL re-run the same founding validators
   (R2.4, R19.1–R19.3), update the field, set `renamed_tick`, and publish
   `ALLIANCE_RENAMED`; IF the cooldown has not elapsed, THEN THE Alliance_System
   SHALL refuse and report the remaining time.
5. BECAUSE the alliance channel is keyed by the immutable `alliance_<id>` (R14.6),
   a rename/retag SHALL NOT disturb chat delivery or channel membership.

### Requirement 20: Alliance Tag Visibility

**User Story:** As a player, I want to see alliance tags next to player names, so
that I can tell friend from foe at a glance.

#### Acceptance Criteria

1. THE game SHALL render a player's alliance tag as `[TAG] Name` (for every
   player, friend and foe alike) at the following four sites: the `who` command,
   the look/tile summary, the `score` command, and the map per-tile player
   payload (C11).
2. WHEN `map_data_provider` emits a per-tile player payload (currently
   `{name, linkdead}`), THE payload SHALL additionally carry the player's alliance
   `tag` (or `None` if the player is in no alliance) (C11).
3. THE tag rendered SHALL be the current `Alliance_Record["tag"]` resolved from
   the player's live Member_Pointer; IF the player is in no alliance, THEN no tag
   prefix SHALL be shown.

### Requirement 21: View Contents and Visibility Scoping

**User Story:** As a player, I want each alliance view to show exactly the right
columns to the right audience, so that information is useful without leaking
private state.

#### Acceptance Criteria

1. THE `leaderboard` view SHALL show the top `balance.alliance_leaderboard_top_n`
   rows with columns rank / tag / name / score / level (C15).
2. THE `info` view for a MEMBER SHALL show name / tag / leader / member-count /
   level / active-perks / treasury-balances; the treasury balances SHALL be
   visible to all MEMBERS; pending invites and pending requests SHALL be visible
   to Officer-or-higher ONLY (C15).
3. THE `info <name|tag>` view for an OUTSIDER (R17.3) SHALL show name / tag /
   leader / member-count / level / active-perks, but SHALL NOT show the treasury
   (C15).
4. THE `board` view SHALL show, per member, rank / level / scored_kills (the sum
   or paired PvP+PvE) / online + last-seen, but SHALL NOT show exact coordinates —
   exact-tile reveal stays exclusive to the shared-vision perk (C15).

### Requirement 22: Combat Gating of Friend/Foe-Changing Verbs

**User Story:** As a game designer, I want side-changing verbs blocked mid-combat,
so that a player cannot alliance-hop to dodge a fight (anti-combat-log).

#### Acceptance Criteria

1. WHEN the actor is in combat (the existing `player_in_combat` check that gates
   quit), THE friend/foe-changing verbs `leave`, `transfer`, `disband`, and
   `kick` SHALL be refused with an in-combat message and write nothing (C16).
2. THE treasury verbs `deposit` and `withdraw` SHALL NOT be combat-gated; this is
   a deliberate choice (they do not change sides) and is documented (C16).

### Requirement 23: Administrative Alliance Router

**User Story:** As an administrator, I want privileged alliance operations that
still honor the single-writer invariant, so that staff can intervene without
corrupting state.

#### Acceptance Criteria

1. THE admin surface SHALL be a `CmdAdminAlliance` router in
   `commands/admin_commands.py` mirroring the existing `AdminSubcommandRouter`
   pattern, registered in the admin cmdset, exposing verbs: `inspect`/`list`
   (read), `force-disband`, `force-kick`, `force-transfer`, and `rename` (C14).
2. EVERY admin write verb (`force-disband`, `force-kick`, `force-transfer`,
   `rename`) SHALL route its mutation THROUGH `AllianceSystem` so the single-writer
   invariant (R1.1) holds; no admin verb SHALL touch an Alliance_Record or
   Member_Pointer directly (C14).
3. THE `inspect`/`list` read verbs SHALL surface full alliance state (including
   treasury, pending invites/requests, and `withdraw_window`) for staff, bypassing
   the R21 member/outsider visibility scoping.
4. A `force-disband` SHALL apply the same even-split treasury rule (R7.5) and
   channel destruction (R14.6) as a normal disband.
