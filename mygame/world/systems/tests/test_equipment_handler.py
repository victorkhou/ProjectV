"""
Unit tests for EquipmentHandler.

Tests equip, unequip, get_equipped, get_all_equipped, get_stat_total,
get_slot_names, auto-unequip on occupied slot, and slot matching.

Requirements: 6.2, 6.17, 6.18
"""

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
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.world.systems.equipment_handler import EquipmentHandler  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

class FakeItem:
    """Lightweight stand-in for a GameItem."""

    def __init__(self, key: str, slot: str, stat_modifiers: dict | None = None):
        self.key = key
        self.slot = slot
        self.stat_modifiers = stat_modifiers or {}

    def get_stat(self, stat_name: str, default: float = 0) -> float:
        return float(self.stat_modifiers.get(stat_name, default))

class FakeCharacter:
    """Lightweight stand-in for a CombatCharacter (no Evennia DB)."""

    def __init__(self):
        self._equipment_slots = {}

# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestEquipBasic(unittest.TestCase):
    def test_equip_item_to_empty_slot(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("assault_rifle", "weapon", {"damage": 25})
        ok, msg = handler.equip(item)
        self.assertTrue(ok)
        self.assertIn("weapon", msg)
        self.assertIs(handler.get_equipped("weapon"), item)

    def test_equip_returns_false_for_no_slot(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("broken", "", {})
        ok, msg = handler.equip(item)
        self.assertFalse(ok)

    def test_equip_multiple_slots(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        weapon = FakeItem("rifle", "weapon", {"damage": 20})
        armor = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(weapon)
        handler.equip(armor)
        self.assertIs(handler.get_equipped("weapon"), weapon)
        self.assertIs(handler.get_equipped("armor"), armor)

class TestAutoUnequip(unittest.TestCase):
    def test_equip_occupied_slot_replaces_item(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        old = FakeItem("knife", "weapon", {"damage": 10})
        new = FakeItem("rifle", "weapon", {"damage": 25})
        handler.equip(old)
        handler.equip(new)
        self.assertIs(handler.get_equipped("weapon"), new)

    def test_equip_occupied_slot_returns_success(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        old = FakeItem("knife", "weapon", {"damage": 10})
        new = FakeItem("rifle", "weapon", {"damage": 25})
        handler.equip(old)
        ok, msg = handler.equip(new)
        self.assertTrue(ok)

class TestUnequip(unittest.TestCase):
    def test_unequip_returns_item(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(item)
        returned = handler.unequip("armor")
        self.assertIs(returned, item)

    def test_unequip_leaves_slot_empty(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        item = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(item)
        handler.unequip("armor")
        self.assertIsNone(handler.get_equipped("armor"))

    def test_unequip_empty_slot_returns_none(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertIsNone(handler.unequip("weapon"))

class TestGetAllEquipped(unittest.TestCase):
    def test_returns_all_equipped(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        w = FakeItem("rifle", "weapon", {"damage": 25})
        a = FakeItem("vest", "armor", {"damage_reduction": 5})
        handler.equip(w)
        handler.equip(a)
        all_eq = handler.get_all_equipped()
        self.assertEqual(len(all_eq), 2)
        self.assertIs(all_eq["weapon"], w)
        self.assertIs(all_eq["armor"], a)

    def test_empty_when_nothing_equipped(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertEqual(handler.get_all_equipped(), {})

class TestGetStatTotal(unittest.TestCase):
    def test_sums_stat_across_items(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        w = FakeItem("rifle", "weapon", {"damage": 25, "range": 5})
        g = FakeItem("scope", "gadget", {"sight_range": 3, "damage": 2})
        handler.equip(w)
        handler.equip(g)
        self.assertAlmostEqual(handler.get_stat_total("damage"), 27.0)

    def test_returns_zero_for_missing_stat(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        w = FakeItem("rifle", "weapon", {"damage": 25})
        handler.equip(w)
        self.assertAlmostEqual(handler.get_stat_total("sight_range"), 0.0)

    def test_returns_zero_when_empty(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertAlmostEqual(handler.get_stat_total("damage"), 0.0)

class TestGetSlotNames(unittest.TestCase):
    def test_returns_occupied_slots(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        handler.equip(FakeItem("rifle", "weapon"))
        handler.equip(FakeItem("vest", "armor"))
        names = sorted(handler.get_slot_names())
        self.assertEqual(names, ["armor", "weapon"])

    def test_empty_when_nothing_equipped(self):
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        self.assertEqual(handler.get_slot_names(), [])

if __name__ == "__main__":
    unittest.main()
