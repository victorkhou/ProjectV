"""
Unit tests for the ItemAdapter (unified-admin-crud task 3.1).

Coverage:
- Grammar contract: full core-verb support, no opt-outs, the
  ``stats``→``show`` alias, empty extra verbs; registration in the
  AdapterRegistry (including ``register_all``) succeeds.
- Field schema correctness: ``instance_fields`` derives one FieldSpec per
  stat any loaded def's ``roll_spec`` bands, with dynamic bounds computed
  from the TARGET instance's own def band; ``definition_fields`` names
  only real ``ItemDef`` fields with sane kinds.
- Dynamic-bounds derivation from ``roll_spec``: per-instance (lo, hi),
  unbounded (None, None) for stats the target's def does not band.
- IQS re-stamp on ``update``: every roll-field write re-stamps the score
  through ``recompute_iqs`` before the success response (Requirement 7.6),
  with band clamping and the SetResult contract.
- Resolution scoping: caller's holdings by default, trailing [player]
  scoping, ``#N`` via the List_Cache.
- ``def_resolve`` delegation to the registry's ``resolve_item``.
- ``read``: ShowReport shape + staleness notes for stamped attributes
  differing from the current merged def (Requirement 10.3).

Requirements: 2.4, 3.1, 3.4, 3.5, 7.6
"""

import dataclasses
import unittest

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.item_adapter import ItemAdapter
from world.admin.resolution import LIST_CACHE
from world.admin.types import CORE_VERBS
from world.definitions import ItemDef


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

RIFLE_SPEC = {
    "stats": {
        "damage": {"min": 10.0, "max": 50.0, "weight": 1.0},
        "range": {"min": 2.0, "max": 6.0, "weight": 1.0},
    },
}

VEST_SPEC = {
    "stats": {
        "armor": {"min": 5.0, "max": 25.0, "weight": 1.0},
    },
}


def _rifle_def():
    return ItemDef(
        key="rifle", name="Rifle", slot="weapon", category="weapon",
        weapon_type="ranged", weight=3.0, roll_spec=RIFLE_SPEC,
    )


def _vest_def():
    return ItemDef(
        key="vest", name="Combat Vest", slot="torso", category="armor",
        weight=2.0, roll_spec=VEST_SPEC,
    )


def _fixed_def():
    return ItemDef(key="medkit", name="Medkit", category="consumable")


class FakeRegistry:
    """Registry double: ``items`` dict + the ``resolve_item`` matcher."""

    def __init__(self, defs):
        self.items = {d.key: d for d in defs}
        self.resolve_calls = []

    def resolve_item(self, token):
        self.resolve_calls.append(token)
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


class Player:
    """Player stub: id, key, contents (holdings), and a name search."""

    _next_id = 1

    def __init__(self, key, contents=None, known_players=()):
        self.id = Player._next_id
        Player._next_id += 1
        self.key = key
        self.contents = list(contents or [])
        self._known = {p.key.lower(): p for p in known_players}

    def search(self, name, **kwargs):
        return self._known.get(str(name).lower())


def _item(item_key, **extra):
    """A dict-shaped live item (the loot roller's dict factory shape)."""
    data = {"item_key": item_key}
    data.update(extra)
    return data


def _adapter(defs=None):
    return ItemAdapter(registry=FakeRegistry(defs or [
        _rifle_def(), _vest_def(), _fixed_def(),
    ]))


# ------------------------------------------------------------------ #
#  Grammar contract + registration
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):
    """Design per-entity matrix: @item supports everything; stats→show."""

    def test_supports_every_core_verb_with_no_opt_outs(self):
        adapter = _adapter()
        self.assertEqual(adapter.supported_verbs, CORE_VERBS)
        self.assertEqual(adapter.opt_outs, {})
        self.assertEqual(adapter.extra_verbs, {})

    def test_stats_alias_maps_to_show(self):
        self.assertEqual(_adapter().aliases, {"stats": "show"})

    def test_registers_cleanly_in_adapter_registry(self):
        registry = AdapterRegistry()
        registry.register(_adapter())
        self.assertIsNotNone(registry.get("item"))

    def test_register_all_includes_item_adapter(self):
        registry = register_all(AdapterRegistry())
        adapter = registry.get("item")
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, ItemAdapter)


# ------------------------------------------------------------------ #
#  Field schemas
# ------------------------------------------------------------------ #

class TestInstanceFieldSchema(unittest.TestCase):
    """Requirements 3.1, 3.4 — one FieldSpec per banded stat, dynamic
    bounds from the target instance's own def band."""

    def setUp(self):
        self.adapter = _adapter()
        self.fields = self.adapter.instance_fields()

    def test_every_banded_stat_across_defs_is_a_field(self):
        # rifle bands damage+range, vest bands armor; medkit is fixed.
        for stat in ("damage", "range", "armor"):
            self.assertIn(stat, self.fields)
            self.assertEqual(self.fields[stat].kind, "float")
            self.assertIsNotNone(self.fields[stat].dynamic_bounds)

    def test_rarity_is_an_enum_field(self):
        spec = self.fields["rarity"]
        self.assertEqual(spec.kind, "enum")
        self.assertIn("legendary", spec.enum_values)

    def test_dynamic_bounds_derive_from_the_targets_roll_spec(self):
        rifle = _item("rifle")
        lo, hi = self.fields["damage"].dynamic_bounds(rifle)
        self.assertEqual((lo, hi), (10.0, 50.0))
        lo, hi = self.fields["range"].dynamic_bounds(rifle)
        self.assertEqual((lo, hi), (2.0, 6.0))

    def test_dynamic_bounds_vary_per_instance(self):
        # armor is banded on the vest, not on the rifle: the same
        # FieldSpec resolves different bounds per target (Req 3.4).
        vest = _item("vest")
        self.assertEqual(self.fields["armor"].dynamic_bounds(vest),
                         (5.0, 25.0))
        rifle = _item("rifle")
        self.assertEqual(self.fields["armor"].dynamic_bounds(rifle),
                         (None, None))


class TestDefinitionFieldSchema(unittest.TestCase):
    """Requirement 3.1 — def-plane Field_Specs against real ItemDef."""

    def test_every_definition_field_is_a_real_itemdef_field(self):
        real = {f.name for f in dataclasses.fields(ItemDef)}
        for name in _adapter().definition_fields():
            self.assertIn(name, real)

    def test_kinds_and_bounds_are_sane(self):
        fields = _adapter().definition_fields()
        self.assertEqual(fields["weight"].kind, "float")
        self.assertEqual(fields["weight"].min_value, 0.0)
        self.assertEqual(fields["max_stack"].kind, "int")
        self.assertEqual(fields["max_stack"].min_value, 1)
        self.assertEqual(fields["weapon_type"].kind, "enum")
        self.assertEqual(set(fields["weapon_type"].enum_values),
                         {"melee", "ranged"})


# ------------------------------------------------------------------ #
#  update — IQS re-stamp, clamping, rejection
# ------------------------------------------------------------------ #

class TestUpdate(unittest.TestCase):
    """Requirements 3.4, 3.5, 7.6 — bounded write + IQS re-stamp."""

    def setUp(self):
        self.adapter = _adapter()
        self.rifle = _item(
            "rifle", rolled_stats={"damage": 30.0, "range": 4.0})
        self.caller = Player("admin")

    def test_in_band_write_applies_unchanged_and_restamps_iqs(self):
        result = self.adapter.update(self.caller, self.rifle, "damage", 50.0)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 50.0)
        self.assertFalse(result.clamped)
        self.assertEqual(self.rifle["rolled_stats"]["damage"], 50.0)
        # IQS re-stamped BEFORE the success response (Req 7.6):
        # damage at max (q=1.0), range at 4.0 (q=0.5) → round(75).
        self.assertEqual(self.rifle["iqs"], 75)

    def test_out_of_band_write_clamps_to_nearest_bound(self):
        result = self.adapter.update(self.caller, self.rifle, "damage", 999)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 50.0)
        self.assertTrue(result.clamped)
        self.assertEqual(self.rifle["rolled_stats"]["damage"], 50.0)

    def test_update_is_idempotent(self):
        self.adapter.update(self.caller, self.rifle, "damage", 999)
        first = (dict(self.rifle["rolled_stats"]), self.rifle["iqs"])
        self.adapter.update(self.caller, self.rifle, "damage", 999)
        self.assertEqual(
            (dict(self.rifle["rolled_stats"]), self.rifle["iqs"]), first)

    def test_unbanded_stat_is_rejected_with_settable_list(self):
        result = self.adapter.update(self.caller, self.rifle, "armor", 10)
        self.assertFalse(result.ok)
        self.assertIn("damage", result.error)
        self.assertIn("range", result.error)
        self.assertNotIn("armor", self.rifle.get("rolled_stats", {}))

    def test_non_numeric_value_is_rejected(self):
        result = self.adapter.update(self.caller, self.rifle, "damage", "x")
        self.assertFalse(result.ok)
        self.assertEqual(self.rifle["rolled_stats"]["damage"], 30.0)

    def test_rarity_writes_the_named_tier(self):
        result = self.adapter.update(self.caller, self.rifle, "rarity", "Epic")
        self.assertTrue(result.ok)
        self.assertEqual(self.rifle["rarity"], "epic")

    def test_unknown_rarity_is_rejected(self):
        result = self.adapter.update(self.caller, self.rifle, "rarity", "shiny")
        self.assertFalse(result.ok)
        self.assertNotIn("rarity", self.rifle)


# ------------------------------------------------------------------ #
#  Resolution scoping (instance plane)
# ------------------------------------------------------------------ #

class TestResolutionScoping(unittest.TestCase):
    """Requirement 2.4 — holdings scope defaults to the caller; a
    trailing [player] token re-scopes; #N uses the List_Cache."""

    def setUp(self):
        self.adapter = _adapter()
        self.rifle = _item("rifle")
        self.vest = _item("vest")
        self.other = Player("Bob", contents=[self.vest])
        self.caller = Player("admin", contents=[self.rifle],
                             known_players=[self.other])
        LIST_CACHE.clear()

    def tearDown(self):
        LIST_CACHE.clear()

    def test_resolves_in_the_callers_own_holdings_by_default(self):
        result = self.adapter.resolve_instance(self.caller, "rifle")
        self.assertTrue(result.ok)
        self.assertIs(result.target, self.rifle)

    def test_trailing_player_token_scopes_to_that_players_holdings(self):
        result = self.adapter.resolve_instance(self.caller, "vest Bob")
        self.assertTrue(result.ok)
        self.assertIs(result.target, self.vest)

    def test_token_outside_any_scope_fails(self):
        result = self.adapter.resolve_instance(self.caller, "vest")
        self.assertFalse(result.ok)

    def test_index_token_resolves_through_the_list_cache(self):
        rows = self.adapter.list_instances(self.caller, "")
        LIST_CACHE.store(self.caller, "item", rows)
        result = self.adapter.resolve_instance(self.caller, "#1")
        self.assertTrue(result.ok)
        self.assertIs(result.target, self.rifle)

    def test_list_instances_scopes_to_a_trailing_player_token(self):
        rows = self.adapter.list_instances(self.caller, "Bob")
        self.assertEqual([r.key for r in rows], ["vest"])

    def test_list_instances_defaults_to_the_caller(self):
        rows = self.adapter.list_instances(self.caller, "")
        self.assertEqual([r.key for r in rows], ["rifle"])


# ------------------------------------------------------------------ #
#  Definition scope
# ------------------------------------------------------------------ #

class TestDefinitionScope(unittest.TestCase):
    """Requirement 2.6-adjacent — def_resolve delegates to resolve_item;
    def_registry_dict serves the live items dict."""

    def setUp(self):
        self.adapter = _adapter()
        self.registry = self.adapter._registry

    def test_def_resolve_delegates_to_resolve_item(self):
        result = self.adapter.def_resolve("rifle")
        self.assertIs(result, self.registry.items["rifle"])
        self.assertIn("rifle", self.registry.resolve_calls)

    def test_def_resolve_prefix_match_via_registry_matcher(self):
        self.assertIs(self.adapter.def_resolve("Combat"),
                      self.registry.items["vest"])

    def test_def_resolve_miss_returns_none(self):
        self.assertIsNone(self.adapter.def_resolve("bogus"))

    def test_def_registry_dict_is_the_items_dict(self):
        self.assertIs(self.adapter.def_registry_dict(),
                      self.registry.items)


# ------------------------------------------------------------------ #
#  read — ShowReport + staleness notes
# ------------------------------------------------------------------ #

class TestRead(unittest.TestCase):
    """Requirement 10.3 — staleness notes for stamped attributes that
    differ from the current merged definition."""

    def setUp(self):
        self.adapter = _adapter()
        self.caller = Player("admin")

    def test_report_lists_banded_stats_as_modifiable_fields(self):
        rifle = _item("rifle", rolled_stats={"damage": 30.0})
        report = self.adapter.read(self.caller, rifle)
        names = [spec.name for spec, _, _ in report.fields]
        self.assertIn("damage", names)
        self.assertIn("range", names)
        self.assertIn("rarity", names)
        self.assertIn("rifle", report.header)

    def test_no_staleness_note_when_stamps_match_the_def(self):
        rifle = _item("rifle", weight=3.0, category="weapon")
        report = self.adapter.read(self.caller, rifle)
        self.assertIsNone(report.staleness_note)

    def test_staleness_note_names_attr_stamped_and_current_values(self):
        # Stamped at spawn with weight 3.0; the merged def now says 5.0.
        rifle = _item("rifle", weight=3.0)
        self.adapter._registry.items["rifle"] = dataclasses.replace(
            _rifle_def(), weight=5.0)
        report = self.adapter.read(self.caller, rifle)
        self.assertIsNotNone(report.staleness_note)
        self.assertIn("weight", report.staleness_note)
        self.assertIn("3.0", report.staleness_note)
        self.assertIn("5.0", report.staleness_note)

    def test_staleness_note_covers_stamped_base_stat_modifiers(self):
        vest = _item("vest", stat_modifiers={"armor": 8.0})
        self.adapter._registry.items["vest"] = dataclasses.replace(
            _vest_def(), stat_modifiers={"armor": 12.0})
        report = self.adapter.read(self.caller, vest)
        self.assertIsNotNone(report.staleness_note)
        self.assertIn("armor", report.staleness_note)


if __name__ == "__main__":
    unittest.main()
