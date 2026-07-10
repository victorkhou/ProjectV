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
    CmdTeleport,
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
        t = token.lower()
        for d in self.items.values():
            if d.key.lower() == t or d.name.lower() == t:
                return d
        return None

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


class TestItemList(unittest.TestCase):
    """@item list enumerates definitions, optionally filtered."""

    def test_list_all(self):
        caller = _item_caller()
        cmd = _make_cmd(CmdAdminItem, caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("assault_rifle", output)
        self.assertIn("frag_grenade", output)

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


if __name__ == "__main__":
    unittest.main()
