"""
Property-based tests for EquipmentHandler.

Property 31: EquipmentHandler slot management
Property 32: Equip/unequip round-trip

Validates: Requirements 6.2, 6.17, 6.18
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

    def __repr__(self):
        return f"FakeItem({self.key!r}, {self.slot!r})"

class FakeCharacter:
    """Lightweight stand-in for a CombatCharacter (no Evennia DB)."""

    def __init__(self):
        self._equipment_slots = {}

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

VALID_SLOTS = ["weapon", "armor", "gadget", "consumable", "accessory"]

STAT_NAMES = ["damage", "damage_reduction", "range", "sight_range", "move_speed"]

@st.composite
def game_item_strategy(draw):
    """Generate a random FakeItem with a valid slot and stat modifiers."""
    slot = draw(st.sampled_from(VALID_SLOTS))
    key = draw(st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"),
                               whitelist_characters="_"),
        min_size=1, max_size=20,
    ))
    # Generate 0-3 stat modifiers
    num_stats = draw(st.integers(min_value=0, max_value=3))
    chosen_stats = draw(
        st.lists(st.sampled_from(STAT_NAMES), min_size=num_stats,
                 max_size=num_stats, unique=True)
    )
    stat_mods = {s: draw(st.floats(min_value=0.1, max_value=100.0,
                                    allow_nan=False, allow_infinity=False))
                 for s in chosen_stats}
    return FakeItem(key=key, slot=slot, stat_modifiers=stat_mods)

@st.composite
def item_list_strategy(draw):
    """Generate a list of 1-5 items, possibly with duplicate slots."""
    items = draw(st.lists(game_item_strategy(), min_size=1, max_size=5))
    return items

# -------------------------------------------------------------- #
#  Property 31: EquipmentHandler slot management
#  **Validates: Requirements 6.2, 6.17, 6.18**
# -------------------------------------------------------------- #

class TestProperty31SlotManagement(unittest.TestCase):
    """Property 31: EquipmentHandler slot management.

    For any GameItem with a defined slot, equipping it SHALL place it in
    the matching Equipment_Slot. Each slot SHALL hold at most one GameItem;
    equipping a new item to an occupied slot SHALL unequip the existing item.
    Unequipping a slot SHALL remove the item and leave the slot empty.

    **Validates: Requirements 6.2, 6.17, 6.18**
    """

    @given(items=item_list_strategy())
    @settings(max_examples=100)
    def test_each_slot_holds_at_most_one_item(self, items):
        """After equipping any sequence of items, each slot holds at most one."""
        char = FakeCharacter()
        handler = EquipmentHandler(char)

        for item in items:
            handler.equip(item)

        equipped = handler.get_all_equipped()
        # Each slot key appears at most once (dict guarantees this)
        # and each value is a single item, not a list
        for slot, item in equipped.items():
            self.assertIsNotNone(item)
            # The item in the slot should be the LAST item equipped to that slot
            self.assertIsInstance(item, FakeItem)

    @given(items=item_list_strategy())
    @settings(max_examples=100)
    def test_last_equipped_item_wins_per_slot(self, items):
        """For each slot, the equipped item is the last one equipped to it."""
        char = FakeCharacter()
        handler = EquipmentHandler(char)

        # Track the last item equipped per slot
        last_per_slot = {}
        for item in items:
            ok, _ = handler.equip(item)
            if ok:
                last_per_slot[item.slot] = item

        for slot, expected_item in last_per_slot.items():
            actual = handler.get_equipped(slot)
            self.assertIs(actual, expected_item,
                          f"Slot {slot!r}: expected {expected_item!r}, got {actual!r}")

    @given(item=game_item_strategy())
    @settings(max_examples=100)
    def test_unequip_leaves_slot_empty(self, item):
        """Unequipping a slot SHALL leave it empty."""
        char = FakeCharacter()
        handler = EquipmentHandler(char)
        handler.equip(item)
        handler.unequip(item.slot)
        self.assertIsNone(handler.get_equipped(item.slot))

# -------------------------------------------------------------- #
#  Property 32: Equip/unequip round-trip
#  **Validates: Requirements 6.2, 6.17**
# -------------------------------------------------------------- #

class TestProperty32EquipUnequipRoundTrip(unittest.TestCase):
    """Property 32: Equip/unequip round-trip.

    For any GameItem equipped to its matching slot, unequipping that slot
    SHALL return the exact same GameItem, and the slot SHALL be empty.
    Re-equipping the same item SHALL restore the slot to its previous state.

    **Validates: Requirements 6.2, 6.17**
    """

    @given(item=game_item_strategy())
    @settings(max_examples=100)
    def test_equip_then_unequip_returns_same_item(self, item):
        """Equip then unequip returns the exact same item object."""
        char = FakeCharacter()
        handler = EquipmentHandler(char)

        ok, _ = handler.equip(item)
        self.assertTrue(ok)

        returned = handler.unequip(item.slot)
        self.assertIs(returned, item)
        self.assertIsNone(handler.get_equipped(item.slot))

    @given(item=game_item_strategy())
    @settings(max_examples=100)
    def test_re_equip_restores_slot(self, item):
        """Re-equipping the same item after unequip restores the slot."""
        char = FakeCharacter()
        handler = EquipmentHandler(char)

        # Equip
        handler.equip(item)
        self.assertIs(handler.get_equipped(item.slot), item)

        # Unequip
        returned = handler.unequip(item.slot)
        self.assertIs(returned, item)
        self.assertIsNone(handler.get_equipped(item.slot))

        # Re-equip
        handler.equip(item)
        self.assertIs(handler.get_equipped(item.slot), item)

    @given(items=st.lists(game_item_strategy(), min_size=2, max_size=5))
    @settings(max_examples=100)
    def test_equip_unequip_all_round_trip(self, items):
        """Equipping then unequipping all items returns each one."""
        char = FakeCharacter()
        handler = EquipmentHandler(char)

        # Track last item per slot
        last_per_slot = {}
        for item in items:
            handler.equip(item)
            last_per_slot[item.slot] = item

        # Unequip all and verify round-trip
        for slot, expected in last_per_slot.items():
            returned = handler.unequip(slot)
            self.assertIs(returned, expected)
            self.assertIsNone(handler.get_equipped(slot))

if __name__ == "__main__":
    unittest.main()
