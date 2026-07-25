"""
Property-based tests for the loot roller (item-loot-economy tasks 1.2/1.4/2.2).

# Feature: item-loot-economy, Property 1: Roll validity, clamping, and determinism
# Feature: item-loot-economy, Property 2: Skew distribution shape
# Feature: item-loot-economy, Property 3: IQS formula bounds and weighting
# Feature: item-loot-economy, Property 4: Crafted band containment and no
# crafted affixes (partial — roller side; the no-affix draw is Phase 2)
# Feature: item-loot-economy, Property 5: Rarity contract — affix budget,
# floor clamp, no duplicates

**Validates: Requirements 1.1, 1.2, 1.4, 1.5, 2.1, 2.2, 2.4, 3.1, 3.2,
3.3, 3.4, 6.1**
"""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from mygame.world.definitions import ItemDef
from mygame.world.systems.loot_roller import (
    AFFIX_VALUE_SCALE,
    DEFAULT_LOOT_ROLL_SKEW,
    RARITY_AFFIX_BUDGETS,
    RARITY_ORDER,
    RollResult,
    compute_iqs,
    rarity_roll_floor,
    recompute_iqs,
    roll_and_stamp,
    roll_item,
)

# ------------------------------------------------------------------ #
#  Strategies
# ------------------------------------------------------------------ #

#: Plausible stat keys — the roller treats them as opaque names.
_stat_names = st.sampled_from(
    ["damage", "range", "damage_reduction", "fire_resist",
     "psychic_resist", "damage_bonus", "regen", "sight_range"]
)

_finite = {"allow_nan": False, "allow_infinity": False}


@st.composite
def _loot_band(draw, min_width=0.0):
    """A valid {min, max, weight} band with max = min + width."""
    lo = draw(st.floats(min_value=-500, max_value=500, **_finite))
    width = draw(st.floats(min_value=min_width, max_value=300, **_finite))
    weight = draw(st.floats(min_value=0.1, max_value=10, **_finite))
    return {"min": lo, "max": lo + width, "weight": weight}


@st.composite
def _roll_spec(draw, with_craft=False):
    """A valid roll_spec; craft bands (if any) are contained in loot bands."""
    stats = draw(
        st.dictionaries(_stat_names, _loot_band(), min_size=1, max_size=4)
    )
    spec = {"stats": stats}
    if draw(st.booleans()):
        spec["skew"] = draw(st.floats(min_value=1, max_value=6, **_finite))
    if with_craft:
        craft = {}
        for stat, band in stats.items():
            if not draw(st.booleans()):
                continue  # this stat keeps its loot band when crafted
            lo, hi = band["min"], band["max"]
            f1 = draw(st.floats(min_value=0, max_value=1, **_finite))
            f2 = draw(st.floats(min_value=0, max_value=1, **_finite))
            f1, f2 = min(f1, f2), max(f1, f2)
            craft[stat] = {"min": lo + f1 * (hi - lo),
                           "max": lo + f2 * (hi - lo)}
        if craft:
            spec["craft"] = craft
    return spec


def _item(spec):
    return ItemDef(key="test_item", name="Test Item", slot="weapon",
                   category="weapon", roll_spec=spec)


# ------------------------------------------------------------------ #
#  Property 1: Roll validity, clamping, and determinism
#  # Feature: item-loot-economy, Property 1: Roll validity, clamping,
#  # and determinism
#  **Validates: Requirements 1.1, 1.5**
# ------------------------------------------------------------------ #

class TestProperty1RollValidityClampingDeterminism:
    """For any valid roll_spec and any RNG seed, roll_item never raises,
    every rolled stat lies within its [min, max] band, and two calls with
    the same injected seed produce identical results."""

    @settings(max_examples=100)
    @given(
        spec=_roll_spec(),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
        crafted=st.booleans(),
        rarity_weight=st.floats(min_value=0, max_value=10, **_finite),
    )
    def test_prop_roll_validity_clamping_determinism(
        self, spec, seed, crafted, rarity_weight
    ):
        item_def = _item(spec)

        result = roll_item(
            item_def,
            source_rarity_weight=rarity_weight,
            crafted=crafted,
            rng=random.Random(seed),
        )

        # A valid spec always yields a result (R1.1) — and never raises
        # (R1.5; reaching this line at all is the never-raise check).
        assert isinstance(result, RollResult)

        # Every rolled stat is one the spec declares, clamped to its band.
        assert set(result.stat_modifiers) == set(spec["stats"])
        for stat, value in result.stat_modifiers.items():
            band = spec["stats"][stat]
            assert band["min"] <= value <= band["max"], (
                f"{stat}={value} escaped band "
                f"[{band['min']}, {band['max']}]"
            )

        # Determinism: same spec + same seed -> identical rolls AND the
        # identical rarity assignment (R1.5, task 2.2).
        again = roll_item(
            item_def,
            source_rarity_weight=rarity_weight,
            crafted=crafted,
            rng=random.Random(seed),
        )
        assert again.stat_modifiers == result.stat_modifiers
        assert again.rarity == result.rarity


# ------------------------------------------------------------------ #
#  Property 2: Skew distribution shape
#  # Feature: item-loot-economy, Property 2: Skew distribution shape
#  **Validates: Requirements 1.2**
# ------------------------------------------------------------------ #

class TestProperty2SkewDistributionShape:
    """For k > 1 the sample median of many rolls falls below the band
    midpoint, near min + (max - min) * 0.5**k (design §1.3)."""

    @settings(max_examples=100, deadline=None)
    @given(
        lo=st.floats(min_value=-500, max_value=500, **_finite),
        width=st.floats(min_value=1, max_value=300, **_finite),
        skew=st.floats(min_value=1.3, max_value=5, **_finite),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
    )
    def test_prop_skew_median_below_midpoint(self, lo, width, skew, seed):
        n = 801
        spec = {
            "stats": {"damage": {"min": lo, "max": lo + width, "weight": 1}},
            "skew": skew,
        }
        item_def = _item(spec)
        rng = random.Random(seed)

        # rarity_table={} disables rarity assignment: this property is
        # about the RAW U**skew shape — the rarity roll-floor deliberately
        # reshapes rare+ rolls and is Property 5's subject (task 2.2).
        samples = sorted(
            roll_item(item_def, rng=rng,
                      rarity_table={}).stat_modifiers["damage"]
            for _ in range(n)
        )
        sample_median = samples[n // 2]

        midpoint = lo + width * 0.5
        expected_median = lo + width * (0.5 ** skew)

        # Top rolls are rare: the mass sits low in the band (R1.2).
        assert sample_median < midpoint
        # And the median tracks the analytic U**skew median. Tolerance is
        # ~6 sigma of the order-statistic noise at n=801 for k<=5.
        assert abs(sample_median - expected_median) < 0.12 * width


# ------------------------------------------------------------------ #
#  Property 4 (partial): Crafted band containment
#  # Feature: item-loot-economy, Property 4: Crafted band containment
#  # and no crafted affixes
#  **Validates: Requirements 1.4, 6.1**
# ------------------------------------------------------------------ #

class TestProperty4CraftedBandContainment:
    """A crafted roll always lands within the craft band, which is itself
    contained in the loot band; crafted results carry no affixes. (The
    affix *draw* logic is Phase 2 — here the roller's crafted output must
    already be affix-free.) Updated for the crafted-rarity change
    (deliberate deviation from R6.1, per user request): a craft_level is
    drawn too — even a Rare craft (whose 0.25 roll floor applies) must
    stay INSIDE the craft band and affix-free."""

    @settings(max_examples=100)
    @given(
        spec=_roll_spec(with_craft=True),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
        craft_level=st.integers(min_value=0, max_value=5),
    )
    def test_prop_crafted_roll_in_craft_band(self, spec, seed, craft_level):
        result = roll_item(_item(spec), crafted=True, rng=random.Random(seed),
                           craft_level=craft_level)
        assert isinstance(result, RollResult)

        craft = spec.get("craft", {})
        for stat, value in result.stat_modifiers.items():
            loot = spec["stats"][stat]
            # Craft band ⊂ loot band: the crafted roll is inside both —
            # a crafted rarity's roll floor lifts the low end but can
            # never escape the band.
            assert loot["min"] <= value <= loot["max"]
            if stat in craft:
                assert craft[stat]["min"] <= value <= craft[stat]["max"], (
                    f"crafted {stat}={value} escaped craft band "
                    f"[{craft[stat]['min']}, {craft[stat]['max']}]"
                )

        # Crafted items never receive affixes (R6.1's no-affix rule stays
        # intact even now that crafted rarity ≤ Rare is possible).
        assert result.affixes == []


# ------------------------------------------------------------------ #
#  Property 3: IQS formula bounds and weighting
#  # Feature: item-loot-economy, Property 3: IQS formula bounds and
#  # weighting
#  **Validates: Requirements 2.1, 2.2, 2.4**
# ------------------------------------------------------------------ #

@st.composite
def _scorable_spec(draw):
    """A roll_spec whose bands all have positive width (scorable)."""
    stats = draw(
        st.dictionaries(_stat_names, _loot_band(min_width=0.5),
                        min_size=1, max_size=4)
    )
    return {"stats": stats}


class TestProperty3IQSFormulaBoundsAndWeighting:
    """IQS equals the weighted mean 100 * sum(w*q)/sum(w) of per-stat roll
    quality: all-minimum rolls yield 0, all-maximum rolls yield 100, the
    score is monotone in each rolled stat (design §2.1), affix values add
    on top of the base IQS (design §2.2 — the displayed score, which may
    exceed 100), and any reroll/insert re-stamps the score to the
    recomputed value through the recompute_iqs single writer (R2.4)."""

    @settings(max_examples=100)
    @given(spec=_scorable_spec())
    def test_prop_iqs_min_zero_max_hundred(self, spec):
        stats = spec["stats"]
        all_min = {s: b["min"] for s, b in stats.items()}
        all_max = {s: b["max"] for s, b in stats.items()}
        assert compute_iqs(all_min, spec) == 0
        assert compute_iqs(all_max, spec) == 100

    @settings(max_examples=100)
    @given(
        spec=_scorable_spec(),
        data=st.data(),
    )
    def test_prop_iqs_weighted_mean(self, spec, data):
        stats = spec["stats"]
        rolled = {}
        for stat, band in stats.items():
            f = data.draw(st.floats(min_value=0, max_value=1, **_finite),
                          label=f"q_{stat}")
            rolled[stat] = band["min"] + f * (band["max"] - band["min"])

        score = compute_iqs(rolled, spec)
        assert score is not None
        assert 0 <= score <= 100

        # Recompute the design §2.1 formula independently.
        weighted_q = 0.0
        total_w = 0.0
        for stat, value in rolled.items():
            band = stats[stat]
            q = (float(value) - float(band["min"])) / (
                float(band["max"]) - float(band["min"]))
            q = min(max(q, 0.0), 1.0)
            weighted_q += float(band["weight"]) * q
            total_w += float(band["weight"])
        assert score == round(100.0 * weighted_q / total_w)

    @settings(max_examples=100)
    @given(
        spec=_scorable_spec(),
        data=st.data(),
    )
    def test_prop_iqs_monotone_in_each_stat(self, spec, data):
        stats = spec["stats"]
        rolled = {}
        for stat, band in stats.items():
            f = data.draw(st.floats(min_value=0, max_value=1, **_finite),
                          label=f"q_{stat}")
            rolled[stat] = band["min"] + f * (band["max"] - band["min"])

        # Raise one stat's roll (within its band): IQS never decreases.
        target = data.draw(st.sampled_from(sorted(stats)), label="stat")
        band = stats[target]
        lo_frac = (rolled[target] - band["min"]) / (band["max"] - band["min"])
        hi_frac = data.draw(
            st.floats(min_value=min(lo_frac, 1.0), max_value=1, **_finite),
            label="raised_q",
        )
        raised = dict(rolled)
        raised[target] = band["min"] + hi_frac * (band["max"] - band["min"])

        base_score = compute_iqs(rolled, spec)
        raised_score = compute_iqs(raised, spec)
        if raised[target] >= rolled[target]:
            assert raised_score >= base_score

    @settings(max_examples=100)
    @given(
        spec=_scorable_spec(),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
        rarity=st.sampled_from(["uncommon", "rare", "epic", "legendary"]),
        data=st.data(),
    )
    def test_prop_affix_values_add_on_top_of_base_iqs(
        self, spec, seed, rarity, data
    ):
        # R2.2 (design §2.2): the stamped iqs is the DISPLAYED score
        # IQS_base + sum(affix.value) — never clamped, so it can exceed
        # 100 when strong affixes ride a good base roll.
        pool = data.draw(_affix_pool(), label="pool")
        spec = dict(spec, affix_pool="weapon")
        table = {"only": {"min_weight": 0.0, "weights": {rarity: 1}}}

        item = {}
        result = roll_and_stamp(item, _item(spec), rng=random.Random(seed),
                                rarity_table=table,
                                affix_pools={"weapon": pool})

        base = compute_iqs(result.stat_modifiers, spec)
        expected = int(round(
            base + sum(affix["value"] for affix in result.affixes)))
        assert result.iqs == expected
        assert item["iqs"] == expected     # the stamp path agrees
        assert result.iqs >= base          # values only ever add (>= 0)

    @settings(max_examples=100)
    @given(
        spec=_scorable_spec(),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
        data=st.data(),
    )
    def test_prop_recompute_restamps_after_roll_or_affix_change(
        self, spec, seed, data
    ):
        # R2.4: whenever the item's rolls or affixes change (reroll,
        # insert applied), recompute_iqs — the single writer — re-stamps
        # iqs to exactly the recomputed displayed score.
        item = {}
        roll_and_stamp(item, _item(spec), rng=random.Random(seed))

        # Simulated reroll: fresh in-band values for every stat.
        new_rolled = {}
        for stat, band in spec["stats"].items():
            f = data.draw(st.floats(min_value=0, max_value=1, **_finite),
                          label=f"new_q_{stat}")
            new_rolled[stat] = band["min"] + f * (band["max"] - band["min"])
        item["rolled_stats"] = new_rolled

        # Simulated insert/affix change: replace the affix list.
        values = data.draw(
            st.lists(st.floats(min_value=0, max_value=60, **_finite),
                     max_size=4),
            label="affix_values",
        )
        item["affixes"] = [
            {"key": f"a{i}", "stat": "damage_bonus", "magnitude": 1.0,
             "value": value}
            for i, value in enumerate(values)
        ]

        restamped = recompute_iqs(item, spec)
        expected = int(round(compute_iqs(new_rolled, spec) + sum(values)))
        assert restamped == expected
        assert item["iqs"] == expected


# ------------------------------------------------------------------ #
#  Property 5: Rarity contract — affix budget, floor clamp, no duplicates
#  # Feature: item-loot-economy, Property 5: Rarity contract — affix
#  # budget, floor clamp, no duplicates
#  **Validates: Requirements 3.1, 3.3, 3.4**
# ------------------------------------------------------------------ #

#: Aggregating-axis affix stats (the Phase-2 scope — design §3.3).
_affix_stats = st.sampled_from(
    ["damage_bonus", "damage_reduction", "fire_resist",
     "psychic_resist", "blast_resist", "poison_resist"]
)


@st.composite
def _affix_pool(draw, min_size=1, max_size=8):
    """A valid affix pool: unique keys, valid magnitude bands, weights."""
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    entries = []
    for i in range(size):
        lo = draw(st.floats(min_value=0, max_value=50, **_finite))
        width = draw(st.floats(min_value=0, max_value=20, **_finite))
        entries.append({
            "key": f"affix_{i}",
            "name": f"of Test {i}",
            "stat": draw(_affix_stats),
            "min": lo,
            "max": lo + width,
            "weight": draw(st.floats(min_value=0.1, max_value=5, **_finite)),
        })
    return entries


class TestProperty5RarityContract:
    """For any assigned rarity tier: the tier is one of the five design
    §3.1 rarities; the item's affix count equals that tier's budget
    (capped by the pool size); all affix keys are unique and drawn from
    the item's category pool, each magnitude inside its entry band; and
    the rarity's roll-floor clamp guarantees base rolls at or above the
    floor fraction of each band (U clamped into [floor, 1] BEFORE the
    skew, design §1.3). Crafted rolls carry no rarity and no affixes
    (R6.1)."""

    @settings(max_examples=100)
    @given(
        spec=_roll_spec(),
        pool=_affix_pool(),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
        rarity_weight=st.floats(min_value=0, max_value=10, **_finite),
    )
    def test_prop_rarity_contract_budget_floor_no_dup(
        self, spec, pool, seed, rarity_weight
    ):
        spec = dict(spec, affix_pool="weapon")
        result = roll_item(
            _item(spec),
            source_rarity_weight=rarity_weight,
            rng=random.Random(seed),
            affix_pools={"weapon": pool},
        )
        assert isinstance(result, RollResult)

        # Loot rolls under the (default) balance table always carry one of
        # the five design §3.1 tiers (R3.1), drawn from the source bucket
        # the weight selects (R3.2).
        assert result.rarity in RARITY_ORDER

        # Affix budget (R3.1): count equals the tier's budget, capped by
        # the pool size (a small pool yields what it has).
        budget = RARITY_AFFIX_BUDGETS[result.rarity]
        assert len(result.affixes) == min(budget, len(pool))

        # No duplicates, all drawn from the item's category pool (R3.4),
        # each magnitude rolled inside its own entry band (R3.4) with a
        # value contribution bounded by weight * SCALE (design §2.2).
        by_key = {entry["key"]: entry for entry in pool}
        keys = [affix["key"] for affix in result.affixes]
        assert len(keys) == len(set(keys)), f"duplicate affix keys: {keys}"
        for affix in result.affixes:
            entry = by_key.get(affix["key"])
            assert entry is not None, (
                f"affix {affix['key']} not drawn from the pool"
            )
            assert affix["stat"] == entry["stat"]
            assert entry["min"] <= affix["magnitude"] <= entry["max"], (
                f"{affix['key']} magnitude {affix['magnitude']} escaped "
                f"band [{entry['min']}, {entry['max']}]"
            )
            assert 0.0 <= affix["value"] <= (
                entry["weight"] * AFFIX_VALUE_SCALE) + 1e-9

        # Floor clamp (R3.3): every rolled stat is at or above the
        # floor**skew fraction of its band. Common/Uncommon (floor 0)
        # degenerate to the plain band minimum.
        floor = rarity_roll_floor(result.rarity)
        skew = spec.get("skew", DEFAULT_LOOT_ROLL_SKEW)
        for stat, value in result.stat_modifiers.items():
            band = spec["stats"][stat]
            lo, hi = band["min"], band["max"]
            guaranteed = lo + (hi - lo) * (floor ** skew)
            tolerance = 1e-9 * max(1.0, abs(lo), abs(hi))
            assert value >= guaranteed - tolerance, (
                f"{result.rarity} {stat}={value} fell below the floor "
                f"guarantee {guaranteed} for band [{lo}, {hi}]"
            )

    @settings(max_examples=100)
    @given(
        spec=_roll_spec(with_craft=True),
        pool=_affix_pool(),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
        rarity_weight=st.floats(min_value=0, max_value=10, **_finite),
        craft_level=st.integers(min_value=0, max_value=9),
    )
    def test_prop_crafted_rolls_capped_at_rare_no_affixes(
        self, spec, pool, seed, rarity_weight, craft_level
    ):
        # Crafted-rarity change (deliberate deviation from R6.1, per user
        # request): crafted gear draws from the building-level craft table
        # instead of the source-weighted loot table — capped at Rare
        # (Epic/Legendary stay loot-only), and NEVER any affixes even when
        # pools are offered (R6.1's no-affix rule stays). Without a usable
        # craft_level (< 1) the original no-rarity behavior holds. The
        # loot-source rarity weight must have no effect on a crafted roll.
        spec = dict(spec, affix_pool="weapon")
        result = roll_item(
            _item(spec),
            source_rarity_weight=rarity_weight,
            crafted=True,
            rng=random.Random(seed),
            affix_pools={"weapon": pool},
            craft_level=craft_level,
        )
        if craft_level < 1:
            assert result.rarity is None
        else:
            assert result.rarity in {"common", "uncommon", "rare"}
        assert result.affixes == []
