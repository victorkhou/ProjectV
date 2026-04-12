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
        caller.msg(map_update=data)
    except Exception:
        pass


def _send_ascii_map(caller, map_text):
    """Send ASCII map text tagged with cls=ascii-map.

    The webclient hides elements with class 'ascii-map' when the
    graphical map is active, avoiding duplicate display. Telnet
    clients see it normally.
    """
    caller.msg(text=(map_text, {"cls": "ascii-map"}))


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
        """Resolve the OverworldRoom at the caller's coordinates, creating on demand.

        Used by commands that need to modify tile state (build, harvest).
        """
        return self._get_player_tile(create=True)

    def _find_player_tile(self):
        """Find the OverworldRoom at the caller's coordinates without creating.

        Used by commands that only read tile state (scan, upgrade, demolish, exits).
        Returns None if no room exists at those coordinates.
        """
        return self._get_player_tile(create=False)

    def _get_player_tile(self, create=False):
        """Internal: get tile room at caller's coordinates."""
        caller = self.caller
        tile_resolver = _get_system(caller, "tile_resolver")
        if tile_resolver is None:
            return None
        x = getattr(caller.db, "coord_x", None)
        y = getattr(caller.db, "coord_y", None)
        planet = getattr(caller.db, "coord_planet", None)
        if x is None or y is None or not planet:
            return None
        try:
            if create:
                return tile_resolver.resolve(x, y, planet)
            return tile_resolver.get_if_exists(x, y, planet)
        except (ValueError, KeyError):
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

        # Check for blocked tiles
        tile_resolver = _get_system(caller, "tile_resolver")
        if tile_resolver is not None:
            target_room = tile_resolver.get_if_exists(tx, ty, planet)
            if target_room is not None:
                building = getattr(target_room, "building", None)
                if building is not None and getattr(building, "is_offline", False):
                    caller.msg("That tile is blocked by an offline building.")
                    return

        # Update coordinates
        caller.db.coord_x = tx
        caller.db.coord_y = ty
        self._ensure_planet_room(caller, planet)
        caller.msg(f"You move {direction} to ({tx}, {ty}).")

        # Auto-enter building if present
        if self._try_enter_building(caller, direction, tile_resolver, tx, ty, planet):
            return

        # Normal overworld display
        caller.db.inside_building = False
        self._update_fog_and_render(caller, tile_resolver)

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
            tile_resolver = _get_system(caller, "tile_resolver")
            if tile_resolver is not None:
                cx = getattr(caller.db, "coord_x", None)
                cy = getattr(caller.db, "coord_y", None)
                cp = getattr(caller.db, "coord_planet", None)
                if cx is not None and cy is not None and cp:
                    cur_room = tile_resolver.get_if_exists(cx, cy, cp)
                    if cur_room is not None:
                        bld = getattr(cur_room, "building", None)
                        if bld is not None and is_exit_closed(bld, direction):
                            caller.msg(f"The {direction} exit is closed.")
                            return False
        caller.db.inside_building = False
        caller.msg("You step outside.")
        return True

    def _resolve_coords(self, caller):
        """Resolve caller's current coordinates, syncing from room if needed."""
        return ensure_coords(caller)

    def _ensure_planet_room(self, caller, planet):
        """Move caller to the shared PlanetRoom if not already there."""
        planet_rooms = get_game_systems().get("planet_rooms", {})
        target_planet_room = planet_rooms.get(planet)
        if target_planet_room and caller.location is not target_planet_room:
            caller.move_to(target_planet_room, quiet=True)

    def _try_enter_building(self, caller, direction, tile_resolver, tx, ty, planet):
        """Auto-enter building at target tile. Returns True if entered."""
        if tile_resolver is None:
            return False
        target_room = tile_resolver.get_if_exists(tx, ty, planet)
        if target_room is None:
            return False
        building = getattr(target_room, "building", None)
        if building is None or getattr(building, "is_offline", False):
            return False
        opposite = _OPPOSITE_DIR.get(direction, direction)
        if not is_admin(caller) and is_exit_closed(building, opposite):
            return False
        caller.db.inside_building = True
        # Update fog before showing interior
        fog_system = _get_system(caller, "fog_system")
        if fog_system is not None:
            try:
                player_buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                visible = fog_system.get_visible_tiles(caller, player_buildings)
                fog_system.update_discovery(caller, visible, tile_resolver)
            except Exception:
                pass
        _show_building_interior(caller, building)
        _send_map_update(caller)
        return True

    def _update_fog_and_render(self, caller, tile_resolver):
        """Update fog of war discovery and render the map."""
        fog_system = _get_system(caller, "fog_system")
        if fog_system is not None and tile_resolver is not None:
            try:
                buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                visible = fog_system.get_visible_tiles(caller, buildings)
                fog_system.update_discovery(caller, visible, tile_resolver)
            except Exception:
                import logging
                logging.getLogger("evennia.commands").exception("Fog of war update failed")

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

        # Resolve the actual tile room at player's coordinates
        tile = self._resolve_player_tile()
        if tile is None:
            self.caller.msg("Cannot determine your position.")
            return

        success, msg = resource_system.harvest(self.caller, tile)
        self.caller.msg(msg)


class CmdBuild(GameCommand):
    """Construct a building on the current tile.

    Usage:
        build <type>

    Example: build HQ
    """

    key = "build"
    aliases = ["bu"]
    help_category = "Game"

    def func(self):
        building_type = self.args.strip().upper()
        if not building_type:
            self.caller.msg("Usage: build <type>")
            return

        building_system = _get_system(self.caller, "building_system")
        if building_system is None:
            self.caller.msg("Building system unavailable.")
            return

        # Resolve the actual tile room at player's coordinates
        tile = self._resolve_player_tile()
        if tile is None:
            self.caller.msg("Cannot determine your position.")
            return

        success, msg = building_system.construct(self.caller, tile, building_type)
        self.caller.msg(msg)


class CmdUpgrade(GameCommand):
    """Upgrade the building you're standing on.

    Usage:
        upgrade

    Must be on a tile with a building you own. Costs base_cost × target_level.
    Increases max HP by 20% per level. All buildings can reach level 5.
    """

    key = "upgrade"
    aliases = ["up"]
    help_category = "Game"

    MAX_LEVEL = 5

    def func(self):
        caller = self.caller

        tile = self._find_player_tile()
        if tile is None:
            caller.msg("Cannot determine your position.")
            return

        building = getattr(tile, "building", None)
        if building is None:
            caller.msg("No building on this tile.")
            return

        # Ownership check (compare by .id for reliability across restarts)
        owner = get_building_attr(building, "owner")
        if not is_owner(caller, owner):
            caller.msg("You do not own this building.")
            return

        # Read current state
        info = get_building_info(building)
        btype = info["type"]
        level = info["level"]

        if level >= self.MAX_LEVEL:
            caller.msg(f"This building is already at maximum level ({self.MAX_LEVEL}).")
            return

        target_level = level + 1

        # Look up cost from registry
        registry = _get_system(caller, "registry")
        if registry is None:
            caller.msg("Registry unavailable.")
            return

        try:
            bdef = registry.get_building(btype)
        except (KeyError, AttributeError):
            caller.msg(f"Unknown building type: {btype}")
            return

        upgrade_cost = {res: amt * target_level for res, amt in bdef.cost.items()}

        # Check resources
        if not caller.has_resources(upgrade_cost):
            cost_str = ", ".join(f"{res}: {amt}" for res, amt in upgrade_cost.items())
            caller.msg(f"Insufficient resources. Upgrade to level {target_level} requires: {cost_str}")
            return

        # Deduct and upgrade
        caller.deduct_resources(upgrade_cost)
        building.attributes.add("building_level", target_level)

        # Increase max HP by 20% per level (from base)
        base_hp = bdef.max_health
        new_max_hp = int(base_hp * (1 + 0.2 * (target_level - 1)))
        building.attributes.add("hp_max", new_max_hp)
        # Heal to new max
        building.attributes.add("hp", new_max_hp)

        caller.msg(f"Upgraded {bdef.name} to level {target_level}. HP: {new_max_hp}/{new_max_hp}")


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

    # Refund percentage by level: 40% at L1, scaling to 80% at L5
    _REFUND_RATES = {1: 0.40, 2: 0.50, 3: 0.60, 4: 0.70, 5: 0.80}

    def func(self):
        caller = self.caller

        # Resolve the actual tile room at player's coordinates
        loc = self._find_player_tile()
        if loc is None:
            caller.msg("Cannot determine your position.")
            return

        # Find building on this tile
        building = None
        for obj in getattr(loc, "contents", []):
            if hasattr(obj, "attributes") and obj.attributes.has("building_type"):
                building = obj
                break

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
        refund = {}
        registry = _get_system(caller, "registry")
        if registry:
            try:
                bdef = registry.get_building(btype)
                # Total invested = base cost × (1 + 2 + ... + level)
                level_sum = level * (level + 1) // 2
                rate = self._REFUND_RATES.get(level, 0.40)
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

        # Delete the building
        if hasattr(building, "delete"):
            building.delete()

        # Clean up the now-empty OverworldRoom (rooms only exist for buildings)
        tile_resolver = _get_system(caller, "tile_resolver")
        if loc is not None and not getattr(loc, "building", None):
            # No more buildings — remove the room from cache and DB
            if tile_resolver is not None:
                cache = getattr(tile_resolver, "_cache", None)
                if cache is not None:
                    lx = getattr(loc, "x", None)
                    ly = getattr(loc, "y", None)
                    lp = getattr(loc, "planet_name", None)
                    if lx is not None and ly is not None and lp:
                        cache.remove(lx, ly, lp)
            if hasattr(loc, "delete"):
                loc.delete()

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

        # Rank name lookup
        rank_name = f"Rank {rank}"
        registry = _get_system(caller, "registry")
        if registry:
            try:
                rdef = registry.get_rank_for_xp(xp)
                rank_name = rdef.name
            except Exception:
                pass

        # Position
        x = getattr(caller.db, "coord_x", "?")
        y = getattr(caller.db, "coord_y", "?")
        planet = getattr(caller.db, "coord_planet", "?")

        lines = [
            f"|w=== {name} ===|n",
            f"  {rank_name} (Level {rank}) | XP: {xp}",
            f"  HP: {hp}/{hp_max}",
            f"  Position: ({x}, {y}) on {planet}",
        ]

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
            loc = getattr(b, "location", None)
            loc_str = ""
            if loc:
                lx = getattr(loc, "x", "?")
                ly = getattr(loc, "y", "?")
                loc_str = f" at ({lx}, {ly})"
            lines.append(f"  {info['type']} Lv{info['level']}{loc_str} — HP: {info['hp']}/{info['hp_max']}")

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

        # Gather players from PlanetRoom, filtered by matching coordinates
        contents = getattr(loc, "contents", [])
        players = []
        for obj in contents:
            if obj is self.caller:
                continue
            if hasattr(obj, "db") and hasattr(obj.db, "combat_xp"):
                # Filter by coordinate match when in a PlanetRoom
                if caller_x is not None and caller_y is not None:
                    ox = getattr(obj.db, "coord_x", None)
                    oy = getattr(obj.db, "coord_y", None)
                    if ox is None or oy is None:
                        continue
                    if int(ox) != int(caller_x) or int(oy) != int(caller_y):
                        continue
                players.append(obj)

        # Check the resolved tile room for buildings
        buildings = []
        tile = self._find_player_tile()
        if tile is not None:
            for obj in getattr(tile, "contents", []):
                if hasattr(obj, "building_type") or (
                    hasattr(obj, "db") and hasattr(obj.db, "building_type")
                ):
                    buildings.append(obj)
            # Also check tile.building attribute
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

    Shows the ASCII map (or building interior if inside one).
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
        # PlanetRoom sends the map via tagged msg and returns empty.
        desc = caller.at_look(target)
        if desc:
            self.msg(text=(desc, {"type": "look"}), options=None)
        # Send graphical map update to webclient
        if not self.args:
            _send_map_update(caller)


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
        caller = self.caller
        renderer = _get_system(caller, "procedural_map_renderer")
        if renderer is None:
            caller.msg("Map system is not available.")
            return

        planet = getattr(caller.db, "coord_planet", None)

        if not planet:
            _, _, planet = ensure_coords(caller)

        if not planet:
            caller.msg("Cannot determine your position. Try logging out and back in.")
            return

        buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []

        try:
            map_str = renderer.render(caller, buildings)
        except Exception:
            import logging
            logging.getLogger("evennia.commands").exception("Map render failed")
            caller.msg("Could not render the map.")
            return

        if map_str:
            x = getattr(caller.db, "coord_x", "?")
            y = getattr(caller.db, "coord_y", "?")
            # Show discovered tile count for debugging fog of war
            fog_system = _get_system(caller, "fog_system")
            disc_count = 0
            if fog_system:
                disc_count = len(fog_system.get_discovered_tile_set(caller))
            caller.msg(text=(f"|wMap — ({x}, {y}) on {planet} | {disc_count} tiles discovered|n\n{map_str}", {"cls": "ascii-map"}))
        else:
            caller.msg("Nothing to display — explore first.")

        _send_map_update(caller)


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
                    _send_ascii_map(caller, f"|wMap — ({x}, {y}) on {planet}|n\n{map_str}")
            except Exception:
                pass
        _send_map_update(caller)


# ------------------------------------------------------------------ #
#  Helper: retrieve a game system from the caller's ndb or global
# ------------------------------------------------------------------ #

# _get_system is imported from world.utils at the top of this file
