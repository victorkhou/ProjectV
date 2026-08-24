# Requirements Document

## Introduction

This feature turns the existing research-lab tree system into a **doctrine commitment** and defines the framework every doctrine's combat vector plugs into. Today a tree is a bundle of flat numbers (`db.tech_bonuses`) and the only cost of changing trees is demolishing one lab. This feature makes each tree a distinct commitment with its own family of buildings and agent roles, makes switching trees require tearing down every building tied to the abandoned tree on that planet, and defines one abstract operation lifecycle that every doctrine's signature capability must implement.

This document is the **foundation** half of a re-scoping. It specifies the branch framework (catalog, affiliation, commitment, switching, dormancy, unlocks, carrier agents) plus the cross-cutting contracts (operation lifecycle, balance web, new-player protection, base integration, economy, communication, persistence, integration invariants). It specifies **no** individual combat vector.

The existing seams this feature builds on:

- **RESEARCH_TREES** (`world/constants.py`) is a controlled vocabulary of four trees (`weapons`, `defense`, `resource`, `research`), each hosted by exactly one `research_lab`-capability building. The SchemaValidator already enforces a tree-to-lab bijection, that every tree has at least one technology, and that a non-lab building declares no `research_tree`.
- **TechLabSystem** gates research on OWNING the hosting lab on the player's current planet (`owned_research_tree`), applies effects into `db.tech_bonuses`, and can already rebuild that dict from scratch from the researched set (`recompute_tech_bonuses`).
- **BuildingSystem** runs an ordered construction validation chain (`_validate_construction`: HQ prerequisite, one-HQ-per-planet, shield-generator cap, one-research-lab-per-planet, rank, deed, terrain, buildable, extractor terrain, tile occupancy, build range, combat lockout, resources), runs the active-presence construction/upgrade/repair timers, and computes cumulative investment (`get_building_investment`) for the existing `demolish` partial refund (`balance.demolish_refund_rates`, level 1 through 5, with `balance.demolish_refund_default`).
- **CombatEngine** already resolves typed damage (`physical`, `fire`, `psychic`, `blast`, `poison`), per-type resist axes, damage-over-time effects on `db.active_effects`, blast armor shred, the chip-damage floor that caps armor absorption, the rank-gap anti-ganking damper, closed-building cover rules, shields, and the single `apply_direct_hit` entry point non-equipped attackers use.
- **BombSystem** is the working precedent for a fused, placed, tile-based hostile object: a persistent world object with a countdown, a tile broadcast, a multi-tick `disarm` attempt with a success roll, an area resolution through `SyntheticWeapon` + `apply_direct_hit`, and restart recovery via `rebuild_from_world`.
- **AgentSystem** owns training, role assignment (roles map to buildings via `BUILDING_ROLE_MAP`), per-tick behavior scripts, patrol routes, agent XP/progression, reserve, and the rank-derived agent cap. Two roles (`soldier`, `medic`) exist as hidden placeholders.
- **DirectiveSystem**, **AllianceSystem**, **ShieldSystem**, **GuardCombatSystem**, **BaseElimination**, **ResourceSystem**, and the **NotificationPresenter** contract (`BaseSystem.notify` publishing structured `PLAYER_NOTIFICATION` events, never composed text) are the surrounding systems the framework must integrate with rather than duplicate.

This feature adds two new trees (`bio`, `cyber`) for a total of six, each with a new hosting lab; requires an agent as the delivery mechanism for every signature capability; and defines the balance contract that keeps the six roughly power-equivalent while remaining stylistically distinct.

## Out of Scope

The six Signature_Vectors are specified separately. Each of the following specs is a conforming implementation of the Operation Contract (Requirement 8) and of the cross-cutting contracts in Requirements 9 through 15, not a fresh design:

- `tech-tree-vector-ordnance` — Strategic_Strike for the `weapons` Branch
- `tech-tree-vector-fortification` — Trap for the `defense` Branch
- `tech-tree-vector-biowarfare` — Contagion for the `bio` Branch
- `tech-tree-vector-signals` — Intrusion for the `cyber` Branch
- `tech-tree-vector-logistics` — Convoy for the `resource` Branch
- `tech-tree-vector-recon` — Detection_Sweep for the `research` Branch

Each vector spec owns that Branch's Signature_Vector mechanics, that Branch's Doctrine_Counter, that Branch's Balance_Config fields, and that Branch's building and technology data. No vector spec may relax a contract stated in this document.

## Branch Overview

The intended catalog the data must express. Requirement 1, Requirement 2, and Requirement 9 validate the shape of this catalog; this table states the content those requirements are validating against, and is the shared vocabulary the six vector specs depend on.

| Branch (tree) | Doctrine | Hosting lab | Signature_Vector | Carrier_Agent | Universal_Counter | Doctrine_Counter |
| --- | --- | --- | --- | --- | --- | --- |
| `weapons` | Ordnance | Weapons Lab | Strategic_Strike — delayed area damage at a designated coordinate | `spotter` | Leave the marked area during the flight window; shields and walls absorb | Fortification interception reduces strike damage |
| `defense` | Fortification | Defense Lab | Trap — hidden, single-use area denial on a tile | `sapper` | Existing multi-tick disarm on a revealed trap | Recon detection sweep reveals traps before contact |
| `resource` | Logistics | Resource Lab | Convoy — interceptable cargo and agent movement, faster redeploy | `courier` | Attack the convoy object; it drops its cargo | Signals intrusion halts the depots a convoy runs between |
| `research` | Recon | Research Lab | Detection_Sweep — reveals hidden hostile state and grants early warning | `scout` | Operate outside the sweep radius | Logistics outpaces static observation |
| `bio` | Biowarfare | Biolab | Contagion — transmissible damage-over-time that spreads tile to tile | `medic` | `poison_resist` gear, passive regeneration, healing consumables | Fortification area denial kills the carriers before they close |
| `cyber` | Signals | Signals Lab | Intrusion — temporary suspension of an enemy building or agent | `infiltrator` | Purge the intrusion by holding the building's tile; kill the infiltrator | Biowarfare contamination kills infiltrators on approach |

Intended Counter_Web cycle, one advantage per Branch so no Branch is doubly countered:

`weapons` → `defense` → `bio` → `cyber` → `resource` → `research` → `weapons`

Read as: Ordnance standoff strikes beat static Fortification; Fortification area denial beats Biowarfare's need to walk carriers in; Biowarfare beats Signals by killing infiltrators on approach; Signals beats Logistics by suspending its infrastructure; Logistics beats Recon by acting faster than observation converts to a response; Recon beats Ordnance by disrupting designation and warning early.

## Glossary

- **Branch**: A technology tree together with the buildings, technologies, agent roles, and signature vector that belong to that tree. The six Branches are `weapons` (Ordnance), `defense` (Fortification), `resource` (Logistics), `research` (Recon), `bio` (Biowarfare), and `cyber` (Signals).
- **Branch_Lab**: A building declaring the existing `research_lab` capability, whose `research_tree` field names the single Branch it hosts.
- **Branch_Building**: A non-lab building whose definition declares a Branch_Affiliation.
- **Branch_Affiliation**: The data field on a building definition naming the one Branch a building belongs to. A building with no Branch_Affiliation is a Neutral_Building.
- **Neutral_Building**: A building with no Branch_Affiliation, buildable under any Branch_Commitment (every building shipped before this feature is a Neutral_Building).
- **Branch_Commitment**: The Branch a player is committed to on one planet, derived from which Branch_Lab that player owns on that planet. A player with no Branch_Lab on a planet has no Branch_Commitment there.
- **Branch_Estate**: The set of buildings a player owns on one planet whose Branch_Affiliation names a given Branch, including that Branch's Branch_Lab and including buildings under construction.
- **Branch_Dormancy**: The state of a Branch whose technologies are recorded for a player but whose Branch_Commitment is absent on the planet in question, so that Branch's bonuses and abilities are inert there.
- **Reinstatement**: Re-establishing a previously abandoned Branch_Commitment, which requires re-researching that Branch's recorded technologies at a reduced cost.
- **Signature_Vector**: The one headline offensive or utility capability a Branch grants. The six are Strategic_Strike (Ordnance: delayed area damage at a designated coordinate), Trap (Fortification: hidden single-use area denial on a tile), Contagion (Biowarfare: transmissible damage-over-time), Intrusion (Signals: temporary suspension of an enemy building or agent), Convoy (Logistics: interceptable cargo and agent movement), and Detection_Sweep (Recon: revelation of hidden hostile state).
- **Vector_Operation**: One instance of a Signature_Vector, from the request that creates it to the terminal state that ends it.
- **Operation_Kind**: The identifier naming which Signature_Vector a Vector_Operation instantiates.
- **Operation_Record**: The persisted attribute set describing one Vector_Operation.
- **Vector_System**: The per-Branch system component a Signature_Vector spec introduces — Ordnance_System, Fortification_System, Contagion_System, Intrusion_System, Logistics_System, and Detection_System. A requirement addressed to the Vector_System is a conformance obligation on each of those six components.
- **Carrier_Agent**: The agent a Vector_Operation requires in order to be performed — either assigned to the originating Branch_Building or present in the field as the delivery mechanism. Every Signature_Vector requires a Carrier_Agent.
- **Counter_Web**: The declared, data-defined set of ordered pairs stating which Branch holds a bounded advantage over which other Branch.
- **Universal_Counter**: A response to a Signature_Vector available to a player under any Branch_Commitment, including none.
- **Doctrine_Counter**: A stronger response to a Signature_Vector available only under a specific Branch_Commitment.
- **Response_Window**: The number of ticks between a target receiving notification of a hostile Vector_Operation and that Vector_Operation taking effect.
- **Branch_System**: The new system component owning Branch resolution, Branch_Commitment, Branch_Estate queries, Branch_Dormancy, the construction gates this feature adds, and the shared operation-lifecycle services the Vector_Systems consume.
- **BuildingSystem**, **TechLabSystem**, **CombatEngine**, **BombSystem**, **AgentSystem**, **EquipmentSystem**, **ShieldSystem**, **GuardCombatSystem**, **AllianceSystem**, **DirectiveSystem**, **ResourceSystem**, **DataRegistry**, **SchemaValidator**, **NotificationPresenter**, **EventBus**: The existing system components named in the Introduction.
- **Operational**: The existing building state meaning constructed, online, and not mid-upgrade, as resolved by `building_is_operational`.
- **Active_HQ_Rule**: The existing rule that a player's base is inert on a planet while that player owns no completed headquarters-capability building there.
- **Balance_Config**: The existing hot-tunable configuration loaded from `balance.yaml` into `BalanceConfig`.

## Requirements

### Requirement 1: Technology Branch Catalog

**User Story:** As a game designer, I want six named Branches defined in data with one hosting lab each, so that adding or retuning a doctrine is a data edit rather than a code change.

#### Acceptance Criteria

1. THE DataRegistry SHALL recognize exactly six Branches: `weapons`, `defense`, `resource`, `research`, `bio`, and `cyber`.
2. WHEN the DataRegistry loads the building definitions, THE SchemaValidator SHALL confirm that each of the six Branches is hosted by exactly one Branch_Lab.
3. IF two building definitions declare the same Branch in the `research_tree` field, THEN THE SchemaValidator SHALL report a validation error naming both building abbreviations and the duplicated Branch, and THE DataRegistry SHALL fail the load.
4. IF a Branch is hosted by no Branch_Lab, THEN THE SchemaValidator SHALL report a validation error naming that Branch, and THE DataRegistry SHALL fail the load.
5. WHEN the DataRegistry loads the technology definitions, THE SchemaValidator SHALL confirm that each of the six Branches has at least one technology declaring that Branch as its `tree`.
6. THE DataRegistry SHALL expose, for a given Branch, the hosting Branch_Lab abbreviation, the technologies belonging to that Branch, and the Branch_Buildings affiliated with that Branch.
7. WHEN the DataRegistry loads the definitions, THE SchemaValidator SHALL collect every Branch-related validation error across all definition files before failing the load, so that one load reports every error.

### Requirement 2: Branch Affiliation of Buildings

**User Story:** As a game designer, I want each new building to declare which Branch it belongs to, so that the commitment and dormancy rules can be applied uniformly without per-building code.

#### Acceptance Criteria

1. THE DataRegistry SHALL load an optional Branch_Affiliation field for each building definition, naming one of the six Branches.
2. WHEN a building definition omits the Branch_Affiliation field, THE DataRegistry SHALL treat that building as a Neutral_Building.
3. IF a building definition declares a Branch_Affiliation naming a value outside the six Branches, THEN THE SchemaValidator SHALL report a validation error naming the building abbreviation and the offending value, and THE DataRegistry SHALL fail the load.
4. WHERE a building definition declares the `research_lab` capability, THE SchemaValidator SHALL require the building's Branch_Affiliation to equal that building's `research_tree` value or to be absent.
5. THE DataRegistry SHALL treat every building definition that existed before this feature as a Neutral_Building, so that construction of those buildings is unaffected by Branch_Commitment.
6. THE DataRegistry SHALL expose the Branch_Affiliation of a building definition through the existing registry building lookup.
7. WHEN the DataRegistry loads the building definitions, THE SchemaValidator SHALL confirm that each of the six Branches has at least one affiliated Branch_Building beyond that Branch's Branch_Lab, so that no Branch grants a lab with no doctrine buildings.

### Requirement 3: Branch Commitment and Exclusivity

**User Story:** As a player, I want the lab I build on a planet to be the single declaration of my doctrine there, so that my strategic choice is unambiguous and visible.

#### Acceptance Criteria

1. THE Branch_System SHALL derive a player's Branch_Commitment on a planet from the Branch_Affiliation of the Branch_Lab that player owns on that planet, holding no separately stored copy of the Branch_Commitment.
2. WHEN a player owns no Branch_Lab on a planet, THE Branch_System SHALL report that player as having no Branch_Commitment on that planet.
3. WHEN a player requests construction of a Branch_Building, THE BuildingSystem SHALL permit the construction only while that player's Branch_Commitment on the target planet equals that building's Branch_Affiliation.
4. IF a player requests construction of a Branch_Building while holding no Branch_Commitment on the target planet, THEN THE BuildingSystem SHALL refuse the construction and SHALL report the Branch_Lab required to unlock that building.
5. IF a player requests construction of a Branch_Building whose Branch_Affiliation differs from that player's Branch_Commitment on the target planet, THEN THE BuildingSystem SHALL refuse the construction and SHALL report both the player's current Branch and the Branch that building requires.
6. THE BuildingSystem SHALL apply the existing one-Branch_Lab-per-planet-per-player limit to all six Branch_Labs.
7. THE Branch_System SHALL scope Branch_Commitment to one player and one planet, so that the same player may hold a different Branch_Commitment on each planet.
8. WHEN a player's Branch_Lab on a planet is destroyed, THE Branch_System SHALL report that player as having no Branch_Commitment on that planet until a Branch_Lab is completed there again.
9. THE Branch_System SHALL derive Branch_Commitment from ownership of a completed Branch_Lab independently of that Branch_Lab's Operational state, so that a temporary suspension of a Branch_Lab's behavior leaves the owner's Branch_Commitment in place.

### Requirement 4: Branch Switching and Abandonment Cost

**User Story:** As a player, I want switching doctrines to cost me real time and resources, so that choosing a Branch is a decision I think through rather than a setting I toggle.

#### Acceptance Criteria

1. IF a player requests construction of a Branch_Lab for one Branch while that player's Branch_Estate for a different Branch on the target planet contains at least one building, THEN THE BuildingSystem SHALL refuse the construction and SHALL report the count of buildings that remain in the conflicting Branch_Estate.
2. WHEN a player requests construction of a Branch_Lab for one Branch while that player's Branch_Estate for a different Branch on the target planet contains at least one building, THE BuildingSystem SHALL report the abbreviation and coordinates of each building in the conflicting Branch_Estate, so that the player knows exactly what stands between the player and the switch.
3. WHEN a player's Branch_Estate for a Branch on a planet becomes empty, THE Branch_System SHALL permit construction of any Branch_Lab on that planet, subject to the construction validations that apply to every building.
4. THE BuildingSystem SHALL apply the existing `demolish` partial refund rates to Branch_Buildings, so that abandoning a Branch returns less than the resources invested in that Branch_Estate.
5. WHEN a player demolishes a building, THE BuildingSystem SHALL report the number of buildings remaining in that building's Branch_Estate on that planet, so that progress toward a switch is measurable.
6. WHEN a hostile action destroys a building belonging to a player's Branch_Estate, THE Branch_System SHALL count that destruction toward emptying the Branch_Estate on the same terms as a demolition, so that an enemy razing an abandoned estate advances the owner's switch.
7. THE Branch_System SHALL count a Branch_Building under construction as a member of its Branch_Estate, so that a partially built building blocks a switch.
8. WHERE a player holds a Branch_Commitment, WHEN that player requests construction of a Branch_Lab for a different Branch and that player's conflicting Branch_Estate is empty, THE BuildingSystem SHALL report the count of that player's recorded technologies in the outgoing Branch that would enter Branch_Dormancy before charging the construction cost.

### Requirement 5: Branch Dormancy and Reinstatement

**User Story:** As a player, I want abandoning a Branch to suspend its benefits rather than erase my research record, so that the penalty for switching is rebuilding and re-committing rather than losing my history.

#### Acceptance Criteria

1. WHILE a player holds no Branch_Commitment for a Branch on the planet the player occupies, THE TechLabSystem SHALL exclude that Branch's technology effects from the bonuses applied to that player.
2. WHEN a player's Branch_Commitment on the occupied planet changes, THE TechLabSystem SHALL recompute that player's applied technology bonuses from the player's recorded technologies filtered to the current Branch_Commitment.
3. THE TechLabSystem SHALL retain a player's record of researched technologies for a Branch while that Branch is in Branch_Dormancy for that player.
4. WHILE a Branch_Building's owner holds no Branch_Commitment matching that building's Branch_Affiliation on the building's planet, THE Branch_System SHALL report that building as non-Operational, so that the building performs no capability behavior.
5. WHEN a player completes a Branch_Lab for a Branch the player previously abandoned, THE TechLabSystem SHALL require a Reinstatement research job for each of that player's recorded technologies in that Branch before the effects of those technologies apply again.
6. THE TechLabSystem SHALL charge a Reinstatement research job a resource cost equal to the technology's defined resource cost multiplied by the Branch_Reinstatement_Cost_Fraction from Balance_Config, defaulting to 0.5, and SHALL charge a research duration equal to the technology's defined duration multiplied by the same fraction.
7. WHILE a Reinstatement research job for a technology is incomplete, THE TechLabSystem SHALL exclude that technology's effect from the player's applied bonuses.
8. THE TechLabSystem SHALL apply the existing rank gate of a technology to that technology's Reinstatement research job.
9. WHEN a player's Branch_Lab is destroyed and that player completes a Branch_Lab hosting the same Branch on the same planet, THE TechLabSystem SHALL restore that Branch's technology effects without requiring a Reinstatement research job, so that losing a lab to an attack is a repair cost rather than a research reset.
10. WHILE a player's Branch_Lab is non-Operational for a reason other than destruction, THE TechLabSystem SHALL keep that Branch's technology effects applied to that player, so that suspending a lab's behavior withholds the lab's function rather than the Branch's researched bonuses.

### Requirement 6: Technology-Gated Branch Building Unlocks

**User Story:** As a player, I want a Branch's interesting buildings to be earned through research inside that Branch, so that progressing a tree unlocks new things to do rather than only larger numbers.

#### Acceptance Criteria

1. THE DataRegistry SHALL load an optional unlocking-technology field for each building definition, naming one technology key.
2. WHEN a player requests construction of a building whose definition names an unlocking technology, THE BuildingSystem SHALL permit the construction only while that player's record of researched technologies contains that technology and that technology's effects are applied.
3. IF a player requests construction of a building whose unlocking technology that player has not researched, THEN THE BuildingSystem SHALL refuse the construction and SHALL report the name of the required technology and the Branch that hosts that technology.
4. IF a building definition names an unlocking technology absent from the technology definitions, THEN THE SchemaValidator SHALL report a validation error naming the building abbreviation and the missing technology key, and THE DataRegistry SHALL fail the load.
5. IF a building definition names an unlocking technology whose `tree` differs from that building's Branch_Affiliation, THEN THE SchemaValidator SHALL report a validation error naming the building abbreviation, the technology key, and both Branch values, and THE DataRegistry SHALL fail the load.
6. THE BuildingSystem SHALL evaluate the unlocking-technology gate in addition to the existing level, deed, HQ, terrain, tile-occupancy, build-range, combat-lockout, and resource validations.
7. THE DataRegistry SHALL define each Branch's Signature_Vector building behind an unlocking technology of that Branch, so that a Branch_Lab alone grants no Signature_Vector.

### Requirement 7: Carrier Agent Requirement and Branch Agent Roles

**User Story:** As a player, I want my agents to be the ones who deliver my doctrine's power, so that my agents matter beyond harvesting and so that every enemy operation has a body I can kill.

#### Acceptance Criteria

1. THE Branch_System SHALL require a Carrier_Agent for every Vector_Operation, so that no Vector_Operation resolves without an agent.
2. THE DataRegistry SHALL record, for each Operation_Kind, the one agent role that Operation_Kind requires as its Carrier_Agent.
3. IF a player requests a Vector_Operation while owning no Carrier_Agent of the required role in an eligible state, THEN THE Vector_System SHALL refuse the operation and SHALL report the required agent role.
4. THE AgentSystem SHALL support the role `spotter` for the Ordnance Branch, `sapper` for the Fortification Branch, `medic` for the Biowarfare Branch, `infiltrator` for the Signals Branch, `courier` for the Logistics Branch, and `scout` for the Recon Branch.
5. THE AgentSystem SHALL treat a Carrier_Agent as eligible while that agent is alive, assigned to the role the Operation_Kind requires, active outside reserve, and free of incapacitation.
6. WHEN a player requests assignment of an agent to a role introduced by this feature, THE AgentSystem SHALL permit the assignment only while that player's Branch_Commitment on the agent's planet equals the Branch that role belongs to.
7. IF a player requests assignment of an agent to a role belonging to a Branch other than that player's Branch_Commitment on the agent's planet, THEN THE AgentSystem SHALL refuse the assignment and SHALL report the Branch that role requires.
8. WHEN a player's Branch_Commitment on a planet is absent, THE AgentSystem SHALL set every agent of that player holding a role introduced by this feature on that planet to the unassigned state, so that a dormant Branch commands no agents.
9. THE AgentSystem SHALL apply the existing rank-derived agent cap without change, so that committing to a Branch grants access to new roles rather than additional agent slots.
10. THE AgentSystem SHALL award agent experience for each Vector_Operation a Carrier_Agent completes, drawing the amount from a Balance_Config field named for that Operation_Kind.
11. THE SchemaValidator SHALL confirm that each of the six roles introduced by this feature belongs to exactly one Branch and that each of the six Branches owns exactly one such role, and SHALL report a validation error naming any role or Branch failing either condition.

### Requirement 8: The Operation Contract

**User Story:** As a developer, I want one lifecycle every doctrine's signature capability implements, so that each vector is a conforming implementation with predictable validation, cost, notification, persistence, and cancellation rather than a fresh design.

#### Acceptance Criteria

1. THE Branch_System SHALL define one Vector_Operation lifecycle comprising the states Pending, Suspended, Resolved, Expired, Cancelled, and Discarded, and THE Vector_System SHALL record each Vector_Operation in exactly one of those states.
2. THE Vector_System SHALL treat Resolved, Expired, Cancelled, and Discarded as terminal states, advancing a Vector_Operation no further after that Vector_Operation enters a terminal state.
3. WHEN a player requests a Vector_Operation, THE Vector_System SHALL evaluate the request in the order collaborator availability, Branch_Commitment match, originating building ownership and Operational state and Active_HQ_Rule, unlocking-technology gate, Carrier_Agent eligibility, target validity, cooldown, in-flight cap, and resource sufficiency, and SHALL refuse the request at the first failing check.
4. WHEN a Vector_System refuses a requested Vector_Operation, THE Vector_System SHALL report the failing check and the value required to pass that check, and SHALL leave every player-owned and world-owned state unchanged.
5. WHEN every check of a requested Vector_Operation passes, THE Vector_System SHALL charge that Operation_Kind's resource cost before the Vector_Operation enters the Pending state.
6. IF a Vector_Operation fails to enter the Pending state after that Vector_Operation's resource cost is charged, THEN THE Vector_System SHALL restore the full charged amount to the requesting player, so that no Vector_Operation both charges and fails.
7. WHEN a Vector_System places a hostile Vector_Operation in the Pending state, THE Vector_System SHALL notify each target player with the Operation_Kind, the originating player name, the affected coordinate, and the number of ticks remaining until the Vector_Operation takes effect.
8. THE Vector_System SHALL set the Response_Window of a hostile Vector_Operation to at least the Minimum_Response_Window_Ticks from Balance_Config, defaulting to 5 ticks, measured from the target's notification to the Vector_Operation taking effect.
9. WHEN a tick elapses, THE Vector_System SHALL advance each Pending Vector_Operation that Vector_System owns by one tick.
10. WHEN a tick elapses, THE Vector_System SHALL isolate the advancement of each Vector_Operation so that a failure advancing one Vector_Operation leaves the remaining Vector_Operations advanced, and SHALL log the failure identifying the Operation_Kind and the affected Vector_Operation.
11. WHEN a Pending Vector_Operation reaches the tick at which that Vector_Operation takes effect, THE Vector_System SHALL apply that Vector_Operation's effect and SHALL move that Vector_Operation to the Resolved state.
12. WHEN a Vector_Operation resolves, THE Vector_System SHALL notify each player who owns an affected entity and each player occupying an affected tile, reporting the Operation_Kind and the affected coordinate.
13. WHEN a Vector_Operation's bounded lifetime elapses, THE Vector_System SHALL move that Vector_Operation to the Expired state, SHALL restore each entity that Vector_Operation suspended to the state that entity held before the suspension, and SHALL notify that Vector_Operation's owner and each affected entity's owner of the expiry.
14. WHILE a Vector_Operation's Carrier_Agent is incapacitated or in reserve, THE Vector_System SHALL move that Vector_Operation to the Suspended state and SHALL advance that Vector_Operation no further.
15. WHEN a Suspended Vector_Operation's Carrier_Agent returns to an eligible state, THE Vector_System SHALL move that Vector_Operation to the Pending state with the remaining ticks that Vector_Operation held on suspension, so that suspension delays a Vector_Operation rather than restarting it.
16. WHEN a Vector_Operation's Carrier_Agent is killed, THE Vector_System SHALL move that Vector_Operation to the Cancelled state and SHALL notify that Vector_Operation's owner of the cancellation.
17. WHEN a Vector_Operation's originating building becomes non-Operational or is destroyed, THE Vector_System SHALL move that Vector_Operation to the Cancelled state and SHALL notify that Vector_Operation's owner of the cancellation.
18. WHEN a Vector_Operation's owner loses the Branch_Commitment that Vector_Operation requires on that Vector_Operation's planet, THE Vector_System SHALL move that Vector_Operation to the Suspended state, so that a dormant Branch resolves no operations.
19. THE Vector_System SHALL enforce a cooldown per originating building per Operation_Kind, drawn from a Balance_Config field named for that Operation_Kind, and SHALL report the remaining cooldown ticks when a request arrives before that cooldown elapses.
20. THE Vector_System SHALL bound the number of simultaneous non-terminal Vector_Operations one player holds of one Operation_Kind on one planet to a cap drawn from a Balance_Config field named for that Operation_Kind, and SHALL report the current count and the cap when a request exceeds that cap.
21. THE Vector_System SHALL record, for each non-terminal Vector_Operation, an Operation_Record containing the Operation_Kind, the owning player, the originating building, the Carrier_Agent, the target coordinate, the target entity, the remaining ticks, the effect magnitude, the effect radius, and the lifecycle state.
22. WHEN the server restarts, THE Vector_System SHALL rebuild the in-memory tracking of every non-terminal Vector_Operation from the persisted Operation_Records, so that each rebuilt Vector_Operation resumes advancing on the tick loop.
23. THE Vector_System SHALL apply the effect of a Vector_Operation to an entity through the CombatEngine single-hit entry point or through the existing active-effects list, attributing the effect to the owning player, so that no Vector_Operation applies damage outside the existing damage pipeline.
24. THE Vector_System SHALL return an outcome value naming the resulting lifecycle state for every Vector_Operation request, so that a caller reads the result rather than inferring the result.

### Requirement 9: Counter Web and Cross-Branch Balance

**User Story:** As a player, I want every doctrine to be beatable and every doctrine to be viable, so that my choice of Branch expresses my style rather than deciding whether I can compete.

#### Acceptance Criteria

1. THE DataRegistry SHALL load a Counter_Web defining, for each of the six Branches, the Branches that Branch holds an advantage over.
2. THE SchemaValidator SHALL confirm that each of the six Branches holds an advantage over at least one Branch and that at least one Branch holds an advantage over each of the six Branches, and SHALL report a validation error naming any Branch failing either condition.
3. THE SchemaValidator SHALL confirm that no Branch holds an advantage over more than two Branches, and SHALL report a validation error naming any Branch exceeding that count.
4. THE Branch_System SHALL express a Counter_Web advantage as a bounded numeric multiplier within the range 1.0 through the Counter_Advantage_Cap from Balance_Config, defaulting to 1.35, or as a Response_Window reduction, so that an advantage changes a magnitude or a timing rather than granting immunity.
5. THE Branch_System SHALL apply at most one Counter_Web advantage multiplier to one Vector_Operation, so that advantage multipliers do not compound.
6. THE Vector_System SHALL provide at least one Universal_Counter for that Vector_System's Signature_Vector, available to a player holding any Branch_Commitment and to a player holding no Branch_Commitment.
7. THE Vector_System SHALL provide at least one Doctrine_Counter for that Vector_System's Signature_Vector, available to a player holding the Branch_Commitment the Counter_Web names as holding an advantage over that Signature_Vector's Branch.
8. THE Vector_System SHALL set the effect of every Vector_Operation on a building to either a temporary suspension of that building's behavior or damage routed through the existing hit-point and shield pipeline, so that no Vector_Operation deletes a building outright and no Vector_Operation transfers ownership.
9. THE SchemaValidator SHALL compute each Branch's investment score as the sum, over the build costs of that Branch's Branch_Lab and Branch_Buildings and the resource costs of that Branch's technologies, of each resource amount multiplied by that resource's weight from the existing Balance_Config resource-weight map.
10. THE SchemaValidator SHALL confirm that each Branch's investment score falls within the Branch_Cost_Parity_Tolerance fraction from Balance_Config of the mean investment score across the six Branches, and SHALL report a validation error naming each Branch outside that tolerance together with that Branch's score and the mean.
11. THE Branch_System SHALL apply the existing chip-damage floor, typed-resist axes, permanent-bonus caps, and shield absorption to every damage source this feature introduces, so that no new vector bypasses the existing damage-balance guardrails.
12. THE SchemaValidator SHALL confirm that the Counter_Web names only the six Branches, and SHALL report a validation error naming any value outside the six Branches.

### Requirement 10: New-Player Protection and Escalation Limits

**User Story:** As a new player, I want a veteran's doctrine weapons to be survivable, so that I keep playing long enough to commit to a doctrine of my own.

#### Acceptance Criteria

1. THE Branch_System SHALL apply the existing rank-gap damage damper to every damage source this feature introduces, so that a much-higher-ranked unprovoked attacker deals reduced damage through a new vector.
2. THE Branch_System SHALL apply the existing rank-gap experience and loot reduction to kills resulting from a Vector_Operation, so that a new vector is no farming route.
3. THE Vector_System SHALL attribute a kill resulting from a Vector_Operation to the Vector_Operation's owning player, so that the existing kill accounting records the responsible player rather than the delivery mechanism.
4. IF a player requests a hostile Vector_Operation against a target player whose level is below the New_Player_Vector_Shield_Level from Balance_Config, THEN THE Vector_System SHALL refuse the operation and SHALL report the level at which the target becomes a valid Vector_Operation target.
5. THE SchemaValidator SHALL confirm that every Branch_Building declares a level requirement at or above the level requirement of that Branch's Branch_Lab, and SHALL report a validation error naming the building abbreviation and both level requirements, so that no Branch content precedes the existing lab gate.
6. THE Branch_System SHALL bound the number of hostile Vector_Operations one player may resolve against one target player within the Escalation_Window_Ticks from Balance_Config to the Escalation_Cap from Balance_Config, and SHALL report the remaining ticks when a request exceeds that cap.
7. THE Branch_System SHALL apply every Vector_Operation gate to alliance members and allies on the same terms as to unaffiliated players, so that alliance membership grants no exemption from escalation limits.
8. WHERE a player holds no Branch_Commitment, THE Branch_System SHALL leave that player's access to melee combat, ranged combat, bombs, walls, turrets, shields, and every Neutral_Building unchanged, so that declining to commit remains a playable state.

### Requirement 11: Base Defense, NPC Base, and Alliance Integration

**User Story:** As a player, I want doctrine buildings to be part of my base rather than a separate game, so that the defenses, alliances, and enemies I already understand still apply.

#### Acceptance Criteria

1. THE ShieldSystem SHALL project shields onto Branch_Buildings on the same terms as onto Neutral_Buildings.
2. THE GuardCombatSystem SHALL defend Branch_Buildings on the same terms as Neutral_Buildings.
3. THE Branch_System SHALL apply the existing Active_HQ_Rule to every Branch_Building, so that a player with no completed headquarters on a planet operates no Branch_Building there.
4. WHEN a base elimination removes a player's or an NPC base's buildings, THE Vector_System SHALL move every non-terminal Vector_Operation originating from a removed building to the Cancelled state and SHALL notify that Vector_Operation's owner.
5. THE DataRegistry SHALL permit an NPC base template to declare a Branch and to include that Branch's Branch_Buildings, so that players can practice against each Signature_Vector before facing a player using that vector.
6. WHEN an NPC base holding a Branch_Building requests a Vector_Operation, THE Vector_System SHALL apply the same notification, Response_Window, and Universal_Counter rules that apply to a player-originated Vector_Operation.
7. THE AllianceSystem SHALL leave alliance perk categories unchanged, so that no alliance perk grants a Signature_Vector.
8. WHERE two players are allied, WHEN one player requests a Vector_Operation in support of the other player, THE Vector_System SHALL perform the operation only while the supported player has consented to receive that support, and SHALL report the missing consent when consent is absent.
9. IF a player requests a hostile Vector_Operation naming an allied entity as the target, THEN THE Vector_System SHALL refuse the operation and SHALL report the alliance that protects that target.
10. WHEN a Vector_Operation applies an area effect, THE Vector_System SHALL apply that effect to each entity within the affected area including entities owned by the originating player and entities allied to the originating player, so that an indiscriminate area effect remains indiscriminate.
11. WHEN a player leaves an alliance, THE Branch_System SHALL revoke that player's outstanding support consents and target-sharing consents with that alliance's members.

### Requirement 12: Economy Costs and Resource Sinks

**User Story:** As a player, I want doctrine power to cost resources every time I use it, so that my economy and my military are the same decision.

#### Acceptance Criteria

1. THE DataRegistry SHALL define a per-use resource cost for every Operation_Kind in Balance_Config.
2. THE Vector_System SHALL charge either the whole resource cost of a Vector_Operation or none of that cost, so that a partial charge leaves no player short of resources for a Vector_Operation that never ran.
3. IF a player holds insufficient resources for a Vector_Operation, THEN THE Vector_System SHALL refuse the operation and SHALL report the existing have-and-need breakdown for the missing resources.
4. THE DataRegistry SHALL define the resource cost of each Branch's Signature_Vector building chain to include at least one of the late-game resources `Circuits`, `Energy`, or `Nexium`, so that Signature_Vector access depends on economic reach.
5. THE SchemaValidator SHALL confirm that each Branch's Signature_Vector building chain names at least one late-game resource, and SHALL report a validation error naming any Branch failing that condition.
6. WHERE a Vector_Operation originates from an NPC base, THE Vector_System SHALL apply no resource charge, so that NPC practice targets require no NPC economy.
7. THE ResourceSystem SHALL leave existing harvest yields, extractor output, and storage capacities unchanged for a player holding any Branch_Commitment other than `resource`, so that committing to a combat Branch costs economic output only through the resources that Branch consumes.
8. THE Branch_System SHALL charge no recurring upkeep for a Branch_Building beyond the existing repair cost, so that owning a Branch_Estate imposes a rebuild-and-use cost rather than a passive drain.

### Requirement 13: Player-Facing Communication and Discoverability

**User Story:** As a player, I want to understand what committing to a doctrine gives me and what it costs before I commit, so that I am not punished for a decision I could not see.

#### Acceptance Criteria

1. WHEN a player requests the technology view, THE TechLabSystem SHALL report the player's Branch_Commitment on the occupied planet, that Branch's Signature_Vector, the researched technologies of that Branch, and the technologies of that Branch available to research.
2. WHEN a player requests the technology view while holding recorded technologies in a Branch in Branch_Dormancy, THE TechLabSystem SHALL report each dormant Branch, the count of recorded technologies in that Branch, and the Reinstatement cost fraction.
3. WHEN a player requests a Branch overview, THE Branch_System SHALL report, for each of the six Branches, that Branch's hosting Branch_Lab, Signature_Vector, agent role, the Branches that Branch holds an advantage over, and the Branches that hold an advantage over that Branch.
4. WHEN a player requests construction of a Branch_Lab that would abandon a Branch, THE BuildingSystem SHALL report the buildings that must be removed and the technologies that would enter Branch_Dormancy before charging any resources.
5. THE Branch_System SHALL emit every player-facing message this feature introduces as a structured notification through the existing EventBus notification contract, composing no message text inside a system component.
6. THE NotificationPresenter SHALL render a notification kind for each Vector_Operation lifecycle transition that reaches a player, covering the Pending, Suspended, Resolved, Expired, Cancelled, and Discarded states.
7. THE DirectiveSystem SHALL include at least one directive step introducing the Branch commitment decision, positioned at or after the existing Branch_Lab level and deed gate.
8. THE Branch_System SHALL name every notification kind this feature introduces in the registry the NotificationPresenter reads, so that an unrendered kind is a load-time error rather than a runtime blank.

### Requirement 14: Persistence and Restart Recovery

**User Story:** As a player, I want an operation I launched to survive a server restart, so that a restart neither erases my investment nor leaves a hazard stuck forever.

#### Acceptance Criteria

1. THE Vector_System SHALL persist each non-terminal Vector_Operation's Operation_Record on a durable owner, choosing the world object the Vector_Operation acts through or the entity the Vector_Operation is attached to.
2. FOR ALL Operation_Records, writing the Operation_Record and then rebuilding the Vector_Operation after a restart SHALL produce a Vector_Operation whose Operation_Kind, owner, target, remaining ticks, effect magnitude, and lifecycle state equal the values written.
3. FOR ALL Operation_Records, rebuilding from the same persisted state twice SHALL produce the same set of tracked Vector_Operations as rebuilding once, so that a repeated rebuild duplicates no Vector_Operation.
4. IF a persisted Operation_Record references a target, originating building, owning player, or Carrier_Agent that no longer exists, THEN THE Vector_System SHALL move that Vector_Operation to the Discarded state and SHALL log the discard identifying the Operation_Kind and the missing reference.
5. WHEN a rebuild step fails for one Operation_Record, THE Vector_System SHALL rebuild the remaining Operation_Records and SHALL log the failure identifying the Operation_Kind, so that one corrupt record leaves the rest recovered.
6. THE Branch_System SHALL derive Branch_Commitment and Branch_Estate membership from the owned buildings at query time, so that no restart can desynchronize a Branch_Commitment from the buildings that define it.
7. WHEN a Vector_System writes a persistent container of Vector_Operation state, THE Vector_System SHALL read the container, modify a copy, and write the whole container back, so that the write persists.
8. THE Vector_System SHALL read every persisted attribute by value and SHALL treat an absent attribute as the documented default for that attribute.

### Requirement 15: System Integration Invariants

**User Story:** As a developer, I want the new systems to follow the conventions the existing systems already follow, so that this feature is maintainable alongside the code it extends.

#### Acceptance Criteria

1. THE Branch_System and every Vector_System SHALL import no game-framework module at module scope, receiving every framework-dependent collaborator through injection at the composition root.
2. WHERE a collaborator required for a Vector_Operation is not injected, THE Vector_System SHALL refuse that Vector_Operation and SHALL log the missing collaborator, so that an unwired system degrades to a refusal rather than an error.
3. WHEN a command layer invokes an operation this feature introduces, THE Branch_System and the Vector_System SHALL return an outcome value for every input, raising no exception into the command layer.
4. THE Branch_System SHALL resolve every building capability check and every Branch_Affiliation lookup through the injected DataRegistry, so that tests run without a global registry.
5. THE Branch_System SHALL hold the single write path for Branch-related persistent player state, so that one component owns each attribute this feature introduces.
6. THE DataRegistry SHALL validate every Balance_Config field this feature introduces for type and range, and SHALL fail the load with a collected error report when a field is invalid.
7. WHEN the Balance_Config is reloaded, THE Branch_System and every Vector_System SHALL read the updated values on the next Vector_Operation, so that every value this feature introduces is hot-tunable.
8. THE Branch_System SHALL expose the Branch_Commitment, Branch_Estate, Branch_Dormancy, Carrier_Agent eligibility, cooldown, in-flight cap, escalation cap, and Counter_Web queries as services a Vector_System consumes, so that a Vector_System reimplements no framework rule.
9. THE Vector_System SHALL register its per-tick step through the existing tick dispatch, so that no Vector_System drives its own timer.
