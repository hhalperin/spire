# Format — Reward

**Mode purpose:** Offer earned cards; make **Skip** the easy, proud default.  
**Player question:** “Do I take something, or keep the deck lean?”  
**Primary action:** Skip.  
**Forbidden:** Forced take, timed offers, burying Skip, opening Shop mid-reward.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome · “Room cleared”                 │
├─────────────────────────────────────────┤
│ HERO: SKIP panel (largest, prefocused)  │
├─────────────────────────────────────────┤
│ OFFERS: up to 3 compact card previews   │
│ (secondary)                             │
├─────────────────────────────────────────┤
│ if over soft-cap: trade-away required   │
│ caption: take/skip run stats            │
└─────────────────────────────────────────┘
```

This format deliberately inverts normal “upgrade shop” UI. **Skip owns the hero region.**

## Accent

`--facet-reward` (safe green) on Skip. Offers use muted panels.

## Interaction

1. Focus lands on Skip.  
2. Take → optional remove if soft-cap; then Map.  
3. No reshuffle / reroll in v0.  
4. Show running take/skip counts (lean pressure).

## Motion

Skip settle 200ms; no card pack opening animation that delays Skip.

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Skip as hero | Skip as tiny text link |
| Max 3 offers | Infinite scroll catalog |
| Soft-cap trade | Silent deck bloat |

## Background

Scene **`chamber`** — `content/scenes.json` → `screens.reward`. A working room. The most-seen scene in the game, so it is also the most restrained: a colonnade in the wings, a floor line under the hand, nothing across the middle.

Reward happens in the room you just cleared, so it is the same scene as combat with the light settled.

- **Safe area** — `x 0.08 → 0.92`, `y 0 → 0.78` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Modifier `calm`** — light ×1.25; the near layer suppressed. The same room with the fight out of it: the foreground framing drops away and the torch opens up.
- **Light** — one warm source at `0.5, 0.06`, spread 1.
- **Layers** — void · far · haze · mid · shaft · near · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
