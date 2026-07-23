"""
NotificationPresenter — formats and delivers player-facing notifications.

Subscribes to the ``PLAYER_NOTIFICATION`` event that domain systems emit and is
the single owner of the per-player message strings — none live inline in the
systems. Each event carries ``player``, ``kind``, and a ``data`` dict; the
presenter looks the ``kind`` up in its format table, builds the line, and
delivers it via the injected :class:`PlayerNotifier`.

Adding or restyling a player message is a one-line change to
``_FORMATTERS`` here, with no edit to the use-case systems.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from world.constants import UNIT_KIND_LABELS as _UNIT_LABELS
from world.event_bus import EventBus, PLAYER_NOTIFICATION

logger = logging.getLogger("evennia.world.presenters.notification")


def _fmt_rank_level_up(d: dict) -> str:
    msg = f"You are now Level {d['level']} ({d['rank_name']} {d['sub']})"
    # Announce planet unlocks when the new level crosses a gate.
    unlocked = d.get("planets_unlocked")
    if unlocked:
        for p in unlocked:
            msg += (
                f"\n|g→ New planet unlocked: |w{p['name']}|g ({p['type']})"
                f" — build a Launch Pad to travel there.|n"
            )
    return msg


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


def _fmt_repair_progress(d: dict) -> str:
    name = d.get("name") or d.get("btype", "building")
    return (
        f"|y[Repair] {name} at {d.get('hp', '?')}/{d.get('hp_max', '?')} HP "
        f"({d.get('pct', '?')}%)...|n"
    )


def _fmt_repair_complete(d: dict) -> str:
    name = d.get("name") or d.get("btype", "building")
    online = " It is back online." if d.get("was_offline") else ""
    return (
        f"|g[Repair] {name} fully repaired to "
        f"{d.get('hp_max', '?')}/{d.get('hp_max', '?')} HP.{online}|n"
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


def _fmt_harvest_crit(d: dict) -> str:
    return f"|g[Rich vein!] +{d['amount']} {d['resource']} bonus!|n"


def _fmt_directive_complete(d: dict) -> str:
    reward = d.get("reward") or {}
    parts = []
    xp = reward.get("xp", 0)
    if xp:
        parts.append(f"+{xp} XP")
    for res, amt in reward.items():
        if res != "xp" and amt:
            parts.append(f"+{amt} {res}")
    suffix = f" — {', '.join(parts)}" if parts else ""
    return f"|w[Directive complete]|n {d.get('description', '?')}{suffix}"


def _fmt_directive_next(d: dict) -> str:
    return f"|y[Next objective]|n {d.get('description', '?')}"


def _fmt_directives_all_complete(d: dict) -> str:
    return "|g[Directives] All objectives complete. Your base is established!|n"


# Attacks landing on the receiving player (you, your building, or your unit)
# are rendered in BRIGHT red so an incoming hit stands out from the yellow/green
# informational lines. In Evennia ANSI, |r is HILITE+RED (bright); |R is
# UNHILITE+RED (dark) — counter-intuitive, so the bright code is the lowercase.
def _fmt_attacked(d: dict) -> str:
    return (
        f"|r[Combat] You were attacked by {d['attacker_name']} with "
        f"{d['weapon_name']} for {d['damage']} damage.|n"
    )


def _fmt_attack_hit(d: dict) -> str:
    # The attacking player's own hit landed — green (your offensive success).
    return (
        f"|g[Combat] You hit {d.get('target_name', 'the target')} with "
        f"{d.get('weapon_name', 'your weapon')} for {d.get('damage', 0)} damage.|n"
    )


def _fmt_building_attacked(d: dict) -> str:
    return (
        f"|r[Combat] Your {d['building_name']} was attacked by "
        f"{d['attacker_name']} with {d['weapon_name']} for "
        f"{d['damage']} damage.|n"
    )


def _fmt_unit_attacked(d: dict) -> str:
    # One of the owner's units (currently an agent) took a hit — bright red.
    label = _UNIT_LABELS.get(d.get("unit_kind"), "unit")
    return (
        f"|r[Combat] Your {label} ({d.get('unit_name', '?')}) was attacked by "
        f"{d.get('attacker_name', 'Unknown')} with {d.get('weapon_name', 'a weapon')} "
        f"for {d.get('damage', 0)} damage.|n"
    )


def _fmt_unit_attack(d: dict) -> str:
    # One of the owner's units (turret/agent) struck a target.
    label = _UNIT_LABELS.get(d.get("unit_kind"), "unit")
    return (
        f"|y[Combat] Your {label} ({d.get('unit_name', '?')}) attacked "
        f"{d.get('target_name', 'a target')} with {d.get('weapon_name', 'a weapon')} "
        f"for {d.get('damage', 0)} damage.|n"
    )


def _fmt_shot_missed(d: dict) -> str:
    # The shooter's ranged shot whiffed — no damage dealt.
    return (
        f"|y[Combat] Your shot at {d.get('target_name', 'the target')} with "
        f"{d.get('weapon_name', 'your weapon')} missed.|n"
    )


def _fmt_shot_dodged(d: dict) -> str:
    # A player was shot at but the attack missed — bright red (incoming).
    return (
        f"|r[Combat] {d.get('attacker_name', 'Someone')} shot at you with "
        f"{d.get('weapon_name', 'a weapon')} and missed.|n"
    )


def _fmt_unit_shot_dodged(d: dict) -> str:
    # One of the owner's units (agent/building) was shot at but the shot missed —
    # bright red (incoming). The unit has no session, so the OWNER hears it.
    label = _UNIT_LABELS.get(d.get("unit_kind"), "unit")
    return (
        f"|r[Combat] {d.get('attacker_name', 'Someone')} shot at your {label} "
        f"({d.get('unit_name', '?')}) with {d.get('weapon_name', 'a weapon')} "
        f"and missed.|n"
    )


_LOCK_LOST_REASONS = {
    "out_of_range": "your target moved out of range",
    "left_area": "you left the area",
    "moved": "you moved",
    "no_weapon": "you no longer have a ranged weapon",
    "target_gone": "your target is gone",
}


def _fmt_targeting(d: dict) -> str:
    # Lock-on started; it completes after a few ticks.
    return (
        f"|y[Combat] Locking onto {d.get('target_name', 'the target')}... "
        f"(~{d.get('ticks', '?')} ticks). Hold fire until locked.|n"
    )


def _fmt_locked(d: dict) -> str:
    return (
        f"|g[Combat] Locked onto {d.get('target_name', 'the target')} — "
        f"'shoot' to fire.|n"
    )


def _fmt_lock_lost(d: dict) -> str:
    why = _LOCK_LOST_REASONS.get(d.get("reason"), "the lock broke")
    return f"|y[Combat] Lock lost — {why}.|n"


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
        "bad_direction": "Throw which way? Use n/s/e/w.",
        "out_of_range": (
            f"{item} is out of range "
            f"({d.get('distance', '?')} > {d.get('range', '?')})."
        ),
    }
    return f"|y[Throw] {messages.get(reason, f'Cannot throw {item}.')}|n"


# ------------------------------------------------------------------ #
#  Bombs — grenades (thrown, directional) + mines (armed in place). A set fuse
#  ticks down before an AoE blast. Everyone on a bomb's tile sees it arm/tick.
# ------------------------------------------------------------------ #

def _fmt_not_a_bomb(d: dict) -> str:
    return f"|y[Bomb] {d.get('item_name', 'That')} isn't a bomb.|n"


def _fmt_not_a_mine(d: dict) -> str:
    return f"|y[Bomb] {d.get('item_name', 'That')} isn't a mine — throw it instead.|n"


def _fmt_bomb_not_held(d: dict) -> str:
    return f"|y[Bomb] You aren't carrying {d.get('item_name', 'that')}.|n"


def _fmt_fuse_set(d: dict) -> str:
    item = d.get("item_name", "bomb")
    secs = d.get("seconds", 0)
    count = d.get("count", 1) or 1
    # "on all 3" only when more than one unit is armed; a single bomb reads plain.
    scope = f" on all {count}" if count > 1 else ""
    if d.get("clamped"):
        return (
            f"|y[Bomb] Fuse for {item} set to {secs}s{scope} "
            f"(clamped to {d.get('fuse_min', '?')}–{d.get('fuse_max', '?')}s).|n"
        )
    return f"|y[Bomb] Fuse for {item} set to {secs}s{scope}.|n"


def _fmt_fuse_all_set(d: dict) -> str:
    count = d.get("count", 0)  # individual bombs armed
    types = d.get("types", 0)  # distinct bomb types
    if not count:
        return "|y[Bomb] No bombs in your inventory to set.|n"
    type_note = f" across {types} type(s)" if types > 1 else ""
    return (
        f"|y[Bomb] Fuse set to {d.get('seconds', 0)}s on {count} bomb(s)"
        f"{type_note} (clamped per bomb).|n"
    )


def _fmt_need_fuse(d: dict) -> str:
    item = d.get("item_name", "bomb")
    return (
        f"|y[Bomb] Set a fuse first: 'set {item} <seconds>' "
        f"(or 'set all <seconds>').|n"
    )


def _fmt_arm_failed(d: dict) -> str:
    item = d.get("item_name", "mine")
    reason = d.get("reason")
    messages = {
        "no_position": "You have no position to arm from.",
    }
    return f"|y[Bomb] {messages.get(reason, f'Cannot arm {item}.')}|n"


def _fmt_grenade_thrown(d: dict) -> str:
    # The thrower's confirmation — the grenade is away and ticking (yellow).
    return (
        f"|y[Bomb] You throw {d.get('item_name', 'a grenade')} to "
        f"({d.get('x', '?')},{d.get('y', '?')}) — {d.get('seconds', '?')}s fuse.|n"
    )


def _fmt_mine_armed(d: dict) -> str:
    # The placer's confirmation for arming a mine.
    return (
        f"|y[Bomb] You arm {d.get('item_name', 'a mine')} here — "
        f"{d.get('seconds', '?')}s fuse. It begins to |rtick|n|y.|n"
    )


def _fmt_bomb_landed(d: dict) -> str:
    # Seen by OTHERS on the tile a grenade lands on (incoming — bright red).
    return (
        f"|r[Bomb] {d.get('item_name', 'A grenade')} lands here, "
        f"ticking ({d.get('seconds', '?')}s)!|n"
    )


def _fmt_bomb_armed(d: dict) -> str:
    # Seen by OTHERS on the tile where a mine is armed (incoming — bright red).
    return (
        f"|r[Bomb] {d.get('item_name', 'A mine')} is armed here, "
        f"ticking ({d.get('seconds', '?')}s)!|n"
    )


def _fmt_bomb_tick(d: dict) -> str:
    # Per-second countdown shown to everyone on the bomb's tile (bright red).
    return (
        f"|r[Bomb] {d.get('item_name', 'A bomb')} ticks... "
        f"{d.get('seconds', '?')}s.|n"
    )


def _fmt_bomb_exploded(d: dict) -> str:
    # Seen by everyone still on the blast tile (bright red).
    return (
        f"|r[Bomb] {d.get('item_name', 'A bomb')} EXPLODES! "
        f"{d.get('count', 0)} caught in the blast.|n"
    )


def _fmt_bomb_detonated(d: dict) -> str:
    # The placer's outcome summary (informational yellow).
    return (
        f"|y[Bomb] Your {d.get('item_name', 'bomb')} detonated at "
        f"({d.get('x', '?')},{d.get('y', '?')}) — {d.get('count', 0)} hit.|n"
    )


def _fmt_disarm_none(d: dict) -> str:
    return "|y[Disarm] There is no ticking bomb here to disarm.|n"


def _fmt_disarm_start(d: dict) -> str:
    return (
        f"|y[Disarm] You start working on the {d.get('item_name', 'bomb')}... "
        f"(~{d.get('ticks', '?')}s). Its fuse is still ticking — stay clear of "
        f"a short one.|n"
    )


def _fmt_disarm_in_progress(d: dict) -> str:
    return (
        f"|y[Disarm] The {d.get('item_name', 'bomb')} is already being "
        f"disarmed.|n"
    )


def _fmt_disarm_success(d: dict) -> str:
    return f"|g[Disarm] You safely neutralized the {d.get('item_name', 'bomb')}.|n"


def _fmt_disarm_success_tile(d: dict) -> str:
    # Seen by others on the tile when someone disarms a bomb there.
    return f"|g[Disarm] The {d.get('item_name', 'bomb')} was disarmed.|n"


def _fmt_disarm_failed(d: dict) -> str:
    # Failure detonates the bomb immediately (bright red).
    return (
        f"|r[Disarm] You botched the {d.get('item_name', 'bomb')} — it goes "
        f"off!|n"
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


def _fmt_recovery_collected(d: dict) -> str:
    """Recovered loadout collected from a Respawn building."""
    items = d.get("items") or {}
    resources = d.get("resources") or {}
    left = d.get("left_behind") or {}
    parts = []
    for key, n in items.items():
        parts.append(f"{n}x {key}")
    for r, n in resources.items():
        parts.append(f"{n} {r}")
    body = ", ".join(parts) if parts else "nothing that fit"
    msg = f"|g[Respawn] Recovered {body}.|n"
    if left:
        leftbody = ", ".join(f"{n} {r}" for r, n in left.items())
        msg += (f" |y{leftbody} stayed in the beacon — over your carry weight; "
                f"come back for it.|n")
    return msg


def _fmt_recovery_empty(d: dict) -> str:
    return "|y[Respawn] Nothing to recover here.|n"


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


def _fmt_harvester_produced(d: dict) -> str:
    # Passive output from an agent-run Extractor (mirrors the equipment
    # buildings' "produced" line so autonomous extraction isn't silent).
    return (
        f"|g[Extractor] +{d.get('amount', 0)} {d.get('resource_type', 'resource')} "
        f"produced. Use 'get' to pick up.|n"
    )


def _fmt_sold(d: dict) -> str:
    from world.utils import format_cost_summary
    name = d.get("item_name", "item")
    refund = d.get("refund") or {}
    if refund:
        parts = format_cost_summary(refund)
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


def _fmt_guard_loot(d: dict) -> str:
    # Per-guard-kill mini-drop (R8.2) — the small variable reward between
    # HQ payouts.
    return (
        f"|g[Loot] The guard dropped {d.get('amount', 0)} "
        f"{d.get('resource', 'resources')} at ({d.get('x', '?')},{d.get('y', '?')}).|n"
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
        "building_upgrading": (
            "This building is being upgraded — it can't be used until the "
            "upgrade finishes (or you 'upgrade cancel')."
        ),
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
        "repair_progress": _fmt_repair_progress,
        "repair_complete": _fmt_repair_complete,
        "agent_training_complete": _fmt_agent_training_complete,
        "agent_training_progress": _fmt_agent_training_progress,
        "harvest_drop": _fmt_harvest_drop,
        "harvest_crit": _fmt_harvest_crit,
        "directive_complete": _fmt_directive_complete,
        "directive_next": _fmt_directive_next,
        "directives_all_complete": _fmt_directives_all_complete,
        "attacked": _fmt_attacked,
        "attack_hit": _fmt_attack_hit,
        "building_attacked": _fmt_building_attacked,
        "unit_attacked": _fmt_unit_attacked,
        "unit_attack": _fmt_unit_attack,
        "shot_missed": _fmt_shot_missed,
        "shot_dodged": _fmt_shot_dodged,
        "unit_shot_dodged": _fmt_unit_shot_dodged,
        "targeting": _fmt_targeting,
        "locked": _fmt_locked,
        "lock_lost": _fmt_lock_lost,
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
        # Bomb feature kinds (grenades + mines): fuse config, deploy, tick, blast.
        "not_a_bomb": _fmt_not_a_bomb,
        "not_a_mine": _fmt_not_a_mine,
        "bomb_not_held": _fmt_bomb_not_held,
        "fuse_set": _fmt_fuse_set,
        "fuse_all_set": _fmt_fuse_all_set,
        "need_fuse": _fmt_need_fuse,
        "arm_failed": _fmt_arm_failed,
        "grenade_thrown": _fmt_grenade_thrown,
        "mine_armed": _fmt_mine_armed,
        "bomb_landed": _fmt_bomb_landed,
        "bomb_armed": _fmt_bomb_armed,
        "bomb_tick": _fmt_bomb_tick,
        "bomb_exploded": _fmt_bomb_exploded,
        "bomb_detonated": _fmt_bomb_detonated,
        "disarm_none": _fmt_disarm_none,
        "disarm_start": _fmt_disarm_start,
        "disarm_in_progress": _fmt_disarm_in_progress,
        "disarm_success": _fmt_disarm_success,
        "disarm_success_tile": _fmt_disarm_success_tile,
        "disarm_failed": _fmt_disarm_failed,
        "out_of_ammo": _fmt_out_of_ammo,
        "reloaded": _fmt_reloaded,
        "reload_failed": _fmt_reload_failed,
        "carry_full": _fmt_carry_full,
        "storage_full": _fmt_storage_full,
        "deposited": _fmt_deposited,
        "withdrew": _fmt_withdrew,
        "recovery_collected": _fmt_recovery_collected,
        "recovery_empty": _fmt_recovery_empty,
        "deposit_failed": _fmt_deposit_failed,
        "withdraw_failed": _fmt_withdraw_failed,
        "unequip_failed": _fmt_unequip_failed,
        "crafted": _fmt_crafted,
        "craft_failed": _fmt_craft_failed,
        "sold": _fmt_sold,
        "junked": _fmt_junked,
        "sell_failed": _fmt_sell_failed,
        "produced": _fmt_produced,
        "harvester_produced": _fmt_harvester_produced,
        "tile_full": _fmt_tile_full,
        "combat_started": _fmt_combat_started,
        "npc_killed": _fmt_npc_killed,
        "guard_loot": _fmt_guard_loot,
        "base_eliminated": _fmt_base_eliminated,
        "base_deactivated": _fmt_base_deactivated,
        "base_reactivated": _fmt_base_reactivated,
    }

    #: Notification kinds that change the RECIPIENT's own HP or level. After
    #: delivering one, we push a fresh status prompt so the player's webclient
    #: footer (and prompt-aware telnet clients) reflect the new HP/level live —
    #: without the player having to type a command. "attacked" = took a hit,
    #: "healed" = restored, "rank_level_up" = levelled. (Kinds about a player's
    #: BUILDINGS/UNITS being hit are excluded: the player's own HP is unchanged.)
    _STATUS_AFFECTING_KINDS = frozenset({"attacked", "healed", "rank_level_up"})

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
        if kind in self._STATUS_AFFECTING_KINDS:
            self._push_status(player)

    @staticmethod
    def _push_status(player: Any) -> None:
        """Push a live status prompt (HP/level/position) to *player*.

        Best-effort and import-local to avoid a load-time cycle
        (status_prompt → world.utils). A failure here must never disrupt the
        notification it follows.
        """
        try:
            from world.status_prompt import push_status
            push_status(player)
        except Exception:  # noqa: BLE001 - live status is best-effort
            logger.debug("live status push failed", exc_info=True)
