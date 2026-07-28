"""
Structured outcomes for the admin routers.

A router says two things at once: it tells the *operator* something in prose,
and it makes a *decision* (this field was clamped to that bound; this tier was
required and the caller lacked it). Until now only the prose existed, so a test
that wanted to check the decision had to read the sentence — which coupled
several hundred assertions to wording nobody considered load-bearing.

An :class:`Outcome` is the decision, recorded alongside the message. The prose
is unchanged and remains what the operator sees; ``kind`` plus ``data`` is what
tests assert on. Rewording a message now touches the message only.

This extends the pattern already used by
:mod:`world.presenters.notification_presenter`, where a ``kind`` + payload is
turned into text by a formatter table — the same split, applied to the admin
plane. The direction of travel differs on purpose: the presenter *renders* from
structured data, while these routers still build their strings inline and record
the decision next to it. Recording first keeps this additive; moving the
rendering behind a formatter table is a later step and does not change what
tests assert.

Kinds recorded here (see the constants below):

``PERM_DENIED``
    A permission gate refused. ``required`` is the tier demanded; ``scope`` is
    ``"verb"`` or ``"field"``, and ``target`` names the verb or field.

``UNKNOWN_FIELD``
    A field name was not in the adapter's schema. ``valid`` is the sorted list
    of names that were offered, and ``plane`` is ``"instance"`` or
    ``"definition"``.

``FIELD_SET``
    A field write landed. ``clamped`` says whether the bound moved the value,
    with ``requested``/``applied`` and the ``lo``/``hi`` in force — recorded on
    every successful set, not just clamped ones, so "did not clamp" is
    assertable as a fact rather than as the absence of a word.
"""

from __future__ import annotations

import dataclasses
from typing import Any

#: A permission gate refused the operation.
PERM_DENIED = "perm_denied"

#: A field name was not present in the adapter's schema.
UNKNOWN_FIELD = "unknown_field"

#: A field write landed (clamped or not).
FIELD_SET = "field_set"


@dataclasses.dataclass(frozen=True)
class Outcome:
    """One router decision: a *kind* plus the facts behind it.

    ``data`` is a plain dict rather than per-kind subclasses — the payload
    shape varies by kind and only tests read it, so a schema would cost more
    than it buys. Frozen because an outcome is a record of something that
    already happened.
    """

    kind: str
    data: dict

    def __getitem__(self, key: str) -> Any:
        """``outcome["field"]`` — the common read is one payload key."""
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class OutcomeRecorder:
    """Mixin giving a command an ``outcomes`` log.

    Commands are instantiated per invocation, so the list is created lazily on
    first use rather than in an ``__init__`` this mixin would have to own (the
    routers sit on Evennia's ``Command``, whose constructor signature is not
    ours to change).
    """

    @property
    def outcomes(self) -> list:
        """Every decision this invocation recorded, in order."""
        existing = getattr(self, "_outcomes", None)
        if existing is None:
            existing = []
            self._outcomes = existing
        return existing

    def record_outcome(self, kind: str, **data) -> None:
        """Record one decision. Never raises: a failure to record must not
        change what the operator sees."""
        self.outcomes.append(Outcome(kind, data))

    # --- reads, for tests ---------------------------------------------- #

    def outcomes_of(self, kind: str) -> list:
        """Every recorded outcome of *kind*."""
        return [o for o in self.outcomes if o.kind == kind]

    def last_outcome(self, kind: str = None):
        """The most recent outcome, optionally of *kind*; ``None`` if none."""
        found = self.outcomes_of(kind) if kind else self.outcomes
        return found[-1] if found else None
