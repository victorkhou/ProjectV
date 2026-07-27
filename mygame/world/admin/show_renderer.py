"""
Uniform ``show`` rendering for the unified admin CRUD layer.

The single responsibility split out of :class:`~commands.command_router.
EntityAdminRouter` (SRP): turning an adapter's :class:`~world.admin.types.
ShowReport` into the text block the ``show`` verb prints — identity header,
live state lines, then the modifiable-fields block as
``field: value [min–max] (perm)`` with ``*override*`` flags and the optional
staleness note. Pure functions of ``(report, entity)`` (plus the shared
``resolve_bounds`` for the per-field band): no router state, no I/O, so they
render identically wherever they are called and are trivially unit-testable.

``fmt_bound`` lives here too because it is the one bound-formatting rule the
router's clamp note and the ``show`` bounds block must agree on.
"""

from __future__ import annotations

from world.admin.types import resolve_bounds


def fmt_bound(value) -> str:
    """Render one bound value (floats without a trailing ``.0``).

    Shared by the ``show`` bounds block (:func:`render_bounds`) and the
    ``set`` clamp note so a bound reads the same in both places; ``None``
    (an unbounded side) renders as the empty string.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_bounds(spec, entity) -> str:
    """``[lo–hi]`` for a FieldSpec against *entity*'s current state.

    Dynamic bounds are computed per entity (via ``spec.dynamic_bounds``),
    so a field carrying them always renders; a statically unbounded field
    (no dynamic bounds and both static bounds ``None``) renders nothing.
    """
    has_dynamic = spec.dynamic_bounds is not None and entity is not None
    if not has_dynamic and spec.min_value is None and spec.max_value is None:
        return ""
    lo, hi = resolve_bounds(spec, entity)
    return f"[{fmt_bound(lo)}–{fmt_bound(hi)}]"


def render_show(report, entity) -> str:
    """Render a :class:`ShowReport` into the ``show`` verb's text block.

    Identity header, then the live state lines, then the modifiable-fields
    block (``field: value [min–max] (perm)`` with ``*override*`` flags),
    then the staleness note when the report carries one.
    """
    lines = [report.header]
    lines.extend(report.state_lines)
    if report.fields:
        lines.append("Modifiable fields:")
        for spec, value, is_override in report.fields:
            parts = [f"  {spec.name}: {value}"]
            bounds = render_bounds(spec, entity)
            if bounds:
                parts.append(bounds)
            parts.append(f"({spec.perm})")
            if is_override:
                parts.append("*override*")
            lines.append(" ".join(parts))
    if report.staleness_note:
        lines.append(report.staleness_note)
    return "\n".join(lines)
