# Wedge — build order

Ship truth at each step. Do not build the company before the loop is fun.

## Stage 0 — Engine (now)

**In this repo today:** scan, deal, `.spire/` save, room clears, campfire, shop, packs, powers, ascension, detection.json.

Done means: plugin dogfoods itself; Heart docs match behavior.

## Stage 1 — Design kit (this folder)

Contracts frozen enough to implement against:

- [x] GDD  
- [x] Glossary  
- [x] Room-prior / `?` contract  
- [x] Content schema  
- [x] MCP client surfaces  
- [x] Non-goals  

## Stage 2 — Vertical demo (“Ship the stub”)

One act, one boss, no marketplace.

| Slice | Deliverable |
| --- | --- |
| Content | ~20 cards, ~8 enemies, ~5 events, 1 boss `launch-stub` |
| Prior | Sensors only (no model) → pressure vector → `?` tables |
| Client | MCP app: Map, Intent, Combat, Reward, Skip — **shipped (beta)** |
| Bridge | `play_card` invokes existing dealt skills / shell acceptance |
| Save | `game` block on `.spire/deck.json` |

**Exit criteria:** 5 external testers finish one clear + skip without reading Spire wiki.

## Stage 3 — Curator-weighted `?`

Add optional cheap model re-rank on prior (same schema as contract). Fail → sensors only.

## Stage 4 — Pack economy

Author 3 packs; shop reads packs/; class bias in shop inventory.

## Stage 5 — Spire AI product wrap

- Landing: “video games for building software with AI”  
- Plugin + MCP app install path  
- Name consistency (Spire) across engine and client  
- Telemetry only opt-in, aggregate room clears / skip rate (never code)

## Stage 6 — Vertical climbs

Templates: Startup MVP, Enterprise Harden, Watcher Eval. Same engine, different act bosses + pack weights.

## Explicit deferrals

Multiplayer, ranked ladder, marketplace payments, mobile — after Stage 2 exit criteria.

## Working agreement

1. Change ideology → update GDD + non-goals in the same PR.  
2. Change `?` behavior → update room-prior-contract + a test fixture.  
3. New card/enemy → validate against content-schema.  
4. No second active room — ever — without a GDD amendment.
