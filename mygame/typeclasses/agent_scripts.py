"""
Agent behavior scripts — Evennia Scripts attached to NPC agent objects.

Each script class corresponds to an agent role and implements
``at_repeat()`` with the role's per-tick logic.  Scripts have
``interval = 0`` so they are driven by the GameTickScript rather
than self-timed.

Requirements: 9.1, 10.1, 10.5, 10.6, 11.1, 11.3, 12.1, 12.3
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from evennia.scripts.scripts import DefaultScript
except ImportError:
    # Fallback for test environments where Evennia is not available.
    class DefaultScript:  # type: ignore[no-redef]
        """Minimal stub so the module can be imported outside Evennia."""

        key = ""
        desc = ""
        interval = 0
        persistent = True
        obj: Any = None

        def at_script_creation(self) -> None: ...
        def at_repeat(self) -> None: ...

logger = logging.getLogger("mygame.agent_scripts")


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Read an Evennia attribute from *obj*, with fallback."""
    from world.utils import get_obj_attr
    return get_obj_attr(obj, key, default)


def _set_attr(obj: Any, key: str, value: Any) -> None:
    """Write an Evennia attribute on *obj*, with fallback."""
    from world.utils import set_obj_attr
    set_obj_attr(obj, key, value)


# ------------------------------------------------------------------ #
#  HarvesterScript  (Req 9.1, 9.2, 9.3, 9.4)
# ------------------------------------------------------------------ #

class HarvesterScript(DefaultScript):
    """Produces resources each tick when attached to an NPC assigned to an Extractor.

    The NPC's ``role_target`` must point to an Extractor building.
    Production is scaled by the Extractor's level using the formula::

        base_rate × (1 + 0.25 × (level - 1))

    Produced resources are added to the Extractor's local inventory,
    respecting its storage capacity.
    """

    def at_script_creation(self) -> None:
        self.key = "harvester_script"
        self.desc = "Harvester agent production loop"
        self.interval = 0  # driven by GameTickScript
        self.persistent = True

    def at_repeat(self) -> None:
        npc = self.obj
        if npc is None:
            return

        # Don't produce while in transit to the building
        queue = getattr(getattr(npc, "db", None), "movement_queue", None)
        if queue:
            return

        # Agent must not be incapacitated
        if getattr(getattr(npc, "db", None), "incapacitated", False):
            return

        building = getattr(getattr(npc, "db", None), "role_target", None)
        if building is None:
            return

        # Determine building type — must be an Extractor (resource category)
        building_type = _get_attr(building, "building_type")
        if building_type != "EX":
            return

        # Determine resource type from the terrain tile the Extractor sits on
        resource_type = self._resolve_resource_type(building)
        if not resource_type:
            return

        # Production cooldown — same rate as manual harvesting at an Extractor.
        # Manual: HARVEST_YIELD_PER_ACTION * EXTRACTOR_HARVEST_MULTIPLIER
        # every HARVEST_COOLDOWN_TICKS ticks, scaled by building level.
        from world.constants import (
            HARVEST_COOLDOWN_TICKS,
            HARVEST_YIELD_PER_ACTION,
            EXTRACTOR_HARVEST_MULTIPLIER,
            EXTRACTOR_LEVEL_BONUS,
        )
        cooldown = getattr(getattr(npc, "db", None), "_harvest_tick_counter", 0) or 0
        cooldown += 1
        if cooldown < HARVEST_COOLDOWN_TICKS:
            npc.db._harvest_tick_counter = cooldown
            npc.db.activity_status = f"Harvesting {resource_type}"
            return
        npc.db._harvest_tick_counter = 0

        level = _get_attr(building, "building_level", 1) or 1
        production = HARVEST_YIELD_PER_ACTION * EXTRACTOR_HARVEST_MULTIPLIER
        production = int(production * (1 + EXTRACTOR_LEVEL_BONUS * (level - 1)))
        production = max(1, production)

        # Drop resources at building coordinates in PlanetRoom
        from world.systems.resource_system import ResourceSystem
        bx = getattr(getattr(building, "db", None), "coord_x", None)
        by = getattr(getattr(building, "db", None), "coord_y", None)
        drop_location = getattr(building, "location", building)
        if bx is not None and by is not None:
            ResourceSystem._spawn_resource_drop(
                drop_location, resource_type, production, x=int(bx), y=int(by)
            )
        else:
            # Legacy fallback: no coordinates on building
            ResourceSystem._spawn_resource_drop(drop_location, resource_type, production)

        npc.db.activity_status = f"Harvesting {resource_type}"
        logger.debug(
            "Harvester on %s produced %d %s (level %d)",
            building, production, resource_type, level,
        )

    # -- internal helpers ---------------------------------------------- #

    @staticmethod
    def _resolve_resource_type(building: Any) -> str | None:
        """Determine the resource type for an Extractor.

        Checks the building's stored ``resource_type`` attribute first,
        then queries TerrainGenerator using the building's coordinates,
        then falls back to reading the terrain tile's resource node.
        """
        # Explicit attribute on the building
        rt = _get_attr(building, "resource_type")
        if rt:
            return rt

        # Try TerrainGenerator using building's coordinates
        bx = getattr(getattr(building, "db", None), "coord_x", None)
        by = getattr(getattr(building, "db", None), "coord_y", None)
        if bx is not None and by is not None:
            try:
                # Get planet from the building's PlanetRoom location
                loc = getattr(building, "location", None)
                planet = None
                if loc is not None:
                    planet = getattr(loc, "planet_name", None)
                    if planet is None or planet == "unknown":
                        planet = getattr(getattr(loc, "db", None), "planet", None)
                if planet:
                    from server.conf.game_init import game_systems
                    generators = game_systems.get("_terrain_generators", {})
                    gen = generators.get(planet)
                    if gen:
                        _terrain_type, resource_type = gen.get_terrain_and_resource(
                            int(bx), int(by)
                        )
                        if resource_type:
                            return resource_type
            except (ImportError, AttributeError):
                pass

        # Legacy fallback: read from the terrain tile the building sits on
        tile = getattr(building, "location", None)
        if tile is None:
            return None

        # Try resource_node_data dict
        node = _get_attr(tile, "resource_node_data")
        if isinstance(node, dict):
            return node.get("resource_type")

        # Try direct terrain attribute
        return _get_attr(tile, "resource_type")

    @staticmethod
    def _get_base_rate() -> int:
        """Return the base harvest rate from the DataRegistry balance config.

        Falls back to a sensible default (5) if the registry is
        unavailable.
        """
        try:
            from world.data_registry import DataRegistry

            registry = DataRegistry.get_instance()
            return registry.balance.gather_amount
        except Exception:
            return 5


# ------------------------------------------------------------------ #
#  EngineerScript  (Req 10.1, 10.5, 10.6)
# ------------------------------------------------------------------ #

class EngineerScript(DefaultScript):
    """Progresses construction and research timers autonomously.

    The NPC's ``role_target`` must point to a building that has an
    active ``construction_total`` (construction/upgrade) or a
    ``research_total`` (Lab research).  Each tick, the script
    increments the corresponding progress counter.
    """

    def at_script_creation(self) -> None:
        self.key = "engineer_script"
        self.desc = "Engineer agent construction/research loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        npc = self.obj
        if npc is None:
            return

        if getattr(getattr(npc, "db", None), "incapacitated", False):
            return

        building = getattr(getattr(npc, "db", None), "role_target", None)
        if building is None:
            return

        # Try construction progress first
        construction_total = _get_attr(building, "construction_total", 0) or 0
        if construction_total > 0:
            progress = _get_attr(building, "construction_progress", 0) or 0
            if progress < construction_total:
                progress += 1
                _set_attr(building, "construction_progress", progress)
                if progress >= construction_total:
                    self._complete_construction(building)
                return

        # Try research progress (Lab)
        research_total = _get_attr(building, "research_total", 0) or 0
        if research_total > 0:
            research_progress = _get_attr(building, "research_progress", 0) or 0
            if research_progress < research_total:
                research_progress += 1
                _set_attr(building, "research_progress", research_progress)
                if research_progress >= research_total:
                    self._complete_research(building)
                return

    @staticmethod
    def _complete_construction(building: Any) -> None:
        """Finalise a completed construction."""
        logger.debug("Engineer completed construction on %s", building)
        # Mark construction as done (progress == total already set)
        # The BuildingSystem's process_agent_construction handles the
        # full completion flow; this is a safety net for direct calls.

    @staticmethod
    def _complete_research(building: Any) -> None:
        """Finalise a completed research project."""
        logger.debug("Engineer completed research on %s", building)
        # Placeholder — full research completion logic will be wired
        # when the technology system is integrated.


# GuardScript and ScoutScript removed — replaced by PatrolBehavior (Req 3.1)


# ------------------------------------------------------------------ #
#  PatrolBehavior  (Req 3.2, 3.3, 3.4, 3.5, 3.6, 10.1, 10.2)
# ------------------------------------------------------------------ #

class PatrolBehavior(DefaultScript):
    """Cycles a guard/scout through patrol waypoints.

    Replaces the placeholder GuardScript and ScoutScript.
    Uses the polling pattern: at_repeat checks if the NPC's movement
    queue is empty and triggers the next waypoint path.
    """

    def at_script_creation(self) -> None:
        self.key = "patrol_behavior"
        self.desc = "Patrol agent waypoint cycling loop"
        self.interval = 0  # driven by GameTickScript
        self.persistent = True

    def at_repeat(self) -> None:
        """If movement queue is empty, path to next waypoint."""
        npc = self.obj
        if npc is None:
            return
        if getattr(getattr(npc, "db", None), "incapacitated", False):
            return
        queue = getattr(npc.db, "movement_queue", None)
        if queue:
            return  # still moving
        self._advance_to_next_waypoint(npc)

    def _advance_to_next_waypoint(self, npc: Any) -> None:
        """Cycle patrol_waypoint_index and request path to next waypoint."""
        patrol_route = getattr(npc.db, "patrol_route", None)
        if not patrol_route:
            return

        route_len = len(patrol_route)
        if route_len == 0:
            return

        npc_x = getattr(npc.db, "coord_x", 0)
        npc_y = getattr(npc.db, "coord_y", 0)
        if npc_x is None:
            npc_x = 0
        if npc_y is None:
            npc_y = 0
        npc_x, npc_y = int(npc_x), int(npc_y)

        target_index = getattr(npc.db, "patrol_waypoint_index", 0) or 0

        # Try each waypoint starting from target_index; skip unreachable
        for _attempt in range(route_len):
            target_index = target_index % route_len
            waypoint = patrol_route[target_index]
            wx, wy = int(waypoint[0]), int(waypoint[1])

            # Already at this waypoint — advance to next
            if npc_x == wx and npc_y == wy:
                target_index = (target_index + 1) % route_len
                npc.db.patrol_waypoint_index = target_index
                npc.db.activity_status = (
                    f"Patrolling waypoint {target_index + 1}/{route_len}"
                )
                continue

            # Compute path to this waypoint
            path = self._compute_path(npc, (npc_x, npc_y), (wx, wy))
            if path:
                # Set movement queue; advance index so next cycle targets
                # the following waypoint after arrival.
                if hasattr(npc, "set_movement_queue"):
                    npc.set_movement_queue(path)
                else:
                    npc.db.movement_queue = [[x, y] for x, y in path]
                next_index = (target_index + 1) % route_len
                npc.db.patrol_waypoint_index = next_index
                npc.db.activity_status = (
                    f"Patrolling waypoint {target_index + 1}/{route_len}"
                )
                return
            else:
                # Waypoint unreachable — skip to next
                logger.debug(
                    "PatrolBehavior: waypoint %d (%d,%d) unreachable for %s, skipping",
                    target_index, wx, wy, npc,
                )
                target_index = (target_index + 1) % route_len
                continue

        # All waypoints unreachable — stay put, retry next tick
        npc.db.activity_status = "Patrol blocked — retrying"
        logger.debug("PatrolBehavior: all waypoints unreachable for %s", npc)

    @staticmethod
    def _compute_path(
        npc: Any,
        start: tuple[int, int],
        goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """Compute a path from start to goal using the Pathfinder.

        Delegates to ``compute_path_for_npc`` in the pathfinding module,
        which builds a passability checker from the NPC's PlanetRoom context.
        """
        from world.pathfinding import compute_path_for_npc
        return compute_path_for_npc(npc, start, goal)


# ------------------------------------------------------------------ #
#  DeliveryBehavior  (Req 4.1–4.8, 7.1–7.4, 8.4, 8.5, 9.1–9.5, 10.1, 10.2)
# ------------------------------------------------------------------ #

class DeliveryBehavior(DefaultScript):
    """Autonomous Extractor → Storage delivery loop for harvesters.

    Coexists with HarvesterScript on the same NPC. HarvesterScript
    handles production (gated by delivery_state), DeliveryBehavior
    handles pickup/transit/deposit.

    Uses the polling pattern: at_repeat checks delivery_state and
    movement_queue emptiness to drive the FSM.

    State machine: idle → picking_up → delivering → returning → idle
    """

    def at_script_creation(self) -> None:
        self.key = "delivery_behavior"
        self.desc = "Harvester agent delivery loop"
        self.interval = 0  # driven by GameTickScript
        self.persistent = True

    def at_repeat(self) -> None:
        """State machine: check delivery_state and movement_queue."""
        npc = self.obj
        if npc is None:
            return

        # Skip if incapacitated — drop carried resources (Req 9.5)
        if getattr(getattr(npc, "db", None), "incapacitated", False):
            self._handle_incapacitated(npc)
            return

        # Still moving — wait for arrival
        queue = getattr(npc.db, "movement_queue", None)
        if queue:
            return

        # Queue empty — act based on delivery_state
        state = getattr(npc.db, "delivery_state", "idle")
        if state == "idle":
            self._try_pick_up(npc)
        elif state == "picking_up":
            self._start_delivery(npc)
        elif state == "delivering":
            self._deposit_and_return(npc)
        elif state == "returning":
            self._arrived_at_extractor(npc)

    # ------------------------------------------------------------------ #
    #  FSM transitions
    # ------------------------------------------------------------------ #

    def _try_pick_up(self, npc: Any) -> None:
        """Check for ResourceDrops at Extractor coords, load up to carry_capacity.

        Transitions: idle → picking_up (if resources found), stays idle otherwise.
        """
        building = getattr(npc.db, "role_target", None)
        if building is None:
            return

        bx = getattr(getattr(building, "db", None), "coord_x", None)
        by = getattr(getattr(building, "db", None), "coord_y", None)
        if bx is None or by is None:
            return

        bx, by = int(bx), int(by)

        # Find ResourceDrops at the Extractor's coordinates
        room = getattr(npc, "location", None)
        if room is None or not hasattr(room, "get_objects_at"):
            return

        drops = room.get_objects_at(bx, by, type_tag="resource_drop")
        if not drops:
            return  # No resources to pick up — stay idle

        # Load resources up to carry_capacity (Req 9.2)
        from world.constants import DEFAULT_CARRY_CAPACITY
        capacity = getattr(npc.db, "carry_capacity", None)
        if capacity is None:
            capacity = DEFAULT_CARRY_CAPACITY
        carried = getattr(npc.db, "carried_resources", None) or {}
        carried = dict(carried)  # ensure mutable copy
        total_carried = sum(carried.values())
        remaining_capacity = max(0, capacity - total_carried)

        if remaining_capacity <= 0:
            # Already full — go straight to delivery
            npc.db.delivery_state = "picking_up"
            npc.db.carried_resources = carried
            npc.db.activity_status = "Loaded — selecting delivery target"
            return

        for drop in list(drops):
            if remaining_capacity <= 0:
                break
            rtype = getattr(getattr(drop, "db", None), "resource_type", None)
            amount = getattr(getattr(drop, "db", None), "amount", 0) or 0
            if not rtype or amount <= 0:
                continue

            take = min(amount, remaining_capacity)
            carried[rtype] = carried.get(rtype, 0) + take
            remaining_amount = amount - take
            remaining_capacity -= take

            # Update or zero out the drop
            if remaining_amount > 0:
                drop.db.amount = remaining_amount
            else:
                drop.db.amount = 0
                # Try to delete the empty drop
                if hasattr(drop, "delete"):
                    try:
                        drop.delete()
                    except Exception:
                        pass

        npc.db.carried_resources = carried
        npc.db.delivery_state = "picking_up"

        total_str = ", ".join(f"{v} {k}" for k, v in carried.items())
        npc.db.activity_status = f"Picked up {total_str}"

    def _start_delivery(self, npc: Any) -> None:
        """Select nearest Storage_Building, path to it, set delivering state.

        Transitions: picking_up → delivering (if storage found and path exists),
                     picking_up → idle (if no storage building).
        """
        carried = getattr(npc.db, "carried_resources", None) or {}
        if not carried or sum(carried.values()) <= 0:
            # Nothing to deliver — go back to idle
            npc.db.delivery_state = "idle"
            npc.db.activity_status = "Idle"
            return

        target = self.select_delivery_target(npc)
        if target is None:
            # No storage building — stay idle (Req 4.6)
            npc.db.delivery_state = "idle"
            npc.db.activity_status = "No storage building — waiting"
            return

        npc.db.delivery_target = target

        # Compute path to target
        npc_x = int(getattr(npc.db, "coord_x", 0) or 0)
        npc_y = int(getattr(npc.db, "coord_y", 0) or 0)
        tx = int(getattr(getattr(target, "db", None), "coord_x", 0) or 0)
        ty = int(getattr(getattr(target, "db", None), "coord_y", 0) or 0)

        path = PatrolBehavior._compute_path(npc, (npc_x, npc_y), (tx, ty))
        if not path:
            # Path blocked — retry next tick (Req 4.7)
            npc.db.activity_status = "Delivery path blocked — retrying"
            return

        # Set movement queue and state
        if hasattr(npc, "set_movement_queue"):
            npc.set_movement_queue(path)
        else:
            npc.db.movement_queue = [[x, y] for x, y in path]

        npc.db.delivery_state = "delivering"

        # Laden speed (Req 8.4)
        from world.constants import HARVESTER_LADEN_DELAY
        npc.db.movement_delay = HARVESTER_LADEN_DELAY

        total_str = ", ".join(f"{v} {k}" for k, v in carried.items())
        dist = len(path)
        npc.db.activity_status = f"Delivering {total_str} ({dist} tiles)"

    def _deposit_and_return(self, npc: Any) -> None:
        """Transfer carried_resources to owner's resource pool, path back to Extractor.

        Transitions: delivering → returning
        """
        # Deposit resources into owner's resource pool (Req 4.4, 9.4)
        self.deposit_resources(npc)

        # Path back to Extractor
        building = getattr(npc.db, "role_target", None)
        if building is None:
            npc.db.delivery_state = "idle"
            npc.db.activity_status = "Idle"
            return

        bx = getattr(getattr(building, "db", None), "coord_x", None)
        by = getattr(getattr(building, "db", None), "coord_y", None)
        if bx is None or by is None:
            npc.db.delivery_state = "idle"
            npc.db.activity_status = "Idle"
            return

        bx, by = int(bx), int(by)
        npc_x = int(getattr(npc.db, "coord_x", 0) or 0)
        npc_y = int(getattr(npc.db, "coord_y", 0) or 0)

        path = PatrolBehavior._compute_path(npc, (npc_x, npc_y), (bx, by))
        if not path:
            # Path blocked — retry next tick (Req 4.7)
            npc.db.activity_status = "Return path blocked — retrying"
            return

        if hasattr(npc, "set_movement_queue"):
            npc.set_movement_queue(path)
        else:
            npc.db.movement_queue = [[x, y] for x, y in path]

        npc.db.delivery_state = "returning"
        npc.db.delivery_target = None

        # Empty speed (Req 8.5)
        from world.constants import HARVESTER_EMPTY_DELAY
        npc.db.movement_delay = HARVESTER_EMPTY_DELAY

        npc.db.activity_status = f"Returning to Extractor ({len(path)} tiles)"

    def _arrived_at_extractor(self, npc: Any) -> None:
        """Arrived back at Extractor, transition to idle.

        Transitions: returning → idle
        """
        from world.constants import HARVESTER_EMPTY_DELAY
        npc.db.delivery_state = "idle"
        npc.db.movement_delay = HARVESTER_EMPTY_DELAY
        npc.db.activity_status = "Idle"

    # ------------------------------------------------------------------ #
    #  Resource operations
    # ------------------------------------------------------------------ #

    @staticmethod
    def deposit_resources(npc: Any) -> None:
        """Transfer carried_resources to owner's resource pool (Req 4.4, 9.4)."""
        carried = getattr(npc.db, "carried_resources", None) or {}
        if not carried:
            return

        owner = getattr(npc.db, "owner", None)
        if owner is not None and hasattr(owner, "add_resource"):
            for rtype, amount in carried.items():
                if amount > 0:
                    owner.add_resource(rtype, amount)

        npc.db.carried_resources = {}

    @staticmethod
    def select_delivery_target(npc: Any) -> Any | None:
        """Find nearest Vault/HQ owned by same player (Req 7.1, 7.2).

        Prefers nearest by Manhattan distance. On tie, prefers Vault (VT) over HQ.
        """
        owner = getattr(npc.db, "owner", None)
        if owner is None:
            return None

        building = getattr(npc.db, "role_target", None)
        if building is None:
            return None

        # Use Extractor coordinates as the reference point
        bx = getattr(getattr(building, "db", None), "coord_x", None)
        by = getattr(getattr(building, "db", None), "coord_y", None)
        if bx is None or by is None:
            return None
        bx, by = int(bx), int(by)

        # Find all storage buildings owned by the same player
        room = getattr(npc, "location", None)
        if room is None:
            return None

        candidates = []

        # Search through all buildings in the room
        # Use search_object_by_tag for buildings, then filter by owner and type
        try:
            from evennia.utils.search import search_object_by_tag
            all_buildings = list(search_object_by_tag(
                key="building", category="object_type"
            ))
        except Exception:
            all_buildings = []

        # Fallback: if search_object_by_tag fails or returns nothing,
        # try iterating room contents
        if not all_buildings and hasattr(room, "contents"):
            all_buildings = [
                obj for obj in getattr(room, "contents", [])
                if hasattr(obj, "tags") and getattr(obj.tags, "get", lambda *a, **kw: False)(
                    "building", category="object_type"
                )
            ]

        for bld in all_buildings:
            bld_type = _get_attr(bld, "building_type")
            if bld_type not in ("VT", "HQ"):
                continue

            bld_owner = _get_attr(bld, "owner")
            if bld_owner is None:
                continue

            # Compare owners by identity or id
            if bld_owner is not owner:
                # Try comparing by id as fallback
                owner_id = getattr(owner, "id", None)
                bld_owner_id = getattr(bld_owner, "id", None)
                if owner_id is None or bld_owner_id is None or owner_id != bld_owner_id:
                    continue

            cx = getattr(getattr(bld, "db", None), "coord_x", None)
            cy = getattr(getattr(bld, "db", None), "coord_y", None)
            if cx is None or cy is None:
                continue

            cx, cy = int(cx), int(cy)
            dist = abs(cx - bx) + abs(cy - by)

            # Sort key: (distance, type_priority) where VT=0, HQ=1
            type_priority = 0 if bld_type == "VT" else 1
            candidates.append((dist, type_priority, bld))

        if not candidates:
            return None

        # Sort by distance first, then by type priority (VT preferred on tie)
        candidates.sort(key=lambda c: (c[0], c[1]))
        return candidates[0][2]

    # ------------------------------------------------------------------ #
    #  Edge case handlers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _handle_incapacitated(npc: Any) -> None:
        """Drop carried resources at current coords when incapacitated (Req 9.5)."""
        carried = getattr(npc.db, "carried_resources", None) or {}
        if not carried:
            return

        room = getattr(npc, "location", None)
        npc_x = getattr(npc.db, "coord_x", None)
        npc_y = getattr(npc.db, "coord_y", None)

        if room is not None and npc_x is not None and npc_y is not None:
            from world.systems.resource_system import ResourceSystem
            for rtype, amount in carried.items():
                if amount > 0:
                    ResourceSystem._spawn_resource_drop(
                        room, rtype, amount,
                        x=int(npc_x), y=int(npc_y),
                    )

        npc.db.carried_resources = {}
        npc.db.delivery_state = "idle"
        npc.db.activity_status = "Incapacitated — dropped resources"


# ------------------------------------------------------------------ #
#  SoldierScript  (Req 11.1)
# ------------------------------------------------------------------ #

class SoldierScript(DefaultScript):
    """Participates in army combat calculations.

    Placeholder — army combat logic will be implemented with the
    CombatEngine expansion.
    """

    def at_script_creation(self) -> None:
        self.key = "soldier_script"
        self.desc = "Soldier agent combat loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        # Placeholder: participate in army combat calculations.
        pass


# ------------------------------------------------------------------ #
#  MedicScript  (Req 11.3)
# ------------------------------------------------------------------ #

class MedicScript(DefaultScript):
    """Heals soldiers after combat and reduces respawn time at Medbay.

    Placeholder — healing and respawn reduction will be implemented
    with the CombatEngine and Medbay integration.
    """

    def at_script_creation(self) -> None:
        self.key = "medic_script"
        self.desc = "Medic agent healing loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        # Placeholder: heal soldiers after combat, reduce respawn time
        # at Medbay.
        pass


# ------------------------------------------------------------------ #
#  Script class lookup
# ------------------------------------------------------------------ #

#: Maps role name → Script class (or list of Script classes) for use by
#: AgentSystem when attaching behavior scripts to NPC agents.
ROLE_SCRIPT_MAP: dict[str, type | list[type]] = {
    "harvester": HarvesterScript,  # production only; resources stay at Extractor
    "engineer": EngineerScript,
    "guard": PatrolBehavior,      # replaces GuardScript
    "scout": PatrolBehavior,      # replaces ScoutScript
    "soldier": SoldierScript,
    "medic": MedicScript,
}
