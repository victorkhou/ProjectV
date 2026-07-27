# Requirements Document

## Introduction

The game has accumulated many entity types (players, agents, NPCs, buildings, items, outposts, alliances, resources, technologies, powerups, terrain, planets), and each grew its own admin command dialect. This feature unifies admin CRUD around a shared adapter layer, a standardized verb grammar enforced structurally by the command router base class, and an overlay-backed definition-editing pipeline. The design separates two CRUD planes that share one grammar: instance scope (live objects on the map) and definition scope (YAML-backed definitions in the data registry), pivoted by the `def` keyword.

These requirements are derived from the approved design document. The design's Recorded Decisions (D1 overlay file, D2 clamp-with-note, D3 `def` keyword, D4 adapter layer + per-entity routers, D5 aliases with phased rollout) are settled and reflected here.

## Glossary

- **Entity_Adapter**: A per-entity-type descriptor declaring target resolution, field schemas, CRUD hooks into existing game systems, and the verb-grammar contract (supported verbs, opt-outs, extras, aliases).
- **Adapter_Registry**: The registration point and lookup for all Entity_Adapters; enforces the verb-grammar contract at registration time.
- **Entity_Admin_Router**: The command router base class that builds each `@<entity>` admin command from its Entity_Adapter, providing shared handlers for the Core_Verbs, the Definition_Scope sub-dispatch, and migration aliases.
- **Core_Verbs**: The verb set every Entity_Adapter must support or explicitly opt out of: `list`, `spawn`, `show`, `set`, `destroy`, plus the Definition_Scope verbs `def list`, `def show`, `def set`, `def reset`, `def diff`.
- **Definition_Scope**: Operations addressed with the `def` keyword that act on YAML-backed entity definitions rather than live instances.
- **Instance_Scope**: Operations that act on live game objects (a spawned building, a held item, an active agent).
- **Overlay_Store**: The component that owns the Overlay_File and is its only writer; provides set, reset, diff, and merge operations.
- **Overlay_File**: The single override document `data/definitions_overrides.yaml` holding all admin definition overrides across all definition domains.
- **Data_Registry**: The existing registry that loads base YAML definition files, validates them through the Schema_Validator, and atomically swaps the live registry on successful reload.
- **Schema_Validator**: The existing validation component that checks definition data against per-domain schemas and cross-domain consistency rules.
- **Field_Spec**: The declaration of one modifiable field: name, kind, static or dynamic bounds, enum values, and permission tier.
- **Resolution_Engine**: The shared target-resolution logic in the adapter layer implementing the uniform token grammar (`#N` index, exact key, exact name, unambiguous prefix, optional trailing player scope).
- **List_Cache**: The per-caller, per-entity-type cache of the most recent `list` output rows, indexed by `#N`; replaced on the next `list` invocation.
- **Builder**: The baseline admin permission tier required to use any `@<entity>` admin command.
- **Admin**: The elevated permission tier required for definition writes and specific escalated verbs or fields.
- **Audit_Log**: The existing `_log_admin` logging path recording operator, verb, target, and values for admin mutations.
- **Migration_Alias**: An old command spelling that dispatches to its canonical verb handler and emits a one-line deprecation note.
- **Planet_Registry**: The existing separate registry for planet data that is not part of the hot-reload pipeline.

## Requirements

### Requirement 1: Enforced Uniform Verb Grammar

**User Story:** As a game admin, I want every `@<entity>` admin command to answer the same core verb set, so that learning one entity's commands teaches me all of them.

#### Acceptance Criteria

1. IF an Entity_Adapter is registered that neither supports nor explicitly opts out of every one of the Core_Verbs, THEN THE Adapter_Registry SHALL reject the registration with an error identifying each unaccounted-for Core_Verb and SHALL NOT add the adapter to the registry.
2. WHERE an Entity_Adapter declares an opt-out for a Core_Verb, THE Adapter_Registry SHALL require the opt-out to carry a reason string that is non-empty after trimming whitespace, and SHALL reject the registration without adding the adapter when the reason is missing or empty.
3. WHEN an Entity_Adapter registration is rejected for incomplete verb coverage or a missing opt-out reason, THE Adapter_Registry SHALL raise the failure at server startup, before the adapter's `@<entity>` command becomes invocable.
4. WHEN an admin invokes a supported Core_Verb on any registered entity, THE Entity_Admin_Router SHALL dispatch it through the shared handler for that verb.
5. WHEN an admin invokes an opted-out verb, THE Entity_Admin_Router SHALL respond with the adapter's declared opt-out reason and a pointer to the supported alternative path, and SHALL make no state change.
6. WHERE an Entity_Adapter declares extra verbs beyond the Core_Verbs, THE Entity_Admin_Router SHALL register those extra verbs alongside the Core_Verbs with their declared help text.
7. IF an Entity_Adapter declares an extra verb or alias whose name collides with a Core_Verb, THEN THE Adapter_Registry SHALL reject the registration at server startup.
8. IF an admin invokes a verb that is neither a Core_Verb, a declared extra verb, nor an installed alias for the entity, THEN THE Entity_Admin_Router SHALL return an error listing the available verbs and SHALL make no state change.

### Requirement 2: Uniform Target Resolution

**User Story:** As a game admin, I want one target-addressing grammar across all entities, so that `#N`, keys, names, and prefixes behave identically everywhere.

#### Acceptance Criteria

1. WHEN a target token has the form `#N` where N is a positive integer, THE Resolution_Engine SHALL resolve it as a 1-based index into the rows of the caller's List_Cache for that entity type.
2. WHEN a target token is not of the form `#N`, THE Resolution_Engine SHALL attempt resolution in this order, stopping at the first tier that yields at least one candidate: case-sensitive exact key match, case-insensitive exact name match, case-insensitive prefix match against both keys and names.
3. IF the first resolution tier yielding any candidates yields more than one candidate, THEN THE Resolution_Engine SHALL return an error listing all matching candidates and SHALL select no target.
4. WHERE an entity type supports player scoping, THE Resolution_Engine SHALL scope resolution to the trailing `[player]` argument's holdings, and SHALL default the scope to the caller when the argument is omitted.
5. THE Resolution_Engine SHALL produce identical results for identical inputs of token, List_Cache contents, and registry state.
6. WHEN a Definition_Scope token is resolved, THE Resolution_Engine SHALL delegate key, name, and prefix matching to the existing Data_Registry resolvers.
7. IF a `#N` token's N is less than 1 or greater than the number of rows in the caller's List_Cache for that entity type, THEN THE Resolution_Engine SHALL return an error stating the valid index range and SHALL select no target.
8. IF a non-index target token yields no candidates at any resolution tier, THEN THE Resolution_Engine SHALL return an error indicating that no match was found for the token and SHALL select no target.
9. IF a trailing `[player]` argument is supplied and does not resolve to exactly one player, THEN THE Resolution_Engine SHALL return an error identifying the unresolved player token and SHALL select no target.

### Requirement 3: Bounded Field Writes on Instances

**User Story:** As a game admin, I want `set` to accept my value but keep it within legal bounds, so that I can adjust live entities quickly without breaking game invariants.

#### Acceptance Criteria

1. THE Entity_Adapter SHALL declare every modifiable field as a Field_Spec with a kind, bounds (static, dynamic, or unbounded), and a permission tier.
2. WHEN an admin sets a numeric field to a value outside its static or dynamic bounds, THE Entity_Admin_Router SHALL clamp the value to the nearest bound, apply the clamped value, and include a note stating the applied value and the bounds in the response.
3. WHEN an admin sets a field to a value within its bounds, or sets a field whose Field_Spec declares unbounded bounds to a value matching its kind, THE Entity_Admin_Router SHALL apply the requested value unchanged.
4. WHERE a Field_Spec declares dynamic bounds, THE Entity_Adapter SHALL compute the bounds from the target entity's current state before clamping.
5. WHEN a `set` operation succeeds, THE Entity_Adapter SHALL apply the write through the entity's existing single-writer system path.
6. WHEN the same `set` operation is applied twice with the same value, THE Entity_Adapter SHALL leave the entity in the same final state as applying it once.
7. IF an admin sets a field not present in the entity's Field_Spec schema, THEN THE Entity_Admin_Router SHALL return an error naming the valid fields and SHALL make no state change.
8. IF an admin sets a field to a value that cannot be interpreted as the field's declared kind, THEN THE Entity_Admin_Router SHALL return an error stating the expected kind and SHALL make no state change.
9. IF an admin sets a field with declared enum values to a value not in that enum set, THEN THE Entity_Admin_Router SHALL return an error listing the valid enum values and SHALL make no state change.
10. IF the write through the entity's existing single-writer system path fails, THEN THE Entity_Admin_Router SHALL report an error indicating the write failure to the admin, and THE target entity SHALL retain its pre-command state.

### Requirement 4: Instance CRUD Surface

**User Story:** As a game admin, I want `list`, `spawn`, `show`, and `destroy` to work the same way for every entity that has live instances, so that instance management follows one pattern.

#### Acceptance Criteria

1. WHEN an admin runs `list` with an optional filter, THE Entity_Admin_Router SHALL display the entity's live instances matching the filter as indexed rows and SHALL replace the caller's List_Cache for that entity type with exactly the displayed rows.
2. WHEN an admin runs `spawn` with a definition token, THE Entity_Adapter SHALL create the instance through the entity's existing creation path and THE Entity_Admin_Router SHALL report the created instance's identity in the response.
3. WHEN an admin runs `show` on a resolved instance, THE Entity_Admin_Router SHALL render an identity header, current state lines, and a modifiable-fields block listing each field as its value, its bounds, and its permission tier.
4. WHEN an admin runs `destroy` on a resolved instance, THE Entity_Adapter SHALL delete the instance through the entity's existing deletion path and THE Entity_Admin_Router SHALL confirm the deletion identifying the destroyed instance.
5. IF a `destroy` invocation targets multiple instances, THEN THE Entity_Admin_Router SHALL display the count and identities of the targeted instances, SHALL delete nothing before receiving explicit confirmation, and SHALL cancel with no state change when confirmation is declined.
6. WHEN a `list` invocation matches no instances, THE Entity_Admin_Router SHALL display a no-instances message and SHALL replace the caller's List_Cache for that entity type with an empty row set.
7. IF a `spawn` definition token resolves to no definition, THEN THE Entity_Admin_Router SHALL return an error naming the token and SHALL create nothing.
8. IF the entity's underlying creation or deletion path fails, THEN THE Entity_Admin_Router SHALL report the failure to the admin and SHALL make no further state change.

### Requirement 5: Overlay-Backed Definition CRUD

**User Story:** As a game admin, I want to edit entity definitions at runtime without touching the base YAML files, so that base data stays pristine and git history remains authoritative.

#### Acceptance Criteria

1. THE Overlay_Store SHALL persist all definition overrides across all domains in the single Overlay_File.
2. WHEN a `def set` command names a field present in the adapter's definition Field_Spec schema, THE Overlay_Store SHALL write only the overridden field to the Overlay_File, replacing any existing override for that field rather than duplicating it, and SHALL leave base YAML files unmodified.
3. THE Overlay_Store SHALL perform every Overlay_File write atomically using a temporary file and rename.
4. WHEN an admin runs `def show` on a definition key, THE Entity_Admin_Router SHALL display the merged definition values and SHALL flag each overridden field as an override.
5. WHEN an admin runs `def set` with a valid value followed by `def reset` for the same field, THE Entity_Admin_Router SHALL restore exactly the base YAML value and SHALL clear the override flag for that field.
6. WHEN an admin runs `def diff`, THE Entity_Admin_Router SHALL display every current deviation from base YAML in that entity's domain as the definition key, the field, the base value, and the override value, and an empty overlay SHALL produce an empty diff.
7. WHEN an admin runs `def list`, THE Entity_Admin_Router SHALL list the definitions in that entity's domain from the merged registry.
8. IF a `def set` names a field not present in the adapter's definition Field_Spec schema, THEN THE Entity_Admin_Router SHALL return an error and SHALL leave the Overlay_File unmodified.
9. IF a `def reset` targets a field with no current override, THEN THE Entity_Admin_Router SHALL return an error stating that no override exists for that field and SHALL leave the Overlay_File unmodified.
10. WHEN the Overlay_File is absent at read time, THE Overlay_Store SHALL treat the overlay as empty.
11. IF the Overlay_File exists but cannot be parsed, THEN THE Overlay_Store SHALL report an error and SHALL reject overlay writes until the file is repaired.

### Requirement 6: Merge-Before-Validate Atomic Reload

**User Story:** As a game admin, I want every definition override validated exactly like base data, so that a bad override can never half-apply or corrupt the live game.

#### Acceptance Criteria

1. WHEN the Data_Registry loads definition data, whether at server startup or during an admin-triggered reload, THE Data_Registry SHALL apply the Overlay_Store merge to each raw YAML document before the Schema_Validator runs.
2. THE Schema_Validator SHALL validate the merged result of base data and overrides using the same schemas and cross-domain rules applied to base data, with no rule relaxed for overridden values.
3. WHEN a `def set` or `def reset` command's overlay write succeeds, THE Entity_Admin_Router SHALL trigger a registry reload covering all definition domains and SHALL respond to the admin only after the reload outcome is known.
4. WHEN validation of the merged data succeeds, THE Data_Registry SHALL atomically swap the live registry to the reloaded state and THE Entity_Admin_Router SHALL report the before and after values to the admin.
5. IF the reload fails for any reason, including merged-data validation failure, a parse error, or an input/output error, THEN THE Data_Registry SHALL leave the live registry unchanged, THE Overlay_Store SHALL restore the Overlay_File to its pre-command snapshot, and THE Entity_Admin_Router SHALL relay the errors to the admin.
6. WHILE a `def set` or `def reset` overlay-write-and-reload sequence is in progress, THE Entity_Admin_Router SHALL queue subsequent `def set` and `def reset` commands in arrival order, executing each against the state left by its predecessor.
7. THE before and after values reported for a `def set` or `def reset` SHALL be the merged definition values immediately before the overlay write and immediately after the successful reload, respectively.
8. IF the overlay write itself fails, THEN THE Entity_Admin_Router SHALL trigger no reload, SHALL leave the Overlay_File unchanged, and SHALL return an error to the admin.

### Requirement 7: New and Extended Admin Surfaces

**User Story:** As a game admin, I want the entities that currently lack admin coverage to gain it under the same grammar, so that technologies, powerups, terrain, and planets stop being unmanageable.

#### Acceptance Criteria

1. THE Entity_Admin_Router SHALL provide a `@tech` command supporting `list` of the technologies granted to the trailing `[player]` argument (defaulting to the caller when omitted), `grant` mapped to the spawn verb, `revoke` mapped to the destroy verb, `show`, and the full Definition_Scope, with the instance `set` verb opted out with a reason stating that technologies have no modifiable per-instance fields.
2. THE Entity_Admin_Router SHALL provide `show` and `set` verbs for `@building` instances, including an integer `level` field declared as a Field_Spec with static bounds of 1 to 5.
3. THE Entity_Admin_Router SHALL provide `show` and `set` verbs for `@agent` instances, with the Definition_Scope opted out because agents have no YAML definition domain.
4. THE Entity_Admin_Router SHALL provide `@powerup` and `@terrain` commands supporting the full Definition_Scope with all instance verbs opted out.
5. THE Entity_Admin_Router SHALL provide a `@planet` command supporting `def list` and `def show` served from the Planet_Registry, with all other Core_Verbs (including `def set`, `def reset`, and `def diff`) opted out with a reason stating that planets are not hot-reloadable.
6. WHEN a `set` on an item instance modifies a roll-derived field, THE Entity_Adapter SHALL derive the field bounds from the item definition's roll bands and SHALL re-stamp the item's quality score through the existing recompute path before returning the success response.
7. WHEN a `@tech grant` completes successfully, THE Entity_Adapter SHALL add the technology to the target player's researched technologies through the existing research path and SHALL recompute the player's derived tech bonuses before returning the success response.
8. WHEN a `@tech revoke` completes successfully, THE Entity_Adapter SHALL remove the technology from the target player's researched technologies and SHALL recompute the player's derived tech bonuses before returning the success response.
9. IF a `@tech grant` targets a technology the target player already holds, or a `@tech revoke` targets a technology the target player does not hold, THEN THE Entity_Admin_Router SHALL return an error stating the player's current grant state for that technology and SHALL make no state change.

### Requirement 8: Layered Permission Model

**User Story:** As a game admin, I want permission checks layered by command, verb, and field, so that global balance changes require higher privilege than single-object tweaks.

#### Acceptance Criteria

1. THE Entity_Admin_Router SHALL require the Builder tier as the floor for every `@<entity>` admin command, such that a caller below the Builder tier can invoke no verb of the command.
2. THE Entity_Admin_Router SHALL permit read verbs (`list`, `show`, `def list`, `def show`, `def diff`) at the Builder tier.
3. THE Entity_Admin_Router SHALL require the Admin tier for `def set` and `def reset` on every entity.
4. WHERE a Field_Spec declares a permission tier above the verb's tier, WHEN a `set` or `def set` command names that field, THE Entity_Admin_Router SHALL check the field-level tier after the verb-level check succeeds and before bounds handling, and SHALL apply no additional field-level check when the Field_Spec tier is at or below the verb's tier.
5. IF a caller lacks the required tier for a verb or field, THEN THE Entity_Admin_Router SHALL reject the command in full with an error message indicating the required tier, SHALL make no instance state change, and SHALL write nothing to the Overlay_File.
6. THE Entity_Admin_Router SHALL require the Builder tier for the instance mutation verbs (`spawn`, `set`, `destroy`) and for adapter-declared extra verbs.
7. WHERE an Entity_Adapter declares a permission tier above a verb's default tier, THE Entity_Admin_Router SHALL enforce the declared tier for that verb in place of the default.

### Requirement 9: Audit Logging

**User Story:** As a game admin, I want every admin mutation recorded, so that changes to instances and definitions are reconstructable after the fact.

#### Acceptance Criteria

1. WHEN a mutating verb (`spawn`, `set`, `destroy`, `def set`, `def reset`, or an extra verb or Migration_Alias that dispatches to one of these) completes successfully, THE Entity_Admin_Router SHALL record exactly one Audit_Log entry containing the operator, the canonical verb, the entity type, the resolved target, and, for the field-writing verbs `set` and `def set`, the field name, the requested value, and the applied value.
2. WHEN a `def set` or `def reset` completes, THE Entity_Admin_Router SHALL record the reload outcome in the Audit_Log entry, stating either that the reload was applied or that validation failed and the Overlay_File was rolled back to its pre-command snapshot.
3. WHEN a clamp occurs during `set`, THE Audit_Log entry SHALL record both the requested value and the differing applied value, such that the two values are distinguishable in the entry.
4. IF the Audit_Log write fails, THEN THE Entity_Admin_Router SHALL leave the completed mutation applied and SHALL include a note in the command response indicating that audit logging failed.

### Requirement 10: Error Handling and Staleness Surfacing

**User Story:** As a game admin, I want clear, safe failure behavior, so that mistakes never guess, never half-apply, and stale state is visible.

#### Acceptance Criteria

1. IF a `#N` token is used when the caller has no List_Cache for that entity type, THEN THE Entity_Admin_Router SHALL respond with an instruction to run `list` first and SHALL make no state change.
2. IF a `#N` token references an instance that no longer exists, THEN THE Entity_Admin_Router SHALL report the cached list as stale, instruct the caller to re-run `list`, and SHALL make no state change.
3. WHEN `show` renders an instance having one or more stamped attributes whose values differ from the current merged definition values, THE Entity_Admin_Router SHALL append a staleness note for each such attribute stating the attribute name, the stamped value, and the current merged definition value.
4. WHEN `def show` renders a definition key for which at least one live instance exists, THE Entity_Admin_Router SHALL append a note stating that existing instances retain previously stamped values.
5. WHEN a definition override changes a value that instances read lazily from the definition, THE Data_Registry SHALL serve the merged value on the first lazy read following the successful registry reload and SHALL leave all stamped instance attributes unmodified.
6. IF a `#N` token's index N exceeds the number of rows in the caller's List_Cache for that entity type, THEN THE Entity_Admin_Router SHALL return an error stating the valid index range and SHALL make no state change.
7. IF a non-index target token matches no candidate by exact key, exact name, or unambiguous prefix, THEN THE Entity_Admin_Router SHALL return an error indicating the token could not be resolved and SHALL make no state change.

### Requirement 11: Migration Aliases and Phased Compatibility

**User Story:** As a game admin, I want my existing command spellings to keep working during the migration, so that muscle memory is never punished mid-rollout.

#### Acceptance Criteria

1. WHEN a Migration_Alias is invoked, THE Entity_Admin_Router SHALL dispatch to the canonical verb handler and produce state changes, permission-check outcomes, command output (excluding the deprecation note), and Audit_Log entries identical to invoking the canonical verb with the same arguments.
2. WHEN a Migration_Alias is invoked, THE Entity_Admin_Router SHALL emit a one-line deprecation note naming both the invoked alias spelling and its canonical spelling, in addition to the canonical verb's normal output.
3. WHEN help output is requested for a command that has at least one installed Migration_Alias, THE Entity_Admin_Router SHALL include a legacy-spellings section listing each installed Migration_Alias paired with its canonical spelling.
4. WHILE `@item` or `@building` Migration_Aliases remain installed (the deprecation window, which begins at that entity's rollout phase and ends only by a separate future removal decision), WHEN `@item list` or `@building list` is invoked, THE Entity_Admin_Router SHALL list instances and SHALL include a pointer stating that definition listing moved to `def list`.
5. THE Entity_Admin_Router SHALL install exactly the Migration_Aliases recorded in the design's per-entity matrix: `stats` to `show` for items, `create` to `spawn` for agents, `inspect` to `show` and `disband` to `destroy` for alliances, `tiers` to `def list` for outposts, `give` to `spawn` for resources, the old `@player` `level` and `rank` verb forms to their `set` equivalents, and the old `@stat` `hp`, `maxhp`, and `xp` verb forms to their `set` equivalents.
6. WHEN an entity's router is migrated to the unified grammar in its rollout phase, THE Entity_Admin_Router SHALL install that entity's Migration_Aliases in the same rollout phase, such that each old spelling remains functional without interruption between phases.
