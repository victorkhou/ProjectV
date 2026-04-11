# Requirements Document

## Introduction

This document defines the requirements for an RTS-inspired combat overworld game built on top of the Evennia MU* framework. The game features a modern/futuristic theme where players navigate coordinate-based overworld maps across multiple planet types, gather terrain-specific resources, construct buildings following a technology tree rooted in a Headquarters prerequisite, engage in real-time PvP combat using equippable items from a unified data-driven item system, and progress through an extensive military rank system that unlocks advanced technologies and powerups. The system leverages Evennia's existing XYZGrid contrib for map management, the Traits contrib for character stats, and Evennia's Script system for real-time tick-based game loops. Players can explore Earth-like planets with natural resources and Industrial planets with futuristic resources. When a player logs out, their buildings enter an offline protection state. The game includes global notifications, a chat system, and equipment-generating buildings. All items (weapons, armor, gadgets, consumables) are represented by a single GameItem typeclass, differentiated entirely by YAML-defined slot types and stat modifiers.

## Glossary

- **Overworld**: The primary coordinate-based game map built on Evennia's XYZGrid system, representing the playable terrain where all gameplay occurs. Each Planet has its own Overworld instance.
- **Planet**: A distinct game world with its own Overworld, terrain types, and resource mappings. Planet types include Earth_Planet and Industrial_Planet.
- **Earth_Planet**: A Planet type featuring natural terrain and organic resources. Terrain types: Plains, Mud, Forest, Rock, Mountain. Resources: Straw, Clay, Wood, Stone, Iron.
- **Industrial_Planet**: A Planet type featuring futuristic terrain and technological resources. Terrain types: Power_Grid, Scrapyard, Circuit_Field, Ruins. Resources: Energy, Metals, Circuits.
- **Tile**: A single coordinate location on the Overworld, represented by an XYZRoom. Each Tile has a terrain type and may contain resources, buildings, or units.
- **Player_Character**: An Evennia Character typeclass representing a player in the game world, extended with combat stats, rank, and inventory via the Traits system.
- **Rank_System**: The progression mechanic that tracks a Player_Character's combat experience and determines which technologies and powerups are available. Ranks are earned and lost through combat.
- **Rank**: A discrete level within the Rank_System corresponding to a military rank (e.g., Recruit, Private, Sergeant, Captain, General). Each Rank has a defined experience threshold. A Player_Character can be demoted when Combat_XP falls below the threshold.
- **Combat_XP**: Experience points earned by a Player_Character through combat actions (damaging enemies, destroying buildings, winning engagements). Combat_XP accumulates toward the next Rank and can decrease upon elimination.
- **Resource**: A collectible material found on Overworld Tiles. Resource types vary by Planet type. Earth_Planet resources: Straw, Clay, Wood, Stone, Iron. Industrial_Planet resources: Energy, Metals, Circuits.
- **Resource_Node**: A Tile feature that produces a specific Resource type at a defined rate when harvested or when a Resource_Building is placed on it.
- **Building**: A persistent structure placed on an Overworld Tile by a Player_Character. Buildings have health points, an owning player, and provide gameplay functions (resource production, defense, equipment generation, technology access).
- **Headquarters (HQ)**: The foundational Building that a Player_Character must construct before any other Building type. The Headquarters serves as the prerequisite for all other Buildings in the Technology_Tree.
- **Resource_Building**: A Building type that extracts Resources from a Resource_Node over time. Resource_Buildings have a Building_Level (1-5) that determines production yield. On Earth_Planet: Mill (MM), Quarry (QQ), Mine (II), Lumberyard (LL), Kiln (KK). On Industrial_Planet: Harvester.
- **Building_Level**: A numeric value (1-5) representing the upgrade tier of a Resource_Building. Higher Building_Level increases the Resource production yield per Game_Tick.
- **Equipment_Building**: A Building type that automatically generates GameItem instances each Game_Tick. Includes Armory (AA) for weapon-slot items and Armorer (AR) for armor-slot items. Constructible on any terrain, requires Headquarters.
- **Turret**: A Building type that automatically attacks hostile Player_Characters within a defined range. Constructible on any terrain, requires Headquarters.
- **Tech_Lab**: A Building type that unlocks new technologies and powerups for the owning Player_Character, gated by Rank.
- **Technology_Tree**: The progression system that defines which Buildings unlock access to advanced Buildings, GameItems, and upgrades. Rooted at the Headquarters.
- **Technology**: A researchable upgrade unlocked at a Tech_Lab, gated by Rank. Technologies provide permanent stat bonuses or unlock new Building types.
- **Powerup**: A temporary combat buff unlocked at a specific Rank and activated by the Player_Character during combat.
- **Combat_Engine**: The Evennia Script responsible for resolving real-time combat between Player_Characters and/or Buildings. It runs on a tick-based loop.
- **Attack_Action**: A combat command issued by a Player_Character targeting another Player_Character or Building within range on the Overworld.
- **GameItem**: A single unified typeclass extending Evennia's DefaultObject that represents any equippable or usable item. GameItems are differentiated entirely by their YAML-defined properties: slot (weapon, armor, gadget, consumable, etc.), stat_modifiers (damage, damage_reduction, sight_range, move_speed, etc.), ammo_cost (optional), classification, and required_rank. Adding a new item category requires only a new slot value and stat keys in YAML — no code changes.
- **Equipment_Slot**: A named slot on a Player_Character's EquipmentHandler where a GameItem can be equipped (e.g., weapon, armor, gadget). Each slot holds at most one GameItem. The set of valid slots is defined in the item definitions YAML.
- **EquipmentHandler**: A handler on the Player_Character that manages Equipment_Slots. Adapted from EvAdventure's pattern, it provides generic equip/unequip operations that work with any slot type.
- **Health_Points (HP)**: A gauge trait representing the durability of a Player_Character or Building. When HP reaches zero, the entity is destroyed or defeated.
- **Damage**: A numeric value subtracted from a target's HP when an Attack_Action resolves. Determined by the attacker's equipped GameItem in the weapon slot (via its stat_modifiers).
- **Range**: The maximum Tile distance (measured in coordinate units) at which an Attack_Action or Turret can engage a target.
- **Game_Tick**: A recurring server-side interval (managed by an Evennia Script) during which the Combat_Engine resolves pending actions, Resource_Nodes produce resources, Equipment_Buildings generate equipment, and Building effects apply.
- **Fog_of_War**: A visibility system that limits a Player_Character's view of the Overworld to Tiles within a defined sight range, hiding distant enemy positions and activities.
- **Offline_Building**: The protected state a Building enters when the owning Player_Character logs out or disconnects. An Offline_Building cannot be destroyed or entered by other Player_Characters.
- **Global_Notification**: A server-wide message broadcast to all connected Player_Characters regardless of location, delivered via Evennia's `SESSION_HANDLER`. Announces significant game events such as logins, logouts, eliminations, and rank changes.
- **Global_Chat**: A server-wide chat message sent by a Player_Character using the "chat" command, delivered via Evennia's Channel system (a "Global" channel) to all subscribed Player_Characters regardless of location.
- **Direct_Message**: A private chat message sent by a Player_Character to a specific target Player_Character by name, delivered via Evennia's built-in `page` command. Visible only to sender and recipient.
- **Definition_File**: A YAML file stored in a designated data directory that contains entity definitions (buildings, items, ranks, technologies, terrain, powerups). Definition_Files are loaded at startup and can be hot-reloaded at runtime.
- **Schema_Validator**: The component that validates Definition_File contents against expected schemas before the game uses the data.
- **Game_Balance_Config**: A YAML configuration file containing tunable numeric values for game balance (production scaling, turret damage, XP awards, gather amounts, respawn timers, combat parameters).
- **Data_Registry**: A centralized runtime registry that holds all loaded definitions and configuration, providing game systems access to entity data without hardcoded constants.
- **Hot_Reload**: The ability to re-read and re-validate Definition_Files at runtime without restarting the server, applying updated definitions to the running game. Triggered via the `@reloaddata` command (distinct from Evennia's `@reload` which restarts server code).

## Requirements

### Requirement 1: Overworld Map and Planet Types

**User Story:** As a player, I want to explore coordinate-based overworld maps across different planet types with varied terrain, so that I can discover different resources, find strategic positions, and plan my expansion on worlds with distinct characteristics.

#### Acceptance Criteria

1. THE Overworld SHALL represent each Planet as a two-dimensional coordinate grid built on Evennia's XYZGrid system, where each coordinate corresponds to a Tile.
2. THE Overworld SHALL support at least two Planet types: Earth_Planet and Industrial_Planet, each with distinct terrain types and resource mappings.
3. THE Earth_Planet SHALL define the following terrain types with two-character map symbols: Plains (PP), Mud (~~), Forest (FF), Rock (RR), and Mountain (MT), each stored as a Tag on the corresponding XYZRoom.
4. THE Industrial_Planet SHALL define the following terrain types with two-character map symbols: Power_Grid (GG), Scrapyard (SS), Circuit_Field (CC), and Ruins (UU), each stored as a Tag on the corresponding XYZRoom.
5. WHEN a Player_Character enters a Tile, THE Overworld SHALL display the Tile's terrain type, any Resource_Nodes present, any Buildings present, and any visible Player_Characters.
6. WHEN a Player_Character issues a movement command, THE Overworld SHALL move the Player_Character to the adjacent Tile in the specified cardinal direction within one Game_Tick.
7. WHILE a Player_Character is on the Overworld, THE Overworld SHALL display a local ASCII map view showing Tiles within the Player_Character's sight range, where each Tile is rendered as a two-character symbol representing its contents.
8. THE ASCII map SHALL render each Tile as exactly two characters wide, using the following priority: a Player_Character indicator ("@@" for self, "**" for others) takes highest priority, followed by the Building abbreviation (e.g., HQ, MM, VV) if a Building is present, followed by the terrain type symbol if no Building or Player_Character is present.
9. THE Fog_of_War system SHALL hide Tile contents (enemy Player_Characters, enemy Buildings) for Tiles outside the Player_Character's sight range, displaying only terrain type.
9. IF a Player_Character attempts to move to a Tile that is impassable (e.g., blocked terrain), THEN THE Overworld SHALL reject the movement and notify the Player_Character that the Tile is impassable.

### Requirement 2: Resource Gathering and Planet-Specific Resources

**User Story:** As a player, I want to gather terrain-specific resources that vary by planet type, so that I can use them to construct buildings, craft equipment, and research technologies.

#### Acceptance Criteria

1. THE Earth_Planet SHALL define the following terrain-to-resource mapping: Plains yields Straw, Mud yields Clay, Forest yields Wood, Rock yields Stone, and Mountain yields Iron.
2. THE Industrial_Planet SHALL define the following terrain-to-resource mapping: Power_Grid yields Energy, Scrapyard yields Metals, and Circuit_Field yields Circuits.
3. WHEN a Player_Character issues a harvest command on a Tile containing a Resource_Node, THE Resource_Node SHALL yield a defined quantity of the corresponding Resource to the Player_Character's inventory.
4. THE Player_Character SHALL store collected Resources as numeric Trait counters, with each Resource type tracked independently across all planet types.
5. IF a Player_Character issues a harvest command on a Tile that does not contain a Resource_Node, THEN THE Overworld SHALL notify the Player_Character that no resources are available at that location.
6. WHEN a Resource_Node is depleted by manual harvesting, THE Resource_Node SHALL regenerate to full capacity after a configured number of Game_Ticks.
7. WHILE a Tile does not contain a Resource_Building, THE Overworld SHALL limit the available gatherable Resources on that Tile to 1 unit at a time.

### Requirement 3: Building Construction and Technology Tree

**User Story:** As a player, I want to construct buildings following a technology tree rooted in a Headquarters, so that I can produce resources automatically, generate equipment, defend my territory, and access advanced technologies.

#### Acceptance Criteria

1. THE Building system SHALL require a Player_Character to construct a Headquarters (HQ) before constructing any other Building type.
2. IF a Player_Character issues a build command for any Building type other than Headquarters and the Player_Character has not constructed a Headquarters, THEN THE Building system SHALL reject the construction and notify the Player_Character that a Headquarters is required first.
3. WHEN a Player_Character issues a build command specifying a Building type and target Tile, THE Building system SHALL create the Building on that Tile, deducting the required Resources from the Player_Character's inventory.
4. IF a Player_Character issues a build command without sufficient Resources, THEN THE Building system SHALL reject the construction and notify the Player_Character of the missing Resources and their quantities.
5. IF a Player_Character issues a build command targeting a Tile that already contains a Building, THEN THE Building system SHALL reject the construction and notify the Player_Character that the Tile is occupied.
6. WHEN a Building is created, THE Building system SHALL assign the Building an owning Player_Character, a Health_Points gauge initialized to the Building type's maximum HP, and the Building type's functional properties.
7. WHILE a Building's Health_Points are above zero, THE Building SHALL remain active and provide its gameplay function.
8. WHEN a Building's Health_Points reach zero, THE Building system SHALL remove the Building from the Tile, notify the owning Player_Character, and revoke any features or unlocks that depended solely on the destroyed Building.
9. THE Building system SHALL restrict Building placement to Tiles within a configurable distance of the Player_Character's current position.
10. THE Building system SHALL prevent a Player_Character from constructing more than one Building on the same Tile.
11. WHEN a Building is constructed, THE Building system SHALL evaluate the Technology_Tree and unlock any new Building types, GameItem types, or upgrades that the constructed Building enables for the owning Player_Character.

### Requirement 4: Building Types and Abbreviations

**User Story:** As a player, I want specialized building types for resource generation, equipment production, and defense, so that I can strategically develop my territory with diverse capabilities.

#### Acceptance Criteria

1. THE Building system SHALL identify each Building type by the following abbreviations, which are used as the Building's display symbol on the ASCII overworld map: Headquarters (HQ), Mill (MM), Quarry (QQ), Mine (II), Lumberyard (LL), Kiln (KK), Armory (AA), Armorer (AR), Turret (VV), Tech_Lab (TL), and Harvester (HV).
2. THE Building system SHALL define the following Earth_Planet Resource_Building types, each requiring a Headquarters and a specific terrain type: Mill (MM) on Plains generating Straw, Quarry (QQ) on Rock generating Stone, Mine (II) on Mountain generating Iron, Lumberyard (LL) on Forest generating Wood, and Kiln (KK) on Mud generating Clay.
3. THE Building system SHALL define the Harvester (HV) as the Industrial_Planet Resource_Building type, requiring a Headquarters and placement on a Tile containing a Resource_Node.
4. IF a Player_Character issues a build command for a Resource_Building type and the Tile's terrain type does not match the required terrain for that Resource_Building, THEN THE Building system SHALL reject the construction and inform the Player_Character of the required terrain type.
5. THE Building system SHALL define the following Equipment_Building types, each requiring a Headquarters and constructible on any terrain type: Armory (AA) generating weapon-slot GameItems each Game_Tick, and Armorer (AR) generating armor-slot GameItems each Game_Tick.
6. THE Building system SHALL define the Turret (VV) as a Defense_Building type, requiring a Headquarters and constructible on any terrain type.
7. THE Building system SHALL define the Tech_Lab (TL) as a research Building type, requiring a Headquarters and constructible on any terrain type.

### Requirement 5: Resource Building Levels and Upgrades

**User Story:** As a player, I want to upgrade my resource buildings to increase production, so that I can produce resources more efficiently as the game progresses.

#### Acceptance Criteria

1. WHEN a Resource_Building is constructed, THE Building system SHALL initialize the Resource_Building at Building_Level 1.
2. WHILE a Resource_Building is active and the owning Player_Character is online, THE Resource_Building SHALL produce the associated Resource for the owning Player_Character on each Game_Tick, with yield proportional to the Building_Level.
3. WHEN a Player_Character issues an upgrade command targeting a Resource_Building the Player_Character owns and the Player_Character possesses sufficient Resources, THE Building system SHALL deduct the upgrade cost and increase the Building_Level by 1.
4. THE Building system SHALL calculate the Resource cost for upgrading a Resource_Building as the base construction cost multiplied by the target Building_Level.
5. IF a Player_Character issues an upgrade command without sufficient Resources, THEN THE Building system SHALL reject the upgrade and inform the Player_Character of the missing Resources and their quantities.
6. THE Building system SHALL enforce a maximum Building_Level of 5 for all Resource_Buildings.
7. IF a Player_Character issues an upgrade command targeting a Resource_Building already at Building_Level 5, THEN THE Building system SHALL reject the upgrade and inform the Player_Character that the maximum level has been reached.
8. WHEN a Resource_Building reaches Building_Level 5, THE Resource_Building SHALL produce a maximum of 1000 Resources per Game_Tick.

### Requirement 6: PvP Combat and Items

**User Story:** As a player, I want to attack other players and their buildings using equippable modern and futuristic items, so that I can compete for territory and resources.

#### Acceptance Criteria

1. THE Combat_Engine SHALL read damage values from the attacker's equipped GameItem in the weapon Equipment_Slot, using the item's stat_modifiers (specifically the "damage" key). GameItems with modern and futuristic classifications each define a damage stat_modifier and an optional ammo_cost.
2. WHEN a Player_Character issues an equip command specifying a valid GameItem and a target Equipment_Slot, THE EquipmentHandler SHALL place the GameItem in the appropriate slot. If the item's slot does not match the target slot, the equip SHALL be rejected.
3. WHEN a Player_Character issues an Attack_Action targeting another Player_Character within Range, THE Combat_Engine SHALL calculate Damage from the attacker's equipped weapon-slot GameItem's stat_modifiers and apply the Damage to the target's Health_Points on the next Game_Tick.
4. WHEN a Player_Character issues an Attack_Action targeting a Building within Range, THE Combat_Engine SHALL calculate Damage from the attacker's equipped weapon-slot GameItem's stat_modifiers and apply the Damage to the Building's Health_Points on the next Game_Tick.
5. WHILE a Turret Building is active, THE Turret SHALL automatically issue an Attack_Action against the nearest hostile Player_Character within the Turret's Range (10 Tiles) on each Game_Tick.
6. WHEN a Player_Character's Health_Points reach zero, THE Combat_Engine SHALL mark the Player_Character as defeated, award Combat_XP to the attacker, deduct Combat_XP from the defeated Player_Character, and respawn the defeated Player_Character at a designated spawn Tile after a configured delay.
7. WHEN a Building is destroyed by an attacking Player_Character, THE Combat_Engine SHALL award Combat_XP to the attacker proportional to the Building type's maximum HP.
8. IF a Player_Character issues an Attack_Action targeting an entity outside of Range, THEN THE Combat_Engine SHALL reject the action and notify the Player_Character that the target is out of range.
9. THE Combat_Engine SHALL resolve all pending Attack_Actions once per Game_Tick in the order they were received.
10. WHILE a Player_Character is in combat (has issued or received an Attack_Action within the last five Game_Ticks), THE Combat_Engine SHALL prevent the Player_Character from issuing build commands.
11. WHEN a combat action consumes Resources as ammunition (defined by the weapon GameItem's ammo_cost), THE Combat_Engine SHALL deduct the Resource cost from the attacking Player_Character's inventory before applying Damage.
12. IF a Player_Character issues an Attack_Action with a weapon-slot GameItem that has an ammo_cost and the Player_Character lacks sufficient Resources, THEN THE Combat_Engine SHALL reject the action and notify the Player_Character of the missing ammo resources.
13. THE Combat_Engine SHALL prevent a Player_Character from attacking the Player_Character's own Buildings.
14. WHEN a Player_Character attacks another Player_Character, THE Combat_Engine SHALL notify the target Player_Character of the attack, the attacker's name, the GameItem used, and the Damage dealt.
15. WHEN a Player_Character attacks a Building, THE Combat_Engine SHALL notify the Building's owning Player_Character of the attack, the attacker's name, the GameItem used, and the Damage dealt.
16. THE Combat_Engine SHALL read damage_reduction from the target's equipped armor-slot GameItem's stat_modifiers (if any) and subtract it from incoming Damage before applying to Health_Points.
17. WHEN a Player_Character issues an unequip command specifying an Equipment_Slot, THE EquipmentHandler SHALL remove the GameItem from that slot and return it to the Player_Character's inventory.
18. THE EquipmentHandler SHALL enforce that each Equipment_Slot holds at most one GameItem at a time. Equipping a new item to an occupied slot SHALL first unequip the existing item.

### Requirement 7: Rank Progression and Demotion

**User Story:** As a player, I want to earn and lose ranks through combat using an extensive military rank system, so that I can progress through meaningful tiers that unlock and revoke bonuses and technologies.

#### Acceptance Criteria

1. THE Rank_System SHALL define the following Rank levels in ascending order: Recruit, Private, Private First Class, Specialist, Corporal, Sergeant, Staff Sergeant, Sergeant First Class, First Sergeant, Master Sergeant, Command Sergeant Major, Sergeant Major, 2nd Lieutenant, 1st Lieutenant, Captain, Major, Lieutenant Colonel, Colonel, Brigadier General, Major General, Lieutenant General, General.
2. EACH Rank SHALL have a defined Combat_XP threshold, with thresholds strictly increasing across the rank sequence.
3. WHEN a Player_Character's accumulated Combat_XP meets or exceeds the threshold for the next Rank, THE Rank_System SHALL promote the Player_Character to that Rank and notify the Player_Character of the promotion.
4. WHEN a Player_Character is eliminated by another Player_Character, THE Rank_System SHALL deduct Combat_XP from the eliminated Player_Character.
5. IF a Player_Character's Combat_XP falls below the threshold for the Player_Character's current Rank after a deduction, THEN THE Rank_System SHALL demote the Player_Character to the appropriate lower Rank and notify the Player_Character of the demotion.
6. IF a Player_Character is demoted, THEN THE Rank_System SHALL revoke all Technologies and Powerups that required the lost Rank level.
7. WHEN a Player_Character is promoted to a new Rank, THE Rank_System SHALL unlock all Technologies and Powerups associated with that Rank and all lower Ranks.
8. THE Rank_System SHALL store the Player_Character's current Rank and accumulated Combat_XP as persistent Traits on the Player_Character.
9. THE Rank_System SHALL award Combat_XP exclusively through combat actions: defeating a Player_Character, destroying a Building, and dealing Damage to hostile entities.
10. THE Rank_System SHALL display the Player_Character's current Rank, accumulated Combat_XP, and Combat_XP remaining to the next Rank when the Player_Character issues a status command.

### Requirement 8: Technology Unlocks

**User Story:** As a player, I want to research technologies at my Tech_Lab, so that I can gain permanent upgrades and unlock new building types as I rank up.

#### Acceptance Criteria

1. WHEN a Player_Character interacts with a Tech_Lab Building they own, THE Tech_Lab SHALL display a list of Technologies available at the Player_Character's current Rank.
2. WHEN a Player_Character selects a Technology to research, THE Tech_Lab SHALL deduct the required Resources from the Player_Character's inventory and begin a research timer measured in Game_Ticks.
3. WHEN the research timer for a Technology completes, THE Tech_Lab SHALL apply the Technology's effect to the owning Player_Character permanently and notify the Player_Character.
4. IF a Player_Character attempts to research a Technology that requires a higher Rank than the Player_Character's current Rank, THEN THE Tech_Lab SHALL reject the research and notify the Player_Character of the required Rank.
5. IF a Player_Character attempts to research a Technology without sufficient Resources, THEN THE Tech_Lab SHALL reject the research and notify the Player_Character of the missing Resources.
6. THE Tech_Lab SHALL support Technologies that provide stat bonuses (increased Damage, increased HP, increased sight range) and Technologies that unlock new Building types.

### Requirement 9: Powerup System

**User Story:** As a player, I want to activate temporary powerups during combat, so that I can gain a tactical advantage in engagements.

#### Acceptance Criteria

1. THE Powerup system SHALL make Powerups available to a Player_Character based on the Player_Character's current Rank.
2. WHEN a Player_Character activates a Powerup, THE Powerup system SHALL apply the Powerup's effect (e.g., increased Damage, damage reduction, increased movement speed) to the Player_Character for a defined duration measured in Game_Ticks.
3. WHILE a Powerup is active on a Player_Character, THE Powerup system SHALL modify the Player_Character's relevant combat stats for the duration.
4. WHEN a Powerup's duration expires, THE Powerup system SHALL remove the Powerup's effect and restore the Player_Character's stats to pre-activation values.
5. WHEN a Player_Character activates a Powerup, THE Powerup system SHALL place that Powerup on cooldown for a configured number of Game_Ticks, preventing reactivation until the cooldown expires.
6. IF a Player_Character attempts to activate a Powerup that is on cooldown, THEN THE Powerup system SHALL reject the activation and notify the Player_Character of the remaining cooldown duration.
7. IF a Player_Character attempts to activate a Powerup that requires a higher Rank than the Player_Character's current Rank, THEN THE Powerup system SHALL reject the activation and notify the Player_Character of the required Rank.

### Requirement 10: Offline Building Protection

**User Story:** As a player, I want my buildings to be protected when I log out, so that other players cannot destroy my progress while I am offline.

#### Acceptance Criteria

1. WHEN a Player_Character logs out or disconnects, THE Building system SHALL transition all Buildings owned by the Player_Character to the Offline_Building state.
2. WHILE a Building is in the Offline_Building state, THE Building system SHALL prevent all other Player_Characters from dealing Damage to the Offline_Building.
3. WHILE a Building is in the Offline_Building state, THE Building system SHALL prevent all other Player_Characters from entering the Tile occupied by the Offline_Building.
4. WHEN a Player_Character logs in, THE Building system SHALL transition all Offline_Buildings owned by the Player_Character back to the normal Building state.
5. WHILE a Building is in the Offline_Building state, THE Building system SHALL suspend all production (resource, equipment) from that Building.

### Requirement 11: Game Tick and Real-Time Loop

**User Story:** As a player, I want the game world to update in real-time, so that resource production, combat resolution, equipment generation, and building effects happen continuously without manual intervention.

#### Acceptance Criteria

1. THE Game_Tick system SHALL run as a persistent Evennia Script that executes at a configurable interval (default: one tick per second).
2. WHEN a Game_Tick fires, THE Game_Tick system SHALL process the following in order: Resource_Building production for all active Resource_Buildings, Equipment_Building production for all active Equipment_Buildings, Combat_Engine resolution for all pending Attack_Actions, Turret automatic attacks, Powerup duration decrements, Technology research timer decrements, and Resource_Node respawn counter decrements.
3. IF the Game_Tick system encounters an error during processing, THEN THE Game_Tick system SHALL log the error, skip the failed operation, and continue processing remaining operations for that tick.
4. THE Game_Tick system SHALL persist its state across server reloads using Evennia's Script persistence, resuming tick processing after a reload without loss of pending actions.
5. WHILE the game server is running, THE Game_Tick system SHALL maintain a consistent tick rate, processing each tick within the configured interval.

### Requirement 12: Global Notification System

**User Story:** As a player, I want to receive server-wide announcements about significant game events, so that I stay informed about important happenings across the game world.

> **Evennia Integration Note:** Notification delivery uses Evennia's `SESSION_HANDLER` (specifically `evennia.SESSION_HANDLER.all_connected_sessions()`) and `msg()` to broadcast to all connected sessions. No custom broadcast infrastructure is needed.

#### Acceptance Criteria

1. WHEN a Player_Character logs in, THE Notification system SHALL send a Global_Notification to all connected Player_Characters announcing the login, using Evennia's session handler for delivery.
2. WHEN a Player_Character logs out or disconnects, THE Notification system SHALL send a Global_Notification to all connected Player_Characters announcing the departure.
3. WHEN a Player_Character eliminates another Player_Character, THE Notification system SHALL send a Global_Notification to all connected Player_Characters announcing the elimination, including the attacker's name and the eliminated Player_Character's name.
4. WHEN a Player_Character is promoted to a higher Rank, THE Notification system SHALL send a Global_Notification to all connected Player_Characters announcing the promotion and the new Rank.
5. WHEN a Player_Character is demoted to a lower Rank, THE Notification system SHALL send a Global_Notification to all connected Player_Characters announcing the demotion and the new Rank.
6. THE Notification system SHALL deliver each Global_Notification to all connected Player_Characters regardless of the Tile each Player_Character currently occupies, using Evennia's `SESSION_HANDLER` for message delivery.

### Requirement 13: Chat System

**User Story:** As a player, I want to communicate with other players through global chat, private messages, and local room chat, so that I can coordinate, socialize, and strategize.

> **Evennia Integration Note:** This requirement leverages Evennia's existing communication infrastructure rather than building custom chat from scratch:
> - **Local say** → Evennia's built-in `say` command (already in the default command set). No custom implementation needed.
> - **Direct messages** → Evennia's built-in `page` command (supports private messaging between accounts). No custom implementation needed.
> - **Global chat** → A game channel (e.g., "Global") created using Evennia's `Channel` system (`evennia.comms.models.ChannelDB`). Players are auto-subscribed on login.
> - **Custom part**: The only new code is overriding channel message formatting to include the sender's Rank alongside their name.

#### Acceptance Criteria

1. WHEN the game server starts, THE Chat system SHALL ensure a "Global" channel exists using Evennia's Channel system, creating it if necessary.
2. WHEN a Player_Character logs in, THE Chat system SHALL auto-subscribe the Player_Character to the "Global" channel if not already subscribed.
3. WHEN a Player_Character issues a "chat" command with a message, THE Chat system SHALL send the message to the "Global" Evennia Channel, which delivers it to all subscribed Player_Characters regardless of Tile location.
4. THE Chat system SHALL override the "Global" channel's message formatting to include the sending Player_Character's name and Rank in each message (e.g., "[Sergeant] PlayerName: message").
5. WHEN a Player_Character issues a "message" command, THE Chat system SHALL delegate to Evennia's built-in `page` command for Direct_Message delivery, visible only to the sending Player_Character and the target Player_Character.
6. THE Chat system SHALL override Direct_Message formatting to include the sending Player_Character's Rank alongside their name.
7. IF a Player_Character issues a "message" command specifying a target Player_Character name that is not currently online, THEN Evennia's `page` command SHALL inform the sending Player_Character that the target is not online.
8. WHEN a Player_Character issues a "say" command, THE Chat system SHALL delegate to Evennia's built-in `say` command, which broadcasts the message to all Player_Characters on the same Tile.

### Requirement 14: Equipment Buildings and Production

**User Story:** As a player, I want my Armory and Armorer buildings to automatically generate GameItems, so that I can arm and equip myself for combat without manual crafting.

#### Acceptance Criteria

1. WHILE an Armory (AA) Building is active and the owning Player_Character is online, THE Armory SHALL generate a weapon-slot GameItem for the owning Player_Character on each Game_Tick.
2. WHILE an Armorer (AR) Building is active and the owning Player_Character is online, THE Armorer SHALL generate an armor-slot GameItem for the owning Player_Character on each Game_Tick.
3. THE Equipment_Building system SHALL select which GameItem to produce from the item definitions associated with the producing building's abbreviation in the items Definition_File.
4. WHEN an Equipment_Building generates a GameItem, THE Equipment_Building system SHALL add the GameItem to the owning Player_Character's inventory and notify the Player_Character.

### Requirement 15: Resource Node Respawn

**User Story:** As a player, I want depleted resource nodes to regenerate over time, so that gathering remains viable as the game progresses.

#### Acceptance Criteria

1. WHEN a Resource_Node is depleted to zero by manual harvesting, THE Resource_Node SHALL begin a respawn counter measured in Game_Ticks.
2. WHEN the respawn counter reaches zero, THE Resource_Node SHALL restore the gatherable resource to 1 unit.
3. THE respawn counter duration SHALL be configurable (default: 30 Game_Ticks).
4. WHEN a Tile contains a Resource_Building, THE Resource_Node respawn system SHALL NOT apply to that Tile, as the Resource_Building handles production independently.

### Requirement 16: Player Status and Information Commands

**User Story:** As a player, I want to view my current status including rank, resources, combat stats, and equipment, so that I can make informed strategic decisions.

#### Acceptance Criteria

1. WHEN a Player_Character issues a status command, THE command system SHALL display the Player_Character's current Rank, Combat_XP, HP, Resource totals for all resource types (Straw, Clay, Wood, Stone, Iron, Energy, Metals, Circuits), equipped GameItems by Equipment_Slot, and active Powerups.
2. WHEN a Player_Character issues a buildings command, THE command system SHALL display a list of all Buildings owned by the Player_Character, including each Building's type, abbreviation, location (Tile coordinates), current Building_Level (for Resource_Buildings), and current HP.
3. WHEN a Player_Character issues a scan command, THE command system SHALL display all visible entities (Player_Characters, Buildings, Resource_Nodes) within the Player_Character's sight range, including their coordinates and basic information.
4. WHEN a Player_Character issues a technology command, THE command system SHALL display all researched Technologies and all Technologies available at the Player_Character's current Rank.
5. WHEN a Player_Character issues an inventory command, THE command system SHALL display the Player_Character's current Resource quantities for all Resource types and all GameItems organized by Equipment_Slot.

### Requirement 17: Data-Driven Building Definitions

**User Story:** As a game designer, I want building types defined in an external YAML file, so that I can add, remove, or modify buildings without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load building definitions from a YAML Definition_File at a configurable path (defaulting to `data/definitions/buildings.yaml`).
2. EACH building definition SHALL specify: name (string), abbreviation (two-character string used as the map symbol), cost (mapping of resource name to integer), max_health (positive integer), requires_hq (boolean), required_terrain (terrain type string or null), category (one of "headquarters", "resource", "equipment", "defense", "research"), produces (string or null), unlocks (list of building abbreviation strings), and map_symbol (two-character string).
3. WHEN the buildings Definition_File is loaded, THE Schema_Validator SHALL verify that each building definition conforms to the expected schema and that all cross-references (required_terrain, unlocks) resolve to valid entries.
4. IF a building definition references a required_terrain value that does not match any loaded terrain definition, THEN THE Schema_Validator SHALL report a validation error.
5. IF a building definition references an unlock abbreviation that does not exist in the loaded building set, THEN THE Schema_Validator SHALL report a validation error.
6. WHEN the buildings Definition_File is missing, THE Data_Registry SHALL raise an error at startup because building definitions are required for the game to function.
7. ALL game systems that reference building types SHALL read from the Data_Registry rather than hardcoded constants.

### Requirement 18: Data-Driven Item Definitions

**User Story:** As a game designer, I want all item types (weapons, armor, gadgets, consumables) defined in a single external YAML file, so that I can add new item categories, adjust stats, or expand equipment options without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load item definitions from a YAML Definition_File at a configurable path (defaulting to `data/definitions/items.yaml`).
2. EACH item definition SHALL specify: name (string), key (string identifier), slot (string identifying the Equipment_Slot, e.g., "weapon", "armor", "gadget", "consumable"), stat_modifiers (mapping of stat name to numeric value, e.g., damage, damage_reduction, sight_range, move_speed), ammo_cost (mapping of resource name to integer, or null), classification (string: "modern" or "futuristic"), required_rank (rank name string or null), and range (positive integer, for weapon-slot items).
3. THE item definitions SHALL be organized into two sections: a top-level list of all item definitions, and a production_map that maps producing building abbreviations (e.g., "AA", "AR") to lists of item keys that building can produce.
4. WHEN the items Definition_File is loaded, THE Schema_Validator SHALL verify that each item definition conforms to the expected schema, that any required_rank references a valid rank name, that all item keys in production_map reference valid item definitions, and that all producing building abbreviations reference valid building definitions with category "equipment".
5. WHEN the items Definition_File is missing, THE Data_Registry SHALL raise an error at startup.
6. ALL game systems that reference item types SHALL read from the Data_Registry rather than hardcoded constants.
7. ADDING a new item category SHALL require only a new slot value and corresponding stat_modifier keys in the YAML file, with no code changes to the GameItem typeclass or EquipmentHandler.

### Requirement 19: Data-Driven Rank Definitions

**User Story:** As a game designer, I want rank levels and XP thresholds defined in an external YAML file, so that I can adjust progression without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load rank definitions from a YAML Definition_File at a configurable path (defaulting to `data/definitions/ranks.yaml`).
2. EACH rank definition SHALL specify: name (string), level (non-negative integer), xp_threshold (non-negative integer), and unlocks (list of strings referencing technology and powerup keys).
3. WHEN the ranks Definition_File is loaded, THE Schema_Validator SHALL verify that rank levels are unique and that xp_threshold values are strictly increasing with level.
4. WHEN the ranks Definition_File is missing, THE Data_Registry SHALL raise an error at startup.
5. ALL game systems that reference rank levels SHALL read from the Data_Registry rather than hardcoded constants.

### Requirement 20: Data-Driven Technology Definitions

**User Story:** As a game designer, I want technologies defined in an external YAML file, so that I can expand the tech tree without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load technology definitions from a YAML Definition_File at a configurable path (defaulting to `data/definitions/technologies.yaml`).
2. EACH technology definition SHALL specify: name (string), key (string identifier), required_rank (rank name string), resource_cost (mapping of resource name to integer), research_ticks (positive integer), effect_type (one of "stat_bonus", "building_unlock", "item_unlock"), and effect_value (type-specific value describing the bonus or unlock).
3. WHEN the technologies Definition_File is loaded, THE Schema_Validator SHALL verify that each required_rank references a valid rank name and that building_unlock and item_unlock effect_values reference valid building or item definitions.
4. WHEN the technologies Definition_File is missing, THE Data_Registry SHALL raise an error at startup.
5. ALL game systems that reference technologies SHALL read from the Data_Registry rather than hardcoded constants.

### Requirement 21: Data-Driven Terrain and Planet Definitions

**User Story:** As a game designer, I want terrain types, resource mappings, and planet configurations defined in an external YAML file, so that I can add new planet types and terrain without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load terrain and planet definitions from a YAML Definition_File at a configurable path (defaulting to `data/definitions/terrain.yaml`).
2. EACH terrain definition SHALL specify: type (string), resource (string), map_symbol (two-character string), and description (string).
3. EACH planet definition SHALL specify: name (string), terrain_types (list of terrain type strings referencing loaded terrain definitions), and description (string).
4. WHEN the terrain Definition_File is loaded, THE Schema_Validator SHALL verify that all planet terrain_type references resolve to valid terrain definitions.
5. WHEN the terrain Definition_File is missing, THE Data_Registry SHALL raise an error at startup.

### Requirement 22: Data-Driven Powerup Definitions

**User Story:** As a game designer, I want powerups defined in an external YAML file, so that I can add new powerups and adjust their effects without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load powerup definitions from a YAML Definition_File at a configurable path (defaulting to `data/definitions/powerups.yaml`).
2. EACH powerup definition SHALL specify: name (string), key (string identifier), required_rank (rank name string), effect_type (string describing the stat modified), effect_value (numeric modifier), duration_ticks (positive integer), and cooldown_ticks (positive integer).
3. WHEN the powerups Definition_File is loaded, THE Schema_Validator SHALL verify that each required_rank references a valid rank name.
4. WHEN the powerups Definition_File is missing, THE Data_Registry SHALL raise an error at startup.
5. ALL game systems that reference powerups SHALL read from the Data_Registry rather than hardcoded constants.

### Requirement 23: (Merged into Requirement 18)

This requirement has been merged into Requirement 18: Data-Driven Item Definitions. All item types (weapons, armor, gadgets, consumables) and their production mappings are now defined in a single `items.yaml` file.

### Requirement 24: Game Balance Configuration

**User Story:** As a game designer, I want game balance values defined in an external YAML file, so that I can tune gameplay without changing code.

#### Acceptance Criteria

1. THE Data_Registry SHALL load Game_Balance_Config from a YAML file at a configurable path (defaulting to `data/config/balance.yaml`).
2. THE Game_Balance_Config SHALL include: production_scaling (mapping of building level to output amount), turret_damage (integer), turret_radius (integer), xp_building_destruction (integer), xp_player_elimination (integer), xp_elimination_penalty (integer), gather_amount (integer), player_default_health (integer), resource_respawn_ticks (integer), and combat_lockout_ticks (integer).
3. WHEN the Game_Balance_Config file is missing, THE Data_Registry SHALL use hardcoded default values and log a warning.
4. WHEN the Game_Balance_Config file contains an invalid value, THE Data_Registry SHALL raise a descriptive validation error at startup.
5. ALL game systems that reference balance values SHALL read from the Data_Registry rather than hardcoded constants.

### Requirement 25: Centralized Data Registry

**User Story:** As a developer, I want a single registry that holds all loaded game definitions, so that game systems can access building types, weapons, ranks, and other definitions from one place without importing hardcoded constants.

#### Acceptance Criteria

1. THE Data_Registry SHALL provide a centralized object that holds all loaded definitions (buildings, items, ranks, technologies, powerups, terrain, planets) and configuration (balance).
2. WHEN the game starts up, THE Data_Registry SHALL load and validate all Definition_Files and configuration before any game system is initialized.
3. THE Data_Registry SHALL be injectable into game systems (Building system, Combat_Engine, Rank_System, Powerup system, Tech_Lab, Equipment_Building system) replacing direct imports of hardcoded constants.
4. IF any required Definition_File fails validation, THEN THE Data_Registry SHALL prevent game startup and report all validation errors.

### Requirement 26: Hot-Reload of Definition Files

**User Story:** As a server operator, I want to reload game definition files at runtime without restarting the server, so that balance changes and new content can be applied immediately.

#### Acceptance Criteria

1. THE game SHALL provide a `@reloaddata` admin command (restricted to Builder+ permission, distinct from Evennia's `@reload`) that triggers a Hot_Reload of all Definition_Files.
2. WHEN a Hot_Reload is triggered, THE Data_Registry SHALL re-read and re-validate all Definition_Files from disk.
3. IF all Definition_Files pass validation, THE Data_Registry SHALL replace the current registry contents with the newly loaded data and log a success message.
4. IF any Definition_File fails validation during Hot_Reload, THE Data_Registry SHALL reject the entire reload, keep the current data intact, and report the validation errors to the operator who issued the command.

### Requirement 27: Presentation-Agnostic Architecture

**User Story:** As a developer, I want game logic separated from presentation rendering, so that a graphical user interface can be added alongside the telnet client in the future without rewriting core systems.

#### Acceptance Criteria

1. ALL game systems (Combat_Engine, Building system, Rank_System, Resource system, Powerup system) SHALL expose their state and actions through a presentation-agnostic interface that does not assume a text-based client.
2. THE Overworld map rendering, room descriptions, and status displays SHALL be implemented as separate presentation layers that consume game state data, rather than being embedded in game logic.
3. THE game SHALL communicate state changes to clients using Evennia's msg() system with structured data (kwargs/tags), so that different client types (telnet, web, future GUI) can render the same game events in their own format.
4. THE ASCII map renderer SHALL be one implementation of a map presentation interface, allowing a graphical renderer to be added as an alternative without modifying the underlying map or tile systems.

### Requirement 28: Event Bus

**User Story:** As a developer, I want game systems to communicate through a publish-subscribe event bus, so that adding new reactions to game events doesn't require modifying existing systems.

#### Acceptance Criteria

1. THE game SHALL provide an Event Bus that supports publishing named events with arbitrary data payloads using Evennia's Signal system.
2. THE Event Bus SHALL support subscribing handler functions to specific event names.
3. THE following events SHALL be published: player_login, player_logout, player_moved, player_eliminated, building_constructed, building_destroyed, building_upgraded, rank_promoted, rank_demoted, combat_action, powerup_activated, powerup_expired, technology_researched, resource_gathered, and tick_completed.
4. THE Global_Notification system, Combat_XP awards, Technology_Tree evaluation, and Offline_Building state transitions SHALL be implemented as Event Bus subscribers rather than direct method calls within the triggering system.
5. NEW game features SHALL be addable by subscribing to existing events without modifying the systems that publish those events.

### Requirement 29: Structured Logging

**User Story:** As a server operator, I want structured log output for game events, so that logs can be easily parsed, searched, and aggregated by monitoring tools.

#### Acceptance Criteria

1. THE game SHALL log the following events with structured context fields (player name, coordinates, target, values): player login, player logout, command executed, combat action, building constructed, building destroyed, building upgraded, rank change, resource gathered, technology researched, and server lifecycle events (startup, shutdown, tick error).
2. EACH log entry SHALL include: timestamp, log level, logger name, event type, and relevant context fields as key-value pairs.
3. THE game SHALL use Python's standard logging module configured through Evennia's logging settings, supporting both human-readable and structured JSON output formats.

### Requirement 30: Server Metrics

**User Story:** As a server operator, I want to monitor server health metrics, so that I can detect performance issues and track usage patterns.

#### Acceptance Criteria

1. THE game SHALL track the following metrics: connected_players (gauge), commands_processed (counter), tick_duration_ms (per-tick timing), combat_actions (counter), buildings_constructed (counter), and errors (counter).
2. THE game SHALL log a metrics summary at a configurable interval (default: 60 seconds) when metrics are enabled.
3. THE metrics system SHALL be lightweight and SHALL NOT add more than 1ms of overhead per Game_Tick.
4. THE metrics_enabled setting SHALL be configurable in the Game_Balance_Config (default: false).

### Requirement 31: World Chunking

**User Story:** As a server operator, I want the server to only process active regions of the world during each tick, so that large worlds don't cause tick slowdowns.

#### Acceptance Criteria

1. THE game SHALL divide each Planet's Overworld into rectangular World_Chunks of configurable size (default: 10x10 Tiles per chunk).
2. A World_Chunk SHALL be considered active if at least one online Player_Character is located within the chunk or within one chunk radius of it.
3. DURING each Game_Tick, THE game SHALL only process Resource_Building production, Equipment_Building production, and Turret attacks for Tiles within active World_Chunks.
4. WHEN a Player_Character moves into an inactive World_Chunk, THE game SHALL activate that chunk and its neighbors.
5. THE World_Chunk size SHALL be configurable in the Game_Balance_Config.

### Requirement 32: Periodic Auto-Save

**User Story:** As a server operator, I want player and world state saved periodically, so that a server crash doesn't lose significant progress.

#### Acceptance Criteria

1. THE game SHALL auto-save all connected Player_Character states at a configurable interval (default: every 30 Game_Ticks).
2. THE auto-save SHALL run asynchronously and SHALL NOT block Game_Tick processing.
3. IF an auto-save fails, THE game SHALL log the error and retry on the next save interval.
4. THE save interval SHALL be configurable in the Game_Balance_Config.

### Requirement 33: Admin Commands

**User Story:** As a server operator, I want game-specific administrative commands to manage gameplay data and resources, so that I can handle operational tasks that Evennia's built-in admin commands don't cover.

> **Evennia Integration Note:** Evennia already provides a comprehensive set of admin commands that cover common moderation tasks. The following built-in commands should be used directly — no custom reimplementation needed:
> - **`@tel`/`@teleport`** — Move objects/players to any location (supports coordinates, room names, quiet mode)
> - **`@boot`** — Kick/disconnect accounts (supports reason message, quiet mode)
> - **`@examine`/`@ex`** — Inspect objects with full attribute details
> - **`@perm`** — Set/remove permissions on accounts
> - **`@ban`/`@unban`** — Ban/unban accounts
> - **`@wall`** — Broadcast to all connected sessions
> - **`@give`** — Give objects to characters (for item-based giving)
> - **`@force`** — Force an object to execute a command
>
> Evennia's permission hierarchy (Player → Helper → Builder → Admin → Developer) is used for access control. No custom "trust level" system is needed.

#### Acceptance Criteria

1. THE game SHALL provide a `@reloaddata` admin command that triggers a Hot_Reload of all Definition_Files as defined in Requirement 26. This is distinct from Evennia's `@reload` (which restarts server code).
2. THE game SHALL provide a `@giveresource` admin command that adds a specified quantity of a Resource to a target Player_Character's inventory. This is distinct from Evennia's `@give` (which transfers objects, not resource trait counters).
3. ALL game-specific admin commands SHALL be restricted to Evennia accounts with Builder permission or higher, using Evennia's `perm()` lock function.
4. ALL game-specific admin command executions SHALL be logged with the operator name, command issued, and target.
5. FOR teleporting players, kicking/disconnecting players, inspecting player details, and banning accounts, server operators SHALL use Evennia's built-in `@tel`, `@boot`, `@examine`, and `@ban` commands respectively.
