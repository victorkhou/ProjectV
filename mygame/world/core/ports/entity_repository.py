"""
Repository and factory ports for entity persistence and creation.

Use-case systems (``AgentSystem``, later ``BuildingSystem`` /
``MovementSystem``) need to *query* and *create* game entities, but must not
import Evennia to do it. These abstractions capture "find agents for an owner",
"find all agents", "find training buildings", and "create an agent" so the
Django ORM / tag-index / ``create_object`` calls live only in the Evennia
adapters (``world.adapters``). Swapping persistence means writing a new adapter,
never editing a system body.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AgentRepository(ABC):
    """Read-side port for player-owned NPC agents.

    Replaces the direct ``ObjectDB.objects.filter(...)`` /
    ``search_object_by_tag(...)`` queries previously embedded in
    ``AgentSystem``.
    """

    @abstractmethod
    def find_agents_for_owner(self, owner: Any) -> list[Any]:
        """Return every agent NPC owned by *owner* (empty list if none)."""

    @abstractmethod
    def find_all_agents(self) -> list[Any]:
        """Return every agent NPC in the world (used by the per-tick sweep)."""

    @abstractmethod
    def find_all_enemies(self) -> list[Any]:
        """Return every enemy NPC (npc_type="enemy") — NPC-base guards.

        Used by the per-tick guard-combat sweep so NPC-base guards fight back
        (they are NOT in the ``npc_type="agent"`` roster).
        """

    @abstractmethod
    def find_training_buildings(self) -> list[Any]:
        """Return buildings that currently hold a ``training_agent_id``."""


class AgentFactory(ABC):
    """Write-side port for creating agent NPCs.

    Replaces ``AgentSystem._default_create_npc`` — the ``create_object`` call,
    tag seeding, and coordinate-index registration all move into the adapter.
    The system decides *whether* and *for whom* to create; the factory owns the
    framework I/O of actually spawning and placing the NPC.
    """

    @abstractmethod
    def create_agent(self, owner: Any, agent_id: int) -> Any:
        """Create, persist, place, and index a new agent NPC; return it."""


class BuildingFactory(ABC):
    """Write-side port for creating Building objects.

    Replaces ``BuildingSystem._default_create_building`` — the
    ``create_object`` call, attribute seeding, and coordinate-index
    registration move into the adapter.
    """

    @abstractmethod
    def create_building(
        self,
        building_def: Any,
        tile: Any,
        owner: Any,
        x: int | None = None,
        y: int | None = None,
    ) -> Any:
        """Create, persist, place, and index a new Building; return it."""


class MovingEntityRepository(ABC):
    """Read-side port for recovering in-flight moving NPCs after a restart.

    Replaces the ``search_object_by_tag("npc", ...)`` scan embedded in
    ``MovementSystem._ensure_initialized``.
    """

    @abstractmethod
    def find_moving_npcs(self) -> list[Any]:
        """Return NPCs that currently have a non-empty movement queue."""


class NpcBaseFactory(ABC):
    """Write-side port for spawning NPC-base entities (Sentinel + enemy guards).

    Lets ``OutpostSpawnerSystem`` create the sentinel Character (the base owner)
    and enemy-guard NPCs without importing Evennia. The ``create_object`` calls,
    tag seeding, and coordinate-index registration live in the adapter.
    """

    @abstractmethod
    def create_sentinel(self, name: str, tile: Any, planet: str) -> Any:
        """Create a non-puppeted Sentinel Character owning an NPC base."""

    @abstractmethod
    def create_enemy_guard(
        self,
        owner: Any,
        tile: Any,
        x: int,
        y: int,
        role: str,
        hp: int,
    ) -> Any:
        """Create, place, and index an enemy-guard NPC owned by *owner*."""
