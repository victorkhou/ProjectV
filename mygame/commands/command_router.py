"""
Subcommand router base classes for the RTS Combat Overworld.

Provides a consistent dispatch pattern: parse verb → look up handler →
check permission → invoke handler.  Subclasses declare a ``subcommands``
dict mapping verb strings to ``(handler, help_text, required_perm)`` tuples.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 8.2
"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand

from commands.game_commands import GameCommand

logger = logging.getLogger("mygame.admin")


class SubcommandRouter(BaseCommand):
    """
    Base class for commands that dispatch to subcommand handler methods.

    Subclasses define a ``subcommands`` dict mapping verb strings to
    ``(handler_method, help_text, required_perm)`` tuples.
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


class GameSubcommandRouter(GameCommand):
    """
    Base class for game commands that use subcommand routing with prefix
    matching.

    Inherits ``GameCommand`` for prefix matching and implements the same
    dispatch logic as ``SubcommandRouter`` via composition (overriding
    ``func()`` directly) to avoid MRO conflicts.

    Sets ``help_category = "Game"``.

    Requirements: 5.10, 6.1, 6.2, 6.3
    """

    help_category = "Game"

    # Subclasses override this:
    # subcommands = {
    #     "list": (sub_list, "List your agents", ""),
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
