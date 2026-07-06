"""
Adapters — Evennia-backed implementations of the core ports.

This package is the *only* place under ``world`` that is allowed to import
Evennia and touch the ORM / session handler / object factory. Each module
implements a port from ``world.core.ports`` so that use-case systems depend on
the abstraction and never on Evennia directly. Adapters are constructed and
injected into the systems at the composition root (``server.conf.game_init``).
"""
