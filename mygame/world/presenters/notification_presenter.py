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
    level = d["level"]
    old_level = d.get("old_level")
    # A level DROP (death XP loss) reuses this kind — keep it quiet and factual,
    # never a celebratory banner.
    if old_level is not None and old_level > level:
        return f"|y[Level] You slipped to Level {level} ({d['rank_name']}).|n"

    lines = [
        "|G============================|n",
        f"|G  ⚡ |wLEVEL UP!|G  You are now Level |w{level}|n",
        f"|G     {d['rank_name']} (tier {d['sub']})|n",
        "|G============================|n",
    ]
    # The concrete payoff: what this level just made buildable.
    for name in d.get("buildings_unlocked") or []:
        lines.append(f"|g  ● You can now build: |w{name}|g — |wbuild|g to place it.|n")
    # Planet gates are a bigger deal — call them out distinctly.
    for p in d.get("planets_unlocked") or []:
        lines.append(
            f"|g  ★ New planet unlocked: |w{p['name']}|g ({p['type']})"
            f" — build a Launch Pad to travel there.|n"
        )
    return "\n".join(lines)


def _fmt_xp_gain(d: dict) -> str:
    """A "+N XP" line for an otherwise-silent award.

    The award ``reason`` is an INTERNAL identifier (``build_complete``,
    ``agent_trained``, …), so it is rendered through
    :data:`world.constants.XP_REASON_LABELS`; an unmapped reason degrades to a
    bare "+N XP" rather than leaking the identifier to the player.
    """
    from world.constants import XP_REASON_LABELS

    amount = d.get("amount", 0)
    label = XP_REASON_LABELS.get(d.get("reason") or "")
    tail = f" ({label})" if label else ""
    return f"|C+{amount} XP{tail}|n"


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
    """The next objective, annotated with any gate the player has not met.

    A directive naming a building the player cannot yet raise (too low a level,
    a missing deed, resources short) would otherwise read as a dead end — the
    chain is strictly sequential, so it just sits there. The annotation names
    what is missing; a reachable objective gets no suffix.
    """
    note = ""
    abbr = d.get("requires_building")
    if abbr:
        from world.build_requirements import requirement_note
        note = requirement_note(d.get("_player"), abbr)
    return f"|y[Next objective]|n {d.get('description', '?')}{note}"


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


def _slot_label(slot) -> str:
    from world.constants import EQUIPMENT_SLOT_LABELS
    return EQUIPMENT_SLOT_LABELS.get(slot, slot or "?")


def _fmt_equipped(d: dict) -> str:
    return (
        f"|g[Equip] Equipped {d.get('item_name', 'item')} "
        f"({_slot_label(d.get('slot'))}).|n"
    )


def _fmt_unequipped(d: dict) -> str:
    return (
        f"|y[Equip] Unequipped {d.get('item_name', 'item')} "
        f"({_slot_label(d.get('slot'))}).|n"
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
    # Rolled gear shows its value: the stamped quality score, plus the
    # rarity when the crafting building's level draw assigned one — the
    # same `[Rare · 73%]` tag GameItem.get_quality_tag renders. Unrolled
    # crafts (supplies, fixed defs) pass no iqs and keep the plain line
    # (R2.5: the readout only appears where it is meaningful).
    name = d.get("item_name", "item")
    iqs = d.get("iqs")
    if iqs is None:
        return f"|g[Craft] Crafted {name}.|n"
    score = min(int(round(float(iqs))), 999)
    rarity = d.get("rarity")
    tag = (f"[{str(rarity).capitalize()} · {score}%]" if rarity
           else f"[{score}%]")
    return f"|g[Craft] Crafted {name} {tag}.|n"


def _fmt_produced(d: dict) -> str:
    # Passive output from an agent-run equipment building.
    labels = {"AR": "Armory", "LB": "Lab", "MB": "Medbay",
              "MP": "Munitions Plant"}
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


def _fmt_pvp_gear_dropped(d: dict) -> str:
    # Fired to the KILLER when a slain player's gear drops on the tile (the PvP
    # underdog bounty). Names the victim, the gear, and where to grab it — the
    # planet is included (unlike guard_loot/base_eliminated) so a cross-planet
    # turret/agent kill isn't ambiguous.
    planet = d.get("planet")
    where = f"({d.get('x', '?')},{d.get('y', '?')})"
    if planet:
        where += f" on {planet}"
    return (
        f"|g[Loot] {d.get('victim_name', 'Your foe')} dropped "
        f"{d.get('items', 'gear')} at {where}.|n"
    )


def _fmt_branch_dormancy_warning(d: dict) -> str:
    # Fired by the Branch switch gate BEFORE any resource is charged: the player
    # is about to commit to a Branch while holding research recorded in others,
    # and that research goes inert under the new commitment. Reports the count
    # and the keys so the cost of the switch is visible before it is paid.
    incoming = d.get("incoming_doctrine") or d.get("incoming_branch") or "this doctrine"
    count = int(d.get("dormant_count") or 0)
    noun = "technology" if count == 1 else "technologies"
    lines = [
        f"|y[Branch] Committing to {incoming} leaves {count} recorded "
        f"{noun} dormant:|n"
    ]
    for branch, keys in (d.get("dormant_technologies") or {}).items():
        listed = ", ".join(str(key) for key in keys or ())
        lines.append(f"|y  {branch}: {listed}|n")
    lines.append(
        "|yThe record is kept — returning to a Branch needs a reduced-cost "
        "Reinstatement per technology.|n"
    )
    return "\n".join(lines)


def _fmt_branch_estate_progress(d: dict) -> str:
    # Fired after a successful demolish of a Branch_Building: how much of that
    # Branch's estate is still standing on this planet. Zero is the interesting
    # number — it is the moment a lab of another Branch becomes buildable here.
    from world.constants import BRANCH_DOCTRINE

    branch = d.get("branch") or ""
    doctrine = BRANCH_DOCTRINE.get(branch) or branch or "that doctrine"
    name = d.get("name") or d.get("btype") or "The building"
    remaining = int(d.get("remaining") or 0)
    head = f"|w[Branch] Demolished {name}.|n"
    if remaining <= 0:
        return (
            f"{head} |gYour {doctrine} estate on this planet is empty — "
            f"you can build another Branch Lab here now.|n"
        )
    noun = "building" if remaining == 1 else "buildings"
    return (
        f"{head} |y{remaining} {doctrine} {noun} left on this planet before "
        f"you can switch Branches.|n"
    )


def _fmt_technology_view(d: dict) -> str:
    # The whole technology view (R13.1, R13.2): the doctrine committed on this
    # planet and its signature vector, that Branch's researched and available
    # technologies, and the research recorded in the Branches left dormant, each
    # priced by the Reinstatement fraction. The system publishes only figures and
    # keys — every word below is composed here.
    from world.constants import BRANCH_DOCTRINE
    from world.utils import format_section

    branch = d.get("branch")
    doctrine = d.get("doctrine") or BRANCH_DOCTRINE.get(branch) or branch
    lines = ["|wTechnologies:|n", ""]

    if branch:
        vector = str(d.get("operation_kind") or "").replace("_", " ").strip()
        head = f"|wDoctrine:|n |c{doctrine}|n (|c{branch}|n)"
        if vector:
            head += f" — signature vector: |c{vector}|n"
        lines.append(head)
    else:
        doctrines = ", ".join(BRANCH_DOCTRINE.values())
        lines.append(
            f"|xNo Branch Lab on this planet, so no doctrine is committed here "
            f"— build one to commit: {doctrines}.|n"
        )
    lines.append("")

    researched = [
        _tech_label(entry) for entry in (d.get("researched") or ())
    ]
    lines.extend(format_section("Researched", researched, empty="none"))
    available = [
        _tech_label(entry) for entry in (d.get("available") or ())
    ]
    lines.extend(format_section("Available", available, empty="none"))

    fraction = _reinstatement_percent(d.get("reinstatement_fraction"))
    pending = [str(key) for key in (d.get("reinstatement_pending") or ())]
    if pending:
        lines.append("")
        lines.append(
            f"|yAwaiting reinstatement ({fraction} of the original cost and "
            f"time):|n {', '.join(pending)}"
        )

    dormant = d.get("dormant") or ()
    if dormant:
        lines.append("")
        lines.append("|yDormant research — inert until you commit again:|n")
        for entry in dormant:
            name = (
                entry.get("doctrine")
                or BRANCH_DOCTRINE.get(entry.get("branch"))
                or entry.get("branch")
                or "?"
            )
            count = int(entry.get("count") or 0)
            noun = "technology" if count == 1 else "technologies"
            lines.append(f"|y  {name}: {count} {noun} on record|n")
        lines.append(
            f"|yThe record is kept — each one comes back through a "
            f"reinstatement job costing {fraction} of the original.|n"
        )
    return "\n".join(lines)


def _tech_label(entry: Any) -> str:
    """Render one technology row of the technology view: ``Name (key)``.

    The payload carries the key and, when the definition is loaded, the display
    name. A key whose definition is missing still shows — a record entry the
    player has is more useful than a silent gap.
    """
    if not isinstance(entry, dict):
        return str(entry)
    key = entry.get("key")
    name = entry.get("name")
    return f"{name} ({key})" if name else str(key)


def _reinstatement_percent(fraction: Any) -> str:
    """Render a Reinstatement cost fraction as a percentage, e.g. ``"50%"``.

    An unreadable fraction renders as ``"a reduced share"`` rather than a broken
    number, so the sentence still reads.
    """
    try:
        return f"{float(fraction) * 100:g}%"
    except (TypeError, ValueError):
        return "a reduced share"


# ------------------------------------------------------------------ #
#  Vector_Operation lifecycle (tech-tree-branch-foundation, design §4.4)
# ------------------------------------------------------------------ #
#
# The nine kinds a Signature_Vector's lifecycle reaches a player through. The
# driver publishes a kind plus structured values and composes nothing (R13.5);
# every sentence below lives here, and R13.6 is why all six lifecycle states are
# covered — a transition with no formatter would be dropped silently, so
# ``world.systems.operation_contract.VECTOR_NOTIFICATION_KINDS`` is asserted
# against this table by the presenter's own tests.
#
# Colour follows the convention the combat kinds set: bright red for something
# landing on YOU (vector_incoming, vector_hit), green for your own success,
# yellow for a setback of yours, grey for a quiet bookkeeping note.

def _vector_label(kind: Any) -> str:
    """Render an Operation_Kind identifier as words: ``strategic_strike`` -> words.

    The payload carries the internal key, never a display name — an unmapped or
    absent kind degrades to "operation" so the sentence still reads.
    """
    label = str(kind or "").replace("_", " ").strip()
    return label or "operation"


def _vector_where(d: dict) -> str:
    """Render the affected coordinate as ``" at (x, y)"``, or ``""``.

    An operation attached to an entity rather than a tile carries no coordinate,
    which reads as a bare sentence rather than as ``at (None, None)``.
    """
    x, y = d.get("x"), d.get("y")
    if x is None or y is None:
        return ""
    return f" at |c({x}, {y})|n"


#: ``reason`` key -> the clause explaining a suspension. The system publishes the
#: key (``operation_contract.SUSPEND_*``); the wording is this table's.
_VECTOR_SUSPEND_REASONS = {
    "carrier_unavailable": "its agent is down or in reserve",
    "commitment_lapsed": "you no longer hold the doctrine it needs on this planet",
}

#: ``reason`` key -> the clause explaining a cancellation
#: (``operation_contract.CANCEL_*``).
_VECTOR_CANCEL_REASONS = {
    "carrier_killed": "its agent was killed",
    "origin_lost": "the building it launched from is out of action",
    "base_eliminated": "the base it launched from was eliminated",
}


def _fmt_vector_incoming(d: dict) -> str:
    # R8.7: the warning a hostile operation's targets get when it enters Pending.
    # The tick count is the Response_Window (R8.8) — the whole point of the line
    # is that it is actionable, so the count leads the second sentence.
    attacker = d.get("attacker_name") or "Someone"
    ticks = d.get("ticks")
    window = (
        f" You have |w{ticks}|n|r tick{'' if ticks == 1 else 's'} to respond."
        if ticks is not None else ""
    )
    return (
        f"|r[Incoming] {attacker} has launched a "
        f"|w{_vector_label(d.get('kind'))}|n|r{_vector_where(d)}.{window}|n"
    )


def _fmt_vector_resolved(d: dict) -> str:
    # R8.12, the originating player's reading: your operation took effect.
    return (
        f"|g[Vector] Your {_vector_label(d.get('kind'))} took effect"
        f"{_vector_where(d)}.|n"
    )


def _fmt_vector_hit(d: dict) -> str:
    # R8.12, the recipient's reading of the same event: it landed on you. Bright
    # red, like every other kind that reports damage arriving.
    attacker = d.get("attacker_name") or "Someone"
    return (
        f"|r[Vector] {attacker}'s {_vector_label(d.get('kind'))} struck"
        f"{_vector_where(d)}.|n"
    )


def _fmt_vector_suspended(d: dict) -> str:
    # R8.14, R8.18: the clock stopped. Name the cause, because both causes are
    # things the owner can act on — recover the agent, or rebuild the lab.
    reason = _VECTOR_SUSPEND_REASONS.get(d.get("reason") or "")
    because = f" — {reason}" if reason else ""
    return (
        f"|y[Vector] Your {_vector_label(d.get('kind'))}{_vector_where(d)} is "
        f"on hold{because}. It keeps the ticks it had left.|n"
    )


def _fmt_vector_resumed(d: dict) -> str:
    # R8.15: suspension delays rather than restarts, so the ticks it resumes with
    # are the ticks it held — quoting them is what makes that visible.
    ticks = d.get("ticks_remaining")
    left = (
        f" |w{ticks}|n|g tick{'' if ticks == 1 else 's'} left."
        if ticks is not None else ""
    )
    return (
        f"|g[Vector] Your {_vector_label(d.get('kind'))} is running again.{left}|n"
    )


def _fmt_vector_expired(d: dict) -> str:
    # R8.13: the bounded lifetime ran out before the effect, and anything the
    # operation had suspended is back to how it was.
    return (
        f"|y[Vector] The {_vector_label(d.get('kind'))}{_vector_where(d)} ran out "
        f"before it took effect. Anything it was holding is back to normal.|n"
    )


def _fmt_vector_cancelled(d: dict) -> str:
    # R8.16, R8.17, R11.4: a lost collaborator ended it. The reason is the useful
    # part — it says what to fix before trying again.
    reason = _VECTOR_CANCEL_REASONS.get(d.get("reason") or "")
    because = f" — {reason}" if reason else ""
    return (
        f"|y[Vector] Your {_vector_label(d.get('kind'))} was called off{because}.|n"
    )


def _fmt_vector_discarded(d: dict) -> str:
    # R14.4: a restart found the operation referring to something that no longer
    # exists. Quiet and factual: there is nothing for the player to do about it.
    return (
        f"|x[Vector] A {_vector_label(d.get('kind'))} of yours could not be "
        f"restored after a restart and has been dropped.|n"
    )


def _fmt_vector_consent_required(d: dict) -> str:
    # R11.8: the refusal a support operation gets when the ally it would help has
    # not consented. Travels back through the validation chain as a refusal key
    # rather than as a lifecycle notification, and renders from the same table.
    ally = d.get("ally_name") or "That ally"
    return (
        f"|y[Vector] {ally} has not agreed to receive support, so your "
        f"{_vector_label(d.get('kind'))} cannot go ahead. Ask them to allow it "
        f"first.|n"
    )


# ------------------------------------------------------------------ #
#  Branch construction refusals (R3.4, R3.5, R4.1, R4.2, R6.3)
# ------------------------------------------------------------------ #
#
# These four are REFUSAL KEYS, not notification kinds: they travel back up
# BuildingSystem's validation chain as a ``BranchRefusal`` (a message key
# carrying structured data, R13.5) and reach the player through the build
# command's own reply rather than through the event bus. The gates compose no
# prose, so the words live here, and :func:`render_construction_refusal` is
# the entry point the renderer seam (``BuildingSystem.set_refusal_renderer``)
# calls. An unknown key answers ``None``, so the caller keeps the key itself
# as its fallback — the same degrade-to-a-key direction an unwired renderer
# takes.


def _doctrine_label(doctrine: Any, branch: Any) -> str:
    """The player-facing Branch name: the doctrine, else the key, else filler."""
    return str(doctrine or branch or "that Branch")


def _fmt_refusal_branch_lab_required(d: dict) -> str:
    # R3.4: no commitment on this planet at all — name the lab that creates one.
    building = d.get("building_name") or d.get("building") or "That building"
    doctrine = _doctrine_label(d.get("required_doctrine"), d.get("required_branch"))
    lab = d.get("required_lab_name") or d.get("required_lab")
    lab_part = (
        f" Build the |w{lab}|n|y here to commit to it." if lab
        else " Build that Branch's lab here to commit to it."
    )
    return (
        f"|y{building} belongs to the |w{doctrine}|n|y Branch, and you hold no "
        f"Branch commitment on this planet.{lab_part}|n"
    )


def _fmt_refusal_branch_mismatch(d: dict) -> str:
    # R3.5: committed here, but to a different Branch — report both Branches.
    building = d.get("building_name") or d.get("building") or "That building"
    required = _doctrine_label(d.get("required_doctrine"), d.get("required_branch"))
    current = _doctrine_label(d.get("current_doctrine"), d.get("current_branch"))
    return (
        f"|y{building} belongs to the |w{required}|n|y Branch, but your "
        f"commitment on this planet is |w{current}|n|y. One Branch per planet — "
        f"switching means emptying your {current} estate first.|n"
    )


def _fmt_refusal_branch_switch_blocked(d: dict) -> str:
    # R4.1 (the count) and R4.2 (each building's abbreviation and coordinates):
    # everything standing between the player and the switch, one per line.
    incoming = _doctrine_label(d.get("incoming_doctrine"), d.get("incoming_branch"))
    count = int(d.get("count") or 0)
    noun = "building" if count == 1 else "buildings"
    verb = "stands" if count == 1 else "stand"
    lines = [
        f"|yYou can't commit to {incoming} here yet — {count} {noun} of "
        f"another Branch still {verb} on this planet:|n"
    ]
    for entry in d.get("blocking") or ():
        if not isinstance(entry, dict):
            continue
        name = entry.get("building_name") or entry.get("building") or "?"
        abbr = entry.get("building")
        label = f"{name} ({abbr})" if abbr and abbr != name else str(name)
        x, y = entry.get("x"), entry.get("y")
        where = f" at ({x}, {y})" if x is not None and y is not None else ""
        branch = _doctrine_label(None, entry.get("branch"))
        lines.append(f"|y  {label}{where} — {branch}|n")
    lines.append("|yDemolish them to switch Branches.|n")
    dormant = int(d.get("dormant_count") or 0)
    if dormant:
        tech_noun = "technology" if dormant == 1 else "technologies"
        lines.append(
            f"|ySwitching also leaves {dormant} recorded {tech_noun} dormant — "
            f"the record is kept, and each comes back through a reduced-cost "
            f"reinstatement job.|n"
        )
    return "\n".join(lines)


def _fmt_refusal_branch_unlock_required(d: dict) -> str:
    # R6.3: name the required technology and the Branch that hosts it, and say
    # WHICH half of "researched and applied" failed (the payload's reason key).
    building = d.get("building_name") or d.get("building") or "That building"
    tech = d.get("technology_name") or d.get("technology") or "a technology"
    doctrine = _doctrine_label(d.get("doctrine"), d.get("branch"))
    reason = d.get("reason")
    if reason == "dormant":
        tail = (
            f"you've researched it, but the {doctrine} Branch isn't your "
            f"commitment on this planet, so its effects are dormant."
        )
    elif reason == "reinstatement_pending":
        tail = (
            "you've researched it, but it's awaiting its reduced-cost "
            "reinstatement job — run 'research' on it to bring it back."
        )
    else:
        lab = d.get("lab_name") or d.get("lab")
        at_lab = f" at your {lab}" if lab else ""
        tail = f"research it{at_lab} first ({doctrine} Branch)."
    return f"|y{building} requires the |w{tech}|n|y technology — {tail}|n"


#: Refusal key -> formatter. Deliberately a table apart from
#: ``NotificationPresenter._FORMATTERS``: these keys are not notification kinds
#: and never travel the event bus, so keeping them separate keeps the
#: presenter-coverage guard's claim ("every kind the new systems emit has a
#: formatter") exact.
_CONSTRUCTION_REFUSAL_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "branch_lab_required": _fmt_refusal_branch_lab_required,
    "branch_mismatch": _fmt_refusal_branch_mismatch,
    "branch_switch_blocked": _fmt_refusal_branch_switch_blocked,
    "branch_unlock_required": _fmt_refusal_branch_unlock_required,
}


def render_construction_refusal(key: Any, data: Any = None) -> str | None:
    """Render one construction-refusal key + payload into player prose.

    The entry point behind ``BuildingSystem.set_refusal_renderer``: the Branch
    construction gates answer a message key carrying structured data (R13.5),
    the validation chain carries it up as a string, and this is where the words
    come from — R3.4's required lab, R3.5's two Branches, R4.1's count with
    R4.2's per-building coordinates, and R6.3's technology and hosting Branch.

    Returns ``None`` — "no words here" — for an unknown or non-string key and
    for a formatter that fails, so the caller keeps its own fallback (the key
    itself) and a rendering bug can never turn a refusal into a crash or, worse,
    into a permitted build.
    """
    if not isinstance(key, str):
        return None
    formatter = _CONSTRUCTION_REFUSAL_FORMATTERS.get(key)
    if formatter is None:
        return None
    try:
        text = formatter(dict(data) if isinstance(data, dict) else {})
    except Exception:  # noqa: BLE001 - a broken formatter costs the words only
        logger.exception("construction refusal %r could not be rendered", key)
        return None
    return text if isinstance(text, str) and text.strip() else None


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
            f"makes it (Armory, Lab, Medbay, or Munitions Plant)."
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


def _fmt_survey_box(d: dict) -> str:
    """The search-box line every survey readout shares."""
    return (
        f"Search area: |c({d.get('x1', '?')}, {d.get('y1', '?')})|n to "
        f"|c({d.get('x2', '?')}, {d.get('y2', '?')})|n "
        f"— {d.get('tiles', '?')} tiles."
    )


def _fmt_survey_started(d: dict) -> str:
    return (
        f"|g[Survey] Signal acquired — a |c{d.get('name', 'base')}|g is "
        f"somewhere in this area.|n\n{_fmt_survey_box(d)}\n"
        f"|wnarrow|n it down, or |wsurvey <x> <y>|n from inside the area to "
        f"take a bearing."
    )


def _fmt_survey_status(d: dict) -> str:
    if not d.get("active"):
        return (
            "|y[Survey] No survey running. Type |wsurvey scan|n to start "
            "one.|n"
        )
    return (
        f"|w[Survey] Tracking a |c{d.get('name', 'base')}|w.|n\n"
        f"{_fmt_survey_box(d)}"
    )


def _fmt_survey_narrowed(d: dict) -> str:
    return (
        f"|g[Survey] Sweep {d.get('narrows', '?')} complete — the signal "
        f"tightened.|n\n{_fmt_survey_box(d)}"
    )


def _fmt_survey_probe(d: dict) -> str:
    return (
        f"|w[Survey] Reading from |c({d.get('x', '?')}, {d.get('y', '?')})|w: "
        f"the |c{d.get('name', 'base')}|w lies to the "
        f"|y{d.get('bearing', 'unknown')}|w — {d.get('band', 'unclear')}.|n"
    )


def _fmt_survey_found(d: dict) -> str:
    tail = (" It is marked on your |wmap|n."
            if d.get("marked") else "")
    return (
        f"|g[Survey] Pinpointed the |c{d.get('name', 'base')}|g at "
        f"|c({d.get('x', '?')}, {d.get('y', '?')})|g!{tail}|n"
    )


def _fmt_survey_abandoned(d: dict) -> str:
    return (
        f"|y[Survey] Dropped the search for the "
        f"{d.get('name', 'outpost')}.|n"
    )


def _fmt_survey_failed(d: dict) -> str:
    reason = d.get("reason")
    if reason == "insufficient_resources":
        head = "|r[Survey] Not enough resources to run that.|n"
        breakdown = d.get("breakdown")
        return f"{head}\n{breakdown}" if breakdown else head
    if reason == "already_active":
        return (
            f"|y[Survey] Already tracking a {d.get('name', 'base')}.|n\n"
            f"{_fmt_survey_box(d)}\n"
            f"Use |wsurvey narrow|n, |wsurvey <x> <y>|n, or "
            f"|wsurvey abandon|n."
        )
    if reason == "outside_box":
        return (
            f"|r[Survey] That tile is outside the search area — probe from "
            f"inside it.|n\n{_fmt_survey_box(d)}"
        )
    messages = {
        "wrong_building": (
            "You need to be standing in your |cSurvey Array|n to do that."
        ),
        "not_owner": "You can only use your own Survey Array.",
        "building_offline": "This Survey Array is offline — repair it first.",
        "building_upgrading": (
            "This Survey Array is being upgraded — it can't be used until the "
            "upgrade finishes (or you 'upgrade cancel')."
        ),
        "no_targets": (
            "The array sweeps the planet and finds nothing new — every enemy "
            "base here is already on your map."
        ),
        "lookup_failed": (
            "The array can't reach the survey network right now — try again "
            "shortly."
        ),
        "no_contract": (
            "No survey running. Type |wsurvey scan|n to start one."
        ),
        "other_planet": (
            f"Your open survey covers |c{d.get('planet', 'another planet')}|n "
            f"— this array only reaches its own planet."
        ),
        "other_planet_active": (
            f"You're already tracking a {d.get('name', 'base')} on "
            f"|c{d.get('planet', 'another planet')}|n. Finish it there, or "
            f"|wsurvey abandon|n to drop it and search here."
        ),
        "target_lost": (
            f"The {d.get('name', 'base')} you were tracking is gone — razed by "
            f"someone else, or it moved on. The search is closed; "
            f"|wsurvey scan|n for a new target."
        ),
        "bad_coords": "Give the tile to probe as two numbers, e.g. |wsurvey 40 62|n.",
        "no_position": "Cannot determine your position.",
    }
    return f"|r[Survey] {messages.get(reason, 'The array cannot do that.')}|n"


def _fmt_insert_applied(d: dict) -> str:
    # Blacksmith bench success (item-loot-economy §4.3): an insert consumable
    # was consumed and the equipped weapon permanently mutated (irreversible).
    return (
        f"|g[Blacksmith] {d.get('item_name', 'Insert')} applied to your "
        f"{d.get('weapon_name', 'weapon')} "
        f"({d.get('slots_used', '?')}/{d.get('slot_limit', '?')} insert "
        f"slots used). The modification is permanent.|n"
    )


def _fmt_insert_failed(d: dict) -> str:
    item = d.get("item_name", "insert")
    reason = d.get("reason")
    messages = {
        "unknown_item": f"No such item '{item}'.",
        "not_an_insert": f"{item} is not a weapon insert.",
        "wrong_building": (
            f"You can't apply {item} here. Stand in your |cBlacksmith|n."
        ),
        "not_owner": "You can only use your own Blacksmith.",
        "building_offline": "This Blacksmith is offline — repair it first.",
        "building_upgrading": (
            "This Blacksmith is being upgraded — it can't be used until the "
            "upgrade finishes (or you 'upgrade cancel')."
        ),
        "no_weapon": (
            f"Equip the weapon you want to modify first, then apply {item}."
        ),
        "weapon_not_equipped": (
            f"You don't have {d.get('weapon_name', 'that weapon')} equipped — "
            f"inserts apply to your equipped weapon."
        ),
        "ambiguous_weapon": (
            f"You have both a {d.get('melee_name', 'melee weapon')} and a "
            f"{d.get('ranged_name', 'ranged weapon')} equipped — say which "
            f"one, e.g. |winsert {item} {d.get('melee_name', '<weapon>')}|n."
        ),
        "no_slots": (
            f"Your {d.get('weapon_name', 'weapon')} has no free insert slots "
            f"({d.get('slot_limit', 1)} at this Blacksmith's level) — upgrade "
            f"the Blacksmith for more."
        ),
        "insufficient_supply": (
            f"You don't carry a {item} — craft one first."
        ),
    }
    return f"|r[Blacksmith] {messages.get(reason, f'Cannot apply {item}.')}|n"


def _fmt_rerolled(d: dict) -> str:
    # Blacksmith bench success (item-loot-economy §4.2/§4.4, task 4.4): the
    # item's base stats were re-rolled (affixes/inserts/rarity untouched) and
    # its quality score re-stamped.
    iqs = d.get("iqs")
    quality = f" — now |w{iqs}%|n quality" if iqs is not None else ""
    return (
        f"|g[Blacksmith] Rerolled your {d.get('item_name', 'item')}"
        f"{quality} (-{d.get('salvage_cost', '?')} Salvage).|n"
    )


def _fmt_reroll_failed(d: dict) -> str:
    item = d.get("item_name", "item")
    reason = d.get("reason")
    # Insufficient resources gets the shared have/need breakdown appended.
    if reason == "insufficient_resources":
        breakdown = d.get("breakdown")
        head = f"|r[Blacksmith] Can't afford to reroll {item}.|n"
        return f"{head}\n{breakdown}" if breakdown else head
    messages = {
        "unknown_item": (
            f"You don't have '{item}' — reroll works on an item you carry "
            f"or have equipped."
        ),
        "not_rerollable": (
            f"{item} has fixed stats — it can't be rerolled."
        ),
        "wrong_building": (
            f"You can't reroll {item} here. Stand in your |cBlacksmith|n."
        ),
        "not_owner": "You can only use your own Blacksmith.",
        "building_offline": "This Blacksmith is offline — repair it first.",
        "building_upgrading": (
            "This Blacksmith is being upgraded — it can't be used until the "
            "upgrade finishes (or you 'upgrade cancel')."
        ),
        "insufficient_salvage": (
            f"Rerolling {item} costs {d.get('salvage_cost', '?')} Salvage — "
            f"you have {d.get('salvage_have', 0)}. Salvage unwanted gear "
            f"here to earn more."
        ),
        "reroll_error": (
            f"Something went wrong rerolling {item}; your Salvage and "
            f"resources were refunded."
        ),
    }
    return f"|r[Blacksmith] {messages.get(reason, f'Cannot reroll {item}.')}|n"


def _fmt_salvaged(d: dict) -> str:
    # Blacksmith bench success (item-loot-economy §5, task 5.2): a carried
    # item was destroyed and its Salvage yield credited (higher IQS and a
    # higher-level bench yield more).
    total = d.get("salvage_total")
    balance = f" (you now have {total})" if total is not None else ""
    return (
        f"|g[Blacksmith] Salvaged your {d.get('item_name', 'item')} into "
        f"|y{d.get('salvage', '?')}|n|g Salvage{balance}.|n"
    )


def _fmt_salvage_failed(d: dict) -> str:
    item = d.get("item_name", "item")
    reason = d.get("reason")
    messages = {
        "unknown_item": (
            f"You don't carry '{item}' — salvage works on a loose item "
            f"you carry."
        ),
        "equipped": (
            f"{item} is equipped — unequip it first to salvage it."
        ),
        "not_gear": (
            f"{item} isn't salvageable gear."
        ),
        "wrong_building": (
            f"You can't salvage {item} here. Stand in your |cBlacksmith|n."
        ),
        "not_owner": "You can only use your own Blacksmith.",
        "building_offline": "This Blacksmith is offline — repair it first.",
        "building_upgrading": (
            "This Blacksmith is being upgraded — it can't be used until the "
            "upgrade finishes (or you 'upgrade cancel')."
        ),
    }
    return f"|r[Blacksmith] {messages.get(reason, f'Cannot salvage {item}.')}|n"


def _fmt_refined(d: dict) -> str:
    # Refinery success (item-loot-economy §7, task 5.3): a resource batch
    # was burned and its Salvage yield credited (a higher-level Refinery
    # converts at a better rate). Salvage is the ONLY output — never Nexium.
    total = d.get("salvage_total")
    balance = f" (you now have {total})" if total is not None else ""
    return (
        f"|g[Refinery] Refined |w{d.get('amount', '?')} "
        f"{d.get('resource', 'resources')}|n|g into "
        f"|y{d.get('salvage', '?')}|n|g Salvage{balance}.|n"
    )


def _fmt_refine_failed(d: dict) -> str:
    resource = d.get("resource", "that")
    reason = d.get("reason")
    messages = {
        "unknown_resource": (
            f"'{resource}' isn't a refinable resource."
        ),
        "wrong_building": (
            f"You can't refine {resource} here. Stand in your |cRefinery|n."
        ),
        "not_owner": "You can only use your own Refinery.",
        "building_offline": "This Refinery is offline — repair it first.",
        "building_upgrading": (
            "This Refinery is being upgraded — it can't be used until the "
            "upgrade finishes (or you 'upgrade cancel')."
        ),
        "insufficient_resources": (
            f"You don't have enough {resource} — you carry "
            f"{d.get('have', 0)}, need {d.get('need', '?')}."
        ),
        "too_little": (
            f"That little {resource} wouldn't yield any Salvage — refine "
            f"a bigger batch."
        ),
    }
    return f"|r[Refinery] {messages.get(reason, f'Cannot refine {resource}.')}|n"


class NotificationPresenter:
    """Formats ``PLAYER_NOTIFICATION`` events and delivers them to players."""

    #: kind -> (data dict) -> formatted string. The single source of truth for
    #: every per-player notification line.
    _FORMATTERS: dict[str, Callable[[dict], str]] = {
        "rank_level_up": _fmt_rank_level_up,
        "xp_gain": _fmt_xp_gain,
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
        "insert_applied": _fmt_insert_applied,
        "insert_failed": _fmt_insert_failed,
        "rerolled": _fmt_rerolled,
        "reroll_failed": _fmt_reroll_failed,
        "salvaged": _fmt_salvaged,
        "salvage_failed": _fmt_salvage_failed,
        "refined": _fmt_refined,
        "refine_failed": _fmt_refine_failed,
        # Survey Array kinds (outpost triangulation).
        "survey_started": _fmt_survey_started,
        "survey_status": _fmt_survey_status,
        "survey_narrowed": _fmt_survey_narrowed,
        "survey_probe": _fmt_survey_probe,
        "survey_found": _fmt_survey_found,
        "survey_abandoned": _fmt_survey_abandoned,
        "survey_failed": _fmt_survey_failed,
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
        "pvp_gear_dropped": _fmt_pvp_gear_dropped,
        "base_deactivated": _fmt_base_deactivated,
        "base_reactivated": _fmt_base_reactivated,
        # Branch feature: the pre-charge dormancy report the switch gate emits.
        "branch_dormancy_warning": _fmt_branch_dormancy_warning,
        # Branch feature: progress toward emptying a Branch_Estate, reported on
        # each successful demolish of a Branch_Building.
        "branch_estate_progress": _fmt_branch_estate_progress,
        # Branch feature: the whole technology view — commitment, signature
        # vector, researched and available techs, and the dormant Branches.
        "technology_view": _fmt_technology_view,
        # Branch feature: the Vector_Operation lifecycle (R13.6). One kind per
        # transition a player can see, covering all six lifecycle states, plus
        # the recipient's reading of a resolution and the missing-consent
        # refusal. The driver publishes each with a variable kind, so this table
        # is what the presenter-coverage guard checks against
        # ``operation_contract.VECTOR_NOTIFICATION_KINDS``.
        "vector_incoming": _fmt_vector_incoming,
        "vector_resolved": _fmt_vector_resolved,
        "vector_hit": _fmt_vector_hit,
        "vector_suspended": _fmt_vector_suspended,
        "vector_resumed": _fmt_vector_resumed,
        "vector_expired": _fmt_vector_expired,
        "vector_cancelled": _fmt_vector_cancelled,
        "vector_discarded": _fmt_vector_discarded,
        "vector_consent_required": _fmt_vector_consent_required,
    }

    #: Notification kinds that change the RECIPIENT's own HP or level. After
    #: delivering one, we push a fresh status prompt so the player's webclient
    #: footer (and prompt-aware telnet clients) reflect the new HP/level live —
    #: without the player having to type a command. "attacked" = took a hit,
    #: "healed" = restored, "rank_level_up" = levelled. (Kinds about a player's
    #: BUILDINGS/UNITS being hit are excluded: the player's own HP is unchanged.)
    #: ``xp_gain`` is included so the prompt's XP bar advances on a SERVER-driven
    #: award (a timed build completing, an agent finishing training) where the
    #: player typed nothing and ``at_post_cmd`` will not fire. On an award that
    #: also levels the player this pushes twice — ``xp_gain`` fires before the
    #: level sync, so its push can read the pre-sync level, and the
    #: ``rank_level_up`` push immediately after is the authoritative one. Both
    #: are OOB-only (no printed line), so the transient is invisible.
    _STATUS_AFFECTING_KINDS = frozenset({
        "attacked", "healed", "rank_level_up", "xp_gain",
    })

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
        # A copy carrying the recipient under a private key, so a formatter that
        # must read player state (e.g. the directive objective, which annotates
        # the requirements the player has not met) can do so without the
        # publishing system composing prose on its behalf. Formatters that don't
        # look for it are unaffected, and the caller's dict is never mutated.
        payload = dict(data or {})
        payload.setdefault("_player", player)
        try:
            message = formatter(payload)
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
