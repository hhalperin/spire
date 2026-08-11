# Format — Map

**Mode purpose:** Choose the *next* single room; show the climb shape.  
**Player question:** “Where do I go next?”  
**Primary action:** Select a reachable node.  
**Forbidden:** Hand, card play, multi-select, editing the deck.

## Layout

```
┌─────────────────────────────────────────┐
│ chrome: Act · Floor · Class · Streak    │
│ banner if active_room (block travel)    │
├─────────────────────────────────────────┤
│ HERO: path graph (left → boss)          │
│   nodes: combat / ? / camp / shop / boss│
├─────────────────────────────────────────┤
│ DETAIL rail (selected node)             │
│   kind · predicted room_type bias · tip │
├─────────────────────────────────────────┤
│ [ Enter node ]     ghost: Refresh prior │
└─────────────────────────────────────────┘
```

Hero = **graph**. Detail rail is supporting, never taller than the graph on desktop; collapses under graph on narrow IDE panels.

## Node shapes (color alone is not enough)

| Kind | Shape | Label |
| --- | --- | --- |
| combat | circle | monster / elite mark |
| `?` | diamond | `?` |
| campfire | rounded square | flame mark |
| shop | hexagon | coin mark |
| boss | large circle + ring | skull / gate |

## Accent

`--facet-map`. `?` nodes may show tiny prior chips (bug/feature) as caption, not as spoilers of the exact enemy.

## Interaction

1. Only reachable nodes are hittable.  
2. If `active_room` set → map is read-only; banner points to Combat/Event.  
3. Enter commits; no “preview fight” without Intent facet.  
4. Refresh prior regenerates *future* `?` tables only (never frozen entered nodes).

## Copy voice

- Good: “Unknown · likely bug pressure”  
- Bad: “AI recommends you MUST fix auth now”

## Do / Don’t

| Do | Don’t |
| --- | --- |
| One path hero | Dashboard of tickets |
| Shape + label nodes | Color-only legend |
| Bias hints on `?` | Autopick next node |

## Background

Scene **`shaft`** — `content/scenes.json` → `screens.map`. Inside the climb, looking up the well. The map screen is literally the thing this scene depicts, so the art stays at the walls and lets the graph own the middle.

- **Safe area** — `x 0.08 → 0.92`, `y 0 → 0.82` in normalised frame coordinates. Silhouette crossing it is attenuated toward the void rather than clipped, because a screen this text-dense has nowhere to draw *around* the content; the floor is 40% of the layer's step.
- **Light** — one warm source at `0.5, 0`, spread 1.3.
- **Layers** — void · far · haze · mid · shaft · near · floor · glow · vignette.
- **Per act** — the biome supplies palette, arch style and pillar variants; the scene supplies structure, so the same grammar reads as a different world each act.

Moving a region on this screen means re-checking that safe rectangle and re-running `node tools/scenes.mjs`. The wireframe is not a drawing of these backgrounds — it is their source.
