"""
Guard Combat AI — makes guard/soldier NPCs defend their base each tick.

A per-tick system that gives ``role`` ``"guard"``/``"soldier"`` NPCs
target-acquisition and auto-attack behavior. It is **ownership-generic**: the
only inputs are an NPC's ``db.owner`` and ``db.role``, so it defends a player's
base against raiders and an NPC outpost against players with the same code path
(spec D9). Each guard whose owner has an active HQ acquires the nearest
non-owner player within ``guard_aggro_radius`` and queues an attack through the
standard combat engine using a synthetic guard weapon.

Placed as the ``"guard_combat"`` tick step BEFORE ``combat_resolution`` so a
guard's queued attack resolves in the SAME tick (an intentional asymmetry with
turrets, which fire AFTER resolution and land the next tick — see the tick
ordering rationale in ``typeclasses/scripts.py``).

Framework-free (no Evennia imports): it reads coordinates/owner via the shared
``world.utils`` helpers and queues attacks via the injected CombatEngine.
"""

from __future__ import annotations

from typing import Any

from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem

#: NPC roles that fight when their base is active.
GUARD_ROLES = ("guard", "soldier")


class GuardCombatSystem(BaseSystem):
    """Per-tick target acquisition + auto-attack for guard/soldier NPCs.

    Args:
        registry: DataRegistry holding balance config and definitions.
        event_bus: EventBus (unused directly; attacks route through the engine,
            which publishes ``COMBAT_ACTION``).
        combat_engine: The :class:`~world.systems.combat_engine.CombatEngine`
            used to queue guard attacks. May be ``None`` in isolated tests that
            only exercise target acquisition.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        combat_engine: Any = None,
        sight_blocked_func: Any = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._combat_engine = combat_engine
        # Optional LOS predicate (location, x1, y1, x2, y2) -> blocked. Injected
        # at the composition root so guards don't shoot through Walls; None = no
        # LOS restriction (unit tests / minimal setups).
        self._sight_blocked = sight_blocked_func

    def set_sight_blocked_func(self, func: Any) -> None:
        """Inject the line-of-sight predicate used to gate guard fire."""
        self._sight_blocked = func

    # ------------------------------------------------------------------ #
    #  Per-tick processing
    # ------------------------------------------------------------------ #

    def process_tick(
        self, tick_number: int, npcs: list, active_owner_ids: set | None = None
    ) -> None:
        """Acquire targets and queue attacks for every eligible guard in *npcs*.

        For each NPC with a guard/soldier role, non-reserved, non-incapacitated,
        alive, and whose owner has an active HQ: find the nearest non-owner
        player within ``guard_aggro_radius`` and queue an attack with a synthetic
        weapon sized for the guard's tier. Each guard is isolated in its own
        try/except so one bad guard never halts the rest of the step.

        Args:
            tick_number: The current game tick (unused today; kept for parity
                with other per-tick systems and future cooldown logic).
            npcs: Candidate NPC objects (the agent roster in PvP; extended with
                enemy-base guards in the PvE spawner phase).
            active_owner_ids: Optional precomputed set of owner ids that have a
                live HQ this tick (see ``world.utils.active_hq_owner_ids``). When
                supplied, the deactivation gate is a set-membership test instead
                of a per-guard ``get_buildings()`` DB query. When ``None``
                (isolated tests), it falls back to the per-owner
                :func:`owner_has_active_hq` live query.
        """
        if self._combat_engine is None or not npcs:
            return

        from world.systems.agent_constants import logger

        aggro_radius = self.registry.balance.guard_aggro_radius

        for npc in npcs:
            try:
                self._process_guard(npc, aggro_radius, active_owner_ids)
            except Exception:  # noqa: BLE001 - one bad guard must not halt the step
                logger.exception(
                    "GuardCombat error for %s", getattr(npc, "key", "?")
                )

    def _process_guard(
        self, npc: Any, aggro_radius: int, active_owner_ids: set | None = None
    ) -> None:
        """Acquire a target for one guard and queue its attack (if any)."""
        # Role gate: only guards/soldiers fight.
        role = getattr(getattr(npc, "db", None), "role", "") or ""
        if role.lower() not in GUARD_ROLES:
            return

        # Skip benched/incapacitated/dead guards.
        if not self._is_combat_ready(npc):
            return

        # Deactivation gate: a guard whose owner's base has no active HQ is inert
        # (mirrors the turret rule). A guard with no owner also fails this
        # (owner_has_active_hq(None, ...) is False), so ownerless guards do
        # nothing rather than attacking everyone.
        owner = getattr(npc.db, "owner", None)
        location = getattr(npc, "location", None)
        if active_owner_ids is not None:
            # Cheap per-tick gate: the owner must have a live HQ (in the set).
            # An ownerless guard has no id in the set, so it stays inert.
            oid = getattr(owner, "id", None)
            if oid is None or oid not in active_owner_ids:
                return
        else:
            from world.utils import owner_has_active_hq

            planet = getattr(location, "planet_name", None)
            if not owner_has_active_hq(owner, planet, provider=self.registry):
                return

        coords = self._get_coords(npc)
        if coords is None:
            return
        gx, gy = coords

        target = self._acquire_target(npc, owner, location, gx, gy, aggro_radius)
        if target is None:
            # No hostile in range — a base guard that chased a now-departed
            # raider walks back to its post so the garrison doesn't drift off
            # the HQ over successive raids.
            self._return_home(npc, gx, gy)
            return

        weapon = self._guard_weapon(role)
        weapon_range = 1 if role.lower() != "soldier" else \
            self.registry.balance.guard_ranged_range

        tcoords = self._get_coords(target)
        if tcoords is None:
            t_loc = getattr(target, "location", target)
            tcoords = self._get_coords(t_loc)
        dist = (abs(gx - tcoords[0]) + abs(gy - tcoords[1])) if tcoords else None

        if dist is not None and dist <= weapon_range:
            # In weapon range — attack. queue_attack still runs the full
            # validation pipeline (range/self/ammo) as a backstop.
            self._combat_engine.queue_attack(npc, target, weapon=weapon)
        else:
            # In aggro range but out of weapon range: close the distance so a
            # melee garrison isn't inert against a raider who kites the walls.
            # Bounded to the base so guards stay defensive (see _chase).
            self._chase(npc, gx, gy, tcoords, aggro_radius)

    # ------------------------------------------------------------------ #
    #  Target acquisition
    # ------------------------------------------------------------------ #

    def _acquire_target(
        self,
        npc: Any,
        owner: Any,
        location: Any,
        gx: int,
        gy: int,
        aggro_radius: int,
    ) -> Any | None:
        """Return the nearest non-owner player within *aggro_radius*, or None."""
        from world.utils import is_owner

        players = self._nearby_players(location, gx, gy, aggro_radius)
        nearest = None
        nearest_dist = aggro_radius + 1
        for player in players:
            # Never attack the guard's own owner or a fellow unit of that owner.
            if is_owner(player, owner):
                continue
            p_coords = self._get_coords(player)
            if p_coords is None:
                p_loc = getattr(player, "location", player)
                p_coords = self._get_coords(p_loc)
            if p_coords is None:
                continue
            dist = abs(gx - p_coords[0]) + abs(gy - p_coords[1])
            if dist > aggro_radius or dist >= nearest_dist:
                continue
            # A Wall between the guard and the target blocks a ranged shot; skip
            # so a soldier doesn't fire through its own fortress walls. Melee
            # guards still "see" adjacent targets (no wall fits between tiles a
            # step apart). LOS is best-effort — off when no predicate injected.
            if self._sight_blocked is not None and self._sight_blocked(
                location, gx, gy, p_coords[0], p_coords[1]
            ):
                continue
            nearest = player
            nearest_dist = dist
        return nearest

    def _chase(
        self, npc: Any, gx: int, gy: int,
        tcoords: tuple[int, int] | None, aggro_radius: int,
    ) -> None:
        """Queue a single greedy step toward *tcoords*, bounded to the base.

        Makes a melee garrison chase a raider who kites, instead of standing
        inert on the HQ tile. Kept conservative and defensive:

        - Only base guards with a stamped home post (``db.home_x/home_y``)
          chase. A guard/soldier with no home (e.g. a player-assigned agent) is
          left alone — chasing without a fixed home would reset the leash each
          tick and let it follow a raider across the whole map.
        - Only when the NPC exposes ``set_movement_queue`` (real NPCs do; test
          doubles usually don't, so this is a no-op there) and has no step
          already queued.
        - Leashed to home: it will not step to a tile farther than
          ``aggro_radius`` (Manhattan) from its post, so guards defend their
          base rather than being lured across the map.
        - One tile per call along the axis of greatest distance. Passability is
          enforced downstream by ``NPC.advance_movement`` (it halts on an
          impassable tile), so no terrain lookup is needed here.
        """
        if tcoords is None:
            return
        home = self._home(npc)
        if home is None:
            # No home anchor (e.g. a player-assigned guard/soldier agent, which
            # has no base post): do NOT chase. Chasing without a fixed home
            # would reset the leash every tick and let the guard follow a raider
            # across the whole map. Only base guards (stamped home_x/home_y)
            # chase, and only within aggro_radius of their post.
            return

        nx, ny = self._greedy_step(gx, gy, tcoords[0], tcoords[1])
        if (nx, ny) == (gx, gy):
            return
        # Leash: never step beyond aggro_radius from home.
        if abs(nx - home[0]) + abs(ny - home[1]) > aggro_radius:
            return
        self._queue_step(npc, nx, ny)

    def _return_home(self, npc: Any, gx: int, gy: int) -> None:
        """Step one tile back toward the guard's home post, if it has drifted.

        Only base guards (with a stamped home) return; player-assigned agents
        (no home) are left where they are. No-op when already home or already
        moving.
        """
        home = self._home(npc)
        if home is None or (gx, gy) == home:
            return
        nx, ny = self._greedy_step(gx, gy, home[0], home[1])
        if (nx, ny) != (gx, gy):
            self._queue_step(npc, nx, ny)

    @staticmethod
    def _home(npc: Any) -> tuple[int, int] | None:
        """Return the guard's home post (x, y), or None if it has no anchor."""
        db = getattr(npc, "db", None)
        if db is None:
            return None
        hx = getattr(db, "home_x", None)
        hy = getattr(db, "home_y", None)
        if hx is None or hy is None:
            return None
        return int(hx), int(hy)

    @staticmethod
    def _greedy_step(gx: int, gy: int, tx: int, ty: int) -> tuple[int, int]:
        """One-tile greedy step from (gx, gy) toward (tx, ty) (larger axis first)."""
        nx, ny = gx, gy
        if abs(tx - gx) >= abs(ty - gy):
            nx = gx + (1 if tx > gx else -1 if tx < gx else 0)
        else:
            ny = gy + (1 if ty > gy else -1 if ty < gy else 0)
        return nx, ny

    @staticmethod
    def _queue_step(npc: Any, nx: int, ny: int) -> None:
        """Queue a single movement step, unless the NPC is already moving."""
        if not hasattr(npc, "set_movement_queue"):
            return
        db = getattr(npc, "db", None)
        if db is None or getattr(db, "movement_queue", None):
            return  # no movement support, or already en route — don't thrash
        try:
            npc.set_movement_queue([(nx, ny)])
        except Exception:  # noqa: BLE001 - a move step must never break the tick
            pass

    def _guard_weapon(self, role: str) -> Any:
        """Build the synthetic weapon for a guard of *role*.

        Guards (outpost) are melee (range 1); soldiers (fortress) are ranged
        with a configurable range. Damage/range come from BalanceConfig so both
        are hot-tunable.
        """
        from world.systems.combat_engine import _GuardWeapon

        bal = self.registry.balance
        if role.lower() == "soldier":
            return _GuardWeapon(
                bal.guard_ranged_damage, bal.guard_ranged_range,
                weapon_type="ranged",
            )
        return _GuardWeapon(bal.guard_melee_damage, 1, weapon_type="melee")

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_combat_ready(npc: Any) -> bool:
        """True if *npc* can fight: not reserved, not incapacitated, HP > 0."""
        db = getattr(npc, "db", None)
        if db is None:
            return False
        if getattr(db, "reserve", False):
            return False
        if getattr(db, "incapacitated", False):
            return False
        hp = getattr(db, "hp", None)
        if hp is not None and hp <= 0:
            return False
        return True

    @staticmethod
    def _get_coords(obj: Any) -> tuple[int, int] | None:
        from world.utils import get_coords
        return get_coords(obj)

    @staticmethod
    def _nearby_players(location: Any, x: int, y: int, radius: int) -> list:
        """Return players near ``(x, y)`` within *radius* via the location.

        Prefers the PlanetRoom's ``get_nearby_players(x, y, radius)`` spatial
        query (shared with turret targeting). Falls back to a ``_nearby_players``
        attribute for lightweight test doubles. Returns ``[]`` otherwise.
        """
        if location is None:
            return []
        if hasattr(location, "get_nearby_players"):
            return location.get_nearby_players(x, y, radius)
        if hasattr(location, "_nearby_players"):
            return location._nearby_players
        return []
