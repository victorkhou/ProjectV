"""
Read-model port for game definitions and hot-tunable balance.

Owner-agnostic code (progression math, capability checks, rank-name lookup)
needs read access to definitions and the live ``BalanceConfig`` without owning
a ``DataRegistry`` reference. Today those paths reach the process-wide
``DataRegistry.get_instance()`` singleton; this port lets a provider be *passed
in* instead, so tests inject a fake and no global state leaks between them.

The concrete implementation is
``world.adapters.registry_definitions_provider.RegistryDefinitionsProvider``.
``balance`` is a property (not a snapshot) so a hot-reload that reassigns
``DataRegistry.balance`` is observed on the next read.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DefinitionsProvider(ABC):
    """Read-only view over game definitions plus the live balance config."""

    @property
    @abstractmethod
    def balance(self) -> Any:
        """The live ``BalanceConfig``. Read lazily so hot-reload is observed."""

    @property
    @abstractmethod
    def ranks(self) -> list[Any]:
        """The ordered rank definitions (each with ``level``/``xp_threshold``)."""

    @abstractmethod
    def resolve_building(self, building_type: str) -> Any | None:
        """Return the ``BuildingDef`` for *building_type*, or ``None``."""

    @abstractmethod
    def get_ability_gates(self) -> list[Any]:
        """Return the ability-gate definitions."""
