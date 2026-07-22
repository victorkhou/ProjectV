"""
Unit tests for admin commands.

Tests permission checks, @reboot success/failure paths,
@giveresource resource addition, and execution logging.

Requirements: 33.3, 33.4
"""

import sys
import types
import unittest
import logging

import pytest

from world import services

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

    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "")
            self.location = None

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "")
        def at_object_creation(self):
            pass
        def at_post_login(self, session=None, **kwargs):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": DefaultCharacter,
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

from mygame.commands.admin_commands import (  # noqa: E402
    CmdReboot,
)
from mygame.commands.admin_commands import CmdAdminResource  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

RESOURCE_TYPES = ("Iron", "Wood", "Stone")


@pytest.fixture(autouse=True)
def _services_sandbox():
    """Give every test a private, empty facade state, restored on exit."""
    with services.override({}):
        yield


def _install_systems(systems):
    """Register fake *systems* for the current test through the facade."""
    services.get_systems().update(systems)


class FakeNDB:
    def __init__(self, systems=None):
        self.systems = systems or {}

class FakeDB:
    def __init__(self):
        self.resources = {r: 0 for r in RESOURCE_TYPES}

class FakeCaller:
    def __init__(self, name="Admin", permissions=None, systems=None):
        self.key = name
        self.permissions = permissions or set()
        self.ndb = FakeNDB()
        if systems:
            _install_systems(systems)
        self.db = FakeDB()
        self._messages = []
        self._search_results = {}

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def search(self, name, **kwargs):
        return self._search_results.get(name)

class FakeTarget:
    def __init__(self, name="Player1"):
        self.key = name
        self.db = FakeDB()
        self._messages = []
        self._resources = {r: 0 for r in RESOURCE_TYPES}

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def add_resource(self, resource_type, amount):
        self._resources[resource_type] = self._resources.get(resource_type, 0) + amount

class FakeRegistry:
    def __init__(self, success=True, errors=None):
        self._success = success
        self._errors = errors or []

    def reload_all(self):
        return self._success, self._errors

def _make_cmd(cmd_class, caller, args=""):
    cmd = cmd_class()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    return cmd

# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestCmdRebootPermission(unittest.TestCase):
    def test_denied_without_builder(self):
        caller = FakeCaller(permissions={"Player"})
        cmd = _make_cmd(CmdReboot, caller)
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_allowed_with_builder(self):
        registry = FakeRegistry(success=True)
        caller = FakeCaller(
            permissions={"Builder"},
            systems={"registry": registry},
        )
        cmd = _make_cmd(CmdReboot, caller)
        cmd.func()
        self.assertTrue(any("successful" in m.lower() for m in caller._messages))

    def test_allowed_with_admin(self):
        registry = FakeRegistry(success=True)
        caller = FakeCaller(
            permissions={"Admin"},
            systems={"registry": registry},
        )
        cmd = _make_cmd(CmdReboot, caller)
        cmd.func()
        self.assertTrue(any("successful" in m.lower() for m in caller._messages))

class TestCmdRebootPaths(unittest.TestCase):
    def test_success_path(self):
        registry = FakeRegistry(success=True)
        caller = FakeCaller(
            permissions={"Builder"},
            systems={"registry": registry},
        )
        cmd = _make_cmd(CmdReboot, caller)
        cmd.func()
        self.assertTrue(any("successful" in m.lower() for m in caller._messages))

    def test_failure_path(self):
        registry = FakeRegistry(success=False, errors=["Missing buildings.yaml"])
        caller = FakeCaller(
            permissions={"Builder"},
            systems={"registry": registry},
        )
        cmd = _make_cmd(CmdReboot, caller)
        cmd.func()
        self.assertTrue(any("failed" in m.lower() for m in caller._messages))
        self.assertTrue(any("buildings.yaml" in m for m in caller._messages))

    def test_no_registry(self):
        caller = FakeCaller(permissions={"Builder"})
        cmd = _make_cmd(CmdReboot, caller)
        cmd.func()
        self.assertTrue(any("unavailable" in m.lower() for m in caller._messages))

class TestCmdGiveResourcePermission(unittest.TestCase):
    def test_denied_without_builder(self):
        caller = FakeCaller(permissions={"Player"})
        # CmdAdminResource uses check_permstring; FakeCaller uses permissions set
        # The router's _check_sub_perm calls check_permstring which FakeCaller
        # doesn't have — add it for this test
        caller.check_permstring = lambda perm: False
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron 50 Player1")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

class TestCmdGiveResourceValidation(unittest.TestCase):
    def test_missing_args(self):
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_invalid_amount(self):
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron abc Player1")
        cmd.func()
        self.assertTrue(any("Invalid amount" in m for m in caller._messages))

    def test_negative_amount(self):
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron -5 Player1")
        cmd.func()
        self.assertTrue(any("positive" in m.lower() for m in caller._messages))

    def test_target_not_found(self):
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron 50 Ghost")
        cmd.func()
        self.assertTrue(any("Could not find" in m for m in caller._messages))

    def test_unknown_resource_rejected(self):
        """An unknown resource name is rejected — NOT minted as a junk resource
        (the reported 'give all' bug that created a resource called 'all')."""
        target = FakeTarget(name="Player1")
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        caller._search_results["Player1"] = target
        cmd = _make_cmd(CmdAdminResource, caller, " give bogus 50 Player1")
        cmd.func()
        self.assertTrue(any("Unknown resource" in m for m in caller._messages))
        self.assertNotIn("bogus", target._resources)
        self.assertNotIn("Bogus", target._resources)

class TestCmdGiveResourceSuccess(unittest.TestCase):
    def test_adds_resources(self):
        target = FakeTarget(name="Player1")
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        caller._search_results["Player1"] = target
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron 50 Player1")
        cmd.func()
        self.assertEqual(target._resources["Iron"], 50)
        self.assertTrue(any("Gave 50 Iron" in m for m in caller._messages))

    def test_target_notified(self):
        target = FakeTarget(name="Player1")
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        caller._search_results["Player1"] = target
        cmd = _make_cmd(CmdAdminResource, caller, " give Iron 25 Player1")
        cmd.func()
        self.assertTrue(any("received" in m.lower() for m in target._messages))

    def test_give_all_grants_every_resource(self):
        """'give all N' grants N of every canonical resource, not a resource
        literally named 'all'."""
        target = FakeTarget(name="Player1")
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        caller._search_results["Player1"] = target
        cmd = _make_cmd(CmdAdminResource, caller, " give all 100 Player1")
        cmd.func()
        for r in RESOURCE_TYPES:
            self.assertEqual(target._resources[r], 100)
        self.assertNotIn("all", target._resources)
        self.assertNotIn("All", target._resources)
        self.assertTrue(any("all resources" in m for m in caller._messages))

    def test_give_resource_case_insensitive(self):
        """A lowercase resource name resolves to the canonical form."""
        target = FakeTarget(name="Player1")
        caller = FakeCaller(permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        caller._search_results["Player1"] = target
        cmd = _make_cmd(CmdAdminResource, caller, " give iron 30 Player1")
        cmd.func()
        self.assertEqual(target._resources["Iron"], 30)

class TestCmdGiveResourceLogging(unittest.TestCase):
    def test_logs_execution(self):
        target = FakeTarget(name="Player1")
        caller = FakeCaller(name="AdminUser", permissions={"Builder"})
        caller.check_permstring = lambda perm: True
        caller._search_results["Player1"] = target

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd = _make_cmd(CmdAdminResource, caller, " give Iron 10 Player1")
            cmd.func()

        log_output = "\n".join(cm.output)
        self.assertIn("AdminUser", log_output)
        self.assertIn("Player1", log_output)
        self.assertIn("Iron", log_output)

if __name__ == "__main__":
    unittest.main()
