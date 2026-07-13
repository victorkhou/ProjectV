"""
Outpost Spawner — places NPC outposts/fortresses and respawns cleared ones.

Keeps the world populated with raid targets. At server start it places
``outpost_count`` outposts + ``fortress_count`` fortresses per planet from the
data-driven templates (``registry.base_templates``); when a base is eliminated
(its HQ destroyed — see :class:`BaseEliminationHandler`) it schedules a respawn
after ``outpost_respawn_ticks`` at a fresh valid location.

Framework-free: all Evennia I/O (creating the Sentinel owner, enemy guards, and
buildings; reading terrain) is injected as ports/callables at the composition
root. The system owns only the placement logic, template expansion, and the
respawn schedule.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem
from world.utils import get_obj_attr, set_obj_attr

logger = logging.getLogger("evennia.world.systems.outpost_spawner")

#: How many random placements to try before giving up on one base (a crowded
#: planet degrades gracefully — the base is skipped and logged, not retried
#: forever). Structural, not a balance knob.
_MAX_PLACEMENT_ATTEMPTS = 100
#: Minimum Manhattan distance an NPC base's HQ must keep from any other NPC
#: base's HQ and from any existing building (incl. player HQs), so bases don't
#: overlap or spawn on top of a player.
_MIN_BASE_SEPARATION = 8
#: When a respawn can't be placed (crowded planet), re-arm it after a fraction
#: of the full cooldown rather than retrying every tick — bounds the placement
#: work a permanently-crowded planet can generate per tick.
_RESPAWN_BACKOFF_DIVISOR = 4
#: PlanetRoom attribute key under which pending respawns for that planet are
#: persisted, so a cleared base still respawns after a server restart (Req 7.6).
_PENDING_ATTR = "npc_base_pending_respawns"


class OutpostSpawnerSystem(BaseSystem):
    """Places NPC bases at init and respawns cleared ones on a cooldown.

    Args:
        registry: DataRegistry (balance + base_templates + building defs).
        event_bus: EventBus (unused directly; elimination is handled elsewhere).
        npc_base_factory: :class:`NpcBaseFactory` port — creates the Sentinel
            owner and enemy guards.
        building_factory: :class:`BuildingFactory` port — creates base buildings
            (reused from BuildingSystem's factory).
        terrain_provider: :class:`TerrainProvider` port — terrain lookup for
            passability checks during placement.
        planet_rooms_provider: zero-arg callable returning the
            ``{planet_key: PlanetRoom}`` dict (late-bound; rooms are created
            after the systems at the composition root).
        planet_registry: PlanetRegistry for planet bounds / coordinate validity.
        rng: Optional ``random.Random`` for deterministic placement in tests.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        npc_base_factory: Any = None,
        building_factory: Any = None,
        terrain_provider: Any = None,
        planet_rooms_provider: Callable[[], dict] | None = None,
        planet_registry: Any = None,
        rng: "random.Random | None" = None,
        current_tick_func: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._npc_factory = npc_base_factory
        self._building_factory = building_factory
        self._terrain = terrain_provider
        self._planet_rooms_provider = planet_rooms_provider or (lambda: {})
        self._planet_registry = planet_registry
        self._rng = rng or random.Random()
        self._current_tick_func = current_tick_func or (lambda: 0)
        #: Active bases: sentinel-id -> {"sentinel", "tier", "planet", "x", "y"}.
        self._active_bases: dict[Any, dict] = {}
        #: Pending respawns: list of {"tier", "planet", "respawn_at"} (tick).
        self._pending_respawns: list[dict] = []
        # Schedule a respawn whenever a base is eliminated (decoupled from the
        # elimination handler — it publishes, we react).
        if event_bus is not None:
            from world.event_bus import BASE_ELIMINATED
            event_bus.subscribe(BASE_ELIMINATED, self.on_base_eliminated)

    # ------------------------------------------------------------------ #
    #  Initial placement
    # ------------------------------------------------------------------ #

    def spawn_initial(self, planet: str) -> list:
        """Place ``outpost_count`` outposts + ``fortress_count`` fortresses.

        Called once per planet at server start. Silently places as many as fit;
        a base that can't find a valid spot after the attempt cap is skipped
        (logged), so a crowded planet never blocks startup.

        Returns the list of spawned base records.
        """
        bal = self.registry.balance
        spawned = []
        plan = (
            [("outpost", bal.outpost_count)]
            + [("fortress", bal.fortress_count)]
        )
        for tier, count in plan:
            for _ in range(max(0, count)):
                base = self.spawn_base(planet, tier)
                if base is not None:
                    spawned.append(base)
        logger.info("Spawned %d NPC base(s) on planet %s.", len(spawned), planet)
        return spawned

    # ------------------------------------------------------------------ #
    #  Spawn one base
    # ------------------------------------------------------------------ #

    def spawn_base(
        self, planet: str, tier: str, coords: tuple[int, int] | None = None
    ) -> dict | None:
        """Spawn one NPC base of *tier* on *planet*.

        Creates the Sentinel owner, then the template's buildings (via the
        building factory) at their offsets and its guards (via the NPC factory)
        at the HQ tile. When *coords* is None a valid location is chosen by
        :meth:`_find_placement`. Returns the base record, or ``None`` if the
        template/room is unavailable or no valid placement was found.
        """
        template = self.registry.get_base_template(tier)
        if template is None:
            logger.warning("No NPC-base template for tier %r.", tier)
            return None

        room = self._planet_rooms_provider().get(planet)
        if room is None:
            logger.warning("No PlanetRoom for planet %r — cannot spawn base.", planet)
            return None

        if self._npc_factory is None or self._building_factory is None:
            return None

        if coords is None:
            coords = self._find_placement(planet, room, template)
            if coords is None:
                logger.warning(
                    "No valid placement for %s on %s after %d attempts.",
                    tier, planet, _MAX_PLACEMENT_ATTEMPTS,
                )
                return None
        hx, hy = int(coords[0]), int(coords[1])

        # 1. Sentinel owner.
        name = self._sentinel_name(template)
        sentinel = self._npc_factory.create_sentinel(name, room, planet)
        # Stamp tier/planet/coords so the elimination handler can schedule a
        # respawn AND so _active_bases can be rebuilt from surviving sentinels
        # after a server restart (rebuild_from_world).
        set_obj_attr(sentinel, "base_tier", tier)
        set_obj_attr(sentinel, "base_planet", planet)
        set_obj_attr(sentinel, "base_x", hx)
        set_obj_attr(sentinel, "base_y", hy)

        # 2. Buildings at their template offsets.
        for b in template.buildings:
            bx, by = hx + b.offset[0], hy + b.offset[1]
            bdef = self._resolve_building(b.building_type)
            if bdef is None:
                logger.warning("Unknown building type %r in template %r.",
                               b.building_type, tier)
                continue
            building = self._building_factory.create_building(
                bdef, room, sentinel, x=bx, y=by
            )
            if building is not None:
                # NPC-base buildings are OPEN — they exist to be raided, so
                # ranged weapons and turrets can hit them (buildings default to
                # closed/cover, which is a player-base concept, not an NPC one).
                set_obj_attr(building, "open", True)
                if b.hp is not None:
                    set_obj_attr(building, "hp", b.hp)
                    set_obj_attr(building, "hp_max", b.hp)

        # 3. Guards at the HQ tile. A running 1-based index across all guard
        # groups makes each guard uniquely named (Guard-1, Guard-2, Soldier-3…).
        guard_hp = self._guard_hp(tier)
        guard_index = 0
        for g in template.guards:
            hp = g.hp if g.hp is not None else guard_hp
            for _ in range(max(0, g.count)):
                guard_index += 1
                self._npc_factory.create_enemy_guard(
                    sentinel, room, hx, hy, g.role, hp, index=guard_index
                )

        record = {
            "sentinel": sentinel, "tier": tier, "planet": planet,
            "x": hx, "y": hy,
        }
        self._active_bases[self._base_key(sentinel)] = record
        logger.info("Spawned %s base at (%d, %d) on %s.", tier, hx, hy, planet)
        return record

    # ------------------------------------------------------------------ #
    #  Respawn scheduling
    # ------------------------------------------------------------------ #

    def on_base_eliminated(
        self, event_name: str = "", sentinel: Any = None, tier: str = "outpost",
        planet: Any = None, **kwargs
    ) -> None:
        """React to a ``BASE_ELIMINATED`` event: drop the base and queue respawn.

        The elimination handler publishes tier/planet in the payload (read
        before it deletes the sentinel), so we don't depend on the now-deleted
        object. Schedules a fresh base of the same tier after
        ``outpost_respawn_ticks`` (0 disables respawning).
        """
        if sentinel is not None:
            self._active_bases.pop(self._base_key(sentinel), None)

        respawn_ticks = self.registry.balance.outpost_respawn_ticks
        if respawn_ticks <= 0 or not planet:
            return
        self._pending_respawns.append({
            "tier": tier, "planet": planet,
            "respawn_at": self._current_tick_func() + respawn_ticks,
        })
        # Persist so the pending respawn survives a server restart (Req 7.6) —
        # without this a base cleared before its cooldown would never come back.
        self._save_state()

    def process_respawns(self, tick_number: int) -> int:
        """Spawn any pending respawns whose cooldown has elapsed.

        Wired as the ``"outpost_respawn"`` tick step. Returns the number of
        bases respawned this tick.
        """
        if not self._pending_respawns:
            return 0
        due = [p for p in self._pending_respawns if p["respawn_at"] <= tick_number]
        if not due:
            return 0
        respawned = 0
        backoff = max(
            1, self.registry.balance.outpost_respawn_ticks // _RESPAWN_BACKOFF_DIVISOR
        )
        for pending in due:
            self._pending_respawns.remove(pending)
            if self.spawn_base(pending["planet"], pending["tier"]) is not None:
                respawned += 1
            else:
                # Couldn't place right now (crowded) — back off and retry after a
                # fraction of the cooldown rather than hammering placement every
                # tick (which runs up to _MAX_PLACEMENT_ATTEMPTS DB lookups).
                pending["respawn_at"] = tick_number + backoff
                self._pending_respawns.append(pending)
        # Active-base set and pending list both changed — persist.
        self._save_state()
        return respawned

    # ------------------------------------------------------------------ #
    #  Persistence (Req 7.6) — survive server restarts
    # ------------------------------------------------------------------ #

    def rebuild_from_world(self, sentinels: list | None = None) -> None:
        """Repopulate in-memory state after a server restart.

        Evennia objects (sentinels, buildings) persist across reboots, but this
        system's ``_active_bases`` / ``_pending_respawns`` are in-memory. On
        startup, ``game_init`` calls this with every surviving sentinel so:

        - ``_active_bases`` is rebuilt from the sentinels' stamped
          tier/planet/coords — restoring the base-separation check (otherwise a
          respawn could land on top of a base that survived the reboot); and
        - ``_pending_respawns`` is reloaded from each PlanetRoom's persisted
          attribute — so a base cleared just before a reboot still respawns.

        Idempotent: safe to call once at startup. Never raises.
        """
        try:
            for s in sentinels or ():
                planet = get_obj_attr(s, "base_planet")
                self._active_bases[self._base_key(s)] = {
                    "sentinel": s,
                    "tier": get_obj_attr(s, "base_tier", "outpost"),
                    "planet": planet,
                    "x": get_obj_attr(s, "base_x"),
                    "y": get_obj_attr(s, "base_y"),
                }
        except Exception:  # noqa: BLE001 - a bad sentinel must not abort startup
            logger.exception("rebuild_from_world: active-base rebuild failed")

        # Reload persisted pending respawns from each planet room.
        try:
            self._pending_respawns = []
            for planet, room in (self._planet_rooms_provider() or {}).items():
                pending = get_obj_attr(room, _PENDING_ATTR, None) or []
                for p in pending:
                    # Copy defensively; ignore malformed rows.
                    if isinstance(p, dict) and "respawn_at" in p:
                        self._pending_respawns.append({
                            "tier": p.get("tier", "outpost"),
                            "planet": p.get("planet", planet),
                            "respawn_at": p["respawn_at"],
                        })
        except Exception:  # noqa: BLE001
            logger.exception("rebuild_from_world: pending-respawn reload failed")

    def _save_state(self) -> None:
        """Persist pending respawns onto their PlanetRoom (Req 7.6).

        Pending respawns are primitives (tier/planet/respawn_at), grouped per
        planet and written to that planet's room attribute. PlanetRooms are
        persistent Evennia objects, so the schedule survives a restart. A no-op
        outside a full Evennia env (test rooms lack ``db``/``attributes``), so
        unit tests keep working in-memory.
        """
        try:
            rooms = self._planet_rooms_provider() or {}
        except Exception:  # noqa: BLE001
            return
        if not rooms:
            return
        by_planet: dict[Any, list] = {planet: [] for planet in rooms}
        for p in self._pending_respawns:
            by_planet.setdefault(p["planet"], []).append({
                "tier": p["tier"], "planet": p["planet"],
                "respawn_at": p["respawn_at"],
            })
        for planet, room in rooms.items():
            try:
                set_obj_attr(room, _PENDING_ATTR, by_planet.get(planet, []))
            except Exception:  # noqa: BLE001 - persistence is best-effort
                logger.exception("Failed to persist pending respawns for %s", planet)

    # ------------------------------------------------------------------ #
    #  Placement algorithm
    # ------------------------------------------------------------------ #

    def _find_placement(
        self, planet: str, room: Any, template: Any
    ) -> tuple[int, int] | None:
        """Find a valid HQ tile for *template*, or None after the attempt cap.

        A candidate is valid when every tile the template occupies (HQ + each
        building offset) is in-bounds, on passable terrain, and unoccupied, and
        the HQ tile keeps ``_MIN_BASE_SEPARATION`` from every existing building
        (other NPC bases and player HQs alike).
        """
        space = self._planet_space(planet)
        if space is None:
            return None
        width, height = space.width, space.height

        offsets = [b.offset for b in template.buildings] or [(0, 0)]

        # Keep bases clear of the player spawn point so a new player never spawns
        # inside a fortress (the separation check below only knows about other
        # NPC bases + existing buildings, and a fresh player owns none yet).
        spawn = (getattr(space, "spawn_x", None), getattr(space, "spawn_y", None))
        spawn_pt = spawn if spawn[0] is not None and spawn[1] is not None else None

        for _ in range(_MAX_PLACEMENT_ATTEMPTS):
            hx = self._rng.randint(0, width - 1)
            hy = self._rng.randint(0, height - 1)
            if self._placement_valid(planet, room, hx, hy, offsets, spawn_pt):
                return (hx, hy)
        return None

    def _placement_valid(
        self, planet: str, room: Any, hx: int, hy: int, offsets: list,
        spawn_pt: tuple[int, int] | None = None,
    ) -> bool:
        """Return True if a base with *offsets* fits at HQ tile (hx, hy)."""
        # Keep clear of the player spawn point.
        if spawn_pt is not None:
            if abs(spawn_pt[0] - hx) + abs(spawn_pt[1] - hy) < _MIN_BASE_SEPARATION:
                return False

        # Separation from existing NPC bases (cheap in-memory check first).
        for rec in self._active_bases.values():
            if rec["planet"] != planet:
                continue
            if abs(rec["x"] - hx) + abs(rec["y"] - hy) < _MIN_BASE_SEPARATION:
                return False

        for dx, dy in offsets:
            x, y = hx + dx, hy + dy
            if not self._coord_valid(planet, x, y):
                return False
            if not self._terrain_passable(planet, x, y):
                return False
            if self._tile_occupied(room, x, y):
                return False
        return True

    def _coord_valid(self, planet: str, x: int, y: int) -> bool:
        if self._planet_registry is None:
            return True
        try:
            return self._planet_registry.is_valid_coordinate(x, y, planet)
        except Exception:  # noqa: BLE001 - unknown planet → invalid
            return False

    def _terrain_passable(self, planet: str, x: int, y: int) -> bool:
        """True if the tile's terrain is passable (buildable)."""
        if self._terrain is None:
            return True
        try:
            terrain_type, _res = self._terrain.get_terrain_and_resource(planet, x, y)
        except Exception:  # noqa: BLE001
            return False
        if terrain_type is None:
            return True  # provider unavailable — don't block placement
        try:
            tdef = self.registry.get_terrain(terrain_type)
        except (KeyError, AttributeError):
            return False
        return bool(getattr(tdef, "passable", True))

    @staticmethod
    def _tile_occupied(room: Any, x: int, y: int) -> bool:
        """True if a building already sits on (x, y)."""
        if room is None or not hasattr(room, "get_buildings_at"):
            return False
        try:
            return bool(room.get_buildings_at(int(x), int(y)))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _planet_space(self, planet: str) -> Any:
        if self._planet_registry is None:
            return None
        try:
            return self._planet_registry.get_space(planet)
        except Exception:  # noqa: BLE001
            return None

    def _resolve_building(self, abbr: str) -> Any:
        try:
            return self.registry.resolve_building(abbr)
        except Exception:  # noqa: BLE001
            return None

    def _guard_hp(self, tier: str) -> int:
        bal = self.registry.balance
        return bal.fortress_guard_hp if tier == "fortress" else bal.outpost_guard_hp

    def _sentinel_name(self, template: Any) -> str:
        """A per-base display name, e.g. "Outpost #3"."""
        # Count existing + spawned bases of this tier for a stable-ish suffix.
        n = 1 + sum(1 for r in self._active_bases.values() if r["tier"] == template.tier)
        return f"{template.display_name} #{n}"

    @staticmethod
    def _base_key(sentinel: Any) -> Any:
        """A stable dict key for a base: the sentinel's id (fallback: identity)."""
        return getattr(sentinel, "id", None) or id(sentinel)
