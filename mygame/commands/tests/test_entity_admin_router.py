"""
Unit tests for the EntityAdminRouter base — dispatch scaffolding, read
verbs, aliases, and opt-outs (unified-admin-crud task 1.11).

Verifies with a toy adapter + router subclass that:

- `list` renders indexed rows and replaces the caller's List_Cache
  (empty result → no-instances message + empty cache) — Requirements
  4.1, 4.6
- `show` resolves via the Resolution_Engine and renders the ShowReport
  (identity header, state lines, `field: value [min–max] (perm)` with
  `*override*` flags, staleness note) — Requirement 4.3
- `def list` / `def show` / `def diff` read handlers work — 5.4, 5.6, 5.7
- aliases dispatch the canonical handler with identical output plus a
  one-line deprecation note naming both spellings — Requirements 11.1, 11.2
- opted-out verbs surface the adapter's declared reason, no state change
  — Requirement 1.5
- unknown verbs error listing the available verbs — Requirement 1.8
- read verbs sit at Builder; `def set`/`def reset` demand Admin —
  Requirements 8.1, 8.2 (8.3 registration tier)

The full router test suite is task 1.16; these are scaffolding checks.
"""

import unittest

from mygame.commands.command_router import EntityAdminRouter
from mygame.world.admin.adapter_registry import AdapterRegistry
from mygame.world.admin.resolution import Resolution
from mygame.world.admin.types import FieldSpec, InstanceRow, ShowReport

# The router module imports via the top-level path (`world.admin...`), so
# the List_Cache singleton it writes to is that module instance — import
# it the same way here, not via `mygame.world...` (a distinct module).
from world.admin.resolution import LIST_CACHE

from .router_harness import OutcomeAssertions, RouterCaller

# ------------------------------------------------------------------ #
#  Test doubles
# ------------------------------------------------------------------ #

FakeCaller = RouterCaller




class FakeOverlay:
    """OverlayStore stand-in serving canned overrides/diffs."""

    def __init__(self, overrides=None, diff=None):
        self._overrides = overrides or {}
        self._diff = diff or {}

    def get(self, domain, key):
        return dict(self._overrides.get((domain, key), {}))

    def diff(self):
        return dict(self._diff)


_LEVEL_SPEC = FieldSpec(
    name="level", kind="int", min_value=1, max_value=5, perm="Builder"
)


class ToyAdapter:
    """Minimal EntityAdapter: supports everything but spawn (opted out)."""

    entity_key = "toy"
    def_domain = "toys"
    supported_verbs = frozenset({
        "list", "show", "set", "destroy",
        "def list", "def show", "def set", "def reset", "def diff",
    })
    opt_outs = {
        "spawn": "toys are found in the world — use the loot system",
    }
    extra_verbs = {"boop": "Boop the toy"}
    aliases = {"stats": "show", "defs": "def list"}

    def __init__(self):
        self.teddy = object()
        self.rows = [
            InstanceRow(index=1, key="toy_1", name="Teddy",
                        summary="Teddy (lvl 3)", ref=self.teddy),
            InstanceRow(index=2, key="toy_2", name="Ball",
                        summary="Ball (lvl 1)", ref=object()),
        ]
        self.definitions = {
            "teddy": {"key": "teddy", "name": "Teddy Bear", "level": 9},
            "ball": {"key": "ball", "name": "Bouncy Ball", "level": 1},
        }
        self.mutations = []

    # --- resolution / listing ---
    def list_instances(self, caller, filter_str):
        if filter_str == "none":
            return []
        return list(self.rows)

    def resolve_instance(self, caller, token):
        if token.lower() == "teddy":
            return Resolution(ok=True, target=self.teddy)
        return Resolution(ok=False, error=f"No match found for '{token}'.")

    # --- field schemas ---
    def instance_fields(self):
        return {"level": _LEVEL_SPEC}

    def definition_fields(self):
        return {"level": _LEVEL_SPEC}

    # --- CRUD hooks (must not run from read verbs / opt-outs) ---
    def create(self, caller, def_token, kwargs):
        self.mutations.append("create")

    def read(self, caller, instance):
        return ShowReport(
            header="Toy: Teddy (toy_1)",
            state_lines=["owner: TestAdmin"],
            fields=[(_LEVEL_SPEC, 3, True)],
            staleness_note="note: level stamped 3, current def says 9",
        )

    def update(self, caller, instance, field, value):
        self.mutations.append("update")

    def delete(self, caller, instance):
        self.mutations.append("delete")

    # --- definition scope ---
    def def_registry_dict(self):
        return self.definitions

    def def_resolve(self, token):
        return self.definitions.get(token.lower())


_TEST_REGISTRY = AdapterRegistry()
_TEST_REGISTRY.register(ToyAdapter())


class ToyRouter(EntityAdminRouter):
    key = "@toy"
    adapter_key = "toy"

    overlay = FakeOverlay()  # per-test override

    def _adapter_registry(self):
        return _TEST_REGISTRY

    def _overlay_store(self):
        return self.overlay

    def sub_boop(self, rest):
        self._booped = rest
        self.caller.msg("Boop!")


def _run(args, perms=("Builder",), overlay=None):
    """Run the toy router with *args*; return the command instance."""
    cmd = ToyRouter()
    cmd.caller = FakeCaller(perms=perms)
    cmd.args = args
    if overlay is not None:
        cmd.overlay = overlay
    cmd.func()
    return cmd


def _adapter():
    return _TEST_REGISTRY.get("toy")


# ------------------------------------------------------------------ #
#  list — indexed rows + List_Cache replacement (R4.1, R4.6)
# ------------------------------------------------------------------ #

class TestListHandler(unittest.TestCase):

    def test_list_renders_indexed_rows(self):
        cmd = _run(" list")
        out = "\n".join(cmd.caller.messages)
        self.assertIn("#1", out)
        self.assertIn("Teddy (lvl 3)", out)
        self.assertIn("#2", out)
        self.assertIn("Ball (lvl 1)", out)

    def test_list_replaces_list_cache_with_displayed_rows(self):
        cmd = _run(" list")
        cached = LIST_CACHE.get(cmd.caller, "toy")
        self.assertEqual(cached, tuple(_adapter().rows))

    def test_empty_list_messages_and_stores_empty_cache(self):
        cmd = _run(" list none")
        self.assertIn("No toy instances found", cmd.caller.messages[0])
        self.assertEqual(LIST_CACHE.get(cmd.caller, "toy"), ())


# ------------------------------------------------------------------ #
#  show — ShowReport rendering (R4.3)
# ------------------------------------------------------------------ #

class TestShowHandler(unittest.TestCase):

    def test_show_renders_full_report(self):
        cmd = _run(" show teddy")
        out = "\n".join(cmd.caller.messages)
        self.assertIn("Toy: Teddy (toy_1)", out)          # identity header
        self.assertIn("owner: TestAdmin", out)             # state line
        self.assertIn("Modifiable fields:", out)
        self.assertIn("level: 3 [1–5] (Builder) *override*", out)
        self.assertIn("note: level stamped 3, current def says 9", out)

    def test_show_unresolved_token_relays_error(self):
        cmd = _run(" show nope")
        self.assertIn("No match found for 'nope'.", cmd.caller.messages[0])


# ------------------------------------------------------------------ #
#  def scope reads (R5.4 rendering, R5.6, R5.7)
# ------------------------------------------------------------------ #

class TestDefReadHandlers(unittest.TestCase):

    def test_def_list_lists_definitions(self):
        cmd = _run(" def list")
        out = "\n".join(cmd.caller.messages)
        self.assertIn("teddy", out)
        self.assertIn("Teddy Bear", out)
        self.assertIn("ball", out)

    def test_def_show_renders_fields_with_override_flag(self):
        overlay = FakeOverlay(overrides={("toys", "teddy"): {"level": 9}})
        cmd = _run(" def show teddy", overlay=overlay)
        out = "\n".join(cmd.caller.messages)
        self.assertIn("toy definition: teddy", out)
        self.assertIn("level: 9 *override*", out)
        self.assertIn("name: Teddy Bear", out)
        self.assertNotIn("name: Teddy Bear *override*", out)

    def test_def_show_unknown_key_errors(self):
        cmd = _run(" def show nada")
        self.assertIn("No definition found for 'nada'.",
                      cmd.caller.messages[0])

    def test_def_diff_renders_domain_deviations(self):
        overlay = FakeOverlay(diff={"toys": {"teddy": {"level": 9}}})
        cmd = _run(" def diff", overlay=overlay)
        out = "\n".join(cmd.caller.messages)
        self.assertIn("teddy.level = 9", out)

    def test_def_diff_empty_overlay_is_empty(self):
        cmd = _run(" def diff", overlay=FakeOverlay())
        self.assertIn("No definition overrides", cmd.caller.messages[0])


# ------------------------------------------------------------------ #
#  aliases — canonical dispatch + deprecation note (R11.1, R11.2)
# ------------------------------------------------------------------ #

class TestAliases(unittest.TestCase):

    def test_alias_emits_note_naming_both_spellings(self):
        cmd = _run(" stats teddy")
        note = cmd.caller.messages[0]
        self.assertIn("stats", note)
        self.assertIn("show", note)

    def test_alias_output_identical_to_canonical(self):
        alias_cmd = _run(" stats teddy")
        canon_cmd = _run(" show teddy")
        self.assertEqual(alias_cmd.caller.messages[1:],
                         canon_cmd.caller.messages)

    def test_alias_to_def_verb_dispatches_def_handler(self):
        alias_cmd = _run(" defs")
        canon_cmd = _run(" def list")
        self.assertEqual(alias_cmd.caller.messages[1:],
                         canon_cmd.caller.messages)
        self.assertIn("defs", alias_cmd.caller.messages[0])
        self.assertIn("def list", alias_cmd.caller.messages[0])


# ------------------------------------------------------------------ #
#  opt-outs (R1.5) and unknown verbs (R1.8)
# ------------------------------------------------------------------ #

class TestOptOutsAndUnknownVerbs(unittest.TestCase):

    def test_opted_out_verb_surfaces_reason_no_state_change(self):
        adapter = _adapter()
        before = list(adapter.mutations)
        cmd = _run(" spawn teddy")
        self.assertIn("toys are found in the world", cmd.caller.messages[0])
        self.assertEqual(adapter.mutations, before)

    def test_unknown_verb_lists_available_verbs(self):
        cmd = _run(" frobnicate")
        out = cmd.caller.messages[0]
        self.assertIn("frobnicate", out)
        for verb in ("list", "show", "set", "destroy",
                     "def list", "def show", "def set", "def reset",
                     "def diff", "boop", "stats", "defs"):
            self.assertIn(verb, out)

    def test_unknown_def_subverb_lists_available_def_verbs(self):
        cmd = _run(" def frob")
        out = cmd.caller.messages[0]
        self.assertIn("def frob", out)
        self.assertIn("def list", out)


# ------------------------------------------------------------------ #
#  permission tiers (R8.1, R8.2; def set/reset registered Admin, R8.3)
# ------------------------------------------------------------------ #

class TestPermissionTiers(OutcomeAssertions, unittest.TestCase):

    def test_read_verbs_allowed_at_builder(self):
        for args in (" list", " show teddy", " def list",
                     " def show teddy", " def diff"):
            cmd = _run(args, perms=("Builder",))
            joined = "\n".join(cmd.caller.messages)
            self.assertNotIn("Permission denied", joined,
                             f"read verb '{args.strip()}' denied at Builder")

    def test_def_set_denied_below_admin(self):
        cmd = _run(" def set teddy level 5", perms=("Builder",))
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def set")

    def test_def_reset_denied_below_admin(self):
        cmd = _run(" def reset teddy level", perms=("Builder",))
        self.assertPermDenied(cmd, required="Admin", scope="verb",
                              target="def reset")

    def test_def_set_reaches_handler_at_admin(self):
        cmd = _run(" def set teddy level 5", perms=("Builder", "Admin"))
        # Past the permission gate and into the def-set flow (which halts
        # at the unavailable Data Registry in this stubbed environment).
        self.assertNotIn("Permission denied", cmd.caller.messages[0])
        self.assertIn("Data Registry unavailable", cmd.caller.messages[0])


# ------------------------------------------------------------------ #
#  extra verbs (R1.6) and missing-adapter guard
# ------------------------------------------------------------------ #

class TestExtrasAndGuards(unittest.TestCase):

    def test_extra_verb_dispatches_subclass_handler(self):
        cmd = _run(" boop hi")
        self.assertEqual(cmd._booped, "hi")
        self.assertIn("Boop!", cmd.caller.messages[0])

    def test_missing_adapter_errors_cleanly(self):
        class OrphanRouter(EntityAdminRouter):
            key = "@orphan"
            adapter_key = "orphan"

            def _adapter_registry(self):
                return _TEST_REGISTRY

        cmd = OrphanRouter()
        cmd.caller = FakeCaller()
        cmd.args = " list"
        cmd.func()
        self.assertIn("No entity adapter", cmd.caller.messages[0])


if __name__ == "__main__":
    unittest.main()
