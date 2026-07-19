"""
Base Elimination — the HQ-destruction consequence handler (PvE fork).

Subscribes to ``BUILDING_DESTROYED``. When the destroyed building is an HQ owned
by a Sentinel (an NPC base), it executes the PvE path (Requirement 6):

    1. Delete every other building the sentinel owns.
    2. Delete every guard NPC the sentinel owns.
    3. Delete the sentinel itself.
    4. Award the destroyer ``xp_hq_destroy`` and drop the template's loot at the
       HQ tile.
    5. Publish ``BASE_ELIMINATED`` (the spawner reacts, queuing a respawn).

A player-owned HQ is left entirely alone here — that is the PvP deactivation
path, handled by the ``owner_has_active_hq`` live predicate (Phase 2), not by
deletion. The fork is exactly the ``is-sentinel`` check.

Framework-free: entity enumeration/deletion and loot-drop I/O are injected as
callables at the composition root.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from world.data_registry import DataRegistry
from world.event_bus import BASE_ELIMINATED, BUILDING_DESTROYED, EventBus
from world.systems.base_system import BaseSystem
from world.utils import get_obj_attr, is_owner, is_player

logger = logging.getLogger("evennia.world.systems.base_elimination")


class BaseEliminationHandler(BaseSystem):
    """Wipes an NPC base when its HQ is destroyed and rewards the destroyer.

    Args:
        registry: DataRegistry (balance + base_templates for loot lookup).
        event_bus: EventBus — subscribes to ``BUILDING_DESTROYED`` here.
        owned_entities_provider: callable ``(sentinel) -> list`` returning every
            building AND guard NPC owned by the sentinel (for the mass-delete).
        loot_drop_func: callable ``(room, resource, amount, x, y) -> None`` that
            drops loot on the ground (injected ResourceSystem drop spawner).
        player_xp_awarder_provider: zero-arg callable returning the RankSystem
            (or ``None``). The base-destroy reward (``xp_hq_destroy`` — the
            largest single XP grant in the game) MUST flow through it so the
            destroyer's level/rank recompute and ``LEVEL_CHANGED`` / ``RANK_*``
            fire; a raw ``db.combat_xp`` write does neither.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        owned_entities_provider: Callable[[Any], list] | None = None,
        loot_drop_func: Callable[..., Any] | None = None,
        player_xp_awarder_provider: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._owned_entities_provider = owned_entities_provider
        self._loot_drop_func = loot_drop_func
        self._player_xp_awarder_provider = player_xp_awarder_provider
        #: Sentinel ids currently being wiped — guards against re-entrancy if a
        #: building/guard deletion during the wipe re-publishes BUILDING_DESTROYED.
        self._eliminating: set = set()
        if event_bus is not None:
            event_bus.subscribe(BUILDING_DESTROYED, self.on_building_destroyed)

    def on_building_destroyed(
        self, event_name: str = "", building: Any = None, attacker: Any = None,
        tile: Any = None, **kwargs
    ) -> None:
        """Handle a destroyed building; act only on a Sentinel-owned HQ."""
        if building is None:
            return
        owner = self._building_owner(building)
        if owner is None or not self._is_sentinel(owner):
            return  # player HQ (PvP deactivation) or non-HQ — not our concern
        if not self._is_headquarters(building):
            return
        # Re-entrancy guard: deleting the base's buildings/guards below may
        # itself publish BUILDING_DESTROYED. If one of those were (mis)read as an
        # HQ, this handler could recurse into the same sentinel. Skip if already
        # wiping this owner.
        owner_key = getattr(owner, "id", None)
        if owner_key is None:
            owner_key = id(owner)
        if owner_key in self._eliminating:
            return
        self._eliminating.add(owner_key)
        try:
            self._eliminate_base(owner, building, attacker, tile)
        except Exception:  # noqa: BLE001 - never let a wipe break the event bus
            logger.exception("Base elimination failed for %r", owner)
        finally:
            self._eliminating.discard(owner_key)

    # ------------------------------------------------------------------ #
    #  Elimination
    # ------------------------------------------------------------------ #

    def _eliminate_base(
        self, sentinel: Any, hq: Any, attacker: Any, tile: Any
    ) -> None:
        """Wipe the base, award XP + loot, and publish BASE_ELIMINATED."""
        tier = get_obj_attr(sentinel, "base_tier", "outpost")
        planet = get_obj_attr(sentinel, "base_planet")
        template = self.registry.get_base_template(tier)

        # HQ tile coords (for the loot drop + event) — read BEFORE deleting.
        hx = get_obj_attr(hq, "coord_x")
        hy = get_obj_attr(hq, "coord_y")
        room = getattr(hq, "location", None) or tile

        # 1 & 2. Delete every other building + guard owned by the sentinel. The
        # HQ itself is already being deleted by the combat engine, so skip it.
        # Compare by .id (not identity): owned_entities_provider re-queries the
        # DB, which after an idmapper flush can return a DISTINCT instance of the
        # same HQ — an `is` check would miss it and (harmlessly) re-delete, but
        # the .id skip is precise. Falls back to identity for id-less doubles.
        hq_id = getattr(hq, "id", None)

        def _is_hq(ent: Any) -> bool:
            eid = getattr(ent, "id", None)
            if hq_id is not None and eid is not None:
                return eid == hq_id
            return ent is hq

        for ent in self._owned_entities(sentinel):
            if _is_hq(ent):
                continue
            self._safe_delete(ent)

        # 3. Delete the sentinel (ownership anchor no longer needed).
        self._safe_delete(sentinel)

        # 4. Reward the destroyer: XP + loot. Only a non-owner player earns it
        #    (mirrors the combat-engine anti-farm guard; a sentinel can't own
        #    itself, but keep the guard for symmetry / admin edge cases).
        xp = self.registry.balance.xp_hq_destroy
        awarded_xp = 0
        # A real player only earns the base-destroy reward. NPCs (agents/enemy
        # guards) also satisfy is_player — they carry combat_xp — so exclude any
        # attacker with an npc_type (a real player has none).
        attacker_npc_type = get_obj_attr(attacker, "npc_type", None)
        if (is_player(attacker) and attacker_npc_type is None
                and not is_owner(attacker, sentinel)):
            awarded_xp = xp
            self._award_xp(attacker, xp)

        loot = dict(template.loot) if template else {}
        self._drop_loot(room, loot, hx, hy)
        # Gear/rare drop rolls on HQ destruction (R8.3, R8.4).
        if template is not None:
            self._try_gear_drops(room, template, hx, hy)

        # Notify the destroyer.
        if awarded_xp and is_player(attacker):
            display = template.display_name if template else tier.title()
            self.notify(
                attacker, "base_eliminated",
                tier=display, xp=awarded_xp,
                loot=loot, x=hx, y=hy,
            )

        # 5. Publish so the spawner queues a respawn.
        self.event_bus.publish(
            BASE_ELIMINATED,
            attacker=attacker, sentinel=sentinel, tier=tier,
            planet=planet, x=hx, y=hy,
        )

    # ------------------------------------------------------------------ #
    #  Loot
    # ------------------------------------------------------------------ #

    def _drop_loot(self, room: Any, loot: dict, x: Any, y: Any) -> None:
        """Drop each loot resource on the ground at (x, y).

        Supports both fixed amounts (int) and range syntax ([min, max])
        drawn uniformly (R8.1).
        """
        import random as _rng
        if not loot or self._loot_drop_func is None or room is None:
            return
        for resource, spec in loot.items():
            if isinstance(spec, list) and len(spec) >= 2:
                amount = _rng.randint(min(spec[0], spec[1]), max(spec[0], spec[1]))
            else:
                amount = int(spec) if spec else 0
            if amount <= 0:
                continue
            try:
                self._loot_drop_func(room, resource, amount, x, y)
            except Exception:  # noqa: BLE001 - a bad drop must not abort the wipe
                logger.exception("Loot drop failed for %s x%s", resource, amount)

    def _try_gear_drops(self, room: Any, template: Any, x: Any, y: Any) -> None:
        """Roll gear and rare gear drops on HQ destruction (R8.3, R8.4)."""
        import random as _rng
        if self._loot_drop_func is None or room is None:
            return
        bal = self.registry.balance
        # Normal gear roll
        chance = getattr(template, "gear_drop_chance", None)
        if chance is None:
            chance = getattr(bal, "gear_drop_chance", 0)
        pool = getattr(template, "gear_pool", None) or []
        if pool and _rng.random() < chance:
            item_key = _rng.choice(pool)
            try:
                self._spawn_gear_item(room, item_key, x, y)
            except Exception:  # noqa: BLE001
                logger.exception("Gear drop failed for %s", item_key)
        # Rare gear roll
        rare_chance = getattr(template, "rare_gear_chance", None)
        if rare_chance is None:
            rare_chance = getattr(bal, "rare_gear_chance", 0)
        rare_pool = getattr(template, "rare_pool", None) or []
        if rare_pool and _rng.random() < rare_chance:
            item_key = _rng.choice(rare_pool)
            try:
                self._spawn_gear_item(room, item_key, x, y)
            except Exception:  # noqa: BLE001
                logger.exception("Rare gear drop failed for %s", item_key)

    def _spawn_gear_item(self, room: Any, item_key: str, x: Any, y: Any) -> None:
        """Spawn a gear item on the ground at (x, y).

        Uses the same loot_drop_func as resource drops (spawn_gear_drop is a
        future enhancement — for now drops a resource_drop tagged with item_key
        as a placeholder until the item-drop spawner is wired).
        """
        # In the current architecture, gear items are spawned via the
        # equipment_system or objects.py spawn_gear_drop. For now, we delegate
        # to the loot_drop_func with a special resource name to mark it as gear.
        # Phase 2 scope: register the drop so the player sees it in notifications;
        # actual item creation uses the existing Game_Item spawner.
        try:
            from typeclasses.objects import spawn_gear_drop
            spawn_gear_drop(room, item_key, x=int(x), y=int(y))
        except (ImportError, AttributeError, TypeError):
            # Fallback: log that gear spawning isn't wired yet.
            logger.debug("Gear drop %s: spawn_gear_drop unavailable", item_key)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _owned_entities(self, sentinel: Any) -> list:
        if self._owned_entities_provider is None:
            return []
        try:
            return list(self._owned_entities_provider(sentinel) or [])
        except Exception:  # noqa: BLE001
            logger.exception("owned_entities_provider failed for %r", sentinel)
            return []

    @staticmethod
    def _safe_delete(entity: Any) -> None:
        if entity is not None and hasattr(entity, "delete"):
            try:
                entity.delete()
            except Exception:  # noqa: BLE001 - keep wiping the rest of the base
                logger.exception("Failed to delete %r during base wipe", entity)

    def _award_xp(self, attacker: Any, xp: int) -> None:
        """Award the base-destroy XP through the progression path.

        Prefers the injected RankSystem (``award_xp`` — recompute level/rank +
        fire LEVEL_CHANGED / RANK_*); falls back to the entity's own
        ``CombatEntity.award_xp`` (recompute, no events); last-resort raw write
        for minimal test doubles. A raw ``db.combat_xp`` write alone would leave
        the destroyer's level/rank stale — and this is the game's largest single
        XP grant, so a rank-up here matters most. Guarded so a wipe never breaks.
        """
        if xp <= 0:
            return
        rank_system = None
        provider = self._player_xp_awarder_provider
        if provider is not None:
            try:
                rank_system = provider()
            except Exception:  # noqa: BLE001 - resolution must not break the wipe
                rank_system = None
        if rank_system is not None and hasattr(rank_system, "award_xp"):
            try:
                rank_system.award_xp(attacker, xp, reason="base_destroy")
                return
            except Exception:  # noqa: BLE001 - fall through to entity-local award
                logger.exception("RankSystem award_xp failed on base destroy")
                return  # do NOT re-award: award_xp mutates before it can raise
        if hasattr(attacker, "award_xp"):
            try:
                attacker.award_xp(xp)
                return
            except Exception:  # noqa: BLE001 - fall through to raw set
                pass
        db = getattr(attacker, "db", None)
        if db is not None:
            db.combat_xp = (getattr(db, "combat_xp", 0) or 0) + xp

    @staticmethod
    def _building_owner(building: Any) -> Any:
        if hasattr(building, "owner"):
            return building.owner
        return get_obj_attr(building, "owner")

    @staticmethod
    def _is_sentinel(owner: Any) -> bool:
        """True if *owner* is a Sentinel Character (an NPC base owner)."""
        return bool(get_obj_attr(owner, "is_sentinel", False))

    def _is_headquarters(self, building: Any) -> bool:
        from world.constants import HEADQUARTERS
        from world.utils import building_has_capability
        return building_has_capability(
            building, HEADQUARTERS, provider=self.registry
        )
