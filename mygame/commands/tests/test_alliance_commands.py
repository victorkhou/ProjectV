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


# -------------------------------------------------------------- #
#  CmdAdminAlliance — the @alliance admin router
#  (unified-admin-crud task 7.2: EntityAdminRouter subclass driven by
#  the AllianceAdapter; inspect→show / disband→destroy aliases; spawn
#  opted out; set/destroy through the AllianceSystem single writer;
#  kick/transfer/rename extra verbs; read-only perks def scope)
# -------------------------------------------------------------- #

from commands.alliance_commands import CmdAdminAlliance  # noqa: E402
from world.admin.adapter_registry import AdapterRegistry  # noqa: E402

from .router_harness import OutcomeAssertions, RouterCaller
from world.admin.adapters.alliance_adapter import (  # noqa: E402
    AllianceAdapter,
    _DEF_WRITE_OPT_OUT,
    _SPAWN_OPT_OUT,
)


def _admin_record(aid, name, tag, **extra):
    rec = {
        "id": aid, "name": name, "tag": tag, "leader_id": None,
        "officer_ids": [], "member_ids": [], "treasury": {},
        "active_perks": {}, "pending_invites": [], "pending_requests": [],
        "open_join": False,
    }
    rec.update(extra)
    return rec


class _FakeMember:
    def __init__(self, mid, key):
        self.id = mid
        self.key = key


class _AdminFakeAllianceRegistry:
    def __init__(self, records=()):
        self._alliances = {rec["id"]: rec for rec in records}

    def get(self, aid):
        return self._alliances.get(aid)

    def all_alliances(self):
        return list(self._alliances.values())

    def put(self, record):
        self._alliances[record["id"]] = record

    def delete(self, aid):
        self._alliances.pop(aid, None)


class _AdminFakeAllianceSystem:
    """AllianceSystem double exposing the admin single-writer paths."""

    def __init__(self, records=(), members=()):
        self._alliances = _AdminFakeAllianceRegistry(records)
        self._members = {m.id: m for m in members}
        self.calls = []

    def alliance_exists(self, aid):
        return self._alliances.get(aid) is not None

    def _live_members(self, aid):
        rec = self._alliances.get(aid) or {}
        ids = list(rec.get("member_ids", []) or [])
        ids += list(rec.get("officer_ids", []) or [])
        if rec.get("leader_id") is not None:
            ids.append(rec["leader_id"])
        return [self._members[i] for i in ids if i in self._members]

    def compute_alliance_level(self, aid):
        return 2

    def _resolve_member(self, cid):
        return self._members.get(cid)

    def admin_find_member(self, aid, name):
        wanted = str(name or "").lower()
        for member in self._live_members(aid):
            if member.key.lower() == wanted:
                return member
        return None

    def admin_set_alliance_field(self, aid, field, value):
        self.calls.append(("set", aid, field, value))
        rec = self._alliances.get(aid)
        if rec is None:
            return False, "That alliance no longer exists."
        rec[field] = value
        return True, ""

    def admin_disband_alliance(self, aid):
        self.calls.append(("disband", aid))
        if self._alliances.get(aid) is None:
            return False, "That alliance no longer exists."
        self._alliances.delete(aid)
        return True, ""

    def admin_kick_member(self, aid, member):
        self.calls.append(("kick", aid, member.key))
        rec = self._alliances.get(aid)
        if rec is not None and member.id == rec.get("leader_id"):
            return False, (
                "Cannot kick the leader — use '@alliance transfer' to "
                "hand off leadership first, or '@alliance destroy'."
            )
        return True, ""

    def admin_transfer_leadership(self, aid, member):
        self.calls.append(("transfer", aid, member.key))
        return True, ""

    def admin_rename_alliance(self, aid, new_name=None, new_tag=None):
        self.calls.append(("rename", aid, new_name, new_tag))
        return True, ""


_ADMIN_PERKS = {
    "shared_vision": {"category": "vision", "effect_type": "vision",
                      "levels": {1: {"tier": 1}}},
    "shared_bank": {"category": "economy", "effect_type": "treasury",
                    "levels": {1: {"tier": 2}}},
}


class _AdminFakeDataRegistry:
    alliance_perks = _ADMIN_PERKS


class _AdminCaller(RouterCaller):
    """Admin caller: top tier so the perm gate is never what fails here."""

    def __init__(self):
        super().__init__(perms=("Developer",), key="AdminUser")

    def msg(self, text=None, **kwargs):
        if text is not None:
            self.messages.append(text)


class _AdminAllianceRouter(CmdAdminAlliance):
    """Test subclass injecting a per-test AdapterRegistry."""

    registry = None

    def _adapter_registry(self):
        return self.registry


class AdminAllianceRouterTestCase(OutcomeAssertions, unittest.TestCase):
    """Fresh system/adapter/registry/caller per test; shared run helper."""

    def _fresh(self, records=None, members=()):
        if records is None:
            records = [_admin_record(1, "Iron Wolves", "IW"),
                       _admin_record(2, "Coalition", "COAL")]
        system = _AdminFakeAllianceSystem(records, members)
        registry = AdapterRegistry()
        registry.register(AllianceAdapter(
            alliance_system=system, registry=_AdminFakeDataRegistry()))
        return system, registry

    def setUp(self):
        from world import services

        self.system, self.registry = self._fresh()
        # alliance_system also served through the services facade for
        # the router's require_system (kick/transfer/rename).
        ctx = services.override({"alliance_system": self.system})
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def run_cmd(self, args, caller=None, registry=None):
        """Drive the router and return the COMMAND.

        Returning the command rather than the caller is what makes the
        recorded outcomes reachable (they live on the command); ``output()``
        from the harness takes either, so reading the prose is unaffected.
        """
        cmd = _AdminAllianceRouter()
        cmd.registry = registry or self.registry
        cmd.caller = caller or _AdminCaller()
        cmd.args = args
        cmd.cmdstring = cmd.key
        cmd.func()
        return cmd


class TestAdminAllianceIsEntityAdminRouter(AdminAllianceRouterTestCase):
    """The @alliance router is an EntityAdminRouter subclass (task 7.2)."""

    def test_subclass_and_wiring(self):
        from commands.command_router import EntityAdminRouter
        self.assertTrue(issubclass(CmdAdminAlliance, EntityAdminRouter))
        self.assertEqual(CmdAdminAlliance.key, "@alliance")
        self.assertEqual(CmdAdminAlliance.adapter_key, "alliance")
        # The Builder floor is unchanged (locks inherited).
        self.assertIn("perm(Builder)", CmdAdminAlliance.locks)


class TestAdminAllianceList(AdminAllianceRouterTestCase):
    """list renders indexed rows over the live alliances."""

    def test_list_shows_indexed_tag_rows(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("#1", out)
        self.assertIn("[IW] Iron Wolves", out)
        self.assertIn("[COAL] Coalition", out)

    def test_list_filter(self):
        cmd = self.run_cmd(" list coal")
        out = self.output(cmd)
        self.assertIn("COAL", out)
        self.assertNotIn("[IW]", out)


class TestAdminAllianceInspectAlias(AdminAllianceRouterTestCase):
    """inspect == show output + one deprecation note (R11.1, R11.2)."""

    def _run_isolated(self, verb):
        system, registry = self._fresh()
        cmd = self.run_cmd(f" {verb} IW", registry=registry)
        return cmd

    def test_inspect_output_is_show_output_plus_one_line_note(self):
        show_cmd = self._run_isolated("show")
        alias_cmd = self._run_isolated("inspect")
        note = alias_cmd.caller.messages[0]
        self.assertEqual(len(note.splitlines()), 1)
        self.assertIn("'inspect'", note)
        self.assertIn("show", note)
        self.assertIn("deprecated", note)
        # Everything after the note is identical to the canonical verb.
        self.assertEqual(alias_cmd.caller.messages[1:],
                         show_cmd.caller.messages)

    def test_show_renders_identity_state_and_fields(self):
        cmd = self.run_cmd(" show IW")
        out = self.output(cmd)
        self.assertIn("Iron Wolves", out)
        self.assertIn("[IW]", out)
        self.assertIn("Treasury", out)
        self.assertIn("Modifiable fields", out)
        self.assertIn("open_join", out)


class TestAdminAllianceDisbandAlias(AdminAllianceRouterTestCase):
    """disband dispatches destroy through the single writer + note."""

    def test_disband_alias_destroys_with_deprecation_note(self):
        cmd = self.run_cmd(" disband IW")
        note = cmd.caller.messages[0]
        self.assertIn("'disband'", note)
        self.assertIn("destroy", note)
        self.assertIn(("disband", 1), self.system.calls)
        self.assertIn("Destroyed", self.output(cmd))

    def test_destroy_goes_through_admin_disband_alliance(self):
        cmd = self.run_cmd(" destroy COAL")
        self.assertIn(("disband", 2), self.system.calls)
        self.assertIn("Destroyed Coalition (COAL)", self.output(cmd))


class TestAdminAllianceSpawnOptOut(AdminAllianceRouterTestCase):
    """spawn surfaces the founded-by-players reason verbatim (R1.5)."""

    def test_spawn_opt_out_reason_verbatim_no_state_change(self):
        cmd = self.run_cmd(" spawn Wolves")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(_SPAWN_OPT_OUT, out)
        self.assertEqual(self.system.calls, [])


class TestAdminAllianceSet(AdminAllianceRouterTestCase):
    """set writes through AllianceSystem.admin_set_alliance_field (R3.5)."""

    def test_set_name_writes_through_the_system(self):
        cmd = self.run_cmd(" set IW name Steel")
        self.assertIn(("set", 1, "name", "Steel"), self.system.calls)
        self.assertFieldSet(cmd, field="name", applied="Steel")

    def test_set_open_join_enum_coerced_to_bool(self):
        self.run_cmd(" set IW open_join on")
        self.assertIn(("set", 1, "open_join", True), self.system.calls)

    def test_set_invalid_enum_value_rejected_no_write(self):
        cmd = self.run_cmd(" set IW open_join maybe")
        self.assertEqual(self.system.calls, [])
        self.assertIn("valid values", self.output(cmd))

    def test_set_unknown_field_rejected_naming_valid_fields(self):
        cmd = self.run_cmd(" set IW treasury 999")
        self.assertEqual(self.system.calls, [])
        self.assertUnknownField(cmd, field="treasury", plane="instance",
                                valid=("open_join",))


class TestAdminAllianceExtraVerbs(AdminAllianceRouterTestCase):
    """kick/transfer/rename mutate via the AllianceSystem admin paths."""

    def setUp(self):
        super().setUp()
        self.boss = _FakeMember(100, "Boss")
        self.grunt = _FakeMember(200, "Grunt")
        rec = self.system._alliances.get(1)
        rec["leader_id"] = self.boss.id
        rec["member_ids"] = [self.grunt.id]
        self.system._members = {m.id: m for m in (self.boss, self.grunt)}

    def test_kick_routes_through_admin_kick_member(self):
        cmd = self.run_cmd(" kick IW Grunt")
        self.assertIn(("kick", 1, "Grunt"), self.system.calls)
        self.assertIn("Force-kicked Grunt from [IW]", self.output(cmd))

    def test_kick_leader_refusal_relayed(self):
        cmd = self.run_cmd(" kick IW Boss")
        self.assertIn("Cannot kick the leader", self.output(cmd))

    def test_kick_unknown_member_errors(self):
        cmd = self.run_cmd(" kick IW Nobody")
        self.assertIn("No member 'Nobody'", self.output(cmd))

    def test_transfer_routes_through_admin_transfer_leadership(self):
        cmd = self.run_cmd(" transfer IW Grunt")
        self.assertIn(("transfer", 1, "Grunt"), self.system.calls)
        self.assertIn("Transferred [IW] leadership to Grunt",
                      self.output(cmd))

    def test_rename_routes_through_admin_rename_alliance(self):
        cmd = self.run_cmd(" rename IW Steel Wolves = SW")
        self.assertIn(("rename", 1, "Steel Wolves", "SW"),
                      self.system.calls)
        self.assertIn("Renamed to [SW] Steel Wolves", self.output(cmd))

    def test_rename_usage_without_equals(self):
        cmd = self.run_cmd(" rename IW Steel Wolves")
        self.assertIn("Usage", self.output(cmd))


class TestAdminAllianceDefScope(AdminAllianceRouterTestCase):
    """def list/show serve the perks catalog; def writes are opted out."""

    def test_def_list_serves_the_perks_catalog(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("shared_vision", out)
        self.assertIn("shared_bank", out)

    def test_def_show_renders_one_perk(self):
        cmd = self.run_cmd(" def show shared_vision")
        out = self.output(cmd)
        self.assertIn("shared_vision", out)
        self.assertIn("vision", out)

    def test_def_set_and_reset_surface_the_opt_out_reason_verbatim(self):
        for sub in ("set shared_vision category x", "reset shared_vision"):
            cmd = self.run_cmd(f" def {sub}")
            out = self.output(cmd)
            self.assertIn("not available", out)
            self.assertIn(_DEF_WRITE_OPT_OUT, out)


if __name__ == "__main__":
    unittest.main()
