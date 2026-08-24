"""
Property-based tests for Operation_Record persistence.

Feature: tech-tree-branch-foundation (design section "Correctness Properties").

The three properties the design's test-module table assigns to this file:

- **Property 21**: An Operation_Record round-trips through persistence —
  Requirements 8.21, 8.22, 14.1, 14.2.
- **Property 22**: Rebuilding is idempotent, isolated, and discards dangling
  records — Requirements 14.3, 14.4, 14.5.
- **Property 23**: Reading a partial record yields documented defaults and never
  raises — Requirements 14.8.

One ``@given`` test per property, with each property's clauses asserted inside
that one test, as the design's Testing Strategy requires. Properties 21 and 23
measure the value types and the persistence pair; Property 22 measures
``OperationDriver.rebuild`` on top of them, and is written last in the module
because it reuses their reference tables and their fakes.

**The durable owner is not a player.** A vector nominates the world object its
operation acts through or the entity it is attached to (R14.1) — a building, a
placed object, an agent — so :class:`_DurableOwner` below is deliberately a bare
object carrying nothing but an ``attributes`` handler. That handler is the whole
surface the persistence pair requires, and nothing here assumes more.

**Measured under a hostile attribute handler.** Both properties store through
``branch_strategies.HostileFakeAttributes``, which copies on the way out *and* on
the way in, so an in-place change to a container a read handed back is discarded
exactly as a real Evennia attribute may discard it. Only genuine read-copy-write
persists anything under it (R14.7), which is what makes a passing round trip
evidence about persistence rather than about a dict that happened to be shared.
The equivalent fake in ``test_operation_contract.py`` (``HostileAttributes``) is
deliberately *not* imported: it is that module's local fixture, importing one
test module from another couples their collection order, and the shared strategy
module already publishes the same discipline for exactly this use.

Every generator is drawn from ``branch_strategies``, and that module also
installs the Evennia stubs at import — hence its import is deliberately FIRST
here, so this module loads with ``evennia`` absent from ``sys.modules`` (R15.1).
"""

import logging
import unittest
from dataclasses import fields, replace

from hypothesis import given, settings
from hypothesis import strategies as st

#: Imported FIRST on purpose: ``branch_strategies`` installs the Evennia stubs
#: at import time, so nothing below can pull in a typeclass without them.
from mygame.world.systems.tests.branch_strategies import (
    LIVE_STATE_VALUES,
    TERMINAL_STATE_VALUES,
    HostileFakeAttributes,
    record_st,
)
from mygame.world.constants import ATTR_VECTOR_OPERATIONS
from mygame.world.systems.operation_contract import (
    OperationDriver,
    OperationRecord,
    OperationState,
    _read_records,
    _write_records,
)

#: Every persisted field of an Operation_Record, in declaration order (design
#: §7). Written out rather than read off the dataclass because it is the
#: *reference* both properties measure against: a field added to the record
#: without a spec change, or one renamed, must fail here rather than quietly
#: dropping out of the round trip and out of the defaults table below. Property
#: 23's first clause asserts this tuple and the dataclass agree.
PERSISTED_FIELDS: tuple[str, ...] = (
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

#: The fields Property 21 names, in the order the property names them:
#: "Operation_Kind, owner, originating building, Carrier_Agent, target
#: coordinate, target entity, remaining ticks, effect magnitude, effect radius,
#: lifecycle state, and charged amount". Eleven clauses, twelve names — the
#: target coordinate is the ``(target_x, target_y)`` pair.
ROUND_TRIP_FIELDS: tuple[str, ...] = (
    "kind",
    "owner_ref",
    "building_ref",
    "carrier_ref",
    "target_x",
    "target_y",
    "target_ref",
    "ticks_remaining",
    "magnitude",
    "radius",
    "state",
    "charged",
)

#: The **documented default** of each persisted field (R14.8, design §7) — the
#: value ``from_dict`` falls back to for an absent or null one. Stated here as
#: the design states it, so Property 23 measures the code against the
#: specification rather than against the code's own field defaults; the property
#: cross-checks the two, which is what keeps this table honest.
#:
#: ``op_id`` is absent from the table on purpose: its documented default is a
#: *fresh* identity rather than a constant, so no equality can express it. The
#: property asserts that shape directly instead — a non-empty string, and a
#: different one on every read, so two id-less records can never collide into a
#: single tracked entry (R14.3).
DOCUMENTED_DEFAULTS: dict[str, object] = {
    "kind": "",
    "owner_ref": None,
    "building_ref": None,
    "carrier_ref": None,
    "planet": None,
    "target_x": None,
    "target_y": None,
    "target_ref": None,
    "ticks_remaining": 0,
    "lifetime_remaining": None,
    "magnitude": 0.0,
    "radius": 0,
    "state": "pending",
    "suspended_ticks": None,
    "charged": {},
}

#: The leaf types a persisted payload may hold. Compared by **exact type**, not
#: with ``isinstance``: an :class:`OperationState` member *is* a ``str``
#: subclass, and the whole point of ``to_dict`` collapsing it is that storage
#: never holds the enum. ``bool`` is likewise absent — no persisted field is a
#: flag, so one appearing would be a change worth failing on.
PLAIN_LEAF_TYPES: tuple[type, ...] = (str, int, float, type(None))


def _plain_data_violations(value, path="stored"):
    """Yield the path of everything inside *value* that is not plain data.

    What "a record survives a restart as a plain dict" means, made checkable: a
    payload is lists, dicts with string keys, and the leaves in
    :data:`PLAIN_LEAF_TYPES`. A dataclass, an enum member, or a live object
    anywhere inside it is reported by path so a failure names the field.
    """
    if type(value) in (list, tuple):
        for index, item in enumerate(value):
            yield from _plain_data_violations(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                yield f"{path} has a {type(key).__name__} key {key!r}"
            yield from _plain_data_violations(item, f"{path}[{key!r}]")
        return
    if type(value) not in PLAIN_LEAF_TYPES:
        yield f"{path} is a {type(value).__name__}"


class _DurableOwner:
    """The durable owner a vector nominates (R14.1), and nothing more.

    An ``attributes`` handler is the entire surface the persistence pair
    requires, so this is the entire fake: no ``db`` proxy, no typeclass, no
    framework, and pointedly not a player — the owner a vector names is the
    world object its operation acts through or the entity it is attached to.

    The handler is hostile (see the module docstring): it copies on the way out
    and on the way in, so nothing here can persist by mutating a list it read.
    """

    def __init__(self):
        self.attributes = HostileFakeAttributes()

    def stored(self):
        """The container as it sits in storage, read without going through the pair."""
        return self.attributes.get(ATTR_VECTOR_OPERATIONS, default=None)


class _Vector(OperationDriver):
    """A minimal conforming vector: the five required hooks and one owner.

    Exists so Property 21 can measure the *driver's own* write path
    (``_persist``) and not only the module-level pair, because that is the path
    every lifecycle transition persists through.
    """

    operation_kind = "strategic_strike"
    branch = "weapons"

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def validate_target(self, ctx):
        return None

    def build_record(self, ctx):
        return OperationRecord(kind=self.operation_kind)

    def on_resolve(self, record):
        return None

    def persistence_owner(self, record):
        return self.owner

    def discover_records(self, planet_rooms):
        return [self.owner]


# ================================================================== #
#  Property 21
# ================================================================== #
#
# The claim is an identity, so the whole question is which path it is measured
# over. Three, all in the one test below, because they are three different ways
# a record reaches storage and a bug can live in any of them:
#
# 1. **In memory** — ``to_dict`` then ``from_dict``. The identity the design
#    rests the property on, with nothing durable involved.
# 2. **Through the durable owner** — ``_write_records`` then ``_read_records``
#    then ``from_dict``, under a handler that discards an in-place change. This
#    is the literal reading of "writing it to its durable owner and rebuilding
#    from that owner", and it is measured over the WHOLE drawn record space
#    including the terminal states, because the write half of the pair stores
#    what it is given and the rebuild is what later decides which payloads to
#    track (R8.22).
# 3. **Through the driver** — ``_persist``, which is what every transition
#    actually calls. Two behaviours of its own, both asserted: persisting one
#    record repeatedly upserts by ``op_id`` rather than appending, and a
#    TERMINAL record is REMOVED from the container instead of being stored. The
#    sweep is a separate clause rather than a caveat on clause 2: it is the
#    reason an owner's container holds exactly its live operations, and a
#    round-trip assertion routed through ``_persist`` would otherwise read as a
#    failure for every terminal record the generator draws.
#
# Both states are forced on every example — the drawn record is replayed once
# with a live state and once with a terminal one — so neither branch of the
# sweep waits on the generator to happen to produce it.


# Feature: tech-tree-branch-foundation, Property 21: An Operation_Record
# round-trips through persistence
#
# **Validates: Requirements 8.21, 8.22, 14.1, 14.2**
class TestProperty21RecordRoundTrip(unittest.TestCase):
    """Writing a record to its durable owner and reading it back changes nothing.

    Every field the requirement enumerates is compared by name, so a failure
    says which one did not survive rather than only that something did not.
    """

    def _assert_named_fields(self, rebuilt, written, where):
        """The eleven clauses Property 21 names, field by field."""
        for name in ROUND_TRIP_FIELDS:
            self.assertEqual(
                getattr(rebuilt, name), getattr(written, name),
                f"{where}: {name} did not survive the round trip",
            )

    @given(
        record=record_st,
        live_state=st.sampled_from(LIVE_STATE_VALUES),
        terminal_state=st.sampled_from(TERMINAL_STATE_VALUES),
    )
    @settings(max_examples=100)
    def test_a_record_round_trips_through_its_durable_owner(
        self, record, live_state, terminal_state
    ):
        """**Validates: Requirements 8.21, 8.22, 14.1, 14.2**"""
        # -- 1. In memory: ``to_dict`` then ``from_dict`` (R8.21) ----------- #
        payload = record.to_dict()
        self.assertEqual(
            set(payload), set(PERSISTED_FIELDS),
            "the persisted payload must carry a key for every recorded field",
        )
        self._assert_named_fields(
            OperationRecord.from_dict(payload), record, "in memory"
        )

        # -- 2. Through the durable owner (R14.1, R14.2) -------------------- #
        owner = _DurableOwner()
        _write_records(owner, [record])
        read = _read_records(owner)
        self.assertEqual(
            [entry.get("op_id") for entry in read], [record.op_id],
            "the owner must hold exactly the one record that was written",
        )
        rebuilt = OperationRecord.from_dict(read[0])
        self._assert_named_fields(rebuilt, record, "through the durable owner")

        # Stronger than the eleven clauses, and the form the design states:
        # ``to_dict`` then ``from_dict`` is the identity on the persisted
        # fields, so the whole record comes back — the planet it lives on, its
        # bounded lifetime, its suspension snapshot, and its identity too.
        self.assertEqual(
            rebuilt, record,
            "every persisted field must survive, not only the eleven named ones",
        )

        # What makes that survivable across a real restart: storage holds
        # values, never a dataclass and never an enum (design §7).
        violations = list(_plain_data_violations(owner.stored()))
        self.assertEqual(
            violations, [],
            f"persisted state must be plain data; found {violations}",
        )

        # -- 3. Through the driver's own write path (R8.22, R14.1) ---------- #
        vector = _Vector(_DurableOwner())
        live = replace(record, state=live_state)
        vector._persist(live)
        vector._persist(live)  # again: an upsert keyed by op_id, not an append
        stored = _read_records(vector.owner)
        self.assertEqual(
            [entry.get("op_id") for entry in stored], [live.op_id],
            "persisting one record repeatedly must store it exactly once",
        )
        persisted = OperationRecord.from_dict(stored[0])
        self._assert_named_fields(persisted, live, "through the driver")
        self.assertEqual(
            persisted, live,
            "a non-terminal record must be recoverable from its owner in full, "
            "so the rebuild can resume advancing it (R8.22)",
        )

        # The terminal sweep, which is the one case that does NOT round-trip by
        # design: the rebuild skips a terminal record anyway, so the driver
        # removes it rather than growing the container without bound.
        vector._persist(replace(live, state=terminal_state))
        self.assertEqual(
            _read_records(vector.owner), [],
            f"a {terminal_state} record must be removed from its owner, not stored",
        )


# ================================================================== #
#  Property 23
# ================================================================== #
#
# "Documented defaults" is only a claim if the defaults are stated somewhere
# other than the code being measured, so the reference is
# :data:`DOCUMENTED_DEFAULTS` — written from the design — and the property's
# first clause is that the shipped dataclass agrees with it. A default changed
# in the code alone fails there; a default changed in both fails nothing, which
# is correct, because at that point the design was changed too.
#
# The generated input space is the one the property states: a persisted record
# dict with an arbitrary subset of keys **removed** and an arbitrary subset
# **set to None**. The two subsets are drawn independently and may overlap, so
# every field is reachable in all three conditions — present, null, absent — and
# the fully stripped payload (every key removed) is the bottom of the lattice.
#
# One boundary is asserted alongside, because "never raises" is otherwise
# ambiguous about where the tolerance stops: a **non-mapping** payload is not a
# partial record but a corrupt one, and ``from_dict`` raises ``TypeError`` on it
# deliberately, so the rebuild's log-and-recover path (R14.5) stays reachable
# instead of a corrupt payload being read as an empty operation. The read path
# is the other half of that: ``_read_records`` drops a non-mapping entry and
# recovers the rest, so a corrupt payload never reaches ``from_dict`` through
# persistence at all. Both are clauses of this property rather than a separate
# one — they pin the edge of the input space the property covers.


# Feature: tech-tree-branch-foundation, Property 23: Reading a partial record
# yields documented defaults and never raises
#
# **Validates: Requirements 14.8**
class TestProperty23PartialRecordDefaults(unittest.TestCase):
    """A record written by an older build, or hand-edited, still reads back whole."""

    def _read(self, payload, where):
        """``from_dict`` over *payload*, failing the test on any exception."""
        try:
            return OperationRecord.from_dict(payload)
        except Exception as error:  # noqa: BLE001 - R14.8: a partial read never raises
            self.fail(f"{where} raised {type(error).__name__}: {error}")

    @given(
        record=record_st,
        removed=st.sets(st.sampled_from(PERSISTED_FIELDS)),
        nulled=st.sets(st.sampled_from(PERSISTED_FIELDS)),
    )
    @settings(max_examples=100)
    def test_a_partial_record_reads_as_the_documented_defaults(
        self, record, removed, nulled
    ):
        """**Validates: Requirements 14.8**"""
        # -- 0. The reference table is the shipped set of defaults ---------- #
        self.assertEqual(
            tuple(f.name for f in fields(OperationRecord)), PERSISTED_FIELDS,
            "the recorded fields and the reference tuple must not disagree",
        )
        shipped = OperationRecord()
        for name, default in DOCUMENTED_DEFAULTS.items():
            self.assertEqual(
                getattr(shipped, name), default,
                f"{name}'s documented default is {default!r}",
            )

        # -- The partial payload -------------------------------------------- #
        payload = record.to_dict()
        for key in removed:
            payload.pop(key, None)
        for key in nulled - removed:
            payload[key] = None
        absent = removed | nulled

        rebuilt = self._read(payload, "the partial payload")

        # -- 1. Fully populated, defaults where the value was absent -------- #
        self.assertIsInstance(rebuilt, OperationRecord)
        again = self._read(payload, "the same partial payload, read twice")
        for name in PERSISTED_FIELDS:
            if name == "op_id":
                continue  # its default is a fresh identity — see clause 2
            expected = (
                DOCUMENTED_DEFAULTS[name] if name in absent
                else getattr(record, name)
            )
            condition = "absent" if name in absent else "written"
            self.assertEqual(
                getattr(rebuilt, name), expected,
                f"{name} was {condition}, so it must read as {expected!r}",
            )
            # Reading the same payload twice reads the same record, so a
            # default is a documented value and not a drifting one.
            self.assertEqual(
                getattr(again, name), getattr(rebuilt, name),
                f"{name} differed between two reads of one payload",
            )

        # -- 2. The identity: a fresh default, never a blank one ------------ #
        if "op_id" in absent:
            self.assertIsInstance(rebuilt.op_id, str)
            self.assertTrue(
                rebuilt.op_id, "a record with no identity must be minted one"
            )
            self.assertNotEqual(
                rebuilt.op_id, again.op_id,
                "two id-less records must not collide into one tracked entry",
            )
        else:
            self.assertEqual(rebuilt.op_id, record.op_id)
            self.assertEqual(again.op_id, record.op_id)

        # -- 3. Fully populated means usable -------------------------------- #
        self.assertEqual(
            rebuilt.is_terminal, str(rebuilt.state) in TERMINAL_STATE_VALUES,
            f"a rebuilt {rebuilt.state!r} record misreports whether it is terminal",
        )
        if "state" in absent:
            self.assertEqual(
                rebuilt.state, OperationState.PENDING,
                "a record with no readable state is treated as in flight",
            )
            self.assertFalse(rebuilt.is_terminal)

        # -- 4. The boundary: a non-mapping is the one payload that raises -- #
        corrupt = sorted(payload.items())
        with self.assertRaises(TypeError):
            OperationRecord.from_dict(corrupt)

        # And the read path never hands ``from_dict`` one: a non-mapping entry
        # is dropped and the rest recovered (R14.5), so the tolerance the
        # property claims holds end to end.
        owner = _DurableOwner()
        owner.attributes.add(ATTR_VECTOR_OPERATIONS, [payload, corrupt])
        recovered = _read_records(owner)
        self.assertEqual(
            len(recovered), 1,
            "the read must recover the partial record and drop the corrupt one",
        )
        via_read = self._read(recovered[0], "the payload the read path recovered")
        for name in PERSISTED_FIELDS:
            if name == "op_id" and "op_id" in absent:
                continue  # minted afresh on every read, by design
            self.assertEqual(
                getattr(via_read, name), getattr(rebuilt, name),
                f"{name} differed between a direct read and a recovered one",
            )


# ================================================================== #
#  Property 22
# ================================================================== #
#
# Four claims in one sentence, and they pull in different directions, so the
# reference computation below is what holds them together: given a drawn list of
# records and a world, it says of EACH record whether the rebuild must track it,
# Discard it, or skip it. Every clause is then measured against that one answer.
#
# What decides a record's fate, in the order the driver decides it:
#
# 1. **Corrupt** — the payload is not a mapping. One log line, and the remaining
#    records are still rebuilt (R14.5). Two layers reach this: ``_read_records``
#    drops a non-mapping entry *before* the rebuild sees it, and the rebuild's own
#    parse guard catches one that gets past the read. Both are measured, because
#    the second is the one R14.5 is actually about — the guard wraps
#    ``_rebuild_one`` as a UNIT, so the parse, the state read, the resolution, and
#    the discard transition all fail the same way.
# 2. **Terminal** — not an operation any more (R8.2), so it is neither tracked nor
#    Discarded and produces no log line.
# 3. **Dangling** — at least one reference the record needs no longer exists, so
#    it is Discarded with a log naming the Operation_Kind, the identity, and each
#    missing reference (R14.4).
# 4. **Whole** — every reference resolved, so it is tracked, keyed by ``op_id``.
#
# Three things about "a reference no longer exists" that the reference
# computation has to get right, because the driver deliberately does:
#
# * ``owner_ref``, ``building_ref``, and ``carrier_ref`` are **required**: an
#   absent one is missing, because an operation with no owner, no originating
#   building, or no Carrier_Agent is not an operation the contract describes
#   (R7.1, R8.21).
# * ``target_ref`` is **optional when the record names a target coordinate**
#   (design §7 makes the coordinate and the entity alternatives). A record naming
#   neither is aimed at nothing and IS Discarded — which is why "any reference is
#   ``None``" is the wrong reference computation and this one carves the case out.
# * **"Cannot judge" discards nothing.** A reference that is not a live object and
#   cannot be looked up — no world handed in, or a value that reads as no id — is
#   left as it was and TRACKED. That is why the world built below always holds at
#   least one live object: over an empty world index every reference becomes
#   unjudgeable, nothing is found missing, and the dangling clause would pass
#   vacuously. The no-world case is a clause of its own instead, asserted against
#   the same reference computation with judging turned off.
#
# And one consequence of resolution, for the clause that checks what a rebuilt
# record holds: after a rebuild a record's references are **live objects**, not
# the values that were persisted. Property 21's plain-data clause is unaffected —
# it never rebuilds — but this property compares references by IDENTITY.

#: The four references a rebuild resolves, in the order R14.4's log names them.
#: Cross-checked against ``OperationDriver._RESOLVED_REFS`` inside the property,
#: for the same reason :data:`PERSISTED_FIELDS` is cross-checked against the
#: dataclass: a reference added to the resolution set without a spec change must
#: fail here rather than quietly escaping the reference computation.
REBUILD_REFS: tuple[str, ...] = (
    "owner_ref",
    "building_ref",
    "carrier_ref",
    "target_ref",
)

#: The reference names the property nulls a subset of, as the design's strategy
#: list spells them (``"owner"``, ``"building"``, ``"carrier"``, ``"target"``).
NULLABLE_REFS: tuple[str, ...] = tuple(
    name.removesuffix("_ref") for name in REBUILD_REFS
)

#: The logger the rebuild reports every discard and every recovered failure
#: through.
CONTRACT_LOGGER = "evennia.world.systems.operation_contract"

#: The planet key the world mapping is filed under. Arbitrary: the rebuild
#: resolves references out of the *rooms* and never reads ``record.planet``.
WORLD_PLANET = "earth"

#: The database ids ``branch_strategies.ref_st`` spells a reference from, held
#: here by value so the world can be built to hold *or* to lack each of them
#: independently of what a draw happens to name. The property asserts every drawn
#: reference reads as one of these, so a change to the shared pool fails loudly
#: rather than quietly narrowing what the world can be made to lack.
REF_ID_POOL: tuple[int, ...] = (1, 2, 3, 4, 5)

#: The id of the world's anchor object — one live object with an id no generated
#: reference spells, live in every world this property builds so the world index
#: is never empty. An empty index is the "cannot judge" case (see above), and a
#: dangling-reference clause measured over one would assert nothing. The property
#: asserts no drawn reference reads as this id, so a change to the shared
#: reference pool fails loudly instead of hollowing the clause out.
ANCHOR_REF_ID = 9001

#: The identity the collision clause gives every record, to measure R14.3's
#: keying directly. Two records *happening* to share an ``op_id`` is far too rare
#: to wait for, so the clause plants the collision instead.
SHARED_OP_ID = "dd" * 16

#: The identities of the two records planted in every payload list (see
#: :func:`_planted_records`).
PLANTED_OP_IDS: tuple[str, str] = ("ee" * 16, "ff" * 16)


def _planted_records():
    """Return two records the rebuild can always resolve.

    A *drawn* record is trackable only when all three required references happen
    to be both present and live, which is rare enough that most examples would
    hold nothing to track at all — and the whole tracked half of this property,
    the keying that makes it idempotent included, would assert nothing on them.
    So two records that always resolve are appended to every payload list.

    Both name the world's **anchor** object, which is live in every world built
    here whatever the draw made absent, and between them they spell a reference
    both ways a persisted one arrives in (a plain id and a ``#dbref``). One is
    aimed at a tile and one at an entity, so design §7's two ways for a record to
    have a target — and therefore both sides of the optional-target carve-out —
    are exercised on every example.
    """
    return [
        OperationRecord(
            op_id=PLANTED_OP_IDS[0],
            kind=_Vector.operation_kind,
            owner_ref=ANCHOR_REF_ID,
            building_ref=f"#{ANCHOR_REF_ID}",
            carrier_ref=ANCHOR_REF_ID,
            planet=WORLD_PLANET,
            target_x=3,
            target_y=4,
            target_ref=None,                 # aimed at a tile: no entity needed
            ticks_remaining=6,
            state=OperationState.PENDING,
        ),
        OperationRecord(
            op_id=PLANTED_OP_IDS[1],
            kind=_Vector.operation_kind,
            owner_ref=f"#{ANCHOR_REF_ID}",
            building_ref=ANCHOR_REF_ID,
            carrier_ref=f"#{ANCHOR_REF_ID}",
            planet=WORLD_PLANET,
            target_ref=ANCHOR_REF_ID,        # aimed at an entity
            ticks_remaining=2,
            state=OperationState.SUSPENDED,
        ),
    ]


#: How a corrupted persisted record is spelled: anything that is not a mapping,
#: which is the one payload ``from_dict`` raises on (see Property 23's boundary
#: clause) and therefore the one R14.5 exists for. Four shapes rather than one,
#: cycled by position, because a hand-edited or truncated container can hold any
#: of them and a guard written against ``list`` alone would pass on ``None``.
CORRUPT_SHAPES: tuple = (
    lambda payload: sorted(payload.items()),
    lambda payload: "not a record at all",
    lambda payload: 7,
    lambda payload: None,
)


def _readable_ref_id(ref):
    """Return the database id *ref* spells, or ``None`` when it spells none.

    The reference computation's own reading of a persisted reference, written
    here rather than borrowed from the module under test so the expected discard
    set is computed independently of the code that produces it. Covers the three
    spellings ``branch_strategies.ref_st`` generates — an integer id, the
    ``"#5"`` dbref naming the same id, and ``None`` — and answers ``None`` for
    anything else, which is the "cannot be read as an id" case.
    """
    if ref is None or isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        text = ref.strip()
        text = text[1:].strip() if text.startswith("#") else text
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _expected_missing(record, live_ids, judgeable=True):
    """Return the references *record* must be Discarded over, in R14.4's order.

    Args:
        record: The record as it was persisted, references still spelled as
            values.
        live_ids: The database ids the world really holds.
        judgeable: Whether a reference can be looked up at all. ``False`` is the
            no-world case, where only an *absent* reference is missing — nothing
            may be concluded about a reference nobody could resolve.
    """
    missing = []
    for name in REBUILD_REFS:
        ref = getattr(record, name)
        if ref is None:
            aimed_at_a_tile = (
                name == "target_ref"
                and record.target_x is not None
                and record.target_y is not None
            )
            if not aimed_at_a_tile:
                missing.append(name)          # required, and absent
            continue
        if not judgeable:
            continue                          # nothing to look it up in
        ref_id = _readable_ref_id(ref)
        if ref_id is None:
            continue                          # reads as no id: judge nothing
        if ref_id not in live_ids:
            missing.append(name)              # R14.4: it no longer exists
    return missing


class _LiveObject:
    """A live world object a persisted reference resolves to.

    The whole surface the resolution reads: an ``id`` for the world index, a
    non-``None`` ``pk`` so it is not mistaken for a deleted object, and an
    ``attributes`` handler, which is what makes it a *world object* rather than a
    value to the driver's duck-typed predicate.
    """

    def __init__(self, obj_id):
        self.id = obj_id
        self.pk = obj_id
        self.key = f"live-{obj_id}"
        self.attributes = HostileFakeAttributes()

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"_LiveObject({self.id})"


class _WorldRoom:
    """A planet room, as far as the rebuild reads one: its ``contents``."""

    def __init__(self, entities):
        self.contents = list(entities)


class _World:
    """The world a rebuild is handed, plus what a reference computation needs.

    Holds a live object for every id in :data:`REF_ID_POOL` except the *absent*
    ones, which is what makes a reference resolvable or dangling by draw rather
    than by accident: a world derived from whichever ids a draw happened to name
    would leave one of the two fates almost unreachable.

    The anchor is always live, whatever the draw made absent, so the world index
    is never empty — an empty one is the "cannot judge" case, and a
    dangling-reference clause measured over it would assert nothing (see
    :data:`ANCHOR_REF_ID`).
    """

    def __init__(self, records, absent=()):
        self.named_ids = tuple(sorted({
            ref_id
            for record in records
            for ref_id in (
                _readable_ref_id(getattr(record, name)) for name in REBUILD_REFS
            )
            if ref_id is not None
        }))
        self.live_ids = {ANCHOR_REF_ID} | {
            ref for ref in REF_ID_POOL if ref not in set(absent)
        }
        self.entities = {ref: _LiveObject(ref) for ref in sorted(self.live_ids)}
        self.anchor = self.entities[ANCHOR_REF_ID]
        self.rooms = {WORLD_PLANET: _WorldRoom(self.entities.values())}

    @staticmethod
    def unreadable():
        """Return the worlds nothing can be looked up in.

        None handed in, an empty mapping, a mapping whose room holds nothing, and
        a value that is not a world at all. Every one of them makes the world
        index empty, which is the "cannot judge" case.
        """
        return (None, {}, {WORLD_PLANET: _WorldRoom([])}, "not a world")


class _RebuildVector(_Vector):
    """The rebuild subject: :class:`_Vector` plus the surface a rebuild reaches.

    ``notify`` is wired deliberately. The discard transition publishes
    ``vector_discarded``, and a driver with no notification path logs a WARNING
    saying so (R15.2) — which is correct behaviour and would sit in the middle of
    the log lines this property counts. Supplying one keeps the captured log
    about the rebuild.
    """

    def __init__(self, owner):
        super().__init__(owner)
        self.swept = []
        self.discarded = []
        self.notified = []

    def discover_records(self, planet_rooms):
        self.swept.append(planet_rooms)
        return [self.owner]

    def on_discard(self, record):
        self.discarded.append(record.op_id)

    def notify(self, player, kind, **data):
        self.notified.append((player, kind, data))
        return True


class _SeededReadVector(_RebuildVector):
    """A vector whose read hands the rebuild the payload list verbatim.

    Two things this reaches that the persistence-backed vector cannot:

    * the rebuild's **own parse guard** (R14.5), because the shipped read path
      drops a non-mapping entry before the rebuild ever sees it; and
    * R14.3's **"from the same persisted state twice"** literally — it persists
      nowhere (``persistence_owner`` answers ``None``, which is a supported
      answer meaning "this operation persists nowhere"), so no discard can edit
      the state the second rebuild reads.
    """

    def __init__(self, owner, payloads):
        super().__init__(owner)
        self.payloads = list(payloads)

    def _read_records(self, owner):
        return list(self.payloads)

    def persistence_owner(self, record):
        return None


class _SweeplessVector(_RebuildVector):
    """A vector whose sweep raises — the broken vector isolation is about."""

    def discover_records(self, planet_rooms):
        raise RuntimeError("this vector cannot sweep the world")


class _LogCapture(logging.Handler):
    """Capture the contract logger's records, including none at all.

    ``assertLogs`` cannot express "exactly this many lines, possibly zero", and
    zero is a legitimate example here — a draw with no corrupt payload and no
    dangling reference must produce no log line at all, which is itself part of
    what the counting clauses claim.
    """

    def __init__(self, level=logging.WARNING):
        super().__init__(level=level)
        self.records = []
        self._logger = logging.getLogger(CONTRACT_LOGGER)
        self._level = level
        self._restore = self._logger.level

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self._restore = self._logger.level
        self._logger.setLevel(self._level)
        self._logger.addHandler(self)
        return self

    def __exit__(self, *_exc_info):
        self._logger.removeHandler(self)
        self._logger.setLevel(self._restore)
        return False

    def matching(self, needle):
        """The messages containing *needle*, in the order they were logged."""
        return [
            record.getMessage()
            for record in self.records
            if needle in record.getMessage()
        ]


# Feature: tech-tree-branch-foundation, Property 22: Rebuilding is idempotent,
# isolated, and discards dangling records
#
# **Validates: Requirements 14.3, 14.4, 14.5**
class TestProperty22RebuildIsIdempotentAndIsolated(unittest.TestCase):
    """A restart recovers every operation it can and loses only what it must."""

    def _op_ids(self, vector):
        """The identities *vector* is tracking, as a sorted list."""
        return sorted(record.op_id for record in vector.tracked_records())

    def _assert_no_duplicates(self, vector, where):
        """R14.3's own words: a rebuild duplicates no Vector_Operation."""
        op_ids = self._op_ids(vector)
        self.assertEqual(
            len(op_ids), len(set(op_ids)),
            f"{where}: the tracked set holds a duplicated op_id — {op_ids}",
        )

    def _assert_tracked(self, vector, expected, where):
        """The tracked set is exactly the whole records, keyed by ``op_id``."""
        self.assertEqual(
            self._op_ids(vector), sorted(expected),
            f"{where}: the wrong operations are tracked",
        )
        self._assert_no_duplicates(vector, where)

    def _assert_discard_log(self, capture, dangling, kind, where):
        """R14.4: one log line per discard, naming the kind and each missing ref."""
        lines = capture.matching("discarded operation")
        self.assertEqual(
            len(lines), len(dangling),
            f"{where}: expected {len(dangling)} discard log line(s), got "
            f"{len(lines)} — {lines}",
        )
        for record, missing in dangling:
            named = [line for line in lines if str(record.op_id) in line]
            self.assertTrue(
                named,
                f"{where}: nothing logged the discard of {record.op_id}",
            )
            self.assertTrue(
                [
                    line for line in named
                    if kind in line and all(ref in line for ref in missing)
                ],
                f"{where}: no discard log line for {record.op_id} names both "
                f"{kind!r} and every missing reference {missing} — {named}",
            )

    @given(
        drawn=st.lists(record_st, max_size=6),
        nulled=st.sets(st.sampled_from(NULLABLE_REFS)),
        # Which of the pooled reference ids the world does NOT hold. The empty
        # set is the world that resolves everything and the full set is the world
        # that resolves nothing but the anchor.
        absent=st.sets(st.sampled_from(REF_ID_POOL)),
        # The design's strategy is ``st.sets(st.integers())`` over the corrupt
        # INDICES. Bounded to just past the list cap here: an index naming no
        # record is the same input as no index at all, so an unbounded pool would
        # leave the corruption clause vacuous on nearly every example, while
        # 6 and 7 still generate the "names no record" case.
        corrupt=st.sets(st.integers(min_value=0, max_value=7)),
    )
    @settings(max_examples=200)
    def test_a_rebuild_is_idempotent_isolated_and_discards_dangling_records(
        self, drawn, nulled, absent, corrupt
    ):
        """**Validates: Requirements 14.3, 14.4, 14.5**"""
        # -- 0. The reference set under test is the shipped one -------------- #
        self.assertEqual(
            OperationDriver._RESOLVED_REFS, REBUILD_REFS,
            "the references a rebuild resolves and the reference tuple disagree",
        )

        # The drawn references are nulled on every OTHER record rather than on
        # all of them: a required reference nulled across the whole list makes
        # one draw decide every record's fate. The absent reference is not
        # under-exercised by that — ``ref_st`` draws ``None`` for a third of the
        # references it generates anyway.
        nulls = {f"{name}_ref": None for name in nulled}
        drawn_records = [
            replace(record, **nulls) if index % 2 else record
            for index, record in enumerate(drawn)
        ]
        world = _World(drawn_records, absent)
        self.assertNotIn(
            ANCHOR_REF_ID, world.named_ids,
            "the anchor id must be one no generated reference spells, or the "
            "world index could be empty and judge nothing",
        )
        self.assertEqual(
            [ref for ref in world.named_ids if ref not in REF_ID_POOL], [],
            "every generated reference must read as a pooled id, or the world "
            "cannot be built to hold or to lack the ids under test",
        )

        # Corruption lands on the DRAWN records only; the two planted ones are
        # appended afterwards, so every example holds something to track however
        # the draw fell (see :func:`_planted_records`).
        planted = _planted_records()
        self.assertFalse(
            {record.op_id for record in drawn_records} & set(PLANTED_OP_IDS),
            "a planted identity must not collide with a drawn one",
        )
        payloads = [
            CORRUPT_SHAPES[index % len(CORRUPT_SHAPES)](record.to_dict())
            if index in corrupt else record.to_dict()
            for index, record in enumerate(drawn_records)
        ] + [record.to_dict() for record in planted]
        corrupted = sum(1 for index in corrupt if index < len(drawn_records))

        # -- The reference computation: what each intact record must become -- #
        intact = [
            *(
                record for index, record in enumerate(drawn_records)
                if index not in corrupt
            ),
            *planted,
        ]
        live = [
            record for record in intact
            if str(record.state) not in TERMINAL_STATE_VALUES
        ]
        dangling = [
            (record, _expected_missing(record, world.live_ids))
            for record in live
            if _expected_missing(record, world.live_ids)
        ]
        whole_by_id = {
            record.op_id: record
            for record in live
            if not _expected_missing(record, world.live_ids)
        }
        kind = _RebuildVector.operation_kind

        # -- 1. Through the durable owner: track, Discard, recover ---------- #
        # Seeded straight onto the attribute rather than through
        # ``_write_records``, because the write path drops a non-mapping entry
        # too — a corrupt payload only exists in storage if something other than
        # this feature put it there (an older build, a hand edit, a failed write).
        owner = _DurableOwner()
        owner.attributes.add(ATTR_VECTOR_OPERATIONS, payloads)
        vector = _RebuildVector(owner)
        with _LogCapture() as capture:
            counted = vector.rebuild(world.rooms)

        self.assertEqual(counted, len(vector.tracked_records()))
        self._assert_tracked(vector, whole_by_id, "through the durable owner")
        self.assertEqual(vector.swept, [world.rooms])
        self._assert_discard_log(
            capture, dangling, kind, "through the durable owner"
        )
        self.assertEqual(
            sorted(vector.discarded), sorted(record.op_id for record, _ in dangling),
            "every dangling record — and only those — must reach on_discard",
        )

        # The first line of defence, measured on its own owner: ONE read drops
        # each corrupt payload once and recovers the rest, so the rebuild above
        # never saw them. Measured as a single read rather than counted inside
        # the rebuild's capture because a read is BY VALUE and reports what it
        # dropped every time — the discards above each re-read the container in
        # order to persist, so the same unreadable entry is honestly re-reported.
        untouched = _DurableOwner()
        untouched.attributes.add(ATTR_VECTOR_OPERATIONS, payloads)
        with _LogCapture() as read_logs:
            recovered = _read_records(untouched)
        self.assertEqual(
            len(recovered), len(payloads) - corrupted,
            "the read must recover every payload that is a record",
        )
        self.assertEqual(
            len(read_logs.matching("not a record")), corrupted,
            "the read must drop each corrupt payload with exactly one log line",
        )

        # A rebuilt record holds LIVE OBJECTS, not the values it was persisted
        # as: that is what carries the cancellation and suspension triggers
        # across a restart, so it is compared by identity.
        for record in vector.tracked_records():
            source = whole_by_id[record.op_id]
            for name in REBUILD_REFS:
                ref_id = _readable_ref_id(getattr(source, name))
                expected = (
                    world.entities[ref_id] if ref_id in world.live_ids
                    else getattr(source, name)
                )
                self.assertIs(
                    getattr(record, name), expected,
                    f"{record.op_id}: {name} was not resolved to what it names",
                )
            self.assertEqual(record.ticks_remaining, source.ticks_remaining)
            self.assertEqual(record.state, str(source.state))

        # -- 2. Idempotent: the same persisted state, rebuilt twice (R14.3) -- #
        # The container is restored first, because the first rebuild legitimately
        # edited it: a discard is terminal, so the persist that settles it
        # removes that record. R14.3 is about rebuilding "from the same persisted
        # state twice", which is what restoring makes it.
        once = self._op_ids(vector)
        owner.attributes.add(ATTR_VECTOR_OPERATIONS, payloads)
        with _LogCapture():
            vector.rebuild(world.rooms)
        self.assertEqual(
            self._op_ids(vector), once,
            "rebuilding the same persisted state twice must track the same set",
        )
        self._assert_no_duplicates(vector, "rebuilt twice")

        # And rebuilding onward from the state the first rebuild left — the real
        # sequence a second restart would see — adds nothing and duplicates
        # nothing; the records it no longer finds are the ones it discarded.
        with _LogCapture():
            vector.rebuild(world.rooms)
        self.assertLessEqual(
            set(self._op_ids(vector)), set(once),
            "a repeated rebuild must invent no operation",
        )
        self._assert_no_duplicates(vector, "rebuilt onward")

        # One record reached through two owners a sweep yielded twice is still
        # one operation, for the same reason: the tracked map is keyed by op_id.
        owner.attributes.add(ATTR_VECTOR_OPERATIONS, payloads)
        twice_swept = _RebuildVector(owner)
        twice_swept.discover_records = lambda planet_rooms: [owner, owner]
        with _LogCapture():
            twice_swept.rebuild(world.rooms)
        self.assertEqual(
            self._op_ids(twice_swept), once,
            "an owner swept twice must not duplicate the records it holds",
        )

        # And the keying itself, measured head-on: give every payload ONE
        # identity and the whole list must collapse to a single tracked
        # operation. This is what "keyed by op_id, so no operation is duplicated"
        # claims, and it is the clause a rebuild that appended to a list instead
        # of keying a map would fail.
        collided = _SeededReadVector(_DurableOwner(), [
            dict(payload, op_id=SHARED_OP_ID) if isinstance(payload, dict)
            else payload
            for payload in payloads
        ])
        with _LogCapture(level=logging.ERROR):
            collided.rebuild(world.rooms)
        self.assertEqual(
            self._op_ids(collided), [SHARED_OP_ID] if whole_by_id else [],
            "every trackable record sharing one identity must collapse to one "
            "tracked operation",
        )

        # -- 3. Isolated per record: the rebuild's own parse guard (R14.5) --- #
        seeded = _SeededReadVector(_DurableOwner(), payloads)
        with _LogCapture(level=logging.ERROR) as errors:
            seeded.rebuild(world.rooms)
        self.assertEqual(
            len(errors.matching("a rebuild step failed")), corrupted,
            "each corrupt record must cost exactly one log line, and one record",
        )
        self._assert_tracked(seeded, whole_by_id, "past the parse guard")

        # The same vector reads the same payloads however often it rebuilds — it
        # persists nowhere — so this is R14.3 with nothing else moving.
        with _LogCapture(level=logging.ERROR):
            seeded.rebuild(world.rooms)
        self._assert_tracked(seeded, whole_by_id, "past the parse guard, twice")

        # -- 4. "Cannot judge" discards nothing ----------------------------- #
        # No world, an empty one, a world whose rooms are empty, and a value that
        # is not a world: a reference nobody could look up is left as it was and
        # TRACKED, so only an ABSENT reference is missing here.
        unjudged = {
            record.op_id: record
            for record in live
            if not _expected_missing(record, (), judgeable=False)
        }
        # ``subTest`` is deliberately not used: Hypothesis disables its reporting
        # inside a ``@given`` test, so the world is named in the message instead.
        for empty in world.unreadable():
            blind = _SeededReadVector(_DurableOwner(), payloads)
            with _LogCapture():
                blind.rebuild(empty)
            self._assert_tracked(blind, unjudged, f"over the world {empty!r}")

        # -- 5. Isolated per vector: one rebuild cannot cost another --------- #
        first = _SeededReadVector(_DurableOwner(), payloads)
        with _LogCapture(level=logging.ERROR):
            first.rebuild(world.rooms)
        before = self._op_ids(first)

        with _LogCapture(level=logging.ERROR) as broken_logs:
            self.assertEqual(_SweeplessVector(_DurableOwner()).rebuild(world.rooms), 0)
        self.assertTrue(
            broken_logs.matching("discover_records failed"),
            "a vector that cannot sweep must log why rather than raise",
        )
        self.assertEqual(
            self._op_ids(first), before,
            "a broken vector's rebuild must not disturb another vector's set",
        )

        second = _SeededReadVector(_DurableOwner(), payloads)
        with _LogCapture(level=logging.ERROR):
            second.rebuild(world.rooms)
        self.assertEqual(
            self._op_ids(first), before,
            "one vector's rebuild must not disturb another vector's set",
        )
        self._assert_tracked(second, whole_by_id, "a second vector")


if __name__ == "__main__":
    unittest.main()
