"""
ResourceAdapter — the ``@resource`` EntityAdapter (unified-admin-crud Phase 3).

Migrates the resource admin surface onto the unified grammar
(Requirements 1.5, 11.5, 11.6). ``@resource`` manages the per-player
resource *balances* (Wood, Stone, Iron, …), not a spawnable collection of
world instances — so its grammar row is unusual (design per-entity matrix):

- **``spawn`` is the grant path** (the ``give`` old spelling maps onto it —
  design "A" = alias). ``spawn <type|all> <amount> [player]`` credits a
  balance additively through the character's existing ``add_resource``
  single-writer (admins bypass the carry-weight cap — Req 16.7). The
  ``give`` Migration_Alias keeps working with a deprecation note. Both
  the positional grant grammar and the additive semantics don't fit the
  base ``spawn <def> [k=v] [player]`` parser, so the ROUTER subclass keeps
  the legacy parsing/messages and delegates the credit to :meth:`create`
  (see ``CmdAdminResource._sub_spawn``, modeled on ``@outpost``).
- **NEW ``show``**: a balances readout (every canonical resource, with the
  modifiable-fields block). ``set``/``show`` default their target to the
  caller (``me``/``self``/empty) — the legacy "defaults to you" behavior.
- **NEW ``set``**: an absolute balance write. Each canonical resource is
  an int Field_Spec floored at 0 (a balance never goes negative); the
  bounded-write clamp/note/audit come from the shared handler. The write
  routes through ``add_resource`` with a computed delta so it stays on the
  same single-writer path grants use.
- **``reset`` extra verb** (Admin+): reset one player — or, with no
  target, every player — to ``STARTING_RESOURCES`` (the legacy bulk path).
- **``list``/``destroy`` and the whole ``def`` scope are OPTED OUT** with
  reasons pointing at the supported path — balances are per-player fields,
  not a listable/spawnable/deletable instance collection, and they have no
  YAML definition domain.

No live game is required to construct or register the adapter — target
resolution reaches the world only lazily (via the caller's own ``search``
through the shared player-scope resolver).
"""

from __future__ import annotations

from typing import Any

from world.admin.resolution import Resolution, resolve_player_scope
from world.admin.types import (
    CreateResult,
    DeleteResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
)
from world.constants import RESOURCE_TYPES

#: The canonical resource names as a stable tuple (Title-case, matching
#: STARTING_RESOURCES / Character.get_resource and how balances render
#: everywhere in the game). These ARE the settable instance fields.
_RESOURCE_TYPES: tuple[str, ...] = tuple(RESOURCE_TYPES)

#: token (lower) -> canonical resource name, for case-insensitive grants.
_CANONICAL: dict[str, str] = {r.lower(): r for r in _RESOURCE_TYPES}

#: Instance-plane opt-out reasons (each names the supported alternative).
_NO_LIST_REASON = (
    "resources are per-player balances, not a listable instance collection "
    "— use '@resource show <player>' (defaults to you) for one player's "
    "balances, or '@player list' for the player roster"
)
_NO_DESTROY_REASON = (
    "balances are fields on a player, not deletable instances — zero a "
    "balance with '@resource set <player> <type> 0', or '@resource reset "
    "<player>' to restore starting resources"
)

#: Reason shared by all five opted-out definition verbs.
_NO_DEF_DOMAIN_REASON = (
    "resources have no YAML definition domain — balances are live per-player "
    "state; use '@resource show <player>' / '@resource set <player> <type> "
    "<amount>' (target defaults to you)"
)


class ResourceAdapter:
    """EntityAdapter for player resource balances (the ``@resource`` surface).

    Constructing and registering the adapter needs no live game; player
    resolution reaches the world only when a verb actually runs.
    """

    entity_key = "resource"

    # --- grammar contract (design per-entity matrix row for @resource) ---
    supported_verbs = frozenset({"spawn", "show", "set"})
    opt_outs: dict[str, str] = {
        "list": _NO_LIST_REASON,
        "destroy": _NO_DESTROY_REASON,
        "def list": _NO_DEF_DOMAIN_REASON,
        "def show": _NO_DEF_DOMAIN_REASON,
        "def set": _NO_DEF_DOMAIN_REASON,
        "def reset": _NO_DEF_DOMAIN_REASON,
        "def diff": _NO_DEF_DOMAIN_REASON,
    }
    #: The bulk reset extra verb (Admin+); handler lives on the router as
    #: ``sub_reset`` (Requirement 1.6).
    extra_verbs: dict[str, str] = {
        "reset": "Reset player(s) to starting resources",
    }
    #: Migration alias (Requirement 11.5): the legacy grant spelling. The
    #: positional ``give <type|all> <amount> [player]`` grammar is preserved
    #: by the router's ``_sub_spawn`` override; here it simply points at the
    #: canonical grant verb.
    aliases: dict[str, str] = {"give": "spawn"}
    #: Verb-tier: ``reset`` was Admin-gated (bulk/destructive); ``spawn``
    #: (grant) and ``set`` stay at the Builder floor, matching the legacy
    #: ``give`` tier. ``set`` fields are floored at 0, so the worst a
    #: Builder can do is zero a balance — no more than a grant already
    #: allowed on the economy.
    verb_perms: dict[str, str] = {"reset": "Admin"}

    # ------------------------------------------------------------------ #
    #  Resource vocabulary (shared by the router's grant grammar)
    # ------------------------------------------------------------------ #

    @staticmethod
    def resource_names() -> tuple[str, ...]:
        """The canonical resource names (for grant validation messages)."""
        return _RESOURCE_TYPES

    @staticmethod
    def resolve_resources(token: str) -> list[str] | None:
        """Resolve a grant token to the resource(s) it names.

        ``all`` (case-insensitive) expands to every canonical resource; any
        other token must match a known resource name case-insensitively.
        Returns ``None`` for an unknown token — the router rejects it rather
        than minting a junk resource (the reported "give all" bug that once
        created a resource literally named ``all``).
        """
        text = (token or "").strip().lower()
        if text == "all":
            return list(_RESOURCE_TYPES)
        canonical = _CANONICAL.get(text)
        return [canonical] if canonical is not None else None

    # ------------------------------------------------------------------ #
    #  Field schema (instance plane)
    # ------------------------------------------------------------------ #

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Every canonical resource as an int Field_Spec floored at 0.

        A balance is a non-negative integer with no upper cap (admins grant
        freely — Req 16.7); the shared bounded-write handler clamps a
        below-zero ``set`` up to 0 with a note.
        """
        return {
            name: FieldSpec(name=name, kind="int", min_value=0,
                            perm="Builder")
            for name in _RESOURCE_TYPES
        }

    def definition_fields(self) -> dict[str, FieldSpec]:
        """No definition plane: resources have no YAML domain."""
        return {}

    # ------------------------------------------------------------------ #
    #  Balance access helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _balance(target: Any, resource: str) -> int:
        """Best-effort current balance of *resource* on *target* (0 when
        absent). Prefers the ``get_resource`` API, falls back to the raw
        ``db.resources`` mapping (the canonical storage)."""
        getter = getattr(target, "get_resource", None)
        if callable(getter):
            try:
                return int(getter(resource))
            except Exception:  # noqa: BLE001 - reads never break a verb
                pass
        res = getattr(getattr(target, "db", None), "resources", None) or {}
        try:
            return int(res.get(resource, 0))
        except Exception:  # noqa: BLE001 - malformed store reads as 0
            return 0

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """``list`` is opted out — balances are not a listable roster."""
        return []

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* to a player whose balances to read/write.

        An empty token — or ``me``/``self`` — resolves to the caller (the
        legacy "defaults to you" behavior). Any other token resolves through
        the shared player-scope resolver: zero matches is a not-found error
        naming the token, several is an ambiguity error (Requirement 2.9).
        """
        text = (token or "").strip()
        if not text or text.lower() in ("me", "self"):
            return Resolution(ok=True, target=caller)
        return resolve_player_scope(caller, text)

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn`` (grant): credit *amount* of the resolved resource(s).

        The router has already parsed and validated the grant grammar and
        resolved the target; ``kwargs`` carries ``amount`` (a positive int),
        ``target`` (the resolved player), and ``resources`` (the resolved
        canonical names). The credit routes through the target's existing
        ``add_resource`` single-writer (admin grant — bypasses the
        carry-weight cap per Req 16.7). Returns the granted description for
        the router's response; a target without ``add_resource`` is a
        refusal with no state change.
        """
        target = kwargs.get("target")
        amount = kwargs.get("amount")
        resources = kwargs.get("resources") or self.resolve_resources(def_token)
        if not resources:
            valid = ", ".join(_RESOURCE_TYPES)
            return CreateResult(
                ok=False,
                error=(f"Unknown resource '{def_token}'. "
                       f"Valid: {valid} (or 'all')."),
            )
        adder = getattr(target, "add_resource", None)
        if not callable(adder):
            name = str(getattr(target, "key", None) or "target")
            return CreateResult(
                ok=False, error=f"{name} is not a valid player character.",
            )
        for resource in resources:
            adder(resource, amount)
        granted = "all resources" if len(resources) > 1 else resources[0]
        return CreateResult(
            ok=True,
            instance={"granted": granted, "amount": amount, "target": target},
        )

    def read(self, caller: Any, target: Any) -> ShowReport:
        """``show``: identity header, a compact balances line, and every
        resource as a modifiable field (Requirement 4.3)."""
        name = str(getattr(target, "key", None) or "player")
        balances = {r: self._balance(target, r) for r in _RESOURCE_TYPES}

        non_zero = [f"{r} {v}" for r, v in balances.items() if v]
        state_lines = [
            "Balances: " + (", ".join(non_zero) if non_zero else "(all zero)")
        ]
        fields = [
            (spec, balances.get(spec.name, 0), False)
            for spec in self.instance_fields().values()
        ]
        return ShowReport(
            header=f"{name} — resource balances",
            state_lines=state_lines,
            fields=fields,
            staleness_note=None,  # no definition domain to drift from
        )

    def update(self, caller: Any, target: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: write an absolute balance (Requirements 3.5, 3.10).

        The router already coerced and clamped *value* into the field's
        bounds (>= 0); this writes the balance by crediting the delta from
        the current value through ``add_resource`` (the same single-writer
        grants use), so the result lands exactly on *value*. A player
        without the resource API falls back to a direct ``db.resources``
        write (the canonical storage). No state change on failure.
        """
        name = str(getattr(target, "key", None) or "player")
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a settable resource; settable: {valid}",
            )
        try:
            applied = int(value)
        except (TypeError, ValueError):
            return SetResult.fail(
                field, value,
                f"value must be a whole number (got '{value}')",
            )

        try:
            adder = getattr(target, "add_resource", None)
            if callable(adder):
                # Credit the delta so the balance lands exactly on `applied`,
                # staying on the same single-writer path grants use.
                adder(field, applied - self._balance(target, field))
            else:
                db = getattr(target, "db", None)
                if db is None:
                    return SetResult.fail(
                        field, value,
                        f"{name} is not a valid player character.",
                    )
                res = dict(getattr(db, "resources", None) or {})
                res[field] = applied
                db.resources = res
        except Exception as exc:  # noqa: BLE001 - relay write-path failures
            return SetResult.fail(
                field, value, f"could not write {field} onto {name}: {exc}"
            )

        return SetResult(ok=True, field=field, requested=applied,
                         applied=applied, clamped=False)

    def delete(self, caller: Any, target: Any) -> DeleteResult:
        """``destroy`` is opted out — balances are not deletable."""
        return DeleteResult(ok=False, error=_NO_DESTROY_REASON)

    # ------------------------------------------------------------------ #
    #  Definition scope (opted out — no YAML definition domain)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> None:
        """Resources have no definition registry (def scope opted out)."""
        return None

    def def_resolve(self, token: str) -> None:
        """No definition domain — nothing to resolve."""
        return None
