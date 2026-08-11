# AGENTS.md

Guidance for AI coding agents working in this repository. This mirrors
[`CLAUDE.md`](CLAUDE.md) for tools that read the cross-agent `AGENTS.md`
convention; when the two ever differ, `CLAUDE.md` wins for Claude Code.

## What this repo is

The **game engine** for the `spire` Claude Code plugin. It holds rules,
classes, and scripts — and **zero project knowledge**. What the plugin learns
about a project is written *into that project*, never stored here. See
[ARCHITECTURE.md](ARCHITECTURE.md).

## Rules of the road

- **Scripts are pure standard library.** `scripts/scan.py`, `scripts/deck.py`,
  `scripts/ascend.py`, `scripts/mapgen.py`, `scripts/run.py`, and everything
  dealt into target repos (`record_play.py`, `ascension_gate.py`) must import
  only the stdlib (no third-party runtime deps) so they run in any repo's
  environment. **Two documented exceptions, and only two.** `scripts/curator.py`
  has a *soft* dependency on `claude-agent-sdk`, because judgment inherently
  needs a model, and it degrades to "skip" cleanly if it is absent. And
  `server/` is a Rust crate — the MCP server is a protocol implementation, not a
  script, and hand-rolling JSON-RPC to avoid `rmcp` would have been dogma rather
  than discipline. The rule it still obeys: the server owns **no game state**.
  Every rule lives in `run.py`; Rust does protocol and presentation only.

- **Classes are data.** Archetypes live in `classes/*.yaml`; the scripts do not
  parse them (the `/spire` skill does). New archetypes and card packs are
  the intended contribution surface — markdown and YAML, not Python.
- **Deterministic before generative.** `scan.py` detects, `deck.py` validates;
  reserve model judgment for choosing classes and assembling cards.
- **Portable paths only.** In skills, reference bundled files via
  `${CLAUDE_SKILL_DIR}` and the target repo via `${CLAUDE_PROJECT_DIR}` — never
  hardcode absolute or install-specific paths.
- **Don't clobber user work.** Appending beats overwriting; `deck.py init` refuses
  to re-deal over an existing `deck.json`.
- **Run vs agent dirs.** `.spire/` holds run knowledge (save, bookkeeping,
  dealt helpers). `.claude/skills/` holds cards the agent loads. Don't put
  skills inside `.spire/`.
- **Detection is data.** Class markers and display names live in
  `classes/detection.json`; packs live under `packs/<name>/pack.yaml`.

## Checks before you commit

```bash
python3 -m pytest tests/          # scan + deck + class-schema + manifest tests
ruff check scripts/ tests/        # lint (matches the repo's ruff-strict relic)
claude plugin validate .          # manifest + skill frontmatter (needs the CLI)
```

## Cursor Cloud specific instructions

This is a pure-stdlib Python plugin engine — no build step, no services, no
database. "Running the app" means exercising the engine CLIs and hooks.

- **Dev deps** (`pytest`, `ruff`, `pyyaml`) are installed by the environment
  update script via `pip install`. `pip` drops console scripts in
  `~/.local/bin`, which is **not on `PATH`**. Invoke tools as modules to avoid
  this: `python3 -m pytest tests/` and `python3 -m ruff check scripts/ tests/`
  (the bare `ruff` command may be "not found").
- **Test/lint** commands are in `CLAUDE.md` / `CONTRIBUTING.md`. One test
  (`tests/test_curator_sdk_mocked.py`) is *skipped* unless the optional
  `claude-agent-sdk` (and its `anyio` dep) is installed — this is by design; a
  single skip is expected, not a failure.
- **Exercise the engine** with the stdlib CLIs, e.g.
  `python3 scripts/scan.py <repo>` (detect class) then
  `python3 scripts/deck.py init|add-card|show|validate --path <repo>` (deal /
  inspect a save file). `deck.py init` refuses to overwrite an existing
  `.spire/deck.json`, so deal into a fresh dir.
- **`claude plugin validate .`** needs the Claude Code CLI
  (`npm i -g @anthropic-ai/claude-code`) and is optional; structural checks are
  duplicated in `tests/test_plugin_manifests.py`, which runs with no CLI.
