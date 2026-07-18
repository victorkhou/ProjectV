"""
Evennia-backed NpcBaseFactory — spawns Sentinel Characters and enemy guards.

Home for the ``evennia.create_object`` calls that build an NPC base's owner
(a :class:`~typeclasses.sentinel.SentinelCharacter`) and its enemy-guard NPCs,
so ``OutpostSpawnerSystem`` depends on the ``NpcBaseFactory`` port rather than
importing Evennia.
"""

from __future__ import annotations

import logging
from typing import Any

from world.core.ports.entity_repository import NpcBaseFactory

logger = logging.getLogger("evennia.world.adapters.npc_base")


class EvenniaNpcBaseFactory(NpcBaseFactory):
    """Creates + places + indexes Sentinel Characters and enemy guards."""

    def create_sentinel(self, name: str, tile: Any, planet: str) -> Any:
        import evennia

        sentinel = evennia.create_object(
            "typeclasses.sentinel.SentinelCharacter",
            key=name,
            location=tile,
        )
        # The sentinel is placed on the planet so its buildings resolve their
        # planet via location (owner_has_active_hq's planet scoping). It carries
        # coord_planet for the same reason, but no coord_x/y — it is not a map
        # actor, just an ownership anchor.
        sentinel.db.coord_planet = planet
        return sentinel

    def create_enemy_guard(
        self,
        owner: Any,
        tile: Any,
        x: int,
        y: int,
        role: str,
        hp: int,
        index: int = 1,
    ) -> Any:
        import evennia

        # A per-base, 1-based *index* makes each guard individually named
        # ("Outpost #2 Guard-1", "Outpost #2 Guard-2", ...) so players can tell
        # co-located guards apart and target a specific one.
        base_label = owner.key if hasattr(owner, "key") else role.title()
        npc = evennia.create_object(
            "typeclasses.npcs.NPC",
            key=f"{base_label} {role.title()}-{index}",
            location=tile,
        )
        npc.db.owner = owner
        npc.db.npc_type = "enemy"
        npc.db.role = role
        npc.db.hp = hp
        npc.db.hp_max = hp
        # Retag from the default "agent" npc_type (set in NPC.at_object_creation)
        # to "enemy" so roster queries and the map renderer classify it correctly.
        try:
            npc.tags.remove("agent", category="npc_type")
        except Exception:  # noqa: BLE001 - tag may already be absent
            pass
        npc.tags.add("enemy", category="npc_type")
        # Owner tag mirrors the agent convention so the base's guards are
        # enumerable by owner id.
        owner_id = getattr(owner, "id", id(owner))
        npc.tags.add(f"player_{owner_id}", category="agent_owner")

        from world.utils import place_on_tile
        place_on_tile(npc, tile, x, y)
        # Stamp the planet so coordinate/planet-scoped logic (e.g. a player's
        # ranged lock-on, which drops when shooter and target aren't on the same
        # planet) sees the guard as a real map actor. Read from the tile's
        # planet_name, falling back to the owner sentinel's coord_planet.
        planet = getattr(tile, "planet_name", None)
        if planet is None:
            planet = getattr(getattr(owner, "db", None), "coord_planet", None)
        npc.db.coord_planet = planet
        # Home anchor: the spawn tile. GuardCombatSystem leashes a chasing guard
        # to within aggro_radius of home so it defends its base instead of being
        # lured away.
        npc.db.home_x = int(x)
        npc.db.home_y = int(y)
        return npc
