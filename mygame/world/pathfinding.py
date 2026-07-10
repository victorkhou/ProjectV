"""
Grid-based A* pathfinding module.

Pure Python with zero Evennia dependencies. Operates on a passability
callback so it can be tested in isolation and reused for any NPC type.
"""

from __future__ import annotations

import heapq
from typing import Callable


def find_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    is_passable: Callable[[int, int], bool],
    width: int,
    height: int,
    max_nodes: int = 500,
) -> list[tuple[int, int]]:
    """Compute an A* path from start to goal on a bounded grid.

    Args:
        start: (x, y) starting coordinate.
        goal: (x, y) goal coordinate.
        is_passable: Callback returning True if (x, y) is walkable.
        width: Planet grid width (0 <= x < width).
        height: Planet grid height (0 <= y < height).
        max_nodes: Maximum node expansions before giving up.

    Returns:
        Ordered list of (x, y) coordinates from start (exclusive) to
        goal (inclusive). Empty list if no path exists, start == goal,
        or node limit exceeded.
    """
    if start == goal:
        return []

    if not is_passable(goal[0], goal[1]):
        return []

    # 4-directional neighbors: N, S, E, W
    directions = ((0, 1), (0, -1), (1, 0), (-1, 0))

    def heuristic(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    # Priority queue entries: (f_score, counter, node)
    # counter breaks ties to keep heapq stable
    counter = 0
    open_set: list[tuple[int, int, tuple[int, int]]] = []
    heapq.heappush(open_set, (heuristic(start, goal), counter, start))

    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], int] = {start: 0}
    closed: set[tuple[int, int]] = set()
    expanded = 0

    while open_set:
        _, _, current = heapq.heappop(open_set)

        if current in closed:
            continue

        closed.add(current)
        expanded += 1

        if expanded > max_nodes:
            return []

        if current == goal:
            # Reconstruct path from start (exclusive) to goal (inclusive)
            path: list[tuple[int, int]] = []
            node = current
            while node != start:
                path.append(node)
                node = came_from[node]
            path.reverse()
            return path

        cx, cy = current
        for dx, dy in directions:
            nx, ny = cx + dx, cy + dy

            # Bounds check
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue

            neighbor = (nx, ny)

            if neighbor in closed:
                continue

            if not is_passable(nx, ny):
                continue

            tentative_g = g_score[current] + 1

            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f = tentative_g + heuristic(neighbor, goal)
                counter += 1
                heapq.heappush(open_set, (f, counter, neighbor))

    return []


def make_passability_checker(
    terrain_generator: "Any",
    data_registry: "Any",
    planet_room: "Any",
    width: int,
    height: int,
) -> Callable[[int, int], bool]:
    """Build an is_passable(x, y) callback for the Pathfinder.

    Checks:
    1. Coordinate within bounds (0 <= x < width, 0 <= y < height).
    2. TerrainDef.passable is True for the terrain at (x, y).
    3. No offline building occupies (x, y) — uses
       PlanetRoom.get_buildings_at(x, y) for O(1) lookup via the
       existing CoordinateIndex.

    Args:
        terrain_generator: TerrainGenerator instance for the planet.
        data_registry: DataRegistry with terrain definitions.
        planet_room: PlanetRoom providing get_buildings_at(x, y).
        width: Planet grid width.
        height: Planet grid height.

    Returns:
        A callable ``is_passable(x, y) -> bool``.
    """

    def is_passable(x: int, y: int) -> bool:
        # 1. Bounds check
        if x < 0 or x >= width or y < 0 or y >= height:
            return False

        # 2. Terrain passability
        terrain_type = terrain_generator.get_terrain(x, y)
        try:
            terrain_def = data_registry.get_terrain(terrain_type)
        except KeyError:
            return False
        if not terrain_def.passable:
            return False

        # 3. Offline building check
        buildings = planet_room.get_buildings_at(x, y)
        for building in buildings:
            if getattr(building, "is_offline", False):
                return False

        return True

    return is_passable


def compute_path_for_npc(
    npc: "Any",
    start: tuple[int, int],
    goal: tuple[int, int],
) -> list[tuple[int, int]]:
    """Compute a path from *start* to *goal* using the NPC's PlanetRoom context.

    Shared helper used by PatrolBehavior, DeliveryBehavior, and
    AgentSystem to avoid duplicating the "resolve game systems → build
    passability checker → call find_path" boilerplate.

    Falls back to a simple bounds-only checker when game systems are
    unavailable (e.g., test environments).

    Returns an empty list if no path exists or pathfinding is unavailable.
    """
    from world.constants import MAX_PATHFINDING_NODES

    room = getattr(npc, "location", None)
    width: int | None = None
    height: int | None = None

    is_passable = None
    if room is not None and hasattr(room, "_game_systems"):
        systems = room._game_systems
        if systems:
            terrain_generators = systems.get("_terrain_generators")
            registry = systems.get("registry")
            planet_registry = systems.get("planet_registry")
            planet_key = None
            if hasattr(room, "db"):
                planet_key = getattr(room.db, "planet", None)

            # Resolve grid dimensions from the PlanetRegistry, which owns the
            # CoordinateSpaceDef (width/height) for each planet. (The old code
            # called registry.get_coord_space(planet_def.coord_space) — neither
            # that DataRegistry method nor the PlanetDef.coord_space field exist,
            # so the AttributeError was silently swallowed and dimensions always
            # fell through to the 100x100 default, breaking A* bounds on any
            # larger planet.)
            if planet_registry is not None and planet_key:
                try:
                    space = planet_registry.get_space(planet_key)
                    width = space.width
                    height = space.height
                except (KeyError, AttributeError):
                    pass

            if width is None or height is None:
                # Fallback: try room attributes
                coord_space = getattr(room, "coordinate_space", None)
                if coord_space:
                    width = getattr(coord_space, "width", width)
                    height = getattr(coord_space, "height", height)
                else:
                    w = getattr(getattr(room, "db", None), "grid_width", None)
                    h = getattr(getattr(room, "db", None), "grid_height", None)
                    if w is not None:
                        width = int(w)
                    if h is not None:
                        height = int(h)

            tgen = (
                terrain_generators.get(planet_key)
                if terrain_generators and planet_key
                else None
            )
            if tgen and registry and width is not None and height is not None:
                is_passable = make_passability_checker(
                    tgen, registry, room, width, height,
                )

    # Final fallback dimensions for test environments
    if width is None:
        width = 100
    if height is None:
        height = 100

    if is_passable is None:
        def is_passable(x: int, y: int) -> bool:
            return 0 <= x < width and 0 <= y < height

    return find_path(start, goal, is_passable, width, height,
                     max_nodes=MAX_PATHFINDING_NODES)
