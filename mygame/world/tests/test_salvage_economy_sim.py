"""
Salvage-economy balance SIMULATION (item-loot-economy task 5.5, design §5
balance guard, R7.5).

The salvage economy must not inflate: rolled loot floods in (drops →
salvage), Salvage drains out (Blacksmith rerolls chasing god-rolls). This
module simulates a plausible raid session with the REAL loot roller + the
REAL shipped balance numbers and asserts the no-inflation guard:

1. A raid session's Salvage income funds a BOUNDED handful of rerolls —
   at least one (the bench is usable), nowhere near hundreds (no
   inflation).
2. Materially improving an item (rerolling until base IQS >= 70 under the
   skew-2 distribution) burns at least a session's worth of mediocre
   drops.
3. A god-roll chase (base IQS >= 85) costs MULTIPLE sessions' income —
   the design intent: "reroll chase burns many mediocre drops".

Session model (documented reasoning)
------------------------------------
A "raid session" is one solid evening of PvE raiding. A planet spawns
4 outposts / 3 strongholds / 2 fortresses / 1 citadel (outposts.yaml
spawn_count); the modeled session clears a slice of that ladder:

    2 outposts + 1 stronghold + 1 fortress, killing every garrison guard
    on the way (2x1 + 1 + 2 = 5 guard kills).

Drop mechanics mirror the shipped wiring exactly:

- HQ destroy: ``gear_rolls`` independent rounds per template; each round
  rolls the gear pool at ``gear_drop_chance`` AND the rare pool at
  ``rare_gear_chance`` (base_elimination._roll_gear_drops).
- Guard kills: ``guard_gear_drop_chance`` (balance, 0.05) per kill from
  the template's gear pool at source weight 0.0 (the guard_kill bucket —
  base_elimination._roll_guard_gear_drop).
- Every drop is rolled through the real ``roll_item`` with the real
  ``balance.rarity_table`` / ``loot_roll_skew`` / affix registry. The
  HQ-destroy source weights are the rarity table's own ``min_weight``
  thresholds (outpost 1.0 < stronghold 2.0 < fortress 3.0 — design §3.2).

Salvage policy: the raider keeps the chase items — anything Epic or
Legendary, or with a displayed IQS >= 70 — and salvages everything else
(the "mediocre drops") at a MID-LEVEL Blacksmith (L3, yield x1.25).
Unrolled drops (no roll_spec, e.g. the scope) salvage at the base floor
(iqs 0), exactly like EquipmentSystem.salvage.

Reroll-chase model: rerolling the design's calibration-anchor weapon
(assault_rifle, §1.1) at the same L3 bench — reroll floor
``REROLL_FLOOR_PER_LEVEL x (3 - 1) = 0.2`` (equipment_system) — until the
recomputed BASE IQS reaches the target. Base IQS is what a reroll
re-stamps (affixes are untouched, R4.5), so it is the honest chase metric.

Bounds (defensible, documented)
-------------------------------
Measured at the shipped numbers (seeded run): income ≈ 40 Salvage per
session — almost exactly ONE reroll at the shipped 40-Salvage cost.
That is the design's neutrality target hit on the nose: aggregate
Salvage-in ≈ Salvage-out per session (R7.5, design §5).

- Session funds ``[0.5, 20]`` rerolls at the shipped cost (observed
  ~1.0): the lower bound guards the "can't fund even one" failure —
  Salvage banks across sessions, so >= 0.5 means at worst two sessions
  fund a reroll (the bench stays reachable); <= 20 keeps a session's
  flood far from the "hundreds of rerolls" inflation failure. The
  Salvage Protocols researched cost (x0.75 -> 30, still inside design
  §9's 30-60 band) must fund >= 1 reroll per session (observed ~1.3) —
  the economy tech visibly matters.
- A material improvement (IQS >= 70) costs >= 1 session's income and
  lands within ~60 rerolls (achievable for a determined player, but not
  free — observed ~7 rerolls ≈ 7 sessions' income).
- The god-roll chase (IQS >= 85) costs >= 3 sessions' income (observed
  ~33 sessions) and stays attainable (<= 200 sessions) so the chase is a
  long-term drain, not an impossibility.

At the current shipped numbers (base_salvage 5, salvage_per_iqs 0.5,
salvage_level_bonus 0.125, reroll_salvage_cost 40, skew 2.0) every guard
holds — NO balance.yaml tuning was needed: the measured session income
(~1.0 reroll) IS the "≈ neutral" design intent, and the chase costs sit
comfortably in "burns many mediocre drops" territory.

All randomness is seeded (deterministic run-to-run); the whole module
runs in a few seconds.

**Validates: Requirements 7.5**
"""

import os
import random
import sys
import types

import pytest


# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules (equipment_system import)
# -------------------------------------------------------------- #

def _ensure_evennia_stubs():
    """Insert lightweight stubs for Evennia modules into sys.modules."""
    if "evennia" in sys.modules:
        mod = sys.modules["evennia"]
        if hasattr(mod, "__file__") and mod.__file__:
            return
    stubs = {}

    def _mod(name, attrs=None):
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        stubs[name] = m
        return m

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
        "DefaultCharacter": type("DefaultCharacter", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.systems.equipment_system import (  # noqa: E402
    REROLL_FLOOR_PER_LEVEL,
    SALVAGE_COST_MULT_FLOOR,
)
from mygame.world.systems.loot_roller import (  # noqa: E402
    compute_iqs,
    reroll_base_stats,
    roll_item,
)

# ------------------------------------------------------------------ #
#  Locate the real data directory (mygame/data)
# ------------------------------------------------------------------ #
_REAL_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data")
)

# ------------------------------------------------------------------ #
#  Simulation constants (see module docstring for the reasoning)
# ------------------------------------------------------------------ #
SEED = 20260724

#: One raid session: the PvE slice a solid evening clears (of a planet's
#: 4/3/2/1 outpost/stronghold/fortress/citadel spawns).
SESSION_CLEARS = ("outpost", "outpost", "stronghold", "fortress")

#: Mid-level Blacksmith (task 5.5: "salvage ... at a mid-level Blacksmith").
BLACKSMITH_LEVEL = 3

#: Keep threshold: drops at/above this displayed IQS — or Epic/Legendary —
#: are the chase items the raider keeps; everything below is "mediocre"
#: and feeds the salvage bench.
KEEP_IQS = 70
KEEP_RARITIES = frozenset({"epic", "legendary"})

#: Chase targets: "materially improve" (task 5.5 example: IQS >= 70) and
#: the god-roll (IQS >= 85).
GOOD_IQS = 70
GOD_IQS = 85

#: Monte-Carlo sizes — deterministic under SEED; keeps runtime to seconds.
N_SESSIONS = 300
N_CHASE_TRIALS = 400
CHASE_REROLL_CAP = 5000


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def registry():
    """The REAL data registry (items, affixes, templates, balance)."""
    reg = DataRegistry()
    reg.load_all(_REAL_DATA_DIR)
    return reg


# ------------------------------------------------------------------ #
#  Session simulation (mirrors base_elimination's drop wiring)
# ------------------------------------------------------------------ #

def _tunable(template, balance, name, default=0.0):
    """Template-overrides-balance knob read (base_elimination._tunable)."""
    val = getattr(template, name, None)
    if val is None:
        val = getattr(balance, name, None)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _source_weight(balance, bucket):
    """The rarity table's min_weight threshold for *bucket* (design §3.2)."""
    row = dict(getattr(balance, "rarity_table", None) or {}).get(bucket) or {}
    try:
        return float(row.get("min_weight", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _session_drop_keys(registry, rng):
    """One session's gear drops: ``[(item_key, source_rarity_weight), ...]``.

    HQ-destroy rounds + guard-kill gear rolls for every cleared base in
    SESSION_CLEARS, using the templates' shipped chances/pools.
    """
    balance = registry.balance
    drops = []
    for tier in SESSION_CLEARS:
        template = registry.get_base_template(tier)
        assert template is not None, f"missing base template {tier!r}"
        weight = _source_weight(balance, tier)

        # HQ destroy: gear_rolls rounds x (gear roll + rare roll).
        rounds = max(1, int(getattr(template, "gear_rolls", 1) or 1))
        for _ in range(rounds):
            for chance_key, pool_key in (
                ("gear_drop_chance", "gear_pool"),
                ("rare_gear_chance", "rare_pool"),
            ):
                pool = list(getattr(template, pool_key, None) or [])
                chance = _tunable(template, balance, chance_key, 0.0)
                if pool and rng.random() < chance:
                    drops.append((rng.choice(pool), weight))

        # Guard kills: the garrison, one gear roll each at weight 0.0
        # (the guard_kill bucket — R3.6).
        guard_chance = _tunable(template, balance,
                                "guard_gear_drop_chance", 0.0)
        gear_pool = list(getattr(template, "gear_pool", None) or [])
        garrison = sum(max(1, int(getattr(g, "count", 1) or 1))
                       for g in (getattr(template, "guards", None) or []))
        for _ in range(garrison):
            if gear_pool and rng.random() < guard_chance:
                drops.append((rng.choice(gear_pool), 0.0))
    return drops


def _salvage_yield(balance, iqs, level):
    """EquipmentSystem.salvage's design §5 yield formula, verbatim."""
    base = float(getattr(balance, "base_salvage", 5) or 0)
    per_iqs = float(getattr(balance, "salvage_per_iqs", 0.5) or 0)
    level_bonus = float(getattr(balance, "salvage_level_bonus", 0.125) or 0)
    level_mult = 1.0 + level_bonus * max(level - 1, 0)
    return max(0, int(round((base + iqs * per_iqs) * level_mult)))


def _session_income(registry, rng, level=BLACKSMITH_LEVEL):
    """Simulate one session; return (salvage_income, salvaged, kept)."""
    balance = registry.balance
    income, salvaged, kept = 0, 0, 0
    for item_key, weight in _session_drop_keys(registry, rng):
        item_def = registry.resolve_item(item_key)
        assert item_def is not None, f"unknown pool item {item_key!r}"
        result = roll_item(
            item_def,
            source_rarity_weight=weight,
            crafted=False,
            rng=rng,
            default_skew=float(getattr(balance, "loot_roll_skew", 2.0)),
            rarity_table=getattr(balance, "rarity_table", None),
            affix_pools=getattr(registry, "affixes", None),
        )
        if result is None:
            # Unrolled drop (no roll_spec, e.g. the scope) — salvages at
            # the base floor with iqs 0, like EquipmentSystem.salvage.
            income += _salvage_yield(balance, 0, level)
            salvaged += 1
            continue
        iqs = int(result.iqs or 0)
        if (result.rarity in KEEP_RARITIES) or iqs >= KEEP_IQS:
            kept += 1  # a chase item — never salvaged
            continue
        income += _salvage_yield(balance, iqs, level)
        salvaged += 1
    return income, salvaged, kept


def _mean_session_income(registry, rng, n=N_SESSIONS):
    return sum(_session_income(registry, rng)[0] for _ in range(n)) / n


# ------------------------------------------------------------------ #
#  Reroll-chase simulation (mirrors EquipmentSystem.reroll's backend)
# ------------------------------------------------------------------ #

def _chase_spec(registry):
    """The chase item's roll_spec — the §1.1 calibration anchor weapon."""
    item_def = registry.resolve_item("assault_rifle")
    assert item_def is not None and isinstance(item_def.roll_spec, dict)
    return item_def.roll_spec


def _rerolls_until(roll_spec, target_iqs, floor, skew, rng,
                   cap=CHASE_REROLL_CAP):
    """Rerolls needed until the fresh BASE IQS reaches *target_iqs*."""
    for n in range(1, cap + 1):
        rolled = reroll_base_stats(roll_spec, floor=floor, rng=rng,
                                   default_skew=skew)
        iqs = compute_iqs(rolled, roll_spec)
        if iqs is not None and iqs >= target_iqs:
            return n
    return cap


def _mean_rerolls_to(registry, target_iqs, rng, trials=N_CHASE_TRIALS):
    balance = registry.balance
    roll_spec = _chase_spec(registry)
    skew = float(getattr(balance, "loot_roll_skew", 2.0))
    # Mid-level bench floor: 0.1 x (3 - 1) = 0.2 (equipment_system §4.4).
    floor = REROLL_FLOOR_PER_LEVEL * (BLACKSMITH_LEVEL - 1)
    total = sum(_rerolls_until(roll_spec, target_iqs, floor, skew, rng)
                for _ in range(trials))
    return total / trials


def _reroll_cost(registry):
    return int(getattr(registry.balance, "reroll_salvage_cost", 40) or 0)


# ================================================================== #
#  The no-inflation guard (task 5.5, design §5, R7.5)
# ================================================================== #

class TestSalvageEconomyNeutrality:
    """Session Salvage-in ≈ reroll Salvage-out: bounded, non-inflationary."""

    def test_reroll_cost_within_design_band(self, registry):
        """The shipped reroll charge sits in design §9's 30-60 band, and
        even the Salvage Protocols floor (x0.5 clamp) cannot push the
        effective charge below half the band floor."""
        cost = _reroll_cost(registry)
        assert 30 <= cost <= 60
        assert int(round(cost * SALVAGE_COST_MULT_FLOOR)) >= 15

    def test_session_income_funds_a_bounded_handful_of_rerolls(self, registry):
        """A raid session's income sits in the neutrality band: at least a
        reroll every two sessions (Salvage banks — the sink is reachable),
        nowhere near the "hundreds of rerolls" inflation failure; the
        researched (x0.75) cost funds >= 1 reroll per session."""
        rng = random.Random(SEED)
        income = _mean_session_income(registry, rng)
        cost = _reroll_cost(registry)

        # Observed ~1.0 at the shipped numbers — the literal "Salvage-in
        # ≈ Salvage-out per session" design target.
        fundable = income / cost
        assert 0.5 <= fundable <= 20.0, (
            f"session income {income:.1f} Salvage funds {fundable:.2f} "
            f"rerolls at cost {cost} — outside the [0.5, 20] neutrality band"
        )

        # Salvage Protocols (task 5.4): the researched charge, clamped the
        # way _salvage_cost_multiplier clamps it, funds at least one
        # reroll per session — the economy tech visibly matters.
        tech = registry.technologies.get("salvage_protocols")
        assert tech is not None
        mult = float((tech.effect_value or {}).get("salvage_cost_mult", 1.0))
        mult = min(1.0, max(SALVAGE_COST_MULT_FLOOR, mult))
        researched_cost = max(1, int(round(cost * mult)))
        fundable_researched = income / researched_cost
        assert 1.0 <= fundable_researched <= 20.0, (
            f"researched cost {researched_cost}: funds "
            f"{fundable_researched:.2f} rerolls — outside [1, 20]"
        )

    def test_material_improvement_burns_a_session_of_mediocre_drops(
            self, registry):
        """Rerolling to a good item (base IQS >= 70) costs at least one
        session's Salvage income — mediocre drops get burned — while
        staying achievable (<= 60 rerolls on average)."""
        income = _mean_session_income(registry, random.Random(SEED))
        mean_rerolls = _mean_rerolls_to(registry, GOOD_IQS,
                                        random.Random(SEED + 1))
        cost = mean_rerolls * _reroll_cost(registry)

        assert mean_rerolls >= 2.0, (
            f"IQS>={GOOD_IQS} in {mean_rerolls:.1f} rerolls — a material "
            "improvement is nearly free; the skew/floor lost its bite"
        )
        assert mean_rerolls <= 60.0, (
            f"IQS>={GOOD_IQS} needs {mean_rerolls:.1f} rerolls — an "
            "ordinary upgrade is out of reach"
        )
        assert cost >= income, (
            f"improving to IQS>={GOOD_IQS} costs {cost:.0f} Salvage but a "
            f"session brings {income:.1f} — the drain is weaker than the "
            "flood (inflation)"
        )

    def test_god_roll_chase_costs_multiple_sessions_income(self, registry):
        """The god-roll chase (base IQS >= 85) burns MULTIPLE sessions'
        income (>= 3x — design: "reroll chase burns many mediocre drops")
        yet remains attainable (<= 200 sessions)."""
        income = _mean_session_income(registry, random.Random(SEED))
        mean_rerolls = _mean_rerolls_to(registry, GOD_IQS,
                                        random.Random(SEED + 2))
        cost = mean_rerolls * _reroll_cost(registry)
        sessions_needed = cost / income

        assert sessions_needed >= 3.0, (
            f"a god-roll (IQS>={GOD_IQS}) costs only {sessions_needed:.1f} "
            "sessions' income — the chase doesn't burn enough (inflation)"
        )
        assert sessions_needed <= 200.0, (
            f"a god-roll costs {sessions_needed:.1f} sessions' income — "
            "the chase is effectively unattainable"
        )
        assert mean_rerolls < CHASE_REROLL_CAP, (
            "god-roll chase hit the reroll cap — target unreachable at "
            "this floor/skew"
        )

    def test_simulation_is_deterministic_under_the_seed(self, registry):
        """Same seed → identical session income (the sim itself honors the
        injected-RNG determinism contract, R1.5)."""
        a = _mean_session_income(registry, random.Random(SEED), n=25)
        b = _mean_session_income(registry, random.Random(SEED), n=25)
        assert a == b
