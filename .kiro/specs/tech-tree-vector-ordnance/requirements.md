# Requirements Document

## Introduction

This feature implements the **Ordnance** Branch's Signature_Vector, the
**Strategic_Strike**: a delayed, publicly warned area attack centered on an
observed coordinate. It is one of six child specs of
`tech-tree-combat-expansion` and implements that parent's Requirement 8.

The shipped Branch framework remains the foundation for this feature, but this
spec does not claim that coordinate targeting, designation transactions,
confirming persistence, keyed mutations, or Ordnance metadata already exist in
that framework:

- `OperationDriver` supplies the six-state lifecycle, ordered validation chain,
  response ticking, restart rebuild, combat orchestration entry point, and generic
  vector notifications. It does not own combat-state atomicity: its additive
  `apply_hit_once` seam is only a thin delegate to the engine-owned keyed API
  required by this spec. A lapsed Branch commitment **suspends** an operation;
  a killed carrier, physically destroyed or deleted origin, or eliminated base
  cancels it. This spec additively requires confirming persistence, durable
  acceptance and resolution protocols, and conservative retry at clock zero.
- `BranchSystem.may_target(actor, target, hostile=True)` protects one concrete
  target. A coordinate with no `target` does not inherit the new-player shield,
  allied-target refusal, or escalation checks. This spec therefore gives every
  strike one concrete Primary_Target_Owner and places that owner in
  `OperationRecord.target_ref`.
- The shipped `OperationRecord` serializer has no schema version or vector-owned
  payload, and the shipped acceptance path has no prerequisite durable
  reserve/commit/rollback transaction. Both are additive contracts required by
  this spec. The one shared global capacity-bounded `Post_Commit_Outbox`, its
  `reserve_once`/`append_reserved`/`release_once` slot contract, and the
  startup-validated global `vector_outbox_capacity` are likewise additive and are
  shared with the sibling Biowarfare vector rather than owned per Branch.
- The shipped `BranchSystem` charge, refund, cooldown, escalation, Counter_Web,
  and `AgentSystem` XP APIs are unkeyed. The shipped
  `CombatEngine.apply_direct_hit` path and its ordinary consequence cascade are
  also unkeyed. They remain legacy-only for existing callers; vectors introduced
  or revised by this spec use the additive keyed, checked, and engine-owned
  transaction APIs defined below. This spec does not claim that wrapping
  `apply_direct_hit` with a caller-side receipt makes that shipped path exactly
  once.
- The shipped `BranchSystem.eligible_carrier` selects the first eligible agent;
  it does not validate a caller-selected agent. This spec requires an additive
  exact-candidate eligibility query so the launching player's selected
  `spotter` can be the operation carrier.
- The shipped presenter has construction-specific refusal rendering, not a
  generic vector-refusal renderer. This spec requires a new generic presenter
  entry point and does not reuse the construction renderer by assertion.

The Ordnance implementation SHALL join the shipped registration, tick, state,
and combat paths rather than create parallel versions of them. The additive
contracts above are part of this feature's requirements and must not be treated
as already delivered behavior.

## Glossary

The parent spec's glossary applies. These terms are new or narrowed here.

- **Ordnance_System**: The Vector_System that owns Strategic_Strikes,
  Designations, Warning_Areas, durable Ordnance transactions and receipts, and
  the two authorized counter-provider seams. It composes `OperationDriver`
  before `BaseSystem` and registers under the `strategic_strike` Operation_Kind.
- **Strategic_Strike**: One hostile `strategic_strike` Vector_Operation whose
  immutable impact center is a designated coordinate and whose victims are
  selected from that area at impact.
- **Designation**: A durable observation value held by one player. Its identity,
  observed coordinate, provenance, Primary_Target_Owner, and lifetime are
  immutable; only its reservation state may change before it is consumed.
- **Designation_Holder**: The player on whom a Designation is persisted and who
  owns the right to consume or share it. This is not necessarily the player who
  later launches a strike.
- **Designation_Producer**: Informational provenance for an observation:
  `spotter` or `detection_sweep`, plus an optional reference to the producing
  agent or operation. It is not the Strategic_Strike lifecycle carrier and
  receives no Strategic_Strike XP.
- **Primary_Target_Owner**: The concrete, non-self, non-allied owner observed at
  designation time. The immutable `primary_target_owner_ref` is passed through
  `BranchSystem.may_target` at strike request, stored in
  `OperationRecord.target_ref`, and is the strike's sole escalation identity.
- **Vector_Shield_Query**: The shared Ordnance and Biowarfare public, read-only
  `BranchSystem.is_vector_shielded(target_owner)` contract used only to read the
  target owner's **current** new-player vector-shield state. It applies no
  alliance, escalation, cooldown, or other `may_target` rule. For hostile
  collateral, an absent or unresolvable `target_owner`, an unavailable or failed
  query, or any result other than the explicit Boolean `False` fails closed and
  is treated as shielded.
- **Operation_Carrier**: The launching player's selected eligible `spotter`,
  stored in `OperationRecord.carrier_ref`. It controls suspension/cancellation
  and is the sole possible Strategic_Strike XP recipient.
- **Warning_Area**: The public, queryable marker `(planet, center_x, center_y,
  radius)` maintained for a strike's entire non-terminal flight. The minimum
  response guarantee belongs to first durable publication of this area, not to
  every entity that may later enter it.
- **Raw_Base_Damage**: The integer snapshotted at strike acceptance as
  `strategic_strike_damage * max(1, origin_building_level)`, before the one
  victim-specific Counter_Web multiplier, interception, and CombatEngine rules.
- **Per_Victim_Raw_Magnitude**: The integer pre-combat magnitude passed into one
  retained victim's blast weapon, computed exactly as
  `max(1, floor(raw_base_damage * one_counter_multiplier * (1 - frozen_interception_fraction)))`.
- **Interception**: An authorized Fortification provider's persisted cumulative
  fractional reduction, permanently frozen in one deterministic pre-damage
  phase.
- **Designation_Disruption**: An authorized Detection provider's persisted
  cumulative extension of a Pending or Suspended strike's clock.
- **Persistence_Result**: The result of a durable write or readback, with exactly
  `confirmed`, `rejected`, or `indeterminate`. `confirmed` requires durable
  atomic-store acknowledgement or positive readback and never means merely that
  a best-effort write did not raise. A confirmed read distinguishes authoritative
  absence from presence; unreadable or timed-out storage is `indeterminate`, not
  absence.
- **Mutation_Result**: The shared keyed-mutation result with exactly `applied`,
  a structured `duplicate(prior=...)`, `conflict`, `rejected`, or
  `indeterminate`. `applied` and `rejected` are immutable original receipt
  outcomes; `duplicate.prior` is exactly that original `applied` or `rejected`
  outcome. The mutation and its immutable payload-hash/outcome receipt commit
  atomically; the same key and payload returns
  `duplicate(prior=<original_outcome>)`, while the same key with a different
  payload returns `conflict` and fails closed. A terminal domain no-op is an
  original `rejected` outcome with an immutable domain reason, not an
  outcome-less duplicate. `duplicate(prior=applied)` retains the authority of
  the original applied receipt and ensures or replays exactly the work that
  receipt already authorized; `duplicate(prior=rejected)` retains the original
  no-application decision and authorizes nothing. A refusal that records no
  original receipt, such as a claimless capacity rejection, is a retriable
  `rejected` that SHALL NOT later be reported as `duplicate(prior=rejected)`.
- **Acceptance_Transaction**: The durable launch transaction keyed by a
  preallocated `op_id`. It owns Designation reservation, charge, Pending entry,
  commit, cooldown, Warning_Area publication readiness, and compensation facts.
- **Combat_Hit_Transaction**: The durable CombatEngine-owned transaction keyed
  by `strike:{op_id}:victim:{stable_id}`. In one engine atomic unit it binds the
  immutable request hash and target combat-state version/preconditions to the
  computed damage outcome; every authoritative shield, HP, and other target
  combat-state delta; the resulting combat state; an authoritative
  zero-HP/death-pending marker when applicable; the hit receipt; and the
  deterministic keys for all required downstream consequences. Core combat is
  an original `applied` outcome only when that unit is confirmed, and
  `duplicate(prior=applied)` reads that same committed transaction without
  reapplying any delta. Consequences that cannot share the atomic combat store
  are resumed as durable engine-owned keyed or outbox work derived from this
  receipt, each carrying its own original `applied` or `rejected` outcome so an
  inapplicable consequence settles as a receipted no-op rather than a repeat.
- **Strike_Resolution_Transaction**: The durable impact transaction keyed by
  `op_id`. It owns the permanent interception freeze, one canonical finite
  candidate snapshot, checked Counter_Web outcomes, the exact shared-outbox
  reservation manifest, and keyed per-victim progress that references the
  authoritative Combat_Hit_Transaction and its downstream receipts.
- **Post_Commit_Outbox**: The one shared global durable outbox for vector event
  and keyed-mutation work. It stores immutable reservation and event IDs,
  payloads including every snapshotted amount and tick, bounded recipients,
  phase, and receipts. It exposes exactly
  `reserve_once(reservation_id, slots)`,
  `append_reserved(reservation_id, event_id, kind, payload, recipients)`, and
  `release_once(reservation_id)`, and each operation returns `Mutation_Result`
  and atomically persists its immutable method key, payload hash, original
  outcome receipt, and corresponding capacity state. `reserve_once` accepts
  `slots` only as an exact non-Boolean positive integer and atomically claims
  that exact number only when capacity permits. A same-key/same-payload retry
  returns `duplicate(prior=<original_outcome>)`:
  `duplicate(prior=applied)` ensures the one original claim, entry, or release
  receipt and creates no second one, while `duplicate(prior=rejected)` creates
  none. A changed slot count, event ID, kind, payload, or recipient set under
  the same method key returns `conflict` and fails closed, and an unreadable
  possible claim returns `indeterminate`. Insufficient capacity returns a
  claimless `rejected` that records no original reservation receipt and consumes
  no slot, so a still-eligible producer MAY retry that same reservation ID after
  capacity changes; that refusal SHALL NOT be reported as
  `duplicate(prior=rejected)`, and no `duplicate` outcome is reported unless a
  matching original reservation receipt exists. `append_reserved` atomically
  converts one unconsumed reserved slot into one live unsettled entry; it never
  appends without a confirmed matching reservation, over-consumes that
  reservation, or changes total capacity use. `release_once` closes the
  reservation and releases only its unconsumed slots after the producer has
  durably completed its exact manifest or definitively abandoned it before an
  irreversible domain action; it never removes a live entry. An indeterminate
  reservation or release retains and conservatively counts its claim pending
  confirming readback. Notification delivery uses the idempotent
  `publish_once(event_id, kind, payload, recipients)` sink. A live entry becomes
  settled and stops consuming capacity only after its sink or named keyed API
  receipt is durably confirmed. Existing work is never evicted; settled entries,
  closed reservations, and their constant-size tombstones use finite retention
  after their source transaction has durably recorded settlement and their
  globally stable namespace cannot be reused. An unkeyed publish, mutation, XP
  call, or combat call plus a caller-side flag never establishes exactly-once
  behavior.
- **Ordnance Works (`OW`)**: The shipped `weapons` Branch_Building, gated by
  `field_marksmanship`, from which a Strategic_Strike launches.

## Requirements

### Requirement 1: Ordnance Vector Integration

**User Story:** As a developer, I want Ordnance to extend the shipped operation
framework explicitly, so that shared lifecycle guarantees are reused without
pretending that Ordnance-specific contracts already exist.

#### Acceptance Criteria

1. THE Ordnance_System SHALL compose `OperationDriver` before `BaseSystem`, SHALL
   declare `operation_kind = "strategic_strike"` and `branch = "weapons"`, and
   SHALL implement `validate_target`, `build_record`, `on_resolve`,
   `persistence_owner`, and `discover_records`; `on_resolve` SHALL participate
   only as the prepare half of the explicit resolution protocol in criterion 8.
2. THE Ordnance_System SHALL declare every required collaborator, including the
   Branch_System, the CombatEngine exposing engine-owned
   `apply_direct_hit_once(attacker, target, weapon, mutation_id, context)` and
   durable `Combat_Hit_Transaction` reconciliation, AgentSystem, current-tick
   source, coordinate service, spatial index or planet-room cache, confirming
   atomic persistence, the same global capacity-governed `Post_Commit_Outbox`,
   `publish_once` sink, and registered counter-provider authorization services,
   so the inherited collaborator check returns a structured refusal instead of
   allowing a request to raise.
3. THE Ordnance_System SHALL import no game-framework module at module scope.
4. WHEN the composition root constructs the Ordnance_System, THE composition
   root SHALL inject its collaborators and register it with
   `BranchSystem.register_vector` before the existing generic
   `registered_vectors()` rebuild loop runs.
5. WHEN the launching player identifies a particular `spotter`, THE carrier
   check SHALL use an additive Branch_System exact-candidate eligibility service
   that applies the shipped alive, role, reserve, incapacitation, ownership, and
   planet rules to that selected agent; it SHALL NOT silently substitute the
   first eligible agent returned by `eligible_carrier`.
6. THE OperationDriver SHALL expose `origin_fatal_reason(record)` as an additive
   fatality query for physical or source-fatal conditions independent of Branch
   commitment and SHALL never return `branch_dormant` from it. Ordnance policy
   SHALL then suspend for carrier unavailability or lapsed `weapons` commitment,
   resume with the exact held clock when both recover, and cancel for a killed
   carrier, destroyed or deleted origin, eliminated base, or another confirmed
   physical fatal reason. A nonphysical origin status caused only by commitment
   dormancy SHALL not become fatal origin loss.
7. THE Ordnance_System SHALL use OperationDriver's single transition writer for
   every lifecycle state change, including acceptance compensation after an
   operation has or may have entered Pending. It SHALL use that writer's
   tracking, tick fan-out, in-flight accounting, persistence-owner path, and the
   additive keyed APIs rather than create a second lifecycle, timer, ledger,
   state mutation path, or direct-damage path. `OperationDriver.apply_hit_once`
   SHALL be only a thin delegate that forwards attacker, target, weapon,
   mutation ID, and context to
   `CombatEngine.apply_direct_hit_once(attacker, target, weapon, mutation_id,
   context)` and returns the engine result. Neither OperationDriver nor
   Ordnance SHALL write a caller-side hit receipt around or invoke the legacy
   unkeyed `CombatEngine.apply_direct_hit` cascade as an exactly-once path.
   Existing unkeyed `charge`, `refund`, `note_cooldown`, `note_escalation`,
   `counter_multiplier`, `award_operation_xp`, and `apply_direct_hit` calls SHALL
   remain legacy-only and SHALL NOT be used by Strategic_Strike.
8. THE OperationDriver SHALL provide a confirming persistence seam returning
   `Persistence_Result` for every operation write/readback and an explicit
   resolution prepare/outcome protocol. A due operation SHALL remain tracked at
   clock `0` whenever prepare, effect commit, terminal write, or confirming
   readback is `rejected`, failed, or `indeterminate`; no prepare attempt alone
   authorizes a terminal transition. A durably terminal operation MAY stop
   ticking, but its source record SHALL be removed only after terminal
   confirmation, every required shared-outbox entry is settled, and every
   related reservation is confirmed released or fully consumed and closed.
   Durable Acceptance_Transaction, Strike_Resolution_Transaction,
   Combat_Hit_Transaction, mutation-receipt, and outbox authority SHALL survive
   as long as needed for replay and reconciliation and SHALL never be discarded
   merely because lifecycle state became terminal.
9. THE BranchSystem SHALL expose additive keyed vector APIs
   `charge_once(player, cost, mutation_id)`,
   `refund_once(player, cost, mutation_id, charge_mutation_id)`,
   `note_cooldown_once(building, kind, ready_at, mutation_id)`, and
   `note_escalation_once(actor, target, resolved_tick, mutation_id)`; THE
   AgentSystem SHALL expose
   `award_operation_xp_once(agent, kind, amount, mutation_id)`. Each SHALL
   return `Mutation_Result` with exactly `applied`, structured
   `duplicate(prior=applied|rejected)`, `conflict`, `rejected`, or
   `indeterminate`. An original domain mutation and its immutable mutation-key,
   payload-hash, original `applied` or `rejected` outcome, and domain-reason
   receipt SHALL be one atomic decision; a terminal domain no-op SHALL retain an
   original `rejected` receipt and immutable reason. Repeating the same key and
   payload SHALL return `duplicate(prior=<original_outcome>)` without
   reapplying: `duplicate(prior=applied)` SHALL retain the authority of the
   original applied receipt, while `duplicate(prior=rejected)` SHALL retain the
   original no-application decision. Repeating a key with a different payload
   SHALL return `conflict` and fail closed; an ambiguous mutation SHALL retain
   its authority for positive readback and SHALL NOT be retried under a new key.
   Strategic_Strike SHALL use the deterministic keys `accept:{op_id}:charge`,
   `accept:{op_id}:refund`, `accept:{op_id}:cooldown`,
   `resolve:{op_id}:escalation`, and `resolve:{op_id}:xp` respectively.
10. THE BranchSystem SHALL expose additive
    `counter_multiplier_checked(actor_branch, target_branch)`, returning exactly
    one of four result variants: `neutral(1.0)`, `advantage(multiplier)`,
    `unavailable(reason)`, or `invalid(reason)`. Only `neutral(1.0)` and
    `advantage(multiplier)` authorize arithmetic. Both `unavailable(reason)` and
    `invalid(reason)` SHALL follow Ordnance's durable fail-closed pair-skip
    behavior in Requirement 8.7. The existing float-returning
    `counter_multiplier` SHALL remain legacy-only and SHALL NOT be used at
    Strategic_Strike impact.
11. THE composition root SHALL inject one shared global `Post_Commit_Outbox`
    governed by startup-validated `vector_outbox_capacity`; it SHALL NOT create
    an Ordnance-private or per-operation capacity pool. That outbox SHALL expose
    exactly `reserve_once(reservation_id, slots)`,
    `append_reserved(reservation_id, event_id, kind, payload, recipients)`, and
    `release_once(reservation_id)`; each SHALL return `Mutation_Result` and
    atomically persist its immutable method key, payload hash, original outcome
    receipt, and corresponding capacity state, with
    `duplicate(prior=applied)` ensuring the one original claim, entry, or release
    receipt and `duplicate(prior=rejected)` creating none. AT every atomic outbox
    boundary, global live unsettled entries plus unconsumed reserved slots SHALL
    be no greater than the current capacity, and a successful `append_reserved`
    SHALL convert one reserved slot into one live entry without increasing that
    sum. THE system SHALL never evict, overwrite, or silently settle existing
    work to admit a reservation or configuration change; startup activation and
    hot reload SHALL reject a capacity below current use; and settled entries,
    closed reservations, and constant-size replay tombstones SHALL be pruned only
    under the declared finite retention horizon of Requirement 11.13. Every
    Ordnance producer SHALL reserve the exact finite manifest before the related
    irreversible domain action, SHALL retain an indeterminate claim for
    confirming readback under the same reservation ID, and SHALL never substitute
    a replacement reservation ID or evict existing work.

### Requirement 2: Designation Value and Coordinate Validity

**User Story:** As an Ordnance player, I want every strike coordinate tied to a
real hostile observation, so that an area attack cannot inherit protections
from an empty coordinate or reach invalid world space.

#### Acceptance Criteria

1. THE Designation value SHALL contain immutable `designation_id`,
   `holder_ref`, canonical `planet`, canonical `x`, canonical `y`,
   `producer_kind`, optional `producer_ref`, immutable
   `primary_target_owner_ref`, `created_tick`, and `expires_at_tick`, plus the
   mutable reservation fields `reservation_state` and
   `reserved_operation_id`.
2. THE `designation_id` SHALL be globally stable and nonempty, THE
   `producer_kind` SHALL be exactly `spotter` or `detection_sweep`, and THE
   `reservation_state` SHALL be exactly `available` or `reserved`; an available
   Designation SHALL have no `reserved_operation_id`, and a reserved one SHALL
   name the operation ID that reserved it.
3. WHEN any Designation or Strategic_Strike command supplies `x` or `y`, THE
   Ordnance_System SHALL accept each coordinate component only when
   `type(value) is int`, thereby rejecting Booleans, floats, strings, NaN, and
   infinities; the planet identifier SHALL instead be resolved as an existing
   canonical planet by the injected coordinate service.
4. WHEN a coordinate is admitted, THE injected coordinate service SHALL verify
   that the supplied planet already exists and SHALL return the in-bounds
   canonical planet and coordinate; THE Ordnance_System SHALL use that returned
   value and SHALL NOT materialize a room, create a planet, or perform an
   object-database scan for arbitrary input.
5. WHEN a `spotter` produces a Designation, THE Ordnance_System SHALL verify that
   the holder currently holds the `weapons` Branch_Commitment, the selected
   producer belongs to the holder, is the exact eligible `spotter` selected on
   the canonical planet, and is within `designation_radius` of the coordinate by
   Chebyshev distance.
6. WHEN a `spotter` produces a Designation, THE Ordnance_System SHALL verify that
   one caller-identified concrete target entity is currently at the canonical
   coordinate, has a resolvable owner other than the holder, and is not allied
   to the holder; THE system SHALL snapshot that owner as
   `primary_target_owner_ref` rather than persist the observed entity as the
   policy target.
7. IF the selected `spotter` is outside `designation_radius`, THEN THE
   Ordnance_System SHALL refuse without mutation and SHALL return the configured
   radius and measured Chebyshev distance as structured data.
8. IF the selected producer or observed primary target fails any ownership,
   role, planet, operability, location, self-target, or alliance condition, THEN
   THE Ordnance_System SHALL refuse without creating a Designation and SHALL
   identify the failed condition through a message key and structured data.
9. WHEN a registered Detection_Sweep provider creates a Designation, THE
   Ordnance_System SHALL require the same canonical coordinate, concrete
   non-self non-allied Primary_Target_Owner, holder, cap, lifetime, and durable
   value shape, SHALL set `producer_kind = "detection_sweep"`, and SHALL allow
   `producer_ref` to be absent without applying the spotter role or
   `designation_radius` checks.
10. WHEN a Designation is created at tick `t`, THE Ordnance_System SHALL set
    `created_tick = t` and `expires_at_tick = t + designation_memory_ticks`, and
    SHALL treat it as valid exactly while `current_tick < expires_at_tick`.
11. WHEN a `producer_ref` later dies, is deleted, or cannot be resolved, THE
    Ordnance_System SHALL retain the Designation until its normal expiry because
    producer identity is informational after the observation is made.

### Requirement 3: Designation Persistence, Capacity, and Sharing

**User Story:** As a player, I want observations to survive restart and be
shareable only by live consent, while their storage and cleanup remain bounded.

#### Acceptance Criteria

1. THE Ordnance_System SHALL persist every Designation by value on its
   Designation_Holder in a per-planet `designations` container using
   read-copy-write replacement; stored nested containers SHALL share no mutable
   identity with the live value.
2. THE persisted Designation SHALL include every field in Requirement 2.1 as a
   plain value or stable reference value, so rebuilding it does not depend on an
   in-memory producer, target entity, or object graph.
3. ON holder load or first access and in an explicit maintenance/write path
   before a bucket is made available to listing, counting, selection, sharing,
   or reservation, THE Ordnance_System SHALL reconcile that bucket's
   reservations by Requirement 5.10 and only then prune entries for which
   `current_tick >= expires_at_tick`. Read-only listing and validation SHALL use
   the last confirmed reconciled snapshot and SHALL not repair or mutate it; an
   incomplete or `indeterminate` reconciliation SHALL fail closed. A live or
   indeterminate Acceptance_Transaction SHALL never be treated as an orphan, and
   uncertainty SHALL never make its Designation reusable.
4. THE Ordnance_System SHALL serialize every maintenance, prune, admission,
   reserve, commit, and rollback write to one holder/planet bucket so a live
   transaction cannot be mistaken for an orphan by a re-entrant access.
5. THE `designation_cap` SHALL bound the total unexpired `available` plus
   `reserved` Designations for one holder on one planet; a reservation SHALL not
   create capacity for another Designation.
6. IF a new Designation would make that available-plus-reserved count exceed
   `designation_cap`, THEN THE Ordnance_System SHALL refuse it and SHALL report
   the current count and cap without changing the bucket.
7. WHEN a holder lists Designations, THE Ordnance_System SHALL return structured
   `designation_id`, planet, coordinate, producer kind, Primary_Target_Owner
   identity, reservation state, and `max(0, expires_at_tick - current_tick)` for
   each unexpired entry.
8. WHEN a requester selects a Designation, THE requester SHALL identify it by
   `designation_id`; THE Ordnance_System SHALL NOT choose ambiguously among
   duplicate coordinates or among multiple allied holders.
9. WHERE the selected Designation belongs to another player, THE
   Ordnance_System SHALL accept it only while the holder and requester are
   currently allied and
   `BranchSystem.has_consent(holder, "target_sharing", requester)` is true,
   including the serialized recheck immediately before reservation under
   Requirement 5.3.
10. IF the holder has not granted `target_sharing`, or the players cease to be
    allied, THEN THE Ordnance_System SHALL refuse use of that allied Designation
    without copying, transferring, reserving, or consuming it.
11. THE Ordnance_System SHALL rebuild or lazily read Designations only from
    their holders and SHALL reconcile reservations from rebuilt transactions and
    operations on holder load/first access or an explicit maintenance/write
    path; it SHALL NOT discover Designations through a full-world or
    object-database scan.

### Requirement 4: Primary Targeting and Strike Admission

**User Story:** As a defender, I want the actual observed hostile owner to pass
the shared protection gates, so that an area coordinate cannot bypass shields,
alliances, or escalation limits.

#### Acceptance Criteria

1. WHERE a player holds the `weapons` Branch_Commitment, WHEN that player
   requests a Strategic_Strike from an owned Operational Ordnance Works using an
   unexpired Designation ID and that player's selected eligible `spotter`, THE
   Ordnance_System SHALL submit the request through the inherited ordered
   validation chain as a hostile operation.
2. WHEN `validate_target` resolves the selected Designation, THE
   Ordnance_System SHALL copy its canonical planet and coordinate into the
   request context and SHALL put its `primary_target_owner_ref` in `ctx.target`
   without consuming or reserving the Designation.
3. THE target check SHALL call
   `BranchSystem.may_target(requester, primary_target_owner_ref, hostile=True)`
   at strike request, so the current new-player shield, current allied-target
   refusal, and current escalation limit apply to the Primary_Target_Owner.
4. IF the Primary_Target_Owner is absent or cannot be presented to
   `BranchSystem.may_target`, THEN THE Ordnance_System SHALL fail closed with a
   structured target refusal rather than treat the coordinate as an unprotected
   target.
5. IF the Designation's planet differs from the originating Ordnance Works'
   canonical planet, or its canonical coordinate is no longer valid on that
   planet, THEN THE Ordnance_System SHALL refuse without reserving or consuming
   it.
6. WHEN a Strategic_Strike record is built, THE Ordnance_System SHALL put the
   same immutable `primary_target_owner_ref` in
   `OperationRecord.target_ref`; THE target reference SHALL be policy and
   escalation metadata and SHALL NOT move the impact coordinate with the target.
7. WHEN a hostile Strategic_Strike durably resolves, THE pre-reserved
   post-resolution outbox entry SHALL invoke exactly
   `BranchSystem.note_escalation_once(owner_ref, target_ref, resolved_tick,
   "resolve:{op_id}:escalation")`. It SHALL record at most one escalation
   against `OperationRecord.target_ref`, SHALL use the snapshotted resolved tick,
   and SHALL create none for collateral owners. Retry and restart SHALL reuse
   that same key and payload, so an original `applied` outcome records the one
   escalation, `duplicate(prior=applied)` ensures that same recorded escalation
   without a second one, and an original `rejected` outcome or
   `duplicate(prior=rejected)` records none while settling the entry with its
   immutable domain reason. A `conflict` SHALL fail closed and quarantine the
   entry rather than record a second escalation. Its slot SHALL be part of the
   exact impact reservation required by Requirement 8.8 rather than admitted
   after terminal state.
8. THE Ordnance_System SHALL never make the requester or a current ally the
   Primary_Target_Owner, but SHALL treat requester-owned and allied entities
   found dynamically inside a legitimately targeted impact area as valid
   indiscriminate collateral.
9. WHEN impact evaluates an entity owned by a hostile collateral owner other
   than the Primary_Target_Owner, THE Ordnance_System SHALL call exactly the
   public, read-only `BranchSystem.is_vector_shielded(target_owner)` query. The
   query SHALL read only that owner's **current** new-player vector-shield state
   and SHALL apply no alliance, escalation, cooldown, or other `may_target`
   rule. IF `target_owner` is absent or cannot be resolved, IF the query is
   unavailable or fails, or IF its result is not an explicit Boolean, THEN THE
   Ordnance_System SHALL fail closed by treating that hostile collateral owner
   as shielded. THE Ordnance_System SHALL skip all entities for an owner whose
   result is `True` or fail-closed and SHALL retain hostile collateral only on
   an explicit `False`; it SHALL NOT call full `may_target` for collateral
   because attacker-owned and allied collateral remains valid and collateral
   creates no alliance or escalation decision.

### Requirement 5: Atomic Designation Consumption

**User Story:** As a player, I want a failed launch to restore my resources and
observation once rollback is durably safe, while an indeterminate durable state
remains reserved rather than risk a duplicate strike.

#### Acceptance Criteria

1. DURING all inherited validation checks, THE OperationDriver and
   Ordnance_System SHALL treat Designation lookup, alliance, consent, warning
   recipient enumeration, and resource sufficiency as read-only; validation
   SHALL NOT consume, reserve, delete, or mutate a Designation or ledger.
2. AFTER the pure validation chain passes, THE OperationDriver SHALL preallocate
   `op_id` and durably persist one `Acceptance_Transaction` under that key before
   its first domain mutation. Its phases SHALL include at least `reserved`,
   `charged`, `pending_confirmed`, `committed`, `compensating`, `compensated`,
   and `indeterminate`; it SHALL retain the reservation identity, Designation
   and holder identities, operation linkage, exact charged cost, charge/refund/
   cooldown keys and receipts, the exact initial-warning recipient manifest and
   `accept:{op_id}:initial-warning` outbox reservation, warning marker and entry
   receipts, and every compensation receipt. Every transaction write SHALL
   return `Persistence_Result`.
3. ONLY after the initial-warning outbox reservation in criterion 4 is confirmed,
   IMMEDIATELY before Designation reservation linearizes and while holding the
   one holder/planet serialization boundary, THE Acceptance_Transaction SHALL
   re-resolve the canonical holder and selected `designation_id`, confirm the
   same planet, immutable payload, unexpired lifetime, and `available` state,
   and, for an allied holder, re-evaluate current alliance and
   `BranchSystem.has_consent(holder, "target_sharing", requester)`. It SHALL
   atomically persist the Designation reservation and transaction phase
   `reserved` only if every recheck passes, and SHALL continue only on
   `Persistence_Result.confirmed`; a consent revocation, alliance change,
   expiry, holder mismatch, missing value, competing reservation, rejected
   write, or indeterminate write SHALL cause no charge, cooldown, operation, or
   consumption, and indeterminate state SHALL retain the Designation claim for
   readback even when an earlier read passed. A definitive recheck refusal SHALL
   durably abandon the acceptance manifest and call
   `release_once("accept:{op_id}:initial-warning")`; an indeterminate
   Designation or outbox fact SHALL retain its respective claim and SHALL not be
   treated as releasable absence.
4. AFTER the transaction is first persisted but BEFORE criterion 3 may reserve a
   Designation or any charge may occur, THE Ordnance_System SHALL use the one
   bounded area query to form and canonicalize the initial owner-recipient union
   required by Requirement 7.3, coalescing the Primary_Target_Owner and area
   occupants. IF its distinct owner count exceeds the current
   `strategic_strike_warning_receipt_cap`, THE transaction SHALL refuse without
   Designation reservation or charge. Otherwise it SHALL snapshot the accepted
   set and cap by value and call
   `Post_Commit_Outbox.reserve_once("accept:{op_id}:initial-warning",
   distinct_owner_count)`, reserving exactly one initial-warning slot per stable
   owner. Only an original `applied` reservation or a matching
   `duplicate(prior=applied)` SHALL permit criterion 3 to run. Insufficient
   capacity SHALL produce a structured refusal before Designation reservation or
   charge, SHALL leave that claimless `rejected` retriable under the same
   reservation ID, and SHALL never be reported as `duplicate(prior=rejected)`;
   `conflict` SHALL quarantine the transaction; and `indeterminate` SHALL retain
   the outbox claim for confirming readback under the same reservation ID while
   performing no Designation, ledger, cooldown, or operation mutation.
   Enumeration order, duplicate bodies, later movement, or hot reload SHALL not
   change the snapshotted manifest or slot count.
5. AFTER a confirmed Designation reservation, THE transaction SHALL call
   `BranchSystem.charge_once` with the exact snapshotted cost and
   `accept:{op_id}:charge`. Only an original `applied` charge receipt or a
   matching `duplicate(prior=applied)` SHALL authorize a confirming transaction
   write of phase `charged`; an original `rejected` charge or its
   `duplicate(prior=rejected)` replay SHALL move no resources and SHALL refuse
   the launch under criterion 7. Only after that phase is confirmed SHALL it
   build the record with `op_id`,
   track it as non-tick-eligible, and ask the single transition writer to enter
   Pending. A confirmed Pending persistence/readback and confirmed transaction
   phase `pending_confirmed` are both required to continue; `conflict`,
   `rejected`, or `indeterminate` at any boundary SHALL fail closed according to
   criteria 7 and 10.
6. AFTER Pending is confirmed, THE transaction SHALL atomically consume the
   matching reserved Designation and persist phase `committed`. Post-commit
   recovery SHALL call
   `BranchSystem.note_cooldown_once(building, "strategic_strike", ready_at,
   "accept:{op_id}:cooldown")` with the snapshotted `ready_at`, then durably
   publish the initial Warning_Area marker and append exactly one bounded
   initial-warning event per snapshotted stable owner through
   `append_reserved("accept:{op_id}:initial-warning", ...)`. Each append SHALL
   reuse its original event ID and payload, so `duplicate(prior=applied)` ensures
   that one already-created entry rather than a second one. After the complete
   manifest is durably appended it SHALL call
   `release_once("accept:{op_id}:initial-warning")` to close any zero remainder.
   A request SHALL be acknowledged and the operation SHALL become tick-eligible
   only after the transaction is confirmed `committed`, Pending is confirmed,
   cooldown is an original `applied` outcome or a matching
   `duplicate(prior=applied)`, the Warning_Area marker and every reserved
   initial-warning entry are durable, and reservation closure is confirmed.
   Notification delivery itself MAY replay after acknowledgment; each live entry
   continues to consume global outbox capacity until settled.
7. IF any refusal, exception, conflicting mutation, failed charge, failed build,
   failed track, failed transition, rejected persistence, or other failure occurs
   after Designation reserve but **before** phase `committed` is durably
   confirmed, THE transaction SHALL enter `compensating` and act only on durably
   knowable facts. Before Pending has or may have persisted, it SHALL remove only
   confirmed non-lifecycle partial artifacts, call `refund_once` only against a
   confirmed original `applied` charge receipt, leave cooldown unchanged, and
   restore the original Designation. Once Pending has or may have persisted, only
   OperationDriver's
   transition writer may move it to `Cancelled`; durable `Cancelled`
   confirmation and transition-writer cleanup SHALL precede `refund_once` and
   Designation restoration. Ordnance SHALL never directly mutate, untrack,
   remove, or delete a possibly Pending operation.

   IF terminal persistence, cleanup, refund, restoration, initial-warning
   reservation/entry closure, or their readback is `indeterminate`, THE
   transaction SHALL persist or retain phase `indeterminate`, the Designation
   and outbox claims, operation linkage, exact cost, and all known receipts. It
   SHALL NOT report rollback complete, issue another unkeyed refund, restore or
   release the Designation, release a possibly live outbox claim, delete the last
   confirmed operation, or infer absence. Recovery SHALL retry the same
   deterministic keys. Once pre-commit compensation and abandonment of the
   warning manifest are confirmed, it SHALL call the same
   `release_once("accept:{op_id}:initial-warning")`; release conflict or
   indeterminacy remains quarantined or pending rather than freeing capacity by
   assumption.

   AFTER `committed` is durably confirmed, a rejected, conflicting, or
   indeterminate cooldown, Warning_Area, append, delivery, or outbox result SHALL
   NOT enter compensation, cancel the operation, refund, or restore the consumed
   Designation. THE committed operation SHALL remain non-tick-eligible and
   unacknowledged while matching marker, append, and closure work is retried; a
   payload conflict or other definitive inconsistency SHALL quarantine the
   committed transaction for operator-visible reconciliation rather than choose
   a second acceptance or rollback outcome.
8. `BranchSystem.refund_once(player, exact_charged_cost,
   "accept:{op_id}:refund", "accept:{op_id}:charge")` SHALL be the only refund
   path. It SHALL apply only against a linked original `applied` charge receipt
   and SHALL atomically retain its own payload-hash/original-outcome receipt, so
   restart, repeated compensation, and duplicate events restore the whole charge
   at most once and never reread a hot-reloaded cost. A matching
   `duplicate(prior=applied)` SHALL return the one original refund receipt
   without moving resources again, and a `duplicate(prior=rejected)` SHALL
   restore nothing; a linked charge that is absent, an original `rejected`, or
   only indeterminate SHALL authorize no refund.
9. WHEN duplicate or re-entrant requests name the same `designation_id`, THE
   holder/planet serialization boundary SHALL allow at most one reservation and
   one matching Acceptance_Transaction to proceed toward Pending. Every loser
   SHALL receive a structured no-mutation refusal; an `indeterminate` winner
   remains the winner until confirmed readback resolves it.
10. WHEN restart reconciliation finds a reserved Designation, an unfinished
    Acceptance_Transaction, or an open/indeterminate initial-warning outbox
    reservation, THE OperationDriver and Ordnance_System SHALL compare
    transaction phase and receipts, Designation reservation ID, operation ID,
    Designation ID, holder identity, the exact owner/event manifest, outbox
    reservation/entry receipts, and confirmed operation persistence. A confirmed
    committed transaction with a matching non-terminal operation SHALL consume
    any leftover Designation reservation, rebuild the operation, and
    idempotently finish its cooldown, Warning_Area, reserved entry append, and
    reservation closure; it SHALL never restore the Designation beside that
    operation. A confirmed pre-commit `Cancelled` settlement, or a positive
    readable store result that authoritatively confirms no matching operation,
    SHALL finish transition cleanup, keyed refund, restoration, durable manifest
    abandonment, and `release_once` before applying expiry. A matching terminal
    operation reached after committed acceptance SHALL consume any leftover
    Designation reservation, SHALL not refund, and SHALL retain its source until
    all appended entries settle. Unreadable storage, conflicting records or
    receipts, an indeterminate outbox claim, a timed-out write, or any other
    `indeterminate` fact SHALL retain and isolate every Designation and outbox
    claim for retry; matching-operation or reservation absence SHALL never be
    inferred from unreadability.
11. THE Designation cap SHALL continue to count a persisted reservation until a
    committed transaction consumes it or confirmed compensation/reconciliation
    restores it. A restored value SHALL preserve all original immutable fields
    and only then undergo the ordinary expiry rule.

### Requirement 6: Durable Operation Metadata, Snapshots, and Carrier

**User Story:** As an operator, I want an in-flight strike to preserve its
identity, warning, damage, counters, and lifecycle owner across restart and hot
reload.

#### Acceptance Criteria

1. A newly constructed in-memory `OperationRecord()` SHALL default
   `schema_version = 1`, while `OperationRecord.from_dict({})` SHALL decode to
   legacy `schema_version = 0`. On read, an absent version, a Boolean, or any
   malformed value whose exact type is not `int` SHALL decode to `0`; any present
   exact non-Boolean integer SHALL be preserved verbatim. Only versions `0` and
   `1` are supported. Every other readable integer, including a negative or
   future version, SHALL be quarantined and reported without partial
   interpretation, migration, or rewrite. Every current new record and every
   successful migration SHALL write exactly version `1`.
2. A missing, non-mapping, or otherwise unreadable `vector_data` SHALL decode as
   a newly allocated `{}` unique to that read. The serializer SHALL recursively
   copy `vector_data` and every nested mutable container by value in both
   directions. The fresh fallback SHALL NOT exempt a version-1 record from
   required-metadata validation; an incomplete version-1 Strategic_Strike SHALL
   be isolated and reported.
3. THE Ordnance `vector_data` schema SHALL persist at least
   `designation_id`, `designation_holder_ref`, `producer_kind`, optional
   `producer_ref`, `primary_target_owner_ref`, `raw_base_damage`,
   `strike_radius`, `flight_ticks_at_acceptance`,
   `response_window_floor_ticks`, `warning_published_tick`,
   `warning_receipt_cap_at_acceptance`, the bounded immutable warning receipt
   map, initial-warning outbox reservation linkage, cumulative accepted
   `interception_fraction`, cumulative accepted `disruption_ticks`, bounded
   immutable counter-action receipt maps containing each key, payload hash, and
   immutable original `applied` or `rejected` outcome with its domain reason, the
   permanently frozen interception value when present,
   snapshotted `agent_xp_strategic_strike`, and durable
   Acceptance_Transaction, Strike_Resolution_Transaction,
   Combat_Hit_Transaction-reference, and impact-outbox-reservation linkage.
4. WHEN a legacy version `0` Strategic_Strike receives the fresh `{}` fallback
   because `vector_data` is missing or unreadable, THE rebuild SHALL derive only
   values unambiguously available from shipped top-level record fields, SHALL
   apply documented backward-compatible defaults only where safe, and SHALL
   isolate and report a record that cannot satisfy the new target, damage,
   warning, or transaction contract rather than invent metadata.
5. WHEN a Strategic_Strike is accepted, THE Ordnance_System SHALL snapshot
   `record.magnitude` and `vector_data.raw_base_damage` to exactly
   `strategic_strike_damage * max(1, origin_building_level)`, using the origin's
   validated integer level at acceptance, and SHALL snapshot `record.radius`
   and `vector_data.strike_radius` to `strategic_strike_radius`.
6. WHEN a Strategic_Strike is accepted, THE Ordnance_System SHALL snapshot the
   configured response-floor value, flight ticks, cooldown ready tick, warning
   receipt cap, and XP amount; SHALL set `record.target_x` and `record.target_y`
   to the immutable canonical center; and SHALL keep top-level magnitude/radius
   equal to their `vector_data` mirrors on every new write and migration. The
   actual floored clock and immutable `warning_published_tick` SHALL be set once
   at durable initial Warning_Area publication under Requirement 7, not during a
   later resume.
7. THE `OperationRecord.carrier_ref` SHALL be the launching player's selected
   eligible `spotter`, including when an ally supplied the Designation or a
   Detection_Sweep produced it; THE designation producer SHALL remain
   informational and SHALL never replace this lifecycle carrier.
8. AFTER all impact candidates and their required engine downstream intents are
   terminal and the Resolved transition is durably confirmed, THE pre-reserved
   post-resolution outbox entry SHALL request exactly
   `AgentSystem.award_operation_xp_once(carrier_ref, "strategic_strike",
   snapshotted_agent_xp, "resolve:{op_id}:xp")`. Retry and restart MAY repeat the
   keyed request but SHALL award at most once: an original `applied` outcome
   awards the snapshotted amount, and `duplicate(prior=applied)` ensures that one
   award without a second. No producer or other agent SHALL receive
   Strategic_Strike XP, and an unresolved carrier SHALL produce an original
   `rejected` outcome carrying that immutable domain reason, which with its
   `duplicate(prior=rejected)` replay awards nothing and settles this reserved
   entry rather than redirect the award.
9. WHEN a producer reference cannot be resolved during operation rebuild, THE
   operation SHALL retain its producer identity metadata and continue; producer
   availability SHALL NOT become a lifecycle requirement.
10. WHEN restart rebuild reads a valid non-terminal Strategic_Strike, THE
    OperationDriver SHALL restore its owner, origin, lifecycle carrier, Primary
    Target Owner, coordinate, exact clocks, radius, raw base damage, warning
    metadata and receipts, Designation and acceptance identity, initial-warning
    reservation state, counter totals and receipts, frozen interception if any,
    finite canonical candidate count and resolution progress if prepared,
    Combat_Hit_Transaction references and downstream receipts, and the exact
    impact outbox manifest/reservation before re-tracking it.
11. THE Acceptance_Transaction, Strike_Resolution_Transaction,
    Combat_Hit_Transaction, Mutation_Result receipts, and Post_Commit_Outbox
    SHALL remain authoritative independently of lifecycle terminality and until
    all required entries settle. Rebuild SHALL reconcile those durable facts
    before reissuing a mutation or notification and SHALL quarantine a
    conflicting payload hash rather than overwrite or reinterpret it. A source
    OperationRecord SHALL not be removed while any related reservation is open
    or indeterminate or any related outbox entry is live and unsettled.

### Requirement 7: Public Area Warning and Movement Response

**User Story:** As a player near an inbound strike, I want its whole danger area
visible for the flight, while accepting that entering an already warned area
late gives me only the time that remains.

#### Acceptance Criteria

1. AFTER Requirement 5's transaction is confirmed committed, Pending and
   cooldown are confirmed, THE Ordnance_System SHALL durably publish exactly one
   initial public Warning_Area marker containing operation ID, planet, canonical
   center, radius, attacker identity, current state, publication tick, and ticks
   remaining. In that same confirming publication boundary it SHALL evaluate the
   response floor exactly once from the accepted flight and snapshotted floor,
   set immutable `warning_published_tick`, and persist the authoritative clock.
   It SHALL append the exact pre-acceptance owner manifest to the already
   confirmed `accept:{op_id}:initial-warning` reservation before making the
   operation tick-eligible; it SHALL NOT attempt fresh capacity admission after
   charge or committed acceptance.
2. THE time from initial Warning_Area publication to the earliest permitted
   impact SHALL be at least the snapshotted
   `minimum_response_window_ticks`; interception and Counter_Web SHALL NOT
   shorten that time, and disruption or suspension may only lengthen it.
   Suspension SHALL hold the exact authoritative remaining ticks, and resume
   SHALL restore exactly that value without calling `response_window`, reading
   current balance, republishing the initial marker, or applying any floor again.
3. THE initial owner-recipient snapshot computed before Designation reservation
   and charge under Requirement 5.4 SHALL coalesce the inherited
   Primary_Target_Owner recipient with every current player and every resolvable
   owner of an entity returned by the one bounded area query, without excluding
   the launching owner or allies. It SHALL be keyed by stable owner identity,
   contain no duplicate owner, and fit the snapshotted warning receipt cap. Its
   exact distinct-owner count SHALL be the initial-warning reservation slot
   count. Each owner SHALL use one bounded event ID derived from operation,
   initial-warning kind, and stable owner and exactly one `append_reserved`
   entry, so an owner receives at most one initial warning despite path overlap
   or multiple bodies. A distinct later-entry event remains eligible under
   criterion 5 and is not pre-reserved here.
4. WHILE a Strategic_Strike is Pending or Suspended, THE Warning_Area SHALL
   remain publicly queryable and SHALL report the operation's current state and
   authoritative remaining clock, including every confirmed disruption.
5. WHEN a player or owned entity enters a live Warning_Area after publication,
   THE movement/event integration SHALL derive bounded event and reservation IDs
   from operation, later-warning kind, and canonical owner. Each warning receipt
   SHALL atomically persist its key, payload hash, and original `applied` or
   `rejected` outcome; the same key and payload SHALL return
   `duplicate(prior=<original_outcome>)`, where `duplicate(prior=applied)`
   ensures the one already-created entry and delivery without dispatching twice
   and `duplicate(prior=rejected)` authorizes none, and the same key with another
   payload SHALL `conflict` and fail closed. For an unknown owner below the
   snapshotted `strategic_strike_warning_receipt_cap`, THE system SHALL first
   call `reserve_once` for exactly one slot. Only an original `applied` or
   matching `duplicate(prior=applied)` reservation may be followed by
   `append_reserved` and delivery through `publish_once` of the operation ID,
   planet, center, radius, and then-current **remaining** ticks; after durable
   append it SHALL close the reservation with `release_once`. If warning-receipt
   capacity is reached or shared outbox capacity returns a claimless `rejected`
   for the one-slot reservation, the optional direct delivery SHALL be suppressed
   with no unkeyed fallback and no warning receipt while the public Warning_Area
   remains queryable; that retriable refusal SHALL never be reported as
   `duplicate(prior=rejected)`. An indeterminate reservation SHALL retain its
   claim for readback, emit no direct delivery, and neither retry under a new ID
   nor assume a free slot. Saturation and indeterminacy SHALL be logged and
   exposed as structured operational status without restarting or flooring the
   clock.
6. THE response-window guarantee SHALL belong to the duration of the publicly
   marked area and SHALL NOT promise every future occupant a personal minimum
   warning; a player knowingly entering late MAY receive fewer than
   `minimum_response_window_ticks` before impact.
7. AFTER a Strategic_Strike becomes durably Resolved, Expired, Cancelled, or
   Discarded, THE Ordnance_System SHALL remove its Warning_Area idempotently and
   disable every new later-entry path. Existing warning receipts, live entries,
   and confirmed or indeterminate reservations SHALL remain replayable or
   reconcilable until the terminal state, absence of a live warning path, entry
   settlement, and reservation closure are all confirmed. Pending or ambiguous
   delivery SHALL never keep the public marker live after confirmed terminal
   state, but its outbox fact SHALL remain recoverable and the source operation
   SHALL not be removed. Only after those conditions MAY live warning detail be
   compacted into finite-retention replay tombstones under the global outbox
   contract.
8. THE Primary_Target_Owner's `target_ref` SHALL NOT force that owner or a moved
   entity into the impact area, damage set, or area-current-occupant subset of
   the initial notification set; all such membership SHALL be determined from
   the canonical area and current location. This SHALL NOT suppress the
   inherited Primary_Target_Owner notification recipient coalesced under
   criterion 3.
9. EVERY warning reservation ID, event ID, and payload SHALL use fixed-schema
   stable references, bounded operation/kind fields, and recipient collections
   bounded by `strategic_strike_warning_receipt_cap`; it SHALL NOT copy
   unbounded entity lists, display names, or arbitrary caller data into the
   outbox. Initial warning slot demand SHALL equal the exact canonical owner
   union count, and every optional later warning SHALL demand exactly one slot.
   A non-raising unkeyed notification plus a local warned flag SHALL NOT satisfy
   any warning delivery, capacity, or dedupe requirement.

### Requirement 8: Impact Selection and Damage

**User Story:** As a player in the blast, I want one consistent blast calculation
per victim owner and the normal combat pipeline to decide shields, resists, and
destruction.

#### Acceptance Criteria

1. WHEN Strategic_Strikes become due, THE Ordnance_System SHALL process due
   operations in ascending `op_id`. Before selecting or damaging any victim, it
   SHALL durably create or resume one `Strike_Resolution_Transaction` keyed by
   `op_id`, persist its resolution epoch/tick and phase, and permanently freeze
   the confirmed cumulative interception as
   `frozen_interception_fraction`. A confirmed freeze SHALL never be recomputed
   or rewritten by retry, restart, a later action, or hot reload; a rejected or
   indeterminate freeze SHALL leave the strike tracked at clock `0` with no
   damage.
2. DURING the one successful prepare phase, THE Ordnance_System SHALL resolve the
   canonical planet's injected spatial index or cached room once, perform one
   bounded-square query that returns a finite materialized occupant collection,
   filter by each candidate's current Chebyshev distance, canonicalize one entry
   per stable entity ID, and persist an immutable candidate snapshot in ascending
   stable-ID order together with its exact nonnegative `candidate_count`. The
   snapshot SHALL include stable identity and the immutable effect inputs needed
   for replay; movement after this snapshot SHALL not change this resolution
   epoch. A prepare retry SHALL reuse the persisted snapshot and SHALL not
   enumerate again. An unavailable, streaming, incomplete, or otherwise
   non-finite query result SHALL be `indeterminate`, SHALL persist no partial
   snapshot, and SHALL permit no impact.
3. THE candidate snapshot SHALL include combat-capable players, agents, and
   buildings in radius even when inside closed cover; THE blast weapon SHALL use
   the `blast` damage type and existing `blast_resist` and bomb-equivalent
   cover-breaching semantics.
4. THE impact filter SHALL retain entities owned by the attacker and entities
   allied to the attacker as indiscriminate collateral, and SHALL retain
   entities of the Primary_Target_Owner when in the area. FOR every other
   hostile collateral owner, THE Ordnance_System SHALL apply exactly the
   fail-closed `BranchSystem.is_vector_shielded` contract in Requirement 4.9.
   The transaction SHALL persist each canonical retention or shield-skip outcome
   so retry does not reevaluate policy after damage has begun.
5. THE transaction SHALL group retained candidates by distinct canonical victim
   owner/Branch pair and call
   `BranchSystem.counter_multiplier_checked("weapons", victim_branch)` exactly
   once per resolvable pair, in deterministic pair order, before victim damage.
   It SHALL persist and reuse exactly one of the four result variants
   `neutral(1.0)`, `advantage(multiplier)`, `unavailable(reason)`, or
   `invalid(reason)` for every candidate in that pair; adding candidates or
   retries SHALL not add lookups or compound an edge.
6. FOR EACH retained victim authorized by criterion 5, THE pre-combat raw
   magnitude SHALL be exactly
   `max(1, floor(raw_base_damage * one_counter_multiplier * (1 - frozen_interception_fraction)))`.
   `one_counter_multiplier` SHALL be extracted only from a persisted
   `neutral(1.0)` or valid finite non-Boolean `advantage(multiplier)` result. THE
   Ordnance_System SHALL perform no intermediate rounding, truncation, repeated
   lookup, edge multiplication, or flight-time reduction and SHALL apply
   `floor` then `max(1, ...)` exactly once.
7. FOR any resolvable victim pair whose checked outcome is
   `unavailable(reason)` or `invalid(reason)`, especially a hostile pair, THE
   transaction SHALL durably mark every candidate in that pair
   `skipped_counter_unavailable`, apply no damage to that pair, log the reason,
   and continue other pairs. Any malformed, non-finite, Boolean, or otherwise
   invalid checked result SHALL be classified as `invalid(reason)` and follow
   the same durable fail-closed pair-skip behavior. Only a truly ownerless or
   unbranchable retained non-hostile/world entity MAY receive an explicit
   persisted neutral `1.0` classification without a lookup. Neutral SHALL never
   be an error fallback or turn an unreadable hostile shield/Branch decision
   into a retained victim.
8. BEFORE the first combat mutation, THE Strike_Resolution_Transaction SHALL
   persist and confirm the complete per-victim progress map for every
   snapshotted candidate. Each entry SHALL contain its stable ID, deterministic
   `strike:{op_id}:victim:{stable_id}` mutation key, immutable hit-payload hash
   inputs, and an initial typed `skipped` state or `pending` state. Definitive
   pre-engine failures, including a disappeared candidate or failed weapon
   construction, SHALL be persisted as typed skips before outbox admission. For
   every retained, Counter-Web-authorized pending candidate, CombatEngine's pure
   pre-hit planning rules SHALL derive from the immutable target kind, weapon,
   attacker, and context a fixed-schema bounded potential-downstream-intent
   manifest with one deterministic event/intent key and one required reserved
   slot per intent. The manifest SHALL cover every consequence class the engine
   may require for that request, SHALL have no hidden recipient or work fan-out,
   and SHALL settle an intent durably as an original `rejected` no-op receipt
   carrying its immutable domain reason when the core outcome makes it
   inapplicable. The exact finite `hit_ready_count` SHALL equal the number of
   those pending candidates and SHALL be no greater than the persisted
   `candidate_count`; `engine_intent_slot_count` SHALL equal the exact sum of
   their manifest cardinalities. The fixed per-candidate schema bound and finite
   candidate snapshot SHALL therefore make `engine_intent_slot_count` finite.

   Before any hit, THE transaction SHALL persist an exact impact outbox manifest
   consisting of every candidate engine intent slot, one bounded
   resolution-notification batch entry whose recipients are the immutable
   initial-warning owner union, one Primary_Target_Owner escalation entry, and
   one carrier-XP entry. It SHALL call
   `Post_Commit_Outbox.reserve_once("resolve:{op_id}:outbox",
   engine_intent_slot_count + 3)`. Only an original `applied` reservation or a
   matching `duplicate(prior=applied)` SHALL authorize combat. Insufficient
   capacity SHALL return a claimless `rejected` that leaves the existing strike
   tracked and counting as in flight at clock `0`, with no hit or terminal
   transition, and that remains retriable under the same reservation ID rather
   than replaying as `duplicate(prior=rejected)`; `indeterminate` SHALL retain
   the claim and do the same pending confirming readback under that same
   reservation ID; and `conflict` SHALL quarantine the resolution without combat.
   The system SHALL neither truncate candidates, omit an engine intent, batch
   hidden fan-out into an uncounted slot, nor split admission into opportunistic
   partial reservations.

   FOR EACH hit-ready candidate in ascending stable-ID order, THE Ordnance_System
   SHALL construct the criterion-6 blast weapon and invoke the thin
   `OperationDriver.apply_hit_once` delegate, which SHALL call exactly
   `CombatEngine.apply_direct_hit_once(attacker, target, weapon, mutation_id,
   context)` with `attacker = OperationRecord.owner_ref`, the deterministic hit
   key, immutable strike/target inputs, and its exact reserved engine-intent
   manifest in `context`. CombatEngine SHALL create or resume the engine-owned
   Combat_Hit_Transaction. In one atomic combat-store unit keyed by
   `strike:{op_id}:victim:{stable_id}`, it SHALL durably commit the immutable
   request hash; target combat-state version and preconditions; computed damage
   outcome; every authoritative shield, HP, and other target combat-state delta;
   resulting combat state; an authoritative zero-HP/death-pending marker when
   applicable; the hit receipt; and deterministic downstream intent keys exactly
   matching the pre-reserved manifest. It SHALL neither invent an unreserved
   downstream intent nor hide fan-out behind one entry. The shipped unkeyed
   `apply_direct_hit` cascade MUST NOT run before that receipt, and no
   caller-side receipt around that legacy call satisfies this boundary. An
   original `applied` outcome means only that this core combat unit is confirmed;
   a matching `duplicate(prior=applied)` reads the same engine transaction and
   never reapplies its deltas. An original `rejected` core outcome SHALL commit a
   receipt with an immutable domain reason and no combat-state delta, and its
   `duplicate(prior=rejected)` replay SHALL apply nothing.
9. THE CombatEngine atomic unit in criterion 8 SHALL preserve the ordinary
   shield-absorption, `blast_resist`, armor, chip-damage, rank-gap, attribution,
   and zero-HP formulas and authority. Ordnance SHALL never directly edit HP,
   shields, death state, XP/loot, or ownership and SHALL never directly delete a
   building. Normal consequences that cannot share the combat atomic store,
   including death routing, rank-gap XP/loot, respawn or building destruction,
   events, and notifications, SHALL instead be durable engine-owned keyed and/or
   reserved-outbox work derived only from an original `applied` hit receipt. Each
   pre-reserved engine-intent entry SHALL resume that same hit key and its one
   deterministic intent key until the required receipt confirms, and each
   consequence SHALL apply at most once: an original `applied` intent outcome
   performs it, `duplicate(prior=applied)` ensures that one performance without a
   second, and an original `rejected` intent outcome or its
   `duplicate(prior=rejected)` replay performs none while settling the intent
   with its immutable domain reason. A target marked death-pending at zero HP
   SHALL be non-actionable for combat, movement, commands, targeting, or other
   gameplay until keyed death settlement confirms; only CombatEngine's normal
   keyed settlement may route death, respawn, or destruction.
10. A definitive failure established before `apply_direct_hit_once` and before
    any combat mutation SHALL remain a durable typed `skipped` outcome with
    operation ID, coordinate, and candidate or pair. Once the engine API is
    invoked, an original core `applied` outcome or a matching
    `duplicate(prior=applied)` SHALL move progress only to
    `core_applied_pending_downstream`; the candidate SHALL become terminal
    `applied_settled` only after every entry in its reserved engine-intent
    manifest and every corresponding engine receipt are durably confirmed,
    counting an original `rejected` no-op receipt for an inapplicable intent as
    confirmed settlement. An original core `rejected` outcome, or its
    `duplicate(prior=rejected)` replay, SHALL be terminal for that candidate as a
    durable typed `skipped_by_engine` progress value retaining that receipt and
    immutable domain reason, with no combat delta and no downstream intent
    performed. A `rejected` engine or downstream result that records no original
    receipt, being a transient refusal, and every `indeterminate` result SHALL
    remain pending for the same-key retry/readback and SHALL never be read as an
    original rejection. A payload/precondition `conflict`, changed target
    identity/version under the immutable request, or downstream receipt
    contradiction SHALL quarantine that candidate and resolution for
    operator-visible reconciliation; none SHALL be converted to skipped or
    applied. Failure of one candidate or pair SHALL not suppress processing of
    later independent candidates in canonical order, but the operation SHALL not
    settle while any candidate is pending or quarantined.
11. ONLY after every snapshotted candidate has a durable terminal progress value
    of definitive typed `skipped`, `skipped_by_engine`, or `applied_settled` SHALL
    the resolution outcome protocol ask OperationDriver's single writer to persist
    Resolved. A `duplicate(prior=applied)` core hit without confirmed downstream
    settlement is not terminal. A receiptless rejected or indeterminate
    effect/downstream commit, terminal write, or readback SHALL retain the due
    operation at clock `0` and resume only missing keyed work from
    Strike_Resolution_Transaction, Combat_Hit_Transaction, and outbox receipts; it
    SHALL never rerun a core hit that already holds an original `applied` or
    `rejected` receipt, invoke the legacy unkeyed cascade, or declare a partial
    effect settled.
12. ONLY after durable Resolved confirmation SHALL the system use the already
    held `resolve:{op_id}:outbox` reservation to append the one snapshotted
    bounded resolution-notification batch, the one Primary_Target_Owner
    escalation mutation under Requirement 4.7, and the one carrier-XP mutation
    under Requirement 6.8. It SHALL perform no fresh capacity admission at this
    irreversible terminal boundary. Those keyed entries MAY replay independently
    under their original keys and payloads, so `duplicate(prior=applied)` ensures
    each one already-created entry without a second and
    `duplicate(prior=rejected)` authorizes none; no pre-terminal or indeterminate
    resolution SHALL publish, escalate, or award. After every required candidate
    engine-intent entry and all three post-resolution entries are durably
    appended, and every remaining slot belongs only to an intent that can no
    longer be authorized because its candidate is a durable typed skip or its
    intent already holds an original `rejected` no-op receipt, THE transaction
    SHALL call `release_once("resolve:{op_id}:outbox")` to close that reservation
    and release exactly those unconsumed slots; entry settlement, not append or
    release alone, remains required for final source cleanup.
13. THE Strike_Resolution_Transaction and every referenced
    Combat_Hit_Transaction SHALL remain durable through confirmed terminal
    transition, confirmed impact-reservation closure, and settlement of every
    required outbox mutation/event and engine downstream intent. The source
    OperationRecord SHALL not be removed before those confirmations. The
    transactions MAY then compact to replay-safe immutable receipts preserving
    keys, request/payload hashes, target-state preconditions, core and downstream
    outcomes, and settlement facts; shared-outbox entries and tombstones SHALL
    follow finite retention only after that durable transfer. Restart, source
    cleanup, or duplicate delivery SHALL not repeat damage, death/destruction,
    escalation, XP/loot, or a resolution notification.

### Requirement 9: Authorized Interception and Disruption Seams

**User Story:** As a defender using a sibling doctrine, I want its committed
system to counter a strike through an authenticated, durable, idempotent seam
rather than through a player command or arbitrary mutation.

#### Acceptance Criteria

1. THE composition root SHALL register only Fortification providers for
   interception and only Detection providers for disruption, and THE
   Ordnance_System SHALL issue system capabilities to those registered provider
   instances rather than expose either seam as a command-layer operation.
2. WHEN a provider requests an adjustment, THE seam SHALL accept operation ID,
   authorized actor, source entity or operation, a nonempty idempotency
   `action_id` whose UTF-8 encoding is at most 128 bytes, and requested finite
   adjustment, and SHALL return a structured outcome for every input without
   raising. Empty, overlong, or unencodable IDs SHALL be rejected without
   authorization or mutation.
3. BEFORE accepting a new under-cap adjustment, THE Ordnance_System SHALL call
   the injected registered provider's authorization service, and that provider
   SHALL verify the operation and source are on the same planet, the source is
   within provider-defined range of the Warning_Area, the actor owns or controls
   the source, the actor holds the provider's required Branch commitment, the
   source is committed to this action, and the source is currently Operational.
4. IF a caller is not the registered provider instance, lacks its capability,
   or fails any provider authorization condition, THEN THE seam SHALL return a
   structured `rejected` no-op and SHALL mutate no operation, source, receipt
   map, outbox, or notification state.
5. WHEN interception is requested before the durably persisted pre-damage freeze
   while the strike is Pending or Suspended, THE seam SHALL accept only a finite
   numeric fraction greater than `0.0` and no greater than `0.75`, rejecting
   Booleans, NaN, and infinities. Once freeze is persisted or its persistence is
   indeterminate, no new interception may be authorized.
6. WHEN a valid interception is accepted, THE seam SHALL apply
   `max(0.0, min(requested_fraction,
   strategic_strike_max_interception_fraction -
   accepted_interception_fraction))`; a positive delta SHALL never make the
   cumulative total exceed the cap in force for that action or absolute `0.75`,
   and a later lower cap SHALL prevent further reduction without rewriting a
   previously accepted total.
7. WHEN disruption is requested, THE seam SHALL accept a new key only while
   the strike is Pending or Suspended, before resolution prepare/freeze has been
   durably persisted or has an indeterminate persistence result, and only when
   requested ticks are an exact non-Boolean integer in `[1, 3600]`. A known
   receipt remains replayable regardless of this phase gate.
8. WHEN a valid disruption is accepted, THE seam SHALL apply
   `max(0, min(requested_ticks,
   strategic_strike_max_disruption_ticks - accepted_disruption_ticks))`; a
   positive delta SHALL never make the cumulative extension exceed the cap in
   force for that action, and a later lower cap SHALL prevent further extension
   without rewriting previously accepted ticks. For Pending, THE seam SHALL
   extend `ticks_remaining`; for Suspended, it SHALL extend authoritative
   `suspended_ticks` and the displayed remaining clock so exact resume cannot
   discard the extension.
9. EACH operation SHALL persist bounded immutable counter-action receipt maps
   keyed by the composite `(counter_kind, action_id)`, storing the full mutation
   key, immutable payload hash, the immutable original `applied` or `rejected`
   outcome with its domain reason, requested and applied adjustment, resulting
   cumulative total, cap used, and persistence receipt.
   The maps MAY remain partitioned by counter kind, but
   `strategic_strike_action_receipt_cap` SHALL apply once per operation to the
   combined live receipt count defined in criterion 15: the sum of live
   interception receipts and live disruption receipts across all partitions. It
   SHALL NOT provide a separate allowance per map or counter kind. For a
   positive adjustment, the exact one-slot notification reservation required by
   criterion 11 SHALL be confirmed before the adjustment. The adjustment and
   action receipt SHALL then be one atomic confirming operation write. The seam
   SHALL return an original `applied` outcome only after
   `Persistence_Result.confirmed`; a rejected write SHALL change neither
   operation fact, SHALL record no original receipt, and SHALL release the outbox
   reservation only after authoritative no-write confirmation, while an
   indeterminate write SHALL retain the reservation and prior visible operation
   state, block conflicting work, and require readback rather than being
   reported as a no-op.
10. Receipt lookup for the composite `(counter_kind, action_id)` key SHALL
    precede provider authorization, action-cap evaluation, outbox admission, and
    phase mutation. The same composite key and payload SHALL return
    `duplicate(prior=<original_outcome>)` without reauthorization,
    reapplication, repersistence, a second reservation, or renotification,
    including after restart: `duplicate(prior=applied)` SHALL retain the
    authority of the one recorded adjustment and its already-created event, while
    `duplicate(prior=rejected)`, including a `cap_reached` no-op, SHALL retain the
    original no-application decision and authorize no adjustment, reservation, or
    notification. The same composite key with another payload SHALL return
    `conflict` and fail closed.
    WHEN the operation's combined live receipt count is at or above the current
    `strategic_strike_action_receipt_cap`, an unknown composite key SHALL return
    `receipt_cap_reached` with no authorization, outbox reservation, operation
    mutation, persistence, or notification, regardless of counter kind; a
    request SHALL NOT bypass capacity by switching between interception and
    disruption. Every known composite key SHALL remain replayable. If no action
    receipt exists but the deterministic outbox reservation for this composite
    key is confirmed or indeterminate, retry SHALL reconcile that same claim and
    payload before another authorization outcome may release it or proceed; it
    SHALL never substitute another reservation ID.
11. IF an unknown operation is named, the operation is terminal, interception or
    disruption has reached or passed persisted resolution prepare/freeze, that
    prepare/freeze persistence is indeterminate, or disruption names another
    state, THEN a new under-cap key SHALL return `unknown_operation`, `terminal`,
    or `wrong_phase` as applicable without creating an action receipt or changing
    state. IF an otherwise valid, authorized, under-receipt-cap action computes
    an applied delta of zero solely because its adjustment cap is exhausted,
    THEN THE seam SHALL atomically persist that terminal domain no-op as an
    original `rejected` receipt whose immutable domain reason is `cap_reached`,
    containing the key, payload hash, cap, and prior totals, SHALL make no outbox
    reservation or notification, and SHALL replay it as
    `duplicate(prior=rejected)` even if a later hot reload raises the cap.

    For an otherwise valid, authorized action with a positive delta, THE seam
    SHALL call
    `Post_Commit_Outbox.reserve_once("counter:{op_id}:{counter_kind}:{action_id}:outbox", 1)`
    before the atomic adjustment. Insufficient shared capacity SHALL return a
    claimless `rejected` reservation surfaced as a structured
    `outbox_capacity_reached` result with no action receipt, adjustment, source
    change, or notification; because it records no original receipt, it MAY retry
    the same unknown composite key and the same reservation ID later subject to
    all ordinary gates and SHALL never be reported as
    `duplicate(prior=rejected)`. `conflict` SHALL fail closed and quarantine that
    attempt without an adjustment. `indeterminate` SHALL retain the one-slot
    claim, return an indeterminate result, and perform no adjustment until
    confirming readback. Only an original `applied` reservation or a matching
    `duplicate(prior=applied)` SHALL authorize the operation write; inability to
    reserve SHALL never be bypassed by direct delivery.
12. AFTER a confirmed interception adjustment, THE Ordnance_System SHALL use its
    confirmed one-slot reservation to `append_reserved` exactly one bounded
    `strategic_strike_intercepted` event and deliver it through `publish_once` to
    the de-duplicated launching owner and authorized counter actor. The immutable
    payload SHALL contain operation ID, action ID, actor/source identity,
    requested and applied fraction, cumulative fraction, cap, planet, center,
    and remaining ticks. Append and delivery retries SHALL reuse that original
    event ID and payload, so `duplicate(prior=applied)` ensures the one entry and
    one delivery without a second. After confirmed append it SHALL call
    `release_once` to close the fully consumed reservation. Receiptless rejected,
    conflicting, or indeterminate append/release SHALL retain the operation source
    and same claim for retry or quarantine; it SHALL never reverse or repeat the
    confirmed adjustment.
13. AFTER a confirmed disruption adjustment, THE Ordnance_System SHALL use its
    confirmed one-slot reservation to `append_reserved` exactly one bounded
    `strategic_strike_disrupted` event and deliver it through `publish_once` to
    the de-duplicated launching owner and authorized counter actor. The immutable
    payload SHALL contain operation ID, action ID, actor/source identity,
    requested and applied ticks, cumulative extension, cap, planet, center, and
    new remaining ticks. Append and delivery retries SHALL reuse that original
    event ID and payload, so `duplicate(prior=applied)` ensures the one entry and
    one delivery without a second. After confirmed append it SHALL call
    `release_once` to close the fully consumed reservation. Receiptless rejected,
    conflicting, or indeterminate append/release SHALL retain the operation source
    and same claim for retry or quarantine; it SHALL never reverse or repeat the
    confirmed adjustment.
14. THE two seams SHALL remain inert when no provider is registered, and an
    absent sibling system SHALL NOT prevent ordinary designation, launch,
    warning, movement, or impact behavior.
15. THE combined live receipt count for an operation SHALL count one receipt for
    each original `applied` outcome and each authorized original `rejected`
    `cap_reached` no-op outcome needed for stable replay across both interception
    and disruption maps. A `duplicate(prior=applied|rejected)` replay SHALL not
    increase that count, and partitioning receipts by kind SHALL not multiply the
    `strategic_strike_action_receipt_cap`. Invalid
    input, authorization rejection, `receipt_cap_reached`,
    `outbox_capacity_reached`, unknown operation, terminal, and `wrong_phase`
    SHALL create no action receipt. Live action receipts SHALL never be evicted
    and SHALL survive restart. After confirmed terminal state, settlement of
    every related counter-event entry, and closure of every related outbox
    reservation, they MAY compact to bounded replay-safe immutable
    key/hash/outcome receipts but SHALL never turn a known key into a newly
    authorizable action. Operation source removal SHALL wait for those outbox
    confirmations.

### Requirement 10: Commands and Generic Presentation

**User Story:** As a player, I want every designation, strike, warning, and
refusal rendered consistently without exposing internal exceptions or blank
message keys.

#### Acceptance Criteria

1. THE command layer SHALL provide commands to create a Designation using a
   selected spotter and concrete target, launch a Strategic_Strike using a
   Designation ID, Ordnance Works, and selected carrier, list the player's own
   Designations and in-flight strikes, and query public Warning_Areas on the
   occupied planet.
2. THE command layer SHALL NOT provide a player command that directly invokes
   interception, disruption, reservation, commit, rollback, clock mutation,
   provider authorization, receipt repair, or transaction reconciliation.
3. THE Ordnance_System and provider seams SHALL return an outcome value for
   every request and SHALL raise no exception into the command layer.
4. THE NotificationPresenter SHALL add
   `render_vector_refusal(key, data)` as the generic renderer for vector command
   refusals, and THE composition root SHALL inject that presenter entry point
   into every vector command introduced by this spec.
5. THE vector commands SHALL NOT claim or use
   `render_construction_refusal` as a generic renderer.
6. WHEN `render_vector_refusal` receives a known key, THE presenter SHALL render
   player-facing prose from that key and structured data; WHEN it receives an
   unknown key, THE presenter SHALL return a visible nonblank fallback that
   includes the unknown key and safely formatted structured data.
7. THE Ordnance_System SHALL compose no player-facing prose and SHALL publish
   only notification/refusal keys plus structured values through the durable
   outbox where this spec requires keyed delivery.
8. THE NotificationPresenter SHALL have formatter coverage for every
   designation, strike, warning, reservation, persistence, keyed-mutation
   outcome, action-receipt-cap, shared-outbox-capacity, interception,
   disruption, quarantine, and balance/refusal key introduced by this spec,
   including the distinct `cap_reached`, `outbox_capacity_reached`, and
   `conflict` reasons; an introduced known key that renders blank or falls
   through the unknown-key fallback SHALL fail coverage.
9. WHEN commands list a Designation, operation, or Warning_Area, THE command
   layer SHALL render the structured identity, planet, center, radius where
   applicable, state or reservation state, provenance where applicable,
   authoritative remaining ticks, and any observable indeterminate,
   action-receipt-saturation, or outbox-capacity/backpressure status without
   exposing mutable transaction internals as success.

### Requirement 11: Balance Validation, Hot Reload, and Bounded Work

**User Story:** As a game designer and operator, I want every Ordnance bound
validated exactly and every area operation predictably bounded.

#### Acceptance Criteria

1. THE Balance_Config SHALL define and the SchemaValidator SHALL validate these
   exact snake_case fields and inclusive ranges. `vector_outbox_capacity` SHALL
   be one global process/shared-store capacity and SHALL be validated at startup
   before any vector producer is registered or any outbox work is admitted:

   | Field | Valid loaded value |
   | --- | --- |
   | `strategic_strike_flight_ticks` | integer `[max(10, minimum_response_window_ticks), 3600]` |
   | `strategic_strike_radius` | integer `[1, 10]` |
   | `strategic_strike_damage` | integer `[1, 1_000_000]` |
   | `designation_radius` | integer `[1, 50]` |
   | `designation_memory_ticks` | integer `[1, 86400]` |
   | `designation_cap` | integer `[1, 100]` |
   | `strategic_strike_max_disruption_ticks` | integer `[0, 3600]` |
   | `strategic_strike_max_interception_fraction` | finite number `[0.0, 0.75]` |
   | `strategic_strike_cooldown_ticks` | integer `[1, 86400]` |
   | `strategic_strike_max_in_flight` | integer `[1, 100]` |
   | `vector_outbox_capacity` | exact non-Boolean integer `[1, 1_000_000]` |
   | `strategic_strike_action_receipt_cap` | integer `[1, 1024]` |
   | `strategic_strike_warning_receipt_cap` | integer `[1, 4096]` |
   | `agent_xp_strategic_strike` | integer `[0, 1_000_000]` |

2. THE SchemaValidator SHALL reject Booleans for every integer or numeric field,
   SHALL require exact integers for integer fields, and SHALL reject NaN and
   infinities for every numeric field before range comparison.
3. THE SchemaValidator SHALL require `strategic_strike_cost` to be a nonempty
   map from known canonical resource names to non-Boolean positive integers and
   SHALL require at least one of `Circuits`, `Energy`, or `Nexium` to appear with
   a positive amount.
4. WHEN multiple Ordnance balance values are missing, mistyped, non-finite,
   out-of-range, or otherwise invalid, THE SchemaValidator SHALL collect and
   report every detected field error in one load failure rather than stop at the
   first error.
5. WHEN balance data hot-reloads, THE new Ordnance values SHALL affect only
   later Designation admissions, later Strategic_Strike acceptances, and later
   new interception/disruption adjustments that read those fields; THE reload
   SHALL NOT rewrite a Designation's expiry, an operation's snapshotted flight/
   radius/raw damage/response floor/cooldown tick/warning cap/XP, a confirmed
   adjustment or receipt, a frozen interception value, or a resolution snapshot.
   A lowered action-receipt cap SHALL still permit known-key replay and SHALL
   refuse unknown keys while the existing combined count is at or above the new
   cap.

   `vector_outbox_capacity` SHALL not be snapshotted per operation. At startup
   and hot reload, THE validator SHALL obtain a confirmed durable current-use
   value equal to global live unsettled entries plus unconsumed reserved slots
   and SHALL reject the configuration if the value is outside `[1, 1_000_000]`,
   is Boolean or not an exact integer, is below current use, or current use is
   unreadable/indeterminate. An accepted increase or decrease no lower than
   current use SHALL govern only subsequent reservation decisions; it SHALL not
   evict, rewrite, or prematurely settle any existing entry or claim.
6. THE impact-time checked Counter_Web lookup SHALL remain at impact as required
   by Requirement 8; it is a shared victim-specific policy lookup, not a rewrite
   of any snapshotted Ordnance balance value.
7. WHEN scanning a Warning_Area or preparing an impact snapshot, THE
   Ordnance_System SHALL resolve the canonical planet room/cache once and use one
   spatial-index area query; its enumeration work SHALL be proportional to the
   bounded `(2 * radius + 1)^2` area plus the finite materialized returned
   occupants, not to world population or database size. Canonical stable-ID
   deduplication SHALL supply one exact persisted `candidate_count`; the subset
   that survives policy, checked Counter-Web, and definitive pre-engine checks
   SHALL supply exact `hit_ready_count`. CombatEngine's fixed bounded potential-
   intent schema SHALL supply an exact finite manifest per hit-ready candidate,
   and `engine_intent_slot_count` SHALL be their exact summed cardinality.
   Because the candidate snapshot is finite and terminal work has exactly three
   fixed entries, impact reservation demand SHALL be the finite exact integer
   `engine_intent_slot_count + 3`. If that demand exceeds total or currently
   available shared capacity, reservation SHALL return a claimless `rejected` and
   the strike SHALL remain tracked/counting at clock `0` without truncation, hit,
   or terminal transition, retriable under the same reservation ID. Any slot whose
   intent later becomes unauthorizable, because its candidate is a durable typed
   skip or its intent holds an original `rejected` no-op receipt, SHALL be
   released by the one `release_once` in Requirement 8.12 rather than leaked, so
   held capacity stays bounded by the same finite demand.
8. WHEN warning a later entrant, THE Ordnance_System SHALL use the movement/event
   and indexed Warning_Area lookup rather than rescan every operation, planet,
   room, or player; warning receipt work SHALL be bounded by the snapshotted
   receipt cap and direct keyed lookup.
9. WHEN pruning or admitting Designations, THE Ordnance_System SHALL inspect at
   most the holder's bounded per-planet bucket, whose valid available-plus-
   reserved size is at most `designation_cap`; it SHALL perform no room, world,
   or object-database scan.
10. WHEN applying Counter_Web at impact, THE Ordnance_System SHALL keep the one
    persisted resolution-local result per distinct victim owner/Branch pair, so
    checked lookups and multiplier work are proportional to distinct returned
    owners rather than candidates times Branches.
11. WHEN applying either counter seam, THE Ordnance_System SHALL resolve the
    operation by indexed operation ID, perform a direct lookup of the composite
    `(counter_kind, action_id)` in the relevant receipt-map partition, enforce
    the operation's combined live interception-plus-disruption receipt count
    without scanning either partition, and authorize the named source directly;
    it SHALL NOT scan all operations, sources, rooms, receipts, or world objects.
12. FOR each operation, `strategic_strike_action_receipt_cap` SHALL bound the
    sum of live interception and disruption action receipts; partitioning maps
    by kind SHALL NOT multiply that bound. THE action and warning receipt caps,
    128-byte action-ID limit, fixed outbox schemas, canonical stable references,
    one-entry-per-initial-owner rule, one-entry-per-optional-warning/action rule,
    fixed bounded engine-intent schema, one-entry-per-exact-engine-intent rule,
    and fixed three-entry terminal manifest SHALL bound reservation counts, IDs,
    recipients, and payloads. Across all vectors and operations, THE outbox SHALL
    atomically maintain
    `live_unsettled_entries + unconsumed_reserved_slots <= vector_outbox_capacity`.
    Partitioning by vector, operation, producer, or kind SHALL NOT multiply or
    bypass that one global bound.
13. THE Post_Commit_Outbox SHALL never evict or overwrite live entries,
    unconsumed reservations, or indeterminate claims to admit new work. It SHALL
    mark an entry settled and free its live capacity only after the matching
    `publish_once` or named keyed-mutation receipt is durably confirmed, and
    `release_once` SHALL free only confirmed unconsumed slots. Terminal source
    removal SHALL wait for every related live entry to settle and reservation to
    close. Settled entries, closed-reservation receipts, and constant-size replay
    tombstones SHALL be retained for a documented finite interval sufficient for
    source settlement/compaction and then pruned; pruning SHALL occur only after
    durable source settlement is recorded and the globally stable
    operation/action/event namespace is permanently non-reusable. Unsettled
    entries, open or indeterminate reservations, and unresolved ambiguity SHALL
    never be pruned. Settled entries and retained tombstones SHALL not consume
    live capacity.

### Requirement 12: Correctness Properties

**User Story:** As a developer, I want the contracts most vulnerable to races,
restart, and area ambiguity stated as properties, so implementation can be
checked against them directly.

#### Acceptance Criteria

1. FOR ALL Designations, serializing to the holder and reading back SHALL
   preserve by value the Designation ID, holder, canonical planet/coordinate,
   producer kind and optional producer identity, Primary_Target_Owner,
   creation/expiry ticks, reservation state, and reserved operation ID without
   shared mutable containers.
2. FOR ALL OperationRecord inputs, a new in-memory instance SHALL default to
   schema version `1`, `from_dict({})` SHALL yield legacy `0`, and an absent,
   Boolean, or non-exact-integer version SHALL decode to `0`; every present exact
   non-Boolean integer SHALL be preserved verbatim. Versions `0` and `1` alone
   SHALL be interpreted, every other readable integer SHALL be quarantined and
   reported without rewrite, and each absent/malformed `vector_data` SHALL yield
   a distinct fresh `{}`. Valid version-1 Strategic_Strikes SHALL round-trip all
   top-level and nested metadata deeply by value, while missing required
   version-1 metadata SHALL be isolated rather than accepted because of the
   fallback.
3. FOR EVERY crash or rejected/indeterminate result before and after each
   Acceptance_Transaction phase write, exact initial-warning slot reservation,
   serialized consent/alliance recheck, Designation reservation, keyed charge,
   record build, track, Pending write/readback, Designation consume, keyed
   cooldown, warning marker, each reserved append, reservation closure,
   cancellation, keyed refund, restoration, and abandonment-release boundary,
   recovery SHALL converge only from confirmed facts. A claimless capacity
   rejection SHALL refuse before Designation reservation or charge and SHALL stay
   retriable under the same reservation ID. A completed acceptance SHALL have one
   consumed Designation, one confirmed Pending operation, one original `applied`
   charge, one original `applied` cooldown, one durable public warning, and
   exactly one reserved initial event per snapshotted owner, with every replay
   observing `duplicate(prior=applied)` rather than a second effect; confirmed
   compensation SHALL restore pre-request
   resources and Designation availability with no matching non-terminal
   operation and no open warning reservation. An ambiguous boundary SHALL retain
   the transaction and every Designation/outbox claim and SHALL acknowledge
   neither acceptance nor rollback.
4. FOR ALL charged requests that fail before committed acceptance, confirmed
   `Cancelled` and transition cleanup SHALL precede
   `refund_once(..., "accept:{op_id}:refund",
   "accept:{op_id}:charge")` and Designation restoration. Replayed recovery SHALL
   observe `duplicate(prior=applied)` on the one original refund receipt and
   restore the exact snapshotted cost at most once, while an original `rejected`
   refund or its `duplicate(prior=rejected)` replay SHALL restore nothing;
   conflict or unreadable receipt/storage SHALL retain compensation rather than
   issue an unkeyed or second refund. A refund SHALL never apply without a linked
   original `applied` charge receipt.
5. FOR ALL successful, duplicate, re-entrant, alliance-change, and
   target-sharing-consent-revocation interleavings using one `designation_id`,
   the alliance, consent, holder, identity, expiry, and availability recheck
   inside the holder/planet boundary SHALL linearize before reservation. Exactly
   one valid request MAY commit one Pending operation and consume the
   Designation; no request whose consent or alliance is false at that boundary
   SHALL reserve the Designation, charge, or launch, and its pre-admission outbox
   claim SHALL be released only from a confirmed definitive refusal.
6. FOR ALL restart states containing a reservation or Acceptance_Transaction, a
   matching confirmed committed non-terminal operation SHALL keep the
   Designation unavailable and cause consumption/rebuild; a matching confirmed
   pre-commit `Cancelled` settlement or positive authoritative absence readback
   SHALL cause keyed cleanup, refund, and restoration before expiry; and
   unreadable, conflicting, timed-out, or otherwise indeterminate persistence
   SHALL retain the reservation. No unreadable store SHALL be interpreted as
   matching-operation absence, and no state SHALL make a Designation reusable
   beside a live or possibly live Pending operation.
7. FOR ALL accepted Strategic_Strikes, `OperationRecord.target_ref` SHALL equal
   the Designation's immutable Primary_Target_Owner, initial admission SHALL use
   `BranchSystem.may_target(requester, primary_target_owner_ref, hostile=True)`,
   and confirmed resolution SHALL request exactly one keyed
   `resolve:{op_id}:escalation` mutation for that owner and none for collateral,
   recording at most one escalation across every replay because repeats observe
   `duplicate(prior=applied)` or `duplicate(prior=rejected)`. Every absent,
   unresolved, failed, unreadable, or non-Boolean hostile-collateral shield
   decision SHALL exclude rather than expose that collateral.
8. FOR ALL accepted Strategic_Strikes, the response floor SHALL be evaluated
   exactly once at confirmed initial Warning_Area publication. The operation
   SHALL not tick before that point; every suspend/resume sequence SHALL restore
   the exact held clock, including disruption, without rereading balance or
   reflooring. Initial recipients SHALL be stable-owner deduplicated and bounded,
   their exact count SHALL be reserved before Designation reservation or charge,
   and acceptance SHALL not proceed without a confirmed reservation. At warning-
   receipt saturation or a claimless one-slot global-outbox rejection, a new later
   owner SHALL receive no direct message and no warning receipt while the public
   marker remains queryable, and that refusal SHALL never replay as
   `duplicate(prior=rejected)`. Every known warning receipt SHALL replay as
   `duplicate(prior=<original_outcome>)` with at most one dispatch, and every
   indeterminate later reservation claim SHALL remain reconcilable under its
   original reservation ID until safe terminal entry settlement and reservation
   cleanup.
9. FOR ALL impact snapshots, a candidate outside the Chebyshev `record.radius`
   at the one prepare query SHALL receive no strike damage regardless of its
   designation, warning, acceptance, or later position; retries SHALL reuse the
   immutable candidate snapshot rather than query a second time.
10. FOR ALL valid and invalid counter actions and orderings, each new applied
    interception/disruption delta SHALL equal its nonnegative remainder formula,
    action IDs SHALL be nonempty and at most 128 UTF-8 bytes, same-key/same-
    payload calls SHALL return `duplicate(prior=<original_outcome>)` on the
    immutable prior action receipt, where `duplicate(prior=applied)` retains the
    one recorded adjustment and event and `duplicate(prior=rejected)` retains the
    original no-application decision, and same-key/different-payload calls SHALL
    conflict. At action-receipt capacity, defined for one operation as the
    combined live interception-plus-disruption action-receipt count, an unknown
    composite `(counter_kind, action_id)` key SHALL perform no authorization,
    outbox reservation, receipt write, or adjustment while known composite keys
    replay; switching counter kind SHALL NOT bypass capacity, and no live receipt
    SHALL be evicted. A new positive adjustment SHALL reserve exactly one
    notification slot first; a claimless capacity rejection SHALL create no action
    receipt or adjustment and SHALL stay retriable rather than replay as
    `duplicate(prior=rejected)`, conflict SHALL fail closed, and an indeterminate
    reservation SHALL retain the claim while applying no adjustment pending
    readback. An authorized action that reaches only its adjustment cap SHALL
    persist one original `rejected` `cap_reached` no-op receipt with no
    notification reservation and SHALL replay it as `duplicate(prior=rejected)`,
    even if hot reload later raises that cap. A
    confirmed or indeterminate resolution prepare/freeze SHALL reject every new
    interception and disruption key, and a confirmed freeze SHALL keep the
    damage factor at least `0.25`.
11. FOR ALL distinct resolvable victim-owner/Branch pairs at one impact,
    exactly one `counter_multiplier_checked` outcome SHALL be persisted and
    reused, and it SHALL be exactly one of `neutral(1.0)`,
    `advantage(multiplier)`, `unavailable(reason)`, or `invalid(reason)`. Only
    the first two authorize arithmetic; both `unavailable(reason)` and
    `invalid(reason)` SHALL follow Ordnance's durable fail-closed pair-skip
    behavior for every candidate in that pair without suppressing another pair.
    Only a truly ownerless or unbranchable retained non-hostile/world entity MAY
    receive explicit neutral classification without a lookup, and no error path
    SHALL silently become neutral.
12. FOR ALL retained and arithmetic-authorized victims under otherwise
    identical CombatEngine conditions, the integer pre-combat raw magnitude
    SHALL equal exactly
    `max(1, floor(raw_base_damage * one_counter_multiplier * (1 - frozen_interception_fraction)))`.
    In particular, base damage `1`, multiplier `1`, and frozen fraction `0.75`
    SHALL produce `1`, not `0`.
13. FOR EVERY crash, duplicate, conflict, rejection, or indeterminate result
    before and after resolution epoch/freeze persistence, finite candidate
    snapshot/count, pair-result persistence, complete progress/intent-manifest
    initialization, exact finite engine-intent manifests and
    `engine_intent_slot_count + 3` outbox reservation, weapon construction, each
    CombatEngine core atomic unit, each reserved engine-intent append and receipt,
    terminal write/readback, each terminal append, reservation closure, and entry
    settlement boundary, replay SHALL preserve due operations in ascending `op_id`
    and victims in ascending stable ID. No hit SHALL precede a confirmed complete
    map and reservation. For each
    `strike:{op_id}:victim:{stable_id}`, only
    `CombatEngine.apply_direct_hit_once` and its Combat_Hit_Transaction MAY
    atomically bind request hash and target-state version/preconditions to the
    computed outcome, all authoritative deltas/resulting state, any zero-HP/
    death-pending marker, hit receipt, and downstream intent keys exactly
    matching the finite pre-reserved manifest, with no hidden fan-out. A
    caller-side receipt around legacy `apply_direct_hit` SHALL never satisfy this
    property.
    Each core hit SHALL mutate combat at most once; an original `applied` outcome
    or a matching `duplicate(prior=applied)` SHALL mean core confirmation only,
    and the candidate SHALL remain nonterminal until every required engine
    downstream receipt confirms, counting an original `rejected` no-op receipt for
    an inapplicable intent as confirmed settlement. Each downstream consequence
    SHALL occur at most once, with `duplicate(prior=applied)` ensuring the one
    already-performed consequence and `duplicate(prior=rejected)` performing none.
    Definitive pre-engine failures SHALL remain typed skips, and an original core
    `rejected` outcome SHALL be a terminal receipted `skipped_by_engine` candidate
    with no delta and no downstream work; receiptless rejection and indeterminacy
    SHALL remain pending; conflict or a changed target identity/version under the
    immutable request SHALL quarantine rather than silently skip. Independent
    later candidates MAY continue once, death-pending targets SHALL remain
    non-actionable until keyed death settlement, and Resolved SHALL wait for every
    candidate and durable confirmation without any fresh capacity admission.
14. FOR ALL allied and Detection_Sweep Designations, the launching player's
    selected eligible spotter SHALL remain `carrier_ref` and the sole possible
    Strategic_Strike XP recipient. After confirmed resolution, retries SHALL use
    the snapshotted amount and key `resolve:{op_id}:xp` and award at most once,
    observing `duplicate(prior=applied)` on the one original award and
    `duplicate(prior=rejected)` on an original unresolved-carrier rejection, and
    SHALL never award the producer; pre-terminal, rejected, or indeterminate
    resolution SHALL award none.
15. FOR ALL valid radii, holder buckets, action maps, warning maps, candidate
    snapshots, and outbox states, warning and impact enumeration SHALL be bounded
    by area plus a finite materialized occupant result, Designation work by
    `designation_cap`, counter-action lookup/storage by the one validated per-
    operation action-receipt cap applied to the combined live interception-plus-
    disruption action-receipt count even when maps are partitioned by kind,
    warning lookup/storage by the validated warning-receipt cap, and checked
    Counter_Web work by distinct persisted victim owner/Branch pairs. Initial
    warning demand SHALL equal its bounded stable-owner union; later warning and
    positive counter demand SHALL each equal one; finite candidate count plus the
    fixed bounded per-candidate engine-intent schema SHALL yield exact
    `engine_intent_slot_count`, and impact demand SHALL equal exact finite
    `engine_intent_slot_count + 3`. No admitted reservation may make global
    live unsettled entries plus unconsumed reserved slots exceed
    `vector_outbox_capacity`, no partition may multiply that bound, no ordinary
    path SHALL perform a full-world or object-database scan, and every outbox ID,
    recipient set, and payload SHALL remain bounded.
16. FOR ALL Ordnance Balance_Config startup loads and hot reloads, every invalid
    field SHALL appear in the same collected result, and no invalid Boolean,
    non-finite number, out-of-range value, invalid receipt cap, invalid global
    `vector_outbox_capacity`, empty/unknown cost map, or cost without a late-game
    resource SHALL be accepted. The global capacity SHALL be an exact non-Boolean
    integer in `[1, 1_000_000]`, SHALL be rejected when durable current use is
    indeterminate or greater than the proposed value, and SHALL never cause
    eviction. Hot reload SHALL not rewrite any accepted snapshot, action receipt,
    outbox receipt/claim, frozen value, or transaction.
17. FOR ALL persistence and keyed mutation calls, `confirmed` SHALL imply atomic
    durable acknowledgement or positive readback, and authoritative absence SHALL
    be distinguishable from unreadability. EVERY keyed mutation SHALL return
    exactly `applied`, `duplicate(prior=applied|rejected)`, `conflict`,
    `rejected`, or `indeterminate`, SHALL commit its state change and its
    immutable key/payload-hash/original-outcome/domain-reason receipt atomically,
    SHALL replay the same key and payload as
    `duplicate(prior=<original_outcome>)` with no second application and no lost
    authority, SHALL record a terminal domain no-op as an original `rejected`
    outcome rather than an outcome-less duplicate, and SHALL `conflict` and fail
    closed on the same key with a different payload without being read as terminal
    rejection, authoritative absence, or completed compensation. A refusal that
    records no original receipt SHALL remain retriable under the same key and
    SHALL never be reported as `duplicate(prior=rejected)`. This includes
    `charge_once`, `refund_once`, `note_cooldown_once`, `note_escalation_once`,
    `award_operation_xp_once`, warning and counter-action receipts,
    `CombatEngine.apply_direct_hit_once`, Combat_Hit_Transaction reconciliation
    and its downstream intent settlements, and
    `Post_Commit_Outbox.reserve_once`, `append_reserved`, and `release_once`.
    The engine API alone SHALL own the atomic combat-state/receipt boundary;
    OperationDriver may only delegate. No non-raising best-effort write, legacy
    unkeyed `apply_direct_hit` or consequence cascade plus a caller receipt,
    unkeyed publish/mutation plus a local flag, attempted delivery, or assumed
    release SHALL establish confirmation or exactly-once behavior. Every
    indeterminate engine or outbox result SHALL retain the potentially committed
    transaction or capacity claim until confirming readback.
18. FOR ALL acceptance warnings, optional warnings, counter notifications,
    combat-hit downstream consequences, resolution notifications, escalation,
    XP/loot, cooldown, and refund keyed/outbox work, immutable reservation IDs,
    event/mutation keys, request/payload hashes, exact manifests, and outcomes
    SHALL survive restart, remain authoritative while a source exists, and
    survive source removal for the required finite replay-retention interval
    after settlement.
    `publish_once`, `CombatEngine` downstream settlement, or the named keyed
    mutation API MAY replay until its durable receipt is confirmed and SHALL
    produce no second externally visible effect for the same key and payload:
    `duplicate(prior=applied)` SHALL ensure or finish exactly the one already
    authorized entry, delivery, or consequence, and `duplicate(prior=rejected)`
    SHALL authorize none while leaving its reserved slot unconsumed for release.
    At every outbox transition,
    `live_unsettled_entries + unconsumed_reserved_slots` SHALL remain no greater
    than the one global `vector_outbox_capacity`; `append_reserved` SHALL convert
    exactly one held slot into one live entry, `release_once` SHALL free only
    confirmed unconsumed slots, and no live/indeterminate work SHALL be evicted.
    A new launch without its exact initial-warning reservation SHALL refuse
    before Designation reservation/charge; a due strike without its exact
    finite candidate-derived `engine_intent_slot_count + 3` reservation SHALL
    remain tracked/counting at zero with no hit or terminal transition; an
    optional later warning without one slot SHALL suppress only direct delivery
    while retaining the public marker; and a positive counter action without one
    slot SHALL create no action receipt or adjustment. Indeterminate admission
    SHALL retain its claim under the same reservation ID, and no path SHALL
    substitute a replacement reservation ID or evict existing work to admit new
    work. A terminal source SHALL remain until every related entry settles and
    reservation closes; only then may settled entries and constant-size tombstones
    follow finite retention and stop consuming capacity.
