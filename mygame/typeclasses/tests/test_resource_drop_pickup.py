"""
Unit tests for ResourceDrop.at_get inflow routing (task 9.3).

Drop pickup must flow through the Equipment_System choke point
(``add_resource_capped``) so the carry-weight cap applies and any
un-carryable remainder spills back to a ground drop (Req 16.7). When
the equipment system is unavailable, pickup falls back to a direct
``add_resource`` so it never hard-breaks.

Requirements: 16.7
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

# A prior conftest/test may have registered an `evennia.utils` stub without a
# `delay` symbol; at_get imports `delay` at call time, so ensure it exists.
import evennia.utils as _evennia_utils  # noqa: E402

if not hasattr(_evennia_utils, "delay"):
    _evennia_utils.delay = lambda *a, **kw: None

from mygame.typeclasses.objects import ResourceDrop  # noqa: E402


# -------------------------------------------------------------- #
#  Helpers
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
    """Minimal player-ish holder with a resource pool."""

    def __init__(self, systems=None):
        self._resources = {}
        self.ndb = _NDB()
        if systems:
            _install_systems(systems)
        self.messages = []

    def add_resource(self, rtype, amt):
        self._resources[rtype] = self._resources.get(rtype, 0) + amt

    def get_resource(self, rtype):
        return self._resources.get(rtype, 0)

    def msg(self, text=None, **kwargs):
        if text is not None:
            self.messages.append(text)


class FakeEquipmentSystem:
    """Records capped-inflow calls and honours a per-call cap."""

    def __init__(self, cap=None):
        # cap = max units the holder can actually take (None = unlimited)
        self.cap = cap
        self.calls = []

    def add_resource_capped(self, holder, resource, amount):
        self.calls.append((holder, resource, amount))
        added = amount if self.cap is None else min(amount, self.cap)
        if added > 0:
            holder.add_resource(resource, added)
        return added


def _make_drop(rtype, amount):
    drop = ResourceDrop.__new__(ResourceDrop)
    drop._attr_store = _AttrStore()
    drop.attributes = drop._attr_store
    drop.db = _DbProxy(drop._attr_store)
    drop.db.resource_type = rtype
    drop.db.amount = amount
    drop._deleted = False

    def _delete():
        drop._deleted = True

    drop.delete = _delete
    return drop


# -------------------------------------------------------------- #
#  Tests
# -------------------------------------------------------------- #

class TestResourceDropRouting(unittest.TestCase):

    def test_routes_through_equipment_system(self):
        """Pickup is funneled through add_resource_capped when available."""
        eq = FakeEquipmentSystem(cap=None)
        getter = FakeGetter(systems={"equipment_system": eq})
        drop = _make_drop("Wood", 7)

        drop.at_get(getter)

        self.assertEqual(eq.calls, [(getter, "Wood", 7)])
        self.assertEqual(getter.get_resource("Wood"), 7)
        self.assertEqual(drop.db.amount, 0)

    def test_message_reflects_actually_added_amount(self):
        """When the cap reduces intake, the message shows the added amount."""
        eq = FakeEquipmentSystem(cap=3)
        getter = FakeGetter(systems={"equipment_system": eq})
        drop = _make_drop("Iron", 10)

        drop.at_get(getter)

        # Only 3 of 10 fit; message reflects the actually-added amount.
        self.assertEqual(getter.get_resource("Iron"), 3)
        self.assertTrue(
            any("Picked up 3 Iron (total: 3)." == m for m in getter.messages),
            getter.messages,
        )

    def test_no_message_when_nothing_added(self):
        """A fully over-cap pickup adds nothing and shows no pickup line."""
        eq = FakeEquipmentSystem(cap=0)
        getter = FakeGetter(systems={"equipment_system": eq})
        drop = _make_drop("Stone", 5)

        drop.at_get(getter)

        self.assertEqual(getter.get_resource("Stone"), 0)
        self.assertFalse(any("Picked up" in m for m in getter.messages))
        # Drop is still consumed/cleared; the system handles the spill.
        self.assertEqual(drop.db.amount, 0)

    def test_fallback_direct_add_when_no_system(self):
        """Without an equipment system, pickup falls back to a direct add."""
        # Non-empty systems dict lacking equipment_system → get_system returns
        # None deterministically (no global game_init fallback lookup).
        getter = FakeGetter(systems={"resource_system": object()})
        drop = _make_drop("Wood", 4)

        drop.at_get(getter)

        self.assertEqual(getter.get_resource("Wood"), 4)
        self.assertTrue(
            any("Picked up 4 Wood (total: 4)." == m for m in getter.messages),
            getter.messages,
        )
        self.assertEqual(drop.db.amount, 0)


if __name__ == "__main__":
    unittest.main()
