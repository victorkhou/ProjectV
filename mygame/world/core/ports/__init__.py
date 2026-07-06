"""
Ports — the abstractions the core depends on.

Each module here declares an abstract interface (``abc.ABC``) that a use-case
system needs from the outside world: persistence (repositories), object
creation (factories), read-only config (providers), or outbound messaging
(notifier). Systems accept these ports via their constructors; the Evennia-
backed implementations in ``world.adapters`` are injected at the composition
root (``server.conf.game_init``).

Depending on a port instead of a concrete class is what makes "swap the DB or
the transport with zero changes to core logic" true: only a new adapter is
written, never a system body.
"""
