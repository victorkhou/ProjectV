"""
Player-lifecycle commands — the spawning and lobby flow.

Command-driven front end to the ``world.player_lifecycle`` state machine.
The character stays puppeted throughout; ``db.player_state`` gates what the
player may do. While SPAWNING or LOBBY the game commands refuse via the
shared :func:`require_in_game` guard.

    SPAWNING:  ``class <name>``  — pick a class
               ``spawn <where>`` — pick a spawn location; once BOTH are
                                   chosen, advance to LOBBY
    LOBBY:     ``enter``         — enter the game world (→ PLAYING)
               ``quit``          — leave (handled by Evennia's quit)

Wiring these into a live cmdset + disabling the behavioral gate is done at the
composition root behind the ``LOBBY_FLOW_ENABLED`` flag.
"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand

from world import player_lifecycle as pl
from world.constants import (
    PLAYER_STATE_LOBBY,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_SPAWNING,
)
from world.utils import get_system as _get_system

logger = logging.getLogger("mygame.lifecycle")


# ------------------------------------------------------------------ #
#  Shared helpers
# ------------------------------------------------------------------ #

def require_in_game(caller) -> bool:
    """Return True if *caller* is PLAYING; else message and return False.

    The single guard the game commands use to refuse actions while a player is
    still in the spawning/lobby flow. A character with no lifecycle state yet
    (``None`` — feature not enabled, or a legacy character) is treated as
    in-game, so nothing changes for players until the flow is switched on.
    """
    state = pl.get_state(caller)
    if state is None or state == PLAYER_STATE_PLAYING:
        return True
    if state == PLAYER_STATE_SPAWNING:
        caller.msg(
            "You are still preparing to deploy. Choose a |wclass|n and a "
            "|wspawn|n point, then type |wenter|n. (See |whelp spawning|n.)"
        )
    else:  # LOBBY
        caller.msg("You are in the staging area. Type |wenter|n to deploy.")
    return False


def _class_choices(caller):
    """Return the list of selectable ClassDefs (may be empty)."""
    registry = _get_system(caller, "registry")
    classes = getattr(registry, "classes", None) if registry else None
    if not classes:
        return []
    return sorted(classes.values(), key=lambda c: c.key)


# ------------------------------------------------------------------ #
#  Spawning menu — numbered, presented one step at a time
# ------------------------------------------------------------------ #

def _spawn_options(caller):
    """Return the ordered ``[(key, label)]`` spawn options selectable by *caller*.

    Filtered to options *caller* actually has a target for right now (a live
    HQ, an owned respawn beacon, a recorded death tile) — an unavailable
    option would otherwise silently redirect to a random tile at deploy time,
    which reads as a bug rather than a limit. ``random`` is always available,
    so a first-time player (no HQ/beacon/death yet) is offered only it.

    Fails open (all options shown) when the resolver or the caller's planet
    isn't available, so a wiring gap never blocks the spawn step.
    """
    from world.spawn_resolver import SPAWN_OPTIONS, SPAWN_OPTION_LABELS, SPAWN_RANDOM
    resolver = _get_system(caller, "spawn_resolver")
    planet = getattr(caller.db, "coord_planet", None)
    options = []
    for opt in SPAWN_OPTIONS:
        if opt != SPAWN_RANDOM and resolver is not None and planet:
            try:
                available = resolver.option_available(caller, opt, planet)
            except Exception:  # noqa: BLE001 - a lookup failure must not hide options
                available = True
            if not available:
                continue
        options.append((opt, SPAWN_OPTION_LABELS[opt]))
    return options


def _present_class_menu(caller, prefix=""):
    """Show the numbered class menu (spawning step 1).

    With no class data defined, the numbered flow can't dead-end: assign a
    default label and fall through to the spawn step.
    """
    choices = _class_choices(caller)
    if not choices:
        if caller.db.player_class is None:
            caller.db.player_class = "Recruit"
        _present_spawn_menu(caller, prefix=prefix)
        return
    lines = [prefix] if prefix else []
    lines.append("|wStep 1/2 — choose your class|n (type the number):")
    for i, c in enumerate(choices, 1):
        desc = f" — {c.description}" if c.description else ""
        lines.append(f"  |w{i}|n. |c{c.name}|n{desc}")
    caller.msg("\n".join(lines))


def _present_spawn_menu(caller, prefix=""):
    """Show available spawn points using their stable canonical numbers."""
    from world.spawn_resolver import SPAWN_OPTIONS

    lines = [prefix] if prefix else []
    lines.append("|wStep 2/2 — choose your spawn point|n (type the number):")
    canonical_numbers = {key: i for i, key in enumerate(SPAWN_OPTIONS, 1)}
    for key, label in _spawn_options(caller):
        lines.append(f"  |w{canonical_numbers[key]}|n. |c{label}|n")
    caller.msg("\n".join(lines))


def present_spawning_step(caller, *, prefix=""):
    """Present the current spawning step's numbered menu (or enter the lobby).

    The single driver for the "one step after another" flow: shows the class
    menu until a class is chosen, then the spawn menu until a spawn point is
    chosen, then (both chosen) advances to the lobby. Shared by the login
    router, the death path, and the selection commands. *prefix* is an optional
    lead line (e.g. a death notice) shown above the menu.
    """
    if caller.db.player_class is None:
        _present_class_menu(caller, prefix=prefix)
    elif not caller.db.pending_spawn_choice:
        _present_spawn_menu(caller, prefix=prefix)
    elif pl.finish_spawning(caller):
        announce_lobby(caller)


def _advance_spawning(caller):
    """Apply-then-advance: after a pick, present the next step or the lobby."""
    if caller.db.player_class is None:
        _present_class_menu(caller)
    elif not caller.db.pending_spawn_choice:
        _present_spawn_menu(caller)
    elif pl.finish_spawning(caller):
        announce_lobby(caller)


def _apply_class(caller, cdef):
    """Persist the chosen class, confirm it, and advance to the next step."""
    caller.db.player_class = cdef.key
    caller.msg(f"Class set to |c{cdef.name}|n. {cdef.description}".rstrip())
    _advance_spawning(caller)


def _apply_spawn(caller, key, label):
    """Persist the chosen spawn point, confirm it, and advance."""
    # Persist the choice so it survives a disconnect mid-spawn; the actual
    # relocation happens on 'enter' (resolved fresh then, so a destroyed HQ or
    # new death is reflected).
    caller.db.pending_spawn_choice = key
    caller.msg(f"Spawn point set to |c{label}|n.")
    _advance_spawning(caller)


def _select_class_by_number(caller, n):
    """Pick the nth class from the numbered menu (1-based)."""
    choices = _class_choices(caller)
    if not choices:
        caller.msg("No classes are defined. Type |wclass <name>|n to set one.")
        return
    if n < 1 or n > len(choices):
        caller.msg(f"Choose a number between |w1|n and |w{len(choices)}|n.")
        _present_class_menu(caller)
        return
    _apply_class(caller, choices[n - 1])


def _select_spawn_by_number(caller, n):
    """Pick a spawn option by its stable canonical number (1-based)."""
    from world.spawn_resolver import SPAWN_OPTIONS

    options = dict(_spawn_options(caller))
    if n < 1 or n > len(SPAWN_OPTIONS):
        listed = ", ".join(
            str(i) for i, key in enumerate(SPAWN_OPTIONS, 1) if key in options
        )
        caller.msg(f"Choose one of the listed numbers: |w{listed}|n.")
        _present_spawn_menu(caller)
        return

    key = SPAWN_OPTIONS[n - 1]
    label = options.get(key)
    if label is None:
        caller.msg(
            f"Spawn option |w{n}|n is not currently available. "
            "Choose one of the listed options."
        )
        _present_spawn_menu(caller)
        return
    _apply_spawn(caller, key, label)


# ------------------------------------------------------------------ #
#  State 3.2 — class selection (by number, name, or prefix)
# ------------------------------------------------------------------ #

class CmdClass(BaseCommand):
    """Choose your class while preparing to deploy (spawning).

    Usage:
      class            — show the numbered class menu
      class <n>        — pick the numbered class
      class <name>     — pick by name, key, or unambiguous prefix

    Your class is a chosen identity shown on your score and in 'who'. While
    spawning you can also just type the number of your choice. See
    'help spawning'.
    """

    key = "class"
    aliases = ["cls"]
    locks = "cmd:all()"
    help_category = "Lifecycle"

    def func(self):
        caller = self.caller
        if pl.get_state(caller) != PLAYER_STATE_SPAWNING:
            caller.msg("You can only choose a class while preparing to deploy.")
            return

        choices = _class_choices(caller)
        arg = self.args.strip()
        if not arg:
            _present_class_menu(caller)
            return

        # A bare number selects from the shown menu.
        if arg.isdigit():
            _select_class_by_number(caller, int(arg))
            return

        # Otherwise resolve by key / name / prefix via the registry resolver.
        registry = _get_system(caller, "registry")
        cdef = None
        if registry and hasattr(registry, "resolve_class"):
            cdef = registry.resolve_class(arg)
        if cdef is None:
            # No class data at all → allow a free-text label so the flow never
            # dead-ends; otherwise report the miss.
            if not choices:
                caller.db.player_class = arg.title()
                caller.msg(f"Class set to |c{arg.title()}|n.")
                _advance_spawning(caller)
                return
            caller.msg(f"Unknown class '{arg}'. Type |wclass|n to list them.")
            return
        _apply_class(caller, cdef)


# ------------------------------------------------------------------ #
#  State 3.1 — spawn-location selection
# ------------------------------------------------------------------ #

class CmdSpawn(BaseCommand):
    """Choose where you will deploy while preparing (spawning).

    Usage:
      spawn            — show the numbered spawn-point menu
      spawn <n>        — pick the numbered spawn point
      spawn respawn    — deploy at your Respawn Beacon
      spawn hq         — deploy at your headquarters
      spawn death      — deploy at your last place of death
      spawn random     — deploy at a random location

    Only options you currently have a target for are offered — hq/respawn/
    death are hidden until you have a live headquarters, an owned respawn
    beacon, or a recorded place of death respectively. Option numbers are
    fixed, so hidden choices leave gaps rather than changing another choice's
    meaning. A first-time character has none of these yet, so only
    ``4. Random location`` is available. While spawning you can also just type
    the number of your choice. See 'help spawning'.
    """

    key = "spawn"
    locks = "cmd:all()"
    help_category = "Lifecycle"

    def func(self):
        caller = self.caller
        if pl.get_state(caller) != PLAYER_STATE_SPAWNING:
            caller.msg("You can only choose a spawn point while preparing to deploy.")
            return

        arg = self.args.strip().lower()
        if not arg:
            _present_spawn_menu(caller)
            return

        # A bare number selects from the shown menu.
        if arg.isdigit():
            _select_spawn_by_number(caller, int(arg))
            return

        # Otherwise accept a prefix of an option (hq/death/random).
        options = _spawn_options(caller)
        match = [(k, lbl) for (k, lbl) in options if k.startswith(arg)]
        if len(match) != 1:
            # A real option that is merely hidden right now (no HQ built yet,
            # never died) reports its availability, matching the numbered
            # path — only an unrecognized word is "unknown".
            from world.spawn_resolver import SPAWN_OPTIONS, SPAWN_OPTION_LABELS

            hidden = [k for k in SPAWN_OPTIONS if k.startswith(arg)]
            if not match and len(hidden) == 1:
                caller.msg(
                    f"|c{SPAWN_OPTION_LABELS[hidden[0]]}|n is not currently "
                    "available. Type |wspawn|n to list your options."
                )
            else:
                caller.msg(
                    f"Unknown spawn option '{arg}'. Type |wspawn|n to list them."
                )
            return
        key, label = match[0]
        _apply_spawn(caller, key, label)


def _account_character_names(caller):
    """Return the caller's account's character names (for the delete hint)."""
    account = getattr(caller, "account", None)
    if account is None:
        return []
    try:
        chars = getattr(account, "characters", None)
        if chars is not None:
            seq = list(chars.all()) if hasattr(chars, "all") else list(chars)
            return [c.key for c in seq if c]
    except Exception:  # noqa: BLE001
        pass
    try:
        legacy = getattr(account.db, "_playable_characters", None) or []
        return [c.key for c in legacy if c]
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ #
#  Bare-number selection — the "type a number" front end
# ------------------------------------------------------------------ #

class CmdSelect(BaseCommand):
    """Select a numbered option from the current staging menu.

    Usage:
      <number>         (e.g. just type '1')
      select <number>

    Drives the numbered wizard by typing a number:
      * SPAWNING — the class then spawn-point menus (1-n).
      * LOBBY — |w1|n enter the game, |w2|n change password, |w3|n delete a
        character, |w0|n quit.
    Bound to the digit keys, so a bare '1' works. Outside staging it does
    nothing.
    """

    key = "select"
    aliases = [str(i) for i in range(0, 10)]  # bare 0-9 select from the menu
    locks = "cmd:all()"
    help_category = "Lifecycle"

    def func(self):
        caller = self.caller
        state = pl.get_state(caller)

        # The number is either the command word itself (bare '1') or its arg
        # ('select 1'). cmdstring is the alias the player typed.
        raw = (self.args or "").strip() or (self.cmdstring or "").strip()

        if state == PLAYER_STATE_LOBBY:
            self._select_lobby(caller, raw, session=self.session)
            return
        if state != PLAYER_STATE_SPAWNING:
            caller.msg("There's nothing to select right now.")
            return

        if not raw.isdigit():
            present_spawning_step(caller)
            return
        n = int(raw)

        # Route to whichever step the player is on.
        if caller.db.player_class is None:
            _select_class_by_number(caller, n)
        elif not caller.db.pending_spawn_choice:
            _select_spawn_by_number(caller, n)
        else:
            # Both already chosen (shouldn't linger in SPAWNING) — advance.
            _advance_spawning(caller)

    @staticmethod
    def _select_lobby(caller, raw, session=None):
        """Handle the lobby menu: 1 enter, 2 password, 3 delete char, 0 quit.

        The account actions (password / chardelete) take arguments, so their
        menu entry prints the command to type rather than running it blind.
        """
        if raw == "1":
            deploy_from_lobby(caller)
        elif raw == "2":
            caller.msg(
                "To change your password, type:  "
                "|wpassword <oldpass> = <newpass>|n"
            )
        elif raw == "3":
            chars = _account_character_names(caller)
            listing = f" Your characters: {', '.join(chars)}." if chars else ""
            caller.msg(
                "To permanently delete a character, type:  "
                "|wchardelete <name>|n (this cannot be undone)." + listing
            )
        elif raw == "0":
            # Forward the invoking session so the quit disconnects THIS session
            # (CmdQuit is account_caller and crashes on a None session).
            if hasattr(caller, "execute_cmd"):
                caller.execute_cmd("quit", session=session)
        else:
            announce_lobby(caller)


# ------------------------------------------------------------------ #
#  State 4.1 — enter the game
# ------------------------------------------------------------------ #

class CmdDeploy(BaseCommand):
    """Enter the game world from the staging area (lobby).

    Usage:
      deploy

    Deploys you at your chosen spawn point and drops you into the game. Only
    available once you have chosen a class and a spawn point. (In the lobby,
    'enter' does the same thing.)
    """

    key = "deploy"
    aliases = ["play"]
    locks = "cmd:all()"
    help_category = "Lifecycle"

    def func(self):
        deploy_from_lobby(self.caller)


#: Why :func:`apply_spawn_choice` refused, recorded on ``caller.ndb`` so the
#: deploy handler can tell "your chosen target is gone" (re-choosing helps)
#: apart from "you could not be placed in the world" (a missing planet room or
#: a rejected index move — the choice was fine).
SPAWN_FAIL_TARGET_GONE = "target_gone"
SPAWN_FAIL_PLACEMENT = "placement_failed"


def _mark_spawn_failure(caller, reason) -> None:
    """Record (or clear) the reason the last spawn placement refused."""
    try:
        caller.ndb._spawn_failure = reason
    except Exception:  # noqa: BLE001 - diagnostics must never block deploy
        pass


def deploy_from_lobby(caller) -> bool:
    """Deploy *caller* from the LOBBY into the game (transition 4.1 → PLAYING).

    Shared by :class:`CmdDeploy` and the building ``CmdEnter`` (which routes
    here when the caller is in the lobby, so plain ``enter`` also deploys). No-op
    with a hint if the player still needs to finish spawning. A selected HQ,
    beacon, or death tile is revalidated immediately before deployment; if it
    disappeared, the player returns to SPAWNING with an explicit re-prompt
    rather than silently landing at a random tile. Returns True only if the
    player entered the game.
    """
    state = pl.get_state(caller)
    if state == PLAYER_STATE_SPAWNING:
        caller.msg("You must choose a |wclass|n and a |wspawn|n point first.")
        return False
    if state != PLAYER_STATE_LOBBY:
        caller.msg("You are already in the game.")
        return False

    # Resolve and revalidate before changing state or clearing lobby markers.
    # A named target may have disappeared after the menu was shown/selected.
    if not apply_spawn_choice(caller):
        from world.spawn_resolver import SPAWN_OPTION_LABELS

        choice = getattr(caller.db, "pending_spawn_choice", None)
        reason = getattr(getattr(caller, "ndb", None), "_spawn_failure", None)
        label = SPAWN_OPTION_LABELS.get(choice) if choice else None
        caller.db.pending_spawn_choice = None

        # Only claim a target is gone when that is what actually happened. A
        # placement failure leaves the choice valid, and a re-entering player
        # with no pending choice never picked one at all.
        if reason == SPAWN_FAIL_PLACEMENT or label is None:
            where = f" at |c{label}|n" if label else ""
            cause = (
                f"Deployment failed{where} — you could not be placed "
                "in the world."
            )
            retry = "Choose a spawn point to try again."
        else:
            cause = f"{label} is no longer available."
            retry = "Choose another spawn point."

        # Re-prompt from SPAWNING: 'spawn' is only accepted in that state, so
        # this is what lets the player pick a different point (which may well
        # succeed when one specific destination is the problem).
        if pl.transition(caller, PLAYER_STATE_SPAWNING,
                         reason="spawn_unavailable"):
            present_spawning_step(caller, prefix=f"|y{cause} {retry}|n")
        else:
            caller.msg(f"|y{cause} Deployment was cancelled.|n")
        return False

    # Clear any stale clean-quit marker as we (re)enter play, so a later unclean
    # drop is correctly classified as linkdead (anti-combat-log).
    try:
        caller.ndb._clean_quit = False
    except Exception:  # noqa: BLE001
        pass

    # Deploy fresh: clear any lingering combat state so a player who died (or
    # quit) mid-fight doesn't re-enter still "in combat" (which would block Wall
    # passage, gate builds, and show a bogus combat timer). Reset both the
    # combat timer and the build-gate lockout tick.
    db = getattr(caller, "db", None)
    if db is not None:
        db.combat_timer_expires = 0
        db.combat_lockout_tick = 0
    if pl.enter_game(caller):
        caller.msg("|gYou deploy into the field.|n")
        _announce_current_directive(caller)
        if hasattr(caller, "execute_cmd"):
            caller.execute_cmd("look")
        return True
    return False


def _announce_current_directive(caller) -> None:
    """Point a FIRST-TIME player at their opening objective.

    A new player lands on an empty map with no idea what to do; the directive
    chain otherwise only speaks up on the first completion. This surfaces the
    opening objective on deploy — "Build your Headquarters" — turning a blank
    map into a clear first move.

    Deliberately limited to a player who has completed NOTHING yet
    (``progress == 0``). ``deploy_from_lobby`` also runs on every death-respawn
    and re-login, so announcing unconditionally would repeat the hint at every
    death for the whole of a mid-chain player's early game. Also silent when the
    chain is muted, complete, or the system is unavailable. Never raises.
    """
    try:
        from world.utils import get_system
        directive_system = get_system(caller, "directive_system")
        if directive_system is None:
            return
        view = directive_system.get_progress_view(caller)
        if view.get("muted"):
            return
        if int(view.get("progress", 0) or 0) != 0:
            return  # already underway — don't re-announce on every respawn
        current = next(
            (s for s in view.get("steps", []) if s.get("current")), None
        )
        if current is None:
            return  # chain complete — nothing to point at
        description = current.get("description")
        if not description:
            return
        from world.build_requirements import requirement_note
        note = requirement_note(caller, current.get("requires_building"))
        caller.msg(
            f"|c[Objective]|n {description}{note}. "
            f"Type |wdirectives|n to see your checklist."
        )
    except Exception:  # noqa: BLE001 - a deploy hint never blocks entering play
        pass


# ------------------------------------------------------------------ #
#  Shared: apply the chosen spawn location (used by enter + respawn)
# ------------------------------------------------------------------ #

def apply_spawn_choice(caller) -> bool:
    """Place *caller* into the world on deploy and report whether it is safe.

    Two cases, distinguished by ``db.pending_spawn_choice``:

    * **A choice is set** (a fresh chargen / post-death pick): revalidate named
      targets, resolve to a concrete ``(planet, x, y)``, and move there. If a
      once-valid HQ, beacon, or death tile has disappeared, return ``False``;
      callers must re-prompt instead of substituting a random spawn.
    * **No choice** (a clean quit → lobby → re-enter): deploy IN PLACE at the
      location the player quit from — their last ``(coord_x, coord_y)``. A quit
      is not a respawn, so it must NOT re-roll a spawn point. If stowed
      (location None), re-index them at those coords; if still located, leave
      them put.

    The pending choice is consumed only after successful application. Missing
    resolver wiring still fails open for compatibility; an installed resolver
    that explicitly rejects or loses a named target fails closed.
    """
    from world.spawn_resolver import SPAWN_RANDOM

    planet = getattr(caller.db, "coord_planet", None)
    choice = getattr(caller.db, "pending_spawn_choice", None)
    _mark_spawn_failure(caller, None)

    if choice:
        resolver = _get_system(caller, "spawn_resolver")
        can_validate = (
            resolver is not None
            and bool(planet)
            and hasattr(resolver, "option_available")
        )
        named_choice = choice != SPAWN_RANDOM

        if named_choice and can_validate:
            try:
                if not resolver.option_available(caller, choice, planet):
                    _mark_spawn_failure(caller, SPAWN_FAIL_TARGET_GONE)
                    return False
            except Exception:  # noqa: BLE001 - do not guess on validation failure
                logger.warning(
                    "Could not validate spawn choice %r for %s",
                    choice, getattr(caller, "key", "?"), exc_info=True,
                )
                _mark_spawn_failure(caller, SPAWN_FAIL_TARGET_GONE)
                return False

        target = None
        if resolver is not None and planet:
            try:
                target = resolver.resolve(caller, choice, planet)
            except Exception:  # noqa: BLE001 - handled as a resolver miss below
                logger.warning(
                    "Could not resolve spawn choice %r for %s",
                    choice, getattr(caller, "key", "?"), exc_info=True,
                )

        if target is not None:
            if not _relocate(caller, target[0], target[1], target[2]):
                _mark_spawn_failure(caller, SPAWN_FAIL_PLACEMENT)
                return False
        elif named_choice and can_validate:
            # Covers the target disappearing between option_available() and
            # resolve(), without silently using random/current coordinates.
            _mark_spawn_failure(caller, SPAWN_FAIL_TARGET_GONE)
            return False
        elif getattr(caller, "location", None) is None and planet:
            # Resolver is absent/unwired, or RANDOM exhausted all fallbacks:
            # avoid deploying a stowed player into the void.
            cx = getattr(caller.db, "coord_x", 0) or 0
            cy = getattr(caller.db, "coord_y", 0) or 0
            if not _relocate(caller, planet, cx, cy):
                _mark_spawn_failure(caller, SPAWN_FAIL_PLACEMENT)
                return False
    elif getattr(caller, "location", None) is None and planet:
        # No pending choice → a quit→re-enter: deploy in place at the quit
        # location (last coords), NOT a re-rolled spawn.
        cx = getattr(caller.db, "coord_x", 0) or 0
        cy = getattr(caller.db, "coord_y", 0) or 0
        if not _relocate(caller, planet, cx, cy):
            _mark_spawn_failure(caller, SPAWN_FAIL_PLACEMENT)
            return False
    # else: no choice AND still located → already in place, nothing to do.

    caller.db.pending_spawn_choice = None
    return True


def _relocate(caller, planet, x, y) -> bool:
    """Transactionally move *caller* to ``(planet, x, y)``.

    The physical room move, destination coordinate index, persisted coordinate
    fields, and origin index form one logical operation. Any explicit movement
    rejection or exception restores the original room/coordinates/indexes so a
    failed deployment cannot leave a SPAWNING character half-present in-world.
    Real movement/index mutators return ``None`` on success in several paths;
    only an explicit ``False`` is treated as rejection.
    """
    try:
        from world.utils import get_game_systems, nearest_free_tile

        systems = get_game_systems()
        planet_rooms = systems.get("planet_rooms", {})
        room = planet_rooms.get(planet)
        if room is None:
            return False

        # Never drop the player onto a tile a building occupies (e.g. the fixed
        # planet spawn, or an enemy structure sitting there) — nudge to the
        # nearest building-free tile, kept in-bounds via the planet registry.
        registry = systems.get("planet_registry")
        in_bounds = None
        if registry is not None and hasattr(registry, "is_valid_coordinate"):
            in_bounds = lambda cx, cy: registry.is_valid_coordinate(  # noqa: E731
                cx, cy, planet
            )
        fx, fy = nearest_free_tile(room, int(x), int(y), in_bounds=in_bounds)
        target_pair = (int(fx), int(fy))
    except Exception:  # noqa: BLE001
        logger.debug("Could not prepare spawn relocation", exc_info=True)
        return False

    db = getattr(caller, "db", None)
    if db is None:
        return False

    origin_room = getattr(caller, "location", None)
    old_x = getattr(db, "coord_x", None)
    old_y = getattr(db, "coord_y", None)
    old_planet = getattr(db, "coord_planet", None)
    cross_room = origin_room is not room
    origin_index = getattr(
        getattr(origin_room, "ndb", None), "_coord_index", None
    )

    old_pair = None
    origin_had_entry = False
    try:
        if old_x is not None and old_y is not None:
            old_pair = (int(old_x), int(old_y))
        if origin_index is not None and old_pair is not None:
            get_at = getattr(origin_index, "get_at", None)
            origin_had_entry = (
                caller in get_at(*old_pair) if callable(get_at) else True
            )
    except Exception:  # noqa: BLE001
        logger.debug("Could not snapshot origin spawn index", exc_info=True)
        return False

    def _rollback() -> None:
        """Best-effort compensation for every spatial mutation above."""
        destination_index = getattr(
            getattr(room, "ndb", None), "_coord_index", None
        )

        # Restore physical containment without firing normal movement hooks.
        if getattr(caller, "location", None) is not origin_room:
            try:
                kwargs = {"quiet": True, "move_hooks": False}
                if origin_room is None:
                    kwargs["to_none"] = True
                result = caller.move_to(origin_room, **kwargs)
                if result is False:
                    raise RuntimeError("rollback move_to rejected")
            except Exception:  # noqa: BLE001
                logger.debug("Spawn rollback move_to failed", exc_info=True)

            # A nonconforming mover may reject or claim success without
            # restoring location. Direct assignment is the final no-hook repair.
            if getattr(caller, "location", None) is not origin_room:
                try:
                    caller.location = origin_room
                except Exception:  # noqa: BLE001
                    logger.error(
                        "Could not restore caller location after spawn failure",
                        exc_info=True,
                    )

        # Restore persisted coordinates before normalizing index membership.
        for field, value in (
            ("coord_x", old_x),
            ("coord_y", old_y),
            ("coord_planet", old_planet),
        ):
            try:
                setattr(db, field, value)
            except Exception:  # noqa: BLE001
                logger.error(
                    "Could not restore %s after spawn failure", field,
                    exc_info=True,
                )

        # PlanetRoom.move_entity may have failed after moving the destination
        # index and/or coordinates. Remove both buckets it can have touched.
        if destination_index is not None:
            rollback_pairs = []
            for pair in (old_pair, target_pair):
                if pair is not None and pair not in rollback_pairs:
                    rollback_pairs.append(pair)
            for pair in rollback_pairs:
                try:
                    destination_index.remove(caller, *pair)
                except Exception:  # noqa: BLE001
                    logger.error(
                        "Could not clean destination spawn index during rollback",
                        exc_info=True,
                    )

        # Restore the exact pre-move origin membership. For a same-room move
        # whose index was lazy before move_entity, use the index it created and
        # restore the caller's old bucket; cross-room lazy origins stay lazy.
        rollback_origin_index = origin_index
        restore_origin_entry = origin_had_entry
        if not cross_room and rollback_origin_index is None:
            rollback_origin_index = destination_index
            restore_origin_entry = rollback_origin_index is not None
        if rollback_origin_index is not None and old_pair is not None:
            try:
                if restore_origin_entry:
                    rollback_origin_index.add(caller, *old_pair)
                else:
                    rollback_origin_index.remove(caller, *old_pair)
            except Exception:  # noqa: BLE001
                logger.error(
                    "Could not restore origin spawn index during rollback",
                    exc_info=True,
                )

    try:
        if cross_room:
            move_result = caller.move_to(room, quiet=True, move_hooks=False)
            if move_result is False:
                raise RuntimeError("destination room rejected spawn move")

        if hasattr(room, "move_entity"):
            index_result = room.move_entity(
                caller, target_pair[0], target_pair[1], notify=False
            )
            if index_result is False:
                raise RuntimeError("destination index rejected spawn move")
        else:
            db.coord_x, db.coord_y = target_pair

        # move_hooks=False skips PlanetRoom.at_object_leave. Treat origin-index
        # cleanup as part of the transaction; a stale ghost is not success.
        if cross_room and origin_index is not None and old_pair is not None:
            cleanup_result = origin_index.remove(caller, *old_pair)
            if cleanup_result is False:
                raise RuntimeError("origin index rejected spawn cleanup")
            get_at = getattr(origin_index, "get_at", None)
            if callable(get_at) and caller in get_at(*old_pair):
                raise RuntimeError("origin index retained caller after cleanup")

        db.coord_planet = planet
        return True
    except Exception:  # noqa: BLE001
        logger.debug("Spawn relocation failed; rolling back", exc_info=True)
        _rollback()
        return False


def announce_lobby(caller) -> None:
    """Present the numbered lobby menu.

    The staging-area hub: deploy, manage account, or disconnect. Replaces the
    OOC state — auto-puppet drops the player straight here on login.
    """
    caller.msg(
        "\n|wStaging area.|n\n"
        "  |w1|n. |cEnter the game|n\n"
        "  |w2|n. |cChange password|n\n"
        "  |w3|n. |cDelete character|n\n"
        "  |w0|n. |cQuit (disconnect)|n\n"
        "(type the number)"
    )


def announce_spawning(caller, *, prefix: str = "") -> None:
    """Present the current SPAWNING step as a numbered menu.

    Shared by the login router (fresh/resumed spawning player) and the death
    path (slain player routed back to SPAWNING), so a player always sees the
    numbered class → spawn → enter flow one step at a time. *prefix* is an
    optional context line (e.g. a death notice) shown above the menu.
    """
    present_spawning_step(caller, prefix=prefix)


# ------------------------------------------------------------------ #
#  State 4.2 — quit (clean disconnect)
# ------------------------------------------------------------------ #

# Import the stock CmdQuit lazily-safe base (Evennia default account command).
try:  # pragma: no cover - real Evennia
    from evennia.commands.default.account import CmdQuit as _BaseQuit
except Exception:  # pragma: no cover - stubbed test env
    _BaseQuit = None


if _BaseQuit is not None:

    class CmdQuit(_BaseQuit):
        """Quit — leave the field to the staging area, or disconnect from it.

        Usage:
          quit

        Two levels, mirroring the lobby wizard:
          * In the game → return to the |wstaging area|n (you stay connected;
            re-deploy with the menu, or |wquit|n again to disconnect).
          * In the staging area → disconnect from the game.

        You cannot quit the field while in combat — the anti-combat-log rule:
        wait for your combat timer to run out (see 'score'), then quit.
        """

        def func(self):
            account = self.account

            # When the lobby flow is on, a PLAYING quit is "leave the field to
            # the staging area" (stay connected), NOT a disconnect. Only a quit
            # from the staging area (LOBBY/SPAWNING) actually disconnects.
            #
            # Fail CLOSED: the retreat path enforces the anti-combat-log rule
            # (an in-combat puppet can't leave the field). If it raises, we must
            # NOT fall through to a clean disconnect — that would let an
            # in-combat player escape to a non-targetable LOBBY on any error.
            # On failure, abort the quit entirely (the player stays PLAYING and
            # can retry) rather than silently disconnecting.
            try:
                from world.lobby_flow import lobby_flow_enabled
                flow_on = lobby_flow_enabled() and account is not None
            except Exception:  # noqa: BLE001 - flag read failed; treat as off
                logger.debug("lobby_flow_enabled check failed", exc_info=True)
                flow_on = False
            if flow_on:
                try:
                    if self._retreat_playing_puppets_to_lobby(account):
                        return  # a puppet left the field (or was combat-blocked)
                except Exception:  # noqa: BLE001 - fail closed, do NOT disconnect
                    logger.warning(
                        "Quit-to-lobby routing failed; blocking quit to avoid a "
                        "combat-log escape", exc_info=True,
                    )
                    self.msg(
                        "|rCouldn't process quit right now — try again.|n"
                    )
                    return

            # In the staging area (or flow off): a real disconnect. Mark every
            # puppet as a clean quit BEFORE the stock quit disconnects the
            # session(s) — Evennia's unpuppet_object does not forward a reason to
            # at_post_unpuppet, so this transient ndb marker is how the disconnect
            # hook tells a clean quit from a dropped connection.
            try:
                if account is not None:
                    for puppet in account.get_all_puppets():
                        if puppet is not None:
                            puppet.ndb._clean_quit = True
            except Exception:  # noqa: BLE001
                pass

            # The stock CmdQuit (account_caller) disconnects self.session, and
            # crashes on a None session. When invoked without a bound session
            # (e.g. routed via a character's execute_cmd), fall back to the
            # account's own session(s) so quit still works from the lobby.
            if self.session is None and account is not None:
                sessions = account.sessions.all()
                if sessions:
                    self.session = sessions[-1]
            super().func()

        @staticmethod
        def _retreat_playing_puppets_to_lobby(account) -> bool:
            """Send the account's PLAYING puppets back to the staging area (LOBBY).

            Returns True if at least one puppet was retreated OR the quit was
            blocked by combat (either way the caller stays connected instead of
            disconnecting). Enforces the anti-combat-log rule ATOMICALLY: if ANY
            PLAYING puppet is in combat, NO puppet is retreated (a two-pass check
            — otherwise a puppet earlier in iteration order would already be
            stowed before a later in-combat puppet aborted the rest, letting a
            player retreat their safe puppets while one is stuck fighting).

            Under MULTISESSION_MODE=0 there is only ever one puppet, so the
            two-pass logic is equivalent to the single-puppet case; it matters
            only if multi-character play is ever enabled (see R12.2).
            """
            from world import player_lifecycle as pl
            from world.constants import PLAYER_STATE_PLAYING
            from world.combat_timer import player_in_combat

            playing = [
                p for p in account.get_all_puppets()
                if p is not None and pl.get_state(p) == PLAYER_STATE_PLAYING
            ]
            if not playing:
                return False

            # Pass 1: if ANY playing puppet is in combat, block the WHOLE quit —
            # retreat nobody, disconnect nobody.
            in_combat = [p for p in playing if player_in_combat(p)]
            if in_combat:
                for puppet in in_combat:
                    puppet.msg(
                        "|rYou can't quit the field while in combat.|n Wait for "
                        "your combat timer to run out (see |wscore|n)."
                    )
                return True

            # Pass 2: none in combat — retreat every playing puppet to the lobby.
            for puppet in playing:
                pl.to_lobby(puppet, reason="quit")
                if hasattr(puppet, "stow_from_world"):
                    puppet.stow_from_world()
                announce_lobby(puppet)
            return True
