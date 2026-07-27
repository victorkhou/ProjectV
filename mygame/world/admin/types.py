"""
Core types for the unified admin CRUD adapter layer.

Frozen dataclasses (matching the ``world.data_registry`` /
``world.definitions`` style) describing everything the shared verb handlers
need per entity type: modifiable-field declarations (``FieldSpec``), list
rows (``InstanceRow``), bounded-write results (``SetResult``), ``show``
readouts (``ShowReport``), and the ``EntityAdapter`` Protocol every
per-entity adapter implements.

The two CRUD planes share one grammar: instance scope (live objects) and
definition scope (YAML-backed defs in ``DataRegistry``), pivoted by the
``def`` keyword. ``CORE_VERBS`` is the verb set every adapter must support
or explicitly opt out of (with a reason) at registration time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

#: The verb set every EntityAdapter must support or explicitly opt out of
#: (with a non-empty reason). Enforced by AdapterRegistry.register at server
#: startup (Requirement 1.1). The ``def *`` entries are the Definition_Scope
#: verbs dispatched through the ``def`` keyword pivot.
CORE_VERBS: frozenset[str] = frozenset(
    {
        "list",
        "spawn",
        "show",
        "set",
        "destroy",
        "def list",
        "def show",
        "def set",
        "def reset",
        "def diff",
    }
)

#: Fields that identify a definition entry, in the order they are tried
#: (matches how DataRegistry keys its registry dicts and how the overlay
#: document names entries): items/technologies/powerups by ``key``,
#: buildings by ``abbreviation``, terrain by ``terrain_type``, then a
#: generic ``name`` fallback; ``planet_key`` names a CoordinateSpaceDef
#: (the ``@planet`` def surface), which carries none of the others.
#: ``key`` still wins for every def that has one, since it is tried first.
#: Planets never enter the overlay pipeline, so ``planet_key`` is inert for
#: overlay lookups but lets the router name a planet definition.
DEF_ID_FIELDS: tuple[str, ...] = (
    "key", "abbreviation", "terrain_type", "name", "planet_key",
)


@dataclass(frozen=True)
class FieldSpec:
    """One modifiable field on an entity (instance or definition plane).

    Bounds may be static (``min_value``/``max_value``), dynamic
    (``dynamic_bounds`` computed from the target entity's current state,
    e.g. item roll bands varying per ItemDef), or unbounded (all three
    ``None``). ``perm`` escalates an individual field above its verb's
    permission tier (checked after the verb-level check, before bounds).
    """

    name: str  # e.g. "level", "hp_max", "damage"
    kind: str  # "int" | "float" | "str" | "enum"
    min_value: float | None = None  # None = unbounded low
    max_value: float | None = None  # None = unbounded high
    perm: str = "Builder"  # "Builder" | "Admin" — per-field tier
    #: Optional: (entity) -> (lo, hi), computed from current entity state
    #: before clamping; used by item roll bands which vary per ItemDef.
    dynamic_bounds: Callable[[Any], tuple[float, float]] | None = None
    #: Valid values for kind == "enum" (e.g. rarity tiers).
    enum_values: tuple[str, ...] | None = None


def resolve_bounds(
    spec: "FieldSpec", entity: Any
) -> tuple[float | None, float | None]:
    """The ``(lo, hi)`` bounds for *spec* against *entity*'s current state.

    The single source of the dynamic-vs-static bound selection shared by
    the clamp (``clamp_field_value``), the ``show`` bound rendering
    (``_render_bounds``), and adapters' defensive re-clamps: dynamic bounds
    (``spec.dynamic_bounds(entity)``, e.g. item roll bands / hp→hp_max) take
    precedence when present and an *entity* is available (Requirement 3.4);
    otherwise the static ``min_value``/``max_value`` (either may be ``None``
    = unbounded on that side).
    """
    if spec.dynamic_bounds is not None and entity is not None:
        return spec.dynamic_bounds(entity)
    return spec.min_value, spec.max_value


@dataclass(frozen=True)
class InstanceRow:
    """One row of ``list`` output; also the resolution-cache (#N) entry."""

    index: int  # the #N the admin can use (1-based)
    key: str  # stable identifier (dbref, agent id, alliance id)
    name: str
    summary: str  # one-line list rendering
    ref: Any  # handle to the live object


@dataclass(frozen=True)
class SetResult:
    """Outcome of a bounded ``set`` write (clamp-with-note contract, D2).

    Contract (Requirements 3.2, 3.3):
    - ``applied`` always lands within the field's (possibly dynamic) bounds
      when ``ok``.
    - ``clamped == (applied != requested)`` for numeric fields; a clamped
      result's response notes the applied value and the bounds.
    """

    ok: bool
    field: str
    requested: Any
    applied: Any  # == requested unless clamped
    clamped: bool  # True => note "(clamped to X; bounds lo–hi)"
    error: str | None = None

    @classmethod
    def fail(cls, field: str, requested: Any, error: str) -> "SetResult":
        """A failed write: nothing applied, nothing clamped, just the reason.

        The canonical refusal shape every adapter's ``update`` shares
        (unknown field, un-coercible value, write-path error) — ``ok=False``
        with ``applied=None`` and ``clamped=False`` so a failure never claims
        a value landed. Collapses the repeated six-field construction to one
        call at each refusal site.
        """
        return cls(ok=False, field=field, requested=requested,
                   applied=None, clamped=False, error=error)


@dataclass(frozen=True)
class CreateResult:
    """Outcome of a ``spawn`` (create) through an adapter's creation path.

    The canonical shape every adapter shares (Requirement 1.x, DRY): ``ok``
    with the created ``instance`` (a single object, a list, or an adapter-
    specific descriptor), or not-``ok`` with a relayable ``error``. The
    router duck-types ``.ok``/``.error``/``.instance``; opted-out adapters
    return it purely as a typed refusal.
    """

    ok: bool
    instance: Any = None
    error: str | None = None


@dataclass(frozen=True)
class DeleteResult:
    """Outcome of a ``destroy`` through an adapter's deletion path.

    Canonical shape: ``ok``, or not-``ok`` with a relayable ``error``.
    """

    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class ShowReport:
    """Uniform ``show`` readout rendered by the shared handler.

    ``fields`` entries are ``(spec, value, is_override)`` — the modifiable
    fields block rendered as ``field: value [min–max] (perm)``, with
    definition overrides flagged ``*override*``. ``staleness_note`` warns
    (instance plane) when stamped attributes differ from the current merged
    definition values (Requirement 10.3).
    """

    header: str  # identity + location/owner
    state_lines: list[str]  # current live state
    fields: list[tuple[FieldSpec, Any, bool]]  # (spec, value, is_override)
    staleness_note: str | None = None


class GrammarContract(Protocol):
    """The verb-grammar contract every adapter declares (Requirement 1.1).

    Pure data the AdapterRegistry validates at startup and the router reads
    to build its dispatch table: which core verbs are supported, which are
    opted out (with a reason), plus extra verbs and migration aliases. No
    behavior — segregated from the two CRUD planes so a def-only adapter
    (terrain/powerup/planet) satisfies this and the definition plane without
    carrying live-instance semantics it never implements (ISP).
    """

    entity_key: str  # "item", "building", "agent", "tech", ...

    #: Core verbs this adapter supports (must, with opt_outs, cover CORE_VERBS).
    supported_verbs: frozenset[str]
    #: Core verb -> human-readable opt-out reason (non-empty after trimming).
    opt_outs: dict[str, str]
    #: Extra verb name -> help text (e.g. {"open": "Open shop menu"}).
    extra_verbs: dict[str, str]
    #: Migration alias: old spelling -> canonical verb (e.g. "stats" -> "show").
    aliases: dict[str, str]


class InstancePlane(Protocol):
    """The live-instance CRUD surface (``list``/``spawn``/``show``/``set``/
    ``destroy``): target resolution, the instance field schema, and hooks
    into the entity's REAL existing creation/read/write/delete paths.

    Def-only adapters opt every instance verb out; their implementations
    are unreachable stubs (see :class:`DefOnlyAdapter`).
    """

    # --- target resolution (instance plane) ---
    def list_instances(self, caller: Any, filter_str: str) -> list[InstanceRow]:
        """Return the live instances matching *filter_str* as indexed rows."""
        ...

    def resolve_instance(self, caller: Any, token: str) -> Any:
        """Resolve *token* per the uniform grammar to a live instance.

        Grammar: ``#N`` index into the caller's last ``list`` for this
        entity type, case-sensitive exact key, case-insensitive exact name,
        unambiguous case-insensitive prefix; trailing ``[player]`` argument
        scopes the search and defaults to the caller where applicable.
        """
        ...

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable fields on live instances, keyed by field name."""
        ...

    # --- instance CRUD hooks (delegate to REAL system paths) ---
    def create(self, caller: Any, def_token: str, kwargs: dict) -> Any:
        """``spawn``: create through the entity's existing creation path."""
        ...

    def read(self, caller: Any, instance: Any) -> ShowReport:
        """``show``: full readout of a resolved live instance."""
        ...

    def update(self, caller: Any, instance: Any, field: str, value: Any) -> SetResult:
        """``set``: bounded write through the existing single-writer path."""
        ...

    def delete(self, caller: Any, instance: Any) -> Any:
        """``destroy``: delete through the entity's existing deletion path."""
        ...


class DefinitionPlane(Protocol):
    """The YAML-backed definition surface (``def *`` verbs): the overridable
    definition field schema and access to the domain's registry dict +
    token resolution. Entities with no definition surface return ``None``
    from :meth:`def_registry_dict` and opt the def verbs out.
    """

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Overridable fields on definitions (``def set``), keyed by name."""
        ...

    def def_registry_dict(self) -> Mapping[str, Any] | None:
        """The domain's registry dict (e.g. ``DataRegistry.items``).

        ``None`` means the entity has no definition surface (the def verbs
        must then be opted out with a reason).
        """
        ...

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a definition token, delegating to the existing
        ``DataRegistry.resolve_*`` key/name/prefix matchers."""
        ...


class EntityAdapter(GrammarContract, InstancePlane, DefinitionPlane, Protocol):
    """Per-entity-type descriptor; one per entity, held by AdapterRegistry.

    Composed (ISP) from three segregated Protocols — the
    :class:`GrammarContract` (verb data), the :class:`InstancePlane` (live
    objects), and the :class:`DefinitionPlane` (YAML-backed defs) — so the
    shared verb handlers depend only on the surface they use, while a full
    adapter still satisfies the union. Routers stay thin; behavior
    differences live in adapter data.
    """
