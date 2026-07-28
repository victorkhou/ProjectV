"""
Unit tests for the AllianceAdapter (unified-admin-crud task 7.2).

Coverage:
- Grammar contract: list/show/set/destroy + the def READ verbs
  supported; ``spawn`` opted out with the founded-by-players reason
  (plus the pointer to the player-facing path); ``def set``/``def
  reset`` opted out (perks catalog loads outside the overlay merge);
  the ``inspect``→``show`` and ``disband``→``destroy`` migration
  aliases; kick/transfer/rename extra verbs; registration in the
  AdapterRegistry (including ``register_all``) succeeds.
- Field schema: name/tag strings, open_join on|off enum; empty
  definition schema.
- Resolution: tag exact-key tier, name and prefix tiers, ambiguity,
  ``#N`` via the List_Cache, stale rows for deleted alliances.
- CRUD hooks all write via the AllianceSystem single-writer path
  (Requirement 3.5): update → ``admin_set_alliance_field``, delete →
  ``admin_disband_alliance``; create refuses (spawn opted out).
- ``read``: ShowReport shape (staff-scope state incl. treasury/perks).
- Definition plane: ``def_registry_dict`` serves the perks catalog;
  ``def_resolve`` exact/prefix matching carries the perk key.

Requirements: 1.5, 3.5, 11.5, 11.6
"""

import unittest

# NOTE: plain (non-``mygame.``-prefixed) imports, matching the import
# spelling the adapter itself uses — so module-level singletons
# (LIST_CACHE) and class identities are shared with the code under test.
from world.admin.adapter_registry import AdapterRegistry, register_all
from world.admin.adapters.alliance_adapter import (
    AllianceAdapter,
    AllianceRef,
    _DEF_WRITE_OPT_OUT,
    _SPAWN_OPT_OUT,
)
from world.admin.resolution import LIST_CACHE
from world.admin.types import CORE_VERBS


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _record(aid, name, tag, **extra):
    rec = {
        "id": aid, "name": name, "tag": tag, "leader_id": None,
        "officer_ids": [], "member_ids": [], "treasury": {},
        "active_perks": {}, "pending_invites": [], "pending_requests": [],
        "open_join": False,
    }
    rec.update(extra)
    return rec


class FakeAllianceRegistry:
    """In-memory AllianceRegistry double (get/all/put/delete)."""

    def __init__(self, records=()):
        self._alliances = {rec["id"]: rec for rec in records}

    def get(self, aid):
        return self._alliances.get(aid)

    def all_alliances(self):
        return list(self._alliances.values())

    def put(self, record):
        self._alliances[record["id"]] = record

    def delete(self, aid):
        self._alliances.pop(aid, None)


class FakeAllianceSystem:
    """AllianceSystem double exposing the admin single-writer paths."""

    def __init__(self, records=(), fail_write=False, fail_disband=False):
        self._alliances = FakeAllianceRegistry(records)
        self.fail_write = fail_write
        self.fail_disband = fail_disband
        self.calls = []

    def alliance_exists(self, aid):
        return self._alliances.get(aid) is not None

    def _live_members(self, aid):
        rec = self._alliances.get(aid) or {}
        return list(rec.get("member_ids", [])) + \
            ([rec["leader_id"]] if rec.get("leader_id") else [])

    def compute_alliance_level(self, aid):
        return 2

    def _resolve_member(self, cid):
        return None

    def admin_set_alliance_field(self, aid, field, value):
        self.calls.append(("set", aid, field, value))
        if self.fail_write:
            return False, "An alliance with that name already exists."
        rec = self._alliances.get(aid)
        if rec is None:
            return False, "That alliance no longer exists."
        rec[field] = value
        return True, ""

    def admin_disband_alliance(self, aid):
        self.calls.append(("disband", aid))
        if self.fail_disband:
            return False, "disband failed"
        if self._alliances.get(aid) is None:
            return False, "That alliance no longer exists."
        self._alliances.delete(aid)
        return True, ""


class Caller:
    _next_id = 1

    def __init__(self):
        self.id = Caller._next_id
        Caller._next_id += 1
        self.key = "Admin"


_PERKS = {
    "shared_vision": {"category": "vision", "effect_type": "vision",
                      "levels": {1: {"tier": 1, "cost": {"Iron": 10}}}},
    "shared_bank": {"category": "economy", "effect_type": "treasury",
                    "levels": {1: {"tier": 2, "cost": {"Iron": 50}}}},
}


class FakeDataRegistry:
    def __init__(self, perks=None):
        self.alliance_perks = _PERKS if perks is None else perks


def _adapter_with(records=(), **kwargs):
    caller = Caller()
    system = FakeAllianceSystem(records, **kwargs)
    adapter = AllianceAdapter(alliance_system=system,
                              registry=FakeDataRegistry())
    return adapter, system, caller


_WOLVES = ("Iron Wolves", "IW")
_COAL = ("Coalition", "COAL")


def _two_alliances():
    return [_record(1, *_WOLVES), _record(2, *_COAL)]


# ------------------------------------------------------------------ #
#  Grammar contract (Requirements 1.5, 11.5)
# ------------------------------------------------------------------ #

class TestGrammarContract(unittest.TestCase):

    def test_supported_verbs_and_opt_outs_cover_core(self):
        adapter = AllianceAdapter()
        self.assertEqual(
            adapter.supported_verbs,
            frozenset({"list", "show", "set", "destroy", "def list",
                       "def show", "def diff"}),
        )
        self.assertEqual(set(adapter.opt_outs),
                         {"spawn", "def set", "def reset"})
        covered = adapter.supported_verbs | set(adapter.opt_outs)
        self.assertEqual(covered, CORE_VERBS)

    def test_spawn_opt_out_reason_points_at_the_player_facing_path(self):
        reason = AllianceAdapter().opt_outs["spawn"]
        self.assertTrue(reason.strip())
        self.assertIn("founded by players", reason)
        self.assertIn("alliance found", reason)

    def test_def_write_opt_out_reasons_name_the_read_only_catalog(self):
        adapter = AllianceAdapter()
        for verb in ("def set", "def reset"):
            reason = adapter.opt_outs[verb]
            self.assertTrue(reason.strip(), f"empty reason for {verb}")
            self.assertIn("alliance_perks.yaml", reason)

    def test_migration_aliases_installed(self):
        self.assertEqual(
            AllianceAdapter().aliases,
            {"inspect": "show", "disband": "destroy"},
        )

    def test_extra_verbs_kept_from_the_legacy_router(self):
        self.assertEqual(set(AllianceAdapter().extra_verbs),
                         {"kick", "transfer", "rename"})


# ------------------------------------------------------------------ #
#  Field schema
# ------------------------------------------------------------------ #

class TestFieldSchema(unittest.TestCase):

    def test_instance_fields_names_and_kinds(self):
        fields = AllianceAdapter().instance_fields()
        self.assertEqual(set(fields), {"name", "tag", "open_join"})
        self.assertEqual(fields["name"].kind, "str")
        self.assertEqual(fields["tag"].kind, "str")
        self.assertEqual(fields["open_join"].kind, "enum")
        self.assertEqual(fields["open_join"].enum_values, ("on", "off"))

    def test_definition_schema_is_empty(self):
        self.assertEqual(AllianceAdapter().definition_fields(), {})


# ------------------------------------------------------------------ #
#  Listing + resolution
# ------------------------------------------------------------------ #

class TestListingAndResolution(unittest.TestCase):

    def test_list_rows_indexed_and_keyed_by_tag(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        rows = adapter.list_instances(caller, "")
        self.assertEqual([r.index for r in rows], [1, 2])
        self.assertEqual([r.key for r in rows], ["IW", "COAL"])
        self.assertEqual(rows[0].name, "Iron Wolves")
        self.assertIn("level 2", rows[0].summary)

    def test_list_filter_matches_tag_or_name(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        self.assertEqual(
            [r.key for r in adapter.list_instances(caller, "coal")],
            ["COAL"],
        )
        self.assertEqual(
            [r.key for r in adapter.list_instances(caller, "wolves")],
            ["IW"],
        )

    def test_resolve_by_exact_tag(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        result = adapter.resolve_instance(caller, "IW")
        self.assertTrue(result.ok)
        self.assertEqual(result.target.alliance_id, 1)

    def test_resolve_by_name_case_insensitive(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        result = adapter.resolve_instance(caller, "iron wolves")
        self.assertTrue(result.ok)
        self.assertEqual(result.target.alliance_id, 1)

    def test_resolve_by_unambiguous_prefix(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        result = adapter.resolve_instance(caller, "Coa")
        self.assertTrue(result.ok)
        self.assertEqual(result.target.alliance_id, 2)

    def test_resolve_ambiguous_prefix_errors_listing_candidates(self):
        records = [_record(1, "Iron Wolves", "IW"),
                   _record(2, "Iron Fist", "IF")]
        adapter, _system, caller = _adapter_with(records)
        result = adapter.resolve_instance(caller, "Iron")
        self.assertFalse(result.ok)
        self.assertIn("ambiguous", result.error)

    def test_resolve_index_token_via_list_cache(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        LIST_CACHE.store(caller, "alliance",
                         adapter.list_instances(caller, ""))
        try:
            result = adapter.resolve_instance(caller, "#2")
            self.assertTrue(result.ok)
            self.assertEqual(result.target.alliance_id, 2)
        finally:
            LIST_CACHE.clear(caller)

    def test_cached_row_stale_after_the_alliance_is_deleted(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        LIST_CACHE.store(caller, "alliance",
                         adapter.list_instances(caller, ""))
        try:
            system._alliances.delete(2)
            result = adapter.resolve_instance(caller, "#2")
            self.assertFalse(result.ok)
            self.assertIn("stale", result.error)
        finally:
            LIST_CACHE.clear(caller)

    def test_resolve_no_match_errors(self):
        adapter, _system, caller = _adapter_with(_two_alliances())
        result = adapter.resolve_instance(caller, "NOPE")
        self.assertFalse(result.ok)
        self.assertIn("NOPE", result.error)


# ------------------------------------------------------------------ #
#  CRUD hooks — all writes via AllianceSystem (Requirement 3.5)
# ------------------------------------------------------------------ #

class TestCreate(unittest.TestCase):

    def test_create_refuses_with_the_spawn_opt_out_reason(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        result = adapter.create(caller, "anything", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, _SPAWN_OPT_OUT)
        self.assertEqual(system.calls, [])


class TestUpdate(unittest.TestCase):

    def _ref(self, adapter, caller, token="IW"):
        return adapter.resolve_instance(caller, token).target

    def test_update_name_writes_via_single_writer_path(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        ref = self._ref(adapter, caller)
        result = adapter.update(caller, ref, "name", "Steel Wolves")
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, "Steel Wolves")
        self.assertIn(("set", 1, "name", "Steel Wolves"), system.calls)

    def test_update_open_join_coerces_enum_to_bool_for_the_system(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        ref = self._ref(adapter, caller)
        result = adapter.update(caller, ref, "open_join", "on")
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, "on")
        self.assertIn(("set", 1, "open_join", True), system.calls)
        result = adapter.update(caller, ref, "open_join", "off")
        self.assertIn(("set", 1, "open_join", False), system.calls)
        self.assertEqual(result.applied, "off")

    def test_update_invalid_enum_value_rejected_listing_valid(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        ref = self._ref(adapter, caller)
        result = adapter.update(caller, ref, "open_join", "maybe")
        self.assertFalse(result.ok)
        self.assertIn("on", result.error)
        self.assertIn("off", result.error)
        self.assertEqual(system.calls, [])

    def test_update_unknown_field_rejected_naming_settable(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        ref = self._ref(adapter, caller)
        result = adapter.update(caller, ref, "treasury", "lots")
        self.assertFalse(result.ok)
        self.assertIn("name", result.error)
        self.assertIn("open_join", result.error)
        self.assertEqual(system.calls, [])

    def test_update_system_rejection_relayed_verbatim(self):
        adapter, _system, caller = _adapter_with(_two_alliances(),
                                                 fail_write=True)
        ref = self._ref(adapter, caller)
        result = adapter.update(caller, ref, "name", "Coalition")
        self.assertFalse(result.ok)
        self.assertIn("already exists", result.error)

    def test_update_is_idempotent_for_the_same_value(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        ref = self._ref(adapter, caller)
        first = adapter.update(caller, ref, "name", "Steel Wolves")
        second = adapter.update(caller, ref, "name", "Steel Wolves")
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(first.applied, second.applied)
        self.assertEqual(
            system._alliances.get(1)["name"], "Steel Wolves"
        )


class TestDelete(unittest.TestCase):

    def test_delete_goes_through_admin_disband_alliance(self):
        adapter, system, caller = _adapter_with(_two_alliances())
        ref = adapter.resolve_instance(caller, "IW").target
        result = adapter.delete(caller, ref)
        self.assertTrue(result.ok)
        self.assertIn(("disband", 1), system.calls)
        self.assertIsNone(system._alliances.get(1))

    def test_delete_failure_reported(self):
        adapter, _system, caller = _adapter_with(_two_alliances(),
                                                 fail_disband=True)
        ref = adapter.resolve_instance(caller, "IW").target
        result = adapter.delete(caller, ref)
        self.assertFalse(result.ok)
        self.assertIn("disband failed", result.error)


# ------------------------------------------------------------------ #
#  show + definition plane
# ------------------------------------------------------------------ #

class TestReadAndDefPlane(unittest.TestCase):

    def test_read_report_shape(self):
        rec = _record(1, "Iron Wolves", "IW", treasury={"Iron": 30},
                      active_perks={"shared_vision": 1}, open_join=True)
        adapter, _system, caller = _adapter_with([rec])
        ref = adapter.resolve_instance(caller, "IW").target
        self.assertIsInstance(ref, AllianceRef)
        report = adapter.read(caller, ref)
        self.assertIn("#1", report.header)
        self.assertIn("Iron Wolves", report.header)
        self.assertIn("[IW]", report.header)
        joined = "\n".join(report.state_lines)
        self.assertIn("Open-join: ON", joined)
        self.assertIn("Iron: 30", joined)
        self.assertIn("shared_vision L1", joined)
        self.assertIn("Pending invites", joined)
        self.assertEqual(
            {spec.name for spec, _value, _ovr in report.fields},
            {"name", "tag", "open_join"},
        )
        self.assertIsNone(report.staleness_note)

    def test_def_registry_dict_serves_the_perks_catalog(self):
        adapter, _system, _caller = _adapter_with()
        self.assertEqual(adapter.def_registry_dict(), _PERKS)

    def test_def_registry_dict_none_without_a_registry(self):
        adapter = AllianceAdapter(
            alliance_system=FakeAllianceSystem(),
            registry=object(),  # no alliance_perks attribute
        )
        self.assertIsNone(adapter.def_registry_dict())

    def test_def_resolve_exact_key_carries_the_perk_key(self):
        adapter, _system, _caller = _adapter_with()
        perk = adapter.def_resolve("shared_vision")
        self.assertEqual(perk["key"], "shared_vision")
        self.assertEqual(perk["category"], "vision")

    def test_def_resolve_unambiguous_prefix(self):
        adapter, _system, _caller = _adapter_with()
        self.assertEqual(adapter.def_resolve("shared_b")["key"],
                         "shared_bank")

    def test_def_resolve_ambiguous_or_unknown_returns_none(self):
        adapter, _system, _caller = _adapter_with()
        self.assertIsNone(adapter.def_resolve("shared"))   # ambiguous
        self.assertIsNone(adapter.def_resolve("bogus"))    # unknown


if __name__ == "__main__":
    unittest.main()
