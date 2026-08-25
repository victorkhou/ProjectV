# Requirements Document

## Introduction

This feature implements the **Signals** Branch's Signature_Vector through one
shipped Operation_Kind, `intrusion`, and two modes:

- `mode = "plant"` applies a temporary **Building_Suspension** after a hostile
  PLANT response window.
- `mode = "jam"` applies a temporary **Agent_Jam** after a hostile jam response
  window.

This specification extends, but does not redefine, the shipped Branch and
operation contracts. In particular:

- `OperationRecord` has the six lifecycle states `pending`, `suspended`,
  `resolved`, `expired`, `cancelled`, and `discarded`. Only `pending` and
  `suspended` are non-terminal. A terminal record is untracked, but its source
  persistence is removed only after every required terminal outbox entry is
  durable and its retained transaction can recover without that record.
- `OperationDriver` owns request ordering, terminal finality, shared protection
  gates, cooldown and in-flight services, notification points, and restart
  rebuilding of non-terminal operations.
- `BranchSystem.is_operational` currently combines the base building gate, the
  Active_HQ_Rule, and Branch commitment. The shipped
  `world.utils.building_is_operational` remains only the base gate, and several
  consumers still call it directly; this specification therefore requires
  explicit consumer migration rather than assuming overlay coverage.
- `AgentSystem.process_tick` is the central dispatcher for agent progression and
  interval-zero behavior scripts. Jam gates that dispatcher without deleting or
  recreating scripts.
- `GameTickScript` already provides one `vector_operations` step through
  `BranchSystem.process_tick`. The Intrusion_System uses that step and does not
  add an independent timer script.

A PLANT or jam application is the **Vector_Operation**. An active denial is not
that operation and is never represented by keeping a Resolved operation alive.
When an application becomes due, the inherited `OperationDriver._resolve` path
uses a result-preserving prepare protocol to stage only a non-authoritative
Proposed_Denial_Template and to confirm the exact bounded outbox reservations
needed by terminal settlement and any possible active-effect lifecycle. The
driver remains the single terminal-state writer: only a confirmed durable
terminal receipt and durable pre-reserved escalation entry permit it to untrack
and remove the resolved Operation_Record and invoke the additive post-resolve
commit seam. On that seam's first eligible attempt, the retained transaction
freezes one effect commit tick, stable effect identity, exact
Final_Effect_Payload, and final hash before any reservation transfer, active
record, or index mutation. The seam then advances through reservation transfer,
separate denial-record persistence, and publish-last active-index installation;
it never turns the transaction itself into denial. A staged template, a frozen
but uncommitted final payload, an outbox reservation, an attempted write, or an
indeterminate receipt never denies service. This separation preserves six-state
finality and makes restart restoration structural rather than a best-effort
cleanup of a terminal operation.

## Glossary

The parent specification's glossary applies. The following terms are narrowed
for this feature.

- **Intrusion_System**: The `OperationDriver` subclass registered for the
  `intrusion` Operation_Kind. It owns both request modes, active-effect indexes,
  purge processing, debt accounting, and lifecycle event handling.
- **PLANT**: A `mode = "plant"` Intrusion Operation_Record in its hostile
  response window. It targets one concrete enemy building and is not an active
  Building_Suspension.
- **Jam_Application**: A `mode = "jam"` Intrusion Operation_Record in its hostile
  response window. It targets one concrete enemy agent and is not an active
  Agent_Jam.
- **Plant_Origin**: The exact planet and tile occupied by the selected
  infiltrator when either mode is accepted. It is distinct from the originating
  Cypher Node.
- **Building_Suspension**: A separately persisted active denial record on one
  building. Its presence contributes one `intrusion_suspended` conjunct to the
  building's current operational answer.
- **Agent_Jam**: A separately persisted active denial record on one agent. Its
  presence contributes one `jammed` conjunct to the agent's current behavior
  availability.
- **Active_Denial_Record**: The versioned, authoritatively committed record shape
  shared by a Building_Suspension and an Agent_Jam. It has its own identity,
  clock, source links, purge state, index, and rebuild lifecycle. Its exact
  schema is defined in Requirement 2. A staged template or frozen-but-
  uncommitted final payload is not an Active_Denial_Record for visibility or
  status purposes.
- **target_owner_ref**: The canonical persistent owner identity resolved from the
  concrete `target_ref` at admission, deep-copied into version-1 operation and
  transaction data, and immutable thereafter. It is attribution and replay data,
  including the target identity passed to delayed escalation; it never replaces
  the concrete target and never authorizes a live ownership-sensitive decision.
- **Proposed_Denial_Template**: The immutable, non-authoritative prepare-time
  description staged by `on_resolve` together with its Template_Hash. It contains
  only values knowable before effect commit, including the final proposed
  duration, and excludes every commit-time field: `effect_commit_tick`,
  `effect_id`, `started_tick`, `duration_ticks`, and `remaining_ticks` as
  final-record fields. Neither the template nor its Template_Hash is ever
  rewritten to carry a commit-time value.
- **Template_Hash**: The immutable hash of the Proposed_Denial_Template computed
  when `on_resolve` stages it. It covers only template fields and therefore
  excludes every commit-time field.
- **Final_Payload_Hash**: The immutable hash of the Final_Effect_Payload,
  computed once at effect finalization over the exact Active_Denial_Record
  schema including the confirmed commit-time fields. It is always distinct from
  the Template_Hash and is never recomputed on retry.
- **Final_Effect_Payload**: The immutable payload frozen and durably confirmed on
  the first eligible `on_resolved_commit` attempt, before any reservation
  transfer, effect, or index mutation. Its shape is exactly the
  Active_Denial_Record schema, its `effect_id` is the one confirmed stable effect
  identity, its `started_tick` equals the one confirmed `effect_commit_tick`, and
  its `duration_ticks` and `remaining_ticks` are both derived from the staged
  Proposed_Denial_Template's final proposed duration. Its own Final_Payload_Hash
  is distinct from the Template_Hash. Every retry, rebuild, or delayed commit
  reuses that same confirmed tick, identity, payload, and hash and never re-reads
  the current `now`.
- **Acceptance_Transaction**: A durable request journal keyed by a preallocated
  `op_id`. It is confirmed before the first domain mutation and retains the
  canonical `target_owner_ref`, target reservation, exact charge, Pending
  linkage, cooldown, the exact two-slot `accept:{op_id}:outbox` reservation
  covering one Pending warning and one terminal outcome, compensation phases, and
  all mutation receipts needed to roll forward or compensate without reusing an
  ambiguous request.
- **Resolution_Transaction**: A durable, target-persisted, idempotent recovery
  authority linking one Proposed_Denial_Template and, once frozen, one
  Final_Effect_Payload to its source operation and reservation. It remains non-
  authoritative after the source Operation_Record is terminal and removed,
  advances through explicit resolution, effect-finalization, effect-commit,
  post-commit, compensation, and indeterminate phases, and is never cleared
  merely because an effect commits or compensation begins.
- **Resolution_Prepare_Result**: The result-preserving prepare outcome returned
  to `OperationDriver._resolve`: at least `prepared(transaction_id)`,
  `retry(reason)`, `settled_no_effect(reason)`, or
  `indeterminate(transaction_id, reason)`.
- **Persistence_Result**: A persistence outcome of exactly `confirmed`,
  `rejected`, or `indeterminate`. `confirmed` requires a durable atomic
  acknowledgement or positive readback; a confirming read distinguishes
  authoritative absence from unreadability.
- **Mutation_Result**: The shared keyed-mutation result with exactly `applied`, a
  structured `duplicate(prior=...)`, `conflict`, `rejected`, or `indeterminate`.
  `applied` and `rejected` are immutable original receipt outcomes;
  `duplicate.prior` is exactly that original `applied` or `rejected` outcome. The
  domain change and its immutable payload-hash/outcome receipt commit atomically;
  the same key and payload returns `duplicate(prior=<original_outcome>)` without
  reapplying, while the same key with a different payload returns `conflict` and
  fails closed. `duplicate(prior=applied)` retains the authority of the original
  applied receipt, and `duplicate(prior=rejected)` retains the original
  no-application decision. A terminal domain no-op is an original `rejected`
  outcome carrying an immutable domain reason, not an outcome-less duplicate. A
  refusal that records no original receipt, such as a claimless capacity
  rejection, is a receiptless retriable `rejected` that changes no domain state,
  consumes no slot, stays retriable under the same key or reservation ID, and is
  never later reported as `duplicate(prior=rejected)`; no `duplicate` outcome is
  reported unless a matching original receipt exists.
- **Post_Commit_Outbox**: The one shared global durable outbox for vector event
  and keyed-mutation work, governed by startup-validated
  `vector_outbox_capacity`. It stores immutable reservation IDs, requested and
  unconsumed slot counts, open/closed reservation phase and receipts, plus
  immutable `event_id`, `kind`, replay-complete payload, payload hash, canonical
  bounded recipients, entry phase, and outcome receipt. It exposes exactly
  `reserve_once(reservation_id, slots)`,
  `append_reserved(reservation_id, event_id, kind, payload, recipients)`, and
  `release_once(reservation_id)`; all three return Mutation_Result and obey its
  key/payload and atomic-receipt semantics, atomically persisting their immutable
  method key, payload hash, original outcome receipt, and corresponding capacity
  state. `reserve_once` accepts only an exact non-Boolean positive integer,
  atomically claims that exact count or rejects without a claim, returns a
  same-key/same-count replay as `duplicate(prior=<original_outcome>)`, a changed
  count as `conflict`, and an unreadable outcome as `indeterminate`. Insufficient
  capacity returns a claimless, receiptless `rejected` that records no original
  reservation receipt and consumes no slot, so a still-eligible producer may
  retry that same reservation ID after capacity changes; that refusal is never
  reported as `duplicate(prior=rejected)`. `append_reserved` requires a confirmed
  matching
  open reservation and atomically consumes exactly one unconsumed slot to create
  exactly one live unsettled entry, without changing total capacity use or
  over-consuming the reservation. `release_once` atomically closes the
  reservation and frees only its unconsumed slots after its exact manifest is
  durably complete or definitively abandoned before the gated irreversible
  action; it never removes a live entry. A changed slot count, event ID, kind,
  payload, or recipient set under the same method key returns `conflict` and
  fails closed. At every atomic write, global live unsettled entries plus global
  unconsumed reserved slots SHALL be no greater than `vector_outbox_capacity`. An
  indeterminate reserve, append, or release retains and conservatively counts its
  claim pending confirming readback. A reservation is one constant-size claim for
  an exact finite slot count and is never taken for a wildcard, unbounded, or
  not-yet-discovered recipient set. Delivery uses `publish_once` for events or
  the named keyed mutation API; an entry becomes settled only after that sink
  receipt is durable. Existing entries, reservations, receipts, and tombstones
  are never evicted, overwritten, or silently settled to admit a reservation or a
  configuration change, and activation or hot reload below current use is
  rejected instead. Settled entries and closed-reservation constant-size
  tombstones are retained for exactly 604800 processed ticks after source
  settlement and namespace closure, then pruned by indexed bounded maintenance;
  rejected, conflict-quarantined, indeterminate, unreadable, or otherwise
  unsettled work never expires by age.
- **Target_Reservation**: A target-persisted exclusive claim spanning an
  accepted Pending application, any in-progress Resolution_Transaction, and the
  active effect it creates. Reservations are global across attackers.
- **Suspension_Debt**: The rolling sum of actual Building_Suspension interval
  overlap on one target across all attackers.
- **Purge_Attempt**: The one persisted, in-progress attempt to remove a
  Building_Suspension by continuous validation of one explicitly selected
  actor.
- **Firewall**: A Signals self-perk. One target-owned Operational Cypher Node in
  range adds one snapshotted PLANT penalty. It is not a Doctrine_Counter and
  never stacks.
- **Doctrine_Counter**: Biowarfare's ordinary Contagion damage against the live
  infiltrator. This feature adds no immunity and imports no Biowarfare system
  directly.

## Requirements

### Requirement 1: Operation and Active-Effect Architecture

**User Story:** As a developer, I want applications and active denials to have
separate lifecycles, so that the shipped terminal-state contract remains true.

#### Acceptance Criteria

1. THE OperationDriver SHALL expose an additive, result-preserving resolution-
   prepare protocol for `on_resolve` and an additive `on_resolved_commit` hook
   that defaults to a no-op for Operation_Kinds that do not use staged
   resolution. THE Intrusion_System SHALL subclass `OperationDriver`, SHALL
   declare `operation_kind = "intrusion"` and `branch = "cyber"`, and SHALL
   implement `validate_target`, `build_record`, `on_resolve`,
   `on_resolved_commit`, `persistence_owner`, and `discover_records`.
2. THE Intrusion_System SHALL represent both a building PLANT and a jam
   application as modes of the one `intrusion` Operation_Kind and SHALL NOT
   register Jam as a second Operation_Kind.
3. WHEN either mode is accepted, THE Intrusion_System SHALL create a concrete
   target-owned Operation_Record in `pending`; that record and its application
   countdown SHALL be the Vector_Operation and Response_Window.
4. THE OperationDriver SHALL retain exactly the six shipped operation states,
   and THE Intrusion_System SHALL treat `resolved`, `expired`, `cancelled`, and
   `discarded` as final and untrackable.
5. WHEN a Pending application successfully prepares and resolves, THE
   Intrusion_System SHALL stage one Proposed_Denial_Template and its
   Template_Hash non-authoritatively, SHALL confirm the exact bounded terminal
   and effect-lifecycle outbox reservations, SHALL allow the OperationDriver's
   single state writer to durably confirm a terminal `resolved` receipt, and
   SHALL only then advance the retained Resolution_Transaction through one-time
   effect finalization that confirms the `effect_commit_tick`, `effect_id`,
   Final_Effect_Payload, and Final_Payload_Hash, and afterward through
   reservation transfer, authoritative Active_Denial_Record persistence, and
   active-index commit in `on_resolved_commit`.
6. THE Intrusion_System SHALL NOT call a Building_Suspension or Agent_Jam an
   `OperationState.SUSPENDED` operation; that state name remains reserved for a
   non-terminal operation paused by the generic operation framework.
7. WHILE a committed Active_Denial_Record exists, THE Intrusion_System SHALL
   advance it through its own active-effect clock and index rather than through
   a terminal Operation_Record's `ticks_remaining` or `lifetime_remaining`.
8. THE Intrusion_System SHALL apply no hit-point damage, ownership transfer,
   deletion, or stored-state replacement to a denial target.
9. THE composition root SHALL inject the Intrusion_System's persistence,
   bounded-spatial-query, BranchSystem, AgentSystem, and other collaborators,
   register exactly one instance through `BranchSystem.register_vector`, and
   invoke its rebuild during server start.
10. THE Intrusion_System SHALL use the existing `vector_operations` tick fan-out
    for Acceptance_Transaction and Resolution_Transaction reconciliation,
    Pending applications, active-effect clocks, purge clocks, bounded outbox
    reservation reconciliation, and outbox delivery; SHALL use the one injected
    global Post_Commit_Outbox rather than a private or per-operation pool; and
    SHALL create no independent InfiltratorScript or timer-script clock.
11. THE Intrusion_System module SHALL import no game-framework module at module
    scope.
12. THE inherited `OperationDriver._resolve` path SHALL remain the sole writer of
    the source operation's terminal state. Before that writer may settle a due
    Signals operation, the Acceptance_Transaction's one remaining
    `accept:{op_id}:outbox` terminal-outcome slot and the Resolution_Transaction's
    exact four-slot Jam or five-slot PLANT
    `resolve:{op_id}:effect_outbox` effect-lifecycle reservation SHALL each be
    confirmed as an original `applied` or a same-payload
    `duplicate(prior=applied)`. A new reservation refused for insufficient
    capacity SHALL be a claimless, receiptless `rejected` that leaves the
    operation non-terminal, tracked, and counting at zero and stays retriable
    under the same reservation ID rather than replaying as
    `duplicate(prior=rejected)`; a conflict or indeterminate result SHALL retain
    the claim and do the same. WHEN the writer resolves a prepared transaction,
    the source `resolved` state, immutable terminal receipt binding concrete
    `target_ref`, the persisted canonical `target_owner_ref`, Template_Hash, and
    `resolved_tick`, and transaction `resolved_confirmed` phase SHALL be one
    atomic owner persistence unit. The writer SHALL then use the already-owned
    terminal slot to durably `append_reserved` the immutable
    `resolve:{op_id}:escalation` intent, whose payload and payload hash SHALL
    snapshot that same `target_owner_ref`. Only confirmation of the complete
    terminal unit and that exact entry SHALL permit untracking, source-record
    removal, or `on_resolved_commit`; THE Intrusion_System SHALL NOT write a
    terminal state or call either keyed or unkeyed escalation APIs from either
    resolution hook.
13. A Resolution_Transaction, Proposed_Denial_Template, Template_Hash,
    finalization receipt, Final_Effect_Payload, Final_Payload_Hash, or outbox
    reservation that has not completed the authoritative effect commit SHALL be
    non-authoritative: it SHALL NOT enter an active index, affect operational or
    behavior status, tick, accrue debt, begin purge, publish an active-start
    notification, or award XP.
14. Only a fully committed Active_Denial_Record whose exact Final_Payload_Hash,
    matching effect-owned target reservation, retained lifetime outbox budget,
    transaction receipts, and publish-last active-index entry all agree SHALL be
    visible to BranchSystem, AgentSystem, commands, clocks, purge, debt, or
    notifications.
15. EVERY OperationDriver persistence writer, including `_persist_owner` and
    `_transition`, SHALL return and propagate `Persistence_Result` with exactly
    `confirmed`, `rejected`, or `indeterminate`. `confirmed` SHALL require a
    durable atomic acknowledgement or positive readback, and every confirming
    read SHALL distinguish authoritative absence from unreadability. An
    attempted, non-raising, timed-out, or unreadable write SHALL NOT mutate the
    driver's authoritative in-memory state or imply persistence success.
16. THE resolution-prepare protocol SHALL return a
    `Resolution_Prepare_Result` of at least `prepared(transaction_id)`,
    `retry(reason)`, `settled_no_effect(reason)`, or
    `indeterminate(transaction_id, reason)`. `_resolve` SHALL call that prepare
    hook through a result-preserving path and SHALL NOT use `_run_hook`, because
    `_run_hook` discards return values and swallowed-hook status. An additive
    legacy adapter MAY map existing void hooks to their prior behavior without
    changing vectors that do not opt into this protocol.
17. `_advance_one` and `advance_all` SHALL leave a due record tracked with
    `ticks_remaining = 0` after `retry`, `indeterminate`, a rejected or
    indeterminate terminal persistence result, or any terminal state/receipt unit
    that is not confirmed in full. In each case the authoritative source record
    SHALL remain non-terminal and tracked. A definitively rejected required
    outbox reservation SHALL produce the same outcome. They SHALL resume the same
    transaction by identity and immutable Template_Hash rather than stage a second
    proposal. Only a confirmed terminal receipt in the atomic unit from Acceptance
    Criterion 12, with every receipt-authorized required entry durably appended
    from its reservation and every unneeded reserved slot durably released, SHALL
    permit untracking or removal of the Operation_Record and entry into
    `on_resolved_commit`.
18. `origin_fatal_reason` SHALL report only physical or source-fatal conditions
    independent of Branch commitment and SHALL never return `branch_dormant`.
    The driver SHALL evaluate carrier unavailability, nonphysical origin status,
    and commitment lapse through each vector's policy afterward; the Signals
    policy SHALL terminally cancel those application-invalid conditions rather
    than suspend them. The inherited hostile response floor SHALL be applied
    exactly once with durable Pending-warning enqueue; any generic resume SHALL
    retain the exact remaining countdown without reflooring, although Signals
    normally cancels instead of resuming.
19. ALL keyed shared domain mutations SHALL return `Mutation_Result` with exactly
    `applied`, structured `duplicate(prior=applied|rejected)`, `conflict`,
    `rejected`, or `indeterminate`. An original domain mutation and its immutable
    mutation-key, payload-hash, original `applied` or `rejected` outcome, and
    domain-reason receipt SHALL be one atomic decision; a terminal domain no-op
    SHALL retain an original `rejected` receipt and immutable domain reason rather
    than an outcome-less duplicate. Repeating the same key and payload SHALL
    return `duplicate(prior=<original_outcome>)` without reapplying;
    `duplicate(prior=applied)` SHALL retain the authority of the original applied
    receipt, while `duplicate(prior=rejected)` SHALL retain the original
    no-application decision. Repeating a key with a different payload SHALL return
    `conflict` and fail closed; an ambiguous mutation SHALL retain its authority
    for positive readback and SHALL NOT be retried under a new key. Signals SHALL
    use `BranchSystem.charge_once(player, cost, mutation_id)`,
    `BranchSystem.refund_once(player, cost, mutation_id, charge_mutation_id)`,
    `BranchSystem.note_cooldown_once(building, kind, ready_at, mutation_id)`,
    `BranchSystem.note_escalation_once(actor, target, resolved_tick,
    mutation_id)`, and `AgentSystem.award_operation_xp_once(agent, kind, amount,
    mutation_id)`. Existing unkeyed APIs SHALL be legacy-only. The mutation keys
    SHALL include `accept:{op_id}:charge`, `accept:{op_id}:refund`,
    `accept:{op_id}:cooldown`, `resolve:{op_id}:escalation`, and
    `resolve:{op_id}:xp`.
20. THE shared Post_Commit_Outbox SHALL expose exactly
    `reserve_once(reservation_id, slots)`,
    `append_reserved(reservation_id, event_id, kind, payload, recipients)`, and
    `release_once(reservation_id)`. Each API SHALL return `Mutation_Result` and
    atomically persist its immutable method key, payload hash, original outcome
    receipt, and corresponding capacity state. `reserve_once` SHALL atomically
    claim exactly the requested positive, exact non-Boolean integer number of
    slots or reject without a claim; `append_reserved` SHALL atomically consume
    exactly one unconsumed slot and create exactly one immutable outbox entry;
    `release_once` SHALL atomically close that reservation and release all of its
    unconsumed slots. A same-key/same-payload retry SHALL return
    `duplicate(prior=<original_outcome>)`; for an original applied operation it
    SHALL ensure the same claim, entry, or release receipt and SHALL NOT create a
    second one, while an original rejected operation SHALL create none. A changed
    slot count, event ID, kind, payload, or recipients under the same method key
    SHALL return `conflict` and fail closed. THE Intrusion_System SHALL use the
    one injected global outbox and SHALL define no private, per-mode, or
    per-operation pool.
21. AT every atomic Post_Commit_Outbox boundary, the number of live unsettled
    entries plus all unconsumed reserved slots SHALL be at most the current
    global `vector_outbox_capacity`. A successful `append_reserved` SHALL convert
    one reserved slot into one live entry without increasing that sum. THE system
    SHALL never evict, overwrite, or silently settle existing work to admit a
    reservation or configuration change. Intrusion_System activation and hot
    reload SHALL reject a capacity below current use. Settled entries, closed
    reservations, and constant-size tombstones SHALL be pruned under the declared
    finite retention horizon in Requirement 2; unresolved entries, reservations,
    and ambiguity SHALL NOT be pruned.
22. BEFORE every irreversible acceptance, resolution prepare, effect commit,
    terminal settlement, purge mutation, and ending, THE owning Signals workflow
    SHALL durably reserve the exact finite number of possible outbox entries
    derived from its already frozen bounded work: exactly two slots for
    `accept:{op_id}:outbox` covering one Pending warning and one terminal
    outcome; exactly four slots for a Jam or five slots for a PLANT
    `resolve:{op_id}:effect_outbox` covering the active start, the
    `resolve:{op_id}:xp` award, the one eventual ending, the eventual
    reservation-release settlement, and for a PLANT the eventual debt closure;
    and exactly two slots for `purge:{effect_id}:{attempt_id}:outbox` covering one
    purge start and one eventual abandonment. It SHALL reserve neither a wildcard
    nor an unbounded or not-yet-discovered recipient set, and one slot SHALL
    authorize at most one immutable event entry. A new request whose exact
    reservation is definitively rejected SHALL refuse before that reservation is
    charged against capacity and before any charge, cooldown, target-reservation,
    Operation_Record, effect, purge, debt, or receipt mutation. A due operation
    whose reservation is definitively rejected SHALL remain tracked and counting
    at zero with no effect, no terminal transition, and no required event append.
    A capacity rejection in either case SHALL be claimless and receiptless, SHALL
    consume no slot, and SHALL stay retriable under the same reservation ID rather
    than replay as `duplicate(prior=rejected)`.
    BEFORE creating an active effect, the exact start/XP plus the eventual single
    ending, debt-closure, and reservation-release budget SHALL already be
    confirmed, so expiry, purge, or source loss can never create unreserved
    backlog. An `indeterminate` reservation SHALL conservatively retain its
    possible claim, SHALL be reconciled under the same reservation ID, and SHALL
    authorize neither mutation nor a replacement reservation. Optional event-only
    work that performs no domain mutation MAY be suppressed before enqueue when no
    slot is available, and that suppression SHALL be logged and status-visible
    while every existing entry stays replayable. Once no further event can be
    authorized, the workflow SHALL append every required entry and call
    `release_once` for all unconsumed slots before the terminal source-removal
    gate in Acceptance Criteria 12 and 17.

### Requirement 2: Versioned Durability and Persistence Discipline

**User Story:** As an operator, I want every fact needed after restart to be
persisted explicitly, so that a restart cannot invent, prolong, or lose denial.

#### Acceptance Criteria

1. THE OperationRecord persistence contract SHALL add `schema_version` and
   `vector_data` without removing or changing the meaning of any shipped field.
2. A newly constructed `OperationRecord()` SHALL default to
   `schema_version = 1` and a fresh `vector_data` mapping, while
   `OperationRecord.from_dict({})` SHALL yield legacy `schema_version = 0`.
   On read, an absent `schema_version` or any malformed value that is not an
   exact non-Boolean integer SHALL yield version `0`; a present exact non-Boolean
   integer SHALL be preserved verbatim. Only versions `0` and `1` are supported;
   every other preserved integer, including a negative or future value, SHALL be
   quarantined and reported without partial interpretation or rewrite. An absent
   or malformed `vector_data`, including any non-mapping value, SHALL become a
   distinct fresh empty mapping on every read. Persistence-layer unreadability
   SHALL instead be `indeterminate` and SHALL NOT synthesize version `0`, an
   empty mapping, or authoritative absence. For version `1`, required metadata
   SHALL be validated and an invalid entry isolated. The current writer SHALL
   emit version `1`, and writing a successfully read version-0 record SHALL
   upgrade it to version `1`. For supported metadata, `to_dict` and `from_dict`
   SHALL preserve all nested content deeply by value without mutable identity
   shared by an input payload, record, serialized result, another record, or
   another read.
3. WHEN either Intrusion mode is accepted, THE persisted version-1 `vector_data`
   SHALL include `mode`, `plant_origin_planet`, `plant_origin_x`,
   `plant_origin_y`, `required_plant_ticks`, `firewall_applied`,
   `target_reservation_id`, and the canonical `target_owner_ref` resolved at
   admission. `target_owner_ref` SHALL be required version-1 metadata, SHALL be
   deep-copied by value, and SHALL be immutable for the record's whole lifetime;
   no later read, rebuild, transfer, or write SHALL replace it by re-resolving an
   owner from the live target. It SHALL NOT replace the concrete `target_ref` and
   SHALL NOT authorize a live ownership-sensitive decision such as `may_target`,
   alliance, consent, or purge-actor authorization.
4. THE persisted `vector_data` SHALL also carry the request snapshots needed to
   keep the accepted application's base effect duration, configured bounds, debt
   policy, and `agent_xp_intrusion` award unchanged by a later Balance_Config
   reload. Before Pending confirmation, the Acceptance_Transaction SHALL retain
   the same exact cost and keyed charge receipt; after confirmation, the existing
   Operation_Record `charged` field SHALL remain the authoritative cost snapshot
   and SHALL agree with that journal.
5. THE Operation_Record for either mode SHALL persist a concrete `target_ref`,
   so that `BranchSystem.may_target` and all lifecycle events apply to the enemy
   building or agent rather than only to coordinates or an owner. `target_ref`
   SHALL remain the concrete target and SHALL NOT be rewritten to an owner
   identity; the immutable `target_owner_ref` from Acceptance Criterion 3 SHALL be
   the one escalation and attribution owner snapshot.
6. THE Intrusion_System SHALL persist a Building_Suspension on its target
   building and an Agent_Jam on its target agent in a container separate from
   that target's `vector_operations` container.
7. THE Intrusion_System SHALL persist each Resolution_Transaction in a third,
   separate staging container, idempotently keyed by source operation. Its
   versioned shape SHALL contain a stable transaction identity, source-operation
   identity, source reservation identity, mode, concrete target, the immutable
   snapshotted `target_owner_ref`, the immutable Proposed_Denial_Template, its
   immutable Template_Hash, the exact `resolve:{op_id}:effect_outbox` reservation
   identity, requested slot count and reservation receipt, phase, durable terminal
   receipt, effect-finalization receipt, the confirmed `effect_commit_tick`,
   confirmed stable `effect_id`, immutable Final_Effect_Payload, its immutable
   Final_Payload_Hash, reservation-transfer receipt, effect-persistence receipt,
   index-commit receipt, keyed escalation and XP receipts, start-notification
   receipt, and compensation and outbox receipts. THE Template_Hash SHALL cover
   only template fields and SHALL exclude every commit-time field; the
   Final_Payload_Hash SHALL cover the exact Active_Denial_Record schema including
   the confirmed commit-time fields, and the two hashes SHALL always be distinct
   and separately persisted. Neither the template nor its Template_Hash SHALL ever
   be rewritten to carry `effect_commit_tick`, `effect_id`, `started_tick`,
   `duration_ticks`, or `remaining_ticks`. The `effect_commit_tick`, `effect_id`,
   Final_Effect_Payload, and Final_Payload_Hash SHALL be absent until effect
   finalization confirms them, and immutable afterward. Its phase SHALL be one of
   `staged`, `resolved_confirmed`, `effect_finalizing`, `effect_finalized`,
   `effect_committing`, `effect_committed`, `post_commit_pending`, `complete`,
   `compensating`, `compensated`, or `indeterminate`. Effect finalization and
   effect commit SHALL each advance the transaction rather than clear it, and no
   transaction field SHALL be added to the exact Active_Denial_Record schema.
8. THE current Active_Denial_Record version SHALL be `schema_version = 1`, and
   its schema SHALL consist of exactly `schema_version`, `effect_id`, `mode`,
   `source_operation_id`, `source_ref`, `origin_building_ref`, `carrier_ref`,
   `target_ref`, `planet`, `started_tick`, `duration_ticks`, `remaining_ticks`,
   and `purge_state`, where `source_ref` is the attacker/source reference and
   `mode` is `plant` for Building_Suspension or `jam` for Agent_Jam.
9. THE `purge_state` value SHALL be a versioned-by-parent mapping containing
   exactly `actor_ref`, `remaining_ticks`, and `last_validated_tick`; WHEN no
   purge is active, including for Agent_Jam, it SHALL default `actor_ref` to
   `None`, `remaining_ticks` to `0`, and `last_validated_tick` to `None`.
10. THE Active_Denial_Record SHALL store no prior `Operational`, reserve,
    incapacitation, role, assignment, script-attachment, mutation receipt, or
    outbox snapshot.
11. THE Intrusion_System SHALL read every Operation_Record,
    Acceptance_Transaction, Resolution_Transaction, Active_Denial_Record,
    reservation container, debt ledger, purge state, ending receipt,
    Post_Commit_Outbox entry, and terminal tombstone deeply by value and SHALL
    replace the whole persisted container on write rather than mutate a stored
    list, mapping, or nested value in place.
12. THE OperationRecord defaults in Acceptance Criterion 2 SHALL be applied
    before entry isolation and SHALL not alone make an otherwise readable legacy
    record malformed. An exact integer version other than `0` or `1` SHALL be
    quarantined and reported as unsupported without interpreting its metadata or
    rewriting it. Separately, a version-1 record with invalid required metadata
    SHALL be isolated and reported as malformed without rewriting the otherwise
    readable entry; unsupported versions and malformed supported metadata SHALL
    NOT be conflated. Independently, IF an
    Acceptance_Transaction, Resolution_Transaction, Active_Denial_Record,
    reservation, ledger, purge, ending, outbox, or tombstone entry is
    non-mapping, malformed, explicitly unsupported for its own schema, has an
    unsupported required value, or cannot resolve required references, THEN THE
    Intrusion_System SHALL isolate and report only that entry, continue with the
    remainder, and apply no denial from it. Unreadability during a confirming
    read SHALL never be treated as authoritative absence.
13. WHEN an operation state, transaction phase, effect finalization, reservation
    ownership, active-effect commit or removal, purge mutation, debt interval,
    keyed side effect, or event changes, THE responsible writer SHALL first obtain
    a `confirmed` Persistence_Result or an `applied` or
    `duplicate(prior=applied)` Mutation_Result for the atomic domain change and
    receipt. Resulting external delivery SHALL be durably enqueued into an
    already-reserved slot before publication through the Post_Commit_Outbox. An
    attempted or indeterminate write SHALL not authorize visibility, notification,
    XP, or cleanup; a `duplicate(prior=rejected)` receipt SHALL authorize none of
    them either; and active-start and XP remain prohibited until authoritative
    effect/index commit and same-tick source reconciliation.
14. WHEN only countdown clocks change during one tick, THE Intrusion_System SHALL
    batch those writes to at most one read-copy-write per target in that tick.
15. THE Intrusion_System SHALL measure all application, active-effect, debt, and
    purge progress in processed game ticks rather than wall-clock time, so server
    downtime adds no elapsed ticks.
16. THE Acceptance_Transaction SHALL be keyed by a preallocated `op_id`, SHALL be
    durably persisted before any reservation or other domain mutation, and SHALL
    retain the target reservation identity, the immutable canonical
    `target_owner_ref` resolved at admission, exact charge and charge receipt,
    Operation_Record linkage, immutable payload hash, cooldown receipt, the exact
    two-slot `accept:{op_id}:outbox` reservation identity, slot count and
    reservation receipt, Pending-warning and outbox receipts, and every
    compensation receipt. Its phases SHALL include at least `prepared`,
    `reserved`, `charged`, `pending_confirmed`, `committed`, `complete`,
    `compensating`, `compensated`, and `indeterminate`. `committed` SHALL mean
    that acceptance is durably acknowledged and tracked; `complete` SHALL require
    every acceptance fact and keyed warning/outbox receipt to agree and every
    unconsumed slot to be released. An indeterminate transaction, its claim, and
    its reservation SHALL remain retained and non-reusable until confirming
    readback resolves it; absence SHALL never be inferred from unreadability.
17. THE durable Post_Commit_Outbox SHALL store an immutable reservation identity,
    mutation or event key, immutable payload including every snapshotted amount,
    tick, and snapshotted `target_owner_ref` needed for replay, canonical bounded
    recipients, phase, and outcome receipt. It SHALL deliver notifications only by
    `publish_once(event_id, kind, payload, recipients)` and SHALL treat `applied`
    and `duplicate(prior=applied)` as delivered,
    `duplicate(prior=rejected)` as authorizing no delivery, `conflict` as
    fail-closed corruption, `rejected` as a structured failure, and
    `indeterminate` as retained work requiring confirming readback or replay. An
    unsettled entry SHALL survive source-record removal until its receipt is
    terminally settled.
18. THE Intrusion_System SHALL prune an Acceptance_Transaction or
    Resolution_Transaction only after it is `complete` or `compensated`, every
    authoritative fact and keyed side-effect/outbox receipt agrees, every entry it
    authorized is settled, every unconsumed slot of its reservations is released
    through `release_once`, and a replay-safe retention horizon of exactly 604800
    processed ticks has elapsed. Pruning SHALL leave a compact constant-size
    terminal tombstone containing the identity, immutable Template_Hash, immutable
    Final_Payload_Hash when finalization occurred, and outcome sufficient to
    prevent recreation, duplicate compensation, or reuse of the same key. A
    tombstone SHALL own zero reserved slots and SHALL authorize no event.

### Requirement 3: Atomic Target Reservation and Transition Transactions

**User Story:** As a defender, I want one exclusive denial claim per target, so
that races and restarts cannot stack effects.

#### Acceptance Criteria

1. THE Intrusion_System SHALL enforce at most one target reservation spanning an
   Acceptance_Transaction, Pending building PLANT, its in-progress
   Resolution_Transaction, or its active Building_Suspension for one building,
   and at most one spanning the equivalent Jam_Application stages for one agent,
   across all attackers.
2. THE Intrusion_System's target validation, debt validation, status queries,
   and reservation-availability query SHALL be read-only and SHALL NOT reserve,
   prune, repair, cancel, reconcile, or otherwise mutate persisted state.
3. AFTER every pure request check passes and BEFORE any domain mutation, THE
   Intrusion_System SHALL preallocate `op_id` and a fresh reservation identity,
   resolve the concrete target's canonical persistent owner into an immutable
   `target_owner_ref`, build an immutable acceptance payload containing that
   owner, and obtain confirmed persistence of the Acceptance_Transaction keyed by
   that `op_id`. IF the canonical owner cannot be resolved, is absent, is
   noncanonical, or is unreadable at admission, THEN THE Intrusion_System SHALL
   fail closed and refuse before any domain mutation, reservation, charge,
   cooldown, outbox reservation, or Operation_Record write. It SHALL then reserve
   the exact two `accept:{op_id}:outbox` slots required by Requirement 1, serialize
   on the concrete target, reserve it with the same identity, atomically record the
   reservation mutation receipt and `reserved` phase, and proceed only on an
   `applied` or same-payload `duplicate(prior=applied)` Mutation_Result.
4. IF the exact two-slot `accept:{op_id}:outbox` reservation is refused for
   insufficient capacity, THEN that `rejected` result SHALL be claimless and
   receiptless, SHALL consume no slot, and SHALL stay retriable under the same
   reservation ID rather than replay as `duplicate(prior=rejected)`. IF the target
   reservation returns an original `rejected` receipt confirming no domain change
   because the target is unavailable, THEN that immutable receipt and its domain
   reason SHALL be retained and SHALL replay as `duplicate(prior=rejected)` under
   the same key. IN either case THE
   Intrusion_System SHALL refuse without charging, starting cooldown, changing
   debt, or creating an Operation_Record, SHALL leave outbox use unchanged, and
   SHALL retain the no-domain-change terminal journal until Requirement 2 permits
   its tombstone. A `conflict` SHALL mean that the same
   mutation key already has a different payload and SHALL fail closed: the
   Acceptance_Transaction and any possible claim SHALL be retained and
   quarantined, no later acceptance mutation SHALL run, and the request SHALL
   NOT be reported as an ordinary refusal, compensated, tombstoned, or reused
   until explicit reconciliation establishes every authoritative fact. An
   `indeterminate` result or unreadable confirming read SHALL likewise retain the
   non-reusable Acceptance_Transaction, possible claim, and conservatively counted
   reservation for reconciliation and SHALL NOT be reported as a refusal or
   treated as an available target or as free capacity.
5. AFTER the `reserved` phase and the confirmed two-slot outbox reservation, THE
   Intrusion_System SHALL, in order: invoke
   `BranchSystem.charge_once` with the exact snapshotted mode-aware cost and
   `accept:{op_id}:charge`; record `charged` only for `applied` or
   same-payload `duplicate(prior=applied)`; build the Operation_Record with the
   same `op_id`, reservation, cost, immutable `target_owner_ref`, and immutable
   snapshots; calculate the hostile response
   floor exactly once in that immutable payload; obtain confirmed persistence of
   a non-tick-eligible Pending candidate and phase `pending_confirmed`; invoke
   `BranchSystem.note_cooldown_once` with the snapshotted `ready_at` and
   `accept:{op_id}:cooldown`; and `append_reserved` the complete target warning and
   immutable floor-applied marker into the first reserved slot, leaving the second
   slot unconsumed for the eventual single terminal outcome. The Pending
   persistence confirms the
   candidate but does not make it tick-eligible. The durable Pending-warning
   enqueue and marker SHALL be the one publication boundary that makes its final
   snapshotted countdown authoritative for tick processing. Only after every
   receipt agrees SHALL the transaction become `committed`, be acknowledged as
   accepted, and enter tracking; warning rendering or recovery MAY replay but
   SHALL never refloor the countdown.
6. IF a pre-commit acceptance step is definitively `rejected` with confirmed
   no-domain-change semantics, or an expected candidate is authoritatively
   absent, THE Intrusion_System SHALL move the Acceptance_Transaction through
   `compensating`, remove any non-eligible Pending entry by a confirmed write,
   call `BranchSystem.refund_once` for a confirmed charge with
   `accept:{op_id}:refund` and charge mutation id `accept:{op_id}:charge`, release
   a confirmed target reservation through its own keyed mutation receipt, close any
   confirmed unused `accept:{op_id}:outbox` slots through `release_once`, and mark
   `compensated` only after all applicable receipts agree. A transaction that
   has crossed an irreversible accepted boundary SHALL roll forward rather than
   be reclassified as refused. For a `conflict` or any `indeterminate`
   persistence or mutation result, it SHALL retain the operation, reservation,
   charge linkage, exact payload, and journal in fail-closed `indeterminate`
   recovery, expose no denial, perform no unconditional rollback or
   compensation, and prohibit reuse until confirming readback or explicit repair
   resolves every authoritative fact; unreadability SHALL never prove absence.
7. WHEN an application reaches zero, THE Intrusion_System SHALL revalidate its
   source, target, reservation, the current `counter_multiplier_checked` result
   when applicable, and the pure debt decision before preparing an active
   effect.
8. WHEN final revalidation succeeds, `on_resolve` SHALL idempotently persist
   exactly one Resolution_Transaction in phase `staged` containing the immutable
   Proposed_Denial_Template, its immutable Template_Hash, the source-operation
   identity, the snapshotted `target_owner_ref`, the source reservation identity,
   and the confirmed exact four-slot Jam or five-slot PLANT
   `resolve:{op_id}:effect_outbox` reservation receipt, and SHALL
   return `prepared(transaction_id)`. THE staged template SHALL contain only
   values knowable before effect commit, including the final proposed duration,
   and SHALL exclude the commit-time fields `effect_commit_tick`, `effect_id`,
   `started_tick`, `duration_ticks`, and `remaining_ticks`; the Template_Hash
   SHALL be computed over exactly those template fields, so no later commit-time
   value can change it. `on_resolve` SHALL NOT read the current `now` into a
   staged field, insert a commit-time field, freeze a Final_Effect_Payload, or
   rewrite the template. It SHALL return `retry(reason)`,
   `settled_no_effect(reason)`, or `indeterminate(transaction_id, reason)` as
   applicable without reserving beyond that exact bounded count, transferring the
   reservation, writing the active-effect container, installing an active-index
   entry, publishing a start, or awarding XP. Re-entry with the same source and
   template SHALL resume the same transaction by identity and Template_Hash; a
   different template SHALL conflict and fail closed.
9. AFTER `prepared`, THE inherited `OperationDriver._resolve` path SHALL use its
   single terminal writer to persist the source `resolved` state, immutable
   terminal receipt, and transaction `resolved_confirmed` phase as one atomic
   owner persistence unit. It SHALL require `Persistence_Result.confirmed` for
   that unit before it updates authoritative in-memory state, untracks, or
   removes the source Operation_Record. The receipt SHALL include the resolved
   outcome, the snapshotted concrete target, the immutable snapshotted
   `target_owner_ref`, the Template_Hash, and `resolved_tick`. The driver SHALL
   then use the already-owned `accept:{op_id}:outbox` terminal slot to
   `append_reserved` an immutable mutation intent keyed
   `resolve:{op_id}:escalation`, whose payload and payload hash SHALL snapshot
   that same `target_owner_ref` and `resolved_tick`. Delivery SHALL invoke
   `BranchSystem.note_escalation_once(actor, target, resolved_tick, mutation_id)`
   with the snapshotted owner as `target` and SHALL NOT re-resolve an owner from a
   possibly mutated, transferred, or deleted target at delivery time. A confirmed
   outbox receipt SHALL gate `on_resolved_commit`; an attempted enqueue or API call
   and a missing receipt are not confirmation.
10. ONLY after Acceptance Criterion 9's terminal confirmation, and ON the first
    eligible `on_resolved_commit` attempt, THE Intrusion_System SHALL advance the
    transaction from `resolved_confirmed` to `effect_finalizing` and, BEFORE any
    reservation-transfer, active-effect, index, debt, or other domain mutation,
    durably choose and confirm in one atomic effect-finalization unit exactly one
    `effect_commit_tick` equal to that processed tick, one stable `effect_id`, and
    the final `started_tick`, `duration_ticks`, and `remaining_ticks`, where
    `started_tick` equals that `effect_commit_tick` and `duration_ticks` and
    `remaining_ticks` both equal the staged template's final proposed duration. It
    SHALL assemble those values with the template into the immutable
    Final_Effect_Payload whose shape is exactly the Active_Denial_Record schema,
    compute the immutable Final_Payload_Hash over it, persist the finalization
    receipt, and advance to `effect_finalized`. A `rejected` or `indeterminate`
    finalization SHALL permit no domain mutation whatever: no reservation transfer,
    no active record, no index entry, no debt interval, no start, and no XP. Only
    `Persistence_Result.confirmed` for the whole finalization unit SHALL permit
    `effect_committing`.
11. AFTER `effect_finalized`, `on_resolved_commit` SHALL advance the transaction
    to `effect_committing`, use keyed and receipted mutations to transfer the
    reservation from `source_operation_id` to the confirmed `effect_id`, persist
    the confirmed Final_Effect_Payload verbatim as the authoritative
    Active_Denial_Record, confirm the matching effect-persistence and index-commit
    receipts, and install the publish-last active-index entry. It SHALL then
    advance to `effect_committed` and `post_commit_pending`, never clear the
    transaction. Every retry, rebuild, or delayed commit SHALL reuse the same
    confirmed `effect_commit_tick`, `effect_id`, Final_Effect_Payload, and
    Final_Payload_Hash and SHALL NEVER re-read the current `now`, recompute a
    duration, or recompute either hash, so a commit delayed across ticks, retries,
    or a restart persists the identical record. A committed
    Active_Denial_Record SHALL be byte-equal by value to the confirmed
    Final_Effect_Payload and SHALL match its Final_Payload_Hash. No observer SHALL
    see denial until the effect-owned reservation, active record, receipt, and
    index all agree.
12. IF preparation returns `retry` or `indeterminate`, a required outbox
    reservation is rejected or indeterminate, effect finalization is rejected or
    indeterminate, or terminal persistence is
    `rejected` or `indeterminate`, THE OperationDriver SHALL leave the Pending
    operation tracked at zero and retain any one staged transaction hidden for
    retry as required by Requirement 1. A confirmed
    `settled_no_effect(reason)` SHALL use the driver's sole terminal writer to
    settle the application without an effect and SHALL advance any transaction
    through keyed compensation; the hook SHALL never mutate terminal state. A
    definite pre-terminal settlement SHALL create or expose no active artifact,
    SHALL retain its staged evidence while advancing any Resolution_Transaction
    through `compensating` to `compensated`, and SHALL release or repair the
    source claim only after confirmed keyed receipts.
13. IF effect finalization, reservation transfer, active-record persistence, or
    active-index commit
    definitely fails after durable `resolved` confirmation, THE
    Intrusion_System SHALL advance the transaction through `compensating`,
    remove or neutralize every partial active record and index entry with keyed
    receipts, expose no denial, enqueue no start or XP, release or repair the
    target reservation, and `release_once` every unconsumed
    `resolve:{op_id}:effect_outbox` slot. It SHALL retain the transaction as
    `compensated` and, only
    after Requirement 2's replay-safe retention horizon permits pruning, retain
    the compact terminal tombstone as recovery evidence. It SHALL preserve the
    source operation's terminal finality and SHALL NOT rewrite `resolved` to
    `cancelled` or delete staged evidence; eventual pruning SHALL require every
    applicable post-commit and compensation receipt to agree.
14. IF any acceptance, prepare, terminal, escalation-enqueue, finalization,
    transfer, effect, index, compensation, target-reservation, or outbox reserve,
    append, or release result is `indeterminate`, THE
    Intrusion_System SHALL retain the applicable Acceptance_Transaction or
    Resolution_Transaction, its immutable payloads and hashes, and the existing
    conservative target and slot claims; expose no staged or partial denial; and
    publish no ineligible start or
    XP. Rebuild or the next ordered write SHALL use confirming readback under the
    original keys to finish
    a fully confirmed transition or compensate a definitively rejected one. It
    SHALL never infer success or absence from an attempted write, timeout,
    non-raising writer, or unreadable record, and SHALL never release a target
    claim or a reserved slot merely because confirmation is unavailable.
15. WHEN a Pending operation is cancelled, expired, or discarded, a resolution
    transaction is compensated without an effect, or an active effect is purged,
    naturally expires, ends from source loss, or is removed for target loss, THE
    Intrusion_System SHALL perform reservation release as a keyed atomic domain
    mutation plus immutable receipt and `append_reserved` any resulting event into
    an already-reserved slot of the
    Post_Commit_Outbox. `applied` and same-payload `duplicate(prior=applied)`
    SHALL converge to
    one release; `conflict` SHALL fail closed; `rejected` SHALL produce the
    structured failure path; and `indeterminate` SHALL preserve the claim as in
    Acceptance Criterion 14.
16. WHEN rebuild finds duplicate claims for one target, THE Intrusion_System
    SHALL choose one winner in this order: a valid committed
    Active_Denial_Record with agreeing effect transaction and receipts; a valid
    Resolution_Transaction with durable `resolved_confirmed` or later evidence;
    then a valid non-terminal Operation_Record with agreeing committed
    Acceptance_Transaction. Among active records it SHALL use lowest
    `started_tick`, then lowest `source_operation_id`, then lowest `effect_id`;
    among confirmed transactions it SHALL use lowest `source_operation_id`, then
    lowest transaction identity; among Pending records it SHALL use lowest
    `op_id`. An unconfirmed transaction attached to a Pending operation is part
    of that Pending claim, not a second claim.
17. WHEN rebuild resolves duplicate claims, THE Intrusion_System SHALL transition
    each losing Resolution_Transaction through confirmed compensation and retain
    its `compensated` receipt, SHALL remove or neutralize losing partial or
    active records with keyed receipts, SHALL cancel or discard losing
    non-terminal operations through their terminal lifecycle, SHALL repair the
    target reservation to the winning operation, transaction, or effect, SHALL
    `release_once` each loser's unconsumed outbox slots after its required entries
    are durable, and SHALL install only a committed winner in an active index.
18. A source-owned reservation backed by a valid Acceptance_Transaction or
    Resolution_Transaction SHALL not be classified as orphaned while acceptance,
    terminal readback, post-resolve commit, or compensation is pending. WHEN
    rebuild confirms a reservation has no valid journal, Pending operation,
    Resolution_Transaction, committed active effect, or tombstone requiring the
    claim, it SHALL release that orphan through a keyed receipt; WHEN it finds a
    valid winner with confirmed reservation absence, it SHALL recreate the
    winner's claim without exposing an uncommitted effect.
19. THE duplicate, ambiguous, and orphan reconciliation in Acceptance Criteria
    14 through 18 SHALL run only in rebuild or an explicit maintenance/write
    path and SHALL never run as a side effect of a read-only validation or status
    query.
20. AT every observable boundary, a Pending operation SHALL remain tracked and
    have no visible denial; a staged Proposed_Denial_Template and a frozen but
    uncommitted Final_Effect_Payload SHALL remain non-authoritative; and a
    visible denial SHALL imply a retained durable `resolved` receipt even though
    its terminal source Operation_Record is untracked and removed, a confirmed
    finalization receipt, an effect-
    owned reservation, a committed Active_Denial_Record equal to the confirmed
    Final_Effect_Payload, an agreeing transaction
    receipt, and an installed active-index entry. Failure or ambiguity in
    `_resolve` SHALL never produce a Pending, untracked operation or a visible
    effect.

### Requirement 4: Central Building Operational Status

**User Story:** As a player, I want every building capability to use one
explainable operational decision, so that suspension is complete without
corrupting another reason the building is inert.

#### Acceptance Criteria

1. THE BranchSystem SHALL expose a public
   `operational_status(building)` query returning exactly one of `operational`,
   `unreadable`, `offline`, `under_construction`, `no_active_hq`,
   `branch_dormant`, or `intrusion_suspended`.
2. THE `operational_status` query SHALL evaluate reasons in this deterministic
   precedence: unreadable building or required ownership data, explicit
   `offline`, `under_construction` including upgrade, no active HQ, mismatched or
   absent Branch commitment for an affiliated building, active
   Building_Suspension, then `operational`.
3. THE BranchSystem's public `is_operational(building)` SHALL delegate to
   `operational_status(building) == "operational"` and SHALL contain no separate
   Boolean implementation.
4. THE BranchSystem SHALL expose public
   `has_active_hq(owner, planet)` and SHALL require consumers to use that method
   rather than reaching a private BranchSystem helper.
5. WHILE a valid Building_Suspension is present in the registered
   Intrusion_System's active index, THE BranchSystem SHALL return
   `intrusion_suspended` unless an earlier reason in Acceptance Criterion 2
   currently applies.
6. WHILE no Intrusion_System is registered or its active-status provider is not
   injected, THE BranchSystem SHALL treat the intrusion conjunct as satisfied
   and SHALL NOT freeze a building merely because a persisted record exists.
7. THE shipped `world.utils.building_is_operational` SHALL remain the base
   offline/construction predicate and SHALL NOT acquire Branch registry,
   commitment, HQ, or Intrusion_System dependencies.
8. THE composition root SHALL inject BranchSystem, or a callable delegating to
   its public status API, into every affected building-capability consumer and
   SHALL migrate direct capability decisions away from
   `world.utils.building_is_operational`.
9. THE migrated consumers SHALL include turret auto-fire, equipment production,
   extractor and Harvester/resource production, research progression tied to a
   lab, building capability and aura behavior, shield generation and
   regeneration behavior, and every vector origin check.
10. THE research integration SHALL retain or resolve the concrete lab whose
    research entry is advancing and SHALL pause only that entry's progression
    while the lab is not `operational`; it SHALL NOT erase research progress.
11. THE shield integration SHALL withhold only the suspended building's current
    shield capability or contribution and SHALL NOT zero, restore, or overwrite
    stored shield values.
12. THE resource and behavior-script integrations SHALL ask the central gate for
    the concrete source building before producing resources or capability
    effects, including behavior driven by an assigned agent.
13. THE implementation SHALL NOT claim that guard AI or any other consumer is
    covered merely because BranchSystem has an overlay; each behavior that
    depends on a concrete building SHALL be explicitly injected and migrated.
14. WHEN a Building_Suspension ends, THE Intrusion_System SHALL delete only the
    intrusion record and index entry; it SHALL NOT set `offline`,
    `under_construction`, HQ state, Branch commitment, or an `Operational` flag.
15. AFTER a Building_Suspension ends, THE building's current operational answer
    SHALL be recomputed from the remaining conjuncts, so an offline,
    under-construction, headless, or dormant building remains non-operational.
16. THE intrusion conjunct SHALL derive only from the presence of one valid
    Building_Suspension record in the registered provider's rebuilt index and
    SHALL NOT be copied into a second persisted online or Operational flag.
17. WHILE a Building_Suspension is active, THE target SHALL remain eligible for
    every existing damage and repair path; suspension SHALL gate capability
    behavior but SHALL grant neither combat protection nor a repair refusal.

### Requirement 5: Shared Agent Behavior Availability

**User Story:** As a player, I want Jam to pause behavior without rewriting my
agent, so that unrelated agent state survives every ending.

#### Acceptance Criteria

1. THE AgentSystem SHALL expose public `behavior_status(agent)` and
   `is_behavior_available(agent)` queries, and the Boolean query SHALL delegate
   to the reason-valued query.
2. THE `behavior_status` query SHALL deterministically distinguish at least
   `available`, `unreadable`, `dead`, `reserve`, `incapacitated`, and `jammed`,
   with the pre-existing non-Jam reasons evaluated before `jammed`.
3. WHILE a valid Agent_Jam is present in the registered Intrusion_System's active
   index, THE AgentSystem SHALL return `jammed` unless an earlier non-Jam reason
   currently applies.
4. WHILE no Intrusion_System or Jam-status provider is injected, THE AgentSystem
   SHALL treat the Jam conjunct as satisfied and SHALL NOT freeze an agent from
   persisted data it does not own.
5. WHILE an agent is jammed, THE AgentSystem's `process_tick` SHALL skip that
   agent's time-served or other per-tick progression and SHALL skip its ordinary
   interval-zero behavior-script dispatch.
6. THE AgentSystem SHALL implement the Jam gate without detaching, deleting,
   recreating, pausing, or reattaching the agent's scripts.
7. THE BranchSystem's selected-carrier eligibility and THE OperationDriver's
   runtime carrier-unavailable decision SHALL consume the same AgentSystem
   behavior-availability result rather than duplicating reserve,
   incapacitation, or Jam reads.
8. WHILE a jammed agent carries a non-Intrusion Vector_Operation, THE
   OperationDriver SHALL suspend that operation through its ordinary
   carrier-unavailable path and SHALL resume it only when the shared predicate
   becomes available again.
9. WHEN an Agent_Jam ends, THE Intrusion_System SHALL remove only the Jam record
   and index entry; it SHALL NOT change reserve, incapacitation, role,
   assignment, movement, carried resources, experience, hit points, or script
   attachment.
10. IF a Jam target dies or leaves the world permanently, THEN THE
    Intrusion_System SHALL remove its Agent_Jam before the target record is
    deleted and SHALL NOT attempt to restore agent state.

### Requirement 6: Requests, Targets, Costs, and Response Windows

**User Story:** As a Signals player, I want both modes to use concrete targets
and the same hostile-operation guardrails, so that neither mode bypasses shared
protection or throttling.

#### Acceptance Criteria

1. WHERE a player holds the `cyber` Branch commitment, WHEN that player requests
   either mode, THE Intrusion_System SHALL pass the concrete enemy building or
   agent as `target_ref` to `BranchSystem.may_target` with `hostile = true`.
2. THE command and request layers SHALL require the player to select one
   originating Cypher Node, one infiltrator, and one concrete target; THE
   Intrusion_System SHALL validate that selected infiltrator rather than silently
   substituting the first eligible roster entry.
3. THE selected Cypher Node SHALL be owned by the attacker, unlocked, and
   `operational`, and THE selected infiltrator SHALL be alive, in the world, on
   the operation's planet, assigned the `infiltrator` role, outside reserve,
   not incapacitated, and not jammed.
4. WHEN `mode = "plant"` is requested, THE target SHALL be an enemy building
   whose `operational_status` is `operational`, and THE selected infiltrator
   SHALL occupy a tile equal or adjacent to that building's tile.
5. WHEN `mode = "jam"` is requested, THE target SHALL be an enemy agent whose
   `behavior_status` is `available`, and that agent SHALL be within the
   snapshotted `jam_radius` of the selected Operational Cypher Node.
6. WHEN either request is accepted, THE Intrusion_System SHALL snapshot the
   selected infiltrator's exact planet, x, and y as the Plant_Origin, and SHALL
   resolve and persist the concrete target's canonical persistent owner as the
   immutable `target_owner_ref` required by Requirements 2 and 3. That resolution
   SHALL be a read-only admission step, SHALL be the one owner snapshot used by
   every later terminal receipt, escalation payload, and delayed delivery, and
   SHALL fail the request closed before any domain mutation when the canonical
   owner is absent, noncanonical, or unreadable. THE Intrusion_System SHALL NOT
   substitute a coordinate, alliance, or `may_target` result for that owner and
   SHALL NOT use the snapshot for any live ownership-sensitive decision.
7. WHEN a building PLANT is accepted, THE Intrusion_System SHALL snapshot the
   unfloored application value as `intrusion_plant_ticks` plus exactly one
   applicable Firewall penalty. It SHALL derive the persisted
   `required_plant_ticks` by applying the inherited hostile response-window
   floor exactly once at the durable Pending-warning enqueue and SHALL retain
   that application marker in the Acceptance_Transaction.
8. WHEN a Jam_Application is accepted, THE Intrusion_System SHALL snapshot the
   unfloored application value as `jam_application_ticks` and SHALL derive
   `required_plant_ticks` by the same one-time floor and warning marker as
   Acceptance Criterion 7.
9. WHEN either application enters Pending, THE Intrusion_System SHALL durably
   enqueue the target-owner warning with mode, source, target, and complete
   response ticks before the record is tick-eligible. It SHALL deliver through
   `publish_once`; duplicate delivery MAY render again from the immutable payload
   but SHALL neither decrement nor refloor the countdown.
10. THE Intrusion_System SHALL use `intrusion_cost` for `mode = "plant"` and
    `jam_cost` for `mode = "jam"`, SHALL use the public BranchSystem shortfall
    query without mutation, and SHALL use
    `BranchSystem.charge_once` and `BranchSystem.refund_once` with the acceptance
    keys and receipts in Requirement 3 rather than any unkeyed charge or refund.
11. THE Intrusion_System SHALL apply the `intrusion` cooldown to both modes on the
    same per-origin basis through `BranchSystem.note_cooldown_once` with
    `accept:{op_id}:cooldown`, so alternating modes or replay cannot bypass or
    duplicate cooldown.
12. THE BranchSystem's `intrusion_max_in_flight` count SHALL combine all
    non-terminal building PLANT and Jam_Application records owned by that player
    on that planet, regardless of mode.
13. THE in-flight count SHALL exclude terminal operations and separately active
    Building_Suspension and Agent_Jam records; active exclusivity SHALL be
    enforced by target reservations instead.
14. THE Intrusion_System SHALL refuse self-owned and allied hostile targets,
    shielded targets, and escalation-limited targets through the shared
    `may_target` result and SHALL NOT duplicate those policies.
15. WHEN a target is already inert for a reason other than this request, THE
    Intrusion_System SHALL refuse rather than reserve or charge it and SHALL
    report its building operational reason or agent behavior reason.

### Requirement 7: Pending Application Cancellation Matrix

**User Story:** As a defender, I want a hostile application to require
continuous source viability, so that movement or disabling the attacker prevents
the denial from being created.

#### Acceptance Criteria

1. WHILE either mode is Pending, THE OperationDriver SHALL use
   `origin_fatal_reason` only for physical or source-fatal loss independent of
   Branch commitment and never for `branch_dormant`. After target checks and
   that fatal check, the Signals per-vector policy SHALL revalidate the same
   selected carrier, Plant_Origin, origin building's nonphysical operational
   status, source base, and `cyber` commitment before decrementing the
   application clock.
2. IF the selected infiltrator leaves the exact persisted Plant_Origin planet or
   tile, THEN THE Intrusion_System SHALL cancel the Pending application, create
   no active effect, and release its target reservation.
3. IF the selected infiltrator enters reserve, becomes incapacitated or jammed,
   loses the infiltrator role, dies, or leaves the world before activation, THEN
   THE Intrusion_System SHALL cancel the Pending application and create no
   active effect.
4. IF the originating Cypher Node is destroyed, leaves the world, or returns any
   operational reason other than `operational`, including the nonphysical reason
   `branch_dormant`, THEN THE Intrusion_System SHALL cancel the Pending
   application and create no active effect. `origin_fatal_reason` SHALL not
   misclassify such a Branch-policy reason as physical source loss.
5. IF the target is destroyed, leaves the world, or its base is eliminated, THEN
   THE Intrusion_System SHALL cancel the Pending application before target-owned
   records are deleted and SHALL create no active effect.
6. IF a building target's status ceases to be `operational`, or IF a Jam target's
   behavior ceases to be `available`, before activation for a reason other than
   this operation's reservation, THEN THE Intrusion_System SHALL cancel the
   Pending application as targeting an already inert entity.
7. IF a Jam target leaves the selected Cypher Node's snapshotted jam radius
   before activation, THEN THE Intrusion_System SHALL cancel the Jam_Application
   and create no Agent_Jam.
8. IF the attacker's base is eliminated or the attacker loses the `cyber`
   commitment on the operation's planet, THEN THE Intrusion_System SHALL cancel
   the Pending application and create no active effect.
9. THE Intrusion_System SHALL override the inherited carrier-unavailable,
   nonphysical-origin, and commitment-lapse suspension policies for both modes,
   so reserve, incapacitation, Jam, any non-`operational` origin status, or
   commitment lapse produces terminal `cancelled`, not
   `OperationState.SUSPENDED`. A generic vector resume SHALL preserve the exact
   remaining countdown without a new response floor; Signals normally has no
   such resume because these conditions cancel it.
10. WHEN two or more cancellation conditions and application resolution fall on
    the same tick, THE Intrusion_System SHALL evaluate target loss or target
    inertness, then source or carrier invalidity under the split in Acceptance
    Criterion 1, then movement/range validity, and SHALL decrement or resolve the
    application only if every preceding check passes.
11. IF the carrier dies on the tick its application would otherwise resolve,
    THEN THE carrier-death cancellation SHALL win and no persistent denial SHALL
    survive that tick.
12. WHEN a Pending application is cancelled by any criterion in this
    requirement, THE Intrusion_System SHALL award no operation XP, add no
    Suspension_Debt interval, perform reservation release and cancellation
    persistence through keyed atomic receipts, and `append_reserved` one
    structured cancellation outcome into the Acceptance_Transaction's
    already-owned `accept:{op_id}:outbox` terminal-outcome slot. It SHALL then
    `release_once` every unconsumed slot of that reservation and of any
    `resolve:{op_id}:effect_outbox` reservation the cancelled application had
    confirmed, before the terminal source-removal gate in Requirement 1. An
    indeterminate result
    SHALL retain the applicable journal and claim rather than report completion.

### Requirement 8: Active Effect Creation, Timing, and Endings

**User Story:** As a player, I want active denial to be bounded and source-linked
without depending on a terminal operation, so that every ending is deterministic.

#### Acceptance Criteria

1. WHEN a building PLANT reaches zero after all revalidation, THE
   Intrusion_System SHALL resolve the target owner's current Branch and request
   exactly one `BranchSystem.counter_multiplier_checked("cyber",
   target_branch)` result for that prepare attempt. The checked result SHALL be
   explicitly `neutral(1.0)`, `advantage(multiplier)`,
   `unavailable(reason)`, or `invalid(reason)`. `neutral(1.0)` is an affirmative
   result, not a fallback; only `neutral` and an `advantage` carrying a finite,
   non-Boolean real multiplier permit arithmetic.
2. THE Intrusion_System SHALL compute a Building_Suspension's proposed effective
   duration as `min(snapshotted_intrusion_max_duration_ticks,
   ceil(snapshotted_intrusion_duration_ticks * checked_multiplier))`, SHALL NOT
   compound edges or multipliers, and SHALL pass that final proposed duration to
   the pure debt check. A transient `unavailable` result SHALL stage or commit no
   denial and SHALL leave the due operation and any existing transaction hidden
   and retained for ordered retry. A confirmed `invalid` configuration SHALL
   return a structured `settled_no_effect(reason)`, commit no denial or XP, and
   drive appropriate reservation and transaction compensation through the
   OperationDriver; neither outcome SHALL be coerced to `1.0`.
3. WHEN a Jam_Application reaches zero after all revalidation, THE
   Intrusion_System SHALL use its snapshotted `jam_duration_ticks` as the
   Agent_Jam duration independently of building Suspension_Debt and SHALL not
   perform a Counter_Web lookup.
4. `on_resolve` MAY stage the final proposed effective duration inside the
   immutable Proposed_Denial_Template, but staging SHALL start no active clock,
   SHALL read no current `now` into a staged field, and SHALL carry no
   commit-time field; the Template_Hash SHALL cover only those template fields.
   ON the first eligible `on_resolved_commit` attempt and BEFORE any
   reservation-transfer, active-effect, index, debt, or other domain mutation,
   THE Intrusion_System SHALL durably choose and confirm in one atomic
   finalization unit exactly one `effect_commit_tick` equal to that processed
   tick, one stable `effect_id`, and the final `started_tick`, `duration_ticks`,
   and `remaining_ticks`, where `started_tick` equals that `effect_commit_tick`
   and `duration_ticks` and `remaining_ticks` both equal the staged template's
   final proposed duration. It SHALL assemble those confirmed values with the
   template into the immutable Final_Effect_Payload and compute its immutable
   Final_Payload_Hash as required by Requirement 3. A `rejected` or
   `indeterminate` finalization SHALL permit no domain mutation whatever. Every
   retry, rebuild, or delayed commit SHALL reuse that same confirmed tick,
   identity, payload, and hash and SHALL NEVER re-read the current `now` or
   recompute the duration, so a commit delayed across ticks, retries, or a
   restart persists the identical record.
5. THE Intrusion_System SHALL make a newly committed active effect queryable only
   after confirmed reservation transfer, Active_Denial_Record persistence,
   agreeing `effect_committed` transaction receipt, and publish-last active-index
   installation. It SHALL NOT decrement `remaining_ticks` on that commit tick. A
   durable transaction, attempted write, indeterminate receipt, or partial active
   record alone SHALL never be visible.
6. THE inherited `OperationDriver._resolve` path after confirmed terminal
   transition SHALL be the sole Signals escalation path. It SHALL
   `append_reserved` the immutable `resolve:{op_id}:escalation` intent into the
   already-owned `accept:{op_id}:outbox` terminal slot with immutable actor, the
   snapshotted canonical `target_owner_ref`, and `resolved_tick`, and its payload
   hash SHALL cover that snapshotted owner. Delivery SHALL call
   `BranchSystem.note_escalation_once(actor, target, resolved_tick, mutation_id)`
   with that snapshotted owner as `target`; it SHALL NOT re-resolve an owner from
   a mutated, transferred, or deleted target, and no delayed delivery, retry, or
   rebuild SHALL require the concrete target to still exist. An original
   `applied` or a same-payload `duplicate(prior=applied)` SHALL be the sole
   successful mutation outcome; `duplicate(prior=rejected)` SHALL retain the
   original no-application decision, `conflict` SHALL fail closed, and
   `indeterminate` SHALL retain the entry and its claim for confirming readback.
   Intrusion_System hooks, retries, rebuild, compensation, and reconciliation
   SHALL never call keyed or unkeyed escalation directly, and the unkeyed legacy
   API SHALL carry no exactly-once claim.
7. ONLY after authoritative reservation/effect/index commit and same-tick
   source-invalid reconciliation leave the effect active, THE post-resolve path
   SHALL durably enqueue an active-start event keyed by (`effect_id`, `start`)
   and the snapshotted XP mutation keyed by `resolve:{op_id}:xp`. XP delivery
   SHALL call `AgentSystem.award_operation_xp_once(agent, "intrusion", amount,
   mutation_id)` with the immutable snapshotted amount; start delivery SHALL use
   `publish_once`. Both entries SHALL consume already-reserved
   `resolve:{op_id}:effect_outbox` slots. Retry or rebuild SHALL replay retained
   outbox entries, with an original `applied` or same-payload
   `duplicate(prior=applied)` producing at most one external award or event and
   `duplicate(prior=rejected)` producing none. Staging, cancellation,
   compensation, terminal-transition failure,
   transfer/effect/index failure, or same-tick source invalidity SHALL enqueue no
   activation XP or start, and no delivery SHALL reread live Balance_Config.
8. WHILE an active effect exists, THE Intrusion_System SHALL decrement its clock
   exactly once per processed world tick without depending on active chunks,
   online status, carrier position, carrier reserve, or carrier incapacitation.
9. IF the carrier dies, the originating Cypher Node is destroyed or becomes
   non-operational, the attacker's base is eliminated, or the attacker loses the
   `cyber` commitment on that planet, THEN THE Intrusion_System SHALL end every
   linked active effect early.
10. IF only the carrier moves, enters reserve, or becomes incapacitated after
    activation, THEN THE Intrusion_System SHALL continue the active effect's
    countdown without pause or reset.
11. IF the target is destroyed, dies, leaves the world, or its base is
    eliminated, THEN THE Intrusion_System SHALL choose and persist the target-
    loss ending through a keyed ending receipt, remove its active effect and
    reservation through keyed mutations, and preserve all recovery receipts and
    outbox work before deletion of target-owned records.
12. WHEN one active-effect tick observes competing endings, THE
    Intrusion_System SHALL apply this precedence: target gone; source invalid
    ending; purge validation and completion for a Building_Suspension; natural
    expiry.
13. IF a valid purge and natural expiry complete on the same tick, THEN THE
    Intrusion_System SHALL classify the ending as `purged` and SHALL NOT also
    classify or notify it as natural expiry.
14. WHEN any active effect ends, THE Intrusion_System SHALL atomically choose one
    immutable ending kind and tick per `effect_id`, then use stable keyed
    mutations and receipts for active-record/index removal, reservation release,
    and any debt closure, and durably `append_reserved` the structured ending
    event into an already-reserved
    `resolve:{op_id}:effect_outbox` slot. A replay SHALL return
    `duplicate(prior=<original_outcome>)` for each of those keys, so
    `duplicate(prior=applied)` ensures the one existing change or entry and
    `duplicate(prior=rejected)` authorizes none; a different payload SHALL
    conflict and fail closed; an indeterminate step SHALL retain the ending
    journal and claim for ordered recovery rather than publish completion early.
15. WHEN a Building_Suspension ends, THE Intrusion_System SHALL remove only the
    intrusion conjunct; WHEN an Agent_Jam ends, it SHALL remove only the Jam
    conjunct; neither path SHALL restore a saved Boolean or script state.
16. IF carrier death or another source-invalid event races staging, terminal
    resolution, or active-effect commit in the same tick, THEN source invalidity
    SHALL win, and no Building_Suspension or Agent_Jam from that source SHALL
    remain persisted or visible at the end of the tick. The transaction and
    ending receipts SHALL remain as recovery evidence, and no activation start
    or XP SHALL be enqueued.
17. AT the beginning of each Intrusion_System tick, THE Intrusion_System SHALL
    capture one tick-start validity snapshot of its indexed Pending applications
    and pre-existing committed active effects after all lifecycle events already
    delivered for that tick, plus the ordered Acceptance_Transactions,
    Resolution_Transactions, ending journals, and Post_Commit_Outbox entries
    eligible for reconciliation in that tick.
18. THE Intrusion_System SHALL process one tick in these deterministic phases:
    pre-existing committed active effects in ascending (`started_tick`,
    `effect_id`) order using Acceptance Criterion 12; pre-existing acceptance and
    resolution transactions requiring recovery in ascending (`op_id`,
    transaction identity) order, each advancing only to its next confirmed,
    compensated, retry, rejected, or indeterminate boundary; Pending
    revalidation and countdown advancement in ascending `op_id` order against
    the tick-start snapshot; then due operations in ascending `op_id` order,
    each performing its result-preserving prepare, inherited terminal write, and
    confirmed post-resolve commit inline up to its first non-confirmed boundary
    before the next due operation begins; then post-commit source-invalid
    reconciliation in ascending `effect_id` order; and finally eligible keyed XP
    mutations, outbox reservation reconciliation, required `append_reserved`
    entries, `release_once` closures, and outbox deliveries in stable
    immutable-key order. A retained
    retry SHALL keep its original ordering key and SHALL not move behind later
    work merely because it is replayed. Every phase that performs an irreversible
    mutation SHALL already hold its confirmed exact reservation from Requirement 1;
    a claimless capacity rejection SHALL end that item's work for the tick without
    reordering, truncating, or skipping any other item.
19. THE phase order SHALL make same-tick results independent of tracked-list,
    persistence-discovery, spatial-candidate, outbox, and database iteration
    order. An effect created in a commit or reconciliation phase SHALL become
    queryable only after complete authoritative commit and SHALL be eligible for
    post-commit source-invalid reconciliation, but SHALL receive no active clock
    or purge progress until the next processed tick. Its start and XP remain
    ineligible until that reconciliation survives.
20. A retrying, rejected, failed, or indeterminate prepare or `_resolve`
    transition SHALL leave its proposed denial non-authoritative and its due
    Pending operation tracked at zero unless a different terminal settlement is
    confirmed. It SHALL never stage a second transaction for the same immutable
    payload or untrack an operation merely because staging succeeded; only the
    matching durable terminal receipt permits post-resolve commit, and the
    retained Resolution_Transaction remains recovery authority after source-
    record removal.

### Requirement 9: Exact Rolling Suspension Debt

**User Story:** As a defender, I want repeated building denial bounded by actual
time denied across all attackers, so that sequential attacks cannot create
permanent suppression.

#### Acceptance Criteria

1. THE Intrusion_System SHALL persist completed Suspension_Debt as target-global
   actual intervals `[start_tick, end_tick)` on the building, without partition
   by attacker.
2. AT validation tick `now` with configured window `W`, THE Intrusion_System
   SHALL calculate `current_debt` as the sum, over every retained completed
   interval and any active Building_Suspension interval, of
   `max(0, min(end, now) - max(start, now - W))`, treating an active interval's
   end as `now`.
3. DURING initial pure request validation and again immediately before
   `on_resolve` prepares a Resolution_Transaction, THE Intrusion_System SHALL
   obtain exactly one current `counter_multiplier_checked` result for that debt
   preview when building mode applies. The final preview SHALL reuse the same
   single checked result required by Requirement 8 for that prepare attempt and
   SHALL NOT perform a second lookup. THE Intrusion_System SHALL derive a
   proposed duration only from affirmative `neutral` or `advantage` and reject
   the attempted transition when `current_debt + proposed_effective_duration >
   suspension_debt_cap_ticks`.
4. WHEN the initial debt check rejects, THE Intrusion_System SHALL refuse the
   request with all persistence unchanged; an initial transient Counter_Web
   `unavailable` SHALL also return a structured no-mutation unavailability rather
   than use a fallback. WHEN the final debt check rejects, it SHALL return
   `settled_no_effect(reason)`, cancel the Pending operation through the driver's
   confirmed terminal path, create no effect or debt interval, compensate the
   reservation and transaction with keyed receipts, and award no XP. A transient
   final `unavailable` SHALL instead retain the due operation and hidden
   transaction for retry as specified in Requirement 8.
5. FOR each debt check, THE `proposed_effective_duration` SHALL include exactly
   one affirmative checked cyber-to-target-Branch multiplier current at that
   check and the snapshotted `intrusion_max_duration_ticks` clamp from
   Requirement 8; `neutral(1.0)` SHALL be used because it is an explicit
   affirmative result, never because lookup failed. Only the final activation
   value SHALL become the persisted effect duration.
6. WHEN either debt check rejects, THE Intrusion_System SHALL report the earliest
   tick `t >= now` for which the same retained actual intervals evaluated over
   `[t - W, t)` would satisfy
   `debt_at_t + proposed_effective_duration <= cap`, together with `t - now`.
7. THE debt validation and earliest-eligibility calculation SHALL be pure reads
   and SHALL NOT prune, merge, append, or rewrite the debt ledger.
8. THE Intrusion_System SHALL prune only during explicit maintenance or a write
   already updating the debt ledger, and SHALL prune only intervals with
   `end_tick <= now - 604800`; shrinking the currently configured window SHALL
   NOT delete history that a larger valid future window or an accepted request's
   snapshotted window can still require.
9. WHEN both debt fields are zero, THE Intrusion_System SHALL disable the debt
   rule and SHALL store no new debt intervals; WHEN enabled, both fields SHALL
   be positive.
10. WHEN a Building_Suspension ends naturally, by purge, or from source loss, THE
    Intrusion_System SHALL close debt through a stable mutation key derived from
    `effect_id` and the immutable ending receipt and SHALL append only its actual
    elapsed interval `[started_tick, ending_tick)`. The ledger change and
    immutable outcome receipt SHALL be atomic and replay-safe; it SHALL NOT
    charge the proposed or originally scheduled remainder as debt.
11. IF a same-tick source loss removes an effect at its `started_tick`, THEN THE
    zero-length interval SHALL contribute zero debt and need not be stored, but a
    keyed no-op debt-closure receipt SHALL still prevent a replay from appending
    a later interval.

### Requirement 10: Firewall, Counter_Web, and Doctrine Counter

**User Story:** As a defender, I want counterplay to use the correct doctrine and
perk semantics, so that Signals does not accidentally counter itself as a
Doctrine_Counter.

#### Acceptance Criteria

1. WHEN a building PLANT is accepted, THE Intrusion_System SHALL use its
   injected bounded spatial index to query only the configured `firewall_radius`
   around the target building on the target planet, and SHALL filter only the
   returned nearby candidates for current target ownership, Cypher Node identity,
   and `operational` status. It SHALL NOT enumerate the target owner's full
   building roster, scan all world objects, or issue a full-table or database
   scan; lookup work SHALL be proportional to the bounded queried area plus the
   candidates returned from that area.
2. IF one or more eligible target-owned Cypher Nodes are in range, THEN THE
   Intrusion_System SHALL add exactly one snapshotted
   `firewall_plant_penalty_ticks` value to that PLANT's required response ticks
   and SHALL persist `firewall_applied = true`.
3. IF no eligible Cypher Node is in range, or WHEN `mode = "jam"`, THE
   Intrusion_System SHALL persist `firewall_applied = false` and SHALL add no
   Firewall penalty.
4. AFTER a PLANT is accepted, THE destruction, construction, movement in status,
   or Balance_Config reload of any Firewall Cypher Node SHALL NOT change that
   application's snapshotted required ticks.
5. THE Intrusion_System SHALL identify Firewall as a Signals self-perk and SHALL
   NOT identify it as the Signals Doctrine_Counter.
6. THE Signals Doctrine_Counter SHALL be Biowarfare's standard Contagion or
   combat damage acting on the live infiltrator through existing damage and
   death paths.
7. THE Intrusion_System SHALL import no Biowarfare implementation, SHALL grant no
   direct Contagion immunity or special damage, and SHALL react only to the
   ordinary carrier-death event and source validity result.
8. WHEN Doctrine_Counter damage kills the infiltrator during a Pending
   application, THE operation SHALL cancel; WHEN it kills the infiltrator during
   an active effect, THE effect SHALL end under Requirement 8.
9. THE Intrusion_System SHALL consume no more than one affirmative
   `counter_multiplier_checked` result for one final Building_Suspension
   duration and SHALL never multiply a path, multiple edges, Firewall, or
   multiple Cypher Nodes together. It SHALL treat `neutral(1.0)` as an explicit
   checked outcome and SHALL never convert `unavailable`, `invalid`, unreadable,
   or malformed results into a numeric fallback.

### Requirement 11: Persisted Purge Attempts

**User Story:** As a target owner, I want purge progress to require one
continuously valid actor and survive restart accurately, so that neither retries
nor downtime grant free progress.

#### Acceptance Criteria

1. WHILE a Building_Suspension is active, WHEN its owner or an allied player
   with the target owner's `support` consent explicitly selects a player or
   entity actor and requests purge, THE Intrusion_System SHALL serialize on that
   target and `effect_id` and validate both requester and actor immediately
   before the purge-start mutation.
2. THE selected player actor SHALL be the requester, or THE selected entity
   actor SHALL be currently owned or controlled by the requester; THE command
   layer SHALL refuse an actor the requester does not control and SHALL permit
   no implicit delegation.
3. IMMEDIATELY before every purge start, progress, abandonment, or completion
   mutation and within the same target/effect serialization boundary, THE
   Intrusion_System SHALL freshly resolve the target owner, selected actor's
   current controller, actor life and world presence, exact target-building
   tile, alliance, and the target owner's current public BranchSystem `support`
   consent. The controller SHALL be the target owner or remain allied with that
   consent. Any loss, mismatch, or unreadability SHALL fail closed and SHALL be
   resolved atomically as abandonment/reset rather than grant progress.
4. THE Intrusion_System SHALL permit at most one in-progress Purge_Attempt per
   Building_Suspension.
5. WHEN the same actor repeats a purge request for the same `effect_id` and
   immutable payload, THE Intrusion_System SHALL return the existing attempt and
   its `duplicate(prior=<original_outcome>)` keyed mutation receipt without
   resetting or decrementing it; WHEN a
   different actor or payload competes, it SHALL return `conflict`, fail closed,
   and report the current actor.
6. WHEN a purge starts after Acceptance Criterion 3's serialized validation, THE
   Intrusion_System SHALL first confirm the exact two-slot
   `purge:{effect_id}:{attempt_id}:outbox` reservation required by Requirement 1,
   then snapshot current `intrusion_purge_ticks` into
   `purge_state.remaining_ticks`, set `actor_ref`, set `last_validated_tick` to
   the current processed tick without granting immediate progress, and commit
   that state plus immutable keyed start receipt atomically. It SHALL
   `append_reserved` the purge-start event into the first reserved slot and leave
   the second unconsumed for the eventual single abandonment. A claimless capacity
   rejection SHALL refuse the purge request before any purge-state, receipt, or
   event mutation and SHALL stay retriable under the same reservation ID; an
   indeterminate reservation SHALL retain its claim and start no attempt.
7. ON each later processed tick, THE Intrusion_System SHALL perform Acceptance
   Criterion 3's validation and the keyed progress mutation in one serialized
   boundary for the same actor. It SHALL decrement `remaining_ticks` at most once
   for that tick; a duplicate `last_validated_tick` SHALL return the prior
   receipt and grant no additional progress.
8. AFTER a restart or other gap in processing, THE Intrusion_System SHALL retain
   persisted remaining progress but SHALL credit no missing tick; the first
   serialized validation after a numeric tick gap SHALL re-anchor
   `last_validated_tick` with a keyed receipt but without decrementing, and only
   consecutive later validations SHALL advance.
9. IF the actor leaves the exact tile, dies, leaves or disconnects from the
   world, is no longer controlled by an authorized owner or consenting ally,
   loses the required alliance or `support` consent, or any required ownership
   or consent read is unreadable, THEN THE Intrusion_System SHALL atomically
   abandon the attempt in that same serialization boundary, clear `actor_ref`
   and `last_validated_tick`, reset progress for a future request, retain the
   Building_Suspension, and `append_reserved` one keyed abandonment event into the
   attempt's second reserved slot. It SHALL then `release_once` that reservation
   once its manifest is complete, and SHALL grant no progress from that
   validation. A later attempt SHALL take its own fresh `attempt_id` reservation.
10. WHEN purge `remaining_ticks` would reach zero, THE Intrusion_System SHALL
    reperform Acceptance Criterion 3 immediately before the completion mutation
    in the same serialized boundary. On success it SHALL atomically choose the
    immutable `purged` ending receipt and use keyed receipts for suspension/index
    removal, reservation release, and debt closure; on loss or unreadability it
    SHALL apply Acceptance Criterion 9 instead. An indeterminate result SHALL
    retain the attempt and ending journal hidden for ordered reconciliation.
11. WHEN purge completes, THE Intrusion_System SHALL `append_reserved` one
    ending entry keyed by (`effect_id`, `purged`) with the target owner and
    attacker as its bounded recipients into the effect's already-reserved
    `resolve:{op_id}:effect_outbox` ending slot, use that reservation's
    debt-closure slot for the keyed debt closure, and deliver
    through `publish_once`; duplicate purge commands, ticks, lifecycle
    events, or replay SHALL publish no second externally applied completion. It
    SHALL then `release_once` the attempt's `purge:{effect_id}:{attempt_id}:outbox`
    reservation, whose abandonment slot is now definitively unneeded, and
    `release_once` the effect reservation's remaining unconsumed slots after every
    required ending entry is durable.
12. THE Intrusion_System SHALL apply the ending precedence in Requirement 8, so
    target loss wins over purge, source-invalid ending wins over purge, and purge
    wins an exact tie with natural expiry.
13. THE Intrusion_System SHALL expose no purge command for Agent_Jam unless a
    future specification adds one; the persisted inactive Jam purge shape SHALL
    not make Jam purgeable.

### Requirement 12: Lifecycle Events, Rebuild, and Feature Absence

**User Story:** As an operator, I want event ordering and restart recovery to be
explicit, so that target-owned records are neither deleted too early nor turned
into hidden flags.

#### Acceptance Criteria

1. THE destruction and base-elimination flows SHALL invoke the Intrusion_System's
   target handlers before deleting a target building, target agent, or any
   target-owned persisted operation, Acceptance_Transaction,
   Resolution_Transaction, active-effect, reservation, debt, purge, or ending
   container. Before such deletion, every receipt or tombstone still needed for
   replay protection SHALL be durably copied to a recovery container whose
   lifetime is independent of the target, and every unsettled Post_Commit_Outbox
   entry and unconsumed reserved slot SHALL survive in the global outbox rather
   than in target-owned storage. Because each terminal receipt and escalation
   entry already snapshots the immutable canonical `target_owner_ref`, an archived
   or delayed `resolve:{op_id}:escalation` delivery SHALL remain replayable after
   the concrete target is deleted and SHALL NOT re-resolve, repair, or invalidate
   that owner from the deleted target.
2. WHEN a target destruction or target-base-elimination handler runs, THE
   Intrusion_System SHALL settle Pending applications only through the
   OperationDriver, advance Acceptance_Transactions and Resolution_Transactions
   through phase-appropriate confirmed compensation, choose target-loss endings
   for committed effects, and use keyed receipts for effect/index removal,
   reservation release, debt closure, and outbox enqueue. It SHALL retain
   compensated transactions or compact tombstones and SHALL permit target
   deletion only after all target-owned authoritative facts are confirmed
   removed or safely archived, every required outbox entry is durable, and every
   unconsumed slot of the affected reservations is released through
   `release_once`; an indeterminate result SHALL block premature
   deletion rather than erase recovery evidence. Deletion SHALL never rewrite or
   drop the snapshotted `target_owner_ref` that a retained receipt, archived
   escalation entry, or tombstone still needs.
3. WHEN a carrier, origin building, attacking base, or `cyber` commitment is
   lost, THE Intrusion_System SHALL end every indexed or journaled Pending
   application, in-progress Resolution_Transaction, or committed active effect
   linked to that source according to Requirements 3, 7, and 8, while preserving
   terminal receipts after source Operation_Record removal.
4. THE target- and source-loss handlers SHALL use persisted reference links and
   acceptance, Pending, resolution, active, ending, and outbox indexes rather
   than scanning every world entity on each event.
5. WHEN the server starts, THE Intrusion_System SHALL independently rebuild and
   reconcile Acceptance_Transactions, non-terminal applications,
   Resolution_Transactions, Building_Suspension, Agent_Jam, purge, debt,
   reservation, ending, Post_Commit_Outbox, and terminal-tombstone indexes solely
   from persisted records, immutable receipts, and confirmed terminal readback.
6. THE rebuild SHALL NOT infer denial, authoritative absence, terminal success,
   mutation application, or transaction commit from `offline`, an activity-
   status string, a missing script, an attempted or non-raising write, a timeout,
   an unreadable confirming read, or any other mutable entity flag.
7. THE rebuild SHALL reconcile every Resolution_Transaction by its durable phase
   before installing authoritative active-index entries. `staged` and
   `indeterminate` work SHALL use terminal and mutation readback to retry or
   compensate; `resolved_confirmed` and `effect_committing` SHALL finish each
   receipted commit step or enter confirmed compensation;
   `effect_committed` and `post_commit_pending` SHALL verify reservation, active
   record, and index agreement, install only the committed entry, and replay
   eligible keyed post-commit work; `complete` and `compensated` SHALL remain
   replay authority until Requirement 2's retention horizon permits a compact
   tombstone. A confirmed non-resolved, other-terminal, or definitively failed
   transaction SHALL compensate by phase. An ambiguous transaction SHALL remain
   hidden with its claim preserved. Rebuild SHALL never clear a transaction at
   effect commit or delete staged evidence. It SHALL prune staged evidence only
   after the transaction is `complete` or `compensated`, every authoritative fact
   and applicable post-commit or compensation receipt agrees, the replay-safe
   retention horizon has elapsed, and a compact terminal tombstone is durable.
8. WHEN rebuild completes, THE BranchSystem and AgentSystem SHALL derive
   operational and behavior availability only by querying committed entries in
   the rebuilt active indexes plus their non-Intrusion conjuncts; staged,
   indeterminate, compensated, or partial records SHALL never enter those
   indexes.
9. IF the Intrusion_System is absent, unwired, or unregistered, THEN persisted
   acceptance, resolution, active-effect, ending, and outbox records SHALL remain
   stored for a later rebuild but SHALL apply no building or agent denial while
   the provider is absent.
10. WHEN the Intrusion_System is registered later, THE composition root SHALL run
    complete rebuild, phase-based transaction reconciliation, committed-index
    validation, and eligible outbox recovery before exposing its provider to
    operational or behavior queries, so a partially rebuilt index is never
    authoritative.
11. AFTER one startup discovery pass, THE Intrusion_System SHALL maintain
    acceptance, Pending, resolution-transaction, committed active-effect, purge,
    debt, reservation, ending, outbox, and tombstone indexes incrementally from
    confirmed requests, transitions, lifecycle events, and receipts.
12. THE Intrusion_System SHALL NOT perform a full-owner-roster, full-world-object,
    full-table, or database scan during ordinary request validation, status
    reads, or per-tick advancement. Firewall admission SHALL use Requirement
    10's bounded spatial query, with work proportional to bounded area plus
    returned candidates.
13. Rebuild and incremental maintenance SHALL preserve a source-owned reservation
    for an ambiguous or confirmed resolution transaction until phase-based
    readback can finish transfer or compensation. They SHALL process transactions
    in Requirement 8's stable ascending order, SHALL NOT expose an effect merely
    to avoid holding a conservative claim, and SHALL not release that claim from
    unreadability.

### Requirement 13: Commands, Status, Refusals, and Notifications

**User Story:** As a player, I want pending applications and active denials shown
separately with exact refusal reasons, so that I can understand what is happening
without inferring state from prose.

#### Acceptance Criteria

1. THE command layer SHALL provide commands to request a building PLANT with an
   explicit origin, carrier, and target; request a Jam_Application with an
   explicit origin, carrier, and target; request purge with an explicit actor;
   and inspect Signals status.
2. THE Signals status command SHALL list tracked non-terminal PLANT and
   Jam_Application Operation_Records separately from committed active
   Building_Suspension and Agent_Jam records. It SHALL never present a
   Resolution_Transaction, staged payload, or partial commit as an active denial;
   any recovery diagnostic SHALL label it non-authoritative.
3. FOR each Pending application, THE status command SHALL report mode,
   Operation_State, source, concrete target, Plant_Origin, application ticks
   remaining, and reservation identity.
4. FOR each committed active effect, THE status command SHALL report mode,
   `effect_id`, source, target, duration and remaining ticks, ending-relevant
   source status, and purge status when applicable.
5. WHEN a player inspects a building, THE status view SHALL expose the exact
   value from `BranchSystem.operational_status`; WHEN the reason is
   `intrusion_suspended`, it SHALL also report the active effect and remaining
   ticks.
6. WHEN a player inspects an agent, THE status view SHALL expose the exact
   `AgentSystem.behavior_status` reason and SHALL distinguish Jam from reserve,
   incapacitation, and death.
7. THE Intrusion_System SHALL return a structured outcome for every request and
   transition and SHALL raise no exception into the command layer.
8. THE Intrusion_System SHALL compose no player-facing prose; it SHALL publish
   message or notification keys plus structured data.
9. THE command layer SHALL receive an injected generic
   `NotificationPresenter.render_vector_refusal(key, data)` callable and SHALL
   use it for every Signals refusal.
10. IF `render_vector_refusal` receives an unknown key, THEN THE presenter SHALL
    return a deterministic, non-empty fallback containing enough structured
    context to diagnose the refusal rather than returning blank text or raising.
11. THE NotificationPresenter SHALL provide formatter coverage for every
    request refusal, Pending warning, cancellation, acceptance or recovery
    diagnostic, active-effect start, purge start/abandon/complete, source-invalid
    ending, natural ending, target loss, and status diagnostic introduced by
    this specification.
12. THE Intrusion_System SHALL key active-effect start and end notifications by
    (`effect_id`, event kind), Pending warnings and cancellations by stable
    `op_id`-derived event keys, and purge events by `effect_id`, actor or attempt,
    and event kind as applicable. Restart rebuilding and duplicate lifecycle
    events SHALL reuse the identical immutable key and payload.
13. EVERY Signals notification SHALL be persisted in the Post_Commit_Outbox with
    immutable recipients, structured payload, snapshotted tick and amount when
    applicable, phase, and receipt before external delivery. Delivery SHALL use
    `publish_once(event_id, kind, payload, recipients)`; an original `applied` or
    same-payload `duplicate(prior=applied)` receipt SHALL settle it,
    `duplicate(prior=rejected)` SHALL authorize no delivery, `conflict` SHALL fail
    closed, and `rejected` or `indeterminate` SHALL remain a structured or
    recoverable outcome without fabricating publication. Every such entry SHALL
    occupy a slot already reserved under Requirement 1; optional event-only work
    with no domain mutation MAY be suppressed before enqueue when no slot is
    available, and that receiptless capacity refusal SHALL be logged and
    status-visible rather than reported as a delivered or duplicate event.

### Requirement 14: Balance Configuration and Validation

**User Story:** As a game designer, I want every Signals value bounded and
validated together, so that configuration cannot create permanent denial or a
mode that bypasses late-game cost.

#### Acceptance Criteria

1. FOR every Signals Balance_Config value specified as a tick count, radius,
   cap, count, XP amount, or other integer quantity, THE SchemaValidator SHALL
   accept only an exact non-Boolean integer and SHALL reject Boolean values,
   integral floats, strings, and other coercible representations. Every numeric
   value that is permitted to be fractional SHALL be a finite, non-Boolean real
   number; `NaN`, positive or negative infinity, strings, and coercion SHALL be
   invalid. Type validation SHALL occur before range or relationship validation.
2. THE Balance_Config SHALL define `intrusion_plant_ticks` in the inclusive
   range [`minimum_response_window_ticks`, 3600].
3. THE Balance_Config SHALL define `intrusion_duration_ticks` in [2, 86400]
   and `intrusion_max_duration_ticks` in
   [`intrusion_duration_ticks`, 86400].
4. THE Balance_Config SHALL define `intrusion_purge_ticks` in [1, 86399], with
   `intrusion_purge_ticks < intrusion_duration_ticks`.
5. THE Balance_Config SHALL define `suspension_debt_cap_ticks` and
   `suspension_debt_window_ticks` as either both zero or both in [1, 604800].
6. WHEN Suspension_Debt is enabled, THE SchemaValidator SHALL require
   `intrusion_max_duration_ticks <= suspension_debt_cap_ticks`.
7. THE Balance_Config SHALL define `firewall_radius` in [1, 50] and
   `firewall_plant_penalty_ticks` in [0, 3600].
8. THE Balance_Config SHALL define `jam_radius` in [1, 50],
   `jam_application_ticks` in [`minimum_response_window_ticks`, 3600], and
   `jam_duration_ticks` in [1, 86400].
9. THE Balance_Config SHALL define `jam_cost` as a non-empty mapping keyed only
   by exact canonical known-resource identifiers from the resource registry;
   aliases, case-normalized substitutes, unknown keys, and key coercion SHALL be
   rejected. Every value SHALL be an exact positive non-Boolean integer, and the
   mapping SHALL contain at least one canonical key from `Circuits`, `Energy`,
   or `Nexium`.
10. THE SchemaValidator SHALL validate the shipped `intrusion_cost` by the same
    non-empty mapping, canonical-key, exact-positive-non-Boolean-integer, and
    at-least-one-late-game-resource rules as `jam_cost`.
11. THE SchemaValidator SHALL validate the shipped
    `intrusion_cooldown_ticks` in [1, 86400],
    `intrusion_max_in_flight` in [1, 100], and
    `agent_xp_intrusion` in [0, 1000000].
12. THE SchemaValidator SHALL traverse every Signals field, every cost key and
    value, and every independently evaluable cross-field relationship and SHALL
    return one deterministic collected error result before load fails. A type
    error in one operand SHALL neither throw nor suppress errors in unrelated
    fields or entries; a relationship error SHALL be added whenever its operands
    are valid enough to evaluate. Validation SHALL NOT fail fast or coerce an
    invalid value into validity.
13. WHEN either mode is accepted, THE Intrusion_System SHALL snapshot its
    application duration, base active duration, applicable maximum duration,
    Firewall decision and penalty, debt-policy values needed by that accepted
    request, canonical mode-aware cost, and `agent_xp_intrusion` amount; a
    Balance_Config reload SHALL affect only later requests.
14. WHEN a purge request is accepted, THE Intrusion_System SHALL snapshot its
    purge duration into that attempt; a reload SHALL not change an in-progress
    attempt but MAY affect a later attempt after abandonment.
15. THE current Counter_Web relationship used at building-effect creation SHALL
    be the explicit exception to request-time numeric snapshots, because
    Requirement 8 requires the target owner's current Branch at that transition.
    Each prepare attempt SHALL call `counter_multiplier_checked` once and SHALL
    accept only explicit `neutral(1.0)` or `advantage(multiplier)`, with a finite
    non-Boolean real multiplier for `advantage`; it SHALL apply that affirmative
    value once through the unchanged `min(maximum, ceil(base * multiplier))`
    formula and persist the resulting `duration_ticks`. Transient `unavailable`
    SHALL retain the due work for retry, confirmed `invalid` SHALL settle without
    an effect through Requirement 8, and no failure SHALL be coerced to `1.0`.
16. THE existing Branch investment-score parity validation SHALL continue to
    include the Signals content and SHALL not be bypassed by the new Jam cost.
17. THE shared global Balance_Config SHALL define the exact field
    `vector_outbox_capacity`, and THE SchemaValidator SHALL accept it only as an
    exact non-Boolean integer in the inclusive range `[1, 1_000_000]`. It SHALL be
    one global process/shared-store capacity for the whole Post_Commit_Outbox
    across every vector, SHALL NOT be partitioned per Branch, mode, operation, or
    producer, and SHALL NOT be snapshotted into an accepted request. It SHALL be
    validated at startup before any vector workflow, including the
    Intrusion_System, may reserve a slot or mutate domain state, and its errors
    SHALL appear in the same collected result as every other Signals field error.
18. AT startup and at hot reload, THE validator SHALL obtain a confirmed durable
    current-use value equal to global live unsettled entries plus unconsumed
    reserved slots and SHALL reject the candidate configuration when the proposed
    value is out of range, Boolean, not an exact integer, below that current use,
    or when current use is unreadable or indeterminate. A rejected startup value
    SHALL reject mutation readiness, and a rejected hot reload SHALL retain the
    prior capacity. Neither path SHALL evict, overwrite, prematurely settle, or
    rewrite an existing entry, reservation, receipt, or tombstone to make the
    value fit; capacity SHALL become reusable only through ordinary settlement,
    `release_once`, and the finite retention pruning in Requirement 2. An accepted
    increase or decrease no lower than current use SHALL govern only later
    reservation decisions.

### Requirement 15: Correctness and Bounded-Work Properties

**User Story:** As a developer, I want the dangerous lifecycle claims expressed
as invariant properties, so that implementation and review can distinguish
terminal operations from active denial.

#### Acceptance Criteria

1. FOR ALL observable active effects, a durable terminal receipt SHALL establish
   that the source PLANT or Jam_Application resolved, became untracked, and had
   its terminal Operation_Record removed; the reservation SHALL be owned by
   `effect_id`; the separately persisted Active_Denial_Record, retained
   Resolution_Transaction receipt, and active-index entry SHALL agree; and no
   active effect SHALL require a non-terminal or retained source Operation_Record.
2. FOR ALL four terminal Operation_States, a late tick, event, rebuild, or
   duplicate command SHALL NOT move or recreate the terminal Operation_Record or
   create a second transaction or effect. Idempotently advancing the one retained
   Resolution_Transaction for a confirmed `resolved` receipt is recovery of that
   original transition, not recreation; a compact tombstone SHALL prevent
   recreation after retention pruning.
3. FOR ALL Building_Suspension endings, removal SHALL delete only the intrusion
   conjunct, and the building's ownership, level, hit points, shield, stored
   contents, assignments, construction or upgrade progress, and other
   operational conjuncts SHALL equal the values produced by non-Intrusion causes.
4. FOR ALL buildings that remain offline, under construction, without an active
   HQ, or Branch-dormant when suspension ends, `operational_status` SHALL remain
   that non-operational reason rather than becoming `operational`.
5. FOR ALL Agent_Jam endings, removal SHALL delete only the Jam conjunct and
   SHALL preserve role, assignment, reserve, incapacitation, movement, carried
   resources, experience, hit points, and script objects except for unrelated
   changes made by other systems.
6. FOR ALL targets and all interleavings of acceptance journaling, reservation,
   charge, Pending confirmation, staging, terminal transition, effect commit,
   ending, restart, and lifecycle events, there SHALL be at most one winning
   claim spanning Acceptance_Transaction, Pending operation,
   Resolution_Transaction, and committed effect in the building lane and at most
   one in the agent lane.
7. FOR ALL duplicate persisted claims, rebuild SHALL choose the same accepted
   operation, confirmed transaction, or committed-effect winner under
   Requirement 3 regardless of discovery order and SHALL converge reservations,
   compensated loser receipts, active records, and indexes to that winner without
   exposing or deleting recovery evidence for a loser prematurely.
8. FOR ALL debt ledgers, validation SHALL equal the exact interval-overlap sum in
   Requirement 9, and an accepted proposed Building_Suspension SHALL satisfy
   `current_debt + proposed_effective_duration <= cap` when debt is enabled. A
   proposed duration SHALL use exactly one affirmative checked multiplier and
   never a lookup-failure fallback.
9. FOR ALL early purge or source-loss endings, the keyed debt-closure mutation
   SHALL record only actual `[started_tick, ending_tick)` elapsed time and SHALL
   never record the unused scheduled remainder; replay SHALL return its prior
   receipt rather than append another interval.
10. FOR ALL Purge_Attempts, duplicate processing of one tick and server downtime
    SHALL add no progress; loss or unreadability of owner, controller, alliance,
    `support` consent, actor life, presence, or exact tile SHALL atomically reset
    the attempt without progress; and a valid completion tied with natural
    expiry SHALL produce one keyed `purged` ending.
11. FOR ALL Jammed agents, the shared behavior gate SHALL prevent both
    AgentSystem progression and ordinary behavior-script dispatch without
    changing script attachment, and every carried non-Intrusion operation SHALL
    observe the same Jam unavailability.
12. FOR ALL valid version-1 OperationRecord metadata, serializing and rebuilding
    SHALL deeply preserve every shipped field and all nested `vector_data` by
    value without shared mutable identity, including mode, exact Plant_Origin,
    required ticks, Firewall snapshot, reservation identity, debt-policy
    snapshots, and snapshotted XP award.
13. FOR ALL OperationRecord constructions and payloads, `OperationRecord()` SHALL
    default to version `1`, while `from_dict({})`, an absent version, or a value
    that is not an exact non-Boolean integer SHALL yield legacy version `0`; a
    present exact non-Boolean integer SHALL be preserved verbatim. Only `0` and
    `1` SHALL be interpreted. Every other integer, including negative and future
    values, SHALL be quarantined without partial rewrite. Absent or malformed
    `vector_data` SHALL produce a fresh unshared empty mapping per read, invalid
    required version-1 metadata SHALL be isolated, and the next successful
    serialization of version `0` SHALL emit version `1` without mutating input.
    Persistence-layer unreadability SHALL yield `indeterminate`, not a legacy
    version, empty mapping, or authoritative absence.
14. FOR ALL valid Active_Denial_Records, serializing and rebuilding SHALL
    preserve every field and purge subfield in Requirement 2 and SHALL restore
    the same remaining logical ticks without crediting server downtime.
15. FOR ALL genuinely refused requests, resources, cooldown, escalation, XP,
    debt, reservations, Operation_Records, Resolution_Transactions,
    Active_Denial_Records, active indexes, and target status SHALL remain
    unchanged except for a no-domain-effect refusal journal and, after the
    retention horizon, its compact tombstone.
    FOR definitively failed pre-terminal activations after committed acceptance,
    the accepted keyed charge and cooldown SHALL retain their specified outcome,
    but no XP, debt interval, active denial, active-index entry, or target-status
    change SHALL occur; stage, Pending settlement, and reservation SHALL converge
    under Requirement 3. An indeterminate acceptance SHALL not be classified as
    refused or reusable. Escalation SHALL be eligible only after the driver's
    confirmed terminal `resolved` receipt.
16. FOR ALL same-tick source-death and activation interleavings, no persistent or
    visible Building_Suspension or Agent_Jam from the dead source SHALL survive
    the tick, no activation XP or start SHALL be enqueued, and retained
    transaction/ending receipts SHALL make that result replay-safe.
17. FOR ALL feature-absence states, persisted acceptance, resolution, denial,
    ending, and outbox records SHALL remain available for a later rebuild while
    BranchSystem and AgentSystem behave as though the Intrusion conjuncts are
    absent.
18. FOR ALL ordinary ticks after the one startup rebuild, work SHALL be
    proportional to indexed acceptance and Pending operations, resolution
    transactions requiring reconciliation, committed active effects, active
    purge attempts, ending journals, and eligible outbox entries, with at most
    one clock write per target and no full-owner, full-world, full-table, or
    full-database scan. Each Firewall lookup SHALL be proportional only to its
    bounded spatial area plus returned candidates.
19. FOR ALL durably resolved Signals source operations, the inherited
    `OperationDriver._resolve` path SHALL enqueue exactly one immutable
    `resolve:{op_id}:escalation` intent into its already-reserved terminal slot
    after terminal confirmation, and delivery SHALL use
    `BranchSystem.note_escalation_once` with the immutable `target_owner_ref`
    snapshotted at admission as `target`; replay SHALL externally apply
    at most one mutation and a different payload SHALL conflict and fail closed.
    No Intrusion_System hook, retry, rebuild, compensation, or reconciliation
    path SHALL call escalation directly, and the legacy unkeyed API SHALL carry
    no exactly-once guarantee.
20. FOR ALL effects that complete authoritative reservation/effect/index commit
    and survive same-tick source-invalid reconciliation, the immutable start
    event and snapshotted XP mutation SHALL be durably enqueued afterward, keyed
    by (`effect_id`, `start`) and `resolve:{op_id}:xp`, delivered by
    `publish_once` and `AgentSystem.award_operation_xp_once`, and externally
    applied at most once. No staging or failed terminal, transfer, effect, or
    index commit SHALL enqueue activation XP or start; retries and restart SHALL
    use prior receipts rather than reread live amounts.
21. FOR ALL active-effect endings, one immutable ending kind and tick SHALL key
    active/index removal, reservation release, debt closure, purge effects, and
    ending outbox work, and every entry SHALL consume an already-reserved slot.
    Each domain change and receipt SHALL be atomic, every replay SHALL return
    `duplicate(prior=<original_outcome>)` for that key, and an indeterminate step
    SHALL retain the journal and conservative claim without early publication.
22. FOR ALL identical tick-start persisted states, same-tick acceptance,
    Pending, resolution-transaction, committed-active, purge, ending, XP, and
    outbox outcomes SHALL be identical regardless of record discovery, tracked-
    list, spatial-candidate, outbox, or database iteration order.
23. FOR ALL resolution interleavings, a staged proposal SHALL never be
    authoritative, a due Pending operation SHALL remain tracked at zero on retry
    or missing terminal confirmation, and a visible effect SHALL never precede a
    confirmed terminal receipt. A definite failure SHALL converge through a
    retained compensated transaction to no active denial; an indeterminate
    result SHALL preserve the hidden transaction and claim until readback can
    finish or compensate it. Removing the confirmed terminal source record SHALL
    leave that transaction or its tombstone as recovery authority.
24. FOR ALL Signals Balance_Config payloads, Boolean, integral-float, string, or
    other coercible representations SHALL never satisfy an exact-integer field,
    and non-finite or Boolean values SHALL never satisfy a fractional numeric
    field. Every accepted cost key SHALL be an exact canonical resource
    identifier, every accepted cost value SHALL be an exact positive non-Boolean
    integer, and the collected validation result SHALL contain every
    independently discoverable field, entry, and relationship error.
25. FOR EVERY crash or retry point before or after reservation, keyed charge,
    Operation_Record construction, Pending confirmation, keyed cooldown,
    Pending-warning enqueue, or acceptance acknowledgement, same-key,
    same-payload replay through the one preallocated Acceptance_Transaction SHALL
    make recovery converge to either one committed acceptance or one confirmed
    compensation. It SHALL never double charge, double refund, double cooldown,
    duplicate warning, reuse an ambiguous claim, or infer absence from
    unreadability. A same-key, different-payload `conflict` SHALL instead remain
    fail-closed and non-reusable until explicit repair establishes every
    authoritative fact; it SHALL NOT be forced through ordinary refusal or
    unconditional compensation.
26. FOR EVERY prepare and terminal-write outcome, `_resolve` SHALL preserve the
    exact `Resolution_Prepare_Result` and `Persistence_Result`; `retry`,
    `indeterminate`, and rejected or unconfirmed terminal state/receipt units
    SHALL keep the authoritative source operation non-terminal and tracked at
    zero and reuse at most one transaction. A `resolved` state, its immutable
    terminal receipt, and `resolved_confirmed` phase SHALL become authoritative
    only as one atomic confirmed persistence unit. Neither `_run_hook`, an
    exception-swallowing adapter, nor a non-raising writer SHALL manufacture
    confirmation.
27. FOR EVERY confirmed source Operation_Record removal, the retained
    Resolution_Transaction SHALL contain the terminal, reservation-transfer,
    effect/index, escalation, XP, notification, and compensation receipts needed
    to finish or compensate without that record. Only a `complete` or
    `compensated` transaction with agreeing facts and receipts may age into a
    compact tombstone after the replay-safe retention horizon.
28. FOR EVERY accepted hostile application, the inherited response floor SHALL
    become authoritative exactly once with the durable Pending-warning marker
    before tick eligibility. Warning replay or a generic suspend/resume SHALL
    preserve the exact remaining countdown and SHALL never refloor it; Signals
    application-invalid carrier, nonphysical-origin, or commitment conditions
    SHALL cancel rather than resume.
29. FOR EVERY Counter_Web preview or resolution attempt, exactly one
    `counter_multiplier_checked` result SHALL be consumed. `neutral(1.0)` and a
    valid `advantage(multiplier)` SHALL be the only arithmetic inputs; transient
    `unavailable` SHALL retain due work hidden for retry, confirmed `invalid`
    SHALL produce a structured no-effect settlement and compensation, and no
    failure SHALL become `1.0`.
30. FOR EVERY purge start, progress, and completion candidate, target owner,
    actor controller, alliance, and current `support` consent SHALL be freshly
    resolved immediately before mutation in the same target/effect serialization
    boundary. Revocation, ownership change, or unreadability racing that boundary
    SHALL atomically abandon/reset with no progress or completion.
31. FOR EVERY persisted transition, only `Persistence_Result.confirmed` from a
    durable atomic acknowledgement or positive readback SHALL authorize state,
    and confirming reads SHALL distinguish absence from unreadability. FOR EVERY
    keyed mutation, the domain change and immutable payload-hash/outcome receipt
    SHALL be atomic; the same key and payload SHALL return
    `duplicate(prior=<original_outcome>)` carrying exactly that original `applied`
    or `rejected` decision, a different payload SHALL return `conflict` and fail
    closed, and `indeterminate` SHALL retain recovery authority. A terminal domain
    no-op SHALL hold an original `rejected` receipt with an immutable domain
    reason, while a receiptless capacity refusal SHALL remain a retriable
    `rejected` that is never reported as `duplicate(prior=rejected)`.
32. FOR ALL effect commits, including those retried, rebuilt, or delayed across
    ticks or a restart, exactly one `effect_commit_tick`, one stable `effect_id`,
    one Final_Effect_Payload, and one Final_Payload_Hash SHALL be confirmed before
    any reservation-transfer, active-record, index, or debt mutation. The persisted
    Active_Denial_Record SHALL be byte-equal by value to that confirmed payload,
    SHALL match its Final_Payload_Hash, SHALL have `started_tick` equal to that
    `effect_commit_tick`, and SHALL have `duration_ticks` and `remaining_ticks`
    equal to the staged template's final proposed duration. No later attempt SHALL
    read the current `now`, recompute a duration, or recompute either hash; the
    immutable Template_Hash SHALL cover only template fields, SHALL never be
    rewritten with a commit-time value, and SHALL always differ from the
    Final_Payload_Hash. A rejected or indeterminate finalization SHALL leave no
    reservation transfer, active record, index entry, debt interval, start, or XP.
33. FOR ALL escalation deliveries, including those replayed after retry, rebuild,
    archival, target transfer, or target deletion, the immutable
    `resolve:{op_id}:escalation` payload and payload hash SHALL carry the canonical
    `target_owner_ref` snapshotted at admission, and `note_escalation_once` SHALL
    receive exactly that owner. No delivery path SHALL re-resolve an owner from a
    live, mutated, transferred, or deleted target, and a request whose canonical
    owner was unresolvable at admission SHALL have failed closed before any
    mutation, so no accepted operation, terminal receipt, or outbox entry exists
    without one immutable owner snapshot. That snapshot SHALL never authorize a
    live ownership-sensitive decision.
34. FOR ALL Post_Commit_Outbox states and at every atomic reserve, append,
    release, settle, and prune boundary, global live unsettled entries plus global
    unconsumed reserved slots SHALL be at most `vector_outbox_capacity`;
    `append_reserved` SHALL convert exactly one held slot into one live entry
    without changing that sum; `release_once` SHALL free only confirmed unconsumed
    slots and never remove a live entry; and no live, reserved, indeterminate, or
    conflict-quarantined work SHALL be evicted, overwritten, or silently settled by
    any reservation, activation, or hot reload. Partitioning by vector, mode,
    operation, or producer SHALL NOT multiply or bypass that one global bound, and
    only settled entries and constant-size tombstones SHALL age out under the
    finite retention horizon.
35. FOR ALL irreversible Signals boundaries, meaning acceptance, resolution
    prepare, effect commit, terminal settlement, purge mutation, and ending, the
    exact bounded reservation for that workflow's already frozen possible entries
    SHALL be confirmed first, and no active effect SHALL exist whose start, XP,
    single ending, debt closure, and reservation-release budget was not already
    confirmed, so expiry, purge, or source loss SHALL never create unreserved
    backlog. A claimless capacity rejection SHALL leave a new request refused
    before any reservation charge or domain mutation and a due operation tracked
    and counting at zero with no effect and no terminal transition, retriable under
    the same reservation ID. Optional event-only work with no domain mutation MAY
    be suppressed with a logged, status-visible refusal while every existing entry
    stays replayable, terminal source removal SHALL wait until every required entry
    is durable and every unconsumed slot is released, and no reservation SHALL be
    taken for a wildcard or unbounded recipient set.
36. FOR ALL ticks, outbox reservation reconciliation, required appends, releases,
    and deliveries SHALL occur inside Requirement 8's deterministic ascending
    phase order in stable immutable-key order, so identical tick-start state
    yields identical capacity use, entries, receipts, and suppressions regardless
    of outbox, tracked-list, spatial-candidate, or database iteration order.
    Per-tick outbox work SHALL be proportional to the eligible indexed entries and
    reservations rather than to total capacity, retained history, or world size.
