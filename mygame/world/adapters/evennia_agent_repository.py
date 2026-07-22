"""
Evennia-backed AgentRepository / AgentFactory implementations.

The single module that knows about ``ObjectDB``, agent tag categories, and
``evennia.create_object`` for agents. Implements the ports in
``world.core.ports.entity_repository`` so ``AgentSystem`` depends on the
abstraction.

On query failure these methods log the exception and return an empty list —
fail-safe, so a transient DB error degrades to "no agents found" rather than
crashing the game tick. Callers that derive *persisted* state from the result
must therefore not treat an empty return as authoritative: e.g.
``AgentSystem.train_agent`` floors the next agent ID on the monotonic
``player.db.next_agent_id`` counter rather than on ``max(roster)`` for exactly
this reason.
"""

from __future__ import annotations

import logging
from typing import Any

from world.core.ports.entity_repository import AgentFactory, AgentRepository

logger = logging.getLogger("evennia.world.adapters.agent")


class EvenniaAgentRepository(AgentRepository):
    """Reads agents out of Evennia's tag index / ObjectDB."""

    def find_agents_for_owner(self, owner: Any) -> list[Any]:
        owner_id = getattr(owner, "id", id(owner))
        try:
            from evennia.objects.models import ObjectDB

            return list(
                ObjectDB.objects.filter(
                    db_tags__db_key="agent",
                    db_tags__db_category="npc_type",
                ).filter(
                    db_tags__db_key=f"player_{owner_id}",
                    db_tags__db_category="agent_owner",
                )
            )
        except Exception:
            logger.exception("find_agents_for_owner failed for owner=%r", owner)
            return []

    def find_all_agents(self) -> list[Any]:
        try:
            from evennia.utils.search import search_object_by_tag

            return list(search_object_by_tag("agent", category="npc_type"))
        except Exception:
            logger.exception("find_all_agents failed")
            return []

    def find_all_enemies(self) -> list[Any]:
        try:
            from evennia.utils.search import search_object_by_tag

            return list(search_object_by_tag("enemy", category="npc_type"))
        except Exception:
            logger.exception("find_all_enemies failed")
            return []

    def find_training_buildings(self) -> list[Any]:
        try:
            from evennia.objects.models import ObjectDB

            candidates = list(
                ObjectDB.objects.filter(db_attributes__db_key="training_agent_id")
            )
            # Preserve the prior semantics: only buildings whose attribute is
            # actually set (not None) count as training.
            result = []
            for b in candidates:
                if b.attributes.get("training_agent_id") is not None:
                    result.append(b)
            return result
        except Exception:
            logger.exception("find_training_buildings failed")
            return []


class EvenniaAgentFactory(AgentFactory):
    """Creates + places + indexes agent NPCs via Evennia.

    Body lifted verbatim from the former ``AgentSystem._default_create_npc`` so
    behavior is preserved: spawn in the owner's PlanetRoom, seed owner/type/id
    tags, and place at the owner's HQ coordinates (falling back to the owner's
    current position), registering in the room coordinate index.
    """

    def create_agent(self, owner: Any, agent_id: int) -> Any:
        import evennia

        planet_room = getattr(owner, "location", None)

        npc = evennia.create_object(
            "typeclasses.npcs.NPC",
            key=f"Agent-{agent_id}",
            location=planet_room,
        )
        npc.db.owner = owner
        npc.db.npc_type = "agent"
        npc.db.agent_id = agent_id
        npc.db.role = ""
        npc.db.role_target = None
        npc.db.reserve = False
        npc.tags.add("agent", category="npc_type")
        owner_id = getattr(owner, "id", id(owner))
        npc.tags.add(f"player_{owner_id}", category="agent_owner")

        spawn_x, spawn_y = self._resolve_spawn_coords(owner)
        if spawn_x is not None and spawn_y is not None:
            # at_object_receive runs during create_object before coords are set,
            # so place_on_tile must stamp coords and register in the index here.
            from world.utils import place_on_tile
            place_on_tile(npc, planet_room, spawn_x, spawn_y)

        return npc

    @staticmethod
    def _resolve_spawn_coords(owner: Any) -> tuple[Any, Any]:
        """HQ coordinates for *owner*, falling back to the owner's position.

        Two-stage fallback (matching the original _default_create_npc): use the
        HQ's coords only when BOTH are set; otherwise — HQ missing, or HQ found
        but without coordinates yet — fall back to the owner's own position so
        the agent is still placed and indexed.
        """
        from world.utils import coords_of

        try:
            buildings = owner.get_buildings() if hasattr(owner, "get_buildings") else []
            for b in buildings:
                if getattr(b.db, "building_type", "") == "HQ":
                    hq_coords = coords_of(b)
                    if hq_coords is not None:
                        hx, hy, _planet = hq_coords
                        return hx, hy
                    break
        except Exception:
            pass
        db = getattr(owner, "db", None)
        if db is None:
            return None, None
        return db.coord_x, db.coord_y
