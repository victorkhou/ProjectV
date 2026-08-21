"""Player status prompt — the classic MUD status line + webclient footer feed.

Renders the player's HP, level, position, and terrain into a status prompt.
Called by two paths so the display never drifts:

* the post-cmd hook refreshes it after every player command;
* the NotificationPresenter pushes it on server-driven HP/level changes.

Dual-push OOB delivery per session:
  - printed text line (``cls="prompt-line"``) — visible on telnet/SSH and
    the webclient Text view; dropped in Map view (footer covers it).
  - ``prompt=`` — input-line prompt for Mudlet/TinTin++.
  - ``prompt_status=`` — structured OOB for the webclient map footer.
"""

from __future__ import annotations

from typing import Any

from world.ui_formatters import format_xp_bar, xp_progress
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

    in_combat, combat_secs = _combat_state(db)

    fields = {
        "hp": int(getattr(db, "hp", 0) or 0),
        "hp_max": int(getattr(db, "hp_max", 0) or 0),
        "level": int(getattr(db, "level", None) or 1),
        "x": x,
        "y": y,
        "planet": planet,
        "terrain": terrain,
        "in_combat": in_combat,
        "combat_secs": combat_secs,
    }

    # XP-in-level progress for the prompt bar. Also emitted on the structured
    # ``prompt_status`` OOB, which the webclient footer can render — it does not
    # today, so treat these as available-but-unrendered rather than live.
    # A maxed player (or an unresolvable curve) yields None and no bar.
    #
    # The level is re-read from xp_progress so the number beside the bar always
    # describes the SAME level the bar was computed for: a legacy character with
    # only ``db.rank_level`` set resolves through get_player_level there, and
    # would otherwise render "Lv 1" next to a level-11 bar.
    prog = xp_progress(player)
    if prog is not None:
        fields["level"] = prog["level"]
        fields["xp_percent"] = round(prog["percent"], 1)
        fields["xp_into_level"] = prog["into_level"]
        fields["xp_level_span"] = prog["level_span"]

    return fields


def _combat_state(db: Any) -> tuple[bool, int]:
    """Return ``(in_combat, seconds_remaining)`` from the combat timer.

    "In combat" means ``db.combat_timer_expires`` (a tick number, set by
    :mod:`world.combat_timer`) is strictly in the future. The remaining seconds
    are ``(expiry - current_tick) * tick_interval``, rounded up so a partial
    tick still reads as ``1s`` rather than ``0s``. Fully guarded: any read
    failure returns ``(False, 0)`` so the prompt never breaks on a combat-timer
    hiccup.
    """
    try:
        expiry = int(getattr(db, "combat_timer_expires", 0) or 0)
        if expiry <= 0:
            return False, 0
        from world.combat_timer import _get_current_tick
        current = _get_current_tick()
        remaining_ticks = expiry - current
        if remaining_ticks <= 0:
            return False, 0
        interval = 1.0
        try:
            bal = get_game_systems().get("registry")
            if bal is not None:
                interval = float(getattr(bal.balance, "tick_interval", 1.0) or 1.0)
        except Exception:  # noqa: BLE001 - tick_interval is optional
            interval = 1.0
        import math
        secs = int(math.ceil(remaining_ticks * interval))
        return True, max(1, secs)
    except Exception:  # noqa: BLE001 - the prompt never breaks on a timer read
        return False, 0


def format_status_line(fields: dict) -> str:
    """Format the telnet status line from :func:`status_fields` output.

    e.g. ``[HP 100/500] [Lv 5] [(25,25) terra] [Plains] [!Combat 42s]`` — each
    field in white brackets, HP colored by fraction (green >=60%, yellow >=30%,
    red below). The combat segment appears (in red) only while the player is in
    combat, showing the seconds until the combat timer clears.
    """
    hp, hp_max = fields["hp"], fields["hp_max"]
    frac = (hp / hp_max) if hp_max > 0 else 0.0
    hp_col = "|g" if frac >= 0.6 else ("|y" if frac >= 0.3 else "|r")
    # Level segment carries a compact XP bar when progress is known — e.g.
    # "Lv 5 ███..." — so the player sees the bar fill as they act. Rendered
    # BARE (no brackets, no percent) to keep the whole prompt inside 80
    # columns, and omitted entirely while in combat, where the combat segment
    # takes the remaining budget. A maxed player has no xp_percent and shows
    # the plain level.
    lvl_seg = f"Lv {fields['level']}"
    xp_pct = fields.get("xp_percent")
    if xp_pct is not None and not fields.get("in_combat"):
        lvl_seg = f"Lv {fields['level']} {format_xp_bar(xp_pct, width=6, bare=True)}"

    segs = [
        f"HP {hp_col}{hp}/{hp_max}|n",
        lvl_seg,
        f"({fields['x']},{fields['y']}) {fields['planet']}",
    ]
    terrain = fields.get("terrain")
    if terrain:
        segs.append(terrain.replace("_", " "))
    if fields.get("in_combat"):
        secs = int(fields.get("combat_secs", 0) or 0)
        segs.append(f"|r!Combat {secs}s|n")
    return " ".join(f"|w[|n{s}|w]|n" for s in segs)


def send_status(player: Any) -> None:
    """Full status refresh after a player's own command (printed line + OOB).

    Broadcasts the printed status line to every session, plus the ``prompt=`` and
    ``prompt_status=`` OOB channels — see the module docstring for why each
    channel exists. Telnet/SSH show the printed line directly; the webclient's
    ``custom_out`` renders it inline in Text view and drops it in Map view (where
    the footer already shows the same fields). A no-op when there's nothing to
    show or the player can't be messaged. Never raises: a prompt hiccup must not
    surface as a command error.
    """
    try:
        fields = status_fields(player)
        if fields is None or not hasattr(player, "msg"):
            return
        text = format_status_line(fields)
        # Broadcast to all sessions (no session= targeting). Per-client
        # presentation of the printed line is decided client-side. A leading
        # newline sets the prompt apart from the command's own output — a blank
        # line on telnet/SSH, and (since the pipeline converts "\n" to <br>) in
        # the webclient's inline Text view too. The bare `prompt=` keeps no
        # leading newline: prompt-aware clients render it in a dedicated area.
        player.msg(text=("\n" + text, {"cls": "prompt-line"}))
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
