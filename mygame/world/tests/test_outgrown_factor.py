"""
Unit tests for world.utils.outgrown_factor — the graduation-economy XP/yield
throttle for players who have outgrown their current planet (§4).
"""

import types
import unittest

from world.utils import outgrown_factor


class _FakeSpace:
    def __init__(self, rank_requirement, planet_type="earth"):
        self.rank_requirement = rank_requirement
        self.planet_type = planet_type


class _FakePlanetRegistry:
    def __init__(self, spaces):
        self._spaces = spaces

    def get_space(self, key):
        if key not in self._spaces:
            raise KeyError(key)
        return self._spaces[key]

    def list_planets(self):
        return list(self._spaces.keys())


class _FakePlayer:
    def __init__(self, level, planet="terra"):
        self.db = types.SimpleNamespace(level=level, coord_planet=planet)


def _install_registry(spaces):
    """Install a fake planet registry into game_systems; return a teardown."""
    from server.conf import game_init
    prev = game_init.game_systems.get("planet_registry")
    game_init.game_systems["planet_registry"] = _FakePlanetRegistry(spaces)

    def teardown():
        if prev is None:
            game_init.game_systems.pop("planet_registry", None)
        else:
            game_init.game_systems["planet_registry"] = prev
    return teardown


# The re-mapped ladder: Terra=1, Forge=21, Tundra=33, Citadel=70, Space=1.
_LADDER = {
    "terra": _FakeSpace(1),
    "forge": _FakeSpace(21, "industrial"),
    "tundra": _FakeSpace(33, "frozen"),
    "space": _FakeSpace(1, "space"),
}


class TestOutgrownFactor(unittest.TestCase):
    def setUp(self):
        self._teardown = _install_registry(_LADDER)

    def tearDown(self):
        self._teardown()

    def test_legitimate_resident_full_factor(self):
        """A player below the next gate is a legit resident → factor 1.0."""
        # On Terra (gate 1), next gate = Forge (21). Level 15 < 21 → resident.
        p = _FakePlayer(level=15, planet="terra")
        self.assertEqual(outgrown_factor(p), 1.0)

    def test_just_eligible_full_factor(self):
        """Exactly at the next gate → factor 1.0 (just became eligible)."""
        p = _FakePlayer(level=21, planet="terra")
        self.assertEqual(outgrown_factor(p), 1.0)

    def test_camping_throttled(self):
        """A player past the next gate + grace is throttled toward the minimum."""
        # Next gate = 21, grace = 5. Level 26+ → min factor (0.25).
        p = _FakePlayer(level=30, planet="terra")
        factor = outgrown_factor(p)
        self.assertLess(factor, 1.0)
        self.assertGreaterEqual(factor, 0.25)
        self.assertEqual(factor, 0.25)  # at/past gate+grace = min

    def test_camping_partial_throttle(self):
        """Between the gate and gate+grace, factor is linear (0.25–1.0)."""
        # Next gate 21, level 23 = 2 over, grace 5 → 1 - 0.75*(2/5) = 0.7
        p = _FakePlayer(level=23, planet="terra")
        factor = outgrown_factor(p)
        self.assertTrue(0.25 < factor < 1.0)
        self.assertAlmostEqual(factor, 0.7, places=5)

    def test_top_of_ladder_no_throttle(self):
        """A player on the highest planet (no next gate) is never throttled."""
        # Tundra is the highest in this ladder (33). Level 99 → no next gate.
        p = _FakePlayer(level=99, planet="tundra")
        self.assertEqual(outgrown_factor(p), 1.0)

    def test_space_never_throttled(self):
        """Space (off-ladder hub) is exempt regardless of level."""
        p = _FakePlayer(level=99, planet="space")
        self.assertEqual(outgrown_factor(p), 1.0)

    def test_no_registry_returns_full(self):
        """No planet registry available → factor 1.0 (safe default)."""
        self._teardown()  # remove the registry
        from server.conf import game_init
        game_init.game_systems.pop("planet_registry", None)
        p = _FakePlayer(level=99, planet="terra")
        self.assertEqual(outgrown_factor(p), 1.0)
        self._teardown = _install_registry(_LADDER)  # restore for tearDown


if __name__ == "__main__":
    unittest.main()
