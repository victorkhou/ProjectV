"""
Characters

Characters are (by default) Objects setup to be puppeted by Accounts.
They are what you "see" in game. The Character class in this module
is setup to be the "default" character type created by the default
creation commands.

"""

from __future__ import annotations

import logging

from evennia.objects.objects import DefaultCharacter

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
    "Straw", "Clay", "Wood", "Stone", "Iron",
    "Energy", "Metals", "Circuits",
)

DEFAULT_HEALTH = 100


class CombatCharacter(DefaultCharacter):
    """Player character with combat stats, resources, rank, and inventory.

    Uses simple Evennia Attributes (``self.db.*``) for all persistent
    state so the class works without the Traits contrib in test
    environments.  The Traits system can be wired in later on a real
    Evennia server.

    Requirements: 2.4, 7.8, 10.1, 10.4, 27.1
    """

    # ------------------------------------------------------------------ #
    #  Creation hook
    # ------------------------------------------------------------------ #

    def at_object_creation(self):
        """Initialize all combat-related attributes."""
        super().at_object_creation()

        # Health
        self.db.hp = DEFAULT_HEALTH
        self.db.hp_max = DEFAULT_HEALTH

        # Combat XP and rank
        self.db.combat_xp = 0
        self.db.rank_level = 1

        # Resources — dict keyed by resource type name
        self.db.resources = {r: 0 for r in RESOURCE_TYPES}

        # Equipment handler (lazy-initialized via property)
        self.db.equipment_slots = {}

        # Powerups / cooldowns / tech
        self.db.active_powerups = {}      # key -> {expires_tick, effect}
        self.db.powerup_cooldowns = {}    # key -> ready_tick
        self.db.researched_techs = set()  # set of tech keys

        # Combat lockout
        self.db.combat_lockout_tick = 0

        # Coordinate tracking (Requirement 1.4, 8.2)
        self.db.coord_x = 0
        self.db.coord_y = 0
        self.db.coord_planet = ""

        # Fog of war discovery memory (Requirement 11.1, 11.7)
        self.db.discovery_memory = {}

        # Building interior tracking
        self.db.inside_building = False

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

        If the character is in Limbo (the default start room), move them
        to the overworld spawn point. Also publishes ``player_login``
        event and transitions buildings online.
        """
        super().at_post_login(session, **kwargs)

        # Ensure character is on the overworld with valid coordinates
        self._ensure_overworld_position()

        # Show the map on login (delayed so the session is fully connected)
        # Note: removed — map is now shown via CmdLook which Evennia
        # triggers automatically after login.

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
            if loc is not None and loc.id != 2:
                if not self.db.coord_planet:
                    self._sync_coords_from_room(loc)
                # If sync succeeded or coords were already set, we're done
                if self.db.coord_planet:
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

            # Try to use the shared planet room
            target = None
            if planet_rooms:
                target = planet_rooms.get(planet_key)

            # Fallback: resolve a tile room if no planet room available
            if target is None:
                resolver = None
                try:
                    from server.conf.game_init import game_systems
                    resolver = game_systems.get("tile_resolver")
                except (ImportError, AttributeError):
                    pass

                if resolver is None:
                    from world.coordinate.planet_registry import PlanetRegistry
                    from world.coordinate.terrain_generator import TerrainGenerator
                    from world.coordinate.room_cache import RoomCache
                    from world.coordinate.tile_resolver import TileResolver

                    if not hasattr(registry, '_spaces'):
                        registry = PlanetRegistry()
                        registry.load_from_yaml("data/definitions/planets.yaml")

                    terrain_generators = {}
                    for pk in registry.list_planets():
                        space_def = registry.get_space(pk)
                        terrain_generators[pk] = TerrainGenerator(space_def)

                    resolver = TileResolver(
                        planet_registry=registry,
                        terrain_generators=terrain_generators,
                        room_cache=RoomCache(),
                    )

                target = resolver.resolve(spawn_x, spawn_y, planet_key)

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
                "planet registry or tile resolver not available yet.",
                exc_info=True,
            )

    def _sync_coords_from_room(self, room):
        """Sync coordinate attributes from the current OverworldRoom.

        Called when a character is already on the overworld but their
        coord_x/coord_y/coord_planet attributes are missing (e.g.
        characters created before the coordinate system was added).
        """
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

        Publishes ``player_logout`` event and transitions buildings offline.
        """
        try:
            from world.event_bus import event_bus, PLAYER_LOGOUT
            event_bus.publish(PLAYER_LOGOUT, player=self)
        except Exception:
            pass
