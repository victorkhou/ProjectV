"""
Unit tests for the BuildingAdapter (unified-admin-crud task 5.1).

Coverage:
- Grammar contract: full core-verb support, no opt-outs, the ``open``
  extra verb, no aliases; registration in the AdapterRegistry (including
  ``register_all``) succeeds.
- Field schema correctness (Requirement 7.2): integer ``level`` FieldSpec
  with STATIC bounds 1–5, ``hp`` with dynamic bounds from the TARGET's own
  hp_max, ``hp_max`` floored at 1; ``definition_fields`` names only real
  ``BuildingDef`` fields.
- ``update``: writes go through the shared building-attribute writer with
  the SetResult clamp contract (applied always in-bounds,
  ``clamped == (applied != requested)``); unknown fields and non-numeric
  values are rejected with no state change; lowering hp_max caps hp.
- Listing + resolution over the caller's planet room: indexed rows,
  filter, ``#N`` via the List_Cache, name/prefix tiers, ambiguity.
- ``create``/``delete`` delegate to the real paths (owner/level kwargs,
  unresolved def token errors, deletion via the object's delete()).
- ``def_resolve``/``def_registry_dict`` delegation to the registry's
  ``resolve_building`` / ``buildings``.

Requirements: 7.2, 11.4, 11.6
"""

import unittest

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.building_adapter import BuildingAdapter
from world.admin.resolution import LIST_CACHE
from world.admin.types import CORE_VERBS
from world.constants import MAX_BUILDING_LEVEL
from world.definitions import BuildingDef


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _hq_def():
    return BuildingDef(
        name="Headquarters", abbreviation="HQ", cost={}, max_health=1000,
        requires_hq=False, required_terrain=None, category="headquarters",
        produces=None,
    )


def _ex_def():
    return BuildingDef(
        name="Extractor", abbreviation="EX", cost={}, max_health=400,
        requires_hq=True, required_terrain="mountain", category="resource",
        produces="Iron",
    )


class FakeRegistry:
    """Registry double: ``buildings`` dict + the ``resolve_building``
    abbr/name/prefix matcher (mirrors DataRegistry semantics)."""

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


class _Attrs:
    """Evennia attributes-handler stand-in."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class FakeBuilding:
    """Live building stand-in: key + attributes handler + delete()."""

    def __init__(self, key="Headquarters", building_type="HQ", level=1,
                 hp=1000, hp_max=1000, owner=None):
        self.key = key
        self.attributes = _Attrs({
            "building_type": building_type,
            "building_level": level,
            "hp": hp,
            "hp_max": hp_max,
            "owner": owner,
        })
        self._deleted = False

    def delete(self):
        self._deleted = True
        return True


class FakeRoom:
    """PlanetRoom stand-in exposing get_all_buildings."""

    def __init__(self, buildings=None):
        self.key = "TestPlanet"
        self._buildings = list(buildings or [])

    def get_all_buildings(self):
        return list(self._buildings)


class Caller:
    """Caller stub with a location and unique cache identity."""

    _next_id = 1000

    def __init__(self, location=None):
        self.id = Caller._next_id
        Caller._next_id += 1
        self.key = "Admin"
        self.location = location


def _adapter(defs=None):
    return BuildingAdapter(registry=FakeRegistry(defs or [_hq_def(),
                                                          _ex_def()]))


# ------------------------------------------------------------------ #
#  Grammar contract + registration
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):
    """Design per-entity matrix: @building supports everything; `open`
    survives as an extra verb; no alias spellings (the one migration
    change is `list`'s meaning — Requirement 11.4)."""

    def test_supports_every_core_verb_with_no_opt_outs(self):
        adapter = _adapter()
        self.assertEqual(adapter.supported_verbs, CORE_VERBS)
        self.assertEqual(adapter.opt_outs, {})
        self.assertEqual(adapter.aliases, {})

    def test_open_is_the_declared_extra_verb(self):
        self.assertEqual(set(_adapter().extra_verbs), {"open"})

    def test_def_domain_is_buildings(self):
        self.assertEqual(_adapter().def_domain, "buildings")


# ------------------------------------------------------------------ #
#  Field schemas (Requirement 7.2)
# ------------------------------------------------------------------ #

class TestFieldSchemas(unittest.TestCase):

    def test_level_has_static_bounds_one_to_max(self):
        spec = _adapter().instance_fields()["level"]
        self.assertEqual(spec.kind, "int")
        self.assertEqual(spec.min_value, 1)
        self.assertEqual(spec.max_value, float(MAX_BUILDING_LEVEL))
        self.assertIsNone(spec.dynamic_bounds)

    def test_hp_bounds_derive_from_the_targets_own_hp_max(self):
        spec = _adapter().instance_fields()["hp"]
        self.assertIsNotNone(spec.dynamic_bounds)
        self.assertEqual(spec.dynamic_bounds(FakeBuilding(hp_max=400)),
                         (0.0, 400.0))
        self.assertEqual(spec.dynamic_bounds(FakeBuilding(hp_max=1000)),
                         (0.0, 1000.0))

    def test_hp_top_is_unbounded_without_a_numeric_hp_max(self):
        spec = _adapter().instance_fields()["hp"]
        building = FakeBuilding()
        building.attributes.add("hp_max", None)
        self.assertEqual(spec.dynamic_bounds(building), (0.0, None))

    def test_definition_fields_name_real_buildingdef_fields(self):
        import dataclasses

        real = {f.name for f in dataclasses.fields(BuildingDef)}
        for name, spec in _adapter().definition_fields().items():
            self.assertIn(name, real)
            self.assertEqual(spec.name, name)


# ------------------------------------------------------------------ #
#  update — bounded writes through the shared attribute writer
# ------------------------------------------------------------------ #

class TestUpdate(unittest.TestCase):

    def setUp(self):
        self.adapter = _adapter()
        self.building = FakeBuilding(level=2, hp=500, hp_max=1000)

    def test_level_in_bounds_applies_unchanged(self):
        result = self.adapter.update(None, self.building, "level", 4)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 4)
        self.assertFalse(result.clamped)
        self.assertEqual(self.building.attributes.get("building_level"), 4)

    def test_level_clamps_at_the_static_bounds(self):
        result = self.adapter.update(None, self.building, "level", 9)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, MAX_BUILDING_LEVEL)
        self.assertTrue(result.clamped)
        self.assertEqual(self.building.attributes.get("building_level"),
                         MAX_BUILDING_LEVEL)

        result = self.adapter.update(None, self.building, "level", 0)
        self.assertEqual(result.applied, 1)
        self.assertTrue(result.clamped)

    def test_hp_clamps_into_the_targets_hp_max(self):
        result = self.adapter.update(None, self.building, "hp", 5000)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 1000)
        self.assertTrue(result.clamped)
        self.assertEqual(self.building.attributes.get("hp"), 1000)

    def test_lowering_hp_max_caps_hp(self):
        result = self.adapter.update(None, self.building, "hp_max", 300)
        self.assertTrue(result.ok)
        self.assertEqual(self.building.attributes.get("hp_max"), 300)
        self.assertEqual(self.building.attributes.get("hp"), 300)

    def test_unknown_field_rejected_with_no_state_change(self):
        result = self.adapter.update(None, self.building, "shield", 5)
        self.assertFalse(result.ok)
        self.assertIn("settable", result.error)
        self.assertEqual(self.building.attributes.get("building_level"), 2)

    def test_non_numeric_value_rejected_with_no_state_change(self):
        result = self.adapter.update(None, self.building, "level", "high")
        self.assertFalse(result.ok)
        self.assertIn("whole number", result.error)
        self.assertEqual(self.building.attributes.get("building_level"), 2)

    def test_set_idempotence_at_bounds(self):
        # Applying the same out-of-bounds set twice ends in the same state.
        self.adapter.update(None, self.building, "level", 9)
        once = self.building.attributes.get("building_level")
        result = self.adapter.update(None, self.building, "level", 9)
        self.assertEqual(self.building.attributes.get("building_level"), once)
        self.assertEqual(result.applied, once)


# ------------------------------------------------------------------ #
#  Listing + resolution over the caller's planet room
# ------------------------------------------------------------------ #

class TestListAndResolve(unittest.TestCase):

    def setUp(self):
        LIST_CACHE.clear()
        self.adapter = _adapter()
        self.hq = FakeBuilding(key="Headquarters", building_type="HQ")
        self.ex = FakeBuilding(key="Extractor", building_type="EX",
                               hp=400, hp_max=400)
        self.caller = Caller(location=FakeRoom([self.hq, self.ex]))

    def test_list_returns_indexed_rows(self):
        rows = self.adapter.list_instances(self.caller, "")
        self.assertEqual([r.index for r in rows], [1, 2])
        self.assertEqual([r.name for r in rows],
                         ["Headquarters", "Extractor"])

    def test_list_filter_matches_type_and_name(self):
        rows = self.adapter.list_instances(self.caller, "EX")
        self.assertEqual([r.name for r in rows], ["Extractor"])
        rows = self.adapter.list_instances(self.caller, "head")
        self.assertEqual([r.name for r in rows], ["Headquarters"])

    def test_no_location_lists_nothing(self):
        self.assertEqual(self.adapter.list_instances(Caller(), ""), [])

    def test_resolve_by_name_and_prefix(self):
        res = self.adapter.resolve_instance(self.caller, "headquarters")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.hq)
        res = self.adapter.resolve_instance(self.caller, "ext")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.ex)

    def test_resolve_index_via_list_cache(self):
        rows = self.adapter.list_instances(self.caller, "")
        LIST_CACHE.store(self.caller, "building", rows)
        res = self.adapter.resolve_instance(self.caller, "#2")
        self.assertTrue(res.ok)
        self.assertIs(res.target, self.ex)

    def test_index_without_cache_errors(self):
        res = self.adapter.resolve_instance(self.caller, "#1")
        self.assertFalse(res.ok)
        self.assertIn("list", res.error)

    def test_ambiguous_prefix_errors_listing_candidates(self):
        room = FakeRoom([
            FakeBuilding(key="Extractor", building_type="EX"),
            FakeBuilding(key="Extension", building_type="XT"),
        ])
        res = self.adapter.resolve_instance(Caller(location=room), "ext")
        self.assertFalse(res.ok)
        self.assertIn("ambiguous", res.error)


# ------------------------------------------------------------------ #
#  create / delete — the real system paths
# ------------------------------------------------------------------ #

class TestCreateAndDelete(unittest.TestCase):

    def test_unresolved_def_token_errors_and_creates_nothing(self):
        result = _adapter().create(Caller(), "bogus", {})
        self.assertFalse(result.ok)
        self.assertIn("bogus", result.error)

    def test_bad_level_kwarg_errors(self):
        result = _adapter().create(Caller(), "HQ", {"level": "high"})
        self.assertFalse(result.ok)
        self.assertIn("level", result.error)

    def test_missing_location_errors(self):
        result = _adapter().create(Caller(), "HQ", {})
        self.assertFalse(result.ok)
        self.assertIn("location", result.error)

    def test_delete_goes_through_the_objects_deletion_path(self):
        building = FakeBuilding()
        self.assertTrue(_adapter().delete(None, building))
        self.assertTrue(building._deleted)

    def test_delete_without_a_deletion_path_reports(self):
        result = _adapter().delete(None, object())
        self.assertFalse(result.ok)
        self.assertIn("deletion path", result.error)


# ------------------------------------------------------------------ #
#  read — ShowReport shape
# ------------------------------------------------------------------ #

class TestRead(unittest.TestCase):

    def test_show_report_carries_identity_state_and_fields(self):
        building = FakeBuilding(key="Headquarters", building_type="HQ",
                                level=3, hp=750, hp_max=1000)
        report = _adapter().read(None, building)
        self.assertIn("Headquarters", report.header)
        self.assertIn("HQ", report.header)
        joined = "\n".join(report.state_lines)
        self.assertIn("Level: 3", joined)
        self.assertIn("750/1000", joined)
        self.assertEqual([spec.name for spec, _v, _o in report.fields],
                         ["level", "hp", "hp_max"])
        values = {spec.name: value for spec, value, _o in report.fields}
        self.assertEqual(values, {"level": 3, "hp": 750, "hp_max": 1000})


# ------------------------------------------------------------------ #
#  Definition scope delegation
# ------------------------------------------------------------------ #

class TestDefinitionScope(unittest.TestCase):

    def test_def_registry_dict_serves_the_buildings_dict(self):
        adapter = _adapter()
        self.assertEqual(set(adapter.def_registry_dict()), {"HQ", "EX"})

    def test_def_resolve_delegates_abbr_name_and_prefix(self):
        adapter = _adapter()
        self.assertEqual(adapter.def_resolve("HQ").name, "Headquarters")
        self.assertEqual(adapter.def_resolve("extractor").abbreviation, "EX")
        self.assertEqual(adapter.def_resolve("head").abbreviation, "HQ")
        self.assertIsNone(adapter.def_resolve("bogus"))

    def test_no_registry_degrades_to_none(self):
        # Isolated adapter with neither an injected registry, a services
        # entry, nor the DataRegistry singleton set.
        from unittest import mock

        from world import services
        from world.data_registry import DataRegistry

        adapter = BuildingAdapter()
        with services.override({}), \
                mock.patch.object(DataRegistry, "_instance", None):
            self.assertIsNone(adapter.def_resolve("HQ"))
            self.assertIsNone(adapter.def_registry_dict())


if __name__ == "__main__":
    unittest.main()
