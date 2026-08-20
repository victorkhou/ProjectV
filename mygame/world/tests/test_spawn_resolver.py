"""
Unit tests for the spawn-location resolver (world/spawn_resolver.py) — the
HQ / place-of-death / random-tile options and their fallback chains (state 3.1).
"""

import types
import unittest

from world.spawn_resolver import (
    SPAWN_DEATH,
    SPAWN_HQ,
    SPAWN_RANDOM,
    SPAWN_RESPAWN,
    SpawnResolver,
)


class _Player:
    def __init__(self, death_x=None, death_y=None, death_planet=None):
        self.db = types.SimpleNamespace(
            death_x=death_x, death_y=death_y, death_planet=death_planet,
        )


class _SeqRandom:
    """Deterministic randint: replays a fixed (x, y) queue then repeats last."""
    def __init__(self, pairs):
        self._pairs = list(pairs)

    def randint(self, lo, hi):
        # Called twice per attempt (x then y); pop from a flattened stream.
        if not hasattr(self, "_stream"):
            self._stream = [v for pair in self._pairs for v in pair]
        if self._stream:
            return self._stream.pop(0)
        return lo


def _resolver(**kw):
    defaults = dict(
        planet_spawn_func=lambda p: (250, 250),
        hq_locator_func=lambda player, p: None,
        in_bounds_func=lambda x, y, p: 0 <= x < 500 and 0 <= y < 500,
        planet_size_func=lambda p: (500, 500),
    )
    defaults.update(kw)
    return SpawnResolver(**defaults)


class TestHQSpawn(unittest.TestCase):
    def test_hq_tile_used_when_present(self):
        r = _resolver(hq_locator_func=lambda player, p: (10, 20))
        self.assertEqual(r.resolve(_Player(), SPAWN_HQ, "terra"), ("terra", 10, 20))

    def test_missing_hq_returns_none_without_random_fallback(self):
        r = _resolver(
            hq_locator_func=lambda player, p: None,
            rng=_SeqRandom([(33, 44)]),
        )
        self.assertIsNone(r.resolve(_Player(), SPAWN_HQ, "terra"))

    def test_hq_locator_error_returns_none(self):
        def boom(player, p):
            raise RuntimeError("db down")

        r = _resolver(hq_locator_func=boom, rng=_SeqRandom([(33, 44)]))
        self.assertIsNone(r.resolve(_Player(), SPAWN_HQ, "terra"))


class TestRespawnSpawn(unittest.TestCase):
    def test_beacon_tile_used_when_present(self):
        r = _resolver(respawn_locator_func=lambda player, p: (5, 6))
        self.assertEqual(
            r.resolve(_Player(), SPAWN_RESPAWN, "terra"), ("terra", 5, 6)
        )

    def test_missing_beacon_returns_none_without_random_fallback(self):
        r = _resolver(
            respawn_locator_func=lambda player, p: None,
            rng=_SeqRandom([(33, 44)]),
        )
        self.assertIsNone(r.resolve(_Player(), SPAWN_RESPAWN, "terra"))


class TestDeathSpawn(unittest.TestCase):
    def test_death_tile_used_when_recorded(self):
        r = _resolver()
        p = _Player(death_x=42, death_y=7, death_planet="terra")
        self.assertEqual(r.resolve(p, SPAWN_DEATH, "terra"), ("terra", 42, 7))

    def test_death_carries_its_own_planet(self):
        # Died on 'forge' but the menu defaults to current planet 'terra' — the
        # death target keeps its recorded planet.
        r = _resolver()
        p = _Player(death_x=5, death_y=5, death_planet="forge")
        self.assertEqual(r.resolve(p, SPAWN_DEATH, "terra"), ("forge", 5, 5))

    def test_death_missing_returns_none_without_random_fallback(self):
        r = _resolver(rng=_SeqRandom([(33, 44)]))
        self.assertIsNone(r.resolve(_Player(), SPAWN_DEATH, "terra"))

    def test_death_out_of_bounds_returns_none_without_random_fallback(self):
        r = _resolver(
            rng=_SeqRandom([(33, 44)]),
            in_bounds_func=lambda x, y, p: 0 <= x < 500 and 0 <= y < 500,
        )
        p = _Player(death_x=9999, death_y=9999, death_planet="terra")
        self.assertIsNone(r.resolve(p, SPAWN_DEATH, "terra"))


class TestRandomSpawn(unittest.TestCase):
    def test_random_returns_in_bounds_tile(self):
        r = _resolver(rng=_SeqRandom([(11, 22)]))
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"), ("terra", 11, 22))

    def test_random_rejection_samples_until_valid(self):
        # First sample (600, 600) is out of a 500x500 bounds; second (30, 40) ok.
        r = _resolver(rng=_SeqRandom([(600, 600), (30, 40)]),
                      in_bounds_func=lambda x, y, p: 0 <= x < 500 and 0 <= y < 500)
        # planet_size 500x500 means randint(0,499); 600 can't actually occur, but
        # the bounds check still exercises rejection — simulate via size 1000.
        r = _resolver(rng=_SeqRandom([(600, 600), (30, 40)]),
                      planet_size_func=lambda p: (1000, 1000),
                      in_bounds_func=lambda x, y, p: 0 <= x < 500 and 0 <= y < 500)
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"), ("terra", 30, 40))

    def test_random_falls_back_when_no_valid_tile(self):
        # Bounds always False -> exhaust attempts -> planet spawn.
        r = _resolver(rng=_SeqRandom([]), in_bounds_func=lambda x, y, p: False)
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"), ("terra", 250, 250))


class TestRandomSpawnBuildingDistance(unittest.TestCase):
    """A random spawn keeps >= min_building_distance (Chebyshev) from buildings."""

    def test_rejects_tiles_near_a_building(self):
        # A building at (100,100); first sample (105,105) is only 5 away -> reject;
        # second (150,150) is 50 away -> accepted (min distance 20).
        r = _resolver(
            rng=_SeqRandom([(105, 105), (150, 150)]),
            buildings_locator_func=lambda p: [(100, 100)],
            min_building_distance=20,
        )
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"),
                         ("terra", 150, 150))

    def test_accepts_tile_exactly_at_min_distance(self):
        # Distance == min is allowed (>= min). Building at (100,100); (120,100)
        # is exactly 20 away.
        r = _resolver(
            rng=_SeqRandom([(120, 100)]),
            buildings_locator_func=lambda p: [(100, 100)],
            min_building_distance=20,
        )
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"),
                         ("terra", 120, 100))

    def test_relaxes_constraint_when_no_far_tile_found(self):
        # Every sampled tile is next to a building; pass 1 (distance) exhausts,
        # pass 2 (bounds-only) accepts a tile so it never dead-ends. A constant
        # rng always returns 101 -> (101,101), 1 away from the building at
        # (100,100), so no sample ever satisfies the distance constraint.
        class _ConstRandom:
            def randint(self, lo, hi):
                return 101
        r = _resolver(
            rng=_ConstRandom(),
            buildings_locator_func=lambda p: [(100, 100)],
            min_building_distance=20,
        )
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"),
                         ("terra", 101, 101))

    def test_no_locator_means_no_distance_constraint(self):
        # Without a buildings locator, random only checks bounds (back-compat).
        r = _resolver(rng=_SeqRandom([(101, 101)]))
        self.assertEqual(r.resolve(_Player(), SPAWN_RANDOM, "terra"),
                         ("terra", 101, 101))


class TestFallbackAndDegradation(unittest.TestCase):
    def test_unknown_choice_returns_none_without_random_fallback(self):
        r = _resolver(rng=_SeqRandom([(33, 44)]))
        self.assertIsNone(r.resolve(_Player(), "bogus", "terra"))

    def test_named_choice_returns_none_when_resolver_unwired(self):
        r = SpawnResolver()
        self.assertIsNone(r.resolve(_Player(), SPAWN_HQ, "terra"))


class TestOptionAvailable(unittest.TestCase):
    """option_available() — the menu-filtering predicate (no fallback)."""

    def test_random_always_available(self):
        r = SpawnResolver()  # nothing wired
        self.assertTrue(r.option_available(_Player(), SPAWN_RANDOM, "terra"))

    def test_unknown_choice_is_unavailable(self):
        r = SpawnResolver()
        self.assertFalse(r.option_available(_Player(), "bogus", "terra"))

    def test_hq_available_when_locator_hits(self):
        r = _resolver(hq_locator_func=lambda player, p: (10, 20))
        self.assertTrue(r.option_available(_Player(), SPAWN_HQ, "terra"))

    def test_hq_unavailable_when_locator_misses(self):
        r = _resolver(hq_locator_func=lambda player, p: None)
        self.assertFalse(r.option_available(_Player(), SPAWN_HQ, "terra"))

    def test_hq_unavailable_when_no_locator_wired(self):
        r = SpawnResolver()
        self.assertFalse(r.option_available(_Player(), SPAWN_HQ, "terra"))

    def test_respawn_available_when_locator_hits(self):
        r = _resolver(respawn_locator_func=lambda player, p: (5, 5))
        self.assertTrue(r.option_available(_Player(), SPAWN_RESPAWN, "terra"))

    def test_respawn_unavailable_when_no_beacon(self):
        r = _resolver(respawn_locator_func=lambda player, p: None)
        self.assertFalse(r.option_available(_Player(), SPAWN_RESPAWN, "terra"))

    def test_death_available_when_recorded(self):
        r = _resolver()
        p = _Player(death_x=1, death_y=1, death_planet="terra")
        self.assertTrue(r.option_available(p, SPAWN_DEATH, "terra"))

    def test_death_unavailable_when_never_died(self):
        r = _resolver()
        self.assertFalse(r.option_available(_Player(), SPAWN_DEATH, "terra"))

    def test_first_time_player_only_random_available(self):
        # No HQ, no beacon, no death record -> only random.
        r = _resolver(hq_locator_func=lambda player, p: None,
                      respawn_locator_func=lambda player, p: None)
        p = _Player()
        available = [
            opt for opt in (SPAWN_RESPAWN, SPAWN_HQ, SPAWN_DEATH, SPAWN_RANDOM)
            if r.option_available(p, opt, "terra")
        ]
        self.assertEqual(available, [SPAWN_RANDOM])


if __name__ == "__main__":
    unittest.main()
