"""
Player game commands for the RTS Combat Overworld.

Each command parses arguments from self.args, delegates to the
appropriate game system, and sends results to the player via
self.caller.msg().

Requirements: 1.6, 1.7, 1.10, 2.3, 2.5, 3.3, 5.3, 6.2, 6.3, 6.4,
              6.8, 6.17, 8.2, 9.2, 13.3, 13.5, 13.8, 16.1, 16.2,
              16.3, 16.4, 16.5
"""

from __future__ import annotations

from evennia.commands.command import Command as BaseCommand
from world.utils import (
    get_system as _get_system,
    get_game_systems,
    ensure_coords,
    get_building_info,
    get_building_attr,
    get_closed_exits,
    is_exit_closed,
    is_owner,
    is_admin,
    format_dm_message,
)


def _send_map_update(caller):
    """Send structured map data to the webclient via OOB.

    The webclient's map_renderer plugin listens for 'map_update'
    messages and renders them on the Canvas. This is a no-op for
    telnet clients (they ignore unknown OOB commands).
    """
    provider = _get_system(caller, "map_data_provider")
    if provider is None:
        return
    try:
        buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
        data = provider.get_map_data(caller, buildings)
        # Add discovered count
        fog = _get_system(caller, "fog_system")
        if fog:
            data["discovered_count"] = len(fog.get_discovered_tile_set(caller))
        # Add current terrain info for the webclient header
        try:
            x = getattr(caller.db, "coord_x", None)
            y = getattr(caller.db, "coord_y", None)
            planet = getattr(caller.db, "coord_planet", None)
            if x is not None and y is not None and planet:
                terrain_generators = get_game_systems().get("_terrain_generators", {})
                gen = terrain_generators.get(planet)
                if gen:
                    tt, res = gen.get_terrain_and_resource(int(x), int(y))
                    data["player"]["terrain"] = tt
                    if res:
                        data["player"]["resource"] = res
        except Exception:
            pass
        caller.msg(map_update=data)
    except Exception:
        import logging
        logging.getLogger("evennia.commands").exception("Map update failed")


def _send_ascii_map(caller, map_text):
    """Send ASCII map text tagged with cls=ascii-map.

    The webclient hides elements with class 'ascii-map' when the
    graphical map is active, avoiding duplicate display. Telnet
    clients see it normally.
    """
    caller.msg(text=(map_text, {"cls": "ascii-map"}))


def _render_and_send_map(caller):
    """Render the full ASCII map with header and send to the caller.

    Also sends the webclient graphical map update. Used by both
    CmdLook (no args) and CmdMap.
    """
    renderer = _get_system(caller, "procedural_map_renderer")
    if renderer is None:
        return

    planet = getattr(caller.db, "coord_planet", None)
    if not planet:
        _, _, planet = ensure_coords(caller)
    if not planet:
        return

    buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []

    try:
        map_str = renderer.render(caller, buildings)
    except Exception:
        import logging
        logging.getLogger("evennia.commands").exception("Map render failed")
        return

    if map_str:
        x = getattr(caller.db, "coord_x", "?")
        y = getattr(caller.db, "coord_y", "?")

        terrain_info = ""
        try:
            if x != "?" and y != "?":
                terrain_generators = get_game_systems().get("_terrain_generators", {})
                gen = terrain_generators.get(planet)
                if gen:
                    terrain_type = gen.get_terrain(int(x), int(y))
                    terrain_info = f" | {terrain_type}"
                    _, resource = gen.get_terrain_and_resource(int(x), int(y))
                    if resource:
                        terrain_info += f" ({resource})"
        except Exception:
            pass

        fog_system = _get_system(caller, "fog_system")
        disc_count = 0
        if fog_system:
            disc_count = len(fog_system.get_discovered_tile_set(caller))
        _send_ascii_map(
            caller,
            f"|wMap — ({x}, {y}) on {planet}{terrain_info} | "
            f"{disc_count} discovered|n\n{map_str}",
        )

    _send_map_update(caller)


class GameCommand(BaseCommand):
    """Base class for all game commands with prefix matching.

    Typing any unambiguous prefix of a command name works:
    'sco' → score, 'inv' → inventory, 'eq' → equipment, etc.

    Exact aliases (n, s, e, w, i, m, a) are still matched first.
    """

    def match(self, cmdname, include_prefixes=True):
        """Override to add prefix matching: if the command key starts
        with the typed input, treat it as a match."""
        # Try the default exact/startswith match first
        result = super().match(cmdname, include_prefixes=include_prefixes)
        if result[0] is not None:
            return result

        # Prefix match: does any key/alias START WITH the input?
        # e.g. input "sco" matches command key "score"
        stripped = cmdname.strip()
        if not stripped:
            return None, None

        if include_prefixes:
            candidates = self._keyaliases
        else:
            candidates = self._noprefix_aliases.keys()

        for cmd_key in candidates:
            if cmd_key.startswith(stripped) and len(stripped) >= 2:
                # Return the full command key so args are parsed correctly
                raw = cmd_key if include_prefixes else self._noprefix_aliases.get(cmd_key, cmd_key)
                return cmd_key, raw

        return None, None

    def _resolve_player_tile(self):
        """Deprecated — returns None. Use PlanetRoom queries instead."""
        return None

    def _find_player_tile(self):
        """Deprecated — returns None. Use PlanetRoom queries instead."""
        return None

    def _get_player_tile(self, create=False):
        """Deprecated — returns None. Use PlanetRoom queries instead."""
        return None


class CmdMove(GameCommand):
    """Move to an adjacent tile.

    Usage:
        move <direction>
        north/south/east/west (or n/s/e/w)

    Directions: north, south, east, west (or n, s, e, w)
    """

    key = "move"
    aliases = ["north", "south", "east", "west", "n", "s", "e", "w"]
    help_category = "Game"

    DIRECTION_MAP = {
        "north": (0, 1), "n": (0, 1),
        "south": (0, -1), "s": (0, -1),
        "east": (1, 0), "e": (1, 0),
        "west": (-1, 0), "w": (-1, 0),
    }

    def func(self):
        direction = self._parse_direction()
        if direction is None:
            return

        delta = self.DIRECTION_MAP.get(direction)
        if delta is None:
            self.caller.msg(
                f"Unknown direction '{direction}'. "
                "Use north, south, east, or west."
            )
            return

        caller = self.caller
        planet_room = caller.location  # Always a PlanetRoom

        # Exit building if inside one
        if not self._try_leave_building(caller, direction):
            return

        x, y, planet = self._resolve_coords(caller)
        if x is None or y is None or not planet:
            caller.msg("Cannot determine your position. Try logging out and back in.")
            return

        dx, dy = delta
        tx, ty = int(x) + dx, int(y) + dy

        # Validate bounds
        planet_registry = _get_system(caller, "planet_registry")
        if planet_registry is None:
            caller.msg("Movement systems are not available yet.")
            return
        if not planet_registry.is_valid_coordinate(tx, ty, planet):
            caller.msg("You have reached the edge of the map.")
            return

        # Check for blocked tiles via coordinate index
        buildings_at_target = planet_room.get_buildings_at(tx, ty) if hasattr(planet_room, "get_buildings_at") else []
        for building in buildings_at_target:
            if getattr(building, "is_offline", False):
                caller.msg("That tile is blocked by an offline building.")
                return
            # Wall passage check: block owner during combat timer (Req 6.24, 17.1-17.5)
            btype = get_building_attr(building, "building_type")
            if btype == "WL" and is_owner(caller, get_building_attr(building, "owner")):
                combat_expires = getattr(caller.db, "combat_timer_expires", 0) or 0
                if combat_expires > 0:
                    caller.msg(
                        "You cannot pass through your own Wall during combat."
                    )
                    return

        # Reset active-presence state on movement (Req 6.6, 6.7)
        if hasattr(caller, "db"):
            prev_state = getattr(caller.db, "activity_state", "idle")
            if prev_state != "idle":
                if prev_state == "building":
                    target = getattr(caller.db, "activity_target", None)
                    btype = get_building_attr(target, "building_type", "??") if target else "??"
                    caller.msg(f"|y[Paused] Construction of {btype} paused. Return to the tile or type 'build' to resume.|n")
                elif prev_state == "harvesting":
                    caller.msg("|y[Paused] Harvesting paused. Return to the tile to resume.|n")
                caller.db.activity_state = "idle"
                caller.db.activity_target = None
                caller.db.activity_progress = 0

        # Atomic coordinate update via move_entity
        planet_room.move_entity(caller, tx, ty)

        # Show terrain at new position
        terrain_label = ""
        try:
            terrain_generators = get_game_systems().get("_terrain_generators", {})
            gen = terrain_generators.get(planet)
            if gen:
                tt = gen.get_terrain(tx, ty)
                terrain_label = f" — {tt}"
                _, res = gen.get_terrain_and_resource(tx, ty)
                if res:
                    terrain_label += f" ({res})"
        except Exception:
            pass
        caller.msg(f"You move {direction} to ({tx}, {ty}){terrain_label}.")

        # Auto-enter building if present
        if buildings_at_target:
            building = buildings_at_target[0]
            if not getattr(building, "is_offline", False):
                opposite = _OPPOSITE_DIR.get(direction, direction)
                if is_admin(caller) or not is_exit_closed(building, opposite):
                    caller.db.inside_building = True
                    # Update fog before showing interior
                    self._update_fog_and_render(caller, show_map=False)
                    _show_building_interior(caller, building)
                    _send_map_update(caller)
                    return

        # Normal overworld display
        caller.db.inside_building = False
        self._update_fog_and_render(caller)
        _show_tile_summary(caller, planet_room)

    def _parse_direction(self):
        """Parse direction from command string or args."""
        cmdstring = self.cmdstring.strip().lower()
        if cmdstring in self.DIRECTION_MAP:
            return cmdstring
        direction = self.args.strip().lower()
        if not direction:
            self.caller.msg("Usage: move <direction>")
            return None
        return direction

    def _try_leave_building(self, caller, direction):
        """Handle leaving a building. Returns False if blocked."""
        if not getattr(caller.db, "inside_building", False):
            return True
        if not is_admin(caller):
            planet_room = caller.location
            cx = getattr(caller.db, "coord_x", None)
            cy = getattr(caller.db, "coord_y", None)
            if cx is not None and cy is not None and hasattr(planet_room, "get_buildings_at"):
                buildings = planet_room.get_buildings_at(int(cx), int(cy))
                for bld in buildings:
                    if is_exit_closed(bld, direction):
                        caller.msg(f"The {direction} exit is closed.")
                        return False
        caller.db.inside_building = False
        caller.msg("You step outside.")
        return True

    def _resolve_coords(self, caller):
        """Resolve caller's current coordinates, syncing from room if needed."""
        return ensure_coords(caller)

    def _update_fog_and_render(self, caller, show_map=True):
        """Update fog of war discovery and render the map."""
        fog_system = _get_system(caller, "fog_system")
        planet_room = caller.location
        if fog_system is not None:
            try:
                buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                visible = fog_system.get_visible_tiles(caller, buildings)
                fog_system.update_discovery(caller, visible, planet_room)
            except Exception:
                import logging
                logging.getLogger("evennia.commands").exception("Fog of war update failed")

        if show_map:
            renderer = _get_system(caller, "procedural_map_renderer")
            if renderer is not None:
                try:
                    buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                    map_str = renderer.render(caller, buildings)
                    if map_str:
                        _send_ascii_map(caller, map_str)
                except Exception:
                    import logging
                    logging.getLogger("evennia.commands").exception("Map render failed")

        _send_map_update(caller)


class CmdHarvest(GameCommand):
    """Gather a resource from the current tile.

    Usage:
        harvest
    """

    key = "harvest"
    aliases = ["ha"]
    help_category = "Game"

    def func(self):
        resource_system = _get_system(self.caller, "resource_system")
        if resource_system is None:
            self.caller.msg("Resource system unavailable.")
            return

        planet_room = self.caller.location
        if planet_room is None:
            self.caller.msg("Cannot determine your position.")
            return

        # Use active-presence harvesting (Req 3.4, 6.6, 6.7)
        # Pass PlanetRoom as the tile — start_harvest already supports
        # PlanetRoom path (reads player coords + TerrainGenerator)
        success, msg = resource_system.start_harvest(self.caller, planet_room)
        self.caller.msg(msg)


class CmdBuild(GameCommand):
    """Construct a building on the current tile.

    Usage:
        build <type>

    Example: build HQ

    If you're on a tile with your own incomplete building, typing
    ``build`` (no arguments) resumes construction.
    """

    key = "build"
    aliases = ["bu"]
    help_category = "Game"

    def func(self):
        caller = self.caller

        building_system = _get_system(caller, "building_system")
        if building_system is None:
            caller.msg("Building system unavailable.")
            return

        planet_room = caller.location
        x = getattr(caller.db, "coord_x", None)
        y = getattr(caller.db, "coord_y", None)
        if x is None or y is None:
            caller.msg("Cannot determine your position.")
            return
        x, y = int(x), int(y)

        building_type = self.args.strip().upper()

        # Check for resuming construction on an incomplete building
        if hasattr(planet_room, "get_buildings_at"):
            existing_buildings = planet_room.get_buildings_at(x, y)
        else:
            existing_buildings = []
            eb = getattr(planet_room, "building", None)
            if eb is not None:
                existing_buildings = [eb]

        for existing in existing_buildings:
            under_construction = get_building_attr(existing, "under_construction", False)
            if under_construction and is_owner(caller, get_building_attr(existing, "owner")):
                # If player typed a specific building type, don't auto-resume
                if building_type:
                    btype = get_building_attr(existing, "building_type", "??")
                    if building_type != btype:
                        caller.msg("This tile already has a building under construction.")
                        return
                # Resume: set player back into building state
                total = get_building_attr(existing, "construction_total", 0) or 0
                progress = get_building_attr(existing, "construction_progress", 0) or 0
                remaining = max(0, total - progress)
                if hasattr(caller, "db"):
                    caller.db.activity_state = "building"
                    caller.db.activity_target = existing
                    caller.db.activity_progress = 0
                btype = get_building_attr(existing, "building_type", "??")
                caller.msg(
                    f"Resuming construction of {btype} "
                    f"({progress}/{total}s, {remaining}s remaining). "
                    f"Stay on the tile to continue."
                )
                return

        if not building_type:
            self._show_available_buildings(caller)
            return

        # Start new construction
        success, msg = building_system.start_construction(
            caller, planet_room, building_type, x=x, y=y
        )
        caller.msg(msg)

        # Refresh the map so the new building is visible immediately
        if success:
            _send_map_update(caller)
            renderer = _get_system(caller, "procedural_map_renderer")
            if renderer:
                try:
                    buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                    map_str = renderer.render(caller, buildings)
                    if map_str:
                        _send_ascii_map(caller, map_str)
                except Exception:
                    pass

    def _show_available_buildings(self, caller):
        """Show buildings the player can construct at their current rank."""
        registry = _get_system(caller, "registry")
        if registry is None:
            caller.msg("Usage: build <type>")
            return

        player_level = getattr(getattr(caller, "db", None), "level", None)
        if player_level is None:
            player_level = getattr(getattr(caller, "db", None), "rank_level", 1) or 1

        lines = ["|wAvailable buildings:|n"]
        for abbr, bdef in sorted(registry.buildings.items(), key=lambda x: x[1].rank_requirement):
            if bdef.rank_requirement > player_level:
                continue
            cost_str = ", ".join(f"{amt} {res}" for res, amt in bdef.cost.items())
            lines.append(f"  |w{abbr}|n — {bdef.name} ({cost_str}) [{bdef.build_time_seconds}s]")

        if len(lines) == 1:
            lines.append("  None available at your level.")

        lines.append("")
        lines.append("Usage: |wbuild <type>|n  (e.g. |wbuild HQ|n)")
        caller.msg("\n".join(lines))


class CmdUpgrade(GameCommand):
    """Upgrade the building you're standing on.

    Usage:
        upgrade

    Must be on a tile with a building you own. Uses active-presence:
    stay on the tile for the timer to progress. Cost and time scale
    exponentially with level (cost × 2^L, time × 3^L).
    """

    key = "upgrade"
    aliases = ["up"]
    help_category = "Game"

    def func(self):
        caller = self.caller

        building_system = _get_system(caller, "building_system")
        if building_system is None:
            caller.msg("Building system unavailable.")
            return

        planet_room = caller.location
        x = getattr(caller.db, "coord_x", None)
        y = getattr(caller.db, "coord_y", None)
        if x is None or y is None:
            caller.msg("Cannot determine your position.")
            return
        x, y = int(x), int(y)

        # Find building at player's coordinates
        if hasattr(planet_room, "get_buildings_at"):
            buildings = planet_room.get_buildings_at(x, y)
        else:
            buildings = []
            b = getattr(planet_room, "building", None)
            if b is not None:
                buildings = [b]

        if not buildings:
            caller.msg("No building on this tile.")
            return

        building = buildings[0]
        success, msg = building_system.start_upgrade(caller, building)
        caller.msg(msg)


class CmdDemolish(GameCommand):
    """Demolish a building you own on your current tile.

    Usage:
        demolish

    Destroys the building and refunds resources based on level.
    Refund scales from 40% at level 1 to 80% at level 5.

    The total invested cost accounts for the base build plus all
    upgrade costs (base × 2 for level 2, base × 3 for level 3, etc).
    """

    key = "demolish"
    aliases = ["demo"]
    help_category = "Game"

    def func(self):
        caller = self.caller

        planet_room = caller.location
        x = getattr(caller.db, "coord_x", None)
        y = getattr(caller.db, "coord_y", None)
        if x is None or y is None:
            caller.msg("Cannot determine your position.")
            return
        x, y = int(x), int(y)

        # Find building at player's coordinates
        if hasattr(planet_room, "get_buildings_at"):
            buildings = planet_room.get_buildings_at(x, y)
        else:
            buildings = []
            for obj in getattr(planet_room, "contents", []):
                if hasattr(obj, "tags") and obj.tags.get("building", category="object_type"):
                    buildings.append(obj)

        building = buildings[0] if buildings else None

        if building is None:
            caller.msg("There is no building on this tile.")
            return

        # Check ownership (compare by .id for reliability across restarts)
        owner = get_building_attr(building, "owner")
        if not is_owner(caller, owner):
            caller.msg("You do not own this building.")
            return

        info = get_building_info(building)
        btype = info["type"]
        level = info["level"]
        name = info["name"]

        # Calculate refund
        from world.constants import DEMOLISH_REFUND_RATES, DEMOLISH_REFUND_DEFAULT
        refund = {}
        registry = _get_system(caller, "registry")
        if registry:
            try:
                bdef = registry.get_building(btype)
                # Total invested = base cost × (1 + 2 + ... + level)
                level_sum = level * (level + 1) // 2
                rate = DEMOLISH_REFUND_RATES.get(level, DEMOLISH_REFUND_DEFAULT)
                refund = {
                    res: int(amt * level_sum * rate)
                    for res, amt in bdef.cost.items()
                }
            except (KeyError, AttributeError):
                pass

        # Refund resources
        if refund and hasattr(caller, "add_resource"):
            for res, amt in refund.items():
                if amt > 0:
                    caller.add_resource(res, amt)

        # Delete the building from PlanetRoom (no room deletion needed)
        if hasattr(building, "delete"):
            building.delete()

        # Exit building state
        caller.db.inside_building = False

        if refund:
            refund_str = ", ".join(f"{res}: {amt}" for res, amt in refund.items() if amt > 0)
            caller.msg(f"Demolished {name} ({btype}). Refunded: {refund_str}.")
        else:
            caller.msg(f"Demolished {name} ({btype}).")


class CmdAttack(GameCommand):
    """Queue an attack against a target.

    Usage:
        attack <target>
    """

    key = "attack"
    aliases = ["at", "a"]
    help_category = "Game"

    def func(self):
        target_name = self.args.strip()
        if not target_name:
            self.caller.msg("Usage: attack <target>")
            return

        combat_engine = _get_system(self.caller, "combat_engine")
        if combat_engine is None:
            self.caller.msg("Combat system unavailable.")
            return

        # Search for target in the caller's location
        target = self.caller.search(target_name) if hasattr(self.caller, "search") else None
        if target is None:
            self.caller.msg(f"Could not find target '{target_name}'.")
            return

        if target is self.caller:
            self.caller.msg("You cannot attack yourself.")
            return

        success, msg = combat_engine.queue_attack(self.caller, target)
        self.caller.msg(msg)


class CmdEquip(GameCommand):
    """Equip an item from your inventory.

    Usage:
        equip <item>
    """

    key = "equip"
    help_category = "Game"

    def func(self):
        item_name = self.args.strip()
        if not item_name:
            self.caller.msg("Usage: equip <item>")
            return

        item = self.caller.search(item_name) if hasattr(self.caller, "search") else None
        if item is None:
            self.caller.msg(f"Could not find item '{item_name}'.")
            return

        handler = getattr(self.caller, "equipment", None)
        if handler is None:
            self.caller.msg("Equipment system unavailable.")
            return

        success, msg = handler.equip(item)
        self.caller.msg(msg)


class CmdUnequip(GameCommand):
    """Unequip an item from a slot.

    Usage:
        unequip <slot>
    """

    key = "unequip"
    help_category = "Game"

    def func(self):
        slot = self.args.strip().lower()
        if not slot:
            self.caller.msg("Usage: unequip <slot>")
            return

        handler = getattr(self.caller, "equipment", None)
        if handler is None:
            self.caller.msg("Equipment system unavailable.")
            return

        item = handler.unequip(slot)
        if item is None:
            self.caller.msg(f"No item equipped in '{slot}' slot.")
            return

        item_name = getattr(item, "key", str(item))
        self.caller.msg(f"Unequipped {item_name} from {slot} slot.")


class CmdResearch(GameCommand):
    """Start researching a technology at your Tech Lab.

    Usage:
        research <tech_key>
    """

    key = "research"
    aliases = ["re"]
    help_category = "Game"

    def func(self):
        tech_key = self.args.strip()
        if not tech_key:
            self.caller.msg("Usage: research <tech_key>")
            return

        tech_system = _get_system(self.caller, "tech_system")
        if tech_system is None:
            self.caller.msg("Tech system unavailable.")
            return

        success, msg = tech_system.start_research(self.caller, tech_key)
        self.caller.msg(msg)


class CmdPowerup(GameCommand):
    """Activate a powerup.

    Usage:
        powerup <key>
    """

    key = "powerup"
    aliases = ["pu"]
    help_category = "Game"

    def func(self):
        powerup_key = self.args.strip()
        if not powerup_key:
            self.caller.msg("Usage: powerup <key>")
            return

        powerup_system = _get_system(self.caller, "powerup_system")
        if powerup_system is None:
            self.caller.msg("Powerup system unavailable.")
            return

        success, msg = powerup_system.activate(self.caller, powerup_key)
        self.caller.msg(msg)


class CmdScore(GameCommand):
    """Display your full character sheet.

    Shows health, rank, resources, equipment, position, buildings,
    technologies, and active powerups.

    Usage:
        score
    """

    key = "score"
    aliases = ["status", "st", "sc"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        status = {}
        if hasattr(caller, "get_structured_status"):
            status = caller.get_structured_status()

        name = status.get("name", getattr(caller, "key", "?"))
        rank = status.get("rank_level", 1)
        xp = status.get("combat_xp", 0)
        hp = status.get("hp", "?")
        hp_max = status.get("hp_max", "?")

        # Level and rank lookup
        level = getattr(getattr(caller, "db", None), "level", None) or rank
        rank_name = f"Rank {rank}"
        xp_to_next_level = None
        rank_system = _get_system(caller, "rank_system")
        registry = _get_system(caller, "registry")
        if rank_system:
            try:
                rank_info = rank_system.get_status(caller)
                rank_name = rank_info.get("rank_name", rank_name)
                level = rank_info.get("level", level)
                xp_to_next_level = rank_info.get("xp_to_next_level")
            except Exception:
                pass
        elif registry:
            try:
                rdef = registry.get_rank_for_xp(xp)
                rank_name = rdef.name
            except Exception:
                pass

        # Position
        x = getattr(caller.db, "coord_x", "?")
        y = getattr(caller.db, "coord_y", "?")
        planet = getattr(caller.db, "coord_planet", "?")

        # Build display line
        rank_display = rank_name.replace("_", " ")
        xp_line = f"  Level {level} — {rank_display} | XP: {xp}"
        if xp_to_next_level is not None and xp_to_next_level > 0:
            next_xp = xp + xp_to_next_level
            xp_line += f"/{next_xp} to Level {level + 1}"

        lines = [
            f"|w=== {name} ===|n",
            xp_line,
            f"  HP: {hp}/{hp_max}",
            f"  Position: ({x}, {y}) on {planet}",
        ]

        # Agent count (Req 7b.10)
        agent_system = _get_system(caller, "agent_system")
        if agent_system:
            try:
                agent_count = agent_system.get_agent_count(caller)
                max_agents = agent_system.get_max_agents(caller)
                lines.append(f"  Agents: {agent_count}/{max_agents}")
            except Exception:
                pass

        # Combat timer (Req 17.5)
        combat_expires = getattr(caller.db, "combat_timer_expires", 0) or 0
        if combat_expires > 0:
            # Estimate remaining seconds from tick count
            remaining = combat_expires
            try:
                from evennia.utils.search import search_script
                tick_scripts = search_script("game_tick")
                if tick_scripts:
                    current_tick = getattr(tick_scripts[0].db, "tick_count", 0)
                    remaining = max(0, combat_expires - current_tick)
            except Exception:
                pass
            if remaining > 0:
                lines.append(f"  |rCombat: {remaining}s|n")

        # Resources
        resources = status.get("resources", {})
        if resources:
            res_parts = [f"{k}: {v}" for k, v in resources.items() if v]
            if res_parts:
                lines.append("  Resources: " + ", ".join(res_parts))

        # Equipment
        handler = getattr(caller, "equipment", None)
        if handler:
            equipped = handler.get_all_equipped()
            if equipped:
                eq_parts = [f"{slot}: {getattr(item, 'key', str(item))}"
                            for slot, item in equipped.items()]
                lines.append("  Equipment: " + ", ".join(eq_parts))

        # Buildings + techs
        building_count = 0
        if hasattr(caller, "get_buildings"):
            building_count = len(caller.get_buildings())
        techs = status.get("researched_techs", [])
        lines.append(f"  Buildings: {building_count} | Technologies: {len(techs)}")

        # Active powerups
        powerups = status.get("active_powerups", {})
        if powerups:
            lines.append(f"  Powerups: {', '.join(powerups.keys())}")

        caller.msg("\n".join(lines))


class CmdEquipment(GameCommand):
    """Display your current equipment loadout with stats.

    Usage:
        equipment
    """

    key = "equipment"
    aliases = ["eq", "gear"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        handler = getattr(caller, "equipment", None)
        if handler is None:
            caller.msg("Equipment system unavailable.")
            return

        equipped = handler.get_all_equipped()
        lines = ["|wEquipment:|n"]

        if not equipped:
            lines.append("  Nothing equipped.")
        else:
            for slot, item in equipped.items():
                item_name = getattr(item, "key", str(item))
                # Show stat modifiers if available
                mods = getattr(item, "stat_modifiers", None)
                if mods and isinstance(mods, dict):
                    mod_str = ", ".join(f"{k}: +{v}" for k, v in mods.items() if v)
                    lines.append(f"  [{slot}] {item_name} ({mod_str})")
                else:
                    lines.append(f"  [{slot}] {item_name}")

        # Show stat totals
        if equipped:
            lines.append("  ---")
            dmg = handler.get_stat_total("damage")
            armor = handler.get_stat_total("damage_reduction")
            if dmg:
                lines.append(f"  Total damage bonus: +{dmg:.0f}")
            if armor:
                lines.append(f"  Total armor: +{armor:.0f}")

        caller.msg("\n".join(lines))


class CmdBuildings(GameCommand):
    """List your owned buildings.

    Usage:
        buildings
    """

    key = "buildings"
    aliases = ["bl"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        buildings = []
        if hasattr(caller, "get_buildings"):
            buildings = caller.get_buildings()

        if not buildings:
            self.caller.msg("You have no buildings.")
            return

        lines = ["|wYour Buildings:|n"]
        for b in buildings:
            info = get_building_info(b)
            # Read coordinates from the building's own attributes
            bx = getattr(getattr(b, "db", None), "coord_x", None)
            by = getattr(getattr(b, "db", None), "coord_y", None)
            if bx is None and hasattr(b, "attributes"):
                bx = b.attributes.get("coord_x", default=None)
                by = b.attributes.get("coord_y", default=None)
            loc_str = ""
            if bx is not None and by is not None:
                loc_str = f" at ({bx}, {by})"
            status = ""
            if get_building_attr(b, "under_construction", False):
                progress = get_building_attr(b, "construction_progress", 0) or 0
                total = get_building_attr(b, "construction_total", 0) or 0
                status = f" |y[building {progress}/{total}s]|n"
            lines.append(f"  {info['type']} Lv{info['level']}{loc_str} — HP: {info['hp']}/{info['hp_max']}{status}")

        self.caller.msg("\n".join(lines))


class CmdScan(GameCommand):
    """Show visible entities within sight range.

    Usage:
        scan
    """

    key = "scan"
    aliases = ["sn"]
    help_category = "Game"

    def func(self):
        loc = self.caller.location
        if loc is None:
            self.caller.msg("You have no location.")
            return

        caller_x = getattr(self.caller.db, "coord_x", None)
        caller_y = getattr(self.caller.db, "coord_y", None)

        # Gather players at the same coordinates
        players = []
        if caller_x is not None and caller_y is not None and hasattr(loc, "get_players_at"):
            for obj in loc.get_players_at(int(caller_x), int(caller_y)):
                if obj is self.caller:
                    continue
                players.append(obj)
        else:
            # Legacy fallback: filter contents by coordinates
            for obj in getattr(loc, "contents", []):
                if obj is self.caller:
                    continue
                if hasattr(obj, "db") and hasattr(obj.db, "combat_xp"):
                    if caller_x is not None and caller_y is not None:
                        ox = getattr(obj.db, "coord_x", None)
                        oy = getattr(obj.db, "coord_y", None)
                        if ox is None or oy is None:
                            continue
                        if int(ox) != int(caller_x) or int(oy) != int(caller_y):
                            continue
                    players.append(obj)

        # Get buildings at player's coordinates from PlanetRoom
        buildings = []
        if caller_x is not None and caller_y is not None and hasattr(loc, "get_buildings_at"):
            buildings = loc.get_buildings_at(int(caller_x), int(caller_y))
        else:
            # Legacy fallback
            tile = self._find_player_tile()
            if tile is not None:
                for obj in getattr(tile, "contents", []):
                    if hasattr(obj, "building_type") or (
                        hasattr(obj, "db") and hasattr(obj.db, "building_type")
                    ):
                        buildings.append(obj)
                tile_building = getattr(tile, "building", None)
                if tile_building is not None and tile_building not in buildings:
                    buildings.append(tile_building)

        lines = ["|wScan Results:|n"]
        if players:
            lines.append("  Players:")
            for p in players:
                lines.append(f"    {getattr(p, 'key', '?')}")
        if buildings:
            lines.append("  Buildings:")
            for b in buildings:
                btype = getattr(b, "building_type", None)
                if btype is None and hasattr(b, "db"):
                    btype = getattr(b.db, "building_type", "??")
                owner = getattr(b, "owner", None)
                owner_name = getattr(owner, "key", "?") if owner else "?"
                lines.append(f"    {btype} (owner: {owner_name})")
        if not players and not buildings:
            lines.append("  Nothing visible nearby.")

        self.caller.msg("\n".join(lines))


class CmdTechnology(GameCommand):
    """List researched and available technologies.

    Usage:
        technology
    """

    key = "technology"
    aliases = ["tech"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        researched = set()
        if hasattr(caller, "db"):
            researched = getattr(caller.db, "researched_techs", set()) or set()

        tech_system = _get_system(caller, "tech_system")

        lines = ["|wTechnologies:|n"]
        if researched:
            lines.append("  Researched: " + ", ".join(sorted(researched)))
        else:
            lines.append("  Researched: none")

        if tech_system:
            available = tech_system.list_available(caller)
            if available:
                avail_names = [f"{t.name} ({t.key})" for t in available]
                lines.append("  Available: " + ", ".join(avail_names))
            else:
                lines.append("  Available: none")

        self.caller.msg("\n".join(lines))


class CmdInventory(GameCommand):
    """Display your resources and equipped items.

    Usage:
        inventory
    """

    key = "inventory"
    aliases = ["inv", "i"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        lines = ["|wInventory:|n"]

        # Resources
        resources = {}
        if hasattr(caller, "_ensure_resources"):
            resources = caller._ensure_resources()
        elif hasattr(caller, "db") and hasattr(caller.db, "resources"):
            resources = caller.db.resources or {}

        if resources:
            lines.append("  Resources:")
            for r, amt in resources.items():
                if amt:
                    lines.append(f"    {r}: {amt}")

        # Equipment
        handler = getattr(caller, "equipment", None)
        if handler:
            equipped = handler.get_all_equipped()
            if equipped:
                lines.append("  Equipped:")
                for slot, item in equipped.items():
                    item_name = getattr(item, "key", str(item))
                    lines.append(f"    [{slot}] {item_name}")

        if len(lines) == 1:
            lines.append("  Empty.")

        self.caller.msg("\n".join(lines))


class CmdChat(GameCommand):
    """Send a message to the Public channel.

    Usage:
        chat <message>

    Shortcut for 'public <message>'. Uses Evennia's channel system.
    """

    key = "chat"
    help_category = "Game"

    def func(self):
        message = self.args.strip()
        if not message:
            self.caller.msg("Usage: chat <message>")
            return

        try:
            from evennia.comms.models import ChannelDB
            channel = ChannelDB.objects.get(db_key="Public")
        except Exception:
            self.caller.msg("Public channel not available.")
            return

        if not channel.has_connection(self.caller.account):
            channel.connect(self.caller.account)

        channel.msg(message, senders=self.caller.account)


class CmdMessage(GameCommand):
    """Send a direct message to another player.

    Usage:
        message <player> <text>
    """

    key = "message"
    aliases = ["msg", "dm", "page", "tell", "whisper"]
    help_category = "Game"

    def func(self):
        args = self.args.strip()
        if not args or " " not in args:
            self.caller.msg("Usage: message <player> <text>")
            return

        target_name, text = args.split(None, 1)

        target = self.caller.search(target_name) if hasattr(self.caller, "search") else None
        if target is None:
            self.caller.msg(f"Could not find player '{target_name}'.")
            return

        formatted = format_dm_message(self.caller, text)

        target.msg(text=(formatted, {"cls": "game-chat"}))
        self.caller.msg(text=(f"You message {target_name}: {text}", {"cls": "game-chat"}))


class CmdSay(GameCommand):
    """Say something to everyone in your current location.

    Usage:
        say <message>
    """

    key = "say"
    help_category = "Game"

    def func(self):
        message = self.args.strip()
        if not message:
            self.caller.msg("Usage: say <message>")
            return

        name = self.caller.key
        loc = self.caller.location
        if loc is None:
            self.caller.msg("You have no location.")
            return

        # Broadcast to all in the room at the same tile
        self.caller.msg(text=(f'You say, "{message}"', {"cls": "game-chat"}))
        loc.msg_contents(
            text=(f'{name} says, "{message}"', {"cls": "game-chat"}),
            exclude=[self.caller],
            from_obj=self.caller,
        )


class CmdLook(GameCommand):
    """Look at your surroundings.

    Usage:
        look
        look <obj>

    Shows the overworld map and building interior (if inside one).
    """

    key = "look"
    aliases = ["l", "ls"]
    locks = "cmd:all()"
    arg_regex = r"\s|$"
    help_category = "General"

    def func(self):
        caller = self.caller
        if not self.args:
            target = caller.location
            if not target:
                caller.msg("You have no location to look at!")
                return
        else:
            target = caller.search(self.args)
            if not target:
                return

        # at_look calls return_appearance on the target, which for
        # PlanetRoom shows the building interior if inside one.
        desc = caller.at_look(target)
        if desc:
            self.msg(text=(desc, {"type": "look"}), options=None)

        # Always render and send the overworld map on bare 'look'
        if not self.args:
            _render_and_send_map(caller)

        # Show tile summary (objects at player's coordinates) after the map
        if not self.args and hasattr(target, "get_objects_at"):
            _show_tile_summary(caller, target)

    # _show_tile_summary is now a module-level function


class CmdMap(GameCommand):
    """Display the procedural ASCII map centered on your position.

    Shows terrain, buildings, and other players within your vision
    range, with RTS-style fog of war for previously explored tiles.

    Usage:
        map
    """

    key = "map"
    aliases = ["m"]
    help_category = "Game"

    def func(self):
        _render_and_send_map(self.caller)


# ------------------------------------------------------------------ #
#  Helper: display building interior
# ------------------------------------------------------------------ #

# Direction opposites for entry checking
_OPPOSITE_DIR = {
    "north": "south", "n": "south",
    "south": "north", "s": "north",
    "east": "west", "e": "west",
    "west": "east", "w": "east",
}

_CARDINAL_DIRS = ("north", "south", "east", "west")


def _show_tile_summary(caller, planet_room):
    """Show resource drops and other players at the caller's tile."""
    if not hasattr(planet_room, "get_objects_at"):
        return
    x = getattr(caller.db, "coord_x", None)
    y = getattr(caller.db, "coord_y", None)
    if x is None or y is None:
        return
    x, y = int(x), int(y)
    parts = []
    drops = planet_room.get_objects_at(x, y, type_tag="resource_drop")
    drop_strs = []
    for d in drops:
        amt = getattr(getattr(d, "db", None), "amount", 0) or 0
        rtype = getattr(getattr(d, "db", None), "resource_type", "?")
        if amt > 0:
            drop_strs.append(f"{amt} {rtype}")
    if drop_strs:
        parts.append(f"Resources: {', '.join(drop_strs)}")
    others = []
    for p in planet_room.get_players_at(x, y):
        if p is not caller:
            others.append(getattr(p, "key", "?"))
    if others:
        parts.append(f"Players: {', '.join(others)}")
    if parts:
        caller.msg(" | ".join(parts))


def _show_building_interior(caller, building):
    """Display the interior of a building.

    Uses the shared formatter from rooms.py to avoid duplication.
    """
    try:
        from typeclasses.rooms import _format_building_interior
        registry = _get_system(caller, "registry")
        caller.msg(_format_building_interior(caller, building, registry=registry))
    except ImportError:
        caller.msg("You are inside a building.")


class CmdCloseExit(GameCommand):
    """Close an exit in a building you own.

    Usage:
        closeexit <direction>

    Prevents anyone from entering or leaving through that direction.
    Admin users are not affected by closed exits.
    """

    key = "closeexit"
    help_category = "Game"

    def func(self):
        caller = self.caller
        direction = self.args.strip().lower()
        dir_map = {"n": "north", "s": "south", "e": "east", "w": "west"}
        direction = dir_map.get(direction, direction)

        if direction not in _CARDINAL_DIRS:
            caller.msg("Usage: closeexit <north, south, east, or west>")
            return

        if not getattr(caller.db, "inside_building", False):
            caller.msg("You must be inside a building to close an exit.")
            return

        tile = self._find_player_tile()
        if tile is None:
            caller.msg("Cannot determine your position.")
            return

        building = getattr(tile, "building", None)
        if building is None:
            caller.msg("No building here.")
            return

        owner = get_building_attr(building, "owner")
        if not is_owner(caller, owner):
            caller.msg("You do not own this building.")
            return

        closed = get_closed_exits(building)

        if direction in closed:
            caller.msg(f"The {direction} exit is already closed.")
            return

        if len(closed) >= 3:
            caller.msg("You must leave at least one exit open.")
            return

        closed.add(direction)
        building.attributes.add("closed_exits", list(closed))
        caller.msg(f"Closed the {direction} exit.")


class CmdOpenExit(GameCommand):
    """Open a closed exit in a building you own.

    Usage:
        openexit <direction>
    """

    key = "openexit"
    help_category = "Game"

    def func(self):
        caller = self.caller
        direction = self.args.strip().lower()
        dir_map = {"n": "north", "s": "south", "e": "east", "w": "west"}
        direction = dir_map.get(direction, direction)

        if direction not in _CARDINAL_DIRS:
            caller.msg("Usage: openexit <north, south, east, or west>")
            return

        if not getattr(caller.db, "inside_building", False):
            caller.msg("You must be inside a building to open an exit.")
            return

        tile = self._find_player_tile()
        if tile is None:
            caller.msg("Cannot determine your position.")
            return

        building = getattr(tile, "building", None)
        if building is None:
            caller.msg("No building here.")
            return

        owner = get_building_attr(building, "owner")
        if not is_owner(caller, owner):
            caller.msg("You do not own this building.")
            return

        closed = get_closed_exits(building)

        if direction not in closed:
            caller.msg(f"The {direction} exit is already open.")
            return

        closed.discard(direction)
        building.attributes.add("closed_exits", list(closed))
        caller.msg(f"Opened the {direction} exit.")


class CmdStop(GameCommand):
    """Stop your current activity and return to idle.

    Usage:
        stop

    Cancels building construction or harvesting in progress.
    Construction progress is saved — you can resume later with
    ``build`` on the same tile.
    """

    key = "stop"
    aliases = ["cancel"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        state = getattr(getattr(caller, "db", None), "activity_state", "idle")

        if state == "idle":
            caller.msg("You aren't doing anything.")
            return

        if state == "building":
            target = getattr(caller.db, "activity_target", None)
            btype = "??"
            if target is not None:
                btype = get_building_attr(target, "building_type", "??")
            caller.msg(f"You stop working on {btype}. Progress is saved.")
        elif state == "harvesting":
            caller.msg("You stop harvesting.")

        caller.db.activity_state = "idle"
        caller.db.activity_target = None
        caller.db.activity_progress = 0


class CmdLeave(GameCommand):
    """Leave the building you're currently inside.

    Usage:
        leave
        outside
    """

    key = "leave"
    aliases = ["outside", "exit building", "out"]
    help_category = "Game"

    def func(self):
        caller = self.caller
        if not getattr(caller.db, "inside_building", False):
            caller.msg("You are not inside a building.")
            return
        caller.db.inside_building = False
        caller.msg("You step outside.")
        # Show the map
        renderer = _get_system(caller, "procedural_map_renderer")
        if renderer:
            buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
            try:
                map_str = renderer.render(caller, buildings)
                if map_str:
                    x = getattr(caller.db, "coord_x", "?")
                    y = getattr(caller.db, "coord_y", "?")
                    planet = getattr(caller.db, "coord_planet", "?")
                    terrain_info = ""
                    try:
                        if x != "?" and y != "?":
                            terrain_generators = get_game_systems().get("_terrain_generators", {})
                            gen = terrain_generators.get(str(planet))
                            if gen:
                                tt = gen.get_terrain(int(x), int(y))
                                terrain_info = f" | {tt}"
                                _, res = gen.get_terrain_and_resource(int(x), int(y))
                                if res:
                                    terrain_info += f" ({res})"
                    except Exception:
                        pass
                    _send_ascii_map(caller, f"|wMap — ({x}, {y}) on {planet}{terrain_info}|n\n{map_str}")
            except Exception:
                pass
        _send_map_update(caller)


class CmdGet(GameCommand):
    """Pick up an object at your current tile.

    Usage:
        get <object>

    Only picks up objects at your coordinates. Objects at other
    tiles in the same PlanetRoom are not accessible.
    """

    key = "get"
    aliases = ["grab", "take"]
    locks = "cmd:all()"
    arg_regex = r"\s|$"
    help_category = "General"

    def func(self):
        caller = self.caller
        if not self.args:
            caller.msg("Get what?")
            return

        obj_name = self.args.strip()
        loc = caller.location
        if loc is None:
            caller.msg("You have no location.")
            return

        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)

        # Handle "get all" — pick up everything at the player's tile
        if obj_name.lower() == "all":
            self._get_all(caller, loc, cx, cy)
            return

        # Search only objects at the player's coordinates
        if cx is not None and cy is not None and hasattr(loc, "get_objects_at"):
            candidates = loc.get_objects_at(int(cx), int(cy))
            # Filter by name match (case-insensitive prefix)
            target = None
            search = obj_name.lower()
            for obj in candidates:
                if obj is caller:
                    continue
                obj_key = getattr(obj, "key", "").lower()
                if obj_key == search or obj_key.startswith(search):
                    target = obj
                    break
            if target is None:
                caller.msg(f"Could not find '{obj_name}' here.")
                return
        else:
            # Fallback: use Evennia's default search
            target = caller.search(obj_name)
            if not target:
                return

        self._pickup(caller, target)

    def _pickup(self, caller, target):
        """Attempt to pick up a single object."""
        if hasattr(target, "at_pre_get"):
            if not target.at_pre_get(caller):
                return

        if hasattr(target, "move_to"):
            target.move_to(caller, quiet=True)

        if hasattr(target, "at_get"):
            target.at_get(caller)

    def _get_all(self, caller, loc, cx, cy):
        """Pick up all gettable objects at the player's coordinates."""
        if cx is None or cy is None or not hasattr(loc, "get_objects_at"):
            caller.msg("Nothing to pick up.")
            return

        candidates = list(loc.get_objects_at(int(cx), int(cy)))
        picked = 0
        for obj in candidates:
            if obj is caller:
                continue
            # Skip players and buildings
            if hasattr(obj, "has_account") and obj.has_account:
                continue
            if hasattr(obj, "tags") and obj.tags.get("building", category="object_type"):
                continue
            if hasattr(obj, "at_pre_get") and not obj.at_pre_get(caller):
                continue
            if hasattr(obj, "move_to"):
                obj.move_to(caller, quiet=True)
            if hasattr(obj, "at_get"):
                obj.at_get(caller)
            picked += 1

        if picked == 0:
            caller.msg("Nothing to pick up.")


# ------------------------------------------------------------------ #
#  Helper: retrieve a game system from the caller's ndb or global
# ------------------------------------------------------------------ #

# _get_system is imported from world.utils at the top of this file


# ------------------------------------------------------------------ #
#  CmdWho — override Evennia's default to show rank and level
# ------------------------------------------------------------------ #

class CmdWho(GameCommand):
    """List who is currently online.

    Usage:
        who

    Shows online players with their rank and level.
    """

    key = "who"
    aliases = ["doing"]
    locks = "cmd:all()"
    account_caller = True

    def func(self):
        import time
        import evennia
        from evennia import utils

        account = self.account
        session_list = evennia.SESSION_HANDLER.get_sessions()
        session_list = sorted(session_list, key=lambda o: o.account.key)

        naccounts = evennia.SESSION_HANDLER.account_count()

        show_admin = (
            account.check_permstring("Developer")
            or account.check_permstring("Admins")
        )

        # Resolve rank names from registry
        registry = None
        try:
            from server.conf.game_init import game_systems
            registry = game_systems.get("registry")
        except Exception:
            pass

        rank_map = {}
        if registry and hasattr(registry, "ranks"):
            for r in registry.ranks:
                rank_map[r.level] = r.name

        if show_admin:
            table = self.styled_table(
                "|wName", "|wRank", "|wLvl",
                "|wOn for", "|wIdle", "|wPuppeting",
            )
        else:
            table = self.styled_table(
                "|wName", "|wRank", "|wLvl", "|wOn for", "|wIdle",
            )

        for session in session_list:
            if not session.logged_in:
                continue
            delta_cmd = time.time() - session.cmd_last_visible
            delta_conn = time.time() - session.conn_time
            session_account = session.get_account()
            puppet = session.get_puppet()

            name = utils.crop(
                session_account.get_display_name(account), width=25
            )

            # Extract rank and level from the puppet
            player_level = 1
            rank_name = "Recruit"
            if puppet and hasattr(puppet, "db"):
                player_level = getattr(puppet.db, "level", None)
                if player_level is None:
                    player_level = getattr(puppet.db, "rank_level", 1) or 1
                from world.systems.rank_system import rank_from_level
                rank_num = rank_from_level(player_level)
                rank_name = rank_map.get(rank_num, f"Rank {rank_num}")

            if show_admin:
                puppet_name = (
                    utils.crop(puppet.get_display_name(account), width=25)
                    if puppet else "None"
                )
                table.add_row(
                    name,
                    rank_name,
                    str(player_level),
                    utils.time_format(delta_conn, 0),
                    utils.time_format(delta_cmd, 1),
                    puppet_name,
                )
            else:
                table.add_row(
                    name,
                    rank_name,
                    str(player_level),
                    utils.time_format(delta_conn, 0),
                    utils.time_format(delta_cmd, 1),
                )

        is_one = naccounts == 1
        self.msg(
            "|wOnline:|n\n%s\n%s player%s online."
            % (table, "One" if is_one else naccounts, "" if is_one else "s")
        )
