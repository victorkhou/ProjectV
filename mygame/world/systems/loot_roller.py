"""
Loot roller — per-instance stat rolling.

A pure, RNG-injected service: given an :class:`~world.definitions.ItemDef`
with a ``roll_spec``, produce the per-instance rolled ``stat_modifiers``
stamped onto a ``GameItem``. No Evennia, no registry, no globals — all
randomness arrives through ``rng``, so rolls are deterministic under a seed.

Contract:

- ``roll_item`` never raises: malformed spec fragments are skipped and any
  unexpected failure degrades to ``None`` (a fixed, unrolled item).
- Every rolled value is clamped to its ``[min, max]`` band.

Distribution::

    rolled = min + (max - min) * (U ** skew)    # U ~ uniform(0,1), skew >= 1

``skew=2`` puts the median roll at ~25% of the band, so near-max rolls are
scarce — that scarcity is the economy.

Crafted items roll in the tighter per-stat ``craft`` band (falling back to
the loot band when absent). The craft band is always intersected with the
loot band, so a crafted roll can never escape the loot band on odd data.

IQS: :func:`compute_iqs` is the BASE score — the weighted mean of per-stat
roll quality, 0-100. The displayed score is ``IQS_base + Σ affix.value``
(:func:`displayed_iqs`), deliberately unclamped so it can read above 100;
the display layer caps rendering at 999. :func:`recompute_iqs` is the only
writer of the stamped ``iqs`` — spawn stamping and the Blacksmith
reroll/insert paths all route through it.

Rarity: a loot roll assigns a tier by weighted choice over the source
bucket's row of the rarity table (``balance.rarity_table``, mirrored by
:data:`DEFAULT_RARITY_TABLE` as a pure fallback). The drop source's
``source_rarity_weight`` (guard kill 0 < outpost 1 < stronghold 2 <
fortress 3 < citadel 4) selects the highest-threshold bucket it reaches.
The tier then raises the roll FLOOR by clamping ``U`` into ``[floor, 1]``
before the skew — Rare 0.25, Epic 0.50, Legendary 0.75 — so high rarity
guarantees good base rolls without removing variance.

Crafted rarity draws from a building-level-keyed table
(``balance.craft_rarity_table``): higher bench levels shift the
distribution up, reaching Rare at 5% at level 5 and 0% at level 1. Capped
at Rare — Epic/Legendary are loot-only — and crafted items never roll
affixes. A rare crafted item applies its 0.25 floor INSIDE the craft band.
When both a rarity floor and the Master Gunsmithing ``craft_iqs_floor``
apply, the effective floor is the ``max`` of the two (mirroring the reroll
path). A ``craft_level`` below 1 skips rarity entirely.

Affixes: the assigned rarity's budget (Common 0 → Legendary 4, see
:data:`RARITY_AFFIX_BUDGETS`) is drawn WITHOUT replacement from the pool
named by ``roll_spec.affix_pool``. Each affix rolls its magnitude in its
own band with the same skew and contributes ``value`` to the displayed
score: normalized magnitude × pool weight × :data:`AFFIX_VALUE_SCALE`. A
budget larger than the pool draws whatever is available. Crafted items and
callers passing no pools get no affixes.
"""

from __future__ import annotations

import math
import random as _random_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Global default for the U**skew exponent. Mirrors the design's
#: ``balance.loot_roll_skew`` starting value (design §9); the spawn wiring
#: passes the live balance value via ``default_skew`` once that tunable
#: lands — this constant is the pure-module fallback.
DEFAULT_LOOT_ROLL_SKEW = 2.0

#: Rarity tiers, low → high (design §3.1). Stored lowercase on instances
#: (``GameItem.db.rarity``); the display layer capitalizes + colors them.
RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")

#: Roll-floor U-clamp per rarity (design §3.1): before the skew, ``U`` is
#: clamped into ``[floor, 1]`` — so Epic/Legendary guarantee base rolls in
#: the upper part of every band without removing variance (R3.3).
RARITY_ROLL_FLOORS = {
    "common": 0.0,
    "uncommon": 0.0,
    "rare": 0.25,
    "epic": 0.50,
    "legendary": 0.75,
}

#: Affix budget per rarity tier (design §3.1, R3.1): the number of affixes
#: a loot roll of that tier draws from its category pool (task 2.3).
RARITY_AFFIX_BUDGETS = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
    "epic": 3,
    "legendary": 4,
}

#: Scale applied to an affix's normalized magnitude × pool weight to get
#: its displayed-score ``value`` contribution (design §2.2). Calibrated to
#: the design's worked example — "4 strong affixes (~27)": a strong roll
#: (q ≈ 0.7) at weight 1.0 contributes ~7 score points.
AFFIX_VALUE_SCALE = 10.0

#: Pure-module fallback for the balance ``rarity_table`` (design §3.2/§9).
#: Mirrors ``BalanceConfig.rarity_table`` — a sync test in
#: test_loot_roller.py keeps the two from drifting. Each SOURCE BUCKET row:
#: ``min_weight`` is the numeric source-rarity-weight threshold that
#: activates the bucket (the highest reached threshold wins), and
#: ``weights`` are the relative rarity odds for drops from that source.
#: Starting numbers per design §9: citadel ≈ {epic 40%, legendary 15%},
#: guard kill ≈ {common 70%, uncommon 25%, rare 5%}. Production drops pass
#: weight 0 → the lowest bucket (guard_kill) — the safe-floor treatment.
DEFAULT_RARITY_TABLE = {
    "guard_kill": {
        "min_weight": 0.0,
        "weights": {"common": 70, "uncommon": 25, "rare": 5},
    },
    "outpost": {
        "min_weight": 1.0,
        "weights": {"common": 50, "uncommon": 33, "rare": 15, "epic": 2},
    },
    "stronghold": {
        "min_weight": 2.0,
        "weights": {"common": 30, "uncommon": 35, "rare": 27, "epic": 7,
                    "legendary": 1},
    },
    "fortress": {
        "min_weight": 3.0,
        "weights": {"common": 15, "uncommon": 27, "rare": 33, "epic": 20,
                    "legendary": 5},
    },
    "citadel": {
        "min_weight": 4.0,
        "weights": {"common": 8, "uncommon": 12, "rare": 25, "epic": 40,
                    "legendary": 15},
    },
}


#: Rarity tiers a CRAFTED roll may reach (deviation-from-R6.1 decision, see
#: the module docstring): crafted gear caps at Rare — Epic/Legendary remain
#: loot-only. ``assign_craft_rarity`` filters any higher tier out of a craft
#: table row defensively, so even odd data can never mint an epic craft.
CRAFT_RARITY_TIERS = frozenset({"common", "uncommon", "rare"})

#: Pure-module fallback for the balance ``craft_rarity_table`` (see the
#: module docstring's crafted-rarity section). Mirrors
#: ``BalanceConfig.craft_rarity_table`` — a sync test in test_loot_roller.py
#: keeps the two from drifting. Keyed by the CRAFTING BUILDING's level
#: (1–5); a level above the highest key uses the highest row, below the
#: lowest key means no rarity. Weights sum to 100 per row so they read as
#: percentages. The curve: L1 has NO Rare chance; Rare rises with level to
#: EXACTLY 5% at L5 (the user-requested cap), with Uncommon steadily
#: displacing Common along the way.
DEFAULT_CRAFT_RARITY_TABLE = {
    1: {"common": 90, "uncommon": 10},
    2: {"common": 79, "uncommon": 20, "rare": 1},
    3: {"common": 68, "uncommon": 30, "rare": 2},
    4: {"common": 57, "uncommon": 40, "rare": 3},
    5: {"common": 45, "uncommon": 50, "rare": 5},
}


@dataclass
class RollResult:
    """Outcome of rolling one item instance.

    ``stat_modifiers`` holds the rolled per-instance values (stat -> float),
    ready to be written to ``GameItem.db.rolled_stats``. ``iqs`` is the
    DISPLAYED item score for those rolls (task 2.4, design §2.2):
    ``IQS_base + Σ affix.value`` via :func:`displayed_iqs` — with no
    affixes it equals the base :func:`compute_iqs` score; with affixes it
    can exceed 100. ``rarity`` is the assigned tier (task 2.2) and
    ``affixes`` the drawn affix dicts ``{key, name, stat, magnitude,
    value}`` (task 2.3), ready for ``GameItem.db.affixes``.
    """

    stat_modifiers: dict[str, float] = field(default_factory=dict)
    affixes: list = field(default_factory=list)
    rarity: str | None = None
    iqs: int | None = None


def _num(val: Any) -> bool:
    """True for real, FINITE numbers (bool excluded, matching the schema
    validator's ``_is_num``).

    NaN/inf are rejected because every NaN comparison is False, so a NaN
    slipping past a ``>``/``<`` guard silently takes the wrong branch — a NaN
    source weight would resolve to the HIGHEST rarity bucket, since no
    ``threshold > nan`` comparison ever skips a row. Non-finite values degrade
    exactly like non-numbers.
    """
    return (isinstance(val, (int, float)) and not isinstance(val, bool)
            and math.isfinite(val))


def _resolve_skew(roll_spec: dict, default_skew: float) -> float:
    """The U**skew exponent: per-item ``roll_spec.skew``, else the default.

    Invalid values (non-numeric, < 1) fall back to the default — load-time
    validation rejects them, but the never-raise contract means we degrade
    rather than trust (R1.5).
    """
    skew = roll_spec.get("skew")
    if _num(skew) and skew >= 1:
        return float(skew)
    if _num(default_skew) and default_skew >= 1:
        return float(default_skew)
    return DEFAULT_LOOT_ROLL_SKEW


def _roll_band(lo: float, hi: float, skew: float, rng,
               floor: float = 0.0) -> float:
    """One skewed roll in ``[lo, hi]``: lo + (hi - lo) * U**skew, clamped.

    ``floor`` is the rarity roll-floor U-clamp (design §1.3/§3.1): ``U`` is
    clamped into ``[floor, 1]`` BEFORE the skew, so the roll can never land
    below the ``floor**skew`` fraction of the band — high rarity guarantees
    good base rolls without removing variance (R3.3).
    """
    u = rng.random()
    if _num(floor) and 0.0 < floor < 1.0:
        u = floor + (1.0 - floor) * u  # clamp U into [floor, 1] (§1.3)
    rolled = lo + (hi - lo) * (u ** skew)
    # Clamp defensively (R1.5) — float edge cases must never escape the band.
    return min(max(rolled, lo), hi)


def rarity_roll_floor(rarity) -> float:
    """The roll-floor U-clamp for *rarity* (design §3.1); 0.0 when none."""
    floor = RARITY_ROLL_FLOORS.get(str(rarity).lower()) if rarity else None
    return float(floor) if _num(floor) and 0.0 < floor < 1.0 else 0.0


def resolve_rarity_bucket(source_rarity_weight: float,
                          rarity_table: dict) -> str | None:
    """The source bucket a numeric drop-source weight lands in (§3.2).

    Buckets are threshold rows: the bucket with the HIGHEST ``min_weight``
    that ``source_rarity_weight`` reaches wins (guard_kill 0 < outpost 1 <
    stronghold 2 < fortress 3 < citadel 4 in the default table). Malformed
    rows are skipped; no reachable bucket → ``None`` (never raises, R1.5).

    A NON-FINITE numeric weight (NaN/inf) degrades to ``0.0`` — the lowest
    bucket, the same safe-floor treatment production drops get (review M1
    decision). Before this hardening a NaN weight resolved to the HIGHEST
    bucket (every ``threshold > nan`` comparison is False, so no row was
    ever skipped) — free citadel-grade loot on corrupt data. Non-numeric
    weights still yield ``None`` (no bucket at all).
    """
    try:
        if (isinstance(source_rarity_weight, float)
                and not math.isfinite(source_rarity_weight)):
            source_rarity_weight = 0.0  # NaN/inf → the lowest-bucket floor
        if not _num(source_rarity_weight) or not isinstance(rarity_table, dict):
            return None
        best_name, best_threshold = None, None
        # Sort for a deterministic winner independent of dict order.
        for name in sorted(rarity_table, key=str):
            row = rarity_table[name]
            if not isinstance(row, dict):
                continue
            threshold = row.get("min_weight")
            if not _num(threshold) or threshold > source_rarity_weight:
                continue
            if best_threshold is None or threshold > best_threshold:
                best_name, best_threshold = str(name), float(threshold)
        return best_name
    except Exception:
        return None


def _rarity_entries(weights: dict) -> list[tuple[str, float]]:
    """Valid ``(rarity, weight)`` pairs in a deterministic draw order.

    Known tiers come first in :data:`RARITY_ORDER` (low → high), unknown
    names after (sorted) — so the weighted choice never depends on dict
    insertion order. Non-positive / non-numeric weights are dropped.
    """
    rank = {name: i for i, name in enumerate(RARITY_ORDER)}
    entries = []
    for name, weight in weights.items():
        if _num(weight) and weight > 0:
            key = str(name).lower()
            entries.append((rank.get(key, len(RARITY_ORDER)), key, float(weight)))
    entries.sort(key=lambda e: (e[0], e[1]))
    return [(name, weight) for _, name, weight in entries]


def assign_rarity(source_rarity_weight: float, rarity_table: dict,
                  rng) -> str | None:
    """Weighted-choice rarity for a drop source (design §3.2, R3.2).

    Resolves the source bucket from the numeric weight, then draws one
    rarity from the bucket's relative ``weights`` under the injected
    ``rng`` (one ``rng.random()`` call — deterministic under a seed).
    An unusable table/bucket consumes NO randomness and returns ``None``
    (no rarity, no floor — the Phase-1 behavior). Never raises (R1.5).
    """
    try:
        bucket = resolve_rarity_bucket(source_rarity_weight, rarity_table)
        if bucket is None:
            return None
        weights = rarity_table[bucket].get("weights")
        if not isinstance(weights, dict):
            return None
        entries = _rarity_entries(weights)
        if not entries:
            return None
        total = sum(weight for _, weight in entries)
        r = rng.random() * total
        cumulative = 0.0
        for name, weight in entries:
            cumulative += weight
            if r < cumulative:
                return name
        return entries[-1][0]  # float-edge fallback (r == total)
    except Exception:
        return None


def assign_craft_rarity(craft_level, craft_rarity_table: dict,
                        rng) -> str | None:
    """Weighted-choice rarity for a CRAFTED roll, capped at Rare.

    The crafted counterpart of :func:`assign_rarity` (deviation-from-R6.1
    decision — module docstring): the crafting building's *craft_level*
    selects a row of *craft_rarity_table* (the row with the HIGHEST level
    key that *craft_level* reaches — so a level above the top key uses the
    top row, and a level below the lowest key yields no rarity), then one
    rarity is drawn from the row's relative weights under the injected
    ``rng`` (one ``rng.random()`` call — deterministic under a seed).

    Tiers above Rare are filtered out of the row defensively
    (:data:`CRAFT_RARITY_TIERS`): crafted gear can NEVER come out Epic or
    Legendary, whatever the data says. A non-numeric / sub-1 *craft_level*
    or an unusable table/row consumes NO randomness and returns ``None``
    (the original crafted no-rarity behavior). Never raises (R1.5).
    """
    try:
        # `not (>= 1)` (rather than `< 1`) so NaN — for which every
        # comparison is False — also degrades to "no rarity". (_num now
        # rejects NaN outright too, M1; the comparison form stays as
        # defense-in-depth.)
        if not _num(craft_level) or not (craft_level >= 1):
            return None
        if not isinstance(craft_rarity_table, dict):
            return None
        # Resolve the row: highest level key <= craft_level. YAML may hand
        # keys through as strings — coerce tolerantly, skip garbage.
        best_key, best_level = None, None
        for key in craft_rarity_table:
            try:
                level = int(key)
            except (TypeError, ValueError):
                continue
            if level < 1 or level > craft_level:
                continue
            if best_level is None or level > best_level:
                best_key, best_level = key, level
        if best_key is None:
            return None
        weights = craft_rarity_table[best_key]
        if not isinstance(weights, dict):
            return None
        # Hard cap at Rare: drop any higher tier before the draw.
        capped = {name: weight for name, weight in weights.items()
                  if str(name).lower() in CRAFT_RARITY_TIERS}
        entries = _rarity_entries(capped)
        if not entries:
            return None
        total = sum(weight for _, weight in entries)
        r = rng.random() * total
        cumulative = 0.0
        for name, weight in entries:
            cumulative += weight
            if r < cumulative:
                return name
        return entries[-1][0]  # float-edge fallback (r == total)
    except Exception:
        return None


def rarity_affix_budget(rarity) -> int:
    """The affix budget for *rarity* (design §3.1, R3.1); 0 when none."""
    budget = RARITY_AFFIX_BUDGETS.get(str(rarity).lower()) if rarity else None
    return int(budget) if _num(budget) and budget > 0 else 0


def _usable_affix_entries(pool) -> list[dict]:
    """The drawable entries of an affix pool, key-deduplicated.

    An entry is usable when it is a dict with a non-empty ``key``, EXACTLY
    ONE of a non-empty ``stat`` axis or a non-empty ``proc`` key (task 3.4
    — proc affixes like ``proc: poison`` are drawable once their combat
    hook exists), and a valid numeric ``[min, max]`` magnitude band. Later
    duplicates of a ``key`` are dropped (the no-dup contract, R3.4 —
    load-time validation rejects them, but the never-raise contract means
    we degrade rather than trust). Order is preserved so the draw is
    deterministic under an injected RNG.
    """
    usable: list[dict] = []
    seen: set[str] = set()
    if not isinstance(pool, (list, tuple)):
        return usable
    for entry in pool:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        stat, proc = entry.get("stat"), entry.get("proc")
        lo, hi = entry.get("min"), entry.get("max")
        if not key or not _num(lo) or not _num(hi) or lo > hi:
            continue
        if bool(stat) == bool(proc):
            continue  # exactly one of stat/proc (mirrors validate_affixes)
        key = str(key)
        if key in seen:
            continue
        seen.add(key)
        usable.append(entry)
    return usable


def _affix_draw_weight(entry) -> float:
    """The relative draw weight of one pool entry.

    ``weight`` in affixes.yaml is a RELATIVE DRAW WEIGHT: a 3.0 entry is drawn
    ~3x as often as a 1.0 entry. Missing, non-numeric, or non-positive weights
    default to 1.0 — degrade, never raise.
    """
    weight = entry.get("weight")
    return float(weight) if _num(weight) and weight > 0 else 1.0


def draw_affixes(pool, budget: int, *, skew: float = DEFAULT_LOOT_ROLL_SKEW,
                 rng) -> list[dict]:
    """Draw up to *budget* affixes WITHOUT replacement from *pool* (§3.3).

    Each draw is WEIGHT-PROPORTIONAL over the remaining candidates (review
    F2 — ``weight`` is a relative draw weight, not display metadata): one
    ``rng.random()`` call walks the cumulative weights, the picked entry is
    removed, and the remainder renormalizes implicitly on the next pass —
    so keys are never duplicated (R3.4) and the whole draw stays
    deterministic under an injected seed (R1.5). Each drawn affix then
    rolls its magnitude in its ``[min, max]`` band with the same skewed
    distribution as base stats (one more ``rng.random()`` per pick —
    design §3.3), and carries its displayed-score ``value`` (design §2.2)::

        q     = (magnitude - min) / (max - min)    # 1.0 on a degenerate band
        value = weight * q * AFFIX_VALUE_SCALE

    If *budget* exceeds the pool size, whatever is available is drawn.

    Args:
        pool: List of affix entry dicts ({key, name, stat-or-proc, min,
            max, weight}) — one pool of ``registry.affixes``.
        budget: The rarity's affix budget (:func:`rarity_affix_budget`).
        skew: The resolved U**skew exponent (same as the base-stat rolls).
        rng: Injected random source exposing ``random()``.

    Returns:
        A list of stored affix dicts ready for ``GameItem.db.affixes`` —
        ``{key, name, stat, magnitude, value}`` for stat affixes,
        ``{key, name, proc, magnitude, value}`` for proc affixes (task
        3.4; a proc entry carries NO ``stat`` key, so ``get_stat`` /
        ``get_stat_total`` never see it — its magnitude is consumed by
        the combat proc dispatch instead). Empty on an unusable pool or a
        non-positive budget. Never raises (R1.5).
    """
    try:
        candidates = _usable_affix_entries(pool)
        if not _num(budget) or budget <= 0 or not candidates:
            return []
        if not (_num(skew) and skew >= 1):
            skew = DEFAULT_LOOT_ROLL_SKEW
        count = min(int(budget), len(candidates))

        drawn: list[dict] = []
        for _ in range(count):
            # Weight-proportional pick without replacement (F2/R3.4): one
            # rng.random() scaled by the remaining total walks the
            # cumulative weights; the picked entry is removed so the next
            # pick renormalizes over what is left.
            total = sum(_affix_draw_weight(e) for e in candidates)
            r = rng.random() * total
            cumulative = 0.0
            idx = len(candidates) - 1  # float-edge fallback (r == total)
            for i, candidate in enumerate(candidates):
                cumulative += _affix_draw_weight(candidate)
                if r < cumulative:
                    idx = i
                    break
            entry = candidates.pop(idx)

            lo, hi = float(entry["min"]), float(entry["max"])
            magnitude = _roll_band(lo, hi, skew, rng)
            w = _affix_draw_weight(entry)
            q = (magnitude - lo) / (hi - lo) if hi > lo else 1.0
            stored = {
                "key": str(entry["key"]),
                "name": str(entry.get("name", entry["key"])),
                "magnitude": magnitude,
                "value": w * q * AFFIX_VALUE_SCALE,
            }
            if entry.get("stat"):
                stored["stat"] = str(entry["stat"])
            else:
                stored["proc"] = str(entry["proc"])
            drawn.append(stored)
        return drawn
    except Exception:
        # Never-raise (R1.5): a drop without affixes is safe; a crashed
        # spawn path never is.
        return []


def _effective_band(stat: str, loot_band: dict, craft_bands: dict,
                    crafted: bool) -> tuple[float, float] | None:
    """The ``(lo, hi)`` band this roll draws from, or None if unusable.

    Loot rolls use ``stats[stat]``. Crafted rolls use ``craft[stat]``
    intersected with the loot band (craft band ⊂ loot band, R6.1 / design
    Property 4); a stat with no craft band falls back to the loot band.
    Malformed bands (non-numeric, min > max) yield None → skip the stat.
    """
    lo, hi = loot_band.get("min"), loot_band.get("max")
    if not _num(lo) or not _num(hi) or lo > hi:
        return None
    lo, hi = float(lo), float(hi)

    if crafted:
        craft = craft_bands.get(stat)
        if isinstance(craft, dict):
            c_lo, c_hi = craft.get("min"), craft.get("max")
            if _num(c_lo) and _num(c_hi) and c_lo <= c_hi:
                # Intersect with the loot band so the craft band can never
                # escape it, even on odd data. Containment (craft ⊂ loot) IS
                # enforced at load (_validate_roll_spec, review M2), but the
                # never-trust contract keeps this defensive clamp anyway —
                # stored specs may predate validation or bypass the loader.
                c_lo = min(max(float(c_lo), lo), hi)
                c_hi = min(max(float(c_hi), lo), hi)
                if c_lo <= c_hi:
                    return c_lo, c_hi
            # Malformed craft band → fall back to the loot band below.
    return lo, hi


def compute_iqs(rolled: dict, roll_spec: dict) -> int | None:
    """Base Item Quality Score: weighted mean roll quality, 0–100 (§2.1).

    Per rolled stat ``s`` with loot band ``[min_s, max_s]`` and weight
    ``w_s`` from ``roll_spec.stats[s].weight``::

        q_s      = (rolled_s - min_s) / (max_s - min_s)      # 0..1
        IQS_base = round(100 * Σ(w_s * q_s) / Σ w_s)          # 0..100

    All-minimum rolls score 0, all-maximum rolls score 100, and the score
    is monotone in every rolled value (design Property 3). Affix values
    add on top of this base in Phase 2 (§2.2) — this is base-stat quality
    only.

    Degrades rather than raises (the module's R1.5 spirit):

    - Degenerate bands (``min == max``) carry no roll-quality signal and
      are excluded from the mean.
    - A missing/invalid ``weight`` defaults to 1.0; ``q_s`` is clamped to
      ``[0, 1]`` so out-of-band values can't push the score outside 0–100.
    - Malformed spec fragments and non-numeric rolled values are skipped.

    Returns:
        The 0–100 score, or ``None`` when nothing is scorable (no usable
        spec, no rolled stats, or only degenerate bands) — the caller
        leaves ``iqs`` neutral. Never raises.
    """
    try:
        # Mapping, NOT dict: on real Evennia, reading a stored dict back
        # through ``db``/``attributes`` yields a ``_SaverDict`` (a
        # MutableMapping that is not a dict subclass). A strict dict check
        # rejects every live item and IQS is silently never stamped — the
        # stubbed test suite can't see that (conftest hands back plain
        # dicts). Same reasoning everywhere this module reads stored state.
        if not isinstance(rolled, Mapping) or not isinstance(roll_spec, Mapping):
            return None
        stats = roll_spec.get("stats")
        if not isinstance(stats, Mapping):
            return None

        weighted_q = 0.0
        total_weight = 0.0
        for stat, value in rolled.items():
            if not _num(value):
                continue
            band = stats.get(stat)
            if not isinstance(band, Mapping):
                continue
            lo, hi = band.get("min"), band.get("max")
            if not _num(lo) or not _num(hi) or hi <= lo:
                continue  # malformed or degenerate band — no quality signal
            weight = band.get("weight")
            w = float(weight) if _num(weight) and weight > 0 else 1.0
            q = (float(value) - float(lo)) / (float(hi) - float(lo))
            q = min(max(q, 0.0), 1.0)  # clamp defensively (R1.5)
            weighted_q += w * q
            total_weight += w

        if total_weight <= 0:
            return None
        score = round(100.0 * weighted_q / total_weight)
        return int(min(max(score, 0), 100))
    except Exception:
        # Never-raise: a missing score is safe; a crashed spawn path isn't.
        return None


def affix_value_total(affixes) -> float:
    """Sum of the displayed-score ``value`` contributions of *affixes* (§2.2).

    Malformed entries and non-numeric values are skipped; unusable input
    totals ``0.0``. Never raises (R1.5 spirit).
    """
    total = 0.0
    try:
        # Sequence, NOT list/tuple: a stored affix list reads back as a
        # ``_SaverList`` (MutableSequence) on real Evennia — see the
        # Mapping note on compute_iqs. str/bytes are sequences too, so
        # exclude them explicitly.
        if not isinstance(affixes, Sequence) or isinstance(affixes, (str, bytes)):
            return 0.0
        for affix in affixes:
            value = affix.get("value") if hasattr(affix, "get") else None
            if _num(value):
                total += float(value)
    except Exception:
        return 0.0
    return total


def displayed_iqs(iqs_base, affixes) -> int | None:
    """The displayed item score: ``IQS_base + Σ affix.value`` (§2.2, R2.2).

    The one place the score math lives — :func:`roll_item` and
    :func:`recompute_iqs` both go through it, so the spawn stamp and every
    later mutation (reroll, insert) can never disagree (task 2.4). The
    score CAN exceed 100 (great base rolls + strong affixes read as
    top-tier, e.g. "Legendary 112") and is NEVER clamped here — the
    display layer caps what it renders at 999, but this number is the
    sort key players trade on.

    Returns:
        The rounded score, or ``None`` when *iqs_base* is ``None``/unusable
        (nothing scorable — the item stays neutral). Never raises.
    """
    if not _num(iqs_base):
        return None
    return int(round(float(iqs_base) + affix_value_total(affixes)))


def _read_instance_field(item, name: str):
    """Best-effort read of one per-instance field off a spawned item.

    The mirror of :func:`write_instance_field` (same duck-typing, same
    precedence): a live ``GameItem`` reads through its ``db`` proxy, an
    Evennia object without a ``db`` shim through the ``attributes``
    handler, and the dict-shaped default/test factory item by plain key.
    Returns ``None`` when the field is unset anywhere. Never raises.
    """
    try:
        db = getattr(item, "db", None)
        if db is not None:
            value = getattr(db, name, None)
            if value is not None:
                return value
        attrs = getattr(item, "attributes", None)
        if attrs is not None and hasattr(attrs, "get"):
            value = attrs.get(name)
            if value is not None:
                return value
        if isinstance(item, dict):
            return item.get(name)
    except Exception:
        pass
    return None


def _resolve_roll_spec(item, spec_source) -> dict | None:
    """The ``roll_spec`` governing *item*'s bands, or ``None``.

    *spec_source* may be the spec dict itself or anything carrying a
    ``roll_spec`` attribute (an ``ItemDef``); with neither, the item's own
    ``item_def`` (the ``GameItem`` registry lookup) is consulted. Never
    raises.
    """
    try:
        if isinstance(spec_source, dict):
            return spec_source
        spec = getattr(spec_source, "roll_spec", None)
        if isinstance(spec, dict):
            return spec
        item_def = getattr(item, "item_def", None)
        spec = getattr(item_def, "roll_spec", None)
        return spec if isinstance(spec, dict) else None
    except Exception:
        return None


def recompute_iqs(item, spec_source=None) -> int | None:
    """Recompute and re-stamp *item*'s displayed IQS (task 2.4, R2.4).

    THE single writer of the stamped ``iqs`` (design §2.3): the spawn
    stamping (:func:`roll_and_stamp`) routes through it, and the Phase-4
    Blacksmith calls it after ANY change to the item's rolls or affixes
    (reroll, insert applied) — so the number players sort/trade on is
    always ``IQS_base + Σ affix.value`` (§2.2) for the item's CURRENT
    state.

    Reads the item's ``rolled_stats`` + ``affixes`` (duck-typed — live
    ``GameItem``, attribute-bag stub, or dict factory item) and the
    governing ``roll_spec`` (pass it, pass the ``ItemDef``, or let the
    item's own ``item_def`` supply it), computes the displayed score, and
    writes ``iqs`` back onto the item.

    Args:
        item: The rolled item instance. ``None`` no-ops.
        spec_source: The ``roll_spec`` dict, an ``ItemDef`` carrying one,
            or ``None`` to use ``item.item_def.roll_spec``.

    Returns:
        The re-stamped score, or ``None`` when nothing is scorable (no
        rolled stats / no usable spec) — in that case the item is left
        untouched. Never raises (R1.5 spirit).
    """
    try:
        if item is None:
            return None
        rolled = _read_instance_field(item, "rolled_stats")
        affixes = _read_instance_field(item, "affixes")
        roll_spec = _resolve_roll_spec(item, spec_source)
        score = displayed_iqs(compute_iqs(rolled, roll_spec), affixes)
        if score is not None:
            write_instance_field(item, "iqs", int(score))
        return score
    except Exception:
        # Never-raise: a stale score is safe; a crashed bench/spawn path
        # never is.
        return None


def roll_item(item_def, *, source_rarity_weight: float = 0.0,
              crafted: bool = False, rng,
              default_skew: float = DEFAULT_LOOT_ROLL_SKEW,
              rarity_table: dict | None = None,
              affix_pools: dict | None = None,
              craft_floor: float = 0.0,
              craft_level: int = 0,
              craft_rarity_table: dict | None = None) -> RollResult | None:
    """Roll a fresh instance of ``item_def``; the core of the loot economy.

    Args:
        item_def: An ItemDef (anything carrying a ``roll_spec`` attribute).
        source_rarity_weight: Drop-source rarity weight (guard kill 0 <
            outpost < ... < citadel). Selects the rarity-table bucket the
            rarity is drawn from (task 2.2, design §3.2).
        crafted: True for the craft path — roll in the tighter per-stat
            ``craft`` band (R1.4, R6.1); crafted items never get affixes.
            With a usable ``craft_level`` a crafted rarity (≤ Rare) is
            drawn from the craft table (deviation-from-R6.1 decision,
            module docstring); without one, rarity assignment is skipped
            (the original behavior).
        craft_floor: Roll-floor U-clamp for CRAFTED rolls only (Master
            Gunsmithing research, R11.6/task 6.4) — the exact mechanism the
            rarity floors use, applied inside the craft band, so a raised
            floor lifts the low end of a crafted roll but can NEVER push it
            past the band (R6.1 stays intact). Ignored on loot rolls (whose
            floor is the rarity floor); values outside ``(0, 1)`` degrade
            to 0 (no floor), mirroring :func:`rarity_roll_floor`. When a
            crafted rarity also carries a floor (a Rare craft), the
            EFFECTIVE floor is ``max`` of the two — mirroring the reroll
            path's bench-floor/rarity-floor combination.
        craft_level: The CRAFTING BUILDING's level (1–5) — selects the
            ``craft_rarity_table`` row a crafted rarity is drawn from
            (deviation-from-R6.1 decision, module docstring). ``< 1`` (the
            default) keeps the original crafted no-rarity behavior.
            Ignored on loot rolls.
        craft_rarity_table: The balance ``craft_rarity_table`` (building
            level → rarity weights, capped at Rare). ``None`` →
            :data:`DEFAULT_CRAFT_RARITY_TABLE`; an empty/unusable table
            disables crafted rarity assignment.
        rng: Injected random source exposing ``random()`` (e.g.
            ``random.Random(seed)``). Same seed → identical result (R1.5).
        default_skew: The balance-level ``loot_roll_skew`` fallback used
            when the spec declares no per-item ``skew``.
        rarity_table: The balance ``rarity_table`` (source bucket →
            {min_weight, weights}). ``None`` → :data:`DEFAULT_RARITY_TABLE`;
            an empty/unusable table disables rarity assignment.
        affix_pools: The affix registry (pool name → entry list, i.e.
            ``registry.affixes``) the item's ``roll_spec.affix_pool`` pool
            is looked up in (task 2.3, design §3.3). ``None``/empty → no
            affixes are ever drawn — production drops and other no-affix
            paths simply don't pass pools (design §3.2).

    Returns:
        A :class:`RollResult`, or ``None`` when the def declares no usable
        ``roll_spec`` — the caller leaves the item fixed, exactly as today
        (R1.3). Never raises (R1.5).
    """
    try:
        roll_spec = getattr(item_def, "roll_spec", None)
        if not isinstance(roll_spec, dict):
            return None
        stats = roll_spec.get("stats")
        if not isinstance(stats, dict) or not stats:
            return None

        skew = _resolve_skew(roll_spec, default_skew)
        craft_bands = roll_spec.get("craft")
        if not isinstance(craft_bands, dict):
            craft_bands = {}

        # Rarity is assigned FIRST (design §2.2/§3.2): the tier then raises
        # the base-roll floor below. Crafted rolls draw from the building-
        # level-keyed craft table instead of the source-weighted loot table
        # (deviation-from-R6.1 decision, module docstring): capped at Rare,
        # 0% Rare at L1 rising to exactly 5% at L5. Without a usable
        # craft_level the crafted roll keeps its original no-rarity,
        # neutral/modest read.
        rarity = None
        floor = 0.0
        if not crafted:
            table = rarity_table if rarity_table is not None else DEFAULT_RARITY_TABLE
            rarity = assign_rarity(source_rarity_weight, table, rng)
            floor = rarity_roll_floor(rarity)
        else:
            if _num(craft_level) and craft_level >= 1:
                craft_table = (craft_rarity_table
                               if craft_rarity_table is not None
                               else DEFAULT_CRAFT_RARITY_TABLE)
                rarity = assign_craft_rarity(craft_level, craft_table, rng)
            # A Rare craft applies its normal 0.25 roll-floor benefit —
            # INSIDE the craft band, so it rolls genuinely better without
            # ever escaping the band (R6.1's band containment stays).
            floor = rarity_roll_floor(rarity)
            if _num(craft_floor) and 0.0 < craft_floor < 1.0:
                # Master Gunsmithing (R11.6) craft_iqs_floor: both floors
                # can apply — take max(floors), mirroring how the reroll
                # path combines its bench floor with the rarity floor.
                floor = max(floor, float(craft_floor))

        rolled: dict[str, float] = {}
        for stat, band in stats.items():
            if not isinstance(band, dict):
                continue  # malformed entry — skip, never raise (R1.5)
            eff = _effective_band(stat, band, craft_bands, crafted)
            if eff is None:
                continue
            lo, hi = eff
            rolled[str(stat)] = _roll_band(lo, hi, skew, rng, floor=floor)

        if not rolled:
            return None

        # Affix draw (task 2.3, design §3.3): budget by rarity, drawn
        # without replacement from the item's category pool. Crafted items
        # NEVER get affixes — R6.1's no-affix rule stays intact even now
        # that a crafted roll can carry a (≤ Rare) rarity: the `not
        # crafted` guard is load-bearing, since a Rare craft would
        # otherwise claim a budget of 2. Affixes are loot-only.
        affixes: list[dict] = []
        if not crafted and rarity is not None and isinstance(affix_pools, dict):
            budget = rarity_affix_budget(rarity)
            if budget > 0:
                pool_name = roll_spec.get("affix_pool")
                pool = affix_pools.get(pool_name) if pool_name else None
                if pool:
                    affixes = draw_affixes(pool, budget, skew=skew, rng=rng)

        # The displayed score (task 2.4, §2.2): base IQS + Σ affix.value —
        # the same math recompute_iqs re-stamps with on any later change.
        return RollResult(stat_modifiers=rolled,
                          affixes=affixes,
                          rarity=rarity,
                          iqs=displayed_iqs(compute_iqs(rolled, roll_spec),
                                            affixes))
    except Exception:
        # Never-raise contract (R1.5): an unrolled (fixed) item is always a
        # safe outcome; a crashed spawn path never is.
        return None


def reroll_base_stats(roll_spec, *, floor: float = 0.0, rng,
                      default_skew: float = DEFAULT_LOOT_ROLL_SKEW
                      ) -> dict[str, float] | None:
    """Re-roll an item's BASE stats in their loot bands (task 4.4, R4.5).

    The Blacksmith reroll backend: draws a fresh skewed roll for every stat
    in ``roll_spec.stats`` — always the LOOT band (a reroll is bench work on
    an existing instance, never the crafted floor) — with an explicit roll
    floor *floor* (the U clamp of :func:`_roll_band`). The caller supplies
    the EFFECTIVE floor: the Blacksmith-level floor combined with the item's
    rarity floor (``max`` of the two — the bench floor is the lever, the
    rarity floor stays the guarantee; design §4.4).

    Base stats ONLY: rarity, affixes, and applied inserts are not this
    function's business — the caller re-applies insert deltas on top and
    re-stamps IQS through :func:`recompute_iqs` (R2.4).

    Args:
        roll_spec: The item's ``roll_spec`` dict (must carry ``stats``).
        floor: The effective roll-floor U-clamp in ``[0, 1)``; invalid
            values degrade to 0 (no floor), mirroring :func:`_roll_band`.
        rng: Injected random source exposing ``random()`` (R1.5 —
            deterministic under a seed).
        default_skew: The balance ``loot_roll_skew`` fallback skew.

    Returns:
        The fresh rolled values (stat -> float, every value in its loot
        band), or ``None`` when the spec is unusable — the caller refuses
        the reroll rather than mutate. Never raises (R1.5).
    """
    try:
        if not isinstance(roll_spec, dict):
            return None
        stats = roll_spec.get("stats")
        if not isinstance(stats, dict) or not stats:
            return None
        skew = _resolve_skew(roll_spec, default_skew)
        rolled: dict[str, float] = {}
        for stat, band in stats.items():
            if not isinstance(band, dict):
                continue  # malformed entry — skip, never raise (R1.5)
            eff = _effective_band(stat, band, {}, False)
            if eff is None:
                continue
            lo, hi = eff
            rolled[str(stat)] = _roll_band(lo, hi, skew, rng, floor=floor)
        return rolled or None
    except Exception:
        # Never-raise contract (R1.5): "no reroll" is always a safe outcome
        # for the bench; a crashed command path never is.
        return None


def stats_at_quality(roll_spec, quality) -> dict[str, float] | None:
    """Deterministic per-stat values at one quality fraction (admin spawn).

    The ``@item spawn ... iqs=<N>`` backend: instead of a random skewed
    draw, every stat lands at the SAME fraction of its loot band::

        rolled = min + q * (max - min)      # q = clamp(quality, 0, 1)

    Because every non-degenerate band sits at the same ``q``,
    :func:`compute_iqs`'s weighted mean reads back exactly ``round(100*q)``
    — the operator's requested value IS the stamped base IQS (whatever the
    per-stat weights are). No randomness, no skew: this is a deliberate,
    reproducible admin stamp, not a loot roll.

    Mirrors :func:`reroll_base_stats`' degradation rules (R1.5 spirit):
    malformed bands are skipped, an unusable spec yields ``None`` (the
    caller leaves the item fixed), and *quality* is clamped into [0, 1].
    Never raises.

    Args:
        roll_spec: The item's ``roll_spec`` dict (must carry ``stats``).
        quality: The quality fraction, clamped into ``[0, 1]``.

    Returns:
        The per-stat values (stat -> float, every value in its loot band),
        or ``None`` when the spec is unusable.
    """
    try:
        if not isinstance(roll_spec, dict) or not _num(quality):
            return None
        stats = roll_spec.get("stats")
        if not isinstance(stats, dict) or not stats:
            return None
        q = min(max(float(quality), 0.0), 1.0)
        rolled: dict[str, float] = {}
        for stat, band in stats.items():
            if not isinstance(band, dict):
                continue  # malformed entry — skip, never raise (R1.5)
            eff = _effective_band(stat, band, {}, False)
            if eff is None:
                continue
            lo, hi = eff
            rolled[str(stat)] = lo + (hi - lo) * q
        return rolled or None
    except Exception:
        # Never-raise: an unrolled (fixed) item is a safe outcome; a
        # crashed admin-spawn path never is.
        return None


def write_instance_field(item, name: str, value: Any) -> bool:
    """Best-effort write of one per-instance field onto a spawned item.

    The spawn wiring's single write path (task 1.5): a live ``GameItem``
    takes the value on its ``db`` proxy (persisted Attribute), an Evennia
    object without a ``db`` shim falls back to the ``attributes`` handler,
    and the dict-shaped default/test item factory takes a plain key. Stays
    duck-typed so ``world/systems`` never imports ``typeclasses``.

    Returns:
        ``True`` if the value was written somewhere, ``False`` otherwise.
        Never raises (R1.5 spirit — a failed stamp degrades to a fixed
        item, never a crashed spawn path).
    """
    try:
        db = getattr(item, "db", None)
        if db is not None:
            setattr(db, name, value)
            return True
        attrs = getattr(item, "attributes", None)
        if attrs is not None and hasattr(attrs, "add"):
            attrs.add(name, value)
            return True
        if isinstance(item, dict):
            item[name] = value
            return True
    except Exception:
        pass
    return False


def roll_and_stamp(item, item_def, *, source_rarity_weight: float = 0.0,
                   crafted: bool = False, rng=None,
                   default_skew: float = DEFAULT_LOOT_ROLL_SKEW,
                   rarity_table: dict | None = None,
                   affix_pools: dict | None = None,
                   craft_floor: float = 0.0,
                   craft_level: int = 0,
                   craft_rarity_table: dict | None = None
                   ) -> RollResult | None:
    """Roll ``item_def`` and stamp the result onto the spawned *item* (task 1.5).

    The one call every ROLLING spawn path makes after its ``GameItem`` exists
    (design §1.2): HQ-destroy gear drops (``base_elimination._spawn_gear_item``),
    passive/agent production drops, and the craft path. The PvP death drop
    never calls this — it carries the dropped instance's state instead (R1.6).

    Writes ``rolled_stats`` (the per-instance rolled values ``get_stat``
    prefers), — when a rarity was assigned (task 2.2) — ``rarity``, and
    — when affixes were drawn (task 2.3) — ``affixes`` onto the item,
    then stamps ``iqs`` through :func:`recompute_iqs` (task 2.4): the
    displayed score ``IQS_base + Σ affix.value``, single-writer (R2.4).
    Only meaningful state is written (R12): unrolled items and crafted
    (no-rarity) items carry no ``rarity``/``affixes`` attributes at all.

    Args:
        item: The freshly-spawned item (``GameItem``, attribute-bag stub, or
            the dict-shaped test factory item). ``None`` no-ops.
        item_def: The def that spawned it; no ``roll_spec`` → no-op (R1.3).
        source_rarity_weight: Drop-source rarity weight — selects the
            rarity-table bucket (design §3.2).
        crafted: True on the craft path — the tighter craft band (R1.4/R6.1);
            crafted rarity (≤ Rare) is drawn from the craft table only when
            a ``craft_level`` is supplied.
        rng: Injected random source (``random()``); defaults to the module
            :mod:`random` for live spawn paths. Tests inject a seeded RNG.
        default_skew: The balance ``loot_roll_skew`` fallback skew.
        rarity_table: The balance ``rarity_table``; ``None`` → the module
            :data:`DEFAULT_RARITY_TABLE`.
        affix_pools: The affix registry (``registry.affixes``) — the pools
            the item's ``roll_spec.affix_pool`` draws from (task 2.3).
            ``None``/empty → no affixes (the production-drop / craft
            treatment, design §3.2).
        craft_floor: Crafted-roll floor U-clamp (Master Gunsmithing
            research, R11.6/task 6.4) — see :func:`roll_item`. Only
            meaningful with ``crafted=True``.
        craft_level: The crafting building's level — selects the crafted-
            rarity row (deviation-from-R6.1 decision; see :func:`roll_item`
            and the module docstring). Only meaningful with
            ``crafted=True``; ``< 1`` keeps the no-rarity behavior.
        craft_rarity_table: The balance ``craft_rarity_table``; ``None`` →
            the module :data:`DEFAULT_CRAFT_RARITY_TABLE`.

    Returns:
        The :class:`RollResult` that was stamped, or ``None`` when nothing
        was rolled (unrolled def / malformed spec / ``item`` is None).
        Never raises.
    """
    if item is None:
        return None
    try:
        result = roll_item(
            item_def,
            source_rarity_weight=source_rarity_weight,
            crafted=crafted,
            rng=rng if rng is not None else _random_module,
            default_skew=default_skew,
            rarity_table=rarity_table,
            affix_pools=affix_pools,
            craft_floor=craft_floor,
            craft_level=craft_level,
            craft_rarity_table=craft_rarity_table,
        )
        if result is None or not result.stat_modifiers:
            return None  # fixed item, exactly as today (R1.3)
        write_instance_field(item, "rolled_stats", dict(result.stat_modifiers))
        if result.rarity is not None:
            write_instance_field(item, "rarity", str(result.rarity))
        if result.affixes:
            write_instance_field(item, "affixes",
                                 [dict(affix) for affix in result.affixes])
        # Single-writer discipline (task 2.4, R2.4): the iqs stamp goes
        # through recompute_iqs — the same writer the Blacksmith's reroll/
        # insert paths use — reading back the state just written, so the
        # spawn stamp and every later mutation can never disagree.
        recompute_iqs(item, getattr(item_def, "roll_spec", None))
        return result
    except Exception:
        # Never-raise contract (R1.5): an unrolled (fixed) item is always a
        # safe outcome; a crashed spawn path never is.
        return None
