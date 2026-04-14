"""
Room

Rooms are simple containers that has no location of their own.

"""

from evennia.objects.objects import DefaultRoom

from .objects import ObjectParent


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

    Buildings still get their own OverworldRoom at their coordinates.
    """

    @property
    def planet_name(self) -> str:
        return self.attributes.get("planet", default="unknown")

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
                tile_resolver = systems.get("tile_resolver")
                if tile_resolver:
                    x = getattr(looker.db, "coord_x", None)
                    y = getattr(looker.db, "coord_y", None)
                    if x is not None and y is not None:
                        tile = tile_resolver.get_if_exists(x, y, planet)
                        if tile:
                            building = getattr(tile, "building", None)
                            if building:
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
        """Show tile info when a player arrives, based on their coordinates."""
        super().at_object_receive(moved_obj, source_location, **kwargs)

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


class OverworldRoom(DefaultRoom):
    """A single tile on the overworld map.

    Extends DefaultRoom (not XYZRoom). Coordinates stored as Attributes.

    In the one-room-per-planet architecture, OverworldRoom is used only
    for tiles that contain buildings. Players live in PlanetRoom.

    Requirements: 7.1, 7.2, 8.1
    """

    # -------------------------------------------------------------- #
    #  Coordinate properties (read from Attributes)
    # -------------------------------------------------------------- #

    @property
    def x(self) -> int:
        return self.attributes.get("x", default=0)

    @property
    def y(self) -> int:
        return self.attributes.get("y", default=0)

    @property
    def planet_name(self) -> str:
        return self.attributes.get("planet", default="unknown")

    # -------------------------------------------------------------- #
    #  Properties
    # -------------------------------------------------------------- #

    @property
    def terrain_type(self) -> str:
        """Return the terrain type string from the room's Tag."""
        tag = self.tags.get(category="terrain", return_list=False)
        return tag or "unknown"

    @property
    def resource_node(self) -> dict | None:
        """Return the resource node data dict, or ``None``.

        Resource node state is stored as an Attribute
        ``resource_node_data`` with keys:
            resource_type (str), depleted (bool), respawn_counter (int)
        """
        data = self.attributes.get("resource_node_data", default=None)
        return data if data else None

    @property
    def building(self):
        """Return the first Building object in this room, or ``None``.

        Detects buildings by checking for a ``building_type`` Attribute
        (set by the BuildingSystem on all building objects).
        """
        for obj in self.contents:
            if hasattr(obj, "attributes") and obj.attributes.has("building_type"):
                return obj
        return None

    # -------------------------------------------------------------- #
    #  Display
    # -------------------------------------------------------------- #

    def get_display_symbol(self, looker) -> str:
        """Return a 2-char symbol for this tile with priority logic.

        Priority (Requirement 1.8):
            1. ``@@`` if *looker* is on this tile
            2. ``**`` if another player character is on this tile
            3. Building abbreviation (e.g. ``HQ``) if a building is present
            4. Terrain symbol from the terrain tag
        """
        # Check for player characters on this tile
        for obj in self.contents:
            # A "player character" is any object with an associated account
            if hasattr(obj, "has_account") and obj.has_account:
                if obj is looker:
                    return "@@"
                return "**"

        # Check for a building
        bld = self.building
        if bld is not None:
            # Try to get the building's display abbreviation
            if hasattr(bld, "get_display_abbreviation"):
                return bld.get_display_abbreviation()
            # Fallback: read building_type attribute
            abbr = bld.attributes.get("building_type", default=None)
            if abbr:
                return str(abbr)[:2]

        # Fallback to terrain symbol
        return self._terrain_symbol()

    def _terrain_symbol(self) -> str:
        """Return the 2-char terrain map symbol.

        Tries to look up the symbol from the DataRegistry; falls back
        to the first two characters of the terrain_type string.
        """
        terrain = self.terrain_type
        try:
            from world.data_registry import registry

            terrain_def = registry.get_terrain(terrain)
            return terrain_def.map_symbol
        except Exception:
            # DataRegistry not available or terrain not found — use
            # first two chars of the terrain type as a safe fallback.
            return terrain[:2] if len(terrain) >= 2 else terrain.ljust(2, "?")

    # -------------------------------------------------------------- #
    #  Hooks
    # -------------------------------------------------------------- #

    def at_object_receive(self, moved_obj, source_location, **kwargs):
        """Called when an object arrives in this room.

        When a player character enters, display tile information.
        """
        super().at_object_receive(moved_obj, source_location, **kwargs)

        # Only show tile info to player characters
        if not (hasattr(moved_obj, "has_account") and moved_obj.has_account):
            return

        state = self.get_structured_state()
        parts = [f"Terrain: {state['terrain_type']}"]

        if state.get("resource_node"):
            rn = state["resource_node"]
            if rn.get("depleted"):
                parts.append(f"Resource: {rn['resource_type']} (depleted)")
            else:
                parts.append(f"Resource: {rn['resource_type']}")

        if state.get("building"):
            bld_info = state["building"]
            parts.append(f"Building: {bld_info.get('name', bld_info.get('type', 'unknown'))}")

        if state.get("players"):
            other_names = [
                p for p in state["players"] if p != moved_obj.key
            ]
            if other_names:
                parts.append(f"Players: {', '.join(other_names)}")

        moved_obj.msg(" | ".join(parts))

    # -------------------------------------------------------------- #
    #  msg_contents override — coordinate-aware proximity filter
    # -------------------------------------------------------------- #

    def msg_contents(self, text, exclude=None, from_obj=None, **kwargs):
        """Only message players at the same coordinates as the sender.

        OverworldRoom tiles have fixed coordinates. If from_obj has
        coordinates matching this room, broadcast to all contents.
        Otherwise fall back to default behavior.
        """
        exclude = exclude or []
        for obj in self.contents:
            if obj in exclude:
                continue
            if hasattr(obj, "msg"):
                obj.msg(text, **kwargs)

    # -------------------------------------------------------------- #
    #  Structured state (Requirement 27.1)
    # -------------------------------------------------------------- #

    def get_structured_state(self) -> dict:
        """Return a presentation-agnostic dict of this tile's state.

        Keys:
            terrain_type (str): The terrain type string.
            resource_node (dict | None): Resource node info or None.
            building (dict | None): Building info or None.
            players (list[str]): Names of player characters on this tile.
        """
        # Terrain
        terrain = self.terrain_type

        # Resource node
        rn = self.resource_node
        rn_info = None
        if rn:
            rn_info = {
                "resource_type": rn.get("resource_type", "unknown"),
                "depleted": rn.get("depleted", False),
                "respawn_counter": rn.get("respawn_counter", 0),
            }

        # Building
        bld = self.building
        bld_info = None
        if bld is not None:
            bld_info = {
                "type": bld.attributes.get("building_type", default="unknown"),
                "name": bld.key if hasattr(bld, "key") else "unknown",
            }
            # Include optional fields if available
            if hasattr(bld, "building_level"):
                bld_info["level"] = bld.building_level
            elif bld.attributes.has("building_level"):
                bld_info["level"] = bld.attributes.get("building_level")
            owner = bld.attributes.get("owner", default=None)
            if owner is not None:
                bld_info["owner"] = str(owner)

        # Players
        players = [
            obj.key
            for obj in self.contents
            if hasattr(obj, "has_account") and obj.has_account
        ]

        return {
            "terrain_type": terrain,
            "resource_node": rn_info,
            "building": bld_info,
            "players": players,
        }


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

    # Show assigned agent
    assigned = get_building_attr(building, "assigned_agent")
    if assigned is not None:
        aid = getattr(getattr(assigned, "db", None), "agent_id", "?")
        role = getattr(getattr(assigned, "db", None), "role", "") or "idle"
        lines.append(f"  |gAgent #{aid}|n assigned as |w{role}|n")

    # Show other agents on this tile (in the room contents)
    tile = getattr(building, "location", None)
    if tile is not None:
        tile_agents = []
        for obj in getattr(tile, "contents", []):
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

    lines.append("")
    lines.append(f"  Exits: {', '.join(exit_parts)}")

    return "\n".join(lines)
