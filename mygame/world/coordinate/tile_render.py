"""
Shared tile-object classification for the two map renderers.

Both the ASCII overworld renderer (:mod:`world.coordinate.procedural_map_renderer`)
and the webclient JSON provider (:mod:`world.coordinate.map_data_provider`) walk a
tile's object list and sort it into the same buckets — the looker, other players,
NPC agents (with owner/role), and the building on the tile — then check whether a
building has any occupant inside. That classification is identical between them;
only the *formatting* of the buckets differs (colored 2-char symbols vs JSON
dicts). This module owns the shared classification so the two renderers can't
drift apart on who-is-what.

Kept dependency-light: only ``world.utils.player_is_present`` (value-based reads,
no evennia import at module scope) so it is safe under the layering rules.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from world.utils import player_is_present


class TileOccupants(NamedTuple):
    """The classified contents of a single tile.

    Attributes:
        looker_present: True if *looker* itself is standing on the tile.
        other_players: The present player characters other than *looker*, in
            tile order (linkdead players included — they render during grace).
        agents: NPC agents on the tile as ``(obj, own, role)`` triples, in tile
            order; ``own`` is True when the agent's owner id matches *looker*'s.
        building: The first building object on the tile, or ``None``.
    """

    looker_present: bool
    other_players: list
    agents: list
    building: Any


def partition_tile_objects(objects, looker) -> TileOccupants:
    """Sort a tile's *objects* into players / agents / building buckets.

    The single classification loop shared by both renderers: player characters
    (via :func:`player_is_present`, so sessionless linkdead players still count),
    ``npc_type``-tagged agents (tagged with ``own`` vs *looker* and their role),
    and the first ``building``-tagged object. Callers apply their own formatting.
    """
    looker_id = getattr(looker, "id", None)
    looker_present = False
    other_players = []
    agents = []
    building = None

    for obj in objects:
        if player_is_present(obj):
            if obj is looker:
                looker_present = True
            else:
                other_players.append(obj)
            continue

        if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
            npc_owner = getattr(obj.db, "owner", None) if hasattr(obj, "db") else None
            npc_owner_id = getattr(npc_owner, "id", None) if npc_owner else None
            role = getattr(obj.db, "role", "") if hasattr(obj, "db") else ""
            own = npc_owner_id is not None and npc_owner_id == looker_id
            agents.append((obj, own, role))
            continue

        if hasattr(obj, "tags") and obj.tags.get("building", category="object_type"):
            if building is None:
                building = obj

    return TileOccupants(looker_present, other_players, agents, building)


def building_is_occupied(building) -> bool:
    """Return True if *building* has any player or NPC inside it.

    The occupancy scan shared by both renderers: True on the first present
    player or ``npc_type``-tagged object in the building's contents.
    """
    for obj in getattr(building, "contents", []):
        if player_is_present(obj):
            return True
        if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
            return True
    return False
