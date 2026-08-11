# Format — Intent

**Mode purpose:** Show what this room will do to you *before* you can play cards.  
**Player question:** “What am I fighting, and what does success mean?”  
**Primary action:** Begin combat / Face event.  
**Forbidden:** Hand interaction, playing cards, skipping acceptance reveal.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome + active-room banner             │
├─────────────────────────────────────────┤
│ HERO: enemy/event portrait + name       │
│ INTENT BAR (telegraph)                  │
│ plain consequence (1–2 sentences)       │
├─────────────────────────────────────────┤
│ ACCEPTANCE panel (mono command / checks)│
├─────────────────────────────────────────┤
│ [ Begin ]           ghost: Flee         │
└─────────────────────────────────────────┘
```

Hero is the **threat + intent**, not the hand. This screen exists so entertainment (drama) buys focus.

## Accent

`--facet-intent`. Danger only on Flee.

## Interaction

1. Begin → Combat or Event; hand locked until this screen is completed.  
2. Flee → confirm → map; streak break.  
3. Acceptance criteria always visible (no surprise win condition).

## Content bindings

`enemy.name`, `intent_pool[].text`, `room.acceptance`, `flavor`.

## Copy voice

- Good: “Will fail CI randomly if ignored.”  
- Bad: “This enemy uses stochastic reliability degradation paradigms.”

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Intent before hand | Jump straight into cards |
| Show clear condition | Hide how to win |
| Short flavor | Lore paragraphs |

## Background

Scene **`chamber`** — `content/scenes.json` → `screens.room`. A working room. The most-seen scene in the game, so it is also the most restrained: a colonnade in the wings, a floor line under the hand, nothing across the middle.

The intent beat and combat are one place at two beats, so they share a scene and the background does not cut between them.

- **Safe area** — `x 0.08 → 0.92`, `y 0 → 0.78` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0.06`, spread 1.
- **Layers** — void · far · haze · mid · shaft · near · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
