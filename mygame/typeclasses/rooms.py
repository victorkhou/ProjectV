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

    def get_nearby_players(self, x: int, y: int, radius: int) -> list:
        """Return player characters within Manhattan distance *radius* of (x, y).

        Used by turret auto-fire (and, later, guard AI) for target acquisition.
        Visits only the (2*radius+1)² candidate tiles via the coordinate index's
        O(1) ``get_at`` — cost is O(radius²), independent of total map
        population — rather than scanning every occupied bucket. Filters to the
        Manhattan-distance disc so the result matches the combat engine's range
        model (which also uses Manhattan distance).

        Skips index entries whose DB row was deleted (``pk is None``) — a delete
        path that bypasses ``at_object_leave`` can leave a stale ref in the
        in-memory index, and touching a deleted object's attributes raises. This
        matches the guard in :meth:`get_objects_at`, and matters more here
        because turret fire hits this path every tick.
        """
        players = []
        for cx in range(x - radius, x + radius + 1):
            for cy in range(y - radius, y + radius + 1):
                if abs(cx - x) + abs(cy - y) > radius:
                    continue  # outside the Manhattan disc (box corner)
                for o in self.coord_index.get_at(cx, cy):
                    if getattr(o, "pk", True) is None:
                        continue  # stale ref to a deleted object
                    if hasattr(o, "has_account") and o.has_account:
                        players.append(o)
        return players

    def get_objects_in_area(self, x1: int, y1: int, x2: int, y2: int) -> list:
        """Return all objects within the bounding box (inclusive)."""
        return self.coord_index.get_in_area(x1, y1, x2, y2)

    # -------------------------------------------------------------- #
    #  Mutation — coordinate changes
    # -------------------------------------------------------------- #

    def move_entity(self, obj, new_x: int, new_y: int, notify: bool = True) -> None:
        """Atomically update an object's coordinates and the index.

        Fires ``at_coord_change(old_x, old_y, new_x, new_y)`` on the
        object if the hook exists, for game systems that need to react.

        When *notify* is True (the default, for step-by-step tile movement),
        notifies players at the old tile that the entity left and players at
        the new tile that it arrived. Pass ``notify=False`` for non-adjacent
        relocations where arrival/departure messaging is meaningless or wrong —
        e.g. an admin teleport, especially a cross-planet one, where the stored
        ``old_x/old_y`` are the ORIGIN planet's coordinates and would notify
        unrelated players who merely share those coords on the destination room.
        """
        old_x = getattr(getattr(obj, "db", None), "coord_x", None)
        old_y = getattr(getattr(obj, "db", None), "coord_y", None)
        self.coord_index.move(obj, old_x, old_y, new_x, new_y)
        obj.db.coord_x = new_x
        obj.db.coord_y = new_y
        if hasattr(obj, "at_coord_change"):
            obj.at_coord_change(old_x, old_y, new_x, new_y)

        # Notify players at old and new tiles (skipped for teleports/relocations).
        if notify:
            self._notify_tile_change(obj, old_x, old_y, new_x, new_y)

    def _notify_tile_change(self, obj, old_x, old_y, new_x, new_y):
        """Send arrival/departure messages to players at affected tiles.

        Applies to agents AND players: when anyone (or anything named) leaves
        the tile you're standing on or arrives on it, you're told. The mover
        itself is never notified — a moving player already gets their own "You
        move..." line and map update, so we exclude ``obj`` from the recipients.
        """
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

        # Notify OTHER players at the old tile (exclude the mover itself).
        if old_x is not None and old_y is not None:
            for player in self.get_players_at(int(old_x), int(old_y)):
                if player is obj:
                    continue
                player.msg(f"|x{name} left{depart_toward}.|n")

        # Notify OTHER players at the new tile (exclude the mover itself).
        for player in self.get_players_at(int(new_x), int(new_y)):
            if player is obj:
                continue
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
                        from world.ui_formatters import format_building_interior
                        parts.append(format_building_interior(looker, building, registry=registry))
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
