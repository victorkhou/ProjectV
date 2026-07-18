"""
Alliance commands — the player-facing ``alliance`` verb router.

One command, many verbs, all routing through the single-writer
``AllianceSystem`` (never touching records directly). Two gate layers beyond the
router's own dispatch:

* **Verb-aware lobby gate** (``at_pre_cmd``): a single class-level
  ``available_out_of_game`` flag can't express per-verb availability, so this
  parses the verb FIRST. ``MUTATING_LOBBY_VERBS`` are usable only from the LOBBY
  (refused in SPAWNING — "finish choosing your character first"); the read-only
  trio is usable from LOBBY or SPAWNING; every other verb applies the normal
  in-game gate.
* **Combat gate**: the friend/foe-changing verbs (leave/transfer/disband/kick)
  are refused while the actor is in combat (the same ``player_in_combat`` check
  that gates ``quit`` — anti-combat-log). ``deposit``/``withdraw`` are NOT gated.
"""

from __future__ import annotations

from commands.command_router import GameSubcommandRouter
from world.utils import get_system


# Verbs usable while OOC in the LOBBY (they mutate state, so NOT in SPAWNING).
MUTATING_LOBBY_VERBS = frozenset({
    "found", "invite", "accept", "decline", "apply", "request", "join",
})
# Verbs usable from either OOC state (LOBBY or SPAWNING) — read-only.
READONLY_OOC_VERBS = frozenset({"info", "board", "leaderboard", "invites"})
# Verbs refused while the actor is in combat (anti-combat-log). Covers BOTH
# directions of a mid-fight side-change: membership-REMOVING verbs (leave/kick/
# disband/transfer) that drop you out of a side, AND membership-ADDING verbs
# (accept/join) that flip you allied to whoever is shooting you — becoming an
# ally silences their turrets/guards (are_allied skip), which is the same
# combat-log escape in the fire-suppressing direction.
COMBAT_GATED_VERBS = frozenset({
    "leave", "transfer", "disband", "kick", "accept", "join",
})


def _resolve_player(caller, name):
    """Resolve a player character by name near the caller, or msg + return None.

    Uses Evennia's search (which reports its own not-found/multi-match message)
    when available; falls back to a simple contents scan for test doubles.
    """
    if not name:
        caller.msg("Specify a player by name.")
        return None
    if hasattr(caller, "search"):
        target = caller.search(name, global_search=True)
        return target  # search() already messaged on miss/ambiguity
    return None


class CmdAlliance(GameSubcommandRouter):
    """Form and run an alliance with other players.

    Usage:
        alliance <subcommand> [args]

    Membership:
        found <name> = <tag>     — found a new alliance (leader)
        invite <player>          — invite a player (officer+)
        invites                  — list your pending invitations
        accept <tag>             — accept an invitation
        decline <tag>            — decline an invitation
        apply <tag>              — request to join an alliance
        join <tag>               — join an open alliance (no invite)
        open on|off              — toggle open-join (leader)
        leave                    — leave your alliance
        kick <player>            — remove a lower-ranked member (officer+)
        promote <player>         — promote a member to officer (leader)
        demote <player>          — demote an officer (leader)
        transfer <player>        — hand leadership to a member (leader)
        claim                    — claim leadership of an absent-leader alliance (officer)
        disband                  — disband the alliance (leader)
        rename <name>            — rename the alliance (leader)
        retag <tag>              — change the alliance tag (leader)
        ignore <player>|all      — block invitations

    Economy & perks:
        deposit <amt> <res> [...]  — deposit resources into the treasury
        withdraw <amt> <res> [...] — withdraw from the treasury (officer+)
        perks                      — list perks and their unlock/cost status
        activate <perk>            — activate/upgrade a perk (leader)

    Information:
        info [<name|tag>]        — show your alliance (or another by tag)
        board                    — your alliance's member ranking
        leaderboard              — the cross-alliance leaderboard
        chat <message>           — send to your alliance channel
    """

    key = "alliance"
    aliases = ["ally"]
    help_category = "Game"
    # The router itself is not blanket-OOC; the verb-aware at_pre_cmd decides.
    available_out_of_game = True

    # ------------------------------------------------------------------ #
    #  Verb-aware lobby + combat gates
    # ------------------------------------------------------------------ #

    def at_pre_cmd(self):
        """Gate by parsed verb: combat lock + OOC availability. Abort => True.

        The combat (anti-combat-log) gate is evaluated INDEPENDENTLY of the lobby
        flow flag — it is a security control, not a lobby concern, and must not
        silently disappear if LOBBY_FLOW_ENABLED is flipped off (its documented
        one-line-revert purpose). Only the OOC lobby-vs-spawning availability
        rules are conditioned on lobby_flow_enabled().
        """
        verb, _ = self._get_subcommand_and_args()
        if verb is None:
            return super().at_pre_cmd()
        try:
            # 1) Combat gate — ALWAYS, regardless of the lobby flow flag. Blocks
            # both membership-removing and membership-adding side-changes while
            # in combat (the anti-combat-log rule, both directions).
            if verb in COMBAT_GATED_VERBS:
                from world.combat_timer import player_in_combat
                if player_in_combat(self.caller):
                    self.caller.msg(
                        "You can't change your alliance while in combat."
                    )
                    return True

            # 2) OOC availability — only meaningful while the lobby flow is on.
            from world.lobby_flow import lobby_flow_enabled
            if lobby_flow_enabled():
                from world import player_lifecycle as pl
                from world.constants import (
                    PLAYER_STATE_LOBBY, PLAYER_STATE_SPAWNING,
                )
                state = pl.get_state(self.caller)
                if state in (PLAYER_STATE_LOBBY, PLAYER_STATE_SPAWNING):
                    if verb in READONLY_OOC_VERBS:
                        return False
                    if verb in MUTATING_LOBBY_VERBS:
                        if state == PLAYER_STATE_SPAWNING:
                            self.caller.msg(
                                "Finish choosing your character first."
                            )
                            return True
                        return False  # LOBBY: allowed
                    self.caller.msg(f"'{verb}' is available in-game only.")
                    return True
        except Exception:  # noqa: BLE001 - a gate must never hard-block a command
            pass
        return super().at_pre_cmd()

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _system(self):
        return self.require_system("alliance_system", label="Alliance system")

    # ------------------------------------------------------------------ #
    #  Membership verbs
    # ------------------------------------------------------------------ #

    def sub_found(self, args):
        system = self._system()
        if system is None:
            return
        if "=" not in args:
            self.caller.msg("Usage: alliance found <name> = <tag>")
            return
        name, tag = (part.strip() for part in args.split("=", 1))
        system.found(self.caller, name, tag)

    def sub_invite(self, args):
        system = self._system()
        if system is None:
            return
        target = _resolve_player(self.caller, args.strip())
        if target is None:
            return
        system.invite(self.caller, target)

    def sub_invites(self, args):
        system = self._system()
        if system is None:
            return
        inbox = system.pending_invites_for(self.caller)
        if not inbox:
            self.caller.msg("You have no pending alliance invitations.")
            return
        lines = ["|wPending alliance invitations:|n"]
        for e in inbox:
            lines.append(f"  [{e['tag']}] |c{e['name']}|n")
        lines.append("Use |walliance accept <tag>|n or |walliance decline <tag>|n.")
        self.caller.msg("\n".join(lines))

    def sub_accept(self, args):
        system = self._system()
        if system is None:
            return
        system.accept(self.caller, args.strip())

    def sub_decline(self, args):
        system = self._system()
        if system is None:
            return
        system.decline(self.caller, args.strip())

    def sub_apply(self, args):
        system = self._system()
        if system is None:
            return
        system.apply_request(self.caller, args.strip())

    # `request` is an alias verb for `apply`.
    def sub_request(self, args):
        self.sub_apply(args)

    def sub_join(self, args):
        system = self._system()
        if system is None:
            return
        system.join_open(self.caller, args.strip())

    def sub_open(self, args):
        system = self._system()
        if system is None:
            return
        flag = args.strip().lower() in ("on", "true", "yes", "1")
        system.set_open_join(self.caller, flag)

    def sub_leave(self, args):
        system = self._system()
        if system is None:
            return
        system.leave(self.caller)

    def sub_kick(self, args):
        system = self._system()
        if system is None:
            return
        target = _resolve_player(self.caller, args.strip())
        if target is None:
            return
        system.kick(self.caller, target)

    def sub_promote(self, args):
        system = self._system()
        if system is None:
            return
        target = _resolve_player(self.caller, args.strip())
        if target is None:
            return
        system.promote(self.caller, target)

    def sub_demote(self, args):
        system = self._system()
        if system is None:
            return
        target = _resolve_player(self.caller, args.strip())
        if target is None:
            return
        system.demote(self.caller, target)

    def sub_transfer(self, args):
        system = self._system()
        if system is None:
            return
        target = _resolve_player(self.caller, args.strip())
        if target is None:
            return
        system.transfer(self.caller, target)

    def sub_claim(self, args):
        system = self._system()
        if system is None:
            return
        system.claim(self.caller)

    def sub_disband(self, args):
        system = self._system()
        if system is None:
            return
        system.disband(self.caller)

    def sub_rename(self, args):
        system = self._system()
        if system is None:
            return
        system.rename(self.caller, args.strip())

    def sub_retag(self, args):
        system = self._system()
        if system is None:
            return
        system.retag(self.caller, args.strip())

    def sub_ignore(self, args):
        system = self._system()
        if system is None:
            return
        target_arg = args.strip()
        if target_arg.lower() == "all":
            system.ignore(self.caller, "all")
            return
        target = _resolve_player(self.caller, target_arg)
        if target is None:
            return
        system.ignore(self.caller, target)

    # ------------------------------------------------------------------ #
    #  Treasury + perks
    # ------------------------------------------------------------------ #

    def _parse_costs(self, args):
        """Parse ``<amt> <res> [<amt> <res> ...]`` into a {res: amt} dict."""
        tokens = args.split()
        if len(tokens) < 2 or len(tokens) % 2 != 0:
            return None
        costs = {}
        for i in range(0, len(tokens), 2):
            try:
                amt = int(tokens[i])
            except ValueError:
                return None
            costs[tokens[i + 1].title()] = costs.get(tokens[i + 1].title(), 0) + amt
        return costs

    def sub_deposit(self, args):
        system = self._system()
        if system is None:
            return
        costs = self._parse_costs(args)
        if not costs:
            self.caller.msg("Usage: alliance deposit <amount> <resource> [...]")
            return
        system.deposit(self.caller, costs)

    def sub_withdraw(self, args):
        system = self._system()
        if system is None:
            return
        costs = self._parse_costs(args)
        if not costs:
            self.caller.msg("Usage: alliance withdraw <amount> <resource> [...]")
            return
        system.withdraw(self.caller, costs)

    def sub_perks(self, args):
        system = self._system()
        if system is None:
            return
        aid = getattr(self.caller.db, "player_alliance", None)
        if aid is None:
            self.caller.msg("You are not in an alliance.")
            return
        perks = system.available_perks(aid)
        if not perks:
            self.caller.msg("No perks are available.")
            return
        lines = ["|wAlliance perks:|n"]
        for p in perks:
            status = "active L%d" % p["current_level"] if p["current_level"] else "inactive"
            if p["next_level"] is not None:
                gate = "unlocked" if p["unlocked"] else "LOCKED"
                afford = "affordable" if p["affordable"] else "too costly"
                lines.append(
                    f"  |c{p['key']}|n ({status}) — next L{p['next_level']}: "
                    f"{gate}, {afford}"
                )
            else:
                lines.append(f"  |c{p['key']}|n ({status}) — max level")
        self.caller.msg("\n".join(lines))

    def sub_activate(self, args):
        system = self._system()
        if system is None:
            return
        perk_key = args.strip()
        if not perk_key:
            self.caller.msg("Usage: alliance activate <perk>")
            return
        system.activate_perk(self.caller, perk_key)

    # ------------------------------------------------------------------ #
    #  Information + chat
    # ------------------------------------------------------------------ #

    def sub_info(self, args):
        system = self._system()
        if system is None:
            return
        token = args.strip()
        if token:
            # Outsider view of another alliance by name/tag.
            record = system._alliances.by_tag(token) if system._alliances else None
            if record is None:
                self.caller.msg("No alliance with that tag.")
                return
            summary = system.alliance_summary(record["id"], for_member=False)
        else:
            aid = getattr(self.caller.db, "player_alliance", None)
            if aid is None:
                self.caller.msg("You are not in an alliance. Use 'alliance info <tag>' to view one.")
                return
            summary = system.alliance_summary(aid, for_member=True)
        if summary is None:
            self.caller.msg("That alliance no longer exists.")
            return
        self._render_info(summary)

    def _render_info(self, s):
        lines = [
            f"|w{s['name']}|n [{s['tag']}]  (level {s['level']})",
            f"  Leader: {s['leader']}   Members: {s['member_count']}",
        ]
        if s["active_perks"]:
            perks = ", ".join(f"{k} L{v}" for k, v in s["active_perks"].items())
            lines.append(f"  Perks: {perks}")
        if s.get("open_join"):
            lines.append("  Open-join: ON")
        if "treasury" in s:
            if s["treasury"]:
                tre = ", ".join(f"{a} {r}" for r, a in s["treasury"].items())
                lines.append(f"  Treasury: {tre}")
            else:
                lines.append("  Treasury: empty")
        self.caller.msg("\n".join(lines))

    def sub_board(self, args):
        system = self._system()
        if system is None:
            return
        aid = getattr(self.caller.db, "player_alliance", None)
        if aid is None:
            self.caller.msg("You are not in an alliance.")
            return
        rows = system.member_board(aid)
        if not rows:
            self.caller.msg("No members to show.")
            return
        lines = ["|wMember board:|n  (rank / name / level / kills / status)"]
        for r in rows:
            status = "online" if r["online"] else "offline"
            lines.append(
                f"  {r['rank']:<7} {r['name']:<16} L{r['level']:<3} "
                f"{r['scored_kills']:<6} {status}"
            )
        self.caller.msg("\n".join(lines))

    def sub_leaderboard(self, args):
        system = self._system()
        if system is None:
            return
        board = system.leaderboard()
        if not board:
            self.caller.msg("No alliances yet.")
            return
        lines = ["|wAlliance leaderboard:|n  (rank / tag / name / score)"]
        for i, (aid, score) in enumerate(board, 1):
            rec = system._record(aid)
            if rec is None:
                continue
            lines.append(f"  {i:>2}. [{rec['tag']}] {rec['name']} — {score:.0f}")
        self.caller.msg("\n".join(lines))

    def sub_chat(self, args):
        system = self._system()
        if system is None:
            return
        msg = args.strip()
        if not msg:
            self.caller.msg("Say what to your alliance?")
            return
        aid = getattr(self.caller.db, "player_alliance", None)
        if aid is None:
            self.caller.msg("You are not in an alliance.")
            return
        system._broadcast(aid, f"[{system.tag_for(self.caller)}] {self.caller.key}: {msg}")

    # ------------------------------------------------------------------ #
    #  Subcommand registry
    # ------------------------------------------------------------------ #

    subcommands = {
        "found": (sub_found, "Found a new alliance (name = tag)", None),
        "invite": (sub_invite, "Invite a player (officer+)", None),
        "invites": (sub_invites, "List your pending invitations", None),
        "accept": (sub_accept, "Accept an invitation by tag", None),
        "decline": (sub_decline, "Decline an invitation by tag", None),
        "apply": (sub_apply, "Request to join an alliance by tag", None),
        "request": (sub_request, "Request to join an alliance by tag", None),
        "join": (sub_join, "Join an open alliance by tag", None),
        "open": (sub_open, "Toggle open-join on|off (leader)", None),
        "leave": (sub_leave, "Leave your alliance", None),
        "kick": (sub_kick, "Kick a lower-ranked member (officer+)", None),
        "promote": (sub_promote, "Promote a member to officer (leader)", None),
        "demote": (sub_demote, "Demote an officer (leader)", None),
        "transfer": (sub_transfer, "Transfer leadership (leader)", None),
        "claim": (sub_claim, "Claim leadership of an absent-leader alliance (officer)", None),
        "disband": (sub_disband, "Disband the alliance (leader)", None),
        "rename": (sub_rename, "Rename the alliance (leader)", None),
        "retag": (sub_retag, "Change the alliance tag (leader)", None),
        "ignore": (sub_ignore, "Block invitations from a player or all", None),
        "deposit": (sub_deposit, "Deposit resources into the treasury", None),
        "withdraw": (sub_withdraw, "Withdraw from the treasury (officer+)", None),
        "perks": (sub_perks, "List perks and their status", None),
        "activate": (sub_activate, "Activate/upgrade a perk (leader)", None),
        "info": (sub_info, "Show alliance info", None),
        "board": (sub_board, "Your alliance's member ranking", None),
        "leaderboard": (sub_leaderboard, "The cross-alliance leaderboard", None),
        "chat": (sub_chat, "Send a message to your alliance channel", None),
    }
