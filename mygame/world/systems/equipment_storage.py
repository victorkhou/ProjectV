"""
Vault/HQ resource storage for the equipment/items feature (D8, D9).

``StorageMixin`` holds the Spend_Pool ↔ Storage_Building transfers
(``deposit``/``withdraw``) and the over-capacity inflow choke point
(``add_resource_capped`` + ground-drop spill). It is combined into
``EquipmentSystem`` via inheritance (mirroring the ``AgentSystem`` mixin
split), so the public API and every ``self.`` call-site are unchanged. Depends
on ``self.registry``/``self.notify`` from ``BaseSystem``, the injected
``_resource_drop_spawner``, the carry-weight methods from ``CarryWeightMixin``
(``carried_weight``/``carry_limit``/``_resource_weight_room``/
``_get_player_resource``), and ``self._item_name``.

"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mygame.equipment_system")


class StorageMixin:
    """Vault/HQ deposit/withdraw and the over-capacity inflow choke point."""

    def deposit(
        self, player: Any, building: Any, resource: str, amount: int | None
    ) -> int:
        """Move up to *amount* of *resource* from *player* into *building* (Req 16.3).

        The Spend_Pool → Storage_Building half of the D8 storage model. The
        co-located ``storage``-capability *building* is located by the caller
        (the ``deposit`` command, task 7.5, via ``buildings_here`` +
        ``building_has_capability("storage")``) and passed in explicitly, so the
        framework-free use-case never performs a coordinate lookup.

        The amount actually deposited is bounded by both what the player holds
        and the building's remaining ``storage_capacity``::

            want   = min(amount, player.get_resource(resource))
            stored = deposit_to_building(building, resource, want)  # capacity-capped

        Only the amount actually stored is deducted from the player, so a full
        building never destroys the player's resources — the surplus simply stays
        on their person (Req 16.8). Emits a ``deposited`` notification with the
        building's new total stored against its capacity. Never raises into the
        command layer.

        Args:
            player: The depositing player (Spend_Pool source).
            building: The co-located Storage_Building (stored-pool sink).
            resource: The resource type to move.
            amount: The number of units requested, or ``None`` to deposit all
                the player currently holds of *resource*.

        Returns:
            The number of units actually deposited (0..amount).
        """
        if building is None:
            return 0

        from world.systems import building_storage

        # Cap by what the player actually holds in their Spend_Pool. ``None``
        # means "all held" (the `deposit iron all` / bare form).
        held = int(self._get_player_resource(player, resource))
        if amount is None:
            want = held
        else:
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                return 0
            if amount <= 0:
                return 0
            want = min(amount, held)
        if want <= 0:
            # The player holds none of that resource — tell them, don't hang.
            self.notify(player, "deposit_failed", resource=resource,
                        reason="nothing_held")
            return 0

        # Cap by the building's remaining storage_capacity (done inside
        # deposit_to_building, which returns the amount actually stored).
        stored = int(building_storage.deposit_to_building(building, resource, want))
        if stored <= 0:
            self.notify(player, "deposit_failed", resource=resource,
                        reason="building_full")
            return 0

        # Deduct from the player only what was actually stored (Req 16.8).
        deduct = getattr(player, "deduct_resources", None)
        if callable(deduct):
            deduct({resource: stored})

        self.notify(
            player,
            "deposited",
            amount=stored,
            resource=resource,
            building=self._item_name(building),
            stored=building_storage.get_total_stored(building),
            capacity=building_storage.get_storage_capacity(building),
        )
        return stored

    def withdraw(
        self, player: Any, building: Any, resource: str, amount: int | None
    ) -> int:
        """Move up to *amount* of *resource* from *building* into *player* (Req 16.4).

        The Storage_Building → Spend_Pool half of the D8 storage model. The
        co-located ``storage``-capability *building* is located by the caller
        (the ``withdraw`` command, task 7.5) and passed in explicitly.

        The amount actually withdrawn is bounded by what the building stores and
        by the player's remaining carry-weight room converted to a resource
        count (Req 16.4, 16.5)::

            room       = floor((carry_limit − carried_weight) / resource_weight)
            want       = min(amount, stored_in_building, room)
            withdrawn  = withdraw_from_building(building, resource, want)

        The leftover stays in storage — it is never dropped — and the fitting
        amount is added to the player's Spend_Pool. Admins have unbounded room
        (Req 15.6). Emits a ``withdrew`` notification with the player's carried
        weight against their limit. Never raises into the command layer.

        Args:
            player: The withdrawing player (Spend_Pool sink).
            building: The co-located Storage_Building (stored-pool source).
            resource: The resource type to move.
            amount: The number of units requested, or ``None`` to withdraw as
                much as the building stores (still capped by carry weight).

        Returns:
            The number of units actually withdrawn (0..amount).
        """
        if building is None:
            return 0

        from world.systems import building_storage

        # Cap by what the building actually stores.
        available = int(building_storage.get_stored(building, resource))
        if available <= 0:
            self.notify(player, "withdraw_failed", resource=resource,
                        reason="nothing_stored")
            return 0

        # ``None`` means "as much as stored" (the `withdraw iron all` / bare
        # form); still bounded below by carry weight.
        if amount is None:
            amount = available
        else:
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                return 0
            if amount <= 0:
                return 0

        # Cap by the player's remaining carry-weight room (as a resource count).
        room = self._resource_weight_room(player, resource)
        if room == float("inf"):
            want = min(amount, available)
        else:
            want = int(min(amount, available, room))
        if want <= 0:
            # There is stock but the player has no carry-weight room for it.
            self.notify(player, "withdraw_failed", resource=resource,
                        reason="carry_full")
            return 0

        # Remove exactly `want` from the building; leftover stays in storage.
        withdrawn = int(
            building_storage.withdraw_from_building(building, resource, want)
        )
        if withdrawn <= 0:
            return 0

        add = getattr(player, "add_resource", None)
        if callable(add):
            add(resource, withdrawn)

        self.notify(
            player,
            "withdrew",
            amount=withdrawn,
            resource=resource,
            carried=self.carried_weight(player),
            limit=self.carry_limit(player),
        )
        return withdrawn

    def add_resource_capped(
        self, holder: Any, resource: str, amount: int
    ) -> int:
        """Add up to *amount* units of *resource* to *holder*'s pool (D9).

        The single funnel for inflows that write a **holder pool** — a player's
        Spend_Pool (``db.resources``) or a Storage_Building's stored-resource
        pool. It adds ``min(amount, room)`` where *room* depends on the holder:

        - **Player** — the carry-weight room converted to a resource count.
          ``weight_room = carry_limit(player) − carried_weight(player)``; since a
          resource has a per-unit weight (``resource_weights[resource]``,
          defaulting to :data:`~world.constants.DEFAULT_RESOURCE_WEIGHT`), the
          count that fits by weight is ``floor(weight_room / resource_weight)``.
          A non-positive resource weight is not a binding constraint (room = ∞);
          an admin's unbounded ``carry_limit`` also yields room = ∞ (admins
          bypass the cap — Req 15.6). The fitting amount is added via
          ``player.add_resource(resource, added)``.
        - **Building** (``storage`` capability) — the ``storage_capacity`` room
          via :func:`building_storage.get_remaining_capacity`; the fitting
          amount is stored via :func:`building_storage.deposit_to_building`.

        Any remainder (``amount − added``) is spawned as a ``ResourceDrop`` at
        the holder's coordinates via the injected resource-drop spawner so no
        resource is ever destroyed (Req 16.8), and the owning player is notified
        ``carry_full`` (player holder) or ``storage_full`` (building holder).
        Carry weight is computed on-demand here, never per tick. Never raises
        into the caller.

        Args:
            holder: The receiving entity — a player (Spend_Pool) or a
                ``storage``-capability building (stored-resource pool).
            resource: The resource type flowing in.
            amount: The number of units offered by the inflow.

        Returns:
            The number of units actually added to the holder's pool (0..amount).
        """
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return 0
        if amount <= 0:
            return 0

        from world.utils import is_building, is_player

        if is_player(holder):
            return self._add_resource_capped_player(holder, resource, amount)
        if is_building(holder):
            return self._add_resource_capped_building(holder, resource, amount)

        # Unknown holder shape — cannot determine a pool to write. Log and add
        # nothing rather than silently destroying the inflow.
        logger.warning(
            "add_resource_capped: %r is neither a player nor a building; "
            "%d %s not added",
            getattr(holder, "key", holder), amount, resource,
        )
        return 0

    def _add_resource_capped_player(
        self, player: Any, resource: str, amount: int
    ) -> int:
        """Add *amount* of *resource* to a player's Spend_Pool, carry-capped.

        See :meth:`add_resource_capped`. Admins bypass the cap and receive the
        full amount. Otherwise the amount is capped to the carry-weight room
        converted to a resource count; the remainder spills to a ground drop and
        the player is notified ``carry_full``.
        """
        # Room (in resource count) by carry weight. An admin's ∞ carry_limit,
        # or a non-positive resource weight, means weight is not a binding
        # constraint (room = ∞); shared with :meth:`withdraw`.
        room = self._resource_weight_room(player, resource)

        added = int(amount if room == float("inf") else max(0, min(amount, room)))
        if added > 0:
            player.add_resource(resource, added)

        dropped = amount - added
        if dropped > 0:
            self._spawn_resource_drop(player, resource, dropped)
            self.notify(
                player,
                "carry_full",
                item_name=resource,
                carried=added,
                dropped=dropped,
            )
        return added

    def _add_resource_capped_building(
        self, building: Any, resource: str, amount: int
    ) -> int:
        """Add *amount* of *resource* to a building's stored pool, capacity-capped.

        See :meth:`add_resource_capped`. The amount is capped to the building's
        remaining ``storage_capacity``; the remainder spills to a ground drop at
        the building and the owning player is notified ``storage_full``.
        """
        from world.systems import building_storage

        added = int(
            building_storage.deposit_to_building(building, resource, amount)
        )

        dropped = amount - added
        if dropped > 0:
            self._spawn_resource_drop(building, resource, dropped)
            owner = getattr(building, "owner", None)
            self.notify(
                owner,
                "storage_full",
                building=self._item_name(building),
                stored=added,
                resource=resource,
                dropped=dropped,
            )
        return added

    def _spawn_resource_drop(
        self, holder: Any, resource: str, amount: int
    ) -> None:
        """Spill *amount* of *resource* to a ground drop at *holder*'s coords.

        Re-creates a ``ResourceDrop`` for the over-capacity remainder of an
        inflow so it is never destroyed (D9). Routes through the injected
        resource-drop spawner (:meth:`set_resource_drop_spawner`) rather than
        importing ``typeclasses`` at module scope, keeping ``world/systems``
        framework-free. When no spawner is wired (before composition-root wiring
        or in a lightweight test), the spill degrades to a log — the leftover is
        still reported via the ``carry_full``/``storage_full`` notification.
        """
        if amount <= 0:
            return
        spawner = self._resource_drop_spawner
        if spawner is None:
            logger.info(
                "add_resource_capped: no resource-drop spawner wired; %d %s "
                "left behind (not respawned)",
                amount, resource,
            )
            return
        try:
            spawner(holder, resource, amount)
        except Exception:  # noqa: BLE001 - a spawn failure must not break inflow
            logger.warning(
                "add_resource_capped: resource-drop spawner failed for %d %s",
                amount, resource,
            )
