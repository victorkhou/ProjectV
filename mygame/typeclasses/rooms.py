"""
Room

Rooms are simple containers that has no location of their own.

"""

import logging

from evennia.objects.objects import DefaultRoom

from .objects import ObjectParent

_log = logging.getLogger("mygame.rooms")


class Room(ObjectParent, DefaultRoom):
    """
    Rooms are like any Object, except their location is None
    (which is default). They also use basetype_setup() to
    add locks so they cannot be puppeted or picked up.
    (to change that, use at_object_creation instead)

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Objects.
    """

    pass


class PlanetRoom(DefaultRoom):
    """Shared room for an entire planet — one per planet.

    All players on a planet share this room. Player position is tracked
    via coord_x/coord_y/coord_planet attributes on the character, NOT
    by which room they are in.

    This eliminates DB writes during movement: no room creation or
    deletion when a player moves between tiles.
    """

    @property
    def planet_name(self) -> str:
        return self.attributes.get("planet", default="unknown")

    # -------------------------------------------------------------- #
    #  Lifecycle
    # -------------------------------------------------------------- #

    def at_init(self):
        """Called on every typeclass cache load (restart/reload).

        Clear the ndb index so it's lazily rebuilt on first access.
        """
        self.ndb._coord_index = None

    # -------------------------------------------------------------- #
    #  Coordinate Index (ndb, non-persistent)
    # -------------------------------------------------------------- #

    @property
    def coord_index(self):
        """Lazy-init coordinate index. Rebuilt from contents on first access."""
        idx = self.ndb._coord_index
        if idx is None:
            idx = self._rebuild_index()
        return idx

    def _rebuild_index(self):
        from world.coordinate.coordinate_index import CoordinateIndex

        idx = CoordinateIndex.build_from_contents(self.contents)
        self.ndb._coord_index = idx
        _log.info(
            "PlanetRoom %s: rebuilt coordinate index (%d objects)",
            self.key,
            len(idx),
        )
        return idx

    # -------------------------------------------------------------- #
    #  Query Methods
    # -------------------------------------------------------------- #

    def get_objects_at(self, x: int, y: int, type_tag: str | None = None) -> list:
        """Return objects at (x, y), optionally filtered by object_type tag.

        Defensively filters out objects whose database row has been
        deleted (``pk is None``) — these can appear in the in-memory
        index if a delete path bypassed ``at_object_delete``. Stale
        refs are lazily removed from the index.

        Args:
            x, y: Tile coordinates.
            type_tag: If provided, only return objects with this tag in
                      the ``object_type`` category.
        """
        raw = self.coord_index.get_at(x, y)
        live = []
        stale = []
        for o in raw:
            if getattr(o, "pk", True) is None:
                stale.append(o)
                continue
            live.append(o)
        # Lazy-clean stale entries so this doesn't recur
        if stale:
            idx = self.ndb._coord_index
            if idx is not None:
                for o in stale:
                    try:
                        idx.remove(o, x, y)
                    except Exception:
                        pass

        if type_tag is None:
            return live
        return [
            o for o in live
            if hasattr(o, "tags") and o.tags.get(type_tag, category="object_type")
        ]

    def get_buildings_at(self, x: int, y: int) -> list:
        """Return Building objects at (x, y)."""
        return self.get_objects_at(x, y, type_tag="building")

    def get_players_at(self, x: int, y: int) -> list:
        """Return player characters at (x, y)."""
        return [
            o for o in self.coord_index.get_at(x, y)
            if hasattr(o, "has_account") and o.has_account
        ]

    def get_objects_in_area(self, x1: int, y1: int, x2: int, y2: int) -> list:
        """Return all objects within the bounding box (inclusive)."""
        return self.coord_index.get_in_area(x1, y1, x2, y2)

    # -------------------------------------------------------------- #
    #  Mutation — coordinate changes
    # -------------------------------------------------------------- #

    def move_entity(self, obj, new_x: int, new_y: int) -> None:
        """Atomically update an object's coordinates and the index.

        Fires ``at_coord_change(old_x, old_y, new_x, new_y)`` on the
        object if the hook exists, for game systems that need to react.

        Notifies players at the old tile that the entity left and
        players at the new tile that the entity arrived.
        """
        old_x = getattr(getattr(obj, "db", None), "coord_x", None)
        old_y = getattr(getattr(obj, "db", None), "coord_y", None)
        self.coord_index.move(obj, old_x, old_y, new_x, new_y)
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y
        if hasattr(obj, "at_coord_change"):
            obj.at_coord_change(old_x, old_y, new_x, new_y)

        # Notify players at old and new tiles
        self._notify_tile_change(obj, old_x, old_y, new_x, new_y)

    def _notify_tile_change(self, obj, old_x, old_y, new_x, new_y):
        """Send arrival/departure messages to players at affected tiles."""
        # Don't notify about player movement (they see the map update)
        if hasattr(obj, "has_account") and obj.has_account:
            return

        name = self._entity_display_name(obj)
        if not name:
            return

        # Compute cardinal direction of movement
        dx = int(new_x) - int(old_x) if old_x is not None else 0
        dy = int(new_y) - int(old_y) if old_y is not None else 0
        arrive_from = ""
        depart_toward = ""
        if dx == 1:
            arrive_from = " from the west"
            depart_toward = " to the east"
        elif dx == -1:
            arrive_from = " from the east"
            depart_toward = " to the west"
        elif dy == 1:
            arrive_from = " from the south"
            depart_toward = " to the north"
        elif dy == -1:
            arrive_from = " from the north"
            depart_toward = " to the south"

        # Notify players at the old tile
        if old_x is not None and old_y is not None:
            for player in self.get_players_at(int(old_x), int(old_y)):
                player.msg(f"|x{name} left{depart_toward}.|n")

        # Notify players at the new tile
        for player in self.get_players_at(int(new_x), int(new_y)):
            player.msg(f"|g{name} arrived{arrive_from}.|n")

    @staticmethod
    def _entity_display_name(obj) -> str:
        """Return a human-readable name for an entity, or empty string to skip."""
        # NPC agents
        if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
            aid = getattr(getattr(obj, "db", None), "agent_id", None)
            role = getattr(getattr(obj, "db", None), "role", "") or "agent"
            if aid:
                return f"Agent #{aid} ({role})"
            return getattr(obj, "key", "an NPC")
        # Buildings shouldn't notify (they don't move)
        if hasattr(obj, "tags") and obj.tags.get("building", category="object_type"):
            return ""
        # Other entities
        return getattr(obj, "key", "")

    # -------------------------------------------------------------- #
    #  Appearance — show map when looked at
    # -------------------------------------------------------------- #

    @property
    def _game_systems(self):
        """Cached reference to the game_systems dict."""
        systems = getattr(self, "_cached_game_systems", None)
        if systems is not None:
            return systems
        try:
            from server.conf.game_init import game_systems
            self._cached_game_systems = game_systems
            return game_systems
        except (ImportError, AttributeError):
            return {}

    def return_appearance(self, looker, **kwargs):
        """Return the room's appearance.

        Called by Evennia's look system (including auto-look on login).
        When inside a building, shows building interior first, then
        the overworld map below it. Otherwise shows just the map.
        """
        if not hasattr(looker, "db"):
            return super().return_appearance(looker, **kwargs)

        planet = getattr(looker.db, "coord_planet", None)
        if not planet:
            return super().return_appearance(looker, **kwargs)

        systems = self._game_systems
        parts = []

        # If inside a building, show building interior first
        if getattr(looker.db, "inside_building", False):
            try:
                x = getattr(looker.db, "coord_x", None)
                y = getattr(looker.db, "coord_y", None)
                if x is not None and y is not None:
                    buildings = self.get_buildings_at(int(x), int(y))
                    if buildings:
                        building = buildings[0]
                        registry = systems.get("registry")
                        parts.append(_format_building_interior(looker, building, registry=registry))
            except Exception:
                pass

        # Always render the overworld map
        try:
            renderer = systems.get("procedural_map_renderer")
            if renderer:
                buildings = looker.get_buildings() if hasattr(looker, "get_buildings") else []
                map_str = renderer.render(looker, buildings)
                if map_str:
                    x = getattr(looker.db, "coord_x", "?")
                    y = getattr(looker.db, "coord_y", "?")
                    looker.msg(text=(f"|wMap — ({x}, {y}) on {planet}|n\n{map_str}", {"cls": "ascii-map"}))
                    self._send_map_oob(looker, systems)
        except Exception:
            pass

        if parts:
            return "\n".join(parts)
        # Return empty so auto-look doesn't duplicate the map text
        return ""

    def _send_map_oob(self, looker, systems):
        """Send structured map data to the webclient via OOB message."""
        try:
            provider = systems.get("map_data_provider")
            if provider is None:
                return
            buildings = looker.get_buildings() if hasattr(looker, "get_buildings") else []
            data = provider.get_map_data(looker, buildings)
            fog = systems.get("fog_system")
            if fog:
                data["discovered_count"] = len(fog.get_discovered_tile_set(looker))
            looker.msg(map_update=data)
        except Exception:
            pass

    # -------------------------------------------------------------- #
    #  msg_contents override — proximity filter
    # -------------------------------------------------------------- #

    def msg_contents(self, text, exclude=None, from_obj=None, **kwargs):
        """Only message players within proximity (same tile as sender).

        If *from_obj* has coordinates, only players at the same
        (coord_x, coord_y) receive the message. Otherwise falls back
        to messaging all contents.
        """
        if from_obj is not None and hasattr(from_obj, "db"):
            sx = getattr(from_obj.db, "coord_x", None)
            sy = getattr(from_obj.db, "coord_y", None)
            if sx is not None and sy is not None:
                exclude = exclude or []
                for obj in self.contents:
                    if obj in exclude:
                        continue
                    if hasattr(obj, "db"):
                        ox = getattr(obj.db, "coord_x", None)
                        oy = getattr(obj.db, "coord_y", None)
                        if ox == sx and oy == sy:
                            if hasattr(obj, "msg"):
                                obj.msg(text, **kwargs)
                return

        # Fallback: broadcast to all (e.g. system messages)
        exclude = exclude or []
        for obj in self.contents:
            if obj in exclude:
                continue
            if hasattr(obj, "msg"):
                obj.msg(text, **kwargs)

    # -------------------------------------------------------------- #
    #  at_object_receive — show tile info based on player coords
    # -------------------------------------------------------------- #

    def at_object_receive(self, moved_obj, source_location, **kwargs):
        """Show tile info when a player arrives, based on their coordinates.

        Also adds the arriving object to the coordinate index if it has
        valid coordinates.
        """
        super().at_object_receive(moved_obj, source_location, **kwargs)

        # Update coordinate index for any object with coordinates
        cx = getattr(getattr(moved_obj, "db", None), "coord_x", None)
        cy = getattr(getattr(moved_obj, "db", None), "coord_y", None)
        if cx is not None and cy is not None:
            self.coord_index.add(moved_obj, int(cx), int(cy))

        if not (hasattr(moved_obj, "has_account") and moved_obj.has_account):
            return

        # Read coordinates from the player, not the room
        x = getattr(moved_obj.db, "coord_x", None) if hasattr(moved_obj, "db") else None
        y = getattr(moved_obj.db, "coord_y", None) if hasattr(moved_obj, "db") else None
        planet = getattr(moved_obj.db, "coord_planet", None) if hasattr(moved_obj, "db") else None

        if x is None or y is None or not planet:
            return

        # Get terrain info from the terrain generator
        try:
            generators = self._game_systems.get("_terrain_generators", {})
            gen = generators.get(planet)
            if gen:
                terrain_type, resource_type = gen.get_terrain_and_resource(x, y)
            else:
                terrain_type = "unknown"
                resource_type = None
        except (ImportError, AttributeError):
            terrain_type = "unknown"
            resource_type = None

        parts = [f"Terrain: {terrain_type}"]
        if resource_type:
            parts.append(f"Resource: {resource_type}")

        # Show other players at the same tile
        other_names = []
        for obj in self.contents:
            if obj is moved_obj:
                continue
            if hasattr(obj, "has_account") and obj.has_account and hasattr(obj, "db"):
                ox = getattr(obj.db, "coord_x", None)
                oy = getattr(obj.db, "coord_y", None)
                if ox == x and oy == y:
                    other_names.append(obj.key)
        if other_names:
            parts.append(f"Players: {', '.join(other_names)}")

        moved_obj.msg(" | ".join(parts))

    def at_object_leave(self, moved_obj, target_location, **kwargs):
        """Remove departing object from the coordinate index."""
        super().at_object_leave(moved_obj, target_location, **kwargs)
        cx = getattr(getattr(moved_obj, "db", None), "coord_x", None)
        cy = getattr(getattr(moved_obj, "db", None), "coord_y", None)
        if cx is not None and cy is not None:
            idx = self.ndb._coord_index
            if idx is not None:
                idx.remove(moved_obj, int(cx), int(cy))

    # -------------------------------------------------------------- #
    #  Resource Node Depletion (string keys for JSON compat)
    # -------------------------------------------------------------- #

    @staticmethod
    def _node_key(x: int, y: int) -> str:
        return f"{x},{y}"

    def get_depleted_nodes(self) -> dict:
        """Return the sparse depletion dict: ``{"x,y": {resource_type, respawn_counter}}``."""
        return self.db.depleted_nodes or {}

    def set_node_depleted(self, x: int, y: int, resource_type: str, respawn_counter: int):
        """Mark the resource node at (x, y) as depleted."""
        nodes = self.db.depleted_nodes or {}
        nodes[self._node_key(x, y)] = {
            "resource_type": resource_type,
            "respawn_counter": respawn_counter,
        }
        self.db.depleted_nodes = nodes

    def clear_node_depletion(self, x: int, y: int):
        """Remove the depletion entry for (x, y), marking it available."""
        nodes = self.db.depleted_nodes or {}
        nodes.pop(self._node_key(x, y), None)
        self.db.depleted_nodes = nodes

    def is_node_depleted(self, x: int, y: int) -> bool:
        """Return True if the node at (x, y) is currently depleted."""
        nodes = self.db.depleted_nodes or {}
        return self._node_key(x, y) in nodes


# ------------------------------------------------------------------ #
#  Helper for building interior display from PlanetRoom
# ------------------------------------------------------------------ #

def _format_building_interior(looker, building, registry=None):
    """Format building interior as a string for return_appearance."""
    from world.utils import get_building_info, get_building_attr, get_closed_exits

    info = get_building_info(building)
    owner = info["owner"]
    owner_name = getattr(owner, "key", str(owner)) if owner else "nobody"

    category = "unknown"
    produces = "—"
    unlocks_str = "—"
    if registry is None:
        try:
            from server.conf.game_init import game_systems
            registry = game_systems.get("registry")
        except Exception:
            pass
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
    bx = getattr(getattr(building, "db", None), "coord_x", None)
    by = getattr(getattr(building, "db", None), "coord_y", None)
    tile = getattr(building, "location", None)

    # Show assigned agent
    assigned = get_building_attr(building, "assigned_agent")
    if assigned is not None:
        aid = getattr(getattr(assigned, "db", None), "agent_id", "?")
        role = getattr(getattr(assigned, "db", None), "role", "") or "idle"
        activity = getattr(getattr(assigned, "db", None), "activity_status", None) or "Idle"

        # Check if the agent is physically at this building's tile
        agent_x = getattr(getattr(assigned, "db", None), "coord_x", None)
        agent_y = getattr(getattr(assigned, "db", None), "coord_y", None)
        at_building = (
            agent_x is not None and agent_y is not None
            and bx is not None and by is not None
            and int(agent_x) == int(bx) and int(agent_y) == int(by)
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
            lines.append(f"  |yResources: {', '.join(drops)}|n")
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
            lines.append(f"  |yResources: {', '.join(drops)}|n")
            lines.append(f"  Use |wget|n to pick them up.")

    # Show other agents at the building's coordinates
    if tile is not None and bx is not None and by is not None and hasattr(tile, "get_objects_at"):
        tile_agents = []
        for obj in tile.get_objects_at(int(bx), int(by)):
            if obj is building:
                continue
            if obj is assigned:
                continue  # already shown above
            if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
                npc_owner = getattr(getattr(obj, "db", None), "owner", None)
                if npc_owner is looker:
                    aid = getattr(obj.db, "agent_id", "?")
                    role = getattr(obj.db, "role", "") or "idle"
                    tile_agents.append(f"Agent #{aid} ({role})")
        if tile_agents:
            lines.append(f"  Agents here: {', '.join(tile_agents)}")
    elif tile is not None:
        # Legacy fallback
        tile_agents = []
        for obj in getattr(tile, "contents", []):
            if obj is building:
                continue
            if obj is assigned:
                continue
            if hasattr(obj, "tags") and obj.tags.get(category="npc_type"):
                npc_owner = getattr(getattr(obj, "db", None), "owner", None)
                if npc_owner is looker:
                    aid = getattr(obj.db, "agent_id", "?")
                    role = getattr(obj.db, "role", "") or "idle"
                    tile_agents.append(f"Agent #{aid} ({role})")
        if tile_agents:
            lines.append(f"  Agents here: {', '.join(tile_agents)}")

    lines.append("")
    lines.append(f"  Exits: {', '.join(exit_parts)}")

    return "\n".join(lines)
