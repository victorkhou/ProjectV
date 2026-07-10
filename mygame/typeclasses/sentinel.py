"""
SentinelCharacter — the non-puppeted owner of an NPC base.

Each NPC outpost/fortress is owned by one Sentinel: a Character whose ``.id``
serves as the ownership key for every building and guard in that base. This
reuses the existing ``db.owner`` reference and ``is_owner`` (.id) friend/foe
rule everywhere — turret targeting, the map renderer's enemy coloring, the
XP/anti-farm guards — with no new faction system.

**Why a Character (not a plain Object):** the base-deactivation predicate
``owner_has_active_hq`` (and ``BuildingSystem._player_has_hq``) enumerate an
owner's buildings via ``owner.get_buildings()``, which is defined ONLY on the
game ``CombatCharacter`` typeclass. A bare object has no ``get_buildings()``, so
its turrets/guards would never activate. SentinelCharacter subclasses
CombatCharacter to inherit that enumeration while staying inert as a "player":
never puppeted, never in ``who``, never an online player, never notified
(Requirement 5.6). A never-puppeted Character satisfies all of that for free —
``has_account`` is False (so target acquisition, which filters on it, never
fires at a sentinel), and Evennia's ``who``/online-player scans only list
connected sessions — with ``msg`` overridden to a hard no-op as belt-and-braces.
"""

from __future__ import annotations

import logging

from typeclasses.characters import CombatCharacter

logger = logging.getLogger("evennia.typeclasses.sentinel")


class SentinelCharacter(CombatCharacter):
    """Non-puppeted owner of an NPC base's buildings and guards.

    Inherits ``get_buildings()`` (for the ownership enumeration that
    ``owner_has_active_hq`` needs) from :class:`CombatCharacter`, but is inert
    as a player: it is never puppeted and swallows all messages.
    """

    def at_object_creation(self):
        """Initialize as a CombatCharacter, then flag/tag as a sentinel."""
        super().at_object_creation()
        # Marker attribute + tag so the sentinel is identifiable for cleanup and
        # excluded from any player-facing enumeration that checks it. The
        # ``is_sentinel`` attribute is the authoritative signal (read by the
        # base-elimination handler); the tag is a queryable index for the
        # spawn-idempotency search and is added best-effort.
        self.db.is_sentinel = True
        if hasattr(self, "tags"):
            self.tags.add("sentinel", category="npc_role")

    def msg(self, text=None, from_obj=None, session=None, **kwargs):
        """Drop (but trace) player-facing output — a sentinel is never messaged.

        Notifications routed to a base owner (e.g. ``building_attacked``) target
        the sentinel; dropping them here keeps that path free of ``if is_player``
        special-casing while guaranteeing a sentinel is never "notified"
        (Req 5.6). Logged at debug so the drop is observable when diagnosing a
        misrouted message, rather than vanishing silently.
        """
        logger.debug("SentinelCharacter.msg dropped for %s: %r",
                     getattr(self, "key", "?"), text)
        return None
