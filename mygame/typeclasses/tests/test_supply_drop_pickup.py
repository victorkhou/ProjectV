"""
Unit tests for GameItem.at_get supply-drop routing (Req 10.5).

A counted Supply drop (a ``GameItem`` carrying ``db.count`` + ``db.item_key``,
spawned by ``spawn_supply_drop`` when an over-capacity pickup spills) must, on
pickup, route its units into the getter's Supply_Bag through the
``EquipmentSystem.add_supply_drop`` choke point (weight/stack-capped, with any
remainder respawned as a fresh ground drop) and then consume the drop object.
A plain equippable Gear GameItem (no ``db.count``) is unaffected — pickup just
clears its coordinates.

Requirements: 10.5
"""

import sys
import types
import unittest

import pytest

from world import services


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
    # at_get schedules deletion via evennia.utils.delay — stub as a no-op.
    _mod("evennia.utils", {"delay": lambda *a, **kw: None})
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

# ``at_get`` schedules deletion via ``evennia.utils.delay(0, self.delete)``.
# Make the stub run the callback synchronously so deletion actually happens
# in-test (a real Evennia delay(0, ...) fires on the next reactor pass).
import evennia.utils as _evennia_utils  # noqa: E402


def _run_delay(_seconds=0, callback=None, *args, **kwargs):
    if callable(callback):
        return callback(*args, **kwargs)
    return None


_evennia_utils.delay = _run_delay

from mygame.typeclasses.objects import GameItem  # noqa: E402


# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

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


class _NDB:
    def __init__(self, systems=None):
        self.systems = systems or {}


@pytest.fixture(autouse=True)
def _services_sandbox():
    """Give every test a private, empty facade state, restored on exit."""
    with services.override({}):
        yield


def _install_systems(systems):
    """Register fake *systems* for the current test through the facade."""
    services.get_systems().update(systems)


class FakeGetter:
    """Minimal player-ish holder with a Supply_Bag-like counter."""

    def __init__(self, systems=None):
        self._bag = {}
        self.ndb = _NDB()
        if systems:
            _install_systems(systems)
        self.messages = []

    def msg(self, text=None, **kwargs):
        if text is not None:
            self.messages.append(text)


class FakeEquipmentSystem:
    """Records add_supply_drop calls and honours a per-call cap.

    Mirrors the real system's contract: adds up to ``cap`` units to the
    getter's bag and returns the amount actually added (the rest is "spilled").
    """

    def __init__(self, cap=None):
        self.cap = cap
        self.calls = []

    def add_supply_drop(self, player, item_key, count):
        self.calls.append((player, item_key, count))
        added = count if self.cap is None else min(count, self.cap)
        if added > 0:
            player._bag[item_key] = player._bag.get(item_key, 0) + added
        return added


def _make_supply_drop(item_key, count, key=None):
    """Build a GameItem supply drop (carries db.item_key + db.count)."""
    drop = GameItem.__new__(GameItem)
    store = _AttrStore()
    drop._attr_store = store
    drop.attributes = store
    drop.db = _DbProxy(store)
    drop.key = key or item_key
    drop.db.item_key = item_key
    drop.db.count = count
    drop.db.coord_x = 5
    drop.db.coord_y = 6
    drop._deleted = False
    drop.delete = lambda: setattr(drop, "_deleted", True)
    return drop


def _make_gear_item(item_key, key=None):
    """Build a plain equippable Gear GameItem (no db.count)."""
    drop = GameItem.__new__(GameItem)
    store = _AttrStore()
    drop._attr_store = store
    drop.attributes = store
    drop.db = _DbProxy(store)
    drop.key = key or item_key
    drop.db.item_key = item_key
    drop.db.coord_x = 5
    drop.db.coord_y = 6
    drop._deleted = False
    drop.delete = lambda: setattr(drop, "_deleted", True)
    return drop


# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestSupplyDropPickup(unittest.TestCase):

    def test_routes_full_count_into_supply_bag(self):
        """A fully-carryable supply drop lands in the bag and is consumed."""
        eq = FakeEquipmentSystem(cap=None)
        getter = FakeGetter(systems={"equipment_system": eq})
        drop = _make_supply_drop("rifle_rounds", 30)

        drop.at_get(getter)

        self.assertEqual(eq.calls, [(getter, "rifle_rounds", 30)])
        self.assertEqual(getter._bag.get("rifle_rounds"), 30)
        # Drop object is consumed (count zeroed, deletion scheduled).
        self.assertEqual(drop.db.count, 0)
        self.assertTrue(drop._deleted)
        self.assertTrue(any("Picked up 30 rifle_rounds" in m for m in getter.messages))

    def test_partial_pickup_adds_capped_amount(self):
        """When only part fits, the added amount is routed and reported."""
        eq = FakeEquipmentSystem(cap=10)
        getter = FakeGetter(systems={"equipment_system": eq})
        drop = _make_supply_drop("medkit", 25)

        drop.at_get(getter)

        # Only 10 of 25 fit; the system handles spilling the remainder.
        self.assertEqual(getter._bag.get("medkit"), 10)
        self.assertTrue(drop._deleted)
        self.assertTrue(any("Picked up 10 medkit" in m for m in getter.messages))

    def test_nothing_added_still_consumes_and_no_message(self):
        """A fully over-cap pickup adds nothing, shows no 'picked up' line."""
        eq = FakeEquipmentSystem(cap=0)
        getter = FakeGetter(systems={"equipment_system": eq})
        drop = _make_supply_drop("frag_grenade", 5)

        drop.at_get(getter)

        self.assertEqual(getter._bag.get("frag_grenade"), None)
        self.assertTrue(drop._deleted)
        self.assertFalse(any("Picked up" in m for m in getter.messages))

    def test_fallback_leaves_object_when_no_system(self):
        """Without an equipment system, the drop is not consumed (fallback)."""
        # Non-empty systems dict lacking equipment_system → get_system → None.
        getter = FakeGetter(systems={"resource_system": object()})
        drop = _make_supply_drop("rifle_rounds", 30)

        drop.at_get(getter)

        # No routing; object survives as an inventory item, coords cleared.
        self.assertFalse(drop._deleted)
        self.assertEqual(drop.db.count, 30)
        self.assertIsNone(drop.db.coord_x)
        self.assertIsNone(drop.db.coord_y)
        self.assertEqual(getter._bag, {})

    def test_plain_gear_item_is_not_routed(self):
        """A Gear GameItem (no db.count) just has its coordinates cleared."""
        eq = FakeEquipmentSystem(cap=None)
        getter = FakeGetter(systems={"equipment_system": eq})
        item = _make_gear_item("kevlar_vest")

        item.at_get(getter)

        self.assertEqual(eq.calls, [])
        self.assertFalse(item._deleted)
        self.assertIsNone(item.db.coord_x)
        self.assertIsNone(item.db.coord_y)


if __name__ == "__main__":
    unittest.main()
