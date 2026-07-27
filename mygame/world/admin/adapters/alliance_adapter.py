"""
AllianceAdapter — the ``@alliance`` EntityAdapter (unified-admin-crud Phase 3).

Migrates the alliance admin surface onto the unified grammar
(Requirements 1.5, 3.5, 11.5, 11.6):

- **``inspect``→``show`` and ``disband``→``destroy``** Migration_Aliases
  (Requirement 11.5, design D5): the legacy staff spellings keep working
  with a deprecation note.
- **NEW ``set``** writing EXCLUSIVELY through the AllianceSystem
  single-writer (Requirement 3.5): ``name``/``tag`` (validated exactly
  like a rename) and ``open_join`` (on|off) via
  :meth:`AllianceSystem.admin_set_alliance_field`; ``destroy`` routes
  through :meth:`AllianceSystem.admin_disband_alliance` (the single
  teardown path).
- **``spawn`` opted out** (design per-entity matrix): alliances are
  founded by players — the reason carries the pointer to the
  player-facing ``alliance found`` path (Requirement 1.5).
- **No YAML defs**: alliances have no definition domain to override. The
  read-only PERKS CATALOG (``DataRegistry.alliance_perks``, the optional
  ``alliance_perks.yaml``) serves ``def list``/``def show``;
  ``def set``/``def reset`` are opted out because the catalog loads
  OUTSIDE the overlay merge pipeline (only ``_REQUIRED_FILES`` domains
  merge), so an override would "reload OK" without ever applying —
  matching the OutpostAdapter precedent. ``def diff`` stays supported:
  the ``alliance_perks`` overlay domain is always empty, and an empty
  overlay produces an empty diff (Requirement 5.6).
- **``kick``/``transfer``/``rename`` stay as extra verbs** (handlers on
  the router subclass); their mutations also route through the
  ``AllianceSystem.admin_*`` single-writer methods.
- **System/registry access is lazy** (services facade, then — for the
  registry — the DataRegistry singleton), so constructing and
  registering the adapter never needs a booted server; tests may inject
  an ``alliance_system`` and/or ``registry`` double.
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
    CreateResult,
    DeleteResult,
    FieldSpec,
    InstanceRow,
    SetResult,
    ShowReport,
)

#: The spawn opt-out reason (with its pointer to the supported path),
#: surfaced verbatim by the router (Requirement 1.5).
_SPAWN_OPT_OUT = (
    "alliances are founded by players, not admin-spawned — use the "
    "player-facing 'alliance found <name> = <tag>' instead"
)

#: Reason shared by the opted-out definition-write verbs (see module doc).
_DEF_WRITE_OPT_OUT = (
    "alliances have no YAML definition domain — the perks catalog "
    "(alliance_perks.yaml) is read-only here and loads outside the "
    "overlay merge pipeline; edit data/definitions/alliance_perks.yaml "
    "and run @reboot instead"
)


@dataclass(frozen=True)
class AllianceRef:
    """One live alliance as the adapter's instance handle.

    Wraps the Alliance_Record with the ``key``/``name`` attributes the
    shared router handlers render (``key`` is the tag — the stable
    spelling the legacy grammar addressed alliances by).
    """

    key: str          # the alliance tag
    name: str         # the alliance name
    alliance_id: int  # the record id (stable identity for system calls)
    record: dict      # the live Alliance_Record


class AllianceAdapter:
    """EntityAdapter for alliances (the ``@alliance`` admin surface).

    Tests may inject an ``alliance_system`` and/or ``registry`` double;
    production resolves both lazily through the services facade (the
    registry falls back to the DataRegistry singleton).
    """

    entity_key = "alliance"
    #: Overlay/definition domain. Exists only for the read surface
    #: (``def diff`` over an always-empty overlay domain); the write
    #: verbs are opted out because load_all never merges it.
    def_domain = "alliance_perks"

    # --- grammar contract (design per-entity matrix row for @alliance) ---
    supported_verbs = frozenset(
        {"list", "show", "set", "destroy", "def list", "def show",
         "def diff"}
    )
    opt_outs: dict[str, str] = {
        "spawn": _SPAWN_OPT_OUT,
        "def set": _DEF_WRITE_OPT_OUT,
        "def reset": _DEF_WRITE_OPT_OUT,
    }
    #: Entity-specific moderation verbs kept from the legacy router
    #: (Requirement 1.6); handlers live on the router subclass.
    extra_verbs: dict[str, str] = {
        "kick": "Force-kick a member (kick <alliance> <player>)",
        "transfer": "Force-transfer leadership "
                    "(transfer <alliance> <player>)",
        "rename": "Rename/retag an alliance "
                  "(rename <alliance> <new name> = <new tag>)",
    }
    #: Migration aliases (design D5): the legacy staff spellings.
    aliases: dict[str, str] = {"inspect": "show", "disband": "destroy"}

    def __init__(self, alliance_system: Any | None = None,
                 registry: Any | None = None) -> None:
        self._alliance_system = alliance_system
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Lazy system access (no live game required to construct)
    # ------------------------------------------------------------------ #

    def _system(self) -> Any | None:
        """The injected alliance_system double, else the live system."""
        return live_service("alliance_system", self._alliance_system)

    def _live_registry(self) -> Any | None:
        """Injected double, else services facade, else the singleton."""
        return live_registry(self._registry)

    # ------------------------------------------------------------------ #
    #  Field schema (instance plane)
    # ------------------------------------------------------------------ #

    def instance_fields(self) -> dict[str, FieldSpec]:
        """Modifiable alliance fields — all written through the
        AllianceSystem single-writer path inside :meth:`update`."""
        specs = (
            FieldSpec(name="name", kind="str", perm="Builder"),
            FieldSpec(name="tag", kind="str", perm="Builder"),
            FieldSpec(name="open_join", kind="enum",
                      enum_values=("on", "off"), perm="Builder"),
        )
        return {spec.name: spec for spec in specs}

    def definition_fields(self) -> dict[str, FieldSpec]:
        """Empty: ``def set``/``def reset`` are opted out (the perks
        catalog loads outside the overlay merge — see the module doc)."""
        return {}

    # ------------------------------------------------------------------ #
    #  Listing + resolution (instance plane)
    # ------------------------------------------------------------------ #

    def _records(self) -> list[dict]:
        """Every Alliance_Record, sorted by id (stable listing order)."""
        system = self._system()
        registry = getattr(system, "_alliances", None) if system else None
        if registry is None:
            return []
        try:
            records = registry.all_alliances() or []
        except Exception:  # noqa: BLE001 - reads must never break a verb
            return []
        return sorted(records, key=lambda rec: rec.get("id", 0))

    def _row(self, index: int, rec: dict) -> InstanceRow:
        """One alliance as an InstanceRow (list output + #N cache).

        The row ``key`` is the alliance TAG — the spelling the legacy
        grammar addressed alliances by, so ``@alliance show IW`` resolves
        by exact key exactly like the legacy tag lookup.
        """
        system = self._system()
        tag = str(rec.get("tag", "?"))
        name = str(rec.get("name", tag))
        members = level = "?"
        if system is not None:
            try:
                members = len(system._live_members(rec["id"]))
                level = system.compute_alliance_level(rec["id"])
            except Exception:  # noqa: BLE001 - derivation never breaks list
                pass
        return InstanceRow(
            index=index, key=tag, name=name,
            summary=f"[{tag}] {name} — {members} members, level {level}",
            ref=AllianceRef(key=tag, name=name, alliance_id=rec["id"],
                            record=rec),
        )

    @staticmethod
    def _matches_filter(rec: dict, filt: str) -> bool:
        """Lenient list filter: tag or name substring."""
        return (
            filt in str(rec.get("tag", "")).lower()
            or filt in str(rec.get("name", "")).lower()
        )

    def list_instances(self, caller: Any, filter_str: str
                       ) -> list[InstanceRow]:
        """Live alliances as indexed rows (tag, name, members, level)."""
        filt = (filter_str or "").strip().lower()
        rows: list[InstanceRow] = []
        for rec in self._records():
            if filt and not self._matches_filter(rec, filt):
                continue
            rows.append(self._row(len(rows) + 1, rec))
        return rows

    def _candidate_rows(self, caller: Any) -> list[InstanceRow]:
        return self.list_instances(caller, "")

    def _is_stale(self, ref: Any) -> bool:
        """A cached row is stale when its alliance no longer exists."""
        if not isinstance(ref, AllianceRef):
            return ref is None
        system = self._system()
        if system is None:
            return True
        try:
            return not system.alliance_exists(ref.alliance_id)
        except Exception:  # noqa: BLE001 - treat a broken check as stale
            return True

    def resolve_instance(self, caller: Any, token: str) -> Resolution:
        """Resolve *token* per the uniform grammar: ``#N`` indexes the
        caller's alliance List_Cache; key/name/prefix tiers run over the
        live alliances (tags are the keys, so the legacy tag addressing
        resolves unchanged)."""
        rows = LIST_CACHE.get(caller, self.entity_key)
        return resolve_instance_token(
            (token or "").strip(), rows=rows,
            candidates=self._candidate_rows(caller),
            is_stale=self._is_stale,
        )

    # ------------------------------------------------------------------ #
    #  Instance CRUD hooks (all writes via the AllianceSystem single writer)
    # ------------------------------------------------------------------ #

    def create(self, caller: Any, def_token: str, kwargs: dict
               ) -> CreateResult:
        """``spawn`` is opted out — defensive refusal should anything
        bypass the router's opt-out dispatch (no state change)."""
        return CreateResult(ok=False, error=_SPAWN_OPT_OUT)

    def read(self, caller: Any, instance: AllianceRef) -> ShowReport:
        """``show``: full staff readout (treasury, perks, pending
        invites/requests — bypassing the member/outsider scoping), plus
        the modifiable-fields block."""
        rec = instance.record
        system = self._system()

        leader_name = "?"
        members = level = "?"
        if system is not None:
            try:
                leader = system._resolve_member(rec.get("leader_id"))
                leader_name = str(getattr(leader, "key", None) or "?")
                members = len(system._live_members(rec["id"]))
                level = system.compute_alliance_level(rec["id"])
            except Exception:  # noqa: BLE001 - derivation never breaks show
                pass

        treasury = dict(rec.get("treasury", {}) or {})
        perks = dict(rec.get("active_perks", {}) or {})
        state_lines = [
            f"Leader: {leader_name}    Members: {members}    "
            f"Level: {level}    Open-join: "
            f"{'ON' if rec.get('open_join') else 'OFF'}",
            f"Officers: {rec.get('officer_ids')}    "
            f"Members: {rec.get('member_ids')}",
            "Treasury: " + (
                ", ".join(f"{k}: {v}" for k, v in sorted(treasury.items()))
                or "empty"
            ),
            "Active perks: " + (
                ", ".join(f"{k} L{v}" for k, v in sorted(perks.items()))
                or "none"
            ),
            f"Pending invites: {rec.get('pending_invites')}",
            f"Pending requests: {rec.get('pending_requests')}",
        ]

        values = {
            "name": rec.get("name"),
            "tag": rec.get("tag"),
            "open_join": "on" if rec.get("open_join") else "off",
        }
        fields = [
            (spec, values.get(spec.name, "—"), False)
            for spec in self.instance_fields().values()
        ]
        return ShowReport(
            header=(f"#{rec.get('id')} {rec.get('name')} "
                    f"[{rec.get('tag')}] — alliance"),
            state_lines=state_lines,
            fields=fields,
            staleness_note=None,  # no definition domain to drift from
        )

    def update(self, caller: Any, instance: AllianceRef, field: str,
               value: Any) -> SetResult:
        """``set``: write through the AllianceSystem single writer
        (Requirement 3.5). Name/tag validation (uniqueness, denylist,
        markup) lives in the system; no state changes on any failure
        (Requirement 3.10)."""
        spec = self.instance_fields().get(field)
        if spec is None:
            valid = ", ".join(sorted(self.instance_fields()))
            return SetResult.fail(
                field, value,
                f"'{field}' is not a modifiable alliance field; "
                f"settable: {valid}",
            )
        if spec.kind == "enum":
            text = str(value).strip().lower()
            if text not in (spec.enum_values or ()):
                return SetResult.fail(
                    field, value,
                    f"'{value}' is not a valid value for '{field}' "
                    f"— valid values: "
                    f"{', '.join(spec.enum_values or ())}",
                )
            write_value: Any = text == "on"
            applied: Any = text
        else:
            write_value = applied = str(value).strip()

        system = self._system()
        if system is None:
            return SetResult.fail(field, value, "Alliance system unavailable")
        ok, error = system.admin_set_alliance_field(
            instance.alliance_id, field, write_value
        )
        if not ok:
            return SetResult.fail(field, value, error or "write path failed")
        return SetResult(ok=True, field=field, requested=value,
                         applied=applied, clamped=False)

    def delete(self, caller: Any, instance: AllianceRef) -> DeleteResult:
        """``destroy``: force-disband through the AllianceSystem's single
        teardown path (even-split, pointer clears, channel destroy)."""
        system = self._system()
        if system is None:
            return DeleteResult(ok=False, error="Alliance system unavailable")
        ok, error = system.admin_disband_alliance(instance.alliance_id)
        if not ok:
            return DeleteResult(ok=False, error=error or "disband failed")
        return DeleteResult(ok=True)

    # ------------------------------------------------------------------ #
    #  Definition scope (the read-only perks catalog)
    # ------------------------------------------------------------------ #

    def def_registry_dict(self) -> dict | None:
        """The live ``DataRegistry.alliance_perks`` catalog dict."""
        registry = self._live_registry()
        perks = getattr(registry, "alliance_perks", None) if registry \
            else None
        return perks if isinstance(perks, dict) else None

    def def_resolve(self, token: str) -> Any | None:
        """Resolve a perk token to its catalog spec.

        Exact perk key (case-insensitive), else unambiguous prefix. The
        returned dict carries the perk ``key`` alongside the raw spec so
        the shared ``def show`` renders a clean identity header.
        """
        perks = self.def_registry_dict()
        if not perks:
            return None
        token = str(token or "").strip().lower()
        if not token:
            return None
        key = token if token in perks else None
        if key is None:
            prefixed = [k for k in sorted(perks) if k.startswith(token)]
            if len(prefixed) != 1:
                return None
            key = prefixed[0]
        spec = perks[key]
        return {"key": key, **spec} if isinstance(spec, dict) \
            else {"key": key, "spec": spec}
