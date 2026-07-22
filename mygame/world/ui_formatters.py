"""
Text UI formatters for the RTS Combat Overworld.

Presentation helpers that turn game objects into player-facing strings. Kept
out of the typeclasses so ``PlanetRoom`` stays a pure spatial container: the
typeclass decides *what* to show, these functions decide *how* it reads.
"""

from __future__ import annotations

from typing import Any

from world.services import get_registry


def format_building_interior(looker: Any, building: Any, registry: Any = None) -> str:
    """Format a building's interior view as a string for ``look``/appearance.

    Shows owner, level/HP, category, production, construction/training progress,
    the assigned agent's status, resource drops on the tile, other agents
    present, and the building's open/closed exits. ``registry`` is looked up
    from the installed game systems when not supplied.
    """
    from world.utils import (
        coords_of, get_building_info, get_building_attr, get_closed_exits,
        get_obj_attr, format_list_block,
    )

    info = get_building_info(building)
    owner = info["owner"]
    owner_name = getattr(owner, "key", str(owner)) if owner else "nobody"

    category = "unknown"
    produces = "—"
    unlocks_str = "—"
    if registry is None:
        registry = get_registry()
    try:
        if registry:
            bdef = registry.get_building(info["type"])
            category = bdef.category
            produces = bdef.produces or "—"
            if bdef.unlocks:
                unlocks_str = ", ".join(bdef.unlocks)
    except Exception:
        pass

    closed = get_closed_exits(building)
    exit_parts = []
    for d in ("north", "south", "east", "west"):
        if d in closed:
            exit_parts.append(f"|r{d} (closed)|n")
        else:
            exit_parts.append(f"|g{d}|n")

    # Check construction state
    under_construction = get_building_attr(building, "under_construction", False)
    progress = get_building_attr(building, "construction_progress", 0) or 0
    total = get_building_attr(building, "construction_total", 0) or 0

    lines = [
        f"|w=== {info['name']} ({info['type']}) ===|n",
    ]

    if under_construction and total > 0:
        pct = int((progress / total) * 100) if total > 0 else 0
        remaining = max(0, total - progress)
        lines.append(f"  |y*** UNDER CONSTRUCTION ***|n")
        lines.append(f"  Progress: {progress}/{total}s ({pct}%) — {remaining}s remaining")
        lines.append(f"  Stay on the tile or assign an Engineer to continue.")
        lines.append("")

    lines.extend([
        f"  Owner: {owner_name}",
        f"  Level: {info['level']} | HP: {info['hp']}/{info['hp_max']}",
    ])
    # Shield (Shield Generator feature): a building covered by a shield carries a
    # second HP bar that soaks damage before HP. Show it only when the building
    # actually has shield capacity, right under the HP line.
    shield_max = int(get_building_attr(building, "shield_max", 0) or 0)
    if shield_max > 0:
        shield = int(get_building_attr(building, "shield", 0) or 0)
        lines.append(f"  |cShield: {shield}/{shield_max}|n")
    lines.extend([
        f"  Category: {category}",
        f"  Produces: {produces}",
    ])
    if unlocks_str != "—":
        lines.append(f"  Unlocks: {unlocks_str}")

    # Show training progress for Academies
    training_agent_id = get_building_attr(building, "training_agent_id")
    if training_agent_id is not None:
        training_remaining = get_building_attr(building, "training_ticks_remaining", 0) or 0
        lines.append("")
        lines.append(f"  |c[Training] Agent #{training_agent_id} — {training_remaining}s remaining|n")

    # Building coordinates (used by assigned-agent check and resource drops)
    b_coords = coords_of(building)
    if b_coords is None:
        bx = by = None
    else:
        bx, by, _planet = b_coords
    tile = getattr(building, "location", None)

    # Show assigned agent
    assigned = get_building_attr(building, "assigned_agent")
    if assigned is not None:
        aid = getattr(getattr(assigned, "db", None), "agent_id", "?")
        role = getattr(getattr(assigned, "db", None), "role", "") or "idle"
        activity = getattr(getattr(assigned, "db", None), "activity_status", None) or "Idle"

        # Check if the agent is physically at this building's tile
        agent_coords = coords_of(assigned)
        at_building = (
            agent_coords is not None
            and bx is not None and by is not None
            and int(agent_coords[0]) == int(bx) and int(agent_coords[1]) == int(by)
        )

        if at_building:
            lines.append(f"  |gAgent #{aid}|n assigned as |w{role}|n — {activity}")
        else:
            lines.append(f"  |yAgent #{aid}|n assigned as |w{role}|n — |yen route|n")

    # Show resource drops at the building's coordinates
    if tile is not None and bx is not None and by is not None and hasattr(tile, "get_objects_at"):
        drops = []
        for obj in tile.get_objects_at(int(bx), int(by), type_tag="resource_drop"):
            rtype = getattr(getattr(obj, "db", None), "resource_type", "?")
            amt = getattr(getattr(obj, "db", None), "amount", 0)
            if amt > 0:
                drops.append(f"{amt} {rtype}")
        if drops:
            lines.append("")
            lines.append("  |yResources:|n")
            lines.extend(format_list_block(drops))
            lines.append(f"  Use |wget|n to pick them up.")
    elif tile is not None:
        # Legacy fallback: iterate contents
        drops = []
        for obj in getattr(tile, "contents", []):
            if hasattr(obj, "tags") and obj.tags.get("resource_drop", category="object_type"):
                rtype = getattr(getattr(obj, "db", None), "resource_type", "?")
                amt = getattr(getattr(obj, "db", None), "amount", 0)
                if amt > 0:
                    drops.append(f"{amt} {rtype}")
        if drops:
            lines.append("")
            lines.append("  |yResources:|n")
            lines.extend(format_list_block(drops))
            lines.append(f"  Use |wget|n to pick them up.")

    # Show dropped/produced items (gear + supply GameItems) on the building's
    # tile — e.g. gear an assigned engineer just produced here. Without this,
    # items on the tile were invisible while inside the building even though
    # 'get' could pick them up.
    if tile is not None and bx is not None and by is not None and hasattr(tile, "get_objects_at"):
        item_strs = []
        for obj in tile.get_objects_at(int(bx), int(by), type_tag="item"):
            name = getattr(obj, "key", "item")
            count = getattr(getattr(obj, "db", None), "count", None)
            item_strs.append(f"{name} x{count}" if count else name)
        if item_strs:
            lines.append("")
            lines.append("  |wItems:|n")
            lines.extend(format_list_block(item_strs))
            lines.append(f"  Use |wget|n to pick them up.")

    # Show other NPCs at the building's coordinates: the looker's OWN agents by
    # id/role, and any HOSTILE NPCs (enemy guards, other players' units) tagged
    # so the looker can see who is attacking them from inside the same building.
    # Without the hostile branch a raider inside an enemy base was hit by a guard
    # on the tile with nothing shown in the interior view.
    tile_objs = None
    if tile is not None and bx is not None and by is not None and hasattr(tile, "get_objects_at"):
        tile_objs = tile.get_objects_at(int(bx), int(by))
    elif tile is not None:
        tile_objs = getattr(tile, "contents", [])
    if tile_objs is not None:
        own_agents = []
        hostiles = []
        for obj in tile_objs:
            if obj is building or obj is assigned:
                continue  # building itself / assigned agent already shown
            if not (hasattr(obj, "tags") and obj.tags.get(category="npc_type")):
                continue
            npc_owner = getattr(getattr(obj, "db", None), "owner", None)
            if npc_owner is looker:
                aid = getattr(obj.db, "agent_id", "?")
                role = getattr(obj.db, "role", "") or "idle"
                own_agents.append(f"Agent #{aid} ({role})")
            else:
                # A hostile unit sharing the tile. Sentinel-owned units are enemy
                # NPC-base guards; others are another player's agents.
                role = getattr(getattr(obj, "db", None), "role", "") or "unit"
                enemy = bool(get_obj_attr(npc_owner, "is_sentinel", False)) if npc_owner else False
                tag = "|R[Enemy]|n " if enemy else ""
                hostiles.append(f"{tag}{getattr(obj, 'key', 'unit')} ({role})")
        if own_agents:
            lines.append("  Agents here:")
            lines.extend(format_list_block(own_agents))
        if hostiles:
            lines.append("  |rHostiles here:|n")
            lines.extend(format_list_block(hostiles))

    # Show other players at the building's tile (excluding the looker), so
    # entering a building reveals who is inside — matching the overworld
    # tile summary. Without this, auto-enter never listed co-located players
    # and you only saw them on an explicit 'look'.
    if tile is not None and bx is not None and by is not None and hasattr(tile, "get_players_at"):
        others = [
            getattr(p, "key", "?")
            for p in tile.get_players_at(int(bx), int(by))
            if p is not looker
        ]
        if others:
            lines.append("  Players here:")
            lines.extend(format_list_block(others))

    lines.append("")
    lines.append(f"  Exits: {', '.join(exit_parts)}")

    return "\n".join(lines)
