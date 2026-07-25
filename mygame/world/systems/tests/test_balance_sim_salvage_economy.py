"""
Balance simulation — the Salvage economy over a raid session (task 5.5, R7.5).

Simulates a representative raid session against the REAL balance numbers
(``data/config/balance.yaml``), the REAL item roll specs
(``data/definitions/items.yaml``), the REAL affix pools
(``data/definitions/affixes.yaml``) and the REAL code paths:

- drops roll through ``loot_roller.roll_item`` (rarity table + affix pools),
- mediocre drops (below an IQS threshold) are salvaged through the real
  ``EquipmentSystem.salvage`` at a mid-level (L3) Blacksmith,
- the accumulated Salvage funds a god-roll reroll chase through the real
  ``EquipmentSystem.reroll`` (charging the real 40 Salvage + 10 Iron).

Asserted bands (R7.5 — the economy does NOT inflate):

1. **The chase burns many mediocre drops**: one reroll costs MORE than the
   Salvage yield of a single average mediocre drop, so chasing a god roll
   requires salvaging multiple drops (rerolls funded < drops salvaged).
2. **Session in/out ≈ neutral**: the session's total Salvage income is the
   same order as the Salvage a plausible reroll cadence burns (one reroll
   per ~4 drops) — the in/out ratio stays inside a generous band, not 10x
   either way (rolls flood in, Salvage is the drain).
3. **The sink can absorb the flood**: spending the session's income on
   rerolls leaves less than one reroll's charge unspent, and the average
   income per salvaged drop never reaches the per-reroll charge.

Statistical assertions: the whole session runs once under a FIXED seed with
generous bands, so the test is deterministic and non-flaky. If a balance
retune moves the shipped numbers (base_salvage / salvage_per_iqs /
reroll_salvage_cost), re-check these bands against design §5/§9.

Validates: Requirements 7.5
"""

import os
import random
import sys
import types
import unittest

# -------------------------------------------------------------- #
#  Bootstrap: stub out Evennia modules
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
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402
from mygame.world.systems.loot_roller import roll_item  # noqa: E402

# -------------------------------------------------------------- #
#  Session shape (a representative raid — design §1.2 / task 5.5)
# -------------------------------------------------------------- #

SEED = 20260724

#: Source mix: mostly guard/outpost drops, some stronghold/fortress.
#: (bucket name, number of drops in the session) — the bucket's
#: rarity weight is read from the REAL balance rarity_table min_weight.
SESSION_MIX = (
    ("guard_kill", 60),
    ("outpost", 30),
    ("stronghold", 20),
    ("fortress", 10),
)

#: Drops reading below this displayed IQS are "mediocre" → salvaged.
MEDIOCRE_IQS_THRESHOLD = 60

#: The bench: a mid-level Blacksmith (L3 → yield mult 1.25 at defaults).
BLACKSMITH_LEVEL = 3

#: Plausible chase cadence for the neutrality band: one reroll per this
#: many session drops (a chaser rerolls their keeper every few kills).
CADENCE_DROPS_PER_REROLL = 4

#: Generous neutrality band for salvage-in / cadence-salvage-out
#: (R7.5: "the same order" — nowhere near 10x either way).
NEUTRAL_RATIO_LO = 1.0 / 3.0
NEUTRAL_RATIO_HI = 3.0


# -------------------------------------------------------------- #
#  Fakes (the same duck-typed shapes the equipment-system tests use)
# -------------------------------------------------------------- #

class _DB:
    """A tiny attribute bag standing in for an Evennia ``.db`` proxy."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Player:
    """CombatCharacter stand-in: Salvage accessors + a resource pool."""

    def __init__(self, level=100, resources=None):
        self.key = "SimRaider"
        self.db = _DB(level=level, salvage=0,
                      resources=dict(resources or {}))
        self.contents = []

    # Salvage currency (mirrors typeclasses/characters.py accessors).
    def get_salvage(self):
        return int(getattr(self.db, "salvage", 0) or 0)

    def add_salvage(self, amount):
        self.db.salvage = max(0, self.get_salvage() + int(amount))

    def spend_salvage(self, amount):
        amount = int(amount)
        if amount < 0:
            return False
        balance = self.get_salvage()
        if balance < amount:
            return False
        self.db.salvage = balance - amount
        return True

    # Resource pool (Spend_Pool).
    def get_resource(self, resource):
        return int(self.db.resources.get(str(resource).title(), 0))

    def add_resource(self, resource, amount):
        key = str(resource).title()
        self.db.resources[key] = self.db.resources.get(key, 0) + int(amount)

    def has_resources(self, costs):
        return all(
            self.db.resources.get(str(r).title(), 0) >= amt
            for r, amt in costs.items()
        )

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            key = str(r).title()
            self.db.resources[key] = self.db.resources.get(key, 0) - int(amt)
        return True


class _DropItem:
    """A loose carried rolled drop with its stamped per-instance state."""

    def __init__(self, key, name, iqs):
        self.key = key
        self.name = name
        self.db = _DB()
        if iqs is not None:
            self.db.iqs = int(iqs)
        self.deleted = False

    def delete(self):
        self.deleted = True


class _ChaseWeapon:
    """The keeper weapon being rerolled (GameItem-style db bag)."""

    def __init__(self, key="assault_rifle", name="Assault Rifle"):
        self.key = key
        self.name = name
        self.db = _DB(rolled_stats={"damage": 20.0, "range": 4.0})


class _Blacksmith:
    """A built Blacksmith bench (BS building instance) stand-in."""

    def __init__(self, owner, level):
        self.key = "Blacksmith"
        self.db = _DB(building_type="BS", offline=False,
                      under_construction=False, building_level=int(level))
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return False


# -------------------------------------------------------------- #
#  The simulation
# -------------------------------------------------------------- #

def _data_dir():
    return os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "data",
    ))


def _run_session(registry):
    """Run one seeded raid session; return the measured economy numbers."""
    balance = registry.balance
    rarity_table = dict(getattr(balance, "rarity_table", None) or {})
    skew = float(getattr(balance, "loot_roll_skew", 2.0))

    # The lootable gear pool: every real item def that rolls (weapons +
    # armor with a roll_spec) — sorted for a deterministic draw order.
    gear_pool = sorted(
        (d for d in registry.items.values()
         if isinstance(getattr(d, "roll_spec", None), dict)
         and getattr(d, "roll_spec", {}).get("stats")),
        key=lambda d: d.key,
    )
    assert gear_pool, "no rollable gear defs in the real items.yaml"

    rng = random.Random(SEED)
    system = EquipmentSystem(registry, EventBus())
    system._rng = random.Random(SEED + 1)  # deterministic rerolls

    player = _Player(level=100, resources={"Iron": 100_000})
    bench = _Blacksmith(owner=player, level=BLACKSMITH_LEVEL)

    # ---- Phase A: the drop session → salvage the mediocre ones ---- #
    drops = []          # (bucket, item_key, iqs)
    mediocre_yields = []
    keepers = 0
    for bucket, count in SESSION_MIX:
        weight = float(rarity_table.get(bucket, {}).get("min_weight", 0.0))
        for _ in range(count):
            item_def = rng.choice(gear_pool)
            result = roll_item(
                item_def,
                source_rarity_weight=weight,
                crafted=False,
                rng=rng,
                default_skew=skew,
                rarity_table=rarity_table,
                affix_pools=registry.affixes,
            )
            assert result is not None, f"{item_def.key} failed to roll"
            iqs = int(result.iqs or 0)
            drops.append((bucket, item_def.key, iqs))
            if iqs >= MEDIOCRE_IQS_THRESHOLD:
                keepers += 1
                continue
            # Salvage the mediocre drop through the REAL code path.
            drop = _DropItem(item_def.key, item_def.name, iqs)
            player.contents = [drop]
            before = player.get_salvage()
            ok = system.salvage(player, item_def.key, bench)
            assert ok, f"salvage refused for {item_def.key}"
            assert drop.deleted, "salvage must destroy the source item"
            mediocre_yields.append(player.get_salvage() - before)

    salvage_in = player.get_salvage()

    # ---- Phase B: the reroll chase burns the session's Salvage ---- #
    weapon = _ChaseWeapon()
    player.contents = [weapon]
    reroll_charges = []
    while True:
        before = player.get_salvage()
        if not system.reroll(player, weapon.key, bench):
            break
        reroll_charges.append(before - player.get_salvage())

    return {
        "drops": drops,
        "n_drops": len(drops),
        "n_salvaged": len(mediocre_yields),
        "n_keepers": keepers,
        "mediocre_yields": mediocre_yields,
        "salvage_in": salvage_in,
        "rerolls_funded": len(reroll_charges),
        "reroll_charges": reroll_charges,
        "salvage_left": player.get_salvage(),
    }


# -------------------------------------------------------------- #
#  The assertions (R7.5 bands)
# -------------------------------------------------------------- #

class TestSalvageEconomySession(unittest.TestCase):
    """Balance sim: Salvage in/out over a raid session (task 5.5, R7.5).

    One seeded session at the SHIPPED balance numbers; every band below is
    deliberately generous — the test guards against the economy drifting
    an order of magnitude, not against small retunes.

    Validates: Requirements 7.5
    """

    @classmethod
    def setUpClass(cls):
        cls.registry = DataRegistry()
        cls.registry.load_all(_data_dir())
        cls.sim = _run_session(cls.registry)

    # ------------------- session sanity ------------------- #

    def test_session_shape_is_representative(self):
        """The seeded session produced drops, salvage fodder AND keepers —
        the mix exercises both sides of the threshold (sim validity)."""
        sim = self.sim
        self.assertEqual(sim["n_drops"],
                         sum(count for _, count in SESSION_MIX))
        self.assertEqual(sim["n_salvaged"] + sim["n_keepers"],
                         sim["n_drops"])
        # Most loot is fodder (skew 2 → median roll ~25% of band), but the
        # session also surfaces chase-worthy keepers.
        self.assertGreater(sim["n_salvaged"], sim["n_drops"] // 2)
        self.assertGreater(sim["n_keepers"], 0)
        self.assertGreater(sim["salvage_in"], 0)

    # ------------------- band 1: the chase burns drops ------------------- #

    def test_one_reroll_costs_more_than_one_mediocre_drop(self):
        """One reroll (40 Salvage at the shipped numbers) costs MORE than
        the average mediocre drop's yield — a god-roll chase can never be
        funded one-drop-per-reroll (R7.5)."""
        sim = self.sim
        charge = sim["reroll_charges"][0]
        avg_yield = sim["salvage_in"] / sim["n_salvaged"]
        self.assertGreater(charge, avg_yield)

    def test_chase_burns_many_mediocre_drops(self):
        """The session funds strictly fewer rerolls than the drops it
        salvaged — every reroll burns MULTIPLE mediocre drops."""
        sim = self.sim
        self.assertGreater(sim["rerolls_funded"], 0)
        self.assertLess(sim["rerolls_funded"], sim["n_salvaged"])

    # ------------------- band 2: in/out ≈ neutral ------------------- #

    def test_session_salvage_in_out_roughly_neutral(self):
        """Salvage-in over the session is the same order as the Salvage a
        plausible reroll cadence (one per ~4 drops) burns: the in/out
        ratio stays inside [1/3, 3] — not 10x either way (R7.5)."""
        sim = self.sim
        charge = sim["reroll_charges"][0]
        cadence_rerolls = sim["n_drops"] / CADENCE_DROPS_PER_REROLL
        salvage_out = cadence_rerolls * charge
        ratio = sim["salvage_in"] / salvage_out
        self.assertGreaterEqual(
            ratio, NEUTRAL_RATIO_LO,
            f"Salvage economy starves: in {sim['salvage_in']} vs plausible "
            f"out {salvage_out:.0f} (ratio {ratio:.2f})")
        self.assertLessEqual(
            ratio, NEUTRAL_RATIO_HI,
            f"Salvage economy inflates: in {sim['salvage_in']} vs plausible "
            f"out {salvage_out:.0f} (ratio {ratio:.2f})")

    # ------------------- band 3: the sink absorbs the flood ---------- #

    def test_sink_absorbs_the_sessions_income(self):
        """Spending the session's income on rerolls leaves less than one
        charge unspent, every reroll charged the same flat rate, and the
        average income per salvaged drop stays below the charge — income
        can never outpace one reroll per drop (no inflation, R7.5)."""
        sim = self.sim
        charge = sim["reroll_charges"][0]
        self.assertLess(sim["salvage_left"], charge)
        self.assertEqual(set(sim["reroll_charges"]), {charge})
        self.assertLess(sim["salvage_in"] / sim["n_salvaged"], charge)


if __name__ == "__main__":
    unittest.main()
