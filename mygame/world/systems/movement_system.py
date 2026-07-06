"""
MovementSystem — NPC movement tracking and pathfinding throttling.

Maintains an in-memory set of moving NPCs to avoid per-tick DB queries
(same pattern as ``agent_system._training_buildings``). Throttles
pathfinding requests to prevent tick stalls.

"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("evennia.world.systems.movement_system")


@dataclass
class PathRequest:
    """A queued pathfinding request."""

    npc: Any
    start: tuple[int, int]
    goal: tuple[int, int]
    on_complete: Callable[[list[tuple[int, int]]], None]
    is_passable: Callable[[int, int], bool] | None = None
    width: int = 1000
    height: int = 1000


class MovementSystem:
    """Manages NPC movement tracking and pathfinding throttling.

    Maintains an in-memory set of moving NPCs to avoid per-tick DB
    queries. Throttles pathfinding requests to prevent tick stalls.
    """

    def __init__(self, max_paths_per_tick: int | None = None):
        from world.constants import MAX_PATHS_PER_TICK
        self.max_paths_per_tick = max_paths_per_tick if max_paths_per_tick is not None else MAX_PATHS_PER_TICK
        self._pending_requests: list[PathRequest] = []
        self._paths_this_tick: int = 0
        self._moving_npcs: set = set()  # in-memory, rebuilt on restart
        self._initialized: bool = False

    # ------------------------------------------------------------------ #
    #  Moving NPC tracking
    # ------------------------------------------------------------------ #

    def register_moving(self, npc: Any) -> None:
        """Add an NPC to the moving set (called by NPC.set_movement_queue)."""
        self._moving_npcs.add(npc)

    def unregister_moving(self, npc: Any) -> None:
        """Remove an NPC from the moving set (called by NPC.clear_movement)."""
        self._moving_npcs.discard(npc)

    # ------------------------------------------------------------------ #
    #  Per-tick movement processing
    # ------------------------------------------------------------------ #

    def process_movement(self, tick_number: int) -> None:
        """Advance all tracked moving NPCs one step.

        Iterates a snapshot of ``_moving_npcs`` so removals during
        iteration are safe. NPCs whose queues become empty are
        automatically unregistered.
        """
        self._ensure_initialized()
        for npc in list(self._moving_npcs):
            try:
                npc.advance_movement(tick_number)
                # Remove if queue is now empty
                queue = getattr(getattr(npc, "db", None), "movement_queue", None)
                if not queue:
                    self._moving_npcs.discard(npc)
            except Exception:
                # Per-NPC resilience — one failure doesn't block others
                logger.exception("Error processing movement for %s", npc)

    # ------------------------------------------------------------------ #
    #  Lazy initialization (server restart recovery)
    # ------------------------------------------------------------------ #

    def _ensure_initialized(self) -> None:
        """Lazy rebuild of ``_moving_npcs`` from DB on first access after restart.

        Scans all NPCs with the ``"npc"`` object_type tag and checks
        ``db.movement_queue``. Only runs once.
        """
        if self._initialized:
            return
        self._initialized = True
        try:
            from evennia.utils.search import search_object_by_tag

            for npc in search_object_by_tag("npc", category="object_type"):
                queue = getattr(getattr(npc, "db", None), "movement_queue", None)
                if queue:
                    self._moving_npcs.add(npc)
        except Exception:
            # Evennia may not be fully available (e.g., during tests)
            pass

    # ------------------------------------------------------------------ #
    #  Pathfinding throttle
    # ------------------------------------------------------------------ #

    def request_path(
        self,
        npc: Any,
        start: tuple[int, int],
        goal: tuple[int, int],
        on_complete: Callable[[list[tuple[int, int]]], None],
        is_passable: Callable[[int, int], bool] | None = None,
        width: int = 1000,
        height: int = 1000,
    ) -> None:
        """Queue a pathfinding request. Processed this tick if under limit."""
        self._pending_requests.append(
            PathRequest(
                npc=npc, start=start, goal=goal, on_complete=on_complete,
                is_passable=is_passable, width=width, height=height,
            )
        )

    def process_pathfinding(self) -> None:
        """Process up to ``max_paths_per_tick`` pending requests.

        Each processed request invokes its ``on_complete`` callback with
        the computed path. Requests beyond the per-tick limit remain in
        the queue for subsequent ticks.
        """
        from world.pathfinding import find_path

        processed = 0
        remaining: list[PathRequest] = []

        for req in self._pending_requests:
            if processed >= self.max_paths_per_tick:
                remaining.append(req)
                continue

            try:
                checker = req.is_passable or (lambda x, y: True)
                path = find_path(
                    start=req.start,
                    goal=req.goal,
                    is_passable=checker,
                    width=req.width,
                    height=req.height,
                )
                req.on_complete(path)
            except Exception:
                logger.exception(
                    "Error computing path for %s from %s to %s",
                    req.npc,
                    req.start,
                    req.goal,
                )
            processed += 1
            self._paths_this_tick += 1

        self._pending_requests = remaining

    def reset_tick(self) -> None:
        """Reset the per-tick counter. Called at start of each tick."""
        self._paths_this_tick = 0
