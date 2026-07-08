"""
Unit tests for the GameItem typeclass field accessors.

Verifies that the new equipment/weapon/supply metadata fields
(category, weapon_type, ammo_type, ammo_per_shot, magazine_size,
effect, max_stack, weight) are exposed on a live GameItem as named
property accessors that read from Evennia attributes with defaults
matching ItemDef, and that the creation factory copies them.

Requirements: 14.4
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
from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402


# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_item(**attrs) -> GameItem:
    """Create a GameItem with stubbed Evennia internals and attributes set."""
    item = GameItem(key=attrs.pop("key", "TestItem"))
    for name, value in attrs.items():
        item.attributes.add(name, value)
    return item


# -------------------------------------------------------------- #
#  Tests: defaults match ItemDef defaults when attribute unset
# -------------------------------------------------------------- #

class TestGameItemAccessorDefaults(unittest.TestCase):
    """When no attribute is set, accessors return the ItemDef defaults."""

    def setUp(self):
        self.item = _make_item()
        self.defaults = ItemDef(key="x", name="X")

    def test_category_default(self):
        self.assertEqual(self.item.category, self.defaults.category)

    def test_weapon_type_default_none(self):
        self.assertIsNone(self.item.weapon_type)
        self.assertEqual(self.item.weapon_type, self.defaults.weapon_type)

    def test_ammo_type_default_none(self):
        self.assertIsNone(self.item.ammo_type)

    def test_ammo_per_shot_default(self):
        self.assertEqual(self.item.ammo_per_shot, self.defaults.ammo_per_shot)
        self.assertEqual(self.item.ammo_per_shot, 1)

    def test_magazine_size_default_none(self):
        self.assertIsNone(self.item.magazine_size)

    def test_effect_default_none(self):
        self.assertIsNone(self.item.effect)

    def test_max_stack_default(self):
        self.assertEqual(self.item.max_stack, self.defaults.max_stack)
        self.assertEqual(self.item.max_stack, 99)

    def test_weight_default(self):
        self.assertEqual(self.item.weight, self.defaults.weight)
        self.assertEqual(self.item.weight, 1.0)


# -------------------------------------------------------------- #
#  Tests: accessors return set attribute values
# -------------------------------------------------------------- #

class TestGameItemAccessorValues(unittest.TestCase):
    """Accessors reflect the values stored on the object's attributes."""

    def test_ranged_weapon_fields(self):
        item = _make_item(
            key="assault_rifle",
            category="weapon",
            weapon_type="ranged",
            ammo_type="rifle_rounds",
            ammo_per_shot=2,
            magazine_size=30,
            weight=8.0,
        )
        self.assertEqual(item.category, "weapon")
        self.assertEqual(item.weapon_type, "ranged")
        self.assertEqual(item.ammo_type, "rifle_rounds")
        self.assertEqual(item.ammo_per_shot, 2)
        self.assertEqual(item.magazine_size, 30)
        self.assertEqual(item.weight, 8.0)

    def test_melee_weapon_fields(self):
        item = _make_item(category="weapon", weapon_type="melee")
        self.assertEqual(item.weapon_type, "melee")
        self.assertIsNone(item.ammo_type)

    def test_consumable_effect_field(self):
        effect = {"type": "heal", "amount": 30}
        item = _make_item(category="consumable", effect=effect, max_stack=10)
        self.assertEqual(item.category, "consumable")
        self.assertEqual(item.effect, effect)
        self.assertEqual(item.max_stack, 10)

    def test_throwable_effect_field(self):
        effect = {"type": "aoe_damage", "amount": 40, "radius": 2}
        item = _make_item(category="throwable", effect=effect)
        self.assertEqual(item.category, "throwable")
        self.assertEqual(item.effect["radius"], 2)


# -------------------------------------------------------------- #
#  Tests: creation factory copies the new fields
# -------------------------------------------------------------- #

class TestCreationFactoryFieldCopy(unittest.TestCase):
    """EquipmentSystem._default_create_item copies the new ItemDef fields."""

    def test_factory_copies_all_new_fields(self):
        item_def = ItemDef(
            key="assault_rifle",
            name="Assault Rifle",
            slot="weapon",
            category="weapon",
            stat_modifiers={"damage": 25, "range": 3},
            weapon_type="ranged",
            ammo_type="rifle_rounds",
            ammo_per_shot=1,
            magazine_size=30,
            effect=None,
            max_stack=99,
            weight=8.0,
        )

        class _Owner:
            def __init__(self):
                self._inventory = []

        owner = _Owner()
        item = EquipmentSystem._default_create_item(item_def, owner)

        self.assertEqual(item["category"], "weapon")
        self.assertEqual(item["weapon_type"], "ranged")
        self.assertEqual(item["ammo_type"], "rifle_rounds")
        self.assertEqual(item["ammo_per_shot"], 1)
        self.assertEqual(item["magazine_size"], 30)
        self.assertIsNone(item["effect"])
        self.assertEqual(item["max_stack"], 99)
        self.assertEqual(item["weight"], 8.0)
        self.assertIn(item, owner._inventory)

    def test_factory_copies_effect_dict(self):
        item_def = ItemDef(
            key="medkit",
            name="Medkit",
            category="consumable",
            effect={"type": "heal", "amount": 30},
            max_stack=10,
            weight=1.5,
        )

        class _Owner:
            def __init__(self):
                self._inventory = []

        item = EquipmentSystem._default_create_item(item_def, _Owner())
        self.assertEqual(item["effect"], {"type": "heal", "amount": 30})
        # ensure it's a copy, not the same dict object
        self.assertIsNot(item["effect"], item_def.effect)
        self.assertEqual(item["max_stack"], 10)
        self.assertEqual(item["weight"], 1.5)


if __name__ == "__main__":
    unittest.main()
