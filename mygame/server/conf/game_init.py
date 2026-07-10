"""
Server startup initialization for the RTS Combat Overworld.

Called from Evennia's at_server_start hook. Initializes all game
systems, wires event subscribers, and starts persistent scripts.

"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("evennia.server.game_init")

# Module-level dict so commands can look up systems
game_systems: dict = {}


def _holder_room_and_coords(holder: Any) -> tuple[Any, int | None, int | None]:
    """Resolve a drop holder's ``(room, x, y)`` for spawning a ground drop.

    Used by the resource-/supply-drop spawners wired into ``EquipmentSystem``
    at the composition root. *holder* is a player or a building; both expose an
    Evennia ``location`` (the room / PlanetRoom the drop lands in) and
    ``db.coord_x``/``db.coord_y`` (its tile). Returns ``(None, None, None)``
    when the holder is not placed on the map so the caller spills nothing rather
    than dropping at an undefined position.
    """
    from world.utils import get_coords

    room = getattr(holder, "location", None)
    coords = get_coords(holder)
    if room is None or coords is None:
        return None, None, None
    return room, coords[0], coords[1]


def initialize_game() -> dict:
    """Initialize all game systems and wire them together.

    This is the main entry point called from at_server_start().

    Returns:
        dict of system_name -> system instance.
    """
    global game_systems

    logger.info("Initializing RTS Combat Overworld game systems...")

    # ---------------------------------------------------------- #
    #  1. Data Registry — load all definitions
    # ---------------------------------------------------------- #
    from world.data_registry import DataRegistry

    registry = DataRegistry()
    try:
        registry.load_all()
        logger.info("DataRegistry loaded successfully.")
        # Build the shared level->XP threshold curve once at server start
        from world import progression
        progression.build_thresholds(registry.ranks)
        logger.info("Progression thresholds built.")
    except Exception:
        logger.exception("DataRegistry load failed — using empty registry.")
    # Register the process-wide singleton so owner-agnostic helpers
    # (world.progression, chat_system, agent_scripts, building capability
    # checks) can resolve the live registry via DataRegistry.get_instance()
    # without a threaded reference. Set AFTER load_all so a load failure never
    # installs a half-populated registry as the singleton — capability lookups
    # then fail closed against a definitively-unpopulated registry rather than a
    # confusingly-partial one, and a healthy load always installs a full one.
    DataRegistry.set_instance(registry)
    # Owner-agnostic helpers (world.utils, world.chat_system, world.progression)
    # resolve a hot-reload-safe DefinitionsProvider over this singleton on demand
    # via ``default_definitions_provider()`` — the single choke point for the
    # former DataRegistry.get_instance() reaches — so no provider instance needs
    # to be threaded through game_systems here.

    # ---------------------------------------------------------- #
    #  2. Event Bus singleton
    # ---------------------------------------------------------- #
    from world.event_bus import event_bus

    logger.info("EventBus initialized.")

    # ---------------------------------------------------------- #
    #  3. Initialize all game systems
    # ---------------------------------------------------------- #
    from world.systems.building_system import BuildingSystem
    from world.systems.combat_engine import CombatEngine
    from world.systems.rank_system import RankSystem
    from world.systems.resource_system import ResourceSystem
    from world.systems.powerup_system import PowerupSystem
    from world.systems.tech_system import TechLabSystem
    from world.systems.equipment_system import EquipmentSystem
    from world.systems.agent_system import AgentSystem
    from world.systems.guard_combat_system import GuardCombatSystem
    from world.systems.outpost_spawner import OutpostSpawnerSystem
    from world.systems.base_elimination import BaseEliminationHandler
    from world.systems.movement_system import MovementSystem
    from world.constants import MAX_PATHS_PER_TICK
    from world.chunking import WorldChunkManager
    from world.notification_system import NotificationSystem
    from world.chat_system import ChatSystem

    from world.adapters.evennia_building_repository import (
        EvenniaBuildingFactory,
        EvenniaMovingEntityRepository,
    )

    # The shared game-tick clock. ``_get_current_tick`` reads the live
    # GameTickScript's tick count on each call, so systems constructed here get
    # the real tick — without it they default to ``lambda: 0`` and their
    # tick-derived math (combat lockout expiry, powerup duration/cooldown, the
    # build-while-in-combat gate) freezes at 0.
    from world.combat_timer import _get_current_tick

    building_system = BuildingSystem(
        registry, event_bus, building_factory=EvenniaBuildingFactory(),
        current_tick_func=_get_current_tick,
    )
    combat_engine = CombatEngine(
        registry, event_bus, current_tick_func=_get_current_tick
    )
    rank_system = RankSystem(registry, event_bus)
    resource_system = ResourceSystem(registry, event_bus)
    powerup_system = PowerupSystem(
        registry, event_bus, current_tick_func=_get_current_tick
    )
    tech_system = TechLabSystem(registry, event_bus)
    from world.systems.regen_system import RegenSystem
    regen_system = RegenSystem(registry, event_bus)
    # Inject the live-GameItem production factory so Gear produced each tick is a
    # real, equippable Evennia object (not the framework-free dict placeholder
    # the use-case defaults to for tests). The ``typeclasses`` import lives here
    # at the composition root; ``world/systems`` stays framework-free. Supplies
    # are still routed into the Supply_Bag as counts by ``process_production``.
    from typeclasses.objects import create_game_item

    equipment_system = EquipmentSystem(
        registry,
        event_bus,
        create_item_func=lambda item_def, owner: create_game_item(owner, item_def),
    )
    # Inject the Evennia-backed agent repository + factory so AgentSystem's
    # roster/tick queries and NPC creation go through the adapter ports rather
    # than importing evennia in the system body.
    from world.adapters.evennia_agent_repository import (
        EvenniaAgentFactory,
        EvenniaAgentRepository,
    )

    agent_system = AgentSystem(
        registry,
        event_bus,
        agent_repository=EvenniaAgentRepository(),
        agent_factory=EvenniaAgentFactory(),
    )
    # Late-bind the agent XP-awarder into CombatEngine now that AgentSystem
    # exists, replacing its game_systems-global reach on agent kills.
    combat_engine.set_agent_xp_awarder(lambda: agent_system)
    # Route player combat/kill/base XP through the RankSystem so a kill recomputes
    # level/rank and fires LEVEL_CHANGED / RANK_* (a raw db.combat_xp write does
    # neither). Late-bound: RankSystem exists by now, but keep the callable form
    # consistent with the agent awarder.
    combat_engine.set_player_xp_awarder(lambda: rank_system)
    # Guard combat AI: guard/soldier NPCs acquire nearby non-owner players and
    # queue attacks through the CombatEngine each tick (before combat_resolution
    # so they land same-tick). Ownership-generic — defends player bases and NPC
    # outposts identically.
    guard_combat_system = GuardCombatSystem(
        registry, event_bus, combat_engine=combat_engine
    )
    # Line-of-sight: turrets and guards must not fire through their own Walls.
    # A shared predicate (blocked when a combat_barrier building lies between
    # shooter and target) is injected into both.
    from world.adapters.line_of_sight import make_sight_blocked
    _sight_blocked = make_sight_blocked(registry)
    combat_engine.set_sight_blocked_func(_sight_blocked)
    guard_combat_system.set_sight_blocked_func(_sight_blocked)
    # Inject the PowerupSystem into EquipmentSystem so ``use`` applies a
    # consumable buff through the real timed-effect machinery (correct entry
    # shape + tick-based expiry) rather than reaching into game_systems.
    equipment_system.set_powerup_system(powerup_system)
    # Inject the area-damage applier so ``EquipmentSystem.throw`` routes each
    # AoE victim through the CombatEngine damage pipeline (real armor reduction
    # + min-0 clamp) rather than reaching into game_systems. Zero-arg callable
    # returning the live CombatEngine.
    equipment_system.set_area_damage_applier(lambda: combat_engine)

    # Inject the resource-drop spawner so ``EquipmentSystem.add_resource_capped``
    # can spill the over-capacity remainder of a *holder-pool* inflow (a
    # player's Spend_Pool or a Storage_Building's stored pool) back to a ground
    # ``ResourceDrop`` at the holder's coordinates, so no resource is ever
    # destroyed (D9, Req 16.8). This is the injected ResourceSystem<->
    # EquipmentSystem inflow-choke relationship (task 9.2/11.1): the callable
    # ``(holder, resource, amount)`` delegates to the existing
    # ``ResourceSystem._spawn_resource_drop`` mechanism rather than
    # ``EquipmentSystem`` reaching into ``game_systems`` or importing
    # ``typeclasses`` — ``world/systems`` stays framework-free.
    def _spawn_resource_drop_for(holder: Any, resource: str, amount: int) -> Any:
        room, cx, cy = _holder_room_and_coords(holder)
        if room is None:
            return None
        return resource_system._spawn_resource_drop(room, resource, amount, x=cx, y=cy)

    equipment_system.set_resource_drop_spawner(_spawn_resource_drop_for)

    # Inject the supply-drop spawner so ``EquipmentSystem.add_supply_drop`` can
    # spill over-stack / over-weight Supply leftovers to a ground pickup at the
    # player's coordinates, so supplies are never destroyed (D9). Supplies are
    # counted items held in a pool distinct from ``db.resources``, so the spill
    # is a ``GameItem`` supply drop (``typeclasses.objects.spawn_supply_drop``),
    # NOT a ``ResourceDrop`` — a ResourceDrop's ``at_get`` would mis-file the
    # units into the resource pool. The ``typeclasses`` import lives here at the
    # composition root; ``world/systems`` holds only the injected callable
    # ``(player, item_key, count)``.
    def _spawn_supply_drop_for(player: Any, item_key: str, count: int) -> Any:
        room, cx, cy = _holder_room_and_coords(player)
        if room is None:
            return None
        from typeclasses.objects import spawn_supply_drop

        return spawn_supply_drop(room, item_key, count, x=cx, y=cy)

    equipment_system.set_supply_drop_spawner(_spawn_supply_drop_for)

    movement_system = MovementSystem(
        max_paths_per_tick=MAX_PATHS_PER_TICK,
        moving_entity_repository=EvenniaMovingEntityRepository(),
    )
    # Restore in-memory training cache from DB (survives server restarts)
    try:
        n = agent_system.restore_training_cache()
        if n:
            logger.info("Restored %d training building(s) from DB.", n)
    except Exception:
        pass
    chunking = WorldChunkManager(
        chunk_size=getattr(registry.balance, "chunk_size", 10)
        if hasattr(registry, "balance") and registry.balance
        else 10
    )
    chat_system = ChatSystem()

    logger.info("All game systems initialized.")

    # ---------------------------------------------------------- #
    #  3b. Initialize Procedural Coordinate World systems
    # ---------------------------------------------------------- #
    planet_registry = None
    fog_system = None
    procedural_map_renderer = None
    terrain_generators = None
    map_data_provider = None
    planet_rooms: dict[str, Any] = {}

    try:
        import os

        from world.coordinate.planet_registry import PlanetRegistry
        from world.coordinate.terrain_generator import TerrainGenerator
        from world.coordinate.fog_of_war import FogOfWarSystem
        from world.coordinate.procedural_map_renderer import ProceduralMapRenderer

        # 1. PlanetRegistry — load planets.yaml
        planet_registry = PlanetRegistry()
        planets_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "definitions", "planets.yaml"
        )
        planets_path = os.path.normpath(planets_path)
        planet_registry.load_from_yaml(planets_path)
        logger.info("PlanetRegistry loaded.")

        # 2. TerrainGenerator per planet
        terrain_generators: dict[str, TerrainGenerator] = {}
        for planet_key in planet_registry.list_planets():
            space_def = planet_registry.get_space(planet_key)
            terrain_generators[planet_key] = TerrainGenerator(space_def, data_registry=registry)
        logger.info(
            "TerrainGenerators created for %d planet(s).", len(terrain_generators)
        )

        # Wire the terrain provider into BuildingSystem now that the per-planet
        # generators exist (they are built after the systems), replacing the
        # game_systems-global fallback used for Extractor placement validation.
        from world.adapters.game_systems_terrain_provider import (
            GameSystemsTerrainProvider,
        )

        building_system.set_terrain_provider(
            GameSystemsTerrainProvider(terrain_generators)
        )

        # 3. Balance config for fog system
        balance = getattr(registry, "balance", None)

        # 4. FogOfWarSystem
        if balance:
            fog_system = FogOfWarSystem(balance)
        else:
            from world.definitions import BalanceConfig
            fog_system = FogOfWarSystem(BalanceConfig())
        logger.info("FogOfWarSystem initialized.")

        # 5. ProceduralMapRenderer
        procedural_map_renderer = ProceduralMapRenderer(
            fog_system=fog_system,
            terrain_generators=terrain_generators,
            data_registry=registry,
        )
        logger.info("ProceduralMapRenderer initialized.")

        # 5b. MapDataProvider (graphical webclient)
        from world.coordinate.map_data_provider import MapDataProvider
        map_data_provider = MapDataProvider(
            fog_system=fog_system,
            terrain_generators=terrain_generators,
        )
        logger.info("MapDataProvider initialized.")

        # 6. Shared PlanetRooms — one room per planet
        planet_rooms: dict[str, Any] = {}
        try:
            from typeclasses.rooms import PlanetRoom
            from evennia.utils.search import search_tag

            for planet_key in planet_registry.list_planets():
                tag_key = f"planet_room_{planet_key}"
                existing = search_tag(tag_key, category="planet_room")
                if existing:
                    planet_rooms[planet_key] = existing[0]
                    logger.info("Found existing PlanetRoom for %s.", planet_key)
                else:
                    from evennia.utils.create import create_object
                    room = create_object(
                        typeclass="typeclasses.rooms.PlanetRoom",
                        key=f"Overworld ({planet_key})",
                    )
                    room.attributes.add("planet", planet_key)
                    room.tags.add(tag_key, category="planet_room")
                    planet_rooms[planet_key] = room
                    logger.info("Created PlanetRoom for %s.", planet_key)
        except Exception:
            logger.exception("PlanetRoom creation failed — using fallback.")

    except Exception:
        logger.exception(
            "Procedural Coordinate World initialization failed — "
            "coordinate systems will be unavailable."
        )

    # ---------------------------------------------------------- #
    #  4. Wire event subscribers
    # ---------------------------------------------------------- #
    # NotificationSystem auto-subscribes in __init__. Inject the Evennia
    # notifier explicitly (rather than relying on the lazy default) so the
    # transport is wired at the composition root like the other adapters.
    from world.adapters.evennia_notifier import EvenniaNotifier

    notification_system = NotificationSystem(event_bus, notifier=EvenniaNotifier())

    # NotificationPresenter — subscribes to PLAYER_NOTIFICATION events emitted
    # by domain systems, formats them via its kind→string table, and delivers
    # via the per-player Evennia adapter. The presenter is the single owner of
    # all per-player notification strings — domain code never composes text.
    from world.adapters.evennia_player_notifier import EvenniaPlayerNotifier
    from world.presenters.notification_presenter import NotificationPresenter

    notification_presenter = NotificationPresenter(event_bus, player_notifier=EvenniaPlayerNotifier())

    # Combat timer: start/reset on COMBAT_ACTION events
    from world.combat_timer import subscribe_combat_timer
    subscribe_combat_timer(event_bus)

    # Agent demotion/promotion: reserve or restore agents on rank change
    from world.event_bus import RANK_DEMOTED, RANK_PROMOTED
    event_bus.subscribe(RANK_DEMOTED, lambda **kw: agent_system.handle_demotion(kw.get("player"), kw.get("new_agent_cap", 2)))
    event_bus.subscribe(RANK_PROMOTED, lambda **kw: agent_system.handle_promotion(kw.get("player"), kw.get("new_agent_cap", 2)))

    # Owner level change: re-evaluate gated abilities on every owned Agent
    from world.event_bus import LEVEL_CHANGED
    event_bus.subscribe(LEVEL_CHANGED, lambda **kw: agent_system.on_owner_level_changed(kw.get("player"), kw.get("old_level"), kw.get("new_level")))

    logger.info("Event subscribers wired.")

    # ---------------------------------------------------------- #
    #  4b. NPC bases: spawner + base-elimination handler (PvE)
    # ---------------------------------------------------------- #
    from world.adapters.evennia_npc_base_factory import EvenniaNpcBaseFactory
    from world.adapters.game_systems_terrain_provider import (
        GameSystemsTerrainProvider,
    )
    # ``_get_current_tick`` already imported above where the tick-sensitive
    # systems are constructed.

    # Enumerate every building AND guard NPC owned by a sentinel, for the
    # mass-delete on base elimination. Buildings come from get_buildings();
    # enemy guards are tagged (player_<id>, agent_owner) by the NPC factory.
    def _owned_entities_for(sentinel: Any) -> list:
        entities: list[Any] = []
        try:
            entities.extend(sentinel.get_buildings())
        except Exception:
            pass
        try:
            from evennia.utils.search import search_object_by_tag
            owner_id = getattr(sentinel, "id", None)
            if owner_id is not None:
                entities.extend(
                    search_object_by_tag(f"player_{owner_id}", category="agent_owner")
                )
        except Exception:
            pass
        return entities

    base_elimination = BaseEliminationHandler(
        registry,
        event_bus,
        owned_entities_provider=_owned_entities_for,
        loot_drop_func=(
            lambda room, resource, amount, x, y:
            resource_system.spawn_resource_drop(room, resource, amount, x=x, y=y)
        ),
    )
    outpost_spawner = OutpostSpawnerSystem(
        registry,
        event_bus,
        npc_base_factory=EvenniaNpcBaseFactory(),
        building_factory=EvenniaBuildingFactory(),
        terrain_provider=(
            GameSystemsTerrainProvider(terrain_generators)
            if terrain_generators else None
        ),
        planet_rooms_provider=lambda: game_systems.get("planet_rooms", {}),
        planet_registry=planet_registry,
        current_tick_func=_get_current_tick,
    )

    # ---------------------------------------------------------- #
    #  5. Initialize ChatSystem — ensure Global channel
    # ---------------------------------------------------------- #
    try:
        chat_system.ensure_global_channel()
        logger.info("ChatSystem: Global channel ensured.")
    except Exception:
        logger.exception("ChatSystem: Could not ensure Global channel.")

    # ---------------------------------------------------------- #
    #  6. Metrics (optional)
    # ---------------------------------------------------------- #
    metrics = None
    try:
        from world.metrics import MetricsCollector
        metrics = MetricsCollector()
        logger.info("MetricsCollector initialized.")
    except Exception:
        logger.info("MetricsCollector not available — skipping.")

    # ---------------------------------------------------------- #
    #  7. Populate game_systems dict
    # ---------------------------------------------------------- #
    game_systems.update({
        "registry": registry,
        "event_bus": event_bus,
        "building_system": building_system,
        "combat_engine": combat_engine,
        "rank_system": rank_system,
        "resource_system": resource_system,
        "powerup_system": powerup_system,
        "tech_system": tech_system,
        "regen_system": regen_system,
        "equipment_system": equipment_system,
        "agent_system": agent_system,
        "guard_combat_system": guard_combat_system,
        "outpost_spawner": outpost_spawner,
        "base_elimination": base_elimination,
        "movement_system": movement_system,
        "chunking": chunking,
        "chat_system": chat_system,
        "notification_system": notification_system,
        "metrics": metrics,
        "planet_registry": planet_registry,
        "fog_system": fog_system,
        "procedural_map_renderer": procedural_map_renderer,
        "map_data_provider": map_data_provider,
        "_terrain_generators": terrain_generators,
        "planet_rooms": planet_rooms,
    })

    # ---------------------------------------------------------- #
    #  7b. Spawn initial NPC bases per planet (idempotent-ish)
    # ---------------------------------------------------------- #
    # Rebuild the spawner's in-memory state from surviving sentinels + persisted
    # pending respawns (Req 7.6), THEN seed only the planets that have no base
    # yet. This is per-planet idempotent: a restart re-seeds a freshly-added
    # planet without stacking duplicate bases on planets that already have them,
    # and a base cleared just before the restart still respawns (its pending
    # entry was persisted on the PlanetRoom).
    try:
        from evennia.utils.search import search_object_by_tag
        from world.utils import get_obj_attr

        existing = list(search_object_by_tag("sentinel", category="npc_role") or [])
        # Restore active-base separation state + reload persisted respawns.
        outpost_spawner.rebuild_from_world(existing)

        if existing:
            logger.info("Found %d existing NPC base(s).", len(existing))
        if registry.base_templates:
            seeded_planets = {
                get_obj_attr(s, "base_planet") for s in existing
            }
            for planet_key in (planet_rooms or {}).keys():
                if planet_key in seeded_planets:
                    continue  # this planet already has NPC bases — don't re-seed
                try:
                    outpost_spawner.spawn_initial(planet_key)
                except Exception:
                    logger.exception(
                        "Initial NPC-base spawn failed for planet %s.", planet_key
                    )
    except Exception:
        logger.exception("Initial NPC-base spawn skipped (search unavailable).")

    # ---------------------------------------------------------- #
    #  8. Start GameTickScript and AutoSaveScript
    # ---------------------------------------------------------- #
    _start_scripts(game_systems)

    logger.info("RTS Combat Overworld initialization complete.")
    return game_systems


def _start_scripts(systems: dict) -> None:
    """Start or retrieve the GameTickScript and AutoSaveScript.

    Uses Evennia's create_script / search to find existing persistent
    scripts or create new ones.
    """
    try:
        from evennia import create_script
        from evennia.utils.search import search_script

        # GameTickScript
        existing = search_script("game_tick")
        if existing:
            tick_script = existing[0]
            # Force-restart the timer to ensure it's actually ticking.
            # After repeated errors, Evennia's internal Twisted task can
            # die while db_is_active remains True.
            tick_script.start(interval=1)
            logger.error("GameTickScript found and force-restarted (interval=1).")
        else:
            tick_script = create_script(
                "typeclasses.scripts.GameTickScript",
                key="game_tick",
                persistent=True,
            )
            logger.error("Created new GameTickScript.")

        # Wire systems into the tick script
        if tick_script:
            tick_script.ndb.systems = systems

        # AutoSaveScript
        existing = search_script("auto_save")
        if existing:
            save_script = existing[0]
            logger.info("Found existing AutoSaveScript.")
        else:
            save_script = create_script(
                "typeclasses.scripts.AutoSaveScript",
                key="auto_save",
                persistent=True,
            )
            logger.info("Created new AutoSaveScript.")

    except ImportError:
        logger.info("Evennia script API not available — skipping script start.")
    except Exception:
        logger.exception("Error starting game scripts.")
