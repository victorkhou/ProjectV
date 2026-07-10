"""
NotificationPresenter — formats and delivers player-facing notifications.

Subscribes to the ``PLAYER_NOTIFICATION`` event that domain systems emit and is
the single owner of the per-player message strings that used to live inline in
the systems. Each event carries ``player``, ``kind``, and a ``data`` dict; the
presenter looks the ``kind`` up in its format table, builds the line, and
delivers it via the injected :class:`PlayerNotifier`.

Adding or restyling a player message is now a one-line change to
``_FORMATTERS`` here, with no edit to the use-case systems.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from world.event_bus import EventBus, PLAYER_NOTIFICATION

logger = logging.getLogger("evennia.world.presenters.notification")


def _fmt_rank_level_up(d: dict) -> str:
    return f"You are now Level {d['level']} ({d['rank_name']} {d['sub']})"


def _fmt_building_progress(d: dict) -> str:
    if d.get("target_level"):
        return (
            f"|y[Building] Upgrading {d['btype']} to L{d['target_level']}... "
            f"{d['progress']}/{d['total']}s ({d['remaining']}s remaining)|n"
        )
    return (
        f"|y[Building] Constructing {d['btype']}... "
        f"{d['progress']}/{d['total']}s ({d['remaining']}s remaining)|n"
    )


def _fmt_building_complete(d: dict) -> str:
    if d.get("target_level"):
        return f"|g[Complete] {d['building_type']} upgraded to level {d['target_level']}!|n"
    return (
        f"|g[Complete] {d['building_type']} construction finished! "
        f"The building is now operational.|n"
    )


def _fmt_agent_training_complete(d: dict) -> str:
    aid = d["agent_id"]
    return (
        f"|g[Complete] Agent #{aid} training finished! "
        f"Use 'agents' to see your roster and 'assign {aid}' "
        f"to put them to work.|n"
    )


def _fmt_agent_training_progress(d: dict) -> str:
    return f"|y[Training] Agent #{d['agent_id']}... {d['remaining']}s remaining|n"


def _fmt_harvest_drop(d: dict) -> str:
    return (
        f"|y[Harvest] +{d['amount']} {d['resource_type']} dropped. "
        f"Use 'get' to pick up.|n"
    )


def _fmt_attacked(d: dict) -> str:
    return (
        f"You were attacked by {d['attacker_name']} with {d['weapon_name']} "
        f"for {d['damage']} damage."
    )


def _fmt_building_attacked(d: dict) -> str:
    return (
        f"Your {d['building_name']} was attacked by {d['attacker_name']} "
        f"with {d['weapon_name']} for {d['damage']} damage."
    )


def _fmt_ability_active(d: dict) -> str:
    return f"|g[Ability] '{d['key']}' is now active for Agent #{d['agent_id']}.|n"


def _fmt_ability_relocked(d: dict) -> str:
    return (
        f"|r[Ability] '{d['key']}' has re-locked for Agent #{d['agent_id']} — "
        f"its level dropped below {d['required']}.|n"
    )


def _fmt_ability_available(d: dict) -> str:
    aid = d["agent_id"]
    return (
        f"|y[Ability] '{d['key']}' is now available for Agent #{aid}. "
        f"Enable it with 'agent ability {aid} {d['key']} on'.|n"
    )


# --------------------------------------------------------------------------- #
#  Equipment feature notification kinds
# --------------------------------------------------------------------------- #


def _fmt_equipped(d: dict) -> str:
    return f"|g[Equip] Equipped {d.get('item_name', 'item')} in {d.get('slot', '?')}.|n"


def _fmt_unequipped(d: dict) -> str:
    return (
        f"|y[Equip] Unequipped {d.get('item_name', 'item')} "
        f"from {d.get('slot', '?')}.|n"
    )


def _fmt_equip_denied(d: dict) -> str:
    return (
        f"|r[Equip] {d.get('item_name', 'item')} requires rank "
        f"{d.get('required_rank', '?')} (you are {d.get('current_rank', '?')}).|n"
    )


def _fmt_use_failed(d: dict) -> str:
    item = d.get("item_name", "item")
    reason = d.get("reason")
    messages = {
        "not_held": f"You aren't carrying {item}.",
        "not_consumable": f"{item} can't be used.",
        "unavailable": f"Can't use {item} right now.",
        "no_effect": f"{item} has no effect.",
    }
    return f"|y[Use] {messages.get(reason, f'Cannot use {item}.')}|n"


def _fmt_healed(d: dict) -> str:
    return (
        f"|g[Use] Healed {d.get('amount', 0)} HP "
        f"({d.get('hp', 0)}/{d.get('hp_max', 0)}).|n"
    )


def _fmt_buff_applied(d: dict) -> str:
    return (
        f"|g[Use] +{d.get('amount', 0)} {d.get('stat', 'stat')} "
        f"for {d.get('duration_ticks', 0)}s.|n"
    )


def _fmt_throw_failed(d: dict) -> str:
    item = d.get("item_name", "item")
    reason = d.get("reason")
    messages = {
        "not_held": f"You aren't carrying {item}.",
        "not_throwable": f"{item} can't be thrown.",
        "no_position": "You have no position to throw from.",
        "out_of_range": (
            f"{item} is out of range "
            f"({d.get('distance', '?')} > {d.get('range', '?')})."
        ),
    }
    return f"|y[Throw] {messages.get(reason, f'Cannot throw {item}.')}|n"


def _fmt_bombed(d: dict) -> str:
    return (
        f"|y[Throw] Hit {d.get('count', 0)} target(s) "
        f"at ({d.get('x', '?')},{d.get('y', '?')}).|n"
    )


def _fmt_out_of_ammo(d: dict) -> str:
    return (
        f"|r[Combat] {d.get('weapon_name', 'weapon')} is empty — "
        f"reload to fire.|n"
    )


def _fmt_reloaded(d: dict) -> str:
    return (
        f"|g[Reload] {d.get('weapon_name', 'weapon')}: "
        f"{d.get('loaded', 0)}/{d.get('magazine_size', 0)} "
        f"({d.get('remaining', 0)} {d.get('ammo_name', 'ammo')} left).|n"
    )


def _fmt_reload_failed(d: dict) -> str:
    reason = d.get("reason")
    messages = {
        "no_ammo": "No ammo left to reload.",
        "already_loaded": "Magazine is already full.",
        "no_ammo_weapon": "No ammo-using weapon equipped.",
        "no_magazine": (
            "Your weapon has no magazine to reload — it fires straight from "
            "your resource stockpile. Just attack."
        ),
    }
    return f"|y[Reload] {messages.get(reason, 'Cannot reload.')}|n"


def _fmt_carry_full(d: dict) -> str:
    return (
        f"|y[Supply] Carried {d.get('carried', 0)} {d.get('item_name', 'item')}; "
        f"{d.get('dropped', 0)} left behind (over carry weight).|n"
    )


def _fmt_storage_full(d: dict) -> str:
    return (
        f"|y[Storage] {d.get('building', 'Storage')} full; stored "
        f"{d.get('stored', 0)} {d.get('resource', 'resource')}, "
        f"{d.get('dropped', 0)} dropped.|n"
    )


def _fmt_deposited(d: dict) -> str:
    return (
        f"|g[Storage] Deposited {d.get('amount', 0)} {d.get('resource', 'resource')} "
        f"→ {d.get('building', 'Storage')} "
        f"({d.get('stored', 0)}/{d.get('capacity', 0)}).|n"
    )


def _fmt_withdrew(d: dict) -> str:
    return (
        f"|g[Storage] Withdrew {d.get('amount', 0)} {d.get('resource', 'resource')} "
        f"(carrying {d.get('carried', 0)}/{d.get('limit', 0)}).|n"
    )


def _fmt_deposit_failed(d: dict) -> str:
    res = d.get("resource", "resource")
    reason = d.get("reason")
    messages = {
        "nothing_held": f"You have no {res} to deposit.",
        "building_full": f"Storage is full — no room for {res}.",
    }
    return f"|y[Storage] {messages.get(reason, f'Cannot deposit {res}.')}|n"


def _fmt_withdraw_failed(d: dict) -> str:
    res = d.get("resource", "resource")
    reason = d.get("reason")
    messages = {
        "nothing_stored": f"No {res} in storage.",
        "carry_full": f"You can't carry any more {res} (over carry weight).",
    }
    return f"|y[Storage] {messages.get(reason, f'Cannot withdraw {res}.')}|n"


def _fmt_unequip_failed(d: dict) -> str:
    slot = d.get("slot", "slot")
    reason = d.get("reason")
    messages = {
        "empty": f"Nothing equipped in your {slot} slot.",
        "bad_slot": f"'{slot}' is not an equipment slot.",
    }
    return f"|y[Equip] {messages.get(reason, f'Cannot unequip {slot}.')}|n"


def _fmt_crafted(d: dict) -> str:
    return f"|g[Craft] Crafted {d.get('item_name', 'item')}.|n"


def _fmt_produced(d: dict) -> str:
    # Passive output from an agent-run equipment building.
    labels = {"AR": "Armory", "LB": "Lab", "MB": "Medbay"}
    where = labels.get(d.get("building_type"), "building")
    return f"|g[{where}] Produced {d.get('item_name', 'item')}.|n"


def _fmt_sold(d: dict) -> str:
    name = d.get("item_name", "item")
    refund = d.get("refund") or {}
    if refund:
        parts = ", ".join(f"{amt} {res}" for res, amt in refund.items())
        return f"|g[Sell] Sold {name} for {parts}.|n"
    return f"|g[Sell] Sold {name}.|n"


def _fmt_junked(d: dict) -> str:
    return f"|y[Junk] Destroyed {d.get('item_name', 'item')}.|n"


def _fmt_sell_failed(d: dict) -> str:
    name = d.get("item_name", "that")
    reasons = {
        "no_item": "You aren't carrying that.",
        "equipped": f"{name} is equipped — unequip it first.",
        "not_gear": "You can only sell or junk carried gear, not supplies.",
        "unknown_item": f"{name} can't be sold or junked.",
    }
    return f"|r{reasons.get(d.get('reason'), f'You cannot do that to {name}.')}|n"


def _fmt_tile_full(d: dict) -> str:
    # The tile is at its item-capacity cap, so a new drop was refused.
    return "|yThe ground here is full — clear some items to gather more.|n"


def _fmt_combat_started(d: dict) -> str:
    # Fired once when a player enters the combat state (not on every hit).
    dur = d.get("duration")
    tail = f" for {dur}s" if dur else ""
    return (
        f"|r[Combat] You are now in combat{tail}. It resets each time you deal "
        f"or take damage, and blocks passing through your own Walls. "
        f"'score' shows the time remaining.|n"
    )


def _fmt_npc_killed(d: dict) -> str:
    # Fired when a player kills an enemy NPC (an NPC-base guard), which dies
    # permanently. Reports the kill and the XP awarded.
    return (
        f"|g[Combat] Killed {d.get('name', 'enemy')}. "
        f"+{d.get('xp', 0)} XP.|n"
    )


def _fmt_base_eliminated(d: dict) -> str:
    # Fired when a player destroys an NPC base's HQ (the whole base is wiped).
    tier = d.get("tier", "Outpost")
    loot = d.get("loot")
    loot_tail = f" Loot dropped at ({d.get('x', '?')},{d.get('y', '?')})." if loot else ""
    return (
        f"|g[Combat] {tier} eliminated! +{d.get('xp', 0)} XP.{loot_tail}|n"
    )


def _fmt_base_deactivated(d: dict) -> str:
    # Fired when a player's HQ is destroyed — the base goes inert until rebuilt.
    return (
        "|r[Alert] Your HQ was destroyed! Base deactivated — "
        "rebuild an HQ to restore operations.|n"
    )


def _fmt_base_reactivated(d: dict) -> str:
    # Fired when a player completes a new HQ, restoring an inert base.
    return "|g[Alert] HQ rebuilt! Base systems are back online.|n"


def _fmt_craft_failed(d: dict) -> str:
    item = d.get("item_name", "item")
    reason = d.get("reason")
    # Insufficient resources gets the shared have/need breakdown appended.
    if reason == "insufficient_resources":
        breakdown = d.get("breakdown")
        head = f"|r[Craft] Can't afford {item}.|n"
        return f"{head}\n{breakdown}" if breakdown else head
    messages = {
        "unknown_item": f"No such item '{item}'.",
        "not_craftable": f"{item} can't be crafted.",
        "wrong_building": (
            f"You can't craft {item} here. Stand in the building that "
            f"makes it (Armory, Lab, or Medbay)."
        ),
        "not_owner": "You can only craft in your own building.",
        "building_offline": "This building is offline — repair it first.",
        "bag_full": (
            f"Your supply bag is full of {item} — use or drop some first."
        ),
        "craft_error": (
            f"Something went wrong making {item}; your resources were refunded."
        ),
    }
    return f"|r[Craft] {messages.get(reason, f'Cannot craft {item}.')}|n"


class NotificationPresenter:
    """Formats ``PLAYER_NOTIFICATION`` events and delivers them to players."""

    #: kind -> (data dict) -> formatted string. The single source of truth for
    #: every per-player notification line.
    _FORMATTERS: dict[str, Callable[[dict], str]] = {
        "rank_level_up": _fmt_rank_level_up,
        "building_progress": _fmt_building_progress,
        "building_complete": _fmt_building_complete,
        "agent_training_complete": _fmt_agent_training_complete,
        "agent_training_progress": _fmt_agent_training_progress,
        "harvest_drop": _fmt_harvest_drop,
        "attacked": _fmt_attacked,
        "building_attacked": _fmt_building_attacked,
        "ability_active": _fmt_ability_active,
        "ability_relocked": _fmt_ability_relocked,
        "ability_available": _fmt_ability_available,
        # Equipment feature kinds.
        "equipped": _fmt_equipped,
        "unequipped": _fmt_unequipped,
        "equip_denied": _fmt_equip_denied,
        "use_failed": _fmt_use_failed,
        "healed": _fmt_healed,
        "buff_applied": _fmt_buff_applied,
        "throw_failed": _fmt_throw_failed,
        "bombed": _fmt_bombed,
        "out_of_ammo": _fmt_out_of_ammo,
        "reloaded": _fmt_reloaded,
        "reload_failed": _fmt_reload_failed,
        "carry_full": _fmt_carry_full,
        "storage_full": _fmt_storage_full,
        "deposited": _fmt_deposited,
        "withdrew": _fmt_withdrew,
        "deposit_failed": _fmt_deposit_failed,
        "withdraw_failed": _fmt_withdraw_failed,
        "unequip_failed": _fmt_unequip_failed,
        "crafted": _fmt_crafted,
        "craft_failed": _fmt_craft_failed,
        "sold": _fmt_sold,
        "junked": _fmt_junked,
        "sell_failed": _fmt_sell_failed,
        "produced": _fmt_produced,
        "tile_full": _fmt_tile_full,
        "combat_started": _fmt_combat_started,
        "npc_killed": _fmt_npc_killed,
        "base_eliminated": _fmt_base_eliminated,
        "base_deactivated": _fmt_base_deactivated,
        "base_reactivated": _fmt_base_reactivated,
    }

    def __init__(self, event_bus: EventBus, player_notifier: Any = None) -> None:
        self.event_bus = event_bus
        from world.adapters.evennia_player_notifier import EvenniaPlayerNotifier

        self._notifier = player_notifier or EvenniaPlayerNotifier()
        event_bus.subscribe(PLAYER_NOTIFICATION, self.on_notification)

    def on_notification(
        self,
        event_name: str = "",
        player: Any = None,
        kind: str = "",
        data: dict | None = None,
        **kwargs,
    ) -> None:
        """Format the notification for its *kind* and deliver it to *player*."""
        formatter = self._FORMATTERS.get(kind)
        if formatter is None:
            logger.warning("No formatter for notification kind %r", kind)
            return
        try:
            message = formatter(data or {})
        except Exception:
            logger.exception("Failed to format notification kind %r: %r", kind, data)
            return
        self._notifier.notify(player, message)
