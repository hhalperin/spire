# CLAUDE.md — spire engine

This repo is the **game engine** for the `spire` Claude Code plugin. It
contains rules, classes, scripts, and skills — and **zero project knowledge**
about any target repo. The knowledge spire produces is *written into other
repos*, never carried here.

## Mental model

| spire term | Reality |
| :-- | :-- |
| Deal a deck | Write `CLAUDE.md` + `.claude/skills/` + `.spire/deck.json` into a target repo |
| Card | A skill (`.claude/skills/…/SKILL.md`) |
| Relic | A `CLAUDE.md` rule |
| Power | A hook |
| Class | A repo archetype detected by `scan.py` |
| Save file | `.spire/deck.json` in the target repo (the `game` block is the client's) |
| Client | The MCP App at `ui://spire/app.html` — map, room, hand, reward |
| Reward | A card offer judged by `curator.py`, surfaced at `/spire:campfire` |
| Ascension | The A0–A20 strictness ladder (`/spire:ascend`) |

**Engine vs save file:** this repo is the engine. The save file lives with the
project being decked, under `.spire/`. Agent primitives the platform must load
(skills, settings hooks) stay in `.claude/`. Keep that separation — nothing
here should hard-code facts about a specific target project.

## Conventions

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
- **Scripts don't parse class YAML.** The `/spire` skill reads the class
  files itself; the scripts stay data-format-agnostic. PyYAML is a *test-only*
  convenience (see `tests/test_classes.py`, which skips if it's absent).
- **Classes are data, not code.** Add archetypes as `classes/<name>.yaml`. The
  contribution surface is markdown and YAML — that's deliberate.
- **Deterministic before generative.** `scan.py` detects, `deck.py` validates;
  the model only judges (which class, which cards) where determinism can't.
- **Skills reference bundled files via `${CLAUDE_SKILL_DIR}`** and target the repo
  via `${CLAUDE_PROJECT_DIR}` — never absolute or install-specific paths.

## Layout

```
.claude-plugin/   plugin.json + marketplace.json (ONLY these live here)
skills/           spire, map, campfire, shop, ascend (commands);
                  card-evaluation, deck-state (model-invoked rubrics)
agents/           deck-curator.md — interactive campfire reviewer (haiku)
hooks/            hooks.json — the engine's own PostToolUse/Stop/SessionStart
scripts/          scan.py, deck.py, ascend.py, paths.py, pack.py, mapgen.py,
                  run.py — stdlib only. run.py is the headless run loop the
                  client drives; mapgen.py generates the climb
                  curator.py — soft-dependency exception (claude-agent-sdk)
                  record_play.py, ascension_gate.py — dealt into target repos
                  under .spire/bin/, self-contained, no engine imports
server/           spire-mcp — the Rust MCP server (rmcp). Protocol + terminal
                  rendering only; shells out to run.py for every rule
app/              the MCP App client: index.html, app.css, app.js, bridge.js,
                  fonts.css (generated). Bundled to server/assets/app.html
tools/            build-app.mjs, build-fonts.py, shoot.mjs, host/ — dev only
classes/          archetypes as YAML + detection.json (stdlib-loaded markers)
content/          run content as JSON — bosses, enemies, cards, events, shop,
                  objects. Loaded by mapgen.py and run.py; never inline in code
packs/            community card packs (pack.yaml; dealt via /spire:shop)
tests/            pytest over the scripts + class schema + manifests
```

## Develop

- Run tests: `python3 -m pytest tests/`
- Build the client + server: `node tools/build-app.mjs && cargo build --release --manifest-path server/Cargo.toml`
- Look at the client: `node tools/host/serve.mjs` then open the printed URL
- Re-shoot every screen and check it: `node tools/shoot.mjs`
- Rust checks: `cargo test` and `cargo clippy -- -D warnings`, both under `server/`
- Try the detector: `python3 scripts/scan.py <some-repo>`
- Validate the plugin (needs the Claude Code CLI): `claude plugin validate`,
  then `claude --plugin-dir ./` and `/help` should list `/spire`.
- Reward-loop tests mock `claude_agent_sdk`; they never make a live API call.
  Installing `claude-agent-sdk` locally additionally runs
  `tests/test_curator_sdk_mocked.py`'s async-plumbing coverage (skipped
  without it, like `tests/test_classes.py` skips without PyYAML).

## spire relics

<!-- Dogfood: this repo runs its own deck (class: defect). These relics were
     dealt by /spire and apply to the engine's own Python code. -->

- Lint and format with Ruff; resolve every warning before committing.
- Type-hint public functions and keep the type checker (mypy/pyright) clean.
- Never ship mock, stub, or placeholder data in production code paths.
