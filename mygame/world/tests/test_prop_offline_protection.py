"""
Property-based tests for offline building protection.

Property 22: Offline building protection round-trip

Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5
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

from mygame.world.offline_protection import (  # noqa: E402
    on_player_login,
    on_player_logout,
    is_building_offline,
    can_damage_building,
    can_enter_tile_with_building,
    is_production_suspended,
)

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeBuilding:
    """Lightweight stand-in for a Building object with offline state."""

    def __init__(self, building_type="HQ", offline=False):
        self.key = building_type
        self._offline = offline

    @property
    def is_offline(self):
        return self._offline

    def set_offline(self, state: bool):
        self._offline = state

class FakePlayer:
    """Lightweight stand-in for CombatCharacter."""

    def __init__(self, name="TestPlayer", buildings=None):
        self.key = name
        self._buildings = buildings or []

    def get_buildings(self):
        return list(self._buildings)

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

@st.composite
def building_count_strategy(draw):
    """Generate a number of buildings (1-10)."""
    return draw(st.integers(min_value=1, max_value=10))

@st.composite
def building_types_strategy(draw):
    """Generate a list of building type abbreviations."""
    count = draw(st.integers(min_value=1, max_value=10))
    types_pool = ["HQ", "MM", "QQ", "II", "LL", "KK", "AA", "AR", "VV", "TL"]
    return [draw(st.sampled_from(types_pool)) for _ in range(count)]

# -------------------------------------------------------------- #
#  Property 22: Offline building protection round-trip
#  **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**
# -------------------------------------------------------------- #

class TestProperty22OfflineBuildingProtection(unittest.TestCase):
    """Property 22: Offline building protection round-trip.

    For any player who logs out and then logs back in, all their
    buildings SHALL transition to offline state on logout (blocking
    damage, entry, and production) and return to active state on
    login (resuming normal function).

    **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**
    """

    @given(building_types=building_types_strategy())
    @settings(max_examples=100)
    def test_logout_sets_all_buildings_offline(self, building_types):
        """All player buildings transition to offline on logout."""
        buildings = [FakeBuilding(bt) for bt in building_types]
        player = FakePlayer(buildings=buildings)

        # All buildings start online
        for b in buildings:
            self.assertFalse(is_building_offline(b))

        # Logout
        on_player_logout(player, buildings=buildings)

        # All buildings should be offline
        for b in buildings:
            self.assertTrue(
                is_building_offline(b),
                f"Building {b.key} should be offline after logout",
            )

    @given(building_types=building_types_strategy())
    @settings(max_examples=100)
    def test_login_sets_all_buildings_online(self, building_types):
        """All player buildings transition to online on login."""
        buildings = [FakeBuilding(bt, offline=True) for bt in building_types]
        player = FakePlayer(buildings=buildings)

        # All buildings start offline
        for b in buildings:
            self.assertTrue(is_building_offline(b))

        # Login
        on_player_login(player, buildings=buildings)

        # All buildings should be online
        for b in buildings:
            self.assertFalse(
                is_building_offline(b),
                f"Building {b.key} should be online after login",
            )

    @given(building_types=building_types_strategy())
    @settings(max_examples=100)
    def test_offline_buildings_block_damage(self, building_types):
        """Offline buildings cannot receive damage."""
        buildings = [FakeBuilding(bt) for bt in building_types]
        player = FakePlayer(buildings=buildings)

        on_player_logout(player, buildings=buildings)

        for b in buildings:
            self.assertFalse(
                can_damage_building(b),
                f"Offline building {b.key} should block damage",
            )

    @given(building_types=building_types_strategy())
    @settings(max_examples=100)
    def test_offline_buildings_block_entry(self, building_types):
        """Tiles with offline buildings block entry."""
        buildings = [FakeBuilding(bt) for bt in building_types]
        player = FakePlayer(buildings=buildings)

        on_player_logout(player, buildings=buildings)

        for b in buildings:
            self.assertFalse(
                can_enter_tile_with_building(b),
                f"Tile with offline building {b.key} should block entry",
            )

    @given(building_types=building_types_strategy())
    @settings(max_examples=100)
    def test_offline_buildings_suspend_production(self, building_types):
        """Offline buildings have production suspended."""
        buildings = [FakeBuilding(bt) for bt in building_types]
        player = FakePlayer(buildings=buildings)

        on_player_logout(player, buildings=buildings)

        for b in buildings:
            self.assertTrue(
                is_production_suspended(b),
                f"Offline building {b.key} should have production suspended",
            )

    @given(building_types=building_types_strategy())
    @settings(max_examples=100)
    def test_full_round_trip(self, building_types):
        """Full logout → offline checks → login → online checks cycle."""
        buildings = [FakeBuilding(bt) for bt in building_types]
        player = FakePlayer(buildings=buildings)

        # Initially online
        for b in buildings:
            self.assertFalse(is_building_offline(b))
            self.assertTrue(can_damage_building(b))
            self.assertTrue(can_enter_tile_with_building(b))
            self.assertFalse(is_production_suspended(b))

        # Logout
        on_player_logout(player, buildings=buildings)

        # Offline: damage blocked, entry blocked, production suspended
        for b in buildings:
            self.assertTrue(is_building_offline(b))
            self.assertFalse(can_damage_building(b))
            self.assertFalse(can_enter_tile_with_building(b))
            self.assertTrue(is_production_suspended(b))

        # Login
        on_player_login(player, buildings=buildings)

        # Back online: everything restored
        for b in buildings:
            self.assertFalse(is_building_offline(b))
            self.assertTrue(can_damage_building(b))
            self.assertTrue(can_enter_tile_with_building(b))
            self.assertFalse(is_production_suspended(b))

if __name__ == "__main__":
    unittest.main()
