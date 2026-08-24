"""
Operation Contract — the Vector_Operation lifecycle value types.

A **Vector_Operation** is one instance of a Branch's Signature_Vector, from the
request that creates it to the terminal state that ends it. This module holds
the *value types* every one of the six Vector_Systems speaks:

* :class:`OperationState` — the six lifecycle states (R8.1), and
  :data:`TERMINAL_STATES`, the four of them that end an operation (R8.2).
* :class:`OperationRecord` — the persisted description of one Vector_Operation
  (R8.21), with the :meth:`~OperationRecord.to_dict` /
  :meth:`~OperationRecord.from_dict` pair that moves it to and from a durable
  owner's attribute (R14.1, R14.8).
* :class:`OperationOutcome` — the value a request answers with, naming the
  resulting lifecycle state or the refusal, so a caller reads the result rather
  than inferring it (R8.24).
* :class:`OperationContext` — the working state of one request as it walks the
  ordered validation chain, and the ``ctx`` a vector's hooks read.
* :func:`_read_records` / :func:`_write_records` — the read-copy-write pair that
  moves those records to and from the durable owner's ``vector_operations``
  attribute (R14.1, R14.7, R14.8).
* :class:`OperationDriver` — the framework half of a Vector_System, which owns
  the control flow those types travel through: a vector subclasses it, supplies
  five hooks, and inherits the whole lifecycle.

The lifecycle has two halves, and both live on the driver. The **request** half
walks the ordered validation chain, charges, and places an operation in Pending.
The **runtime** half advances it: ``advance_all`` gives every operation one tick
inside its own try/except (R8.9, R8.10), and each of the conditions that can end
or pause one reaches its transition from exactly one place — the polled ones (a
benched carrier, a lapsed Branch_Commitment) from the tick itself, because
nothing announces them, and the announced ones (a slain Carrier_Agent, a
destroyed building, an eliminated base) from three event subscriptions, because
each of those has to act at the moment it happens. Every one of them routes
through :meth:`OperationDriver.cancel` or :meth:`OperationDriver.suspend`, so
the single-writer guarantee covers the event-driven half exactly as it covers
the tick.

**Two ways to reach an entity, and no third (R8.23).**
:meth:`OperationDriver.apply_hit` routes one hit through
``CombatEngine.apply_direct_hit`` and :meth:`OperationDriver.apply_effect`
appends to the existing ``db.active_effects`` list; both attribute the effect to
the owning player (R10.3). Those are the only two effect paths a vector's
``on_resolve`` has to call, and this module writes no hit points, deletes
nothing, and reassigns no ownership — which is what makes the damage-balance
guardrails (R9.11, R10.1, R10.2) and R9.8's "no Vector_Operation deletes a
building outright" inherited rather than restated six times.

The value types come first and stand alone on purpose: they are pure data with
no collaborators, so a vector, a test, or a persistence helper can speak the
vocabulary without the driver.

Three invariants shape the module:

* **Framework-free (R15.1).** Nothing here imports a game-framework module at
  module scope; the imports are the stdlib plus ``world.constants``, which is
  itself framework-free. This module imports with ``evennia`` absent from
  ``sys.modules``, and it reaches the framework only *duck-typed*, through the
  ``attributes`` handler of whatever owner a vector hands the persistence pair.
* **Single resolvable references, never an object graph.** Every field of an
  :class:`OperationRecord` is a plain value or a reference to **one** world
  entity — never a container of objects and never anything holding
  back-references, which is what lets a whole record survive a restart as a
  plain dict. At runtime the four ``*_ref`` fields should hold the **live
  object itself**: the lifecycle's death, loss, and audience reads all judge
  live objects (a dbref is not a corpse), the attribute layer serializes an
  object reference to its dbref on persist, and the restart rebuild resolves
  each one back to the live object (:meth:`OperationDriver._resolve_refs`). A
  bare id or dbref string is *tolerated* — the rebuild and
  ``BranchSystem._owner_matches`` both read it — but it degrades the lifecycle
  until a rebuild re-lives it: R8.16's carrier death, R8.17's lost origin, and
  the owner's notifications all need the object, not its name.
* **A partial record never raises (R14.8).** Every field is read by value with
  its documented default, so a record written by an older build, hand-edited by
  an admin, or truncated by a failed write still reads back as a complete
  record. The dataclass field defaults below *are* those documented defaults:
  one place states each, and :meth:`OperationRecord.from_dict` falls back to it.

**No prose (R13.5).** A refused request answers a message *key* plus the
structured values required to pass the failing check — the ``MSG_VECTOR_*``
constants below, in the same shape ``BranchSystem``'s construction gates use —
and every lifecycle notification publishes a *kind* plus structured values, the
``NOTIFY_VECTOR_*`` constants below. Not one player-facing sentence is composed
here; the presenter and the command layer own every word. Each of
:data:`VECTOR_NOTIFICATION_KINDS` has a formatter in
``NotificationPresenter._FORMATTERS`` (R13.6, R13.8), and
:data:`STATE_NOTIFICATIONS` maps every one of the six lifecycle states to the
kind that reports reaching it, so no transition a player can see is silent.
The notification path itself is reached duck-typed for the same reason the
Branch services are: ``notify`` belongs to ``BaseSystem``, which reaches the
framework, so a driver composed without one degrades to a logged no-op (R15.2).

**The terminal vocabulary is shared by value, not by import.**
``world.systems.branch_system`` counts a player's in-flight operations over
records it reads duck-typed, and it compares the four terminal names as plain
strings rather than importing :class:`OperationState` — the dependency runs the
other way, a vector consumes Branch services and the Branch system never
consumes the contract. :class:`OperationState` being a ``StrEnum`` is what makes
that safe: a member, a plain string, and a persisted value all reduce to the
same name and hash alike, so ``"resolved" in TERMINAL_STATES`` is ``True``. The
two spellings must therefore stay identical; the states below are the authority
and ``branch_system._TERMINAL_STATE_NAMES`` is the private by-value copy.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from world.constants import ATTR_VECTOR_OPERATIONS, BRANCH_DOCTRINE

logger = logging.getLogger("evennia.world.systems.operation_contract")

__all__ = [
    "CANCEL_BASE_ELIMINATED",
    "CANCEL_CARRIER_KILLED",
    "CANCEL_ORIGIN_LOST",
    "MSG_VECTOR_CARRIER_REQUIRED",
    "MSG_VECTOR_COMMITMENT_REQUIRED",
    "MSG_VECTOR_COOLDOWN",
    "MSG_VECTOR_IN_FLIGHT_CAP",
    "MSG_VECTOR_INSUFFICIENT_RESOURCES",
    "MSG_VECTOR_ORIGIN_UNAVAILABLE",
    "MSG_VECTOR_TARGET_INVALID",
    "MSG_VECTOR_UNLOCK_REQUIRED",
    "MSG_VECTOR_UNWIRED",
    "NOTIFY_VECTOR_CANCELLED",
    "NOTIFY_VECTOR_CONSENT_REQUIRED",
    "NOTIFY_VECTOR_DISCARDED",
    "NOTIFY_VECTOR_EXPIRED",
    "NOTIFY_VECTOR_HIT",
    "NOTIFY_VECTOR_INCOMING",
    "NOTIFY_VECTOR_RESOLVED",
    "NOTIFY_VECTOR_RESUMED",
    "NOTIFY_VECTOR_SUSPENDED",
    "OperationContext",
    "OperationDriver",
    "OperationOutcome",
    "OperationRecord",
    "OperationState",
    "ORIGIN_MISSING",
    "ORIGIN_NOT_OPERATIONAL",
    "ORIGIN_NOT_OWNED",
    "STATE_NOTIFICATIONS",
    "SUSPEND_CARRIER_UNAVAILABLE",
    "SUSPEND_COMMITMENT_LAPSED",
    "TERMINAL_STATES",
    "VECTOR_NOTIFICATION_KINDS",
    "new_op_id",
]

#: Message KEYS the ordered validation chain refuses with — one per check that
#: has a refusal of its own, in the same key-plus-structured-data shape
#: ``BranchSystem``'s construction gates use. A refusal never composes prose
#: (R13.5): the key travels on the refusal detail's ``message`` entry and the
#: presenter or the command layer owns every word a player reads.
#:
#: The two checks with no key of their own are ``target`` and ``unlock``: a
#: target refusal carries whichever key the vector's own hook or
#: ``BranchSystem.may_target`` answered (the new-player shield, the allied
#: target, the missing consent, the escalation cap), so
#: :data:`MSG_VECTOR_TARGET_INVALID` is only the fallback for a hook that named
#: none.
MSG_VECTOR_UNWIRED = "vector_unwired"
MSG_VECTOR_COMMITMENT_REQUIRED = "vector_commitment_required"
MSG_VECTOR_ORIGIN_UNAVAILABLE = "vector_origin_unavailable"
MSG_VECTOR_UNLOCK_REQUIRED = "vector_unlock_required"
MSG_VECTOR_CARRIER_REQUIRED = "vector_carrier_required"
MSG_VECTOR_TARGET_INVALID = "vector_target_invalid"
MSG_VECTOR_COOLDOWN = "vector_cooldown"
MSG_VECTOR_IN_FLIGHT_CAP = "vector_in_flight_cap"
MSG_VECTOR_INSUFFICIENT_RESOURCES = "vector_insufficient_resources"

#: ``reason`` values on a :data:`MSG_VECTOR_ORIGIN_UNAVAILABLE` refusal, naming
#: which half of the origin check failed: no building was named at all, the
#: named one belongs to somebody else, or it is not Operational (which folds the
#: Active_HQ_Rule and the Branch being live — see ``BranchSystem.is_operational``).
ORIGIN_MISSING = "missing"
ORIGIN_NOT_OWNED = "not_owned"
ORIGIN_NOT_OPERATIONAL = "not_operational"

#: Notification KINDS the lifecycle publishes (design §4.4). A notification is
#: a kind plus structured values — never a composed sentence (R13.5) — and every
#: one of these is a key of ``NotificationPresenter._FORMATTERS`` (R13.6, R13.8),
#: which is what makes an unrendered kind a test failure rather than a blank
#: line: the presenter logs and drops a kind it cannot format.
#:
#: The payload each carries, from the design's table:
#:
#: =============================== ==========================================
#: Kind                            Payload
#: =============================== ==========================================
#: ``vector_incoming``             kind, attacker_name, x, y, ticks   (R8.7)
#: ``vector_resolved``             kind, x, y                         (R8.12)
#: ``vector_hit``                  kind, attacker_name, x, y          (R8.12)
#: ``vector_suspended``            kind, reason, x, y                 (R13.6)
#: ``vector_resumed``              kind, ticks_remaining              (R13.6)
#: ``vector_expired``              kind, x, y                         (R8.13)
#: ``vector_cancelled``            kind, reason              (R8.16, R8.17)
#: ``vector_discarded``            kind                               (R14.4)
#: =============================== ==========================================
NOTIFY_VECTOR_INCOMING = "vector_incoming"
NOTIFY_VECTOR_RESOLVED = "vector_resolved"
NOTIFY_VECTOR_HIT = "vector_hit"
NOTIFY_VECTOR_SUSPENDED = "vector_suspended"
NOTIFY_VECTOR_RESUMED = "vector_resumed"
NOTIFY_VECTOR_EXPIRED = "vector_expired"
NOTIFY_VECTOR_CANCELLED = "vector_cancelled"
NOTIFY_VECTOR_DISCARDED = "vector_discarded"

#: The ninth kind this feature introduces, and the one the driver never
#: publishes: R11.8's missing-support-consent refusal, which travels back through
#: the validation chain as ``BranchSystem.may_target``'s refusal key rather than
#: as a notification. It carries the same ``kind`` plus ``ally_name`` payload and
#: the presenter renders it from the same table, so the command layer can hand a
#: refusal straight to the presenter. Held here **by value**, exactly as
#: ``branch_system`` holds the terminal state names by value: the two spellings
#: must stay identical and ``branch_system.MSG_VECTOR_CONSENT_REQUIRED`` is the
#: authority, because that is the module that answers the refusal.
NOTIFY_VECTOR_CONSENT_REQUIRED = "vector_consent_required"

#: Every notification kind this feature introduces (R13.8) — the eight the
#: driver publishes plus the one refusal key the presenter renders as a kind.
#: One tuple so the presenter-coverage guard has a single list to walk: the
#: driver's kinds are emitted through a guarded helper with a *variable* kind, so
#: the AST scan that reads string literals out of a ``self.notify(...)`` call
#: cannot see them and the coverage test reads this instead.
VECTOR_NOTIFICATION_KINDS: tuple[str, ...] = (
    NOTIFY_VECTOR_INCOMING,
    NOTIFY_VECTOR_RESOLVED,
    NOTIFY_VECTOR_HIT,
    NOTIFY_VECTOR_SUSPENDED,
    NOTIFY_VECTOR_RESUMED,
    NOTIFY_VECTOR_EXPIRED,
    NOTIFY_VECTOR_CANCELLED,
    NOTIFY_VECTOR_DISCARDED,
    NOTIFY_VECTOR_CONSENT_REQUIRED,
)

#: ``reason`` values on a :data:`NOTIFY_VECTOR_SUSPENDED` notification, naming
#: which of R8.14's and R8.18's two causes paused the operation: the
#: Carrier_Agent is incapacitated or in reserve, or the owner lost the
#: Branch_Commitment the operation requires on its planet. Declared here rather
#: than at the transition that emits them (the tick advance, task 11.5) so the
#: emitting side and the rendering side name the same two causes.
SUSPEND_CARRIER_UNAVAILABLE = "carrier_unavailable"
SUSPEND_COMMITMENT_LAPSED = "commitment_lapsed"

#: ``reason`` values on a :data:`NOTIFY_VECTOR_CANCELLED` notification, naming
#: which lost collaborator ended the operation: the Carrier_Agent was killed
#: (R8.16), the originating building became non-Operational or was destroyed
#: (R8.17), or a base elimination removed that building outright (R11.4).
CANCEL_CARRIER_KILLED = "carrier_killed"
CANCEL_ORIGIN_LOST = "origin_lost"
CANCEL_BASE_ELIMINATED = "base_eliminated"


class OperationState(StrEnum):
    """Vector_Operation lifecycle states (R8.1).

    ``StrEnum`` so a persisted value round-trips as a plain string through an
    Evennia attribute: a member compares and hashes equal to its value
    (``OperationState.PENDING == "pending"``), serializes to that string, and a
    plain string read back out of storage is usable everywhere a member is.

    The six, and what each means:

    * ``PENDING`` — accepted, charged, and advancing on the tick loop.
    * ``SUSPENDED`` — paused because the Carrier_Agent is incapacitated or in
      reserve, or because the owner lost the required Branch_Commitment (R8.14,
      R8.18). Resumes with the ticks it held on suspension (R8.15).
    * ``RESOLVED`` — its effect was applied (R8.11).
    * ``EXPIRED`` — its bounded lifetime elapsed before it took effect (R8.13).
    * ``CANCELLED`` — ended by a lost collaborator: the Carrier_Agent was
      killed, or the originating building was lost (R8.16, R8.17, R11.4).
    * ``DISCARDED`` — a rebuild found a reference that no longer exists (R14.4).
    """

    PENDING = "pending"
    SUSPENDED = "suspended"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DISCARDED = "discarded"


#: The four states R8.2 declares TERMINAL: an operation in one of these advances
#: no further, and the single writer of ``record.state`` refuses to move it.
#: Held as a ``frozenset`` of members which — because ``OperationState`` is a
#: ``StrEnum`` — also answers membership for the equivalent plain strings, so a
#: record read straight out of persistence tests against it without conversion.
TERMINAL_STATES: frozenset[OperationState] = frozenset({
    OperationState.RESOLVED,
    OperationState.EXPIRED,
    OperationState.CANCELLED,
    OperationState.DISCARDED,
})

#: Lifecycle state -> the notification kind that reports reaching it (R13.6).
#: R13.6 asks the presenter to render a kind for **each** of the six states, so
#: the mapping is declared here — keyed off the enum itself — rather than being
#: implied by which helpers happen to exist: a seventh state added without a kind,
#: or a kind renamed on one side only, breaks the coverage guard instead of
#: leaving one transition silent.
#:
#: ``vector_resumed`` and ``vector_hit`` are deliberately absent: resuming
#: returns an operation to ``pending``, whose *own* kind is the incoming warning
#: the target already had, and ``vector_hit`` is the recipient's view of the same
#: ``resolved`` transition the owner reads as ``vector_resolved``.
STATE_NOTIFICATIONS: dict[str, str] = {
    str(OperationState.PENDING): NOTIFY_VECTOR_INCOMING,
    str(OperationState.SUSPENDED): NOTIFY_VECTOR_SUSPENDED,
    str(OperationState.RESOLVED): NOTIFY_VECTOR_RESOLVED,
    str(OperationState.EXPIRED): NOTIFY_VECTOR_EXPIRED,
    str(OperationState.CANCELLED): NOTIFY_VECTOR_CANCELLED,
    str(OperationState.DISCARDED): NOTIFY_VECTOR_DISCARDED,
}


def new_op_id() -> str:
    """Return a fresh Operation_Record identity: a uuid4 hex string.

    ``op_id`` is the identity a restart rebuild's idempotence rests on (R14.3) —
    the rebuild keys its tracked map by it, so a dict cannot hold a duplicate.
    Minted here rather than inline so the driver, a vector's ``build_record``,
    and the tests all mint identities the same way.
    """
    return uuid.uuid4().hex


def _as_str(value: Any) -> str | None:
    """Coerce *value* to a plain string; ``None`` for an absent one. No raise."""
    if value is None:
        return None
    if isinstance(value, str):
        return str(value)  # collapses a StrEnum member to its plain value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - an unrenderable value is an absent one
        return None


def _as_name(value: Any) -> str | None:
    """Return *value* as a non-empty stripped string, or ``None``. No raise.

    The identity normalizer for the *names* the validation chain compares —
    a Branch, an Operation_Kind, an agent role, a technology key, a message key
    — as distinct from :func:`_as_str`, which coerces whatever it is handed
    because a persisted record field has to read back as *something*. Mirrors
    ``BranchSystem._clean`` deliberately: a blank name and an absent one collapse
    to the same value on both sides of the conversation between the two modules,
    so neither can compare ``""`` against ``None`` and call it a difference.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_opt_int(value: Any) -> int | None:
    """Coerce *value* to an int; ``None`` when it cannot be read as one."""
    if value is None:
        return None
    if isinstance(value, int):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_int(value: Any, default: int) -> int:
    """Coerce *value* to an int, falling back to *default*. No raise."""
    read = _as_opt_int(value)
    return default if read is None else read


def _as_float(value: Any, default: float) -> float:
    """Coerce *value* to a float, falling back to *default*. No raise."""
    if value is None:
        return default
    if isinstance(value, float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_ref_id(value: Any) -> int | None:
    """Return the database id *value* spells, or ``None``. No raise.

    The one reading of a *resolvable reference* (design §7) this module makes: an
    Operation_Record holds an owner, a building, a carrier, and a target as
    values so the whole record survives a restart as a plain dict, and the
    restart rebuild has to turn those values back into live objects. The three
    spellings are the ones ``BranchSystem._owner_matches`` already resolves — a
    plain database id, the ``"#5"`` dbref string naming the same id, and the
    ``"5"`` a hand-edited attribute may hold.

    ``bool`` is excluded deliberately: ``True`` is an ``int`` in Python and
    object ``#1`` is not what a flag meant. Anything else — a live object, a
    coordinate tuple, a name — answers ``None``, which the rebuild reads as "this
    reference cannot be read as an id" and therefore judges nothing about.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    text = _as_name(value)
    if text is None:
        return None
    if text.startswith("#"):
        text = text[1:].strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _as_cost_map(value: Any) -> dict[str, int]:
    """Coerce *value* to a ``{resource: amount}`` map. No raise.

    A fresh dict every time, so a record never shares a container with the
    payload it was read from — an in-place mutation of one must not reach the
    other (R14.7). An entry whose amount cannot be read as an int is dropped
    rather than guessed at: a refund must not invent an amount.
    """
    if not isinstance(value, Mapping):
        return {}
    coerced: dict[str, int] = {}
    for res, amount in value.items():
        parsed = _as_opt_int(amount)
        resource = _as_str(res)
        if parsed is None or not resource:
            continue
        coerced[resource] = parsed
    return coerced


def _reach(entity: Any, name: str) -> Any:
    """Return ``entity.name``, or ``None`` when reading it is not possible.

    The guarded ``getattr`` the notification points reach the world through
    (R15.1): every world object the driver touches at a notification point — a
    room, a tile occupant, an affected entity — is reached duck-typed, by
    attribute name, and a stale reference, a deleted object, or a property that
    raises answers ``None`` rather than breaking the transition that asked.
    """
    if entity is None:
        return None
    try:
        return getattr(entity, name, None)
    except Exception:  # noqa: BLE001 - an unreadable attribute is an absent one
        return None


@dataclass
class OperationRecord:
    """The persisted description of one Vector_Operation (R8.21, R14.1).

    Stored as a plain dict under the ``vector_operations`` attribute of the
    durable owner the vector nominates — the world object the operation acts
    through (a placed trap object, a convoy object) or the entity it is attached
    to (an intruded building, an infected agent). Every field is a value or a
    reference to one world entity, never an object graph — and the four
    ``*_ref`` fields should hold the **live object** while the process runs,
    because the lifecycle's death, loss, and notification reads judge live
    objects and the attribute layer persists an object reference as its dbref
    (see the module docstring's reference invariant).

    Mutable, unlike the frozen :class:`OperationOutcome`: the tick advance
    decrements the two clocks in place, and the driver's single state writer
    reassigns :attr:`state`. Every reader outside the driver reads by value.

    Each field carries its **documented default** (R14.8) — the value
    :meth:`from_dict` falls back to for an absent or unreadable one, which makes
    ``OperationRecord()`` the all-defaults record. The one non-constant default
    is :attr:`op_id`, which mints a fresh identity so two id-less records can
    never collide into a single tracked entry.
    """

    #: Identity across a rebuild, and the key the rebuild's idempotence rests
    #: on (R14.3). Default: a fresh :func:`new_op_id`.
    op_id: str = field(default_factory=new_op_id)
    #: The Operation_Kind — which Signature_Vector this instantiates; one of
    #: ``world.constants.OPERATION_KINDS``. Default: ``""``.
    kind: str = ""
    #: The owning player, as a reference resolved lazily. Default: ``None``.
    owner_ref: Any = None
    #: The originating Branch_Building, as a reference. Default: ``None``.
    building_ref: Any = None
    #: The Carrier_Agent, as a reference. Default: ``None``.
    carrier_ref: Any = None
    #: The planet this operation lives on. Default: ``None``.
    planet: str | None = None
    #: Target coordinate; ``None`` where the vector targets an entity rather
    #: than a tile. Default: ``None``.
    target_x: int | None = None
    target_y: int | None = None
    #: The target entity, as a reference, where the vector has one.
    #: Default: ``None``.
    target_ref: Any = None
    #: Ticks until the effect applies (R8.11), floored for a hostile operation
    #: at the Response_Window minimum (R8.8). Default: ``0``.
    ticks_remaining: int = 0
    #: Ticks of bounded lifetime left. ``None`` means no bounded lifetime, so
    #: R8.13's expiry does not apply to this operation. Default: ``None``.
    lifetime_remaining: int | None = None
    #: Effect magnitude. Default: ``0.0``.
    magnitude: float = 0.0
    #: Effect radius in tiles. Default: ``0``.
    radius: int = 0
    #: The lifecycle state: an :class:`OperationState` value. Default:
    #: ``"pending"`` — a record with no readable state is treated as in flight,
    #: the same conservative direction ``BranchSystem`` takes when it cannot
    #: read one, so a hand-edited record is picked up by the rebuild and judged
    #: by the normal lifecycle instead of being silently dropped.
    state: str = OperationState.PENDING
    #: Ticks held at suspension, restored on resume so a suspension delays an
    #: operation rather than restarting it (R8.15). ``None`` while the operation
    #: has never been suspended. Default: ``None``.
    suspended_ticks: int | None = None
    #: What the request charged, kept for the R8.6 refund. Default: ``{}``.
    charged: dict[str, int] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """True when this record's state is one of the four terminal states.

        Judged on the state's **value** (R8.2), so a plain string persisted by
        an older build answers the same as an :class:`OperationState` member.
        """
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        """Return the plain-dict payload this record persists as.

        Plain values only: :attr:`state` collapses to its string so a
        ``StrEnum`` member never reaches storage as an enum, and :attr:`charged`
        is copied so the stored payload and the live record share no container
        (R14.7). The four references pass through as they are — a live world
        object is serialized to its dbref by the attribute layer on the way
        into storage, and a plain id or dbref string is already a value.
        """
        return {
            "op_id": self.op_id,
            "kind": self.kind,
            "owner_ref": self.owner_ref,
            "building_ref": self.building_ref,
            "carrier_ref": self.carrier_ref,
            "planet": self.planet,
            "target_x": self.target_x,
            "target_y": self.target_y,
            "target_ref": self.target_ref,
            "ticks_remaining": self.ticks_remaining,
            "lifetime_remaining": self.lifetime_remaining,
            "magnitude": self.magnitude,
            "radius": self.radius,
            "state": str(self.state),
            "suspended_ticks": self.suspended_ticks,
            "charged": dict(self.charged),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OperationRecord:
        """Rebuild a record from its persisted payload (R14.8).

        Reads **each** field by value and treats an absent — or unreadable —
        value as the documented default for that field, which is the field
        default declared above. So a partial record (written by an older build,
        hand-edited by an admin, truncated by a failed write) reads back as a
        fully populated record, and this method raises nothing on one. Paired
        with :meth:`to_dict` it is the identity on the persisted fields, which is
        what the round-trip property rests on.

        Args:
            data: The persisted payload — any mapping. A non-mapping is not a
                partial record but a corrupt one, and is the one input that
                raises: the rebuild loop catches it, logs it, and recovers the
                remaining records (R14.5), which it could not do if a corrupt
                payload were read as an empty operation instead.

        Raises:
            TypeError: If *data* is not a mapping.
        """
        if not isinstance(data, Mapping):
            raise TypeError(
                "OperationRecord.from_dict expects a mapping, got "
                f"{type(data).__name__}"
            )
        return cls(
            op_id=_as_str(data.get("op_id")) or new_op_id(),
            kind=_as_str(data.get("kind")) or "",
            owner_ref=data.get("owner_ref"),
            building_ref=data.get("building_ref"),
            carrier_ref=data.get("carrier_ref"),
            planet=_as_str(data.get("planet")),
            target_x=_as_opt_int(data.get("target_x")),
            target_y=_as_opt_int(data.get("target_y")),
            target_ref=data.get("target_ref"),
            ticks_remaining=_as_int(data.get("ticks_remaining"), 0),
            lifetime_remaining=_as_opt_int(data.get("lifetime_remaining")),
            magnitude=_as_float(data.get("magnitude"), 0.0),
            radius=_as_int(data.get("radius"), 0),
            state=_as_str(data.get("state")) or str(OperationState.PENDING),
            suspended_ticks=_as_opt_int(data.get("suspended_ticks")),
            charged=_as_cost_map(data.get("charged")),
        )


@dataclass(frozen=True)
class OperationOutcome:
    """What a Vector_Operation request answers with (R8.24, R15.3).

    Every request returns one of these for every input — the driver raises
    nothing into a caller — and it names the resulting lifecycle state, or the
    check that refused the request, so a caller reads the result rather than
    inferring it from a side effect.

    Frozen because an outcome is a *report* of something that already happened:
    nothing may rewrite a decision after the fact.

    Attributes:
        ok: Whether the request was accepted.
        state: The resulting lifecycle state — an :class:`OperationState` value
            — when an operation exists; ``None`` for a refusal or a failure,
            which create none.
        check: The name of the check that refused the request (one of
            ``OperationDriver._CHECK_ORDER``), or the point that failed;
            ``None`` on acceptance.
        detail: The structured data the refusal reports — the value required to
            pass the failing check (R8.4). A message key plus data, never
            composed prose: the presenter owns every player-facing word (R13.5).
        op_id: The accepted operation's identity; ``None`` when none exists.
    """

    ok: bool
    state: str | None = None
    check: str | None = None
    detail: dict | None = None
    op_id: str | None = None

    @classmethod
    def accepted(cls, record: OperationRecord) -> OperationOutcome:
        """The request passed every check, was charged, and *record* entered its
        state — normally Pending (R8.5). Reports that state and the identity.

        Reads *record* defensively so an outcome can always be built: the
        request path must answer a value even when handed something odd.
        """
        return cls(
            ok=True,
            state=_as_str(getattr(record, "state", None))
            or str(OperationState.PENDING),
            op_id=_as_str(getattr(record, "op_id", None)),
        )

    @classmethod
    def refused(cls, check: str, detail: dict | None = None) -> OperationOutcome:
        """A check refused the request (R8.4).

        Names the failing check and carries the value required to pass it. No
        operation exists, so there is no state to report, and by contract every
        player-owned and world-owned state is left unchanged.
        """
        return cls(
            ok=False,
            check=_as_str(check) or "",
            detail=dict(detail) if isinstance(detail, Mapping) else None,
        )

    @classmethod
    def failed(cls, check: str, detail: dict | None = None) -> OperationOutcome:
        """The request passed every check but could not become an operation.

        The one path that reaches here is a failure entering Pending *after* the
        cost was charged, which the driver answers by refunding the whole
        charged amount before returning this (R8.6) — so a failure, like a
        refusal, leaves the player's resources exactly as they were.
        """
        return cls(
            ok=False,
            check=_as_str(check) or "",
            detail=dict(detail) if isinstance(detail, Mapping) else None,
        )


@dataclass
class OperationContext:
    """One request, as it walks the ordered validation chain — the ``ctx``.

    Built by :meth:`OperationDriver._build_context` from the requesting player
    and the keyword parameters the command layer passed, then handed to every
    check in turn and finally to the vector's ``validate_target`` and
    ``build_record`` hooks. It is deliberately **mutable**: each check resolves
    what it needed and leaves the answer here, so the chain resolves the planet
    once, the originating building once, and the Carrier_Agent once, and the
    hooks read those answers rather than repeating the lookups.

    Nothing on it is persisted and nothing outside one request reads it — it is
    the working surface of a single call, which is what makes "a refusal changes
    nothing" (R8.4) easy to see: every write a refused request performs lands
    here, and this object is discarded with the refusal.

    Attributes:
        player: The requesting player, or an NPC base's Sentinel.
        params: The request's own keyword parameters, by value. The driver reads
            the six documented below; every other key is the vector's, passed
            through untouched for its hooks.
        planet: The planet the operation happens on, from ``params["planet"]``,
            else the originating building's, else the player's. ``None`` is the
            "any planet" wildcard the Branch services already document.
        building: The originating Branch_Building, from ``params["building"]``.
        carrier: The eligible Carrier_Agent the ``carrier`` check resolved.
        role: The agent role that carrier had to hold (R7.2, R7.3).
        target: The target entity, from ``params["target"]``.
        target_x: Target coordinate, from ``params["x"]``.
        target_y: Target coordinate, from ``params["y"]``.
        hostile: Whether the operation is aimed *at* the target rather than
            performed in support of them, from ``params["hostile"]``. Defaults to
            ``True``, the stricter reading, so a caller that forgets gets the
            protection gates rather than skipping them.
        cost: The Operation_Kind's per-use resource cost, resolved by the
            ``resources`` check and charged (once) by the acceptance half.
    """

    player: Any = None
    params: dict[str, Any] = field(default_factory=dict)
    planet: Any = None
    building: Any = None
    carrier: Any = None
    role: str | None = None
    target: Any = None
    target_x: int | None = None
    target_y: int | None = None
    hostile: bool = True
    cost: dict[str, int] = field(default_factory=dict)

    def param(self, name: str, default: Any = None) -> Any:
        """Return one request parameter by name, or *default*. No raise."""
        try:
            return self.params.get(name, default)
        except Exception:  # noqa: BLE001 - an unreadable params map has none
            return default


# ------------------------------------------------------------------ #
#  Persistence: the read-copy-write pair
# ------------------------------------------------------------------ #
#
# The two functions ``OperationDriver`` persists through, and the only code in
# this feature that touches the ``vector_operations`` attribute
# (:data:`world.constants.ATTR_VECTOR_OPERATIONS`). Module-level rather than
# methods because they need nothing from a driver: given an owner they are a pure
# function of that owner's attribute, which is what lets a vector's
# ``discover_records`` sweep, a rebuild, and a test all use the same pair. The
# driver binds them as ``self._read_records`` / ``self._write_records``.
#
# Why the discipline (R14.7). An Evennia attribute hands out the stored container
# itself, and does not observe an in-place change to it: mutating the list a read
# returned may or may not survive the process, which is the worst of both. So
# every write REPLACES the whole container, and every read is BY VALUE — the
# caller gets containers it owns, mutates those, and writes the whole list back.
#
# Why an owner is optional. The durable owner is the vector's choice (R14.1) and
# a vector may nominate a world object that has since been deleted, or none at
# all. Neither is an error worth raising into a tick: a missing owner reads as no
# records and writes nothing, so a lost owner loses its own operations and
# nothing else.


def _owner_attributes(owner: Any) -> Any:
    """Return *owner*'s attribute handler, or ``None`` when it has none.

    The tolerance both halves of the pair share: ``None``, a fake without the
    handler, and an object whose ``attributes`` is itself ``None`` all answer
    ``None``, so the caller degrades to "no records" rather than raising.
    """
    if owner is None:
        return None
    try:
        return getattr(owner, "attributes", None)
    except Exception:  # noqa: BLE001 - a property that raises is no handler
        return None


def _read_records(owner: Any) -> list[dict[str, Any]]:
    """Return *owner*'s persisted Operation_Record payloads, by value (R14.8).

    The read half of the read-copy-write discipline. Every container in the
    result is freshly built, so the caller may mutate what it gets without the
    change reaching — or failing to reach — storage. Raw dicts, not
    :class:`OperationRecord` objects: the rebuild is what decides which payloads
    become records, because it is the only thing that knows what to do with the
    ones that cannot (R14.5).

    Its **documented default is an empty list**: an owner with no attribute
    handler, an absent attribute, an empty one, and a hand-edited value that is
    not a list of records at all all read as ``[]``. And a single corrupt entry
    inside an otherwise readable list is logged and skipped rather than sinking
    the read, so one bad record costs one record — the same "recover the rest"
    posture the rebuild takes (R14.5).

    Args:
        owner: The durable owner a vector nominated (R14.1), or ``None``.

    Returns:
        One plain dict per readable record, in stored order.
    """
    handler = _owner_attributes(owner)
    if handler is None:
        return []
    try:
        stored = handler.get(ATTR_VECTOR_OPERATIONS, default=[])
    except Exception:  # noqa: BLE001 - an unreadable attribute is an absent one
        logger.debug(
            "operation records could not be read from %r", owner, exc_info=True
        )
        return []
    if not stored:
        return []  # absent, or an empty container: the documented default
    if isinstance(stored, (str, bytes, Mapping)) or not isinstance(stored, Iterable):
        logger.warning(
            "operation records on %r are a %s, not a list of records — reading "
            "none; the next write replaces the container",
            owner, type(stored).__name__,
        )
        return []
    try:
        entries = list(stored)
    except Exception:  # noqa: BLE001 - an uniterable container holds no records
        logger.warning(
            "operation records on %r could not be iterated — reading none",
            owner, exc_info=True,
        )
        return []
    records: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, Mapping):
            records.append(dict(entry))  # by value, one fresh dict per record
            continue
        logger.warning(
            "operation record %d on %r is a %s, not a record — skipping it and "
            "recovering the rest",
            index, owner, type(entry).__name__,
        )
    return records


def _write_records(owner: Any, records: Iterable[Any]) -> None:
    """Replace *owner*'s whole persisted record container with *records* (R14.7).

    The write half: the container is written **wholesale**, never mutated in
    place, which is the only way an Evennia attribute write is guaranteed to
    persist. Writing an empty sequence is the legitimate way to clear the
    container — the last record going terminal leaves an owner with none — so
    this always writes rather than skipping an empty list.

    Each entry is reduced to a plain dict on the way in: an
    :class:`OperationRecord` is passed through :meth:`OperationRecord.to_dict`,
    a mapping is copied, and anything else is logged and dropped rather than
    stored. So storage holds values only, never a dataclass or an enum, and the
    stored payload shares no container with the live record (R14.7).

    Answers nothing and raises nothing: an owner with no attribute handler, and a
    handler whose write fails, are both logged and shrugged off, because a failed
    persist must not break the tick that triggered it (R15.3).

    Args:
        owner: The durable owner a vector nominated (R14.1), or ``None``.
        records: The records to persist, as :class:`OperationRecord` objects or
            as the plain dicts they persist as.
    """
    handler = _owner_attributes(owner)
    if handler is None:
        return
    try:
        handler.add(ATTR_VECTOR_OPERATIONS, _records_payload(records, owner))
    except Exception:  # noqa: BLE001 - a failed write never breaks a tick
        logger.debug(
            "operation records could not be written to %r", owner, exc_info=True
        )


def _records_payload(records: Iterable[Any], owner: Any = None) -> list[dict[str, Any]]:
    """Return *records* as the plain list of dicts that goes into storage.

    A fresh list of fresh dicts, so the stored container is nobody else's: the
    caller keeps its own list and its own records, and neither can reach the
    stored value by mutation afterwards.
    """
    if records is None:
        return []
    if isinstance(records, (str, bytes, Mapping)):
        logger.warning(
            "refusing to persist a %s as the operation records of %r — writing "
            "an empty container instead",
            type(records).__name__, owner,
        )
        return []
    try:
        entries = list(records)
    except Exception:  # noqa: BLE001 - an uniterable argument holds no records
        logger.warning(
            "the operation records offered for %r could not be iterated — "
            "writing an empty container instead",
            owner, exc_info=True,
        )
        return []
    payload: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        to_dict = getattr(entry, "to_dict", None)
        if callable(to_dict):
            try:
                entry = to_dict()
            except Exception:  # noqa: BLE001 - a record that cannot serialize
                logger.warning(
                    "operation record %d for %r could not be serialized — "
                    "dropping it and persisting the rest", index, owner,
                    exc_info=True,
                )
                continue
        if isinstance(entry, Mapping):
            payload.append(dict(entry))
            continue
        logger.warning(
            "operation record %d for %r is a %s, not a record — dropping it and "
            "persisting the rest",
            index, owner, type(entry).__name__,
        )
    return payload


# ------------------------------------------------------------------ #
#  OperationDriver — the framework half of a Vector_System
# ------------------------------------------------------------------ #

#: The six state names, by value, as the only spellings :meth:`OperationDriver.
#: _transition` will write. Derived from :class:`OperationState` so the guard and
#: the vocabulary cannot disagree: R8.1 says an operation is recorded in one of
#: *those* states, and a transition to anything else is a coding error the single
#: writer declines rather than persists.
_STATE_NAMES: frozenset[str] = frozenset(str(state) for state in OperationState)

#: "Nobody could answer that at all", as distinct from an answer of ``None``.
#: Two places need the distinction, and both need it for the same reason — the
#: driver never destroys an operation over a read it could not make:
#:
#: * :meth:`OperationDriver._ask`, where "this owner holds no Branch_Commitment"
#:   is R8.18's suspension trigger while "nobody could say what this owner holds"
#:   must pause nothing, and the two collapse into one answer without a sentinel
#:   to tell them apart;
#: * the restart rebuild's reference resolution, where "this reference points at
#:   something that no longer exists" is R14.4's discard while "there was nothing
#:   to look it up in" must discard nothing.
_UNREADABLE: Any = object()

#: Check name -> the message KEY a refusal of that check carries when the check
#: named none itself. Every one of :data:`OperationDriver._CHECK_ORDER` has an
#: entry, so a check that could not run at all — missing from a subclass, or
#: raising — still refuses with a key a presenter can render rather than with a
#: blank (R13.5). The ``target`` entry is a fallback in the ordinary case too: a
#: target refusal normally carries the key the vector's own hook or
#: ``BranchSystem.may_target`` answered, which is what names *which* of the four
#: protection gates fired.
_CHECK_MESSAGES: dict[str, str] = {
    "collaborators": MSG_VECTOR_UNWIRED,
    "commitment": MSG_VECTOR_COMMITMENT_REQUIRED,
    "origin": MSG_VECTOR_ORIGIN_UNAVAILABLE,
    "unlock": MSG_VECTOR_UNLOCK_REQUIRED,
    "carrier": MSG_VECTOR_CARRIER_REQUIRED,
    "target": MSG_VECTOR_TARGET_INVALID,
    "cooldown": MSG_VECTOR_COOLDOWN,
    "in_flight": MSG_VECTOR_IN_FLIGHT_CAP,
    "resources": MSG_VECTOR_INSUFFICIENT_RESOURCES,
}


class OperationDriver:
    """Framework half of a Vector_System. A vector subclasses this.

    One lifecycle, implemented once. A Signature_Vector spec supplies five hooks
    — target validity, the record, the effect, the durable owner, and the record
    sweep — and inherits the ordered validation chain, the refusal shape, charge
    and refund, the notification points, the Response_Window floor, the tick
    advance and its isolation, suspend/resume, every cancellation trigger, the
    cooldown/in-flight/escalation ledgers, persistence, and the restart rebuild.
    The driver owns the control flow and calls the hooks from inside it, so no
    vector can relax any of it (design §4.10).

    **A mixin, not a base system.** The design composes a vector as
    ``class OrdnanceSystem(OperationDriver, BaseSystem)`` — this class comes
    first in the MRO and :class:`~world.systems.base_system.BaseSystem` supplies
    ``registry``, ``event_bus``, and ``notify``. It cannot inherit from
    ``BaseSystem`` itself: that module reaches the framework, and this one must
    import with ``evennia`` absent (R15.1). So :meth:`__init__` is *cooperative*
    — it takes the driver's own collaborator as a keyword argument, consumes it,
    and passes every remaining argument along the MRO, which is what lets a
    vector write one ``super().__init__(registry, event_bus, ...)`` call.

    **Registration is duck-typed.** ``BranchSystem.register_vector`` keys on
    :attr:`operation_kind` and the in-flight cap counts
    :meth:`tracked_records`; the tick fan-out asks for ``advance_all``. Nothing
    in that path imports this class — the dependency runs the other way, a
    vector consumes Branch services and the Branch system never consumes the
    contract — so a subclass satisfies it by exposing those names and nothing
    more.

    Class attributes a subclass sets:
        operation_kind: The Operation_Kind this vector owns — one of
            :data:`world.constants.OPERATION_KINDS`, and the key
            ``register_vector`` files it under. Also the stem of its
            Balance_Config field names (``<kind>_cost``, ``<kind>_cooldown_ticks``,
            …). Defaults to ``""``, which registration reads as "names no kind"
            and skips with a log rather than raising, so one mis-declared vector
            does not stop a composition root from wiring the others.
        branch: The Branch this vector's Signature_Vector belongs to — one of
            :data:`world.constants.BRANCHES` — and therefore the
            Branch_Commitment a request must match. Defaults to ``""``.
        _required_collaborators: The names of the collaborators this vector
            cannot operate without, declared so the first check of the chain can
            *degrade* an unwired system to a refusal naming the missing one
            instead of raising deep inside a request (R15.2). Each name is read
            off the instance, both plainly and with the private ``_`` prefix
            this codebase stores injected collaborators under, so
            ``("combat_engine",)`` matches ``self.combat_engine`` or
            ``self._combat_engine``. The declaration is the whole of a vector's
            obligation here; the checking is the driver's.

    Args:
        *args: Passed along the MRO untouched — in the composed shape, the
            ``registry`` and ``event_bus`` ``BaseSystem`` takes.
        branch_system: The :class:`~world.systems.branch_system.BranchSystem`,
            source of every service the six vectors share rather than
            reimplement: commitment, carrier eligibility, cooldown, in-flight
            cap, escalation cap, Counter_Web, and charge/refund (R15.8).
            Optional, like every collaborator in this codebase, so a driver is
            constructible from a bare registry and event bus in a test and an
            unwired deployment degrades to a refusal rather than an error
            (R15.2).
        **kwargs: Passed along the MRO untouched.
    """

    #: The Operation_Kind this vector owns. A subclass sets it.
    operation_kind: str = ""
    #: The Branch this vector's Signature_Vector belongs to. A subclass sets it.
    branch: str = ""
    #: Collaborator names the first check of the chain degrades on (R15.2).
    _required_collaborators: tuple[str, ...] = ()

    #: The nine checks a request runs, **in the order R8.3 fixes** (design §4.2).
    #: :meth:`request` walks this tuple and calls ``self._check_<name>(ctx)`` for
    #: each, refusing at the first one that answers, so this tuple *is* the
    #: order — there is no second list of checks anywhere and no check runs
    #: except through here.
    #:
    #: The order is not arbitrary. Cheap identity checks precede expensive world
    #: queries; refusals a player can act on immediately (wire the system, build
    #: the lab, research the technology, train the spotter) precede refusals that
    #: depend on timing (cooldown, cap); and resource sufficiency is **last**, so
    #: a player blocked for a structural reason hears the structural reason
    #: rather than "not enough Iron".
    #:
    #: ``branch_strategies.OPERATION_CHECK_ORDER`` is a by-value copy for the
    #: generators, written from the design because the strategies landed before
    #: this tuple did; **this tuple is the authority** and the driver's unit tests
    #: cross-check the two so neither can drift.
    _CHECK_ORDER: tuple[str, ...] = (
        "collaborators",      # R15.2  — an unwired system degrades to a refusal
        "commitment",         # R8.3   — Branch_Commitment matches this vector's Branch
        "origin",             # R8.3   — building owned, Operational, Active_HQ_Rule
        "unlock",             # R6.6   — the originating building's unlock technology
        "carrier",            # R7.3   — an eligible Carrier_Agent of the right role
        "target",             # R8.3   — vector-supplied validity + R10.4/R10.6/R11.9
        "cooldown",           # R8.19
        "in_flight",          # R8.20
        "resources",          # R12.3  — sufficiency; the charge happens after
    )

    def __init__(self, *args: Any, branch_system: Any = None, **kwargs: Any) -> None:
        #: Every non-terminal Operation_Record this vector is advancing (R8.21).
        #: The list *is* the in-flight count (R8.20) — there is no separate
        #: ledger — and it is rebuilt from persistence at server start (R8.22),
        #: so it is a live index of durable state rather than cached state.
        self._tracked: list[OperationRecord] = []
        self._branch: Any = branch_system
        super().__init__(*args, **kwargs)
        # After the MRO call, because the event bus belongs to ``BaseSystem``:
        # a bare driver in a test has none and subscribes to nothing.
        self._subscribe_lifecycle_events(_reach(self, "event_bus"))

    # The persistence pair, bound so the driver reaches storage as
    # ``self._read_records`` / ``self._write_records``. Module-level functions
    # because they need nothing from a driver — given an owner they are a pure
    # function of that owner's attribute — and ``staticmethod`` because binding
    # them here is what keeps the vector-facing spelling uniform: a vector's
    # ``discover_records`` sweep, the rebuild, and a test all reach the same
    # single implementation, and there is still exactly one code path that
    # touches ``vector_operations``.
    _read_records = staticmethod(_read_records)
    _write_records = staticmethod(_write_records)

    # ------------------------------------------------------------------ #
    #  Hooks a vector supplies — the five required
    # ------------------------------------------------------------------ #

    def validate_target(self, ctx: Any) -> str | None:
        """Return why *ctx*'s target is invalid for this vector, or ``None``.

        Called from ``_check_target``, which folds this vector-specific answer
        together with the protection gates every vector shares — the new-player
        shield (R10.4), the allied-target refusal (R11.9), and the escalation
        cap (R10.6) — so a vector answers only the question no other vector can:
        is *this* the sort of thing *this* operation may be aimed at.

        Returns a refusal **key plus structured data, never composed prose**
        (R13.5): the player-facing wording lives in the
        :class:`~world.presenters.notification_presenter.NotificationPresenter`.

        Args:
            ctx: The request context the driver built.

        Returns:
            ``None`` when the target is valid; otherwise the refusal.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement validate_target(ctx)"
        )

    def build_record(self, ctx: Any) -> OperationRecord:
        """Return the :class:`OperationRecord` describing *ctx*'s operation.

        Called once, after every check has passed and the cost has been charged.
        The vector fills the fields only it knows — the magnitude, the radius,
        the bounded lifetime, the target — and the driver owns the rest: it
        stamps the charge for the refund path, floors a hostile
        Response_Window (R8.8), moves the record into Pending through the single
        state writer (R8.5), tracks it, and persists it.

        For the four ``*_ref`` fields, hand over the **live world object** —
        the owner, the building, the carrier the chain resolved onto the
        context, the target. The lifecycle's carrier-death (R8.16),
        origin-loss (R8.17), and notification reads all judge live objects and
        deliberately conclude nothing from a bare reference, the attribute
        layer persists an object reference as its dbref, and the restart
        rebuild resolves it back to the object (R8.22) — so a vector that
        stores an id instead has built an operation those triggers cannot see
        until a restart re-lives it. Every *other* field must be a plain value
        (a coordinate, a count, a name), never a container of objects, because
        the whole record has to survive a restart as a plain dict (R14.1).

        Args:
            ctx: The request context the driver built.

        Returns:
            A fresh record. Raising instead is the R8.6 path: the driver refunds
            the whole charged amount and answers ``failed``, so an operation
            never both charges and fails.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement build_record(ctx)"
        )

    def on_resolve(self, record: OperationRecord) -> None:
        """Apply *record*'s effect. The one hook that changes the world (R8.11).

        Called when the effect clock reaches zero, immediately before the driver
        moves the record to Resolved and notifies the affected players (R8.12).

        Every damage source routes through ``CombatEngine.apply_direct_hit`` or
        an append to the existing ``db.active_effects`` list, attributed to
        ``record.owner_ref`` (R8.23, R10.3). That is not a style preference: the
        chip-damage floor, the typed-resist axes, the permanent-bonus caps,
        shield absorption, the rank-gap damage damper, and the rank-gap XP/loot
        reduction all live inside that path, so routing through it is what makes
        R9.11 and R10.1–10.3 inherited rather than reimplemented. The driver
        offers no other way to deal damage.

        Args:
            record: The resolving operation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement on_resolve(record)"
        )

    def persistence_owner(self, record: OperationRecord) -> Any:
        """Return the durable object *record* persists on, or ``None`` (R14.1).

        The vector's choice, because only the vector knows whether its operation
        *has* a world object: the thing it acts through (a placed trap, a
        convoy) or the entity it is attached to (an intruded building, an
        infected agent). The driver requires only that the answer has an
        ``attributes`` handler — it is never inspected further, and a ``None``
        owner is a supported answer that simply persists nothing.

        Args:
            record: The operation whose owner to name.

        Returns:
            The durable owner, or ``None`` when the operation has none — or has
            lost it, which costs that operation its persistence and nothing
            else.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement persistence_owner(record)"
        )

    def discover_records(self, planet_rooms: Any) -> Iterable[Any]:
        """Yield the durable owners that may hold this vector's records (R8.22).

        The restart rebuild's sweep, and the inverse of
        :meth:`persistence_owner`: the driver asks where to look, reads each
        owner's container through the persistence pair, and decides which
        payloads become tracked operations.

        Args:
            planet_rooms: The world the rebuild walks, in whatever shape the
                composition root hands it.

        Returns:
            An iterable of candidate owners. Yielding an owner that holds no
            records is free — the read answers an empty list.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement discover_records(planet_rooms)"
        )

    # ------------------------------------------------------------------ #
    #  Hooks a vector may supply — the five optional
    # ------------------------------------------------------------------ #
    #
    # Each is called after the driver has already done the framework half of
    # that transition: the state is written, persisted, and notified whether or
    # not a vector overrides the hook. So these are for the effect a vector must
    # *undo* or *finish*, not for the bookkeeping — which is why the default is
    # a no-op rather than a NotImplementedError, and why a vector whose
    # operation leaves no trace behind it overrides none of them.

    def on_expire(self, record: OperationRecord) -> None:
        """*record*'s bounded lifetime elapsed before it took effect (R8.13).

        The place a vector restores each entity it suspended to the state that
        entity held before the suspension — the driver notified the owners, but
        only the vector knows what it paused.
        """

    def on_suspend(self, record: OperationRecord) -> None:
        """*record* paused: carrier lost or commitment lapsed (R8.14, R8.18).

        The remaining ticks are already snapshotted, so this is for pausing a
        vector-side effect, not for saving the clock.
        """

    def on_resume(self, record: OperationRecord) -> None:
        """*record* resumed with the ticks it held on suspension (R8.15).

        Suspension delays an operation rather than restarting it; the driver has
        already restored the clock.
        """

    def on_cancel(self, record: OperationRecord) -> None:
        """*record* ended early: carrier killed, or origin lost (R8.16, R8.17).

        The place a vector releases whatever its operation reserved. The world
        must be left as if the operation had never been placed.
        """

    def on_discard(self, record: OperationRecord) -> None:
        """A rebuild found *record* referring to something gone (R14.4).

        The operation never resumes, and this is the one lifecycle hook that can
        be reached with a reference that will not resolve — so it must tolerate
        a half-missing record.
        """

    # ------------------------------------------------------------------ #
    #  The tracked-record list (R8.20, R8.21)
    # ------------------------------------------------------------------ #

    def tracked_records(self) -> list[OperationRecord]:
        """Return this vector's tracked Operation_Records, by value.

        The accessor ``BranchSystem`` prefers when it counts a player's
        simultaneous operations against the in-flight cap (R8.20) — the tracked
        list *is* that count, so no separate ledger exists to fall out of step
        with it.

        A fresh list, because a caller counting or filtering records must not be
        able to reach the driver's own tracking by mutating what it was handed;
        :meth:`_transition` and the tick advance are the only writers.
        """
        return list(getattr(self, "_tracked", None) or ())

    def _track(self, record: OperationRecord) -> None:
        """Start advancing *record*, replacing any entry with the same identity.

        Keyed by ``op_id`` for the same reason the rebuild is (R14.3): tracking
        an operation twice would double its in-flight count and advance it twice
        per tick, so the identity — not the object — decides what is already
        tracked.
        """
        if record is None:
            return
        op_id = _as_str(getattr(record, "op_id", None))
        tracked = [
            entry for entry in self.tracked_records()
            if op_id is None or _as_str(getattr(entry, "op_id", None)) != op_id
        ]
        tracked.append(record)
        self._tracked = tracked

    def _untrack(self, record: OperationRecord) -> None:
        """Stop advancing *record*, by identity. Untracking twice is harmless."""
        if record is None:
            return
        op_id = _as_str(getattr(record, "op_id", None))
        self._tracked = [
            entry for entry in self.tracked_records()
            if entry is not record
            and (op_id is None or _as_str(getattr(entry, "op_id", None)) != op_id)
        ]

    # ------------------------------------------------------------------ #
    #  The ordered validation chain (R8.3, R8.4)
    # ------------------------------------------------------------------ #

    def request(self, player: Any, **params: Any) -> OperationOutcome:
        """Request a Vector_Operation for *player*. The vectors' one entry point.

        Walks :data:`_CHECK_ORDER` and refuses at the **first** failing check,
        naming that check and carrying the value required to pass it (R8.3,
        R8.4). For one input the chain yields exactly *one* refusal reason — the
        earliest failing check in the declared order — so a caller never has to
        work out which of several problems it is being told about.

        **A refused request changes nothing** (R8.4). No check writes: they are
        reads over the world and over the Branch services, the charge lives in
        the acceptance half that runs only after all nine pass, and the single
        thing the chain does write to is the request context, which is discarded
        with the refusal.

        Args:
            player: The requesting player, or an NPC base's Sentinel — the same
                gates, notifications, and Response_Window apply to both (R11.6).
            **params: The request's parameters. The driver reads ``building``
                (the originating Branch_Building), ``target``, ``x``, ``y``,
                ``hostile``, and ``planet``; every other key rides along on the
                context for the vector's own hooks (see
                :class:`OperationContext`).

        Returns:
            An :class:`OperationOutcome` for **every** input (R8.24, R15.3):
            accepted naming the resulting state, refused naming the failing
            check, or failed. Nothing raises into the caller — a check that
            raises is caught, logged, and answered as a refusal *of that check*,
            because a command layer must read a result rather than guard a call.
        """
        try:
            ctx = self._build_context(player, params)
            for name in self._CHECK_ORDER:
                refusal = self._run_check(name, ctx)
                if refusal is not None:
                    # R8.4: the failing check, the value required to pass it,
                    # and a world nothing has touched.
                    return OperationOutcome.refused(name, refusal)
            return self._accept(ctx)
        except Exception:  # noqa: BLE001 - a request answers, never raises (R15.3)
            logger.exception(
                "%s: request failed outside every guarded step for %r",
                self.operation_kind, player,
            )
            return OperationOutcome.failed("request")

    def _accept(self, ctx: OperationContext) -> OperationOutcome:
        """Charge the cost, then move a new record into Pending (R8.5, R8.6).

        The acceptance half of a request, reached only once **every** check has
        passed. Its shape is the one thing R8.5 and R8.6 fix between them: the
        cost is charged **before** the record enters Pending, and a record that
        then fails to enter Pending gives the **whole** charged amount back — so
        no Vector_Operation both charges and fails, and the only path that ends
        with a player's resources reduced is the one that ends with an operation.

        The order, and why each step is where it is:

        1. **Charge** the cost the ``resources`` check already resolved and left
           on the context, so the amount checked is the amount charged. The
           charge is whole-or-none (R12.2) and it is the *authority* — the check
           ahead of it is a pre-check that reports a shortfall early, so a charge
           that fails anyway refuses the same ``resources`` check with the same
           have-and-need breakdown (R12.3). An **empty cost is not charged at
           all**, which is what makes an NPC-originated operation free rather
           than special-cased (R12.6, R11.6): there is nothing to charge and so
           nothing to refund.
        2. **Build** the record through the vector's hook, stamp what was
           charged on it for the refund path, and **floor** a hostile
           operation's Response_Window (R8.8) — before the state write, so the
           record that reaches persistence already carries the floored clock.
        3. **Track** it, then move it to Pending through :meth:`_transition`, the
           single writer of ``record.state``, which persists on the way (R8.5).
        4. **Note the cooldown** (R8.19), which measures from the request rather
           than from the effect, so a long-fused operation and an instant one
           throttle their originating building the same way. This is the *only*
           ledger that fires here: ``note_escalation`` is R10.6's and fires when
           a hostile operation **resolves**, not when it is accepted.

        Every step from the record's construction to its Pending entry sits
        inside one guarded block, and each of them is a genuine failure point —
        the hook can raise, tracking can raise, the persist inside the
        transition can raise, and the transition itself declines a record a
        vector handed back already terminal. All four land on the same refund
        path (:meth:`_refund_failed_entry`), because R8.6 draws no distinction
        between them: the player was charged and holds no operation.

        R8.7's notification of a hostile operation's targets sits between the
        Pending entry and the cooldown note, because the Response_Window is
        measured from that notification to the effect (R8.8) — so the targets are
        warned the moment the clock the payload quotes starts running, and the
        clock it quotes is the floored one step 2 already wrote. It publishes a
        kind plus structured values and composes no message (R13.5), and it is
        guarded end to end, so a broken presenter path cannot unmake an operation
        that has already been charged and placed.
        """
        cost = dict(ctx.cost)
        if cost and not self._ask("charge", ctx.player, cost, default=False):
            # R12.2: whole-or-none, and a refused charge wrote nothing — so this
            # is a refusal of the check the pre-check reports, in its shape.
            return OperationOutcome.refused(
                "resources",
                self._refusal_detail(
                    self._insufficient_detail(ctx.player, cost),
                    MSG_VECTOR_INSUFFICIENT_RESOURCES,
                ),
            )
        record = None
        try:
            record = self.build_record(ctx)
            record.charged = dict(cost)                   # R8.6: what to give back
            record.ticks_remaining = self._floor_response_window(
                record, hostile=ctx.hostile
            )                                             # R8.8
            self._track(record)
            entered = self._transition(
                record, OperationState.PENDING, reason="accepted"
            )                                             # R8.5, and persists
        except Exception:  # noqa: BLE001 - a charged request answers, never raises
            logger.exception(
                "%s: an operation charged %r failed on its way into Pending",
                self.operation_kind, cost,
            )
            entered = False
        if not entered:
            return self._refund_failed_entry(ctx, record, cost)
        self._notify_targets_pending(record, hostile=ctx.hostile)   # R8.7
        origin = ctx.building
        if origin is None:
            origin = getattr(record, "building_ref", None)
        self._ask("note_cooldown", origin, self.operation_kind)
        return OperationOutcome.accepted(record)           # R8.24

    def _refund_failed_entry(
        self, ctx: OperationContext, record: Any, cost: Mapping[str, int]
    ) -> OperationOutcome:
        """Give back a charge that could not become a Pending operation (R8.6).

        The whole charged amount, not the part that got as far as being spent:
        ``charge`` is whole-or-none, so there is only ever the whole amount to
        return. The half-built record is **untracked** first, so a failed request
        leaves nothing counting against the in-flight cap (R8.20) and nothing on
        the tick loop — it may have reached the tracked list before the step that
        failed, and it is not an operation.

        Answers ``failed`` rather than ``refused``: every check passed, so there
        is no check a player could act on, and the detail says what came back
        rather than what is required. No message key and no prose (R13.5) — this
        is an internal failure the log owns, and the one thing a caller needs is
        that the player is whole again.
        """
        self._untrack(record)
        if cost:
            self._ask("refund", ctx.player, dict(cost))
            # "refunding", not "refunded": the refund service answers nothing,
            # so this line claims the request, and the service's own warnings
            # (a missing add_resource, a failed line) own the completion story.
            logger.warning(
                "%s: refunding %r to %r — a charged operation never reached "
                "Pending (R8.6)", self.operation_kind, dict(cost), ctx.player,
            )
        else:
            logger.warning(
                "%s: %r's operation never reached Pending, and there was "
                "nothing to refund", self.operation_kind, ctx.player,
            )
        return OperationOutcome.failed(
            "pending_entry",
            {"kind": self.operation_kind, "refunded": dict(cost)},
        )

    def _run_check(self, name: str, ctx: OperationContext) -> dict[str, Any] | None:
        """Run the check called *name*, or refuse in its name when it cannot run.

        The guarded call site of every check, so :meth:`request` reads exactly
        one shape back: ``None`` for a pass, and a plain dict naming a message
        key plus the values required to pass for a refusal.

        Two failures are answered rather than raised (R15.3), both reported as a
        refusal of the check that could not answer — the safe direction, since a
        check that cannot run has not been passed:

        * **the check is missing**, which only a subclass deleting one can cause;
        * **the check raised**, which for the ``target`` check includes a
          vector's own ``validate_target`` hook raising.
        """
        message = _CHECK_MESSAGES.get(name, MSG_VECTOR_UNWIRED)
        check = getattr(self, f"_check_{name}", None)
        if not callable(check):
            logger.error(
                "%s: the %r check is missing from the driver; refusing the request",
                self.operation_kind, name,
            )
            return self._refusal_detail(None, message, reason="check_missing")
        try:
            answer = check(ctx)
        except Exception:  # noqa: BLE001 - a broken check refuses, never raises
            logger.exception(
                "%s: the %r check failed; refusing the request",
                self.operation_kind, name,
            )
            return self._refusal_detail(None, message, reason="check_failed")
        if answer is None:
            return None
        return self._refusal_detail(answer, message)

    def _refusal_detail(
        self, value: Any, message: str, **extra: Any
    ) -> dict[str, Any]:
        """Return *value* as the structured detail a refusal carries (R8.4).

        The one normalizer three shapes of answer pass through, so a refusal is
        always a plain dict naming a message **key** and the values required to
        pass the check:

        * a ``BranchRefusal`` — the key as its string value, carrying its payload
          on ``data``, which is what ``BranchSystem.may_target`` answers and the
          shape a vector's ``validate_target`` is invited to copy;
        * a mapping — what every check in this driver builds;
        * a plain string — a bare refusal key, the shape the ``validate_target``
          signature advertises.

        **Never composed prose** (R13.5): the ``message`` entry is a message key
        and the presenter or the command layer owns every word a player reads.
        Every detail also names the Operation_Kind, because a refusal is read far
        from the vector that produced it.
        """
        detail: dict[str, Any] = {}
        key = _as_name(getattr(value, "key", None))
        payload = getattr(value, "data", None)
        if key and isinstance(payload, Mapping):
            detail = {"message": key, **dict(payload)}
        elif isinstance(value, Mapping):
            detail = dict(value)
        elif isinstance(value, str) and value.strip():
            detail = {"message": value.strip()}
        detail.update(extra)
        detail.setdefault("message", message)
        detail.setdefault("kind", self.operation_kind)
        return detail

    def _build_context(self, player: Any, params: Any) -> OperationContext:
        """Return the :class:`OperationContext` one request walks the chain in.

        Reads the six parameters the driver understands by value and copies the
        whole parameter map, so a vector's own keys reach its hooks and nothing
        the caller passed can be mutated underneath it.
        """
        data = dict(params) if isinstance(params, Mapping) else {}
        ctx = OperationContext(
            player=player,
            params=data,
            building=data.get("building"),
            target=data.get("target"),
            target_x=_as_opt_int(data.get("x")),
            target_y=_as_opt_int(data.get("y")),
            hostile=bool(data.get("hostile", True)),
        )
        ctx.planet = self._request_planet(ctx)
        return ctx

    def _request_planet(self, ctx: OperationContext) -> Any:
        """Return the planet a request happens on, or ``None`` for "any planet".

        Resolved **once** per request, in the order that is most specific first:
        the planet the caller named, then the originating building's, then the
        requesting player's. Every planet-scoped Branch service the chain calls
        gets this one answer, so no two checks can end up asking about different
        planets — and ``None`` is the "any planet" wildcard those services
        already document.
        """
        named = ctx.param("planet")
        if named is not None:
            return named
        return self._entity_planet(ctx.building) or self._entity_planet(ctx.player)

    # ------------------------------------------------------------------ #
    #  The nine checks, in the order R8.3 fixes
    # ------------------------------------------------------------------ #

    def _check_collaborators(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse while a collaborator this vector needs is not injected (R15.2).

        First in the order, because an unwired system can answer no other
        question: every later check reads a Branch service or the registry. The
        requirement's word is *degrade* — an unwired system refuses the operation
        and **logs** the missing collaborator, rather than raising from deep
        inside a request — so this is the whole of that behaviour, in one place,
        for all six vectors.

        Two sources of names, and both are read off the instance:

        * the **Branch_System**, which every vector needs whatever it declares:
          commitment, carrier eligibility, targeting, both ledgers, and the
          resource breakdown are all its services (R15.8);
        * each name in ``_required_collaborators``, resolved plainly *and* with
          the private ``_`` prefix this codebase stores injected collaborators
          under, so ``("combat_engine",)`` matches ``self.combat_engine`` or
          ``self._combat_engine``.

        The refusal names the first missing collaborator in declaration order and
        carries the whole missing list, which is the value required to pass:
        wire these, and the request can be judged on its merits.
        """
        required = self._collaborator_names()
        missing = [name for name in required if self._collaborator(name) is None]
        if not missing:
            return None
        logger.warning(
            "%s: refusing a request from %r — these collaborators are not "
            "injected: %s (R15.2)",
            self.operation_kind, ctx.player, ", ".join(missing),
        )
        return {
            "message": MSG_VECTOR_UNWIRED,
            "collaborator": missing[0],
            "missing": list(missing),
            "required": list(required),
        }

    def _check_commitment(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse unless the owner's Branch_Commitment is this vector's Branch.

        A Signature_Vector belongs to one Branch, and a Branch is live for a
        player on a planet only while that player owns that Branch's lab there
        (R3.1) — so a dormant Branch requests no operations, and this is the
        check a Detection_Sweep from an uncommitted owner of a scout fails
        (design §3.7).

        A vector that declares **no** Branch matches no commitment and so refuses
        every request: a blank ``branch`` is a mis-declared vector, and the
        degrade-to-refusal direction makes that visible instead of silently
        exempting it from the one gate every operation shares.

        The refusal reports the Branch required, its doctrine name, and the lab
        that establishes it — the value required to pass — alongside the Branch
        the player actually holds.
        """
        required = _as_name(self.branch)
        held = _as_name(self._ask("commitment", ctx.player, ctx.planet))
        if required is not None and held == required:
            return None
        return {
            "message": MSG_VECTOR_COMMITMENT_REQUIRED,
            "required_branch": required,
            "required_doctrine": BRANCH_DOCTRINE.get(required) if required else None,
            "required_lab": (
                _as_name(self._ask("lab_for_branch", required)) if required else None
            ),
            "current_branch": held,
            "current_doctrine": BRANCH_DOCTRINE.get(held) if held else None,
            "planet": ctx.planet,
        }

    def _check_origin(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse unless the originating building is owned and Operational.

        Three conditions, and the ``reason`` on the refusal names which failed:

        * a building was **named** at all — an operation originates from a
          Branch_Building, and a request naming none has nothing to originate
          from;
        * the requester **owns** it, compared by ``.id`` exactly as every other
          ownership check in the game compares;
        * it is **Operational**, through ``BranchSystem.is_operational``, which
          is where the Active_HQ_Rule (R11.3) and "a dormant Branch's buildings
          perform no capability behaviour" (R5.4) already live. Both are folded
          in by delegating rather than reimplemented here.
        """
        if ctx.building is None:
            return self._origin_refusal(ctx, ORIGIN_MISSING)
        if not self._is_same_entity(self._entity_owner(ctx.building), ctx.player):
            return self._origin_refusal(ctx, ORIGIN_NOT_OWNED)
        if not self._ask("is_operational", ctx.building, default=False):
            return self._origin_refusal(ctx, ORIGIN_NOT_OPERATIONAL)
        return None

    def _origin_refusal(self, ctx: OperationContext, reason: str) -> dict[str, Any]:
        """Return the ``origin`` refusal, naming which condition failed."""
        bdef = self._building_definition(ctx.building)
        return {
            "message": MSG_VECTOR_ORIGIN_UNAVAILABLE,
            "reason": reason,
            "building": _as_name(getattr(bdef, "abbreviation", None)),
            "building_name": _as_name(getattr(bdef, "name", None)),
            "required_branch": _as_name(self.branch),
            "planet": ctx.planet,
        }

    def _check_unlock(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse unless the originating building's unlock technology is live.

        R6.2's two conditions, asked as one question: the technology is in the
        owner's record **and** its effects are currently applied — its Branch
        committed here, and no Reinstatement job still pending for it. That is
        exactly what ``BranchSystem.applied_technologies`` answers, so the gate
        that unlocked the building and the gate that lets it act cannot disagree
        about what a researched technology is (R6.6).

        A building declaring no ``unlock_technology`` passes untouched (R6.1),
        which is every building shipped before this feature.

        The refusal reports the technology and the Branch and lab that host it —
        R6.3's shape, and the value required to pass.
        """
        bdef = self._building_definition(ctx.building)
        required = _as_name(getattr(bdef, "unlock_technology", None))
        if required is None:
            return None                                   # ungated by research
        applied = self._ask(
            "applied_technologies", ctx.player, ctx.planet, default=frozenset()
        )
        try:
            if required in applied:
                return None
        except TypeError:  # an unreadable answer withholds the technology
            pass
        hosting = _as_name(self._ask("branch_of_technology", required))
        return {
            "message": MSG_VECTOR_UNLOCK_REQUIRED,
            "technology": required,
            "branch": hosting,
            "doctrine": BRANCH_DOCTRINE.get(hosting) if hosting else None,
            "lab": _as_name(self._ask("lab_for_branch", hosting)) if hosting else None,
            "building": _as_name(getattr(bdef, "abbreviation", None)),
            "building_name": _as_name(getattr(bdef, "name", None)),
            "planet": ctx.planet,
        }

    def _check_carrier(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse unless an eligible Carrier_Agent of the required role exists.

        Every Vector_Operation needs a body to send (R7.1), so this is the check
        that makes "no Vector_Operation resolves without an agent" structural.
        The role is the Operation_Kind's own — the registry entry's
        ``carrier_role``, with that Branch's single role as the fallback — and
        eligibility is ``BranchSystem.eligible_carrier``'s conjunction of alive,
        assigned to the role, active outside reserve, and not incapacitated
        (R7.5).

        The eligible agent is kept on the context, so the record the acceptance
        half builds names the carrier this check found rather than searching the
        roster a second time and possibly finding a different one.

        The refusal reports **the required role** (R7.3), which is the value
        required to pass: train or reassign an agent to it.
        """
        role = self._carrier_role()
        ctx.role = role
        carrier = (
            self._ask("eligible_carrier", ctx.player, role, ctx.planet)
            if role is not None else None
        )
        if carrier is not None:
            ctx.carrier = carrier
            return None
        return {
            "message": MSG_VECTOR_CARRIER_REQUIRED,
            "role": role,
            "branch": _as_name(self.branch),
            "planet": ctx.planet,
        }

    def _check_target(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse an invalid or protected target — the vector's hook, then the gates.

        Two questions, in this order:

        1. the **vector's own**, through the ``validate_target`` hook: is this
           the sort of thing *this* operation may be aimed at. No other vector
           can answer it, and no framework rule should try.
        2. the **shared protection gates**, through
           ``BranchSystem.may_target``, which folds all four into one answer —
           the new-player shield (R10.4), the allied-target refusal (R11.9), the
           support-consent check (R11.8), and the escalation cap (R10.6) — and
           applies them to alliance members, allies, and unaffiliated players on
           identical terms (R10.7). They are folded in *here* because they are
           target-validity questions, and they are **delegated** rather than
           reimplemented so all six vectors share one implementation of them.

        Either answer arrives as a message key plus structured data and is
        carried through unchanged, so a refusal says which of the four gates
        fired and reports the value that would pass it — the qualifying level,
        the protecting alliance, the missing consent, the remaining ticks.

        A request naming no target passes both: whether a target is *required* is
        the vector's question, and a coordinate-only operation names the occupant
        it would affect as its ``target`` when it has one.

        **The gates fail closed for a targeted request.** A Branch_System that
        cannot be asked ``may_target`` at all — one predating the service, or
        one that raises — refuses the request rather than waving it past four
        protections at once (R15.2's degrade-to-refusal direction). The service
        *answering* ``None`` is a different thing and still passes: may_target
        owns that reading and already guards its own lookups.

        **The ``hostile`` flag is a trust boundary between the driver and the
        vector, not a player input.** It decides whether the shield, the allied
        refusal, and the escalation cap run at all, whether the targets are
        warned (R8.7), and whether the Response_Window floor applies (R8.8) —
        so a vector or command layer that labels an offensive operation
        ``hostile=False`` bypasses every one of them. The default is ``True``,
        the stricter reading; a vector must only ever relax it for an operation
        that genuinely acts *for* its target.
        """
        refusal = self.validate_target(ctx)
        if refusal is not None:
            return self._refusal_detail(refusal, MSG_VECTOR_TARGET_INVALID)
        gate = self._ask(
            "may_target", ctx.player, ctx.target, hostile=ctx.hostile,
            default=_UNREADABLE,
        )
        if gate is _UNREADABLE:
            # The gates could not be ASKED at all — a Branch_System predating
            # ``may_target``, or one that raised. For a request that names a
            # target that is not a pass: the four protections exist for the
            # target's sake, and R15.2's degrade-to-refusal direction applies
            # to them exactly as it applies to an unwired collaborator. (An
            # answer of ``None`` from the service itself still means "may
            # proceed" — may_target owns that reading and guards its own
            # lookups.) A request naming no target has nothing the gates
            # protect, so it passes on the vector's own answer above.
            if ctx.target is None:
                return None
            logger.warning(
                "%s: the protection gates could not be asked about %r; "
                "refusing the request rather than skipping them (R15.2)",
                self.operation_kind, ctx.target,
            )
            return self._refusal_detail(
                None, MSG_VECTOR_TARGET_INVALID,
                reason="protection_gates_unavailable",
            )
        if gate is not None:
            return self._refusal_detail(gate, MSG_VECTOR_TARGET_INVALID)
        return None

    def _check_cooldown(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse while this building's cooldown for this kind has not elapsed.

        The cooldown is per originating building per Operation_Kind (R8.19), and
        the ledger lives on the building — so this reports
        ``BranchSystem.cooldown_remaining``'s own figure, which is the number of
        ticks the requirement asks be reported and the value required to pass.
        """
        remaining = _as_int(
            self._ask(
                "cooldown_remaining", ctx.building, self.operation_kind, default=0
            ),
            0,
        )
        if remaining <= 0:
            return None
        bdef = self._building_definition(ctx.building)
        return {
            "message": MSG_VECTOR_COOLDOWN,
            "remaining_ticks": remaining,
            "building": _as_name(getattr(bdef, "abbreviation", None)),
            "building_name": _as_name(getattr(bdef, "name", None)),
        }

    def _check_in_flight(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse while this player already holds the cap of this kind here.

        R8.20 bounds the simultaneous **non-terminal** operations one player
        holds of one Operation_Kind on one planet, and both figures the
        requirement asks be reported come from the Branch services rather than
        from a second tally kept here: the count is this vector's own tracked
        records read back through ``in_flight_count``, and the cap is the
        Balance_Config value ``in_flight_cap`` reads per call.

        A cap below ``1`` is **unbounded**, not "refuse everything" — an absent
        or unreadable knob must not lock a player out of their own doctrine.
        """
        cap = _as_int(self._ask("in_flight_cap", self.operation_kind, default=0), 0)
        if cap < 1:
            return None                                   # no cap configured
        count = _as_int(
            self._ask(
                "in_flight_count", ctx.player, self.operation_kind, ctx.planet,
                default=0,
            ),
            0,
        )
        if count < cap:
            return None
        return {
            "message": MSG_VECTOR_IN_FLIGHT_CAP,
            "count": count,
            "cap": cap,
            "planet": ctx.planet,
        }

    def _check_resources(self, ctx: OperationContext) -> dict[str, Any] | None:
        """Refuse while the player cannot afford this Operation_Kind's cost.

        **Last** in the order, so a player blocked for a structural reason hears
        the structural reason rather than "not enough Iron". The refusal carries
        the existing have-and-need breakdown for every required resource (R12.3),
        which is the value required to pass, plus the cost itself.

        This is a *sufficiency* check, not the charge: the charge is whole-or-none
        and happens in the acceptance half (R8.5, R12.2). So an answer this cannot
        judge — a player whose stock cannot be read at all — passes here and is
        refused by the charge, which is the authority. The cost is resolved once
        and kept on the context, so the amount checked is the amount charged.
        """
        cost = self._resource_cost(ctx)
        ctx.cost = dict(cost)
        if not cost:
            return None                                   # nothing to afford
        lines = self._shortfall_lines(ctx.player, cost)
        if not lines:
            return None                                   # the charge decides
        if not self._missing_lines(lines):
            return None
        return self._insufficient_detail(ctx.player, cost, lines)

    def _shortfall_lines(
        self, player: Any, cost: Mapping[str, int]
    ) -> dict[str, dict[str, int]]:
        """Return ``{resource: {"have", "need"}}`` for *cost*, or ``{}``.

        ``BranchSystem.resource_shortfall``'s answer, normalized: the resource
        names cleaned and every line reduced to a plain dict this driver owns. An
        answer that is not a mapping at all, and an empty one, both reduce to
        ``{}`` — "nothing can be said about this player's stock" — which the
        ``resources`` check reads as "let the charge decide" and the charge's own
        refusal reports as an empty breakdown rather than as a guess.
        """
        breakdown = self._ask("resource_shortfall", player, cost, default={})
        if not isinstance(breakdown, Mapping):
            return {}
        return {
            name: (dict(line) if isinstance(line, Mapping) else {})
            for name, line in (
                (_as_name(resource), line) for resource, line in breakdown.items()
            )
            if name is not None
        }

    @staticmethod
    def _missing_lines(
        lines: Mapping[str, Mapping[str, int]]
    ) -> dict[str, dict[str, int]]:
        """Return only those *lines* whose ``have`` falls short of their ``need``."""
        return {
            resource: dict(line) for resource, line in lines.items()
            if _as_int(line.get("have"), 0) < _as_int(line.get("need"), 0)
        }

    def _insufficient_detail(
        self,
        player: Any,
        cost: Mapping[str, int],
        lines: Mapping[str, Mapping[str, int]] | None = None,
    ) -> dict[str, Any]:
        """Return the "cannot afford this" detail R12.3 asks be reported.

        One shape for both places a request can be refused over a cost — the
        ``resources`` pre-check, and the whole-or-none charge that is the
        authority — so a caller reads the same have-and-need breakdown either
        way and never has to know which of the two refused. *lines* is passed in
        by the pre-check, which has already read them; the charge path lets this
        read them once for itself.
        """
        if lines is None:
            lines = self._shortfall_lines(player, cost)
        return {
            "message": MSG_VECTOR_INSUFFICIENT_RESOURCES,
            "cost": dict(cost),
            "resources": {name: dict(line) for name, line in lines.items()},
            "missing": self._missing_lines(lines),
        }

    # ------------------------------------------------------------------ #
    #  What the checks read: collaborators, the registry, the balance knobs
    # ------------------------------------------------------------------ #

    def _collaborator_names(self) -> tuple[str, ...]:
        """Return every collaborator a request needs, the driver's own first.

        The Branch_System leads because the chain cannot ask a single question
        without it; the vector's declared names follow, in declaration order,
        deduplicated so a name declared twice is reported once.
        """
        declared: list[str] = []
        for name in tuple(self._required_collaborators or ()):
            cleaned = _as_name(name)
            if cleaned and cleaned != "branch_system" and cleaned not in declared:
                declared.append(cleaned)
        return ("branch_system", *declared)

    def _collaborator(self, name: str) -> Any:
        """Return the collaborator called *name*, or ``None`` when unwired.

        Read off the instance **both plainly and with the private ``_`` prefix**
        this codebase stores injected collaborators under, so a vector declaring
        ``("combat_engine",)`` is satisfied by ``self.combat_engine`` or by
        ``self._combat_engine`` and need not know which convention its own
        composition root used. The Branch_System is the one name held under a
        shorter attribute, so it resolves explicitly.
        """
        if name == "branch_system":
            return self._branch
        for attr in (name, f"_{name}"):
            try:
                value = getattr(self, attr, None)
            except Exception:  # noqa: BLE001 - a property that raises is unwired
                value = None
            if value is not None:
                return value
        return None

    def _ask(self, service: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
        """Call one Branch_System service by name, answering *default* if it cannot.

        The single guarded call site for every shared service the chain consumes
        (R15.8) — ``commitment``, ``lab_for_branch``, ``is_operational``,
        ``applied_technologies``, ``branch_of_technology``, ``role_for_branch``,
        ``eligible_carrier``, ``may_target``, ``cooldown_remaining``,
        ``in_flight_count``, ``in_flight_cap``, and ``resource_shortfall``. Each
        of those already answers a value for every input and raises nothing
        (R15.3); this guard covers the two shapes that are not the service's
        fault — a Branch_System predating a service, and a test double exposing
        only some of them — so neither reaches a command layer as an exception.

        ``_check_collaborators`` has already refused an entirely unwired driver
        by the time any service is asked (R15.2), which is what makes a default
        here the degraded answer for a *partially* wired one. Each caller picks
        the default that refuses rather than the one that waves a request
        through — including the ``target`` check, which asks ``may_target``
        with a sentinel default so "the gates could not be asked" refuses a
        targeted request instead of skipping four protections at once. (The
        service *answering* ``None`` still means "may proceed": may_target owns
        that reading and guards its own lookups.)
        """
        try:
            call = getattr(self._branch, service, None)
        except Exception:  # noqa: BLE001 - an unreadable attribute is no service
            call = None
        if not callable(call):
            logger.debug(
                "%s: the Branch system exposes no %r; answering %r",
                self.operation_kind, service, default,
            )
            return default
        try:
            return call(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a service failure is not a raise
            logger.exception(
                "%s: the Branch service %r failed", self.operation_kind, service
            )
            return default

    def _carrier_role(self) -> str | None:
        """Return the agent role this Operation_Kind requires, or ``None`` (R7.2).

        Two layers, the order the design intends: the **binding is data** — the
        Operation_Kind registry entry names the role, so a kind can be retargeted
        in ``branches.yaml`` without code — and this Branch's single role
        (``BranchSystem.role_for_branch``) is the fallback that keeps the read
        working with that data absent. ``None`` when neither can answer, which
        the carrier check refuses on: an operation whose required role is unknown
        cannot be shown to have a carrier, and R7.1 admits no operation without
        one.
        """
        role = _as_name(getattr(self._kind_def(), "carrier_role", None))
        if role is not None:
            return role
        return _as_name(self._ask("role_for_branch", _as_name(self.branch)))

    def _resource_cost(self, ctx: OperationContext) -> dict[str, int]:
        """Return this Operation_Kind's per-use resource cost (R12.1).

        Two layers again: the Operation_Kind registry entry names the
        Balance_Config field (``cost_field``), and the naming convention
        ``<kind>_cost`` is the fallback. The value is read on **every** request
        and never cached, so an ``@reload`` retunes the next operation (R15.7).

        **An NPC base's operation costs nothing** (R12.6). The whole of that
        requirement is this one early return: the cost is ``{}``, so the
        ``resources`` check has nothing to refuse over and the acceptance half
        has nothing to charge — an NPC practice target needs no NPC economy.
        Waiving the *charge* is all it waives: the notification, the
        Response_Window, and the Universal_Counter rules reach an NPC-originated
        operation exactly as they reach a player's (R11.6), because none of them
        reads the cost.

        Returns:
            ``{resource: positive int}`` — a fresh map, so no caller can reach the
            configured one by mutation. Empty for an NPC base's operation, for a
            kind with no configured cost, and for every registry this driver
            cannot read, each of which the charge treats as a no-op that
            succeeds.
        """
        kind = _as_name(self.operation_kind)
        if kind is None:
            return {}
        if self._is_npc_owner(ctx.player):
            return {}                                     # R12.6
        field_name = _as_name(getattr(self._kind_def(), "cost_field", None))
        configured = _as_cost_map(self._balance_value(field_name or f"{kind}_cost"))
        return {
            resource: amount for resource, amount in configured.items() if amount > 0
        }

    def _registry(self) -> Any:
        """Return the injected DataRegistry, or ``None`` (R15.4).

        Read off the instance rather than held here: in the composed shape
        ``class V(OperationDriver, BaseSystem)`` the registry belongs to
        ``BaseSystem``, and a bare driver in a test may have none at all — which
        answers ``None`` and degrades every registry-backed read to its default
        rather than raising.
        """
        try:
            return getattr(self, "registry", None)
        except Exception:  # noqa: BLE001 - an unreadable registry is no registry
            return None

    def _kind_def(self) -> Any:
        """Return this Operation_Kind's registry definition, or ``None``.

        The binding table behind the per-kind Balance_Config field names and the
        Carrier_Agent role (R7.2, R8.19, R8.20, R12.1), read through the
        **injected** registry (R15.4).
        """
        kinds = getattr(self._registry(), "operation_kinds", None) or {}
        try:
            return kinds.get(_as_name(self.operation_kind))
        except (AttributeError, TypeError):
            return None

    def _balance_value(self, field_name: str | None, default: Any = None) -> Any:
        """Return the Balance_Config value of *field_name*, or *default*.

        Read from the injected registry on every call, so every knob this driver
        consumes stays hot (R15.7). An absent registry, an absent balance, a
        blank field name, and a field holding ``None`` all answer *default*.
        """
        if not field_name:
            return default
        balance = getattr(self._registry(), "balance", None)
        if balance is None:
            return default
        try:
            value = getattr(balance, field_name, None)
        except Exception:  # noqa: BLE001 - an unreadable knob is an absent one
            return default
        return default if value is None else value

    def _building_definition(self, building: Any) -> Any:
        """Return the definition of *building*, or ``None``.

        Resolves the object's ``building_type`` through the **injected** registry
        (R15.4), so the abbreviation a refusal quotes and the ``unlock_technology``
        the chain gates on come from the same definitions every other system
        reads. A definition passed straight through is returned as it is,
        recognized by carrying an ``abbreviation``.

        ``None`` for a building carrying no type, a type absent from the loaded
        definitions, and a registry that cannot answer — each of which reads
        downstream as "no unlock technology and no abbreviation to quote", never
        as an error.
        """
        if building is None:
            return None
        if isinstance(building, str):
            abbr = _as_name(building)
        else:
            try:
                if getattr(building, "abbreviation", None) is not None:
                    return building                       # already a definition
            except Exception:  # noqa: BLE001 - an unreadable field is absent
                pass
            abbr = _as_name(self._entity_attr(building, "building_type"))
        if abbr is None:
            return None
        buildings = getattr(self._registry(), "buildings", None) or {}
        try:
            return buildings.get(abbr) or buildings.get(abbr.upper())
        except (AttributeError, TypeError):
            return None

    @staticmethod
    def _entity_attr(entity: Any, key: str) -> Any:
        """Read one persisted attribute off *entity* by value, or ``None``.

        The attribute handler first, then the ``db`` proxy — the same order
        ``world.utils.get_obj_attr`` reads in, done **duck-typed** here so this
        module keeps reaching the framework through nothing but an object's own
        handler (R15.1).
        """
        if entity is None:
            return None
        handler = _owner_attributes(entity)
        getter = getattr(handler, "get", None)
        if callable(getter):
            try:
                value = getter(key, default=None)
            except Exception:  # noqa: BLE001 - an unreadable attribute is absent
                value = None
            if value is not None:
                return value
        try:
            return getattr(getattr(entity, "db", None), key, None)
        except Exception:  # noqa: BLE001 - an unreadable db proxy holds nothing
            return None

    @classmethod
    def _entity_planet(cls, entity: Any) -> Any:
        """Return the planet *entity* is on, or ``None`` for "any planet"."""
        return cls._entity_attr(entity, "coord_planet")

    @classmethod
    def _entity_owner(cls, entity: Any) -> Any:
        """Return the player or Sentinel owning *entity*, or ``None``."""
        return cls._entity_attr(entity, "owner")

    @classmethod
    def _is_npc_owner(cls, entity: Any) -> bool:
        """Return True when *entity* is an NPC base rather than a player (R12.6).

        The **same two markers** ``BranchSystem._entity_level`` reads when it
        decides that a target is no "new player" and so no candidate for the
        level shield: an NPC base's Sentinel carries ``is_sentinel``, and an NPC
        object carries ``npc_type``. Reused rather than reinvented so "what an
        NPC base is" has one answer across the feature — a driver that judged it
        differently would either charge an NPC base or waive a player's cost.

        Read by value through :meth:`_entity_attr`, so an unreadable entity
        answers ``False``: an entity nobody can identify as an NPC base is
        charged like a player, which is the direction that cannot leak free
        operations.
        """
        if entity is None:
            return False
        return bool(
            cls._entity_attr(entity, "is_sentinel")
            or cls._entity_attr(entity, "npc_type")
        )

    @staticmethod
    def _is_same_entity(left: Any, right: Any) -> bool:
        """Return True when *left* and *right* are the same entity.

        Compared by ``.id`` when both expose one and by identity otherwise —
        the same comparison ``world.utils.is_owner`` makes, written duck-typed so
        this module needs no framework import (R15.1). Reliable across a restart
        for the same reason: an id survives, an object reference does not.
        """
        if left is None or right is None:
            return False
        try:
            left_id = getattr(left, "id", None)
            right_id = getattr(right, "id", None)
        except Exception:  # noqa: BLE001 - an unreadable identity is not a match
            return left is right
        if left_id is not None and right_id is not None:
            return bool(left_id == right_id)
        return left is right

    # ------------------------------------------------------------------ #
    #  The Response_Window floor (R8.8, R9.4) — design §4.5
    # ------------------------------------------------------------------ #

    def _floor_response_window(
        self, record: OperationRecord, hostile: bool | None = None
    ) -> int:
        """Return *record*'s ``ticks_remaining``, floored for a hostile op (R8.8).

        R8.8 puts a floor under the warning a hostile operation's target gets:
        whatever the vector asks for, and whatever a Counter_Web Response_Window
        reduction (R9.4's second permitted form) takes off, the target gets at
        least ``minimum_response_window_ticks``. The floor is a ``max`` rather
        than a subtraction, which is what makes the claim hold for **every**
        reduction value including absurd ones — a reduction larger than the base
        leaves the floor exactly, never a negative window.

        Called at the **two** points a clock enters the tick loop, so a reduction
        is clamped rather than trusted at either:

        * the single point where a record enters Pending (:meth:`_accept`), which
          passes the request's own ``hostile`` rather than inferring it;
        * **and on resume**, where the ticks held at suspension are restored
          (R8.15). The resume path lands with the tick advance (task 11.5) and
          **must call this** — a resumed hostile operation whose window was not
          re-floored would give its target less warning than R8.8 promises, and
          nothing else re-checks it.

        The figure comes from ``BranchSystem.response_window``, which already
        reads the floor per call (R15.7) and is the one implementation all six
        vectors share (R15.8). The local floor read is the guard for a partially
        wired Branch_System: it can only ever raise the answer, so the claim
        holds even when the service cannot be reached at all.

        Args:
            record: The operation whose clock to floor.
            hostile: Whether the operation is aimed *at* its target. ``None``
                asks :meth:`_is_hostile`, which is what the resume path — with
                only a persisted record in hand — has to rely on.

        Returns:
            The ticks until the effect: at least the configured floor for a
            hostile operation, and never negative for any operation.
        """
        base = _as_int(getattr(record, "ticks_remaining", 0), 0)
        if hostile is None:
            hostile = self._is_hostile(record)
        if not hostile:
            return max(0, base)                           # no window to protect
        floor = max(0, _as_int(self._balance_value("minimum_response_window_ticks"), 0))
        windowed = _as_int(
            self._ask("response_window", base, default=None), max(floor, base)
        )
        return max(floor, windowed)

    def _is_hostile(self, record: OperationRecord) -> bool:
        """Whether *record*'s operation is aimed AT its target, rather than at
        supporting it — the question the Response_Window floor turns on (R8.8).

        A request states this itself (``params["hostile"]``, defaulting to the
        stricter reading) and :meth:`_accept` passes that answer straight through,
        so this is only consulted where the request is gone and a persisted
        record is all there is: the resume path. Hostility is deliberately **not**
        a persisted field — the Operation_Record holds what an operation *is*, not
        how it was asked for — so the default here is ``True``, the same stricter
        reading the request parameter defaults to: an over-floored window costs a
        target more warning than needed, while an under-floored one breaks R8.8.

        A vector whose Signature_Vector supports rather than attacks overrides
        this with ``return False``; one that does both should decide from a field
        it stamped on the record itself.
        """
        return True

    # ------------------------------------------------------------------ #
    #  The notification points (R8.7, R8.12, R8.13, R13.5) — design §4.4
    # ------------------------------------------------------------------ #
    #
    # Every point publishes a notification KIND plus structured values through
    # ``BaseSystem.notify``, and composes not one player-facing word (R13.5) —
    # the ``NotificationPresenter`` owns every sentence, and holds a formatter
    # for each of :data:`VECTOR_NOTIFICATION_KINDS` (R13.6, R13.8).
    #
    # The points live here, on the driver, so all six vectors notify identically
    # and an NPC-originated operation notifies exactly as a player's does (R11.6).
    # Two of them are called from the request path and the rest from the
    # transitions the tick advance drives (task 11.5) and the restart rebuild
    # (task 11.6): those transitions call the helper, they do not compose their
    # own payload.

    def _notify_targets_pending(
        self, record: OperationRecord, hostile: bool | None = None
    ) -> int:
        """Warn a hostile operation's targets that it is inbound (R8.7).

        Published at the single point a record enters Pending, **between** the
        Pending entry and the cooldown note, because R8.8 measures the
        Response_Window *from this notification* to the effect: the clock the
        payload quotes is the floored one the record already carries.

        The payload is R8.7's four values — the Operation_Kind, the originating
        player's name, the affected coordinate, and the ticks remaining — and
        nothing else. A **supporting** operation publishes none: R8.7 is about a
        hostile operation, and an ally being helped is not being warned.

        The audience is every player the effect would reach except the
        originating player, who has their own accepted outcome to read and does
        not need warning about their own operation.

        Args:
            record: The operation that has just entered Pending.
            hostile: Whether the operation is aimed *at* its target. ``None``
                asks :meth:`_is_hostile`, the reading the record alone supports.

        Returns:
            How many players were notified.
        """
        if hostile is None:
            hostile = self._is_hostile(record)
        if not hostile:
            return 0
        x, y = self._record_coords(record)
        owner = self._record_owner(record)
        audience = [
            player for player in self._resolution_audience(record)
            if not self._is_same_entity(player, owner)
        ]
        return self._notify_each(
            audience,
            NOTIFY_VECTOR_INCOMING,
            kind=self.operation_kind,
            attacker_name=self._name_of(owner),
            x=x,
            y=y,
            ticks=_as_int(_reach(record, "ticks_remaining"), 0),
        )

    def _notify_resolution(self, record: OperationRecord) -> int:
        """Report an operation's effect to everyone it reached (R8.12).

        R8.12 names two audiences — the players who own an affected entity and
        the players standing on an affected tile — and this resolves **both**
        from the effect's area and de-duplicates them, so a player who is both
        gets one notification rather than two (design §4.4).

        Two kinds, because the two readings of the same event are different
        messages: the originating player reads ``vector_resolved`` (*your*
        operation landed, and where), and everybody else in the area reads
        ``vector_hit`` (an operation landed *on* you, and whose it was). Both
        carry the Operation_Kind and the affected coordinate R8.12 asks be
        reported.

        **No ownership or alliance exclusion** (R11.10): the area is walked as it
        is, so the originating player's own entities and their allies' entities
        put their owners in the audience exactly as an enemy's do — an
        indiscriminate area effect stays indiscriminate, in the notification as
        well as in the damage.

        Returns:
            How many players were notified.
        """
        x, y = self._record_coords(record)
        owner = self._record_owner(record)
        sent = 0
        if self._notify(
            owner, NOTIFY_VECTOR_RESOLVED, kind=self.operation_kind, x=x, y=y
        ):
            sent += 1
        recipients = [
            player for player in self._resolution_audience(record)
            if not self._is_same_entity(player, owner)
        ]
        return sent + self._notify_each(
            recipients,
            NOTIFY_VECTOR_HIT,
            kind=self.operation_kind,
            attacker_name=self._name_of(owner),
            x=x,
            y=y,
        )

    def _notify_expiry(self, record: OperationRecord) -> int:
        """Report a bounded lifetime elapsing before the effect (R8.13).

        R8.13's audience is narrower than a resolution's, and deliberately so:
        the operation's **owner** and each **affected entity's owner** — the
        people whose entities this operation had suspended and who are about to
        get them back — not every player who happened to be standing nearby,
        because nothing landed on them.

        Called by the expiry transition (task 11.5), which restores each
        suspended entity through the ``on_expire`` hook.

        Returns:
            How many players were notified.
        """
        x, y = self._record_coords(record)
        return self._notify_each(
            [self._record_owner(record), *self._affected_owners(record)],
            NOTIFY_VECTOR_EXPIRED,
            kind=self.operation_kind,
            x=x,
            y=y,
        )

    def _notify_suspension(self, record: OperationRecord, reason: str = "") -> int:
        """Report an operation pausing (R8.14, R8.18, R13.6).

        The owner's notification only: a suspension changes nothing in the world,
        it stops a clock, and the person whose clock stopped is the one who needs
        to know. The ``reason`` is one of :data:`SUSPEND_CARRIER_UNAVAILABLE` or
        :data:`SUSPEND_COMMITMENT_LAPSED` — a **key**, not a sentence (R13.5).

        Called by the suspend transition (task 11.5).
        """
        x, y = self._record_coords(record)
        return int(self._notify(
            self._record_owner(record),
            NOTIFY_VECTOR_SUSPENDED,
            kind=self.operation_kind,
            reason=_as_name(reason),
            x=x,
            y=y,
        ))

    def _notify_resume(self, record: OperationRecord) -> int:
        """Report an operation resuming with the ticks it held (R8.15, R13.6).

        Quotes ``ticks_remaining`` because that figure *is* the requirement: a
        suspension delays an operation rather than restarting it, and the ticks
        the owner reads back are the ticks the operation held on suspension.

        Called by the resume transition (task 11.5), **after** it has restored
        the clock and re-floored a hostile window (R8.8).
        """
        return int(self._notify(
            self._record_owner(record),
            NOTIFY_VECTOR_RESUMED,
            kind=self.operation_kind,
            ticks_remaining=_as_int(_reach(record, "ticks_remaining"), 0),
        ))

    def _notify_cancellation(self, record: OperationRecord, reason: str = "") -> int:
        """Report an operation ended by a lost collaborator (R8.16, R8.17, R11.4).

        The owner's notification, which all three requirements ask for in the
        same words: notify *that Vector_Operation's owner* of the cancellation.
        The ``reason`` names which collaborator was lost —
        :data:`CANCEL_CARRIER_KILLED`, :data:`CANCEL_ORIGIN_LOST`, or
        :data:`CANCEL_BASE_ELIMINATED` — as a key (R13.5).

        Called by the cancel transition (task 11.5), which the carrier-death,
        ``BUILDING_DESTROYED``, and base-elimination subscriptions all drive.
        """
        return int(self._notify(
            self._record_owner(record),
            NOTIFY_VECTOR_CANCELLED,
            kind=self.operation_kind,
            reason=_as_name(reason),
        ))

    def _notify_discard(self, record: OperationRecord) -> int:
        """Report a rebuild discarding a record it could not resolve (R14.4).

        The one notification published about an operation whose references have
        *gone*, which is why its payload is the Operation_Kind alone: the
        coordinate, the building, and the carrier are exactly the values that
        could not be resolved, so quoting them would be quoting the reason it
        was discarded. Which references were missing goes to the log, naming the
        Operation_Kind and each of them.

        Called by :meth:`_discard`, which is the restart rebuild's transition.
        """
        return int(self._notify(
            self._record_owner(record),
            NOTIFY_VECTOR_DISCARDED,
            kind=self.operation_kind,
        ))

    # ------------------------------------------------------------------ #
    #  Who a notification reaches: the audience (R8.12, R11.10)
    # ------------------------------------------------------------------ #

    #: Ceiling on the radius the tile sweep below walks, in tiles. A record's
    #: radius is data — a Balance_Config value, a vector's arithmetic, or a
    #: hand-edited persisted field — and the sweep is quadratic in it, so an
    #: absurd radius must cost a bounded number of queries rather than a stalled
    #: tick. 32 is 65x65 tiles, far beyond any effect this game configures.
    _MAX_AUDIENCE_RADIUS = 32

    def _resolution_audience(self, record: OperationRecord) -> list:
        """Return every player a resolution notifies, de-duplicated (R8.12).

        The union of R8.12's two audiences, resolved from the effect's area:

        * the **owner of each affected entity**, read off the entity itself, so
          an absent owner (a loose item, a piece of scenery) simply contributes
          nobody; and
        * the **players standing on an affected tile**, from the room's own tile
          query — which is how a player who owns nothing here but is standing in
          the blast still hears about it.

        De-duplicated by identity, so a player who owns an affected building
        *and* is standing next to it is notified once (design §4.4). Filtered by
        nothing else (R11.10): being the originating player, or their ally, does
        not remove anyone from the audience.
        """
        return self._distinct(
            [*self._affected_owners(record), *self._tile_occupants(record)]
        )

    def _affected_owners(self, record: OperationRecord) -> list:
        """Return the owner of each entity in the effect's area, de-duplicated."""
        return self._distinct(
            self._entity_owner(entity) for entity in self._affected_entities(record)
        )

    def _affected_entities(self, record: OperationRecord) -> list:
        """Return every entity inside this operation's effect area (R11.10).

        The area is the Chebyshev ball of ``record.radius`` around the target
        coordinate — the **same metric** every other spatial reach in this game
        uses, so a Vector_Operation's area matches a bomb blast's rather than
        introducing a second notion of "nearby" — read through the room's own
        area query and then filtered by that distance, because a room may answer
        the bounding box rather than the ball.

        The named target entity is always included. It is what the operation was
        aimed at, so it is affected by definition, and including it is what lets
        an operation with a target but no reachable room still notify the person
        whose thing it landed on.

        No ownership or alliance filter is applied, or ever should be (R11.10).

        Returns:
            The entities, de-duplicated by identity. Empty when the operation
            has no target and no room to ask, which notifies nobody rather than
            raising.
        """
        entities = [_reach(record, "target_ref")]
        center = self._area_center(record)
        room = self._area_room(record) if center is not None else None
        query = _reach(room, "get_objects_in_area")
        if center is not None and callable(query):
            x, y, radius = center
            try:
                candidates = list(
                    query(x - radius, y - radius, x + radius, y + radius)
                )
            except Exception:  # noqa: BLE001 - an area nobody can read holds none
                logger.debug(
                    "%s: the effect area around (%s, %s) could not be read",
                    self.operation_kind, x, y, exc_info=True,
                )
                candidates = []
            for entity in candidates:
                coords = self._entity_coords(entity)
                if coords is None:
                    continue
                if max(abs(coords[0] - x), abs(coords[1] - y)) <= radius:
                    entities.append(entity)
        return self._distinct(entities)

    def _tile_occupants(self, record: OperationRecord) -> list:
        """Return the players standing on a tile the effect reaches (R8.12).

        One ``get_players_at`` per tile of the area, which is the query the rest
        of the game announces a tile event through (``BombSystem._broadcast_tile``
        uses the same one), so a player inside a building on an affected tile is
        included exactly as they are for a blast — being indoors does not change
        a player's coordinates.

        The sweep is bounded by :data:`_MAX_AUDIENCE_RADIUS`, so a hand-edited
        radius costs a bounded number of queries. A room with no tile query, and
        a query that raises for one tile, both contribute nobody.
        """
        center = self._area_center(record)
        if center is None:
            return []
        query = _reach(self._area_room(record), "get_players_at")
        if not callable(query):
            return []
        x, y, radius = center
        occupants: list = []
        for tile_x in range(x - radius, x + radius + 1):
            for tile_y in range(y - radius, y + radius + 1):
                try:
                    occupants.extend(query(tile_x, tile_y) or ())
                except Exception:  # noqa: BLE001 - a tile nobody can read is empty
                    logger.debug(
                        "%s: the occupants of (%s, %s) could not be read",
                        self.operation_kind, tile_x, tile_y, exc_info=True,
                    )
        return self._distinct(occupants)

    def _area_center(self, record: OperationRecord) -> tuple[int, int, int] | None:
        """Return ``(x, y, radius)`` for this operation's area, or ``None``.

        ``None`` when the record names no coordinate, which is a legitimate
        shape: a vector that attaches its operation to an entity rather than a
        tile has an audience of that entity's owner and no tile sweep at all.
        The radius is clamped to :data:`_MAX_AUDIENCE_RADIUS` and never negative.
        """
        x, y = self._record_coords(record)
        if x is None or y is None:
            return None
        radius = max(0, _as_int(_reach(record, "radius"), 0))
        if radius > self._MAX_AUDIENCE_RADIUS:
            logger.warning(
                "%s: an effect radius of %d is beyond the audience sweep limit "
                "of %d tiles; sweeping the limit",
                self.operation_kind, radius, self._MAX_AUDIENCE_RADIUS,
            )
            radius = self._MAX_AUDIENCE_RADIUS
        return (x, y, radius)

    def _area_room(self, record: OperationRecord) -> Any:
        """Return the room whose tile queries cover this operation's area.

        The driver holds no world reference of its own (R15.1), so the room is
        reached through the record's own references: the target, the originating
        building, the Carrier_Agent, and — last, because asking costs a call into
        the vector's hook — the durable owner the vector nominated. A tile in
        this game belongs to a planet-wide room, so any of them answers the same
        room, and the first that answers wins.

        ``None`` when none of them is a live object any more, which notifies the
        record's own owner and nobody else rather than raising.
        """
        for name in ("target_ref", "building_ref", "carrier_ref"):
            room = self._tile_queryable(_reach(record, name))
            if room is not None:
                return room
        return self._tile_queryable(self._resolve_persistence_owner(record))

    @staticmethod
    def _tile_queryable(candidate: Any) -> Any:
        """Return *candidate*, or its ``location``, if either answers tile queries.

        Recognized by the two coordinate queries the audience needs —
        ``get_players_at`` and ``get_objects_in_area`` — rather than by type, so
        this module needs no framework import (R15.1) and a test's room fake is
        the same shape to the driver as a real ``PlanetRoom``.
        """
        if candidate is None:
            return None
        for entity in (candidate, _reach(candidate, "location")):
            if entity is None:
                continue
            if callable(_reach(entity, "get_players_at")) or callable(
                _reach(entity, "get_objects_in_area")
            ):
                return entity
        return None

    @classmethod
    def _entity_coords(cls, entity: Any) -> tuple[int, int] | None:
        """Return *entity*'s tile as ``(x, y)``, or ``None``. No raise.

        The same two persisted fields ``world.utils.coords_of`` reads
        (``coord_x``, ``coord_y``), read duck-typed here so the driver stays
        framework-free (R15.1). An entity missing either coordinate has no place
        in an area and is left out of it.
        """
        x = _as_opt_int(cls._entity_attr(entity, "coord_x"))
        y = _as_opt_int(cls._entity_attr(entity, "coord_y"))
        if x is None or y is None:
            return None
        return (x, y)

    @staticmethod
    def _record_coords(record: Any) -> tuple[int | None, int | None]:
        """Return *record*'s affected coordinate, as far as it has one."""
        return (
            _as_opt_int(_reach(record, "target_x")),
            _as_opt_int(_reach(record, "target_y")),
        )

    def _record_owner(self, record: Any) -> Any:
        """Return the owning player of *record* as a live object, or ``None``.

        ``owner_ref`` is a *reference* by design — a dbref or an id, so the whole
        record survives a restart as a plain dict — and a reference cannot be
        notified. So this resolves an object in the order that needs no framework
        lookup: the reference itself when a vector kept a live object there, then
        the **owner of the originating building**, which the ``origin`` check
        already proved is the requesting player, then the Carrier_Agent's owner.

        ``None`` when none of them resolves, which notifies nobody and logs
        nothing: an operation whose owner has been deleted has no owner to tell.
        """
        owner = _reach(record, "owner_ref")
        if self._is_notifiable(owner):
            return owner
        for name in ("building_ref", "carrier_ref"):
            candidate = self._entity_owner(_reach(record, name))
            if self._is_notifiable(candidate):
                return candidate
        return None

    @staticmethod
    def _is_notifiable(entity: Any) -> bool:
        """Return True when *entity* is an object a notification can reach.

        A **reference is not a recipient**: a dbref string, an id, and any other
        plain value answer ``False``, so a record's ``owner_ref`` is only used as
        a recipient when a vector kept the live object there. Recognized by the
        surface the presenter's notifier and this module's own attribute reads
        need — ``msg``, ``db``, or ``attributes`` — rather than by type (R15.1).
        """
        if entity is None or isinstance(entity, (str, bytes, bool, int, float)):
            return False
        return (
            callable(_reach(entity, "msg"))
            or _reach(entity, "db") is not None
            or _reach(entity, "attributes") is not None
        )

    @staticmethod
    def _name_of(entity: Any) -> str | None:
        """Return *entity*'s display name, or ``None``. No raise.

        ``key`` first, exactly as ``BranchSystem._name_of`` reads it, so the name
        a refusal quotes and the name a notification quotes are the same name;
        ``name`` is the fallback for an owner that carries one instead, which an
        NPC base's Sentinel may.
        """
        for attr in ("key", "name"):
            found = _as_name(_reach(entity, attr))
            if found is not None:
                return found
        return None

    @classmethod
    def _distinct(cls, entities: Iterable[Any]) -> list:
        """Return *entities* with duplicates and ``None``\\ s removed, in order.

        Identity is ``.id`` where there is one and the object's own identity
        otherwise — the same basis :meth:`_is_same_entity` compares on, so "one
        notification per player" (design §4.4) survives a player reached twice
        through two different references to the same character.
        """
        seen: set = set()
        distinct: list = []
        for entity in entities or ():
            if entity is None:
                continue
            key = cls._identity_key(entity)
            if key in seen:
                continue
            seen.add(key)
            distinct.append(entity)
        return distinct

    @staticmethod
    def _identity_key(entity: Any) -> tuple[str, Any]:
        """Return the de-duplication key for *entity*: its ``.id``, else itself."""
        entity_id = _reach(entity, "id")
        if entity_id is None:
            return ("object", id(entity))
        return ("id", entity_id)

    # ------------------------------------------------------------------ #
    #  How a notification is published (R13.5, R15.1, R15.2, R15.3)
    # ------------------------------------------------------------------ #

    def _notify_each(
        self, players: Iterable[Any], kind: str, /, **data: Any
    ) -> int:
        """Publish *kind* to each distinct player in *players*. Returns the count."""
        return sum(
            1 for player in self._distinct(players)
            if self._notify(player, kind, **data)
        )

    def _notify(self, player: Any, kind: str, /, **data: Any) -> bool:
        """Publish one notification. **The single guarded call site of ``notify``.**

        Reached **duck-typed**, for the same reason every Branch service is
        (:meth:`_ask`): ``notify`` belongs to
        :class:`~world.systems.base_system.BaseSystem`, that module reaches the
        framework, and this one must import with ``evennia`` absent (R15.1) — so
        the driver cannot inherit the method it publishes through and must find
        it on the composed instance instead.

        Two failures are logged rather than raised (R15.2, R15.3):

        * **a driver with no ``notify`` at all** — a bare driver in a test, or a
          vector composed without a ``BaseSystem`` — degrades to a logged no-op,
          because a missing presenter path must not unmake an accepted operation
          or break a tick; and
        * **a publish that raises** — a subscriber failing downstream — is logged
          with a traceback and answered as "not delivered".

        Nothing here composes text (R13.5): *kind* is a key and *data* is
        structured values, and the presenter owns every word.

        Both leading arguments are **positional-only**, because every payload
        carries a ``kind`` entry of its own — the Operation_Kind — which is a
        different thing from the notification kind being published.

        Args:
            player: The recipient. ``None`` is dropped, so every caller above can
                pass an owner it could not resolve without guarding first.
            kind: One of :data:`VECTOR_NOTIFICATION_KINDS`.
            **data: The payload the design's table declares for that kind.

        Returns:
            Whether the notification was published.
        """
        if player is None:
            return False
        publish = _reach(self, "notify")
        if not callable(publish):
            logger.warning(
                "%s: no notification path is wired, so %r goes unreported to %r "
                "(R15.2)", self.operation_kind, kind, player,
            )
            return False
        try:
            publish(player, kind, **data)
        except Exception:  # noqa: BLE001 - a failed notification breaks nothing
            logger.exception(
                "%s: publishing %r to %r failed", self.operation_kind, kind, player
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    #  Per-tick advancement, isolated per operation (R8.9, R8.10) — §4.7
    # ------------------------------------------------------------------ #

    def advance_all(self, tick: int = 0) -> None:
        """Advance every operation this vector owns by one tick (R8.9, R8.10).

        The body of one ``vector_operations`` step for this vector, reached
        duck-typed from ``BranchSystem.process_tick`` — which wraps this whole
        call in a try/except of its own, so the two together give the outer ring
        (one bad *vector* cannot stop the others) and the inner ring (one bad
        *operation* cannot stop the others) of the same isolation discipline.

        **Each operation is advanced inside its own try/except, and one that
        raises is KEPT** (R8.10). That is the one place this differs from
        ``BombSystem.process_tick``, which drops a bomb that raises, and the
        difference is deliberate: a dropped operation would be a silent hazard
        leak, because the record stays persisted on its owner and the next
        rebuild would resurrect it anyway. Keeping it means a *transient* failure
        — a momentarily unresolvable reference — self-heals on the next tick,
        while a permanent one reads as a repeating log line naming the
        Operation_Kind and the ``op_id`` rather than as an operation that
        vanished. :meth:`_carrier_fatal` and :meth:`_origin_fatal` are the
        intended exits.

        The tracked list is rebuilt from a **snapshot** taken before the pass, so
        a transition that untracks its own record mid-pass cannot disturb the
        walk. Anything tracked *during* the pass — a vector's ``on_resolve``
        chaining a follow-up operation — is carried through rather than swept
        away by that rebuild, because it was never offered a tick of its own.

        The pass persists its surviving clocks in **one batched write per
        durable owner** (:meth:`_persist_many`) after the walk, rather than one
        write per record inside it: the transitions still persist themselves at
        the moment they happen, so what the batch carries is the plain
        decrements, and an owner holding many operations costs one attribute
        write per tick instead of one per operation.

        Args:
            tick: The current game tick, passed through for the log. A tick
                advances every operation by exactly one whatever it is
                *numbered*, so the value is never arithmetic here.

        Returns:
            ``None`` always. Nothing raises into the tick step.
        """
        tracked = self.tracked_records()
        if not tracked:
            return                                        # the shipped state
        known = {_as_str(_reach(record, "op_id")) for record in tracked}
        survivors: list[OperationRecord] = []
        for record in tracked:
            try:
                keep = self._advance_one(record, tick)
            except Exception:  # noqa: BLE001 - one bad operation is kept, not lost
                logger.exception(
                    "%s: advance failed for operation %s on tick %r",
                    self.operation_kind, _as_str(_reach(record, "op_id")), tick,
                )
                keep = True    # one bad tick is not a terminal state (R8.10)
            if keep:
                survivors.append(record)
        placed = [
            record for record in self.tracked_records()
            if _as_str(_reach(record, "op_id")) not in known
        ]
        self._tracked = [*survivors, *placed]
        # One batched write per durable owner covers every surviving clock
        # (R14.7): the transitions persisted their own records the moment they
        # happened, so what is left to store is the tick's plain decrements —
        # and re-storing a record a transition already persisted is the same
        # payload again, never a conflict.
        self._persist_many(survivors)

    def _advance_one(self, record: OperationRecord, tick: int = 0) -> bool:
        """Advance one operation. Returns whether to keep tracking it.

        The order is the whole of the design's §4.7, and every step of it is a
        requirement:

        1. **A terminal record does not move** (R8.2) and is dropped from
           tracking — a record that reached a terminal state between ticks, or
           one a caller tracked already finished.
        2. **The fatal conditions, before the clock.** The Carrier_Agent killed
           (R8.16) and the originating building lost (R8.17) both cancel, and
           they are checked *ahead* of the clock so a doomed operation never gets
           a free tick of progress — an operation whose building fell this tick
           must not resolve this tick.
        3. **The pause.** The carrier incapacitated or in reserve (R8.14), or the
           owner's Branch_Commitment lapsed (R8.18), suspends — and returns
           **True**, because a suspended operation is still this vector's to
           advance when its carrier comes back. Neither clock runs while it is
           paused, which is the whole of "advance that Vector_Operation no
           further".
        4. **The resume.** No condition holds and the record is Suspended, so it
           returns to Pending with the ticks it held on suspension (R8.15) — and
           then takes this tick's progress, so the delay is exactly the number of
           ticks the condition held for. A resume that could not be written
           leaves the operation paused rather than advancing it.
        5. **The bounded lifetime, then the effect clock.** The lifetime is
           decremented first, so an operation that runs out of *life* on the same
           tick its effect would land expires (R8.13) rather than resolving —
           R8.13's bound is a deadline, and a deadline that could be beaten by a
           tie would not be one. Then the effect clock, and a clock that reaches
           zero resolves (R8.11).

        Args:
            record: The operation to advance.
            tick: The current game tick, for the log only.

        Returns:
            ``True`` to keep tracking *record*, ``False`` once it has settled.
        """
        if record is None or self._settled(record):
            return False                                  # R8.2
        if (fatal := self._carrier_fatal(record)) is not None:
            self.cancel(record, fatal)                    # R8.16
            return False
        if (fatal := self._origin_fatal(record)) is not None:
            self.cancel(record, fatal)                    # R8.17
            return False
        if (pause := self._suspend_reason(record)) is not None:
            self.suspend(record, pause)                   # R8.14, R8.18
            return True
        if self._state_of(record) == str(OperationState.SUSPENDED):
            if not self.resume(record):                   # R8.15
                return True                               # still paused
        lifetime = _as_opt_int(_reach(record, "lifetime_remaining"))
        if lifetime is not None:
            record.lifetime_remaining = lifetime - 1
            if record.lifetime_remaining <= 0:
                self._expire(record)                      # R8.13
                return False
        record.ticks_remaining = _as_int(_reach(record, "ticks_remaining"), 0) - 1
        if record.ticks_remaining <= 0:
            self._resolve(record)                         # R8.11
            return False
        # No per-record persist here: the decremented clock reaches storage in
        # advance_all's one batched write per durable owner, so a tick costs
        # each owner one attribute write however many operations it holds.
        return True

    # ------------------------------------------------------------------ #
    #  What ends or pauses an operation (R8.14, R8.16, R8.17, R8.18)
    # ------------------------------------------------------------------ #
    #
    # Four conditions, read off the world every tick. Each answers a REASON KEY
    # or ``None``, never a sentence (R13.5), and each is written in the direction
    # that cannot destroy an operation over a read it could not make: a
    # reference nobody can resolve, a Branch service that is not wired, and an
    # attribute that raises all answer "no condition", so the operation survives
    # to be judged again next tick. The opposite direction would let a partially
    # wired deployment cancel every operation in flight.
    #
    # Two of the four are polled here rather than subscribed, because no event
    # announces them: an agent entering **reserve** is a plain attribute write on
    # the agent (``AgentProgression.handle_demotion``), and a **Branch_Commitment
    # lapsing** is the absence of an owned lab, which is derived rather than
    # published. Polling is therefore not a shortcut — it is the only reading
    # that cannot miss one. The three that *are* announced (a carrier killed, a
    # building destroyed, a base eliminated) are subscribed further down, because
    # each of those needs to act at the moment it happens rather than a tick
    # later, and one of them (a killed agent, which respawns immediately) would
    # be invisible to a poll altogether.

    def _carrier_fatal(self, record: OperationRecord) -> str | None:
        """Return why *record*'s Carrier_Agent ends it, or ``None`` (R8.16).

        The poll behind the carrier-death cancellation, and the backstop for the
        ``PLAYER_ELIMINATED`` subscription: an agent killed through a path that
        announces nothing, and a carrier deleted out from under a live operation,
        are both caught here.

        A carrier that is a plain **reference** rather than a live object cannot
        be judged and so ends nothing — a dbref is not a corpse.
        """
        carrier = _reach(record, "carrier_ref")
        if not self._is_world_object(carrier):
            return None
        if self._is_deleted(carrier) or self._is_dead(carrier):
            return CANCEL_CARRIER_KILLED
        return None

    def _origin_fatal(self, record: OperationRecord) -> str | None:
        """Return why *record*'s originating building ends it, or ``None`` (R8.17).

        R8.17's two conditions in one read: the building was **destroyed**, or it
        **became non-Operational** — which through ``BranchSystem.is_operational``
        folds in going offline, entering an upgrade, the Active_HQ_Rule (R11.3),
        and the owner's Branch going dormant (R5.4). Delegated rather than
        reimplemented, so the gate that let the operation launch and the gate
        that lets it continue are the same gate.

        The Branch service's default here is **True** — "Operational" — which is
        the one place in the driver a service failure is read optimistically, and
        deliberately: ``is_operational`` answers ``False`` for every input it
        cannot read, so a driver that trusted it blindly would cancel every
        operation in flight the moment its Branch_System was not reachable.
        """
        building = _reach(record, "building_ref")
        if not self._is_world_object(building):
            return None
        if self._is_deleted(building):
            return CANCEL_ORIGIN_LOST
        if not self._ask("is_operational", building, default=True):
            return CANCEL_ORIGIN_LOST
        return None

    def _suspend_reason(self, record: OperationRecord) -> str | None:
        """Return why *record* pauses, or ``None`` (R8.14, R8.18).

        The two causes, in the order the requirements number them: the
        Carrier_Agent is unavailable, or the owner's Branch_Commitment lapsed.
        Checked *after* the fatal conditions, so a killed carrier cancels rather
        than pausing.
        """
        if self._carrier_unavailable(record):
            return SUSPEND_CARRIER_UNAVAILABLE            # R8.14
        if self._commitment_lapsed(record):
            return SUSPEND_COMMITMENT_LAPSED              # R8.18
        return None

    def _carrier_unavailable(self, record: OperationRecord) -> bool:
        """Whether *record*'s carrier is incapacitated or in reserve (R8.14).

        Exactly R8.14's two states, read off the carrier itself by the same two
        attribute names every other consumer reads them by
        (``world.utils.derive_activity_status``, ``AgentSystem.process_tick``,
        ``BranchSystem.eligible_carrier``) — so a benched agent is benched for a
        Vector_Operation exactly when it is benched for its behaviour script.

        Not the whole of ``eligible_carrier``'s conjunction: being **dead** is a
        cancellation rather than a pause (R8.16), and holding a different role is
        neither, because R8.14 names these two conditions and no others.
        """
        carrier = _reach(record, "carrier_ref")
        if not self._is_world_object(carrier):
            return False
        return bool(
            self._entity_attr(carrier, "reserve")
            or self._entity_attr(carrier, "incapacitated")
        )

    def _commitment_lapsed(self, record: OperationRecord) -> bool:
        """Whether *record*'s owner has lost this vector's Branch there (R8.18).

        A Branch is live for a player on a planet only while they own that
        Branch's lab there, so this is the same ``commitment`` read the request's
        own check made — asked again every tick, because a lab can be demolished,
        destroyed, or left behind on another planet while an operation is in
        flight, and R8.18 says a dormant Branch resolves nothing.

        Three answers are **not** a lapse, and each is a read this cannot make
        rather than a commitment that is gone: a vector declaring no Branch (it
        never had one to lose), an owner that resolves to nothing (a persisted
        reference with no live object behind it), and a Branch_System that could
        not be asked at all — which is why the service is asked with a sentinel
        default instead of ``None``, since ``None`` is a *real* answer here and
        means "this owner holds no commitment".
        """
        required = _as_name(self.branch)
        if required is None:
            return False
        owner = self._record_owner(record)
        if owner is None:
            return False
        planet = _reach(record, "planet") or self._entity_planet(owner)
        held = self._ask("commitment", owner, planet, default=_UNREADABLE)
        if held is _UNREADABLE:
            return False
        return _as_name(held) != required

    # ------------------------------------------------------------------ #
    #  The transitions (R8.11, R8.13, R8.14, R8.15, R8.16, R8.17, R11.4)
    # ------------------------------------------------------------------ #
    #
    # Five transitions, one shape: settle the record's clocks, move it through
    # :meth:`_transition` (the single writer, which persists), stop tracking it
    # if it has ended, call the vector's hook for that transition, and publish
    # the notification point that reports it. Each composes nothing — the payload
    # and the audience belong to the ``_notify_*`` helpers — and each answers
    # whether it happened, so a caller that must not act twice reads the answer
    # rather than re-reading the state.
    #
    # The **sixth** transition, :meth:`_discard`, has the same shape and lives
    # with the restart rebuild at the foot of the class, because the rebuild is
    # its only caller: a record is discarded when its references no longer
    # resolve (R14.4), which is a thing only a rebuild can discover.
    #
    # The clock work sits BEFORE the state write on purpose: the write persists,
    # so a suspension's snapshot and a resume's restored clock reach storage with
    # the state that explains them, and a crash between the two cannot leave a
    # Suspended record with no snapshot to resume from.

    def suspend(self, record: OperationRecord, reason: str = "") -> bool:
        """Pause *record*, snapshotting the ticks it holds (R8.14, R8.18, R8.15).

        The snapshot is the whole of "suspension delays an operation rather than
        restarting it": :attr:`OperationRecord.suspended_ticks` keeps the effect
        clock as it stood, and :meth:`resume` puts it back. Taken before the
        state write so it persists with the state, which is what lets a
        suspension survive a restart.

        **Idempotent and quiet.** An already-suspended record is not
        re-snapshotted and not re-notified: the tick advance calls this on every
        tick the pausing condition holds, and an owner who is told once that
        their operation is paused should not be told again every tick.

        Args:
            record: The operation to pause.
            reason: :data:`SUSPEND_CARRIER_UNAVAILABLE` or
                :data:`SUSPEND_COMMITMENT_LAPSED` — a key, never a sentence
                (R13.5).

        Returns:
            Whether this call moved the record to Suspended.
        """
        if record is None or self._settled(record):
            return False
        if self._state_of(record) == str(OperationState.SUSPENDED):
            return False                                  # already paused
        record.suspended_ticks = max(0, _as_int(_reach(record, "ticks_remaining"), 0))
        if not self._transition(record, OperationState.SUSPENDED, reason=reason):
            return False
        self._run_hook("on_suspend", record)
        self._notify_suspension(record, reason)
        return True

    def resume(self, record: OperationRecord) -> bool:
        """Return *record* to Pending with the ticks it held on suspension (R8.15).

        Three things happen before the state write, in this order:

        1. **The snapshot is restored**, so the operation picks up where it
           paused rather than starting over. A record with no snapshot — one
           suspended by an older build, or hand-edited — keeps the clock it has,
           which is the conservative reading: it cannot invent a delay.
        2. **A hostile window is re-floored** through
           :meth:`_floor_response_window` (R8.8). This is the second of that
           helper's two call sites and nothing else re-checks it, so a resumed
           operation whose window had run below the floor is given the floor
           again. The floor is a ``max``, so it can only ever *lengthen* the
           warning its target gets — a suspension never shortens one.
        3. **The snapshot is cleared**, because the operation is no longer
           suspended and the field means "the ticks held at suspension".

        Then the state write, the vector's ``on_resume`` hook, and the owner's
        notification — which quotes the restored clock, because that figure *is*
        R8.15's claim.

        Returns:
            Whether this call moved the record back to Pending. ``False`` for a
            record that was not suspended, which is what lets the tick advance
            ask unconditionally.
        """
        if record is None or self._settled(record):
            return False
        if self._state_of(record) != str(OperationState.SUSPENDED):
            return False
        snapshot = _as_opt_int(_reach(record, "suspended_ticks"))
        if snapshot is not None:
            record.ticks_remaining = max(0, snapshot)
        record.ticks_remaining = self._floor_response_window(record)   # R8.8
        record.suspended_ticks = None
        if not self._transition(
            record, OperationState.PENDING, reason="carrier eligible"
        ):
            return False
        self._run_hook("on_resume", record)
        self._notify_resume(record)
        return True

    def cancel(self, record: OperationRecord, reason: str = "") -> OperationOutcome:
        """End *record* early: a collaborator was lost (R8.16, R8.17, R11.4).

        The one transition with a public caller besides the tick advance — the
        three world-event handlers below all reach the Cancelled state through
        here — so every cancellation notifies the owner the same way and names
        which collaborator was lost.

        Args:
            record: The operation to end.
            reason: :data:`CANCEL_CARRIER_KILLED`,
                :data:`CANCEL_ORIGIN_LOST`, or :data:`CANCEL_BASE_ELIMINATED` —
                a key, never a sentence (R13.5).

        Returns:
            An :class:`OperationOutcome` naming the resulting state (R8.24). A
            record that had already settled is reported as it stands, not moved:
            a cancellation racing a resolution loses, because R8.2 says the
            terminal state is final.
        """
        if record is None:
            return OperationOutcome.failed("cancel")
        if self._settled(record) or not self._transition(
            record, OperationState.CANCELLED, reason=reason
        ):
            return self._settled_outcome(record, "cancel")
        self._untrack(record)
        self._run_hook("on_cancel", record)
        self._notify_cancellation(record, reason)
        return OperationOutcome.accepted(record)

    def _resolve(self, record: OperationRecord) -> bool:
        """Apply *record*'s effect and move it to Resolved (R8.11, R8.12).

        The order R8.11 fixes — apply the effect, *then* move the record — and
        the reason the notification comes last (R8.12): the resolution audience
        is read from the world the effect has already changed, so a player whose
        building the effect just destroyed is still in it and one the effect
        moved is found where the effect left them.

        **The effect hook is guarded, and the record resolves either way.** A
        vector whose ``on_resolve`` raises has applied part of its effect at
        most; leaving the operation Pending would hand it another tick and apply
        that part again, and again, so the driver logs the failure and settles
        the record. An effect that must be all-or-nothing is the vector's to make
        so.

        The escalation ledger is noted here rather than at acceptance because
        R10.6 bounds the hostile operations one player **resolves** against one
        target: an operation that is cancelled or expires costs its owner nothing
        against the cap.
        """
        if record is None or self._settled(record):
            return False
        self._run_hook("on_resolve", record)              # R8.11 — the effect
        if not self._transition(
            record, OperationState.RESOLVED, reason="effect applied"
        ):
            return False
        self._untrack(record)
        if self._is_hostile(record):
            self._ask(                                    # R10.6
                "note_escalation",
                self._record_owner(record),
                _reach(record, "target_ref"),
            )
        self._notify_resolution(record)                   # R8.12
        return True

    def _expire(self, record: OperationRecord) -> bool:
        """*record*'s bounded lifetime elapsed before its effect (R8.13).

        R8.13's three obligations, in its own order: move the operation to
        Expired, **restore each entity it suspended** to the state that entity
        held before the suspension, and notify its owner and each affected
        entity's owner.

        The restoration runs through the vector's ``on_expire`` hook, and that is
        not a delegation of convenience: what a Signature_Vector paused is the
        vector's own knowledge — a building's behaviour, an agent's orders, a
        tile's traversability — and the Operation_Record deliberately holds
        values rather than a ledger of other objects' prior states. What the
        driver guarantees is that the hook runs on **exactly** the expiry path,
        after the state is settled and before the owners are told, and that a
        hook which raises costs the restoration rather than the expiry.
        """
        if record is None or self._settled(record):
            return False
        if not self._transition(
            record, OperationState.EXPIRED, reason="lifetime elapsed"
        ):
            return False
        self._untrack(record)
        self._run_hook("on_expire", record)               # R8.13 — restore
        self._notify_expiry(record)
        return True

    def _run_hook(self, name: str, record: OperationRecord) -> None:
        """Call one vector hook by name, logging rather than raising (R15.3).

        The single guarded call site of every lifecycle hook, so a transition
        cannot be broken by the vector's own code: a hook that raises, and a
        required hook a vector forgot to implement at all (which raises
        ``NotImplementedError``), both log with the Operation_Kind and the
        ``op_id`` and leave the transition standing.
        """
        hook = getattr(self, name, None)
        if not callable(hook):
            logger.debug(
                "%s: no %s hook to call for operation %s",
                self.operation_kind, name, _as_str(_reach(record, "op_id")),
            )
            return
        try:
            hook(record)
        except Exception:  # noqa: BLE001 - a broken hook never breaks a transition
            logger.exception(
                "%s: the %s hook failed for operation %s",
                self.operation_kind, name, _as_str(_reach(record, "op_id")),
            )

    @staticmethod
    def _state_of(record: Any) -> str | None:
        """Return *record*'s lifecycle state by value, or ``None``. No raise."""
        return _as_str(_reach(record, "state"))

    @classmethod
    def _settled(cls, record: Any) -> bool:
        """Whether *record* has reached a terminal state (R8.2).

        A **read**, and the only kind of state read the transitions make: it
        decides whether a hook or a notification should run at all, while
        :meth:`_transition` remains the sole authority on the write and refuses
        the same move independently. So the guard is duplicated on purpose — the
        read keeps a vector's effect from firing on a finished operation, and the
        write keeps a finished operation from moving.
        """
        return cls._state_of(record) in TERMINAL_STATES

    @staticmethod
    def _settled_outcome(record: Any, point: str) -> OperationOutcome:
        """Return the outcome for a transition a settled record declined (R8.24)."""
        return OperationOutcome(
            ok=False,
            state=_as_str(_reach(record, "state")),
            check=_as_str(point) or "",
            op_id=_as_str(_reach(record, "op_id")),
        )

    @classmethod
    def _is_world_object(cls, entity: Any) -> bool:
        """Whether *entity* is a live world object rather than a reference to one.

        The same predicate :meth:`_is_notifiable` reads, under the name the
        lifecycle conditions need it by: a record's four references are *values*
        by design, so a dbref string, an id, and a coordinate all answer
        ``False`` and none of them can be asked whether it is dead, benched, or
        Operational. Recognized by an object's own handlers rather than by type
        (R15.1).
        """
        return cls._is_notifiable(entity)

    @staticmethod
    def _is_deleted(entity: Any) -> bool:
        """Whether *entity* is a world object that has been deleted.

        The convention ``BombSystem._tick_one`` and ``CombatEngine._live_or_none``
        already read: a deleted Evennia object keeps its Python identity but its
        primary key becomes ``None``, while a non-framework double has no ``pk``
        at all — which the default tells apart, so a test fake is never mistaken
        for a corpse.

        An entity whose ``pk`` cannot even be read answers ``False``: this
        predicate cancels operations, and the driver does not end one over a read
        it could not make.
        """
        try:
            return getattr(entity, "pk", True) is None
        except Exception:  # noqa: BLE001 - an unreadable identity ends nothing
            return False

    @classmethod
    def _is_dead(cls, entity: Any) -> bool:
        """Whether *entity* is READABLY dead — a corpse, not an unknown.

        The inverse of ``BranchSystem._agent_is_alive``, and the same two reads
        in the same order: the existing ``is_alive()`` combat predicate first, so
        a real agent is judged dead by the rule combat judges it by, then ``hp``
        for an object exposing only the attribute. An entity that exposes
        neither, and one whose reads raise, is taken to be **alive** — this
        conjunct exists to recognize a corpse, not to demand proof of life.
        """
        checker = _reach(entity, "is_alive")
        if callable(checker):
            try:
                return not bool(checker())
            except Exception:  # noqa: BLE001 - fall through to the hp read
                logger.debug("is_alive failed for %r", entity, exc_info=True)
        hp = cls._entity_attr(entity, "hp")
        if hp is None:
            hp = _reach(entity, "hp")
        if hp is None:
            return False
        try:
            return float(hp) <= 0
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------ #
    #  The world events that drive a transition (R8.16, R8.17, R8.18, R11.4)
    # ------------------------------------------------------------------ #
    #
    # Three subscriptions, each owning one condition a poll cannot see in time:
    #
    # * ``PLAYER_ELIMINATED`` is the agent-death path — ``CombatEngine.
    #   _handle_player_defeat`` publishes it for a slain **agent** as well as a
    #   slain player, and it RESPAWNS the victim (full HP) before publishing. So
    #   a killed Carrier_Agent is alive again by the time any tick could look at
    #   it: without this subscription R8.16 would never fire at all.
    # * ``BUILDING_DESTROYED`` fires while the building is still readable and
    #   before it leaves its owner's roster, which is what lets the origin
    #   cancellation identify it (R8.17) and what makes a destroyed Branch_Lab
    #   recognizable as the commitment that is about to lapse (R8.18).
    # * ``BASE_ELIMINATED`` is the only announcement left once a whole base has
    #   been wiped: its buildings and its Sentinel are already deleted, so the
    #   record's own references no longer resolve and R11.4's cancellation has to
    #   be driven from the owner identity the payload carries.
    #
    # Every handler is keyed on the records this vector tracks — never on the
    # world at large — so a driver reacts to its own operations and to nothing
    # else, and each routes through :meth:`cancel` or :meth:`suspend` rather than
    # writing a state, so the single-writer guarantee (R8.2) covers the
    # event-driven half of the lifecycle exactly as it covers the tick.

    def _subscribe_lifecycle_events(self, event_bus: Any) -> None:
        """Subscribe the three events that end or pause an operation.

        Wired from ``__init__`` rather than at the composition root, following
        the convention every event-driven system in this codebase uses, and the
        import is **function-local** so this module stays importable with the
        game framework absent (R15.1) — exactly the shape
        ``BranchSystem._subscribe_consent_revocation`` uses.

        A bus that cannot subscribe — a bare driver with no ``BaseSystem`` half,
        a minimal test double — is a no-op rather than an error (R15.3): the
        polled conditions still run on every tick, so such a driver loses the
        promptness of the three announcements and none of the lifecycle.
        """
        subscribe = _reach(event_bus, "subscribe")
        if not callable(subscribe):
            return
        from world.event_bus import (
            BASE_ELIMINATED,
            BUILDING_DESTROYED,
            PLAYER_ELIMINATED,
        )

        for event, handler in (
            (PLAYER_ELIMINATED, self.handle_player_eliminated),
            (BUILDING_DESTROYED, self.handle_building_destroyed),
            (BASE_ELIMINATED, self.handle_base_eliminated),
        ):
            try:
                subscribe(event, handler)
            except Exception:  # noqa: BLE001 - an unwired trigger is not fatal
                logger.debug(
                    "%s: subscription to %r failed", self.operation_kind, event,
                    exc_info=True,
                )

    def handle_player_eliminated(
        self, event_name: str = "", victim: Any = None, **_payload: Any
    ) -> int:
        """A unit died: cancel every operation it was carrying (R8.16).

        ``PLAYER_ELIMINATED`` carries a slain **agent** as readily as a slain
        player, and an agent is what a Carrier_Agent is — so this is the
        agent-death path, matched on the victim being the record's own carrier
        and on nothing else. A player's own death cancels nothing: R8.16 is about
        the body sent to do the work, and a player who dies and respawns still
        owns their operations.

        Returns:
            How many operations were cancelled. ``0`` for every payload this
            vector has no operation for, which is the ordinary case.
        """
        if victim is None:
            return 0
        cancelled = 0
        for record in self.tracked_records():
            if self._settled(record):
                continue
            if not self._is_same_entity(_reach(record, "carrier_ref"), victim):
                continue
            if self.cancel(record, CANCEL_CARRIER_KILLED).ok:
                cancelled += 1
        return cancelled

    def handle_building_destroyed(
        self, event_name: str = "", building: Any = None, **_payload: Any
    ) -> int:
        """A building fell: cancel what launched from it, pause what it hosted.

        Two different losses reach this one event, and they are different
        transitions:

        * **the originating building** of an operation was destroyed, which
          cancels that operation (R8.17) — and covers R11.4's player-base case,
          where a base's buildings are removed one at a time and each publishes
          here; and
        * **this Branch's lab** was destroyed, which is the owner's
          Branch_Commitment lapsing on that planet, and suspends their
          operations there rather than ending them (R8.18) — so rebuilding the
          lab resumes an operation that was merely paused.

        The lab case is handled here rather than left to the tick's own
        commitment poll because the event fires *before* the building leaves its
        owner's roster: at this moment the commitment still reads as live, so a
        poll would answer "held" and the operations would keep advancing for one
        more tick. Recognizing the destroyed building *as* the lab needs no
        roster read at all.

        Returns:
            How many operations this changed — cancelled plus suspended.
        """
        if building is None:
            return 0
        changed = 0
        for record in self.tracked_records():
            if self._settled(record):
                continue
            if not self._is_same_entity(_reach(record, "building_ref"), building):
                continue
            if self.cancel(record, CANCEL_ORIGIN_LOST).ok:  # R8.17, R11.4
                changed += 1
        if self._is_branch_lab(building):
            changed += self._suspend_estate(                # R8.18
                self._entity_owner(building), self._entity_planet(building)
            )
        return changed

    def handle_base_eliminated(
        self,
        event_name: str = "",
        sentinel: Any = None,
        sentinel_id: Any = None,
        planet: Any = None,
        **_payload: Any,
    ) -> int:
        """A whole base was wiped: cancel every operation it launched (R11.4).

        By the time this is published the base's buildings *and* its Sentinel have
        been deleted, so a record's ``building_ref`` no longer resolves and the
        origin match the ``BUILDING_DESTROYED`` handler makes cannot be repeated
        here. The owner identity is what survives — which is why the payload
        carries ``sentinel_id``, the Sentinel's pre-delete database id — so this
        matches on ownership and, where both are readable, on the planet the base
        stood on, leaving that owner's operations elsewhere alone.

        Both halves of R11.4 are covered between the two handlers: an NPC base's
        wipe arrives here, and a player losing buildings arrives as one
        ``BUILDING_DESTROYED`` per building.

        Returns:
            How many operations were cancelled.
        """
        cancelled = 0
        for record in self.tracked_records():
            if self._settled(record):
                continue
            if not self._record_owned_by(record, sentinel, sentinel_id):
                continue
            if planet is not None and _reach(record, "planet") not in (None, planet):
                continue
            if self.cancel(record, CANCEL_BASE_ELIMINATED).ok:
                cancelled += 1
        return cancelled

    def _suspend_estate(self, owner: Any, planet: Any) -> int:
        """Pause *owner*'s operations on *planet*: their Branch lapsed (R8.18).

        Scoped to one owner and one planet because a Branch_Commitment is
        per-planet: losing the lab on one planet leaves an operation on another
        untouched. An owner nobody can resolve pauses nothing — the tick's own
        commitment poll is the backstop for that.

        Returns:
            How many operations were suspended.
        """
        if owner is None:
            return 0
        paused = 0
        for record in self.tracked_records():
            if self._settled(record):
                continue
            if not self._is_same_entity(self._record_owner(record), owner):
                continue
            if planet is not None and _reach(record, "planet") not in (None, planet):
                continue
            if self.suspend(record, SUSPEND_COMMITMENT_LAPSED):
                paused += 1
        return paused

    def _is_branch_lab(self, building: Any) -> bool:
        """Whether *building* is the Branch_Lab that establishes this vector's Branch.

        Compared by abbreviation against ``BranchSystem.lab_for_branch``, so the
        one building whose loss lapses a commitment is identified from the
        catalog rather than from a flag this driver would have to be told about.
        """
        wanted = _as_name(self._ask("lab_for_branch", _as_name(self.branch)))
        if wanted is None:
            return False
        bdef = self._building_definition(building)
        abbr = _as_name(getattr(bdef, "abbreviation", None))
        return abbr is not None and abbr.upper() == wanted.upper()

    def _record_owned_by(
        self, record: OperationRecord, owner: Any, owner_id: Any = None
    ) -> bool:
        """Whether *record* belongs to *owner*, identified by object or by id.

        Both readings are needed because the caller is a base wipe: the Sentinel
        object is already deleted, so ``.id`` reads as ``None`` and only the
        pre-delete id the payload carries can match. So a record matches when its
        ``owner_ref`` — or the owner it resolves to — *is* that object, carries
        that id, or **is** that id, which covers a vector that stored a bare
        database id as its reference.
        """
        identity = _as_opt_int(owner_id)
        if identity is None:
            identity = _as_opt_int(_reach(owner, "id"))
        for candidate in (_reach(record, "owner_ref"), self._record_owner(record)):
            if candidate is None:
                continue
            if self._is_same_entity(candidate, owner):
                return True
            if identity is None:
                continue
            if _as_opt_int(_reach(candidate, "id")) == identity:
                return True
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                if candidate == identity:
                    return True
        return False

    # ------------------------------------------------------------------ #
    #  The two sanctioned effect paths (R8.23, R9.8, R9.11, R10.1 - R10.3)
    # ------------------------------------------------------------------ #
    #
    # R8.23 permits a Vector_Operation exactly two ways to reach an entity: the
    # CombatEngine's single-hit entry point, and an append to the existing
    # ``db.active_effects`` list. These two methods are those two ways, and the
    # driver offers no third — there is no HP write, no deletion, and no
    # ownership write anywhere in this module, which is what makes R9.8's "no
    # Vector_Operation deletes a building outright and none transfers ownership"
    # structural rather than a rule each of the six vectors has to remember.
    #
    # Routing through them is also what makes a whole family of requirements
    # INHERITED rather than reimplemented, because every one of these already
    # lives inside that path: the chip-damage floor and the typed-resist axes,
    # the permanent-bonus caps, shield absorption (R9.11), the rank-gap damage
    # damper (R10.1), the rank-gap XP and loot reduction (R10.2), and kill
    # accounting crediting the responsible player (R10.3). A vector that wanted
    # to bypass them would have to write its own damage application; the driver
    # does not offer one, and a vector's ``on_resolve`` has nothing else to call.
    #
    # Both attribute the effect to the OWNING PLAYER (R8.23, R10.3) — never the
    # Carrier_Agent that delivered it and never the object it was delivered
    # through — which is the same choice ``BombSystem._apply_blast`` makes for its
    # placer, and the reason the existing kill accounting credits the player who
    # ordered the operation.

    def apply_hit(
        self,
        record: OperationRecord,
        target: Any,
        weapon: Any,
        include_attacker_bonus: bool = False,
        current_tick: int | None = None,
    ) -> int:
        """Deal *record*'s damage to *target* through the combat pipeline (R8.23).

        The first of R8.23's two paths: one call to
        ``CombatEngine.apply_direct_hit`` with the **owning player** as the
        attacker, which is the whole of R10.3 and the door every guardrail in
        R9.11 and R10.1 - R10.2 sits behind.

        The weapon is the **vector's**, not the driver's: only the vector knows
        what its Signature_Vector fires, what type it does it with, and how its
        magnitude and radius become a weapon's stats, so it hands one over —
        typically a ``SyntheticWeapon``, as the turret and thrown-explosive paths
        already do. The driver declines to invent one, because inventing a weapon
        is inventing damage.

        Four things answer ``0`` rather than raising, and none of them deals
        damage another way: no combat engine injected (R15.2), no weapon offered,
        a target that is not a live object, and an owning player who can no
        longer be resolved — that last one because an unattributable hit would
        break R10.3, and crediting the delivery mechanism instead is exactly what
        the requirement forbids.

        Args:
            record: The resolving operation, read for its owner and nothing else.
            target: The entity to hit.
            weapon: The weapon-shaped object the pipeline reads the damage off.
            include_attacker_bonus: Whether the attacker's aggregated
                ``damage_bonus`` applies. Defaults to ``False``, matching the
                thrown-explosive path: a delivered effect's magnitude is the
                vector's own arithmetic, so the owner's melee bonuses do not ride
                along unless the vector says they should.
            current_tick: The tick for lockout timing; ``None`` lets the engine
                use its own clock.

        Returns:
            The damage applied, or ``0``.
        """
        engine = self._collaborator("combat_engine")
        hit = _reach(engine, "apply_direct_hit")
        if not callable(hit):
            logger.warning(
                "%s: no combat engine is wired, so operation %s dealt no damage "
                "(R15.2)", self.operation_kind, _as_str(_reach(record, "op_id")),
            )
            return 0
        if weapon is None:
            logger.warning(
                "%s: operation %s offered no weapon; the driver does not invent "
                "one", self.operation_kind, _as_str(_reach(record, "op_id")),
            )
            return 0
        if not self._is_world_object(target):
            logger.debug(
                "%s: %r is not a live target for operation %s",
                self.operation_kind, target, _as_str(_reach(record, "op_id")),
            )
            return 0
        attacker = self._record_owner(record)             # R8.23, R10.3
        if attacker is None:
            logger.warning(
                "%s: operation %s has no resolvable owner to attribute a hit to, "
                "so it deals none", self.operation_kind,
                _as_str(_reach(record, "op_id")),
            )
            return 0
        try:
            damage = hit(
                attacker,
                target,
                weapon,
                include_attacker_bonus=bool(include_attacker_bonus),
                current_tick=current_tick,
            )
        except Exception:  # noqa: BLE001 - one bad target never breaks a resolution
            logger.exception(
                "%s: the hit from operation %s on %r failed",
                self.operation_kind, _as_str(_reach(record, "op_id")), target,
            )
            return 0
        return max(0, _as_int(damage, 0))

    def apply_effect(
        self,
        record: OperationRecord,
        target: Any,
        effect_type: str,
        damage: int = 0,
        ticks: int = 1,
    ) -> bool:
        """Attach a timed effect to *target* through the existing list (R8.23).

        The second of R8.23's two paths: one append to the **existing**
        ``db.active_effects`` list, in the shape
        ``CombatEngine.tick_effects_on_entity`` already counts down —
        ``{"type", "damage", "ticks_remaining", "source"}`` — so a Vector_Operation's
        effect is ticked, decremented, and expired by the machinery the burn and
        the poison DoT already use, and the ``effect_ticks`` step needs no
        knowledge of this feature.

        The write is a **read-append-reassign**, not an in-place mutation, which
        is what an Evennia attribute requires to persist the change — the same
        discipline ``CombatEngine._add_active_effect`` follows and the same one
        the Operation_Record persistence pair follows.

        ``source`` is the **owning player** (R8.23, R10.3), so a DoT that
        finishes a target off credits the player who ordered the operation. An
        owner who can no longer be resolved leaves the effect unattributed rather
        than undelivered, with a warning: the existing tick already tolerates a
        source that has gone (``CombatEngine._live_or_none``), and dropping the
        effect would silently unmake a resolution that has already happened.

        Args:
            record: The resolving operation, read for its owner.
            target: The entity to attach the effect to.
            effect_type: The effect key. ``"burn"`` and ``"poison"`` are the two
                the existing tick deals damage for; any other key is a **status**
                effect that counts down without damage, which is R9.8's permitted
                "temporary suspension of that building's behaviour" and is read by
                the vector that put it there.
            damage: Damage per tick, for a damaging type. Never negative.
            ticks: How many ticks the effect lasts. At least one, because an
                effect the tick would discard on sight is not an effect.

        Returns:
            Whether the effect was attached.
        """
        kind = _as_name(effect_type)
        if kind is None:
            logger.warning(
                "%s: operation %s named no effect type",
                self.operation_kind, _as_str(_reach(record, "op_id")),
            )
            return False
        store = _reach(target, "db")
        if store is None:
            logger.debug(
                "%s: %r holds no effects, so operation %s attached none",
                self.operation_kind, target, _as_str(_reach(record, "op_id")),
            )
            return False
        source = self._record_owner(record)               # R8.23, R10.3
        if source is None:
            logger.warning(
                "%s: operation %s has no resolvable owner, so its %r effect on "
                "%r is unattributed", self.operation_kind,
                _as_str(_reach(record, "op_id")), kind, target,
            )
        effects = self._existing_effects(store)
        effects.append({
            "type": kind,
            "damage": max(0, _as_int(damage, 0)),
            "ticks_remaining": max(1, _as_int(ticks, 1)),
            "source": source,
        })
        try:
            store.active_effects = effects                # reassign, never in place
        except Exception:  # noqa: BLE001 - a failed write never breaks a resolution
            logger.exception(
                "%s: operation %s could not attach a %r effect to %r",
                self.operation_kind, _as_str(_reach(record, "op_id")), kind, target,
            )
            return False
        return True

    @staticmethod
    def _existing_effects(store: Any) -> list:
        """Return *store*'s active effects as a fresh list this driver owns.

        By value, so the append lands on a container the reassignment replaces
        the stored one with. An absent list, and a hand-edited value that is not
        a list of effects at all, both read as empty — the reassignment then
        replaces the garbage with a well-formed list rather than propagating it.
        """
        existing = _reach(store, "active_effects")
        if not existing:
            return []
        if isinstance(existing, (str, bytes, Mapping)) or not isinstance(
            existing, Iterable
        ):
            logger.warning(
                "active effects on %r are a %s, not a list — replacing them",
                store, type(existing).__name__,
            )
            return []
        try:
            return [entry for entry in existing if isinstance(entry, Mapping)]
        except Exception:  # noqa: BLE001 - an uniterable container holds none
            return []

    # ------------------------------------------------------------------ #
    #  The single writer of record.state (R8.2)
    # ------------------------------------------------------------------ #

    def _transition(self, record: OperationRecord, new_state: Any, reason: str = "") -> bool:
        """Move *record* to *new_state*. **The only writer of ``record.state``.**

        Terminal-state finality (R8.2) is enforced here and nowhere else. Every
        path — resolve, expire, cancel, suspend, resume, discard, and the tick
        advance — goes through this method, and there is no second assignment to
        ``state`` anywhere in the driver, so "no advancement after a terminal
        state" is a *structural* property of the module rather than a discipline
        each path has to remember. That is what makes it directly testable, and
        what an architectural scan of this file can assert.

        Two refusals, both answered rather than raised:

        * **A terminal record does not move.** Resolved, Expired, Cancelled, and
          Discarded are final, so a late tick, a duplicate event, and a
          cancellation racing a resolution all reduce to a logged no-op instead
          of resurrecting a finished operation.
        * **An unknown state is not written.** R8.1 says an operation is
          recorded in one of six states; a typo or a stale name would put a
          record outside the lifecycle where nothing would ever advance or
          rebuild it, so the writer declines it and keeps the current state.

        An accepted transition **persists immediately** (:meth:`_persist`), so
        the durable record and the in-memory one never disagree about the state
        — a crash between the two would otherwise resurrect a resolved
        operation on the next rebuild.

        Args:
            record: The operation to move.
            new_state: The target state — an :class:`OperationState` member or
                the plain string naming one.
            reason: Why, for the log. Structured data for a player belongs in a
                notification, not here (R13.5).

        Returns:
            ``True`` when the state was written and persisted, ``False`` for
            either refusal. A caller that must not act twice reads the answer.
        """
        if record is None:
            return False
        op_id = _as_str(getattr(record, "op_id", None))
        current = _as_str(getattr(record, "state", None))
        target = _as_str(new_state)
        if target not in _STATE_NAMES:
            logger.warning(
                "%s: refusing to move operation %s to unknown state %r; "
                "keeping %s (R8.1)",
                self.operation_kind, op_id, new_state, current,
            )
            return False
        if current in TERMINAL_STATES:
            logger.debug(
                "%s: %s is terminal (%s); ignoring -> %s",
                self.operation_kind, op_id, current, target,
            )
            return False
        record.state = target
        logger.debug(
            "%s: %s %s -> %s%s",
            self.operation_kind, op_id, current, target,
            f" ({reason})" if reason else "",
        )
        self._persist(record)
        return True

    # ------------------------------------------------------------------ #
    #  Persistence
    # ------------------------------------------------------------------ #

    def _persist(self, record: OperationRecord) -> None:
        """Write *record* through to its durable owner (R14.1, R14.7).

        Read-copy-write, always: the whole container is replaced, because an
        Evennia attribute does not observe an in-place change to a list it handed
        out. The transitions call this one record at a time; the tick advance
        persists its surviving clocks through :meth:`_persist_many` instead, so
        an owner is written once per tick however many operations it holds. The
        matching, appending, and terminal-removal semantics live in
        :meth:`_persist_owner`, which both paths share: an owner's container
        holds exactly its live operations, and the last one going terminal
        leaves the owner with an empty container.

        Answers nothing and raises nothing (R15.3): a vector whose
        :meth:`persistence_owner` fails, an owner with no attribute handler, and
        a handler whose write fails are all logged and shrugged off, because a
        failed persist must not break the tick — or the transition — that
        triggered it.
        """
        self._persist_many([record])

    def _persist_many(self, records: Iterable[Any]) -> None:
        """Write *records* through, with **one read-copy-write per owner** (R14.7).

        The batching behind the tick: :meth:`advance_all` hands every surviving
        record here at the end of a pass, so an owner holding N operations costs
        one attribute read and one attribute write per tick instead of N of
        each — the read-copy-write discipline is unchanged, only the batch size
        is. A single-record call (every transition) reduces to exactly the old
        per-record write.

        Records are grouped by the durable owner the vector nominates. A record
        with no ``op_id``, and one whose owner cannot be resolved, is logged and
        skipped — it costs that record its persistence and nothing else, the
        same posture the single-record path always took.
        """
        groups: dict[int, tuple[Any, dict[str, Any]]] = {}
        for record in records or ():
            if record is None:
                continue
            op_id = _as_str(getattr(record, "op_id", None))
            if not op_id:
                logger.debug(
                    "%s: not persisting a record with no op_id",
                    self.operation_kind,
                )
                continue
            owner = self._resolve_persistence_owner(record)
            if owner is None:
                continue
            _, group = groups.setdefault(id(owner), (owner, {}))
            group[op_id] = record
        for owner, group in groups.values():
            self._persist_owner(owner, group)

    def _persist_owner(self, owner: Any, records: Mapping[str, Any]) -> None:
        """One read-copy-write of *owner*'s whole container, covering *records*.

        Each record's entry is matched **by ``op_id``** and updated in place
        within the stored list, so persisting the same operation repeatedly
        neither duplicates it nor reshuffles its neighbours; a record not yet
        stored is appended in offered order; and a **terminal record is
        removed** rather than stored — the rebuild already skips one (R8.22),
        so keeping it would only grow the attribute without bound.
        """
        pending: dict[str, list[dict[str, Any]]] = {}
        for op_id, record in records.items():
            keep = _as_str(getattr(record, "state", None)) not in TERMINAL_STATES
            pending[op_id] = _records_payload([record], owner) if keep else []
        stored: list[dict[str, Any]] = []
        written: set[str] = set()
        for entry in self._read_records(owner):
            entry_id = _as_str(entry.get("op_id"))
            if entry_id in pending:
                if entry_id not in written:
                    stored.extend(pending[entry_id])
                    written.add(entry_id)
                continue
            stored.append(entry)
        for op_id, payload in pending.items():
            if payload and op_id not in written:
                stored.extend(payload)
        self._write_records(owner, stored)

    def _resolve_persistence_owner(self, record: OperationRecord) -> Any:
        """Return the vector's nominated durable owner for *record*, or ``None``.

        The guarded call site of the :meth:`persistence_owner` hook. A vector
        that has not implemented it, or whose implementation raises on a record
        whose references have gone, degrades to "this operation persists
        nowhere" with a logged traceback rather than taking down the transition
        that asked.
        """
        try:
            return self.persistence_owner(record)
        except Exception:  # noqa: BLE001 - an unresolvable owner persists nothing
            logger.exception(
                "%s: persistence_owner failed for operation %s",
                self.operation_kind, _as_str(getattr(record, "op_id", None)),
            )
            return None

    # ------------------------------------------------------------------ #
    #  The restart rebuild (R8.22, R14.3, R14.4, R14.5) — design §4.9
    # ------------------------------------------------------------------ #
    #
    # A restart empties the tracked list and nothing else: the records persist on
    # the durable owners the vector nominated, so recovery is a *read*. The
    # rebuild reads them back, decides which are still operations, turns each
    # one's references into live objects again, and hands the survivors to the
    # tick loop — which is the whole of R8.22's "each rebuilt Vector_Operation
    # resumes advancing".
    #
    # Three failure directions, and they are deliberately different:
    #
    # * a record that **cannot be parsed** costs that record and nothing else
    #   (R14.5) — one corrupt payload must not cost a player every operation they
    #   had in flight;
    # * a record whose **references have gone** is Discarded, with a log naming
    #   the Operation_Kind and each missing reference (R14.4) — it can never
    #   resolve, so keeping it would leave a hazard that no tick could finish;
    # * a reference nobody could **look up at all** discards nothing. That is the
    #   same posture the lifecycle conditions take (§"What ends or pauses an
    #   operation"): the driver does not destroy an operation over a read it
    #   could not make, so a rebuild handed no world leaves the references as the
    #   values they were and lets the clock judge the operation.

    #: The record references R14.4 discards over, in the order it names them.
    #: Read as a tuple so the log and the discard reason list them the same way
    #: every time.
    _RESOLVED_REFS: tuple[str, ...] = (
        "owner_ref",       # the owning player
        "building_ref",    # the originating building
        "carrier_ref",     # the Carrier_Agent
        "target_ref",      # the target entity
    )

    def rebuild(self, planet_rooms: Any) -> int:
        """Re-track every non-terminal operation from its persisted records.

        The restart half of persistence (R8.22): the vector's
        :meth:`discover_records` sweep says where to look, the persistence pair
        reads each owner's container, and this decides what each payload becomes.
        Called once per vector at server start, from the composition root,
        alongside the existing ``BombSystem.rebuild_from_world`` call and isolated
        per vector so one broken vector does not stop the others.

        **Idempotent, structurally** (R14.3). The tracked map is keyed by
        ``op_id``, so a dict cannot hold a duplicate: rebuilding twice over the
        same persisted state yields the same tracked set as rebuilding once, and
        so does one record reached through two owners a sweep yielded twice. The
        identity does the work rather than a "have I run yet" flag, which would
        be a second piece of state to get wrong.

        What each record can become, in the order this decides:

        1. **Nothing, logged** — the payload could not be parsed, or some later
           step of that one record's rebuild failed (R14.5). The remaining
           records are still rebuilt and the log names the Operation_Kind. The
           read path already drops a non-mapping entry before it gets here, so
           this is the second line of defence rather than the first.
        2. **Nothing, silently** — the record is terminal (R8.2). It is not an
           operation any more, and :meth:`_persist` already sweeps a terminal
           record out of its owner's container, so this is only reached for one
           written by an older build or by hand.
        3. **Discarded** — a reference no longer exists (R14.4). See
           :meth:`_discard`.
        4. **Tracked** — its references resolved, so it goes back on the tick
           loop with the clocks it was persisted with.

        Args:
            planet_rooms: The world the rebuild walks, in whatever shape the
                composition root hands it — the mapping of planet key to
                ``PlanetRoom`` in this game. Passed to the vector's sweep hook
                and used to resolve the records' references (see
                :meth:`_ref_resolver`). ``None`` is supported and means no
                reference can be looked up, which discards nothing.

        Returns:
            How many operations this vector is now tracking. ``0`` for a vector
            whose sweep hook is unimplemented or raises, which is logged rather
            than raised (R15.3) — a composition root must finish booting.
        """
        resolve = self._ref_resolver(planet_rooms)
        tracked: dict[str, OperationRecord] = {}
        for owner in self._discovered_owners(planet_rooms):
            for raw in self._read_records(owner):
                try:
                    record = self._rebuild_one(raw, resolve)
                except Exception:  # noqa: BLE001 - one bad record costs one record
                    logger.exception(
                        "%s: a rebuild step failed for one record on %r; "
                        "recovering the remaining records (R14.5)",
                        self.operation_kind, owner,
                    )
                    continue
                if record is None:
                    continue
                tracked[_as_str(record.op_id) or new_op_id()] = record  # R14.3
        self._tracked = list(tracked.values())
        logger.debug(
            "%s: rebuilt %d operation(s) from persistence",
            self.operation_kind, len(self._tracked),
        )
        return len(self._tracked)

    def _rebuild_one(
        self, raw: Any, resolve: Any = None
    ) -> OperationRecord | None:
        """Return the record *raw* rebuilds into, or ``None`` if it is not one.

        The per-record body of :meth:`rebuild`, in its own method because the
        loop guards it as a unit: R14.5's "a rebuild step fails for one
        Operation_Record" is *any* step of it, not only the parse, so parsing,
        judging the state, resolving the references, and the discard transition
        all fail the same way — one logged record, and the rest recovered.

        Raises:
            Exception: Whatever the steps raise — :meth:`rebuild` is the handler.
                :meth:`OperationRecord.from_dict` raises ``TypeError`` on a
                corrupt (non-mapping) payload, which is the case R14.5 exists
                for.
        """
        record = OperationRecord.from_dict(raw)
        if self._settled(record):
            return None                                   # R8.2, R8.22
        missing = self._resolve_refs(record, resolve)
        if missing:
            self._discard(record, missing)                # R14.4
            return None
        return record

    def _discovered_owners(self, planet_rooms: Any) -> list:
        """Return the durable owners the vector's sweep hook names.

        The guarded call site of :meth:`discover_records`. A vector that has not
        implemented it (which raises ``NotImplementedError``), one whose sweep
        raises, and one that answers something that cannot be iterated all
        degrade to "no owners" with a logged traceback — so an unfinished vector
        rebuilds nothing rather than stopping a server start (R15.2, R15.3).
        """
        try:
            owners = self.discover_records(planet_rooms)
        except Exception:  # noqa: BLE001 - an unswept vector rebuilds nothing
            logger.exception(
                "%s: discover_records failed, so nothing was rebuilt",
                self.operation_kind,
            )
            return []
        if owners is None:
            return []
        try:
            return list(owners)
        except Exception:  # noqa: BLE001 - an uniterable answer names no owners
            logger.exception(
                "%s: discover_records answered something that could not be "
                "iterated, so nothing was rebuilt", self.operation_kind,
            )
            return []

    # ------------------------------------------------------------------ #
    #  Turning a persisted reference back into a live object (R14.4)
    # ------------------------------------------------------------------ #
    #
    # Why this matters beyond R14.4's discard. Every condition that ends or
    # pauses an operation gates on :meth:`_is_world_object` — a dbref is not a
    # corpse, a dbref is not a demolished building — so an operation rebuilt with
    # its references still spelled as values would be judged by its clock alone:
    # R8.16's carrier death, R8.17's lost origin, and R8.18's lapsed commitment
    # would all be dead for it until it resolved. Resolving the references here is
    # what carries those triggers across a restart.
    #
    # The lookup is **duck-typed through the world the rebuild was handed**
    # (R15.1): this module holds no world reference of its own and imports no
    # framework, so the id-to-object bridge is an index built from the rooms
    # ``rebuild`` was given — the same place ``BombSystem.rebuild_from_world``
    # looks, and every object a record can reference (a player, a building, an
    # agent, a target) lives directly in its planet's room.

    def _resolve_refs(self, record: Any, resolve: Any = None) -> list[str]:
        """Replace *record*'s references with live objects; name the missing ones.

        Mutates *record* in place — this is the one place a record stops holding
        values and starts holding objects, and it happens on the way back from
        persistence rather than on the way to it.

        Each of :data:`_RESOLVED_REFS` lands in one of four states:

        * **already live** — a vector kept the object itself; left as it is, and
          reported missing only if that object has since been **deleted**, which
          is positive evidence that it is gone and needs no lookup at all;
        * **absent** — the record names nothing. Reported missing, because an
          operation with no owner, no originating building, or no Carrier_Agent
          is not an operation the contract describes (R7.1, R8.21). The one
          exception is the **target**, which a vector aiming at a *tile* rather
          than an entity legitimately leaves unset — so an absent target is
          missing only when the record names no target coordinate either, and is
          therefore aimed at nothing at all;
        * **resolved** — the reference read as an id and the world holds that
          object; the field is replaced with it;
        * **gone** — the reference read as an id and the world does not hold it.
          Reported missing (R14.4).

        And the fifth case, which is not a state of the reference but of the
        lookup: a reference nobody could **look up** — no world was handed in, or
        the value cannot be read as an id — is left exactly as it is and reported
        as nothing. The driver does not discard an operation over a read it could
        not make; such a record is tracked and judged by its clock, which is a
        degraded lifecycle rather than a destroyed operation (R15.2).

        Args:
            record: The record to resolve, normally one :meth:`rebuild` has just
                read back out of persistence.
            resolve: The lookup :meth:`_ref_resolver` built. ``None`` means no
                lookup is possible, which is the "cannot judge" case above.

        Returns:
            The names of the missing references, in :data:`_RESOLVED_REFS` order.
            Empty when the record is whole, which is what :meth:`rebuild` reads
            as "track it".
        """
        missing: list[str] = []
        for name in self._RESOLVED_REFS:
            ref = _reach(record, name)
            if self._is_world_object(ref):
                if self._is_deleted(ref):
                    missing.append(name)                  # positively gone
                continue
            if ref is None:
                if not self._ref_optional(record, name):
                    missing.append(name)
                continue
            answer = resolve(ref) if callable(resolve) else _UNREADABLE
            if answer is _UNREADABLE:
                continue                                  # nothing could judge it
            if answer is None or self._is_deleted(answer):
                missing.append(name)                      # R14.4
                continue
            try:
                setattr(record, name, answer)
            except Exception:  # noqa: BLE001 - an unwritable field keeps its value
                logger.debug(
                    "%s: %s could not be replaced with the object it names",
                    self.operation_kind, name, exc_info=True,
                )
        return missing

    @classmethod
    def _ref_optional(cls, record: Any, name: str) -> bool:
        """Whether *record* may legitimately leave the reference *name* unset.

        Only the target, and only for an operation aimed at a **tile**: design §7
        makes the target coordinate and the target entity alternatives, so a
        record naming a coordinate is aimed at something even with no entity
        reference, while one naming neither is aimed at nothing and cannot be
        rebuilt into a working operation.
        """
        if name != "target_ref":
            return False
        x, y = cls._record_coords(record)
        return x is not None and y is not None

    def _ref_resolver(self, planet_rooms: Any) -> Any:
        """Return ``resolve(ref)`` for the world *planet_rooms* describes.

        A closure rather than a dict, for two reasons: the index is built **once
        per rebuild** however many references need it, and it is built **lazily**,
        so a vector with no persisted records pays nothing for the world walk.

        The three answers, and the caller depends on all three being distinct:

        * a **live object** — the reference resolved;
        * ``None`` — the world was searched and holds no such object, which is
          R14.4's "no longer exists";
        * :data:`_UNREADABLE` — nothing could be searched, or the reference
          cannot be read as an id, so nothing may be concluded from it.
        """
        index: dict[int, Any] | None = None

        def resolve(ref: Any) -> Any:
            nonlocal index
            ref_id = _as_ref_id(ref)
            if ref_id is None:
                return _UNREADABLE            # not a reference this can read
            if index is None:
                index = self._world_index(planet_rooms)
            if not index:
                return _UNREADABLE            # no world to look it up in
            return index.get(ref_id)

        return resolve

    @classmethod
    def _world_index(cls, planet_rooms: Any) -> dict[int, Any]:
        """Return ``{database id: live object}`` for everything in *planet_rooms*.

        The id-to-object bridge, built duck-typed from the rooms the rebuild was
        handed so this module needs no framework lookup and no injected
        repository (R15.1). Every object an Operation_Record can reference — the
        owning player, the originating building, the Carrier_Agent, the target —
        stands on a tile of its planet's room and is therefore in that room's
        contents; a character *inside* a building is a flag on the character in
        this game, not a change of location, so it is in there too.

        Deleted objects are left out (a deleted object *is* a missing
        reference), the first object claiming an id keeps it, and an empty result
        is the "no world" answer :meth:`_ref_resolver` reads as "conclude
        nothing" — so a rebuild over an unreadable world discards nothing.
        """
        index: dict[int, Any] = {}
        for room in cls._world_rooms(planet_rooms):
            for entity in cls._room_entities(room):
                if entity is None or cls._is_deleted(entity):
                    continue
                entity_id = _as_ref_id(_reach(entity, "id"))
                if entity_id is None:
                    continue
                index.setdefault(entity_id, entity)
        return index

    @staticmethod
    def _world_rooms(planet_rooms: Any) -> list:
        """Return the rooms *planet_rooms* names, in whatever shape it came in.

        The mapping of planet key to room the composition root holds, a plain
        sequence of rooms, or a single one — read the same way
        ``BombSystem.rebuild_from_world`` reads it, so the composition root hands
        both rebuilds the same argument. ``None``, a string, and anything that
        cannot be iterated answer no rooms rather than raising.
        """
        if planet_rooms is None or isinstance(planet_rooms, (str, bytes)):
            return []
        values = _reach(planet_rooms, "values")
        try:
            if callable(values):
                return list(values())
            if isinstance(planet_rooms, Iterable):
                return list(planet_rooms)
        except Exception:  # noqa: BLE001 - a world nobody can walk holds no rooms
            logger.debug(
                "the world offered to the rebuild could not be walked",
                exc_info=True,
            )
            return []
        return [planet_rooms]

    @staticmethod
    def _room_entities(room: Any) -> list:
        """Return the objects standing in *room*, or none.

        ``contents`` is the room surface every Evennia room exposes and the one
        ``BombSystem.rebuild_from_world`` falls back to. A plain collection is
        accepted as its own contents, so a caller holding a flat list of world
        objects can hand it over directly.
        """
        if isinstance(room, (list, tuple, set, frozenset)):
            return list(room)
        contents = _reach(room, "contents")
        if not contents:
            return []
        try:
            return list(contents)
        except Exception:  # noqa: BLE001 - unreadable contents hold nothing
            logger.debug("a room's contents could not be read", exc_info=True)
            return []

    # ------------------------------------------------------------------ #
    #  The sixth transition (R14.4)
    # ------------------------------------------------------------------ #

    def _discard(self, record: OperationRecord, missing: Any = ()) -> bool:
        """Discard *record*: a reference it needs no longer exists (R14.4).

        The rebuild's transition, in the same shape as the other five: move the
        record through :meth:`_transition` (the single writer of ``record.state``,
        which persists — and because Discarded is terminal, that persist *removes*
        the record from its owner's container, so the next rebuild does not have
        to discard it again), stop tracking it, call the vector's hook, and
        publish the notification that reports it.

        R14.4's log is here rather than in the caller, because this is the point
        the discard becomes true: it names the Operation_Kind, the identity, and
        **each** missing reference, which is the only place those names are
        recorded — :meth:`_notify_discard`'s payload deliberately carries the kind
        alone, since the coordinate, the building, and the carrier are exactly the
        values that could not be resolved.

        ``on_discard`` is the one lifecycle hook reachable with a half-missing
        record, which is why it is documented as having to tolerate one: some of
        *record*'s references may still be the unresolvable values they were
        read as.

        Args:
            record: The operation to end.
            missing: The reference names that could not be resolved, as
                :meth:`_resolve_refs` reported them.

        Returns:
            Whether this call moved the record to Discarded. ``False`` for a
            record the single writer declined — an already-terminal one, so a
            rebuild that reads the same dangling record twice reports it once.
        """
        if record is None:
            return False
        if not self._transition(
            record, OperationState.DISCARDED, reason="rebuild"
        ):
            return False
        logger.warning(
            "%s: discarded operation %s on rebuild — these references no longer "
            "exist: %s (R14.4)",
            self.operation_kind, _as_str(_reach(record, "op_id")),
            ", ".join(_as_str(name) or "?" for name in (missing or ())) or "unknown",
        )
        self._untrack(record)
        self._run_hook("on_discard", record)
        self._notify_discard(record)
        return True
