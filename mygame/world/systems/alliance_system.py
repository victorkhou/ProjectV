"""
Alliance system — the game's first many-players-to-one-group construct.

This module co-locates the persistent registry and the single-writer authority,
mirroring how ``world.player_lifecycle`` co-locates the FSM and its helpers:

* :class:`AllianceRegistry` — a persistent ``DefaultScript`` holding every
  Alliance_Record (the non-derivable state: name, tag, leader, roster, treasury,
  active perks, pending invites/requests, open-join flag, withdraw window).
* :class:`AllianceSystem` — a :class:`BaseSystem` that is the SINGLE WRITER of
  every Alliance_Record and every Member_Pointer (``db.player_alliance`` /
  ``db.alliance_rank`` on a ``CombatCharacter``). All founding, invite, join,
  leave, kick, disband, transfer, treasury, and perk mutations route through it.

Design invariants (see .kiro/specs/alliance):

* **Single writer** — no other module writes an Alliance_Record or a
  Member_Pointer (the admin router routes through this system too).
* **Single ally authority** — ``world.utils.are_allied`` is the one predicate for
  "same side"; it calls :meth:`AllianceSystem.alliance_exists` to confirm a live
  record.
* **Value-based reads** — an Evennia ``DbHolder`` returns ``None`` for unset
  attributes, so every read coalesces ``None`` and compares with ``is None`` /
  ``==`` (never ``hasattr`` on ``db``, never truthiness on ``player_alliance``).
* **Read-modify-reassign** — in-place mutation of a ``SaverDict``/``SaverSet`` is
  unreliable in this codebase; every treasury/roster/active_perks/pending-*
  mutation reads, mutates a plain copy, and writes the whole container back.
* **Roster rebuildable** — the roster is reconstructable from the per-character
  Member_Pointers via ``search_object_attribute`` (NOT a ``db_strvalue`` filter,
  which matches nothing for a pickled int).
* **Shallow integration** — ``owner_has_active_hq`` / ``active_hq_owner_ids`` are
  never consulted or modified here; an ally's HQ does not power your base.

Known constraints (settled simplifications — see .kiro/specs/alliance R16):

* **Even-split on disband** — the treasury is split across the CURRENT roster
  (remainder to the Leader), not discarded. RESIDUAL RISK (accepted, not solved):
  because officer withdraws are capped rather than the disband being a
  recent-window split, a Leader can still kick everyone THEN disband to keep the
  whole split. Documented, not defended against.
* **Grandfathered perks** — an activated perk stays active even if the alliance
  level later drops below its tier (activation is a permanent purchase); the
  level-recompute path is read-only for perks.
* **Kept fog residual-intel** — tiles / enemy-building intel discovered through
  an ally's vision persist as ordinary discovered memory after a member leaves
  (only LIVE shared vision cuts off on leave).
* **Free founding, uncapped treasury** — founding costs no resources and the
  treasury has no capacity cap in v1; officer count IS capped
  (``alliance_max_officers``) to keep withdraw/kick privilege scarce.
* **Single-character-per-account** — membership is per-CHARACTER while chat is
  per-ACCOUNT; this is coherent only while ``MAX_NR_CHARACTERS == 1`` (multi-char
  is out of scope).
* **Best-effort reconciliation** — roster/pointer consistency is reconciled on
  registry load + on demand (no timer); the Member_Pointer is the tiebreaker. An
  absent Leader is handled by reconcile succession or a proactive Officer
  ``claim``. Treasury deposit/withdraw is protected by ordered writes + a
  pre-write re-read + in-call rollback, not a cross-object transaction.
* **First-guess tuning** — the score weights, decay knobs, level thresholds, and
  the perk catalog are all first-guess values flagged for live balancing.
"""

from __future__ import annotations

import logging
from typing import Any

from world.systems.base_system import BaseSystem
from world.constants import (
    ALLIANCE_RANK_LEADER,
    ALLIANCE_RANK_OFFICER,
    ALLIANCE_RANK_MEMBER,
    ALLIANCE_RANK_ORDER,
    ALLIANCE_NAME_DENYLIST,
    ALLIANCE_IGNORE_ALL,
    ALLIANCE_PERK_CATEGORIES,
    RESOURCE_TYPES,
    PLAYER_STATE_PLAYING,
)

logger = logging.getLogger("mygame.alliance")

# Seconds per day, for the leader-absence threshold (last-seen is wall-clock).
_SECONDS_PER_DAY = 86400


# The persistent registry Script (``AllianceRegistry``) lives in
# ``typeclasses.scripts`` — a typeclass, like ``GameTickScript`` — because
# ``world/systems`` may not import evennia at module scope (a layering
# invariant). It is a thin persistent data holder (``db.alliances`` +
# ``db.next_alliance_id``) exposing ``get`` / ``all_alliances`` / ``by_tag`` /
# ``allocate_id`` / ``put`` / ``delete``; ALL mutation logic lives here in
# :class:`AllianceSystem` (the single writer). The two are wired together at the
# composition root (``server/conf/game_init.py``). The module-level ``_normalize``
# below is shared by both.


# ------------------------------------------------------------------ #
#  Helpers (module-level, pure)
# ------------------------------------------------------------------ #

def _normalize(text: str) -> str:
    """NFKC-normalize + casefold *text* for uniqueness/denylist comparison.

    NFKC folds homoglyph/compatibility forms together so a Cyrillic-A
    'Аdmin' cannot slip past the reserved-name check that a plain lower() would
    miss. Returns ``""`` for a non-string.
    """
    import unicodedata
    if not isinstance(text, str):
        return ""
    return unicodedata.normalize("NFKC", text).strip().casefold()


def _has_markup(text: str) -> bool:
    """Return True if *text* contains an Evennia color/markup code.

    Evennia markup is ``|`` followed by a color/style code (``|r``, ``|500``,
    ``|[R``, ``|n`` …). Names/tags with markup would inject color into the
    leaderboard and chat, so they are rejected.
    """
    return "|" in (text or "")


def _roster_ids(record: dict) -> list[int]:
    """Return every member id in *record* (leader + officers + members)."""
    ids: list[int] = []
    leader = record.get("leader_id")
    if leader is not None:
        ids.append(leader)
    ids.extend(record.get("officer_ids", []) or [])
    ids.extend(record.get("member_ids", []) or [])
    return ids


# ------------------------------------------------------------------ #
#  The single-writer system
# ------------------------------------------------------------------ #

class AllianceSystem(BaseSystem):
    """Single writer of all alliance state.

    Constructed as ``AllianceSystem(registry, event_bus, alliance_registry=...,
    tick_provider=...)`` and registered in ``game_systems`` as
    ``"alliance_system"``. ``alliance_registry`` is the persistent
    :class:`AllianceRegistry`; ``tick_provider`` is a zero-arg callable returning
    the current game tick (for expiry/decay/cooldown math) — both injectable for
    tests.
    """

    def __init__(self, registry, event_bus, alliance_registry=None,
                 tick_provider=None) -> None:
        super().__init__(registry, event_bus)
        self._alliances = alliance_registry
        self._tick_provider = tick_provider

    # ------------------------------------------------------------------ #
    #  Small accessors
    # ------------------------------------------------------------------ #

    def _now_tick(self) -> int:
        """Current game tick (0 if no provider wired)."""
        if self._tick_provider is None:
            return 0
        try:
            return int(self._tick_provider())
        except Exception:  # noqa: BLE001
            return 0

    @property
    def _bal(self):
        """The live BalanceConfig."""
        return self.registry.balance

    def _record(self, alliance_id) -> dict | None:
        """Return the Alliance_Record for *alliance_id*, or ``None``."""
        if self._alliances is None:
            return None
        return self._alliances.get(alliance_id)

    def alliance_exists(self, alliance_id) -> bool:
        """Return True if *alliance_id* resolves to a live record.

        The liveness check ``world.utils.are_allied`` calls to reject a stale
        pointer left by a disband while a member was offline.
        """
        return self._record(alliance_id) is not None

    def _rank_of(self, player) -> str | None:
        """Return *player*'s Alliance_Rank pointer value (or None)."""
        return getattr(getattr(player, "db", None), "alliance_rank", None)

    def _alliance_of(self, player) -> int | None:
        """Return *player*'s alliance id pointer value (or None)."""
        return getattr(getattr(player, "db", None), "player_alliance", None)

    def _record_of(self, player) -> dict | None:
        """Return the live record *player* points at (or None)."""
        return self._record(self._alliance_of(player))

    # ------------------------------------------------------------------ #
    #  Member resolution (the single id -> object bridge)
    # ------------------------------------------------------------------ #

    def _resolve_member(self, char_id) -> Any | None:
        """Resolve a stored member ``.id`` to a live character object, or None.

        Roster records store ``.id`` ints; derivation (level, score, buildings)
        needs live objects. Uses ``evennia.search_object`` and returns ``None``
        on any miss or failure so callers never raise.
        """
        if char_id is None:
            return None
        try:
            from evennia.objects.models import ObjectDB
            obj = ObjectDB.objects.filter(id=char_id).first()
            return obj
        except Exception:  # noqa: BLE001 - resolution failure -> treat as absent
            return None

    def _live_members(self, alliance_id) -> list:
        """Return resolved member objects whose pointer still == *alliance_id*.

        Reconcile-then-score: a ghost id left in a roster by a crash-orphaned
        path — or a member now in a rival alliance — is filtered out, so it can
        never inflate the score or the level.
        """
        record = self._record(alliance_id)
        if record is None:
            return []
        out = []
        for cid in _roster_ids(record):
            obj = self._resolve_member(cid)
            if obj is not None and self._alliance_of(obj) == alliance_id:
                out.append(obj)
        return out

    def _is_real_player(self, obj) -> bool:
        """Delegate to the shared real-player guard (has_account/Sentinel/npc)."""
        from world.utils import _is_real_player
        return _is_real_player(obj)

    # ------------------------------------------------------------------ #
    #  Member_Pointer writes (the ONLY place these are assigned)
    # ------------------------------------------------------------------ #

    def _set_pointer(self, player, alliance_id, rank) -> None:
        """Write a player's Member_Pointer (id + rank). Single-writer choke."""
        player.db.player_alliance = alliance_id
        player.db.alliance_rank = rank

    def _clear_pointer(self, player) -> None:
        """Clear a player's Member_Pointer back to 'no alliance'."""
        player.db.player_alliance = None
        player.db.alliance_rank = None

    # ------------------------------------------------------------------ #
    #  Event + notification plumbing (best-effort, never breaks a mutation)
    # ------------------------------------------------------------------ #

    def _publish(self, event_name, **payload) -> None:
        """Publish an event, swallowing any error (telemetry never blocks)."""
        try:
            self.event_bus.publish(event_name, **payload)
        except Exception:  # noqa: BLE001
            logger.debug("Alliance event %s failed", event_name, exc_info=True)

    def _channel_key(self, alliance_id) -> str:
        """The immutable channel key for an alliance (no player-facing alias)."""
        return f"alliance_{alliance_id}"

    # ------------------------------------------------------------------ #
    #  Level derivation (SUM of member levels through the tier table)
    # ------------------------------------------------------------------ #

    def compute_alliance_level(self, alliance_id) -> int:
        """Return the alliance's tier from the SUM of member Entity_Levels.

        Derived (never stored authoritatively): the sum of live members'
        ``get_player_level`` mapped through ``balance.alliance_level_thresholds``
        (the greatest threshold ``<=`` the sum), clamped to ``[1, #tiers]``.
        A non-numeric/unresolvable member contributes the default level rather
        than raising (mirrors ``get_player_level``'s coercion). Monotonic: more
        aggregate levels never lowers the tier.
        """
        from world.utils import get_player_level

        thresholds = self._bal.alliance_level_thresholds or {0: 1}
        max_tier = max(thresholds.values()) if thresholds else 1

        total = 0
        for member in self._live_members(alliance_id):
            total += get_player_level(member, default=1)

        tier = 1
        best_key = -1
        for min_sum, t in thresholds.items():
            if total >= min_sum and min_sum > best_key:
                best_key = min_sum
                tier = t
        return max(1, min(int(tier), int(max_tier)))

    # ------------------------------------------------------------------ #
    #  Name / tag validation (R19)
    # ------------------------------------------------------------------ #

    def _validate_name_tag(self, name, tag, *, exclude_id=None) -> str | None:
        """Return an error string, or ``None`` if *name*/*tag* are acceptable.

        Enforces: non-empty after trim; no Evennia markup; tag ASCII-alnum and
        length-bounded; name ASCII-alnum + single interior spaces; neither may
        contain a reserved-substring (post-NFKC, casefolded); both unique after
        normalization (excluding *exclude_id* for a rename of the same alliance).
        """
        if not isinstance(name, str) or not name.strip():
            return "An alliance needs a name."
        if not isinstance(tag, str) or not tag.strip():
            return "An alliance needs a tag."
        name = name.strip()
        tag = tag.strip()
        if _has_markup(name) or _has_markup(tag):
            return "Names and tags may not contain color/markup codes."

        max_tag = int(self._bal.alliance_tag_max_len)
        if len(tag) > max_tag:
            return f"Tag too long (max {max_tag} characters)."
        if not tag.isascii() or not tag.isalnum():
            return "Tag must be ASCII letters and digits only."
        # Name: ASCII, alphanumerics and single interior spaces.
        if not name.isascii():
            return "Name must use ASCII characters only."
        collapsed = " ".join(name.split())
        if collapsed != name:
            return "Name may not have leading, trailing, or repeated spaces."
        if not all(c.isalnum() or c == " " for c in name):
            return "Name must be letters, digits, and single spaces only."

        norm_name = _normalize(name)
        norm_tag = _normalize(tag)
        # Also test a space-STRIPPED form: interior spaces are allowed in a name,
        # so "a d m i n" normalizes to "a d m i n" and would slip a plain
        # substring check — collapse spaces so spaced-out reserved words are
        # caught too (defeats the impersonation bypass).
        norm_name_nospace = norm_name.replace(" ", "")
        norm_tag_nospace = norm_tag.replace(" ", "")
        for banned in ALLIANCE_NAME_DENYLIST:
            if (banned in norm_name or banned in norm_tag
                    or banned in norm_name_nospace or banned in norm_tag_nospace):
                return f"Names/tags may not contain the reserved word '{banned}'."

        for rec in (self._alliances.all_alliances() if self._alliances else []):
            if exclude_id is not None and rec.get("id") == exclude_id:
                continue
            if _normalize(rec.get("name", "")) == norm_name:
                return "An alliance with that name already exists."
            if _normalize(rec.get("tag", "")) == norm_tag:
                return "An alliance with that tag already exists."
        return None

    # ------------------------------------------------------------------ #
    #  Founding (R2)
    # ------------------------------------------------------------------ #

    def found(self, player, name, tag) -> int | None:
        """Found a new alliance led by *player*. Return its id, or ``None``.

        Refuses (writing nothing) if *player* is not a real player, is below
        ``alliance_found_min_level``, is already in an alliance, or the name/tag
        fails validation. Founding is FREE in v1 (no resource cost).
        """
        from world.utils import get_player_level

        if self._alliances is None:
            return None
        if not self._is_real_player(player):
            return None
        if self._alliance_of(player) is not None:
            player.msg("You are already in an alliance.")
            return None
        min_level = int(self._bal.alliance_found_min_level)
        if get_player_level(player) < min_level:
            player.msg(f"You must be level {min_level} to found an alliance.")
            return None
        err = self._validate_name_tag(name, tag)
        if err:
            player.msg(err)
            return None

        alliance_id = self._alliances.allocate_id()
        record = {
            "id": alliance_id,
            "name": name.strip(),
            "tag": tag.strip(),
            "leader_id": getattr(player, "id", None),
            "officer_ids": [],
            "member_ids": [],
            "treasury": {},
            "active_perks": {},
            "pending_invites": [],
            "pending_requests": [],
            "open_join": False,
            "withdraw_window": {},
            "created_tick": self._now_tick(),
            "renamed_tick": 0,
        }
        self._alliances.put(record)
        self._set_pointer(player, alliance_id, ALLIANCE_RANK_LEADER)
        self._ensure_channel(alliance_id)
        self._subscribe(player, alliance_id)

        from world.event_bus import ALLIANCE_CREATED
        self._publish(ALLIANCE_CREATED, alliance_id=alliance_id, leader=player)
        player.msg(f"You found the alliance |c{name}|n [{tag}].")
        return alliance_id

    # ------------------------------------------------------------------ #
    #  Channel wiring (Account-level, keyed by immutable alliance_<id>)
    # ------------------------------------------------------------------ #

    def _get_channel_db(self):
        """Return Evennia's ChannelDB class, or None outside a full env."""
        try:
            from evennia.comms.models import ChannelDB
            return ChannelDB
        except Exception:  # noqa: BLE001
            return None

    def _ensure_channel(self, alliance_id):
        """Create the alliance's channel if missing; return it (or None).

        Keyed by the immutable ``alliance_<id>`` with NO player-facing alias, so
        it can never collide with the reserved global Public/chat/pub channel and
        it survives a rename. Best-effort — never raises into a mutation.
        """
        channel_db = self._get_channel_db()
        if channel_db is None:
            return None
        key = self._channel_key(alliance_id)
        try:
            from django.db import transaction
            with transaction.atomic():
                existing = channel_db.objects.filter(db_key=key).first()
                if existing is not None:
                    return existing
                from evennia import create_channel
                return create_channel(key, desc=f"Alliance {alliance_id} chat")
        except Exception:  # noqa: BLE001
            logger.debug("Alliance channel ensure failed for %s", alliance_id, exc_info=True)
            return None

    def _account_of(self, player):
        """Best-effort account behind a character puppet (or None)."""
        return getattr(player, "account", None)

    def _subscribe(self, player, alliance_id) -> None:
        """Subscribe *player*'s account to the alliance channel (best-effort)."""
        channel_db = self._get_channel_db()
        account = self._account_of(player)
        if channel_db is None or account is None:
            return
        try:
            from django.db import transaction
            with transaction.atomic():
                channel = channel_db.objects.filter(
                    db_key=self._channel_key(alliance_id)
                ).first()
                if channel is not None and not channel.has_connection(account):
                    channel.connect(account)
        except Exception:  # noqa: BLE001
            logger.debug("Alliance subscribe failed", exc_info=True)

    def _unsubscribe(self, player, alliance_id) -> None:
        """Unsubscribe *player*'s account from the alliance channel."""
        channel_db = self._get_channel_db()
        account = self._account_of(player)
        if channel_db is None or account is None:
            return
        try:
            from django.db import transaction
            with transaction.atomic():
                channel = channel_db.objects.filter(
                    db_key=self._channel_key(alliance_id)
                ).first()
                if channel is not None and channel.has_connection(account):
                    channel.disconnect(account)
        except Exception:  # noqa: BLE001
            logger.debug("Alliance unsubscribe failed", exc_info=True)

    def _destroy_channel(self, alliance_id) -> None:
        """Delete the alliance channel object (called on disband)."""
        channel_db = self._get_channel_db()
        if channel_db is None:
            return
        try:
            from django.db import transaction
            with transaction.atomic():
                channel = channel_db.objects.filter(
                    db_key=self._channel_key(alliance_id)
                ).first()
                if channel is not None:
                    channel.delete()
        except Exception:  # noqa: BLE001
            logger.debug("Alliance channel destroy failed", exc_info=True)

    def _broadcast(self, alliance_id, message) -> None:
        """Send a system line to the alliance channel (best-effort)."""
        channel_db = self._get_channel_db()
        if channel_db is None:
            return
        try:
            channel = channel_db.objects.filter(
                db_key=self._channel_key(alliance_id)
            ).first()
            if channel is not None:
                channel.msg(message)
        except Exception:  # noqa: BLE001
            logger.debug("Alliance broadcast failed", exc_info=True)

    # ------------------------------------------------------------------ #
    #  Membership — invitations (R3, R18)
    # ------------------------------------------------------------------ #

    def _member_count(self, record) -> int:
        """Number of members in *record* (leader + officers + members)."""
        return len(_roster_ids(record))

    def _invite_expiry_tick(self) -> int:
        """The tick a new invite expires at (now + invite_expiry_days)."""
        days = int(self._bal.alliance_invite_expiry_days)
        # ticks ~= seconds at 1 tick/s; days -> ticks.
        return self._now_tick() + days * _SECONDS_PER_DAY

    def _prune_expired_invites(self, record) -> list[dict]:
        """Return *record*'s pending invites with expired ones removed."""
        now = self._now_tick()
        return [
            inv for inv in (record.get("pending_invites", []) or [])
            if inv.get("expiry_tick", 0) > now
        ]

    def _is_ignored_by(self, target, inviter) -> bool:
        """Return True if *target* has *inviter* (or all) on its Ignore_List."""
        ignore = getattr(getattr(target, "db", None), "alliance_invite_ignore", None)
        if ignore is None:
            return False
        if ignore == ALLIANCE_IGNORE_ALL:
            return True
        try:
            return getattr(inviter, "id", None) in ignore
        except TypeError:
            return False

    def invite(self, actor, target) -> bool:
        """Invite *target* to *actor*'s alliance (Officer+). Return success.

        Refuses (writing nothing) if actor lacks rank, the target is not a real
        player, the target is already in an alliance, the target ignores the
        inviter, or an invite cooldown is still active. Idempotent for a target
        already pending in this alliance.
        """
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if not self._can(actor, ALLIANCE_RANK_OFFICER):
            actor.msg("Only officers and the leader can invite.")
            return False
        if not self._is_real_player(target):
            actor.msg("You can only invite another player.")
            return False
        if self._alliance_of(target) is not None:
            actor.msg(f"{target.key} is already in an alliance.")
            return False
        if self._is_ignored_by(target, actor):
            actor.msg(f"{target.key} is not accepting invites from you.")
            return False

        now = self._now_tick()
        invites = self._prune_expired_invites(record)
        tid = getattr(target, "id", None)
        for inv in invites:
            if inv.get("id") == tid:
                # Cooldown gate on re-invite — also enforces the post-decline
                # suppression window (a declined stub carries the same shape).
                cd = int(self._bal.alliance_invite_cooldown_ticks)
                if now - inv.get("sent_tick", 0) < cd:
                    if inv.get("declined"):
                        actor.msg(f"{target.key} recently declined — wait before re-inviting.")
                    else:
                        actor.msg(f"{target.key} was invited recently — wait before re-inviting.")
                    return False
                # Cooldown elapsed: revive as a fresh, live invite (clear the
                # declined suppression flag so it shows in the target's inbox).
                inv["sent_tick"] = now
                inv["expiry_tick"] = self._invite_expiry_tick()
                inv.pop("declined", None)
                break
        else:
            invites.append({
                "id": tid,
                "sent_tick": now,
                "expiry_tick": self._invite_expiry_tick(),
            })
        record["pending_invites"] = invites
        self._alliances.put(record)

        actor.msg(f"You invite {target.key} to the alliance.")
        try:
            target.msg(
                f"|c{record['name']}|n [{record['tag']}] has invited you to their "
                f"alliance. Type |walliance accept {record['tag']}|n to join or "
                f"|walliance decline {record['tag']}|n to decline."
            )
        except Exception:  # noqa: BLE001
            pass
        return True

    def _find_invite_record(self, player, ref):
        """Resolve a pending-invite reference to the target Alliance_Record.

        *ref* may be an alliance tag, a 1-based inbox index, or a raw id. Returns
        the record, or ``None`` if the player has no matching live pending invite.
        """
        pid = getattr(player, "id", None)
        inbox = self.pending_invites_for(player)  # already filtered to live+unexpired
        if not inbox:
            return None
        # Numeric ref: try inbox index first, then a raw alliance id.
        idx = None
        try:
            idx = int(ref)
        except (TypeError, ValueError):
            idx = None
        if idx is not None:
            if 1 <= idx <= len(inbox):
                return self._record(inbox[idx - 1]["alliance_id"])
            rec = self._record(idx)
            if rec is not None and any(e["alliance_id"] == idx for e in inbox):
                return rec
            return None
        # Otherwise treat ref as a tag.
        rec = self._alliances.by_tag(ref) if self._alliances else None
        if rec is None:
            return None
        if any(e["alliance_id"] == rec["id"] for e in inbox):
            return rec
        return None

    def pending_invites_for(self, player) -> list[dict]:
        """Return *player*'s live, unexpired invites as ``{alliance_id, tag, name}``.

        The inbox listing (``alliance invites``) and the reference resolver both
        read this. Expired invites are skipped (and lazily pruned from the record
        they live on). Also skips invites into an alliance that no longer exists.
        """
        pid = getattr(player, "id", None)
        if pid is None or self._alliances is None:
            return []
        now = self._now_tick()
        out = []
        for rec in self._alliances.all_alliances():
            for inv in (rec.get("pending_invites", []) or []):
                if inv.get("id") != pid:
                    continue
                if inv.get("expiry_tick", 0) <= now:
                    continue
                # A declined stub is a suppression marker, not a live invite —
                # it must not show in the inbox nor be acceptable.
                if inv.get("declined"):
                    continue
                out.append({
                    "alliance_id": rec["id"],
                    "tag": rec.get("tag", ""),
                    "name": rec.get("name", ""),
                })
        return out

    def replay_invites(self, player) -> None:
        """Re-deliver a player's live pending invites on login (best-effort)."""
        inbox = self.pending_invites_for(player)
        if not inbox:
            return
        try:
            lines = [f"  [{e['tag']}] |c{e['name']}|n" for e in inbox]
            player.msg(
                "You have pending alliance invitations:\n"
                + "\n".join(lines)
                + "\nType |walliance accept <tag>|n or |walliance decline <tag>|n."
            )
        except Exception:  # noqa: BLE001
            pass

    def accept(self, player, ref) -> bool:
        """Accept a pending invite (by tag / inbox index / id). Return success."""
        from world.utils import get_player_level

        record = self._find_invite_record(player, ref)
        if record is None:
            player.msg("You have no such pending invitation.")
            return False
        if self._alliance_of(player) is not None:
            player.msg("You are already in an alliance.")
            return False
        gate = self._join_gate(player, record)
        if gate is not None:
            player.msg(gate)
            # A failed level gate removes the now-stale invite.
            self._remove_invite(record, getattr(player, "id", None))
            return False
        self._admit(player, record, ALLIANCE_RANK_MEMBER)
        return True

    def decline(self, player, ref) -> bool:
        """Decline a pending invite. Starts the post-decline suppression window."""
        record = self._find_invite_record(player, ref)
        if record is None:
            player.msg("You have no such pending invitation.")
            return False
        self._remove_invite(record, getattr(player, "id", None), decline=True)
        player.msg(f"You decline the invitation from {record['name']}.")
        return True

    def _remove_invite(self, record, char_id, *, decline=False) -> None:
        """Remove *char_id*'s pending invite from *record* (read-modify-reassign).

        On a DECLINE, replace the entry with a suppression stub (``declined``
        flag, ``sent_tick`` = now, ``expiry_tick`` = now + invite cooldown)
        instead of dropping it, so ``invite()``'s cooldown branch finds it and
        refuses an immediate re-invite for the anti-harassment window. The stub
        is excluded from the inbox / accept resolution (``pending_invites_for``
        skips ``declined``) and self-cleans when the window elapses (pruned by
        ``expiry_tick``). A non-decline removal (stale invite after a failed
        level gate) just drops the entry.
        """
        invites = [
            inv for inv in (record.get("pending_invites", []) or [])
            if inv.get("id") != char_id
        ]
        if decline:
            now = self._now_tick()
            cd = int(self._bal.alliance_invite_cooldown_ticks)
            invites.append({
                "id": char_id,
                "sent_tick": now,
                "expiry_tick": now + cd,
                "declined": True,
            })
        record["pending_invites"] = invites
        self._alliances.put(record)

    def _join_gate(self, player, record) -> str | None:
        """Return an error string if *player* may not join *record*, else None.

        Shared by accept / open-join / accept-request: level gate, member cap,
        one-alliance, real-player, and rejoin-cooldown.
        """
        from world.utils import get_player_level

        if not self._is_real_player(player):
            return "Only players can join an alliance."
        if self._alliance_of(player) is not None:
            return "You are already in an alliance."
        min_level = int(self._bal.alliance_join_min_level)
        if get_player_level(player) < min_level:
            return f"You must be level {min_level} to join an alliance."
        if self._member_count(record) >= int(self._bal.alliance_max_members):
            return "That alliance is full."
        # Rejoin cooldown after a recent leave/kick.
        until = getattr(getattr(player, "db", None), "alliance_rejoin_until", 0) or 0
        if self._now_tick() < until:
            return "You left an alliance too recently — wait before joining another."
        return None

    def _admit(self, player, record, rank) -> None:
        """Admit *player* into *record* at *rank* (the shared join tail).

        Sets the pointer, adds to the roster, purges the joiner's id from EVERY
        alliance's pending invites AND requests (so a stale invite can't later
        re-activate), subscribes to chat, publishes + notifies.
        """
        alliance_id = record["id"]
        members = list(record.get("member_ids", []) or [])
        pid = getattr(player, "id", None)
        if pid not in members:
            members.append(pid)
        record["member_ids"] = members
        self._alliances.put(record)
        self._set_pointer(player, alliance_id, rank)
        self._purge_pending_everywhere(pid)
        self._subscribe(player, alliance_id)

        from world.event_bus import ALLIANCE_MEMBER_JOINED
        self._publish(ALLIANCE_MEMBER_JOINED, alliance_id=alliance_id, player=player)
        player.msg(f"You join |c{record['name']}|n [{record['tag']}].")
        self._broadcast(alliance_id, f"{player.key} has joined the alliance.")

    def _purge_pending_everywhere(self, char_id) -> None:
        """Remove *char_id* from every alliance's pending invites AND requests."""
        if self._alliances is None:
            return
        for rec in self._alliances.all_alliances():
            invites = [i for i in (rec.get("pending_invites", []) or [])
                       if i.get("id") != char_id]
            requests = [r for r in (rec.get("pending_requests", []) or [])
                        if r != char_id]
            if (len(invites) != len(rec.get("pending_invites", []) or [])
                    or len(requests) != len(rec.get("pending_requests", []) or [])):
                rec["pending_invites"] = invites
                rec["pending_requests"] = requests
                self._alliances.put(rec)

    # ------------------------------------------------------------------ #
    #  Permission helper
    # ------------------------------------------------------------------ #

    def _can(self, player, min_rank) -> bool:
        """Return True if *player*'s Alliance_Rank is at least *min_rank*."""
        rank = self._rank_of(player)
        if rank is None:
            return False
        return ALLIANCE_RANK_ORDER.get(rank, 0) >= ALLIANCE_RANK_ORDER.get(min_rank, 99)

    # ------------------------------------------------------------------ #
    #  Membership — requests / open-join / outsider info (R17)
    # ------------------------------------------------------------------ #

    def apply_request(self, player, ref) -> bool:
        """Request to join an alliance by tag (inbound, Officer approves later)."""
        if self._alliances is None:
            return False
        if self._alliance_of(player) is not None:
            player.msg("You are already in an alliance.")
            return False
        record = self._alliances.by_tag(ref)
        if record is None:
            player.msg("No alliance with that tag.")
            return False
        gate = self._join_gate(player, record)
        if gate is not None:
            player.msg(gate)
            return False
        pid = getattr(player, "id", None)
        requests = list(record.get("pending_requests", []) or [])
        if pid not in requests:
            requests.append(pid)
            record["pending_requests"] = requests
            self._alliances.put(record)
        from world.event_bus import ALLIANCE_REQUEST_CREATED
        self._publish(ALLIANCE_REQUEST_CREATED, alliance_id=record["id"], requester=player)
        player.msg(f"You request to join |c{record['name']}|n [{record['tag']}].")
        self._broadcast(record["id"], f"{player.key} has requested to join.")
        return True

    def accept_request(self, actor, target) -> bool:
        """Officer+ approves an inbound join request from *target*."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if not self._can(actor, ALLIANCE_RANK_OFFICER):
            actor.msg("Only officers and the leader can approve requests.")
            return False
        tid = getattr(target, "id", None)
        if tid not in (record.get("pending_requests", []) or []):
            actor.msg(f"{target.key} has not requested to join.")
            return False
        gate = self._join_gate(target, record)
        if gate is not None:
            actor.msg(f"Cannot admit {target.key}: {gate}")
            # Drop the stale request.
            reqs = [r for r in record.get("pending_requests", []) if r != tid]
            record["pending_requests"] = reqs
            self._alliances.put(record)
            return False
        self._admit(target, record, ALLIANCE_RANK_MEMBER)
        return True

    def set_open_join(self, actor, flag) -> bool:
        """Leader toggles the open-join flag."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can change open-join.")
            return False
        record["open_join"] = bool(flag)
        self._alliances.put(record)
        actor.msg(f"Open-join is now {'ON' if flag else 'OFF'}.")
        return True

    def join_open(self, player, ref) -> bool:
        """Join an open alliance by tag with no invite (still gated)."""
        if self._alliances is None:
            return False
        record = self._alliances.by_tag(ref)
        if record is None:
            player.msg("No alliance with that tag.")
            return False
        if not record.get("open_join", False):
            player.msg("That alliance is not accepting open joins — request an invite.")
            return False
        gate = self._join_gate(player, record)
        if gate is not None:
            player.msg(gate)
            return False
        self._admit(player, record, ALLIANCE_RANK_MEMBER)
        return True

    # ------------------------------------------------------------------ #
    #  Membership — leave / kick / disband / transfer / promote / demote
    # ------------------------------------------------------------------ #

    def _remove_from_roster(self, record, char_id) -> None:
        """Strip *char_id* from every roster list in *record* (not leader_id)."""
        record["officer_ids"] = [i for i in (record.get("officer_ids", []) or []) if i != char_id]
        record["member_ids"] = [i for i in (record.get("member_ids", []) or []) if i != char_id]

    def _start_rejoin_cooldown(self, player) -> None:
        """Stamp the rejoin cooldown deadline on *player* after leave/kick."""
        cd = int(self._bal.alliance_rejoin_cooldown_ticks)
        try:
            player.db.alliance_rejoin_until = self._now_tick() + cd
        except Exception:  # noqa: BLE001
            pass

    def leave(self, player) -> bool:
        """Leave the alliance. A sole-leader leave becomes a disband; a
        non-sole leader must transfer or disband first."""
        record = self._record_of(player)
        if record is None:
            player.msg("You are not in an alliance.")
            return False
        alliance_id = record["id"]
        if self._rank_of(player) == ALLIANCE_RANK_LEADER:
            if self._member_count(record) <= 1:
                return self.disband(player)
            player.msg("As leader you must transfer leadership or disband first.")
            return False
        self._remove_from_roster(record, getattr(player, "id", None))
        self._alliances.put(record)
        self._unsubscribe(player, alliance_id)
        self._clear_pointer(player)
        self._start_rejoin_cooldown(player)
        from world.event_bus import ALLIANCE_MEMBER_LEFT
        self._publish(ALLIANCE_MEMBER_LEFT, alliance_id=alliance_id, player=player)
        player.msg(f"You leave |c{record['name']}|n.")
        self._broadcast(alliance_id, f"{player.key} has left the alliance.")
        return True

    def kick(self, actor, target) -> bool:
        """Kick a STRICTLY-lower-ranked member (Officer+)."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if not self._can(actor, ALLIANCE_RANK_OFFICER):
            actor.msg("Only officers and the leader can kick.")
            return False
        if self._alliance_of(target) != record["id"]:
            actor.msg(f"{target.key} is not in your alliance.")
            return False
        actor_rank = ALLIANCE_RANK_ORDER.get(self._rank_of(actor), 0)
        target_rank = ALLIANCE_RANK_ORDER.get(self._rank_of(target), 0)
        if target_rank >= actor_rank:
            actor.msg("You can only kick members of lower rank.")
            return False
        alliance_id = record["id"]
        self._remove_from_roster(record, getattr(target, "id", None))
        self._alliances.put(record)
        self._unsubscribe(target, alliance_id)
        self._clear_pointer(target)
        self._start_rejoin_cooldown(target)
        from world.event_bus import ALLIANCE_MEMBER_LEFT
        self._publish(ALLIANCE_MEMBER_LEFT, alliance_id=alliance_id, player=target)
        actor.msg(f"You kick {target.key} from the alliance.")
        try:
            target.msg(f"You have been kicked from {record['name']}.")
        except Exception:  # noqa: BLE001
            pass
        self._broadcast(alliance_id, f"{target.key} has been kicked from the alliance.")
        return True

    def disband(self, actor) -> bool:
        """Disband the alliance (Leader only). Even-splits the treasury, clears
        every pointer, destroys the channel, deletes the record."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can disband the alliance.")
            return False
        self._do_disband(record)
        actor.msg(f"You disband |c{record['name']}|n.")
        return True

    def _do_disband(self, record) -> None:
        """Internal disband: even-split, clear pointers, destroy channel, delete.

        Used by ``disband`` (Leader), succession (no member resolves), and the
        admin force-disband — the single teardown path.
        """
        alliance_id = record["id"]
        name = record.get("name", "The alliance")
        # Announce the disband on the channel FIRST — before anyone is
        # unsubscribed and before the channel is destroyed, or the broadcast
        # reaches no one (an empty/deleted channel is a silent no-op).
        self._broadcast(alliance_id, f"{name} has been disbanded.")
        # Even-split the treasury across the current live roster.
        self._even_split_treasury(record)
        # Clear every member's pointer + unsubscribe, and DM each one so the
        # dissolution is acknowledged even after they leave the channel.
        for cid in _roster_ids(record):
            member = self._resolve_member(cid)
            if member is not None and self._alliance_of(member) == alliance_id:
                try:
                    member.msg(f"{name} has been disbanded.")
                except Exception:  # noqa: BLE001
                    pass
                self._unsubscribe(member, alliance_id)
                self._clear_pointer(member)
        self._destroy_channel(alliance_id)
        self._alliances.delete(alliance_id)
        from world.event_bus import ALLIANCE_DISBANDED
        self._publish(ALLIANCE_DISBANDED, alliance_id=alliance_id)

    def transfer(self, actor, target) -> bool:
        """Transfer leadership to a member (Leader only). Demotes actor to Officer."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can transfer leadership.")
            return False
        if self._alliance_of(target) != record["id"]:
            actor.msg(f"{target.key} is not in your alliance.")
            return False
        if getattr(target, "id", None) == getattr(actor, "id", None):
            actor.msg("You are already the leader.")
            return False
        self._install_leader(record, actor, target)
        actor.msg(f"You transfer leadership to {target.key}.")
        return True

    def _install_leader(self, record, old_leader, new_leader) -> None:
        """Make *new_leader* the Leader and demote *old_leader* to Officer.

        Rebuilds roster lists accordingly and updates leader_id. *old_leader* may
        be ``None`` (succession/claim on an absent leader).
        """
        alliance_id = record["id"]
        new_id = getattr(new_leader, "id", None)
        # Pull the new leader out of officer/member lists.
        self._remove_from_roster(record, new_id)
        # Demote the old leader into officers (if still present).
        if old_leader is not None:
            old_id = getattr(old_leader, "id", None)
            officers = list(record.get("officer_ids", []) or [])
            if old_id is not None and old_id not in officers:
                officers.append(old_id)
            record["officer_ids"] = officers
            self._set_pointer(old_leader, alliance_id, ALLIANCE_RANK_OFFICER)
        record["leader_id"] = new_id
        self._alliances.put(record)
        self._set_pointer(new_leader, alliance_id, ALLIANCE_RANK_LEADER)
        from world.event_bus import ALLIANCE_RANK_CHANGED
        self._publish(ALLIANCE_RANK_CHANGED, alliance_id=alliance_id,
                      member=new_leader, new_rank=ALLIANCE_RANK_LEADER)
        self._broadcast(alliance_id, f"{new_leader.key} is now the leader.")

    def promote(self, actor, target) -> bool:
        """Promote a Member to Officer (Leader only), respecting the officer cap."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can promote.")
            return False
        if self._alliance_of(target) != record["id"] or self._rank_of(target) != ALLIANCE_RANK_MEMBER:
            actor.msg("You can only promote a member of your alliance.")
            return False
        if len(record.get("officer_ids", []) or []) >= int(self._bal.alliance_max_officers):
            actor.msg("You already have the maximum number of officers.")
            return False
        tid = getattr(target, "id", None)
        record["member_ids"] = [i for i in (record.get("member_ids", []) or []) if i != tid]
        officers = list(record.get("officer_ids", []) or [])
        officers.append(tid)
        record["officer_ids"] = officers
        self._alliances.put(record)
        self._set_pointer(target, record["id"], ALLIANCE_RANK_OFFICER)
        from world.event_bus import ALLIANCE_RANK_CHANGED
        self._publish(ALLIANCE_RANK_CHANGED, alliance_id=record["id"],
                      member=target, new_rank=ALLIANCE_RANK_OFFICER)
        actor.msg(f"You promote {target.key} to officer.")
        self._broadcast(record["id"], f"{target.key} has been promoted to officer.")
        return True

    def demote(self, actor, target) -> bool:
        """Demote an Officer to Member (Leader only)."""
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can demote.")
            return False
        if self._alliance_of(target) != record["id"] or self._rank_of(target) != ALLIANCE_RANK_OFFICER:
            actor.msg("You can only demote an officer of your alliance.")
            return False
        tid = getattr(target, "id", None)
        record["officer_ids"] = [i for i in (record.get("officer_ids", []) or []) if i != tid]
        members = list(record.get("member_ids", []) or [])
        members.append(tid)
        record["member_ids"] = members
        self._alliances.put(record)
        self._set_pointer(target, record["id"], ALLIANCE_RANK_MEMBER)
        from world.event_bus import ALLIANCE_RANK_CHANGED
        self._publish(ALLIANCE_RANK_CHANGED, alliance_id=record["id"],
                      member=target, new_rank=ALLIANCE_RANK_MEMBER)
        actor.msg(f"You demote {target.key} to member.")
        self._broadcast(record["id"], f"{target.key} has been demoted to member.")
        return True

    def claim(self, actor) -> bool:
        """Officer claims leadership of an alliance whose Leader is long absent.

        Judged on-demand from the Leader's last-seen data (no timer): if the
        Leader has been offline longer than ``alliance_leader_absence_days``, the
        claiming Officer becomes Leader.
        """
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_OFFICER:
            actor.msg("Only an officer can claim leadership.")
            return False
        leader = self._resolve_member(record.get("leader_id"))
        if leader is not None and not self._leader_absent(leader):
            actor.msg("The leader is not absent long enough to claim leadership.")
            return False
        # Absent or unresolvable leader -> install the claiming officer.
        self._install_leader(record, None, actor)
        actor.msg("You claim leadership of the alliance.")
        return True

    def _leader_absent(self, leader) -> bool:
        """Return True if *leader* has been offline past the absence threshold.

        An unresolvable leader (``None``) is absent. A currently-connected leader
        is present. Otherwise the leader is absent iff their last-seen wall-clock
        stamp (``db.last_seen_time``, epoch seconds, set on disconnect by
        ``Character.at_post_unpuppet``) is older than
        ``alliance_leader_absence_days``. This uses a real LAST-SEEN time, NOT
        ``account.last_login`` (which is set only at connect and never updated,
        so an actively-played 8-day session would read as absent). A leader with
        no recorded last-seen (never disconnected via our hook) reads present —
        we do not coup an online-or-unknown leader on missing data.
        """
        if leader is None:
            return True
        days = int(self._bal.alliance_leader_absence_days)
        try:
            # A connected account means present.
            sessions = getattr(leader, "sessions", None)
            if sessions is not None and sessions.count() > 0:
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            import time as _t
            last_seen = getattr(getattr(leader, "db", None), "last_seen_time", None)
            if last_seen is None:
                # No recorded last-seen (offline but never stamped) — do not
                # treat an unknown leader as absent; require real evidence.
                return False
            elapsed = _t.time() - float(last_seen)
            return elapsed > days * _SECONDS_PER_DAY
        except Exception:  # noqa: BLE001
            return False

    def rename(self, actor, new_name) -> bool:
        """Rename the alliance (Leader only), re-running validators + cooldown."""
        return self._rename_or_retag(actor, new_name=new_name)

    def retag(self, actor, new_tag) -> bool:
        """Change the alliance tag (Leader only)."""
        return self._rename_or_retag(actor, new_tag=new_tag)

    def _rename_or_retag(self, actor, new_name=None, new_tag=None) -> bool:
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can rename the alliance.")
            return False
        cd = int(self._bal.alliance_rename_cooldown_ticks)
        if self._now_tick() - int(record.get("renamed_tick", 0)) < cd:
            actor.msg("You renamed the alliance too recently — wait before renaming again.")
            return False
        candidate_name = new_name if new_name is not None else record["name"]
        candidate_tag = new_tag if new_tag is not None else record["tag"]
        err = self._validate_name_tag(candidate_name, candidate_tag, exclude_id=record["id"])
        if err:
            actor.msg(err)
            return False
        old = (record["name"], record["tag"])
        record["name"] = candidate_name.strip()
        record["tag"] = candidate_tag.strip()
        record["renamed_tick"] = self._now_tick()
        self._alliances.put(record)
        from world.event_bus import ALLIANCE_RENAMED
        self._publish(ALLIANCE_RENAMED, alliance_id=record["id"], old=old,
                      new=(record["name"], record["tag"]))
        actor.msg(f"Alliance is now |c{record['name']}|n [{record['tag']}].")
        self._broadcast(record["id"], f"The alliance is now {record['name']} [{record['tag']}].")
        return True

    def ignore(self, player, target_or_all) -> bool:
        """Add an inviter (or 'all') to the player's invite Ignore_List."""
        if target_or_all == ALLIANCE_IGNORE_ALL or (
            isinstance(target_or_all, str) and target_or_all.lower() == "all"
        ):
            player.db.alliance_invite_ignore = ALLIANCE_IGNORE_ALL
            player.msg("You will no longer receive any alliance invitations.")
            return True
        tid = getattr(target_or_all, "id", None)
        if tid is None:
            player.msg("No such player to ignore.")
            return False
        current = getattr(player.db, "alliance_invite_ignore", None)
        if current == ALLIANCE_IGNORE_ALL:
            player.msg("You are already ignoring all invitations.")
            return True
        blocked = set(current) if current else set()
        blocked.add(tid)
        player.db.alliance_invite_ignore = blocked
        player.msg(f"You will no longer receive invitations from {getattr(target_or_all, 'key', 'them')}.")
        return True

    def on_character_deleted(self, player) -> None:
        """Route a character deletion (chardelete) through the single writer.

        A deleted member must not leave an orphaned pointer/roster entry. Treated
        as an implicit leave; if the deleted character was the Leader, succession
        runs on the next reconcile.
        """
        record = self._record_of(player)
        if record is None:
            return
        alliance_id = record["id"]
        pid = getattr(player, "id", None)
        was_leader = self._rank_of(player) == ALLIANCE_RANK_LEADER
        # Strip from the roster AND clear the member pointer BEFORE reconcile.
        # on_character_deleted runs from at_object_delete, i.e. while the row is
        # still ORM-resolvable — so if we reconcile before clearing the pointer,
        # _live_members still resolves the dying leader (pointer + rank intact)
        # and succession is skipped, re-stamping leader_id onto a dead PK. Clear
        # both first so reconcile sees the leader as truly absent.
        self._remove_from_roster(record, pid)
        if was_leader:
            # _remove_from_roster never touches leader_id by design; null it here
            # so reconcile's "no leader resolves" branch promotes an heir.
            record["leader_id"] = None
        self._alliances.put(record)
        try:
            self._clear_pointer(player)
        except Exception:  # noqa: BLE001
            pass
        if was_leader:
            self.reconcile(alliance_id)

    # ------------------------------------------------------------------ #
    #  Shared treasury (R7)
    # ------------------------------------------------------------------ #

    def deposit(self, player, costs: dict) -> bool:
        """Deposit resources from *player* into the alliance treasury.

        Ordered write with in-call rollback (R7.1): add to the treasury FIRST
        (read-modify-reassign, with a pre-write-back re-read per C9), THEN deduct
        from the member. If the member deduction fails, roll the treasury add
        back so no resources are created. Any member may deposit.
        """
        record = self._record_of(player)
        if record is None:
            player.msg("You are not in an alliance.")
            return False
        costs = {k.title(): int(v) for k, v in (costs or {}).items() if int(v) > 0}
        if not costs:
            player.msg("Deposit what?")
            return False
        if not player.has_resources(costs):
            from world.utils import format_insufficient_resources
            player.msg(format_insufficient_resources(player, costs))
            return False
        # Pre-write-back re-read (C9): fetch the freshest record right before we
        # compute the new treasury, so a concurrent write isn't clobbered.
        record = self._record(record["id"])
        treasury = dict(record.get("treasury", {}) or {})
        for res, amt in costs.items():
            treasury[res] = treasury.get(res, 0) + amt
        record["treasury"] = treasury
        self._alliances.put(record)
        if not player.deduct_resources(costs):
            # Roll back the treasury add — the member could not pay.
            record = self._record(record["id"])
            treasury = dict(record.get("treasury", {}) or {})
            for res, amt in costs.items():
                treasury[res] = treasury.get(res, 0) - amt
            record["treasury"] = treasury
            self._alliances.put(record)
            player.msg("Deposit failed.")
            return False
        from world.event_bus import ALLIANCE_TREASURY_DEPOSITED
        self._publish(ALLIANCE_TREASURY_DEPOSITED, alliance_id=record["id"],
                      actor=player, amounts=dict(costs))
        summary = ", ".join(f"{a} {r}" for r, a in costs.items())
        player.msg(f"You deposit {summary} into the treasury.")
        self._broadcast(record["id"], f"{player.key} deposited {summary}.")
        return True

    def withdraw(self, actor, costs: dict) -> bool:
        """Withdraw resources from the treasury (Officer+), capped per window.

        An Officer's cumulative withdrawal per resource within a rolling window
        may not exceed ``alliance_withdraw_cap_per_window``; a Leader withdraw
        bypasses the cap. Ordered write with re-read + rollback (never negative).
        """
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if not self._can(actor, ALLIANCE_RANK_OFFICER):
            actor.msg("Only officers and the leader can withdraw.")
            return False
        costs = {k.title(): int(v) for k, v in (costs or {}).items() if int(v) > 0}
        if not costs:
            actor.msg("Withdraw what?")
            return False
        record = self._record(record["id"])  # freshest
        treasury = dict(record.get("treasury", {}) or {})
        # Never-negative check (atomic refusal).
        for res, amt in costs.items():
            if treasury.get(res, 0) < amt:
                actor.msg(f"The treasury does not have {amt} {res}.")
                return False
        # Per-window cap (Officers only; Leader bypasses).
        is_leader = self._rank_of(actor) == ALLIANCE_RANK_LEADER
        window = self._current_withdraw_window(record)
        if not is_leader:
            cap = int(self._bal.alliance_withdraw_cap_per_window)
            withdrawn = dict(window.get("withdrawn", {}) or {})
            for res, amt in costs.items():
                if withdrawn.get(res, 0) + amt > cap:
                    remaining = max(0, cap - withdrawn.get(res, 0))
                    actor.msg(
                        f"Withdrawal cap reached for {res} this period "
                        f"({remaining} remaining). Ask the leader."
                    )
                    return False
        # Subtract from treasury FIRST.
        for res, amt in costs.items():
            treasury[res] = treasury.get(res, 0) - amt
        record["treasury"] = treasury
        # Record against the window accumulator (Officers).
        if not is_leader:
            withdrawn = dict(window.get("withdrawn", {}) or {})
            for res, amt in costs.items():
                withdrawn[res] = withdrawn.get(res, 0) + amt
            window["withdrawn"] = withdrawn
            record["withdraw_window"] = window
        self._alliances.put(record)
        # Credit the withdrawer.
        for res, amt in costs.items():
            actor.add_resource(res, amt)
        from world.event_bus import ALLIANCE_TREASURY_WITHDRAWN
        self._publish(ALLIANCE_TREASURY_WITHDRAWN, alliance_id=record["id"],
                      actor=actor, amounts=dict(costs))
        summary = ", ".join(f"{a} {r}" for r, a in costs.items())
        actor.msg(f"You withdraw {summary} from the treasury.")
        self._broadcast(record["id"], f"{actor.key} withdrew {summary}.")
        return True

    def _current_withdraw_window(self, record) -> dict:
        """Return the live withdraw-window accumulator, resetting if elapsed."""
        window = dict(record.get("withdraw_window", {}) or {})
        length = int(self._bal.alliance_withdraw_window_ticks)
        now = self._now_tick()
        start = window.get("window_start_tick")
        if start is None or now - start >= length:
            window = {"window_start_tick": now, "withdrawn": {}}
        return window

    def _even_split_treasury(self, record) -> None:
        """Even-split the treasury across the current roster; remainder to Leader.

        Called on disband. Credits each resolved member an equal integer share of
        each resource; the non-even remainder goes to the Leader. Total credited
        equals the pre-split treasury (no dupe, no loss). Best-effort per member.
        """
        treasury = dict(record.get("treasury", {}) or {})
        if not treasury:
            return
        members = [self._resolve_member(cid) for cid in _roster_ids(record)]
        members = [m for m in members if m is not None]
        leader = self._resolve_member(record.get("leader_id"))
        if not members:
            # No one to credit — nothing to do (resources dissolve).
            record["treasury"] = {}
            return
        n = len(members)
        for res, total in treasury.items():
            share = total // n
            remainder = total - share * n
            for m in members:
                if share > 0:
                    try:
                        m.add_resource(res, share)
                    except Exception:  # noqa: BLE001
                        pass
            if remainder > 0 and leader is not None:
                try:
                    leader.add_resource(res, remainder)
                except Exception:  # noqa: BLE001
                    pass
            elif remainder > 0:
                # No resolvable leader — give the remainder to the first member.
                try:
                    members[0].add_resource(res, remainder)
                except Exception:  # noqa: BLE001
                    pass
        record["treasury"] = {}

    # ------------------------------------------------------------------ #
    #  Reconciliation + succession (R14.5, R4.7)
    # ------------------------------------------------------------------ #

    def reconcile(self, alliance_id=None) -> None:
        """Rebuild roster + leader_id from Member_Pointers; run succession.

        On-load and on-demand only (no timer). The Member_Pointer is
        authoritative: the roster and ``leader_id`` are reconstructed from the
        per-character ``db.alliance_rank`` of the members that still point at this
        alliance. If no member resolves to Leader, succession promotes the senior
        remaining member; if no member resolves at all, the alliance is even-split
        and disbanded.
        """
        if self._alliances is None:
            return
        ids = [alliance_id] if alliance_id is not None else [
            rec["id"] for rec in self._alliances.all_alliances()
        ]
        for aid in ids:
            record = self._record(aid)
            if record is None:
                continue
            members = self._live_members(aid)
            if not members:
                # Nobody left — even-split (no-op, no members) and disband.
                self._do_disband(record)
                continue
            leaders = [m for m in members if self._rank_of(m) == ALLIANCE_RANK_LEADER]
            officers = [m for m in members if self._rank_of(m) == ALLIANCE_RANK_OFFICER]
            plain = [m for m in members if self._rank_of(m) == ALLIANCE_RANK_MEMBER]

            if not leaders:
                # Succession: promote the senior remaining member (an officer,
                # else the earliest-joined plain member).
                heir = officers[0] if officers else (plain[0] if plain else members[0])
                self._set_pointer(heir, aid, ALLIANCE_RANK_LEADER)
                record["leader_id"] = getattr(heir, "id", None)
                leaders = [heir]
                officers = [o for o in officers if o is not heir]
                plain = [p for p in plain if p is not heir]
                from world.event_bus import ALLIANCE_RANK_CHANGED
                self._publish(ALLIANCE_RANK_CHANGED, alliance_id=aid,
                              member=heir, new_rank=ALLIANCE_RANK_LEADER)
            elif len(leaders) > 1:
                # Keep the one matching leader_id (else the first); demote rest.
                keep = next((m for m in leaders
                             if getattr(m, "id", None) == record.get("leader_id")),
                            leaders[0])
                for m in leaders:
                    if m is not keep:
                        self._set_pointer(m, aid, ALLIANCE_RANK_OFFICER)
                        officers.append(m)
                leaders = [keep]
                record["leader_id"] = getattr(keep, "id", None)

            record["leader_id"] = getattr(leaders[0], "id", None)
            record["officer_ids"] = [getattr(o, "id", None) for o in officers]
            record["member_ids"] = [getattr(p, "id", None) for p in plain]
            self._alliances.put(record)

    # ------------------------------------------------------------------ #
    #  Perks — unlock + activation (R9), one per category (C2)
    # ------------------------------------------------------------------ #

    def _perk_spec(self, perk_key) -> dict | None:
        """Return the catalog spec for *perk_key*, or None."""
        try:
            return self.registry.get_alliance_perk(perk_key)
        except Exception:  # noqa: BLE001
            return None

    def available_perks(self, alliance_id) -> list[dict]:
        """Return catalog perks with their unlock/activation status for the UI.

        Each entry: ``{key, category, current_level, next_level, unlocked,
        affordable, next_cost}``. ``unlocked`` reflects the level gate at the
        alliance's current level; ``affordable`` the treasury.
        """
        record = self._record(alliance_id)
        if record is None or not getattr(self.registry, "alliance_perks", None):
            return []
        level = self.compute_alliance_level(alliance_id)
        active = dict(record.get("active_perks", {}) or {})
        treasury = dict(record.get("treasury", {}) or {})
        out = []
        for key, spec in self.registry.alliance_perks.items():
            cur = active.get(key, 0)
            nxt = cur + 1
            levels = spec.get("levels", {})
            payload = levels.get(nxt)
            unlocked = payload is not None and level >= int(payload.get("tier", 99))
            cost = dict(payload.get("cost", {})) if payload else {}
            affordable = all(treasury.get(r.title(), 0) >= a for r, a in cost.items())
            out.append({
                "key": key,
                "category": spec.get("category", key),
                "current_level": cur,
                "next_level": nxt if payload else None,
                "unlocked": unlocked,
                "affordable": affordable if payload else False,
                "next_cost": cost,
            })
        return out

    def activate_perk(self, actor, perk_key) -> bool:
        """Activate or upgrade a perk (Leader only). Both gates apply.

        Level gate: the alliance level must meet the next level's ``tier``.
        Treasury gate: the treasury must pay the next level's ``cost``. Enforces
        one perk per category (an occupied category only permits upgrading the
        already-active perk).
        """
        record = self._record_of(actor)
        if record is None:
            actor.msg("You are not in an alliance.")
            return False
        if self._rank_of(actor) != ALLIANCE_RANK_LEADER:
            actor.msg("Only the leader can activate perks.")
            return False
        spec = self._perk_spec(perk_key)
        if spec is None:
            actor.msg("No such perk.")
            return False
        category = spec.get("category", perk_key)
        active = dict(record.get("active_perks", {}) or {})
        # One-per-category: another perk in this category blocks activation.
        for other_key, lvl in active.items():
            if other_key == perk_key:
                continue
            other_spec = self._perk_spec(other_key)
            if other_spec and other_spec.get("category") == category:
                actor.msg(f"Another {category} perk is already active.")
                return False
        cur = active.get(perk_key, 0)
        nxt = cur + 1
        payload = spec.get("levels", {}).get(nxt)
        if payload is None:
            actor.msg("That perk is already at its maximum level.")
            return False
        level = self.compute_alliance_level(record["id"])
        if level < int(payload.get("tier", 99)):
            actor.msg(f"Your alliance must be level {payload.get('tier')} to unlock that.")
            return False
        cost = {r.title(): int(a) for r, a in (payload.get("cost", {}) or {}).items()}
        record = self._record(record["id"])  # freshest
        treasury = dict(record.get("treasury", {}) or {})
        for res, amt in cost.items():
            if treasury.get(res, 0) < amt:
                actor.msg(f"The treasury cannot afford it (need {amt} {res}).")
                return False
        for res, amt in cost.items():
            treasury[res] = treasury.get(res, 0) - amt
        record["treasury"] = treasury
        active = dict(record.get("active_perks", {}) or {})
        active[perk_key] = nxt
        record["active_perks"] = active
        self._alliances.put(record)
        from world.event_bus import ALLIANCE_PERK_ACTIVATED
        self._publish(ALLIANCE_PERK_ACTIVATED, alliance_id=record["id"],
                      perk_key=perk_key, level=nxt)
        actor.msg(f"You activate |c{perk_key}|n (level {nxt}).")
        self._broadcast(record["id"], f"Alliance perk {perk_key} is now level {nxt}.")
        return True

    # ------------------------------------------------------------------ #
    #  Perk effect lookups (membership-derived; read live each evaluation)
    # ------------------------------------------------------------------ #

    def _active_perk_level(self, player, category) -> tuple[str | None, int, dict | None]:
        """Return (perk_key, level, level_payload) for *player*'s active perk in
        *category*, or ``(None, 0, None)`` if none / not a member."""
        record = self._record_of(player)
        if record is None:
            return (None, 0, None)
        active = record.get("active_perks", {}) or {}
        for key, lvl in active.items():
            spec = self._perk_spec(key)
            if spec and spec.get("category") == category:
                payload = spec.get("levels", {}).get(int(lvl))
                return (key, int(lvl), payload)
        return (None, 0, None)

    def perk_multiplier(self, player, category) -> float:
        """Return the active MULTIPLIER for *player* in *category* (1.0 if none).

        For ``shared_regen`` / ``harvest_boost`` (effect_type multiplier). A
        non-member, or a category with no active perk, yields ``1.0``.
        """
        _, _, payload = self._active_perk_level(player, category)
        if payload is None:
            return 1.0
        try:
            return float(payload.get("multiplier", 1.0))
        except (TypeError, ValueError):
            return 1.0

    def perk_flat_bonus(self, player, category, field) -> int:
        """Return the active FLAT additive bonus for *player* in *category*.

        For ``combat_damage`` (field ``damage_bonus``) / ``combat_armor`` (field
        ``damage_reduction``). ``0`` if the player is not a member or the perk is
        not active.
        """
        _, _, payload = self._active_perk_level(player, category)
        if payload is None:
            return 0
        try:
            return int(payload.get(field, 0))
        except (TypeError, ValueError):
            return 0

    def has_shared_vision(self, player) -> bool:
        """Return True if *player*'s alliance has the shared_vision perk active."""
        key, lvl, _ = self._active_perk_level(player, "shared_vision")
        return key is not None and lvl > 0

    # ------------------------------------------------------------------ #
    #  Shared vision helper (used by ALL THREE get_visible_tiles callers)
    # ------------------------------------------------------------------ #

    def shared_visible_tiles(self, member, member_buildings, fog_system,
                             building_lookup=None) -> set:
        """Union *member*'s own visible tiles with each PLAYING ally's.

        Only allies who are (a) ``player_state == PLAYING`` AND (b) on the SAME
        planet as *member* contribute. The PLAYING filter closes the offline
        phantom-vision leak; the same-planet filter is essential because
        ``get_visible_tiles`` returns bare ``(x, y)`` tuples with NO planet
        dimension, and planets have overlapping numeric coordinate ranges — so
        unioning a cross-planet ally's circle would render (and permanently
        discover) wrong-planet coordinates on the member's viewport.
        ``building_lookup(ally)`` supplies an ally's own buildings for their
        building-vision circles; when omitted, allies contribute only their
        position circle. Returns the member's own tiles unchanged when the perk
        is inactive.
        """
        own = set(fog_system.get_visible_tiles(member, member_buildings) or [])
        if not self.has_shared_vision(member):
            return own
        member_planet = getattr(getattr(member, "db", None), "coord_planet", None)
        alliance_id = self._alliance_of(member)
        for ally in self._live_members(alliance_id):
            if getattr(ally, "id", None) == getattr(member, "id", None):
                continue
            ally_db = getattr(ally, "db", None)
            state = getattr(ally_db, "player_state", None)
            if state != PLAYER_STATE_PLAYING:
                continue
            # Same-planet only — (x, y) tuples carry no planet, and planets share
            # numeric ranges, so a cross-planet ally would leak/pollute vision.
            if getattr(ally_db, "coord_planet", None) != member_planet:
                continue
            ally_buildings = []
            if building_lookup is not None:
                try:
                    ally_buildings = building_lookup(ally) or []
                except Exception:  # noqa: BLE001
                    ally_buildings = []
            try:
                own |= set(fog_system.get_visible_tiles(ally, ally_buildings) or [])
            except Exception:  # noqa: BLE001
                continue
        return own

    def is_allied_building_owner(self, viewer, owner) -> bool:
        """Return True if *owner* is an ally of *viewer* (for fog enemy-flagging).

        A wrapper over ``are_allied`` so ``update_discovery`` can suppress
        enemy-flagging of an ally's building without importing the predicate at
        each of its three call sites.
        """
        from world.utils import are_allied
        return are_allied(viewer, owner)

    # ------------------------------------------------------------------ #
    #  Leaderboard + member board (R13)
    # ------------------------------------------------------------------ #

    def _decayed_kills(self, member) -> tuple[float, float]:
        """Return *member*'s (pvp, pve) kill tallies decayed to the current tick.

        Lazy exponential decay: multiply each stored tally by
        ``decay_factor ** (elapsed_ticks / decay_interval_ticks)``. Reads only
        (does not persist) — the persisted decay happens on increment in the
        combat path; here we compute the value AS OF now for scoring.
        """
        db = getattr(member, "db", None)
        if db is None:
            return (0.0, 0.0)
        pvp = getattr(db, "scored_kills_pvp", 0.0) or 0.0
        pve = getattr(db, "scored_kills_pve", 0.0) or 0.0
        factor = self._decay_multiplier(getattr(db, "last_kill_decay_tick", 0) or 0)
        try:
            return (float(pvp) * factor, float(pve) * factor)
        except (TypeError, ValueError):
            return (0.0, 0.0)

    def _decay_multiplier(self, last_tick) -> float:
        """Return ``decay_factor ** (elapsed / interval)`` for lazy decay.

        Guards a None/non-numeric ``last_tick`` (→ no decay) and clamps a huge
        elapsed span so ``factor ** n`` never underflows into a crash.
        """
        factor = float(self._bal.alliance_score_decay_factor)
        interval = max(1, int(self._bal.alliance_score_decay_interval_ticks))
        if factor >= 1.0:
            return 1.0
        try:
            elapsed = max(0, self._now_tick() - int(last_tick))
        except (TypeError, ValueError):
            return 1.0
        n = min(elapsed / interval, 10000)  # clamp
        try:
            return factor ** n
        except (OverflowError, ValueError):
            return 0.0

    def alliance_score(self, alliance_id) -> float:
        """Return the composite Alliance_Score over the live-pointer roster.

        Per member: ``level*w_level + decayed_pvp*w_kills_pvp +
        decayed_pve*w_kills_pve + buildings*w_buildings``. An unresolved/unreadable
        member contributes zero rather than raising.
        """
        from world.utils import get_player_level

        b = self._bal
        total = 0.0
        for member in self._live_members(alliance_id):
            try:
                pvp, pve = self._decayed_kills(member)
                buildings = len(member.get_buildings()) if hasattr(member, "get_buildings") else 0
                total += (
                    get_player_level(member) * b.alliance_score_w_level
                    + pvp * b.alliance_score_w_kills_pvp
                    + pve * b.alliance_score_w_kills_pve
                    + buildings * b.alliance_score_w_buildings
                )
            except Exception:  # noqa: BLE001 - a bad member scores zero
                continue
        return total

    def leaderboard(self, top_n=None) -> list[tuple[int, float]]:
        """Return ``(alliance_id, score)`` descending, deterministic, top-N.

        Ties break by ascending alliance_id, so identical state always yields the
        same ordering. Truncated to ``alliance_leaderboard_top_n`` (or *top_n*).
        """
        if self._alliances is None:
            return []
        rows = [(rec["id"], self.alliance_score(rec["id"]))
                for rec in self._alliances.all_alliances()]
        rows.sort(key=lambda r: (-r[1], r[0]))
        limit = top_n if top_n is not None else int(self._bal.alliance_leaderboard_top_n)
        return rows[:limit]

    def member_board(self, alliance_id) -> list[dict]:
        """Return per-member rows for the within-alliance board.

        Each row: ``{name, rank, level, scored_kills, online}``. Reuses
        ``RankSystem.get_status`` for the rank-name basis where available;
        scored_kills is the decayed pvp+pve sum. Sorted by descending per-member
        score contribution.
        """
        from world.utils import get_player_level

        b = self._bal
        rows = []
        for member in self._live_members(alliance_id):
            pvp, pve = self._decayed_kills(member)
            level = get_player_level(member)
            buildings = len(member.get_buildings()) if hasattr(member, "get_buildings") else 0
            score = (
                level * b.alliance_score_w_level
                + pvp * b.alliance_score_w_kills_pvp
                + pve * b.alliance_score_w_kills_pve
                + buildings * b.alliance_score_w_buildings
            )
            online = False
            try:
                online = bool(getattr(member, "has_account", False)) and (
                    member.sessions.count() > 0 if hasattr(member, "sessions") else False
                )
            except Exception:  # noqa: BLE001
                online = False
            rows.append({
                "name": getattr(member, "key", "?"),
                "rank": self._rank_of(member) or ALLIANCE_RANK_MEMBER,
                "level": level,
                "scored_kills": round(pvp + pve, 1),
                "online": online,
                "_score": score,
            })
        rows.sort(key=lambda r: -r["_score"])
        for r in rows:
            r.pop("_score", None)
        return rows

    # ------------------------------------------------------------------ #
    #  Tag visibility helper (R20)
    # ------------------------------------------------------------------ #

    def tag_for(self, player) -> str | None:
        """Return *player*'s alliance tag (for the ``[TAG] Name`` render), or None."""
        record = self._record_of(player)
        if record is None:
            return None
        return record.get("tag")

    # ------------------------------------------------------------------ #
    #  Read-only presentation helpers for the command layer
    # ------------------------------------------------------------------ #

    def alliance_summary(self, alliance_id, *, for_member=False) -> dict | None:
        """Return an info dict for the ``info`` view (member vs outsider scope).

        Members see the treasury; outsiders do not. Pending invites/requests are
        included only for a member (the command layer further gates them to
        Officer+). Returns ``None`` if the alliance does not exist.
        """
        record = self._record(alliance_id)
        if record is None:
            return None
        leader = self._resolve_member(record.get("leader_id"))
        summary = {
            "name": record.get("name"),
            "tag": record.get("tag"),
            "leader": getattr(leader, "key", "?") if leader else "?",
            "member_count": self._member_count(record),
            "level": self.compute_alliance_level(alliance_id),
            "active_perks": dict(record.get("active_perks", {}) or {}),
            "open_join": bool(record.get("open_join", False)),
        }
        if for_member:
            summary["treasury"] = dict(record.get("treasury", {}) or {})
            summary["pending_invites"] = list(record.get("pending_invites", []) or [])
            summary["pending_requests"] = list(record.get("pending_requests", []) or [])
        return summary
