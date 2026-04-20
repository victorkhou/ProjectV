"""
Object

The Object is the class for general items in the game world.

Use the ObjectParent class to implement common features for *all* entities
with a location in the game world (like Characters, Rooms, Exits).

"""

from __future__ import annotations

from evennia.objects.objects import DefaultObject

from world.definitions import BuildingDef


class ObjectParent:
    """
    This is a mixin that can be used to override *all* entities inheriting at
    some distance from DefaultObject (Objects, Exits, Characters and Rooms).

    Just add any method that exists on `DefaultObject` to this class. If one
    of the derived classes has itself defined that same hook already, that will
    take precedence.

    """


class Object(ObjectParent, DefaultObject):
    pass


# ------------------------------------------------------------------ #
#  Game object base
# ------------------------------------------------------------------ #

class GameEntity(DefaultObject):
    """Base class for all game-world objects (buildings, items, drops, etc).

    Provides a consistent pattern for:
    - Tag-based type identification via ``object_type`` category
    - Structured state export for UI/API
    - Display name customization

    Subclasses should:
    1. Set ``_object_type_tag`` to a unique string (e.g. "building")
    2. Override ``at_object_creation`` (calling super) to set attributes
    3. Override ``get_structured_state`` for UI export

    All game entities are tagged ``(tag, "object_type")`` at creation
    for efficient DB queries and room-content filtering.
    """

    #: Override in subclasses — the tag value for this object type.
    #: Used by at_object_creation to auto-tag, and by classmethods
    #: to query objects of this type.
    _object_type_tag: str = "game_entity"

    def at_object_creation(self):
        """Tag the object and initialize coordinate attributes."""
        if self._object_type_tag:
            self.tags.add(self._object_type_tag, category="object_type")
        # Coordinate attributes — None means "not placed on the map"
        self.db.coord_x = None
        self.db.coord_y = None

    def at_object_delete(self):
        """Remove self from the PlanetRoom coordinate index before deletion.

        Evennia's ``delete()`` does NOT call ``at_object_leave`` on the
        location, so the coordinate index would otherwise retain a
        reference to the deleted object. Any subsequent
        ``get_objects_at`` would iterate over a stale reference and
        raise a Django M2M error when touching ``.tags``.

        Returns True to allow deletion to proceed.
        """
        room = self.location
        cx = getattr(self.db, "coord_x", None)
        cy = getattr(self.db, "coord_y", None)
        if room is not None and cx is not None and cy is not None:
            idx = getattr(getattr(room, "ndb", None), "_coord_index", None)
            if idx is not None:
                try:
                    idx.remove(self, int(cx), int(cy))
                except Exception:
                    pass
        return True

    def at_pre_get(self, getter, **kwargs):
        """Block pickup if getter is not at the same coordinates."""
        if self.db.coord_x is None:
            return True  # not placed, allow
        gx = getattr(getattr(getter, "db", None), "coord_x", None)
        gy = getattr(getattr(getter, "db", None), "coord_y", None)
        if gx is None or gy is None:
            return False
        if int(gx) != int(self.db.coord_x) or int(gy) != int(self.db.coord_y):
            getter.msg("That's not here.")
            return False
        return True

    def get_structured_state(self) -> dict:
        """Return a presentation-agnostic dict of this object's state.

        Override in subclasses to add type-specific fields.
        """
        return {
            "key": self.key,
            "type_tag": self._object_type_tag,
        }

    @classmethod
    def get_all(cls) -> list:
        """Query all objects of this type from the DB via tag."""
        try:
            from evennia.utils.search import search_object_by_tag
            return list(search_object_by_tag(
                key=cls._object_type_tag, category="object_type"
            ))
        except Exception:
            return []

    @classmethod
    def get_in_room(cls, room) -> list:
        """Return all objects of this type in a room's contents."""
        tag = cls._object_type_tag
        results = []
        for obj in getattr(room, "contents", []):
            if hasattr(obj, "tags") and obj.tags.get(tag, category="object_type"):
                results.append(obj)
        return results


class GameItem(GameEntity):
    """A unified item object. Slot type and stats come from the item definition.

    All equippable/usable items use this single typeclass. Items are
    differentiated entirely by their YAML-defined properties — no subclasses
    needed for weapons, armor, gadgets, etc.

    Attributes set at creation from ItemDef:
        item_key (str) — references the ItemDef in DataRegistry
        slot (str) — "weapon", "armor", "gadget", "consumable", etc.
        stat_modifiers (dict) — {"damage": 25, "range": 3} etc.
        ammo_cost (dict | None) — {"iron": 1} or None
        classification (str) — "modern" or "futuristic"
        required_rank (str | None) — rank name or None

    Requirements: 18.6, 18.7
    """

    _object_type_tag = "item"

    def at_get(self, getter, **kwargs):
        """Clear coordinates when picked up."""
        self.db.coord_x = None
        self.db.coord_y = None

    def at_drop(self, dropper, **kwargs):
        """Set coordinates to dropper's position when dropped."""
        if hasattr(dropper, "db"):
            self.db.coord_x = getattr(dropper.db, "coord_x", None)
            self.db.coord_y = getattr(dropper.db, "coord_y", None)

    @property
    def item_def(self):
        """Look up the ItemDef from DataRegistry by item_key."""
        item_key = self.attributes.get("item_key", default=None)
        if item_key is None:
            return None
        try:
            from world.data_registry import DataRegistry
            import world.data_registry as dr_mod
            if hasattr(dr_mod, "registry"):
                return dr_mod.registry.get_item(item_key)
        except Exception:
            pass
        return None

    @property
    def slot(self) -> str:
        """Return the equipment slot for this item."""
        return self.attributes.get("slot", default="")

    @property
    def stat_modifiers(self) -> dict[str, float]:
        """Return the stat modifiers dict for this item."""
        return self.attributes.get("stat_modifiers", default={})

    @property
    def ammo_cost(self) -> dict[str, int] | None:
        """Return the ammo cost dict, or None if no ammo required."""
        return self.attributes.get("ammo_cost", default=None)

    @property
    def classification(self) -> str:
        """Return the item classification (modern/futuristic)."""
        return self.attributes.get("classification", default="modern")

    @property
    def required_rank(self) -> str | None:
        """Return the required rank name, or None."""
        return self.attributes.get("required_rank", default=None)

    def get_stat(self, stat_name: str, default: float = 0) -> float:
        """Return the value of a stat modifier, or the default.

        Args:
            stat_name: The stat key to look up (e.g. "damage", "range").
            default: Value to return if the stat is not present.

        Returns:
            The stat value as a float.
        """
        mods = self.stat_modifiers
        return float(mods.get(stat_name, default))

    def get_structured_state(self) -> dict:
        """Return a presentation-agnostic dict of this item's state."""
        return {
            "item_key": self.attributes.get("item_key", default=""),
            "name": self.key if hasattr(self, "key") else "",
            "slot": self.slot,
            "stat_modifiers": self.stat_modifiers,
            "ammo_cost": self.ammo_cost,
            "classification": self.classification,
            "required_rank": self.required_rank,
        }


class Building(GameEntity):
    """A building placed on an overworld tile.

    Uses simple Evennia Attributes for all persistent state so the class
    works without the Traits contrib in test environments.

    Attributes set at creation:
        building_type (str) — abbreviation referencing BuildingDef
        owner (Character dbref or ref)
        building_level (int) — 1-5 for resource buildings
        offline (bool) — True when owner is logged out
        hp (int) — current health points
        hp_max (int) — maximum health points

    Requirements: 3.6, 3.7, 3.8, 10.1, 10.5, 27.1
    """

    _object_type_tag = "building"

    # ------------------------------------------------------------------ #
    #  Properties
    # ------------------------------------------------------------------ #

    @property
    def building_def(self) -> BuildingDef | None:
        """Look up the BuildingDef from DataRegistry by building_type."""
        btype = self.attributes.get("building_type", default=None)
        if btype is None:
            return None
        try:
            from world.data_registry import DataRegistry
            import world.data_registry as dr_mod
            if hasattr(dr_mod, "registry"):
                return dr_mod.registry.get_building(btype)
        except Exception:
            pass
        return None

    @property
    def owner(self):
        """Return the owning character object/ref."""
        return self.attributes.get("owner", default=None)

    @property
    def is_offline(self) -> bool:
        """Return True if this building is in offline-protection state."""
        return bool(self.attributes.get("offline", default=False))

    @property
    def building_level(self) -> int:
        """Return the building level (1-5 for resource buildings)."""
        return self.attributes.get("building_level", default=1)

    # ------------------------------------------------------------------ #
    #  Mutators
    # ------------------------------------------------------------------ #

    def set_offline(self, state: bool) -> None:
        """Set the offline protection state."""
        self.attributes.add("offline", state)

    def take_damage(self, amount: int, attacker=None) -> None:
        """Apply *amount* damage to this building's HP.

        If HP reaches zero the building goes offline (Req 6.9) rather
        than being destroyed.  Publishes ``building_destroyed`` when HP
        hits 0.
        """
        hp = self.attributes.get("hp", default=0)
        hp = max(0, hp - amount)
        self.attributes.add("hp", hp)

        if hp <= 0:
            # Req 6.9: set offline instead of destroying
            self.set_offline(True)
            try:
                from world.event_bus import event_bus, BUILDING_DESTROYED
                event_bus.publish(
                    BUILDING_DESTROYED,
                    attacker=attacker,
                    building=self,
                    tile=self.location,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Display helpers
    # ------------------------------------------------------------------ #

    def get_display_abbreviation(self) -> str:
        """Return the 2-char map abbreviation for this building."""
        btype = self.attributes.get("building_type", default="??")
        return str(btype)[:2]

    # ------------------------------------------------------------------ #
    #  Structured state (Requirement 27.1)
    # ------------------------------------------------------------------ #

    def get_structured_state(self) -> dict:
        """Return a presentation-agnostic dict of this building's state."""
        owner = self.owner
        owner_name = ""
        if owner is not None:
            owner_name = owner.key if hasattr(owner, "key") else str(owner)
        return {
            "building_type": self.attributes.get("building_type", default=""),
            "name": self.key if hasattr(self, "key") else "",
            "owner": owner_name,
            "building_level": self.building_level,
            "hp": self.attributes.get("hp", default=0),
            "hp_max": self.attributes.get("hp_max", default=0),
            "offline": self.is_offline,
        }


class ResourceDrop(GameEntity):
    """A stack of resources dropped on the ground or in a building.

    Lightweight Evennia object representing harvestable/collectable
    resources. Uses stacking: multiple drops of the same type on the
    same tile merge into one object with a higher ``amount``.

    Attributes:
        resource_type (str): "Wood", "Stone", "Iron", etc.
        amount (int): How many units in this stack.

    Tags:
        ``resource_drop`` (category ``object_type``) — for DB queries.
    """

    _object_type_tag = "resource_drop"

    def at_object_creation(self):
        """Set defaults."""
        super().at_object_creation()
        self.db.resource_type = ""
        self.db.amount = 0
        self.locks.add("get:all()")

    def get_display_name(self, looker=None, **kwargs):
        """Show as '5 Wood' instead of the object key."""
        amt = self.db.amount or 0
        rtype = self.db.resource_type or "Resource"
        return f"{amt} {rtype}"

    def get_numbered_name(self, count, looker, **kwargs):
        """Support Evennia's stacking display."""
        return self.get_display_name(looker), self.key

    def at_get(self, getter, **kwargs):
        """When picked up, add to the getter's resources and schedule deletion.

        The object has already been moved to the getter's inventory by
        Evennia's get command. We add the resources, then schedule
        deletion on the next tick to avoid issues with the command
        referencing the object after this hook.
        """
        amt = self.db.amount or 0
        rtype = self.db.resource_type or ""
        if amt > 0 and rtype and hasattr(getter, "add_resource"):
            getter.add_resource(rtype, amt)
            total = getter.get_resource(rtype) if hasattr(getter, "get_resource") else amt
            getter.msg(f"Picked up {amt} {rtype} (total: {total}).")
        # Zero out so it can't be double-collected
        self.db.amount = 0
        # Delete after the command finishes processing
        from evennia.utils import delay
        delay(0, self.delete)

    def at_pre_get(self, getter, **kwargs):
        """Block pickup if getter is not at the same coordinates."""
        if self.db.coord_x is None:
            return True  # not placed on the map, allow
        gx = getattr(getattr(getter, "db", None), "coord_x", None)
        gy = getattr(getattr(getter, "db", None), "coord_y", None)
        if gx is None or gy is None:
            return False
        if int(gx) != int(self.db.coord_x) or int(gy) != int(self.db.coord_y):
            getter.msg("That's not here.")
            return False
        return True


def spawn_resource_drop(location, resource_type, amount, x=None, y=None):
    """Create or merge a ResourceDrop at *location*.

    If a ResourceDrop of the same type already exists at the location
    (and at the same coordinates when x/y are provided), adds to it
    instead of creating a new object.

    Args:
        location: Room/tile to place the drop in (PlanetRoom or legacy room).
        resource_type: "Wood", "Stone", etc.
        amount: Number of units.
        x: Optional x coordinate for PlanetRoom-based placement.
        y: Optional y coordinate for PlanetRoom-based placement.

    Returns:
        The ResourceDrop object.
    """
    if amount <= 0:
        return None

    # When coordinates are provided, use PlanetRoom coordinate query for merge
    if x is not None and y is not None and hasattr(location, "get_objects_at"):
        for obj in location.get_objects_at(x, y, type_tag="resource_drop"):
            if getattr(obj.db, "resource_type", None) == resource_type:
                obj.db.amount = (obj.db.amount or 0) + amount
                return obj
    else:
        # Legacy path: merge with any drop of same type in the room
        for obj in ResourceDrop.get_in_room(location):
            if getattr(obj.db, "resource_type", None) == resource_type:
                obj.db.amount = (obj.db.amount or 0) + amount
                return obj

    # Create new drop
    import evennia
    drop = evennia.create_object(
        "typeclasses.objects.ResourceDrop",
        key=resource_type,
        location=location,
    )
    drop.db.resource_type = resource_type
    drop.db.amount = amount
    if x is not None and y is not None:
        drop.db.coord_x = x
        drop.db.coord_y = y
        # at_object_receive saw coord_x=None during create_object,
        # so manually register in the coordinate index now.
        if hasattr(location, "coord_index"):
            location.coord_index.add(drop, x, y)
    return drop
