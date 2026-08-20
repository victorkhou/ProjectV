"""
Unit tests for the ``EntityAdminRouter`` base (unified-admin-crud task 1.16).

The consolidated router-base test module: later phase tasks (3.2–3.4, 5.4,
7.6) append per-router tests for the migrated ``@<entity>`` commands here.
Built on the ``test_command_router.py`` router-testing patterns (concrete
toy subclass + FakeCaller) via the toy-adapter approach of
``test_entity_admin_router.py`` / ``test_entity_admin_mutations.py``.

Covers the seven router-base behaviors the task names:

- alias dispatch to the canonical handler + one-line deprecation note
  naming both spellings — Requirements 11.1, 11.2
- opted-out verb → declared reason, no state change — Requirement 1.5
- unknown verb → error listing the available verbs — Requirement 1.8
- ``def`` sub-dispatch (pivot into the Definition_Scope handlers, def-verb
  perms, unknown def subverbs)
- per-field perm escalation checked after the verb tier, before bounds;
  insufficient tier rejects in full naming the required tier —
  Requirements 8.4, 8.5
- bulk-destroy confirmation: count + identities shown, nothing deleted
  before explicit confirmation, cancel → no state change — Requirement 4.5
- audit-write failure leaves the mutation applied and notes the failure in
  the response — Requirement 9.4
"""

import unittest

from mygame.commands.admin_commands import CmdAdminItem
from mygame.commands.command_router import EntityAdminRouter
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.item_adapter import ItemAdapter
from world.admin.resolution import Resolution
from world.admin.types import FieldSpec, InstanceRow, ShowReport
from world.definitions import ItemDef

# Shared caller double: real Evennia hierarchy permissions, process-unique
# caller ids, and one message-capture spelling. The eight hand-written
# callers this file used to carry implemented TWO different permission
# rules — four the hierarchy rule, four a bare set-membership check — so
# whether a Builder could pass a "Player"-tier gate depended on which
# entity's section the test sat in. See router_harness.py.
from .router_harness import RouterCaller, RouterTestCase, next_entity_id

FakeCaller = RouterCaller


class Gadget:
    """A live instance with the fields the adapter declares."""

    def __init__(self, key, name, level=3, xp_mult=1.0):
        self.key = key
        self.name = name
        self.level = level
        self.xp_mult = xp_mult


_FIELDS = {
    "level": FieldSpec(name="level", kind="int", min_value=1, max_value=5,
                       perm="Builder"),
    "xp_mult": FieldSpec(name="xp_mult", kind="float", perm="Admin"),
}

_DEF_FIELDS = {
    "level": FieldSpec(name="level", kind="int", perm="Builder"),
}


class GadgetAdapter:
    """EntityAdapter double exercising the full router-base contract.

    Supports every core verb except ``spawn`` (opted out with a reason),
    declares one extra verb, and installs aliases to both an instance
    verb and a def verb.
    """

    entity_key = "gadget"
    def_domain = "gadgets"
    supported_verbs = frozenset({
        "list", "show", "set", "destroy",
        "def list", "def show", "def set", "def reset", "def diff",
    })
    opt_outs = {
        "spawn": "gadgets self-assemble — use the fabricator system",
    }
    extra_verbs = {"zap": "Zap a gadget"}
    aliases = {"stats": "show", "defs": "def list"}

    def __init__(self):
        self.instances = {
            "gadget_1": Gadget("gadget_1", "Widget"),
            "gadget_2": Gadget("gadget_2", "Sprocket", level=1),
        }
        self.definitions = {
            "widget": {"key": "widget", "name": "Widget Mk1", "level": 3},
        }
        self.mutations = []  # every CRUD-hook invocation, in order

    # --- resolution / listing ---
    def list_instances(self, caller, filter_str):
        return [
            InstanceRow(index=i, key=g.key, name=g.name,
                        summary=f"{g.name} (lvl {g.level})", ref=g)
            for i, g in enumerate(self.instances.values(), start=1)
        ]

    def resolve_instance(self, caller, token):
        gadget = self.instances.get(token)
        if gadget is None:
            return Resolution(ok=False,
                              error=f"No match found for '{token}'.")
        return Resolution(ok=True, target=gadget)

    # --- field schemas ---
    def instance_fields(self):
        return dict(_FIELDS)

    def definition_fields(self):
        return dict(_DEF_FIELDS)

    # --- CRUD hooks ---
    def create(self, caller, def_token, kwargs):
        self.mutations.append(("create", def_token))

    def read(self, caller, instance):
        return ShowReport(header=f"Gadget: {instance.name} ({instance.key})",
                          state_lines=[], fields=[])

    def update(self, caller, instance, field, value):
        self.mutations.append(("update", instance.key, field, value))
        setattr(instance, field, value)

    def delete(self, caller, instance):
        self.mutations.append(("delete", instance.key))
        del self.instances[instance.key]

    # --- definition scope ---
    def def_registry_dict(self):
        return self.definitions

    def def_resolve(self, token):
        return self.definitions.get(token.lower())


class FakeOverlay:
    """OverlayStore stand-in serving canned overrides/diffs (no disk)."""

    def __init__(self, overrides=None, diff=None):
        self._overrides = overrides or {}
        self._diff = diff or {}

    def get(self, domain, key):
        return dict(self._overrides.get((domain, key), {}))

    def diff(self):
        return dict(self._diff)


class GadgetRouter(EntityAdminRouter):
    key = "@gadget"
    adapter_key = "gadget"

    registry = None      # per-test AdapterRegistry
    overlay = FakeOverlay()  # per-test override (keeps def reads off disk)
    audit_log = None     # per-test list of (verb, detail)
    audit_fail = False   # raise from the audit sink (R9.4)

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.overlay

    def _log_admin(self, verb, detail):
        if self.audit_fail:
            raise RuntimeError("audit sink down")
        self.audit_log.append((verb, detail))

    def sub_zap(self, rest):
        self.caller.msg("Zap!")


class RouterBaseTestCase(RouterTestCase):
    """Fresh adapter/registry/caller per test; shared run() helper."""

    def setUp(self):
        super().setUp()
        self.adapter = GadgetAdapter()
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)
        self.caller = FakeCaller(perms=("Builder",))
        self.audit_log = []

    def run_cmd(self, args, caller=None, audit_fail=False):
        cmd = GadgetRouter()
        cmd.registry = self.registry
        cmd.audit_log = self.audit_log
        cmd.audit_fail = audit_fail
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd



# ------------------------------------------------------------------ #
#  Alias dispatch + deprecation note (R11.1, R11.2)
# ------------------------------------------------------------------ #

class TestAliasDispatch(RouterBaseTestCase):

    def test_alias_emits_one_line_note_naming_both_spellings(self):
        cmd = self.run_cmd(" stats gadget_1")
        note = cmd.caller.messages[0]
        self.assertNotIn("\n", note)          # one line
        self.assertIn("stats", note)           # invoked spelling
        self.assertIn("show", note)            # canonical spelling

    def test_alias_output_identical_to_canonical_after_the_note(self):
        alias_cmd = self.run_cmd(" stats gadget_1")
        canon_cmd = self.run_cmd(" show gadget_1",
                                 caller=FakeCaller(perms=("Builder",)))
        self.assertEqual(alias_cmd.caller.messages[1:],
                         canon_cmd.caller.messages)

    def test_alias_to_def_verb_dispatches_the_def_handler(self):
        alias_cmd = self.run_cmd(" defs")
        canon_cmd = self.run_cmd(" def list",
                                 caller=FakeCaller(perms=("Builder",)))
        self.assertEqual(alias_cmd.caller.messages[1:],
                         canon_cmd.caller.messages)

    def test_alias_perm_outcome_identical_to_canonical(self):
        # An alias of a mutating verb still runs the CANONICAL verb's
        # perm check — a denied caller is rejected identically (R11.1).
        denier = FakeCaller(perms=())
        cmd = self.run_cmd(" stats gadget_1", caller=denier)
        # The alias is rejected by the CANONICAL verb's gate, not its own.
        self.assertPermDenied(cmd, scope="verb", target="show")
        self.assertEqual(self.adapter.mutations, [])

    def test_alias_state_change_and_audit_identical_to_canonical(self):
        self.run_cmd(" set gadget_1 level 4")
        canonical_audit = list(self.audit_log)
        canonical_mutations = list(self.adapter.mutations)
        # Same command through an alias-shaped adapter: install a set
        # alias on the fly and re-run against a fresh adapter.
        self.adapter = GadgetAdapter()
        self.adapter.aliases = {"tweak": "set"}
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)
        self.audit_log = []
        self.run_cmd(" tweak gadget_1 level 4",
                     caller=FakeCaller(perms=("Builder",)))
        self.assertEqual(self.adapter.mutations, canonical_mutations)
        self.assertEqual(self.audit_log, canonical_audit)


# ------------------------------------------------------------------ #
#  Opt-out messaging (R1.5)
# ------------------------------------------------------------------ #

class TestOptOutMessaging(RouterBaseTestCase):

    def test_opted_out_verb_surfaces_declared_reason(self):
        cmd = self.run_cmd(" spawn widget")
        out = self.output(cmd)
        self.assertIn("gadgets self-assemble", out)        # declared reason
        self.assertIn("fabricator system", out)            # supported-path pointer

    def test_opted_out_verb_makes_no_state_change(self):
        self.run_cmd(" spawn widget")
        self.assertEqual(self.adapter.mutations, [])
        self.assertEqual(self.audit_log, [])

    def test_opted_out_verb_names_the_command_and_verb(self):
        cmd = self.run_cmd(" spawn widget")
        out = self.output(cmd)
        self.assertIn("@gadget", out)
        self.assertIn("spawn", out)


# ------------------------------------------------------------------ #
#  Unknown-verb listing (R1.8)
# ------------------------------------------------------------------ #

class TestUnknownVerbListing(RouterBaseTestCase):

    def test_unknown_verb_lists_every_available_spelling(self):
        cmd = self.run_cmd(" frobnicate")
        out = cmd.caller.messages[0]
        self.assertIn("frobnicate", out)
        for verb in ("list", "show", "set", "destroy",
                     "def list", "def show", "def set", "def reset",
                     "def diff", "zap", "stats", "defs"):
            self.assertIn(verb, out)

    def test_unknown_verb_makes_no_state_change(self):
        self.run_cmd(" frobnicate gadget_1")
        self.assertEqual(self.adapter.mutations, [])
        self.assertEqual(self.audit_log, [])


# ------------------------------------------------------------------ #
#  def sub-dispatch
# ------------------------------------------------------------------ #

class TestDefSubDispatch(RouterBaseTestCase):

    def test_def_pivots_into_the_definition_scope_handlers(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("widget", out)
        self.assertIn("Widget Mk1", out)

    def test_def_show_dispatches_with_the_sub_args(self):
        cmd = self.run_cmd(" def show widget")
        out = self.output(cmd)
        self.assertIn("gadget definition: widget", out)
        self.assertIn("level: 3", out)

    def test_bare_def_shows_def_usage(self):
        cmd = self.run_cmd(" def")
        out = cmd.caller.messages[0]
        self.assertIn("def", out)
        self.assertIn("def list", out)

    def test_unknown_def_subverb_lists_available_def_verbs(self):
        cmd = self.run_cmd(" def frob")
        out = cmd.caller.messages[0]
        self.assertIn("def frob", out)
        for verb in ("def list", "def show", "def set", "def reset",
                     "def diff"):
            self.assertIn(verb, out)

    def test_def_write_verbs_gated_at_admin_inside_the_sub_dispatch(self):
        for verb, args in ((" def set", " def set widget level 5"),
                          (" def reset", " def reset widget level")):
            cmd = self.run_cmd(args, caller=FakeCaller(perms=("Builder",)))
            self.assertPermDenied(cmd, required="Admin", scope="verb",
                                  target=verb.strip())

    def test_def_read_verbs_pass_at_builder(self):
        for args in (" def list", " def show widget", " def diff"):
            cmd = self.run_cmd(args, caller=FakeCaller(perms=("Builder",)))
            self.assertNotIn("Permission denied", self.output(cmd))


# ------------------------------------------------------------------ #
#  Per-field perm escalation (R8.4, R8.5)
# ------------------------------------------------------------------ #

class TestFieldPermEscalation(RouterBaseTestCase):

    def test_escalated_field_rejected_in_full_naming_required_tier(self):
        cmd = self.run_cmd(" set gadget_1 xp_mult 2")
        self.assertPermDenied(cmd, required="Admin", scope="field",
                              target="xp_mult")
        self.assertEqual(self.adapter.instances["gadget_1"].xp_mult, 1.0)
        self.assertEqual(self.adapter.mutations, [])       # nothing written
        self.assertEqual(self.audit_log, [])

    def test_escalated_field_applies_at_the_escalated_tier(self):
        admin = FakeCaller(perms=("Builder", "Admin"))
        self.run_cmd(" set gadget_1 xp_mult 2", caller=admin)
        self.assertEqual(self.adapter.instances["gadget_1"].xp_mult, 2.0)

    def test_field_at_verb_tier_adds_no_extra_check(self):
        # Caller holds exactly Builder (the set verb tier): a Builder
        # field passes with no additional field-level gate (R8.4).
        cmd = self.run_cmd(" set gadget_1 level 2")
        self.assertNotIn("Permission denied", self.output(cmd))
        self.assertEqual(self.adapter.instances["gadget_1"].level, 2)


# ------------------------------------------------------------------ #
#  Bulk-destroy confirmation (R4.5)
# ------------------------------------------------------------------ #

class TestBulkDestroyConfirmation(RouterBaseTestCase):

    def test_multi_target_shows_count_and_identities_deletes_nothing(self):
        cmd = self.run_cmd(" destroy gadget_1, gadget_2")
        out = self.output(cmd)
        self.assertIn("2", out)
        self.assertIn("Widget", out)
        self.assertIn("Sprocket", out)
        self.assertIn("destroy confirm", out)
        self.assertEqual(len(self.adapter.instances), 2)   # nothing deleted
        self.assertEqual(self.adapter.mutations, [])
        self.assertEqual(self.audit_log, [])

    def test_confirm_executes_the_pending_bulk_destroy(self):
        self.run_cmd(" destroy gadget_1, gadget_2")
        cmd = self.run_cmd(" destroy confirm")
        self.assertEqual(self.adapter.instances, {})
        out = self.output(cmd)
        self.assertIn("Destroyed 2", out)
        self.assertIn("Widget", out)
        self.assertIn("Sprocket", out)

    def test_cancel_declines_with_no_state_change(self):
        self.run_cmd(" destroy gadget_1, gadget_2")
        cmd = self.run_cmd(" destroy cancel")
        self.assertIn("cancelled", self.output(cmd))
        self.assertEqual(len(self.adapter.instances), 2)
        self.assertEqual(self.adapter.mutations, [])
        # The declined destroy leaves nothing pending.
        cmd2 = self.run_cmd(" destroy confirm")
        self.assertIn("No destroy is pending", self.output(cmd2))
        self.assertEqual(len(self.adapter.instances), 2)

    def test_single_target_needs_no_confirmation(self):
        cmd = self.run_cmd(" destroy gadget_1")
        self.assertNotIn("gadget_1", self.adapter.instances)
        self.assertIn("Destroyed", self.output(cmd))
        self.assertNotIn("destroy confirm", self.output(cmd))


# ------------------------------------------------------------------ #
#  Audit-failure note (R9.4)
# ------------------------------------------------------------------ #

class TestAuditFailureNote(RouterBaseTestCase):

    def test_audit_failure_leaves_set_applied_and_notes_it(self):
        cmd = self.run_cmd(" set gadget_1 level 4", audit_fail=True)
        self.assertEqual(self.adapter.instances["gadget_1"].level, 4)
        self.assertIn("audit logging failed", self.output(cmd))

    def test_audit_failure_leaves_destroy_applied_and_notes_it(self):
        cmd = self.run_cmd(" destroy gadget_1", audit_fail=True)
        self.assertNotIn("gadget_1", self.adapter.instances)
        self.assertIn("audit logging failed", self.output(cmd))

    def test_audit_success_adds_no_failure_note(self):
        cmd = self.run_cmd(" set gadget_1 level 4")
        self.assertNotIn("audit logging failed", self.output(cmd))
        self.assertEqual(len(self.audit_log), 1)


# ================================================================== #
#  Migrated @item router (unified-admin-crud task 3.2)
#
#  CmdAdminItem is now an EntityAdminRouter subclass driven by the real
#  ItemAdapter with an injected fake DataRegistry (the
#  world/admin/tests/test_item_adapter.py FakeRegistry/Player pattern).
#  The pre-migration @item tests are ported here onto the unified
#  grammar; where a test asserted old behavior the design deliberately
#  changed (`list` meant definitions; positional spawn count; def
#  indexes) the test asserts the NEW contract instead (Requirement
#  11.4: `list` = instances + the moved-to-'def list' pointer).
# ================================================================== #

import sys
import types
from unittest import mock

from world.systems.loot_roller import RARITY_ORDER  # noqa: E402


class _ItemDb:
    """Attribute-bag db proxy for FakeGameItem (get/set anything)."""

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


class FakeGameItem:
    """Live GameItem stand-in: db bag + object key + deletion path."""

    def __init__(self, item_key, key=None, **fields):
        self.key = key or item_key
        self.db = _ItemDb(item_key=item_key, **fields)
        self._deleted = False

    def delete(self):
        self._deleted = True
        return True


class FakeEquipment:
    """Supply_Bag stand-in honoring the per-entry max_stack cap."""

    def __init__(self):
        self.supplies = {}

    def add_supply(self, item_key, count, max_stack=99):
        current = self.supplies.get(item_key, 0)
        added = max(0, min(count, max_stack - current))
        self.supplies[item_key] = current + added
        return added


class FakeDataRegistry:
    """DataRegistry double: ``items`` dict + the ``resolve_item`` matcher."""

    def __init__(self, defs):
        self.items = {d.key: d for d in defs}

    def resolve_item(self, token):
        if token in self.items:
            return self.items[token]
        lowered = token.lower()
        matches = [
            d for d in self.items.values()
            if d.key.lower().startswith(lowered)
            or d.name.lower().startswith(lowered)
        ]
        return matches[0] if len(matches) == 1 else None

    def get_item(self, key):
        return self.items[key]


class ItemCaller(RouterCaller):
    """Caller stub for @item: holdings, perm hierarchy, player search."""

    def __init__(self, name="Admin", perm_level="Builder", contents=None):
        super().__init__(tier=perm_level, key=name, contents=contents)


class ItemTarget:
    """Grant-recipient stub (a player with an equipment handler)."""

    def __init__(self, name="Bob"):
        self.key = name
        self.equipment = FakeEquipment()
        self._messages = []

    def msg(self, text, **kwargs):
        self._messages.append(text)


# A plain Gear def, a Supply def, and a rolled Gear def cover every
# spawn/set branch (mirrors the pre-migration fixtures).
_ITEM_RIFLE = ItemDef(
    key="assault_rifle", name="Assault Rifle", category="weapon",
    slot="weapon_ranged", weapon_type="ranged", weight=10.0,
)
_ITEM_GRENADE = ItemDef(
    key="frag_grenade", name="Frag Grenade", category="throwable",
    max_stack=5, weight=3.0,
)
_ITEM_SNIPER_SPEC = {
    "stats": {
        "damage": {"min": 40, "max": 60, "weight": 3},
        "range": {"min": 8, "max": 12, "weight": 1},
    },
}
_ITEM_SNIPER = ItemDef(
    key="sniper_rifle", name="Sniper Rifle", category="weapon",
    slot="weapon_ranged", weapon_type="ranged", weight=12.0,
    roll_spec=_ITEM_SNIPER_SPEC,
)


class ItemRouterUnderTest(CmdAdminItem):
    """CmdAdminItem with the registry/overlay test hooks injected."""

    registry = None          # per-test AdapterRegistry
    overlay = FakeOverlay()  # keeps def-scope reads off disk


    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.overlay


def _item_cmd(caller, args, defs=None):
    """Build + run one @item invocation against an injected registry."""
    adapter = ItemAdapter(registry=FakeDataRegistry(
        defs or [_ITEM_RIFLE, _ITEM_GRENADE, _ITEM_SNIPER]))
    registry = AdapterRegistry()
    registry.register(adapter)
    cmd = ItemRouterUnderTest()
    cmd.registry = registry
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    cmd.func()
    return cmd


def _patch_create_game_item(created):
    """Patch typeclasses.objects.create_game_item to mint dict-shaped
    items — the loot roller's writes are duck-typed, so the dict records
    exactly what the spawn path stamped (rolled_stats / rarity / iqs)."""
    fake_objects = types.ModuleType("typeclasses.objects")

    def create(owner, idef):
        item = {}
        created.append((owner, idef, item))
        return item

    fake_objects.create_game_item = create
    return mock.patch.dict(sys.modules, {
        "typeclasses": types.ModuleType("typeclasses"),
        "typeclasses.objects": fake_objects,
    })


class ItemRouterTestCase(RouterTestCase):
    """Shared plumbing: fresh List_Cache per test + output helper."""

    def setUp(self):
        super().setUp()



# ------------------------------------------------------------------ #
#  Migration wiring: stats alias, list pointer, def list, destroy
#  (Requirements 4.1, 4.4, 5.7, 11.1, 11.2, 11.4, 11.5)
# ------------------------------------------------------------------ #

class TestItemMigrationWiring(ItemRouterTestCase):

    def test_stats_alias_dispatches_show_with_deprecation_note(self):
        item = FakeGameItem("sniper_rifle",
                            rolled_stats={"damage": 50.0}, iqs=50)
        alias_cmd = _item_cmd(ItemCaller(contents=[item]), " stats sniper")
        note = alias_cmd.caller._messages[0]
        self.assertNotIn("\n", note)      # one line (R11.2)
        self.assertIn("stats", note)       # invoked spelling
        self.assertIn("show", note)        # canonical spelling
        # Output after the note is identical to the canonical verb (R11.1).
        item2 = FakeGameItem("sniper_rifle",
                             rolled_stats={"damage": 50.0}, iqs=50)
        show_cmd = _item_cmd(ItemCaller(contents=[item2]), " show sniper")
        self.assertEqual(alias_cmd.caller._messages[1:],
                         show_cmd.caller._messages)

    def test_list_shows_instances_with_def_list_pointer(self):
        caller = ItemCaller(contents=[
            FakeGameItem("assault_rifle"), FakeGameItem("sniper_rifle"),
        ])
        cmd = _item_cmd(caller, " list")
        out = self.output(cmd)
        self.assertIn("#1", out)            # indexed instance rows (R4.1)
        self.assertIn("#2", out)
        self.assertIn("assault_rifle", out)
        self.assertIn("sniper_rifle", out)
        # The deprecation-window pointer: definition listing moved (R11.4).
        self.assertIn("def list", out)
        self.assertIn("moved", out)

    def test_empty_list_still_includes_the_pointer(self):
        cmd = _item_cmd(ItemCaller(), " list")
        out = self.output(cmd)
        self.assertIn("No item instances", out)
        self.assertIn("def list", out)

    def test_def_list_serves_the_definitions(self):
        cmd = _item_cmd(ItemCaller(), " def list")
        out = self.output(cmd)
        for key in ("assault_rifle", "frag_grenade", "sniper_rifle"):
            self.assertIn(key, out)

    def test_destroy_deletes_an_instance(self):
        item = FakeGameItem("sniper_rifle")
        cmd = _item_cmd(ItemCaller(contents=[item]), " destroy sniper")
        self.assertTrue(item._deleted)
        self.assertIn("Destroyed", self.output(cmd))


# ------------------------------------------------------------------ #
#  spawn — gear/supply branches (ported; count is now a kwarg)
# ------------------------------------------------------------------ #

class TestItemSpawnGear(ItemRouterTestCase):
    """@item spawn <gear> creates equippable objects in the recipient's
    inventory (count=N kwarg under the unified grammar)."""

    def test_spawn_gear_creates_objects(self):
        target = ItemTarget(name="Bob")
        caller = ItemCaller()
        caller._search_results["Bob"] = target

        created = []
        with _patch_create_game_item(created):
            cmd = _item_cmd(caller, " spawn assault_rifle count=2 Bob")

        self.assertEqual(len(created), 2)
        self.assertTrue(all(idef is _ITEM_RIFLE and owner is target
                            for owner, idef, _item in created))
        self.assertIn("2x Assault Rifle", self.output(cmd))

    def test_spawn_gear_defaults_to_caller(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn assault_rifle")

        self.assertEqual(len(created), 1)
        self.assertIs(created[0][0], caller)  # defaults to caller


class TestItemSpawnSupply(ItemRouterTestCase):
    """@item spawn <supply> adds counts to the recipient's Supply_Bag."""

    def test_spawn_supply_adds_to_bag(self):
        target = ItemTarget(name="Bob")
        caller = ItemCaller()
        caller._search_results["Bob"] = target

        cmd = _item_cmd(caller, " spawn frag_grenade count=3 Bob")

        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 3)
        self.assertIn("3x Frag Grenade", self.output(cmd))

    def test_spawn_supply_respects_stack_cap(self):
        target = ItemTarget(name="Bob")
        caller = ItemCaller()
        caller._search_results["Bob"] = target

        # max_stack=5, request 8 → 5 added; the response reports what was
        # actually granted.
        cmd = _item_cmd(caller, " spawn frag_grenade count=8 Bob")

        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 5)
        self.assertIn("5x Frag Grenade", self.output(cmd))


class TestItemSpawnErrors(ItemRouterTestCase):
    """@item spawn input validation."""

    def test_spawn_no_args_shows_usage(self):
        cmd = _item_cmd(ItemCaller(), " spawn")
        self.assertIn("Usage", self.output(cmd))

    def test_spawn_unknown_item(self):
        cmd = _item_cmd(ItemCaller(), " spawn nonexistent")
        out = self.output(cmd)
        self.assertIn("No definition found", out)
        self.assertIn("nonexistent", out)

    def test_spawn_unknown_player(self):
        cmd = _item_cmd(ItemCaller(), " spawn frag_grenade count=1 Nobody")
        out = self.output(cmd)
        self.assertIn("Could not resolve player", out)
        self.assertIn("Nobody", out)


class TestItemSpawnByPrefix(ItemRouterTestCase):
    """Spawn def tokens resolve by key/name/prefix via resolve_item.

    Definition INDEXES are gone with the list-meaning change: `#N`/bare
    numbers indexed the old def list, which moved to `def list`
    (Requirement 11.4) — a numeric token is now just an unknown def."""

    def test_spawn_by_prefix(self):
        target = ItemTarget(name="Bob")
        caller = ItemCaller()
        caller._search_results["Bob"] = target
        # "frag" uniquely prefixes frag_grenade.
        _item_cmd(caller, " spawn frag count=3 Bob")
        self.assertEqual(target.equipment.supplies.get("frag_grenade"), 3)

    def test_spawn_numeric_def_index_no_longer_supported(self):
        cmd = _item_cmd(ItemCaller(), " spawn 99")
        self.assertIn("No definition found", self.output(cmd))


# ------------------------------------------------------------------ #
#  list — instance meaning (ported from the def-list tests, R11.4)
# ------------------------------------------------------------------ #

class TestItemListInstances(ItemRouterTestCase):
    """@item list enumerates the caller's live item instances (the old
    definition meaning moved to `def list`), optionally filtered."""

    def _caller_with_items(self):
        return ItemCaller(contents=[
            FakeGameItem("assault_rifle", category="weapon"),
            FakeGameItem("frag_grenade", category="throwable"),
        ])

    def test_list_all_instances(self):
        cmd = _item_cmd(self._caller_with_items(), " list")
        out = self.output(cmd)
        self.assertIn("assault_rifle", out)
        self.assertIn("frag_grenade", out)

    def test_list_shows_index_numbers(self):
        cmd = _item_cmd(self._caller_with_items(), " list")
        out = self.output(cmd)
        self.assertIn("#1", out)
        self.assertIn("#2", out)

    def test_list_filter_by_category(self):
        cmd = _item_cmd(self._caller_with_items(), " list weapon")
        out = self.output(cmd)
        self.assertIn("assault_rifle", out)
        self.assertNotIn("frag_grenade", out)

    def test_list_filter_no_match(self):
        cmd = _item_cmd(self._caller_with_items(), " list bogus")
        self.assertIn("No item instances", self.output(cmd))

    def test_list_replaces_the_list_cache_for_hash_n(self):
        caller = self._caller_with_items()
        _item_cmd(caller, " list")
        cmd = _item_cmd(caller, " show #1")
        self.assertIn("assault_rifle", self.output(cmd))


# ------------------------------------------------------------------ #
#  Permissions (ported unchanged: Builder floor per verb)
# ------------------------------------------------------------------ #

class TestItemPermissions(ItemRouterTestCase):
    """@item subcommands require Builder+."""

    def test_spawn_denied_for_player(self):
        cmd = _item_cmd(ItemCaller(perm_level="Player"),
                        " spawn frag_grenade")
        self.assertPermDenied(cmd, scope="verb", target="spawn")

    def test_set_denied_for_player(self):
        cmd = _item_cmd(ItemCaller(perm_level="Player"),
                        " set rifle damage 50")
        self.assertPermDenied(cmd, scope="verb", target="set")

    def test_stats_denied_for_player(self):
        # The alias runs the CANONICAL verb's perm check (R11.1).
        cmd = _item_cmd(ItemCaller(perm_level="Player"), " stats rifle")
        self.assertPermDenied(cmd, scope="verb", target="show")

    def test_def_set_denied_for_builder(self):
        cmd = _item_cmd(ItemCaller(perm_level="Builder"),
                        " def set sniper_rifle weight 5")
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def set")


# ------------------------------------------------------------------ #
#  spawn iqs=/rarity= (rolled-gear admin tooling, ported)
# ------------------------------------------------------------------ #

class TestItemSpawnWithQuality(ItemRouterTestCase):
    """@item spawn iqs=<N> stamps a deterministic roll at that quality."""

    def test_iqs_stamps_stats_at_the_quality_fraction(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn sniper_rifle iqs=90")

        self.assertEqual(len(created), 1)
        item = created[0][2]
        # rolled = min + 0.9 * (max - min), per stat.
        self.assertAlmostEqual(item["rolled_stats"]["damage"], 58.0)
        self.assertAlmostEqual(item["rolled_stats"]["range"], 11.6)
        # The stamped base IQS reads back exactly the requested value.
        self.assertEqual(item["iqs"], 90)
        # No rarity requested → none stamped (neutral read).
        self.assertNotIn("rarity", item)

    def test_iqs_with_rarity_stamps_the_tier(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn sniper_rifle iqs=90 rarity=legendary")

        item = created[0][2]
        self.assertEqual(item["rarity"], "legendary")
        self.assertEqual(item["iqs"], 90)

    def test_iqs_out_of_range_is_clamped(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn sniper_rifle iqs=150")

        item = created[0][2]
        self.assertEqual(item["rolled_stats"]["damage"], 60)  # band max
        self.assertEqual(item["iqs"], 100)

    def test_invalid_rarity_rejected_nothing_created(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            cmd = _item_cmd(caller, " spawn sniper_rifle rarity=mythic")

        self.assertEqual(created, [])
        out = self.output(cmd)
        self.assertIn("unknown rarity", out)
        self.assertIn("mythic", out)

    def test_non_numeric_iqs_rejected(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            cmd = _item_cmd(caller, " spawn sniper_rifle iqs=high")

        self.assertEqual(created, [])
        self.assertIn("must be a number", self.output(cmd))

    def test_forced_rarity_alone_rolls_with_the_tier_floor(self):
        # rarity= without iqs=: a random roll forced to the tier — its roll
        # floor applies, so every stat lands at or above floor**skew of the
        # band (legendary 0.75² = 0.5625 → damage ≥ 40 + 20·0.5625 = 51.25).
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn sniper_rifle rarity=legendary")

        item = created[0][2]
        self.assertEqual(item["rarity"], "legendary")
        self.assertGreaterEqual(item["rolled_stats"]["damage"], 51.25 - 1e-9)
        self.assertLessEqual(item["rolled_stats"]["damage"], 60)


class TestItemSpawnDefaultRoll(ItemRouterTestCase):
    """Without iqs=, rolled defs get a normal random roll on spawn; defs
    without roll bands stay fixed exactly as always."""

    def test_rolled_def_gets_random_roll(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn sniper_rifle")

        item = created[0][2]
        self.assertIn("rolled_stats", item)
        self.assertTrue(40 <= item["rolled_stats"]["damage"] <= 60)
        self.assertTrue(8 <= item["rolled_stats"]["range"] <= 12)
        self.assertTrue(0 <= item["iqs"])

    def test_unrolled_def_stays_fixed(self):
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn assault_rifle")

        item = created[0][2]
        self.assertNotIn("rolled_stats", item)
        self.assertNotIn("iqs", item)

    def test_unrolled_def_ignores_iqs(self):
        # A fixed def stays fixed even when iqs= is passed — the roll
        # wiring never touches defs without roll bands.
        caller = ItemCaller()
        created = []
        with _patch_create_game_item(created):
            _item_cmd(caller, " spawn assault_rifle iqs=50")

        item = created[0][2]
        self.assertNotIn("rolled_stats", item)
        self.assertNotIn("iqs", item)

    def test_supply_with_iqs_still_grants(self):
        # Supplies carry no per-instance rolls; the grant itself works.
        caller = ItemCaller()
        caller.equipment = FakeEquipment()
        _item_cmd(caller, " spawn frag_grenade count=3 iqs=50")
        self.assertEqual(caller.equipment.supplies.get("frag_grenade"), 3)


# ------------------------------------------------------------------ #
#  set — band clamp + IQS re-stamp through the shared handler (ported)
# ------------------------------------------------------------------ #

class TestItemSet(ItemRouterTestCase):
    """@item set <item> <stat> <value> writes the rolled_stats override
    (clamped to the roll band) and re-stamps IQS (Requirement 7.6)."""

    def _caller_with_item(self, item_key="sniper_rifle", **fields):
        item = FakeGameItem(item_key, **fields)
        return ItemCaller(contents=[item]), item

    def test_set_in_band_writes_rolled_stat_and_restamps_iqs(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper damage 50")

        self.assertEqual(item.db.rolled_stats, {"damage": 50.0})
        # Only damage is rolled: q = (50-40)/20 = 0.5 → IQS 50.
        self.assertEqual(item.db.iqs, 50)
        self.assertFieldSet(cmd, field="damage", applied=50.0)

    def test_set_above_band_clamps_with_note(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper damage 999")

        self.assertEqual(item.db.rolled_stats["damage"], 60.0)
        self.assertEqual(item.db.iqs, 100)
        # The band is 40-60; 999 clamps to the upper bound.
        self.assertClamped(cmd, field="damage", applied=60.0,
                           lo=40.0, hi=60.0, requested=999.0)

    def test_set_below_band_clamps_with_note(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper damage -5")

        self.assertEqual(item.db.rolled_stats["damage"], 40.0)
        self.assertClamped(cmd, field="damage", applied=40.0,
                           lo=40.0, hi=60.0, requested=-5.0)

    def test_set_preserves_other_rolled_stats(self):
        caller, item = self._caller_with_item(
            rolled_stats={"damage": 45.0, "range": 9.0})
        _item_cmd(caller, " set sniper damage 50")

        self.assertEqual(item.db.rolled_stats,
                         {"damage": 50.0, "range": 9.0})

    def test_set_unknown_field_rejected_naming_valid_fields(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper accuracy 5")

        self.assertIsNone(item.db.rolled_stats)
        # R3.7: rejected by name, and the offered list names the real fields.
        self.assertUnknownField(cmd, field="accuracy",
                                valid=("damage", "range"), plane="instance")

    def test_set_on_fixed_item_rejected(self):
        # assault_rifle's def declares no roll_spec — nothing is settable.
        caller, item = self._caller_with_item(item_key="assault_rifle")
        cmd = _item_cmd(caller, " set assault damage 50")

        self.assertIsNone(item.db.rolled_stats)
        self.assertIn("not a modifiable stat", self.output(cmd))

    def test_set_rarity_valid_tier(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper rarity epic")

        self.assertEqual(item.db.rarity, "epic")
        self.assertFieldSet(cmd, field="rarity", applied="epic")

    def test_set_rarity_invalid_tier_rejected(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper rarity mythic")

        self.assertIsNone(item.db.rarity)
        out = self.output(cmd)
        self.assertIn("not a valid value", out)  # R3.9: lists valid values
        for tier in RARITY_ORDER:
            self.assertIn(tier, out)

    def test_set_unknown_item_reports(self):
        cmd = _item_cmd(ItemCaller(), " set ghost damage 50")
        self.assertIn("No match found", self.output(cmd))

    def test_set_non_numeric_value_rejected(self):
        caller, item = self._caller_with_item()
        cmd = _item_cmd(caller, " set sniper damage high")
        self.assertIsNone(item.db.rolled_stats)
        self.assertIn("cannot be interpreted", self.output(cmd))

    def test_set_too_few_args_shows_usage(self):
        cmd = _item_cmd(ItemCaller(), " set sniper")
        self.assertIn("Usage", self.output(cmd))

    def test_set_finds_equipped_items(self):
        caller = ItemCaller()
        item = FakeGameItem("sniper_rifle")

        class _Handler:
            def get_all_equipped(self):
                return {"weapon": item}

        caller.equipment = _Handler()
        _item_cmd(caller, " set sniper damage 44")
        self.assertEqual(item.db.rolled_stats, {"damage": 44.0})


# ------------------------------------------------------------------ #
#  stats → show alias readout (ported)
# ------------------------------------------------------------------ #

class TestItemStatsAliasRolledBands(ItemRouterTestCase):
    """@item stats <item> (the legacy spelling of `show`) lists each
    modifiable stat with its current value and [min–max] band, plus
    IQS/rarity state."""

    def _caller_with_item(self, item_key="sniper_rifle", **fields):
        item = FakeGameItem(item_key, **fields)
        return ItemCaller(contents=[item]), item

    def test_stats_shows_bands_and_current_values(self):
        caller, item = self._caller_with_item(
            rolled_stats={"damage": 50.0}, iqs=50, rarity="rare")
        cmd = _item_cmd(caller, " stats sniper")

        out = self.output(cmd)
        self.assertIn("damage", out)
        self.assertIn("[40–60]", out)
        self.assertIn("50", out)          # current rolled value
        self.assertIn("range", out)
        self.assertIn("[8–12]", out)
        self.assertIn("IQS: 50", out)
        self.assertIn("rare", out)

    def test_stats_lists_affix_state(self):
        caller, item = self._caller_with_item(
            rolled_stats={"damage": 50.0},
            affixes=[{"key": "keen", "stat": "damage_bonus",
                      "magnitude": 4.0, "value": 5.0}],
        )
        cmd = _item_cmd(caller, " stats sniper")
        self.assertIn("Affixes: 1", self.output(cmd))

    def test_stats_fixed_item_lists_no_band_fields(self):
        caller, item = self._caller_with_item(item_key="assault_rifle")
        cmd = _item_cmd(caller, " stats assault")
        out = self.output(cmd)
        # No roll bands → only the rarity field is modifiable.
        self.assertIn("rarity", out)
        self.assertNotIn("[40–60]", out)

    def test_stats_unknown_item_reports(self):
        cmd = _item_cmd(ItemCaller(), " stats ghost")
        self.assertIn("No match found", self.output(cmd))

    def test_stats_no_args_shows_usage(self):
        cmd = _item_cmd(ItemCaller(), " stats")
        self.assertIn("Usage", self.output(cmd))


if __name__ == "__main__":
    unittest.main()


# ================================================================== #
#  @item pilot router (unified-admin-crud task 3.2)
#
#  CmdAdminItem migrated to EntityAdminRouter, driven by the REAL
#  ItemAdapter (with an injected registry double — no live game):
#  - `list` shows live instances AND the def-list-moved pointer
#    (Requirements 4.1, 11.4)
#  - `stats` is a Migration_Alias of `show`: canonical output plus the
#    one-line deprecation note (Requirements 11.1, 11.2, 11.5)
#  - the def scope is reachable end-to-end (`def list`/`def show`/
#    `def diff`) — Requirements 4.1 (grammar), 5.4, 5.6, 5.7
# ================================================================== #

RIFLE_SPEC = {
    "stats": {
        "damage": {"min": 10.0, "max": 50.0, "weight": 1.0},
    },
}


class FakeItemRegistry:
    """DataRegistry double: ``items`` dict + ``resolve_item`` matcher."""

    def __init__(self, defs):
        self.items = {d.key: d for d in defs}

    def resolve_item(self, token):
        if token in self.items:
            return self.items[token]
        lowered = str(token).lower()
        matches = [
            d for d in self.items.values()
            if d.key.lower().startswith(lowered)
            or d.name.lower().startswith(lowered)
        ]
        return matches[0] if len(matches) == 1 else None

    def get_item(self, key):
        return self.items[key]


class ItemRouterTestCase(RouterTestCase):
    """CmdAdminItem + real ItemAdapter over dict-shaped held items."""

    def setUp(self):
        super().setUp()
        self.data_registry = FakeItemRegistry([
            ItemDef(key="rifle", name="Rifle", slot="weapon_ranged",
                    category="weapon", weapon_type="ranged", weight=3.0,
                    roll_spec=RIFLE_SPEC),
            ItemDef(key="medkit", name="Medkit", category="consumable"),
        ])
        self.adapter = ItemAdapter(registry=self.data_registry)
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)
        self.caller = FakeCaller(perms=("Builder",))
        # Dict-shaped held items (the loot roller's test shape) in the
        # caller's contents — the adapter's holdings scan finds them.
        self.caller.contents = [
            {"item_key": "rifle", "iqs": 72.0, "rarity": "rare",
             "category": "weapon", "rolled_stats": {"damage": 30.0}},
            {"item_key": "medkit", "category": "consumable"},
        ]

        outer = self

        class _ItemRouter(CmdAdminItem):
            """CmdAdminItem with the test registry/overlay injected."""

            def _adapter_registry(self):
                return outer.registry

            def _overlay_store(self):
                return outer.overlay

        self.router_cls = _ItemRouter
        self.overlay = FakeOverlay()

    def run_cmd(self, args, caller=None):
        cmd = self.router_cls()
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.func()
        return cmd



class TestItemRouterIdentity(ItemRouterTestCase):
    """The migration preserves the command key and the Builder lock."""

    def test_command_key_and_adapter_key_preserved(self):
        self.assertEqual(CmdAdminItem.key, "@item")
        self.assertEqual(CmdAdminItem.adapter_key, "item")
        # Compare by qualified name: admin_commands imports the router
        # base by its plain (non-``mygame.``) module spelling.
        self.assertIn(
            "EntityAdminRouter",
            [c.__name__ for c in CmdAdminItem.__mro__],
        )

    def test_builder_floor_lock_unchanged(self):
        self.assertIn("cmd:perm(Builder)", CmdAdminItem.locks)


class TestItemListInstancesWithPointer(ItemRouterTestCase):
    """`@item list` = live instances + the def-list pointer (R4.1, 11.4)."""

    def test_list_shows_live_instance_rows(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("Item instances (2)", out)
        self.assertIn("#1", out)
        self.assertIn("rifle", out)
        self.assertIn("medkit", out)

    def test_list_includes_the_def_list_moved_pointer(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("moved to '@item def list'", out)

    def test_empty_list_still_includes_the_pointer(self):
        empty_caller = FakeCaller(perms=("Builder",))
        empty_caller.contents = []
        cmd = self.run_cmd(" list", caller=empty_caller)
        out = self.output(cmd)
        self.assertIn("No item instances found", out)
        self.assertIn("moved to '@item def list'", out)

    def test_list_does_not_show_definitions(self):
        # The old def-list meaning moved to `def list`: an unheld def
        # (medkit is held; drop it) must not appear in `list` output.
        self.caller.contents = [self.caller.contents[0]]  # rifle only
        cmd = self.run_cmd(" list")
        listing = "\n".join(
            m for m in cmd.caller.messages if "moved to" not in m
        )
        self.assertNotIn("medkit", listing)


class TestItemStatsAlias(ItemRouterTestCase):
    """`stats` → `show` Migration_Alias (R11.1, 11.2, 11.5)."""

    def test_stats_emits_deprecation_note_naming_both_spellings(self):
        cmd = self.run_cmd(" stats rifle")
        note = cmd.caller.messages[0]
        self.assertNotIn("\n", note)
        self.assertIn("stats", note)
        self.assertIn("show", note)

    def test_stats_output_identical_to_show_after_the_note(self):
        alias_cmd = self.run_cmd(" stats rifle")
        show_caller = FakeCaller(perms=("Builder",))
        show_caller.contents = list(self.caller.contents)
        show_cmd = self.run_cmd(" show rifle", caller=show_caller)
        self.assertEqual(alias_cmd.caller.messages[1:],
                         show_cmd.caller.messages)

    def test_stats_renders_the_instance_show_report(self):
        cmd = self.run_cmd(" stats rifle")
        out = self.output(cmd)
        # Dict-shaped test items carry no object key, so the display
        # name falls back to the stamped item_key.
        self.assertIn("rifle (rifle) — item instance", out)
        self.assertIn("IQS: 72", out)
        self.assertIn("damage: 30.0 [10–50]", out)


class TestItemDefScopeReachable(ItemRouterTestCase):
    """The full def scope is live on @item (R5.4, 5.6, 5.7)."""

    def test_def_list_lists_item_definitions(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("Item definitions (2)", out)
        self.assertIn("medkit", out)
        self.assertIn("rifle", out)

    def test_def_show_renders_merged_definition(self):
        cmd = self.run_cmd(" def show rifle")
        out = self.output(cmd)
        self.assertIn("item definition: rifle", out)
        self.assertIn("name: Rifle", out)

    def test_def_show_flags_overridden_fields(self):
        self.overlay = FakeOverlay(
            overrides={("items", "rifle"): {"weight": 5.0}},
        )
        cmd = self.run_cmd(" def show rifle")
        out = self.output(cmd)
        self.assertIn("weight", out)
        self.assertIn("*override*", out)

    def test_def_diff_reads_the_items_domain(self):
        self.overlay = FakeOverlay(
            diff={"items": {"rifle": {"weight": 5.0}}},
        )
        cmd = self.run_cmd(" def diff")
        out = self.output(cmd)
        self.assertIn("rifle.weight = 5.0", out)

    def test_def_write_verbs_registered_at_admin(self):
        cmd = self.run_cmd(" def set rifle weight 5")
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def set")


# ================================================================== #
#  @item pilot — router-level set/show behavior (task 3.3)
#
#  Fills the gaps between the adapter-level coverage in
#  world/admin/tests/test_item_adapter.py (IQS re-stamp + idempotence
#  through adapter.update directly) and the task 3.2 router tests above
#  (alias/list/pointer/def scope): the same behaviors driven through
#  the ROUTER's `set` and `show` verbs end-to-end.
#
#  - dynamic-band clamp note in the router response, stating the
#    applied value and the def-derived band bounds (Requirement 7.6,
#    design D2)
#  - IQS re-stamped through `@item set` before the success response is
#    rendered (Requirement 7.6)
#  - set-twice idempotence THROUGH the router (same final rolled stats
#    and IQS — no drift through the clamp/re-stamp path)
#  - staleness note rendered in `@item show` output when a stamped
#    attribute differs from the current merged def (Requirement 10.3)
# ================================================================== #


class TestItemRouterSetClampNote(ItemRouterTestCase):
    """Router `set` clamp note carries the dynamic band bounds (R7.6)."""

    def test_out_of_band_set_renders_clamp_note_with_band_bounds(self):
        # RIFLE_SPEC bands damage 10–50: 999 clamps to the upper bound
        # and the response names the applied value AND the bounds.
        cmd = self.run_cmd(" set rifle damage 999")
        self.assertClamped(cmd, field="damage", applied=50.0,
                           lo=10.0, hi=50.0, requested=999.0)
        # The clamped value actually landed on the instance.
        self.assertEqual(
            self.caller.contents[0]["rolled_stats"]["damage"], 50.0)

    def test_below_band_set_clamps_to_the_lower_bound(self):
        cmd = self.run_cmd(" set rifle damage 1")
        self.assertClamped(cmd, field="damage", applied=10.0,
                           lo=10.0, hi=50.0, requested=1.0)
        self.assertEqual(
            self.caller.contents[0]["rolled_stats"]["damage"], 10.0)

    def test_in_band_set_has_no_clamp_note(self):
        cmd = self.run_cmd(" set rifle damage 40")
        self.assertNotClamped(cmd, field="damage", applied=40.0)


class TestItemRouterSetRestampsIqs(ItemRouterTestCase):
    """`@item set` on a roll field re-stamps IQS via the router (R7.6)."""

    def test_set_restamps_iqs_before_the_success_response(self):
        # Stamped at 72; damage 50 is the band max (quality 1.0) so the
        # recompute path must re-stamp IQS to 100.
        self.assertEqual(self.caller.contents[0]["iqs"], 72.0)
        cmd = self.run_cmd(" set rifle damage 50")
        self.assertFieldSet(cmd, field="damage", applied=50.0)
        self.assertEqual(self.caller.contents[0]["iqs"], 100)

    def test_clamped_set_restamps_iqs_from_the_applied_value(self):
        # 999 clamps to 50 (band max): IQS follows the APPLIED value.
        self.run_cmd(" set rifle damage 999")
        self.assertEqual(self.caller.contents[0]["iqs"], 100)

    def test_set_to_band_midpoint_restamps_proportionally(self):
        # damage 30 on the 10–50 band is quality 0.5 → IQS 50.
        self.run_cmd(" set rifle damage 30")
        self.assertEqual(self.caller.contents[0]["iqs"], 50)


class TestItemRouterSetIdempotence(ItemRouterTestCase):
    """Same `set` twice through the router → same final state (R3.6, 7.6)."""

    def test_set_twice_same_value_yields_same_final_state(self):
        self.run_cmd(" set rifle damage 40")
        rifle = self.caller.contents[0]
        first = (dict(rifle["rolled_stats"]), rifle["iqs"])
        self.run_cmd(" set rifle damage 40",
                     caller=self._same_holdings_caller())
        self.assertEqual((dict(rifle["rolled_stats"]), rifle["iqs"]), first)

    def test_clamped_set_twice_does_not_drift(self):
        # Out-of-band writes clamp then re-stamp; repeating the same
        # request must not compound through the clamp/re-stamp path.
        self.run_cmd(" set rifle damage 999")
        rifle = self.caller.contents[0]
        first = (dict(rifle["rolled_stats"]), rifle["iqs"])
        self.run_cmd(" set rifle damage 999",
                     caller=self._same_holdings_caller())
        self.assertEqual((dict(rifle["rolled_stats"]), rifle["iqs"]), first)

    def _same_holdings_caller(self):
        """A fresh caller holding the SAME live instances (fresh List
        Cache identity, same targets) for the second invocation."""
        caller = FakeCaller(perms=("Builder",))
        caller.contents = list(self.caller.contents)
        return caller


class TestItemRouterShowStalenessNote(ItemRouterTestCase):
    """`@item show` renders the staleness note for drifted stamps (R10.3)."""

    def _drift_weight(self):
        """Stamp weight 3.0 on the held rifle, then change the merged
        def to weight 5.0 (as a `def set` + reload would)."""
        self.caller.contents[0]["weight"] = 3.0
        old = self.data_registry.items["rifle"]
        self.data_registry.items["rifle"] = ItemDef(
            key=old.key, name=old.name, slot=old.slot,
            category=old.category, weapon_type=old.weapon_type,
            weight=5.0, roll_spec=old.roll_spec,
        )

    def test_show_appends_note_naming_attr_stamped_and_current_values(self):
        self._drift_weight()
        cmd = self.run_cmd(" show rifle")
        out = self.output(cmd)
        self.assertIn("note: weight stamped 3.0", out)
        self.assertIn("current def says 5.0", out)

    def test_show_has_no_staleness_note_when_stamps_match_the_def(self):
        # Stamped value equals the merged def value → no note.
        self.caller.contents[0]["weight"] = 3.0
        cmd = self.run_cmd(" show rifle")
        self.assertNotIn("def changed after spawn", self.output(cmd))

    def test_stats_alias_renders_the_same_staleness_note(self):
        # The Migration_Alias path shares the canonical show rendering
        # (R11.1) — the staleness note included.
        self._drift_weight()
        cmd = self.run_cmd(" stats rifle")
        out = self.output(cmd)
        self.assertIn("note: weight stamped 3.0", out)
        self.assertIn("current def says 5.0", out)


# ================================================================== #
#  Migrated @building router (unified-admin-crud task 5.1)
#
#  CmdAdminBuilding is now an EntityAdminRouter subclass driven by the
#  real BuildingAdapter with an injected fake DataRegistry. Covers the
#  migration wiring the task names:
#
#  - `list` = live instances + the moved-to-'def list' pointer; the old
#    def-meaning of `list` served by `def list` (Requirements 11.4, 11.6)
#  - NEW `show` and `set` verbs (Requirement 7.2): uniform readout with
#    the level [1–5] bounds rendered; bounded writes through the shared
#    building-attribute path
#  - `destroy` keeps its legacy no-target meaning (the building at the
#    caller's tile) while the targeted grammar works too
#  - the `open` extra verb kept on the router subclass
#  - spawn through the existing creation path (unresolved def token
#    errors, nothing created)
# ================================================================== #

from mygame.commands.admin_commands import CmdAdminBuilding  # noqa: E402
from world.admin.adapters.building_adapter import BuildingAdapter  # noqa: E402
from world.constants import MAX_BUILDING_LEVEL  # noqa: E402
from world.definitions import BuildingDef  # noqa: E402


def _bdef_hq():
    return BuildingDef(
        name="Headquarters", abbreviation="HQ", cost={}, max_health=1000,
        requires_hq=False, required_terrain=None, category="headquarters",
        produces=None,
    )


def _bdef_ex():
    return BuildingDef(
        name="Extractor", abbreviation="EX", cost={}, max_health=400,
        requires_hq=True, required_terrain="mountain", category="resource",
        produces="Iron",
    )


class FakeBuildingDataRegistry:
    """DataRegistry double: ``buildings`` dict + ``resolve_building``."""

    def __init__(self, defs):
        self.buildings = {d.abbreviation: d for d in defs}

    def resolve_building(self, token):
        t = token.strip().lower()
        for d in self.buildings.values():
            if d.abbreviation.lower() == t or d.name.lower() == t:
                return d
        matches = [
            d for d in self.buildings.values()
            if d.abbreviation.lower().startswith(t)
            or d.name.lower().startswith(t)
        ]
        return matches[0] if len(matches) == 1 else None

    def get_building(self, abbr):
        return self.buildings[abbr]


class _BAttrs:
    """Evennia attributes-handler stand-in."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class FakeLiveBuilding:
    """Live building stand-in: key, attributes handler, deletion path."""

    def __init__(self, key="Headquarters", building_type="HQ", level=1,
                 hp=1000, hp_max=1000):
        self.key = key
        self.attributes = _BAttrs({
            "building_type": building_type,
            "building_level": level,
            "hp": hp,
            "hp_max": hp_max,
        })
        self._deleted = False

    def delete(self):
        self._deleted = True
        return True

    def set_open(self, state):
        self.attributes.add("open", bool(state))


class FakePlanetRoom:
    """PlanetRoom stand-in: room-wide + per-tile building queries."""

    def __init__(self, buildings=None):
        self.key = "TestPlanet"
        self._buildings = list(buildings or [])

    def get_all_buildings(self):
        return list(self._buildings)

    def get_objects_at(self, x, y, type_tag=None):
        return list(self._buildings)


class _BuildingDb:
    """Minimal db proxy carrying the caller's tile coordinates."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, key):
        return None


class BuildingCaller(RouterCaller):
    """Caller stub for @building: location, coords, perm hierarchy."""

    def __init__(self, perm_level="Builder", location=None, coords=(3, 7)):
        super().__init__(tier=perm_level)
        self.location = location
        self.db = _BuildingDb(coord_x=coords[0], coord_y=coords[1])


class BuildingRouterUnderTest(CmdAdminBuilding):
    """CmdAdminBuilding with the registry/overlay test hooks injected."""

    registry = None          # per-test AdapterRegistry
    overlay = FakeOverlay()  # keeps def-scope reads off disk

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.overlay


def _building_cmd(caller, args, defs=None):
    """Build + run one @building invocation against an injected registry."""
    adapter = BuildingAdapter(registry=FakeBuildingDataRegistry(
        defs or [_bdef_hq(), _bdef_ex()]))
    registry = AdapterRegistry()
    registry.register(adapter)
    cmd = BuildingRouterUnderTest()
    cmd.registry = registry
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    cmd.func()
    return cmd


class BuildingRouterTestCase(RouterTestCase):
    """Shared plumbing: fresh List_Cache per test + output helper."""

    def setUp(self):
        super().setUp()
        self.hq = FakeLiveBuilding(key="Headquarters", building_type="HQ",
                                   level=2, hp=500, hp_max=1000)
        self.ex = FakeLiveBuilding(key="Extractor", building_type="EX",
                                   hp=400, hp_max=400)
        self.room = FakePlanetRoom([self.hq, self.ex])
        self.caller = BuildingCaller(location=self.room)



class TestBuildingMigrationWiring(BuildingRouterTestCase):
    """`list` = instances + pointer; old def-meaning on `def list`
    (Requirements 11.4, 11.6)."""

    def test_list_shows_instances_with_def_list_pointer(self):
        cmd = _building_cmd(self.caller, " list")
        out = self.output(cmd)
        self.assertIn("#1", out)            # indexed instance rows (R4.1)
        self.assertIn("#2", out)
        self.assertIn("Headquarters", out)
        self.assertIn("Extractor", out)
        # The deprecation-window pointer: definition listing moved (R11.4).
        self.assertIn("def list", out)
        self.assertIn("moved", out)

    def test_empty_list_still_includes_the_pointer(self):
        caller = BuildingCaller(location=FakePlanetRoom([]))
        cmd = _building_cmd(caller, " list")
        out = self.output(cmd)
        self.assertIn("No building instances", out)
        self.assertIn("def list", out)

    def test_def_list_serves_the_definitions(self):
        cmd = _building_cmd(self.caller, " def list")
        out = self.output(cmd)
        self.assertIn("Headquarters", out)
        self.assertIn("Extractor", out)

    def test_def_show_renders_the_merged_definition(self):
        cmd = _building_cmd(self.caller, " def show extractor")
        out = self.output(cmd)
        self.assertIn("EX", out)
        self.assertIn("max_health", out)
        self.assertIn("400", out)


class TestBuildingShow(BuildingRouterTestCase):
    """NEW `show` verb (Requirement 7.2): uniform readout with the
    modifiable-fields block and the level [1–5] bounds."""

    def test_show_renders_identity_state_and_field_bounds(self):
        cmd = _building_cmd(self.caller, " show Headquarters")
        out = self.output(cmd)
        self.assertIn("Headquarters (HQ)", out)
        self.assertIn("Level: 2", out)
        self.assertIn("500/1000", out)
        self.assertIn("Modifiable fields:", out)
        # level rendered with its static 1–5 bounds (R7.2).
        self.assertIn(f"level: 2 [1\u2013{MAX_BUILDING_LEVEL}]", out)

    def test_show_resolves_by_index_from_the_list_cache(self):
        _building_cmd(self.caller, " list")
        cmd = _building_cmd(self.caller, " show #2")
        self.assertIn("Extractor (EX)", self.output(cmd))


class TestBuildingSet(BuildingRouterTestCase):
    """NEW `set` verb (Requirement 7.2): bounded writes through the
    shared building-attribute path."""

    def test_set_level_in_bounds_writes_the_attribute(self):
        cmd = _building_cmd(self.caller, " set Headquarters level 4")
        self.assertNotClamped(cmd, field="level", applied=4,
                              target="Headquarters")
        self.assertEqual(self.hq.attributes.get("building_level"), 4)

    def test_set_level_above_max_clamps_to_five_with_note(self):
        # Task 5.4 names this case verbatim: `@building set level` clamps
        # to the static 1–5 bound with a note (Requirements 7.2, 3.2, D2).
        cmd = _building_cmd(self.caller, " set Headquarters level 9")
        self.assertClamped(cmd, field="level", applied=MAX_BUILDING_LEVEL,
                           lo=1, hi=MAX_BUILDING_LEVEL, requested=9)
        self.assertEqual(self.hq.attributes.get("building_level"),
                         MAX_BUILDING_LEVEL)

    def test_set_level_below_min_clamps_to_one_with_note(self):
        cmd = _building_cmd(self.caller, " set Headquarters level 0")
        self.assertClamped(cmd, field="level", applied=1,
                           lo=1, hi=MAX_BUILDING_LEVEL, requested=0)
        self.assertEqual(self.hq.attributes.get("building_level"), 1)

    def test_set_hp_clamps_into_the_targets_hp_max_with_note(self):
        cmd = _building_cmd(self.caller, " set Extractor hp 9999")
        # Dynamic upper bound: the Extractor's own hp_max.
        self.assertClamped(cmd, field="hp", applied=400, hi=400,
                           requested=9999)
        self.assertEqual(self.ex.attributes.get("hp"), 400)

    def test_set_unknown_field_names_the_valid_fields(self):
        cmd = _building_cmd(self.caller, " set Headquarters shield 5")
        self.assertUnknownField(cmd, field="shield", valid=("level",),
                                plane="instance")
        self.assertEqual(self.hq.attributes.get("building_level"), 2)


class TestBuildingDestroy(BuildingRouterTestCase):
    """`destroy` keeps its legacy no-target tile meaning alongside the
    targeted unified grammar (Requirement 11.6)."""

    def test_destroy_without_target_destroys_the_tile_building(self):
        cmd = _building_cmd(self.caller, " destroy")
        self.assertTrue(self.hq._deleted)
        self.assertIn("Destroyed", self.output(cmd))

    def test_destroy_without_target_and_no_building_reports(self):
        caller = BuildingCaller(location=FakePlanetRoom([]))
        cmd = _building_cmd(caller, " destroy")
        self.assertIn("No building found", self.output(cmd))

    def test_destroy_by_name_deletes_that_instance(self):
        cmd = _building_cmd(self.caller, " destroy Extractor")
        self.assertTrue(self.ex._deleted)
        self.assertFalse(self.hq._deleted)
        self.assertIn("Destroyed", self.output(cmd))


class TestBuildingOpenExtraVerb(BuildingRouterTestCase):
    """The `open` extra verb survives on the router subclass (R1.6)."""

    def test_open_and_close_toggle_the_tile_building(self):
        cmd = _building_cmd(self.caller, " open close")
        self.assertFalse(self.hq.attributes.get("open"))
        self.assertIn("closed", self.output(cmd).lower())

        caller = BuildingCaller(location=self.room)
        cmd = _building_cmd(caller, " open")
        self.assertTrue(self.hq.attributes.get("open"))
        self.assertIn("open", self.output(cmd).lower())


class TestBuildingSpawn(BuildingRouterTestCase):
    """`spawn` resolves defs through resolve_building; unresolved tokens
    error and create nothing (Requirement 4.7)."""

    def test_unresolved_def_token_errors_and_creates_nothing(self):
        cmd = _building_cmd(self.caller, " spawn bogus")
        out = self.output(cmd)
        self.assertIn("No definition found for 'bogus'", out)
        self.assertIn("nothing created", out)

    def test_spawn_no_args_shows_usage(self):
        cmd = _building_cmd(self.caller, " spawn")
        self.assertIn("Usage", self.output(cmd))

    def test_bad_level_kwarg_reports_through_the_creation_path(self):
        cmd = _building_cmd(self.caller, " spawn HQ level=high")
        self.assertIn("level must be a number", self.output(cmd))


# ================================================================== #
#  NEW @tech router (unified-admin-crud task 5.3)
#
#  CmdAdminTech is an EntityAdminRouter subclass driven by the real
#  TechnologyAdapter over the REAL TechLabSystem (single writer for
#  researched-tech state) with an injected fake DataRegistry. Covers
#  the surface task 5.3 names:
#
#  - grant/revoke round-trip through the research path with the
#    derived-bonus recompute landing before the response
#    (Requirements 7.7, 7.8)
#  - double-grant / absent-revoke errors stating the player's current
#    grant state, no state change (Requirement 7.9)
#  - instance `set` opted out with the no-per-instance-fields reason
#    (Requirement 7.1)
#  - `list` of the technologies granted to the trailing [player]
#    (default caller) (Requirement 7.1)
#  - the full def scope served from the technologies domain
# ================================================================== #

from mygame.commands.admin_commands import CmdAdminTech  # noqa: E402
from world.admin.adapters.tech_adapter import TechnologyAdapter  # noqa: E402
from world.definitions import TechnologyDef  # noqa: E402
from world.event_bus import EventBus  # noqa: E402
from world.systems.tech_system import TechLabSystem  # noqa: E402


def _tech_def(key, name, effect_value=None, rank="Private"):
    return TechnologyDef(
        name=name, key=key, required_rank=rank,
        resource_cost={"Wood": 10}, research_ticks=5,
        effect_type="stat_bonus" if effect_value else "",
        effect_value=effect_value,
    )


class FakeTechDataRegistry:
    """DataRegistry double: ``technologies`` dict + ``resolve_technology``."""

    def __init__(self, defs):
        self.technologies = {d.key: d for d in defs}

    def resolve_technology(self, token):
        t = token.strip().lower()
        for d in self.technologies.values():
            if d.key.lower() == t or d.name.lower() == t:
                return d
        matches = [
            d for d in self.technologies.values()
            if d.key.lower().startswith(t) or d.name.lower().startswith(t)
        ]
        return matches[0] if len(matches) == 1 else None


class _TechDb:
    """Attribute-bag db proxy for a tech-holding player."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, key):
        return None


class TechCaller(RouterCaller):
    """Caller stub for @tech: granted techs, perm hierarchy, player search."""

    def __init__(self, key="TestAdmin", perm_level="Builder",
                 researched=(), known_players=()):
        super().__init__(tier=perm_level, key=key)
        self.db = _TechDb(researched_techs=set(researched), tech_bonuses={})
        # RouterCaller.search falls back to a case-insensitive lookup, which
        # is what this caller's own ``_known`` map did explicitly.
        self.search_results.update({p.key: p for p in known_players})


class TechRouterUnderTest(CmdAdminTech):
    """CmdAdminTech with the registry/overlay test hooks injected."""

    registry = None          # per-test AdapterRegistry
    overlay = FakeOverlay()  # keeps def-scope reads off disk

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.overlay


_TECH_DEFS = (
    _tech_def("drone_swarm", "Drone Swarm", effect_value={"damage": 5}),
    _tech_def("nano_armor", "Nano Armor",
              effect_value={"damage_reduction": 2}),
)


def _tech_cmd(caller, args, tech_system=None):
    """Build + run one @tech invocation against an injected registry.

    The adapter drives the REAL TechLabSystem over a fake DataRegistry,
    so grants/revokes exercise the actual single-writer research path.
    """
    data_registry = FakeTechDataRegistry(_TECH_DEFS)
    system = tech_system or TechLabSystem(
        registry=data_registry, event_bus=EventBus()
    )
    adapter = TechnologyAdapter(registry=data_registry, tech_system=system)
    registry = AdapterRegistry()
    registry.register(adapter)
    cmd = TechRouterUnderTest()
    cmd.registry = registry
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    cmd.func()
    return cmd


class TechRouterTestCase(RouterTestCase):
    """Shared plumbing: fresh List_Cache per test + output helper."""

    def setUp(self):
        super().setUp()
        self.caller = TechCaller()



class TestTechGrantRevokeRoundTrip(TechRouterTestCase):
    """grant → spawn / revoke → destroy through the research path with
    the derived-bonus recompute (Requirements 7.1, 7.7, 7.8)."""

    def test_grant_adds_and_recomputes_bonuses_before_the_response(self):
        cmd = _tech_cmd(self.caller, " grant drone_swarm")
        out = self.output(cmd)
        self.assertIn("Drone Swarm (drone_swarm)", out)
        self.assertIn("drone_swarm", self.caller.db.researched_techs)
        # Derived bonuses recomputed BEFORE the success response (R7.7).
        self.assertEqual(self.caller.db.tech_bonuses, {"damage": 5.0})

    def test_revoke_removes_and_recomputes_bonuses(self):
        self.caller.db.researched_techs = {"drone_swarm", "nano_armor"}
        cmd = _tech_cmd(self.caller, " revoke drone_swarm")
        out = self.output(cmd)
        self.assertIn("Destroyed", out)
        self.assertIn("Drone Swarm (drone_swarm)", out)
        self.assertEqual(self.caller.db.researched_techs, {"nano_armor"})
        # Derived bonuses recomputed BEFORE the response (R7.8).
        self.assertEqual(self.caller.db.tech_bonuses,
                         {"damage_reduction": 2.0})

    def test_grant_then_revoke_restores_the_prior_state(self):
        _tech_cmd(self.caller, " grant nano_armor")
        self.assertEqual(self.caller.db.tech_bonuses,
                         {"damage_reduction": 2.0})
        _tech_cmd(self.caller, " revoke nano_armor")
        self.assertEqual(self.caller.db.researched_techs, set())
        self.assertEqual(self.caller.db.tech_bonuses, {})

    def test_grant_trailing_player_targets_that_player(self):
        bob = TechCaller(key="Bob")
        caller = TechCaller(known_players=(bob,))
        _tech_cmd(caller, " grant drone_swarm Bob")
        self.assertIn("drone_swarm", bob.db.researched_techs)
        self.assertEqual(bob.db.tech_bonuses, {"damage": 5.0})
        self.assertEqual(caller.db.researched_techs, set())

    def test_revoke_trailing_player_targets_that_player(self):
        bob = TechCaller(key="Bob", researched=("drone_swarm",))
        caller = TechCaller(known_players=(bob,))
        cmd = _tech_cmd(caller, " revoke drone_swarm Bob")
        self.assertIn("Destroyed", self.output(cmd))
        self.assertEqual(bob.db.researched_techs, set())

    def test_spawn_and_destroy_canonical_spellings_work_too(self):
        _tech_cmd(self.caller, " spawn drone_swarm")
        self.assertIn("drone_swarm", self.caller.db.researched_techs)
        _tech_cmd(self.caller, " destroy drone_swarm")
        self.assertEqual(self.caller.db.researched_techs, set())


class TestTechGrantStateErrors(TechRouterTestCase):
    """Double-grant / absent-revoke → error stating the player's current
    grant state, no state change (Requirement 7.9)."""

    def test_double_grant_states_granted_and_changes_nothing(self):
        _tech_cmd(self.caller, " grant drone_swarm")
        bonuses_before = dict(self.caller.db.tech_bonuses)
        self.caller._messages.clear()
        cmd = _tech_cmd(self.caller, " grant drone_swarm")
        out = self.output(cmd)
        self.assertIn("already holds", out)
        self.assertIn("granted", out)
        self.assertEqual(self.caller.db.researched_techs, {"drone_swarm"})
        self.assertEqual(self.caller.db.tech_bonuses, bonuses_before)

    def test_absent_revoke_states_not_granted_and_changes_nothing(self):
        cmd = _tech_cmd(self.caller, " revoke drone_swarm")
        out = self.output(cmd)
        self.assertIn("does not hold", out)
        self.assertIn("not granted", out)
        self.assertIn("Nothing was destroyed", out)
        self.assertEqual(self.caller.db.researched_techs, set())


class TestTechSetOptOut(TechRouterTestCase):
    """Instance `set` opted out with the declared reason (R1.5, 7.1)."""

    def test_set_surfaces_the_no_per_instance_fields_reason(self):
        self.caller.db.researched_techs = {"drone_swarm"}
        cmd = _tech_cmd(self.caller, " set drone_swarm damage 9")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn("no modifiable per-instance fields", out)
        # Pointer to the supported paths.
        self.assertIn("grant", out)


class TestTechListAndShow(TechRouterTestCase):
    """`list` of the trailing [player]'s granted techs (default caller)
    and the granted-tech `show` readout (Requirement 7.1)."""

    def test_list_shows_the_callers_granted_techs_indexed(self):
        self.caller.db.researched_techs = {"drone_swarm", "nano_armor"}
        cmd = _tech_cmd(self.caller, " list")
        out = self.output(cmd)
        self.assertIn("#1", out)
        self.assertIn("#2", out)
        self.assertIn("Drone Swarm", out)
        self.assertIn("Nano Armor", out)

    def test_list_trailing_player_scopes_to_that_player(self):
        bob = TechCaller(key="Bob", researched=("nano_armor",))
        caller = TechCaller(known_players=(bob,))
        cmd = _tech_cmd(caller, " list Bob")
        out = self.output(cmd)
        self.assertIn("Nano Armor", out)
        self.assertNotIn("Drone Swarm", out)

    def test_empty_list_reports_no_instances(self):
        cmd = _tech_cmd(self.caller, " list")
        self.assertIn("No tech instances", self.output(cmd))

    def test_show_renders_the_granted_tech_readout(self):
        self.caller.db.researched_techs = {"drone_swarm"}
        cmd = _tech_cmd(self.caller, " show drone_swarm")
        out = self.output(cmd)
        self.assertIn("Drone Swarm (drone_swarm)", out)
        self.assertIn("granted to TestAdmin", out)
        self.assertIn("Private", out)      # def-backed rank info
        self.assertIn("stat_bonus", out)   # def-backed effect info

    def test_show_resolves_by_index_from_the_list_cache(self):
        self.caller.db.researched_techs = {"drone_swarm", "nano_armor"}
        _tech_cmd(self.caller, " list")
        cmd = _tech_cmd(self.caller, " show #2")
        self.assertIn("Nano Armor (nano_armor)", self.output(cmd))


class TestTechDefScope(TechRouterTestCase):
    """The full def scope is live on @tech, served from the
    technologies domain (Requirement 7.1)."""

    def test_def_list_serves_the_technology_definitions(self):
        cmd = _tech_cmd(self.caller, " def list")
        out = self.output(cmd)
        self.assertIn("drone_swarm", out)
        self.assertIn("nano_armor", out)

    def test_def_show_renders_the_merged_definition(self):
        cmd = _tech_cmd(self.caller, " def show drone_swarm")
        out = self.output(cmd)
        self.assertIn("drone_swarm", out)
        self.assertIn("required_rank", out)
        self.assertIn("Private", out)

    def test_def_diff_empty_overlay_reports_no_overrides(self):
        cmd = _tech_cmd(self.caller, " def diff")
        self.assertIn("No definition overrides", self.output(cmd))

    def test_def_set_requires_admin(self):
        cmd = _tech_cmd(self.caller, " def set drone_swarm research_ticks 9")
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def set")


# ================================================================== #
#  Phase 2 router unit tests (unified-admin-crud task 5.4) — AUDIT
#
#  Task 5.4's required coverage for this file already exists verbatim
#  from tasks 5.1/5.3; nothing is duplicated here:
#
#  - `@building set <target> level 9` clamps to 5 with the bounds note
#    through the ROUTER: TestBuildingSet
#    .test_set_level_above_max_clamps_to_five_with_note (R7.2, 3.2)
#  - `@building set <target> level 0` clamps to 1 with the bounds note
#    through the ROUTER: TestBuildingSet
#    .test_set_level_below_min_clamps_to_one_with_note (R7.2, 3.2)
#  - `@tech grant` → `revoke` round-trip through the router with the
#    tech_bonuses recompute observed before the response:
#    TestTechGrantRevokeRoundTrip (R7.1, 7.7, 7.8)
#  - double-grant / absent-revoke errors stating the player's current
#    grant state, no state change: TestTechGrantStateErrors (R7.9)
#
#  The @agent gap-fill (create/spawn full equivalence, verbatim
#  def-scope opt-out reason) lives in the task-5.4 section of
#  commands/tests/test_agent_router.py (R7.3, 11.1, 11.2).
# ================================================================== #


# ================================================================== #
#  Migrated @outpost router (unified-admin-crud task 7.1)
#
#  CmdAdminOutpost is now an EntityAdminRouter subclass driven by the
#  real OutpostAdapter with injected spawner/registry doubles. Covers
#  the migration wiring the task names (Requirements 11.5, 11.6):
#
#  - `list` KEEPS its instance meaning (active NPC bases, #N-indexed
#    rows — no def-list pointer: nothing moved)
#  - `tiers` → `def list` Migration_Alias: deprecation note naming both
#    spellings + the legacy [N]-indexed tier rendering preserved
#  - NEW `show`/`set`/`destroy` through the outpost spawner paths
#    (disturbed_at stamp; wipe_bases_in_area unit-wipe; bulk destroy
#    confirmation-gated)
#  - `def set`/`def reset` opted out with the outposts.yaml reason
#    (templates load outside the overlay merge)
#  - legacy `spawn <tier> [x y]` grammar preserved on the subclass
# ================================================================== #

from mygame.commands.admin_commands import CmdAdminOutpost  # noqa: E402
from world import services  # noqa: E402
from world.admin.adapters.outpost_adapter import OutpostAdapter  # noqa: E402
from world.definitions import BaseTemplateDef  # noqa: E402


class _OAttrs:
    """Evennia attributes-handler stand-in."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class OutpostSentinel:
    """Sentinel owner stand-in: key + attributes handler."""

    def __init__(self, key):
        self.key = key
        self.attributes = _OAttrs()


class FakeOutpostSpawner:
    """OutpostSpawnerSystem double: tracking records + spawner paths."""

    def __init__(self, bases=None, spawn_result="ok"):
        self._active_bases = dict(bases or {})
        self.spawn_calls = []

    def spawn_base(self, planet, tier, coords=None):
        self.spawn_calls.append((planet, tier, coords))
        x, y = coords if coords else (7, 7)
        rec = {"tier": tier, "planet": planet, "x": x, "y": y,
               "disturbed_at": 0}
        self._active_bases[len(self._active_bases)] = rec
        return rec

    def wipe_bases_in_area(self, planet, x1, y1, x2, y2):
        victims = [
            key for key, rec in self._active_bases.items()
            if rec.get("planet") == planet
            and x1 <= int(rec["x"]) <= x2 and y1 <= int(rec["y"]) <= y2
        ]
        for key in victims:
            self._active_bases.pop(key)
        return len(victims)


class FakeOutpostTemplateRegistry:
    """DataRegistry double carrying ``base_templates``."""

    def __init__(self, tiers=("fortress", "outpost")):
        self.base_templates = {
            t: BaseTemplateDef(tier=t, display_name=t.title())
            for t in tiers
        }


class _OutpostDb:
    """Minimal db proxy carrying the caller's tile + planet."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, key):
        return None


class OutpostCaller(RouterCaller):
    """Caller stub for @outpost: coords, planet, perm hierarchy."""

    def __init__(self, perm_level="Builder", coords=(3, 4),
                 planet="earth"):
        super().__init__(tier=perm_level)
        self.db = _OutpostDb(coord_x=coords[0], coord_y=coords[1],
                             coord_planet=planet)


class OutpostRouterUnderTest(CmdAdminOutpost):
    """CmdAdminOutpost with the registry/overlay test hooks injected."""

    registry = None          # per-test AdapterRegistry
    overlay = FakeOverlay()  # keeps def-scope reads off disk

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.overlay


def _outpost_cmd(caller, args, spawner, tiers=("fortress", "outpost")):
    """Build + run one @outpost invocation against injected doubles."""
    adapter = OutpostAdapter(
        registry=FakeOutpostTemplateRegistry(tiers), spawner=spawner)
    registry = AdapterRegistry()
    registry.register(adapter)
    cmd = OutpostRouterUnderTest()
    cmd.registry = registry
    cmd.caller = caller
    cmd.args = args
    cmd.cmdstring = cmd.key
    cmd.func()
    return cmd


class OutpostRouterTestCase(RouterTestCase):
    """Shared plumbing: fresh List_Cache per test + output helper."""

    def setUp(self):
        super().setUp()
        self.s1 = OutpostSentinel("Outpost #1")
        self.s2 = OutpostSentinel("Fortress #1")
        self.spawner = FakeOutpostSpawner(bases={
            101: {"sentinel": self.s1, "tier": "outpost",
                  "planet": "earth", "x": 5, "y": 6, "disturbed_at": 0},
            102: {"sentinel": self.s2, "tier": "fortress",
                  "planet": "earth", "x": 20, "y": 30, "disturbed_at": 44},
        })
        self.caller = OutpostCaller()

    def run_cmd(self, args, caller=None):
        return _outpost_cmd(caller or self.caller, args, self.spawner)



class TestOutpostRouterIdentity(OutpostRouterTestCase):
    """The migration preserves the command key and the Builder lock."""

    def test_key_and_locks_preserved(self):
        self.assertEqual(CmdAdminOutpost.key, "@outpost")
        self.assertIn("perm(Builder)", CmdAdminOutpost.locks)
        self.assertEqual(CmdAdminOutpost.adapter_key, "outpost")


class TestOutpostListKeepsInstanceMeaning(OutpostRouterTestCase):
    """`list` = active NPC bases, unchanged meaning (R4.1, matrix row) —
    unlike @item/@building, NO moved-to-def-list pointer is emitted."""

    def test_list_shows_indexed_base_rows(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("#1", out)
        self.assertIn("#2", out)
        self.assertIn("Outpost #1", out)
        self.assertIn("Fortress #1", out)
        self.assertIn("(5, 6)", out)
        self.assertNotIn("moved to", out)

    def test_empty_list_stores_an_empty_cache(self):
        self.spawner._active_bases.clear()
        cmd = self.run_cmd(" list")
        self.assertIn("No outpost instances", self.output(cmd))


class TestOutpostTiersAlias(OutpostRouterTestCase):
    """`tiers` → `def list` Migration_Alias (R11.1, 11.2, 11.5) with the
    legacy [N]-indexed tier rendering preserved."""

    def test_tiers_emits_deprecation_note_naming_both_spellings(self):
        cmd = self.run_cmd(" tiers")
        note = next(m for m in cmd.caller._messages if "deprecated" in m)
        self.assertIn("tiers", note)
        self.assertIn("def list", note)

    def test_tiers_renders_the_indexed_tier_listing(self):
        cmd = self.run_cmd(" tiers")
        out = self.output(cmd)
        self.assertIn("[1]", out)
        self.assertIn("fortress", out)
        self.assertIn("outpost", out)

    def test_def_list_canonical_spelling_matches(self):
        alias_cmd = self.run_cmd(" tiers")
        direct_cmd = self.run_cmd(" def list", caller=OutpostCaller())
        alias_out = [m for m in alias_cmd.caller._messages
                     if "deprecated" not in m]
        self.assertEqual(alias_out, direct_cmd.caller._messages)


class TestOutpostShow(OutpostRouterTestCase):
    """NEW `show` (uniform readout through the spawner records)."""

    def test_show_renders_base_state_and_modifiable_fields(self):
        cmd = self.run_cmd(" show Fortress #1")
        out = self.output(cmd)
        self.assertIn("Fortress #1", out)
        self.assertIn("fortress", out)
        self.assertIn("(20, 30)", out)
        self.assertIn("since tick 44", out)
        self.assertIn("disturbed_at", out)
        self.assertIn("Modifiable fields", out)

    def test_show_resolves_the_cached_index(self):
        self.run_cmd(" list")
        cmd = self.run_cmd(" show #1")
        self.assertIn("Outpost #1", self.output(cmd))


class TestOutpostSet(OutpostRouterTestCase):
    """NEW `set` — disturbed_at through the spawner's own stamp path."""

    def test_set_writes_the_record_and_the_sentinel_stamp(self):
        # `set` splits on whitespace — spaced names use #N or a prefix.
        self.run_cmd(" list")
        cmd = self.run_cmd(" set #1 disturbed_at 77")
        self.assertFieldSet(cmd, field="disturbed_at", applied=77)
        self.assertEqual(
            self.spawner._active_bases[101]["disturbed_at"], 77)
        self.assertEqual(
            self.s1.attributes.get("base_disturbed_at"), 77)

    def test_set_clamps_negative_values_with_the_bounds_note(self):
        cmd = self.run_cmd(" set Out disturbed_at -9")  # unique prefix
        self.assertClamped(cmd, field="disturbed_at", applied=0, lo=0,
                           requested=-9)
        self.assertEqual(
            self.spawner._active_bases[101]["disturbed_at"], 0)

    def test_set_unknown_field_names_the_valid_fields(self):
        cmd = self.run_cmd(" set Out tier citadel")
        self.assertUnknownField(cmd, field="tier", valid=("disturbed_at",),
                                plane="instance")
        self.assertEqual(
            self.spawner._active_bases[101]["tier"], "outpost")


class TestOutpostDestroy(OutpostRouterTestCase):
    """NEW `destroy` — unit-wipe via the spawner's admin-clear path."""

    def test_destroy_wipes_the_base_through_the_spawner(self):
        self.run_cmd(" list")
        cmd = self.run_cmd(" destroy #1")
        self.assertIn("Destroyed Outpost #1", self.output(cmd))
        self.assertEqual(list(self.spawner._active_bases), [102])

    def test_bulk_destroy_is_confirmation_gated(self):
        self.run_cmd(" list")
        cmd = self.run_cmd(" destroy #1, #2")
        out = self.output(cmd)
        self.assertIn("2", out)
        self.assertIn("confirm", out)
        # Nothing deleted before explicit confirmation (R4.5).
        self.assertEqual(len(self.spawner._active_bases), 2)
        confirm = self.run_cmd(" destroy confirm")
        self.assertIn("Destroyed 2", self.output(confirm))
        self.assertEqual(self.spawner._active_bases, {})


class TestOutpostDefScope(OutpostRouterTestCase):
    """def reads live; def writes opted out with the outposts.yaml
    reason (templates load outside the overlay merge)."""

    def test_def_show_renders_the_template(self):
        cmd = self.run_cmd(" def show fortress")
        out = self.output(cmd)
        self.assertIn("fortress", out)
        self.assertIn("display_name", out)

    def test_def_set_surfaces_the_opt_out_reason_and_changes_nothing(self):
        caller = OutpostCaller(perm_level="Admin")
        cmd = self.run_cmd(" def set fortress loot 5", caller=caller)
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn("outposts.yaml", out)

    def test_def_reset_is_opted_out_too(self):
        caller = OutpostCaller(perm_level="Admin")
        cmd = self.run_cmd(" def reset fortress", caller=caller)
        self.assertIn("not available", self.output(cmd))


class TestOutpostSpawnLegacyGrammar(OutpostRouterTestCase):
    """`spawn <tier> [x y]` keeps its legacy grammar and messages on the
    migrated router (Requirement 11.6; the full legacy matrix lives in
    test_admin_legacy_routers.py)."""

    def test_spawn_with_explicit_coords_reaches_the_spawner(self):
        with services.override({"outpost_spawner": self.spawner}):
            cmd = self.run_cmd(" spawn fort 20 30")
        self.assertEqual(self.spawner.spawn_calls[-1],
                         ("earth", "fortress", (20, 30)))
        self.assertIn("Spawned fortress base", self.output(cmd))

    def test_spawn_unknown_tier_points_at_def_list(self):
        with services.override({"outpost_spawner": self.spawner}):
            cmd = self.run_cmd(" spawn bogus")
        out = self.output(cmd)
        self.assertIn("Unknown or ambiguous tier", out)
        self.assertIn("def list", out)
        self.assertEqual(self.spawner.spawn_calls, [])


# ================================================================== #
#  Migrated @player router (unified-admin-crud task 7.3)
#
#  CmdAdminPlayer is now an EntityAdminRouter subclass driven by the
#  real PlayerAdapter with injected rank-system/registry/player doubles.
#  Covers the migration wiring the task names (Requirements 1.5, 11.5,
#  11.6):
#
#  - NEW `show` + `set` with `level` (int, STATIC bounds 1–100,
#    clamp-with-note through the router) and `rank` (enum over the
#    numeric rank ids — invalid values error listing the valid set,
#    no state change)
#  - legacy `level <N> [player]` / `rank <N> [player]` verb forms as
#    Migration_Aliases of their `set` equivalents: argument reshaping
#    on the router subclass, deprecation note naming both spellings,
#    identical effect to the canonical spelling, caller default when
#    [player] is omitted
#  - `spawn`/`destroy`/def scope opted out with their reasons (players
#    register; pointer to the '@obliterate' flow; no YAML defs)
#  - `set` Admin-gated via verb_perms (the legacy tier — R8.7)
# ================================================================== #

from mygame.commands.admin_commands import CmdAdminPlayer  # noqa: E402
from world.admin.adapters.player_adapter import PlayerAdapter  # noqa: E402
from world.constants import NUM_RANKS  # noqa: E402
from world.systems.rank_system import level_range_for_rank  # noqa: E402


class _PDb:
    """Attribute-bag double for a player's ``db``."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):  # unset fields read as None
        return None


class PlayerCaller(RouterCaller):
    """Caller stub for @player: itself a live player (db + search)."""

    def __init__(self, perms=("Builder",), key="TestAdmin"):
        super().__init__(perms=perms, key=key)
        self.db = _PDb(level=1, rank_level=1, combat_xp=0)

    def search(self, name, **kwargs):
        return None  # candidates come from the injected provider


class FakePlayerTarget:
    """A live player character the adapter enumerates."""

    def __init__(self, key, level=1, rank_level=1, combat_xp=0):
        self.id = next_entity_id()
        self.key = key
        self.db = _PDb(level=level, rank_level=rank_level,
                       combat_xp=combat_xp)


class FakePlayerRankSystem:
    """RankSystem double exposing the progression single-writer hooks."""

    def __init__(self):
        self.promotions = []

    def xp_for_level(self, level):
        return level * 100

    def check_promotion(self, player):
        self.promotions.append(player)


class PlayerRouterUnderTest(CmdAdminPlayer):
    """CmdAdminPlayer with the registry test hook injected."""

    registry = None  # per-test AdapterRegistry

    def _adapter_registry(self):
        return self.registry


class PlayerRouterTestCase(RouterTestCase):
    """Shared plumbing: fresh List_Cache/adapter per test + helpers."""

    def setUp(self):
        super().setUp()
        self.bob = FakePlayerTarget("Bob", level=5, rank_level=1)
        self.rank_system = FakePlayerRankSystem()
        self.caller = PlayerCaller(perms=("Builder", "Admin"))
        self.adapter = PlayerAdapter(
            rank_system=self.rank_system,
            registry=object(),  # rank names fall back to "Rank N"
            players_provider=lambda: [self.bob, self.caller],
        )
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)

    def run_cmd(self, args, caller=None):
        cmd = PlayerRouterUnderTest()
        cmd.registry = self.registry
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.cmdstring = cmd.key
        cmd.func()
        return cmd



class TestPlayerRouterIdentity(PlayerRouterTestCase):
    """The migration preserves the command key and the Builder lock."""

    def test_key_and_locks_preserved(self):
        self.assertEqual(CmdAdminPlayer.key, "@player")
        self.assertIn("perm(Builder)", CmdAdminPlayer.locks)
        self.assertEqual(CmdAdminPlayer.adapter_key, "player")


class TestPlayerSetLevelClamp(PlayerRouterTestCase):
    """`set <target> level` clamps at the STATIC 1–100 bounds with the
    bounds note (Requirements 3.2, D2; task 7.6 coverage seed)."""

    def test_set_level_above_max_clamps_to_100_with_note(self):
        cmd = self.run_cmd(" set Bob level 150")
        self.assertClamped(cmd, field="level", applied=100, lo=1, hi=100,
                           requested=150)
        self.assertEqual(self.bob.db.level, 100)

    def test_set_level_below_min_clamps_to_1_with_note(self):
        cmd = self.run_cmd(" set Bob level 0")
        self.assertClamped(cmd, field="level", applied=1, lo=1, hi=100,
                           requested=0)
        self.assertEqual(self.bob.db.level, 1)

    def test_in_bounds_level_applies_unchanged_through_the_system(self):
        cmd = self.run_cmd(" set Bob level 42")
        self.assertNotClamped(cmd, field="level", applied=42, target="Bob")
        self.assertEqual(self.bob.db.level, 42)
        # XP re-stamped + rank recompute through the injected system.
        self.assertEqual(self.bob.db.combat_xp, 4200)
        self.assertEqual(self.rank_system.promotions, [self.bob])


class TestPlayerSetRankEnum(PlayerRouterTestCase):
    """`set <target> rank` is an enum write: invalid ids error listing
    the valid values with NO state change (Requirement 3.9)."""

    def test_rank_outside_the_enum_errors_listing_valid_values(self):
        cmd = self.run_cmd(f" set Bob rank {NUM_RANKS + 1}")
        out = self.output(cmd)
        self.assertIn("not a valid value", out)
        self.assertIn("valid values", out)
        self.assertIn(str(NUM_RANKS), out)
        self.assertEqual(self.bob.db.rank_level, 1)  # unchanged
        self.assertEqual(self.rank_system.promotions, [])

    def test_valid_rank_jumps_to_the_bands_first_level(self):
        cmd = self.run_cmd(" set Bob rank 3")
        # ``rank`` is a str-valued enum spec (_RANK_ENUM_VALUES); the adapter
        # coerces to int on the way to db.rank_level, asserted below.
        self.assertFieldSet(cmd, field="rank", applied="3", target="Bob")
        self.assertEqual(self.bob.db.rank_level, 3)
        self.assertEqual(self.bob.db.level, level_range_for_rank(3)[0])


class TestPlayerLegacyAliases(PlayerRouterTestCase):
    """The `level`/`rank` Migration_Aliases: deprecation note naming
    both spellings + identical effect to the canonical `set` spelling
    (Requirements 11.1, 11.2, 11.5)."""

    def test_level_alias_emits_the_deprecation_note(self):
        cmd = self.run_cmd(" level 9 Bob")
        note = next(m for m in cmd.caller.messages if "deprecated" in m)
        self.assertIn("level", note)
        self.assertIn("set", note)

    def test_level_alias_matches_the_canonical_spelling(self):
        alias_cmd = self.run_cmd(" level 9 Bob")
        alias_out = [m for m in alias_cmd.caller.messages
                     if "deprecated" not in m]
        other = FakePlayerTarget("Bob", level=5, rank_level=1)
        self.adapter = PlayerAdapter(
            rank_system=self.rank_system, registry=object(),
            players_provider=lambda: [other],
        )
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)
        direct_cmd = self.run_cmd(
            " set Bob level 9", caller=PlayerCaller(perms=("Builder",
                                                           "Admin")))
        self.assertEqual(alias_out, direct_cmd.caller.messages)
        self.assertEqual(self.bob.db.level, other.db.level)
        self.assertEqual(self.bob.db.rank_level, other.db.rank_level)

    def test_rank_alias_reshapes_and_writes(self):
        cmd = self.run_cmd(" rank 2 Bob")
        self.assertIn("deprecated", self.output(cmd))
        self.assertFieldSet(cmd, field="rank", applied="2", target="Bob")
        self.assertEqual(self.bob.db.rank_level, 2)

    def test_alias_defaults_the_target_to_the_caller(self):
        # Legacy `@player level <N>` (no player) targeted the caller.
        cmd = self.run_cmd(" level 7")
        self.assertFieldSet(cmd, field="level", applied=7,
                            target=self.caller.key)
        self.assertEqual(self.caller.db.level, 7)

    def test_alias_without_a_value_shows_usage(self):
        cmd = self.run_cmd(" level")
        self.assertIn("Usage", self.output(cmd))
        self.assertEqual(self.caller.db.level, 1)  # unchanged

    def test_alias_hits_the_canonical_admin_gate(self):
        # Requirement 11.1: identical permission outcome to `set`.
        builder = PlayerCaller(perms=("Builder",))
        cmd = self.run_cmd(" level 9 Bob", caller=builder)
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="set")
        self.assertEqual(self.bob.db.level, 5)  # unchanged


class TestPlayerSetPermission(PlayerRouterTestCase):
    """`set` keeps the legacy Admin tier via verb_perms (R8.7)."""

    def test_set_denied_for_builder(self):
        builder = PlayerCaller(perms=("Builder",))
        cmd = self.run_cmd(" set Bob level 9", caller=builder)
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="set")
        self.assertEqual(self.bob.db.level, 5)  # unchanged

    def test_show_allowed_for_builder(self):
        builder = PlayerCaller(perms=("Builder",))
        cmd = self.run_cmd(" show Bob", caller=builder)
        self.assertNotIn("Permission denied", self.output(cmd))


class TestPlayerOptOuts(PlayerRouterTestCase):
    """Opted-out verbs surface their declared reasons verbatim with no
    state change (Requirement 1.5)."""

    def test_spawn_opt_out_names_registration(self):
        cmd = self.run_cmd(" spawn scout")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(self.adapter.opt_outs["spawn"], out)

    def test_destroy_opt_out_points_at_obliterate(self):
        cmd = self.run_cmd(" destroy Bob")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn("@obliterate", out)
        self.assertEqual(self.bob.db.level, 5)  # unchanged

    def test_def_scope_opt_out_names_the_missing_domain(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn("no YAML definition domain", out)


class TestPlayerListAndShow(PlayerRouterTestCase):
    """NEW `show` + `list` (live players, #N-indexed rows)."""

    def test_list_shows_indexed_player_rows(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("#1", out)
        self.assertIn("Bob", out)
        self.assertIn("level 5", out)

    def test_show_renders_progression_and_modifiable_fields(self):
        cmd = self.run_cmd(" show Bob")
        out = self.output(cmd)
        self.assertIn("Bob", out)
        self.assertIn("Level: 5", out)
        self.assertIn("Modifiable fields", out)
        self.assertIn("level", out)
        self.assertIn("[1–100]", out)
        self.assertIn("rank", out)

    def test_show_resolves_the_cached_index(self):
        self.run_cmd(" list")
        cmd = self.run_cmd(" show #1")
        self.assertIn("Bob", self.output(cmd))


# ================================================================== #
#  Migrated @resource router (unified-admin-crud task 7.5/7.6)
#
#  CmdAdminResource is now an EntityAdminRouter subclass driven by the
#  real ResourceAdapter. @resource manages per-player resource BALANCES
#  (not a spawnable instance collection), so its grammar row is unusual.
#  Covers the migration wiring the task names (Requirements 1.5, 1.6,
#  11.5, 11.6):
#
#  - `spawn` is the grant path (positional `<type|all> <amount>
#    [player]`), preserved on the subclass via `_sub_spawn` (like
#    @outpost); `give` is its Migration_Alias — deprecation note naming
#    both spellings + byte-identical effect/output to canonical `spawn`
#  - NEW `show` (balances readout, target defaults to you) + NEW `set`
#    (absolute balance write, each resource an int Field_Spec floored at
#    0 with no upper cap → clamp-below-0-to-0 with the bounds note)
#  - `reset` extra verb (Admin-gated via verb_perms) restores one
#    player to STARTING_RESOURCES; `spawn`/`set` stay at the Builder
#    floor (the legacy `give` tier)
#  - `list`/`destroy` + the whole `def` scope opted out with reasons
#    pointing at the supported path (no listable roster, no YAML defs)
# ================================================================== #

from mygame.commands.admin_commands import CmdAdminResource  # noqa: E402
from world.admin.adapters.resource_adapter import ResourceAdapter  # noqa: E402
from world.constants import RESOURCE_TYPES  # noqa: E402


class _ResAttrs:
    """Evennia attributes-handler stand-in (the `reset` write target)."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class FakeResourceTarget:
    """A player whose balances @resource reads/writes.

    Carries both the ``get_resource``/``add_resource`` single-writer API
    (the adapter's preferred path) and an ``attributes`` handler (the
    legacy ``reset`` bulk-write target).
    """

    def __init__(self, key, **balances):
        self.id = next_entity_id()
        self.key = key
        self._resources = {r: 0 for r in RESOURCE_TYPES}
        self._resources.update(balances)
        self.attributes = _ResAttrs()
        self.messages = []

    def msg(self, text, **kwargs):
        self.messages.append(text)

    def get_resource(self, resource):
        return self._resources.get(resource, 0)

    def add_resource(self, resource, amount):
        self._resources[resource] = (
            self._resources.get(resource, 0) + amount
        )


class ResourceCaller(RouterCaller):
    """Caller stub for @resource: itself a balance holder (grants/sets to
    ``me`` land here) with ``search`` + a configurable permission set."""

    def __init__(self, perms=("Builder", "Admin"), key="TestAdmin",
                 **balances):
        super().__init__(perms=perms, key=key)
        self._resources = {r: 0 for r in RESOURCE_TYPES}
        self._resources.update(balances)

    def get_resource(self, resource):
        return self._resources.get(resource, 0)

    def add_resource(self, resource, amount):
        self._resources[resource] = (
            self._resources.get(resource, 0) + amount
        )


class ResourceRouterUnderTest(CmdAdminResource):
    """CmdAdminResource with the registry test hook injected."""

    registry = None  # per-test AdapterRegistry

    def _adapter_registry(self):
        return self.registry


class ResourceRouterTestCase(RouterTestCase):
    """Shared plumbing: fresh List_Cache/adapter per test + helpers."""

    def setUp(self):
        super().setUp()
        self.bob = FakeResourceTarget("Bob", Wood=40, Stone=25, Iron=10)
        self.caller = ResourceCaller(perms=("Builder", "Admin"))
        self.caller._search_results["Bob"] = self.bob
        self.adapter = ResourceAdapter()
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)

    def run_cmd(self, args, caller=None):
        cmd = ResourceRouterUnderTest()
        cmd.registry = self.registry
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.cmdstring = cmd.key
        cmd.func()
        return cmd



class TestResourceRouterIdentity(ResourceRouterTestCase):
    """The migration preserves the command key and the Builder lock."""

    def test_key_and_locks_preserved(self):
        self.assertEqual(CmdAdminResource.key, "@resource")
        self.assertIn("perm(Builder)", CmdAdminResource.locks)
        self.assertEqual(CmdAdminResource.adapter_key, "resource")


class TestResourceGiveAlias(ResourceRouterTestCase):
    """The `give` Migration_Alias: deprecation note naming both spellings
    + byte-identical effect/output to the canonical `spawn` grant
    (Requirements 11.1, 11.2, 11.5)."""

    def test_give_emits_the_deprecation_note(self):
        cmd = self.run_cmd(" give Wood 50 Bob")
        note = next(m for m in cmd.caller.messages if "deprecated" in m)
        self.assertIn("give", note)
        self.assertIn("spawn", note)

    def test_give_matches_the_canonical_spawn_spelling(self):
        alias_cmd = self.run_cmd(" give Wood 50 Bob")
        alias_out = [m for m in alias_cmd.caller.messages
                     if "deprecated" not in m]

        other_bob = FakeResourceTarget("Bob", Wood=40, Stone=25, Iron=10)
        fresh_caller = ResourceCaller(perms=("Builder", "Admin"))
        fresh_caller._search_results["Bob"] = other_bob
        direct_cmd = self.run_cmd(" spawn Wood 50 Bob", caller=fresh_caller)

        # Same output (minus the note) and same balance effect.
        self.assertEqual(alias_out, direct_cmd.caller.messages)
        self.assertEqual(self.bob.get_resource("Wood"),
                         other_bob.get_resource("Wood"))


class TestResourceSpawnGrant(ResourceRouterTestCase):
    """`spawn <type|all> <amount> [player]` credits additively through
    the target's add_resource single-writer (Requirement 11.6)."""

    def test_spawn_grants_to_named_player_and_notifies_them(self):
        cmd = self.run_cmd(" spawn Wood 50 Bob")
        self.assertIn("Gave 50 Wood to Bob", self.output(cmd))
        self.assertEqual(self.bob.get_resource("Wood"), 90)  # 40 + 50
        self.assertTrue(any("received" in m.lower()
                            for m in self.bob.messages))

    def test_spawn_defaults_the_target_to_the_caller(self):
        cmd = self.run_cmd(" spawn Wood 30")
        self.assertIn("Gave 30 Wood to TestAdmin", self.output(cmd))
        self.assertEqual(self.caller.get_resource("Wood"), 30)

    def test_spawn_unknown_resource_is_rejected_not_minted(self):
        cmd = self.run_cmd(" spawn bogus 50 Bob")
        out = self.output(cmd)
        self.assertIn("Unknown resource 'bogus'", out)
        self.assertNotIn("bogus", self.bob._resources)
        self.assertNotIn("Bogus", self.bob._resources)


class TestResourceSetBalance(ResourceRouterTestCase):
    """NEW `set <player> <type> <amount>`: an absolute balance write,
    floored at 0 with no upper cap (Requirements 3.2, 3.3, D2)."""

    def test_set_writes_an_absolute_balance_unclamped(self):
        cmd = self.run_cmd(" set Bob Wood 500")
        self.assertNotClamped(cmd, field="Wood", applied=500, target="Bob")
        self.assertEqual(self.bob.get_resource("Wood"), 500)

    def test_set_below_zero_clamps_to_0_with_the_bounds_note(self):
        cmd = self.run_cmd(" set Bob Wood -5")
        # Floor of 0, no upper cap.
        self.assertClamped(cmd, field="Wood", applied=0, lo=0, hi=None,
                           requested=-5)
        self.assertEqual(self.bob.get_resource("Wood"), 0)

    def test_set_me_targets_the_caller(self):
        cmd = self.run_cmd(" set me Wood 200")
        self.assertFieldSet(cmd, field="Wood", applied=200,
                            target=self.caller.key)
        self.assertEqual(self.caller.get_resource("Wood"), 200)

    def test_set_unknown_field_errors_with_no_state_change(self):
        cmd = self.run_cmd(" set Bob Gold 5")
        self.assertUnknownField(cmd, field="Gold", valid=("Wood", "Iron"),
                                plane="instance")
        self.assertEqual(self.bob.get_resource("Wood"), 40)  # untouched


class TestResourceShowBalances(ResourceRouterTestCase):
    """NEW `show [player]`: a balances readout defaulting to the caller,
    every resource a modifiable field bounded `[0–]`."""

    def test_show_defaults_to_the_caller_and_lists_balances(self):
        self.caller.add_resource("Wood", 15)
        cmd = self.run_cmd(" show")
        out = self.output(cmd)
        self.assertIn("TestAdmin — resource balances", out)
        self.assertIn("Wood 15", out)
        self.assertIn("Modifiable fields", out)
        self.assertIn("[0–]", out)  # floored at 0, no upper cap

    def test_show_named_player_reads_their_balances(self):
        cmd = self.run_cmd(" show Bob")
        out = self.output(cmd)
        self.assertIn("Bob — resource balances", out)
        self.assertIn("Wood 40", out)


class TestResourceReset(ResourceRouterTestCase):
    """`reset [player]` extra verb (Admin-gated) restores one player to
    STARTING_RESOURCES (Requirement 1.6)."""

    def test_reset_single_player_restores_starting_resources(self):
        self.bob.add_resource("Wood", 9999)
        cmd = self.run_cmd(" reset Bob")
        from typeclasses.characters import STARTING_RESOURCES
        self.assertEqual(self.bob.attributes.get("resources"),
                         dict(STARTING_RESOURCES))
        self.assertIn("Reset Bob to starting resources", self.output(cmd))


class TestResourcePermissions(ResourceRouterTestCase):
    """`spawn`/`set` keep the Builder floor (the legacy `give` tier);
    `reset` is Admin-gated via verb_perms (Requirements 8.7)."""

    def _builder(self):
        builder = ResourceCaller(perms=("Builder",))
        builder._search_results["Bob"] = self.bob
        return builder

    def test_reset_denied_for_builder(self):
        cmd = self.run_cmd(" reset Bob", caller=self._builder())
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="reset")

    def test_spawn_allowed_for_builder(self):
        cmd = self.run_cmd(" spawn Wood 10 Bob", caller=self._builder())
        self.assertNotIn("Permission denied", self.output(cmd))

    def test_set_allowed_for_builder(self):
        cmd = self.run_cmd(" set Bob Wood 10", caller=self._builder())
        self.assertNotIn("Permission denied", self.output(cmd))


class TestResourceOptOuts(ResourceRouterTestCase):
    """Opted-out verbs surface their declared reasons verbatim with no
    state change (Requirement 1.5)."""

    def test_list_opt_out_points_at_show(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(self.adapter.opt_outs["list"], out)

    def test_destroy_opt_out_points_at_set_or_reset(self):
        cmd = self.run_cmd(" destroy Bob")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(self.adapter.opt_outs["destroy"], out)
        self.assertEqual(self.bob.get_resource("Wood"), 40)  # unchanged

    def test_def_scope_opt_out_names_the_missing_domain(self):
        cmd = self.run_cmd(" def list")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn("no YAML definition domain", out)


# ================================================================== #
#  Migrated @stat router (unified-admin-crud task 7.4/7.6)
#
#  CmdAdminStat is now an EntityAdminRouter subclass driven by the real
#  StatAdapter. @stat edits the combat/progression fields of ONE named
#  unit (player OR NPC), defaulting to the caller — it manages neither a
#  spawnable collection nor a YAML domain. The task 7.6 coverage here is
#  the WRITE SEMANTICS the shared handler + adapter reproduce
#  (Requirements 3.2, 3.4, 11.1, 11.5, 11.6):
#
#  - `set <target> hp <N>` clamps to the target's OWN hp_max (a dynamic
#    bound, Req 3.4) with the bounds note, and reviving side effect:
#    positive hp on a downed unit clears incapacitated/respawn_timer
#  - `set <target> hp_max <N>` tops a full unit up to the new ceiling
#  - `set <target> combat_xp <N>` re-derives level/rank via the unit's
#    recompute_progression single-writer
#  - the VALUE-first `hp`/`maxhp`/`xp` Migration_Aliases reshape to the
#    TARGET-first canonical `set` (with the maxhp→hp_max / xp→combat_xp
#    field remap), emit the deprecation note, and hit the canonical
#    Admin gate — identical effect to the canonical spelling
#  - `set` is Admin-gated via verb_perms; `show` stays at Builder
#  - caller-default resolution (`me`/`self`/omitted → you)
#  - `list`/`spawn`/`destroy` + the whole `def` scope opted out
# ================================================================== #

from mygame.commands.admin_commands import CmdAdminStat  # noqa: E402
from world.admin.adapters.stat_adapter import StatAdapter  # noqa: E402


class _SDb:
    """Attribute-bag double for a combat unit's ``db`` (unset → None)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return None


class FakeStatUnit:
    """A live combat unit the @stat resolver enumerates (player OR NPC).

    Carries the ``recompute_progression`` single-writer the ``combat_xp``
    write re-derives level/rank through.
    """

    def __init__(self, key, hp=100, hp_max=100, combat_xp=0, level=1,
                 rank_level=1, incapacitated=False, kills=0, deaths=0):
        self.id = next_entity_id()
        self.key = key
        self.db = _SDb(hp=hp, hp_max=hp_max, combat_xp=combat_xp,
                       level=level, rank_level=rank_level,
                       incapacitated=incapacitated, respawn_timer=0,
                       kills=kills, deaths=deaths)
        self.recomputes = 0

    def recompute_progression(self):
        self.recomputes += 1
        # Re-derive level from XP (100/level, mirroring the curve doubles
        # elsewhere in this module) so the side effect is observable.
        self.db.level = max(1, int(self.db.combat_xp) // 100)


class StatCaller(RouterCaller):
    """Caller stub for @stat: itself a live unit (`me`/omitted → here)
    with a configurable permission set. No ``search`` — units come from
    the injected resolver."""

    def __init__(self, perms=("Builder", "Admin"), key="TestAdmin",
                 hp=100, hp_max=100):
        super().__init__(perms=perms, key=key)
        self.db = _SDb(hp=hp, hp_max=hp_max, combat_xp=0, level=1,
                       rank_level=1, incapacitated=False, respawn_timer=0,
                       kills=0, deaths=0)
        self.recomputes = 0

    def recompute_progression(self):
        self.recomputes += 1


class StatRouterUnderTest(CmdAdminStat):
    """CmdAdminStat with the registry test hook injected."""

    registry = None  # per-test AdapterRegistry

    def _adapter_registry(self):
        return self.registry


class StatRouterTestCase(RouterTestCase):
    """Shared plumbing: injected unit resolver + fresh adapter per test."""

    def setUp(self):
        super().setUp()
        self.bob = FakeStatUnit("Bob", hp=100, hp_max=100)
        self._units = [self.bob]
        self.caller = StatCaller(perms=("Builder", "Admin"))
        # Resolver: exact case-insensitive key match over the live units.
        self.adapter = StatAdapter(
            resolver=lambda caller, token: [
                u for u in self._units if u.key.lower() == token.lower()
            ]
        )
        self.registry = AdapterRegistry()
        self.registry.register(self.adapter)

    def run_cmd(self, args, caller=None):
        cmd = StatRouterUnderTest()
        cmd.registry = self.registry
        cmd.caller = caller or self.caller
        cmd.args = args
        cmd.cmdstring = cmd.key
        cmd.func()
        return cmd



class TestStatRouterIdentity(StatRouterTestCase):
    """The migration preserves the command key and the Builder lock."""

    def test_key_and_locks_preserved(self):
        self.assertEqual(CmdAdminStat.key, "@stat")
        self.assertIn("perm(Builder)", CmdAdminStat.locks)
        self.assertEqual(CmdAdminStat.adapter_key, "stat")


class TestStatSetHpDynamicClamp(StatRouterTestCase):
    """`set <target> hp` clamps to the target's OWN hp_max — a dynamic
    bound computed from the target's state (Requirement 3.4)."""

    def test_hp_above_hp_max_clamps_with_the_bounds_note(self):
        cmd = self.run_cmd(" set Bob hp 150")  # Bob hp_max = 100
        # Dynamic bounds derived from Bob's own hp_max.
        self.assertClamped(cmd, field="hp", applied=100, lo=0, hi=100,
                           requested=150)
        self.assertEqual(self.bob.db.hp, 100)

    def test_in_bounds_hp_applies_unchanged(self):
        cmd = self.run_cmd(" set Bob hp 40")
        self.assertNotClamped(cmd, field="hp", applied=40, target="Bob")
        self.assertEqual(self.bob.db.hp, 40)


class TestStatSetSideEffects(StatRouterTestCase):
    """The three legacy write side effects the adapter reproduces."""

    def test_positive_hp_revives_a_downed_unit(self):
        self.bob.db.hp = 0
        self.bob.db.incapacitated = True
        self.bob.db.respawn_timer = 30
        cmd = self.run_cmd(" set Bob hp 50")
        self.assertFieldSet(cmd, field="hp", applied=50, target="Bob")
        self.assertEqual(self.bob.db.hp, 50)
        self.assertFalse(self.bob.db.incapacitated)
        self.assertEqual(self.bob.db.respawn_timer, 0)

    def test_hp_max_tops_a_full_unit_up_to_the_new_ceiling(self):
        # Bob is at full HP (100/100); raising the ceiling tops him up.
        cmd = self.run_cmd(" set Bob hp_max 200")
        self.assertFieldSet(cmd, field="hp_max", applied=200, target="Bob")
        self.assertEqual(self.bob.db.hp_max, 200)
        self.assertEqual(self.bob.db.hp, 200)

    def test_combat_xp_recomputes_progression(self):
        cmd = self.run_cmd(" set Bob combat_xp 500")
        self.assertFieldSet(cmd, field="combat_xp", applied=500,
                            target="Bob")
        self.assertEqual(self.bob.db.combat_xp, 500)
        self.assertEqual(self.bob.recomputes, 1)  # single-writer fired
        self.assertEqual(self.bob.db.level, 5)    # re-derived from XP


class TestStatLegacyAliases(StatRouterTestCase):
    """The VALUE-first `hp`/`maxhp`/`xp` Migration_Aliases: reshape to
    the canonical TARGET-first `set` (+ field remap), deprecation note,
    canonical Admin gate (Requirements 11.1, 11.2, 11.5)."""

    def test_hp_alias_reshapes_writes_and_notes(self):
        cmd = self.run_cmd(" hp 40 Bob")
        self.assertIn("deprecated", self.output(cmd))
        self.assertFieldSet(cmd, field="hp", applied=40, target="Bob")
        self.assertEqual(self.bob.db.hp, 40)

    def test_maxhp_alias_remaps_to_hp_max(self):
        cmd = self.run_cmd(" maxhp 250 Bob")
        self.assertIn("deprecated", self.output(cmd))
        self.assertFieldSet(cmd, field="hp_max", applied=250, target="Bob")
        self.assertEqual(self.bob.db.hp_max, 250)

    def test_xp_alias_remaps_to_combat_xp_and_recomputes(self):
        cmd = self.run_cmd(" xp 300 Bob")
        self.assertFieldSet(cmd, field="combat_xp", applied=300,
                            target="Bob")
        self.assertEqual(self.bob.db.combat_xp, 300)
        self.assertEqual(self.bob.recomputes, 1)

    def test_alias_defaults_the_target_to_the_caller(self):
        cmd = self.run_cmd(" hp 30")  # no target → me
        self.assertFieldSet(cmd, field="hp", applied=30,
                            target=self.caller.key)
        self.assertEqual(self.caller.db.hp, 30)

    def test_alias_without_a_value_shows_usage(self):
        cmd = self.run_cmd(" hp")
        self.assertIn("Usage", self.output(cmd))
        self.assertEqual(self.bob.db.hp, 100)  # unchanged

    def test_alias_hits_the_canonical_admin_gate(self):
        builder = StatCaller(perms=("Builder",))
        cmd = self.run_cmd(" hp 40 Bob", caller=builder)
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="set")
        self.assertEqual(self.bob.db.hp, 100)  # unchanged


class TestStatPermissions(StatRouterTestCase):
    """`set` keeps the legacy Admin tier via verb_perms; `show` is
    Builder (Requirement 8.7)."""

    def test_set_denied_for_builder(self):
        builder = StatCaller(perms=("Builder",))
        cmd = self.run_cmd(" set Bob hp 40", caller=builder)
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="set")
        self.assertEqual(self.bob.db.hp, 100)  # unchanged

    def test_show_allowed_for_builder(self):
        builder = StatCaller(perms=("Builder",))
        cmd = self.run_cmd(" show Bob", caller=builder)
        self.assertNotIn("Permission denied", self.output(cmd))


class TestStatShowAndDefaults(StatRouterTestCase):
    """NEW `show` readout + caller-default resolution."""

    def test_show_defaults_to_the_caller(self):
        cmd = self.run_cmd(" show")
        out = self.output(cmd)
        self.assertIn("TestAdmin — combat stats", out)
        self.assertIn("HP: 100/100", out)
        self.assertIn("Modifiable fields", out)

    def test_show_named_unit_renders_its_stats(self):
        cmd = self.run_cmd(" show Bob")
        self.assertIn("Bob — combat stats", self.output(cmd))

    def test_set_me_targets_the_caller(self):
        cmd = self.run_cmd(" set me hp 55")
        self.assertFieldSet(cmd, field="hp", applied=55,
                            target=self.caller.key)
        self.assertEqual(self.caller.db.hp, 55)


class TestStatUnknownFieldAndOptOuts(StatRouterTestCase):
    """Unknown-field rejection + opted-out verbs (Requirements 3.7,
    1.5)."""

    def test_set_unknown_field_lists_the_allowlist_no_change(self):
        cmd = self.run_cmd(" set Bob coord_x 5")
        self.assertUnknownField(cmd, field="coord_x",
                                valid=("hp", "hp_max", "level"),
                                plane="instance")
        self.assertEqual(self.bob.db.coord_x, None)  # never written

    def test_list_opt_out_points_at_show(self):
        cmd = self.run_cmd(" list")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(self.adapter.opt_outs["list"], out)

    def test_spawn_opt_out_points_at_agent_outpost(self):
        cmd = self.run_cmd(" spawn scout")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(self.adapter.opt_outs["spawn"], out)

    def test_destroy_opt_out_points_at_unit_deletion(self):
        cmd = self.run_cmd(" destroy Bob")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn(self.adapter.opt_outs["destroy"], out)
        self.assertEqual(self.bob.db.hp, 100)  # unchanged

    def test_def_scope_opt_out_names_the_missing_domain(self):
        cmd = self.run_cmd(" def set foo bar 1")
        out = self.output(cmd)
        self.assertIn("not available", out)
        self.assertIn("no YAML definition domain", out)
