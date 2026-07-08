# Help & Command-Help Style Guide

The single standard for **all** player-facing help in RTS Combat Overworld:
in-game command help (Evennia turns each command's class docstring into its
`help <command>` entry) and the file help topics in
[`world/help_entries.py`](world/help_entries.py). Follow it for every new
command and every help topic so the whole game reads as one voice.

It extends [`CODING_STYLE.md`](../CODING_STYLE.md) ("Default Command Docstrings")
— read that first for the base Evennia conventions; this file pins the
project-specific structure, tone, and colour vocabulary.

---

## 1. Voice & tone

- **Second person, imperative, concise.** "Stand on a resource tile and
  `harvest`." Not "The player may harvest by…".
- **Explain the *what* and the *why*, not the implementation.** A new player
  should learn what a feature is for, not how it's coded.
- **Assume no prior knowledge.** Spell out jargon the first time it appears in
  a topic (e.g. "Extractor — a building that boosts harvesting").
- **Never reference code, classes, or internal names** (no `EquipmentSystem`,
  `db.resources`, `ItemDef`). Use the in-game noun ("your supply bag", "carry
  weight").
- Keep lines under ~76 characters so they don't wrap awkwardly in narrow
  clients.

---

## 2. Colour vocabulary (use consistently)

Evennia colour tags. Always close with `|n`. Use the *same* colour for the
*same kind* of thing everywhere:

| Tag | Used for | Example |
|-----|----------|---------|
| `|w` (bright white) | **commands & keywords** the player types | `|wharvest|n`, `|wbuild HQ|n` |
| `|c` (cyan) | **resources, items, and game nouns** | `|cWood|n`, `|cMedkit|n`, `|cVault|n` |
| `|g` / `|G` | success / positive, and green terrain | `|gComplete|n`, forest `|G&&|n` |
| `|r` / `|R` | danger, denial, combat, failure | `|rout of ammo|n` |
| `|y` (yellow) | warnings, notices, in-progress | `|y[Building]…|n` |
| `|W` (bright grey) | rock / stone / neutral terrain | mountain `|W/\\|n` |
| `|x` (dark grey) | de-emphasised / secondary text | — |

Do **not** invent new colour meanings. If it's a command, it's `|w`; if it's a
resource/item/building noun, it's `|c`.

---

## 3. Command docstrings (in-game `help <command>`)

Every command's class docstring **is** its help entry. Use this exact skeleton
(2-space indentation per Evennia convention). Omit a section only if it has
nothing to say — but every command has at least a summary + `Usage:`.

```
Short one-line summary (what it does, plainly).

Usage:
  key <required> [optional]

Options:
  <arg>       what it is and accepted values
  [optional]  what happens when omitted (the default)

Examples:
  key foo         what this produces
  key bar 3       …

Notes:
  Any gotchas, gating, or related commands. One idea per line.
```

**Rules:**

- **Summary line first**, one sentence, no period needed on the fragment.
- **Bracket convention** (from CODING_STYLE.md):
  - `<arg>` — a *description* of what to type (a value you supply).
  - `[arg]` — *optional*; skippable.
  - `a|b` choices — separate with ` | ` (spaces) or `||`; never a bare `|`
    (it parses as a colour code).
- **Always document extra parameters.** If a command accepts `all`, an
  optional amount, coordinates, abbreviations *and* full names, subcommands, or
  flags — every one must appear under `Usage:` or `Options:`. A parameter the
  code accepts but the docstring hides is a bug in the help.
- **Give at least one `Examples:` line** for any command that takes an argument.
  Argument-free commands (`score`, `map`) may skip it.
- **List aliases** in a `Notes:` line when they aren't obvious
  (`Notes: Aliases: msg, dm, page, tell.`).
- **Do not** put colour codes inside command docstrings — Evennia's help
  renderer shows them literally in some clients. Keep docstrings plain text.
  (Colour is for the file help topics in §4, which are rendered.)

### Subcommand routers (`agent`, `@building`, …)

Two help surfaces — keep them consistent:

1. **The class docstring** = full `help <command>` text. List every subcommand
   with its full argument syntax under `Usage:` (this is the only place syntax
   appears), then a one-line description of each.
2. **The per-subcommand `help_text`** (index 1 of the `subcommands` tuple
   `(handler, help_text, perm)`) = the terse line shown when the command is
   typed bare or with a bad verb. Keep it a single clause, verb-first
   ("Assign an agent to a role"). Do not duplicate full syntax here.

---

## 4. File help topics (`world/help_entries.py`)

Longer conceptual guides (`help buildings`, `help equipment`). Each is a dict:
`{"key", "aliases", "category": "Game", "text": "..."}`. Structure the `text`:

```
|wTitle|n

One or two sentences: what this system is and why it matters.

# Section Heading

Body text. Commands in |w..|n, game nouns in |c..|n.

# Another Section

...

# See Also

|whelp <topic>|n · |whelp <topic>|n
```

**Rules:**

- **First line: bold title** (`|wEquipment|n`), then a blank line, then a
  plain-language intro paragraph.
- Use `#` headings for sections, `##` for subsections (Evennia subtopics). Never
  skip a level.
- **End every topic with a `# See Also` line** cross-linking related topics as
  `|whelp <topic>|n`, separated by ` · `. This is how players discover the rest.
- **Reference real commands and real content only.** Building lists, resource
  names, agent roles, and item names must match the YAML/data — verify before
  writing. Stale help is worse than none.
- Category is **`"Game"`** for all player topics (capitalised — matches the
  existing entries; the Evennia default `"general"` is a different bucket).
- Keys and aliases must not collide with command names (a same-named command
  shadows the file topic). Use distinct topic keys (e.g. `equipment guide`
  aliases, not the bare command name where it clashes).

---

## 5. Cross-linking & discoverability

- The `tutorial` topic is the front door: it must end by pointing at the
  deeper topics (`help buildings`, `help agents`, `help equipment`, …).
- Every topic's `# See Also` links its 2–4 nearest neighbours.
- Prefer `|whelp <thing>|n` over prose like "see the buildings help" — players
  can click/copy it.

---

## 6. Checklist before committing a help change

- [ ] Summary line reads plainly to a first-time player.
- [ ] Every accepted parameter/option/alias is documented.
- [ ] At least one example for any command taking arguments.
- [ ] Colours follow §2 (commands `|w`, nouns `|c`); all closed with `|n`.
- [ ] Command docstrings are plain text; file topics use `#` sections.
- [ ] `# See Also` present on file topics.
- [ ] Content matches real data (buildings, resources, roles, items).
