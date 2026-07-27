"""
Unit tests for the PlayerAdapter (unified-admin-crud task 7.3).

Coverage:
- Grammar contract: ``list``/``show``/``set`` supported; ``spawn`` opted
  out (players register), ``destroy`` opted out with the pointer to the
  existing ``@obliterate`` flow, ALL five def verbs opted out (no YAML
  definition domain); the ``level``/``rank`` Migration_Aliases to
  ``set`` (Requirement 11.5); the Admin-tier ``set`` escalation
  (Requirement 8.7); registration in the AdapterRegistry (including
  ``register_all``) succeeds.
- Field schema: ``level`` int with STATIC bounds 1–100 (MAX_LEVEL);
  ``rank`` enum over the numeric rank ids 1–NUM_RANKS; empty definition
  schema.
- Resolution: ``me``/``self`` → the caller, exact-key/name/prefix tiers
  over the enumerated players, ``#N`` via the List_Cache, ambiguity,
  fallback to the caller-scoped player search.
- ``update`` writes through the EXISTING progression path the legacy
  verbs used (Requirement 3.5): XP re-stamped via
  ``RankSystem.xp_for_level``, ``db.level``/``db.rank_level`` written,
  ``check_promotion`` recompute observed; level defensively clamped
  (SetResult contract); rank jumps to the band's first level;
  idempotence (Requirement 3.6).
- ``read``: ShowReport shape; no staleness note (no def domain);
  ``def_registry_dict``/``def_resolve`` return None; ``create``/
  ``delete`` refuse defensively.

Requirements: 1.5, 11.5, 11.6
"""

import unittest
from types import SimpleNamespace

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.player_adapter import PlayerAdapter
from world.admin.resolution import LIST_CACHE
from world.admin.types import CORE_VERBS
from world.constants import MAX_LEVEL, NUM_RANKS
from world.systems.rank_system import level_range_for_rank, rank_from_level


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

class _Db:
    """Attribute-bag double for a player's ``db``."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):  # unset fields read as None
        return None


class FakePlayer:
    _next_id = 1

    def __init__(self, key, level=1, rank_level=1, combat_xp=0,
                 known_players=()):
        self.id = FakePlayer._next_id
        FakePlayer._next_id += 1
        self.key = key
        self.db = _Db(level=level, rank_level=rank_level,
                      combat_xp=combat_xp)
        self._known = {p.key.lower(): p for p in known_players}

    def search(self, name, **kwargs):
        return self._known.get(str(name).lower())


class FakeRankSystem:
    """RankSystem double exposing the progression single-writer hooks."""

    def __init__(self):
        self.promotions = []

    def xp_for_level(self, level):
        return level * 100

    def check_promotion(self, player):
        self.promotions.append(player)


def _registry_with_ranks():
    """DataRegistry double carrying a ``ranks`` list (level + name)."""
    return SimpleNamespace(ranks=[
        SimpleNamespace(level=1, name="Recruit"),
        SimpleNamespace(level=2, name="Private"),
        SimpleNamespace(level=3, name="Corporal"),
    ])


def _adapter_with(players=(), rank_system=None, registry=None):
    return PlayerAdapter(
        rank_system=rank_system,
        registry=registry if registry is not None else _registry_with_ranks(),
        players_provider=lambda: list(players),
    )


# ------------------------------------------------------------------ #
#  Grammar contract (Requirements 1.5, 11.5)
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):

    def setUp(self):
        self.adapter = _adapter_with()

    def test_every_core_verb_is_supported_or_opted_out(self):
        accounted = self.adapter.supported_verbs | set(self.adapter.opt_outs)
        self.assertEqual(CORE_VERBS - accounted, set())

    def test_supported_verbs_are_list_show_set(self):
        self.assertEqual(self.adapter.supported_verbs,
                         frozenset({"list", "show", "set"}))

    def test_spawn_opt_out_points_at_registration(self):
        reason = self.adapter.opt_outs["spawn"]
        self.assertTrue(reason.strip())
        self.assertIn("register", reason)

    def test_destroy_opt_out_points_at_obliterate(self):
        reason = self.adapter.opt_outs["destroy"]
        self.assertTrue(reason.strip())
        self.assertIn("@obliterate", reason)

    def test_all_def_verbs_opted_out_with_no_domain_reason(self):
        for verb in ("def list", "def show", "def set", "def reset",
                     "def diff"):
            reason = self.adapter.opt_outs[verb]
            self.assertTrue(reason.strip())
            self.assertIn("no YAML definition domain", reason)

    def test_exactly_the_matrix_aliases_installed(self):
        # Requirement 11.5: the old level/rank verb forms → set.
        self.assertEqual(self.adapter.aliases,
                         {"level": "set", "rank": "set"})

    def test_set_escalated_to_admin(self):
        # Requirement 8.7: the legacy level/rank verbs were Admin-gated.
        self.assertEqual(self.adapter.verb_perms.get("set"), "Admin")

    def test_registers_cleanly_in_the_adapter_registry(self):
        registry = AdapterRegistry()
        registry.register(self.adapter)
        self.assertIs(registry.get("player"), self.adapter)

    def test_register_all_includes_the_player_adapter(self):
        registry = register_all(AdapterRegistry())
        self.assertIsInstance(registry.get("player"), PlayerAdapter)


# ------------------------------------------------------------------ #
#  Field schema
# ------------------------------------------------------------------ #

class TestFieldSchema(unittest.TestCase):

    def setUp(self):
        self.fields = _adapter_with().instance_fields()

    def test_level_is_int_with_static_bounds_1_to_100(self):
        spec = self.fields["level"]
        self.assertEqual(spec.kind, "int")
        self.assertEqual(spec.min_value, 1)
        self.assertEqual(spec.max_value, MAX_LEVEL)
        self.assertEqual(spec.max_value, 100)
        self.assertIsNone(spec.dynamic_bounds)

    def test_rank_is_an_enum_over_the_numeric_rank_ids(self):
        spec = self.fields["rank"]
        self.assertEqual(spec.kind, "enum")
        self.assertEqual(
            spec.enum_values,
            tuple(str(i) for i in range(1, NUM_RANKS + 1)),
        )

    def test_no_definition_fields(self):
        self.assertEqual(_adapter_with().definition_fields(), {})


# ------------------------------------------------------------------ #
#  Listing + resolution
# ------------------------------------------------------------------ #

class TestListing(unittest.TestCase):

    def setUp(self):
        self.bob = FakePlayer("Bob", level=5, rank_level=1)
        self.eve = FakePlayer("Eve", level=20, rank_level=3)
        self.adapter = _adapter_with(players=(self.eve, self.bob))

    def test_rows_are_indexed_and_sorted_by_key(self):
        rows = self.adapter.list_instances(self.bob, "")
        self.assertEqual([r.index for r in rows], [1, 2])
        self.assertEqual([r.key for r in rows], ["Bob", "Eve"])

    def test_row_summary_carries_progression_state(self):
        rows = self.adapter.list_instances(self.bob, "")
        self.assertIn("level 5", rows[0].summary)
        self.assertIn("Recruit", rows[0].summary)

    def test_filter_is_a_key_substring(self):
        rows = self.adapter.list_instances(self.bob, "ev")
        self.assertEqual([r.key for r in rows], ["Eve"])

    def test_no_provider_and_no_db_lists_nothing(self):
        adapter = PlayerAdapter(registry=_registry_with_ranks())
        self.assertEqual(adapter.list_instances(self.bob, ""), [])


class TestResolution(unittest.TestCase):

    def setUp(self):
        LIST_CACHE.clear()
        self.bob = FakePlayer("Bob")
        self.eve = FakePlayer("Eve")
        self.admin = FakePlayer("Admin", known_players=(self.bob, self.eve))
        self.adapter = _adapter_with(
            players=(self.admin, self.bob, self.eve))

    def test_me_resolves_to_the_caller(self):
        for token in ("me", "self", "ME"):
            res = self.adapter.resolve_instance(self.admin, token)
            self.assertTrue(res.ok)
            self.assertIs(res.target, self.admin)

    def test_exact_key_tier(self):
        res = self.adapter.resolve_instance(self.admin, "Bob")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.bob)

    def test_case_insensitive_prefix_tier(self):
        res = self.adapter.resolve_instance(self.admin, "ev")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.eve)

    def test_ambiguous_prefix_errors_listing_candidates(self):
        eva = FakePlayer("Eva")
        adapter = _adapter_with(players=(self.eve, eva))
        res = adapter.resolve_instance(self.admin, "ev")
        self.assertFalse(res.ok)
        self.assertIn("ambiguous", res.error)

    def test_index_token_resolves_via_the_list_cache(self):
        rows = self.adapter.list_instances(self.admin, "")
        LIST_CACHE.store(self.admin, "player", rows)
        res = self.adapter.resolve_instance(self.admin, "#2")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.bob)  # sorted: Admin, Bob, Eve

    def test_index_without_a_cache_instructs_list_first(self):
        res = self.adapter.resolve_instance(self.admin, "#1")
        self.assertFalse(res.ok)
        self.assertIn("list", res.error)

    def test_falls_back_to_the_caller_scoped_player_search(self):
        # No enumerable players (stubbed environment) — the caller's own
        # search path still resolves, like every legacy admin command.
        adapter = _adapter_with(players=())
        res = adapter.resolve_instance(self.admin, "Bob")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.bob)

    def test_unresolvable_token_errors(self):
        res = self.adapter.resolve_instance(self.admin, "Nobody")
        self.assertFalse(res.ok)
        self.assertIn("Nobody", res.error)


# ------------------------------------------------------------------ #
#  update — the existing progression write path (R3.5, R3.6)
# ------------------------------------------------------------------ #

class TestUpdateLevel(unittest.TestCase):

    def setUp(self):
        self.bob = FakePlayer("Bob")
        self.system = FakeRankSystem()
        self.adapter = _adapter_with(players=(self.bob,),
                                     rank_system=self.system)

    def test_level_writes_level_rank_and_xp_through_the_system(self):
        result = self.adapter.update(None, self.bob, "level", 5)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 5)
        self.assertFalse(result.clamped)
        self.assertEqual(self.bob.db.level, 5)
        self.assertEqual(self.bob.db.rank_level, rank_from_level(5))
        self.assertEqual(self.bob.db.combat_xp,
                         self.system.xp_for_level(5))
        # The rank-event recompute ran before the response (legacy path).
        self.assertEqual(self.system.promotions, [self.bob])

    def test_level_defensively_clamps_into_1_to_100(self):
        result = self.adapter.update(None, self.bob, "level", 150)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, MAX_LEVEL)
        self.assertTrue(result.clamped)
        self.assertEqual(self.bob.db.level, MAX_LEVEL)

        result = self.adapter.update(None, self.bob, "level", 0)
        self.assertEqual(result.applied, 1)
        self.assertTrue(result.clamped)

    def test_level_without_a_rank_system_still_writes_the_fields(self):
        adapter = _adapter_with(players=(self.bob,), rank_system=None)
        result = adapter.update(None, self.bob, "level", 7)
        self.assertTrue(result.ok)
        self.assertEqual(self.bob.db.level, 7)
        self.assertEqual(self.bob.db.rank_level, rank_from_level(7))

    def test_level_set_is_idempotent(self):
        # Requirement 3.6: applying the same set twice = applying once.
        self.adapter.update(None, self.bob, "level", 42)
        snapshot = (self.bob.db.level, self.bob.db.rank_level,
                    self.bob.db.combat_xp)
        self.adapter.update(None, self.bob, "level", 42)
        self.assertEqual(
            (self.bob.db.level, self.bob.db.rank_level,
             self.bob.db.combat_xp),
            snapshot,
        )

    def test_non_numeric_level_errors_without_state_change(self):
        result = self.adapter.update(None, self.bob, "level", "lots")
        self.assertFalse(result.ok)
        self.assertEqual(self.bob.db.level, 1)


class TestUpdateRank(unittest.TestCase):

    def setUp(self):
        self.bob = FakePlayer("Bob")
        self.system = FakeRankSystem()
        self.adapter = _adapter_with(players=(self.bob,),
                                     rank_system=self.system)

    def test_rank_jumps_to_the_bands_first_level(self):
        result = self.adapter.update(None, self.bob, "rank", "3")
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 3)
        self.assertEqual(self.bob.db.rank_level, 3)
        expected_level, _ = level_range_for_rank(3)
        self.assertEqual(self.bob.db.level, expected_level)
        self.assertEqual(self.bob.db.combat_xp,
                         self.system.xp_for_level(expected_level))
        self.assertEqual(self.system.promotions, [self.bob])

    def test_rank_outside_the_enum_errors_listing_valid_values(self):
        result = self.adapter.update(
            None, self.bob, "rank", str(NUM_RANKS + 1))
        self.assertFalse(result.ok)
        self.assertIn("valid values", result.error)
        self.assertEqual(self.bob.db.rank_level, 1)  # unchanged


class TestUpdateGuards(unittest.TestCase):

    def test_unknown_field_names_the_settable_fields(self):
        adapter = _adapter_with()
        result = adapter.update(None, FakePlayer("Bob"), "xp", 999)
        self.assertFalse(result.ok)
        self.assertIn("level", result.error)
        self.assertIn("rank", result.error)

    def test_non_player_target_is_rejected(self):
        adapter = _adapter_with()
        result = adapter.update(None, object(), "level", 5)
        self.assertFalse(result.ok)
        self.assertIn("not a valid player", result.error)


# ------------------------------------------------------------------ #
#  read / defensive refusals / def scope
# ------------------------------------------------------------------ #

class TestReadAndRefusals(unittest.TestCase):

    def setUp(self):
        self.bob = FakePlayer("Bob", level=12, rank_level=3,
                              combat_xp=1200)
        self.adapter = _adapter_with(players=(self.bob,))

    def test_read_renders_the_progression_readout(self):
        report = self.adapter.read(None, self.bob)
        self.assertIn("Bob", report.header)
        self.assertIn("Corporal", report.header)
        self.assertIn("Level: 12", report.state_lines[0])
        self.assertIn("XP: 1200", report.state_lines[0])
        self.assertEqual([spec.name for spec, _, _ in report.fields],
                         ["level", "rank"])
        self.assertIsNone(report.staleness_note)

    def test_create_refuses_defensively(self):
        result = self.adapter.create(None, "anything", {})
        self.assertFalse(result.ok)

    def test_delete_refuses_defensively(self):
        result = self.adapter.delete(None, self.bob)
        self.assertFalse(result.ok)
        self.assertIn("@obliterate", result.error)

    def test_no_definition_registry_or_resolver(self):
        self.assertIsNone(self.adapter.def_registry_dict())
        self.assertIsNone(self.adapter.def_resolve("anything"))


if __name__ == "__main__":
    unittest.main()
