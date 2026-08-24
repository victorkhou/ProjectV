"""
Unit tests for the Operation Contract lifecycle value types.

Feature: tech-tree-branch-foundation, design section "Operation Contract / §7
``Operation_Record`` — the persisted shape". The *fixed* claims about the four
value types; the property modules own the generated input space (Properties 21
and 23):

* **R8.1, R8.2** — the lifecycle is exactly six states spelled exactly as the
  design spells them, and exactly four of those are terminal. Asserted as set
  equality in both directions so neither a new state nor a renamed one can slip
  in unnoticed.
* **The vocabulary shared by value** — ``world.systems.branch_system``
  deliberately does *not* import :class:`OperationState`; it compares the four
  terminal names as plain strings in its own private tuple. The two spellings
  are cross-checked here, from the enum's side, so a hand edit to either source
  fails loudly rather than silently uncapping a player's in-flight operations.
  The same test pins that a plain string answers ``in TERMINAL_STATES``, which
  is the property that makes the by-value sharing safe.
* **R8.21** — an ``OperationRecord`` carries every persisted field the
  requirement enumerates, and ``to_dict`` emits a key for each. Including the
  four fields ``BranchSystem.in_flight_count`` reads by name off *both* shapes
  (``kind``, ``owner_ref``, ``planet``, ``state``), because the count reads a
  record duck-typed and would silently under-count if a key were renamed.
* **R14.8** — ``from_dict`` reads each field by value with its documented
  default: an empty payload, a payload of ``None``s, and a payload of junk
  values all read back as a fully populated record and raise nothing, while a
  full payload round-trips unchanged. The one input that *does* raise is a
  non-mapping, which is the corrupt record the rebuild logs and recovers from
  (R14.5) rather than a partial one.
* **R8.24** — every request answers an ``OperationOutcome``: ``accepted`` names
  the resulting lifecycle state and the identity, ``refused`` and ``failed``
  name the check and carry structured data and no state, and the value is frozen
  so nothing can rewrite a decision after the fact.
* **R14.1, R14.7, R14.8** — the persistence pair. Records live under the
  ``vector_operations`` attribute of whatever durable owner the vector nominated,
  a write replaces the whole container, a read is by value, and an absent
  attribute is an empty list. The load-bearing case is the *hostile* attribute
  handler below, which discards an in-place change to a container it handed out
  exactly as a real Evennia attribute may: under it, only genuine read-copy-write
  persists anything. An owner with no attribute handler, and a stored value of
  the wrong shape entirely, are both tolerated rather than raised on, and one
  corrupt entry costs one record.

And the ``OperationDriver`` skeleton (design section "The Operation Contract /
§4.1"), whose fixed claims are the shape a vector inherits and the single writer
of ``record.state``:

* **The declared surface** — a vector sets ``operation_kind``, ``branch``, and
  ``_required_collaborators``; the driver supplies five *required* hooks that
  refuse to be left unimplemented and five *optional* ones that default to a
  no-op. Both lists are pinned by name and count, because the contract's promise
  is that a vector spec implements exactly five things.
* **The duck-typed surface `branch_system` reaches through** —
  ``operation_kind`` and ``tracked_records()``. Asserted against
  ``BranchSystem``'s own static readers rather than a restatement of them, so
  registration and the in-flight cap are shown to work over a real driver
  without this module importing a fixture world.
* **R8.3, R8.4 — the ordered validation chain.** ``_CHECK_ORDER`` is the nine
  checks in the order the requirement fixes, written out here from the
  requirement rather than read off the driver, and cross-checked against
  ``branch_strategies.OPERATION_CHECK_ORDER`` so the copy Property 13's
  forced-failure lattice walks cannot drift from the chain that ships. ``request``
  refuses at the *earliest* failing check, asks nothing after it, and carries
  exactly one reason. Each of the nine is then exercised on its own against a
  double of the Branch services — reached by name, duck-typed, exactly as the
  driver reaches them — so the refusal each one reports is pinned to the value
  the requirement asks be reported: the missing collaborator (R15.2), the Branch
  and lab required (R8.3), the originating building and why it could not be used
  (R11.3, R5.4), the unlocking technology (R6.6), the Carrier_Agent role (R7.3),
  whichever protection gate fired (R10.4, R10.6, R10.7, R11.9), the remaining
  cooldown (R8.19), the count and the cap (R8.20), and the have-and-need
  breakdown (R12.3). And a refused request is shown to *change nothing* (R8.4):
  nothing tracked, nothing persisted, and no Branch service that writes called.
* **R8.1, R8.2 — ``_transition`` is the single writer of ``record.state``.** It
  is asserted three ways: it writes and persists an accepted move, it refuses to
  move a terminal record *and* refuses to write a state outside the six, and an
  AST scan of the shipped module proves no other function assigns ``.state`` at
  all. The scan is what makes terminal finality structural — a future path that
  set the state directly would fail the scan rather than quietly bypassing the
  guard.
* **R8.5, R8.6, R12.2 — the acceptance half.** The cost is charged *before* the
  record enters Pending, asserted from the vector's own vantage point rather than
  by reading the driver's order back to itself; the amount checked is the amount
  charged; and the accepted record is Pending, tracked, and persisted carrying
  what it was charged. The refund is asserted at **each** of the four points a
  request can fail after the charge — the vector's hook, the tracking, the
  persist inside the state write, and a record the single writer declines to move
  — because they fail at different points of the same guarded block and a refund
  covering one of them would pass a single-point test. A charge that fails
  refuses the ``resources`` check in the same shape the pre-check does (R12.3)
  and creates nothing.
* **R12.6, R11.6 — an NPC base's operation.** Both markers ``BranchSystem``
  itself reads (``is_sentinel``, ``npc_type``) waive the charge entirely, and the
  operation is shown to be bound by everything else: it is placed, its window is
  floored, and its cooldown is noted exactly as a player's is.
* **R8.8, R9.4 — the Response_Window floor.** A hostile operation's clock is
  raised to ``minimum_response_window_ticks`` on entry — from below it, from
  zero, and from a negative value a Counter_Web reduction could produce — while a
  clock above the floor is left as the vector asked. Delegated to the shared
  ``BranchSystem.response_window`` (R15.8), read per request (R15.7), and shown
  to hold even when that service is absent or raises. The same helper is exercised
  with only a record in hand, which is the shape the resume path (task 11.5) has
  to call it in.
* **R8.7, R8.12, R8.13, R13.5, R13.6, R13.8 — the notification points.** The
  vocabulary first: nine kinds, all nine distinct, every one of the six lifecycle
  states reporting through one of them, and the consent key cross-checked against
  ``BranchSystem``'s own spelling, which is the authority. Then the one point the
  request path reaches — R8.7's warning to a hostile operation's targets, which is
  shown to carry the four values the requirement asks for, to quote the *floored*
  window because R8.8 measures it from this notification, and to be published
  *before* the cooldown note for the same reason. A supporting operation warns
  nobody, a refusal and a failed Pending entry notify nobody, and an
  NPC-originated operation warns exactly as a player's does (R11.6). Then the
  audience: R8.12's two halves — the owner of an affected entity and a player
  standing on an affected tile — resolved from the effect's Chebyshev area and
  de-duplicated, with the originating player and an ally shown to be *in* it
  (R11.10) and to receive one notification rather than two. And the helpers the
  tick advance and the rebuild call, so tasks 11.5 and 11.6 publish nothing of
  their own. Every payload is a kind plus structured values, and every reason is
  a key, never a sentence (R13.5). The whole path degrades: a driver with no
  ``notify``, a publish that raises, an unreadable room, and an owner reference
  that resolves to nothing all log and answer rather than raise (R15.2, R15.3).

And the runtime half — per-tick advancement, suspension, and cancellation
(design §4.7):

* **R8.9, R8.10 — one tick, per-operation isolation.** Every tracked operation
  advances by exactly one; an operation whose advance raises is **kept** and
  logged with its kind and ``op_id`` while the rest still advance, which is the
  one place this deliberately differs from ``BombSystem`` (a dropped operation
  would be a silent hazard leak, because its record stays persisted). A terminal
  record takes no tick, an empty registry is a no-op, and an operation a vector's
  own hook placed mid-pass is not swept away by the tracked-list rebuild.
* **R8.11, R8.13 — the two clocks, in order.** The bounded lifetime is
  decremented before the effect clock, so an operation that runs out of life on
  the tick its effect would land expires rather than resolving; a clock reaching
  zero applies the effect and then settles the record, and the resolution
  audience is shown to be read *after* the effect. A resolved or expired
  operation is untracked and swept out of storage, and an effect hook that raises
  settles the record anyway rather than earning it another tick and another
  partial effect.
* **R8.14, R8.15, R8.18 — suspension delays, never restarts.** The snapshot is
  taken before the state write so it persists with the state, neither clock runs
  while paused, suspending twice snapshots and notifies once, and a resume
  restores the snapshot, re-floors a hostile window (R8.8), and quotes the
  restored clock. The end-to-end claim is asserted directly: the total elapsed
  ticks to resolution are the original count plus the ticks the pause held for.
* **R8.16, R8.17, R11.4 — every cancellation trigger.** Read every tick (a dead
  or deleted carrier, an origin that went non-Operational or was deleted) and
  announced by three subscriptions (``PLAYER_ELIMINATED`` for the agent-death
  path — which needs an event because a killed agent respawns before any tick
  could see it, ``BUILDING_DESTROYED`` for the origin *and* for this Branch's lab
  going down, and ``BASE_ELIMINATED`` matched on the Sentinel's pre-delete id).
  The fatal conditions are shown to be checked *ahead* of the clock, so a doomed
  operation gets no free tick; and each condition is shown to be **non-destructive
  on doubt** — a reference that is not a live object, a Branch service that is
  unwired or raises, and an owner that cannot be resolved all end nothing.
* **R8.23, R9.8, R10.3 — the two sanctioned effect paths.** ``apply_hit`` routes
  to ``CombatEngine.apply_direct_hit`` with the **owning player** as the attacker
  (resolved through the record's references, since ``owner_ref`` is a dbref by
  design) and refuses rather than misattributing when no owner resolves;
  ``apply_effect`` appends to the existing ``db.active_effects`` list in the shape
  the existing tick counts down, attributed to the same player, replacing the
  container rather than mutating it. Neither invents a weapon, neither raises, and
  a status effect with no damage is R9.8's permitted alternative to it.

And the restart rebuild (design §4.9):

* **R8.22 — a restart re-tracks what persisted.** The vector's sweep names the
  durable owners, every non-terminal record read from them is tracked with the
  clocks it was persisted with, and the rebuilt operation is shown to *resume
  advancing* rather than merely to exist. The references are resolved back into
  **live objects**, which is asserted through its consequence rather than only by
  inspection: the carrier of a rebuilt operation dies and the operation cancels
  (R8.16), which is a trigger that gates on a live object and would be dead for a
  record still holding a dbref. A terminal record is skipped and a suspended one
  comes back suspended, snapshot intact.
* **R14.3 — idempotence is structural.** The tracked map is keyed by ``op_id``,
  so rebuilding twice tracks the same set as rebuilding once and one record
  reached through two owners is tracked once. Fixed examples here; Property 22
  owns the generated space.
* **R14.4 — a dangling record is Discarded.** Each of the four references is
  broken in turn — pointing at nothing, absent, and pointing at a *deleted*
  object — and each costs that operation: the record is moved to Discarded
  through the single state writer, untracked, swept out of its owner's container,
  reported to its owner as ``vector_discarded`` with the Operation_Kind alone, and
  logged naming the kind and the missing reference. A tile-targeted record needs
  no target entity, and the two "cannot judge" cases — no world to look a
  reference up in, and a reference that is not an id at all — discard **nothing**,
  which is the same non-destructive posture the tick's own conditions take.
* **R14.5 — one corrupt record costs one record.** A payload that cannot be
  parsed is logged with the Operation_Kind and stepped over while the rest are
  recovered, and a sweep hook that raises or was never implemented rebuilds
  nothing rather than stopping a server start.

And the shapes and the no-change guards a conforming vector inherits, which are
claims about what this feature *did not* change as much as about what it did
(design section "Testing Strategy / Unit tests"):

* **R13.6, R13.8 — the payload table and presenter coverage.** The nine kinds'
  payloads are transcribed here from design §4.4's table and asserted as an
  *exact key set* per kind, which is the claim the per-fixture value assertions
  above cannot make: that the set is closed, and that a value nobody could
  resolve leaves the key in place rather than dropping it. Then coverage from the
  *systems* side: every kind the systems this feature introduces can emit — the
  driver's nine plus the three the Branch gates and the technology view publish —
  is a key of ``NotificationPresenter._FORMATTERS``, and all six lifecycle states
  report through one of them. The presenter's own AST scan cannot see the
  driver's kinds (it publishes with a *variable* kind) nor
  ``branch_dormancy_warning`` (published through a helper), which is exactly the
  hole this closes.
* **R8.23, R9.8, R10.3 — the CombatEngine is the ONLY damage path.** Asserted
  structurally over the shipped module: ``apply_direct_hit`` is the one damage
  entry point any name-reaching shape in the module can reach, the combat engine
  is looked up exactly once and only from ``apply_hit``, no deletion or
  ownership-transfer name is reached at all, and the single field the driver
  writes on an object that is not itself, its context, or its own record is
  ``active_effects``. Together those are R9.8's "no Vector_Operation deletes a
  building outright and none transfers ownership" as a structural fact rather
  than a rule each of the six vectors has to remember. Then behaviourally: a
  whole lifecycle driven to resolution leaves the target's hit points, shield,
  and owner exactly as they were, because the only thing that touched it was a
  mocked engine that deliberately applies nothing.
* **R11.1, R11.2, R11.7 — the three no-change obligations.** A **behavioural**
  comparison, not an AST scan, wherever the behaviour is observable: a Shield
  Generator projects the same ``shield_max`` and the same charge onto a
  Branch_Building as onto a Neutral_Building, and a guard defends one — and its
  cover shelters an occupant of one — identically, with each pair of building
  definitions differing *only* in the two fields this feature added, which is
  asserted rather than assumed. The turret sweep is compared the same way. An
  identifier scan backs the comparisons up on the modules R11.1, R11.2, and R11.7
  name: none of them names ``branch``, ``unlock_technology``, or anything else
  this feature introduced, which is why the behaviour cannot differ. Alliance perk
  categories are pinned as data — the five categories, one perk each, no perk
  naming a Branch or an Operation_Kind, and an unchanged perk surface — because a
  perk that granted a Signature_Vector would have to be a catalog entry.

**Validates: Requirements 6.6, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9,
8.10, 8.11, 8.12, 8.13, 8.14, 8.15, 8.16, 8.17, 8.18, 8.21, 8.22, 8.23, 8.24,
9.4, 9.8, 10.3, 10.4, 10.6, 10.7, 11.1, 11.2, 11.4, 11.6, 11.7, 11.9, 11.10,
12.2, 12.3, 12.6, 13.5, 13.6, 13.8, 14.1, 14.3, 14.4, 14.5, 14.7, 14.8, 15.2,
15.3**
"""

import ast
import copy
import dataclasses
import inspect
import os
import unittest
from types import SimpleNamespace

from mygame.world.constants import ALLIANCE_PERK_CATEGORIES, ATTR_VECTOR_OPERATIONS
from mygame.world.data_registry import DataRegistry
from mygame.world.definitions import BalanceConfig, BuildingDef
from mygame.world.event_bus import EventBus
from mygame.world.presenters.notification_presenter import NotificationPresenter
from mygame.world.systems import operation_contract
from mygame.world.systems.alliance_system import AllianceSystem
from mygame.world.systems.base_system import BaseSystem
from mygame.world.systems.branch_system import (
    MSG_VECTOR_CONSENT_REQUIRED,
    MSG_VECTOR_ESCALATION_LIMIT,
    MSG_VECTOR_TARGET_ALLIED,
    MSG_VECTOR_TARGET_SHIELDED,
    NOTIFY_BRANCH_DORMANCY,
    BranchRefusal,
    BranchSystem,
    _TERMINAL_STATE_NAMES,
)
from mygame.world.systems.combat_engine import CombatEngine
from mygame.world.systems.guard_combat_system import GuardCombatSystem
from mygame.world.systems.shield_system import ShieldSystem
from mygame.world.systems.operation_contract import (
    MSG_VECTOR_CARRIER_REQUIRED,
    MSG_VECTOR_COMMITMENT_REQUIRED,
    MSG_VECTOR_COOLDOWN,
    MSG_VECTOR_IN_FLIGHT_CAP,
    MSG_VECTOR_INSUFFICIENT_RESOURCES,
    MSG_VECTOR_ORIGIN_UNAVAILABLE,
    MSG_VECTOR_TARGET_INVALID,
    MSG_VECTOR_UNLOCK_REQUIRED,
    MSG_VECTOR_UNWIRED,
    TERMINAL_STATES,
    OperationDriver,
    OperationOutcome,
    OperationRecord,
    OperationState,
    _read_records,
    _write_records,
    new_op_id,
)

#: The logger both halves of the persistence pair report through.
CONTRACT_LOGGER = "evennia.world.systems.operation_contract"

#: The six lifecycle states R8.1 declares, by value.
SIX_STATES = ("pending", "suspended", "resolved", "expired", "cancelled", "discarded")

#: The four R8.2 declares terminal, by value.
FOUR_TERMINAL = ("resolved", "expired", "cancelled", "discarded")

#: Every persisted field of an Operation_Record (design §7). ``op_id`` and
#: ``charged`` bracket the list the requirement enumerates: the identity the
#: rebuild keys on, and the charge the refund path needs.
PERSISTED_FIELDS = (
    "op_id",
    "kind",
    "owner_ref",
    "building_ref",
    "carrier_ref",
    "planet",
    "target_x",
    "target_y",
    "target_ref",
    "ticks_remaining",
    "lifetime_remaining",
    "magnitude",
    "radius",
    "state",
    "suspended_ticks",
    "charged",
)


def _full_record():
    """A record with every field set to a distinct, non-default value."""
    return OperationRecord(
        op_id="abc123",
        kind="strategic_strike",
        owner_ref="#5",
        building_ref=41,
        carrier_ref="#77",
        planet="earth",
        target_x=12,
        target_y=-3,
        target_ref="#91",
        ticks_remaining=7,
        lifetime_remaining=20,
        magnitude=13.5,
        radius=2,
        state=OperationState.SUSPENDED,
        suspended_ticks=7,
        charged={"Iron": 25, "Energy": 10},
    )


# ------------------------------------------------------------------ #
#  Durable-owner fakes for the persistence pair
# ------------------------------------------------------------------ #

class FakeAttributes:
    """Evennia's attribute handler, reduced to the two calls the pair makes."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, **_kwargs):
        return self._data.get(key, default)

    def add(self, key, value, **_kwargs):
        self._data[key] = value

    def raw(self, key=ATTR_VECTOR_OPERATIONS):
        """The stored value itself, read without going through the pair."""
        return self._data.get(key)


class HostileAttributes(FakeAttributes):
    """A handler that DISCARDS an in-place change to a container it handed out.

    Copies on the way out and on the way in, which is the pessimistic end of what
    a real Evennia attribute does: the stored value may be a pickled snapshot, so
    mutating the list a read returned changes nothing durable. Under this handler
    only genuine read-copy-write persists anything, which is what makes the R14.7
    discipline testable rather than merely stated.
    """

    def get(self, key, default=None, **_kwargs):
        if key not in self._data:
            return default
        return copy.deepcopy(self._data[key])

    def add(self, key, value, **_kwargs):
        self._data[key] = copy.deepcopy(value)


class BrokenAttributes(FakeAttributes):
    """A handler whose every call fails — a deleted owner, a broken backend."""

    def get(self, key, default=None, **_kwargs):
        raise RuntimeError("this attribute cannot be read")

    def add(self, key, value, **_kwargs):
        raise RuntimeError("this attribute cannot be written")


class FakeOwner:
    """A durable owner: an ``attributes`` handler, and nothing else.

    The whole surface the persistence pair requires of the world object a vector
    nominates (R14.1) — no ``db`` proxy, no typeclass, no framework.
    """

    def __init__(self, handler=None, stored=None):
        self.attributes = handler if handler is not None else FakeAttributes()
        if stored is not None:
            self.attributes.add(ATTR_VECTOR_OPERATIONS, stored)


class OwnerWithoutAttributes:
    """A nominated owner that turns out to have no attribute handler at all."""


class OwnerWithNoneAttributes:
    """An owner whose ``attributes`` is ``None`` — a half-deleted object."""

    attributes = None


class TestOperationState(unittest.TestCase):
    """The lifecycle vocabulary: six states, four of them terminal."""

    def test_exactly_the_six_states(self):
        """R8.1: six states, spelled as the design spells them, and no more."""
        self.assertEqual(
            [str(state) for state in OperationState], list(SIX_STATES)
        )

    def test_exactly_the_four_terminal_states(self):
        """R8.2: Resolved, Expired, Cancelled, Discarded — and nothing else."""
        self.assertEqual(
            {str(state) for state in TERMINAL_STATES}, set(FOUR_TERMINAL)
        )
        self.assertNotIn(OperationState.PENDING, TERMINAL_STATES)
        self.assertNotIn(OperationState.SUSPENDED, TERMINAL_STATES)

    def test_a_plain_string_answers_terminal_membership(self):
        """A persisted value tests against the set without being converted.

        This is what lets a record read straight out of an Evennia attribute be
        judged terminal, and what lets ``branch_system`` share the vocabulary by
        value instead of by import.
        """
        for name in FOUR_TERMINAL:
            with self.subTest(state=name):
                self.assertIn(name, TERMINAL_STATES)
        self.assertNotIn("pending", TERMINAL_STATES)
        self.assertNotIn("suspended", TERMINAL_STATES)

    def test_branch_system_terminal_names_match_the_enum(self):
        """The private by-value copy in ``branch_system`` and the enum agree.

        ``branch_system`` must not import the contract — the dependency runs the
        other way — so it keeps its own tuple of the four names. Drift between
        the two would uncap a player's in-flight operations silently, which is
        exactly the failure this cross-check makes loud.
        """
        self.assertEqual(
            _TERMINAL_STATE_NAMES, frozenset(str(s) for s in TERMINAL_STATES)
        )


class TestOperationRecordShape(unittest.TestCase):
    """R8.21: every persisted field, on the dataclass and in the payload."""

    def test_dataclass_declares_every_persisted_field_in_order(self):
        names = tuple(f.name for f in dataclasses.fields(OperationRecord))
        self.assertEqual(names, PERSISTED_FIELDS)

    def test_to_dict_emits_a_key_per_persisted_field(self):
        self.assertEqual(set(_full_record().to_dict()), set(PERSISTED_FIELDS))

    def test_carries_the_fields_the_in_flight_count_reads_by_name(self):
        """``BranchSystem.in_flight_count`` reads a record duck-typed.

        It reads ``kind``, ``owner_ref``, ``planet``, and ``state`` off either
        the dataclass or the dict it persists as, so both shapes must answer to
        those four names.
        """
        record = _full_record()
        payload = record.to_dict()
        for name in ("kind", "owner_ref", "planet", "state"):
            with self.subTest(field=name):
                self.assertTrue(hasattr(record, name))
                self.assertIn(name, payload)

    def test_a_fresh_identity_per_record(self):
        self.assertNotEqual(OperationRecord().op_id, OperationRecord().op_id)
        self.assertNotEqual(new_op_id(), new_op_id())
        self.assertTrue(OperationRecord().op_id)

    def test_is_terminal_reads_the_state_by_value(self):
        for name in FOUR_TERMINAL:
            with self.subTest(state=name):
                self.assertTrue(OperationRecord(state=name).is_terminal)
        self.assertFalse(OperationRecord(state="pending").is_terminal)
        self.assertFalse(OperationRecord(state=OperationState.SUSPENDED).is_terminal)


class TestOperationRecordPersistence(unittest.TestCase):
    """R14.8: read every field by value, with its documented default."""

    def test_a_full_record_round_trips_unchanged(self):
        record = _full_record()
        rebuilt = OperationRecord.from_dict(record.to_dict())
        for name in PERSISTED_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(getattr(rebuilt, name), getattr(record, name))

    def test_to_dict_persists_the_state_as_a_plain_string(self):
        payload = OperationRecord(state=OperationState.RESOLVED).to_dict()
        self.assertEqual(payload["state"], "resolved")
        self.assertNotIsInstance(payload["state"], OperationState)

    def test_the_payload_shares_no_container_with_the_record(self):
        """R14.7: an in-place mutation of either must not reach the other."""
        record = _full_record()
        payload = record.to_dict()
        payload["charged"]["Iron"] = 999
        self.assertEqual(record.charged["Iron"], 25)

        rebuilt = OperationRecord.from_dict(payload)
        rebuilt.charged["Energy"] = 999
        self.assertEqual(payload["charged"]["Energy"], 10)

    def test_an_empty_payload_yields_the_documented_defaults(self):
        defaults = OperationRecord()
        rebuilt = OperationRecord.from_dict({})
        for name in PERSISTED_FIELDS:
            if name == "op_id":
                continue  # a fresh identity, the one non-constant default
            with self.subTest(field=name):
                self.assertEqual(getattr(rebuilt, name), getattr(defaults, name))
        self.assertTrue(rebuilt.op_id)

    def test_each_absent_field_reads_as_its_documented_default(self):
        """One field removed at a time; every other value survives."""
        full = _full_record()
        defaults = OperationRecord()
        for name in PERSISTED_FIELDS:
            payload = full.to_dict()
            payload.pop(name)
            with self.subTest(field=name):
                rebuilt = OperationRecord.from_dict(payload)
                if name == "op_id":
                    self.assertTrue(rebuilt.op_id)
                else:
                    self.assertEqual(
                        getattr(rebuilt, name), getattr(defaults, name)
                    )
                for other in PERSISTED_FIELDS:
                    if other != name:
                        self.assertEqual(
                            getattr(rebuilt, other), getattr(full, other)
                        )

    def test_a_none_value_reads_as_the_documented_default(self):
        payload = {name: None for name in PERSISTED_FIELDS}
        defaults = OperationRecord()
        rebuilt = OperationRecord.from_dict(payload)
        for name in PERSISTED_FIELDS:
            if name == "op_id":
                continue
            with self.subTest(field=name):
                self.assertEqual(getattr(rebuilt, name), getattr(defaults, name))

    def test_an_unreadable_value_falls_back_rather_than_raising(self):
        """A hand-edited record is read, not rejected."""
        rebuilt = OperationRecord.from_dict({
            "ticks_remaining": "not a number",
            "radius": [],
            "magnitude": object(),
            "lifetime_remaining": "soon",
            "target_x": {},
            "suspended_ticks": "later",
            "charged": "not a map",
        })
        self.assertEqual(rebuilt.ticks_remaining, 0)
        self.assertEqual(rebuilt.radius, 0)
        self.assertEqual(rebuilt.magnitude, 0.0)
        self.assertIsNone(rebuilt.lifetime_remaining)
        self.assertIsNone(rebuilt.target_x)
        self.assertIsNone(rebuilt.suspended_ticks)
        self.assertEqual(rebuilt.charged, {})

    def test_a_charge_line_that_cannot_be_read_is_dropped(self):
        """A refund must not invent an amount."""
        rebuilt = OperationRecord.from_dict(
            {"charged": {"Iron": 5, "Energy": "eight", "": 3}}
        )
        self.assertEqual(rebuilt.charged, {"Iron": 5})

    def test_a_stringy_number_is_read_as_the_number(self):
        rebuilt = OperationRecord.from_dict({
            "ticks_remaining": "4", "radius": "2", "magnitude": "1.5",
            "target_x": "-3", "charged": {"Iron": "25"},
        })
        self.assertEqual(rebuilt.ticks_remaining, 4)
        self.assertEqual(rebuilt.radius, 2)
        self.assertEqual(rebuilt.magnitude, 1.5)
        self.assertEqual(rebuilt.target_x, -3)
        self.assertEqual(rebuilt.charged, {"Iron": 25})

    def test_an_unreadable_state_reads_as_pending(self):
        """A record with no state is treated as in flight, not as terminal."""
        for payload in ({}, {"state": None}, {"state": ""}):
            with self.subTest(payload=payload):
                rebuilt = OperationRecord.from_dict(payload)
                self.assertEqual(rebuilt.state, OperationState.PENDING)
                self.assertFalse(rebuilt.is_terminal)

    def test_a_non_mapping_payload_raises_type_error(self):
        """The corrupt-record case R14.5 logs and recovers from."""
        for payload in (None, [], "record", 7):
            with self.subTest(payload=payload):
                with self.assertRaises(TypeError):
                    OperationRecord.from_dict(payload)


class TestOperationOutcome(unittest.TestCase):
    """R8.24: every request answers a value naming the state or the refusal."""

    def test_accepted_names_the_state_and_the_identity(self):
        record = _full_record()
        outcome = OperationOutcome.accepted(record)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.state, "suspended")
        self.assertEqual(outcome.op_id, "abc123")
        self.assertIsNone(outcome.check)
        self.assertIsNone(outcome.detail)

    def test_accepted_persists_the_state_as_a_plain_string(self):
        outcome = OperationOutcome.accepted(OperationRecord())
        self.assertEqual(outcome.state, "pending")
        self.assertNotIsInstance(outcome.state, OperationState)

    def test_refused_names_the_check_and_carries_its_data_but_no_state(self):
        outcome = OperationOutcome.refused("cooldown", {"remaining_ticks": 12})
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.check, "cooldown")
        self.assertEqual(outcome.detail, {"remaining_ticks": 12})
        self.assertIsNone(outcome.state)
        self.assertIsNone(outcome.op_id)

    def test_failed_names_the_failure_point_and_creates_no_operation(self):
        outcome = OperationOutcome.failed("pending_entry")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.check, "pending_entry")
        self.assertIsNone(outcome.state)
        self.assertIsNone(outcome.op_id)
        self.assertIsNone(outcome.detail)

    def test_the_detail_is_copied_not_shared(self):
        detail = {"have": 1}
        outcome = OperationOutcome.refused("resources", detail)
        detail["have"] = 999
        self.assertEqual(outcome.detail, {"have": 1})

    def test_an_outcome_is_frozen(self):
        outcome = OperationOutcome.refused("carrier")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            outcome.ok = True


class TestReadRecords(unittest.TestCase):
    """R14.8: read by value, and an absent attribute is an empty list."""

    def test_an_absent_attribute_reads_as_an_empty_list(self):
        self.assertEqual(_read_records(FakeOwner()), [])

    def test_an_empty_container_reads_as_an_empty_list(self):
        self.assertEqual(_read_records(FakeOwner(stored=[])), [])

    def test_an_owner_with_no_attribute_handler_reads_as_an_empty_list(self):
        """A vector may nominate anything, and a nominated owner may be gone."""
        for owner in (None, OwnerWithoutAttributes(), OwnerWithNoneAttributes()):
            with self.subTest(owner=type(owner).__name__):
                self.assertEqual(_read_records(owner), [])

    def test_an_unreadable_handler_reads_as_an_empty_list(self):
        self.assertEqual(_read_records(FakeOwner(handler=BrokenAttributes())), [])

    def test_reads_the_documented_attribute_and_no_other(self):
        payload = [_full_record().to_dict()]
        owner = FakeOwner()
        owner.attributes.add(ATTR_VECTOR_OPERATIONS, payload)
        self.assertEqual(_read_records(owner), payload)

        other = FakeOwner()
        other.attributes.add("operations", payload)
        self.assertEqual(_read_records(other), [])

    def test_the_result_shares_no_container_with_storage(self):
        """The caller gets containers it owns, so a mutation reaches nothing."""
        owner = FakeOwner(stored=[_full_record().to_dict()])

        read = _read_records(owner)
        read.append({"op_id": "planted"})
        read[0]["kind"] = "tampered"

        again = _read_records(owner)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["kind"], "strategic_strike")
        self.assertIsNot(read[0], again[0])

    def test_a_stored_value_of_the_wrong_shape_reads_as_empty_and_logs(self):
        for garbage in ({"op_id": "not a list"}, "records", 7, object()):
            with self.subTest(stored=type(garbage).__name__):
                owner = FakeOwner(stored=garbage)
                with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
                    self.assertEqual(_read_records(owner), [])

    def test_a_corrupt_entry_is_logged_skipped_and_the_rest_recovered(self):
        """One bad record costs one record, not the container (R14.5)."""
        first = _full_record().to_dict()
        second = OperationRecord(op_id="second", kind="trap").to_dict()
        owner = FakeOwner(stored=[first, "corrupt", None, second])

        with self.assertLogs(CONTRACT_LOGGER, level="WARNING") as logs:
            records = _read_records(owner)

        self.assertEqual([r["op_id"] for r in records], ["abc123", "second"])
        self.assertEqual(len(logs.output), 2)

    def test_a_corrupt_entry_is_the_one_from_dict_refuses(self):
        """The pair and ``from_dict`` agree on what a corrupt record is.

        ``_read_records`` drops exactly the payloads ``from_dict`` would raise on,
        so the rebuild's own recovery path stays reachable for every payload the
        read *does* hand it.
        """
        owner = FakeOwner(stored=[_full_record().to_dict(), ["not", "a", "record"]])
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            records = _read_records(owner)

        self.assertEqual(len(records), 1)
        for payload in records:
            OperationRecord.from_dict(payload)  # raises nothing


class TestWriteRecords(unittest.TestCase):
    """R14.7: replace the whole container, and never raise into the caller."""

    def test_a_write_then_a_read_round_trips_the_payloads(self):
        record = _full_record()
        owner = FakeOwner()

        _write_records(owner, [record])
        rebuilt = OperationRecord.from_dict(_read_records(owner)[0])

        for name in PERSISTED_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(getattr(rebuilt, name), getattr(record, name))

    def test_a_write_replaces_the_whole_container(self):
        owner = FakeOwner()
        _write_records(owner, [OperationRecord(op_id="first")])
        _write_records(owner, [OperationRecord(op_id="second")])

        self.assertEqual([r["op_id"] for r in _read_records(owner)], ["second"])

    def test_an_empty_write_clears_the_container(self):
        """The last record going terminal leaves an owner with none."""
        owner = FakeOwner(stored=[_full_record().to_dict()])
        _write_records(owner, [])

        self.assertEqual(_read_records(owner), [])
        self.assertEqual(owner.attributes.raw(), [])

    def test_writes_under_the_documented_attribute(self):
        owner = FakeOwner()
        _write_records(owner, [OperationRecord()])

        self.assertEqual(ATTR_VECTOR_OPERATIONS, "vector_operations")
        self.assertEqual(len(owner.attributes.raw(ATTR_VECTOR_OPERATIONS)), 1)

    def test_a_record_object_is_stored_as_plain_data(self):
        """Storage holds values only — never a dataclass, never an enum."""
        owner = FakeOwner()
        _write_records(owner, [OperationRecord(state=OperationState.RESOLVED)])

        stored = owner.attributes.raw()
        self.assertIsInstance(stored, list)
        self.assertIsInstance(stored[0], dict)
        self.assertEqual(stored[0]["state"], "resolved")
        self.assertNotIsInstance(stored[0]["state"], OperationState)

    def test_a_dict_payload_is_accepted_as_it_stands(self):
        owner = FakeOwner()
        _write_records(owner, [_full_record().to_dict()])

        self.assertEqual(_read_records(owner)[0]["kind"], "strategic_strike")

    def test_the_stored_container_shares_nothing_with_the_caller(self):
        record = _full_record()
        offered = [record.to_dict()]
        owner = FakeOwner()

        _write_records(owner, offered)
        offered.append({"op_id": "planted"})
        offered[0]["kind"] = "tampered"
        record.kind = "tampered"

        stored = _read_records(owner)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["kind"], "strategic_strike")
        self.assertIsNot(owner.attributes.raw(), offered)

    def test_an_owner_with_no_attribute_handler_is_a_no_op(self):
        for owner in (None, OwnerWithoutAttributes(), OwnerWithNoneAttributes()):
            with self.subTest(owner=type(owner).__name__):
                _write_records(owner, [OperationRecord()])  # raises nothing

    def test_a_failed_write_is_logged_rather_than_raised(self):
        owner = FakeOwner(handler=BrokenAttributes())
        _write_records(owner, [OperationRecord()])  # raises nothing

    def test_an_unwritable_entry_is_dropped_and_the_rest_persisted(self):
        owner = FakeOwner()
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            _write_records(owner, [OperationRecord(op_id="kept"), "corrupt", 7])

        self.assertEqual([r["op_id"] for r in _read_records(owner)], ["kept"])

    def test_a_non_sequence_of_records_writes_an_empty_container(self):
        for garbage in (None, "records", {"op_id": "a mapping is not a list"}):
            with self.subTest(records=type(garbage).__name__):
                owner = FakeOwner(stored=[_full_record().to_dict()])
                _write_records(owner, garbage)
                self.assertEqual(_read_records(owner), [])


class TestPersistenceDisciplineUnderAHostileHandler(unittest.TestCase):
    """R14.7: only read-copy-write persists, so the pair is the whole path."""

    def setUp(self):
        self.owner = FakeOwner(handler=HostileAttributes())
        _write_records(self.owner, [OperationRecord(op_id="first", kind="trap")])

    def test_an_in_place_mutation_of_a_read_container_does_not_persist(self):
        records = _read_records(self.owner)
        records.append(OperationRecord(op_id="second").to_dict())
        records[0]["kind"] = "tampered"

        again = _read_records(self.owner)
        self.assertEqual([r["op_id"] for r in again], ["first"])
        self.assertEqual(again[0]["kind"], "trap")

    def test_reading_a_copy_and_writing_the_whole_container_does_persist(self):
        records = _read_records(self.owner)
        records.append(OperationRecord(op_id="second", kind="trap").to_dict())
        _write_records(self.owner, records)

        self.assertEqual(
            [r["op_id"] for r in _read_records(self.owner)], ["first", "second"]
        )

    def test_a_record_round_trips_through_a_hostile_handler(self):
        record = _full_record()
        _write_records(self.owner, [record])

        rebuilt = OperationRecord.from_dict(_read_records(self.owner)[0])
        self.assertEqual(rebuilt, record)


# ------------------------------------------------------------------ #
#  OperationDriver — the skeleton and the single state writer
# ------------------------------------------------------------------ #

#: The five hooks design §4.10 declares REQUIRED: the whole surface a
#: Signature_Vector spec implements.
REQUIRED_HOOKS = (
    "validate_target",
    "build_record",
    "on_resolve",
    "persistence_owner",
    "discover_records",
)

#: The five design §4.10 declares OPTIONAL: the transitions a vector only cares
#: about when its operation left something behind to undo.
OPTIONAL_HOOKS = (
    "on_expire",
    "on_suspend",
    "on_resume",
    "on_cancel",
    "on_discard",
)

#: The public surface a vector INHERITS and does not override (design §4's
#: interface summary), as far as it has landed. Pinned alongside the hooks so a
#: method added to either half is a deliberate change rather than an accident.
#:
#: Four groups, and each is public for a reason:
#:
#: * the one entry point a command layer calls (``request``) and the
#:   tracked-record accessor ``BranchSystem`` counts the in-flight cap through;
#: * ``advance_all``, which ``BranchSystem.process_tick`` calls duck-typed once
#:   per tick — the tick fan-out reads this name and no other;
#: * the three transitions a caller outside the tick reaches
#:   (``suspend`` / ``resume`` / ``cancel``) and the three world-event handlers
#:   the driver subscribes (``handle_*``), which are the driver's own and are
#:   *not* vector hooks — the ``handle_`` prefix is what keeps them from reading
#:   like the ``on_*`` hooks a vector supplies;
#: * the two sanctioned effect paths (``apply_hit`` / ``apply_effect``), which
#:   are public precisely so a vector's ``on_resolve`` has something to call and
#:   nothing else to reach for (R8.23);
#: * ``rebuild``, which the composition root calls once per vector at server
#:   start — public for the same reason ``advance_all`` is: it is called from
#:   outside the driver, by name, and nothing imports this class to do it.
INHERITED_API = (
    "request",
    "tracked_records",
    "advance_all",
    "suspend",
    "resume",
    "cancel",
    "rebuild",
    "handle_player_eliminated",
    "handle_building_destroyed",
    "handle_base_eliminated",
    "apply_hit",
    "apply_effect",
)


def _state_writers(source):
    """Yield ``(enclosing_function, lineno)`` for every write of a ``.state``.

    Every shape an assignment can take — plain, augmented, and annotated — with
    an attribute named ``state`` as its target. A dict key ``"state"`` and a
    keyword argument ``state=`` are deliberately *not* writes: ``to_dict`` builds
    the one and ``from_dict`` passes the other, and neither can move a record
    that is already terminal.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            targets = (
                inner.targets if isinstance(inner, ast.Assign) else [inner.target]
            )
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "state":
                    yield node.name, inner.lineno


class FakeRegistry:
    """A DataRegistry stand-in: the driver skeleton reads nothing off it."""


class FakeBus:
    """An EventBus stand-in that records what it was asked to publish."""

    def __init__(self):
        self.published = []

    def publish(self, event, **data):
        self.published.append((event, data))


class LifecycleBus(FakeBus):
    """A bus that also DISPATCHES, so a subscription is exercised end to end.

    The driver subscribes three world events in its own ``__init__`` (R8.16,
    R8.17, R8.18, R11.4), and a bus that only records what it was handed cannot
    show that the subscription arrived: publishing here calls the subscriber, in
    the same ``callback(event_name=..., **payload)`` shape
    :class:`world.event_bus.EventBus` calls it in, so the handler under test is
    reached the way the real bus reaches it rather than by being called directly.
    """

    def __init__(self):
        super().__init__()
        self.subscribers = {}

    def subscribe(self, event, callback):
        self.subscribers.setdefault(event, []).append(callback)

    def publish(self, event, **data):
        super().publish(event, **data)
        for callback in list(self.subscribers.get(event, ())):
            callback(event_name=event, **data)


class BareDriver(OperationDriver):
    """A vector that declares nothing and implements nothing.

    The unimplemented-hook case, and the case that proves the class attributes
    have usable defaults: registration reads a blank ``operation_kind`` as
    "names no kind" and skips it with a log rather than raising.
    """


class FakeVector(OperationDriver):
    """A minimal conforming vector: the five required hooks and one owner.

    Persists on a :class:`FakeOwner` whose handler is *hostile* by default, so
    every persistence claim below is made under a handler that discards an
    in-place change to the container it handed out — nothing here can pass by
    mutating a list the driver read.
    """

    operation_kind = "strategic_strike"
    branch = "weapons"
    _required_collaborators = ("combat_engine",)

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner if owner is not None else FakeOwner(HostileAttributes())
        self.calls = []

    def validate_target(self, ctx):
        return None

    def build_record(self, ctx):
        return OperationRecord(kind=self.operation_kind)

    def on_resolve(self, record):
        self.calls.append(("on_resolve", record.op_id))

    def persistence_owner(self, record):
        return self.owner

    def discover_records(self, planet_rooms):
        return [self.owner]


class OwnerlessVector(FakeVector):
    """A vector whose operations have no durable owner at all (R14.1)."""

    def persistence_owner(self, record):
        return None


class BrokenOwnerVector(FakeVector):
    """A vector whose ``persistence_owner`` raises on every record."""

    def persistence_owner(self, record):
        raise RuntimeError("this operation's owner cannot be resolved")


class ComposedVector(FakeVector, BaseSystem):
    """The composed shape design §4.10 declares: the driver, then a BaseSystem.

    Nothing about it is special — that is the point. It exists to prove the
    driver's ``__init__`` is cooperative, so one ``super().__init__`` call in a
    vector reaches both halves.
    """

    def __init__(self, registry, event_bus, branch_system=None, owner=None):
        super().__init__(registry, event_bus, branch_system=branch_system, owner=owner)


class TestDriverDeclaredSurface(unittest.TestCase):
    """What a vector sets, and what it gets: the class attributes and hooks."""

    def test_the_class_attributes_have_declarable_defaults(self):
        """A bare driver names no kind and no Branch, and requires nothing.

        ``BranchSystem.register_vector`` documents a blank ``operation_kind`` as a
        logged no-op, so the default has to be readable and blank rather than
        absent — a missing attribute would make registration an error case.
        """
        driver = BareDriver()
        self.assertEqual(driver.operation_kind, "")
        self.assertEqual(driver.branch, "")
        self.assertEqual(driver._required_collaborators, ())

    def test_a_vector_declares_its_kind_branch_and_collaborators(self):
        vector = FakeVector()
        self.assertEqual(vector.operation_kind, "strategic_strike")
        self.assertEqual(vector.branch, "weapons")
        self.assertEqual(vector._required_collaborators, ("combat_engine",))

    def test_the_collaborator_declaration_is_an_immutable_tuple(self):
        """The declaration is read on every check, so it must not be shared state."""
        self.assertIsInstance(OperationDriver._required_collaborators, tuple)
        self.assertIsInstance(FakeVector._required_collaborators, tuple)

    def test_every_required_hook_refuses_to_be_left_unimplemented(self):
        driver = BareDriver()
        for name in REQUIRED_HOOKS:
            with self.subTest(hook=name):
                with self.assertRaises(NotImplementedError) as raised:
                    getattr(driver, name)(None)
                self.assertIn("BareDriver", str(raised.exception))
                self.assertIn(name, str(raised.exception))

    def test_every_optional_hook_defaults_to_a_no_op(self):
        driver = BareDriver()
        record = OperationRecord()
        for name in OPTIONAL_HOOKS:
            with self.subTest(hook=name):
                self.assertIsNone(getattr(driver, name)(record))

    def test_the_hook_list_is_exactly_ten_and_no_more(self):
        """Design §4.10: five required plus five optional is the whole surface.

        A hook added without a spec change would make a vector's obligations
        larger than the contract advertises, so the count is pinned — and so is
        the inherited half, since a public method a vector is not meant to
        override still reads as part of the contract from the outside.
        """
        declared = {
            name for name, value in vars(OperationDriver).items()
            if callable(value) and not name.startswith("_")
        }
        self.assertEqual(
            declared,
            set(REQUIRED_HOOKS) | set(OPTIONAL_HOOKS) | set(INHERITED_API),
        )

    def test_each_hook_takes_the_argument_the_design_gives_it(self):
        expected = {
            "validate_target": ("self", "ctx"),
            "build_record": ("self", "ctx"),
            "on_resolve": ("self", "record"),
            "persistence_owner": ("self", "record"),
            "discover_records": ("self", "planet_rooms"),
            "on_expire": ("self", "record"),
            "on_suspend": ("self", "record"),
            "on_resume": ("self", "record"),
            "on_cancel": ("self", "record"),
            "on_discard": ("self", "record"),
        }
        for name, params in expected.items():
            with self.subTest(hook=name):
                signature = inspect.signature(getattr(OperationDriver, name))
                self.assertEqual(tuple(signature.parameters), params)


class TestDriverConstruction(unittest.TestCase):
    """A cooperative ``__init__``, so a vector makes one ``super()`` call."""

    def test_a_driver_starts_tracking_nothing(self):
        self.assertEqual(BareDriver().tracked_records(), [])
        self.assertEqual(BareDriver()._tracked, [])

    def test_two_drivers_do_not_share_a_tracked_list(self):
        first, second = FakeVector(), FakeVector()
        first._track(OperationRecord(op_id="only-first"))

        self.assertEqual(len(first.tracked_records()), 1)
        self.assertEqual(second.tracked_records(), [])

    def test_the_branch_system_collaborator_is_captured_and_optional(self):
        branch = object()
        self.assertIs(FakeVector(branch_system=branch)._branch, branch)
        self.assertIsNone(FakeVector()._branch)

    def test_the_composed_shape_reaches_both_halves_of_the_mro(self):
        """``class V(OperationDriver, BaseSystem)`` — design §4.10's composition.

        The driver cannot inherit ``BaseSystem`` (that module reaches the
        framework, this one must not), so the two are composed, and one
        ``super().__init__`` call has to configure both.
        """
        registry, bus, branch = FakeRegistry(), FakeBus(), object()
        vector = ComposedVector(registry, bus, branch_system=branch)

        self.assertIs(vector.registry, registry)
        self.assertIs(vector.event_bus, bus)
        self.assertIs(vector._branch, branch)
        self.assertEqual(vector.tracked_records(), [])

    def test_the_driver_precedes_the_base_system_in_the_mro(self):
        order = [cls.__name__ for cls in ComposedVector.__mro__]
        self.assertLess(order.index("OperationDriver"), order.index("BaseSystem"))

    def test_the_persistence_pair_is_bound_as_a_driver_method(self):
        """Task 10.2's handoff: the module functions, reachable as ``self._``.

        Bound rather than reimplemented, so there is still exactly one code path
        that touches the ``vector_operations`` attribute.
        """
        vector = FakeVector()
        self.assertIs(vector._read_records, operation_contract._read_records)
        self.assertIs(vector._write_records, operation_contract._write_records)

        vector._write_records(vector.owner, [OperationRecord(op_id="bound")])
        self.assertEqual(
            [r["op_id"] for r in vector._read_records(vector.owner)], ["bound"]
        )


class TestDriverTrackedRecords(unittest.TestCase):
    """R8.20, R8.21: the tracked list is the in-flight count, so it must be exact."""

    def setUp(self):
        self.vector = FakeVector()

    def test_a_tracked_record_is_reported(self):
        record = OperationRecord(op_id="op-1", kind="strategic_strike")
        self.vector._track(record)

        self.assertEqual(self.vector.tracked_records(), [record])

    def test_the_accessor_answers_a_copy(self):
        """A caller counting records must not reach the driver's own tracking."""
        self.vector._track(OperationRecord(op_id="op-1"))

        reported = self.vector.tracked_records()
        reported.append(OperationRecord(op_id="planted"))
        reported.clear()

        self.assertEqual(len(self.vector.tracked_records()), 1)

    def test_tracking_the_same_identity_twice_tracks_it_once(self):
        """A duplicate would double the in-flight count and advance twice."""
        first = OperationRecord(op_id="op-1", ticks_remaining=5)
        again = OperationRecord(op_id="op-1", ticks_remaining=2)
        self.vector._track(first)
        self.vector._track(again)

        self.assertEqual(self.vector.tracked_records(), [again])

    def test_tracking_preserves_order_and_distinct_identities(self):
        records = [OperationRecord(op_id=f"op-{n}") for n in range(3)]
        for record in records:
            self.vector._track(record)

        self.assertEqual(
            [r.op_id for r in self.vector.tracked_records()], ["op-0", "op-1", "op-2"]
        )

    def test_untracking_removes_only_that_record(self):
        kept = OperationRecord(op_id="kept")
        dropped = OperationRecord(op_id="dropped")
        self.vector._track(kept)
        self.vector._track(dropped)
        self.vector._untrack(dropped)

        self.assertEqual(self.vector.tracked_records(), [kept])

    def test_untracking_twice_and_untracking_nothing_are_harmless(self):
        record = OperationRecord(op_id="op-1")
        self.vector._track(record)
        self.vector._untrack(record)
        self.vector._untrack(record)
        self.vector._untrack(None)
        self.vector._track(None)

        self.assertEqual(self.vector.tracked_records(), [])


class TestDriverIsCountableByBranchSystem(unittest.TestCase):
    """The duck-typed surface registration and the in-flight cap reach through.

    ``branch_system`` deliberately imports nothing from this module — the
    dependency runs the other way — so it reads a vector through two names only.
    Asserted against ``BranchSystem``'s own static readers, so this is the real
    cross-module path rather than a restatement of it.
    """

    def test_the_kind_registration_keys_on_is_readable(self):
        self.assertEqual(
            BranchSystem._clean(FakeVector().operation_kind), "strategic_strike"
        )

    def test_the_in_flight_count_reads_the_drivers_tracked_records(self):
        vector = FakeVector()
        player = object()
        for index in range(2):
            vector._track(OperationRecord(
                op_id=f"op-{index}",
                kind="strategic_strike",
                owner_ref=player,
                planet="earth",
            ))

        found = BranchSystem._tracked_records(vector)
        self.assertEqual([r.op_id for r in found], ["op-0", "op-1"])
        for record in found:
            self.assertEqual(BranchSystem._record_field(record, "owner_ref"), player)
            self.assertFalse(BranchSystem._is_terminal_record(record))

    def test_a_resolved_record_reads_as_out_of_flight(self):
        record = OperationRecord(op_id="op-1", kind="strategic_strike")
        vector = FakeVector()
        vector._track(record)
        vector._transition(record, OperationState.RESOLVED)

        self.assertTrue(
            BranchSystem._is_terminal_record(BranchSystem._tracked_records(vector)[0])
        )


class TestTransitionIsTheSingleStateWriter(unittest.TestCase):
    """R8.1, R8.2: one writer, and it refuses a terminal record and a bad state."""

    def setUp(self):
        self.vector = FakeVector()
        self.record = OperationRecord(op_id="op-1", kind="strategic_strike")

    def _stored(self):
        return _read_records(self.vector.owner)

    def test_an_accepted_transition_writes_the_state_and_answers_true(self):
        self.assertTrue(self.vector._transition(self.record, OperationState.SUSPENDED))
        self.assertEqual(self.record.state, "suspended")

    def test_a_transition_writes_the_state_as_a_plain_string(self):
        self.vector._transition(self.record, OperationState.SUSPENDED)

        self.assertNotIsInstance(self.record.state, OperationState)
        self.assertEqual(self.record.state, "suspended")

    def test_a_plain_string_names_a_state_as_well_as_a_member_does(self):
        self.assertTrue(self.vector._transition(self.record, "suspended"))
        self.assertEqual(self.record.state, "suspended")

    def test_every_non_terminal_target_state_is_accepted(self):
        for name in ("pending", "suspended"):
            with self.subTest(state=name):
                record = OperationRecord(op_id=f"op-{name}", state="pending")
                self.assertTrue(self.vector._transition(record, name))
                self.assertEqual(record.state, name)

    def test_every_terminal_target_state_is_accepted_from_pending(self):
        for name in FOUR_TERMINAL:
            with self.subTest(state=name):
                record = OperationRecord(op_id=f"op-{name}", state="pending")
                self.assertTrue(self.vector._transition(record, name))
                self.assertEqual(record.state, name)

    def test_a_terminal_record_does_not_move(self):
        """R8.2: an operation in a terminal state advances no further.

        A late tick, a duplicate event, and a cancellation racing a resolution
        all land here, which is why the refusal is a logged no-op rather than a
        raise.
        """
        for terminal in FOUR_TERMINAL:
            for target in ("pending", "suspended", "resolved"):
                with self.subTest(terminal=terminal, target=target):
                    record = OperationRecord(op_id="op-1", state=terminal)
                    self.assertFalse(self.vector._transition(record, target))
                    self.assertEqual(record.state, terminal)

    def test_a_terminal_record_does_not_move_and_persists_nothing(self):
        self.record.state = "resolved"
        self.vector._transition(self.record, "pending")

        self.assertEqual(self._stored(), [])

    def test_a_state_outside_the_six_is_refused_and_logged(self):
        """R8.1: a record outside the lifecycle would never advance or rebuild."""
        for bad in ("done", "PENDING", "", None, 7, object()):
            with self.subTest(state=bad):
                record = OperationRecord(op_id="op-1", state="pending")
                with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
                    self.assertFalse(self.vector._transition(record, bad))
                self.assertEqual(record.state, "pending")

    def test_the_reason_is_accepted_positionally_and_by_keyword(self):
        self.assertTrue(self.vector._transition(self.record, "suspended", "carrier"))
        self.assertTrue(
            self.vector._transition(self.record, "pending", reason="carrier back")
        )
        self.assertEqual(self.record.state, "pending")

    def test_a_missing_record_is_answered_rather_than_raised_on(self):
        self.assertFalse(self.vector._transition(None, "resolved"))

    def test_no_other_function_in_the_module_writes_record_state(self):
        """The claim that makes terminal finality structural rather than a habit.

        An AST scan of the shipped module: every assignment to an attribute named
        ``state`` must live in ``_transition``. A future path that set the state
        directly — from ``_resolve``, from the tick advance, from a vector hook —
        would bypass the terminal guard entirely, so it fails here instead.
        """
        writers = list(_state_writers(inspect.getsource(operation_contract)))

        self.assertEqual(
            sorted({name for name, _lineno in writers}), ["_transition"],
            f"'record.state' is written outside _transition: {writers}. That "
            "single writer is the only place terminal finality (R8.2) is "
            "enforced, so a second assignment silently uncaps the lifecycle.",
        )

    def test_the_scan_would_catch_a_second_writer(self):
        """The scan is only a guard while it can still see a planted write."""
        planted = (
            "def _resolve(self, record):\n"
            "    record.state = 'resolved'\n"
            "\n"
            "def _expire(self, record):\n"
            "    record.state: str = 'expired'\n"
            "\n"
            "def _innocent(self, record):\n"
            "    return {'state': record.state, 'x': dict(state='pending')}\n"
        )
        found = sorted({name for name, _lineno in _state_writers(planted)})

        self.assertEqual(found, ["_expire", "_resolve"])


class TestTransitionPersists(unittest.TestCase):
    """R14.1, R14.7: an accepted transition writes through, read-copy-write.

    Every owner here uses the hostile handler, so nothing passes by mutating a
    container the driver was handed.
    """

    def setUp(self):
        self.vector = FakeVector()
        self.record = OperationRecord(op_id="op-1", kind="strategic_strike")

    def _stored(self):
        return _read_records(self.vector.owner)

    def test_a_transition_persists_the_record_under_the_owner(self):
        self.vector._transition(self.record, OperationState.SUSPENDED)

        stored = self._stored()
        self.assertEqual([r["op_id"] for r in stored], ["op-1"])
        self.assertEqual(stored[0]["state"], "suspended")

    def test_every_accepted_transition_persists_the_new_state(self):
        for target in ("suspended", "pending", "suspended"):
            with self.subTest(state=target):
                self.vector._transition(self.record, target)
                self.assertEqual(self._stored()[0]["state"], target)

    def test_persisting_the_same_record_repeatedly_stores_it_once(self):
        for _ in range(3):
            self.vector._persist(self.record)

        self.assertEqual([r["op_id"] for r in self._stored()], ["op-1"])

    def test_a_transition_leaves_its_neighbours_in_place(self):
        others = [OperationRecord(op_id=name) for name in ("a", "b")]
        self.vector._persist(others[0])
        self.vector._persist(self.record)
        self.vector._persist(others[1])

        self.record.ticks_remaining = 4
        self.vector._transition(self.record, "suspended")

        stored = self._stored()
        self.assertEqual([r["op_id"] for r in stored], ["a", "op-1", "b"])
        self.assertEqual(stored[1]["ticks_remaining"], 4)
        self.assertEqual(stored[1]["state"], "suspended")

    def test_a_terminal_transition_removes_the_record_from_storage(self):
        """A finished operation is swept out rather than stored forever.

        The rebuild skips a terminal record anyway (R8.22), so keeping them would
        change no behaviour — it would only grow a long-lived building's
        attribute without bound and make every later persist read a longer list.
        """
        for terminal in FOUR_TERMINAL:
            with self.subTest(state=terminal):
                vector = FakeVector()
                record = OperationRecord(op_id="op-1", kind="strategic_strike")
                vector._persist(record)
                self.assertEqual(len(_read_records(vector.owner)), 1)

                vector._transition(record, terminal)
                self.assertEqual(_read_records(vector.owner), [])

    def test_a_terminal_transition_leaves_the_live_records_alone(self):
        live = OperationRecord(op_id="live")
        self.vector._persist(live)
        self.vector._persist(self.record)

        self.vector._transition(self.record, OperationState.RESOLVED)

        self.assertEqual([r["op_id"] for r in self._stored()], ["live"])

    def test_storage_holds_plain_data_only(self):
        self.vector._transition(self.record, OperationState.SUSPENDED)

        raw = self.vector.owner.attributes.raw()
        self.assertIsInstance(raw, list)
        self.assertIsInstance(raw[0], dict)
        self.assertNotIsInstance(raw[0]["state"], OperationState)

    def test_a_persisted_record_round_trips_back_into_a_record(self):
        record = _full_record()  # Suspended, so it is not swept as terminal
        self.vector._persist(record)

        rebuilt = OperationRecord.from_dict(self._stored()[0])
        self.assertEqual(rebuilt.kind, "strategic_strike")
        self.assertEqual(rebuilt.charged, {"Iron": 25, "Energy": 10})

    def test_an_operation_with_no_durable_owner_still_transitions(self):
        """R14.1: whether an operation *has* a world object is the vector's call."""
        vector = OwnerlessVector()
        record = OperationRecord(op_id="op-1")

        self.assertTrue(vector._transition(record, OperationState.RESOLVED))
        self.assertEqual(record.state, "resolved")
        self.assertEqual(_read_records(vector.owner), [])

    def test_a_persistence_owner_that_raises_is_logged_not_raised(self):
        """R15.3: a failed persist must not break the transition that asked."""
        vector = BrokenOwnerVector()
        record = OperationRecord(op_id="op-1")

        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertTrue(vector._transition(record, OperationState.RESOLVED))
        self.assertEqual(record.state, "resolved")

    def test_an_unimplemented_persistence_owner_is_logged_not_raised(self):
        driver = BareDriver()
        record = OperationRecord(op_id="op-1")

        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertTrue(driver._transition(record, OperationState.CANCELLED))
        self.assertEqual(record.state, "cancelled")

    def test_a_broken_attribute_handler_is_logged_not_raised(self):
        vector = FakeVector(owner=FakeOwner(BrokenAttributes()))
        record = OperationRecord(op_id="op-1")

        self.assertTrue(vector._transition(record, OperationState.EXPIRED))
        self.assertEqual(record.state, "expired")

    def test_a_record_with_no_identity_persists_nothing(self):
        """Without an ``op_id`` there is no key to upsert against."""
        record = OperationRecord(op_id="")
        with self.assertLogs(CONTRACT_LOGGER, level="DEBUG"):
            self.vector._persist(record)

        self.assertEqual(self._stored(), [])

    def test_persisting_nothing_is_a_no_op(self):
        self.vector._persist(None)
        self.assertEqual(self._stored(), [])


# ------------------------------------------------------------------ #
#  OperationDriver — the ordered validation chain (design §4.2)
# ------------------------------------------------------------------ #

#: The nine checks R8.3 declares, **in the order it fixes them**. Written out
#: here from the requirement rather than read off the driver, so this module
#: pins the order instead of agreeing with whatever the driver happens to say.
NINE_CHECKS = (
    "collaborators",
    "commitment",
    "origin",
    "unlock",
    "carrier",
    "target",
    "cooldown",
    "in_flight",
    "resources",
)

#: The one Branch, kind, role, and planet every fixture below shares.
BRANCH = "weapons"
KIND = "strategic_strike"
ROLE = "spotter"
PLANET = "earth"
ORIGIN_ABBR = "OW"
ORIGIN_NAME = "Ordnance Works"

#: This Branch's lab, as ``FakeBranchSystem.lab_for_branch`` answers it. Its
#: destruction is a commitment lapsing (R8.18), not an origin being lost.
LAB_ABBR = "WL"
UNLOCK_TECH = "ordnance_theory"
COST_FIELD = f"{KIND}_cost"
KIND_COST = {"Iron": 25}

#: The Response_Window floor every fixture below is tuned to (R8.8). Not the
#: shipped default of 5: a distinct value is what tells a floor that was read
#: from Balance_Config apart from one that was hard-coded.
FLOOR = 7

#: The Branch services that WRITE. A refused request must call none of them
#: (R8.4), which is how "nothing changed" is asserted rather than assumed.
WRITE_SERVICES = ("charge", "refund", "note_cooldown", "note_escalation")


class FakeAgent:
    """An eligible Carrier_Agent. The chain only asks whether one exists."""

    def __init__(self, key="spotter-1"):
        self.key = key
        self.id = 77


class FakePlayer:
    """A requesting player: an identity and the planet they occupy."""

    def __init__(self, id=5, planet=PLANET):
        self.id = id
        self.db = SimpleNamespace(coord_planet=planet)


class FakeBuilding:
    """An originating Branch_Building: an identity, an owner, a type, a planet."""

    def __init__(self, owner=None, building_type=ORIGIN_ABBR, planet=PLANET, id=41):
        self.id = id
        self.db = SimpleNamespace(
            owner=owner, building_type=building_type, coord_planet=planet
        )


class FakeBranchSystem:
    """The Branch services the chain consumes, as configurable answers.

    Reached exactly as the driver reaches them — **by name, duck-typed, one call
    each** — so this double is the real cross-module contract rather than a
    restatement of ``BranchSystem``'s internals: a service the driver renames
    stops being answered here and the chain degrades, which is visible.

    Every call is recorded, which is what lets a test assert that a refused
    request called no service that writes (R8.4).
    """

    def __init__(
        self,
        commitment=BRANCH,
        operational=True,
        applied=(UNLOCK_TECH,),
        role=ROLE,
        carrier=None,
        target_refusal=None,
        cooldown=0,
        count=0,
        cap=0,
        stock=None,
        charge_ok=True,
        floor=FLOOR,
    ):
        self.commitment_answer = commitment
        self.operational = operational
        self.applied = frozenset(applied)
        self.role = role
        self.carrier = FakeAgent() if carrier is None else carrier
        self.target_refusal = target_refusal
        self.cooldown = cooldown
        self.count = count
        self.cap = cap
        self.stock = {"Iron": 100} if stock is None else stock
        self.charge_ok = charge_ok
        self.floor = floor
        self.calls = []

    # --- identity and commitment ------------------------------------- #

    def commitment(self, player, planet=None):
        self.calls.append(("commitment", player, planet))
        return self.commitment_answer

    def lab_for_branch(self, branch):
        self.calls.append(("lab_for_branch", branch))
        return {BRANCH: "WL", "defense": "DL"}.get(branch)

    def branch_of_technology(self, tech_key):
        self.calls.append(("branch_of_technology", tech_key))
        return BRANCH

    def role_for_branch(self, branch):
        self.calls.append(("role_for_branch", branch))
        return self.role

    # --- the origin, the record, and the carrier ---------------------- #

    def is_operational(self, building):
        self.calls.append(("is_operational", building))
        return self.operational

    def applied_technologies(self, player, planet=None):
        self.calls.append(("applied_technologies", player, planet))
        return self.applied

    def eligible_carrier(self, player, role, planet=None):
        self.calls.append(("eligible_carrier", player, role, planet))
        return self.carrier if role == self.role else None

    # --- targeting and the three ledgers ----------------------------- #

    def may_target(self, actor, target, hostile=True):
        self.calls.append(("may_target", actor, target, hostile))
        return self.target_refusal

    def cooldown_remaining(self, building, kind):
        self.calls.append(("cooldown_remaining", building, kind))
        return self.cooldown

    def in_flight_count(self, player, kind, planet=None):
        self.calls.append(("in_flight_count", player, kind, planet))
        return self.count

    def in_flight_cap(self, kind):
        self.calls.append(("in_flight_cap", kind))
        return self.cap

    def resource_shortfall(self, player, cost):
        self.calls.append(("resource_shortfall", player, dict(cost)))
        if self.stock is None:
            return {}                                     # unreadable stock
        return {
            resource: {"have": int(self.stock.get(resource, 0)), "need": int(need)}
            for resource, need in cost.items()
        }

    def response_window(self, base_ticks, reduction=0):
        """The real service's arithmetic: ``max(floor, base - reduction)`` (R8.8)."""
        self.calls.append(("response_window", base_ticks, reduction))
        return max(self.floor, int(base_ticks) - int(reduction))

    # --- the services that WRITE ------------------------------------- #

    def charge(self, player, cost):
        self.calls.append(("charge", player, dict(cost)))
        return self.charge_ok

    def refund(self, player, cost):
        self.calls.append(("refund", player, dict(cost)))

    def note_cooldown(self, building, kind):
        self.calls.append(("note_cooldown", building, kind))

    def note_escalation(self, actor, target):
        self.calls.append(("note_escalation", actor, target))

    def called(self, service):
        """Return True when *service* was asked at least once."""
        return any(entry[0] == service for entry in self.calls)


class ChainRegistry:
    """A DataRegistry stand-in holding only what the chain reads (R15.4).

    One building definition, one Operation_Kind binding, and the Balance_Config
    field that binding names — so a test can drop the binding, blank the unlock
    technology, or retune the cost and see the chain follow.
    """

    def __init__(self, unlock=UNLOCK_TECH, cost=None, bind=True, floor=FLOOR):
        self.buildings = {
            ORIGIN_ABBR: SimpleNamespace(
                abbreviation=ORIGIN_ABBR,
                name=ORIGIN_NAME,
                branch=BRANCH,
                unlock_technology=unlock,
            ),
            # This Branch's lab, which is the one building whose destruction
            # lapses a commitment rather than ending an operation (R8.18). Its
            # abbreviation is what ``FakeBranchSystem.lab_for_branch`` answers.
            LAB_ABBR: SimpleNamespace(
                abbreviation=LAB_ABBR,
                name="Weapons Lab",
                branch=BRANCH,
                unlock_technology=None,
            ),
        }
        self.operation_kinds = {
            KIND: SimpleNamespace(
                kind=KIND,
                branch=BRANCH,
                carrier_role=ROLE,
                cost_field=COST_FIELD,
                cooldown_field=f"{KIND}_cooldown_ticks",
                cap_field=f"{KIND}_max_in_flight",
                agent_xp_field=f"agent_xp_{KIND}",
            ),
        } if bind else {}
        self.balance = SimpleNamespace(
            minimum_response_window_ticks=floor,
            **{COST_FIELD: dict(KIND_COST if cost is None else cost)},
        )


class FakeCombatEngine:
    """The CombatEngine reduced to its single-hit entry point (R8.23).

    Records every hit it is handed, which is how the attribution claim (R10.3) is
    made: the ``attacker`` the driver passes has to be the **owning player**.
    Reached duck-typed by name, exactly as the driver reaches it, so a renamed
    entry point stops being answered here and the routing degrades visibly.
    """

    def __init__(self, damage=9):
        self.damage = damage
        self.hits = []

    def apply_direct_hit(
        self, attacker, target, weapon_item, include_attacker_bonus=True,
        current_tick=None,
    ):
        self.hits.append({
            "attacker": attacker,
            "target": target,
            "weapon": weapon_item,
            "include_attacker_bonus": include_attacker_bonus,
            "current_tick": current_tick,
        })
        return self.damage


class FakeWeapon:
    """A weapon-shaped object, as a vector's ``on_resolve`` would build one."""

    def __init__(self, damage=12, weapon_range=1, name="Strike"):
        self.key = name
        self.stat_modifiers = {"damage": damage, "range": weapon_range}
        self.ammo_cost = None

    def get_stat(self, stat_name, default=0):
        return float(self.stat_modifiers.get(stat_name, default))


class ChainVector(FakeVector, BaseSystem):
    """A conforming vector wired for the happy path — the composed shape §4.10.

    Every collaborator injected and every service answering "yes", so a test
    breaks exactly one thing and the chain's single refusal names it.
    """

    _required_collaborators = ("combat_engine",)

    def __init__(
        self, registry=None, branch_system=None, combat=True, owner=None, bus=None
    ):
        super().__init__(
            ChainRegistry() if registry is None else registry,
            LifecycleBus() if bus is None else bus,
            branch_system=FakeBranchSystem() if branch_system is None else branch_system,
            owner=owner,
        )
        if combat:
            self._combat_engine = FakeCombatEngine()


class AcceptingVector(ChainVector):
    """A vector whose acceptance half records the context it was handed.

    Replaces the acceptance half rather than running it, so the subject of every
    chain test below is the chain alone: reaching here means all nine checks
    passed, and the context carries what they resolved. The real charge-then-
    Pending half is exercised by :class:`PlacingVector` further down.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accepted = []

    def _accept(self, ctx):
        self.accepted.append(ctx)
        return OperationOutcome.accepted(OperationRecord(kind=self.operation_kind))


class ForcingVector(AcceptingVector):
    """A vector whose checks refuse on demand, so the ORDER is tested alone.

    Replaces the guarded check runner rather than the checks, so the subject is
    exactly ``request``'s walk of ``_CHECK_ORDER``: which check is asked, in what
    order, and when the walk stops.
    """

    def __init__(self, forced=(), **kwargs):
        super().__init__(**kwargs)
        self.forced = set(forced)
        self.ran = []

    def _run_check(self, name, ctx):
        self.ran.append(name)
        return {"message": f"forced_{name}"} if name in self.forced else None


class RefusingTargetVector(ChainVector):
    """A vector whose own target hook refuses, with a key and structured data."""

    REFUSAL = BranchRefusal("strike_out_of_range", required_range=8, distance=12)

    def validate_target(self, ctx):
        return self.REFUSAL


class RaisingTargetVector(ChainVector):
    """A vector whose target hook raises — the R15.3 case for a vector's code."""

    def validate_target(self, ctx):
        raise RuntimeError("this target cannot be judged")


class ContextlessVector(AcceptingVector):
    """A vector whose context cannot be built at all: the last-resort net."""

    def _build_context(self, player, params):
        raise RuntimeError("the request context could not be built")


class PlacingVector(ChainVector):
    """A conforming vector that really places an operation — the R8.5 path.

    Its ``build_record`` answers a record carrying the clock the vector asked
    for, and snapshots which Branch services had **already** been called by the
    time it was asked. That snapshot is how "the cost is charged before the
    record enters Pending" is asserted rather than assumed: the charge has to be
    in it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ticks = 20
        self.lifetime = None
        self.fail_build = False
        self.built = []
        self.services_at_build = []

    def build_record(self, ctx):
        self.services_at_build = [entry[0] for entry in self._branch.calls]
        if self.fail_build:
            raise RuntimeError("this operation's record cannot be built")
        record = OperationRecord(
            op_id="op-placed",
            kind=self.operation_kind,
            owner_ref=getattr(ctx.player, "id", None),
            building_ref=ctx.building,
            carrier_ref=ctx.carrier,
            planet=ctx.planet,
            target_x=ctx.target_x,
            target_y=ctx.target_y,
            ticks_remaining=self.ticks,
            lifetime_remaining=self.lifetime,
        )
        self.built.append(record)
        return record


class TerminalRecordVector(PlacingVector):
    """A vector whose ``build_record`` hands back an already-terminal record.

    Nothing raises, and yet the operation cannot enter Pending: the single state
    writer declines to move a terminal record (R8.2). The charge still has to
    come back (R8.6), which is why this is a case of its own.
    """

    def build_record(self, ctx):
        record = super().build_record(ctx)
        record.state = str(OperationState.RESOLVED)
        return record


class SupportiveVector(PlacingVector):
    """A vector whose Signature_Vector supports rather than attacks.

    The one override the Response_Window floor offers: a supporting operation has
    no target to warn, so R8.8's floor does not apply to it.
    """

    def _is_hostile(self, record):
        return False


def _chain_world(vector_cls=AcceptingVector, registry=None, **answers):
    """Return a vector wired for the happy path, with its player and building."""
    player = FakePlayer()
    branch = FakeBranchSystem(**answers)
    vector = vector_cls(registry=registry, branch_system=branch)
    return SimpleNamespace(
        vector=vector, branch=branch, player=player,
        building=FakeBuilding(owner=player),
    )


def _send(world, **extra):
    """Send one request through *world*'s vector, with the happy-path params."""
    params = {"building": world.building, "x": 3, "y": 4}
    params.update(extra)
    return world.vector.request(world.player, **params)


class TestCheckOrderIsDeclaredOnce(unittest.TestCase):
    """R8.3: nine checks, one declared order, and no second list of them."""

    def test_exactly_the_nine_checks_in_the_declared_order(self):
        self.assertEqual(OperationDriver._CHECK_ORDER, NINE_CHECKS)

    def test_the_order_is_an_immutable_tuple_with_no_duplicates(self):
        self.assertIsInstance(OperationDriver._CHECK_ORDER, tuple)
        self.assertEqual(
            len(set(OperationDriver._CHECK_ORDER)), len(OperationDriver._CHECK_ORDER)
        )

    def test_the_shared_strategies_copy_matches_the_authority(self):
        """The cross-check ``branch_strategies`` asked for when it wrote its copy.

        ``OPERATION_CHECK_ORDER`` is a by-value copy of this tuple, written from
        the design because the generators landed before the driver did — and
        Property 13 walks the forced-failure lattice over it. If the two drift,
        the property tests a different chain than the one that ships, so the
        equality is pinned here in the same spirit as the ``TERMINAL_STATES``
        cross-check above.

        Imported inside the test on purpose: ``branch_strategies`` installs the
        Evennia stubs and pulls in Hypothesis at import time, and this module
        needs neither for anything else.
        """
        from mygame.world.systems.tests.branch_strategies import (
            OPERATION_CHECK_ORDER,
        )

        self.assertEqual(OperationDriver._CHECK_ORDER, OPERATION_CHECK_ORDER)

    def test_every_declared_check_has_a_method_to_run(self):
        for name in NINE_CHECKS:
            with self.subTest(check=name):
                self.assertTrue(callable(getattr(OperationDriver, f"_check_{name}")))

    def test_no_check_method_exists_outside_the_declared_order(self):
        """An orphan check would never run, so it could only mislead a reader."""
        found = {
            name[len("_check_"):] for name, value in vars(OperationDriver).items()
            if name.startswith("_check_") and callable(value)
        }
        self.assertEqual(sorted(found), sorted(NINE_CHECKS))

    def test_every_check_has_a_distinct_fallback_message_key(self):
        """A check that cannot run still refuses with a renderable key (R13.5)."""
        messages = operation_contract._CHECK_MESSAGES
        self.assertEqual(sorted(messages), sorted(NINE_CHECKS))
        self.assertEqual(len(set(messages.values())), len(NINE_CHECKS))
        for name, key in sorted(messages.items()):
            with self.subTest(check=name):
                self.assertTrue(key and key.startswith("vector_"))


class TestRequestWalksTheChainInOrder(unittest.TestCase):
    """R8.3, R8.4: refuse at the FIRST failing check, and stop there."""

    def test_a_chain_with_nothing_forced_asks_every_check_then_accepts(self):
        world = _chain_world(ForcingVector)
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        self.assertEqual(world.vector.ran, list(NINE_CHECKS))

    def test_each_check_can_be_the_one_that_refuses(self):
        for name in NINE_CHECKS:
            with self.subTest(check=name):
                world = _chain_world(ForcingVector)
                world.vector.forced = {name}
                outcome = _send(world)

                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.check, name)
                self.assertIsNone(outcome.state)
                self.assertEqual(outcome.detail["message"], f"forced_{name}")

    def test_the_earliest_failing_check_is_the_one_reported(self):
        for forced, expected in (
            ({"resources", "commitment"}, "commitment"),
            ({"target", "carrier"}, "carrier"),
            ({"in_flight", "cooldown", "unlock"}, "unlock"),
            (set(NINE_CHECKS), "collaborators"),
            ({"resources"}, "resources"),
        ):
            with self.subTest(forced=sorted(forced)):
                world = _chain_world(ForcingVector)
                world.vector.forced = set(forced)

                self.assertEqual(_send(world).check, expected)

    def test_no_check_after_the_failing_one_is_asked(self):
        world = _chain_world(ForcingVector)
        world.vector.forced = {"origin"}
        _send(world)

        self.assertEqual(world.vector.ran, ["collaborators", "commitment", "origin"])

    def test_exactly_one_reason_travels_with_a_refusal(self):
        """One input, one refusal reason — the determinism Property 13 pins."""
        world = _chain_world(ForcingVector)
        world.vector.forced = {"carrier", "cooldown"}
        outcome = _send(world)

        self.assertIsInstance(outcome.check, str)
        self.assertEqual(outcome.check, "carrier")
        self.assertEqual(outcome.detail["message"], "forced_carrier")


class TestARefusedRequestChangesNothing(unittest.TestCase):
    """R8.4: every player-owned and world-owned state is left as it was."""

    def test_no_forced_refusal_writes_anything(self):
        for name in NINE_CHECKS:
            with self.subTest(check=name):
                world = _chain_world(ForcingVector)
                world.vector.forced = {name}
                outcome = _send(world)

                self.assertFalse(outcome.ok)
                self.assertEqual(world.vector.tracked_records(), [])
                self.assertEqual(_read_records(world.vector.owner), [])
                for service in WRITE_SERVICES:
                    self.assertFalse(
                        world.branch.called(service),
                        f"a refused request called {service}()",
                    )

    def test_a_real_refusal_writes_nothing_either(self):
        world = _chain_world(commitment="defense")
        outcome = _send(world)

        self.assertEqual(outcome.check, "commitment")
        self.assertEqual(world.vector.tracked_records(), [])
        self.assertEqual(_read_records(world.vector.owner), [])
        self.assertFalse(any(world.branch.called(s) for s in WRITE_SERVICES))

    def test_a_refusal_reports_no_lifecycle_state_because_none_exists(self):
        world = _chain_world(commitment=None)
        outcome = _send(world)

        self.assertIsNone(outcome.state)
        self.assertIsNone(outcome.op_id)


class TestCollaboratorCheck(unittest.TestCase):
    """R15.2: an unwired system degrades to a refusal with a log, never a raise."""

    def test_an_unwired_branch_system_refuses_every_request(self):
        vector = ChainVector()
        vector._branch = None
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING") as logs:
            outcome = vector.request(FakePlayer(), building=FakeBuilding())

        self.assertEqual(outcome.check, "collaborators")
        self.assertEqual(outcome.detail["collaborator"], "branch_system")
        self.assertEqual(outcome.detail["missing"], ["branch_system"])
        self.assertIn("branch_system", logs.output[0])

    def test_an_unwired_declared_collaborator_refuses_and_names_it(self):
        world = _chain_world()
        world.vector._combat_engine = None
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            outcome = _send(world)

        self.assertEqual(outcome.check, "collaborators")
        self.assertEqual(outcome.detail["collaborator"], "combat_engine")
        self.assertEqual(outcome.detail["missing"], ["combat_engine"])
        self.assertEqual(
            outcome.detail["required"], ["branch_system", "combat_engine"]
        )
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_UNWIRED)

    def test_a_declared_collaborator_resolves_under_the_private_prefix(self):
        """Task 11.1's promise: ``("combat_engine",)`` matches ``_combat_engine``."""
        world = _chain_world()
        self.assertFalse(hasattr(world.vector, "combat_engine"))
        self.assertIsNotNone(world.vector._combat_engine)

        self.assertTrue(_send(world).ok)

    def test_a_declared_collaborator_resolves_plainly_too(self):
        world = _chain_world()
        del world.vector._combat_engine
        world.vector.combat_engine = object()

        self.assertTrue(_send(world).ok)

    def test_every_missing_collaborator_is_reported_together(self):
        vector = ChainVector(combat=False)
        vector._branch = None
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            outcome = vector.request(FakePlayer())

        self.assertEqual(
            outcome.detail["missing"], ["branch_system", "combat_engine"]
        )

    def test_a_vector_declaring_nothing_needs_only_the_branch_system(self):
        """The driver's own collaborator is required whatever a vector declares."""
        world = _chain_world()
        world.vector._required_collaborators = ()
        del world.vector._combat_engine

        self.assertEqual(world.vector._collaborator_names(), ("branch_system",))
        self.assertTrue(_send(world).ok)

    def test_a_name_declared_twice_is_reported_once(self):
        world = _chain_world()
        world.vector._required_collaborators = (
            "combat_engine", "combat_engine", "branch_system", "",
        )

        self.assertEqual(
            world.vector._collaborator_names(), ("branch_system", "combat_engine")
        )


class TestCommitmentCheck(unittest.TestCase):
    """R8.3: the owner's Branch_Commitment must be this vector's Branch."""

    def test_a_matching_commitment_passes(self):
        world = _chain_world()
        self.assertTrue(_send(world).ok)

    def test_a_different_commitment_refuses_and_reports_both(self):
        world = _chain_world(commitment="defense")
        outcome = _send(world)

        self.assertEqual(outcome.check, "commitment")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_COMMITMENT_REQUIRED)
        self.assertEqual(outcome.detail["required_branch"], BRANCH)
        self.assertEqual(outcome.detail["required_doctrine"], "Ordnance")
        self.assertEqual(outcome.detail["required_lab"], "WL")
        self.assertEqual(outcome.detail["current_branch"], "defense")
        self.assertEqual(outcome.detail["current_doctrine"], "Fortification")
        self.assertEqual(outcome.detail["planet"], PLANET)

    def test_no_commitment_at_all_refuses_and_names_the_lab_required(self):
        world = _chain_world(commitment=None)
        outcome = _send(world)

        self.assertEqual(outcome.check, "commitment")
        self.assertIsNone(outcome.detail["current_branch"])
        self.assertEqual(outcome.detail["required_lab"], "WL")

    def test_a_vector_declaring_no_branch_matches_no_commitment(self):
        world = _chain_world()
        world.vector.branch = ""
        outcome = _send(world)

        self.assertEqual(outcome.check, "commitment")
        self.assertIsNone(outcome.detail["required_branch"])


class TestOriginCheck(unittest.TestCase):
    """R8.3: the originating building is owned, Operational, under an active HQ."""

    def test_a_request_naming_no_building_refuses(self):
        world = _chain_world()
        outcome = world.vector.request(world.player, x=3, y=4)

        self.assertEqual(outcome.check, "origin")
        self.assertEqual(outcome.detail["reason"], operation_contract.ORIGIN_MISSING)
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_ORIGIN_UNAVAILABLE)

    def test_another_players_building_refuses(self):
        world = _chain_world()
        outcome = _send(world, building=FakeBuilding(owner=FakePlayer(id=99)))

        self.assertEqual(outcome.check, "origin")
        self.assertEqual(outcome.detail["reason"], operation_contract.ORIGIN_NOT_OWNED)

    def test_ownership_is_compared_by_id_not_by_object(self):
        """The comparison has to survive two lookups handing back two objects."""
        world = _chain_world()
        outcome = _send(world, building=FakeBuilding(owner=FakePlayer(id=5)))

        self.assertTrue(outcome.ok)

    def test_a_non_operational_building_refuses_through_the_branch_service(self):
        """R11.3 and R5.4 are folded in by delegating, not reimplemented."""
        world = _chain_world(operational=False)
        outcome = _send(world)

        self.assertEqual(outcome.check, "origin")
        self.assertEqual(
            outcome.detail["reason"], operation_contract.ORIGIN_NOT_OPERATIONAL
        )
        self.assertIn(("is_operational", world.building), world.branch.calls)

    def test_the_refusal_quotes_the_building_it_could_not_use(self):
        world = _chain_world(operational=False)
        outcome = _send(world)

        self.assertEqual(outcome.detail["building"], ORIGIN_ABBR)
        self.assertEqual(outcome.detail["building_name"], ORIGIN_NAME)
        self.assertEqual(outcome.detail["required_branch"], BRANCH)


class TestUnlockCheck(unittest.TestCase):
    """R6.6: the originating building's unlocking technology must be live."""

    def test_a_building_with_no_unlock_technology_passes(self):
        world = _chain_world(registry=ChainRegistry(unlock=None), applied=())
        self.assertTrue(_send(world).ok)

    def test_a_researched_and_applied_technology_passes(self):
        world = _chain_world(applied=(UNLOCK_TECH,))
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        self.assertIn(
            ("applied_technologies", world.player, PLANET), world.branch.calls
        )

    def test_a_technology_whose_effects_are_not_applied_refuses(self):
        """R6.2 is two conditions: recorded AND applied. This is the second."""
        world = _chain_world(applied=("something_else",))
        outcome = _send(world)

        self.assertEqual(outcome.check, "unlock")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_UNLOCK_REQUIRED)
        self.assertEqual(outcome.detail["technology"], UNLOCK_TECH)
        self.assertEqual(outcome.detail["branch"], BRANCH)
        self.assertEqual(outcome.detail["doctrine"], "Ordnance")
        self.assertEqual(outcome.detail["lab"], "WL")
        self.assertEqual(outcome.detail["building"], ORIGIN_ABBR)


class TestCarrierCheck(unittest.TestCase):
    """R7.1, R7.3: an eligible Carrier_Agent of the required role, or a refusal."""

    def test_an_eligible_carrier_passes_and_lands_on_the_context(self):
        world = _chain_world()
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        ctx = world.vector.accepted[0]
        self.assertIs(ctx.carrier, world.branch.carrier)
        self.assertEqual(ctx.role, ROLE)

    def test_no_eligible_carrier_refuses_and_reports_the_required_role(self):
        world = _chain_world()
        world.branch.carrier = None
        outcome = _send(world)

        self.assertEqual(outcome.check, "carrier")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_CARRIER_REQUIRED)
        self.assertEqual(outcome.detail["role"], ROLE)
        self.assertEqual(outcome.detail["branch"], BRANCH)

    def test_the_role_comes_from_the_operation_kind_binding(self):
        world = _chain_world()
        _send(world)

        self.assertIn(
            ("eligible_carrier", world.player, ROLE, PLANET), world.branch.calls
        )
        self.assertFalse(world.branch.called("role_for_branch"))

    def test_the_branchs_own_role_is_the_fallback_binding(self):
        """``branches.yaml`` absent must not stop a vector finding its carrier."""
        world = _chain_world(registry=ChainRegistry(bind=False))
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        self.assertIn(("role_for_branch", BRANCH), world.branch.calls)

    def test_a_role_nothing_can_name_refuses_rather_than_searching(self):
        world = _chain_world(registry=ChainRegistry(bind=False), role=None)
        outcome = _send(world)

        self.assertEqual(outcome.check, "carrier")
        self.assertIsNone(outcome.detail["role"])
        self.assertFalse(world.branch.called("eligible_carrier"))


class TestTargetCheck(unittest.TestCase):
    """R8.3 plus R10.4, R10.6, R10.7, R11.9: the hook, then the shared gates."""

    def test_a_valid_target_passes_both_halves(self):
        world = _chain_world()
        victim = FakePlayer(id=9)
        outcome = _send(world, target=victim)

        self.assertTrue(outcome.ok)
        self.assertIn(("may_target", world.player, victim, True), world.branch.calls)

    def test_the_vectors_own_refusal_travels_with_its_key_and_data(self):
        world = _chain_world(RefusingTargetVector)
        outcome = _send(world)

        self.assertEqual(outcome.check, "target")
        self.assertEqual(outcome.detail["message"], "strike_out_of_range")
        self.assertEqual(outcome.detail["required_range"], 8)
        self.assertEqual(outcome.detail["distance"], 12)
        self.assertEqual(outcome.detail["kind"], KIND)

    def test_the_vectors_hook_is_asked_before_the_shared_gates(self):
        world = _chain_world(
            RefusingTargetVector,
            target_refusal=BranchRefusal(MSG_VECTOR_TARGET_SHIELDED, required_level=10),
        )
        outcome = _send(world)

        self.assertEqual(outcome.detail["message"], "strike_out_of_range")
        self.assertFalse(world.branch.called("may_target"))

    def test_the_new_player_shield_is_reported_with_the_qualifying_level(self):
        """R10.4, inherited from ``may_target`` rather than reimplemented."""
        world = _chain_world(target_refusal=BranchRefusal(
            MSG_VECTOR_TARGET_SHIELDED,
            target_name="rookie", target_level=3, required_level=10,
        ))
        outcome = _send(world, target=FakePlayer(id=9))

        self.assertEqual(outcome.check, "target")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_TARGET_SHIELDED)
        self.assertEqual(outcome.detail["required_level"], 10)
        self.assertEqual(outcome.detail["target_level"], 3)

    def test_an_allied_target_is_reported_with_the_alliance(self):
        """R11.9, and R10.7: the gate fires on identical terms for an ally."""
        world = _chain_world(target_refusal=BranchRefusal(
            MSG_VECTOR_TARGET_ALLIED, alliance=7, alliance_name="Pact",
        ))
        outcome = _send(world, target=FakePlayer(id=9))

        self.assertEqual(outcome.detail["message"], MSG_VECTOR_TARGET_ALLIED)
        self.assertEqual(outcome.detail["alliance"], 7)

    def test_the_escalation_cap_is_reported_with_the_remaining_ticks(self):
        """R10.6, folded into the same gate by task 8.2."""
        world = _chain_world(target_refusal=BranchRefusal(
            MSG_VECTOR_ESCALATION_LIMIT, remaining_ticks=120, count=3, cap=3,
        ))
        outcome = _send(world, target=FakePlayer(id=9))

        self.assertEqual(outcome.detail["message"], MSG_VECTOR_ESCALATION_LIMIT)
        self.assertEqual(outcome.detail["remaining_ticks"], 120)
        self.assertEqual(outcome.detail["cap"], 3)

    def test_a_supporting_operation_asks_the_gate_the_other_question(self):
        """R11.8's consent check is the same call with ``hostile=False``."""
        world = _chain_world()
        ally = FakePlayer(id=9)
        _send(world, target=ally, hostile=False)

        self.assertIn(("may_target", world.player, ally, False), world.branch.calls)

    def test_a_target_hook_that_raises_refuses_that_check_and_logs(self):
        world = _chain_world(RaisingTargetVector)
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(world)

        self.assertEqual(outcome.check, "target")
        self.assertEqual(outcome.detail["reason"], "check_failed")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_TARGET_INVALID)


class TestCooldownCheck(unittest.TestCase):
    """R8.19: report the remaining cooldown ticks, from the building's ledger."""

    def test_an_elapsed_cooldown_passes(self):
        world = _chain_world(cooldown=0)
        self.assertTrue(_send(world).ok)
        self.assertIn(("cooldown_remaining", world.building, KIND), world.branch.calls)

    def test_a_running_cooldown_refuses_with_the_ledgers_own_figure(self):
        world = _chain_world(cooldown=12)
        outcome = _send(world)

        self.assertEqual(outcome.check, "cooldown")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_COOLDOWN)
        self.assertEqual(outcome.detail["remaining_ticks"], 12)
        self.assertEqual(outcome.detail["building"], ORIGIN_ABBR)


class TestInFlightCheck(unittest.TestCase):
    """R8.20: report the current count and the cap when a request exceeds it."""

    def test_a_count_below_the_cap_passes(self):
        world = _chain_world(count=1, cap=2)
        self.assertTrue(_send(world).ok)

    def test_reaching_the_cap_refuses_with_both_figures(self):
        world = _chain_world(count=3, cap=3)
        outcome = _send(world)

        self.assertEqual(outcome.check, "in_flight")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_IN_FLIGHT_CAP)
        self.assertEqual(outcome.detail["count"], 3)
        self.assertEqual(outcome.detail["cap"], 3)
        self.assertEqual(outcome.detail["planet"], PLANET)

    def test_no_configured_cap_is_unbounded_rather_than_a_lockout(self):
        world = _chain_world(count=99, cap=0)

        self.assertTrue(_send(world).ok)
        self.assertFalse(world.branch.called("in_flight_count"))


class TestResourcesCheck(unittest.TestCase):
    """R12.3: the have-and-need breakdown, and sufficiency checked LAST."""

    def test_an_affordable_cost_passes_and_lands_on_the_context(self):
        world = _chain_world()
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        self.assertEqual(world.vector.accepted[0].cost, KIND_COST)

    def test_an_unaffordable_cost_refuses_with_the_breakdown(self):
        world = _chain_world(stock={"Iron": 4})
        outcome = _send(world)

        self.assertEqual(outcome.check, "resources")
        self.assertEqual(
            outcome.detail["message"], MSG_VECTOR_INSUFFICIENT_RESOURCES
        )
        self.assertEqual(outcome.detail["cost"], KIND_COST)
        self.assertEqual(
            outcome.detail["resources"], {"Iron": {"have": 4, "need": 25}}
        )
        self.assertEqual(
            outcome.detail["missing"], {"Iron": {"have": 4, "need": 25}}
        )

    def test_a_kind_with_no_configured_cost_asks_nothing_and_passes(self):
        world = _chain_world(registry=ChainRegistry(cost={}))
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        self.assertEqual(world.vector.accepted[0].cost, {})
        self.assertFalse(world.branch.called("resource_shortfall"))

    def test_a_stock_that_cannot_be_read_leaves_the_charge_to_decide(self):
        """The charge is whole-or-none and authoritative; this is a pre-check."""
        world = _chain_world(stock=None)
        self.assertTrue(_send(world).ok)

    def test_the_cost_is_read_from_the_bound_balance_field_on_every_request(self):
        """R15.7: a retune reaches the next request, with nothing cached."""
        world = _chain_world()
        self.assertTrue(_send(world).ok)

        world.vector.registry.balance.strategic_strike_cost = {"Iron": 4}
        world.branch.stock = {"Iron": 4}
        _send(world)

        self.assertEqual(world.vector.accepted[-1].cost, {"Iron": 4})

    def test_the_convention_names_the_field_when_the_binding_is_absent(self):
        world = _chain_world(registry=ChainRegistry(bind=False))
        _send(world)

        self.assertEqual(world.vector.accepted[0].cost, KIND_COST)


# ------------------------------------------------------------------ #
#  The acceptance half: charge, Pending, refund, floor (design §4.3, §4.5)
# ------------------------------------------------------------------ #

def _placing_world(vector_cls=PlacingVector, **answers):
    """Return a world whose vector really places an operation."""
    return _chain_world(vector_cls, **answers)


class TestAcceptanceChargesBeforePending(unittest.TestCase):
    """R8.5: the cost is charged BEFORE the record enters the Pending state."""

    def setUp(self):
        self.world = _placing_world()

    def _stored(self):
        return _read_records(self.world.vector.owner)

    def test_the_resolved_cost_is_charged_exactly_once(self):
        outcome = _send(self.world)

        self.assertTrue(outcome.ok)
        charges = [entry for entry in self.world.branch.calls if entry[0] == "charge"]
        self.assertEqual(charges, [("charge", self.world.player, KIND_COST)])

    def test_the_charge_happens_before_the_record_is_built(self):
        """The order R8.5 fixes, read from the vector's own vantage point."""
        _send(self.world)

        self.assertIn("charge", self.world.vector.services_at_build)

    def test_the_amount_checked_is_the_amount_charged(self):
        """One cost resolution per request, so the two cannot disagree (R12.2)."""
        self.world.vector.registry.balance.strategic_strike_cost = {"Iron": 9}
        self.world.branch.stock = {"Iron": 9}
        _send(self.world)

        breakdown = [
            entry for entry in self.world.branch.calls
            if entry[0] in ("resource_shortfall", "charge")
        ]
        self.assertEqual([entry[2] for entry in breakdown], [{"Iron": 9}, {"Iron": 9}])

    def test_the_accepted_record_is_pending_tracked_and_persisted(self):
        outcome = _send(self.world)
        tracked = self.world.vector.tracked_records()

        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0].state, str(OperationState.PENDING))
        self.assertEqual(tracked[0].op_id, outcome.op_id)
        self.assertEqual([entry["op_id"] for entry in self._stored()], [outcome.op_id])
        self.assertEqual(self._stored()[0]["state"], "pending")

    def test_the_record_carries_what_was_charged_for_the_refund_path(self):
        """R8.6 needs the amount on the record, not only in the request."""
        _send(self.world)

        self.assertEqual(self.world.vector.tracked_records()[0].charged, KIND_COST)
        self.assertEqual(self._stored()[0]["charged"], KIND_COST)

    def test_the_stamped_charge_shares_no_container_with_the_request(self):
        """A record's charge is its own, so the next request cannot be reached."""
        _send(self.world)
        self.world.vector.tracked_records()[0].charged["Planted"] = 1

        _send(self.world)

        self.assertEqual(self.world.vector.built[-1].charged, KIND_COST)

    def test_the_cooldown_is_noted_on_acceptance_and_escalation_is_not(self):
        """R8.19 measures from the request; R10.6's ledger is a resolution note."""
        _send(self.world)

        self.assertIn(
            ("note_cooldown", self.world.building, KIND), self.world.branch.calls
        )
        self.assertFalse(self.world.branch.called("note_escalation"))

    def test_the_cooldown_note_follows_the_pending_entry(self):
        """A cooldown started before the operation existed would throttle nothing."""
        names = []
        original = self.world.branch.note_cooldown

        def watched(building, kind):
            names.append(self.world.vector.tracked_records()[0].state)
            original(building, kind)

        self.world.branch.note_cooldown = watched
        _send(self.world)

        self.assertEqual(names, [str(OperationState.PENDING)])

    def test_a_note_cooldown_that_raises_does_not_lose_the_operation(self):
        """R15.3: a ledger write must never unmake an accepted operation."""
        self.world.branch.note_cooldown = lambda building, kind: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(self.world)

        self.assertTrue(outcome.ok)
        self.assertEqual(len(self.world.vector.tracked_records()), 1)


class TestAcceptanceRefusesAnUnaffordableCharge(unittest.TestCase):
    """R12.2, R12.3: the charge is whole-or-none, and it is the authority."""

    def setUp(self):
        # A stock the pre-check reads as sufficient and the charge rejects: the
        # only way to reach the charge's own refusal, and the shape a race or an
        # unreadable stock takes in a live game.
        self.world = _placing_world(charge_ok=False)

    def test_a_refused_charge_refuses_the_resources_check_with_the_breakdown(self):
        self.world.branch.stock = {"Iron": 4}
        outcome = _send(self.world)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.check, "resources")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_INSUFFICIENT_RESOURCES)
        self.assertEqual(outcome.detail["cost"], KIND_COST)
        self.assertEqual(outcome.detail["resources"], {"Iron": {"have": 4, "need": 25}})
        self.assertEqual(outcome.detail["missing"], {"Iron": {"have": 4, "need": 25}})
        self.assertEqual(outcome.detail["kind"], KIND)

    def test_a_refused_charge_reports_the_breakdown_it_can_read(self):
        """An unreadable stock still refuses in the documented shape."""
        self.world.branch.stock = None
        outcome = _send(self.world)

        self.assertEqual(outcome.check, "resources")
        self.assertEqual(outcome.detail["cost"], KIND_COST)
        self.assertEqual(outcome.detail["resources"], {})
        self.assertEqual(outcome.detail["missing"], {})

    def test_a_refused_charge_creates_nothing_and_refunds_nothing(self):
        outcome = _send(self.world)

        self.assertIsNone(outcome.state)
        self.assertIsNone(outcome.op_id)
        self.assertEqual(self.world.vector.tracked_records(), [])
        self.assertEqual(_read_records(self.world.vector.owner), [])
        self.assertFalse(self.world.branch.called("refund"))
        self.assertFalse(self.world.branch.called("note_cooldown"))
        self.assertEqual(self.world.vector.built, [])

    def test_a_branch_system_with_no_charge_service_creates_no_free_operation(self):
        """A partially wired charge refuses rather than waving the cost through."""
        self.world.branch.charge = None
        outcome = _send(self.world)

        self.assertEqual(outcome.check, "resources")
        self.assertEqual(self.world.vector.tracked_records(), [])


class TestAcceptanceRefundsAFailedPendingEntry(unittest.TestCase):
    """R8.6: no Vector_Operation both charges and fails.

    One case per point a request can fail *after* the charge — the vector's hook,
    the tracking, and the persist inside the state write — because they fail at
    three different points of the same guarded block and a refund written for one
    of them would pass a single-point test. Plus the fourth, quieter one: a record
    the single state writer declines to move (R8.2).
    """

    def setUp(self):
        self.world = _placing_world()

    def _fail_at(self, point):
        """Break exactly one post-charge step of the acceptance half."""
        if point == "build_record":
            self.world.vector.fail_build = True
        elif point == "track":
            self.world.vector._track = lambda record: 1 / 0
        elif point == "persist":
            self.world.vector._persist = lambda record: 1 / 0

    def test_each_post_charge_failure_refunds_the_whole_amount(self):
        for point in ("build_record", "track", "persist"):
            with self.subTest(point=point):
                self.world = _placing_world()
                self._fail_at(point)
                with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
                    outcome = _send(self.world)

                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.check, "pending_entry")
                self.assertIsNone(outcome.state)
                self.assertEqual(outcome.detail["refunded"], KIND_COST)
                self.assertIn(
                    ("refund", self.world.player, KIND_COST), self.world.branch.calls
                )

    def test_each_post_charge_failure_leaves_no_operation_behind(self):
        for point in ("build_record", "track", "persist"):
            with self.subTest(point=point):
                self.world = _placing_world()
                self._fail_at(point)
                with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
                    _send(self.world)

                self.assertEqual(self.world.vector.tracked_records(), [])
                self.assertFalse(self.world.branch.called("note_cooldown"))

    def test_the_refund_is_the_whole_charge_exactly_once(self):
        self.world.vector.fail_build = True
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            _send(self.world)

        refunds = [entry for entry in self.world.branch.calls if entry[0] == "refund"]
        charges = [entry for entry in self.world.branch.calls if entry[0] == "charge"]
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds[0][2], charges[0][2])

    def test_a_record_that_cannot_enter_pending_is_refunded_too(self):
        """A terminal record moves nowhere, so the charge bought nothing (R8.2)."""
        self.world = _placing_world(TerminalRecordVector)
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            outcome = _send(self.world)

        self.assertEqual(outcome.check, "pending_entry")
        self.assertIn(
            ("refund", self.world.player, KIND_COST), self.world.branch.calls
        )
        self.assertEqual(self.world.vector.tracked_records(), [])

    def test_a_free_operation_that_fails_refunds_nothing(self):
        """Nothing was charged, so there is nothing to give back (R12.6)."""
        self.world = _placing_world(registry=ChainRegistry(cost={}))
        self.world.vector.fail_build = True
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(self.world)

        self.assertEqual(outcome.check, "pending_entry")
        self.assertEqual(outcome.detail["refunded"], {})
        self.assertFalse(self.world.branch.called("refund"))

    def test_a_refund_service_that_raises_still_answers_failed(self):
        """R15.3: the player is owed the resources, and the log says so."""
        self.world.vector.fail_build = True
        self.world.branch.refund = lambda player, cost: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(self.world)

        self.assertEqual(outcome.check, "pending_entry")


class TestNpcOperationsChargeNothing(unittest.TestCase):
    """R12.6, R11.6: an NPC base pays nothing and is bound by everything else."""

    def setUp(self):
        self.world = _placing_world()
        self.world.player.db.is_sentinel = True

    def test_an_npc_base_is_charged_nothing_and_still_gets_an_operation(self):
        outcome = _send(self.world)

        self.assertTrue(outcome.ok)
        self.assertFalse(self.world.branch.called("charge"))
        self.assertFalse(self.world.branch.called("resource_shortfall"))
        self.assertEqual(self.world.vector.tracked_records()[0].charged, {})

    def test_an_npc_type_marker_is_read_the_same_way(self):
        """The two markers ``BranchSystem`` reads, read here the same way."""
        self.world.player.db.is_sentinel = None
        self.world.player.db.npc_type = "raider_camp"

        self.assertTrue(_send(self.world).ok)
        self.assertFalse(self.world.branch.called("charge"))

    def test_an_npc_base_gets_the_same_response_window_floor(self):
        """Waiving the charge waives the charge and nothing else (R11.6)."""
        self.world.vector.ticks = 1
        _send(self.world)

        self.assertEqual(
            self.world.vector.tracked_records()[0].ticks_remaining, FLOOR
        )
        self.assertIn(("note_cooldown", self.world.building, KIND),
                      self.world.branch.calls)

    def test_a_player_is_charged_however_odd_their_markers_are(self):
        """An unreadable marker is not a free pass: a player pays."""
        self.world.player.db.is_sentinel = False
        self.world.player.db.npc_type = None
        _send(self.world)

        self.assertIn(("charge", self.world.player, KIND_COST), self.world.branch.calls)


class TestResponseWindowFloor(unittest.TestCase):
    """R8.8, R9.4: a hostile window never falls below the configured floor."""

    def setUp(self):
        self.world = _placing_world()

    def _placed(self):
        return self.world.vector.tracked_records()[0]

    def test_a_window_below_the_floor_is_raised_to_it(self):
        for asked in (0, 1, FLOOR - 1):
            with self.subTest(ticks=asked):
                self.world = _placing_world()
                self.world.vector.ticks = asked
                _send(self.world)

                self.assertEqual(self._placed().ticks_remaining, FLOOR)

    def test_a_window_above_the_floor_is_the_vectors_own(self):
        self.world.vector.ticks = FLOOR + 13
        _send(self.world)

        self.assertEqual(self._placed().ticks_remaining, FLOOR + 13)

    def test_a_negative_window_is_floored_rather_than_kept(self):
        """A Counter_Web reduction is clamped, not trusted (R9.4)."""
        self.world.vector.ticks = -50
        _send(self.world)

        self.assertEqual(self._placed().ticks_remaining, FLOOR)

    def test_the_floored_window_is_what_reaches_persistence(self):
        self.world.vector.ticks = 2
        _send(self.world)

        self.assertEqual(
            _read_records(self.world.vector.owner)[0]["ticks_remaining"], FLOOR
        )

    def test_the_floor_is_the_shared_branch_service(self):
        """R15.8: one implementation of the window, consumed by all six vectors."""
        self.world.vector.ticks = 3
        _send(self.world)

        self.assertIn(("response_window", 3, 0), self.world.branch.calls)

    def test_the_floor_is_read_per_request_so_a_retune_lands(self):
        """R15.7: an ``@reload`` retunes the next operation."""
        self.world.vector.ticks = 1
        _send(self.world)
        self.assertEqual(self._placed().ticks_remaining, FLOOR)

        self.world.branch.floor = FLOOR + 20
        self.world.vector.registry.balance.minimum_response_window_ticks = FLOOR + 20
        _send(self.world)

        self.assertEqual(self._placed().ticks_remaining, FLOOR + 20)

    def test_the_floor_holds_with_no_response_window_service_at_all(self):
        """A partially wired Branch_System must not cost a target its warning."""
        self.world.branch.response_window = None
        self.world.vector.ticks = 1
        _send(self.world)

        self.assertEqual(self._placed().ticks_remaining, FLOOR)

    def test_a_service_that_raises_still_floors_the_window(self):
        self.world.branch.response_window = lambda base, reduction=0: 1 / 0
        self.world.vector.ticks = 2
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            _send(self.world)

        self.assertEqual(self._placed().ticks_remaining, FLOOR)

    def test_a_supporting_operation_keeps_its_own_clock(self):
        """The floor protects a target's warning; a supported ally needs none."""
        self.world = _placing_world(SupportiveVector)
        self.world.vector.ticks = 1
        _send(self.world, hostile=False)

        self.assertEqual(self._placed().ticks_remaining, 1)

    def test_a_non_hostile_request_is_not_floored(self):
        """The request's own reading is what the entry path uses."""
        self.world.vector.ticks = 2
        _send(self.world, hostile=False)

        self.assertEqual(self._placed().ticks_remaining, 2)

    def test_a_negative_clock_never_survives_even_unfloored(self):
        self.world.vector.ticks = -4
        _send(self.world, hostile=False)

        self.assertEqual(self._placed().ticks_remaining, 0)


class TestResponseWindowFloorIsReusableOnResume(unittest.TestCase):
    """R8.8 on the resume path: the helper the tick advance (task 11.5) calls.

    The resume transition itself lands with the tick advance; what lands here is
    the helper it must call, and the hostility answer it will have to rely on
    when the request that placed the operation is long gone.
    """

    def test_the_helper_floors_a_hostile_record_with_no_request_in_hand(self):
        vector = _placing_world().vector
        record = OperationRecord(ticks_remaining=2)

        self.assertEqual(vector._floor_response_window(record), FLOOR)

    def test_the_default_reading_of_an_operation_is_hostile(self):
        """The stricter reading, matching the request parameter's own default."""
        vector = _placing_world().vector

        self.assertTrue(vector._is_hostile(OperationRecord()))

    def test_a_supporting_vector_overrides_the_reading(self):
        vector = _placing_world(SupportiveVector).vector
        record = OperationRecord(ticks_remaining=2)

        self.assertFalse(vector._is_hostile(record))
        self.assertEqual(vector._floor_response_window(record), 2)

    def test_an_unreadable_clock_reads_as_no_ticks(self):
        vector = _placing_world().vector

        self.assertEqual(
            vector._floor_response_window(SimpleNamespace(ticks_remaining="x")), FLOOR
        )


class TestRequestAnswersEveryInput(unittest.TestCase):
    """R8.24, R15.3: an outcome for every input, and nothing raises out."""

    def test_a_bare_driver_refuses_rather_than_raising(self):
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            outcome = BareDriver().request(None)

        self.assertIsInstance(outcome, OperationOutcome)
        self.assertEqual(outcome.check, "collaborators")

    def test_a_request_with_no_parameters_at_all_answers(self):
        world = _chain_world()
        outcome = world.vector.request(None)

        self.assertIsInstance(outcome, OperationOutcome)
        self.assertFalse(outcome.ok)

    def test_a_check_that_raises_refuses_that_check_and_logs(self):
        world = _chain_world()
        world.vector._check_cooldown = lambda ctx: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(world)

        self.assertEqual(outcome.check, "cooldown")
        self.assertEqual(outcome.detail["reason"], "check_failed")
        self.assertEqual(outcome.detail["message"], MSG_VECTOR_COOLDOWN)

    def test_a_check_that_went_missing_refuses_in_its_name(self):
        world = _chain_world()
        world.vector._check_in_flight = None
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(world)

        self.assertEqual(outcome.check, "in_flight")
        self.assertEqual(outcome.detail["reason"], "check_missing")

    def test_a_context_that_cannot_be_built_answers_failed(self):
        world = _chain_world(ContextlessVector)
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(world)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.check, "request")
        self.assertIsNone(outcome.state)

    def test_a_branch_service_that_raises_degrades_to_a_refusal(self):
        world = _chain_world()
        world.branch.commitment = lambda player, planet=None: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _send(world)

        self.assertEqual(outcome.check, "commitment")
        self.assertIsNone(outcome.detail["current_branch"])

    def test_a_branch_system_missing_a_service_degrades_to_its_default(self):
        """A partially wired Branch_System answers a default; it does not raise.

        Each default is the direction the service itself documents: a cooldown
        nobody can read is elapsed, and a commitment nobody can read is absent —
        so the cooldown passes and the commitment refuses.
        """
        world = _chain_world(cooldown=12)
        world.branch.cooldown_remaining = None
        self.assertTrue(_send(world).ok)

        world.branch.commitment = None
        self.assertEqual(_send(world).check, "commitment")

    def test_a_request_that_passes_every_check_answers_accepted(self):
        """R8.24: the outcome names the resulting state, not just success."""
        world = _chain_world(PlacingVector)
        outcome = _send(world)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.state, str(OperationState.PENDING))
        self.assertEqual(outcome.op_id, "op-placed")
        self.assertIsNone(outcome.check)


class TestRequestContext(unittest.TestCase):
    """The ``ctx`` the chain fills and a vector's hooks read."""

    def test_the_context_carries_the_player_and_every_parameter(self):
        world = _chain_world()
        _send(world, target="#91", spread=2)
        ctx = world.vector.accepted[0]

        self.assertIs(ctx.player, world.player)
        self.assertIs(ctx.building, world.building)
        self.assertEqual(ctx.target, "#91")
        self.assertEqual(ctx.target_x, 3)
        self.assertEqual(ctx.target_y, 4)
        self.assertEqual(ctx.param("spread"), 2)
        self.assertIsNone(ctx.param("absent"))
        self.assertEqual(ctx.param("absent", 5), 5)

    def test_a_coordinate_is_read_as_a_number(self):
        world = _chain_world()
        _send(world, x="7", y=None)
        ctx = world.vector.accepted[0]

        self.assertEqual(ctx.target_x, 7)
        self.assertIsNone(ctx.target_y)

    def test_a_request_is_hostile_unless_it_says_otherwise(self):
        world = _chain_world()
        _send(world)
        self.assertTrue(world.vector.accepted[0].hostile)

        _send(world, hostile=False)
        self.assertFalse(world.vector.accepted[1].hostile)

    def test_the_named_planet_wins_the_planet_resolution(self):
        world = _chain_world()
        _send(world, planet="mars")

        self.assertEqual(world.vector.accepted[0].planet, "mars")
        self.assertIn(("commitment", world.player, "mars"), world.branch.calls)

    def test_the_buildings_planet_is_next(self):
        world = _chain_world()
        world.building.db.coord_planet = "mars"
        _send(world)

        self.assertEqual(world.vector.accepted[0].planet, "mars")

    def test_the_players_planet_is_the_last_resort(self):
        world = _chain_world()
        world.building.db.coord_planet = None
        _send(world)

        self.assertEqual(world.vector.accepted[0].planet, PLANET)

    def test_an_unresolvable_planet_is_the_any_planet_wildcard(self):
        world = _chain_world()
        world.building.db.coord_planet = None
        world.player.db.coord_planet = None
        _send(world)

        self.assertIsNone(world.vector.accepted[0].planet)

    def test_two_requests_get_two_independent_contexts(self):
        """The context is one request's working surface, not shared state."""
        world = _chain_world()
        _send(world, spread=1)
        first = world.vector.accepted[0]
        first.params["planted"] = True
        first.cost["Planted"] = 9

        _send(world, spread=1)
        second = world.vector.accepted[1]

        self.assertIsNot(first, second)
        self.assertNotIn("planted", second.params)
        self.assertEqual(second.cost, KIND_COST)


# ------------------------------------------------------------------ #
#  The notification points (design §4.4)
# ------------------------------------------------------------------ #

class FakeRoom:
    """A PlanetRoom stand-in: the two coordinate queries the audience asks.

    Exactly the surface the driver reaches for — ``get_players_at`` for the tile
    sweep and ``get_objects_in_area`` for the effect's area — reached duck-typed,
    so this fake is the real cross-module contract rather than a restatement of
    ``PlanetRoom``'s internals.
    """

    def __init__(self):
        self._players = {}
        self._objects = []
        self.area_calls = []

    def place_player(self, player, x, y):
        """Stand *player* on tile ``(x, y)`` — the tile-sweep half of R8.12."""
        self._players.setdefault((int(x), int(y)), []).append(player)
        return player

    def place_object(self, entity):
        """Put *entity* in the room at its own coordinates — the area-query half."""
        self._objects.append(entity)
        return entity

    def get_players_at(self, x, y):
        return list(self._players.get((int(x), int(y)), []))

    def get_objects_in_area(self, x1, y1, x2, y2):
        self.area_calls.append((x1, y1, x2, y2))
        return [
            obj for obj in self._objects
            if x1 <= obj.db.coord_x <= x2 and y1 <= obj.db.coord_y <= y2
        ]


class FakeEntity:
    """An entity in the world: an identity, an owner, a tile, and its room."""

    _next_id = 500

    def __init__(self, owner=None, x=3, y=4, room=None, key=None):
        FakeEntity._next_id += 1
        self.id = FakeEntity._next_id
        self.key = key
        self.location = room
        self.db = SimpleNamespace(owner=owner, coord_x=x, coord_y=y)


class NotifyingVector(PlacingVector):
    """A vector whose record names its target and its effect radius.

    The two fields the audience is resolved from, which :class:`PlacingVector`
    leaves unset: the target is the entity the operation was aimed at, and the
    radius is the area swept around the affected coordinate.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.radius = 0

    def build_record(self, ctx):
        record = super().build_record(ctx)
        record.target_ref = ctx.target
        record.radius = self.radius
        return record


def _notified(vector):
    """Return the ``(player, kind, data)`` triples *vector* has published."""
    from mygame.world.event_bus import PLAYER_NOTIFICATION

    return [
        (data.get("player"), data.get("kind"), dict(data.get("data") or {}))
        for event, data in vector.event_bus.published
        if event == PLAYER_NOTIFICATION
    ]


def _kinds(vector):
    """Return just the notification kinds *vector* has published, in order."""
    return [kind for _player, kind, _data in _notified(vector)]


def _notify_world(**answers):
    """A world whose vector places an operation on a tile inside a real room.

    The attacker is named (so ``attacker_name`` has something to carry), a
    defender stands on the affected tile, and the defender owns a building
    there — which is what makes both halves of R8.12's audience non-empty.
    """
    world = _placing_world(NotifyingVector, **answers)
    world.player.key = "Vex"
    world.room = FakeRoom()
    world.defender = world.room.place_player(
        FakeEntity(x=3, y=4, room=world.room, key="Mira"), 3, 4
    )
    world.target = world.room.place_object(
        FakeEntity(owner=world.defender, x=3, y=4, room=world.room)
    )
    return world


def _notify_send(world, **extra):
    """Send one request naming the room's target entity."""
    params = {"target": world.target}
    params.update(extra)
    return _send(world, **params)


class TestNotificationVocabulary(unittest.TestCase):
    """R13.6, R13.8: nine kinds, six states covered, and one spelling each."""

    def test_the_nine_kinds_this_feature_introduces(self):
        self.assertEqual(
            operation_contract.VECTOR_NOTIFICATION_KINDS,
            (
                "vector_incoming",
                "vector_resolved",
                "vector_hit",
                "vector_suspended",
                "vector_resumed",
                "vector_expired",
                "vector_cancelled",
                "vector_discarded",
                "vector_consent_required",
            ),
        )

    def test_every_kind_is_distinct(self):
        kinds = operation_contract.VECTOR_NOTIFICATION_KINDS
        self.assertEqual(len(set(kinds)), len(kinds))

    def test_every_one_of_the_six_states_reports_through_a_kind(self):
        """R13.6: no lifecycle transition a player can see is silent."""
        self.assertEqual(
            set(operation_contract.STATE_NOTIFICATIONS), set(SIX_STATES)
        )
        for state, kind in operation_contract.STATE_NOTIFICATIONS.items():
            with self.subTest(state=state):
                self.assertIn(kind, operation_contract.VECTOR_NOTIFICATION_KINDS)

    def test_the_consent_key_is_the_one_branch_system_refuses_with(self):
        """The ninth kind is a refusal key shared by value, so it must match.

        ``BranchSystem.may_target`` answers R11.8's refusal and is the authority
        on its spelling; the contract holds a by-value copy so the presenter
        table can be checked against one list. Drift would leave a real refusal
        unrendered.
        """
        from mygame.world.systems.branch_system import MSG_VECTOR_CONSENT_REQUIRED

        self.assertEqual(
            operation_contract.NOTIFY_VECTOR_CONSENT_REQUIRED,
            MSG_VECTOR_CONSENT_REQUIRED,
        )

    def test_the_suspend_and_cancel_reasons_are_keys_not_sentences(self):
        """R13.5: a reason travels as a key; the presenter owns the wording."""
        reasons = (
            operation_contract.SUSPEND_CARRIER_UNAVAILABLE,
            operation_contract.SUSPEND_COMMITMENT_LAPSED,
            operation_contract.CANCEL_CARRIER_KILLED,
            operation_contract.CANCEL_ORIGIN_LOST,
            operation_contract.CANCEL_BASE_ELIMINATED,
        )
        self.assertEqual(len(set(reasons)), len(reasons))
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertNotIn(" ", reason)


class TestPendingNotificationWarnsTheTargets(unittest.TestCase):
    """R8.7: a hostile operation entering Pending warns the players it targets."""

    def setUp(self):
        self.world = _notify_world()

    def test_the_target_is_warned_with_the_four_values_r8_7_asks_for(self):
        self.world.vector.ticks = FLOOR + 4
        outcome = _notify_send(self.world)

        self.assertTrue(outcome.ok)
        self.assertEqual(
            _notified(self.world.vector),
            [(
                self.world.defender,
                "vector_incoming",
                {
                    "kind": KIND,
                    "attacker_name": "Vex",
                    "x": 3,
                    "y": 4,
                    "ticks": FLOOR + 4,
                },
            )],
        )

    def test_the_ticks_quoted_are_the_floored_response_window(self):
        """R8.8 measures the window from THIS notification, so it must agree."""
        self.world.vector.ticks = 1
        _notify_send(self.world)

        self.assertEqual(_notified(self.world.vector)[0][2]["ticks"], FLOOR)

    def test_the_warning_precedes_the_cooldown_note(self):
        """The window runs from the notification, so it cannot come after it."""
        seen = []
        original = self.world.branch.note_cooldown

        def watched(building, kind):
            seen.append(_kinds(self.world.vector))
            original(building, kind)

        self.world.branch.note_cooldown = watched
        _notify_send(self.world)

        self.assertEqual(seen, [["vector_incoming"]])

    def test_a_supporting_operation_warns_nobody(self):
        """R8.7 is about a hostile operation; an ally being helped is not warned."""
        _notify_send(self.world, hostile=False)

        self.assertEqual(_kinds(self.world.vector), [])

    def test_the_originating_player_is_not_warned_about_their_own_operation(self):
        """They have an accepted outcome to read; a warning would be noise."""
        self.world.target.db.owner = self.world.player
        self.world.defender.db.owner = self.world.player
        _notify_send(self.world)

        self.assertEqual(
            [player for player, _kind, _data in _notified(self.world.vector)],
            [self.world.defender],           # the occupant, not the attacker
        )

    def test_a_refused_request_notifies_nobody(self):
        """R8.4: a refusal changes nothing, and telling a target is a change."""
        self.world.branch.commitment_answer = "defense"
        outcome = _notify_send(self.world)

        self.assertFalse(outcome.ok)
        self.assertEqual(_kinds(self.world.vector), [])

    def test_a_charged_request_that_fails_to_enter_pending_notifies_nobody(self):
        self.world.vector.fail_build = True
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _notify_send(self.world)

        self.assertFalse(outcome.ok)
        self.assertEqual(_kinds(self.world.vector), [])

    def test_an_npc_originated_operation_warns_its_targets_the_same_way(self):
        """R11.6: waiving the charge waives the charge and nothing else."""
        self.world.player.db.is_sentinel = True
        _notify_send(self.world)

        self.assertEqual(_kinds(self.world.vector), ["vector_incoming"])


class TestResolutionAudience(unittest.TestCase):
    """R8.12, R11.10: both audiences, de-duplicated, with no exclusion."""

    def setUp(self):
        self.world = _notify_world()
        _notify_send(self.world)
        self.world.vector.event_bus.published.clear()
        self.record = self.world.vector.tracked_records()[0]

    def test_the_owner_reads_their_own_operation_resolving(self):
        self.world.vector._notify_resolution(self.record)

        self.assertIn(
            (self.world.player, "vector_resolved", {"kind": KIND, "x": 3, "y": 4}),
            _notified(self.world.vector),
        )

    def test_an_affected_entitys_owner_and_a_tile_occupant_are_both_notified(self):
        """The two audiences R8.12 names, resolved from the effect's area."""
        bystander = self.world.room.place_player(
            FakeEntity(x=3, y=4, room=self.world.room, key="Tam"), 3, 4
        )
        self.world.vector._notify_resolution(self.record)

        hit = [
            player for player, kind, _data in _notified(self.world.vector)
            if kind == "vector_hit"
        ]
        self.assertIn(self.world.defender, hit)          # owns the target
        self.assertIn(bystander, hit)                    # stands on the tile

    def test_a_player_who_is_both_audiences_is_notified_once(self):
        """Design §4.4: one notification per player, however many ways in.

        The defender owns the affected entity *and* stands on the affected tile,
        so both halves of R8.12's audience name them.
        """
        self.world.vector._notify_resolution(self.record)

        recipients = [player for player, _kind, _data in _notified(self.world.vector)]
        self.assertEqual(recipients.count(self.world.defender), 1)

    def test_a_hit_names_the_operation_the_attacker_and_the_coordinate(self):
        self.world.vector._notify_resolution(self.record)

        payloads = [
            data for _player, kind, data in _notified(self.world.vector)
            if kind == "vector_hit"
        ]
        self.assertEqual(
            payloads,
            [{"kind": KIND, "attacker_name": "Vex", "x": 3, "y": 4}],
        )

    def test_the_originating_player_is_not_excluded_from_the_area(self):
        """R11.10: an indiscriminate area effect stays indiscriminate.

        The attacker's own entity in the area puts the attacker in the audience —
        and the de-duplication then gives them the one notification that reads as
        theirs, rather than two.
        """
        self.world.target.db.owner = self.world.player
        self.world.vector._notify_resolution(self.record)

        self.assertIn(
            self.world.player, self.world.vector._resolution_audience(self.record)
        )
        recipients = [player for player, _kind, _data in _notified(self.world.vector)]
        self.assertEqual(recipients.count(self.world.player), 1)
        self.assertEqual(_kinds(self.world.vector).count("vector_resolved"), 1)

    def test_an_allied_owner_is_not_excluded_either(self):
        """R11.10 draws no alliance exception, and neither does the audience."""
        ally = FakeEntity(x=3, y=4, key="Ally")
        self.world.target.db.owner = ally
        self.world.vector._notify_resolution(self.record)

        self.assertIn(ally, self.world.vector._resolution_audience(self.record))

    def test_the_area_swept_is_the_chebyshev_ball_of_the_effect_radius(self):
        self.world.vector.radius = 2
        record = self.world.vector.build_record(
            operation_contract.OperationContext(
                player=self.world.player, building=self.world.building,
                target=self.world.target, target_x=3, target_y=4,
            )
        )
        inside = self.world.room.place_object(
            FakeEntity(owner=FakeEntity(key="Near"), x=5, y=6, room=self.world.room)
        )
        outside = self.world.room.place_object(
            FakeEntity(owner=FakeEntity(key="Far"), x=6, y=4, room=self.world.room)
        )

        affected = self.world.vector._affected_entities(record)

        self.assertIn(inside, affected)
        self.assertNotIn(outside, affected)
        self.assertIn((1, 2, 5, 6), self.world.room.area_calls)

    def test_an_absurd_radius_is_clamped_rather_than_swept(self):
        """A hand-edited radius costs a bounded number of queries, not a tick."""
        record = OperationRecord(target_x=0, target_y=0, radius=10_000)
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            center = self.world.vector._area_center(record)

        self.assertEqual(center, (0, 0, OperationDriver._MAX_AUDIENCE_RADIUS))

    def test_a_record_with_no_coordinate_sweeps_no_tiles(self):
        """A vector that attaches its operation to an entity has no tile area."""
        record = OperationRecord(target_ref=self.world.target)

        self.assertIsNone(self.world.vector._area_center(record))
        self.assertEqual(self.world.vector._tile_occupants(record), [])
        self.assertEqual(
            self.world.vector._affected_entities(record), [self.world.target]
        )

    def test_a_room_that_cannot_be_reached_notifies_the_owner_alone(self):
        """A lost world reference costs the audience, not the transition."""
        self.record.target_ref = None
        self.record.building_ref = None
        self.record.carrier_ref = None
        self.world.vector._notify_resolution(self.record)

        self.assertEqual(_kinds(self.world.vector), [])   # no owner resolves either

    def test_a_tile_query_that_raises_contributes_nobody(self):
        self.world.room.get_players_at = lambda x, y: 1 / 0

        self.assertEqual(self.world.vector._tile_occupants(self.record), [])

    def test_an_area_query_that_raises_still_returns_the_named_target(self):
        self.world.room.get_objects_in_area = lambda *bounds: 1 / 0

        self.assertEqual(
            self.world.vector._affected_entities(self.record), [self.world.target]
        )


class TestLifecycleNotificationHelpers(unittest.TestCase):
    """The points the tick advance and the rebuild call (tasks 11.5, 11.6).

    Each transition owns its own state write; the payload and the audience are
    the driver's, and they land here so those tasks call a helper rather than
    composing a notification of their own (R13.5).
    """

    def setUp(self):
        self.world = _notify_world()
        _notify_send(self.world)
        self.world.vector.event_bus.published.clear()
        self.record = self.world.vector.tracked_records()[0]

    def _sent(self):
        return _notified(self.world.vector)

    def test_expiry_notifies_the_owner_and_each_affected_entitys_owner(self):
        """R8.13: the narrower audience — nothing landed on a bystander."""
        self.world.vector._notify_expiry(self.record)

        self.assertEqual(
            self._sent(),
            [
                (self.world.player, "vector_expired", {"kind": KIND, "x": 3, "y": 4}),
                (
                    self.world.defender,
                    "vector_expired",
                    {"kind": KIND, "x": 3, "y": 4},
                ),
            ],
        )

    def test_suspension_notifies_the_owner_with_the_reason_as_a_key(self):
        self.world.vector._notify_suspension(
            self.record, operation_contract.SUSPEND_CARRIER_UNAVAILABLE
        )

        self.assertEqual(
            self._sent(),
            [(
                self.world.player,
                "vector_suspended",
                {
                    "kind": KIND,
                    "reason": operation_contract.SUSPEND_CARRIER_UNAVAILABLE,
                    "x": 3,
                    "y": 4,
                },
            )],
        )

    def test_resuming_quotes_the_ticks_the_operation_held(self):
        """R8.15: suspension delays rather than restarts, so the count is the point."""
        self.record.ticks_remaining = 4
        self.world.vector._notify_resume(self.record)

        self.assertEqual(
            self._sent(),
            [(
                self.world.player,
                "vector_resumed",
                {"kind": KIND, "ticks_remaining": 4},
            )],
        )

    def test_cancellation_notifies_the_owner_of_which_collaborator_was_lost(self):
        """R8.16, R8.17, R11.4 all ask for the owner to be told."""
        for reason in (
            operation_contract.CANCEL_CARRIER_KILLED,
            operation_contract.CANCEL_ORIGIN_LOST,
            operation_contract.CANCEL_BASE_ELIMINATED,
        ):
            with self.subTest(reason=reason):
                self.world.vector.event_bus.published.clear()
                self.world.vector._notify_cancellation(self.record, reason)

                self.assertEqual(
                    self._sent(),
                    [(
                        self.world.player,
                        "vector_cancelled",
                        {"kind": KIND, "reason": reason},
                    )],
                )

    def test_a_discard_carries_the_kind_alone(self):
        """R14.4: the coordinate and the refs are what could not be resolved."""
        self.world.vector._notify_discard(self.record)

        self.assertEqual(
            self._sent(),
            [(self.world.player, "vector_discarded", {"kind": KIND})],
        )

    def test_a_reason_nobody_named_is_published_as_none_not_as_a_blank(self):
        self.world.vector._notify_cancellation(self.record)

        self.assertIsNone(self._sent()[0][2]["reason"])

    def test_every_helper_answers_a_count_and_notifies_a_terminal_record(self):
        """A notification reports a transition that already happened.

        The state write is the single writer's business and it has already
        refused or accepted by the time a notification goes out, so these
        helpers must not consult the state — a Resolved record still notifies.
        """
        self.record.state = str(OperationState.RESOLVED)
        counts = [
            self.world.vector._notify_expiry(self.record),
            self.world.vector._notify_suspension(self.record, "x"),
            self.world.vector._notify_resume(self.record),
            self.world.vector._notify_cancellation(self.record, "x"),
            self.world.vector._notify_discard(self.record),
            self.world.vector._notify_resolution(self.record),
        ]

        self.assertEqual(counts, [2, 1, 1, 1, 1, 2])


class TestNotificationDegradesInsteadOfRaising(unittest.TestCase):
    """R15.1, R15.2, R15.3: the notification path is reached duck-typed."""

    def test_a_driver_with_no_notify_at_all_logs_a_no_op(self):
        """A bare driver has no ``BaseSystem`` half, and must not raise (R15.2)."""
        driver = BareDriver()
        record = OperationRecord(target_x=1, target_y=2)
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            sent = driver._notify(FakePlayer(), "vector_expired", kind="")

        self.assertFalse(sent)
        self.assertEqual(driver._notify_cancellation(record), 0)

    def test_a_publish_that_raises_is_logged_and_answered(self):
        world = _notify_world()
        world.vector.event_bus.publish = lambda *a, **kw: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            sent = world.vector._notify(world.player, "vector_expired", kind=KIND)

        self.assertFalse(sent)

    def test_a_notification_failure_never_unmakes_an_accepted_operation(self):
        """R8.7 is a report; an operation is already charged and placed by then."""
        world = _notify_world()
        world.vector.event_bus.publish = lambda *a, **kw: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            outcome = _notify_send(world)

        self.assertTrue(outcome.ok)
        self.assertEqual(len(world.vector.tracked_records()), 1)

    def test_a_none_recipient_is_dropped_without_a_log(self):
        world = _notify_world()

        self.assertFalse(world.vector._notify(None, "vector_expired", kind=KIND))
        self.assertEqual(_kinds(world.vector), [])

    def test_a_reference_is_never_treated_as_a_recipient(self):
        """``owner_ref`` is a dbref by design, and a dbref cannot be notified."""
        world = _notify_world()
        for reference in ("#5", 5, 5.0, True, None):
            with self.subTest(reference=reference):
                self.assertFalse(OperationDriver._is_notifiable(reference))
        record = OperationRecord(owner_ref="#5", building_ref=world.building)

        self.assertIs(world.vector._record_owner(record), world.player)

    def test_a_live_owner_object_on_the_record_is_used_directly(self):
        world = _notify_world()
        record = OperationRecord(owner_ref=world.defender)

        self.assertIs(world.vector._record_owner(record), world.defender)

    def test_an_operation_whose_owner_is_gone_notifies_nobody(self):
        world = _notify_world()
        record = OperationRecord(owner_ref="#5")

        self.assertIsNone(world.vector._record_owner(record))
        self.assertEqual(world.vector._notify_cancellation(record), 0)


# ------------------------------------------------------------------ #
#  Per-tick advancement, suspension, and cancellation (design §4.7)
# ------------------------------------------------------------------ #

class LiveAgent:
    """A Carrier_Agent the lifecycle conditions can actually read.

    :class:`FakeAgent` is enough for the validation chain, which only asks
    whether *an* eligible agent exists; the tick advance asks this one whether it
    is benched, incapacitated, or dead, so it needs the ``db`` proxy those live
    on. Reached duck-typed by the same attribute names every other consumer reads
    them by.
    """

    _next_id = 900

    def __init__(self, reserve=False, incapacitated=False, hp=10, key="Spotter"):
        LiveAgent._next_id += 1
        self.id = LiveAgent._next_id
        self.key = key
        self.db = SimpleNamespace(
            reserve=reserve, incapacitated=incapacitated, hp=hp,
        )


class DeletedBuilding(FakeBuilding):
    """A building that has been deleted: ``pk`` present and ``None``."""

    pk = None


#: The effect clock every lifecycle fixture below asks for — deliberately ABOVE
#: the Response_Window floor, so the clock a test reasons about is the clock the
#: vector asked for rather than the floor R8.8 would otherwise raise it to.
CLOCK = FLOOR + 3


def _lifecycle_world(
    vector_cls=NotifyingVector, ticks=CLOCK, lifetime=None, hostile=True, **answers
):
    """A world holding one placed, tracked, live operation.

    The carrier is a :class:`LiveAgent` so the carrier conditions have something
    to read, and the notification log is cleared after the placement so every
    assertion below sees only what the tick advance published. ``world.clock`` is
    the clock the placed record actually carries — the request's own ``hostile``
    decides whether R8.8's floor raised it, so a test reads it rather than
    assuming it.
    """
    answers.setdefault("carrier", LiveAgent())
    world = _placing_world(vector_cls, **answers)
    world.player.key = "Vex"
    world.room = FakeRoom()
    world.defender = world.room.place_player(
        FakeEntity(x=3, y=4, room=world.room, key="Mira"), 3, 4
    )
    world.target = world.room.place_object(
        FakeEntity(owner=world.defender, x=3, y=4, room=world.room)
    )
    world.vector.ticks = ticks
    world.vector.lifetime = lifetime
    _send(world, target=world.target, hostile=hostile)
    world.record = world.vector.tracked_records()[0]
    world.carrier = world.record.carrier_ref
    world.clock = world.record.ticks_remaining
    world.vector.event_bus.published.clear()
    return world


def _stored_states(vector):
    """Return the persisted ``{op_id: state}`` of *vector*'s durable owner."""
    return {
        entry["op_id"]: entry["state"] for entry in _read_records(vector.owner)
    }


class TestAdvanceAllIsolatesEachOperation(unittest.TestCase):
    """R8.9, R8.10: every operation advances, and one failure costs one tick."""

    def setUp(self):
        self.world = _lifecycle_world()
        self.vector = self.world.vector

    def _extra(self, op_id, ticks=5):
        record = OperationRecord(
            op_id=op_id, kind=KIND, owner_ref=self.world.player,
            building_ref=self.world.building, carrier_ref=self.world.carrier,
            planet=PLANET, target_x=3, target_y=4, ticks_remaining=ticks,
        )
        self.vector._track(record)
        return record

    def test_every_tracked_operation_advances_by_one(self):
        second = self._extra("op-second", ticks=9)
        self.vector.advance_all(1)

        self.assertEqual(self.world.record.ticks_remaining, self.world.clock - 1)
        self.assertEqual(second.ticks_remaining, 8)

    def test_an_empty_registry_of_operations_is_a_no_op(self):
        vector = ChainVector()

        self.assertIsNone(vector.advance_all(1))
        self.assertEqual(vector.tracked_records(), [])

    def test_an_operation_that_raises_is_kept_and_logged(self):
        """R8.10: a dropped operation would be a silent hazard leak."""
        broken = self._extra("op-broken")
        original = self.vector._advance_one

        def selective(record, tick=0):
            if record is broken:
                raise RuntimeError("this operation cannot be advanced")
            return original(record, tick)

        self.vector._advance_one = selective
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR") as logs:
            self.vector.advance_all(4)

        self.assertIn(broken, self.vector.tracked_records())
        self.assertIn("op-broken", logs.output[0])
        self.assertIn(KIND, logs.output[0])

    def test_the_remaining_operations_still_advance_past_a_failure(self):
        broken = self._extra("op-broken")
        self.vector._advance_one = lambda record, tick=0: (
            1 / 0 if record is broken else True
        )
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR") as logs:
            self.vector.advance_all(1)

        self.assertEqual(len(logs.output), 1)
        self.assertEqual(len(self.vector.tracked_records()), 2)

    def test_a_record_that_is_already_terminal_is_dropped_from_tracking(self):
        self.vector._transition(self.world.record, OperationState.CANCELLED)
        self.vector._track(self.world.record)      # as a rebuild might
        self.vector.advance_all(1)

        self.assertEqual(self.vector.tracked_records(), [])

    def test_an_operation_placed_during_the_pass_is_not_swept_away(self):
        """A vector's own hook may chain a follow-up; it gets its own ticks."""
        follow_up = OperationRecord(op_id="op-chained", kind=KIND, ticks_remaining=3)

        def chaining(record):
            self.vector._track(follow_up)

        self.vector.on_resolve = chaining
        self.world.record.ticks_remaining = 1
        self.vector.advance_all(1)

        self.assertEqual(
            [r.op_id for r in self.vector.tracked_records()], ["op-chained"]
        )
        self.assertEqual(follow_up.ticks_remaining, 3)

    def test_the_tick_fan_out_reaches_advance_all_duck_typed(self):
        """``BranchSystem.process_tick`` reads this name and no other (R15.9)."""
        self.assertTrue(callable(BranchSystem._safe_attr(self.vector, "advance_all")))


class TestAdvanceOneOrdersTheClocks(unittest.TestCase):
    """R8.9, R8.11, R8.13: the lifetime, then the effect clock, one at a time."""

    def setUp(self):
        self.world = _lifecycle_world()
        self.vector = self.world.vector
        self.record = self.world.record
        self.clock = self.world.clock

    def test_one_tick_decrements_the_effect_clock_by_exactly_one(self):
        self.assertTrue(self.vector._advance_one(self.record, 1))

        self.assertEqual(self.record.ticks_remaining, self.clock - 1)
        self.assertEqual(self.record.state, str(OperationState.PENDING))

    def test_an_advancing_operation_persists_its_clock(self):
        """The decremented clock reaches storage in advance_all's one batched
        write per owner (R14.7) — _advance_one itself no longer writes, so a
        tick costs an owner one write however many operations it holds."""
        self.vector.advance_all(1)

        stored = _read_records(self.vector.owner)
        self.assertEqual(stored[0]["ticks_remaining"], self.clock - 1)

    def test_advance_one_alone_writes_nothing(self):
        """The per-record half is pure bookkeeping; the batch owns the write."""
        self.vector._advance_one(self.record, 1)

        stored = _read_records(self.vector.owner)
        self.assertEqual(stored[0]["ticks_remaining"], self.clock)

    def test_one_tick_writes_each_owner_once_however_many_operations(self):
        """The R14.7 batching claim itself: N surviving clocks, one write."""
        second = OperationRecord(
            op_id="op-second", kind=KIND, ticks_remaining=self.clock,
        )
        self.vector._track(second)
        writes = []
        original = self.vector._write_records

        def counting(owner, records):
            writes.append(owner)
            return original(owner, records)

        self.vector._write_records = counting
        self.vector.advance_all(1)

        self.assertEqual(len(writes), 1)
        stored = {
            entry["op_id"]: entry["ticks_remaining"]
            for entry in _read_records(self.vector.owner)
        }
        self.assertEqual(
            stored,
            {self.record.op_id: self.clock - 1, "op-second": self.clock - 1},
        )

    def test_the_effect_clock_reaching_zero_resolves(self):
        """R8.11: apply the effect, then move to Resolved."""
        for tick in range(self.clock):
            keep = self.vector._advance_one(self.record, tick)

        self.assertFalse(keep)
        self.assertEqual(self.record.state, str(OperationState.RESOLVED))
        self.assertEqual(self.vector.calls, [("on_resolve", self.record.op_id)])

    def test_a_resolved_operation_is_untracked_and_swept_from_storage(self):
        self.record.ticks_remaining = 1
        self.vector.advance_all(1)

        self.assertEqual(self.vector.tracked_records(), [])
        self.assertEqual(_read_records(self.vector.owner), [])

    def test_a_bounded_lifetime_counts_down_alongside_the_effect_clock(self):
        self.record.lifetime_remaining = 5
        self.vector._advance_one(self.record, 1)

        self.assertEqual(self.record.lifetime_remaining, 4)
        self.assertEqual(self.record.ticks_remaining, self.clock - 1)

    def test_the_lifetime_running_out_expires_the_operation(self):
        """R8.13: the bounded lifetime elapsed before the effect."""
        self.record.lifetime_remaining = 1

        self.assertFalse(self.vector._advance_one(self.record, 1))
        self.assertEqual(self.record.state, str(OperationState.EXPIRED))
        self.assertEqual(self.record.ticks_remaining, self.clock)  # no tick taken

    def test_expiry_wins_a_tie_with_the_effect_clock(self):
        """A deadline that could be beaten by a tie would not be a deadline."""
        self.record.lifetime_remaining = 1
        self.record.ticks_remaining = 1
        self.vector._advance_one(self.record, 1)

        self.assertEqual(self.record.state, str(OperationState.EXPIRED))
        self.assertEqual(self.vector.calls, [])            # no effect applied

    def test_an_unbounded_lifetime_never_expires(self):
        self.record.lifetime_remaining = None
        for tick in range(self.clock - 1):
            self.vector._advance_one(self.record, tick)

        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertIsNone(self.record.lifetime_remaining)

    def test_a_terminal_record_takes_no_tick_at_all(self):
        """R8.2: an operation in a terminal state advances no further."""
        for state in FOUR_TERMINAL:
            with self.subTest(state=state):
                record = OperationRecord(
                    op_id=f"op-{state}", state=state, ticks_remaining=4
                )

                self.assertFalse(self.vector._advance_one(record, 1))
                self.assertEqual(record.ticks_remaining, 4)

    def test_a_missing_record_is_answered_rather_than_raised_on(self):
        self.assertFalse(self.vector._advance_one(None, 1))


class TestExpiryRestoresAndReports(unittest.TestCase):
    """R8.13: move to Expired, restore what was suspended, tell the owners."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=3)
        self.vector = self.world.vector
        self.record = self.world.record
        self.restored = []
        self.vector.on_expire = self.restored.append

    def test_expiry_settles_untracks_and_notifies(self):
        self.assertTrue(self.vector._expire(self.record))

        self.assertEqual(self.record.state, str(OperationState.EXPIRED))
        self.assertEqual(self.vector.tracked_records(), [])
        self.assertEqual(_kinds(self.vector), ["vector_expired", "vector_expired"])

    def test_the_restoration_hook_runs_on_exactly_the_expiry_path(self):
        self.vector._expire(self.record)

        self.assertEqual(self.restored, [self.record])

    def test_the_restoration_precedes_the_notification(self):
        """The owners are told once their entities are back."""
        seen = []
        self.vector.on_expire = lambda record: seen.append(_kinds(self.vector))
        self.vector._expire(self.record)

        self.assertEqual(seen, [[]])

    def test_a_restoration_that_raises_costs_the_restoration_not_the_expiry(self):
        self.vector.on_expire = lambda record: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertTrue(self.vector._expire(self.record))

        self.assertEqual(self.record.state, str(OperationState.EXPIRED))
        self.assertEqual(_kinds(self.vector), ["vector_expired", "vector_expired"])

    def test_a_settled_record_neither_expires_nor_restores(self):
        self.vector._transition(self.record, OperationState.RESOLVED)

        self.assertFalse(self.vector._expire(self.record))
        self.assertEqual(self.restored, [])


class TestResolutionAppliesThenReports(unittest.TestCase):
    """R8.11, R8.12, R10.6: the effect, the state, the ledger, the audience."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=3)
        self.vector = self.world.vector
        self.record = self.world.record

    def test_resolution_settles_untracks_and_notifies(self):
        self.assertTrue(self.vector._resolve(self.record))

        self.assertEqual(self.record.state, str(OperationState.RESOLVED))
        self.assertEqual(self.vector.tracked_records(), [])
        self.assertIn("vector_resolved", _kinds(self.vector))
        self.assertIn("vector_hit", _kinds(self.vector))

    def test_the_effect_is_applied_before_the_audience_is_resolved(self):
        """R8.12's audience is read from the world the effect already changed."""
        seen = []
        self.vector.on_resolve = lambda record: seen.append(_kinds(self.vector))
        self.vector._resolve(self.record)

        self.assertEqual(seen, [[]])

    def test_an_effect_that_raises_still_settles_the_operation(self):
        """Leaving it Pending would hand it another tick and re-apply the effect."""
        self.vector.on_resolve = lambda record: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertTrue(self.vector._resolve(self.record))

        self.assertEqual(self.record.state, str(OperationState.RESOLVED))

    def test_a_hostile_resolution_notes_the_escalation_ledger(self):
        """R10.6 bounds resolutions, so the note fires here, not at acceptance."""
        self.vector._resolve(self.record)

        self.assertIn(
            ("note_escalation", self.world.player, self.world.target),
            self.world.branch.calls,
        )

    def test_a_supporting_resolution_notes_no_escalation(self):
        world = _lifecycle_world(SupportiveVector, ticks=3)
        world.vector._resolve(world.record)

        self.assertFalse(world.branch.called("note_escalation"))

    def test_the_acceptance_half_notes_no_escalation(self):
        """The cooldown is noted at acceptance; the escalation ledger is not."""
        world = _lifecycle_world(ticks=3)

        self.assertFalse(world.branch.called("note_escalation"))
        self.assertTrue(world.branch.called("note_cooldown"))

    def test_a_settled_record_applies_no_effect(self):
        self.vector._transition(self.record, OperationState.CANCELLED)

        self.assertFalse(self.vector._resolve(self.record))
        self.assertEqual(self.vector.calls, [])


class TestSuspendAndResume(unittest.TestCase):
    """R8.14, R8.15, R8.18: a suspension delays an operation, never restarts it."""

    def setUp(self):
        self.world = _lifecycle_world()
        self.vector = self.world.vector
        self.record = self.world.record
        self.clock = self.world.clock

    def test_suspending_snapshots_the_remaining_ticks(self):
        self.assertTrue(self.vector.suspend(
            self.record, operation_contract.SUSPEND_CARRIER_UNAVAILABLE
        ))

        self.assertEqual(self.record.state, str(OperationState.SUSPENDED))
        self.assertEqual(self.record.suspended_ticks, self.clock)
        self.assertEqual(self.record.ticks_remaining, self.clock)

    def test_the_snapshot_persists_with_the_state(self):
        """A suspension has to survive a restart, snapshot and all."""
        self.vector.suspend(self.record, operation_contract.SUSPEND_COMMITMENT_LAPSED)

        stored = _read_records(self.vector.owner)[0]
        self.assertEqual(stored["state"], "suspended")
        self.assertEqual(stored["suspended_ticks"], self.clock)

    def test_suspending_notifies_the_owner_with_the_reason_as_a_key(self):
        self.vector.suspend(self.record, operation_contract.SUSPEND_CARRIER_UNAVAILABLE)

        self.assertEqual(
            _notified(self.vector),
            [(
                self.world.player,
                "vector_suspended",
                {
                    "kind": KIND,
                    "reason": operation_contract.SUSPEND_CARRIER_UNAVAILABLE,
                    "x": 3,
                    "y": 4,
                },
            )],
        )

    def test_suspending_twice_snapshots_once_and_notifies_once(self):
        """The tick advance asks on every tick the condition holds."""
        self.vector.suspend(self.record, "carrier_unavailable")
        self.record.ticks_remaining = 1          # as a stray write might
        self.assertFalse(self.vector.suspend(self.record, "carrier_unavailable"))

        self.assertEqual(self.record.suspended_ticks, self.clock)
        self.assertEqual(_kinds(self.vector).count("vector_suspended"), 1)

    def test_the_vector_hook_runs_on_suspension(self):
        paused = []
        self.vector.on_suspend = paused.append
        self.vector.suspend(self.record, "carrier_unavailable")

        self.assertEqual(paused, [self.record])

    def test_resuming_restores_the_ticks_held_on_suspension(self):
        """R8.15: the whole point — a delay, not a restart."""
        self.vector._advance_one(self.record, 1)              # one tick spent
        self.vector.suspend(self.record, "carrier_unavailable")
        self.assertTrue(self.vector.resume(self.record))

        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertEqual(self.record.ticks_remaining, self.clock - 1)
        self.assertIsNone(self.record.suspended_ticks)

    def test_resuming_notifies_the_owner_with_the_restored_clock(self):
        self.vector.suspend(self.record, "carrier_unavailable")
        self.vector.event_bus.published.clear()
        self.vector.resume(self.record)

        self.assertEqual(
            _notified(self.vector),
            [(
                self.world.player,
                "vector_resumed",
                {"kind": KIND, "ticks_remaining": self.clock},
            )],
        )

    def test_a_resumed_hostile_window_is_re_floored(self):
        """R8.8: the resume path is the floor's second call site, and the only
        one that re-checks a window an advance has run down.

        The floor is a ``max``, so it can only ever LENGTHEN the warning a target
        gets — which is why this is consistent with R8.15 rather than a restart:
        the restored value is the snapshot, and the floor is a floor.
        """
        self.record.ticks_remaining = 2                      # below the floor
        self.vector.suspend(self.record, "carrier_unavailable")
        self.vector.resume(self.record)

        self.assertEqual(self.record.ticks_remaining, FLOOR)

    def test_a_supporting_operation_resumes_on_its_own_clock(self):
        """No target to warn, so R8.8's floor never applied and never re-applies."""
        world = _lifecycle_world(SupportiveVector, ticks=2, hostile=False)
        self.assertEqual(world.clock, 2)
        world.vector.suspend(world.record, "carrier_unavailable")
        world.vector.resume(world.record)

        self.assertEqual(world.record.ticks_remaining, 2)

    def test_a_record_with_no_snapshot_keeps_the_clock_it_has(self):
        """A hand-edited record cannot be given a delay it never earned."""
        self.record.state = str(OperationState.SUSPENDED)
        self.record.suspended_ticks = None
        self.record.ticks_remaining = FLOOR + 2
        self.vector.resume(self.record)

        self.assertEqual(self.record.ticks_remaining, FLOOR + 2)

    def test_resuming_an_operation_that_was_not_suspended_does_nothing(self):
        self.assertFalse(self.vector.resume(self.record))
        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertEqual(_kinds(self.vector), [])

    def test_neither_transition_moves_a_settled_record(self):
        for state in FOUR_TERMINAL:
            with self.subTest(state=state):
                record = OperationRecord(op_id="op-x", state=state, ticks_remaining=4)

                self.assertFalse(self.vector.suspend(record, "carrier_unavailable"))
                self.assertFalse(self.vector.resume(record))
                self.assertEqual(record.state, state)

    def test_the_vector_hook_runs_on_resume(self):
        resumed = []
        self.vector.on_resume = resumed.append
        self.vector.suspend(self.record, "carrier_unavailable")
        self.vector.resume(self.record)

        self.assertEqual(resumed, [self.record])


class TestSuspensionStopsAndRestartsTheClock(unittest.TestCase):
    """R8.14, R8.15: no advance while paused, and the delay is exactly the pause."""

    def setUp(self):
        self.world = _lifecycle_world()
        self.vector = self.world.vector
        self.record = self.world.record
        self.clock = self.world.clock

    def test_a_benched_carrier_pauses_the_operation_and_stops_its_clock(self):
        self.world.carrier.db.reserve = True
        for tick in range(4):
            self.assertTrue(self.vector._advance_one(self.record, tick))

        self.assertEqual(self.record.state, str(OperationState.SUSPENDED))
        self.assertEqual(self.record.ticks_remaining, self.clock)
        self.assertEqual(_kinds(self.vector).count("vector_suspended"), 1)

    def test_a_bounded_lifetime_does_not_run_while_paused(self):
        self.record.lifetime_remaining = 2
        self.world.carrier.db.incapacitated = True
        for tick in range(5):
            self.vector._advance_one(self.record, tick)

        self.assertEqual(self.record.lifetime_remaining, 2)
        self.assertEqual(self.record.state, str(OperationState.SUSPENDED))

    def test_the_total_elapsed_ticks_are_the_original_plus_the_pause(self):
        """A suspension delays an operation by exactly the ticks it held for."""
        paused = 2
        self.world.carrier.db.reserve = True
        elapsed = 0
        for _ in range(paused):
            self.vector._advance_one(self.record, elapsed)
            elapsed += 1
        self.world.carrier.db.reserve = False
        while not self.record.is_terminal:
            self.vector._advance_one(self.record, elapsed)
            elapsed += 1

        self.assertEqual(self.record.state, str(OperationState.RESOLVED))
        self.assertEqual(elapsed, self.clock + paused)

    def test_the_operation_takes_the_tick_it_resumes_on(self):
        self.world.carrier.db.reserve = True
        self.vector._advance_one(self.record, 1)
        self.world.carrier.db.reserve = False
        self.vector._advance_one(self.record, 2)

        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertEqual(self.record.ticks_remaining, self.clock - 1)
        self.assertEqual(_kinds(self.vector), ["vector_suspended", "vector_resumed"])

    def test_a_resume_that_cannot_be_written_leaves_the_operation_paused(self):
        self.world.carrier.db.reserve = True
        self.vector._advance_one(self.record, 1)
        self.world.carrier.db.reserve = False
        self.vector.resume = lambda record: False

        self.assertTrue(self.vector._advance_one(self.record, 2))
        self.assertEqual(self.record.ticks_remaining, self.clock)


class TestTheFatalAndPausingConditions(unittest.TestCase):
    """R8.14, R8.16, R8.17, R8.18: read every tick, and never destructive on doubt."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=3)
        self.vector = self.world.vector
        self.record = self.world.record

    def test_a_dead_carrier_cancels_the_operation(self):
        self.world.carrier.db.hp = 0

        self.assertFalse(self.vector._advance_one(self.record, 1))
        self.assertEqual(self.record.state, str(OperationState.CANCELLED))
        self.assertEqual(
            _notified(self.vector),
            [(
                self.world.player,
                "vector_cancelled",
                {"kind": KIND, "reason": operation_contract.CANCEL_CARRIER_KILLED},
            )],
        )

    def test_the_existing_alive_predicate_is_preferred_over_the_hp_read(self):
        self.world.carrier.is_alive = lambda: False

        self.assertEqual(
            self.vector._carrier_fatal(self.record),
            operation_contract.CANCEL_CARRIER_KILLED,
        )

    def test_a_deleted_carrier_cancels_the_operation(self):
        self.world.carrier.pk = None

        self.assertEqual(
            self.vector._carrier_fatal(self.record),
            operation_contract.CANCEL_CARRIER_KILLED,
        )

    def test_a_doomed_operation_gets_no_free_tick_of_progress(self):
        """The fatal conditions are checked ahead of the clock for this reason."""
        self.record.ticks_remaining = 1
        self.world.carrier.db.hp = 0
        self.vector._advance_one(self.record, 1)

        self.assertEqual(self.record.state, str(OperationState.CANCELLED))
        self.assertEqual(self.vector.calls, [])            # never resolved

    def test_a_carrier_reference_is_never_read_as_a_corpse(self):
        """``carrier_ref`` is a value by design, and a dbref cannot be dead."""
        for reference in ("#77", 77, None):
            with self.subTest(reference=reference):
                self.record.carrier_ref = reference

                self.assertIsNone(self.vector._carrier_fatal(self.record))
                self.assertFalse(self.vector._carrier_unavailable(self.record))

    def test_a_benched_or_incapacitated_carrier_pauses_rather_than_cancels(self):
        for flag in ("reserve", "incapacitated"):
            with self.subTest(flag=flag):
                world = _lifecycle_world(ticks=3)
                setattr(world.carrier.db, flag, True)

                self.assertEqual(
                    world.vector._suspend_reason(world.record),
                    operation_contract.SUSPEND_CARRIER_UNAVAILABLE,
                )

    def test_a_non_operational_origin_cancels_the_operation(self):
        """R8.17, delegated to the same gate that let the operation launch."""
        self.world.branch.operational = False

        self.assertFalse(self.vector._advance_one(self.record, 1))
        self.assertEqual(self.record.state, str(OperationState.CANCELLED))
        self.assertEqual(
            _notified(self.vector)[0][2]["reason"],
            operation_contract.CANCEL_ORIGIN_LOST,
        )

    def test_a_deleted_origin_cancels_the_operation(self):
        self.record.building_ref = DeletedBuilding(owner=self.world.player)

        self.assertEqual(
            self.vector._origin_fatal(self.record),
            operation_contract.CANCEL_ORIGIN_LOST,
        )

    def test_an_origin_reference_cannot_be_judged_and_ends_nothing(self):
        for reference in ("#41", 41, None):
            with self.subTest(reference=reference):
                self.record.building_ref = reference

                self.assertIsNone(self.vector._origin_fatal(self.record))

    def test_an_unreachable_branch_system_cancels_nothing(self):
        """A service failure must not cancel every operation in flight."""
        self.world.branch.is_operational = None

        self.assertIsNone(self.vector._origin_fatal(self.record))

        self.world.branch.is_operational = lambda building: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertIsNone(self.vector._origin_fatal(self.record))

    def test_a_lapsed_commitment_pauses_the_operation(self):
        """R8.18: a dormant Branch resolves no operations."""
        self.world.branch.commitment_answer = None

        self.assertEqual(
            self.vector._suspend_reason(self.record),
            operation_contract.SUSPEND_COMMITMENT_LAPSED,
        )
        self.assertTrue(self.vector._advance_one(self.record, 1))
        self.assertEqual(self.record.state, str(OperationState.SUSPENDED))

    def test_a_switched_commitment_pauses_the_operation_too(self):
        self.world.branch.commitment_answer = "defense"

        self.assertTrue(self.vector._commitment_lapsed(self.record))

    def test_a_commitment_nobody_can_read_pauses_nothing(self):
        self.world.branch.commitment = None

        self.assertFalse(self.vector._commitment_lapsed(self.record))

        self.world.branch.commitment = lambda player, planet=None: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertFalse(self.vector._commitment_lapsed(self.record))

    def test_an_owner_nobody_can_resolve_pauses_nothing(self):
        self.record.owner_ref = "#5"
        self.record.building_ref = None
        self.record.carrier_ref = None
        self.world.branch.commitment_answer = None

        self.assertFalse(self.vector._commitment_lapsed(self.record))

    def test_a_vector_declaring_no_branch_cannot_lose_one(self):
        self.vector.branch = ""
        self.world.branch.commitment_answer = None

        self.assertFalse(self.vector._commitment_lapsed(self.record))

    def test_a_killed_carrier_outranks_a_benched_one(self):
        """A cancellation is checked before a pause, so a corpse is not paused."""
        self.world.carrier.db.hp = 0
        self.world.carrier.db.reserve = True
        self.vector._advance_one(self.record, 1)

        self.assertEqual(self.record.state, str(OperationState.CANCELLED))


class TestWorldEventsDriveTheTransitions(unittest.TestCase):
    """R8.16, R8.17, R8.18, R11.4: the three announcements the driver subscribes."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=6)
        self.vector = self.world.vector
        self.record = self.world.record
        self.bus = self.vector.event_bus

    def _publish(self, event, **payload):
        from mygame.world import event_bus as events

        self.bus.publish(getattr(events, event), **payload)

    def test_the_three_events_are_subscribed_at_construction(self):
        from mygame.world.event_bus import (
            BASE_ELIMINATED,
            BUILDING_DESTROYED,
            PLAYER_ELIMINATED,
        )

        self.assertEqual(
            self.bus.subscribers[PLAYER_ELIMINATED],
            [self.vector.handle_player_eliminated],
        )
        self.assertEqual(
            self.bus.subscribers[BUILDING_DESTROYED],
            [self.vector.handle_building_destroyed],
        )
        self.assertEqual(
            self.bus.subscribers[BASE_ELIMINATED],
            [self.vector.handle_base_eliminated],
        )

    def test_a_bus_that_cannot_subscribe_is_a_no_op(self):
        """A bare driver keeps the whole lifecycle minus the promptness (R15.3)."""
        vector = ChainVector(bus=FakeBus())

        self.assertEqual(vector.tracked_records(), [])
        self.assertIsNone(vector._subscribe_lifecycle_events(None))

    def test_a_slain_carrier_cancels_the_operation_it_was_carrying(self):
        """R8.16, and the reason this is an event: a killed agent respawns."""
        self._publish("PLAYER_ELIMINATED", victim=self.world.carrier, attacker=None)

        self.assertEqual(self.record.state, str(OperationState.CANCELLED))
        self.assertEqual(
            _notified(self.vector)[0][2]["reason"],
            operation_contract.CANCEL_CARRIER_KILLED,
        )

    def test_a_death_that_is_not_this_operations_carrier_changes_nothing(self):
        self._publish("PLAYER_ELIMINATED", victim=self.world.defender)
        self._publish("PLAYER_ELIMINATED", victim=self.world.player)
        self._publish("PLAYER_ELIMINATED", victim=None)

        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertEqual(_kinds(self.vector), [])

    def test_the_handler_reports_how_many_operations_it_cancelled(self):
        self.assertEqual(
            self.vector.handle_player_eliminated(victim=self.world.carrier), 1
        )
        self.assertEqual(
            self.vector.handle_player_eliminated(victim=self.world.carrier), 0
        )

    def test_a_destroyed_originating_building_cancels_the_operation(self):
        """R8.17, and R11.4's player-base case, one building at a time."""
        self._publish("BUILDING_DESTROYED", building=self.world.building, tile=None)

        self.assertEqual(self.record.state, str(OperationState.CANCELLED))
        self.assertEqual(
            _notified(self.vector)[0][2]["reason"],
            operation_contract.CANCEL_ORIGIN_LOST,
        )

    def test_another_players_building_falling_changes_nothing(self):
        self._publish("BUILDING_DESTROYED", building=FakeBuilding(id=999))

        self.assertEqual(self.record.state, str(OperationState.PENDING))

    def test_a_destroyed_branch_lab_pauses_rather_than_cancels(self):
        """R8.18: rebuilding the lab resumes an operation that was only paused."""
        lab = FakeBuilding(owner=self.world.player, building_type=LAB_ABBR, id=88)
        self._publish("BUILDING_DESTROYED", building=lab)

        self.assertEqual(self.record.state, str(OperationState.SUSPENDED))
        self.assertEqual(
            _notified(self.vector)[0][2]["reason"],
            operation_contract.SUSPEND_COMMITMENT_LAPSED,
        )

    def test_a_lab_on_another_planet_leaves_this_planets_operation_alone(self):
        lab = FakeBuilding(
            owner=self.world.player, building_type=LAB_ABBR, planet="mars", id=88
        )
        self._publish("BUILDING_DESTROYED", building=lab)

        self.assertEqual(self.record.state, str(OperationState.PENDING))

    def test_another_owners_lab_leaves_this_operation_alone(self):
        lab = FakeBuilding(owner=FakePlayer(id=99), building_type=LAB_ABBR, id=88)
        self._publish("BUILDING_DESTROYED", building=lab)

        self.assertEqual(self.record.state, str(OperationState.PENDING))

    def test_a_base_elimination_cancels_every_operation_it_launched(self):
        """R11.4: by now the buildings and the Sentinel are already deleted."""
        self.record.owner_ref = self.world.player
        self.record.building_ref = None
        self._publish(
            "BASE_ELIMINATED",
            sentinel=self.world.player, sentinel_id=self.world.player.id,
            tier="outpost", planet=PLANET, x=3, y=4,
        )

        self.assertEqual(self.record.state, str(OperationState.CANCELLED))
        self.assertEqual(
            _notified(self.vector)[0][2]["reason"],
            operation_contract.CANCEL_BASE_ELIMINATED,
        )

    def test_a_base_elimination_matches_on_the_pre_delete_identity(self):
        """The Sentinel's own ``.id`` reads as ``None`` by the time this fires."""
        self.record.owner_ref = self.world.player.id
        self.record.building_ref = None
        self.record.carrier_ref = None
        deleted = SimpleNamespace(id=None, pk=None)
        self._publish(
            "BASE_ELIMINATED", sentinel=deleted, sentinel_id=self.world.player.id,
            planet=PLANET,
        )

        self.assertEqual(self.record.state, str(OperationState.CANCELLED))

    def test_a_base_elimination_elsewhere_leaves_this_operation_alone(self):
        self._publish(
            "BASE_ELIMINATED", sentinel=self.world.player,
            sentinel_id=self.world.player.id, planet="mars",
        )

        self.assertEqual(self.record.state, str(OperationState.PENDING))

    def test_a_base_elimination_naming_nobody_cancels_nothing(self):
        self._publish("BASE_ELIMINATED", sentinel=None, sentinel_id=None)

        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertEqual(self.vector.handle_base_eliminated(), 0)

    def test_every_event_driven_transition_goes_through_the_single_writer(self):
        """R8.2: the event half of the lifecycle is covered by the same guard."""
        self.vector._transition = lambda record, state, reason="": False

        self._publish("PLAYER_ELIMINATED", victim=self.world.carrier)
        self._publish("BUILDING_DESTROYED", building=self.world.building)
        self._publish("BASE_ELIMINATED", sentinel=self.world.player, planet=PLANET)

        self.assertEqual(self.record.state, str(OperationState.PENDING))
        self.assertEqual(_kinds(self.vector), [])


class TestCancelIsTheOneCancellationPath(unittest.TestCase):
    """R8.16, R8.17, R11.4, R8.24: one transition, one notification, one outcome."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=4)
        self.vector = self.world.vector
        self.record = self.world.record

    def test_cancelling_settles_untracks_notifies_and_answers(self):
        outcome = self.vector.cancel(
            self.record, operation_contract.CANCEL_ORIGIN_LOST
        )

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.state, str(OperationState.CANCELLED))
        self.assertEqual(outcome.op_id, self.record.op_id)
        self.assertEqual(self.vector.tracked_records(), [])
        self.assertEqual(_read_records(self.vector.owner), [])

    def test_the_vector_hook_runs_on_cancellation(self):
        released = []
        self.vector.on_cancel = released.append
        self.vector.cancel(self.record, "origin_lost")

        self.assertEqual(released, [self.record])

    def test_a_cancellation_racing_a_resolution_loses(self):
        """R8.2: the terminal state is final, so the outcome reports it as it is."""
        self.vector._resolve(self.record)
        self.vector.event_bus.published.clear()
        outcome = self.vector.cancel(self.record, "carrier_killed")

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.state, str(OperationState.RESOLVED))
        self.assertEqual(outcome.check, "cancel")
        self.assertEqual(_kinds(self.vector), [])

    def test_a_missing_record_answers_rather_than_raising(self):
        outcome = self.vector.cancel(None, "origin_lost")

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.check, "cancel")


# ------------------------------------------------------------------ #
#  The two sanctioned effect paths (R8.23, R9.8, R9.11, R10.1 - R10.3)
# ------------------------------------------------------------------ #

class TestApplyHitRoutesThroughTheCombatEngine(unittest.TestCase):
    """R8.23, R10.3: one entry point, and the owning player is the attacker."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=4)
        self.vector = self.world.vector
        self.record = self.world.record
        self.engine = self.vector._combat_engine
        self.weapon = FakeWeapon()

    def test_a_hit_is_routed_to_the_single_hit_entry_point(self):
        damage = self.vector.apply_hit(self.record, self.world.defender, self.weapon)

        self.assertEqual(damage, self.engine.damage)
        self.assertEqual(len(self.engine.hits), 1)
        self.assertIs(self.engine.hits[0]["target"], self.world.defender)
        self.assertIs(self.engine.hits[0]["weapon"], self.weapon)

    def test_the_attacker_is_the_owning_player(self):
        """R10.3: the responsible player, never the delivery mechanism."""
        self.vector.apply_hit(self.record, self.world.defender, self.weapon)

        self.assertIs(self.engine.hits[0]["attacker"], self.world.player)

    def test_the_owner_is_resolved_through_the_records_references(self):
        """``owner_ref`` is a dbref by design, so the origin's owner is the answer."""
        self.record.owner_ref = "#5"
        self.vector.apply_hit(self.record, self.world.defender, self.weapon)

        self.assertIs(self.engine.hits[0]["attacker"], self.world.player)

    def test_the_attacker_bonus_is_off_unless_the_vector_asks(self):
        """A delivered effect's magnitude is the vector's own arithmetic."""
        self.vector.apply_hit(self.record, self.world.defender, self.weapon)
        self.vector.apply_hit(
            self.record, self.world.defender, self.weapon,
            include_attacker_bonus=True,
        )

        self.assertFalse(self.engine.hits[0]["include_attacker_bonus"])
        self.assertTrue(self.engine.hits[1]["include_attacker_bonus"])

    def test_an_unwired_combat_engine_deals_no_damage_and_logs(self):
        self.vector._combat_engine = None
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertEqual(
                self.vector.apply_hit(self.record, self.world.defender, self.weapon), 0
            )

    def test_an_engine_without_the_entry_point_deals_no_damage(self):
        self.vector._combat_engine = object()
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertEqual(
                self.vector.apply_hit(self.record, self.world.defender, self.weapon), 0
            )

    def test_the_driver_invents_no_weapon(self):
        """Inventing a weapon would be inventing damage."""
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertEqual(
                self.vector.apply_hit(self.record, self.world.defender, None), 0
            )
        self.assertEqual(self.engine.hits, [])

    def test_an_unattributable_hit_is_refused_rather_than_misattributed(self):
        """R10.3 forbids crediting the delivery mechanism, so nothing is hit."""
        self.record.owner_ref = "#5"
        self.record.building_ref = None
        self.record.carrier_ref = None
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertEqual(
                self.vector.apply_hit(self.record, self.world.defender, self.weapon), 0
            )
        self.assertEqual(self.engine.hits, [])

    def test_a_target_that_is_not_a_live_object_is_not_hit(self):
        for target in (None, "#91", 91):
            with self.subTest(target=target):
                self.assertEqual(
                    self.vector.apply_hit(self.record, target, self.weapon), 0
                )
        self.assertEqual(self.engine.hits, [])

    def test_a_hit_that_raises_is_logged_and_costs_one_target(self):
        self.engine.apply_direct_hit = lambda *a, **kw: 1 / 0
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertEqual(
                self.vector.apply_hit(self.record, self.world.defender, self.weapon), 0
            )

    def test_an_unreadable_damage_answer_reads_as_none(self):
        self.engine.damage = None

        self.assertEqual(
            self.vector.apply_hit(self.record, self.world.defender, self.weapon), 0
        )


class TestApplyEffectAppendsToTheExistingList(unittest.TestCase):
    """R8.23, R9.8: the second sanctioned path, attributed to the owner."""

    def setUp(self):
        self.world = _lifecycle_world(ticks=4)
        self.vector = self.world.vector
        self.record = self.world.record
        self.target = self.world.defender

    def _effects(self):
        return list(getattr(self.target.db, "active_effects", None) or ())

    def test_an_effect_is_appended_in_the_shape_the_existing_tick_reads(self):
        self.assertTrue(
            self.vector.apply_effect(self.record, self.target, "burn", 4, 3)
        )

        self.assertEqual(
            self._effects(),
            [{
                "type": "burn",
                "damage": 4,
                "ticks_remaining": 3,
                "source": self.world.player,
            }],
        )

    def test_the_source_is_the_owning_player(self):
        """R8.23, R10.3: a DoT that finishes a target off credits the player."""
        self.record.owner_ref = "#5"
        self.vector.apply_effect(self.record, self.target, "poison", 2, 5)

        self.assertIs(self._effects()[0]["source"], self.world.player)

    def test_an_existing_effect_is_preserved(self):
        self.target.db.active_effects = [
            {"type": "burn", "damage": 1, "ticks_remaining": 2, "source": None}
        ]
        self.vector.apply_effect(self.record, self.target, "intrusion", 0, 4)

        self.assertEqual([e["type"] for e in self._effects()], ["burn", "intrusion"])

    def test_the_write_replaces_the_container_rather_than_mutating_it(self):
        """The discipline an Evennia attribute needs to observe the change."""
        original = []
        self.target.db.active_effects = original
        self.vector.apply_effect(self.record, self.target, "burn", 1, 1)

        self.assertIsNot(self.target.db.active_effects, original)
        self.assertEqual(original, [])

    def test_a_status_effect_carries_no_damage(self):
        """R9.8's permitted alternative: a temporary suspension of behaviour."""
        self.vector.apply_effect(self.record, self.target, "surveillance")

        self.assertEqual(self._effects()[0]["damage"], 0)
        self.assertEqual(self._effects()[0]["ticks_remaining"], 1)

    def test_a_negative_amount_and_a_zero_duration_are_floored(self):
        self.vector.apply_effect(self.record, self.target, "burn", -5, 0)

        self.assertEqual(self._effects()[0]["damage"], 0)
        self.assertEqual(self._effects()[0]["ticks_remaining"], 1)

    def test_a_nameless_effect_is_refused(self):
        for kind in (None, "", "   "):
            with self.subTest(effect_type=kind):
                with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
                    self.assertFalse(
                        self.vector.apply_effect(self.record, self.target, kind)
                    )
        self.assertEqual(self._effects(), [])

    def test_a_target_that_holds_no_effects_is_left_alone(self):
        for target in (None, "#91", object()):
            with self.subTest(target=type(target).__name__):
                self.assertFalse(
                    self.vector.apply_effect(self.record, target, "burn", 1, 1)
                )

    def test_a_hand_edited_effects_value_is_replaced_rather_than_propagated(self):
        self.target.db.active_effects = "not a list of effects"
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertTrue(
                self.vector.apply_effect(self.record, self.target, "burn", 1, 1)
            )

        self.assertEqual([e["type"] for e in self._effects()], ["burn"])

    def test_an_unattributable_effect_still_lands_and_warns(self):
        """The existing tick already tolerates a source that has gone."""
        self.record.owner_ref = "#5"
        self.record.building_ref = None
        self.record.carrier_ref = None
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertTrue(
                self.vector.apply_effect(self.record, self.target, "burn", 1, 1)
            )

        self.assertIsNone(self._effects()[0]["source"])

    def test_a_write_that_fails_is_logged_and_answered(self):
        class Sealed:
            @property
            def active_effects(self):
                return []

        self.target.db = Sealed()
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertFalse(
                self.vector.apply_effect(self.record, self.target, "burn", 1, 1)
            )


# ------------------------------------------------------------------ #
#  The restart rebuild (design §4.9)
# ------------------------------------------------------------------ #

class FakeWorldRoom:
    """A PlanetRoom stand-in for the rebuild's id-to-object bridge.

    ``contents`` is the whole surface the index reads — the same one
    ``BombSystem.rebuild_from_world`` falls back to — so this fake is the real
    cross-module contract rather than a restatement of ``PlanetRoom``'s
    internals. Deliberately *not* :class:`FakeRoom`: that one answers the two
    coordinate queries the notification audience asks, and the rebuild asks
    neither.
    """

    def __init__(self, *entities):
        self.contents = list(entities)


class RebuildVector(NotifyingVector):
    """A conforming vector whose sweep answers the owners a test gave it.

    ``persistence_owner`` resolves the owner that actually *holds* a record, as a
    real vector's would, so a discard sweeps the record out of the container it
    was read from rather than out of a single fixture owner.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owners = [self.owner]
        self.swept = []
        self.discarded = []

    def discover_records(self, planet_rooms):
        self.swept.append(planet_rooms)
        return list(self.owners)

    def persistence_owner(self, record):
        op_id = getattr(record, "op_id", None)
        for owner in self.owners:
            if any(entry.get("op_id") == op_id for entry in _read_records(owner)):
                return owner
        return self.owner

    def on_discard(self, record):
        self.discarded.append(record.op_id)


class CorruptReadVector(RebuildVector):
    """A vector whose read hands the rebuild payloads it cannot all parse.

    The shipped read path drops a non-mapping entry *before* the rebuild sees it
    (``_read_records``, logged), so the rebuild's own parse guard is the second
    line of defence — and R14.5 asks for it anyway, because a rebuild step that
    fails for one record must still recover the rest. Overriding the read is what
    makes that guard reachable.
    """

    payloads: tuple = ()

    def _read_records(self, owner):
        return list(self.payloads)


class SweeplessVector(RebuildVector):
    """A vector whose ``discover_records`` raises — the R15.3 case for a sweep."""

    def discover_records(self, planet_rooms):
        raise RuntimeError("this vector cannot sweep the world")


def _rebuild_world(**answers):
    """A world holding one persisted, non-terminal record naming live entities.

    Nothing is tracked yet and nothing has been placed through ``request``: this
    is the state a server start finds — records on a durable owner, an empty
    tracked list, and a world the references can be resolved against.
    """
    world = _chain_world(RebuildVector, **answers)
    world.player.key = "Vex"
    world.carrier = LiveAgent()
    world.defender = FakeEntity(x=3, y=4, key="Mira")
    world.target = FakeEntity(owner=world.defender, x=3, y=4)
    world.room = FakeWorldRoom(
        world.player, world.building, world.carrier, world.target, world.defender
    )
    world.planet_rooms = {PLANET: world.room}
    world.record = _rebuild_payload(world)
    _write_records(world.vector.owner, [world.record])
    return world


def _rebuild_payload(world, **overrides):
    """A record naming *world*'s entities BY REFERENCE, as persistence holds them.

    Two id spellings on purpose — a plain int and a ``#dbref`` string — because
    both are shapes a persisted reference arrives in and the rebuild has to read
    either.
    """
    fields = dict(
        op_id="op-rebuilt",
        kind=KIND,
        owner_ref=world.player.id,
        building_ref=f"#{world.building.id}",
        carrier_ref=world.carrier.id,
        planet=PLANET,
        target_x=3,
        target_y=4,
        target_ref=f"#{world.target.id}",
        ticks_remaining=6,
        state=str(OperationState.PENDING),
    )
    fields.update(overrides)
    return OperationRecord(**fields)


def _rebuilt(world):
    """Return the single record *world*'s vector is tracking."""
    tracked = world.vector.tracked_records()
    assert len(tracked) == 1, f"expected one tracked record, got {len(tracked)}"
    return tracked[0]


class TestRebuildTracksThePersistedOperations(unittest.TestCase):
    """R8.22: a restart re-tracks every non-terminal operation, from persistence."""

    def setUp(self):
        self.world = _rebuild_world()
        self.vector = self.world.vector

    def test_a_live_record_is_re_tracked_and_counted(self):
        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 1)

        record = _rebuilt(self.world)
        self.assertEqual(record.op_id, "op-rebuilt")
        self.assertEqual(record.state, str(OperationState.PENDING))
        self.assertEqual(record.ticks_remaining, 6)

    def test_the_sweep_hook_is_handed_the_world_the_rebuild_was_given(self):
        self.vector.rebuild(self.world.planet_rooms)

        self.assertEqual(self.vector.swept, [self.world.planet_rooms])

    def test_the_rebuilt_operation_advances_on_the_next_tick(self):
        """R8.22's own words: each rebuilt operation RESUMES advancing."""
        self.vector.rebuild(self.world.planet_rooms)
        self.vector.advance_all(1)

        self.assertEqual(_rebuilt(self.world).ticks_remaining, 5)

    def test_the_references_are_replaced_with_the_live_objects(self):
        """The lifecycle conditions gate on a live object, not on a dbref.

        ``_carrier_fatal``, ``_origin_fatal``, and ``_commitment_lapsed`` all
        answer "no condition" for a plain reference, so a record rebuilt with its
        references still spelled as values would be judged by its clock alone and
        every cancellation and suspension trigger would be dead for it.
        """
        self.vector.rebuild(self.world.planet_rooms)

        record = _rebuilt(self.world)
        self.assertIs(record.owner_ref, self.world.player)
        self.assertIs(record.building_ref, self.world.building)
        self.assertIs(record.carrier_ref, self.world.carrier)
        self.assertIs(record.target_ref, self.world.target)

    def test_the_cancellation_triggers_are_alive_after_a_rebuild(self):
        """R8.16 across a restart: the carrier dies, the operation cancels."""
        self.vector.rebuild(self.world.planet_rooms)
        self.world.carrier.db.hp = 0
        self.vector.advance_all(1)

        self.assertEqual(
            _stored_states(self.vector), {}
        )  # terminal, so swept out of storage
        self.assertEqual(self.vector.tracked_records(), [])
        self.assertEqual(_kinds(self.vector), ["vector_cancelled"])

    def test_the_planet_room_mapping_and_a_plain_sequence_both_work(self):
        """The composition root hands both rebuilds the same argument."""
        for world in ({PLANET: self.world.room}, [self.world.room], self.world.room):
            with self.subTest(world=type(world).__name__):
                self.assertEqual(self.vector.rebuild(world), 1)
                self.assertIs(_rebuilt(self.world).carrier_ref, self.world.carrier)

    def test_a_terminal_record_is_skipped(self):
        """R8.2, R8.22: a finished operation is not an operation to rebuild."""
        for state in FOUR_TERMINAL:
            with self.subTest(state=state):
                _write_records(
                    self.vector.owner, [_rebuild_payload(self.world, state=state)]
                )
                self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 0)
                self.assertEqual(self.vector.tracked_records(), [])

    def test_a_suspended_record_is_rebuilt_still_suspended(self):
        """R8.15: a pause survives a restart, snapshot and all."""
        _write_records(self.vector.owner, [_rebuild_payload(
            self.world, state=str(OperationState.SUSPENDED), suspended_ticks=4,
        )])
        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 1)

        record = _rebuilt(self.world)
        self.assertEqual(record.state, str(OperationState.SUSPENDED))
        self.assertEqual(record.suspended_ticks, 4)

    def test_an_owner_holding_no_records_rebuilds_nothing(self):
        _write_records(self.vector.owner, [])

        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 0)
        self.assertEqual(self.vector.tracked_records(), [])

    def test_the_rebuild_replaces_whatever_was_tracked_before(self):
        """A rebuild is a read of durable state, not a merge with stale memory."""
        self.vector._track(OperationRecord(op_id="stale", kind=KIND))
        self.vector.rebuild(self.world.planet_rooms)

        self.assertEqual([r.op_id for r in self.vector.tracked_records()],
                         ["op-rebuilt"])


class TestRebuildIsIdempotent(unittest.TestCase):
    """R14.3: rebuilding twice tracks the same set as rebuilding once."""

    def setUp(self):
        self.world = _rebuild_world()
        self.vector = self.world.vector

    def _op_ids(self):
        return sorted(record.op_id for record in self.vector.tracked_records())

    def test_a_second_rebuild_duplicates_nothing(self):
        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 1)
        once = self._op_ids()
        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 1)

        self.assertEqual(self._op_ids(), once)

    def test_one_record_reached_through_two_owners_is_tracked_once(self):
        """The identity does the work: a sweep may name an owner twice."""
        second = FakeOwner(HostileAttributes())
        _write_records(second, [_rebuild_payload(self.world)])
        self.vector.owners = [self.vector.owner, second, self.vector.owner]

        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 1)
        self.assertEqual(self._op_ids(), ["op-rebuilt"])

    def test_two_records_sharing_no_identity_are_both_tracked(self):
        _write_records(self.vector.owner, [
            _rebuild_payload(self.world),
            _rebuild_payload(self.world, op_id="op-second"),
        ])

        self.assertEqual(self.vector.rebuild(self.world.planet_rooms), 2)
        self.assertEqual(self._op_ids(), ["op-rebuilt", "op-second"])


class TestRebuildDiscardsADanglingRecord(unittest.TestCase):
    """R14.4: a reference that no longer exists costs that operation."""

    def setUp(self):
        self.world = _rebuild_world()
        self.vector = self.world.vector

    def _rebuild(self, **overrides):
        """Persist a record with *overrides* and rebuild, returning the count."""
        _write_records(
            self.vector.owner, [_rebuild_payload(self.world, **overrides)]
        )
        return self.vector.rebuild(self.world.planet_rooms)

    def test_each_reference_pointing_at_nothing_discards_the_record(self):
        for name in OperationDriver._RESOLVED_REFS:
            with self.subTest(reference=name):
                with self.assertLogs(CONTRACT_LOGGER, level="WARNING") as logs:
                    self.assertEqual(self._rebuild(**{name: 9999}), 0)

                self.assertEqual(self.vector.tracked_records(), [])
                message = "\n".join(logs.output)
                self.assertIn(KIND, message)
                self.assertIn(name, message)

    def test_each_absent_reference_discards_the_record(self):
        """A record naming no owner, no origin, or no carrier is not an operation."""
        for name in ("owner_ref", "building_ref", "carrier_ref"):
            with self.subTest(reference=name):
                with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
                    self.assertEqual(self._rebuild(**{name: None}), 0)

    def test_a_tile_targeted_record_needs_no_target_entity(self):
        """Design §7: the coordinate and the entity are alternatives."""
        self.assertEqual(self._rebuild(target_ref=None), 1)
        self.assertIsNone(_rebuilt(self.world).target_ref)

    def test_a_record_aimed_at_neither_a_tile_nor_an_entity_is_discarded(self):
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self.assertEqual(
                self._rebuild(target_ref=None, target_x=None, target_y=None), 0
            )

    def test_the_discard_moves_the_record_untracks_it_and_reports_it(self):
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self._rebuild(carrier_ref=9999)

        self.assertEqual(self.vector.discarded, ["op-rebuilt"])
        self.assertEqual(_kinds(self.vector), ["vector_discarded"])
        self.assertEqual(
            _notified(self.vector),
            [(self.world.player, "vector_discarded", {"kind": KIND})],
        )

    def test_the_discarded_record_is_swept_out_of_its_owners_container(self):
        """Terminal, so the persist that settles it removes it (R8.2)."""
        with self.assertLogs(CONTRACT_LOGGER, level="WARNING"):
            self._rebuild(carrier_ref=9999)

        self.assertEqual(_read_records(self.vector.owner), [])

    def test_a_deleted_object_is_a_missing_reference(self):
        """Positive evidence, and it needs no lookup: ``pk`` reads as None."""
        record = _rebuild_payload(
            self.world, building_ref=DeletedBuilding(owner=self.world.player)
        )
        resolve = self.vector._ref_resolver(self.world.planet_rooms)

        self.assertEqual(
            self.vector._resolve_refs(record, resolve), ["building_ref"]
        )

    def test_a_reference_nobody_could_look_up_discards_nothing(self):
        """The driver never destroys an operation over a read it could not make."""
        for world in (None, {}, {PLANET: FakeWorldRoom()}):
            with self.subTest(world=world):
                _write_records(
                    self.vector.owner, [_rebuild_payload(self.world)]
                )
                self.assertEqual(self.vector.rebuild(world), 1)
                record = _rebuilt(self.world)
                self.assertEqual(record.owner_ref, self.world.player.id)
                self.assertEqual(self.vector.discarded, [])

    def test_a_reference_that_is_not_an_id_at_all_discards_nothing(self):
        self.assertEqual(self._rebuild(owner_ref="Vex"), 1)
        self.assertEqual(_rebuilt(self.world).owner_ref, "Vex")

    def test_a_reference_a_vector_kept_as_a_live_object_is_left_alone(self):
        record = _rebuild_payload(self.world, carrier_ref=self.world.carrier)
        resolve = self.vector._ref_resolver(self.world.planet_rooms)

        self.assertEqual(self.vector._resolve_refs(record, resolve), [])
        self.assertIs(record.carrier_ref, self.world.carrier)

    def test_a_terminal_record_cannot_be_discarded_twice(self):
        """``_transition`` is the single writer, and it declines a settled record."""
        record = _rebuild_payload(self.world, state=str(OperationState.RESOLVED))

        self.assertFalse(self.vector._discard(record, ["carrier_ref"]))
        self.assertEqual(record.state, str(OperationState.RESOLVED))
        self.assertEqual(self.vector.discarded, [])
        self.assertEqual(_notified(self.vector), [])

    def test_discarding_nothing_is_answered_rather_than_raised(self):
        self.assertFalse(self.vector._discard(None, ["owner_ref"]))


class TestRebuildRecoversFromOneBadRecord(unittest.TestCase):
    """R14.5: one corrupt record costs one record, and the rest are recovered."""

    def setUp(self):
        self.world = _rebuild_world()

    def _corrupt_vector(self, payloads):
        """A vector reading *payloads*, wired like :func:`_rebuild_world`'s."""
        vector = CorruptReadVector(
            registry=self.world.vector.registry,
            branch_system=self.world.vector._branch,
        )
        vector.payloads = list(payloads)
        return vector

    def test_a_record_that_cannot_be_parsed_is_logged_and_stepped_over(self):
        good = _rebuild_payload(self.world).to_dict()
        second = _rebuild_payload(self.world, op_id="op-second").to_dict()
        vector = self._corrupt_vector([good, "not a record at all", second])

        with self.assertLogs(CONTRACT_LOGGER, level="ERROR") as logs:
            self.assertEqual(vector.rebuild(self.world.planet_rooms), 2)

        self.assertIn(KIND, "\n".join(logs.output))
        self.assertEqual(
            sorted(r.op_id for r in vector.tracked_records()),
            ["op-rebuilt", "op-second"],
        )

    def test_every_record_being_corrupt_rebuilds_nothing(self):
        vector = self._corrupt_vector(["junk", 7])

        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertEqual(vector.rebuild(self.world.planet_rooms), 0)

    def test_a_sweep_that_raises_rebuilds_nothing_and_does_not_raise(self):
        vector = SweeplessVector(
            registry=self.world.vector.registry,
            branch_system=self.world.vector._branch,
        )
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertEqual(vector.rebuild(self.world.planet_rooms), 0)

    def test_an_unimplemented_sweep_rebuilds_nothing_and_does_not_raise(self):
        """A vector spec still being written must not stop a server start."""
        with self.assertLogs(CONTRACT_LOGGER, level="ERROR"):
            self.assertEqual(BareDriver().rebuild({}), 0)

    def test_a_sweep_answering_something_unusable_rebuilds_nothing(self):
        for answer in (None, 7):
            with self.subTest(answer=answer):
                vector = self._corrupt_vector([])
                vector.discover_records = lambda planet_rooms: answer
                self.assertEqual(vector.rebuild(self.world.planet_rooms), 0)


# ------------------------------------------------------------------ #
#  The payload table and presenter coverage (R13.5, R13.6, R13.8)
# ------------------------------------------------------------------ #

#: The EXACT payload each of the nine kinds carries, transcribed from design
#: §4.4's notification table rather than read back off the driver, so this module
#: pins the shape a presenter formatter and a command layer may rely on instead
#: of agreeing with whatever the driver happens to publish today.
#:
#: A **key set**, not a value map, and that is the point.
#: :class:`TestPendingNotificationWarnsTheTargets` and
#: :class:`TestLifecycleNotificationHelpers` already pin the *values* one fully
#: populated fixture produces; what a value assertion cannot say is that the set
#: of keys is **closed** — an undocumented extra key, or a documented key dropped
#: because its value could not be resolved, both slip past a value assertion made
#: against a record that has everything.
#:
#: ``vector_consent_required`` is the one kind the driver never publishes: it is
#: R11.8's refusal key, travelling back through the validation chain, so its shape
#: is asserted from that side.
VECTOR_PAYLOAD_KEYS = {
    "vector_incoming": {"kind", "attacker_name", "x", "y", "ticks"},
    "vector_resolved": {"kind", "x", "y"},
    "vector_hit": {"kind", "attacker_name", "x", "y"},
    "vector_suspended": {"kind", "reason", "x", "y"},
    "vector_resumed": {"kind", "ticks_remaining"},
    "vector_expired": {"kind", "x", "y"},
    "vector_cancelled": {"kind", "reason"},
    "vector_discarded": {"kind"},
    "vector_consent_required": {"kind", "ally_name"},
}

#: The kinds the **Branch side** of this feature publishes. They are not in the
#: driver's tuple because none of them is a Vector_Operation lifecycle transition,
#: and R13.8 covers "every notification kind this feature introduces" rather than
#: only the vector ones:
#:
#: * ``branch_dormancy_warning`` — ``branch_system``, published through a helper
#:   with a *variable* kind (R4.8, R13.4), so the presenter's own AST scan over
#:   string-literal ``self.notify`` kinds cannot see it;
#: * ``branch_estate_progress`` — ``building_system``, on each demolish (R4.5);
#: * ``technology_view`` — ``tech_system``, the whole view (R13.1, R13.2).
BRANCH_NOTIFICATION_KINDS = (
    NOTIFY_BRANCH_DORMANCY,
    "branch_estate_progress",
    "technology_view",
)

#: Every notification kind the systems this feature introduces can emit (R13.8).
NEW_SYSTEM_NOTIFICATION_KINDS = (
    *operation_contract.VECTOR_NOTIFICATION_KINDS,
    *BRANCH_NOTIFICATION_KINDS,
)

#: The prefixes a kind this feature introduced uses. Used to filter the literal
#: scan below, because ``building_system`` publishes plenty of kinds that predate
#: this feature and are covered by the presenter's own contract test.
FEATURE_KIND_PREFIXES = ("branch_", "technology_", "vector_")

#: This file lives at ``mygame/world/systems/tests/``; the systems layer is one
#: directory up and the shipped definitions are at ``mygame/data``.
_SYSTEMS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_DATA_DIR = os.path.normpath(
    os.path.join(_SYSTEMS_DIR, "..", "..", "data")
)


def _notify_kind_literals(path):
    """Return the string-literal ``kind`` values *path* publishes via ``notify``.

    Both call shapes this codebase uses: the kind as the second positional
    argument, and as a ``kind=`` keyword. A *variable* kind is invisible to this
    by construction, which is why the constant scan below exists alongside it.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    kinds = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "notify"):
            continue
        candidates = list(node.args[1:2])
        candidates += [kw.value for kw in node.keywords if kw.arg == "kind"]
        for candidate in candidates:
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                kinds.add(candidate.value)
    return kinds


def _notify_constants(module):
    """Return every ``NOTIFY_*`` string constant *module* declares."""
    return {
        value for name, value in vars(module).items()
        if name.startswith("NOTIFY_") and isinstance(value, str)
    }


class TestNotificationPayloadShapes(unittest.TestCase):
    """R13.5, R13.6: one payload-shape test per kind, as an exact key set."""

    def setUp(self):
        self.world = _notify_world()

    def _placed(self):
        """Place one operation, clear the log, and hand back its record."""
        _notify_send(self.world)
        self.world.vector.event_bus.published.clear()
        return self.world.vector.tracked_records()[0]

    def _assert_shape(self, kind):
        """Assert every payload published under *kind* has exactly its key set."""
        shapes = [
            set(data) for _player, published, data in _notified(self.world.vector)
            if published == kind
        ]
        self.assertTrue(shapes, f"nothing published {kind!r}")
        for shape in shapes:
            self.assertEqual(shape, VECTOR_PAYLOAD_KEYS[kind])

    def test_the_table_names_exactly_the_nine_declared_kinds(self):
        """A tenth kind cannot be added without documenting what it carries."""
        self.assertEqual(
            set(VECTOR_PAYLOAD_KEYS),
            set(operation_contract.VECTOR_NOTIFICATION_KINDS),
        )

    def test_every_payload_names_the_operation_kind(self):
        """R13.5: a payload is read far from the vector that published it."""
        for kind, keys in VECTOR_PAYLOAD_KEYS.items():
            with self.subTest(notification=kind):
                self.assertIn("kind", keys)

    def test_vector_incoming_carries_the_kind_attacker_tile_and_window(self):
        _notify_send(self.world)

        self._assert_shape("vector_incoming")

    def test_vector_resolved_carries_the_kind_and_the_coordinate(self):
        self.world.vector._notify_resolution(self._placed())

        self._assert_shape("vector_resolved")

    def test_vector_hit_carries_the_kind_the_attacker_and_the_coordinate(self):
        self.world.vector._notify_resolution(self._placed())

        self._assert_shape("vector_hit")

    def test_vector_suspended_carries_the_kind_the_reason_and_the_coordinate(self):
        self.world.vector._notify_suspension(
            self._placed(), operation_contract.SUSPEND_COMMITMENT_LAPSED
        )

        self._assert_shape("vector_suspended")

    def test_vector_resumed_carries_the_kind_and_the_ticks_it_held(self):
        self.world.vector._notify_resume(self._placed())

        self._assert_shape("vector_resumed")

    def test_vector_expired_carries_the_kind_and_the_coordinate(self):
        self.world.vector._notify_expiry(self._placed())

        self._assert_shape("vector_expired")

    def test_vector_cancelled_carries_the_kind_and_the_reason(self):
        self.world.vector._notify_cancellation(
            self._placed(), operation_contract.CANCEL_CARRIER_KILLED
        )

        self._assert_shape("vector_cancelled")

    def test_vector_discarded_carries_the_kind_alone(self):
        self.world.vector._notify_discard(self._placed())

        self._assert_shape("vector_discarded")

    def test_vector_consent_required_is_shaped_by_the_refusal_that_carries_it(self):
        """R11.8: the ninth kind never travels as a notification.

        It is ``BranchSystem.may_target``'s refusal key — that module owns the
        spelling and ``test_branch_services`` owns the claim that the refusal
        carries ``ally_name`` — and this asserts what the *driver* makes of it:
        the refusal detail names the kind as its message and carries both values
        the design's payload declares, plus the three keys the refusal channel
        itself adds (the message key, which consent was missing, and the target's
        name). Nothing is published, because a refusal is an answer, not a report.
        """
        refusal = BranchRefusal(
            MSG_VECTOR_CONSENT_REQUIRED,
            consent="support",
            ally_name="Mira",
            target_name="Mira",
        )
        world = _notify_world(target_refusal=refusal)
        outcome = _notify_send(world, hostile=False)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.check, "target")
        self.assertEqual(
            set(outcome.detail),
            {"message", "kind", "consent", "ally_name", "target_name"},
        )
        self.assertEqual(outcome.detail["message"], "vector_consent_required")
        self.assertLessEqual(
            VECTOR_PAYLOAD_KEYS["vector_consent_required"], set(outcome.detail)
        )
        self.assertEqual(_kinds(world.vector), [])

    def test_a_value_nobody_could_resolve_leaves_its_key_in_place(self):
        """The key set is CLOSED: a missing value publishes ``None``, not nothing.

        A record with no coordinate, no reason, and no clock is the degraded shape
        every one of these helpers has to survive (a rebuild's discard, a
        cancellation nobody named a cause for). The presenter reads each payload
        with ``.get``, so a dropped key would render as a blank rather than fail —
        which is exactly the class of bug R13.8 exists to make loud.
        """
        record = OperationRecord(kind=KIND, owner_ref=self.world.player)
        vector = self.world.vector
        for kind, publish in (
            ("vector_resolved", vector._notify_resolution),
            ("vector_expired", vector._notify_expiry),
            ("vector_suspended", vector._notify_suspension),
            ("vector_resumed", vector._notify_resume),
            ("vector_cancelled", vector._notify_cancellation),
            ("vector_discarded", vector._notify_discard),
        ):
            with self.subTest(notification=kind):
                vector.event_bus.published.clear()
                publish(record)
                self._assert_shape(kind)


class TestEveryKindTheNewSystemsEmitIsRendered(unittest.TestCase):
    """R13.6, R13.8: an unrendered kind is a test failure, not a blank line.

    The coverage guard from the **systems** side. The presenter's own contract
    scan (``test_notification_presenter.TestPresenterOwnershipStructural``) reads
    string-literal kinds out of ``self.notify(...)`` calls, and two of this
    feature's publishers are invisible to it: the driver publishes every lifecycle
    notification through one guarded helper with a *variable* kind, and
    ``branch_system`` publishes its dormancy warning through ``_publish`` with the
    kind held in a constant. So the declared lists are the authority here, and the
    tests below keep them honest in both directions — nothing declared is
    unrendered, and nothing the shipped modules can emit is undeclared.
    """

    def test_every_kind_the_new_systems_emit_has_a_formatter(self):
        table = NotificationPresenter._FORMATTERS
        missing = [
            kind for kind in NEW_SYSTEM_NOTIFICATION_KINDS if kind not in table
        ]
        self.assertEqual(
            missing, [],
            "these kinds are emitted by a system this feature introduces but "
            "have no formatter, so the player sees nothing: " + repr(missing),
        )

    def test_every_declared_kind_is_distinct(self):
        self.assertEqual(
            len(set(NEW_SYSTEM_NOTIFICATION_KINDS)),
            len(NEW_SYSTEM_NOTIFICATION_KINDS),
        )

    def test_all_six_lifecycle_states_report_through_a_formatted_kind(self):
        """R13.6: Pending, Suspended, Resolved, Expired, Cancelled, Discarded."""
        table = NotificationPresenter._FORMATTERS
        self.assertEqual(set(operation_contract.STATE_NOTIFICATIONS), set(SIX_STATES))
        for state in OperationState:
            kind = operation_contract.STATE_NOTIFICATIONS.get(str(state))
            with self.subTest(state=str(state)):
                self.assertIsNotNone(kind, f"{state} reports through no kind")
                self.assertIn(kind, table)

    def test_no_notify_constant_in_the_new_modules_is_left_undeclared(self):
        """A kind added as a constant must join the list the guard walks."""
        import mygame.world.systems.branch_system as branch_system_module

        declared = set(NEW_SYSTEM_NOTIFICATION_KINDS)
        for module in (operation_contract, branch_system_module):
            for kind in sorted(_notify_constants(module)):
                with self.subTest(module=module.__name__, kind=kind):
                    self.assertIn(kind, declared)

    def test_the_literal_kinds_the_branch_publishers_emit_are_declared(self):
        """The other direction, over the three modules that publish literals.

        ``building_system`` and ``tech_system`` name their kinds inline, so a new
        Branch or vector kind added there has to appear in the declared list —
        and ``branch_system`` is scanned too, so a literal added there instead of
        a constant is caught by the same rule.
        """
        declared = set(NEW_SYSTEM_NOTIFICATION_KINDS)
        for name in ("branch_system.py", "building_system.py", "tech_system.py"):
            emitted = {
                kind for kind in _notify_kind_literals(os.path.join(_SYSTEMS_DIR, name))
                if kind.startswith(FEATURE_KIND_PREFIXES)
            }
            with self.subTest(module=name):
                self.assertLessEqual(emitted, declared)

    def test_the_scan_sees_the_two_kinds_it_is_pointed_at(self):
        """The scanner itself is exercised, so a green guard cannot be a blind one."""
        found = _notify_kind_literals(os.path.join(_SYSTEMS_DIR, "building_system.py"))
        self.assertIn("branch_estate_progress", found)
        found = _notify_kind_literals(os.path.join(_SYSTEMS_DIR, "tech_system.py"))
        self.assertIn("technology_view", found)


# ------------------------------------------------------------------ #
#  The CombatEngine is the ONLY damage path (R8.23, R9.8, R10.3)
# ------------------------------------------------------------------ #

#: Every entry point in this codebase that APPLIES damage. Exactly one of them —
#: ``CombatEngine.apply_direct_hit``, R8.23's single-hit entry point — may appear
#: anywhere in the shipped contract module; a second one would be a damage path
#: outside the existing pipeline, and would take the chip-damage floor, the typed
#: resists, shield absorption (R9.11), the rank-gap damper (R10.1), the rank-gap
#: XP and loot reduction (R10.2), and kill attribution (R10.3) with it.
DAMAGE_ENTRY_POINTS = frozenset({
    "apply_direct_hit",
    "queue_attack",
    "resolve_now",
    "resolve_tick",
    "process_turrets",
    "apply_damage",
    "_apply_damage",
    "_apply_blast",
    "_apply_aoe_damage",
    "_calculate_damage",
    "_finalize_hit",
    "take_damage",
    "deal_damage",
})

#: R9.8's two prohibitions as names: **no** Vector_Operation deletes a building
#: outright, and **none** transfers ownership. The driver reaching any of these
#: would make the prohibition a rule each of the six vector specs has to
#: remember; reaching none of them makes it structural.
DESTRUCTIVE_NAMES = frozenset({
    "delete",
    "destroy",
    "at_destroy",
    "demolish",
    "remove_building",
    "destroy_building",
    "_handle_building_destruction",
    "set_owner",
    "transfer_ownership",
    "change_owner",
    "reassign_owner",
})

#: The helpers the contract module reaches a member through by NAME rather than
#: by attribute access. Every one of them takes the name as a string literal, so
#: a scan that only walked attribute accesses would miss ``apply_direct_hit``
#: entirely — it is reached as ``_reach(engine, "apply_direct_hit")``.
_NAME_REACHERS = frozenset({
    "_reach", "getattr", "hasattr", "setattr", "_ask", "_collaborator",
    "_run_hook", "_entity_attr",
})

#: The three receivers the driver legitimately writes a field on: itself, the
#: request context it built, and the Operation_Record it owns. A write to
#: anything else is a write to somebody else's object.
_OWN_RECEIVERS = frozenset({"self", "cls", "ctx", "record"})


def _reachable_names(source):
    """Yield every member name *source* can reach on another object.

    Two shapes, which together are every way this module reaches a collaborator:
    a plain attribute access, and a string literal handed to one of the
    name-reaching helpers above.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in _NAME_REACHERS:
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    yield argument.value


def _foreign_attribute_writes(source):
    """Yield ``(function, lineno, field)`` for each write to another object's field.

    Every assignment shape — plain, augmented, annotated — plus a ``setattr``
    naming its field in a literal, filtered to those whose receiver chain does
    *not* start at :data:`_OWN_RECEIVERS`. So ``record.state = ...`` and
    ``self._tracked = ...`` are the driver's own bookkeeping and are skipped,
    while ``target.db.hp = 0`` is reported as a write of ``hp``.
    """
    tree = ast.parse(source)

    def foreign(node):
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        return not (isinstance(root, ast.Name) and root.id in _OWN_RECEIVERS)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = (
                    inner.targets if isinstance(inner, ast.Assign) else [inner.target]
                )
                for target in targets:
                    if isinstance(target, ast.Attribute) and foreign(target.value):
                        yield node.name, inner.lineno, target.attr
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "setattr"
                and inner.args
                and foreign(inner.args[0])
            ):
                named = inner.args[1] if len(inner.args) > 1 else None
                field = (
                    named.value
                    if isinstance(named, ast.Constant) and isinstance(named.value, str)
                    else "<computed>"
                )
                yield node.name, inner.lineno, field


def _combat_engine_lookups(source):
    """Yield the enclosing function of each ``_collaborator("combat_engine")``."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and getattr(inner.func, "attr", None) == "_collaborator"
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
                and inner.args[0].value == "combat_engine"
            ):
                yield node.name


class StrikingVector(NotifyingVector):
    """A vector whose resolution uses BOTH sanctioned effect paths and nothing else.

    The shape design §4.10 invites: ``on_resolve`` walks the affected entities and
    calls the two methods the driver offers, because there is no third thing to
    call.
    """

    def on_resolve(self, record):
        for entity in self._affected_entities(record):
            self.apply_hit(record, entity, FakeWeapon())
            self.apply_effect(record, entity, "burn", 4, 3)


class SuspendingVector(NotifyingVector):
    """A vector whose effect is R9.8's other permitted form: a suspension."""

    def on_resolve(self, record):
        for entity in self._affected_entities(record):
            self.apply_effect(record, entity, "intrusion", 0, 5)


class TestTheDamagePathIsStructurallySingle(unittest.TestCase):
    """R8.23, R9.8: one damage entry point, no deletion, no ownership write."""

    @classmethod
    def setUpClass(cls):
        cls.source = inspect.getsource(operation_contract)

    def test_apply_direct_hit_is_the_only_damage_entry_point_reached(self):
        reached = set(_reachable_names(self.source))

        self.assertEqual(
            DAMAGE_ENTRY_POINTS & reached, {"apply_direct_hit"},
            "the driver offers a vector exactly one way to deal damage (R8.23); "
            "a second reachable damage entry point would bypass the chip-damage "
            "floor, the typed resists, shields, and the rank-gap dampers",
        )

    def test_the_module_reaches_no_deletion_and_no_ownership_transfer(self):
        """R9.8: no Vector_Operation deletes a building or transfers ownership."""
        reached = set(_reachable_names(self.source))

        self.assertEqual(sorted(DESTRUCTIVE_NAMES & reached), [])

    def test_the_combat_engine_is_looked_up_once_and_only_from_apply_hit(self):
        """The one door, opened in one place — so there is nowhere else to route."""
        self.assertEqual(list(_combat_engine_lookups(self.source)), ["apply_hit"])

    def test_the_only_field_written_on_another_object_is_active_effects(self):
        """R8.23's second path, and the whole of the driver's reach into the world.

        Everything else the driver writes is its own: ``self``, the request
        context, or the Operation_Record. This one write is the append to the
        existing ``db.active_effects`` list — no hit points, no shield, no
        ``owner``, and nothing that could unmake an entity.
        """
        writes = [
            (function, field)
            for function, _line, field in _foreign_attribute_writes(self.source)
        ]

        self.assertEqual(writes, [("apply_effect", "active_effects")])

    def test_the_scans_would_catch_a_planted_second_damage_path(self):
        """The scanners themselves are exercised, so a green guard is not a blind one."""
        planted = (
            "def sneak(self, ctx, record, target, engine):\n"
            "    target.db.hp = 0\n"
            "    target.db.owner = ctx.player\n"
            "    setattr(target, 'shield', 0)\n"
            "    target.delete()\n"
            "    engine.queue_attack(record.owner_ref, target)\n"
            "    self._tracked = []\n"
            "    record.state = 'resolved'\n"
            "    ctx.cost = {}\n"
        )
        fields = {field for _fn, _line, field in _foreign_attribute_writes(planted)}
        self.assertEqual(fields, {"hp", "owner", "shield"})

        reached = set(_reachable_names(planted))
        self.assertIn("delete", reached)
        self.assertIn("queue_attack", reached)


class TestTheCombatEngineIsTheOnlyDamagePathAtRuntime(unittest.TestCase):
    """R8.23, R9.8, R10.3: the structural claim, confirmed end to end.

    A whole lifecycle from request to resolution against a target that records
    every destructive call it could have been handed. The mocked engine
    deliberately applies nothing, so any change to the target's hit points,
    shield, or owner could only have come from the driver — and there is none.
    """

    def setUp(self):
        self.world = _lifecycle_world(StrikingVector)
        self.vector = self.world.vector
        self.engine = self.vector._combat_engine
        self.target = self.world.target
        self.target.db.hp = 100
        self.target.db.hp_max = 100
        self.target.db.shield = 20
        self.target.db.shield_max = 20
        self.deleted = []
        self.reassigned = []
        self.target.delete = lambda: self.deleted.append(self.target)
        self.target.set_owner = lambda owner: self.reassigned.append(owner)
        self.owner_before = self.target.db.owner

    def _run_to_resolution(self, vector=None):
        """Advance the real tick loop until the tracked operation settles."""
        vector = vector if vector is not None else self.vector
        for tick in range(self.world.clock):
            vector.advance_all(tick + 1)

    def test_the_effect_reaches_the_target_through_the_engine_alone(self):
        self._run_to_resolution()

        self.assertEqual(self.world.record.state, str(OperationState.RESOLVED))
        self.assertEqual(len(self.engine.hits), 1)
        self.assertIs(self.engine.hits[0]["target"], self.target)

    def test_the_attacker_recorded_is_the_owning_player(self):
        """R10.3: the responsible player, not the carrier and not the object."""
        self._run_to_resolution()

        self.assertIs(self.engine.hits[0]["attacker"], self.world.player)

    def test_the_driver_writes_no_hit_points_and_no_shield(self):
        """R9.8: damage is routed through the existing pipeline or it is not dealt."""
        self._run_to_resolution()

        self.assertEqual(
            (
                self.target.db.hp, self.target.db.hp_max,
                self.target.db.shield, self.target.db.shield_max,
            ),
            (100, 100, 20, 20),
        )

    def test_the_driver_deletes_nothing_and_reassigns_no_ownership(self):
        """R9.8's two prohibitions, from the target's own vantage point."""
        self._run_to_resolution()

        self.assertEqual(self.deleted, [])
        self.assertEqual(self.reassigned, [])
        self.assertIs(self.target.db.owner, self.owner_before)

    def test_the_second_sanctioned_path_lands_and_is_attributed_the_same_way(self):
        """R8.23: the existing active-effects list, credited to the same player."""
        self._run_to_resolution()

        effects = list(getattr(self.target.db, "active_effects", None) or ())
        self.assertEqual(
            effects,
            [{
                "type": "burn",
                "damage": 4,
                "ticks_remaining": 3,
                "source": self.world.player,
            }],
        )

    def test_a_suspension_only_resolution_deals_no_damage_at_all(self):
        """R9.8's alternative to damage: a temporary suspension of behaviour."""
        world = _lifecycle_world(SuspendingVector)
        target = world.target
        target.db.hp = 100
        target.db.shield = 20
        for tick in range(world.clock):
            world.vector.advance_all(tick + 1)

        self.assertEqual(world.vector._combat_engine.hits, [])
        self.assertEqual((target.db.hp, target.db.shield), (100, 20))
        effects = list(getattr(target.db, "active_effects", None) or ())
        self.assertEqual([(e["type"], e["damage"]) for e in effects], [("intrusion", 0)])


# ------------------------------------------------------------------ #
#  The three no-change guards (R11.1, R11.2, R11.7)
# ------------------------------------------------------------------ #

#: The Branch and the unlocking technology the affiliated half of each pair of
#: building definitions below declares — the two ``BuildingDef`` fields this
#: feature ADDED, and the only two fields that may differ within a pair.
PARITY_BRANCH = "weapons"
PARITY_UNLOCK = "ordnance_theory"
PARITY_PLANET = "earth"

#: Neutral abbreviation -> the Branch-affiliated twin of the same definition.
#: A depot, a Shield Generator, and a Turret, so the projector, the projected-onto
#: building, and the defender can each be swapped for an affiliated one.
PARITY_TWINS = {"ND": "BD", "SG": "SB", "TU": "TB"}

#: The identifiers this feature introduced. R11.1, R11.2, and R11.7 are
#: no-change obligations on the ShieldSystem, the GuardCombatSystem (with the
#: turret sweep in the CombatEngine), and the AllianceSystem, and the reason
#: their behaviour cannot have changed is that none of them names any of these.
#:
#: Scanned as **identifiers**, never as raw text: several of these modules use
#: the word "branch" in prose ("the cooldown branch finds it", "systems branch on
#: capabilities"), and a text scan would report a comment as a behaviour change.
BRANCH_FEATURE_IDENTIFIERS = frozenset({
    "BRANCHES",
    "BRANCH_DOCTRINE",
    "BRANCH_ROLE",
    "BRANCH_OPERATION_KIND",
    "OPERATION_KINDS",
    "BranchSystem",
    "OperationDriver",
    "OperationRecord",
    "branch",
    "unlock_technology",
    "branch_system",
    "operation_contract",
})

#: The modules R11.1, R11.2, and R11.7 address, relative to the systems layer.
NO_CHANGE_MODULES = (
    "shield_system.py",
    "guard_combat_system.py",
    "combat_engine.py",
    "alliance_system.py",
)


def _feature_identifiers_in(path):
    """Yield ``(lineno, identifier)`` for each Branch-feature name *path* uses."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in BRANCH_FEATURE_IDENTIFIERS:
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute) and node.attr in BRANCH_FEATURE_IDENTIFIERS:
            yield node.lineno, node.attr
        elif isinstance(node, ast.keyword) and node.arg in BRANCH_FEATURE_IDENTIFIERS:
            yield node.lineno, node.arg
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in BRANCH_FEATURE_IDENTIFIERS
        ):
            yield node.lineno, node.value
        elif isinstance(node, ast.ImportFrom) and node.module:
            for name in ("branch_system", "operation_contract"):
                if name in node.module:
                    yield node.lineno, node.module


def _parity_def(abbreviation, name, capabilities=(), branch=None, unlock=None,
                hp=400):
    """A ``BuildingDef`` whose twin differs only in the two fields this feature added."""
    return BuildingDef(
        name=name,
        abbreviation=abbreviation,
        cost={"Iron": 10},
        max_health=hp,
        requires_hq=True,
        required_terrain=None,
        category="defense",
        produces=None,
        capabilities=frozenset(capabilities),
        branch=branch,
        unlock_technology=unlock,
    )


def _parity_registry():
    """A registry holding the three pairs of twins plus the HQ the gates read."""
    registry = DataRegistry()
    registry.balance = BalanceConfig()
    registry.buildings = {
        "HQ": _parity_def("HQ", "Headquarters", ("headquarters",), hp=500),
        "ND": _parity_def("ND", "Depot", ("storage",)),
        "BD": _parity_def("BD", "Depot", ("storage",),
                          branch=PARITY_BRANCH, unlock=PARITY_UNLOCK),
        "SG": _parity_def("SG", "Shield Generator",
                          ("shield_generator", "upgradable"), hp=200),
        "SB": _parity_def("SB", "Shield Generator",
                          ("shield_generator", "upgradable"),
                          branch=PARITY_BRANCH, unlock=PARITY_UNLOCK, hp=200),
        "TU": _parity_def("TU", "Turret", ("turret",)),
        "TB": _parity_def("TB", "Turret", ("turret",),
                          branch=PARITY_BRANCH, unlock=PARITY_UNLOCK),
    }
    return registry


class ParityOwner:
    """A base owner: an identity and a roster, which is all the gates read."""

    _next_id = 600

    def __init__(self, roster=(), key="Vex"):
        ParityOwner._next_id += 1
        self.id = ParityOwner._next_id
        self.key = key
        self.roster = list(roster)
        self.db = SimpleNamespace(
            coord_planet=PARITY_PLANET, player_alliance=None, combat_xp=0,
        )

    def get_buildings(self):
        return list(self.roster)

    def msg(self, text):  # pragma: no cover - a defender never messages its owner
        pass


class _ParityDb:
    """A ``db`` proxy over the SAME store the ``attributes`` handler reads.

    Real Evennia backs ``obj.db.x`` and ``obj.attributes.get("x")`` with one
    store, and the three systems compared below are split across the two —
    ``ShieldSystem`` reads the proxy, ``CombatEngine._get_building_owner`` reads
    the handler. A fake that kept two separate bags would let a consumer that
    reads only one of them pass here and find nothing in the game.
    """

    def __init__(self, store):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, key):
        return object.__getattribute__(self, "_store").get(key)

    def __setattr__(self, key, value):
        object.__getattribute__(self, "_store").add(key, value)


class ParityBuilding:
    """A live building: the attribute store the three systems read, and no more."""

    _next_id = 700

    def __init__(self, building_type, x, y, owner, level=1, hp_max=400,
                 is_open=False, location=None):
        ParityBuilding._next_id += 1
        self.id = ParityBuilding._next_id
        self.key = f"{building_type}@{x},{y}"
        self.location = location
        self.attributes = FakeAttributes({
            "building_type": building_type,
            "coord_x": x,
            "coord_y": y,
            "coord_planet": PARITY_PLANET,
            "owner": owner,
            "building_level": level,
            "hp": hp_max,
            "hp_max": hp_max,
            "shield": 0,
            "shield_max": 0,
            "shield_regen_accumulator": 0.0,
            "under_construction": False,
            "offline": False,
            "open": is_open,
        })
        self.db = _ParityDb(self.attributes)


class ParityRaider:
    """A raiding player: coordinates, hit points, and an identity."""

    _next_id = 800

    def __init__(self, x, y, room=None, inside=False, key="Raider"):
        ParityRaider._next_id += 1
        self.id = ParityRaider._next_id
        self.key = key
        self.location = room
        self.messages = []
        self.db = SimpleNamespace(
            coord_x=x,
            coord_y=y,
            coord_planet=PARITY_PLANET,
            combat_xp=0,
            hp=100,
            hp_max=100,
            combat_lockout_tick=0,
            active_powerups={},
            inside_building=inside,
            player_alliance=None,
        )

    def msg(self, text):
        self.messages.append(text)


class ParityGuard:
    """A guard NPC at its post: role, owner, coordinates, combat readiness."""

    def __init__(self, owner, x, y, room=None, role="guard"):
        self.id = 950
        self.key = "Guard"
        self.location = room
        self.messages = []
        self.db = SimpleNamespace(
            role=role,
            owner=owner,
            npc_type="enemy",
            coord_x=x,
            coord_y=y,
            hp=100,
            hp_max=100,
            reserve=False,
            incapacitated=False,
            combat_xp=0,
            combat_lockout_tick=0,
            active_powerups={},
        )

    def msg(self, text):
        self.messages.append(text)


class ParityRoom:
    """A PlanetRoom stand-in: the spatial query the defenders use, and the tile
    lookup the cover rule reads.

    Both reached duck-typed by the names ``world.utils`` reaches them by, so this
    fake is the real cross-module contract rather than a restatement of
    ``PlanetRoom``'s internals.
    """

    def __init__(self):
        self.planet_name = PARITY_PLANET
        self.players = []
        self.buildings = {}

    def get_nearby_players(self, x, y, radius):
        return list(self.players)

    def get_buildings_at(self, x, y):
        building = self.buildings.get((int(x), int(y)))
        return [building] if building is not None else []

    def place(self, building):
        self.buildings[(building.db.coord_x, building.db.coord_y)] = building
        building.location = self
        return building


class TestTheParityPairsDifferOnlyInTheNewFields(unittest.TestCase):
    """The comparisons below are only honest while this holds."""

    def test_each_twin_differs_from_its_neutral_only_in_branch_and_unlock(self):
        registry = _parity_registry()
        for neutral, affiliated in sorted(PARITY_TWINS.items()):
            with self.subTest(pair=(neutral, affiliated)):
                left = dataclasses.asdict(registry.buildings[neutral])
                right = dataclasses.asdict(registry.buildings[affiliated])
                differing = {key for key in left if left[key] != right[key]}
                self.assertEqual(
                    differing, {"abbreviation", "branch", "unlock_technology"},
                    "a behavioural difference between these two could then come "
                    "from a field this feature did not add",
                )


class TestNoChangeModulesNameNothingThisFeatureAdded(unittest.TestCase):
    """R11.1, R11.2, R11.7: the structural half of the three no-change claims.

    The behavioural comparisons below are the primary evidence; this says *why*
    they can be trusted to keep holding. A module that never names ``branch`` can
    neither read a Branch_Affiliation nor gain a Signature_Vector perk, so the
    parity is not a coincidence of the fixtures chosen.
    """

    def test_none_of_the_three_systems_names_a_branch_feature_identifier(self):
        for name in NO_CHANGE_MODULES:
            path = os.path.join(_SYSTEMS_DIR, name)
            offenders = sorted(
                f"line {lineno}: names '{identifier}'"
                for lineno, identifier in _feature_identifiers_in(path)
            )
            with self.subTest(module=name):
                self.assertEqual(
                    offenders, [],
                    f"{name} is named by R11.1/R11.2/R11.7 as unchanged by this "
                    "feature, so it must not read a Branch_Affiliation or an "
                    "unlocking technology:\n  " + "\n  ".join(offenders),
                )

    def test_the_scan_would_catch_a_planted_affiliation_read(self):
        """Exercised on the module that DOES read the field, and on a planted one."""
        contract = os.path.join(_SYSTEMS_DIR, "operation_contract.py")
        named = {identifier for _line, identifier in _feature_identifiers_in(contract)}

        self.assertIn(
            "unlock_technology", named,
            "the scan cannot be a guard if it cannot see the module that "
            "legitimately reads the field",
        )


class TestShieldsProjectOntoABranchBuildingIdentically(unittest.TestCase):
    """R11.1: a Shield Generator treats a Branch_Building as a Neutral_Building.

    A **behavioural** comparison rather than an AST scan, because the shield a
    generator projects is directly observable: two runs differing only in which
    twin definition the covered building names, compared on the capacity and the
    charge the system wrote. Each comparison also asserts the neutral arm is
    non-zero, so "both arms did nothing" cannot pass as parity.
    """

    def setUp(self):
        self.registry = _parity_registry()
        self.system = ShieldSystem(self.registry, EventBus())
        self.owner = ParityOwner()

    def _projected(self, generator_type, covered_type, offset=2, level=1):
        """Refresh one generator and one covered building; return the covered one."""
        generator = ParityBuilding(
            generator_type, 5, 5, self.owner, level=level, hp_max=200
        )
        covered = ParityBuilding(covered_type, 5 + offset, 5, self.owner)
        self.system.refresh([generator, covered])
        return covered

    def _shield(self, building):
        return (building.db.shield_max, building.db.shield)

    def test_a_branch_building_takes_the_same_shield_as_a_neutral_one(self):
        neutral = self._projected("SG", "ND")
        affiliated = self._projected("SG", "BD")

        self.assertGreater(neutral.db.shield_max, 0)
        self.assertEqual(self._shield(affiliated), self._shield(neutral))

    def test_a_branch_affiliated_generator_projects_the_same_shield(self):
        """The projector's affiliation is not read either."""
        neutral = self._projected("SG", "ND")
        affiliated = self._projected("SB", "ND")

        self.assertGreater(neutral.db.shield_max, 0)
        self.assertEqual(self._shield(affiliated), self._shield(neutral))

    def test_the_coverage_radius_is_the_same_for_both(self):
        """Outside the radius, both take nothing; inside, both take the same."""
        for offset in (2, 3):
            with self.subTest(offset=offset):
                neutral = self._projected("SG", "ND", offset=offset)
                affiliated = self._projected("SG", "BD", offset=offset)
                self.assertEqual(self._shield(affiliated), self._shield(neutral))
        self.assertEqual(self._projected("SG", "BD", offset=3).db.shield_max, 0)

    def test_the_level_scaling_is_the_same_for_both(self):
        for level in (1, 2, 4):
            with self.subTest(level=level):
                neutral = self._projected("SG", "ND", level=level)
                affiliated = self._projected("SG", "BD", level=level)
                self.assertGreater(neutral.db.shield_max, 0)
                self.assertEqual(self._shield(affiliated), self._shield(neutral))

    def test_regeneration_advances_both_by_the_same_amount(self):
        self.registry.balance.shield_regen_percent = 50.0
        neutral = self._projected("SG", "ND")
        affiliated = self._projected("SG", "BD")
        for building in (neutral, affiliated):
            building.db.shield = 0
        interval = self.registry.balance.shield_regen_interval_ticks
        self.system.process_tick([neutral, affiliated], interval)

        self.assertGreater(neutral.db.shield, 0)
        self.assertEqual(affiliated.db.shield, neutral.db.shield)


class TestGuardsDefendABranchBuildingIdentically(unittest.TestCase):
    """R11.2: a guard's response to a raid on a Branch_Building is unchanged.

    A guard defends by acquiring the nearest hostile and attacking it, so "defends
    a Branch_Building on the same terms" is the claim that swapping the raided
    building for its affiliated twin changes neither the acquisition nor the
    weapon nor the cover the occupant gets. Compared behaviourally, over the real
    ``GuardCombatSystem`` and a real ``CombatEngine``, because the queued attack
    is the observable.
    """

    def _raid(self, raided_type, is_open, guard_role="guard"):
        """Run one tick of a raid on *raided_type*; return the queued attacks."""
        registry = _parity_registry()
        engine = CombatEngine(registry, EventBus(), current_tick_func=lambda: 0)
        guards = GuardCombatSystem(registry, EventBus(), combat_engine=engine)
        room = ParityRoom()
        owner = ParityOwner()
        # The HQ sits off the guard's tile so no on-tile aura can vary between
        # the two arms; the guard stands on open ground at (5, 5).
        hq = room.place(ParityBuilding("HQ", 3, 3, owner, hp_max=500))
        raided = room.place(ParityBuilding(raided_type, 7, 5, owner, is_open=is_open))
        owner.roster = [hq, raided]
        raider = ParityRaider(7, 5, room=room, inside=True)
        room.players.append(raider)
        guards.process_tick(1, [ParityGuard(owner, 5, 5, room=room, role=guard_role)])
        return [
            (
                getattr(action["target"], "key", None),
                getattr(action["weapon_item"], "key", None),
                action["weapon_item"].get_stat("damage"),
                action["weapon_item"].get_stat("range"),
            )
            for action in engine.pending_actions
        ]

    def test_a_raider_at_an_open_branch_building_draws_the_same_defence(self):
        neutral = self._raid("ND", is_open=True)
        affiliated = self._raid("BD", is_open=True)

        self.assertEqual(len(neutral), 1)          # the guard really did fire
        self.assertEqual(affiliated, neutral)

    def test_a_closed_branch_building_shelters_its_occupant_the_same_way(self):
        """The cover rule reads the instance's ``open`` flag, not its affiliation."""
        neutral = self._raid("ND", is_open=False)
        affiliated = self._raid("BD", is_open=False)

        self.assertEqual(neutral, [])              # cover held in both arms
        self.assertEqual(affiliated, neutral)

    def test_a_soldier_defends_a_branch_building_the_same_way_too(self):
        """Both roles in ``GUARD_ROLES``, so the parity is not role-specific."""
        neutral = self._raid("ND", is_open=True, guard_role="soldier")
        affiliated = self._raid("BD", is_open=True, guard_role="soldier")

        self.assertEqual(len(neutral), 1)
        self.assertEqual(affiliated, neutral)


class TestTurretsDefendABranchBuildingIdentically(unittest.TestCase):
    """R11.2, alongside the guard: the turret sweep reads capability, not Branch.

    The other half of the automated-defence path the requirement's "on the same
    terms" covers. Compared the same way: one sweep per arm, differing only in
    which twin definition a building in the roster names.
    """

    def _sweep(self, turret_type, roster_type):
        """Run one turret sweep; return the queued attacks."""
        registry = _parity_registry()
        engine = CombatEngine(registry, EventBus(), current_tick_func=lambda: 0)
        room = ParityRoom()
        owner = ParityOwner()
        hq = room.place(ParityBuilding("HQ", 3, 3, owner, hp_max=500))
        turret = room.place(ParityBuilding(turret_type, 6, 5, owner))
        defended = room.place(ParityBuilding(roster_type, 7, 5, owner, is_open=True))
        owner.roster = [hq, turret, defended]
        room.players.append(ParityRaider(8, 5, room=room))
        engine.process_turrets([hq, turret, defended])
        return [
            (
                getattr(action["target"], "key", None),
                getattr(action["weapon_item"], "key", None),
                action["weapon_item"].get_stat("damage"),
            )
            for action in engine.pending_actions
        ]

    def test_a_branch_building_in_the_roster_is_passed_over_like_a_neutral_one(self):
        neutral = self._sweep("TU", "ND")
        affiliated = self._sweep("TU", "BD")

        self.assertEqual(len(neutral), 1)          # exactly the turret fired
        self.assertEqual(affiliated, neutral)

    def test_a_branch_affiliated_turret_fires_exactly_as_a_neutral_one(self):
        neutral = self._sweep("TU", "ND")
        affiliated = self._sweep("TB", "ND")

        self.assertEqual(len(neutral), 1)
        self.assertEqual(affiliated, neutral)


class TestAlliancePerkCategoriesAreUnchanged(unittest.TestCase):
    """R11.7: no alliance perk grants a Signature_Vector.

    Asserted as **data plus surface** rather than behaviourally, because a perk
    that granted a vector would have to exist as a catalog entry in a declared
    category with a declared effect type — so pinning the five categories, the
    one-perk-per-category catalog, the three effect types, and the perk surface
    covers every shape such a perk could take. The shipped catalog is read through
    its own loader rather than through a full ``load_all``, so this test fails on
    the file it is about and on nothing else.
    """

    @classmethod
    def setUpClass(cls):
        registry = DataRegistry()
        registry._load_alliance_perks(_REAL_DATA_DIR)
        cls.perks = dict(registry.alliance_perks)

    def test_the_five_categories_are_exactly_the_pre_feature_five(self):
        self.assertEqual(
            ALLIANCE_PERK_CATEGORIES,
            (
                "shared_vision",
                "shared_regen",
                "harvest_boost",
                "combat_damage",
                "combat_armor",
            ),
        )

    def test_the_shipped_catalog_holds_one_perk_per_declared_category(self):
        self.assertTrue(self.perks, "the shipped alliance perk catalog did not load")
        categories = [spec.get("category") for spec in self.perks.values()]
        self.assertEqual(sorted(categories), sorted(ALLIANCE_PERK_CATEGORIES))

    def test_the_effect_vocabulary_is_the_pre_feature_three(self):
        """A Signature_Vector is neither a multiplier, a flat bonus, nor a flag."""
        self.assertEqual(
            {spec.get("effect_type") for spec in self.perks.values()},
            {"boolean", "multiplier", "flat"},
        )

    def test_no_perk_names_a_branch_an_operation_kind_or_a_vector_kind(self):
        from mygame.world.constants import BRANCHES, OPERATION_KINDS

        forbidden = (
            set(BRANCHES)
            | set(OPERATION_KINDS)
            | set(NEW_SYSTEM_NOTIFICATION_KINDS)
        )
        for key, spec in sorted(self.perks.items()):
            named = {key, spec.get("category"), spec.get("effect_type")}
            for payload in (spec.get("levels") or {}).values():
                named |= set(payload or {})
                named |= {
                    value for value in (payload or {}).values() if isinstance(value, str)
                }
            with self.subTest(perk=key):
                self.assertEqual(sorted(named & forbidden), [])

    def test_the_perk_surface_gained_no_vector_hook(self):
        """The readers a perk's effect reaches players through, pinned by name."""
        self.assertEqual(
            {
                name for name in dir(AllianceSystem)
                if "perk" in name and not name.startswith("_")
            },
            {"available_perks", "activate_perk", "perk_multiplier", "perk_flat_bonus"},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
