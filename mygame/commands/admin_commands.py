"""
Admin commands for the RTS Combat Overworld.

Restricted to Builder+ permission level. All executions are logged
with operator name, command, and target.

"""

from __future__ import annotations

import logging

from evennia.commands.command import Command as BaseCommand
from commands.command_router import (
    AdminSubcommandRouter,
    EntityAdminRouter,
    ValueFirstSetAliasMixin,
)
# Aliased to private names: 'goto'/'transfer' below use them, and
# they stay importable from here for backward compatibility.
from world.adapters.relocation import (
    relocate_object as _relocate_object,
    resolve_planet_room as _resolve_planet_room,
)
from world.utils import coords_of
from world.utils import get_system as _get_system, resolve_player

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


class CmdAdminBuilding(EntityAdminRouter):
    """Manage buildings under the unified admin grammar.

    Usage:
      @building list [filter]
      @building spawn <type> [owner=<name>] [level=<N>] [player]
      @building show <building>
      @building set <building> <field> <value>
      @building destroy [<building>[, <building> ...]]
      @building open [close]
      @building def list | def show <key> | def diff
      @building def set <key> <field> <value> | def reset <key> [field]

    Core verbs (shared EntityAdminRouter handlers, driven by the building
    adapter registered under ``adapter_key = "building"``):
      list    — live building instances on your current planet room;
                definition (type) listing moved to 'def list'
      spawn   — create a building at your current tile through the
                existing creation path (kwargs: owner=<name|none>,
                level=<1-5>); <type> accepts an abbreviation (EX), a full
                name (extractor), or an unambiguous prefix
      show    — full instance readout: level, HP, owner, position,
                open/offline state, modifiable fields with [min–max]
      set     — bounded field write (level 1-5, hp clamped into the
                target's hp_max, hp_max) through the shared building
                attribute writer, clamp-with-note
      destroy — delete a building instance; with no target, destroys the
                building at your current tile (legacy behavior)
      def …   — definition scope: 'def list'/'def show'/'def diff' at
                Builder; 'def set'/'def reset' (overlay-backed, validated
                reload) at Admin

    Targets resolve uniformly: '#N' from the last '@building list', the
    building key/abbreviation (e.g. 'EX'), the building name, or an
    unambiguous prefix.

    Extra verbs:
      open    — open/close the building at your tile to ranged fire
    """

    key = "@building"
    adapter_key = "building"

    def _sub_list(self, rest):
        """``list``: shared instance-rows handler + the def-list pointer.

        While the ``@building`` migration window remains open, every
        ``@building list`` includes the pointer that definition listing
        moved to ``def list`` — the old def-list meaning of ``list``
        moved there in this rollout phase (Requirement 11.4). The shared
        handler stays generic; only this subclass appends the pointer.
        """
        super()._sub_list(rest)
        self.caller.msg(
            "Note: definition listing moved to '@building def list' — "
            "'list' now shows live building instances."
        )

    def _sub_destroy(self, rest):
        """``destroy``: shared handler, with the legacy no-target form.

        ``@building destroy`` with no target keeps its pre-migration
        meaning — destroy the building at the caller's current tile —
        so existing muscle memory is never punished (Requirement 11.6).
        Targeted (and multi-target, confirmation-gated) destroys go
        through the shared handler.
        """
        if (rest or "").strip():
            super()._sub_destroy(rest)
            return
        building = self._building_at_tile()
        if building is None:
            return
        self._destroy_now([building])

    def _building_at_tile(self):
        """The first building at the caller's tile, or ``None`` (messaged)."""
        caller = self.caller

        planet_room = caller.location
        if planet_room is None:
            caller.msg("You have no location.")
            return None

        coords = coords_of(caller)
        if coords is None:
            caller.msg("You have no coordinates set.")
            return None
        cx, cy, _planet = coords

        if not hasattr(planet_room, "get_objects_at"):
            caller.msg("Current location does not support coordinate queries.")
            return None

        buildings = planet_room.get_objects_at(int(cx), int(cy), type_tag="building")
        if not buildings:
            caller.msg(f"No building found at ({cx}, {cy}).")
            return None
        return buildings[0]

    def sub_open(self, args):
        """Toggle whether the building at your tile is open or closed to ranged fire.

        Usage:
            @building open        — open it (ranged weapons/turrets can hit it)
            @building open close  — close it (only melee attacks reach it)
        """
        caller = self.caller

        planet_room = caller.location
        coords = coords_of(caller)
        if planet_room is None or coords is None:
            caller.msg("You have no coordinates set.")
            return
        cx, cy, _planet = coords
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


class CmdAdminResource(EntityAdminRouter):
    """Manage player resource balances under the unified admin grammar.

    Usage:
      @resource spawn <type|all> <amount> [player]   (grant; alias: give)
      @resource show [player]
      @resource set <player> <type> <amount>
      @resource reset [player]

    Core verbs (shared EntityAdminRouter handlers, driven by the resource
    adapter registered under ``adapter_key = "resource"``):
      spawn   — grant (credit) <amount> of a resource, or 'all' for every
                resource, to a player (defaults to you) via the existing
                add_resource single-writer; admin grants bypass the
                carry-weight cap (Req 16.7). The positional grant grammar
                is kept by the ``_sub_spawn`` override below.
      show    — one player's balances readout (defaults to you)
      set     — set an absolute balance (bounded ≥ 0, clamp with a note)
      reset   — reset one player, or every player, to STARTING_RESOURCES
                (Admin+)
      list    — not available: balances are per-player fields, not a roster
      destroy — not available: zero a balance or use 'reset' instead
      def …   — not available: resources have no YAML definition domain

    Targets resolve uniformly: 'me'/'self' (or omitted) means you; any
    other token resolves to a single player by name.

    Legacy spelling (deprecated migration alias):
      give <type|all> <amount> [player] — alias of 'spawn' (same grant)
    """

    key = "@resource"
    adapter_key = "resource"

    #: ``show [player]`` keeps the legacy "defaults to you" behavior — an
    #: omitted target becomes ``me`` (which the adapter resolves to the
    #: caller), via the shared ``_sub_show``.
    default_show_target = "me"

    def _sub_spawn(self, rest):
        """``spawn <type|all> <amount> [player]``: the grant path.

        The design maps the legacy ``give`` onto ``spawn`` (per-entity
        matrix, "A"), but the positional grant grammar and its additive
        credit semantics don't fit the base ``spawn <def> [k=v] [player]``
        parser — so this subclass keeps the legacy parsing/messages and
        delegates the credit to the adapter's single-writer (Requirement
        11.6). Target resolution uses the shared ``resolve_player`` so the
        not-found wording is unchanged from the legacy verb.
        """
        caller = self.caller
        adapter = self.adapter

        parts = (rest or "").split()
        if len(parts) < 2:
            caller.msg(
                f"Usage: {self.key} spawn <type|all> <amount> [player]"
            )
            return

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

        # Resolve which resource(s) to grant; an unknown token is REJECTED
        # (not minted as a junk resource — the reported "give all" bug that
        # once created a resource literally named "all").
        resources = adapter.resolve_resources(resource_token)
        if resources is None:
            valid = ", ".join(adapter.resource_names())
            caller.msg(
                f"Unknown resource '{resource_token}'. "
                f"Valid: {valid} (or 'all')."
            )
            return

        # Resolve target: a named player (the shared resolver messages on a
        # miss/ambiguity) or self.
        if player_name:
            target = resolve_player(caller, player_name)
            if target is None:
                return
        else:
            target = caller

        # Credit through the adapter's single-writer; a path failure reports
        # the reason and changes nothing (Requirement 4.8).
        try:
            result = adapter.create(
                caller, resource_token,
                {"amount": amount, "target": target, "resources": resources},
            )
        except Exception as exc:  # noqa: BLE001 - relay grant-path errors
            caller.msg(f"Grant failed: {exc}")
            return
        if result is None or not getattr(result, "ok", False):
            caller.msg(getattr(result, "error", None) or "Grant failed.")
            return

        target_name = getattr(target, "key", "?")
        granted = result.instance["granted"]
        note = self._audit("give", f"{amount} {granted} to {target_name}")
        caller.msg(f"Gave {amount} {granted} to {target_name}.{note}")

        # Notify the target if they can receive messages and aren't the caller.
        if hasattr(target, "msg") and target is not caller:
            target.msg(
                f"|y[Admin] You received {amount} {granted} "
                f"from {caller.key}.|n"
            )

    def _sub_show(self, rest):
        """``show [player]``: default the target to the caller (the legacy
        "defaults to you" behavior) before the shared readout."""
        token = (rest or "").strip() or "me"
        super()._sub_show(token)

    def sub_reset(self, rest):
        """Reset player(s) to starting resources (the ``reset`` extra verb).

        Args:
            rest: "[player]" — if specified, reset just that player;
                  if empty, reset all players.
        """
        caller = self.caller
        player_name = rest.strip() if rest else ""

        if player_name:
            # Reset a single player
            target = resolve_player(self.caller, player_name)
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


class CmdAdminItem(EntityAdminRouter):
    """Manage items under the unified admin grammar.

    Usage:
      @item list [filter] [player]
      @item spawn <def> [count=N] [iqs=<0-100>] [rarity=<tier>] [player]
      @item show <item>
      @item set <item> <stat> <value>
      @item destroy <item>[, <item> ...]
      @item def list | def show <key> | def diff
      @item def set <key> <field> <value> | def reset <key> [field]

    Core verbs (shared EntityAdminRouter handlers, driven by the item
    adapter registered under ``adapter_key = "item"``):
      list    — live item instances in a player's holdings (defaults to
                yours); definition listing moved to 'def list'
      spawn   — create item(s) from a definition through the existing
                creation paths (kwargs: count=, iqs=, rarity=); rollable
                Gear is rolled exactly like a loot drop
      show    — full instance readout: state, modifiable fields with
                [min–max] roll bands, staleness notes for stamped
                attributes that drifted from the current merged def
      set     — bounded stat write clamped into the def's roll band
                (with a note), re-stamping IQS through the loot roller
                before the response; 'rarity' accepts a tier name
      destroy — delete an item instance (multi-target destroys need
                explicit confirmation)
      def …   — definition scope: 'def list'/'def show'/'def diff' at
                Builder; 'def set'/'def reset' (overlay-backed, validated
                reload) at Admin

    Targets resolve uniformly: '#N' from the last '@item list', the item
    key (e.g. 'assault_rifle'), the item name, or an unambiguous prefix;
    a trailing player name scopes to that player's holdings ('@item show
    assault_rifle Bob').

    Legacy spellings (deprecated migration aliases):
      stats <item> — alias of 'show <item>'
    """

    key = "@item"
    adapter_key = "item"

    def _sub_list(self, rest):
        """``list``: shared instance-rows handler + the def-list pointer.

        While the ``@item`` migration aliases remain installed (the
        deprecation window), every ``@item list`` includes the pointer
        that definition listing moved to ``def list`` — the old def-list
        meaning of ``list`` moved there in this rollout phase
        (Requirement 11.4). The shared handler stays generic; only this
        subclass appends the pointer.
        """
        super()._sub_list(rest)
        self.caller.msg(
            "Note: definition listing moved to '@item def list' — "
            "'list' now shows live item instances."
        )


class CmdAdminTech(EntityAdminRouter):
    """Manage player technologies under the unified admin grammar.

    Usage:
      @tech list [filter] [player]
      @tech grant <tech> [player]
      @tech revoke <tech> [player]
      @tech show <tech> [player]
      @tech def list | def show <key> | def diff
      @tech def set <key> <field> <value> | def reset <key> [field]

    Core verbs (shared EntityAdminRouter handlers, driven by the tech
    adapter registered under ``adapter_key = "tech"``):
      list    — technologies granted to a player (defaults to you);
                optional filter on key/name/effect
      grant   — grant a technology (maps to the spawn verb): adds
                through the existing research path and recomputes the
                player's derived tech bonuses before the response;
                granting an already-held tech errors stating the
                current grant state, nothing changes
      revoke  — revoke a granted technology (maps to the destroy verb):
                removes + recomputes derived bonuses before the
                response; revoking a non-held tech errors stating the
                current grant state, nothing changes
      show    — one granted tech: holder, rank/cost/effect read live
                from the merged definition
      set     — not available: technologies have no modifiable
                per-instance fields
      def …   — definition scope: 'def list'/'def show'/'def diff' at
                Builder; 'def set'/'def reset' (overlay-backed,
                validated reload) at Admin

    Targets resolve uniformly: '#N' from the last '@tech list', the
    tech key (e.g. 'drone_swarm'), the tech name, or an unambiguous
    prefix; a trailing player name scopes to that player's granted
    techs ('@tech revoke drone_swarm Bob').
    """

    key = "@tech"
    adapter_key = "tech"

    def _dispatch_extra_to_core(self, canonical: str, rest: str):
        """Dispatch an extra verb through its canonical core handler.

        Like ``_dispatch_alias`` but WITHOUT the deprecation note —
        ``grant``/``revoke`` are the intended spellings, not legacy
        ones. The canonical verb's permission check and handler run so
        state changes, perm outcomes, and audit entries are identical
        to invoking the canonical verb directly (Requirement 9.1).
        """
        entry = self.subcommands.get(canonical)
        if entry is None:
            self.caller.msg(f"'{canonical}' is not available here.")
            return
        handler, _help_text, perm = entry
        if perm and not self._check_sub_perm(perm, canonical):
            return
        handler(self, rest)

    def sub_grant(self, rest):
        """``grant <tech> [player]`` — maps to spawn (Requirement 7.1)."""
        self._dispatch_extra_to_core("spawn", rest)

    def sub_revoke(self, rest):
        """``revoke <tech> [player]`` — maps to destroy (Requirement 7.1)."""
        self._dispatch_extra_to_core("destroy", rest)


class CmdAdminPlayer(ValueFirstSetAliasMixin, EntityAdminRouter):
    """Manage players under the unified admin grammar.

    Usage:
      @player list [filter]
      @player show <player>
      @player set <player> level <1-100>
      @player set <player> rank <1-12>

    Core verbs (shared EntityAdminRouter handlers, driven by the player
    adapter registered under ``adapter_key = "player"``):
      list    — live player characters as indexed rows
      show    — full progression readout: level, rank, XP, modifiable
                fields with [min–max] bounds
      set     — bounded field write (Admin+) through the existing
                rank-system progression path: 'level' (1-100, out-of-
                bounds values clamp with a note) re-stamps XP and
                recomputes the rank; 'rank' (numeric rank id 1-12)
                jumps to that rank's first level. Rank events (tech
                unlocks, agent-cap adjustments) recompute before the
                response, exactly like the legacy verbs.
      spawn   — not available: players register through account creation
      destroy — not available: use the '@obliterate' flow instead
      def …   — not available: players have no YAML definition domain

    Targets resolve uniformly: 'me'/'self' (yourself), '#N' from the
    last '@player list', an exact name, or an unambiguous prefix.

    Legacy spellings (deprecated migration aliases):
      level <N> [player] — alias of 'set <player> level <N>'
      rank <N> [player]  — alias of 'set <player> rank <N>'
      ([player] omitted targets you, as before)
    """

    key = "@player"
    adapter_key = "player"

    #: The legacy VALUE-first verb forms whose argument order differs from
    #: the canonical ``set <target> <field> <value>`` grammar. The reshape
    #: (and the shared alias dispatch) live in ``ValueFirstSetAliasMixin``;
    #: ``@player`` has no ``ALIAS_FIELDS`` remap, so ``level``/``rank`` are
    #: written to the field of the same name.
    _LEGACY_SET_ALIASES = ("level", "rank")
    _ALIAS_TARGET_NOUN = "player"


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

        c_coords = coords_of(caller)
        if c_coords is None:
            cx = cy = None
        else:
            cx, cy, _planet = c_coords

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
        coords = coords_of(caller)
        if coords is None or not coords[2]:
            caller.msg("You have no overworld position to transfer a unit to.")
            return
        tx, ty, planet = coords

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


class CmdPeace(BaseCommand):
    """Clear a player's combat timer — take them out of combat now.

    Usage:
        @peace [player]

    Zeroes the combat timer (``combat_timer_expires``) and the build-gate
    lockout (``combat_lockout_tick``) so the target is immediately out of
    combat: Wall passage, builds, and quitting are no longer blocked and the
    on-screen combat clock clears. Any pending ranged lock-on the target holds
    is also dropped. If no player is named, targets you.

    Note: this does not stop hostiles already shooting the target — it clears
    the target's own combat state. Restricted to Builder+.
    """

    key = "@peace"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        target_name = self.args.strip()

        if target_name:
            target = resolve_player(caller, target_name)
            if target is None:
                return
        else:
            target = caller

        db = getattr(target, "db", None)
        if db is None:
            caller.msg(f"{getattr(target, 'key', 'target')} has no combat state.")
            return

        db.combat_timer_expires = 0
        db.combat_lockout_tick = 0
        # Drop any held ranged lock-on so the target isn't left mid-aim.
        try:
            targeting = _get_system(caller, "targeting_system")
            if targeting is not None and targeting.get_target(target) is not None:
                targeting.clear_lock(target, reason="peace")
        except Exception:  # noqa: BLE001 - lock clear is best-effort
            pass

        name = getattr(target, "key", "?")
        logger.info("Admin %s cleared combat state for %s", caller.key, name)
        caller.msg(f"|gCleared combat state for {name}.|n")
        if target is not caller and hasattr(target, "msg"):
            target.msg("|gAn administrator has taken you out of combat.|n")


class CmdRestore(BaseCommand):
    """Heal a player (or NPC) to full health and revive if downed.

    Usage:
        @restore [target]

    Sets ``hp`` to the target's ``hp_max`` and clears any incapacitation /
    respawn timer, so a downed unit is instantly revived at full HP. Works on
    players and NPCs (agents, guards). If no target is named, restores you.

    Restricted to Builder+.
    """

    key = "@restore"
    aliases = ["@heal"]
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    def func(self):
        caller = self.caller
        target_name = self.args.strip()

        if target_name:
            # Any combat unit (player OR NPC), so use the shared entity search
            # rather than resolve_player (which is player-scoped messaging).
            target = _resolve_combat_unit(caller, target_name)
            if target is None:
                return
        else:
            target = caller

        db = getattr(target, "db", None)
        if db is None:
            caller.msg(f"{getattr(target, 'key', 'target')} has no health to restore.")
            return

        hp_max = getattr(db, "hp_max", None) or 0
        if hp_max <= 0:
            caller.msg(f"{getattr(target, 'key', '?')} has no maximum health set.")
            return

        db.hp = hp_max
        # Revive a downed unit: clear incapacitation and cancel the respawn timer.
        if getattr(db, "incapacitated", False):
            db.incapacitated = False
        db.respawn_timer = 0

        name = getattr(target, "key", "?")
        logger.info("Admin %s restored %s to full health (%d)", caller.key, name, hp_max)
        caller.msg(f"|gRestored {name} to full health ({hp_max}/{hp_max}).|n")
        if target is not caller and hasattr(target, "msg"):
            target.msg("|gAn administrator has restored you to full health.|n")


def _resolve_combat_unit(caller, name):
    """Resolve *name* to a single combat unit (player or NPC), or msg + None.

    Shared by the stat/restore admin commands: unlike ``resolve_player`` (which
    is player-scoped), this accepts any movable combat unit — a player, an
    agent, or an enemy NPC — since staff set stats and heal on all of them. Uses
    the same name/prefix search as ``transfer``; on no match or ambiguity it
    messages the caller (listing co-named candidates) and returns ``None``.
    """
    candidates = _search_entities(caller, name)
    if not candidates:
        caller.msg(f"No unit named '{name}' found. Use a name or unambiguous prefix.")
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous — list the matches with owners/coords so the op can disambiguate.
    from world.utils import get_coords
    lines = [f"|yMultiple units match '{name}'|n:"]
    for c in candidates:
        owner = _owner_label(c)
        owner_tag = f" owner={owner}" if owner else ""
        coords = get_coords(c)
        loc = f" at ({coords[0]}, {coords[1]})" if coords else ""
        lines.append(f"  |c{getattr(c, 'key', '?')}|n{owner_tag}{loc}")
    caller.msg("\n".join(lines))
    return None


#: Import-once cache of Evennia's DefaultCharacter (populated by
#: _is_protected_player on first use — the class import is deferred so this
#: module stays importable without a full Evennia env, e.g. in unit tests).
_DefaultCharacter = None


def _is_protected_player(obj) -> bool:
    """True if *obj* is a real player character that obliterate must NOT delete.

    A player is a ``DefaultCharacter`` (even while disconnected — a linkdead or
    stowed character still owns its account and world state). A Sentinel is ALSO
    a DefaultCharacter subclass, so we explicitly exclude it (``is_sentinel``):
    the NPC-base HQ IS a valid obliterate target. Fails safe — anything we can't
    classify as a non-sentinel character is treated as NOT protected (obliterate
    is an explicit destructive admin action; the one thing it guards is real
    players).
    """
    global _DefaultCharacter
    if _DefaultCharacter is None:
        from evennia.objects.objects import DefaultCharacter
        _DefaultCharacter = DefaultCharacter
    if not isinstance(obj, _DefaultCharacter):
        return False
    # A sentinel is a character but represents an NPC base HQ — destroyable.
    return not bool(getattr(getattr(obj, "db", None), "is_sentinel", False))


class CmdObliterate(BaseCommand):
    """Destroy every building/entity within a radius — a blunt cleanup tool.

    Usage:
        obliterate <radius>
        obliterate <radius> <x> <y> [z]

    Options:
        <radius>   Chebyshev radius in tiles (0 = just the center tile).
        <x> <y>    center coordinates; defaults to your current tile.
        [z]        planet by z-level (0/1/2) — or name/prefix; defaults to the
                   center's planet (your planet when no coords are given).

    Destroys, within range: buildings (incl. NPC-base HQs/turrets/walls/shield
    generators), NPCs (agents, enemy guards, sentinels), dropped items, resource
    drops, and armed bombs. Real player characters are ALWAYS spared — including
    disconnected/linkdead ones.

    Examples:
        obliterate 5                — everything within 5 tiles of you
        obliterate 5 250 250 3      — within 5 tiles of (250,250) on z-level 3
        obliterate 0                — just your own tile

    This is a hard delete: no XP, no loot, no BASE_ELIMINATED event (so wiping a
    Sentinel HQ won't trigger the base-elimination reward path). Any NPC base
    whose HQ is in range is cleared as a UNIT (owning Sentinel + all buildings +
    guards) and dropped from the '@outpost list' — the base owner is an off-map
    ownership anchor, so a plain tile sweep would otherwise leave a phantom base.
    Restricted to Builder+.
    """

    key = "obliterate"
    locks = "cmd:perm(Builder);view:perm(Builder)"
    help_category = "Admin"

    _USAGE = "Usage: obliterate <radius> [<x> <y> [z]]  (commas optional)"

    def func(self):
        caller = self.caller
        args = self.args.strip()
        if not args:
            caller.msg(self._USAGE)
            return

        # Accept commas or spaces interchangeably, matching goto/throw.
        parts = args.replace(",", " ").split()

        try:
            radius = int(parts[0])
        except ValueError:
            caller.msg("Radius must be an integer.")
            return
        if radius < 0:
            caller.msg("Radius must be zero or positive.")
            return

        registry = _get_system(caller, "planet_registry")

        # Resolve center + planet. Either just <radius> (use caller's position)
        # or <radius> <x> <y> [z].
        if len(parts) == 1:
            coords = coords_of(caller)
            if coords is None or not coords[2]:
                caller.msg("You have no coordinates set. Specify: obliterate <radius> <x> <y> [z].")
                return
            cx, cy, planet = int(coords[0]), int(coords[1]), coords[2]
        elif len(parts) >= 3:
            try:
                cx = int(parts[1])
                cy = int(parts[2])
            except ValueError:
                caller.msg("Coordinates must be integers.")
                return
            if len(parts) >= 4:
                if registry is None:
                    caller.msg("Planet registry not available.")
                    return
                planet = registry.resolve_planet(parts[3])
                if planet is None:
                    caller.msg(f"Unknown planet '{parts[3]}'. Use a name, prefix, or z-level (0/1/2).")
                    return
            else:
                planet = getattr(caller.db, "coord_planet", None)
                if not planet:
                    caller.msg("No planet specified and no current planet set.")
                    return
        else:
            caller.msg(self._USAGE)
            return

        # Find the target planet's room (may be a different planet than caller's).
        room = self._room_for_planet(caller, planet)
        if room is None:
            caller.msg(f"No PlanetRoom found for {planet}.")
            return
        if not hasattr(room, "get_objects_in_area"):
            caller.msg("Target location does not support coordinate queries.")
            return

        x1, y1 = cx - radius, cy - radius
        x2, y2 = cx + radius, cy + radius

        spawner = _get_system(caller, "outpost_spawner")

        # NPC bases FIRST, as whole units. A base's owning Sentinel holds its
        # tracking record but is NOT a map actor (no coords / not in the tile
        # index), so the tile sweep below would delete a base's buildings+guards
        # yet never the Sentinel — leaving a phantom base in '@outpost list'.
        # wipe_bases_in_area removes each base whose HQ is in range as a unit
        # (Sentinel + buildings + guards) and untracks it, so the list updates.
        bases_wiped = 0
        try:
            if spawner is not None and hasattr(spawner, "wipe_bases_in_area"):
                bases_wiped = spawner.wipe_bases_in_area(planet, x1, y1, x2, y2)
        except Exception:  # noqa: BLE001 - base wipe is best-effort
            logger.exception("obliterate: base wipe failed")

        # Then sweep any remaining loose entities on the tiles (buildings/guards
        # of a partially-overlapping base already gone above are skipped as stale
        # refs; player structures, dropped items, resource nodes, bombs remain).
        try:
            candidates = room.get_objects_in_area(x1, y1, x2, y2)
        except Exception:  # noqa: BLE001
            logger.exception("obliterate: area query failed")
            caller.msg("Failed to query the target area.")
            return

        destroyed, spared_players = self._destroy_all(candidates)

        # Reconcile any base whose Sentinel was somehow removed by the tile sweep
        # (defense-in-depth — wipe_bases_in_area already handled in-range bases).
        try:
            if spawner is not None and hasattr(spawner, "forget_dead_bases"):
                spawner.forget_dead_bases()
        except Exception:  # noqa: BLE001 - reconcile is best-effort
            logger.exception("obliterate: base reconcile failed")

        where = f"({cx}, {cy}) on {planet}"
        self._log_obliterate(caller, radius, where, destroyed, bases_wiped)
        spared = (
            f" Spared {spared_players} player(s)." if spared_players else ""
        )
        bases_note = f" Cleared {bases_wiped} NPC base(s)." if bases_wiped else ""
        caller.msg(
            f"|rObliterated {destroyed} entit"
            f"{'y' if destroyed == 1 else 'ies'}|n within {radius} tile(s) of "
            f"{where}.{bases_note}{spared}"
        )

    def _destroy_all(self, candidates):
        """Delete every non-protected entity in *candidates*.

        Returns ``(destroyed_count, spared_player_count)``. Best-effort per
        entity: one failed delete never aborts the sweep. Skips objects whose DB
        row is already gone (a stale index ref).
        """
        destroyed = 0
        spared = 0
        for obj in candidates:
            if getattr(obj, "pk", True) is None:
                continue  # already-deleted stale index entry
            if _is_protected_player(obj):
                spared += 1
                continue
            if not hasattr(obj, "delete"):
                continue
            try:
                obj.delete()
                destroyed += 1
            except Exception:  # noqa: BLE001 - keep sweeping past a bad delete
                logger.exception(
                    "obliterate: delete failed for %s",
                    getattr(obj, "key", "?"),
                )
        return destroyed, spared

    @staticmethod
    def _room_for_planet(caller, planet):
        """Return the PlanetRoom for *planet* — the caller's location if it
        matches, else the shared room from the ``planet_rooms`` service."""
        loc = getattr(caller, "location", None)
        if loc is not None and getattr(getattr(caller, "db", None), "coord_planet", None) == planet:
            return loc
        try:
            from world.services import get_service
            rooms = get_service("planet_rooms") or {}
            return rooms.get(planet)
        except Exception:  # noqa: BLE001
            return loc

    @staticmethod
    def _log_obliterate(caller, radius, where, destroyed, bases_wiped=0):
        logger.info(
            "Admin %s: obliterate r=%d at %s — destroyed %d entities, "
            "cleared %d NPC bases",
            getattr(caller, "key", "?"), radius, where, destroyed, bases_wiped,
        )


class CmdAdminStat(ValueFirstSetAliasMixin, EntityAdminRouter):
    """Set health, XP, and other combat stats on a player or NPC.

    Usage:
        @stat show [target]
        @stat set <target> <field> <value>
        @stat hp <N> [target]
        @stat maxhp <N> [target]
        @stat xp <N> [target]

    Core verbs (shared EntityAdminRouter handlers, driven by the stat
    adapter registered under ``adapter_key = "stat"``):
      show    — core combat stats readout (HP, XP, level, rank, kills,
                deaths) plus the modifiable-fields block; the target
                defaults to you (Builder+).
      set     — bounded field write (Admin+) of one allowlisted combat/
                progression field (hp, hp_max, combat_xp, level,
                rank_level, kills, deaths); out-of-bounds values clamp
                with a note. 'hp' clamps to the target's own hp_max and
                revives a downed unit; 'hp_max' tops a full unit up (and
                clamps an over-max unit down); 'combat_xp' recomputes
                level/rank from the XP curve.
      list    — not available: stats are per-unit fields, not a roster
      spawn   — not available: create units via '@agent'/'@outpost'
      destroy — not available: delete the unit ('@agent'/'@outpost'/
                'obliterate'), not its stats
      def …   — not available: combat stats have no YAML definition domain

    Targets resolve uniformly: 'me'/'self' (or omitted — yourself), an
    exact name, or an unambiguous prefix of a live player or NPC.

    Legacy spellings (deprecated migration aliases):
      hp <N> [target]    — alias of 'set <target> hp <N>'
      maxhp <N> [target] — alias of 'set <target> hp_max <N>'
      xp <N> [target]    — alias of 'set <target> combat_xp <N>'
      ([target] omitted targets you, as before)
    """

    key = "@stat"
    adapter_key = "stat"

    #: The legacy VALUE-first stat verbs, whose argument order differs
    #: from the canonical ``set <target> <field> <value>`` grammar — and
    #: whose spelling differs from the db field they write. The reshape
    #: (and the field remap via the adapter's ``ALIAS_FIELDS`` —
    #: ``maxhp``→``hp_max``, ``xp``→``combat_xp``) lives in
    #: ``ValueFirstSetAliasMixin``.
    _LEGACY_SET_ALIASES = ("hp", "maxhp", "xp")
    _ALIAS_TARGET_NOUN = "target"

    #: ``show [target]`` keeps the legacy "defaults to you" behavior — an
    #: omitted target becomes ``me`` (which the adapter resolves to the
    #: caller), via the shared ``_sub_show``.
    default_show_target = "me"


class CmdAdminOutpost(EntityAdminRouter):
    """Manage NPC bases (outposts/fortresses) under the unified admin grammar.

    Usage:
      @outpost list [filter]
      @outpost spawn <tier> [x y]
      @outpost show <base>
      @outpost set <base> <field> <value>
      @outpost destroy [<base>[, <base> ...]]
      @outpost def list | def show <tier> | def diff

    Core verbs (shared EntityAdminRouter handlers, driven by the outpost
    adapter registered under ``adapter_key = "outpost"``):
      list    — active NPC bases the spawner is tracking (unchanged
                instance meaning); rows are #N-addressable
      spawn   — place a base through the existing spawner path; <tier>
                accepts a name (fortress), an unambiguous prefix (fort),
                or a [N] index from 'def list'; coords default to your
                current tile
      show    — one base's readout: planet, HQ tile, staleness state,
                modifiable fields
      set     — bounded field write through the spawner's own state
                paths (disturbed_at — the staleness clock)
      destroy — wipe a base AS A UNIT (Sentinel + owned buildings/guards,
                no respawn) via the spawner's admin-clear path
      def …   — the base-template tiers: 'def list'/'def show'/'def diff'
                at Builder; 'def set'/'def reset' are opted out (templates
                load outside the overlay merge — edit outposts.yaml and
                @reboot)

    Targets resolve uniformly: '#N' from the last '@outpost list', the
    base's key/name, or an unambiguous prefix.

    Legacy spellings:
      tiers   — deprecated alias of 'def list'
    """

    key = "@outpost"
    adapter_key = "outpost"

    def _sub_spawn(self, rest):
        """``spawn <tier> [x y]``: the legacy spawn grammar, preserved.

        The design leaves ``@outpost spawn`` unchanged (per-entity matrix),
        and its positional ``[x y]`` form doesn't fit the shared
        ``spawn <def> [k=v ...] [player]`` parser — so this subclass keeps
        the legacy parsing/messages and delegates creation to the
        adapter's spawner path (Requirement 11.6).
        """
        caller = self.caller
        spawner = self.require_system("outpost_spawner", "Outpost spawner")
        if spawner is None:
            return

        parts = (rest or "").split()
        if not parts:
            caller.msg("Usage: @outpost spawn <tier> [x y]")
            return
        adapter = self.adapter
        tier = adapter.resolve_tier(parts[0])
        if tier is None:
            valid = ", ".join(adapter.tier_names()) or "none loaded"
            caller.msg(
                f"Unknown or ambiguous tier '{parts[0]}'. Valid: {valid}. "
                f"Use '@outpost def list' for index numbers."
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
            c_coords = coords_of(caller)
            if c_coords is not None:
                coords = (int(c_coords[0]), int(c_coords[1]))

        result = adapter.create(caller, tier,
                                {"planet": planet, "coords": coords})
        if not result.ok:
            caller.msg(
                f"Could not spawn {tier!r} base "
                f"(unknown tier or no valid placement)."
            )
            return
        base = result.instance
        note = self._audit(
            "spawn", f"{tier} at {base['x']},{base['y']} on {planet}"
        )
        caller.msg(
            f"|gSpawned {tier} base|n at ({base['x']}, {base['y']}) "
            f"on {planet}.{note}"
        )

    def _def_list(self, rest):
        """``def list``: base tiers with a stable 1-based [N] index.

        Overrides the shared handler's plain listing to keep the legacy
        tier rendering — ``@outpost spawn <N>`` (or ``#N``) spawns the
        tier shown as ``[N]``, so the listing must number the rows.
        """
        templates = self.adapter.def_registry_dict()
        if not templates:
            self.caller.msg("No base tiers loaded.")
            return
        lines = ["|w=== Base tiers (spawn by name or [index]) ===|n"]
        for idx, tier in enumerate(sorted(templates), start=1):
            tmpl = templates[tier]
            display = getattr(tmpl, "display_name", "") if tmpl else ""
            suffix = (f" — {display}"
                      if display and display.lower() != tier else "")
            lines.append(f"  |w[{idx}]|n {tier}{suffix}")
        self.caller.msg("\n".join(lines))

    @staticmethod
    def _definition_key(definition):
        """A base template's identifying key is its ``tier`` (the
        ``BaseTemplateDef`` field the registry dict is keyed by)."""
        return str(getattr(definition, "tier", definition))


class CmdAdminPowerup(EntityAdminRouter):
    """Inspect and tune powerup definitions under the unified admin grammar.

    Usage:
      @powerup def list
      @powerup def show <key>
      @powerup def set <key> <field> <value>
      @powerup def reset <key> [field]
      @powerup def diff

    Powerups are definition-only: they are applied to players through the
    powerup system, not spawned as standalone admin objects, so every
    instance verb (list/spawn/show/set/destroy) is not available and
    points here instead. The full definition scope is driven by the
    powerup adapter registered under ``adapter_key = "powerup"``:
      def list  — every loaded powerup definition (Builder)
      def show  — one powerup's merged fields, overrides flagged (Builder)
      def diff  — current overrides in the 'powerups' domain (Builder)
      def set   — overlay-backed, validated-reload field override (Admin)
      def reset — remove an override + validated reload (Admin)

    Definition tokens resolve by key, name, or unambiguous prefix.
    Overrides land in the shared definition overlay and go live on the
    next reload; a rejected reload rolls the overlay back unchanged.
    """

    key = "@powerup"
    adapter_key = "powerup"


class CmdAdminTerrain(EntityAdminRouter):
    """Inspect and tune terrain definitions under the unified admin grammar.

    Usage:
      @terrain def list
      @terrain def show <type>
      @terrain def set <type> <field> <value>
      @terrain def reset <type> [field]
      @terrain def diff

    Terrain is definition-only: tiles are generated by the procedural map,
    not spawned, so every instance verb (list/spawn/show/set/destroy) is
    not available and points here instead. The full definition scope is
    driven by the terrain adapter registered under
    ``adapter_key = "terrain"``:
      def list  — every loaded terrain definition (Builder)
      def show  — one terrain type's merged fields, overrides flagged
                  (Builder)
      def diff  — current overrides in the 'terrain' domain (Builder)
      def set   — overlay-backed, validated-reload field override (Admin);
                  settable fields are map_symbol, resource_type, and the
                  vision/movement/defense/latitude modifiers
                  (passable/buildable are booleans — edit terrain.yaml)
      def reset — remove an override + validated reload (Admin)

    Definition tokens resolve by terrain_type, or an unambiguous prefix.
    Overrides land in the shared definition overlay and affect newly
    generated/derived tiles on the next reload; a rejected reload rolls
    the overlay back unchanged.
    """

    key = "@terrain"
    adapter_key = "terrain"


class CmdAdminPlanet(EntityAdminRouter):
    """Inspect planet definitions under the unified admin grammar.

    Usage:
      @planet def list
      @planet def show <key>

    Planets are definition-READ-only. ``planets.yaml`` loads into a
    separate PlanetRegistry that is NOT part of the hot-reload pipeline,
    so planets are not hot-reloadable: every write verb — 'def set',
    'def reset' — and 'def diff' (planets have no overlay) are not
    available, with the reason "planets are not hot-reloadable; edit
    planets.yaml and restart". Every instance verb is not available too
    (a planet is a coordinate-space definition, not an admin object).

    The read-only definition scope is driven by the planet adapter
    registered under ``adapter_key = "planet"``, served straight from the
    PlanetRegistry:
      def list — every loaded planet (Builder)
      def show — one planet's coordinate-space definition (Builder)

    Definition tokens resolve by planet key, z-level, or unambiguous
    prefix. To change a planet, edit planets.yaml and restart the server.
    """

    key = "@planet"
    adapter_key = "planet"
