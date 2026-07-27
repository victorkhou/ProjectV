"""
Unit tests for OverlayStore (unified-admin-crud task 1.7).

Covers set/reset/diff round-trips, replace-not-duplicate, reset-with-no-
override errors, absent-file-empty behavior, unparseable-file write
rejection, atomic writes, snapshot rollback, and the merge_into hook.

Requirements: 5.1, 5.2, 5.3, 5.9, 5.10, 5.11
"""

import os
import tempfile
import unittest

import yaml

from mygame.world.admin.overlay_store import (
    OVERLAY_FILENAME,
    OverlayStore,
    OverlayStoreError,
)


class OverlayStoreTestBase(unittest.TestCase):
    """Shared temp-dir fixture: each test gets a fresh base_path."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_path = self._tmp.name
        self.store = OverlayStore(base_path=self.base_path)
        self.path = os.path.join(self.base_path, OVERLAY_FILENAME)

    def read_raw(self):
        """Parse the overlay file straight off disk."""
        with open(self.path, "r") as f:
            return yaml.safe_load(f)


class TestSetResetDiffRoundTrips(OverlayStoreTestBase):
    """set/reset/diff round-trips and replace-not-duplicate (R5.1, 5.2, 5.9)."""

    def test_set_then_get_round_trip(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.assertEqual(self.store.get("items", "rifle"), {"damage_max": 42})

    def test_all_domains_share_the_single_overlay_file(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.set("buildings", "HQ", "hp_max", 500)
        raw = self.read_raw()
        self.assertEqual(raw["items"]["rifle"]["damage_max"], 42)
        self.assertEqual(raw["buildings"]["HQ"]["hp_max"], 500)

    def test_set_replaces_existing_override_never_duplicates(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.set("items", "rifle", "damage_max", 55)
        self.assertEqual(self.store.get("items", "rifle"), {"damage_max": 55})
        # The field appears exactly once in the file.
        with open(self.path, "r") as f:
            content = f.read()
        self.assertEqual(content.count("damage_max"), 1)

    def test_set_persists_across_store_instances(self):
        self.store.set("items", "rifle", "damage_max", 42)
        fresh = OverlayStore(base_path=self.base_path)
        self.assertEqual(fresh.get("items", "rifle"), {"damage_max": 42})

    def test_reset_field_removes_only_that_field(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.set("items", "rifle", "range", 7)
        self.store.reset("items", "rifle", "damage_max")
        self.assertEqual(self.store.get("items", "rifle"), {"range": 7})

    def test_reset_whole_key_removes_all_its_fields(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.set("items", "rifle", "range", 7)
        self.store.set("items", "pistol", "damage_max", 9)
        self.store.reset("items", "rifle", None)
        self.assertEqual(self.store.get("items", "rifle"), {})
        self.assertEqual(self.store.get("items", "pistol"), {"damage_max": 9})

    def test_reset_prunes_empty_parents(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.reset("items", "rifle", "damage_max")
        self.assertEqual(self.store.diff(), {})

    def test_reset_field_with_no_override_errors_and_leaves_file_untouched(self):
        self.store.set("items", "rifle", "damage_max", 42)
        before = self.read_raw()
        with self.assertRaises(OverlayStoreError):
            self.store.reset("items", "rifle", "range")
        self.assertEqual(self.read_raw(), before)

    def test_reset_key_with_no_override_errors(self):
        with self.assertRaises(OverlayStoreError):
            self.store.reset("items", "rifle", None)

    def test_diff_shape_is_domain_key_fields(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.set("buildings", "HQ", "hp_max", 500)
        self.assertEqual(
            self.store.diff(),
            {
                "items": {"rifle": {"damage_max": 42}},
                "buildings": {"HQ": {"hp_max": 500}},
            },
        )

    def test_diff_returns_a_copy(self):
        self.store.set("items", "rifle", "damage_max", 42)
        d = self.store.diff()
        d["items"]["rifle"]["damage_max"] = 999
        self.assertEqual(self.store.get("items", "rifle"), {"damage_max": 42})

    def test_written_file_carries_the_managed_header(self):
        self.store.set("items", "rifle", "damage_max", 42)
        with open(self.path, "r") as f:
            first_line = f.readline()
        self.assertTrue(first_line.startswith("#"))
        self.assertIn("Do not hand-edit", open(self.path).read())


class TestAbsentAndUnparseableFile(OverlayStoreTestBase):
    """Absent file -> empty overlay; unparseable -> writes rejected (R5.10, 5.11)."""

    def test_absent_file_reads_as_empty_overlay(self):
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.store.get("items", "rifle"), {})
        self.assertEqual(self.store.diff(), {})

    def test_absent_file_merge_into_returns_raw_unchanged(self):
        raw = {"items": [{"key": "rifle", "damage_max": 30}]}
        self.assertEqual(self.store.merge_into(raw, "items"), raw)

    def _corrupt_file(self, content="{ not: valid: yaml: ["):
        with open(self.path, "w") as f:
            f.write(content)

    def test_unparseable_file_rejects_set(self):
        self._corrupt_file()
        with self.assertRaises(OverlayStoreError):
            self.store.set("items", "rifle", "damage_max", 42)
        # File untouched — still the corrupt content, no partial write.
        self.assertEqual(open(self.path).read(), "{ not: valid: yaml: [")

    def test_unparseable_file_rejects_reset(self):
        self._corrupt_file()
        with self.assertRaises(OverlayStoreError):
            self.store.reset("items", "rifle", "damage_max")

    def test_unparseable_file_reports_error_on_read(self):
        self._corrupt_file()
        with self.assertRaises(OverlayStoreError):
            self.store.get("items", "rifle")

    def test_non_mapping_document_rejects_writes(self):
        self._corrupt_file("- just\n- a\n- list\n")
        with self.assertRaises(OverlayStoreError):
            self.store.set("items", "rifle", "damage_max", 42)

    def test_writes_accepted_again_once_repaired(self):
        self._corrupt_file()
        with self.assertRaises(OverlayStoreError):
            self.store.set("items", "rifle", "damage_max", 42)
        os.remove(self.path)  # "repair" by removing the corrupt file
        self.store.set("items", "rifle", "damage_max", 42)
        self.assertEqual(self.store.get("items", "rifle"), {"damage_max": 42})


class TestAtomicWrite(OverlayStoreTestBase):
    """Atomic temp-file + rename writes (R5.3)."""

    def test_no_stray_temp_files_after_writes(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.reset("items", "rifle", "damage_max")
        leftovers = [n for n in os.listdir(self.base_path) if n != OVERLAY_FILENAME]
        self.assertEqual(leftovers, [])

    def test_failed_write_leaves_existing_file_intact(self):
        self.store.set("items", "rifle", "damage_max", 42)
        before = self.read_raw()

        class NotYamlSerializable:
            pass

        # safe_dump cannot represent arbitrary objects -> the write fails
        # before the rename, so the on-disk overlay must be unchanged.
        with self.assertRaises(Exception):
            self.store.set("items", "rifle", "damage_max", NotYamlSerializable())
        self.assertEqual(self.read_raw(), before)
        leftovers = [n for n in os.listdir(self.base_path) if n != OVERLAY_FILENAME]
        self.assertEqual(leftovers, [])

    def test_file_is_always_parseable_after_write(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.assertIsInstance(self.read_raw(), dict)


class TestSnapshotRollback(OverlayStoreTestBase):
    """Pre-write snapshot + restore_snapshot rollback (supports R6.5)."""

    def test_restore_rolls_back_to_pre_write_state(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.set("items", "rifle", "damage_max", 99)  # snapshot taken here
        self.store.restore_snapshot()
        self.assertEqual(self.store.get("items", "rifle"), {"damage_max": 42})

    def test_restore_after_first_write_removes_the_file(self):
        self.store.set("items", "rifle", "damage_max", 42)  # file was absent
        self.store.restore_snapshot()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.store.diff(), {})

    def test_restore_rolls_back_a_reset(self):
        self.store.set("items", "rifle", "damage_max", 42)
        self.store.reset("items", "rifle", "damage_max")
        self.store.restore_snapshot()
        self.assertEqual(self.store.get("items", "rifle"), {"damage_max": 42})

    def test_restore_without_snapshot_errors(self):
        with self.assertRaises(OverlayStoreError):
            self.store.restore_snapshot()


class TestMergeInto(OverlayStoreTestBase):
    """merge_into applies the domain's overrides over raw YAML documents."""

    def test_merges_into_mapping_with_entity_list(self):
        # items.yaml shape: {"items": [ ... ], "production_map": {...}}
        self.store.set("items", "rifle", "damage_max", 42)
        raw = {
            "items": [
                {"key": "rifle", "damage_max": 30, "range": 5},
                {"key": "pistol", "damage_max": 10},
            ],
            "production_map": {"Armory": ["rifle"]},
        }
        merged = self.store.merge_into(raw, "items")
        self.assertEqual(merged["items"][0]["damage_max"], 42)
        self.assertEqual(merged["items"][0]["range"], 5)  # untouched field
        self.assertEqual(merged["items"][1]["damage_max"], 10)  # other entity
        self.assertEqual(merged["production_map"], {"Armory": ["rifle"]})

    def test_merges_into_top_level_list(self):
        # buildings.yaml shape: top-level list, identified by "abbreviation".
        self.store.set("buildings", "HQ", "hp_max", 500)
        raw = [
            {"name": "Headquarters", "abbreviation": "HQ", "hp_max": 300},
            {"name": "Armory", "abbreviation": "AR", "hp_max": 200},
        ]
        merged = self.store.merge_into(raw, "buildings")
        self.assertEqual(merged[0]["hp_max"], 500)
        self.assertEqual(merged[1]["hp_max"], 200)

    def test_merges_into_canonical_mapping_shape(self):
        self.store.set("powerups", "shield", "duration", 30)
        raw = {"shield": {"duration": 10, "cooldown_ticks": 5}}
        merged = self.store.merge_into(raw, "powerups")
        self.assertEqual(merged["shield"], {"duration": 30, "cooldown_ticks": 5})

    def test_does_not_mutate_raw(self):
        self.store.set("items", "rifle", "damage_max", 42)
        raw = {"items": [{"key": "rifle", "damage_max": 30}]}
        self.store.merge_into(raw, "items")
        self.assertEqual(raw["items"][0]["damage_max"], 30)

    def test_only_applies_the_requested_domain(self):
        self.store.set("buildings", "HQ", "hp_max", 500)
        raw = {"items": [{"key": "HQ", "damage_max": 30}]}
        merged = self.store.merge_into(raw, "items")
        self.assertEqual(merged, raw)  # buildings overrides don't leak

    def test_unmatched_override_key_is_left_unapplied(self):
        self.store.set("items", "ghost_item", "damage_max", 42)
        raw = {"items": [{"key": "rifle", "damage_max": 30}]}
        merged = self.store.merge_into(raw, "items")
        self.assertEqual(merged, raw)


if __name__ == "__main__":
    unittest.main()
