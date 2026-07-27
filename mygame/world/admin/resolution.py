"""
Resolution_Engine and List_Cache for the unified admin CRUD layer.

Implements the uniform target-resolution grammar shared by every
``@<entity>`` admin router (design: "Target Resolution (uniform grammar)"):

1. ``#N`` — 1-based index into the caller's most recent ``list`` output for
   this entity type (the per-caller, per-entity List_Cache, replaced on the
   next ``list`` invocation).
2. Case-sensitive exact key match.
3. Case-insensitive exact name match.
4. Case-insensitive prefix match against both keys and names.

Tiers are tried in order and resolution stops at the FIRST tier yielding at
least one candidate. Multiple candidates at that tier is an error listing
every candidate — resolution never guesses (Requirement 2.3). No candidates
at any tier is a not-found error (Requirements 2.8, 10.7).

A trailing ``[player]`` argument scopes resolution to that player's
holdings, defaulting to the caller when omitted; a player token that does
not resolve to exactly one player is an error identifying the token
(Requirements 2.4, 2.9).

Definition-scope tokens delegate to the existing ``DataRegistry.resolve_*``
key/name/prefix matchers (Requirement 2.6).

Every resolver here returns an explicit :class:`Resolution` (success with a
target, or failure with a relayable error message) instead of raising, so
router handlers can hand the message to the admin verbatim and guarantee "no
state change" on failure. All resolution is a pure function of
(token, cached list, registry state) — identical inputs always produce
identical results (Requirement 2.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from world.admin.types import InstanceRow

__all__ = [
    "Resolution",
    "ListCache",
    "LIST_CACHE",
    "caller_key",
    "resolve_index_token",
    "resolve_instance_token",
    "resolve_player_scope",
]


def caller_key(caller: Any) -> Any:
    """A stable per-caller key: the dbid when available, else identity.

    The single definition shared by the List_Cache (keying cached ``list``
    rows) and the router's pending-destroy confirmations, so the same
    caller maps to the same key across both.
    """
    cid = getattr(caller, "id", None)
    return cid if cid is not None else id(caller)


# ------------------------------------------------------------------ #
#  Resolution result (explicit success-or-error, never raises)
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class Resolution:
    """Outcome of one target resolution.

    ``ok`` with a ``target``, or not-``ok`` with a human-readable ``error``
    the router relays verbatim. ``candidates`` carries the ambiguous-match
    descriptions when more than one candidate matched at the first yielding
    tier (Requirement 2.3), for callers that want them programmatically —
    they are already embedded in ``error``.
    """

    ok: bool
    target: Any = None
    error: str | None = None
    candidates: tuple[str, ...] = ()


def _resolved(target: Any) -> Resolution:
    return Resolution(ok=True, target=target)


def _failed(error: str, candidates: Sequence[str] = ()) -> Resolution:
    return Resolution(ok=False, error=error, candidates=tuple(candidates))


# ------------------------------------------------------------------ #
#  List_Cache — per-caller, per-entity-type cached ``list`` rows
# ------------------------------------------------------------------ #

class ListCache:
    """The per-caller, per-entity-type cache of ``list`` output rows.

    Each ``list`` invocation replaces the caller's cache for that entity
    type with exactly the displayed rows (an empty result stores an empty
    row set — Requirements 4.1, 4.6). ``#N`` tokens index into these rows
    1-based.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[Any, str], tuple[InstanceRow, ...]] = {}

    @staticmethod
    def _caller_key(caller: Any) -> Any:
        """A stable per-caller key: the dbid when available, else identity."""
        return caller_key(caller)

    def store(self, caller: Any, entity_key: str,
              rows: Sequence[InstanceRow]) -> None:
        """Replace the caller's cached rows for *entity_key* with *rows*."""
        self._rows[(self._caller_key(caller), entity_key)] = tuple(rows)

    def get(self, caller: Any, entity_key: str) -> tuple[InstanceRow, ...] | None:
        """The caller's cached rows, or ``None`` when no ``list`` ran yet."""
        return self._rows.get((self._caller_key(caller), entity_key))

    def clear(self, caller: Any | None = None,
              entity_key: str | None = None) -> None:
        """Drop cached rows — everything, one caller's, or one entry."""
        if caller is None:
            self._rows.clear()
            return
        ckey = self._caller_key(caller)
        if entity_key is not None:
            self._rows.pop((ckey, entity_key), None)
            return
        for k in [k for k in self._rows if k[0] == ckey]:
            del self._rows[k]


#: Module-level singleton used by the routers; tests may construct their own.
LIST_CACHE = ListCache()


# ------------------------------------------------------------------ #
#  ``#N`` index resolution
# ------------------------------------------------------------------ #

#: The ``#N`` index form — ``#`` followed only by digits. Anything else
#: (``#abc``, ``#1x``) is NOT an index token and falls through to the
#: key/name/prefix tiers.
_INDEX_RE = re.compile(r"^#(\d+)$")


def _default_is_stale(ref: Any) -> bool:
    """Whether a cached row's live object no longer exists.

    Deleted Evennia typeclassed objects keep the Python handle but null out
    ``pk``; objects without a ``pk`` attribute (test doubles, non-DB
    handles) are assumed live.
    """
    if ref is None:
        return True
    if hasattr(ref, "pk"):
        return ref.pk is None
    return False


def resolve_index_token(
    n: int,
    rows: Sequence[InstanceRow] | None,
    *,
    is_stale: Callable[[Any], bool] | None = None,
) -> Resolution:
    """Resolve a ``#N`` token against the caller's cached rows.

    - No cache at all → instruct the caller to run ``list`` first
      (Requirement 10.1).
    - ``n`` < 1 or past the end → error stating the valid index range
      (Requirements 2.7, 10.6).
    - The row's live object no longer exists → the cache is stale;
      instruct the caller to re-run ``list`` (Requirement 10.2).
    """
    if rows is None:
        return _failed(
            "No cached list for this entity type — run `list` first."
        )
    if not rows:
        return _failed(
            f"Index #{n} is out of range — the last `list` matched no "
            "instances (no valid indexes); re-run `list`."
        )
    if n < 1 or n > len(rows):
        return _failed(
            f"Index #{n} is out of range — valid indexes are "
            f"#1–#{len(rows)}."
        )
    row = rows[n - 1]
    stale_check = is_stale if is_stale is not None else _default_is_stale
    if stale_check(row.ref):
        return _failed(
            f"#{n} ({row.name}) no longer exists — the cached list is "
            "stale; re-run `list`."
        )
    return _resolved(row.ref)


# ------------------------------------------------------------------ #
#  Tiered key/name/prefix resolution (instance plane)
# ------------------------------------------------------------------ #

def _describe(row: InstanceRow) -> str:
    """One candidate as shown in an ambiguity error: ``key (name)``."""
    return f"{row.key} ({row.name})" if row.name != row.key else row.key


def _first_matching_tier(
    token: str, candidates: Sequence[InstanceRow]
) -> list[InstanceRow]:
    """Candidates from the FIRST tier that yields any match (Req 2.2).

    Tier order: case-sensitive exact key, case-insensitive exact name,
    case-insensitive prefix against both keys and names. Returns an empty
    list when no tier matches.
    """
    lowered = token.lower()

    # Tier 1: case-sensitive exact key.
    matches = [row for row in candidates if row.key == token]
    if matches:
        return matches

    # Tier 2: case-insensitive exact name.
    matches = [row for row in candidates if row.name.lower() == lowered]
    if matches:
        return matches

    # Tier 3: case-insensitive prefix against both keys and names.
    return [
        row
        for row in candidates
        if row.key.lower().startswith(lowered)
        or row.name.lower().startswith(lowered)
    ]


def resolve_instance_token(
    token: str,
    *,
    rows: Sequence[InstanceRow] | None,
    candidates: Sequence[InstanceRow],
    is_stale: Callable[[Any], bool] | None = None,
) -> Resolution:
    """Resolve an instance-plane target *token* per the uniform grammar.

    ``rows`` is the caller's List_Cache for this entity type (``None`` when
    no ``list`` ran yet) and only serves ``#N`` tokens. ``candidates`` is
    the full candidate set for the key/name/prefix tiers — the adapter
    builds it from the applicable scope (e.g. a player's holdings).

    Pure function of its inputs (Requirement 2.5): no side effects, no
    caller messaging, deterministic for identical (token, rows, candidates).
    """
    token = (token or "").strip()
    if not token:
        return _failed("No target given.")

    index_match = _INDEX_RE.match(token)
    if index_match:
        return resolve_index_token(
            int(index_match.group(1)), rows, is_stale=is_stale
        )

    matches = _first_matching_tier(token, candidates)
    if not matches:
        return _failed(f"No match found for '{token}'.")
    if len(matches) > 1:
        described = [_describe(row) for row in matches]
        return _failed(
            f"'{token}' is ambiguous — matches: {', '.join(described)}. "
            "Use a longer prefix or a #N index.",
            candidates=described,
        )
    return _resolved(matches[0].ref)


# ------------------------------------------------------------------ #
#  Trailing [player] scoping
# ------------------------------------------------------------------ #

def resolve_player_scope(caller: Any, player_token: str | None) -> Resolution:
    """Resolve the trailing ``[player]`` scope argument.

    Omitted (``None``/empty) defaults the scope to the caller
    (Requirement 2.4). A supplied token must resolve to exactly one player
    — zero or several matches is an error identifying the token
    (Requirement 2.9). Uses the quiet search path so no messaging happens
    as a side effect; the router relays the returned error itself.
    """
    if player_token is None or not player_token.strip():
        return _resolved(caller)
    token = player_token.strip()

    search = getattr(caller, "search", None)
    if search is None:
        return _failed(f"Could not resolve player '{token}'.")
    try:
        matches = search(token, quiet=True, global_search=True)
    except TypeError:
        # Test doubles with a bare search(name) signature: a single hit or
        # None (their own messaging, if any, is theirs to manage).
        matches = search(token)
    if matches is None:
        matches = []
    elif not isinstance(matches, (list, tuple)):
        matches = [matches]

    if len(matches) == 1:
        return _resolved(matches[0])
    if not matches:
        return _failed(f"Could not resolve player '{token}'.")
    names = ", ".join(str(getattr(m, "key", m)) for m in matches)
    return _failed(
        f"Player token '{token}' matches more than one player: {names}."
    )
