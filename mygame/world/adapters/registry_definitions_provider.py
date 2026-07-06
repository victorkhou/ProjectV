"""
DataRegistry-backed :class:`DefinitionsProvider` implementation.

A thin, hot-reload-safe view over a live ``DataRegistry``: every access reads
through to the registry rather than snapshotting, so a hot-reload that
reassigns ``DataRegistry.balance`` / ``ranks`` / ``ability_gates`` is picked up
without reconstructing the provider. Holds no Evennia dependency itself — it
just adapts the registry's shape to the port — but lives in ``adapters`` since
it is the concrete wiring injected at the composition root.
"""

from __future__ import annotations

from typing import Any

from world.core.ports.definitions_provider import DefinitionsProvider


class RegistryDefinitionsProvider(DefinitionsProvider):
    """Read-only view over a live ``DataRegistry`` (never snapshots)."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    @property
    def balance(self) -> Any:
        return self._registry.balance

    @property
    def ranks(self) -> list[Any]:
        return self._registry.ranks

    def resolve_building(self, building_type: str) -> Any | None:
        return self._registry.resolve_building(building_type)

    def get_ability_gates(self) -> list[Any]:
        return self._registry.get_ability_gates()
