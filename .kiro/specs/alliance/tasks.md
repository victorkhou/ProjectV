# Implementation Plan: Alliances

## Overview

This plan builds the alliance feature net-new. It threads a single ally predicate
(`world.utils.are_allied`) and a single-writer authority
(`world/systems/alliance_system.py`) through only the seams the requirements
demand, keeping integration shallow (base ownership untouched). All boxes are
unchecked — nothing here is built yet.

The change spans `world/utils.py` (predicate), `world/systems/alliance_system.py`
(NEW: registry + system + Member_Resolver + throttling + rename + claim), 
`commands/alliance_commands.py` (NEW: verb router with verb-aware lobby gate +
combat gate), `commands/admin_commands.py` (NEW: `CmdAdminAlliance` router),
`commands/default_cmdsets.py` (registration), `typeclasses/characters.py`
(pointers + split decaying scored_kills + invite-ignore), `lifecycle_commands.py`
(chardelete → implicit leave; login invite-replay), `world/definitions.py` +
`data/config/balance.yaml` + `schema_validator.py` (scalars + the nested
thresholds dict), `data/definitions/alliance_perks.yaml` (NEW: perk catalog),
`world/systems/combat_engine.py` + `world/systems/guard_combat_system.py`
(targeting + XP seams + split decaying scored_kills on both reward paths + flat
combat perk terms), `world/coordinate/fog_of_war.py` (shared vision, PLAYING-only),
`world/coordinate/map_data_provider.py` + `commands/game_commands.py` (alliance-tag
visibility), `world/systems/regen_system.py` / `resource_system.py` (perk hooks),
`world/event_bus.py` + `server/conf/game_init.py` (events + wiring), and the test
files.

## Tasks

- [ ] 1. Data model + persistence foundation
  - [ ] 1.1 Add `player_alliance=None`, `alliance_rank=None`,
    `scored_kills_pvp=0.0`, `scored_kills_pve=0.0`, `last_kill_decay_tick=0`,
    `alliance_invite_ignore=None` to `PLAYER_DEFAULTS` in
    `typeclasses/characters.py`; back-fill in `ensure_attributes` (value-based).
    - _Requirements: 14.1, 14.4_
  - [ ] 1.2 Add `ALLIANCE_RANKS`, `ALLIANCE_RANK_ORDER`,
    `ALLIANCE_PERK_CATEGORIES`, and `ALLIANCE_NAME_DENYLIST` to
    `world/constants.py`.
    - _Requirements: 5.1, 9.7, 19.2_
  - [ ] 1.3 Implement `AllianceRegistry(DefaultScript)` (persistent;
    `db.alliances`, `db.next_alliance_id` initialized to `1`, coerce-`None`→`1`
    on read; `by_tag` normalized-tag lookup) in
    `world/systems/alliance_system.py`.
    - _Requirements: 14.1, 6.6_
  - [ ] 1.4 Roster enumeration helper using `search_object_attribute(key=
    "player_alliance", value=alliance_id)` (NOT a `db_strvalue` filter);
    returns `[]` if unavailable. Add the `_resolve_member` Member_Resolver
    (id→object via `evennia.search_object`/`ObjectDB`, `None` on miss),
    `_live_members` (pointer==id filter), and `_is_real_player`
    (`has_account`/Sentinel/`npc_type`) guard.
    - _Requirements: 14.2, 13.7, 1.8_

- [ ] 2. The single ally predicate (`world/utils.py`)
  - [ ] 2.1 Implement `are_allied(a, b)` alongside `is_owner`: two distinct REAL
    players (sameness via `.id` like `is_owner`; real-player guard per C8)
    sharing a non-None `db.player_alliance` whose id resolves to a live
    Alliance_Record; value-based reads (`is None`/`==`, never truthiness);
    fail-toward-False on missing db / same player / non-real-player / unavailable
    registry / unresolved id.
    - _Requirements: 1.2, 1.3, 1.4, 1.6, 1.7, 1.8_

- [ ] 3. AllianceSystem — membership authority (single writer)
  - [ ] 3.1 `AllianceSystem(BaseSystem)`; `found` with level gate
    (`alliance_found_min_level`), free founding, real-player guard, name/tag
    policy (NFKC normalization, ASCII-alnum, single interior spaces for name,
    markup rejection, reserved-substring denylist, post-normalization
    uniqueness), initial record init (incl. `pending_requests`, `open_join`,
    `withdraw_window`, `renamed_tick`); sets founder pointer to Leader; creates
    the `alliance_<id>` channel.
    - _Requirements: 1.1, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 19.1, 19.2, 19.3_
  - [ ] 3.2 `invite` / `accept` / `decline`: pending-invite bookkeeping with
    `{id, expiry_tick}`; cap + one-alliance + join-level gates on accept; purge
    the joiner's id from ALL other alliances' `pending_invites` AND
    `pending_requests` on join; refuse accept if the record was disbanded, the id
    is not in `pending_invites`, or the invite expired; accept/decline resolve a
    TAG or inbox index.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 6.2, 6.3_
  - [ ] 3.3 `apply`/`request` (INBOUND `pending_requests`) + `accept_request`
    (Officer+ two-sided consent) + publish `ALLIANCE_REQUEST_CREATED`; `open`
    Leader toggle of `open_join`; `join_open` (open-join without invite, still
    level/cap/one-alliance gated); outsider `info <name|tag>`.
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_
  - [ ] 3.4 `leave` / `kick` / `disband` / `transfer`: strictly-lower-rank kick
    guard; leader-must-transfer-or-disband; sole-leader-leave = disband; clear
    pointers roster-wide on disband; even-split treasury on disband (task 4.3);
    destroy the `alliance_<id>` channel on disband; transfer publishes
    `ALLIANCE_RANK_CHANGED`; start rejoin cooldown on leave/kick.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 14.6, 18.6_
  - [ ] 3.5 `promote` / `demote` and the role-permission matrix (Leader-only vs
    Officer+ vs Member); enforce `alliance_max_officers` on promote; refuse with
    required-rank message on violation; publish `ALLIANCE_RANK_CHANGED` on rank
    change.
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - [ ] 3.6 One-alliance-per-player + member-cap invariants enforced before every
    write (all presence checks use `is None`/`==`); `reconcile` treating
    Member_Pointer as authoritative, rebuilding roster + `leader_id` from
    `db.alliance_rank`; cadence on-load + on-demand only.
    - _Requirements: 6.1, 6.4, 6.5, 6.6, 14.5, 14.7_
  - [ ] 3.7 Succession + chardelete + claim: `on_character_deleted` routes deletion
    through the single writer as an implicit leave; reconcile promotes the senior
    remaining member when `leader_id` does not resolve, or even-splits + disbands
    when no member resolves; `claim` (Officer) promotes when the Leader has been
    offline > `alliance_leader_absence_days` (on-demand, no timer), else refuses;
    publishes `ALLIANCE_RANK_CHANGED`.
    - _Requirements: 4.7, 4.8, 14.7_
  - [ ] 3.8 `rename` / `retag`: re-run the founding validators (R2.4, R19.1–R19.3),
    enforce `alliance_rename_cooldown_ticks` since `renamed_tick`, update the
    field, set `renamed_tick`, publish `ALLIANCE_RENAMED`; channel keyed by
    immutable id is undisturbed.
    - _Requirements: 19.4, 19.5_
  - [ ] 3.9 All mutations use read-modify-reassign for treasury/roster/
    active_perks/pending_invites/pending_requests/withdraw_window; value-based
    reads throughout.
    - _Requirements: 14.3, 14.4_

- [ ] 4. Shared treasury
  - [ ] 4.1 `deposit`: RE-READ treasury before write-back (C9); add to `treasury`
    FIRST (read-modify-reassign) then deduct from member `db.resources`
    (`deduct_resources`); roll back the treasury add if the member deduction
    fails; refuse if member lacks resources; publish `ALLIANCE_TREASURY_DEPOSITED`
    + channel line.
    - _Requirements: 7.1, 7.4, 7.6, 7.8, 14.8_
  - [ ] 4.2 `withdraw`: Officer+ only; enforce the per-window Withdrawal_Cap
    (`alliance_withdraw_cap_per_window` over `alliance_withdraw_window_ticks`) with
    a Leader override; RE-READ treasury before write-back (C9); subtract from
    treasury FIRST then credit the withdrawer, rollback on credit failure;
    never-negative + atomic refusal; reset the `withdraw_window` accumulator when
    the window elapses; publish `ALLIANCE_TREASURY_WITHDRAWN` + channel line.
    - _Requirements: 7.2, 7.3, 7.6, 7.7, 7.8, 14.8_
  - [ ] 4.3 Even-split treasury on disband (and no-member succession): credit each
    resolved member an equal integer share via `add_resource`, remainder to the
    Leader; total credited = pre-split treasury. Replaces the former discard rule.
    Document the residual kick-then-disband constraint.
    - _Requirements: 7.5, 7.6, 16.2_

- [ ] 5. Alliance level derivation
  - [ ] 5.1 `compute_alliance_level` from the SUM of member `get_player_level`
    (resolved via `_resolve_member`) mapped through the calibrated
    `balance.alliance_level_thresholds` table; not authoritative (MAY be memoized
    + invalidated, always recomputable on read); non-numeric/unresolved coercion;
    monotonic; CAPPED at the number of Perk_Tiers.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 6. Perk tiers — unlock + activation
  - [ ] 6.1 Author `data/definitions/alliance_perks.yaml` (the v1 catalog: one perk
    per category — shared_vision, shared_regen ~1.25x, harvest_boost ~+50% ON TOP
    of `extractor_harvest_multiplier`, combat_damage +2 flat, combat_armor +3 flat
    — each with a tier gate, a treasury cost naming the six resource types, and
    2–3 upgrade levels) + load/validate; flag values for live tuning.
    - _Requirements: 9.1, 10.3, 10.4_
  - [ ] 6.2 `available_perks` / `activate_perk`: apply BOTH gates (level tier
    unlock + treasury cost); atomic treasury deduction; enforce at most ONE perk
    per Perk_Category (upgrade of the same perk only); record in `active_perks`;
    publish `ALLIANCE_PERK_ACTIVATED`; upgrade levels re-apply both gates.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7_
  - [ ] 6.3 Activated perk stays active if level later drops (GRANDFATHERED, no
    retro-revoke); settled intended behavior.
    - _Requirements: 9.6, 16.3_

- [ ] 7. Concrete perk effects (membership-derived hooks)
  - [ ] 7.1 Shared vision: add a shared `AllianceSystem.shared_visible_tiles`
    helper that unions the member's own `FogOfWarSystem.get_visible_tiles` result
    with a per-ally `get_visible_tiles` result for allies whose
    `player_state == PLAYING` only (positions at player radius, buildings at
    building radius); recomputed from live membership; no allied positions passed
    via the building-list arg; `get_visible_tiles` signature unchanged. Invoke the
    helper at ALL THREE `get_visible_tiles` call sites so shared vision cannot
    drift: `procedural_map_renderer.py:149` (ASCII map), `map_data_provider.py:64`
    (web map-data payload), and `game_commands.py:597`/`_update_fog_and_render`
    (look).
    - _Requirements: 10.1, 12.1, 12.3, 12.4_
  - [ ] 7.2 Fog `update_discovery`: do NOT flag an allied member's building as an
    enemy discovery (treat allied like own at the `owner is not player` check);
    apply this ally-suppression at all THREE `update_discovery` call sites
    (renderer, map-data provider, look path) so it cannot drift; document residual
    discovered-memory persistence after leave (settled).
    - _Requirements: 12.2, 16.7_
  - [ ] 7.3 Shared regen: register a `(entity)->float` MULTIPLIER provider via
    `RegenSystem.add_modifier_provider` returning the perk multiplier for members,
    `1.0` otherwise.
    - _Requirements: 10.2, 10.5_
  - [ ] 7.4 Harvest perk: scale member active-presence yield at
    `resource_system.process_harvest_tick` (the extractor-bonus branch that reads
    `extractor_harvest_multiplier`) by the perk's OWN multiplier applied ON TOP of
    the existing `extractor_harvest_multiplier` factor (never reusing that key);
    agent-driven `process_extractor_production` (uses `gather_amount`) is OUT of
    scope in v1.
    - _Requirements: 10.3_
  - [ ] 7.5 Combat perk: add the FLAT `damage_bonus` term at `_get_attacker_bonus`
    and the FLAT `damage_reduction` term at `_get_target_armor_reduction`, both
    LIVE via `perk_multiplier(_owning_player(entity), ...)`; NEVER via
    `PowerupSystem.apply_timed_effect`/`db.active_powerups` (would persist past
    leave and reduction would be un-consumed); effect gone on next evaluation
    after leave.
    - _Requirements: 10.4, 10.5_
  - [ ] 7.6 Assert perks never touch base ownership (`owner_has_active_hq` /
    `active_hq_owner_ids` unchanged; no ally-territory build / shared structures).
    - _Requirements: 10.6, 16.1_

- [ ] 8. Friendly fire — hybrid rule (combat seams)
  - [ ] 8.1 `process_turrets` (`combat_engine.py`): add `or are_allied(player,
    owner)` to the target-loop continue-guard so turrets skip allies.
    - _Requirements: 11.1_
  - [ ] 8.2 `guard_combat_system._acquire_target`: add `or are_allied(player,
    owner)` to the `is_owner` continue-guard so guards/agents skip allies.
    - _Requirements: 11.2_
  - [ ] 8.3 Extend the XP guards against Owning_Players: in `_handle_player_defeat`
    match `are_allied(attacker_owner, self._owning_player(victim))` (NOT raw
    `victim.db.owner`, which is `None` for a player); in
    `_handle_building_destruction` first resolve `attacker_owner =
    self._owning_player(attacker)` then match `are_allied(attacker_owner, owner)`
    — so a manual hit/kill on an ally (player or building) grants no XP.
    - _Requirements: 11.4, 1.5_
  - [ ] 8.4 Increment the SPLIT decaying tallies on the Owning_Player on the
    matching non-friendly reward branch: `_handle_player_defeat` →
    `db.scored_kills_pvp`, `_handle_enemy_death` → `db.scored_kills_pve`; each
    increment first applies Score_Decay (`factor ** elapsed_intervals`) and updates
    `last_kill_decay_tick`; leave cosmetic `db.kills` incrementing for all kills;
    leave `_prepare_attack` and player target resolution unfiltered.
    - _Requirements: 11.3, 11.5, 11.6_

- [ ] 9. Leaderboard + member board
  - [ ] 9.1 `_decayed_kills` helper (lazy decay of `scored_kills_pvp` /
    `scored_kills_pve` to the evaluation tick); `alliance_score` = per-member
    `level*w_level + decayed_pvp*w_kills_pvp + decayed_pve*w_kills_pve +
    buildings*w_buildings` (via `_resolve_member` → `get_player_level`, the split
    tallies, `get_buildings()`); score only members whose live pointer ==
    alliance_id; zero-out unreadable/unresolved members.
    - _Requirements: 13.1, 13.4, 13.5, 13.7_
  - [ ] 9.2 `leaderboard` (desc score, asc-id tiebreak, deterministic, truncated to
    `alliance_leaderboard_top_n`); `member_board` reusing `RankSystem.get_status`
    rows.
    - _Requirements: 13.2, 13.3_
  - [ ] 9.3 Add the split score weight + decay scalars
    (`alliance_score_w_kills_pvp`, `alliance_score_w_kills_pve`, and the decay
    knobs) as NET-NEW fields to BalanceConfig + `balance.yaml` + validator
    (int/float partition). (No single `alliance_score_w_kills` exists to remove —
    alliance is net-new.)
    - _Requirements: 13.6_

- [ ] 10. Commands + events + wiring
  - [ ] 10.1 `CmdAlliance(GameSubcommandRouter)` in
    `commands/alliance_commands.py` with all verbs (`found`, `invite`, `accept`,
    `decline`, `invites`, `apply`/`request`, `open`, `join`, `leave`, `kick`,
    `promote`, `demote`, `transfer`, `claim`, `disband`, `deposit`, `withdraw`,
    `chat`, `info`, `perks`, `activate`, `rename`, `retag`, `ignore`, `board`,
    `leaderboard`); the `join <tag>` handler routes to `AllianceSystem.join_open`
    (R17.4); register in `commands/default_cmdsets.py`.
    - _Requirements: 15.1, 15.6, 17.4_
  - [ ] 10.2 Implement the verb-aware lobby gate: `MUTATING_LOBBY_VERBS`
    (LOBBY-only, refused in SPAWNING) + `READONLY_OOC_VERBS` (LOBBY or SPAWNING) +
    overridden `at_pre_cmd` that parses the verb first and refuses all other verbs
    from the lobby.
    - _Requirements: 15.2, 3.6_
  - [ ] 10.3 Combat gate the side-changing verbs (`leave`, `transfer`, `disband`,
    `kick`) via the existing `player_in_combat` check; leave `deposit`/`withdraw`
    NOT gated (documented).
    - _Requirements: 22.1, 22.2_
  - [ ] 10.4 Add all alliance events to `world/event_bus.py` (`ALLIANCE_CREATED`,
    `ALLIANCE_MEMBER_JOINED`, `ALLIANCE_MEMBER_LEFT`, `ALLIANCE_DISBANDED`,
    `ALLIANCE_PERK_ACTIVATED`, `ALLIANCE_RANK_CHANGED`, `ALLIANCE_RENAMED`,
    `ALLIANCE_REQUEST_CREATED`, `ALLIANCE_TREASURY_DEPOSITED`,
    `ALLIANCE_TREASURY_WITHDRAWN`); publish on each mutation with a
    swallow-on-error wrapper.
    - _Requirements: 15.3, 15.4, 15.7_
  - [ ] 10.5 Change notifications (C12): on join/leave/kick/promote/demote/
    perk-activate/disband/treasury-move, broadcast a system line on the
    `alliance_<id>` channel AND direct-message the affected player, wired off the
    published events.
    - _Requirements: 15.9_
  - [ ] 10.6 Optionally subscribe to `LEVEL_CHANGED` / `PLAYER_ELIMINATED` ONLY to
    invalidate the alliance-level memo (never mutate a pointer); omit the
    subscription if no memo is kept.
    - _Requirements: 15.5, 8.2_
  - [ ] 10.7 Construct `AllianceSystem`, ensure `AllianceRegistry` exists
    (idempotent, `next_alliance_id=1` if unset), register perk hook adapters
    (regen provider, combat perk lookups at the two aggregation sites, harvest
    scaling), add `"alliance_system"` to `game_systems` in
    `server/conf/game_init.py`; invoke `replay_invites` on login.
    - _Requirements: 1.1, 10.2, 18.2_
  - [ ] 10.8 Add the genuine balance scalars (`alliance_found_min_level`,
    `alliance_join_min_level`, `alliance_max_members`, `alliance_max_officers`,
    `alliance_tag_max_len`, `alliance_leader_absence_days`,
    `alliance_invite_expiry_days`, `alliance_invite_cooldown_ticks`,
    `alliance_rejoin_cooldown_ticks`, `alliance_rename_cooldown_ticks`,
    `alliance_withdraw_cap_per_window`, `alliance_withdraw_window_ticks`,
    `alliance_leaderboard_top_n`, and the score/decay floats) to BalanceConfig +
    `balance.yaml` + int/float validator; add `alliance_level_thresholds` as a
    nested int-keyed dict via the `special` set in `_build_balance` (int-key
    coercion) + a dedicated dict-validation clause in `validate_balance` (NOT the
    scalar path); populate the calibrated 5-tier table.
    - _Requirements: 2.1, 3.5, 6.3, 5.7, 2.4, 4.8, 8.1, 8.5, 13.6, 18.3, 18.4, 18.6, 19.4, 7.7_
  - [ ] 10.9 Alliance chat: Account-level channel keyed by the immutable
    `alliance_<id>` with no alias (not a character puppet hook); `chat` verb
    delivers to members; subscribe account on join, unsubscribe on
    leave/kick/disband; DESTROY the channel on disband; roster/treasury/perk data
    stays on Character/registry.
    - _Requirements: 14.6, 15.8, 5.4_
  - [ ] 10.10 Route chardelete of any member through `AllianceSystem`
    (`on_character_deleted`) in `lifecycle_commands.py` so a deleted member is an
    implicit leave, never an orphaned pointer.
    - _Requirements: 4.7_
  - [ ] 10.11 Invite inbox + throttling + ignore: `invites` verb (inbox listing),
    login replay, per-target invite cooldown + post-decline suppression, `ignore
    <tag|all>` Ignore_List, global rejoin cooldown after leave/kick.
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

- [ ] 11. Alliance-tag visibility (R20)
  - [ ] 11.1 `AllianceSystem.tag_for(player)` helper; render `[TAG] Name` in the
    `who` and `score` commands and the look/tile summary in
    `commands/game_commands.py` (friend and foe).
    - _Requirements: 20.1, 20.3_
  - [ ] 11.2 Add the alliance `tag` (or `None`) to the per-tile player payload in
    `world/coordinate/map_data_provider.py` (currently `{name, linkdead}`).
    - _Requirements: 20.2, 20.3_

- [ ] 12. Administrative alliance router (R23 / C14)
  - [ ] 12.1 `CmdAdminAlliance(AdminSubcommandRouter)` in
    `commands/admin_commands.py` with `inspect`/`list` (read full state bypassing
    R21 scoping), `force-disband`, `force-kick`, `force-transfer`, `rename`;
    register in the admin cmdset.
    - _Requirements: 23.1, 23.3_
  - [ ] 12.2 Route every admin write verb THROUGH `AllianceSystem` (single-writer
    preserved); `force-disband` reuses the even-split + channel-destroy path.
    - _Requirements: 23.2, 23.4_

- [ ] 13. Tests
  - [ ] 13.1 `world/tests/test_are_allied.py` — the predicate truth table incl.
    same-`.id` instances, unresolved/dead alliance id, registry unavailable, and
    the real-player guard (`has_account`-False / Sentinel / `npc_type`-set).
    - _Requirements: 1.3, 1.4, 1.6, 1.7, 1.8_
  - [ ] 13.2 `world/tests/test_alliance_system.py` — founding (free; name/tag
    policy)/membership/roles + officer cap/invariants/treasury (conservation +
    rollback + pre-write re-read + withdraw cap + even-split disband)/level (SUM +
    cap)/perk double-gate + one-per-category + grandfather/apply+open-join/invite
    inbox+expiry+throttle+ignore+rejoin/rename+cooldown/reconciliation + leader_id
    rebuild + succession + claim + invite/request purge.
    - _Requirements: 2.1, 3.1, 3.9, 4.1, 4.7, 4.8, 5.2, 5.7, 6.1, 7.5, 7.6, 7.7, 8.1, 8.5, 9.1, 9.7, 14.5, 14.7, 17.1, 17.4, 18.1, 18.3, 18.4, 19.4_
  - [ ] 13.3 `world/tests/test_alliance_combat_seams.py` — turret/guard skip
    allies; allied player-kill and allied building-raze grant no XP + no
    Scored_Kills; NON-friendly player kill bumps `scored_kills_pvp`, enemy-NPC kill
    bumps `scored_kills_pve`; Score_Decay reduces a stale tally; manual attack
    still lands; cosmetic kills unchanged.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_
  - [ ] 13.4 `world/tests/test_alliance_perks.py` — per-ally vision union
    (PLAYING-only) + no enemy-flagging; regen/harvest (ON TOP of
    `extractor_harvest_multiplier`)/combat FLAT perk terms for members only;
    combat reduction applied at `_get_target_armor_reduction`; effect gone after
    leave with no `db.active_powerups` residue; one-per-category refusal.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 12.1, 12.2_
  - [ ] 13.5 `commands/tests/test_alliance_commands.py` — verb routing (all verbs),
    verb-aware lobby gate (mutating-lobby verbs LOBBY-only/refused in SPAWNING,
    read-only trio from LOBBY/SPAWNING, others refused from lobby), combat gate
    (leave/transfer/disband/kick refused mid-combat, deposit/withdraw allowed),
    permission gating, `board` vs `leaderboard` vs member/outsider `info`, chat
    scoped to members, not-in-alliance messaging.
    - _Requirements: 15.1, 15.2, 15.6, 15.8, 21.1, 21.2, 21.3, 21.4, 22.1, 22.2_
  - [ ] 13.6 `commands/tests/test_admin_routers.py` — `CmdAdminAlliance`
    inspect/list read; force verbs route through `AllianceSystem`; force-disband
    even-splits + destroys channel.
    - _Requirements: 23.1, 23.2, 23.4_
  - [ ] 13.7 Tag-visibility tests — `who`/`score`/tile summary render `[TAG] Name`;
    `map_data_provider` payload carries `tag` (or `None`) for friend and foe.
    - _Requirements: 20.1, 20.2, 20.3_
  - [ ] 13.8 `tests/test_live_boot_smoke.py` additions — real registry persist
    (`next_alliance_id` starts at 1) + roster rebuild via
    `search_object_attribute`; end-to-end two-char flow through disband with
    even-split credited + channel destroyed; channel survives rename; leaderboard
    produces the EXACT composite score (PvP-weighted, post-decay at a fixed tick)
    for known member stats (guards a broken Member_Resolver).
    - _Requirements: 13.1, 13.2, 14.1, 14.2, 14.5, 14.6, 7.5_

- [ ] 14. Known constraints (documented, tracked, mostly no-code)
  - [ ] 14.1 Assert shallow integration in a test: no alliance code path reads or
    writes `owner_has_active_hq` / `active_hq_owner_ids`.
    - _Requirements: 16.1, 16.5_
  - [ ] 14.2 Document the settled behaviors: even-split disband (+ residual
    kick-then-disband constraint), grandfathered perks, kept fog residual-intel,
    free founding + uncapped treasury + officer cap, `MAX_NR_CHARACTERS==1`
    assumption, and the first-guess/tuning flags on weights/decay/perk-catalog/tier
    table.
    - _Requirements: 14.9, 16.2, 16.3, 16.7, 16.8, 16.10, 16.11_
  - [ ] 14.3 Document alliance/class independence (class stays cosmetic) and
    best-effort reconciliation (Member_Pointer is tiebreaker; no timer; absentee
    leader handled by reconcile succession or Officer claim; treasury protected by
    ordered writes + pre-write re-read + rollback, not a cross-object transaction).
    No code change.
    - _Requirements: 16.4, 16.6, 16.9_
