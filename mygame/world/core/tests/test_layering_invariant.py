"""
Architecture guard: enforce the Clean-Architecture dependency rule.

The core layer (``world/core``) must stay framework-free — no Evennia, no
Django/Twisted, and no reach into the ``server.conf.game_init`` composition
root — and the use-case systems must not import Evennia at module scope. These
are asserted against the actual ``import`` statements via the AST (not text
search), so a docstring mentioning ``evennia`` never trips the guard and a real
import can never slip in unnoticed.

This is the acceptance test for the DI pass: it is what makes "swap the DB or
framework with zero changes to core logic" a checkable property rather than a
convention.
"""

from __future__ import annotations

import ast
import pathlib

# world/core/tests/ -> world/
_WORLD_DIR = pathlib.Path(__file__).resolve().parents[2]
_CORE_DIR = _WORLD_DIR / "core"
_SYSTEMS_DIR = _WORLD_DIR / "systems"

_FORBIDDEN_IN_CORE = ("evennia", "django", "twisted", "server.conf.game_init")


def _iter_py_files(root: pathlib.Path):
    for path in root.rglob("*.py"):
        # Skip test modules — tests may legitimately install stubs / fakes.
        if "tests" in path.parts:
            continue
        yield path


def _imported_modules(path: pathlib.Path):
    """Yield every module name reached by an import statement in *path*.

    Covers both ``import x.y`` and ``from x.y import z`` at any nesting depth
    (module-level or inside a function), since even a lazy in-method import of
    Evennia into the core would violate the rule.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        yield from _import_targets(node)


def _import_targets(node: ast.AST):
    """Yield (module_name, lineno) for one import node.

    For ``from pkg import name`` this yields BOTH ``pkg`` and ``pkg.name`` so a
    forbidden symbol imported by name (e.g. ``from server.conf import
    game_init``) is caught, not just forbidden packages imported wholesale. For
    ``from . import name`` (relative, ``module`` is None) it yields the bare
    ``name`` so those aren't silently skipped either.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name, node.lineno
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            yield node.module, node.lineno
            for alias in node.names:
                yield f"{node.module}.{alias.name}", node.lineno
        else:
            for alias in node.names:
                yield alias.name, node.lineno


def _matches_forbidden(module: str, forbidden) -> bool:
    return any(module == f or module.startswith(f + ".") for f in forbidden)


class TestCoreLayerIsFrameworkFree:
    """world/core imports nothing framework-specific — anywhere."""

    def test_core_has_no_forbidden_imports(self):
        violations = []
        for path in _iter_py_files(_CORE_DIR):
            for module, lineno in _imported_modules(path):
                if _matches_forbidden(module, _FORBIDDEN_IN_CORE):
                    rel = path.relative_to(_WORLD_DIR)
                    violations.append(f"{rel}:{lineno} imports '{module}'")
        assert not violations, (
            "world/core must be framework-free but found:\n  "
            + "\n  ".join(violations)
        )


class TestSystemsHaveNoModuleLevelEvennia:
    """Use-case systems must not import Evennia at module scope.

    Lazy in-method imports of ``server.conf.game_init`` remain as documented
    legacy fallbacks, but a *top-level* ``import evennia`` in a system body
    would mean the module cannot even load without the framework.
    """

    def test_no_module_level_evennia_import(self):
        violations = []
        for path in _iter_py_files(_SYSTEMS_DIR):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in tree.body:  # module-level statements only
                for module, lineno in _import_targets(node):
                    if _matches_forbidden(module, ("evennia",)):
                        rel = path.relative_to(_WORLD_DIR)
                        violations.append(f"{rel}:{lineno} imports '{module}'")
        assert not violations, (
            "world/systems must not import evennia at module scope but found:\n  "
            + "\n  ".join(violations)
        )
