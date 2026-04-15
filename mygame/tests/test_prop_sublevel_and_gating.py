"""
Property-based tests for sub-level XP distribution and planet rank gating.

**Property 9: Sub-Level XP Distribution**
For any two consecutive rank thresholds T1 and T2, the 5 sub-level boundaries
SHALL be evenly spaced at intervals of (T2 - T1) / 5, such that Level N starts
at T1 + (N-1) × (T2-T1)/5.
**Validates: Requirements 4b.2**

**Property 3: Planet Rank Gating**
For any player rank level and planet rank requirement, travel to the planet
SHALL be allowed if and only if the player's rank level is greater than or
equal to the planet's rank_requirement.
**Validates: Requirements 1.4, 6.5**
"""

import os
import sys
import types
import unittest

import yaml
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

    class _AttrStore:
        def __init__(self):
            self._data = {}
        def get(self, key, default=None, **kw):
            return self._data.get(key, default)
        def add(self, key, value, **kw):
            self._data[key] = value
        def has(self, key):
            return key in self._data

    class _DbProxy:
        def __init__(self, store):
            object.__setattr__(self, "_store", store)
        def __getattr__(self, key):
            store = object.__getattribute__(self, "_store")
            return store.get(key)
        def __setattr__(self, key, value):
            store = object.__getattribute__(self, "_store")
            store.add(key, value)

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.key = kwargs.get("key", "TestChar")
        def at_object_creation(self):
            pass
        def at_post_login(self, session, **kwargs):
            pass

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultCharacter": DefaultCharacter,
        "DefaultObject": type("DefaultObject", (), {}),
        "DefaultRoom": type("DefaultRoom", (), {}),
    })
    _mod("evennia.commands")
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

_ensure_evennia_stubs()

from mygame.typeclasses.characters import CombatCharacter  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402
from mygame.world.coordinate.planet_registry import PlanetRegistry  # noqa: E402
from mygame.world.event_bus import EventBus  # noqa: E402
from mygame.world.systems.rank_system import RankSystem  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers — load real YAML data
# -------------------------------------------------------------- #

_DATA_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "data", "definitions")


def _load_registry_with_ranks() -> DataRegistry:
    """Create a DataRegistry populated with ranks from ranks.yaml."""
    registry = DataRegistry()
    with open(os.path.join(_DATA_DIR, "ranks.yaml"), "r") as f:
        raw = yaml.safe_load(f)
    registry._populate_ranks(raw)
    return registry


def _load_planet_registry() -> PlanetRegistry:
    """Load PlanetRegistry from planets.yaml."""
    pr = PlanetRegistry()
    pr.load_from_yaml(os.path.join(_DATA_DIR, "planets.yaml"))
    return pr


def _make_player(rank_level: int = 1, combat_xp: int = 0) -> CombatCharacter:
    """Create a stubbed CombatCharacter with given rank and XP.

    Sets db.level to the first level of the given rank for consistency
    with the level-based rank system.
    """
    from mygame.world.systems.rank_system import level_range_for_rank
    char = CombatCharacter(key="TestPlayer")
    char.at_object_creation()
    char.db.rank_level = rank_level
    # Set level to first level of this rank
    level, _ = level_range_for_rank(rank_level)
    char.db.level = level
    char.db.combat_xp = combat_xp
    return char


# Pre-load data once for all tests
_REGISTRY = _load_registry_with_ranks()
_PLANET_REGISTRY = _load_planet_registry()

# Extract rank info for strategies
_RANKS = _REGISTRY.ranks  # sorted by level ascending
_RANK_THRESHOLDS = [r.xp_threshold for r in _RANKS]
# Pairs of consecutive ranks (for sub-level testing)
_RANK_PAIRS = list(zip(_RANKS[:-1], _RANKS[1:]))

# Planet keys and their rank requirements
_PLANET_KEYS = _PLANET_REGISTRY.list_planets()
_PLANET_RANK_REQS = {
    pk: _PLANET_REGISTRY.get_space(pk).rank_requirement for pk in _PLANET_KEYS
}

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

# Index into consecutive rank pairs
rank_pair_index_st = st.integers(min_value=0, max_value=len(_RANK_PAIRS) - 1)

# Player rank levels (1 through max rank level)
rank_level_st = st.integers(min_value=1, max_value=_RANKS[-1].level)

# Planet key strategy
planet_key_st = st.sampled_from(_PLANET_KEYS)


# ================================================================== #
#  Property 9: Sub-Level XP Distribution
#  **Validates: Requirements 4b.2**
# ================================================================== #

class TestProperty9SubLevelXPDistribution(unittest.TestCase):
    """Property 9: Sub-Level XP Distribution.

    For any two consecutive rank thresholds T1 and T2, the 5 sub-level
    boundaries SHALL be evenly spaced at intervals of (T2 - T1) / 5,
    such that Level N starts at T1 + (N-1) × (T2-T1)/5.

    **Validates: Requirements 4b.2**
    """

    def setUp(self):
        self.event_bus = EventBus()
        self.rank_system = RankSystem(_REGISTRY, self.event_bus)

    @given(pair_idx=rank_pair_index_st, sub_level=st.integers(min_value=1, max_value=5))
    @settings(max_examples=100)
    def test_sub_level_boundaries_are_evenly_spaced(self, pair_idx, sub_level):
        """XP at the exact boundary of sub-level N yields that sub-level."""
        rank_low, rank_high = _RANK_PAIRS[pair_idx]
        t1 = rank_low.xp_threshold
        t2 = rank_high.xp_threshold
        interval = (t2 - t1) / 5

        # XP at the start of sub-level N: T1 + (N-1) * interval
        boundary_xp = int(t1 + (sub_level - 1) * interval)

        # Ensure XP stays within this rank's range
        assume(boundary_xp < t2)

        player = _make_player(rank_level=rank_low.level, combat_xp=boundary_xp)
        # Set the level to match the sub-level within this rank
        from mygame.world.systems.rank_system import level_range_for_rank
        base_level, _ = level_range_for_rank(rank_low.level)
        player.db.level = base_level + (sub_level - 1)

        result = self.rank_system.get_sub_level(player)

        self.assertEqual(
            result, sub_level,
            f"At XP {boundary_xp} (rank {rank_low.name}, T1={t1}, T2={t2}, "
            f"interval={interval}), expected sub-level {sub_level}, got {result}"
        )

    @given(
        pair_idx=rank_pair_index_st,
        fraction=st.floats(min_value=0.0, max_value=0.9999, allow_nan=False,
                           allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_random_xp_within_rank_yields_correct_sub_level(self, pair_idx, fraction):
        """Random XP within a rank range maps to the correct sub-level per formula."""
        rank_low, rank_high = _RANK_PAIRS[pair_idx]
        t1 = rank_low.xp_threshold
        t2 = rank_high.xp_threshold
        interval = (t2 - t1) / 5

        # Generate XP within [T1, T2)
        xp = int(t1 + fraction * (t2 - t1))
        assume(t1 <= xp < t2)

        # Expected sub-level from formula
        xp_into_rank = xp - t1
        expected_level = min(int(xp_into_rank // interval) + 1, 5)

        player = _make_player(rank_level=rank_low.level, combat_xp=xp)
        # Compute the actual player level from XP using the rank system
        computed_level = self.rank_system.level_for_xp(xp)
        player.db.level = computed_level

        result = self.rank_system.get_sub_level(player)

        self.assertEqual(
            result, expected_level,
            f"At XP {xp} (rank {rank_low.name}, T1={t1}, T2={t2}, "
            f"interval={interval}), expected sub-level {expected_level}, got {result}"
        )

    @given(pair_idx=rank_pair_index_st)
    @settings(max_examples=100)
    def test_sub_level_at_rank_start_is_1(self, pair_idx):
        """At exactly the rank threshold XP, sub-level should be 1."""
        rank_low, _ = _RANK_PAIRS[pair_idx]
        t1 = rank_low.xp_threshold

        player = _make_player(rank_level=rank_low.level, combat_xp=t1)
        result = self.rank_system.get_sub_level(player)

        self.assertEqual(
            result, 1,
            f"At rank start XP {t1} (rank {rank_low.name}), "
            f"expected sub-level 1, got {result}"
        )


# ================================================================== #
#  Property 3: Planet Rank Gating
#  **Validates: Requirements 1.4, 6.5**
# ================================================================== #

class TestProperty3PlanetRankGating(unittest.TestCase):
    """Property 3: Planet Rank Gating.

    For any player rank level and planet rank requirement, travel to the
    planet SHALL be allowed if and only if the player's rank level is
    greater than or equal to the planet's rank_requirement.

    **Validates: Requirements 1.4, 6.5**
    """

    def setUp(self):
        self.event_bus = EventBus()
        self.rank_system = RankSystem(
            _REGISTRY, self.event_bus, planet_registry=_PLANET_REGISTRY
        )

    @given(rank_level=rank_level_st, planet_key=planet_key_st)
    @settings(max_examples=100)
    def test_access_iff_rank_meets_requirement(self, rank_level, planet_key):
        """Player can access planet iff level >= planet's rank_requirement."""
        planet_req = _PLANET_RANK_REQS[planet_key]

        # Find a valid XP for this rank level
        rank_def = None
        for r in _RANKS:
            if r.level == rank_level:
                rank_def = r
                break
        assert rank_def is not None

        player = _make_player(rank_level=rank_level, combat_xp=rank_def.xp_threshold)
        result = self.rank_system.can_access_planet(player, planet_key)

        # Player level is the first level of this rank
        from mygame.world.systems.rank_system import level_range_for_rank
        player_level, _ = level_range_for_rank(rank_level)
        expected = player_level >= planet_req
        self.assertEqual(
            result, expected,
            f"Player level {player_level} (rank {rank_level}) vs planet '{planet_key}' "
            f"(req={planet_req}): expected {expected}, got {result}"
        )

    @given(planet_key=planet_key_st)
    @settings(max_examples=100)
    def test_exact_requirement_rank_grants_access(self, planet_key):
        """A player at exactly the planet's required level can access it."""
        planet_req = _PLANET_RANK_REQS[planet_key]

        # planet_req is now a level requirement — find which rank that maps to
        from mygame.world.systems.rank_system import rank_from_level
        req_rank = rank_from_level(planet_req)

        rank_def = None
        for r in _RANKS:
            if r.level == req_rank:
                rank_def = r
                break
        assume(rank_def is not None)

        player = _make_player(rank_level=req_rank, combat_xp=rank_def.xp_threshold)
        # Override level to exactly the requirement
        player.db.level = planet_req
        result = self.rank_system.can_access_planet(player, planet_key)

        self.assertTrue(
            result,
            f"Player at exact requirement level {planet_req} should access "
            f"planet '{planet_key}'"
        )

    @given(planet_key=planet_key_st)
    @settings(max_examples=100)
    def test_below_requirement_rank_denies_access(self, planet_key):
        """A player below the planet's required level cannot access it."""
        planet_req = _PLANET_RANK_REQS[planet_key]
        assume(planet_req > 1)  # Skip if requirement is 1

        # Set player level to one below the requirement
        below_level = planet_req - 1
        from mygame.world.systems.rank_system import rank_from_level
        below_rank = rank_from_level(below_level)

        rank_def = None
        for r in _RANKS:
            if r.level == below_rank:
                rank_def = r
                break
        assume(rank_def is not None)

        player = _make_player(rank_level=below_rank, combat_xp=rank_def.xp_threshold)
        player.db.level = below_level
        result = self.rank_system.can_access_planet(player, planet_key)

        self.assertFalse(
            result,
            f"Player at level {below_level} should NOT access planet "
            f"'{planet_key}' (req={planet_req})"
        )


if __name__ == "__main__":
    unittest.main()
