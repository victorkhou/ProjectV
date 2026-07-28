"""
Unit tests for the Phase 4 def-only admin surfaces (unified-admin-crud
task 8.3): the NEW ``@powerup``, ``@terrain`` (full def scope) and
``@planet`` (def-read-only) routers.

Two harnesses, both driving the REAL router + REAL adapter through the
production code path (dispatch, def sub-dispatch, perms, the def
set/reset flow) with only the documented test hooks injected:

- ``@powerup`` / ``@terrain``: a REAL ``OverlayStore`` over a temp data
  dir and a REAL ``DataRegistry`` loaded from it (real ``SchemaValidator``,
  real atomic-swap reload) — the same temp-dir pattern as
  ``test_admin_def_integration.py``. This exercises the full ``def set``
  → overlay write → validated reload → live-registry re-read round-trip,
  ``def reset``, ``def diff``, and the instance-verb opt-outs.
- ``@planet``: a REAL ``PlanetRegistry`` loaded from a temp
  ``planets.yaml``; the adapter reads planets straight from it. This
  covers ``def list``/``def show`` from the registry and the
  not-hot-reloadable opt-out on every write verb (``def set``/``def
  reset``/``def diff``), plus the instance-verb opt-outs.

_Requirements: 7.4, 7.5_
"""

import os
import shutil
import tempfile
import unittest

import yaml

from mygame.commands.admin_commands import (
    CmdAdminPlanet,
    CmdAdminPowerup,
    CmdAdminTerrain,
)
from world.admin.adapter_registry import AdapterRegistry
from world.admin.adapters.planet_adapter import (
    _NOT_HOT_RELOADABLE,
    PlanetAdapter,
)
from world.admin.adapters.powerup_adapter import PowerupAdapter
from world.admin.adapters.terrain_adapter import TerrainAdapter
from world.admin.overlay_store import OverlayStore
from world.coordinate.planet_registry import PlanetRegistry
from world.data_registry import OVERLAY_RELOAD_LOCK, DataRegistry

# The canonical minimal-valid YAML fixtures keep the temp data dir in
# lockstep with the schemas the real registry enforces (same pattern as
# test_admin_def_integration.py).
from mygame.world.tests.test_data_registry import (
    VALID_ABILITY_GATES,
    VALID_BALANCE,
    VALID_BUILDINGS,
    VALID_ITEMS,
    VALID_POWERUPS,
    VALID_RANKS,
    VALID_TECHNOLOGIES,
    VALID_TERRAIN,
)

from .router_harness import OutcomeAssertions, RouterCaller


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f)


def _make_data_dir():
    """A fresh temp data directory holding all valid base YAML files."""
    tmpdir = tempfile.mkdtemp(prefix="def_only_")
    defs = os.path.join(tmpdir, "definitions")
    conf = os.path.join(tmpdir, "config")
    os.makedirs(defs)
    os.makedirs(conf)
    _write_yaml(os.path.join(defs, "buildings.yaml"), VALID_BUILDINGS)
    _write_yaml(os.path.join(defs, "items.yaml"), VALID_ITEMS)
    _write_yaml(os.path.join(defs, "ranks.yaml"), VALID_RANKS)
    _write_yaml(os.path.join(defs, "technologies.yaml"), VALID_TECHNOLOGIES)
    _write_yaml(os.path.join(defs, "powerups.yaml"), VALID_POWERUPS)
    _write_yaml(os.path.join(defs, "terrain.yaml"), VALID_TERRAIN)
    _write_yaml(os.path.join(defs, "ability_gates.yaml"), VALID_ABILITY_GATES)
    _write_yaml(os.path.join(conf, "balance.yaml"), VALID_BALANCE)
    return tmpdir


class FakeCaller(RouterCaller):
    """Caller mock: Builder+Admin by default (def writes are Admin-pinned)."""

    def __init__(self, perms=("Builder", "Admin")):
        super().__init__(perms=perms)


# ================================================================== #
#  @powerup / @terrain — real overlay + registry round-trip harness
# ================================================================== #

class _OverlayRoundTripRouter:
    """Mixin installing the real overlay/registry hooks on a router.

    Subclasses set the concrete ``CmdAdmin*`` base; the per-test wiring
    (``registry``/``store``/``data_registry``/``audit_log``) is assigned
    by the test case before ``func()`` runs — everything else is the
    production code path.
    """

    registry = None        # per-test AdapterRegistry (real adapter)
    store = None           # per-test REAL OverlayStore over the temp dir
    data_registry = None   # per-test REAL DataRegistry over the temp dir
    audit_log = None       # per-test list of (verb, detail)

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.store

    def _data_registry(self):
        return self.data_registry

    def _reload_lock(self):
        return OVERLAY_RELOAD_LOCK

    def _log_admin(self, verb, detail):
        self.audit_log.append((verb, detail))


class PowerupRoundTripRouter(_OverlayRoundTripRouter, CmdAdminPowerup):
    pass


class TerrainRoundTripRouter(_OverlayRoundTripRouter, CmdAdminTerrain):
    pass


class DefOnlyRoundTripTestCase(OutcomeAssertions, unittest.TestCase):
    """Temp data dir + real registry/store/adapter per test.

    Subclasses declare ``router_cls``, ``adapter_cls`` and ``entity_key``.
    """

    router_cls = None
    adapter_cls = None
    entity_key = None

    def setUp(self):
        self.tmpdir = _make_data_dir()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.data_registry = DataRegistry()
        self.data_registry.load_all(self.tmpdir)
        self.store = OverlayStore(self.tmpdir)
        self.adapter = self.adapter_cls(registry=self.data_registry)
        self.adapter_registry = AdapterRegistry()
        self.adapter_registry.register(self.adapter)
        self.caller = FakeCaller()
        self.audit_log = []

    def run_cmd(self, args, caller=None):
        cmd = self.router_cls()
        cmd.registry = self.adapter_registry
        cmd.store = self.store
        cmd.data_registry = self.data_registry
        cmd.audit_log = self.audit_log
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd



# ------------------------------------------------------------------ #
#  @powerup
# ------------------------------------------------------------------ #

class PowerupTestCase(DefOnlyRoundTripTestCase):
    router_cls = PowerupRoundTripRouter
    adapter_cls = PowerupAdapter
    entity_key = "powerup"


class TestPowerupRouterIdentity(PowerupTestCase):
    def test_key_and_adapter_key(self):
        self.assertEqual(CmdAdminPowerup.key, "@powerup")
        self.assertEqual(CmdAdminPowerup.adapter_key, "powerup")

    def test_registered_under_powerup(self):
        self.assertIs(self.adapter_registry.get("powerup"), self.adapter)


class TestPowerupDefRead(PowerupTestCase):
    def test_def_list_lists_powerup_definitions(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("Powerup definitions", out)
        self.assertIn("adrenaline_rush", out)

    def test_def_show_renders_merged_definition(self):
        cmd = self.run_cmd(" def show adrenaline_rush")
        out = self.output(cmd)
        self.assertIn("powerup definition: adrenaline_rush", out)
        self.assertIn("effect_type: damage_bonus", out)
        self.assertIn("duration_ticks: 30", out)

    def test_def_show_resolves_by_prefix(self):
        cmd = self.run_cmd(" def show adren")
        self.assertIn("powerup definition: adrenaline_rush",
                      self.output(cmd))

    def test_def_show_unknown_key_reports_not_found(self):
        cmd = self.run_cmd(" def show nonesuch")
        self.assertIn("No definition found for 'nonesuch'",
                      self.output(cmd))


class TestPowerupDefSetRoundTrip(PowerupTestCase):
    """def set → overlay → validated reload → live re-read (R7.4)."""

    def test_def_set_applies_through_reload(self):
        cmd = self.run_cmd(" def set adrenaline_rush duration_ticks 45")
        out = self.output(cmd)
        self.assertIn("adrenaline_rush.duration_ticks: 30 → 45", out)
        self.assertIn("Reloaded OK", out)
        # Live merged registry serves the override after the reload.
        self.assertEqual(
            self.data_registry.resolve_powerup(
                "adrenaline_rush").duration_ticks,
            45,
        )

    def test_def_set_persists_to_overlay_file(self):
        self.run_cmd(" def set adrenaline_rush duration_ticks 45")
        raw = yaml.safe_load(open(self.store.overlay_path))
        self.assertEqual(
            raw["powerups"]["adrenaline_rush"]["duration_ticks"], 45
        )

    def test_def_set_records_one_audit_entry(self):
        self.run_cmd(" def set adrenaline_rush duration_ticks 45")
        self.assertEqual(len(self.audit_log), 1)
        self.assertEqual(self.audit_log[0][0], "def set")

    def test_def_set_invalid_value_rejected_and_rolled_back(self):
        # duration_ticks <= 0 passes int coercion but fails the real
        # SchemaValidator on the merged data — reload fails, overlay rolls
        # back, live registry unchanged (R6.5).
        cmd = self.run_cmd(" def set adrenaline_rush duration_ticks 0")
        out = self.output(cmd)
        self.assertIn("Override rejected", out)
        self.assertIn("duration_ticks", out)
        self.assertIn("rolled back", out)
        self.assertEqual(
            self.data_registry.resolve_powerup(
                "adrenaline_rush").duration_ticks,
            30,
        )

    def test_def_reset_restores_base(self):
        self.run_cmd(" def set adrenaline_rush duration_ticks 45")
        cmd = self.run_cmd(" def reset adrenaline_rush duration_ticks")
        out = self.output(cmd)
        self.assertIn("duration_ticks: 45 → 30 (base)", out)
        self.assertEqual(
            self.data_registry.resolve_powerup(
                "adrenaline_rush").duration_ticks,
            30,
        )

    def test_def_diff_shows_deviation_then_empties(self):
        self.run_cmd(" def set adrenaline_rush duration_ticks 45")
        cmd = self.run_cmd(" def diff")
        self.assertIn("adrenaline_rush.duration_ticks = 45",
                      self.output(cmd))
        self.run_cmd(" def reset adrenaline_rush duration_ticks")
        cmd = self.run_cmd(" def diff")
        self.assertIn("No definition overrides in the 'powerups' domain",
                      self.output(cmd))


class TestPowerupDefWritePerms(PowerupTestCase):
    def test_def_set_gated_at_admin(self):
        cmd = self.run_cmd(" def set adrenaline_rush duration_ticks 45",
                           caller=FakeCaller(perms=("Builder",)))
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def set")

    def test_def_read_passes_at_builder(self):
        cmd = self.run_cmd(" def list", caller=FakeCaller(perms=("Builder",)))
        self.assertNotIn("Permission denied", self.output(cmd))


class TestPowerupInstanceOptOuts(PowerupTestCase):
    """Every instance verb is opted out with a def-scope pointer (R7.4)."""

    def test_list_opt_out(self):
        out = self.output(self.run_cmd(" list"))
        self.assertIn("@powerup list is not available", out)
        self.assertIn("def list", out)

    def test_spawn_opt_out(self):
        out = self.output(self.run_cmd(" spawn adrenaline_rush"))
        self.assertIn("@powerup spawn is not available", out)
        self.assertIn("def set", out)

    def test_show_opt_out(self):
        out = self.output(self.run_cmd(" show adrenaline_rush"))
        self.assertIn("@powerup show is not available", out)
        self.assertIn("def show", out)

    def test_set_opt_out(self):
        out = self.output(self.run_cmd(" set adrenaline_rush x 1"))
        self.assertIn("@powerup set is not available", out)
        self.assertIn("def set", out)

    def test_destroy_opt_out(self):
        out = self.output(self.run_cmd(" destroy adrenaline_rush"))
        self.assertIn("@powerup destroy is not available", out)


# ------------------------------------------------------------------ #
#  @terrain
# ------------------------------------------------------------------ #

class TerrainTestCase(DefOnlyRoundTripTestCase):
    router_cls = TerrainRoundTripRouter
    adapter_cls = TerrainAdapter
    entity_key = "terrain"


class TestTerrainRouterIdentity(TerrainTestCase):
    def test_key_and_adapter_key(self):
        self.assertEqual(CmdAdminTerrain.key, "@terrain")
        self.assertEqual(CmdAdminTerrain.adapter_key, "terrain")

    def test_registered_under_terrain(self):
        self.assertIs(self.adapter_registry.get("terrain"), self.adapter)


class TestTerrainDefRead(TerrainTestCase):
    def test_def_list_lists_terrain_definitions(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("Terrain definitions", out)
        self.assertIn("Plains", out)
        self.assertIn("Forest", out)

    def test_def_show_renders_merged_definition(self):
        cmd = self.run_cmd(" def show Plains")
        out = self.output(cmd)
        # terrain_type is the id field → header renders it cleanly.
        self.assertIn("terrain definition: Plains", out)
        self.assertIn("map_symbol: PP", out)

    def test_def_show_resolves_case_insensitively(self):
        cmd = self.run_cmd(" def show plains")
        self.assertIn("terrain definition: Plains", self.output(cmd))


class TestTerrainDefSetRoundTrip(TerrainTestCase):
    """def set → overlay → validated reload → live re-read (R7.4)."""

    def test_def_set_applies_through_reload(self):
        cmd = self.run_cmd(" def set Plains vision_modifier 2")
        out = self.output(cmd)
        self.assertIn("Plains.vision_modifier: 0 → 2", out)
        self.assertIn("Reloaded OK", out)
        self.assertEqual(
            self.data_registry.get_terrain("Plains").vision_modifier, 2
        )

    def test_def_set_float_modifier_applies(self):
        cmd = self.run_cmd(" def set Forest defense_modifier 0.25")
        out = self.output(cmd)
        self.assertIn("Reloaded OK", out)
        self.assertEqual(
            self.data_registry.get_terrain("Forest").defense_modifier, 0.25
        )

    def test_def_set_persists_to_overlay_under_terrain_domain(self):
        self.run_cmd(" def set Plains vision_modifier 2")
        raw = yaml.safe_load(open(self.store.overlay_path))
        self.assertEqual(raw["terrain"]["Plains"]["vision_modifier"], 2)

    def test_def_set_invalid_map_symbol_rejected_and_rolled_back(self):
        # map_symbol must be exactly 2 chars — a 3-char value passes str
        # coercion but fails the real SchemaValidator; overlay rolls back.
        cmd = self.run_cmd(" def set Plains map_symbol XYZ")
        out = self.output(cmd)
        self.assertIn("Override rejected", out)
        self.assertIn("map_symbol", out)
        self.assertIn("rolled back", out)
        self.assertEqual(
            self.data_registry.get_terrain("Plains").map_symbol, "PP"
        )

    def test_def_reset_restores_base(self):
        self.run_cmd(" def set Plains vision_modifier 2")
        cmd = self.run_cmd(" def reset Plains vision_modifier")
        out = self.output(cmd)
        self.assertIn("vision_modifier: 2 → 0 (base)", out)
        self.assertEqual(
            self.data_registry.get_terrain("Plains").vision_modifier, 0
        )

    def test_def_diff_shows_deviation(self):
        self.run_cmd(" def set Plains vision_modifier 2")
        cmd = self.run_cmd(" def diff")
        self.assertIn("Plains.vision_modifier = 2", self.output(cmd))


class TestTerrainDefWritePerms(TerrainTestCase):
    def test_def_set_gated_at_admin(self):
        cmd = self.run_cmd(" def set Plains vision_modifier 2",
                           caller=FakeCaller(perms=("Builder",)))
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def set")


class TestTerrainInstanceOptOuts(TerrainTestCase):
    def test_spawn_opt_out_points_at_def(self):
        out = self.output(self.run_cmd(" spawn Plains"))
        self.assertIn("@terrain spawn is not available", out)
        self.assertIn("def set", out)

    def test_show_opt_out_points_at_def_show(self):
        out = self.output(self.run_cmd(" show Plains"))
        self.assertIn("@terrain show is not available", out)
        self.assertIn("def show", out)

    def test_destroy_opt_out(self):
        out = self.output(self.run_cmd(" destroy Plains"))
        self.assertIn("@terrain destroy is not available", out)


# ================================================================== #
#  @planet — real PlanetRegistry, def-read-only harness
# ================================================================== #

# A minimal two-planet planets.yaml the real PlanetRegistry accepts.
_PLANETS_YAML = {
    "planets": [
        {
            "planet_key": "terra",
            "planet_type": "earth",
            "z_level": 0,
            "width": 100,
            "height": 100,
            "terrain_seed": 42,
            "terrain_weights": {"Plains": 1.0},
            "default_planet": True,
            "rank_requirement": 1,
        },
        {
            "planet_key": "forge",
            "planet_type": "industrial",
            "z_level": 1,
            "width": 120,
            "height": 120,
            "terrain_seed": 7,
            "terrain_weights": {"Rock": 1.0},
            "rank_requirement": 21,
        },
    ]
}


class PlanetRouterUnderTest(CmdAdminPlanet):
    """The real @planet router with only the adapter-registry hook
    injected (def-read-only: no overlay/data-registry/reload needed)."""

    registry = None  # per-test AdapterRegistry (real PlanetAdapter)

    def _adapter_registry(self):
        return self.registry


class PlanetTestCase(OutcomeAssertions, unittest.TestCase):
    """Real PlanetRegistry loaded from a temp planets.yaml per test."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="planet_def_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        path = os.path.join(self.tmpdir, "planets.yaml")
        _write_yaml(path, _PLANETS_YAML)
        self.planet_registry = PlanetRegistry()
        self.planet_registry.load_from_yaml(path)
        self.adapter = PlanetAdapter(registry=self.planet_registry)
        self.adapter_registry = AdapterRegistry()
        self.adapter_registry.register(self.adapter)
        self.caller = FakeCaller()

    def run_cmd(self, args, caller=None):
        cmd = PlanetRouterUnderTest()
        cmd.registry = self.adapter_registry
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd



class TestPlanetRouterIdentity(PlanetTestCase):
    def test_key_and_adapter_key(self):
        self.assertEqual(CmdAdminPlanet.key, "@planet")
        self.assertEqual(CmdAdminPlanet.adapter_key, "planet")

    def test_registered_under_planet(self):
        self.assertIs(self.adapter_registry.get("planet"), self.adapter)


class TestPlanetDefRead(PlanetTestCase):
    """def list / def show served straight from PlanetRegistry (R7.5)."""

    def test_def_list_lists_planets_from_registry(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("Planet definitions", out)
        self.assertIn("terra", out)
        self.assertIn("forge", out)

    def test_def_show_renders_coordinate_space(self):
        cmd = self.run_cmd(" def show terra")
        out = self.output(cmd)
        # planet_key is the id field (added to _DEF_ID_FIELDS) → clean header.
        self.assertIn("planet definition: terra", out)
        self.assertIn("planet_type: earth", out)
        self.assertIn("width: 100", out)

    def test_def_show_resolves_by_prefix(self):
        cmd = self.run_cmd(" def show for")
        self.assertIn("planet definition: forge", self.output(cmd))

    def test_def_show_unknown_reports_not_found(self):
        cmd = self.run_cmd(" def show pluto")
        self.assertIn("No definition found for 'pluto'", self.output(cmd))


class TestPlanetNotHotReloadable(PlanetTestCase):
    """Every write verb is opted out with the not-hot-reloadable reason
    (R7.5); the reason names planets.yaml and a restart."""

    def test_def_set_opted_out_with_reason(self):
        cmd = self.run_cmd(" def set terra width 200")
        out = self.output(cmd)
        self.assertIn(_NOT_HOT_RELOADABLE, out)
        self.assertIn("planets.yaml", out)
        # Nothing changed on the registry.
        self.assertEqual(self.planet_registry.get_space("terra").width, 100)

    def test_def_reset_opted_out_with_reason(self):
        out = self.output(self.run_cmd(" def reset terra width"))
        self.assertIn(_NOT_HOT_RELOADABLE, out)

    def test_def_diff_opted_out_with_reason(self):
        out = self.output(self.run_cmd(" def diff"))
        self.assertIn(_NOT_HOT_RELOADABLE, out)

    def test_def_set_absent_from_available_def_verbs(self):
        # A bare `def` lists only the supported def verbs (list/show).
        cmd = self.run_cmd(" def")
        out = self.output(cmd)
        self.assertIn("def list", out)
        self.assertIn("def show", out)
        self.assertNotIn("def set", out)
        self.assertNotIn("def reset", out)


class TestPlanetInstanceOptOuts(PlanetTestCase):
    def test_list_opt_out(self):
        out = self.output(self.run_cmd(" list"))
        self.assertIn("@planet list is not available", out)

    def test_show_opt_out(self):
        out = self.output(self.run_cmd(" show terra"))
        self.assertIn("@planet show is not available", out)

    def test_spawn_opt_out_names_not_hot_reloadable(self):
        out = self.output(self.run_cmd(" spawn terra"))
        self.assertIn("@planet spawn is not available", out)
        self.assertIn(_NOT_HOT_RELOADABLE, out)


if __name__ == "__main__":
    unittest.main()
