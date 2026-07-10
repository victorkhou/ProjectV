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
        slot (str) — one of EQUIPMENT_SLOTS (e.g. "weapon", "torso"); "" for Supplies
        category (str) — armor|weapon|accessory|ammo|consumable|throwable
        stat_modifiers (dict) — {"damage": 25, "range": 3} etc.
        weapon_type (str | None) — "melee" or "ranged" (weapon category)
        ammo_type (str | None) — ammo item key the magazine holds (ranged)
        ammo_per_shot (int) — rounds drawn from the magazine per shot
        magazine_size (int | None) — magazine capacity (ranged)
        ammo_cost (dict | None) — {"iron": 1} or None
        effect (dict | None) — {"type": ..., ...} for consumables/throwables
        max_stack (int) — per-entry Supply_Bag stack cap
        weight (float) — per-unit carried weight
        classification (str) — "modern" or "futuristic"
        required_rank (str | None) — rank name or None

    """

    _object_type_tag = "item"

    def at_get(self, getter, **kwargs):
        """Handle pickup.

        A counted **Supply drop** (a GameItem carrying ``db.count`` + an
        ``item_key`` — spawned by ``spawn_supply_drop`` when an over-capacity
        pickup spills, see ``EquipmentSystem.add_supply_drop``) is routed into
        the getter's Supply_Bag through the ``EquipmentSystem`` choke point so
        the carry-weight / ``max_stack`` cap applies and any un-carryable
        remainder spills back to a fresh ground drop (Req 10.5). The drop object
        is then consumed. Falls back to leaving the object in inventory when no
        equipment system is available so pickup never hard-breaks.

        A plain equippable **Gear** GameItem (no ``db.count``) just has its
        coordinates cleared, as before.
        """
        count = getattr(self.db, "count", None)
        item_key = getattr(self.db, "item_key", None)
        if count is not None and item_key:
            from world.utils import get_system
            equipment_system = get_system(getter, "equipment_system")
            if equipment_system is not None and hasattr(
                equipment_system, "add_supply_drop"
            ):
                added = 0
                if count > 0:
                    added = equipment_system.add_supply_drop(
                        getter, item_key, int(count)
                    )
                if added > 0 and hasattr(getter, "msg"):
                    getter.msg(f"Picked up {added} {self.key}.")
                # Units are now accounted for in the Supply_Bag (and any
                # remainder respawned as its own drop); consume this object.
                self.db.count = 0
                self.db.coord_x = None
                self.db.coord_y = None
                from evennia.utils import delay
                delay(0, self.delete)
                return
        # Plain Gear item: clear coordinates when picked up.
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

    @property
    def category(self) -> str:
        """Return the item category (armor/weapon/accessory/ammo/consumable/throwable)."""
        return self.attributes.get("category", default="armor")

    @property
    def weapon_type(self) -> str | None:
        """Return the weapon type (melee/ranged), or None for non-weapons."""
        return self.attributes.get("weapon_type", default=None)

    @property
    def ammo_type(self) -> str | None:
        """Return the ammo item key the magazine holds (ranged), or None."""
        return self.attributes.get("ammo_type", default=None)

    @property
    def ammo_per_shot(self) -> int:
        """Return the number of rounds drawn from the magazine per shot."""
        return self.attributes.get("ammo_per_shot", default=1)

    @property
    def magazine_size(self) -> int | None:
        """Return the magazine capacity (ranged), or None."""
        return self.attributes.get("magazine_size", default=None)

    @property
    def effect(self) -> dict | None:
        """Return the item effect dict for consumables/throwables, or None."""
        return self.attributes.get("effect", default=None)

    @property
    def max_stack(self) -> int:
        """Return the per-entry Supply_Bag stack cap."""
        return self.attributes.get("max_stack", default=99)

    @property
    def weight(self) -> float:
        """Return the per-unit carried weight (>= 0)."""
        return self.attributes.get("weight", default=1.0)

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
            "category": self.category,
            "stat_modifiers": self.stat_modifiers,
            "weapon_type": self.weapon_type,
            "ammo_type": self.ammo_type,
            "ammo_per_shot": self.ammo_per_shot,
            "magazine_size": self.magazine_size,
            "ammo_cost": self.ammo_cost,
            "effect": self.effect,
            "max_stack": self.max_stack,
            "weight": self.weight,
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

    """

    _object_type_tag = "building"

    # ------------------------------------------------------------------ #
    #  Lifecycle — keep the tick loop's building-index cache fresh
    # ------------------------------------------------------------------ #

    def at_object_creation(self):
        """Tag/init as a GameEntity, then invalidate the building-index cache."""
        super().at_object_creation()
        self._bump_building_index()

    def at_object_delete(self):
        """Clean up the coordinate index, then invalidate the building cache."""
        result = super().at_object_delete()
        self._bump_building_index()
        return result

    @staticmethod
    def _bump_building_index() -> None:
        """Advance the building-index generation so the tick loop re-searches.

        Guarded so a building create/delete never fails if the counter module
        is somehow unavailable (defensive; it is a pure-stdlib module).
        """
        try:
            from world import building_index
            building_index.bump()
        except Exception:  # noqa: BLE001 - cache freshness must not block lifecycle
            pass

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

        If HP reaches zero the building goes offline rather
        than being destroyed.  Publishes ``building_destroyed`` when HP
        hits 0.
        """
        hp = self.attributes.get("hp", default=0)
        hp = max(0, hp - amount)
        self.attributes.add("hp", hp)

        if hp <= 0:
            # set offline instead of destroying
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
    #  Structured state
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
            # Route the pickup through the Equipment_System inflow choke point
            # so the carry-weight cap applies and any un-carryable remainder
            # spills back to a ground drop (Req 16.7). Fall back to a direct
            # add when the system is unavailable so pickup never hard-breaks.
            from world.utils import get_system
            equipment_system = get_system(getter, "equipment_system")
            if equipment_system is not None and hasattr(
                equipment_system, "add_resource_capped"
            ):
                added = equipment_system.add_resource_capped(getter, rtype, amt)
            else:
                getter.add_resource(rtype, amt)
                added = amt
            # Reflect the amount actually taken — the cap may reduce it.
            if added > 0:
                total = (
                    getter.get_resource(rtype)
                    if hasattr(getter, "get_resource")
                    else added
                )
                getter.msg(f"Picked up {added} {rtype} (total: {total}).")
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


def _apply_item_def(item, item_def):
    """Copy an ``ItemDef``'s metadata onto a freshly-created ``GameItem``.

    Shared by :func:`create_game_item` (inventory) and :func:`spawn_gear_drop`
    (ground drop) so the metadata copy — and the full-magazine rule for a fresh
    ranged weapon — lives in one place.
    """
    item.db.item_key = item_def.key
    item.db.slot = item_def.slot
    item.db.category = item_def.category
    item.db.stat_modifiers = dict(item_def.stat_modifiers)
    item.db.weapon_type = item_def.weapon_type
    item.db.ammo_type = item_def.ammo_type
    item.db.ammo_per_shot = item_def.ammo_per_shot
    item.db.magazine_size = item_def.magazine_size
    item.db.ammo_cost = dict(item_def.ammo_cost) if item_def.ammo_cost else None
    item.db.effect = dict(item_def.effect) if item_def.effect else None
    item.db.max_stack = item_def.max_stack
    item.db.weight = item_def.weight
    item.db.classification = item_def.classification
    item.db.required_rank = item_def.required_rank
    # A freshly produced ranged weapon arrives with a full magazine so it is
    # usable before the first reload (Req 5.2, 11.7).
    if item_def.weapon_type == "ranged" and item_def.magazine_size is not None:
        item.db.loaded = item_def.magazine_size


def create_game_item(owner, item_def):
    """Create a live ``GameItem`` for *owner* from an ``ItemDef`` (Gear).

    Used by manual ``craft`` (the crafter holds what they made) and the admin
    ``@item give`` command. Spawns a real Evennia ``GameItem`` object in the
    owner's inventory so produced Gear is equippable/usable end-to-end. Supplies
    are NOT created here — they are counted in the Supply_Bag.

    (Passive/agent production spawns Gear as a GROUND DROP on the building's tile
    instead — see :func:`spawn_gear_drop` — so a player collects it with ``get``.)

    Args:
        owner: The player/entity that gets the item (its new location).
        item_def: The ``ItemDef`` to instantiate.

    Returns:
        The created ``GameItem``.
    """
    import evennia

    item = evennia.create_object(
        "typeclasses.objects.GameItem",
        key=item_def.name,
        location=owner,
    )
    _apply_item_def(item, item_def)
    return item


def spawn_gear_drop(location, item_def, x=None, y=None):
    """Create a unique equippable Gear ``GameItem`` as a GROUND DROP at a tile.

    The passive/agent production drop path (an Armory/Lab/Medbay with an Engineer
    produces gear onto its own tile, not into thin air on the owner). Mirrors
    :func:`spawn_supply_drop`, but for a unique Gear object rather than a counted
    Supply stack — so each produced weapon/armor is its own pickup.

    Sets ``coord_x``/``coord_y`` and manually registers in the room's coordinate
    index: ``at_object_receive`` ran during ``create_object`` when the coords were
    still ``None``, so it skipped indexing — without this the drop would be
    invisible to ``get``/``scan``/``look`` (all coordinate-index queries).

    Args:
        location: Room/tile (PlanetRoom) to place the drop in.
        item_def: The ``ItemDef`` to instantiate.
        x, y: Tile coordinates for PlanetRoom placement + indexing.

    Returns:
        The created ``GameItem``, or ``None`` when *location* is falsy.
    """
    if location is None:
        return None
    import evennia

    item = evennia.create_object(
        "typeclasses.objects.GameItem",
        key=item_def.name,
        location=location,
    )
    _apply_item_def(item, item_def)
    if x is not None and y is not None:
        item.db.coord_x = int(x)
        item.db.coord_y = int(y)
        # at_object_receive saw coord_x=None during create_object, so register
        # in the coordinate index now (same pattern as spawn_supply_drop).
        if hasattr(location, "coord_index"):
            location.coord_index.add(item, int(x), int(y))
    return item


def spawn_supply_drop(location, item_key, count, x=None, y=None):
    """Create or merge a counted Supply drop (``GameItem``) at *location*.

    Supplies (``ammo``/``consumable``/``throwable``) normally live as a count
    in a holder's Supply_Bag rather than as map objects. When an over-stack or
    over-weight pickup cannot fully fit, ``EquipmentSystem.add_supply_drop``
    spills the leftover here so the units are never destroyed (D9). The spill
    is a ``GameItem`` carrying the supply ``item_key`` and a ``count`` — NOT a
    :class:`ResourceDrop`, whose ``at_get`` would mis-file the units into the
    holder's resource pool (``db.resources``); Supplies and resources are
    distinct pools.

    Mirrors :func:`spawn_resource_drop`: a supply drop of the same ``item_key``
    at the same coordinates merges into one object (its ``count`` grows) instead
    of spawning duplicates. Supply drops are identified by carrying a ``count``
    attribute, distinguishing them from equippable Gear ``GameItem`` objects.

    Args:
        location: Room/tile to place the drop in (PlanetRoom or legacy room).
        item_key: The Supply item key (references an ItemDef in DataRegistry).
        count: Number of units in the spilled stack.
        x: Optional x coordinate for PlanetRoom-based placement.
        y: Optional y coordinate for PlanetRoom-based placement.

    Returns:
        The ``GameItem`` supply-drop object, or ``None`` when *count* <= 0.
    """
    if count <= 0:
        return None

    # Merge with an existing supply drop of the same item_key at the location.
    if x is not None and y is not None and hasattr(location, "get_objects_at"):
        for obj in location.get_objects_at(x, y, type_tag="item"):
            if (
                getattr(obj.db, "item_key", None) == item_key
                and getattr(obj.db, "count", None) is not None
            ):
                obj.db.count = (obj.db.count or 0) + count
                return obj
    else:
        for obj in GameItem.get_in_room(location):
            if (
                getattr(obj.db, "item_key", None) == item_key
                and getattr(obj.db, "count", None) is not None
            ):
                obj.db.count = (obj.db.count or 0) + count
                return obj

    # Create a new supply drop.
    import evennia

    drop = evennia.create_object(
        "typeclasses.objects.GameItem",
        key=item_key,
        location=location,
    )
    drop.db.item_key = item_key
    drop.db.count = count
    if x is not None and y is not None:
        drop.db.coord_x = x
        drop.db.coord_y = y
        # at_object_receive saw coord_x=None during create_object,
        # so manually register in the coordinate index now.
        if hasattr(location, "coord_index"):
            location.coord_index.add(drop, x, y)
    return drop
