# Format — Event

**Mode purpose:** A non-combat choice with tradeoffs (design fork, scope trap, orient).  
**Player question:** “Which tradeoff do I accept?”  
**Primary action:** Pick exactly one choice.  
**Forbidden:** Playing the combat hand, multi-select, deferring without a choice id.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome                                  │
├─────────────────────────────────────────┤
│ HERO: event title + short narrative     │
│ (max ~60 words)                         │
├─────────────────────────────────────────┤
│ CHOICES (2–3 stacked buttons)           │
│   label + one-line consequence preview  │
├─────────────────────────────────────────┤
│ caption: this still counts as the room  │
└─────────────────────────────────────────┘
```

Hero = **story prompt**. Choices are the whole action surface — no separate CTA bar.

## Accent

`--facet-event`. Trap events may use danger on the greedy choice only.

## Interaction

1. One click resolves; confirm only if curse applied.  
2. Choice effects must map to schema (`effects[]`).  
3. Returns to Map (or Reward if event grants cards).

## Copy voice

- Good: “Accept scope — gain Bloated Scope curse.”  
- Bad: “Synergize stakeholder alignment outcomes.”

## Do / Don’t

| Do | Don’t |
| --- | --- |
| 2–3 clear choices | 7 radio buttons |
| Preview consequence | Hide the curse |
| Short narrative | Quest log dump |

## Background

Scene **`crossroads`** — `content/scenes.json` → `screens.event`. A fork with no checkable way out. Two arches going different directions and no floor line committing to either — the ambiguity is the point, since an event is the one room with no honest intent.

- **Safe area** — `x 0.08 → 0.92`, `y 0 → 0.8` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0.05`, spread 1.05.
- **Layers** — void · far · haze · mid · shaft · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
