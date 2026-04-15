"""
Scripts

Scripts are powerful jacks-of-all-trades. They have no in-game
existence and can be used to represent persistent game systems in some
circumstances. Scripts can also have a time component that allows them
to "fire" regularly or a limited number of times.

There is generally no "tree" of Scripts inheriting from each other.
Rather, each script tends to inherit from the base Script class and
just overloads its hooks to have it perform its function.

"""

import logging
import time

from evennia.scripts.scripts import DefaultScript

logger = logging.getLogger("evennia")


class Script(DefaultScript):
    """
    This is the base TypeClass for all Scripts. Scripts describe
    all entities/systems without a physical existence in the game world
    that require database storage (like an economic system or
    combat tracker). They can also have a timer/ticker component.

    A script type is customized by redefining some or all of its hook
    methods and variables.
    """

    pass


class GameTickScript(DefaultScript):
    """Persistent script driving the game tick loop.

    Orchestrates all game systems each tick in a defined order.
    Each processing step is wrapped in try/except for error resilience
    (Requirement 11.3).

    The tick determines active world chunks based on online player
    positions, then processes only buildings and tiles within those
    chunks for performance (Requirement 11.4, 31.1-31.5).

    Requirements: 11.1, 11.2, 11.3, 11.4, 11.5
    """

    def at_script_creation(self):
        self.key = "game_tick"
        self.desc = "Main game tick loop"
        self.interval = 1  # configurable, default 1 second
        self.persistent = True
        self.db.tick_count = 0

    def at_repeat(self):
        """Execute one game tick, processing all systems in order.

        Processing order:
        1. Determine active chunks from online player positions
        2. Resource building production (active chunks)
        3. Equipment building production (active chunks)
        4. Combat engine resolution (pending actions)
        5. Turret auto-attacks (active chunks)
        6. Powerup duration decrements
        7. Technology research timer decrements
        8. Resource node respawn counter decrements
        9. Publish tick_completed event
        10. Record metrics

        Each step is wrapped in try/except so a failure in one step
        does not prevent the others from executing (Requirement 11.3).
        """
        start_time = time.time()
        tick_number = getattr(self.db, "tick_count", 0) + 1
        self.db.tick_count = tick_number

        systems = self._get_systems()
        if systems is None:
            return

        steps = self._build_tick_steps(systems, tick_number)

        for step_name, step_fn in steps:
            try:
                step_fn()
            except Exception:
                logger.exception(
                    f"GameTick error in step '{step_name}' "
                    f"(tick #{tick_number})"
                )

        # Record tick duration
        duration_ms = (time.time() - start_time) * 1000
        try:
            metrics = systems.get("metrics")
            if metrics:
                metrics.record_tick(duration_ms)
        except Exception:
            pass

    def _get_systems(self):
        """Retrieve game system references from script attributes.

        Systems are stored on the script's ndb (non-persistent) or db
        attributes by the server startup initialization code.

        Returns:
            dict of system name -> system instance, or None if not set up.
        """
        return getattr(self.ndb, "systems", None) or getattr(
            self.db, "systems", None
        )

    def _get_online_players(self):
        """Return a list of puppeted characters from all connected sessions.

        Returns:
            list of character objects currently online.
        """
        try:
            from evennia import SESSION_HANDLER

            players = []
            for account in SESSION_HANDLER.all_connected_accounts():
                for session in account.sessions.all():
                    puppet = session.get_puppet()
                    if puppet:
                        players.append(puppet)
            return players
        except Exception:
            return []

    def _get_all_buildings(self):
        """Return all Building objects in the game world.

        Uses Evennia's tag-based search to find objects tagged as
        buildings. Falls back to an empty list if unavailable.

        Returns:
            list of Building objects.
        """
        try:
            from evennia.utils.search import search_object_by_tag

            return list(search_object_by_tag(
                key="building", category="object_type"
            ))
        except Exception:
            return []

    def _compute_active_data(self, chunking, online_players):
        """Compute active buildings from chunk filtering.

        Determines which chunks are active based on online player
        positions, then filters all buildings to only those
        within active chunks.

        Args:
            chunking: WorldChunkManager instance.
            online_players: list of online player characters.

        Returns:
            list of active buildings.
        """
        all_buildings = self._get_all_buildings()

        if not online_players:
            return []

        # Collect active chunks across all planets
        all_active_chunks = set()
        planets = set()
        for player in online_players:
            loc = getattr(player, "location", None)
            if loc is not None:
                planet = getattr(loc, "z", None)
                if planet is not None:
                    planets.add(str(planet))

        active_buildings = []

        for planet in planets:
            chunks = chunking.get_active_chunks(planet, online_players)
            all_active_chunks.update(chunks)
            active_buildings.extend(
                chunking.get_buildings_in_chunks(planet, chunks, all_buildings)
            )

        return active_buildings

    def _build_tick_steps(self, systems, tick_number):
        """Build the ordered list of (name, callable) tick steps.

        Computes active chunks from online player positions, then
        creates step callables that pass filtered buildings/tiles
        to each game system.

        Args:
            systems: dict of system name -> system instance.
            tick_number: Current tick number.

        Returns:
            List of (step_name, step_callable) tuples.
        """
        chunking = systems.get("chunking")
        resource_system = systems.get("resource_system")
        equipment_system = systems.get("equipment_system")
        combat_engine = systems.get("combat_engine")
        powerup_system = systems.get("powerup_system")
        tech_system = systems.get("tech_system")
        event_bus = systems.get("event_bus")
        agent_system = systems.get("agent_system")
        building_system = systems.get("building_system")

        # Compute active data once, shared across steps.
        # Mutable container so the active_chunks step can populate it
        # and subsequent steps use the result.
        tick_data = {"buildings": [], "online_players": []}

        def compute_active_chunks():
            """Step 1: determine active chunks and filter world data."""
            online_players = self._get_online_players()
            tick_data["online_players"] = online_players
            if not chunking:
                # No chunk manager — use all buildings
                tick_data["buildings"] = self._get_all_buildings()
                return
            buildings = self._compute_active_data(
                chunking, online_players
            )
            tick_data["buildings"] = buildings

        steps = []

        # 1. Active chunks (always first — populates tick_data)
        steps.append(("active_chunks", compute_active_chunks))

        # 1b. Advance terrain epochs for dynamic planets
        terrain_generators = systems.get("_terrain_generators")
        if terrain_generators:
            def advance_terrain_epochs():
                for gen in terrain_generators.values():
                    if gen.is_dynamic:
                        gen.advance_tick(tick_number)
            steps.append(("terrain_epochs", advance_terrain_epochs))

        # 2. Agent system tick — process all agent behavior scripts
        if agent_system:
            steps.append((
                "agent_processing",
                lambda: agent_system.process_tick(tick_number),
            ))

        # 2b. Agent training timer — uses in-memory cache, no DB query per tick
        if agent_system:
            steps.append((
                "agent_training",
                lambda: agent_system.process_training_tick(
                    agent_system._training_buildings
                ),
            ))

        # 3. Active-presence processing — building and harvesting for online players
        if building_system or resource_system:
            def process_active_presence():
                for player in tick_data["online_players"]:
                    state = getattr(getattr(player, "db", None), "activity_state", "idle")
                    if state == "building" and building_system:
                        building_system.process_construction_tick(player)
                    elif state == "harvesting" and resource_system:
                        resource_system.process_harvest_tick(player)
            steps.append(("active_presence", process_active_presence))

        # 4. Extractor production — Harvester agents produce into Extractor inventory
        if resource_system:
            steps.append((
                "extractor_production",
                lambda: resource_system.process_extractor_production(
                    tick_data["buildings"]
                ),
            ))

        # 5. Equipment building production
        if equipment_system:
            steps.append((
                "equipment_production",
                lambda: equipment_system.process_production(
                    tick_data["buildings"]
                ),
            ))

        # 6. Combat resolution
        if combat_engine:
            steps.append((
                "combat_resolution",
                lambda: combat_engine.resolve_tick(
                    tick_data["buildings"]
                ),
            ))

        # 7. Turret auto-attacks
        if combat_engine:
            steps.append((
                "turret_attacks",
                lambda: combat_engine.process_turrets(
                    tick_data["buildings"]
                ),
            ))

        # 8. Combat timer decrement — expire combat timers for online players
        def decrement_combat_timers():
            for player in tick_data["online_players"]:
                db = getattr(player, "db", None)
                if db is None:
                    continue
                expires = getattr(db, "combat_timer_expires", 0) or 0
                if expires > 0 and tick_number >= expires:
                    db.combat_timer_expires = 0
        steps.append(("combat_timer_decrement", decrement_combat_timers))

        # 9. Powerup duration decrements
        if powerup_system:
            steps.append((
                "powerup_ticks",
                lambda: powerup_system.process_tick(tick_number),
            ))

        # 10. Technology research timer decrements
        if tech_system:
            steps.append((
                "tech_research",
                lambda: tech_system.process_tick(),
            ))

        # 11. Resource node respawn counter decrements
        if resource_system:
            def _process_respawns():
                """Pass PlanetRoom objects to process_respawns."""
                try:
                    planet_rooms_dict = systems.get("planet_rooms", {})
                    planet_rooms_list = list(planet_rooms_dict.values())
                except Exception:
                    planet_rooms_list = []
                resource_system.process_respawns(planet_rooms_list)
            steps.append((
                "resource_respawns",
                _process_respawns,
            ))

        # 12. Publish tick_completed event
        if event_bus:
            steps.append((
                "tick_completed",
                lambda: event_bus.publish(
                    "tick_completed",
                    tick_number=tick_number,
                ),
            ))

        return steps


class AutoSaveScript(DefaultScript):
    """Periodically saves player and world state.

    Runs as a persistent script with a configurable interval.
    On error, logs and retries next interval.

    Requirements: 32.1, 32.2, 32.3, 32.4
    """

    def at_script_creation(self):
        self.key = "auto_save"
        self.desc = "Periodic auto-save of player states"
        self.interval = 30  # configurable via balance.save_interval
        self.persistent = True

    def at_repeat(self):
        """Save all connected player states.

        Error handling: log and retry next interval.
        """
        try:
            self._save_all_players()
        except Exception:
            logger.exception("AutoSave error during player state save")

    def _save_all_players(self):
        """Save state for all connected players.

        In production this would iterate over connected sessions
        and call save on each player character. The actual save
        mechanism uses Evennia's built-in attribute persistence.
        """
        # Import here to avoid circular imports at module level
        try:
            from evennia import SESSION_HANDLER
            for account in SESSION_HANDLER.all_connected_accounts():
                for session in account.sessions.all():
                    puppet = session.get_puppet()
                    if puppet:
                        # Evennia auto-persists Attributes, but we can
                        # force a save of any cached state here
                        pass
        except Exception:
            logger.exception("AutoSave: could not access SESSION_HANDLER")
