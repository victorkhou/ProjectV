# Requirements Document

## Introduction

The player lifecycle feature introduces a persisted state machine that governs a
character's journey from connection to in-world play and back: choosing a class
and spawn point (staging), waiting in a lobby, deploying into the field, and the
handling of quits, deaths, and dropped connections. Before this feature, a login
auto-puppeted the character straight into the world and a death respawned it in
place instantly. That gave no room for character preparation, allowed
combat-logging (quitting mid-fight to escape), and let a just-spawned or
just-logged-in character be attacked before the player had control.

The feature adds four persisted dwell states — **SPAWNING → LOBBY → PLAYING →
LINKDEAD** — with a single-writer transition function, a numbered "wizard" UI for
class and spawn selection, a lobby menu (deploy / manage account / disconnect), a
two-level `quit` (in-game quit retreats to the lobby; lobby quit disconnects), an
anti-combat-log rule (you cannot leave the field while your combat timer runs),
and a linkdead grace window (a dropped connection lingers as a live combat target
for a short time). The whole flow is behind a single feature flag
(`LOBBY_FLOW_ENABLED`) so it can be reverted in one line; when the flag is off,
the legacy instant-entry / instant-respawn behavior is preserved unchanged.

This document is a **retroactive** specification: the feature is already
implemented and shipped (flag ON by default) across commits `5496bae`..`7a20b37`
on branch `feat/player-lifecycle`. Requirements below describe the built,
tested behavior. Requirements 11–13 capture known edge cases and
intentionally-incomplete surfaces that are NOT yet fully resolved.

## Glossary

- **Player_State**: The persisted lifecycle state of a character, stored in
  `db.player_state`. One of `PLAYER_STATES` = {`"spawning"`, `"lobby"`,
  `"playing"`, `"linkdead"`}, or `None` for a never-routed (brand-new or legacy)
  character.
- **Lifecycle_Module**: `world/player_lifecycle.py` — the framework-free module
  that owns `db.player_state`. Exposes `transition` (the single writer),
  `route_on_login`, `record_death`, `begin_linkdead`, `is_linkdead_expired`,
  `expire_linkdead`, `enter_game`, `to_lobby`, `finish_spawning`, `get_state`,
  `state_label`.
- **Transition_Function**: `Lifecycle_Module.transition(player, new_state, *,
  reason="", event_bus=None) -> bool` — the ONLY code path that writes
  `db.player_state`. Validates the move against `PLAYER_STATE_TRANSITIONS`.
- **Transition_Table**: `PLAYER_STATE_TRANSITIONS` in `world/constants.py`,
  mapping each state to the set of states reachable from it.
- **Login_Router**: `Lifecycle_Module.route_on_login(player)` — reads the
  persisted state and returns the state a connecting character should resume in.
- **Staging**: Being in the SPAWNING or LOBBY state — connected and puppeted, but
  not yet deployed into the game world (out-of-character, OOC).
- **Spawning_Wizard**: The numbered, one-step-at-a-time UI (in
  `commands/lifecycle_commands.py`) for choosing a class (step 1/2) and a spawn
  point (step 2/2) while SPAWNING.
- **Lobby_Menu**: The numbered staging-area menu shown while LOBBY: `1` Enter the
  game, `2` Change password, `3` Delete character, `0` Quit.
- **Spawn_Resolver**: `world/spawn_resolver.py` — resolves a spawn choice
  (`hq`/`death`/`random`) to a concrete `(planet, x, y)`, with fallbacks.
- **Spawn_Choice**: The player's selected spawn point, persisted in
  `db.pending_spawn_choice` while SPAWNING; consumed on deploy.
- **Combat_Timer**: `db.combat_timer_expires`, a tick-based deadline set by
  `on_combat_action`. A player is "in combat" while it is strictly in the future
  (`world.combat_timer.player_in_combat`). Duration `COMBAT_TIMER_DURATION = 60`.
- **Linkdead_Grace**: The wall-clock window (`db.linkdead_until`, default
  `balance.linkdead_grace_seconds = 30.0`) during which a dropped-connection
  character lingers in the world as a live combat target.
- **Clean_Quit_Marker**: The transient `ndb._clean_quit` flag set by `CmdQuit`
  before it disconnects, used by `at_post_unpuppet` to tell a clean quit
  (→ LOBBY) from a dropped connection (→ LINKDEAD).
- **Lobby_Flow_Enabled**: `world.lobby_flow.lobby_flow_enabled()` — reads
  `settings.LOBBY_FLOW_ENABLED` (default `False` when settings unreadable, e.g.
  in the stubbed test env). The master switch for the entire feature.
- **CombatCharacter**: The player character typeclass (`typeclasses/characters.py`)
  that carries the lifecycle attributes and implements the login/disconnect hooks.
- **Present**: A player that can be hit or seen by turret/guard targeting, per
  `world.utils.player_is_present`. PLAYING and LINKDEAD characters are present;
  SPAWNING and LOBBY characters are not.

## Requirements

### Requirement 1: Single-Writer State Machine

**User Story:** As a game developer, I want exactly one function to write
`db.player_state` and validate every move, so that no two code paths can drive an
illegal or conflicting state transition.

#### Acceptance Criteria

1. THE Transition_Function SHALL be the only code path that assigns
   `db.player_state`.
2. WHEN `transition` is called with a `new_state` not in `PLAYER_STATES`, THE
   Transition_Function SHALL reject the move (return `False`), write nothing, and
   log a warning.
3. WHEN the character's current state is `None`, THE Transition_Function SHALL
   allow a move to any state in `PLAYER_STATES` (the Login_Router promoting a
   fresh/legacy character).
4. WHEN the character's current state is not `None`, THE Transition_Function SHALL
   allow the move only if `new_state` is in `PLAYER_STATE_TRANSITIONS[current]`,
   and otherwise SHALL reject it (return `False`), write nothing, and log a
   warning.
5. WHEN `new_state` equals the current state, THE Transition_Function SHALL treat
   it as an idempotent no-op: return `True` without re-writing or re-publishing.
6. WHEN a move succeeds, THE Transition_Function SHALL write `db.player_state` and
   THEN publish a `PLAYER_STATE_CHANGED` event carrying `player`, `old_state`,
   `new_state`, and `reason`.
7. IF publishing `PLAYER_STATE_CHANGED` raises, THEN THE Transition_Function SHALL
   swallow the error (telemetry must never break a transition) and still report
   the transition as successful.
8. THE Transition_Table SHALL be: SPAWNING → {LOBBY, SPAWNING}; LOBBY → {PLAYING,
   LOBBY}; PLAYING → {LOBBY, SPAWNING, LINKDEAD}; LINKDEAD → {PLAYING, SPAWNING,
   LOBBY}.

### Requirement 2: Login Routing

**User Story:** As a player, I want logging in to resume me in the right state,
so that a new character is prepared, an in-progress selection continues, and a
reconnect drops me back into play.

#### Acceptance Criteria

1. WHEN a character with `player_state == None` logs in, THE Login_Router SHALL
   transition it to SPAWNING (reason `"login_new"`) and return SPAWNING.
2. WHEN a character in SPAWNING or LOBBY logs in, THE Login_Router SHALL leave the
   state unchanged and return it (resume selection / resume in lobby).
3. WHEN a character in PLAYING (e.g. a server crash left it PLAYING) or LINKDEAD
   logs in, THE Login_Router SHALL clear the Linkdead_Grace deadline and
   transition to PLAYING (reason `"reconnect"`), returning PLAYING.
4. THE Login_Router SHALL only be invoked when Lobby_Flow_Enabled is true;
   otherwise login SHALL follow the legacy path (normal puppet, no routing).

### Requirement 3: Post-Login Puppet Hook

**User Story:** As a player, I want the correct screen when I connect — the
staging wizard/menu when I'm not deployed, or the world when I am — instead of
always dumping me into the map.

#### Acceptance Criteria

1. THE post-login logic (attribute migration, overworld positioning, lifecycle
   routing, login event) SHALL run in `CombatCharacter.at_post_puppet` (Evennia
   provides no `Character.at_post_login` hook).
2. WHEN `at_post_puppet` runs under a test harness (`settings.TEST_ENVIRONMENT`
   truthy), THE hook SHALL defer to the parent puppet and skip all custom
   side-effects (so the harness's per-test DB rollback is not corrupted).
3. WHEN login routes to SPAWNING or LOBBY (staging), THE hook SHALL suppress the
   default "You become X" message and auto-look, and instead present the
   Spawning_Wizard or Lobby_Menu and a "You take control of X" acknowledgement.
4. WHEN login resolves to PLAYING (or the flow is disabled), THE hook SHALL defer
   to the parent puppet (emit "You become X" and look at the current tile).
5. WHEN routing to SPAWNING, THE hook SHALL stow the character out of the world
   (de-index from the coordinate index and null its location) before presenting
   the wizard, so it cannot be attacked while the player is choosing.
6. IF lifecycle routing raises, THEN the hook SHALL log at debug level and fall
   through as if the flow were disabled (routing must never block login).

### Requirement 4: Spawning Wizard — Class and Spawn Selection

**User Story:** As a player preparing to deploy, I want a guided numbered menu to
pick my class and spawn point one step at a time, so that I don't have to know
free-text command syntax.

#### Acceptance Criteria

1. WHILE a character is SPAWNING and has no `player_class`, THE Spawning_Wizard
   SHALL present "Step 1/2 — choose your class" as a numbered list of the defined
   classes.
2. WHILE a character is SPAWNING and has a `player_class` but no
   `pending_spawn_choice`, THE Spawning_Wizard SHALL present "Step 2/2 — choose
   your spawn point" as a numbered list (`Headquarters`, `Place of death`,
   `Random location`).
3. WHEN a player types a bare number (via `CmdSelect`, bound to digit keys 0–9),
   THE Spawning_Wizard SHALL apply it to the current step and immediately present
   the next step.
4. WHEN a player types a number outside the valid range for a step, THE
   Spawning_Wizard SHALL report the valid range and re-present that step.
5. THE `class` command SHALL also accept a class by name, key, or unambiguous
   prefix; THE `spawn` command SHALL also accept a spawn option by unambiguous
   prefix.
6. IF no class definitions are loaded, THEN THE Spawning_Wizard SHALL assign a
   default class label ("Recruit") and fall through to the spawn step (the flow
   must never dead-end).
7. WHEN both a class and a spawn choice are set, THE Spawning_Wizard SHALL advance
   the character SPAWNING → LOBBY (via `finish_spawning`) and present the
   Lobby_Menu.
8. WHEN `class` or `spawn` is used outside the SPAWNING state, THE command SHALL
   refuse with a message and change nothing.
9. THE `spawn` selection SHALL persist `pending_spawn_choice` only; the actual
   relocation SHALL be deferred to deploy (so a destroyed HQ or a new death is
   reflected when resolved fresh).

### Requirement 5: Lobby Menu and Deployment

**User Story:** As a player in the staging area, I want a menu to deploy into the
game, manage my account, or disconnect, so that the lobby is my
connected-but-not-deployed hub.

#### Acceptance Criteria

1. WHILE a character is LOBBY, THE Lobby_Menu SHALL offer: `1` Enter the game,
   `2` Change password, `3` Delete character, `0` Quit (disconnect).
2. WHEN the player selects `1` (or types `deploy`/`enter`), THE system SHALL
   deploy the character into the game (LOBBY → PLAYING).
3. WHEN the player selects `2`, THE system SHALL print the `password <old> =
   <new>` command to type (leaning on the stock Evennia command) rather than
   running it.
4. WHEN the player selects `3`, THE system SHALL print the `chardelete <name>`
   command to type, along with the account's character names, rather than running
   it.
5. WHEN the player selects `0`, THE system SHALL route to `quit`, forwarding the
   invoking session so the disconnect targets a real session.
6. WHEN the player selects any other value, THE system SHALL re-present the
   Lobby_Menu.
7. WHEN deploying, THE system SHALL apply the spawn choice, reset
   `db.combat_timer_expires` and `db.combat_lockout_tick` to 0 (so a player who
   died/quit mid-fight does not re-enter "in combat"), clear the
   Clean_Quit_Marker, transition to PLAYING, and show the world (look).
8. WHEN `deploy`/`enter` is used while SPAWNING, THE system SHALL refuse with a
   hint to choose a class and spawn point first; WHEN used while already PLAYING,
   THE system SHALL report "already in the game".

### Requirement 6: Spawn Location Resolution

**User Story:** As a player, I want my spawn choice honored — HQ, place of death,
or a random open location — with sensible fallbacks so I never deploy nowhere or
on top of a structure.

#### Acceptance Criteria

1. WHEN the spawn choice is `hq` and the player has an HQ with coordinates, THE
   Spawn_Resolver SHALL return the HQ's `(planet, x, y)`.
2. WHEN the spawn choice is `death` and a recorded death tile exists in bounds,
   THE Spawn_Resolver SHALL return the death tile's `(planet, x, y)` (which may be
   on a different planet than the current one).
3. WHEN the chosen option is unavailable (no HQ, never died, or out of bounds) OR
   the choice is `random`, THE Spawn_Resolver SHALL return a random open tile —
   NOT the fixed planet spawn.
4. WHEN sampling a random tile AND buildings exist on the planet, THE
   Spawn_Resolver SHALL first attempt to find a tile at least
   `RANDOM_SPAWN_MIN_BUILDING_DISTANCE` (20, Chebyshev) from every building; IF no
   such tile is found within the attempt budget, THEN it SHALL relax to any
   in-bounds tile (best-effort, not a hard guarantee).
5. IF random sampling fails entirely, THEN THE Spawn_Resolver SHALL fall back to
   the planet's fixed spawn point as a last resort; IF nothing resolves at all,
   THEN it SHALL return `None` and the caller SHALL leave the player where they
   are.
6. WHEN placing a deployed character, THE system SHALL nudge them to the nearest
   building-free tile (kept in bounds) so they never deploy on top of a building.

### Requirement 7: Death and Respawn

**User Story:** As a player who is eliminated, I want to be clearly told and sent
back through the full spawning wizard, so that a death is a meaningful setback and
I re-pick my class and spawn point.

#### Acceptance Criteria

1. WHEN a PLAYING or LINKDEAD player's HP reaches 0, THE system SHALL record the
   death tile (`db.death_x/death_y/death_planet`) and transition the player to
   SPAWNING (reason `"death"`).
2. WHEN recording a death, THE system SHALL clear `db.player_class` and
   `db.pending_spawn_choice`, so the Spawning_Wizard restarts at step 1 (class).
3. WHEN a player is routed to SPAWNING by death, THE system SHALL emit an
   elimination notice and the redeploy prompt (not route them back silently).
4. IF death-tile coordinates cannot be coerced to integers, THEN THE system SHALL
   store `death_x/death_y` as `None` (keeping the planet) rather than raise.
5. WHEN a player is SPAWNING (post-death or otherwise), THE player SHALL NOT be
   Present (cannot be targeted by turrets, guards, bombs, or melee).

### Requirement 8: Two-Level Quit

**User Story:** As a player, I want `quit` in the game to retreat me to the
staging area (staying connected) and `quit` from the staging area to disconnect,
so that leaving the field and leaving the game are distinct actions.

#### Acceptance Criteria

1. WHEN a PLAYING player issues `quit` AND Lobby_Flow_Enabled is true, THE system
   SHALL transition PLAYING → LOBBY, stow the character out of the world, present
   the Lobby_Menu, and keep the session connected (no disconnect).
2. WHEN a LOBBY or SPAWNING player issues `quit` (or the flow is disabled), THE
   system SHALL disconnect the session (the legacy quit behavior).
3. WHEN quitting from staging, THE system SHALL set the Clean_Quit_Marker on every
   puppet before disconnecting.
4. IF `CmdQuit` is invoked without a bound session (e.g. routed from a lobby menu
   selection), THEN THE system SHALL fall back to one of the account's own
   sessions so the disconnect succeeds instead of crashing on a `None` session.
5. IF quit routing raises, THEN THE system SHALL log at debug level and fall
   through to the disconnect path (routing must never block quit).

### Requirement 9: Anti-Combat-Log

**User Story:** As a player, I want opponents unable to escape a fight by quitting
or pulling their connection, so that combat can't be dodged by logging off.

#### Acceptance Criteria

1. WHEN a PLAYING player in combat (Combat_Timer strictly in the future) issues
   `quit`, THE system SHALL refuse the field-to-staging retreat, tell them to wait
   for their combat timer, and neither retreat nor disconnect.
2. THE "in combat" check SHALL be the single shared predicate
   `world.combat_timer.player_in_combat`, used by both the quit gate and the
   movement/enter/leave gates.
3. IF the current-tick lookup fails while checking combat state, THEN
   `player_in_combat` SHALL err toward reporting in-combat (block), so a transient
   failure cannot open an escape.
4. WHEN a PLAYING player's connection is dropped WITHOUT a clean quit, THE system
   SHALL transition PLAYING → LINKDEAD with a grace deadline and SHALL NOT stow
   the character (it lingers in the world as a live combat target).
5. WHILE a character is LINKDEAD, THE character SHALL be Present to turret and
   guard targeting even though it holds no session.

### Requirement 10: Linkdead Grace and Expiry

**User Story:** As a game designer, I want a dropped connection to linger briefly
and then be cleaned up, so that a brief network blip resumes seamlessly but an
abandoned character doesn't sit in the world forever.

#### Acceptance Criteria

1. WHEN a PLAYING player drops uncleanly, THE system SHALL set `db.linkdead_until
   = monotonic_now + grace_seconds` and transition to LINKDEAD.
2. THE grace window SHALL be read from `balance.linkdead_grace_seconds` (default
   `1800.0` — 30 minutes; see R13.1), falling back to `1800.0` when the registry
   is unavailable.
3. THE tick loop SHALL check lingering LINKDEAD characters and, when
   `is_linkdead_expired` is true, transition LINKDEAD → LOBBY and remove the
   character from the world (stow / de-index).
4. IF a LINKDEAD character's grace deadline is corrupt (non-numeric), THEN
   `is_linkdead_expired` SHALL treat it as expired so it cannot wedge in the
   world indefinitely.
5. WHEN a LINKDEAD player reconnects before expiry, THE Login_Router SHALL clear
   the deadline and resume them in PLAYING.
6. WHEN a LINKDEAD player is killed during grace, THE system SHALL route them
   through death (→ SPAWNING), not through grace-expiry (→ LOBBY).
7. THE enumeration of LINKDEAD characters (for expiry and the `who` table) SHALL
   query `player_state` via `search_object_attribute` on the pickled `db_value`
   (a `db_strvalue` ORM filter matches nothing for this attribute) so the grace
   timer is not effectively infinite.

### Requirement 11: Feature Flag and Legacy Compatibility

**User Story:** As an operator, I want the entire flow behind one switch, so that
I can revert to legacy instant-entry behavior in a single line if needed.

#### Acceptance Criteria

1. THE entire flow (login routing, the in-game gate, death/disconnect rerouting)
   SHALL be gated by `Lobby_Flow_Enabled`.
2. WHEN Lobby_Flow_Enabled is false, login SHALL puppet the character straight
   into the world, world commands SHALL not be gated, and death SHALL respawn in
   place (legacy behavior).
3. WHEN a character has `player_state == None`, THE `require_in_game` gate SHALL
   treat it as in-game (so a legacy character or a disabled flow is never blocked).
4. THE default value of `settings.LOBBY_FLOW_ENABLED` SHALL be `True`;
   `lobby_flow_enabled()` SHALL default to `False` only when settings are
   unreadable (the Django-free stubbed test env).

### Requirement 12: Account Session Model Assumptions (Known Constraint)

**User Story:** As a game developer, I want the login/lobby model's dependency on
Evennia's session settings documented, so that a future config change doesn't
silently break the staging flow.

#### Acceptance Criteria

1. THE staging model SHALL assume Evennia auto-puppets a single character on login
   (`AUTO_PUPPET_ON_LOGIN=True`, `MULTISESSION_MODE=0`, `MAX_NR_CHARACTERS=1`), so
   that `at_post_puppet` fires and drops the player into the
   Lobby_Menu/Spawning_Wizard rather than an OOC character-select screen. These
   are pinned EXPLICITLY in `settings.py` (with an explanatory comment) so a
   future edit can't silently break the flow, and asserted by a live-boot test.
2. THE multi-puppet handling in `CmdQuit` SHALL block the whole quit ATOMICALLY
   when any PLAYING puppet is in combat: NO puppet is retreated unless ALL are
   clear. RESOLVED: `_retreat_playing_puppets_to_lobby` was made two-pass (check
   all puppets for combat first, retreat only if none are). The prior single-pass
   loop retreated puppets earlier in iteration order before a later in-combat
   puppet aborted the rest — an anti-combat-log hole (retreat your safe puppets
   while one is stuck fighting) that could only manifest if multi-character play
   were enabled. Covered by a live-boot test with multiple puppets.
3. WHERE authentication is concerned, a connection SHALL always require explicit
   username + password; auto-puppet only skips the `ic <name>` character-select
   step, never authentication.
4. WHEN a NEW session connects (a new webclient tab or a fresh client), THE
   system SHALL present the connect/login screen and require explicit credentials
   — it SHALL NOT auto-authenticate from a shared browser-session cookie. This
   closes the reported defect where opening a new session silently logged in as
   the last-played account and (with `MULTISESSION_MODE=0`) USURPED the character
   already playing on another session. RESOLVED by disabling both auto-login
   cookie writers: `SharedLoginMiddleware` is removed from `MIDDLEWARE` (the
   website→webclient share), and the webclient protocols' `at_login`
   cookie-write is neutralized via a portal startup monkeypatch in
   `server/conf/portal_services_plugins.py` (the webclient→webclient persistence).
5. WHEN an already-live session reconnects (e.g. a page reload of a currently
   connected tab), THE system SHALL still resume that session — the
   explicit-login requirement applies only to brand-new sessions, not to the
   reconnect of a live one.

### Requirement 13: Known Edge Cases and Incomplete Surfaces (Backlog)

**User Story:** As a maintainer, I want the lifecycle's rough edges recorded, so
that they are tracked rather than rediscovered.

#### Acceptance Criteria

1. THE linkdead grace and the Combat_Timer SHALL be reconciled so that a player
   who pulls the plug mid-fight remains a targetable LINKDEAD character for at
   least as long as their combat timer would otherwise run. RESOLVED:
   `linkdead_grace_seconds` is set to `1800.0` (30 minutes), far above the ~60s
   combat timer, so a dropped body outlives any combat timer regardless of the
   tick-vs-wall-clock difference. (Trade-off: an accidentally-disconnected
   character stays targetable in the world for up to 30 minutes before being
   swept to the lobby.)
2. THE `finish_spawning` guard SHALL require BOTH `player_class` and
   `pending_spawn_choice`. RESOLVED: the guard now rejects the SPAWNING → LOBBY
   move unless both are set, so a direct caller cannot advance to LOBBY with no
   spawn choice (which `apply_spawn_choice` would misread as a quit-in-place at
   possibly-default coords). The wizard command path always sets both first, so
   normal deployment is unaffected.
3. THE reconnect-vs-expiry race SHALL have this INTENDED behavior: if grace
   expiry runs before reconnect, the character is already LOBBY (its linkdead
   body was swept away + stowed), and reconnect resumes it in LOBBY — it
   re-deploys fresh rather than silently popping back into the world at stale
   coords. With the 30-minute grace this race is rare, and landing in the lobby
   after being gone that long is correct. (Locked with a unit test.)
4. THE crash-resume path SHALL have this INTENDED behavior: a server crash leaves
   the character persisted PLAYING; login resumes it in place (PLAYING) at its
   persisted coords/HP, so the player picks up where they were rather than being
   force-restaged on every crash. Treated identically to a linkdead reconnect
   (both are "return to play"). (Locked with a unit test.)
5. THE `player_class` selection SHALL remain cosmetic (selection + label only, no
   mechanical effect) until a class-effects feature is specified; the SPAWNING
   gate uses it only as the "selection complete" signal.
