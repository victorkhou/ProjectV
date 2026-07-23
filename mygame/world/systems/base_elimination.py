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
from world.event_bus import (
    BASE_ELIMINATED,
    BUILDING_DESTROYED,
    NPC_ELIMINATED,
    EventBus,
)
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
            # Per-guard mini-drops (R8.2) ride the guard-death event. The
            # engine publishes NPC_ELIMINATED BEFORE deleting the victim, so
            # its owner/coords are still readable here.
            event_bus.subscribe(NPC_ELIMINATED, self.on_npc_eliminated)

    def on_npc_eliminated(
        self, event_name: str = "", victim: Any = None, attacker: Any = None,
        tile: Any = None, attacker_owner: Any = None, **kwargs
    ) -> None:
        """Roll the per-guard-kill mini-drop (R8.2).

        Each NPC-base guard kill has ``guard_loot_chance`` of dropping
        ``guard_loot_amount`` of one random resource from the base's loot
        table at the guard's tile — the instant-gratification beat between
        HQ payouts. Only guards owned by a Sentinel (outpost/fortress)
        qualify; template values override the balance defaults.
        """
        import random as _rng
        if victim is None or self._loot_drop_func is None:
            return
        owner = get_obj_attr(victim, "owner")
        if owner is None or not self._is_sentinel(owner):
            return
        tier = get_obj_attr(owner, "base_tier", "outpost")
        template = self.registry.get_base_template(tier)
        loot_table = dict(getattr(template, "loot", None) or {})
        if not loot_table:
            return
        if _rng.random() >= self._tunable(template, "guard_loot_chance", 0):
            return
        amount = self._roll_amount(self._tunable(template, "guard_loot_amount", 0))
        if amount <= 0:
            return
        resource = _rng.choice(sorted(loot_table))
        room = getattr(victim, "location", None) or tile
        x = get_obj_attr(victim, "coord_x")
        y = get_obj_attr(victim, "coord_y")
        if room is None:
            return
        try:
            self._loot_drop_func(room, resource, amount, x, y)
        except Exception:  # noqa: BLE001 - a bad drop must not break the kill
            logger.exception("Guard mini-drop failed for %s x%s", resource, amount)
            return
        if attacker_owner is not None and is_player(attacker_owner):
            self.notify(
                attacker_owner, "guard_loot",
                resource=resource, amount=amount, x=x, y=y,
            )

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
        #    XP is difficulty-scaled: the template's ``xp_reward`` overrides the
        #    balance ``xp_hq_destroy`` default, so a fortress pays out far more
        #    than an easy outpost.
        xp = self._tunable_xp(template)
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
        if not loot or self._loot_drop_func is None or room is None:
            return
        for resource, spec in loot.items():
            amount = self._roll_amount(spec)
            if amount <= 0:
                continue
            try:
                self._loot_drop_func(room, resource, amount, x, y)
            except Exception:  # noqa: BLE001 - a bad drop must not abort the wipe
                logger.exception("Loot drop failed for %s x%s", resource, amount)

    def _try_gear_drops(self, room: Any, template: Any, x: Any, y: Any) -> None:
        """Roll gear and rare gear drops on HQ destruction (R8.3, R8.4).

        Each ROUND makes two independent rolls (normal + rare), each: pool
        non-empty AND ``random() < chance`` → spawn one random item from the
        pool. The template's ``gear_rolls`` (default 1) sets how many rounds to
        run, so a difficult base can rain several upgrades from one wipe
        (difficulty-scaled loot).
        """
        import random as _rng
        if self._loot_drop_func is None or room is None:
            return
        rounds = max(1, int(getattr(template, "gear_rolls", 1) or 1))
        for _ in range(rounds):
            for chance_key, pool_key in (
                ("gear_drop_chance", "gear_pool"),
                ("rare_gear_chance", "rare_pool"),
            ):
                chance = self._tunable(template, chance_key, 0)
                pool = getattr(template, pool_key, None) or []
                if pool and _rng.random() < chance:
                    self._spawn_gear_item(room, _rng.choice(pool), x, y)

    def _tunable_xp(self, template: Any) -> int:
        """HQ-destroy XP for *template*: its ``xp_reward``, else ``xp_hq_destroy``.

        Difficulty-scaled: a tier that declares ``xp_reward`` pays that out; one
        that doesn't falls back to the global balance default. Never negative.
        """
        value = getattr(template, "xp_reward", None) if template is not None else None
        if value is None:
            value = getattr(self.registry.balance, "xp_hq_destroy", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _tunable(self, template: Any, key: str, default: Any = 0) -> Any:
        """Read *key* from the base template, falling back to balance.

        The shared template-overrides-balance rule for every variable-reward
        knob (guard mini-drops, gear/rare chances) — one lookup order, no
        per-site drift.
        """
        value = getattr(template, key, None)
        if value is None:
            value = getattr(self.registry.balance, key, default)
        return value if value is not None else default

    @staticmethod
    def _roll_amount(spec: Any) -> int:
        """Resolve a loot amount spec: ``[min, max]`` → uniform roll, int → int.

        The single range-syntax resolver (R8.1) shared by base loot and guard
        mini-drops. Misordered ranges are clamped (min/max swap); falsy or
        malformed specs resolve to 0 (caller skips).
        """
        import random as _rng
        if isinstance(spec, list) and len(spec) >= 2:
            return _rng.randint(min(spec[0], spec[1]), max(spec[0], spec[1]))
        try:
            return int(spec) if spec else 0
        except (TypeError, ValueError):
            return 0

    def _spawn_gear_item(self, room: Any, item_key: str, x: Any, y: Any) -> None:
        """Spawn the gear item *item_key* as a ground drop at (x, y).

        Resolves the key to its ``ItemDef`` via the registry (pool keys are
        load-time validated against item definitions — R11.5) and creates a
        real Gear ``GameItem`` via ``spawn_gear_drop`` (which expects an
        ItemDef, not a key string). Failures are logged at ERROR — a won gear
        roll must never vanish silently (the anti-dopamine failure R11.5
        exists to prevent).
        """
        item_def = (self.registry.items or {}).get(item_key)
        if item_def is None:
            # Should be impossible after load-time pool validation (R11.5).
            logger.error("Gear drop %r: no such item definition", item_key)
            return
        try:
            from typeclasses.objects import spawn_gear_drop
        except ImportError:
            logger.debug("Gear drop %s: Evennia unavailable (test env)", item_key)
            return
        try:
            if spawn_gear_drop(room, item_def, x=int(x), y=int(y)) is None:
                logger.warning("Gear drop %s refused (tile full) at (%s, %s)",
                               item_key, x, y)
        except Exception:  # noqa: BLE001 - a bad drop must not abort the wipe
            logger.exception("Gear drop failed for %s", item_key)

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
