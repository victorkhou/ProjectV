"""
Shared gates for player-operated *bench* buildings.

A bench is a building whose owner stands in it and issues a command against it —
the Blacksmith (``insert``/``reroll``/``salvage``), the Refinery (``refine``),
the equipment buildings (``craft``), and the Survey Array (``survey``). Every one
of them needs the same two checks in the same order before it may act:

1. is this building the caller's, and is it operational (not offline, not
   mid-upgrade/construction), and
2. can the caller afford the cost, charged BEFORE the effect is applied.

Both are security-relevant and must not exist as drifting copies, so they live
here once. This is a mixin rather than a method on ``BaseSystem`` deliberately:
only the two systems that own a bench have any concept of building ownership,
while ``BaseSystem`` is inherited by every system in the game (regen, rank,
targeting, alliances, …). Mixing the vocabulary in where it is needed matches
the existing ``CarryWeightMixin`` / ``StorageMixin`` / ``AgentProgressionMixin``
idiom and keeps ``world.utils``' building helpers out of the root abstraction.

Requires the host class to provide ``notify`` (i.e. to also inherit
:class:`~world.systems.base_system.BaseSystem`).
"""

from __future__ import annotations

from typing import Any


class BenchGateMixin:
    """Ownership/operational and resource-cost gates for a bench building."""

    def check_owner_operational(
        self, player: Any, building: Any, fail_kind: str, **payload: Any
    ) -> bool:
        """Return True when *player* owns *building* and it is operational.

        On failure emits *fail_kind* with the matching ``reason``
        (``not_owner`` / ``building_offline`` / ``building_upgrading``) plus the
        caller's *payload* (e.g. ``item_name=...``) and returns ``False``.

        The building-capability/catalog gate is the CALLER's job and must pass
        first, so *building* is non-``None`` here. There is deliberately NO
        active-HQ usage gate (item-loot-economy design §4.1) — callers that want
        one apply it themselves at the command layer.
        """
        from world.utils import is_owner, get_building_attr, get_obj_attr

        owner = getattr(building, "owner", None)
        if owner is None:
            owner = get_building_attr(building, "owner")
        if not is_owner(player, owner):
            self.notify(player, fail_kind, reason="not_owner", **payload)
            return False
        if getattr(building, "is_offline", False):
            self.notify(player, fail_kind, reason="building_offline", **payload)
            return False
        if get_obj_attr(building, "under_construction", False):
            self.notify(player, fail_kind, reason="building_upgrading", **payload)
            return False
        return True

    def charge_resources(
        self, player: Any, cost: dict, fail_kind: str, **payload: Any
    ) -> bool:
        """Deduct *cost* from *player*, or notify and return ``False``.

        Deduct-BEFORE-effect: callers apply their effect only once this returns
        ``True``, so a failed spend can never mint a free result. An empty cost
        is free and always succeeds.

        Fails CLOSED. A holder with no resource pool cannot pay, so it is
        refused rather than waved through — a spend path must never default to
        granting the effect. On refusal emits *fail_kind* with
        ``reason="insufficient_resources"`` and, when it can be computed, the
        shared have/need ``breakdown``.
        """
        if not cost:
            return True
        has = getattr(player, "has_resources", None)
        deduct = getattr(player, "deduct_resources", None)
        if not callable(has) or not callable(deduct):
            self.notify(player, fail_kind, reason="insufficient_resources",
                        **payload)
            return False
        if not has(cost) or not deduct(cost):
            from world.utils import format_insufficient_resources
            self.notify(
                player, fail_kind, reason="insufficient_resources",
                breakdown=format_insufficient_resources(player, cost),
                **payload,
            )
            return False
        return True
