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


class OverworldRoom(DefaultRoom):
    """A single tile on the overworld map.

    Extends DefaultRoom (not XYZRoom). Coordinates stored as Attributes.

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
