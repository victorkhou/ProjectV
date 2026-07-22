"""
Unit tests for the ``GameTickScript._get_systems`` contract.

The tick script obtains its systems from the ``world.services`` facade:
``_get_systems()`` returns the installed dict itself (identity, not a copy)
when the mapping is non-empty, and None — the tick loop's skip signal — when
the facade holds an empty dict or was never installed.

Injection goes through ``services.override`` so no installed state leaks
between tests; the never-installed state is exercised by snapshotting and
restoring ``services._systems`` around a reset to None (the same pattern as
``test_services.py``).

Validates: Requirements 7.8.
"""

import unittest

from typeclasses.scripts import GameTickScript
from world import services


class TestGetSystemsInstalledNonEmpty(unittest.TestCase):
    """_get_systems returns the installed dict itself when non-empty."""

    def test_returns_installed_dict_identity(self):
        systems = {"registry": object(), "combat": object()}
        with services.override(systems):
            self.assertIs(GameTickScript()._get_systems(), systems)

    def test_single_entry_dict_is_returned_as_is(self):
        systems = {"metrics": object()}
        with services.override(systems):
            self.assertIs(GameTickScript()._get_systems(), systems)


class TestGetSystemsInstalledEmpty(unittest.TestCase):
    """_get_systems returns None when the facade holds an empty dict."""

    def test_installed_empty_dict_returns_none(self):
        with services.override({}):
            self.assertIsNone(GameTickScript()._get_systems())


class TestGetSystemsNeverInstalled(unittest.TestCase):
    """_get_systems returns None when install() has never run."""

    def setUp(self):
        self._previous = services._systems
        services._systems = None
        self.addCleanup(self._restore)

    def _restore(self):
        services._systems = self._previous

    def test_never_installed_returns_none(self):
        self.assertIsNone(GameTickScript()._get_systems())


if __name__ == "__main__":
    unittest.main()
