"""
Player lifecycle state machine — the single authority for ``db.player_state``.

A player character moves through a small set of persisted lifecycle states
(see ``world.constants.PLAYER_STATE_*``):

    SPAWNING  → picking class + spawn location (out-of-character)
    LOBBY     → waiting to enter the game; enter / quit (out-of-character)
    PLAYING   → puppeted, in the game world
    LINKDEAD  → connection dropped without ``quit``; a grace timer runs

Two earlier phases — a raw socket connection and an authenticated-but-not-yet
routed account — are NOT persisted here; they live in Evennia's built-in
session FSM (unloggedin → logged_in). This module owns only the states a
character can *dwell* in, and the transitions between them.

Design (mirrors the ``resting_activity_status`` single-authority precedent, but
for a genuinely persisted FSM rather than a derived value):

* :func:`transition` is the ONLY function that writes ``db.player_state``. Every
  other module calls it. It validates the move against
  ``PLAYER_STATE_TRANSITIONS`` (so two callers can't drive an illegal edge),
  writes the field, and publishes ``PLAYER_STATE_CHANGED`` for observers.
* :func:`route_on_login` reads the *current* persisted state and returns the
  state a re-connecting/logging-in character should resume in — the seam that
  implements the spec's login rules (new char → SPAWNING; existing non-dead →
  LOBBY; mid-spawn/dead → SPAWNING; was PLAYING/LINKDEAD → PLAYING resume).
* The remaining helpers (:func:`record_death`, :func:`begin_linkdead`,
  :func:`is_linkdead_expired`, :func:`get_state`, :func:`state_label`) are thin,
  side-effect-scoped operations the login/disconnect/death hooks and the tick
  loop call — none of them writes ``player_state`` except through
  :func:`transition`.

This module is framework-free (no Evennia imports): it operates on any object
exposing ``.db`` and takes the event bus / clock as parameters or resolves the
process singletons lazily, so it is unit-testable with plain fakes.
"""

from __future__ import annotations

import logging
from typing import Any

from world.constants import (
    PLAYER_STATE_LABELS,
    PLAYER_STATE_LINKDEAD,
    PLAYER_STATE_LOBBY,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_SPAWNING,
    PLAYER_STATE_TRANSITIONS,
    PLAYER_STATES,
)

logger = logging.getLogger("mygame.player_lifecycle")


# ------------------------------------------------------------------ #
#  Reads
# ------------------------------------------------------------------ #

def get_state(player: Any) -> str | None:
    """Return *player*'s current persisted lifecycle state, or ``None``.

    ``None`` means the character has never been routed (a brand-new character,
    or a legacy character created before the field existed) — the login router
    promotes it into a concrete state.
    """
    db = getattr(player, "db", None)
    if db is None:
        return None
    return getattr(db, "player_state", None)


def state_label(state: str | None) -> str:
    """Return the human-readable label for a lifecycle *state* (for ``who``).

    ``None``/unknown renders as ``"—"`` so the admin table never shows a raw
    internal token or blank cell.
    """
    if state is None:
        return "—"
    return PLAYER_STATE_LABELS.get(state, state)


# ------------------------------------------------------------------ #
#  The single writer
# ------------------------------------------------------------------ #

def transition(
    player: Any, new_state: str, *, reason: str = "", event_bus: Any = None,
) -> bool:
    """Move *player* to *new_state*. The ONLY writer of ``db.player_state``.

    Validates the move against ``PLAYER_STATE_TRANSITIONS``:

    * a character with no state yet (``None``) may enter any state — this is the
      login router promoting a fresh/legacy character;
    * an already-stated character may only follow a declared edge;
    * a no-op self-transition (``current == new_state``) is always allowed and
      returns ``True`` without re-publishing (idempotent).

    On a successful change it writes the field and publishes
    ``PLAYER_STATE_CHANGED`` (``player``, ``old_state``, ``new_state``,
    ``reason``) on the event bus so observers (metrics, notifications, the
    combat/tick loop) can react without this module coupling to them.

    Args:
        player: the character (anything with a writable ``.db``).
        new_state: a value in ``PLAYER_STATES``.
        reason: short tag for logging/telemetry (``"login"``, ``"enter"``,
            ``"death"``, ``"disconnect"``, ``"grace_expired"``, ...).
        event_bus: optional bus to publish on; falls back to the process
            singleton. Never raises if publishing fails.

    Returns:
        ``True`` if the state changed (or was a valid no-op), ``False`` if the
        move was rejected as illegal (nothing written).
    """
    db = getattr(player, "db", None)
    if db is None:
        return False
    if new_state not in PLAYER_STATES:
        logger.warning("Rejected transition to unknown player state %r", new_state)
        return False

    current = getattr(db, "player_state", None)
    if current == new_state:
        return True  # idempotent no-op (e.g. two disconnect signals)

    if current is not None:
        allowed = PLAYER_STATE_TRANSITIONS.get(current, set())
        if new_state not in allowed:
            logger.warning(
                "Rejected illegal player-state transition %s -> %s (reason=%r)",
                current, new_state, reason,
            )
            return False

    db.player_state = new_state
    _publish_state_changed(player, current, new_state, reason, event_bus)
    logger.info(
        "Player %s: %s -> %s (%s)",
        getattr(player, "key", "?"), current, new_state, reason or "-",
    )
    return True


# ------------------------------------------------------------------ #
#  Login routing (the state-2 router)
# ------------------------------------------------------------------ #

def route_on_login(player: Any, *, event_bus: Any = None) -> str:
    """Resume *player* into the correct state on login, and return that state.

    Called from the authenticated phase (Account/Character post-login) to
    implement the spec's login rules from the single persisted ``player_state``:

    * ``None`` (brand-new or legacy character) → **SPAWNING** (must pick class +
      spawn location before entering);
    * ``SPAWNING`` (disconnected mid-selection, or died and not yet re-spawned)
      → stays **SPAWNING** (resume selection);
    * ``LOBBY`` (existing, non-dead, hadn't entered / had quit to lobby) → stays
      **LOBBY**;
    * ``PLAYING`` (clean state was in-game; e.g. a server crash left it PLAYING)
      or ``LINKDEAD`` (unclean drop) → **PLAYING** (reconnect/resume in place).

    A ``None`` or ``SPAWNING`` result is written via :func:`transition` (so the
    field is concrete after login and the ``PLAYER_STATE_CHANGED`` event fires);
    a ``LINKDEAD`` → ``PLAYING`` resume is likewise a real transition. A
    character already in ``LOBBY``/``PLAYING`` is a no-op that returns the
    current state unchanged.
    """
    current = get_state(player)

    if current is None:
        transition(player, PLAYER_STATE_SPAWNING, reason="login_new",
                   event_bus=event_bus)
        return PLAYER_STATE_SPAWNING

    if current in (PLAYER_STATE_PLAYING, PLAYER_STATE_LINKDEAD):
        # Reconnect / crash-resume: return to play in place. Clear any linkdead
        # grace deadline; the player is back at the keyboard.
        clear_linkdead(player)
        transition(player, PLAYER_STATE_PLAYING, reason="reconnect",
                   event_bus=event_bus)
        return PLAYER_STATE_PLAYING

    # SPAWNING or LOBBY: resume exactly where the player left off.
    return current


# ------------------------------------------------------------------ #
#  Death
# ------------------------------------------------------------------ #

def record_death(player: Any, x: Any, y: Any, planet: Any, *,
                 event_bus: Any = None) -> None:
    """Record *player*'s place of death and route them to SPAWNING.

    Called from the combat death path when a player's HP reaches 0. Stores the
    death tile (so "respawn at place of death" has a target) and transitions
    PLAYING/LINKDEAD → SPAWNING (per the spec, death re-runs stage 3: re-pick
    class + spawn location). Writes only ``death_*`` here; the SPAWNING move
    goes through :func:`transition`.
    """
    db = getattr(player, "db", None)
    if db is not None:
        try:
            db.death_x = int(x) if x is not None else None
            db.death_y = int(y) if y is not None else None
            db.death_planet = planet
        except (TypeError, ValueError):
            db.death_x = db.death_y = None
            db.death_planet = planet
        # Death re-runs the FULL stage 3: clear the prior class + spawn choice so
        # the spawning wizard restarts at step 1 (class), not straight to the
        # spawn step. (Per spec: death → re-pick class + spawn location.)
        db.player_class = None
        db.pending_spawn_choice = None
    transition(player, PLAYER_STATE_SPAWNING, reason="death",
               event_bus=event_bus)


# ------------------------------------------------------------------ #
#  Linkdead grace
# ------------------------------------------------------------------ #

def begin_linkdead(player: Any, now: float, grace_seconds: float, *,
                   event_bus: Any = None) -> bool:
    """Enter LINKDEAD with a grace deadline. Returns True if the move applied.

    Called on an UNCLEAN disconnect (connection dropped without ``quit``) from
    PLAYING. Sets ``db.linkdead_until = now + grace_seconds`` (the wall-clock
    monotonic deadline the tick loop checks) and transitions PLAYING → LINKDEAD.
    A clean ``quit`` must NOT call this — it goes straight to LOBBY.
    """
    db = getattr(player, "db", None)
    if db is not None:
        db.linkdead_until = float(now) + float(grace_seconds)
    return transition(player, PLAYER_STATE_LINKDEAD, reason="disconnect",
                      event_bus=event_bus)


def clear_linkdead(player: Any) -> None:
    """Clear the linkdead grace deadline (does NOT change ``player_state``).

    Called on reconnect (before/around routing back to PLAYING) so a resumed
    player carries no stale deadline. State movement is a separate
    :func:`transition` call — this only resets the timer field.
    """
    db = getattr(player, "db", None)
    if db is not None:
        db.linkdead_until = 0.0


def is_linkdead_expired(player: Any, now: float) -> bool:
    """Return True if *player* is LINKDEAD and its grace deadline has passed.

    The tick loop calls this over lingering linkdead characters; when True it
    should remove the character to the lobby (see :func:`expire_linkdead`).
    """
    if get_state(player) != PLAYER_STATE_LINKDEAD:
        return False
    db = getattr(player, "db", None)
    if db is None:
        return False
    deadline = getattr(db, "linkdead_until", 0.0) or 0.0
    try:
        return float(now) >= float(deadline)
    except (TypeError, ValueError):
        return True  # corrupt deadline: treat as expired so it can't wedge


def expire_linkdead(player: Any, *, event_bus: Any = None) -> bool:
    """Handle grace expiry for a still-alive linkdead player → LOBBY.

    Clears the deadline and transitions LINKDEAD → LOBBY (matching a clean quit:
    next login lands in the lobby). The caller is responsible for the world-side
    removal (unpuppet / pull from the coordinate index); this only advances the
    persisted state. A linkdead player KILLED during grace goes through
    :func:`record_death` instead (→ SPAWNING), not here.
    """
    clear_linkdead(player)
    return transition(player, PLAYER_STATE_LOBBY, reason="grace_expired",
                      event_bus=event_bus)


# ------------------------------------------------------------------ #
#  Lobby / enter (state 4 → 5) and quit (state 5 → 4)
# ------------------------------------------------------------------ #

def enter_game(player: Any, *, event_bus: Any = None) -> bool:
    """Enter the game world from the lobby (transition 4.1): LOBBY → PLAYING."""
    return transition(player, PLAYER_STATE_PLAYING, reason="enter",
                      event_bus=event_bus)


def to_lobby(player: Any, *, reason: str = "quit", event_bus: Any = None) -> bool:
    """Return to the lobby (clean quit): PLAYING → LOBBY."""
    return transition(player, PLAYER_STATE_LOBBY, reason=reason,
                      event_bus=event_bus)


def finish_spawning(player: Any, *, event_bus: Any = None) -> bool:
    """Advance from SPAWNING to the lobby once class + location are chosen.

    Guarded: refuses (returns ``False``) unless the player has actually picked a
    class (``db.player_class``) — the spawn location is applied by the caller
    (it moves the character), but the class selection is the persisted gate. The
    caller checks the return value before showing the lobby.
    """
    db = getattr(player, "db", None)
    if db is None or getattr(db, "player_class", None) is None:
        return False
    return transition(player, PLAYER_STATE_LOBBY, reason="spawned",
                      event_bus=event_bus)


# ------------------------------------------------------------------ #
#  Internal
# ------------------------------------------------------------------ #

def _publish_state_changed(player, old_state, new_state, reason, event_bus):
    """Publish PLAYER_STATE_CHANGED, resolving the bus if not supplied. Never raises."""
    try:
        bus = event_bus
        if bus is None:
            from world.event_bus import event_bus as _bus
            bus = _bus
        from world.event_bus import PLAYER_STATE_CHANGED

        bus.publish(
            PLAYER_STATE_CHANGED,
            player=player,
            old_state=old_state,
            new_state=new_state,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - telemetry must never break a transition
        logger.debug("PLAYER_STATE_CHANGED publish failed", exc_info=True)
