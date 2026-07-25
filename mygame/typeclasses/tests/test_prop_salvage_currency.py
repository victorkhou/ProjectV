"""
Property-based tests for the Salvage currency primitive (db.salvage).

Salvage is a per-player counted currency stored as a dedicated
``db.salvage`` int on the character (item-loot-economy design §5):
- add_salvage credits, spend_salvage debits only when covered,
- an overdraft is refused (False, balance unchanged),
- the balance can never go negative,
- a legacy character (attribute unset) reads 0.

**Validates: Requirements 7.3, 12.1**
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

from mygame.typeclasses.characters import CombatCharacter  # noqa: E402

# -------------------------------------------------------------- #
#  Helpers / strategies
# -------------------------------------------------------------- #


def _make_character(name="TestChar") -> CombatCharacter:
    """Create a CombatCharacter with stubbed Evennia internals."""
    char = CombatCharacter(key=name)
    char.at_object_creation()
    return char


amount_st = st.integers(min_value=0, max_value=10_000)
positive_amount_st = st.integers(min_value=1, max_value=10_000)

# An operation is ("add", n) or ("spend", n).
op_st = st.tuples(st.sampled_from(["add", "spend"]), positive_amount_st)
ops_st = st.lists(op_st, min_size=0, max_size=50)


# -------------------------------------------------------------- #
#  Salvage currency accounting
#  **Validates: Requirements 7.3, 12.1**
# -------------------------------------------------------------- #


class TestSalvageCurrencyAccounting(unittest.TestCase):
    """Salvage currency: add credits exactly, spend refuses overdraft,
    the balance never goes negative, legacy characters read 0.

    **Validates: Requirements 7.3, 12.1**
    """

    @given(amount=positive_amount_st)
    @settings(max_examples=25)
    def test_add_salvage_increments_by_exact_amount(self, amount):
        """add_salvage(n) increases the balance by exactly n."""
        char = _make_character()
        before = char.get_salvage()
        char.add_salvage(amount)
        self.assertEqual(char.get_salvage(), before + amount)

    @given(start=amount_st, spend=positive_amount_st)
    @settings(max_examples=25)
    def test_spend_decrements_or_refuses(self, start, spend):
        """spend_salvage succeeds iff covered; a refusal changes nothing."""
        char = _make_character()
        char.add_salvage(start)
        result = char.spend_salvage(spend)
        if start >= spend:
            self.assertTrue(result)
            self.assertEqual(char.get_salvage(), start - spend)
        else:
            self.assertFalse(result)
            self.assertEqual(char.get_salvage(), start)

    @given(ops=ops_st)
    @settings(max_examples=25)
    def test_balance_never_negative_over_any_sequence(self, ops):
        """No sequence of add/spend operations drives the balance below 0,
        and every successful spend was fully covered."""
        char = _make_character()
        for op, amount in ops:
            if op == "add":
                char.add_salvage(amount)
            else:
                before = char.get_salvage()
                ok = char.spend_salvage(amount)
                if ok:
                    self.assertGreaterEqual(before, amount)
            self.assertGreaterEqual(char.get_salvage(), 0)
        self.assertGreaterEqual(char.get_salvage(), 0)

    def test_legacy_character_defaults_to_zero(self):
        """A character without the attribute reads 0 (R12.1)."""
        char = _make_character()
        self.assertIsNone(char.db.salvage)
        self.assertEqual(char.get_salvage(), 0)


if __name__ == "__main__":
    unittest.main()
