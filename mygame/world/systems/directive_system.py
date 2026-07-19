"""
Directive System — the onboarding checklist + deed awards (early-game rebalance).

Two responsibilities, sharing one payload-resolution helper (D7):

1. **Directives (R10)**: a per-player ordered checklist loaded from
   ``data/definitions/directives.yaml``. Each directive names a trigger event
   (from the existing EventBus), an optional payload condition, and a one-time
   reward (XP + resources). When the player on step N performs the matching
   action, the reward is granted, the step completes, and the next objective is
   announced. The chain is dismissable as a whole (``directives off`` — D2):
   muted players still ADVANCE silently (no reward, no notification) so a
   returning player is never stuck mid-chain; forfeited rewards are not
   back-paid.

2. **Deeds (R9/D9)**: counted milestone records on ``player.db.deeds``
   (deed-id → count). Subscribes to ``BASE_ELIMINATED`` and increments
   ``outpost_cleared`` / ``fortress_cleared`` for the crediting player. Deed
   gates on buildings read these counts (``BuildingSystem
   ._validate_deed_requirement``).

**Payload adapter (D7)**: each directive may declare ``player_key`` (default
``"player"``) naming the event-payload key that carries the acting entity.
When the resolved entity is an NPC/agent/turret rather than a player, credit
resolves to its owner (``db.owner``) — delegation is never penalized
(consistent with D1). Events whose resolved actor is not a player are
discarded without side effects.

Framework-free (no Evennia imports at module scope) per the layering invariant.
"""

from __future__ import annotations

from typing import Any

from world.event_bus import BASE_ELIMINATED, EventBus
from world.systems.base_system import BaseSystem
from world.constants import DEED_OUTPOST_CLEARED, DEED_FORTRESS_CLEARED

import logging

logger = logging.getLogger("mygame.directive_system")


class DirectiveSystem(BaseSystem):
    """Onboarding directive chain + deed awards.

    Args:
        registry: DataRegistry holding ``directives`` (the ordered list loaded
            from directives.yaml) and balance config.
        event_bus: EventBus to subscribe on.
    """

    def __init__(self, registry: Any, event_bus: EventBus) -> None:
        super().__init__(registry, event_bus)
        #: Ordered directive dicts (keys: key, description, trigger_event,
        #: condition, reward, player_key). Empty when the YAML is absent.
        self._directives: list[dict] = list(
            getattr(registry, "directives", None) or []
        )
        #: trigger_event -> [directive, ...] (an event can trigger several
        #: steps, e.g. PATROL_SET drives both step 7 and step 10).
        self._by_event: dict[str, list[dict]] = {}
        for d in self._directives:
            self._by_event.setdefault(d.get("trigger_event", ""), []).append(d)
        self._subscribe_all()

    # ------------------------------------------------------------------ #
    #  Subscriptions
    # ------------------------------------------------------------------ #

    def _subscribe_all(self) -> None:
        """Subscribe once per distinct trigger event + the deed source."""
        for event_name in self._by_event:
            if event_name:
                self.event_bus.subscribe(event_name, self._on_event)
        # Deed awards ride BASE_ELIMINATED even when no directive uses it.
        if BASE_ELIMINATED not in self._by_event:
            self.event_bus.subscribe(BASE_ELIMINATED, self._on_event)

    # ------------------------------------------------------------------ #
    #  Event handling
    # ------------------------------------------------------------------ #

    def _on_event(self, event_name: str = "", **payload: Any) -> None:
        """Route one event: deed awards first, then directive advancement."""
        # 1. Deed awards (R9) — BASE_ELIMINATED increments the tier deed.
        if event_name == BASE_ELIMINATED:
            try:
                self._award_base_deed(payload)
            except Exception:  # noqa: BLE001 - a deed award never breaks the wipe
                logger.exception("Deed award failed for BASE_ELIMINATED")

        # 2. Directive advancement (R10).
        for directive in self._by_event.get(event_name, ()):
            try:
                self._try_advance(directive, payload)
            except Exception:  # noqa: BLE001 - one player's failure is isolated
                logger.exception(
                    "Directive advance failed for %r", directive.get("key")
                )

    # ------------------------------------------------------------------ #
    #  Deeds (R9/D9)
    # ------------------------------------------------------------------ #

    def _award_base_deed(self, payload: dict) -> None:
        """Increment the tier deed for the player credited with the wipe."""
        player = self._resolve_player({"player_key": "attacker"}, payload)
        if player is None:
            return
        tier = (payload.get("tier") or "").lower()
        deed = {
            "outpost": DEED_OUTPOST_CLEARED,
            "fortress": DEED_FORTRESS_CLEARED,
        }.get(tier)
        if deed is None:
            return
        self.award_deed(player, deed)

    @staticmethod
    def award_deed(player: Any, deed_id: str, count: int = 1) -> None:
        """Increment *deed_id* on ``player.db.deeds`` (read-modify-reassign)."""
        db = getattr(player, "db", None)
        if db is None:
            return
        deeds = dict(getattr(db, "deeds", None) or {})
        deeds[deed_id] = deeds.get(deed_id, 0) + count
        db.deeds = deeds

    # ------------------------------------------------------------------ #
    #  Directives (R10)
    # ------------------------------------------------------------------ #

    def _try_advance(self, directive: dict, payload: dict) -> None:
        """Advance the resolved player if they're on *directive* and it matches."""
        player = self._resolve_player(directive, payload)
        if player is None:
            return
        db = getattr(player, "db", None)
        if db is None:
            return
        idx = getattr(db, "directives_progress", 0) or 0
        if idx >= len(self._directives):
            return  # chain complete
        if self._directives[idx] is not directive:
            return  # player isn't on this step
        if not self._check_condition(directive, payload):
            return
        self._complete_directive(player, directive, idx)

    def _complete_directive(self, player: Any, directive: dict, idx: int) -> None:
        """Reward (unless muted), advance, announce the next objective."""
        db = player.db
        muted = bool(getattr(db, "directives_muted", False))

        # Advance FIRST (idempotence: a re-entrant event sees the new index).
        db.directives_progress = idx + 1

        if not muted:
            self._grant_reward(player, directive)
            self.notify(
                player, "directive_complete",
                description=directive.get("description", directive.get("key", "?")),
                reward=directive.get("reward") or {},
            )
            # Announce the next objective, if any.
            if idx + 1 < len(self._directives):
                nxt = self._directives[idx + 1]
                self.notify(
                    player, "directive_next",
                    description=nxt.get("description", nxt.get("key", "?")),
                )
            else:
                self.notify(player, "directives_all_complete")

    def _grant_reward(self, player: Any, directive: dict) -> None:
        """Grant the directive's XP + resource reward (R10.3a)."""
        reward = dict(directive.get("reward") or {})
        xp = int(reward.pop("xp", 0) or 0)
        # Resources first (never blocked by an XP failure).
        for resource, amount in reward.items():
            try:
                if hasattr(player, "add_resource"):
                    player.add_resource(resource, int(amount))
            except Exception:  # noqa: BLE001
                logger.exception("Directive resource grant failed: %s", resource)
        if xp > 0:
            from world.utils import award_player_xp
            award_player_xp(player, xp, reason="directive")

    # ------------------------------------------------------------------ #
    #  Payload adapter (D7)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_player(directive: dict, payload: dict) -> Any | None:
        """Resolve the acting player from an event payload.

        Reads the payload key named by the directive's ``player_key`` (default
        ``"player"``). An NPC/agent/turret actor resolves to its owner via
        ``db.owner``. Returns None when the resolved actor is not a player.
        """
        from world.utils import is_player

        actor = payload.get(directive.get("player_key") or "player")
        if actor is None:
            return None
        db = getattr(actor, "db", None)
        # An NPC (carries npc_type) or a building/turret (no combat_xp but an
        # owner) resolves to its owner (D7 — delegation never penalized).
        if db is not None and getattr(db, "npc_type", None) is not None:
            actor = getattr(db, "owner", None)
        elif not is_player(actor):
            owner = getattr(db, "owner", None) if db is not None else None
            actor = owner
        if actor is None or not is_player(actor):
            return None
        # Exclude NPC owners (e.g. Sentinel) — only real players hold directives.
        actor_db = getattr(actor, "db", None)
        if actor_db is not None and getattr(actor_db, "npc_type", None) is not None:
            return None
        return actor

    @staticmethod
    def _check_condition(directive: dict, payload: dict) -> bool:
        """Match the directive's optional condition against the payload.

        Every key in ``condition`` must equal the payload value. Two special
        conventions used by the shipped chain:

        - ``building_type: HQ`` matches ``payload["building"].db.building_type``
          (CONSTRUCTION_COMPLETED carries the building object, not its type).
        - ``new_level: 2`` (BUILDING_UPGRADED) compares as int.
        - ``base_kind: outpost`` matches ``payload["tier"]``.
        - ``role: scout`` matches ``payload["role"]`` (PATROL_SET / AGENT_ASSIGNED).
        """
        condition = directive.get("condition") or {}
        for key, expected in condition.items():
            if key == "building_type":
                building = payload.get("building")
                actual = getattr(getattr(building, "db", None), "building_type", None)
                if actual is None and building is not None:
                    actual = getattr(building, "building_type", None)
            elif key == "base_kind":
                actual = payload.get("tier")
            else:
                actual = payload.get(key)
            if isinstance(expected, int):
                try:
                    if int(actual) != expected:
                        return False
                except (TypeError, ValueError):
                    return False
            elif str(actual or "").lower() != str(expected or "").lower():
                return False
        return True

    # ------------------------------------------------------------------ #
    #  Views (the `directives` command)
    # ------------------------------------------------------------------ #

    def get_progress_view(self, player: Any) -> dict:
        """Return the player's chain state for the ``directives`` command."""
        db = getattr(player, "db", None)
        idx = (getattr(db, "directives_progress", 0) or 0) if db else 0
        muted = bool(getattr(db, "directives_muted", False)) if db else False
        steps = []
        for i, d in enumerate(self._directives):
            steps.append({
                "key": d.get("key", f"step_{i + 1}"),
                "description": d.get("description", "?"),
                "done": i < idx,
                "current": i == idx,
            })
        return {"steps": steps, "progress": idx,
                "total": len(self._directives), "muted": muted}

    @staticmethod
    def set_muted(player: Any, muted: bool) -> None:
        """Set the dismiss-all flag (D2). Muted = silent advance, no rewards."""
        db = getattr(player, "db", None)
        if db is not None:
            db.directives_muted = bool(muted)
