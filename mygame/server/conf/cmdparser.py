"""
Command parser override — NOT wired.

Evennia's own ``evennia.commands.cmdparser.cmdparser`` is used (the default
``COMMAND_PARSER``). Per-command argument parsing belongs in
``Command.parse``; this module is only for replacing the global
cmdname-matching pass, which is rarely necessary.

To do so, implement ``cmdparser(raw_string, cmdset, caller, match_index=None)``
returning ``[(cmdname, args, cmdobj, cmdlen, mratio), ...]`` and set::

    COMMAND_PARSER = "server.conf.cmdparser.cmdparser"
"""
