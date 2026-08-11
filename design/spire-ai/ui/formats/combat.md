# Format — Combat

**Mode purpose:** Play a limited hand against one room until acceptance passes.  
**Player question:** “What do I play with the energy I have?”  
**Primary action:** Play a card (or Run acceptance when ready).  
**Forbidden:** Opening another room, shopping, pruning deck mid-fight.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome: energy pips · room HP/meter     │
├─────────────────────────────────────────┤
│ HERO: enemy status + current intent     │
│ log (last 3 plays)                      │
├─────────────────────────────────────────┤
│ HAND (fan / row) — legal cards only     │
│ cost pips on cards                      │
├─────────────────────────────────────────┤
│ [ Run acceptance ]  ghost: End turn     │
│ text link: Flee                         │
└─────────────────────────────────────────┘
```

Hero = **enemy state**. Hand is secondary but always visible. Primary CTA is **Run acceptance** once the player believes the room is clear — not “ask AI to finish.”

## Accent

`--facet-combat`. Card highlight uses ink focus ring.

## Interaction

1. Illegal cards (wrong `room_types`) hidden or greyed with reason.  
2. Play card → 200–400ms commit → engine/agent effect → log line.  
3. Run acceptance → show mono log tail; on pass → clear fanfare → Reward.  
4. Energy 0 → can still Run acceptance or End turn (draw/rules TBD v0: just wait).  

## Motion

Card commit + clear fanfare only. No damage number fireworks spam.

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Filter hand to room | Show entire deck |
| One enemy focus | Split view of backlog |
| Acceptance as win | “Chat until done” as win |

## Background

Scene **`chamber`** — `content/scenes.json` → `screens.room`. A working room. The most-seen scene in the game, so it is also the most restrained: a colonnade in the wings, a floor line under the hand, nothing across the middle.

Elite rooms take the `elite` modifier and boss rooms swap to `sanctum` entirely; see `screens.room.elite` and `screens.room.boss`.

- **Safe area** — `x 0.08 → 0.92`, `y 0 → 0.78` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0.06`, spread 1.
- **Layers** — void · far · haze · mid · shaft · near · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
