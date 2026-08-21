"""
Text UI formatters for the RTS Combat Overworld.

Presentation helpers that turn game objects into player-facing strings. Kept
out of the typeclasses so ``PlanetRoom`` stays a pure spatial container: the
typeclass decides *what* to show, these functions decide *how* it reads.
"""

from __future__ import annotations

import logging
from typing import Any

from world.services import get_registry

logger = logging.getLogger("evennia.world.ui_formatters")


def format_xp_bar(percent: float, width: int = 20, bare: bool = False) -> str:
    """Render an XP progress bar like ``[|g██████|n............] 57%``.

    *percent* is 0-100 (clamped). The filled portion is green, the remainder
    dim dots, so the bar reads as "how far into this level" at a glance. The
    single renderer shared by the status prompt and the ``score`` sheet so both
    look identical.

    The filled cell count FLOORS rather than rounds, so a bar only reads as
    completely full at a true 100% — otherwise a 97% player would see a full
    bar and wonder why they hadn't levelled.

    Set *bare* for the status prompt: drops the brackets and the trailing
    percent (which the prompt has no column budget for), leaving just the
    glyph run.
    """
    try:
        pct = max(0.0, min(100.0, float(percent)))
    except (TypeError, ValueError):
        pct = 0.0
    width = max(1, int(width))
    filled = int(pct / 100.0 * width)  # floor: only a true 100% fills the bar
    filled = max(0, min(width, filled))
    bar = "|g" + ("\u2588" * filled) + "|n" + ("|x" + ("." * (width - filled)) + "|n")
    if bare:
        return bar
    return f"|w[|n{bar}|w]|n {int(pct)}%"


def xp_progress(player: Any) -> dict | None:
    """Return a player's XP-in-level progress, or ``None`` when unavailable.

    Computes, from the player's total ``combat_xp`` and the shared level curve
    (``world.progression``): the current level, the XP at the start of that
    level, the XP needed for the next level, the XP earned within the level, and
    the percent toward the next level. Returns ``None`` for a maxed player (no
    next level) or when the curve/level can't be resolved, so callers can hide
    the bar in those cases.

    ``into_level`` is clamped into ``[0, level_span]``: a stored ``db.level``
    can lag ``combat_xp`` between an award and its level sync, and an unclamped
    value would render as "340/297 to Level 13".

    Never raises — but DOES log, so a genuine bug here surfaces instead of
    silently removing the bar everywhere.
    """
    try:
        from world import progression
        from world.constants import MAX_LEVEL
        from world.utils import get_player_level

        db = getattr(player, "db", None)
        if db is None:
            return None
        level = get_player_level(player, default=1)
        if level >= MAX_LEVEL:
            return None
        current_xp = int(getattr(db, "combat_xp", 0) or 0)
        start = progression.xp_for_level(level)
        nxt = progression.xp_for_level(level + 1)
        span = nxt - start
        if span <= 0:
            return None
        into = max(0, min(span, current_xp - start))
        pct = max(0.0, min(100.0, into / span * 100.0))
        return {
            "level": level,
            "current_xp": current_xp,
            "level_start_xp": start,
            "next_level_xp": nxt,
            "into_level": into,
            "level_span": span,
            "percent": pct,
        }
    except Exception:  # noqa: BLE001 - never break a prompt/sheet on a read
        logger.exception("XP-progress computation failed; hiding the XP bar.")
        return None


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
