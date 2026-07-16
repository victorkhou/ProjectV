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

from world.constants import RESOURCE_TYPES
from .combat_entity import CombatEntity
from .objects import ObjectParent

logger = logging.getLogger("mygame.characters")


# ------------------------------------------------------------------ #
#  Module-level helpers (used by at_pre_unpuppet)
# ------------------------------------------------------------------ #

# Lazy import cache — populated on first use.
_DefaultCharacter = None


def _get_building_type(building) -> str | None:
    """Return the building_type attribute, or None if missing."""
    if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
        return building.attributes.get("building_type")
    if hasattr(building, "db"):
        return getattr(building.db, "building_type", None)
    return None


def _clear_extractor_inventory(building) -> None:
    """Reset ``resource_inventory`` to ``{}`` on an Extractor building.

    Mirrors the accessor pattern of ``ResourceSystem._set_extractor_inventory``.
    No-op if the building has no ``resource_inventory`` attribute.
    """
    if hasattr(building, "attributes") and hasattr(building.attributes, "add"):
        building.attributes.add("resource_inventory", {})
    elif hasattr(building, "db"):
        building.db.resource_inventory = {}


def _is_preserved(obj, building) -> bool:
    """Return True if *obj* must NOT be deleted during tile cleanup."""
    if obj is building:
        return True
    # Player characters (even disconnected ones) must survive.
    global _DefaultCharacter
    if _DefaultCharacter is None:
        from evennia.objects.objects import DefaultCharacter
        _DefaultCharacter = DefaultCharacter
    if isinstance(obj, _DefaultCharacter):
        return True
    # NPCs and other buildings are tagged in the object_type category.
    if hasattr(obj, "tags"):
        if obj.tags.get("npc", category="object_type"):
            return True
        if obj.tags.get("building", category="object_type"):
            return True
    return False


def _delete_objects_at_building(building) -> None:
    """Delete all non-preserved objects at a building's tile.

    Preserved: the building itself, player characters, NPCs, and
    other buildings.  Everything else (resource drops, items) is deleted.

    Silently skips buildings with no valid coordinates or location.
    """
    bx = getattr(getattr(building, "db", None), "coord_x", None)
    by = getattr(getattr(building, "db", None), "coord_y", None)
    room = getattr(building, "location", None)

    if bx is None or by is None or room is None:
        return

    if hasattr(room, "get_objects_at"):
        objs = list(room.get_objects_at(int(bx), int(by)))
    else:
        # Fallback: match coordinates from room.contents.
        objs = [
            o for o in list(getattr(room, "contents", []))
            if getattr(getattr(o, "db", None), "coord_x", None) == bx
            and getattr(getattr(o, "db", None), "coord_y", None) == by
        ]

    for obj in objs:
        if not _is_preserved(obj, building) and hasattr(obj, "delete"):
            obj.delete()


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

# RESOURCE_TYPES is imported from world.constants (single source of truth) at
# the top of this module and re-exported here for existing importers.

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
    # Movement — earliest tick the player may next move while in combat
    # (see CmdMove's in-combat movement lag). 0 = no pending lag.
    "next_move_tick": 0,
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
    # Player lifecycle state machine (world.player_lifecycle). None = never
    # routed: a brand-new character has no lifecycle state yet, so the login
    # router sends it through SPAWNING (pick class + location). The single
    # WRITER of this field is world.player_lifecycle.transition — never assign
    # db.player_state directly. Existing characters are back-filled on login by
    # ensure_attributes; the router (world.player_lifecycle.route_on_login)
    # promotes a None/legacy character into a concrete state.
    "player_state": None,
    # Selected player class label (state 3.2). None = not yet chosen; the
    # spawning gate requires it before advancing to the lobby. Selection +
    # stored label only — no mechanical effect yet.
    "player_class": None,
    # Place of death (state 3.1 respawn option). None until the player has died
    # at least once; recorded by the death path so "respawn at place of death"
    # has a target.
    "death_x": None,
    "death_y": None,
    "death_planet": None,
    # Linkdead grace deadline (monotonic wall-clock seconds). 0 = not linkdead.
    # Set when an unclean disconnect enters LINKDEAD; the character stays a full
    # combat target until this passes, then is removed to the lobby.
    "linkdead_until": 0.0,
}


class CombatCharacter(CombatEntity, DefaultCharacter):
    """Player character with combat stats, resources, rank, and inventory.

    Extends :class:`CombatEntity` (shared mixin for hp, equipment,
    incapacitation, respawn) and Evennia's ``DefaultCharacter``.

    Uses simple Evennia Attributes (``self.db.*``) for all persistent
    state so the class works without the Traits contrib in test
    environments.  The Traits system can be wired in later on a real
    Evennia server.

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
    #  Structured status
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

    def at_post_puppet(self, **kwargs):
        """Called after an account puppets (logs into) this character.

        This is the REAL post-login hook for a Character — Evennia has no
        ``Character.at_post_login`` (that hook lives only on the Account and is
        NOT forwarded here), so puppeting is where "You become X" + the auto-look
        happen and where our login logic must run: attribute migration, overworld
        positioning, lifecycle routing, and the login event.

        When the lobby flow routes this login to SPAWNING/LOBBY, the player is
        staging (not in the world), so we suppress the parent's "You become X" +
        map look and show the wizard prompt instead. Otherwise we defer to the
        parent (which emits the become-message and looks at the current tile).
        """
        # Under EvenniaTest, setUp() performs a synthetic ``login()`` that
        # puppets a bare character with no game systems wired. Our custom login
        # side-effects (attribute-writes, positioning, routing, the login event)
        # would run against that half-built fixture and corrupt the harness's
        # per-test DB rollback. Defer to the stock puppet there — real logins
        # (and the real-boot smoke driver) run the full path below.
        try:
            from django.conf import settings
            if getattr(settings, "TEST_ENVIRONMENT", False):
                super().at_post_puppet(**kwargs)
                return
        except Exception:  # noqa: BLE001 - settings unreadable -> proceed normally
            pass

        # Auto-migrate: ensure all PLAYER_DEFAULTS attributes exist, and ensure a
        # valid overworld position, BEFORE routing (routing may stow the char).
        self.ensure_attributes()
        self._ensure_overworld_position()

        # Player lifecycle routing (states 3-6). Returns the resume state when
        # the lobby flow is enabled (else None). SPAWNING/LOBBY means the player
        # is staging — we show the wizard instead of dropping them into the map.
        staging_state = self._route_lifecycle_on_login()

        if staging_state is None:
            # Flow disabled, or resumed straight into PLAYING: normal puppet —
            # the parent emits "You become X" and looks at the current tile.
            super().at_post_puppet(**kwargs)
        else:
            # Staging (SPAWNING/LOBBY): no world look; the wizard prompt shown by
            # _route_lifecycle_on_login is the player's cue. Still announce entry
            # to the account so the connection feels acknowledged.
            self.msg(f"\nYou take control of |c{self.key}|n.")

        # (Channel auto-subscribe is handled on the Account login hook — see
        # typeclasses.accounts.Account.at_post_login — not here: it is an
        # account-level concern, and doing account/channel writes from the
        # character puppet hook corrupted EvenniaTest's per-test rollback.)

        # First-time nudge toward the tutorial. Shown once (flagged on the
        # character) so veterans aren't spammed on every login; nothing in
        # the game directed new players to help before this.
        try:
            if not self.db.seen_welcome:
                self.msg(
                    "\n|wNew here?|n Type |whelp tutorial|n to get started, "
                    "or |whelp commands|n for the full command list.\n"
                )
                self.db.seen_welcome = True
        except Exception:
            pass

        try:
            from world.event_bus import event_bus, PLAYER_LOGIN
            event_bus.publish(PLAYER_LOGIN, player=self)
        except Exception:
            pass

    def _route_lifecycle_on_login(self):
        """Route this login through the player lifecycle state machine.

        Returns the STAGING state (``SPAWNING``/``LOBBY``) when the player is not
        yet in the world — so the caller suppresses the normal map look and lets
        the wizard prompt stand — or ``None`` when the flow is disabled or the
        player resumed straight into PLAYING (normal puppet). Guarded so a
        routing hiccup never blocks login (returns ``None`` on error).

        SPAWNING → stow OOC + show the class/spawn wizard; LOBBY → show the
        deploy menu; PLAYING → nothing (resume in the world, e.g. a reconnect or
        crash-resume).
        """
        try:
            from world.lobby_flow import lobby_flow_enabled
            if not lobby_flow_enabled():
                return None
            from world import player_lifecycle as pl
            from world.constants import (
                PLAYER_STATE_SPAWNING, PLAYER_STATE_LOBBY,
            )
            state = pl.route_on_login(self)
            if state == PLAYER_STATE_SPAWNING:
                # SPAWNING is OOC — pull the character out of the world so it
                # can't be attacked while the player picks class + spawn.
                # (_ensure_overworld_position, which ran just before this, may
                # have placed it on the overworld; undo that for spawning.)
                self.stow_from_world()
                from commands.lifecycle_commands import announce_spawning
                announce_spawning(self)
                return PLAYER_STATE_SPAWNING
            if state == PLAYER_STATE_LOBBY:
                from commands.lifecycle_commands import announce_lobby
                announce_lobby(self)
                return PLAYER_STATE_LOBBY
            return None
        except Exception:  # noqa: BLE001 - routing must never block login
            logger.debug("Lifecycle login routing failed", exc_info=True)
            return None

    def _ensure_overworld_position(self):
        """Ensure the character is on the overworld with valid coordinates.

        Three cases:
        1. Character in Limbo (room id 2) → move to shared planet room
        2. Character on a PlanetRoom but missing coord attrs → sync planet
        3. Character already has valid coords → nothing to do

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

    def at_coord_change(self, old_x, old_y, new_x, new_y):
        """React to this character's overworld position changing.

        Fired by ``PlanetRoom.move_entity`` on every coordinate change (a
        step, a teleport). Any ranged lock-on is broken the instant the shooter
        moves: a lock is a held aim, so you must line up, hold still to lock,
        and fire — moving (in any direction) drops it. Done here rather than in
        the per-tick upkeep so there is no window to move-then-shoot at the
        higher locked accuracy before the next tick.
        """
        try:
            from world.utils import get_system
            targeting = get_system(self, "targeting_system")
            if targeting is not None and targeting.get_target(self) is not None:
                targeting.clear_lock(self, reason="moved")
        except Exception:
            # A lock-clear must never break movement.
            pass

    def at_pre_unpuppet(self, **kwargs):
        """Called just before the Account un-puppets this character.

        Evennia calls this hook automatically on disconnect. Destroys
        all contents of unprotected buildings owned by this character
        (clears extractor inventories, deletes all objects on building
        tiles) and publishes the ``player_logout`` event.

        Protected building types (defined in
        ``world.constants.PROTECTED_BUILDING_TYPES``) are skipped
        entirely — their contents survive disconnect.
        """
        from world.constants import PROTECTED_BUILDING_TYPES

        try:
            buildings = self.get_buildings()
            logger.debug(
                "Disconnect cleanup for %s: found %d buildings",
                getattr(self, "key", "?"), len(buildings),
            )
            for b in buildings:
                try:
                    btype = _get_building_type(b)
                    if btype in PROTECTED_BUILDING_TYPES:
                        continue

                    # 1. Clear harvestable-building (Extractor) inventory
                    from world.constants import HARVESTABLE
                    from world.utils import building_has_capability
                    if building_has_capability(b, HARVESTABLE):
                        _clear_extractor_inventory(b)
                        logger.debug("Cleared inventory on %s", getattr(b, "key", "?"))

                    # 2. Delete all objects at building tile (except building)
                    _delete_objects_at_building(b)
                except Exception:
                    logger.debug(
                        "Cleanup error for building %s",
                        getattr(b, "key", "?"),
                        exc_info=True,
                    )
        except Exception:
            logger.debug(
                "Failed cleanup on disconnect for %s",
                getattr(self, "key", "?"),
                exc_info=True,
            )

        # Always publish logout event
        try:
            from world.event_bus import event_bus, PLAYER_LOGOUT
            event_bus.publish(PLAYER_LOGOUT, player=self)
        except Exception:
            pass

    def at_post_unpuppet(self, account=None, session=None, **kwargs):
        """Route lifecycle state on disconnect, then run the default stow-away.

        Distinguishes a CLEAN quit from a dropped connection (lobby flow only):

        * a player who was PLAYING and dropped WITHOUT quitting → LINKDEAD with a
          grace timer (they stay a live combat target until it expires — the
          anti-combat-log rule); the character is NOT stowed away, so it lingers
          in the world during grace.
        * a clean quit (``reason`` starts with "quit") from PLAYING → LOBBY
          (next login lands in the lobby), then the default stow-away removes the
          character from the grid.
        * SPAWNING/LOBBY states are left as-is (a mid-selection disconnect
          resumes there on next login).

        A CLEAN quit is detected via the transient ``ndb._clean_quit`` marker set
        by :class:`~commands.lifecycle_commands.CmdQuit` just before it
        disconnects. Evennia's ``unpuppet_object`` does NOT forward the disconnect
        ``reason`` to this hook, so a marker (not the reason) is the reliable
        signal: marker present → clean quit → LOBBY; absent → dropped connection
        → LINKDEAD. A no-op beyond the default when the flow is disabled, so
        current behavior is unchanged.
        """
        linkdead = False
        try:
            from world.lobby_flow import lobby_flow_enabled
            if lobby_flow_enabled():
                from world import player_lifecycle as pl
                from world.constants import PLAYER_STATE_PLAYING
                is_clean_quit = bool(getattr(self.ndb, "_clean_quit", False))
                # Consume the marker immediately so it can't linger on the cached
                # object and mis-classify a LATER unclean drop as a clean quit
                # (which would defeat the anti-combat-log rule for the rest of
                # the server run). Belt-and-suspenders with the clear on deploy.
                try:
                    self.ndb._clean_quit = False
                except Exception:  # noqa: BLE001
                    pass
                if pl.get_state(self) == PLAYER_STATE_PLAYING:
                    if is_clean_quit:
                        pl.to_lobby(self, reason="quit")
                    else:
                        import time as _t
                        grace = self._linkdead_grace_seconds()
                        pl.begin_linkdead(self, _t.monotonic(), grace)
                        linkdead = True
        except Exception:  # noqa: BLE001 - disconnect routing must never raise
            logger.debug("Lifecycle disconnect routing failed", exc_info=True)

        if linkdead:
            # Do NOT run the default stow-away: a linkdead character must linger
            # in the world (on its tile, in the coordinate index) so it stays a
            # combat target during the grace window. The tick loop removes it
            # when the grace expires (expire_linkdead + world removal).
            return
        super().at_post_unpuppet(account=account, session=session, **kwargs)

    def stow_from_world(self):
        """Remove this character from the map grid (de-index + stow away).

        Used to pull a player OUT of the world while they are OOC in the
        SPAWNING state — after death, or on a login that resumes spawning — so
        they can't be attacked (by anything: turrets, guards, bombs, melee)
        while choosing their class + spawn point. ``deploy`` relocates them back
        onto a tile. Mirrors the linkdead-expiry stow-away: de-index from the
        room's coordinate index FIRST (setting ``location=None`` does not fire
        ``at_object_leave``, which is the only path that de-indexes), then null
        the location. Best-effort — never raises.
        """
        try:
            room = self.location
            if room is None:
                return
            cx = getattr(self.db, "coord_x", None)
            cy = getattr(self.db, "coord_y", None)
            idx = getattr(getattr(room, "ndb", None), "_coord_index", None)
            if idx is not None and cx is not None and cy is not None:
                idx.remove(self, int(cx), int(cy))
            self.db.prelogout_location = room
            self.location = None
        except Exception:  # noqa: BLE001 - stow must never raise into a hook
            logger.debug("stow_from_world failed", exc_info=True)

    @staticmethod
    def _linkdead_grace_seconds() -> float:
        """Linkdead grace window (seconds) from balance config, default 1800 (30 min).

        Falls back to a safe default when the registry/balance is unavailable
        (early boot / tests). Set far above the ~60s combat timer so pulling the
        plug can't dodge an active fight — the dropped body stays a live target
        well past when any combat timer would expire.
        """
        try:
            from world.data_registry import DataRegistry
            reg = DataRegistry.get_instance()
            if reg is not None:
                return float(getattr(reg.balance, "linkdead_grace_seconds", 1800.0))
        except Exception:  # noqa: BLE001
            pass
        return 1800.0
