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
    CmdSelect,
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

    def execute_cmd(self, cmd, session=None, **kw):
        self._executed.append(cmd)
        self._executed_sessions = getattr(self, "_executed_sessions", [])
        self._executed_sessions.append(session)

    # last message helper
    def last(self):
        return self._messages[-1] if self._messages else ""


_CLASSES = [
    ClassDef(key="vanguard", name="Vanguard", description="Front line."),
    ClassDef(key="engineer", name="Engineer", description="Builder."),
]


def _run(cmd_cls, caller, args="", cmdstring=None, session=None):
    cmd = cmd_cls()
    cmd.caller = caller
    cmd.args = args
    cmd.session = session
    cmd.cmdstring = cmdstring if cmdstring is not None else getattr(cmd_cls, "key", "")
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
    def test_lists_numbered_choices_when_no_arg(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "")
        msg = c.last()
        self.assertIn("Vanguard", msg)
        self.assertIn("Engineer", msg)
        self.assertIn("1", msg)  # numbered
        self.assertIn("2", msg)

    def test_number_selects_class(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "1")  # classes sorted by key: engineer, vanguard
        self.assertEqual(c.db.player_class, "engineer")

    def test_out_of_range_number_reprompts(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdClass, c, "5")
        self.assertIsNone(c.db.player_class)
        self.assertTrue(any("between" in m.lower() for m in c._messages))

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
    def test_lists_numbered_options_when_no_arg(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "")
        msg = c.last()
        # Numbered menu with human labels (not raw keys).
        self.assertIn("1", msg)
        self.assertIn("Headquarters", msg)
        self.assertIn("Random location", msg)

    def test_sets_choice(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "hq")
        self.assertEqual(c.db.pending_spawn_choice, "hq")

    def test_prefix_choice(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "ran")
        self.assertEqual(c.db.pending_spawn_choice, "random")

    def test_number_selects_spawn_option(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "1")  # first option = hq
        self.assertEqual(c.db.pending_spawn_choice, "hq")

    def test_out_of_range_number_reprompts(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING)
        _run(CmdSpawn, c, "9")
        self.assertIsNone(c.db.pending_spawn_choice)
        self.assertTrue(any("between" in m.lower() for m in c._messages))

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
#  Bare-number selection (CmdSelect) — the numbered wizard
# -------------------------------------------------------------- #

class TestCmdSelect(unittest.TestCase):
    def test_bare_number_picks_class_then_spawn_in_order(self):
        # A player types '1' then '1' — first pick is a class, second a spawn.
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSelect, c, args="", cmdstring="1")  # bare '1' -> class[0]
        self.assertEqual(c.db.player_class, "engineer")
        self.assertEqual(c.db.player_state, PLAYER_STATE_SPAWNING)  # spawn still needed
        _run(CmdSelect, c, args="", cmdstring="1")  # bare '1' -> spawn[0] = hq
        self.assertEqual(c.db.pending_spawn_choice, "hq")
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_select_with_arg_form(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSelect, c, args="2", cmdstring="select")  # class[1] = vanguard
        self.assertEqual(c.db.player_class, "vanguard")

    def test_select_noop_outside_spawning(self):
        c = _Caller(state=PLAYER_STATE_PLAYING, classes=_CLASSES)
        _run(CmdSelect, c, args="", cmdstring="1")
        self.assertIsNone(c.db.player_class)
        self.assertIn("nothing to select", c.last().lower())

    def test_select_non_number_reprompts_current_step(self):
        c = _Caller(state=PLAYER_STATE_SPAWNING, classes=_CLASSES)
        _run(CmdSelect, c, args="", cmdstring="select")  # no number given
        # Reprompts the class menu (still on step 1).
        self.assertIn("class", c.last().lower())
        self.assertIsNone(c.db.player_class)

    def test_lobby_select_1_enters_game(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        _run(CmdSelect, c, args="", cmdstring="1")
        self.assertEqual(c.db.player_state, PLAYER_STATE_PLAYING)

    def test_lobby_select_0_quits(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="0")
        self.assertIn("quit", c._executed)  # routed to the quit command
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)  # still lobby

    def test_lobby_select_0_forwards_session_to_quit(self):
        # The quit must carry the invoking session, or CmdQuit (account_caller)
        # crashes on a None session (the reported lobby-quit traceback).
        c = _Caller(state=PLAYER_STATE_LOBBY)
        sentinel_session = object()
        _run(CmdSelect, c, args="", cmdstring="0", session=sentinel_session)
        self.assertEqual(c._executed, ["quit"])
        self.assertEqual(c._executed_sessions, [sentinel_session])

    def test_lobby_select_2_shows_password_hint(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="2")
        self.assertIn("password", c.last().lower())
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_lobby_select_3_shows_chardelete_hint(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="3")
        self.assertIn("chardelete", c.last().lower())
        self.assertEqual(c.db.player_state, PLAYER_STATE_LOBBY)

    def test_lobby_select_other_reprompts_menu(self):
        c = _Caller(state=PLAYER_STATE_LOBBY)
        _run(CmdSelect, c, args="", cmdstring="7")
        self.assertIn("enter the game", c.last().lower())
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

    def test_deploy_clears_lingering_combat_state(self):
        # A player who died/quit mid-fight must re-enter NOT in combat.
        c = _Caller(state=PLAYER_STATE_LOBBY)
        c.db.player_class = "vanguard"
        c.db.pending_spawn_choice = "hq"
        c.db.combat_timer_expires = 9999
        c.db.combat_lockout_tick = 9999
        _run(CmdDeploy, c, "")
        self.assertEqual(c.db.combat_timer_expires, 0)
        self.assertEqual(c.db.combat_lockout_tick, 0)

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
