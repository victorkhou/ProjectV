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

    # Hot-reload-safe read-model view over the live registry. Injected into
    # systems/helpers that need definitions + balance without owning a registry
    # reference, in place of the DataRegistry.get_instance() singleton reach.
    from world.adapters.registry_definitions_provider import RegistryDefinitionsProvider

    definitions_provider = RegistryDefinitionsProvider(registry)

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
    from world.systems.movement_system import MovementSystem
    from world.constants import MAX_PATHS_PER_TICK
    from world.chunking import WorldChunkManager
    from world.notification_system import NotificationSystem
    from world.chat_system import ChatSystem

    building_system = BuildingSystem(registry, event_bus)
    combat_engine = CombatEngine(registry, event_bus)
    rank_system = RankSystem(registry, event_bus)
    resource_system = ResourceSystem(registry, event_bus)
    powerup_system = PowerupSystem(registry, event_bus)
    tech_system = TechLabSystem(registry, event_bus)
    equipment_system = EquipmentSystem(registry, event_bus)
    agent_system = AgentSystem(registry, event_bus)
    movement_system = MovementSystem(max_paths_per_tick=MAX_PATHS_PER_TICK)
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
    # NotificationSystem auto-subscribes in __init__
    notification_system = NotificationSystem(event_bus)

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
        "definitions_provider": definitions_provider,
        "event_bus": event_bus,
        "building_system": building_system,
        "combat_engine": combat_engine,
        "rank_system": rank_system,
        "resource_system": resource_system,
        "powerup_system": powerup_system,
        "tech_system": tech_system,
        "equipment_system": equipment_system,
        "agent_system": agent_system,
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
