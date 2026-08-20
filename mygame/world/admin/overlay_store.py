"""
OverlayStore — the definition-override overlay for admin ``def set``/``def reset``.

Owns ``data/definitions_overrides.yaml``: a SINGLE overlay document covering
all definition domains (items, buildings, technologies, ...), holding only
the admin-made deviations from the base YAML files. Base YAML stays pristine
(decision D1); this class is the overlay file's ONLY writer.

Overlay file shape (domain -> definition key -> field -> value)::

    items:
      rifle:
        damage_max: 42
    buildings:
      hq:
        hp_max: 500

Guarantees (Requirements 5.1, 5.2, 5.3, 5.9, 5.10, 5.11):

- Every write is atomic: the new document is written to a temp file in the
  same directory and moved into place with ``os.replace``.
- ``set`` replaces any existing override for the field — never duplicates.
- ``reset`` errors when no override exists for the targeted field/key.
- An absent overlay file reads as an empty overlay.
- A present-but-unparseable overlay file raises ``OverlayStoreError`` on
  read, and (because every write re-reads first) all overlay writes are
  rejected until the file is repaired.
- ``set``/``reset`` take a pre-write snapshot; ``restore_snapshot`` rolls
  the file back to it (used when a merged-validation reload fails, R6.5).

The ``base_path`` ctor argument mirrors ``DataRegistry.load_all(base_path)``
(default ``"data"``); the overlay lives directly under it.
"""

from __future__ import annotations

import copy
import logging
import os
import tempfile
from typing import Any

import yaml

from world.admin.types import DEF_ID_FIELDS as _ID_FIELDS

logger = logging.getLogger("mygame.overlay_store")

#: Overlay filename, resolved under the same base path DataRegistry uses.
OVERLAY_FILENAME = "definitions_overrides.yaml"

#: Header written atop every overlay document.
_HEADER = (
    "# definitions_overrides.yaml — admin overrides merged over base YAML.\n"
    "# Managed by `@<entity> def set` / `def reset`. Do not hand-edit while\n"
    "# the server runs: this file is rewritten atomically on every change.\n"
)

#: Fields that identify an entity entry inside a raw YAML document, tried in
#: order — the single :data:`world.admin.types.DEF_ID_FIELDS` list, shared
#: with the router's definition-key logic so the two can't drift.


class OverlayStoreError(Exception):
    """Raised on overlay read/parse failures and invalid overlay operations."""


class OverlayStore:
    """Sole reader/writer of the definition-override overlay file."""

    def __init__(self, base_path: str = "data") -> None:
        self._base_path = base_path
        #: Pre-write snapshot of the overlay file: raw bytes, or ``None``
        #: when the file was absent at snapshot time.
        self._snapshot: bytes | None = None
        self._has_snapshot: bool = False

    # ------------------------------------------------------------------ #
    #  Path / raw I/O
    # ------------------------------------------------------------------ #

    @property
    def overlay_path(self) -> str:
        """Absolute or relative path of the overlay file under base_path."""
        return os.path.join(self._base_path, OVERLAY_FILENAME)

    def _read(self) -> dict:
        """Read the overlay document. Absent file -> empty overlay (R5.10).

        Raises:
            OverlayStoreError: If the file exists but cannot be parsed as a
                YAML mapping (R5.11). Because every write re-reads first,
                this also rejects writes until the file is repaired.
        """
        path = self.overlay_path
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except Exception as exc:
            raise OverlayStoreError(
                f"Overlay file '{path}' cannot be parsed: {exc}. "
                "Overlay writes are rejected until the file is repaired."
            )
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise OverlayStoreError(
                f"Overlay file '{path}' must contain a mapping of "
                f"domain -> key -> field -> value, got {type(raw).__name__}. "
                "Overlay writes are rejected until the file is repaired."
            )
        return raw

    def _write_atomic(self, overlay: dict) -> None:
        """Write *overlay* atomically: temp file in-dir + ``os.replace`` (R5.3)."""
        path = self.overlay_path
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        body = yaml.safe_dump(
            overlay,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=True,
        )
        fd, tmp_path = tempfile.mkstemp(
            prefix=".definitions_overrides.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_HEADER)
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            # Never leave a stray temp file; the real overlay is untouched.
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ #
    #  Snapshot / rollback (consumed by the def set/reset reload flow)
    # ------------------------------------------------------------------ #

    def _take_snapshot(self) -> None:
        """Capture the overlay file's current on-disk state (pre-write)."""
        path = self.overlay_path
        if os.path.isfile(path):
            with open(path, "rb") as f:
                self._snapshot = f.read()
        else:
            self._snapshot = None
        self._has_snapshot = True

    def restore_snapshot(self) -> None:
        """Restore the overlay file to the last pre-write snapshot (R6.5).

        If the file was absent at snapshot time, it is removed again.

        Raises:
            OverlayStoreError: If no snapshot has been taken.
        """
        if not self._has_snapshot:
            raise OverlayStoreError("No overlay snapshot to restore.")
        path = self.overlay_path
        if self._snapshot is None:
            if os.path.isfile(path):
                os.remove(path)
            return
        directory = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(
            prefix=".definitions_overrides.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self._snapshot)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ #
    #  Overlay operations
    # ------------------------------------------------------------------ #

    def get(self, domain: str, key: str) -> dict:
        """Return the current overrides for *domain*/*key* (copy; may be {})."""
        overlay = self._read()
        return dict((overlay.get(domain) or {}).get(key) or {})

    def set(self, domain: str, key: str, field: str, value: Any) -> None:
        """Write one field override, replacing any existing one (R5.2).

        Takes a pre-write snapshot (for ``restore_snapshot``) and writes the
        whole document atomically. Only the overlay file is touched — base
        YAML is never modified.
        """
        overlay = self._read()  # raises if unparseable -> write rejected (R5.11)
        self._take_snapshot()
        domain_map = overlay.setdefault(domain, {})
        if not isinstance(domain_map, dict):
            raise OverlayStoreError(
                f"Overlay domain '{domain}' is not a mapping; repair the file."
            )
        key_map = domain_map.setdefault(key, {})
        if not isinstance(key_map, dict):
            raise OverlayStoreError(
                f"Overlay entry '{domain}.{key}' is not a mapping; repair the file."
            )
        key_map[field] = value
        self._write_atomic(overlay)
        logger.info("Overlay set: %s.%s.%s = %r", domain, key, field, value)

    def reset(self, domain: str, key: str, field: str | None = None) -> None:
        """Remove the override for one field, or the whole key when
        *field* is ``None``. Empty parent mappings are pruned.

        Raises:
            OverlayStoreError: If no override exists for the target (R5.9),
                or the file is unparseable (write rejected, R5.11).
        """
        overlay = self._read()
        domain_map = overlay.get(domain) or {}
        key_map = domain_map.get(key) or {}
        if field is None:
            if not key_map:
                raise OverlayStoreError(
                    f"No override exists for '{domain}.{key}'."
                )
        elif field not in key_map:
            raise OverlayStoreError(
                f"No override exists for '{domain}.{key}.{field}'."
            )
        self._take_snapshot()
        if field is None:
            del domain_map[key]
        else:
            del key_map[field]
            if not key_map:
                del domain_map[key]
        if not domain_map:
            overlay.pop(domain, None)
        self._write_atomic(overlay)
        logger.info("Overlay reset: %s.%s%s", domain, key,
                    "" if field is None else f".{field}")

    def diff(self) -> dict[str, dict[str, dict]]:
        """Return every current deviation as domain -> key -> {field: value}.

        An empty (or absent) overlay produces an empty diff. The returned
        structure is a deep copy — mutating it never touches the store.
        """
        return copy.deepcopy(self._read())

    # ------------------------------------------------------------------ #
    #  Merge hook (consumed by DataRegistry.load_all pre-validate — task 1.8)
    # ------------------------------------------------------------------ #

    def merge_into(self, raw: Any, domain: str) -> Any:
        """Return a deep copy of *raw* with this domain's overrides applied.

        *raw* is one just-read YAML document, whose shape varies per domain:

        - a mapping of key -> field-mapping (canonical overlay shape),
        - a top-level list of entity mappings (buildings.yaml,
          technologies.yaml, powerups.yaml), or
        - a mapping containing list(s) of entity mappings (items.yaml's
          ``items:`` list, terrain.yaml's sections).

        Entity entries inside lists are matched by the first identifying
        field present (``key``, ``abbreviation``, ``terrain_type``,
        ``name``) equal to the override key. Overrides whose key matches no
        entry are left unapplied — the router validates keys before writing,
        and downstream schema validation governs the merged result. *raw*
        itself is never mutated.
        """
        overrides = self._read().get(domain) or {}
        merged = copy.deepcopy(raw)
        if not overrides or merged is None:
            return merged
        for def_key, fields in overrides.items():
            if not isinstance(fields, dict):
                continue
            entry = self._find_entry(merged, def_key)
            if entry is not None:
                entry.update(copy.deepcopy(fields))
        return merged

    @staticmethod
    def _find_entry(doc: Any, def_key: str) -> dict | None:
        """Locate the entity mapping identified by *def_key* inside *doc*."""

        def _scan_list(entries: list) -> dict | None:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for id_field in _ID_FIELDS:
                    if id_field in entry:
                        if entry[id_field] == def_key:
                            return entry
                        break  # entry identified by this field; no match
            return None

        if isinstance(doc, list):
            return _scan_list(doc)
        if isinstance(doc, dict):
            # Canonical mapping shape: key -> field-mapping.
            direct = doc.get(def_key)
            if isinstance(direct, dict):
                return direct
            # Mapping containing entity lists (items.yaml, terrain.yaml).
            for value in doc.values():
                if isinstance(value, list):
                    found = _scan_list(value)
                    if found is not None:
                        return found
        return None
