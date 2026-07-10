"""
Shared Evennia stub setup for all mygame tests.

This conftest.py runs before any test collection, ensuring that
Evennia module stubs are installed in sys.modules with rich enough
implementations to support all typeclasses (Building, CombatCharacter,
PlanetRoom, GameItem, etc.).

This prevents stub conflicts caused by test collection order.
"""

import sys
import types


def _ensure_evennia_stubs():
    """Install comprehensive Evennia stubs into sys.modules."""
    # Escape hatch for the live-boot smoke test: when EVENNIA_REAL_BOOT=1 the
    # caller wants the REAL Evennia + a Django test DB (see
    # tests/test_live_boot_smoke.py), so never install stubs. This keeps the
    # fast stubbed suite (the default) and the slow real-boot test from
    # clobbering each other's sys.modules["evennia"].
    import os
    if os.environ.get("EVENNIA_REAL_BOOT") == "1":
        return

    # If real Evennia is installed, don't overwrite
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

    # --- Rich attribute/db stubs ---

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

    class DefaultObject:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.tags = _TagStore()
            self.key = kwargs.get("key", "")
            self.location = None

    class DefaultCharacter:
        def __init__(self, **kwargs):
            self._attr_store = _AttrStore()
            self.attributes = self._attr_store
            self.db = _DbProxy(self._attr_store)
            self.tags = _TagStore()
            self.key = kwargs.get("key", "")
        def at_object_creation(self):
            pass
        def at_post_login(self, session=None, **kwargs):
            pass

    class DefaultRoom:
        def at_object_receive(self, moved_obj, source_location, **kwargs):
            pass

    class Command:
        key = ""
        aliases = []
        locks = ""
        help_category = "General"
        def func(self):
            pass

    class DefaultScript:
        pass

    # --- Register all stubs ---

    _mod("evennia")
    _mod("evennia.objects")
    _mod("evennia.objects.objects", {
        "DefaultObject": DefaultObject,
        "DefaultRoom": DefaultRoom,
        "DefaultCharacter": DefaultCharacter,
    })
    _mod("evennia.commands")
    _mod("evennia.commands.command", {
        "Command": Command,
    })
    _mod("evennia.commands.cmdset")
    _mod("evennia.utils")
    _mod("evennia.utils.utils")
    _mod("evennia.utils.logger")
    _mod("evennia.scripts")
    _mod("evennia.scripts.scripts", {
        "DefaultScript": DefaultScript,
    })

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


# Run stubs at import time (before any test collection)
_ensure_evennia_stubs()
