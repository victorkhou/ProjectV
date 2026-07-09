"""
Property-based tests for EquipmentSystem production.

Property 25: Equipment production per tick
    Validates: Requirements 14.1, 14.2, 14.3, 14.4

Property 5: Category -> storage (gear => slots, supply => bag; no crossover)
    Validates: Requirements 3.2, 3.3
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

from mygame.world.constants import (  # noqa: E402
    GEAR_CATEGORIES,
    SUPPLY_CATEGORIES,
)
from mygame.world.systems.equipment_system import EquipmentSystem  # noqa: E402
from mygame.world.systems.equipment_handler import EquipmentHandler  # noqa: E402
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
    """Lightweight stand-in for CombatCharacter.

    Holds Gear as Game_Item objects in ``_inventory`` (the default item
    factory appends here) and Supplies as counted stacks in a real
    ``EquipmentHandler`` Supply_Bag (the handler falls back to a plain
    ``_supplies`` dict in this stubbed environment).
    """
    def __init__(self, name="TestPlayer", resources=None):
        self.key = name
        self._inventory = []
        self.equipment = EquipmentHandler(self)
        # Plentiful stockpile by default so resource-charged production paths
        # (which now spend craft_cost) aren't starved in these routing props.
        self._resources = dict(resources or {
            r: 1000000 for r in
            ("Wood", "Stone", "Iron", "Energy", "Circuits", "Nexium")
        })

    @property
    def inventory(self):
        return list(self._inventory)

    def get_buildings(self):
        # Owns a completed HQ so passive production isn't blocked by the
        # base-deactivation gate (production stops with no active HQ).
        return [type("_HQ", (), {
            "db": type("_D", (), {"building_type": "HQ",
                                  "under_construction": False})(),
            "location": None,
        })()]

    def get_resource(self, resource):
        return int(self._resources.get(str(resource).title(), 0))

    def add_resource(self, resource, amount):
        key = str(resource).title()
        self._resources[key] = self._resources.get(key, 0) + int(amount)

    def has_resources(self, costs):
        return all(
            self._resources.get(str(r).title(), 0) >= amt
            for r, amt in costs.items()
        )

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, amt in costs.items():
            key = str(r).title()
            self._resources[key] = self._resources.get(key, 0) - int(amt)
        return True

class FakeBuilding:
    """Lightweight stand-in for a Building object."""
    def __init__(self, building_type="AR", owner=None, offline=False,
                 assigned_agent="engineer"):
        self.key = building_type
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "owner": owner,
            "offline": offline,
            "assigned_agent": assigned_agent,
        })
        self._owner = owner

    @property
    def owner(self):
        return self._owner

    @property
    def is_offline(self):
        return bool(self.attributes.get("offline", default=False))

# Sample item definitions spanning both storage kinds:
#   Gear  (armor/weapon/accessory)  -> unique Game_Item slot objects
#   Supply (ammo/consumable/throwable) -> counted Supply_Bag stacks
SAMPLE_ITEMS = {
    # Gear
    "combat_knife": ItemDef(
        key="combat_knife", name="Combat Knife", slot="weapon",
        category="weapon", weapon_type="melee",
        stat_modifiers={"damage": 10, "range": 1}, craft_cost={"Iron": 5},
    ),
    "assault_rifle": ItemDef(
        key="assault_rifle", name="Assault Rifle", slot="weapon",
        category="weapon", weapon_type="ranged", ammo_type="rifle_rounds",
        magazine_size=30, stat_modifiers={"damage": 25, "range": 3},
        craft_cost={"Iron": 25},
    ),
    "kevlar_vest": ItemDef(
        key="kevlar_vest", name="Kevlar Vest", slot="torso",
        category="armor", stat_modifiers={"damage_reduction": 5},
        craft_cost={"Iron": 20},
    ),
    # Supply
    "rifle_rounds": ItemDef(
        key="rifle_rounds", name="Rifle Rounds", slot="", category="ammo",
        weight=0.1, max_stack=200, craft_cost={"Iron": 2},
    ),
    "medkit": ItemDef(
        key="medkit", name="Medkit", slot="", category="consumable",
        effect={"type": "heal", "amount": 30}, weight=5.0, max_stack=10,
        craft_cost={"Wood": 5},
    ),
    "frag_grenade": ItemDef(
        key="frag_grenade", name="Frag Grenade", slot="", category="throwable",
        effect={"type": "aoe_damage", "amount": 40, "radius": 2, "range": 6},
        weight=3.0, max_stack=10, craft_cost={"Iron": 10},
    ),
}

# Mirrors items.yaml: AR = weapons/ammo/modern gear, MB = consumables,
# LB = futuristic gear + throwables.
SAMPLE_PRODUCTION_MAP = {
    "AR": ["combat_knife", "assault_rifle", "kevlar_vest", "rifle_rounds"],
    "MB": ["medkit"],
    "LB": ["kevlar_vest", "frag_grenade"],
}

SAMPLE_BUILDING_DEFS = {
    "AR": BuildingDef(
        name="Armory", abbreviation="AR",
        cost={"Iron": 50}, max_health=200,
        requires_hq=True, required_terrain=None,
        category="equipment", produces=None,
    ),
    "MB": BuildingDef(
        name="Medbay", abbreviation="MB",
        cost={"Wood": 15}, max_health=200,
        requires_hq=True, required_terrain=None,
        category="medical", produces=None,
    ),
    "LB": BuildingDef(
        name="Lab", abbreviation="LB",
        cost={"Wood": 25}, max_health=200,
        requires_hq=True, required_terrain=None,
        category="research", produces=None,
    ),
    # HQ so a production owner passes the base-deactivation gate.
    "HQ": BuildingDef(
        name="Headquarters", abbreviation="HQ",
        cost={"Wood": 10}, max_health=500,
        requires_hq=False, required_terrain=None,
        category="headquarters", produces=None,
        capabilities=frozenset({"headquarters"}),
    ),
}

def _make_registry():
    """Create a DataRegistry with test definitions.

    Production is set to yield every tick (``equipment_production_ticks=1``)
    so these routing/accumulation properties exercise one item per call; the
    cooldown gate itself is covered by dedicated tests.
    """
    registry = DataRegistry()
    registry.items = dict(SAMPLE_ITEMS)
    registry.item_production_map = dict(SAMPLE_PRODUCTION_MAP)
    registry.buildings = dict(SAMPLE_BUILDING_DEFS)
    registry.balance = BalanceConfig(equipment_production_ticks=1)
    return registry

def _make_system(registry=None, event_bus=None, create_item_func=None):
    """Create an EquipmentSystem with optional overrides."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    return EquipmentSystem(
        registry, event_bus, create_item_func=create_item_func
    ), event_bus


def _total_stored(player):
    """Total units produced into *player*'s stores (gear objects + supplies)."""
    return len(player.inventory) + sum(player.equipment.get_supplies().values())


# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def production_building_strategy(draw):
    """Generate a production building type (AR, MB, or LB)."""
    return draw(st.sampled_from(["AR", "MB", "LB"]))

@st.composite
def tick_count_strategy(draw):
    """Generate a number of ticks to process (1-10)."""
    return draw(st.integers(min_value=1, max_value=10))

# -------------------------------------------------------------- #
#  Property 25: Equipment production per tick
#  **Validates: Requirements 14.1, 14.2, 14.3, 14.4**
# -------------------------------------------------------------- #

class TestProperty25EquipmentProduction(unittest.TestCase):
    """Property 25: item production per tick.

    For any active production building (Armory/Medbay/Lab) owned by an online
    player, exactly one item from the building's ``production_map`` list SHALL
    be produced per game tick and routed into the owner's stores (a Game_Item
    for Gear, a counted Supply_Bag unit for Supplies).

    **Validates: Requirements 14.1, 14.2, 14.3, 14.4**
    """

    @given(building_type=production_building_strategy())
    @settings(max_examples=100)
    def test_one_item_produced_per_tick(self, building_type):
        """Exactly one unit is produced per tick per production building."""
        player = FakePlayer()
        system, _ = _make_system()
        building = FakeBuilding(building_type=building_type, owner=player)

        system.process_production([building])

        self.assertEqual(
            _total_stored(player), 1,
            f"Expected exactly 1 unit produced, got {_total_stored(player)}",
        )

    @given(
        building_type=production_building_strategy(),
        num_ticks=tick_count_strategy(),
    )
    @settings(max_examples=100)
    def test_items_accumulate_over_ticks(self, building_type, num_ticks):
        """Produced units accumulate in the owner's stores over ticks."""
        player = FakePlayer()
        system, _ = _make_system()
        building = FakeBuilding(building_type=building_type, owner=player)

        for _ in range(num_ticks):
            system.process_production([building])

        self.assertEqual(
            _total_stored(player), num_ticks,
            f"Expected {num_ticks} units after {num_ticks} ticks",
        )

    @given(building_type=production_building_strategy())
    @settings(max_examples=100)
    def test_produced_item_is_from_building_production_map(self, building_type):
        """The produced item key is one of the building's producible items."""
        player = FakePlayer()
        system, _ = _make_system()
        building = FakeBuilding(building_type=building_type, owner=player)

        system.process_production([building])

        valid_keys = set(SAMPLE_PRODUCTION_MAP[building_type])
        produced_keys = {i["key"] for i in player.inventory}
        produced_keys |= set(player.equipment.get_supplies().keys())
        self.assertEqual(len(produced_keys), 1)
        self.assertTrue(produced_keys <= valid_keys)

    @given(building_type=production_building_strategy())
    @settings(max_examples=100)
    def test_offline_building_produces_nothing(self, building_type):
        """Offline production buildings do not produce items."""
        player = FakePlayer()
        system, _ = _make_system()
        building = FakeBuilding(
            building_type=building_type, owner=player, offline=True,
        )

        system.process_production([building])

        self.assertEqual(_total_stored(player), 0)


# -------------------------------------------------------------- #
#  Property 5: Category -> storage (gear => slots, supply => bag)
#  **Validates: Requirements 3.2, 3.3**
# -------------------------------------------------------------- #

class TestProperty5CategoryStorage(unittest.TestCase):
    """Property 5: a produced item is routed to storage by its category.

    Gear (``armor``/``weapon``/``accessory``) becomes a unique Game_Item slot
    object (created via the item factory, landing in the inventory); Supply
    (``ammo``/``consumable``/``throwable``) becomes a counted Supply_Bag stack.
    There is no crossover: gear never lands in the Supply_Bag, and a supply
    never becomes a Game_Item object.

    **Validates: Requirements 3.2, 3.3**
    """

    @given(item_key=st.sampled_from(sorted(SAMPLE_ITEMS.keys())))
    @settings(max_examples=100)
    def test_produced_item_routes_by_category_with_no_crossover(self, item_key):
        """Any produced item lands in exactly its category's store."""
        player = FakePlayer()
        system, _ = _make_system()
        item_def = SAMPLE_ITEMS[item_key]

        routed = system._route_produced_item(item_def, player)
        self.assertTrue(routed)

        bag = player.equipment.get_supplies()
        inv_keys = [i["key"] for i in player.inventory]

        if item_def.category in SUPPLY_CATEGORIES:
            # Supply => counted stack in the bag, NOT a slot object.
            self.assertEqual(bag.get(item_key, 0), 1)
            self.assertEqual(
                player.inventory, [],
                "supply must never become a Game_Item object",
            )
        else:
            self.assertIn(item_def.category, GEAR_CATEGORIES)
            # Gear => a unique Game_Item object, NOT a bag count.
            self.assertEqual(inv_keys, [item_key])
            self.assertEqual(
                bag, {},
                "gear must never land in the Supply_Bag",
            )

    def test_supply_building_fills_bag_only(self):
        """A Medbay (supply-only) tick fills the bag and creates no objects."""
        player = FakePlayer()
        system, _ = _make_system()
        building = FakeBuilding(building_type="MB", owner=player)

        for _ in range(5):
            system.process_production([building])

        # All five produced units are counted medkits; no Game_Item objects.
        self.assertEqual(player.equipment.get_supply("medkit"), 5)
        self.assertEqual(player.inventory, [])

    def test_gear_building_creates_objects_only(self):
        """A gear-only production run creates objects and never touches the bag."""
        player = FakePlayer()
        system, _ = _make_system()
        # A registry whose AR list is gear-only makes the run deterministic.
        system.registry.item_production_map["AR"] = ["combat_knife"]
        building = FakeBuilding(building_type="AR", owner=player)

        for _ in range(3):
            system.process_production([building])

        self.assertEqual(len(player.inventory), 3)
        self.assertEqual(player.equipment.get_supplies(), {})

    def test_supply_without_equipment_handler_produces_nothing(self):
        """A supply produced for an owner with no handler is safely dropped."""
        system, _ = _make_system()

        class NoHandlerOwner:
            key = "NoHandler"

        owner = NoHandlerOwner()
        routed = system._route_produced_item(SAMPLE_ITEMS["medkit"], owner)
        self.assertFalse(routed)


if __name__ == "__main__":
    unittest.main()
