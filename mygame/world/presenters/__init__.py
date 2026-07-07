"""
Presenters — turn domain notification events into player-facing strings.

A presenter subscribes to the ``PLAYER_NOTIFICATION`` event, formats the line
for its ``kind``, and delivers it through a :class:`PlayerNotifier`. This keeps
*all* presentation (string composition + transport) out of the use-case
systems: a system emits structured data ("this player, this kind, these
values") and never composes or sends text.
"""
