"""
Unit tests for the OutpostAdapter (unified-admin-crud task 7.1).

Coverage:
- Grammar contract: ``list`` keeps its instance meaning alongside NEW
  ``show``/``set``/``destroy``; ``def set``/``def reset`` are opted out
  with a reason (base templates load outside the overlay merge);
  the ``tiers``→``def list`` Migration_Alias; registration in the
  AdapterRegistry (including ``register_all``) succeeds.
- Tier resolution (definition plane): exact name, unambiguous prefix,
  ``#N``/``N`` index into the sorted tiers, ambiguous → None, and the
  legacy no-templates fallback (raw lowercased token) for ``resolve_tier``
  only.
- Listing + resolution over the spawner's ``_active_bases``: indexed
  rows, filter, ``#N`` via the List_Cache, name/prefix tiers, ambiguity,
  staleness when a base is no longer tracked.
- ``update``: ``disturbed_at`` writes through the spawner's own state
  paths (tracking record + sentinel stamp) with the SetResult clamp
  contract; unknown fields and non-numeric values rejected with no state
  change.
- ``create``/``delete`` delegate to the REAL spawner paths
  (``spawn_base`` with planet/coords defaults; ``wipe_bases_in_area``
  over the base's exact HQ tile).

Requirements: 11.5, 11.6
"""

import itertools
import unittest

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.outpost_adapter import OutpostAdapter, OutpostBase
from world.admin.resolution import LIST_CACHE
from world.admin.types import CORE_VERBS
from world.definitions import BaseTemplateDef

_CALLER_IDS = itertools.count(50_000)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

class _Attrs:
    """Evennia attributes-handler stand-in."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, **kw):
        return self._data.get(key, default)

    def add(self, key, value, **kw):
        self._data[key] = value


class FakeSentinel:
    """Sentinel owner stand-in: key + attributes handler."""

    def __init__(self, key="Outpost #1"):
        self.key = key
        self.attributes = _Attrs()


class FakeSpawner:
    """OutpostSpawnerSystem double: tracking records + spawner paths."""

    def __init__(self, bases=None, spawn_result="ok"):
        #: base_key -> record, exactly like the real _active_bases.
        self._active_bases = dict(bases or {})
        self._spawn_result = spawn_result
        self.spawn_calls = []
        self.wipe_calls = []

    def spawn_base(self, planet, tier, coords=None):
        self.spawn_calls.append((planet, tier, coords))
        if self._spawn_result is None:
            return None
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
            self.wipe_calls.append(key)
        return len(victims)


class FakeTemplateRegistry:
    """DataRegistry double carrying ``base_templates``."""

    def __init__(self, tiers=("fortress", "outpost")):
        self.base_templates = {
            t: BaseTemplateDef(tier=t, display_name=t.title())
            for t in tiers
        }


class Caller:
    """Caller stub with coords and a unique cache identity."""

    def __init__(self, x=3, y=4, planet="earth"):
        self.id = next(_CALLER_IDS)
        self.key = "Admin"

        class _Db:
            coord_x, coord_y, coord_planet = x, y, planet

            def __getattr__(self, key):
                return None

        self.db = _Db()


def _record(tier="outpost", planet="earth", x=5, y=6, sentinel=None,
            disturbed_at=0):
    return {"sentinel": sentinel, "tier": tier, "planet": planet,
            "x": x, "y": y, "disturbed_at": disturbed_at}


def _adapter(bases=None, tiers=("fortress", "outpost"), spawner=None):
    return OutpostAdapter(
        registry=FakeTemplateRegistry(tiers),
        spawner=spawner if spawner is not None else FakeSpawner(bases),
    )


# ------------------------------------------------------------------ #
#  Grammar contract + registration (Requirements 11.5, 11.6)
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):
    """Design per-entity matrix row for @outpost: list keeps its instance
    meaning, NEW show/set/destroy, tiers→def list alias, def writes opted
    out because outposts.yaml loads outside the overlay merge."""

    def test_supports_all_core_verbs_except_the_def_writes(self):
        adapter = _adapter()
        self.assertEqual(adapter.supported_verbs,
                         CORE_VERBS - {"def set", "def reset"})

    def test_def_writes_opted_out_with_a_reason(self):
        adapter = _adapter()
        self.assertEqual(set(adapter.opt_outs), {"def set", "def reset"})
        for reason in adapter.opt_outs.values():
            self.assertTrue(reason.strip())
            self.assertIn("outposts.yaml", reason)

    def test_tiers_is_the_declared_alias_of_def_list(self):
        self.assertEqual(_adapter().aliases, {"tiers": "def list"})
        self.assertEqual(_adapter().extra_verbs, {})

    def test_registers_cleanly_in_adapter_registry(self):
        registry = AdapterRegistry()
        registry.register(_adapter())
        self.assertIsNotNone(registry.get("outpost"))

    def test_register_all_includes_the_outpost_adapter(self):
        registry = register_all(AdapterRegistry())
        self.assertIsInstance(registry.get("outpost"), OutpostAdapter)

    def test_def_domain_is_outposts(self):
        self.assertEqual(_adapter().def_domain, "outposts")


# ------------------------------------------------------------------ #
#  Tier resolution (definition plane)
# ------------------------------------------------------------------ #

class TestTierResolution(unittest.TestCase):

    def test_exact_prefix_and_index(self):
        adapter = _adapter()  # sorted: fortress [1], outpost [2]
        self.assertEqual(adapter.resolve_tier("outpost"), "outpost")
        self.assertEqual(adapter.resolve_tier("FORT"), "fortress")
        self.assertEqual(adapter.resolve_tier("2"), "outpost")
        self.assertEqual(adapter.resolve_tier("#1"), "fortress")

    def test_ambiguous_or_unknown_is_none(self):
        adapter = _adapter(tiers=("fortress", "fortalice"))
        self.assertIsNone(adapter.resolve_tier("fort"))
        self.assertIsNone(adapter.resolve_tier("bogus"))
        self.assertIsNone(adapter.resolve_tier("#9"))

    def test_no_templates_falls_back_to_the_raw_token(self):
        # Legacy behavior: without template metadata the spawner validates.
        adapter = OutpostAdapter(registry=object(), spawner=FakeSpawner())
        self.assertEqual(adapter.resolve_tier("Outpost"), "outpost")

    def test_def_resolve_returns_the_template_without_fallback(self):
        adapter = _adapter()
        self.assertEqual(adapter.def_resolve("outpost").tier, "outpost")
        self.assertEqual(adapter.def_resolve("#2").tier, "outpost")
        self.assertIsNone(adapter.def_resolve("bogus"))
        bare = OutpostAdapter(registry=object(), spawner=FakeSpawner())
        self.assertIsNone(bare.def_resolve("outpost"))

    def test_def_registry_dict_serves_base_templates(self):
        self.assertEqual(set(_adapter().def_registry_dict()),
                         {"fortress", "outpost"})


# ------------------------------------------------------------------ #
#  Listing + resolution over the spawner's tracking records
# ------------------------------------------------------------------ #

class TestListAndResolve(unittest.TestCase):

    def setUp(self):
        LIST_CACHE.clear()
        self.s1 = FakeSentinel("Outpost #1")
        self.s2 = FakeSentinel("Fortress #1")
        self.spawner = FakeSpawner(bases={
            101: _record(tier="outpost", x=5, y=6, sentinel=self.s1),
            102: _record(tier="fortress", x=20, y=30, sentinel=self.s2,
                         disturbed_at=44),
        })
        self.adapter = _adapter(spawner=self.spawner)
        self.caller = Caller()

    def test_list_returns_indexed_rows_from_active_bases(self):
        rows = self.adapter.list_instances(self.caller, "")
        self.assertEqual([r.index for r in rows], [1, 2])
        self.assertEqual([r.name for r in rows],
                         ["Outpost #1", "Fortress #1"])
        self.assertIn("outpost at (5, 6) on earth", rows[0].summary)
        self.assertIn("[disturbed]", rows[1].summary)

    def test_list_filter_matches_tier_planet_and_name(self):
        rows = self.adapter.list_instances(self.caller, "fort")
        self.assertEqual([r.name for r in rows], ["Fortress #1"])
        self.assertEqual(
            self.adapter.list_instances(self.caller, "mars"), [])

    def test_record_without_sentinel_names_the_tier(self):
        # The spawner rebuilds records lazily; a sentinel-less record
        # (e.g. a minimal double) still renders and resolves by tier.
        spawner = FakeSpawner(bases={0: {"tier": "outpost",
                                         "planet": "earth", "x": 5, "y": 6}})
        rows = _adapter(spawner=spawner).list_instances(self.caller, "")
        self.assertEqual(rows[0].name, "outpost")
        self.assertIn("(5, 6)", rows[0].summary)

    def test_resolve_by_name_and_prefix(self):
        res = self.adapter.resolve_instance(self.caller, "fortress #1")
        self.assertTrue(res.ok)
        self.assertIs(res.target.record,
                      self.spawner._active_bases[102])
        res = self.adapter.resolve_instance(self.caller, "Out")
        self.assertTrue(res.ok)
        self.assertIs(res.target.record,
                      self.spawner._active_bases[101])

    def test_resolve_index_via_list_cache(self):
        rows = self.adapter.list_instances(self.caller, "")
        LIST_CACHE.store(self.caller, "outpost", rows)
        res = self.adapter.resolve_instance(self.caller, "#2")
        self.assertTrue(res.ok)
        self.assertEqual(res.target.base_key, 102)

    def test_index_without_cache_errors(self):
        res = self.adapter.resolve_instance(self.caller, "#1")
        self.assertFalse(res.ok)
        self.assertIn("list", res.error)

    def test_untracked_base_makes_the_cached_index_stale(self):
        rows = self.adapter.list_instances(self.caller, "")
        LIST_CACHE.store(self.caller, "outpost", rows)
        self.spawner._active_bases.pop(101)  # wiped behind our back
        res = self.adapter.resolve_instance(self.caller, "#1")
        self.assertFalse(res.ok)
        self.assertIn("stale", res.error)

    def test_ambiguous_prefix_errors_listing_candidates(self):
        spawner = FakeSpawner(bases={
            1: _record(sentinel=FakeSentinel("Outpost #1")),
            2: _record(sentinel=FakeSentinel("Outpost #2"), x=9, y=9),
        })
        res = _adapter(spawner=spawner).resolve_instance(
            self.caller, "outpost")
        self.assertFalse(res.ok)
        self.assertIn("ambiguous", res.error)


# ------------------------------------------------------------------ #
#  update — the spawner's own staleness-stamp write path
# ------------------------------------------------------------------ #

class TestUpdate(unittest.TestCase):

    def setUp(self):
        self.sentinel = FakeSentinel()
        self.record = _record(sentinel=self.sentinel)
        self.instance = OutpostBase(key="Outpost #1", name="Outpost #1",
                                    base_key=101, record=self.record)
        self.adapter = _adapter()

    def test_disturbed_at_writes_record_and_sentinel_stamp(self):
        result = self.adapter.update(None, self.instance,
                                     "disturbed_at", 77)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 77)
        self.assertFalse(result.clamped)
        self.assertEqual(self.record["disturbed_at"], 77)
        self.assertEqual(
            self.sentinel.attributes.get("base_disturbed_at"), 77)

    def test_negative_value_clamps_to_zero(self):
        result = self.adapter.update(None, self.instance,
                                     "disturbed_at", -5)
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, 0)
        self.assertTrue(result.clamped)
        self.assertEqual(self.record["disturbed_at"], 0)

    def test_unknown_field_rejected_with_no_state_change(self):
        result = self.adapter.update(None, self.instance, "tier", "citadel")
        self.assertFalse(result.ok)
        self.assertIn("settable", result.error)
        self.assertEqual(self.record["tier"], "outpost")

    def test_non_numeric_value_rejected_with_no_state_change(self):
        result = self.adapter.update(None, self.instance,
                                     "disturbed_at", "soon")
        self.assertFalse(result.ok)
        self.assertIn("whole number", result.error)
        self.assertEqual(self.record["disturbed_at"], 0)


# ------------------------------------------------------------------ #
#  create / delete — the real spawner paths
# ------------------------------------------------------------------ #

class TestCreateAndDelete(unittest.TestCase):

    def test_create_spawns_at_the_callers_tile_by_default(self):
        spawner = FakeSpawner()
        adapter = _adapter(spawner=spawner)
        result = adapter.create(Caller(x=3, y=4), "outpost", {})
        self.assertTrue(result.ok)
        self.assertEqual(spawner.spawn_calls,
                         [("earth", "outpost", (3, 4))])

    def test_create_honors_explicit_planet_and_coords(self):
        spawner = FakeSpawner()
        adapter = _adapter(spawner=spawner)
        result = adapter.create(Caller(), "fort",
                                {"planet": "mars", "coords": (20, 30)})
        self.assertTrue(result.ok)
        self.assertEqual(spawner.spawn_calls,
                         [("mars", "fortress", (20, 30))])

    def test_create_placement_failure_reports(self):
        adapter = _adapter(spawner=FakeSpawner(spawn_result=None))
        result = adapter.create(Caller(), "outpost", {})
        self.assertFalse(result.ok)
        self.assertIn("could not spawn", result.error)

    def test_create_unknown_tier_reports(self):
        result = _adapter().create(Caller(), "bogus", {})
        self.assertFalse(result.ok)
        self.assertIn("bogus", result.error)

    def test_create_without_a_planet_position_reports(self):
        result = _adapter().create(Caller(planet=None), "outpost", {})
        self.assertFalse(result.ok)
        self.assertIn("planet", result.error)

    def test_delete_wipes_the_base_via_the_spawner_area_clear(self):
        spawner = FakeSpawner(bases={
            101: _record(x=5, y=6),
            102: _record(tier="fortress", x=200, y=200),
        })
        adapter = _adapter(spawner=spawner)
        instance = OutpostBase(key="outpost", name="outpost",
                               base_key=101,
                               record=spawner._active_bases[101])
        self.assertTrue(adapter.delete(None, instance))
        # The one-tile box wipes exactly this base; the distant one stays.
        self.assertEqual(list(spawner._active_bases), [102])

    def test_delete_of_an_untracked_base_reports_stale(self):
        spawner = FakeSpawner()
        adapter = _adapter(spawner=spawner)
        instance = OutpostBase(key="outpost", name="outpost",
                               base_key=101, record=_record())
        result = adapter.delete(None, instance)
        self.assertFalse(result.ok)
        self.assertIn("no longer tracked", result.error)


# ------------------------------------------------------------------ #
#  read — ShowReport shape
# ------------------------------------------------------------------ #

class TestRead(unittest.TestCase):

    def test_show_report_carries_identity_state_and_fields(self):
        record = _record(tier="fortress", x=20, y=30, disturbed_at=44,
                         sentinel=FakeSentinel("Fortress #1"))
        instance = OutpostBase(key="Fortress #1", name="Fortress #1",
                               base_key=102, record=record)
        report = _adapter().read(None, instance)
        self.assertIn("Fortress #1", report.header)
        self.assertIn("fortress", report.header)
        joined = "\n".join(report.state_lines)
        self.assertIn("earth", joined)
        self.assertIn("(20, 30)", joined)
        self.assertIn("since tick 44", joined)
        self.assertEqual([spec.name for spec, _v, _o in report.fields],
                         ["disturbed_at"])
        self.assertEqual(report.fields[0][1], 44)

    def test_pristine_base_reads_as_undisturbed(self):
        instance = OutpostBase(key="outpost", name="outpost",
                               base_key=1, record=_record())
        report = _adapter().read(None, instance)
        self.assertIn("pristine",
                      "\n".join(report.state_lines))


if __name__ == "__main__":
    unittest.main()
