"""
The definition-overlay write transaction for the unified admin CRUD layer.

``def set`` and ``def reset`` share one delicate sequence — the piece split
out of :class:`~commands.command_router.EntityAdminRouter` here (SRP): under
the reload-serialization lock (Requirement 6.6), resolve the definition,
snapshot the "before" values, mutate the overlay, reload the registry, and
either read the "after" values (success) or roll the overlay back to its
pre-command snapshot (reload failure, Requirement 6.5). Only the store
mutation (``set`` vs ``reset``) and which fields to report differ; both were
previously open-coded twice.

This class owns just that transactional control flow and returns a
:class:`DefWriteResult` describing the outcome. Validation (field/kind/perm)
and response rendering stay in the router — this makes no assumptions about
how the outcome is shown and messages nobody, so it is a pure unit of overlay
transaction mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable

from world.admin.overlay_store import OverlayStoreError

#: Outcome stages, mirroring the branch points the router renders.
NOT_FOUND = "not_found"        # def_resolve(token) returned None
OVERLAY_ERROR = "overlay_error"  # the store mutation raised (no reload run)
OK = "ok"                      # overlay written + reload applied
RELOAD_FAILED = "reload_failed"  # reload rejected; overlay rolled back


@dataclass
class DefWriteResult:
    """Structured outcome of a :class:`DefWriteTransaction` run.

    ``status`` is one of the module-level stage constants; the remaining
    fields carry only what that stage's response needs (``def_key`` and the
    ``before``/``after`` field maps for the success report, ``errors`` +
    ``rollback_note`` for a rejected reload, ``store_error`` for an
    overlay-write failure).
    """

    status: str
    def_key: str | None = None
    before: dict = dataclass_field(default_factory=dict)
    after: dict = dataclass_field(default_factory=dict)
    errors: list = dataclass_field(default_factory=list)
    rollback_note: str = ""
    store_error: str = ""


class DefWriteTransaction:
    """Runs one overlay-write-then-reload transaction under the reload lock.

    Constructed with the resolved collaborators (the adapter for
    definition resolution, the overlay ``store``, the ``registry`` whose
    ``reload_all`` performs the atomic swap, the serialization ``lock``,
    the ``domain`` being written) plus the two small readers the router
    already owns (``definition_key`` / ``definition_value``). One
    :meth:`run` per ``def set`` / ``def reset`` invocation.
    """

    def __init__(
        self,
        adapter: Any,
        store: Any,
        registry: Any,
        lock: Any,
        domain: str,
        definition_key: Callable[[Any], str],
        definition_value: Callable[[Any, str], Any],
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._registry = registry
        self._lock = lock
        self._domain = domain
        self._definition_key = definition_key
        self._definition_value = definition_value

    def run(
        self,
        token: str,
        mutate: Callable[[Any, str, str], None],
        snapshot_fields: Callable[[Any, str], list[str]],
    ) -> DefWriteResult:
        """Execute the transaction for definition *token*.

        Holds the reload lock across the whole resolve → snapshot → write →
        reload sequence (Requirement 6.6). ``mutate(store, domain, def_key)``
        performs the overlay change (may raise ``OverlayStoreError``);
        ``snapshot_fields(definition, def_key)`` names the fields whose
        merged values are captured before the write and re-read after a
        successful reload (for the before→after report).
        """
        with self._lock:
            definition = self._adapter.def_resolve(token)
            if definition is None:
                return DefWriteResult(status=NOT_FOUND)
            def_key = self._definition_key(definition)

            report_fields = snapshot_fields(definition, def_key)
            before = {
                name: self._definition_value(definition, name)
                for name in report_fields
            }

            try:
                mutate(self._store, self._domain, def_key)
            except OverlayStoreError as exc:
                return DefWriteResult(
                    status=OVERLAY_ERROR, def_key=def_key,
                    before=before, store_error=str(exc),
                )

            success, errors = self._registry.reload_all()
            if success:
                reloaded = self._adapter.def_resolve(def_key)
                after = {
                    name: self._definition_value(reloaded, name)
                    for name in report_fields
                }
                return DefWriteResult(
                    status=OK, def_key=def_key, before=before, after=after,
                )

            return DefWriteResult(
                status=RELOAD_FAILED, def_key=def_key, before=before,
                errors=list(errors or []),
                rollback_note=self._rollback(),
            )

    def _rollback(self) -> str:
        """Restore the overlay's pre-command snapshot; describe the result."""
        try:
            self._store.restore_snapshot()
            return "Overlay rolled back; nothing changed."
        except Exception as exc:  # noqa: BLE001 - report, don't mask errors
            return f"|rOverlay rollback FAILED: {exc}|n"
