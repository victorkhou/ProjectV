"""
Schema Validator for RTS Combat Overworld definition files.

Validates raw YAML dicts against expected schemas before they enter the
Data Registry. Each validation method returns a list of error strings
(empty list = valid).
"""

from __future__ import annotations

import dataclasses
import math
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

#: Valid modifier kinds for terrain affinities (terrain-strategy). Single
#: source of truth — the DataRegistry imports this for class-affinity parsing
#: so the validator and loader can never drift.
_AFFINITY_KINDS = frozenset({"vision", "movement", "defense"})

#: Rarity tiers (item-loot-economy design §3.1) accepted in
#: ``balance.rarity_table`` weights. Matches ``loot_roller.RARITY_ORDER``
#: (lowercase; the display layer capitalizes/colors).
RARITY_TIERS = frozenset({"common", "uncommon", "rare", "epic", "legendary"})


def _is_num(val) -> bool:
    """True for real, FINITE numbers (bool excluded — it is an int subclass).

    The single numeric check the per-file validators share (review M1 + DRY:
    this replaces five identical local closures). NaN/inf are REJECTED:
    every NaN comparison is False, so a NaN band bound (``min: .nan`` parses
    fine in YAML) used to sail through ``min <= max`` checks and flow into
    combat as ``rolled_stats = nan``. Non-finite numbers now fail the load
    exactly like non-numbers, matching ``loot_roller._num``.
    """
    return (isinstance(val, (int, float)) and not isinstance(val, bool)
            and math.isfinite(val))

#: Stat axes an affix may target (item-loot-economy tasks 2.1 + 3.4): the
#: AGGREGATING axes — ``damage_bonus``/``damage_reduction`` sum across gear
#: via ``get_stat_total`` (see ``AGGREGATED_STATS``) and every
#: ``<type>_resist`` aggregates for free because combat builds the resist key
#: as ``f"{damage_type}_resist"`` — plus ``range``, unlocked by task 3.4 now
#: that the R8 range-resolution hook exists: ``_resolve_weapon_range`` reads
#: the weapon INSTANCE via ``get_stat``, which adds affix magnitudes on the
#: same axis, so a ``+range`` affix on the weapon flows into combat (and only
#: on the weapon — range never aggregates across equipped items, R8.1).
AFFIX_STAT_AXES = frozenset({
    "damage_bonus",
    "damage_reduction",
    "fire_resist",
    "psychic_resist",
    "blast_resist",
    "poison_resist",
    "range",
})

#: Proc keys an affix may declare (``proc: poison`` etc.). ``poison`` was
#: unlocked by task 3.4 once the Phase-3 poison-DoT hook (R9) landed: a
#: poison-proc affix on a weapon rides every landed hit as a DoT (see
#: ``combat_engine._apply_weapon_procs``). Any future proc needs its combat
#: consumer added BEFORE its key is added here — no dead proc keys.
AFFIX_PROC_KEYS: frozenset[str] = frozenset({"poison"})

#: Known affix pool names — one pool per Gear category (design §3.3 keys the
#: registry by category: weapon / armor / accessory). An item's
#: ``roll_spec.affix_pool`` names one of these.
AFFIX_POOL_NAMES = frozenset(GEAR_CATEGORIES)

#: Damage types a ``type: damage_type`` insert may convert a weapon to
#: (item-loot-economy §4.3): the typed damages the combat engine dispatches on
#: (``CombatEngine._get_damage_type`` consumers — fire/poison DoT, psychic
#: resist read, blast shred). ``physical`` is deliberately excluded: it is the
#: default, so "converting" to it is an authoring error, not an insert.
INSERT_DAMAGE_TYPES = frozenset({"fire", "psychic", "blast", "poison"})


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
            # `max_hp` is a wired effect (raises hp_max — task 6.4); `accuracy`
            # is an accepted numeric key with no wired effect (D6).
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

            # ---- roll_spec shape (item-loot-economy R1/R12) -------------- #
            # Optional; absent/None = fixed item, never rolled. A declared
            # roll_spec must be fully well-formed so bad data fails at LOAD,
            # not when the roller first fires at runtime.
            roll_spec = entry.get("roll_spec")
            if roll_spec is not None:
                errors.extend(self._validate_roll_spec(prefix, roll_spec))

            # ---- insert_effect (item-loot-economy §4.3, task 4.2) -------- #
            # ``category: insert`` items MUST declare a well-formed
            # insert_effect; every other category MUST NOT — the payload is
            # meaningless outside the Blacksmith `insert` command, so a stray
            # one is an authoring error that fails at LOAD.
            insert_effect = entry.get("insert_effect")
            if effective_category == "insert":
                if insert_effect is None:
                    errors.append(
                        f"{prefix}: 'insert' items must declare an insert_effect"
                    )
                else:
                    errors.extend(
                        self._validate_insert_effect(prefix, insert_effect)
                    )
            elif insert_effect is not None:
                errors.append(
                    f"{prefix}: insert_effect is only valid on 'insert' items, "
                    f"got category '{effective_category}'"
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

    #: Allowed top-level keys of an ItemDef.roll_spec (design §1.1).
    _ROLL_SPEC_KEYS = frozenset({"stats", "craft", "skew", "affix_pool"})

    @staticmethod
    def _validate_roll_spec(prefix: str, roll_spec) -> list:
        """Validate the shape of an ItemDef ``roll_spec`` (item-loot-economy §1.1).

        Expected shape::

            {
              "stats": {"<stat>": {"min": num, "max": num, "weight": num}},  # required
              "craft": {"<stat>": {"min": num, "max": num}},                 # optional
              "skew": num >= 1,                                              # optional
              "affix_pool": str,                                             # optional
            }

        ``stats`` is required and non-empty (a roll_spec that rolls nothing is
        authoring error); every band needs numeric ``min <= max`` and a
        positive ``weight`` (the IQS value weight). ``craft`` bands may only
        reference stats declared in ``stats`` and must be CONTAINED in the
        matching loot band (craft ⊂ loot per stat — R6.1 / design Property 4;
        review M2: the roller clamps defensively, but an out-of-band craft
        band is authoring error and fails the load). ``affix_pool`` must name
        one of the closed category pools (:data:`AFFIX_POOL_NAMES` — review
        F3: a typo like ``waepon`` used to load silently and the item simply
        never rolled affixes). Unknown keys anywhere are rejected so typos
        fail the load rather than being silently ignored.
        """
        out: list[str] = []
        if not isinstance(roll_spec, dict):
            return [
                f"{prefix}: roll_spec must be a dict, got {type(roll_spec).__name__}"
            ]

        unknown = set(roll_spec) - SchemaValidator._ROLL_SPEC_KEYS
        if unknown:
            out.append(
                f"{prefix}: roll_spec has unknown keys {sorted(unknown)} "
                f"(allowed: {sorted(SchemaValidator._ROLL_SPEC_KEYS)})"
            )

        def _check_band(where: str, band, *, require_weight: bool) -> None:
            allowed = {"min", "max", "weight"} if require_weight else {"min", "max"}
            if not isinstance(band, dict):
                out.append(
                    f"{prefix}: roll_spec.{where} must be a dict, "
                    f"got {type(band).__name__}"
                )
                return
            extra = set(band) - allowed
            if extra:
                out.append(
                    f"{prefix}: roll_spec.{where} has unknown keys {sorted(extra)} "
                    f"(allowed: {sorted(allowed)})"
                )
            lo, hi = band.get("min"), band.get("max")
            if not _is_num(lo) or not _is_num(hi):
                out.append(
                    f"{prefix}: roll_spec.{where} requires numeric 'min' and "
                    f"'max', got min={lo!r}, max={hi!r}"
                )
            elif lo > hi:
                out.append(
                    f"{prefix}: roll_spec.{where} min ({lo}) must be <= max ({hi})"
                )
            if require_weight:
                weight = band.get("weight")
                if not _is_num(weight) or weight <= 0:
                    out.append(
                        f"{prefix}: roll_spec.{where}.weight must be a number "
                        f"> 0, got {weight!r}"
                    )

        # ---- stats: required, non-empty ------------------------------- #
        stats = roll_spec.get("stats")
        if not isinstance(stats, dict) or not stats:
            out.append(
                f"{prefix}: roll_spec.stats must be a non-empty dict of "
                f"stat -> {{min, max, weight}}, got {stats!r}"
            )
            stats = {}
        else:
            for stat, band in stats.items():
                _check_band(f"stats['{stat}']", band, require_weight=True)

        # ---- craft: optional; stats must exist in the loot band, and the
        # craft band must be CONTAINED in it (M2 — craft ⊂ loot, per stat).
        # Without this, loot_roller._effective_band's "validated at load"
        # promise was a lie: {craft 26..40 vs loot 18..30} loaded clean and
        # only the roller's defensive intersection saved the roll.
        craft = roll_spec.get("craft")
        if craft is not None:
            if not isinstance(craft, dict):
                out.append(
                    f"{prefix}: roll_spec.craft must be a dict, "
                    f"got {type(craft).__name__}"
                )
            else:
                for stat, band in craft.items():
                    if stats and stat not in stats:
                        out.append(
                            f"{prefix}: roll_spec.craft['{stat}'] has no "
                            f"matching entry in roll_spec.stats"
                        )
                    before = len(out)
                    _check_band(f"craft['{stat}']", band, require_weight=False)
                    # Containment only when BOTH bands are individually valid
                    # (a malformed band already errored above).
                    loot_band = stats.get(stat) if isinstance(stats, dict) else None
                    if len(out) == before and isinstance(loot_band, dict):
                        c_lo, c_hi = band.get("min"), band.get("max")
                        l_lo, l_hi = loot_band.get("min"), loot_band.get("max")
                        if (_is_num(l_lo) and _is_num(l_hi)
                                and not (l_lo <= c_lo and c_hi <= l_hi)):
                            out.append(
                                f"{prefix}: roll_spec.craft['{stat}'] band "
                                f"[{c_lo}, {c_hi}] must be contained in the "
                                f"loot band [{l_lo}, {l_hi}] "
                                f"(craft ⊂ loot, R6.1)"
                            )

        # ---- skew: optional number >= 1 ------------------------------- #
        skew = roll_spec.get("skew")
        if skew is not None and (not _is_num(skew) or skew < 1):
            out.append(
                f"{prefix}: roll_spec.skew must be a number >= 1, got {skew!r}"
            )

        # ---- affix_pool: optional; must name a known category pool ----- #
        # The pool namespace is CLOSED (one pool per Gear category — design
        # §3.3), so validating against AFFIX_POOL_NAMES catches typos
        # (``waepon``) at load without any cross-file coupling to the
        # optional affixes.yaml (review F3).
        pool = roll_spec.get("affix_pool")
        if pool is not None and pool not in AFFIX_POOL_NAMES:
            out.append(
                f"{prefix}: roll_spec.affix_pool must name a known affix "
                f"pool {sorted(AFFIX_POOL_NAMES)}, got {pool!r}"
            )

        return out

    #: Allowed top-level keys of an ItemDef.insert_effect (design §4.3).
    _INSERT_EFFECT_KEYS = frozenset({"type", "value", "stat", "tradeoff"})

    #: Valid insert_effect ``type`` discriminators.
    _INSERT_EFFECT_TYPES = ("damage_type", "range", "stat")

    @staticmethod
    def _validate_insert_effect(prefix: str, insert_effect) -> list:
        """Validate the shape of an ItemDef ``insert_effect`` (design §4.3).

        Expected shapes (one per ``type`` discriminator)::

            {"type": "damage_type", "value": "fire"}          # typed conversion
            {"type": "range",       "value": 2}               # +range
            {"type": "stat", "stat": "damage", "value": 4,
             "tradeoff": {"range": -1}}                       # stat (+tradeoff)

        ``damage_type`` values must be a typed damage the combat engine
        dispatches on (``INSERT_DAMAGE_TYPES`` — no "physical": converting a
        weapon back to the default is not an insert). ``stat`` is required
        for — and only valid on — ``type: stat``; ``tradeoff`` (optional,
        ``type: stat`` only) maps stat names to numeric deltas.
        """
        out: list[str] = []
        if not isinstance(insert_effect, dict):
            return [
                f"{prefix}: insert_effect must be a dict, "
                f"got {type(insert_effect).__name__}"
            ]

        unknown = set(insert_effect) - SchemaValidator._INSERT_EFFECT_KEYS
        if unknown:
            out.append(
                f"{prefix}: insert_effect has unknown keys {sorted(unknown)} "
                f"(allowed: {sorted(SchemaValidator._INSERT_EFFECT_KEYS)})"
            )

        etype = insert_effect.get("type")
        value = insert_effect.get("value")
        stat = insert_effect.get("stat")
        tradeoff = insert_effect.get("tradeoff")

        if etype not in SchemaValidator._INSERT_EFFECT_TYPES:
            out.append(
                f"{prefix}: insert_effect.type must be one of "
                f"{list(SchemaValidator._INSERT_EFFECT_TYPES)}, got {etype!r}"
            )
            return out  # the remaining checks are type-dependent

        if etype == "damage_type":
            if value not in INSERT_DAMAGE_TYPES:
                out.append(
                    f"{prefix}: insert_effect.value must be one of "
                    f"{sorted(INSERT_DAMAGE_TYPES)} for type 'damage_type', "
                    f"got {value!r}"
                )
        else:  # "range" or "stat" — numeric magnitude
            if not _is_num(value):
                out.append(
                    f"{prefix}: insert_effect.value must be numeric for type "
                    f"'{etype}', got {value!r}"
                )

        if etype == "stat":
            if not isinstance(stat, str) or not stat:
                out.append(
                    f"{prefix}: insert_effect.stat must be a non-empty string "
                    f"for type 'stat', got {stat!r}"
                )
            if tradeoff is not None:
                if not isinstance(tradeoff, dict) or not tradeoff:
                    out.append(
                        f"{prefix}: insert_effect.tradeoff must be a non-empty "
                        f"dict of stat -> number, got {tradeoff!r}"
                    )
                else:
                    for t_stat, t_val in tradeoff.items():
                        if not isinstance(t_stat, str) or not t_stat:
                            out.append(
                                f"{prefix}: insert_effect.tradeoff key must be "
                                f"a non-empty string, got {t_stat!r}"
                            )
                        if not _is_num(t_val):
                            out.append(
                                f"{prefix}: insert_effect.tradeoff"
                                f"['{t_stat}'] must be numeric, got {t_val!r}"
                            )
        else:
            if stat is not None:
                out.append(
                    f"{prefix}: insert_effect.stat is only valid for type "
                    f"'stat', got type '{etype}'"
                )
            if tradeoff is not None:
                out.append(
                    f"{prefix}: insert_effect.tradeoff is only valid for type "
                    f"'stat', got type '{etype}'"
                )

        return out

    # ------------------------------------------------------------------ #
    #  Ranks
    # ------------------------------------------------------------------ #
    def validate_ranks(self, data: list[dict]) -> list[str]:
        """Validate a list of rank definition dicts."""
        errors: list[str] = []
        required = {"name", "level", "xp_threshold", "agent_cap"}
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

            # Terrain technologies (terrain-strategy, Req 7.1/7.5): the
            # effect_value payload carries structured bonus keys.
            if entry.get("effect_type") == "terrain_affinity":
                errors.extend(
                    self._validate_terrain_affinity_effect(
                        prefix, entry.get("effect_value")
                    )
                )

        return errors

    @staticmethod
    def _parse_affinity_key(key) -> "tuple[str, str] | None":
        """Split a structured terrain-affinity bonus key into (terrain, kind).

        Returns ``None`` unless *key* is a string of the exact form
        ``terrain_affinity:{terrain_type}:{kind}`` with a non-empty terrain
        type segment. The kind segment is NOT checked here — callers decide
        whether an unknown kind is an error (``validate_technologies``) or a
        skip (``cross_validate``, which only checks terrain existence).
        """
        if not isinstance(key, str):
            return None
        parts = key.split(":")
        if len(parts) != 3 or parts[0] != "terrain_affinity" or not parts[1]:
            return None
        return parts[1], parts[2]

    def _validate_terrain_affinity_effect(self, prefix: str, effect_value) -> list[str]:
        """Validate one technology's ``terrain_affinity`` effect_value payload.

        Every key must match ``terrain_affinity:{terrain_type}:{kind}`` with a
        kind of vision/movement/defense, and every value must be numeric
        (bool rejected explicitly — it is an int subclass). Terrain-type
        existence is a cross-file concern checked in :meth:`cross_validate`,
        which runs after the terrain definitions have populated (Req 7.5).
        """
        out: list[str] = []
        if not isinstance(effect_value, dict):
            out.append(
                f"{prefix}: terrain_affinity effect_value must be a dict of "
                f"'terrain_affinity:{{terrain_type}}:{{kind}}' keys, "
                f"got {effect_value!r}"
            )
            return out
        for key, val in effect_value.items():
            parsed = self._parse_affinity_key(key)
            if parsed is None:
                out.append(
                    f"{prefix}: effect_value key {key!r} must match "
                    f"'terrain_affinity:{{terrain_type}}:{{kind}}'"
                )
            elif parsed[1] not in _AFFINITY_KINDS:
                out.append(
                    f"{prefix}: effect_value key {key!r} has invalid kind "
                    f"{parsed[1]!r} (expected one of vision, movement, defense)"
                )
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                out.append(
                    f"{prefix}: effect_value[{key!r}] must be numeric, got {val!r}"
                )
        return out

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

        modifier_fields = ("vision_modifier", "movement_modifier",
                           "defense_modifier", "latitude_bias", "latitude_min")

        for prefix, entry in self._iter_dict_entries(terrain_list, "terrain", required, errors):
            ms = entry.get("map_symbol")
            if isinstance(ms, str) and len(ms) != 2:
                errors.append(f"{prefix}: map_symbol must be 2 characters, got '{ms}'")

            tt = entry.get("terrain_type")
            if isinstance(tt, str):
                terrain_types.add(tt)

            # Modifier fields, when present and non-null, must be numeric.
            # (bool is a subclass of int, so reject it explicitly.)
            for field in modifier_fields:
                val = entry.get(field)
                if val is not None and (
                    not isinstance(val, (int, float)) or isinstance(val, bool)
                ):
                    errors.append(
                        f"{prefix} ('{tt}'): {field} must be a number, got {val!r}"
                    )

            # buildable, when present, must be a bool.
            bld = entry.get("buildable")
            if bld is not None and not isinstance(bld, bool):
                errors.append(
                    f"{prefix} ('{tt}'): buildable must be true/false, got {bld!r}"
                )

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
        resource_map_fields = ["base_training_cost", "reroll_resource_cost"]
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
            "hp_regen_percent", "hp_regen_interval_ticks",
            "repair_hp_percent_per_tick",
            "attack_cooldown_seconds", "linkdead_grace_seconds",
            "chip_damage_min_fraction",
            "rank_gap_penalty_threshold", "rank_gap_full_penalty_span",
            "rank_gap_min_damage_mult", "rank_gap_xp_loot_mult",
            "travel_cooldown_ticks", "travel_cooldown_owned_ticks",
            "travel_manifest_weight_per_level",
            "travel_fuel_per_agent", "travel_fuel_per_hop",
            "baseline_resist",
            "loot_roll_skew",
            "guard_gear_drop_chance",
            "reroll_salvage_cost",
            "base_salvage", "salvage_per_iqs", "salvage_level_bonus",
            "refine_salvage_per_unit", "refine_level_bonus",
            "build_cost_mult_floor",
            "max_weapon_range",
            "fire_burn_fraction", "fire_burn_ticks",
            "poison_dot_fraction", "poison_dot_ticks",
            "blast_shred_per_hit", "blast_shred_decay_per_tick",
            "perm_bonus_cap_damage", "perm_bonus_cap_dr",
            "outgrown_grace_levels", "outgrown_min_factor",
            "terrain_vision_bound", "terrain_movement_bound",
            "terrain_defense_bound", "min_vision_radius",
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

        # rarity_table (item-loot-economy task 2.2, design §3.2): source
        # bucket -> {min_weight: number >= 0, weights: {rarity: number >= 0,
        # at least one > 0}}. Rarity names are checked against the known
        # tiers so a typo fails the load, not the runtime.
        errors.extend(self._validate_rarity_table(data.get("rarity_table")))

        # craft_rarity_table (crafted-rarity deviation from R6.1 — see
        # BalanceConfig.craft_rarity_table): building level (1-5) ->
        # {rarity: number >= 0}, tiers capped at rare.
        errors.extend(self._validate_craft_rarity_table(
            data.get("craft_rarity_table")))

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

    @staticmethod
    def _validate_rarity_table(table) -> list[str]:
        """Validate ``balance.rarity_table`` (item-loot-economy task 2.2).

        Shape: source bucket -> ``{min_weight: number >= 0, weights:
        {rarity: number >= 0}}`` with at least one positive weight per
        bucket. Rarity names must be one of the design §3.1 tiers — a typo
        here would silently mis-weight the loot economy, so it fails the
        load instead. Bucket names are free-form (buckets are selected by
        threshold, not name).
        """
        out: list[str] = []
        if table is None:
            return out
        if not isinstance(table, dict):
            return [f"balance.rarity_table: expected dict, "
                    f"got {type(table).__name__}"]

        for bucket, row in table.items():
            prefix = f"balance.rarity_table['{bucket}']"
            if not isinstance(row, dict):
                out.append(f"{prefix}: expected dict, got {type(row).__name__}")
                continue
            unknown = set(row) - {"min_weight", "weights"}
            if unknown:
                out.append(
                    f"{prefix}: unknown keys {sorted(unknown)} "
                    f"(allowed: ['min_weight', 'weights'])"
                )
            mw = row.get("min_weight")
            if not _is_num(mw) or not (mw >= 0):
                out.append(f"{prefix}.min_weight: must be a number >= 0, "
                           f"got {mw!r}")
            weights = row.get("weights")
            if not isinstance(weights, dict) or not weights:
                out.append(f"{prefix}.weights: expected a non-empty dict, "
                           f"got {weights!r}")
                continue
            any_positive = False
            for rarity, weight in weights.items():
                if str(rarity).lower() not in RARITY_TIERS:
                    out.append(
                        f"{prefix}.weights['{rarity}']: unknown rarity; "
                        f"must be one of {sorted(RARITY_TIERS)}"
                    )
                if not _is_num(weight) or not (weight >= 0):
                    out.append(
                        f"{prefix}.weights['{rarity}']: must be a number "
                        f">= 0, got {weight!r}"
                    )
                elif weight > 0:
                    any_positive = True
            if not any_positive:
                out.append(f"{prefix}.weights: needs at least one positive "
                           f"weight")
        return out

    @staticmethod
    def _validate_craft_rarity_table(table) -> list[str]:
        """Validate ``balance.craft_rarity_table`` (crafted-rarity change).

        Shape: building level (int 1-5, digit strings tolerated the way
        ``demolish_refund_rates`` keys are) -> ``{rarity: number >= 0}``
        with at least one positive weight per row. Crafted gear is capped
        at Rare (the deviation-from-R6.1 decision — see
        ``BalanceConfig.craft_rarity_table``), so rarity names above rare
        FAIL the load here: an epic weight in a craft row is authoring
        error, not a tunable (the roller would ignore it anyway).
        """
        out: list[str] = []
        if table is None:
            return out
        if not isinstance(table, dict):
            return [f"balance.craft_rarity_table: expected dict, "
                    f"got {type(table).__name__}"]

        craft_tiers = {"common", "uncommon", "rare"}
        for level, weights in table.items():
            k = int(level) if isinstance(level, str) and level.isdigit() else level
            prefix = f"balance.craft_rarity_table[{level!r}]"
            if not isinstance(k, int) or isinstance(k, bool) or k < 1 or k > 5:
                out.append(
                    f"balance.craft_rarity_table: key must be a building "
                    f"level 1-5, got {level!r}"
                )
            if not isinstance(weights, dict) or not weights:
                out.append(f"{prefix}: expected a non-empty dict of rarity "
                           f"weights, got {weights!r}")
                continue
            any_positive = False
            for rarity, weight in weights.items():
                if str(rarity).lower() not in craft_tiers:
                    out.append(
                        f"{prefix}['{rarity}']: crafted rarity is capped at "
                        f"rare; must be one of {sorted(craft_tiers)}"
                    )
                if not _is_num(weight) or not (weight >= 0):
                    out.append(
                        f"{prefix}['{rarity}']: must be a number >= 0, "
                        f"got {weight!r}"
                    )
                elif weight > 0:
                    any_positive = True
            if not any_positive:
                out.append(f"{prefix}: needs at least one positive weight")
        return out

    # ------------------------------------------------------------------ #
    #  Affixes (item-loot-economy R3)
    # ------------------------------------------------------------------ #

    #: Allowed keys of one affix entry. Exactly one of ``stat``/``proc`` must
    #: be present (checked in validate_affixes); the rest are required.
    _AFFIX_ENTRY_KEYS = frozenset({"key", "name", "stat", "proc", "min", "max", "weight"})

    def validate_affixes(self, data) -> list[str]:
        """Validate the affix registry (affixes.yaml, item-loot-economy §3.3).

        Expected shape — category-keyed pools::

            weapon:
              - {key: keen, stat: damage_bonus, min: 2, max: 6,
                 weight: 1.0, name: "of Power"}
            armor:
              - {key: sturdy, stat: damage_reduction, min: 2, max: 6,
                 weight: 1.0, name: "of the Bulwark"}

        Checks (design Error Handling — invalid data fails the LOAD):
        - top level is a dict of pool-name → list; pool names must be known
          Gear categories (:data:`AFFIX_POOL_NAMES`);
        - every entry is a dict with ``key``/``name`` non-empty strings,
          numeric ``min <= max`` with ``min > 0`` (magnitudes are additive
          bonuses; zero/negative is authoring error), and ``weight > 0``;
        - exactly one of ``stat`` / ``proc``, cross-checked against the known
          axes (:data:`AFFIX_STAT_AXES` / :data:`AFFIX_PROC_KEYS`) so an affix
          can never target a stat no system reads;
        - no duplicate ``key`` within a pool (affixes draw without
          replacement — a duplicate key breaks the no-dup contract);
        - unknown entry keys rejected so typos fail the load.
        """
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"affixes: expected a dict of pool -> list, got {type(data).__name__}"]

        for pool_name, entries in data.items():
            if pool_name not in AFFIX_POOL_NAMES:
                errors.append(
                    f"affixes: unknown pool '{pool_name}' "
                    f"(known: {sorted(AFFIX_POOL_NAMES)})"
                )
                continue
            if not isinstance(entries, list):
                errors.append(
                    f"affixes.{pool_name}: expected a list of affix entries, "
                    f"got {type(entries).__name__}"
                )
                continue

            keys_seen: set[str] = set()
            for idx, entry in enumerate(entries):
                prefix = f"affixes.{pool_name}[{idx}]"
                if not isinstance(entry, dict):
                    errors.append(
                        f"{prefix}: expected dict, got {type(entry).__name__}"
                    )
                    continue

                unknown = set(entry) - self._AFFIX_ENTRY_KEYS
                if unknown:
                    errors.append(
                        f"{prefix}: unknown keys {sorted(unknown)} "
                        f"(allowed: {sorted(self._AFFIX_ENTRY_KEYS)})"
                    )

                # ---- key: non-empty string, unique within the pool ------ #
                key = entry.get("key")
                if not isinstance(key, str) or not key:
                    errors.append(
                        f"{prefix}: key must be a non-empty string, got {key!r}"
                    )
                elif key in keys_seen:
                    errors.append(f"{prefix}: duplicate key '{key}' in pool")
                else:
                    keys_seen.add(key)

                # ---- name: non-empty string ----------------------------- #
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    errors.append(
                        f"{prefix}: name must be a non-empty string, got {name!r}"
                    )

                # ---- exactly one of stat / proc, in the known axes ------ #
                stat = entry.get("stat")
                proc = entry.get("proc")
                if (stat is None) == (proc is None):
                    errors.append(
                        f"{prefix}: exactly one of 'stat' or 'proc' is "
                        f"required, got stat={stat!r}, proc={proc!r}"
                    )
                elif stat is not None and stat not in AFFIX_STAT_AXES:
                    errors.append(
                        f"{prefix}: stat '{stat}' is not a known affix axis "
                        f"(allowed: {sorted(AFFIX_STAT_AXES)})"
                    )
                elif proc is not None and proc not in AFFIX_PROC_KEYS:
                    errors.append(
                        f"{prefix}: proc '{proc}' is not a known proc key "
                        f"(allowed: {sorted(AFFIX_PROC_KEYS)}; a proc needs "
                        f"its combat consumer before it can be authored)"
                    )

                # ---- magnitude band: numeric, 0 < min <= max ------------ #
                lo, hi = entry.get("min"), entry.get("max")
                if not _is_num(lo) or not _is_num(hi):
                    errors.append(
                        f"{prefix}: requires numeric 'min' and 'max', "
                        f"got min={lo!r}, max={hi!r}"
                    )
                elif lo <= 0:
                    errors.append(f"{prefix}: min must be > 0, got {lo!r}")
                elif lo > hi:
                    errors.append(f"{prefix}: min ({lo}) must be <= max ({hi})")

                # ---- weight: numeric > 0 -------------------------------- #
                weight = entry.get("weight")
                if not _is_num(weight) or weight <= 0:
                    errors.append(
                        f"{prefix}: weight must be a number > 0, got {weight!r}"
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

        # Terrain-technology effect keys → terrain types must exist in the
        # terrain definitions (terrain-strategy, Req 7.5). Key format and
        # value types are checked in validate_technologies; here only the
        # cross-file terrain reference is validated (terrain populates before
        # this step runs), so malformed keys are skipped rather than re-flagged.
        for key, tdef in registry.technologies.items():
            if tdef.effect_type != "terrain_affinity":
                continue
            effect_value = tdef.effect_value if isinstance(tdef.effect_value, dict) else {}
            for bonus_key in effect_value:
                parsed = self._parse_affinity_key(bonus_key)
                if parsed is not None and parsed[0] not in terrain_types:
                    errors.append(
                        f"technology '{key}': effect_value key {bonus_key!r} "
                        f"names unknown terrain type '{parsed[0]}'"
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
        # typo in any cost/ammo/tech-cost/terrain-yield would load silently
        # and only surface at runtime. Validate them here.
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
