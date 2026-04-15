"""
Custom admin panel for RTS Combat Overworld.

Extends the default Evennia admin with game-specific columns and
filters for CombatCharacters, Buildings, PlanetRooms, and Scripts.
"""

from django.contrib import admin
from django.utils.html import format_html, mark_safe

from evennia.objects.models import ObjectDB
from evennia.scripts.models import ScriptDB

from evennia.web.admin.objects import (
    ObjectAdmin,
    ObjectAttributeInline,
    ObjectTagInline,
)
from evennia.web.admin.scripts import (
    ScriptAdmin,
    ScriptAttributeInline,
    ScriptTagInline,
)


def _get_attr(obj, key, default=""):
    """Safely read an Evennia Attribute from a DB object."""
    try:
        val = obj.attributes.get(key)
        return val if val is not None else default
    except Exception:
        return default


def _get_tag(obj, category, default=""):
    """Safely read an Evennia Tag from a DB object."""
    try:
        val = obj.tags.get(category=category, return_list=False)
        return val if val else default
    except Exception:
        return default


# ------------------------------------------------------------------ #
#  Unregister Evennia defaults so we can replace them
# ------------------------------------------------------------------ #

admin.site.unregister(ObjectDB)
admin.site.unregister(ScriptDB)


# ------------------------------------------------------------------ #
#  Enhanced Object Admin
# ------------------------------------------------------------------ #

@admin.register(ObjectDB)
class GameObjectAdmin(ObjectAdmin):
    """Extended object admin with game-specific columns."""

    list_display = (
        "id",
        "db_key",
        "typeclass_short",
        "game_coords",
        "game_terrain",
        "game_hp",
        "game_owner",
        "db_location",
        "db_date_created",
    )
    list_display_links = ("id", "db_key")
    list_filter = ("db_typeclass_path",)
    search_fields = [
        "=id",
        "^db_key",
        "db_typeclass_path",
        "^db_account__db_key",
        "^db_location__db_key",
    ]

    def typeclass_short(self, obj):
        """Show just the class name, not the full path."""
        path = obj.db_typeclass_path or ""
        return path.rsplit(".", 1)[-1] if "." in path else path
    typeclass_short.short_description = "Type"
    typeclass_short.admin_order_field = "db_typeclass_path"

    def game_coords(self, obj):
        """Show coordinates for rooms, characters, and buildings."""
        tc = obj.db_typeclass_path or ""
        if "CombatCharacter" in tc or "Character" in tc:
            x = _get_attr(obj, "coord_x", "")
            y = _get_attr(obj, "coord_y", "")
            planet = _get_attr(obj, "coord_planet", "")
            if x != "" and y != "" and planet:
                return f"({x}, {y}) {planet}"
        # Buildings with coordinates
        if _get_attr(obj, "building_type"):
            x = _get_attr(obj, "coord_x", "")
            y = _get_attr(obj, "coord_y", "")
            if x != "" and y != "":
                return f"({x}, {y})"
        return ""
    game_coords.short_description = "Coords"

    def game_terrain(self, obj):
        """Show terrain type for rooms (placeholder — terrain is procedural)."""
        return ""
    game_terrain.short_description = "Terrain"

    def game_hp(self, obj):
        """Show HP for characters and buildings."""
        hp = _get_attr(obj, "hp")
        hp_max = _get_attr(obj, "hp_max")
        if hp != "" and hp_max != "":
            try:
                ratio = int(hp) / max(int(hp_max), 1)
                color = "#6abf69" if ratio > 0.5 else "#c9a84c" if ratio > 0.2 else "#e94560"
                return mark_safe(f'<span style="color:{color};">{hp}/{hp_max}</span>')
            except (ValueError, TypeError):
                pass
        return ""
    game_hp.short_description = "HP"

    def game_owner(self, obj):
        """Show owner for buildings, rank for characters."""
        tc = obj.db_typeclass_path or ""
        owner = _get_attr(obj, "owner")
        if owner:
            name = getattr(owner, "db_key", str(owner))
            return name
        if "CombatCharacter" in tc:
            rank = _get_attr(obj, "rank_level", "")
            xp = _get_attr(obj, "combat_xp", "")
            if rank != "":
                return f"Rank {rank} ({xp} XP)"
        btype = _get_attr(obj, "building_type")
        if btype:
            level = _get_attr(obj, "building_level", 1)
            return f"{btype} Lv{level}"
        return ""
    game_owner.short_description = "Info"


# ------------------------------------------------------------------ #
#  Enhanced Script Admin
# ------------------------------------------------------------------ #

@admin.register(ScriptDB)
class GameScriptAdmin(ScriptAdmin):
    """Extended script admin with game-specific info."""

    list_display = (
        "id",
        "db_key",
        "script_type_short",
        "db_interval",
        "db_repeats",
        "db_persistent",
        "script_status",
        "db_date_created",
    )

    def script_type_short(self, obj):
        """Show just the class name."""
        path = obj.db_typeclass_path or ""
        return path.rsplit(".", 1)[-1] if "." in path else path
    script_type_short.short_description = "Type"
    script_type_short.admin_order_field = "db_typeclass_path"

    def script_status(self, obj):
        """Show if the script is running."""
        if obj.db_interval and obj.db_interval > 0:
            return mark_safe(
                f'<span style="color:#6abf69;">● running ({obj.db_interval}s)</span>'
            )
        return mark_safe('<span style="color:#a0a0a0;">○ idle</span>')
    script_status.short_description = "Status"
