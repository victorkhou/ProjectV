"""
Property-based tests for CombatCharacter resource trait accounting.

Property 3: Resource trait accounting

Validates: Requirements 2.4
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

    # Minimal DefaultCharacter stub with db/attributes support
    class _AttrStore:
        """Minimal Evennia-like Attribute store."""
        def __init__(self):
            self._data = {}

        def get(self, key, default=None, **kw):
            return self._data.get(key, default)

        def add(self, key, value, **kw):
            self._data[key] = value

        def has(self, key):
            return key in self._data

    class _DbProxy:
        """Proxy that reads/writes through an _AttrStore."""
        def __init__(self, store):
            object.__setattr__(self, "_store", store)

        def __getattr__(self, key):
            store = object.__getattribute__(self, "_store")
            return store.get(key)

        def __setattr__(self, key, value):
            store = object.__getattribute__(self, "_store")
            store.add(key, value)

    class DefaultCharacter:
        """Lightweight stub for Evennia's DefaultCharacter."""
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

# -------------------------------------------------------------- #
#  Helpers
# -------------------------------------------------------------- #

def _make_character(name="TestChar") -> CombatCharacter:
    """Create a CombatCharacter with stubbed Evennia internals."""
    char = CombatCharacter(key=name)
    char.at_object_creation()
    return char

# -------------------------------------------------------------- #
#  Hypothesis strategies
# -------------------------------------------------------------- #

resource_type_st = st.sampled_from(list(RESOURCE_TYPES))
amount_st = st.integers(min_value=0, max_value=10_000)
positive_amount_st = st.integers(min_value=1, max_value=10_000)

@st.composite
def costs_strategy(draw):
    """Generate a dict of resource costs (1-4 resource types, positive amounts)."""
    num = draw(st.integers(min_value=1, max_value=4))
    chosen = draw(
        st.lists(resource_type_st, min_size=num, max_size=num, unique=True)
    )
    costs = {r: draw(st.integers(min_value=1, max_value=500)) for r in chosen}
    return costs

# -------------------------------------------------------------- #
#  Property 3: Resource trait accounting
#  **Validates: Requirements 2.4**
# -------------------------------------------------------------- #

class TestProperty3ResourceAccounting(unittest.TestCase):
    """Property 3: Resource trait accounting.

    - add_resource(type, amount) increases the resource by exactly amount
    - deduct_resources only succeeds when all resources are sufficient
    - deduct_resources decreases each resource by exactly the cost amount
    - has_resources returns True iff all resources are >= costs
    - Resources never go negative

    **Validates: Requirements 2.4**
    """

    @given(rtype=resource_type_st, amount=positive_amount_st)
    @settings(max_examples=100)
    def test_add_resource_increases_by_exact_amount(self, rtype, amount):
        """add_resource(type, amount) increases the resource by exactly amount."""
        char = _make_character()
        before = char.get_resource(rtype)
        char.add_resource(rtype, amount)
        after = char.get_resource(rtype)
        self.assertEqual(after, before + amount)

    @given(costs=costs_strategy())
    @settings(max_examples=100)
    def test_deduct_resources_succeeds_when_sufficient(self, costs):
        """deduct_resources succeeds when all resources are sufficient."""
        char = _make_character()
        # Give enough resources
        for r, amt in costs.items():
            char.add_resource(r, amt)
        result = char.deduct_resources(costs)
        self.assertTrue(result)

    @given(costs=costs_strategy())
    @settings(max_examples=100)
    def test_deduct_resources_fails_when_insufficient(self, costs):
        """deduct_resources fails when any resource is insufficient."""
        char = _make_character()
        # Zero out all resources first so we control the exact amounts
        for r in RESOURCE_TYPES:
            char.db.resources[r] = 0
        # Give less than needed for the first resource
        first_r = list(costs.keys())[0]
        char.add_resource(first_r, costs[first_r] - 1)
        # Give enough for the rest
        for r, amt in costs.items():
            if r != first_r:
                char.add_resource(r, amt)
        result = char.deduct_resources(costs)
        self.assertFalse(result)

    @given(costs=costs_strategy())
    @settings(max_examples=100)
    def test_deduct_resources_decreases_by_exact_cost(self, costs):
        """deduct_resources decreases each resource by exactly the cost amount."""
        char = _make_character()
        # Give extra resources so deduction succeeds
        extra = 100
        for r, amt in costs.items():
            char.add_resource(r, amt + extra)
        before = {r: char.get_resource(r) for r in costs}
        char.deduct_resources(costs)
        for r, amt in costs.items():
            self.assertEqual(char.get_resource(r), before[r] - amt)

    @given(costs=costs_strategy())
    @settings(max_examples=100)
    def test_has_resources_true_when_sufficient(self, costs):
        """has_resources returns True when all resources >= costs."""
        char = _make_character()
        for r, amt in costs.items():
            char.add_resource(r, amt)
        self.assertTrue(char.has_resources(costs))

    @given(costs=costs_strategy())
    @settings(max_examples=100)
    def test_has_resources_false_when_insufficient(self, costs):
        """has_resources returns False when any resource < cost."""
        char = _make_character()
        # Zero out all resources so costs (>= 1) are always insufficient
        for r in RESOURCE_TYPES:
            char.db.resources[r] = 0
        self.assertFalse(char.has_resources(costs))

    @given(costs=costs_strategy())
    @settings(max_examples=100)
    def test_failed_deduction_does_not_change_resources(self, costs):
        """A failed deduct_resources does not modify any resource."""
        char = _make_character()
        # Zero out all resources so costs (>= 1) will always fail
        for r in RESOURCE_TYPES:
            char.db.resources[r] = 0
        before = {r: char.get_resource(r) for r in RESOURCE_TYPES}
        char.deduct_resources(costs)
        for r in RESOURCE_TYPES:
            self.assertEqual(char.get_resource(r), before[r])

    @given(
        rtype=resource_type_st,
        add_amt=positive_amount_st,
        deduct_amt=positive_amount_st,
    )
    @settings(max_examples=100)
    def test_resources_never_go_negative(self, rtype, add_amt, deduct_amt):
        """Resources never go negative after any sequence of operations."""
        char = _make_character()
        char.add_resource(rtype, add_amt)
        char.deduct_resources({rtype: deduct_amt})
        self.assertGreaterEqual(char.get_resource(rtype), 0)

if __name__ == "__main__":
    unittest.main()
