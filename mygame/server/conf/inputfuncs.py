"""
Client-to-server input functions (loaded via ``settings.INPUT_FUNC_MODULES``).

Intentionally empty: this project's client traffic uses ordinary commands plus
the webclient map payload, neither of which needs a custom input func.

Every *global function* in this module becomes a client-callable input handler
with the signature ``cmdname(session, *args, **kwargs)``. The reserved name
``default(session, cmdname, *args, **kwargs)`` catches unmatched command names.
"""
