"""
Unit tests for CombatEntity mixin.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""

import unittest


# ------------------------------------------------------------------ #
#  Lightweight host class that provides self.db.* like Evennia does
# ------------------------------------------------------------------ #

class _AttrStore:
    """Minimal Evennia-style attribute store."""
    def __init__(self):
        self._data = {}
    def get(self, key, default=None, **kw):
        return self._data.get(key, default)
    def add(self, key, value, **kw):
        self._data[key] = value
    def has(self, key):
        return key in self._data


class _DbProxy:
    """Minimal proxy mimicking Evennia's db handler."""
    def __init__(self, store):
        object.__setattr__(self, "_store", store)
    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)
    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


from mygame.typeclasses.combat_entity import CombatEntity, DEFAULT_RESPAWN_TICKS


class _Host(CombatEntity):
    """Fake host class providing self.db like Evennia typeclasses."""
    def __init__(self):
        self._attr_store = _AttrStore()
        self.db = _DbProxy(self._attr_store)
        self.at_combat_entity_init()


def _make() -> _Host:
    return _Host()


# ------------------------------------------------------------------ #
#  Tests: at_combat_entity_init
# ------------------------------------------------------------------ #

class TestInit(unittest.TestCase):
    def test_hp_defaults(self):
        e = _make()
        self.assertEqual(e.db.hp, 100)
        self.assertEqual(e.db.hp_max, 100)

    def test_equipment_slots_empty(self):
        e = _make()
        self.assertEqual(e.db.equipment_slots, {})

    def test_incapacitated_false(self):
        e = _make()
        self.assertFalse(e.db.incapacitated)

    def test_respawn_timer_zero(self):
        e = _make()
        self.assertEqual(e.db.respawn_timer, 0)

    def test_respawn_location_none(self):
        e = _make()
        self.assertIsNone(e.db.respawn_location)


# ------------------------------------------------------------------ #
#  Tests: take_damage
# ------------------------------------------------------------------ #

class TestTakeDamage(unittest.TestCase):
    def test_reduces_hp(self):
        e = _make()
        actual = e.take_damage(30)
        self.assertEqual(actual, 30)
        self.assertEqual(e.db.hp, 70)

    def test_returns_actual_damage_when_overkill(self):
        e = _make()
        e.db.hp = 10
        actual = e.take_damage(50)
        self.assertEqual(actual, 10)
        self.assertEqual(e.db.hp, 0)

    def test_incapacitates_at_zero_hp(self):
        e = _make()
        e.take_damage(100)
        self.assertTrue(e.db.incapacitated)
        self.assertEqual(e.db.hp, 0)
        self.assertEqual(e.db.respawn_timer, DEFAULT_RESPAWN_TICKS)

    def test_negative_amount_treated_as_zero(self):
        e = _make()
        actual = e.take_damage(-5)
        self.assertEqual(actual, 0)
        self.assertEqual(e.db.hp, 100)

    def test_zero_damage(self):
        e = _make()
        actual = e.take_damage(0)
        self.assertEqual(actual, 0)
        self.assertEqual(e.db.hp, 100)


# ------------------------------------------------------------------ #
#  Tests: heal
# ------------------------------------------------------------------ #

class TestHeal(unittest.TestCase):
    def test_heals_damage(self):
        e = _make()
        e.db.hp = 60
        actual = e.heal(20)
        self.assertEqual(actual, 20)
        self.assertEqual(e.db.hp, 80)

    def test_caps_at_hp_max(self):
        e = _make()
        e.db.hp = 90
        actual = e.heal(50)
        self.assertEqual(actual, 10)
        self.assertEqual(e.db.hp, 100)

    def test_heal_at_full_hp(self):
        e = _make()
        actual = e.heal(10)
        self.assertEqual(actual, 0)
        self.assertEqual(e.db.hp, 100)

    def test_negative_amount_treated_as_zero(self):
        e = _make()
        e.db.hp = 50
        actual = e.heal(-10)
        self.assertEqual(actual, 0)
        self.assertEqual(e.db.hp, 50)


# ------------------------------------------------------------------ #
#  Tests: is_alive
# ------------------------------------------------------------------ #

class TestIsAlive(unittest.TestCase):
    def test_alive_at_full_hp(self):
        e = _make()
        self.assertTrue(e.is_alive())

    def test_alive_at_partial_hp(self):
        e = _make()
        e.db.hp = 1
        self.assertTrue(e.is_alive())

    def test_not_alive_at_zero_hp(self):
        e = _make()
        e.db.hp = 0
        self.assertFalse(e.is_alive())

    def test_not_alive_when_incapacitated(self):
        e = _make()
        e.db.incapacitated = True
        self.assertFalse(e.is_alive())


# ------------------------------------------------------------------ #
#  Tests: incapacitate
# ------------------------------------------------------------------ #

class TestIncapacitate(unittest.TestCase):
    def test_sets_incapacitated(self):
        e = _make()
        e.incapacitate(5)
        self.assertTrue(e.db.incapacitated)
        self.assertEqual(e.db.respawn_timer, 5)


# ------------------------------------------------------------------ #
#  Tests: tick_respawn
# ------------------------------------------------------------------ #

class TestTickRespawn(unittest.TestCase):
    def test_decrements_timer(self):
        e = _make()
        e.incapacitate(3)
        result = e.tick_respawn()
        self.assertFalse(result)
        self.assertEqual(e.db.respawn_timer, 2)

    def test_respawns_when_timer_expires(self):
        e = _make()
        e.incapacitate(1)
        result = e.tick_respawn()
        self.assertTrue(result)
        self.assertFalse(e.db.incapacitated)
        self.assertEqual(e.db.hp, 100)
        self.assertEqual(e.db.respawn_timer, 0)

    def test_noop_when_not_incapacitated(self):
        e = _make()
        result = e.tick_respawn()
        self.assertFalse(result)

    def test_full_respawn_cycle(self):
        e = _make()
        e.take_damage(100)  # incapacitates
        self.assertTrue(e.db.incapacitated)
        # Tick through the respawn timer
        for _ in range(DEFAULT_RESPAWN_TICKS - 1):
            self.assertFalse(e.tick_respawn())
        self.assertTrue(e.tick_respawn())
        self.assertTrue(e.is_alive())
        self.assertEqual(e.db.hp, 100)


# ------------------------------------------------------------------ #
#  Tests: get_structured_state
# ------------------------------------------------------------------ #

class TestGetStructuredState(unittest.TestCase):
    def test_returns_expected_keys(self):
        e = _make()
        state = e.get_structured_state()
        expected_keys = {"hp", "hp_max", "incapacitated", "respawn_timer", "equipment_slots"}
        self.assertEqual(set(state.keys()), expected_keys)

    def test_reflects_current_state(self):
        e = _make()
        e.take_damage(40)
        state = e.get_structured_state()
        self.assertEqual(state["hp"], 60)
        self.assertEqual(state["hp_max"], 100)
        self.assertFalse(state["incapacitated"])

    def test_reflects_incapacitated_state(self):
        e = _make()
        e.take_damage(100)
        state = e.get_structured_state()
        self.assertEqual(state["hp"], 0)
        self.assertTrue(state["incapacitated"])
        self.assertEqual(state["respawn_timer"], DEFAULT_RESPAWN_TICKS)


if __name__ == "__main__":
    unittest.main()
