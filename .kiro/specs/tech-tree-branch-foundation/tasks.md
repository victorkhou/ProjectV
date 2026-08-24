# Implementation Plan: Technology Branch Foundation

## Overview

The plan follows the design's own rollout order (design §Migration and Rollout), which is already staged so each stage is independently shippable and independently revertible:

1. **Data and validation** — the six-Branch vocabulary, the two new `BuildingDef` fields, the two new labs, `branches.yaml`, the new `BalanceConfig` fields, and the twelve new schema rules. At the end of this stage the six Branches exist in data and the catalog validates.
2. **`Branch_System`** — identity, commitment, estate, dormancy, reinstatement, the three construction gates, the agent-role changes, the shared vector services, and the composition-root wiring. At the end of this stage commitment, switching, dormancy, and the role gates are live.
3. **`OperationDriver`** — the lifecycle state machine, the ordered validation chain, charge-then-Pending, notifications, the ledgers, `OperationRecord` persistence, and the idempotent restart rebuild. With no Vector_System registered the operation half is inert, which is exactly the state the six vector specs build on.

No Signature_Vector is implemented here. Strategic_Strike, Trap, Contagion, Intrusion, Convoy, and Detection_Sweep each ship in their own spec as a conforming subclass of `OperationDriver`.

Language: Python 3, matching the existing codebase. Property tests use Hypothesis in the established style of `mygame/world/systems/tests/test_prop_*.py` (Evennia stubbed at import, framework-free fakes, `@given` + `@settings`).

## Tasks

- [x] 1. Branch vocabulary, definition fields, and the Operation_Kind registry
  - [x] 1.1 Extend the Branch vocabulary in `mygame/world/constants.py`
    - Add `RESEARCH_TREE_BIO = "bio"` and `RESEARCH_TREE_CYBER = "cyber"`; extend `RESEARCH_TREES` to the six-value tuple
    - Add the `BRANCHES` alias (same tuple, domain name), `BRANCH_DOCTRINE`, `BRANCH_ROLE`, `BRANCH_OPERATION_KIND`, and `OPERATION_KINDS = tuple(BRANCH_OPERATION_KIND.values())`
    - Add `ATTR_BRANCH_ABANDONED = "branch_abandoned"` and `ATTR_BRANCH_REINSTATEMENT = "branch_reinstatement"`
    - _Requirements: 1.1, 7.4, 7.11, 15.5_

  - [x] 1.2 Add the two new building-definition fields and the NPC-template Branch field
    - In `mygame/world/definitions.py`: `BuildingDef.branch: str | None = None` and `BuildingDef.unlock_technology: str | None = None`, both documented as optional so every pre-feature building stays a Neutral_Building
    - In `mygame/world/definitions.py`: `BaseTemplateDef.branch: str | None = None`
    - In `mygame/world/data_registry.py`: map both new building keys in the building loader and the template key in the outpost loader, so `get_building(abbr).branch` and the template's Branch read back
    - _Requirements: 2.1, 2.2, 2.5, 2.6, 6.1, 11.5_

  - [x] 1.3 Add `OperationKindDef` and load `data/definitions/branches.yaml`
    - Add the frozen `OperationKindDef` dataclass (`kind`, `branch`, `carrier_role`, `cost_field`, `cooldown_field`, `cap_field`, `agent_xp_field`) to `mygame/world/definitions.py`
    - Create `mygame/data/definitions/branches.yaml` with the `counter_web` map (the shipped one-advantage cycle) and the six `operations` entries
    - Add `DataRegistry._load_branches` following the `_load_alliance_perks` / `_load_directives` pattern: optional file, present-but-invalid fails the load, absent yields empty `self.counter_web` / `self.operation_kinds`
    - _Requirements: 7.2, 9.1, 12.1_

  - [x] 1.4 Add the new `BalanceConfig` fields and their type/range validation
    - In `mygame/world/definitions.py` `BalanceConfig`: the seven cross-cutting fields (`branch_reinstatement_cost_fraction`, `minimum_response_window_ticks`, `counter_advantage_cap`, `branch_cost_parity_tolerance`, `new_player_vector_shield_level`, `escalation_window_ticks`, `escalation_cap`) and the four per-Operation_Kind fields for each of the six kinds (cost map, cooldown ticks, max-in-flight, agent XP) with the design's placeholder defaults
    - In `mygame/world/schema_validator.py` `validate_balance`: append the six `*_cost` maps to `resource_map_fields`, and add the explicit range checks from the design's range table, collecting every violation rather than failing on the first
    - _Requirements: 12.1, 15.6, 15.7_

  - [x] 1.5 Create the shared Hypothesis strategies module (catalog and commitment half)
    - New `mygame/world/systems/tests/branch_strategies.py` with the Evennia stub block copied from `test_prop_building_system.py`
    - Define `branch_st`, `maybe_branch_st`, `noisy_branch_st`, `abbr_st`, `cost_map_st`, `building_def_dict_st`, `tech_def_dict_st`, `tech_key_st`, `dataset_st`, `counter_web_st`, `owned_buildings_st`, `researched_set_st`, `pending_set_st`, `agent_state_st`, `tick_st`, `balance_value_st`
    - Add the `FakePlayer` / `FakeBuilding` / `FakeAttributes` fakes the property modules share, including the in-place-hostile `FakeAttributes` variant that discards mutations to a returned container
    - _Requirements: 15.1, 15.4_

  - [x] 1.6 Write the definition round-trip and balance-validation property tests
    - New `mygame/world/systems/tests/test_prop_branch_catalog.py`
    - **Property 3: Definition fields round-trip through the loader with documented defaults**
    - **Validates: Requirements 2.1, 2.2, 6.1, 9.1, 11.5**
    - **Property 26: Balance-field validation reports exactly the reference violation set**
    - **Validates: Requirements 15.6**

- [x] 2. Branch catalog data and the twelve schema rules
  - [x] 2.1 Add the two new lab buildings
    - In `mygame/data/definitions/buildings.yaml`: Biolab (`BX`, `research_tree: bio`, `branch: bio`) and Signals Lab (`SG`, `research_tree: cyber`, `branch: cyber`), both with the `research_lab` capability and the same level/deed gate shape as the existing four labs
    - _Requirements: 1.2, 2.4, 3.6, 10.5_

  - [x] 2.2 Seed the minimum Branch content the coverage rules require
    - In `mygame/data/definitions/technologies.yaml`: at least one technology with `tree: bio` and one with `tree: cyber` (the four original trees already have technologies)
    - In `mygame/data/definitions/buildings.yaml`: for each of the six Branches, one non-lab building declaring `branch: <B>` and `unlock_technology: <a tech of B>`, with at least one of `Circuits` / `Energy` / `Nexium` in its cost and a `rank_requirement` at or above that Branch's lab; the vector specs extend this chain rather than replacing the affiliation
    - Do not add `branch` to any building that shipped before this feature — every one of those stays Neutral
    - _Requirements: 1.5, 2.5, 2.7, 6.7, 10.5, 12.4_

  - [x] 2.3 Add the per-building Branch rules to `validate_buildings`
    - Rule 1 — `branch`, when present, must be one of `BRANCHES`; error names the abbreviation and the offending value
    - Rule 2 — a `research_lab`-capability building must have `branch` absent or equal to `research_tree`; error names the abbreviation and both values
    - Rule 3 — `unlock_technology`, when present, must be a non-empty string
    - _Requirements: 2.3, 2.4, 1.7_

  - [x] 2.4 Add the catalog-coverage cross-file rules to `cross_validate`
    - Rule 4 — six-Branch tree-to-lab bijection: the duplicate check made unconditional with the error naming both abbreviations and the duplicated Branch; a Branch hosted by no lab is an error whenever the dataset uses labs
    - Rule 5 — every Branch has at least one `TechnologyDef` with that `tree`
    - Rule 6 — every Branch has at least one non-lab `BuildingDef` with that `branch`, and at least one such building carrying an `unlock_technology`
    - Rule 7 — `unlock_technology` exists in `registry.technologies`, and its `tree` equals the building's `branch`; two distinct errors
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.7, 2.7, 6.4, 6.5, 6.7_

  - [x] 2.5 Add the web, role, level, and resource cross-file rules to `cross_validate`
    - Rule 9 — role-to-Branch bijection over `BRANCH_ROLE`, cross-checked against `AGENT_ROLES[role].branch` so the constant and the role table cannot disagree; one error shape per failing direction
    - Rule 10 — Counter_Web well-formedness: keys and values inside the six, out-degree between 1 and 2, in-degree at least 1, self-edges rejected
    - Rule 11 — every Branch_Building's `rank_requirement` is at or above its Branch's lab's; error names the abbreviation and both values
    - Rule 12 — the union of build costs over each Branch's tech-gated buildings names at least one of `Circuits`, `Energy`, `Nexium`
    - _Requirements: 7.11, 9.2, 9.3, 9.12, 10.5, 12.5, 1.7_

  - [x] 2.6 Add the investment-score parity rule
    - Implement `SchemaValidator._branch_investment_score(registry, branch)` as the weighted sum over the Branch's lab and Branch_Buildings' build costs plus its technologies' resource costs, using `balance.resource_weights` with `DEFAULT_RESOURCE_WEIGHT` as the fallback
    - Rule 8 — flag every Branch whose absolute deviation from the six-Branch mean exceeds `branch_cost_parity_tolerance`, naming the Branch, its score, and the mean
    - Tune the costs added in tasks 2.1 and 2.2 until the shipped dataset passes the rule
    - _Requirements: 9.9, 9.10_

  - [x] 2.7 Write the catalog validation property test
    - Append to `mygame/world/systems/tests/test_prop_branch_catalog.py`
    - **Property 1: Catalog validation reports exactly the reference violation set**
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.7, 2.3, 2.4, 2.7, 6.4, 6.5, 6.7, 7.11, 9.2, 9.3, 9.12, 10.5, 12.4, 12.5**

  - [x] 2.8 Write the investment-score parity property test
    - Append to `mygame/world/systems/tests/test_prop_branch_catalog.py`
    - **Property 2: A Branch's investment score is the weighted sum, and the parity flag is the tolerance comparison**
    - **Validates: Requirements 9.9, 9.10**

  - [x] 2.9 Write the fixed-cardinality and membership unit tests
    - New `mygame/world/systems/tests/test_branch_catalog.py`: six Branches in `RESEARCH_TREES` / `BRANCHES`, six `operation_kinds` registry entries each naming a distinct Branch and role, six sets of four `BalanceConfig` per-kind fields present and well-formed, the shipped Counter_Web giving each Branch exactly one advantage and one disadvantage
    - Assert the existing one-research-lab-per-planet limit refuses each of the other five labs
    - _Requirements: 1.1, 3.6, 7.2, 9.1, 12.1_

- [x] 3. Checkpoint - catalog loads and validates
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. `Branch_System` identity, commitment, and estate queries
  - [x] 4.1 Create the `Branch_System` module with the identity surface
    - New `mygame/world/systems/branch_system.py`: `BranchSystem(BaseSystem)` with every collaborator constructor-injected and no module-scope Evennia import
    - Implement `branch_of_building`, `branch_of_technology`, `lab_for_branch`, `branch_buildings`, `role_for_branch`, `branch_overview`, all resolving through the injected `DataRegistry` and all returning a documented empty value rather than raising
    - _Requirements: 1.6, 2.6, 13.3, 15.1, 15.3, 15.4_

  - [x] 4.2 Implement the commitment queries
    - `commitment(player, planet=None)` derived from `world.utils.owner_research_lab` plus the lab definition's `research_tree`, holding no stored copy; `has_commitment(player, branch, planet=None)`
    - Leave `owner_research_lab` untouched so a lab that is offline, mid-upgrade, or suspended still yields its owner's commitment, and a destroyed lab yields none
    - Add `TechLabSystem.set_branch_resolver` and make `owned_research_tree` a thin forwarder to the resolver when one is wired
    - _Requirements: 3.1, 3.2, 3.7, 3.8, 3.9, 14.6_

  - [x] 4.3 Implement the estate queries
    - `estate(player, branch, planet=None)`, `estate_count`, and `conflicting_estates(player, planet, incoming_branch)`, planet-scoped, including buildings under construction, resolving each building's Branch through `_branch_of_live_building` (`bdef.branch or bdef.research_tree`)
    - _Requirements: 4.3, 4.6, 4.7, 14.6_

  - [x] 4.4 Write the registry-accessor property test
    - Append to `mygame/world/systems/tests/test_prop_branch_catalog.py`
    - **Property 4: Registry accessors agree with a naive scan, with or without a global registry**
    - **Validates: Requirements 1.6, 2.6, 13.3, 15.4**

  - [x] 4.5 Write the commitment property test
    - New `mygame/world/systems/tests/test_prop_branch_commitment.py`
    - **Property 5: Branch_Commitment is the owned completed lab's Branch, independent of Operational state and of restarts**
    - **Validates: Requirements 3.1, 3.2, 3.7, 3.8, 3.9, 5.10, 14.6**

- [x] 5. Construction gates and Branch switching
  - [x] 5.1 Implement the three construction gates
    - In `branch_system.py`: `_validate_branch_affiliation` (refuse a Branch_Building without a matching commitment, reporting the required lab, or with a mismatched commitment, reporting both Branches), `_validate_branch_switch` (refuse a Branch_Lab while a conflicting estate is non-empty, reporting the count and each blocking building's abbreviation and coordinates; when no conflict remains but the commitment changes, report the count of outgoing recorded technologies that will go dormant), and `_validate_unlock_technology` (require the named technology researched and its effects applied, reporting the technology name and its hosting Branch)
    - Expose them through `construction_validators()`, each returning a message key plus structured data and never composed prose
    - _Requirements: 3.3, 3.4, 3.5, 4.1, 4.2, 4.8, 6.2, 6.3, 13.4, 13.5_

  - [x] 5.2 Splice the gates into `BuildingSystem`'s ordered chain
    - Add `BuildingSystem.set_branch_validators` and insert the three callables into `_validate_construction` immediately after `_validate_one_research_lab_per_planet` and before `_validate_rank_requirement`, so every gate runs before `_validate_resources`
    - Update `_validate_one_research_lab_per_planet`'s message text to the Branch vocabulary; its capability keying already covers the two new labs
    - _Requirements: 3.6, 4.8, 6.6, 13.4_

  - [x] 5.3 Report estate progress on demolish
    - In `mygame/world/systems/building_system.py`: on a successful demolish of a Branch_Building, report the number of buildings remaining in that building's Branch_Estate on that planet via one `estate_count` call, leaving the existing `demolish_refund_rates` × `get_building_investment` refund arithmetic unchanged
    - _Requirements: 4.4, 4.5_

  - [x] 5.4 Write the estate-and-switch property test
    - Append to `mygame/world/systems/tests/test_prop_branch_commitment.py`
    - **Property 6: Branch_Estate membership equals the reference scan, and the switch report equals the estate**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.5, 4.7, 13.4**

  - [x] 5.5 Write the switching and refund unit tests
    - New `mygame/world/systems/tests/test_branch_switching.py`: the demolish refund on a Branch_Building equals `rate × get_building_investment`; a Neutral_Building is buildable under every commitment and under none; melee, ranged, bombs, walls, turrets, and shields are unaffected by holding no commitment
    - _Requirements: 4.4, 10.8, 2.5_

- [x] 6. Dormancy, the Operational overlay, and Reinstatement
  - [x] 6.1 Implement the Operational overlay
    - `BranchSystem.is_operational(building)` = `world.utils.building_is_operational` AND (no affiliation OR the owner's commitment on that planet equals the affiliation), with `world.utils.building_is_operational` left unmodified
    - _Requirements: 5.4, 11.3_

  - [x] 6.2 Add the dormancy filter to the bonus recompute
    - In `mygame/world/systems/tech_system.py`: filter `recompute_tech_bonuses` to the live commitment, excluding technologies awaiting Reinstatement, and keep the researched set untouched
    - Keep the unwired-resolver fallback: with no Branch resolver injected the method accumulates every researched technology exactly as before this feature
    - _Requirements: 5.1, 5.2, 5.3, 5.7, 5.10_

  - [x] 6.3 Subscribe the recompute triggers
    - In `branch_system.py`: subscribe to `CONSTRUCTION_COMPLETED` and `BUILDING_DESTROYED` for Branch_Labs, plus the demolish and planet-change paths, each calling `TechLabSystem.recompute_tech_bonuses`
    - Call `AgentSystem.unassign_branch_roles` from the same lapse path so a dormant Branch commands no agents
    - _Requirements: 5.2, 3.8, 7.8_

  - [x] 6.4 Implement the Reinstatement bookkeeping
    - In `branch_system.py`, as the single writer of `db.branch_abandoned` and `db.branch_reinstatement`: set the abandoned bit only on a voluntary demolition of that Branch's lab, and on a Branch_Lab completion seed the pending set from the owner's recorded technologies in that Branch when the bit is set, clearing the bit
    - A lab lost to hostile destruction writes nothing, so rebuilding it restores the Branch's effects on the next recompute with no research
    - _Requirements: 5.5, 5.9, 15.5_

  - [x] 6.5 Implement the Reinstatement research job
    - In `mygame/world/systems/tech_system.py`: an ordinary `_active_research` entry carrying a `reinstatement: True` marker so it shares the tick countdown, the completion publish, and the existing rank gate
    - Scale cost per line and duration by `branch_reinstatement_cost_fraction` with the documented rounding and a floor of one; on completion remove the key from `db.branch_reinstatement` and recompute
    - Teach `start_research` to treat a key in the pending set as reinstatable rather than already researched
    - _Requirements: 5.6, 5.7, 5.8_

  - [x] 6.6 Report commitment and dormancy in the technology view
    - In `mygame/world/systems/tech_system.py`: the technology view reports the occupied planet's commitment, that Branch's Signature_Vector, that Branch's researched and available technologies, and for each dormant Branch the count of recorded technologies and the Reinstatement cost fraction — all as structured notification data
    - _Requirements: 13.1, 13.2, 13.5_

  - [x] 6.7 Write the bonus-filter and Operational-overlay property tests
    - Append to `mygame/world/systems/tests/test_prop_branch_commitment.py`
    - **Property 7: Applied tech bonuses equal the accumulation over the committed, non-pending techs**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.7, 5.10, 13.1, 13.2**
    - **Property 10: A Branch_Building is Operational exactly when the base gate passes and its Branch is live**
    - **Validates: Requirements 5.4, 11.3**

  - [x] 6.8 Write the Reinstatement property tests
    - New `mygame/world/systems/tests/test_prop_branch_reinstatement.py`
    - **Property 8: A Reinstatement job costs the defined values scaled by the configured fraction**
    - **Validates: Requirements 5.6**
    - **Property 9: Reinstatement is required after abandonment and not after destruction**
    - **Validates: Requirements 5.5, 5.9**

- [x] 7. Branch agent roles
  - [x] 7.1 Add the Branch field and the six roles to the role table
    - In `mygame/typeclasses/agent_scripts.py`: `RoleSpec.branch: str | None = None`; add `spotter`, `sapper`, `courier`, `infiltrator`; drop `hidden` from `medic`; give `scout` `branch="research"`
    - Add the four new behavior scripts as the minimal per-tick shells the role table needs; leave the derived maps in `mygame/world/systems/agent_constants.py` deriving as they already do
    - _Requirements: 7.4, 7.11_

  - [x] 7.2 Gate role assignment and add the lapse and XP paths
    - In `mygame/world/systems/agent_system.py`: `set_branch_resolver`; `assign_role` refuses a gated role whose Branch differs from the commitment on the agent's planet, reporting the required Branch; `scout` stays ungated so existing patrols keep working
    - Add `unassign_branch_roles(player, planet, branch)` reusing the existing `_detach_behavior_script` and `role_target` clearing path, and `award_operation_xp(agent, kind)` reading `OperationKindDef.agent_xp_field`
    - Leave the rank-derived agent cap untouched
    - _Requirements: 7.6, 7.7, 7.8, 7.9, 7.10_

  - [x] 7.3 Write the role-gate property test
    - Append to `mygame/world/systems/tests/test_prop_branch_commitment.py`
    - **Property 12: The Branch role gate permits exactly matching commitments, and a lapse clears exactly the gated roles on that planet**
    - **Validates: Requirements 7.6, 7.7, 7.8**

  - [x] 7.4 Write the role-table unit tests
    - New `mygame/world/systems/tests/test_branch_roles.py`: six roles carry the expected `branch`, `scout` is assignable with no commitment, the rank-derived agent cap is unchanged under every commitment, and harvest yields, extractor output, and storage capacities are unchanged for every non-`resource` commitment
    - _Requirements: 7.4, 7.9, 12.7_

- [x] 8. Shared vector services, composition root, and tick registration
  - [x] 8.1 Implement carrier eligibility, the charge and refund path, and targeting
    - In `branch_system.py`: `eligible_carrier(player, role, planet=None)` as the conjunction of alive, assigned to the required role, outside reserve, and not incapacitated
    - `charge(player, cost)` delegating to the existing whole-or-none `has_resources` / `deduct_resources` pair, and `refund(player, cost)` adding each line back
    - `may_target(actor, target)` folding the new-player shield level, the allied-target refusal, and the support-consent check, applied to alliance members and allies on identical terms; revoke a player's outstanding support and target-sharing consents when they leave an alliance
    - _Requirements: 7.1, 7.5, 10.4, 10.7, 11.8, 11.9, 11.11, 12.2, 12.3_

  - [x] 8.2 Implement the cooldown, in-flight, and escalation ledgers
    - Cooldown per originating building per Operation_Kind on the building's `db.vector_cooldowns`, read against the injected tick function; `cooldown_remaining` and `note_cooldown`
    - `in_flight_count` / `in_flight_cap` counting a vector's own non-terminal records for that player on that planet
    - Escalation on the attacker's `db.vector_escalation`, pruned to `escalation_window_ticks` on read; `escalation_remaining` and `note_escalation`
    - _Requirements: 8.19, 8.20, 10.6, 10.7_

  - [x] 8.3 Implement the Counter_Web multiplier and the Response_Window helper
    - `counter_multiplier(actor_branch, target_branch)`: a single lookup clamped to `[1.0, counter_advantage_cap]`, returning exactly `1.0` when the web names no edge, with no accumulation loop
    - `response_window(base_ticks, reduction=0)` returning `max(minimum_response_window_ticks, base - reduction)` for a hostile operation
    - _Requirements: 8.8, 9.4, 9.5_

  - [x] 8.4 Implement vector registration and the tick fan-out
    - `register_vector(vector)` and `process_tick(tick_number)` calling each registered vector's `advance_all(tick)` inside its own try/except so a broken vector cannot stop the others; an empty registry is a no-op
    - _Requirements: 8.10, 15.8, 15.9_

  - [x] 8.5 Wire the composition root
    - In `mygame/server/conf/game_init.py`: construct `BranchSystem` with every collaborator injected, call `building_system.set_branch_validators(...)`, `tech_system.set_branch_resolver(...)`, `agent_system.set_branch_resolver(...)`, and register it in `game_systems`
    - _Requirements: 15.1, 15.2, 15.4_

  - [x] 8.6 Register the tick step
    - In `mygame/typeclasses/scripts.py`: add `("vector_operations", ...)` to `TICK_STEP_ORDER` between `combat_resolution` and `effect_ticks`, and register the step in `_build_tick_steps` only when `branch_system` is present
    - _Requirements: 15.9_

  - [x] 8.7 Add the Branch-commitment directive step and its wiring tests
    - In `mygame/data/definitions/directives.yaml`: at least one directive step introducing the Branch commitment decision, positioned at or after the existing lab level and deed gate
    - New `mygame/world/tests/test_branch_integration.py`: composition-root smoke asserting `branch_system` is installed, the three validators sit in `BuildingSystem`'s chain at the documented positions, `_build_tick_steps` emits `vector_operations`, and the directive step sits at or after the lab gate
    - _Requirements: 13.7, 15.9_

  - [x] 8.8 Write the architectural guard tests
    - New `mygame/world/systems/tests/test_branch_architecture.py`: `branch_system` imports with `evennia` absent from `sys.modules`; its AST holds no top-level `evennia` import; every query answers with the singleton `DataRegistry` cleared; no module other than `branch_system.py` assigns `db.branch_abandoned` or `db.branch_reinstatement`; mutating each new balance field after construction changes the next call's behavior; 100 ticks with a Branch_Estate and no operations leaves resources unchanged
    - _Requirements: 12.8, 15.1, 15.4, 15.5, 15.7_

- [x] 9. Checkpoint - commitment, dormancy, and role gates are live
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Operation records, states, and persistence discipline
  - [x] 10.1 Add the lifecycle value types
    - New `mygame/world/systems/operation_contract.py`: `OperationState` (StrEnum, six values), `TERMINAL_STATES`, the `OperationRecord` dataclass with every persisted field from the design, `OperationRecord.to_dict` / `from_dict` reading each field by value with its documented default, and the frozen `OperationOutcome` with its `accepted` / `refused` / `failed` constructors
    - _Requirements: 8.1, 8.2, 8.21, 8.24, 14.8_

  - [x] 10.2 Implement the read-copy-write persistence helpers
    - `_read_records(owner)` returning a copy and treating an absent attribute as an empty list; `_write_records(owner, records)` replacing the whole container; both tolerant of an owner with no `attributes` handler
    - _Requirements: 14.1, 14.7, 14.8_

  - [x] 10.3 Extend the shared strategies with the lifecycle generators
    - In `mygame/world/systems/tests/branch_strategies.py`: `record_st`, `lifecycle_event_st`, `event_sequence_st`, `check_subset_st`, and the fault-point pool the resource-conservation property needs
    - _Requirements: 15.1_

  - [x] 10.4 Write the record persistence property tests
    - New `mygame/world/systems/tests/test_prop_operation_persistence.py`
    - **Property 21: An Operation_Record round-trips through persistence**
    - **Validates: Requirements 8.21, 8.22, 14.1, 14.2**
    - **Property 23: Reading a partial record yields documented defaults and never raises**
    - **Validates: Requirements 14.8**

- [x] 11. `OperationDriver` — the Operation Contract
  - [x] 11.1 Implement the driver skeleton and the single state writer
    - In `operation_contract.py`: `OperationDriver` with `operation_kind`, `branch`, `_required_collaborators`, the five required hooks and the five optional ones, the tracked-record list, and `_transition(record, new_state, reason="")` as the only function that writes `record.state`, refusing to move a terminal record and persisting on every accepted transition
    - _Requirements: 8.1, 8.2_

  - [x] 11.2 Implement the ordered validation chain
    - `_CHECK_ORDER` as the declared nine-name tuple and `request(player, **params)` refusing at the first failing check with the check name and the value required to pass it, changing nothing
    - `_check_collaborators` degrades an unwired system to a refusal with a log; `_check_target` folds in the vector hook plus `Branch_System.may_target`; `_check_cooldown`, `_check_in_flight`, and `_check_resources` report their ledger values
    - _Requirements: 6.6, 7.3, 8.3, 8.4, 10.4, 10.6, 10.7, 11.9, 15.2, 15.3_

  - [x] 11.3 Implement charge-then-Pending with the refund path and the window floor
    - Charge the Operation_Kind's cost through `Branch_System.charge` before the record enters Pending; on any failure entering Pending, refund the whole charged amount, log, and return `failed`
    - `_resource_cost` returns an empty map for an NPC-base owner so an NPC operation charges nothing
    - `_floor_response_window` clamps a hostile operation's `ticks_remaining` to `minimum_response_window_ticks` on entry and on resume; `note_cooldown` fires on acceptance
    - _Requirements: 8.5, 8.6, 8.8, 9.4, 11.6, 12.2, 12.3, 12.6_

  - [x] 11.4 Implement the notification points and the presenter kinds
    - Publish the five notification points through `BaseSystem.notify` with structured payloads and no composed text; resolve the resolution audience as the owners of affected entities plus the players on affected tiles, de-duplicated, from the effect's area with no ownership or alliance exclusion
    - Add the nine new kinds (`vector_incoming`, `vector_resolved`, `vector_hit`, `vector_suspended`, `vector_resumed`, `vector_expired`, `vector_cancelled`, `vector_discarded`, `vector_consent_required`) to `mygame/world/presenters/notification_presenter.py`
    - _Requirements: 8.7, 8.12, 8.13, 11.8, 11.10, 13.5, 13.6, 13.8_

  - [x] 11.5 Implement per-tick advancement, suspension, and cancellation
    - `advance_all(tick)` isolating each record in its own try/except and keeping a record whose advance raised; `_advance_one` checking the fatal carrier and origin conditions before the clock, then suspension, resume, bounded lifetime, and the effect clock
    - `suspend` snapshots the remaining ticks and `resume` restores them; `_expire` restores each suspended entity to its prior state; subscribe to agent death and reserve, `BUILDING_DESTROYED`, the base-elimination path, and commitment loss so each drives the transition the contract declares
    - Route every effect through `CombatEngine.apply_direct_hit` or the existing `db.active_effects` list, attributing the effect to the owning player
    - _Requirements: 8.9, 8.10, 8.11, 8.13, 8.14, 8.15, 8.16, 8.17, 8.18, 8.23, 9.8, 9.11, 10.1, 10.2, 10.3, 11.4_

  - [x] 11.6 Implement the restart rebuild
    - `rebuild(planet_rooms)` keyed by `op_id` so a repeated rebuild duplicates nothing, skipping terminal records, discarding a record with an unresolvable owner, building, carrier, or target with a log naming the Operation_Kind and each missing reference, and logging and continuing past a record that fails to parse
    - Call each registered vector's rebuild from `mygame/server/conf/game_init.py` alongside the existing `bomb_system.rebuild_from_world` call, isolated per vector
    - _Requirements: 8.22, 14.3, 14.4, 14.5_

  - [x] 11.7 Write the validation-chain and resource-conservation property tests
    - New `mygame/world/systems/tests/test_prop_operation_lifecycle.py`
    - **Property 13: The validation chain refuses at the earliest failing check, with exactly one reason**
    - **Validates: Requirements 6.6, 7.3, 8.3, 8.4, 11.9, 15.2**
    - **Property 14: Resources are conserved unless an operation reaches Pending**
    - **Validates: Requirements 4.8, 8.4, 8.5, 8.6, 12.2, 12.6**
    - **Property 24: Every request and every public query returns a value and raises nothing**
    - **Validates: Requirements 8.24, 15.3**

  - [x] 11.8 Write the lifecycle and timing property tests
    - Append to `mygame/world/systems/tests/test_prop_operation_lifecycle.py`
    - **Property 15: A terminal state is final, and each event drives the expected transition** (`max_examples=200`)
    - **Validates: Requirements 8.1, 8.2, 8.11, 8.13, 8.16, 8.17, 8.18, 11.4**
    - **Property 16: Suspension delays rather than restarts, and one tick advances by exactly one** (`max_examples=200`)
    - **Validates: Requirements 8.9, 8.14, 8.15**
    - **Property 17: The Response_Window never falls below the floor, whatever the reduction**
    - **Validates: Requirements 8.8, 9.4, 11.6**

  - [x] 11.9 Write the services, ledger, and area property tests
    - Append to `mygame/world/systems/tests/test_prop_operation_lifecycle.py`
    - **Property 11: Carrier eligibility is the conjunction of the four conditions**
    - **Validates: Requirements 7.1, 7.5**
    - **Property 18: A Counter_Web advantage is bounded and never compounds**
    - **Validates: Requirements 9.4, 9.5**
    - **Property 19: Cooldown and in-flight counts equal their reference computations, and refusals report them**
    - **Validates: Requirements 8.19, 8.20**
    - **Property 20: The escalation cap and the new-player shield hold regardless of the alliance relationship**
    - **Validates: Requirements 10.4, 10.6, 10.7**
    - **Property 25: An area effect reaches every entity in the area, allied or not**
    - **Validates: Requirements 11.10**

  - [x] 11.10 Write the rebuild property test
    - Append to `mygame/world/systems/tests/test_prop_operation_persistence.py`
    - **Property 22: Rebuilding is idempotent, isolated, and discards dangling records** (`max_examples=200`)
    - **Validates: Requirements 14.3, 14.4, 14.5**

  - [x] 11.11 Write the notification, damage-path, and no-change unit tests
    - New `mygame/world/systems/tests/test_operation_contract.py`: one payload-shape test per new notification kind; every kind the new systems emit is a key of `NotificationPresenter._FORMATTERS` and all six lifecycle states have a kind; the mocked `CombatEngine` receives the owning player as `attacker` and is the only damage path the driver calls; shields project onto and guards defend a Branch_Building identically to a Neutral_Building; alliance perk categories are unchanged
    - _Requirements: 9.8, 10.3, 11.1, 11.2, 11.7, 13.6, 13.8_

  - [x] 11.12 Write the restart round-trip integration test
    - Append to `mygame/world/tests/test_branch_integration.py`: place operations against a fake world, tear the systems down, rebuild, and assert every non-terminal operation is tracked and advances on the next tick
    - _Requirements: 8.22, 14.2, 14.3_

- [x] 12. Final checkpoint - the framework ships inert
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- No sub-task is marked optional. The design's Testing Strategy presents all 26 property modules and the guard tests as required deliverables (the properties are how the requirements are verified, and several guards — presenter-kind coverage, the single-writer check, the damage-path check — exist specifically to make a regression a test failure rather than a silent behavior change).
- Each stage matches the design's rollout: stage 1 (tasks 1-2) ships the catalog, stage 2 (tasks 4-8) ships `Branch_System`, stage 3 (tasks 10-11) ships the Operation Contract with no vector registered.
- Property tests are scheduled immediately after the code they exercise, and `branch_strategies.py` lands before the modules that import it (task 1.5 for the catalog and commitment generators, task 10.3 for the lifecycle and record generators).
- Several tasks touch the same file in sequence (`definitions.py`, `schema_validator.py`, `branch_system.py`, `operation_contract.py`). The dependency graph keeps those in separate waves.
- No Signature_Vector work appears here. Each vector spec supplies `validate_target`, `build_record`, `on_resolve`, `persistence_owner`, and `discover_records`, plus its data, and inherits everything task 11 builds.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3"] },
    { "id": 3, "tasks": ["1.4", "2.1"] },
    { "id": 4, "tasks": ["1.5", "2.2"] },
    { "id": 5, "tasks": ["1.6", "2.3"] },
    { "id": 6, "tasks": ["2.4"] },
    { "id": 7, "tasks": ["2.5"] },
    { "id": 8, "tasks": ["2.6"] },
    { "id": 9, "tasks": ["2.7", "4.1"] },
    { "id": 10, "tasks": ["2.8", "4.2"] },
    { "id": 11, "tasks": ["2.9", "4.3"] },
    { "id": 12, "tasks": ["4.4", "5.1"] },
    { "id": 13, "tasks": ["4.5", "5.2"] },
    { "id": 14, "tasks": ["5.3", "6.1"] },
    { "id": 15, "tasks": ["5.4", "6.2"] },
    { "id": 16, "tasks": ["5.5", "6.3"] },
    { "id": 17, "tasks": ["6.4", "7.1"] },
    { "id": 18, "tasks": ["6.5", "7.2"] },
    { "id": 19, "tasks": ["6.6", "7.3"] },
    { "id": 20, "tasks": ["6.7", "7.4"] },
    { "id": 21, "tasks": ["6.8", "8.1"] },
    { "id": 22, "tasks": ["8.2"] },
    { "id": 23, "tasks": ["8.3"] },
    { "id": 24, "tasks": ["8.4"] },
    { "id": 25, "tasks": ["8.5", "8.6"] },
    { "id": 26, "tasks": ["8.7", "8.8", "10.1"] },
    { "id": 27, "tasks": ["10.2"] },
    { "id": 28, "tasks": ["10.3", "11.1"] },
    { "id": 29, "tasks": ["10.4", "11.2"] },
    { "id": 30, "tasks": ["11.3"] },
    { "id": 31, "tasks": ["11.4"] },
    { "id": 32, "tasks": ["11.5"] },
    { "id": 33, "tasks": ["11.6"] },
    { "id": 34, "tasks": ["11.7"] },
    { "id": 35, "tasks": ["11.8", "11.10"] },
    { "id": 36, "tasks": ["11.9", "11.11"] },
    { "id": 37, "tasks": ["11.12"] }
  ]
}
```
