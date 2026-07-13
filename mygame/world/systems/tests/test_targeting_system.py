"""
Unit tests for TargetingSystem — ranged lock-on acquire, upkeep, and queries.
"""

import types
import unittest

from world.data_registry import DataRegistry
from world.definitions import BalanceConfig
from world.event_bus import EventBus, PLAYER_NOTIFICATION
from world.systems.targeting_system import TargetingSystem


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _Weapon:
    """A weapon Game_Item with a weapon_type + stat modifiers."""
    def __init__(self, weapon_type="ranged", weapon_range=8, **stats):
        self.key = "rifle"
        self.slot = "weapon"
        self.weapon_type = weapon_type
        self.stat_modifiers = {"range": weapon_range, **stats}

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class _Equipment:
    def __init__(self, weapon=None):
        self._weapon = weapon

    def get_equipped(self, slot):
        return self._weapon if slot == "weapon" else None


class _Player:
    def __init__(self, x=0, y=0, planet="earth", weapon=None, oid=None):
        self.key = "Player"
        self.db = types.SimpleNamespace(
            coord_x=x, coord_y=y, coord_planet=planet,
            lock_target=None, lock_progress=0, lock_ready=False,
        )
        self.equipment = _Equipment(weapon)
        if oid is not None:
            self.id = oid


class _Enemy:
    def __init__(self, x=2, y=0, planet="earth", oid=99):
        self.key = "Outpost #1 Guard-1"
        self.db = types.SimpleNamespace(coord_x=x, coord_y=y, coord_planet=planet)
        self.id = oid
        self.pk = oid  # live (not deleted)


class _Sink:
    def __init__(self):
        self.kinds = []

    def __call__(self, event_name=None, player=None, kind=None, data=None, **_):
        self.kinds.append(kind)


def _make(**balance):
    registry = DataRegistry()
    registry.balance = BalanceConfig(**balance)
    bus = EventBus()
    sink = _Sink()
    bus.subscribe(PLAYER_NOTIFICATION, sink)
    return TargetingSystem(registry, bus), sink


# -------------------------------------------------------------- #
#  Ranged-weapon gate
# -------------------------------------------------------------- #

class TestRangedWeaponGate(unittest.TestCase):
    def test_ranged_weapon_returned(self):
        sys, _ = _make()
        p = _Player(weapon=_Weapon(weapon_type="ranged"))
        self.assertIsNotNone(sys.get_ranged_weapon(p))

    def test_melee_weapon_is_not_ranged(self):
        sys, _ = _make()
        p = _Player(weapon=_Weapon(weapon_type="melee"))
        self.assertIsNone(sys.get_ranged_weapon(p))

    def test_no_weapon(self):
        sys, _ = _make()
        self.assertIsNone(sys.get_ranged_weapon(_Player()))

    def test_in_weapon_range(self):
        sys, _ = _make()
        p = _Player(x=0, y=0)
        weapon = _Weapon(weapon_range=4)
        self.assertTrue(sys.in_weapon_range(p, _Enemy(x=3, y=0), weapon))
        self.assertFalse(sys.in_weapon_range(p, _Enemy(x=10, y=0), weapon))


# -------------------------------------------------------------- #
#  Acquire
# -------------------------------------------------------------- #

class TestAcquire(unittest.TestCase):
    def test_acquire_starts_lock(self):
        sys, sink = _make()
        p = _Player(weapon=_Weapon(weapon_range=8))
        enemy = _Enemy(x=3, y=0)
        ok, _ = sys.acquire(p, enemy)
        self.assertTrue(ok)
        self.assertIs(p.db.lock_target, enemy)
        self.assertEqual(p.db.lock_progress, 0)
        self.assertFalse(p.db.lock_ready)
        self.assertIn("targeting", sink.kinds)

    def test_acquire_rejects_without_ranged_weapon(self):
        sys, _ = _make()
        p = _Player(weapon=_Weapon(weapon_type="melee"))
        ok, msg = sys.acquire(p, _Enemy())
        self.assertFalse(ok)
        self.assertIn("ranged weapon", msg)

    def test_acquire_rejects_out_of_range(self):
        sys, _ = _make()
        p = _Player(weapon=_Weapon(weapon_range=4))
        ok, msg = sys.acquire(p, _Enemy(x=10, y=0))
        self.assertFalse(ok)
        self.assertIn("range", msg.lower())

    def test_reacquire_same_target_keeps_progress(self):
        sys, _ = _make()
        p = _Player(weapon=_Weapon(weapon_range=8))
        enemy = _Enemy(x=2, y=0)
        sys.acquire(p, enemy)
        p.db.lock_progress = 2  # simulate mid-lock
        sys.acquire(p, enemy)   # re-target the same enemy
        self.assertEqual(p.db.lock_progress, 2)  # not reset


# -------------------------------------------------------------- #
#  Upkeep: progress, completion, interruption
# -------------------------------------------------------------- #

class TestUpkeep(unittest.TestCase):
    def test_lock_completes_after_lock_ticks(self):
        sys, sink = _make(target_lock_ticks=3)
        p = _Player(weapon=_Weapon(weapon_range=8))
        enemy = _Enemy(x=2, y=0)
        sys.acquire(p, enemy)
        for _ in range(3):
            sys.process_tick(1, [p])
        self.assertTrue(p.db.lock_ready)
        self.assertTrue(sys.is_locked(p))
        self.assertIn("locked", sink.kinds)

    def test_lock_not_ready_before_completion(self):
        sys, _ = _make(target_lock_ticks=3)
        p = _Player(weapon=_Weapon(weapon_range=8))
        sys.acquire(p, _Enemy(x=2, y=0))
        sys.process_tick(1, [p])  # 1 of 3
        self.assertFalse(sys.is_locked(p))

    def test_lock_speed_reduces_lock_time(self):
        sys, _ = _make(target_lock_ticks=5)
        p = _Player(weapon=_Weapon(weapon_range=8, lock_speed=3))
        sys.acquire(p, _Enemy(x=2, y=0))
        for _ in range(2):  # 5 - 3 = 2 ticks
            sys.process_tick(1, [p])
        self.assertTrue(p.db.lock_ready)

    def test_lock_broken_when_target_leaves_range(self):
        sys, sink = _make(target_lock_ticks=2)
        p = _Player(weapon=_Weapon(weapon_range=4))
        enemy = _Enemy(x=2, y=0)
        sys.acquire(p, enemy)
        sys.process_tick(1, [p])
        enemy.db.coord_x = 20  # moved out of range
        sys.process_tick(2, [p])
        self.assertIsNone(p.db.lock_target)
        self.assertIn("lock_lost", sink.kinds)

    def test_lock_broken_when_shooter_changes_planet(self):
        sys, _ = _make()
        p = _Player(weapon=_Weapon(weapon_range=8))
        sys.acquire(p, _Enemy(x=2, y=0))
        p.db.coord_planet = "mars"  # left the area
        sys.process_tick(1, [p])
        self.assertIsNone(p.db.lock_target)

    def test_lock_broken_when_weapon_unequipped(self):
        sys, _ = _make()
        weapon = _Weapon(weapon_range=8)
        p = _Player(weapon=weapon)
        sys.acquire(p, _Enemy(x=2, y=0))
        p.equipment._weapon = None  # unequipped
        sys.process_tick(1, [p])
        self.assertIsNone(p.db.lock_target)

    def test_lock_broken_when_target_deleted(self):
        """A locked target that has been deleted (pk is None — e.g. a guard died
        and its DB row was removed) drops the lock with reason 'target_gone'."""
        sys, sink = _make()
        p = _Player(weapon=_Weapon(weapon_range=8))
        enemy = _Enemy(x=2, y=0)
        sys.acquire(p, enemy)
        enemy.pk = None  # target deleted
        sys.process_tick(1, [p])
        self.assertIsNone(p.db.lock_target)
        self.assertIn("lock_lost", sink.kinds)

    def test_lock_survives_when_target_has_no_coord_planet_but_shares_room(self):
        """An agent/building target carries coords but NOT coord_planet. The lock
        must NOT break on the planet check — _planet falls back to the target's
        room planet, which matches the shooter's. (Regression: a lock onto such a
        target used to break on the first upkeep tick.)"""
        sys, _ = _make(target_lock_ticks=3)
        room = types.SimpleNamespace(planet_name="earth")
        p = _Player(weapon=_Weapon(weapon_range=8))
        p.location = room
        # A building-like target: coords, no coord_planet, same room.
        target = types.SimpleNamespace(
            key="Enemy Turret",
            db=types.SimpleNamespace(coord_x=2, coord_y=0),  # NO coord_planet
            location=room, pk=1,
        )
        sys.acquire(p, target)
        sys.process_tick(1, [p])
        self.assertIs(p.db.lock_target, target,
                      "lock must hold when target's planet resolves via its room")

    def test_upkeep_isolates_errors(self):
        sys, _ = _make()

        class _Boom:
            @property
            def db(self):
                raise RuntimeError("boom")

        good = _Player(weapon=_Weapon(weapon_range=8))
        sys.acquire(good, _Enemy(x=2, y=0))
        # Boom first; good must still advance.
        sys.process_tick(1, [_Boom(), good])
        self.assertEqual(good.db.lock_progress, 1)


# -------------------------------------------------------------- #
#  Accuracy helpers
# -------------------------------------------------------------- #

class TestAccuracyHelpers(unittest.TestCase):
    def test_targeted_accuracy_baseline(self):
        sys, _ = _make(accuracy_targeted=0.8)
        self.assertAlmostEqual(sys.targeted_accuracy(_Weapon()), 0.8)

    def test_directional_accuracy_baseline(self):
        sys, _ = _make(accuracy_directional=0.5)
        self.assertAlmostEqual(sys.directional_accuracy(_Weapon()), 0.5)

    def test_weapon_accuracy_stat_raises_and_clamps(self):
        sys, _ = _make(accuracy_targeted=0.8)
        # +0.5 would exceed 1.0 -> clamped.
        self.assertEqual(sys.targeted_accuracy(_Weapon(accuracy=0.5)), 1.0)

    def test_negative_weapon_accuracy_clamps_at_zero(self):
        """A large negative accuracy modifier can't push the hit chance below 0
        (the low-end clamp of _clamp01)."""
        sys, _ = _make(accuracy_directional=0.5)
        self.assertEqual(sys.directional_accuracy(_Weapon(accuracy=-0.9)), 0.0)

    def test_clamp01_bounds(self):
        from world.systems.targeting_system import _clamp01
        self.assertEqual(_clamp01(-3.0), 0.0)
        self.assertEqual(_clamp01(0.4), 0.4)
        self.assertEqual(_clamp01(9.0), 1.0)


if __name__ == "__main__":
    unittest.main()
