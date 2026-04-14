"""
File-based help entries. These complements command-based help and help entries
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

"""

HELP_ENTRY_DICTS = [
    {
        "key": "tutorial",
        "aliases": ["new", "start", "getting started", "newbie", "beginner"],
        "category": "Game",
        "text": """
            |wWelcome, Commander.|n

            You've been dropped on |cTerra|n with a handful of resources and
            a mission: build a base, train agents, and expand across the
            galaxy. Here's how to get started.

            # Getting Started

            ## Step 1: Find Your Spot

            Use |wmap|n to see the terrain around you. Look for tiles with
            resources — |gForest|n (|G&&|n) gives Wood, |wRock|n (|w##|n)
            gives Stone, and |WMountain|n (|W/\\|n) gives Iron.

            You want to set up near at least two different resource types.
            Move with |wnorth|n, |wsouth|n, |weast|n, |wwest|n (or just
            |wn|n, |ws|n, |we|n, |ww|n).

            ## Step 2: Build a Headquarters

            Once you've found a good spot, type |wbuild HQ|n. Stay on the
            tile while it constructs. This is your home base — everything
            else requires it.

            ## Step 3: Set Up Extractors

            Extractors boost your harvesting when placed on resource tiles.
            Walk to a Forest or Rock tile and |wbuild EX|n. You have enough
            to build two, but you might need to harvest a bit for the second.

            ## Step 4: Learn to Harvest

            Stand on any resource tile and type |wharvest|n. You'll gather
            resources as long as you stay put. Harvesting at an Extractor
            is significantly faster than on raw terrain.

            ## Step 5: Build an Academy

            You'll need an Academy to train agents. It costs more than your
            starting resources, so you'll need to gather from your Extractors
            first. Type |wbuild AC|n when you're ready.

            ## Step 6: Train Your First Agent

            Step inside your Academy and type |wtrain|n. Training takes time
            and resources. Once your agent is ready, you can assign it to
            work for you.

            ## Step 7: Put Your Agent to Work

            Walk to one of your Extractors and type |wassign 2|n. Your agent
            will start harvesting autonomously — no more standing around.
            Type |wagents|n to see your roster.

            # What's Next

            With passive income flowing, you can start expanding. Upgrade
            buildings with |wupgrade|n (costs and time grow fast at higher
            levels). Explore further. Build defenses. Train more agents.

            Eventually you'll unlock new planets with tougher terrain and
            rarer resources. But that's a story for another rank.

            # Useful Commands

            |wmap|n — see the overworld around you
            |wscore|n — your stats, rank, and resources
            |winventory|n — what you're carrying
            |wbuildings|n — list your buildings
            |wagents|n — list your agents
            |wscan|n — see who's nearby
            |whelp <command>|n — detailed help on any command

        """,
    },
    {
        "key": "resources",
        "aliases": ["resource", "gathering", "harvesting guide"],
        "category": "Game",
        "text": """
            |wResources|n

            There are 6 resources in the game. Where you find them depends
            on which planet you're on.

            |cWood|n — from Forest terrain. Used in most early buildings.
            |cStone|n — from Rock and Permafrost terrain. Walls and defenses.
            |cIron|n — from Mountain, Scrapyard, and other rocky terrain.
            |cEnergy|n — from Power Grids and Magma Vents. Mid-game tech.
            |cCircuits|n — from Circuit Fields and Control Rooms. Advanced tech.
            |cNexium|n — only from Citadel's Vault Rooms. Endgame material.

            # How to Gather

            Stand on a resource tile and type |wharvest|n. You'll gather
            automatically every few seconds as long as you stay put.

            Building an |wExtractor|n on a resource tile multiplies your
            yield. Assigning a |wHarvester agent|n to an Extractor makes
            it fully automatic.

            # Tips

            Terra has Wood, Stone, and Iron — enough to get started.
            You'll need to reach Forge for Energy and Circuits.
            Plan your base location around the resources you need most.

        """,
    },
    {
        "key": "agents",
        "aliases": ["agent guide", "agent help"],
        "category": "Game",
        "text": """
            |wAgents|n

            Agents are NPCs you train and assign to do work for you.
            They're the key to scaling your base beyond what you can
            do manually.

            # Training

            Build an |wAcademy|n, step inside, and type |wtrain|n.
            Each agent costs more than the last. Training takes time.

            # Roles

            Assign agents to buildings or your army:

            |wHarvester|n — assign to an Extractor for passive resource income
            |wEngineer|n — assign to a building to progress construction/upgrades
            |wGuard|n — assign to a Turret to activate auto-defense
            |wScout|n — assign to a Radar to extend vision
            |wSoldier|n — joins your army for raids
            |wMedic|n — heals after combat, reduces respawn time

            # Commands

            |wagents|n — list all your agents
            |wassign <id>|n — assign agent (inside a building, role is automatic)
            |wassign <id> <role>|n — assign to an army role
            |wunassign <id>|n — return agent to HQ

            # Agent Cap

            Your rank determines how many agents you can have. Getting
            demoted puts excess agents in reserve — they keep working
            but can't be reassigned until you rank back up.

        """,
    },
    {
        "key": "buildings",
        "aliases": ["building guide", "building help", "construction"],
        "category": "Game",
        "text": """
            |wBuildings|n

            Buildings are the backbone of your base. Each type serves a
            different purpose, and all can be upgraded to level 5.

            # Building

            Stand on a tile and type |wbuild <type>|n. You need to stay
            on the tile while it constructs. An Engineer agent can do
            this for you.

            # Upgrading

            Stand on a building and type |wupgrade|n. Upgrade costs and
            times grow exponentially — this is where your agents and
            resources really get tested.

            # Types

            |wHQ|n — required first. Your respawn point.
            |wExtractor|n — place on resource terrain for boosted harvesting
            |wAcademy|n — train new agents here
            |wWall|n — blocks enemy movement
            |wBarracks|n — increases army capacity
            |wArmory|n — craft equipment (needs Engineer)
            |wLab|n — research tech (needs Engineer)
            |wTurret|n — auto-attacks enemies (needs Guard)
            |wRadar|n — extends vision (needs Scout)
            |wVault|n — protects resources from raids
            |wMedbay|n — reduces respawn time (enhanced by Medic)
            |wRelay|n — boosts nearby Turret damage

            Higher-rank buildings unlock as you progress. Check |wscore|n
            to see your current rank and what's available.

        """,
    },
    {
        "key": "evennia",
        "aliases": ["ev"],
        "category": "General",
        "locks": "read:perm(Developer)",
        "text": """
            Evennia is a MU-game server and framework written in Python. You can read more
            on https://www.evennia.com.

            # subtopics

            ## Installation

            You'll find installation instructions on https://www.evennia.com.

            ## Community

            There are many ways to get help and communicate with other devs!

            ### Discussions

            The Discussions forum is found at https://github.com/evennia/evennia/discussions.

            ### Discord

            There is also a discord channel for chatting - connect using the
            following link: https://discord.gg/AJJpcRUhtF

        """,
    },
]
