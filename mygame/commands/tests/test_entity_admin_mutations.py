"""
Unit tests for the EntityAdminRouter shared mutating verbs — ``spawn``,
``set``, ``destroy`` (unified-admin-crud task 1.12).

Built on the toy-adapter pattern from ``test_entity_admin_router.py``:

- spawn: def-token resolution (unresolved → error naming the token,
  nothing created — R4.7), creation through the adapter path with the
  created identity reported (R4.2), creation-path failure → error, no
  further state change (R4.8)
- set: unknown field names the valid fields (R3.7), kind-coercion error
  states the expected kind (R3.8), enum violation lists valid values
  (R3.9), static + dynamic bounds clamp with an applied-value-and-bounds
  note (R3.2, 3.4, D2), in-bounds/unbounded apply unchanged (R3.3),
  write-path failure → error with pre-command state retained (R3.10)
- destroy: single-target deletes with a confirmation message identifying
  the instance (R4.4); multi-target shows count + identities, deletes
  nothing before explicit confirmation, and cancels with no state change
  when declined (R4.5)
- per-field perm escalation above the verb tier checked before bounds;
  insufficient tier → full rejection naming the required tier, no state
  change (R8.4, 8.5)
- audit: exactly one Audit_Log entry per successful mutation with
  requested + applied values distinguishable on clamp (R9.1, 9.3);
  audit-write failure leaves the mutation applied and notes the failure
  in the response (R9.4)
"""

import itertools
import unittest

from mygame.commands.command_router import (
    EntityAdminRouter,
    clamp_field_value,
    coerce_field_value,
)
from world.admin.adapter_registry import AdapterRegistry
from world.admin.resolution import Resolution
from world.admin.types import FieldSpec, InstanceRow, SetResult, ShowReport

_CALLER_IDS = itertools.count(10_000)


class FakeCaller:
    """Caller mock with msg() and a configurable permission set."""

    def __init__(self, perms=("Builder",)):
        self.id = next(_CALLER_IDS)  # unique pending/cache identity
        self.key = "TestAdmin"
        self.perms = set(perms)
        self.messages = []

    def msg(self, text, **kwargs):
        self.messages.append(text)

    def check_permstring(self, perm):
        return perm in self.perms


class Toy:
    """A live instance with the fields the adapter declares."""

    def __init__(self, key, name, level=3, power=5.0, cap=10.0,
                 rarity="common", xp_mult=1.0, label="plain"):
        self.key = key
        self.name = name
        self.level = level
        self.power = power
        self.cap = cap  # feeds the dynamic bounds of "power"
        self.rarity = rarity
        self.xp_mult = xp_mult
        self.label = label


_FIELDS = {
    "level": FieldSpec(name="level", kind="int", min_value=1, max_value=5,
                       perm="Builder"),
    "power": FieldSpec(name="power", kind="float", perm="Builder",
                       dynamic_bounds=lambda toy: (0.0, toy.cap)),
    "rarity": FieldSpec(name="rarity", kind="enum", perm="Builder",
                        enum_values=("common", "rare", "epic")),
    "xp_mult": FieldSpec(name="xp_mult", kind="float", perm="Admin"),
    "label": FieldSpec(name="label", kind="str", perm="Builder"),
}


class MutAdapter:
    """EntityAdapter double whose CRUD hooks mutate real state."""

    entity_key = "mut"
    def_domain = "muts"
    supported_verbs = frozenset({
        "list", "spawn", "show", "set", "destroy",
        "def list", "def show", "def set", "def reset", "def diff",
    })
    opt_outs = {}
    extra_verbs = {}
    aliases = {}

    def __init__(self):
        self.instances = {
            "toy_1": Toy("toy_1", "Teddy"),
            "toy_2": Toy("toy_2", "Ball", level=1),
        }
        self.definitions = {"teddy": {"key": "teddy", "name": "Teddy Bear"}}
        self.created = []
        self.create_kwargs = None
        self.fail_create = False
        self.fail_update = False
        self.fail_update_result = False  # return SetResult(ok=False) instead
        self.fail_delete = False
        self._spawn_ids = itertools.count(3)

    # --- resolution / listing ---
    def list_instances(self, caller, filter_str):
        return [
            InstanceRow(index=i, key=t.key, name=t.name,
                        summary=f"{t.name} (lvl {t.level})", ref=t)
            for i, t in enumerate(self.instances.values(), start=1)
        ]

    def resolve_instance(self, caller, token):
        toy = self.instances.get(token)
        if toy is None:
            return Resolution(ok=False,
                              error=f"No match found for '{token}'.")
        return Resolution(ok=True, target=toy)

    # --- field schemas ---
    def instance_fields(self):
        return dict(_FIELDS)

    def definition_fields(self):
        return {}

    # --- CRUD hooks ---
    def create(self, caller, def_token, kwargs):
        self.create_kwargs = dict(kwargs)
        if self.fail_create:
            raise RuntimeError("factory jammed")
        key = f"toy_{next(self._spawn_ids)}"
        toy = Toy(key, self.definitions[def_token]["name"])
        self.instances[key] = toy
        self.created.append(toy)
        return toy

    def read(self, caller, instance):
        return ShowReport(header=instance.name, state_lines=[], fields=[])

    def update(self, caller, instance, field, value):
        if self.fail_update:
            raise RuntimeError("disk on fire")
        if self.fail_update_result:
            return SetResult(ok=False, field=field, requested=value,
                             applied=None, clamped=False,
                             error="single-writer rejected the write")
        setattr(instance, field, value)
        return None

    def delete(self, caller, instance):
        if self.fail_delete:
            raise RuntimeError("indestructible")
        del self.instances[instance.key]

    # --- definition scope ---
    def def_registry_dict(self):
        return self.definitions

    def def_resolve(self, token):
        return self.definitions.get(token.lower())


class MutRouter(EntityAdminRouter):
    key = "@mut"
    adapter_key = "mut"

    registry = None      # per-test AdapterRegistry
    audit_log = None     # per-test list of (verb, detail)
    audit_fail = False   # raise from the audit sink

    def _adapter_registry(self):
        return self.registry

    def _log_admin(self, verb, detail):
        if self.audit_fail:
            raise RuntimeError("audit sink down")
        self.audit_log.append((verb, detail))


class MutationTestCase(unittest.TestCase):
    """Fresh adapter/registry/caller per test; shared run() helper."""

    def setUp(self):
        self.adapter = MutAdapter()
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)
        self.caller = FakeCaller(perms=("Builder",))
        self.audit_log = []

    def run_cmd(self, args, caller=None, audit_fail=False):
        cmd = MutRouter()
        cmd.registry = self.registry
        cmd.audit_log = self.audit_log
        cmd.audit_fail = audit_fail
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd

    def output(self, cmd):
        return "\n".join(cmd.caller.messages)


# ------------------------------------------------------------------ #
#  spawn (R4.2, R4.7, R4.8)
# ------------------------------------------------------------------ #

class TestSpawn(MutationTestCase):

    def test_spawn_creates_and_reports_identity(self):
        cmd = self.run_cmd(" spawn teddy")
        self.assertEqual(len(self.adapter.created), 1)
        created = self.adapter.created[0]
        out = self.output(cmd)
        self.assertIn("Spawned", out)
        self.assertIn(created.name, out)
        self.assertIn(created.key, out)

    def test_spawn_passes_kwargs_to_creation_path(self):
        self.run_cmd(" spawn teddy level=5")
        self.assertEqual(self.adapter.create_kwargs, {"level": "5"})

    def test_spawn_unresolved_def_names_token_creates_nothing(self):
        cmd = self.run_cmd(" spawn bogus")
        self.assertIn("bogus", self.output(cmd))
        self.assertIn("nothing created", self.output(cmd))
        self.assertEqual(self.adapter.created, [])
        self.assertIsNone(self.adapter.create_kwargs)  # path never entered

    def test_spawn_creation_path_failure_reports_error(self):
        self.adapter.fail_create = True
        cmd = self.run_cmd(" spawn teddy")
        self.assertIn("Spawn failed", self.output(cmd))
        self.assertIn("factory jammed", self.output(cmd))
        self.assertEqual(self.adapter.created, [])
        self.assertEqual(self.audit_log, [])  # no audit for a failure


# ------------------------------------------------------------------ #
#  set — validation errors (R3.7, R3.8, R3.9)
# ------------------------------------------------------------------ #

class TestSetValidation(MutationTestCase):

    def test_unknown_field_names_valid_fields_no_state_change(self):
        cmd = self.run_cmd(" set toy_1 bogus 4")
        out = self.output(cmd)
        self.assertIn("Unknown field 'bogus'", out)
        for name in ("level", "power", "rarity", "xp_mult", "label"):
            self.assertIn(name, out)
        self.assertEqual(self.adapter.instances["toy_1"].level, 3)

    def test_kind_error_states_expected_kind(self):
        cmd = self.run_cmd(" set toy_1 level banana")
        out = self.output(cmd)
        self.assertIn("banana", out)
        self.assertIn("int", out)
        self.assertEqual(self.adapter.instances["toy_1"].level, 3)

    def test_enum_violation_lists_valid_values(self):
        cmd = self.run_cmd(" set toy_1 rarity legendary")
        out = self.output(cmd)
        self.assertIn("common", out)
        self.assertIn("rare", out)
        self.assertIn("epic", out)
        self.assertEqual(self.adapter.instances["toy_1"].rarity, "common")

    def test_unresolved_target_relays_error(self):
        cmd = self.run_cmd(" set nope level 4")
        self.assertIn("No match found for 'nope'.", self.output(cmd))


# ------------------------------------------------------------------ #
#  set — bounds and clamping (R3.2, R3.3, R3.4, D2)
# ------------------------------------------------------------------ #

class TestSetBounds(MutationTestCase):

    def test_in_bounds_value_applies_unchanged_no_clamp_note(self):
        cmd = self.run_cmd(" set toy_1 level 4")
        self.assertEqual(self.adapter.instances["toy_1"].level, 4)
        out = self.output(cmd)
        self.assertIn("level set to 4", out)
        self.assertNotIn("clamped", out)

    def test_out_of_bounds_clamps_with_note_stating_value_and_bounds(self):
        cmd = self.run_cmd(" set toy_1 level 99")
        self.assertEqual(self.adapter.instances["toy_1"].level, 5)
        out = self.output(cmd)
        self.assertIn("clamped to 5", out)
        self.assertIn("1–5", out)

    def test_below_lower_bound_clamps_to_lower(self):
        cmd = self.run_cmd(" set toy_1 level -3")
        self.assertEqual(self.adapter.instances["toy_1"].level, 1)
        self.assertIn("clamped to 1", self.output(cmd))

    def test_dynamic_bounds_computed_from_current_entity_state(self):
        self.adapter.instances["toy_1"].cap = 7.5
        cmd = self.run_cmd(" set toy_1 power 50")
        self.assertEqual(self.adapter.instances["toy_1"].power, 7.5)
        out = self.output(cmd)
        self.assertIn("clamped to 7.5", out)
        self.assertIn("0–7.5", out)

    def test_unbounded_str_field_applies_unchanged(self):
        cmd = self.run_cmd(" set toy_1 label shiny")
        self.assertEqual(self.adapter.instances["toy_1"].label, "shiny")
        self.assertNotIn("clamped", self.output(cmd))


# ------------------------------------------------------------------ #
#  set — write-path failure (R3.5, R3.10)
# ------------------------------------------------------------------ #

class TestSetWriteFailure(MutationTestCase):

    def test_write_exception_reports_error_state_retained(self):
        self.adapter.fail_update = True
        cmd = self.run_cmd(" set toy_1 level 4")
        out = self.output(cmd)
        self.assertIn("Write failed", out)
        self.assertIn("disk on fire", out)
        self.assertEqual(self.adapter.instances["toy_1"].level, 3)
        self.assertEqual(self.audit_log, [])

    def test_write_failure_result_reports_error_state_retained(self):
        self.adapter.fail_update_result = True
        cmd = self.run_cmd(" set toy_1 level 4")
        out = self.output(cmd)
        self.assertIn("Write failed", out)
        self.assertIn("single-writer rejected the write", out)
        self.assertEqual(self.adapter.instances["toy_1"].level, 3)
        self.assertEqual(self.audit_log, [])


# ------------------------------------------------------------------ #
#  per-field perm escalation (R8.4, R8.5)
# ------------------------------------------------------------------ #

class TestFieldPermEscalation(MutationTestCase):

    def test_escalated_field_rejected_below_tier_naming_tier(self):
        cmd = self.run_cmd(" set toy_1 xp_mult 2")
        out = self.output(cmd)
        self.assertIn("Permission denied", out)
        self.assertIn("Admin", out)
        self.assertIn("xp_mult", out)
        self.assertEqual(self.adapter.instances["toy_1"].xp_mult, 1.0)
        self.assertEqual(self.audit_log, [])

    def test_escalated_field_applies_at_sufficient_tier(self):
        admin = FakeCaller(perms=("Builder", "Admin"))
        self.run_cmd(" set toy_1 xp_mult 2", caller=admin)
        self.assertEqual(self.adapter.instances["toy_1"].xp_mult, 2.0)

    def test_field_at_verb_tier_needs_no_extra_check(self):
        # Caller holds exactly Builder; a Builder-tier field passes with
        # no additional field-level gate (R8.4 at-or-below case).
        cmd = self.run_cmd(" set toy_1 level 2")
        self.assertNotIn("Permission denied", self.output(cmd))
        self.assertEqual(self.adapter.instances["toy_1"].level, 2)


# ------------------------------------------------------------------ #
#  destroy (R4.4, R4.5, R4.8)
# ------------------------------------------------------------------ #

class TestDestroy(MutationTestCase):

    def test_single_target_destroys_and_identifies_instance(self):
        cmd = self.run_cmd(" destroy toy_1")
        self.assertNotIn("toy_1", self.adapter.instances)
        out = self.output(cmd)
        self.assertIn("Destroyed", out)
        self.assertIn("Teddy", out)

    def test_multi_target_prompts_and_deletes_nothing_before_confirm(self):
        cmd = self.run_cmd(" destroy toy_1, toy_2")
        out = self.output(cmd)
        self.assertIn("2", out)
        self.assertIn("Teddy", out)
        self.assertIn("Ball", out)
        self.assertIn("destroy confirm", out)
        self.assertEqual(len(self.adapter.instances), 2)  # nothing deleted
        self.assertEqual(self.audit_log, [])

    def test_confirm_executes_pending_multi_destroy(self):
        self.run_cmd(" destroy toy_1, toy_2")
        cmd = self.run_cmd(" destroy confirm")
        self.assertEqual(self.adapter.instances, {})
        out = self.output(cmd)
        self.assertIn("Destroyed 2", out)
        self.assertIn("Teddy", out)
        self.assertIn("Ball", out)

    def test_cancel_declines_with_no_state_change(self):
        self.run_cmd(" destroy toy_1, toy_2")
        cmd = self.run_cmd(" destroy cancel")
        self.assertIn("cancelled", self.output(cmd))
        self.assertEqual(len(self.adapter.instances), 2)
        # A later confirm finds nothing pending.
        cmd2 = self.run_cmd(" destroy confirm")
        self.assertIn("No destroy is pending", self.output(cmd2))
        self.assertEqual(len(self.adapter.instances), 2)

    def test_confirm_without_pending_errors(self):
        cmd = self.run_cmd(" destroy confirm")
        self.assertIn("No destroy is pending", self.output(cmd))

    def test_unresolved_target_in_multi_deletes_nothing(self):
        cmd = self.run_cmd(" destroy toy_1, bogus")
        out = self.output(cmd)
        self.assertIn("No match found for 'bogus'.", out)
        self.assertIn("Nothing was destroyed", out)
        self.assertEqual(len(self.adapter.instances), 2)

    def test_deletion_path_failure_reports_error(self):
        self.adapter.fail_delete = True
        cmd = self.run_cmd(" destroy toy_1")
        out = self.output(cmd)
        self.assertIn("Destroy failed", out)
        self.assertIn("indestructible", out)
        self.assertIn("toy_1", self.adapter.instances)
        self.assertEqual(self.audit_log, [])


# ------------------------------------------------------------------ #
#  audit (R9.1, R9.3, R9.4)
# ------------------------------------------------------------------ #

class TestAudit(MutationTestCase):

    def test_exactly_one_entry_per_successful_mutation(self):
        self.run_cmd(" spawn teddy")
        self.run_cmd(" set toy_1 level 4")
        self.run_cmd(" destroy toy_2")
        self.assertEqual([verb for verb, _ in self.audit_log],
                         ["spawn", "set", "destroy"])

    def test_set_entry_records_requested_and_applied_distinguishably(self):
        self.run_cmd(" set toy_1 level 99")  # clamps to 5
        verb, detail = self.audit_log[0]
        self.assertEqual(verb, "set")
        self.assertIn("requested=99", detail)
        self.assertIn("applied=5", detail)

    def test_destroy_entry_identifies_target(self):
        self.run_cmd(" destroy toy_1")
        verb, detail = self.audit_log[0]
        self.assertEqual(verb, "destroy")
        self.assertIn("Teddy", detail)

    def test_audit_failure_leaves_mutation_applied_and_notes_it(self):
        cmd = self.run_cmd(" set toy_1 level 4", audit_fail=True)
        self.assertEqual(self.adapter.instances["toy_1"].level, 4)
        self.assertIn("audit logging failed", self.output(cmd))

    def test_audit_failure_on_destroy_leaves_deletion_applied(self):
        cmd = self.run_cmd(" destroy toy_1", audit_fail=True)
        self.assertNotIn("toy_1", self.adapter.instances)
        self.assertIn("audit logging failed", self.output(cmd))


# ------------------------------------------------------------------ #
#  the pure clamp/coerce helpers (targets of prop tasks 1.13/1.14)
# ------------------------------------------------------------------ #

class TestClampHelper(unittest.TestCase):

    def test_clamped_iff_applied_differs_from_requested(self):
        spec = _FIELDS["level"]
        applied, clamped, lo, hi = clamp_field_value(spec, None, 3)
        self.assertEqual((applied, clamped), (3, False))
        applied, clamped, lo, hi = clamp_field_value(spec, None, 9)
        self.assertEqual((applied, clamped, lo, hi), (5, True, 1, 5))

    def test_non_numeric_kinds_never_clamp(self):
        spec = _FIELDS["label"]
        applied, clamped, lo, hi = clamp_field_value(spec, None, "anything")
        self.assertEqual((applied, clamped, lo, hi),
                         ("anything", False, None, None))

    def test_coerce_reports_kind_and_enum_errors(self):
        value, err = coerce_field_value(_FIELDS["level"], "4")
        self.assertEqual((value, err), (4, None))
        _, err = coerce_field_value(_FIELDS["level"], "x")
        self.assertIn("int", err)
        _, err = coerce_field_value(_FIELDS["rarity"], "mythic")
        self.assertIn("epic", err)


if __name__ == "__main__":
    unittest.main()
