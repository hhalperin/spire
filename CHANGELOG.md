# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — the game client (beta)

- **`scripts/run.py`** — the headless run loop: enter a room, spend energy, play
  a card, run acceptance, clear or flee, resolve a reward, smith or prune, buy,
  annotate the map. Stdlib only, JSON in and out, and it fails open with a
  structured error rather than a traceback. Enforces the single active room.
  An event's choice *is* its clear — `spire_clear_or_flee` takes a `choice`, ten
  effect verbs are implemented, and unmet gates refuse with a reason — and every
  verb that can move you returns a fresh map alongside the state. Every reload
  goes through one method that carries this module's fields across a `deck.py`
  write, so a relic gained at an event cannot be discarded by the reload after.
- **`server/`** — `spire-mcp`, an MCP server on the official Rust SDK (`rmcp`).
  Serves `ui://spire/app.html` as an MCP Apps resource and fifteen tools, four of
  them app-only so the agent does not narrate every card play. Every result
  carries a drawn terminal rendering *and* structured content, so Claude Code —
  which cannot render MCP Apps — plays the same game as Claude Desktop.
- **`app/`** — the client. One self-contained HTML document: dark torchlit theme
  by default with the original paper palette as the light theme, the Slay the
  Spire 2 intent taxonomy bound to deterministic sensors, parchment card frames,
  a vertical climb, map annotation, run badges, and full keyboard play. Fonts are
  subset and inlined, so the resource declares an empty CSP allowlist. The climb
  scrolls: `.map-canvas` gets `overflow-x: hidden` rather than the `overflow`
  shorthand, which would have won and clipped it.
- **`content/{enemies,cards,events,shop,objects}.json`** — the content pools,
  lifted out of `demo.js`. This pays off the debt `ENTITY_STANDARDS.md` rule 8
  had been recording against the project since it was written.
- **`game` block in `deck.json`** — additive, validated only when present, so
  saves dealt before the client existed keep loading unchanged.
- **`tools/`** — `build-app.mjs` bundles the client, `build-fonts.py` subsets and
  inlines the type, `host/` is a minimal MCP Apps host for looking at the client,
  and `shoot.mjs` drives the shipping client through a whole run and photographs
  every screen in dark, light, narrow, greyscale and reduced-motion — failing if
  any screen does not render what its format doc promises.
- **`design/spire-ai/sts-fidelity.md` Part 6** — the Slay the Spire 2 ledger,
  one verdict per system.

### Added — scene backgrounds

- **`content/scenes.json`** — a wireframe per scene, as data: nine layers, ten
  places, eighteen components, five biomes, and the safe rectangles content sits
  in. It is not documentation about the backgrounds; it is their source.
- **`app/scene.js` + `app/scene.css`** — the composer. Silhouette layers are
  generated SVG, atmosphere layers are CSS gradients, and `mountScene` weaves
  them in the declared order so the fog genuinely sits between the distance and
  the architecture. No image assets, because an MCP App has no external origin to
  fetch one from — which is why this is a grammar and not an asset pipeline.
- **Per act × scene variation, with no client RNG.** `mapgen.scene_rolls` seeds a
  float vector from the run seed and the act; `run.py` ships it on the map
  payload. Re-entering a floor is not similar to last time, it is the same
  picture. Act I's chamber and Act III's are the same room in different worlds.
  The length is *derived* — `mapgen.scene_budget` reads it off the scene's own
  grammar and each component's declared roll cost, so nobody maintains a number
  that can disagree with what the builders draw. `tools/scenes.mjs` runs every
  builder at the extremes of every range and fails the build if one outdraws its
  declaration, which is a check only JavaScript can make.
- **`tools/scenes.mjs`** — the gallery: every scene as a labelled wireframe and
  as composed art, per act, drawn from the same object the client reads, using
  rolls from the same function the engine ships.
- **A legibility check on real pixels.** `tools/shoot.mjs` photographs the
  composed background inside every safe rectangle and fails if the local
  luminance step exceeds the ceiling `content/scenes.json` declares. The first
  time it ran it flagged the demo host's own iframe border, which was the check
  working.
- **`## Background` in every format doc** and in `templates/facet-format.md`,
  because a facet with no answer for where it stands is how text ends up on a
  busy patch.

### Fixed

- A resolved unknown node that became an event rendered the literal string
  `undefined` on the demo map: `GLYPH` had no `event` key while `resolveUnknown`
  defaulted to `"event"`.
- The demo's "Refresh prior" button had no handler and silently did nothing.
- `mapgen.py` rendered `M/E/R/$/T/?` while every other surface used
  `✦/✸/▲/◆/▮/◇/☠`. One glyph vocabulary now, from ENTITY_STANDARDS.

### Changed
- **A click costs ~18% less.** `engine.rs` spawns a Python process per tool
  call, so import cost is paid per click — it was ~80% of one. `mapgen` no
  longer imports `dataclasses` (which drags in `inspect`, ~10ms, for two class
  definitions): `Node` is a `NamedTuple` with the same field syntax and
  `SpireMap` a plain class. `acceptance` imports `subprocess` in the one branch
  that shells out rather than at module scope. Measured A/B against the previous
  commit: `state` 64.5→51.7ms, `map` 68.9→57.5ms, `badges` 67.1→54.4ms.
  `test_run_modules.py` holds both out, since the cost is invisible in review.
- **One declaration per thing that used to have two.** The CLI parser and the
  dispatch table are both derived from a single `VERBS` row per subcommand, so a
  verb can no longer exist in `HANDLERS` with no way to reach it. Event effects
  are a dispatch table rather than an eighty-line `if/elif`, which makes the set
  of implemented verbs readable by a test — the one that checks content against
  the engine was comparing it to a hand-typed list of ten, and would have stayed
  green if a verb here were renamed while content still used the old name.
  `card_by_id` / `object_by_id` are dict lookups against an index built once,
  rather than list scans repeated a few hundred times per `state`.
- `mapgen.SpireMap` gained `remember` / `recall` for resolved unknown nodes.
  `runstate` was assigning into `smap._unknown` from another module.
- **The run loop is seven modules instead of one 1562-line file.**
  `scripts/run.py` keeps the CLI, the dispatch table and the fifteen verbs; the
  rules moved to flat siblings — `gamedata` (content lookup), `runstate`
  (`RunError` + `Run`), `rooms` (a room from a node, and the hand for it),
  `acceptance` (the deterministic sensor), `events` (choices and their effects),
  `rewards` (offers, chest draws, removal, badges) and `serialize` (the client's
  payloads). Imports run one way and `run.py` re-exports every name it moved, so
  nothing that reached into it before has to change. No behaviour change: the
  same tests pass, none edited.
- CI now lints `tools/` alongside `scripts/` and `tests/`, and checks that the
  committed `docs/scenes.html` matches `app/scene.js` — it was generated to
  `/tmp`, so the gallery had drifted a composer rewrite behind the client.
- The design system's default flipped from paper to stone-and-ember; the paper
  palette is now the light theme, with every recorded contrast decision intact.
- The map climbs. It used to run left-to-right, which read as a flowchart.
- Rebranded the plugin from `deck-builder` to `spire`; commands are now
  `/spire`, `/spire:map`, `/spire:campfire`, `/spire:shop`, and `/spire:ascend`.
- Moved the run home to `.spire/` (`deck.json`, `state.json`,
  `pending-reward.json`, `ascension.json`, `bin/`). Agent primitives
  (skills, settings hooks, CLAUDE.md relics) stay under `.claude/` /
  `CLAUDE.md`. Legacy `.claude/deck.json` layouts migrate automatically.
- Class detection markers and display names live in `classes/detection.json`
  (loaded by `scan.py` / `deck.py`) so new archetypes need no Python edits
  for detection.

### Added
- Room clears: `reward_gate` advances `floor` / `rooms_cleared` /
  `clean_room_streak` whenever a candidate room is judged (offer or skip);
  `deck.py clear-room` for the same bookkeeping.
- First Heart pack: `packs/testing-discipline`, `scripts/pack.py` list/path,
  and `/spire:shop` to draw pack cards/relics.
- Starter powers on Defect, Silent, Ironclad, and Watcher (recorded in
  `deck.json.powers` at deal time).
- Open-source project files: SECURITY, CODE_OF_CONDUCT, ARCHITECTURE, AGENTS, CHANGELOG, packs/, GitHub issue and PR templates, a CI workflow, and repo hygiene configs.
- GitHub best-practice scaffolding: CODEOWNERS, Dependabot, a Python 3.9/3.12 CI matrix, a pre-commit job enforcing the existing .pre-commit-config.yaml, and an optional claude plugin validate CI job.
- Project-level .claude/settings.json: a safe read-only/test/lint permissions allowlist and a non-blocking PostToolUse ruff hook.
- Act 2 (the engine): a Stop-hook reward loop (reward_gate.py + curator.py using claude-agent-sdk on a cheap model with schema-enforced JSON output), per-card play tracking via skill-scoped Stop hooks (record_play.py), and /spire:campfire (accept/skip a pending reward, or prune via the new deck-curator agent).
- Act 3 (ascension): the A0-A20 ascension ladder via /spire:ascend and ascend.py (merges a Stop hook into the target repo's own .claude/settings.json without touching anything else there), the self-contained ascension_gate.py (lint/test/coverage-regression gate), and deck.py stats for deck-health numbers.
- Per-class lint/test commands in classes/*.yaml, used by the ascension ladder to generate real gate commands (null where no command is universal enough for a class).

## [0.1.0] - 2026-07-24

### Added
- Act 1 (MVP): /deck-builder deals a class-based starter deck, a deterministic scan.py stack detector, five class archetypes, a deck.json save file, and /deck-builder:map.
