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

            With passive income flowing, expand: |wupgrade|n buildings (costs grow fast), stockpile surplus in a |cVault|n (|whelp storage|n), explore, climb the |clevels and ranks|n to unlock new planets and buildings (|whelp level|n), and research techs at a |cLab|n (rank gates which techs you can research).

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

            XP comes from two kinds of activity, and both feed the same level bar:

            |cBuilding your base|n — the early game. You earn XP for completing a construction (|c+30|n), completing an upgrade (|c+30|n), training an agent (|c+40|n), and each manual |wharvest|n yield (|c+1|n). This is renewable-but-modest income designed to carry a brand-new commander to roughly |clevel 8-9|n through base-building alone.
            |cCombat|n — the long game. Defeating a player (|c+100|n), destroying a building (|c+50|n), and wrecking an enemy base's |cHeadquarters|n (|c+300|n) are the big earners. Combat is where the levels past the early game come from — raiding |coutposts|n is the reliable source (|whelp outposts|n).

            Losing a fight costs you XP (|c-50|n) and can drop your level — but see Ranks below: your |crank never falls|n even if your level does.

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

            Rank is a |chigh-water mark|n: once earned it sticks, so a bad losing streak that lowers your level never demotes your title.

            # What Levels & Ranks Unlock

            |cBuildings|n gate on level — higher-tier structures (Turret, Barracks, Lab, Relay, Medbay) unlock as you climb. |wbuild|n with no argument always lists what you can build right now, and |whelp buildings|n shows the full tier list. Some buildings also need a |cdeed|n — proof of action, like destroying an outpost — on top of the level (|whelp outposts|n).
            |cAgent capacity|n rises with rank — a higher rank lets you command more agents (|whelp agents|n). Your agents also can't out-level you: an agent's effective level is capped at your own.
            |cNew planets|n open at rank thresholds — |cForge|n at Staff Sergeant, |cTundra|n and |cSpace|n at Captain, |cInferno|n at Colonel, the |cCitadel|n at General.
            |cTechnologies|n gate on rank — a |cLab|n lets you research techs, with the earliest available at Corporal and stronger ones spaced upward (|whelp lab|n).

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

            You always have one |ccurrent objective|n. Do the thing it asks — build your HQ, set up an Extractor, train an agent, and so on — and it completes automatically, rewards you, and advances to the next. There's nothing to "accept" or "turn in"; just play and the checklist keeps up.

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
            |wcraft <item>|n — make gear/ammo at an Armory, Lab, or Medbay
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

            Everything you carry has |cweight|n, and you can only carry so much — resources are light but not free. Stockpile the overflow in a |cVault|n or your |cHQ|n with |wdeposit|n, and pull it back with |wwithdraw|n. See |whelp storage|n.

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
            |cEngineer|n — at an |cArmory|n or |cLab|n: builds/researches
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

            Stand on a tile and type |wbuild <type>|n (by abbreviation like |wEX|n or full name like |wextractor|n). Stay on the tile while it builds — or let an |cEngineer|n agent finish it. |wbuild|n with no argument lists what you can build right now.

            |wupgrade|n improves the building you're standing on; costs and times climb steeply. |wrepair|n restores a damaged building to full HP for resources (buildings don't heal on their own — the cost scales with how damaged it is, and a building knocked offline comes back online when repaired). |wdemolish|n tears one down for a partial refund (40% at L1 up to 80% at L5).

            # Building Types

            Each line: |wABBR|n |cName|n — purpose (unlocks at level N).

            |wHQ|n |cHeadquarters|n — home base, respawn point, holds storage. Required before most other buildings. (L1)
            |wEX|n |cExtractor|n — boosts harvesting; must sit on resource terrain. Harvester agents work here. (L1)
            |wAC|n |cAcademy|n — train agents here (|wagent train|n inside). (L1)
            |wAR|n |cArmory|n — crafts weapons, armor, and ammo. (L3)
            |wWL|n |cWall|n — a barrier that blocks passage. (L2)
            |wBK|n |cBarracks|n — army capacity. (L7, requires deed: destroy an outpost)
            |wLB|n |cLab|n — research and craft advanced gear; needs an Engineer to run. (L11, requires deed: destroy 3 outposts)
            |wRD|n |cRadar|n — extends vision. (L9)
            |wTU|n |cTurret|n — auto-attacks enemies in range while your HQ stands. (L5)
            |wVT|n |cVault|n — high-capacity resource storage, protected while you're offline; harvesters prefer to deliver here. (L4)
            |wRL|n |cRelay|n — boosts nearby Turret damage. (L15)
            |wSG|n |cShield Generator|n — projects a regenerating shield onto nearby buildings. (L15, max 4 per planet)
            |wMB|n |cMedbay|n — crafts medkits and stims; reduces respawn time. (L18)

            Higher-tier buildings unlock as you gain |clevels|n; a few also require a |cdeed|n (Barracks needs one destroyed outpost, Lab needs three). Check |wscore|n for your current level, |wbuild|n to see what's available now, and |whelp level|n for the full progression picture.

            # Per-Building Guides

            Every building has its own help topic with costs, level, dependencies, and examples: |whelp hq|n · |whelp extractor|n · |whelp academy|n · |whelp armory|n · |whelp wall|n · |whelp barracks|n · |whelp lab|n · |whelp radar|n · |whelp turret|n · |whelp vault|n · |whelp relay|n · |whelp shield|n · |whelp medbay|n.

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

            Gear makes you tougher and deadlier. You have eleven equipment |cslots|n covering you head to toe, plus a weapon and an accessory slot. Every equipped piece adds armor and other bonuses.

            # Getting Gear

            Build an |cArmory|n (|wbuild AR|n) for weapons, armor, and ammo, a |cLab|n (|wbuild LB|n) for advanced gear, or a |cMedbay|n (|wbuild MB|n) for medkits and stims. Two ways to get items from them: stand in the building and |wcraft <item>|n to make one instantly for resources, or assign an |cEngineer|n agent and it crafts the same catalog passively while you're away (see |whelp craft|n). Made gear lands in your inventory; pick up dropped items with |wget|n.

            # Slots

            |chead eyes face torso arms hands legs feet back|n — armor and utility. |cweapon|n — your active weapon. |caccessory|n — a utility item (scope, hauler pack). One item per slot; equipping a new one swaps out the old.

            # Stat Bonuses

            Beyond |carmor|n (less incoming damage) and |cdamage|n, gear can grant utility: |cmove speed|n, |csight range|n, |ccarry capacity|n, and |cmax HP|n. Stats from every worn piece stack, and |wequipment|n shows your combined totals. Max-HP gear raises your health ceiling — equipping it adds headroom (it doesn't heal you on the spot), and taking it off lowers the ceiling, trimming any HP above the new max.

            # Wearing Gear

            |wequip <item>|n — wear an item from your inventory (a partial name works, e.g. |wequip assault|n). Alias: |wwear|n.
            |wequip all|n — wear every piece of carried gear at once
            |wunequip <item>|n — take off gear by name (|wunequip assault|n) or by slot (|wunequip head|n). Alias: |wremove|n.
            |wunequip all|n — take off everything
            |wequipment|n (|weq|n) — full paperdoll: every slot, its item, stat bonuses, your loaded weapon's ammo, and combined totals

            Powerful gear may require a |crank|n — |wequip|n tells you if you're not high enough.

            # Consumables & Bombs

            These live in your |csupply bag|n (counted, not slotted):
            |wuse medkit|n — restore health
            |wuse combat_stim|n — a temporary combat buff
            |wset <bomb> <sec>|n then |wthrow <grenade> <dir>|n / |warm <mine>|n — fused area explosives (|whelp bombs|n)

            # Carry Weight

            Every item and resource has weight, and you can carry only so much. Equipped gear is free — it's worn, not hauled — but supplies and resources on you count. A |chauler pack|n raises your limit. See |whelp storage|n. |winventory|n shows your current load.

            # See Also

            |whelp combat|n · |whelp storage|n · |whelp buildings|n
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

            Combat is real-time and resolves on the game tick. Damage is your weapon's power plus bonuses, minus the target's armor.

            # Attacking

            |wattack <target>|n (|wa|n) — attack a player, building, or agent in reach. You can target anything within your |creach|n — whichever is greater of what you can |csee|n ('scan') and your equipped weapon's |crange|n — so 'attack guard' picks the nearest matching foe within reach (a long-range weapon like a sniper rifle can hit a foe beyond your sight range). Your equipped |cweapon|n decides your damage and reach: a melee weapon only reaches a foe on your |csame tile|n (close in first — an adjacent enemy is not yet in melee reach), while ranged weapons strike at a distance. Buildings can be meleed from an adjacent tile (to break a wall by hand). Equip a weapon first (|whelp equipment|n).

            # Ranged: Target & Shoot

            With a |cranged weapon|n you can fight at a distance in two ways:
            |wtarget <enemy>|n (|wlock|n) — lock onto an enemy in your weapon's range. It takes a few ticks to lock (faster with better gear); you're told when it completes. A lock is a |cheld aim|n — |rhold still while it locks, because moving in any direction breaks it|n. Once locked, your shot is far more accurate (|c90%|n baseline) and keeps hitting the enemy even as |cthey|n move — until they leave your range (or you move).
            |wshoot|n (|wfire|n) — fire your ranged weapon. With a |clocked|n target, plain |wshoot|n fires at them. Otherwise |wshoot <n/s/e/w>|n fires in a direction and hits the first thing in the line of fire, at lower accuracy (|c70%|n baseline). You can only hit a specific player by locking onto them first. A directional shot |cbreaches cover|n — it damages a |cbuilding|n (open or closed) in the line of fire, so it's how you shoot down a |cWall|n; and if you're |cinside|n a building, any direction fires at the structure around you, letting you shoot your way out. Every shot spends ammo whether it hits or misses.

            # Timing: Instant vs. Ticked

            Your own |wattack|n and directional |wshoot|n resolve |cinstantly|n — the hit lands the moment you act, throttled by a short per-weapon |ccooldown|n (you'll be told if you fire again too soon). |cTurrets|n, |cguards|n, and a |clocked|n tracking shot resolve on the world |ctick|n instead: that tiny delay is their dodge window (you can duck into cover between a turret locking on and firing).

            # The Combat State

            Dealing or taking damage puts you |rin combat|n for a short time — you'll get a |r[Combat]|n notice when it starts, and |wscore|n shows the seconds remaining. Each new hit resets the timer. While in combat you can't slip through your own |cWalls|n, you can't manually |wenter|n or |wleave|n a building, and moving is slower (better |cmove speed|n gear eases this). It clears on its own once the timer runs out.

            # Friendly Fire

            You |ccan|n attack your own things — your buildings and your own agents — as well as other players. There's no XP or benefit for hitting your own (you can't farm yourself), and it still puts you in the combat state, but it's allowed (handy to clear a misplaced building). Take care with area attacks: a |cgrenade|n hits everything in the blast, friend or foe.

            # Ammo & Reloading

            Ranged weapons feed their ammo one of two ways. Most (like the |cassault rifle|n) fire straight from your |cresource stockpile|n — each shot spends a little Iron, Energy, or similar, so there's nothing to reload; just keep the resource stocked. Magazine weapons (like the |cservice rifle|n) fire from a loaded |cmagazine|n and run dry: |wreload|n refills the magazine from the matching |cammo|n in your supply bag (make it at an |cArmory|n or |cLab|n). |wequipment|n shows a magazine weapon's loaded count.

            # Armor & Defense

            Every armor piece you |wequip|n reduces incoming damage, and they stack across all slots. |cTurrets|n auto-attack intruders; |cWalls|n block movement. A |cVault|n protects your stored resources while you're offline. A |cShield Generator|n wraps nearby buildings in a regenerating |cshield|n that soaks damage before their HP (|whelp shield|n). You and your agents heal over time, but |cbuildings do not|n — repair a damaged building with |wrepair|n (see |whelp buildings|n).

            # Buildings as Cover

            A |cmelee|n attacker only reaches a foe on their |csame tile|n — an enemy standing on the next tile over is not yet in reach, so someone has to close in first (guards chase onto your tile; you step onto theirs). Being |cinside a building|n adds ranged cover on top of this: |cRanged|n fire and |cturrets|n depend on the building — a |cclosed|n building fully shelters the |coccupant|n from ranged attacks, while an |copen|n one (like the raidable buildings in enemy bases) does not — you can still be shot inside it. So ducking into an open enemy structure stops nothing ranged, and melee still needs the same tile either way. A |cclosed|n building still protects the |cperson|n inside — but the |cbuilding itself|n can be shot down: a directional |wshoot|n breaches the structure (the round hits the wall, not the occupant), which is how you knock down a |cWall|n or a sealed structure from range.

            # Guards

            A |cGuard|n agent (or |cSoldier|n) automatically attacks any enemy that comes within range each tick — so assigning one actually defends your base. Melee guards must be on your |csame tile|n to strike, so they chase onto it to reach you; ranged soldiers reach several tiles out. This cuts both ways: enemy |coutpost|n and |cfortress|n guards attack you the same way when you raid them (|whelp outposts|n).

            # Destroying a Base

            Destroying an owner's |cHeadquarters|n is decisive. Wreck an |cenemy base|n's HQ and the whole base is eliminated at once — every building and guard is wiped and loot drops on the spot: |g[Combat] Outpost eliminated! +X XP. Loot dropped at (x,y).|n Lose your |cown|n HQ (in PvP) and nothing is deleted — your base goes |rinert|n instead: turrets stop, production halts, agents idle, and building commands are refused until you |wbuild|n a new HQ.

            # Bombs: Grenades & Mines

            Bombs are |cfused|n explosives — set a fuse first with |wset <bomb> <seconds>|n (or |wset all <seconds>|n for your whole inventory), then deploy. A |cgrenade|n is |wthrow|n-n in a direction (|wthrow frag_grenade n|n): it flies until it hits the first obstacle or its max range, |clands|n, and ticks down before exploding. A |cmine|n is |warm|n-ed in place (|warm land_mine|n): it ticks down where you stand. Anyone on a bomb's tile sees it |rtick|n. The blast hits everything in radius — enemies, your own units, and |ryou|n if you're too close — so mind the fuse and your distance. See |whelp bombs|n. Bombs come from a |cLab|n.

            # After a Fight

            Losing costs XP and sends you back to your |cHQ|n. A |cMedbay|n shortens respawn time. Winning awards XP toward your next |clevel|n — combat is the main source of levels past the early game (|whelp level|n).

            # See Also

            |whelp level|n · |whelp equipment|n · |whelp bombs|n · |whelp outposts|n · |whelp agents|n · |whelp buildings|n
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

            Before you enter the field you prepare in a |cstaging area|n: choose a |cclass|n and a |cspawn point|n, then deploy. It's a short numbered wizard — just |wtype the number|n of your choice at each step. You are not yet in the world while staging — you can't move, build, or fight until you |wenter|n.

            # Step 1 — Choose a Class

            A numbered list of classes is shown; type its |wnumber|n (e.g. |w1|n) to pick one. Your class is a chosen identity shown on your |wscore|n and in |wwho|n. (You can also type |wclass <name>|n — a name or unambiguous prefix like |wvan|n for Vanguard.)

            # Step 2 — Choose a Spawn Point

            Next, a numbered list of spawn points appears; type its |wnumber|n to pick:
            |cHeadquarters|n — deploy at your HQ.
            |cPlace of death|n — deploy where you last died.
            |cRandom location|n — deploy at a random tile in |wopen ground|n, well clear of any building.
            If your choice isn't available (no HQ yet, or you've never died), you deploy at your planet's default spawn instead. (|wspawn <where>|n also works.)

            # Enter the Game

            Once a class and spawn point are set, a final menu appears: type |w1|n to enter the world at your chosen point, or |w0|n to quit. (|wenter|n / |wdeploy|n and |wquit|n also work.)

            # Quitting & Reconnecting

            |wquit|n works in two levels: from the game it pulls you back to the |wstaging area|n (you stay connected — re-deploy from the menu, and you land |wright back where you left off|n, NOT a re-rolled spawn); from the staging area it disconnects. You |wcan't quit the field while in combat|n (see |wscore|n for your timer) — the anti-combat-log rule. If your connection |rdrops|n without |wquit|n, your character lingers in the world briefly (still a target) before being pulled back to staging — so don't rely on pulling the plug to escape a fight.

            # Dying

            When you're defeated you return here and re-run the whole wizard — |wpick a class again|n, then a spawn point (|cHQ|n / |cplace of death|n / |crandom|n). You re-enter at full health with a |wcleared combat timer|n.

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

            Bombs are |cfused|n area explosives. There are two families: |cgrenades|n (thrown) and |cmines|n (placed) — with variants of each (e.g. |cFrag|n and |cPlasma|n grenades; |cLand|n and |cProximity|n mines). Both come from a |cLab|n (|wcraft|n one, or assign an Engineer).

            # Set the Fuse First

            You must set a fuse before every throw or arm — |wset <bomb> <seconds>|n. The fuse is clamped to that bomb's min/max (grenades short, mines longer). |wset all <seconds>|n sets every bomb type in your inventory at once (each clamped to its own limits). The set fuse is |cconsumed|n when you deploy — set it again for the next one.

            # Grenades — throw in a direction

            |wthrow <grenade> <n/s/e/w>|n (alias |wth|n) hurls the grenade in a compass direction. It flies until it hits the |cfirst obstacle|n or reaches its |cmax range|n, then |clands|n and the fuse ticks down before it explodes. It lands just |cin front of a building|n (the blast then breaches the wall from outside), on a |cunit's tile|n if it hits someone, or at max range on a clear line. You can't pick a grenade up once it's away.

            # Mines — arm in place

            |warm <mine>|n plants the mine on |cyour current tile|n and starts its fuse. A mine can't be thrown, and once armed it can't be picked up — it ticks down where you left it. Good as a timed trap on a chokepoint.

            # Ticking & the Blast

            Everyone standing on a bomb's tile sees it |rtick|n each second (and sees a grenade |cland|n or a mine |carm|n). When the fuse reaches zero it explodes: everything within the blast |cradius|n takes flat damage minus armor — |renemies, your own agents and buildings, and you|n if you're in range. A blast |cbreaches cover|n: unlike a gunshot, it damages buildings whether open or closed and reaches players sheltered inside them, so mind your distance. Kills you cause credit you.

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

            # Raiding

            Clear the |cguards|n, dodge or destroy the |cTurrets|n, breach the |cWalls|n, and destroy the enemy |cHeadquarters|n. Guards fight back and turrets auto-fire, so bring armor, ammo, and medkits (|whelp equipment|n, |whelp combat|n). Guards you kill stay dead. Destroying the |cHQ|n eliminates the entire base at once: |g[Combat] Outpost eliminated! +X XP. Loot dropped at (x,y).|n Pick up the loot with |wget|n.

            # Respawns

            Cleared bases respawn elsewhere after a while, so there's always something to raid — at rising difficulty as you climb the ranks.

            # See Also

            |whelp combat|n · |whelp equipment|n · |whelp buildings|n · |whelp agents|n
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

            With no amount (or |wall|n), deposit moves everything you hold and withdraw takes as much as fits under your carry limit. You can only use storage you |cown|n.

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
            |cLab|n (|wbuild LB|n) — futuristic gear and grenades (also runs research)
            |cMedbay|n (|wbuild MB|n) — medkits and combat stims

            # Crafting by Hand

            Stand in the building and type |wcraft|n with no argument to list what it makes and each item's resource cost. Then |wcraft <item>|n makes one instantly, spending the resources from your stockpile.

            # Letting Agents Craft

            Assign an |cEngineer|n to an Armory or Lab (|wagent assign <id>|n while inside) and it crafts items on its own over time, paying the same resource cost from your stockpile — the hands-off way to stock up while you do other things. That asynchronous work is the whole point of agents.

            # What You Get

            Gear (weapons, armor, accessories) goes into your inventory — |wequip|n it or see it with |winventory|n. Supplies (ammo, medkits, stims, grenades) go into your supply bag — |wuse|n or |wreload|n them. Powerful items may need a minimum |crank|n.

            # Examples

            |wcraft|n — list this building's items and costs
            |wcraft assault_rifle|n — make one Assault Rifle
            |wcraft medkit|n — make one Medkit (at a Medbay)

            # See Also

            |whelp equipment|n · |whelp armory|n · |whelp lab|n · |whelp medbay|n · |whelp agents|n
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

            Cost: |c10 Wood|n, |c10 Stone|n, |c10 Iron|n. Level: |c1|n. Dependencies: none — the HQ is the one building you can raise with no prerequisites. One HQ per planet.

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

            Cost: |c15 Wood|n, |c10 Stone|n. Level: |c1|n. Dependencies: an |cHQ|n, and it must sit on |cresource terrain|n (Forest, Rock, Mountain, etc. — |wmap|n shows which tiles yield what).

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

            Cost: |c20 Wood|n, |c15 Stone|n, |c10 Iron|n. Level: |c1|n. Dependencies: an |cHQ|n.

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

            Cost: |c20 Wood|n, |c15 Stone|n, |c15 Iron|n. Level: |c3|n. Dependencies: an |cHQ|n.

            # What It Does

            Produces modern gear: |ccombat knife|n, |cassault rifle|n, |csniper rifle|n, |cservice rifle|n, |crifle rounds|n, and the full armor set (helmet, vest, gloves, greaves, boots), plus the |cscope|n and |chauler pack|n accessories. Crafting spends resources per item.

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

            Cost: |c5 Stone|n. Level: |c2|n. Dependencies: an |cHQ|n.

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

            Cost: |c15 Wood|n, |c15 Stone|n, |c10 Iron|n. Level: |c7|n. Deed: destroy an |coutpost|n. Dependencies: an |cHQ|n.

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
        "aliases": ["lb", "lab building", "laboratory"],
        "category": "Buildings",
        "text": """
            |wLab (LB)|n

            Your research center and futuristic-gear workshop. It runs the tech tree and crafts your most advanced equipment.

            # Build Requirements

            Cost: |c25 Wood|n, |c20 Stone|n, |c15 Iron|n. Level: |c11|n. Deed: destroy |c3 outposts|n. Dependencies: an |cHQ|n, and an |cEngineer|n agent to run research.

            # What It Does

            Researches |ctechnologies|n (|wresearch <tech>|n) and crafts futuristic gear: |cplasma rifle|n, |cpower armor|n, |cjetpack|n, |cenergy cell|n, and |cfrag grenade|n. An |cEngineer|n drives research progress and passive crafting.

            # Using It

            |wbuild LB|n, assign an |cEngineer|n (|wagent assign <id>|n inside), then |wtechnology|n to see the tree and |wresearch <tech>|n to start one. Craft gear with |wcraft|n / |wcraft plasma_rifle|n. See |whelp craft|n.

            # See Also

            |whelp technology|n · |whelp craft|n · |whelp armory|n · |whelp equipment|n
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

            Cost: |c15 Iron|n, |c10 Energy|n. Level: |c9|n. Dependencies: an |cHQ|n.

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

            Cost: |c20 Stone|n, |c15 Iron|n. Level: |c5|n. Dependencies: an |cHQ|n.

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

            Cost: |c25 Stone|n, |c10 Iron|n. Level: |c4|n. Dependencies: an |cHQ|n.

            # What It Does

            Stores far more than your HQ's starting capacity and is |cprotected while you're offline|n, so raiders can't drain it. Harvester agents with |cdelivery|n prefer to haul resources here.

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

            Cost: |c20 Iron|n, |c15 Energy|n. Level: |c15|n. Dependencies: an |cHQ|n.

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

            Cost: |c40 Iron|n, |c30 Energy|n, |c20 Circuits|n. Level: |c15|n. Dependencies: an |cHQ|n. Limit: |c4 per planet|n.

            # What It Does

            Every building you own within its radius gains a shield equal to a share of that building's max HP. Both the radius and the shield strength scale with the generator's |clevel|n:

            |cLevel 1|n — radius 2 (a 5x5 area around the generator), shield = |c25%|n of each covered building's HP.
            Each level adds |c+1|n to the radius and |c+25%|n to the shield: at |cLevel 4|n a covered building has a shield equal to |c100%|n of its HP (effectively doubling its durability).

            The shield absorbs incoming damage first — from players, turrets, guards, and bombs alike — and only overflow hits the building. A drained shield |cregenerates|n on its own (about 1% of its capacity every few seconds), so between attacks your base heals its shields back up even though buildings never heal their own HP.

            # Overlap & Limits

            If several generators cover the same building, it takes the |csingle strongest|n shield — they don't stack, so spreading generators out to cover more ground beats piling them up. You may build at most |c4 per planet|n. (Future tech research will raise these limits.)

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

            Cost: |c15 Wood|n, |c10 Stone|n, |c10 Iron|n, |c5 Energy|n. Level: |c18|n. Dependencies: an |cHQ|n.

            # What It Does

            Crafts consumables — |cmedkits|n (restore HP with |wuse medkit|n) and |ccombat stims|n (temporary combat buff) — and reduces your respawn time after losing a fight.

            # Using It

            |wbuild MB|n, stand on it, and |wcraft|n to list its items, then |wcraft medkit|n to make one instantly. Use what you make with |wuse medkit|n. See |whelp craft|n and |whelp combat|n.

            # See Also

            |whelp craft|n · |whelp combat|n · |whelp equipment|n
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
