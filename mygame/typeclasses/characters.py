"""
Characters

Characters are (by default) Objects setup to be puppeted by Accounts.
They are what you "see" in game. The Character class in this module
is setup to be the "default" character type created by the default
creation commands.

"""

from __future__ import annotations

import copy
import logging

from evennia.objects.objects import DefaultCharacter

from .combat_entity import CombatEntity
from .objects import ObjectParent

logger = logging.getLogger("mygame.characters")


class Character(ObjectParent, DefaultCharacter):
    """
    The Character just re-implements some of the Object's methods and hooks
    to represent a Character entity in-game.

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Object child classes like this.

    """

    pass


# ------------------------------------------------------------------ #
#  Resource type constants
# ------------------------------------------------------------------ #

RESOURCE_TYPES = (
    "Wood", "Stone", "Iron",
    "Energy", "Circuits", "Nexium",
)

DEFAULT_HEALTH = 100

# ------------------------------------------------------------------ #
#  Player attribute schema
# ------------------------------------------------------------------ #
#
# Single source of truth for all player attributes and their defaults.
# Used by at_object_creation (new players), ensure_attributes (migration),
# and @migrate (admin command).
#
# To add a new player attribute:
#   1. Add it here with its default value
#   2. That's it — at_object_creation and ensure_attributes both read this

STARTING_RESOURCES = {
    "Wood": 40, "Stone": 25, "Iron": 10,
    "Energy": 0, "Circuits": 0, "Nexium": 0,
}

PLAYER_DEFAULTS: dict[str, object] = {
    # CombatEntity (shared with NPCs)
    "hp": DEFAULT_HEALTH,
    "hp_max": DEFAULT_HEALTH,
    "equipment_slots": {},
    "incapacitated": False,
    "respawn_timer": 0,
    "respawn_location": None,
    # Progression
    "combat_xp": 0,
    "rank_level": 1,
    "level": 1,
    # Resources
    "resources": dict(STARTING_RESOURCES),
    # Powerups / tech
    "active_powerups": {},
    "powerup_cooldowns": {},
    "researched_techs": set(),
    # Combat
    "combat_lockout_tick": 0,
    "combat_timer_expires": 0,
    # Position
    "coord_x": 0,
    "coord_y": 0,
    "coord_planet": "",
    # Fog of war
    "discovery_memory": {},
    # Building state
    "inside_building": False,
    # Agent system
    "next_agent_id": 1,
    # Active-presence
    "activity_state": "idle",
    "activity_target": None,
    "activity_progress": 0,
}


class CombatCharacter(CombatEntity, DefaultCharacter):
    """Player character with combat stats, resources, rank, and inventory.

    Extends :class:`CombatEntity` (shared mixin for hp, equipment,
    incapacitation, respawn) and Evennia's ``DefaultCharacter``.

    Uses simple Evennia Attributes (``self.db.*``) for all persistent
    state so the class works without the Traits contrib in test
    environments.  The Traits system can be wired in later on a real
    Evennia server.

    Requirements: 2.4, 3.2, 3.3, 7.6, 7.8, 10.1, 10.4, 14.1, 16.5, 16.6, 27.1
    """

    # ------------------------------------------------------------------ #
    #  Creation hook
    # ------------------------------------------------------------------ #

    def at_object_creation(self):
        """Initialize all attributes from PLAYER_DEFAULTS."""
        super().at_object_creation()
        for key, default in PLAYER_DEFAULTS.items():
            setattr(self.db, key, copy.deepcopy(default))

    def ensure_attributes(self):
        """Ensure all PLAYER_DEFAULTS attributes exist with valid values.

        Called on login to auto-migrate existing players when new
        attributes are added. Only sets attributes that are missing
        or None — never overwrites existing valid data.

        Special handling: if ``level`` is missing but ``rank_level``
        exists, derives level from the old rank number (1-12) using
        ``(rank - 1) * 5 + 1``.
        """
        for key, default in PLAYER_DEFAULTS.items():
            if self.attributes.get(key) is None:
                if key == "level":
                    # Migrate from old rank_level (1-12) to new level (1-60)
                    from world.constants import NUM_RANKS, LEVELS_PER_RANK
                    old_rank = self.attributes.get("rank_level")
                    if old_rank is not None and isinstance(old_rank, int) and 1 <= old_rank <= NUM_RANKS:
                        self.attributes.add("level", (old_rank - 1) * LEVELS_PER_RANK + 1)
                        continue
                self.attributes.add(key, copy.deepcopy(default))

    # ------------------------------------------------------------------ #
    #  Equipment handler (lazy property)
    # ------------------------------------------------------------------ #

    @property
    def equipment(self):
        """Return the EquipmentHandler for this character."""
        if not hasattr(self, "_equipment_handler"):
            from world.systems.equipment_handler import EquipmentHandler
            self._equipment_handler = EquipmentHandler(self)
        return self._equipment_handler

    # ------------------------------------------------------------------ #
    #  Resource helpers
    # ------------------------------------------------------------------ #

    def _ensure_resources(self) -> dict:
        """Return the resources dict, creating it if missing."""
        res = self.db.resources
        if res is None:
            res = {r: 0 for r in RESOURCE_TYPES}
            self.db.resources = res
        return res

    def get_resource(self, resource_type: str) -> int:
        """Return the current amount of *resource_type*."""
        res = self._ensure_resources()
        return res.get(resource_type.title(), 0)

    def add_resource(self, resource_type: str, amount: int) -> None:
        """Add *amount* units of *resource_type*."""
        resource_type = resource_type.title()
        res = self._ensure_resources()
        res[resource_type] = res.get(resource_type, 0) + amount
        self.db.resources = res

    def has_resources(self, costs: dict[str, int]) -> bool:
        """Return ``True`` iff the character has all resources in *costs*."""
        res = self._ensure_resources()
        return all(res.get(r.title(), 0) >= amt for r, amt in costs.items())

    def deduct_resources(self, costs: dict[str, int]) -> bool:
        """Deduct *costs* from resources if sufficient. Return success."""
        if not self.has_resources(costs):
            return False
        res = self._ensure_resources()
        for r, amt in costs.items():
            key = r.title()
            res[key] = res.get(key, 0) - amt
        self.db.resources = res
        return True

    # ------------------------------------------------------------------ #
    #  Building helpers
    # ------------------------------------------------------------------ #

    def get_buildings(self) -> list:
        """Return all Building objects owned by this character.

        Queries the database for objects with a ``building_type``
        attribute whose ``owner`` attribute matches this character.
        Falls back to an empty list outside a full Evennia environment.
        """
        try:
            from evennia.objects.models import ObjectDB

            return list(
                ObjectDB.objects.filter(
                    db_attributes__db_key="owner",
                    db_attributes__db_value=self,
                ).filter(
                    db_attributes__db_key="building_type",
                )
            )
        except Exception:
            # Outside full Evennia context (tests, early startup)
            return []

    # ------------------------------------------------------------------ #
    #  Structured status (Requirement 27.1)
    # ------------------------------------------------------------------ #

    def get_structured_status(self) -> dict:
        """Return a presentation-agnostic dict of this character's state."""
        res = self._ensure_resources()
        return {
            "name": self.key if hasattr(self, "key") else "",
            "hp": self.db.hp,
            "hp_max": self.db.hp_max,
            "combat_xp": self.db.combat_xp,
            "rank_level": self.db.rank_level,
            "resources": dict(res),
            "active_powerups": dict(self.db.active_powerups or {}),
            "researched_techs": list(self.db.researched_techs or set()),
            "combat_lockout_tick": self.db.combat_lockout_tick or 0,
        }

    # ------------------------------------------------------------------ #
    #  Login / logout hooks
    # ------------------------------------------------------------------ #

    def at_post_login(self, session, **kwargs):
        """Called after the player logs in.

        Auto-migrates attributes, ensures overworld position, and
        publishes the login event.
        """
        super().at_post_login(session, **kwargs)

        # Auto-migrate: ensure all PLAYER_DEFAULTS attributes exist
        self.ensure_attributes()

        # Ensure character is on the overworld with valid coordinates
        self._ensure_overworld_position()

        # Auto-subscribe the account to game channels
        try:
            from world.utils import get_system
            chat_system = get_system(self, "chat_system")
            if chat_system and self.account:
                chat_system.auto_subscribe(self.account)
        except Exception:
            pass

        # Map is now shown via CmdLook which Evennia triggers
        # automatically after login (super().at_post_login calls look).

        try:
            from world.event_bus import event_bus, PLAYER_LOGIN
            event_bus.publish(PLAYER_LOGIN, player=self)
        except Exception:
            pass

    def _ensure_overworld_position(self):
        """Ensure the character is on the overworld with valid coordinates.

        Three cases:
        1. Character in Limbo (room id 2) → move to shared planet room
        2. Character on a PlanetRoom but missing coord attrs → sync planet
        3. Character already has valid coords → nothing to do

        Requirements: 7.8, 8.2
        """
        try:
            loc = self.location

            # Case 2: already on a room but coords not set
            from world.constants import LIMBO_ROOM_ID
            if loc is not None and loc.id != LIMBO_ROOM_ID:
                if not self.db.coord_planet:
                    self._sync_coords_from_room(loc)
                # If sync succeeded or coords were already set, ensure
                # we're in the correct PlanetRoom for our planet
                if self.db.coord_planet:
                    try:
                        from server.conf.game_init import game_systems
                        planet_rooms = game_systems.get("planet_rooms", {})
                        expected_room = planet_rooms.get(self.db.coord_planet)
                        if expected_room and self.location is not expected_room:
                            self.move_to(expected_room, quiet=True)
                    except (ImportError, AttributeError):
                        pass
                    return
                # Otherwise fall through to spawn logic (room has no coords)

            # Case 1: in Limbo — move to spawn on the shared planet room
            # Try shared systems first (initialised in game_init)
            registry = None
            planet_rooms = None
            try:
                from server.conf.game_init import game_systems

                registry = game_systems.get("planet_registry")
                planet_rooms = game_systems.get("planet_rooms", {})
            except (ImportError, AttributeError):
                pass

            # Fallback: create local instances if game_init hasn't run yet
            if registry is None:
                from world.coordinate.planet_registry import PlanetRegistry

                registry = PlanetRegistry()
                registry.load_from_yaml("data/definitions/planets.yaml")

            # Find the default planet
            default_space = None
            for planet_key in registry.list_planets():
                space = registry.get_space(planet_key)
                if space.default_planet:
                    default_space = space
                    break

            if default_space is None:
                logger.warning("No default planet configured — cannot spawn.")
                return

            spawn_x = default_space.spawn_x
            spawn_y = default_space.spawn_y
            planet_key = default_space.planet_key

            # Use the shared planet room
            target = None
            if planet_rooms:
                target = planet_rooms.get(planet_key)

            if target:
                self.move_to(target, quiet=True)
                self.db.coord_x = spawn_x
                self.db.coord_y = spawn_y
                self.db.coord_planet = planet_key
                self.msg(
                    f"You arrive at the overworld ({spawn_x}, {spawn_y})."
                )
        except Exception:
            logger.debug(
                "Could not move to overworld spawn — "
                "planet registry not available yet.",
                exc_info=True,
            )

    def _sync_coords_from_room(self, room):
        """Sync coordinate attributes from the current room (PlanetRoom)."""
        x = getattr(room, "x", None)
        y = getattr(room, "y", None)
        planet = getattr(room, "planet_name", None)

        if x is not None and y is not None and planet and planet != "unknown":
            self.db.coord_x = x
            self.db.coord_y = y
            self.db.coord_planet = planet
            logger.info(
                "Synced coordinates for %s from room: (%s, %s, %s)",
                self.key, x, y, planet,
            )

    def at_pre_disconnect(self, **kwargs):
        """Called just before the player disconnects.

        Clears resource inventories from all owned buildings except
        Vaults, then publishes ``player_logout`` event.
        """
        # Clear resource drops from non-Vault buildings
        try:
            buildings = self.get_buildings()
            for b in buildings:
                btype = None
                if hasattr(b, "attributes") and hasattr(b.attributes, "get"):
                    btype = b.attributes.get("building_type")
                elif hasattr(b, "db"):
                    btype = getattr(b.db, "building_type", None)
                if btype == "VT":
                    continue  # Vaults preserve resources
                # Get building coordinates and its PlanetRoom
                bx = getattr(getattr(b, "db", None), "coord_x", None)
                by = getattr(getattr(b, "db", None), "coord_y", None)
                room = getattr(b, "location", None)
                if bx is None or by is None or room is None:
                    continue
                # Query PlanetRoom for ResourceDrops at building coordinates
                if hasattr(room, "get_objects_at"):
                    for obj in list(room.get_objects_at(int(bx), int(by), type_tag="resource_drop")):
                        if hasattr(obj, "delete"):
                            obj.delete()
                else:
                    # Legacy fallback: iterate room contents
                    for obj in list(getattr(room, "contents", [])):
                        if hasattr(obj, "tags") and obj.tags.get("resource_drop", category="object_type"):
                            if hasattr(obj, "delete"):
                                obj.delete()
        except Exception:
            logger.debug(
                "Failed to clear building inventories on disconnect for %s",
                getattr(self, "key", "?"), exc_info=True,
            )

        try:
            from world.event_bus import event_bus, PLAYER_LOGOUT
            event_bus.publish(PLAYER_LOGOUT, player=self)
        except Exception:
            pass
