"""
Unit tests for the AllianceSystem single-writer authority.

Drives ``AllianceSystem`` and ``AllianceRegistry`` directly with plain fakes (no
Evennia DB), per the spec's test-harness caveat. Covers founding + name/tag
policy, membership (invite/accept/decline/apply/open-join/leave/kick/disband/
transfer/promote/demote/claim), one-alliance + member + officer caps, the shared
treasury (deposit/withdraw/never-negative/rollback/cap/even-split), level
derivation, perk double-gate + one-per-category, reconciliation + succession, and
the composite leaderboard with PvP/PvE split + decay.
"""

import types
import unittest

from world.event_bus import EventBus
from world.definitions import BalanceConfig
from world.systems.alliance_system import AllianceSystem


# -------------------------------------------------------------- #
#  Fakes
# -------------------------------------------------------------- #

class _FakePlayer:
    """A minimal player: id, key, has_account, a db namespace, resources."""

    _next_id = 1000

    def __init__(self, key="P", level=10, has_account=True, npc_type=None):
        _FakePlayer._next_id += 1
        self.id = _FakePlayer._next_id
        self.key = key
        self.has_account = has_account
        self.messages = []
        self.account = types.SimpleNamespace()  # present but channel ops are stubbed
        self.db = types.SimpleNamespace(
            player_alliance=None,
            alliance_rank=None,
            level=level,
            # combat_xp present so is_player (and thus _is_real_player) treats
            # this fake as a genuine combat character; is_sentinel False.
            combat_xp=0,
            is_sentinel=False,
            scored_kills_pvp=0.0,
            scored_kills_pve=0.0,
            last_kill_decay_tick=0,
            alliance_invite_ignore=None,
            alliance_rejoin_until=0,
            npc_type=npc_type,
            player_state="playing",
        )
        self._resources = {}
        self._buildings = []
        self.tags = _NoTags()

    # resource helpers mirroring CombatCharacter
    def get_resource(self, r):
        return self._resources.get(r.title(), 0)

    def add_resource(self, r, amt):
        self._resources[r.title()] = self._resources.get(r.title(), 0) + amt

    def has_resources(self, costs):
        return all(self._resources.get(r.title(), 0) >= a for r, a in costs.items())

    def deduct_resources(self, costs):
        if not self.has_resources(costs):
            return False
        for r, a in costs.items():
            self._resources[r.title()] -= a
        return True

    def get_buildings(self):
        return list(self._buildings)

    def msg(self, text, **kw):
        self.messages.append(text)


class _NoTags:
    def get(self, *a, **k):
        return []


class _FakeAllianceRegistry:
    """In-memory stand-in for the persistent AllianceRegistry Script."""

    def __init__(self):
        self._alliances = {}
        self._next = 1

    def get(self, aid):
        return self._alliances.get(aid) if aid is not None else None

    def all_alliances(self):
        return list(self._alliances.values())

    def by_tag(self, tag):
        from world.systems.alliance_system import _normalize
        if not tag:
            return None
        norm = _normalize(tag)
        for rec in self._alliances.values():
            if _normalize(rec.get("tag", "")) == norm:
                return rec
        return None

    def allocate_id(self):
        nid = self._next
        self._next += 1
        return nid

    def put(self, record):
        self._alliances[record["id"]] = record

    def delete(self, aid):
        self._alliances.pop(aid, None)


class _FakeRegistry:
    """Stand-in DataRegistry: a BalanceConfig + an alliance perk catalog."""

    def __init__(self, balance=None, perks=None):
        self.balance = balance or BalanceConfig()
        self.alliance_perks = perks or {}

    def get_alliance_perk(self, key):
        return self.alliance_perks.get(key)


# -------------------------------------------------------------- #
#  Harness
# -------------------------------------------------------------- #

class _AllianceTestBase(unittest.TestCase):
    def setUp(self):
        self.tick = [0]
        self.alliances = _FakeAllianceRegistry()
        self.registry = _FakeRegistry()
        self.bus = EventBus()
        self.sys = AllianceSystem(
            self.registry, self.bus,
            alliance_registry=self.alliances,
            tick_provider=lambda: self.tick[0],
        )
        # A shared member index so _resolve_member (patched below) can find fakes.
        self.people = {}
        self.sys._resolve_member = lambda cid: self.people.get(cid)
        # Channel ops are no-ops in the fake env.
        self.sys._ensure_channel = lambda aid: None
        self.sys._subscribe = lambda p, aid: None
        self.sys._unsubscribe = lambda p, aid: None
        self.sys._destroy_channel = lambda aid: None
        self.sys._broadcast = lambda aid, msg: None

    def _mk(self, key="P", level=10, **kw):
        p = _FakePlayer(key=key, level=level, **kw)
        self.people[p.id] = p
        return p


# -------------------------------------------------------------- #
#  Founding + name/tag policy
# -------------------------------------------------------------- #

class TestFounding(_AllianceTestBase):
    def test_found_sets_leader_pointer_and_record(self):
        p = self._mk("Ada", level=10)
        aid = self.sys.found(p, "Iron Wolves", "IW")
        self.assertIsNotNone(aid)
        self.assertEqual(p.db.player_alliance, aid)
        self.assertEqual(p.db.alliance_rank, "leader")
        rec = self.alliances.get(aid)
        self.assertEqual(rec["leader_id"], p.id)
        self.assertEqual(rec["name"], "Iron Wolves")

    def test_found_refused_below_level(self):
        p = self._mk("Low", level=9)  # found_min_level default 10
        self.assertIsNone(self.sys.found(p, "Toolow", "TL"))
        self.assertIsNone(p.db.player_alliance)

    def test_found_refused_when_already_in_alliance(self):
        p = self._mk("Ada", level=20)
        self.sys.found(p, "First", "F1")
        self.assertIsNone(self.sys.found(p, "Second", "F2"))

    def test_name_uniqueness_case_insensitive(self):
        a = self._mk("A", level=20)
        b = self._mk("B", level=20)
        self.sys.found(a, "Wolves", "WLV")
        self.assertIsNone(self.sys.found(b, "wolves", "WL2"))

    def test_tag_uniqueness_and_length(self):
        a = self._mk("A", level=20)
        b = self._mk("B", level=20)
        self.sys.found(a, "Alpha", "AAA")
        self.assertIsNone(self.sys.found(b, "Beta", "aaa"))  # dup tag (normalized)
        self.assertIsNone(self.sys.found(b, "Beta", "TOOLONG"))  # > 5 chars

    def test_reserved_and_markup_rejected(self):
        a = self._mk("A", level=20)
        self.assertIsNone(self.sys.found(a, "Admins United", "AU"))  # reserved word
        b = self._mk("B", level=20)
        self.assertIsNone(self.sys.found(b, "Red|rTeam", "RT"))  # markup

    def test_non_real_player_cannot_found(self):
        npc = self._mk("Guard", level=20, npc_type="enemy")
        self.assertIsNone(self.sys.found(npc, "NPCs", "NPC"))


# -------------------------------------------------------------- #
#  Invitations + join gating
# -------------------------------------------------------------- #

class TestInvites(_AllianceTestBase):
    def _founded(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        return leader, aid

    def test_invite_accept_flow(self):
        leader, aid = self._founded()
        rookie = self._mk("Rookie", level=10)
        self.assertTrue(self.sys.invite(leader, rookie))
        self.assertTrue(self.sys.accept(rookie, "ALN"))
        self.assertEqual(rookie.db.player_alliance, aid)
        self.assertEqual(rookie.db.alliance_rank, "member")
        self.assertIn(rookie.id, self.alliances.get(aid)["member_ids"])

    def test_accept_below_join_level_refused(self):
        leader, aid = self._founded()
        low = self._mk("Low", level=4)  # join_min_level default 5
        self.sys.invite(leader, low)
        self.assertFalse(self.sys.accept(low, "ALN"))
        self.assertIsNone(low.db.player_alliance)

    def test_invite_requires_officer(self):
        leader, aid = self._founded()
        member = self._mk("Mem", level=10)
        self.sys.invite(leader, member)
        self.sys.accept(member, "ALN")
        # A plain member cannot invite.
        other = self._mk("Other", level=10)
        self.assertFalse(self.sys.invite(member, other))

    def test_invite_already_in_alliance_refused(self):
        leader, aid = self._founded()
        other_leader = self._mk("OL", level=20)
        self.sys.found(other_leader, "Other", "OTH")
        self.assertFalse(self.sys.invite(leader, other_leader))

    def test_decline_removes_invite(self):
        leader, aid = self._founded()
        rookie = self._mk("Rookie", level=10)
        self.sys.invite(leader, rookie)
        self.assertTrue(self.sys.decline(rookie, "ALN"))
        self.assertEqual(self.sys.pending_invites_for(rookie), [])

    def test_invite_expiry(self):
        leader, aid = self._founded()
        rookie = self._mk("Rookie", level=10)
        self.sys.invite(leader, rookie)
        # Jump past the expiry window (7 days in ticks).
        self.tick[0] = 8 * 86400
        self.assertEqual(self.sys.pending_invites_for(rookie), [])
        self.assertFalse(self.sys.accept(rookie, "ALN"))

    def test_ignore_blocks_invite(self):
        leader, aid = self._founded()
        rookie = self._mk("Rookie", level=10)
        self.sys.ignore(rookie, leader)
        self.assertFalse(self.sys.invite(leader, rookie))

    def test_member_cap(self):
        leader, aid = self._founded()
        # default cap 10: fill to cap then refuse.
        bal = self.registry.balance
        bal.alliance_max_members = 2  # leader + 1
        m1 = self._mk("M1", level=10)
        self.sys.invite(leader, m1)
        self.assertTrue(self.sys.accept(m1, "ALN"))
        m2 = self._mk("M2", level=10)
        self.sys.invite(leader, m2)
        self.assertFalse(self.sys.accept(m2, "ALN"))

    def test_purge_pending_on_join(self):
        leader, aid = self._founded()
        # Second alliance also invites the rookie.
        l2 = self._mk("L2", level=20)
        aid2 = self.sys.found(l2, "Second", "SEC")
        rookie = self._mk("Rookie", level=10)
        self.sys.invite(leader, rookie)
        self.sys.invite(l2, rookie)
        self.assertEqual(len(self.sys.pending_invites_for(rookie)), 2)
        self.sys.accept(rookie, "ALN")
        # After joining, the other alliance's pending invite is purged.
        self.assertEqual(self.sys.pending_invites_for(rookie), [])


# -------------------------------------------------------------- #
#  Apply / open-join
# -------------------------------------------------------------- #

class TestApplyOpenJoin(_AllianceTestBase):
    def test_apply_then_officer_accepts(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        rookie = self._mk("Rookie", level=10)
        self.assertTrue(self.sys.apply_request(rookie, "ALN"))
        self.assertIn(rookie.id, self.alliances.get(aid)["pending_requests"])
        self.assertTrue(self.sys.accept_request(leader, rookie))
        self.assertEqual(rookie.db.player_alliance, aid)

    def test_open_join_toggle(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        rookie = self._mk("Rookie", level=10)
        # Closed by default.
        self.assertFalse(self.sys.join_open(rookie, "ALN"))
        self.assertTrue(self.sys.set_open_join(leader, True))
        self.assertTrue(self.sys.join_open(rookie, "ALN"))
        self.assertEqual(rookie.db.player_alliance, aid)


# -------------------------------------------------------------- #
#  Leave / kick / disband / transfer / promote / demote / claim
# -------------------------------------------------------------- #

class TestRosterOps(_AllianceTestBase):
    def _team(self, n_members=2):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        members = []
        for i in range(n_members):
            m = self._mk(f"M{i}", level=10)
            self.sys.invite(leader, m)
            self.sys.accept(m, "ALN")
            members.append(m)
        return leader, aid, members

    def test_member_leave(self):
        leader, aid, members = self._team(1)
        m = members[0]
        self.assertTrue(self.sys.leave(m))
        self.assertIsNone(m.db.player_alliance)
        self.assertNotIn(m.id, self.alliances.get(aid)["member_ids"])

    def test_leader_leave_refused_with_members(self):
        leader, aid, members = self._team(1)
        self.assertFalse(self.sys.leave(leader))
        self.assertIsNotNone(leader.db.player_alliance)

    def test_sole_leader_leave_disbands(self):
        leader = self._mk("Solo", level=20)
        aid = self.sys.found(leader, "Solo Alliance", "SOLO")
        self.assertTrue(self.sys.leave(leader))
        self.assertIsNone(self.alliances.get(aid))
        self.assertIsNone(leader.db.player_alliance)

    def test_kick_lower_rank(self):
        leader, aid, members = self._team(1)
        m = members[0]
        self.assertTrue(self.sys.kick(leader, m))
        self.assertIsNone(m.db.player_alliance)

    def test_kick_equal_or_higher_refused(self):
        leader, aid, members = self._team(2)
        self.sys.promote(leader, members[0])
        self.sys.promote(leader, members[1])
        # Officer cannot kick another officer.
        self.assertFalse(self.sys.kick(members[0], members[1]))

    def test_transfer_leadership(self):
        leader, aid, members = self._team(1)
        m = members[0]
        self.assertTrue(self.sys.transfer(leader, m))
        self.assertEqual(m.db.alliance_rank, "leader")
        self.assertEqual(leader.db.alliance_rank, "officer")
        self.assertEqual(self.alliances.get(aid)["leader_id"], m.id)

    def test_promote_demote_and_officer_cap(self):
        leader, aid, members = self._team(2)
        self.registry.balance.alliance_max_officers = 1
        self.assertTrue(self.sys.promote(leader, members[0]))
        self.assertEqual(members[0].db.alliance_rank, "officer")
        # Second promote refused by the cap.
        self.assertFalse(self.sys.promote(leader, members[1]))
        # Demote works.
        self.assertTrue(self.sys.demote(leader, members[0]))
        self.assertEqual(members[0].db.alliance_rank, "member")

    def test_disband_clears_all_pointers(self):
        leader, aid, members = self._team(2)
        self.assertTrue(self.sys.disband(leader))
        self.assertIsNone(self.alliances.get(aid))
        self.assertIsNone(leader.db.player_alliance)
        for m in members:
            self.assertIsNone(m.db.player_alliance)


# -------------------------------------------------------------- #
#  Treasury
# -------------------------------------------------------------- #

class TestTreasury(_AllianceTestBase):
    def _team(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        m = self._mk("Mem", level=10)
        self.sys.invite(leader, m)
        self.sys.accept(m, "ALN")
        return leader, aid, m

    def test_deposit_moves_resources(self):
        leader, aid, m = self._team()
        m.add_resource("Iron", 50)
        self.assertTrue(self.sys.deposit(m, {"Iron": 30}))
        self.assertEqual(m.get_resource("Iron"), 20)
        self.assertEqual(self.alliances.get(aid)["treasury"]["Iron"], 30)

    def test_deposit_conservation(self):
        leader, aid, m = self._team()
        m.add_resource("Iron", 40)
        self.sys.deposit(m, {"Iron": 25})
        total = m.get_resource("Iron") + self.alliances.get(aid)["treasury"].get("Iron", 0)
        self.assertEqual(total, 40)

    def test_deposit_refused_without_resources(self):
        leader, aid, m = self._team()
        self.assertFalse(self.sys.deposit(m, {"Iron": 5}))
        self.assertNotIn("Iron", self.alliances.get(aid).get("treasury", {}))

    def test_withdraw_officer_only(self):
        leader, aid, m = self._team()
        m.add_resource("Iron", 50)
        self.sys.deposit(m, {"Iron": 50})
        # Plain member cannot withdraw.
        self.assertFalse(self.sys.withdraw(m, {"Iron": 10}))
        # Leader can.
        self.assertTrue(self.sys.withdraw(leader, {"Iron": 10}))
        self.assertEqual(leader.get_resource("Iron"), 10)

    def test_withdraw_never_negative(self):
        leader, aid, m = self._team()
        m.add_resource("Iron", 10)
        self.sys.deposit(m, {"Iron": 10})
        self.assertFalse(self.sys.withdraw(leader, {"Iron": 999}))
        self.assertEqual(self.alliances.get(aid)["treasury"]["Iron"], 10)

    def test_officer_withdraw_cap_and_leader_override(self):
        leader, aid, m = self._team()
        self.registry.balance.alliance_withdraw_cap_per_window = 20
        m.add_resource("Iron", 100)
        self.sys.deposit(m, {"Iron": 100})
        self.sys.promote(leader, m)  # m is now officer
        self.assertTrue(self.sys.withdraw(m, {"Iron": 20}))
        # Second withdraw exceeds the per-window cap.
        self.assertFalse(self.sys.withdraw(m, {"Iron": 1}))
        # Leader bypasses the cap.
        self.assertTrue(self.sys.withdraw(leader, {"Iron": 50}))

    def test_even_split_on_disband(self):
        leader, aid, m = self._team()
        # Put 11 Iron in the treasury: 2 members -> 5 each, remainder 1 to leader.
        m.add_resource("Iron", 11)
        self.sys.deposit(m, {"Iron": 11})
        leader_before = leader.get_resource("Iron")
        m_before = m.get_resource("Iron")
        self.sys.disband(leader)
        self.assertEqual(m.get_resource("Iron") - m_before, 5)
        self.assertEqual(leader.get_resource("Iron") - leader_before, 6)  # 5 + remainder 1


# -------------------------------------------------------------- #
#  Level derivation
# -------------------------------------------------------------- #

class TestLevel(_AllianceTestBase):
    def test_sum_maps_through_thresholds(self):
        # thresholds default {0:1, 40:2, 100:3, 180:4, 280:5}
        leader = self._mk("Leader", level=50)
        aid = self.sys.found(leader, "Alliance", "ALN")
        self.assertEqual(self.sys.compute_alliance_level(aid), 2)  # 50 -> tier 2
        m = self._mk("M", level=60)
        self.sys.invite(leader, m)
        self.sys.accept(m, "ALN")
        self.assertEqual(self.sys.compute_alliance_level(aid), 3)  # 110 -> tier 3

    def test_level_capped_at_num_tiers(self):
        leader = self._mk("Leader", level=60)
        aid = self.sys.found(leader, "Alliance", "ALN")
        # Stuff the roster to a huge sum; still capped at tier 5.
        for i in range(10):
            m = self._mk(f"M{i}", level=60)
            self.sys.invite(leader, m)
            self.sys.accept(m, "ALN")
        self.assertEqual(self.sys.compute_alliance_level(aid), 5)


# -------------------------------------------------------------- #
#  Perks — double gate + one per category
# -------------------------------------------------------------- #

_PERKS = {
    "shared_regen": {
        "category": "shared_regen", "effect_type": "multiplier",
        "levels": {
            1: {"tier": 2, "multiplier": 1.25, "cost": {"Iron": 10}},
            2: {"tier": 3, "multiplier": 1.4, "cost": {"Iron": 20}},
        },
    },
    "combat_damage": {
        "category": "combat_damage", "effect_type": "flat",
        "levels": {1: {"tier": 3, "damage_bonus": 2, "cost": {"Iron": 10}}},
    },
    "shared_vision": {
        "category": "shared_vision", "effect_type": "boolean",
        "levels": {1: {"tier": 2, "effect": True, "cost": {"Iron": 5}}},
    },
}


class TestPerks(_AllianceTestBase):
    def setUp(self):
        super().setUp()
        self.registry.alliance_perks = _PERKS

    def _rich_alliance(self, level_each=50, iron=1000):
        leader = self._mk("Leader", level=level_each)
        aid = self.sys.found(leader, "Alliance", "ALN")
        leader.add_resource("Iron", iron)
        self.sys.deposit(leader, {"Iron": iron})
        return leader, aid

    def test_level_gate_blocks_activation(self):
        leader = self._mk("Leader", level=10)  # sum 10 -> tier 1
        aid = self.sys.found(leader, "Alliance", "ALN")
        leader.add_resource("Iron", 100)
        self.sys.deposit(leader, {"Iron": 100})
        # combat_damage needs tier 3.
        self.assertFalse(self.sys.activate_perk(leader, "combat_damage"))

    def test_treasury_gate_blocks_activation(self):
        leader = self._mk("Leader", level=50)  # tier 2
        aid = self.sys.found(leader, "Alliance", "ALN")
        # No treasury deposited.
        self.assertFalse(self.sys.activate_perk(leader, "shared_regen"))

    def test_activate_and_upgrade(self):
        leader, aid = self._rich_alliance(level_each=60)  # tier 2 (60 -> t2)
        # Bump to tier 3 by adding a big-level member.
        m = self._mk("M", level=60)
        self.sys.invite(leader, m)
        self.sys.accept(m, "ALN")  # sum 120 -> tier 3
        self.assertTrue(self.sys.activate_perk(leader, "shared_regen"))  # L1 tier2
        self.assertEqual(self.alliances.get(aid)["active_perks"]["shared_regen"], 1)
        self.assertTrue(self.sys.activate_perk(leader, "shared_regen"))  # L2 tier3
        self.assertEqual(self.alliances.get(aid)["active_perks"]["shared_regen"], 2)

    def test_one_per_category(self):
        # Two perks in the same category — only one may be active.
        self.registry.alliance_perks = {
            "regen_a": {"category": "shared_regen", "effect_type": "multiplier",
                        "levels": {1: {"tier": 1, "multiplier": 1.1, "cost": {"Iron": 1}}}},
            "regen_b": {"category": "shared_regen", "effect_type": "multiplier",
                        "levels": {1: {"tier": 1, "multiplier": 1.2, "cost": {"Iron": 1}}}},
        }
        leader = self._mk("Leader", level=50)
        aid = self.sys.found(leader, "Alliance", "ALN")
        leader.add_resource("Iron", 100)
        self.sys.deposit(leader, {"Iron": 100})
        self.assertTrue(self.sys.activate_perk(leader, "regen_a"))
        self.assertFalse(self.sys.activate_perk(leader, "regen_b"))

    def test_perk_multiplier_membership_derived(self):
        leader, aid = self._rich_alliance(level_each=50)
        self.sys.activate_perk(leader, "shared_regen")
        self.assertAlmostEqual(self.sys.perk_multiplier(leader, "shared_regen"), 1.25)
        # A non-member sees 1.0.
        outsider = self._mk("Out", level=10)
        self.assertEqual(self.sys.perk_multiplier(outsider, "shared_regen"), 1.0)

    def test_grandfather_on_level_drop(self):
        leader, aid = self._rich_alliance(level_each=60)
        m = self._mk("M", level=60)
        self.sys.invite(leader, m)
        self.sys.accept(m, "ALN")  # tier 3
        self.sys.activate_perk(leader, "combat_damage")  # tier 3
        self.assertEqual(self.sys.perk_flat_bonus(leader, "combat_damage", "damage_bonus"), 2)
        # Member leaves -> level drops, but the perk stays active.
        self.sys.kick(leader, m)
        self.assertEqual(self.alliances.get(aid)["active_perks"].get("combat_damage"), 1)


# -------------------------------------------------------------- #
#  Leaderboard + decay
# -------------------------------------------------------------- #

class TestLeaderboard(_AllianceTestBase):
    def test_score_weights_pvp_over_pve(self):
        leader = self._mk("Leader", level=10)
        aid = self.sys.found(leader, "Alliance", "ALN")
        leader.db.scored_kills_pvp = 2.0
        leader.db.scored_kills_pve = 2.0
        # score = 10*1 + 2*3 + 2*1 + 0*1.5 = 18
        self.assertAlmostEqual(self.sys.alliance_score(aid), 18.0)

    def test_leaderboard_orders_desc_with_id_tiebreak(self):
        a = self._mk("A", level=10)
        aid_a = self.sys.found(a, "Aaa", "AAA")
        b = self._mk("B", level=20)
        aid_b = self.sys.found(b, "Bbb", "BBB")
        board = self.sys.leaderboard()
        # B (level 20 -> 20) outranks A (level 10 -> 10).
        self.assertEqual(board[0][0], aid_b)
        self.assertEqual(board[1][0], aid_a)

    def test_decay_reduces_stale_kills(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        leader.db.level = 0  # isolate the decay effect from the level term
        leader.db.scored_kills_pvp = 100.0
        leader.db.last_kill_decay_tick = 0
        fresh = self.sys.alliance_score(aid)
        # Advance many decay intervals; the pvp term should shrink.
        self.tick[0] = 600 * 50  # 50 intervals at default interval 600
        decayed = self.sys.alliance_score(aid)
        self.assertLess(decayed, fresh)

    def test_ghost_member_scores_zero(self):
        leader = self._mk("Leader", level=10)
        aid = self.sys.found(leader, "Alliance", "ALN")
        # Inject a roster id that points elsewhere (rival) -> filtered out.
        ghost = self._mk("Ghost", level=60)
        ghost.db.player_alliance = 9999  # not this alliance
        self.alliances.get(aid)["member_ids"].append(ghost.id)
        # Score counts only the leader (level 10), not the ghost's level 60.
        self.assertAlmostEqual(self.sys.alliance_score(aid), 10.0)


# -------------------------------------------------------------- #
#  Reconciliation + succession
# -------------------------------------------------------------- #

class TestReconcile(_AllianceTestBase):
    def test_succession_promotes_senior_when_leader_gone(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        officer = self._mk("Off", level=10)
        self.sys.invite(leader, officer)
        self.sys.accept(officer, "ALN")
        self.sys.promote(leader, officer)
        # Leader vanishes: pointer cleared + removed from resolver.
        del self.people[leader.id]
        rec = self.alliances.get(aid)
        rec["leader_id"] = leader.id  # dangling
        self.alliances.put(rec)
        self.sys.reconcile(aid)
        self.assertEqual(officer.db.alliance_rank, "leader")
        self.assertEqual(self.alliances.get(aid)["leader_id"], officer.id)

    def test_reconcile_disbands_when_no_member_resolves(self):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        del self.people[leader.id]
        self.sys.reconcile(aid)
        self.assertIsNone(self.alliances.get(aid))


# -------------------------------------------------------------- #
#  Code-review fix regressions
# -------------------------------------------------------------- #

class TestReviewFixes(_AllianceTestBase):
    def _team(self, n_members=1):
        leader = self._mk("Leader", level=20)
        aid = self.sys.found(leader, "Alliance", "ALN")
        members = []
        for i in range(n_members):
            m = self._mk(f"M{i}", level=10)
            self.sys.invite(leader, m)
            self.sys.accept(m, "ALN")
            members.append(m)
        return leader, aid, members

    # Fix #2 — leader chardelete promotes an heir, no dangling leader_id.
    def test_leader_chardelete_runs_succession(self):
        leader, aid, members = self._team(1)
        heir = members[0]
        # Simulate chardelete: the row is still resolvable when the hook runs,
        # so on_character_deleted must clear the pointer BEFORE reconcile.
        self.sys.on_character_deleted(leader)
        rec = self.alliances.get(aid)
        self.assertIsNotNone(rec, "alliance survives (had another member)")
        self.assertEqual(rec["leader_id"], heir.id,
                         "heir promoted; leader_id not left dangling at deleted id")
        self.assertEqual(heir.db.alliance_rank, "leader")

    def test_sole_leader_chardelete_disbands(self):
        leader = self._mk("Solo", level=20)
        aid = self.sys.found(leader, "Solo", "SOLO")
        self.sys.on_character_deleted(leader)
        self.assertIsNone(self.alliances.get(aid), "no members left -> disbanded")

    # Fix #5 — declining suppresses an immediate re-invite for the cooldown.
    def test_decline_suppresses_reinvite(self):
        leader, aid, _ = self._team(0)
        rookie = self._mk("Rookie", level=10)
        self.assertTrue(self.sys.invite(leader, rookie))
        self.assertTrue(self.sys.decline(rookie, "ALN"))
        # Immediate re-invite is refused (still within the cooldown window).
        self.assertFalse(self.sys.invite(leader, rookie))
        # The declined stub is NOT shown as a live invite.
        self.assertEqual(self.sys.pending_invites_for(rookie), [])
        # After the cooldown elapses, a re-invite works and shows in the inbox.
        self.tick[0] += int(self.registry.balance.alliance_invite_cooldown_ticks) + 1
        self.assertTrue(self.sys.invite(leader, rookie))
        self.assertEqual(len(self.sys.pending_invites_for(rookie)), 1)

    # Fix #7 — disband notifies members (DM) and does so before teardown.
    def test_disband_notifies_members(self):
        # Use a real broadcast/DM capture rather than the no-op stub.
        broadcasts = []
        self.sys._broadcast = lambda aid, msg: broadcasts.append((aid, msg))
        leader, aid, members = self._team(1)
        member = members[0]
        member.messages.clear()
        self.sys.disband(leader)
        # The channel broadcast fired for the disband...
        self.assertTrue(any("disbanded" in m.lower() for _, m in broadcasts))
        # ...and the member got a direct message too.
        self.assertTrue(any("disbanded" in m.lower() for m in member.messages))

    # Fix #9 — spaced-out reserved words are rejected.
    def test_denylist_blocks_spaced_reserved_word(self):
        p = self._mk("Sneaky", level=20)
        self.assertIsNone(self.sys.found(p, "a d m i n", "SNK"),
                          "'a d m i n' must be rejected (spaced-out 'admin')")
        self.assertIsNone(p.db.player_alliance)

    # Fix #6 — _leader_absent uses last_seen_time, not a coup on an active leader.
    def test_leader_absent_uses_last_seen(self):
        import time as _t
        leader = self._mk("Leader", level=20)
        # Offline (no sessions) but seen 1 hour ago -> NOT absent.
        leader.sessions = types.SimpleNamespace(count=lambda: 0)
        leader.db.last_seen_time = _t.time() - 3600
        self.assertFalse(self.sys._leader_absent(leader))
        # Seen 30 days ago -> absent (default threshold 7 days).
        leader.db.last_seen_time = _t.time() - 30 * 86400
        self.assertTrue(self.sys._leader_absent(leader))
        # Currently connected -> present regardless of last_seen.
        leader.sessions = types.SimpleNamespace(count=lambda: 1)
        self.assertFalse(self.sys._leader_absent(leader))
        # Unresolvable leader -> absent.
        self.assertTrue(self.sys._leader_absent(None))


if __name__ == "__main__":
    unittest.main()
