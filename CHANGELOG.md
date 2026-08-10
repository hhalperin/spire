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
- **`server/`** — `spire-mcp`, an MCP server on the official Rust SDK (`rmcp`).
  Serves `ui://spire/app.html` as an MCP Apps resource and fifteen tools, four of
  them app-only so the agent does not narrate every card play. Every result
  carries a drawn terminal rendering *and* structured content, so Claude Code —
  which cannot render MCP Apps — plays the same game as Claude Desktop.
- **`app/`** — the client. One self-contained HTML document: dark torchlit theme
  by default with the original paper palette as the light theme, the Slay the
  Spire 2 intent taxonomy bound to deterministic sensors, parchment card frames,
  a vertical climb, map annotation, run badges, and full keyboard play. Fonts are
  subset and inlined, so the resource declares an empty CSP allowlist.
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

### Fixed

- **Event choices applied nothing.** The client rendered every consequence in
  `content/events.json` — curses, focus, relics — and then cleared the room
  without sending the choice id, so none of it ran. The choice now *is* the
  clear: `spire_clear_or_flee` takes a `choice`, `run.py` implements all ten
  effect verbs, gates refuse when unmet, and the client renders an unmet gate as
  locked. A test now fails if content ever uses a verb nothing implements.
- **A relic gained at an event never persisted.** `deck.py` subcommands rewrite
  the save from disk, so anything mutated in memory beforehand was discarded by
  the reload after — the resolution text said you gained the relic and the next
  load disagreed. Every reload now goes through one method that carries the
  fields this module owns across, so the whole class is closed rather than the
  one instance.
- **The map went stale after a room ended.** `clear`, `flee`, `reward` and
  `campfire` returned state without a map, so the client kept rendering
  reachability from before the room was cleared. Every verb that can move you now
  returns a fresh map.
- **The map could not scroll.** `.map-canvas` set `overflow-y: auto` and then
  `overflow: hidden` for the border radius; the shorthand won, clipping the
  climb and defeating the scroll-to-your-position code.
- A resolved unknown node that became an event rendered the literal string
  `undefined` on the demo map: `GLYPH` had no `event` key while `resolveUnknown`
  defaulted to `"event"`.
- The demo's "Refresh prior" button had no handler and silently did nothing.
- `mapgen.py` rendered `M/E/R/$/T/?` while every other surface used
  `✦/✸/▲/◆/▮/◇/☠`. One glyph vocabulary now, from ENTITY_STANDARDS.

### Changed

- The design system's default flipped from paper to stone-and-ember; the paper
  palette is now the light theme, with every recorded contrast decision intact.
- The map climbs. It used to run left-to-right, which read as a flowchart.

### Changed
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
