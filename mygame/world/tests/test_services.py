"""
Unit tests for the ``world.services`` facade named accessors.

Covers ``get_registry`` with the registry present and absent, ``get_balance``
with a registry carrying ``balance``, a registry without it, and no registry
at all, plus the pre-install (never-installed) behavior of every accessor.

Injection goes through ``services.override`` so no installed state leaks
between tests; the never-installed state is exercised by snapshotting and
restoring ``services._systems`` around a reset to None.

Validates: Requirements 5.4, 5.6, 5.7, 5.8.
"""

import unittest

from world import services


class _Registry:
    """Fake registry system, optionally carrying a ``balance`` attribute."""

    def __init__(self, balance=None, with_balance=True):
        if with_balance:
            self.balance = balance


class _NeverInstalledMixin:
    """Snapshot/restore ``services._systems`` and force the never-installed state."""

    def setUp(self):
        self._previous = services._systems
        services._systems = None
        self.addCleanup(self._restore)

    def _restore(self):
        services._systems = self._previous


class TestGetRegistry(unittest.TestCase):
    """get_registry returns the installed registry or None (Requirements 5.4, 5.8)."""

    def test_registry_present_returns_the_installed_object(self):
        registry = _Registry()
        with services.override({"registry": registry}):
            self.assertIs(services.get_registry(), registry)

    def test_registry_absent_from_installed_mapping_returns_none(self):
        with services.override({"combat": object()}):
            self.assertIsNone(services.get_registry())

    def test_installed_empty_mapping_returns_none(self):
        with services.override({}):
            self.assertIsNone(services.get_registry())


class TestGetBalance(unittest.TestCase):
    """get_balance reads ``balance`` off the registry, else None (Requirements 5.7, 5.8)."""

    def test_registry_with_balance_returns_the_balance_object(self):
        balance = object()
        with services.override({"registry": _Registry(balance=balance)}):
            self.assertIs(services.get_balance(), balance)

    def test_registry_without_balance_attribute_returns_none(self):
        with services.override({"registry": _Registry(with_balance=False)}):
            self.assertIsNone(services.get_balance())

    def test_no_registry_installed_returns_none(self):
        with services.override({"combat": object()}):
            self.assertIsNone(services.get_balance())


class TestPreInstallBehavior(_NeverInstalledMixin, unittest.TestCase):
    """Every accessor is safe before install() has ever run (Requirements 5.6, 5.8)."""

    def test_get_service_returns_none(self):
        self.assertIsNone(services.get_service("registry"))
        self.assertIsNone(services.get_service("anything"))

    def test_get_systems_returns_empty_dict(self):
        self.assertEqual(services.get_systems(), {})

    def test_get_registry_returns_none(self):
        self.assertIsNone(services.get_registry())

    def test_get_balance_returns_none(self):
        self.assertIsNone(services.get_balance())


if __name__ == "__main__":
    unittest.main()
