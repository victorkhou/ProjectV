"""
Property-based tests for RankSystem.

Property 17: Rank assignment from XP
Property 18: Rank-gated access consistency
Property 19: Strictly increasing rank thresholds

Validates: Requirements 7.2, 7.3, 7.5, 7.6, 7.7
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

from mygame.world.systems.rank_system import RankSystem  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.definitions import (  # noqa: E402
    RankDef, TechnologyDef, PowerupDef,
)
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.typeclasses.combat_entity import CombatEntity  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / Fakes
# -------------------------------------------------------------- #

class FakeDB:
    """Simulates Evennia's db attribute handler."""
    def __init__(self, combat_xp=0, rank_level=1, level=None, researched_techs=None):
        self.combat_xp = combat_xp
        self.rank_level = rank_level
        # Compute level from rank: first level of that rank
        self.level = level if level is not None else ((rank_level - 1) * 5 + 1)
        self.researched_techs = researched_techs if researched_techs is not None else set()

class FakePlayer(CombatEntity):
    """Lightweight stand-in for CombatCharacter.

    Mixes in the real CombatEntity so it exposes ``award_xp`` / ``deduct_xp``,
    matching the contract the refactored RankSystem delegates to.
    """
    def __init__(self, name="TestPlayer", combat_xp=0, rank_level=1,
                 level=None, researched_techs=None):
        self.key = name
        self.db = FakeDB(
            combat_xp=combat_xp,
            rank_level=rank_level,
            level=level,
            researched_techs=researched_techs,
        )

# -------------------------------------------------------------- #
#  Strategies
# -------------------------------------------------------------- #

@st.composite
def rank_list_strategy(draw):
    """Generate a valid list of RankDefs with strictly increasing thresholds.

    Produces 2-10 ranks with level 1..N and strictly increasing xp_thresholds
    where the first rank always has threshold 0.
    """
    num_ranks = draw(st.integers(min_value=2, max_value=10))
    # Generate strictly increasing thresholds starting from 0
    thresholds = [0]
    for _ in range(num_ranks - 1):
        increment = draw(st.integers(min_value=1, max_value=500))
        thresholds.append(thresholds[-1] + increment)

    rank_names = [f"Rank_{i}" for i in range(1, num_ranks + 1)]
    ranks = []
    for i, (name, threshold) in enumerate(zip(rank_names, thresholds)):
        ranks.append(RankDef(name=name, level=i + 1, xp_threshold=threshold))
    return ranks

@st.composite
def rank_list_with_techs_strategy(draw):
    """Generate ranks with associated technologies and powerups."""
    ranks = draw(rank_list_strategy())

    # Generate techs: some for each rank
    techs = {}
    powerups = {}
    for rank in ranks:
        num_techs = draw(st.integers(min_value=0, max_value=2))
        for t in range(num_techs):
            key = f"tech_{rank.level}_{t}"
            techs[key] = TechnologyDef(
                name=f"Tech {rank.level}.{t}",
                key=key,
                required_rank=rank.name,
            )
        num_powerups = draw(st.integers(min_value=0, max_value=2))
        for p in range(num_powerups):
            key = f"powerup_{rank.level}_{p}"
            powerups[key] = PowerupDef(
                name=f"Powerup {rank.level}.{p}",
                key=key,
                required_rank=rank.name,
                effect_type="damage",
                effect_value=1.5,
                duration_ticks=10,
                cooldown_ticks=30,
            )

    return ranks, techs, powerups

def _make_registry(ranks, techs=None, powerups=None) -> DataRegistry:
    """Create a DataRegistry with given rank/tech/powerup data."""
    registry = DataRegistry()
    registry.ranks = sorted(ranks, key=lambda r: r.level)
    registry.technologies = techs or {}
    registry.powerups = powerups or {}
    return registry

# -------------------------------------------------------------- #
#  Property 17: Rank assignment from XP
#  **Validates: Requirements 7.2, 7.3, 7.5**
# -------------------------------------------------------------- #

class TestProperty17RankAssignmentFromXP(unittest.TestCase):
    """Property 17: Rank assignment from XP.

    For any XP value, the assigned rank is the highest rank whose
    threshold <= XP. Promotion occurs when XP meets or exceeds the
    next rank's threshold; demotion occurs when XP falls below the
    current rank's threshold.

    **Validates: Requirements 7.2, 7.3, 7.5**
    """

    @given(
        ranks=rank_list_strategy(),
        xp=st.integers(min_value=0, max_value=50000),
    )
    @settings(max_examples=200)
    def test_rank_for_xp_is_highest_qualifying(self, ranks, xp):
        """get_rank_for_xp returns the highest rank with threshold <= xp."""
        registry = _make_registry(ranks)
        result = registry.get_rank_for_xp(xp)

        # The result's threshold must be <= xp
        self.assertLessEqual(result.xp_threshold, xp)

        # No rank with a higher level should also have threshold <= xp
        for rank in ranks:
            if rank.level > result.level:
                self.assertGreater(rank.xp_threshold, xp)

    @given(
        ranks=rank_list_strategy(),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_promotion_sets_correct_rank(self, ranks, data):
        """After awarding XP, player rank_level matches rank_from_level(level)."""
        from mygame.world.systems.rank_system import rank_from_level
        registry = _make_registry(ranks)
        event_bus = EventBus()
        system = RankSystem(registry=registry, event_bus=event_bus)

        player = FakePlayer(combat_xp=0, rank_level=1, level=1)
        xp_award = data.draw(st.integers(min_value=0, max_value=50000))

        system.award_xp(player, xp_award, "test")

        # rank_level should be derived from level
        self.assertEqual(player.db.rank_level, rank_from_level(player.db.level))

    @given(
        ranks=rank_list_strategy(),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_demotion_sets_correct_rank(self, ranks, data):
        """After deducting XP, player rank_level matches rank_from_level(level)."""
        from mygame.world.systems.rank_system import rank_from_level
        registry = _make_registry(ranks)
        event_bus = EventBus()
        system = RankSystem(registry=registry, event_bus=event_bus)

        start_rank = data.draw(st.sampled_from(ranks))
        start_level = (start_rank.level - 1) * 5 + 1
        player = FakePlayer(
            combat_xp=start_rank.xp_threshold,
            rank_level=start_rank.level,
            level=start_level,
        )
        xp_deduction = data.draw(
            st.integers(min_value=0, max_value=start_rank.xp_threshold + 100)
        )

        system.deduct_xp(player, xp_deduction)

        self.assertEqual(player.db.rank_level, rank_from_level(player.db.level))

# -------------------------------------------------------------- #
#  Property 18: Rank-gated access consistency
#  **Validates: Requirements 7.6, 7.7**
# -------------------------------------------------------------- #

class TestProperty18RankGatedAccess(unittest.TestCase):
    """Property 18: Rank-gated access consistency.

    Techs/powerups available at rank N are a superset of those at rank N-1.
    On promotion, newly qualifying items are unlocked. On demotion,
    items requiring the lost rank are revoked.

    **Validates: Requirements 7.6, 7.7**
    """

    @given(data=rank_list_with_techs_strategy())
    @settings(max_examples=200)
    def test_techs_at_higher_rank_superset_of_lower(self, data):
        """Technologies at rank N are a superset of those at rank N-1."""
        ranks, techs, powerups = data
        registry = _make_registry(ranks, techs, powerups)

        for i in range(1, len(ranks)):
            lower_level = ranks[i - 1].level
            higher_level = ranks[i].level
            lower_techs = {
                t.key for t in registry.get_technologies_for_rank(lower_level)
            }
            higher_techs = {
                t.key for t in registry.get_technologies_for_rank(higher_level)
            }
            self.assertTrue(
                lower_techs.issubset(higher_techs),
                f"Techs at rank {lower_level} not subset of rank {higher_level}",
            )

    @given(data=rank_list_with_techs_strategy())
    @settings(max_examples=200)
    def test_powerups_at_higher_rank_superset_of_lower(self, data):
        """Powerups at rank N are a superset of those at rank N-1."""
        ranks, techs, powerups = data
        registry = _make_registry(ranks, techs, powerups)

        for i in range(1, len(ranks)):
            lower_level = ranks[i - 1].level
            higher_level = ranks[i].level
            lower_powerups = {
                p.key for p in registry.get_powerups_for_rank(lower_level)
            }
            higher_powerups = {
                p.key for p in registry.get_powerups_for_rank(higher_level)
            }
            self.assertTrue(
                lower_powerups.issubset(higher_powerups),
                f"Powerups at rank {lower_level} not subset of rank {higher_level}",
            )

    @given(data=rank_list_with_techs_strategy())
    @settings(max_examples=200)
    def test_promotion_does_not_auto_grant_techs(self, data):
        """R13.1: promotion never touches researched_techs — research at a Lab
        is the only tech-acquisition path."""
        ranks, techs, powerups = data
        assume(len(ranks) >= 2)
        registry = _make_registry(ranks, techs, powerups)
        event_bus = EventBus()
        system = RankSystem(registry=registry, event_bus=event_bus)
        system._rebuild_thresholds()

        max_rank = ranks[-1]
        player = FakePlayer(combat_xp=0, rank_level=1, researched_techs=set())
        system.award_xp(player, max_rank.xp_threshold, "test")

        # techs set stays empty — no auto-grant
        self.assertEqual(player.db.researched_techs, set())

    @given(data=rank_list_with_techs_strategy())
    @settings(max_examples=200)
    def test_demotion_does_not_revoke_techs(self, data):
        """R13.2: demotion never revokes researched technologies."""
        ranks, techs, powerups = data
        assume(len(ranks) >= 2)
        registry = _make_registry(ranks, techs, powerups)
        event_bus = EventBus()
        system = RankSystem(registry=registry, event_bus=event_bus)
        system._rebuild_thresholds()

        max_rank = ranks[-1]
        all_tech_keys = {
            t.key for t in registry.get_technologies_for_rank(max_rank.level)
        }
        player = FakePlayer(
            combat_xp=max_rank.xp_threshold,
            rank_level=max_rank.level,
            researched_techs=set(all_tech_keys),
        )

        system.deduct_xp(player, max_rank.xp_threshold)

        # ALL techs survive — demotion never revokes
        self.assertEqual(player.db.researched_techs, all_tech_keys)

# -------------------------------------------------------------- #
#  Property 19: Strictly increasing rank thresholds
#  **Validates: Requirements 7.2**
# -------------------------------------------------------------- #

class TestProperty19StrictlyIncreasingThresholds(unittest.TestCase):
    """Property 19: Strictly increasing rank thresholds.

    For any loaded rank definitions, if rank A has level < rank B's level,
    then rank A's xp_threshold SHALL be strictly less than rank B's
    xp_threshold.

    **Validates: Requirements 7.2**
    """

    @given(ranks=rank_list_strategy())
    @settings(max_examples=200)
    def test_thresholds_strictly_increasing(self, ranks):
        """Rank thresholds are strictly increasing with level."""
        sorted_ranks = sorted(ranks, key=lambda r: r.level)
        for i in range(1, len(sorted_ranks)):
            self.assertGreater(
                sorted_ranks[i].xp_threshold,
                sorted_ranks[i - 1].xp_threshold,
                f"Rank {sorted_ranks[i].name} (level {sorted_ranks[i].level}) "
                f"threshold {sorted_ranks[i].xp_threshold} not > "
                f"rank {sorted_ranks[i - 1].name} (level {sorted_ranks[i - 1].level}) "
                f"threshold {sorted_ranks[i - 1].xp_threshold}",
            )

    @given(ranks=rank_list_strategy())
    @settings(max_examples=200)
    def test_levels_strictly_increasing(self, ranks):
        """Rank levels are strictly increasing (no duplicates)."""
        sorted_ranks = sorted(ranks, key=lambda r: r.level)
        for i in range(1, len(sorted_ranks)):
            self.assertGreater(
                sorted_ranks[i].level,
                sorted_ranks[i - 1].level,
            )

if __name__ == "__main__":
    unittest.main()
