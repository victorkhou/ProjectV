"""
Unit tests for the TechnologyAdapter (unified-admin-crud task 5.3).

Coverage:
- Grammar contract: every core verb supported except instance ``set``,
  which is opted out with the no-modifiable-per-instance-fields reason
  (Requirement 7.1); ``grant``/``revoke`` extra verbs; no aliases;
  registration in the AdapterRegistry (including ``register_all``)
  succeeds.
- Field schema: empty instance schema (set opted out); definition
  schema against real ``TechnologyDef`` fields.
- Listing + resolution: rows over the scoped player's granted techs
  (trailing ``[player]`` defaults to the caller — Requirements 2.4,
  7.1), ``#N`` via the List_Cache, key/name/prefix tiers, and the
  grant-state error when a real technology is not held
  (Requirement 7.9).
- CRUD hooks through the REAL TechLabSystem single-writer paths
  (Requirement 3.5): create → ``admin_grant_technology`` adds through
  the research path AND recomputes derived tech bonuses before
  returning (Requirement 7.7); delete → ``admin_revoke_technology``
  removes + recomputes (Requirement 7.8); double-grant / absent-revoke
  error stating the grant state with no state change (Requirement 7.9).
- ``read``: ShowReport shape with def-backed info and an empty
  modifiable-fields block; ``def_registry_dict``/``def_resolve`` serve
  the technologies domain.

Requirements: 7.1, 7.7, 7.8, 7.9
"""

import unittest

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.tech_adapter import TechGrant, TechnologyAdapter
from world.admin.resolution import LIST_CACHE
from world.admin.types import CORE_VERBS
from world.definitions import TechnologyDef
from world.event_bus import EventBus
from world.systems.tech_system import TechLabSystem


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _tdef(key, name, effect_type="", effect_value=None, rank="Private"):
    return TechnologyDef(
        name=name, key=key, required_rank=rank,
        resource_cost={"Wood": 10}, research_ticks=5,
        effect_type=effect_type, effect_value=effect_value,
    )


class FakeTechRegistry:
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


class _Db:
    """Attribute-bag double for a player's ``db``."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):  # unset fields read as None
        return None


class Player:
    _next_id = 1

    def __init__(self, key, researched=(), known_players=()):
        self.id = Player._next_id
        Player._next_id += 1
        self.key = key
        self.db = _Db(researched_techs=set(researched), tech_bonuses={})
        self._known = {p.key.lower(): p for p in known_players}

    def search(self, name, **kwargs):
        return self._known.get(str(name).lower())


_DEFS = (
    _tdef("drone_swarm", "Drone Swarm", effect_type="stat_bonus",
          effect_value={"damage": 5}),
    _tdef("nano_armor", "Nano Armor", effect_type="stat_bonus",
          effect_value={"damage_reduction": 2}),
    _tdef("deep_scan", "Deep Scan"),
)


def _adapter_with(researched=(), caller=None, defs=_DEFS):
    caller = caller or Player("Admin", researched=researched)
    registry = FakeTechRegistry(defs)
    system = TechLabSystem(registry=registry, event_bus=EventBus())
    adapter = TechnologyAdapter(registry=registry, tech_system=system)
    return adapter, system, caller


# ------------------------------------------------------------------ #
#  Grammar contract (Requirement 7.1)
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):

    def test_all_core_verbs_supported_except_instance_set(self):
        adapter = TechnologyAdapter()
        self.assertEqual(adapter.supported_verbs, CORE_VERBS - {"set"})
        self.assertEqual(set(adapter.opt_outs), {"set"})

    def test_set_opt_out_reason_names_no_per_instance_fields(self):
        reason = TechnologyAdapter().opt_outs["set"]
        self.assertTrue(reason.strip())
        self.assertIn("no modifiable per-instance fields", reason)
        # Pointer to the supported paths (Requirement 1.5).
        self.assertIn("grant", reason)
        self.assertIn("def set", reason)

    def test_grant_and_revoke_extra_verbs_declared(self):
        extras = TechnologyAdapter().extra_verbs
        self.assertEqual(set(extras), {"grant", "revoke"})
        self.assertIn("spawn", extras["grant"])
        self.assertIn("destroy", extras["revoke"])

    def test_no_aliases_installed(self):
        self.assertEqual(TechnologyAdapter().aliases, {})

    def test_registers_cleanly_covering_all_core_verbs(self):
        adapter = TechnologyAdapter()
        covered = adapter.supported_verbs | set(adapter.opt_outs)
        self.assertEqual(covered, CORE_VERBS)
        registry = AdapterRegistry()
        registry.register(adapter)  # must not raise
        self.assertIs(registry.get("tech"), adapter)

    def test_register_all_includes_the_tech_adapter(self):
        registry = register_all(AdapterRegistry())
        self.assertIsInstance(registry.get("tech"), TechnologyAdapter)


# ------------------------------------------------------------------ #
#  Field schemas
# ------------------------------------------------------------------ #

class TestFieldSchema(unittest.TestCase):

    def test_instance_schema_is_empty(self):
        self.assertEqual(TechnologyAdapter().instance_fields(), {})

    def test_definition_schema_names_real_technologydef_fields(self):
        fields = TechnologyAdapter().definition_fields()
        self.assertEqual(
            set(fields),
            {"name", "required_rank", "research_ticks", "effect_type"},
        )
        self.assertEqual(fields["research_ticks"].kind, "int")
        self.assertEqual(fields["research_ticks"].min_value, 1)
        for spec in fields.values():
            self.assertEqual(spec.perm, "Admin")


# ------------------------------------------------------------------ #
#  Listing + resolution (Requirements 2.4, 7.1, 7.9)
# ------------------------------------------------------------------ #

class TestListingAndResolution(unittest.TestCase):

    def test_list_rows_are_the_callers_granted_techs(self):
        adapter, _system, caller = _adapter_with(
            researched=("drone_swarm", "nano_armor"))
        rows = adapter.list_instances(caller, "")
        self.assertEqual([r.index for r in rows], [1, 2])
        self.assertEqual([r.key for r in rows],
                         ["drone_swarm", "nano_armor"])
        self.assertEqual(rows[0].name, "Drone Swarm")
        grant = rows[0].ref
        self.assertIsInstance(grant, TechGrant)
        self.assertIs(grant.player, caller)

    def test_list_filter_matches_key_or_name(self):
        adapter, _system, caller = _adapter_with(
            researched=("drone_swarm", "nano_armor"))
        rows = adapter.list_instances(caller, "nano")
        self.assertEqual([r.key for r in rows], ["nano_armor"])

    def test_list_trailing_player_token_scopes_the_grants(self):
        bob = Player("Bob", researched=("deep_scan",))
        caller = Player("Admin", known_players=(bob,))
        adapter, _system, _ = _adapter_with(caller=caller)
        rows = adapter.list_instances(caller, "Bob")
        self.assertEqual([r.key for r in rows], ["deep_scan"])

    def test_resolve_by_exact_key_name_and_prefix(self):
        adapter, _system, caller = _adapter_with(
            researched=("drone_swarm",))
        for token in ("drone_swarm", "Drone Swarm", "dro"):
            result = adapter.resolve_instance(caller, token)
            self.assertTrue(result.ok, f"failed for {token!r}")
            self.assertEqual(result.target.key, "drone_swarm")

    def test_resolve_index_token_via_list_cache(self):
        adapter, _system, caller = _adapter_with(
            researched=("drone_swarm", "nano_armor"))
        LIST_CACHE.store(caller, "tech",
                         adapter.list_instances(caller, ""))
        try:
            result = adapter.resolve_instance(caller, "#2")
            self.assertTrue(result.ok)
            self.assertEqual(result.target.key, "nano_armor")
        finally:
            LIST_CACHE.clear(caller)

    def test_resolve_trailing_player_scope(self):
        bob = Player("Bob", researched=("deep_scan",))
        caller = Player("Admin", known_players=(bob,))
        adapter, _system, _ = _adapter_with(caller=caller)
        result = adapter.resolve_instance(caller, "deep_scan Bob")
        self.assertTrue(result.ok)
        self.assertIs(result.target.player, bob)

    def test_resolve_not_held_real_tech_states_grant_state(self):
        """Requirement 7.9: a real technology the player does not hold
        errors stating the current grant state."""
        adapter, _system, caller = _adapter_with(researched=())
        result = adapter.resolve_instance(caller, "drone_swarm")
        self.assertFalse(result.ok)
        self.assertIn("does not hold", result.error)
        self.assertIn("not granted", result.error)
        self.assertIn("drone_swarm", result.error)

    def test_resolve_unknown_token_is_a_plain_not_found(self):
        adapter, _system, caller = _adapter_with(researched=())
        result = adapter.resolve_instance(caller, "warp_drive")
        self.assertFalse(result.ok)
        self.assertIn("warp_drive", result.error)
        self.assertNotIn("grant state", result.error)


# ------------------------------------------------------------------ #
#  create (grant) — Requirements 7.7, 7.9
# ------------------------------------------------------------------ #

class TestCreateGrant(unittest.TestCase):

    def test_grant_adds_through_the_research_path(self):
        adapter, _system, caller = _adapter_with()
        result = adapter.create(caller, "drone_swarm", {})
        self.assertTrue(result.ok)
        self.assertIn("drone_swarm", caller.db.researched_techs)
        self.assertEqual(result.instance.key, "drone_swarm")
        self.assertIs(result.instance.player, caller)

    def test_grant_recomputes_derived_bonuses_before_returning(self):
        """Requirement 7.7: tech_bonuses reflect the grant when the
        success result comes back."""
        adapter, _system, caller = _adapter_with()
        result = adapter.create(caller, "drone_swarm", {})
        self.assertTrue(result.ok)
        self.assertEqual(caller.db.tech_bonuses, {"damage": 5.0})

    def test_grant_targets_the_player_kwarg(self):
        bob = Player("Bob")
        adapter, _system, caller = _adapter_with()
        result = adapter.create(caller, "nano_armor", {"player": bob})
        self.assertTrue(result.ok)
        self.assertIn("nano_armor", bob.db.researched_techs)
        self.assertNotIn("nano_armor", caller.db.researched_techs)

    def test_double_grant_errors_stating_grant_state_no_change(self):
        """Requirement 7.9: already-held grant errors with the current
        grant state and changes nothing."""
        adapter, _system, caller = _adapter_with(
            researched=("drone_swarm",))
        adapter._system().recompute_tech_bonuses(caller)
        bonuses_before = dict(caller.db.tech_bonuses)
        result = adapter.create(caller, "drone_swarm", {})
        self.assertFalse(result.ok)
        self.assertIn("already holds", result.error)
        self.assertIn("granted", result.error)
        self.assertEqual(caller.db.researched_techs, {"drone_swarm"})
        self.assertEqual(caller.db.tech_bonuses, bonuses_before)

    def test_unresolved_def_token_creates_nothing(self):
        adapter, _system, caller = _adapter_with()
        result = adapter.create(caller, "warp_drive", {})
        self.assertFalse(result.ok)
        self.assertIn("warp_drive", result.error)
        self.assertEqual(caller.db.researched_techs, set())


# ------------------------------------------------------------------ #
#  delete (revoke) — Requirements 7.8, 7.9
# ------------------------------------------------------------------ #

class TestDeleteRevoke(unittest.TestCase):

    def test_revoke_removes_and_recomputes_bonuses(self):
        """Requirement 7.8: the researched set and the derived bonuses
        both reflect the revoke when the result comes back."""
        adapter, system, caller = _adapter_with(
            researched=("drone_swarm", "nano_armor"))
        system.recompute_tech_bonuses(caller)
        self.assertEqual(caller.db.tech_bonuses,
                         {"damage": 5.0, "damage_reduction": 2.0})
        grant = TechGrant(player=caller, key="drone_swarm",
                          name="Drone Swarm")
        result = adapter.delete(caller, grant)
        self.assertTrue(result.ok)
        self.assertEqual(caller.db.researched_techs, {"nano_armor"})
        self.assertEqual(caller.db.tech_bonuses,
                         {"damage_reduction": 2.0})

    def test_absent_revoke_errors_stating_grant_state_no_change(self):
        """Requirement 7.9: revoking a non-held tech errors with the
        current grant state and changes nothing."""
        adapter, _system, caller = _adapter_with(researched=())
        grant = TechGrant(player=caller, key="drone_swarm",
                          name="Drone Swarm")
        result = adapter.delete(caller, grant)
        self.assertFalse(result.ok)
        self.assertIn("does not hold", result.error)
        self.assertIn("not granted", result.error)
        self.assertEqual(caller.db.researched_techs, set())

    def test_grant_revoke_round_trip_restores_prior_state(self):
        adapter, _system, caller = _adapter_with()
        created = adapter.create(caller, "drone_swarm", {})
        self.assertTrue(created.ok)
        result = adapter.delete(caller, created.instance)
        self.assertTrue(result.ok)
        self.assertEqual(caller.db.researched_techs, set())
        self.assertEqual(caller.db.tech_bonuses, {})


# ------------------------------------------------------------------ #
#  show + definition plane
# ------------------------------------------------------------------ #

class TestReadAndDefPlane(unittest.TestCase):

    def test_read_report_shape_with_def_backed_info(self):
        adapter, _system, caller = _adapter_with(
            researched=("drone_swarm",))
        grant = adapter.resolve_instance(caller, "drone_swarm").target
        report = adapter.read(caller, grant)
        self.assertIn("Drone Swarm (drone_swarm)", report.header)
        self.assertIn("Admin", report.header)  # the holder
        joined = "\n".join(report.state_lines)
        self.assertIn("Private", joined)        # required rank
        self.assertIn("Wood 10", joined)        # resource cost
        self.assertIn("stat_bonus", joined)     # effect
        # No modifiable per-instance fields (Requirement 7.1).
        self.assertEqual(report.fields, [])
        self.assertIsNone(report.staleness_note)

    def test_def_registry_dict_serves_the_technologies_domain(self):
        adapter, _system, _caller = _adapter_with()
        registry_dict = adapter.def_registry_dict()
        self.assertEqual(set(registry_dict),
                         {"drone_swarm", "nano_armor", "deep_scan"})

    def test_def_resolve_delegates_to_resolve_technology(self):
        adapter, _system, _caller = _adapter_with()
        self.assertEqual(adapter.def_resolve("Nano Armor").key,
                         "nano_armor")
        self.assertEqual(adapter.def_resolve("deep").key, "deep_scan")
        self.assertIsNone(adapter.def_resolve("warp_drive"))

    def test_def_domain_is_technologies(self):
        self.assertEqual(TechnologyAdapter().def_domain, "technologies")


if __name__ == "__main__":
    unittest.main()
