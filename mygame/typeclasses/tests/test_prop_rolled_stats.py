"""
Property-based tests for GameItem per-instance roll storage.

Property 9: Unrolled items are untouched

Validates: Requirements 1.3, 2.5, 12.1
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

    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "TestItem")
            self.location = None

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultCharacter": type("DefaultCharacter", (), {}),
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

from mygame.typeclasses.objects import GameItem  # noqa: E402
from mygame.world.definitions import ItemDef  # noqa: E402

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

STAT_NAMES = [
    "damage", "damage_reduction", "range", "sight_range", "move_speed",
    "fire_resist", "poison_resist", "damage_bonus",
]

@st.composite
def unrolled_item_def_strategy(draw):
    """Generate an ItemDef WITHOUT a roll_spec (a fixed, never-rolled item)."""
    key = draw(st.text(
        alphabet=st.characters(whitelist_categories=("Ll",),
                               whitelist_characters="_"),
        min_size=1, max_size=20,
    ))
    stats = draw(st.dictionaries(
        keys=st.sampled_from(STAT_NAMES),
        values=st.one_of(
            st.integers(min_value=0, max_value=100),
            st.floats(min_value=0.0, max_value=100.0,
                      allow_nan=False, allow_infinity=False),
        ),
        max_size=4,
    ))
    return ItemDef(
        key=key, name=key, slot="weapon_ranged", category="weapon",
        weapon_type="ranged", stat_modifiers=stats,
    )


def _spawn_unrolled(item_def: ItemDef) -> GameItem:
    """Mimic the spawn path for a def without roll_spec: copy stat_modifiers
    from the ItemDef onto the instance; no roll data is ever written."""
    item = GameItem(key=item_def.key)
    item.attributes.add("item_key", item_def.key)
    item.attributes.add("slot", item_def.slot)
    item.attributes.add("stat_modifiers", dict(item_def.stat_modifiers))
    return item

# -------------------------------------------------------------- #
#  Property 9: Unrolled items are untouched
#  **Validates: Requirements 1.3, 2.5, 12.1**
# -------------------------------------------------------------- #

# Feature: item-loot-economy, Property 9: Unrolled items are untouched
class TestProperty9UnrolledItemsUntouched(unittest.TestCase):
    """Property 9: Unrolled items are untouched.

    For any ItemDef without a roll_spec, the spawned item carries no
    rolled_stats/iqs/rarity, and get_stat returns the def base exactly
    as today — never retro-rolled.

    **Validates: Requirements 1.3, 2.5, 12.1**
    """

    @given(item_def=unrolled_item_def_strategy(),
           default=st.floats(min_value=-10, max_value=10,
                             allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_prop_unrolled_items_untouched(self, item_def, default):
        assert item_def.roll_spec is None

        item = _spawn_unrolled(item_def)

        # Carries no roll state at all — never retro-rolled.
        self.assertFalse(item.attributes.has("rolled_stats"))
        self.assertFalse(item.attributes.has("iqs"))
        self.assertFalse(item.attributes.has("rarity"))
        self.assertEqual(item.rolled_stats, {})
        self.assertIsNone(item.iqs)
        self.assertIsNone(item.rarity)

        # get_stat returns the def base exactly, for every stat on the def.
        for stat, base in item_def.stat_modifiers.items():
            self.assertEqual(item.get_stat(stat), float(base))

        # A stat absent from the def falls through to the caller's default.
        self.assertEqual(item.get_stat("__absent_stat__", default),
                         float(default))

        # Display is neutral: no IQS/rarity readout anywhere (R2.5).
        self.assertEqual(item.get_quality_tag(), "")
        self.assertEqual(item.get_display_name(), item_def.key)
        self.assertEqual(item.get_roll_details(), [])


if __name__ == "__main__":
    unittest.main()
