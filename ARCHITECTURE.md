# Architecture

How spire is put together, and why.

## The one idea

**The plugin is the game engine. The target repo holds the save file.**

spire (this repo) contains rules, classes, and scripts — and *zero
knowledge about any specific project*. Everything it learns about a project is
written **into that project**: `CLAUDE.md` rules, `.claude/skills/` cards, and a
`.spire/deck.json` save file. This isn't just thematic; it's forced by the
platform: a plugin's own `CLAUDE.md` is not loaded as project context, so the
engine must *write* config into repos rather than carry it.

**Run knowledge vs agent primitives:** `.spire/` is the run home (durable save
+ disposable bookkeeping + self-contained helpers). Agent primitives the
platform must load — skills, settings hooks — stay under `.claude/` (and
`CLAUDE.md`). The deal step is the bridge.

```
 ENGINE (this repo)                        TARGET REPO
 ┌────────────────────────┐   /spire       ┌────────────────────────┐
 │ scan.py   (detect)     │  ─────────────▶│ CLAUDE.md   (relics)   │
 │ deck.py   (save I/O)   │                │ .claude/skills/ (cards)│
 │ paths.py  (.spire I/O) │                │ .claude/settings.json  │
 │ classes/  (data)       │                │   (ascension hook only)│
 │ skills/   (commands)   │  ◀─────────────│ .spire/                │
 │ hooks/    (the loop)   │  ◀─(Stop/etc.)─│   deck.json (save)     │
 └────────────────────────┘                │   state.json (ephemeral)│
                                           │   pending-reward.json  │
                                           │   ascension.json       │
                                           │   bin/ (dealt helpers) │
                                           └────────────────────────┘
```

The engine's *own* hooks (`hooks/hooks.json`) run whenever the plugin is
enabled, in any repo. What they write into a target repo — dealt cards' own
hooks, `ascension_gate.py` — is deliberately **self-contained**: it must keep
working even if spire is later uninstalled, so it never imports the
engine, only stdlib.

## Deterministic before generative

The spine of the design: mechanical work is done by deterministic Python; the
model only judges where determinism can't reach — and even that judgment is
gated so it almost never runs.

- `scan.py` **detects** the stack (no LLM, no network, stdlib only).
- `deck.py` **validates and writes** the save file atomically (stdlib only).
- `paths.py` **owns** `.spire/` locations and migrates a pre-spire `.claude/`
  save layout once, idempotently.
- `reward_gate.py` **decides whether it's worth asking at all** (a new commit,
  or enough activity) before ever invoking a model (stdlib only).
- `curator.py` **judges** a reward offer — the one script with a soft,
  optional dependency (`claude-agent-sdk`), because judgment inherently needs
  a model. It degrades to "skip" if the dependency is absent or anything
  fails; the reward loop is a bonus, never a point of failure.
- The `/spire` skill **judges** which class(es) to deal and assembles
  the cards; `/spire:ascend` reads class YAML for lint/test commands.

A consequence: the scripts never parse the class YAML. The skills read the
class files as prompt content, so the scripts stay dependency-free and portable.

## Components

### `scripts/scan.py` — the detector
Walks the target repo (pruning `.git`, `node_modules`, `.venv`, build/caches) and
scores five classes from marker files, directory names, dependency manifests, and
file-extension prevalence:

| Class | Family | Signals |
|---|---|---|
| defect | python | `pyproject.toml`, `setup.py`, `requirements*.txt`, `Pipfile` |
| silent | javascript | `package.json`, `tsconfig.json`, `*.ts`, framework configs |
| ironclad | infra | `Dockerfile`, `*.tf`, `terraform/`, `docker-compose*` |
| watcher | python | `*.ipynb`, `notebooks/`, `models/`, ML dependencies |
| colorless | none | (fallback when nothing scores) |

It emits JSON: `{primary, classes[], families[], monorepo, scores, signals}`.
Multiple classes within one family (e.g. a Python ML repo → `watcher` + `defect`)
are reported together but are **not** a monorepo; strong signals across two or
more *families* set `monorepo: true`, which becomes a dual-class run.

### `scripts/deck.py` — the save file
Owns `<repo>/.spire/deck.json`. Subcommands: `init`, `add-card`, `add-relic`,
`add-power`, `remove-card`, `remove-relic`, `record-play`, `mark-offered`,
`mark-taken`, `mark-skipped`, `show`, `stats`, `validate`. Writes are atomic
(temp file + `os.replace`) and `init` is idempotent (refuses to re-deal
without `--force`). `show`/`stats` back `/spire:map`. Removing a skill card
also deletes `.claude/skills/<name>/` so a pruned card cannot still load.

### `scripts/paths.py` — run home
Centralizes `.spire/` paths and migrates legacy deck-builder files that lived
under `.claude/` (`deck.json`, `deck-builder-state.json`,
`deck-pending-reward.json`, `deck-builder-ascension.json`,
`.claude/deck-builder/*.py`). Prefer existing `.spire/` files; never clobber.
Also rewrites a legacy ascension Stop-hook command in `.claude/settings.json`
to point at `.spire/bin/ascension_gate.py`.

### `classes/*.yaml` — the archetypes (data)
Each class file declares `detected_by` signals, `flavor`, a `commands`
block (`lint`/`test` — `null` where no command is universal enough to
enforce, e.g. Colorless), `relics` (CLAUDE.md rules), and `cards` (each a
`name` + `description` + SKILL.md `body`). This is the primary contribution
surface — adding an archetype is a data change, not a code change.

### `skills/` — the commands and rubrics
- `spire/` → `/spire` (Neow's blessing: scan → deal), user-invoked.
- `map/` → `/spire:map` (run state + `deck.py stats`), user-invoked.
- `campfire/` → `/spire:campfire` (resolve a pending reward, or review
  the deck via the `deck-curator` agent), user-invoked.
- `ascend/` → `/spire:ascend` (the A0–A20 ladder), user-invoked.
- `card-evaluation/`, `deck-state/` → model-invoked rubrics that guide Claude when
  judging what belongs in a deck and how to touch `deck.json` safely.

Skills reference bundled scripts via `${CLAUDE_SKILL_DIR}/../../scripts/…` and the
target repo via `${CLAUDE_PROJECT_DIR}`, so paths stay install-independent.

### `agents/deck-curator.md` — the interactive reviewer
A cheap-model (`haiku`) subagent `/spire:campfire` delegates to for a
conversational deck review (which unplayed cards look safe to prune). Distinct
from `curator.py`: the agent is for *interactive* campfire review; the script
is for the *automated*, headless Stop-hook reward judgment. Same house rules,
two different invocation mechanisms suited to their trigger context.

## The reward loop (Act 2)

```
 every Stop  ──▶  activity_log.py (PostToolUse)      cheap counter, no LLM
                        │
 enough Stops ──▶ reward_gate.py (Stop)               deterministic gate:
                        │                              new commit, or activity
                        │                              past a threshold?
                        ▼
                  curator.py                          claude-agent-sdk,
                  (soft dependency)                    cheap model, tool-free,
                        │                               schema-enforced JSON
                        ▼
          .spire/pending-reward.json                   written only on "offer"
                        │
   next SessionStart ──▶ status_line.py                surfaces it quietly
   or /spire:campfire ──▶ accept/skip                  never interrupts Stop
```

- **`activity_log.py`** (PostToolUse): if `.spire/deck.json` exists, bumps a
  counter in `.spire/state.json` (capped, never unbounded). A
  silent no-op in any repo without a dealt deck.
- **`reward_gate.py`** (Stop): the deterministic gate. A candidate check fires
  on a new commit since last check, or activity past `ACTIVITY_THRESHOLD`
  (lower — effectively 1 — once ascension reaches A20, so every room gets
  reviewed instead of a sample). On a candidate, it gathers a bounded git diff
  (stat + real content, so the curator can see a *repeated pattern*, not just
  a line count) and calls `curator.judge()`. Always resets its window
  afterward and **never blocks Stop**, wrapped in a top-level catch-all.
- **`curator.py`**: one cheap-model (`claude-haiku-4-5`), tool-free,
  `max_turns=1` call via `claude-agent-sdk`, with `output_format` set to a
  JSON schema so the response is structurally guaranteed
  (`{recommend, reason, offer[], remove[]}`). House rules baked into the
  system prompt: default skip; offer only for a genuinely repeated pattern;
  past a ~12-card soft cap, every offer must name a card to remove; at most 3
  cards. Any failure — missing dependency, timeout, malformed output —
  degrades to `{"recommend": "skip", ...}`, never an exception.
- **Play tracking**: dealt cards aren't fixed tool names, so `PostToolUse`
  can't attribute a play to a specific card. Instead, `/spire` embeds a
  **skill-scoped `Stop` hook** in each dealt card's own SKILL.md frontmatter
  (Claude Code hooks can live in a skill's frontmatter, "scoped to the
  component's lifecycle" — they fire only while that skill is active),
  pointing at `.spire/bin/record_play.py`. That script is dealt
  (copied) into the target repo once, self-contained, so it keeps crediting
  plays even without the engine installed.

## The ascension ladder (Act 3)

`/spire:ascend` reads the deck's class(es) YAML for `commands.lint`/
`commands.test` (a skill responsibility — scripts don't parse YAML), then
calls `scripts/ascend.py`, which:

1. **Merges** (never overwrites) a `Stop` hook into the target repo's own
   `.claude/settings.json`, identifying "our" prior entry by a marker in its
   command string so re-ascending replaces it rather than duplicating, and
   de-escalating to A0 removes only that one entry — everything else already
   in `settings.json` (a user's own permissions, other plugins' hooks) is left
   exactly as found. The command points at `.spire/bin/ascension_gate.py`.
2. **Writes** `.spire/ascension.json` (tier + resolved
   lint/test commands + a coverage baseline) — the self-contained
   `ascension_gate.py`'s only config source.
3. **Updates** `deck.json.ascension`.

`ascension_gate.py` (dealt into `.spire/bin/`, no engine
dependency) is the actual gate, run as that Stop hook:

| Tier | Enforces |
|---|---|
| A0 | nothing (not wired in) |
| A5 | blocks if `lint_cmd` fails |
| A10 | A5, + blocks if `test_cmd` fails |
| A15 | A10, + blocks on a coverage regression, parsed from test output with a regex tolerant of common `coverage`/pytest-cov layouts (`TOTAL <stmts> <miss> NN%`); no coverage number found → that check silently no-ops |
| A20 | A15 (the "review every room" half of A20 lives in `reward_gate.py`'s lowered threshold, not here — it's a sampling decision, not a pass/fail gate) |

A missing/unset command for a tier's check is always a silent no-op for that
specific check, never a block — the gate never pretends to enforce something
it can't actually verify. Any unexpected internal error also fails open.

## `deck.json` schema (v1)

```json
{
  "schema_version": 1,
  "class": "defect",
  "classes": ["defect"],
  "act": 1, "floor": 0, "ascension": 0,
  "created": "YYYY-MM-DD",
  "cards": [{"name": "…", "type": "skill", "added_floor": 0, "plays": 0, "last_played": null}],
  "relics": ["…"],
  "powers": [{"event": "…", "name": "…"}],
  "rooms_cleared": [],
  "clean_room_streak": 0,
  "rewards": {"offered": 0, "taken": 0, "skipped": 0}
}
```

`rewards.taken / rewards.skipped` is a deck-health signal — a high skip rate is
healthy. `ascension` (0–20) is raised only by `/spire:ascend`, never
silently; `clean_room_streak` is reserved for a future opt-in auto-raise (still
unused today — ascension stays manual).

### Ephemeral files (not part of the versioned save)

Separate from `deck.json` on purpose — disposable bookkeeping, not a run a
player would want preserved or would expect to hand-edit:

- `.spire/state.json` — Stop-hook window (last checked commit,
  activity count since).
- `.spire/pending-reward.json` — a card offer awaiting a
  `/spire:campfire` decision; deleted once resolved either way.
- `.spire/ascension.json` — the ascension gate's config (committed;
  regenerable via `/spire:ascend`).

## Extending

- **Add a class:** create `classes/<name>.yaml` (including a `commands`
  block) and register detection/display metadata in
  `classes/detection.json`. `scan.py` / `deck.py` load that file — no Python
  edits for markers or display names. `tests/test_classes.py` enforces that
  the data agrees.
- **Add a card pack:** add `packs/<name>/pack.yaml` (see `packs/README.md`);
  `/spire:shop` deals from it. `scripts/pack.py list` indexes packs without
  parsing YAML.

## The game client

Acts 1-3 gave the engine a save, a reward loop and an ascension ladder. What it
never had was the *run*: a map, a room you enter, a hand you spend, a floor that
ticks. That layer exists now, and it is split across three pieces.

```
 Claude Desktop / claude.ai / VS Code / Cursor        Claude Code (terminal)
              │  renders ui://spire/app.html                    │  reads content[0].text
              ▼                                                 ▼
        ┌──────────────────────────────────────────────────────────┐
        │  server/  —  spire-mcp (Rust, rmcp)                      │
        │  protocol + presentation. Owns no game state.            │
        └──────────────────────────────┬───────────────────────────┘
                                       │ python3 scripts/run.py <verb> --json
                                       ▼
        ┌──────────────────────────────────────────────────────────┐
        │  scripts/run.py  —  the run loop (stdlib)                 │
        │  reads content/*.json, mutates .spire/deck.json via deck.py│
        └──────────────────────────────────────────────────────────┘
```

**`scripts/mapgen.py`** generates the climb: a 15x7 grid walked by six paths,
quota-bag room assignment, unknown-node resolution with per-outcome ramp
counters, and per-floor RNG isolation via `seed + floor`. It is pure arithmetic
over a seed, which is why `mapgen.py verify` can check hundreds of maps against
the invariants in one command.

**`scripts/run.py`** is the run loop — enter, play, acceptance, clear, flee,
reward, campfire, shop, annotate. Every subcommand takes `--path` and prints
JSON; failures come back as `{"ok": false, "error": {...}}` with exit 0, because
the client must never brick on a bad verb. It owns the single-room lock, the
energy budget, card legality and the reward roll. It carries no HP, no damage
math and no fabricated intents — see `design/spire-ai/sts-fidelity.md` for why
each of those is a refusal rather than an omission.

**`server/`** is a Rust crate using the official `rmcp` SDK. It declares one
MCP Apps resource, `ui://spire/app.html` with mimeType
`text/html;profile=mcp-app`, and fifteen tools that each carry
`_meta.ui.resourceUri` pointing at it. Every tool result carries the state three
ways at once: a drawn terminal rendering in `content[0].text`, the machine
payload in `structuredContent`, and the view reference in `_meta`. One call,
both surfaces, no host detection — which matters because **Claude Code is not on
the MCP Apps support matrix** and is the plugin's primary host.

Tools split by visibility. `spire_play_card`, `spire_list_hand`,
`spire_end_turn` and `spire_annotate_node` are `["app"]`-only: they fire on
every click, and keeping them off the model's tool list is what stops the agent
narrating each card play.

**`app/`** is the client — one self-contained HTML document with the stylesheet,
the script and three subset woff2 faces inlined as data URIs. That is what lets
the resource declare an empty CSP allowlist: there is genuinely nothing to
allow. `tools/build-app.mjs` bundles it; `server/build.rs` makes cargo rebuild
when it changes.

### Backgrounds

The same constraint shapes the art. With no external origin available there can
be no image assets, so every background is generated at runtime as inline SVG
and CSS gradients — which is why the system is a grammar rather than an asset
pipeline. `content/scenes.json` declares each place as layers, zones, safe areas
and a component grammar; `app/scene.js` composes it; `tools/scenes.mjs` renders
the labelled wireframe *from the same object*, so the spec cannot drift from the
art.

The client has no RNG here either. Following `mapgen.unknown_rolls`, the engine
pre-rolls a fixed float vector per scene — `run.py`'s `scene_table`, seeded from
the run seed and the act — and ships it on the map payload, which is the payload
whose lifetime matches. Re-entering a floor is not similar to last time; it is
the same picture.

Legibility is bought with contrast, not geometry: the content column covers most
of the frame, so there is nowhere to draw around the text. Each layer declares
how far it may step from the void, and a shape crossing a safe rectangle has that
step attenuated. `tools/shoot.mjs` samples real pixels inside every safe
rectangle and fails the run if the background steps harder than the declared
ceiling.

## Status

Acts 1–3 (the starter deck, the reward-loop engine, and the ascension ladder)
ship in this repo today. The Heart has started: one bundled pack
(`testing-discipline`), `/spire:shop`, room clears that advance `floor`,
starter powers on the non-colorless classes, and data-driven detection via
`classes/detection.json`. More community classes and deck export are still
planned — see the roadmap in [README.md](README.md).
