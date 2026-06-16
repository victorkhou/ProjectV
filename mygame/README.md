# RTS Combat Overworld — game directory

This is the Evennia game directory for the **RTS Combat Overworld** project. All
of the game's code, content, and tests live here:

- `commands/` — player, agent, and admin commands (thin controllers)
- `world/` — game systems, coordinate/terrain engine, data registry, tick loop
- `typeclasses/` — Evennia typeclasses (characters, rooms, objects, NPCs, scripts)
- `server/conf/` — settings and startup wiring
- `web/` — Django website and graphical web client
- `data/` — YAML content (buildings, items, ranks, tech, powerups, terrain, planets)

## Quick start

```bash
evennia migrate   # first time only
evennia start     # asks to create a superuser on first run
evennia reload    # hot-reload code
evennia stop
```

Connect on `localhost:4000` (MUD client) or `http://localhost:4001` (web client).

## Full documentation

The complete project overview — gameplay, the full command reference,
architecture, and testing instructions — is in the
[top-level README](../README.md).
