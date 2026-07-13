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


# ------------------------------------------------------------------ #
#  Tick step ordering (declared as data)
# ------------------------------------------------------------------ #
#
# Canonical per-tick execution order. Each entry is (step_name, rationale).
# The order is SIGNIFICANT — several steps depend on an earlier one having run
# this tick, and those dependencies are documented here rather than being
# implicit in append-order inside ``_build_tick_steps``:
#
#   - ``active_chunks`` MUST run first: it populates the shared ``tick_data``
#     (online players + active buildings) that nearly every later step reads.
#   - ``npc_movement`` / ``agent_processing`` run before combat so agents are at
#     their resolved positions when attacks resolve.
#   - ``guard_combat`` runs BEFORE ``combat_resolution`` so guard-queued attacks
#     resolve in the SAME tick (intentional asymmetry with turrets, which fire
#     AFTER resolution and land next tick — see GuardCombatSystem's docstring).
#   - ``combat_resolution`` runs before ``turret_attacks`` so turrets fire at
#     the post-resolution world state.
#   - ``powerup_ticks`` runs AFTER ``combat_resolution`` so a powerup lasts
#     through the tick it would expire on (its buff still applied to this
#     tick's combat).
#   - ``tick_completed`` is last: it announces the tick is fully processed.
#
# A step whose backing system is absent this run is simply not registered by
# ``_build_tick_steps`` and is skipped here, so the same declared order works in
# minimal/test setups. To add or reorder a step, edit THIS tuple (and register
# its builder) — do not rely on code position.
TICK_STEP_ORDER = (
    ("active_chunks", "First: populates shared tick_data (players + buildings)."),
    ("terrain_epochs", "Advance dynamic-planet terrain before tiles are read."),
    ("npc_movement", "Move NPCs before combat so positions are current."),
    ("agent_processing", "Run agent behavior (incl. harvester production) pre-combat."),
    ("agent_training", "Decrement training timers."),
    ("active_presence", "Online-player construction/harvest progress."),
    ("equipment_production", "Equipment buildings emit items."),
    ("targeting_upkeep", "Advance/validate ranged lock-ons before shots resolve."),
    ("guard_combat", "Guards/soldiers acquire targets and queue attacks."),
    ("combat_resolution", "Resolve queued attacks before turrets/expiry."),
    ("turret_attacks", "Turrets fire at the post-resolution world state."),
    ("bomb_fuse", "Tick down live bombs; detonate + AoE those whose fuse hits 0."),
    ("combat_timer_decrement", "Expire combat lockouts."),
    ("hp_regen", "Passive HP regen for players/agents (after combat)."),
    ("powerup_ticks", "Expire powerups after this tick's combat resolved."),
    ("tech_research", "Decrement research timers."),
    ("resource_respawns", "Decrement depleted-node respawn counters."),
    ("outpost_respawn", "Respawn cleared NPC bases whose cooldown elapsed."),
    ("tick_completed", "Last: announce the tick is fully processed."),
)


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
    Each processing step is wrapped in try/except for error resilience.

    The tick determines active world chunks based on online player
    positions, then processes only buildings and tiles within those
    chunks for performance.

    """

    def at_script_creation(self):
        self.key = "game_tick"
        self.desc = "Main game tick loop"
        self.interval = 1  # configurable, default 1 second
        self.persistent = True
        self.db.tick_count = 0

    def at_repeat(self):
        """Execute one game tick, processing all systems in order.

        The per-tick step sequence — and the rationale for each step's position —
        is the module-level ``TICK_STEP_ORDER`` constant (the single source of
        truth); ``_build_tick_steps`` registers the available steps and emits them
        in that order. Do NOT re-list the order here — a second copy drifts (this
        docstring once listed 10 steps while ``TICK_STEP_ORDER`` had 17).

        Each step is wrapped in try/except so a failure in one step
        does not prevent the others from executing.
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

        Uses Evennia's tag-based search to find objects tagged as buildings.
        The result is cached on the script and reused across ticks: the tag
        search runs only when the building-index generation advances (a
        building was created or destroyed — see ``world.building_index``),
        instead of issuing a full DB query every second. Falls back to an empty
        list if the search is unavailable.

        Returns:
            list of Building objects.
        """
        try:
            from world import building_index
            generation = building_index.generation()
        except Exception:  # noqa: BLE001 - if the counter is unavailable, don't cache
            generation = None

        # Serve the cache while the world's building set is unchanged.
        if (
            generation is not None
            and getattr(self, "_buildings_cache_gen", None) == generation
            and getattr(self, "_buildings_cache", None) is not None
        ):
            return self._buildings_cache

        try:
            from evennia.utils.search import search_object_by_tag

            buildings = list(search_object_by_tag(
                key="building", category="object_type"
            ))
        except Exception:
            return []

        # Cache against the generation we observed (skip caching if unknown).
        if generation is not None:
            self._buildings_cache = buildings
            self._buildings_cache_gen = generation
        return buildings

    def _get_all_agents(self, agent_system):
        """Return the full agent roster, cached against the agent-index gen.

        Mirrors :meth:`_get_all_buildings`: ``agent_system.get_all_agents()``
        runs a DB tag-search, and the roster only changes when an agent NPC is
        created or deleted (see ``world.agent_index``). Reuse the cached list
        while the generation is unchanged so passive systems (HP regen) don't
        re-scan every interval. Falls back to a live (uncached) query if the
        counter is unavailable.

        Args:
            agent_system: the AgentSystem exposing ``get_all_agents()``.

        Returns:
            list of agent NPC objects (empty on any failure).
        """
        if agent_system is None:
            return []
        try:
            from world import agent_index
            generation = agent_index.generation()
        except Exception:  # noqa: BLE001 - if the counter is unavailable, don't cache
            generation = None

        if (
            generation is not None
            and getattr(self, "_agents_cache_gen", None) == generation
            and getattr(self, "_agents_cache", None) is not None
        ):
            return self._agents_cache

        try:
            agents = list(agent_system.get_all_agents())
        except Exception:
            return []

        if generation is not None:
            self._agents_cache = agents
            self._agents_cache_gen = generation
        return agents

    def _get_all_enemies(self, agent_system):
        """Return the full enemy-NPC roster, cached against the agent-index gen.

        The guard-combat sweep needs NPC-base guards (``npc_type="enemy"``),
        which are NOT in the ``get_all_agents`` roster. Enemy NPCs are created
        and deleted through the same ``NPC`` lifecycle that bumps the
        agent-index generation, so this reuses that counter for cache
        invalidation exactly like :meth:`_get_all_agents`.

        Args:
            agent_system: the AgentSystem exposing ``get_all_enemies()``.

        Returns:
            list of enemy NPC objects (empty on any failure / missing system).
        """
        if agent_system is None or not hasattr(agent_system, "get_all_enemies"):
            return []
        try:
            from world import agent_index
            generation = agent_index.generation()
        except Exception:  # noqa: BLE001 - if the counter is unavailable, don't cache
            generation = None

        if (
            generation is not None
            and getattr(self, "_enemies_cache_gen", None) == generation
            and getattr(self, "_enemies_cache", None) is not None
        ):
            return self._enemies_cache

        try:
            enemies = list(agent_system.get_all_enemies())
        except Exception:
            return []

        if generation is not None:
            self._enemies_cache = enemies
            self._enemies_cache_gen = generation
        return enemies

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

        # Collect active chunks across all planets. The planet lives on the
        # ENTITY (``db.coord_planet``) — the coordinate model stores position on
        # the object, not on the room — with the PlanetRoom's ``planet_name`` as
        # a fallback. (The old ``loc.z`` read the wrong model: a PlanetRoom has
        # no ``z``/``x``/``y``, so it always yielded None and left this set empty,
        # feeding an empty active-building list to every downstream tick step
        # whenever a player was online.)
        all_active_chunks = set()
        planets = set()
        for player in online_players:
            planet = getattr(getattr(player, "db", None), "coord_planet", None)
            if not planet:
                loc = getattr(player, "location", None)
                planet = getattr(loc, "planet_name", None) if loc else None
            if planet:
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
        """Build the tick steps, emitted in the canonical ``TICK_STEP_ORDER``.

        Each available system registers a named step callable into a dict; the
        method then emits those steps in the order declared by the module-level
        ``TICK_STEP_ORDER`` (the single source of truth for ordering and its
        rationale). A step whose backing system is absent is simply never
        registered and is skipped. This keeps execution order declarative —
        reordering means editing ``TICK_STEP_ORDER``, not moving code.

        Args:
            systems: dict of system name -> system instance.
            tick_number: Current tick number.

        Returns:
            List of (step_name, step_callable) tuples, in ``TICK_STEP_ORDER``.
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
        movement_system = systems.get("movement_system")
        terrain_generators = systems.get("_terrain_generators")

        # Compute active data once, shared across steps.
        # Mutable container so the active_chunks step can populate it
        # and subsequent steps use the result.
        tick_data = {"buildings": [], "online_players": []}

        # Registry of name -> callable. Only steps whose backing system is
        # present get registered; ordering is applied afterward.
        registered = {}

        def compute_active_chunks():
            """Determine active chunks and filter world data (populates tick_data)."""
            online_players = self._get_online_players()
            tick_data["online_players"] = online_players
            if not chunking:
                # No chunk manager — use all buildings
                tick_data["buildings"] = self._get_all_buildings()
                return
            tick_data["buildings"] = self._compute_active_data(
                chunking, online_players
            )
        registered["active_chunks"] = compute_active_chunks

        if terrain_generators:
            def advance_terrain_epochs():
                for gen in terrain_generators.values():
                    if gen.is_dynamic:
                        gen.advance_tick(tick_number)
            registered["terrain_epochs"] = advance_terrain_epochs

        if movement_system:
            def process_npc_movement():
                movement_system.reset_tick()
                movement_system.process_movement(tick_number)
                movement_system.process_pathfinding()
            registered["npc_movement"] = process_npc_movement

        if agent_system:
            registered["agent_processing"] = (
                # Feed the cached agent roster (invalidated only on NPC
                # create/delete) so process_tick doesn't re-issue a full
                # find_all_agents DB tag-scan every second.
                lambda: agent_system.process_tick(
                    tick_number, self._get_all_agents(agent_system)
                )
            )
            # Uses in-memory cache, no DB query per tick.
            registered["agent_training"] = (
                lambda: agent_system.process_training_tick(
                    agent_system._training_buildings
                )
            )

        # NOTE: Harvester-agent production is driven by HarvesterScript
        # (one script per agent, run in the agent_processing step), per the
        # agent-ai spec. The old
        # process_extractor_production tick step was a second, faster driver
        # for the same (extractor, agent) pairs and produced resources twice
        # per tick — it has been removed. process_extractor_production remains
        # for direct unit/integration test use but is no longer in the loop.

        if building_system or resource_system:
            def process_active_presence():
                for player in tick_data["online_players"]:
                    state = getattr(getattr(player, "db", None), "activity_state", "idle")
                    if state == "building" and building_system:
                        building_system.process_construction_tick(player)
                    elif state == "harvesting" and resource_system:
                        resource_system.process_harvest_tick(player)
            registered["active_presence"] = process_active_presence

        if equipment_system:
            registered["equipment_production"] = (
                lambda: equipment_system.process_production(tick_data["buildings"])
            )

        registry = systems.get("registry")

        def _active_hq_owner_ids():
            """Owner ids with a live HQ this tick — one in-memory pass over the
            active buildings, shared by guard AI and turrets so the "no HQ =
            inert" gate costs zero DB queries per entity (see
            ``world.utils.active_hq_owner_ids``)."""
            from world.utils import active_hq_owner_ids
            return active_hq_owner_ids(tick_data["buildings"], provider=registry)

        targeting_system = systems.get("targeting_system")
        if targeting_system:
            def process_targeting_upkeep():
                # Only online players hold ranged locks; advance/validate each.
                targeting_system.process_tick(
                    tick_number, tick_data["online_players"]
                )
            registered["targeting_upkeep"] = process_targeting_upkeep

        guard_combat_system = systems.get("guard_combat_system")
        if guard_combat_system:
            def process_guard_combat():
                # Feed BOTH rosters so guards fight back on player bases AND NPC
                # outposts: player-assigned guards live in the npc_type="agent"
                # roster; NPC-base guards live in the npc_type="enemy" roster.
                # Both are cached against the agent-index generation (a DB scan
                # only when an NPC is created/deleted). GuardCombatSystem is
                # ownership-generic and filters by role internally, so a
                # non-guard agent in the list is simply skipped.
                guards = self._get_all_agents(agent_system)
                guards = guards + self._get_all_enemies(agent_system)
                guard_combat_system.process_tick(
                    tick_number, guards,
                    active_owner_ids=_active_hq_owner_ids(),
                )
            registered["guard_combat"] = process_guard_combat

        if combat_engine:
            registered["combat_resolution"] = (
                lambda: combat_engine.resolve_tick(tick_data["buildings"])
            )
            registered["turret_attacks"] = (
                lambda: combat_engine.process_turrets(
                    tick_data["buildings"],
                    active_owner_ids=_active_hq_owner_ids(),
                )
            )

        bomb_system = systems.get("bomb_system")
        if bomb_system:
            # Tick down every live bomb each second and detonate those that hit
            # 0. BombSystem tracks its own live-bomb list (a mine in an abandoned
            # area still counts down), so this needs no tick_data — pass the tick
            # number for parity with the other timed systems.
            registered["bomb_fuse"] = (
                lambda: bomb_system.process_tick(tick_number)
            )

        def decrement_combat_timers():
            for player in tick_data["online_players"]:
                db = getattr(player, "db", None)
                if db is None:
                    continue
                expires = getattr(db, "combat_timer_expires", 0) or 0
                if expires > 0 and tick_number >= expires:
                    db.combat_timer_expires = 0
        registered["combat_timer_decrement"] = decrement_combat_timers

        regen_system = systems.get("regen_system")
        if regen_system:
            def process_hp_regen():
                # Skip on off-interval ticks BEFORE the agent-roster scan — the
                # gate is cheap, enumerating every agent (a DB tag search) is
                # not, and process_tick would only discard the result anyway.
                if not regen_system.should_regen_this_tick(tick_number):
                    return
                # Players and agents only — buildings do NOT passively heal.
                # The agent roster is cached against the agent-index generation
                # (see _get_all_agents), so this is a DB scan only when an agent
                # was created/deleted since the last interval tick.
                entities = list(tick_data["online_players"])
                entities.extend(self._get_all_agents(agent_system))
                regen_system.process_tick(entities, tick_number)
            registered["hp_regen"] = process_hp_regen

        if powerup_system:
            registered["powerup_ticks"] = (
                lambda: powerup_system.process_tick(tick_number)
            )

        if tech_system:
            registered["tech_research"] = lambda: tech_system.process_tick()

        if resource_system:
            def _process_respawns():
                """Pass PlanetRoom objects to process_respawns."""
                try:
                    planet_rooms_dict = systems.get("planet_rooms", {})
                    planet_rooms_list = list(planet_rooms_dict.values())
                except Exception:
                    planet_rooms_list = []
                resource_system.process_respawns(planet_rooms_list)
            registered["resource_respawns"] = _process_respawns

        outpost_spawner = systems.get("outpost_spawner")
        if outpost_spawner:
            registered["outpost_respawn"] = (
                lambda: outpost_spawner.process_respawns(tick_number)
            )

        if event_bus:
            registered["tick_completed"] = (
                lambda: event_bus.publish("tick_completed", tick_number=tick_number)
            )

        # Emit in the canonical declared order, skipping unregistered steps.
        return [
            (name, registered[name])
            for name, _rationale in TICK_STEP_ORDER
            if name in registered
        ]


class AutoSaveScript(DefaultScript):
    """Periodically saves player and world state.

    Runs as a persistent script with a configurable interval.
    On error, logs and retries next interval.

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
