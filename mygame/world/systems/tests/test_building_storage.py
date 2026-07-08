"""
Unit tests for the Storage_Building resource pool (world.systems.building_storage).

Covers the capacity-bounded pool helpers added in task 9.4:
- deposit caps at remaining capacity (returns amount actually stored),
- withdraw caps at available (returns amount actually withdrawn),
- remaining-capacity math (capacity - total stored, floored at 0),
- the pool is a plain dict, distinct from any Spend_Pool.

Requirements: 16.1
"""

import unittest

from mygame.world.systems import building_storage as bs


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Db:
    """A bare attribute namespace standing in for Evennia's ``obj.db``."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeBuilding:
    """Lightweight stand-in for a Building object.

    Exposes only ``db`` (no ``attributes``) so ``world.utils.get_obj_attr`` /
    ``set_obj_attr`` exercise the ``db`` access path.
    """

    def __init__(self, building_type="VT"):
        self.db = _Db(building_type=building_type)


class _CapDef:
    """A minimal BuildingDef-like object exposing ``storage_capacity``."""

    def __init__(self, storage_capacity):
        self.storage_capacity = storage_capacity


class FakeProvider:
    """In-memory DefinitionsProvider stand-in resolving building types."""

    def __init__(self, buildings):
        self._buildings = buildings

    def resolve_building(self, building_type):
        return self._buildings.get(building_type)


def _provider(capacity):
    return FakeProvider({"VT": _CapDef(capacity)})


# -------------------------------------------------------------- #
#  Capacity resolution
# -------------------------------------------------------------- #

class TestCapacityResolution(unittest.TestCase):
    def test_resolves_capacity_from_def(self):
        b = FakeBuilding()
        self.assertEqual(bs.get_storage_capacity(b, provider=_provider(500)), 500)

    def test_unknown_type_is_zero(self):
        b = FakeBuilding(building_type="ZZ")
        self.assertEqual(bs.get_storage_capacity(b, provider=_provider(500)), 0)

    def test_no_building_type_is_zero(self):
        b = FakeBuilding(building_type=None)
        self.assertEqual(bs.get_storage_capacity(b, provider=_provider(500)), 0)

    def test_negative_capacity_clamped_to_zero(self):
        b = FakeBuilding()
        self.assertEqual(bs.get_storage_capacity(b, provider=_provider(-10)), 0)


# -------------------------------------------------------------- #
#  Remaining-capacity math
# -------------------------------------------------------------- #

class TestRemainingCapacity(unittest.TestCase):
    def test_empty_pool_full_room(self):
        b = FakeBuilding()
        self.assertEqual(bs.get_remaining_capacity(b, provider=_provider(100)), 100)

    def test_remaining_is_capacity_minus_total(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 30, provider=_provider(100))
        bs.deposit_to_building(b, "Stone", 20, provider=_provider(100))
        self.assertEqual(bs.get_total_stored(b), 50)
        self.assertEqual(bs.get_remaining_capacity(b, provider=_provider(100)), 50)

    def test_zero_capacity_means_no_storage(self):
        b = FakeBuilding()
        self.assertEqual(bs.get_remaining_capacity(b, provider=_provider(0)), 0)
        self.assertEqual(bs.deposit_to_building(b, "Wood", 5, provider=_provider(0)), 0)


# -------------------------------------------------------------- #
#  Deposit
# -------------------------------------------------------------- #

class TestDeposit(unittest.TestCase):
    def test_deposit_within_capacity_stores_all(self):
        b = FakeBuilding()
        stored = bs.deposit_to_building(b, "Wood", 40, provider=_provider(100))
        self.assertEqual(stored, 40)
        self.assertEqual(bs.get_stored(b, "Wood"), 40)

    def test_deposit_caps_at_remaining_capacity(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 80, provider=_provider(100))
        # Only 20 room left; depositing 50 stores 20.
        stored = bs.deposit_to_building(b, "Stone", 50, provider=_provider(100))
        self.assertEqual(stored, 20)
        self.assertEqual(bs.get_total_stored(b), 100)
        self.assertEqual(bs.get_stored(b, "Stone"), 20)

    def test_deposit_when_full_stores_nothing(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 100, provider=_provider(100))
        stored = bs.deposit_to_building(b, "Wood", 10, provider=_provider(100))
        self.assertEqual(stored, 0)
        self.assertEqual(bs.get_total_stored(b), 100)

    def test_deposit_accumulates_same_resource(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Iron", 10, provider=_provider(100))
        bs.deposit_to_building(b, "Iron", 15, provider=_provider(100))
        self.assertEqual(bs.get_stored(b, "Iron"), 25)

    def test_deposit_non_positive_is_noop(self):
        b = FakeBuilding()
        self.assertEqual(bs.deposit_to_building(b, "Wood", 0, provider=_provider(100)), 0)
        self.assertEqual(bs.deposit_to_building(b, "Wood", -5, provider=_provider(100)), 0)
        self.assertEqual(bs.get_total_stored(b), 0)

    def test_total_never_exceeds_capacity(self):
        b = FakeBuilding()
        for _ in range(10):
            bs.deposit_to_building(b, "Wood", 30, provider=_provider(100))
        self.assertLessEqual(bs.get_total_stored(b), 100)
        self.assertEqual(bs.get_total_stored(b), 100)


# -------------------------------------------------------------- #
#  Withdraw
# -------------------------------------------------------------- #

class TestWithdraw(unittest.TestCase):
    def test_withdraw_within_available_takes_all(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 40, provider=_provider(100))
        taken = bs.withdraw_from_building(b, "Wood", 25)
        self.assertEqual(taken, 25)
        self.assertEqual(bs.get_stored(b, "Wood"), 15)

    def test_withdraw_caps_at_available(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 40, provider=_provider(100))
        taken = bs.withdraw_from_building(b, "Wood", 100)
        self.assertEqual(taken, 40)
        self.assertEqual(bs.get_stored(b, "Wood"), 0)

    def test_withdraw_drains_key_from_pool(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 40, provider=_provider(100))
        bs.withdraw_from_building(b, "Wood", 40)
        self.assertNotIn("Wood", bs.get_stored_pool(b))

    def test_withdraw_missing_resource_is_zero(self):
        b = FakeBuilding()
        self.assertEqual(bs.withdraw_from_building(b, "Stone", 10), 0)

    def test_withdraw_non_positive_is_noop(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 40, provider=_provider(100))
        self.assertEqual(bs.withdraw_from_building(b, "Wood", 0), 0)
        self.assertEqual(bs.withdraw_from_building(b, "Wood", -5), 0)
        self.assertEqual(bs.get_stored(b, "Wood"), 40)

    def test_deposit_withdraw_round_trip_conserves(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 60, provider=_provider(100))
        taken = bs.withdraw_from_building(b, "Wood", 60)
        self.assertEqual(taken, 60)
        self.assertEqual(bs.get_total_stored(b), 0)


# -------------------------------------------------------------- #
#  Pool isolation
# -------------------------------------------------------------- #

class TestPoolIsolation(unittest.TestCase):
    def test_get_stored_pool_returns_copy(self):
        b = FakeBuilding()
        bs.deposit_to_building(b, "Wood", 10, provider=_provider(100))
        snapshot = bs.get_stored_pool(b)
        snapshot["Wood"] = 999
        snapshot["Gold"] = 500
        # Mutating the snapshot must not affect the building's pool.
        self.assertEqual(bs.get_stored(b, "Wood"), 10)
        self.assertNotIn("Gold", bs.get_stored_pool(b))

    def test_empty_building_has_empty_pool(self):
        b = FakeBuilding()
        self.assertEqual(bs.get_stored_pool(b), {})
        self.assertEqual(bs.get_total_stored(b), 0)


if __name__ == "__main__":
    unittest.main()
