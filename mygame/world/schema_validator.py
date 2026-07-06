"""
Schema Validator for RTS Combat Overworld definition files.

Validates raw YAML dicts against expected schemas before they enter the
Data Registry. Each validation method returns a list of error strings
(empty list = valid).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from world.constants import MAX_LEVEL

if TYPE_CHECKING:
    pass  # DataRegistry imported only for type hints in cross_validate


class SchemaValidator:
    """Validates definition file contents against expected schemas."""

    # ------------------------------------------------------------------ #
    #  Buildings
    # ------------------------------------------------------------------ #
    def validate_buildings(self, data: list[dict]) -> list[str]:
        """Validate a list of building definition dicts."""
        errors: list[str] = []
        if not isinstance(data, list):
            return [f"buildings: expected a list, got {type(data).__name__}"]

        required = {
            "name", "abbreviation", "cost", "max_health", "requires_hq", "category",
            "build_time_seconds", "max_level", "rank_requirement", "requires_agent",
            "storage_capacity",
        }
        for idx, entry in enumerate(data):
            prefix = f"buildings[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

            # abbreviation must be 2 chars
            abbr = entry.get("abbreviation")
            if isinstance(abbr, str) and len(abbr) != 2:
                errors.append(f"{prefix}: abbreviation must be 2 characters, got '{abbr}'")

            # cost values must be positive ints
            cost = entry.get("cost")
            if isinstance(cost, dict):
                for res, val in cost.items():
                    if not isinstance(val, int) or val <= 0:
                        errors.append(
                            f"{prefix}: cost['{res}'] must be a positive integer, got {val!r}"
                        )

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
            bts = entry.get("build_time_seconds")
            if bts is not None:
                if not isinstance(bts, int) or bts <= 0:
                    errors.append(
                        f"{prefix}: build_time_seconds must be a positive integer, got {bts!r}"
                    )

            # max_level must be a positive int within the structural ceiling
            ml = entry.get("max_level")
            if ml is not None:
                from world.constants import MAX_BUILDING_LEVEL
                if not isinstance(ml, int) or ml <= 0:
                    errors.append(
                        f"{prefix}: max_level must be a positive integer, got {ml!r}"
                    )
                elif ml > MAX_BUILDING_LEVEL:
                    errors.append(
                        f"{prefix}: max_level {ml} exceeds MAX_BUILDING_LEVEL "
                        f"({MAX_BUILDING_LEVEL})"
                    )

            # rank_requirement must be a positive int
            rr = entry.get("rank_requirement")
            if rr is not None:
                if not isinstance(rr, int) or rr <= 0:
                    errors.append(
                        f"{prefix}: rank_requirement must be a positive integer, got {rr!r}"
                    )

            # requires_agent must be a bool
            ra = entry.get("requires_agent")
            if ra is not None and not isinstance(ra, bool):
                errors.append(
                    f"{prefix}: requires_agent must be a boolean, got {type(ra).__name__}"
                )

            # storage_capacity must be a non-negative int
            sc = entry.get("storage_capacity")
            if sc is not None:
                if not isinstance(sc, int) or sc < 0:
                    errors.append(
                        f"{prefix}: storage_capacity must be a non-negative integer, got {sc!r}"
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

        required = {"key", "name", "slot"}
        item_keys: set[str] = set()

        for idx, entry in enumerate(items_list):
            prefix = f"items[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

            key = entry.get("key")
            if isinstance(key, str):
                item_keys.add(key)

            # stat_modifiers values must be numeric
            sm = entry.get("stat_modifiers")
            if sm is not None:
                if not isinstance(sm, dict):
                    errors.append(
                        f"{prefix}: stat_modifiers must be a dict, got {type(sm).__name__}"
                    )
                else:
                    for stat, val in sm.items():
                        if not isinstance(val, (int, float)):
                            errors.append(
                                f"{prefix}: stat_modifiers['{stat}'] must be numeric, got {val!r}"
                            )

            # ammo_cost values must be positive ints if present
            ac = entry.get("ammo_cost")
            if ac is not None:
                if not isinstance(ac, dict):
                    errors.append(
                        f"{prefix}: ammo_cost must be a dict, got {type(ac).__name__}"
                    )
                else:
                    for res, val in ac.items():
                        if not isinstance(val, int) or val <= 0:
                            errors.append(
                                f"{prefix}: ammo_cost['{res}'] must be a positive integer, got {val!r}"
                            )

        return errors

    # ------------------------------------------------------------------ #
    #  Ranks
    # ------------------------------------------------------------------ #
    def validate_ranks(self, data: list[dict]) -> list[str]:
        """Validate a list of rank definition dicts."""
        errors: list[str] = []
        if not isinstance(data, list):
            return [f"ranks: expected a list, got {type(data).__name__}"]

        required = {"name", "level", "xp_threshold", "agent_cap", "planet_access"}
        levels_seen: set[int] = set()
        level_xp: list[tuple[int, int]] = []

        for idx, entry in enumerate(data):
            prefix = f"ranks[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

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
            ac = entry.get("agent_cap")
            if ac is not None:
                if not isinstance(ac, int) or ac <= 0:
                    errors.append(
                        f"{prefix}: agent_cap must be a positive integer, got {ac!r}"
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
        if not isinstance(data, list):
            return [f"technologies: expected a list, got {type(data).__name__}"]

        required = {"name", "key", "required_rank", "resource_cost", "research_ticks"}
        for idx, entry in enumerate(data):
            prefix = f"technologies[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

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
        if not isinstance(data, list):
            return [f"powerups: expected a list, got {type(data).__name__}"]

        required = {
            "name", "key", "required_rank", "effect_type",
            "effect_value", "duration_ticks", "cooldown_ticks",
        }
        for idx, entry in enumerate(data):
            prefix = f"powerups[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

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
        if not isinstance(data, list):
            return [f"ability_gates: expected a list, got {type(data).__name__}"]

        required = {"key", "required_level"}
        keys_seen: set[str] = set()

        for idx, entry in enumerate(data):
            prefix = f"ability_gates[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

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

        for idx, entry in enumerate(terrain_list):
            prefix = f"terrain[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{prefix}: expected dict, got {type(entry).__name__}")
                continue

            missing = required - entry.keys()
            if missing:
                errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

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

        int_fields = [
            "turret_damage", "turret_radius", "xp_kill", "xp_building_destroy",
            "xp_death_loss", "gather_amount", "player_default_health",
            "resource_respawn_ticks", "combat_lockout_ticks", "chunk_size",
            "save_interval", "metrics_interval",
            "agent_xp_harvest", "agent_xp_delivery", "agent_xp_construction",
            "agent_xp_combat", "agent_xp_time_served", "agent_xp_death_loss",
            # Migrated economy tuning (formerly world.constants literals)
            "base_training_ticks",
            "harvest_cooldown_ticks", "harvest_yield_per_action",
            "extractor_harvest_multiplier", "extractor_base_capacity",
            "extractor_capacity_per_level", "vault_base_capacity",
            "vault_capacity_per_level", "upgrade_cost_base", "upgrade_time_base",
            # Coordinate-world / GC knobs. Present in balance.yaml and now read
            # generically by DataRegistry._build_balance, so validate their type
            # here too (previously the explicit constructor silently dropped them).
            "player_vision_radius", "building_vision_radius", "room_cache_max_size",
            "gc_interval_ticks", "gc_min_age_ticks", "map_border_tiles",
        ]
        float_fields = [
            "xp_damage", "tick_interval",
            "academy_training_reduction_per_level", "extractor_level_bonus",
            "turret_level_bonus", "demolish_refund_default",
        ]
        bool_fields = ["metrics_enabled"]
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

        # production_scaling keys must be 1-5
        ps = data.get("production_scaling")
        if ps is not None:
            if not isinstance(ps, dict):
                errors.append(
                    f"balance.production_scaling: expected dict, got {type(ps).__name__}"
                )
            else:
                for key, val in ps.items():
                    k = int(key) if isinstance(key, str) and key.isdigit() else key
                    if not isinstance(k, int) or k < 1 or k > 5:
                        errors.append(
                            f"balance.production_scaling: key must be 1-5, got {key!r}"
                        )
                    if not isinstance(val, int):
                        errors.append(
                            f"balance.production_scaling[{key}]: expected int, got {type(val).__name__}"
                        )

        return errors

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
        for key, idef in registry.items.items():
            if idef.required_rank and idef.required_rank not in rank_names:
                errors.append(
                    f"item '{key}': required_rank '{idef.required_rank}' "
                    f"not found in rank definitions"
                )

        # Technology required_rank → valid rank names
        for key, tdef in registry.technologies.items():
            if tdef.required_rank and tdef.required_rank not in rank_names:
                errors.append(
                    f"technology '{key}': required_rank '{tdef.required_rank}' "
                    f"not found in rank definitions"
                )

        # Powerup required_rank → valid rank names
        for key, pdef in registry.powerups.items():
            if pdef.required_rank and pdef.required_rank not in rank_names:
                errors.append(
                    f"powerup '{key}': required_rank '{pdef.required_rank}' "
                    f"not found in rank definitions"
                )

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
