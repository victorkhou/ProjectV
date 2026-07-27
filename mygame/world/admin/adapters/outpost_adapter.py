"""
OutpostAdapter — the `@outpost` EntityAdapter (unified-admin-crud Phase 3).

Migrates the NPC-base admin surface onto the unified grammar
(Requirements 11.5, 11.6):

- **`list` keeps its instance meaning** (design per-entity matrix: the
  ``@outpost list`` meaning is unchanged — active NPC bases tracked by the
  ``OutpostSpawnerSystem``). Rows come from the spawner's
  ``_active_bases`` records and feed the ``#N`` List_Cache.
- **NEW ``show``/``set``/``destroy``** through the outpost spawner paths:
  ``show`` renders one base record; ``set`` exposes the one mutable piece
  of base state the spawner itself writes — the ``disturbed_at`` staleness
  stamp (mirroring ``OutpostSpawnerSystem.on_combat_action``'s record +
  sentinel write); ``destroy`` wipes the base AS A UNIT (Sentinel + all
  owned buildings/guards, no respawn) via the spawner's existing
  ``wipe_bases_in_area`` admin-clear path.
- **``tiers`` → ``def list``** Migration_Alias (Requirement 11.5): the
  spawnable base tiers ARE the definition domain, served from
  ``DataRegistry.base_templates`` (the optional ``outposts.yaml``).
- **``def set``/``def reset`` are OPTED OUT**: base templates load through
  ``DataRegistry._load_base_templates`` — an optional file OUTSIDE the
  overlay merge step (which only covers ``_REQUIRED_FILES`` domains), so
  an overlay override would "reload OK" without ever reaching
  ``base_templates``. Following what the data actually supports, the
  write verbs point at editing ``outposts.yaml`` + ``@reboot`` instead.
  ``def diff`` stays supported (the ``outposts`` overlay domain is always
  empty — an empty overlay produces an empty diff, Requirement 5.6).
- **Registry/spawner access is lazy**: injected doubles first (tests),
  then the services facade, then the DataRegistry singleton — nothing in
  construction/registration needs a running game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from world.admin.adapters._support import live_registry, live_service
from world.admin.resolution import (
    LIST_CACHE,
    Resolution,
    resolve_instance_token,
)
from world.admin.types import (
    CORE_VERBS,
    CreateResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
)
from world.utils import coords_of, set_obj_attr


@dataclass(frozen=True)
class OutpostBase:
    """One live NPC base as the adapter's instance handle.

    Wraps the spawner's active-base record (the SAME dict tracked in
    ``_active_bases``, so ``set`` writes are live) with the name/key
    attributes the shared router handlers render.
    """

    key: str        # display key (sentinel name, else the tier)
    name: str
    base_key: Any   # the spawner's _active_bases key (sentinel id)
    record: dict    # the live tracking record


#: Reason shared by the opted-out definition-write verbs (see module doc).
_DEF_WRITE_OPT_OUT = (
    "outpost base templates (outposts.yaml) load outside the overlay "
    "merge pipeline, so overrides would never apply — edit "
    "data/definitions/outposts.yaml and run @reboot instead"
)


class OutpostAdapter:
    """EntityAdapter for NPC bases (the ``@outpost`` admin surface).

    Tests may inject a registry and/or spawner double; otherwise both are
    resolved lazily per call (services facade, then — for the registry —
    the DataRegistry singleton), so construction never needs a booted
    server and a hot-reload is picked up immediately.
    """

    entity_key = "outpost"
    #: Overlay/definition domain. The domain exists only for the read
    #: surface (``def diff`` over an always-empty overlay domain); the
    #: write verbs are opted out because load_all never merges it.
    def_domain = "outposts"

    # --- grammar contract (design per-entity matrix row for @outpost) ---
    supported_verbs = frozenset(CORE_VERBS - {"def set", "def reset"})
    opt_outs: dict[str, str] = {
        "def set": _DEF_WRITE_OPT_OUT,
        "def reset": _DEF_WRITE_OPT_OUT,
    }
    extra_verbs: dict[str, str] = {}
    #: The legacy tier listing IS the definition listing (Requirement 11.5).
    aliases: dict[str, str] = {"tiers": "def list"}

    def __init__(self, registry: Any | None = None,
                 spawner: Any | None = None) -> None:
        self._registry = registry
        self._spawner_double = spawner

    # ------------------------------------------------------------------ #
    #  Lazy system access (no live game required to construct)
    # ------------------------------------------------------------------ #

    def _live_registry(self) -> Any | None:
        """Injected double, else services facade, else the singleton."""
        return live_registry(self._registry)

    def _spawner(self) -> Any | None:
        """The OutpostSpawnerSystem: injected double, else the facade."""
        return live_service("outpost_spawner", self._spawner_double)

    # ------------------------------------------------------------------ #
    #  Tier (definition) resolution
    # ------------------------------------------------------------------ #

    def tier_names(self) -> list[str]:
        """The stable, sorted base-template tier names.

        The SAME sorted order ``def list`` numbers, so an index the
        operator typed maps to exactly the row they saw (legacy
        ``@outpost tiers``/``spawn <N>`` behavior preserved).
        """
        registry = self._live_registry()
        templates = getattr(registry, "base_templates", None) if registry \
            else None
        if not templates:
            return []
        return sorted(templates.keys())

    @staticmethod
    def _parse_index(token: str) -> int | None:
        """A 1-based ``#N``/``N`` index token, or None when not one."""
        body = token[1:] if token.startswith("#") else token
        if body.isdigit():
            n = int(body)
            return n if n >= 1 else None
        return None

    def resolve_tier(self, token: str) -> str | None:
        """Resolve *token* to a tier name (legacy resolution preserved).

        Accepts an index (``#2``/``2`` from the tier listing), an exact
        tier name, or an unambiguous prefix — case-insensitive. With no
        template metadata loaded (e.g. a minimal test spawner) falls back
        to the raw lowercased token and lets the spawner validate it.
        """
        token = str(token or "").strip()
        if not token:
            return None
        tiers = self.tier_names()
        if not tiers:
            return token.lower()
        index = self._parse_index(token)
        if index is not None:
            return tiers[index - 1] if index <= len(tiers) else None
        norm = token.lower()
        if norm in tiers:
            return norm
        prefixed = [t for t in tiers if t.startswith(norm)]
        if len(prefixed) == 1:
            return prefixed[0]
        return None

    # ------------------------------------------------------------------ #
    #  Field schemas
    # ------------------------------------------------------------------ #

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable live-base fields.

        ``disturbed_at`` is the one piece of base state the spawner itself
        mutates after spawn (its staleness clock): 0 = pristine (no timer),
        >0 = the tick the base was first disturbed. Writable so an admin
        can arm/steer/clear a base's staleness refresh timer.
        """
        return {
            "disturbed_at": FieldSpec(
                name="disturbed_at", kind="int", min_value=0,
                perm="Builder",
            ),
        }

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Empty: ``def set``/``def reset`` are opted out (templates load
        outside the overlay merge — see the module docstring)."""
        return {}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _base_name(base_key: Any, rec: dict) -> str:
        """A base's display name: its sentinel's key, else the tier."""
        sentinel = rec.get("sentinel")
        name = getattr(sentinel, "key", None) if sentinel is not None else None
        return str(name) if name else str(rec.get("tier", base_key))

    def _row(self, index: int, base_key: Any, rec: dict) -> InstanceRow:
        """One active base as an InstanceRow (list output + #N cache)."""
        name = self._base_name(base_key, rec)
        tier = str(rec.get("tier", "?"))
        bits = [name] if name != tier else []
        bits.append(tier)
        x, y = rec.get("x"), rec.get("y")
        if x is not None and y is not None:
            bits.append(f"at ({x}, {y})")
        bits.append(f"on {rec.get('planet', '?')}")
        if rec.get("disturbed_at"):
            bits.append("[disturbed]")
        return InstanceRow(
            index=index, key=name, name=name, summary=" ".join(bits),
            ref=OutpostBase(key=name, name=name, base_key=base_key,
                            record=rec),
        )

    @staticmethod
    def _matches_filter(rec: dict, name: str, filt: str) -> bool:
        """Lenient list filter: tier, planet, or name substring."""
        return (
            filt in str(rec.get("tier", "")).lower()
            or filt in str(rec.get("planet", "")).lower()
            or filt in name.lower()
        )

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Active NPC bases the spawner is tracking, as indexed rows."""
        spawner = self._spawner()
        bases = getattr(spawner, "_active_bases", None) if spawner else None
        if not bases:
            return []
        filt = (filter_str or "").strip().lower()
        rows: list[InstanceRow] = []
        for base_key, rec in bases.items():
            name = self._base_name(base_key, rec)
            if filt and not self._matches_filter(rec, name, filt):
                continue
            rows.append(self._row(len(rows) + 1, base_key, rec))
        return rows

    def _candidate_rows(self, caller: Any) -> list[InstanceRow]:
        return self.list_instances(caller, "")

    def _is_stale(self, ref: Any) -> bool:
        """A cached row is stale when its base is no longer tracked."""
        if not isinstance(ref, OutpostBase):
            return ref is None
        spawner = self._spawner()
        bases = getattr(spawner, "_active_bases", None) if spawner else None
        return not (bases and ref.base_key in bases)

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar: ``#N`` indexes the
        caller's outpost List_Cache; key/name/prefix tiers run over the
        spawner's currently tracked bases."""
        rows = LIST_CACHE.get(caller, self.entity_key)
        return resolve_instance_token(
            (token or "").strip(), rows=rows,
            candidates=self._candidate_rows(caller),
            is_stale=self._is_stale,
        )

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (delegating to the REAL spawner paths)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn``: place a base via ``OutpostSpawnerSystem.spawn_base``.

        Kwargs: ``planet`` (default the caller's ``coord_planet``) and
        ``coords`` (default the caller's tile; ``None`` lets the spawner
        pick a valid placement). Returns the spawner's base record as the
        created instance.
        """
        spawner = self._spawner()
        if spawner is None:
            return CreateResult(ok=False, error="Outpost spawner unavailable")

        tier = self.resolve_tier(str(def_token))
        if tier is None:
            return CreateResult(
                ok=False,
                error=f"unknown or ambiguous tier '{def_token}'",
            )

        planet = kwargs.get("planet")
        if not planet:
            planet = getattr(getattr(caller, "db", None),
                             "coord_planet", None)
        if not planet:
            return CreateResult(
                ok=False,
                error="you have no planet position to spawn a base on",
            )

        if "coords" in kwargs:
            coords = kwargs["coords"]
        else:
            coords = None
            c_coords = coords_of(caller)
            if c_coords is not None:
                coords = (int(c_coords[0]), int(c_coords[1]))

        try:
            base = spawner.spawn_base(planet, tier, coords=coords)
        except Exception as exc:  # noqa: BLE001 - relay path failures
            return CreateResult(ok=False, error=str(exc))
        if base is None:
            return CreateResult(
                ok=False,
                error=(f"could not spawn {tier!r} base "
                       "(unknown tier or no valid placement)"),
            )
        return CreateResult(ok=True, instance=base)

    def read(self, caller: Any, instance: Any) -> ShowReport:
        """``show``: identity header, base state, modifiable fields."""
        rec = instance.record
        tier = str(rec.get("tier", "?"))
        disturbed_at = rec.get("disturbed_at") or 0

        state_lines = [
            f"Planet: {rec.get('planet', '?')}",
            f"HQ position: ({rec.get('x', '?')}, {rec.get('y', '?')})",
            ("Disturbed: no (pristine — staleness timer not running)"
             if not disturbed_at
             else f"Disturbed: yes (since tick {disturbed_at})"),
        ]

        spec = self.instance_fields()["disturbed_at"]
        return ShowReport(
            header=f"{instance.name} ({tier}) — NPC base",
            state_lines=state_lines,
            fields=[(spec, disturbed_at, False)],
        )

    def update(self, caller: Any, instance: Any, field: str, value: Any
               ) -> SetResult:
        """``set``: bounded write through the spawner's own state paths.

        ``disturbed_at`` mirrors exactly how the spawner stamps it in
        ``on_combat_action``: the tracking record AND the sentinel's
        persisted ``base_disturbed_at`` attribute. Defensively re-clamps
        into the field's bounds so the SetResult contract (applied always
        in-bounds) holds whoever calls update."""
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a modifiable outpost field; "
                f"settable: {valid}",
            )

        try:
            requested = int(value)
        except (TypeError, ValueError):
            return SetResult.fail(
                field, value,
                f"value must be a whole number (got '{value}')",
            )

        applied = requested
        if spec.min_value is not None:
            applied = max(applied, int(spec.min_value))
        if spec.max_value is not None:
            applied = min(applied, int(spec.max_value))

        rec = instance.record
        try:
            rec["disturbed_at"] = applied
            sentinel = rec.get("sentinel")
            if sentinel is not None:
                set_obj_attr(sentinel, "base_disturbed_at", applied)
        except Exception as exc:  # noqa: BLE001 - relay write-path failures
            return SetResult.fail(
                field, requested,
                f"could not write {field} onto {instance.name}: {exc}",
            )

        return SetResult(ok=True, field=field, requested=requested,
                         applied=applied, clamped=(applied != requested))

    def delete(self, caller: Any, instance: Any) -> Any:
        """``destroy``: wipe the base AS A UNIT via the spawner's existing
        admin-clear path.

        ``wipe_bases_in_area`` over the base's exact HQ tile removes the
        Sentinel + every owned building/guard and untracks the record —
        silent housekeeping (no XP/loot, no BASE_ELIMINATED, no respawn),
        exactly what an admin destroy intends. Bases keep a minimum
        separation, so the one-tile box hits exactly this base.
        """
        spawner = self._spawner()
        if spawner is None:
            return CreateResult(ok=False, error="Outpost spawner unavailable")
        rec = instance.record
        planet, x, y = rec.get("planet"), rec.get("x"), rec.get("y")
        wipe = getattr(spawner, "wipe_bases_in_area", None)
        if not callable(wipe) or planet is None or x is None or y is None:
            return CreateResult(
                ok=False,
                error=f"{instance.name} has no deletion path",
            )
        try:
            count = wipe(planet, int(x), int(y), int(x), int(y))
        except Exception as exc:  # noqa: BLE001 - relay deletion failures
            return CreateResult(ok=False, error=str(exc))
        if not count:
            return CreateResult(
                ok=False,
                error=(f"{instance.name} is no longer tracked — "
                       "the cached list is stale; re-run list"),
            )
        return True

    # ------------------------------------------------------------------ #
    #  Definition scope (the base-template tiers)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """The live ``DataRegistry.base_templates`` dict."""
        registry = self._live_registry()
        templates = getattr(registry, "base_templates", None) if registry \
            else None
        return templates if isinstance(templates, dict) else None

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a tier token to its ``BaseTemplateDef``.

        Same grammar the tier listing teaches: ``#N``/``N`` index into the
        sorted tiers, exact tier name (case-insensitive), unambiguous
        prefix. No no-templates fallback here — with nothing loaded there
        is no definition to show."""
        templates = self.def_registry_dict()
        if not templates:
            return None
        token = str(token or "").strip()
        if not token:
            return None
        tiers = sorted(templates.keys())
        index = self._parse_index(token)
        if index is not None:
            return templates[tiers[index - 1]] if index <= len(tiers) \
                else None
        norm = token.lower()
        if norm in templates:
            return templates[norm]
        prefixed = [t for t in tiers if t.startswith(norm)]
        if len(prefixed) == 1:
            return templates[prefixed[0]]
        return None
