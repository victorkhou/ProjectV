"""
Player-lifecycle commands — the state-3 (spawning) and state-4 (lobby) flow.

These are the command-driven front end to the ``world.player_lifecycle`` state
machine. The game is imperative-command-based throughout (no EvMenu anywhere),
so the spawning/lobby flow follows suit: a small set of commands the player
issues while their character sits in the SPAWNING or LOBBY state, before they
Enter the world.

Model: the character stays PUPPETED throughout (auto-puppet is unchanged), and
``db.player_state`` gates what the player may do. While SPAWNING or LOBBY the
player is "not yet in the game" — the game commands (move/attack/build/...)
refuse via the shared :func:`require_in_game` guard, and combat/tick systems
treat a non-PLAYING character as not a live participant. The commands here are
the only way to advance:

    SPAWNING:  ``class <name>``  — pick a class (state 3.2)
               ``spawn <where>`` — pick a spawn location (state 3.1); once BOTH
                                   class and location are chosen, advance to LOBBY
    LOBBY:     ``enter``         — enter the game world (→ PLAYING)   [4.1]
               ``quit``          — leave (handled by Evennia's quit)  [4.2]

Informational and out-of-world social commands remain available in every state
(they don't require being deployed): ``look``, ``help``, ``who``, ``score``,
``map``, ``inventory``, ``message`` (DMs), and ``chat`` (the global channel).
Tile-scoped ``say`` is gated — a staging player is not on a tile to speak on.

Wiring these into a live cmdset + disabling the behavioral gate is done at the
composition root behind the ``LOBBY_FLOW_ENABLED`` flag, so the machinery can
ship and be verified before the flow is switched on.
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

def _spawn_options():
    """Return the ordered ``[(key, label)]`` spawn options for the menu."""
    from world.spawn_resolver import SPAWN_OPTIONS, SPAWN_OPTION_LABELS
    return [(opt, SPAWN_OPTION_LABELS[opt]) for opt in SPAWN_OPTIONS]


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
    """Show the numbered spawn-point menu (spawning step 2)."""
    lines = [prefix] if prefix else []
    lines.append("|wStep 2/2 — choose your spawn point|n (type the number):")
    for i, (_key, label) in enumerate(_spawn_options(), 1):
        lines.append(f"  |w{i}|n. |c{label}|n")
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
    """Pick the nth spawn option from the numbered menu (1-based)."""
    options = _spawn_options()
    if n < 1 or n > len(options):
        caller.msg(f"Choose a number between |w1|n and |w{len(options)}|n.")
        _present_spawn_menu(caller)
        return
    key, label = options[n - 1]
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
      spawn hq         — deploy at your headquarters
      spawn death      — deploy at your last place of death
      spawn random     — deploy at a random location

    While spawning you can also just type the number of your choice. If your
    chosen point is unavailable (no HQ, never died), you deploy at the planet's
    default spawn instead. See 'help spawning'.
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
        options = _spawn_options()
        match = [(k, lbl) for (k, lbl) in options if k.startswith(arg)]
        if len(match) != 1:
            caller.msg(f"Unknown spawn option '{arg}'. Type |wspawn|n to list them.")
            return
        key, label = match[0]
        _apply_spawn(caller, key, label)


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
      * LOBBY — |w1|n to enter the game, |w0|n to quit.
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
            self._select_lobby(caller, raw)
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
    def _select_lobby(caller, raw):
        """Handle the lobby deployment menu: 1 = enter game, 0 = quit."""
        if raw == "1":
            deploy_from_lobby(caller)
        elif raw == "0":
            if hasattr(caller, "execute_cmd"):
                caller.execute_cmd("quit")
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


def deploy_from_lobby(caller) -> bool:
    """Deploy *caller* from the LOBBY into the game (transition 4.1 → PLAYING).

    Shared by :class:`CmdDeploy` and the building ``CmdEnter`` (which routes
    here when the caller is in the lobby, so plain ``enter`` also deploys). No-op
    with a hint if the player still needs to finish spawning. Returns True if the
    player entered the game.
    """
    state = pl.get_state(caller)
    if state == PLAYER_STATE_SPAWNING:
        caller.msg("You must choose a |wclass|n and a |wspawn|n point first.")
        return False
    if state != PLAYER_STATE_LOBBY:
        caller.msg("You are already in the game.")
        return False

    # Clear any stale clean-quit marker as we (re)enter play, so a later unclean
    # drop is correctly classified as linkdead (anti-combat-log).
    try:
        caller.ndb._clean_quit = False
    except Exception:  # noqa: BLE001
        pass

    # Apply the chosen spawn location, then transition to PLAYING.
    apply_spawn_choice(caller)
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
        if hasattr(caller, "execute_cmd"):
            caller.execute_cmd("look")
        return True
    return False


# ------------------------------------------------------------------ #
#  Shared: apply the chosen spawn location (used by enter + respawn)
# ------------------------------------------------------------------ #

def apply_spawn_choice(caller) -> None:
    """Place *caller* into the world on deploy.

    Two cases, distinguished by ``db.pending_spawn_choice``:

    * **A choice is set** (a fresh chargen / post-death pick — hq/death/random):
      resolve it to a concrete ``(planet, x, y)`` via the ``spawn_resolver`` and
      move there. This is the ONLY path that relocates by spawn option.
    * **No choice** (a clean quit → lobby → re-enter): deploy IN PLACE at the
      location the player quit from — their last ``(coord_x, coord_y)``. A quit
      is not a respawn, so it must NOT re-roll a spawn point (that was the "every
      re-enter goes random" bug). If stowed (location None), re-index them at
      those coords; if still located, leave them put.

    Clears the pending choice once applied.
    """
    planet = getattr(caller.db, "coord_planet", None)
    choice = getattr(caller.db, "pending_spawn_choice", None)

    if choice:
        # Explicit chargen / post-death pick — resolve the chosen spawn option.
        resolver = _get_system(caller, "spawn_resolver")
        target = None
        if resolver is not None and planet:
            try:
                target = resolver.resolve(caller, choice, planet)
            except Exception:  # noqa: BLE001 - a spawn miss must not block entering
                target = None
        if target is not None:
            _relocate(caller, target[0], target[1], target[2])
        elif getattr(caller, "location", None) is None and planet:
            # Resolver miss but the player is stowed — land them at their last
            # coords rather than deploying into the void.
            cx = getattr(caller.db, "coord_x", 0) or 0
            cy = getattr(caller.db, "coord_y", 0) or 0
            _relocate(caller, planet, cx, cy)
    elif getattr(caller, "location", None) is None and planet:
        # No pending choice → a quit→re-enter: deploy in place at the quit
        # location (last coords), NOT a re-rolled spawn.
        cx = getattr(caller.db, "coord_x", 0) or 0
        cy = getattr(caller.db, "coord_y", 0) or 0
        _relocate(caller, planet, cx, cy)
    # else: no choice AND still located → already in place, nothing to do.

    caller.db.pending_spawn_choice = None


def _relocate(caller, planet, x, y) -> None:
    """Move *caller* to ``(planet, x, y)`` on the shared PlanetRoom.

    Mirrors ``CmdTeleport``: move into the destination planet room if changing
    planets, then update coords via ``move_entity`` so the coordinate index is
    correct. Best-effort — never raises into the caller.
    """
    try:
        from world.utils import get_game_systems, nearest_free_tile
        systems = get_game_systems()
        planet_rooms = systems.get("planet_rooms", {})
        room = planet_rooms.get(planet)
        if room is None:
            return
        # Never drop the player onto a tile a building occupies (e.g. the fixed
        # planet spawn, or an enemy structure sitting there) — nudge to the
        # nearest building-free tile, kept in-bounds via the planet registry.
        registry = systems.get("planet_registry")
        in_bounds = None
        if registry is not None and hasattr(registry, "is_valid_coordinate"):
            in_bounds = lambda cx, cy: registry.is_valid_coordinate(cx, cy, planet)  # noqa: E731
        fx, fy = nearest_free_tile(room, int(x), int(y), in_bounds=in_bounds)
        caller.db.coord_planet = planet
        if caller.location is not room:
            caller.move_to(room, quiet=True, move_hooks=False)
        if hasattr(room, "move_entity"):
            room.move_entity(caller, fx, fy, notify=False)
        else:
            caller.db.coord_x = fx
            caller.db.coord_y = fy
    except Exception:  # noqa: BLE001
        logger.debug("Spawn relocation failed", exc_info=True)


def announce_lobby(caller) -> None:
    """Present the numbered lobby (deployment) menu (SPAWNING → LOBBY / on login).

    The final wizard step: type |w1|n to enter the game or |w0|n to quit.
    """
    caller.msg(
        "\n|wReady to deploy.|n\n"
        "  |w1|n. |cEnter the game|n\n"
        "  |w0|n. |cQuit|n\n"
        "(type the number, or |wenter|n / |wquit|n)"
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
        """Quit the game — a CLEAN disconnect.

        Usage:
          quit

        Marks this as a deliberate quit so your character is NOT left in the
        world as a link-dead combat target: a PLAYING character returns to the
        lobby (you re-enter on next login), rather than lingering on its tile
        during the link-dead grace window.
        """

        def func(self):
            # Mark every puppet as a clean quit BEFORE the stock quit disconnects
            # the session(s). Evennia's unpuppet_object does not forward a reason
            # to at_post_unpuppet, so this transient ndb marker is how the
            # character's disconnect hook tells a clean quit from a dropped
            # connection. Guarded so a marking hiccup never blocks quitting.
            try:
                account = self.account
                if account is not None:
                    for puppet in account.get_all_puppets():
                        if puppet is not None:
                            puppet.ndb._clean_quit = True
            except Exception:  # noqa: BLE001
                pass
            super().func()
