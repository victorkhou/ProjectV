"""
Property-based test for the R9 poison DoT (item-loot-economy task 3.2,
design §6.2).

# Feature: item-loot-economy, Property 8: Poison DoT mirrors the fire model with mitigation

For any poison hit with raw damage ``d``, a DoT effect of
``max(1, round(d * poison_dot_fraction))`` per tick for ``poison_dot_ticks``
ticks is applied, ticking to zero HP routes through ``_handle_zero_hp``, and
``poison_resist`` plus ``baseline_resist`` mitigate the typed hit per the
existing resist math (chip floor included).

**Validates: Requirements 9.1, 9.3**
"""

import math
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

from mygame.world.systems.combat_engine import CombatEngine  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import BalanceConfig  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Fakes (modeled on test_combat_engine.py's — the shapes the
#  engine's duck-typed reads expect)
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""

    def __init__(self, hp=100, hp_max=100, combat_xp=0):
        self.hp = hp
        self.hp_max = hp_max
        self.combat_xp = combat_xp
        self.combat_lockout_tick = 0
        self.active_powerups = {}
        self.active_effects = []


class FakeEquipmentHandler:
    def __init__(self):
        self._slots = {}

    def equip(self, item):
        self._slots[getattr(item, "slot", "weapon")] = item

    def get_equipped(self, slot):
        return self._slots.get(slot)

    def get_stat_total(self, stat_name):
        return sum(
            item.get_stat(stat_name, 0) for item in self._slots.values()
        )


class FakeWeapon:
    def __init__(self, damage=25, damage_type="poison", key="venom_rifle"):
        self.key = key
        self.slot = "weapon"
        self.damage_type = damage_type
        self.stat_modifiers = {"damage": damage, "range": 3}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakeResistGear:
    """A non-weapon gear piece carrying ``poison_resist`` — aggregates for
    free via get_stat_total (R9.3)."""

    def __init__(self, poison_resist, key="antidote_charm"):
        self.key = key
        self.slot = "back"
        self.stat_modifiers = {"poison_resist": poison_resist}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class FakePlayer:
    """A player fake (combat_xp set, so _is_player/_owning_player resolve)."""

    def __init__(self, name="TestPlayer", hp=100, hp_max=100):
        self.key = name
        self.db = FakeDB(hp=hp, hp_max=hp_max)
        self.location = None
        self.equipment = FakeEquipmentHandler()

    def msg(self, text):
        pass


def _make_engine(**balance_overrides):
    registry = DataRegistry()
    registry.balance = BalanceConfig(**balance_overrides)
    engine = CombatEngine(
        registry=registry, event_bus=EventBus(), current_tick_func=lambda: 0,
    )
    return engine

# -------------------------------------------------------------- #
#  Property 8: Poison DoT mirrors the fire model with mitigation
#  # Feature: item-loot-economy, Property 8: Poison DoT mirrors the
#  # fire model with mitigation
#  **Validates: Requirements 9.1, 9.3**
# -------------------------------------------------------------- #

class TestProperty8PoisonDoT(unittest.TestCase):
    """Property 8: Poison DoT mirrors the fire model with mitigation.

    # Feature: item-loot-economy, Property 8: Poison DoT mirrors the fire model with mitigation

    For any poison hit with raw damage ``d``, a DoT effect of
    ``max(1, round(d · poison_dot_fraction))`` per tick for
    ``poison_dot_ticks`` ticks is applied, ticking to zero HP routes through
    ``_handle_zero_hp``, and ``poison_resist`` plus ``baseline_resist``
    mitigate the typed hit per the existing resist math (chip floor included).

    **Validates: Requirements 9.1, 9.3**
    """

    @given(
        raw=st.integers(min_value=1, max_value=500),
        fraction=st.floats(min_value=0.01, max_value=0.95,
                           allow_nan=False, allow_infinity=False),
        ticks=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=25)
    def test_poison_hit_applies_formulaic_dot_that_ticks(
        self, raw, fraction, ticks,
    ):
        """A poison hit through _finalize_hit (the shared hit choke point)
        applies exactly max(1, round(raw * poison_dot_fraction)) per tick
        for poison_dot_ticks ticks, and the DoT ticks that amount each tick
        until it expires (R9.1)."""
        engine = _make_engine(
            poison_dot_fraction=fraction, poison_dot_ticks=ticks,
        )
        attacker = FakePlayer(name="A")
        weapon = FakeWeapon(damage=raw, damage_type="poison")
        # HP large enough that neither the hit nor the full DoT kills.
        expected_per_tick = max(1, int(round(raw * fraction)))
        target = FakePlayer(
            name="T",
            hp=expected_per_tick * ticks + raw + 10,
            hp_max=expected_per_tick * ticks + raw + 10,
        )

        engine._finalize_hit(attacker, target, weapon, damage=1,
                             current_tick=0)

        effects = target.db.active_effects
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["type"], "poison")
        self.assertEqual(effects[0]["damage"], expected_per_tick)
        self.assertEqual(effects[0]["ticks_remaining"], ticks)

        # The DoT ticks its per-tick amount for exactly `ticks` ticks.
        hp_before = target.db.hp
        for i in range(1, ticks + 1):
            engine.tick_effects_on_entity(target)
            self.assertEqual(target.db.hp, hp_before - i * expected_per_tick)
        self.assertEqual(target.db.active_effects, [],
                         "DoT must expire after poison_dot_ticks ticks")

    @given(
        hp=st.integers(min_value=1, max_value=5),
        dot_damage=st.integers(min_value=5, max_value=20),
        ticks=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=25)
    def test_poison_tick_to_zero_hp_routes_through_handle_zero_hp(
        self, hp, dot_damage, ticks,
    ):
        """A poison tick that drops an enemy NPC to zero HP routes through
        _handle_zero_hp — permanent death (deleted), never the
        player-respawn path (R9.1 — the fire-model kill routing)."""
        engine = _make_engine()
        guard = FakePlayer(name="Guard", hp=hp, hp_max=hp)
        guard.db.npc_type = "enemy"  # an NPC-base guard
        deleted = []
        guard.delete = lambda: deleted.append(True)
        guard.db.active_effects = [
            {"type": "poison", "damage": dot_damage,
             "ticks_remaining": ticks, "source": None},
        ]

        engine.tick_effects_on_entity(guard)

        self.assertTrue(deleted,
                        "poison-killed enemy NPC must die permanently")
        self.assertLessEqual(guard.db.hp, 0,
                             "must NOT be respawned to full HP")
        self.assertEqual(guard.db.active_effects, [],
                         "effects cleared on death")

    @given(
        raw=st.integers(min_value=1, max_value=500),
        resist=st.integers(min_value=0, max_value=200),
        baseline=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=25)
    def test_poison_typed_hit_mitigated_by_resists_with_chip_floor(
        self, raw, resist, baseline,
    ):
        """A pure poison typed hit is reduced by poison_resist (aggregated
        gear) + baseline_resist per the existing typed-resist math, floored
        at the 50% chip so stacked resist never grants immunity (R9.3)."""
        engine = _make_engine(
            baseline_resist=float(baseline),
            chip_damage_min_fraction=0.5,
        )
        attacker = FakePlayer(name="A")
        target = FakePlayer(name="T", hp=10_000, hp_max=10_000)
        if resist:
            target.equipment.equip(FakeResistGear(poison_resist=resist))

        dealt = engine._calculate_damage(
            attacker=attacker, target=target,
            weapon_item=FakeWeapon(damage=raw, damage_type="poison"),
        )

        # Existing resist math: net = raw - (resist + baseline), chip floor
        # = ceil(raw * 0.5), dealt = max(chip, net, 0).
        net = int(raw - (resist + baseline))
        chip = int(math.ceil(raw * 0.5))
        self.assertEqual(dealt, max(chip, net, 0))


if __name__ == "__main__":
    unittest.main()
