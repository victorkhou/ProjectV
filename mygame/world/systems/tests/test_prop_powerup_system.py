"""
Property-based tests for PowerupSystem.

Property 20: Powerup activation and expiry round-trip
Property 21: Powerup cooldown enforcement

Validates: Requirements 9.2, 9.3, 9.4, 9.5
"""

import sys
import types
import unittest

from hypothesis import given, settings, assume
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

from mygame.world.systems.powerup_system import PowerupSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    BalanceConfig,
    PowerupDef,
    RankDef,
)
from mygame.world.event_bus import EventBus  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self):
        self.rank_level = 5
        self.active_powerups = {}
        self.powerup_cooldowns = {}

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""
    def __init__(self, name="TestPlayer", rank_level=5):
        self.key = name
        self.db = FakeDB()
        self.db.rank_level = rank_level

SAMPLE_RANKS = [
    RankDef(name="Recruit", level=0, xp_threshold=0),
    RankDef(name="Private", level=1, xp_threshold=100),
    RankDef(name="Corporal", level=2, xp_threshold=300),
    RankDef(name="Sergeant", level=3, xp_threshold=600),
    RankDef(name="Captain", level=5, xp_threshold=1500),
    RankDef(name="General", level=10, xp_threshold=10000),
]

SAMPLE_POWERUPS = {
    "adrenaline_rush": PowerupDef(
        name="Adrenaline Rush", key="adrenaline_rush",
        required_rank="Corporal", effect_type="damage_bonus",
        effect_value=1.5, duration_ticks=30, cooldown_ticks=120,
    ),
    "shield_boost": PowerupDef(
        name="Shield Boost", key="shield_boost",
        required_rank="Recruit", effect_type="damage_reduction",
        effect_value=5.0, duration_ticks=20, cooldown_ticks=60,
    ),
    "speed_surge": PowerupDef(
        name="Speed Surge", key="speed_surge",
        required_rank="Sergeant", effect_type="move_speed",
        effect_value=2.0, duration_ticks=15, cooldown_ticks=90,
    ),
}

def _make_registry(ranks=None, powerups=None):
    """Create a DataRegistry with test definitions."""
    registry = DataRegistry()
    registry.ranks = ranks or list(SAMPLE_RANKS)
    registry.powerups = dict(powerups or SAMPLE_POWERUPS)
    registry.balance = BalanceConfig()
    return registry

def _make_system(registry=None, event_bus=None, current_tick=0):
    """Create a PowerupSystem with optional overrides."""
    if registry is None:
        registry = _make_registry()
    if event_bus is None:
        event_bus = EventBus()
    tick_val = [current_tick]
    system = PowerupSystem(
        registry, event_bus,
        current_tick_func=lambda: tick_val[0],
    )
    return system, event_bus, tick_val

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def powerup_key_strategy(draw):
    """Generate a valid powerup key from the sample set."""
    return draw(st.sampled_from(list(SAMPLE_POWERUPS.keys())))

@st.composite
def duration_strategy(draw):
    """Generate a powerup duration (1-200 ticks)."""
    return draw(st.integers(min_value=1, max_value=200))

@st.composite
def cooldown_strategy(draw):
    """Generate a cooldown duration (1-500 ticks)."""
    return draw(st.integers(min_value=1, max_value=500))

@st.composite
def powerup_def_strategy(draw):
    """Generate a random PowerupDef."""
    duration = draw(st.integers(min_value=1, max_value=200))
    cooldown = draw(st.integers(min_value=1, max_value=500))
    effect_value = draw(st.floats(min_value=0.1, max_value=10.0, allow_nan=False))
    effect_type = draw(st.sampled_from(["damage_bonus", "damage_reduction", "move_speed"]))
    key = f"test_powerup_{draw(st.integers(min_value=0, max_value=9999))}"
    return PowerupDef(
        name=f"Test {key}", key=key,
        required_rank="Recruit", effect_type=effect_type,
        effect_value=effect_value, duration_ticks=duration,
        cooldown_ticks=cooldown,
    )

# -------------------------------------------------------------- #
#  Property 20: Powerup activation and expiry round-trip
#  **Validates: Requirements 9.2, 9.3, 9.4**
# -------------------------------------------------------------- #

class TestProperty20PowerupActivationExpiry(unittest.TestCase):
    """Property 20: Powerup activation and expiry round-trip.

    For any powerup activated by a player with sufficient rank, the
    player's relevant combat stat SHALL be modified by the powerup's
    effect_value for exactly duration_ticks game ticks. After expiry,
    the stat SHALL return to its pre-activation value.

    **Validates: Requirements 9.2, 9.3, 9.4**
    """

    @given(pdef=powerup_def_strategy())
    @settings(max_examples=100)
    def test_activation_applies_stat_modifier(self, pdef):
        """Activating a powerup applies its effect_value as a stat modifier."""
        registry = _make_registry(
            powerups={pdef.key: pdef},
        )
        system, bus, tick_val = _make_system(registry=registry, current_tick=0)
        player = FakePlayer(rank_level=10)

        ok, msg = system.activate(player, pdef.key)
        self.assertTrue(ok, f"Activation should succeed: {msg}")

        # Stat modifier should reflect the powerup's effect
        modifier = system.get_stat_modifier(player, pdef.effect_type)
        self.assertAlmostEqual(
            modifier, pdef.effect_value,
            msg=f"Stat modifier for {pdef.effect_type} should be {pdef.effect_value}",
        )

    @given(pdef=powerup_def_strategy())
    @settings(max_examples=100)
    def test_powerup_expires_after_duration(self, pdef):
        """Powerup effect is removed after exactly duration_ticks."""
        registry = _make_registry(
            powerups={pdef.key: pdef},
        )
        system, bus, tick_val = _make_system(registry=registry, current_tick=0)
        player = FakePlayer(rank_level=10)

        system.activate(player, pdef.key)

        # Process ticks up to duration - 1: should still be active
        for t in range(1, pdef.duration_ticks):
            tick_val[0] = t
            system.process_tick(t)
            modifier = system.get_stat_modifier(player, pdef.effect_type)
            self.assertAlmostEqual(
                modifier, pdef.effect_value,
                msg=f"Powerup should still be active at tick {t}",
            )

        # Process the expiry tick
        tick_val[0] = pdef.duration_ticks
        system.process_tick(pdef.duration_ticks)

        # Stat modifier should be 0 after expiry
        modifier = system.get_stat_modifier(player, pdef.effect_type)
        self.assertAlmostEqual(
            modifier, 0.0,
            msg="Stat modifier should be 0 after powerup expires",
        )

    @given(pdef=powerup_def_strategy())
    @settings(max_examples=100)
    def test_active_powerups_list_during_and_after(self, pdef):
        """get_active_powerups returns the powerup while active, empty after."""
        registry = _make_registry(
            powerups={pdef.key: pdef},
        )
        system, bus, tick_val = _make_system(registry=registry, current_tick=0)
        player = FakePlayer(rank_level=10)

        system.activate(player, pdef.key)

        # Should be in active list
        active = system.get_active_powerups(player)
        keys = [p["key"] for p in active]
        self.assertIn(pdef.key, keys)

        # Expire it
        tick_val[0] = pdef.duration_ticks
        system.process_tick(pdef.duration_ticks)

        active = system.get_active_powerups(player)
        keys = [p["key"] for p in active]
        self.assertNotIn(pdef.key, keys)

# -------------------------------------------------------------- #
#  Property 21: Powerup cooldown enforcement
#  **Validates: Requirements 9.5**
# -------------------------------------------------------------- #

class TestProperty21PowerupCooldown(unittest.TestCase):
    """Property 21: Powerup cooldown enforcement.

    For any powerup that has been activated, attempting to reactivate
    it within cooldown_ticks game ticks SHALL be rejected. After the
    cooldown expires, reactivation SHALL be allowed.

    **Validates: Requirements 9.5**
    """

    @given(pdef=powerup_def_strategy())
    @settings(max_examples=100)
    def test_reactivation_during_cooldown_rejected(self, pdef):
        """Cannot reactivate a powerup during its cooldown period."""
        registry = _make_registry(
            powerups={pdef.key: pdef},
        )
        system, bus, tick_val = _make_system(registry=registry, current_tick=0)
        player = FakePlayer(rank_level=10)

        # First activation succeeds
        ok, _ = system.activate(player, pdef.key)
        self.assertTrue(ok)

        # Expire the powerup so it's no longer "already active"
        tick_val[0] = pdef.duration_ticks
        system.process_tick(pdef.duration_ticks)

        # Try to reactivate during cooldown — should fail
        # Cooldown started at tick 0, expires at tick cooldown_ticks
        # We're at tick duration_ticks which may be < cooldown_ticks
        if pdef.duration_ticks < pdef.cooldown_ticks:
            ok2, msg2 = system.activate(player, pdef.key)
            self.assertFalse(ok2, f"Should be rejected during cooldown: {msg2}")
            self.assertIn("cooldown", msg2.lower())

    @given(pdef=powerup_def_strategy())
    @settings(max_examples=100)
    def test_reactivation_after_cooldown_allowed(self, pdef):
        """Can reactivate a powerup after its cooldown expires."""
        registry = _make_registry(
            powerups={pdef.key: pdef},
        )
        system, bus, tick_val = _make_system(registry=registry, current_tick=0)
        player = FakePlayer(rank_level=10)

        # First activation
        ok, _ = system.activate(player, pdef.key)
        self.assertTrue(ok)

        # Expire the powerup
        tick_val[0] = pdef.duration_ticks
        system.process_tick(pdef.duration_ticks)

        # Advance past cooldown
        tick_val[0] = pdef.cooldown_ticks
        system.process_tick(pdef.cooldown_ticks)

        # Reactivation should succeed
        ok2, msg2 = system.activate(player, pdef.key)
        self.assertTrue(ok2, f"Should succeed after cooldown: {msg2}")

    @given(
        pdef=powerup_def_strategy(),
        attempt_tick=st.integers(min_value=1, max_value=499),
    )
    @settings(max_examples=100)
    def test_cooldown_boundary(self, pdef, attempt_tick):
        """Cooldown is enforced exactly at the boundary tick."""
        registry = _make_registry(
            powerups={pdef.key: pdef},
        )
        system, bus, tick_val = _make_system(registry=registry, current_tick=0)
        player = FakePlayer(rank_level=10)

        # Activate at tick 0
        system.activate(player, pdef.key)

        # Expire the powerup first
        expire_tick = pdef.duration_ticks
        tick_val[0] = expire_tick
        system.process_tick(expire_tick)

        # Try at attempt_tick
        tick_val[0] = attempt_tick
        ok, msg = system.activate(player, pdef.key)

        if attempt_tick < pdef.cooldown_ticks:
            # Should be rejected (still on cooldown)
            self.assertFalse(ok, f"Should be on cooldown at tick {attempt_tick}")
        else:
            # Should be allowed (cooldown expired)
            self.assertTrue(ok, f"Should be allowed at tick {attempt_tick}: {msg}")

if __name__ == "__main__":
    unittest.main()
