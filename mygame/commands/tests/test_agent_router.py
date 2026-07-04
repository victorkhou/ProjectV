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

# Real backend pieces, used by the end-to-end `agent ability` coverage below
# (task 14.4) so enable/disable/status/gate logic is exercised through the
# command path against a genuine AgentSystem rather than a fake.
from mygame.world.systems.agent_system import (  # noqa: E402
    AgentSystem,
    ABILITY_SCRIPT_KEYS,
)
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import AbilityGateDef  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.constants import DeliveryState  # noqa: E402


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

    def __init__(self, agents=None, progression_views=None):
        self._agents = agents or []
        self._assign_result = (True, "Assigned.")
        self._unassign_result = (True, "Unassigned.")
        self._train_result = (True, "Training started.")
        self._patrol_result = (True, "Patrol route set.")
        self._clear_patrol_result = (True, "Patrol route cleared.")
        self._stop_result = (True, "Agent stopped.")
        # Map agent_id -> progression view dict (task 13.1 shape). Used by the
        # roster to render the progression segment (task 13.2).
        self._progression_views = progression_views or {}

    def get_agents(self, caller):
        return self._agents

    def get_agent_progression_view(self, agent):
        aid = getattr(agent.db, "agent_id", None)
        if aid in self._progression_views:
            return self._progression_views[aid]
        # Sensible default view when none configured.
        return {
            "effective_level": 1,
            "rank_name": "Recruit",
            "ability_status": {"delivery": "locked:21"},
            "capped_by_commander": False,
        }

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


class FakeAbilityAgentSystem:
    """Fake agent_system exposing the gated-ability backends used by
    ``CmdAgent.sub_ability``.

    Replicates the real ``AgentSystem`` rejection strings so the command
    wiring (parse + delegate + surface message) can be exercised end-to-end:

    - unknown agent id (one the player does not own) → ``Agent #<id> not found.``
      (Req 16.7)
    - unknown ability key → ``Unknown ability '<key>'.`` (Req 16.6)
    """

    def __init__(self, owned_ids=(1,), known_keys=("delivery",)):
        self._owned_ids = set(owned_ids)
        self._known_keys = set(known_keys)
        self.calls = []

    def _reject(self, agent_id, key=None):
        if agent_id not in self._owned_ids:
            return f"Agent #{agent_id} not found."
        if key is not None and key not in self._known_keys:
            return f"Unknown ability '{key}'."
        return None

    def enable_ability(self, player, agent_id, key):
        self.calls.append(("enable", agent_id, key))
        rejection = self._reject(agent_id, key)
        if rejection is not None:
            return rejection
        return f"Ability '{key}' enabled for Agent #{agent_id}."

    def disable_ability(self, player, agent_id, key):
        self.calls.append(("disable", agent_id, key))
        rejection = self._reject(agent_id, key)
        if rejection is not None:
            return rejection
        return f"Ability '{key}' disabled for Agent #{agent_id}."

    def get_ability_status(self, player, agent_id):
        self.calls.append(("status", agent_id, None))
        rejection = self._reject(agent_id)
        if rejection is not None:
            return rejection
        return f"Agent #{agent_id} abilities (level 1):"


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

    def test_list_shows_level_and_rank(self):
        """Req 11.1: roster shows each agent's effective level and rank name."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 22,
                "rank_name": "Veteran",
                "ability_status": {"delivery": "enabled"},
                "capped_by_commander": False,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Lv 22 Veteran", output)

    def test_list_shows_enabled_ability_status(self):
        """Req 11.2: roster shows per-ability status (enabled)."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 25,
                "rank_name": "Veteran",
                "ability_status": {"delivery": "enabled"},
                "capped_by_commander": False,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("delivery: enabled", output)

    def test_list_shows_available_ability_status(self):
        """Req 11.2: roster shows per-ability status (available)."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 21,
                "rank_name": "Veteran",
                "ability_status": {"delivery": "available"},
                "capped_by_commander": False,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("delivery: available", output)

    def test_list_translates_locked_encoding(self):
        """Req 11.3: a fully-locked agent shows 'no abilities'."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 3,
                "rank_name": "Recruit",
                "ability_status": {"delivery": "locked:21"},
                "capped_by_commander": False,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("no abilities", output)

    def test_list_lists_abilities_when_any_qualifies(self):
        """Req 11.2/11.3: when at least one ability qualifies, each gate and
        its (readable) state is listed rather than 'no abilities'."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 21,
                "rank_name": "Veteran",
                "ability_status": {
                    "delivery": "available",
                    "scouting": "locked:41",
                },
                "capped_by_commander": False,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("no abilities", output)
        self.assertIn("delivery: available", output)
        self.assertIn("scouting: locked Lv41", output)

    def test_list_shows_capped_marker(self):
        """Req 11.4: roster shows the [capped] marker when capped by commander."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 5,
                "rank_name": "Recruit",
                "ability_status": {"delivery": "locked:21"},
                "capped_by_commander": True,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("[capped]", output)

    def test_list_no_capped_marker_when_not_capped(self):
        """Req 11.4: no [capped] marker when the agent is not capped."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        views = {
            1: {
                "effective_level": 5,
                "rank_name": "Recruit",
                "ability_status": {"delivery": "locked:21"},
                "capped_by_commander": False,
            }
        }
        agent_sys = FakeAgentSystem(agents=agents, progression_views=views)
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertNotIn("[capped]", output)

    def test_list_survives_progression_view_error(self):
        """The roster never breaks if get_agent_progression_view raises."""
        agents = [FakeAgent(agent_id=1, role="harvester")]
        agent_sys = FakeAgentSystem(agents=agents)

        def _boom(agent):
            raise RuntimeError("progression unavailable")

        agent_sys.get_agent_progression_view = _boom
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        # Core agent info still rendered despite the progression failure.
        self.assertIn("#1", output)


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


# -------------------------------------------------------------- #
#  agent ability rejection tests (Req 16.6, 16.7)
# -------------------------------------------------------------- #

class TestAgentAbilityRejection(unittest.TestCase):
    """Req 16.6, 16.7: agent ability rejects unknown keys and unowned agents.

    These exercise the COMMAND path (CmdAgent.sub_ability) so the parse +
    delegate + surface-message wiring is covered, complementing the
    AgentSystem-level backend tests from task 9.1.
    """

    def test_unknown_ability_key_rejected(self):
        """Req 16.6: enabling an unknown ability key surfaces a rejection."""
        agent_sys = FakeAbilityAgentSystem(owned_ids=(1,), known_keys=("delivery",))
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " ability 1 boguskey on")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Unknown ability", output)
        self.assertIn("boguskey", output)
        # Command must route through the backend with the parsed key.
        self.assertIn(("enable", 1, "boguskey"), agent_sys.calls)

    def test_unknown_ability_key_rejected_on_disable(self):
        """Req 16.6: disabling an unknown ability key also surfaces a rejection."""
        agent_sys = FakeAbilityAgentSystem(owned_ids=(1,), known_keys=("delivery",))
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " ability 1 boguskey off")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Unknown ability", output)
        self.assertIn("boguskey", output)
        self.assertIn(("disable", 1, "boguskey"), agent_sys.calls)

    def test_unowned_agent_rejected_on_enable(self):
        """Req 16.7: enabling on an agent id the player does not own is rejected."""
        agent_sys = FakeAbilityAgentSystem(owned_ids=(1,), known_keys=("delivery",))
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " ability 99 delivery on")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("not found", output.lower())
        self.assertIn("#99", output)
        self.assertIn(("enable", 99, "delivery"), agent_sys.calls)

    def test_unowned_agent_rejected_on_status(self):
        """Req 16.7: status for an unowned agent id is rejected."""
        agent_sys = FakeAbilityAgentSystem(owned_ids=(1,), known_keys=("delivery",))
        caller = FakeCaller(systems={"agent_system": agent_sys})
        cmd = _make_cmd(caller, " ability 99")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("not found", output.lower())
        self.assertIn("#99", output)
        self.assertIn(("status", 99, None), agent_sys.calls)


# -------------------------------------------------------------- #
#  agent ability END-TO-END against the REAL AgentSystem (task 14.4)
# -------------------------------------------------------------- #
#
#  The rejection tests above exercise the command wiring against a fake
#  backend. The class below drives `agent ability` end-to-end through a
#  genuine ``AgentSystem`` (with a real delivery gate at level 21) so the
#  enable/disable/status/gate logic is actually tested via the command path:
#  ownership lookup, effective-level gating, script attach/detach, and the
#  sticky enabled set. (Req 16.2-16.7)


class _AbilityScript:
    """Minimal stand-in for an attached Evennia Script (key + delete)."""

    def __init__(self, key):
        self.key = key
        self._deleted = False

    def delete(self):
        self._deleted = True


class _AbilityScriptManager:
    """Minimal scripts manager mirroring the slice AgentSystem relies on.

    ``all()`` lists live scripts, ``add(cls)`` attaches a new script resolving
    its key the same way ``AgentSystem`` does (via ``ABILITY_SCRIPT_KEYS``),
    and ``delete()`` on a script removes it.
    """

    def __init__(self, keys=None):
        self._scripts = [_AbilityScript(k) for k in (keys or [])]

    def all(self):
        return [s for s in self._scripts if not s._deleted]

    def add(self, script_cls):
        key = ABILITY_SCRIPT_KEYS.get(
            getattr(script_cls, "__name__", ""),
            getattr(script_cls, "key", "") or script_cls.__name__,
        )
        self._scripts.append(_AbilityScript(key))


class _AbilityAgent:
    """Owned agent NPC with a controllable raw level and a scripts manager."""

    def __init__(self, agent_id, owner, raw_level=1, script_keys=None,
                 enabled_abilities=None):
        self.key = f"Agent-{agent_id}"
        self._raw_level = raw_level
        self.db = FakeDB(
            agent_id=agent_id,
            owner=owner,
            role="",
            role_target=None,
            reserve=False,
            incapacitated=False,
            enabled_abilities=list(enabled_abilities) if enabled_abilities else None,
        )
        self.scripts = _AbilityScriptManager(script_keys)

    def get_raw_level(self):
        return self._raw_level


def _make_real_ability_system(agents):
    """Build a REAL AgentSystem with a delivery gate at level 21.

    ``_get_agents_fallback`` is wired so ``get_agent_by_id(caller, id)`` finds
    the agents whose ``db.owner`` is the querying caller (Evennia DB is absent
    in tests, so ``get_agents`` falls through to this fallback).
    """
    registry = DataRegistry()
    registry.ranks = []
    registry.ability_gates = {
        "delivery": AbilityGateDef(key="delivery", required_level=21),
    }
    system = AgentSystem(registry=registry, event_bus=EventBus())

    def _fallback(player):
        return [a for a in agents if getattr(a.db, "owner", None) is player]

    system._get_agents_fallback = _fallback
    return system


def _script_keys(agent):
    return [s.key for s in agent.scripts.all()]


class TestAgentAbilityEndToEnd(unittest.TestCase):
    """`agent ability` driven end-to-end through a real AgentSystem.

    Req 16.2: enable at/above gate records + attaches the behavior script.
    Req 16.3: enable below gate rejects with the required level (no attach/record).
    Req 16.4: disable detaches the ability script, keeping HarvesterScript.
    Req 16.5: status reports locked/available/enabled per effective level + enabled set.
    Req 16.6: unknown ability key rejected.
    Req 16.7: unowned/missing agent rejected.
    """

    def _caller_with(self, agents, owner_level):
        """Wire a FakeCaller owning *agents*, exposing a real AgentSystem."""
        caller = FakeCaller()
        caller.db.level = owner_level
        for agent in agents:
            agent.db.owner = caller
        system = _make_real_ability_system(agents)
        caller.ndb = FakeNDB({"agent_system": system})
        return caller, system

    # -- Req 16.2: enable at/above gate attaches + records ----------- #

    def test_enable_at_or_above_gate_attaches_and_records(self):
        # owner level 30 → ceiling 29; raw 25 → effective 25 >= 21
        agent = _AbilityAgent(1, owner=None, raw_level=25)
        caller, system = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1 delivery on")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("enabled", output.lower())
        # Recorded in the sticky enabled set...
        self.assertIn("delivery", system.get_enabled_abilities(agent))
        # ...and the behavior script is attached, with delivery state inited.
        self.assertIn("delivery_behavior", _script_keys(agent))
        self.assertEqual(agent.db.delivery_state, DeliveryState.IDLE)

    # -- Req 16.3: enable below gate rejects, no attach/record ------- #

    def test_enable_below_gate_rejects_with_required_level(self):
        # owner level 30 → ceiling 29; raw 10 → effective 10 < 21
        agent = _AbilityAgent(1, owner=None, raw_level=10)
        caller, system = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1 delivery on")
        cmd.func()

        output = "\n".join(caller._messages)
        # Rejection names the required level (21) and does not record/attach.
        self.assertIn("21", output)
        self.assertNotIn("delivery", system.get_enabled_abilities(agent))
        self.assertNotIn("delivery_behavior", _script_keys(agent))

    def test_enable_below_gate_when_capped_by_owner(self):
        # raw level high, but owner level 5 → ceiling 4 caps effective to 4.
        agent = _AbilityAgent(1, owner=None, raw_level=25)
        caller, system = self._caller_with([agent], owner_level=5)

        cmd = _make_cmd(caller, " ability 1 delivery on")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("21", output)
        self.assertNotIn("delivery", system.get_enabled_abilities(agent))
        self.assertNotIn("delivery_behavior", _script_keys(agent))

    # -- Req 16.4: disable detaches delivery, keeps HarvesterScript -- #

    def test_disable_detaches_delivery_keeps_harvester(self):
        agent = _AbilityAgent(
            1,
            owner=None,
            raw_level=25,
            script_keys=["harvester_script", "delivery_behavior"],
            enabled_abilities=["delivery"],
        )
        caller, system = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1 delivery off")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("disabled", output.lower())
        keys = _script_keys(agent)
        self.assertNotIn("delivery_behavior", keys)
        self.assertIn("harvester_script", keys)
        # Enabled flag cleared so it will not auto re-attach.
        self.assertNotIn("delivery", system.get_enabled_abilities(agent))

    # -- Req 16.5: status reports locked / available / enabled ------- #

    def test_status_reports_locked(self):
        # effective 10 < 21 → locked (Lv 21)
        agent = _AbilityAgent(1, owner=None, raw_level=10)
        caller, _ = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("delivery", output)
        self.assertIn("locked", output.lower())
        self.assertIn("21", output)

    def test_status_reports_available(self):
        # effective 25 >= 21 but not enabled → available
        agent = _AbilityAgent(1, owner=None, raw_level=25)
        caller, _ = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("available", output.lower())

    def test_status_reports_enabled(self):
        agent = _AbilityAgent(
            1, owner=None, raw_level=25, enabled_abilities=["delivery"]
        )
        caller, _ = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("enabled", output.lower())

    # -- Req 16.6: unknown ability key rejected (real backend) ------- #

    def test_unknown_key_rejected(self):
        agent = _AbilityAgent(1, owner=None, raw_level=25)
        caller, system = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 1 bogus on")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("Unknown ability", output)
        self.assertIn("bogus", output)
        # Nothing recorded or attached for the bogus key.
        self.assertEqual(system.get_enabled_abilities(agent), set())
        self.assertEqual(_script_keys(agent), [])

    # -- Req 16.7: unowned / missing agent rejected (real backend) --- #

    def test_unowned_agent_rejected(self):
        agent = _AbilityAgent(1, owner=None, raw_level=25)
        caller, _ = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 999 delivery on")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("not found", output.lower())
        self.assertIn("#999", output)

    def test_unowned_agent_rejected_on_status(self):
        agent = _AbilityAgent(1, owner=None, raw_level=25)
        caller, _ = self._caller_with([agent], owner_level=30)

        cmd = _make_cmd(caller, " ability 999")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("not found", output.lower())
        self.assertIn("#999", output)


if __name__ == "__main__":
    unittest.main()
