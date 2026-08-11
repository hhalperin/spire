# Format — Campfire

**Mode purpose:** Safe rest — prune, upgrade, or rest. No combat.  
**Player question:** “What dead weight do I burn?”  
**Primary action:** Choose one campfire action (Prune default highlight if unplayed cards exist).  
**Forbidden:** Entering another room without leaving, buying from shop, raising ascension here.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome                                  │
├─────────────────────────────────────────┤
│ HERO: campfire mark + “Rest · Prune ·   │
│        Upgrade” mode switcher           │
├─────────────────────────────────────────┤
│ LIST: deck cards with plays · last used │
│ unplayed sorted first                   │
├─────────────────────────────────────────┤
│ [ Confirm prune ]  ghost: Rest only     │
└─────────────────────────────────────────┘
```

Hero = **mode switcher**. List is the work surface. Warm accent — rest, not urgency.

## Accent

`--facet-campfire`.

## Interaction

1. Prune requires explicit confirm; calls engine `remove-card`.  
2. Rest = heal energy flavor + leave to map (v0 may only clear banner).  
3. Upgrade = optional v0 stub.  
4. Never auto-prune.

## Copy voice

- Good: “Unplayed · 0 plays · added floor 1”  
- Bad: “AI selected these cards for deletion.”

## Do / Don’t

| Do | Don’t |
| --- | --- |
| Sort unplayed first | Hide play counts |
| Confirm destructive | Swipe-to-delete without confirm |
| One action per visit (v0) | Full deckbuilder editor |

## Background

Scene **`alcove`** — `content/scenes.json` → `screens.campfire`. A rest cut into the wall. The one scene with its own light source in frame — the brazier, not the shaft — so the warm pool sits low and close.

- **Safe area** — `x 0.08 → 0.92`, `y 0 → 0.76` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0.78`, spread 0.7.
- **Layers** — void · haze · mid · shaft · near · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
