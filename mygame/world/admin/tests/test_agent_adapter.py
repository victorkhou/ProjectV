"""
Unit tests for the AgentAdapter (unified-admin-crud task 5.2).

Coverage:
- Grammar contract: the five instance verbs supported; ALL five def
  verbs opted out with the no-YAML-definition-domain reason (plus a
  pointer to the supported path); the ``create``→``spawn`` migration
  alias; Admin-tier verb escalations; registration in the
  AdapterRegistry (including ``register_all``) succeeds.
- Field schema: hp with dynamic bounds from the TARGET agent's own
  ``hp_max``; hp_max/kills/deaths static floors; empty definition
  schema.
- Resolution: agent-id exact-key tier, name and prefix tiers, ``#N``
  via the List_Cache, trailing ``[player]`` scoping (the legacy
  ``<id> <player>`` addressing), ambiguity.
- CRUD hooks all write via the AgentSystem single-writer path
  (Requirement 3.5): create → ``admin_create_agent`` (count honored),
  update → ``admin_set_agent_field`` (with the SetResult clamp
  contract), delete → ``admin_destroy_agent``.
- ``read``: ShowReport shape; no staleness note (no def domain);
  ``def_registry_dict``/``def_resolve`` return None.

Requirements: 2.4, 3.5, 7.3, 11.5
"""

import unittest

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.agent_adapter import AgentAdapter
from world.admin.resolution import LIST_CACHE


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

class _Db:
    """Attribute-bag double for an agent's ``db``."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):  # unset fields read as None
        return None


class FakeAgent:
    def __init__(self, agent_id, owner=None, role="soldier", hp=80,
                 hp_max=100, kills=0, deaths=0):
        self.key = f"Agent-{agent_id}"
        self.db = _Db(agent_id=agent_id, owner=owner, role=role, hp=hp,
                      hp_max=hp_max, kills=kills, deaths=deaths)


class FakeAgentSystem:
    """AgentSystem double exposing the admin single-writer paths."""

    def __init__(self, rosters=None, fail_create=False, fail_write=False,
                 fail_delete=False):
        #: player -> list of agents
        self._rosters = rosters or {}
        self.fail_create = fail_create
        self.fail_write = fail_write
        self.fail_delete = fail_delete
        self.calls = []

    def get_agents(self, player):
        return list(self._rosters.get(player, []))

    def admin_create_agent(self, player):
        self.calls.append(("create", player))
        if self.fail_create:
            return None
        roster = self._rosters.setdefault(player, [])
        next_id = max(
            (a.db.agent_id for a in roster), default=0
        ) + 1
        agent = FakeAgent(next_id, owner=player)
        roster.append(agent)
        return agent

    def admin_set_agent_field(self, agent, field, value):
        self.calls.append(("set", agent, field, value))
        if self.fail_write:
            return False
        setattr(agent.db, field, value)
        return True

    def admin_destroy_agent(self, agent):
        self.calls.append(("destroy", agent))
        if self.fail_delete:
            return False
        for roster in self._rosters.values():
            if agent in roster:
                roster.remove(agent)
        return True


class Player:
    _next_id = 1

    def __init__(self, key, known_players=()):
        self.id = Player._next_id
        Player._next_id += 1
        self.key = key
        self._known = {p.key.lower(): p for p in known_players}

    def search(self, name, **kwargs):
        return self._known.get(str(name).lower())


def _adapter_with(caller_agents=(), caller=None, **kwargs):
    caller = caller or Player("Admin")
    system = FakeAgentSystem(rosters={caller: list(caller_agents)}, **kwargs)
    return AgentAdapter(agent_system=system), system, caller


# ------------------------------------------------------------------ #
#  Grammar contract (Requirements 7.3, 11.5)
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):

    def test_instance_verbs_supported_def_verbs_opted_out(self):
        adapter = AgentAdapter()
        self.assertEqual(
            adapter.supported_verbs,
            frozenset({"list", "spawn", "show", "set", "destroy"}),
        )
        def_verbs = {"def list", "def show", "def set", "def reset",
                     "def diff"}
        self.assertEqual(set(adapter.opt_outs), def_verbs)

    def test_opt_out_reason_names_missing_def_domain_with_pointer(self):
        adapter = AgentAdapter()
        for verb, reason in adapter.opt_outs.items():
            self.assertTrue(reason.strip(), f"empty reason for {verb}")
            self.assertIn("no YAML definition domain", reason)
            # Pointer to the supported path (Requirement 1.5).
            self.assertIn("instance verbs", reason)

    def test_create_spawn_alias_installed(self):
        self.assertEqual(AgentAdapter().aliases, {"create": "spawn"})

    def test_admin_tier_escalations_for_mutating_verbs(self):
        perms = AgentAdapter().verb_perms
        for verb in ("spawn", "set", "destroy"):
            self.assertEqual(perms[verb], "Admin")


# ------------------------------------------------------------------ #
#  Field schema
# ------------------------------------------------------------------ #

class TestFieldSchema(unittest.TestCase):

    def test_instance_fields_names_and_kinds(self):
        fields = AgentAdapter().instance_fields()
        self.assertEqual(set(fields), {"hp", "hp_max", "kills", "deaths"})
        for spec in fields.values():
            self.assertEqual(spec.kind, "int")

    def test_hp_dynamic_bounds_follow_the_targets_hp_max(self):
        spec = AgentAdapter().instance_fields()["hp"]
        agent = FakeAgent(1, hp_max=140)
        self.assertEqual(spec.dynamic_bounds(agent), (0, 140))

    def test_hp_bounds_unbounded_high_without_hp_max(self):
        spec = AgentAdapter().instance_fields()["hp"]
        agent = FakeAgent(1, hp_max=None)
        self.assertEqual(spec.dynamic_bounds(agent), (0, None))

    def test_static_floors(self):
        fields = AgentAdapter().instance_fields()
        self.assertEqual(fields["hp_max"].min_value, 1)
        self.assertEqual(fields["kills"].min_value, 0)
        self.assertEqual(fields["deaths"].min_value, 0)

    def test_definition_schema_is_empty(self):
        self.assertEqual(AgentAdapter().definition_fields(), {})


# ------------------------------------------------------------------ #
#  Listing + resolution (Requirement 2.4)
# ------------------------------------------------------------------ #

class TestListingAndResolution(unittest.TestCase):

    def test_list_rows_indexed_and_keyed_by_agent_id(self):
        agents = [FakeAgent(1), FakeAgent(2, role="medic")]
        adapter, _system, caller = _adapter_with(agents)
        rows = adapter.list_instances(caller, "")
        self.assertEqual([r.index for r in rows], [1, 2])
        self.assertEqual([r.key for r in rows], ["1", "2"])
        self.assertEqual(rows[0].name, "Agent-1")

    def test_list_filter_matches_role(self):
        agents = [FakeAgent(1, role="soldier"), FakeAgent(2, role="medic")]
        adapter, _system, caller = _adapter_with(agents)
        rows = adapter.list_instances(caller, "medic")
        self.assertEqual([r.key for r in rows], ["2"])

    def test_list_trailing_player_token_scopes_the_roster(self):
        bob = Player("Bob")
        caller = Player("Admin", known_players=(bob,))
        adapter, system, _ = _adapter_with((), caller=caller)
        system._rosters[bob] = [FakeAgent(7, owner=bob)]
        rows = adapter.list_instances(caller, "Bob")
        self.assertEqual([r.key for r in rows], ["7"])

    def test_resolve_by_exact_agent_id(self):
        agents = [FakeAgent(1), FakeAgent(2)]
        adapter, _system, caller = _adapter_with(agents)
        result = adapter.resolve_instance(caller, "2")
        self.assertTrue(result.ok)
        self.assertIs(result.target, agents[1])

    def test_resolve_by_name_and_prefix(self):
        agents = [FakeAgent(3)]
        adapter, _system, caller = _adapter_with(agents)
        self.assertIs(
            adapter.resolve_instance(caller, "agent-3").target, agents[0]
        )
        self.assertIs(
            adapter.resolve_instance(caller, "Agent-").target, agents[0]
        )

    def test_resolve_ambiguous_prefix_errors_listing_candidates(self):
        agents = [FakeAgent(1), FakeAgent(2)]
        adapter, _system, caller = _adapter_with(agents)
        result = adapter.resolve_instance(caller, "Agent")
        self.assertFalse(result.ok)
        self.assertIn("ambiguous", result.error)

    def test_resolve_index_token_via_list_cache(self):
        agents = [FakeAgent(1), FakeAgent(2)]
        adapter, _system, caller = _adapter_with(agents)
        LIST_CACHE.store(caller, "agent",
                         adapter.list_instances(caller, ""))
        try:
            result = adapter.resolve_instance(caller, "#2")
            self.assertTrue(result.ok)
            self.assertIs(result.target, agents[1])
        finally:
            LIST_CACHE.clear(caller)

    def test_resolve_trailing_player_scope_legacy_id_player_form(self):
        bob = Player("Bob")
        caller = Player("Admin", known_players=(bob,))
        adapter, system, _ = _adapter_with((), caller=caller)
        bobs_agent = FakeAgent(4, owner=bob)
        system._rosters[bob] = [bobs_agent]
        result = adapter.resolve_instance(caller, "4 Bob")
        self.assertTrue(result.ok)
        self.assertIs(result.target, bobs_agent)

    def test_resolve_no_match_errors(self):
        adapter, _system, caller = _adapter_with(())
        result = adapter.resolve_instance(caller, "99")
        self.assertFalse(result.ok)
        self.assertIn("99", result.error)


# ------------------------------------------------------------------ #
#  CRUD hooks — all writes via AgentSystem (Requirement 3.5)
# ------------------------------------------------------------------ #

class TestCreate(unittest.TestCase):

    def test_create_goes_through_admin_create_agent(self):
        adapter, system, caller = _adapter_with(())
        result = adapter.create(caller, "Admin", {"player": caller})
        self.assertTrue(result.ok)
        self.assertEqual(result.created_ids, (1,))
        self.assertEqual(system.calls, [("create", caller)])

    def test_create_count_creates_several(self):
        adapter, system, caller = _adapter_with(())
        result = adapter.create(
            caller, "Admin", {"player": caller, "count": 3}
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.created_ids, (1, 2, 3))

    def test_create_failure_reported(self):
        adapter, _system, caller = _adapter_with((), fail_create=True)
        result = adapter.create(caller, "Admin", {"player": caller})
        self.assertFalse(result.ok)
        self.assertIn("creation path failed", result.error)


class TestUpdate(unittest.TestCase):

    def test_update_writes_via_single_writer_path(self):
        agent = FakeAgent(1, hp=50, hp_max=100)
        adapter, system, caller = _adapter_with([agent])
        result = adapter.update(caller, agent, "hp", 75)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 75)
        self.assertFalse(result.clamped)
        self.assertIn(("set", agent, "hp", 75), system.calls)
        self.assertEqual(agent.db.hp, 75)

    def test_update_clamps_hp_into_the_targets_hp_max(self):
        agent = FakeAgent(1, hp=50, hp_max=100)
        adapter, _system, caller = _adapter_with([agent])
        result = adapter.update(caller, agent, "hp", 500)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 100)
        self.assertTrue(result.clamped)
        self.assertEqual(agent.db.hp, 100)

    def test_update_clamped_iff_applied_differs_from_requested(self):
        agent = FakeAgent(1, hp_max=100)
        adapter, _system, caller = _adapter_with([agent])
        in_bounds = adapter.update(caller, agent, "hp", 100)
        self.assertFalse(in_bounds.clamped)
        below = adapter.update(caller, agent, "kills", -5)
        self.assertTrue(below.clamped)
        self.assertEqual(below.applied, 0)

    def test_update_unknown_field_rejected_naming_settable(self):
        agent = FakeAgent(1)
        adapter, system, caller = _adapter_with([agent])
        result = adapter.update(caller, agent, "role", "medic")
        self.assertFalse(result.ok)
        self.assertIn("hp", result.error)
        self.assertNotIn(("set", agent, "role", "medic"), system.calls)

    def test_update_non_numeric_value_rejected(self):
        agent = FakeAgent(1)
        adapter, _system, caller = _adapter_with([agent])
        result = adapter.update(caller, agent, "hp", "lots")
        self.assertFalse(result.ok)
        self.assertIn("number", result.error)

    def test_update_write_failure_reports_unchanged(self):
        agent = FakeAgent(1, hp=50, hp_max=100)
        adapter, _system, caller = _adapter_with([agent], fail_write=True)
        result = adapter.update(caller, agent, "hp", 60)
        self.assertFalse(result.ok)
        self.assertIn("unchanged", result.error)
        self.assertEqual(agent.db.hp, 50)


class TestDelete(unittest.TestCase):

    def test_delete_goes_through_admin_destroy_agent(self):
        agent = FakeAgent(1)
        adapter, system, caller = _adapter_with([agent])
        result = adapter.delete(caller, agent)
        self.assertTrue(result.ok)
        self.assertIn(("destroy", agent), system.calls)

    def test_delete_failure_reported(self):
        agent = FakeAgent(1)
        adapter, _system, caller = _adapter_with([agent], fail_delete=True)
        result = adapter.delete(caller, agent)
        self.assertFalse(result.ok)
        self.assertIn("deletion path failed", result.error)


# ------------------------------------------------------------------ #
#  show + definition plane
# ------------------------------------------------------------------ #

class TestReadAndDefPlane(unittest.TestCase):

    def test_read_report_shape(self):
        owner = Player("Bob")
        agent = FakeAgent(2, owner=owner, role="medic", hp=40, hp_max=90,
                          kills=3, deaths=1)
        adapter, _system, caller = _adapter_with([agent])
        report = adapter.read(caller, agent)
        self.assertIn("Agent #2", report.header)
        self.assertIn("Bob", report.header)
        joined = "\n".join(report.state_lines)
        self.assertIn("medic", joined)
        self.assertIn("40/90", joined)
        self.assertIn("Kills: 3", joined)
        self.assertEqual(
            {spec.name for spec, _value, _ovr in report.fields},
            {"hp", "hp_max", "kills", "deaths"},
        )
        self.assertIsNone(report.staleness_note)

    def test_no_definition_registry_or_resolver(self):
        adapter = AgentAdapter()
        self.assertIsNone(adapter.def_registry_dict())
        self.assertIsNone(adapter.def_resolve("anything"))


if __name__ == "__main__":
    unittest.main()
