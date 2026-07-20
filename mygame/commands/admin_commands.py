"""
Admin commands for the RTS Combat Overworld.

Restricted to Builder+ permission level. All executions are logged
with operator name, command, and target.

"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand
from commands.command_router import AdminSubcommandRouter
from world.utils import get_system as _get_system

logger = logging.getLogger("mygame.admin")


class CmdReboot(BaseCommand):
    """Hot-reload all YAML definition files.

    Usage:
        @reboot

    Restricted to Builder+ permission level.
    """

    key = "@reboot"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        # Permission check
        if not _check_perm(caller, "Builder"):
            caller.msg("Permission denied. Builder+ required.")
            return

        logger.info(
            "Admin command @reboot executed by %s",
            getattr(caller, "key", "?"),
        )

        registry = _get_system(caller, "registry")
        if registry is None:
            caller.msg("Data Registry unavailable.")
            return

        success, errors = registry.reload_all()
        if success:
            caller.msg("|gData reload successful.|n")
            logger.info("Data reload successful (operator: %s)", caller.key)
        else:
            error_text = "\n".join(errors)
            caller.msg(f"|rData reload failed:|n\n{error_text}")
            logger.warning(
                "Data reload failed (operator: %s): %s",
                caller.key, "; ".join(errors),
            )


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _check_perm(caller, perm_name):
    """Check if the caller has the required permission.

    Tries Evennia's perm() method first, then falls back to a
    simple attribute check for testing.
    """
    if hasattr(caller, "check_permstring"):
        return caller.check_permstring(perm_name)
    if hasattr(caller, "permissions"):
        perms = caller.permissions
        if hasattr(perms, "check"):
            return perms.check(perm_name)
        if isinstance(perms, (list, tuple, set)):
            # Simple hierarchy check
            hierarchy = ["Player", "Helper", "Builder", "Admin", "Developer"]
            caller_level = -1
            required_level = -1
            for i, p in enumerate(hierarchy):
                if p in perms:
                    caller_level = max(caller_level, i)
                if p == perm_name:
                    required_level = i
            return caller_level >= required_level
    # Fallback for testing: check _permissions attribute
    if hasattr(caller, "_permissions"):
        return perm_name in caller._permissions
    return False



def _parse_index_token(token):
    """Parse a 1-based index token (``#3`` or ``3``), or ``None`` if not one.

    Admin ``spawn`` commands accept an index shown by their matching ``list``
    (e.g. '@item list' numbers each item). A leading ``#`` is optional so both
    ``@item spawn 3`` and ``@item spawn #3`` work. Returns the integer index, or
    ``None`` when *token* isn't a bare/hash-prefixed positive integer (so the
    caller falls through to name/key/prefix resolution).
    """
    if not token:
        return None
    body = token[1:] if token[0] == "#" else token
    if body.isdigit():
        n = int(body)
        return n if n >= 1 else None
    return None


def _resolve_by_index(token, ordered):
    """Return the def at 1-based *token* index within *ordered*, else ``None``.

    *ordered* is the SAME stable, sorted sequence the matching ``list``
    subcommand numbers, so an index typed by the operator maps to exactly the
    row they saw. Out-of-range or non-index tokens return ``None``.
    """
    n = _parse_index_token(token)
    if n is None or n > len(ordered):
        return None
    return ordered[n - 1]


def _resolve_planet_room(caller, planet):
    """Return the shared PlanetRoom for *planet*, or None (after messaging).

    The single lookup shared by the teleport ('goto') and transfer commands:
    both need the destination planet's one PlanetRoom to relocate an object
    into. Messages the caller on any failure so callers just bail on None.
    """
    planet_rooms = None
    try:
        from server.conf.game_init import game_systems
        planet_rooms = game_systems.get("planet_rooms", {})
    except (ImportError, AttributeError):
        pass

    if not planet_rooms:
        caller.msg("Planet rooms not available.")
        return None

    target_room = planet_rooms.get(planet)
    if not target_room:
        caller.msg(f"No PlanetRoom found for {planet}.")
        return None
    return target_room


def _relocate_object(obj, target_room, tx, ty, planet):
    """Relocate *obj* to ``(tx, ty, planet)`` within/into *target_room*.

    The shared spatial move behind both 'goto' (relocating the caller) and
    'transfer' (pulling another entity to the caller's tile). Handles the
    cross-planet PlanetRoom move plus coordinate-index bookkeeping. Does NOT
    message or look — that is the caller's concern, since who-sees-what differs
    between moving yourself and summoning someone else.

    move_hooks=False on the cross-planet move_to: Evennia's arrival hooks
    (at_object_receive + the auto-look via at_post_move) fire DURING move_to —
    before move_entity sets the new x/y below — so they'd render/react at the
    STALE origin coords. We do the index bookkeeping ourselves instead.

    notify=False on move_entity: a teleport/summon is not a step onto an
    adjacent tile; for a cross-planet move the stored old coords belong to the
    origin planet, so arrival/departure messaging would notify the wrong
    players.
    """
    origin_room = obj.location
    old_x = getattr(obj.db, "coord_x", None)
    old_y = getattr(obj.db, "coord_y", None)

    obj.db.coord_planet = planet

    if obj.location is not target_room:
        # Skipping at_object_leave means the origin room's coordinate index
        # still holds the object — remove it explicitly so it doesn't leak.
        if origin_room is not None and old_x is not None and old_y is not None:
            idx = getattr(getattr(origin_room, "ndb", None), "_coord_index", None)
            if idx is not None:
                try:
                    idx.remove(obj, int(old_x), int(old_y))
                except Exception:  # pragma: no cover - defensive
                    pass
        obj.move_to(target_room, quiet=True, move_hooks=False)

    target_room.move_entity(obj, tx, ty, notify=False)


def _search_entities(caller, name):
    """Return every entity matching *name*, excluding *caller* itself.

    Resolution order (shared by 'goto' and 'transfer'):

    1. ``caller.search(name, quiet=True)`` — Evennia's search does partial
       (prefix) matching scoped to the caller's location. Since every overworld
       entity shares one PlanetRoom per planet, this finds any
       player/NPC/building/item on the caller's *current* planet by name or
       prefix (the common case: acting on someone here).
    2. ``evennia.search_object(name)`` — a global exact-by-key fallback that
       reaches entities on OTHER planets when the local search misses.

    Returns a (possibly empty) list. Callers decide how to handle 0 / 1 /
    many matches — 'goto' picks the nearest, 'transfer' lists them.
    """
    matches = []
    if hasattr(caller, "search"):
        res = caller.search(name, quiet=True)
        if res:
            matches = list(res) if isinstance(res, (list, tuple)) else [res]

    if not matches:
        try:
            from evennia import search_object
            matches = list(search_object(name) or [])
        except Exception:  # noqa: BLE001 - no global search in stubbed tests
            matches = []

    return [m for m in matches if m is not caller]


def _owner_label(entity):
    """Return a short owner tag for *entity* — '(yours)'-style disambiguator.

    Agents and enemy NPCs share a name across owners (every player owns an
    'Agent-1'), so the owner is the natural differentiator when 'transfer'
    lists co-named candidates. Returns '' when the entity has no owner (players,
    unowned buildings).
    """
    owner = getattr(getattr(entity, "db", None), "owner", None)
    if owner is None:
        return ""
    return getattr(owner, "key", None) or "?"


class CmdAdminBuilding(AdminSubcommandRouter):
    """Manage buildings on the overworld.

    Usage:
      @building spawn <type> [owner=<name>] [level=<N>]
      @building destroy

    Options:
      <type>         building abbreviation (EX) or full name (extractor)
      [owner=<name>] owner to assign; 'none' for an unowned building
                     (defaults to you)
      [level=<N>]    starting level 1-5 (defaults to 1)

    Subcommands:
      spawn   — Spawn a building at your current tile (Builder+)
      destroy — Destroy the building at your current tile (Builder+)
      open    — Open/close the building at your tile to ranged fire (Builder+)
      list    — List building types with index numbers (Builder+)

    """

    key = "@building"

    @staticmethod
    def _building_index(registry):
        """Return the stable, sorted list of BuildingDefs '@building list' numbers.

        Shared by ``sub_list`` (prints the 1-based index) and ``sub_spawn``
        (resolves an index the operator typed), so ``@building spawn N`` maps to
        the row shown as ``[N]``. Sorted by abbreviation — deterministic across
        reloads.
        """
        buildings = getattr(registry, "buildings", None) if registry else None
        if not buildings:
            return []
        return [buildings[abbr] for abbr in sorted(buildings.keys())]

    def sub_spawn(self, args):
        """Spawn a building at the caller's current tile.

        Args:
            args: "<type> [owner=<name>] [level=<N>]"
        """
        caller = self.caller
        if not args:
            caller.msg("Usage: @building spawn <type> [owner=<name>] [level=<N>]")
            return

        parts = args.split()
        btype = parts[0].upper()

        # Parse optional kwargs
        owner = caller
        level = 1
        for part in parts[1:]:
            if part.lower().startswith("owner="):
                owner_name = part.split("=", 1)[1]
                if owner_name.lower() in ("none", "nobody", "null", ""):
                    owner = None
                else:
                    found = caller.search(owner_name, quiet=True) if hasattr(caller, "search") else None
                    if not found:
                        caller.msg(f"Could not find player '{owner_name}'.")
                        return
                    owner = found[0] if isinstance(found, list) else found
            elif part.lower().startswith("level="):
                try:
                    level = int(part.split("=", 1)[1])
                    level = max(1, min(level, 5))
                except ValueError:
                    caller.msg("Level must be a number 1-5.")
                    return

        # Validate building type exists. Accept an index (``#3`` or ``3`` from
        # '@building list'), an abbreviation (EX), a full name (extractor), or an
        # unambiguous prefix — same typo-tolerant resolver the player 'build'
        # command uses, plus the index shortcut.
        registry = _get_system(caller, "registry")
        if registry:
            bdef = _resolve_by_index(parts[0], self._building_index(registry))
            if bdef is None:
                resolver = getattr(registry, "resolve_building", None)
                if callable(resolver):
                    bdef = resolver(parts[0])
            if bdef is None:
                try:
                    bdef = registry.get_building(btype)
                except KeyError:
                    bdef = None
            if bdef is None:
                # List the real, current abbreviations from the registry rather
                # than a hardcoded string that can drift from buildings.yaml.
                valid = ", ".join(sorted(getattr(registry, "buildings", {}) or {}))
                caller.msg(
                    f"Unknown building type '{parts[0]}'. "
                    f"Valid: {valid or 'none loaded'}. "
                    f"Or use '@building list' for names + index numbers."
                )
                return
            btype = bdef.abbreviation
        else:
            bdef = None

        # Get current location — must be a PlanetRoom
        planet_room = caller.location
        if planet_room is None:
            caller.msg("You have no location.")
            return

        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)

        if cx is None or cy is None:
            caller.msg("You have no coordinates set.")
            return

        # Create the building in PlanetRoom at caller's coordinates
        try:
            from evennia.utils.create import create_object

            hp = bdef.max_health if bdef else 500
            name = bdef.name if bdef else btype

            building = create_object(
                typeclass="typeclasses.objects.Building",
                key=name,
                location=planet_room,
            )
            building.attributes.add("building_type", btype)
            building.attributes.add("owner", owner)
            building.attributes.add("building_level", level)
            building.attributes.add("hp", hp)
            building.attributes.add("hp_max", hp)
            building.attributes.add("offline", False)
            # Stamp coords + register in the coordinate index (at_object_receive
            # saw coord_x=None during create_object).
            from world.utils import place_on_tile
            place_on_tile(building, planet_room, cx, cy)

            # Announce the spawn on the event bus exactly as the player build
            # path does, so subscribers (e.g. the ShieldSystem recomputing
            # shields from a new generator) react immediately rather than only
            # on the next periodic sweep. Best-effort — a missing bus or a
            # subscriber error must never fail the admin spawn.
            event_bus = _get_system(caller, "event_bus")
            if event_bus is not None:
                try:
                    from world.event_bus import BUILDING_CONSTRUCTED
                    event_bus.publish(
                        BUILDING_CONSTRUCTED,
                        player=owner, building=building, tile=planet_room,
                    )
                except Exception:  # noqa: BLE001
                    pass

            owner_name = getattr(owner, "key", "nobody") if owner else "nobody"
            self._log_admin(
                "spawn",
                f"{btype} level {level} owner={owner_name} at ({cx}, {cy}) in "
                f"{planet_room.key if hasattr(planet_room, 'key') else planet_room}",
            )
            caller.msg(
                f"Spawned {name} ({btype}) level {level}, "
                f"owned by {owner_name} at ({cx}, {cy})."
            )
        except Exception as e:
            caller.msg(f"Failed to create building: {e}")

    def sub_destroy(self, args):
        """Destroy the building at the caller's current tile.

        Finds the first building at the caller's coordinates and deletes
        it without refunding resources (admin override).
        """
        caller = self.caller

        planet_room = caller.location
        if planet_room is None:
            caller.msg("You have no location.")
            return

        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)

        if cx is None or cy is None:
            caller.msg("You have no coordinates set.")
            return

        if not hasattr(planet_room, "get_objects_at"):
            caller.msg("Current location does not support coordinate queries.")
            return

        buildings = planet_room.get_objects_at(int(cx), int(cy), type_tag="building")
        if not buildings:
            caller.msg(f"No building found at ({cx}, {cy}).")
            return

        building = buildings[0]
        btype = building.attributes.get("building_type", default="??") if hasattr(building, "attributes") else "??"
        bname = getattr(building, "key", btype)

        # NOTE: deliberately does NOT publish BUILDING_DESTROYED — that event
        # triggers base-elimination (a full NPC-base wipe on a Sentinel HQ),
        # which is not what a surgical admin delete intends. Shield capacity on
        # the survivors self-corrects on the ShieldSystem's next periodic sweep
        # (refresh_owners, each regen interval).
        building.delete()

        self._log_admin("destroy", f"{bname} ({btype}) at ({cx}, {cy})")
        caller.msg(f"Destroyed {bname} ({btype}) at ({cx}, {cy}).")

    def sub_open(self, args):
        """Toggle whether the building at your tile is open or closed to ranged fire.

        Usage:
            @building open        — open it (ranged weapons/turrets can hit it)
            @building open close  — close it (only melee attacks reach it)
        """
        caller = self.caller

        planet_room = caller.location
        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)
        if planet_room is None or cx is None or cy is None:
            caller.msg("You have no coordinates set.")
            return
        if not hasattr(planet_room, "get_objects_at"):
            caller.msg("Current location does not support coordinate queries.")
            return

        buildings = planet_room.get_objects_at(int(cx), int(cy), type_tag="building")
        if not buildings:
            caller.msg(f"No building found at ({cx}, {cy}).")
            return

        building = buildings[0]
        # "close" -> closed; anything else (incl. no arg) -> open.
        want_open = args.strip().lower() not in ("close", "closed", "off", "false", "no")

        if hasattr(building, "set_open"):
            building.set_open(want_open)
        else:
            building.attributes.add("open", want_open)

        btype = building.attributes.get("building_type", default="??") \
            if hasattr(building, "attributes") else "??"
        bname = getattr(building, "key", btype)
        state = "open" if want_open else "closed"
        self._log_admin("open", f"{bname} ({btype}) at ({cx}, {cy}) -> {state}")
        caller.msg(
            f"{bname} ({btype}) at ({cx}, {cy}) is now |w{state}|n "
            f"({'ranged + melee' if want_open else 'melee only'})."
        )

    def sub_list(self, args):
        """List building types with a stable 1-based index.

        ``@building spawn <N>`` (or ``#N``) spawns the type shown as ``[N]``, so
        an operator can reference a building by index instead of its abbreviation
        or full name.

        Args:
            args: unused (accepted for router-signature consistency).
        """
        caller = self.caller
        registry = _get_system(caller, "registry")
        ordered = self._building_index(registry)
        if not ordered:
            caller.msg("No building definitions loaded.")
            return

        lines = ["|w=== Building types (spawn by name or [index]) ===|n"]
        for idx, b in enumerate(ordered, start=1):
            cat = f" ({b.category})" if getattr(b, "category", "") else ""
            lines.append(f"  |w[{idx}]|n {b.abbreviation:<4s} {b.name}{cat}")
        self._log_admin("list", "building types")
        caller.msg("\n".join(lines))

    subcommands = {
        "spawn": (sub_spawn, "Spawn a building at your tile", "Builder"),
        "destroy": (sub_destroy, "Destroy building at your tile", "Builder"),
        "open": (sub_open, "Open/close building to ranged fire", "Builder"),
        "list": (sub_list, "List building types with index numbers", "Builder"),
    }


class CmdAdminAgent(AdminSubcommandRouter):
    """Manage agent NPCs for players.

    Usage:
        @agent create <player> [count]
        @agent destroy <id> <player>
        @agent destroy training <player>
        @agent list <player>

    Subcommands:
        create  — Instantly create agent(s) bypassing cost/timer (Admin+)
        destroy — Destroy an agent by ID or clear training state (Admin+)
        list    — List all agents for a player (Builder+)

    """

    key = "@agent"

    def sub_create(self, args):
        """Create agent(s) for a player, bypassing cost and timer.

        Args:
            args: "<player> [count]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @agent create <player> [count]")
            return

        parts = args.strip().split()
        player_name = parts[0]
        count = 1
        if len(parts) >= 2:
            count = self.parse_int(parts[1], "Count")
            if count is None:
                return
            if count < 1:
                caller.msg("Count must be at least 1.")
                return

        target = self.resolve_player(player_name)
        if target is None:
            return

        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        created_ids = []
        for _ in range(count):
            agents = agent_system.get_agents(target)
            if agents:
                max_id = max(getattr(a.db, "agent_id", 0) for a in agents)
                next_id = max_id + 1
            else:
                next_id = 1

            npc = agent_system._create_npc_func(target, next_id)
            if npc is not None:
                created_ids.append(next_id)
                target.db.next_agent_id = next_id + 1

        if created_ids:
            ids_str = ", ".join(f"#{i}" for i in created_ids)
            self._log_admin("create", f"{len(created_ids)} agent(s) for {target.key}: {ids_str}")
            caller.msg(f"Created {len(created_ids)} agent(s) for {target.key}: {ids_str}.")
            if hasattr(target, "msg") and target is not caller:
                target.msg(f"|y[Admin] {len(created_ids)} agent(s) created for you: {ids_str}.|n")
        else:
            caller.msg("Failed to create agents.")

    def sub_destroy(self, args):
        """Destroy an agent by ID or clear training state.

        Args:
            args: "<id> <player>" or "training <player>"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @agent destroy <id> <player> | @agent destroy training <player>")
            return

        parts = args.strip().split()
        if len(parts) < 2:
            caller.msg("Usage: @agent destroy <id> <player> | @agent destroy training <player>")
            return

        first_arg = parts[0]
        player_name = parts[1]

        # Find the target player
        target = self.resolve_player(player_name)
        if target is None:
            return

        if first_arg.lower() == "training":
            self._clear_training(caller, target)
            return

        try:
            agent_id = int(first_arg)
        except ValueError:
            caller.msg("Agent ID must be a number or 'training'.")
            return

        self._destroy_agent(caller, target, agent_id)

    def _clear_training(self, caller, target):
        """Clear all stuck training state for a player."""
        cleared = 0
        try:
            from evennia.objects.models import ObjectDB

            buildings = list(ObjectDB.objects.filter(
                db_attributes__db_key="training_owner",
            ))
            for b in buildings:
                owner = b.attributes.get("training_owner")
                if owner is target:
                    b.attributes.add("training_agent_id", None)
                    b.attributes.add("training_ticks_remaining", None)
                    b.attributes.add("training_owner", None)
                    cleared += 1
        except Exception:
            pass

        # Also try via player's buildings
        try:
            for b in target.get_buildings():
                agent_id = None
                if hasattr(b, "attributes"):
                    agent_id = b.attributes.get("training_agent_id")
                elif hasattr(b, "db"):
                    agent_id = getattr(b.db, "training_agent_id", None)
                if agent_id is not None:
                    if hasattr(b, "attributes"):
                        b.attributes.add("training_agent_id", None)
                        b.attributes.add("training_ticks_remaining", None)
                        b.attributes.add("training_owner", None)
                    cleared += 1
        except Exception:
            pass

        self._log_admin("destroy", f"cleared training state on {cleared} building(s) for {target.key}")
        caller.msg(f"Cleared training state on {cleared} building(s) for {target.key}.")

    def _destroy_agent(self, caller, target, agent_id):
        """Destroy a specific agent NPC, or clear its stuck training state."""
        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        agent = agent_system.get_agent_by_id(target, agent_id)
        if agent is not None:
            # Clear building assignment if any
            building = getattr(agent.db, "role_target", None) if hasattr(agent, "db") else None
            if building is not None and hasattr(building, "db"):
                if getattr(building.db, "assigned_agent", None) is agent:
                    building.db.assigned_agent = None

            agent_name = getattr(agent, "key", f"Agent-{agent_id}")
            if hasattr(agent, "delete"):
                agent.delete()

            self._log_admin("destroy", f"agent #{agent_id} ({agent_name}) belonging to {target.key}")
            caller.msg(f"Destroyed agent #{agent_id} ({agent_name}) belonging to {target.key}.")
            return

        # Agent NPC doesn't exist — check if it's stuck in training
        cleared = False
        try:
            for b in target.get_buildings():
                tid = None
                if hasattr(b, "attributes"):
                    tid = b.attributes.get("training_agent_id")
                elif hasattr(b, "db"):
                    tid = getattr(b.db, "training_agent_id", None)
                if tid == agent_id:
                    if hasattr(b, "attributes"):
                        b.attributes.add("training_agent_id", None)
                        b.attributes.add("training_ticks_remaining", None)
                        b.attributes.add("training_owner", None)
                    elif hasattr(b, "db"):
                        b.db.training_agent_id = None
                        b.db.training_ticks_remaining = None
                        b.db.training_owner = None
                    cleared = True
                    self._log_admin("destroy", f"cleared stuck training #{agent_id} for {target.key}")
                    caller.msg(f"Cleared stuck training for agent #{agent_id} on {target.key}'s Academy.")
                    break
        except Exception:
            pass

        if not cleared:
            caller.msg(f"Agent #{agent_id} not found for {target.key} (not spawned, not in training).")

    def sub_list(self, args):
        """List all agents belonging to a player.

        Args:
            args: "<player>"
        """
        caller = self.caller

        player_name = args.strip() if args else ""
        if not player_name:
            caller.msg("Usage: @agent list <player>")
            return

        target = self.resolve_player(player_name)
        if target is None:
            return

        agent_system = self.require_system("agent_system")
        if agent_system is None:
            return

        agents = agent_system.get_agents(target)
        next_id = getattr(getattr(target, "db", None), "next_agent_id", None)
        count = agent_system.get_agent_count(target)

        lines = [f"|w=== Agents for {target.key} ({count} agents, next_id={next_id}) ===|n"]

        if not agents:
            lines.append("  No trained agents.")
        else:
            for agent in sorted(agents, key=lambda a: getattr(a.db, "agent_id", 0)):
                aid = getattr(agent.db, "agent_id", "?")
                role = getattr(agent.db, "role", "") or "unassigned"
                incap = getattr(agent.db, "incapacitated", False)
                reserve = getattr(agent.db, "reserve", False)
                obj_id = getattr(agent, "id", "?")

                status_parts = []
                if incap:
                    status_parts.append("|rIncapacitated|n")
                if reserve:
                    status_parts.append("|yReserved|n")
                if not status_parts:
                    status_parts.append("|gActive|n")
                status = " ".join(status_parts)

                target_bld = getattr(agent.db, "role_target", None)
                loc_str = "HQ"
                if target_bld is not None:
                    btype = getattr(target_bld.db, "building_type", "?") if hasattr(target_bld, "db") else "?"
                    loc_str = btype
                elif role in ("soldier", "medic"):
                    loc_str = "army"

                lines.append(
                    f"  |c#{aid}|n (db#{obj_id})  {role:<12s}  "
                    f"{loc_str:<10s}  {status}"
                )

        # Show training state on buildings
        try:
            for b in target.get_buildings():
                tid = None
                if hasattr(b, "attributes"):
                    tid = b.attributes.get("training_agent_id")
                if tid is not None:
                    remaining = b.attributes.get("training_ticks_remaining") or 0
                    btype = b.attributes.get("building_type") or "??"
                    lines.append(f"  |y[Training] #{tid} at {btype} — {remaining}s remaining|n")
        except Exception:
            pass

        self._log_admin("list", f"agents for {target.key}")
        caller.msg("\n".join(lines))

    subcommands = {
        "create": (sub_create, "Create agent(s) for a player", "Admin"),
        "destroy": (sub_destroy, "Destroy agent or clear training", "Admin"),
        "list": (sub_list, "List agents for a player", "Builder"),
    }


class CmdAdminResource(AdminSubcommandRouter):
    """Manage player resources.

    Usage:
        @resource give <type|all> <amount> [player]
        @resource reset [player]

    Subcommands:
        give  — Give resources to a player (Builder+). <type> is a resource
                name (Wood, Stone, Iron, Energy, Circuits, Nexium) or 'all'
                for every resource; an unknown name is rejected.
        reset — Reset player(s) to starting resources (Admin+)

    """

    key = "@resource"

    def sub_give(self, args):
        """Give resources to a player.

        Args:
            args: "<type> <amount> [player]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @resource give <type> <amount> [player]")
            return

        parts = args.strip().split()
        if len(parts) < 2:
            caller.msg("Usage: @resource give <type> <amount> [player]")
            return

        from world.constants import RESOURCE_TYPES

        resource_token = parts[0]
        amount_str = parts[1]
        player_name = parts[2] if len(parts) >= 3 else None

        try:
            amount = int(amount_str)
        except ValueError:
            caller.msg(f"Invalid amount: {amount_str}")
            return

        if amount <= 0:
            caller.msg("Amount must be positive.")
            return

        # Resolve which resource(s) to grant. 'all' grants every canonical
        # resource; otherwise the token must match a known resource type
        # (case-insensitively) — an unknown name is REJECTED rather than
        # silently minting a junk resource like a literal "all" (the reported
        # bug), which would then pollute the player's resource dict forever.
        canonical = {r.lower(): r for r in RESOURCE_TYPES}
        if resource_token.lower() == "all":
            resources = list(RESOURCE_TYPES)
        else:
            resolved = canonical.get(resource_token.lower())
            if resolved is None:
                valid = ", ".join(RESOURCE_TYPES)
                caller.msg(
                    f"Unknown resource '{resource_token}'. "
                    f"Valid: {valid} (or 'all')."
                )
                return
            resources = [resolved]

        # Resolve target: specified player or self
        if player_name:
            target = self.resolve_player(player_name)
            if target is None:
                return
        else:
            target = caller

        if not hasattr(target, "add_resource"):
            target_name = getattr(target, "key", "target")
            caller.msg(f"{target_name} is not a valid player character.")
            return

        # Admin override: give resources directly, bypassing the carry-weight
        # cap (Req 16.7 — admins are exempt). We intentionally do NOT route this
        # through EquipmentSystem.add_resource_capped, keeping it the simplest
        # correct path for an admin grant.
        for resource_type in resources:
            target.add_resource(resource_type, amount)

        target_name = getattr(target, "key", "?")
        granted = "all resources" if len(resources) > 1 else resources[0]
        caller.msg(f"Gave {amount} {granted} to {target_name}.")

        self._log_admin("give", f"{amount} {granted} to {target_name}")

        # Notify the target if they have msg and are not the caller
        if hasattr(target, "msg") and target is not caller:
            target.msg(
                f"|y[Admin] You received {amount} {granted} "
                f"from {caller.key}.|n"
            )

    def sub_reset(self, args):
        """Reset player(s) to starting resources.

        Args:
            args: "[player]" — if specified, reset just that player;
                  if empty, reset all players.
        """
        caller = self.caller
        player_name = args.strip() if args else ""

        if player_name:
            # Reset a single player
            target = self.resolve_player(player_name)
            if target is None:
                return

            try:
                from typeclasses.characters import STARTING_RESOURCES
            except ImportError:
                caller.msg("Could not load starting resource definitions.")
                return

            try:
                target.attributes.add("resources", dict(STARTING_RESOURCES))
            except Exception:
                caller.msg(f"Failed to reset resources for {target.key}.")
                return

            self._log_admin("reset", f"resources for {target.key}")
            caller.msg(f"Reset {target.key} to starting resources.")
        else:
            # Reset all players
            try:
                from typeclasses.characters import STARTING_RESOURCES
                from evennia.objects.models import ObjectDB

                characters = list(
                    ObjectDB.objects.filter(db_attributes__db_key="combat_xp")
                )
            except Exception:
                caller.msg("Could not query player characters from the database.")
                return

            if not characters:
                caller.msg("No player characters found in the database.")
                return

            updated = 0
            for char in characters:
                try:
                    char.attributes.add("resources", dict(STARTING_RESOURCES))
                    updated += 1
                except Exception:
                    logger.exception(
                        "Failed to reset resources for %s",
                        getattr(char, "key", "?"),
                    )

            self._log_admin("reset", f"resources for {updated} character(s)")
            caller.msg(f"Reset {updated} player(s) to starting resources.")

    subcommands = {
        "give": (sub_give, "Give resources to a player", "Builder"),
        "reset": (sub_reset, "Reset player(s) to starting resources", "Admin"),
    }


class CmdAdminItem(AdminSubcommandRouter):
    """Spawn equipment, weapons, and supplies for players.

    Usage:
      @item spawn <key> [count] [player]
      @item list [filter]

    Options:
      <key>     item key or full name (assault_rifle | "Assault Rifle")
      [count]   how many to grant (default 1)
      [player]  recipient; defaults to you
      [filter]  restrict 'list' to a category (weapon, armor, accessory,
                ammo, consumable, throwable) or a slot (torso, weapon, ...)

    Subcommands:
      spawn — Grant item(s) to a player, bypassing cost (Builder+)
      list  — List item definitions available to spawn (Builder+)

    Gear (armor/weapon/accessory) is created as equippable object(s) in the
    recipient's inventory; Supplies (ammo/consumable/throwable) are added to
    their Supply_Bag as counts. Admin grants bypass the carry-weight cap,
    matching '@resource give'.
    """

    key = "@item"

    def sub_spawn(self, args):
        """Grant item(s) to a player, bypassing production cost.

        Args:
            args: "<key> [count] [player]"
        """
        caller = self.caller
        if not args:
            caller.msg("Usage: @item spawn <key> [count] [player]")
            return

        parts = args.split()
        token = parts[0]

        # Optional [count] then optional [player]. The first extra token is a
        # count only if it parses as an int; otherwise it's a player name.
        count = 1
        player_name = None
        rest = parts[1:]
        if rest:
            try:
                count = int(rest[0])
                rest = rest[1:]
            except ValueError:
                pass  # first extra token is a player name, not a count
            if count < 1:
                caller.msg("Count must be at least 1.")
                return
        if rest:
            player_name = rest[0]

        # Resolve the item definition. Accept an index (``#3`` or ``3``, as shown
        # by '@item list'), a key, a full name, or an unambiguous prefix — all
        # typo-tolerant via the shared registry resolver.
        registry = _get_system(caller, "registry")
        if registry is None:
            caller.msg("Data Registry unavailable.")
            return
        item_def = _resolve_by_index(token, self._item_index(registry))
        if item_def is None:
            resolver = getattr(registry, "resolve_item", None)
            if callable(resolver):
                item_def = resolver(token)
        if item_def is None and hasattr(registry, "get_item"):
            try:
                item_def = registry.get_item(token)
            except KeyError:
                item_def = None
        if item_def is None:
            caller.msg(
                f"Unknown item '{token}'. Use '@item list' to see valid keys "
                f"and index numbers."
            )
            return

        # Resolve recipient: named player or self.
        if player_name:
            target = self.resolve_player(player_name)
            if target is None:
                return
        else:
            target = caller

        from world.constants import GEAR_CATEGORIES
        is_gear = item_def.category in GEAR_CATEGORIES
        if is_gear:
            granted = self._spawn_gear(target, item_def, count)
        else:
            granted = self._grant_supply(target, item_def, count)

        target_name = getattr(target, "key", "?")
        if granted <= 0:
            caller.msg(f"Failed to grant {item_def.name} to {target_name}.")
            return

        kind = "gear" if is_gear else "supplies"
        suffix = ""
        if granted < count:
            suffix = f" ({count - granted} exceeded the stack cap)"
        self._log_admin("spawn", f"{granted}x {item_def.key} for {target_name}")
        caller.msg(
            f"Spawned {granted}x {item_def.name} ({kind}) for {target_name}{suffix}."
        )
        if hasattr(target, "msg") and target is not caller:
            target.msg(
                f"|y[Admin] You received {granted}x {item_def.name} "
                f"from {caller.key}.|n"
            )

    def _spawn_gear(self, target, item_def, count):
        """Create *count* equippable GameItem objects in *target*'s inventory.

        Returns the number successfully created.
        """
        from typeclasses.objects import create_game_item

        created = 0
        for _ in range(count):
            try:
                create_game_item(target, item_def)
                created += 1
            except Exception:
                logger.exception("Failed to create item %s", item_def.key)
                break
        return created

    def _grant_supply(self, target, item_def, count):
        """Add up to *count* units of a Supply to *target*'s Supply_Bag.

        Respects the per-entry ``max_stack`` cap (the data-structure limit);
        returns the number actually added.
        """
        equipment = getattr(target, "equipment", None)
        if equipment is None or not hasattr(equipment, "add_supply"):
            return 0
        return int(
            equipment.add_supply(item_def.key, count, max_stack=item_def.max_stack)
        )

    @staticmethod
    def _item_index(registry):
        """Return the stable, sorted list of ItemDefs '@item list' numbers.

        The single ordering shared by ``sub_list`` (which prints the 1-based
        index alongside each item) and ``sub_spawn`` (which resolves an index the
        operator typed), so ``@item spawn N`` always maps to the row shown as
        ``[N]``. Sorted by (category, key) — deterministic across reloads.
        """
        items = getattr(registry, "items", None) if registry else None
        if not items:
            return []
        return sorted(items.values(), key=lambda d: (d.category, d.key))

    def sub_list(self, args):
        """List item definitions available to spawn, grouped by category.

        Each item is numbered with a stable 1-based index; ``@item spawn <N>``
        (or ``#N``) spawns that item, so an operator can reference an item by
        index instead of typing its full key.

        Args:
            args: "[filter]" — optional category or slot to restrict the list.
        """
        caller = self.caller
        registry = _get_system(caller, "registry")
        ordered = self._item_index(registry)
        if not ordered:
            caller.msg("No item definitions loaded.")
            return

        filt = args.strip().lower() if args else ""

        lines = ["|w=== Item definitions (spawn by name or [index]) ===|n"]
        shown = 0
        current_cat = None
        for idx, d in enumerate(ordered, start=1):
            if filt and filt not in (d.category.lower(), (d.slot or "").lower()):
                continue
            if d.category != current_cat:
                current_cat = d.category
                lines.append(f"|c{current_cat}|n")
            slot = f" slot={d.slot}" if d.slot else ""
            lines.append(f"  |w[{idx}]|n {d.key:<18s} {d.name}{slot} wt={d.weight:g}")
            shown += 1

        if shown == 0:
            caller.msg(f"No items match '{filt}'.")
            return
        self._log_admin("list", f"items filter='{filt}'")
        caller.msg("\n".join(lines))

    subcommands = {
        "spawn": (sub_spawn, "Spawn item(s) for a player", "Builder"),
        "list": (sub_list, "List item definitions", "Builder"),
    }


class CmdAdminPlayer(AdminSubcommandRouter):
    """Manage player level and rank.

    Usage:
        @player level <N> [player]
        @player rank <N> [player]

    Subcommands:
        level — Set a player's level (Admin+)
        rank  — Set a player's rank (Admin+)

    If [player] is omitted, targets the caller.

    """

    key = "@player"

    def sub_level(self, args):
        """Set a player's level by numeric value (1-MAX_LEVEL).

        Args:
            args: "<N> [player]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @player level <N> [player]")
            return

        parts = args.strip().split()
        level = self.parse_int(parts[0], "Level")
        if level is None:
            return

        player_name = parts[1] if len(parts) >= 2 else None

        from world.constants import MAX_LEVEL
        if level < 1 or level > MAX_LEVEL:
            caller.msg(f"Level must be between 1 and {MAX_LEVEL}.")
            return

        # Resolve target: specified player or self
        if player_name:
            target = self.resolve_player(player_name)
            if target is None:
                return
        else:
            target = caller

        if not hasattr(target, "db"):
            target_name = getattr(target, "key", "target")
            caller.msg(f"{target_name} is not a valid player character.")
            return

        # Compute rank from level
        from world.systems.rank_system import rank_from_level
        rank_num = rank_from_level(level)

        # Set XP to the threshold for this level
        rank_system = _get_system(caller, "rank_system")
        if rank_system:
            xp = rank_system.xp_for_level(level)
            target.db.combat_xp = xp
        else:
            xp = None

        target.db.level = level
        target.db.rank_level = rank_num

        # Trigger rank events (unlock techs, adjust agent cap)
        if rank_system:
            rank_system.check_promotion(target)

        # Look up rank name
        rank_name = f"Rank {rank_num}"
        registry = _get_system(caller, "registry")
        if registry and hasattr(registry, "ranks"):
            rank_def = next((r for r in registry.ranks if r.level == rank_num), None)
            if rank_def:
                rank_name = rank_def.name

        target_name = getattr(target, "key", "?")
        xp_str = f", XP={xp}" if xp is not None else ""
        self._log_admin("level", f"set {target_name} to level {level} ({rank_name}{xp_str})")
        caller.msg(f"Set {target_name} to level {level} ({rank_name}{xp_str}).")

    def sub_rank(self, args):
        """Set a player's rank by numeric rank ID (1-NUM_RANKS).

        Args:
            args: "<N> [player]"
        """
        caller = self.caller

        if not args:
            caller.msg("Usage: @player rank <N> [player]")
            return

        parts = args.strip().split()
        rank_id = self.parse_int(parts[0], "Rank ID")
        if rank_id is None:
            return

        player_name = parts[1] if len(parts) >= 2 else None

        from world.constants import NUM_RANKS
        if rank_id < 1 or rank_id > NUM_RANKS:
            caller.msg(f"Rank ID must be between 1 and {NUM_RANKS}.")
            return

        # Resolve target: specified player or self
        if player_name:
            target = self.resolve_player(player_name)
            if target is None:
                return
        else:
            target = caller

        if not hasattr(target, "db"):
            target_name = getattr(target, "key", "target")
            caller.msg(f"{target_name} is not a valid player character.")
            return

        # Convert rank to level: first level of that rank
        from world.systems.rank_system import level_range_for_rank
        level, _ = level_range_for_rank(rank_id)

        # Set XP to the threshold for this level
        rank_system = _get_system(caller, "rank_system")
        if rank_system:
            xp = rank_system.xp_for_level(level)
            target.db.combat_xp = xp
        else:
            xp = None

        target.db.level = level
        target.db.rank_level = rank_id

        # Trigger rank events (unlock techs, adjust agent cap)
        if rank_system:
            rank_system.check_promotion(target)

        # Look up rank name
        rank_name = f"Rank {rank_id}"
        registry = _get_system(caller, "registry")
        if registry and hasattr(registry, "ranks"):
            rank_def = next((r for r in registry.ranks if r.level == rank_id), None)
            if rank_def:
                rank_name = rank_def.name

        target_name = getattr(target, "key", "?")
        xp_str = f", XP={xp}" if xp is not None else ""
        self._log_admin("rank", f"set {target_name} to {rank_name} (rank {rank_id}, level {level}{xp_str})")
        caller.msg(f"Set {target_name} to {rank_name} (rank {rank_id}, level {level}{xp_str}).")

    subcommands = {
        "level": (sub_level, "Set a player's level", "Admin"),
        "rank": (sub_rank, "Set a player's rank", "Admin"),
    }


class CmdTeleport(BaseCommand):
    """Teleport to coordinates — or to any entity on the overworld.

    Usage:
      @teleport <x> <y> [planet]
      goto <x> <y> [z]
      goto <name>

    Options:
      <x> <y>   destination coordinates (spaces or commas: "25 25" or "25,25")
      [planet]  optional target planet by name, prefix, or z-level (0/1/2);
                defaults to your current planet
      <name>    an entity to jump to — a player, NPC, building, or item, by
                name or unambiguous prefix. You are placed on its tile.

    Examples:
      @teleport 25 25
      @teleport 50 50 earth
      goto 25 25
      goto 50 50 2
      goto Raider          (jump to the player Raider)
      goto agent           (jump to the nearest/only matching NPC)
      goto HQ              (jump to a building)

    Notes:
      Aliases: @tel, goto. A leading number is read as coordinates; anything
      else is resolved as an entity name. Builder+ only.
    """

    key = "@teleport"
    aliases = ["@tel", "goto"]
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    _USAGE = (
        "Usage: goto <x> <y> [planet]  |  goto <name>  "
        "(commas optional: <x>,<y>)"
    )

    def func(self):
        caller = self.caller
        args = self.args.strip()
        if not args:
            caller.msg(self._USAGE)
            return

        # A leading number → coordinate teleport; anything else → jump to a
        # named entity. (An entity name never starts with a digit, so this
        # disambiguation is unambiguous.)
        first = args.replace(",", " ").split()[0]
        if first.lstrip("-").isdigit():
            self._teleport_to_coords(caller, args)
        else:
            self._teleport_to_entity(caller, args)

    # ------------------------------------------------------------------ #
    #  goto <x> <y> [planet]
    # ------------------------------------------------------------------ #
    def _teleport_to_coords(self, caller, args):
        # Accept commas or spaces interchangeably between all parts, so
        # "25 25", "25,25", "50 50 earth", and "50,50,earth" all parse — the
        # same coordinate convention the 'throw' command uses.
        parts = args.replace(",", " ").split()
        if len(parts) < 2:
            caller.msg(self._USAGE)
            return

        try:
            tx = int(parts[0])
            ty = int(parts[1])
        except ValueError:
            caller.msg("Coordinates must be integers.")
            return

        # Get registry for planet resolution
        registry = _get_system(caller, "planet_registry")
        if registry is None:
            caller.msg("Planet registry not available.")
            return

        if len(parts) >= 3:
            planet = registry.resolve_planet(parts[2])
            if planet is None:
                caller.msg(f"Unknown planet '{parts[2]}'. Use a name, prefix, or z-level (0/1/2).")
                return
        else:
            planet = getattr(caller.db, "coord_planet", None)
            if not planet:
                caller.msg("No planet specified and no current planet set.")
                return

        # Validate bounds
        if not registry.is_valid_coordinate(tx, ty, planet):
            caller.msg(f"Coordinates ({tx}, {ty}) are out of bounds for {planet}.")
            return

        self._do_teleport(caller, tx, ty, planet)

    # ------------------------------------------------------------------ #
    #  goto <name> — jump to a player/NPC/building/item's tile
    # ------------------------------------------------------------------ #
    def _teleport_to_entity(self, caller, name):
        target = self._resolve_entity(caller, name)
        if target is None:
            caller.msg(
                f"No entity named '{name}' found. Use a name or unambiguous "
                f"prefix, or 'goto <x> <y>' for coordinates."
            )
            return

        from world.utils import get_coords

        coords = get_coords(target)
        planet = getattr(getattr(target, "db", None), "coord_planet", None)
        if coords is None or not planet:
            tname = getattr(target, "key", "that")
            caller.msg(f"{tname} is not on the overworld — it has no location to go to.")
            return

        registry = _get_system(caller, "planet_registry")
        if registry is not None:
            try:
                in_bounds = registry.is_valid_coordinate(coords[0], coords[1], planet)
            except KeyError:
                # The entity's planet isn't a registered planet (legacy/bad data).
                caller.msg(
                    f"{getattr(target, 'key', 'that')} is on an unknown planet "
                    f"'{planet}' — cannot go there."
                )
                return
            if not in_bounds:
                caller.msg(
                    f"{getattr(target, 'key', 'that')} is at ({coords[0]}, "
                    f"{coords[1]}) on {planet}, which is out of bounds."
                )
                return

        self._do_teleport(
            caller, coords[0], coords[1], planet,
            label=getattr(target, "key", None),
        )

    @staticmethod
    def _resolve_entity(caller, name):
        """Resolve *name* to a single overworld entity (or None).

        On multiple matches, picks the closest by Chebyshev distance so an
        ambiguous prefix (e.g. two Agents) lands somewhere sensible rather than
        erroring. Excludes the caller itself. See :func:`_search_entities` for
        the search order (local prefix search, then global exact fallback).

        This is the RIGHT behavior for 'goto' — jumping the caller to *some*
        match is harmless. 'transfer', which moves someone ELSE'S unit, must not
        guess, so it lists the ambiguous candidates instead of picking one.
        """
        candidates = _search_entities(caller, name)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Multiple hits — pick the nearest with coordinates.
        from world.utils import get_coords, chebyshev_distance

        cx = getattr(caller.db, "coord_x", None)
        cy = getattr(caller.db, "coord_y", None)

        def _rank(obj):
            c = get_coords(obj)
            if c is None:
                return (1, 0)  # entities with no coords sort last
            if cx is None or cy is None:
                return (0, 0)
            return (0, chebyshev_distance(cx, cy, c[0], c[1]))

        return sorted(candidates, key=_rank)[0]

    # ------------------------------------------------------------------ #
    #  Shared relocation
    # ------------------------------------------------------------------ #
    def _do_teleport(self, caller, tx, ty, planet, label=None):
        """Move *caller* to ``(tx, ty, planet)`` and show the destination.

        Shared by the coordinate and entity paths. Handles the cross-planet
        move + coordinate-index bookkeeping + the single correct look.
        """
        target_room = _resolve_planet_room(caller, planet)
        if target_room is None:
            return

        # move_entity(notify=False) + the cross-planet index bookkeeping is
        # shared with 'transfer' (which pulls another entity here) — see
        # _relocate_object for the move_hooks=False / notify=False rationale.
        _relocate_object(caller, target_room, tx, ty, planet)

        logger.info("Admin %s teleported to (%d, %d, %s)", caller.key, tx, ty, planet)
        if label:
            caller.msg(f"Teleported to |c{label}|n at ({tx}, {ty}) on {planet}.")
        else:
            caller.msg(f"Teleported to ({tx}, {ty}) on {planet}.")

        # Always show the destination after teleporting, now that ALL coords +
        # planet are fully updated. A same-planet (X/Y-only) teleport fires no
        # arrival hook at all, and the cross-planet move above suppressed its
        # (stale-coord) auto-look — so this single explicit look is the one
        # correct view (appearance + map + tile summary) for every teleport,
        # regardless of which coordinate changed.
        if hasattr(caller, "execute_cmd"):
            caller.execute_cmd("look")


class CmdTransfer(BaseCommand):
    """Pull an entity to your current tile — the inverse of 'goto'.

    Usage:
      transfer <name>
      transfer <name> owner=<player>
      transfer #<id> owner=<player>

    Options:
      <name>          a movable unit to summon — a player, agent, or NPC — by
                      name or unambiguous prefix. It is moved to YOUR tile
                      (and planet).
      owner=<player>  disambiguate co-named units by their owner. Agents are all
                      named 'Agent-<n>', so 'transfer Agent-1 owner=Raider'
                      pulls Raider's agent, not yours. Accepts a name or prefix.
      #<id>           with owner=, selects that owner's agent by its stable
                      agent ID (e.g. 'transfer #3 owner=Raider') — the surest
                      way to name a specific agent.

    Examples:
      transfer Scout            (pull the player/NPC 'Scout' to you)
      transfer Agent-2          (pull YOUR Agent-2, if unambiguous)
      transfer #3 owner=Raider  (pull Raider's agent #3 to you)
      transfer Guard-1 owner=Outpost #2   (pull that base's guard)

    Notes:
      Builder+ only. Only movable units (players, agents, NPCs) can be
      transferred — buildings and dropped items are fixed to their tile. If a
      name matches several units, they're listed with their owners so you can
      re-run with 'owner=' to pick one.
    """

    key = "transfer"
    aliases = ["@transfer", "summon"]
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    _USAGE = (
        "Usage: transfer <name> [owner=<player>]  |  "
        "transfer #<id> owner=<player>"
    )

    def func(self):
        caller = self.caller
        args = self.args.strip()
        if not args:
            caller.msg(self._USAGE)
            return

        name, owner_name = self._split_owner(args)
        if not name:
            caller.msg(self._USAGE)
            return

        target = self._resolve_unit(caller, name, owner_name)
        if target is None:
            return  # _resolve_unit already messaged the caller

        # Movable units only. Buildings/items are fixed to their tile — pulling
        # one would corrupt the coordinate index (two things claim a tile) and
        # makes no sense for a fixed structure. is_player() is True for players
        # AND all combat NPCs (they carry combat_xp); GameEntity-only buildings/
        # items read None and are excluded.
        from world.utils import is_player
        if not is_player(target):
            tname = getattr(target, "key", "that")
            caller.msg(
                f"{tname} is not a movable unit — only players, agents, and "
                f"NPCs can be transferred."
            )
            return

        self._pull_to_caller(caller, target)

    @staticmethod
    def _split_owner(args):
        """Split ``"<name> owner=<player>"`` into ``(name, owner_name|None)``.

        ``owner=`` may appear anywhere; everything before it is the unit name,
        everything after is the owner name (which may itself contain spaces,
        e.g. 'Outpost #2'). Returns ``owner_name=None`` when no ``owner=`` given.
        """
        lower = args.lower()
        marker = "owner="
        pos = lower.find(marker)
        if pos == -1:
            return args.strip(), None
        name = args[:pos].strip()
        owner_name = args[pos + len(marker):].strip()
        return name, (owner_name or None)

    def _resolve_unit(self, caller, name, owner_name):
        """Resolve *name* (+ optional *owner_name*) to a single unit, or None.

        Messages the caller on no-match or ambiguity (listing co-named
        candidates with their owners) and returns None in those cases, so the
        caller just bails on None.
        """
        # An explicit owner + '#<id>' or bare agent name: resolve via the owner's
        # roster, which is the authoritative, unambiguous per-owner lookup.
        if owner_name is not None:
            resolved = self._resolve_by_owner(caller, name, owner_name)
            # _resolve_by_owner messages + returns None on any failure.
            return resolved

        candidates = _search_entities(caller, name)
        if not candidates:
            caller.msg(
                f"No unit named '{name}' found. Use a name or unambiguous "
                f"prefix; add 'owner=<player>' to disambiguate agents/NPCs."
            )
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Ambiguous — do NOT guess when moving someone else's unit. List the
        # matches with their owners so the operator can re-run with 'owner='.
        self._report_ambiguous(caller, name, candidates)
        return None

    def _resolve_by_owner(self, caller, name, owner_name):
        """Resolve an owned unit by its owner (+ '#id' or a name).

        Returns the unit, or None after messaging. Two selectors:

        * ``#<id>`` — the owner's agent with that stable agent ID, via the live
          agent roster (agent IDs are an agent concept). Unambiguous even when
          many players own an 'Agent-3'.
        * a name/prefix — matched against the owner's units found by name, then
          filtered to those actually owned by *owner*. This covers ANY owned
          unit (agents AND enemy base guards), not just the agent roster.
        """
        owner_disp = owner_name
        owner = None
        if hasattr(caller, "search"):
            found = caller.search(owner_name, quiet=True)
            if found:
                owner = found[0] if isinstance(found, (list, tuple)) else found
        if owner is None:
            caller.msg(f"Could not find owner '{owner_name}'.")
            return None
        owner_disp = getattr(owner, "key", owner_name)

        # '#<id>' or bare digits → select that owner's agent by stable ID.
        idn = _parse_index_token(name)
        if idn is not None:
            agent_system = _get_system(caller, "agent_system")
            roster = agent_system.get_agents(owner) if agent_system else []
            match = next(
                (a for a in roster if getattr(a.db, "agent_id", None) == idn), None
            )
            if match is None:
                caller.msg(f"{owner_disp} has no agent #{idn}.")
                return None
            return match

        # A name/prefix → search by name, keep only units owned by *owner*. Works
        # for agents and enemy NPCs alike (both carry db.owner).
        candidates = [
            c for c in _search_entities(caller, name)
            if getattr(getattr(c, "db", None), "owner", None) is owner
        ]
        if not candidates:
            caller.msg(
                f"{owner_disp} has no unit matching '{name}'. Try "
                f"'transfer #<id> owner={owner_disp}' or '@agent list {owner_disp}'."
            )
            return None
        if len(candidates) > 1:
            self._report_ambiguous(caller, name, candidates)
            return None
        return candidates[0]

    @staticmethod
    def _report_ambiguous(caller, name, candidates):
        """List co-named candidates with owner + coords so the op can pick one."""
        from world.utils import get_coords

        lines = [
            f"|yMultiple units match '{name}'|n — add 'owner=<player>' to pick one:"
        ]
        for c in candidates:
            owner = _owner_label(c)
            owner_tag = f" owner={owner}" if owner else " (unowned)"
            coords = get_coords(c)
            loc = f" at ({coords[0]}, {coords[1]})" if coords else ""
            lines.append(f"  |c{getattr(c, 'key', '?')}|n{owner_tag}{loc}")
        caller.msg("\n".join(lines))

    def _pull_to_caller(self, caller, target):
        """Move *target* to the caller's tile + planet, then re-render for both."""
        planet = getattr(caller.db, "coord_planet", None)
        tx = getattr(caller.db, "coord_x", None)
        ty = getattr(caller.db, "coord_y", None)
        if not planet or tx is None or ty is None:
            caller.msg("You have no overworld position to transfer a unit to.")
            return

        target_room = _resolve_planet_room(caller, planet)
        if target_room is None:
            return

        _relocate_object(target, target_room, int(tx), int(ty), planet)

        tname = getattr(target, "key", "the unit")
        owner = _owner_label(target)
        owner_tag = f" ({owner}'s)" if owner else ""
        logger.info(
            "Admin %s transferred %s%s to (%s, %s, %s)",
            caller.key, tname, owner_tag, tx, ty, planet,
        )
        caller.msg(
            f"Transferred |c{tname}|n{owner_tag} to your tile ({tx}, {ty}) on {planet}."
        )

        # Tell the summoned unit it was moved and refresh ITS view (a puppeted
        # player would otherwise see a stale map until their next action; agents/
        # NPCs have neither msg nor execute_cmd, so both calls are guarded).
        if target is not caller:
            if hasattr(target, "msg"):
                target.msg(
                    f"|yYou have been transferred to {caller.key}'s location.|n"
                )
            if hasattr(target, "execute_cmd"):
                target.execute_cmd("look")

        # Refresh the caller's view so the arriving unit shows on the tile summary.
        if hasattr(caller, "execute_cmd"):
            caller.execute_cmd("look")


class CmdClearFog(BaseCommand):
    """Clear a player's fog of war discovery memory.

    Usage:
        @clearfog [player]

    If no player is specified, clears your own fog.
    Restricted to Builder+ permission level.
    """

    key = "@clearfog"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        target_name = self.args.strip()

        if target_name:
            target = caller.search(target_name, quiet=True) if hasattr(caller, "search") else None
            if not target:
                caller.msg(f"Could not find player '{target_name}'.")
                return
            target = target[0] if isinstance(target, list) else target
        else:
            target = caller

        if hasattr(target, "db"):
            target.db.discovery_memory = {"discovered": {}, "buildings": {}}

        name = getattr(target, "key", "?")
        logger.info("Admin %s cleared fog of war for %s", caller.key, name)
        caller.msg(f"Cleared fog of war for {name}.")


class CmdPurgeRooms(BaseCommand):
    """Delete all legacy OverworldRoom objects from the database.

    Removes leftover OverworldRoom objects as a one-time migration cleanup
    (the game now uses a single PlanetRoom per planet).

    Usage:
      @purgerooms

    Notes:
      Builder+ only. This is a destructive, irreversible cleanup — run it
      only when you know the legacy rooms are no longer needed.
    """

    key = "@purgerooms"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        try:
            from evennia.utils.search import search_tag

            all_rooms = list(search_tag("overworld_tile", category="room_type"))
        except Exception:
            caller.msg("Could not query overworld rooms.")
            return

        deleted = 0
        for room in all_rooms:
            room.delete()
            deleted += 1

        logger.info(
            "Admin %s purged %d legacy OverworldRoom objects",
            caller.key, deleted,
        )
        caller.msg(f"Purged {deleted} legacy OverworldRoom object(s).")


class CmdMigrate(BaseCommand):
    """Ensure all players have valid attributes.

    Usage:
        @migrate

    Reads PLAYER_DEFAULTS from characters.py and ensures every player
    has all attributes with valid (non-None) values. Only fills in
    missing attributes — never overwrites existing data.

    Run this after adding new player attributes to the codebase.

    Restricted to Admin+ permission level.
    """

    key = "@migrate"
    locks = "cmd:perm(Admin);view:perm(Admin)"
    help_category = "Admin"

    def func(self):
        caller = self.caller

        if not _check_perm(caller, "Admin"):
            caller.msg("Permission denied. Admin+ required.")
            return

        try:
            from typeclasses.characters import PLAYER_DEFAULTS
            from evennia.objects.models import ObjectDB

            characters = list(
                ObjectDB.objects.filter(db_attributes__db_key="combat_xp")
            )
        except Exception:
            caller.msg("Could not query player characters from the database.")
            return

        if not characters:
            caller.msg("No player characters found in the database.")
            return

        updated = 0
        attrs_added = 0
        for char in characters:
            try:
                for key, default in PLAYER_DEFAULTS.items():
                    current = char.attributes.get(key)
                    if current is None:
                        import copy
                        char.attributes.add(key, copy.deepcopy(default))
                        attrs_added += 1
                updated += 1
            except Exception:
                logger.exception("Failed to migrate %s", getattr(char, "key", "?"))

        logger.info("Admin %s migrated %d characters (%d attrs added)", caller.key, updated, attrs_added)
        caller.msg(f"Migrated {updated} player(s). {attrs_added} missing attribute(s) filled in.")


class CmdAdminOutpost(AdminSubcommandRouter):
    """Spawn and inspect NPC bases (outposts/fortresses).

    Usage:
        @outpost spawn <tier> [x y]
        @outpost list
        @outpost tiers

    Options:
        <tier>   template tier ("outpost" or "fortress"), an unambiguous prefix
                 ("fort"), or an index (#2) from '@outpost tiers'
        [x y]    HQ tile coordinates; defaults to your current tile

    Subcommands:
        spawn — Spawn an NPC base at a tile (Builder+)
        list  — List active NPC bases (Builder+)
        tiers — List spawnable base tiers with index numbers (Builder+)

    """

    key = "@outpost"

    @staticmethod
    def _tier_index(registry):
        """Return the stable, sorted list of base-template tier names.

        Shared by ``sub_tiers`` (prints the 1-based index) and ``sub_spawn``
        (resolves an index/prefix the operator typed), so ``@outpost spawn N``
        maps to the tier shown as ``[N]``. Sorted alphabetically — deterministic.
        """
        templates = getattr(registry, "base_templates", None) if registry else None
        if not templates:
            return []
        return sorted(templates.keys())

    def _resolve_tier(self, registry, token):
        """Resolve *token* to a base-template tier name, or ``None``.

        Accepts an index (``#2`` / ``2`` from '@outpost tiers'), an exact tier
        name, or an unambiguous prefix — mirroring the item/building resolvers so
        the operator needn't type the full tier. Case-insensitive.
        """
        tiers = self._tier_index(registry)
        if not tiers:
            # No template metadata available (e.g. minimal test spawner): fall
            # back to the raw lowercased token; the spawner validates it.
            return token.lower()
        by_index = _resolve_by_index(token, tiers)
        if by_index is not None:
            return by_index
        norm = token.strip().lower()
        if norm in tiers:
            return norm
        prefixed = [t for t in tiers if t.startswith(norm)]
        if len(prefixed) == 1:
            return prefixed[0]
        return None

    def sub_spawn(self, args):
        """Spawn an NPC base of a tier at the caller's tile (or given x y)."""
        caller = self.caller
        spawner = self.require_system("outpost_spawner", "Outpost spawner")
        if spawner is None:
            return

        parts = args.split()
        if not parts:
            caller.msg("Usage: @outpost spawn <tier> [x y]")
            return
        registry = _get_system(caller, "registry")
        tier = self._resolve_tier(registry, parts[0])
        if tier is None:
            valid = ", ".join(self._tier_index(registry)) or "none loaded"
            caller.msg(
                f"Unknown or ambiguous tier '{parts[0]}'. Valid: {valid}. "
                f"Use '@outpost tiers' for index numbers."
            )
            return

        planet = getattr(caller.db, "coord_planet", None)
        if not planet:
            caller.msg("You have no planet position to spawn a base on.")
            return

        coords = None
        if len(parts) >= 3:
            x = self.parse_int(parts[1], "X")
            y = self.parse_int(parts[2], "Y")
            if x is None or y is None:
                return
            coords = (x, y)
        else:
            cx = getattr(caller.db, "coord_x", None)
            cy = getattr(caller.db, "coord_y", None)
            if cx is not None and cy is not None:
                coords = (int(cx), int(cy))

        base = spawner.spawn_base(planet, tier, coords=coords)
        if base is None:
            caller.msg(
                f"Could not spawn {tier!r} base "
                f"(unknown tier or no valid placement)."
            )
            return
        self._log_admin("spawn", f"{tier} at {base['x']},{base['y']} on {planet}")
        caller.msg(
            f"|gSpawned {tier} base|n at ({base['x']}, {base['y']}) on {planet}."
        )

    def sub_list(self, args):
        """List active NPC bases the spawner is tracking."""
        caller = self.caller
        spawner = self.require_system("outpost_spawner", "Outpost spawner")
        if spawner is None:
            return
        bases = list(getattr(spawner, "_active_bases", {}).values())
        if not bases:
            caller.msg("No active NPC bases.")
            return
        lines = ["|wActive NPC bases:|n"]
        for rec in bases:
            lines.append(
                f"  {rec['tier']} at ({rec['x']}, {rec['y']}) on {rec['planet']}"
            )
        caller.msg("\n".join(lines))

    def sub_tiers(self, args):
        """List spawnable base tiers with a stable 1-based index.

        ``@outpost spawn <N>`` (or ``#N``) spawns the tier shown as ``[N]``.
        """
        caller = self.caller
        registry = _get_system(caller, "registry")
        tiers = self._tier_index(registry)
        if not tiers:
            caller.msg("No base tiers loaded.")
            return
        lines = ["|w=== Base tiers (spawn by name or [index]) ===|n"]
        templates = getattr(registry, "base_templates", {}) or {}
        for idx, tier in enumerate(tiers, start=1):
            tmpl = templates.get(tier)
            display = getattr(tmpl, "display_name", "") if tmpl else ""
            suffix = f" — {display}" if display and display.lower() != tier else ""
            lines.append(f"  |w[{idx}]|n {tier}{suffix}")
        self._log_admin("tiers", "base tiers")
        caller.msg("\n".join(lines))

    subcommands = {
        "spawn": (sub_spawn, "Spawn an NPC base at a tile", "Builder"),
        "list": (sub_list, "List active NPC bases", "Builder"),
        "tiers": (sub_tiers, "List spawnable base tiers with index numbers", "Builder"),
    }


class CmdAdminAlliance(AdminSubcommandRouter):
    """Inspect and moderate alliances (staff).

    Usage:
        @alliance list
        @alliance inspect <tag>
        @alliance disband <tag>
        @alliance kick <tag> <player>
        @alliance transfer <tag> <player>
        @alliance rename <tag> <new name> = <new tag>

    Every write verb routes its mutation THROUGH the AllianceSystem (the single
    writer), so the single-writer invariant holds even for staff actions.
    Inspect/list read full state (treasury, pending invites/requests) bypassing
    the normal member/outsider scoping.
    """

    key = "@alliance"

    def _system(self):
        return self.require_system("alliance_system", "Alliance system")

    def _find(self, system, tag):
        """Resolve an alliance by tag, or msg + return None."""
        rec = system._alliances.by_tag(tag) if system._alliances else None
        if rec is None:
            self.caller.msg(f"No alliance with tag '{tag}'.")
        return rec

    def _resolve_member_by_name(self, system, record, name):
        """Resolve a roster member of *record* by (case-insensitive) name."""
        from world.systems.alliance_system import _roster_ids
        for cid in _roster_ids(record):
            obj = system._resolve_member(cid)
            if obj is not None and getattr(obj, "key", "").lower() == name.lower():
                return obj
        self.caller.msg(f"No member '{name}' in that alliance.")
        return None

    def sub_list(self, args):
        system = self._system()
        if system is None:
            return
        alliances = system._alliances.all_alliances() if system._alliances else []
        if not alliances:
            self.caller.msg("No alliances exist.")
            return
        lines = ["|wAlliances:|n  (id / tag / name / members / level)"]
        for rec in alliances:
            lines.append(
                f"  #{rec['id']} [{rec['tag']}] {rec['name']} — "
                f"{len(system._live_members(rec['id']))} members, "
                f"level {system.compute_alliance_level(rec['id'])}"
            )
        self.caller.msg("\n".join(lines))
        self._log_admin("list", f"{len(alliances)} alliances")

    def sub_inspect(self, args):
        system = self._system()
        if system is None:
            return
        rec = self._find(system, args.strip())
        if rec is None:
            return
        summary = system.alliance_summary(rec["id"], for_member=True)
        from world.utils import format_section
        lines = [
            f"|w#{rec['id']} {rec['name']}|n [{rec['tag']}]",
            f"  Leader: {summary['leader']}  Members: {summary['member_count']}"
            f"  Level: {summary['level']}  Open-join: {summary['open_join']}",
            f"  Officers: {rec.get('officer_ids')}  Members: {rec.get('member_ids')}",
        ]
        # Treasury + active perks render as clean Key - Value rows (they are
        # mappings — a raw dict repr is unreadable); the raw id/invite lists
        # above stay as-is (admin diagnostics).
        lines.extend(format_section("Treasury", summary.get("treasury") or {}, empty="empty"))
        lines.extend(format_section(
            "Active perks",
            {k: f"L{v}" for k, v in (summary.get("active_perks") or {}).items()},
            empty="none",
        ))
        lines.append(f"  Pending invites: {summary.get('pending_invites')}")
        lines.append(f"  Pending requests: {summary.get('pending_requests')}")
        self.caller.msg("\n".join(lines))
        self._log_admin("inspect", f"#{rec['id']} {rec['tag']}")

    def sub_disband(self, args):
        system = self._system()
        if system is None:
            return
        rec = self._find(system, args.strip())
        if rec is None:
            return
        # Route through the single-writer teardown (even-split + channel destroy).
        system._do_disband(rec)
        self.caller.msg(f"Force-disbanded [{rec['tag']}] {rec['name']}.")
        self._log_admin("disband", f"#{rec['id']} {rec['tag']}")

    def sub_kick(self, args):
        system = self._system()
        if system is None:
            return
        parts = args.split(None, 1)
        if len(parts) < 2:
            self.caller.msg("Usage: @alliance kick <tag> <player>")
            return
        rec = self._find(system, parts[0])
        if rec is None:
            return
        member = self._resolve_member_by_name(system, rec, parts[1].strip())
        if member is None:
            return
        # Kicking the LEADER would strand the alliance: _remove_from_roster never
        # touches leader_id, so leader_id would dangle at the kicked player with
        # no succession (and `claim` can't recover while the ex-leader is online).
        # Refuse — staff should transfer or disband instead.
        if getattr(member, "id", None) == rec.get("leader_id"):
            self.caller.msg(
                "Cannot kick the leader — use '@alliance transfer' to hand off "
                "leadership first, or '@alliance disband'."
            )
            return
        # Force-kick through the single writer: strip from roster + clear pointer.
        system._remove_from_roster(rec, getattr(member, "id", None))
        system._alliances.put(rec)
        system._unsubscribe(member, rec["id"])
        system._clear_pointer(member)
        self.caller.msg(f"Force-kicked {member.key} from [{rec['tag']}].")
        self._log_admin("kick", f"{member.key} from #{rec['id']}")

    def sub_transfer(self, args):
        system = self._system()
        if system is None:
            return
        parts = args.split(None, 1)
        if len(parts) < 2:
            self.caller.msg("Usage: @alliance transfer <tag> <player>")
            return
        rec = self._find(system, parts[0])
        if rec is None:
            return
        member = self._resolve_member_by_name(system, rec, parts[1].strip())
        if member is None:
            return
        old_leader = system._resolve_member(rec.get("leader_id"))
        system._install_leader(rec, old_leader, member)
        self.caller.msg(f"Transferred [{rec['tag']}] leadership to {member.key}.")
        self._log_admin("transfer", f"#{rec['id']} -> {member.key}")

    def sub_rename(self, args):
        system = self._system()
        if system is None:
            return
        # "<tag> <new name> = <new tag>"
        if "=" not in args:
            self.caller.msg("Usage: @alliance rename <tag> <new name> = <new tag>")
            return
        left, new_tag = (p.strip() for p in args.split("=", 1))
        parts = left.split(None, 1)
        if len(parts) < 2:
            self.caller.msg("Usage: @alliance rename <tag> <new name> = <new tag>")
            return
        rec = self._find(system, parts[0])
        if rec is None:
            return
        new_name = parts[1].strip()
        # Validate + apply through the system (bypassing the leader/cooldown gate
        # by writing the record after a validation-only check).
        err = system._validate_name_tag(new_name, new_tag, exclude_id=rec["id"])
        if err:
            self.caller.msg(err)
            return
        old = (rec["name"], rec["tag"])
        rec["name"] = new_name
        rec["tag"] = new_tag
        system._alliances.put(rec)
        from world.event_bus import ALLIANCE_RENAMED
        system._publish(ALLIANCE_RENAMED, alliance_id=rec["id"], old=old,
                        new=(new_name, new_tag))
        self.caller.msg(f"Renamed to [{new_tag}] {new_name}.")
        self._log_admin("rename", f"#{rec['id']} -> {new_tag}")

    subcommands = {
        "list": (sub_list, "List all alliances", "Builder"),
        "inspect": (sub_inspect, "Inspect an alliance's full state", "Builder"),
        "disband": (sub_disband, "Force-disband an alliance", "Builder"),
        "kick": (sub_kick, "Force-kick a member", "Builder"),
        "transfer": (sub_transfer, "Force-transfer leadership", "Builder"),
        "rename": (sub_rename, "Rename/retag an alliance", "Builder"),
    }
