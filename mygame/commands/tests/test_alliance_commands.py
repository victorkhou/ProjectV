"""
Unit tests for the alliance command router (CmdAlliance).

Verifies verb routing to the AllianceSystem, the verb-aware lobby gate
(MUTATING-lobby verbs refused in SPAWNING, read-only trio allowed OOC, other
verbs refused from the lobby), the combat gate on side-changing verbs, and that
the info/board/leaderboard views render. Drives the command with fakes and a
fake AllianceSystem installed through the services facade.
"""

import sys
import types
import unittest


def _ensure_evennia_stubs():
    if "evennia" in sys.modules and getattr(sys.modules["evennia"], "__file__", None):
        return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        for k, v in (attrs or {}).items():
            setattr(m, k, v)
        stubs[name] = m
        return m

    class Command:
        key = ""
        aliases = []
        locks = ""
        help_category = "General"

        def at_pre_cmd(self):
            return False

        def func(self):
            pass

    _mod("evennia")
    _mod("evennia.commands")
    _mod("evennia.commands.command", {"Command": Command})
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from commands.alliance_commands import CmdAlliance  # noqa: E402


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Db(types.SimpleNamespace):
    def __getattr__(self, _):
        return None


class _Caller:
    def __init__(self, alliance=None, state="playing"):
        self.key = "Caller"
        self.id = 1
        self.messages = []
        self.db = _Db(player_alliance=alliance, player_state=state)

    def msg(self, text, **kw):
        self.messages.append(text)

    def search(self, name, **kw):
        return None  # not used by the verb-routing tests


class _RecordingAllianceSystem:
    """Records verb calls; the router should delegate to these."""

    def __init__(self):
        self.calls = []
        self._alliances = None

    def _rec(self, name):
        def _fn(*a, **k):
            self.calls.append((name, a, k))
            return True
        return _fn

    def __getattr__(self, name):
        # Any method the router calls is recorded.
        return self._rec(name)

    # A few methods need real-ish returns for the info/board/leaderboard views.
    def pending_invites_for(self, player):
        self.calls.append(("pending_invites_for", (player,), {}))
        return []

    def tag_for(self, player):
        return "TAG"


def _make(caller, args):
    cmd = CmdAlliance()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = "alliance"
    return cmd


class _AllianceCmdBase(unittest.TestCase):
    def setUp(self):
        from world import services

        self.system = _RecordingAllianceSystem()
        ctx = services.override({"alliance_system": self.system})
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)


# -------------------------------------------------------------- #
#  Verb routing
# -------------------------------------------------------------- #

class TestVerbRouting(_AllianceCmdBase):
    def _verbs_called(self):
        return [c[0] for c in self.system.calls]

    def test_found_parses_name_and_tag(self):
        _make(_Caller(), "found Iron Wolves = IW").func()
        self.assertIn("found", self._verbs_called())
        name, tag = self.system.calls[0][1][1], self.system.calls[0][1][2]
        self.assertEqual(name, "Iron Wolves")
        self.assertEqual(tag, "IW")

    def test_leave_routes(self):
        _make(_Caller(alliance=1), "leave").func()
        self.assertIn("leave", self._verbs_called())

    def test_deposit_parses_costs(self):
        _make(_Caller(alliance=1), "deposit 30 iron 10 wood").func()
        self.assertIn("deposit", self._verbs_called())
        costs = self.system.calls[0][1][1]
        self.assertEqual(costs, {"Iron": 30, "Wood": 10})

    def test_open_parses_flag(self):
        _make(_Caller(alliance=1), "open on").func()
        name, args, _ = self.system.calls[0]
        self.assertEqual(name, "set_open_join")
        self.assertIs(args[1], True)

    def test_unknown_verb_reports(self):
        c = _Caller(alliance=1)
        _make(c, "frobnicate").func()
        self.assertTrue(any("Unknown subcommand" in m for m in c.messages))


# -------------------------------------------------------------- #
#  Verb-aware lobby + combat gates
# -------------------------------------------------------------- #

class TestGates(_AllianceCmdBase):
    def setUp(self):
        super().setUp()
        # Force the lobby flow ON so the gate is active.
        import world.lobby_flow as lf
        self._orig_enabled = lf.lobby_flow_enabled
        lf.lobby_flow_enabled = lambda: True

    def tearDown(self):
        import world.lobby_flow as lf
        lf.lobby_flow_enabled = self._orig_enabled
        super().tearDown()

    def test_readonly_verb_allowed_in_spawning(self):
        cmd = _make(_Caller(state="spawning"), "leaderboard")
        self.assertFalse(cmd.at_pre_cmd())  # allowed (False = don't abort)

    def test_mutating_verb_refused_in_spawning(self):
        c = _Caller(state="spawning")
        cmd = _make(c, "found A = B")
        self.assertTrue(cmd.at_pre_cmd())  # aborted
        self.assertTrue(any("choosing your character" in m for m in c.messages))

    def test_mutating_verb_allowed_in_lobby(self):
        cmd = _make(_Caller(state="lobby"), "found A = B")
        self.assertFalse(cmd.at_pre_cmd())

    def test_ingame_only_verb_refused_from_lobby(self):
        c = _Caller(state="lobby")
        cmd = _make(c, "deposit 10 iron")
        self.assertTrue(cmd.at_pre_cmd())
        self.assertTrue(any("in-game only" in m for m in c.messages))

    def test_side_changing_verb_refused_in_combat(self):
        import world.combat_timer as ct
        orig = ct.player_in_combat
        ct.player_in_combat = lambda char: True
        try:
            c = _Caller(alliance=1, state="playing")
            cmd = _make(c, "leave")
            self.assertTrue(cmd.at_pre_cmd())
            self.assertTrue(any("in combat" in m for m in c.messages))
        finally:
            ct.player_in_combat = orig

    def test_deposit_not_combat_gated(self):
        import world.combat_timer as ct
        orig = ct.player_in_combat
        ct.player_in_combat = lambda char: True
        try:
            cmd = _make(_Caller(alliance=1, state="playing"), "deposit 10 iron")
            self.assertFalse(cmd.at_pre_cmd())  # allowed despite combat
        finally:
            ct.player_in_combat = orig

    # Fix #1 — membership-ADDING verbs (accept/join) are combat-gated too, so a
    # player can't flip allied mid-fight to silence turrets/guards.
    def test_accept_and_join_refused_in_combat(self):
        import world.combat_timer as ct
        orig = ct.player_in_combat
        ct.player_in_combat = lambda char: True
        try:
            for verb in ("accept COAL", "join COAL"):
                c = _Caller(state="playing")
                cmd = _make(c, verb)
                self.assertTrue(cmd.at_pre_cmd(), f"{verb} must be combat-gated")
                self.assertTrue(any("in combat" in m for m in c.messages))
        finally:
            ct.player_in_combat = orig

    # Fix #8 — the combat gate holds even when the lobby flow is DISABLED.
    def test_combat_gate_independent_of_lobby_flow(self):
        import world.lobby_flow as lf
        import world.combat_timer as ct
        orig_flow, orig_combat = lf.lobby_flow_enabled, ct.player_in_combat
        lf.lobby_flow_enabled = lambda: False   # flag flipped off
        ct.player_in_combat = lambda char: True
        try:
            c = _Caller(alliance=1, state="playing")
            cmd = _make(c, "leave")
            self.assertTrue(cmd.at_pre_cmd(),
                            "combat gate must hold with lobby flow off")
            self.assertTrue(any("in combat" in m for m in c.messages))
        finally:
            lf.lobby_flow_enabled = orig_flow
            ct.player_in_combat = orig_combat


if __name__ == "__main__":
    unittest.main()
