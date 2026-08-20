"""
Property-based test for the R8 weapon-range resolution (item-loot-economy
task 3.1, design §6.1).

# Feature: item-loot-economy, Property 7: Range resolution formula, scope, and cap

For any weapon instance (including rolled and insert range), owner tech
bonus, and tile bonus, ``_resolve_weapon_range`` returns exactly
``weapon base + weapon_range tech + tile bonus`` clamped to
``max_weapon_range``, melee always resolves to 1, and a ``+range`` value on
any non-weapon equipped item has no effect on the result.

**Validates: Requirements 8.1, 8.3**
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

from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.constants import WEAPON_SLOT_BY_TYPE  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class FakeWeapon:
    """A weapon fake with GameItem ``get_stat`` semantics: a rolled value
    (``rolled_stats``) takes precedence over the def base
    (``stat_modifiers``), and affix magnitudes on the same axis (a +range
    affix or an applied insert on the WEAPON) add on top."""

    def __init__(self, base_range=3, rolled_range=None, range_affix=0,
                 weapon_type="ranged", key="test_weapon"):
        self.key = key
        self.slot = WEAPON_SLOT_BY_TYPE[weapon_type]
        self.category = "weapon"
        self.weapon_type = weapon_type
        self.stat_modifiers = {"damage": 10, "range": base_range}
        self.rolled_stats = (
            {"range": rolled_range} if rolled_range is not None else {}
        )
        self.affixes = (
            [{"stat": "range", "magnitude": range_affix}] if range_affix else []
        )
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        base = self.rolled_stats.get(stat_name)
        if base is None:
            base = self.stat_modifiers.get(stat_name, default)
        affix_total = sum(
            a["magnitude"] for a in self.affixes if a.get("stat") == stat_name
        )
        return float(base) + affix_total


class FakeAccessory:
    """A non-weapon gear fake carrying ``range`` in stat_modifiers (R8.1)."""

    def __init__(self, range_value, key="scope_charm"):
        self.key = key
        self.slot = "accessory"
        self.category = "gear"
        self.stat_modifiers = {"range": range_value}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakeEquipmentHandler:
    def __init__(self):
        self._slots = {}

    def equip(self, item):
        self._slots[getattr(item, "slot", "weapon_ranged")] = item

    def get_equipped(self, slot):
        return self._slots.get(slot)

    def get_stat_total(self, stat_name):
        return sum(
            item.get_stat(stat_name, 0) for item in self._slots.values()
        )


class FakePlayer:
    """A player fake (combat_xp set, so _owning_player resolves to it)."""

    def __init__(self, tech_range_bonus=0):
        self.key = "TestPlayer"
        self.db = types.SimpleNamespace(
            hp=100, hp_max=100, combat_xp=0,
            tech_bonuses={"weapon_range": tech_range_bonus},
        )
        self.equipment = FakeEquipmentHandler()


def _make_engine(max_weapon_range):
    registry = DataRegistry()
    registry.balance = BalanceConfig(max_weapon_range=max_weapon_range)
    return CombatEngine(
        registry=registry, event_bus=EventBus(), current_tick_func=lambda: 0,
    )

# -------------------------------------------------------------- #
#  Property 7: Range resolution formula, scope, and cap
#  # Feature: item-loot-economy, Property 7: Range resolution formula,
#  # scope, and cap
#  **Validates: Requirements 8.1, 8.3**
# -------------------------------------------------------------- #

class TestProperty7RangeResolution(unittest.TestCase):
    """Property 7: Range resolution formula, scope, and cap.

    # Feature: item-loot-economy, Property 7: Range resolution formula, scope, and cap

    For any weapon instance (including rolled and insert range), owner tech
    bonus, and tile bonus, ``_resolve_weapon_range`` returns exactly
    ``weapon base + weapon_range tech + tile bonus`` clamped to
    ``max_weapon_range``; melee always resolves to 1; and a ``+range`` value
    on any non-weapon equipped item has no effect on the result (R8.1 —
    range never aggregates across equipped items).

    **Validates: Requirements 8.1, 8.3**
    """

    @given(
        base_range=st.integers(min_value=1, max_value=20),
        rolled_range=st.one_of(st.none(), st.integers(min_value=1, max_value=20)),
        range_affix=st.integers(min_value=0, max_value=5),
        tech_bonus=st.integers(min_value=0, max_value=5),
        tile_bonus=st.integers(min_value=0, max_value=3),
        cap=st.integers(min_value=1, max_value=24),
        accessory_range=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=25)
    def test_range_formula_scope_and_cap(
        self, base_range, rolled_range, range_affix, tech_bonus,
        tile_bonus, cap, accessory_range,
    ):
        """result == weapon base (rolled preferred + weapon affixes/inserts)
        + weapon_range tech + tile bonus, clamped to max_weapon_range; a
        non-weapon +range item never contributes."""
        engine = _make_engine(max_weapon_range=cap)
        # Tile bonus stub: Sniper Nest ships in Phase 6; the formula term
        # exists now, so drive it directly.
        engine._tile_range_bonus = lambda attacker: tile_bonus

        weapon = FakeWeapon(
            base_range=base_range, rolled_range=rolled_range,
            range_affix=range_affix,
        )
        attacker = FakePlayer(tech_range_bonus=tech_bonus)
        attacker.equipment.equip(weapon)
        if accessory_range:
            # R8.1: range on a NON-WEAPON equipped item must be inert.
            attacker.equipment.equip(FakeAccessory(accessory_range))

        weapon_base = (
            rolled_range if rolled_range is not None else base_range
        ) + range_affix
        expected = min(cap, weapon_base + tech_bonus + tile_bonus)

        self.assertEqual(
            engine._resolve_weapon_range(attacker, weapon), expected,
        )

    @given(
        base_range=st.integers(min_value=1, max_value=20),
        rolled_range=st.one_of(st.none(), st.integers(min_value=1, max_value=20)),
        range_affix=st.integers(min_value=0, max_value=5),
        tech_bonus=st.integers(min_value=0, max_value=5),
        tile_bonus=st.integers(min_value=0, max_value=3),
        cap=st.integers(min_value=1, max_value=24),
    )
    @settings(max_examples=25)
    def test_melee_always_resolves_to_one(
        self, base_range, rolled_range, range_affix, tech_bonus,
        tile_bonus, cap,
    ):
        """A melee weapon resolves to 1 regardless of any range stat,
        rolled value, affix, tech, or tile bonus."""
        engine = _make_engine(max_weapon_range=cap)
        engine._tile_range_bonus = lambda attacker: tile_bonus

        weapon = FakeWeapon(
            base_range=base_range, rolled_range=rolled_range,
            range_affix=range_affix, weapon_type="melee",
        )
        attacker = FakePlayer(tech_range_bonus=tech_bonus)
        attacker.equipment.equip(weapon)

        self.assertEqual(engine._resolve_weapon_range(attacker, weapon), 1)


if __name__ == "__main__":
    unittest.main()
