# Format — Title / Continue

**Mode purpose:** Start or resume a run without teaching Spire lore.  
**Player question:** “Am I continuing, or beginning a climb?”  
**Primary action:** Continue (if save) or Begin climb.  
**Forbidden:** Hand, map nodes, rewards, settings rabbit holes.

## Layout

```
┌─────────────────────────────────────────┐
│ wordmark: SPIRE                         │
│ tagline: one room at a time             │
├─────────────────────────────────────────┤
│ HERO: climb template card               │
│   Ship the stub · Act I · class detect  │
├─────────────────────────────────────────┤
│ SAVE strip (if any): floor, class, deck │
├─────────────────────────────────────────┤
│ [ Continue ]     [ New climb ]          │
│ ghost: Ascension preview                │
└─────────────────────────────────────────┘
```

Hero is the **climb template**, not a marketing splash. Brand is present but subordinate to the run choice (see site work pages: project is the hero).

## Accent

`--facet-map` for new climb; ink CTA for Continue.

## Interaction

1. If `.spire/deck.json` + `game` block → Continue is primary solid CTA.  
2. New climb confirms overwrite only with `--force` equivalent dialog.  
3. Ascension preview is ghost link → Ascension facet, not a modal essay.

## Copy voice

- Good: “Continue · Floor 3 · Defect”  
- Bad: “Welcome to your AI-powered journey!!!”

## Do / Don’t

| Do | Don’t |
| --- | --- |
| One hero climb card | Carousel of features |
| Show save facts | Hide that a run exists |
| Short tagline | Tutorial wall |

## Background

Scene **`gate`** — `content/scenes.json` → `screens.title`. Outside, looking up. The only scene where you can see the whole spire, which is why the title screen is here.

- **Safe area** — `x 0 → 1`, `y 0 → 0.72` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0.02`, spread 1.15.
- **Layers** — void · far · haze · mid · shaft · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
