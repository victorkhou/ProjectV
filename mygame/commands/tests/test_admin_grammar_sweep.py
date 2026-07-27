"""
Cross-entity integration tests for the unified admin grammar
(unified-admin-crud task 9.2).

Where the per-router suites (``test_admin_routers.py``,
``test_admin_legacy_routers.py``, ``test_admin_def_only_routers.py``)
prove each ``@<entity>`` command's behaviour in depth, this module proves
the invariants that must hold UNIFORMLY across EVERY registered adapter —
the properties a new adapter (or a change to an existing one) could
silently break without any single per-router test noticing:

1. Grammar sweep (R1.4, R1.5) — for every adapter the real
   ``register_all()`` installs, each of the ten ``CORE_VERBS`` either
   *dispatches* (the router has a live route for it) or *surfaces its
   declared opt-out reason* through the real router. Nothing is left
   unaccounted for, and every opt-out reason is non-empty and shown
   verbatim.

2. Alias matrix (R11.1, R11.5) — every migration alias every adapter
   declares (the full ``adapter.aliases`` matrix, 11 aliases across 7
   adapters) is installed pointing at a valid canonical verb, and driving
   it through the REAL router emits the one-line deprecation note naming
   both spellings and routes to that canonical. This exercises the
   value-first reshaping overrides (``@player level/rank``, ``@stat
   hp/maxhp/xp``) as well as the six pass-through aliases.

3. Audit trail (R9.1) — every mutating verb (``spawn``/``set``/
   ``destroy``/``def set``/``def reset``) writes EXACTLY ONE audit entry
   on its success path, tagged with the verb.

_Requirements: 1.4, 1.5, 9.1, 11.1, 11.5_
"""

import itertools
import unittest
from contextlib import nullcontext

from mygame.commands.command_router import EntityAdminRouter
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.resolution import Resolution
from world.admin.types import CORE_VERBS, FieldSpec, InstanceRow, ShowReport

# The twelve migrated @<entity> routers — the concrete subclasses whose
# real _dispatch_alias overrides (player/stat reshape args) the alias
# matrix must exercise, keyed by the adapter_key each binds to.
from mygame.commands.admin_commands import (
    CmdAdminBuilding,
    CmdAdminItem,
    CmdAdminOutpost,
    CmdAdminPlanet,
    CmdAdminPlayer,
    CmdAdminPowerup,
    CmdAdminResource,
    CmdAdminStat,
    CmdAdminTech,
    CmdAdminTerrain,
)
from mygame.commands.agent_commands import CmdAdminAgent
from mygame.commands.alliance_commands import CmdAdminAlliance

_CALLER_IDS = itertools.count(900_000)


#: adapter_key -> the concrete router class the cmdset installs. The
#: alias matrix (Part 2B) drives the REAL router so the value-first
#: reshaping subclasses (@player, @stat) are exercised, not just the base.
_ROUTER_FOR = {
    "item": CmdAdminItem,
    "building": CmdAdminBuilding,
    "agent": CmdAdminAgent,
    "tech": CmdAdminTech,
    "outpost": CmdAdminOutpost,
    "alliance": CmdAdminAlliance,
    "player": CmdAdminPlayer,
    "stat": CmdAdminStat,
    "resource": CmdAdminResource,
    "powerup": CmdAdminPowerup,
    "terrain": CmdAdminTerrain,
    "planet": CmdAdminPlanet,
}


class FakeCaller:
    """Caller mock: msg() + a configurable permission set (Builder+Admin
    by default so neither the verb tier nor the def-write Admin pin ever
    short-circuits a sweep before the behaviour under test)."""

    def __init__(self, perms=("Builder", "Admin")):
        self.id = next(_CALLER_IDS)
        self.key = "SweepAdmin"
        self.perms = set(perms)
        self.messages = []

    def msg(self, text, **kwargs):
        self.messages.append(text)

    def check_permstring(self, perm):
        return perm in self.perms


def _output(caller):
    return "\n".join(str(m) for m in caller.messages)


# ================================================================== #
#  Part 1 — grammar sweep: dispatch-or-opt-out for every core verb
#  across every registered adapter (Requirements 1.4, 1.5)
# ================================================================== #

class _SweepRouter(EntityAdminRouter):
    """Generic router bound to an arbitrary adapter for table inspection.

    Only the dispatch TABLE and the opt-out messaging are exercised here
    (never a supported verb's system-touching handler), so a generic
    subclass — ``adapter_key`` set per iteration — suffices for every
    entity.
    """

    registry = None

    def _adapter_registry(self):
        return self.registry


_INSTANCE_VERBS = ("list", "spawn", "show", "set", "destroy")
_DEF_VERBS = ("def list", "def show", "def set", "def reset", "def diff")


class GrammarSweepTestCase(unittest.TestCase):
    """Every adapter the real composition root registers, inspected
    through a live router — a fresh registry built via ``register_all``
    (the same path ``game_init`` runs)."""

    def setUp(self):
        from world.admin.resolution import LIST_CACHE as cache
        cache.clear()
        self.registry = register_all(AdapterRegistry())
        self.adapters = self.registry.all()

    def _router_for(self, adapter):
        cmd = _SweepRouter()
        cmd.registry = self.registry
        cmd.adapter_key = adapter.entity_key
        cmd.key = f"@{adapter.entity_key}"
        cmd.caller = FakeCaller()
        return cmd

    def _drive(self, adapter, args):
        cmd = self._router_for(adapter)
        cmd.args = args
        cmd.func()
        return cmd

    # -- the registry installs the full production roster -------------- #

    def test_register_all_installs_the_twelve_entity_adapters(self):
        keys = sorted(a.entity_key for a in self.adapters)
        self.assertEqual(keys, sorted(_ROUTER_FOR))
        self.assertEqual(len(self.adapters), 12)

    # -- every core verb is accounted for, per adapter ----------------- #

    def test_every_adapter_accounts_for_every_core_verb(self):
        """Support-or-opt-out with a non-empty reason — the same contract
        the registry enforces, re-asserted at the point the router reads
        it (so the two can't drift)."""
        for adapter in self.adapters:
            supported = set(adapter.supported_verbs)
            opt_outs = dict(adapter.opt_outs)
            for verb in CORE_VERBS:
                with self.subTest(entity=adapter.entity_key, verb=verb):
                    self.assertTrue(
                        verb in supported or verb in opt_outs,
                        f"{adapter.entity_key}: core verb '{verb}' is "
                        "neither supported nor opted out",
                    )
                    if verb in opt_outs:
                        self.assertTrue(
                            opt_outs[verb].strip(),
                            f"{adapter.entity_key}: opt-out for '{verb}' "
                            "has an empty reason",
                        )

    # -- supported instance verbs have a live (non-opt-out) route ------ #

    def test_supported_instance_verbs_dispatch_not_opt_out(self):
        for adapter in self.adapters:
            cmd = self._router_for(adapter)
            subs = cmd.subcommands
            for verb in _INSTANCE_VERBS:
                if verb not in adapter.supported_verbs:
                    continue
                with self.subTest(entity=adapter.entity_key, verb=verb):
                    self.assertIn(verb, subs)
                    _handler, help_text, perm = subs[verb]
                    # An opted-out verb's help begins "Not available — ";
                    # a live route carries the core help + its verb tier.
                    self.assertFalse(
                        help_text.startswith("Not available"),
                        f"{adapter.entity_key} '{verb}' routes to opt-out",
                    )
                    self.assertEqual(perm, cmd._verb_perm(verb))

    # -- opted-out instance verbs surface the declared reason ---------- #

    def test_opted_out_instance_verbs_surface_their_reason(self):
        tokens = {
            "list": " list", "spawn": " spawn x", "show": " show x",
            "set": " set x f 1", "destroy": " destroy x",
        }
        for adapter in self.adapters:
            for verb in _INSTANCE_VERBS:
                if verb not in adapter.opt_outs:
                    continue
                with self.subTest(entity=adapter.entity_key, verb=verb):
                    cmd = self._drive(adapter, tokens[verb])
                    out = _output(cmd.caller)
                    self.assertIn(f"@{adapter.entity_key} {verb} "
                                  "is not available:", out)
                    self.assertIn(adapter.opt_outs[verb], out)

    # -- supported def verbs are routable through the def sub-dispatch - #

    def test_supported_def_verbs_are_routable(self):
        for adapter in self.adapters:
            cmd = self._router_for(adapter)
            subs = cmd.subcommands
            for verb in _DEF_VERBS:
                if verb not in adapter.supported_verbs:
                    continue
                subverb = verb.split(None, 1)[1]
                with self.subTest(entity=adapter.entity_key, verb=verb):
                    # The `def` keyword is always installed; the concrete
                    # def verb is routed inside _sub_def, gated by its tier.
                    self.assertIn("def", subs)
                    self.assertIn(subverb, cmd._DEF_SUBVERBS)

    # -- opted-out def verbs surface the declared reason --------------- #

    def test_opted_out_def_verbs_surface_their_reason(self):
        tokens = {
            "def list": " def list", "def show": " def show x",
            "def set": " def set x f 1", "def reset": " def reset x",
            "def diff": " def diff",
        }
        for adapter in self.adapters:
            for verb in _DEF_VERBS:
                if verb not in adapter.opt_outs:
                    continue
                with self.subTest(entity=adapter.entity_key, verb=verb):
                    cmd = self._drive(adapter, tokens[verb])
                    out = _output(cmd.caller)
                    self.assertIn(f"@{adapter.entity_key} {verb} "
                                  "is not available:", out)
                    self.assertIn(adapter.opt_outs[verb], out)

    # -- the def-write tier is pinned to Admin everywhere -------------- #

    def test_def_write_verbs_pinned_to_admin_on_every_adapter(self):
        """def set / def reset are Admin on every entity and cannot be
        lowered by a verb_perms override (Requirement 8.3)."""
        for adapter in self.adapters:
            cmd = self._router_for(adapter)
            for verb in ("def set", "def reset"):
                with self.subTest(entity=adapter.entity_key, verb=verb):
                    self.assertEqual(cmd._verb_perm(verb), "Admin")


# ================================================================== #
#  Part 2 — the migration-alias matrix, end to end
#  (Requirements 11.1, 11.2, 11.5)
# ================================================================== #

# The full adapter-declared alias matrix (11 aliases / 7 adapters). The
# six pass-through aliases share the canonical's grammar; the five
# value-first aliases (level/rank/hp/maxhp/xp) reshape their arguments in
# a router-subclass _dispatch_alias override before the shared note. The
# test derives the matrix from register_all() and asserts THIS is exactly
# it, so a new/removed alias fails loudly rather than silently escaping
# the sweep.
_EXPECTED_ALIAS_MATRIX = {
    "agent": {"create": "spawn"},
    "alliance": {"inspect": "show", "disband": "destroy"},
    "item": {"stats": "show"},
    "outpost": {"tiers": "def list"},
    "player": {"level": "set", "rank": "set"},
    "resource": {"give": "spawn"},
    "stat": {"hp": "set", "maxhp": "set", "xp": "set"},
}

#: A well-formed argument per alias — value-first aliases need a value
#: token so the reshape doesn't bail to a usage message before the note.
_ALIAS_ARG = {
    "create": "rifleman", "inspect": "ABC", "disband": "ABC",
    "stats": "rifle", "tiers": "", "give": "metal 5",
    "level": "5", "rank": "3", "hp": "50", "maxhp": "100", "xp": "200",
}


class TestAliasMatrixStructure(unittest.TestCase):
    """Part 2A — every declared alias installs, pointing at a valid
    canonical, across the whole register_all() roster."""

    def setUp(self):
        self.registry = register_all(AdapterRegistry())
        self.adapters = self.registry.all()

    def test_declared_alias_matrix_is_exactly_the_expected_set(self):
        actual = {
            a.entity_key: dict(a.aliases)
            for a in self.adapters if a.aliases
        }
        self.assertEqual(actual, _EXPECTED_ALIAS_MATRIX)

    def test_every_alias_points_at_a_supported_canonical_verb(self):
        for adapter in self.adapters:
            supported = set(adapter.supported_verbs)
            for alias, canonical in adapter.aliases.items():
                with self.subTest(entity=adapter.entity_key, alias=alias):
                    self.assertIn(
                        canonical, supported,
                        f"{adapter.entity_key}: alias '{alias}' points at "
                        f"'{canonical}', which the adapter does not support",
                    )

    def test_router_installs_every_alias_as_a_deprecated_route(self):
        for adapter in self.adapters:
            cmd = _SweepRouter()
            cmd.registry = self.registry
            cmd.adapter_key = adapter.entity_key
            cmd.key = f"@{adapter.entity_key}"
            cmd.caller = FakeCaller()
            subs = cmd.subcommands
            for alias, canonical in adapter.aliases.items():
                with self.subTest(entity=adapter.entity_key, alias=alias):
                    self.assertIn(alias, subs)
                    self.assertEqual(
                        subs[alias][1], f"Alias of '{canonical}' (deprecated)"
                    )


class TestAliasMatrixBehaviour(unittest.TestCase):
    """Part 2B — driving each alias through its REAL router emits the
    one-line deprecation note naming both spellings and routes to the
    declared canonical. Covers the value-first reshaping overrides."""

    def _run_alias(self, entity_key, adapter, alias, canonical):
        """Drive one alias on its real router with the canonical handler
        spied out, so only the alias mechanic (note + routing) is
        observed — the per-router suites cover the canonical's effect."""
        router_cls = _ROUTER_FOR[entity_key]
        cmd = router_cls()
        cmd._adapter_cache = adapter          # bypass registry lookup
        cmd.caller = FakeCaller()
        cmd.args = f" {alias} {_ALIAS_ARG[alias]}".rstrip()

        # Build the real subcommand table, then replace the canonical
        # handlers with spies (perm dropped so routing always proceeds;
        # the note is emitted before the perm check regardless).
        subs = cmd.subcommands
        routed = []
        for verb in _INSTANCE_VERBS:
            if verb in subs:
                _h, help_text, _p = subs[verb]
                subs[verb] = (
                    (lambda v: lambda c, r: routed.append((v, r)))(verb),
                    help_text, "",
                )
        # def-prefixed canonicals (e.g. tiers -> "def list") route through
        # _sub_def, not the subcommands table — spy that path too.
        cmd._sub_def = lambda rest: routed.append(("def", rest))

        cmd.func()
        return cmd, routed

    def test_every_alias_emits_the_one_line_deprecation_note(self):
        registry = register_all(AdapterRegistry())
        for adapter in registry.all():
            entity_key = adapter.entity_key
            for alias, canonical in adapter.aliases.items():
                with self.subTest(entity=entity_key, alias=alias):
                    from world.admin.resolution import LIST_CACHE
                    LIST_CACHE.clear()
                    cmd, routed = self._run_alias(
                        entity_key, adapter, alias, canonical
                    )
                    notes = [m for m in cmd.caller.messages
                             if "deprecated" in str(m)]
                    self.assertTrue(
                        notes, f"{entity_key} '{alias}': no deprecation note"
                    )
                    note = str(notes[0])
                    self.assertNotIn("\n", note)              # one line (R11.2)
                    self.assertIn(f"'{alias}'", note)          # invoked spelling
                    self.assertIn(
                        f"@{entity_key} {canonical}", note      # canonical target
                    )

    def test_every_alias_routes_to_its_declared_canonical(self):
        registry = register_all(AdapterRegistry())
        for adapter in registry.all():
            entity_key = adapter.entity_key
            for alias, canonical in adapter.aliases.items():
                with self.subTest(entity=entity_key, alias=alias):
                    from world.admin.resolution import LIST_CACHE
                    LIST_CACHE.clear()
                    _cmd, routed = self._run_alias(
                        entity_key, adapter, alias, canonical
                    )
                    # def-prefixed canonicals land on the ("def", ...) spy;
                    # instance canonicals on their own verb spy.
                    expected = "def" if canonical.startswith("def ") \
                        else canonical
                    self.assertTrue(
                        any(r[0] == expected for r in routed),
                        f"{entity_key} '{alias}' did not route to "
                        f"'{canonical}' (routed={routed})",
                    )


# ================================================================== #
#  Part 3 — every mutating verb writes exactly one audit entry
#  (Requirement 9.1)
# ================================================================== #

class _Toy:
    def __init__(self, key, name, power=3):
        self.key = key
        self.name = name
        self.power = power


class _AuditToyAdapter:
    """An adapter double supporting ALL ten core verbs, so a single toy
    drives every mutating verb (spawn/set/destroy/def set/def reset) to
    its success path and the audit count can be asserted uniformly."""

    entity_key = "toy"
    def_domain = "toys"
    supported_verbs = frozenset(CORE_VERBS)
    opt_outs: dict = {}
    extra_verbs: dict = {}
    aliases: dict = {}

    def __init__(self):
        self.instances = {"toy_1": _Toy("toy_1", "One")}
        self.definitions = {
            "widget": {"key": "widget", "name": "Widget", "power": 3},
        }
        self.created = []

    def list_instances(self, caller, filter_str):
        return [InstanceRow(index=1, key="toy_1", name="One",
                            summary="One", ref=self.instances["toy_1"])]

    def resolve_instance(self, caller, token):
        toy = self.instances.get(token)
        if toy is None:
            return Resolution(ok=False, error=f"No match for '{token}'.")
        return Resolution(ok=True, target=toy)

    def instance_fields(self):
        return {"power": FieldSpec(name="power", kind="int",
                                   min_value=1, max_value=9, perm="Builder")}

    def definition_fields(self):
        return {"power": FieldSpec(name="power", kind="int", perm="Builder")}

    def create(self, caller, def_token, kwargs):
        toy = _Toy("toy_new", "New")
        self.instances["toy_new"] = toy
        self.created.append(def_token)
        return toy

    def read(self, caller, instance):
        return ShowReport(header="", state_lines=[], fields=[])

    def update(self, caller, instance, field, value):
        setattr(instance, field, value)

    def delete(self, caller, instance):
        del self.instances[instance.key]

    def def_registry_dict(self):
        return self.definitions

    def def_resolve(self, token):
        return self.definitions.get(str(token).lower())


class _FakeStore:
    """OverlayStore stand-in for the def-write flow (no disk)."""

    def __init__(self):
        self.calls = []

    def get(self, domain, key):
        return {}

    def set(self, domain, key, field, value):
        self.calls.append(("set", domain, key, field, value))

    def reset(self, domain, key, field=None):
        self.calls.append(("reset", domain, key, field))

    def restore_snapshot(self):
        self.calls.append(("restore",))

    def diff(self):
        return {}


class _FakeDataRegistry:
    """Reload always succeeds — the def-write path reaches its audit call."""

    def reload_all(self):
        return True, []


class _AuditToyRouter(EntityAdminRouter):
    key = "@toy"
    adapter_key = "toy"

    registry = None
    store = None
    datareg = None
    audit_log = None

    def _adapter_registry(self):
        return self.registry

    def _overlay_store(self):
        return self.store

    def _data_registry(self):
        return self.datareg

    def _reload_lock(self):
        return nullcontext()

    def _log_admin(self, verb, detail):
        self.audit_log.append((verb, detail))


class AuditPerMutationTestCase(unittest.TestCase):
    """Fresh toy adapter + router per verb so each audit count is
    isolated. Caller holds Admin (covers the def-write pin)."""

    def setUp(self):
        from world.admin.resolution import LIST_CACHE as cache
        cache.clear()

    def _run(self, args):
        adapter = _AuditToyAdapter()
        registry = AdapterRegistry()
        registry.register(adapter)
        cmd = _AuditToyRouter()
        cmd.registry = registry
        cmd.store = _FakeStore()
        cmd.datareg = _FakeDataRegistry()
        cmd.audit_log = []
        cmd.caller = FakeCaller(perms=("Builder", "Admin"))
        cmd.args = args
        cmd.func()
        return cmd

    def test_spawn_writes_exactly_one_audit_entry(self):
        cmd = self._run(" spawn widget")
        self.assertEqual(len(cmd.audit_log), 1)
        self.assertEqual(cmd.audit_log[0][0], "spawn")

    def test_set_writes_exactly_one_audit_entry(self):
        cmd = self._run(" set toy_1 power 5")
        self.assertEqual(len(cmd.audit_log), 1)
        self.assertEqual(cmd.audit_log[0][0], "set")

    def test_destroy_writes_exactly_one_audit_entry(self):
        cmd = self._run(" destroy toy_1")
        self.assertEqual(len(cmd.audit_log), 1)
        self.assertEqual(cmd.audit_log[0][0], "destroy")

    def test_def_set_writes_exactly_one_audit_entry(self):
        cmd = self._run(" def set widget power 7")
        self.assertEqual(len(cmd.audit_log), 1)
        self.assertEqual(cmd.audit_log[0][0], "def set")

    def test_def_reset_writes_exactly_one_audit_entry(self):
        cmd = self._run(" def reset widget power")
        self.assertEqual(len(cmd.audit_log), 1)
        self.assertEqual(cmd.audit_log[0][0], "def reset")

    def test_read_only_verbs_write_no_audit_entry(self):
        """list/show/def list/def show/def diff mutate nothing → no
        audit entry (the audit trail is mutation-only, R9.1)."""
        for args in (" list", " show toy_1", " def list",
                     " def show widget", " def diff"):
            with self.subTest(args=args):
                cmd = self._run(args)
                self.assertEqual(cmd.audit_log, [], f"{args} audited")


if __name__ == "__main__":
    unittest.main()
