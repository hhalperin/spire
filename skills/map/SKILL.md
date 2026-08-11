---
description: Show the current spire run — the climb map with reachable nodes, plus class, act/floor, ascension, cards with play counts, relics, and the reward ratio.
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_SKILL_DIR}/../../scripts/deck.py" *), Bash(python3 "${CLAUDE_SKILL_DIR}/../../scripts/run.py" *)
---

# /spire:map — the run map

Render the current run for this project: where you are on the climb, and what
the deck looks like.

## 1. The climb

If the `spire` MCP server is connected, call its **`spire_map_refresh`** tool and
show its text output verbatim. That output is the drawn map — the branching
graph with the boss at the top, cleared nodes dimmed, and only the reachable
nodes bracketed — and re-rendering it in prose loses the shape, which is the
whole point of a map.

If the server is not connected (it needs a one-time
`cargo build --release --manifest-path server/Cargo.toml`), fall back to:

```
python3 "${CLAUDE_SKILL_DIR}/../../scripts/run.py" --path "${CLAUDE_PROJECT_DIR}" map
```

and describe the position from the JSON: the act, the floor, which node ids are
`legal`, and what kind each of those is. Say plainly that the drawn map needs the
game server built — do not pretend the prose version is the same thing.

## 2. The deck

Then run both of these, in this order:

```
python3 "${CLAUDE_SKILL_DIR}/../../scripts/deck.py" show  --path "${CLAUDE_PROJECT_DIR}"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/deck.py" stats --path "${CLAUDE_PROJECT_DIR}"
```

`stats` is the deterministic health read — unplayed cards, the most-played card,
soft-cap status, and the reward take rate — rather than eyeballing it from
`show`'s raw list.

## 3. Present it as one summary

Map first, deck second. Name the live deck count against the soft cap, because a
deck cost you cannot see is one that never makes refusing a card feel like a win.

If there is no deck yet, tell the user to run `/spire` first to deal a starter
deck.

**Do not modify anything here — `/spire:map` is read-only.** Entering a node is a
commitment; it belongs to the client or to an explicit `spire_enter_node` call,
never to a command whose job is to show you where you stand.
