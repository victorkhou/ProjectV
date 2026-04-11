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
        # If invoked as a direction alias (e.g. "north"), use the command name as direction
        cmdstring = self.cmdstring.strip().lower()
        if cmdstring in self.DIRECTION_MAP:
            direction = cmdstring
        else:
            direction = self.args.strip().lower()

        if not direction:
            self.caller.msg("Usage: move <direction>")
            return

        delta = self.DIRECTION_MAP.get(direction)
        if delta is None:
            self.caller.msg(
                f"Unknown direction '{direction}'. "
                "Use north, south, east, or west."
            )
            return

        caller = self.caller
        tile_resolver = _get_system(caller, "tile_resolver")
        planet_registry = _get_system(caller, "planet_registry")

        if tile_resolver is None or planet_registry is None:
            caller.msg("Movement systems are not available yet.")
            return

        dx, dy = delta

        # Read current coordinates from caller Attributes
        x = getattr(caller.db, "coord_x", None)
        y = getattr(caller.db, "coord_y", None)
        planet = getattr(caller.db, "coord_planet", None)

        # If coords are missing, try to sync from the current room
        if x is None or y is None or not planet:
            loc = caller.location
            if loc is not None and hasattr(loc, "x") and hasattr(loc, "planet_name"):
                rx = getattr(loc, "x", None)
                ry = getattr(loc, "y", None)
                rp = getattr(loc, "planet_name", None)
                if rx is not None and ry is not None and rp and rp != "unknown":
                    caller.db.coord_x = rx
                    caller.db.coord_y = ry
                    caller.db.coord_planet = rp
                    x, y, planet = rx, ry, rp

        # If still no coords, try spawning to the overworld
        if x is None or y is None or not planet:
            if hasattr(caller, "_ensure_overworld_position"):
                caller._ensure_overworld_position()
                x = getattr(caller.db, "coord_x", None)
                y = getattr(caller.db, "coord_y", None)
                planet = getattr(caller.db, "coord_planet", None)

        if x is None or y is None or not planet:
            caller.msg("Cannot determine your position. Try logging out and back in.")
            return

        tx, ty = int(x) + dx, int(y) + dy

        # Validate bounds
        if not planet_registry.is_valid_coordinate(tx, ty, planet):
            caller.msg("You have reached the edge of the map.")
            return

        # Resolve target room
        try:
            target = tile_resolver.resolve(tx, ty, planet)
        except (ValueError, KeyError):
            caller.msg("You cannot move there — tile is impassable or does not exist.")
            return

        if target is None:
            caller.msg("You cannot move there — tile is impassable or does not exist.")
            return

        # Check for offline buildings blocking entry
        building = getattr(target, "building", None)
        if building is not None and getattr(building, "is_offline", False):
            caller.msg("That tile is blocked by an offline building.")
            return

        # Remember old room before moving
        old_room = caller.location

        # Move player
        caller.move_to(target, quiet=True)

        # Clean up the room we just left if it has no reason to persist.
        # Rooms with buildings, other players, or custom state are kept.
        if old_room is not None and old_room is not target:
            _maybe_cleanup_room(old_room, tile_resolver)

        # Update coordinate Attributes
        caller.db.coord_x = tx
        caller.db.coord_y = ty

        caller.msg(f"You move {direction} to ({tx}, {ty}).")

        # Trigger fog of war discovery update
        fog_system = _get_system(caller, "fog_system")
        if fog_system is not None:
            try:
                buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                visible = fog_system.get_visible_tiles(caller, buildings)
                fog_system.update_discovery(caller, visible, tile_resolver)
            except Exception:
                import logging
                logging.getLogger("mygame.commands").exception("Fog of war update failed")

        # Render the procedural map after move
        renderer = _get_system(caller, "procedural_map_renderer")
        if renderer is not None:
            try:
                buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []
                map_str = renderer.render(caller, buildings)
                if map_str:
                    caller.msg(map_str)
            except Exception:
                import logging
                logging.getLogger("mygame.commands").exception("Map render failed")


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

        tile = self.caller.location
        if tile is None:
            self.caller.msg("You have no location.")
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

        tile = self.caller.location
        if tile is None:
            self.caller.msg("You have no location.")
            return

        success, msg = building_system.construct(self.caller, tile, building_type)
        self.caller.msg(msg)


class CmdUpgrade(GameCommand):
    """Upgrade a building you own.

    Usage:
        upgrade <building_type>

    Upgrades the building of the given type on your current tile.
    """

    key = "upgrade"
    aliases = ["up"]
    help_category = "Game"

    def func(self):
        building_type = self.args.strip().upper()
        if not building_type:
            self.caller.msg("Usage: upgrade <building_type>")
            return

        tile = self.caller.location
        if tile is None:
            self.caller.msg("You have no location.")
            return

        building = getattr(tile, "building", None)
        if building is None:
            self.caller.msg("No building on this tile.")
            return

        btype = getattr(building, "building_type", None)
        if hasattr(building, "db"):
            btype = btype or getattr(building.db, "building_type", None)
        if btype and btype.upper() != building_type:
            self.caller.msg(f"No {building_type} building on this tile.")
            return

        building_system = _get_system(self.caller, "building_system")
        if building_system is None:
            self.caller.msg("Building system unavailable.")
            return

        success, msg = building_system.upgrade(self.caller, building)
        self.caller.msg(msg)


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
            btype = getattr(b, "building_type", "??")
            if hasattr(b, "db"):
                btype = btype or getattr(b.db, "building_type", "??")
            level = getattr(b, "building_level", 1)
            hp = getattr(b, "hp", "?")
            if hasattr(b, "db"):
                hp = getattr(b.db, "hp", hp)
            hp_max = getattr(b, "hp_max", "?")
            if hasattr(b, "db"):
                hp_max = getattr(b.db, "hp_max", hp_max)
            loc = getattr(b, "location", None)
            loc_str = ""
            if loc:
                lx = getattr(loc, "x", "?")
                ly = getattr(loc, "y", "?")
                loc_str = f" at ({lx}, {ly})"
            lines.append(f"  {btype} Lv{level}{loc_str} — HP: {hp}/{hp_max}")

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

        # Gather entities from the current tile's contents
        contents = getattr(loc, "contents", [])
        players = []
        buildings = []
        for obj in contents:
            if obj is self.caller:
                continue
            if hasattr(obj, "db") and hasattr(obj.db, "combat_xp"):
                players.append(obj)
            elif hasattr(obj, "building_type") or (
                hasattr(obj, "db") and hasattr(obj.db, "building_type")
            ):
                buildings.append(obj)

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
    """Send a message to the Global channel.

    Usage:
        chat <message>
    """

    key = "chat"
    help_category = "Game"

    def func(self):
        message = self.args.strip()
        if not message:
            self.caller.msg("Usage: chat <message>")
            return

        chat_system = _get_system(self.caller, "chat_system")
        if chat_system is None:
            self.caller.msg("Chat system unavailable.")
            return

        formatted = chat_system.format_channel_message(self.caller, message)
        # Broadcast via the Global channel or fallback to msg
        try:
            channel_db = chat_system._get_channel_db()
            if channel_db:
                channel = channel_db.objects.get(
                    db_key=chat_system.GLOBAL_CHANNEL_KEY
                )
                channel.msg(formatted)
                return
        except Exception:
            pass

        # Fallback: just echo
        self.caller.msg(formatted)


class CmdMessage(GameCommand):
    """Send a direct message to another player.

    Usage:
        message <player> <text>
    """

    key = "message"
    aliases = ["msg", "dm"]
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

        chat_system = _get_system(self.caller, "chat_system")
        if chat_system:
            formatted = chat_system.format_dm_message(self.caller, text)
        else:
            formatted = f"{self.caller.key} (DM): {text}"

        target.msg(formatted)
        self.caller.msg(f"You message {target_name}: {text}")


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

        # Broadcast to all in the room
        self.caller.msg(f'You say, "{message}"')
        loc.msg_contents(f'{name} says, "{message}"', exclude=[self.caller])


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

        # If coords are missing, try to sync from the current room
        if not planet:
            loc = caller.location
            if loc is not None and hasattr(loc, "x") and hasattr(loc, "planet_name"):
                rx = getattr(loc, "x", None)
                ry = getattr(loc, "y", None)
                rp = getattr(loc, "planet_name", None)
                if rx is not None and ry is not None and rp and rp != "unknown":
                    caller.db.coord_x = rx
                    caller.db.coord_y = ry
                    caller.db.coord_planet = rp
                    planet = rp

        # If still no coords, try spawning to the overworld
        if not planet:
            if hasattr(caller, "_ensure_overworld_position"):
                caller._ensure_overworld_position()
                planet = getattr(caller.db, "coord_planet", None)

        if not planet:
            caller.msg("Cannot determine your position. Try logging out and back in.")
            return

        buildings = caller.get_buildings() if hasattr(caller, "get_buildings") else []

        try:
            map_str = renderer.render(caller, buildings)
        except Exception:
            import logging
            logging.getLogger("mygame.commands").exception("Map render failed")
            caller.msg("Could not render the map.")
            return

        if map_str:
            x = getattr(caller.db, "coord_x", "?")
            y = getattr(caller.db, "coord_y", "?")
            caller.msg(f"|wMap — ({x}, {y}) on {planet}|n\n{map_str}")
        else:
            caller.msg("Nothing to display — explore first.")


# ------------------------------------------------------------------ #
#  Helper: clean up empty rooms after a player leaves
# ------------------------------------------------------------------ #

def _maybe_cleanup_room(room, tile_resolver):
    """Delete a room if it has no players, no buildings, and no custom state.

    This keeps the DB lean — only rooms with meaningful state persist.
    Rooms are cheap to recreate from the terrain generator.
    """
    try:
        # Don't clean up non-overworld rooms (Limbo, tutorial, etc.)
        if not hasattr(room, "tags"):
            return
        room_type = room.tags.get(category="room_type", return_list=False)
        if room_type != "overworld_tile":
            return

        # Keep rooms with players still in them
        contents = getattr(room, "contents", [])
        for obj in contents:
            if hasattr(obj, "has_account") and obj.has_account:
                return
            if hasattr(obj, "attributes") and obj.attributes.has("building_type"):
                return

        # Keep rooms with custom descriptions
        desc = getattr(room.db, "desc", None) if hasattr(room, "db") else None
        if desc and desc != "" and desc != "You see nothing special.":
            return

        # Keep rooms with depleted resources (state that matters)
        rn = getattr(room.db, "resource_node_data", None) if hasattr(room, "db") else None
        if rn and isinstance(rn, dict) and rn.get("depleted"):
            return

        # Safe to delete — evict from cache and remove from DB
        x = getattr(room, "x", None)
        y = getattr(room, "y", None)
        planet = getattr(room, "planet_name", None)
        if x is not None and y is not None and planet:
            tile_resolver._cache.remove(x, y, planet)

        if hasattr(room, "delete"):
            room.delete()
    except Exception:
        pass  # Never crash the move command over cleanup


# ------------------------------------------------------------------ #
#  Helper: retrieve a game system from the caller's ndb or global
# ------------------------------------------------------------------ #

def _get_system(caller, system_name):
    """Look up a game system by name.

    Checks caller.ndb.systems first (set during game init),
    then falls back to a module-level registry.
    """
    systems = getattr(getattr(caller, "ndb", None), "systems", None)
    if systems and isinstance(systems, dict):
        return systems.get(system_name)
    # Fallback: try module-level game_systems dict
    try:
        from server.conf.game_init import game_systems
        return game_systems.get(system_name)
    except (ImportError, AttributeError):
        return None
