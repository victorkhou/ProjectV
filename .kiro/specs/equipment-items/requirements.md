# Requirements Document

## Introduction

This feature deepens the existing item system into a full **equipment, weapons, and special-items** layer: anatomical armor slots, typed weapons (melee/ranged), counted ammunition, usable/throwable consumables (medkits, stims, bombs), and a **weight-based carry-capacity** limit on everything a player holds (with real Vault/HQ overflow storage so surplus has somewhere to go; the cap is written holder-generically but binds only on players in this feature — see D7). It builds directly on the seam that already exists — a single `GameItem` typeclass differentiated by validated `ItemDef` data (`world/definitions.py`), an `EquipmentHandler` storage mechanism on `CombatEntity` (`world/systems/equipment_handler.py`), an `EquipmentSystem` production service (`world/systems/equipment_system.py`), and a `CombatEngine` that already reads weapon `damage`/`range`/`ammo_cost` and target `damage_reduction` (`world/systems/combat_engine.py`).

The central architectural observation shaping this feature: **the combat engine is already built for multi-slot equipment.** Target armor is computed as `target.equipment.get_stat_total("damage_reduction")` — a sum across *every* equipped item, not a single "armor" slot (`combat_engine.py` `_get_target_armor_reduction`, the `return` ~`:520`). Going from today's ad-hoc slot strings (`weapon`/`armor`/`gadget`/`consumable`) to a canonical eleven-slot body model therefore requires **zero** changes to the damage formula; the pieces simply aggregate. Weapon `range` is already enforced by Manhattan distance, and ammo is already deducted on the attack-queue step. This feature spends its effort on the parts that are genuinely new — a canonical slot vocabulary, weapon typing, counted ammunition, use/throw actions, and rank-gated equipping — rather than on re-plumbing combat.

Two storage kinds are introduced, each mirroring a pattern already present in the codebase:

- **Gear** — unique `GameItem` objects that occupy exactly one body slot each, stored in the existing `db.equipment_slots` dict managed by `EquipmentHandler` (one item per slot, auto-replace on re-equip).
- **Supplies** — fungible, counted stacks (ammunition, medkits, stims, bombs) stored in a new `db.supplies: dict[item_key, count]` bag, mirroring how `db.resources` already stores counted resources. This avoids spawning thousands of individual ammo objects and reuses a counting model players already understand.

All mutating actions (equip, unequip, use, throw, reload) are routed through the `EquipmentSystem` use-case rather than the command→handler shortcut that exists today, so rank-gating, ammo/carry checks, and player-facing notifications live in one framework-free place consistent with the ports/adapters/presenter architecture. The `EquipmentHandler` remains a pure per-entity storage mechanism. All new player-facing strings are emitted as structured `PLAYER_NOTIFICATION` events and formatted by the `NotificationPresenter` (`world/presenters/notification_presenter.py`) — domain systems compose no text.

### Alignment with existing systems

- Item typeclass: `mygame/typeclasses/objects.py` (`GameItem`) — reads all metadata from Evennia attributes resolved lazily against `ItemDef`; carries `coord_x/coord_y` for drops.
- Item definitions: `mygame/world/definitions.py` (`ItemDef`), loaded by `DataRegistry` (`world/data_registry.py`), validated by `SchemaValidator` (`world/schema_validator.py`), authored in `data/definitions/items.yaml`.
- Equipment storage: `mygame/world/systems/equipment_handler.py` (`EquipmentHandler`), a lazy `equipment` property on `mygame/typeclasses/combat_entity.py` (`CombatEntity`), shared by players (`CombatCharacter`) and agents (`NPC`).
- Equipment production: `mygame/world/systems/equipment_system.py` (`EquipmentSystem`), driven per tick by Armory (`AA`) and Armorer (`AR`) buildings via `registry.get_items_for_building`.
- Combat: `mygame/world/systems/combat_engine.py` — damage `= max(0, weapon.damage + bonuses − Σ damage_reduction)`; range via Manhattan distance; ammo deducted on queue; synthetic weapon pattern (`_TurretWeapon`) already used for non-equipped attackers.
- Commands: `mygame/commands/game_commands.py` (`equip`, `unequip`, `equipment`, `inventory`, `get`), thin `GameCommand` controllers with `require_system`/`require_coords`/`buildings_here` helpers.
- Notifications: `mygame/world/presenters/notification_presenter.py` (`NotificationPresenter`) subscribing to `PLAYER_NOTIFICATION`; systems call `BaseSystem.notify(player, kind, **data)`.
- Tuning: structural constants in `world/constants.py`; hot-tunable numbers in `data/config/balance.yaml` via `BalanceConfig`.

### Resolved design decisions (confirmed with stakeholder)

These decisions were confirmed and drive this draft; they can be revisited during review:

- **D1 — Balanced body slot set (confirmed).** Eleven slots: nine armor-bearing body slots (`head`, `eyes`, `face`, `torso`, `arms`, `hands`, `legs`, `feet`, `back`), one `weapon` slot, and one `accessory` slot. Every slot can contribute `damage_reduction` and other aggregated stats. This gives a broad stat surface without the fiddliness of ring/offhand/neck micro-slots, and remains extensible (adding a slot is a constant + data change).
- **D2 — Ammunition as counted items (confirmed).** Ranged ballistic weapons consume counted ammo from `db.supplies` (e.g. `rifle_rounds`, `frag_grenade`), not solely from the resource pool. Energy weapons may still draw from the `Energy` resource via the existing `ammo_cost` field; both checks coexist. Ammo is produced by buildings and picked up as drops.
- **D3 — Include basic use/throw in this feature (confirmed).** `use <item>` (consumables: medkit heals HP, stim applies a temporary buff) and `throw <item> [target|x y]` (throwables: bombs deal area damage at coordinates) ship in this feature, not a follow-up. Area damage routes through the existing combat damage pipeline via a synthetic weapon.
- **D4 — Enforce `required_rank` on equip (confirmed).** The `ItemDef.required_rank` field already exists and is already cross-validated against real ranks at load, but is not checked when equipping. This feature enforces it: a player may not equip an item whose `required_rank` exceeds the player's current rank/level, and is told the requirement. Rank gating also applies to `use`/`throw` where a supply declares a `required_rank`.
- **D5 — Magazine / reload model adopted (resolved from Open Decision O1).** Ranged weapons hold a loaded magazine and are reloaded from the Supply_Bag: a `weapon` Game_Item tracks its loaded rounds in `db.loaded` up to `Item_Def.magazine_size`; each ranged shot draws `ammo_per_shot` from `db.loaded` (not directly from the bag); a `reload` action transfers up to `magazine_size` of the weapon's Ammo_Type from the Supply_Bag into `db.loaded`. Counted ammo (D2) is therefore the reserve that reload draws from. Requirement 5 and Requirement 11 are written unconditionally against this model.
- **D6 — `max_hp` from equipment: deferred at spec time, since delivered (Open Decision O2).** Equipped gear *may* raise a character's maximum HP via a `max_hp` stat. This was originally low-priority and out of scope for the initial delivery, with `max_hp` a declared, load-validated numeric stat with no HP effect. **Update (task 6.4, delivered):** the follow-up is now wired — `CombatEntity.refresh_equipment_hp_max()` folds `Σ max_hp(gear)` into `hp_max` on equip/unequip; equipping raises the ceiling with no free heal, unequipping lowers it and clamps current HP down. Criterion 6.4 below is superseded by this delivery (see design "Resolved decisions & deferred follow-up").
- **D7 — Weight-based carry capacity replaces the count-based cap (confirmed).** Every item and every resource has a **weight**, and every **player** has a maximum total carry weight ("a lot, but not infinite"). This **replaces** the count-based `Carry_Capacity` from the earlier draft (Requirement 10). What counts toward carried weight: unequipped Supplies (Σ `Item_Def.weight` × count) plus on-person resources (Σ per-resource weight × amount). **Equipped Gear does NOT count** — worn gear is a slot commitment, not a hauling penalty (so heavy armor never shrinks your hauling room). Per-item weight is a new `Item_Def.weight` field; per-resource weights are a hot-tunable `BalanceConfig.resource_weights` map (resources are deliberately light); the base cap is a structural `BASE_CARRY_WEIGHT` constant; the `carry_capacity` Aggregated_Stat is retained but now RAISES the weight limit (e.g. a hauler pack). **Admins (Builder+) are exempt.** **Scope note on Agents:** the cap logically applies to any resource/supply *holder*, but in the current code Agents hold no `db.resources` and no path populates an Agent's `db.supplies` (a harvester carries a *transient* per-trip `carried_resources` count governed by `DEFAULT_CARRY_CAPACITY`, a separate delivery-load budget — not the weight cap). So the weight cap **binds only on players today**; it is written to apply to any holder pool so it extends to Agents for free if Agents ever gain a persistent resource/supply pool, but no Agent resource plumbing is built here. All weights are explicitly post-playtest tuning targets.
- **D8 — Real Vault/HQ resource storage added (confirmed; expands scope).** Today Vault (`VT`) and HQ buildings do not actually store resources — a delivery deposit writes straight to `player.db.resources` and the building's `storage_capacity` is cosmetic (unenforced). Because D7 caps what a player can carry, surplus needs somewhere to go, so this feature makes **storage buildings real**: `db.resources` on the character remains the carry-weight-capped **spend pool** (unchanged for all cost checks — build, upgrade, research, ammo), and storage buildings gain a per-building stored-resource pool with an enforced `storage_capacity`. Players `deposit`/`withdraw` between their person and a storage building on the same tile, and harvester delivery now fills the building's store rather than the player's person. Over-capacity inflow spills to the ground (see D9).
- **D9 — Over-capacity inflow spills to the ground (confirmed).** When an inflow into a *holder pool* — a player picking up a drop (into their Spend_Pool/Supply_Bag) or a harvester delivering into a Storage_Building's pool — would exceed the holder's limit (carry weight for a player, `storage_capacity` for a building), the system adds only up to the limit and spawns the un-carryable remainder as a `ResourceDrop` at the holder's coordinates (reusing `ResourceSystem._spawn_resource_drop`), then notifies the player. Resources are never silently destroyed; the cap never becomes meaningless. **Note:** Extractor output and presence-harvest already spawn ground `ResourceDrop`s in the current code rather than writing to a holder pool, so for those paths the carry cap is enforced at *pickup* (a player action), not at production — there is no per-tick weight computation on the production path. See the scalability note in the design.

## Glossary

- **Game_Item**: The `GameItem` typeclass (`typeclasses/objects.py`), one Evennia object per unique gear item, reading `slot`, `stat_modifiers`, `ammo_cost`, `category`, `weapon_type`, `required_rank`, etc. from attributes resolved against its `Item_Def`.
- **Item_Def**: The `ItemDef` dataclass (`world/definitions.py`), the validated static definition of an item keyed by `key`, loaded into `Data_Registry.items`.
- **Item_Category**: A required classification of an item, one of `armor`, `weapon`, `accessory`, `ammo`, `consumable`, `throwable`. Determines storage kind (Gear vs Supply) and which actions apply.
- **Equipment_Slot**: One of the eleven canonical body/equipment slots (`head`, `eyes`, `face`, `torso`, `arms`, `hands`, `legs`, `feet`, `back`, `weapon`, `accessory`), defined once in `world/constants.py` as `EQUIPMENT_SLOTS`. Each slot holds at most one Game_Item.
- **Gear**: An item of category `armor`, `weapon`, or `accessory` — a unique Game_Item that occupies exactly one Equipment_Slot, stored in `db.equipment_slots`.
- **Supply**: An item of category `ammo`, `consumable`, or `throwable` — a fungible, counted item stored as a count in the Supply_Bag; not equipped to a slot.
- **Supply_Bag**: The per-entity attribute `db.supplies: dict[item_key, int]` holding counts of Supply items, mirroring `db.resources`.
- **Weapon_Type**: For a `weapon`-category item, one of `melee` or `ranged`, stored in `Item_Def.weapon_type`. A melee weapon has effective attack range 1 and never consumes ammo; a ranged weapon may consume ammo.
- **Ammo_Type**: For a ranged weapon, the `item_key` of the Supply the weapon consumes, stored in `Item_Def.ammo_type`. Ammo is loaded into the weapon's Magazine via reload; each shot draws `Item_Def.ammo_per_shot` from the Magazine, not from the Supply_Bag directly.
- **Magazine**: The loaded-rounds state of a ranged weapon Game_Item, stored in `db.loaded` (0..`Item_Def.magazine_size`). Shots draw from the Magazine; the `reload` action refills it from the Supply_Bag's Ammo_Type.
- **Resource_Ammo_Cost**: The existing `Item_Def.ammo_cost: dict[str, int] | None` — a per-use cost drawn from the entity's `db.resources` pool (e.g. `{Energy: 2}`), used by energy weapons. Coexists with Ammo_Type.
- **Item_Effect**: For a `consumable` or `throwable`, the `Item_Def.effect: dict` describing what happens on use/throw, e.g. `{"type": "heal", "amount": 30}`, `{"type": "buff", "stat": "damage_bonus", "amount": 10, "duration_ticks": 30}`, `{"type": "aoe_damage", "amount": 40, "radius": 2}`.
- **Equipment_Handler**: `EquipmentHandler` (`world/systems/equipment_handler.py`), the per-entity storage mechanism for equipped Gear; provides `equip`/`unequip`/`get_equipped`/`get_all_equipped`/`get_stat_total`/`get_slot_names`.
- **Equipment_System**: `EquipmentSystem` (`world/systems/equipment_system.py`), the framework-free use-case that mediates equip/unequip/use/throw (enforcing gating, ammo, and carry limits, emitting notifications) and drives per-tick item production.
- **Combat_Engine**: `CombatEngine` (`world/systems/combat_engine.py`), which resolves attacks, computes damage, enforces range, and consumes ammo.
- **Aggregated_Stat**: A numeric stat summed across all equipped Gear via `Equipment_Handler.get_stat_total(name)`. The combat-relevant aggregated stats are `damage_reduction` and `damage_bonus`; utility aggregated stats include `move_speed`, `sight_range`, and `carry_capacity` (which adds to the weight limit, not a count).
- **Item_Weight**: The per-item `Item_Def.weight` (a float ≥ 0, default 1.0) — how much one unit of the item contributes to carried weight.
- **Resource_Weight**: The per-resource weight from `BalanceConfig.resource_weights` (a resource→float map, values ≥ 0), deliberately light (≤ ~2.0). Hot-tunable in `balance.yaml`.
- **Carry_Capacity**: The maximum total *weight* a holder may carry, equal to `BASE_CARRY_WEIGHT` (structural constant) plus the holder's Aggregated_Stat `carry_capacity` (weight units) from equipped Gear. **Carried weight** = Σ(`Item_Weight` × count) over Supplies + Σ(`Resource_Weight` × amount) over on-person resources; **equipped Gear weight is excluded**. Admins (Builder+) are exempt from the cap.
- **Spend_Pool**: The character's `db.resources` — the resources on the person, used for all cost checks (build/upgrade/research/ammo). This pool counts toward Carry_Capacity. Distinct from a Storage_Building's stored pool.
- **Storage_Building**: A building with the `storage` capability (Vault `VT`, HQ) holding a per-building stored-resource pool up to its `storage_capacity`, separate from any player's Spend_Pool. Players `deposit`/`withdraw` between their Spend_Pool and a co-located Storage_Building; harvester delivery fills it.
- **Notification_Presenter**: `NotificationPresenter` (`world/presenters/notification_presenter.py`), the single owner of player-facing message strings, subscribing to `PLAYER_NOTIFICATION`.
- **Data_Registry**: `DataRegistry` (`world/data_registry.py`), which loads and exposes validated `Item_Def` data and the item production map.
- **Schema_Validator**: `SchemaValidator` (`world/schema_validator.py`), which validates `items.yaml` at load time, including the new slot/category/weapon-type/effect rules.
- **Player**: A `CombatCharacter` controlled by a human account.
- **Item_Production**: The per-tick creation of items by Armory (`AA`) and Armorer (`AR`) buildings, extended here to also cover Medbay (`MB`) and Lab (`LB`) for consumables/ammo/futuristic gear.

## Requirements

### Requirement 1: Canonical equipment-slot vocabulary

**User Story:** As a player, I want a logical set of body equipment slots, so that I can outfit my character head-to-toe and each piece matters.

#### Acceptance Criteria

1. THE Equipment_System SHALL define exactly eleven canonical Equipment_Slots in `world/constants.py` as `EQUIPMENT_SLOTS`: `head`, `eyes`, `face`, `torso`, `arms`, `hands`, `legs`, `feet`, `back`, `weapon`, `accessory`.
2. THE Equipment_Handler SHALL store at most one Game_Item per Equipment_Slot.
3. WHEN a player equips a Game_Item whose slot already holds another Game_Item, THE Equipment_Handler SHALL unequip the previously equipped item and equip the new one.
4. THE Equipment_System SHALL treat a Game_Item's slot as authoritative from its `Item_Def.slot`, and SHALL reject equipping an item whose slot is not in `EQUIPMENT_SLOTS`.
5. THE Equipment_System SHALL support both players and agents (any `Combat_Entity`) using the same slot vocabulary and handler.

### Requirement 2: Per-slot stat and armor contribution

**User Story:** As a player, I want every equipped piece to affect my stats and armor, so that outfitting choices are meaningful.

#### Acceptance Criteria

1. THE Equipment_Handler SHALL compute an Aggregated_Stat for a given stat name as the sum of that stat's value across all currently equipped Game_Items.
2. THE Combat_Engine SHALL compute a target's total damage reduction as the Aggregated_Stat `damage_reduction` across all the target's equipped Game_Items, unchanged from current behavior.
3. THE Combat_Engine SHALL add the attacker's Aggregated_Stat `damage_bonus` to base weapon damage when computing attack damage.
4. THE Equipment_System SHALL recognize the following aggregated stat keys as valid on equipped Gear: `damage_reduction`, `damage_bonus`, `move_speed`, `sight_range`, `carry_capacity` (raises the weight limit — see Requirement 15), and (reserved, see Requirement 6) `max_hp`, `accuracy`.
5. WHEN no Game_Item is equipped in any slot, THE Equipment_Handler SHALL report every Aggregated_Stat as zero.

### Requirement 3: Item categories and storage kind

**User Story:** As a developer, I want each item classified so the system knows how to store and use it, so that gear, ammo, and consumables behave correctly.

#### Acceptance Criteria

1. THE Item_Def SHALL carry a required `category` field, one of: `armor`, `weapon`, `accessory`, `ammo`, `consumable`, `throwable`.
2. THE Equipment_System SHALL treat items of category `armor`, `weapon`, or `accessory` as Gear stored in `db.equipment_slots`.
3. THE Equipment_System SHALL treat items of category `ammo`, `consumable`, or `throwable` as Supplies stored as counts in the Supply_Bag `db.supplies`.
4. THE Schema_Validator SHALL reject at load time any `Item_Def` whose `category` is not one of the six defined categories.
5. WHEN an item's `category` is a Gear category, THE Schema_Validator SHALL require its `slot` to be a member of `EQUIPMENT_SLOTS`.
6. WHEN an item's `category` is a Supply category, THE Schema_Validator SHALL NOT require a slot.

### Requirement 4: Weapons — melee and ranged

**User Story:** As a player, I want distinct melee and ranged weapons, so that positioning and range matter in combat.

#### Acceptance Criteria

1. THE Item_Def SHALL carry a `weapon_type` field for `weapon`-category items, one of `melee` or `ranged`.
2. THE Combat_Engine SHALL enforce a melee weapon's effective attack range as 1, regardless of any `range` stat on the item.
3. THE Combat_Engine SHALL enforce a ranged weapon's attack range as the weapon's `range` stat via Manhattan distance, unchanged from current behavior.
4. THE Combat_Engine SHALL never consume ammo for a melee weapon.
5. THE Schema_Validator SHALL require `weapon_type` to be `melee` or `ranged` for every `weapon`-category item, and SHALL reject `weapon_type` on non-weapon items.
6. THE Combat_Engine SHALL determine base attack damage from the equipped `weapon`-slot item's `damage` stat, unchanged from current behavior.

### Requirement 5: Counted ammunition and the loaded magazine

**User Story:** As a player, I want ranged weapons to fire from a loaded magazine drawn from ammunition I carry and can run out of, so that supply management and reloading are part of combat.

#### Acceptance Criteria

1. THE Item_Def SHALL carry, for ranged weapons, an `ammo_type` (an Ammo_Type item key), `ammo_per_shot` (a positive integer, default 1), and `magazine_size` (a positive integer).
2. THE weapon Game_Item SHALL track its currently loaded rounds in `db.loaded`, an integer between 0 and `magazine_size` inclusive.
3. WHEN a player attacks with a ranged weapon that declares an `ammo_type`, THE Combat_Engine SHALL verify `db.loaded` is at least `ammo_per_shot` before resolving the attack.
4. WHEN `db.loaded` is below `ammo_per_shot`, THE Combat_Engine SHALL reject the attack and notify the player the weapon is empty and must be reloaded.
5. WHEN a ranged-weapon attack proceeds, THE Combat_Engine SHALL deduct `ammo_per_shot` from the weapon's `db.loaded` at the same point ammo is deducted today (attack-queue step), and SHALL NOT draw from the Supply_Bag on a shot (the Supply_Bag is drawn only on reload, per Requirement 11).
6. THE Combat_Engine SHALL continue to support the existing Resource_Ammo_Cost (`ammo_cost` drawn from `db.resources`) for weapons that declare it, applying it per shot in addition to the `db.loaded` deduction when a weapon declares both.
7. THE Schema_Validator SHALL reject at load time any weapon whose `ammo_type` does not reference an existing `ammo`-category Item_Def.
8. THE Schema_Validator SHALL reject `ammo_type`, `ammo_per_shot`, or `magazine_size` on melee weapons.

### Requirement 6: Utility stats — wired now vs. reserved

**User Story:** As a player, I want gear to improve movement, vision, and carrying, so that non-combat gear is worthwhile; and as a developer, I want a stat vocabulary I can extend by data alone.

#### Acceptance Criteria

1. THE Equipment_System SHALL apply the Aggregated_Stat `move_speed` from equipped Gear to a player's movement, using the same equipment-derived movement mechanism agents already use.
2. THE Fog_Of_War vision computation SHALL add the player's Aggregated_Stat `sight_range` from equipped Gear to the player's vision radius.
3. THE Equipment_System SHALL compute an entity's Carry_Capacity as `BASE_CARRY_WEIGHT` (weight units) plus the Aggregated_Stat `carry_capacity` (weight units) from equipped Gear (see Requirement 15 for the full weight model).
4. THE Schema_Validator SHALL accept `max_hp` and `accuracy` as valid numeric stat keys on Gear. **Update (task 6.4, delivered):** `max_hp` from equipped Gear now raises the wearer's `hp_max` (equipping adds headroom without healing; unequipping lowers the ceiling and clamps current HP down). `accuracy` remains reserved with no combat effect (see decision D6).
5. THE Schema_Validator SHALL validate every value in `stat_modifiers` as an integer or float.

### Requirement 7: Rank-gated equipping and use

**User Story:** As a player, I want powerful gear to require rank, so that progression unlocks capability.

#### Acceptance Criteria

1. WHEN a player attempts to equip a Game_Item whose `Item_Def.required_rank` is set, THE Equipment_System SHALL permit the equip only if the player's current rank is at least the item's `required_rank`.
2. WHEN a player attempts to equip an item above their rank, THE Equipment_System SHALL reject the equip and notify the player of the required rank and their current rank.
3. WHEN a player attempts to use or throw a Supply whose `Item_Def.required_rank` is set and exceeds the player's rank, THE Equipment_System SHALL reject the action and notify the player of the requirement.
4. THE Equipment_System SHALL resolve an item's `required_rank` (a rank name) to a rank threshold using the same rank data players are ranked against, and SHALL compare against the player's current rank/level.
5. THE Schema_Validator SHALL continue to reject at load time any item whose `required_rank` is not an existing rank name (unchanged from current behavior).

### Requirement 8: Consumables — the `use` action

**User Story:** As a player, I want to use medkits and stims, so that I can heal and gain temporary advantages.

#### Acceptance Criteria

1. THE Equipment_System SHALL provide a `use` action that consumes one unit of a `consumable`-category Supply from the player's Supply_Bag and applies its Item_Effect.
2. WHEN a consumable's Item_Effect type is `heal`, THE Equipment_System SHALL restore the effect's `amount` of HP to the player, not exceeding the player's maximum HP.
3. WHEN a consumable's Item_Effect type is `buff`, THE Equipment_System SHALL apply a temporary modifier to the named stat for the effect's `duration_ticks`, using the existing timed-buff (powerup) mechanism.
4. WHEN a player attempts to use a consumable they do not hold in their Supply_Bag, THE Equipment_System SHALL reject the action and notify the player.
5. WHEN a `use` action succeeds, THE Equipment_System SHALL decrement the consumable's count in the Supply_Bag by one and notify the player of the effect.
6. THE Equipment_System SHALL reject a `use` action targeting an item whose category is not `consumable`, and notify the player.

### Requirement 9: Throwables — the `throw` action

**User Story:** As a player, I want to throw bombs at a location, so that I can damage clustered enemies.

#### Acceptance Criteria

1. THE Equipment_System SHALL provide a `throw` action that consumes one unit of a `throwable`-category Supply from the player's Supply_Bag and applies its Item_Effect at a target location.
2. THE `throw` action SHALL accept a target expressed as either explicit coordinates or a target entity resolvable to coordinates on the player's current planet.
3. THE Equipment_System SHALL reject a `throw` whose target is farther than the throwable's throw range (from its Item_Effect or a default) and notify the player.
4. WHEN a throwable's Item_Effect type is `aoe_damage`, THE Combat_Engine SHALL apply the effect's `amount` of damage to every valid target within the effect's `radius` (Manhattan) of the target location, routing damage through the existing damage pipeline via a synthetic weapon (as turrets do).
5. WHEN a `throw` action succeeds, THE Equipment_System SHALL decrement the throwable's count in the Supply_Bag by one and notify the thrower of the outcome.
6. THE Equipment_System SHALL reject a `throw` action targeting an item whose category is not `throwable`, and notify the player.
7. THE damage applied by a throwable SHALL respect target armor (Aggregated_Stat `damage_reduction`) via the same damage formula as weapon attacks.

### Requirement 10: Supply bag and pickup

**User Story:** As a player, I want to carry and pick up ammo and consumables with sensible limits, so that supplies are a managed resource.

#### Acceptance Criteria

1. THE Supply_Bag SHALL store Supplies as `db.supplies: dict[item_key, int]` with non-negative integer counts, and SHALL omit or zero any depleted entry.
2. WHEN a player picks up a Supply drop, THE Equipment_System SHALL add the drop's count to the matching Supply_Bag entry, not exceeding the item's `max_stack` per entry and not exceeding the player's Carry_Capacity in total weight (see Requirement 15).
3. WHEN adding a Supply would exceed `max_stack` for that entry or the player's Carry_Capacity weight, THE Equipment_System SHALL add only up to the binding limit, spawn the remainder as a drop at the player's location, and notify the player (`carry_full`).
4. THE Item_Def SHALL carry a `max_stack` field (positive integer, default 99) for Supply items — an anti-degenerate-stacking cap per bag entry, orthogonal to the weight limit.
5. THE `get` command SHALL pick up Supply drops into the Supply_Bag and Gear drops into the player's inventory (as Game_Item objects), consistent with current coordinate-locked pickup behavior, subject to the weight limit in Requirement 15.

### Requirement 11: Reloading the magazine

**User Story:** As a player, I want to reload my ranged weapon from the ammunition I carry, so that combat has a reload cadence and running dry has a cost.

#### Acceptance Criteria

1. THE Equipment_System SHALL provide a `reload` action that, for the player's equipped ranged weapon, transfers Ammo_Type units from the Supply_Bag into the weapon's `db.loaded`.
2. THE `reload` action SHALL transfer at most `magazine_size − db.loaded` rounds, and at most the amount of that Ammo_Type available in the Supply_Bag, decrementing the Supply_Bag by exactly the amount transferred.
3. WHEN the equipped weapon is already full (`db.loaded == magazine_size`), THE Equipment_System SHALL take no ammo from the Supply_Bag and SHALL notify the player the weapon is already loaded.
4. WHEN the Supply_Bag holds no matching Ammo_Type, THE Equipment_System SHALL reject the reload and notify the player they have no ammunition of that type.
5. WHEN the equipped item is not a ranged weapon (no `ammo_type`), THE Equipment_System SHALL reject the reload and notify the player the weapon does not use ammunition.
6. WHEN a `reload` succeeds, THE Equipment_System SHALL notify the player of the weapon's new loaded/`magazine_size` count and the remaining Ammo_Type in the Supply_Bag.
7. WHEN a fresh ranged weapon Game_Item is created, THE Equipment_System SHALL initialize its `db.loaded` to `magazine_size` (a produced/picked-up weapon arrives with a full magazine, so it is usable before the player's first reload); this starting amount is a tunable behavior, not drawn from the Supply_Bag.

### Requirement 12: Player-facing commands and display

**User Story:** As a player, I want clear commands and a readable equipment view, so that I can manage my loadout.

#### Acceptance Criteria

1. THE `equip <item>` command SHALL equip a resolved Game_Item via the Equipment_System, applying rank gating, and report success or the reason for failure.
2. THE `unequip <slot>` command SHALL unequip the item in a named Equipment_Slot, rejecting any slot name not in `EQUIPMENT_SLOTS`.
3. THE `equipment` command (aliases `eq`, `gear`) SHALL display all eleven Equipment_Slots including empty ones, each slot's occupying item and its stats, and totals for armor (`damage_reduction`), damage (`damage_bonus`), move speed, and sight range.
4. THE `inventory` command (aliases `inv`, `i`) SHALL display carried resources, equipped Gear, and the Supply_Bag contents with their counts.
5. THE `use <item>` command SHALL invoke the Equipment_System `use` action on a resolved consumable.
6. THE `throw <item> [<target>|<x> <y>]` command SHALL invoke the Equipment_System `throw` action on a resolved throwable at the given location.
7. THE `reload` command SHALL invoke the Equipment_System `reload` action on the player's equipped ranged weapon.
8. THE `deposit <resource> [<amount>|all]` and `withdraw <resource> [<amount>|all]` commands SHALL move resources between the player's Spend_Pool and a co-located Storage_Building (Requirement 16).
9. THE `score`/`inventory` command SHALL display the player's carried weight against their carry limit; when the player is at a Storage_Building, its stored contents SHALL be viewable.
10. THE `score` command SHALL include equipment-derived aggregated stat totals.
11. THE `equipment` command SHALL display the equipped ranged weapon's loaded/`magazine_size` ammunition count.
12. Every new player-facing message from these actions SHALL be emitted as a structured `PLAYER_NOTIFICATION` event and formatted by the Notification_Presenter; no domain system SHALL compose player-facing text inline.

### Requirement 13: Content, production, and validation

**User Story:** As a game designer, I want to author equipment, ammo, and consumables in YAML with the game producing them, so that I can tune content without code changes.

#### Acceptance Criteria

1. THE items content (`data/definitions/items.yaml`) SHALL define a starter set spanning the armor body slots, at least one melee and one ranged weapon, at least one Ammo_Type, and at least one consumable and one throwable.
2. THE existing items SHALL be migrated to the new model with no load failure: `scope`→slot `eyes`, `jetpack`→slot `back`, `kevlar_vest`→slot `torso`, `power_armor`→slot `torso` (all four current weapons annotated with a `weapon_type`); every item given a `category`. The ad-hoc slot strings `gadget`, `consumable`, and `armor` (now a *category*, not a slot) SHALL be retired — no shipped item may retain a slot string outside `EQUIPMENT_SLOTS`, since the validator (Requirement 13.5) rejects such Gear at load.
3. THE item production map SHALL associate Armorer (`AR`) with weapons and ammo, Armory (`AA`) with armor, Medbay (`MB`) with consumables, and Lab (`LB`) with futuristic gear and throwables.
4. THE Equipment_System per-tick production SHALL create Supplies into the owner's Supply_Bag and Gear as Game_Item objects, using the production map, unchanged in tick-timing behavior.
5. THE Schema_Validator SHALL enforce, at load time: `slot` in `EQUIPMENT_SLOTS` for Gear; `category` in the six categories; `weapon_type` in `{melee, ranged}` for weapons; `ammo_type` referencing an existing ammo item; `effect.type` in a known set for consumables/throwables; `max_stack` a positive integer; `weight` a non-negative number; `BalanceConfig.resource_weights` keys ⊆ `RESOURCE_TYPES` with non-negative values; and all existing item foreign-key checks (`required_rank`, production-map keys, `ammo_cost` resource names).
6. THE reload of definition data (`@reloaddata`) SHALL atomically swap the extended item definitions, consistent with existing hot-reload behavior.

### Requirement 14: Backward compatibility and safety

**User Story:** As a maintainer, I want the deepened item system to not break existing combat, saves, or tests, so that the feature ships safely.

#### Acceptance Criteria

1. THE Combat_Engine damage formula SHALL remain `max(0, base_weapon_damage + bonuses − Σ damage_reduction)`, with `damage_bonus` added to bonuses and no other change to the formula's shape.
2. WHEN a character has no equipment and no supplies, THE system SHALL behave exactly as an unequipped character does today (no weapon → default/melee behavior; zero armor; zero aggregated stats).
3. THE feature SHALL preserve the existing `EquipmentHandler` public API (`equip`, `unequip`, `get_equipped`, `get_all_equipped`, `get_stat_total`, `get_slot_names`), extending but not removing methods.
4. THE feature SHALL make the new item fields readable from a live `GameItem` — either by adding explicit accessors and extending the creation factory's field-copy, or by reading through the item's `ItemDef` (via `item_key`). `GameItem` today exposes only a fixed set of named accessors and its factory copies only those, so the new fields do NOT resolve automatically; this is explicit work (task 1.7). Existing `GameItem` creation paths continue to work with added `ItemDef` fields defaulted.
5. THE full test suite SHALL pass, and new behavior SHALL be covered by unit tests, property-based tests for stat-aggregation and carry-limit invariants, and end-to-end presenter tests for the new notification kinds.
6. THE character's `db.resources` (Spend_Pool) SHALL remain the pool every existing cost check reads (build, upgrade, research, `ammo_cost`), so no cost path changes semantics; only its new upper bound (carry weight) and the addition of a separate Storage_Building pool are introduced.

### Requirement 15: Weight-based carry capacity

**User Story:** As a player, I want a sensible limit on how much I can carry, so that hauling is a real decision — I can carry a lot, but not infinitely.

#### Acceptance Criteria

1. THE Item_Def SHALL carry a `weight` field (a number ≥ 0, default 1.0) — the weight of one unit of the item.
2. THE BalanceConfig SHALL carry a hot-tunable `resource_weights` map (resource name → number ≥ 0), giving each resource type a light per-unit weight; a resource absent from the map SHALL default to a documented small constant.
3. THE `world/constants.py` SHALL define `BASE_CARRY_WEIGHT`, the structural base carry-weight limit for any holder.
4. THE Equipment_System SHALL compute a holder's carried weight as Σ(`Item_Def.weight` × count) over the Supply_Bag plus Σ(`resource_weights[type]` × amount) over the holder's on-person resources (`db.resources`), and SHALL NOT count equipped Gear toward carried weight.
5. THE Equipment_System SHALL compute a holder's carry limit as `BASE_CARRY_WEIGHT` plus the Aggregated_Stat `carry_capacity` from equipped Gear.
6. WHEN a holder is an admin (satisfies `world.utils.is_admin`, i.e. Builder+), THE Equipment_System SHALL NOT enforce any carry limit on that holder.
7. WHEN a non-admin holder holds a resource/supply pool (a player today; any future Agent that gains one — see D7 scope note), THE Equipment_System SHALL enforce that its carried weight never exceeds the holder's carry limit, except transiently before an over-capacity inflow is resolved per Requirement 16. In this feature the binding holder is the player; no Agent resource/supply plumbing is added.
8. WHEN a player action (pickup, deposit/withdraw) changes carried weight, THE Equipment_System SHALL evaluate the new carried weight against the limit before committing the change; a passive inflow is handled per Requirement 16. (Equip/unequip do not change carried weight — equipped Gear is excluded.)
9. THE Item_Weight, Resource_Weight values, and `BASE_CARRY_WEIGHT` are explicitly tunable and SHALL be chosen for sensible early-game feel (a player can carry a large but finite amount), subject to post-playtest adjustment.

### Requirement 16: Vault/HQ resource storage and over-capacity inflow

**User Story:** As a player, I want to stockpile resources in my Vault/HQ beyond what I can personally carry, so that the carry limit constrains hauling without capping my whole economy.

#### Acceptance Criteria

1. THE system SHALL give each Storage_Building (a building with the `storage` capability, e.g. HQ, Vault `VT`) a persistent stored-resource pool distinct from any player's Spend_Pool, bounded by the building's `storage_capacity`.
2. THE HQ SHALL have a non-zero `storage_capacity` so that a functional Storage_Building is reachable from level 1 (the HQ ships with `storage_capacity: 0` today; this feature raises it). A `storage_capacity` of 0 SHALL mean "no storage," never "unlimited," so the enforcement this feature adds is never silently disabled. (The Vault at rank 36 remains the higher-capacity late-game store.)
3. THE `deposit` command SHALL move resources from the player's Spend_Pool (`db.resources`) into a co-located Storage_Building's pool, up to the building's remaining `storage_capacity`.
4. THE `withdraw` command SHALL move resources from a co-located Storage_Building's pool into the player's Spend_Pool, up to the player's remaining carry weight (Requirement 15).
5. WHEN a withdraw would exceed the player's carry limit, THE Equipment_System SHALL move only up to the limit and notify the player of the amount left in storage.
6. THE harvester delivery behavior SHALL deposit delivered resources into the target Storage_Building's pool (up to `storage_capacity`) rather than the owning player's Spend_Pool, and SHALL skip storage buildings with no remaining capacity when selecting a delivery target so a full/zero-capacity building never causes a delivery to spill its entire load.
7. WHEN a resource inflow to a *holder pool* (a player picking up a drop into their Spend_Pool/Supply_Bag; a harvester delivery into a Storage_Building's pool) would exceed the holder's limit (carry weight for a player, `storage_capacity` for a building), THE system SHALL add only up to the limit and spawn the remainder as a `ResourceDrop` at the holder's coordinates, and SHALL notify the owning player (`storage_full`/`carry_full`). (Note: Extractor output and presence-harvest already spawn ground drops rather than writing a holder pool — see Requirement 15's note — so the cap for those bites only at pickup, not at production.)
8. THE system SHALL NOT silently destroy resources on over-capacity: the amount added plus the amount spawned as drops SHALL equal the amount that attempted to flow in.
9. THE Spend_Pool SHALL remain the pool all cost checks (build, upgrade, research, `ammo_cost`) read, so building storage never satisfies a cost directly; players withdraw to their person to spend.
10. THE `score`/`inventory` display SHALL show the player's carried weight against their carry limit; a Storage_Building's contents SHALL be viewable when the player is at it.
