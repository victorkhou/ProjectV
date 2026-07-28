"""
Unit tests for the EntityAdminRouter ``def set``/``def reset`` flow
(unified-admin-crud task 1.15).

Built on the toy-adapter pattern from ``test_entity_admin_mutations.py``,
with a fake OverlayStore + fake DataRegistry so the whole overlay-write →
serialized-reload → respond flow runs without touching disk:

- def set success: before→after merged values reported after the reload
  outcome is known (R6.3, 6.4, 6.7)
- unknown definition field → error naming valid fields, overlay untouched
  (R5.8); unresolved key → error, overlay untouched
- def reset with no existing override → error, overlay untouched (R5.9);
  success reports the restored base values (R5.5)
- reload failure → live registry unchanged, overlay snapshot restored,
  validator errors relayed (R6.5)
- overlay-write failure → no reload, overlay unchanged, error (R6.8)
- audit entries record the reload outcome — applied or rolled back (R9.2)
- the whole sequence runs under the serialization lock; the default lock
  is the real ``world.data_registry.OVERLAY_RELOAD_LOCK`` (R6.6)
- per-field perm escalation applies to def set (R8.4)
- ``def show`` appends the live-instances note when the adapter reports
  at least one live instance for the def key (R10.4)
"""

import copy
import unittest

from mygame.commands.command_router import EntityAdminRouter
from world.admin.adapter_registry import AdapterRegistry
from world.admin.overlay_store import OverlayStoreError
from world.admin.types import FieldSpec
from world.data_registry import OVERLAY_RELOAD_LOCK

# Shared caller double + base class. This file previously seeded its own
# caller ids at 50_000 — the same seed as test_prop_admin_set.py and
# test_outpost_adapter.py, so callers in different files collided on the
# identity that keys LIST_CACHE and the pending-destroy map.
from .router_harness import RouterCaller, RouterTestCase


class FakeOverlayStore:
    """In-memory OverlayStore double with snapshot/rollback semantics."""

    def __init__(self):
        self.data = {}          # domain -> key -> field -> value
        self._snapshot = None
        self.restore_calls = 0
        self.set_calls = []
        self.reset_calls = []
        self.fail_set = False   # raise OverlayStoreError from set()

    def get(self, domain, key):
        return dict((self.data.get(domain) or {}).get(key) or {})

    def set(self, domain, key, field, value):
        if self.fail_set:
            raise OverlayStoreError("overlay disk full.")
        self._snapshot = copy.deepcopy(self.data)
        self.data.setdefault(domain, {}).setdefault(key, {})[field] = value
        self.set_calls.append((domain, key, field, value))

    def reset(self, domain, key, field=None):
        key_map = (self.data.get(domain) or {}).get(key) or {}
        if field is None:
            if not key_map:
                raise OverlayStoreError(
                    f"No override exists for '{domain}.{key}'."
                )
        elif field not in key_map:
            raise OverlayStoreError(
                f"No override exists for '{domain}.{key}.{field}'."
            )
        self._snapshot = copy.deepcopy(self.data)
        if field is None:
            del self.data[domain][key]
        else:
            del key_map[field]
        self.reset_calls.append((domain, key, field))

    def restore_snapshot(self):
        self.restore_calls += 1
        self.data = copy.deepcopy(self._snapshot)

    def diff(self):
        return copy.deepcopy(self.data)


class SpyLock:
    """Context-manager lock spy recording enter/exit."""

    def __init__(self):
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *args):
        self.exited += 1
        return False

    @property
    def held(self):
        return self.entered > self.exited


class FakeRegistry:
    """DataRegistry double: reload merges the overlay over base defs.

    On success the adapter's definitions become base + overlay (the
    merged registry after the atomic swap); on failure nothing changes
    (the live registry keeps its pre-command state).
    """

    def __init__(self, adapter, overlay, domain, lock=None):
        self.adapter = adapter
        self.overlay = overlay
        self.domain = domain
        self.lock = lock
        self.base = copy.deepcopy(adapter.definitions)
        self.reload_calls = 0
        self.fail_errors = None     # list of errors => reload fails
        self.lock_held_at_reload = None

    def reload_all(self):
        self.reload_calls += 1
        if self.lock is not None:
            self.lock_held_at_reload = self.lock.held
        if self.fail_errors is not None:
            return False, list(self.fail_errors)
        merged = copy.deepcopy(self.base)
        for key, fields in (self.overlay.data.get(self.domain) or {}).items():
            if key in merged:
                merged[key].update(copy.deepcopy(fields))
        self.adapter.definitions = merged
        return True, []


_DEF_FIELDS = {
    "level": FieldSpec(name="level", kind="int", min_value=None,
                       max_value=None, perm="Builder"),
    "name": FieldSpec(name="name", kind="str", perm="Builder"),
    "xp_mult": FieldSpec(name="xp_mult", kind="float", perm="Developer"),
}


class DefAdapter:
    """EntityAdapter double for the definition plane."""

    entity_key = "toydef"
    def_domain = "toydefs"
    supported_verbs = frozenset({
        "list", "spawn", "show", "set", "destroy",
        "def list", "def show", "def set", "def reset", "def diff",
    })
    opt_outs = {}
    extra_verbs = {}
    aliases = {}

    def __init__(self):
        self.definitions = {
            "teddy": {"key": "teddy", "name": "Teddy Bear", "level": 3,
                      "xp_mult": 1.0},
        }
        self.live_def_keys = set()   # feeds has_live_instances

    # --- instance plane (unused by the def flow) ---
    def list_instances(self, caller, filter_str):
        return []

    def resolve_instance(self, caller, token):
        raise AssertionError("instance resolution must not run for def verbs")

    def instance_fields(self):
        return {}

    def create(self, caller, def_token, kwargs):
        raise AssertionError("create must not run for def verbs")

    def read(self, caller, instance):
        raise AssertionError("read must not run for def verbs")

    def update(self, caller, instance, field, value):
        raise AssertionError("update must not run for def verbs")

    def delete(self, caller, instance):
        raise AssertionError("delete must not run for def verbs")

    # --- definition scope ---
    def definition_fields(self):
        return dict(_DEF_FIELDS)

    def def_registry_dict(self):
        return self.definitions

    def def_resolve(self, token):
        return self.definitions.get(token.lower())

    def has_live_instances(self, def_key):
        return def_key in self.live_def_keys


class DefRouter(EntityAdminRouter):
    key = "@toydef"
    adapter_key = "toydef"

    registry = None        # per-test AdapterRegistry
    store = None           # per-test FakeOverlayStore
    data_registry = None   # per-test FakeRegistry
    lock = None            # per-test SpyLock
    audit_log = None       # per-test list of (verb, detail)
    audit_fail = False

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.store

    def _data_registry(self):
        return self.data_registry

    def _reload_lock(self):
        return self.lock

    def _log_admin(self, verb, detail):
        if self.audit_fail:
            raise RuntimeError("audit sink down")
        self.audit_log.append((verb, detail))


class DefMutationTestCase(RouterTestCase):
    """Fresh adapter/store/registry/lock per test; caller + run_cmd from
    the shared harness. Def writes are Admin-gated, so the caller holds
    Admin here rather than the harness default of Builder."""

    router_class = DefRouter

    def setUp(self):
        super().setUp()
        self.caller = RouterCaller(perms=("Builder", "Admin"))
        self.adapter = DefAdapter()
        self.adapter_registry = AdapterRegistry()
        self.adapter_registry.register(self.adapter)
        self.store = FakeOverlayStore()
        self.lock = SpyLock()
        self.data_registry = FakeRegistry(
            self.adapter, self.store, "toydefs", lock=self.lock
        )
        self.cmd_attrs = {
            "registry": self.adapter_registry,
            "store": self.store,
            "data_registry": self.data_registry,
            "lock": self.lock,
            "audit_log": self.audit_log,
            "audit_fail": False,
        }


# ------------------------------------------------------------------ #
#  def set — success (R6.3, R6.4, R6.7, R5.2)
# ------------------------------------------------------------------ #

class TestDefSetSuccess(DefMutationTestCase):

    def test_success_reports_before_and_after_merged_values(self):
        cmd = self.run_cmd(" def set teddy level 9")
        out = self.output(cmd)
        self.assertIn("teddy.level: 3 → 9", out)
        self.assertIn("Reloaded OK", out)

    def test_success_writes_overlay_and_reloads_once(self):
        self.run_cmd(" def set teddy level 9")
        self.assertEqual(self.store.set_calls,
                         [("toydefs", "teddy", "level", 9)])
        self.assertEqual(self.data_registry.reload_calls, 1)
        self.assertEqual(self.store.restore_calls, 0)

    def test_success_merged_registry_serves_the_override(self):
        self.run_cmd(" def set teddy level 9")
        self.assertEqual(self.adapter.definitions["teddy"]["level"], 9)

    def test_value_coerced_per_field_kind(self):
        self.run_cmd(" def set teddy level 9")
        _, _, _, value = self.store.set_calls[0]
        self.assertIsInstance(value, int)

    def test_kind_coercion_failure_leaves_overlay_untouched(self):
        cmd = self.run_cmd(" def set teddy level banana")
        out = self.output(cmd)
        self.assertIn("banana", out)
        self.assertIn("int", out)
        self.assertEqual(self.store.set_calls, [])
        self.assertEqual(self.data_registry.reload_calls, 0)


# ------------------------------------------------------------------ #
#  def set — validation errors (R5.8, unresolved key)
# ------------------------------------------------------------------ #

class TestDefSetValidation(DefMutationTestCase):

    def test_unknown_field_names_valid_fields_overlay_untouched(self):
        cmd = self.run_cmd(" def set teddy bogus 4")
        self.assertUnknownField(cmd, field="bogus", plane="definition",
                                valid=("level", "name", "xp_mult"))
        self.assertEqual(self.store.set_calls, [])
        self.assertEqual(self.data_registry.reload_calls, 0)

    def test_unresolved_key_errors_overlay_untouched(self):
        cmd = self.run_cmd(" def set nope level 4")
        out = self.output(cmd)
        self.assertIn("No definition found for 'nope'", out)
        self.assertIn("was not modified", out)
        self.assertEqual(self.store.set_calls, [])
        self.assertEqual(self.data_registry.reload_calls, 0)


# ------------------------------------------------------------------ #
#  def set — per-field perm escalation (R8.4)
# ------------------------------------------------------------------ #

class TestDefSetFieldPerm(DefMutationTestCase):

    def test_escalated_field_rejected_below_tier_nothing_written(self):
        # Caller holds Admin (the def-set verb tier) but not Developer.
        cmd = self.run_cmd(" def set teddy xp_mult 2")
        self.assertPermDenied(cmd, required="Developer", scope="field",
                              target="xp_mult")
        self.assertEqual(self.store.set_calls, [])
        self.assertEqual(self.data_registry.reload_calls, 0)

    def test_escalated_field_applies_at_sufficient_tier(self):
        dev = RouterCaller(perms=("Builder", "Admin", "Developer"))
        self.run_cmd(" def set teddy xp_mult 2", caller=dev)
        self.assertEqual(self.store.set_calls,
                         [("toydefs", "teddy", "xp_mult", 2.0)])


# ------------------------------------------------------------------ #
#  def set — reload failure rollback (R6.5)
# ------------------------------------------------------------------ #

class TestDefSetReloadFailure(DefMutationTestCase):

    def test_failure_restores_snapshot_and_relays_errors(self):
        self.data_registry.fail_errors = [
            "items.teddy: level must be <= 5",
        ]
        cmd = self.run_cmd(" def set teddy level 99")
        out = self.output(cmd)
        self.assertIn("Override rejected", out)
        self.assertIn("level must be <= 5", out)      # validator errors relayed
        self.assertIn("rolled back", out)
        self.assertEqual(self.store.restore_calls, 1)
        self.assertEqual(self.store.data, {})          # overlay back to empty

    def test_failure_leaves_live_registry_unchanged(self):
        self.data_registry.fail_errors = ["boom"]
        self.run_cmd(" def set teddy level 99")
        self.assertEqual(self.adapter.definitions["teddy"]["level"], 3)


# ------------------------------------------------------------------ #
#  def set — overlay-write failure (R6.8)
# ------------------------------------------------------------------ #

class TestDefSetOverlayWriteFailure(DefMutationTestCase):

    def test_write_failure_triggers_no_reload_overlay_unchanged(self):
        self.store.fail_set = True
        cmd = self.run_cmd(" def set teddy level 9")
        out = self.output(cmd)
        self.assertIn("Override write failed", out)
        self.assertIn("overlay disk full", out)
        self.assertEqual(self.data_registry.reload_calls, 0)   # no reload
        self.assertEqual(self.store.data, {})                  # unchanged
        self.assertEqual(self.store.restore_calls, 0)


# ------------------------------------------------------------------ #
#  def reset (R5.5, R5.9)
# ------------------------------------------------------------------ #

class TestDefReset(DefMutationTestCase):

    def _install_override(self, field="level", value=9):
        self.store.data = {"toydefs": {"teddy": {field: value}}}
        # The live merged registry currently serves the override.
        self.data_registry.reload_all()
        self.data_registry.reload_calls = 0

    def test_reset_field_restores_base_value_and_reports_it(self):
        self._install_override()
        cmd = self.run_cmd(" def reset teddy level")
        out = self.output(cmd)
        self.assertIn("level: 9 → 3 (base)", out)      # restored base value
        self.assertIn("Reloaded OK", out)
        self.assertEqual(self.adapter.definitions["teddy"]["level"], 3)
        self.assertEqual(self.store.reset_calls,
                         [("toydefs", "teddy", "level")])

    def test_reset_whole_key_reports_every_restored_field(self):
        self.store.data = {"toydefs": {"teddy": {"level": 9,
                                                 "name": "Ted"}}}
        self.data_registry.reload_all()
        self.data_registry.reload_calls = 0
        cmd = self.run_cmd(" def reset teddy")
        out = self.output(cmd)
        self.assertIn("level: 9 → 3 (base)", out)
        self.assertIn("name: Ted → Teddy Bear (base)", out)
        self.assertEqual(self.adapter.definitions["teddy"]["level"], 3)

    def test_reset_without_override_errors_overlay_untouched(self):
        cmd = self.run_cmd(" def reset teddy level")
        out = self.output(cmd)
        self.assertIn("No override exists", out)
        self.assertEqual(self.data_registry.reload_calls, 0)
        self.assertEqual(self.store.data, {})

    def test_reset_unresolved_key_errors_overlay_untouched(self):
        cmd = self.run_cmd(" def reset nope")
        self.assertIn("No definition found for 'nope'", self.output(cmd))
        self.assertEqual(self.data_registry.reload_calls, 0)

    def test_reset_reload_failure_restores_snapshot(self):
        self._install_override()
        self.data_registry.fail_errors = ["cross-validate exploded"]
        cmd = self.run_cmd(" def reset teddy level")
        out = self.output(cmd)
        self.assertIn("Reset rejected", out)
        self.assertIn("cross-validate exploded", out)
        self.assertEqual(self.store.restore_calls, 1)
        # Overlay is back to its pre-command state (override still there).
        self.assertEqual(self.store.data,
                         {"toydefs": {"teddy": {"level": 9}}})


# ------------------------------------------------------------------ #
#  audit — reload outcome recorded (R9.2, R9.4)
# ------------------------------------------------------------------ #

class TestDefAudit(DefMutationTestCase):

    def test_successful_def_set_audits_reload_applied(self):
        self.run_cmd(" def set teddy level 9")
        self.assertEqual(len(self.audit_log), 1)
        verb, detail = self.audit_log[0]
        self.assertEqual(verb, "def set")
        self.assertIn("requested=9", detail)
        self.assertIn("applied=9", detail)
        self.assertIn("reload applied", detail)

    def test_failed_def_set_audits_rollback(self):
        self.data_registry.fail_errors = ["nope"]
        self.run_cmd(" def set teddy level 9")
        self.assertEqual(len(self.audit_log), 1)
        verb, detail = self.audit_log[0]
        self.assertEqual(verb, "def set")
        self.assertIn("rolled back", detail)

    def test_successful_def_reset_audits_reload_applied(self):
        self.store.data = {"toydefs": {"teddy": {"level": 9}}}
        self.run_cmd(" def reset teddy level")
        verb, detail = self.audit_log[0]
        self.assertEqual(verb, "def reset")
        self.assertIn("reload applied", detail)

    def test_audit_failure_leaves_change_applied_and_notes_it(self):
        cmd = self.run_cmd(" def set teddy level 9", audit_fail=True)
        self.assertEqual(self.adapter.definitions["teddy"]["level"], 9)
        self.assertIn("audit logging failed", self.output(cmd))


# ------------------------------------------------------------------ #
#  serialization lock (R6.6)
# ------------------------------------------------------------------ #

class TestSerializationLock(DefMutationTestCase):

    def test_def_set_runs_write_and_reload_under_the_lock(self):
        self.run_cmd(" def set teddy level 9")
        self.assertEqual(self.lock.entered, 1)
        self.assertEqual(self.lock.exited, 1)
        self.assertTrue(self.data_registry.lock_held_at_reload)

    def test_def_reset_runs_under_the_lock(self):
        self.store.data = {"toydefs": {"teddy": {"level": 9}}}
        self.run_cmd(" def reset teddy level")
        self.assertEqual(self.lock.entered, 1)
        self.assertTrue(self.data_registry.lock_held_at_reload)

    def test_default_lock_is_the_real_overlay_reload_lock(self):
        cmd = EntityAdminRouter()
        self.assertIs(
            EntityAdminRouter._reload_lock(cmd), OVERLAY_RELOAD_LOCK
        )


# ------------------------------------------------------------------ #
#  def show — live-instances note (R10.4)
# ------------------------------------------------------------------ #

class TestDefShowLiveInstancesNote(DefMutationTestCase):

    def test_note_appended_when_live_instances_exist(self):
        self.adapter.live_def_keys.add("teddy")
        cmd = self.run_cmd(" def show teddy")
        self.assertIn("existing instances retain previously stamped values",
                      self.output(cmd))

    def test_no_note_without_live_instances(self):
        cmd = self.run_cmd(" def show teddy")
        self.assertNotIn("retain previously stamped", self.output(cmd))

    def test_no_note_when_adapter_lacks_the_hook(self):
        original = DefAdapter.has_live_instances
        del DefAdapter.has_live_instances
        try:
            cmd = self.run_cmd(" def show teddy")
            self.assertNotIn("retain previously stamped", self.output(cmd))
        finally:
            DefAdapter.has_live_instances = original


if __name__ == "__main__":
    unittest.main()
