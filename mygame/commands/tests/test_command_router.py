"""
Property-based tests for SubcommandRouter dispatch.

Property 1: Subcommand dispatch correctness — for any registered verb and
any args string, func() invokes the correct handler with the remaining args.
**Validates: Requirements 6.1, 6.2, 6.3**

Property 2: Invalid subcommand error — for any string not in the subcommands
dict, func() produces an error containing all valid subcommand names.
**Validates: Requirements 6.4**

Property 3: Case-insensitive verb matching — for any case variation of a
registered verb, func() dispatches to the same handler.
**Validates: Requirements 6.6**
"""

import sys
import types
import unittest

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
# -------------------------------------------------------------- #


def _ensure_evennia_stubs():
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.command", {
        "Command": type("Command", (), {"func": lambda self: None}),
    })
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {
        "DefaultScript": type("DefaultScript", (), {}),
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from mygame.commands.command_router import SubcommandRouter  # noqa: E402

# -------------------------------------------------------------- #
#  Test helpers
# -------------------------------------------------------------- #


class FakeCaller:
    """Minimal caller mock with msg() and check_permstring()."""

    def __init__(self):
        self.key = "TestPlayer"
        self._messages = []

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def check_permstring(self, perm):
        return True


def _handler_a(self, args):
    """Handler stub that records its call."""
    self._called_handler = ("a", args)


def _handler_b(self, args):
    """Handler stub that records its call."""
    self._called_handler = ("b", args)


def _handler_c(self, args):
    """Handler stub that records its call."""
    self._called_handler = ("c", args)


class TestRouter(SubcommandRouter):
    """Concrete router subclass with known subcommands for testing."""

    key = "@test"
    subcommands = {
        "alpha": (_handler_a, "Do alpha things", ""),
        "beta": (_handler_b, "Do beta things", ""),
        "gamma": (_handler_c, "Do gamma things", ""),
    }


REGISTERED_VERBS = list(TestRouter.subcommands.keys())
HANDLER_MAP = {
    "alpha": "a",
    "beta": "b",
    "gamma": "c",
}


def _make_router(args_str):
    """Create a TestRouter wired to a fake caller with given args."""
    cmd = TestRouter()
    cmd.caller = FakeCaller()
    cmd.args = args_str
    cmd._called_handler = None
    return cmd


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

# Printable text that won't contain control chars or be empty whitespace-only
safe_args_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters="\x00",
    ),
    min_size=0,
    max_size=50,
)

# Strategy for picking a registered verb
verb_strategy = st.sampled_from(REGISTERED_VERBS)


# -------------------------------------------------------------- #
#  Property 1: Subcommand dispatch correctness
#  **Validates: Requirements 6.1, 6.2, 6.3**
# -------------------------------------------------------------- #


class TestProperty1DispatchCorrectness(unittest.TestCase):
    """Property 1: Subcommand dispatch correctness.

    For any registered verb and any args string, func() invokes the
    correct handler with the remaining args.

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    @given(verb=verb_strategy, rest=safe_args_text)
    @settings(max_examples=200)
    def test_dispatch_invokes_correct_handler(self, verb, rest):
        """func() dispatches to the handler mapped to the given verb."""
        if rest:
            args_str = f" {verb} {rest}"
        else:
            args_str = f" {verb}"

        cmd = _make_router(args_str)
        cmd.func()

        expected_tag = HANDLER_MAP[verb]
        self.assertIsNotNone(
            cmd._called_handler,
            f"Handler was not called for verb '{verb}' with args '{rest}'",
        )
        self.assertEqual(
            cmd._called_handler[0],
            expected_tag,
            f"Expected handler '{expected_tag}' for verb '{verb}', "
            f"got '{cmd._called_handler[0]}'",
        )
        self.assertEqual(
            cmd._called_handler[1],
            rest,
            f"Expected rest args '{rest}' but handler received "
            f"'{cmd._called_handler[1]}'",
        )


# -------------------------------------------------------------- #
#  Property 2: Invalid subcommand error
#  **Validates: Requirements 6.4**
# -------------------------------------------------------------- #


class TestProperty2InvalidSubcommandError(unittest.TestCase):
    """Property 2: Invalid subcommand error.

    For any non-empty string that is not a registered verb, func()
    produces an error message containing every valid subcommand name.

    **Validates: Requirements 6.4**
    """

    @given(
        invalid_verb=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=200)
    def test_invalid_verb_shows_all_valid_subcommands(self, invalid_verb):
        """Error message for unknown verb lists all valid subcommands."""
        # Ensure the generated string is not a valid verb (case-insensitive)
        assume(invalid_verb.lower() not in REGISTERED_VERBS)

        args_str = f" {invalid_verb}"
        cmd = _make_router(args_str)
        cmd.func()

        # Handler should NOT have been called
        self.assertIsNone(
            cmd._called_handler,
            f"Handler was unexpectedly called for invalid verb '{invalid_verb}'",
        )

        # Error message should have been sent
        self.assertTrue(
            len(cmd.caller._messages) > 0,
            f"No error message sent for invalid verb '{invalid_verb}'",
        )

        error_msg = cmd.caller._messages[0]
        for valid_verb in REGISTERED_VERBS:
            self.assertIn(
                valid_verb,
                error_msg,
                f"Error message missing valid verb '{valid_verb}'. "
                f"Full message: {error_msg}",
            )


# -------------------------------------------------------------- #
#  Property 3: Case-insensitive verb matching
#  **Validates: Requirements 6.6**
# -------------------------------------------------------------- #


def _random_case(s, data):
    """Generate a random case variation of string s using hypothesis data."""
    return "".join(
        data.draw(st.sampled_from([c.lower(), c.upper()]))
        for c in s
    )


class TestProperty3CaseInsensitiveMatching(unittest.TestCase):
    """Property 3: Case-insensitive verb matching.

    For any case variation of a registered verb, func() dispatches
    to the same handler as the lowercase form.

    **Validates: Requirements 6.6**
    """

    @given(verb=verb_strategy, data=st.data())
    @settings(max_examples=200)
    def test_case_variation_dispatches_same_handler(self, verb, data):
        """Any case variation of a registered verb dispatches correctly."""
        case_varied = _random_case(verb, data)

        args_str = f" {case_varied}"
        cmd = _make_router(args_str)
        cmd.func()

        expected_tag = HANDLER_MAP[verb]
        self.assertIsNotNone(
            cmd._called_handler,
            f"Handler not called for case variation '{case_varied}' "
            f"of verb '{verb}'",
        )
        self.assertEqual(
            cmd._called_handler[0],
            expected_tag,
            f"Case variation '{case_varied}' of '{verb}' dispatched to "
            f"'{cmd._called_handler[0]}' instead of '{expected_tag}'",
        )


# -------------------------------------------------------------- #
#  Unit Tests: Help display when no subcommand provided
#  **Validates: Requirements 6.5**
# -------------------------------------------------------------- #


class TestHelpDisplayNoSubcommand(unittest.TestCase):
    """When args is empty, the caller receives help listing all subcommands.

    **Validates: Requirements 6.5**
    """

    def test_empty_args_shows_help_with_all_verbs(self):
        """Calling func() with empty args sends help containing every verb."""
        cmd = _make_router("")
        cmd.func()

        self.assertEqual(len(cmd.caller._messages), 1)
        help_text = cmd.caller._messages[0]
        for verb in REGISTERED_VERBS:
            self.assertIn(verb, help_text)

    def test_whitespace_only_args_shows_help(self):
        """Calling func() with whitespace-only args sends help."""
        cmd = _make_router("   ")
        cmd.func()

        self.assertEqual(len(cmd.caller._messages), 1)
        help_text = cmd.caller._messages[0]
        for verb in REGISTERED_VERBS:
            self.assertIn(verb, help_text)

    def test_help_contains_usage_line(self):
        """Help text starts with a usage line referencing the command key."""
        cmd = _make_router("")
        cmd.func()

        help_text = cmd.caller._messages[0]
        self.assertIn("@test", help_text)
        self.assertIn("Usage", help_text)

    def test_no_handler_invoked_on_empty_args(self):
        """No handler is called when args is empty."""
        cmd = _make_router("")
        cmd.func()

        self.assertIsNone(cmd._called_handler)


# -------------------------------------------------------------- #
#  Unit Tests: Permission denied message
#  **Validates: Requirements 8.2**
# -------------------------------------------------------------- #


class FakeCallerDenied(FakeCaller):
    """Caller variant where check_permstring always returns False."""

    def check_permstring(self, perm):
        return False


class TestPermissionDenied(unittest.TestCase):
    """When _check_sub_perm fails, the caller gets a permission-denied message.

    **Validates: Requirements 8.2**
    """

    def _make_denied_router(self, args_str):
        """Create a TestRouter with a caller that lacks permissions."""
        cmd = TestRouter()
        cmd.caller = FakeCallerDenied()
        cmd.args = args_str
        cmd._called_handler = None
        return cmd

    def test_perm_denied_sends_message(self):
        """_check_sub_perm returns False and sends a denial message."""
        cmd = _make_router("")
        cmd.caller = FakeCallerDenied()
        result = cmd._check_sub_perm("Admin", "create")

        self.assertFalse(result)
        self.assertEqual(len(cmd.caller._messages), 1)
        self.assertIn("Permission denied", cmd.caller._messages[0])
        self.assertIn("Admin", cmd.caller._messages[0])
        self.assertIn("create", cmd.caller._messages[0])

    def test_perm_denied_handler_not_invoked(self):
        """When permission is denied, the handler is NOT called."""
        # Create a router with a perm-required subcommand
        class PermRouter(SubcommandRouter):
            key = "@permtest"
            subcommands = {
                "secret": (_handler_a, "Secret action", "Admin"),
            }

        cmd = PermRouter()
        cmd.caller = FakeCallerDenied()
        cmd.args = " secret"
        cmd._called_handler = None
        cmd.func()

        self.assertIsNone(cmd._called_handler)
        self.assertIn("Permission denied", cmd.caller._messages[0])


# -------------------------------------------------------------- #
#  Unit Tests: _log_admin writes to admin logger
#  **Validates: Requirements 8.1**
# -------------------------------------------------------------- #


class TestLogAdmin(unittest.TestCase):
    """_log_admin writes an INFO record to the mygame.admin logger.

    **Validates: Requirements 8.1**
    """

    def test_log_admin_writes_info(self):
        """_log_admin emits an INFO log containing operator, verb, detail."""
        cmd = _make_router("")

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd._log_admin("create", "created 3 agents for bob")

        self.assertEqual(len(cm.output), 1)
        log_line = cm.output[0]
        self.assertIn("TestPlayer", log_line)
        self.assertIn("@test", log_line)
        self.assertIn("create", log_line)
        self.assertIn("created 3 agents for bob", log_line)

    def test_log_admin_uses_caller_key(self):
        """_log_admin includes the caller's key in the log message."""
        cmd = _make_router("")
        cmd.caller.key = "AdminUser"

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd._log_admin("destroy", "removed building")

        self.assertIn("AdminUser", cm.output[0])


# -------------------------------------------------------------- #
#  Unit Tests: Command registration in CharacterCmdSet
#  **Validates: Requirements 7.1, 7.2, 7.3, 7.4**
# -------------------------------------------------------------- #


def _setup_default_cmds_stubs():
    """Add stubs for evennia.default_cmds and related modules.

    These are needed to import default_cmdsets.py which inherits from
    evennia.default_cmds.CharacterCmdSet, etc.
    """
    # Tracking CmdSet base that records add/remove calls
    class _TrackingCmdSet:
        key = "BaseCmdSet"

        def __init__(self):
            self._added = []
            self._removed = []

        def at_cmdset_creation(self):
            pass

        def add(self, cmd_instance):
            self._added.append(cmd_instance)

        def remove(self, cmd_class):
            self._removed.append(cmd_class)

    class FakeCharacterCmdSet(_TrackingCmdSet):
        key = "DefaultCharacter"

    class FakeAccountCmdSet(_TrackingCmdSet):
        key = "DefaultAccount"

    class FakeUnloggedinCmdSet(_TrackingCmdSet):
        key = "DefaultUnloggedin"

    class FakeSessionCmdSet(_TrackingCmdSet):
        key = "DefaultSession"

    # Stub evennia.default_cmds
    default_cmds_mod = types.ModuleType("evennia.default_cmds")
    default_cmds_mod.CharacterCmdSet = FakeCharacterCmdSet
    default_cmds_mod.AccountCmdSet = FakeAccountCmdSet
    default_cmds_mod.UnloggedinCmdSet = FakeUnloggedinCmdSet
    default_cmds_mod.SessionCmdSet = FakeSessionCmdSet
    sys.modules["evennia.default_cmds"] = default_cmds_mod

    # Also make it accessible as evennia.default_cmds attribute
    if "evennia" in sys.modules:
        sys.modules["evennia"].default_cmds = default_cmds_mod

    # Stub evennia.commands.default and sub-modules for inline imports
    default_pkg = types.ModuleType("evennia.commands.default")
    sys.modules.setdefault("evennia.commands.default", default_pkg)

    general_mod = types.ModuleType("evennia.commands.default.general")
    general_mod.CmdWhisper = type("CmdWhisper", (), {"key": "whisper"})
    sys.modules["evennia.commands.default.general"] = general_mod

    comms_mod = types.ModuleType("evennia.commands.default.comms")
    comms_mod.CmdPage = type("CmdPage", (), {"key": "page"})
    sys.modules["evennia.commands.default.comms"] = comms_mod


_setup_default_cmds_stubs()

from mygame.commands.default_cmdsets import CharacterCmdSet  # noqa: E402


def _get_registered_keys(cmdset_instance):
    """Return a set of command keys from all added command instances."""
    return {cmd.key for cmd in cmdset_instance._added}


class TestCharacterCmdSetRegistration(unittest.TestCase):
    """Verify CharacterCmdSet registers the correct commands.

    **Validates: Requirements 7.1, 7.2, 7.3, 7.4**
    """

    @classmethod
    def setUpClass(cls):
        """Create a CharacterCmdSet and populate it once for all tests."""
        cls.cmdset = CharacterCmdSet()
        cls.cmdset.at_cmdset_creation()
        cls.registered_keys = _get_registered_keys(cls.cmdset)

    # --- Requirement 7.1: New admin routers are registered ---

    def test_admin_building_router_registered(self):
        """CmdAdminBuilding (@building) is registered."""
        self.assertIn("@building", self.registered_keys)

    def test_admin_agent_router_registered(self):
        """CmdAdminAgent (@agent) is registered."""
        self.assertIn("@agent", self.registered_keys)

    def test_admin_resource_router_registered(self):
        """CmdAdminResource (@resource) is registered."""
        self.assertIn("@resource", self.registered_keys)

    def test_admin_player_router_registered(self):
        """CmdAdminPlayer (@player) is registered."""
        self.assertIn("@player", self.registered_keys)

    # --- Requirement 7.2: New game agent router is registered ---

    def test_game_agent_router_registered(self):
        """CmdAgent (agent) game router is registered."""
        self.assertIn("agent", self.registered_keys)

    # --- Requirement 7.3: Unchanged standalone commands still registered ---

    def test_reloaddata_still_registered(self):
        """CmdReloadData (@reloaddata) is still registered."""
        self.assertIn("@reloaddata", self.registered_keys)

    def test_teleport_still_registered(self):
        """CmdTeleport (@teleport) is still registered."""
        self.assertIn("@teleport", self.registered_keys)

    def test_clearfog_still_registered(self):
        """CmdClearFog (@clearfog) is still registered."""
        self.assertIn("@clearfog", self.registered_keys)

    def test_purgerooms_still_registered(self):
        """CmdPurgeRooms (@purgerooms) is still registered."""
        self.assertIn("@purgerooms", self.registered_keys)

    def test_migrate_still_registered(self):
        """CmdMigrate (@migrate) is still registered."""
        self.assertIn("@migrate", self.registered_keys)

    # --- Requirement 7.4: Old standalone classes are NOT registered ---

    def test_old_spawn_building_not_registered(self):
        """Old CmdSpawnBuilding key is not registered (replaced by @building)."""
        self.assertNotIn("@spawnbuilding", self.registered_keys)

    def test_old_create_agent_not_registered(self):
        """Old CmdCreateAgent key is not registered (replaced by @agent)."""
        self.assertNotIn("@createagent", self.registered_keys)

    def test_old_destroy_agent_not_registered(self):
        """Old CmdDestroyAgent key is not registered (replaced by @agent)."""
        self.assertNotIn("@destroyagent", self.registered_keys)

    def test_old_list_agents_not_registered(self):
        """Old CmdListAgents key is not registered (replaced by @agent)."""
        self.assertNotIn("@listagents", self.registered_keys)

    def test_old_give_resource_not_registered(self):
        """Old CmdGiveResource key is not registered (replaced by @resource)."""
        self.assertNotIn("@giveresource", self.registered_keys)

    def test_old_reset_resources_not_registered(self):
        """Old CmdResetResources key is not registered (replaced by @resource)."""
        self.assertNotIn("@resetresources", self.registered_keys)

    def test_old_set_level_not_registered(self):
        """Old CmdSetLevel key is not registered (replaced by @player)."""
        self.assertNotIn("@setlevel", self.registered_keys)

    def test_old_set_rank_not_registered(self):
        """Old CmdSetRank key is not registered (replaced by @player)."""
        self.assertNotIn("@setrank", self.registered_keys)

    def test_old_agents_not_registered(self):
        """Old CmdAgents key is not registered (replaced by agent router)."""
        self.assertNotIn("agents", self.registered_keys)

    def test_old_assign_not_registered(self):
        """Old CmdAssign key is not registered (replaced by agent router)."""
        self.assertNotIn("assign", self.registered_keys)

    def test_old_unassign_not_registered(self):
        """Old CmdUnassign key is not registered (replaced by agent router)."""
        self.assertNotIn("unassign", self.registered_keys)

    def test_old_train_not_registered(self):
        """Old CmdTrain key is not registered (replaced by agent router)."""
        self.assertNotIn("train", self.registered_keys)

    def test_old_patrol_not_registered(self):
        """Old CmdPatrol key is not registered (replaced by agent router)."""
        self.assertNotIn("patrol", self.registered_keys)

    def test_old_stop_agent_not_registered(self):
        """Old CmdStopAgent key is not registered (replaced by agent router)."""
        self.assertNotIn("stopagent", self.registered_keys)

    # --- Unchanged game commands still registered ---

    def test_game_commands_still_registered(self):
        """Core game commands (move, build, look, etc.) are still registered."""
        expected_game_keys = {
            "move", "harvest", "build", "upgrade", "demolish",
            "attack", "equip", "unequip", "research", "powerup",
            "score", "equipment", "buildings", "scan", "technology",
            "inventory", "chat", "message", "say", "look", "get",
            "map", "leave", "stop", "closeexit", "openexit", "who",
        }
        for key in expected_game_keys:
            self.assertIn(
                key, self.registered_keys,
                f"Game command '{key}' should still be registered",
            )


if __name__ == "__main__":
    unittest.main()
