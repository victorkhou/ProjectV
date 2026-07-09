"""
Unit tests for RegenSystem — passive HP regen for players and agents.

Covers: interval gating, percent-of-max healing, hp_max cap, skipping
dead/incapacitated/full entities, fractional accumulation for sub-1-HP rates,
the per-entity regen_multiplier hook, injected modifier providers, and the
disabled (0%) config.
"""

import sys
import types
import unittest


def _ensure_evennia_stubs():
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
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_ensure_evennia_stubs()

from mygame.world.systems.regen_system import RegenSystem  # noqa: E402
from mygame.world.definitions import BalanceConfig  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402


class _DB:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Entity:
    """Minimal CombatEntity stand-in with a db bag."""
    def __init__(self, hp=50, hp_max=100, incapacitated=False,
                 regen_multiplier=None):
        self.key = "Ent"
        self.db = _DB(hp=hp, hp_max=hp_max, incapacitated=incapacitated)
        if regen_multiplier is not None:
            self.db.regen_multiplier = regen_multiplier


class _Registry:
    def __init__(self, balance=None):
        self.balance = balance or BalanceConfig()


def _make(percent=1.0, interval=2):
    balance = BalanceConfig()
    balance.hp_regen_percent = percent
    balance.hp_regen_interval_ticks = interval
    return RegenSystem(_Registry(balance), EventBus())


class TestRegenBasics(unittest.TestCase):
    def test_heals_percent_of_max_on_interval_tick(self):
        # 1% of 100 = 1 HP, interval 2 -> applies on tick % 2 == 0.
        system = _make(percent=1.0, interval=2)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 51)

    def test_no_heal_off_interval(self):
        system = _make(percent=1.0, interval=2)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=3)  # 3 % 2 != 0
        self.assertEqual(ent.db.hp, 50)

    def test_caps_at_hp_max(self):
        system = _make(percent=50.0, interval=1)  # 50 HP/tick
        ent = _Entity(hp=90, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 100)  # not 140

    def test_full_hp_entity_skipped(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=100, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 100)

    def test_dead_entity_not_regenerated(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=0, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 0)  # revives via respawn, not regen

    def test_incapacitated_entity_not_regenerated(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=20, hp_max=100, incapacitated=True)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 20)

    def test_accumulator_reset_at_cap_no_burst_after_damage(self):
        """Surplus is dropped when capping, so a later hit can't burst-heal.

        Regression for the reviewer-flagged gap: if the accumulator retained
        the surplus at cap instead of resetting to 0.0, a subsequent damage
        event would let a big banked remainder heal back instantly.
        """
        system = _make(percent=50.0, interval=1)  # 50 HP/interval
        ent = _Entity(hp=90, hp_max=100)
        system.process_tick([ent], tick_number=1)  # 90 -> cap 100, surplus 40
        self.assertEqual(ent.db.hp, 100)
        # The surplus must NOT be banked (else it would be ~40).
        self.assertEqual(ent.db.hp_regen_accumulator, 0.0)
        # Take damage, then regen one interval: heals only one interval's worth
        # (50), not 90 (= 50 + a retained 40 surplus).
        ent.db.hp = 20
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 70)


class TestShouldRegenThisTick(unittest.TestCase):
    """The interval/enabled gate the tick loop consults before enumerating."""

    def test_true_on_interval_boundary(self):
        system = _make(percent=1.0, interval=2)
        self.assertTrue(system.should_regen_this_tick(4))

    def test_false_off_interval(self):
        system = _make(percent=1.0, interval=2)
        self.assertFalse(system.should_regen_this_tick(3))

    def test_false_when_percent_disabled(self):
        system = _make(percent=0.0, interval=2)
        self.assertFalse(system.should_regen_this_tick(4))

    def test_false_when_interval_non_positive(self):
        system = _make(percent=1.0, interval=0)
        self.assertFalse(system.should_regen_this_tick(4))


class TestFractionalAccumulation(unittest.TestCase):
    def test_sub_one_hp_rate_accumulates_then_heals(self):
        # 0.5% of 100 = 0.5 HP/interval; interval 1 for simplicity.
        system = _make(percent=0.5, interval=1)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 50)  # 0.5 banked, no whole HP yet
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 51)  # 0.5 + 0.5 = 1.0 applied

    def test_remainder_carried_across_applications(self):
        # 1.5 HP/interval -> +1 now with 0.5 banked, +2 next (0.5+1.5=2.0).
        system = _make(percent=1.5, interval=1)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 51)
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 53)


class TestModifiers(unittest.TestCase):
    def test_per_entity_multiplier_scales_rate(self):
        system = _make(percent=1.0, interval=1)  # base 1 HP
        ent = _Entity(hp=50, hp_max=100, regen_multiplier=3.0)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 53)  # 1 * 3

    def test_zero_multiplier_disables_for_entity(self):
        system = _make(percent=10.0, interval=1)
        ent = _Entity(hp=50, hp_max=100, regen_multiplier=0.0)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 50)

    def test_injected_modifier_provider_applies(self):
        system = _make(percent=1.0, interval=1)
        # A "heal-rate tech" style provider doubling regen for everyone.
        system.add_modifier_provider(lambda e: 2.0)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 52)

    def test_provider_and_entity_multiplier_compound(self):
        system = _make(percent=1.0, interval=1)
        system.add_modifier_provider(lambda e: 2.0)
        ent = _Entity(hp=50, hp_max=100, regen_multiplier=2.0)
        system.process_tick([ent], tick_number=1)
        self.assertEqual(ent.db.hp, 54)  # 1 * 2 * 2


class TestDisabled(unittest.TestCase):
    def test_zero_percent_disables_regen(self):
        system = _make(percent=0.0, interval=2)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=2)
        self.assertEqual(ent.db.hp, 50)

    def test_zero_interval_disables_regen(self):
        system = _make(percent=1.0, interval=0)
        ent = _Entity(hp=50, hp_max=100)
        system.process_tick([ent], tick_number=0)
        self.assertEqual(ent.db.hp, 50)

    def test_empty_entities_is_noop(self):
        system = _make()
        system.process_tick([], tick_number=2)  # must not raise
        system.process_tick(None, tick_number=2)  # tolerate None


if __name__ == "__main__":
    unittest.main()
