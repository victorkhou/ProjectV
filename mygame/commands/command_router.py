"""
Subcommand router base classes for the RTS Combat Overworld.

Provides a consistent dispatch pattern: parse verb → look up handler →
check permission → invoke handler.  Subclasses declare a ``subcommands``
dict mapping verb strings to ``(handler, help_text, required_perm)`` tuples.

The dispatch logic lives in :class:`SubcommandDispatchMixin` so it can be
shared by both the plain (:class:`SubcommandRouter`) and prefix-matching
(:class:`GameSubcommandRouter`) router bases without copy-paste.  The mixin
also exposes two guard helpers every handler needs — :meth:`require_system`
and :meth:`parse_int` — so individual ``sub_*`` methods don't re-implement the
"look up system / msg on failure" and "parse int arg / msg on failure"
boilerplate.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 8.2
"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand

from commands.game_commands import GameCommand
from world.utils import get_system

logger = logging.getLogger("mygame.admin")


class SubcommandDispatchMixin:
    """Shared verb-dispatch behavior + handler guard helpers.

    A pure mixin (extends ``object`` only) so it can be combined with either
    ``BaseCommand`` or ``GameCommand`` without MRO conflicts.  It relies on the
    command instance providing ``self.args``, ``self.caller`` and ``self.key``
    (both Evennia command bases do).
    """

    # Subclasses override this:
    # subcommands = {
    #     "spawn": (sub_spawn, "Spawn a building", "Builder"),
    #     "destroy": (sub_destroy, "Destroy a building", "Builder"),
    # }
    subcommands: dict = {}

    def func(self):
        verb, rest = self._get_subcommand_and_args()
        if verb is None:
            self._show_help()
            return
        entry = self.subcommands.get(verb)
        if entry is None:
            self._show_error(verb)
            return
        handler, _help_text, perm = entry
        if perm and not self._check_sub_perm(perm, verb):
            return
        handler(self, rest)

    def _get_subcommand_and_args(self) -> tuple:
        """Parse first token as verb, remainder as args. Case-insensitive."""
        raw = self.args.strip()
        if not raw:
            return None, ""
        parts = raw.split(None, 1)
        verb = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        return verb, rest

    def _show_help(self):
        """Display help listing all subcommands."""
        lines = [f"|wUsage: {self.key} <subcommand> [args]|n", ""]
        for verb, (_, help_text, perm) in self.subcommands.items():
            perm_tag = f" ({perm}+)" if perm else ""
            lines.append(f"  |c{verb}|n — {help_text}{perm_tag}")
        self.caller.msg("\n".join(lines))

    def _show_error(self, invalid_verb: str):
        """Display error for unknown subcommand with valid list."""
        valid = ", ".join(self.subcommands.keys())
        self.caller.msg(
            f"Unknown subcommand '{invalid_verb}'. "
            f"Available: {valid}"
        )

    def _check_sub_perm(self, perm: str, verb: str) -> bool:
        """Check caller permission; msg on failure. Returns True if allowed."""
        if self.caller.check_permstring(perm):
            return True
        self.caller.msg(
            f"Permission denied. {perm}+ required for '{verb}'."
        )
        return False

    def _log_admin(self, verb: str, detail: str):
        """Log admin action: operator, command+verb, target."""
        logger.info(
            "Admin %s: %s %s — %s",
            self.caller.key, self.key, verb, detail,
        )

    # ------------------------------------------------------------------ #
    #  Handler guard helpers (shared by every sub_* method)
    # ------------------------------------------------------------------ #

    def require_system(self, name: str, label: str | None = None):
        """Return game system ``name``, or msg the caller and return ``None``.

        Collapses the ``system = get_system(...); if system is None: msg;
        return`` boilerplate that every handler needs.  The generated message
        is ``"{label} unavailable."`` where ``label`` defaults to the system
        name with underscores spaced out (``"agent_system"`` → ``"Agent
        system unavailable."``).

        Args:
            name: System key to look up via ``world.utils.get_system``.
            label: Optional human-readable name for the failure message.

        Returns:
            The system instance, or ``None`` (after messaging the caller).
        """
        system = get_system(self.caller, name)
        if system is None:
            pretty = label or name.replace("_", " ").capitalize()
            self.caller.msg(f"{pretty} unavailable.")
            return None
        return system

    def parse_int(self, raw, label: str = "Agent ID"):
        """Parse ``raw`` as an int, or msg the caller and return ``None``.

        Collapses the ``try: int(x) except ValueError: msg; return`` guard
        repeated across every id/count-parsing handler.  The failure message
        is ``"{label} must be a number."``.

        Args:
            raw: The raw string (or value) to convert.
            label: Subject of the failure message (e.g. ``"Agent ID"``).

        Returns:
            The parsed ``int``, or ``None`` (after messaging the caller).
        """
        try:
            return int(raw)
        except (TypeError, ValueError):
            self.caller.msg(f"{label} must be a number.")
            return None


class SubcommandRouter(SubcommandDispatchMixin, BaseCommand):
    """
    Base class for commands that dispatch to subcommand handler methods.

    Subclasses define a ``subcommands`` dict mapping verb strings to
    ``(handler_method, help_text, required_perm)`` tuples.  All dispatch
    behavior comes from :class:`SubcommandDispatchMixin`.
    """


class AdminSubcommandRouter(SubcommandRouter):
    """
    Base class for admin commands that use subcommand routing.

    Sets help_category to "Admin" and locks to Builder+ (the lowest admin
    level).  Individual subcommands enforce stricter permissions via
    ``_check_sub_perm``.

    Requirements: 1.5, 2.7, 2.8, 3.5, 3.6, 4.5
    """

    help_category = "Admin"
    locks = "cmd:perm(Builder);view:perm(Builder)"


class GameSubcommandRouter(SubcommandDispatchMixin, GameCommand):
    """
    Base class for game commands that use subcommand routing with prefix
    matching.

    Inherits ``GameCommand`` for prefix matching and gets its dispatch logic
    from :class:`SubcommandDispatchMixin` (a pure mixin, so combining it with
    ``GameCommand`` raises no MRO conflict).

    Sets ``help_category = "Game"``.

    Requirements: 5.10, 6.1, 6.2, 6.3
    """

    help_category = "Game"
