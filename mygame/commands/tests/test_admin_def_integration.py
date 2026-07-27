"""
Integration tests for the definition plane end-to-end
(unified-admin-crud task 3.4).

NOTE: the task names ``commands/tests/test_admin_routers.py`` as the
location; these integration tests live in this dedicated module instead
because task 3.3 appends unit tests to that file in parallel
(spec-named location adjusted to avoid parallel-edit conflicts).

Unlike the unit tests in ``test_entity_admin_def_mutations.py`` (fake
store + fake registry), these tests drive the REAL ``CmdAdminItem``
router with the REAL ``ItemAdapter``, a REAL ``OverlayStore`` over a
temp data directory, and a REAL ``DataRegistry`` loaded from that
directory (real ``SchemaValidator``, real atomic-swap reload). The
components are injected through the router's test hooks
(``_overlay_store`` / ``_data_registry`` / ``_reload_lock``) and the
adapter's ``registry`` parameter.

Covered end-to-end flows:

- ``@item def set`` → real overlay write → real reload → instance lazy
  re-read serves the override while stamped attributes stay unmodified
  (R10.5). Live ``GameItem``s read defs lazily via the ``item_def``
  property (``typeclasses/objects.py``), which needs a booted Evennia
  DB — impractical in the stubbed suite — so the lazy re-read is
  demonstrated through a dict-shaped item and the adapter's
  ``_item_def_for`` lazy lookup (the same registry-resolver path the
  property uses).
- Invalid override end-to-end: real SchemaValidator errors relayed in
  the router output, overlay rolled back on disk, subsequent reload
  still clean (R6.4, R6.5).
- ``def reset`` restores the base YAML value end-to-end (R5.5).
- ``def diff`` shows the deviation and empties after reset (R5.6).

_Requirements: 5.5, 5.6, 6.4, 6.5, 10.5_
"""

import os
import shutil
import tempfile
import unittest

import yaml

from mygame.commands.admin_commands import CmdAdminItem
from world.admin.adapter_registry import AdapterRegistry
from world.admin.adapters.item_adapter import ItemAdapter
from world.admin.overlay_store import OverlayStore
from world.data_registry import OVERLAY_RELOAD_LOCK, DataRegistry

# The canonical minimal-valid YAML fixtures keep the temp data dir in
# lockstep with the schemas the real registry enforces (the same pattern
# as world/admin/tests/test_prop_overlay.py).
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


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f)


def _make_data_dir():
    """A fresh temp data directory holding all valid base YAML files."""
    tmpdir = tempfile.mkdtemp(prefix="def_integration_")
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


class FakeCaller:
    """Caller mock with msg() and a configurable permission set."""

    def __init__(self, perms=("Builder", "Admin")):
        self.key = "TestAdmin"
        self.perms = set(perms)
        self.messages = []

    def msg(self, text, **kwargs):
        self.messages.append(text)

    def check_permstring(self, perm):
        return perm in self.perms


class ItemDefIntegrationRouter(CmdAdminItem):
    """The real @item router with the real components injected via the
    documented test hooks — everything else (dispatch, def sub-dispatch,
    perms, the def set/reset flow) is the production code path."""

    registry = None        # per-test AdapterRegistry (real ItemAdapter)
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


class DefIntegrationTestCase(unittest.TestCase):
    """Temp data dir + real registry/store/adapter per test."""

    def setUp(self):
        self.tmpdir = _make_data_dir()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.data_registry = DataRegistry()
        self.data_registry.load_all(self.tmpdir)
        self.store = OverlayStore(self.tmpdir)
        self.adapter = ItemAdapter(registry=self.data_registry)
        self.adapter_registry = AdapterRegistry()
        self.adapter_registry.register(self.adapter)
        self.caller = FakeCaller()
        self.audit_log = []
        # combat_knife's base max_stack comes from the ItemDef default
        # (the fixture omits it) — read it, never hardcode it.
        self.base_max_stack = self.data_registry.get_item(
            "combat_knife"
        ).max_stack

    def run_cmd(self, args, caller=None):
        cmd = ItemDefIntegrationRouter()
        cmd.registry = self.adapter_registry
        cmd.store = self.store
        cmd.data_registry = self.data_registry
        cmd.audit_log = self.audit_log
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd

    def output(self, cmd):
        return "\n".join(str(m) for m in cmd.caller.messages)

    def overlay_bytes(self):
        """The overlay file's raw on-disk state; ``None`` when absent."""
        path = self.store.overlay_path
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()


# ------------------------------------------------------------------ #
#  def set → reload → instance lazy re-read (R10.5)
# ------------------------------------------------------------------ #

class TestDefSetLazyReRead(DefIntegrationTestCase):
    """_Requirements: 10.5_"""

    def _stamped_item(self):
        """A dict-shaped live item that stamped attributes at creation
        and reads its def lazily via the adapter's ``_item_def_for``
        registry lookup (the stubbed-suite stand-in for GameItem's
        ``item_def`` property)."""
        return {
            "item_key": "combat_knife",
            "name": "Combat Knife",
            # Stamped-at-creation attribute (mirrors _apply_item_def).
            "max_stack": self.base_max_stack,
        }

    def test_override_served_on_next_lazy_def_read(self):
        item = self._stamped_item()
        # Pre-command lazy read serves the base value.
        self.assertEqual(
            self.adapter._item_def_for(item).max_stack,
            self.base_max_stack,
        )

        cmd = self.run_cmd(" def set combat_knife max_stack 42")
        out = self.output(cmd)
        self.assertIn(
            f"combat_knife.max_stack: {self.base_max_stack} → 42", out
        )
        self.assertIn("Reloaded OK", out)

        # First lazy def read after the successful reload serves the
        # merged (overridden) value (R10.5).
        self.assertEqual(self.adapter._item_def_for(item).max_stack, 42)
        # The live merged registry agrees.
        self.assertEqual(
            self.data_registry.get_item("combat_knife").max_stack, 42
        )

    def test_stamped_instance_attributes_stay_unmodified(self):
        item = self._stamped_item()
        self.run_cmd(" def set combat_knife max_stack 42")
        # The stamped attribute is untouched by the def-plane write
        # (R10.5: only lazy reads pick the override up).
        self.assertEqual(item["max_stack"], self.base_max_stack)

    def test_override_persisted_to_the_overlay_file_on_disk(self):
        self.run_cmd(" def set combat_knife max_stack 42")
        raw = yaml.safe_load(self.overlay_bytes())
        self.assertEqual(raw["items"]["combat_knife"]["max_stack"], 42)
        # Base YAML untouched.
        with open(
            os.path.join(self.tmpdir, "definitions", "items.yaml")
        ) as f:
            base_raw = yaml.safe_load(f)
        for entry in base_raw["items"]:
            self.assertNotIn("max_stack", entry)


# ------------------------------------------------------------------ #
#  Invalid override end-to-end (R6.4, R6.5)
# ------------------------------------------------------------------ #

class TestInvalidOverrideEndToEnd(DefIntegrationTestCase):
    """_Requirements: 6.4, 6.5_"""

    def test_validator_errors_relayed_and_registry_unchanged(self):
        # max_stack=0 passes the FieldSpec int coercion but fails the
        # real SchemaValidator (_check_positive_int) on the merged data.
        cmd = self.run_cmd(" def set combat_knife max_stack 0")
        out = self.output(cmd)
        self.assertIn("Override rejected", out)
        self.assertIn("max_stack", out)           # validator errors relayed
        self.assertIn("rolled back", out)
        # Live registry keeps its pre-command state (R6.5).
        self.assertEqual(
            self.data_registry.get_item("combat_knife").max_stack,
            self.base_max_stack,
        )

    def test_overlay_rolled_back_on_disk_and_subsequent_reload_clean(self):
        # Populate the overlay with a valid override first so rollback is
        # exercised against a real pre-command file state.
        self.run_cmd(" def set kevlar_vest max_stack 7")
        pre_overlay = self.overlay_bytes()
        self.assertIsNotNone(pre_overlay)

        self.run_cmd(" def set combat_knife max_stack 0")

        # The overlay file equals its pre-command on-disk state (R6.5).
        self.assertEqual(self.overlay_bytes(), pre_overlay)
        # A SUBSEQUENT reload over the rolled-back overlay is clean, and
        # the surviving valid override is still live (R6.4/R6.5).
        ok, errors = self.data_registry.reload_all()
        self.assertTrue(ok, f"reload after rollback failed: {errors}")
        self.assertEqual(
            self.data_registry.get_item("kevlar_vest").max_stack, 7
        )
        self.assertEqual(
            self.data_registry.get_item("combat_knife").max_stack,
            self.base_max_stack,
        )

    def test_rollback_from_empty_overlay_leaves_reload_clean(self):
        pre_overlay = self.overlay_bytes()   # absent before any override
        self.run_cmd(" def set combat_knife max_stack 0")
        self.assertEqual(self.overlay_bytes(), pre_overlay)
        ok, errors = self.data_registry.reload_all()
        self.assertTrue(ok, f"reload after rollback failed: {errors}")


# ------------------------------------------------------------------ #
#  def reset restores base end-to-end (R5.5)
# ------------------------------------------------------------------ #

class TestDefResetEndToEnd(DefIntegrationTestCase):
    """_Requirements: 5.5_"""

    def test_reset_restores_exact_base_value(self):
        self.run_cmd(" def set combat_knife max_stack 42")
        self.assertEqual(
            self.data_registry.get_item("combat_knife").max_stack, 42
        )

        cmd = self.run_cmd(" def reset combat_knife max_stack")
        out = self.output(cmd)
        self.assertIn(
            f"max_stack: 42 → {self.base_max_stack} (base)", out
        )
        self.assertIn("Reloaded OK", out)
        # The merged registry serves exactly the base YAML value again.
        self.assertEqual(
            self.data_registry.get_item("combat_knife").max_stack,
            self.base_max_stack,
        )
        # And the lazy instance read follows suit.
        item = {"item_key": "combat_knife"}
        self.assertEqual(
            self.adapter._item_def_for(item).max_stack,
            self.base_max_stack,
        )

    def test_reset_clears_the_override_from_the_overlay_file(self):
        self.run_cmd(" def set combat_knife max_stack 42")
        self.run_cmd(" def reset combat_knife max_stack")
        overrides = (self.store.diff().get("items") or {}).get(
            "combat_knife"
        ) or {}
        self.assertNotIn("max_stack", overrides)


# ------------------------------------------------------------------ #
#  def diff shows the deviation, empties after reset (R5.6)
# ------------------------------------------------------------------ #

class TestDefDiffEndToEnd(DefIntegrationTestCase):
    """_Requirements: 5.6_"""

    def test_empty_overlay_produces_empty_diff(self):
        cmd = self.run_cmd(" def diff")
        self.assertIn(
            "No definition overrides in the 'items' domain",
            self.output(cmd),
        )

    def test_diff_shows_deviation_and_empties_after_reset(self):
        self.run_cmd(" def set combat_knife max_stack 42")
        cmd = self.run_cmd(" def diff")
        out = self.output(cmd)
        self.assertIn("combat_knife.max_stack = 42", out)

        self.run_cmd(" def reset combat_knife max_stack")
        cmd = self.run_cmd(" def diff")
        self.assertIn(
            "No definition overrides in the 'items' domain",
            self.output(cmd),
        )


if __name__ == "__main__":
    unittest.main()
