"""
Unit tests for RegistryDefinitionsProvider.

Locks in the hot-reload safety the adapter promises: because it reads through
to the registry on every access (never snapshots), reassigning
``registry.balance`` after construction must be observed on the next read.
"""

from mygame.world.adapters.registry_definitions_provider import (
    RegistryDefinitionsProvider,
)


class _FakeRegistry:
    """Minimal stand-in exposing the members the provider adapts."""

    def __init__(self):
        self.balance = object()
        self.ranks = ["r1", "r2"]
        self._buildings = {"HQ": "hq_def"}
        self._gates = ["gate_a", "gate_b"]

    def resolve_building(self, token):
        return self._buildings.get(token)

    def get_ability_gates(self):
        return self._gates


class TestRegistryDefinitionsProvider:
    def test_balance_is_read_lazily(self):
        reg = _FakeRegistry()
        provider = RegistryDefinitionsProvider(reg)
        first = provider.balance
        assert first is reg.balance

        # Simulate a hot-reload swapping the balance object.
        new_balance = object()
        reg.balance = new_balance
        assert provider.balance is new_balance
        assert provider.balance is not first

    def test_ranks_pass_through(self):
        reg = _FakeRegistry()
        provider = RegistryDefinitionsProvider(reg)
        assert provider.ranks == ["r1", "r2"]
        reg.ranks = ["r1", "r2", "r3"]
        assert provider.ranks == ["r1", "r2", "r3"]

    def test_resolve_building_delegates(self):
        provider = RegistryDefinitionsProvider(_FakeRegistry())
        assert provider.resolve_building("HQ") == "hq_def"
        assert provider.resolve_building("ZZ") is None

    def test_get_ability_gates_delegates(self):
        provider = RegistryDefinitionsProvider(_FakeRegistry())
        assert provider.get_ability_gates() == ["gate_a", "gate_b"]


class TestDefaultBalance:
    """default_balance() returns a BalanceConfig even with no registry.

    Note: the singleton is set/read through the ``world.`` namespace (NOT
    ``mygame.world.``) because production's ``default_balance`` imports
    ``from world.data_registry import DataRegistry`` — the two import paths are
    distinct module objects with separate class-level singletons.
    """

    def test_falls_back_to_defaults_without_registry(self):
        # No DataRegistry singleton registered → the choke point must return a
        # default BalanceConfig rather than None, matching the semantics the
        # retired BalanceConfig.current() gave.
        from world.adapters.registry_definitions_provider import default_balance
        from world.definitions import BalanceConfig
        from world.data_registry import DataRegistry

        prev = DataRegistry.get_instance()
        DataRegistry.set_instance(None)
        try:
            bal = default_balance()
        finally:
            DataRegistry.set_instance(prev)
        assert isinstance(bal, BalanceConfig)
        assert hasattr(bal, "gather_amount")

    def test_returns_live_registry_balance_when_registered(self):
        from world.adapters.registry_definitions_provider import default_balance
        from world.data_registry import DataRegistry

        class _Reg:
            balance = object()

        reg = _Reg()
        prev = DataRegistry.get_instance()
        DataRegistry.set_instance(reg)
        try:
            assert default_balance() is reg.balance
        finally:
            DataRegistry.set_instance(prev)
