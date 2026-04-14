"""
Property-based tests for resource round-trip, deduction rejection,
and rank resolution.

**Property 6: Resource Add/Deduct Round-Trip**
For any CombatCharacter resource state, resource type, and positive amount,
adding the amount and then deducting the same amount SHALL return the resource
to its original value.
**Validates: Requirements 3.7**

**Property 7: Resource Deduction Rejection Preserves State**
For any CombatCharacter resource state and cost dict where at least one
resource cost exceeds the player's current stock, deduct_resources SHALL
return failure and the resource state SHALL remain unchanged.
**Validates: Requirements 3.6**

**Property 8: Rank Resolution Is a Total Function**
For any XP value in [0, 120000], get_rank_for_xp(xp) SHALL return exactly
one RankDef where rank.xp_threshold <= xp and either the rank is the highest
rank or next_rank.xp_threshold > xp.
**Validates: Requirements 4.2, 4.3, 4.4, 4.9, 4.13**
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

from mygame.typeclasses.characters import CombatCharacter, RESOURCE_TYPES  # noqa: E402
from mygame.world.definitions import RankDef  # noqa: E402
from mygame.world.data_registry import DataRegistry  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_character(name="TestChar") -> CombatCharacter:
    """Create a CombatCharacter with stubbed Evennia internals."""
    char = CombatCharacter(key=name)
    char.at_object_creation()
    return char


def _make_registry_with_ranks() -> DataRegistry:
    """Create a DataRegistry with ranks populated from ranks.yaml."""
    import os
    import yaml

    registry = DataRegistry()
    yaml_path = os.path.join(
        os.path.dirname(__file__), os.pardir, "data", "definitions", "ranks.yaml"
    )
    with open(yaml_path, "r") as f:
        raw = yaml.safe_load(f)
    registry._populate_ranks(raw)
    return registry

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

resource_type_st = st.sampled_from(list(RESOURCE_TYPES))
positive_amount_st = st.integers(min_value=1, max_value=10_000)

# Strategy for initial resource amounts (non-negative)
resource_state_st = st.dictionaries(
    keys=st.sampled_from(list(RESOURCE_TYPES)),
    values=st.integers(min_value=0, max_value=10_000),
    min_size=len(RESOURCE_TYPES),
    max_size=len(RESOURCE_TYPES),
)


@st.composite
def costs_exceeding_stock(draw):
    """Generate a resource state and cost dict where at least one cost exceeds stock.

    Returns (resource_state_dict, costs_dict).
    """
    # Pick 1-4 resource types for the cost
    num = draw(st.integers(min_value=1, max_value=4))
    chosen = draw(
        st.lists(resource_type_st, min_size=num, max_size=num, unique=True)
    )
    # Build a resource state with known amounts
    state = {r: 0 for r in RESOURCE_TYPES}
    for r in RESOURCE_TYPES:
        state[r] = draw(st.integers(min_value=0, max_value=500))

    # Build costs — ensure at least one exceeds the stock
    costs = {}
    exceeded = False
    for i, r in enumerate(chosen):
        if i == 0 and not exceeded:
            # Force the first resource to exceed stock
            costs[r] = state[r] + draw(st.integers(min_value=1, max_value=500))
            exceeded = True
        else:
            costs[r] = draw(st.integers(min_value=1, max_value=500))

    return state, costs


# XP strategy covering the full rank range
xp_st = st.integers(min_value=0, max_value=120_000)


# ================================================================== #
#  Property 6: Resource Add/Deduct Round-Trip
#  **Validates: Requirements 3.7**
# ================================================================== #

class TestProperty6ResourceRoundTrip(unittest.TestCase):
    """Property 6: Resource Add/Deduct Round-Trip.

    For any CombatCharacter resource state, resource type, and positive
    amount, adding the amount and then deducting the same amount SHALL
    return the resource to its original value.

    **Validates: Requirements 3.7**
    """

    @given(rtype=resource_type_st, amount=positive_amount_st)
    @settings(max_examples=100)
    def test_add_then_deduct_returns_to_original(self, rtype, amount):
        """Adding then deducting the same amount restores original value."""
        char = _make_character()
        original = char.get_resource(rtype)

        char.add_resource(rtype, amount)
        result = char.deduct_resources({rtype: amount})

        self.assertTrue(result, "Deduction should succeed after adding same amount")
        self.assertEqual(
            char.get_resource(rtype), original,
            f"Resource {rtype} should return to {original} after add/deduct round-trip"
        )

    @given(
        rtype=resource_type_st,
        initial_extra=st.integers(min_value=0, max_value=5000),
        amount=positive_amount_st,
    )
    @settings(max_examples=100)
    def test_add_then_deduct_with_varied_initial_state(self, rtype, initial_extra, amount):
        """Round-trip holds regardless of initial resource amount."""
        char = _make_character()
        # Set up a varied initial state
        char.add_resource(rtype, initial_extra)
        original = char.get_resource(rtype)

        char.add_resource(rtype, amount)
        result = char.deduct_resources({rtype: amount})

        self.assertTrue(result)
        self.assertEqual(char.get_resource(rtype), original)


# ================================================================== #
#  Property 7: Resource Deduction Rejection Preserves State
#  **Validates: Requirements 3.6**
# ================================================================== #

class TestProperty7DeductionRejectionPreservesState(unittest.TestCase):
    """Property 7: Resource Deduction Rejection Preserves State.

    For any CombatCharacter resource state and cost dict where at least
    one resource cost exceeds the player's current stock, deduct_resources
    SHALL return failure and the resource state SHALL remain unchanged.

    **Validates: Requirements 3.6**
    """

    @given(data=costs_exceeding_stock())
    @settings(max_examples=100)
    def test_insufficient_deduction_returns_false_and_preserves_state(self, data):
        """Failed deduction returns False and leaves all resources unchanged."""
        state, costs = data
        char = _make_character()

        # Set the character's resources to the generated state
        for r, amt in state.items():
            char.db.resources[r] = amt

        # Snapshot before
        before = {r: char.get_resource(r) for r in RESOURCE_TYPES}

        result = char.deduct_resources(costs)

        self.assertFalse(result, "Deduction should fail when cost exceeds stock")
        for r in RESOURCE_TYPES:
            self.assertEqual(
                char.get_resource(r), before[r],
                f"Resource {r} should be unchanged after failed deduction"
            )


# ================================================================== #
#  Property 8: Rank Resolution Is a Total Function
#  **Validates: Requirements 4.2, 4.3, 4.4, 4.9, 4.13**
# ================================================================== #

# The 12 canonical rank thresholds from ranks.yaml
RANK_THRESHOLDS = [0, 200, 600, 1500, 3500, 7000, 12000, 20000, 35000, 55000, 80000, 120000]


class TestProperty8RankResolutionTotalFunction(unittest.TestCase):
    """Property 8: Rank Resolution Is a Total Function.

    For any XP value in [0, 120000], get_rank_for_xp(xp) SHALL return
    exactly one RankDef where rank.xp_threshold <= xp and either the
    rank is the highest rank or next_rank.xp_threshold > xp.

    **Validates: Requirements 4.2, 4.3, 4.4, 4.9, 4.13**
    """

    @classmethod
    def setUpClass(cls):
        cls.registry = _make_registry_with_ranks()

    @given(xp=xp_st)
    @settings(max_examples=100)
    def test_rank_resolution_returns_exactly_one_rank(self, xp):
        """get_rank_for_xp returns exactly one RankDef for any XP in range."""
        rank = self.registry.get_rank_for_xp(xp)

        # Must return a RankDef (check by class name to avoid import-path identity issues)
        self.assertEqual(type(rank).__name__, "RankDef")
        self.assertTrue(hasattr(rank, "xp_threshold"))
        self.assertTrue(hasattr(rank, "level"))
        self.assertTrue(hasattr(rank, "name"))

        # rank.xp_threshold <= xp
        self.assertLessEqual(
            rank.xp_threshold, xp,
            f"Rank {rank.name} threshold {rank.xp_threshold} should be <= xp {xp}"
        )

        # Either this is the highest rank, or the next rank's threshold > xp
        ranks = self.registry.ranks
        rank_idx = None
        for i, r in enumerate(ranks):
            if r.level == rank.level:
                rank_idx = i
                break

        self.assertIsNotNone(rank_idx, f"Rank {rank.name} not found in registry")

        if rank_idx < len(ranks) - 1:
            next_rank = ranks[rank_idx + 1]
            self.assertGreater(
                next_rank.xp_threshold, xp,
                f"Next rank {next_rank.name} threshold {next_rank.xp_threshold} "
                f"should be > xp {xp} (current rank: {rank.name})"
            )

    @given(xp=xp_st)
    @settings(max_examples=100)
    def test_rank_resolution_is_deterministic(self, xp):
        """Calling get_rank_for_xp twice with same XP returns same rank."""
        rank1 = self.registry.get_rank_for_xp(xp)
        rank2 = self.registry.get_rank_for_xp(xp)
        self.assertEqual(rank1.level, rank2.level)
        self.assertEqual(rank1.name, rank2.name)

    @given(xp=xp_st)
    @settings(max_examples=100)
    def test_rank_thresholds_are_boundaries(self, xp):
        """The resolved rank's threshold is the greatest threshold <= xp."""
        rank = self.registry.get_rank_for_xp(xp)

        # Verify no other rank has a higher threshold that's still <= xp
        for r in self.registry.ranks:
            if r.xp_threshold <= xp:
                self.assertLessEqual(
                    r.level, rank.level,
                    f"Rank {r.name} (threshold {r.xp_threshold}) is <= xp {xp} "
                    f"but has level {r.level} > resolved rank {rank.name} level {rank.level}"
                )


if __name__ == "__main__":
    unittest.main()
