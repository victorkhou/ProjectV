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

## Requirements

- **Python 3.12** (the framework pins `>=3.12, <3.13`).
- A single, consistent **Evennia 6.0** install. This repo vendors the Evennia
  framework at the repository root (`evennia/`), so install it from there in
  editable mode rather than pulling a separate copy from PyPI — running with
  both a vendored and a pip-installed Evennia on the path leads to a split
  install where command sets can fail to load. A virtualenv is strongly
  recommended.

## First-time setup

Run these once, from the **repository root**:

```bash
python3 -m venv .venv && source .venv/bin/activate   # recommended
pip install -e .                                     # install vendored Evennia + deps
```

This makes the `evennia` command available and ensures `import evennia` resolves
to the vendored copy. Verify with `evennia --version` (should report `6.0.0`).

## Running the game

All `evennia` commands run from the **`mygame/` directory**:

```bash
cd mygame
evennia --initmissing    # first time only — creates secret_settings.py + logs dir
evennia migrate          # first time only — initialise the database
evennia start            # start the server (asks to create a superuser first time)
evennia reload           # hot-reload code without dropping connections
evennia stop
```

> **Important — import paths.** The live server runs with `mygame/` as its
> Python root, so all imports *inside* `mygame/` must be written relative to it
> (e.g. `from world.constants import MAX_LEVEL`, `from commands... import ...`).
> Do **not** prefix game imports with `mygame.` in production modules — that
> prefix only resolves when running the test suite from the repo root, and will
> crash the server with `ModuleNotFoundError: No module named 'mygame'` (which
> in turn breaks command-set loading, so no in-game commands work). The
> `mygame.` prefix belongs only in test files.

Connect with a MUD client on `localhost:4000`, or open the web client at
`http://localhost:4001`. The web client includes a custom graphical map
renderer (see [`mygame/web/static/webclient/js/plugins/`](mygame/web/static/webclient/js/plugins/));
telnet clients get an ASCII map of the same data.

The main configuration is [`mygame/server/conf/settings.py`](mygame/server/conf/settings.py).
Local-only overrides (e.g. the Django `SECRET_KEY`) belong in
`mygame/server/conf/secret_settings.py`, which is git-ignored and created
automatically by `evennia --initmissing`.

### Troubleshooting

- **`secret_settings.py file not found or failed to import.`** — Harmless on its
  own (it's just a notice), but to silence it and get a generated `SECRET_KEY`,
  run `evennia --initmissing` from `mygame/`.
- **In-game commands all report `Command '<x>' is not available`** — The
  character/account command set failed to load or build. Check
  `mygame/server/logs/server.log` for an import traceback at startup (a common
  cause is a bad `mygame.`-prefixed import — see the import-paths note above),
  fix it, then fully restart: `evennia stop && evennia start`. A plain `reload`
  does not always rebuild a command set that failed at boot.
- **`ModuleNotFoundError: No module named 'evennia'` when running from `mygame/`**
  — Evennia isn't installed in the active environment. Activate your virtualenv
  and run `pip install -e .` from the repository root.

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
- **Equipment & supplies.** Items fall into six categories. Three are **Gear**
  (`armor`, `weapon`, `accessory`) — unique objects `equip`ped into one of eleven
  body slots (`head`, `eyes`, `face`, `torso`, `arms`, `hands`, `legs`, `feet`,
  `back`, `weapon`, `accessory`), whose stats aggregate across every slot
  (armor, damage bonus, move speed, sight range, carry capacity). Three are
  **Supplies** (`ammo`, `consumable`, `throwable`) — fungible counted stacks held
  in a Supply bag. `use` a consumable to heal or apply a timed buff; `throw` a
  grenade for area damage that respects target armor. Powerful gear can be
  rank-gated.
- **Weapons, ammo & reloading.** Weapons are `melee` (fixed range 1, no ammo) or
  `ranged`. A ranged weapon fires from a loaded magazine; each shot draws from the
  magazine, and `reload` refills it from the ammunition you carry. Fire an empty
  weapon and combat tells you to reload. Energy weapons may also draw a per-shot
  resource cost. A freshly acquired ranged weapon arrives with a full magazine.
- **Carry weight & storage.** Everything you carry has weight — Supplies plus the
  resources on your person (equipped Gear is free). Your total must stay under a
  base carry limit (raised by `carry_capacity` gear such as a hauler pack); admins
  are exempt. `db.resources` remains your **spend pool** for all costs. Surplus
  goes into **Vault/HQ storage buildings**, which now hold a real capacity-bounded
  pool: `deposit`/`withdraw` between your person and a co-located store, and
  harvester agents deliver into it. Any inflow past a carry or storage limit drops
  the remainder on the ground rather than destroying it.
- **Ranks.** Twelve military ranks from Recruit to Marshal
  ([`mygame/data/definitions/ranks.yaml`](mygame/data/definitions/ranks.yaml)). XP gained from
  combat promotes you; dying can demote you. Rank gates technologies, powerups,
  and planet access.
- **Agents.** Players train autonomous NPC agents at an Academy and assign them
  roles: army roles `guard` (auto-defense) and `scout` (recon/vision), plus
  building roles `harvester` (Extractor) and `engineer` (Armory/Lab). Agents
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
| `equip` / `unequip` | `eq` `gear` | Equip/remove Gear into one of the 11 body slots |
| `use <item>` | | Use a consumable from your supplies (medkit heals, stim buffs) |
| `throw <item> <target\|x y>` | | Throw a throwable (grenade) at a target or coordinates |
| `reload` | | Reload your equipped ranged weapon from carried ammo |
| `deposit <res> <amt>` | | Deposit resources into a co-located storage building |
| `withdraw <res> <amt>` | | Withdraw resources from a co-located storage building |
| `inventory` | `inv` `i` | List Gear, Supplies, resources, and carried weight vs limit |
| `get <item>` | `grab` `take` | Pick up an item (weight- and stack-limited) |
| `research <tech>` | `re` | Research a technology at a Lab |
| `technology` | `tech` | View the tech tree |
| `powerup` | `pu` | Activate / view powerups |
| `equipment` | | Paperdoll: all 11 equipment slots, per-slot stats + totals |
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

Plus standalone admin tools: `@teleport`, `@clearfog`, `@reboot` (hot-reload
YAML definitions), `@purgerooms`, `@migrate`.

---

## Architecture

The code follows a **Clean-Architecture** layered design: thin command
"controllers" delegate to **framework-free** plain-Python game **systems**, which
read static data from a central **registry** and reach all Evennia I/O through
abstract **ports** whose concrete **adapters** are the only code that imports
Evennia. A one-second **tick script** drives the real-time simulation, an **event
bus** decouples systems from one another, and **presenters** turn domain events
into player-facing text. The whole thing is wired at one **composition root**
(`server/conf/game_init.py`), so swapping the framework or DB touches zero core
logic — a property enforced by an AST layering-guard test, not just convention.

All game code lives under [`mygame/`](mygame/):

```
commands/         Player, agent, and admin commands (thin controllers)
  command_router.py    SubcommandRouter base for noun+verb commands
world/
  systems/        Domain logic (framework-free) — one class per concern:
                    BuildingSystem, ResourceSystem, CombatEngine, AgentSystem,
                    RankSystem, PowerupSystem, TechLabSystem, EquipmentSystem,
                    MovementSystem  (all take collaborator ports via DI)
  core/ports/     Abstract ports (stdlib-only ABCs): Notifier, PlayerNotifier,
                    DefinitionsProvider, Agent/Building/MovingEntity repos +
                    factories, TerrainProvider — the contracts the core depends on
  adapters/       Evennia implementations of the ports (the ONLY modules that
                    import Evennia); constructed + injected at the composition root
  presenters/     NotificationPresenter — Observer over PLAYER_NOTIFICATION;
                    formats + delivers per-player messages via a port
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
  cross-reference checks) and supports atomic hot-reload via `@reboot`.
- **Centralised tick loop.** `GameTickScript` runs an ordered list of
  individually error-guarded steps each second, filtered to chunks near online
  players. Agent behaviour scripts have `interval = 0` and are driven by this
  loop rather than self-timing, so the whole simulation advances deterministically.
- **Ports & adapters (dependency inversion).** Game systems depend only on
  abstract ports; Evennia lives behind adapters injected at the composition root.
  The core imports no framework, so it's unit-testable without a server or DB —
  and the boundary is guarded by an AST test
  ([`world/core/tests/test_layering_invariant.py`](mygame/world/core/tests/test_layering_invariant.py)),
  making "framework swap = zero core changes" a checkable property.
- **Presentation seam.** Domain systems never build player-facing text; they
  publish structured `PLAYER_NOTIFICATION` events, and a single
  `NotificationPresenter` (Observer) formats and delivers them. Restyling any
  player message is a one-line edit in one file.

---

## Testing

The game ships with an extensive test suite — 80 test files, ~1,500 test
functions, including ~309 [Hypothesis](https://hypothesis.readthedocs.io)
property-based tests (file names prefixed `test_prop_`). A `conftest.py` installs
lightweight Evennia stubs so the bulk of the suite runs as fast plain-Python unit
tests without a live server or database. Two AST-based guard tests defend the
architecture itself: the layering invariant (core stays framework-free) and the
composition-root name check (`game_init` calls nothing it didn't import).

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
