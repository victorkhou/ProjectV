"""
Unit tests for the player-lifecycle commands (commands/lifecycle_commands.py):
the class / spawn selection (state 3) and deploy (state 4) flow.
"""

import sys
import types
import unittest


def _ensure_evennia_stubs():
    if "evennia" in sys.modules and getattr(sys.modules["evennia"], "__file__", None):
        return

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        for k, v in (attrs or {}).items():
            setattr(m, k, v)
        sys.modules.setdefault(name, m)
        return m

    class Command:
        key = ""
        aliases = []
        locks = ""
        help_category = "General"
        def func(self):
            pass

    _mod("evennia")
    _mod("evennia.commands")
    _mod("evennia.commands.command", {"Command": Command})


_ensure_evennia_stubs()

from world.constants import (  # noqa: E402
    PLAYER_STATE_LOBBY,
    PLAYER_STATE_PLAYING,
    PLAYER_STATE_SPAWNING,
)
from world.definitions import ClassDef  # noqa: E402
from commands.lifecycle_commands import (  # noqa: E402
    CmdClass,
    CmdDeploy,
    CmdSpawn,
    require_in_game,
)


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Registry:
    def __init__(self, classes):
        self.classes = {c.key: c for c in classes}

    def resolve_class(self, token):
        t = token.strip().lower()
        for c in self.classes.values():
            if c.key.lower() == t or c.name.lower() == t:
                return c
        matches = [c for c in self.classes.values() if c.key.lower().startswith(t)]
        return matches[0] if len(matches) == 1 else None


class _NDB:
    def __init__(self, systems):
        self.systems = systems
        self.spawn_choice = None


class _Caller:
    def __init__(self, state=None, classes=None):
        self.db = types.SimpleNamespace(
            player_state=state, player_class=None,
            pending_spawn_choice=None, coord_planet="terra",
            coord_x=1, coord_y=1,
        )
        self.ndb = _NDB({"registry": _Registry(classes or [])})
        self._messages = []
        self._executed = []

    def msg(self, text=None, **kw):
        if text is not None:
            self._messages.append(text)

    def execute_cmd(self, cmd):
        self._executed.append(cmd)

    # last message helper
    def last(self):
        return self._messages[-1] if self._messages else ""


_CLASSES = [
    ClassDef(key="vanguard", name="Vanguard", description="Front line."),
    ClassDef(key="engineer", name="Engineer", description="Builder."),
]


def _run(cmd_cls, caller, args=""):
    cmd = cmd_cls()
    cmd.caller = caller
    cmd.args = args
    cmd.func()
    return caller


# -------------------------------------------------------------- #
#  require_in_game guard
# -------------------------------------------------------------- #

class TestRequireInGame(unittest.TestCase):
    def test_playing_allowed(self):
        c = _Caller(state=PLAYER_STATE_PLAYING)
        self.assertTrue(require_in_game(c))

    def test_none_state_allowed(self):
        # No lifecycle state (flow off / legacy char) -> allowed, unchanged.
        c = _Caller(state=None)
        self.assertTrue(require_in_game(c))

    def test_spawning_blocked_with_hint(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        self.assertFalse(require_in_game(c))
        self.assertIn("class", c.last().lower())

    def test_lobby_blocked_with_hint(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        self.assertFalse(require_in_game(c))
        self.assertIn("enter", c.last().lower())


# -------------------------------------------------------------- #
#  class selection (3.2)
# -------------------------------------------------------------- #

class TestCmdClass(unittest.TestCase):
    def test_lists_choices_when_no_arg(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "")
        self.assertIn("Vanguard", c.last())
        self.assertIn("Engineer", c.last())

    def test_sets_class_by_name(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "Vanguard")
        self.assertEqual(c.db.player_class, "vanguard")

    def test_sets_class_by_prefix(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "eng")
        self.assertEqual(c.db.player_class, "engineer")

    def test_unknown_class_reports(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "wizard")
        self.assertIsNone(c.db.player_class)
        self.assertIn("unknown", c.last().lower())

    def test_refused_outside_spawning(self):
        c = _Caller(state=PLAYER_STATE_PLAYING, classes=_CLASSES)
        _run(CmdClass, c, "Vanguard")
        self.assertIsNone(c.db.player_class)


# -------------------------------------------------------------- #
#  spawn selection (3.1)
# -------------------------------------------------------------- #

class TestCmdSpawn(unittest.TestCase):
    def test_lists_options_when_no_arg(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "")
        self.assertIn("hq", c.last().lower())
        self.assertIn("random", c.last().lower())

    def test_sets_choice(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "hq")
        self.assertEqual(c.db.pending_spawn_choice, "hq")

    def test_prefix_choice(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "ran")
        self.assertEqual(c.db.pending_spawn_choice, "random")

    def test_unknown_choice_reports(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "nowhere")
        self.assertIsNone(c.db.pending_spawn_choice)


# -------------------------------------------------------------- #
#  Advance SPAWNING -> LOBBY once both chosen
# -------------------------------------------------------------- #

class TestAdvanceToLobby(unittest.TestCase):
    def test_class_then_spawn_advances_to_lobby(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "Vanguard")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)  # need spawn too
        _run(CmdSpawn, c, "hq")
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_spawn_then_class_advances_to_lobby(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSpawn, c, "random")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        _run(CmdClass, c, "Engineer")
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)


# -------------------------------------------------------------- #
#  deploy (4.1)
# -------------------------------------------------------------- #

class TestCmdDeploy(unittest.TestCase):
    def test_deploy_from_lobby_enters_game(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        _run(CmdDeploy, c, "")
        self.assertEqual(c.db.player_state, PLAYER_STATE_PLAYING)
        self.assertIn("look", c._executed)  # world shown on deploy
        self.assertIsNone(c.db.pending_spawn_choice)  # choice consumed

    def test_deploy_blocked_while_spawning(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdDeploy, c, "")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)
        self.assertIn("class", c.last().lower())

    def test_deploy_when_already_playing(self):
        c = _Caller(state=PLAYER_STATE_PLAYING)
        _run(CmdDeploy, c, "")
        self.assertIn("already in the game", c.last().lower())


if __name__ == "__main__":
    unittest.main()
