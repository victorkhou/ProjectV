"""
Static guard for the composition root (server/conf/game_init.py).

game_init wires every system/adapter/presenter together but is not exercised by
the unit suite (it does live Evennia work). A NameError there — e.g. calling an
adapter class that was never imported — would abort server startup and is
invisible to the tests. This guard parses game_init with the AST and asserts
that every capitalized name *called* is either imported or a known builtin/
Evennia factory, catching that class of bug without booting a server.

Regression: the Presenter refactor once called ``EvenniaPlayerNotifier()``
without importing it, silently dropping all player notifications on boot.
"""

from __future__ import annotations

import ast
import pathlib

_GAME_INIT = (
    pathlib.Path(__file__).resolve().parents[1] / "conf" / "game_init.py"
)

# Names that are legitimately unbound-at-module-level: Python builtins plus the
# Evennia helpers game_init imports lazily/at call time inside try blocks.
_ALLOWED = {
    "int", "len", "getattr", "hasattr", "dict", "set", "list", "str", "bool",
    "create_object", "create_script", "search_tag", "search_script",
    "TerrainGenerator",  # imported inside the coordinate try-block loop
}


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _assigned_names(tree: ast.AST) -> set[str]:
    """Locally-assigned names (e.g. `foo = Bar()` then `foo(...)`)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def test_game_init_has_no_unimported_call_names():
    """Every capitalized name called in game_init must be imported/defined.

    Catches the "call an adapter class that was never imported" NameError that
    the live suite cannot see because it never runs initialize_game().
    """
    src = _GAME_INIT.read_text()
    tree = ast.parse(src, filename=str(_GAME_INIT))

    bound = _imported_names(tree) | _assigned_names(tree) | _ALLOWED

    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            # Only class-like names (Capitalized) — lowercase helpers are noise.
            if name[:1].isupper() and name not in bound:
                missing.append(f"{name} (line {node.lineno})")

    assert not missing, (
        "game_init.py calls names it never imports/defines "
        "(NameError at server startup):\n  " + "\n  ".join(sorted(set(missing)))
    )
