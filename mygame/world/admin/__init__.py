"""
Unified admin CRUD adapter layer (unified-admin-crud feature).

This package holds the shared EntityAdapter layer that unifies the per-entity
``@<entity>`` admin command dialects: core types (``world.admin.types``),
the adapter registry with startup verb-coverage enforcement, the shared
target-resolution engine, and the definition-override overlay store.

NOTE: ``world/adapters/`` is the pre-existing hexagonal-port adapter package
and is unrelated to this one.
"""
