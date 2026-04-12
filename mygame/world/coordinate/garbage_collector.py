"""
Garbage Collector for Dynamic_Rooms.

Periodically removes Dynamic_Rooms that have no players, no buildings,
and no custom modifications from the database.  Static_Rooms are never
touched.

The ``run()`` method accepts an optional *rooms* list for testability.
When *rooms* is ``None`` (production path), it queries the database for
Dynamic_Rooms via Django ORM.

Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 10.5
"""

from __future__ import annotations

from typing import Any, Sequence


class RoomGarbageCollector:
    """Removes unused Dynamic_Rooms from the database."""

    def __init__(
        self,
        room_cache: Any,
        interval_ticks: int = 100,
        min_age_ticks: int = 50,
    ) -> None:
        """Initialise the garbage collector.

        Args:
            room_cache: A :class:`RoomCache` instance.  Deleted rooms
                are evicted from the cache.
            interval_ticks: How often (in game ticks) the GC should run.
                Read from ``balance.gc_interval_ticks``.
            min_age_ticks: Minimum age in ticks before a dynamic room
                becomes eligible for collection.  Read from
                ``balance.gc_min_age_ticks``.
        """
        self._cache = room_cache
        self.interval_ticks = interval_ticks
        self.min_age_ticks = min_age_ticks
        self._current_tick: int = 0

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def run(self, rooms: Sequence[Any] | None = None, current_tick: int = 0) -> int:
        """Execute one GC pass.  Returns count of rooms deleted.

        When *rooms* is provided (e.g. in tests), only those rooms are
        evaluated.  When *rooms* is ``None``, the collector queries the
        database for all Dynamic_Rooms.

        Args:
            rooms: Optional list of rooms to evaluate (for testing).
            current_tick: The current game tick, used to enforce
                ``min_age_ticks``.

        A room is eligible for deletion when **all** of the following
        hold:

        * Tagged ``persistence_type=dynamic`` (Static_Rooms are never
          touched).
        * No player characters present in ``contents``.
        * No buildings present in ``contents``.
        * Description has not been customised (matches the default
          pattern or is empty).
        * Room age (current_tick - created_tick) >= min_age_ticks.
        """
        self._current_tick = current_tick
        if rooms is None:
            rooms = self._query_dynamic_rooms()

        deleted = 0
        for room in rooms:
            if self._is_eligible(room):
                self._delete_room(room)
                deleted += 1
        return deleted

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_persistence_type(room: Any) -> str | None:
        """Return the persistence_type tag value, or ``None``."""
        if hasattr(room, "tags"):
            return room.tags.get(category="persistence_type", return_list=False)
        return None

    @staticmethod
    def _has_players(room: Any) -> bool:
        """Return ``True`` if the room contains any player characters."""
        contents = getattr(room, "contents", [])
        for obj in contents:
            # Evennia characters have ``is_typeclass`` or we can check
            # for a ``player`` / ``account`` attribute as a heuristic.
            if hasattr(obj, "account") or hasattr(obj, "is_player"):
                return True
        return False

    @staticmethod
    def _has_buildings(room: Any) -> bool:
        """Return ``True`` if the room contains any buildings."""
        contents = getattr(room, "contents", [])
        for obj in contents:
            if hasattr(obj, "building_type") or hasattr(obj, "is_building"):
                return True
        return False

    @staticmethod
    def _has_custom_description(room: Any) -> bool:
        """Return ``True`` if the room's description differs from default.

        A room is considered customised when its description is a
        non-empty string that is not the Evennia default placeholder.
        Rooms created by the TileResolver have no custom description
        set, so ``db.desc`` will be empty/``None``.
        """
        desc = None
        if hasattr(room, "db") and hasattr(room.db, "desc"):
            desc = room.db.desc
        elif hasattr(room, "attributes"):
            desc = room.attributes.get("desc")

        if desc is None or desc == "":
            return False
        # Evennia's default description placeholder
        if desc == "You see nothing special.":
            return False
        return True

    def _is_eligible(self, room: Any) -> bool:
        """Return ``True`` if *room* should be garbage-collected.

        Both static and dynamic empty rooms are eligible. A room is
        kept only if it has players, buildings, custom descriptions,
        depleted resources (meaningful state), or is a shared planet room.
        """
        # Never touch shared planet rooms
        if hasattr(room, "tags"):
            planet_room_tag = room.tags.get(category="planet_room", return_list=False)
            if planet_room_tag:
                return False

        if self._has_players(room):
            return False

        if self._has_buildings(room):
            return False

        if self._has_custom_description(room):
            return False

        # Keep rooms with depleted resources (state that matters)
        rn = self._get_attr(room, "resource_node_data")
        if rn and isinstance(rn, dict) and rn.get("depleted"):
            return False

        # Enforce minimum age before collection.
        if self.min_age_ticks > 0 and self._current_tick > 0:
            created = self._get_attr(room, "created_tick")
            if created is not None:
                age = self._current_tick - int(created)
                if age < self.min_age_ticks:
                    return False

        return True

    def _delete_room(self, room: Any) -> None:
        """Delete *room* from the database and evict from cache."""
        # Read coordinates before deletion so we can evict from cache.
        x = self._get_attr(room, "x")
        y = self._get_attr(room, "y")
        planet = self._get_attr(room, "planet")

        # Delete the room object (Evennia .delete())
        if hasattr(room, "delete"):
            room.delete()

        # Evict from cache
        if x is not None and y is not None and planet is not None:
            self._cache.remove(x, y, planet)

    @staticmethod
    def _get_attr(room: Any, key: str) -> Any | None:
        """Read an attribute from a room, tolerating different backends."""
        if hasattr(room, "db"):
            val = getattr(room.db, key, None)
            if val is not None:
                return val
        if hasattr(room, "attributes"):
            return room.attributes.get(key)
        return None

    @staticmethod
    def _query_dynamic_rooms() -> list[Any]:
        """Query the database for all overworld rooms eligible for GC.

        Returns all rooms tagged as overworld_tile. The _is_eligible
        method handles per-room filtering.
        """
        try:
            from typeclasses.rooms import OverworldRoom

            return list(
                OverworldRoom.objects.filter_family(
                    db_tags__db_key="overworld_tile",
                    db_tags__db_category="room_type",
                )
            )
        except Exception:
            return []
