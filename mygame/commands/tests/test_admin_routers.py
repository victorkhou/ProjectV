"""
Unit tests for admin command routers.

Tests subcommand delegation, permission enforcement, and admin logging
for CmdAdminBuilding, CmdAdminAgent, CmdAdminResource, CmdAdminPlayer.

Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.7, 2.8, 3.1, 3.2,
              3.5, 3.6, 4.1, 4.2, 4.5, 8.1
"""

import sys
import types
import unittest
import logging

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

from mygame.commands.admin_commands import (  # noqa: E402
    CmdAdminBuilding,
    CmdAdminAgent,
    CmdAdminResource,
    CmdAdminItem,
    CmdAdminPlayer,
    CmdAdminOutpost,
    CmdAdminAlliance,
    CmdTeleport,
    CmdTransfer,
)


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
    """Fake caller with configurable permission checks."""

    def __init__(self, name="Admin", perm_level="Admin", systems=None):
        self.key = name
        self._perm_level = perm_level
        self.ndb = FakeNDB(systems)
        self.db = FakeDB()
        self._messages = []
        self._executed = []  # records execute_cmd calls (e.g. the post-transfer look)
        self._search_results = {}
        self.location = None

    # Permission hierarchy used by check_permstring
    _HIERARCHY = ["Player", "Helper", "Builder", "Admin", "Developer"]

    def check_permstring(self, perm):
        try:
            required = self._HIERARCHY.index(perm)
            actual = self._HIERARCHY.index(self._perm_level)
            return actual >= required
        except ValueError:
            return False

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def execute_cmd(self, cmd, **kwargs):
        self._executed.append(cmd)

    def search(self, name, **kwargs):
        return self._search_results.get(name)


class FakeBuilding:
    """Fake building object for @building destroy tests."""

    def __init__(self, key="HQ", building_type="HQ"):
        self.key = key
        self._deleted = False

        class _Attrs:
            def __init__(self, btype):
                self._data = {"building_type": btype}
            def get(self, key, default=None, **kw):
                return self._data.get(key, default)
            def add(self, key, value, **kw):
                self._data[key] = value

        self.attributes = _Attrs(building_type)

    def delete(self):
        self._deleted = True


class FakeLocation:
    """Fake PlanetRoom with get_objects_at."""

    def __init__(self, buildings=None):
        self._buildings = buildings or []
        self.key = "TestPlanet"

    def get_objects_at(self, x, y, type_tag=None):
        return self._buildings


class FakeAgent:
    """Fake agent NPC."""

    def __init__(self, agent_id=1, key="Agent-1"):
        self.key = key
        self.db = FakeDB(agent_id=agent_id, role="soldier", role_target=None)
        self.id = agent_id
        self._deleted = False

    def delete(self):
        self._deleted = True


class FakeAgentSystem:
    """Fake agent_system with get_agents, _create_npc_func, etc."""

    def __init__(self, agents=None):
        self._agents = agents or []
        self._created = []

    def get_agents(self, target):
        return self._agents

    def get_agent_count(self, target):
        return len(self._agents)

    def get_agent_by_id(self, target, agent_id):
        for a in self._agents:
            if getattr(a.db, "agent_id", None) == agent_id:
                return a
        return None

    def _create_npc_func(self, target, next_id):
        npc = FakeAgent(agent_id=next_id, key=f"Agent-{next_id}")
        self._created.append(npc)
        self._agents.append(npc)
        return npc


class FakeTarget:
    """Fake player target for resource/player commands."""

    def __init__(self, name="Player1"):
        self.key = name
        self.db = FakeDB()
        self._messages = []
        self._resources = {}

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def add_resource(self, resource_type, amount):
        self._resources[resource_type] = self._resources.get(resource_type, 0) + amount


def _make_cmd(cmd_class, caller, args=""):
    cmd = cmd_class()
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    return cmd


# -------------------------------------------------------------- #
#  CmdAdminBuilding tests
# -------------------------------------------------------------- #

class TestBuildingSpawnDelegation(unittest.TestCase):
    """Req 1.1: @building spawn delegates to spawn logic."""

    def test_spawn_no_args_shows_usage(self):
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_spawn_delegates_to_sub_spawn(self):
        """Spawn with valid type reaches the handler (fails gracefully
        without a real registry/create_object, but proves delegation)."""
        caller = FakeCaller(perm_level="Builder")
        caller.location = FakeLocation()
        caller.db.coord_x = 5
        caller.db.coord_y = 10
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn HQ")
        cmd.func()
        # Without a registry it will still attempt to create — we just
        # verify it didn't show "Unknown subcommand" or "Permission denied"
        self.assertFalse(any("Unknown subcommand" in m for m in caller._messages))
        self.assertFalse(any("Permission denied" in m for m in caller._messages))


class FakeBuildingDef:
    """Minimal BuildingDef stand-in for @building list/index tests."""

    def __init__(self, abbreviation, name, category="", max_health=500):
        self.abbreviation = abbreviation
        self.name = name
        self.category = category
        self.max_health = max_health


class FakeBuildingRegistry:
    """Registry exposing buildings + resolve_building for @building tests."""

    def __init__(self, defs):
        self.buildings = {d.abbreviation: d for d in defs}

    def get_building(self, abbr):
        return self.buildings[abbr]

    def resolve_building(self, token):
        t = token.strip().lower().replace("_", " ")
        # exact abbreviation / name, then unambiguous prefix (mirrors registry).
        for d in self.buildings.values():
            if d.abbreviation.lower() == t or d.name.lower().replace("_", " ") == t:
                return d
        matches = [d for d in self.buildings.values()
                   if d.abbreviation.lower().startswith(t)
                   or d.name.lower().replace("_", " ").startswith(t)]
        return matches[0] if len(matches) == 1 else None


_HQ_DEF = FakeBuildingDef("HQ", "Headquarters", category="headquarters")
_EX_DEF = FakeBuildingDef("EX", "Extractor", category="resource")


class TestBuildingList(unittest.TestCase):
    """@building list numbers building types for index spawning."""

    def _caller(self):
        reg = FakeBuildingRegistry([_HQ_DEF, _EX_DEF])
        return FakeCaller(perm_level="Builder", systems={"registry": reg})

    def test_list_shows_types_and_indexes(self):
        caller = self._caller()
        cmd = _make_cmd(CmdAdminBuilding, caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("[1]", output)
        self.assertIn("Headquarters", output)
        self.assertIn("Extractor", output)

    def test_spawn_by_index_resolves_type(self):
        # Sorted by abbreviation: EX(1), HQ(2). Spawn #2 -> Headquarters.
        caller = self._caller()
        caller.location = FakeLocation()
        caller.db.coord_x = 1
        caller.db.coord_y = 1
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn 2")
        cmd.func()
        # No real create_object under stubs, but resolution must not report the
        # type as unknown (index -> Headquarters resolved).
        self.assertFalse(any("Unknown building type" in m for m in caller._messages))

    def test_spawn_unknown_index_reports(self):
        caller = self._caller()
        caller.location = FakeLocation()
        caller.db.coord_x = 1
        caller.db.coord_y = 1
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn 99")
        cmd.func()
        self.assertTrue(any("Unknown building type" in m for m in caller._messages))


class TestBuildingDestroy(unittest.TestCase):
    """Req 1.2: @building destroy removes building at caller's tile."""

    def test_destroy_deletes_building(self):
        building = FakeBuilding(key="HQ", building_type="HQ")
        location = FakeLocation(buildings=[building])
        caller = FakeCaller(perm_level="Builder")
        caller.location = location
        caller.db.coord_x = 3
        caller.db.coord_y = 7

        cmd = _make_cmd(CmdAdminBuilding, caller, " destroy")
        cmd.func()

        self.assertTrue(building._deleted)
        self.assertTrue(any("Destroyed" in m for m in caller._messages))

    def test_destroy_no_building_at_tile(self):
        location = FakeLocation(buildings=[])
        caller = FakeCaller(perm_level="Builder")
        caller.location = location
        caller.db.coord_x = 3
        caller.db.coord_y = 7

        cmd = _make_cmd(CmdAdminBuilding, caller, " destroy")
        cmd.func()
        self.assertTrue(any("No building" in m for m in caller._messages))

    def test_open_close_toggles_building(self):
        building = FakeBuilding(key="Wall", building_type="WA")
        location = FakeLocation(buildings=[building])
        caller = FakeCaller(perm_level="Builder")
        caller.location = location
        caller.db.coord_x = 3
        caller.db.coord_y = 7

        # Close it.
        cmd = _make_cmd(CmdAdminBuilding, caller, " open close")
        cmd.func()
        self.assertFalse(building.attributes.get("open"))
        self.assertTrue(any("closed" in m.lower() for m in caller._messages))

        # Re-open it.
        caller._messages.clear()
        cmd = _make_cmd(CmdAdminBuilding, caller, " open")
        cmd.func()
        self.assertTrue(building.attributes.get("open"))
        self.assertTrue(any("open" in m.lower() for m in caller._messages))


# -------------------------------------------------------------- #
#  CmdAdminAgent tests
# -------------------------------------------------------------- #

class TestAgentCreate(unittest.TestCase):
    """Req 2.1: @agent create delegates to agent creation logic."""

    def test_create_agent_for_player(self):
        target = FakeTarget(name="Bob")
        target.db.next_agent_id = 1
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(name="Admin", perm_level="Admin",
                            systems={"agent_system": agent_sys})
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminAgent, caller, " create Bob")
        cmd.func()

        self.assertEqual(len(agent_sys._created), 1)
        self.assertTrue(any("Created" in m for m in caller._messages))

    def test_create_no_args_shows_usage(self):
        caller = FakeCaller(perm_level="Admin")
        cmd = _make_cmd(CmdAdminAgent, caller, " create")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


class TestAgentDestroy(unittest.TestCase):
    """Req 2.2: @agent destroy <id> <player> delegates to destruction."""

    def test_destroy_agent_by_id(self):
        agent = FakeAgent(agent_id=1, key="Agent-1")
        agent_sys = FakeAgentSystem(agents=[agent])
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Admin",
                            systems={"agent_system": agent_sys})
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminAgent, caller, " destroy 1 Bob")
        cmd.func()

        self.assertTrue(agent._deleted)
        self.assertTrue(any("Destroyed" in m for m in caller._messages))


class TestAgentDestroyTraining(unittest.TestCase):
    """Req 2.3: @agent destroy training <player> clears training state."""

    def test_destroy_training_delegates(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Admin")
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminAgent, caller, " destroy training Bob")
        cmd.func()

        # Should report clearing (even if 0 buildings cleared)
        self.assertTrue(any("Cleared" in m or "cleared" in m.lower()
                            for m in caller._messages))


class TestAgentList(unittest.TestCase):
    """Req 2.4: @agent list <player> delegates to listing logic."""

    def test_list_agents(self):
        agent = FakeAgent(agent_id=1, key="Agent-1")
        agent_sys = FakeAgentSystem(agents=[agent])
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Builder",
                            systems={"agent_system": agent_sys})
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminAgent, caller, " list Bob")
        cmd.func()

        output = "\n".join(caller._messages)
        self.assertIn("Bob", output)
        self.assertIn("#1", output)


# -------------------------------------------------------------- #
#  CmdAdminResource tests
# -------------------------------------------------------------- #

class TestResourceGive(unittest.TestCase):
    """Req 3.1, 3.2: @resource give delegates to resource-giving logic."""

    def test_give_resource_to_target(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Builder")
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminResource, caller, " give Iron 100 Bob")
        cmd.func()

        self.assertEqual(target._resources.get("Iron"), 100)
        self.assertTrue(any("Gave 100 Iron" in m for m in caller._messages))

    def test_give_no_args_shows_usage(self):
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminResource, caller, " give")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


class TestResourceReset(unittest.TestCase):
    """Req 3.5, 3.6: @resource reset requires Admin+."""

    def test_reset_no_args_attempts_reset(self):
        """Reset with no player arg tries to reset all — will fail
        gracefully without DB, but proves delegation happened."""
        caller = FakeCaller(perm_level="Admin")
        cmd = _make_cmd(CmdAdminResource, caller, " reset")
        cmd.func()
        # Should attempt the reset path (not show "Unknown subcommand")
        self.assertFalse(any("Unknown subcommand" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  CmdAdminPlayer tests
# -------------------------------------------------------------- #

class TestPlayerLevel(unittest.TestCase):
    """Req 4.1: @player level delegates to level-setting logic."""

    def test_level_sets_on_target(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Admin")
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminPlayer, caller, " level 5 Bob")
        cmd.func()

        self.assertEqual(target.db.level, 5)
        self.assertTrue(any("Set Bob to level 5" in m for m in caller._messages))

    def test_level_no_args_shows_usage(self):
        caller = FakeCaller(perm_level="Admin")
        cmd = _make_cmd(CmdAdminPlayer, caller, " level")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


class TestPlayerRank(unittest.TestCase):
    """Req 4.2: @player rank delegates to rank-setting logic."""

    def test_rank_sets_on_target(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Admin")
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminPlayer, caller, " rank 3 Bob")
        cmd.func()

        self.assertEqual(target.db.rank_level, 3)
        self.assertTrue(any("Bob" in m and "rank" in m.lower() for m in caller._messages))

    def test_rank_no_args_shows_usage(self):
        caller = FakeCaller(perm_level="Admin")
        cmd = _make_cmd(CmdAdminPlayer, caller, " rank")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  Permission enforcement tests
# -------------------------------------------------------------- #

class TestPermissionEnforcement(unittest.TestCase):
    """Req 2.7, 2.8, 3.5, 3.6, 4.5: Per-subcommand permission checks."""

    def test_agent_create_denied_for_builder(self):
        """@agent create requires Admin+; Builder should be denied."""
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminAgent, caller, " create Bob")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_agent_list_allowed_for_builder(self):
        """@agent list requires Builder+; Builder should be allowed."""
        target = FakeTarget(name="Bob")
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(perm_level="Builder",
                            systems={"agent_system": agent_sys})
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminAgent, caller, " list Bob")
        cmd.func()
        self.assertFalse(any("Permission denied" in m for m in caller._messages))

    def test_agent_destroy_denied_for_builder(self):
        """@agent destroy requires Admin+; Builder should be denied."""
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminAgent, caller, " destroy 1 Bob")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_resource_give_allowed_for_builder(self):
        """@resource give requires Builder+; Builder should be allowed."""
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Builder")
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminResource, caller, " give Iron 10 Bob")
        cmd.func()
        self.assertFalse(any("Permission denied" in m for m in caller._messages))

    def test_resource_reset_denied_for_builder(self):
        """@resource reset requires Admin+; Builder should be denied."""
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminResource, caller, " reset Bob")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_player_level_denied_for_builder(self):
        """@player level requires Admin+; Builder should be denied."""
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminPlayer, caller, " level 5 Bob")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_player_rank_denied_for_builder(self):
        """@player rank requires Admin+; Builder should be denied."""
        caller = FakeCaller(perm_level="Builder")
        cmd = _make_cmd(CmdAdminPlayer, caller, " rank 3 Bob")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_building_spawn_allowed_for_builder(self):
        """@building spawn requires Builder+; Builder should be allowed."""
        caller = FakeCaller(perm_level="Builder")
        caller.location = FakeLocation()
        caller.db.coord_x = 1
        caller.db.coord_y = 1
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn HQ")
        cmd.func()
        self.assertFalse(any("Permission denied" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  Admin logging tests
# -------------------------------------------------------------- #

class TestAdminLogging(unittest.TestCase):
    """Req 8.1: Admin logging on successful actions."""

    def test_building_destroy_logs(self):
        building = FakeBuilding(key="HQ", building_type="HQ")
        location = FakeLocation(buildings=[building])
        caller = FakeCaller(name="AdminUser", perm_level="Builder")
        caller.location = location
        caller.db.coord_x = 3
        caller.db.coord_y = 7

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd = _make_cmd(CmdAdminBuilding, caller, " destroy")
            cmd.func()

        log_output = "\n".join(cm.output)
        self.assertIn("AdminUser", log_output)
        self.assertIn("destroy", log_output)

    def test_agent_create_logs(self):
        target = FakeTarget(name="Bob")
        target.db.next_agent_id = 1
        agent_sys = FakeAgentSystem()
        caller = FakeCaller(name="AdminUser", perm_level="Admin",
                            systems={"agent_system": agent_sys})
        caller._search_results["Bob"] = target

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd = _make_cmd(CmdAdminAgent, caller, " create Bob")
            cmd.func()

        log_output = "\n".join(cm.output)
        self.assertIn("AdminUser", log_output)
        self.assertIn("create", log_output)
        self.assertIn("Bob", log_output)

    def test_resource_give_logs(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(name="AdminUser", perm_level="Builder")
        caller._search_results["Bob"] = target

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd = _make_cmd(CmdAdminResource, caller, " give Iron 50 Bob")
            cmd.func()

        log_output = "\n".join(cm.output)
        self.assertIn("AdminUser", log_output)
        self.assertIn("give", log_output)
        self.assertIn("Iron", log_output)

    def test_agent_list_logs(self):
        agent_sys = FakeAgentSystem()
        target = FakeTarget(name="Bob")
        caller = FakeCaller(name="AdminUser", perm_level="Builder",
                            systems={"agent_system": agent_sys})
        caller._search_results["Bob"] = target

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd = _make_cmd(CmdAdminAgent, caller, " list Bob")
            cmd.func()

        log_output = "\n".join(cm.output)
        self.assertIn("AdminUser", log_output)
        self.assertIn("list", log_output)

    def test_player_level_logs(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(name="AdminUser", perm_level="Admin")
        caller._search_results["Bob"] = target

        with self.assertLogs("mygame.admin", level="INFO") as cm:
            cmd = _make_cmd(CmdAdminPlayer, caller, " level 5 Bob")
            cmd.func()

        log_output = "\n".join(cm.output)
        self.assertIn("AdminUser", log_output)
        self.assertIn("level", log_output)


# -------------------------------------------------------------- #
#  CmdAdminItem tests
# -------------------------------------------------------------- #

class FakeItemDef:
    """Minimal ItemDef stand-in for @item spawn/list tests."""

    def __init__(self, key, name, category, slot="", max_stack=99, weight=1.0):
        self.key = key
        self.name = name
        self.category = category
        self.slot = slot
        self.max_stack = max_stack
        self.weight = weight


class FakeItemRegistry:
    """Registry exposing resolve_item / get_item / items for item tests."""

    def __init__(self, defs):
        self.items = {d.key: d for d in defs}

    def resolve_item(self, token):
        # Mirror DataRegistry._resolve: exact key / name, then unambiguous prefix.
        t = token.strip().lower().replace("_", " ")
        for d in self.items.values():
            if d.key.lower().replace("_", " ") == t or d.name.lower() == t:
                return d
        matches = [d for d in self.items.values()
                   if d.key.lower().replace("_", " ").startswith(t)
                   or d.name.lower().replace("_", " ").startswith(t)]
        return matches[0] if len(matches) == 1 else None

    def get_item(self, key):
        return self.items[key]


class FakeEquipment:
    """Supply_Bag stand-in honoring the per-entry max_stack cap."""

    def __init__(self):
        self.supplies = {}

    def add_supply(self, item_key, count, max_stack=99):
        current = self.supplies.get(item_key, 0)
        added = max(0, min(count, max_stack - current))
        self.supplies[item_key] = current + added
        return added


# A rifle (Gear) and grenades (Supply) cover both spawn branches.
_RIFLE = FakeItemDef("assault_rifle", "Assault Rifle", "weapon", slot="weapon", weight=10.0)
_GRENADE = FakeItemDef("frag_grenade", "Frag Grenade", "throwable", max_stack=5, weight=3.0)


def _item_caller(perm_level="Builder"):
    registry = FakeItemRegistry([_RIFLE, _GRENADE])
    return FakeCaller(perm_level=perm_level, systems={"registry": registry})


class TestItemSpawnGear(unittest.TestCase):
    """@item spawn <gear> creates equippable objects in the recipient's inv."""

    def test_spawn_gear_creates_objects(self):
        import unittest.mock as mock

        target = FakeTarget(name="Bob")
        caller = _item_caller()
        caller._search_results["Bob"] = target

        created = []
        fake_objects = types.ModuleType("typeclasses.objects")
        fake_objects.create_game_item = lambda owner, idef: created.append((owner, idef))
        with mock.patch.dict(sys.modules, {
            "typeclasses": types.ModuleType("typeclasses"),
            "typeclasses.objects": fake_objects,
        }):
            cmd = _make_cmd(CmdAdminItem, caller, " spawn assault_rifle 2 Bob")
            cmd.func()

        self.assertEqual(len(created), 2)
        self.assertTrue(all(idef is _RIFLE and owner is target for owner, idef in created))
        self.assertTrue(any("Spawned 2x Assault Rifle" in m for m in caller._messages))

    def test_spawn_gear_defaults_to_caller(self):
        import unittest.mock as mock

        caller = _item_caller()
        created = []
        fake_objects = types.ModuleType("typeclasses.objects")
        fake_objects.create_game_item = lambda owner, idef: created.append((owner, idef))
        with mock.patch.dict(sys.modules, {
            "typeclasses": types.ModuleType("typeclasses"),
            "typeclasses.objects": fake_objects,
        }):
            # No count/player → defaults to 1 for the caller.
            cmd = _make_cmd(CmdAdminItem, caller, " spawn assault_rifle")
            cmd.func()

        self.assertEqual(len(created), 1)
        self.assertIs(created[0][0], caller)  # defaults to caller


class TestItemSpawnSupply(unittest.TestCase):
    """@item spawn <supply> adds counts to the recipient's Supply_Bag."""

    def test_spawn_supply_adds_to_bag(self):
        target = FakeTarget(name="Bob")
        target.equipment = FakeEquipment()
        caller = _item_caller()
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminItem, caller, " spawn frag_grenade 3 Bob")
        cmd.func()

        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 3)
        self.assertTrue(any("Spawned 3x Frag Grenade" in m for m in caller._messages))

    def test_spawn_supply_respects_stack_cap(self):
        target = FakeTarget(name="Bob")
        target.equipment = FakeEquipment()
        caller = _item_caller()
        caller._search_results["Bob"] = target

        # max_stack=5, request 8 → 5 added, 3 reported as exceeding the cap.
        cmd = _make_cmd(CmdAdminItem, caller, " spawn frag_grenade 8 Bob")
        cmd.func()

        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 5)
        self.assertTrue(any("exceeded the stack cap" in m for m in caller._messages))


class TestItemSpawnErrors(unittest.TestCase):
    """@item spawn input validation."""

    def test_spawn_no_args_shows_usage(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " spawn")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_spawn_unknown_item(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " spawn nonexistent")
        cmd.func()
        self.assertTrue(any("Unknown item" in m for m in caller._messages))

    def test_spawn_unknown_player(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " spawn frag_grenade 1 Nobody")
        cmd.func()
        self.assertTrue(any("Could not find player" in m for m in caller._messages))


class TestItemSpawnByIndexAndPrefix(unittest.TestCase):
    """@item spawn accepts an index (#N / N from '@item list') or a prefix."""

    def _ordered_keys(self):
        # Same order sub_list / _item_index use: sorted by (category, key).
        return [d.key for d in sorted(
            FakeItemRegistry([_RIFLE, _GRENADE]).items.values(),
            key=lambda d: (d.category, d.key),
        )]

    def test_spawn_by_index(self):
        target = FakeTarget(name="Bob")
        target.equipment = FakeEquipment()
        caller = _item_caller()
        caller._search_results["Bob"] = target
        # frag_grenade is a throwable (category 'throwable'); assault_rifle is
        # 'weapon'. Sorted by (category, key): throwable < weapon, so [1] is the
        # grenade. Spawn it by index.
        keys = self._ordered_keys()
        idx = keys.index("frag_grenade") + 1
        cmd = _make_cmd(CmdAdminItem, caller, f" spawn {idx} 2 Bob")
        cmd.func()
        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 2)

    def test_spawn_by_hash_index(self):
        target = FakeTarget(name="Bob")
        target.equipment = FakeEquipment()
        caller = _item_caller()
        caller._search_results["Bob"] = target
        keys = self._ordered_keys()
        idx = keys.index("frag_grenade") + 1
        cmd = _make_cmd(CmdAdminItem, caller, f" spawn #{idx} 1 Bob")
        cmd.func()
        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 1)

    def test_spawn_by_prefix(self):
        target = FakeTarget(name="Bob")
        target.equipment = FakeEquipment()
        caller = _item_caller()
        caller._search_results["Bob"] = target
        # "frag" uniquely prefixes frag_grenade.
        cmd = _make_cmd(CmdAdminItem, caller, " spawn frag 3 Bob")
        cmd.func()
        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 3)

    def test_spawn_index_out_of_range_is_unknown(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " spawn 99")
        cmd.func()
        self.assertTrue(any("Unknown item" in m for m in caller._messages))


class TestItemList(unittest.TestCase):
    """@item list enumerates definitions, optionally filtered."""

    def test_list_all(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("assault_rifle", output)
        self.assertIn("frag_grenade", output)

    def test_list_shows_index_numbers(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("[1]", output)
        self.assertIn("[2]", output)

    def test_list_filter_by_category(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " list weapon")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("assault_rifle", output)
        self.assertNotIn("frag_grenade", output)

    def test_list_filter_no_match(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " list bogus")
        cmd.func()
        self.assertTrue(any("No items match" in m for m in caller._messages))


class TestItemPermissions(unittest.TestCase):
    """@item subcommands require Builder+."""

    def test_spawn_denied_for_player(self):
        registry = FakeItemRegistry([_RIFLE, _GRENADE])
        caller = FakeCaller(perm_level="Player", systems={"registry": registry})
        cmd = _make_cmd(CmdAdminItem, caller, " spawn frag_grenade")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))


class _RecordingRoom:
    """PlanetRoom stand-in that records move_entity's notify kwarg."""

    def __init__(self):
        self.calls = []  # (obj, x, y, notify)

    def move_entity(self, obj, new_x, new_y, notify=True):
        self.calls.append((obj, new_x, new_y, notify))
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y


class _FakePlanetRegistry:
    def resolve_planet(self, token):
        return "earth"

    def is_valid_coordinate(self, x, y, planet):
        return True


class _EntityStub:
    """A goto-target stand-in: any object with a key + coords + planet."""

    def __init__(self, key, x, y, planet):
        self.key = key
        self.db = FakeDB(coord_x=x, coord_y=y, coord_planet=planet)


class TestTeleportSuppressesNotifications(unittest.TestCase):
    """Regression: @teleport must relocate silently (notify=False).

    A teleport is not a step onto an adjacent tile; for a cross-planet jump the
    stored old coords belong to the origin planet, so arrival/departure
    messaging would notify the wrong players. CmdTeleport must pass
    notify=False to move_entity.
    """

    def test_teleport_calls_move_entity_with_notify_false(self):
        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _FakePlanetRegistry()},
        )
        caller.db.coord_planet = "earth"
        caller.location = room  # same-planet -> no move_to needed

        # Patch the game_systems dict CmdTeleport imports for planet_rooms.
        from server.conf import game_init
        original = getattr(game_init, "game_systems", None)
        game_init.game_systems = {"planet_rooms": {"earth": room}}
        try:
            cmd = _make_cmd(CmdTeleport, caller, " 25 25 earth")
            cmd.func()
        finally:
            if original is not None:
                game_init.game_systems = original

        self.assertEqual(len(room.calls), 1)
        _obj, tx, ty, notify = room.calls[0]
        self.assertEqual((tx, ty), (25, 25))
        self.assertFalse(notify)  # notifications suppressed

    def test_goto_is_registered_as_an_alias(self):
        """'goto <x> <y> [z]' is an alias for @teleport."""
        self.assertIn("goto", CmdTeleport.aliases)

    def test_goto_teleports_like_at_teleport(self):
        """Invoking via the 'goto' alias moves to the parsed coordinates."""
        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _FakePlanetRegistry()},
        )
        caller.db.coord_planet = "earth"
        caller.location = room

        from server.conf import game_init
        original = getattr(game_init, "game_systems", None)
        game_init.game_systems = {"planet_rooms": {"earth": room}}
        try:
            cmd = _make_cmd(CmdTeleport, caller, " 50 50 2")
            cmd.cmdstring = "goto"  # invoked via the alias
            cmd.func()
        finally:
            if original is not None:
                game_init.game_systems = original

        self.assertEqual(len(room.calls), 1)
        _obj, tx, ty, _notify = room.calls[0]
        self.assertEqual((tx, ty), (50, 50))

    def _entity_goto(self, caller, arg, room, search_results):
        """Run 'goto <arg>' with the given search results, patching planet_rooms."""
        caller._search_results = search_results
        from server.conf import game_init
        original = getattr(game_init, "game_systems", None)
        game_init.game_systems = {"planet_rooms": {"earth": room}}
        try:
            cmd = _make_cmd(CmdTeleport, caller, arg)
            cmd.cmdstring = "goto"
            cmd.func()
        finally:
            if original is not None:
                game_init.game_systems = original

    def test_goto_name_jumps_to_entity_tile(self):
        """'goto <name>' teleports the caller to that entity's coordinates."""
        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _FakePlanetRegistry()},
        )
        caller.db.coord_planet = "earth"
        caller.location = room

        target = _EntityStub("Raider", x=30, y=42, planet="earth")
        self._entity_goto(caller, "Raider", room, {"Raider": target})

        self.assertEqual(len(room.calls), 1)
        _obj, tx, ty, notify = room.calls[0]
        self.assertEqual((tx, ty), (30, 42))
        self.assertFalse(notify)  # a teleport is silent
        self.assertTrue(any("Raider" in str(m) for m in caller._messages))

    def test_goto_name_not_found(self):
        """A name with no match is reported, not crashed, and no move happens."""
        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _FakePlanetRegistry()},
        )
        caller.db.coord_planet = "earth"
        self._entity_goto(caller, "Nobody", room, {})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(any("No entity named" in str(m) for m in caller._messages))

    def test_goto_entity_without_coords_is_rejected(self):
        """An entity that isn't on the overworld (no coords) can't be jumped to."""
        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _FakePlanetRegistry()},
        )
        caller.db.coord_planet = "earth"
        target = _EntityStub("Ghost", x=None, y=None, planet=None)
        self._entity_goto(caller, "Ghost", room, {"Ghost": target})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(any("not on the overworld" in str(m) for m in caller._messages))

    def test_goto_entity_on_unknown_planet_is_rejected(self):
        """is_valid_coordinate raises KeyError on an unregistered planet; the
        entity path must catch it and report cleanly, not crash."""
        class _RaisingRegistry:
            def resolve_planet(self, token):
                return None
            def is_valid_coordinate(self, x, y, planet):
                raise KeyError(planet)

        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _RaisingRegistry()},
        )
        caller.db.coord_planet = "earth"
        caller.location = room
        target = _EntityStub("Legacy", x=5, y=5, planet="atlantis")
        self._entity_goto(caller, "Legacy", room, {"Legacy": target})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(any("unknown planet" in str(m) for m in caller._messages))

    def test_goto_ambiguous_prefix_picks_nearest(self):
        """Multiple matches → jump to the closest by Chebyshev distance."""
        room = _RecordingRoom()
        caller = FakeCaller(
            perm_level="Builder",
            systems={"planet_registry": _FakePlanetRegistry()},
        )
        caller.db.coord_planet = "earth"
        caller.db.coord_x, caller.db.coord_y = 10, 10
        caller.location = room  # same-planet -> no cross-planet move_to
        far = _EntityStub("Agent-far", x=90, y=90, planet="earth")
        near = _EntityStub("Agent-near", x=13, y=12, planet="earth")
        self._entity_goto(caller, "Agent", room, {"Agent": [far, near]})
        self.assertEqual(len(room.calls), 1)
        _obj, tx, ty, _notify = room.calls[0]
        self.assertEqual((tx, ty), (13, 12))  # the nearer Agent


# -------------------------------------------------------------- #
#  CmdTransfer tests — pull a unit to the caller's tile
# -------------------------------------------------------------- #

class _UnitStub:
    """A transferable unit stand-in (player or NPC).

    Carries ``combat_xp`` so world.utils.is_player() treats it as movable, and
    records whether it was notified. ``owner`` differentiates co-named agents.
    """

    def __init__(self, key, x, y, planet="earth", owner=None, agent_id=None,
                 puppeted=False):
        self.key = key
        self.location = None
        self.db = FakeDB(
            coord_x=x, coord_y=y, coord_planet=planet,
            combat_xp=0, owner=owner, agent_id=agent_id,
        )
        self._messages = []
        self._executed = []
        # A puppeted player has execute_cmd (so it gets a look-refresh); an
        # agent/NPC does not. Add it conditionally to mirror the guard in
        # _pull_to_caller.
        if puppeted:
            self.execute_cmd = lambda cmd, **kw: self._executed.append(cmd)

    def msg(self, text, **kwargs):
        self._messages.append(text)

    def move_to(self, destination, **kwargs):
        self.location = destination


class _BuildingStub:
    """A fixed structure — no combat_xp, so is_player() is False (not movable)."""

    def __init__(self, key, x, y, planet="earth"):
        self.key = key
        self.location = None
        self.db = FakeDB(coord_x=x, coord_y=y, coord_planet=planet)

    def move_to(self, destination, **kwargs):
        self.location = destination


class _FakeAgentRoster:
    """Minimal agent_system exposing get_agents(owner)."""

    def __init__(self, by_owner):
        self._by_owner = by_owner  # {owner_obj: [units]}

    def get_agents(self, owner):
        return self._by_owner.get(owner, [])


class TestTransfer(unittest.TestCase):
    """CmdTransfer pulls players/agents/NPCs to the caller's tile."""

    def _caller(self, systems=None):
        caller = FakeCaller(perm_level="Builder", systems=systems or {})
        caller.db.coord_planet = "earth"
        caller.db.coord_x, caller.db.coord_y = 100, 100
        return caller

    def _run(self, caller, args, room, search_results=None):
        caller._search_results = search_results or {}
        from server.conf import game_init
        original = getattr(game_init, "game_systems", None)
        game_init.game_systems = {"planet_rooms": {"earth": room}}
        try:
            cmd = _make_cmd(CmdTransfer, caller, args)
            cmd.func()
        finally:
            if original is not None:
                game_init.game_systems = original

    def test_registers_expected_aliases(self):
        self.assertIn("summon", CmdTransfer.aliases)
        self.assertIn("@transfer", CmdTransfer.aliases)

    def test_pulls_named_unit_to_caller_tile(self):
        room = _RecordingRoom()
        caller = self._caller()
        unit = _UnitStub("Scout", x=5, y=5)
        self._run(caller, "Scout", room, {"Scout": unit})

        self.assertEqual(len(room.calls), 1)
        obj, tx, ty, notify = room.calls[0]
        self.assertIs(obj, unit)
        self.assertEqual((tx, ty), (100, 100))  # the caller's tile
        self.assertFalse(notify)  # relocation is silent
        # The unit is told it moved.
        self.assertTrue(any("transferred" in str(m).lower() for m in unit._messages))
        self.assertTrue(any("Scout" in str(m) for m in caller._messages))

    def test_transfer_refreshes_views_for_puppeted_target_and_caller(self):
        # A puppeted player target gets a 'look' refresh (stale-map fix), and the
        # caller's view refreshes too so the arriving unit shows on the tile.
        room = _RecordingRoom()
        caller = self._caller()
        unit = _UnitStub("Scout", x=5, y=5, puppeted=True)
        self._run(caller, "Scout", room, {"Scout": unit})

        self.assertEqual(len(room.calls), 1)  # the move happened
        self.assertIn("look", unit._executed,
                      "a puppeted transferred player must get a look-refresh")
        self.assertIn("look", caller._executed,
                      "the caller's view must refresh after pulling a unit in")

    def test_transfer_agent_target_without_execute_cmd_is_safe(self):
        # An agent/NPC target has no execute_cmd; the look-refresh branch is
        # guarded, so the transfer still succeeds without raising.
        room = _RecordingRoom()
        caller = self._caller()
        agent = _UnitStub("Agent-1", x=5, y=5, puppeted=False)  # no execute_cmd
        self.assertFalse(hasattr(agent, "execute_cmd"))
        self._run(caller, "Agent-1", room, {"Agent-1": agent})
        self.assertEqual(len(room.calls), 1)  # moved, no crash
        self.assertIn("look", caller._executed)  # caller still refreshes

    def test_buildings_cannot_be_transferred(self):
        room = _RecordingRoom()
        caller = self._caller()
        bld = _BuildingStub("HQ", x=5, y=5)
        self._run(caller, "HQ", room, {"HQ": bld})

        self.assertEqual(len(room.calls), 0)  # no move happened
        self.assertTrue(
            any("not a movable unit" in str(m) for m in caller._messages)
        )

    def test_unknown_name_reports_and_does_not_move(self):
        room = _RecordingRoom()
        caller = self._caller()
        self._run(caller, "Nobody", room, {})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(any("No unit named" in str(m) for m in caller._messages))

    def test_ambiguous_name_lists_candidates_with_owners(self):
        room = _RecordingRoom()
        caller = self._caller()
        raider = _UnitStub("Raider", x=1, y=1)
        me = _UnitStub("Me", x=2, y=2)
        a1 = _UnitStub("Agent-1", x=8, y=8, owner=raider)
        a2 = _UnitStub("Agent-1", x=9, y=9, owner=me)
        self._run(caller, "Agent-1", room, {"Agent-1": [a1, a2]})

        # Ambiguous → NOT moved; both owners listed for disambiguation.
        self.assertEqual(len(room.calls), 0)
        joined = " ".join(str(m) for m in caller._messages)
        self.assertIn("Multiple units match", joined)
        self.assertIn("Raider", joined)
        self.assertIn("Me", joined)

    def test_owner_disambiguates_by_agent_id(self):
        room = _RecordingRoom()
        raider = _UnitStub("Raider", x=1, y=1)
        a3 = _UnitStub("Agent-3", x=8, y=8, owner=raider, agent_id=3)
        roster = _FakeAgentRoster({raider: [a3]})
        caller = self._caller(systems={"agent_system": roster})
        # owner= resolves the owner via caller.search; '#3' picks by agent_id.
        self._run(caller, "#3 owner=Raider", room, {"Raider": raider})

        self.assertEqual(len(room.calls), 1)
        obj, tx, ty, _notify = room.calls[0]
        self.assertIs(obj, a3)
        self.assertEqual((tx, ty), (100, 100))

    def test_owner_disambiguates_by_name(self):
        # 'Agent-1 owner=Raider' searches by name, then keeps only Raider's.
        room = _RecordingRoom()
        raider = _UnitStub("Raider", x=1, y=1)
        me = _UnitStub("Me", x=2, y=2)
        mine = _UnitStub("Agent-1", x=3, y=3, owner=me)
        theirs = _UnitStub("Agent-1", x=8, y=8, owner=raider)
        caller = self._caller()
        self._run(
            caller, "Agent-1 owner=Raider", room,
            {"Raider": raider, "Agent-1": [mine, theirs]},
        )
        self.assertEqual(len(room.calls), 1)
        obj, _tx, _ty, _notify = room.calls[0]
        self.assertIs(obj, theirs)  # Raider's, not mine

    def test_owner_with_missing_agent_id_reports(self):
        room = _RecordingRoom()
        raider = _UnitStub("Raider", x=1, y=1)
        roster = _FakeAgentRoster({raider: []})  # owns no agents
        caller = self._caller(systems={"agent_system": roster})
        self._run(caller, "#9 owner=Raider", room, {"Raider": raider})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(any("no agent #9" in str(m) for m in caller._messages))

    def test_owner_not_found_reports(self):
        room = _RecordingRoom()
        caller = self._caller(systems={"agent_system": _FakeAgentRoster({})})
        self._run(caller, "#1 owner=Ghost", room, {})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(
            any("Could not find owner" in str(m) for m in caller._messages)
        )

    def test_no_args_shows_usage(self):
        room = _RecordingRoom()
        caller = self._caller()
        self._run(caller, "", room, {})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(any("Usage:" in str(m) for m in caller._messages))

    def test_caller_without_position_is_rejected(self):
        room = _RecordingRoom()
        caller = self._caller()
        caller.db.coord_x = None  # no tile to pull to
        unit = _UnitStub("Scout", x=5, y=5)
        self._run(caller, "Scout", room, {"Scout": unit})
        self.assertEqual(len(room.calls), 0)
        self.assertTrue(
            any("no overworld position" in str(m) for m in caller._messages)
        )


# -------------------------------------------------------------- #
#  CmdAdminOutpost tests
# -------------------------------------------------------------- #

class FakeSpawner:
    """Fake OutpostSpawnerSystem recording spawn_base calls."""

    def __init__(self, result="ok"):
        self.calls = []
        self._result = result
        self._active_bases = {}

    def spawn_base(self, planet, tier, coords=None):
        self.calls.append((planet, tier, coords))
        if self._result is None:
            return None
        x, y = coords if coords else (7, 7)
        rec = {"tier": tier, "planet": planet, "x": x, "y": y}
        self._active_bases[len(self._active_bases)] = rec
        return rec


class TestCmdAdminOutpost(unittest.TestCase):

    def _caller(self, spawner, x=3, y=4, planet="earth", perm="Builder"):
        caller = FakeCaller(perm_level=perm,
                            systems={"outpost_spawner": spawner})
        caller.db.coord_x = x
        caller.db.coord_y = y
        caller.db.coord_planet = planet
        return caller

    def test_spawn_uses_caller_tile_by_default(self):
        spawner = FakeSpawner()
        caller = self._caller(spawner, x=3, y=4)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn outpost")
        cmd.func()
        self.assertEqual(spawner.calls, [("earth", "outpost", (3, 4))])
        self.assertTrue(any("Spawned outpost" in m for m in caller._messages))

    def test_spawn_with_explicit_coords(self):
        spawner = FakeSpawner()
        caller = self._caller(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn fortress 20 30")
        cmd.func()
        self.assertEqual(spawner.calls, [("earth", "fortress", (20, 30))])

    def test_spawn_no_tier_shows_usage(self):
        spawner = FakeSpawner()
        caller = self._caller(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn")
        cmd.func()
        self.assertTrue(any("Usage" in m for m in caller._messages))
        self.assertEqual(spawner.calls, [])

    def test_spawn_failure_reports(self):
        spawner = FakeSpawner(result=None)  # placement fails
        caller = self._caller(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn outpost")
        cmd.func()
        self.assertTrue(any("Could not spawn" in m for m in caller._messages))

    def test_spawn_denied_without_builder(self):
        spawner = FakeSpawner()
        caller = self._caller(spawner, perm="Player")
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn outpost")
        cmd.func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))
        self.assertEqual(spawner.calls, [])

    def test_list_shows_active_bases(self):
        spawner = FakeSpawner()
        spawner._active_bases = {0: {"tier": "outpost", "planet": "earth",
                                     "x": 5, "y": 6}}
        caller = self._caller(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("outpost", output)
        self.assertIn("5", output)

    # -- tier index / prefix resolution (uses a registry with base_templates) --

    class _FakeTemplate:
        def __init__(self, tier, display_name):
            self.tier = tier
            self.display_name = display_name

    class _FakeTierRegistry:
        def __init__(self, tiers):
            self.base_templates = {
                t: TestCmdAdminOutpost._FakeTemplate(t, t.title()) for t in tiers
            }

    def _caller_with_tiers(self, spawner, tiers=("fortress", "outpost")):
        caller = self._caller(spawner)
        caller.ndb.systems["registry"] = self._FakeTierRegistry(tiers)
        return caller

    def test_spawn_by_tier_index(self):
        spawner = FakeSpawner()
        caller = self._caller_with_tiers(spawner)  # sorted: fortress(1), outpost(2)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn 2")
        cmd.func()
        self.assertEqual(spawner.calls, [("earth", "outpost", (3, 4))])

    def test_spawn_by_tier_prefix(self):
        spawner = FakeSpawner()
        caller = self._caller_with_tiers(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn fort")
        cmd.func()
        self.assertEqual(spawner.calls, [("earth", "fortress", (3, 4))])

    def test_spawn_unknown_tier_reports(self):
        spawner = FakeSpawner()
        caller = self._caller_with_tiers(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "spawn bogus")
        cmd.func()
        self.assertTrue(any("Unknown or ambiguous tier" in m for m in caller._messages))
        self.assertEqual(spawner.calls, [])

    def test_tiers_lists_with_index(self):
        spawner = FakeSpawner()
        caller = self._caller_with_tiers(spawner)
        cmd = _make_cmd(CmdAdminOutpost, caller, "tiers")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("[1]", output)
        self.assertIn("fortress", output)
        self.assertIn("outpost", output)


# -------------------------------------------------------------- #
#  CmdAdminAlliance tests
# -------------------------------------------------------------- #

class _FakeAllianceSystemForAdmin:
    """Records the single-writer calls the admin router should route through."""

    def __init__(self):
        self.calls = []
        self._records = {
            7: {"id": 7, "name": "Wolves", "tag": "WLV", "leader_id": 100,
                "officer_ids": [], "member_ids": [200], "treasury": {"Iron": 5},
                "active_perks": {}, "pending_invites": [], "pending_requests": []},
        }
        # Resolvable roster members for the kick tests: id 100 = leader, 200 = a
        # plain member.
        self._members = {
            100: types.SimpleNamespace(id=100, key="Boss",
                                       db=types.SimpleNamespace(player_alliance=7)),
            200: types.SimpleNamespace(id=200, key="Grunt",
                                       db=types.SimpleNamespace(player_alliance=7)),
        }

        class _Reg:
            def __init__(self, recs):
                self._recs = recs

            def all_alliances(self):
                return list(self._recs.values())

            def by_tag(self, tag):
                for r in self._recs.values():
                    if r["tag"].lower() == tag.lower():
                        return r
                return None

            def put(self, rec):
                self._recs[rec["id"]] = rec

        self._alliances = _Reg(self._records)

    def _record(self, aid):
        return self._records.get(aid)

    def _resolve_member(self, cid):
        return self._members.get(cid)

    def _remove_from_roster(self, record, cid):
        record["officer_ids"] = [i for i in record.get("officer_ids", []) if i != cid]
        record["member_ids"] = [i for i in record.get("member_ids", []) if i != cid]
        self.calls.append(("_remove_from_roster", cid))

    def _clear_pointer(self, member):
        self.calls.append(("_clear_pointer", member.id))

    def _unsubscribe(self, member, aid):
        pass

    def _live_members(self, aid):
        return []

    def compute_alliance_level(self, aid):
        return 1

    def alliance_summary(self, aid, for_member=False):
        r = self._records.get(aid)
        if r is None:
            return None
        return {
            "name": r["name"], "tag": r["tag"], "leader": "Leader",
            "member_count": 1, "level": 1, "active_perks": {}, "open_join": False,
            "treasury": dict(r["treasury"]), "pending_invites": [],
            "pending_requests": [],
        }

    def _do_disband(self, record):
        self.calls.append(("_do_disband", record["id"]))
        self._records.pop(record["id"], None)


def _alliance_caller(perm="Builder"):
    system = _FakeAllianceSystemForAdmin()
    caller = FakeCaller(perm_level=perm, systems={"alliance_system": system})
    return caller, system


class TestAdminAlliance(unittest.TestCase):
    def test_list_reads_alliances(self):
        caller, system = _alliance_caller()
        _make_cmd(CmdAdminAlliance, caller, " list").func()
        out = "\n".join(caller._messages)
        self.assertIn("Wolves", out)
        self.assertIn("WLV", out)

    def test_inspect_shows_full_state(self):
        caller, system = _alliance_caller()
        _make_cmd(CmdAdminAlliance, caller, " inspect WLV").func()
        out = "\n".join(caller._messages)
        self.assertIn("Treasury", out)

    def test_force_disband_routes_through_system(self):
        caller, system = _alliance_caller()
        _make_cmd(CmdAdminAlliance, caller, " disband WLV").func()
        self.assertIn(("_do_disband", 7), system.calls)

    def test_unknown_tag_reports(self):
        caller, system = _alliance_caller()
        _make_cmd(CmdAdminAlliance, caller, " inspect NOPE").func()
        self.assertTrue(any("No alliance" in m for m in caller._messages))

    def test_requires_builder(self):
        caller, system = _alliance_caller(perm="Player")
        _make_cmd(CmdAdminAlliance, caller, " disband WLV").func()
        # A non-Builder is refused; the disband never routes through.
        self.assertNotIn(("_do_disband", 7), system.calls)

    # Fix #4 — @alliance kick must refuse the leader (would strand leader_id).
    def test_kick_leader_refused(self):
        caller, system = _alliance_caller()
        _make_cmd(CmdAdminAlliance, caller, " kick WLV Boss").func()
        self.assertTrue(any("Cannot kick the leader" in m for m in caller._messages))
        # No roster mutation happened.
        self.assertNotIn(("_remove_from_roster", 100), system.calls)

    def test_kick_non_leader_member_works(self):
        caller, system = _alliance_caller()
        _make_cmd(CmdAdminAlliance, caller, " kick WLV Grunt").func()
        self.assertIn(("_remove_from_roster", 200), system.calls)
        self.assertIn(("_clear_pointer", 200), system.calls)


if __name__ == "__main__":
    unittest.main()
