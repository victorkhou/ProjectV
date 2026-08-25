# Requirements Document

> ## Delivery Status — READ FIRST
>
> **This document is the parent spec. Two thirds of it has shipped, and the rest
> has been split into six child specs. Do not implement from this document
> directly.**
>
> The framework layer was carved out of this spec and delivered as
> **`tech-tree-branch-foundation`** (its own requirements, design, and tasks
> live in `.kiro/specs/tech-tree-branch-foundation/`). That spec restates the
> requirements below as its own R1–R15 and is the authority on them; where the
> two disagree, the foundation's wording is what shipped.
>
> ### Delivered by `tech-tree-branch-foundation`
>
> | This document | Delivered as | What shipped |
> | --- | --- | --- |
> | R1 Technology Branch Catalog | foundation R1 | Six Branches, lab bijection, `branches.yaml` catalog, collected load-time validation |
> | R2 Branch Affiliation of Buildings | foundation R2 | `branch` field, Neutral_Building default, lab/affiliation agreement rule |
> | R3 Branch Commitment and Exclusivity | foundation R3 | Commitment derived from the owned lab, per-planet, one lab per planet |
> | R4 Branch Switching and Abandonment Cost | foundation R4 | Estate queries, switch gate, blocking-building report, demolish progress |
> | R5 Branch Dormancy and Reinstatement | foundation R5 | Dormancy overlay, record retention, Reinstatement jobs at the cost fraction |
> | R6 Technology-Gated Branch Building Unlocks | foundation R6 | `unlock_technology` field and gate, researched-AND-applied semantics |
> | R7 Carrier Agent Requirement and Branch Agent Roles | foundation R7 | Six roles, commitment gate, dormancy release, carrier eligibility, operation XP |
> | R14 Cross-Branch Balance and the Counter Web | foundation R9 | Counter_Web, bounded non-compounding multiplier, investment-score parity rule |
> | R15 New-Player Protection and Escalation Limits | foundation R10 | Level shield, escalation cap and window, rank-gap damper inheritance |
> | R16 Base Defense, NPC Base, and Alliance Integration | foundation R11 | Active_HQ_Rule overlay, base-elimination cancellation, support consent, allied-target refusal |
> | R17 Economy Costs and Resource Sinks | foundation R12 | Per-use cost per Operation_Kind, charge/refund, have-and-need breakdown |
> | R18 Player-Facing Communication and Discoverability | foundation R13 | Structured notifications, presenter coverage, technology view, Branch overview, directive step |
> | R19 Persistence and Restart Recovery | foundation R14 | `OperationRecord` persistence, idempotent restart rebuild, discard-on-missing-reference |
> | R20 System Integration Invariants | foundation R15 | Framework-free modules, degrade-to-refusal, no-raise outcomes, read-copy-write, hot-tunable knobs |
>
> The foundation also shipped the abstract lifecycle every vector inherits:
> the `OperationDriver` contract (six states, four terminal, the ordered
> nine-check validation chain, charge-then-refund, the Response_Window floor,
> per-tick isolation, suspend/resume, every cancellation trigger, the
> cooldown/in-flight/escalation ledgers, persistence, and the restart rebuild),
> the `BranchSystem` shared services, and the per-tick `vector_operations`
> fan-out. It ships with **no Vector_System registered**, so the operation half
> is inert until a child spec registers one.
>
> ### Remaining — the six Signature_Vectors, one child spec each
>
> | This document | Child spec | Status |
> | --- | --- | --- |
> | R8 Ordnance — Strategic Strike | `tech-tree-vector-ordnance` | Requirements drafted |
> | R10 Biowarfare — Contagion and Cures | `tech-tree-vector-biowarfare` | Requirements drafted |
> | R11 Signals — Intrusion and Electronic Warfare | `tech-tree-vector-signals` | Requirements drafted |
> | R9 Fortification — Traps, Area Denial, and Interception | `tech-tree-vector-fortification` | Not started |
> | R12 Logistics — Convoys, Transport, and Redeployment | `tech-tree-vector-logistics` | Not started |
> | R13 Recon — Detection, Counter-Intelligence, and Early Warning | `tech-tree-vector-recon` | Not started |
>
> The first three are the vectors with no dependency on another vector. The
> last three each consume something the first three produce: Fortification
> intercepts an in-flight Strategic_Strike (R9.10), Recon reveals Traps,
> Intrusions, Designations, Convoys, and infiltrators (R13.1), and Logistics'
> depots are what Signals suspends. Building them in that order means no child
> spec has to stub a sibling.
>
> Each child spec **inherits** the framework above rather than restating it, so
> a child's requirements cover only what the foundation cannot know: the five
> `OperationDriver` hooks, that vector's own gates and effect, its
> Universal_Counter and Doctrine_Counter, and its own Balance_Config knobs.

## Introduction

This feature turns the existing research-lab tree system into a **doctrine commitment**: the lab a player builds on a planet decides not only which permanent stat bonuses they may research, but which *combat vector* and which *agent roles* they gain access to. Today a tree is a bundle of flat numbers (`db.tech_bonuses`) and the only cost of changing trees is demolishing one lab. This feature makes each tree a distinct way to fight, gives each tree its own family of buildings and agent roles, and makes switching trees require tearing down every building tied to the abandoned tree on that planet.

The existing seams this feature builds on:

- **RESEARCH_TREES** (`world/constants.py`) is a controlled vocabulary of four trees (`weapons`, `defense`, `resource`, `research`), each hosted by exactly one `research_lab`-capability building. The SchemaValidator already enforces a tree-to-lab bijection, that every tree has at least one technology, and that a non-lab building declares no `research_tree`.
- **TechLabSystem** gates research on OWNING the hosting lab on the player's current planet (`owned_research_tree`), applies effects into `db.tech_bonuses`, and can already rebuild that dict from scratch from the researched set (`recompute_tech_bonuses`).
- **BuildingSystem** validates construction (HQ prerequisite, one-lab-per-planet, level/deed gates, terrain, tile occupancy, build range, combat lockout, resources), runs the active-presence construction/upgrade/repair timers, and computes cumulative investment for the existing `demolish` partial refund (40% at level 1 rising to 80% at level 5).
- **CombatEngine** already resolves typed damage (`physical`, `fire`, `psychic`, `blast`, `poison`), per-type resist axes, damage-over-time effects on `db.active_effects`, blast armor shred, the chip-damage floor that caps armor absorption, the rank-gap anti-ganking damper, closed-building cover rules, shields, and the single `apply_direct_hit` entry point non-equipped attackers use.
- **BombSystem** is the working precedent for a fused, placed, tile-based hostile object: a persistent world object with a countdown, a tile broadcast, a multi-tick `disarm` attempt with a success roll, an area resolution through `SyntheticWeapon` + `apply_direct_hit`, and restart recovery via `rebuild_from_world`.
- **AgentSystem** owns training, role assignment (roles map to buildings via `BUILDING_ROLE_MAP`), per-tick behavior scripts, patrol routes, agent XP/progression, reserve, and the rank-derived agent cap. Two roles (`soldier`, `medic`) exist as hidden placeholders.
- **DirectiveSystem**, **AllianceSystem**, **ShieldSystem**, **GuardCombatSystem**, **BaseElimination**, **ResourceSystem**, and the **NotificationPresenter** contract (`BaseSystem.notify` publishing structured `PLAYER_NOTIFICATION` events, never composed text) are the surrounding systems each branch must integrate with rather than duplicate.

This feature adds two new trees (`bio`, `cyber`) for a total of six, gives every tree a signature combat vector, requires an agent as the delivery mechanism for every signature vector, and defines the balance contract that keeps the six roughly power-equivalent while remaining stylistically distinct.

## Branch Overview

The intended catalog the data must express. Requirement 1 and Requirement 14 validate the shape of this catalog; this table states the content those requirements are validating against.

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
- **Signature_Vector**: The one headline offensive or utility capability a Branch grants: Strategic_Strike (Ordnance), Trap (Fortification), Contagion (Biowarfare), Intrusion (Signals), Convoy (Logistics), and Detection_Sweep (Recon).
- **Carrier_Agent**: The agent an operation requires in order to be performed — either assigned to the originating Branch_Building or present in the field as the delivery mechanism. Every Signature_Vector requires a Carrier_Agent.
- **Strategic_Strike**: An Ordnance operation that damages an area around a designated coordinate on the player's current planet after a flight delay.
- **Designation**: A record naming a target coordinate, produced by a `spotter` Carrier_Agent or by a Detection_Sweep, that a Strategic_Strike consumes.
- **Trap**: A persistent, initially hidden, owner-placed world object on a tile that resolves an area effect when a non-allied entity enters that tile.
- **Contagion**: A transmissible damage-over-time effect that can pass from a carrying entity to another entity sharing a tile, at a strength that decays with each transmission.
- **Intrusion**: A Signals effect planted on an enemy building that suspends that building's capability behavior for a bounded number of ticks.
- **Convoy**: A Logistics operation in which a `courier` Carrier_Agent moves cargo or agents between two of the owning player's buildings as an interceptable world object.
- **Detection_Sweep**: A Recon operation that reveals hidden hostile state (Traps, Intrusions, in-flight Strategic_Strikes, infiltrating agents) within a radius.
- **Counter_Web**: The declared, data-defined set of ordered pairs stating which Branch holds a bounded advantage over which other Branch.
- **Universal_Counter**: A response to a Signature_Vector available to a player under any Branch_Commitment, including none.
- **Doctrine_Counter**: A stronger response to a Signature_Vector available only under a specific Branch_Commitment.
- **Response_Window**: The number of ticks between a target receiving notification of a hostile operation and that operation taking effect.
- **Branch_System**: The new system component owning Branch resolution, Branch_Commitment, Branch_Estate queries, Branch_Dormancy, and the construction gates this feature adds.
- **Ordnance_System**, **Fortification_System**, **Contagion_System**, **Intrusion_System**, **Logistics_System**, **Detection_System**: The new per-Branch system components, one per Branch, each owning that Branch's Signature_Vector and that Branch's Doctrine_Counter.
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

1. THE Branch_System SHALL require a Carrier_Agent for every Signature_Vector operation, so that no Signature_Vector operation resolves without an agent.
2. IF a player requests a Signature_Vector operation while no Carrier_Agent of the role that operation requires is assigned and non-incapacitated, THEN THE originating Branch system SHALL refuse the operation and SHALL report the required agent role.
3. THE AgentSystem SHALL support the role `spotter` for the Ordnance Branch, `sapper` for the Fortification Branch, `medic` for the Biowarfare Branch, `infiltrator` for the Signals Branch, `courier` for the Logistics Branch, and `scout` for the Recon Branch.
4. WHEN a player requests assignment of an agent to a role introduced by this feature, THE AgentSystem SHALL permit the assignment only while that player's Branch_Commitment on the agent's planet equals the Branch that role belongs to.
5. IF a player requests assignment of an agent to a role belonging to a Branch other than that player's Branch_Commitment on the agent's planet, THEN THE AgentSystem SHALL refuse the assignment and SHALL report the Branch that role requires.
6. WHEN a player's Branch_Commitment on a planet is absent, THE AgentSystem SHALL set every agent of that player holding a role introduced by this feature on that planet to the unassigned state, so that a dormant Branch commands no agents.
7. THE AgentSystem SHALL apply the existing rank-derived agent cap without change, so that committing to a Branch grants access to new roles rather than additional agent slots.
8. THE AgentSystem SHALL award agent experience for each Signature_Vector operation a Carrier_Agent completes, drawing the amount from a Balance_Config field named for that operation.
9. WHILE a Carrier_Agent is incapacitated, in reserve, or dead, THE originating Branch system SHALL suspend the operations that Carrier_Agent delivers.
10. WHEN a Carrier_Agent performing a Signature_Vector operation in the field is killed, THE originating Branch system SHALL cancel that operation and SHALL notify the operation's owner of the cancellation.

### Requirement 8: Ordnance Branch — Strategic Strike

**User Story:** As an Ordnance player, I want to strike a distant enemy position from my base, so that I can break a fortified position I cannot assault directly.

#### Acceptance Criteria

1. WHERE a player holds the `weapons` Branch_Commitment, WHILE that player occupies an Operational Strategic_Strike building the player owns, WHEN that player requests a Strategic_Strike against a Designation, THE Ordnance_System SHALL schedule a Strategic_Strike resolving at the designated coordinate after the Strike_Flight_Ticks from Balance_Config.
2. THE Ordnance_System SHALL require a Designation naming a coordinate on the planet the originating building occupies, so that a Strategic_Strike is a planet-scale operation rather than a cross-planet operation.
3. THE Ordnance_System SHALL accept a Designation produced by a `spotter` Carrier_Agent positioned within the Designation_Radius from Balance_Config of the designated coordinate, or produced by a Detection_Sweep, so that a Strategic_Strike requires observation of the target.
4. IF a player requests a Strategic_Strike against a coordinate for which no Designation is held, THEN THE Ordnance_System SHALL refuse the operation and SHALL report that a Designation is required.
5. WHEN the Ordnance_System schedules a Strategic_Strike, THE Ordnance_System SHALL notify every player who owns a building within the Strike_Radius from Balance_Config of the designated coordinate, and SHALL notify every player occupying a tile within that radius, reporting the designated coordinate and the remaining ticks.
6. THE Ordnance_System SHALL set the Response_Window of a Strategic_Strike to the Strike_Flight_Ticks from Balance_Config, with a value of at least 10 ticks.
7. WHEN a scheduled Strategic_Strike reaches resolution, THE Ordnance_System SHALL apply damage to each player, agent, and building within the Strike_Radius of the designated coordinate through the CombatEngine single-hit entry point, attributing the damage to the originating player.
8. THE Ordnance_System SHALL resolve Strategic_Strike damage as the `blast` damage type, so that the existing typed-resist axis, armor shred, chip-damage floor, shield absorption, and rank-gap damper apply.
9. WHEN a scheduled Strategic_Strike reaches resolution, THE Ordnance_System SHALL notify each player who owns a damaged entity and each player occupying a damaged tile, reporting the resolved coordinate.
10. THE Ordnance_System SHALL charge the Strike_Cost resource map from Balance_Config before scheduling a Strategic_Strike, and SHALL refund that charge when scheduling fails.
11. THE Ordnance_System SHALL enforce the Strike_Cooldown_Ticks from Balance_Config per originating building, and SHALL report the remaining cooldown when a request arrives before that cooldown elapses.
12. WHILE a scheduled Strategic_Strike is in flight, WHEN the originating building becomes non-Operational or is destroyed, THE Ordnance_System SHALL cancel that Strategic_Strike and SHALL notify the originating player.
13. THE Ordnance_System SHALL apply the existing Active_HQ_Rule to the originating building, so that a player with no completed headquarters on the originating planet launches no Strategic_Strike.

### Requirement 9: Fortification Branch — Traps, Area Denial, and Interception

**User Story:** As a Fortification player, I want to shape the ground around my base with hidden hazards and shoot down incoming strikes, so that attacking me costs the attacker more than it costs me.

#### Acceptance Criteria

1. WHERE a player holds the `defense` Branch_Commitment, WHEN a `sapper` Carrier_Agent assigned to an Operational Trap-producing building the player owns is directed to a coordinate within the Trap_Placement_Radius from Balance_Config of that building, THE Fortification_System SHALL place one Trap at that coordinate.
2. THE Fortification_System SHALL charge the Trap_Cost resource map from Balance_Config to the placing player before placing a Trap, and SHALL refund that charge when placement fails.
3. THE Fortification_System SHALL enforce the Trap_Cap_Per_Planet from Balance_Config per player per planet, and SHALL report the current count and the cap when a placement request exceeds that cap.
4. WHEN a player who is neither the Trap owner nor allied to the Trap owner enters a tile holding a Trap, THE Fortification_System SHALL resolve that Trap's area effect through the CombatEngine single-hit entry point, attributing the damage to the Trap owner.
5. WHEN an agent owned by a player who is neither the Trap owner nor allied to the Trap owner enters a tile holding a Trap, THE Fortification_System SHALL resolve that Trap's area effect attributing the damage to the Trap owner.
6. WHEN a Trap resolves, THE Fortification_System SHALL remove that Trap from the world, so that a Trap is consumed by triggering.
7. WHILE a Trap is unrevealed, THE Fortification_System SHALL exclude that Trap from the tile contents reported to players other than the Trap owner and the Trap owner's allies.
8. THE Fortification_System SHALL reveal a Trap to a player when that player performs a Detection_Sweep covering the Trap's coordinate, and SHALL reveal a Trap to a player whose resolved detection value meets the Trap_Detection_Threshold from Balance_Config when that player occupies a tile adjacent to the Trap's coordinate.
9. WHILE a Trap is revealed to a player, WHEN that player issues the existing bomb disarm command on the Trap's tile, THE Fortification_System SHALL resolve the attempt through the existing multi-tick disarm mechanic, so that disarming a Trap is a Universal_Counter.
10. WHERE a player holds the `defense` Branch_Commitment, WHILE that player owns an Operational interception building with an assigned Carrier_Agent within the Interception_Radius from Balance_Config of a designated Strategic_Strike coordinate, WHEN that Strategic_Strike resolves, THE Fortification_System SHALL reduce the Strategic_Strike damage applied within that radius by the Interception_Reduction_Fraction from Balance_Config, bounded to at most 0.75.
11. WHEN an interception reduces Strategic_Strike damage, THE Fortification_System SHALL notify the intercepting player and the originating player of the interception.
12. THE Fortification_System SHALL charge the Interception_Cost resource map from Balance_Config to the intercepting player per interception, and SHALL apply no reduction when that player holds insufficient resources.
13. WHEN a Trap's owner loses the `defense` Branch_Commitment on the Trap's planet, THE Fortification_System SHALL suspend that Trap's trigger behavior until that Branch_Commitment is restored, so that a dormant Branch denies no ground.

### Requirement 10: Biowarfare Branch — Contagion and Cures

**User Story:** As a Biowarfare player, I want to contaminate a position so that the enemy's own movement spreads my weapon, so that I win crowded fights I could not win with direct fire.

#### Acceptance Criteria

1. WHERE a player holds the `bio` Branch_Commitment, WHILE that player occupies an Operational Contagion-producing building the player owns and a Carrier_Agent is assigned to that building, WHEN that player requests a Contagion release against a coordinate within the Contagion_Release_Radius from Balance_Config, THE Contagion_System SHALL apply a Contagion effect to each player and agent within the Contagion_Radius from Balance_Config of that coordinate.
2. THE Contagion_System SHALL record a Contagion as an entry on the target's existing active-effects list, using the existing `poison` damage-over-time shape, so that the existing effect tick, death routing, and clear-on-respawn behavior apply without change.
3. THE Contagion_System SHALL mitigate a Contagion's per-tick damage by the target's existing `poison_resist` value, so that resist gear is a Universal_Counter.
4. WHEN a tick elapses in which an entity carrying a Contagion shares a tile with an entity carrying no Contagion, THE Contagion_System SHALL apply a Contagion to the second entity with a per-tick damage equal to the carrier's per-tick damage multiplied by the Contagion_Transmission_Decay from Balance_Config, bounded to less than 1.0.
5. WHEN a transmitted Contagion's per-tick damage falls below 1, THE Contagion_System SHALL apply no Contagion to the receiving entity, so that transmission terminates within a bounded number of hops.
6. THE Contagion_System SHALL apply a Contagion to an entity of the releasing player and to an entity allied to the releasing player on the same terms as any other entity, so that a Contagion is indiscriminate.
7. WHERE a player holds the `bio` Branch_Commitment, WHEN a `medic` Carrier_Agent that player owns occupies a tile with an entity that player owns or is allied to, THE Contagion_System SHALL remove Contagion effects from that entity, and THE AgentSystem SHALL award that `medic` agent experience for the cure.
8. WHEN a player uses an existing healing consumable while carrying a Contagion, THE Contagion_System SHALL reduce that Contagion's remaining ticks by the Contagion_Consumable_Relief_Ticks from Balance_Config, so that a medical kit is a Universal_Counter.
9. THE Contagion_System SHALL charge the Contagion_Cost resource map from Balance_Config before applying a Contagion release, and SHALL refund that charge when the release fails.
10. THE Contagion_System SHALL bound the total per-tick damage of all Contagion effects on one entity to the Contagion_Damage_Cap from Balance_Config, so that stacked transmissions cannot exceed a known ceiling.
11. WHEN the Contagion_System applies a Contagion to an entity, THE Contagion_System SHALL notify that entity's owning player, reporting the coordinate and the remaining ticks.
12. THE Contagion_System SHALL enforce the Contagion_Cooldown_Ticks from Balance_Config per originating building, and SHALL report the remaining cooldown when a request arrives before that cooldown elapses.

### Requirement 11: Signals Branch — Intrusion and Electronic Warfare

**User Story:** As a Signals player, I want to switch off an enemy's defenses instead of destroying them, so that I can take a hardened base with a small force.

#### Acceptance Criteria

1. WHERE a player holds the `cyber` Branch_Commitment, WHEN an `infiltrator` Carrier_Agent that player owns occupies a tile adjacent to or equal to an enemy building's tile, THE Intrusion_System SHALL permit that player to request an Intrusion against that building.
2. THE Intrusion_System SHALL require the requesting `infiltrator` Carrier_Agent to remain within the tile from which the Intrusion was requested for the Intrusion_Plant_Ticks from Balance_Config, and SHALL cancel the Intrusion when that agent moves, is incapacitated, or is killed before those ticks elapse.
3. WHEN an Intrusion is planted on a building, THE Intrusion_System SHALL report that building as non-Operational for the Intrusion_Duration_Ticks from Balance_Config, so that the building's capability behavior is suspended.
4. THE Intrusion_System SHALL bound the Intrusion_Duration_Ticks from Balance_Config to at most the Intrusion_Max_Duration_Ticks from Balance_Config, so that an Intrusion is temporary denial.
5. THE Intrusion_System SHALL preserve the intruded building's ownership, level, hit points, shield, and stored contents for the duration of the Intrusion, so that an Intrusion transfers no assets.
6. WHEN an Intrusion is planted on a building, THE Intrusion_System SHALL notify the building's owner, reporting the building abbreviation and coordinates and the remaining Intrusion ticks.
7. WHILE an Intrusion is active on a building, WHEN that building's owner occupies that building's tile and issues the purge command, THE Intrusion_System SHALL remove that Intrusion after the Intrusion_Purge_Ticks from Balance_Config of continuous presence, so that purging an Intrusion is a Universal_Counter.
8. WHERE a player holds the `cyber` Branch_Commitment, WHILE that player owns an Operational counter-intrusion building within the Firewall_Radius from Balance_Config of a targeted building, THE Intrusion_System SHALL extend the required Intrusion_Plant_Ticks by the Firewall_Plant_Penalty_Ticks from Balance_Config and SHALL notify the defending player when an Intrusion attempt begins.
9. WHEN an Intrusion targets a building whose owner holds no completed headquarters on that planet, THE Intrusion_System SHALL refuse the Intrusion and SHALL report that the target base is already inert, so that Intrusion adds no value against an already-disabled base.
10. THE Intrusion_System SHALL charge the Intrusion_Cost resource map from Balance_Config before planting an Intrusion, and SHALL refund that charge when planting fails.
11. WHERE a player holds the `cyber` Branch_Commitment, WHEN that player requests a jam against an enemy agent within the Jam_Radius from Balance_Config of an Operational Signals building that player owns, THE Intrusion_System SHALL suspend that agent's behavior script for the Jam_Duration_Ticks from Balance_Config and SHALL notify that agent's owner.
12. THE Intrusion_System SHALL preserve a jammed agent's role, assignment, carried resources, and experience for the duration of a jam, so that a jam commands no agent and destroys no progress.
13. WHEN an Intrusion or a jam expires, THE Intrusion_System SHALL restore the affected building or agent to the state that entity held before the effect, and SHALL notify that entity's owner of the restoration.

### Requirement 12: Logistics Branch — Convoys, Transport, and Redeployment

**User Story:** As a Logistics player, I want to move resources and agents across the map faster than anyone else, so that I answer threats and exploit openings before my opponents can.

#### Acceptance Criteria

1. WHERE a player holds the `resource` Branch_Commitment, WHILE that player occupies an Operational Logistics building the player owns, WHEN that player dispatches a Convoy carrying resources or agents toward another building that player owns on the same planet, THE Logistics_System SHALL create a Convoy world object at the originating building's coordinate carrying the dispatched cargo.
2. THE Logistics_System SHALL require a `courier` Carrier_Agent for each Convoy, and SHALL assign that agent to that Convoy for the duration of the Convoy.
3. WHEN a tick elapses while a Convoy is in transit, THE Logistics_System SHALL advance that Convoy along a path toward the destination building at the Convoy_Tiles_Per_Tick from Balance_Config.
4. WHEN a Convoy reaches the destination building, THE Logistics_System SHALL transfer the carried cargo into that building's storage up to that building's remaining capacity, SHALL return the `courier` Carrier_Agent to the unassigned state, and SHALL notify the owning player of the delivered amounts.
5. IF a Convoy's carried cargo exceeds the destination building's remaining capacity, THEN THE Logistics_System SHALL route the excess through the existing over-capacity spill path, so that cargo is never destroyed by a full destination.
6. THE Logistics_System SHALL represent a Convoy as an entity visible on the tile the Convoy occupies to any player whose vision covers that tile, so that a Convoy is interceptable.
7. WHEN a Convoy takes damage sufficient to reduce the Convoy's hit points to zero, THE Logistics_System SHALL drop the carried cargo as ground pickups at the Convoy's coordinate and SHALL notify the owning player of the loss.
8. THE Logistics_System SHALL bound the number of simultaneous Convoys a player may operate on one planet to the Convoy_Cap from Balance_Config, and SHALL report the current count and the cap when a dispatch request exceeds that cap.
9. WHERE a player holds the `resource` Branch_Commitment, WHEN that player requests a redeployment of an agent between two Operational Logistics buildings that player owns on the same planet, THE Logistics_System SHALL move that agent to the destination building after the Redeploy_Ticks from Balance_Config and SHALL charge the Redeploy_Cost resource map from Balance_Config.
10. THE Logistics_System SHALL charge the Convoy_Cost resource map from Balance_Config before creating a Convoy, and SHALL refund that charge when creation fails.
11. THE Logistics_System SHALL apply the existing carry-weight rules to cargo a Convoy delivers to a player, so that Convoy delivery grants no exemption from carry weight.
12. WHILE a player holds the `resource` Branch_Commitment, THE Logistics_System SHALL reduce that player's existing cross-planet launch preparation time by the Launch_Preparation_Reduction_Fraction from Balance_Config, bounded to at most 0.5.

### Requirement 13: Recon Branch — Detection, Counter-Intelligence, and Early Warning

**User Story:** As a Recon player, I want to see what other doctrines hide, so that my knowledge is the weapon that neutralizes their surprises.

#### Acceptance Criteria

1. WHERE a player holds the `research` Branch_Commitment, WHILE that player occupies an Operational Detection building the player owns with an assigned `scout` Carrier_Agent, WHEN that player requests a Detection_Sweep, THE Detection_System SHALL reveal to that player every Trap, planted Intrusion, in-flight Strategic_Strike Designation, Convoy, and enemy `infiltrator` agent within the Sweep_Radius from Balance_Config of the Detection building.
2. THE Detection_System SHALL charge the Sweep_Cost resource map from Balance_Config before performing a Detection_Sweep, and SHALL refund that charge when the sweep fails.
3. THE Detection_System SHALL retain a Detection_Sweep's revelations for the Sweep_Memory_Ticks from Balance_Config, and SHALL report a revealed entity as no longer revealed after those ticks elapse.
4. WHEN a Strategic_Strike is scheduled against a coordinate within the Sweep_Radius of an Operational Detection building, THE Detection_System SHALL notify that building's owner of the designated coordinate and the remaining flight ticks at scheduling time, so that Recon holds the earliest warning.
5. WHERE a player holds the `research` Branch_Commitment, WHEN that player performs a Detection_Sweep covering a coordinate holding a Designation against a building that player owns, THE Detection_System SHALL increase the flight ticks of Strategic_Strikes resolving at that coordinate by the Designation_Disruption_Ticks from Balance_Config and SHALL notify the originating player of the delay.
6. THE Detection_System SHALL produce a Designation usable by an allied Ordnance player only while the allied player has consented to receive Designations, so that Designation sharing is explicit.
7. WHERE a player holds the `research` Branch_Commitment, THE Detection_System SHALL add the Counter_Intel_Detection_Bonus from Balance_Config to that player's resolved detection value, so that Recon meets Trap and Intrusion detection thresholds that other Branches do not.
8. WHEN a Detection_Sweep reveals an entity, THE Detection_System SHALL notify the sweeping player with the entity kind and coordinates for each revealed entity.
9. THE Detection_System SHALL enforce the Sweep_Cooldown_Ticks from Balance_Config per Detection building, and SHALL report the remaining cooldown when a request arrives before that cooldown elapses.

### Requirement 14: Cross-Branch Balance and the Counter Web

**User Story:** As a player, I want every doctrine to be beatable and every doctrine to be viable, so that my choice of Branch expresses my style rather than deciding whether I can compete.

#### Acceptance Criteria

1. THE DataRegistry SHALL load a Counter_Web defining, for each of the six Branches, the Branches that Branch holds an advantage over.
2. THE SchemaValidator SHALL confirm that each of the six Branches holds an advantage over at least one Branch and that at least one Branch holds an advantage over each of the six Branches, and SHALL report a validation error naming any Branch failing either condition.
3. THE SchemaValidator SHALL confirm that no Branch holds an advantage over more than two Branches, and SHALL report a validation error naming any Branch exceeding that count.
4. THE Branch_System SHALL express a Counter_Web advantage as a bounded numeric multiplier within the range 1.0 through the Counter_Advantage_Cap from Balance_Config, defaulting to 1.35, or as a Response_Window reduction, so that an advantage changes a magnitude or a timing rather than granting immunity.
5. THE Branch_System SHALL provide at least one Universal_Counter for each Signature_Vector, available to a player holding any Branch_Commitment and to a player holding no Branch_Commitment.
6. THE Branch_System SHALL provide at least one Doctrine_Counter for each Signature_Vector.
7. THE Branch_System SHALL set the effect of every Signature_Vector on a building to either a temporary suspension of that building's behavior or damage routed through the existing hit-point and shield pipeline, so that no Signature_Vector deletes a building outright and no Signature_Vector transfers ownership.
8. THE Branch_System SHALL grant each hostile Signature_Vector operation's target a Response_Window of at least the Minimum_Response_Window_Ticks from Balance_Config, defaulting to 5 ticks, measured from the target's notification to the operation's effect.
9. THE SchemaValidator SHALL compute each Branch's investment score as the sum, over the build costs of that Branch's Branch_Lab and Branch_Buildings and the resource costs of that Branch's technologies, of each resource amount multiplied by that resource's weight from the existing Balance_Config resource-weight map.
10. THE SchemaValidator SHALL confirm that each Branch's investment score falls within the Branch_Cost_Parity_Tolerance fraction from Balance_Config of the mean investment score across the six Branches, and SHALL report a validation error naming each Branch outside that tolerance together with that Branch's score and the mean.
11. THE Branch_System SHALL apply the existing chip-damage floor, typed-resist axes, permanent-bonus caps, and shield absorption to every damage source this feature introduces, so that no new vector bypasses the existing damage-balance guardrails.
12. THE Branch_System SHALL apply at most one Counter_Web advantage multiplier to one operation, so that advantage multipliers do not compound.

### Requirement 15: New-Player Protection and Escalation Limits

**User Story:** As a new player, I want a veteran's doctrine weapons to be survivable, so that I keep playing long enough to commit to a doctrine of my own.

#### Acceptance Criteria

1. THE Branch_System SHALL apply the existing rank-gap damage damper to every damage source this feature introduces, so that a much-higher-ranked unprovoked attacker deals reduced damage through a new vector.
2. THE Branch_System SHALL apply the existing rank-gap experience and loot reduction to kills resulting from a Signature_Vector, so that a new vector is no farming route.
3. IF a player requests a hostile Signature_Vector operation against a target player whose level is below the New_Player_Vector_Shield_Level from Balance_Config, THEN THE originating Branch system SHALL refuse the operation and SHALL report the level at which the target becomes a valid Signature_Vector target.
4. THE Branch_System SHALL gate every Branch_Building introduced by this feature behind a level requirement at or above the existing Branch_Lab level requirement, so that no Branch content precedes the existing lab gate.
5. THE Branch_System SHALL bound the number of hostile Signature_Vector operations one player may resolve against one target player within the Escalation_Window_Ticks from Balance_Config to the Escalation_Cap from Balance_Config, and SHALL report the remaining ticks when a request exceeds that cap.
6. THE Branch_System SHALL apply every Signature_Vector operation gate to alliance members and allies on the same terms as to unaffiliated players, so that alliance membership grants no exemption from escalation limits.
7. WHERE a player holds no Branch_Commitment, THE Branch_System SHALL leave that player's access to melee combat, ranged combat, bombs, walls, turrets, shields, and every Neutral_Building unchanged, so that declining to commit remains a playable state.

### Requirement 16: Base Defense, NPC Base, and Alliance Integration

**User Story:** As a player, I want doctrine buildings to be part of my base rather than a separate game, so that the defenses, alliances, and enemies I already understand still apply.

#### Acceptance Criteria

1. THE ShieldSystem SHALL project shields onto Branch_Buildings on the same terms as onto Neutral_Buildings.
2. THE GuardCombatSystem SHALL defend Branch_Buildings on the same terms as Neutral_Buildings.
3. THE Branch_System SHALL apply the existing Active_HQ_Rule to every Branch_Building, so that a player with no completed headquarters on a planet operates no Branch_Building there.
4. WHEN a base elimination removes a player's or an NPC base's buildings, THE Branch_System SHALL cancel every in-flight Signature_Vector operation originating from a removed building and SHALL notify the operation's owner.
5. THE DataRegistry SHALL permit an NPC base template to include Branch_Buildings, so that players can practice against each Signature_Vector before facing a player using that vector.
6. WHEN an NPC base holding a Branch_Building resolves a Signature_Vector operation, THE originating Branch system SHALL apply the same notification, Response_Window, and Universal_Counter rules that apply to a player-originated operation.
7. THE AllianceSystem SHALL leave alliance perk categories unchanged, so that no alliance perk grants a Signature_Vector.
8. WHERE two players are allied, WHEN one player requests a Signature_Vector operation in support of the other player, THE originating Branch system SHALL perform the operation only while the supported player has consented to receive that support, and SHALL report the missing consent when consent is absent.
9. THE Branch_System SHALL exclude allied entities from being valid targets of a hostile Signature_Vector operation requested against them, and SHALL apply Contagion and Trap and Strategic_Strike area effects to allied entities within an area of effect, so that indiscriminate area effects remain indiscriminate while deliberate targeting of an ally is refused.
10. WHEN a player leaves an alliance, THE Branch_System SHALL revoke that player's outstanding Designation-sharing and support consents with that alliance's members.

### Requirement 17: Economy Costs and Resource Sinks

**User Story:** As a player, I want doctrine power to cost resources every time I use it, so that my economy and my military are the same decision.

#### Acceptance Criteria

1. THE DataRegistry SHALL define a per-use resource cost for every Signature_Vector operation in Balance_Config.
2. THE originating Branch system SHALL charge a Signature_Vector operation's resource cost before that operation takes effect, and SHALL refund that charge when the operation fails to take effect.
3. IF a player holds insufficient resources for a Signature_Vector operation, THEN THE originating Branch system SHALL refuse the operation and SHALL report the existing have-and-need breakdown for the missing resources.
4. THE DataRegistry SHALL define the resource cost of each Branch's Signature_Vector building chain to include at least one of the late-game resources `Circuits`, `Energy`, or `Nexium`, so that Signature_Vector access depends on economic reach.
5. THE ResourceSystem SHALL leave existing harvest yields, extractor output, and storage capacities unchanged for a player holding any Branch_Commitment other than `resource`, so that committing to a combat Branch costs economic output only through the resources that Branch consumes.
6. WHERE a player holds the `resource` Branch_Commitment, THE ResourceSystem SHALL apply that Branch's researched production and cost technologies through the existing production and build-cost paths, with the existing clamps applied.
7. THE Branch_System SHALL charge no recurring upkeep for a Branch_Building beyond the existing repair cost, so that owning a Branch_Estate imposes a rebuild-and-use cost rather than a passive drain.

### Requirement 18: Player-Facing Communication and Discoverability

**User Story:** As a player, I want to understand what committing to a doctrine gives me and what it costs before I commit, so that I am not punished for a decision I could not see.

#### Acceptance Criteria

1. WHEN a player requests the technology view, THE TechLabSystem SHALL report the player's Branch_Commitment on the occupied planet, that Branch's Signature_Vector, the researched technologies of that Branch, and the technologies of that Branch available to research.
2. WHEN a player requests the technology view while holding recorded technologies in a Branch in Branch_Dormancy, THE TechLabSystem SHALL report each dormant Branch, the count of recorded technologies in that Branch, and the Reinstatement cost fraction.
3. WHEN a player requests a Branch overview, THE Branch_System SHALL report, for each of the six Branches, that Branch's hosting Branch_Lab, Signature_Vector, agent role, the Branches that Branch holds an advantage over, and the Branches that hold an advantage over that Branch.
4. WHEN a player requests construction of a Branch_Lab that would abandon a Branch, THE BuildingSystem SHALL report the buildings that must be removed and the technologies that would enter Branch_Dormancy before charging any resources.
5. THE Branch_System SHALL emit every player-facing message this feature introduces as a structured notification through the existing EventBus notification contract, composing no message text inside a system component.
6. THE NotificationPresenter SHALL render a notification kind for each hostile operation's scheduling, resolution, cancellation, detection, cure, purge, and expiry.
7. THE DirectiveSystem SHALL include at least one directive step introducing the Branch commitment decision, positioned at or after the existing Branch_Lab level and deed gate.
8. WHEN a hostile Signature_Vector operation is scheduled against a target, THE originating Branch system SHALL notify the target with the operation kind, the originating player name, the affected coordinate, and the remaining ticks, so that the Response_Window is actionable.

### Requirement 19: Persistence and Restart Recovery

**User Story:** As a player, I want the traps I laid and the strike I launched to survive a server restart, so that a restart neither erases my investment nor leaves a hazard stuck forever.

#### Acceptance Criteria

1. THE Fortification_System SHALL persist each Trap's owner, coordinate, area effect, damage, radius, and revealed state on the Trap world object.
2. THE Ordnance_System SHALL persist each in-flight Strategic_Strike's originating building, originating player, designated coordinate, remaining flight ticks, damage, and radius.
3. THE Intrusion_System SHALL persist each active Intrusion's target building, originating player, and remaining ticks, and each active jam's target agent, originating player, and remaining ticks.
4. THE Contagion_System SHALL persist each Contagion as an entry on the carrying entity's existing active-effects list.
5. THE Logistics_System SHALL persist each in-transit Convoy's owner, `courier` Carrier_Agent, cargo, current coordinate, and destination building.
6. WHEN the server restarts, THE Fortification_System, Ordnance_System, Intrusion_System, Contagion_System, and Logistics_System SHALL rebuild the in-memory tracking of persisted operations from the persisted state, so that every persisted operation resumes advancing on the tick loop.
7. FOR ALL persisted operation states, writing the state and then rebuilding the state after a restart SHALL produce an operation whose owner, target, remaining ticks, and effect magnitude equal the values written.
8. IF a persisted operation references a target, originating building, or Carrier_Agent that no longer exists, THEN THE owning Branch system SHALL discard that operation and SHALL log the discard identifying the operation kind and the missing reference.
9. THE Branch_System SHALL derive Branch_Commitment and Branch_Estate membership from the owned buildings at query time, so that no restart can desynchronize a Branch_Commitment from the buildings that define it.

### Requirement 20: System Integration Invariants

**User Story:** As a developer, I want the new systems to follow the conventions the existing systems already follow, so that this feature is maintainable alongside the code it extends.

#### Acceptance Criteria

1. THE Branch_System, Ordnance_System, Fortification_System, Contagion_System, Intrusion_System, Logistics_System, and Detection_System SHALL import no game-framework module at module scope, receiving every framework-dependent collaborator through injection at the composition root.
2. WHERE a collaborator required for an operation is not injected, THE owning Branch system SHALL refuse that operation and SHALL log the missing collaborator, so that an unwired system degrades to a refusal rather than an error.
3. WHEN a command layer invokes an operation this feature introduces, THE owning Branch system SHALL return an outcome value for every input, raising no exception into the command layer.
4. THE owning Branch system SHALL charge resources before applying an operation's effect and SHALL restore charged resources when the effect fails to apply, so that no operation both charges and fails.
5. THE Branch_System SHALL resolve every building capability check through the injected DataRegistry, so that tests run without a global registry.
6. WHEN a Branch system writes a persistent container of operation state, THE Branch system SHALL read the container, modify a copy, and write the whole container back, so that the write persists.
7. THE Branch_System SHALL read every persisted attribute by value and SHALL treat an absent attribute as the documented default for that attribute.
8. WHEN a per-tick step processes a collection of operations, THE owning Branch system SHALL isolate each operation's processing so that a failure in one operation leaves the remaining operations processed.
9. THE DataRegistry SHALL validate every Balance_Config field this feature introduces for type and range, and SHALL fail the load with a collected error report when a field is invalid.
10. WHEN the Balance_Config is reloaded, THE Branch systems SHALL read the updated values on the next operation, so that every value this feature introduces is hot-tunable.
