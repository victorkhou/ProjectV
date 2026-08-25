# Requirements Document

## Introduction

This feature implements the **Biowarfare** Branch's Signature_Vector:
**Contagion**, a transmissible damage-over-time effect that spreads between
in-world entities at a decaying strength. It is one of six child specs of
`tech-tree-combat-expansion`, and it implements that parent's Requirement 10.

The `bio` Branch, Biolab (`BX`), Culture Vats (`CV`), `medic` role,
`contagion` Operation_Kind, shared Vector_Operation lifecycle, Counter_Web,
consent store, tick phases, and the shipped operation fields
`contagion_cost`, `contagion_cooldown_ticks`, `contagion_max_in_flight`, and
`agent_xp_contagion` already exist. This spec extends those shared contracts; it
does not create parallel versions of them.

### Shipped behavior this spec must correct or extend

- `OperationDriver` is a cooperative mixin. A concrete vector has the shipped
  shape `class ContagionSystem(OperationDriver, BaseSystem)`, with
  `OperationDriver` first in the method-resolution order.
- `OperationDriver.apply_effect` currently writes the legacy four-key active
  effect shape `type`, `damage`, `ticks_remaining`, and `source`. It has no
  metadata parameter and therefore cannot yet preserve a Contagion's durable
  release identity, generation, or origin.
- `CombatEngine.tick_effects_on_entity` currently sends burn and poison damage
  directly to `_apply_damage`. That path drains shields and subtracts hit
  points, and the subsequent zero-HP path shares death routing, but it does not
  apply typed resistance, the chip-damage floor, or rank-gap damage damping.
  This spec therefore requires a shared typed-effect-tick API; those rules are
  not treated as already inherited.
- `EquipmentSystem.use` currently refuses a healing consumable at full HP,
  applies healing before inventory removal, and exposes no domain seam for
  Contagion relief. This spec requires an injected, atomic extension of that
  flow rather than a notification subscriber.
- `AgentSystem` already owns assignment, reserve, incapacitation, Branch-role
  release, and freeze-aware XP paths. `OperationDriver` already owns Pending,
  Suspended, Resolved, Cancelled, Expired, and Discarded transitions. The
  Contagion_System must use those authorities rather than write role or
  operation state itself.

Transmission can multiply stored effects and per-tick work. This spec bounds it
independently by decaying integer damage, a generation limit, a per-entity
release count, a per-entity aggregate raw-damage cap, a start-of-sweep snapshot,
and an event-maintained carrier index.

## Glossary

The parent specification's glossary applies in full. These terms are new or
narrowed here.

- **Contagion_System**: The `contagion` Vector_System, composed from
  `OperationDriver` and `BaseSystem`, that owns releases, transmission, cure,
  effect admission, and the nonpersistent Carrier_Index.
- **Contagion_Effect**: A versioned active-effect mapping whose `type` is
  `poison` and whose `effect_kind` is `contagion`. Ordinary four-key poison
  effects are not Contagion_Effects.
- **Release_ID**: The immutable canonical string copied from the releasing
  Operation_Record's `op_id` into every descendant Contagion_Effect.
- **Raw_Damage**: The immutable `damage` value stored on one Contagion_Effect,
  before the current Counter_Web multiplier, current typed mitigation, chip
  floor, shields, and rank-gap damping are applied for a tick.
- **Contagion_Release**: One delayed hostile `contagion` Vector_Operation that
  has a concrete primary hostile target owner and an affected coordinate.
- **Primary_Target_Owner**: The canonical owning player explicitly named by a
  release request and proven by an observable entity that owner has at the
  requested coordinate. This is the Operation_Record's `target_ref`.
- **Generation**: The number of transmissions from the original release. A
  released effect is generation 0; a transmitted effect is its selected
  carrier effect's generation plus one.
- **Carrier_Index**: The nonpersistent index from canonical `(planet, x, y)` to
  in-world entities carrying at least one valid active Contagion_Effect.
- **Plant_Origin_Tile**: The selected medic's canonical planet and exact
  coordinate at release acceptance, persisted on the Operation_Record and held
  for the Pending lifecycle.
- **Cure**: An explicit player command using one selected eligible `medic` to
  remove all Contagion_Effects from one selected entity. Cure is a Biowarfare
  utility; it is not the Fortification Doctrine_Counter.
- **In_World**: An entity with a concrete world body and canonical planet and
  coordinates. A player in `PLAYING` or `LINKDEAD` with that body is In_World;
  a player removed to `LOBBY`, `SPAWNING`, or equivalent fully offline storage
  is not.
- **Persistence_Result**: The shared durable-write result with exactly
  `confirmed`, `rejected`, or `indeterminate`. `confirmed` requires a durable
  atomic acknowledgement or positive readback; a non-raising best-effort write
  is never confirmation. A readable absence is distinct from unreadable
  storage.
- **Mutation_Result**: The shared keyed-mutation result with exactly `applied`,
  a structured `duplicate(prior=...)`, `conflict`, `rejected`, or
  `indeterminate`. `applied` and `rejected` are immutable original receipt
  outcomes; `duplicate.prior` is exactly that original `applied` or `rejected`
  outcome. The mutation and its immutable payload-hash/outcome receipt commit
  atomically; the same key and payload returns
  `duplicate(prior=<original_outcome>)`, while the same key with a different
  payload returns `conflict` and fails closed. A terminal domain no-op is an
  original `rejected` outcome with an immutable domain reason, not an
  outcome-less duplicate.
- **Post_Commit_Outbox**: The global capacity-bounded durable outbox whose
  immutable entries contain an event or mutation key, immutable payload
  including every snapshotted amount and tick, recipients, phase, and receipt.
  It owns atomic slot reservations and its notification sink is the idempotent
  `publish_once(event_id, kind, payload, recipients)` API.
- **Acceptance_Transaction**: The durable acceptance coordinator keyed by a
  preallocated `op_id`, persisted before the first resource mutation and
  retained until commit or confirmed compensation.
- **Warning_Receipt_Ledger**: The separate bounded durable ledger keyed by
  `(release_id, canonical_owner_id)` that owns each direct-warning immutable
  payload hash and outcome receipt; it is not stored in
  `OperationRecord.vector_data`.
- **Release_Resolution_Transaction**: The durable per-release coordinator that
  freezes the release recipient stable IDs and order, owns keyed admission
  receipts, and remains authoritative through terminal confirmation and
  post-commit settlement.
- **Transmission_Sweep_Transaction**: The one permitted unresolved durable
  transmission coordinator for the Contagion_System, keyed by one stable
  `sweep_id`. It freezes canonically selected candidates before their first
  mutation and owns their admission and infection-notification receipts through
  settlement. No later sweep transaction may be created until it is settled and
  pruned to at most a constant-size replay tombstone.

## Requirements

### Requirement 1: Shared System, Effect Schema, and Damage Path

**User Story:** As a developer, I want Contagion to extend the shipped vector and
combat contracts explicitly, so that persistence and damage rules have one
implementation.

#### Acceptance Criteria

1. THE Contagion_System SHALL be composed as
   `class ContagionSystem(OperationDriver, BaseSystem)`, SHALL declare
   `operation_kind = "contagion"` and `branch = "bio"`, SHALL implement the
   five required hooks `validate_target`, `build_record`, `on_resolve`,
   `persistence_owner`, and `discover_records`, and SHALL override the additive
   runtime hook `carrier_pause_reason(record)`.
2. THE composition root SHALL inject every required collaborator, SHALL register
   the Contagion_System through `BranchSystem.register_vector`, SHALL invoke the
   inherited operation rebuild at startup, and SHALL not require a game-framework
   import at the Contagion_System module's import time.
3. THE shared `OperationDriver.apply_effect` API SHALL be extended additively
   with an optional metadata input, so that every existing caller using only
   `record`, `target`, `effect_type`, `damage`, and `ticks` retains its existing
   call and result contract.
4. WHEN `apply_effect` receives metadata, THE OperationDriver SHALL add a fresh
   copy of that metadata without permitting it to overwrite the base effect's
   `type`, `damage`, `ticks_remaining`, or legacy `source` values.
5. THE Contagion_System SHALL persist each Contagion_Effect by value with
   `schema_version = 1`, `type = "poison"`,
   `effect_kind = "contagion"`, a nonempty immutable `release_id`, a durable
   `source_ref`, a canonical `origin_planet`, an integer `generation`, `damage`
   as the Raw_Damage, `ticks_remaining`, and `applied_tick`.
6. THE Contagion_System SHALL set a released effect's `release_id` to the
   Operation_Record's `op_id`, SHALL set `source_ref` to the releasing player,
   and SHALL retain the same source in the legacy `source` field for consumers
   of the four-key shape.
7. THE active-effect reader, ticker, admission path, cure path, and consumable
   path SHALL use read-copy-write reassignment, SHALL preserve every unrelated
   active effect and every supported metadata field they do not change, and
   SHALL never depend on an in-place container mutation being persisted.
8. THE Contagion_System SHALL treat `release_id`, `source_ref`,
   `origin_planet`, `generation`, Raw_Damage, and `applied_tick` as immutable
   after an effect is admitted; only `ticks_remaining` may decrease.
9. THE Contagion_System SHALL derive transmission, attribution, dormancy, and
   effect reporting from each Contagion_Effect's durable metadata rather than
   from a terminal Operation_Record, so that a Resolved release may be removed
   from operation persistence without orphaning its effects.
10. THE shared active-effect reader SHALL continue to read and tick legacy
    four-key burn and poison entries, and SHALL not infer
    `effect_kind = "contagion"` for an entry that lacks that value.
11. IF one active-effect entry is not a mapping, names an unsupported schema
    version, or has malformed fields required by its effect kind, THEN THE
    active-effect reader SHALL skip and log that entry in isolation and SHALL
    continue processing and preserving every other readable entry on the
    entity.
12. THE CombatEngine SHALL expose one shared typed-effect-tick API accepting
    `target`, `raw_damage`, `damage_type`, and `source`, and every damaging
    active effect, including burn, ordinary poison, and Contagion, SHALL use that
    API.
13. WHEN the typed-effect-tick API receives positive Raw_Damage, THE
    CombatEngine SHALL apply the current typed-resistance axis, the current
    chip-damage floor, current shield absorption, current rank-gap damage
    damping, and the shared zero-HP death route that applies rank-gap XP and loot
    rules.
14. THE typed-effect-tick API SHALL map a burn to `fire` and a poison or
    Contagion_Effect to `poison`, SHALL attribute the tick to the effective
    source selected under criterion 17, and SHALL not execute on-hit typed-effect
    creation or weapon-proc logic, so that an effect tick cannot recursively
    create another DoT.
15. WHEN a Contagion_Effect ticks, THE Contagion effect adapter SHALL resolve
    whether the recipient is truly ownerless before any Branch lookup. A truly
    ownerless recipient SHALL use explicit `neutral(1.0)`; otherwise the adapter
    SHALL read the owner's current Branch and obtain at most one current
    `BranchSystem.counter_multiplier_checked("bio", target_branch)` result.
    The checked API SHALL return exactly one of `neutral(1.0)`,
    `advantage(multiplier)`, `unavailable(reason)`, or `invalid(reason)`. Only
    `neutral(1.0)` and a finite non-Boolean `advantage(multiplier)` SHALL
    authorize `floor(Raw_Damage * multiplier)` and one typed-effect-tick call.
    `unavailable(reason)`, `invalid(reason)`, an unreadable owner, or an
    unreadable Branch SHALL authorize no offensive damage that tick, SHALL be
    logged in isolation, and SHALL still follow the normal stored-effect clock.
    A malformed checked response SHALL be classified as `invalid(reason)` and
    SHALL never become neutral. This feature SHALL not call the legacy float
    `counter_multiplier` API.
16. THE Contagion_System SHALL never subtract hit points, drain shields, route a
    death, award kill XP or loot, or clear respawn effects directly.
17. THE active-effect ticker SHALL resolve attribution from a readable
    `source_ref` first and SHALL use legacy `source` only as a backward-compatible
    fallback for a readable legacy effect that lacks `source_ref`; when both are
    present and differ, `source_ref` SHALL win. A version-1 Contagion_Effect with
    an absent or malformed required `source_ref` SHALL instead follow criterion
    11, and source selection SHALL not rewrite either immutable field.
18. THE shared `OperationRecord` persistence contract SHALL be extended
    additively with top-level `schema_version` and `vector_data` fields while
    preserving every shipped top-level field and existing caller contract.
    `OperationRecord()` SHALL default to version `1`, and every newly
    constructed or written record SHALL write exactly `schema_version = 1`.
    `OperationRecord.from_dict({})` SHALL produce legacy version `0`; an absent,
    malformed, non-exact-integer, or Boolean schema version SHALL decode as
    `0`, while every present exact non-Boolean integer SHALL be preserved.
    Explicit versions other than `0` or `1`, including negative and future
    versions, SHALL be quarantined and reported and SHALL never be interpreted
    as version `0`, migrated, or rewritten. An absent or malformed/non-mapping
    `vector_data` member in a successfully read payload SHALL decode to a fresh,
    unshared `{}` for that record, but this fallback SHALL not bypass required
    version-1 metadata validation or isolation. Unreadable record storage or an
    unreadable containing payload SHALL be `indeterminate` and SHALL never be
    converted to the empty-mapping fallback.
19. THE `OperationRecord` writer, reader, discovery path, and rebuild path SHALL
    recursively copy and round-trip `vector_data` by value, including nested
    mappings and sequences, so that neither the decoded record, its persisted
    representation, nor a caller-owned input aliases another.
20. FOR every accepted Contagion_Release, `OperationRecord.vector_data` SHALL
    persist by value the `release_id` equal to top-level `op_id`, primary target
    identity equal to top-level `target_ref`, canonical release planet and
    integer `x` and `y`, affected radius, Raw_Damage, effect duration, release
    delay, snapshotted minimum response floor, selected medic identity equal to
    top-level `carrier_ref`, Plant_Origin planet and integer `x` and `y`, the
    snapshotted release-XP amount, and every Contagion lifecycle value not
    represented by a shipped top-level field. The initial Warning_Area marker,
    publication tick, direct-warning receipts, and warned-owner identities SHALL
    instead live in the Acceptance_Transaction, Post_Commit_Outbox, and bounded
    Warning_Receipt_Ledger and SHALL NOT be embedded in `vector_data`.
    Discovery or rebuild SHALL log, isolate, and discard a legacy record whose
    data cannot safely establish the required release values and SHALL not
    invent a coordinate, identity, timing, warning, or lifecycle value.
21. THE OperationDriver SHALL expose the additive, vector-specific, read-only
    runtime hook `carrier_pause_reason(record)`, whose default returns no pause
    reason for existing vectors, and `_suspend_reason(record)` SHALL consult that
    hook after inherited fatal conditions have been evaluated. The hook result
    SHALL feed only the single inherited suspend/resume transition and SHALL not
    authorize a vector-local lifecycle write.
22. THE BranchSystem SHALL expose the exact public, read-only API
    `BranchSystem.is_vector_shielded(target_owner)`, which SHALL report only the
    target owner's current new-player vector shield and SHALL perform no
    `may_target`, escalation, or mutation. For a prospective hostile recipient,
    an absent or unreadable owner or a failed shield read SHALL fail closed as
    shielded.
23. EVERY persistence seam introduced or strengthened by this spec SHALL return
    `Persistence_Result` with exactly `confirmed`, `rejected`, or
    `indeterminate`. `confirmed` SHALL require durable atomic acknowledgement or
    positive readback and SHALL never be inferred from a call merely not
    raising. Readable confirmed absence SHALL be represented separately from
    unreadability; timeout, unreadability, or ambiguous acknowledgement SHALL be
    `indeterminate`, not authoritative absence.
24. EVERY keyed mutation seam introduced by this spec SHALL return
    `Mutation_Result` with exactly `applied`, structured
    `duplicate(prior=applied|rejected)`, `conflict`, `rejected`, or
    `indeterminate`. An original domain mutation and its immutable mutation-key,
    payload-hash, original `applied` or `rejected` outcome, and domain-reason
    receipt SHALL be one atomic decision; a terminal domain no-op SHALL retain
    an original `rejected` receipt and immutable reason. Repeating the same key
    and payload SHALL return `duplicate(prior=<original_outcome>)` without
    reapplying. `duplicate(prior=applied)` SHALL retain the authority of the
    original applied receipt, while `duplicate(prior=rejected)` SHALL retain the
    original no-application decision. Repeating a key with a different payload
    SHALL return `conflict` and fail closed; an ambiguous mutation SHALL retain
    its authority for positive readback and SHALL not be retried under a new key.
25. THE BranchSystem SHALL add
    `charge_once(player, cost, mutation_id)`,
    `refund_once(player, cost, mutation_id, charge_mutation_id)`,
    `note_cooldown_once(building, kind, ready_at, mutation_id)`, and
    `note_escalation_once(actor, target, resolved_tick, mutation_id)`; THE
    AgentSystem SHALL add
    `award_operation_xp_once(agent, kind, amount, mutation_id)`. Existing
    unkeyed charge, refund, cooldown, escalation, and operation-XP APIs SHALL be
    legacy-only and SHALL not be used by this feature. A release SHALL use
    `accept:{op_id}:charge`, `accept:{op_id}:refund`,
    `accept:{op_id}:cooldown`, `resolve:{op_id}:escalation`, and
    `resolve:{op_id}:xp` exactly.
26. THE shared Post_Commit_Outbox SHALL durably store each immutable event ID,
    kind, payload, recipients, phase, and receipt, including every snapshotted
    amount and tick where applicable. Notification delivery SHALL use only the
    idempotent `publish_once(event_id, kind, payload, recipients)` sink. Event
    append and delivery retries SHALL reuse the original key and payload;
    `duplicate(prior=applied)` SHALL ensure or replay the one already-created
    outbox fact and SHALL never create a second event, while
    `duplicate(prior=rejected)` SHALL authorize no event. An unsettled outbox
    entry SHALL survive source-record removal until its receipt is terminally
    settled.
27. THE OperationDriver SHALL make resolution preparation, its structured
    outcome, and the driver-owned confirming terminal writer explicit.
    `on_resolve` SHALL prepare or idempotently advance vector effects without
    writing terminal operation state; only the confirming writer SHALL persist
    a terminal transition and receipt. A due operation SHALL remain tracked at
    zero when preparation fails, when a required candidate mutation is
    `indeterminate`, when persistence is `rejected` before a terminal candidate
    receipt exists, when required outbox capacity is unavailable, or when
    terminal persistence is `rejected` or `indeterminate`; it SHALL neither
    advance below zero nor be treated as absent. A durably receipted terminal
    domain no-op MAY be terminal for only that candidate under Requirement 2.22.
    Terminal untracking and source-record removal SHALL occur only after
    confirmed terminal persistence, every receipt-authorized required outbox
    entry is durably appended from its reservation, and every unneeded reserved
    slot is durably released. A vector transaction that remains authoritative
    after that removal SHALL continue independently until its post-commit work
    settles.
28. THE OperationDriver SHALL expose an `origin_fatal_reason(record)` decision
    independent of Branch commitment. It SHALL never map `branch_dormant` or a
    commitment lapse to a fatal reason. Physical carrier death and a
    destroyed/deleted origin or source-base loss SHALL remain fatal and SHALL be
    evaluated before suspension; Contagion carrier unavailability and
    commitment lapse SHALL remain suspension reasons.
29. THE BranchSystem SHALL expose
    `counter_multiplier_checked(actor_branch, target_branch)`, returning exactly
    one of four result variants: `neutral(1.0)`, `advantage(multiplier)`,
    `unavailable(reason)`, or `invalid(reason)`. Only the first two outcomes
    SHALL authorize arithmetic; the checked API SHALL preserve temporary
    unavailability and confirmed invalidity as distinct fail-closed outcomes
    rather than converting either to a legacy float.
30. THE shared Post_Commit_Outbox SHALL expose exactly
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
    SHALL ensure the same claim, entry, or release receipt and SHALL not create a
    second one, while an original rejected operation SHALL create none. A
    changed slot count, event ID, kind, payload, or recipients under the same
    method key SHALL return `conflict` and fail closed.
31. AT every atomic Post_Commit_Outbox boundary, the number of live unsettled
    entries plus all unconsumed reserved slots SHALL be at most the current
    global `vector_outbox_capacity`. A successful `append_reserved` SHALL
    convert one reserved slot into one live entry without increasing that sum.
    THE system SHALL never evict, overwrite, or silently settle existing work to
    admit a reservation or configuration change. Startup and hot reload SHALL
    reject a capacity below current use. Settled entries, closed reservations,
    and constant-size replay tombstones SHALL be pruned under a declared finite
    retention horizon; unresolved entries, reservations, and ambiguity SHALL not
    be pruned.
32. BEFORE an irreversible acceptance, resolution/admission, Cure, consumable,
    or other event-authorizing mutation, THE owning workflow SHALL durably
    reserve the exact finite number of possible outbox entries derived from its
    already frozen bounded work. It SHALL reserve neither a wildcard nor an
    unbounded or not-yet-discovered recipient set, and one slot SHALL authorize
    at most one immutable event entry. A new command request whose exact
    reservation is definitively rejected SHALL refuse before charge, cooldown,
    inventory, HP, effect, receipt-ledger, or operation mutation. An existing
    due release whose reservation is definitively rejected SHALL remain tracked
    and counting at zero with no candidate effect, terminal transition, or
    required event append. An `indeterminate` reservation SHALL conservatively
    retain its possible claim, SHALL be reconciled under the same reservation
    ID, and SHALL authorize neither mutation nor a replacement reservation.
    Once no further event can be authorized, the workflow SHALL append every
    required entry and call `release_once` for all unconsumed slots before its
    terminal source-removal gate.

### Requirement 2: Hostile Coordinate Release and Public Warning

**User Story:** As a Biowarfare player, I want to contaminate an observed hostile
position after a public warning, so that the release is threatening without
bypassing targeting protections.

#### Acceptance Criteria

1. WHERE a player holds the `bio` Branch_Commitment on a planet, owns an
   Operational Culture Vats there, and selects an eligible medic, WHEN that
   player requests a Contagion_Release against a coordinate and a
   Primary_Target_Owner, THE Contagion_System SHALL submit the request through
   the inherited ordered validation chain as a hostile operation and SHALL use
   the durable Acceptance_Transaction in criteria 17 through 19 for every
   charge, Pending entry, cooldown, warning publication, acknowledgment, and
   compensation.
2. THE Contagion_System SHALL accept only a coordinate whose planet is an
   existing canonical planet, whose `x` and `y` are finite integers other than
   booleans, and whose values are within that planet's current bounds, and SHALL
   validate it without creating or loading a room as a side effect.
3. THE Contagion_System SHALL require the coordinate to be on the originating
   Culture Vats' planet and within the current `contagion_release_radius` of the
   Culture Vats, and a radius refusal SHALL report the allowed radius and
   measured distance.
4. THE Contagion_System SHALL require the request to name one concrete canonical
   Primary_Target_Owner for whom the requester can currently observe at least
   one owned in-world entity at the exact coordinate, and SHALL refuse a stale,
   absent, own, allied, or noncanonical primary owner.
5. THE Contagion_System SHALL set `OperationRecord.target_ref` to the
   Primary_Target_Owner and SHALL call the shared `may_target` authority exactly
   once for that owner during pure request validation. After confirmed terminal
   resolution, inherited resolution SHALL enqueue exactly one
   `note_escalation_once` mutation for that same owner with the snapshotted
   resolved tick and mutation ID `resolve:{op_id}:escalation`; neither
   collateral nor any preparation, retry, rebuild, or reconciliation path SHALL
   create another escalation.
6. THE Contagion_System SHALL persist the accepted release snapshot in
   `OperationRecord.vector_data` under Requirement 1.20 and SHALL initialize its
   held Pending clock from the snapshotted `contagion_release_delay_ticks`.
   Before that clock is tick-eligible, the Acceptance_Transaction SHALL durably
   confirm the initial public Warning_Area marker, publication tick, exact
   initial-warning outbox reservation, and immutable warning outbox facts. The
   response-floor interval SHALL begin only at that durably confirmed publication
   tick, and the earliest permitted release SHALL be at least the snapshotted
   `minimum_response_window_ticks` later.
7. BEFORE charge, THE Contagion_System SHALL compute the initial direct-warning
   recipient union from the canonical Primary_Target_Owner and every current
   in-world player or resolvable owner of an agent in the affected area, keyed
   and ordered by canonical owner ID. Multiple bodies and overlap with the
   inherited Primary_Target_Owner path SHALL contribute one owner. IF that union
   exceeds the current `contagion_warning_receipt_cap`, THEN acceptance SHALL
   refuse without charge or operation mutation. Otherwise the frozen union SHALL
   be stored in the Acceptance_Transaction, which SHALL use
   `reserve_once("accept:{op_id}:warnings", union_count)` to reserve exactly its
   bounded number of warning entries after transaction preallocation and before
   charge. A definitive reservation rejection SHALL refuse before charge; an
   `indeterminate` reservation SHALL retain the transaction and possible claim
   without charge or acceptance progress. Committed acceptance SHALL use
   `append_reserved` to create for each owner one
   `(release_id, canonical_owner_id)` Warning_Receipt_Ledger entry and one
   immutable Post_Commit_Outbox event containing the release ID, coordinate,
   affected radius, publication tick, and remaining ticks.
8. WHILE a release is nonterminal and its Warning_Area marker is durably live,
   THE Contagion_System SHALL expose its public warning through a query by
   planet and coordinate, including its affected radius, lifecycle state,
   current remaining ticks, and receipt-ledger saturation status.
9. WHEN an entity enters a warned area after acceptance, THE Contagion_System
   SHALL first read `(release_id, canonical_owner_id)` from the
   Warning_Receipt_Ledger. A known receipt SHALL replay its prior immutable
   outcome and SHALL not construct a changed payload or dispatch twice. For an
   unknown owner below the current `contagion_warning_receipt_cap`, THE system
   SHALL first reserve exactly one slot under the stable late-warning reservation
   ID for that release and owner. Only a confirmed reservation SHALL permit one
   atomic receipt insertion and `append_reserved` outbox fact with the
   then-current remaining ticks. A definitive reservation rejection SHALL
   suppress that optional direct warning without inserting a receipt; an
   `indeterminate` reservation SHALL retain its possible claim and insert
   nothing until reconciliation. At the receipt cap, an unknown owner SHALL
   receive no direct dispatch and no reservation SHALL be created. Every such
   suppression SHALL be logged and status-visible, and the public Warning_Area
   SHALL remain queryable. An owner first entering after publication MAY receive
   fewer than the snapshotted response floor; entry SHALL not restart or refloor
   the release clock.
10. WHEN a release becomes terminal, THE Contagion_System SHALL disable every
    later-entry warning path before removing its Warning_Area from public query.
    It SHALL never evict a live warning receipt or outbox entry. The
    Warning_Receipt_Ledger, warning outbox facts, and closed reservation receipts
    MAY be pruned only after terminal persistence is confirmed, warning paths
    are disabled, every related outbox entry is settled, every late-warning
    reservation is reconciled or released, and the finite retention horizon is
    met; they SHALL not depend on the source Operation_Record remaining.
11. WHEN a Pending release becomes due, THE Contagion_System SHALL prepare or
    resume one durable Release_Resolution_Transaction and SHALL freeze by value
    the ascending stable IDs of every in-world player or agent within the
    snapshotted affected radius; it SHALL include no building. The source
    operation SHALL remain tracked at zero while those candidates are processed.
12. THE Contagion_System SHALL offer release candidates to the requester's own
    and allied entities as indiscriminate collateral without making either an
    allowed Primary_Target_Owner.
13. FOR each potential hostile release recipient, THE Contagion_System SHALL
    call `BranchSystem.is_vector_shielded(target_owner)` with that recipient's
    canonical owner and SHALL skip the recipient when the result is shielded or
    the hostile owner cannot be read. It SHALL not call `may_target` for any
    release collateral recipient; own and allied collateral SHALL bypass this
    hostile shield query and remain indiscriminate.
14. THE Contagion_System SHALL pass each frozen release candidate through the
    common admission contract in Requirement 4 using mutation ID
    `release:{release_id}:recipient:{stable_id}`, fixed infection event ID equal
    to that key plus `:infection`, and the immutable release infection kind,
    payload, and recipient list frozen for that candidate. An original `applied`
    admission SHALL make exactly that reserved infection outbox fact durable in
    the same atomic admission unit as the effect and receipt.
    `duplicate(prior=applied)` SHALL ensure or replay that already-created fact
    and its original event ID without creating a second entry or event. An
    original terminal `rejected` admission, including the `already_present`
    domain no-op, its `duplicate(prior=rejected)` replay, or a definitive durably
    receipted `skipped` candidate SHALL create no infection fact and MAY become
    terminal for that candidate. A `conflict` SHALL create and publish nothing,
    quarantine the Release_Resolution_Transaction, and block that candidate,
    every later cap-dependent candidate, and source-operation settlement until
    explicit reconciliation proves the authoritative key and payload. A
    persistence rejection without a receipt or an `indeterminate` result SHALL
    remain pending under criterion 15.
15. IF recipient lookup or hostile shield validation definitively fails before
    effect mutation, THEN THE Release_Resolution_Transaction SHALL durably record
    that stable ID as terminally `skipped`, log the release ID, coordinate, and
    entity, fail closed for an unreadable hostile recipient, and continue in
    frozen order. A persistence rejection before that skip receipt exists SHALL
    keep the candidate pending. The effect-list replacement or terminal domain
    no-op, immutable admission receipt, and original-applied-only reserved
    infection outbox fact SHALL be one atomic write; `indeterminate` SHALL keep
    the entire unit pending under the same mutation ID for positive readback and
    SHALL not be classified as skipped or retried as a second infection. A
    `conflict` SHALL remain quarantined under criterion 14 and SHALL never be
    converted to a skip, rejection, or terminal candidate by retry.
16. AFTER every release candidate is terminal and the OperationDriver confirms
    terminal `resolved`, THE Post_Commit_Outbox SHALL use the release
    resolution reservation and `append_reserved` before source removal for the
    entry that invokes `AgentSystem.award_operation_xp_once` for
    `OperationRecord.carrier_ref`, kind `contagion`, the snapshotted
    `agent_xp_contagion` amount, and mutation ID `resolve:{op_id}:xp`. No other
    medic or carrying entity SHALL receive release XP, and no live balance value
    SHALL be reread by a retry.
17. AFTER pure validation and the bounded recipient-union check pass, THE
    acceptance path SHALL preallocate `op_id` and durably persist an
    Acceptance_Transaction keyed by that ID before the first resource mutation.
    Its phases SHALL include at least `reserved`, `charged`,
    `pending_confirmed`, `commit_required`, `committed`, `compensating`,
    `compensated`, and `indeterminate`. It SHALL store the immutable cost,
    operation linkage, charge and cooldown keys and receipts, cooldown
    `ready_at`, initial warning marker and publication tick, frozen warning
    recipients and outbox facts, exact outbox reservation ID, slot count and
    receipt, and every refund, cancellation, warning-disable, reservation-
    release, or other compensation receipt.
18. THE Acceptance_Transaction SHALL use this ordered protocol: persist
    `reserved`; call
    `reserve_once("accept:{op_id}:warnings", initial_owner_union_count)` and
    obtain a confirmed claim for that exact bounded union before calling
    `BranchSystem.charge_once` with `accept:{op_id}:charge`; build, track, and
    obtain confirmed durable Pending persistence; call
    `BranchSystem.note_cooldown_once` with the snapshotted `ready_at` and
    `accept:{op_id}:cooldown`; after that keyed cooldown is confirmed, enter the
    irreversible `commit_required` roll-forward boundary; then durably make the
    initial Warning_Area marker live with its publication tick and atomically
    insert every initial warning receipt with its `append_reserved` outbox fact,
    close the fully consumed reservation, and mark `committed`. A definitive
    reservation rejection SHALL refuse and clean up the uncharged transaction;
    an indeterminate reservation SHALL retain the transaction and possible claim
    without charging. The request SHALL acknowledge acceptance and the release
    SHALL become tick-eligible only after the Pending record, keyed cooldown
    receipt, public warning marker, every initial Warning_Receipt_Ledger receipt,
    every warning outbox fact, and the closed reservation receipt are durably
    `confirmed`. No non-raising best effort SHALL satisfy a phase.
19. BEFORE the keyed cooldown is confirmed, an Acceptance_Transaction that
    cannot complete SHALL compensate only from confirmed facts. A confirmed
    charge eligible for refund SHALL use `BranchSystem.refund_once` with
    `accept:{op_id}:refund` and `accept:{op_id}:charge`; an operation that has or
    may have entered Pending SHALL first settle only through the OperationDriver's
    confirming terminal writer, and any confirmed unused warning reservation
    SHALL be closed through `release_once`. AFTER the keyed cooldown is
    confirmed, THE transaction SHALL never compensate or refund and SHALL
    instead retain `commit_required` and roll forward the original Warning_Area,
    receipt insertions, and reserved outbox facts until they are confirmed,
    while withholding acknowledgment and ticks. Unreadable operation or
    reservation storage SHALL never be treated as confirmed absence. Any
    ambiguous write, read, reservation, cancellation, refund, cooldown,
    Warning_Area, outbox, or cleanup result SHALL retain the transaction and its
    linkages in `indeterminate` or `commit_required` as applicable and SHALL be
    reconciled under the original keys after restart. Any same-key/different-
    payload `conflict` SHALL instead quarantine the Acceptance_Transaction and
    prohibit acknowledgment, compensation, reuse, reservation release, or later
    phase progress until explicit reconciliation establishes the authoritative
    payload.
20. EACH Warning_Receipt_Ledger insertion SHALL atomically persist its immutable
    `(release_id, canonical_owner_id)` key, payload hash, original `applied` or
    `rejected` outcome, and matching `append_reserved` outbox fact when applied.
    The same key and payload SHALL return
    `duplicate(prior=<original_outcome>)`; `duplicate(prior=applied)` SHALL
    ensure the already-created warning fact without dispatching twice, while
    `duplicate(prior=rejected)` SHALL create none. A different payload under the
    same key SHALL conflict and fail closed. A release accepted under criterion
    7 SHALL start at or below its then-current cap. Thereafter an unknown owner
    SHALL be inserted only while the live count is strictly below the current
    `contagion_warning_receipt_cap` and its one-slot reservation is confirmed; a
    hot-reload reduction MAY leave preserved receipts above the new cap but
    SHALL admit none, evict none, and SHALL never let any live ledger exceed the
    hard validated maximum of `4096`.
21. EACH Release_Resolution_Transaction SHALL persist separately from the
    Operation_Record and SHALL contain release ID, frozen recipient stable IDs
    in ascending order, each immutable candidate and fixed infection event
    payload and hash, each `release:{release_id}:recipient:{stable_id}` admission
    key and receipt, each corresponding `:infection` event ID, kind, payload and
    recipient list, per-recipient phase, terminal-confirmation receipt, and
    linked escalation, XP, and release-resolution event facts. Before its first
    admission, it SHALL confirm one exact outbox reservation of `K + 3` slots:
    one possible infection entry for each of its `K` frozen candidates plus one
    slot each for escalation, XP, and release-resolution notification. A
    definitive reservation rejection SHALL leave the source operation tracked
    and counting at zero with no candidate effect or terminal transition; an
    `indeterminate` reservation SHALL retain its possible claim and block every
    admission under the same reservation ID. The release-resolution event ID
    SHALL be `resolve:{op_id}:notification`. Each candidate's effect-list change
    or terminal domain no-op, admission key/payload-hash/original-outcome
    receipt, and, only for original `applied`, its `append_reserved` infection
    fact SHALL commit as one atomic admission unit.
22. THE OperationDriver SHALL not confirm `resolved`, untrack, or remove the
    source release until every frozen candidate has a durable terminal original
    outcome of `applied` or `rejected`, a matching
    `duplicate(prior=applied|rejected)` replay, or is definitively `skipped`, and
    terminal persistence is `confirmed`. A `conflict` SHALL quarantine the
    transaction and block terminal settlement until explicit reconciliation; a
    persistence rejection before a terminal receipt, an `indeterminate`
    candidate, or an unresolved reservation SHALL keep the operation tracked at
    zero. After confirmed terminal persistence, the transaction SHALL ensure
    every original-applied infection fact, append the reserved escalation, XP,
    and release-resolution entries, and durably `release_once` every unconsumed
    candidate slot before the source is untracked or removed. After that gate,
    the Release_Resolution_Transaction SHALL remain durable and authoritative
    through delivery and keyed escalation, XP, infection, and release-resolution
    settlement.
23. THE resolution outbox SHALL use the snapshotted resolved tick for the
    reserved `note_escalation_once` entry, the snapshotted acceptance-time XP
    amount for the reserved `award_operation_xp_once` entry, and immutable
    notification payloads. Rebuild or retry MAY ensure and replay those original
    entries under their reservation, but SHALL not call an unkeyed API, allocate
    a replacement event key, reread a live amount or tick, exceed the reserved
    slots, or produce more than the receipt-authorized outcome.

### Requirement 3: Carrier Commitment and the Fortification Counter Contract

**User Story:** As a defender, I want the live medic planting a release to remain
exposed to ordinary counterplay, so that stopping the carrier can stop the
Pending release.

#### Acceptance Criteria

1. THE Contagion_System SHALL accept as `OperationRecord.carrier_ref` only the
   selected player's own alive, actively assigned `medic` that is not in
   reserve, is not incapacitated, and is authorized by the current `bio`
   Branch_Commitment on that planet.
2. WHEN a release is requested, THE selected medic SHALL be In_World on the same
   planet and at Chebyshev distance at most one from the release coordinate.
3. WHEN the release is accepted, THE Contagion_System SHALL persist the medic's
   exact current coordinate as the Plant_Origin_Tile and SHALL require the same
   medic to remain on that exact tile while the operation is Pending.
4. WHILE the carrier is alive, THE Contagion_System's
   `carrier_pause_reason(record)` implementation SHALL return a pause reason if
   the same `carrier_ref` is no longer actively assigned as an eligible `medic`,
   enters reserve, becomes incapacitated, is not In_World, or is not at the
   exact Plant_Origin planet and coordinate. `OperationDriver._suspend_reason`
   SHALL consult that hook and SHALL own the single inherited suspension that
   preserves the remaining clock; the Contagion_System SHALL write no lifecycle
   state and create no second suspend/resume path.
5. A Suspended release SHALL resume through the inherited resume transition only
   after the same `carrier_ref` is again alive, actively assigned as an eligible
   `medic`, out of reserve, not incapacitated, In_World on the exact
   Plant_Origin_Tile, `carrier_pause_reason(record)` returns no reason, and the
   shared source-commitment and origin checks pass. Resume SHALL restore the
   exact held pre-suspension clock and SHALL NOT reapply a response floor, reset
   from release delay, or consult current delay or response-window config.
6. WHEN the carrier dies before release resolution, THE inherited fatal check
   and carrier-death event SHALL cancel the operation before `_suspend_reason`
   or `carrier_pause_reason(record)` can classify it as paused and before any
   release candidate or release XP is applied, even if combat respawns the medic
   immediately.
7. THE Fortification Doctrine_Counter integration SHALL use ordinary standard
   trap or area damage against the live medic through CombatEngine; the medic
   SHALL remain subject to ordinary targeting, mitigation, and damage rules
   without a vector-specific exception.
8. IF Fortification damage reduces the medic to zero HP in the same scheduler
   tick in which the release clock would reach zero, THEN THE tick coordinator
   SHALL complete damage, shared death routing, and carrier-cancellation event
   delivery before advancing that release, so the Cancelled state wins the tie.
9. THE Contagion_System and Fortification system SHALL communicate this counter
   only through the shared combat, event, and OperationDriver contracts and
   SHALL import neither sibling system directly.
10. THE medic's Cure command SHALL be classified as a utility action and SHALL
    not satisfy, replace, or be described as the Fortification
    Doctrine_Counter.

### Requirement 4: Per-Release Uniqueness and Admission Caps

**User Story:** As a player, I want separate releases to coexist within explicit
caps while duplicate copies cannot refresh themselves, so that spread is useful
but bounded.

#### Acceptance Criteria

1. THE Contagion_System SHALL use one common admission function for release and
   transmission candidates. It SHALL accept a stable mutation ID, immutable
   candidate payload, fixed infection event ID, kind, payload and recipients,
   and the confirmed outbox reservation owning that candidate's one possible
   event slot. Its immutable admission payload hash SHALL cover both the effect
   candidate and complete infection fact, and it SHALL return `Mutation_Result`.
   Read-only existing-effect or cap decisions made before effect change SHALL
   become durable original `rejected` receipts when the caller requires crash
   replay.
2. WHEN admission begins, THE function SHALL first remove every readable
   Contagion_Effect whose `ticks_remaining` is at most zero, using
   read-copy-write and leaving ordinary burn, ordinary poison, and unreadable
   isolated entries unchanged.
3. IF the target carries an unexpired Contagion_Effect with the candidate's
   `release_id`, THEN admission SHALL be a terminal domain no-op with original
   `rejected` reason `already_present`; it SHALL not refresh duration, replace
   source, change generation or Raw_Damage, reorder effects, or create an
   infection outbox fact.
4. THE admission function SHALL count only valid unexpired entries whose
   `effect_kind` is `contagion`, and SHALL sum their stored Raw_Damage without a
   Counter_Web or mitigation multiplier.
5. IF admitting a candidate would make that count exceed the current
   `contagion_max_effects_per_entity` or make that sum exceed the current
   `contagion_damage_cap`, THEN admission SHALL return an original terminal
   `rejected` outcome without evicting, clamping, merging, refreshing, rewriting
   any existing effect, or creating an infection outbox fact.
6. IF a hot reload lowers either cap below an entity's existing state, THEN THE
   Contagion_System SHALL retain and continue ticking that state and SHALL admit
   no candidate to that entity until its existing count and aggregate Raw_Damage
   are both below their current caps and the candidate would remain within both.
7. THE Contagion_System SHALL permit valid effects with different `release_id`
   values to coexist up to both current caps, regardless of whether they share a
   source player or origin planet.
8. WHEN a candidate is admitted, THE Contagion_System SHALL snapshot that
   candidate's Raw_Damage and duration into a new mapping and SHALL never retune
   those stored values because Balance_Config later changes.
9. FOR every release or transmission candidate, THE effect-list replacement or
   terminal domain no-op, immutable mutation key, payload hash covering the
   candidate and fixed infection fact, original `applied` or `rejected` outcome
   receipt, and, if and only if that original outcome is `applied`, the immutable
   `append_reserved` Post_Commit_Outbox infection fact SHALL commit as one atomic
   admission unit. There SHALL be no durable state in which the effect is
   admitted but its infection event is ineligible or absent. The same key and
   payload SHALL return `duplicate(prior=<original_outcome>)` without another
   effect mutation. `duplicate(prior=applied)` SHALL ensure or replay the already-
   created outbox fact under the original event ID and SHALL not create a second
   event; `duplicate(prior=rejected)` SHALL create and publish none. The same key
   with a different payload SHALL return `conflict`, make no mutation or event,
   retain and quarantine the owning release or sweep transaction, and block
   terminal settlement and later cap-dependent admissions until explicit
   reconciliation. A persistence-layer rejection before an outcome receipt
   exists SHALL leave the effect list, admission receipt, outbox entry, and
   reserved slot unchanged and SHALL not count as a terminal candidate.
10. AN `indeterminate` admission, a `conflict`, or a persistence rejection
    without a terminal receipt SHALL leave its transaction and reservation
    authoritative. An indeterminate or unreceipted result SHALL be reconciled by
    positive readback of the entire atomic admission unit or retried under the
    original mutation ID, fixed infection fact, and reservation; if readback
    proves original `applied`, only its already-created outbox fact becomes
    deliverable. A conflict SHALL require explicit reconciliation of the key and
    immutable payload. None SHALL be treated as absence, skipped, retried under
    a new key, allowed to allocate a replacement event, or allowed to unblock a
    dependent cap decision or terminal settlement.

### Requirement 5: Deterministic Transmission and Tick Ordering

**User Story:** As a player, I want transmission to produce the same result from
the same tick state, so that container order and density cannot decide who is
infected.

#### Acceptance Criteria

1. AT the start of each Contagion sweep, THE Contagion_System SHALL take an
   immutable by-value snapshot of every indexed carrier's stable ID, canonical
   position, and valid Contagion_Effects before any release or transmission
   candidate is applied during that sweep.
2. FOR each snapshotted carrier effect, THE Contagion_System SHALL compute a
   transmission candidate Raw_Damage as
   `floor(carrier_raw_damage * contagion_transmission_decay)` using the current
   decay value.
3. IF the candidate Raw_Damage is less than 1, or its generation would exceed
   the current `contagion_max_generations`, THEN THE Contagion_System SHALL
   create no candidate.
4. THE Contagion_System SHALL set a candidate's generation to the carrier
   effect's generation plus one, SHALL retain its `release_id`, `source_ref`,
   legacy `source`, and `origin_planet`, and SHALL read the current
   `contagion_duration_ticks` and current tick for its new
   `ticks_remaining` and `applied_tick`.
5. THE Contagion_System SHALL generate candidates only between a snapshotted
   carrier and in-world occupants returned for that carrier's snapshotted exact
   tile, regardless of ownership or alliance.
6. FOR each potential hostile transmission recipient, THE Contagion_System SHALL
   call `BranchSystem.is_vector_shielded(target_owner)` with that recipient's
   canonical owner and SHALL create no candidate when the result is shielded or
   the hostile owner cannot be read. It SHALL not call `may_target` for
   transmission collateral; own and allied spread SHALL bypass this hostile
   shield query and remain indiscriminate.
7. FOR each receiver and `release_id` with multiple candidate carriers, THE
   Contagion_System SHALL retain exactly one candidate by highest Raw_Damage,
   then lowest candidate generation, then lexically lowest source-carrier
   stable ID.
8. FOR each receiver, THE Contagion_System SHALL process retained candidates in
   canonical order by Raw_Damage descending, generation ascending,
   `release_id` lexical ascending, and source-carrier stable ID lexical
   ascending; receivers SHALL be processed by lexical stable ID so iteration
   order is never a tie-breaker.
9. THE Contagion_System SHALL apply each selected candidate through Requirement
   4 using the current count and Raw_Damage caps and SHALL not fall back to a
   lower-precedence carrier after a terminal original `applied` or `rejected`
   outcome, including an `already_present` domain no-op, or a matching
   `duplicate(prior=applied|rejected)` replay. A `conflict` SHALL block the owning
   sweep under Requirement 4.10; a persistence rejection without a terminal
   receipt or an `indeterminate` result SHALL remain pending under the original
   candidate key and reservation before any cap-dependent later candidate.
10. EACH Contagion sweep SHALL use these deterministic phases: first capture by
    value the carrier-effect and tracked-Pending snapshots; revalidate and
    decrement Pending releases in ascending `op_id`; freeze the complete due set
    only after all eligible decrements; prepare or resume due releases in
    ascending `release_id` (which equals `op_id`), enumerating each release's
    frozen recipients in ascending stable ID; then run the bounded transmission-
    transaction recovery or admission phase in criterion 15. A newly prepared
    transmission transaction SHALL derive candidates only from this sweep's
    carrier snapshot and SHALL retain the receiver and candidate order in
    criterion 8. All phases SHALL remain within the existing
    `vector_operations` phase before global `effect_ticks`.
11. A Contagion_Effect admitted by a release or transmission in a tick SHALL take
    its first typed damage tick in that same tick, but SHALL not act as a
    transmission carrier until the next sweep.
12. FOR each release or transmission candidate, an original `applied`
    admission SHALL atomically create exactly one reserved infection outbox fact
    whose kind identifies either `release` or `transmission`, never both.
    `duplicate(prior=applied)` SHALL ensure or replay that same fact and event ID
    without a second event. An original `rejected` candidate, including
    protected, malformed, below-one, over-generation, over-count, over-damage,
    failed, or `already_present` outcomes, and its
    `duplicate(prior=rejected)` replay SHALL create neither infection kind. A
    `conflict` or unresolved `indeterminate` candidate SHALL create no
    independently deliverable fact; conflict SHALL additionally retain and
    quarantine its transaction.
13. IF a carrier-tile query, hostile shield read, or candidate-generation step
    definitively fails before a mutation attempt, THEN THE Contagion_System SHALL
    log its canonical identity, fail closed for only the unreadable hostile
    recipient, and continue the remaining snapshotted tiles and candidates
    without changing canonical order. IF an attempted mutation or persistence
    call raises without a definitive result, THEN it SHALL be `indeterminate`
    and later cap-dependent processing SHALL wait under criterion 14 rather than
    infer a skip.
14. Persistence order, discovery order, tracked-container order, occupant-query
    order, active-effect order, and mapping iteration SHALL never break a tie or
    alter the due-release set, release order, frozen recipient order, selected
    transmission candidate, or final admitted set. An earlier `indeterminate`
    admission whose outcome could affect a later cap decision SHALL be resolved
    by readback under its original key before that later decision is authorized.
    An earlier `conflict` SHALL quarantine the transaction and block every
    dependent decision until explicit reconciliation; it SHALL never be treated
    as a terminal rejection to preserve forward progress.
15. AT the transmission subphase, THE Contagion_System SHALL first look up the at
    most one unresolved Transmission_Sweep_Transaction or indeterminate sweep
    reservation claim. If one exists, it SHALL resume or reconcile that
    authority in its persisted order and SHALL create no new transmission
    transaction, reservation, or mutation from the current carrier snapshot
    until the existing authority is fully settled and pruned. Pending-release
    advancement and the later global effect-tick phase SHALL continue while
    transmission preparation is blocked. If none exists, THE system SHALL
    allocate a stable `sweep_id`, complete the canonical selection, and let `K`
    be its exact finite selected-candidate count. A zero-candidate selection
    SHALL create neither reservation nor transaction. For positive `K`, before
    creating a Transmission_Sweep_Transaction or attempting its first admission,
    THE system SHALL call
    `reserve_once("transmit:{sweep_id}:outbox", K)` for exactly its `K` possible
    infection entries. A definitive reservation rejection SHALL create or mutate
    no sweep transaction or transmission effect. An `indeterminate` reservation
    SHALL retain its possible claim as the sole unresolved sweep authority,
    create no transmission mutation, and block a later `sweep_id` until positive
    readback or `release_once` settles that original claim. A confirmed
    reservation SHALL be stored with exactly one durable
    Transmission_Sweep_Transaction containing the processed tick; reservation
    ID, slot count and receipt; every selected receiver ID, release ID and
    source-carrier stable ID; every immutable candidate payload and hash; every
    fixed infection event ID, kind, payload and recipient list; and the canonical
    order, all before the first admission. Each admission SHALL use
    `transmit:{sweep_id}:receiver:{receiver_id}:release:{release_id}` and its
    infection event SHALL use that key plus `:infection`; retry or rebuild SHALL
    reuse those identities and the original reservation. A `conflict` or
    `indeterminate` admission SHALL retain this sole transaction and prevent a
    later `sweep_id`. Once every admission is terminal, THE transaction SHALL
    ensure every original-applied infection entry, call `release_once` for every
    unconsumed rejected-candidate slot, and remain until all authorized entries
    are terminally settled. Only then SHALL the system prune it, optionally
    retaining one constant-size replay tombstone for the finite declared
    retention horizon, before allocating the next transaction. Persistent live
    sweep storage SHALL therefore be `O(K)` for one frozen sweep and SHALL never
    accumulate one live journal per processed tick.

### Requirement 6: Carrier Index, Work Bound, and Online Semantics

**User Story:** As an operator, I want transmission work bounded by active local
state and offline effects paused, so that map size and disconnected players do
not create hidden load or damage.

#### Acceptance Criteria

1. THE Contagion_System SHALL maintain a nonpersistent Carrier_Index keyed by
   canonical `(planet, x, y)` and containing only In_World entities with at least
   one valid unexpired Contagion_Effect.
2. THE Carrier_Index SHALL be updated from active-effect add, decrement, expiry,
   cure, consumable relief, and respawn-clear events; coordinate movement events;
   death events; and world-entry or world-exit events, rather than from a
   per-tick entity scan.
3. WHEN multiple indexed carriers share a canonical tile, THE transmission sweep
   SHALL issue exactly one spatial occupant query for that unique tile and SHALL
   reuse the returned occupant snapshot for every carrier effect there.
4. FOR a sweep with `C` active indexed carriers, `O` occupants returned across
   unique carrier tiles, and `K` generated candidates, THE Contagion_System's
   work SHALL be `O(C + O + K log K)`, including the canonical candidate and
   receiver grouping and sorting required by Requirement 5, and SHALL perform no
   map-size scan, room scan, global entity scan, or database query.
5. AT server startup, THE composition root SHALL pass the already cached
   effect-capable entity roster to the Contagion_System exactly once to rebuild
   the Carrier_Index from persisted active effects, and the rebuild SHALL not
   issue another roster or database scan.
6. IF the startup roster contains a malformed effect entry or an entity without
   canonical coordinates, THEN THE index rebuild SHALL skip and log that entry
   or entity and SHALL continue indexing the remaining roster.
7. WHILE a player is `PLAYING` or `LINKDEAD` and retains an In_World body, THE
   player's Contagion_Effects SHALL tick, the player MAY transmit, and the player
   MAY receive transmission on the same terms as another In_World entity.
8. WHILE an entity is fully offline, removed to `LOBBY` or `SPAWNING`, or
   otherwise has no In_World body, THE entity's Contagion_Effects SHALL neither
   decrement nor deal damage, the entity SHALL neither transmit nor receive, and
   its stored `ticks_remaining` SHALL be retained unchanged.
9. WHEN an offline entity returns In_World without having respawned, THE index
   and effect tick SHALL resume from the retained effects and remaining ticks,
   without retroactive damage or transmission for paused ticks.
10. WHEN shared death handling respawns an entity, THE existing clear-on-respawn
    behavior SHALL remove its active effects and the corresponding effect-change
    event SHALL remove it from the Carrier_Index before a later sweep.
11. THE Contagion_System SHALL index at most one unresolved
    Transmission_Sweep_Transaction or indeterminate prospective-sweep
    reservation claim. Its persistent live storage SHALL be proportional to the
    one frozen sweep's `K` selected candidates plus constant-size reservation
    metadata, and recovery SHALL use direct keyed entries rather than scan the
    world. The system SHALL settle and prune that transaction or release an
    orphaned pre-transaction claim before allocating another, so storage cannot
    accumulate one live transaction per tick; a retained tombstone SHALL be
    constant-size and bounded by the finite declared replay-retention policy.
12. Post_Commit_Outbox capacity accounting, reservation lookup, append, release,
    and receipt readback SHALL use direct keyed state and SHALL perform no map,
    room, global-entity, or recipient-discovery scan. A reservation SHALL be one
    constant-size claim for an exact finite slot count, not one persistent object
    per unknown future recipient. Outbox live work across all vector workflows
    SHALL be bounded by `vector_outbox_capacity`; release and transmission add
    only `O(K)` already-frozen candidate/event state, initial warnings add at
    most `contagion_warning_receipt_cap`, Cure adds two slots, and a consumable
    use or optional late warning adds one.

### Requirement 7: Explicit Medic Cure

**User Story:** As a Biowarfare player, I want to direct one committed medic to
cure a selected nearby unit, so that cure is deliberate, consent-aware, and
race-safe.

#### Acceptance Criteria

1. THE command layer SHALL require a Cure request to name both one selected
   `medic` and one selected target; co-occupancy without that command SHALL
   cause no Cure action. After the pure initial checks pass and before entering
   the mutation transaction, THE Contagion_System SHALL allocate and durably
   retain a unique `cure_id`; every retry of that attempt SHALL reuse the same
   identity.
2. THE Contagion_System SHALL initially accept Cure eligibility only when the
   selected medic is owned by the requester, alive, actively assigned as
   `medic`, not in reserve, not incapacitated, In_World on the target's exact
   canonical tile, and backed by the requester's current `bio`
   Branch_Commitment on that planet; criterion 11 SHALL revalidate these facts
   at mutation linearization.
3. THE Contagion_System SHALL initially permit the requester to cure the
   requester's own entity, SHALL permit an allied entity only while that
   entity's owner has granted the requester the existing `support` consent, and
   SHALL refuse an entity owned by any other player; this read SHALL not replace
   the linearization-time owner, alliance, and consent check in criterion 11.
4. THE Contagion_System SHALL persist one cure-ready tick per medic, SHALL
   snapshot the current `contagion_cure_cooldown_ticks` at mutation
   linearization, SHALL set the resulting `ready_at` only in the atomic
   successful Cure mutation, and SHALL report the remaining ticks when that
   medic is still cooling down.
5. WHEN an eligible target contains one or more mappings whose `effect_kind` is
   `contagion`, THE Cure transaction SHALL remove all such mappings across all
   `release_id` values and SHALL preserve ordinary poison, burn, status effects,
   and unrelated metadata by value.
6. WHEN the target contains zero mappings with
   `effect_kind = "contagion"`, THE Contagion_System SHALL return a structured
   no-effect refusal and SHALL not rewrite the effect list, start cooldown,
   award XP, or publish a Cure success notification.
7. BEFORE the Cure commit, THE durable Cure transaction SHALL call
   `reserve_once("cure:{cure_id}:outbox", 2)` for exactly its XP and success-
   notification entries. A definitive reservation rejection SHALL return a
   structured refusal before any effect-list or cooldown mutation; an
   `indeterminate` reservation SHALL retain its possible claim and transaction
   without authorizing the commit. Only after confirmed reservation and the
   criterion-11 revalidation, a successful Cure SHALL atomically commit the
   target's rewritten effect list, the medic's snapshotted cooldown `ready_at`,
   the immutable `cure:{cure_id}:commit` payload-hash/original-outcome receipt,
   and both reserved immutable XP and success-notification outbox facts. That
   commit SHALL return `Mutation_Result`; there SHALL be no committed Cure state
   without its required post-commit entries.
8. A Cure commit whose original outcome is `rejected` SHALL leave the effect
   list and cooldown unchanged while atomically retaining its immutable rejected
   outcome receipt; it SHALL authorize no XP or notification and SHALL close its
   two-slot reservation through `release_once`. A persistence rejection before
   any mutation receipt exists SHALL leave domain state and reserved slots
   unchanged and retain the Cure transaction for retry under the same key. An
   `indeterminate` commit SHALL retain the durable Cure transaction and
   reservation, authorize no independently constructed post-commit side effect
   until positive readback confirms the atomic original outcome, and SHALL never
   claim rollback from unreadable storage. A `conflict` SHALL make no mutation,
   quarantine the Cure transaction, immutable payload, and reservation,
   authorize no XP or success event, and require explicit reconciliation rather
   than becoming a refusal or completed no-op. A same-key/same-payload replay of
   a confirmed applied result SHALL return `duplicate(prior=applied)` and ensure
   the same two outbox facts rather than repeat the mutation or create another
   event; a replay of a rejected result SHALL return
   `duplicate(prior=rejected)` and publish none.
9. WHEN concurrent, repeated, retried, or post-crash Cure requests address the
   same effects, THE Contagion_System SHALL combine the target/medic
   serialization boundary with the durable keyed mutation receipt, so that at
   most one distinct commit removes the effects and starts cooldown. Caller-side
   serialization alone SHALL NOT be the exactly-once authority. A retry with the
   same `cure_id` SHALL observe `duplicate(prior=<original_outcome>)`; a distinct
   later `cure_id` SHALL observe committed effects and cooldown and follow the
   no-effect or cooldown outcome.
10. THE Post_Commit_Outbox SHALL use the Cure reservation and
    `append_reserved` for the entry that invokes
    `AgentSystem.award_operation_xp_once(medic, "contagion_cure",
    snapshotted_agent_xp_contagion_cure, "cure:{cure_id}:xp")`. The
    AgentSystem public freeze-aware API SHALL require no protected XP helper;
    retry or rebuild SHALL reuse the stored amount and key and SHALL never reread
    live balance or award a second time.
11. IMMEDIATELY before the first mutation and inside the same target/medic
    serialization boundary, THE Contagion_System SHALL re-resolve the target's
    current canonical owner, the medic's current controller and all medic
    eligibility facts, current alliance, and the target owner's current
    `support` consent for the requester. Ownership change, controller change,
    alliance loss, consent revocation, or unreadability SHALL return a
    structured refusal with no effect, cooldown, commit receipt, XP, or success-
    notification mutation and SHALL durably release any confirmed Cure
    reservation; an indeterminate reservation SHALL instead remain retained for
    reconciliation.
12. THE durable Cure transaction keyed by `cure_id` SHALL store target, medic,
    requester, pre-mutation effect snapshot hash, rewritten payload hash,
    snapshotted cooldown and XP amount, commit key and receipt, phase, exact
    two-slot outbox reservation ID/count/receipt and release receipt, and XP and
    notification outbox keys and receipts. It SHALL remain until every applied
    commit's post-commit work is settled, SHALL reconcile ambiguity only under
    those original immutable keys, and SHALL retain a payload conflict in a
    quarantined phase until explicit reconciliation establishes the authoritative
    key and payload.
13. AFTER a confirmed original-applied Cure, THE Post_Commit_Outbox SHALL call
    `publish_once` with event ID `cure:{cure_id}:success` and the one reserved
    structured Cure success payload for the affected owner and medic owner,
    deduplicated when they are the same player. `duplicate(prior=applied)` MAY
    finish delivery of that same event but SHALL not create or publish a second
    one. No refusal, original or replayed rejected outcome, unresolved
    indeterminate commit, or conflicting replay SHALL publish success.

### Requirement 8: Atomic Healing-Consumable Relief

**User Story:** As any player, I want an existing healing consumable to shorten
all my Contagion_Effects without risking the item on a partial failure, so that
there is a universal utility counter.

#### Acceptance Criteria

1. THE EquipmentSystem SHALL expose injected healing-consumable preflight and
   post-success seams, and THE composition root SHALL inject the
   Contagion_System adapter without either system importing the other directly.
2. THE preflight seam SHALL be side-effect free and SHALL return an immutable
   plan describing whether the current healing use would reduce at least one
   valid Contagion_Effect under the current
   `contagion_consumable_relief_ticks`.
3. WHEN a healing consumable user is at full HP, THE EquipmentSystem SHALL allow
   the use only if the preflight plan would reduce at least one
   Contagion_Effect; otherwise the shipped full-HP refusal and unchanged
   inventory SHALL remain in force.
4. BEFORE mutation, THE EquipmentSystem SHALL allocate and retain one stable
   `use_id`, revalidate the held item, rank, HP, effect snapshot, and relief plan
   under that use transaction, and call
   `reserve_once("consumable:{use_id}:outbox", 1)` for its one possible combined
   success event. A definitive reservation rejection SHALL return a structured
   refusal before inventory, HP, or effect mutation. An `indeterminate`
   reservation SHALL retain the use transaction and possible claim under that ID
   and SHALL authorize no mutation or replacement reservation. Only a confirmed
   reservation SHALL permit removal of exactly one inventory unit provisionally
   before applying healing or Contagion relief.
5. IF inventory removal fails, THEN THE EquipmentSystem SHALL commit neither HP
   healing nor any active-effect mutation, SHALL durably `release_once` the
   confirmed one-slot reservation, and SHALL return a structured failure.
6. WHEN the post-success seam commits, THE Contagion_System SHALL subtract the
   current `contagion_consumable_relief_ticks` from `ticks_remaining` on every
   mapping whose `effect_kind` is `contagion`, SHALL remove each result at or
   below zero immediately, and SHALL preserve ordinary poison, burn, and every
   unrelated active effect.
7. A successful use SHALL atomically commit consumption of exactly one inventory
   unit, HP and Contagion relief, and one immutable
   `append_reserved` Post_Commit_Outbox fact with event ID
   `consumable:{use_id}:success` containing HP healed, relief applied, and
   effects removed. It SHALL publish that one structured combined success event
   through `publish_once` and SHALL not create separate healing and Contagion-
   relief success notifications for the same use.
8. IF preflight or post-success raises, returns failure, observes a stale plan,
   or cannot persist the effect rewrite or reserved success fact, THEN THE
   EquipmentSystem SHALL roll back the provisional inventory removal, HP, and
   active-effect list to their pre-request values, SHALL return a structured
   refusal, and SHALL publish no success notification. A definitively unused
   confirmed reservation SHALL be closed through `release_once`; an
   indeterminate reservation or commit SHALL retain the use transaction and
   possible claim for readback under the original IDs and SHALL never be treated
   as absence.
9. A hook failure SHALL be isolated to that use and SHALL not disable later
   consumable uses; retrying the same `use_id` SHALL reuse its reservation and
   event ID and SHALL not duplicate inventory consumption, relief, outbox entry,
   or publication.
10. WHEN the user is below full HP and the preflight plan contains zero
    Contagion relief, THE EquipmentSystem SHALL preserve the shipped
    healing-consumable behavior, subject to the same pre-mutation one-slot
    reservation, remove-before-commit atomicity, and single combined success
    entry required above.

### Requirement 9: Dormancy, Durable Source, and Pending Lifecycle

**User Story:** As a player, I want abandoning Biowarfare to stop new spread
without erasing damage already inflicted, so that dormancy is neither free cure
nor active doctrine use.

#### Acceptance Criteria

1. WHEN a Pending release's owner loses the `bio` Branch_Commitment on the
   release's planet, THE shared OperationDriver lifecycle SHALL classify the
   commitment lapse as suspension and preserve the exact held Pending clock.
   `origin_fatal_reason(record)` SHALL evaluate independently of commitment and
   SHALL never reinterpret `branch_dormant` or a commitment lapse as fatal merely
   because the still-existing Culture Vats is non-operational under dormancy.
2. WHEN the physical carrier dies, the originating Culture Vats is destroyed or
   deleted, the source base is eliminated, or another inherited fatal-origin
   condition independent of commitment dormancy occurs, THE inherited fatal
   check and confirming cancellation contract SHALL cancel the release before
   pause or resolution; THE Contagion_System SHALL not override that result.
3. THE Contagion_System SHALL perform every Pending suspend, resume, cancel, and
   terminal transition through OperationDriver and SHALL add no vector-local
   lifecycle state writer. Resume SHALL restore the exact held clock and SHALL
   neither reapply the response floor nor read current delay or response-window
   config.
4. WHILE an applied effect's `source_ref` lacks a current `bio`
   Branch_Commitment on that effect's `origin_planet`, THE effect SHALL remain
   stored and tick normally but SHALL generate no transmission candidate.
5. WHEN that same source again holds the `bio` commitment on the persisted
   `origin_planet`, THE effect MAY transmit on a later sweep under current
   decay, generation, duration, shield, and cap rules; dormant sweeps SHALL not
   be replayed.
6. IF `source_ref`, `origin_planet`, or the commitment authority is unreadable,
   THEN THE Contagion_System SHALL fail closed for transmission, SHALL retain and
   tick the applied effect, and SHALL log the isolated source-read failure.
7. THE Contagion_System SHALL enforce dormancy from the metadata on each applied
   effect even after its Operation_Record has become terminal and disappeared.
8. WHEN Branch dormancy releases a medic from the `medic` role, THE
   Contagion_System SHALL refuse Cure through the active-assignment and
   commitment gates; role release SHALL never trigger a Cure.

### Requirement 10: Commands, Outcomes, and Presentation

**User Story:** As a player, I want releases, warnings, infections, and cures to
use consistent structured commands and messages, so that mechanics remain
understandable without leaking internal keys.

#### Acceptance Criteria

1. THE command layer SHALL provide a release command naming a Culture Vats,
   selected medic, canonical planet coordinate, and Primary_Target_Owner; an
   explicit Cure command naming a selected medic and target; and a status command
   reporting each carried Contagion_Effect's `release_id`, effective attribution
   source (`source_ref` first, with legacy `source` only as the fallback defined
   by Requirement 1.17), generation, Raw_Damage, and remaining ticks.
2. THE command layer SHALL provide a query for public active Contagion warnings
   at or affecting a coordinate, including `release_id`, center, radius,
   lifecycle state, remaining ticks, Warning_Receipt_Ledger use and cap, and an
   explicit saturation indicator that does not expose internal mutation keys or
   owner identities.
3. THE Contagion_System, EquipmentSystem seam, and command handlers SHALL return
   an outcome value for every request and SHALL raise no request failure into the
   command layer.
4. THE Contagion_System SHALL compose no player-facing prose and SHALL publish
   notification kinds with structured values only.
5. THE NotificationPresenter SHALL expose a generic
   `render_vector_refusal(key, data)` entry point, and the composition root SHALL
   inject that callable into vector command handlers rather than requiring a
   system or command to import the presenter.
6. WHEN a release, Cure, warning query, or consumable extension refuses a
   request, THE command layer SHALL pass its refusal key and structured data to
   `render_vector_refusal`; an unknown key or formatter failure SHALL retain a
   visible key fallback and SHALL never turn a refusal into acceptance.
7. THE NotificationPresenter SHALL contain a formatter for every notification
   kind and vector-refusal key introduced by this spec, and formatter-coverage
   validation SHALL fail when a declared kind or key has no formatter.
8. THE two infection notification kinds SHALL identify `release` and
   `transmission` distinctly and SHALL include `release_id`, coordinate,
   generation, Raw_Damage, and remaining ticks.
9. THE public-warning, Cure-success, consumable-success, suspension, resume,
   cancellation, infection, and release-resolution notifications SHALL each
   include the stable identities and remaining-time or change values needed to
   render them without another world lookup. Every notification introduced by
   this spec SHALL be an immutable Post_Commit_Outbox entry created only through
   `append_reserved` after an exact confirmed reservation and delivered through
   `publish_once`; one slot SHALL create at most one fixed event and SHALL never
   stand for an unbounded or later-discovered recipient set. No lifecycle or
   effect path SHALL infer publication from a non-raising best-effort call.

### Requirement 11: Balance Configuration and Hot Reload

**User Story:** As a game designer, I want every Contagion value validated and
hot-reload behavior explicit, so that a retune cannot corrupt effects already in
flight.

#### Acceptance Criteria

1. THE Balance_Config SHALL define the following exact snake_case fields in
   addition to the four shipped operation fields:
   `contagion_release_delay_ticks`, `contagion_release_radius`,
   `contagion_radius`, `contagion_damage_per_tick`,
   `contagion_duration_ticks`, `contagion_transmission_decay`,
   `contagion_damage_cap`, `contagion_max_generations`,
   `contagion_max_effects_per_entity`,
   `contagion_consumable_relief_ticks`,
   `contagion_cure_cooldown_ticks`, `agent_xp_contagion_cure`, and
   `contagion_warning_receipt_cap`. The shared global Balance_Config SHALL also
   define the exact field `vector_outbox_capacity`; it is not a per-release or
   per-Branch snapshot.
2. THE SchemaValidator SHALL accept `contagion_release_delay_ticks` only as an
   integer other than a boolean in the inclusive range
   `[minimum_response_window_ticks, 3600]`.
3. THE SchemaValidator SHALL accept `contagion_release_radius` only in `[1, 50]`
   and `contagion_radius` only in `[1, 10]`.
4. THE SchemaValidator SHALL accept `contagion_damage_per_tick` and
   `contagion_damage_cap` only in `[1, 1_000_000]`, and SHALL require
   `contagion_damage_cap >= contagion_damage_per_tick`.
5. THE SchemaValidator SHALL accept `contagion_duration_ticks` and
   `contagion_consumable_relief_ticks` only in `[1, 86400]`, and SHALL require
   `contagion_consumable_relief_ticks <= contagion_duration_ticks`.
6. THE SchemaValidator SHALL accept `contagion_transmission_decay` only as a
   finite numeric value other than a boolean that is greater than `0.0` and
   strictly less than `1.0`.
7. THE SchemaValidator SHALL accept `contagion_max_generations` only in
   `[0, 100]` and `contagion_max_effects_per_entity` only in `[1, 64]`.
8. THE SchemaValidator SHALL accept `contagion_cure_cooldown_ticks` only in
   `[1, 86400]` and `agent_xp_contagion_cure` only in `[0, 1_000_000]`.
9. THE SchemaValidator SHALL validate the shipped `contagion_cost` as a nonempty
   mapping of known canonical resources to positive integer amounts and SHALL
   require it to contain at least one of `Circuits`, `Energy`, or `Nexium`.
10. THE SchemaValidator SHALL accept `contagion_cooldown_ticks` only in
    `[1, 86400]`, `contagion_max_in_flight` only in `[1, 100]`, and
    `agent_xp_contagion` only in `[0, 1_000_000]`.
11. WHEN multiple Balance_Config fields or cross-field relationships are
    invalid, THE SchemaValidator SHALL collect and report every error in one
    validation result before rejecting the load.
12. WHEN a release request is accepted, THE Contagion_System SHALL snapshot its
    delay, minimum response floor, affected radius, Raw_Damage, duration,
    Plant_Origin_Tile, acceptance-time release-XP amount, cooldown `ready_at`,
    and immutable initial warning inputs. `OperationRecord.vector_data` SHALL
    hold only the release fields assigned to it by Requirement 1.20; the
    Acceptance_Transaction, Warning_Area marker, Post_Commit_Outbox, and
    Warning_Receipt_Ledger SHALL separately hold their publication and receipt
    facts. A later reload SHALL rewrite none of those snapshots or any admitted
    effect.
13. ON each new transmission sweep or admission, THE Contagion_System SHALL read
    the current decay, generation limit, duration, effect-count cap, and
    Raw_Damage cap; ON each effect tick it SHALL read current mitigation and at
    most one current checked Counter_Web result. It SHALL use only affirmative
    `neutral` or `advantage` results for arithmetic under Requirement 1.15.
14. WHEN a reload lowers an admission cap below existing state, THE hot-reload
    behavior SHALL be exactly Requirement 4.6 and SHALL not make the reload fail
    solely because persisted entities already exceed the new cap.
15. THE Biowarfare Branch investment score SHALL remain within the shipped
    Branch_Cost_Parity_Tolerance of the six-Branch mean under the existing
    collected parity validation.
16. THE SchemaValidator SHALL accept `contagion_warning_receipt_cap` only as an
    exact non-Boolean integer in the inclusive range `[1, 4096]`. A hot reload
    below a live release's existing receipt count SHALL not evict or rewrite a
    receipt; that release SHALL remain saturated for unknown owners until the
    live count is below the new cap or terminal pruning becomes eligible.
17. THE SchemaValidator SHALL accept the global `vector_outbox_capacity` only as
    an exact non-Boolean integer in the inclusive range `[1, 1_000_000]` and
    SHALL validate it at startup before any vector workflow may mutate. Startup
    SHALL reject mutation readiness, and hot reload SHALL reject the candidate
    configuration while retaining the prior capacity, when the proposed value
    is below current Post_Commit_Outbox use, defined as live unsettled entries
    plus unconsumed reserved slots. Neither path SHALL evict or rewrite existing
    entries, reservations, receipts, or tombstones to make the value fit;
    capacity may become reusable only through ordinary settlement,
    `release_once`, and finite-retention pruning.

### Requirement 12: Correctness Properties

**User Story:** As a developer, I want the identity, determinism, atomicity, and
work bounds asserted over generated states, so that a local example cannot hide
a replication defect.

#### Acceptance Criteria

1. FOR ALL valid version-1 Contagion_Effects, persisting, reading, copying,
   decrementing, and rewriting the active-effect list SHALL preserve
   `release_id`, `source_ref`, `origin_planet`, `generation`, Raw_Damage, and
   `applied_tick` exactly, including after the originating Operation_Record is
   removed.
2. FOR ALL active-effect lists containing malformed and legacy entries,
   processing one malformed entry SHALL not change whether any other readable
   entry ticks, persists, expires, cures, or receives consumable relief.
3. FOR ALL readable damaging active effects with positive Raw_Damage, a burn or
   ordinary poison tick and every Contagion tick authorized by explicit
   ownerless `neutral(1.0)` or an affirmative checked `neutral`/`advantage`
   result SHALL make exactly one typed-effect-tick call and no recursive
   active-effect proc. An owned Contagion recipient whose owner or Branch is
   unreadable or whose checked lookup is `unavailable(reason)` or
   `invalid(reason)` SHALL receive no offensive typed-effect-tick call for that
   effect that tick while its stored clock advances normally. A
   Contagion_System path SHALL perform no direct HP subtraction. Attribution SHALL be `source_ref` whenever present and readable,
   including when legacy `source` differs, and SHALL fall back to legacy
   `source` only for a readable legacy effect lacking `source_ref`; selecting
   either SHALL leave both stored fields unchanged.
4. FOR ALL entities and `release_id` values, admission SHALL leave at most one
   unexpired Contagion_Effect with that identity, and a candidate encountering an
   existing same-release effect SHALL produce the original terminal `rejected`
   reason `already_present` and leave that mapping byte-for-value unchanged apart
   from changes made independently by the effect tick.
5. FOR ALL admission states and current caps, a candidate SHALL be admitted if
   and only if no unexpired same-release effect exists and adding it keeps both
   valid-effect count and aggregate Raw_Damage within their caps; an original
   rejection or `duplicate(prior=rejected)` replay SHALL evict or rewrite no
   existing effect, including after a cap-lowering reload.
6. FOR ALL transmission chains, candidate Raw_Damage SHALL equal the floor of
   the selected carrier Raw_Damage times the current decay, SHALL be at least 1,
   and candidate generation SHALL not exceed the current configured maximum.
7. FOR ALL sweeps, an effect absent from the start snapshot SHALL generate zero
   candidates in that sweep, while an effect admitted before `effect_ticks`
   SHALL take its first damage tick that tick if its entity is In_World.
8. FOR ALL permutations of tracked Pending releases, persistence discovery,
   carriers, occupants, active-effect entries, and mapping iteration, the frozen
   due set SHALL be identical; due releases SHALL be applied by ascending
   `release_id`, each release's recipients by ascending stable ID, and only then
   transmission receivers and retained candidates under their canonical order.
   The selected candidate for each receiver and `release_id` and the final
   admitted set under shared count and Raw_Damage caps SHALL therefore be
   identical, with no container order used as a tie-breaker.
9. FOR ALL sweeps with `C` active indexed carriers, `O` occupants returned
   across unique carrier tiles, and `K` generated candidates, occupant-query
   count SHALL equal the number of unique snapshotted carrier tiles and total
   work SHALL be `O(C + O + K log K)`, including deterministic canonical
   grouping and sorting, independently of map, room, global-entity, and database
   size.
10. FOR ALL fully offline or lobby-removed intervals, an entity's
    `ticks_remaining`, HP, and transmission output SHALL be unchanged by elapsed
    game ticks; returning In_World SHALL resume from exactly the retained state,
    unless respawn cleared it.
11. FOR ALL commitment histories, applied effects SHALL tick during source
    dormancy, SHALL emit no transmission while the persisted source lacks `bio`
    on `origin_planet`, and SHALL need no live Operation_Record to resume future
    transmission.
12. FOR ALL same-tick Fortification carrier kills and release-clock expiry,
    death routing and inherited fatal cancellation SHALL settle before
    `_suspend_reason`, `carrier_pause_reason(record)`, or release resolution,
    leaving zero newly released effects and zero release XP.
13. FOR ALL Cure races, retries, crashes, and rebuilds, one durable `cure_id`,
    one confirmed two-slot reservation, and one `cure:{cure_id}:commit` receipt
    SHALL authorize at most one atomic effect-list rewrite and cooldown start.
    Only an original applied outcome or its `duplicate(prior=applied)` replay
    SHALL own the same one `cure:{cure_id}:xp` entry with the snapshotted amount
    and one `cure:{cure_id}:success` entry; both facts SHALL exist atomically with
    the commit and each delivery SHALL settle at most once. An original rejected
    outcome or `duplicate(prior=rejected)` SHALL authorize neither and SHALL
    release both slots. Indeterminate reservation or commit state SHALL retain
    its claim; conflict SHALL authorize neither and SHALL retain the Cure
    transaction quarantined until explicit reconciliation. Caller serialization
    without those durable receipts SHALL prove no exactly-once property.
14. FOR ALL healing-consumable requests, a confirmed one-slot reservation SHALL
    precede inventory, HP, and effect mutation. Failures, including reservation,
    inventory-removal, hook, effect-write, and outbox-append failures, SHALL leave
    inventory count, HP, and the active-effect list equal to their pre-request
    values and SHALL leave no success event; a confirmed unused slot SHALL be
    released while an indeterminate claim is retained. FOR ALL successes,
    exactly one unit SHALL be consumed, every Contagion_Effect SHALL receive
    exactly one relief subtraction, and the same atomic commit SHALL own exactly
    one combined immutable success entry.
15. FOR ALL infection candidates, the fixed release event derived from
    `release:{release_id}:recipient:{stable_id}` or transmission event derived
    from `transmit:{sweep_id}:receiver:{receiver_id}:release:{release_id}` SHALL
    be included in the admission payload hash. The effect-list change or terminal
    domain no-op, admission receipt, and, if and only if the immutable original
    outcome is `applied`, that one reserved outbox fact SHALL be atomic, so no
    crash can expose an admitted effect without event eligibility. An original
    applied result SHALL create exactly one such fact;
    `duplicate(prior=applied)` SHALL ensure or replay the same fact and event ID
    without a second entry or publication. An original rejected result,
    `duplicate(prior=rejected)`, definitive skipped or failed candidate SHALL
    create neither infection kind. A `conflict` or unresolved `indeterminate`
    SHALL create no independent event authority; conflict SHALL retain and
    quarantine the owning transaction instead of becoming terminal progress.
16. FOR ALL refused release, Cure, warning-query, and healing-consumable
    requests, the player's resources and inventory, every active-effect list,
    medic cooldown and XP, tracked operations, Carrier_Index, and public-warning
    state SHALL remain unchanged. The only permitted durable addition is the
    request's immutable terminal rejected reservation receipt or finite-retention
    tombstone, which SHALL own zero slots and authorize no event, plus the
    explicitly read-only structured refusal outcome.
17. FOR ALL `OperationRecord` constructions and payloads,
    `OperationRecord()` SHALL start at version `1` while `from_dict({})` SHALL
    start at version `0`; every absent, malformed, non-exact-integer, or Boolean
    schema value SHALL decode as `0`, and every present exact non-Boolean integer
    SHALL be preserved. Versions other than `0` and `1`, including negative and
    future versions, SHALL be quarantined and reported without interpretation or
    rewrite. Every absent or malformed/non-mapping `vector_data` member in a
    successfully read payload SHALL yield its own fresh `{}`, but unreadable
    record storage or an unreadable containing payload SHALL remain
    `indeterminate` and yield no authoritative fallback. A version-1 record
    missing required Contagion metadata SHALL still be isolated. FOR ALL valid
    version-1 records and supported nested `vector_data`,
    persist/read/discover/rebuild SHALL preserve every shipped field with deep
    value equality and no shared nested identity; an unsafe legacy record SHALL
    be discarded with no invented operation, warning, or effect.
18. FOR ALL accepted releases, permutations of initially present bodies and
    overlap between area and primary-target paths SHALL produce one initial
    canonical owner union before charge and at most one immutable warning receipt
    per owner and release. A union above `contagion_warning_receipt_cap` SHALL
    refuse before charge. A live ledger SHALL never exceed `4096`, evict a live
    receipt, or insert an unknown owner while its count is at or above the
    current cap; it MAY remain above a newly lowered cap with all prior receipts
    preserved. At saturation, an unknown late owner SHALL receive no direct
    dispatch while the Warning_Area remains queryable and saturation is visible.
    The earliest release tick minus the durably confirmed warning publication
    tick SHALL be at least the snapshotted response floor, and only a first late
    entrant MAY have a shorter personal interval.
19. FOR ALL shared persistence operations, `confirmed` SHALL imply durable
    atomic acknowledgement or positive readback, `rejected` SHALL imply a
    definite non-application, and ambiguity or unreadability SHALL remain
    `indeterminate`; readable absence SHALL never be conflated with unreadable
    storage. FOR ALL keyed mutations, the mutation and immutable payload-hash/
    outcome receipt SHALL be atomic and same-key/same-payload replay SHALL return
    the prior result. A same-key/different-payload replay SHALL return
    `conflict`, make no mutation, retain and quarantine its owning transaction,
    and block dependent progress until explicit reconciliation; it SHALL never
    be treated as terminal rejection, authoritative absence, or completed
    compensation.
20. FOR ALL crashes, timeouts, retries, and restarts at any
    Acceptance_Transaction phase, at most one `accept:{op_id}:charge` SHALL
    apply, and acknowledgment or ticking SHALL occur if and only if Pending,
    `accept:{op_id}:cooldown`, the live Warning_Area marker, every initial
    warning receipt, and warning outbox facts are durably confirmed. Before keyed
    cooldown confirmation, confirmed compensation SHALL first settle any
    possibly Pending operation through the confirming terminal writer and SHALL
    use at most one `accept:{op_id}:refund` linked to the original charge. After
    keyed cooldown confirmation, recovery SHALL roll forward only and SHALL
    retain the operation without acknowledgment or ticking until warning marker,
    initial receipts, and outbox facts are confirmed. Unreadable or ambiguous
    state SHALL retain the transaction and SHALL neither invent absence nor
    claim an unauthorized rollback. A payload conflict SHALL quarantine the
    transaction and prohibit acknowledgment, compensation, reuse, and later
    phase progress until explicit reconciliation.
21. FOR ALL release-resolution crashes and replays, the persisted recipient IDs
    and order SHALL remain unchanged, each
    `release:{release_id}:recipient:{stable_id}` key SHALL produce at most one
    effect-list mutation, and an effect write SHALL never exist without its
    matching atomic receipt. Definitive domain failures recorded as durable
    skipped receipts MAY become terminal; a persistence rejection before a
    terminal receipt or an `indeterminate` outcome SHALL remain pending, while a
    `conflict` SHALL quarantine the transaction and block settlement until
    explicit reconciliation. The release SHALL stay tracked at zero until all
    candidates are terminal and terminal persistence is confirmed, while its
    Release_Resolution_Transaction SHALL survive source-record removal until
    escalation, XP, infection, and resolution-notification outbox work settles.
22. FOR ALL carrier-unavailability and commitment-lapse suspension intervals,
    resume SHALL restore exactly the held clock, independent of elapsed ticks,
    current release-delay config, and current response-floor config. It SHALL
    never refloor or restart the clock. FOR a competing physical carrier death,
    destroyed/deleted origin, or source-base loss, the fatal cancellation SHALL
    win before suspension or resolution.
23. FOR ALL acceptance, compensation, and resolution retries, Biowarfare SHALL
    use exactly `accept:{op_id}:charge`, `accept:{op_id}:refund`,
    `accept:{op_id}:cooldown`, `resolve:{op_id}:escalation`, and
    `resolve:{op_id}:xp`; release escalation SHALL use the snapshotted resolved
    tick and release XP the snapshotted amount. Cure SHALL use
    `cure:{cure_id}:commit`, `cure:{cure_id}:xp`, and
    `cure:{cure_id}:success`. Same-payload replay SHALL not duplicate a charge,
    refund, cooldown, escalation, release or Cure XP award, or notification; no
    feature path SHALL use the legacy unkeyed API.
24. FOR ALL Cure interleavings in which target ownership, medic control,
    alliance, or `support` consent changes after initial validation, the values
    re-resolved immediately before mutation inside the target/medic boundary
    SHALL decide the outcome. Revocation, hostile ownership, controller change,
    or unreadability at that linearization point SHALL leave effects, cooldown,
    mutation receipt, XP, and success notification unchanged.
25. FOR ALL terminal warning cleanup interleavings, no Warning_Receipt_Ledger or
    warning-outbox entry SHALL be pruned until terminal persistence is confirmed,
    every later-entry path is disabled, and every related outbox entry is
    settled. Rebuild SHALL retain unsettled bounded receipts without requiring
    the terminal Operation_Record and SHALL never make pruning permit a late
    duplicate dispatch.
26. FOR ALL transmission crashes and retries, at most one unresolved
    Transmission_Sweep_Transaction SHALL exist. Its frozen candidate identities
    and order, mutation keys, payload hashes, and infection event IDs SHALL
    remain unchanged, and each admission/publication SHALL settle at most once
    under that sweep's keys. While it is `indeterminate`, conflicted, or otherwise
    unsettled, no later sweep transaction or transmission mutation SHALL be
    allocated, although Pending releases and ordinary effect ticks SHALL
    continue. After all admissions and events settle, it SHALL be pruned to no
    more than one constant-size retention tombstone before a later distinct
    `sweep_id` is allocated. Live persistent sweep state SHALL remain `O(K)` for
    one sweep and SHALL never grow by one unresolved journal per tick; a later
    sweep may attempt the same receiver and release only as a new admission whose
    existing-effect duplicate rule still forbids refresh or a second infection
    notification.
