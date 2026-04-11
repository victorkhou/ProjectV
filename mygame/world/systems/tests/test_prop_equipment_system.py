"""
Property-based tests for EquipmentSystem.

Property 25: Equipment production per tick

Validates: Requirements 14.1, 14.2, 14.3, 14.4
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
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeAttributes:
    """Simulates Evennia's Attribute handler."""
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def add(self, key, value):
        self._data[key] = value

class FakePlayer:
    """Lightweight stand-in for CombatCharacter with inventory tracking."""
    def __init__(self, name="TestPlayer"):
        self.key = name
        self._inventory = []

    @property
    def inventory(self):
        return list(self._inventory)

class FakeBuilding:
    """Lightweight stand-in for a Building object."""
    def __init__(self, building_type="AA", owner=None, offline=False):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": owner,
            "offline": offline,
        })
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))

# Sample item definitions
SAMPLE_ITEMS = {
    "combat_knife": ItemDef(
        key="combat_knife", name="Combat Knife", slot="weapon",
        stat_modifiers={"damage": 10, "range": 1},
    ),
    "assault_rifle": ItemDef(
        key="assault_rifle", name="Assault Rifle", slot="weapon",
        stat_modifiers={"damage": 25, "range": 3},
        ammo_cost={"Iron": 1},
    ),
    "kevlar_vest": ItemDef(
        key="kevlar_vest", name="Kevlar Vest", slot="armor",
        stat_modifiers={"damage_reduction": 5},
    ),
}

SAMPLE_PRODUCTION_MAP = {
    "AA": ["combat_knife", "assault_rifle"],
    "AR": ["kevlar_vest"],
}

SAMPLE_BUILDING_DEFS = {
    "AA": BuildingDef(
        name="Armory", abbreviation="AA",
        cost={"Iron": 50}, max_health=200,
        requires_hq=True, required_terrain=None,
        category="equipment", produces=None,
    ),
    "AR": BuildingDef(
        name="Armorer", abbreviation="AR",
        cost={"Iron": 50}, max_health=200,
        requires_hq=True, required_terrain=None,
        category="equipment", produces=None,
    ),
}

def _make_registry():
    """Create a DataRegistry with test definitions."""
    registry = DataRegistry()
    registry.items = dict(SAMPLE_ITEMS)
    registry.item_production_map = dict(SAMPLE_PRODUCTION_MAP)
    registry.buildings = dict(SAMPLE_BUILDING_DEFS)
    registry.balance = BalanceConfig()
    return registry

def _make_system(registry=None, event_bus=None, create_item_func=None):
    """Create an EquipmentSystem with optional overrides."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    return EquipmentSystem(registry, event_bus, create_item_func=create_item_func), event_bus

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def equipment_building_strategy(draw):
    """Generate an equipment building type (AA or AR)."""
    return draw(st.sampled_from(["AA", "AR"]))

@st.composite
def tick_count_strategy(draw):
    """Generate a number of ticks to process (1-10)."""
    return draw(st.integers(min_value=1, max_value=10))

# -------------------------------------------------------------- #
#  Property 25: Equipment production per tick
#  **Validates: Requirements 14.1, 14.2, 14.3, 14.4**
# -------------------------------------------------------------- #

class TestProperty25EquipmentProduction(unittest.TestCase):
    """Property 25: Equipment production per tick.

    For any active equipment building (Armory or Armorer) owned by an
    online player, exactly one GameItem from the building's production_map
    item list SHALL be created and added to the owner's inventory per
    game tick, with the GameItem's slot and stat_modifiers matching its
    item definition.

    **Validates: Requirements 14.1, 14.2, 14.3, 14.4**
    """

    @given(building_type=equipment_building_strategy())
    @settings(max_examples=100)
    def test_one_item_produced_per_tick(self, building_type):
        """Exactly one item is produced per tick per equipment building."""
        player = FakePlayer()
        items_created = []

        def track_create(item_def, owner):
            item = {
                "key": item_def.key,
                "slot": item_def.slot,
                "stat_modifiers": dict(item_def.stat_modifiers),
            }
            items_created.append(item)
            owner._inventory.append(item)
            return item

        system, _ = _make_system(create_item_func=track_create)
        building = FakeBuilding(building_type=building_type, owner=player)

        system.process_production([building])

        self.assertEqual(
            len(items_created), 1,
            f"Expected exactly 1 item produced, got {len(items_created)}",
        )

    @given(
        building_type=equipment_building_strategy(),
        num_ticks=tick_count_strategy(),
    )
    @settings(max_examples=100)
    def test_items_accumulate_over_ticks(self, building_type, num_ticks):
        """Items accumulate in inventory over multiple ticks."""
        player = FakePlayer()
        items_created = []

        def track_create(item_def, owner):
            item = {
                "key": item_def.key,
                "slot": item_def.slot,
                "stat_modifiers": dict(item_def.stat_modifiers),
            }
            items_created.append(item)
            owner._inventory.append(item)
            return item

        system, _ = _make_system(create_item_func=track_create)
        building = FakeBuilding(building_type=building_type, owner=player)

        for _ in range(num_ticks):
            system.process_production([building])

        self.assertEqual(
            len(items_created), num_ticks,
            f"Expected {num_ticks} items after {num_ticks} ticks",
        )
        self.assertEqual(len(player.inventory), num_ticks)

    @given(building_type=equipment_building_strategy())
    @settings(max_examples=100)
    def test_produced_item_matches_definition(self, building_type):
        """Produced item's slot and stat_modifiers match its item definition."""
        player = FakePlayer()
        items_created = []

        def track_create(item_def, owner):
            item = {
                "key": item_def.key,
                "slot": item_def.slot,
                "stat_modifiers": dict(item_def.stat_modifiers),
            }
            items_created.append((item, item_def))
            owner._inventory.append(item)
            return item

        system, _ = _make_system(create_item_func=track_create)
        building = FakeBuilding(building_type=building_type, owner=player)

        system.process_production([building])

        self.assertEqual(len(items_created), 1)
        item, item_def = items_created[0]

        # Slot must match
        self.assertEqual(item["slot"], item_def.slot)

        # stat_modifiers must match
        self.assertEqual(item["stat_modifiers"], item_def.stat_modifiers)

        # Item key must be from the building's production map
        valid_keys = SAMPLE_PRODUCTION_MAP[building_type]
        self.assertIn(item["key"], valid_keys)

    @given(building_type=equipment_building_strategy())
    @settings(max_examples=100)
    def test_produced_item_has_correct_slot_for_building(self, building_type):
        """Armory produces weapon-slot items, Armorer produces armor-slot items."""
        player = FakePlayer()
        items_created = []

        def track_create(item_def, owner):
            item = {
                "key": item_def.key,
                "slot": item_def.slot,
                "stat_modifiers": dict(item_def.stat_modifiers),
            }
            items_created.append(item)
            owner._inventory.append(item)
            return item

        system, _ = _make_system(create_item_func=track_create)
        building = FakeBuilding(building_type=building_type, owner=player)

        system.process_production([building])

        item = items_created[0]
        if building_type == "AA":
            self.assertEqual(item["slot"], "weapon")
        elif building_type == "AR":
            self.assertEqual(item["slot"], "armor")

    @given(building_type=equipment_building_strategy())
    @settings(max_examples=100)
    def test_offline_building_produces_nothing(self, building_type):
        """Offline equipment buildings do not produce items."""
        player = FakePlayer()
        items_created = []

        def track_create(item_def, owner):
            item = {"key": item_def.key}
            items_created.append(item)
            owner._inventory.append(item)
            return item

        system, _ = _make_system(create_item_func=track_create)
        building = FakeBuilding(
            building_type=building_type, owner=player, offline=True,
        )

        system.process_production([building])

        self.assertEqual(len(items_created), 0)
        self.assertEqual(len(player.inventory), 0)

if __name__ == "__main__":
    unittest.main()
