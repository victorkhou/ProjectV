"""
Framework-free core layer for the RTS Combat Overworld.

Everything under ``world.core`` is pure Python: it must not import Evennia,
Django, Twisted, or the ``server.conf.game_init`` composition root. The core
holds abstractions (``world.core.ports``) that use-case systems depend on, and
pure decision logic (``world.core.strategies``). Concrete, framework-bound
implementations of the ports live in ``world.adapters`` and are wired to the
systems at the composition root.

This one-way dependency rule — core knows nothing about the framework, the
framework depends on core — is what lets the persistence/transport layer be
swapped without touching business logic.
"""
