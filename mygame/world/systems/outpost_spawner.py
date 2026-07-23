"""
Outpost Spawner — places NPC outposts/fortresses and respawns cleared ones.

Keeps the world populated with raid targets. At server start it places
``outpost_count`` outposts + ``fortress_count`` fortresses per planet from the
data-driven templates (``registry.base_templates``); when a base is eliminated
(its HQ destroyed — see :class:`BaseEliminationHandler`) it schedules a respawn
after ``outpost_respawn_ticks`` at a fresh valid location.

Framework-free: all Evennia I/O (creating the Sentinel owner, enemy guards, and
buildings; reading terrain) is injected as ports/callables at the composition
root. The system owns only the placement logic, template expansion, and the
respawn schedule.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem
from world.utils import get_obj_attr, set_obj_attr

logger = logging.getLogger("evennia.world.systems.outpost_spawner")

#: How many random placements to try before giving up on one base (a crowded
#: planet degrades gracefully — the base is skipped and logged, not retried
#: forever). Structural, not a balance knob.
_MAX_PLACEMENT_ATTEMPTS = 100
#: Minimum Manhattan distance an NPC base's HQ must keep from any other NPC
#: base's HQ and from any existing building (incl. player HQs), so bases don't
#: overlap or spawn on top of a player.
_MIN_BASE_SEPARATION = 8
#: When a respawn can't be placed (crowded planet), re-arm it after a fraction
#: of the full cooldown rather than retrying every tick — bounds the placement
#: work a permanently-crowded planet can generate per tick.
_RESPAWN_BACKOFF_DIVISOR = 4
#: PlanetRoom attribute key under which pending respawns for that planet are
#: persisted, so a cleared base still respawns after a server restart (Req 7.6).
_PENDING_ATTR = "npc_base_pending_respawns"
#: Current NPC-base spec version. Stamped on every spawned sentinel; a startup
#: sweep wipes + re-seeds any base stamped with an OLDER version, so a template
#: overhaul (new tiers/layouts/rewards) — OR a new placement rule — automatically
#: clears stale-spec bases from live worlds. BUMP THIS whenever outposts.yaml
#: changes materially or placement constraints change.
#: v3: bases must sit on buildable terrain (no rivers/hazards); re-seed to
#: relocate any base placed on treacherous terrain under the old rule.
_BASE_SPEC_VERSION = 3
#: Chebyshev range (tiles) at which a player is shown a nearby base's status on
#: their map (type + staleness countdown) — the "you're engaging an event" cue.
BASE_PROXIMITY_RADIUS = 15


class OutpostSpawnerSystem(BaseSystem):
    """Places NPC bases at init and respawns cleared ones on a cooldown.

    Args:
        registry: DataRegistry (balance + base_templates + building defs).
        event_bus: EventBus (unused directly; elimination is handled elsewhere).
        npc_base_factory: :class:`NpcBaseFactory` port — creates the Sentinel
            owner and enemy guards.
        building_factory: :class:`BuildingFactory` port — creates base buildings
            (reused from BuildingSystem's factory).
        terrain_provider: :class:`TerrainProvider` port — terrain lookup for
            passability checks during placement.
        planet_rooms_provider: zero-arg callable returning the
            ``{planet_key: PlanetRoom}`` dict (late-bound; rooms are created
            after the systems at the composition root).
        planet_registry: PlanetRegistry for planet bounds / coordinate validity.
        rng: Optional ``random.Random`` for deterministic placement in tests.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        npc_base_factory: Any = None,
        building_factory: Any = None,
        terrain_provider: Any = None,
        planet_rooms_provider: Callable[[], dict] | None = None,
        planet_registry: Any = None,
        rng: "random.Random | None" = None,
        current_tick_func: Callable[[], int] | None = None,
        owned_entities_provider: Callable[[Any], list] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._npc_factory = npc_base_factory
        self._building_factory = building_factory
        self._terrain = terrain_provider
        self._planet_rooms_provider = planet_rooms_provider or (lambda: {})
        self._planet_registry = planet_registry
        self._rng = rng or random.Random()
        self._current_tick_func = current_tick_func or (lambda: 0)
        #: Enumerate every building + guard a sentinel owns (for the stale wipe).
        #: Shared with BaseEliminationHandler at the composition root; None in
        #: isolated tests that don't exercise the wipe.
        self._owned_entities_provider = owned_entities_provider
        #: Active bases: sentinel-id -> {"sentinel", "tier", "planet", "x", "y"}.
        self._active_bases: dict[Any, dict] = {}
        #: Pending respawns: list of {"tier", "planet", "respawn_at"} (tick).
        self._pending_respawns: list[dict] = []
        # Schedule a respawn whenever a base is eliminated (decoupled from the
        # elimination handler — it publishes, we react). Also watch COMBAT_ACTION
        # to start a base's staleness timer the moment it's first disturbed.
        if event_bus is not None:
            from world.event_bus import BASE_ELIMINATED, COMBAT_ACTION
            event_bus.subscribe(BASE_ELIMINATED, self.on_base_eliminated)
            event_bus.subscribe(COMBAT_ACTION, self.on_combat_action)

    # ------------------------------------------------------------------ #
    #  Initial placement
    # ------------------------------------------------------------------ #

    def spawn_initial(self, planet: str) -> list:
        """Place every configured NPC-base tier per planet at server start.

        Iterates ALL loaded templates (not just the two legacy tiers), so adding
        a difficulty tier is a pure outposts.yaml edit. Each tier's count comes
        from its ``spawn_count``; a template that omits it falls back to the
        balance count for its ``difficulty_class`` (``outpost_count`` /
        ``fortress_count``). Silently places as many as fit; a base that can't
        find a valid spot after the attempt cap is skipped (logged), so a crowded
        planet never blocks startup.

        Returns the list of spawned base records.
        """
        spawned = []
        # Deterministic order (sorted by tier key) so placement is reproducible
        # under a seeded RNG in tests, regardless of dict insertion order.
        for tier in sorted(self.registry.base_templates.keys()):
            template = self.registry.base_templates[tier]
            count = self._spawn_count(template)
            for _ in range(max(0, count)):
                base = self.spawn_base(planet, tier)
                if base is not None:
                    spawned.append(base)
        logger.info("Spawned %d NPC base(s) on planet %s.", len(spawned), planet)
        return spawned

    def _spawn_count(self, template: Any) -> int:
        """How many of *template* to place: its ``spawn_count``, else the class
        balance default (fortress-class → ``fortress_count``, else
        ``outpost_count``)."""
        if getattr(template, "spawn_count", None) is not None:
            return int(template.spawn_count)
        bal = self.registry.balance
        if getattr(template, "difficulty_class", "outpost") == "fortress":
            return bal.fortress_count
        return bal.outpost_count

    # ------------------------------------------------------------------ #
    #  Spawn one base
    # ------------------------------------------------------------------ #

    def spawn_base(
        self, planet: str, tier: str, coords: tuple[int, int] | None = None
    ) -> dict | None:
        """Spawn one NPC base of *tier* on *planet*.

        Creates the Sentinel owner, then the template's buildings (via the
        building factory) at their offsets and its guards (via the NPC factory)
        at the HQ tile. When *coords* is None a valid location is chosen by
        :meth:`_find_placement`. Returns the base record, or ``None`` if the
        template/room is unavailable or no valid placement was found.
        """
        template = self.registry.get_base_template(tier)
        if template is None:
            logger.warning("No NPC-base template for tier %r.", tier)
            return None

        room = self._planet_rooms_provider().get(planet)
        if room is None:
            logger.warning("No PlanetRoom for planet %r — cannot spawn base.", planet)
            return None

        if self._npc_factory is None or self._building_factory is None:
            return None

        if coords is None:
            coords = self._find_placement(planet, room, template)
            if coords is None:
                logger.warning(
                    "No valid placement for %s on %s after %d attempts.",
                    tier, planet, _MAX_PLACEMENT_ATTEMPTS,
                )
                return None
        hx, hy = int(coords[0]), int(coords[1])

        # 1. Sentinel owner.
        name = self._sentinel_name(template)
        sentinel = self._npc_factory.create_sentinel(name, room, planet)
        # Stamp tier/planet/coords so the elimination handler can schedule a
        # respawn AND so _active_bases can be rebuilt from surviving sentinels
        # after a server restart (rebuild_from_world).
        set_obj_attr(sentinel, "base_tier", tier)
        set_obj_attr(sentinel, "base_planet", planet)
        set_obj_attr(sentinel, "base_x", hx)
        set_obj_attr(sentinel, "base_y", hy)

        # 2. Buildings at their template offsets.
        for b in template.buildings:
            bx, by = hx + b.offset[0], hy + b.offset[1]
            bdef = self._resolve_building(b.building_type)
            if bdef is None:
                logger.warning("Unknown building type %r in template %r.",
                               b.building_type, tier)
                continue
            building = self._building_factory.create_building(
                bdef, room, sentinel, x=bx, y=by
            )
            if building is not None:
                # NPC-base buildings are OPEN — they exist to be raided, so
                # ranged weapons and turrets can hit them (buildings default to
                # closed/cover, which is a player-base concept, not an NPC one).
                set_obj_attr(building, "open", True)
                if b.hp is not None:
                    set_obj_attr(building, "hp", b.hp)
                    set_obj_attr(building, "hp_max", b.hp)

        # 3. Guards at the HQ tile. A running 1-based index across all guard
        # groups makes each guard uniquely named (Guard-1, Guard-2, Soldier-3…).
        guard_hp = self._guard_hp(template, planet)
        guard_index = 0
        for g in template.guards:
            hp = g.hp if g.hp is not None else guard_hp
            for _ in range(max(0, g.count)):
                guard_index += 1
                self._npc_factory.create_enemy_guard(
                    sentinel, room, hx, hy, g.role, hp, index=guard_index
                )

        # A fresh base is undisturbed: no staleness timer runs until it's first
        # damaged/loses a guard (see on_combat_action). disturbed_at == 0 means
        # "pristine". Persisted on the sentinel so the timer survives a restart.
        set_obj_attr(sentinel, "base_disturbed_at", 0)
        # Stamp the spec version so a later template overhaul can identify and
        # purge stale-spec bases (see purge_outdated_bases).
        set_obj_attr(sentinel, "base_spec_version", _BASE_SPEC_VERSION)

        record = {
            "sentinel": sentinel, "tier": tier, "planet": planet,
            "x": hx, "y": hy, "disturbed_at": 0,
        }
        self._active_bases[self._base_key(sentinel)] = record
        logger.info("Spawned %s base at (%d, %d) on %s.", tier, hx, hy, planet)
        return record

    # ------------------------------------------------------------------ #
    #  Respawn scheduling
    # ------------------------------------------------------------------ #

    def on_base_eliminated(
        self, event_name: str = "", sentinel: Any = None, tier: str = "outpost",
        planet: Any = None, **kwargs
    ) -> None:
        """React to a ``BASE_ELIMINATED`` event: drop the base and queue respawn.

        The elimination handler publishes tier/planet in the payload (read
        before it deletes the sentinel), so we don't depend on the now-deleted
        object. Schedules a fresh base of the same tier after
        ``outpost_respawn_ticks`` (0 disables respawning).
        """
        if sentinel is not None:
            self._active_bases.pop(self._base_key(sentinel), None)

        respawn_ticks = self.registry.balance.outpost_respawn_ticks
        if respawn_ticks <= 0 or not planet:
            return
        self._pending_respawns.append({
            "tier": tier, "planet": planet,
            "respawn_at": self._current_tick_func() + respawn_ticks,
        })
        # Persist so the pending respawn survives a server restart (Req 7.6) —
        # without this a base cleared before its cooldown would never come back.
        self._save_state()

    # ------------------------------------------------------------------ #
    #  Staleness decay — refresh partially-raided bases
    # ------------------------------------------------------------------ #

    def on_combat_action(
        self, event_name: str = "", target: Any = None, damage: Any = 0, **kwargs
    ) -> None:
        """Start a base's staleness timer the first time it is disturbed.

        A base is "disturbed" when one of its buildings takes damage or one of
        its guards is attacked — both surface here as a ``COMBAT_ACTION`` whose
        ``target`` is owned by the base's Sentinel. On the FIRST such hit we
        stamp ``disturbed_at`` (the current tick) on the base record and the
        sentinel; subsequent hits don't reset it, so the 24h clock runs from the
        first disturbance. A pristine, untouched base never starts the timer.

        Best-effort and cheap: a hit on anything not owned by a tracked,
        still-pristine sentinel is ignored. Never raises into the combat path.
        """
        try:
            if target is None or not damage or int(damage) <= 0:
                return
            sentinel = get_obj_attr(target, "owner")
            if sentinel is None or not get_obj_attr(sentinel, "is_sentinel", False):
                return
            record = self._active_bases.get(self._base_key(sentinel))
            if record is None or record.get("disturbed_at"):
                return  # untracked, or already ticking — don't reset the clock
            now = self._current_tick_func()
            record["disturbed_at"] = now
            set_obj_attr(sentinel, "base_disturbed_at", now)
            logger.info(
                "NPC base %s disturbed at tick %d — staleness timer started.",
                getattr(sentinel, "key", "?"), now,
            )
        except Exception:  # noqa: BLE001 - staleness tracking never breaks combat
            logger.debug("on_combat_action staleness stamp failed", exc_info=True)

    def process_stale(self, tick_number: int) -> int:
        """Wipe + regenerate any DISTURBED base past its staleness deadline.

        Wired as the ``"outpost_stale"`` tick step. A base whose ``disturbed_at``
        is more than ``outpost_stale_ticks`` old — i.e. it was damaged but not
        fully cleared within ~24h — is deleted and a fresh base of the same tier
        is spawned (at a new valid location), keeping partially-raided bases from
        sitting stale. Pristine bases (``disturbed_at == 0``) are never touched.
        Returns the number of bases refreshed this tick.
        """
        stale_ticks = self.registry.balance.outpost_stale_ticks
        if stale_ticks <= 0 or not self._active_bases:
            return 0
        # Snapshot: the wipe mutates _active_bases (pops the sentinel key).
        due = [
            rec for rec in list(self._active_bases.values())
            if rec.get("disturbed_at")
            and tick_number - rec["disturbed_at"] >= stale_ticks
        ]
        refreshed = 0
        for rec in due:
            tier, planet = rec["tier"], rec["planet"]
            self._wipe_base(rec["sentinel"])
            logger.info(
                "NPC base (%s on %s) went stale — wiped and regenerating.",
                tier, planet,
            )
            if self.spawn_base(planet, tier) is not None:
                refreshed += 1
        if refreshed:
            self._save_state()
        return refreshed

    def ticks_remaining(self, record: dict, tick_number: int) -> int | None:
        """Ticks until *record*'s base goes stale, or None if not yet disturbed.

        ``None`` = pristine (no timer running). A value <= 0 means the deadline
        has passed (it will be wiped by the next ``process_stale``). Returns None
        when the staleness decay is disabled (``outpost_stale_ticks`` <= 0).
        """
        stale_ticks = self.registry.balance.outpost_stale_ticks
        if stale_ticks <= 0:
            return None
        disturbed_at = record.get("disturbed_at") or 0
        if disturbed_at <= 0:
            return None
        return disturbed_at + stale_ticks - tick_number

    def bases_near(
        self, planet: Any, x: int, y: int, radius: int, tick_number: int
    ) -> list[dict]:
        """Return status for each active base HQ within *radius* of (x, y).

        Chebyshev distance on the same planet. Each entry:
        ``{key, tier, name, x, y, dist, disturbed, ticks_remaining}`` — where
        ``name`` is the tier's display name and ``ticks_remaining`` is None for a
        pristine (undisturbed) base. Sorted nearest-first. Powers the player's
        map proximity readout (see the map command). Pure read — never mutates.
        """
        from world.utils import chebyshev_distance
        out = []
        for key, rec in self._active_bases.items():
            if rec.get("planet") != planet:
                continue
            bx, by = rec.get("x"), rec.get("y")
            if bx is None or by is None:
                continue
            dist = chebyshev_distance(int(x), int(y), int(bx), int(by))
            if dist > radius:
                continue
            template = self.registry.get_base_template(rec.get("tier"))
            name = getattr(template, "display_name", None) or str(
                rec.get("tier", "Base")
            ).title()
            out.append({
                "key": key, "tier": rec.get("tier"), "name": name,
                "x": int(bx), "y": int(by), "dist": dist,
                "disturbed": bool(rec.get("disturbed_at")),
                "ticks_remaining": self.ticks_remaining(rec, tick_number),
            })
        out.sort(key=lambda b: b["dist"])
        return out

    def is_active(self, key: Any) -> bool:
        """Return True if *key* still identifies a tracked, live base.

        Lets the map proximity readout tell "the player walked away" (base still
        active, just out of range) from "the base was wiped" (key gone) so it
        only announces a disappearance for the latter.
        """
        return key in self._active_bases

    def forget_dead_bases(self) -> int:
        """Drop tracked bases whose sentinel HQ was deleted out from under us.

        A base is normally removed via ``_wipe_base`` / the elimination path,
        which pops ``_active_bases`` as it deletes. But an EXTERNAL delete — an
        admin ``obliterate`` sweeping an HQ, say — removes the sentinel without
        telling this system, leaving a stale record that would (a) block
        placement near the now-empty tile via the separation check and (b) drive
        a phantom proximity readout. This reconciles: any record whose sentinel
        is gone (``pk`` is None, or it no longer reports as a sentinel) is
        dropped. Returns the number forgotten. Safe to call anytime; never
        raises. Persists state if anything changed.
        """
        dead = []
        for key, rec in list(self._active_bases.items()):
            sentinel = rec.get("sentinel")
            alive = (
                sentinel is not None
                and getattr(sentinel, "pk", None) is not None
                and bool(get_obj_attr(sentinel, "is_sentinel", False))
            )
            if not alive:
                dead.append(key)
        for key in dead:
            self._active_bases.pop(key, None)
        if dead:
            try:
                self._save_state()
            except Exception:  # noqa: BLE001 - reconcile must not raise
                logger.exception("forget_dead_bases: state save failed")
        return len(dead)

    def wipe_bases_in_area(
        self, planet: Any, x1: int, y1: int, x2: int, y2: int
    ) -> int:
        """Wipe (no respawn) every tracked base whose HQ tile lies in the box.

        The base owner — the Sentinel — carries the base record but is NOT a map
        actor (no coord_x/coord_y, not in the coordinate index), so an area sweep
        that only walks tiles (e.g. admin ``obliterate``) deletes a base's
        buildings/guards yet never the Sentinel — leaving a phantom base in
        tracking + the ``@outpost list``. Callers that clear a region invoke this
        FIRST so each affected base is removed as a UNIT (Sentinel + all owned
        entities via ``_wipe_base``) and untracked. Range is the base's stored HQ
        tile (``rec['x']``/``rec['y']``) against the inclusive box. Returns the
        number of bases wiped. Unlike the staleness/proximity refresh this does
        NOT respawn — an admin clearing a region wants it emptied, not re-seeded.
        """
        victims = [
            rec for rec in self._active_bases.values()
            if rec.get("planet") == planet
            and rec.get("x") is not None and rec.get("y") is not None
            and x1 <= int(rec["x"]) <= x2 and y1 <= int(rec["y"]) <= y2
        ]
        for rec in victims:
            self._wipe_base(rec["sentinel"])  # deletes sentinel + owned, untracks
            logger.info(
                "NPC base (%s on %s at %s,%s) wiped by area clear.",
                rec.get("tier"), planet, rec.get("x"), rec.get("y"),
            )
        if victims:
            self._save_state()
        return len(victims)

    def refresh_base_by_key(self, key: Any) -> bool:
        """Wipe + regenerate the tracked base identified by *key* (its sentinel
        id). Used by the proximity path so a base whose timer expired while a
        player watched is refreshed on the spot. Returns True if a base was
        wiped. No-op (False) for an unknown key.
        """
        rec = self._active_bases.get(key)
        if rec is None:
            return False
        tier, planet = rec["tier"], rec["planet"]
        self._wipe_base(rec["sentinel"])
        logger.info(
            "NPC base (%s on %s) refreshed on proximity (timer expired).",
            tier, planet,
        )
        self.spawn_base(planet, tier)
        self._save_state()
        return True

    def _wipe_base(self, sentinel: Any) -> None:
        """Delete a base's sentinel + all its buildings/guards, and untrack it.

        Reuses the shared owned-entities enumeration (buildings via
        ``get_buildings`` + guards via the owner tag). Unlike the elimination
        path this awards no XP/loot and publishes no BASE_ELIMINATED — it's a
        silent housekeeping refresh, not a player kill. Best-effort per entity so
        one bad delete never aborts the sweep.
        """
        self._active_bases.pop(self._base_key(sentinel), None)
        entities = []
        if self._owned_entities_provider is not None:
            try:
                entities = list(self._owned_entities_provider(sentinel) or [])
            except Exception:  # noqa: BLE001
                logger.exception("Stale wipe: owned-entity enumeration failed")
        for ent in entities:
            self._safe_delete(ent)
        self._safe_delete(sentinel)

    @staticmethod
    def _safe_delete(entity: Any) -> None:
        """Delete *entity* if it supports it; never raise into the sweep."""
        if entity is not None and hasattr(entity, "delete"):
            try:
                entity.delete()
            except Exception:  # noqa: BLE001
                logger.exception("Stale wipe: delete failed for %s",
                                 getattr(entity, "key", "?"))

    def process_respawns(self, tick_number: int) -> int:
        """Spawn any pending respawns whose cooldown has elapsed.

        Wired as the ``"outpost_respawn"`` tick step. Returns the number of
        bases respawned this tick.
        """
        if not self._pending_respawns:
            return 0
        due = [p for p in self._pending_respawns if p["respawn_at"] <= tick_number]
        if not due:
            return 0
        respawned = 0
        backoff = max(
            1, self.registry.balance.outpost_respawn_ticks // _RESPAWN_BACKOFF_DIVISOR
        )
        for pending in due:
            self._pending_respawns.remove(pending)
            if self.spawn_base(pending["planet"], pending["tier"]) is not None:
                respawned += 1
            else:
                # Couldn't place right now (crowded) — back off and retry after a
                # fraction of the cooldown rather than hammering placement every
                # tick (which runs up to _MAX_PLACEMENT_ATTEMPTS DB lookups).
                pending["respawn_at"] = tick_number + backoff
                self._pending_respawns.append(pending)
        # Active-base set and pending list both changed — persist.
        self._save_state()
        return respawned

    # ------------------------------------------------------------------ #
    #  Persistence (Req 7.6) — survive server restarts
    # ------------------------------------------------------------------ #

    def rebuild_from_world(self, sentinels: list | None = None) -> None:
        """Repopulate in-memory state after a server restart.

        Evennia objects (sentinels, buildings) persist across reboots, but this
        system's ``_active_bases`` / ``_pending_respawns`` are in-memory. On
        startup, ``game_init`` calls this with every surviving sentinel so:

        - ``_active_bases`` is rebuilt from the sentinels' stamped
          tier/planet/coords — restoring the base-separation check (otherwise a
          respawn could land on top of a base that survived the reboot); and
        - ``_pending_respawns`` is reloaded from each PlanetRoom's persisted
          attribute — so a base cleared just before a reboot still respawns.

        Idempotent: safe to call once at startup. Never raises.
        """
        try:
            for s in sentinels or ():
                planet = get_obj_attr(s, "base_planet")
                self._active_bases[self._base_key(s)] = {
                    "sentinel": s,
                    "tier": get_obj_attr(s, "base_tier", "outpost"),
                    "planet": planet,
                    "x": get_obj_attr(s, "base_x"),
                    "y": get_obj_attr(s, "base_y"),
                    # Restore the staleness clock — it persists on the sentinel,
                    # so a base disturbed before the reboot keeps ticking (a
                    # crash can't reset the 24h refresh timer).
                    "disturbed_at": get_obj_attr(s, "base_disturbed_at", 0) or 0,
                }
        except Exception:  # noqa: BLE001 - a bad sentinel must not abort startup
            logger.exception("rebuild_from_world: active-base rebuild failed")

        # Reload persisted pending respawns from each planet room.
        try:
            self._pending_respawns = []
            for planet, room in (self._planet_rooms_provider() or {}).items():
                pending = get_obj_attr(room, _PENDING_ATTR, None) or []
                for p in pending:
                    # Copy defensively; ignore malformed rows.
                    if isinstance(p, dict) and "respawn_at" in p:
                        self._pending_respawns.append({
                            "tier": p.get("tier", "outpost"),
                            "planet": p.get("planet", planet),
                            "respawn_at": p["respawn_at"],
                        })
        except Exception:  # noqa: BLE001
            logger.exception("rebuild_from_world: pending-respawn reload failed")

    def purge_outdated_bases(self, sentinels: list | None = None) -> int:
        """Wipe every base whose stamped spec version is older than the current.

        A one-shot startup migration: when ``_BASE_SPEC_VERSION`` is bumped (a
        template overhaul — new tiers, layouts, or rewards), bases spawned under
        the old spec are deleted here so the next re-seed replaces them with
        current-spec bases. A base already at the current version is left alone,
        so this is idempotent across restarts (it only acts the first boot after
        a bump). Returns the number of bases purged. Never raises.

        Call BEFORE ``rebuild_from_world`` at startup so purged sentinels aren't
        re-tracked, and so their planets read as un-seeded and get re-seeded.
        """
        purged = 0
        for s in list(sentinels or ()):
            try:
                version = get_obj_attr(s, "base_spec_version", 0) or 0
                if version >= _BASE_SPEC_VERSION:
                    continue
                self._wipe_base(s)
                purged += 1
            except Exception:  # noqa: BLE001 - one bad sentinel never aborts startup
                logger.exception("purge_outdated_bases: wipe failed for %s",
                                 getattr(s, "key", "?"))
        if purged:
            logger.info(
                "Purged %d outdated NPC base(s) (spec < v%d) for regeneration.",
                purged, _BASE_SPEC_VERSION,
            )
        return purged

    def _save_state(self) -> None:
        """Persist pending respawns onto their PlanetRoom (Req 7.6).

        Pending respawns are primitives (tier/planet/respawn_at), grouped per
        planet and written to that planet's room attribute. PlanetRooms are
        persistent Evennia objects, so the schedule survives a restart. A no-op
        outside a full Evennia env (test rooms lack ``db``/``attributes``), so
        unit tests keep working in-memory.
        """
        try:
            rooms = self._planet_rooms_provider() or {}
        except Exception:  # noqa: BLE001
            return
        if not rooms:
            return
        by_planet: dict[Any, list] = {planet: [] for planet in rooms}
        for p in self._pending_respawns:
            by_planet.setdefault(p["planet"], []).append({
                "tier": p["tier"], "planet": p["planet"],
                "respawn_at": p["respawn_at"],
            })
        for planet, room in rooms.items():
            try:
                set_obj_attr(room, _PENDING_ATTR, by_planet.get(planet, []))
            except Exception:  # noqa: BLE001 - persistence is best-effort
                logger.exception("Failed to persist pending respawns for %s", planet)

    # ------------------------------------------------------------------ #
    #  Placement algorithm
    # ------------------------------------------------------------------ #

    def _find_placement(
        self, planet: str, room: Any, template: Any
    ) -> tuple[int, int] | None:
        """Find a valid HQ tile for *template*, or None after the attempt cap.

        A candidate is valid when every tile the template occupies (HQ + each
        building offset) is in-bounds, on passable terrain, and unoccupied, and
        the HQ tile keeps ``_MIN_BASE_SEPARATION`` from every existing building
        (other NPC bases and player HQs alike).
        """
        space = self._planet_space(planet)
        if space is None:
            return None
        width, height = space.width, space.height

        offsets = [b.offset for b in template.buildings] or [(0, 0)]

        # Keep bases clear of the player spawn point so a new player never spawns
        # inside a fortress (the separation check below only knows about other
        # NPC bases + existing buildings, and a fresh player owns none yet).
        spawn = (getattr(space, "spawn_x", None), getattr(space, "spawn_y", None))
        spawn_pt = spawn if spawn[0] is not None and spawn[1] is not None else None

        for _ in range(_MAX_PLACEMENT_ATTEMPTS):
            hx = self._rng.randint(0, width - 1)
            hy = self._rng.randint(0, height - 1)
            if self._placement_valid(planet, room, hx, hy, offsets, spawn_pt):
                return (hx, hy)
        return None

    def _placement_valid(
        self, planet: str, room: Any, hx: int, hy: int, offsets: list,
        spawn_pt: tuple[int, int] | None = None,
    ) -> bool:
        """Return True if a base with *offsets* fits at HQ tile (hx, hy)."""
        # Keep clear of the player spawn point.
        if spawn_pt is not None:
            if abs(spawn_pt[0] - hx) + abs(spawn_pt[1] - hy) < _MIN_BASE_SEPARATION:
                return False

        # Separation from existing NPC bases (cheap in-memory check first).
        for rec in self._active_bases.values():
            if rec["planet"] != planet:
                continue
            if abs(rec["x"] - hx) + abs(rec["y"] - hy) < _MIN_BASE_SEPARATION:
                return False

        for dx, dy in offsets:
            x, y = hx + dx, hy + dy
            if not self._coord_valid(planet, x, y):
                return False
            if not self._terrain_buildable(planet, x, y):
                return False
            if self._tile_occupied(room, x, y):
                return False
        return True

    def _coord_valid(self, planet: str, x: int, y: int) -> bool:
        if self._planet_registry is None:
            return True
        try:
            return self._planet_registry.is_valid_coordinate(x, y, planet)
        except Exception:  # noqa: BLE001 - unknown planet → invalid
            return False

    def _terrain_buildable(self, planet: str, x: int, y: int) -> bool:
        """True if a base may occupy the tile — passable AND buildable terrain.

        Mirrors the player build rule (BuildingSystem._validate_buildable): a
        tile whose TerrainDef sets ``buildable: false`` (River, Toxic_Waste,
        Lava_Flow, …) is treacherous and hosts no buildings, and an impassable
        tile (Void) can't hold one either. So NPC bases never spawn on a river
        or other hazard the way a player could never build there. Unresolvable
        terrain or a missing provider fails open (don't block placement) so
        legacy rooms/test fakes are unaffected.
        """
        if self._terrain is None:
            return True
        try:
            terrain_type, _res = self._terrain.get_terrain_and_resource(planet, x, y)
        except Exception:  # noqa: BLE001
            return False
        if terrain_type is None:
            return True  # provider unavailable — don't block placement
        try:
            tdef = self.registry.get_terrain(terrain_type)
        except (KeyError, AttributeError):
            return False
        return bool(getattr(tdef, "passable", True)) and bool(
            getattr(tdef, "buildable", True)
        )

    @staticmethod
    def _tile_occupied(room: Any, x: int, y: int) -> bool:
        """True if a building already sits on (x, y)."""
        if room is None or not hasattr(room, "get_buildings_at"):
            return False
        try:
            return bool(room.get_buildings_at(int(x), int(y)))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _planet_space(self, planet: str) -> Any:
        if self._planet_registry is None:
            return None
        try:
            return self._planet_registry.get_space(planet)
        except Exception:  # noqa: BLE001
            return None

    def _resolve_building(self, abbr: str) -> Any:
        try:
            return self.registry.resolve_building(abbr)
        except Exception:  # noqa: BLE001
            return None

    def _guard_hp(self, template: Any, planet: str | None = None) -> int:
        """Default guard HP for *template*, scaled by the planet's ``npc_scale``.

        The base HP is chosen by the template's ``difficulty_class``
        (fortress-class → ``fortress_guard_hp``, else ``outpost_guard_hp``), so a
        new difficulty tier inherits sensible guard HP just by declaring its
        class. A per-guard ``hp`` in the template still overrides this. The scale
        is data-driven from planets.yaml via the injected PlanetRegistry (shared
        ``world.utils.planet_scale`` lookup) — adding a planet needs no code
        change. Unknown planets scale 1.0.
        """
        from world.utils import planet_scale
        bal = self.registry.balance
        cls = getattr(template, "difficulty_class", "outpost")
        base = bal.fortress_guard_hp if cls == "fortress" else bal.outpost_guard_hp
        scale = planet_scale(planet, "npc_scale",
                             planet_registry=self._planet_registry)
        return int(round(base * scale))

    def _sentinel_name(self, template: Any) -> str:
        """A per-base display name, e.g. "Outpost #3"."""
        # Count existing + spawned bases of this tier for a stable-ish suffix.
        n = 1 + sum(1 for r in self._active_bases.values() if r["tier"] == template.tier)
        return f"{template.display_name} #{n}"

    @staticmethod
    def _base_key(sentinel: Any) -> Any:
        """A stable dict key for a base: the sentinel's id (fallback: identity)."""
        return getattr(sentinel, "id", None) or id(sentinel)
