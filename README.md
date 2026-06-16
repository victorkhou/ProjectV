# RTS Combat Overworld

A real-time-strategy / PvP combat MUD built on the [Evennia](https://www.evennia.com)
MU\* framework (Evennia 6.0, Python 3.12). Players explore procedurally
generated, coordinate-based planet maps, harvest terrain-specific resources,
construct and upgrade a tech tree of buildings, command autonomous AI agents,
and fight in real time while climbing a military rank ladder that gates access
to new planets, technologies, and powerups.

The game is data-driven: buildings, items, ranks, technologies, powerups,
terrain, planets, and balance values all live in YAML under
[`mygame/data/`](mygame/data/) and are loaded into validated dataclasses at
startup, so most content tuning needs no code changes.

> The game itself lives in [`mygame/`](mygame/). The `evennia/` directory is the
> vendored Evennia framework this project is built on.

---

## Running the game

From the `mygame/` directory:

```bash
cd mygame
evennia migrate          # first time only — initialise the database
evennia start            # start the server (asks to create a superuser first time)
evennia reload           # hot-reload code without dropping connections
evennia stop
```

Connect with a MUD client on `localhost:4000`, or open the web client at
`http://localhost:4001`. The web client includes a custom graphical map
renderer (see [`mygame/web/static/webclient/js/plugins/`](mygame/web/static/webclient/js/plugins/));
telnet clients get an ASCII map of the same data.

The main configuration is [`mygame/server/conf/settings.py`](mygame/server/conf/settings.py).
Local-only overrides (e.g. the Django `SECRET_KEY`) belong in
`mygame/server/conf/secret_settings.py`, which is git-ignored.

---

## Gameplay overview

- **Overworld.** Each planet is a single shared `PlanetRoom` (not a room per
  tile). Position is tracked by `(x, y)` coordinates via an in-memory
  `CoordinateIndex`, and terrain is generated deterministically from a per-planet
  seed — no per-tile rows are stored. Six planets ship in
  [`mygame/data/definitions/planets.yaml`](mygame/data/definitions/planets.yaml): `terra`
  (earth, the rank-1 starter), `forge` (industrial), `tundra` (frozen),
  `inferno` (volcanic), `citadel` (fortress), and `space`. Higher planets carry
  a `rank_requirement`.
- **Resources & harvesting.** Tiles have terrain-specific resource nodes
  (Wood, Stone, Iron, Energy, Metals, Circuits, …). Players harvest by standing
  on a node, or place an **Extractor** with an assigned **harvester agent** to
  produce automatically.
- **Buildings.** Twelve building types form a tech tree rooted at a
  **Headquarters** (HQ): Extractor (EX), Academy (AC), Lab (LB), Armory (AR),
  Turret (TU), Vault (VT), Radar (RD), Wall (WL), Barracks (BK), Medbay (MB),
  Relay (RL). Buildings have HP and an owner, take ticks to construct/upgrade,
  and enter an **offline-protection** state when their owner disconnects.
- **Combat.** Real-time, tick-resolved PvP using equippable items (all items
  share one `GameItem` typeclass, differentiated by YAML slot/stat data).
  Turrets auto-attack in range. Defeats award/deduct Combat XP.
- **Ranks.** Twelve military ranks from Recruit to Marshal
  ([`mygame/data/definitions/ranks.yaml`](mygame/data/definitions/ranks.yaml)). XP gained from
  combat promotes you; dying can demote you. Rank gates technologies, powerups,
  and planet access.
- **Agents.** Players train autonomous NPC agents at an Academy and assign them
  roles: `harvester` (Extractor), `engineer` (Armory/Lab construction), `guard`
  (Turret), `scout` (Radar), plus army roles `soldier` and `medic`. Agents
  pathfind and act on their own each tick.

---

## Commands

### Player commands

| Command | Aliases | Purpose |
|---|---|---|
| `move <dir>` | `n` `s` `e` `w` `north` … | Move one tile on the overworld |
| `look` | `l` `ls` | Look at the tile / overworld map |
| `map` | `m` | Show the overworld map |
| `scan` | `sn` | Scan nearby tiles / buildings |
| `harvest` | `ha` | Harvest the resource node on your tile |
| `build <type>` | `bu` | Construct a building at your tile |
| `upgrade` | `up` | Upgrade the building you're in |
| `demolish` | `demo` | Demolish a building |
| `leave` | `out` `outside` | Step outside the building you're in |
| `closeexit` / `openexit` | | Close / open a building exit |
| `attack <target>` | `at` `a` | Attack a player, building, or NPC |
| `equip` / `unequip` | `eq` `gear` | Manage equipped items |
| `inventory` | `inv` `i` | List carried items |
| `get <item>` | `grab` `take` | Pick up an item |
| `research <tech>` | `re` | Research a technology at a Lab |
| `technology` | `tech` | View the tech tree |
| `powerup` | `pu` | Activate / view powerups |
| `equipment` | | View Armory equipment |
| `buildings` | `bl` | List your buildings |
| `score` | `status` `st` `sc` | Show your stats, rank, and resources |
| `stop` | `cancel` | Cancel your current action |
| `chat <msg>` | | Public channel chat |
| `say <msg>` | | Speak to players on your tile |
| `message <player> <msg>` | `msg` `dm` `page` `tell` | Private message |
| `who` | `doing` | List online players with rank/level |

### Agent command

A single `agent` command with subcommands:

```
agent list                       list your agents
agent train                      train a new agent at an Academy
agent assign <id> <role> [bldg]  assign an agent to a role
agent unassign <id>              unassign an agent
agent patrol <id> ...            set or clear a patrol route
agent stop <id>                  stop an agent's current action
```

### Admin commands

Admin actions are grouped into noun+verb routers (Builder+ to see, with
per-subcommand permission checks). These replace the older one-command-per-action
layout.

```
@building spawn <type> [owner=<name>] [level=<N>]   (Builder+)
@building destroy                                   (Builder+)
@agent create <player> [count]                      (Admin+)
@agent destroy <id> <player> | training <player>    (Admin+)
@agent list <player>                                (Builder+)
@resource give <type> <amount> [player]             (Builder+)
@resource reset [player]                            (Admin+)
@player level <player> <N>                          (Admin+)
@player rank <player> <rank>                        (Admin+)
```

Plus standalone admin tools: `@teleport`, `@clearfog`, `@reloaddata` (hot-reload
YAML definitions), `@purgerooms`, `@migrate`.

---

## Architecture

The code follows a layered design: thin command "controllers" delegate to
plain-Python game **systems**, which read static data from a central
**registry** and mutate persistent state on Evennia **typeclasses**. A
one-second **tick script** drives the real-time simulation, and an **event bus**
decouples systems from one another.

All game code lives under [`mygame/`](mygame/):

```
commands/         Player, agent, and admin commands (thin controllers)
  command_router.py    SubcommandRouter base for noun+verb commands
world/
  systems/        Domain logic — one class per concern:
                    BuildingSystem, ResourceSystem, CombatEngine, AgentSystem,
                    RankSystem, PowerupSystem, TechLabSystem, EquipmentSystem,
                    MovementSystem
  coordinate/     Coordinate index, procedural terrain generation, fog-of-war
                    (bit-packed), per-planet registry, map rendering
  data_registry.py + schema_validator.py + definitions.py
                  Load and validate YAML content into dataclasses (hot-reloadable)
  event_bus.py    Module-level pub/sub for cross-system events
  chunking.py     "Simulate near players only" tick optimisation
  pathfinding.py  Bounded A* for agent movement
typeclasses/
  characters.py   CombatCharacter (the base player typeclass)
  combat_entity.py  HP / respawn mixin shared by players and NPCs
  rooms.py        PlanetRoom (one shared room per planet + coordinate index)
  objects.py      GameEntity, GameItem, Building, ResourceDrop
  npcs.py         Agent NPCs
  agent_scripts.py  Per-role agent behaviour scripts (harvester, engineer, …)
  scripts.py      GameTickScript (1s game loop) and AutoSaveScript
server/conf/      Settings and lifecycle hooks; game_init.initialize_game()
                    wires up all systems at server start
web/              Django website, graphical web client, admin
data/             YAML content: buildings, items, ranks, technologies,
                    powerups, terrain, planets, and balance config
```

### Key design choices

- **One room per planet.** Collapsing a tile grid into a single `PlanetRoom`
  with an in-memory coordinate index avoids the database churn of a room-per-tile
  model while still supporting fast `(x, y)` lookups.
- **Procedural terrain.** Terrain is regenerated on demand from a seed, so no
  map data is persisted — maps are reproducible and cheap.
- **Data-driven content.** All game content is YAML validated on load (with
  cross-reference checks) and supports atomic hot-reload via `@reloaddata`.
- **Centralised tick loop.** `GameTickScript` runs an ordered list of
  individually error-guarded steps each second, filtered to chunks near online
  players. Agent behaviour scripts have `interval = 0` and are driven by this
  loop rather than self-timing, so the whole simulation advances deterministically.

---

## Testing

The game ships with an extensive test suite — 66 test files, ~1,170 test
functions, including ~249 [Hypothesis](https://hypothesis.readthedocs.io)
property-based tests (file names prefixed `test_prop_`). A `conftest.py` installs
lightweight Evennia stubs so the bulk of the suite runs as fast plain-Python unit
tests without a live server or database.

Run the fast unit/property tests with `pytest` **from the repository root**
(the test modules import packages such as `world` and `commands` relative to
the repo root, so running from inside `mygame/` will fail to collect):

```bash
# from the repository root:
python -m pytest mygame                       # whole suite
python -m pytest mygame/world/systems/tests/  # a single area
python -m pytest mygame -k combat             # filter by name

# Full Evennia integration test runner (uses a real test DB); from mygame/:
evennia test --keepdb .
```

---

## Further reading

Design and requirements documents for each feature live under
[`.kiro/specs/`](.kiro/specs/) (procedural world, coordinate-room refactor,
RTS combat overworld, agent AI, command consolidation, quit-building cleanup).
For the framework itself, see the [Evennia documentation](https://www.evennia.com/docs/latest).
