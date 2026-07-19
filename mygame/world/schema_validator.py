"""
Schema Validator for RTS Combat Overworld definition files.

Validates raw YAML dicts against expected schemas before they enter the
Data Registry. Each validation method returns a list of error strings
(empty list = valid).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from world.constants import (
    BOMB_CATEGORIES,
    EFFECT_TYPES,
    EQUIPMENT_SLOTS,
    GEAR_CATEGORIES,
    ITEM_CATEGORIES,
    MAX_LEVEL,
    RESOURCE_TYPES,
    WEAPON_TYPES,
)
from world.definitions import BalanceConfig

if TYPE_CHECKING:
    pass  # DataRegistry imported only for type hints in cross_validate


# ---------------------------------------------------------------------------
# Derive the balance-field type lists from BalanceConfig's dataclass fields so
# they can never drift out of sync with the dataclass definition.
# ---------------------------------------------------------------------------
def _balance_fields_by_type():
    """Partition BalanceConfig fields into (int_names, float_names, bool_names).

    Dict-typed fields (resource_weights, demolish_refund_rates, etc.) require
    per-field semantic validation and are handled individually — they are
    excluded from the returned sets.
    """
    int_f, float_f, bool_f = [], [], []
    for f in dataclasses.fields(BalanceConfig):
        t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
        if t == "int":
            int_f.append(f.name)
        elif t == "float":
            float_f.append(f.name)
        elif t == "bool":
            bool_f.append(f.name)
        # dict[...] fields are validated individually (different key/value semantics)
    return int_f, float_f, bool_f


_BALANCE_INT_FIELDS, _BALANCE_FLOAT_FIELDS, _BALANCE_BOOL_FIELDS = _balance_fields_by_type()


class SchemaValidator:
    """Validates definition file contents against expected schemas."""

    # ------------------------------------------------------------------ #
    #  Buildings
    # ------------------------------------------------------------------ #
    def validate_buildings(self, data: list[dict]) -> list[str]:
        """Validate a list of building definition dicts."""
        errors: list[str] = []
        required = {
            "name", "abbreviation", "cost", "max_health", "requires_hq", "category",
            "build_time_seconds", "max_level", "rank_requirement", "requires_agent",
            "storage_capacity",
        }
        for prefix, entry in self._iter_dict_entries(data, "buildings", required, errors):
            # abbreviation must be 2 chars
            abbr = entry.get("abbreviation")
            if isinstance(abbr, str) and len(abbr) != 2:
                errors.append(f"{prefix}: abbreviation must be 2 characters, got '{abbr}'")

            # cost values must be positive ints
            cost = entry.get("cost")
            if isinstance(cost, dict):
                self._check_positive_int_map(errors, prefix, "cost", cost)

            # max_health > 0
            mh = entry.get("max_health")
            if isinstance(mh, int) and mh <= 0:
                errors.append(f"{prefix}: max_health must be > 0, got {mh}")
            elif mh is not None and not isinstance(mh, int):
                errors.append(f"{prefix}: max_health must be an integer, got {type(mh).__name__}")

            # map_symbol must be 2 chars if present
            ms = entry.get("map_symbol")
            if ms is not None and isinstance(ms, str) and len(ms) != 2:
                errors.append(f"{prefix}: map_symbol must be 2 characters, got '{ms}'")

            # build_time_seconds must be a positive int
            self._check_positive_int(
                errors, prefix, "build_time_seconds", entry.get("build_time_seconds")
            )

            # max_level must be a positive int within the structural ceiling
            ml = entry.get("max_level")
            self._check_positive_int(errors, prefix, "max_level", ml)
            if isinstance(ml, int) and not isinstance(ml, bool) and ml > 0:
                from world.constants import MAX_BUILDING_LEVEL
                if ml > MAX_BUILDING_LEVEL:
                    errors.append(
                        f"{prefix}: max_level {ml} exceeds MAX_BUILDING_LEVEL "
                        f"({MAX_BUILDING_LEVEL})"
                    )

            # rank_requirement must be a positive int
            self._check_positive_int(
                errors, prefix, "rank_requirement", entry.get("rank_requirement")
            )

            # requires_agent must be a bool
            ra = entry.get("requires_agent")
            if ra is not None and not isinstance(ra, bool):
                errors.append(
                    f"{prefix}: requires_agent must be a boolean, got {type(ra).__name__}"
                )

            # storage_capacity must be a non-negative int
            self._check_positive_int(
                errors, prefix, "storage_capacity",
                entry.get("storage_capacity"), allow_zero=True,
            )

            # capabilities (optional) must be a list of known capability flags
            caps = entry.get("capabilities")
            if caps is not None:
                from world.constants import BUILDING_CAPABILITIES
                if not isinstance(caps, list):
                    errors.append(
                        f"{prefix}: capabilities must be a list, got {type(caps).__name__}"
                    )
                else:
                    for cap in caps:
                        if cap not in BUILDING_CAPABILITIES:
                            errors.append(
                                f"{prefix}: unknown capability '{cap}' "
                                f"(known: {sorted(BUILDING_CAPABILITIES)})"
                            )

        return errors


    # ------------------------------------------------------------------ #
    #  Items
    # ------------------------------------------------------------------ #
    def validate_items(self, data: dict) -> list[str]:
        """Validate an items definition dict (items list + production_map)."""
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"items: expected a dict, got {type(data).__name__}"]

        items_list = data.get("items", [])
        if not isinstance(items_list, list):
            errors.append(f"items.items: expected a list, got {type(items_list).__name__}")
            items_list = []

        # `slot` is required only for Gear categories (handled per-item below),
        # so it is not part of the unconditional required set. Supply items
        # (ammo/consumable/throwable) occupy no slot.
        required = {"key", "name"}
        item_keys: set[str] = set()

        for prefix, entry in self._iter_dict_entries(items_list, "items", required, errors):
            key = entry.get("key")
            if isinstance(key, str):
                item_keys.add(key)

            # stat_modifiers values must be numeric; no key allowlist is applied.
            # `max_hp` is now a wired effect (raises hp_max — task 6.4); `accuracy`
            # remains an accepted numeric key with no wired effect (D6).
            sm = entry.get("stat_modifiers")
            if sm is not None:
                if not isinstance(sm, dict):
                    errors.append(
                        f"{prefix}: stat_modifiers must be a dict, got {type(sm).__name__}"
                    )
                else:
                    for stat, val in sm.items():
                        if not isinstance(val, (int, float)) or isinstance(val, bool):
                            errors.append(
                                f"{prefix}: stat_modifiers['{stat}'] must be numeric, got {val!r}"
                            )

            # ammo_cost values must be positive ints if present
            self._check_positive_int_map(
                errors, prefix, "ammo_cost", entry.get("ammo_cost")
            )

            # craft_cost values must be positive ints if present (same shape as
            # ammo_cost) — resources spent per unit via the `craft` command.
            self._check_positive_int_map(
                errors, prefix, "craft_cost", entry.get("craft_cost")
            )

            # ---- category (Req 3.4) ------------------------------------- #
            # A missing category defaults to "armor" in the populator, so an
            # absent category is treated as the default rather than an error.
            category = entry.get("category")
            if category is not None and category not in ITEM_CATEGORIES:
                errors.append(
                    f"{prefix}: category '{category}' not one of {list(ITEM_CATEGORIES)}"
                )
            effective_category = category if category is not None else "armor"

            # ---- slot: required for Gear, not for Supply (Req 3.5, 3.6) -- #
            slot = entry.get("slot")
            if effective_category in GEAR_CATEGORIES:
                if slot is None:
                    errors.append(
                        f"{prefix}: slot is required for '{effective_category}' "
                        f"(Gear) items"
                    )
                elif slot not in EQUIPMENT_SLOTS:
                    errors.append(
                        f"{prefix}: slot '{slot}' not in EQUIPMENT_SLOTS "
                        f"{list(EQUIPMENT_SLOTS)}"
                    )
                # A `weapon`-category item must occupy the `weapon` slot:
                # combat resolves the attacker's weapon via the `weapon` slot
                # specifically, so a weapon parked in a body slot (e.g. `head`)
                # would never be found and could never be used to attack.
                # (`armor`/`accessory` gear may occupy any body slot — e.g. a
                # scope in `eyes`, a jetpack in `back`.)
                elif effective_category == "weapon" and slot != "weapon":
                    errors.append(
                        f"{prefix}: weapon items must use slot 'weapon', got '{slot}'"
                    )

            # ---- weapon_type: required iff weapon, rejected otherwise (Req 4.5)
            weapon_type = entry.get("weapon_type")
            if effective_category == "weapon":
                if weapon_type not in WEAPON_TYPES:
                    errors.append(
                        f"{prefix}: weapon_type must be one of {list(WEAPON_TYPES)} "
                        f"for weapon items, got {weapon_type!r}"
                    )
            elif weapon_type is not None:
                errors.append(
                    f"{prefix}: weapon_type is only valid on weapon-category "
                    f"items, got {weapon_type!r}"
                )

            # ---- ranged-weapon ammo fields must be positive ints (Req 5.1) #
            if effective_category == "weapon" and weapon_type == "ranged":
                self._check_positive_int(
                    errors, prefix, "ammo_per_shot", entry.get("ammo_per_shot")
                )
                mag = entry.get("magazine_size")
                self._check_positive_int(errors, prefix, "magazine_size", mag)
                # A ranged weapon that consumes counted ammo (declares an
                # ammo_type) MUST declare a magazine (Req 5.1). Without it the
                # weapon seeds db.loaded=0 and can never fire — a load-time
                # brick, so reject it up front rather than shipping dead gear.
                if entry.get("ammo_type") is not None and mag is None:
                    errors.append(
                        f"{prefix}: ranged weapon with ammo_type must declare a "
                        f"positive magazine_size"
                    )

            # ---- max_stack must be a positive int (Req 10.4) ------------ #
            self._check_positive_int(
                errors, prefix, "max_stack", entry.get("max_stack")
            )

            # ---- weight must be a number >= 0 (Req 15.1) ---------------- #
            weight = entry.get("weight")
            if weight is not None and (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or weight < 0
            ):
                errors.append(
                    f"{prefix}: weight must be a number >= 0, got {weight!r}"
                )

            # ---- effect.type for consumable/throwable/mine (Req 6.4, 13.5) - #
            effect = entry.get("effect")
            if effect is not None and effective_category in ("consumable", "throwable", "mine"):
                if not isinstance(effect, dict):
                    errors.append(
                        f"{prefix}: effect must be a dict, got {type(effect).__name__}"
                    )
                else:
                    etype = effect.get("type")
                    if etype not in EFFECT_TYPES:
                        errors.append(
                            f"{prefix}: effect.type must be one of {list(EFFECT_TYPES)}, "
                            f"got {etype!r}"
                        )
                    # ---- bomb fuse fields (grenades + mines) ------------- #
                    # A bomb (throwable/mine) declares a fuse the player must set
                    # before deploying. Validate bomb_type and the fuse bounds so
                    # a misconfigured bomb fails at LOAD, not at detonation time.
                    if effective_category in BOMB_CATEGORIES:
                        errors.extend(
                            self._validate_bomb_effect(prefix, effect, effective_category)
                        )

        return errors

    @staticmethod
    def _validate_bomb_effect(prefix: str, effect: dict, category: str) -> list:
        """Validate the bomb-specific effect fields (bomb_type + fuse bounds).

        A ``throwable`` item must be ``bomb_type: grenade`` and a ``mine`` item
        ``bomb_type: mine`` (the category and the discriminator must agree, so a
        grenade can never be armed as a mine or vice-versa). ``fuse_min`` /
        ``fuse_max`` / ``fuse_default`` (if present) must be positive ints with
        ``fuse_min <= fuse_default <= fuse_max``. Absent fuse fields fall back to
        the module-level DEFAULT_BOMB_FUSE_* constants at runtime, so they are
        optional here — but a declared value must be well-formed.
        """
        out = []
        expected = "grenade" if category == "throwable" else "mine"
        bomb_type = effect.get("bomb_type")
        if bomb_type is not None and bomb_type != expected:
            out.append(
                f"{prefix}: effect.bomb_type must be '{expected}' for a "
                f"'{category}' item, got {bomb_type!r}"
            )
        fuse_vals = {}
        for fkey in ("fuse_min", "fuse_max", "fuse_default"):
            v = effect.get(fkey)
            if v is None:
                continue
            before = len(out)
            SchemaValidator._check_positive_int(out, prefix, f"effect.{fkey}", v)
            if len(out) == before:
                fuse_vals[fkey] = v
        fmin = fuse_vals.get("fuse_min")
        fmax = fuse_vals.get("fuse_max")
        fdef = fuse_vals.get("fuse_default")
        if fmin is not None and fmax is not None and fmin > fmax:
            out.append(f"{prefix}: effect.fuse_min ({fmin}) must be <= fuse_max ({fmax})")
        if fdef is not None:
            if fmin is not None and fdef < fmin:
                out.append(f"{prefix}: effect.fuse_default ({fdef}) must be >= fuse_min ({fmin})")
            if fmax is not None and fdef > fmax:
                out.append(f"{prefix}: effect.fuse_default ({fdef}) must be <= fuse_max ({fmax})")
        return out

    # ------------------------------------------------------------------ #
    #  Ranks
    # ------------------------------------------------------------------ #
    def validate_ranks(self, data: list[dict]) -> list[str]:
        """Validate a list of rank definition dicts."""
        errors: list[str] = []
        required = {"name", "level", "xp_threshold", "agent_cap", "planet_access"}
        levels_seen: set[int] = set()
        level_xp: list[tuple[int, int]] = []

        for prefix, entry in self._iter_dict_entries(data, "ranks", required, errors):
            level = entry.get("level")
            if isinstance(level, int):
                if level <= 0:
                    errors.append(f"{prefix}: level must be a positive integer, got {level}")
                elif level in levels_seen:
                    errors.append(f"{prefix}: duplicate level {level}")
                levels_seen.add(level)

                xp = entry.get("xp_threshold")
                if isinstance(xp, int):
                    level_xp.append((level, xp))

            # agent_cap must be a positive int
            self._check_positive_int(
                errors, prefix, "agent_cap", entry.get("agent_cap")
            )

            # planet_access must be a list of strings
            pa = entry.get("planet_access")
            if pa is not None:
                if not isinstance(pa, list):
                    errors.append(
                        f"{prefix}: planet_access must be a list, got {type(pa).__name__}"
                    )
                else:
                    for pi, item in enumerate(pa):
                        if not isinstance(item, str):
                            errors.append(
                                f"{prefix}: planet_access[{pi}] must be a string, "
                                f"got {type(item).__name__}"
                            )

        # xp_thresholds must be strictly increasing when sorted by level
        level_xp.sort(key=lambda t: t[0])
        for i in range(1, len(level_xp)):
            prev_lvl, prev_xp = level_xp[i - 1]
            cur_lvl, cur_xp = level_xp[i]
            if cur_xp <= prev_xp:
                errors.append(
                    f"ranks: xp_threshold for level {cur_lvl} ({cur_xp}) must be "
                    f"greater than level {prev_lvl} ({prev_xp})"
                )

        return errors

    # ------------------------------------------------------------------ #
    #  Technologies
    # ------------------------------------------------------------------ #
    def validate_technologies(self, data: list[dict]) -> list[str]:
        """Validate a list of technology definition dicts."""
        errors: list[str] = []
        required = {"name", "key", "required_rank", "resource_cost", "research_ticks"}
        for prefix, entry in self._iter_dict_entries(data, "technologies", required, errors):
            rt = entry.get("research_ticks")
            if isinstance(rt, int) and rt <= 0:
                errors.append(f"{prefix}: research_ticks must be > 0, got {rt}")
            elif rt is not None and not isinstance(rt, int):
                errors.append(
                    f"{prefix}: research_ticks must be an integer, got {type(rt).__name__}"
                )

        return errors

    # ------------------------------------------------------------------ #
    #  Powerups
    # ------------------------------------------------------------------ #
    def validate_powerups(self, data: list[dict]) -> list[str]:
        """Validate a list of powerup definition dicts."""
        errors: list[str] = []
        required = {
            "name", "key", "required_rank", "effect_type",
            "effect_value", "duration_ticks", "cooldown_ticks",
        }
        for prefix, entry in self._iter_dict_entries(data, "powerups", required, errors):
            dt = entry.get("duration_ticks")
            if isinstance(dt, int) and dt <= 0:
                errors.append(f"{prefix}: duration_ticks must be > 0, got {dt}")

            ct = entry.get("cooldown_ticks")
            if isinstance(ct, int) and ct <= 0:
                errors.append(f"{prefix}: cooldown_ticks must be > 0, got {ct}")

        return errors

    # ------------------------------------------------------------------ #
    #  Ability gates
    # ------------------------------------------------------------------ #
    def validate_ability_gates(self, data: list[dict]) -> list[str]:
        """Validate a list of ability-gate definition dicts."""
        errors: list[str] = []
        required = {"key", "required_level"}
        keys_seen: set[str] = set()

        for prefix, entry in self._iter_dict_entries(data, "ability_gates", required, errors):
            # key must be a non-empty string; duplicates reported by name
            key = entry.get("key")
            if "key" in entry:
                if not isinstance(key, str) or not key:
                    errors.append(
                        f"{prefix}: key must be a non-empty string, got {key!r}"
                    )
                elif key in keys_seen:
                    errors.append(f"{prefix}: duplicate key '{key}'")
                else:
                    keys_seen.add(key)

            # required_level must be an int in range 1..MAX_LEVEL
            # (bool is a subclass of int, so reject it explicitly)
            rl = entry.get("required_level")
            if "required_level" in entry:
                if not isinstance(rl, int) or isinstance(rl, bool):
                    errors.append(
                        f"{prefix}: required_level must be an integer, "
                        f"got {type(rl).__name__}"
                    )
                elif rl < 1 or rl > MAX_LEVEL:
                    errors.append(
                        f"{prefix}: required_level must be between 1 and "
                        f"{MAX_LEVEL}, got {rl}"
                    )

        return errors

    # ------------------------------------------------------------------ #
    #  Terrain
    # ------------------------------------------------------------------ #
    def validate_terrain(self, data: dict) -> list[str]:
        """Validate a terrain definition dict (terrain list + planets list)."""
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"terrain: expected a dict, got {type(data).__name__}"]

        terrain_list = data.get("terrain", [])
        if not isinstance(terrain_list, list):
            errors.append(
                f"terrain.terrain: expected a list, got {type(terrain_list).__name__}"
            )
            terrain_list = []

        required = {"terrain_type", "map_symbol"}
        terrain_types: set[str] = set()

        for prefix, entry in self._iter_dict_entries(terrain_list, "terrain", required, errors):
            ms = entry.get("map_symbol")
            if isinstance(ms, str) and len(ms) != 2:
                errors.append(f"{prefix}: map_symbol must be 2 characters, got '{ms}'")

            tt = entry.get("terrain_type")
            if isinstance(tt, str):
                terrain_types.add(tt)

        # Validate planet references to terrain types
        planets_list = data.get("planets", [])
        if isinstance(planets_list, list):
            for idx, planet in enumerate(planets_list):
                prefix = f"planets[{idx}]"
                if not isinstance(planet, dict):
                    errors.append(f"{prefix}: expected dict, got {type(planet).__name__}")
                    continue
                for tt in planet.get("terrain_types", []):
                    if tt not in terrain_types:
                        errors.append(
                            f"{prefix}: terrain_type '{tt}' not found in terrain definitions"
                        )

        return errors

    # ------------------------------------------------------------------ #
    #  Balance
    # ------------------------------------------------------------------ #
    def validate_balance(self, data: dict) -> list[str]:
        """Validate a balance configuration dict."""
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"balance: expected a dict, got {type(data).__name__}"]

        # Derived from BalanceConfig's dataclass fields at module-import time.
        # See _balance_fields_by_type() — adding a new int/float/bool field to
        # BalanceConfig automatically validates it here; no second list to edit.
        int_fields = _BALANCE_INT_FIELDS
        float_fields = _BALANCE_FLOAT_FIELDS
        bool_fields = _BALANCE_BOOL_FIELDS
        # Resource->int maps: keys are resource names, values positive ints
        resource_map_fields = ["base_training_cost"]
        # Level->float maps: keys are building levels (1-5), values fractions
        level_rate_map_fields = ["demolish_refund_rates"]

        for field in int_fields:
            val = data.get(field)
            if val is not None and not isinstance(val, int):
                errors.append(
                    f"balance.{field}: expected int, got {type(val).__name__}"
                )

        for field in float_fields:
            val = data.get(field)
            if val is not None and not isinstance(val, (int, float)):
                errors.append(
                    f"balance.{field}: expected float, got {type(val).__name__}"
                )

        for field in bool_fields:
            val = data.get(field)
            if val is not None and not isinstance(val, bool):
                errors.append(
                    f"balance.{field}: expected bool, got {type(val).__name__}"
                )

        # Range checks: these tunables must be non-negative. The runtime treats
        # 0/non-positive as "disabled" (regen off; free repairs), so a NEGATIVE
        # value is a misconfiguration that would silently disable the feature
        # instead of erroring — catch it here. NaN also fails (nan >= 0 is
        # False), so a malformed float can't slip through the type check.
        non_negative_fields = [
            "hp_regen_percent", "hp_regen_interval_ticks", "repair_cost_fraction",
            "attack_cooldown_seconds", "linkdead_grace_seconds",
        ]
        for field in non_negative_fields:
            val = data.get(field)
            if (
                val is not None
                and isinstance(val, (int, float))
                and not isinstance(val, bool)
                and not (val >= 0)
            ):
                errors.append(
                    f"balance.{field}: must be >= 0, got {val!r}"
                )

        # Resource->positive-int maps (e.g. base_training_cost)
        for field in resource_map_fields:
            val = data.get(field)
            if val is None:
                continue
            if not isinstance(val, dict):
                errors.append(
                    f"balance.{field}: expected dict, got {type(val).__name__}"
                )
                continue
            for res, amount in val.items():
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    errors.append(
                        f"balance.{field}['{res}']: must be a positive integer, "
                        f"got {amount!r}"
                    )

        # resource_weights: keys must be a subset of RESOURCE_TYPES (case-sensitive
        # title-case), values must be numbers >= 0.
        rw = data.get("resource_weights")
        if rw is not None:
            if not isinstance(rw, dict):
                errors.append(
                    f"balance.resource_weights: expected dict, got {type(rw).__name__}"
                )
            else:
                for res, weight in rw.items():
                    if res not in RESOURCE_TYPES:
                        errors.append(
                            f"balance.resource_weights['{res}']: unknown resource; "
                            f"must be one of {RESOURCE_TYPES}"
                        )
                    if (
                        not isinstance(weight, (int, float))
                        or isinstance(weight, bool)
                        or weight < 0
                    ):
                        errors.append(
                            f"balance.resource_weights['{res}']: must be a number "
                            f">= 0, got {weight!r}"
                        )

        # Level(1-5)->fraction maps (e.g. demolish_refund_rates)
        for field in level_rate_map_fields:
            val = data.get(field)
            if val is None:
                continue
            if not isinstance(val, dict):
                errors.append(
                    f"balance.{field}: expected dict, got {type(val).__name__}"
                )
                continue
            for lvl, rate in val.items():
                k = int(lvl) if isinstance(lvl, str) and lvl.isdigit() else lvl
                if not isinstance(k, int) or k < 1 or k > 5:
                    errors.append(
                        f"balance.{field}: key must be 1-5, got {lvl!r}"
                    )
                if not isinstance(rate, (int, float)) or isinstance(rate, bool):
                    errors.append(
                        f"balance.{field}[{lvl}]: expected number, "
                        f"got {type(rate).__name__}"
                    )

        # alliance_level_thresholds: summed-level(int >= 0) -> tier(int >= 1) map.
        alt = data.get("alliance_level_thresholds")
        if alt is not None:
            if not isinstance(alt, dict):
                errors.append(
                    f"balance.alliance_level_thresholds: expected dict, "
                    f"got {type(alt).__name__}"
                )
            else:
                for key, tier in alt.items():
                    k = int(key) if isinstance(key, str) and key.isdigit() else key
                    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
                        errors.append(
                            f"balance.alliance_level_thresholds: key must be a "
                            f"non-negative int, got {key!r}"
                        )
                    if not isinstance(tier, int) or isinstance(tier, bool) or tier < 1:
                        errors.append(
                            f"balance.alliance_level_thresholds[{key}]: tier must be "
                            f"a positive int, got {tier!r}"
                        )

        return errors

    # ------------------------------------------------------------------ #
    #  Shared field/reference validators
    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_positive_int(errors, prefix, name, value, *, allow_zero=False):
        """Append an error if *value* is not a positive (or non-negative) int.

        ``bool`` is a subclass of ``int`` but is never a valid count, so it is
        rejected uniformly here. ``None`` is skipped (the field is optional).
        With ``allow_zero`` the bound is ``>= 0`` ("non-negative integer");
        otherwise ``> 0`` ("positive integer"). The message keeps the same
        ``"{name} must be a (positive|non-negative) integer, got X"`` wording
        the per-field clauses used, so callers reading errors see no change.
        """
        if value is None:
            return
        label = "non-negative" if allow_zero else "positive"
        # Order matters: the type/bool guards must short-circuit BEFORE the
        # numeric comparison, or a non-numeric value (str/list/dict) would raise
        # TypeError on ``value > 0`` instead of producing a validation error.
        bad = (
            not isinstance(value, int)
            or isinstance(value, bool)
            or (value < 0 if allow_zero else value <= 0)
        )
        if bad:
            errors.append(
                f"{prefix}: {name} must be a {label} integer, got {value!r}"
            )

    @staticmethod
    def _check_positive_int_map(errors, prefix, name, mapping):
        """Validate a ``{resource: positive-int}`` map field.

        When *mapping* is not a dict, appends ``"{name} must be a dict"`` and
        returns. Otherwise every value must be a positive int (``bool`` rejected)
        with the ``"{name}['{res}'] must be a positive integer, got X"`` wording.
        """
        if mapping is None:
            return
        if not isinstance(mapping, dict):
            errors.append(f"{prefix}: {name} must be a dict, got {type(mapping).__name__}")
            return
        for res, val in mapping.items():
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                errors.append(
                    f"{prefix}: {name}['{res}'] must be a positive integer, got {val!r}"
                )

    @staticmethod
    def _iter_dict_entries(data, label, required, errors):
        """Yield ``(prefix, entry)`` for each valid dict entry in a list.

        The shared per-file scaffold every list validator repeats: reject a
        non-list ``data`` (``"{label}: expected a list, got X"``), then for each
        entry emit ``"{label}[i]: expected dict, got X"`` (and skip it) for a
        non-dict, and ``"{label}[i]: missing required fields: [...]"`` for any
        missing *required* keys — before yielding ``(prefix, entry)`` so the
        caller performs only its own field-specific checks. Errors are appended
        to the caller's *errors* list; nothing is yielded for a non-list.
        """
        if not isinstance(data, list):
            errors.append(f"{label}: expected a list, got {type(data).__name__}")
            return
        for idx, entry in enumerate(data):
            prefix = f"{label}[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue
            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")
            yield prefix, entry

    @staticmethod
    def _check_required_rank(errors, label, defs, rank_names):
        """Append an FK error for each def whose ``required_rank`` is unknown.

        The identical "required_rank must name a loaded rank" check shared by the
        item / technology / powerup cross-validation loops.
        """
        for key, d in defs.items():
            if d.required_rank and d.required_rank not in rank_names:
                errors.append(
                    f"{label} '{key}': required_rank '{d.required_rank}' "
                    f"not found in rank definitions"
                )

    # ------------------------------------------------------------------ #
    #  Cross-validation
    # ------------------------------------------------------------------ #
    def cross_validate(self, registry) -> list[str]:
        """Validate inter-file references after all files are loaded.

        Args:
            registry: A DataRegistry instance with all definitions loaded.

        Returns:
            List of error strings (empty = valid).
        """
        errors: list[str] = []

        terrain_types = set(registry.terrain.keys())
        rank_names = {r.name for r in registry.ranks}
        building_abbrs = set(registry.buildings.keys())
        item_keys = set(registry.items.keys())

        # Building required_terrain → valid terrain types
        for abbr, bdef in registry.buildings.items():
            if bdef.required_terrain and bdef.required_terrain not in terrain_types:
                errors.append(
                    f"building '{abbr}': required_terrain '{bdef.required_terrain}' "
                    f"not found in terrain definitions"
                )

        # Item required_rank → valid rank names
        self._check_required_rank(errors, "item", registry.items, rank_names)

        # Item ammo_type → must reference an existing 'ammo'-category item
        # (Req 5.7); melee weapons must not declare any ammo fields (Req 5.8).
        for key, idef in registry.items.items():
            # ammo_type FK: when set, it must name an existing ammo item.
            if idef.ammo_type is not None:
                ref = registry.items.get(idef.ammo_type)
                if ref is None:
                    errors.append(
                        f"item '{key}': ammo_type '{idef.ammo_type}' "
                        f"not found in item definitions"
                    )
                elif ref.category != "ammo":
                    errors.append(
                        f"item '{key}': ammo_type '{idef.ammo_type}' "
                        f"is not an 'ammo'-category item "
                        f"(category '{ref.category}')"
                    )

            # Melee weapons carry no ammunition. ammo_per_shot defaults to 1,
            # so only a non-default value is treated as "declared" — ammo_type
            # and magazine_size are None by default, so any non-None value is a
            # violation.
            if idef.category == "weapon" and idef.weapon_type == "melee":
                if idef.ammo_type is not None:
                    errors.append(
                        f"item '{key}': melee weapon must not declare "
                        f"ammo_type '{idef.ammo_type}'"
                    )
                if idef.magazine_size is not None:
                    errors.append(
                        f"item '{key}': melee weapon must not declare "
                        f"magazine_size {idef.magazine_size}"
                    )
                if idef.ammo_per_shot != 1:
                    errors.append(
                        f"item '{key}': melee weapon must not declare "
                        f"ammo_per_shot {idef.ammo_per_shot}"
                    )

        # Technology required_rank → valid rank names
        self._check_required_rank(
            errors, "technology", registry.technologies, rank_names
        )

        # Powerup required_rank → valid rank names
        self._check_required_rank(errors, "powerup", registry.powerups, rank_names)

        # production_map building abbreviations → valid buildings
        # production_map item keys → valid items
        for babbr, ikeys in registry.item_production_map.items():
            if babbr not in building_abbrs:
                errors.append(
                    f"production_map: building abbreviation '{babbr}' "
                    f"not found in building definitions"
                )
            for ik in ikeys:
                if ik not in item_keys:
                    errors.append(
                        f"production_map['{babbr}']: item key '{ik}' "
                        f"not found in item definitions"
                    )

        # Planet terrain_weights → terrain types must exist in terrain definitions
        for pname, pdef in registry.planets.items():
            for tt in pdef.terrain_types:
                if tt not in terrain_types:
                    errors.append(
                        f"planet '{pname}': terrain_weight type '{tt}' "
                        f"not found in terrain definitions"
                    )

        # Resource-name references → the canonical RESOURCE_TYPES set.
        # 'Resource' has no definition file (it's just string keys), so a
        # typo in any cost/ammo/tech-cost/terrain-yield previously loaded
        # silently and only surfaced at runtime. Validate them here.
        from world.constants import RESOURCE_TYPES

        valid_resources = set(RESOURCE_TYPES)

        for abbr, bdef in registry.buildings.items():
            for res in (bdef.cost or {}):
                if res not in valid_resources:
                    errors.append(
                        f"building '{abbr}': cost resource '{res}' "
                        f"not a known resource {sorted(valid_resources)}"
                    )
            if bdef.produces and bdef.produces not in valid_resources:
                errors.append(
                    f"building '{abbr}': produces '{bdef.produces}' "
                    f"not a known resource {sorted(valid_resources)}"
                )

        for key, idef in registry.items.items():
            for res in (idef.ammo_cost or {}):
                if res not in valid_resources:
                    errors.append(
                        f"item '{key}': ammo_cost resource '{res}' "
                        f"not a known resource {sorted(valid_resources)}"
                    )

        for key, tdef in registry.technologies.items():
            rcost = tdef.resource_cost
            if rcost and not isinstance(rcost, dict):
                errors.append(
                    f"technology '{key}': resource_cost must be a mapping, "
                    f"got {type(rcost).__name__}"
                )
                rcost = {}
            for res in (rcost or {}):
                if res not in valid_resources:
                    errors.append(
                        f"technology '{key}': resource_cost resource '{res}' "
                        f"not a known resource {sorted(valid_resources)}"
                    )

        for ttype, tdef in registry.terrain.items():
            if tdef.resource_type and tdef.resource_type not in valid_resources:
                errors.append(
                    f"terrain '{ttype}': resource_type '{tdef.resource_type}' "
                    f"not a known resource {sorted(valid_resources)}"
                )

        # Building unlocks → valid building abbreviations. This is the
        # runtime-consumed unlocks field (typeclasses.rooms reads it), keyed
        # by abbreviation like registry.buildings. NOTE: RankDef.unlocks is a
        # separate, cosmetic-only field keyed by building *name* and carrying
        # non-building tokens ('All', 'Barracks_L2'); it is intentionally NOT
        # validated here.
        for abbr, bdef in registry.buildings.items():
            for unlocked in (bdef.unlocks or []):
                if unlocked not in building_abbrs:
                    errors.append(
                        f"building '{abbr}': unlocks '{unlocked}' "
                        f"not found in building definitions (expects an "
                        f"abbreviation)"
                    )

        return errors
