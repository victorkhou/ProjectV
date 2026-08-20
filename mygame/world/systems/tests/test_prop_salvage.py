"""
Property-based tests for Blacksmith salvage (item-loot-economy task 5.2,
design §5/§4.4).

# Feature: item-loot-economy, Property 10: Salvage yield formula and item destruction

For any rolled item and any Blacksmith level, salvaging yields exactly
``round((base_salvage + iqs * salvage_per_iqs) * level_mult(blacksmith_level))``
(monotone non-decreasing in both IQS and Blacksmith level) credited to the
owner's ``db.salvage``, and the source item is destroyed.

**Validates: Requirements 7.1, 7.2, 7.3**
"""

import sys
import types
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

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

from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig,
    BuildingDef,
    ItemDef,
)
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _DB:
    """A tiny attribute bag standing in for an Evennia ``.db`` proxy."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Player:
    """CombatCharacter stand-in with the real Salvage accessor semantics."""

    def __init__(self, salvage=0):
        self.key = "PropPlayer"
        self.db = _DB(salvage=int(salvage))
        self.contents = []

    def get_salvage(self):
        return int(getattr(self.db, "salvage", 0) or 0)

    def add_salvage(self, amount):
        self.db.salvage = max(0, self.get_salvage() + int(amount))


class _Item:
    """A loose carried rolled-item stand-in with iqs + delete tracking."""

    def __init__(self, iqs):
        self.key = "assault_rifle"
        self.name = "Assault Rifle"
        self.db = _DB(iqs=int(iqs))
        self.deleted = False

    def delete(self):
        self.deleted = True


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


def _make_system(balance):
    registry = DataRegistry()
    registry.items = {
        "assault_rifle": ItemDef(
            key="assault_rifle", name="Assault Rifle", slot="weapon_ranged",
            category="weapon", weapon_type="ranged",
            stat_modifiers={"damage": 25, "range": 5}, weight=8.0,
        ),
    }
    registry.buildings = {
        "BS": BuildingDef(
            name="Blacksmith", abbreviation="BS", cost={"Iron": 50},
            max_health=300, requires_hq=True, required_terrain=None,
            category="equipment", produces=None,
            capabilities=frozenset({"blacksmith"}),
        ),
    }
    registry.balance = balance
    return EquipmentSystem(registry, EventBus())


def _salvage_yield(system, iqs, level, salvage_start=0):
    """Run one full salvage and return ``(ok, yield, item)``."""
    player = _Player(salvage=salvage_start)
    item = _Item(iqs=iqs)
    player.contents = [item]
    bench = _Blacksmith(owner=player, level=level)
    ok = system.salvage(player, "assault_rifle", bench)
    return ok, player.get_salvage() - salvage_start, item


# -------------------------------------------------------------- #
#  Property 10: Salvage yield formula and item destruction
#  # Feature: item-loot-economy, Property 10: Salvage yield formula
#  # and item destruction
#  **Validates: Requirements 7.1, 7.2, 7.3**
# -------------------------------------------------------------- #

_finite = {"allow_nan": False, "allow_infinity": False}

_iqs_st = st.integers(min_value=0, max_value=100)
_level_st = st.integers(min_value=1, max_value=5)
_base_st = st.integers(min_value=0, max_value=50)
_per_iqs_st = st.floats(min_value=0.0, max_value=2.0, **_finite)
_level_bonus_st = st.floats(min_value=0.0, max_value=0.5, **_finite)
_start_st = st.integers(min_value=0, max_value=10_000)


class TestProperty10SalvageYieldAndDestruction(unittest.TestCase):
    """Property 10: Salvage yield formula and item destruction.

    # Feature: item-loot-economy, Property 10: Salvage yield formula and item destruction

    For any rolled item and any Blacksmith level, salvaging yields exactly
    ``round((base_salvage + iqs * salvage_per_iqs)
    * level_mult(blacksmith_level))`` where
    ``level_mult(l) = 1 + salvage_level_bonus * (l - 1)`` (monotone
    non-decreasing in both IQS and Blacksmith level), credited to the
    owner's ``db.salvage``, and the source item is destroyed.

    **Validates: Requirements 7.1, 7.2, 7.3**
    """

    @given(
        iqs=_iqs_st,
        level=_level_st,
        base=_base_st,
        per_iqs=_per_iqs_st,
        level_bonus=_level_bonus_st,
        start=_start_st,
    )
    @settings(max_examples=25)
    def test_prop_yield_formula_credit_and_destruction(
        self, iqs, level, base, per_iqs, level_bonus, start
    ):
        """The credited yield equals the design §5 formula exactly, lands
        on ``db.salvage`` on top of the prior balance, and the source item
        is destroyed (R7.1, R7.3, R7.4)."""
        system = _make_system(BalanceConfig(
            base_salvage=base, salvage_per_iqs=per_iqs,
            salvage_level_bonus=level_bonus))

        ok, credited, item = _salvage_yield(
            system, iqs, level, salvage_start=start)

        expected = int(round((base + iqs * per_iqs)
                             * (1.0 + level_bonus * (level - 1))))
        self.assertTrue(ok)
        self.assertEqual(credited, expected)
        self.assertTrue(item.deleted, "source item must be destroyed")

    @given(
        iqs_a=_iqs_st,
        iqs_b=_iqs_st,
        level=_level_st,
    )
    @settings(max_examples=25)
    def test_prop_yield_monotone_non_decreasing_in_iqs(
        self, iqs_a, iqs_b, level
    ):
        """At any bench level, a higher-IQS item never salvages for less
        (R7.1) — checked at the shipped balance defaults."""
        system = _make_system(BalanceConfig())
        lo, hi = sorted((iqs_a, iqs_b))

        _, yield_lo, _ = _salvage_yield(system, lo, level)
        _, yield_hi, _ = _salvage_yield(system, hi, level)

        self.assertLessEqual(yield_lo, yield_hi)

    @given(iqs=_iqs_st)
    @settings(max_examples=25)
    def test_prop_yield_monotone_non_decreasing_in_level(self, iqs):
        """For the same item, the yield at L1..L5 is monotone
        non-decreasing (R7.2) — checked at the shipped balance defaults
        (L1 1.0x → L5 1.5x)."""
        system = _make_system(BalanceConfig())

        yields = [_salvage_yield(system, iqs, level)[1]
                  for level in (1, 2, 3, 4, 5)]

        self.assertEqual(yields, sorted(yields))


if __name__ == "__main__":
    unittest.main()
