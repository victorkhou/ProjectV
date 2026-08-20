"""
Unit tests for the loot roller (item-loot-economy task 1.2).

Example-based coverage complementing test_prop_loot_roller.py: the no-op
path for unrolled items (R1.3), fixed-seed determinism (R1.5), the crafted
band fallback (R6.1), skew resolution, the never-raise contract on
malformed data, and the neutral Phase-2 result fields.
"""

import os
import random
import unittest
from collections.abc import MutableMapping, MutableSequence

import yaml

from mygame.world.definitions import ItemDef
from mygame.world.systems.loot_roller import (
    AFFIX_VALUE_SCALE,
    CRAFT_RARITY_TIERS,
    DEFAULT_CRAFT_RARITY_TABLE,
    DEFAULT_LOOT_ROLL_SKEW,
    DEFAULT_RARITY_TABLE,
    RARITY_AFFIX_BUDGETS,
    RARITY_ORDER,
    RARITY_ROLL_FLOORS,
    RollResult,
    affix_value_total,
    assign_craft_rarity,
    compute_iqs,
    displayed_iqs,
    draw_affixes,
    rarity_affix_budget,
    rarity_roll_floor,
    recompute_iqs,
    reroll_base_stats,
    resolve_rarity_bucket,
    roll_and_stamp,
    roll_item,
    stats_at_quality,
)


class FixedRNG:
    """An rng whose ``random()`` returns scripted values (then repeats last)."""

    def __init__(self, *values):
        self._values = list(values)

    def random(self):
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


def _item(roll_spec=None, **kwargs):
    defaults = dict(
        key="rifle", name="Rifle", slot="weapon_ranged",
        category="weapon", weapon_type="ranged",
    )
    defaults.update(kwargs)
    return ItemDef(roll_spec=roll_spec, **defaults)


SPEC = {
    "stats": {
        "damage": {"min": 18, "max": 30, "weight": 3},
        "range": {"min": 4, "max": 7, "weight": 1},
    },
    "craft": {
        "damage": {"min": 20, "max": 25},
    },
}


class TestUnrolledItems(unittest.TestCase):
    """R1.3: no roll_spec -> no-op; the item stays fixed exactly as today."""

    def test_no_roll_spec_returns_none(self):
        self.assertIsNone(roll_item(_item(None), rng=random.Random(1)))

    def test_empty_stats_returns_none(self):
        self.assertIsNone(roll_item(_item({"stats": {}}), rng=random.Random(1)))

    def test_object_without_roll_spec_attribute_returns_none(self):
        self.assertIsNone(roll_item(object(), rng=random.Random(1)))


class TestDeterminismAndClamping(unittest.TestCase):
    def test_same_seed_same_rolls(self):
        """R1.5: identical injected seeds produce identical results."""
        a = roll_item(_item(SPEC), rng=random.Random(42))
        b = roll_item(_item(SPEC), rng=random.Random(42))
        self.assertEqual(a.stat_modifiers, b.stat_modifiers)

    def test_rolls_land_inside_bands(self):
        result = roll_item(_item(SPEC), rng=random.Random(7))
        self.assertTrue(18 <= result.stat_modifiers["damage"] <= 30)
        self.assertTrue(4 <= result.stat_modifiers["range"] <= 7)

    def test_u_zero_and_one_hit_band_edges(self):
        """U=0 -> min; U=1 -> max (the roll spans the full band)."""
        lo = roll_item(_item(SPEC), rng=FixedRNG(0.0)).stat_modifiers
        hi = roll_item(_item(SPEC), rng=FixedRNG(1.0)).stat_modifiers
        self.assertEqual(lo["damage"], 18)
        self.assertEqual(hi["damage"], 30)


class TestSkewResolution(unittest.TestCase):
    """rolled = min + (max - min) * U**skew (design §1.3)."""

    BAND = {"stats": {"damage": {"min": 0, "max": 16, "weight": 1}}}

    def test_default_skew_is_two(self):
        # U=0.5, skew=2 -> 0 + 16 * 0.25 = 4
        result = roll_item(_item(self.BAND), rng=FixedRNG(0.5))
        self.assertAlmostEqual(result.stat_modifiers["damage"], 4.0)
        self.assertEqual(DEFAULT_LOOT_ROLL_SKEW, 2.0)

    def test_per_item_skew_overrides_default(self):
        spec = dict(self.BAND, skew=1)  # U=0.5, skew=1 -> midpoint 8
        result = roll_item(_item(spec), rng=FixedRNG(0.5))
        self.assertAlmostEqual(result.stat_modifiers["damage"], 8.0)

    def test_default_skew_parameter_honored(self):
        # Wiring passes balance.loot_roll_skew through default_skew.
        result = roll_item(_item(self.BAND), rng=FixedRNG(0.5),
                           default_skew=4)
        self.assertAlmostEqual(result.stat_modifiers["damage"], 1.0)

    def test_invalid_skew_falls_back_to_default(self):
        spec = dict(self.BAND, skew="steep")
        result = roll_item(_item(spec), rng=FixedRNG(0.5))
        self.assertAlmostEqual(result.stat_modifiers["damage"], 4.0)


class TestCraftedRolls(unittest.TestCase):
    """R1.4 / R6.1: crafted=True rolls in the tighter craft band."""

    def test_crafted_uses_craft_band(self):
        # U=1 -> craft max (25), not loot max (30).
        result = roll_item(_item(SPEC), crafted=True, rng=FixedRNG(1.0))
        self.assertEqual(result.stat_modifiers["damage"], 25)

    def test_crafted_stat_without_craft_band_falls_back_to_loot_band(self):
        # `range` has no craft entry: U=1 -> loot max (7).
        result = roll_item(_item(SPEC), crafted=True, rng=FixedRNG(1.0))
        self.assertEqual(result.stat_modifiers["range"], 7)

    def test_crafted_without_any_craft_section_uses_loot_bands(self):
        spec = {"stats": {"damage": {"min": 18, "max": 30, "weight": 3}}}
        result = roll_item(_item(spec), crafted=True, rng=FixedRNG(1.0))
        self.assertEqual(result.stat_modifiers["damage"], 30)

    def test_craft_band_outside_loot_band_is_intersected(self):
        # Defensive (validated at load, but never trusted): a craft band
        # escaping the loot band is clamped into it (design Property 4).
        spec = {
            "stats": {"damage": {"min": 18, "max": 30, "weight": 3}},
            "craft": {"damage": {"min": 10, "max": 40}},
        }
        lo = roll_item(_item(spec), crafted=True, rng=FixedRNG(0.0))
        hi = roll_item(_item(spec), crafted=True, rng=FixedRNG(1.0))
        self.assertEqual(lo.stat_modifiers["damage"], 18)
        self.assertEqual(hi.stat_modifiers["damage"], 30)

    def test_loot_roll_ignores_craft_band(self):
        # crafted=False: U=1 -> loot max (30) despite the craft band.
        result = roll_item(_item(SPEC), crafted=False, rng=FixedRNG(1.0))
        self.assertEqual(result.stat_modifiers["damage"], 30)


class TestNeverRaise(unittest.TestCase):
    """R1.5: roll_item never raises — malformed data degrades safely."""

    def test_non_dict_roll_spec_returns_none(self):
        self.assertIsNone(roll_item(_item("weapon"), rng=random.Random(1)))

    def test_malformed_stat_band_is_skipped(self):
        spec = {"stats": {
            "damage": {"min": 18, "max": 30, "weight": 3},
            "range": "not_a_band",
        }}
        result = roll_item(_item(spec), rng=random.Random(1))
        self.assertIn("damage", result.stat_modifiers)
        self.assertNotIn("range", result.stat_modifiers)

    def test_band_min_above_max_is_skipped(self):
        spec = {"stats": {"damage": {"min": 30, "max": 18, "weight": 3}}}
        self.assertIsNone(roll_item(_item(spec), rng=random.Random(1)))

    def test_non_numeric_band_is_skipped(self):
        spec = {"stats": {"damage": {"min": "a", "max": "b", "weight": 1}}}
        self.assertIsNone(roll_item(_item(spec), rng=random.Random(1)))

    def test_all_bands_malformed_returns_none(self):
        spec = {"stats": {"damage": None, "range": []}}
        self.assertIsNone(roll_item(_item(spec), rng=random.Random(1)))


class TestResultShape(unittest.TestCase):
    """iqs is stamped (task 1.4); rarity is assigned (task 2.2); the affix
    draw (task 2.3) stays neutral when no pools are passed."""

    def test_affixes_stay_neutral_without_pools(self):
        # No affix_pools passed (and SPEC names no affix_pool) → the
        # production-drop / pure-caller treatment: no affixes (design §3.2).
        result = roll_item(_item(SPEC), rng=random.Random(1))
        self.assertIsInstance(result, RollResult)
        self.assertEqual(result.affixes, [])
        self.assertIn(result.rarity, RARITY_ORDER)

    def test_roll_item_stamps_iqs(self):
        # Task 1.4: roll_item computes the base IQS for its own rolls.
        result = roll_item(_item(SPEC), rng=random.Random(1))
        self.assertEqual(result.iqs,
                         compute_iqs(result.stat_modifiers, SPEC))
        self.assertTrue(0 <= result.iqs <= 100)


class TestComputeIQS(unittest.TestCase):
    """Task 1.4 (design §2.1): weighted-mean base IQS, 0–100."""

    def test_all_min_rolls_score_zero(self):
        rolled = {"damage": 18, "range": 4}
        self.assertEqual(compute_iqs(rolled, SPEC), 0)

    def test_all_max_rolls_score_hundred(self):
        rolled = {"damage": 30, "range": 7}
        self.assertEqual(compute_iqs(rolled, SPEC), 100)

    def test_weighted_mean(self):
        # damage q=1 (w=3), range q=0 (w=1) -> 100 * 3/4 = 75.
        rolled = {"damage": 30, "range": 4}
        self.assertEqual(compute_iqs(rolled, SPEC), 75)
        # Flip it: damage q=0, range q=1 -> 100 * 1/4 = 25.
        rolled = {"damage": 18, "range": 7}
        self.assertEqual(compute_iqs(rolled, SPEC), 25)

    def test_missing_weight_defaults_to_one(self):
        spec = {"stats": {
            "damage": {"min": 0, "max": 10},
            "range": {"min": 0, "max": 10},
        }}
        # Equal (default) weights: (1 + 0) / 2 -> 50.
        self.assertEqual(compute_iqs({"damage": 10, "range": 0}, spec), 50)

    def test_degenerate_band_excluded(self):
        spec = {"stats": {
            "damage": {"min": 18, "max": 30, "weight": 3},
            "range": {"min": 5, "max": 5, "weight": 100},
        }}
        # range carries no roll-quality signal; damage q=1 alone -> 100.
        self.assertEqual(compute_iqs({"damage": 30, "range": 5}, spec), 100)

    def test_all_degenerate_returns_none(self):
        spec = {"stats": {"damage": {"min": 5, "max": 5, "weight": 1}}}
        self.assertIsNone(compute_iqs({"damage": 5}, spec))

    def test_out_of_band_value_clamped_into_zero_hundred(self):
        rolled = {"damage": 999, "range": -999}
        score = compute_iqs(rolled, SPEC)
        self.assertTrue(0 <= score <= 100)

    def test_never_raises_on_malformed_inputs(self):
        self.assertIsNone(compute_iqs(None, SPEC))
        self.assertIsNone(compute_iqs({"damage": 20}, None))
        self.assertIsNone(compute_iqs({"damage": 20}, {"stats": "nope"}))
        self.assertIsNone(compute_iqs({"damage": "high"}, SPEC))
        self.assertIsNone(compute_iqs({}, SPEC))


class _SaverDict(MutableMapping):
    """Faithful stand-in for evennia.utils.dbserialize._SaverDict.

    The real class is a MutableMapping wrapping a plain dict — it is NOT a
    dict subclass, so ``isinstance(x, dict)`` is False. Reading any stored
    dict back through ``db``/``attributes`` on real Evennia yields one of
    these; the stubbed conftest hands back plain dicts, which is exactly
    how the strict-isinstance IQS bug stayed green.
    """

    def __init__(self, data=None):
        self._data = dict(data or {})

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __delitem__(self, key):
        del self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


class _SaverList(MutableSequence):
    """Faithful stand-in for evennia's _SaverList (MutableSequence, not list)."""

    def __init__(self, data=None):
        self._data = list(data or [])

    def __getitem__(self, idx):
        return self._data[idx]

    def __setitem__(self, idx, value):
        self._data[idx] = value

    def __delitem__(self, idx):
        del self._data[idx]

    def __len__(self):
        return len(self._data)

    def insert(self, idx, value):
        self._data.insert(idx, value)


class TestSaverTypesFromRealEvennia(unittest.TestCase):
    """REGRESSION (review H1): the scoring functions must accept Evennia's
    SaverDict/SaverList read-back types, not just plain dict/list.

    Without duck-typing, ``compute_iqs`` returned None for every live item
    (no IQS ever stamped in production; salvage always paid the floor) and
    ``affix_value_total`` dropped all affix score — while the stubbed test
    suite stayed green.
    """

    def test_compute_iqs_accepts_saver_mappings(self):
        rolled = _SaverDict({"damage": 30, "range": 4})
        spec = _SaverDict({"stats": _SaverDict({
            "damage": _SaverDict({"min": 18, "max": 30, "weight": 3}),
            "range": _SaverDict({"min": 4, "max": 7, "weight": 1}),
        })})
        # Same weighted mean the plain-dict test asserts: 100 * 3/4 = 75.
        self.assertEqual(compute_iqs(rolled, spec), 75)

    def test_affix_value_total_accepts_saver_list(self):
        affixes = _SaverList([
            _SaverDict({"key": "keen", "value": 4.0}),
            _SaverDict({"key": "sturdy", "value": 2.5}),
        ])
        self.assertEqual(affix_value_total(affixes), 6.5)

    def test_affix_value_total_rejects_strings(self):
        # str is a Sequence; the duck-typed guard must still exclude it.
        self.assertEqual(affix_value_total("keen"), 0.0)

    def test_recompute_iqs_on_item_with_saver_state(self):
        # End-to-end: an item whose stored state reads back as Saver types
        # (the live-Evennia shape) still gets a re-stamped score.
        item = {
            "rolled_stats": _SaverDict({"damage": 30, "range": 4}),
            "affixes": _SaverList([_SaverDict({"key": "keen", "value": 5})]),
        }
        self.assertEqual(recompute_iqs(item, SPEC), 80)
        self.assertEqual(item["iqs"], 80)


class TestRarityBucketResolution(unittest.TestCase):
    """Task 2.2 (design §3.2): the numeric source weight selects the
    highest-threshold bucket it reaches in the rarity table."""

    def test_default_table_bucket_thresholds(self):
        for weight, bucket in (
            (0.0, "guard_kill"), (0.5, "guard_kill"),
            (1.0, "outpost"), (1.9, "outpost"),
            (2.0, "stronghold"), (3.0, "fortress"), (3.5, "fortress"),
            (4.0, "citadel"), (99.0, "citadel"),
        ):
            self.assertEqual(
                resolve_rarity_bucket(weight, DEFAULT_RARITY_TABLE), bucket,
                f"weight {weight} should land in {bucket}",
            )

    def test_negative_weight_reaches_no_bucket(self):
        self.assertIsNone(resolve_rarity_bucket(-1.0, DEFAULT_RARITY_TABLE))

    def test_nan_weight_degrades_to_lowest_bucket(self):
        # Review M1: every `threshold > nan` comparison is False, so a NaN
        # weight used to skip NO rows and resolve to the HIGHEST bucket
        # ("citadel") — free apex loot on corrupt data. The documented safe
        # degradation is the LOWEST bucket (0.0 — the same safe-floor
        # treatment production drops get).
        self.assertEqual(
            resolve_rarity_bucket(float("nan"), DEFAULT_RARITY_TABLE),
            "guard_kill")

    def test_inf_weight_degrades_to_lowest_bucket(self):
        # inf is never a legitimate source weight — same safe degradation.
        self.assertEqual(
            resolve_rarity_bucket(float("inf"), DEFAULT_RARITY_TABLE),
            "guard_kill")
        self.assertEqual(
            resolve_rarity_bucket(float("-inf"), DEFAULT_RARITY_TABLE),
            "guard_kill")

    def test_nan_row_threshold_is_skipped(self):
        # A NaN min_weight row can never be selected (it fails _num).
        table = {
            "broken": {"min_weight": float("nan"), "weights": {"epic": 1}},
            "ok": {"min_weight": 0.0, "weights": {"common": 1}},
        }
        self.assertEqual(resolve_rarity_bucket(2.0, table), "ok")

    def test_unusable_table_resolves_none(self):
        self.assertIsNone(resolve_rarity_bucket(2.0, {}))
        self.assertIsNone(resolve_rarity_bucket(2.0, "nope"))
        self.assertIsNone(
            resolve_rarity_bucket(2.0, {"bucket": "not_a_row"}))
        self.assertIsNone(
            resolve_rarity_bucket(2.0, {"bucket": {"min_weight": "high"}}))


class TestRarityAssignment(unittest.TestCase):
    """Task 2.2 (R3.2): weighted-choice rarity per source bucket, drawn
    under the injected rng — deterministic, data-tunable, never raises."""

    # A table where each bucket forces ONE rarity — assignment is then
    # fully predictable regardless of the rng draw.
    FORCED = {
        "low": {"min_weight": 0.0, "weights": {"common": 1}},
        "high": {"min_weight": 5.0, "weights": {"legendary": 1}},
    }

    def test_source_weight_selects_the_bucket_distribution(self):
        a = roll_item(_item(SPEC), source_rarity_weight=0.0,
                      rng=random.Random(9), rarity_table=self.FORCED)
        b = roll_item(_item(SPEC), source_rarity_weight=5.0,
                      rng=random.Random(9), rarity_table=self.FORCED)
        self.assertEqual(a.rarity, "common")
        self.assertEqual(b.rarity, "legendary")

    def test_rarity_deterministic_under_seed(self):
        a = roll_item(_item(SPEC), source_rarity_weight=4.0,
                      rng=random.Random(123))
        b = roll_item(_item(SPEC), source_rarity_weight=4.0,
                      rng=random.Random(123))
        self.assertEqual(a.rarity, b.rarity)
        self.assertEqual(a.stat_modifiers, b.stat_modifiers)

    def test_weighted_choice_walks_cumulative_order(self):
        # guard_kill {common 70, uncommon 25, rare 5}: the draw walks the
        # RARITY_ORDER cumulative — 0.0 → common, 0.71 → uncommon,
        # 0.96 → rare (scripted first draw; stat draws repeat the last).
        for first_draw, expected in ((0.0, "common"), (0.71, "uncommon"),
                                     (0.96, "rare")):
            result = roll_item(_item(SPEC), source_rarity_weight=0.0,
                               rng=FixedRNG(first_draw, 0.5))
            self.assertEqual(result.rarity, expected,
                             f"draw {first_draw} should pick {expected}")

    def test_crafted_skips_rarity_assignment_without_craft_level(self):
        # Crafted-rarity change (deviation from R6.1): rarity on the craft
        # path now comes ONLY from the building-level craft table — the
        # loot-source weight never applies, and without a usable
        # craft_level (< 1) the original no-rarity behavior holds (reads
        # neutral/modest), even with a high source weight.
        result = roll_item(_item(SPEC), source_rarity_weight=99.0,
                           crafted=True, rng=random.Random(3))
        self.assertIsNone(result.rarity)

    def test_empty_or_malformed_table_disables_rarity(self):
        for table in ({}, {"b": {"min_weight": 0.0, "weights": {}}},
                      {"b": {"min_weight": 0.0, "weights": {"common": 0}}},
                      {"b": {"min_weight": 0.0, "weights": "junk"}}):
            result = roll_item(_item(SPEC), rng=random.Random(5),
                               rarity_table=table)
            self.assertIsNone(result.rarity)
            self.assertEqual(set(result.stat_modifiers), {"damage", "range"})

    def test_nan_source_weight_rolls_in_the_lowest_bucket(self):
        # Review M1 end-to-end: a NaN source weight degrades to the lowest
        # bucket (guard_kill: no epic/legendary at all), never the highest.
        rng = random.Random(7)
        for _ in range(300):
            result = roll_item(_item(SPEC),
                               source_rarity_weight=float("nan"), rng=rng)
            self.assertIn(result.rarity, ("common", "uncommon", "rare"))

    def test_nan_band_bounds_never_produce_nan_rolls(self):
        # A NaN bound fails _num, so the stat is skipped — no NaN can ever
        # reach rolled_stats (load-time validation also rejects it; the
        # roller must degrade anyway, R1.5).
        spec = {"stats": {
            "damage": {"min": float("nan"), "max": 30, "weight": 3},
            "range": {"min": 4, "max": 7, "weight": 1},
        }}
        result = roll_item(_item(spec), rng=random.Random(1))
        self.assertNotIn("damage", result.stat_modifiers)
        self.assertIn("range", result.stat_modifiers)

    def test_default_table_matches_balance_config(self):
        # The pure-module fallback and the balance default must never drift.
        from mygame.world.definitions import BalanceConfig
        self.assertEqual(BalanceConfig().rarity_table, DEFAULT_RARITY_TABLE)


class TestRarityFloorClamp(unittest.TestCase):
    """Task 2.2 (R3.3, design §1.3/§3.1): rarity raises the roll floor by
    clamping U into [floor, 1] BEFORE the skew."""

    BAND = {"stats": {"damage": {"min": 0, "max": 16, "weight": 1}}}
    LEGENDARY_ONLY = {"o": {"min_weight": 0.0, "weights": {"legendary": 1}}}

    def test_floors_match_design_table(self):
        self.assertEqual(RARITY_ROLL_FLOORS, {
            "common": 0.0, "uncommon": 0.0, "rare": 0.25,
            "epic": 0.50, "legendary": 0.75,
        })
        self.assertEqual(rarity_roll_floor("legendary"), 0.75)
        self.assertEqual(rarity_roll_floor("Epic"), 0.50)  # case-blind
        self.assertEqual(rarity_roll_floor("common"), 0.0)
        self.assertEqual(rarity_roll_floor(None), 0.0)

    def test_legendary_floor_raises_min_roll(self):
        # First draw picks the (forced) legendary rarity; the second (stat)
        # draw is the WORST possible U=0 → clamped to the 0.75 floor, then
        # skewed: rolled = 0 + 16 * 0.75^2 = 9.0. Without the floor the
        # same U=0 rolls the band minimum (0).
        result = roll_item(_item(self.BAND), rng=FixedRNG(0.0),
                           rarity_table=self.LEGENDARY_ONLY)
        self.assertEqual(result.rarity, "legendary")
        self.assertAlmostEqual(result.stat_modifiers["damage"], 9.0)

        no_floor = roll_item(_item(self.BAND), rng=FixedRNG(0.0),
                             rarity_table={})
        self.assertEqual(no_floor.stat_modifiers["damage"], 0.0)

    def test_floor_preserves_the_band_top(self):
        # U=1 still reaches the band max — the floor removes the bottom of
        # the band, not the top (variance preserved, design §1.3).
        result = roll_item(_item(self.BAND), rng=FixedRNG(1.0),
                           rarity_table=self.LEGENDARY_ONLY)
        self.assertEqual(result.stat_modifiers["damage"], 16.0)

    def test_common_has_no_floor(self):
        forced_common = {"o": {"min_weight": 0.0, "weights": {"common": 1}}}
        result = roll_item(_item(self.BAND), rng=FixedRNG(0.0),
                           rarity_table=forced_common)
        self.assertEqual(result.rarity, "common")
        self.assertEqual(result.stat_modifiers["damage"], 0.0)

    def test_statistical_floor_guarantee(self):
        # Every legendary roll lands at or above the floor**skew fraction
        # of the band: 16 * 0.75^2 = 9.0 (R3.3 — floor raises min roll).
        rng = random.Random(42)
        for _ in range(500):
            result = roll_item(_item(self.BAND), rng=rng,
                               rarity_table=self.LEGENDARY_ONLY)
            self.assertGreaterEqual(result.stat_modifiers["damage"], 9.0 - 1e-9)


class TestSourceDistributionShift(unittest.TestCase):
    """Task 2.2 statistical test: a higher source bucket shifts the rarity
    distribution upward (design §3.2 — guard kill < ... < citadel)."""

    N = 4000

    def _rarity_counts(self, weight, seed=7):
        rng = random.Random(seed)
        counts = {name: 0 for name in RARITY_ORDER}
        for _ in range(self.N):
            result = roll_item(_item(SPEC), source_rarity_weight=weight,
                               rng=rng)
            counts[result.rarity] += 1
        return counts

    def test_higher_bucket_shifts_rarity_mass_upward(self):
        rank = {name: i for i, name in enumerate(RARITY_ORDER)}
        guard = self._rarity_counts(0.0)     # guard_kill bucket
        citadel = self._rarity_counts(4.0)   # citadel bucket

        def mean_rank(counts):
            return sum(rank[r] * n for r, n in counts.items()) / self.N

        self.assertGreater(mean_rank(citadel), mean_rank(guard))

    def test_bucket_distributions_track_design_starting_numbers(self):
        # Guard kills: no epic/legendary at all; mostly common (§9 ≈ 70%).
        guard = self._rarity_counts(0.0)
        self.assertEqual(guard["epic"], 0)
        self.assertEqual(guard["legendary"], 0)
        self.assertGreater(guard["common"] / self.N, 0.6)
        # Citadel: epic+legendary carry real mass (§9 ≈ 40% + 15%).
        citadel = self._rarity_counts(4.0)
        self.assertGreater(citadel["epic"] / self.N, 0.3)
        self.assertGreater(citadel["legendary"] / self.N, 0.08)

    def test_monotone_mean_rank_across_all_buckets(self):
        rank = {name: i for i, name in enumerate(RARITY_ORDER)}
        means = []
        for weight in (0.0, 1.0, 2.0, 3.0, 4.0):
            counts = self._rarity_counts(weight)
            means.append(
                sum(rank[r] * n for r, n in counts.items()) / self.N)
        self.assertEqual(means, sorted(means))


class TestRollAndStampRarity(unittest.TestCase):
    """Task 2.2: roll_and_stamp writes ``rarity`` onto the item when one
    was assigned — and never writes it when none was (crafted/no-table)."""

    def test_stamps_rarity_on_loot_roll(self):
        item = {}
        result = roll_and_stamp(item, _item(SPEC),
                                source_rarity_weight=4.0,
                                rng=random.Random(11))
        self.assertIn(result.rarity, RARITY_ORDER)
        self.assertEqual(item["rarity"], result.rarity)
        self.assertIn("rolled_stats", item)
        self.assertIn("iqs", item)

    def test_no_rarity_key_written_for_crafted_without_level(self):
        # Crafted-rarity change (deviation from R6.1): with no craft_level
        # supplied, the crafted no-rarity behavior stays — leveled crafts
        # are TestCraftedRarity's subject.
        item = {}
        roll_and_stamp(item, _item(SPEC), crafted=True,
                       rng=random.Random(11))
        self.assertNotIn("rarity", item)
        self.assertIn("rolled_stats", item)

    def test_no_rarity_key_written_without_usable_table(self):
        item = {}
        roll_and_stamp(item, _item(SPEC), rng=random.Random(11),
                       rarity_table={})
        self.assertNotIn("rarity", item)
        self.assertIn("rolled_stats", item)

    def test_balance_style_table_is_honored(self):
        # The wiring passes balance.rarity_table through — a custom table
        # (data-tunable, design §3.2) changes the assignment.
        table = {"only": {"min_weight": 0.0, "weights": {"epic": 1}}}
        item = {}
        roll_and_stamp(item, _item(SPEC), rng=random.Random(11),
                       rarity_table=table)
        self.assertEqual(item["rarity"], "epic")


class TestShippedBalanceTableShape(unittest.TestCase):
    """Review L3: the BalanceConfig-vs-DEFAULT sync tests only pin the
    dataclass default against the module fallback — the SHIPPED
    balance.yaml could still drift silently. balance.yaml is the tunable
    (its numbers MAY diverge from the defaults), so these tests assert
    only the SHAPE: the same source buckets / level rows as the module
    defaults, only known tiers, and per-row weights that sum to 100 (the
    read-as-percentages convention both tables document)."""

    #: mygame/world/systems/tests → up 3 to mygame → data/config.
    BALANCE_PATH = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..",
        "data", "config", "balance.yaml"))

    @classmethod
    def setUpClass(cls):
        with open(cls.BALANCE_PATH) as f:
            cls.balance = yaml.safe_load(f)

    def test_real_balance_file_exists(self):
        self.assertTrue(os.path.isfile(self.BALANCE_PATH))

    def test_shipped_rarity_table_shape(self):
        table = self.balance.get("rarity_table")
        self.assertIsInstance(table, dict)
        # Same source buckets as the module fallback (bucket names are the
        # contract the drop wiring's source weights target).
        self.assertEqual(set(table), set(DEFAULT_RARITY_TABLE))
        for bucket, row in table.items():
            self.assertIsInstance(row, dict, bucket)
            self.assertGreaterEqual(row.get("min_weight"), 0.0, bucket)
            weights = row.get("weights")
            self.assertIsInstance(weights, dict, bucket)
            self.assertTrue(set(weights) <= set(RARITY_ORDER),
                            f"{bucket}: unknown tiers {set(weights) - set(RARITY_ORDER)}")
            self.assertEqual(sum(weights.values()), 100,
                             f"{bucket} weights must sum to 100 "
                             f"(read as percentages)")

    def test_shipped_rarity_table_bucket_thresholds_match_defaults(self):
        # The threshold LADDER (0 < 1 < 2 < 3 < 4) is structural — the
        # outposts.yaml tier weights aim at it; only the rarity ODDS are
        # the tunable.
        table = self.balance["rarity_table"]
        for bucket, row in DEFAULT_RARITY_TABLE.items():
            self.assertEqual(table[bucket]["min_weight"], row["min_weight"],
                             bucket)

    def test_shipped_craft_rarity_table_shape(self):
        table = self.balance.get("craft_rarity_table")
        self.assertIsInstance(table, dict)
        # Same building-level rows as the module fallback (1–5).
        self.assertEqual({int(k) for k in table},
                         set(DEFAULT_CRAFT_RARITY_TABLE))
        for level, weights in table.items():
            self.assertIsInstance(weights, dict, level)
            self.assertTrue(set(weights) <= CRAFT_RARITY_TIERS,
                            f"L{level}: tiers above rare {set(weights) - CRAFT_RARITY_TIERS}")
            self.assertEqual(sum(weights.values()), 100,
                             f"L{level} weights must sum to 100")


# -------------------------------------------------------------- #
#  Crafted rarity (post-spec change — deliberate deviation from R6.1,
#  per user request): the crafting BUILDING's level draws a rarity from
#  the craft_rarity_table, capped at Rare (exactly 5% at L5, none at L1).
#  Affixes stay loot-only; the craft band still contains every roll.
# -------------------------------------------------------------- #

class TestCraftedRarity(unittest.TestCase):
    """Building-level crafted-rarity draw (assign_craft_rarity + the
    crafted branch of roll_item/roll_and_stamp)."""

    FORCED_RARE = {5: {"rare": 1}}

    def test_default_craft_table_matches_balance_config(self):
        # The pure-module fallback and the balance default must never drift
        # (mirrors the rarity_table sync test).
        from mygame.world.definitions import BalanceConfig
        self.assertEqual(BalanceConfig().craft_rarity_table,
                         DEFAULT_CRAFT_RARITY_TABLE)

    def test_default_table_curve(self):
        # The documented curve: rows sum to 100 (read as percentages), no
        # Rare at L1, Rare EXACTLY 5% at L5, monotone Rare share in level,
        # and no tier above Rare anywhere.
        self.assertEqual(sorted(DEFAULT_CRAFT_RARITY_TABLE), [1, 2, 3, 4, 5])
        rare_shares = []
        for level, weights in sorted(DEFAULT_CRAFT_RARITY_TABLE.items()):
            self.assertEqual(sum(weights.values()), 100)
            self.assertTrue(set(weights) <= CRAFT_RARITY_TIERS)
            rare_shares.append(weights.get("rare", 0))
        self.assertEqual(rare_shares[0], 0)     # L1: no rare
        self.assertEqual(rare_shares[-1], 5)    # L5: exactly 5%
        self.assertEqual(rare_shares, sorted(rare_shares))

    def test_level_selects_row(self):
        table = {1: {"common": 1}, 5: {"rare": 1}}
        rng = random.Random(1)
        self.assertEqual(assign_craft_rarity(1, table, rng), "common")
        self.assertEqual(assign_craft_rarity(5, table, rng), "rare")
        # Between rows: the highest key <= level wins (L4 -> the L1 row).
        self.assertEqual(assign_craft_rarity(4, table, rng), "common")
        # Above the top key: the top row (defensive on odd data).
        self.assertEqual(assign_craft_rarity(9, table, rng), "rare")

    def test_no_rarity_below_lowest_row_or_invalid_level(self):
        table = {3: {"rare": 1}}
        rng = random.Random(1)
        self.assertIsNone(assign_craft_rarity(2, table, rng))
        for bad in (0, -1, None, "five", float("nan")):
            self.assertIsNone(assign_craft_rarity(bad, table, rng))

    def test_unusable_table_yields_no_rarity(self):
        rng = random.Random(1)
        for table in ({}, None, "junk", {1: "junk"}, {1: {}},
                      {"level": {"rare": 1}}, {1: {"rare": 0}}):
            self.assertIsNone(assign_craft_rarity(5, table, rng))

    def test_string_level_keys_tolerated(self):
        # YAML can hand level keys through as strings; the lookup coerces.
        self.assertEqual(
            assign_craft_rarity(5, {"5": {"rare": 1}}, random.Random(1)),
            "rare")

    def test_tiers_above_rare_filtered_out(self):
        # The hard cap: epic/legendary weights in a craft row are ignored —
        # crafted gear can never come out above Rare, whatever the data.
        table = {5: {"legendary": 99, "epic": 99, "rare": 1}}
        rng = random.Random(1)
        for _ in range(50):
            self.assertEqual(assign_craft_rarity(5, table, rng), "rare")
        # A row with ONLY epic+ weights degrades to no rarity.
        self.assertIsNone(
            assign_craft_rarity(5, {5: {"legendary": 1}}, rng))

    def test_crafted_rarity_deterministic_under_seed(self):
        a = roll_item(_item(SPEC), crafted=True, craft_level=5,
                      rng=random.Random(77))
        b = roll_item(_item(SPEC), crafted=True, craft_level=5,
                      rng=random.Random(77))
        self.assertEqual(a.rarity, b.rarity)
        self.assertEqual(a.stat_modifiers, b.stat_modifiers)

    def test_rare_craft_floor_applies_inside_craft_band(self):
        # Forced rare: first draw picks the rarity, then the WORST stat
        # draw (U=0) is clamped to the 0.25 rare floor INSIDE the craft
        # band [20, 25]: rolled = 20 + 5 * 0.25**2 = 20.3125 — a rare
        # craft genuinely rolls better, but never escapes the band.
        result = roll_item(_item(SPEC), crafted=True, craft_level=5,
                           craft_rarity_table=self.FORCED_RARE,
                           rng=FixedRNG(0.0))
        self.assertEqual(result.rarity, "rare")
        self.assertAlmostEqual(result.stat_modifiers["damage"],
                               20 + 5 * 0.25 ** 2)

    def test_rare_craft_never_exceeds_craft_band_top(self):
        # U=1 still tops out at the CRAFT max (25), never the loot max
        # (30) — the floor lifts the bottom, not the top.
        result = roll_item(_item(SPEC), crafted=True, craft_level=5,
                           craft_rarity_table=self.FORCED_RARE,
                           rng=FixedRNG(1.0))
        self.assertEqual(result.rarity, "rare")
        self.assertEqual(result.stat_modifiers["damage"], 25)

    def test_rarity_floor_and_master_gunsmithing_floor_take_max(self):
        # Both floors can apply (a Rare craft under Master Gunsmithing's
        # craft_iqs_floor): the roller takes max(floors), mirroring the
        # reroll path's bench-floor/rarity-floor combination.
        # craft_floor 0.4 > rare 0.25 -> 20 + 5 * 0.4**2 = 20.8
        higher_tech = roll_item(_item(SPEC), crafted=True, craft_level=5,
                                craft_rarity_table=self.FORCED_RARE,
                                craft_floor=0.4, rng=FixedRNG(0.0))
        self.assertAlmostEqual(higher_tech.stat_modifiers["damage"], 20.8)
        # craft_floor 0.1 < rare 0.25 -> the rarity floor wins (20.3125).
        higher_rarity = roll_item(_item(SPEC), crafted=True, craft_level=5,
                                  craft_rarity_table=self.FORCED_RARE,
                                  craft_floor=0.1, rng=FixedRNG(0.0))
        self.assertAlmostEqual(higher_rarity.stat_modifiers["damage"],
                               20 + 5 * 0.25 ** 2)

    def test_crafted_never_draws_affixes_even_with_rarity(self):
        # R6.1's no-affix rule stays: a Rare craft (affix budget 2 on the
        # loot side) still draws nothing, even with pools offered.
        pool = [{"key": "keen", "name": "of Power", "stat": "damage_bonus",
                 "min": 2, "max": 6, "weight": 1.0}]
        spec = dict(SPEC, affix_pool="weapon")
        result = roll_item(_item(spec), crafted=True, craft_level=5,
                           craft_rarity_table=self.FORCED_RARE,
                           rng=random.Random(9),
                           affix_pools={"weapon": pool})
        self.assertEqual(result.rarity, "rare")
        self.assertEqual(result.affixes, [])

    def test_roll_and_stamp_writes_crafted_rarity(self):
        item = {}
        result = roll_and_stamp(item, _item(SPEC), crafted=True,
                                craft_level=5,
                                craft_rarity_table=self.FORCED_RARE,
                                rng=random.Random(11))
        self.assertEqual(result.rarity, "rare")
        self.assertEqual(item["rarity"], "rare")
        self.assertNotIn("affixes", item)
        self.assertIn("rolled_stats", item)
        self.assertIn("iqs", item)


class TestCraftedRarityDistribution(unittest.TestCase):
    """Statistical contract of the DEFAULT craft table: L1 crafts never
    come out Rare; L5 crafts are Rare ≈ 5% of the time (the requested
    cap); higher levels shift the mass upward; nothing ever exceeds Rare."""

    N = 4000

    def _counts(self, level, seed=7):
        rng = random.Random(seed)
        counts = {name: 0 for name in RARITY_ORDER}
        for _ in range(self.N):
            result = roll_item(_item(SPEC), crafted=True, craft_level=level,
                               rng=rng)
            counts[result.rarity] += 1
        return counts

    def test_l1_has_no_rare_and_nothing_above(self):
        counts = self._counts(1)
        self.assertEqual(counts["rare"], 0)
        self.assertEqual(counts["epic"], 0)
        self.assertEqual(counts["legendary"], 0)
        self.assertGreater(counts["common"] / self.N, 0.8)  # L1 ≈ 90%

    def test_l5_rare_is_about_five_percent(self):
        counts = self._counts(5)
        share = counts["rare"] / self.N
        # Expected 5% — a generous band around it (N=4000 → ±3.5σ ≈ 1.2%).
        self.assertGreater(share, 0.03)
        self.assertLess(share, 0.07)
        self.assertEqual(counts["epic"], 0)
        self.assertEqual(counts["legendary"], 0)

    def test_higher_level_shifts_mass_upward(self):
        rank = {name: i for i, name in enumerate(RARITY_ORDER)}

        def mean_rank(counts):
            return sum(rank[r] * n for r, n in counts.items()) / self.N

        means = [mean_rank(self._counts(level)) for level in (1, 3, 5)]
        self.assertEqual(means, sorted(means))
        self.assertLess(means[0], means[-1])


# -------------------------------------------------------------- #
#  Affix draw (item-loot-economy task 2.3 — R3.1, R3.3, R3.4, R6.1)
# -------------------------------------------------------------- #

#: A five-entry pool (≥ the legendary budget of 4) on aggregating axes.
POOL = [
    {"key": "keen", "name": "of Power", "stat": "damage_bonus",
     "min": 2, "max": 6, "weight": 1.0},
    {"key": "warding_f", "name": "of Embers", "stat": "fire_resist",
     "min": 2, "max": 6, "weight": 0.8},
    {"key": "warding_ps", "name": "of Focus", "stat": "psychic_resist",
     "min": 2, "max": 6, "weight": 0.8},
    {"key": "sturdy", "name": "of the Bulwark", "stat": "damage_reduction",
     "min": 2, "max": 6, "weight": 1.0},
    {"key": "warding_b", "name": "of Deflection", "stat": "blast_resist",
     "min": 2, "max": 6, "weight": 0.8},
]

POOLS = {"weapon": POOL}

#: SPEC + the pool name — the item draws from the weapon pool (§3.3).
AFFIX_SPEC = dict(SPEC, affix_pool="weapon")


def _forced_table(rarity):
    """A rarity table whose single bucket forces *rarity*."""
    return {"only": {"min_weight": 0.0, "weights": {rarity: 1}}}


class TestAffixBudgets(unittest.TestCase):
    """R3.1: the affix count equals the rarity tier's budget (design §3.1
    — Common 0 / Uncommon 1 / Rare 2 / Epic 3 / Legendary 4)."""

    def test_budgets_match_design_table(self):
        self.assertEqual(RARITY_AFFIX_BUDGETS, {
            "common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4,
        })
        self.assertEqual(rarity_affix_budget("legendary"), 4)
        self.assertEqual(rarity_affix_budget("Epic"), 3)  # case-blind
        self.assertEqual(rarity_affix_budget("common"), 0)
        self.assertEqual(rarity_affix_budget(None), 0)
        self.assertEqual(rarity_affix_budget("nonsense"), 0)

    def test_affix_count_equals_budget_per_tier(self):
        for rarity, budget in RARITY_AFFIX_BUDGETS.items():
            result = roll_item(_item(AFFIX_SPEC), rng=random.Random(3),
                               rarity_table=_forced_table(rarity),
                               affix_pools=POOLS)
            self.assertEqual(result.rarity, rarity)
            self.assertEqual(len(result.affixes), budget,
                             f"{rarity} should draw {budget} affixes")

    def test_budget_exceeding_pool_draws_what_is_available(self):
        small_pool = {"weapon": POOL[:2]}  # legendary budget 4 > pool 2
        result = roll_item(_item(AFFIX_SPEC), rng=random.Random(3),
                           rarity_table=_forced_table("legendary"),
                           affix_pools=small_pool)
        self.assertEqual(len(result.affixes), 2)


class TestAffixNoDuplicates(unittest.TestCase):
    """R3.4: affixes are drawn WITHOUT replacement — no duplicate keys,
    all drawn from the item's category pool."""

    def test_no_duplicate_keys_across_many_seeds(self):
        pool_keys = {entry["key"] for entry in POOL}
        for seed in range(200):
            result = roll_item(_item(AFFIX_SPEC), rng=random.Random(seed),
                               rarity_table=_forced_table("legendary"),
                               affix_pools=POOLS)
            keys = [affix["key"] for affix in result.affixes]
            self.assertEqual(len(keys), len(set(keys)),
                             f"seed {seed} drew duplicate affixes: {keys}")
            self.assertTrue(set(keys) <= pool_keys)

    def test_duplicate_pool_keys_deduplicated_before_draw(self):
        # Defensive (load-validated, never trusted): a pool with a repeated
        # key can still never produce a duplicate draw.
        dup_pool = {"weapon": [POOL[0], dict(POOL[0]), POOL[1]]}
        result = roll_item(_item(AFFIX_SPEC), rng=random.Random(1),
                           rarity_table=_forced_table("legendary"),
                           affix_pools=dup_pool)
        keys = [affix["key"] for affix in result.affixes]
        self.assertEqual(sorted(keys), ["keen", "warding_f"])


class TestAffixWeightedDraw(unittest.TestCase):
    """Review F2: ``weight`` is a relative DRAW weight (affixes.yaml
    documents it as such) — the pick is weight-proportional without
    replacement, not uniform. Missing/non-positive weights default to
    1.0; the draw stays deterministic under an injected seed."""

    N = 30_000

    def _single_draw_counts(self, pool, seed=42):
        rng = random.Random(seed)
        counts = {entry["key"]: 0 for entry in pool}
        for _ in range(self.N):
            drawn = draw_affixes(pool, 1, rng=rng)
            counts[drawn[0]["key"]] += 1
        return counts

    def test_weight_three_drawn_about_three_times_as_often(self):
        pool = [
            {"key": "heavy", "name": "of Heft", "stat": "damage_bonus",
             "min": 2, "max": 6, "weight": 3.0},
            {"key": "light", "name": "of Air", "stat": "fire_resist",
             "min": 2, "max": 6, "weight": 1.0},
        ]
        counts = self._single_draw_counts(pool)
        # Expected 75% / 25%. N=30k → sigma ≈ 0.25%; ±2% is ~8 sigma.
        heavy_share = counts["heavy"] / self.N
        self.assertGreater(heavy_share, 0.73)
        self.assertLess(heavy_share, 0.77)
        # The ratio itself reads ~3:1.
        ratio = counts["heavy"] / counts["light"]
        self.assertGreater(ratio, 2.6)
        self.assertLess(ratio, 3.4)

    def test_missing_and_non_positive_weights_default_to_one(self):
        pool = [
            {"key": "a", "name": "A", "stat": "damage_bonus",
             "min": 2, "max": 6},                    # no weight → 1.0
            {"key": "b", "name": "B", "stat": "fire_resist",
             "min": 2, "max": 6, "weight": 0},       # non-positive → 1.0
            {"key": "c", "name": "C", "stat": "blast_resist",
             "min": 2, "max": 6, "weight": "junk"},  # non-numeric → 1.0
        ]
        counts = self._single_draw_counts(pool)
        # All three behave as weight 1.0 → ~1/3 each (±3%).
        for key, n in counts.items():
            share = n / self.N
            self.assertGreater(share, 0.30, key)
            self.assertLess(share, 0.37, key)

    def test_weighted_draw_without_replacement_no_duplicates(self):
        # The no-dup contract (R3.4) survives the weighted pick: even a
        # crushing weight can only be drawn once.
        pool = [dict(POOL[0], weight=1000.0)] + [dict(e) for e in POOL[1:]]
        for seed in range(100):
            drawn = draw_affixes(pool, 4, rng=random.Random(seed))
            keys = [affix["key"] for affix in drawn]
            self.assertEqual(len(keys), len(set(keys)),
                             f"seed {seed} drew duplicates: {keys}")
            # The 1000-weight entry is a near-certain first pick.
            self.assertEqual(keys[0], "keen")

    def test_weighted_draw_deterministic_under_seed(self):
        pool = [dict(e) for e in POOL]
        a = draw_affixes(pool, 3, rng=random.Random(99))
        b = draw_affixes([dict(e) for e in POOL], 3, rng=random.Random(99))
        self.assertEqual(a, b)

    def test_scripted_zero_draw_picks_first_candidate(self):
        # r = 0 * total = 0 lands in the FIRST entry's cumulative slot —
        # the anchor the FixedRNG-scripted tests elsewhere rely on.
        drawn = draw_affixes([dict(e) for e in POOL], 1,
                             rng=FixedRNG(0.0, 0.5))
        self.assertEqual(drawn[0]["key"], POOL[0]["key"])


class TestAffixMagnitudeAndValue(unittest.TestCase):
    """§3.3/§2.2: each affix rolls its magnitude in its own [min, max]
    band with the same skew, and carries value = weight * q * SCALE."""

    def test_magnitudes_stay_inside_entry_bands(self):
        by_key = {entry["key"]: entry for entry in POOL}
        for seed in range(100):
            result = roll_item(_item(AFFIX_SPEC), rng=random.Random(seed),
                               rarity_table=_forced_table("legendary"),
                               affix_pools=POOLS)
            for affix in result.affixes:
                band = by_key[affix["key"]]
                self.assertTrue(
                    band["min"] <= affix["magnitude"] <= band["max"],
                    f"{affix['key']} magnitude {affix['magnitude']} escaped "
                    f"[{band['min']}, {band['max']}]")

    def test_stored_shape_and_value_formula(self):
        # One-entry pool, budget 1 (uncommon). Scripted rng: draw #1 picks
        # the entry (idx 0), draw #2 is the magnitude U=1.0 → band max
        # (q=1) → value = weight * 1 * AFFIX_VALUE_SCALE.
        pool = {"weapon": [POOL[0]]}
        result = roll_item(_item(AFFIX_SPEC),
                           rng=FixedRNG(0.0, 1.0),  # rarity draw, then U=1
                           rarity_table=_forced_table("uncommon"),
                           affix_pools=pool)
        self.assertEqual(len(result.affixes), 1)
        affix = result.affixes[0]
        self.assertEqual(affix["key"], "keen")
        self.assertEqual(affix["name"], "of Power")
        self.assertEqual(affix["stat"], "damage_bonus")
        self.assertEqual(affix["magnitude"], 6.0)  # U=1 → band max
        self.assertAlmostEqual(affix["value"], 1.0 * 1.0 * AFFIX_VALUE_SCALE)

    def test_min_magnitude_scores_zero_value(self):
        # U=0 → band min (q=0) → value 0: a bottom-rolled affix adds no
        # displayed score (design §2.2 — value tracks the roll quality).
        pool = {"weapon": [POOL[0]]}
        result = roll_item(_item(AFFIX_SPEC),
                           rng=FixedRNG(0.0),
                           rarity_table=_forced_table("uncommon"),
                           affix_pools=pool)
        affix = result.affixes[0]
        self.assertEqual(affix["magnitude"], 2.0)
        self.assertEqual(affix["value"], 0.0)

    def test_affix_draw_deterministic_under_seed(self):
        a = roll_item(_item(AFFIX_SPEC), rng=random.Random(77),
                      rarity_table=_forced_table("epic"), affix_pools=POOLS)
        b = roll_item(_item(AFFIX_SPEC), rng=random.Random(77),
                      rarity_table=_forced_table("epic"), affix_pools=POOLS)
        self.assertEqual(a.affixes, b.affixes)
        self.assertEqual(a.stat_modifiers, b.stat_modifiers)


class TestAffixDrawGuards(unittest.TestCase):
    """R6.1 + never-raise (R1.5): crafted items and unusable pools/specs
    draw nothing; malformed entries are skipped, never raised on."""

    def test_crafted_items_never_receive_affixes(self):
        result = roll_item(_item(AFFIX_SPEC), crafted=True,
                           rng=random.Random(5), affix_pools=POOLS)
        self.assertEqual(result.affixes, [])

    def test_spec_without_affix_pool_draws_nothing(self):
        result = roll_item(_item(SPEC), rng=random.Random(5),
                           rarity_table=_forced_table("legendary"),
                           affix_pools=POOLS)
        self.assertEqual(result.affixes, [])

    def test_unknown_pool_name_draws_nothing(self):
        spec = dict(SPEC, affix_pool="no_such_pool")
        result = roll_item(_item(spec), rng=random.Random(5),
                           rarity_table=_forced_table("legendary"),
                           affix_pools=POOLS)
        self.assertEqual(result.affixes, [])

    def test_common_rarity_draws_nothing(self):
        result = roll_item(_item(AFFIX_SPEC), rng=random.Random(5),
                           rarity_table=_forced_table("common"),
                           affix_pools=POOLS)
        self.assertEqual(result.affixes, [])

    def test_malformed_entries_are_skipped(self):
        junk_pool = [
            "not_a_dict",
            {"key": "no_stat", "min": 2, "max": 6, "weight": 1},
            {"key": "bad_band", "stat": "fire_resist", "min": 6, "max": 2},
            {"stat": "fire_resist", "min": 2, "max": 6},  # no key
            {"key": "good", "name": "of Good", "stat": "fire_resist",
             "min": 2, "max": 6, "weight": 1},
        ]
        drawn = draw_affixes(junk_pool, 4, rng=random.Random(1))
        self.assertEqual([affix["key"] for affix in drawn], ["good"])

    def test_draw_affixes_never_raises(self):
        self.assertEqual(draw_affixes(None, 2, rng=random.Random(1)), [])
        self.assertEqual(draw_affixes("junk", 2, rng=random.Random(1)), [])
        self.assertEqual(draw_affixes(POOL, 0, rng=random.Random(1)), [])
        self.assertEqual(draw_affixes(POOL, -3, rng=random.Random(1)), [])
        self.assertEqual(draw_affixes(POOL, "many", rng=random.Random(1)), [])
        # Invalid skew falls back to the default rather than raising.
        drawn = draw_affixes(POOL, 1, skew="steep", rng=FixedRNG(0.0, 0.5))
        self.assertEqual(len(drawn), 1)

    def test_degenerate_band_gets_full_value(self):
        # min == max: the magnitude is fixed; q defaults to 1.0.
        pool = [{"key": "flat", "name": "of Flat", "stat": "fire_resist",
                 "min": 4, "max": 4, "weight": 0.5}]
        drawn = draw_affixes(pool, 1, rng=random.Random(1))
        self.assertEqual(drawn[0]["magnitude"], 4.0)
        self.assertAlmostEqual(drawn[0]["value"], 0.5 * AFFIX_VALUE_SCALE)


class TestProcAffixDraw(unittest.TestCase):
    """Task 3.4: proc affixes (``proc: poison``) are drawable — stored as
    ``{key, name, proc, magnitude, value}`` with NO ``stat`` key, so the
    stat-read path (``get_stat``/``get_stat_total``) never sees them and
    the combat proc dispatch consumes the magnitude instead."""

    VIPER = {"key": "venomous", "name": "of the Viper", "proc": "poison",
             "min": 1, "max": 3, "weight": 1.6}

    def test_proc_entry_drawn_with_proc_shape(self):
        # Scripted rng: pick idx 0, then magnitude U=1.0 → band max (q=1).
        drawn = draw_affixes([self.VIPER], 1, rng=FixedRNG(0.0, 1.0))
        self.assertEqual(len(drawn), 1)
        affix = drawn[0]
        self.assertEqual(affix["key"], "venomous")
        self.assertEqual(affix["name"], "of the Viper")
        self.assertEqual(affix["proc"], "poison")
        self.assertNotIn("stat", affix)
        self.assertEqual(affix["magnitude"], 3.0)  # U=1 → band max
        self.assertAlmostEqual(affix["value"], 1.6 * 1.0 * AFFIX_VALUE_SCALE)

    def test_proc_magnitude_clamped_to_band(self):
        for seed in range(20):
            drawn = draw_affixes([self.VIPER], 1, rng=random.Random(seed))
            self.assertTrue(1.0 <= drawn[0]["magnitude"] <= 3.0)

    def test_mixed_pool_draws_both_shapes(self):
        pool = [
            {"key": "long", "name": "of Reach", "stat": "range",
             "min": 1, "max": 3, "weight": 1.4},
            self.VIPER,
        ]
        drawn = draw_affixes(pool, 2, rng=random.Random(3))
        by_key = {a["key"]: a for a in drawn}
        self.assertEqual(set(by_key), {"long", "venomous"})
        self.assertEqual(by_key["long"]["stat"], "range")
        self.assertNotIn("proc", by_key["long"])
        self.assertEqual(by_key["venomous"]["proc"], "poison")
        self.assertNotIn("stat", by_key["venomous"])

    def test_entry_with_both_stat_and_proc_skipped(self):
        # Exactly one of stat/proc (mirrors validate_affixes) — a botched
        # entry is skipped, never drawn (R1.5 degrade-don't-trust).
        both = dict(self.VIPER, stat="damage_bonus")
        drawn = draw_affixes([both], 1, rng=random.Random(1))
        self.assertEqual(drawn, [])

    def test_proc_draw_deterministic_under_seed(self):
        a = draw_affixes([self.VIPER], 1, rng=random.Random(9))
        b = draw_affixes([self.VIPER], 1, rng=random.Random(9))
        self.assertEqual(a, b)


class TestRollAndStampAffixes(unittest.TestCase):
    """Task 2.3: roll_and_stamp writes ``affixes`` onto the item when any
    were drawn — and never writes the key when none were (R12)."""

    def test_stamps_affixes_on_loot_roll(self):
        item = {}
        result = roll_and_stamp(item, _item(AFFIX_SPEC),
                                rng=random.Random(11),
                                rarity_table=_forced_table("legendary"),
                                affix_pools=POOLS)
        self.assertEqual(len(result.affixes), 4)
        self.assertEqual(item["affixes"], result.affixes)
        # Stamped as plain copies, not shared with the result's dicts.
        self.assertIsNot(item["affixes"][0], result.affixes[0])

    def test_no_affixes_key_written_for_crafted(self):
        item = {}
        roll_and_stamp(item, _item(AFFIX_SPEC), crafted=True,
                       rng=random.Random(11), affix_pools=POOLS)
        self.assertNotIn("affixes", item)
        self.assertIn("rolled_stats", item)

    def test_no_affixes_key_written_without_pools(self):
        item = {}
        roll_and_stamp(item, _item(AFFIX_SPEC), rng=random.Random(11),
                       rarity_table=_forced_table("legendary"))
        self.assertNotIn("affixes", item)
        self.assertIn("rolled_stats", item)

    def test_no_affixes_key_written_for_common(self):
        item = {}
        roll_and_stamp(item, _item(AFFIX_SPEC), rng=random.Random(11),
                       rarity_table=_forced_table("common"),
                       affix_pools=POOLS)
        self.assertNotIn("affixes", item)

    def test_live_registry_pool_shape_is_drawable(self):
        # The shipped affixes.yaml shape ({pool: [entry, ...]}) round-trips
        # through the draw: keys/stat/magnitude all land on the item.
        item = {}
        roll_and_stamp(item, _item(AFFIX_SPEC), rng=random.Random(2),
                       rarity_table=_forced_table("rare"),
                       affix_pools={"weapon": [dict(e) for e in POOL]})
        self.assertEqual(len(item["affixes"]), 2)
        for affix in item["affixes"]:
            self.assertEqual(
                set(affix), {"key", "name", "stat", "magnitude", "value"})


# -------------------------------------------------------------- #
#  Displayed score + recompute_iqs (task 2.4 — R2.2, R2.4)
# -------------------------------------------------------------- #

class TestDisplayedScoreMath(unittest.TestCase):
    """Task 2.4 (design §2.2): the stamped iqs is the DISPLAYED score
    IQS_base + Σ affix.value — it can exceed 100 and is never clamped."""

    def test_displayed_iqs_adds_affix_values(self):
        affixes = [{"value": 7.4}, {"value": 20.0}]
        self.assertEqual(displayed_iqs(85, affixes), 112)  # > 100, unclamped

    def test_displayed_iqs_without_affixes_equals_base(self):
        self.assertEqual(displayed_iqs(73, []), 73)
        self.assertEqual(displayed_iqs(73, None), 73)

    def test_displayed_iqs_none_base_stays_none(self):
        self.assertIsNone(displayed_iqs(None, [{"value": 20.0}]))
        self.assertIsNone(displayed_iqs("high", [{"value": 20.0}]))

    def test_affix_value_total_skips_malformed(self):
        affixes = [
            {"value": 5.0},
            {"value": "big"},          # non-numeric — skipped
            {"magnitude": 4},           # no value — skipped
            "not_a_dict",
            {"value": True},            # bool — skipped (schema convention)
            {"value": 2.5},
        ]
        self.assertEqual(affix_value_total(affixes), 7.5)
        self.assertEqual(affix_value_total(None), 0.0)
        self.assertEqual(affix_value_total("junk"), 0.0)

    def test_roll_item_iqs_is_base_plus_affix_values(self):
        result = roll_item(_item(AFFIX_SPEC), rng=random.Random(11),
                           rarity_table=_forced_table("legendary"),
                           affix_pools=POOLS)
        base = compute_iqs(result.stat_modifiers, AFFIX_SPEC)
        expected = round(base + sum(a["value"] for a in result.affixes))
        self.assertEqual(len(result.affixes), 4)
        self.assertEqual(result.iqs, expected)

    def test_stamped_score_can_exceed_100(self):
        # Scripted rng: draw 1 picks the (forced) legendary rarity; draws
        # 2-3 roll both stats at U=1 → band max → base IQS 100. The affix
        # pool has one degenerate-band entry (q defaults to 1.0), so its
        # value is weight * 1 * AFFIX_VALUE_SCALE = 20 → score 120.
        pool = {"weapon": [{"key": "flat", "name": "of Flat",
                            "stat": "damage_bonus", "min": 4, "max": 4,
                            "weight": 2.0}]}
        item = {}
        result = roll_and_stamp(item, _item(AFFIX_SPEC),
                                rng=FixedRNG(0.0, 1.0),
                                rarity_table=_forced_table("legendary"),
                                affix_pools=pool)
        self.assertEqual(result.iqs, 120)
        self.assertEqual(item["iqs"], 120)  # stored unclamped (§2.2)

    def test_no_affixes_stamps_base_score_unchanged(self):
        # Production-drop / craft treatment: no pools → iqs == base IQS.
        item = {}
        result = roll_and_stamp(item, _item(SPEC), rng=random.Random(11))
        self.assertEqual(item["iqs"],
                         compute_iqs(result.stat_modifiers, SPEC))


class TestRecomputeIQS(unittest.TestCase):
    """Task 2.4 (R2.4, design §2.3): recompute_iqs is the SINGLE WRITER —
    it re-stamps the displayed score after any roll/affix change, and the
    spawn stamp routes through the same math."""

    def test_restamps_after_simulated_reroll(self):
        item = {}
        roll_and_stamp(item, _item(SPEC), rng=random.Random(7),
                       rarity_table={})
        item["rolled_stats"] = {"damage": 30, "range": 7}  # all-max reroll
        self.assertEqual(recompute_iqs(item, SPEC), 100)
        self.assertEqual(item["iqs"], 100)

    def test_restamps_after_simulated_affix_change(self):
        item = {"rolled_stats": {"damage": 30, "range": 7}}
        self.assertEqual(recompute_iqs(item, SPEC), 100)
        # An insert/affix lands on the item: values add on top (R2.2).
        item["affixes"] = [{"key": "keen", "stat": "damage_bonus",
                            "magnitude": 4, "value": 12.0}]
        self.assertEqual(recompute_iqs(item, SPEC), 112)
        self.assertEqual(item["iqs"], 112)

    def test_stamp_path_and_recompute_agree(self):
        # Single-writer discipline: recomputing an untouched item yields
        # exactly the score the spawn path stamped.
        item = {}
        result = roll_and_stamp(item, _item(AFFIX_SPEC),
                                rng=random.Random(23),
                                rarity_table=_forced_table("epic"),
                                affix_pools=POOLS)
        stamped = item["iqs"]
        self.assertEqual(stamped, result.iqs)
        self.assertEqual(recompute_iqs(item, AFFIX_SPEC), stamped)
        self.assertEqual(item["iqs"], stamped)

    def test_accepts_item_def_as_spec_source(self):
        item = {"rolled_stats": {"damage": 30, "range": 7}}
        self.assertEqual(recompute_iqs(item, _item(SPEC)), 100)

    def test_finds_spec_via_items_own_item_def(self):
        # The GameItem shape: state on a db proxy, spec via item.item_def
        # (the registry lookup) — no explicit spec argument needed.
        class _NS:
            pass

        item = _NS()
        item.db = _NS()
        item.db.rolled_stats = {"damage": 30, "range": 4}
        item.item_def = _item(SPEC)
        self.assertEqual(recompute_iqs(item), 75)  # damage q=1 (w3) → 75
        self.assertEqual(item.db.iqs, 75)

    def test_unscorable_item_left_untouched(self):
        # No rolled stats / no spec → None, and no iqs key is written.
        for item in ({}, {"rolled_stats": {"damage": 30, "range": 7}}):
            self.assertIsNone(recompute_iqs(dict(item)))
        item = {"rolled_stats": {"damage": 30}}
        self.assertIsNone(recompute_iqs(item))
        self.assertNotIn("iqs", item)

    def test_never_raises(self):
        self.assertIsNone(recompute_iqs(None))
        self.assertIsNone(recompute_iqs(object()))
        self.assertIsNone(recompute_iqs({}, spec_source="junk"))
        self.assertIsNone(
            recompute_iqs({"rolled_stats": "junk", "affixes": 3}, SPEC))


class TestRerollBaseStats(unittest.TestCase):
    """Task 4.4 (R4.5, design §4.4): the Blacksmith reroll backend draws
    fresh LOOT-band rolls with an explicit floor — base stats only."""

    def test_uses_loot_band_never_craft_band(self):
        # U = 1.0 lands on the loot band max (30), not the craft max (25):
        # a reroll is bench work on an existing instance, never the
        # crafted floor.
        rolled = reroll_base_stats(SPEC, rng=FixedRNG(1.0))
        self.assertEqual(rolled["damage"], 30)
        self.assertEqual(rolled["range"], 7)

    def test_floor_clamps_worst_roll(self):
        # U = 0 with floor f → rolled = lo + (hi - lo) * f**skew.
        rolled = reroll_base_stats(SPEC, floor=0.4, rng=FixedRNG(0.0))
        self.assertAlmostEqual(rolled["damage"], 18 + 12 * 0.4 ** 2)
        self.assertAlmostEqual(rolled["range"], 4 + 3 * 0.4 ** 2)

    def test_deterministic_and_in_band(self):
        a = reroll_base_stats(SPEC, rng=random.Random(5))
        b = reroll_base_stats(SPEC, rng=random.Random(5))
        self.assertEqual(a, b)
        self.assertTrue(18 <= a["damage"] <= 30)
        self.assertTrue(4 <= a["range"] <= 7)

    def test_unusable_spec_returns_none(self):
        self.assertIsNone(reroll_base_stats(None, rng=FixedRNG(0.5)))
        self.assertIsNone(reroll_base_stats({}, rng=FixedRNG(0.5)))
        self.assertIsNone(reroll_base_stats({"stats": {}}, rng=FixedRNG(0.5)))
        self.assertIsNone(reroll_base_stats(
            {"stats": {"damage": {"min": 9, "max": 3}}}, rng=FixedRNG(0.5)))

    def test_never_raises_on_broken_rng(self):
        class _Boom:
            def random(self):
                raise RuntimeError("boom")

        self.assertIsNone(reroll_base_stats(SPEC, rng=_Boom()))


class TestCraftFloor(unittest.TestCase):
    """Master Gunsmithing craft floor (item-loot-economy task 6.4, R11.6):
    ``craft_floor`` U-clamps CRAFTED rolls inside the craft band — the
    exact rarity-floor mechanism — raising the low end without ever
    escaping the band, and is ignored on loot rolls."""

    def test_craft_floor_raises_the_min_crafted_roll(self):
        # SPEC craft band for damage: [20, 25]. U=0 with floor f → rolled
        # = lo + (hi - lo) * f**skew = 20 + 5 * 0.25^2 = 20.3125.
        result = roll_item(_item(SPEC), crafted=True, rng=FixedRNG(0.0),
                           craft_floor=0.25)
        self.assertAlmostEqual(result.stat_modifiers["damage"],
                               20 + 5 * 0.25 ** 2)
        # Without the floor the same U=0 rolls the craft-band minimum.
        no_floor = roll_item(_item(SPEC), crafted=True, rng=FixedRNG(0.0))
        self.assertEqual(no_floor.stat_modifiers["damage"], 20)

    def test_craft_floor_never_escapes_the_craft_band(self):
        # U=1 still tops out at the CRAFT max (25), never the loot max
        # (30) — the floor lifts the bottom, not the top (R6.1).
        result = roll_item(_item(SPEC), crafted=True, rng=FixedRNG(1.0),
                           craft_floor=0.25)
        self.assertEqual(result.stat_modifiers["damage"], 25)
        rng = random.Random(42)
        for _ in range(300):
            result = roll_item(_item(SPEC), crafted=True, rng=rng,
                               craft_floor=0.25)
            self.assertTrue(
                20 <= result.stat_modifiers["damage"] <= 25,
                f"crafted roll {result.stat_modifiers['damage']} escaped "
                "the craft band")

    def test_craft_floor_ignored_on_loot_rolls(self):
        # A loot roll's floor is its RARITY floor; craft_floor must not
        # leak in (U=0, no rarity table → band minimum despite the param).
        result = roll_item(_item(SPEC), crafted=False, rng=FixedRNG(0.0),
                           rarity_table={}, craft_floor=0.9)
        self.assertEqual(result.stat_modifiers["damage"], 18)

    def test_invalid_craft_floor_degrades_to_no_floor(self):
        for bad in (-0.5, 0.0, 1.0, 2.0, "high", None):
            result = roll_item(_item(SPEC), crafted=True, rng=FixedRNG(0.0),
                               craft_floor=bad)
            self.assertEqual(result.stat_modifiers["damage"], 20,
                             f"craft_floor={bad!r} should mean no floor")

    def test_roll_and_stamp_passes_craft_floor_through(self):
        item = {}
        roll_and_stamp(item, _item(SPEC), crafted=True, rng=FixedRNG(0.0),
                       craft_floor=0.25)
        self.assertAlmostEqual(item["rolled_stats"]["damage"],
                               20 + 5 * 0.25 ** 2)
        # Crafted contract intact: no rarity, no affixes (R6.1).
        self.assertNotIn("rarity", item)
        self.assertNotIn("affixes", item)


class TestStatsAtQuality(unittest.TestCase):
    """stats_at_quality: the '@item spawn iqs=<N>' deterministic stamp —
    every stat at the same fraction of its loot band, so compute_iqs
    reads back exactly round(100*q)."""

    def test_values_land_at_the_quality_fraction(self):
        rolled = stats_at_quality(SPEC, 0.5)
        self.assertAlmostEqual(rolled["damage"], 24.0)  # 18 + 0.5*12
        self.assertAlmostEqual(rolled["range"], 5.5)    # 4 + 0.5*3

    def test_iqs_reads_back_the_requested_value(self):
        # All stats at the same q → the weighted mean is q, whatever the
        # weights: the operator's requested value IS the stamped base IQS.
        for q in (0.0, 0.25, 0.5, 0.9, 1.0):
            rolled = stats_at_quality(SPEC, q)
            self.assertEqual(compute_iqs(rolled, SPEC), round(100 * q))

    def test_quality_clamped_into_zero_one(self):
        self.assertEqual(stats_at_quality(SPEC, -3.0),
                         stats_at_quality(SPEC, 0.0))
        self.assertEqual(stats_at_quality(SPEC, 7.0),
                         stats_at_quality(SPEC, 1.0))
        self.assertEqual(stats_at_quality(SPEC, 1.0)["damage"], 30)
        self.assertEqual(stats_at_quality(SPEC, 0.0)["damage"], 18)

    def test_always_uses_the_loot_band_never_the_craft_band(self):
        # q=1 → loot max (30), not the craft max (25) — the admin stamp is
        # a loot-band position, never a craft roll.
        self.assertEqual(stats_at_quality(SPEC, 1.0)["damage"], 30)

    def test_unusable_spec_returns_none(self):
        for bad in (None, "weapon", {}, {"stats": {}}, {"stats": "junk"}):
            self.assertIsNone(stats_at_quality(bad, 0.5))
        self.assertIsNone(stats_at_quality(SPEC, "high"))

    def test_malformed_band_skipped(self):
        spec = {"stats": {
            "damage": {"min": 18, "max": 30},
            "range": "not_a_band",
        }}
        rolled = stats_at_quality(spec, 0.5)
        self.assertIn("damage", rolled)
        self.assertNotIn("range", rolled)


if __name__ == "__main__":
    unittest.main()
