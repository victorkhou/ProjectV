"""
Unit tests for the pure-function form of the progression helpers.

Passing an explicit ``thresholds`` table makes ``level_for_xp`` /
``xp_for_level`` pure functions with no reach for the module singleton, so they
can be exercised in isolation with no DataRegistry registered and no global
state leaking between tests.
"""

from mygame.world import progression
from mygame.world.constants import MAX_LEVEL


def _flat_table(step: int = 100) -> list[int]:
    """A simple monotonic threshold table: level L needs (L-1)*step XP."""
    return [0] + [(lvl - 1) * step for lvl in range(1, MAX_LEVEL + 1)]


class TestLevelForXpPure:
    def test_maps_xp_to_level_without_singleton(self):
        table = _flat_table(100)
        assert progression.level_for_xp(0, thresholds=table) == 1
        assert progression.level_for_xp(100, thresholds=table) == 2
        assert progression.level_for_xp(250, thresholds=table) == 3

    def test_clamps_to_max_level(self):
        table = _flat_table(100)
        huge = (MAX_LEVEL + 50) * 100
        assert progression.level_for_xp(huge, thresholds=table) == MAX_LEVEL

    def test_none_xp_is_level_one(self):
        assert progression.level_for_xp(None, thresholds=_flat_table()) == 1

    def test_is_pure_no_global_leak(self):
        # Two different explicit tables must not interfere with each other, and
        # must not depend on / mutate any module singleton. At 500 XP: the
        # coarse table (step 1000) is still at level 1; the fine table
        # (step 10) is well up the curve, at level 51.
        coarse = _flat_table(1000)
        fine = _flat_table(10)
        assert progression.level_for_xp(500, thresholds=coarse) == 1
        assert progression.level_for_xp(500, thresholds=fine) == 51


class TestXpForLevelPure:
    def test_returns_threshold_from_explicit_table(self):
        table = _flat_table(100)
        assert progression.xp_for_level(1, thresholds=table) == 0
        assert progression.xp_for_level(3, thresholds=table) == 200

    def test_clamps_level_bounds(self):
        table = _flat_table(100)
        assert progression.xp_for_level(0, thresholds=table) == 0
        assert progression.xp_for_level(MAX_LEVEL + 99, thresholds=table) == table[MAX_LEVEL]
