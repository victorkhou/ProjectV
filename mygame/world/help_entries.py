"""
File-based help entries. These complement command-based help and help entries
added in the database using the `sethelp` command in-game.

Control where Evennia reads these entries with `settings.FILE_HELP_ENTRY_MODULES`,
which is a list of python-paths to modules to read.

A module like this should hold a global `HELP_ENTRY_DICTS` list, containing
dicts that each represent a help entry. If no `HELP_ENTRY_DICTS` variable is
given, all top-level variables that are dicts in the module are read as help
entries.

Each dict is on the form
::

    {'key': <str>,
     'text': <str>}``     # the actual help text. Can contain # subtopic sections
     'category': <str>,   # optional, otherwise settings.DEFAULT_HELP_CATEGORY
     'aliases': <list>,   # optional
     'locks': <str>       # optional, 'view' controls seeing in help index, 'read'
                          #           if the entry can be read. If 'view' is unset,
                          #           'read' is used for the index. If unset, everyone
                          #           can read/view the entry.

Authoring style: follow ``mygame/HELP_STYLE.md`` — bold title, plain intro,
``#`` section headings, commands in ``|w..|n`` and game nouns in ``|c..|n``,
and a ``# See Also`` cross-link block at the end of every topic. Keep content
in sync with the real data files (buildings.yaml, items.yaml, agent roles).

Do NOT hard-wrap prose. Write each paragraph (and each ``# See Also`` block) as
a single physical line — the client wraps it to whatever width the reader's
screen allows, so a manual break only produces ragged output on narrow panels.
Keep a newline only where the structure needs one: blank lines between
paragraphs, ``#`` headings, and one-item-per-line lists (resource rows, command
rows, building rows, etc.).

"""

HELP_ENTRY_DICTS = [
    # ----------------------------------------------------------------- #
    #  Onboarding
    # ----------------------------------------------------------------- #
    {
        "key": "tutorial",
        "aliases": ["new", "start", "getting started", "newbie", "beginner"],
        "category": "Game",
        "text": """
            |wWelcome, Commander.|n

            You've been dropped on |cTerra|n with a handful of resources and a mission: build a base, train agents, arm yourself, and expand across the galaxy. This is a real-time strategy game — the world keeps ticking whether you act or not. Here's how to get started.

            # Step 1 — Find Your Spot

            Type |wmap|n to see the terrain around you. Look for tiles with resources: |gForest|n (|G&&|n) gives |cWood|n, |wRock|n (|w##|n) gives |cStone|n, and |WMountain|n (|W/\\|n) gives |cIron|n.

            Set up near at least two resource types. Move with |wnorth|n, |wsouth|n, |weast|n, |wwest|n (or just |wn|n, |ws|n, |we|n, |ww|n).

            # Step 2 — Build a Headquarters

            On a good tile, type |wbuild HQ|n and stay put while it builds. Your |cHeadquarters|n is your home base and respawn point — everything else requires it. See |whelp buildings|n.

            # Step 3 — Set Up Extractors

            An |cExtractor|n multiplies harvesting on a resource tile. Walk to a Forest or Rock tile and |wbuild EX|n. Build two if you can.

            # Step 4 — Harvest

            Stand on a resource tile and type |wharvest|n. You gather while you stay put — much faster on an Extractor. See |whelp resources|n.

            # Step 5 — Train an Agent

            Build an |cAcademy|n (|wbuild AC|n), step inside, and type |wagent train|n. Agents are NPC workers that scale your base.

            # Step 6 — Put It to Work

            Walk to an Extractor and type |wagent assign 2|n (use the id from |wagent list|n). It harvests on its own from now on. See |whelp agents|n.

            # Step 7 — Arm Yourself

            Build an |cArmory|n (|wbuild AR|n) to produce weapons and armor, or a |cMedbay|n (|wbuild MB|n) for medkits. |wequip|n gear, |wreload|n weapons, and check your loadout with |wequipment|n. See |whelp equipment|n and |whelp combat|n.

            # What's Next

            With passive income flowing, expand: |wupgrade|n buildings (costs grow fast), stockpile surplus in a |cVault|n (|whelp storage|n), explore, climb the levels and ranks to unlock new planets and buildings (|whelp level|n), and pick a |clab|n to research a technology tree (|whelp technology|n).

            # Follow the Directives

            You don't have to remember these steps — your |cdirectives|n checklist guides you through them one at a time and rewards each. Type |wdirectives|n to see your current objective. See |whelp directives|n.

            # See Also

            |whelp commands|n · |whelp directives|n · |whelp level|n · |whelp resources|n · |whelp buildings|n · |whelp agents|n · |whelp equipment|n · |whelp combat|n · |whelp outposts|n · |whelp storage|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Levels, ranks & progression
    # ----------------------------------------------------------------- #
    {
        "key": "level",
        "aliases": ["rank", "ranks", "levels", "progression", "xp",
                    "experience", "leveling", "levelling", "promotion"],
        "category": "Game",
        "text": """
            |wLevels, Ranks & Progression|n

            You have a single |cLevel|n from |c1|n to |c100|n. Every bit of XP you earn raises it, and your |cRank|n — your title, from |cRecruit|n up to |cMarshal|n — is decided by which level you've reached. Check both any time with |wscore|n.

            # Earning XP

            XP comes from two kinds of activity, both feeding the same level bar:

            Base-building — the early game. Modest but renewable; carries a new commander to roughly |clevel 8-9|n:
            Construction complete — |c+30|n XP
            Upgrade complete — |c+30|n XP
            Agent trained — |c+40|n XP
            Manual harvest — |c+1|n XP per yield

            Combat — the long game. Where levels past the early game come from; raiding |coutposts|n is the reliable source (|whelp outposts|n):
            Defeating a player — |c+100|n XP
            Destroying a building — |c+50|n XP
            Destroying an enemy HQ — |c+300|n XP
            Losing a fight — |c-50|n XP (can drop your level)

            Your rank never falls even if your level does — see Ranks below.

            # The Curve

            Each level costs more XP than the last, so early levels come fast and later ones are long-term goals. Reaching |clevel 2|n takes 40 XP; |clevel 6|n about 300; |clevel 11|n about 1,000; |clevel 20|n about 6,200; |clevel 100|n over a million. |wscore|n shows your current XP and how much remains to the next level.

            # Ranks

            Your rank is a band of levels — climb into a new band and you're promoted:

            |cRecruit|n — levels 1-5
            |cPrivate|n — 6-10
            |cCorporal|n — 11-15
            |cSergeant|n — 16-21
            |cStaff Sergeant|n — 22-28
            |cLieutenant|n — 29-36
            |cCaptain|n — 37-45
            |cMajor|n — 46-56
            |cColonel|n — 57-69
            |cBrigadier|n — 70-84
            |cGeneral|n — 85-99
            |cMarshal|n — 100 (the capstone)

            Rank is a high-water mark: once earned it sticks, so a bad losing streak that lowers your level never demotes your title.

            # What Levels & Ranks Unlock

            Buildings gate on level — higher-tier structures unlock as you climb. |wbuild|n with no argument lists what you can build now; |whelp buildings|n shows the full tier list. Some buildings also need a deed (|whelp outposts|n).
            Agent capacity rises with rank — more agents under your command. Your agents can't out-level you: an agent's effective level is capped at yours (|whelp agents|n).
            New planets open at rank thresholds — |cForge|n at Staff Sergeant, |cTundra|n/|cSpace|n at Captain, |cInferno|n at Colonel, |cCitadel|n at General.
            Technologies gate on rank and lab ownership — each of four labs hosts one tech tree. Earliest techs open at Corporal, stronger ones spaced upward (|whelp technology|n).

            # See Also

            |whelp score|n · |whelp directives|n · |whelp buildings|n · |whelp agents|n · |whelp outposts|n · |whelp combat|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Directives — the onboarding checklist
    # ----------------------------------------------------------------- #
    {
        "key": "directives",
        "aliases": ["directive", "objectives", "objective", "checklist",
                    "quests", "tasks", "goals"],
        "category": "Game",
        "text": """
            |wDirectives|n

            Directives are a guided checklist that walks you through your first hour, one objective at a time, and pays out a reward — XP and sometimes resources — as you complete each. They're the fastest way to learn the game while getting a head start.

            # How They Work

            You always have one current objective. Do the thing it asks — build your HQ, set up an Extractor, train an agent, and so on — and it completes automatically, rewards you, and advances to the next. There's nothing to "accept" or "turn in"; just play and the checklist keeps up.

            # Commands

            |wdirectives|n — show your current objective and what you've already done
            |wdirectives off|n — dismiss the checklist (you |rforfeit remaining rewards|n)
            |wdirectives on|n — turn it back on from where you left off

            Alias: |wobjectives|n.

            # If You Dismiss Them

            Turning directives |woff|n silences the prompts and stops the reward payouts — you still advance in the background, but you won't be paid for objectives completed while off, and there's no back-pay if you turn them on again. Leave them on until you know the ropes.

            # See Also

            |whelp tutorial|n · |whelp level|n · |whelp commands|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Command index
    # ----------------------------------------------------------------- #
    {
        "key": "commands",
        "aliases": ["command list", "command", "cmds"],
        "category": "Game",
        "text": """
            |wCommand Reference|n

            A map of what you can do. Type |whelp <command>|n for full detail on any one (e.g. |whelp build|n). Typing any unambiguous prefix works too — |wsco|n runs |wscore|n, |weq|n runs |wequipment|n.

            # Moving & Looking

            |wmove <dir>|n / |wn|n |ws|n |we|n |ww|n — move one tile
            |wlook|n (|wl|n) — look at your tile / a thing
            |wmap|n (|wm|n) — the fog-of-war overworld map
            |wscan|n — who and what is on your tile
            |wenter|n / |wleave|n — step into / out of a building

            # Economy & Base

            |wharvest|n — gather the resource under you
            |wbuild <type>|n — construct a building (bare |wbuild|n lists types)
            |wupgrade|n — upgrade the building you're on
            |wrepair|n — restore a damaged building's HP for resources
            |wdemolish|n — tear down for a partial refund
            |wbuildings|n (|wbl|n) — list your buildings
            |wdeposit|n / |wwithdraw|n — move resources to/from storage
            |wget <obj>|n — pick up something on your tile

            # Combat & Equipment

            |wattack <target>|n (|wa|n) — attack a player, building, or agent
            |wtarget <enemy>|n (|wlock|n) — lock a ranged weapon onto an enemy
            |wshoot|n (|wfire|n) — fire ranged: at a locked target, or a direction
            |wcraft <item>|n — make gear/ammo at an Armory, Research Lab, or Medbay
            |winsert <item>|n — permanently mod your equipped weapon (at your Blacksmith)
            |wreroll <item>|n — re-roll an item's base stats (at your Blacksmith)
            |wsalvage <item>|n — break an item into Salvage (at your Blacksmith)
            |wrefine <resource>|n — convert resources into Salvage (at your Refinery)
            |wequip <item>|n / |wunequip <slot>|n — manage worn gear (or |wall|n)
            |wequipment|n (|weq|n) — your full loadout (paperdoll)
            |wuse <item>|n — use a consumable (medkit, stim)
            |wset <bomb> <sec>|n — set a bomb's fuse (or |wset all <sec>|n)
            |wthrow <grenade> <n/s/e/w>|n — throw a grenade in a direction
            |warm <mine>|n — arm a mine where you stand
            |wreload|n — refill your ranged weapon's magazine

            # Agents

            |wagent list|n — your roster
            |wagent train|n — train a new agent (inside an Academy)
            |wagent assign <id> [role]|n — put an agent to work
            (see |whelp agents|n for the rest)

            # Progression & Info

            |wscore|n (|wst|n) — full character sheet: level, rank, XP, combat timer
            |wdirectives|n — your onboarding checklist and its rewards
            |winventory|n (|wi|n) — resources, gear, supplies, carry weight
            |wtechnology|n / |wresearch <tech>|n — the tech tree
            |wpowerup <key>|n — activate a powerup

            # Social

            |wsay <msg>|n — speak to your tile
            |wchat <msg>|n — the public channel
            |wmessage <player> <msg>|n — private message
            |wwho|n — who's online

            # See Also

            |whelp tutorial|n · |whelp level|n · |whelp directives|n · |whelp buildings|n · |whelp equipment|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Resources
    # ----------------------------------------------------------------- #
    {
        "key": "resources",
        "aliases": ["resource", "gathering", "harvesting guide"],
        "category": "Game",
        "text": """
            |wResources|n

            Six resources fuel everything you build, research, and fire. Where you find each depends on the planet you're on.

            |cWood|n — Forest terrain. Most early buildings.
            |cStone|n — Rock and Permafrost. Walls and defenses.
            |cIron|n — Mountain, Scrapyard, and rocky terrain.
            |cEnergy|n — Power Grids and Magma Vents. Mid-game tech.
            |cCircuits|n — Circuit Fields and Control Rooms. Advanced tech.
            |cNexium|n — only Citadel Vault Rooms. Endgame material.

            # Gathering

            Stand on a resource tile and type |wharvest|n — you gather every few seconds while you stay put. Building an |cExtractor|n (|wbuild EX|n) on the tile multiplies the yield; assigning a |charvester|n agent to it makes it fully automatic.

            # Carrying & Storing

            Everything you carry has weight, and you can only carry so much — resources are light but not free. Stockpile the overflow in a |cVault|n or your |cHQ|n with |wdeposit|n, and pull it back with |wwithdraw|n. See |whelp storage|n.

            # Tips

            Terra has Wood, Stone, and Iron — enough to start. Energy and Circuits await on Forge. Plan your base around what you need most.

            # See Also

            |whelp storage|n · |whelp buildings|n · |whelp agents|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Agents
    # ----------------------------------------------------------------- #
    {
        "key": "agents",
        "aliases": ["agent guide", "agent help"],
        "category": "Game",
        "text": """
            |wAgents|n

            Agents are NPC workers you train and assign. They're how you scale past what you can do by hand. All agent actions go through the |wagent|n command — type |wagent|n alone to see its subcommands.

            # Training

            Build an |cAcademy|n (|wbuild AC|n), step inside, and type |wagent train|n. Each agent costs more than the last, and training takes time. Watch progress with |wagent list|n.

            # Roles

            Assign an agent |winside|n a building and its role is chosen for you; or name an army role explicitly.

            |cHarvester|n — at an |cExtractor|n: passive resource income
            |cEngineer|n — at an |cArmory|n or any |clab|n: builds/researches
            |cGuard|n — army role, assignable anywhere: auto-defense and patrol combat
            |cScout|n — army role, assignable anywhere: patrols and reveals the map within its vision radius

            # Key Commands

            |wagent list|n — your roster and ids
            |wagent assign <id>|n — assign by the building you're standing in
            |wagent assign <id> <role>|n — assign to a named army role
            |wagent unassign <id>|n — send the agent back to HQ
            |wagent patrol <id> <x,y> …|n — set a guard/scout patrol route
            |wagent ability <id> [<key> on || off]|n — view/toggle gated abilities

            # Abilities

            Some agents unlock abilities at higher levels. |cdelivery|n lets a harvester haul from its Extractor to a Vault/HQ on its own — enable it with |wagent ability <id> delivery on|n once the agent qualifies.

            # Agent Cap

            Your rank sets how many agents you can command, so climbing ranks grows your workforce (|whelp level|n). A demotion puts the excess into reserve — they keep working but can't be reassigned until you rank back up. An agent also can't out-level you: its effective level is capped at your own.

            # See Also

            |whelp level|n · |whelp buildings|n · |whelp resources|n · |whelp tutorial|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Buildings
    # ----------------------------------------------------------------- #
    {
        "key": "buildings",
        "aliases": ["building guide", "building help", "construction"],
        "category": "Game",
        "text": """
            |wBuildings|n

            Buildings are your base. Each type has a purpose, most need your |cHeadquarters|n first, and all upgrade to level 5.

            # Building & Upgrading

            |wbuild <type>|n — construct a building (by abbreviation like |wEX|n or full name like |wextractor|n). Stay on the tile or assign an |cEngineer|n. |wbuild|n alone lists available types.
            |wupgrade|n — improve the building you're on; costs and times climb steeply. Building goes offline until done. Pause by stepping away; resume with |wupgrade|n again; cancel with |wupgrade cancel|n (full refund).
            |wrepair|n — restore a damaged building over time. Stay on the tile or assign an |cEngineer|n. Each tick restores 5% HP and costs 5% of total investment. Buildings don't self-heal; one knocked offline comes back online as soon as healing starts.
            |wdemolish|n — tear down for a partial refund: 40% at L1, 80% at L5.

            # Building Types

            Each line: |wABBR|n |cName|n — purpose (unlocks at level N).

            |wHQ|n |cHeadquarters|n — home base, respawn point, holds storage. Required before most other buildings. (L1)
            |wEX|n |cExtractor|n — boosts harvesting; must sit on resource terrain. Harvester agents work here. (L1)
            |wAC|n |cAcademy|n — train agents here (|wagent train|n inside). (L1)
            |wAR|n |cArmory|n — crafts weapons, armor, and ammo. (L3)
            |wWL|n |cWall|n — a barrier that blocks passage. (L2)
            |wBK|n |cBarracks|n — army capacity. (L7, requires deed: destroy an outpost)
            |wLB|n |cResearch Lab|n — hosts the Research tech tree and crafts advanced gear; needs an Engineer. (L11, requires deed: destroy 3 outposts)
            |wWX|n |cWeapons Lab|n — hosts the Weapons tech tree (damage, range, gear quality). (L11, requires deed: destroy 3 outposts)
            |wDF|n |cDefense Lab|n — hosts the Defense tech tree (building HP, armor). (L11, requires deed: destroy 3 outposts)
            |wRX|n |cResource Lab|n — hosts the Resource tech tree (production, build cost, salvage). (L11, requires deed: destroy 3 outposts)
            |wRD|n |cRadar|n — extends vision. (L9)
            |wTU|n |cTurret|n — auto-attacks enemies in range while your HQ stands. (L5)
            |wVT|n |cVault|n — high-capacity resource storage, protected while you're offline; harvesters prefer to deliver here. (L4)
            |wRL|n |cRelay|n — boosts nearby Turret damage. (L15)
            |wSG|n |cShield Generator|n — projects a regenerating shield onto nearby buildings. (L15, max 4 per planet)
            |wMB|n |cMedbay|n — crafts medkits and stims; reduces respawn time. (L18)
            |wWT|n |cWatchtower|n — +sight range while you stand on it. (L7)
            |wSN|n |cSniper Nest|n — +weapon range while you stand on it. (L9)
            |wFH|n |cField Hospital|n — heals you over time while you stand on it. (L10)
            |wBS|n |cBlacksmith|n — the gear bench: insert, reroll, salvage. (L11, requires deed: destroy 3 outposts)
            |wRF|n |cRefinery|n — converts surplus resources into Salvage. (L13, requires deed: destroy a fortress)
            |wMP|n |cMunitions Plant|n — the bomb works: crafts every grenade and mine. (L6)
            |wSA|n |cSurvey Array|n — triangulates enemy outpost locations on this planet. (L6)

            Higher-tier buildings unlock as you gain levels; a few also require a deed (Barracks needs one destroyed outpost, the four labs each need three). You may own only one lab per planet — the four labs each host a different technology tree (|whelp technology|n). Check |wscore|n for your current level, |wbuild|n to see what's available now, and |whelp level|n for the full progression picture.

            # Per-Building Guides

            Every building has its own help topic with costs, level, dependencies, and examples: |whelp hq|n · |whelp extractor|n · |whelp academy|n · |whelp armory|n · |whelp wall|n · |whelp barracks|n · |whelp lab|n · |whelp weapons lab|n · |whelp defense lab|n · |whelp resource lab|n · |whelp radar|n · |whelp turret|n · |whelp vault|n · |whelp relay|n · |whelp shield|n · |whelp medbay|n · |whelp watchtower|n · |whelp sniper nest|n · |whelp field hospital|n · |whelp blacksmith|n · |whelp refinery|n · |whelp munitions plant|n · |whelp survey array|n.

            # See Also

            |whelp resources|n · |whelp agents|n · |whelp storage|n · |whelp equipment|n · |whelp craft|n · |whelp outposts|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Equipment
    # ----------------------------------------------------------------- #
    {
        "key": "equipment",
        "aliases": ["gear", "equip guide", "weapons", "armor", "items"],
        "category": "Game",
        "text": """
            |wEquipment|n

            Gear makes you tougher and deadlier. You have twelve equipment slots total: armor and utility slots, one melee weapon slot, one ranged weapon slot, and one accessory slot.

            # Getting Gear

            Build an |cArmory|n (|wbuild AR|n) for weapons, armor, and ammo, a |cResearch Lab|n (|wbuild LB|n) for advanced gear, or a |cMedbay|n (|wbuild MB|n) for medkits and stims. Two ways to get items from them: stand in the building and |wcraft <item>|n to make one instantly for resources, or assign an |cEngineer|n agent and it crafts the same catalog passively while you're away (see |whelp craft|n). Gear also drops as |cloot|n from enemy bases and guards — dropped gear is rolled (two copies of the same item differ) and can carry a rarity and affixes; see |whelp loot|n. Made gear lands in your inventory; pick up dropped items with |wget|n.

            # Slots

            Armor/utility: head, eyes, face, torso, arms, hands, legs, feet, back
            Melee weapon: weapon_melee — used by |wattack|n
            Ranged weapon: weapon_ranged — used by |wtarget|n / |wshoot|n / |wreload|n
            Accessory: one utility slot (scope, hauler pack)

            One item per slot; equipping a new one swaps out the old. Melee and ranged weapons auto-equip to their respective slots, so you carry one of each at once.

            # Stat Bonuses

            Armor: reduces incoming damage, stacks across all worn slots.
            Weapons: each attack uses only that slot's weapon + shared non-weapon bonuses. Your other equipped weapon never contributes its damage or affixes.
            Other stats: move speed, sight range, carry capacity, max HP. Passive bonuses from armor and accessories stack.
            Max-HP gear raises your health ceiling — equipping adds headroom (doesn't heal), removing lowers the ceiling and trims any HP above the new max.

            |wequipment|n shows shared bonuses and separate effective melee/ranged damage.

            Looted and crafted gear is rolled — a quality tag like |c[Rare · 73%]|n on the name tells you how good this copy is, and |wlook <item>|n shows each stat's roll. See |whelp loot|n and |whelp rarity|n.

            # Wearing Gear

            |wequip <item>|n — wear an item from your inventory (a partial name works, e.g. |wequip assault|n). Alias: |wwear|n.
            |wequip all|n — wear every piece of carried gear at once
            |wunequip <item>|n — take off gear by name (|wunequip assault|n) or by slot (|wunequip head|n). Alias: |wremove|n.
            |wunequip all|n — take off everything
            |wequipment|n (|weq|n) — full paperdoll: every slot, its item, stat bonuses, ranged ammo, passive totals, and separate melee/ranged damage

            Powerful gear may require a |crank|n — |wequip|n tells you if you're not high enough.

            # Consumables & Bombs

            These live in your supply bag (counted, not slotted):
            |wuse medkit|n — restore health
            |wuse combat_stim|n — a temporary combat buff
            |wset <bomb> <sec>|n then |wthrow <grenade> <dir>|n / |warm <mine>|n — fused area explosives (|whelp bombs|n)

            # Carry Weight

            Every item and resource has weight, and you can carry only so much. Equipped gear is free — it's worn, not hauled — but supplies and resources on you count. A |chauler pack|n raises your limit. See |whelp storage|n. |winventory|n shows your current load.

            # See Also

            |whelp combat|n · |whelp loot|n · |whelp storage|n · |whelp buildings|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Combat
    # ----------------------------------------------------------------- #
    {
        "key": "combat",
        "aliases": ["combat guide", "fighting", "attack guide", "ammo"],
        "category": "Game",
        "text": """
            |wCombat|n

            Combat is real-time. A hit uses the selected weapon's power, shared non-weapon bonuses, and that weapon's own affixes, minus the target's armor. Your other equipped weapon contributes nothing to that attack.

            # Attacking

            |wattack <target>|n (|wa|n) — melee-attack a player, building, or agent with your equipped melee weapon. Melee only reaches a foe on your same tile (close in first — an adjacent enemy is not in melee reach). Buildings can be meleed from an adjacent tile. To fight at range, use |wtarget|n/|wshoot|n below — melee and ranged live in separate slots, so you carry both at once. Equip a weapon first (|whelp equipment|n).

            # Ranged: Target & Shoot

            With a ranged weapon you can fight at a distance in two ways:
            |wtarget <enemy>|n (|wlock|n) — lock onto an enemy in range. Takes a few ticks (faster with better gear). |rHold still while it locks — moving breaks it.|n Once locked, shots are far more accurate (|c90%|n baseline) and track the target until they leave range or you move.
            |wshoot|n (|wfire|n) — fire your ranged weapon. With a lock, plain |wshoot|n fires at the locked target. Without one, |wshoot <n/s/e/w>|n fires in a direction and hits the first thing in line at lower accuracy (|c70%|n baseline). A directional shot breaches cover — it damages buildings (open or closed) in the line of fire, and if you're inside a building it fires at the structure around you, letting you shoot your way out. Every shot spends ammo whether it hits or misses.

            # Timing: Instant vs. Ticked

            Your own |wattack|n, directional |wshoot|n, and locked-target |wshoot|n resolve instantly — the hit lands the moment you act, throttled by a short per-weapon cooldown (you'll be told if you fire again too soon). |cTurrets|n and guards resolve on the world tick instead: that tiny delay is their dodge window and preserves automated-combat ordering.

            # The Combat State

            Dealing or taking damage puts you |rin combat|n for a short time — you'll get a |r[Combat]|n notice when it starts, and |wscore|n shows the seconds remaining. Each new hit resets the timer. While in combat you can't slip through your own |cWalls|n, you can't manually |wenter|n or |wleave|n a building, and moving is slower (better move speed gear eases this). It clears on its own once the timer runs out.

            # Friendly Fire

            You can attack your own things — your buildings and your own agents — as well as other players. There's no XP or benefit for hitting your own (you can't farm yourself), and it still puts you in the combat state, but it's allowed (handy to clear a misplaced building). Take care with area attacks: a |cgrenade|n hits everything in the blast, friend or foe.

            # Ammo & Reloading

            Ranged weapons feed ammo one of two ways:
            Resource ammo: most weapons (like the |cassault rifle|n) spend resources per shot (Iron, Energy, etc.) — no reload needed, just keep the resource stocked.
            Magazine weapons: some (like the |cservice rifle|n) fire from a loaded magazine and run dry. |wreload|n refills from matching ammo in your supply bag (craft it at an |cArmory|n or |cResearch Lab|n). |wequipment|n shows the loaded count.

            # Armor & Defense

            Every armor piece you |wequip|n reduces incoming damage, and they stack across all slots. Some weapons deal typed damage that lingers — |cfire|n burns and |cpoison|n keeps hurting for a few ticks after the hit (|whelp poison|n); matching resist gear and affixes blunt them. |cTurrets|n auto-attack intruders; |cWalls|n block movement. A |cVault|n protects your stored resources while you're offline. A |cShield Generator|n wraps nearby buildings in a regenerating shield that soaks damage before their HP (|whelp shield|n). You and your agents heal over time, but buildings do not — repair a damaged building with |wrepair|n (see |whelp buildings|n).

            # Buildings as Cover

            Closed building = full ranged cover (occupant can't be shot inside).
            Open building = no ranged cover (you can still be shot inside).
            Directional |wshoot|n breaches any building — the round hits the structure, not the occupant. This is how you knock down a |cWall|n or sealed structure from range.
            Melee always requires same tile regardless of cover.

            # Guards

            A |cGuard|n agent (or |cSoldier|n) automatically attacks any enemy that comes within range each tick — so assigning one actually defends your base. Melee guards must be on your same tile to strike, so they chase onto it to reach you; ranged soldiers reach several tiles out. This cuts both ways: enemy |coutpost|n and |cfortress|n guards attack you the same way when you raid them (|whelp outposts|n).

            # Destroying a Base

            Destroying an owner's |cHeadquarters|n is decisive. Wreck an enemy base's HQ and the whole base is eliminated at once — every building and guard is wiped and loot drops on the spot: |g[Combat] Outpost eliminated! +X XP. Loot dropped at (x,y).|n Lose your own HQ (in PvP) and nothing is deleted — your base goes |rinert|n instead: turrets stop, production halts, agents idle, and building commands are refused until you |wbuild|n a new HQ.

            # Bombs: Grenades & Mines

            Bombs are fused explosives — set a fuse first with |wset <bomb> <seconds>|n (or |wset all <seconds>|n for your whole inventory), then deploy. A grenade is |wthrow|n-n in a direction (|wthrow frag_grenade n|n): it flies until it hits the first obstacle or its max range, lands, and ticks down before exploding. A mine is |warm|n-ed in place (|warm land_mine|n): it ticks down where you stand. Anyone on a bomb's tile sees it |rtick|n. The blast hits everything in radius — enemies, your own units, and |ryou|n if you're too close — so mind the fuse and your distance. See |whelp bombs|n. Bombs come from a |cMunitions Plant|n.

            # Death & Recovery

            Death is costly:
            You lose all equipped gear, supplies, and resources on your person.
            Resources in storage (|cHQ|n / |cVault|n) are safe — death strips you, not your base.
            A |cRespawn Beacon|n (|wbuild RB|n) salvages a fraction: |c55%|n at L1, up to |c95%|n at L5. Upgrade it to keep more.
            No beacon on the planet you died on = total loss.
            |wcollect|n (|wrecover|n) at the beacon retrieves salvaged gear and resources up to carry weight (the rest waits in the beacon).
            Death also costs XP.

            # After a Fight

            When you fall you redeploy from the staging area — choose your |cRespawn Beacon|n (where your recovered loadout waits), your |cHQ|n, your place of death, or a random tile (|whelp spawning|n). A |cMedbay|n shortens respawn time. Winning awards XP toward your next |clevel|n — combat is the main source of levels past the early game (|whelp level|n).

            # See Also

            |whelp level|n · |whelp equipment|n · |whelp poison|n · |whelp bombs|n · |whelp outposts|n · |whelp agents|n · |whelp buildings|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Spawning & the staging area (lobby lifecycle)
    # ----------------------------------------------------------------- #
    {
        "key": "spawning",
        "aliases": ["spawn", "deploy", "lobby", "staging", "class", "classes"],
        "category": "Game",
        "text": """
            |wDeploying into the Game|n

            Before you enter the field you prepare in a staging area: choose a class and a spawn point, then deploy. It's a short numbered wizard — just |wtype the number|n of your choice at each step. You are not yet in the world while staging — you can't move, build, or fight until you |wenter|n.

            # Step 1 — Choose a Class

            A numbered list of classes is shown; type its |wnumber|n (e.g. |w1|n) to pick one. Your class is a chosen identity shown on your |wscore|n and in |wwho|n. (You can also type |wclass <name>|n — a name or unambiguous prefix like |wvan|n for Vanguard.)

            # Step 2 — Choose a Spawn Point

            Next, the currently available spawn points appear under fixed numbers. Hidden choices leave gaps, so a number never changes meaning:
            |w1|n. |cRespawn Beacon|n — deploy at your owned beacon, where recovered gear waits.
            |w2|n. |cHeadquarters|n — deploy at your live HQ.
            |w3|n. Place of death — deploy where you last died.
            |w4|n. Random location — deploy at a random tile in |wopen ground|n, well clear of any building.
            Unavailable choices are omitted. If a selected HQ, beacon, or death tile disappears before you enter, deployment is cancelled and you are asked to choose again — it never silently changes to random. (|wspawn <where>|n also works.)

            # Enter the Game

            Once a class and spawn point are set, a final menu appears: type |w1|n to enter the world at your chosen point, or |w0|n to quit. (|wenter|n / |wdeploy|n and |wquit|n also work.)

            # Quitting & Reconnecting

            |wquit|n works in two levels: from the game it pulls you back to the |wstaging area|n (you stay connected — re-deploy from the menu, and you land |wright back where you left off|n, NOT a re-rolled spawn); from the staging area it disconnects. You |wcan't quit the field while in combat|n (see |wscore|n for your timer) — the anti-combat-log rule. If your connection |rdrops|n without |wquit|n, your character lingers in the world briefly (still a target) before being pulled back to staging — so don't rely on pulling the plug to escape a fight.

            # Dying

            When you're defeated you return here and re-run the whole wizard — |wpick a class again|n, then an available spawn point (|cRespawn Beacon|n / |cHQ|n / place of death / random). You re-enter at full health with a |wcleared combat timer|n.

            # See Also

            |whelp combat|n · |whelp headquarters|n · |whelp commands|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Bombs — grenades & mines (fused explosives)
    # ----------------------------------------------------------------- #
    {
        "key": "bombs",
        "aliases": ["bomb", "grenade", "grenades", "mine", "mines", "fuse"],
        "category": "Game",
        "text": """
            |wBombs: Grenades & Mines|n

            Bombs are fused area explosives. There are two families: |cgrenades|n (thrown) and |cmines|n (placed) — with variants of each (e.g. |cFrag|n and |cPlasma|n grenades; |cLand|n and |cProximity|n mines). Both come from a |cMunitions Plant|n (|wbuild MP|n, then |wcraft|n one — or assign an Engineer to make them while you're away).

            # Set the Fuse First

            You must set a fuse before you throw or arm — |wset <bomb> <seconds>|n. This arms every unit of that bomb you carry, so |wset frag_grenade 3|n with 3 grenades lets you throw all 3 at a 3s fuse. The fuse is clamped to that bomb's min/max (grenades short, mines longer). |wset all <seconds>|n arms every bomb in your inventory at once (each clamped to its own limits). Each throw/arm consumes one armed fuse — re-set once you've deployed them all, or to change the timer.

            # Grenades — throw in a direction

            |wthrow <grenade> <n/s/e/w>|n (alias |wth|n) hurls the grenade in a compass direction. It flies until it hits the first obstacle or reaches its max range, then lands and the fuse ticks down before it explodes. It lands just in front of a building (the blast then breaches the wall from outside), on a unit's tile if it hits someone, or at max range on a clear line. You can't pick a grenade up once it's away.

            # Mines — arm in place

            |warm <mine>|n plants the mine on your current tile and starts its fuse. A mine can't be thrown, and once armed it can't be picked up — it ticks down where you left it. Good as a timed trap on a chokepoint.

            # Ticking & the Blast

            Everyone standing on a bomb's tile sees it |rtick|n each second (and sees a grenade land or a mine arm). When the fuse reaches zero it explodes: everything within the blast radius takes flat damage minus armor — |renemies, your own agents and buildings, and you|n if you're in range. A blast breaches cover: unlike a gunshot, it damages buildings whether open or closed and reaches players sheltered inside them, so mind your distance. Kills you cause credit you.

            # Disarming

            |wdisarm|n (alias |wdis|n) works on a ticking bomb on your current tile to neutralize it. It's not instant — it takes |c2-10 ticks|n, and the bomb's fuse keeps ticking the whole time, so a short-fuse bomb can explode before you finish (the fuse wins the race). When the attempt resolves it's a base |c70%|n chance to succeed (the bomb is removed, no blast); on |rfailure it detonates immediately|n. Technology, equipment, and your class improve the success chance and shorten the work later.

            # See Also

            |whelp combat|n · |whelp equipment|n · |whelp buildings|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Outposts & Fortresses (PvE NPC bases)
    # ----------------------------------------------------------------- #
    {
        "key": "outposts",
        "aliases": ["outpost", "fortress", "fortresses", "raid", "raiding",
                    "enemy base", "enemy bases", "npc bases"],
        "category": "Game",
        "text": """
            |wOutposts & Fortresses|n

            Enemy bases are scattered across the map — clusters of enemy buildings defended by guards. Raid them for XP and loot: it's the reason to gear up and go on the offensive, even with no other players around.

            # Two Tiers

            |cOutpost|n — small: an enemy Headquarters plus a building or two and one or two melee guards. Soloable at low rank.
            |cFortress|n — large: an HQ with Walls, Turrets, and an Armory, defended by three to five mixed melee and ranged guards. Bring a higher rank and good gear.

            # Finding Them

            Explore with |wmove|n and watch your |wmap|n — enemy structures and units show up in |rred|n (your own are cyan), so a cluster of red buildings is a base. Stand near one and |wscan|n to list what's on the tiles around you; enemy buildings and guards are tagged |R[Enemy]|n.

            Wandering is the free way. The deliberate way is a |cSurvey Array|n (|wbuild SA|n): |wsurvey scan|n triangulates one outpost on your current planet that isn't on your map yet, then you close in on it with sweeps and bearings until its tile is marked permanently. See |whelp survey|n.

            # Raiding

            Clear the guards, dodge or destroy the |cTurrets|n, breach the |cWalls|n, and destroy the enemy |cHeadquarters|n. Guards fight back and turrets auto-fire, so bring armor, ammo, and medkits (|whelp equipment|n, |whelp combat|n). Guards you kill stay dead. Destroying the |cHQ|n eliminates the entire base at once: |g[Combat] Outpost eliminated! +X XP. Loot dropped at (x,y).|n Pick up the loot with |wget|n.

            # Respawns

            Cleared bases respawn elsewhere after a while, so there's always something to raid — at rising difficulty as you climb the ranks.

            # See Also

            |whelp survey|n · |whelp combat|n · |whelp equipment|n · |whelp buildings|n · |whelp agents|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Survey Array — outpost triangulation
    # ----------------------------------------------------------------- #
    {
        "key": "survey",
        "aliases": ["surveying", "triangulate", "triangulation", "recon"],
        "category": "Game",
        "text": """
            |wSurveying: Finding Outposts|n

            A |cSurvey Array|n turns base hunting from wandering into a search you actually run. It never simply hands over coordinates — it gives you a search area and two ways to close in on what's inside it. Build one with |wbuild SA|n, then stand in it (it must be online and not mid-upgrade).

            # Open a Search

            |wsurvey scan|n locks onto one enemy base on your current planet that isn't on your map yet, and reports a rectangular search area guaranteed to contain it. Any tier is fair game — the array names what it found, so an |cOutpost|n and a |cFortress|n read differently and you can decide whether you want that fight. The base sits at a random spot inside the area — the centre is not the answer — and each scan picks its target at random, so no two searches play out the same way. If every base on the planet is already mapped, the array tells you the planet is swept and charges you nothing.

            Only one search runs at a time, on any planet. Finish it, or |wsurvey abandon|n it — the array will never quietly throw away readings you paid for. If the base you're tracking is razed by someone else while you hunt it, the search closes and tells you so rather than charging for readings against nothing.

            |wsurvey|n on its own re-shows the area you're tracking. That readout is free; everything else costs resources.

            # Close In

            |wsurvey narrow|n buys a sweep that shrinks the area to roughly a quarter of its size, re-placed around the target. It's the blunt tool: reliable, and it always works.

            |wsurvey <x> <y>|n takes a reading from a tile inside the area and reports a compass bearing toward the base plus a rough distance — "very close", "far off", and so on. It's the cheapest action and the one that rewards thinking: a single bearing gives you a direction, and two readings from different tiles cross to a much smaller pocket than either alone.

            # Pinpoint It

            Probe close enough to the base — or narrow the area down to a single tile — and it's pinpointed: you're given the exact coordinates and the tile is remembered permanently, in enemy red, even though you've never laid eyes on it. Your |wmap|n only draws the area around you, so the mark appears once you're within map range of it — the coordinates are what you travel by. Then go raid it (|whelp outposts|n).

            |wsurvey abandon|n drops the search with no refund, freeing you to |wscan|n for a different target.

            # Array Level

            Upgrading the array tightens the opening area, so a maxed array starts you where a level 1 array would need several sweeps to reach. That's the whole benefit — the sweeps and probes themselves cost the same at every level.

            # See Also

            |whelp survey array|n · |whelp outposts|n · |whelp buildings|n · |whelp map|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Storage & carry weight
    # ----------------------------------------------------------------- #
    {
        "key": "storage",
        "aliases": ["carry weight", "carry", "weight", "deposit", "vault"],
        "category": "Game",
        "text": """
            |wStorage & Carry Weight|n

            You can carry a lot, but not an unlimited amount. Storage buildings let you stockpile far more than you can hold on your person.

            # Carry Weight

            Every item and resource has weight. What you carry on your person — loose resources and supplies (ammo, medkits, grenades) — counts toward your carry limit. Equipped gear does |wnot|n count; it's worn. |winventory|n and |wscore|n show your current weight against your limit. A |chauler pack|n (accessory) raises the limit.

            If your pack is full when resources come in (from harvesting or a delivery agent), the overflow drops on the ground rather than being lost — pick it up with |wget|n once you've made room.

            # Storage Buildings

            Your |cHeadquarters|n has storage from the start, and a |cVault|n (|wbuild VT|n) holds much more and is protected while you're offline. Stand on the building and:

            |wdeposit <resource> [<amount> || all]|n — move from you into storage
            |wwithdraw <resource> [<amount> || all]|n — take from storage back to you

            With no amount (or |wall|n), deposit moves everything you hold and withdraw takes as much as fits under your carry limit. You can only use storage you own.

            # Examples

            |wdeposit iron 100|n — bank 100 Iron
            |wdeposit wood|n — bank all your Wood
            |wwithdraw energy all|n — take all the Energy that fits

            # See Also

            |whelp resources|n · |whelp buildings|n · |whelp equipment|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Crafting
    # ----------------------------------------------------------------- #
    {
        "key": "craft",
        "aliases": ["crafting", "make", "craft guide", "production"],
        "category": "Game",
        "text": """
            |wCrafting|n

            You make your own gear, ammo, and supplies at production buildings — by hand for what you need now, or by an assigned agent that produces it for you over time.

            # Where to Craft

            Each production building makes a different set of items. Stand on your own building (or |wenter|n it) to craft there:

            |cArmory|n (|wbuild AR|n) — modern weapons, armor, ammo
            |cResearch Lab|n (|wbuild LB|n) — futuristic gear (and the Research tech tree)
            |cMedbay|n (|wbuild MB|n) — medkits and combat stims
            |cMunitions Plant|n (|wbuild MP|n) — every grenade and mine

            # Crafting by Hand

            Stand in the building and type |wcraft|n with no argument to list what it makes and each item's resource cost. Then |wcraft <item>|n makes one instantly, spending the resources from your stockpile.

            # Letting Agents Craft

            Assign an |cEngineer|n to an Armory or Research Lab (|wagent assign <id>|n while inside) and it crafts items on its own over time, paying the same resource cost from your stockpile — the hands-off way to stock up while you do other things. That asynchronous work is the whole point of agents.

            # What You Get

            Gear (weapons, armor, accessories) goes into your inventory — |wequip|n it or see it with |winventory|n. Supplies (ammo, medkits, stims, grenades) go into your supply bag — |wuse|n or |wreload|n them. Powerful items may need a minimum |crank|n.

            # Crafting Quality

            Crafted gear is rolled like loot, but in a tighter, more reliable band — a craft is dependable where a drop gambles (|whelp loot|n). The craft message shows the result's value: its quality tag, e.g. |c[73%]|n. A higher-level crafting building can even land a rarity — up to a |c5%|n chance of |cRare|n at level 5 — and the |cMaster Gunsmithing|n research raises the floor of every roll you craft. Crafted gear |wnever|n carries affixes; those are loot-only (|whelp affixes|n).

            # Examples

            |wcraft|n — list this building's items and costs
            |wcraft assault_rifle|n — make one Assault Rifle
            |wcraft medkit|n — make one Medkit (at a Medbay)

            # See Also

            |whelp equipment|n · |whelp loot|n · |whelp armory|n · |whelp lab|n · |whelp medbay|n · |whelp agents|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Loot & item quality (item-loot-economy)
    # ----------------------------------------------------------------- #
    {
        "key": "loot",
        "aliases": ["loot guide", "quality", "item quality", "iqs",
                    "rolled stats", "item rolls"],
        "category": "Game",
        "text": """
            |wLoot & Item Quality|n

            Gear that drops as loot is rolled: each stat is drawn fresh from that item's roll band, so two copies of the same item are rarely equal — one Assault Rifle hits harder, another shoots further. Hunting a great roll is the point.

            # The Quality Score

            A rolled item carries a quality tag on its name — |c[73%]|n — summing how good its rolls are. Rarity shows in the same tag (|c[Rare · 73%]|n) and colors the name (|whelp rarity|n). Affix bonuses add to the score, so a great Legendary can read above 100%. Unrolled things (ammo, consumables, fixed starter gear) show no tag — they're identical by design.

            # Inspecting an Item

            |wlook <item>|n shows the full picture: each stat as rolled (min–max) so you can see where in the band this copy landed, plus any affixes. |wequipment|n and |winventory|n show the tags at a glance.

            # Where Rolls Come From

            Enemy-base loot and guard kills drop rolled gear — the source decides the rarity odds (|whelp rarity|n, |whelp outposts|n). Crafting rolls too, in a tighter, reliable band (|whelp craft|n). Top rolls are rare — most land low in the band — which is why the |cBlacksmith|n reroll bench exists (|whelp blacksmith|n). And gear is loseable power: in PvP, an item that drops on death keeps its rolls, so your god-roll is something someone else can take.

            # See Also

            |whelp rarity|n · |whelp affixes|n · |whelp blacksmith|n · |whelp craft|n · |whelp equipment|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Rarity tiers (item-loot-economy)
    # ----------------------------------------------------------------- #
    {
        "key": "rarity",
        "aliases": ["rarities", "common", "uncommon", "rare", "epic",
                    "legendary"],
        "category": "Game",
        "text": """
            |wRarity|n

            Looted gear rolls a rarity tier that colors its name and quality tag: |wCommon|n, |gUncommon|n, |cRare|n, |mEpic|n, |yLegendary|n.

            # What Rarity Gives

            Two things. |cBetter base rolls|n — each tier above Uncommon guarantees a higher floor inside the item's roll bands, so a Legendary can never roll near the bottom. |cMore affixes|n — Common 0, Uncommon 1, Rare 2, Epic 3, Legendary 4 bonus properties (|whelp affixes|n).

            # Where the Tiers Drop

            The source shifts the odds: guard kills are mostly Common with a sliver of Rare; |cOutposts|n reach Epic; |cStrongholds|n and |cFortresses|n drop Rares and Epics regularly with a taste of Legendary; |cCitadels|n are where Epics and Legendaries actually live. Raid up the ladder for better colors (|whelp outposts|n).

            # Crafted Gear

            Crafting caps at |cRare|n — a higher-level crafting building has a small chance of one (up to 5% at level 5) — and crafted gear never carries affixes. Epic and above come from loot only (|whelp craft|n).

            # Gear Is Loseable Power

            Rarity doesn't bind: gear that drops when a player dies keeps its rolls, rarity, and affixes — the winner picks up exactly what was lost.

            # See Also

            |whelp loot|n · |whelp affixes|n · |whelp outposts|n · |whelp craft|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Affixes (item-loot-economy)
    # ----------------------------------------------------------------- #
    {
        "key": "affixes",
        "aliases": ["affix", "item affixes"],
        "category": "Game",
        "text": """
            |wAffixes|n

            Affixes are bonus properties on looted gear, shown as a name suffix — an Assault Rifle |cof Power|n hits harder, |cof Reach|n shoots further, |cof the Viper|n poisons what it hits.

            # How They Roll

            An item's |crarity|n sets how many affixes it draws (Common 0 up to Legendary 4), each rolled from a pool matching the item. Weapons draw things like |cof Power|n (+damage), |cof Reach|n (+range), |cof the Viper|n (a |cpoison|n proc on every landed hit — |whelp poison|n), and typed-resist wards; armor draws |cof the Bulwark|n (+armor) and resists like |cof Antitoxin|n (poison) or |cof Ashes|n (fire). No duplicates on one item.

            # Loot-Only

            Affixes come from loot only — crafted gear never has them, which is why raid drops can beat the craft bench. Affix values add to the item's quality tag, so an affixed item can read above 100%.

            # Permanent

            Affixes are part of the item: the Blacksmith |wreroll|n bench re-rolls only base stats and leaves affixes (and rarity) untouched, and salvaging destroys them with the item.

            # See Also

            |whelp loot|n · |whelp rarity|n · |whelp poison|n · |whelp blacksmith|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Poison damage type (item-loot-economy)
    # ----------------------------------------------------------------- #
    {
        "key": "poison",
        "aliases": ["poison damage", "venom", "toxin", "damage over time"],
        "category": "Game",
        "text": """
            |wPoison|n

            Poison is a damage type that keeps hurting after the hit lands: a poisoned target takes extra damage each tick for a few ticks — roughly half the hit again, spread over four ticks.

            # Poisoning Your Enemies

            Two sources: a |cVenom Coating|n insert applied to your equipped weapon at the |cBlacksmith|n converts its damage to poison (|whelp insert|n), and a looted weapon |cof the Viper|n adds a poison proc to every landed hit (|whelp affixes|n). The |cToxicology|n research makes your poison tick a quarter harder.

            # Countering It

            Poison resist gear and affixes (|cof Antitoxin|n) blunt the poisoned hit itself, and the lingering ticks are light enough that natural regeneration and a |cmedkit|n out-heal them — heal through it rather than panic.

            # See Also

            |whelp combat|n · |whelp affixes|n · |whelp blacksmith|n · |whelp equipment|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Salvage currency (item-loot-economy)
    # ----------------------------------------------------------------- #
    {
        "key": "salvage currency",
        "aliases": ["currency"],
        "category": "Game",
        "text": """
            |wSalvage (Currency)|n

            Salvage is the currency of the gear economy: weightless scrap that pays for Blacksmith work. Your balance shows on |wscore|n alongside your resources.

            # Earning It

            |wsalvage <item>|n at your |cBlacksmith|n breaks an unwanted item into Salvage — the better the item's quality and the higher the bench's level, the more it pays. |wrefine <resource> [<amount> || all]|n at your |cRefinery|n burns surplus resources (Nexium included) into Salvage at a level-scaled rate.

            # Spending It

            A Blacksmith |wreroll|n costs Salvage plus a little Iron — the chase for a god roll is what the currency is for. The |cSalvage Protocols|n research cuts the reroll charge by a quarter.

            # See Also

            |whelp blacksmith|n · |whelp refinery|n · |whelp loot|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Technologies — the research tree at a glance
    # ----------------------------------------------------------------- #
    {
        "key": "technologies",
        "aliases": ["techs", "tech tree", "research guide"],
        "category": "Game",
        "text": """
            |wTechnologies|n

            Research permanent bonuses at a lab. There are four technology trees, each hosted by its own lab, and you may own only one lab (one tree) per planet — so the lab you build commits that planet to a tree. |wtechnology|n shows the tree your lab hosts and your progress; |wresearch <tech>|n starts one (an |cEngineer|n drives it). Each tech is gated by |crank|n and paid in resources.

            To research a different tree, |wdemolish|n your lab and build the one that hosts it — you can't have two labs on the same planet.

            # Weapons Tree — |cWeapons Lab (WX)|n

            |cField Marksmanship|n — +5 damage (Corporal).
            |cAdvanced Weapons|n — +10 damage (Lieutenant).
            |cToxicology|n — poison ticks 25% harder (Captain, |whelp poison|n).
            |cBallistics Optimization|n — +1 weapon range (Major).
            |cMunitions Refinement|n — +1 more weapon range (Colonel).
            |cMaster Gunsmithing|n — crafted gear rolls with a raised floor (Colonel, |whelp craft|n).

            # Defense Tree — |cDefense Lab (DF)|n

            |cReinforced Walls|n — buildings gain +50 HP (Corporal).
            |cImproved Armor|n — +5 armor (Sergeant).
            |cAblative Plating|n — +3 more armor (Lieutenant).
            |cStructural Bracing|n — more building HP (Captain).
            |cReactive Plating|n — +3 more armor, up to the cap (Major).

            # Resource Tree — |cResource Lab (RX)|n

            |cPrefab Logistics|n — cheaper build/upgrade costs (Sergeant).
            |cEfficient Construction|n — build and upgrade costs cut 15% (Lieutenant).
            |cRapid Production|n — production buildings work 1.5x faster (Captain).
            |cSalvage Protocols|n — Blacksmith rerolls cost 25% less (Captain, |whelp blacksmith|n).
            |cAutomated Fabrication|n — even faster production (Colonel).

            # Research Tree — |cResearch Lab (LB)|n

            |cForest Warfare|n — move faster through Forest (Sergeant).
            |cExtended Range|n — +2 sight range (Staff Sergeant).
            |cMountain Surveying|n — +2 vision on Mountains (Staff Sergeant).
            |cRuin Fortification|n — +2 defense in Ruins (Lieutenant).
            |cGlacier Traversal|n — move faster on Glaciers and Frozen Lakes (Captain).

            Permanent damage and armor bonuses from research (and alliance perks) are capped — past the cap, gear is where power grows, and gear is loseable (|whelp loot|n).

            # See Also

            |whelp lab|n · |whelp weapons lab|n · |whelp defense lab|n · |whelp resource lab|n · |whelp level|n · |whelp craft|n
        """,
    },
    # ================================================================= #
    #  Per-building guides (one topic per building type)
    # ================================================================= #
    {
        "key": "headquarters",
        "aliases": ["hq", "hq building"],
        "category": "Buildings",
        "text": """
            |wHeadquarters (HQ)|n

            Your home base and the anchor of everything you build. It's your respawn point, holds your first block of storage, and must exist before you can raise most other buildings.

            # Build Requirements

            Cost:
              Wood - |c10|n
              Stone - |c10|n
              Iron - |c10|n

            Requirements:
              Player - |clevel 1|n
              Dependencies - none (the HQ is the one building you raise with no prerequisites)

            Limit: one HQ per planet.

            # What It Does

            Acts as your spawn/respawn point, provides |c200|n base storage (|wdeposit|n / |wwithdraw|n here), and unlocks the rest of your base. Losing a fight sends you back here.

            # Using It

            Stand on a good central tile and |wbuild HQ|n. Then |wdeposit iron all|n to bank surplus, or |wwithdraw wood 50|n to pull some back.

            # See Also

            |whelp buildings|n · |whelp storage|n · |whelp extractor|n
        """,
    },
    {
        "key": "extractor",
        "aliases": ["ex", "extractor building"],
        "category": "Buildings",
        "text": """
            |wExtractor (EX)|n

            A resource pump. Built on a resource tile, it multiplies what you harvest there — and a |cHarvester|n agent can work it for you automatically.

            # Build Requirements

            Cost:
              Wood - |c15|n
              Stone - |c10|n

            Requirements:
              Player - |clevel 1|n
              HQ - |crequired|n
              Terrain - resource tile (Forest, Rock, Mountain, etc. — |wmap|n shows which tiles yield what)

            # What It Does

            Boosts the harvest yield of the tile it stands on. Assign a |cHarvester|n agent and it produces passively while you do other things; a |cdelivery|n-enabled harvester even hauls the output to your Vault/HQ.

            # Using It

            Walk onto a resource tile and |wbuild EX|n. Harvest by hand with |wharvest|n, or |wagent assign <id>|n inside it to automate. See |whelp resources|n and |whelp agents|n.

            # See Also

            |whelp resources|n · |whelp agents|n · |whelp buildings|n
        """,
    },
    {
        "key": "academy",
        "aliases": ["ac", "academy building"],
        "category": "Buildings",
        "text": """
            |wAcademy (AC)|n

            Where you train |cagents|n — the NPC workers and soldiers that scale your base beyond what you can do by hand.

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c15|n
              Iron - |c10|n

            Requirements:
              Player - |clevel 1|n
              HQ - |crequired|n

            # What It Does

            Trains new agents. Each agent costs more than the last and takes time to train; your rank caps how many you can command at once.

            # Using It

            |wbuild AC|n, step inside (|wenter|n), then |wagent train|n. Watch progress with |wagent list|n, and once trained, |wagent assign <id>|n to put them to work. See |whelp agents|n.

            # See Also

            |whelp agents|n · |whelp buildings|n
        """,
    },
    {
        "key": "armory",
        "aliases": ["ar", "armory building"],
        "category": "Buildings",
        "text": """
            |wArmory (AR)|n

            Your modern-gear workshop: weapons, armor, and ammunition. Craft items by hand here, or assign an |cEngineer|n to churn them out passively.

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c15|n
              Iron - |c15|n

            Requirements:
              Player - |clevel 3|n
              HQ - |crequired|n

            # What It Does

            Produces modern gear (crafting spends resources per item):

            Crafts:
              Weapons - |ccombat knife|n, |cassault rifle|n, |csniper rifle|n, |cservice rifle|n
              Ammo - |crifle rounds|n
              Armor - helmet, vest, gloves, greaves, boots
              Accessories - |cscope|n, |chauler pack|n

            # Using It

            Stand on it (or |wenter|n) and type |wcraft|n to list what it makes and each cost, then |wcraft assault_rifle|n to make one instantly. Assign an |cEngineer|n (|wagent assign <id>|n inside) and it crafts the same items on its own from your resources while you're away. Made gear lands in your inventory — |wequip|n it.

            # See Also

            |whelp craft|n · |whelp equipment|n · |whelp lab|n · |whelp medbay|n
        """,
    },
    {
        "key": "wall",
        "aliases": ["wl", "wall building"],
        "category": "Buildings",
        "text": """
            |wWall (WL)|n

            A cheap, tough barrier that blocks movement — the backbone of base defense and choke points.

            # Build Requirements

            Cost:
              Stone - |c5|n

            Requirements:
              Player - |clevel 2|n
              HQ - |crequired|n

            # What It Does

            Blocks passage through its tile for everyone. High HP (600) makes it a durable shield for the buildings behind it. Combine with |cTurrets|n to funnel attackers into kill zones.

            # Using It

            |wbuild WL|n on the tile you want to seal. Tear it down later with |wdemolish|n if you need the path back.

            # See Also

            |whelp combat|n · |whelp turret|n · |whelp buildings|n
        """,
    },
    {
        "key": "barracks",
        "aliases": ["bk", "barracks building"],
        "category": "Buildings",
        "text": """
            |wBarracks (BK)|n

            Military housing that raises how large an army you can field.

            # Build Requirements

            Cost:
              Wood - |c15|n
              Stone - |c15|n
              Iron - |c10|n

            Requirements:
              Player - |clevel 7|n
              HQ - |crequired|n
              Deed - destroy an |coutpost|n

            # What It Does

            Increases your army capacity (|cGuard|n / |cScout|n agents). Build one to grow your fighting force.

            # Using It

            |wbuild BK|n near your base. Train agents at an |cAcademy|n and assign them army roles. See |whelp agents|n.

            # See Also

            |whelp agents|n · |whelp combat|n · |whelp buildings|n
        """,
    },
    {
        "key": "lab",
        "aliases": ["lb", "lab building", "laboratory", "research lab"],
        "category": "Buildings",
        "text": """
            |wResearch Lab (LB)|n

            One of the four specialized labs. It hosts the |cResearch|n technology tree (vision, terrain, and utility techs) and crafts your most advanced gear.

            The other three labs host the other trees: |cWeapons Lab (WX)|n, |cDefense Lab (DF)|n, and |cResource Lab (RX)|n. You may own only one lab per planet, so building this one commits the planet to the Research tree — |wdemolish|n it to switch.

            # Build Requirements

            Cost:
              Wood - |c25|n
              Stone - |c20|n
              Iron - |c15|n

            Requirements:
              Player - |clevel 11|n
              HQ - |crequired|n
              Deed - destroy |c3 outposts|n
              Agent - an |cEngineer|n to run research

            # What It Does

            Researches the |cResearch|n tree (|wresearch <tech>|n) — Extended Range, Forest Warfare, Mountain Surveying, Ruin Fortification, Glacier Traversal — and crafts futuristic gear. An |cEngineer|n drives research and passive crafting.

            Crafts:
              Weapons - |cplasma rifle|n
              Armor - |cpower armor|n
              Accessories - |cjetpack|n
              Ammo - |cenergy cell|n
              Throwables - |cfrag grenade|n

            # Using It

            |wbuild LB|n, assign an |cEngineer|n (|wagent assign <id>|n inside), then |wtechnology|n to see the tree and |wresearch <tech>|n to start one. Craft gear with |wcraft|n / |wcraft plasma_rifle|n. See |whelp craft|n.

            # See Also

            |whelp technology|n · |whelp weapons lab|n · |whelp defense lab|n · |whelp resource lab|n · |whelp craft|n
        """,
    },
    {
        "key": "weapons lab",
        "aliases": ["wx", "weapons lab building", "weapon lab"],
        "category": "Buildings",
        "text": """
            |wWeapons Lab (WX)|n

            The lab that hosts the |cWeapons|n technology tree — offense: weapon damage and range, plus crafted-gear quality. One of the four specialized labs; you may own only one lab per planet, so building this commits the planet to the Weapons tree (|wdemolish|n to switch).

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c15|n
              Iron - |c25|n

            Requirements:
              Player - |clevel 11|n
              HQ - |crequired|n
              Deed - destroy |c3 outposts|n
              Agent - an |cEngineer|n to run research

            # What It Does

            Researches the |cWeapons|n tree: |cField Marksmanship|n and |cAdvanced Weapons|n (+damage), |cBallistics Optimization|n and |cMunitions Refinement|n (+range), |cToxicology|n (harder poison), and |cMaster Gunsmithing|n (better crafted gear).

            # Using It

            |wbuild WX|n, assign an |cEngineer|n (|wagent assign <id>|n inside), then |wtechnology|n and |wresearch <tech>|n.

            # See Also

            |whelp technology|n · |whelp lab|n · |whelp defense lab|n · |whelp resource lab|n
        """,
    },
    {
        "key": "defense lab",
        "aliases": ["df", "defense lab building"],
        "category": "Buildings",
        "text": """
            |wDefense Lab (DF)|n

            The lab that hosts the |cDefense|n technology tree — survivability: building HP and armor / damage reduction. One of the four specialized labs; you may own only one lab per planet, so building this commits the planet to the Defense tree (|wdemolish|n to switch).

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c25|n
              Iron - |c15|n

            Requirements:
              Player - |clevel 11|n
              HQ - |crequired|n
              Deed - destroy |c3 outposts|n
              Agent - an |cEngineer|n to run research

            # What It Does

            Researches the |cDefense|n tree: |cReinforced Walls|n and |cStructural Bracing|n (+building HP), and |cImproved Armor|n, |cAblative Plating|n, and |cReactive Plating|n (+armor, up to the cap).

            # Using It

            |wbuild DF|n, assign an |cEngineer|n (|wagent assign <id>|n inside), then |wtechnology|n and |wresearch <tech>|n.

            # See Also

            |whelp technology|n · |whelp lab|n · |whelp weapons lab|n · |whelp resource lab|n
        """,
    },
    {
        "key": "resource lab",
        "aliases": ["rx", "resource lab building"],
        "category": "Buildings",
        "text": """
            |wResource Lab (RX)|n

            The lab that hosts the |cResource|n technology tree — economy: production speed, build cost, and salvage efficiency. One of the four specialized labs; you may own only one lab per planet, so building this commits the planet to the Resource tree (|wdemolish|n to switch).

            # Build Requirements

            Cost:
              Wood - |c25|n
              Stone - |c20|n
              Iron - |c15|n

            Requirements:
              Player - |clevel 11|n
              HQ - |crequired|n
              Deed - destroy |c3 outposts|n
              Agent - an |cEngineer|n to run research

            # What It Does

            Researches the |cResource|n tree: |cPrefab Logistics|n and |cEfficient Construction|n (cheaper builds), |cRapid Production|n and |cAutomated Fabrication|n (faster production), and |cSalvage Protocols|n (cheaper Blacksmith rerolls).

            # Using It

            |wbuild RX|n, assign an |cEngineer|n (|wagent assign <id>|n inside), then |wtechnology|n and |wresearch <tech>|n.

            # See Also

            |whelp technology|n · |whelp lab|n · |whelp weapons lab|n · |whelp defense lab|n
        """,
    },
    {
        "key": "radar",
        "aliases": ["rd", "radar building"],
        "category": "Buildings",
        "text": """
            |wRadar (RD)|n

            An intelligence outpost that widens how far you can see through the fog of war.

            # Build Requirements

            Cost:
              Iron - |c15|n
              Energy - |c10|n

            Requirements:
              Player - |clevel 9|n
              HQ - |crequired|n

            # What It Does

            Extends your vision radius, revealing more of the map around it — useful for spotting enemies and scouting expansion sites. No agent required.

            # Using It

            |wbuild RD|n where you want coverage. For mobile recon, send a |cScout|n on patrol with |wagent patrol <id> <x,y> ...|n. Check the map with |wmap|n and |wscan|n.

            # See Also

            |whelp agents|n · |whelp combat|n · |whelp buildings|n
        """,
    },
    {
        "key": "turret",
        "aliases": ["tu", "turret building"],
        "category": "Buildings",
        "text": """
            |wTurret (TU)|n

            An automated defense emplacement that fires on intruders without you lifting a finger.

            # Build Requirements

            Cost:
              Stone - |c20|n
              Iron - |c15|n

            Requirements:
              Player - |clevel 5|n
              HQ - |crequired|n

            # What It Does

            Auto-attacks enemies in range each tick while your |cHQ|n is active — no agent required. Pair with |cWalls|n to hold a line and a |cRelay|n to boost its damage.

            # Using It

            |wbuild TU|n where you want coverage. It fires on its own as long as your |cHQ|n stands. See |whelp combat|n and |whelp relay|n.

            # See Also

            |whelp combat|n · |whelp wall|n · |whelp relay|n
        """,
    },
    {
        "key": "vault",
        "aliases": ["vt", "vault building"],
        "category": "Buildings",
        "text": """
            |wVault (VT)|n

            High-capacity storage that keeps your stockpile safe — even while you're logged off.

            # Build Requirements

            Cost:
              Stone - |c25|n
              Iron - |c10|n

            Requirements:
              Player - |clevel 4|n
              HQ - |crequired|n

            # What It Does

            Stores far more than your HQ's starting capacity and is protected while you're offline, so raiders can't drain it. Harvester agents with |cdelivery|n prefer to haul resources here.

            # Using It

            |wbuild VT|n, stand on it, and |wdeposit <resource> [amount || all]|n to bank, |wwithdraw <resource> [amount || all]|n to pull back. You can only use storage you own. See |whelp storage|n.

            # See Also

            |whelp storage|n · |whelp resources|n · |whelp headquarters|n
        """,
    },
    {
        "key": "relay",
        "aliases": ["rl", "relay building"],
        "category": "Buildings",
        "text": """
            |wRelay (RL)|n

            A support structure that amplifies the firepower of nearby |cTurrets|n.

            # Build Requirements

            Cost:
              Iron - |c20|n
              Energy - |c15|n

            Requirements:
              Player - |clevel 15|n
              HQ - |crequired|n

            # What It Does

            Boosts the damage of Turrets near it — force-multiplying a defensive cluster. Position it central to a ring of Turrets for the widest effect.

            # Using It

            |wbuild RL|n within your Turret cluster. No agent required. See |whelp turret|n and |whelp combat|n.

            # See Also

            |whelp turret|n · |whelp combat|n · |whelp buildings|n
        """,
    },
    {
        "key": "shield",
        "aliases": ["sg", "shield generator", "shield building", "shields"],
        "category": "Buildings",
        "text": """
            |wShield Generator (SG)|n

            A defensive structure that wraps your nearby buildings in a regenerating energy |cshield|n — a second health bar that soaks damage before the building's own HP takes any.

            # Build Requirements

            Cost:
              Iron - |c40|n
              Energy - |c30|n
              Circuits - |c20|n

            Requirements:
              Player - |clevel 15|n
              HQ - |crequired|n

            Limit: |c4 per planet|n.

            # What It Does

            Every building you own within its radius gains a shield equal to a share of that building's max HP. Both the radius and the shield strength scale with the generator's |clevel|n:

            |cLevel 1|n — radius 2 (a 5x5 area around the generator), shield = |c25%|n of each covered building's HP.
            Each level adds |c+1|n to the radius and |c+25%|n to the shield: at |cLevel 4|n a covered building has a shield equal to |c100%|n of its HP (effectively doubling its durability).

            The shield absorbs incoming damage first — from players, turrets, guards, and bombs alike — and only overflow hits the building. A drained shield regenerates on its own (about 1% of its capacity every few seconds), so between attacks your base heals its shields back up even though buildings never heal their own HP.

            # Overlap & Limits

            If several generators cover the same building, it takes the single strongest shield — they don't stack, so spreading generators out to cover more ground beats piling them up. You may build at most |c4 per planet|n. (Future tech research will raise these limits.)

            # Using It

            |wbuild SG|n central to the buildings you want to protect — your |cHQ|n, |cVault|n, and |cTurret|n line are prime candidates. Upgrade it to widen the radius and thicken the shield. No agent required.

            # See Also

            |whelp combat|n · |whelp turret|n · |whelp wall|n · |whelp buildings|n
        """,
    },
    {
        "key": "medbay",
        "aliases": ["mb", "medbay building", "medical bay"],
        "category": "Buildings",
        "text": """
            |wMedbay (MB)|n

            A medical facility that crafts healing supplies and shortens how long you're out after a defeat.

            # Build Requirements

            Cost:
              Wood - |c15|n
              Stone - |c10|n
              Iron - |c10|n
              Energy - |c5|n

            Requirements:
              Player - |clevel 18|n
              HQ - |crequired|n

            # What It Does

            Crafts consumables and reduces your respawn time after losing a fight.

            Crafts:
              |cmedkit|n - restore HP with |wuse medkit|n
              |ccombat stim|n - temporary combat buff

            # Using It

            |wbuild MB|n, stand on it, and |wcraft|n to list its items, then |wcraft medkit|n to make one instantly. Use what you make with |wuse medkit|n. See |whelp craft|n and |whelp combat|n.

            # See Also

            |whelp craft|n · |whelp combat|n · |whelp equipment|n
        """,
    },
    {
        "key": "blacksmith",
        "aliases": ["bs", "blacksmith building", "bench"],
        "category": "Buildings",
        "text": """
            |wBlacksmith (BS)|n

            The gear workbench. Standing in your own Blacksmith (online, not mid-upgrade) unlocks three bench commands: |winsert|n, |wreroll|n, and |wsalvage|n. It produces nothing on its own — it's where you improve and recycle what you already have.

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c20|n
              Iron - |c25|n

            Requirements:
              Player - |clevel 11|n
              HQ - |crequired|n
              Deed - destroy |c3 outposts|n

            # The Bench

            |winsert <item> [weapon]|n — permanently apply a crafted insert to your equipped weapon: a damage-type coating (|cVenom Coating|n → poison, |cIncendiary Core|n → fire), an |cExtended Barrel|n (+range), or a |cHollow-Point Kit|n (+damage, -range). Inserts |rcannot be removed|n and a weapon has limited insert slots. If you have both a melee and a ranged weapon equipped, name the |c[weapon]|n to say which one gets the insert. Craft inserts at the Armory or Research Lab first.
            |wreroll <item>|n — draw fresh base stats for a rolled item and re-stamp its quality score. Costs |cSalvage|n plus a little Iron. Affixes, rarity, and applied inserts are untouched.
            |wsalvage <item>|n — destroy a carried item and pocket |cSalvage|n; better items pay more.

            # Level Scaling

            Upgrading improves every bench: the reroll floor rises (a higher-level bench can't roll near the bottom of the band), weapons gain a second insert slot from level 3, and salvage yield grows up to +50% at level 5.

            # Costs & Research

            Rerolls charge Salvage plus resources; the |cSalvage Protocols|n research cuts the charge by 25%. Earn Salvage by salvaging here or refining resources at a |cRefinery|n (|whelp salvage currency|n).

            # See Also

            |whelp loot|n · |whelp salvage currency|n · |whelp refinery|n · |whelp craft|n
        """,
    },
    {
        "key": "refinery",
        "aliases": ["rf", "refinery building"],
        "category": "Buildings",
        "text": """
            |wRefinery (RF)|n

            A resource converter that turns surplus stockpile into |cSalvage|n — the late-game sink for resources you no longer need, |cNexium|n included.

            # Build Requirements

            Cost:
              Stone - |c25|n
              Iron - |c30|n
              Circuits - |c15|n

            Requirements:
              Player - |clevel 13|n
              HQ - |crequired|n
              Deed - destroy a |cfortress|n

            # What It Does

            Stand in your own Refinery (online, not mid-upgrade) and |wrefine <resource> [<amount> || all]|n: the batch is |rburned|n and you're credited Salvage — roughly 1 Salvage per 2 units at level 1, and each level improves the rate (1.5x at level 5). The conversion is one-way: the Refinery outputs Salvage only, never resources.

            # Using It

            |wrefine nexium all|n — convert your whole Nexium surplus
            |wrefine iron 100|n — convert 100 Iron

            Spend the Salvage on Blacksmith |wreroll|ns. See |whelp salvage currency|n.

            # See Also

            |whelp salvage currency|n · |whelp blacksmith|n · |whelp resources|n
        """,
    },
    {
        "key": "munitions plant",
        "aliases": ["mp", "munitions", "munitions plant building",
                    "bomb factory"],
        "category": "Buildings",
        "text": """
            |wMunitions Plant (MP)|n

            The bomb works. Every |cgrenade|n and |cmine|n in the game is made here and nowhere else — if you want explosives, you build this.

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c15|n
              Iron - |c20|n

            Requirements:
              Player - |clevel 6|n
              HQ - |crequired|n

            # What It Does

            Crafts the full bomb catalog: |cFrag|n and |cPlasma|n grenades (thrown in a direction) and |cLand|n and |cProximity|n mines (armed where you stand). Bombs breach cover — they damage closed buildings and the people sheltering inside them — which makes them the answer to a walled outpost and to anyone turtling in a structure.

            Like the Armory, Research Lab, and Medbay it is a |cproduction|n building, so an assigned |cEngineer|n crafts the same catalog passively while you're elsewhere, paying the same resource cost from your stockpile.

            # Using It

            |wbuild MP|n, stand in it, then |wcraft|n with no argument to list the bombs and their costs. |wcraft frag_grenade|n makes one. Bombs land in your supply bag — set a fuse with |wset frag_grenade 3|n before you |wthrow|n or |warm|n them.

            The |cFrag Grenade|n and |cLand Mine|n cost only starter resources and carry no rank gate, so a plant pays off as soon as you can raise one. |cPlasma|n grenades and |cProximity|n mines need a rank on top.

            # See Also

            |whelp bombs|n · |whelp craft|n · |whelp combat|n · |whelp buildings|n
        """,
    },
    {
        "key": "survey array",
        "aliases": ["sa", "survey array building", "array"],
        "category": "Buildings",
        "text": """
            |wSurvey Array (SA)|n

            A signals station that hunts down enemy bases on your planet — outposts and fortresses alike. It is how you stop wandering the map hoping to bump into one.

            # Build Requirements

            Cost:
              Wood - |c15|n
              Stone - |c25|n
              Iron - |c20|n

            Requirements:
              Player - |clevel 6|n
              HQ - |crequired|n

            # What It Does

            Runs the |wsurvey|n search. |wsurvey scan|n picks one enemy base on your current planet that isn't on your map yet — any tier, and it names which — and returns a search area containing it, placed so the base sits at a random spot inside, never at the centre. |wsurvey narrow|n shrinks that area; |wsurvey <x> <y>|n reads a bearing and rough distance from a tile inside it. Pinpoint the outpost and you're given its exact coordinates, and its tile is remembered on your |wmap|n for good.

            Every action except the status readout costs resources, so a search is a real investment — and cheap probing beats expensive sweeping if you read the bearings well.

            # Level Scaling

            Upgrading tightens the opening search area (a maxed array starts roughly where a level 1 array needs several sweeps to get). Sweep and probe costs don't change with level.

            # Using It

            |wbuild SA|n, stand in it, then |wsurvey scan|n. Full walkthrough in |whelp survey|n.

            # See Also

            |whelp survey|n · |whelp outposts|n · |whelp radar|n · |whelp buildings|n
        """,
    },
    {
        "key": "sniper nest",
        "aliases": ["sniper", "nest", "sniper nest building"],
        "category": "Buildings",
        "text": """
            |wSniper Nest (SN)|n

            An elevated firing position: while you stand on it, your weapon shoots further.

            # Build Requirements

            Cost:
              Wood - |c15|n
              Stone - |c20|n
              Iron - |c20|n

            Requirements:
              Player - |clevel 9|n
              HQ - |crequired|n

            # What It Does

            While its owner stands on the nest's tile (and it's online), your equipped weapon gains bonus range: |c+1|n at level 1, |c+2|n at level 3, |c+3|n at level 5. The bonus is strictly positional — step off the tile and it's gone — and it helps no one but you. Total weapon range is hard-capped, so stacking every range bonus can never make a global sniper.

            # Using It

            |wbuild SN|n at a commanding spot — covering a chokepoint or your walls — climb on, then |wtarget|n and |wshoot|n. Pairs naturally with a |csniper rifle|n. See |whelp combat|n.

            # See Also

            |whelp combat|n · |whelp watchtower|n · |whelp turret|n
        """,
    },
    {
        "key": "watchtower",
        "aliases": ["wt", "watchtower building"],
        "category": "Buildings",
        "text": """
            |wWatchtower (WT)|n

            A lookout post: while you stand on it, you see further through the fog of war.

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c15|n
              Iron - |c5|n

            Requirements:
              Player - |clevel 7|n
              HQ - |crequired|n

            # What It Does

            While its owner stands on the tower's tile (and it's online), your sight range grows: |c+1|n at level 1, |c+2|n at level 3, |c+3|n at level 5 — on top of your base 7-tile vision. Strictly positional and owner-only: step off and it's gone. Cheaper and earlier than a |cRadar|n, but you have to climb it yourself.

            # Using It

            |wbuild WT|n at a viewpoint near your perimeter, stand on it, and check |wmap|n and |wscan|n. For always-on coverage, see |whelp radar|n.

            # See Also

            |whelp radar|n · |whelp sniper nest|n · |whelp combat|n
        """,
    },
    {
        "key": "field hospital",
        "aliases": ["fh", "hospital", "field hospital building"],
        "category": "Buildings",
        "text": """
            |wField Hospital (FH)|n

            A patch-up station: while you stand on it, you heal faster.

            # Build Requirements

            Cost:
              Wood - |c20|n
              Stone - |c15|n
              Iron - |c10|n

            Requirements:
              Player - |clevel 10|n
              HQ - |crequired|n

            # What It Does

            While its owner stands on the hospital's tile (and it's online), each natural regeneration tick heals extra HP: |c+1|n at level 1, |c+2|n at level 3, |c+3|n at level 5. It follows the normal regen rules — never past your max HP, and it can't raise the dead. Strictly positional and owner-only: camp the tile between fights, top up, move out.

            # Using It

            |wbuild FH|n behind your walls and retreat to it after a raid. For burst healing mid-fight, a |cmedkit|n is still faster (|whelp medbay|n).

            # See Also

            |whelp medbay|n · |whelp combat|n · |whelp buildings|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Admin — the unified @<entity> CRUD grammar (staff only)
    # ----------------------------------------------------------------- #
    {
        "key": "admin",
        "aliases": ["admin commands", "admin crud", "entity admin",
                    "@entity", "staff commands"],
        "category": "Admin",
        "locks": "read:perm(Builder)",
        "text": """
            |wAdmin: the unified entity grammar|n

            Every |w@<entity>|n staff command — |w@item|n, |w@building|n, |w@agent|n, |w@tech|n, |w@outpost|n, |w@alliance|n, |w@player|n, |w@stat|n, |w@resource|n, |w@powerup|n, |w@terrain|n, |w@planet|n — speaks one grammar: the same verbs, the same target addressing, and the same definition scope. Learn it once and it works everywhere. Type |whelp @item|n (or any command) for that entity's exact fields, kwargs, and opt-outs.

            # The Core Verbs

            Ten verbs, split into the instance plane (live objects in the world) and the definition plane (the YAML data the world is built from):

            |wlist|n [filter] [player] — live instances as numbered rows (fills the |c#N|n cache)
            |wspawn|n <def> [kwargs] [player] — create an instance from a definition
            |wshow|n <target> — full readout of one instance
            |wset|n <target> <field> <value> — write one bounded field (out-of-range values clamp, with a note)
            |wdestroy|n <target>[, <target> …] — delete an instance (multi-target needs confirmation)
            |wdef list|n — every loaded definition
            |wdef show|n <key> — one definition's merged fields, overrides flagged
            |wdef set|n <key> <field> <value> — override a definition field (overlay-backed, validated reload)
            |wdef reset|n <key> [field] — drop an override and reload
            |wdef diff|n — the current overrides in this entity's domain

            An entity supports each verb or opts out of it with a reason — there is no third state. Invoking an opted-out verb prints its reason and a pointer to the supported path, and changes nothing (e.g. |w@player destroy|n points you at |wobliterate|n; |w@agent def list|n explains agents have no YAML domain).

            # Permissions

            Read and instance verbs (|wlist|n, |wspawn|n, |wshow|n, |wset|n, |wdestroy|n) and the read side of the def scope (|wdef list|n, |wdef show|n, |wdef diff|n) sit at |cBuilder|n. The two definition writes — |wdef set|n and |wdef reset|n — are |cAdmin|n on every entity and cannot be lowered. A few entities pin an instance verb higher (e.g. |w@stat set|n is Admin).

            # Target Grammar

            Every |w<target>|n resolves the same way, trying each tier in order and stopping at the first hit:

            |c#N|n — the Nth row from your most recent |wlist|n on that entity (run |wlist|n first; a stale index tells you to re-list)
            |ckey|n — exact, case-sensitive (e.g. |cassault_rifle|n)
            |cname|n — exact, case-insensitive (e.g. |cAssault Rifle|n)
            |cprefix|n — case-insensitive prefix of a key or name; an ambiguous prefix lists the candidates instead of guessing

            A trailing |c[player]|n scopes to that player's holdings (roster, items, resources); omit it and it defaults to you. |wme|n / |wself|n also mean you.

            # The Definition Scope

            |wdef set|n writes to a shared overlay file layered over the base YAML, then triggers a validated hot-reload: on success the new value goes live and you see the before→after; on any validation, parse, or I/O failure the live data is untouched and the overlay is rolled back, with the validator's errors relayed. |wdef reset|n removes an override (a field, or a whole key) and reloads. |wdef diff|n shows what you've overridden. Not every entity has a definition domain — see the opt-outs below.

            # Definition-Only & Read-Only Commands

            |w@powerup|n and |w@terrain|n are definition-only: they are never spawned as standalone objects, so every instance verb opts out and points at the |wdef|n scope. |w@planet|n is definition-read-only: |wdef list|n and |wdef show|n serve straight from the planet registry, but planets are not hot-reloadable, so |wdef set|n / |wdef reset|n / |wdef diff|n opt out — to change a planet, edit |cplanets.yaml|n and restart.

            # Legacy Spellings

            Old command spellings still work during the migration. Each prints a one-line deprecation note naming its canonical replacement, then behaves identically. Pairs (alias → canonical):

            |w@item stats|n → |wshow|n
            |w@agent create|n → |wspawn|n
            |w@alliance inspect|n → |wshow|n, |w@alliance disband|n → |wdestroy|n
            |w@outpost tiers|n → |wdef list|n
            |w@resource give|n → |wspawn|n
            |w@player level|n / |wrank|n → |wset|n
            |w@stat hp|n / |wmaxhp|n / |wxp|n → |wset|n

            # Extra Verbs

            Some entities add verbs beyond the core ten (these are current spellings, not deprecated): |w@building open|n (open/close to ranged fire), |w@alliance kick|n / |wtransfer|n / |wrename|n (staff moderation), |w@tech grant|n / |wrevoke|n (the tech write model, mapped onto spawn/destroy), and |w@resource reset|n (restore starting resources).

            # See Also

            |whelp @item|n · |whelp @building|n · |whelp @player|n · |whelp @stat|n · |whelp @tech|n · |whelp @outpost|n · |whelp @alliance|n · |whelp @resource|n · |whelp @powerup|n · |whelp @terrain|n · |whelp @planet|n
        """,
    },
    # ----------------------------------------------------------------- #
    #  Framework (dev only)
    # ----------------------------------------------------------------- #
    {
        "key": "evennia",
        "aliases": ["ev"],
        "category": "General",
        "locks": "read:perm(Developer)",
        "text": """
            Evennia is a MU-game server and framework written in Python. You can read more on https://www.evennia.com.

            # subtopics

            ## Installation

            You'll find installation instructions on https://www.evennia.com.

            ## Community

            There are many ways to get help and communicate with other devs!

            ### Discussions

            The Discussions forum is found at https://github.com/evennia/evennia/discussions.

            ### Discord

            There is also a discord channel for chatting - connect using the following link: https://discord.gg/AJJpcRUhtF

        """,
    },
]
