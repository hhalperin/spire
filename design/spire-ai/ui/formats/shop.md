# Format — Shop

**Mode purpose:** Optional pack draw — deliberate purchase, not a room reward.  
**Player question:** “Do I spend focus on a themed pack card?”  
**Primary action:** Buy (or Leave).  
**Forbidden:** Soft-forcing buys; mixing reward skip UI here; multi-pack cart checkout in v0.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome · focus tokens / gold            │
├─────────────────────────────────────────┤
│ HERO: pack identity (name + blurb)      │
├─────────────────────────────────────────┤
│ GRID: 3–6 wares (card/relic tiles)      │
│ price on each                           │
├─────────────────────────────────────────┤
│ [ Buy selected ]     ghost: Leave shop  │
└─────────────────────────────────────────┘
```

Hero = **pack brand** (like a distinct work-page style per pack later). Grid is catalog; one selection at a time in v0.

## Accent

`--facet-shop`. Pack themes may tint hero subtly; wares stay paper/ink.

## Interaction

1. Select ware → Buy enabled.  
2. Buy → engine deal skill/relic → token debit.  
3. Leave → Map.  
4. Soft-cap warning before buy if over 12.

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Pack as hero identity | Generic “store” |
| Leave always easy | Dark-pattern discount timer |
| Show soft-cap | Infinite buy spam |

## Background

Scene **`market`** — `content/scenes.json` → `screens.shop`. A merchant's nook. Cloth and awning rather than stone, because the shop is the one place in the spire somebody chose to be.

- **Safe area** — `x 0.08 → 0.92`, `y 0.02 → 0.78` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0.08`, spread 0.95.
- **Layers** — void · haze · mid · shaft · near · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
