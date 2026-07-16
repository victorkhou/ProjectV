# Implementation Plan: Player Lifecycle

## Overview

This is a **retroactive** plan: tasks 1–9 describe the shipped implementation
(branch `feat/player-lifecycle`, commits `5496bae`..`7a20b37`) and are checked
as done. Tasks 10–12 are the real, open backlog — the known edge cases and
incomplete surfaces captured in Requirements 12–13, which are NOT yet resolved.

The core change spans `world/player_lifecycle.py` (new FSM),
`world/lobby_flow.py` (flag), `commands/lifecycle_commands.py` (staging UI +
deploy), `world/spawn_resolver.py` (spawn resolution), `typeclasses/characters.py`
+ `typeclasses/accounts.py` (hooks), `world/combat_timer.py` (in-combat
predicate), `world/utils.py` (presence), `typeclasses/scripts.py` (grace expiry),
`world/constants.py` (states + table), and `server/conf/settings.py` (flag on).

## Tasks

- [x] 1. Build the single-writer state machine (`world/player_lifecycle.py`)
  - [x] 1.1 Define states + Transition_Table in `world/constants.py`
    - `PLAYER_STATE_SPAWNING/LOBBY/PLAYING/LINKDEAD`, `PLAYER_STATES`,
      `PLAYER_STATE_LABELS`, `PLAYER_STATE_TRANSITIONS`
    - _Requirements: 1.8_
  - [x] 1.2 Implement `transition` as the sole writer with validation + event
    - Reject unknown states and illegal edges; allow `None`→any and idempotent
      no-ops; publish `PLAYER_STATE_CHANGED`; swallow publish errors
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_
  - [x] 1.3 Implement `get_state` / `state_label` reads
    - _Requirements: 1.1_
  - [x] 1.4 Keep the module framework-free (no Evennia imports; bus injectable)
    - _Requirements: 1.1_

- [x] 2. Login routing (`route_on_login`)
  - [x] 2.1 Route None→SPAWNING, staging→resume, PLAYING/LINKDEAD→reconnect
    - Clear the linkdead deadline on reconnect
    - _Requirements: 2.1, 2.2, 2.3_
  - [x] 2.2 Gate routing behind `lobby_flow_enabled()`
    - _Requirements: 2.4, 11.1_

- [x] 3. Character + account hooks (`typeclasses/characters.py`, `accounts.py`)
  - [x] 3.1 Move login logic to `at_post_puppet` (not the nonexistent
    `Character.at_post_login`)
    - _Requirements: 3.1_
  - [x] 3.2 Add the `settings.TEST_ENVIRONMENT` guard to defer to the parent
    puppet under the test harness
    - _Requirements: 3.2_
  - [x] 3.3 `_route_lifecycle_on_login`: suppress "You become X" for staging,
    stow on SPAWNING, present wizard/menu; defer to parent for PLAYING
    - _Requirements: 3.3, 3.4, 3.5, 3.6_
  - [x] 3.4 Seed lifecycle fields in `PLAYER_DEFAULTS`; `ensure_attributes`
    backfill
    - _Requirements: 11.3_
  - [x] 3.5 Move channel auto-subscribe to `Account.at_post_login`; savepoint-wrap
    - _Requirements: 3.2_

- [x] 4. Spawning wizard (`commands/lifecycle_commands.py`)
  - [x] 4.1 `CmdClass` / `CmdSpawn`: numbered menus + name/prefix selection,
    SPAWNING-only guard
    - _Requirements: 4.1, 4.2, 4.5, 4.8_
  - [x] 4.2 `CmdSelect` bound to digits 0–9: route a bare number to the current
    step; out-of-range reprompt
    - _Requirements: 4.3, 4.4_
  - [x] 4.3 `present_spawning_step` one-step-at-a-time driver; no-class-data →
    default "Recruit" fall-through
    - _Requirements: 4.6, 4.7_
  - [x] 4.4 Persist `pending_spawn_choice`; defer relocation to deploy
    - _Requirements: 4.9_
  - [x] 4.5 Shared `announce_spawning` used by both login and death paths
    - _Requirements: 4.1, 7.3_

- [x] 5. Lobby menu + deploy (`commands/lifecycle_commands.py`)
  - [x] 5.1 `announce_lobby` 4-option menu; `CmdSelect._select_lobby` routing
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6_
  - [x] 5.2 `deploy_from_lobby`: apply spawn, reset combat state, clear clean-quit
    marker, enter, look
    - _Requirements: 5.2, 5.7, 5.8_
  - [x] 5.3 Forward the invoking session on the lobby `0`→quit path
    - _Requirements: 5.5, 8.4_
  - [x] 5.4 `apply_spawn_choice`: choice-set → resolve; no-choice → deploy in
    place at last coords (fixes "re-enter goes random")
    - _Requirements: 5.7_
  - [x] 5.5 Route `enter` (CmdEnter) to `deploy_from_lobby` when staging
    - _Requirements: 5.2_

- [x] 6. Spawn resolution (`world/spawn_resolver.py`)
  - [x] 6.1 `resolve`: HQ / death / random with fallbacks; HQ-miss → random (not
    planet spawn); death carries its own planet
    - _Requirements: 6.1, 6.2, 6.3, 6.5_
  - [x] 6.2 Min-building-distance sampling (`RANDOM_SPAWN_MIN_BUILDING_DISTANCE`)
    with bounds-only relaxation
    - _Requirements: 6.4_
  - [x] 6.3 `_relocate` uses `nearest_free_tile` so deploy never lands on a
    building
    - _Requirements: 6.6_

- [x] 7. Death + respawn
  - [x] 7.1 `record_death` stores death tile, clears class + spawn choice, routes
    to SPAWNING
    - _Requirements: 7.1, 7.2, 7.4_
  - [x] 7.2 Emit elimination notice + redeploy prompt on death (not silent)
    - _Requirements: 7.3_
  - [x] 7.3 SPAWNING characters are not Present (`player_is_present`)
    - _Requirements: 7.5_

- [x] 8. Two-level quit + anti-combat-log
  - [x] 8.1 `CmdQuit`: PLAYING quit → retreat to LOBBY + stow + menu, stay
    connected; staging quit → disconnect
    - _Requirements: 8.1, 8.2_
  - [x] 8.2 Clean-quit marker on all puppets before disconnect; None-session
    fallback
    - _Requirements: 8.3, 8.4, 8.5_
  - [x] 8.3 `player_in_combat` shared predicate; quit gate blocks the retreat in
    combat
    - _Requirements: 9.1, 9.2, 9.3_
  - [x] 8.4 Unclean drop → LINKDEAD, not stowed, Present as a target
    - _Requirements: 9.4, 9.5_

- [x] 9. Linkdead grace + expiry + flag
  - [x] 9.1 `begin_linkdead` / `clear_linkdead` / `is_linkdead_expired` /
    `expire_linkdead`; corrupt-deadline → expired
    - _Requirements: 10.1, 10.2, 10.4, 10.5, 10.6_
  - [x] 9.2 Tick-loop expiry step; enumerate via `search_object_attribute` (H1
    regression: `db_strvalue` filter matched nothing)
    - _Requirements: 10.3, 10.7_
  - [x] 9.3 `LOBBY_FLOW_ENABLED` flag; `require_in_game` opt-out for None/PLAYING;
    flip flag to True by default
    - _Requirements: 11.1, 11.2, 11.3, 11.4_
  - [x] 9.4 Full test coverage: unit (FSM, commands, resolver, timer) + live-boot
    smoke (real Evennia + real DB)
    - _Requirements: all of 1–11_

- [x] 10. Reconcile linkdead grace with the combat timer (R13.1)
  - Resolved by raising `linkdead_grace_seconds` from 30.0 to `1800.0` (30 min),
    far above the ~60s combat timer, so a mid-fight disconnect leaves the body
    targetable well past any combat timer — the tick-vs-wall-clock mismatch no
    longer opens an escape window.
  - Updated all three sources: `data/config/balance.yaml` (runtime value),
    `world/definitions.py` (dataclass default), and
    `CombatCharacter._linkdead_grace_seconds` (hardcoded fallback); corrected the
    stale "tuned >= combat lockout" comments.
  - Trade-off accepted: an accidentally-disconnected character stays targetable
    for up to 30 minutes before being swept to the lobby.
  - _Requirements: 13.1_

- [x] 11. Resolve the smaller lifecycle edge cases (R13.2–13.4)
  - [x] 11.1 Tighten `finish_spawning` to require `pending_spawn_choice` in
    addition to `player_class`; add unit tests for the no-spawn-choice reject and
    the both-set advance. Updated the FSM/live-boot full-walk tests to set both.
    - _Requirements: 13.2_
  - [x] 11.2 Lock the intended reconnect-vs-expiry behavior (grace expired first →
    resume in LOBBY, re-deploy fresh) with a unit test.
    - _Requirements: 13.3_
  - [x] 11.3 Lock the intended crash-resume behavior (persisted PLAYING → resume
    in place) with a unit test.
    - _Requirements: 13.4_

- [x] 12. Document/verify the account-session assumptions (R12) and class scope
  (R13.5)
  - [x] 12.1 Pin `AUTO_PUPPET_ON_LOGIN=True`, `MULTISESSION_MODE=0`,
    `MAX_NR_CHARACTERS=1` explicitly in `settings.py` (with a comment explaining
    the lifecycle dependency) so a future edit can't silently break the flow; add
    a live-boot test asserting they hold.
    - _Requirements: 12.1, 12.3_
  - [x] 12.2 Make `_retreat_playing_puppets_to_lobby` block the whole quit
    atomically (two-pass) — fixing a latent anti-combat-log hole where a puppet
    earlier in iteration order was retreated before a later in-combat puppet
    aborted; add a multi-puppet live-boot test.
    - _Requirements: 12.2_
  - [x] 12.3 `player_class` intentionally remains cosmetic (selection + label
    only); tracked here for a future class-effects feature. No code change.
    - _Requirements: 13.5_

- [x] 13. Require an explicit login on every new session (R12.4–12.5)
  - Fix the reported defect: opening a new webclient session auto-logged-in as
    the last-played account (no connect screen) and, with `MULTISESSION_MODE=0`,
    usurped the character already playing on another session.
  - [x] 13.1 Remove `SharedLoginMiddleware` from `MIDDLEWARE` in `settings.py`
    (kills the website→webclient shared-login cookie write).
    - _Requirements: 12.4_
  - [x] 13.2 Neutralize the webclient protocols' own `at_login` cookie write via a
    portal startup monkeypatch in `server/conf/portal_services_plugins.py` (kills
    the webclient→webclient persistence; done as a monkeypatch to avoid the
    settings-path circular import the AJAX protocol class body triggers).
    - _Requirements: 12.4, 12.5_
