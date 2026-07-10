"""
Unit tests for SentinelCharacter (PvE NPC bases, Phase 5).

A Sentinel owns an NPC base's buildings/guards. It must: inherit
``get_buildings()`` from CombatCharacter (so ``owner_has_active_hq`` works),
carry the ``is_sentinel`` marker, and be inert as a player — its ``msg`` is a
no-op so it is never "notified" (Requirement 5.6).
"""

import sys
import types
import unittest


def _ensure_evennia_stubs():
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
            return object.__getattribute__(self, "_store").get(key)
        def __setattr__(self, key, value):
            object.__getattribute__(self, "_store").add(key, value)

    class _TagStore:
        def __init__(self):
            self._tags = set()
        def add(self, key, category=None):
            self._tags.add((key, category))
        def get(self, key=None, category=None):
            return [k for (k, c) in self._tags if c == category]
        def remove(self, key, category=None):
            self._tags.discard((key, category))

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.tags = _TagStore()
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

from mygame.typeclasses.sentinel import SentinelCharacter  # noqa: E402


def _make_sentinel(name="Outpost #1") -> SentinelCharacter:
    s = SentinelCharacter(key=name)
    s.at_object_creation()
    return s


class TestSentinel(unittest.TestCase):

    def test_is_a_combat_character(self):
        """Must subclass CombatCharacter so it inherits get_buildings().

        Checked by MRO class-name (robust to the mygame.* vs bare-path dual
        import, which yields distinct class objects for isinstance).
        """
        s = _make_sentinel()
        mro_names = [c.__name__ for c in type(s).__mro__]
        self.assertIn("CombatCharacter", mro_names)
        self.assertTrue(hasattr(s, "get_buildings"))

    def test_is_sentinel_flag_set(self):
        s = _make_sentinel()
        self.assertTrue(s.db.is_sentinel)

    def test_tagged_sentinel(self):
        s = _make_sentinel()
        self.assertIn("sentinel", s.tags.get(category="npc_role"))

    def test_msg_is_noop(self):
        """A sentinel never receives player-facing output (Req 5.6)."""
        s = _make_sentinel()
        # Must not raise and must return None regardless of args.
        self.assertIsNone(s.msg("hello"))
        self.assertIsNone(s.msg(text="x", from_obj=object()))

    def test_get_buildings_default_empty(self):
        """Outside a full Evennia DB, get_buildings returns [] (no crash)."""
        s = _make_sentinel()
        self.assertEqual(s.get_buildings(), [])


if __name__ == "__main__":
    unittest.main()
