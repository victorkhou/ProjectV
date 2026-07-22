"""
Bomb System — fused explosives (grenades + mines).

Two bomb families, both a fused AoE explosive placed on a tile:

* **Grenades** (``throwable`` items) are THROWN in a compass direction with
  ``throw <grenade> <n/s/e/w>``. The grenade flies until it hits the first
  obstacle (a building/unit) or reaches the item's max ``range``, LANDS on that
  tile, and a countdown fuse ticks down before it explodes.
* **Mines** (``mine`` items) are ARMED in place with ``arm <mine>``; the same
  fuse then ticks down before the explosion on the arming tile.

Both require the player to have SET a fuse first with ``set <bomb> <seconds>``
(or ``set all <seconds>`` for the whole inventory). A bomb thrown/armed without
a set fuse is rejected. Setting a fuse arms EVERY unit of that type the player
holds: the fuses are stored as a per-type queue on the player
(``db.bomb_fuses = {item_key: [seconds, ...]}``), one entry per held unit, and
each throw/arm consumes one — so a single ``set all 3`` lets a stack of 3
grenades all be thrown. Re-setting a type resets its queue to the current held
count.

A live bomb is a :class:`~typeclasses.objects.LiveBomb` resting on its tile.
Each tick the system decrements every live bomb's fuse and shows the countdown
to everyone standing on that tile; at zero it detonates as an indiscriminate
AoE blast (hitting enemies, the placer's own units, AND the placer if they are
in the radius — kills credit the placer) and the bomb is removed.

Framework-free: all Evennia I/O (creating the LiveBomb, querying a planet's
tiles, resolving the placing player) is injected as callables at the
composition root. The system owns fuse math, the throw ray, the countdown, and
detonation.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from world.constants import (
    BOMB_CATEGORIES,
    DEFAULT_BOMB_FUSE,
    DEFAULT_BOMB_FUSE_MAX,
    DEFAULT_BOMB_FUSE_MIN,
    DEFAULT_THROW_RANGE,
)
from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem
from world.utils import coords_of

logger = logging.getLogger("evennia.world.systems.bomb_system")

#: Compass direction -> unit (dx, dy) step for throwing a grenade. Mirrors
#: CmdMove.DIRECTION_MAP (north = +y).
_DIRECTIONS = {
    "north": (0, 1), "n": (0, 1),
    "south": (0, -1), "s": (0, -1),
    "east": (1, 0), "e": (1, 0),
    "west": (-1, 0), "w": (-1, 0),
}


class BombSystem(BaseSystem):
    """Owns bomb fuse config, throwing/arming, the fuse countdown, and blasts.

    Injected collaborators (all optional so the system is testable in isolation
    and degrades gracefully before composition-root wiring):

    * ``spawn_bomb_func(location, item_def, x, y, owner, bomb_type, fuse,
      amount, radius) -> LiveBomb|None`` — create a live bomb on a tile.
    * ``area_damage_applier() -> combat_engine`` — resolve the AoE blast; the
      engine's ``apply_direct_hit`` deals the flat blast damage and runs the
      shared post-damage pipeline (lockout, event, notify, defeat).
    * ``current_tick_func() -> int`` — the game tick (unused for the pure
      relative countdown, accepted for parity with other systems).
    * ``in_bounds_func(x, y, planet_key) -> bool`` — whether a tile is on the
      map, so a thrown grenade stops at the map edge instead of landing off-map.
      When absent (test/unwired) all tiles are treated as in-bounds.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        spawn_bomb_func: Callable | None = None,
        area_damage_applier: Callable[[], Any] | None = None,
        current_tick_func: Callable[[], int] | None = None,
        in_bounds_func: Callable[[int, int, str], bool] | None = None,
        rng_func: Callable[[], float] | None = None,
        randint_func: Callable[[int, int], int] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._spawn_bomb_func = spawn_bomb_func
        self._area_damage_applier = area_damage_applier
        self._current_tick_func = current_tick_func or (lambda: 0)
        self._in_bounds_func = in_bounds_func
        #: Returns a float in [0, 1) for the disarm success roll, and an int in
        #: [a, b] for the disarm-duration roll; injectable so tests can force a
        #: deterministic outcome/duration. Default to the stdlib random.
        import random as _random
        self._rng_func = rng_func or _random.random
        self._randint_func = randint_func or _random.randint
        #: Live bombs currently counting down. An in-memory list so the per-tick
        #: countdown does not DB-scan every second; rebuilt from the world on
        #: restart via :meth:`rebuild_from_world` (fuse state persists on the
        #: LiveBomb's db, so a reboot resumes rather than resets).
        self._live_bombs: list = []

    # ------------------------------------------------------------------ #
    #  Setters for late-bound collaborators (composition root)
    # ------------------------------------------------------------------ #

    def set_spawn_bomb_func(self, fn: Callable) -> None:
        self._spawn_bomb_func = fn

    def set_area_damage_applier(self, provider: Callable[[], Any]) -> None:
        self._area_damage_applier = provider

    def set_in_bounds_func(self, fn: Callable[[int, int, str], bool]) -> None:
        self._in_bounds_func = fn

    def _tile_in_bounds(self, x: int, y: int, planet_key) -> bool:
        """Return True if ``(x, y)`` is a valid on-map tile for *planet_key*.

        Delegates to the injected bounds check (``planet_registry.
        is_valid_coordinate`` at the composition root). Falls open (True) when no
        bounds func is wired or the planet is unknown, so a bomb never fails to
        place in a lightweight/test context — the check only ever REMOVES an
        off-map landing, never blocks a valid one.
        """
        if self._in_bounds_func is None or not planet_key:
            return True
        try:
            return bool(self._in_bounds_func(int(x), int(y), planet_key))
        except Exception:  # noqa: BLE001 - unknown planet / bad coords: fall open
            return True

    # ------------------------------------------------------------------ #
    #  Fuse configuration ('set <bomb> <sec>' / 'set all <sec>')
    # ------------------------------------------------------------------ #

    def _bomb_item_def(self, item_key: str):
        """Resolve *item_key* to its ItemDef iff it is a bomb, else None."""
        idef = self.registry.resolve_item(item_key)
        if idef is None:
            return None
        if getattr(idef, "category", None) not in BOMB_CATEGORIES:
            return None
        return idef

    def _rank_allows(self, player: Any, item_def, item_name: str) -> bool:
        """Return True if *player*'s rank meets the bomb's ``required_rank``.

        Mirrors ``EquipmentSystem._rank_allows`` so a rank-gated bomb (e.g.
        plasma_grenade → Sergeant, proximity_mine → Staff_Sergeant) can't be
        deployed by a player below its rank — passive production/craft doesn't
        rank-gate, so the gate must live on the deploy path too. Emits
        ``equip_denied`` (the shared rank-gate notification) and returns False
        when the rank is insufficient. An unknown rank name falls open, matching
        the equip/use gate. Never raises.
        """
        required_rank = getattr(item_def, "required_rank", None)
        if not required_rank:
            return True
        from world.utils import get_player_level
        from world.systems.rank_system import rank_from_level, player_meets_rank

        player_level = get_player_level(player)
        if not player_meets_rank(player_level, required_rank, self.registry):
            rank_num = rank_from_level(player_level)
            current_def = self.registry.get_rank_by_level(rank_num)
            current = current_def.name if current_def else f"Rank {rank_num}"
            self.notify(player, "equip_denied", item_name=item_name,
                        required_rank=required_rank, current_rank=current)
            return False
        return True

    @staticmethod
    def _fuse_bounds(item_def) -> tuple[int, int, int]:
        """Return (fuse_min, fuse_max, fuse_default) for a bomb def.

        Reads the bomb's ``effect`` dict, falling back to the module defaults
        for any bound the item does not declare.
        """
        effect = getattr(item_def, "effect", None) or {}

        def _as_int(key, default):
            try:
                v = int(effect.get(key, default))
                return v if v > 0 else default
            except (TypeError, ValueError):
                return default

        fmin = _as_int("fuse_min", DEFAULT_BOMB_FUSE_MIN)
        fmax = _as_int("fuse_max", DEFAULT_BOMB_FUSE_MAX)
        fdef = _as_int("fuse_default", DEFAULT_BOMB_FUSE)
        if fmin > fmax:
            fmin, fmax = fmax, fmin
        fdef = max(fmin, min(fmax, fdef))
        return fmin, fmax, fdef

    def set_fuse(self, player: Any, item_key: str, seconds: int) -> bool:
        """Set the fuse (seconds) on EVERY unit of one bomb type the player holds.

        Clamps to the bomb's [fuse_min, fuse_max] and arms all of that type at
        once: a queue of one fuse per held unit is stored on the player's
        ``db.bomb_fuses`` map, and each throw/arm consumes one — so setting once
        lets you deploy every bomb you carry of that type. Rejects a non-bomb
        item or one the player does not hold. Notifies the outcome.
        """
        item_def = self._bomb_item_def(item_key)
        item_name = getattr(item_def, "name", None) or item_key
        if item_def is None:
            self.notify(player, "not_a_bomb", item_name=item_name)
            return False
        handler = getattr(player, "equipment", None)
        held = handler.get_supply(item_key) if handler is not None else 0
        if handler is None or held <= 0:
            self.notify(player, "bomb_not_held", item_name=item_name)
            return False
        fmin, fmax, _ = self._fuse_bounds(item_def)
        clamped = max(fmin, min(fmax, int(seconds)))
        self._arm_fuses(player, item_key, clamped, held)
        self.notify(player, "fuse_set", item_name=item_name, seconds=clamped,
                    clamped=(clamped != int(seconds)), fuse_min=fmin, fuse_max=fmax,
                    count=held)
        return True

    def set_all(self, player: Any, seconds: int) -> int:
        """Arm the fuse on EVERY unit of EVERY bomb type in the inventory.

        Each bomb type is clamped to its own [fuse_min, fuse_max] (they may
        differ), so 'set all 20' gives a grenade its max 10 and a mine 20. Every
        unit held is armed (a queue of one fuse per unit), so a stack of 3
        grenades can all be thrown from a single 'set all'. Returns the number
        of individual bombs armed; notifies a summary.
        """
        handler = getattr(player, "equipment", None)
        if handler is None:
            self.notify(player, "fuse_all_set", count=0, types=0,
                        seconds=int(seconds))
            return 0
        armed = 0
        types = 0
        for item_key, held in list(handler.get_supplies().items()):
            item_def = self._bomb_item_def(item_key)
            if item_def is None or held <= 0:
                continue
            fmin, fmax, _ = self._fuse_bounds(item_def)
            clamped = max(fmin, min(fmax, int(seconds)))
            self._arm_fuses(player, item_key, clamped, held)
            armed += held
            types += 1
        self.notify(player, "fuse_all_set", count=armed, types=types,
                    seconds=int(seconds))
        return armed

    @staticmethod
    def _arm_fuses(player: Any, item_key: str, seconds: int, count: int) -> None:
        """Store a fuse for each of *count* held units of *item_key*.

        Replaces (does not append to) any existing queue for this type, so
        re-setting a type resets its pending fuses to the current held count
        rather than stacking stale entries.
        """
        fuses = dict(getattr(player.db, "bomb_fuses", None) or {})
        fuses[item_key] = [int(seconds)] * max(0, int(count))
        player.db.bomb_fuses = fuses

    @staticmethod
    def _pending_fuse(player: Any, item_key: str):
        """Return the NEXT queued fuse for *item_key*, or None if none pending.

        Peeks the head of the per-type fuse queue (does not consume it — the
        deploy path calls :meth:`_consume_fuse` only after a successful place).
        Tolerates a legacy scalar value (pre-queue saves stored one int). The
        queue reads back from a real DB as an Evennia ``_SaverList`` (NOT a
        ``list`` subclass), so we detect the legacy SCALAR form explicitly and
        treat everything else as a sequence.
        """
        fuses = getattr(player.db, "bomb_fuses", None) or {}
        val = fuses.get(item_key)
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val)  # legacy scalar
        queue = list(val)  # list / tuple / _SaverList
        return int(queue[0]) if queue else None

    @staticmethod
    def _consume_fuse(player: Any, item_key: str) -> None:
        """Consume ONE queued fuse after a bomb is deployed.

        Pops the head of the per-type queue; removes the key entirely when the
        queue empties. Tolerates the legacy scalar form (clears it outright).
        The stored value may be an Evennia ``_SaverList`` on a real DB, so the
        legacy scalar is detected explicitly and anything else is a sequence.
        """
        fuses = dict(getattr(player.db, "bomb_fuses", None) or {})
        val = fuses.get(item_key)
        if val is None:
            return
        if isinstance(val, (int, float)):
            del fuses[item_key]  # legacy scalar: one-shot
        else:
            remaining = list(val)[1:]  # list / tuple / _SaverList
            if remaining:
                fuses[item_key] = remaining
            else:
                del fuses[item_key]
        player.db.bomb_fuses = fuses

    # ------------------------------------------------------------------ #
    #  Grenade throw (directional) + mine arm
    # ------------------------------------------------------------------ #

    def throw_grenade(self, player: Any, item_key: str, direction: str) -> bool:
        """Throw a grenade in a compass direction; it lands and starts its fuse.

        The grenade flies from the player's tile in *direction* until it hits
        the first obstacle or reaches the item's max throw ``range`` (Chebyshev):
        it lands just BEFORE a building (bouncing off the structure, so the blast
        breaches from outside), ON a unit's tile (lob it at them), or on the
        max-range tile when the line is clear (see :meth:`_grenade_landing`). A
        :class:`LiveBomb` is placed there with the player's set fuse; everyone on
        the landing tile is told a grenade landed and is ticking. Requires a set
        fuse (``set <bomb> <sec>``) — rejects otherwise.
        """
        item_def = self._bomb_item_def(item_key)
        item_name = getattr(item_def, "name", None) or item_key

        # Gate: is it a grenade the player holds?
        if item_def is None or getattr(item_def, "category", None) != "throwable":
            self.notify(player, "throw_failed", item_name=item_name,
                        reason="not_throwable")
            return False
        handler = getattr(player, "equipment", None)
        if handler is None or handler.get_supply(item_key) <= 0:
            self.notify(player, "throw_failed", item_name=item_name,
                        reason="not_held")
            return False

        # Rank gate — a rank-gated grenade can't be thrown below its rank
        # (production/pickup don't rank-gate, so the deploy path must).
        if not self._rank_allows(player, item_def, item_name):
            return False

        step = _DIRECTIONS.get(direction)
        if step is None:
            self.notify(player, "throw_failed", item_name=item_name,
                        reason="bad_direction")
            return False

        # Fuse must be set first (must set each time).
        fuse = self._pending_fuse(player, item_key)
        if fuse is None:
            self.notify(player, "need_fuse", item_name=item_name)
            return False

        from world.utils import get_coords
        p_coords = get_coords(player)
        location = getattr(player, "location", None)
        if p_coords is None or location is None:
            self.notify(player, "throw_failed", item_name=item_name,
                        reason="no_position")
            return False

        effect = getattr(item_def, "effect", None) or {}
        try:
            throw_range = int(effect.get("range", DEFAULT_THROW_RANGE))
        except (TypeError, ValueError):
            throw_range = DEFAULT_THROW_RANGE

        planet_key = getattr(player.db, "coord_planet", None)
        lx, ly = self._grenade_landing(location, p_coords[0], p_coords[1],
                                       step[0], step[1], throw_range, planet_key)
        amount, radius = self._blast_stats(effect)

        # Place the live bomb FIRST; only consume the grenade + its set fuse on a
        # successful placement, so a placement failure never eats the item.
        bomb = self._place_bomb(location, item_def, lx, ly, player,
                                "grenade", fuse, amount, radius)
        if bomb is None:
            self.notify(player, "throw_failed", item_name=item_name,
                        reason="no_position")
            return False
        handler.remove_supply(item_key, 1)
        self._consume_fuse(player, item_key)
        # Tell the thrower it's away, and everyone on the landing tile.
        self.notify(player, "grenade_thrown", item_name=item_name,
                    x=lx, y=ly, seconds=fuse)
        self._broadcast_tile(location, lx, ly, "bomb_landed",
                             exclude=player, item_name=item_name, seconds=fuse)
        return True

    def arm_mine(self, player: Any, item_key: str) -> bool:
        """Arm a mine on the player's current tile; its fuse starts counting.

        Requires a set fuse. Places a :class:`LiveBomb` on the player's tile and
        announces the arming to everyone on that tile (including the placer, via
        a distinct 'you armed' notice).
        """
        item_def = self._bomb_item_def(item_key)
        item_name = getattr(item_def, "name", None) or item_key

        if item_def is None or getattr(item_def, "category", None) != "mine":
            self.notify(player, "not_a_mine", item_name=item_name)
            return False
        handler = getattr(player, "equipment", None)
        if handler is None or handler.get_supply(item_key) <= 0:
            self.notify(player, "bomb_not_held", item_name=item_name)
            return False
        # Rank gate — a rank-gated mine can't be armed below its rank.
        if not self._rank_allows(player, item_def, item_name):
            return False
        fuse = self._pending_fuse(player, item_key)
        if fuse is None:
            self.notify(player, "need_fuse", item_name=item_name)
            return False

        from world.utils import get_coords
        p_coords = get_coords(player)
        location = getattr(player, "location", None)
        if p_coords is None or location is None:
            self.notify(player, "arm_failed", item_name=item_name,
                        reason="no_position")
            return False

        effect = getattr(item_def, "effect", None) or {}
        amount, radius = self._blast_stats(effect)
        ax, ay = int(p_coords[0]), int(p_coords[1])

        # Place FIRST; consume the mine + its fuse only on success so a failed
        # placement never eats the item.
        bomb = self._place_bomb(location, item_def, ax, ay, player,
                                "mine", fuse, amount, radius)
        if bomb is None:
            self.notify(player, "arm_failed", item_name=item_name,
                        reason="no_position")
            return False
        handler.remove_supply(item_key, 1)
        self._consume_fuse(player, item_key)
        self.notify(player, "mine_armed", item_name=item_name, seconds=fuse)
        # Everyone else on the tile sees it arm (the placer got 'mine_armed').
        self._broadcast_tile(location, ax, ay, "bomb_armed",
                             exclude=player, item_name=item_name, seconds=fuse)
        return True

    # ------------------------------------------------------------------ #
    #  Disarm ('disarm' — neutralize a ticking bomb on your tile)
    # ------------------------------------------------------------------ #

    def disarm(self, player: Any) -> bool:
        """Begin a multi-tick attempt to disarm a ticking bomb on your tile.

        Disarming is NOT instant: it takes ``bomb_disarm_ticks_min..max`` ticks
        (rolled now), during which the bomb's own fuse KEEPS TICKING — so if the
        fuse runs out first, it explodes mid-attempt (a short-fuse bomb may be
        undisarmable). When the disarm timer elapses, a single roll of
        ``bomb_disarm_base_success`` (default 0.7, +bonuses) decides the outcome
        in :meth:`_resolve_disarm`: success removes the bomb; FAILURE detonates
        it immediately. The countdown is driven by the fuse tick loop (see
        :meth:`_tick_one`).

        Starts the attempt and returns True; returns False (with a notice) when
        there is no bomb here or one is already being disarmed. No live bomb on
        the tile → 'nothing to disarm'.
        """
        from world.utils import get_coords
        coords = get_coords(player)
        if coords is None:
            self.notify(player, "disarm_none")
            return False
        px, py = coords

        bomb = self._live_bomb_at(px, py)
        if bomb is None:
            self.notify(player, "disarm_none")
            return False

        db = getattr(bomb, "db", None)
        item_name = getattr(bomb, "key", "bomb")
        # Already being disarmed (by anyone)? Don't restart the timer.
        if db is not None and int(getattr(db, "disarm_ticks_remaining", 0) or 0) > 0:
            self.notify(player, "disarm_in_progress", item_name=item_name)
            return False

        ticks = self._roll_disarm_ticks()
        if db is not None:
            db.disarm_ticks_remaining = ticks
            # Remember who is disarming so the resolution notifies them (they may
            # step off the tile before it finishes). Store the player ref.
            db.disarm_by = player
        self.notify(player, "disarm_start", item_name=item_name, ticks=ticks)
        return True

    def _roll_disarm_ticks(self) -> int:
        """Roll the disarm duration in ``[ticks_min, ticks_max]`` (>= 1)."""
        bal = getattr(self.registry, "balance", None)
        lo = int(getattr(bal, "bomb_disarm_ticks_min", 2) or 2)
        hi = int(getattr(bal, "bomb_disarm_ticks_max", 10) or 10)
        lo = max(1, lo)
        hi = max(lo, hi)
        return int(self._randint_func(lo, hi))

    def _resolve_disarm(self, bomb: Any) -> bool:
        """Resolve a completed disarm attempt. Return True to KEEP the bomb live.

        Called by the tick loop when ``disarm_ticks_remaining`` reaches 0. Rolls
        the success chance for the disarming player: success removes the bomb
        (no blast, returns False so the loop drops it); FAILURE detonates it
        immediately (also returns False — the bomb is gone either way).
        """
        db = getattr(bomb, "db", None)
        location = getattr(bomb, "location", None)
        item_name = getattr(bomb, "key", "bomb")
        player = getattr(db, "disarm_by", None) if db is not None else None
        b_coords = coords_of(bomb)

        if self._rng_func() < self._disarm_success_chance(player):
            # Success: remove it from the world with no explosion.
            if player is not None:
                self.notify(player, "disarm_success", item_name=item_name)
            if location is not None and b_coords is not None:
                bx, by, _planet = b_coords
                self._broadcast_tile(location, int(bx), int(by),
                                     "disarm_success_tile", exclude=player,
                                     item_name=item_name)
            self._delete_bomb(bomb)
            return False
        # Failure: it goes off right now.
        if player is not None:
            self.notify(player, "disarm_failed", item_name=item_name)
        self._detonate(bomb)
        return False

    def _disarm_success_chance(self, player: Any) -> float:
        """Return the disarm success probability for *player*, clamped to [0,1].

        Base ``balance.bomb_disarm_base_success`` (default 0.7). A hook for
        future tech / equipment / class bonuses — a ``db.disarm_bonus`` on the
        player, if present, is added now so those systems can wire in later
        without touching this method. A None player (disarmer vanished) uses the
        base chance alone.
        """
        base = float(getattr(self.registry.balance, "bomb_disarm_base_success", 0.7))
        bonus = 0.0
        db = getattr(player, "db", None) if player is not None else None
        if db is not None:
            try:
                bonus = float(getattr(db, "disarm_bonus", 0.0) or 0.0)
            except (TypeError, ValueError):
                bonus = 0.0
        return max(0.0, min(1.0, base + bonus))

    def _live_bomb_at(self, x: int, y: int) -> Any:
        """Return the first tracked live bomb on tile ``(x, y)``, or None."""
        for bomb in self._live_bombs:
            if getattr(bomb, "pk", True) is None:
                continue
            b_coords = coords_of(bomb)
            if b_coords is None:
                continue
            bx, by, _planet = b_coords
            if int(bx) == int(x) and int(by) == int(y):
                return bomb
        return None

    @staticmethod
    def _blast_stats(effect: dict) -> tuple[int, int]:
        """Read (amount, radius) from a bomb effect, defaulting safely."""
        try:
            amount = int(effect.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0
        try:
            radius = int(effect.get("radius", 0))
        except (TypeError, ValueError):
            radius = 0
        return amount, radius

    def _grenade_landing(self, location, cx, cy, dx, dy, throw_range,
                         planet_key=None) -> tuple[int, int]:
        """Return the tile a thrown grenade lands on (direction to first obstacle).

        Walks outward from ``(cx, cy)`` in the ``(dx, dy)`` direction up to
        *throw_range* tiles. The landing tile depends on what stops the throw:

        * a **building** stops the grenade OUTSIDE it — the grenade lands on the
          last clear tile *before* the building (it bounces off the wall/structure
          rather than passing inside), so its blast then breaches from the tile in
          front. A building on the very first step lands the grenade at the
          thrower's own feet.
        * a **unit** (player/agent/enemy) lands the grenade at their feet (on
          their tile) — you can lob a grenade directly at someone.
        * a clear line lands on the furthest in-bounds tile.

        The ray STOPS at the map edge: a step off-map is never taken, so a
        grenade thrown toward an edge lands ON the edge tile rather than at an
        off-map coordinate (where its blast could reach nothing and the LiveBomb
        would sit on an unreachable tile). Reuses the room's coordinate index the
        same way directional 'shoot' resolves its ray.
        """
        from world.utils import is_building, is_player
        cx, cy = int(cx), int(cy)
        last = (cx, cy)
        get_at = getattr(location, "get_objects_at", None)
        for step in range(1, int(throw_range) + 1):
            tx, ty = cx + dx * step, cy + dy * step
            if not self._tile_in_bounds(tx, ty, planet_key):
                break  # off-map — land on the last in-bounds tile
            if callable(get_at):
                blocked_by_building = False
                blocked_by_unit = False
                for obj in get_at(tx, ty):
                    if is_building(obj):
                        blocked_by_building = True
                    elif is_player(obj):
                        blocked_by_unit = True
                if blocked_by_building:
                    return last  # land on the clear tile just before the building
                if blocked_by_unit:
                    return (tx, ty)  # land at the unit's feet
            last = (tx, ty)
        return last  # clear line — lands at the furthest in-bounds tile

    def _place_bomb(self, location, item_def, x, y, owner, bomb_type,
                    fuse, amount, radius):
        """Create + register a live bomb via the injected spawner, tracking it."""
        if self._spawn_bomb_func is None:
            logger.warning("bomb: no spawn_bomb_func wired; bomb not placed")
            return None
        try:
            bomb = self._spawn_bomb_func(location, item_def, x, y, owner,
                                         bomb_type, fuse, amount, radius)
        except Exception:  # noqa: BLE001 - placement must not raise into a command
            logger.exception("bomb: spawn_bomb_func failed")
            return None
        if bomb is not None:
            self._live_bombs.append(bomb)
        return bomb

    # ------------------------------------------------------------------ #
    #  Per-tick fuse countdown + detonation
    # ------------------------------------------------------------------ #

    def process_tick(self, tick_number: int | None = None) -> None:
        """Advance every live bomb's fuse by one; detonate those that reach 0.

        For each live bomb: skip if deleted (pk None); decrement
        ``fuse_remaining``; if it reaches 0 detonate + delete; otherwise show
        the countdown to everyone on the bomb's tile. Each bomb is isolated so a
        single bad bomb never halts the step. Detonated/dead bombs are pruned
        from the in-memory list.
        """
        if not self._live_bombs:
            return
        survivors = []
        for bomb in self._live_bombs:
            try:
                keep = self._tick_one(bomb)
            except Exception:  # noqa: BLE001 - one bad bomb must not halt the step
                logger.exception("bomb: fuse tick failed for %r",
                                 getattr(bomb, "key", "?"))
                keep = False
            if keep:
                survivors.append(bomb)
        self._live_bombs = survivors

    def _tick_one(self, bomb: Any) -> bool:
        """Tick a single bomb. Return True to keep it live, False to drop it."""
        if getattr(bomb, "pk", True) is None:  # deleted out from under us
            return False
        db = getattr(bomb, "db", None)
        if db is None:
            return False
        # The FUSE always ticks first — the bomb's own countdown wins the race,
        # so a disarm-in-progress bomb still explodes if its fuse runs out
        # before the disarm timer does.
        remaining = int(getattr(db, "fuse_remaining", 0) or 0) - 1
        db.fuse_remaining = remaining
        location = getattr(bomb, "location", None)
        b_coords = coords_of(bomb)
        if remaining <= 0:
            self._detonate(bomb)
            return False
        # A disarm attempt in progress? Count it down and resolve at 0. The bomb
        # survived the fuse tick above, so now the disarm timer gets its tick.
        disarm_left = int(getattr(db, "disarm_ticks_remaining", 0) or 0)
        if disarm_left > 0:
            disarm_left -= 1
            db.disarm_ticks_remaining = disarm_left
            if disarm_left <= 0:
                return self._resolve_disarm(bomb)
        item_name = getattr(bomb, "key", "bomb")
        # Still ticking — show the countdown to everyone on the tile.
        if location is not None and b_coords is not None:
            bx, by, _planet = b_coords
            self._broadcast_tile(location, int(bx), int(by), "bomb_tick",
                                 exclude=None, item_name=item_name,
                                 seconds=remaining)
        return True

    def _detonate(self, bomb: Any) -> None:
        """Resolve a bomb's AoE blast, then delete the bomb.

        The blast is indiscriminate AND breaches cover: every player, agent, and
        building within the Chebyshev *radius* takes flat ``amount − armor`` —
        including the placer's own units AND the placer if they are in the radius,
        buildings whether open or closed, and players inside them. An explosion is
        an anti-structure weapon: it reaches through walls and levels closed
        buildings, so nothing in radius is spared (see :meth:`_blast_targets`).
        Kills credit the placing player.
        """
        db = getattr(bomb, "db", None)
        location = getattr(bomb, "location", None)
        if db is None or location is None:
            self._delete_bomb(bomb)
            return
        b_coords = coords_of(bomb)
        owner = getattr(db, "owner", None)
        amount = int(getattr(db, "amount", 0) or 0)
        radius = int(getattr(db, "radius", 0) or 0)
        item_name = getattr(bomb, "key", "bomb")
        if b_coords is None:
            self._delete_bomb(bomb)
            return
        bx, by, _planet = b_coords
        bx, by = int(bx), int(by)

        targets = self._blast_targets(location, bx, by, radius)
        count = self._apply_blast(owner, targets, amount, radius, item_name, bomb)
        # Announce the explosion to everyone still on the blast's center tile.
        self._broadcast_tile(location, bx, by, "bomb_exploded", exclude=None,
                             item_name=item_name, count=count)
        # Notify the placer of the outcome (owner may be off-tile).
        if owner is not None:
            self.notify(owner, "bomb_detonated", item_name=item_name,
                        x=bx, y=by, count=count)
        self._delete_bomb(bomb)

    @staticmethod
    def _blast_targets(location, bx, by, radius) -> list:
        """Return players/agents/buildings in Chebyshev *radius* of the blast.

        A bomb blast BREACHES cover — unlike ranged fire, an explosion reaches
        buildings and the people inside them:

        * every building in radius is hit, OPEN OR CLOSED (a grenade/mine is an
          anti-structure weapon — it damages and can level a closed wall/building
          just as it does an open one); and
        * every player in radius is hit, sheltered or not (standing inside a
          building is no protection from a blast on or beside your tile — this is
          why the placer standing on their own mine, or in a nearby structure,
          is caught in it).

        Indiscriminate: also includes the placer and their own units (friendly
        fire is intentional). The only filter is Chebyshev range and being a
        damageable combat entity (player/agent/building).
        """
        from world.utils import (
            get_coords, is_building, is_player, chebyshev_distance,
        )
        x1, y1, x2, y2 = bx - radius, by - radius, bx + radius, by + radius
        candidates: list = []
        getter = getattr(location, "get_objects_in_area", None)
        if callable(getter):
            candidates = list(getter(x1, y1, x2, y2))
        else:
            idx = getattr(location, "coord_index", None)
            if idx is not None and hasattr(idx, "get_in_area"):
                candidates = list(idx.get_in_area(x1, y1, x2, y2))
        targets = []
        for obj in candidates:
            if not (is_player(obj) or is_building(obj)):
                continue
            coords = get_coords(obj)
            if coords is None:
                continue
            if chebyshev_distance(coords[0], coords[1], bx, by) <= radius:
                targets.append(obj)
        return targets

    def _apply_blast(self, owner, targets, amount, radius, item_name, bomb) -> int:
        """Apply flat blast damage to each target via the injected combat engine.

        Attributes the blast to the placing *owner* so a kill credits them. When
        the owner is gone (None — e.g. logged out/deleted), the bomb itself is
        the attacker (a valid combat entity with no owner), so the blast still
        resolves. Returns the number of targets damaged.
        """
        if not targets:
            return 0
        if self._area_damage_applier is None:
            return len(targets)
        try:
            engine = self._area_damage_applier()
        except Exception:  # noqa: BLE001
            engine = None
        if engine is None:
            logger.warning("bomb: no area-damage applier; %d target(s) unharmed",
                           len(targets))
            return len(targets)

        from world.systems.combat_engine import SyntheticWeapon
        weapon = SyntheticWeapon(amount, radius, name=item_name)
        attacker = owner if owner is not None else bomb
        hit = 0
        for target in targets:
            try:
                engine.apply_direct_hit(attacker, target, weapon,
                                        include_attacker_bonus=False)
                hit += 1
            except Exception:  # noqa: BLE001 - one bad target must not abort
                logger.warning("bomb: blast failed on %r",
                               getattr(target, "key", target))
        return hit

    @staticmethod
    def _delete_bomb(bomb: Any) -> None:
        """Remove a spent bomb from the world (de-indexes via at_object_delete)."""
        if getattr(bomb, "pk", True) is None:
            return
        try:
            bomb.delete()
        except Exception:  # noqa: BLE001
            logger.exception("bomb: failed to delete spent bomb")

    # ------------------------------------------------------------------ #
    #  Tile broadcast
    # ------------------------------------------------------------------ #

    def _broadcast_tile(self, location, x, y, kind, exclude=None, **data) -> None:
        """Notify every player standing on tile ``(x, y)`` with *kind* + *data*.

        Uses ``get_players_at`` (players on the exact tile — including any
        inside a building on it, since inside_building does not change coords),
        skipping *exclude* (the actor, who gets their own distinct notice) and
        any stale/deleted refs. This is how 'everyone in the same room sees the
        bomb arm and TICK' is delivered — one notify per player through the
        presenter contract.
        """
        get_players = getattr(location, "get_players_at", None)
        if not callable(get_players):
            return
        try:
            players = list(get_players(int(x), int(y)))
        except Exception:  # noqa: BLE001
            return
        for p in players:
            if p is exclude:
                continue
            if getattr(p, "pk", True) is None:
                continue
            self.notify(p, kind, **data)

    # ------------------------------------------------------------------ #
    #  Restart recovery
    # ------------------------------------------------------------------ #

    def rebuild_from_world(self, planet_rooms) -> int:
        """Repopulate the live-bomb list from placed LiveBomb objects on restart.

        The fuse state persists on each LiveBomb's ``db`` (and its coords, so the
        coordinate index rebuilds for free), but the in-memory countdown list is
        non-persistent — without this a bomb armed before a reboot would sit
        inert forever. Scans each PlanetRoom's bomb objects and re-tracks any
        with a positive remaining fuse. Returns the number re-tracked.
        """
        self._live_bombs = []
        rooms = planet_rooms.values() if hasattr(planet_rooms, "values") else planet_rooms
        for room in rooms:
            get_at = getattr(room, "get_objects_at", None)
            # Prefer a whole-room bomb scan when available (get_in_room), else
            # fall back to nothing — a room with no bomb query yields no bombs.
            bombs = []
            finder = getattr(room, "get_all_bombs", None)
            if callable(finder):
                try:
                    bombs = list(finder())
                except Exception:  # noqa: BLE001
                    bombs = []
            else:
                # Scan contents for the bomb object_type tag.
                for obj in getattr(room, "contents", []):
                    tags = getattr(obj, "tags", None)
                    if tags is not None and tags.get("bomb", category="object_type"):
                        bombs.append(obj)
            for bomb in bombs:
                db = getattr(bomb, "db", None)
                if db is None:
                    continue
                if int(getattr(db, "fuse_remaining", 0) or 0) > 0:
                    self._live_bombs.append(bomb)
        return len(self._live_bombs)
