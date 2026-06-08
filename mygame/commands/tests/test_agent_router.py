"""
Unit tests for the game agent command router (CmdAgent).

Tests subcommand delegation, help display, invalid subcommand error,
and GameCommand inheritance for prefix matching.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10
"""

import sys
import types
import unittest

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

    class Command:
        key = ""
        aliases = []
        locks = ""
        help_category = "General"
        def func(self):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": DefaultCharacter,
    })
    _mod("evennia.commands")
    _mod("evennia.commands.command", {"Command": Command})
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

from mygame.commands.agent_commands import CmdAgent  # noqa: E402
from commands.game_commands import GameCommand  # noqa: E402


# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeNDB:
    def __init__(self, systems=None):
        self.systems = systems or {}


class FakeDB:
    """Attribute-bag that allows arbitrary get/set."""
    def __init__(self, **kwargs):
        self._data = dict(kwargs)

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        return self._data.get(key)

    def __setattr__(self, key, value):
        if key.startswith("_"):
            object.__setattr__(self, key, value)
        else:
            self._data[key] = value


class FakeCaller:
    """Fake caller for game commands (no perm check needed)."""

    def __init__(self, name="Player1", systems=None):
        self.key = name
        self.ndb = FakeNDB(systems)
        self.db = FakeDB()
        self._messages = []
        self.location = None

    def check_permstring(self, perm):
        return True

    def msg(self, text=None, **kwargs):
        if text is not None:
            self._messages.append(text)


class FakeAgent:
    """Fake agent NPC for list tests."""

    def __init__(self, agent_id=1, role="soldier"):
        self.key = f"Agent-{agent_id}"
        self.db = FakeDB(
            agent_id=agent_id,
            role=role,
            role_target=None,
            incapacitated=False,
            reserve=False,
            activity_status="Idle",
        )


class FakeAgentSystem:
    """Fake agent_system with all methods used by CmdAgent subcommands."""

    def __init__(self, agents=None):
        self._agents = agents or []
        self._assign_result = (True, "Assigned.")
        self._unassign_result = (True, "Unassigned.")
        self._train_result = (True, "Training started.")
        self._patrol_result = (True, "Patrol route set.")
        self._clear_patrol_result = (True, "Patrol route cleared.")
        self._stop_result = (True, "Agent stopped.")

    def get_agents(self, caller):
        return self._agents

    def assign_agent(self, caller, agent_id, role, target_building=None):
        return self._assign_result

    def unassign_agent(self, caller, agent_id):
        return self._unassign_result

    def train_agent(self, caller, building):
        return self._train_result

    def set_patrol_route(self, caller, agent_id, waypoints):
        self._last_waypoints = waypoints
        return self._patrol_result

    def clear_patrol_route(self, caller, agent_id):
        return self._clear_patrol_result

    def stop_agent(self, caller, agent_id):
        return self._stop_result


def _make_cmd(caller, args=""):
    cmd = CmdAgent()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    return cmd


# -------------------------------------------------------------- #
#  Inheritance test (Req 5.10)
# -------------------------------------------------------------- #

class TestCmdAgentInheritance(unittest.TestCase):
    """Req 5.10: CmdAgent inherits GameCommand for prefix matching."""

    def test_inherits_game_command(self):
        self.assertTrue(issubclass(CmdAgent, GameCommand))


# -------------------------------------------------------------- #
#  Help display test (Req 5.8)
# -------------------------------------------------------------- #

class TestAgentHelpDisplay(unittest.TestCase):
    """Req 5.8: agent with no subcommand shows help with all subcommands."""

    def test_no_args_shows_help(self):
        caller = FakeCaller()
        cmd = _make_cmd(caller, "")
        cmd.func()
        output = "\n".join(caller._messages)
        for verb in ("list", "assign", "unassign", "train", "patrol", "stop"):
            self.assertIn(verb, output)


# -------------------------------------------------------------- #
#  Invalid subcommand test (Req 5.9)
# -------------------------------------------------------------- #

class TestAgentInvalidSubcommand(unittest.TestCase):
    """Req 5.9: agent <invalid> shows error with valid subcommand names."""

    def test_invalid_subcommand_error(self):
        caller = FakeCaller()
        cmd = _make_cmd(caller, " invalid")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Unknown subcommand", output)
        for verb in ("list", "assign", "unassign", "train", "patrol", "stop"):
            self.assertIn(verb, output)


# -------------------------------------------------------------- #
#  agent list delegation (Req 5.1)
# -------------------------------------------------------------- #

class TestAgentList(unittest.TestCase):
    """Req 5.1: agent list delegates to agent listing logic."""

    def test_list_shows_agent_ids(self):
        agents = [FakeAgent(agent_id=1), FakeAgent(agent_id=2, role="medic")]
        agent_sys = FakeAgentSystem(agents=agents)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("#1", output)
        self.assertIn("#2", output)

    def test_list_no_system_shows_unavailable(self):
        caller = FakeCaller(systems={})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        self.assertTrue(any("unavailable" in m.lower() for m in caller._messages))


# -------------------------------------------------------------- #
#  agent assign delegation (Req 5.2)
# -------------------------------------------------------------- #

class TestAgentAssign(unittest.TestCase):
    """Req 5.2: agent assign delegates to assignment logic."""

    def test_assign_success(self):
        agent_sys = FakeAgentSystem()
        agent_sys._assign_result = (True, "Agent 1 assigned to soldier.")
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " assign 1 soldier")
        cmd.func()
        self.assertTrue(any("assigned" in m.lower() for m in caller._messages))

    def test_assign_no_args_shows_usage(self):
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " assign")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  agent unassign delegation (Req 5.3)
# -------------------------------------------------------------- #

class TestAgentUnassign(unittest.TestCase):
    """Req 5.3: agent unassign delegates to unassignment logic."""

    def test_unassign_success(self):
        agent_sys = FakeAgentSystem()
        agent_sys._unassign_result = (True, "Agent 1 unassigned.")
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " unassign 1")
        cmd.func()
        self.assertTrue(any("unassigned" in m.lower() for m in caller._messages))

    def test_unassign_no_args_shows_usage(self):
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " unassign")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  agent train delegation (Req 5.4)
# -------------------------------------------------------------- #

class TestAgentTrain(unittest.TestCase):
    """Req 5.4: agent train delegates to training logic."""

    def test_train_not_in_building(self):
        """Train outside a building shows error."""
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(systems={"agent_system": agent_sys})
        # caller has no inside_building flag set, so _get_current_building returns None
        cmd = _make_cmd(caller, " train")
        cmd.func()
        self.assertTrue(any("Academy" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  agent patrol delegation (Req 5.5, 5.6)
# -------------------------------------------------------------- #

class TestAgentPatrol(unittest.TestCase):
    """Req 5.5, 5.6: agent patrol sets or clears patrol route."""

    def test_patrol_set_waypoints(self):
        agent_sys = FakeAgentSystem()
        agent_sys._patrol_result = (True, "Patrol route set for agent 1.")
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " patrol 1 10,20 30,40")
        cmd.func()
        self.assertTrue(any("patrol" in m.lower() for m in caller._messages))
        self.assertEqual(agent_sys._last_waypoints, [(10, 20), (30, 40)])

    def test_patrol_clear(self):
        agent_sys = FakeAgentSystem()
        agent_sys._clear_patrol_result = (True, "Patrol route cleared for agent 1.")
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " patrol 1 clear")
        cmd.func()
        self.assertTrue(any("cleared" in m.lower() for m in caller._messages))

    def test_patrol_no_args_shows_usage(self):
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " patrol")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  agent stop delegation (Req 5.7)
# -------------------------------------------------------------- #

class TestAgentStop(unittest.TestCase):
    """Req 5.7: agent stop delegates to stop logic."""

    def test_stop_success(self):
        agent_sys = FakeAgentSystem()
        agent_sys._stop_result = (True, "Agent 1 stopped.")
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " stop 1")
        cmd.func()
        self.assertTrue(any("stopped" in m.lower() for m in caller._messages))

    def test_stop_no_args_shows_usage(self):
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " stop")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


if __name__ == "__main__":
    unittest.main()
