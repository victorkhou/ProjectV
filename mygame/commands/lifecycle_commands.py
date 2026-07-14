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
#  State 3.2 — class selection
# ------------------------------------------------------------------ #

class CmdClass(BaseCommand):
    """Choose your class while preparing to deploy (spawning).

    Usage:
      class            — list the available classes
      class <name>     — pick a class (name, key, or unambiguous prefix)

    Your class is a chosen identity shown on your score and in 'who'. Pick a
    class and a spawn point, then type 'enter' to deploy. See 'help spawning'.
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
            self._show_choices(caller, choices)
            return

        # Resolve the choice (key / name / prefix) via the registry resolver.
        registry = _get_system(caller, "registry")
        cdef = None
        if registry and hasattr(registry, "resolve_class"):
            cdef = registry.resolve_class(arg)
        if cdef is None:
            # No class data at all → allow a single default label so the flow
            # never dead-ends; otherwise report the miss.
            if not choices:
                caller.db.player_class = arg.title()
                caller.msg(f"Class set to |w{arg.title()}|n.")
                self._maybe_advance(caller)
                return
            caller.msg(f"Unknown class '{arg}'. Type |wclass|n to list them.")
            return

        caller.db.player_class = cdef.key
        caller.msg(f"Class set to |w{cdef.name}|n. {cdef.description}".rstrip())
        self._maybe_advance(caller)

    @staticmethod
    def _show_choices(caller, choices):
        if not choices:
            caller.msg(
                "No classes are defined. Type |wclass <name>|n to set any "
                "label, or just |wspawn|n and |wenter|n."
            )
            return
        lines = ["|wChoose a class|n (type 'class <name>'):"]
        for c in choices:
            desc = f" — {c.description}" if c.description else ""
            lines.append(f"  |w{c.name}|n{desc}")
        current = caller.db.player_class
        if current:
            lines.append(f"\nCurrent: |w{current}|n")
        caller.msg("\n".join(lines))

    @staticmethod
    def _maybe_advance(caller):
        """If class + spawn are both chosen, advance SPAWNING → LOBBY."""
        if caller.db.player_class is None:
            return
        if not caller.ndb.spawn_choice and not caller.db.pending_spawn_choice:
            caller.msg("Now choose a |wspawn|n point (type |wspawn|n).")
            return
        if pl.finish_spawning(caller):
            _announce_lobby(caller)


# ------------------------------------------------------------------ #
#  State 3.1 — spawn-location selection
# ------------------------------------------------------------------ #

class CmdSpawn(BaseCommand):
    """Choose where you will deploy while preparing (spawning).

    Usage:
      spawn                 — list spawn options
      spawn hq              — deploy at your headquarters
      spawn death           — deploy at your last place of death
      spawn random          — deploy at a random location

    Pick a class and a spawn point, then 'enter' to deploy. If your chosen
    point is unavailable (no HQ, never died), you deploy at the planet's
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

        from world.spawn_resolver import SPAWN_OPTIONS, SPAWN_OPTION_LABELS

        arg = self.args.strip().lower()
        if not arg:
            lines = ["|wChoose a spawn point|n (type 'spawn <option>'):"]
            for opt in SPAWN_OPTIONS:
                lines.append(f"  |w{opt}|n — {SPAWN_OPTION_LABELS[opt]}")
            caller.msg("\n".join(lines))
            return

        # Accept a prefix of an option (hq/death/random).
        match = [o for o in SPAWN_OPTIONS if o.startswith(arg)]
        if len(match) != 1:
            caller.msg(f"Unknown spawn option '{arg}'. Type |wspawn|n to list them.")
            return
        choice = match[0]
        # Persist the choice so it survives a disconnect mid-spawn; the actual
        # relocation happens on 'enter' (resolved fresh then, so a destroyed HQ
        # or new death is reflected).
        caller.db.pending_spawn_choice = choice
        caller.msg(
            f"Spawn point set to |w{SPAWN_OPTION_LABELS[choice]}|n."
        )
        CmdClass._maybe_advance(caller)


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
    if pl.enter_game(caller):
        caller.msg("|gYou deploy into the field.|n")
        if hasattr(caller, "execute_cmd"):
            caller.execute_cmd("look")
        return True
    return False


# ------------------------------------------------------------------ #
#  Shared: apply the chosen spawn location (used by enter + respawn)
# ------------------------------------------------------------------ #

def apply_spawn_choice(caller, default_choice="hq") -> None:
    """Relocate *caller* to their chosen spawn point via the spawn resolver.

    Reads ``db.pending_spawn_choice`` (falling back to *default_choice*),
    resolves it to a concrete ``(planet, x, y)`` through the wired
    ``spawn_resolver`` system, and moves the character there. A missing resolver
    or an unresolvable choice leaves the player where they are (they still enter
    the game). Clears the pending choice once applied.
    """
    resolver = _get_system(caller, "spawn_resolver")
    choice = getattr(caller.db, "pending_spawn_choice", None) or default_choice
    planet = getattr(caller.db, "coord_planet", None)
    target = None
    if resolver is not None and planet:
        try:
            target = resolver.resolve(caller, choice, planet)
        except Exception:  # noqa: BLE001 - a spawn miss must not block entering
            target = None
    if target is not None:
        _relocate(caller, target[0], target[1], target[2])
    elif getattr(caller, "location", None) is None and planet:
        # No resolvable spawn, but the player is STOWED (location None, e.g.
        # after death/spawning) — they must land SOMEWHERE or they'd deploy into
        # the void. Fall back to their last coords on their planet room.
        cx = getattr(caller.db, "coord_x", 0) or 0
        cy = getattr(caller.db, "coord_y", 0) or 0
        _relocate(caller, planet, cx, cy)
    caller.db.pending_spawn_choice = None


def _relocate(caller, planet, x, y) -> None:
    """Move *caller* to ``(planet, x, y)`` on the shared PlanetRoom.

    Mirrors ``CmdTeleport``: move into the destination planet room if changing
    planets, then update coords via ``move_entity`` so the coordinate index is
    correct. Best-effort — never raises into the caller.
    """
    try:
        from world.utils import get_game_systems
        planet_rooms = get_game_systems().get("planet_rooms", {})
        room = planet_rooms.get(planet)
        if room is None:
            return
        caller.db.coord_planet = planet
        if caller.location is not room:
            caller.move_to(room, quiet=True, move_hooks=False)
        if hasattr(room, "move_entity"):
            room.move_entity(caller, int(x), int(y), notify=False)
        else:
            caller.db.coord_x = int(x)
            caller.db.coord_y = int(y)
    except Exception:  # noqa: BLE001
        logger.debug("Spawn relocation failed", exc_info=True)


def _announce_lobby(caller) -> None:
    """Tell the player they're staged and ready to enter (SPAWNING → LOBBY)."""
    caller.msg(
        "\n|wReady to deploy.|n Type |wenter|n to join the game, "
        "or |wquit|n to disconnect."
    )


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
