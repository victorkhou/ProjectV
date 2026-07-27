"""
Unit tests for the LEGACY (not-yet-migrated) admin command routers.

Tests subcommand delegation, permission enforcement, and admin logging
for CmdAdminBuilding, CmdAdminResource, CmdAdminPlayer,
CmdAdminOutpost, CmdAdminStat, CmdPeace, CmdRestore,
CmdObliterate, CmdTeleport, and CmdTransfer.

These routers keep their pre-unified-admin-crud behavior until their own
rollout phases (spec tasks 5.x/7.x) migrate them onto ``EntityAdminRouter``;
their tests are preserved here verbatim. The migrated ``@item`` router's
tests live in ``test_admin_routers.py`` (unified-admin-crud task 3.2).
``@building`` migrated in task 5.1: its legacy tests below still exercise
the preserved behaviors (tile destroy, open toggle, spawn kwargs, perms,
logging); only the ``list``-means-definitions assertions moved to the
``def list`` spelling per the design's Requirement 11.4 meaning change.
New @building grammar coverage lives in ``test_admin_routers.py``.
``@outpost`` migrated in task 7.1: its legacy tests below keep passing —
``spawn <tier> [x y]`` and ``list`` (instance meaning) are preserved, and
``tiers`` survives as a Migration_Alias of ``def list`` (same [N]-indexed
tier rendering, plus the one-line deprecation note per Requirement 11.2).
New @outpost grammar coverage lives in ``test_admin_routers.py``.
``@player`` migrated in task 7.3: the legacy ``level``/``rank`` verb forms
survive as Migration_Aliases of ``set <target> level|rank <N>`` — state
changes, permission outcomes (Admin+), and audit logging are preserved;
only the success wording is now the canonical shared ``set`` output per
Requirement 11.1 (see TestPlayerLevel). New @player grammar coverage lives
in ``test_admin_routers.py``.
``@stat`` migrated in task 7.4: the legacy VALUE-first ``hp``/``maxhp``/
``xp`` verbs survive as Migration_Aliases of ``set <target> <field> <N>``
(with the field-name remap maxhp→hp_max, xp→combat_xp) — the clamp,
revive, top-up, and XP-recompute side effects and the Admin+ gate are
preserved; the old FIELD-first ``set`` grammar is replaced by the
canonical TARGET-first order (see TestAdminStat). ``@alliance`` migrated
in task 7.2 and now lives in ``test_alliance_commands.py`` — its legacy
duplicate router and tests were removed.

Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.7, 2.8, 3.1, 3.2,
              3.5, 3.6, 4.1, 4.2, 4.5, 8.1
"""

import sys
import types
import unittest
import logging
from unittest import mock

import pytest

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
    CmdAdminResource,
    CmdAdminPlayer,
    CmdAdminOutpost,
    CmdAdminStat,
    CmdPeace,
    CmdRestore,
    CmdObliterate,
    CmdTeleport,
    CmdTransfer,
)

from world import services  # noqa: E402
from world.admin.adapter_registry import get_registry  # noqa: E402
from world.admin.adapters.building_adapter import BuildingAdapter  # noqa: E402
from world.admin.adapters.outpost_adapter import OutpostAdapter  # noqa: E402
from world.admin.adapters.player_adapter import PlayerAdapter  # noqa: E402
from world.admin.adapters.resource_adapter import ResourceAdapter  # noqa: E402
from world.admin.adapters.stat_adapter import StatAdapter  # noqa: E402

# CmdAdminBuilding migrated onto EntityAdminRouter (unified-admin-crud task
# 5.1), CmdAdminOutpost in task 7.1, CmdAdminPlayer in task 7.3, CmdAdminStat
# in task 7.4, CmdAdminResource in task 7.5: the routers resolve their adapters
# through the process-wide AdapterRegistry, which only ``register_all()``
# (server startup) populates. Register the adapters here so the preserved
# legacy tests keep exercising the real commands; idempotent per entity_key.
if get_registry().get("building") is None:
    get_registry().register(BuildingAdapter())
if get_registry().get("outpost") is None:
    get_registry().register(OutpostAdapter())
if get_registry().get("player") is None:
    get_registry().register(PlayerAdapter())
if get_registry().get("stat") is None:
    get_registry().register(StatAdapter())
if get_registry().get("resource") is None:
    get_registry().register(ResourceAdapter())


# -------------------------------------------------------------- #
#  Per-test system injection via the services facade
# -------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _services_sandbox():
    """Give every test a private, empty facade state, restored on exit.

    Tests inject fake systems through ``services.override`` (via
    ``_install_systems``); all system lookup reads the facade.
    """
    with services.override({}):
        yield


def _install_systems(systems):
    """Register fake *systems* for the current test through the facade."""
    services.get_systems().update(systems)


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
        self.ndb = FakeNDB()
        if systems:
            _install_systems(systems)
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
    """Definition (type) listing under the unified grammar.

    UPDATED for unified-admin-crud task 5.1 (Requirements 11.4, 11.6): the
    design's per-entity matrix moves ``@building list``'s old def-meaning
    to ``def list`` — ``list`` now shows live instances plus a moved-to
    pointer, and definition types are served by ``def list``. The
    spawn-by-def-list-index affordance (``@building spawn 2`` referencing
    the old numbered type list) went away with that numbering; ``spawn``
    resolves abbreviation/name/prefix through ``resolve_building``.
    """

    def _caller(self):
        reg = FakeBuildingRegistry([_HQ_DEF, _EX_DEF])
        return FakeCaller(perm_level="Builder", systems={"registry": reg})

    def test_list_shows_types_and_indexes(self):
        # Old def-meaning of `list` moved to `def list` (Req 11.4).
        caller = self._caller()
        cmd = _make_cmd(CmdAdminBuilding, caller, " def list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("Headquarters", output)
        self.assertIn("Extractor", output)
        # `list` itself now points at the moved definition listing.
        caller._messages.clear()
        cmd = _make_cmd(CmdAdminBuilding, caller, " list")
        cmd.func()
        output = "\n".join(caller._messages)
        self.assertIn("def list", output)

    def test_spawn_by_index_resolves_type(self):
        # Name/prefix resolution replaces the old def-list index.
        caller = self._caller()
        caller.location = FakeLocation()
        caller.db.coord_x = 1
        caller.db.coord_y = 1
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn Headquarters")
        cmd.func()
        # No real create_object under stubs, but resolution must not report
        # the type as unknown (name -> Headquarters resolved).
        self.assertFalse(any("No definition found" in m for m in caller._messages))

    def test_spawn_unknown_index_reports(self):
        caller = self._caller()
        caller.location = FakeLocation()
        caller.db.coord_x = 1
        caller.db.coord_y = 1
        cmd = _make_cmd(CmdAdminBuilding, caller, " spawn 99")
        cmd.func()
        self.assertTrue(any("No definition found" in m for m in caller._messages))


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
    """Req 4.1: @player level delegates to level-setting logic.

    UPDATED for unified-admin-crud task 7.3 (Requirements 11.1, 11.5):
    ``level`` is now a Migration_Alias of ``set <target> level <N>``, so
    its success message is the canonical shared-handler output
    ("Bob: level set to 5.") rather than the legacy wording — alias
    output must be identical to the canonical verb's (Req 11.1). The
    state change (db.level written through the existing progression
    path) is unchanged.
    """

    def test_level_sets_on_target(self):
        target = FakeTarget(name="Bob")
        caller = FakeCaller(perm_level="Admin")
        caller._search_results["Bob"] = target

        cmd = _make_cmd(CmdAdminPlayer, caller, " level 5 Bob")
        cmd.func()

        self.assertEqual(target.db.level, 5)
        # Canonical `set` output (Req 11.1) + the deprecation note (11.2).
        self.assertTrue(any("level set to 5" in m for m in caller._messages))
        self.assertTrue(any("deprecated" in m for m in caller._messages))

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

        # Install the planet_rooms CmdTeleport resolves via the facade.
        _install_systems({"planet_rooms": {"earth": room}})
        cmd = _make_cmd(CmdTeleport, caller, " 25 25 earth")
        cmd.func()

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

        _install_systems({"planet_rooms": {"earth": room}})
        cmd = _make_cmd(CmdTeleport, caller, " 50 50 2")
        cmd.cmdstring = "goto"  # invoked via the alias
        cmd.func()

        self.assertEqual(len(room.calls), 1)
        _obj, tx, ty, _notify = room.calls[0]
        self.assertEqual((tx, ty), (50, 50))

    def _entity_goto(self, caller, arg, room, search_results):
        """Run 'goto <arg>' with the given search results, installing planet_rooms."""
        caller._search_results = search_results
        _install_systems({"planet_rooms": {"earth": room}})
        cmd = _make_cmd(CmdTeleport, caller, arg)
        cmd.cmdstring = "goto"
        cmd.func()

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
        _install_systems({"planet_rooms": {"earth": room}})
        cmd = _make_cmd(CmdTransfer, caller, args)
        cmd.func()

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
        _install_systems({"registry": self._FakeTierRegistry(tiers)})
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
#  CmdAdminAlliance tests — MIGRATED (unified-admin-crud task 7.2)
#
#  ``@alliance`` moved onto ``EntityAdminRouter`` and now lives in
#  ``commands.alliance_commands.CmdAdminAlliance`` (the legacy
#  ``AdminSubcommandRouter`` duplicate that shadowed it in
#  ``admin_commands`` has been removed). Every behavior the old
#  ``TestAdminAlliance`` covered — list, inspect (now a show alias),
#  force-disband (now a destroy alias), unknown-tag, Builder gating,
#  and the leader-kick refusal — is covered (and extended with set /
#  spawn-opt-out / def-scope / transfer / rename) by the migrated
#  router's suite in ``test_alliance_commands.py``. No @alliance tests
#  remain here.
# -------------------------------------------------------------- #


# -------------------------------------------------------------- #
#  CmdPeace tests — clear a player's combat state
# -------------------------------------------------------------- #

class TestPeace(unittest.TestCase):
    """@peace zeroes the combat timer + build-gate lockout."""

    def test_peace_clears_self(self):
        caller = FakeCaller(perm_level="Builder")
        caller.db.combat_timer_expires = 120
        caller.db.combat_lockout_tick = 55
        _make_cmd(CmdPeace, caller, "").func()
        self.assertEqual(caller.db.combat_timer_expires, 0)
        self.assertEqual(caller.db.combat_lockout_tick, 0)
        self.assertTrue(any("Cleared combat state" in m for m in caller._messages))

    def test_peace_clears_named_target(self):
        target = FakeTarget(name="Bob")
        target.db.combat_timer_expires = 99
        caller = FakeCaller(perm_level="Builder")
        caller._search_results["Bob"] = target
        _make_cmd(CmdPeace, caller, "Bob").func()
        self.assertEqual(target.db.combat_timer_expires, 0)
        # The target is notified.
        self.assertTrue(any("out of combat" in m for m in target._messages))

    def test_peace_unknown_target(self):
        caller = FakeCaller(perm_level="Builder")
        _make_cmd(CmdPeace, caller, "Ghost").func()
        self.assertTrue(any("Ghost" in m for m in caller._messages))

    def test_peace_requires_builder(self):
        # The lock string gates the command; verify it's Builder-scoped.
        self.assertIn("Builder", CmdPeace.locks)


# -------------------------------------------------------------- #
#  CmdRestore tests — heal / revive
# -------------------------------------------------------------- #

class TestRestore(unittest.TestCase):
    """@restore heals to hp_max and revives a downed unit."""

    def test_restore_self_to_full(self):
        caller = FakeCaller(perm_level="Builder")
        caller.db.hp = 3
        caller.db.hp_max = 500
        _make_cmd(CmdRestore, caller, "").func()
        self.assertEqual(caller.db.hp, 500)
        self.assertTrue(any("full health" in m for m in caller._messages))

    def test_restore_revives_downed_target(self):
        target = FakeTarget(name="Bob")
        target.db.hp = 0
        target.db.hp_max = 100
        target.db.incapacitated = True
        target.db.respawn_timer = 8
        caller = FakeCaller(perm_level="Builder")
        caller._search_results["Bob"] = target
        _make_cmd(CmdRestore, caller, "Bob").func()
        self.assertEqual(target.db.hp, 100)
        self.assertFalse(target.db.incapacitated)
        self.assertEqual(target.db.respawn_timer, 0)

    def test_restore_no_max_health(self):
        caller = FakeCaller(perm_level="Builder")
        # hp_max unset -> 0 -> refuse rather than heal to 0.
        _make_cmd(CmdRestore, caller, "").func()
        self.assertTrue(any("maximum health" in m for m in caller._messages))

    def test_restore_heal_alias(self):
        self.assertIn("@heal", CmdRestore.aliases)


# -------------------------------------------------------------- #
#  CmdAdminStat tests — set hp / maxhp / xp / arbitrary fields
# -------------------------------------------------------------- #

class TestAdminStat(unittest.TestCase):
    """@stat sets combat/progression fields on a player or NPC.

    UPDATED for unified-admin-crud task 7.4 (Requirements 1.5, 11.1,
    11.5, 11.6): ``@stat`` is now an ``EntityAdminRouter`` driven by the
    stat adapter. The legacy VALUE-first verbs ``hp``/``maxhp``/``xp``
    survive as Migration_Aliases of ``set <target> <field> <N>`` (with
    the field-name remap ``maxhp``→``hp_max``, ``xp``→``combat_xp``);
    their state changes, Admin+ permission outcome, and side effects
    (clamp, revive, top-up, XP recompute) are preserved, and they now
    also emit the one-line deprecation note (Requirement 11.2). The old
    FIELD-first ``set <field> <value> [target]`` grammar is REPLACED by
    the canonical TARGET-first ``set <target> <field> <value>`` (the two
    ``set`` tests below use the new order); the unknown-field rejection
    is now the shared "Unknown field ... valid fields:" wording, and the
    ``show`` readout header is the uniform "<name> — combat stats".
    """

    def test_hp_clamped_to_max(self):
        caller = FakeCaller(perm_level="Admin")
        caller.db.hp_max = 100
        _make_cmd(CmdAdminStat, caller, " hp 9999").func()
        self.assertEqual(caller.db.hp, 100)  # clamped to hp_max
        # Alias dispatches through the canonical set path (Req 11.2 note).
        self.assertTrue(any("deprecated" in m for m in caller._messages))

    def test_hp_revives_downed(self):
        target = FakeTarget(name="Bob")
        target.db.hp_max = 100
        target.db.incapacitated = True
        target.db.respawn_timer = 5
        caller = FakeCaller(perm_level="Admin")
        caller._search_results["Bob"] = target
        _make_cmd(CmdAdminStat, caller, " hp 50 Bob").func()
        self.assertEqual(target.db.hp, 50)
        self.assertFalse(target.db.incapacitated)

    def test_maxhp_tops_up_full_unit(self):
        caller = FakeCaller(perm_level="Admin")
        caller.db.hp = 100
        caller.db.hp_max = 100
        _make_cmd(CmdAdminStat, caller, " maxhp 1000").func()
        self.assertEqual(caller.db.hp_max, 1000)
        self.assertEqual(caller.db.hp, 1000)  # full unit topped up

    def test_maxhp_clamps_overmax(self):
        caller = FakeCaller(perm_level="Admin")
        caller.db.hp = 900
        caller.db.hp_max = 1000
        _make_cmd(CmdAdminStat, caller, " maxhp 500").func()
        self.assertEqual(caller.db.hp_max, 500)
        self.assertEqual(caller.db.hp, 500)  # clamped down

    def test_xp_sets_value(self):
        caller = FakeCaller(perm_level="Admin")
        _make_cmd(CmdAdminStat, caller, " xp 1234").func()
        self.assertEqual(caller.db.combat_xp, 1234)

    def test_set_allowlisted_field(self):
        """Canonical TARGET-first grammar: set <target> <field> <value>."""
        caller = FakeCaller(perm_level="Admin")
        _make_cmd(CmdAdminStat, caller, " set me kills 42").func()
        self.assertEqual(caller.db.kills, 42)

    def test_set_rejects_unlisted_field(self):
        """A field outside the allowlist is refused, naming valid fields."""
        caller = FakeCaller(perm_level="Admin")
        _make_cmd(CmdAdminStat, caller, " set me coord_x 5").func()
        self.assertIsNone(caller.db.coord_x)
        self.assertTrue(
            any("Unknown field" in m for m in caller._messages)
        )

    def test_show_lists_stats(self):
        caller = FakeCaller(perm_level="Builder")
        caller.db.hp = 50
        caller.db.hp_max = 500
        _make_cmd(CmdAdminStat, caller, " show").func()
        self.assertTrue(
            any("combat stats" in m for m in caller._messages)
        )

    def test_hp_denied_for_builder(self):
        caller = FakeCaller(perm_level="Builder")
        _make_cmd(CmdAdminStat, caller, " hp 50").func()
        self.assertTrue(any("Permission denied" in m for m in caller._messages))

    def test_show_allowed_for_builder(self):
        caller = FakeCaller(perm_level="Builder")
        _make_cmd(CmdAdminStat, caller, " show").func()
        self.assertFalse(any("Permission denied" in m for m in caller._messages))

    def test_hp_no_args_shows_usage(self):
        caller = FakeCaller(perm_level="Admin")
        _make_cmd(CmdAdminStat, caller, " hp").func()
        self.assertTrue(any("Usage" in m for m in caller._messages))


# -------------------------------------------------------------- #
#  CmdObliterate tests — radius mass-delete
# -------------------------------------------------------------- #

from evennia.objects.objects import DefaultCharacter as _StubDefaultCharacter  # noqa: E402


class _Deletable:
    """A destroyable entity (building/NPC/item) at a tile."""

    _next_pk = 1

    def __init__(self, key="thing", x=0, y=0):
        self.key = key
        self.pk = _Deletable._next_pk
        _Deletable._next_pk += 1
        self.db = FakeDB(coord_x=x, coord_y=y)
        self.deleted = False

    def delete(self):
        self.deleted = True
        self.pk = None


class _ObliteratePlayer(_StubDefaultCharacter):
    """A real (stub) player character — must be spared by obliterate."""

    def __init__(self, key="Hero"):
        super().__init__(key=key)
        self.pk = 999
        self.deleted = False

    def delete(self):
        self.deleted = True


class _ObliterateSentinel(_StubDefaultCharacter):
    """A Sentinel HQ — a DefaultCharacter, but is_sentinel → destroyable."""

    def __init__(self, key="Sentinel"):
        super().__init__(key=key)
        self.pk = 500
        self.db.is_sentinel = True
        self.deleted = False

    def delete(self):
        self.deleted = True
        self.pk = None


class _AreaRoom:
    """PlanetRoom stand-in exposing get_objects_in_area over a fixed list."""

    def __init__(self, objects):
        self._objects = list(objects)
        self.key = "TestPlanet"

    def get_objects_in_area(self, x1, y1, x2, y2):
        out = []
        for o in self._objects:
            ox = getattr(o.db, "coord_x", None)
            oy = getattr(o.db, "coord_y", None)
            if ox is None or oy is None:
                continue
            if x1 <= ox <= x2 and y1 <= oy <= y2:
                out.append(o)
        return out


class _FakeSpawner:
    """Spawner stand-in tracking bases by HQ coords (like the real one keys them
    off a Sentinel that is NOT on the tile map). ``bases`` is a list of dicts
    ``{tier, planet, x, y}``; wipe_bases_in_area removes those whose HQ is in the
    box, mirroring the real wipe-as-a-unit behavior."""

    def __init__(self, bases=None):
        self.bases = list(bases or [])
        self.reconciled = 0

    def wipe_bases_in_area(self, planet, x1, y1, x2, y2):
        victims = [
            b for b in self.bases
            if b["planet"] == planet and x1 <= b["x"] <= x2 and y1 <= b["y"] <= y2
        ]
        for b in victims:
            self.bases.remove(b)
        return len(victims)

    def forget_dead_bases(self):
        self.reconciled += 1
        return 0


class _ObliteratePlanetRegistry:
    def resolve_planet(self, ident):
        # Accept a z-level number or the literal name "earth".
        if ident in ("0", "earth"):
            return "earth"
        if ident == "3":
            return "mars"
        return None

    def is_valid_coordinate(self, x, y, planet):
        return True


class TestObliterate(unittest.TestCase):
    """obliterate <radius> [<x> <y> [z]] mass-deletes entities, sparing players."""

    def _caller(self, x=10, y=10, planet="earth"):
        caller = FakeCaller(perm_level="Builder")
        caller.db.coord_x = x
        caller.db.coord_y = y
        caller.db.coord_planet = planet
        return caller

    def test_destroys_entities_in_radius_around_self(self):
        near = _Deletable("HQ", x=11, y=11)      # within 5 of (10,10)
        far = _Deletable("FarHQ", x=99, y=99)    # out of range
        room = _AreaRoom([near, far])
        caller = self._caller()
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}})
        _make_cmd(CmdObliterate, caller, "5").func()
        self.assertTrue(near.deleted)
        self.assertFalse(far.deleted)
        self.assertTrue(any("Obliterated 1" in m for m in caller._messages))

    def test_spares_player_characters(self):
        player = _ObliteratePlayer("Hero")
        player.db.coord_x, player.db.coord_y = 10, 10
        building = _Deletable("HQ", x=10, y=10)
        room = _AreaRoom([player, building])
        caller = self._caller()
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}})
        _make_cmd(CmdObliterate, caller, "3").func()
        self.assertFalse(player.deleted)          # player spared
        self.assertTrue(building.deleted)         # building destroyed
        self.assertTrue(any("Spared 1 player" in m for m in caller._messages))

    def test_destroys_sentinel_hq(self):
        sentinel = _ObliterateSentinel("Sentinel")
        sentinel.db.coord_x, sentinel.db.coord_y = 10, 10
        room = _AreaRoom([sentinel])
        caller = self._caller()
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}})
        _make_cmd(CmdObliterate, caller, "2").func()
        self.assertTrue(sentinel.deleted)         # sentinel HQ is destroyable

    def test_explicit_coords_and_zlevel(self):
        # obliterate 5 250 250 3 → around (250,250) on z-level 3 ("mars").
        target = _Deletable("MarsHQ", x=250, y=250)
        mars_room = _AreaRoom([target])
        caller = self._caller(planet="earth")
        caller.location = _AreaRoom([])  # caller is on earth; target on mars
        _install_systems({
            "planet_registry": _ObliteratePlanetRegistry(),
            "planet_rooms": {"earth": caller.location, "mars": mars_room},
        })
        _make_cmd(CmdObliterate, caller, "5 250 250 3").func()
        self.assertTrue(target.deleted)
        self.assertTrue(any("on mars" in m for m in caller._messages))

    def test_reconciles_dead_bases(self):
        sentinel = _ObliterateSentinel("Sentinel")
        sentinel.db.coord_x, sentinel.db.coord_y = 10, 10
        room = _AreaRoom([sentinel])
        spawner = _FakeSpawner()
        caller = self._caller()
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}, "outpost_spawner": spawner})
        _make_cmd(CmdObliterate, caller, "2").func()
        self.assertEqual(spawner.reconciled, 1)   # base bookkeeping reconciled

    def test_clears_npc_base_from_tracking(self):
        # Regression: obliterating an outpost must remove it from '@outpost list'.
        # The base's owning Sentinel is NOT on the tile map (no coords), so the
        # tile sweep alone would leave a phantom base — wipe_bases_in_area (called
        # by obliterate, keyed off the HQ coords) must clear it as a unit.
        room = _AreaRoom([_Deletable("HQ building", x=25, y=25)])
        spawner = _FakeSpawner(bases=[
            {"tier": "outpost", "planet": "earth", "x": 25, "y": 25},
            {"tier": "fortress", "planet": "earth", "x": 200, "y": 200},  # far away
        ])
        caller = self._caller(x=25, y=25)
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}, "outpost_spawner": spawner})
        _make_cmd(CmdObliterate, caller, "5").func()
        # The in-range outpost is gone from tracking; the distant fortress stays.
        remaining = [(b["tier"], b["x"], b["y"]) for b in spawner.bases]
        self.assertEqual(remaining, [("fortress", 200, 200)])
        self.assertTrue(any("Cleared 1 NPC base" in m for m in caller._messages))

    def test_radius_zero_hits_only_center_tile(self):
        center = _Deletable("Center", x=10, y=10)
        adjacent = _Deletable("Adj", x=11, y=10)
        room = _AreaRoom([center, adjacent])
        caller = self._caller()
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}})
        _make_cmd(CmdObliterate, caller, "0").func()
        self.assertTrue(center.deleted)
        self.assertFalse(adjacent.deleted)

    def test_no_args_shows_usage(self):
        caller = self._caller()
        _make_cmd(CmdObliterate, caller, "").func()
        self.assertTrue(any("Usage" in m for m in caller._messages))

    def test_negative_radius_rejected(self):
        caller = self._caller()
        _make_cmd(CmdObliterate, caller, "-3").func()
        self.assertTrue(any("zero or positive" in m for m in caller._messages))

    def test_non_integer_radius_rejected(self):
        caller = self._caller()
        _make_cmd(CmdObliterate, caller, "big").func()
        self.assertTrue(any("integer" in m for m in caller._messages))

    def test_requires_builder(self):
        self.assertIn("Builder", CmdObliterate.locks)

    def test_skips_already_deleted_stale_refs(self):
        stale = _Deletable("Ghost", x=10, y=10)
        stale.pk = None  # already-deleted stale index entry
        live = _Deletable("HQ", x=10, y=10)
        room = _AreaRoom([stale, live])
        caller = self._caller()
        caller.location = room
        _install_systems({"planet_rooms": {"earth": room}})
        _make_cmd(CmdObliterate, caller, "1").func()
        self.assertFalse(stale.deleted)   # never touched
        self.assertTrue(live.deleted)
        self.assertTrue(any("Obliterated 1" in m for m in caller._messages))


if __name__ == "__main__":
    unittest.main()
