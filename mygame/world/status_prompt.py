"""Player status prompt — the classic MUD status line + webclient footer feed.

One source of truth for the player's status readout (HP, level, position, and
the terrain underfoot), shared by two callers so they never drift:

* the command post-hook (:meth:`commands.game_commands.GameCommand.at_post_cmd`)
  refreshes it after every command the player issues; and
* the :class:`~world.presenters.notification_presenter.NotificationPresenter`
  pushes it whenever a *server-driven* event changes the player's HP/level
  (being attacked, healed, levelling) — so the webclient footer updates live
  even when the player never typed anything.

Delivery is per-channel so each client shows it once and only once:

* a printed text line tagged ``cls="prompt-line"`` — the visible classic MUD
  prompt. Sent to TELNET/SSH sessions only: a bare ``prompt=`` OOB is unreliable
  on raw telnet (with Evennia's default ``NOGOAHEAD`` no telnet GA is emitted,
  so basic clients swallow the promptless line). The webclient must NOT get this
  line — it already shows the same fields in its map footer, so a printed copy
  would duplicate the map/footer info in the text panel.
* ``prompt=`` — the same text as an input-line prompt, for capable clients
  (Mudlet/TinTin++) with a dedicated prompt area. Hidden in the webclient (CSS
  hides ``#prompt``; ``custom_out`` also drops it).
* ``prompt_status=`` — a structured OOB the webclient's ``map_renderer`` folds
  into the map footer, so HP/level/position refresh on every update. Telnet
  ignores it.
"""

from __future__ import annotations

from typing import Any

from world.utils import get_game_systems, coords_of


def status_fields(player: Any) -> dict | None:
    """Return the status fields for *player* as a dict, or ``None``.

    Fields: current/max health, level, coordinates + planet, and the terrain
    under the player's feet (the player's own tile is always known, so terrain
    needs no discovery gate).

    Returns ``None`` when there is nothing meaningful to show — the caller isn't
    a positioned player, or is still OOC in the spawning/lobby flow — so callers
    skip the prompt in those states (a prompt during the spawn wizard is noise).
    """
    db = getattr(player, "db", None)
    if db is None:
        return None

    # Suppress while the player is staging (SPAWNING/LOBBY) — a prompt only makes
    # sense once they're deployed. A no-op when the lobby flow is off or the
    # character is legacy (state None), matching player_is_present's gate.
    try:
        from world.lobby_flow import lobby_flow_enabled
        if lobby_flow_enabled():
            from world import player_lifecycle as pl
            from world.constants import PLAYER_STATE_PLAYING
            if pl.get_state(player) not in (PLAYER_STATE_PLAYING, None):
                return None
    except Exception:  # noqa: BLE001 - gate never blocks the prompt
        pass

    coords = coords_of(player)
    if coords is None or not coords[2]:
        return None
    x, y, planet = coords

    terrain = ""
    try:
        gens = get_game_systems().get("_terrain_generators", {})
        gen = gens.get(planet)
        if gen:
            terrain = gen.get_terrain(int(x), int(y)) or ""
    except Exception:  # noqa: BLE001 - terrain is optional in the prompt
        terrain = ""

    return {
        "hp": int(getattr(db, "hp", 0) or 0),
        "hp_max": int(getattr(db, "hp_max", 0) or 0),
        "level": int(getattr(db, "level", None) or 1),
        "x": x,
        "y": y,
        "planet": planet,
        "terrain": terrain,
    }


def format_status_line(fields: dict) -> str:
    """Format the telnet status line from :func:`status_fields` output.

    e.g. ``[HP 100/500] [Lv 5] [(25,25) terra] [Plains]`` — each field in white
    brackets, HP colored by fraction (green >=60%, yellow >=30%, red below).
    """
    hp, hp_max = fields["hp"], fields["hp_max"]
    frac = (hp / hp_max) if hp_max > 0 else 0.0
    hp_col = "|g" if frac >= 0.6 else ("|y" if frac >= 0.3 else "|r")
    segs = [
        f"HP {hp_col}{hp}/{hp_max}|n",
        f"Lv {fields['level']}",
        f"({fields['x']},{fields['y']}) {fields['planet']}",
    ]
    terrain = fields.get("terrain")
    if terrain:
        segs.append(terrain.replace("_", " "))
    return " ".join(f"|w[|n{s}|w]|n" for s in segs)


#: Substrings identifying a WEBCLIENT session's ``protocol_key`` (Evennia uses
#: "webclient/websocket" and "webclient/ajax"). The webclient shows the status
#: fields in its map footer, so it must NOT also get the printed line.
_WEBCLIENT_PROTOCOL_MARKERS = ("webclient", "ajax", "websocket")


def _printed_line_sessions(player: Any):
    """Sessions that should receive the PRINTED status line.

    Fails OPEN: everything EXCEPT sessions positively identified as a webclient.
    The printed line's whole point is telnet visibility, so if a protocol can't
    be classified we still send it (a stray duplicate on some exotic client is
    far better than telnet showing no prompt at all). Only a session whose
    ``protocol_key`` is unmistakably a webclient is excluded — its map footer
    already shows the same fields, fed by ``prompt_status``.

    Returns a list of sessions to target, or ``None`` when sessions can't be
    enumerated (e.g. a test double) — the caller then falls back to a plain
    ``msg`` (telnet-style broadcast), which is also what tests capture.
    """
    try:
        sessions = list(player.sessions.all())
    except Exception:  # noqa: BLE001 - test doubles have no session handler
        return None
    if not sessions:
        return None
    return [s for s in sessions if not _is_webclient_session(s)]


def _is_webclient_session(session: Any) -> bool:
    """True only when *session* is unmistakably a webclient (see markers)."""
    key = str(getattr(session, "protocol_key", "")).lower()
    return any(marker in key for marker in _WEBCLIENT_PROTOCOL_MARKERS)


def send_status(player: Any) -> None:
    """Full status refresh after a player's own command (printed line + OOB).

    Sends the printed status line (telnet/ssh only), the ``prompt=`` OOB, and the
    ``prompt_status=`` OOB — see the module docstring for why each channel. A
    no-op when there's nothing to show or the player can't be messaged. Never
    raises: a prompt hiccup must not surface as a command error.
    """
    try:
        fields = status_fields(player)
        if fields is None or not hasattr(player, "msg"):
            return
        text = format_status_line(fields)
        sessions = _printed_line_sessions(player)
        if sessions is None:
            # Can't enumerate sessions (test double / no handler) → plain send,
            # so telnet-style clients still get the visible prompt line.
            player.msg(text=(text, {"cls": "prompt-line"}))
        elif sessions:
            # Every non-webclient session gets the printed line; webclient
            # sessions are excluded (their footer already shows the same fields).
            player.msg(text=(text, {"cls": "prompt-line"}), session=sessions)
        player.msg(prompt=text)
        player.msg(prompt_status=fields)
    except Exception:  # noqa: BLE001 - prompt must never break a command
        pass


def push_status(player: Any) -> None:
    """Lightweight status push for a server-driven HP/level change.

    Sends only the OOB channels (``prompt=`` + ``prompt_status=``) — NOT the
    printed line — so the webclient footer (and any prompt-aware telnet client)
    reflects the new HP immediately, without spamming the telnet scrollback with
    a status line on every incoming hit. Never raises.
    """
    try:
        fields = status_fields(player)
        if fields is None or not hasattr(player, "msg"):
            return
        player.msg(prompt=format_status_line(fields))
        player.msg(prompt_status=fields)
    except Exception:  # noqa: BLE001 - a status hiccup must not break combat
        pass
