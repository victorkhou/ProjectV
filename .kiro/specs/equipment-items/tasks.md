# Implementation Plan: Equipment, Weapons & Special Items

## Overview

Deepen the item layer into anatomical armor slots, typed weapons, counted ammunition,
usable/throwable consumables, and a **weight-based carry cap with real Vault/HQ storage** — while
touching the combat engine as little as possible (it already aggregates armor across all slots and
enforces range/ammo). Built bottom-up so nothing is orphaned: (1) data model + constants + validation
(incl. `weight` and `resource_weights`), (2) Supply_Bag storage on the handler, (3) the
`EquipmentSystem` use-case (equip/unequip/use/throw/reload + carry weight + deposit/withdraw) with rank
gating, (4) the two additive combat touches (damage_bonus, melee/magazine), (5) presenter kinds,
(6) utility-stat wiring, (7) commands + paperdoll + deposit/withdraw, (8) content + production +
weights, (9) weight/storage enforcement + delivery-FSM redirect, (10) stale-test reconciliation, and
(11) final integration. Each numbered top-level task is intended to be an independently green PR.
Property tests validate the 18 correctness properties from the design; almost all work extends
existing files.

Grouping into PRs (from the design): **PR1 = tasks 1–2** (foundation + weight config), **PR2 = task
3+4** (use-case + combat), **PR3 = task 5–6** (presenter + utility stats), **PR4 = task 7–8** (commands
+ content), **PR5 = task 9** (weight/storage enforcement + delivery FSM), **PR6 = task 10–11** (tests +
integration). The magazine/reload model is adopted (D5) and is core to PR2/PR4. Weight carry (D7),
real Vault/HQ storage (D8), and over-capacity spill (D9) are core, concentrated in PR5. The
`max_hp`-from-gear wiring is intended but deferred (D6); its one task (6.4) is marked `(deferred)` and
left unchecked for a later prioritized pass.

## Tasks

- [x] 1. Item data model, constants, and schema validation
  - [x] 1.1 Extend `ItemDef` in `world/definitions.py`
    - Add defaulted fields: `category="armor"`, `weapon_type=None`, `ammo_type=None`, `ammo_per_shot=1`, `magazine_size=None`, `effect=None`, `max_stack=99`, `weight=1.0`; make `slot` default `""`
    - Keep existing fields (`ammo_cost`, `required_rank`, `classification`) unchanged
    - _Requirements: 3.1, 4.1, 5.1, 8.1, 9.1, 10.4, 14.4, 15.1_
  - [x] 1.2 Add equipment constants to `world/constants.py`
    - `EQUIPMENT_SLOTS` (11), `GEAR_CATEGORIES`, `SUPPLY_CATEGORIES`, `ITEM_CATEGORIES`, `WEAPON_TYPES`, `AGGREGATED_STATS`, `EFFECT_TYPES`, `BASE_CARRY_WEIGHT` (1000), `DEFAULT_RESOURCE_WEIGHT`, `DEFAULT_THROW_RANGE`
    - _Requirements: 1.1, 2.4, 3.1, 4.1, 6.3, 9.3, 15.3_
  - [x] 1.3 Extend the item populator in `world/data_registry.py`
    - Read the new fields via `entry.get(...)` with the defaults in `_populate_items` (incl. `weight`)
    - _Requirements: 3.1, 4.1, 5.1, 10.4, 14.4, 15.1_
  - [x] 1.4 Add `resource_weights` to `BalanceConfig` (`world/definitions.py`) + balance plumbing
    - New `resource_weights: dict[str, float]` field with light defaults (D7); `_load_balance` read; optional `balance.yaml` override block; new resource→float rule in `validate_balance` (keys ⊆ `RESOURCE_TYPES`, values ≥ 0)
    - _Requirements: 15.2, 13.5_
  - [x] 1.5 Extend `SchemaValidator.validate_items` in `world/schema_validator.py`
    - Enforce: `category` ∈ `ITEM_CATEGORIES`; `slot` ∈ `EQUIPMENT_SLOTS` for Gear categories (not required for Supply); `weapon_type` ∈ `WEAPON_TYPES` iff weapon (rejected otherwise); `ammo_per_shot` and `magazine_size` positive int on ranged weapons; `max_stack` positive int; `weight` ≥ 0; `effect.type` ∈ `EFFECT_TYPES` for consumable/throwable; accept `max_hp`/`accuracy` as numeric stat keys with no wired effect (D6)
    - _Requirements: 3.4, 3.5, 3.6, 4.5, 5.1, 6.4, 6.5, 13.5, 15.1_
  - [x] 1.6 Extend cross-validation in `world/schema_validator.py`
    - `ammo_type` (when set) references an existing `ammo`-category item; reject `ammo_type`/`ammo_per_shot`/`magazine_size` on melee weapons; keep existing `required_rank`/production-map/`ammo_cost` FK checks
    - _Requirements: 5.7, 5.8, 7.5, 13.5_
  - [x] 1.7 Expose the new fields on `GameItem` (`typeclasses/objects.py`)
    - Combat/use/throw read `weapon_type`/`ammo_type`/`ammo_per_shot`/`magazine_size`/`effect`/`category`/`max_stack`/`weight` off a live GameItem, but GameItem exposes only ~5 named property accessors and its creation factory copies only those. Either add accessors + extend the factory field-copy for the new fields, OR route reads through the existing `item_def` property (resolve def by `item_key`). Do NOT assume generic attribute resolution exists.
    - _Requirements: 14.4_
  - [x] 1.8 Property test for item + balance schema validation
    - **Property 14: Schema fail-fast** — validator reports an error iff an item has a bad `category`, a Gear item with a slot ∉ `EQUIPMENT_SLOTS`, a weapon with `weapon_type` ∉ `{melee,ranged}`, an `ammo_type` not referencing an ammo item, `ammo_type`/`magazine_size` on a melee weapon, a negative `weight`, an `effect.type` ∉ `EFFECT_TYPES`, or a `resource_weights` key ∉ `RESOURCE_TYPES` / negative value
    - **Validates: Requirements 3.4–3.6, 4.5, 5.7–5.8, 13.5, 15.1–15.2**
    - Test file: `mygame/world/tests/test_prop_schema_validation.py`

- [x] 2. Supply_Bag storage on `EquipmentHandler`
  - [x] 2.1 Add Supply_Bag helpers to `world/systems/equipment_handler.py`
    - `get_supplies`, `get_supply`, `add_supply` (respects `max_stack`, returns amount added), `remove_supply` (False if insufficient; removes depleted), `supplies_weight(self, provider)` (Σ `item_def.weight` × count; `provider` is an explicit `DefinitionsProvider` ARGUMENT — the handler `__init__` takes only `character` and holds no provider)
    - Store via `attributes.get/add("supplies", {})` with a `_supplies` fallback dict for tests; mirror `_get_slots`/`_set_slots`
    - Preserve every existing public method unchanged in signature/behavior
    - _Requirements: 10.1, 14.3, 15.4_
  - [x] 2.2 Unit + property tests for the Supply_Bag
    - **Property 11: Supply non-negativity & stack cap** — counts stay in `[0, max_stack]`; `remove_supply` never underflows; `add_supply` never exceeds `max_stack`
    - **Property 15: API preservation** — existing `EquipmentHandler` methods (`equip`/`unequip`/`get_equipped`/`get_all_equipped`/`get_stat_total`/`get_slot_names`) unchanged in signature/behavior (Req 14.3)
    - Round-trip add/remove; depleted-key removal; `supplies_weight` = Σ weight×count
    - **Validates: Requirements 10.1, 10.4**
    - Test file: `mygame/world/systems/tests/test_equipment_handler.py`

- [x] 3. `EquipmentSystem` use-case: equip / unequip / use / throw / reload
  - [x] 3.1 Add `equip(player, item)` with rank gating in `world/systems/equipment_system.py`
    - Reject slot ∉ `EQUIPMENT_SLOTS`; resolve `required_rank` → level via registry rank table; compare to `world.utils.get_player_level`; on pass delegate to `player.equipment.equip`; `notify` success/`equip_denied`
    - _Requirements: 1.4, 7.1, 7.2, 7.4, 12.1, 12.8_
  - [x] 3.2 Add `unequip(player, slot)`
    - Validate slot ∈ `EQUIPMENT_SLOTS`; delegate to handler; `notify`
    - _Requirements: 12.2, 12.8_
  - [x] 3.3 Add `use(player, item_key)` for consumables
    - Verify held in Supply_Bag and category `consumable`; apply `effect` (`heal` → `CombatEntity.heal` clamped to `hp_max`; `buff` → apply via a `PowerupSystem` entry point — e.g. extract `PowerupSystem.apply_timed_effect(player, effect_type, value, duration_ticks)` from `activate` — so the entry uses the real `{expires_tick, effect:{effect_type, effect_value}}` shape AND the player is registered in `_active_players`; a hand-written `db.active_powerups` dict is never expired by `process_tick`); decrement supply; rank gate; `notify` `healed`/`buff_applied`
    - Add an explicit test that a stim buff EXPIRES (advance ticks past `expires_tick` → bonus gone), not just that it applies
    - _Requirements: 7.3, 8.1–8.6, 12.5, 12.12_
  - [x] 3.4 Add `throw(player, item_key, tx, ty)` for throwables
    - Verify held and category `throwable`; enforce throw range (Manhattan ≤ `effect.range`|`DEFAULT_THROW_RANGE`); resolve targets within `radius` on the player's planet via the coordinate index; apply `aoe_damage` through the injected area-damage applier; decrement supply; rank gate; `notify` `bombed`
    - _Requirements: 7.3, 9.1–9.7, 12.6, 12.8_
  - [x] 3.5 Add `reload(player)` for the equipped ranged weapon
    - Read the `weapon`-slot Game_Item; reject non-ranged (`notify reload_failed` reason `no_ammo_weapon`) or already-full (`already_loaded`); else transfer `min(magazine_size − db.loaded, bag[ammo_type])` from Supply_Bag into `db.loaded`, decrement bag by exactly that; `notify` `reloaded`/`reload_failed` (`no_ammo`)
    - _Requirements: 11.1–11.6_
  - [x] 3.6 Add `add_supply_drop(player, item_key, count)` (weight- and stack-aware pickup)
    - Add `min(count, max_stack_room, floor(weight_room / item.weight))` where `weight_room = carry_limit − carried_weight` (helpers land in task 9); spill remainder to a drop; `notify` `carry_full` on partial
    - _Requirements: 10.2, 10.3_
  - [x] 3.7 Inject the area-damage applier at the composition root
    - `equipment_system.set_area_damage_applier(lambda: combat_engine)` in `server/conf/game_init.py`; no `game_systems` reach inside the system (layering guard stays green)
    - _Requirements: 9.4, 14.1_
  - [x] 3.8 Unit tests for the use-case
    - equip accept/deny by rank; **re-equip replaces the item in an occupied slot (slot cardinality)**; unequip bad-slot rejection; use-heal clamp; use-buff entry shape + expiry; throw target selection + armor respected; reload transfer + already-full/no-ammo paths; carry partial add
    - **Property 1: Slot cardinality** (Req 1.2, 1.3), **Property 8: Reload conservation**, **Property 9: Rank gate**, **Property 10: Heal clamp**, **Property 12: Throw AoE + armor**
    - **Validates: Requirements 1.2, 1.3, 7.x, 8.x, 9.x, 10.x, 11.x**
    - Test files: `test_equipment_system.py`, `test_prop_equipment.py`

- [x] 4. Combat engine — the two additive touches (`world/systems/combat_engine.py`)
  - [x] 4.1 Add `damage_bonus` aggregation
    - In `_get_attacker_bonus`, add `attacker.equipment.get_stat_total("damage_bonus")` to the powerup bonus; update the stale "folded into weapon damage" comment; keep formula shape
    - _Requirements: 2.3, 14.1_
  - [x] 4.2 Melee gating + magazine draw in attack-queue validation
    - `weapon_type == "melee"` ⇒ effective range 1, skip all ammo; `ranged` + `ammo_type` ⇒ require `weapon.db.loaded >= ammo_per_shot`, decrement `db.loaded` by `ammo_per_shot` on a shot (never touch the Supply_Bag on a shot), apply any resource `ammo_cost` per shot; empty magazine ⇒ reject + `notify` `out_of_ammo` (prompt reload)
    - _Requirements: 4.2, 4.3, 4.4, 5.3, 5.4, 5.5, 5.6_
  - [x] 4.3 Initialize fresh ranged weapons full
    - When a ranged weapon Game_Item is created (production/pickup factory), set `db.loaded = magazine_size`
    - _Requirements: 5.2, 11.7_
  - [x] 4.4 Unit tests for combat touches
    - **Property 6: Melee range**, **Property 7: Magazine draw conservation**, **Property 3: Damage-bonus aggregation**, **Property 2: Armor aggregation invariance**
    - ranged shot decrements `db.loaded` by `ammo_per_shot`; empty magazine rejects; melee ignores range stat and never touches ammo; no regressions in existing combat tests
    - **Validates: Requirements 2.2, 2.3, 4.2–4.4, 5.3–5.6, 11.7, 14.1**
    - Test files: `test_combat_engine.py`, `test_prop_combat_engine.py`

- [x] 5. Presenter — new notification kinds (`world/presenters/notification_presenter.py`)
  - [x] 5.1 Add formatters to `_FORMATTERS` (all 11 kinds)
    - `equip_denied`, `out_of_ammo`, `reloaded`, `reload_failed`, `healed`, `buff_applied`, `bombed`, `carry_full`, `storage_full`, `deposited`, `withdrew`; reuse `attacked` for throw victims. (A `kind` with no formatter is silently dropped by `on_notification`, so this list MUST match every kind any system emits.)
    - _Requirements: 12.12_
  - [x] 5.2 End-to-end presenter tests
    - **Property 13: Presenter ownership** — assert no player-facing string is composed in `world/systems/` (grep-style + behavioral); each new kind (all 11) renders through an attached presenter to `player.msg`
    - **Validates: Requirements 12.12**
    - Test file: `mygame/world/presenters/tests/test_notification_presenter.py` + e2e in system tests

- [ ] 6. Utility-stat wiring
  - [x] 6.1 `move_speed` for players
    - Apply `get_stat_total("move_speed")` to player movement via the same equipment-derived mechanism agents use (`npcs.py:182` analog)
    - _Requirements: 6.1_
  - [x] 6.2 `sight_range` into fog-of-war
    - Add `+ player.equipment.get_stat_total("sight_range")` to the vision radius in `world/coordinate/fog_of_war.py`
    - _Requirements: 6.2_
  - [x] 6.3 `carry_capacity` gear stat raises the weight limit
    - Consumed by `carry_limit` (task 9); add a focused test that a `carry_capacity` gear piece raises the cap
    - _Requirements: 6.3, 15.5_
  - [ ] 6.4 (deferred, D6) `max_hp` from gear — NOT in this feature
    - Reserved follow-up: fold `get_stat_total("max_hp")` into `hp_max` on `CombatEntity` and clamp current HP on unequip. Left unchecked/unimplemented until prioritized; for this feature `max_hp` is validated as a numeric stat with no HP effect (task 1.4)
    - _Requirements: 6.4_

- [x] 7. Player-facing commands (`commands/game_commands.py`)
  - [x] 7.1 Re-route `equip`/`unequip` through `EquipmentSystem`
    - Use `require_system("equipment_system")`; preserve message shape
    - _Requirements: 12.1, 12.2_
  - [x] 7.2 Paperdoll `equipment` command
    - Iterate all 11 `EQUIPMENT_SLOTS` incl. empties; per-slot item + stats; totals for armor/damage/move/sight; show the equipped ranged weapon's `loaded`/`magazine_size`
    - _Requirements: 12.3, 12.9_
  - [x] 7.3 `inventory`/`score` Supplies + carried-weight section
    - Add resources + gear + Supply_Bag counts; show carried weight vs carry limit (`carried_weight`/`carry_limit` from task 9)
    - _Requirements: 12.4, 12.9, 16.9_
  - [x] 7.4 New `use`, `throw`, and `reload` commands
    - Thin `GameCommand`s resolving items via `registry.resolve_item`; `throw` parses `<target>|<x> <y>` and uses `require_coords`; `reload` acts on the equipped ranged weapon
    - _Requirements: 12.5, 12.6, 12.7_
  - [x] 7.5 New `deposit`/`withdraw` commands
    - Thin `GameCommand`s that locate a co-located Storage_Building via `buildings_here` + `building_has_capability("storage")` and call `EquipmentSystem.deposit`/`withdraw`; show `stored/capacity` and carried weight
    - _Requirements: 12.8, 16.2, 16.3, 16.9_
  - [x] 7.6 `score` includes aggregated equipment totals
    - _Requirements: 12.10_
  - [x] 7.7 Command unit tests
    - equip/unequip/use/throw/reload/deposit/withdraw happy + failure paths; paperdoll renders empties + ammo count; inventory shows supplies + carried weight
    - _Requirements: 12.1–12.12_

- [x] 8. Content and production
  - [x] 8.1 Migrate existing items in `data/definitions/items.yaml`
    - `scope` → slot `eyes`; `jetpack` → slot `back`; **`kevlar_vest`/`power_armor` → slot `torso`** (they ship `slot: armor`, now a category not a slot — validator would reject); add `category` to all 8; add `weapon_type` to the 4 weapons; retire ad-hoc `gadget`/`consumable`/`armor` slot strings; give HQ a non-zero `storage_capacity` in `buildings.yaml` (Req 16.2)
    - _Requirements: 13.2_
  - [x] 8.2 Seed the starter set + weights
    - Body armor across slots; ≥1 melee + ≥1 ranged weapon; ammo (`rifle_rounds`, `energy_cell`); consumables (`medkit`, `combat_stim`); throwable (`frag_grenade`); a `carry_capacity` hauler pack (`back`). Assign a `weight` to every item; set `resource_weights` in `balance.yaml`
    - _Requirements: 13.1, 15.1, 15.2_
  - [x] 8.3 Extend the production map
    - `AR → weapons+ammo`, `AA → armor`, `MB → consumables`, `LB → futuristic gear+throwables`
    - _Requirements: 13.3_
  - [x] 8.4 Route production by category in `EquipmentSystem.process_production`
    - Supply-category produce → `add_supply` (Supply_Bag); Gear-category → `GameItem` object (slot); no crossover
    - **Property 5: Category→storage** — gear ⇒ slots, supply ⇒ bag; no crossover (Req 3.2, 3.3)
    - _Requirements: 3.2, 3.3, 13.4_
  - [x] 8.5 Content-load + reload tests
    - Migrated + seeded content loads clean; `@reloaddata` swaps atomically
    - _Requirements: 13.5, 13.6_

- [x] 9. Weight carry cap, Vault/HQ storage, and inflow choke point (D7/D8/D9)
  - [x] 9.1 `carried_weight` / `carry_limit` on `EquipmentSystem`
    - `carried_weight(player)` = `handler.supplies_weight(provider)` + Σ(`resource_weights[type]` × `db.resources[type]`), equipped Gear excluded; `carry_limit(player)` = `∞` if `is_admin(player)` else `BASE_CARRY_WEIGHT + get_stat_total("carry_capacity")`
    - _Requirements: 15.4, 15.5, 15.6_
  - [x] 9.2 `add_resource_capped(holder, resource, amount)` inflow choke point
    - Add `min(amount, room)` where room = carry-weight room (player) or `storage_capacity` room (building); spawn remainder via `ResourceSystem._spawn_resource_drop` at holder coords; `notify` `carry_full`/`storage_full`; admins bypass the cap. Computed on-demand at the call, never per tick
    - **Property 16: Carry-weight bound**, **Property 17: Weight conservation on over-capacity**
    - _Requirements: 15.7, 16.7, 16.8_
  - [x] 9.3 Route the holder-pool inflow paths through the choke point
    - Only paths that write a *holder pool*: drop pickup (`get`/`at_get` → player Spend_Pool/Supply_Bag), harvester delivery deposit (→ Storage_Building), admin `@resource give` (admins bypass). Extractor output and presence-harvest already spawn ground drops (`resource_system.py`), NOT a holder pool — leave them as-is; their cap bites at pickup. Do NOT add per-tick weight computation to the production path
    - _Requirements: 16.7_
  - [x] 9.4 Real Storage_Building pool
    - `db.stored_resources: dict[str,int]` on `storage`-capability buildings, bounded by `storage_capacity` (now enforced, was cosmetic); helpers to read/deposit/withdraw
    - _Requirements: 16.1_
  - [x] 9.5 `deposit`/`withdraw` use-case methods
    - `deposit(player, resource, amount)` Spend_Pool → building (≤ capacity); `withdraw(player, resource, amount)` building → Spend_Pool (≤ carry-weight room, leftover stays); `notify` `deposited`/`withdrew`
    - **Property 18: Deposit/withdraw conservation & split**
    - _Requirements: 16.2, 16.3, 16.4, 16.8_
  - [x] 9.6 Redirect harvester delivery deposit to the Storage_Building
    - `DeliveryBehavior` deposit step (`typeclasses/agent_scripts.py` `deposit_resources`, reads `npc.db.owner`/`carried_resources`) → `add_resource_capped(building, …)` instead of `owner.add_resource`; `select_delivery_target` (~`:737`) skips storage buildings with 0 remaining capacity so a full building never spills a whole load; transient per-trip `carried_resources` + `DEFAULT_CARRY_CAPACITY` laden/empty movement unchanged
    - _Requirements: 16.6_
  - [x] 9.7 Weight/storage tests
    - player capped, admin unlimited; equipped gear excluded from carried weight; over-cap drop-pickup and over-cap delivery spawn a drop and conserve (added + dropped == in); deposit/withdraw conserve totals and never exceed carry weight; HQ (non-zero capacity) usable from level 1; `db.resources` still satisfies every cost check (build/upgrade/research/ammo)
    - **Validates: Requirements 15.4–15.7, 16.1–16.10**
    - Test files: `test_equipment_system.py`, `test_prop_equipment.py`, `test_resource_system.py`

- [x] 10. Stale-test reconciliation
  - [x] 10.1 Update fixtures assuming the old slot strings / `ItemDef` shape / count-based carry
    - Adjust any test constructing items with `gadget`/`consumable` slots, the old 7-field `ItemDef`, or `BASE_CARRY_CAPACITY`
    - _Requirements: 14.3, 14.4_
  - [x] 10.2 Confirm zero-equipment identity + spend-pool preservation
    - **Property 4: Zero-equipment identity** — unequipped, supply-less character behaves as today; existing cost checks read `db.resources` unchanged
    - **Validates: Requirements 2.5, 14.2, 14.6**

- [x] 11. Final integration
  - [x] 11.1 Wire `EquipmentSystem` collaborators + any new events in `server/conf/game_init.py`
    - Area-damage applier injection; `ResourceSystem`↔`EquipmentSystem` inflow-choke wiring (injected, not `game_systems` reach); ensure `test_game_init_names.py` guard passes
    - _Requirements: 9.4, 14.1, 16.6_
  - [x] 11.2 Full-suite green + layering guard
    - `python -m pytest mygame/` all pass; `test_layering_invariant.py` green (no new evennia import in systems)
    - _Requirements: 14.5_
  - [x] 11.3 Update docs
    - `ARCHITECTURE.md` (item/equipment model, weight+storage, new notification kinds), `COMPLEXITY_REVIEW.md` (touchpoint scorecard rows for "add an equipment slot / item category" 🟢 data-only, "add a resource weight" 🟢 balance.yaml, and "add an EFFECT type" 🟡 — tuple + validator + use/throw branch + presenter kind, NOT data-only), root `README.md` (commands incl. deposit/withdraw/reload/use/throw + gameplay incl. carry weight + Vault storage)
    - _Requirements: 14.5_
