"""
Loot roller — per-instance stat rolling.

A pure, RNG-injected service: given an ItemDef with a ``roll_spec``, produce
per-instance rolled ``stat_modifiers`` stamped onto a GameItem. All randomness
arrives through ``rng``, so rolls are deterministic under a seed.

Distribution::

    rolled = min + (max - min) * (U ** skew)    # U ~ uniform(0,1), skew >= 1

``skew=2`` puts the median at ~25% of the band — near-max rolls are scarce.

Key concepts:

- **IQS** — weighted mean of per-stat roll quality (0–100 base). Displayed
  score is ``IQS_base + Σ affix.value``, unclamped (can exceed 100).
- **Rarity** — assigned by weighted choice from source-bucket tables. Higher
  tiers clamp ``U`` into ``[floor, 1]`` before the skew, guaranteeing better
  base rolls without removing variance.
- **Affixes** — drawn without replacement from category pools; budget set by
  rarity tier. Crafted items never get affixes.

Contract: ``roll_item`` never raises; malformed specs degrade to ``None``.
Every rolled value is clamped to its ``[min, max]`` band.
"""

from __future__ import annotations

import math
import random as _random_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Default U**skew exponent. The spawn wiring passes the live balance value;
#: this is the pure-module fallback.
DEFAULT_LOOT_ROLL_SKEW = 2.0

#: Rarity tiers, low → high. Stored lowercase on instances.
RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")

#: Roll-floor U-clamp per rarity: before the skew, ``U`` is clamped into
#: ``[floor, 1]`` so higher rarities guarantee better base rolls.
RARITY_ROLL_FLOORS = {
    "common": 0.0,
    "uncommon": 0.0,
    "rare": 0.25,
    "epic": 0.50,
    "legendary": 0.75,
}

#: Number of affixes a loot roll of each tier draws from its category pool.
RARITY_AFFIX_BUDGETS = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
    "epic": 3,
    "legendary": 4,
}

#: Scale applied to an affix's normalized magnitude × pool weight to get its
#: displayed-score contribution. A strong roll (q ≈ 0.7) at weight 1.0
#: contributes ~7 score points.
AFFIX_VALUE_SCALE = 10.0

#: Fallback rarity table (mirrored by ``BalanceConfig.rarity_table``).
#: Each source bucket has a ``min_weight`` threshold and relative rarity
#: ``weights``. The highest-threshold bucket reached by the source wins.
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


#: Rarity tiers a CRAFTED roll may reach — capped at Rare. Epic/Legendary
#: are loot-only; higher tiers in craft data are filtered out defensively.
CRAFT_RARITY_TIERS = frozenset({"common", "uncommon", "rare"})

#: Fallback craft rarity table (mirrored by ``BalanceConfig.craft_rarity_table``).
#: Keyed by crafting building level (1–5). L1 has no Rare chance; Rare rises
#: to exactly 5% at L5. Levels above the highest key use the top row.
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
    """True for real, FINITE numbers (bool excluded).

    NaN/inf are rejected because every NaN comparison is False, so a NaN
    slipping past a ``>``/``<`` guard silently takes the wrong branch.
    Non-finite values degrade exactly like non-numbers.
    """
    return (isinstance(val, (int, float)) and not isinstance(val, bool)
            and math.isfinite(val))


def _resolve_skew(roll_spec: dict, default_skew: float) -> float:
    """The U**skew exponent: per-item ``roll_spec.skew``, else the default.

    Invalid values (non-numeric, < 1) fall back to the default — degrade
    rather than raise.
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

    ``floor`` is the rarity roll-floor U-clamp: ``U`` is clamped into
    ``[floor, 1]`` BEFORE the skew, so the roll can never land below the
    ``floor**skew`` fraction of the band.
    """
    u = rng.random()
    if _num(floor) and 0.0 < floor < 1.0:
        u = floor + (1.0 - floor) * u  # clamp U into [floor, 1]
    rolled = lo + (hi - lo) * (u ** skew)
    # Clamped defensively — float edge cases must never escape the band.
    return min(max(rolled, lo), hi)


def rarity_roll_floor(rarity) -> float:
    """The roll-floor U-clamp for *rarity*; 0.0 when none."""
    floor = RARITY_ROLL_FLOORS.get(str(rarity).lower()) if rarity else None
    return float(floor) if _num(floor) and 0.0 < floor < 1.0 else 0.0


def resolve_rarity_bucket(source_rarity_weight: float,
                          rarity_table: dict) -> str | None:
    """The source bucket a numeric drop-source weight lands in.

    Buckets are threshold rows: the bucket with the HIGHEST ``min_weight``
    that ``source_rarity_weight`` reaches wins. Malformed rows are skipped;
    no reachable bucket → ``None``. Never raises.

    A non-finite numeric weight (NaN/inf) degrades to ``0.0`` — the lowest
    bucket. Before this hardening a NaN weight resolved to the HIGHEST
    bucket (every ``threshold > nan`` comparison is False). Non-numeric
    weights yield ``None`` (no bucket at all).
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
    """Weighted-choice rarity for a drop source.

    Resolves the source bucket from the numeric weight, then draws one
    rarity from the bucket's relative ``weights`` under the injected
    ``rng`` (one ``rng.random()`` call — deterministic under a seed).
    An unusable table/bucket consumes NO randomness and returns ``None``.
    Never raises.
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

    The crafting building's *craft_level* selects a row of
    *craft_rarity_table* (the row with the HIGHEST level key that
    *craft_level* reaches), then one rarity is drawn from the row's
    relative weights (one ``rng.random()`` call — deterministic under a
    seed).

    Tiers above Rare are filtered out defensively: crafted gear can NEVER
    come out Epic or Legendary. A non-numeric / sub-1 *craft_level* or an
    unusable table/row consumes NO randomness and returns ``None``.
    Never raises.
    """
    try:
        # `not (>= 1)` (rather than `< 1`) so NaN — for which every
        # comparison is False — also degrades to "no rarity".
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
    """The affix budget for *rarity*; 0 when none."""
    budget = RARITY_AFFIX_BUDGETS.get(str(rarity).lower()) if rarity else None
    return int(budget) if _num(budget) and budget > 0 else 0


def _usable_affix_entries(pool) -> list[dict]:
    """The drawable entries of an affix pool, key-deduplicated.

    An entry is usable when it is a dict with a non-empty ``key``, EXACTLY
    ONE of a non-empty ``stat`` axis or a non-empty ``proc`` key (proc
    affixes like ``proc: poison`` are drawable once their combat hook
    exists), and a valid numeric ``[min, max]`` magnitude band. Later
    duplicates of a ``key`` are dropped (the no-dup contract — degrade
    rather than raise). Order is preserved so the draw is deterministic
    under an injected RNG.
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
    """Draw up to *budget* affixes WITHOUT replacement from *pool*.

    Each draw is WEIGHT-PROPORTIONAL over the remaining candidates: one
    ``rng.random()`` call walks the cumulative weights, the picked entry is
    removed, and the remainder renormalizes implicitly on the next pass —
    so keys are never duplicated and the whole draw stays deterministic
    under an injected seed. Each drawn affix rolls its magnitude in its
    ``[min, max]`` band with the same skewed distribution as base stats,
    and carries its displayed-score ``value``::

        q     = (magnitude - min) / (max - min)    # 1.0 on a degenerate band
        value = weight * q * AFFIX_VALUE_SCALE

    If *budget* exceeds the pool size, whatever is available is drawn.

    Args:
        pool: List of affix entry dicts ({key, name, stat-or-proc, min,
            max, weight}) — one pool from the affix registry.
        budget: The rarity's affix budget (:func:`rarity_affix_budget`).
        skew: The resolved U**skew exponent (same as the base-stat rolls).
        rng: Injected random source exposing ``random()``.

    Returns:
        A list of stored affix dicts ready for ``GameItem.db.affixes`` —
        ``{key, name, stat, magnitude, value}`` for stat affixes,
        ``{key, name, proc, magnitude, value}`` for proc affixes (a proc
        entry carries NO ``stat`` key — its magnitude is consumed by the
        combat proc dispatch instead). Empty on an unusable pool or a
        non-positive budget. Never raises.
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
            # Weight-proportional pick without replacement: one rng.random()
            # scaled by the remaining total walks the cumulative weights;
            # the picked entry is removed so the next pick renormalizes.
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
        # Never-raise: a drop without affixes is safe.
        return []


def _effective_band(stat: str, loot_band: dict, craft_bands: dict,
                    crafted: bool) -> tuple[float, float] | None:
    """The ``(lo, hi)`` band this roll draws from, or None if unusable.

    Loot rolls use ``stats[stat]``. Crafted rolls use ``craft[stat]``
    intersected with the loot band (craft band ⊂ loot band); a stat with
    no craft band falls back to the loot band. Malformed bands yield
    None → skip the stat.
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
                # escape it, even on odd data.
                c_lo = min(max(float(c_lo), lo), hi)
                c_hi = min(max(float(c_hi), lo), hi)
                if c_lo <= c_hi:
                    return c_lo, c_hi
            # Malformed craft band → fall back to the loot band below.
    return lo, hi


def compute_iqs(rolled: dict, roll_spec: dict) -> int | None:
    """Base Item Quality Score: weighted mean roll quality, 0–100.

    Per rolled stat ``s`` with loot band ``[min_s, max_s]`` and weight
    ``w_s`` from ``roll_spec.stats[s].weight``::

        q_s      = (rolled_s - min_s) / (max_s - min_s)      # 0..1
        IQS_base = round(100 * Σ(w_s * q_s) / Σ w_s)          # 0..100

    All-minimum rolls score 0, all-maximum rolls score 100. Affix values
    add on top of this base via :func:`displayed_iqs`.

    Degrades rather than raises:

    - Degenerate bands (``min == max``) are excluded from the mean.
    - A missing/invalid ``weight`` defaults to 1.0; ``q_s`` is clamped to
      ``[0, 1]`` so out-of-band values can't push the score outside 0–100.
    - Malformed spec fragments and non-numeric rolled values are skipped.

    Returns:
        The 0–100 score, or ``None`` when nothing is scorable. Never raises.
    """
    try:
        # Mapping, NOT dict: on real Evennia, stored dicts read back as
        # ``_SaverDict`` (a MutableMapping that isn't a dict subclass).
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
            q = min(max(q, 0.0), 1.0)  # clamp defensively
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
    """Sum of the displayed-score ``value`` contributions of *affixes*.

    Malformed entries and non-numeric values are skipped; unusable input
    totals ``0.0``. Never raises.
    """
    total = 0.0
    try:
        # Sequence, NOT list/tuple: stored affix lists read back as
        # ``_SaverList`` (MutableSequence) on real Evennia.
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
    """The displayed item score: ``IQS_base + Σ affix.value``.

    The one place the score math lives — :func:`roll_item` and
    :func:`recompute_iqs` both route through it, so the spawn stamp and
    every later mutation can never disagree. The score CAN exceed 100 and
    is NEVER clamped here — the display layer caps rendering at 999.

    Returns:
        The rounded score, or ``None`` when *iqs_base* is unusable.
        Never raises.
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
    """Recompute and re-stamp *item*'s displayed IQS.

    THE single writer of the stamped ``iqs``: the spawn stamping routes
    through it, and the Blacksmith calls it after ANY change to the item's
    rolls or affixes — so the number is always ``IQS_base + Σ affix.value``
    for the item's CURRENT state.

    Args:
        item: The rolled item instance. ``None`` no-ops.
        spec_source: The ``roll_spec`` dict, an ``ItemDef`` carrying one,
            or ``None`` to use ``item.item_def.roll_spec``.

    Returns:
        The re-stamped score, or ``None`` when nothing is scorable — item
        is left untouched. Never raises.
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
        # Never-raise: a stale score is safe.
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
            outpost < ... < citadel). Selects the rarity-table bucket.
        crafted: True for the craft path — roll in the tighter per-stat
            ``craft`` band; crafted items never get affixes. With a usable
            ``craft_level`` a crafted rarity (≤ Rare) is drawn from the
            craft table; without one, rarity assignment is skipped.
        craft_floor: Roll-floor U-clamp for CRAFTED rolls only (Master
            Gunsmithing research) — applied inside the craft band. Ignored
            on loot rolls; values outside ``(0, 1)`` degrade to 0. When a
            crafted rarity also carries a floor, the effective floor is
            ``max`` of the two.
        craft_level: The crafting building's level (1–5) — selects the
            ``craft_rarity_table`` row. ``< 1`` keeps no-rarity behavior.
            Ignored on loot rolls.
        craft_rarity_table: The balance ``craft_rarity_table`` (building
            level → rarity weights, capped at Rare). ``None`` →
            :data:`DEFAULT_CRAFT_RARITY_TABLE`.
        rng: Injected random source exposing ``random()``. Same seed →
            identical result.
        default_skew: The balance-level ``loot_roll_skew`` fallback used
            when the spec declares no per-item ``skew``.
        rarity_table: The balance ``rarity_table`` (source bucket →
            {min_weight, weights}). ``None`` → :data:`DEFAULT_RARITY_TABLE`.
        affix_pools: The affix registry (pool name → entry list) the item's
            ``roll_spec.affix_pool`` pool is looked up in. ``None``/empty →
            no affixes are drawn.

    Returns:
        A :class:`RollResult`, or ``None`` when the def declares no usable
        ``roll_spec`` — the caller leaves the item fixed. Never raises.
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

        # Rarity is assigned FIRST: the tier then raises the base-roll
        # floor. Crafted rolls draw from the building-level-keyed craft
        # table instead of the source-weighted loot table.
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
            # A Rare craft applies its normal roll-floor benefit INSIDE the
            # craft band.
            floor = rarity_roll_floor(rarity)
            if _num(craft_floor) and 0.0 < craft_floor < 1.0:
                # Both floors can apply — take max(floors).
                floor = max(floor, float(craft_floor))

        rolled: dict[str, float] = {}
        for stat, band in stats.items():
            if not isinstance(band, dict):
                continue  # malformed entry — skip, never raise
            eff = _effective_band(stat, band, craft_bands, crafted)
            if eff is None:
                continue
            lo, hi = eff
            rolled[str(stat)] = _roll_band(lo, hi, skew, rng, floor=floor)

        if not rolled:
            return None

        # Affix draw: budget by rarity, drawn without replacement from the
        # item's category pool. Crafted items NEVER get affixes — the
        # `not crafted` guard is load-bearing, since a Rare craft would
        # otherwise claim a budget of 2.
        affixes: list[dict] = []
        if not crafted and rarity is not None and isinstance(affix_pools, dict):
            budget = rarity_affix_budget(rarity)
            if budget > 0:
                pool_name = roll_spec.get("affix_pool")
                pool = affix_pools.get(pool_name) if pool_name else None
                if pool:
                    affixes = draw_affixes(pool, budget, skew=skew, rng=rng)

        # Displayed score: base IQS + Σ affix.value.
        return RollResult(stat_modifiers=rolled,
                          affixes=affixes,
                          rarity=rarity,
                          iqs=displayed_iqs(compute_iqs(rolled, roll_spec),
                                            affixes))
    except Exception:
        # Never-raise: an unrolled (fixed) item is always a safe outcome.
        return None


def reroll_base_stats(roll_spec, *, floor: float = 0.0, rng,
                      default_skew: float = DEFAULT_LOOT_ROLL_SKEW
                      ) -> dict[str, float] | None:
    """Re-roll an item's BASE stats in their loot bands.

    The Blacksmith reroll backend: draws a fresh skewed roll for every stat
    in ``roll_spec.stats`` — always the LOOT band — with an explicit roll
    floor (the U clamp of :func:`_roll_band`). The caller supplies the
    EFFECTIVE floor: the Blacksmith-level floor combined with the item's
    rarity floor (``max`` of the two).

    Base stats ONLY: rarity, affixes, and applied inserts are not this
    function's business — the caller re-applies insert deltas on top and
    re-stamps IQS through :func:`recompute_iqs`.

    Args:
        roll_spec: The item's ``roll_spec`` dict (must carry ``stats``).
        floor: The effective roll-floor U-clamp in ``[0, 1)``; invalid
            values degrade to 0 (no floor).
        rng: Injected random source exposing ``random()``.
        default_skew: The balance ``loot_roll_skew`` fallback skew.

    Returns:
        The fresh rolled values (stat -> float, every value in its loot
        band), or ``None`` when the spec is unusable. Never raises.
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
                continue  # malformed entry — skip, never raise
            eff = _effective_band(stat, band, {}, False)
            if eff is None:
                continue
            lo, hi = eff
            rolled[str(stat)] = _roll_band(lo, hi, skew, rng, floor=floor)
        return rolled or None
    except Exception:
        # Never-raise: "no reroll" is always a safe outcome for the bench.
        return None


def stats_at_quality(roll_spec, quality) -> dict[str, float] | None:
    """Deterministic per-stat values at one quality fraction (admin spawn).

    The ``@item spawn ... iqs=<N>`` backend: every stat lands at the SAME
    fraction of its loot band::

        rolled = min + q * (max - min)      # q = clamp(quality, 0, 1)

    Because every non-degenerate band sits at the same ``q``,
    :func:`compute_iqs`'s weighted mean reads back exactly ``round(100*q)``
    — the operator's requested value IS the stamped base IQS. No
    randomness, no skew: a deliberate, reproducible admin stamp.

    Malformed bands are skipped; an unusable spec yields ``None``;
    *quality* is clamped to [0, 1]. Never raises.

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
                continue  # malformed entry — skip, never raise
            eff = _effective_band(stat, band, {}, False)
            if eff is None:
                continue
            lo, hi = eff
            rolled[str(stat)] = lo + (hi - lo) * q
        return rolled or None
    except Exception:
        # Never-raise: an unrolled (fixed) item is a safe outcome.
        return None


def write_instance_field(item, name: str, value: Any) -> bool:
    """Best-effort write of one per-instance field onto a spawned item.

    A live ``GameItem`` takes the value on its ``db`` proxy, an Evennia
    object without a ``db`` shim falls back to the ``attributes`` handler,
    and a dict-shaped test factory item takes a plain key. Stays duck-typed
    so ``world/systems`` never imports ``typeclasses``.

    Returns:
        ``True`` if the value was written somewhere, ``False`` otherwise.
        Never raises.
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
    """Roll ``item_def`` and stamp the result onto the spawned *item*.

    The one call every ROLLING spawn path makes after its ``GameItem``
    exists: HQ-destroy gear drops, passive/agent production drops, and the
    craft path.

    Writes ``rolled_stats``, and when applicable ``rarity`` and ``affixes``
    onto the item, then stamps ``iqs`` through :func:`recompute_iqs`.
    Only meaningful state is written (unrolled/no-rarity items carry no
    ``rarity``/``affixes`` attributes).

    Args:
        item: The freshly-spawned item. ``None`` no-ops.
        item_def: The def that spawned it; no ``roll_spec`` → no-op.
        source_rarity_weight: Drop-source rarity weight — selects the
            rarity-table bucket.
        crafted: True on the craft path — tighter craft band; crafted
            rarity (≤ Rare) drawn from the craft table only when a
            ``craft_level`` is supplied.
        rng: Injected random source; defaults to the module :mod:`random`
            for live spawn paths. Tests inject a seeded RNG.
        default_skew: The balance ``loot_roll_skew`` fallback skew.
        rarity_table: The balance ``rarity_table``; ``None`` → the module
            :data:`DEFAULT_RARITY_TABLE`.
        affix_pools: The affix registry — the pools the item's
            ``roll_spec.affix_pool`` draws from. ``None``/empty → no
            affixes.
        craft_floor: Crafted-roll floor U-clamp (Master Gunsmithing
            research) — see :func:`roll_item`. Only meaningful with
            ``crafted=True``.
        craft_level: The crafting building's level — selects the crafted-
            rarity row. Only meaningful with ``crafted=True``; ``< 1``
            keeps the no-rarity behavior.
        craft_rarity_table: The balance ``craft_rarity_table``; ``None`` →
            the module :data:`DEFAULT_CRAFT_RARITY_TABLE`.

    Returns:
        The :class:`RollResult` that was stamped, or ``None`` when nothing
        was rolled. Never raises.
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
            return None  # fixed item — no roll_spec
        write_instance_field(item, "rolled_stats", dict(result.stat_modifiers))
        if result.rarity is not None:
            write_instance_field(item, "rarity", str(result.rarity))
        if result.affixes:
            write_instance_field(item, "affixes",
                                 [dict(affix) for affix in result.affixes])
        # Single-writer discipline: the iqs stamp goes through
        # recompute_iqs — the same writer the Blacksmith's reroll/insert
        # paths use.
        recompute_iqs(item, getattr(item_def, "roll_spec", None))
        return result
    except Exception:
        # Never-raise: an unrolled (fixed) item is always a safe outcome.
        return None
