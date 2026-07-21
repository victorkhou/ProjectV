"""
Unit tests for the cross-planet travel commands (CmdLaunch, CmdRecall,
CmdLoad, CmdUnload) added in Phase 2b/2c.
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

import world.utils  # noqa: E402

# Patch building_has_capability to use the fake's _capabilities list directly.
_original_bhc = world.utils.building_has_capability


def _fake_building_has_capability(building, capability, provider=None):
    caps = getattr(building, "_capabilities", None)
    if caps is not None:
        return capability in caps
    return _original_bhc(building, capability, provider=provider)


world.utils.building_has_capability = _fake_building_has_capability

from commands.game_commands import CmdLaunch, CmdRecall, CmdLoad, CmdUnload  # noqa: E402


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _FakeBuilding:
    def __init__(self, capabilities=None, planet="terra", x=5, y=5):
        self.db = types.SimpleNamespace(
            coord_planet=planet,
            coord_x=x,
            coord_y=y,
            manifest=None,
        )
        self._capabilities = capabilities or []

    def get_capabilities(self):
        return self._capabilities


class _FakeAgent:
    def __init__(self, agent_id):
        self.id = agent_id
        self.db = types.SimpleNamespace(
            reserve=False,
            incapacitated=False,
            owner=None,
        )


class _FakeAgentSystem:
    def __init__(self, agents=None):
        self._agents = {a.id: a for a in (agents or [])}

    def get_agent_by_id(self, player, agent_id):
        return self._agents.get(agent_id)


class _FakeRankSystem:
    def __init__(self, accessible=None):
        self._accessible = accessible or set()

    def can_access_planet(self, player, planet_key):
        return planet_key in self._accessible


class _FakeSpace:
    def __init__(self, spawn_x=200, spawn_y=200, rank_requirement=1):
        self.spawn_x = spawn_x
        self.spawn_y = spawn_y
        self.rank_requirement = rank_requirement


class _FakeRegistry:
    def __init__(self, spaces=None):
        self._spaces = spaces or {}
        self.balance = types.SimpleNamespace(
            travel_cooldown_ticks=300,
            travel_cooldown_owned_ticks=120,
            travel_manifest_weight_per_level=200,
            travel_fuel_per_agent=1,
            travel_fuel_per_hop=1,
        )

    def get_space(self, key):
        if key in self._spaces:
            return self._spaces[key]
        raise KeyError(key)


class _FakePlanetRoom:
    def __init__(self):
        self._buildings = []

    def get_buildings_at(self, x, y):
        return self._buildings

    def move_entity(self, obj, x, y, notify=False):
        obj.db.coord_x = x
        obj.db.coord_y = y


class _NDB:
    def __init__(self, systems):
        self.systems = systems


class _Caller:
    def __init__(self, planet="terra", x=5, y=5, buildings=None, supplies=None,
                 resources=None, systems=None):
        self.db = types.SimpleNamespace(
            coord_planet=planet,
            coord_x=x,
            coord_y=y,
            combat_timer_expires=0,
            current_tick=100,
            last_launch_tick=0,
            supplies=supplies or {},
            resources=resources or {},
            inside_building=False,
        )
        self.ndb = _NDB(systems or {})
        self._messages = []
        self._executed = []
        self._buildings = buildings or []
        self.location = _FakePlanetRoom()

    def msg(self, text=None, **kw):
        if text is not None:
            self._messages.append(text)

    def execute_cmd(self, cmd, **kw):
        self._executed.append(cmd)

    def get_buildings(self):
        return self._buildings

    def last(self):
        return self._messages[-1] if self._messages else ""


def _run(cmd_cls, caller, args="", building=None):
    """Execute a command with a fake building at the caller's tile."""
    cmd = cmd_cls()
    cmd.caller = caller
    cmd.args = args
    cmd.session = None
    cmd.cmdstring = getattr(cmd_cls, "key", "")

    # Patch _building_at_caller to return our fake building
    cmd._building_at_caller = lambda c, **kw: building

    # Patch require_system to return from caller.ndb.systems
    def _require(name, label=None):
        sys = caller.ndb.systems.get(name)
        if sys is None:
            caller.msg(f"{label or name} not available.")
        return sys
    cmd.require_system = _require

    cmd.func()
    return caller


# -------------------------------------------------------------- #
#  CmdLaunch tests
# -------------------------------------------------------------- #

class TestCmdLaunch(unittest.TestCase):
    def test_no_pad_shows_teaching_message(self):
        """Without a Launch Pad, launch explains how to build one."""
        c = _Caller()
        _run(CmdLaunch, c, "", building=None)
        self.assertIn("Launch Pad", c.last())
        self.assertIn("build", c.last().lower())

    def test_surface_launch_no_fuel_refused(self):
        """Launching without fuel tells you what you need."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(supplies={})
        _run(CmdLaunch, c, "", building=pad)
        self.assertIn("fuel", c.last().lower())

    def test_surface_launch_with_fuel_moves_to_space(self):
        """With fuel, launch from surface moves to space."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(supplies={"basic_fuel_cell": 5})

        # Patch _do_travel to just record the destination
        cmd = CmdLaunch()
        cmd.caller = c
        cmd.args = ""
        cmd.session = None
        cmd.cmdstring = "launch"
        cmd._building_at_caller = lambda caller, **kw: pad
        cmd.require_system = lambda n, **kw: None
        travel_log = []
        cmd._do_travel = lambda caller, dest, *a, **kw: travel_log.append(dest)
        cmd._get_balance = lambda caller: _FakeRegistry().balance
        cmd.func()

        self.assertEqual(travel_log, ["space"])
        # Fuel consumed
        self.assertEqual(c.db.supplies.get("basic_fuel_cell", 0), 4)

    def test_from_space_with_arg_checks_access(self):
        """From Space, launch <planet> checks can_access_planet."""
        rank_sys = _FakeRankSystem(accessible={"terra"})
        c = _Caller(planet="space", systems={"rank": rank_sys},
                    supplies={"basic_fuel_cell": 5})
        # Try to launch to forge (not accessible)
        _run(CmdLaunch, c, "forge", building=None)
        self.assertIn("cannot access", c.last().lower())

    def test_from_space_launch_succeeds(self):
        """From Space with access + fuel, launch to destination works."""
        rank_sys = _FakeRankSystem(accessible={"terra", "forge"})
        c = _Caller(planet="space", systems={"rank": rank_sys},
                    supplies={"basic_fuel_cell": 5})

        cmd = CmdLaunch()
        cmd.caller = c
        cmd.args = "terra"
        cmd.session = None
        cmd.cmdstring = "launch"
        cmd._building_at_caller = lambda caller, **kw: None
        cmd.require_system = lambda n, **kw: rank_sys
        travel_log = []
        cmd._do_travel = lambda caller, dest, tx=None, ty=None: travel_log.append(dest)
        cmd._resolve_arrival = lambda caller, dest: (200, 200)
        cmd.func()

        self.assertEqual(travel_log, ["terra"])
        self.assertEqual(c.db.supplies.get("basic_fuel_cell", 0), 4)

    def test_surface_with_planet_arg_refused(self):
        """From surface, launch <planet> tells you to go to Space first."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(supplies={"basic_fuel_cell": 5})
        _run(CmdLaunch, c, "forge", building=pad)
        self.assertIn("Space", c.last())


# -------------------------------------------------------------- #
#  CmdRecall tests
# -------------------------------------------------------------- #

class TestCmdRecall(unittest.TestCase):
    def test_no_beacon_refuses(self):
        """Without a Respawn Beacon, recall tells you to build one."""
        c = _Caller(planet="forge")
        _run(CmdRecall, c, "", building=None)
        self.assertIn("Respawn Beacon", c.last())

    def test_same_planet_refuses(self):
        """Recalling while on the same planet as your beacon is blocked."""
        beacon = _FakeBuilding(capabilities=["respawn_point"], planet="terra")
        c = _Caller(planet="terra", buildings=[beacon])
        _run(CmdRecall, c, "", building=None)
        self.assertIn("same planet", c.last().lower())

    def test_in_combat_blocked(self):
        """Cannot recall while in combat."""
        beacon = _FakeBuilding(capabilities=["respawn_point"], planet="terra")
        c = _Caller(planet="forge", buildings=[beacon])
        c.db.combat_timer_expires = 999  # combat active
        c.db.current_tick = 100
        _run(CmdRecall, c, "", building=None)
        self.assertIn("combat", c.last().lower())


# -------------------------------------------------------------- #
#  CmdLoad / CmdUnload tests
# -------------------------------------------------------------- #

class TestCmdLoad(unittest.TestCase):
    def test_no_pad_refused(self):
        """Load requires standing on a Launch Pad."""
        c = _Caller()
        _run(CmdLoad, c, "100 Wood", building=None)
        self.assertIn("Launch Pad", c.last())

    def test_load_resource_success(self):
        """Loading resources transfers them to the pad manifest."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(resources={"Wood": 500})
        _run(CmdLoad, c, "200 Wood", building=pad)
        self.assertEqual(c.db.resources["Wood"], 300)
        manifest = pad.db.manifest
        self.assertEqual(manifest["resources"]["Wood"], 200)

    def test_load_resource_insufficient(self):
        """Loading more than you have is refused."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(resources={"Wood": 50})
        _run(CmdLoad, c, "200 Wood", building=pad)
        self.assertIn("only have", c.last().lower())

    def test_load_agent_success(self):
        """Loading an agent puts it in reserve and adds to manifest."""
        agent = _FakeAgent(3)
        agent_sys = _FakeAgentSystem([agent])
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(systems={"agent": agent_sys})
        _run(CmdLoad, c, "agent 3", building=pad)
        self.assertTrue(agent.db.reserve)
        self.assertIn(3, pad.db.manifest["agents"])

    def test_load_agent_already_reserved(self):
        """Cannot load an agent that's already in reserve."""
        agent = _FakeAgent(3)
        agent.db.reserve = True
        agent_sys = _FakeAgentSystem([agent])
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller(systems={"agent": agent_sys})
        _run(CmdLoad, c, "agent 3", building=pad)
        self.assertIn("already in reserve", c.last().lower())

    def test_show_manifest_when_empty(self):
        """No args shows the manifest (or empty hint)."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        c = _Caller()
        _run(CmdLoad, c, "", building=pad)
        self.assertIn("empty", c.last().lower())


class TestCmdUnload(unittest.TestCase):
    def test_unload_resource(self):
        """Unloading resources returns them to the player."""
        pad = _FakeBuilding(capabilities=["launch_pad"])
        pad.db.manifest = {"resources": {"Wood": 300}, "agents": []}
        c = _Caller(resources={"Wood": 100})
        _run(CmdUnload, c, "200 Wood", building=pad)
        self.assertEqual(c.db.resources["Wood"], 300)
        self.assertEqual(pad.db.manifest["resources"].get("Wood", 0), 100)

    def test_unload_agent(self):
        """Unloading an agent returns it from reserve."""
        agent = _FakeAgent(3)
        agent.db.reserve = True
        agent_sys = _FakeAgentSystem([agent])
        pad = _FakeBuilding(capabilities=["launch_pad"])
        pad.db.manifest = {"agents": [3], "resources": {}}
        c = _Caller(systems={"agent": agent_sys})
        _run(CmdUnload, c, "agent 3", building=pad)
        self.assertFalse(agent.db.reserve)
        self.assertNotIn(3, pad.db.manifest["agents"])

    def test_unload_all(self):
        """Unload all returns everything."""
        agent = _FakeAgent(2)
        agent.db.reserve = True
        agent_sys = _FakeAgentSystem([agent])
        pad = _FakeBuilding(capabilities=["launch_pad"])
        pad.db.manifest = {"agents": [2], "resources": {"Iron": 50}}
        c = _Caller(resources={"Iron": 10}, systems={"agent": agent_sys})
        _run(CmdUnload, c, "all", building=pad)
        self.assertEqual(c.db.resources["Iron"], 60)
        self.assertFalse(agent.db.reserve)
        self.assertEqual(pad.db.manifest, {})


if __name__ == "__main__":
    unittest.main()
